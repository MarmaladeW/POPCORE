"""Dated store activity, separate from merchandise sales and inventory."""
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from flask import Blueprint, jsonify, request

from auth import role_required
from checkout_access import business_date, checkout_access
from db import get_db
from goods_operations import _begin, _remember, _replay
from inventory_commands import InventoryConflict, InventoryError, require_inventory_access
from validation import read_date, read_int

bp = Blueprint('store_events', __name__)
OPERATIONAL = {'claw_prize', 'claw_refill', 'display_in'}


@bp.errorhandler(InventoryError)
@bp.errorhandler(ValueError)
@bp.errorhandler(PermissionError)
def error(exc):
    if isinstance(exc, InventoryError):
        return jsonify(error=str(exc), code=exc.code), exc.status
    if isinstance(exc, PermissionError):
        return jsonify(error='Store event access denied'), 403
    return jsonify(error=str(exc), code='invalid_input'), 400


def _scope(con, actor, store_id, date, *, write=False):
    access = checkout_access(con, actor)
    live = {store['id'] for store in access['live_stores']}
    if store_id not in live:
        if write or access['role'] != 'staff':
            raise PermissionError('Store event access denied')
        owned = con.execute('''SELECT 1 FROM store_events WHERE store_id=? AND business_date=? AND actor_sub=?
            UNION SELECT 1 FROM checkout_orders WHERE store_id=? AND business_date=? AND created_by=?''',
            (store_id,date,actor['sub'],store_id,date,actor['sub'])).fetchone()
        if not owned:
            raise PermissionError('Store event access denied')
    return 'personal' if access['role'] == 'staff' else 'store'


def _event(con, event_id):
    row = con.execute('''SELECT e.*,COALESCE(NULLIF(emp.name,''),e.actor_sub) AS actor_name
        FROM store_events e LEFT JOIN employees emp ON emp.auth0_id=e.actor_sub WHERE e.id=?''',(event_id,)).fetchone()
    return dict(row)


@bp.post('/api/store-events')
@role_required('staff')
def create():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError('JSON object required')
    kind = data.get('kind')
    if not isinstance(kind,str) or kind not in OPERATIONAL | {'cash_exchange'}:
        raise ValueError('kind is invalid')
    allowed = {'store_id','kind','note'} | ({'product_id','quantity'} if kind in OPERATIONAL else {'tender','amount_cents'})
    if set(data) - allowed:
        raise ValueError('Unknown fields')
    store_id = read_int(data.get('store_id'),'store_id',minimum=1)
    note = data.get('note','')
    if not isinstance(note,str) or len(note.strip()) > 500:
        raise ValueError('note must be text up to 500 characters')
    note = note.strip()
    if kind in OPERATIONAL:
        product_id = read_int(data.get('product_id'),'product_id',minimum=1)
        quantity = read_int(data.get('quantity'),'quantity',minimum=1)
        tender = amount = None
    else:
        if data.get('tender') not in ('card','e_transfer'):
            raise ValueError('tender must be card or e_transfer')
        tender = data['tender']
        amount = read_int(data.get('amount_cents'),'amount_cents',minimum=1)
        if not note:
            raise ValueError('note must confirm digital receipt and physical cash payout')
        product_id = quantity = None
    con = get_db(); actor = request.jwt_payload; today = business_date()
    _scope(con,actor,store_id,today,write=True)
    if kind == 'cash_exchange':
        require_inventory_access(con,actor,(store_id,),'staff')
    _begin(con)
    try:
        today = business_date()
        _scope(con,actor,store_id,today,write=True)
        if kind == 'cash_exchange':
            require_inventory_access(con,actor,(store_id,),'staff')
        intent = dict(store_id=store_id,kind=kind,product_id=product_id,quantity=quantity,
            tender=tender,amount_cents=amount,note=note)
        key,digest,prior = _replay(con,request.headers.get('Idempotency-Key'),'store_event',actor,intent)
        if prior:
            con.commit()
            return jsonify(_event(con,prior['id'])),201
        if con.execute("SELECT 1 FROM closing_sessions WHERE store_id=? AND business_date=? AND status='closed'",(store_id,today)).fetchone():
            raise InventoryConflict('Store day is closed','closing_state_conflict')
        product_name = None
        if product_id:
            product = con.execute('''SELECT COALESCE(NULLIF(TRIM(jizhanming),''),
                NULLIF(TRIM(name_cn_en),''),NULLIF(TRIM(sku),'')) AS name
                FROM products WHERE id=?''',(product_id,)).fetchone()
            if not product:
                raise ValueError('product_id is invalid')
            product_name = product['name'] or str(product_id)
        cash_event_id = None
        if kind == 'cash_exchange':
            cash_event_id = con.execute('''INSERT INTO cash_events
                (store_id,business_date,event_type,amount_cents,reason,actor_sub)
                VALUES (?,?,'payout',?,?,?)''',(store_id,today,amount,'Cash exchange: '+note,actor['sub'])).lastrowid
        event_id = con.execute('''INSERT INTO store_events
            (store_id,business_date,kind,actor_sub,product_id,product_name,quantity,tender,amount_cents,note,cash_event_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
            (store_id,today,kind,actor['sub'],product_id,product_name,quantity,tender,amount,note,cash_event_id)).lastrowid
        result = _event(con,event_id)
        _remember(con,key,'store_event',event_id,actor,digest,{'id':event_id})
        con.commit()
        return jsonify(result),201
    except Exception:
        con.rollback()
        raise


@bp.get('/api/store-events')
@role_required('staff')
def summary():
    con = get_db(); actor = request.jwt_payload
    store_id = read_int(request.args.get('store_id'),'store_id',minimum=1)
    date = read_date(request.args.get('business_date') or business_date(),'business_date')
    scope = _scope(con,actor,store_id,date)
    personal = scope == 'personal'
    subject = actor['sub']
    event_ids = [row['id'] for row in con.execute('''SELECT id FROM store_events
        WHERE store_id=? AND business_date=? AND (?=0 OR actor_sub=?) ORDER BY id''',
        (store_id,date,int(personal),subject))]
    events = [_event(con,event_id) for event_id in event_ids]
    orders = con.execute('''SELECT o.id,o.reference,o.status,o.sale_id,o.created_by,o.created_at,o.payload,
        COALESCE(NULLIF(emp.name,''),o.created_by) AS actor_name,a.tender,a.amount_cents
        FROM checkout_orders o JOIN checkout_attempts a ON a.checkout_id=o.id
        LEFT JOIN employees emp ON emp.auth0_id=o.created_by
        WHERE o.store_id=? AND o.business_date=? AND o.status='completed'
        AND a.status='completed' AND (?=0 OR o.created_by=?) ORDER BY o.id,a.id''',
        (store_id,date,int(personal),subject)).fetchall()
    by_order = {}
    for row in orders:
        checkout = by_order.setdefault(row['id'],dict(id=row['id'],reference=row['reference'],
            sale_id=row['sale_id'],created_by=row['created_by'],actor_name=row['actor_name'],
            created_at=row['created_at'],items=[{'name':line['product_name_snapshot'],'quantity':line['quantity']}
                for line in json.loads(row['payload']).get('lines',[])],amount_cents=0,tenders=[],payments=[]))
        checkout['amount_cents'] += row['amount_cents']
        checkout['tenders'].append(row['tender'])
        checkout['payments'].append({'tender':row['tender'],'amount_cents':row['amount_cents']})
    checkouts = {'pos':[],'non_pos':[],'split':[]}
    for checkout in by_order.values():
        group = 'split' if len(checkout['tenders']) > 1 else ('pos' if checkout['tenders'][0] in ('cash','card') else 'non_pos')
        checkouts[group].append(checkout)
    receipts = []; transfers = []
    try:
        require_inventory_access(con,actor,(store_id,),'staff')
    except PermissionError:
        pass
    else:
        receipts = [dict(row) for row in con.execute('''SELECT id,store_id,business_date,shipment_reference,supplier,status,created_by
            FROM goods_receipts WHERE store_id=? AND business_date=? ORDER BY id''',
            (store_id,date))]
        transfers = [dict(row) for row in con.execute('''SELECT d.id,d.kind,d.business_date,d.status,d.created_by,
            src.store_id AS source_store_id,dst.store_id AS destination_store_id FROM inventory_deliveries d
            JOIN inventory_locations src ON src.id=d.source_location_id
            JOIN inventory_locations dst ON dst.id=d.destination_location_id
            WHERE dst.store_id=? AND d.business_date=? AND d.kind='transfer' ORDER BY d.id''',
            (store_id,date))]
    pending = con.execute('''SELECT COUNT(*) FROM checkout_orders WHERE store_id=? AND business_date=?
        AND status='open' AND (?=0 OR created_by=?)''',
        (store_id,date,int(personal),subject)).fetchone()[0]
    store_code = con.execute('SELECT code FROM stores WHERE id=?',(store_id,)).fetchone()['code']
    def local_time(timestamp):
        parsed = datetime.fromisoformat(timestamp.replace('Z','+00:00'))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(ZoneInfo('America/Toronto')).strftime('%H:%M')
    labels = {'claw_prize':'娃娃机出奖','claw_refill':'入娃娃机','display_in':'入display','cash_exchange':'换现金'}
    lines = [f"{store_code} · {date} · {'个人' if personal else '门店'}汇总",
        '按付款方式分组，仅含本系统已完成买单']
    for group, label in (('pos','卡/现金订单'),('non_pos','电子支付订单'),('split','混合付款订单')):
        items = checkouts[group]
        lines.append(f"{label}: {len(items)} 单，${sum(item['amount_cents'] for item in items)/100:.2f}")
        for item in items:
            payments = ' + '.join(f"{pay['tender']} ${pay['amount_cents']/100:.2f}" for pay in item['payments'])
            lines.append(f"  {local_time(item['created_at'])} {item['actor_name']} · {item['reference']} · {payments}")
            lines.extend(f"    {line['name']} × {line['quantity']}" for line in item['items'])
    lines.append(f"待完成订单: {pending}")
    for kind in ('claw_prize','claw_refill','cash_exchange','display_in'):
        kind_events = [event for event in events if event['kind'] == kind]
        lines.append(f"{labels[kind]}: {len(kind_events)}")
        for event in kind_events:
            detail = (f"{event['product_name']} × {event['quantity']}" if event['product_id'] else
                f"{event['tender']} 收款 ${event['amount_cents']/100:.2f}，现金支出")
            lines.append(f"  {local_time(event['created_at'])} {event['actor_name']} · {detail}" + (f" · {event['note']}" if event['note'] else ''))
    lines.append(f"入店收货: {len(receipts)}；入店调拨: {len(transfers)}")
    lines.extend(f"  收货 {item['id']}: {item['shipment_reference'] or '无单号'} ({item['status']})" for item in receipts)
    lines.extend(f"  调拨 {item['id']}: {item['status']}" for item in transfers)
    return jsonify(business_date=date,scope=scope,events=events,checkouts=checkouts,
        receipts=receipts,transfers=transfers,summary_text='\n'.join(lines))
