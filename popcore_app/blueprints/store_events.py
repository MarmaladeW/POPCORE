"""Dated store activity, separate from merchandise sales and inventory."""
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from flask import Blueprint, jsonify, request

from auth import role_required
from checkout_access import REGISTER_NAME, REGISTER_SUB, business_date, checkout_access
from db import get_db
from goods_operations import _balance, _begin, _remember, _replay
from inventory_commands import (InventoryConflict, InventoryError, _post_inventory_in_transaction,
    require_inventory_access, store_is_live)
from validation import read_date, read_int

bp = Blueprint('store_events', __name__)
OPERATIONAL = {'claw_prize', 'claw_refill', 'display_in'}
REFILL_SOURCES = ('floor', 'upstairs', 'warehouse')


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


def _post_claw(con, actor, key, store_id, kind, product_id, quantity, source_location_id, today):
    """Post a claw refill (move in) or prize (consume) in authoritative mode; see docs/inventory-core.md."""
    if not store_is_live(con, store_id):
        return None
    claw = con.execute("""SELECT id FROM inventory_locations
        WHERE store_id=? AND code='claw' AND is_active=1""",(store_id,)).fetchone()
    if not claw:
        raise InventoryConflict('This store has no claw machine location','claw_location_missing')
    unit = con.execute('SELECT stock_unit FROM products WHERE id=?',(product_id,)).fetchone()['stock_unit']
    line = {'product_id':product_id,'unit':unit,'quantity':quantity}
    if kind == 'claw_refill':
        if source_location_id is None:
            raise ValueError('source_location_id is required for a claw refill')
        line.update(from_location_id=source_location_id,from_disposition='saleable',
            to_location_id=claw['id'],to_disposition='saleable',expected_versions={
                'from':_balance(con,product_id,source_location_id,'saleable')['version'],
                'to':_balance(con,product_id,claw['id'],'saleable')['version']})
    else:
        line.update(from_location_id=claw['id'],from_disposition='saleable',expected_versions={
            'from':_balance(con,product_id,claw['id'],'saleable')['version']})
    return _post_inventory_in_transaction(con,{'kind':'move' if kind == 'claw_refill' else 'consume',
        'business_date':today,'source_type':'store_event','source_id':f'{key}:claw','lines':[line]},
        actor=actor,request_key=f'{key}:claw',access_store_ids=(store_id,))['document_id']


def _post_display(con, actor, key, store_id, product_id, quantity, source_location_id, today):
    """Put a piece on display in authoritative mode: moved from the floor or back stock when a source is
    given, otherwise received already opened. Display stock stays saleable; see docs/inventory-core.md."""
    if not store_is_live(con, store_id):
        return None
    display = con.execute("""SELECT id FROM inventory_locations
        WHERE store_id=? AND code='display' AND is_active=1""",(store_id,)).fetchone()
    if not display:
        raise InventoryConflict('This store has no display location','display_location_missing')
    unit = con.execute('SELECT stock_unit FROM products WHERE id=?',(product_id,)).fetchone()['stock_unit']
    line = {'product_id':product_id,'unit':unit,'quantity':quantity,'to_location_id':display['id'],
        'to_disposition':'saleable'}
    to_version = _balance(con,product_id,display['id'],'saleable')['version']
    if source_location_id is not None:
        line.update(from_location_id=source_location_id,from_disposition='saleable',expected_versions={
            'from':_balance(con,product_id,source_location_id,'saleable')['version'],'to':to_version})
    else:
        line['expected_versions'] = {'to':to_version}
    return _post_inventory_in_transaction(con,{'kind':'move' if source_location_id is not None else 'receipt',
        'business_date':today,'source_type':'store_event','source_id':f'{key}:display','lines':[line]},
        actor=actor,request_key=f'{key}:display',access_store_ids=(store_id,))['document_id']


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
    if kind in ('claw_refill','display_in'):
        allowed |= {'source_location_id'}
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
        source_location_id = (None if data.get('source_location_id') is None else
            read_int(data['source_location_id'],'source_location_id',minimum=1))
    else:
        if data.get('tender') not in ('card','e_transfer'):
            raise ValueError('tender must be card or e_transfer')
        tender = data['tender']
        amount = read_int(data.get('amount_cents'),'amount_cents',minimum=1)
        if not note:
            raise ValueError('note must confirm digital receipt and physical cash payout')
        product_id = quantity = source_location_id = None
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
            tender=tender,amount_cents=amount,note=note,
            **({'source_location_id':source_location_id} if source_location_id is not None else {}))
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
        if source_location_id is not None and not con.execute(f'''SELECT 1 FROM inventory_locations
                WHERE id=? AND store_id=? AND is_active=1 AND code IN ({','.join('?'*len(REFILL_SOURCES))})''',
                (source_location_id,store_id,*REFILL_SOURCES)).fetchone():
            raise ValueError('source_location_id must be this store\'s floor or back stock')
        document_id = (_post_claw(con,actor,key,store_id,kind,product_id,quantity,source_location_id,today)
            if kind in ('claw_prize','claw_refill') else
            _post_display(con,actor,key,store_id,product_id,quantity,source_location_id,today)
            if kind == 'display_in' else None)
        cash_event_id = None
        if kind == 'cash_exchange':
            cash_event_id = con.execute('''INSERT INTO cash_events
                (store_id,business_date,event_type,amount_cents,reason,actor_sub)
                VALUES (?,?,'payout',?,?,?)''',(store_id,today,amount,'Cash exchange: '+note,actor['sub'])).lastrowid
        event_id = con.execute('''INSERT INTO store_events
            (store_id,business_date,kind,actor_sub,product_id,product_name,quantity,tender,amount_cents,note,cash_event_id,
             source_location_id,inventory_document_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (store_id,today,kind,actor['sub'],product_id,product_name,quantity,tender,amount,note,cash_event_id,
             source_location_id,document_id)).lastrowid
        result = _event(con,event_id)
        _remember(con,key,'store_event',event_id,actor,digest,{'id':event_id})
        con.commit()
        return jsonify(result),201
    except Exception:
        con.rollback()
        raise


SUMMARY_METHODS = (('cash','现金'),('card','刷卡'),('e_transfer','转账 EMT'),('wechat','微信'),('alipay','支付宝'))


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
        CASE WHEN o.created_by=? THEN ? ELSE COALESCE(NULLIF(emp.name,''),o.created_by) END AS actor_name,
        a.tender,a.amount_cents
        FROM checkout_orders o JOIN checkout_attempts a ON a.checkout_id=o.id
        LEFT JOIN employees emp ON emp.auth0_id=o.created_by
        WHERE o.store_id=? AND o.business_date=? AND o.status='completed'
        AND a.status='completed' AND (?=0 OR o.created_by=?) ORDER BY o.id,a.id''',
        (REGISTER_SUB,REGISTER_NAME,store_id,date,int(personal),subject)).fetchall()
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
        '按付款方式分开，仅含本系统已完成买单']
    # One group per payment method. A split order shows its part under each method it used,
    # marked 拆单; its items are listed once, under the first method it appears in.
    ordered = sorted(by_order.values(), key=lambda item: (item['created_at'], item['id']))
    methods = list(SUMMARY_METHODS)
    known = {tender for tender, _ in methods}
    methods += [(tender, tender) for tender in sorted({pay['tender'] for item in ordered
        for pay in item['payments']} - known)]
    listed = set()
    for tender, label in methods:
        parts = [(item, sum(pay['amount_cents'] for pay in item['payments'] if pay['tender'] == tender))
            for item in ordered if any(pay['tender'] == tender for pay in item['payments'])]
        lines.append(f"{label}: {len(parts)} 笔，${sum(amount for _, amount in parts)/100:.2f}")
        for item, amount in parts:
            split = len({pay['tender'] for pay in item['payments']}) > 1
            lines.append(f"  {local_time(item['created_at'])} {item['actor_name']} · {item['reference']} · ${amount/100:.2f}"
                + (' 拆单' if split else ''))
            if item['id'] not in listed:
                listed.add(item['id'])
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
    return jsonify(business_date=date,scope=scope,events=events,checkouts=checkouts,pending=pending,
        receipts=receipts,transfers=transfers,summary_text='\n'.join(lines))
