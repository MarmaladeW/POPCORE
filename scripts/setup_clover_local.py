"""Prepare a local POPCORE database for a hands-on Clover sandbox run.

Local development only. A dry run (the default) changes nothing: it prints the plan and the
opening review. With --apply it:

1. copies the database to a new backup file before anything else;
2. gives the Clover store a floor location;
3. grants inventory access to the given admin for every store, and to the Clover register for
   the Clover store;
4. creates one identity-verified test product per Clover sandbox item, linked by Clover item ID
   (existing products are never changed);
5. posts the reviewed opening through inventory_opening.apply_opening_counts: test stock on the
   Clover store's floor, every other location opened empty. That switches the whole database to
   authoritative inventory, so it stops if any location still holds verified legacy stock.

    .local/frontend-venv/bin/python scripts/setup_clover_local.py --db popcore_app/popcore.db \\
        --admin-sub 'auth0|...' --item ABCDEFGHIJKLM=Labubu=1999 [--from-connector] [--apply]

--from-connector reads the items of the connector's latest orders; it needs CLOVER_CONNECTOR_URL
and CLOVER_CONNECTOR_PASSWORD in the environment.
"""
import argparse
import json
import os
import re
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'popcore_app'))
os.environ.setdefault('AUTH0_DOMAIN', 'clover-local-setup.invalid')
os.environ.setdefault('DISABLE_SCHEDULER', '1')

from checkout_access import REGISTER_SUB  # noqa: E402
from inventory_opening import (  # noqa: E402
    OpeningCountError, active_locations, apply_opening_counts, connect, review_opening_counts,
)

IDENTIFIER = re.compile(r'^[A-Za-z0-9]{13}$')
REASON = 'Local Clover sandbox test stock'


def parse_item(value):
    item_id, _, rest = value.partition('=')
    name, _, price = rest.rpartition('=')
    if not IDENTIFIER.fullmatch(item_id) or not name.strip() or not price.isdigit():
        raise argparse.ArgumentTypeError('use ITEMID=Name=price_cents, for example ABCDEFGHIJKLM=Labubu=1999')
    return {'id': item_id, 'name': name.strip()[:120], 'price': int(price)}


def connector_items():
    """Distinct Clover items in the connector's latest orders."""
    import requests
    url = os.environ.get('CLOVER_CONNECTOR_URL', '').strip().rstrip('/')
    password = os.environ.get('CLOVER_CONNECTOR_PASSWORD', '')
    if not url or not password:
        raise SystemExit('--from-connector needs CLOVER_CONNECTOR_URL and CLOVER_CONNECTOR_PASSWORD')
    response = requests.get(url + '/orders', auth=('popcore', password), timeout=(3, 20))
    response.raise_for_status()
    items = {}
    for order in response.json().get('orders') or []:
        for line in order.get('items') or []:
            item_id = str(line.get('itemId') or '')
            if IDENTIFIER.fullmatch(item_id) and type(line.get('price')) is int:
                items.setdefault(item_id, {'id': item_id, 'name': str(line.get('name') or 'Clover item')[:120],
                                           'price': line['price']})
    return list(items.values())


def backup(db_path, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise SystemExit(f'refusing existing backup path: {target}')
    with closing(sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)) as source, \
            closing(sqlite3.connect(target)) as copy:
        source.backup(copy)
        if copy.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise SystemExit('backup failed integrity_check')


def prepare(con, store, admin_sub, items, quantity):
    """Inside the caller's transaction: location, grants and test products. Returns the opening rows."""
    con.execute("INSERT OR IGNORE INTO inventory_locations(store_id,code,name,is_active) VALUES (?,'floor','Floor',1)",
                (store['id'],))
    location_id = con.execute("SELECT id FROM inventory_locations WHERE store_id=? AND code='floor'",
                              (store['id'],)).fetchone()['id']
    con.execute('INSERT OR IGNORE INTO inventory_scope_state(store_id,location_id,opening_verified) VALUES (?,?,0)',
                (store['id'], location_id))
    for row in con.execute('SELECT id FROM stores WHERE is_active=1').fetchall():
        con.execute('INSERT OR IGNORE INTO inventory_access(auth0_sub,store_id) VALUES (?,?)', (admin_sub, row['id']))
    con.execute('INSERT OR IGNORE INTO inventory_access(auth0_sub,store_id) VALUES (?,?)', (REGISTER_SUB, store['id']))
    rows = []
    for number, item in enumerate(items, 1):
        product = con.execute('SELECT * FROM products WHERE clover_item_id=?', (item['id'],)).fetchone()
        if product is None:
            name = f"Clover sandbox · {item['name']}"
            con.execute("""INSERT INTO products(sku,name_cn_en,jizhanming,price,product_type,boxes_per_dan,stock_form,
                stock_unit,identity_status,clover_item_id,clover_item_name)
                VALUES (?,?,?,?,'ordinary',1,'ordinary','piece','verified',?,?)""",
                (f"CLOVER-SBX-{item['id']}", name, name, item['price'] / 100, item['id'], item['name']))
            product = con.execute('SELECT * FROM products WHERE clover_item_id=?', (item['id'],)).fetchone()
        elif product['identity_status'] != 'verified' or not product['stock_unit']:
            raise SystemExit(f"product {product['id']} already has Clover item {item['id']} but is not "
                             'identity-verified with a stock unit; verify it first')
        rows.append({'store': store['code'], 'location': 'floor', 'product_id': str(product['id']),
                     'sku': product['sku'], 'unit': product['stock_unit'], 'count': str(quantity),
                     '_file': 'setup_clover_local', '_row': number})
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--store', default='MT', help='code of the store that takes Clover orders (default MT)')
    parser.add_argument('--admin-sub', required=True, help='Auth0 subject of the admin approving the opening')
    parser.add_argument('--item', action='append', type=parse_item, default=[], metavar='ITEMID=Name=price_cents')
    parser.add_argument('--from-connector', action='store_true', help="also use the connector's latest order items")
    parser.add_argument('--quantity', type=int, default=20, help='opening stock per test product (default 20)')
    parser.add_argument('--apply', action='store_true', help='back up, then make the changes')
    args = parser.parse_args(argv)
    db_path = args.db.resolve()
    if not db_path.is_file():
        raise SystemExit(f'database does not exist: {db_path}')
    if not 0 < args.quantity <= 1000:
        raise SystemExit('--quantity must be between 1 and 1000')
    items = {item['id']: item for item in args.item + (connector_items() if args.from_connector else [])}
    if not items:
        raise SystemExit('name at least one Clover item with --item or --from-connector')
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backups = ROOT / '.local' / 'backups'
    first = backups / f'{db_path.stem}-before-clover-setup-{stamp}.db'
    second = backups / f'{db_path.stem}-before-clover-opening-{stamp}.db'
    business_date = datetime.now(ZoneInfo('America/Toronto')).date().isoformat()
    if args.apply:
        backup(db_path, first)
    con = connect(db_path, read_only=False)
    try:
        con.execute('PRAGMA busy_timeout=5000')
        con.execute('BEGIN IMMEDIATE')
        if con.execute('SELECT mode FROM inventory_mode WHERE id=1').fetchone()['mode'] != 'legacy':
            raise SystemExit('inventory is already authoritative; a new location cannot be opened here')
        store = con.execute('SELECT * FROM stores WHERE code=? AND is_active=1', (args.store.upper(),)).fetchone()
        if store is None:
            raise SystemExit(f'no active store {args.store}')
        rows = prepare(con, store, args.admin_sub, list(items.values()), args.quantity)
        empty = [f"{loc['store_code']}-{loc['code']}" for loc in active_locations(con)
                 if not (loc['store_id'] == store['id'] and loc['code'] == 'floor')]
        review = review_opening_counts(con, rows, empty_locations=empty, actor_sub=args.admin_sub,
                                       business_date=business_date, reason=REASON)
        plan = {'database': str(db_path), 'store': store['code'], 'items': list(items.values()),
                'opened_empty': empty, 'business_date': business_date, 'errors': review['errors'],
                'warnings': review['warnings'][:20], 'summary': review['summary']}
        if not args.apply or not review['ready_to_apply']:
            con.rollback()
            print(json.dumps({**plan, 'applied': False, 'dry_run': not args.apply}, indent=2, ensure_ascii=False))
            raise SystemExit(0 if review['ready_to_apply'] else 1)
        con.commit()
    finally:
        con.close()
    try:
        result = apply_opening_counts(db_path, rows, actor_sub=args.admin_sub, business_date=business_date,
                                      backup_path=second, empty_locations=empty, reason=REASON)
    except OpeningCountError as exc:
        raise SystemExit(f'{exc.code}: {exc} (restore {first} to undo the setup)') from exc
    print(json.dumps({**plan, 'applied': result['applied'], 'inventory_mode': result.get('inventory_mode'),
                      'backup_before_setup': str(first), 'backup_before_opening': str(second),
                      'opening_document_id': result.get('opening_document_id')}, indent=2, ensure_ascii=False))
    return result


if __name__ == '__main__':
    main()
