"""Scoped goods receiving, delivery, and physical count APIs."""
import json

from flask import Blueprint, jsonify, request

from auth import login_required, role_required
from db import get_db
from goods_operations import (
    act_on_delivery, cancel_receipt, create_count, create_delivery, create_receipt,
    delivery_detail, identify_design, post_receipt, transition_count,
    restock_suggestions, save_target, update_count, update_receipt,
)
from inventory_commands import InventoryError, require_inventory_access
from inventory_visibility import can_see_inventory, hide_quantities, inventory_hidden
from validation import invalid_input, read_int
from catalog_identity import resolve_barcode


bp = Blueprint('goods', __name__)


def _error(exc):
    if isinstance(exc, InventoryError):
        return jsonify({'error': str(exc), 'code': exc.code}), exc.status
    return jsonify({'error': 'Inventory access denied',
                    'code': 'inventory_forbidden'}), 403


def _body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


# 入库 receiving, 调拨 transfers and identifying designs belong to the 库存 tab: only accounts that
# may see the store's stock use them (owner, 2026-11-01). 晚盘 counts and 补货 suggestions stay open.
def _hidden(con, store_ids):
    store_ids = {store_id for store_id in store_ids if store_id is not None}
    return inventory_hidden(con, request.jwt_payload, store_ids) if store_ids else None


def _location_store(con, location_id):
    row = con.execute('SELECT store_id FROM inventory_locations WHERE id=?',
                      (location_id if type(location_id) is int else None,)).fetchone()
    return row['store_id'] if row else None


def _receipt_store(con, receipt_id):
    row = con.execute('SELECT store_id FROM goods_receipts WHERE id=?', (receipt_id,)).fetchone()
    return row['store_id'] if row else None


def _delivery_stores(con, delivery_id):
    row = con.execute(
        """SELECT sl.store_id AS source, dl.store_id AS destination FROM inventory_deliveries d
           JOIN inventory_locations sl ON sl.id=d.source_location_id
           JOIN inventory_locations dl ON dl.id=d.destination_location_id
           WHERE d.id=?""", (delivery_id,)).fetchone()
    return (row['source'], row['destination']) if row else (None, None)


@bp.get('/api/goods/barcodes/resolve')
@role_required('staff')
def barcode_resolve():
    try:
        return jsonify(resolve_barcode(
            get_db(), request.args.get('code'), request.args.get('purpose', 'receive')
        ))
    except ValueError as exc:
        return jsonify({'error': str(exc), 'code': 'invalid_input'}), 400


@bp.post('/api/goods/identify')
@role_required('staff')
def design_identify():
    con = get_db()
    hidden = _hidden(con, {_location_store(con, _body().get('location_id'))})
    if hidden:
        return hidden
    try:
        return jsonify(identify_design(
            con, _body(), actor=request.jwt_payload,
            request_key=request.headers.get('Idempotency-Key')))
    except (InventoryError, PermissionError) as exc:
        return _error(exc)
    except ValueError as exc:
        return jsonify(invalid_input(exc)), 400


@bp.post('/api/goods/receipts')
@role_required('staff')
def receipt_create():
    con = get_db()
    store_id = _body().get('store_id')
    hidden = _hidden(con, {store_id if type(store_id) is int else None})
    if hidden:
        return hidden
    try:
        result = create_receipt(con, _body(), actor=request.jwt_payload,
                                request_key=request.headers.get('Idempotency-Key'))
        return jsonify(result), 201
    except (InventoryError, PermissionError) as exc:
        return _error(exc)


@bp.get('/api/goods/receipts/<int:receipt_id>')
@login_required
def receipt_detail(receipt_id):
    con = get_db()
    row = con.execute(
        """SELECT r.* FROM goods_receipts r
           JOIN inventory_access a ON a.store_id=r.store_id
           WHERE r.id=? AND a.auth0_sub=?""",
        (receipt_id, request.jwt_payload.get('sub')),
    ).fetchone()
    if row is None:
        return jsonify({'error': 'Receipt not found'}), 404
    hidden = _hidden(con, {row['store_id']})
    if hidden:
        return hidden
    result = dict(row)
    result['lines'] = [dict(line) for line in con.execute(
        'SELECT * FROM goods_receipt_lines WHERE receipt_id=? ORDER BY line_no',
        (receipt_id,),
    )]
    return jsonify(result)


@bp.patch('/api/goods/receipts/<int:receipt_id>')
@role_required('staff')
def receipt_update(receipt_id):
    con = get_db()
    hidden = _hidden(con, {_receipt_store(con, receipt_id)})
    if hidden:
        return hidden
    try:
        return jsonify(update_receipt(
            con, receipt_id, _body(), actor=request.jwt_payload,
            request_key=request.headers.get('Idempotency-Key')))
    except (InventoryError, PermissionError) as exc:
        return _error(exc)


@bp.post('/api/goods/receipts/<int:receipt_id>/post')
@role_required('staff')
def receipt_post(receipt_id):
    con = get_db()
    hidden = _hidden(con, {_receipt_store(con, receipt_id)})
    if hidden:
        return hidden
    try:
        return jsonify(post_receipt(
            con, receipt_id, _body(), actor=request.jwt_payload,
            request_key=request.headers.get('Idempotency-Key')))
    except (InventoryError, PermissionError) as exc:
        return _error(exc)


@bp.post('/api/goods/receipts/<int:receipt_id>/cancel')
@role_required('staff')
def receipt_cancel(receipt_id):
    con = get_db()
    hidden = _hidden(con, {_receipt_store(con, receipt_id)})
    if hidden:
        return hidden
    try:
        return jsonify(cancel_receipt(
            con, receipt_id, _body(), actor=request.jwt_payload,
            request_key=request.headers.get('Idempotency-Key')))
    except (InventoryError, PermissionError) as exc:
        return _error(exc)


@bp.get('/api/goods/transfers')
@role_required('staff')
def incoming_transfers():
    con = get_db()
    try:
        store_id = read_int(request.args.get('store_id'), 'store_id', minimum=1)
        hidden = _hidden(con, {store_id})
        if hidden:
            return hidden
        require_inventory_access(con, request.jwt_payload, (store_id,), 'staff')
    except ValueError as exc:
        return jsonify(invalid_input(exc)), 400
    except PermissionError as exc:
        return _error(exc)
    transfers = {}
    for row in con.execute(
        """SELECT d.id,d.status,d.business_date,ss.name AS source_store_name,
                  src.name AS source_location_name,dst.name AS destination_location_name,
                  line.line_no,line.product_id,line.native_unit,p.sku,
                  COALESCE(NULLIF(TRIM(p.jizhanming),''),NULLIF(TRIM(p.name_cn_en),''),p.sku) AS product_name,
                  line.dispatched_quantity-line.received_quantity-line.returned_quantity-line.loss_quantity AS outstanding_transit,
                  line.requested_quantity-line.dispatched_quantity-line.short_quantity AS awaiting_dispatch
           FROM inventory_deliveries d
           JOIN inventory_locations src ON src.id=d.source_location_id
           JOIN stores ss ON ss.id=src.store_id
           JOIN inventory_locations dst ON dst.id=d.destination_location_id
           JOIN inventory_delivery_lines line ON line.delivery_id=d.id
           JOIN products p ON p.id=line.product_id
           WHERE dst.store_id=? AND d.kind='transfer'
             AND d.status IN ('planned','active')
           ORDER BY d.business_date,d.id,line.line_no""", (store_id,)
    ):
        transfer = transfers.setdefault(row['id'], {
            key: row[key] for key in ('id','status','business_date',
                                     'source_store_name','source_location_name',
                                     'destination_location_name')
        })
        transfer.setdefault('lines', []).append({
            key: row[key] for key in ('line_no','product_id','product_name','sku','native_unit',
                                     'outstanding_transit','awaiting_dispatch')
        })
    return jsonify(list(transfers.values()))


@bp.post('/api/goods/transfers')
@role_required('manager')
def transfer_create():
    try:
        result = create_delivery(
            get_db(), _body(), actor=request.jwt_payload,
            request_key=request.headers.get('Idempotency-Key'), kind='transfer')
        return jsonify(result), 201
    except (InventoryError, PermissionError) as exc:
        return _error(exc)


@bp.get('/api/goods/transfers/<int:delivery_id>')
@login_required
def transfer_detail(delivery_id):
    con = get_db()
    stores = [store_id for store_id in _delivery_stores(con, delivery_id) if store_id is not None]
    if stores and not any(can_see_inventory(con, request.jwt_payload, (store_id,)) for store_id in stores):
        return _hidden(con, stores)
    try:
        return jsonify(delivery_detail(con, delivery_id,
                                       actor=request.jwt_payload))
    except (InventoryError, PermissionError) as exc:
        return _error(exc)


@bp.post('/api/goods/transfers/<int:delivery_id>/<action>')
@role_required('staff')
def transfer_action(delivery_id, action):
    con = get_db()
    source, destination = _delivery_stores(con, delivery_id)
    hidden = _hidden(con, {destination if action == 'receive' else source})
    if hidden:
        return hidden
    try:
        return jsonify(act_on_delivery(
            con, delivery_id, action, _body(), actor=request.jwt_payload,
            request_key=request.headers.get('Idempotency-Key')))
    except (InventoryError, PermissionError) as exc:
        return _error(exc)


@bp.post('/api/goods/counts')
@role_required('staff')
def count_create():
    try:
        result = create_count(get_db(), _body(), actor=request.jwt_payload,
                              request_key=request.headers.get('Idempotency-Key'))
        return jsonify(result), 201
    except (InventoryError, PermissionError) as exc:
        return _error(exc)


@bp.patch('/api/goods/counts/<int:count_id>')
@role_required('staff')
def count_update(count_id):
    try:
        return jsonify(update_count(get_db(), count_id, _body(), actor=request.jwt_payload,
                                    request_key=request.headers.get('Idempotency-Key')))
    except (InventoryError, PermissionError) as exc:
        return _error(exc)
    except ValueError as exc:
        return jsonify(invalid_input(exc)), 400


@bp.get('/api/goods/counts/<int:count_id>')
@login_required
def count_detail(count_id):
    con = get_db()
    row = con.execute(
        """SELECT c.* FROM inventory_counts c
           JOIN inventory_access a ON a.store_id=c.store_id
           WHERE c.id=? AND a.auth0_sub=?""",
        (count_id, request.jwt_payload.get('sub')),
    ).fetchone()
    if row is None:
        return jsonify({'error': 'Count not found'}), 404
    result = dict(row)
    if row['status'] == 'returned':
        returned = con.execute(
            "SELECT stored_result FROM operation_requests WHERE operation='count_return' AND resource_id=?",
            (count_id,),
        ).fetchone()
        if returned:
            result['recount_id'] = json.loads(returned['stored_result'])['recount_id']
    result['lines'] = [dict(line) for line in con.execute(
        'SELECT * FROM inventory_count_lines WHERE count_id=? ORDER BY line_no',
        (count_id,),
    )]
    return jsonify(result)


@bp.post('/api/goods/counts/<int:count_id>/<action>')
@role_required('staff')
def count_action(count_id, action):
    if action not in {'submit', 'approve', 'return'}:
        return jsonify({'error': 'Count action not found'}), 404
    try:
        return jsonify(transition_count(
            get_db(), count_id, action, _body(), actor=request.jwt_payload,
            request_key=request.headers.get('Idempotency-Key')))
    except (InventoryError, PermissionError) as exc:
        return _error(exc)


@bp.put('/api/goods/targets')
@role_required('manager')
def target_save():
    try:
        return jsonify(save_target(
            get_db(), _body(), actor=request.jwt_payload,
            request_key=request.headers.get('Idempotency-Key')))
    except (InventoryError, PermissionError) as exc:
        return _error(exc)


@bp.get('/api/goods/restock-suggestions')
@role_required('staff')
def suggestion_list():
    con = get_db()
    try:
        result = restock_suggestions(con, request.args.get('location_id'), actor=request.jwt_payload)
    except (InventoryError, PermissionError) as exc:
        return _error(exc)
    if not can_see_inventory(con, request.jwt_payload, (_location_store(con, result['location_id']),)):
        # Without 库存查看, 补货 says only whether upstairs is out of stock (owner, 2026-11-01).
        result['items'] = [hide_quantities({**item, 'floor_quantity': None, 'outstanding_inbound': None,
                                            'suggested_quantity': None}, 'available_back_quantity')
                           for item in result['items']]
    return jsonify(result)
