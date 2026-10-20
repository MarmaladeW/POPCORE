"""Authenticated cashier queue and assigned phone evidence routes."""
import time

from flask import Blueprint, jsonify, request, send_file

import clover_checkout
import exchange_rates
from auth import role_required
from checkout_access import REGISTER_SUB, checkout_access, require_checkout
from checkout_operations import (attach_photo, change_checkout, checkout_detail, receive_manual, undo_manual,
    checkout_row, create_checkout, is_clover, manager, read_order_note)
from db import get_db
from goods_operations import _begin, _remember, _replay
from inventory_commands import InventoryError, InventoryConflict
from payment_evidence import evidence_path, prepare_image, publish, remove_unreferenced
from sales_operations import record_late_adjustment
from validation import read_date, read_int

bp = Blueprint('checkout', __name__)
TENDERS = ('cash', 'card', 'e_transfer', 'wechat', 'alipay')
# Keyword search: the Clover or manual reference, the order note, and every item name (also Chinese names,
# which the stored JSON keeps escaped, so names are compared after json_extract decodes them).
SEARCH = """(reference LIKE ? ESCAPE '\\' OR note LIKE ? ESCAPE '\\'
    OR EXISTS(SELECT 1 FROM json_each(checkout_orders.payload, '$.lines') line
        LEFT JOIN products p ON p.id=json_extract(line.value, '$.product_id')
        WHERE json_extract(line.value, '$.product_name_snapshot') LIKE ? ESCAPE '\\'
           OR json_extract(line.value, '$.raw_product_text') LIKE ? ESCAPE '\\'
           OR p.name_cn_en LIKE ? ESCAPE '\\' OR p.jizhanming LIKE ? ESCAPE '\\' OR p.sku LIKE ? ESCAPE '\\')
    OR EXISTS(SELECT 1 FROM clover_orders c, json_each(c.snapshot, '$.items') item
        WHERE c.checkout_id=checkout_orders.id AND json_extract(item.value, '$.name') LIKE ? ESCAPE '\\'))"""


@bp.errorhandler(InventoryError)
@bp.errorhandler(ValueError)
@bp.errorhandler(PermissionError)
@bp.errorhandler(clover_checkout.ConnectorError)
def error(exc):
    if isinstance(exc, clover_checkout.ConnectorError):
        return jsonify(error=str(exc), code=exc.code), 503
    if isinstance(exc, InventoryError):
        return jsonify(error=str(exc), code=exc.code), exc.status
    if isinstance(exc, PermissionError):
        return jsonify(error='Checkout access denied'), 403
    return jsonify(error=str(exc)), 400


def body():
    value = request.get_json(silent=True)
    return value if isinstance(value, dict) else {}


def _clover_sync(con, store_id):
    """Pull Clover's latest orders for the Clover store before showing them (rate-limited)."""
    cfg = clover_checkout.store_config(store_id)
    if cfg is None:
        return None
    try:
        clover_checkout.sync(con, cfg)
    except (InventoryError, PermissionError, ValueError) as exc:
        return {'enabled': True, 'connected': False, 'message': str(exc)[:200], 'environment': cfg['environment'],
                'writes': False, 'fetched_at': 0, 'server_ms': 0}
    return clover_checkout.feed_status(con, cfg)


@bp.get('/api/checkouts/access')
@role_required('staff')
def access():
    scope = checkout_access(get_db(), request.jwt_payload)
    try:
        cfg = clover_checkout.config()
    except ValueError:
        cfg = None
    # WeChat Pay and Alipay: the rate set in Settings, otherwise today's reference rate.
    source = 'Settings' if scope['cny_per_cad'] else None
    if not scope['cny_per_cad']:
        live = exchange_rates.cny_per_cad()
        if live:
            scope, source = {**scope, 'cny_per_cad': live['rate']}, f"{live['source']} reference rate, {live['date']}"
    return jsonify(**scope, cny_rate_source=source, clover_store_id=cfg['store_id'] if cfg else None,
                   clover_environment=cfg['environment'] if cfg else None)


@bp.get('/api/checkouts')
@role_required('staff')
def queue():
    con = get_db(); actor = request.jwt_payload
    scope = checkout_access(con, actor)
    store_id = read_int(request.args.get('store_id'), 'store_id', minimum=1)
    view = request.args.get('view', 'live')
    if view not in ('live', 'history'):
        raise ValueError('view must be live or history')
    if view == 'live' and store_id not in {s['id'] for s in scope['live_stores']}:
        raise PermissionError('Checkout shift access denied')
    if view == 'history' and scope['role'] != 'staff' and store_id not in {s['id'] for s in scope['history_stores']}:
        raise PermissionError('Checkout history access denied')
    where = ['store_id=?']; values = [store_id]
    if view == 'live':
        where.append("status='open'")
    elif scope['role'] == 'staff' and store_id in {s['id'] for s in scope['live_stores']}:
        # Staff on shift also see the sales the Clover register recorded today.
        where.append('(created_by=? OR (created_by=? AND business_date=?))')
        values += [actor['sub'], REGISTER_SUB, scope['business_date']]
    elif scope['role'] == 'staff':
        where.append('created_by=?'); values.append(actor['sub'])
    if request.args.get('business_date'):
        where.append('business_date=?'); values.append(read_date(request.args['business_date'], 'business_date'))
    if request.args.get('before_id'):
        where.append('id<?'); values.append(read_int(request.args['before_id'], 'before_id', minimum=1))
    if request.args.get('tender'):
        if request.args['tender'] not in TENDERS:
            raise ValueError('tender must be cash, card, e_transfer, wechat or alipay')
        where.append("EXISTS(SELECT 1 FROM checkout_attempts a WHERE a.checkout_id=checkout_orders.id AND a.status='completed' AND a.tender=?)")
        values.append(request.args['tender'])
    text = (request.args.get('q') or '').strip()
    if text:
        if len(text) > 80:
            raise ValueError('Search for at most 80 characters')
        like = '%' + text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        where.append(SEARCH); values += [like] * SEARCH.count('?')
    clover = _clover_sync(con, store_id)  # history shows the same live Clover status
    rows = con.execute('SELECT id FROM checkout_orders WHERE ' + ' AND '.join(where) + ' ORDER BY id DESC LIMIT 101', values).fetchall()
    staff = [dict(row) for row in con.execute("""SELECT a.auth0_sub AS value,
        COALESCE(NULLIF(e.name,''),a.auth0_sub) AS label FROM inventory_access a
        JOIN employees e ON e.auth0_id=a.auth0_sub AND e.is_active=1
        WHERE a.store_id=? ORDER BY label""", (store_id,))] if view == 'live' else []
    return jsonify(orders=[checkout_detail(con,row['id'],actor) for row in rows[:100]],staff=staff,
                   next_before_id=rows[99]['id'] if len(rows)>100 else None,
                   clover=clover or {'enabled':False,'connected':False,
                                     'message':'Clover is not connected for this store. Payments are recorded manually.'})


@bp.get('/api/checkouts/clover/<order_id>')
@role_required('staff')
def find_clover(order_id):
    """Find the POPCORE checkout of a Clover order by the ID the Clover Orders app shows."""
    con = get_db(); actor = request.jwt_payload
    store_id = read_int(request.args.get('store_id'), 'store_id', minimum=1)
    checkout_id = clover_checkout.find(con, store_id, order_id)
    if checkout_id is None:
        return jsonify(error='No POPCORE order has that Clover ID at this store'), 404
    return jsonify(checkout_detail(con, checkout_id, actor))


@bp.post('/api/checkouts')
@role_required('staff')
def create():
    return jsonify(create_checkout(get_db(),body(),request.jwt_payload,request.headers.get('Idempotency-Key'))),201


@bp.get('/api/checkouts/<int:checkout_id>')
@role_required('staff')
def detail(checkout_id):
    con = get_db(); actor = request.jwt_payload
    row = checkout_row(con, checkout_id, actor)
    if row['status'] == 'open' and is_clover(con, row['id']):
        cfg = clover_checkout.store_config(row['store_id'])
        link = con.execute('SELECT * FROM clover_orders WHERE checkout_id=?', (row['id'],)).fetchone()
        if cfg and link['fetch_next_at'] <= time.time():
            # An open order on screen reads straight from Clover, so its price is exact.
            try:
                clover_checkout.refresh_one(con, cfg, link)
            except (clover_checkout.ConnectorError, InventoryError, PermissionError, ValueError):
                pass
    return jsonify(checkout_detail(con,checkout_id,actor))


@bp.post('/api/checkouts/<int:checkout_id>/notes')
@role_required('staff')
def save_note(checkout_id):
    con = get_db(); actor = request.jwt_payload
    row = checkout_row(con, checkout_id, actor)
    if row['created_by'] != actor['sub'] and not manager(actor):
        raise PermissionError('Order note access denied')
    note, expected = read_order_note(body())
    if row['note'] != note:
        if row['note'] != expected:
            raise InventoryConflict('Order note changed. Refresh before saving.', 'checkout_note_conflict')
        with con:
            changed = con.execute('UPDATE checkout_orders SET note=? WHERE id=? AND note=?',
                                  (note, checkout_id, expected)).rowcount
            if not changed:
                raise InventoryConflict('Order note changed. Refresh before saving.', 'checkout_note_conflict')
    return jsonify(checkout_detail(con, checkout_id, actor))


@bp.post('/api/checkouts/<int:checkout_id>/<action>')
@role_required('staff')
def change(checkout_id,action):
    con = get_db()
    if action in clover_checkout.ACTIONS and is_clover(con, checkout_id):
        return jsonify(clover_checkout.change(con,checkout_id,action,body(),request.jwt_payload,
                                              request.headers.get('Idempotency-Key')))
    if action in ('receive', 'undo'):
        # Manual orders are paid the same way as Clover orders, with card recorded in POPCORE.
        operation = receive_manual if action == 'receive' else undo_manual
        return jsonify(operation(con,checkout_id,body(),request.jwt_payload,request.headers.get('Idempotency-Key')))
    return jsonify(change_checkout(con,checkout_id,action,body(),request.jwt_payload,request.headers.get('Idempotency-Key')))


def photo_scope(con,checkout_id,attempt_id,actor, *, write=False):
    order = checkout_row(con,checkout_id,actor)
    if write:
        require_checkout(con, order, actor, write=True)
    attempt_id = read_int(attempt_id,'attempt_id',minimum=1)
    attempt = con.execute('SELECT * FROM checkout_attempts WHERE id=? AND checkout_id=?',(attempt_id,checkout_id)).fetchone()
    if attempt is None:
        raise ValueError('Payment attempt does not belong to this checkout')
    if actor['sub'] not in (attempt['assigned_to'],order['created_by']) and not manager(actor):
        raise PermissionError('Photo access denied')
    return order,attempt


@bp.post('/api/checkouts/<int:checkout_id>/attempts/<int:attempt_id>/evidence')
@role_required('staff')
def upload(checkout_id,attempt_id):
    con = get_db(); actor = request.jwt_payload; published = None
    photo_scope(con,checkout_id,attempt_id,actor, write=True)
    prepared = prepare_image(request.files.get('image'))
    intent = {'id':checkout_id,'attempt_id':attempt_id,'hash':prepared['content_hash']}
    _begin(con)
    try:
        order,attempt = photo_scope(con,checkout_id,attempt_id,actor, write=True)
        key,digest,prior = _replay(con,request.headers.get('Idempotency-Key'),'checkout_photo',actor,intent)
        if prior:
            con.commit()
            return jsonify(prior),201
        if order['status'] == 'cancelled' or attempt['status'] == 'cancelled':
            raise InventoryConflict('This attempt was cancelled. Its photos cannot be moved to another payment.','attempt_state_conflict')
        published = publish(prepared)
        photo_id = con.execute('''INSERT INTO checkout_evidence(attempt_id,object_id,mime_type,byte_size,uploader_sub)
            VALUES (?,?,?,?,?)''',(attempt_id,prepared['object_id'],prepared['mime_type'],prepared['byte_size'],actor['sub'])).lastrowid
        if attempt['payment_id']:
            attach_photo(con,con.execute('SELECT * FROM checkout_evidence WHERE id=?',(photo_id,)).fetchone(),attempt['payment_id'])
        con.execute('UPDATE checkout_orders SET version=version+1 WHERE id=?',(checkout_id,))
        record_late_adjustment(con,order['store_id'],order['business_date'],'checkout_photo',photo_id,'Checkout evidence added after store-day close',actor['sub'])
        result = {'id':photo_id,'attempt_id':attempt_id}
        _remember(con,key,'checkout_photo',photo_id,actor,digest,result)
        con.commit()
        return jsonify(result),201
    except Exception:
        con.rollback()
        if published:
            remove_unreferenced(published,con)
        raise


@bp.get('/api/checkouts/<int:checkout_id>/evidence/<int:photo_id>')
@role_required('staff')
def content(checkout_id,photo_id):
    con = get_db(); actor = request.jwt_payload
    checkout_row(con,checkout_id,actor)
    photo_id = read_int(photo_id,'photo_id',minimum=1)
    photo = con.execute('SELECT * FROM checkout_evidence WHERE id=?',(photo_id,)).fetchone()
    if photo is None:
        raise ValueError('Photo does not exist')
    photo_scope(con,checkout_id,photo['attempt_id'],actor)
    response = send_file(evidence_path(photo['object_id']),mimetype=photo['mime_type'])
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response
