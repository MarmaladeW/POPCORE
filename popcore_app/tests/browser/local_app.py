"""Disposable loopback POPCORE API for the Build 6 real-browser pilot."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import date, timedelta
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--port', required=True, type=int)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[3]
    allowed = (repo / '.local').resolve()
    root = Path(args.root).resolve()
    if allowed not in root.parents:
        raise SystemExit('Refusing integration paths outside the repository .local directory')
    root.mkdir(parents=True, exist_ok=True)
    os.environ.update({
        'AUTH0_DOMAIN': 'fixture.invalid', 'AUTH0_AUDIENCE': 'build6-pilot',
        'DISABLE_SCHEDULER': '1', 'SENTRY_DSN': '',
        'POPCORE_DB_PATH': str(root / 'pilot.db'),
        'POPCORE_HIDDEN_IMG_DIR': str(root / 'uploads' / 'hidden'),
        'POPCORE_PAYMENT_EVIDENCE_DIR': str(root / 'uploads' / 'payments'),
        'POPCORE_CONDITION_EVIDENCE_DIR': str(root / 'uploads' / 'conditions'),
    })
    app_dir = repo / 'popcore_app'
    sys.path.insert(0, str(app_dir))

    import auth
    import db
    from jose import jwt
    from flask import Flask
    from blueprints import closing, goods, inventory, payments, products, reports
    from blueprints import restock, sale_documents, sales, schedule, stock, stores, trades, today

    secret = os.environ.get('POPCORE_FIXTURE_SECRET', 'build6-local-key-only')
    def decode_fixture(token):
        return jwt.decode(token, secret, algorithms=['HS256'], audience='build6-pilot')
    auth._decode_token = decode_fixture

    import requests
    requests.sessions.Session.request = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError('External network is disabled in the Build 6 pilot'))

    db.migrate_db()
    con = sqlite3.connect(db.DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        stores_by_code = {row['code']: row['id'] for row in con.execute('SELECT id,code FROM stores')}
        product_id = con.execute("""INSERT INTO products
            (sku,name_cn_en,jizhanming,product_type,boxes_per_dan,stock_form,stock_unit,identity_status)
            VALUES ('PILOT-BOX','Pilot Box / 测试盒','Pilot Box','ordinary',1,'ordinary','piece','verified')""").lastrowid
        floor_id = con.execute("SELECT id FROM inventory_locations WHERE store_id=? AND code='floor'",
                               (stores_by_code['DT'],)).fetchone()[0]
        con.execute("INSERT INTO inventory_balances(product_id,location_id,disposition,quantity,version) VALUES (?,?,?,?,?)",
                    (product_id,floor_id,'saleable',20,1))
        con.execute("INSERT INTO stock(product_id,store_id,upstairs_qty,instore_qty,claw_qty) VALUES (?,?,?,?,?)",
                    (product_id,stores_by_code['DT'],0,20,0))
        con.execute('UPDATE inventory_scope_state SET opening_verified=1 WHERE store_id=?',
                    (stores_by_code['DT'],))
        con.execute("UPDATE inventory_mode SET mode='authoritative' WHERE id=1")
        for subject in ('staff1','staff2','manager','admin','viewer'):
            for code in ('DT','MK') if subject in {'manager','admin'} else ('DT',):
                con.execute('INSERT INTO inventory_access(auth0_sub,store_id) VALUES (?,?)',
                            (f'auth0|{subject}', stores_by_code[code]))
        for subject in ('staff1','staff2','manager','admin','viewer'):
            employee_id = con.execute('INSERT INTO employees(auth0_id,name,email) VALUES (?,?,?)',
                (f'auth0|{subject}', subject.title(), f'{subject}@example.invalid')).lastrowid
            con.execute('INSERT OR IGNORE INTO employee_stores(employee_id,store_id) VALUES (?,?)',
                        (employee_id, stores_by_code['DT']))
            if subject.startswith('staff'):
                con.execute("""INSERT INTO shifts(employee_id,date,start_time,end_time,assigned_by,notes,store_id,position)
                    VALUES (?,?,?,?,?,?,?,?)""", (employee_id,date.today().isoformat(),'09:00','17:00',
                    'auth0|manager','Pilot shift',stores_by_code['DT'],'Floor'))
        con.commit()
    finally:
        con.close()

    app = Flask('build6_local_app')
    for blueprint in (sales.bp,stock.bp,restock.bp,inventory.bp,products.bp,stores.bp,
                      goods.bp,sale_documents.bp,payments.bp,closing.bp,trades.bp,today.bp,
                      schedule.bp,reports.bp):
        app.register_blueprint(blueprint)
    app.teardown_appcontext(db.close_db)

    now = int(time.time())
    tokens = {}
    for role, subject in (('viewer','viewer'),('staff','staff1'),('manager','manager'),('admin','admin')):
        tokens[role] = jwt.encode({'sub':f'auth0|{subject}',auth.ROLE_CLAIM:role,
            'aud':'build6-pilot','exp':now+3600},secret,algorithm='HS256')
    (root / 'ready.json').write_text(json.dumps({'port':args.port,'tokens':tokens,
        'db_path':db.DB_PATH,'product_id':product_id,'store_id':stores_by_code['DT']}),encoding='utf-8')
    app.run(host='127.0.0.1',port=args.port,debug=False,use_reloader=False,threaded=False)


if __name__ == '__main__':
    main()
