"""Authenticated cashier queue and assigned phone evidence routes."""
from flask import Blueprint, jsonify, request, send_file

from auth import role_required
from checkout_access import checkout_access, require_checkout
from checkout_operations import (attach_photo, change_checkout, checkout_detail,
    checkout_row, create_checkout, manager)
from db import get_db
from goods_operations import _begin, _remember, _replay
from inventory_commands import InventoryError, InventoryConflict
from payment_evidence import evidence_path, prepare_image, publish, remove_unreferenced
from sales_operations import record_late_adjustment
from validation import read_date, read_int

bp = Blueprint('checkout', __name__)


@bp.errorhandler(InventoryError)
@bp.errorhandler(ValueError)
@bp.errorhandler(PermissionError)
def error(exc):
    if isinstance(exc, InventoryError):
        return jsonify(error=str(exc), code=exc.code), exc.status
    if isinstance(exc, PermissionError):
        return jsonify(error='Checkout access denied'), 403
    return jsonify(error=str(exc)), 400


def body():
    value = request.get_json(silent=True)
    return value if isinstance(value, dict) else {}


@bp.get('/api/checkouts/access')
@role_required('staff')
def access():
    from blueprints.clover_sandbox import enabled
    return jsonify(**checkout_access(get_db(), request.jwt_payload), clover_sandbox_enabled=enabled())


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
    elif scope['role'] == 'staff':
        where.append('created_by=?'); values.append(actor['sub'])
    if request.args.get('business_date'):
        where.append('business_date=?'); values.append(read_date(request.args['business_date'], 'business_date'))
    if request.args.get('before_id'):
        where.append('id<?'); values.append(read_int(request.args['before_id'], 'before_id', minimum=1))
    rows = con.execute('SELECT id FROM checkout_orders WHERE ' + ' AND '.join(where) + ' ORDER BY id DESC LIMIT 101', values).fetchall()
    staff = [dict(row) for row in con.execute("""SELECT a.auth0_sub AS value,
        COALESCE(NULLIF(e.name,''),a.auth0_sub) AS label FROM inventory_access a
        JOIN employees e ON e.auth0_id=a.auth0_sub AND e.is_active=1
        WHERE a.store_id=? ORDER BY label""", (store_id,))] if view == 'live' else []
    return jsonify(orders=[checkout_detail(con,row['id'],actor) for row in rows[:100]],staff=staff,
                   next_before_id=rows[99]['id'] if len(rows)>100 else None,
                   clover={'connected':False,'message':'Clover is not connected. Payments are recorded manually.'})


@bp.post('/api/checkouts')
@role_required('staff')
def create():
    return jsonify(create_checkout(get_db(),body(),request.jwt_payload,request.headers.get('Idempotency-Key'))),201


@bp.get('/api/checkouts/<int:checkout_id>')
@role_required('staff')
def detail(checkout_id):
    return jsonify(checkout_detail(get_db(),checkout_id,request.jwt_payload))


@bp.post('/api/checkouts/<int:checkout_id>/<action>')
@role_required('staff')
def change(checkout_id,action):
    return jsonify(change_checkout(get_db(),checkout_id,action,body(),request.jwt_payload,request.headers.get('Idempotency-Key')))


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
