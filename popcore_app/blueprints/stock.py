"""
blueprints/stock.py — stock queries, movements, adjustments, export.
"""
from datetime import date
import json
from flask import Blueprint, request, jsonify, Response

from db import get_db, esc_csv, _ensure_stock_row
from auth import login_required, role_required
from validation import SQLITE_INTEGER_MAX, invalid_input, read_date, read_int
from blueprints.stores import _resolve_store
from inventory_commands import (
    InventoryError, post_inventory, require_inventory_access,
)


def _missing_product(cur, product_id):
    return cur.execute(
        'SELECT 1 FROM products WHERE id = ?', (product_id,)
    ).fetchone() is None

bp = Blueprint('stock', __name__)


def _inventory_error(exc):
    return jsonify({'error': str(exc), 'code': exc.code}), exc.status


def _inventory_store(con, store_code):
    resolved = _resolve_store(con, (store_code or '').strip().upper())
    if resolved is None:
        return None
    return resolved


def _is_authoritative(con):
    row = con.execute('SELECT mode FROM inventory_mode WHERE id=1').fetchone()
    return row is not None and row['mode'] == 'authoritative'


def _inventory_location(con, store_id, *codes):
    placeholders = ','.join('?' * len(codes))
    row = con.execute(
        f"""SELECT id FROM inventory_locations
            WHERE store_id=? AND is_active=1 AND code IN ({placeholders})
            ORDER BY CASE code WHEN ? THEN 0 ELSE 1 END LIMIT 1""",
        (store_id, *codes, codes[0]),
    ).fetchone()
    return row['id'] if row else None


def _authoritative_stock_adapter(con, data, store_id, operation, product_id,
                                 quantity, business_date, notes='', location=None,
                                 new_quantity=None):
    request_key = request.headers.get('Idempotency-Key', '').strip()
    if not request_key:
        raise InventoryError(
            'Idempotency-Key is required in authoritative inventory mode',
            'idempotency_key_required', 400,
        )
    prior_line = con.execute(
        """SELECT l.from_version, l.to_version, l.quantity,
                  l.from_location_id, l.to_location_id
           FROM inventory_documents d
           JOIN inventory_document_lines l ON l.document_id=d.id AND l.line_no=1
           WHERE d.request_key=?""", (request_key,),
    ).fetchone()
    product = con.execute(
        'SELECT stock_unit FROM products WHERE id=?', (product_id,)
    ).fetchone()
    unit = product['stock_unit'] if product else None
    floor = _inventory_location(con, store_id, 'floor')
    back = _inventory_location(con, store_id, 'upstairs', 'warehouse')
    if floor is None or back is None:
        raise InventoryError('Reviewed inventory locations are missing',
                             'inventory_store_missing', 409)

    def current(location_id):
        return con.execute(
            """SELECT quantity, version FROM inventory_balances
               WHERE product_id=? AND location_id=? AND disposition='saleable'""",
            (product_id, location_id),
        ).fetchone()

    if operation == 'restock_upstairs':
        to = current(back)
        kind = 'receipt'
        line = {'product_id': product_id, 'quantity': quantity, 'unit': unit,
                'to_location_id': back, 'to_disposition': 'saleable',
                'expected_versions': {'to': (prior_line['to_version'] if prior_line
                                              else to['version'] if to else 0)}}
    elif operation == 'ru_dian':
        source, target = current(back), current(floor)
        kind = 'move'
        line = {'product_id': product_id, 'quantity': quantity, 'unit': unit,
                'from_location_id': back, 'from_disposition': 'saleable',
                'to_location_id': floor, 'to_disposition': 'saleable',
                'expected_versions': {
                    'from': (prior_line['from_version'] if prior_line
                             else source['version'] if source else 0),
                    'to': (prior_line['to_version'] if prior_line
                           else target['version'] if target else 0),
                }}
    elif operation == 'adjust':
        target_id = floor if location == 'instore' else back
        balance = current(target_id)
        old_quantity = balance['quantity'] if balance else 0
        delta = new_quantity - old_quantity
        if prior_line and delta == 0:
            stored = con.execute(
                """SELECT stored_result FROM inventory_documents
                   WHERE request_key=? AND status='posted'""", (request_key,)
            ).fetchone()
            if stored and stored['stored_result']:
                result = json.loads(stored['stored_result'])
                return {**result,
                        'upstairs_qty': current(back)['quantity'] if current(back) else 0,
                        'instore_qty': current(floor)['quantity'] if current(floor) else 0}
        if delta == 0:
            raise InventoryError('New quantity matches the current balance',
                                 'no_change', 400)
        if not str(notes).strip():
            raise InventoryError('A correction reason is required',
                                 'reason_required', 400)
        kind = 'correction'
        endpoint = ({'to_location_id': target_id, 'to_disposition': 'saleable',
                     'expected_versions': {'to': (prior_line['to_version'] if prior_line
                                                  else balance['version'] if balance else 0)}}
                    if delta > 0 else
                    {'from_location_id': target_id, 'from_disposition': 'saleable',
                     'expected_versions': {'from': (prior_line['from_version'] if prior_line
                                                    else balance['version'] if balance else 0)}})
        line = {'product_id': product_id, 'quantity': abs(delta), 'unit': unit,
                **endpoint}
    else:
        raise InventoryError('Legacy operation has no authoritative meaning',
                             'migration_required', 409)

    result = post_inventory(
        con, {'kind': kind, 'business_date': business_date,
              'reason': str(notes).strip() or operation, 'lines': [line]},
        actor=request.jwt_payload, request_key=request_key,
    )
    floor_balance, back_balance = current(floor), current(back)
    return {**result,
            'upstairs_qty': back_balance['quantity'] if back_balance else 0,
            'instore_qty': floor_balance['quantity'] if floor_balance else 0}


@bp.route('/api/inventory/commands', methods=['POST'])
@login_required
def post_inventory_command():
    request_key = request.headers.get('Idempotency-Key', '').strip()
    if not request_key:
        return jsonify({'error': 'Idempotency-Key is required',
                        'code': 'idempotency_key_required'}), 400
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({'error': 'Expected a JSON object',
                        'code': 'invalid_input'}), 400
    if payload.get('kind') == 'opening':
        return jsonify({'error': 'Opening is available only in the migration rehearsal',
                        'code': 'opening_not_public'}), 400
    con = get_db()
    try:
        existed = con.execute(
            'SELECT 1 FROM inventory_documents WHERE request_key=?',
            (request_key,),
        ).fetchone() is not None
        result = post_inventory(
            con, payload, actor=request.jwt_payload, request_key=request_key,
        )
        return jsonify(result), 200 if existed else 201
    except PermissionError:
        if con.in_transaction:
            con.rollback()
        return jsonify({'error': 'Inventory access denied',
                        'code': 'inventory_forbidden'}), 403
    except InventoryError as exc:
        if con.in_transaction:
            con.rollback()
        return _inventory_error(exc)
    finally:
        con.close()


@bp.route('/api/inventory/balances')
@login_required
def get_inventory_balances():
    con = get_db()
    try:
        resolved = _inventory_store(con, request.args.get('store_code'))
        if resolved is None:
            return jsonify({'error': 'Valid store_code is required',
                            'code': 'invalid_input'}), 400
        store_id, store_code = resolved
        try:
            require_inventory_access(
                con, request.jwt_payload, (store_id,), 'viewer'
            )
        except PermissionError:
            return jsonify({'error': 'Inventory access denied',
                            'code': 'inventory_forbidden'}), 403
        rows = con.execute(
            """SELECT b.product_id, p.sku, p.jizhanming, p.name_cn_en,
                      p.stock_unit AS unit, p.identity_status,
                      b.location_id, l.code AS location_code,
                      l.name AS location_name, b.disposition,
                      b.quantity, b.version, ss.opening_verified
               FROM inventory_balances b
               JOIN products p ON p.id=b.product_id
               JOIN inventory_locations l ON l.id=b.location_id
               JOIN inventory_scope_state ss ON ss.location_id=l.id
               WHERE l.store_id=?
               ORDER BY p.sku, l.code, b.disposition""", (store_id,)
        ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item['opening_verified'] = bool(item['opening_verified'])
            items.append(item)
        mode = con.execute(
            'SELECT mode FROM inventory_mode WHERE id=1'
        ).fetchone()['mode']
        return jsonify({'store_code': store_code, 'mode': mode, 'items': items})
    finally:
        con.close()


@bp.route('/api/inventory/documents/<int:document_id>')
@login_required
def get_inventory_document(document_id):
    con = get_db()
    try:
        document = con.execute(
            """SELECT id, kind, source_type, source_id, actor_sub,
                      business_date, posted_at, correction_of, status
               FROM inventory_documents WHERE id=? AND status='posted'""",
            (document_id,),
        ).fetchone()
        if document is None:
            return jsonify({'error': 'Inventory document not found',
                            'code': 'not_found'}), 404
        store_ids = [row['store_id'] for row in con.execute(
            """SELECT DISTINCT l.store_id
               FROM inventory_movements m
               JOIN inventory_locations l ON l.id=m.location_id
               WHERE m.document_id=?""", (document_id,)
        )]
        try:
            require_inventory_access(
                con, request.jwt_payload, store_ids, 'viewer'
            )
        except PermissionError:
            return jsonify({'error': 'Inventory access denied',
                            'code': 'inventory_forbidden'}), 403
        lines = [dict(row) for row in con.execute(
            """SELECT line_no, product_id, native_unit, quantity,
                      from_location_id, from_disposition,
                      to_location_id, to_disposition,
                      from_version, to_version,
                      conversion_id, conversion_factor
               FROM inventory_document_lines WHERE document_id=?
               ORDER BY line_no""", (document_id,)
        )]
        movements = [dict(row) for row in con.execute(
            """SELECT id, line_no, product_id, location_id,
                      disposition, quantity
               FROM inventory_movements WHERE document_id=? ORDER BY id""",
            (document_id,),
        )]
        corrections = [row['id'] for row in con.execute(
            """SELECT id FROM inventory_documents
               WHERE correction_of=? AND status='posted' ORDER BY id""",
            (document_id,),
        )]
        return jsonify({**dict(document), 'lines': lines,
                        'movements': movements, 'corrections': corrections})
    finally:
        con.close()


def _require_store_param(con):
    """Read store_code from GET query params, validate, return (store_id, code) or error tuple."""
    store_code = (request.args.get('store_code') or '').strip().upper()
    if not store_code:
        return None, None, (jsonify({'error': 'store_code is required'}), 400)
    if store_code == 'ALL':
        return None, 'ALL', None
    resolved = _resolve_store(con, store_code)
    if resolved is None:
        return None, None, (jsonify({'error': 'Invalid store code'}), 400)
    store_id, store_code = resolved
    return store_id, store_code, None


def _require_store_body(con, data):
    """Read store_code from POST/PATCH body dict, validate, return (store_id, code) or error tuple."""
    store_code = (data.get('store_code') or '').strip().upper()
    if not store_code:
        return None, None, (jsonify({'error': 'store_code is required'}), 400)
    if store_code == 'ALL':
        return None, None, (jsonify({'error': 'Cannot write with store_code ALL. Select a specific store.'}), 400)
    resolved = _resolve_store(con, store_code)
    if resolved is None:
        return None, None, (jsonify({'error': 'Invalid store code'}), 400)
    store_id, store_code = resolved
    return store_id, store_code, None


@bp.route('/api/stock')
@login_required
def get_all_stock():
    """
    Return products with stock, joined with product info.
    When the 'page' param is provided, returns {items, total, page, page_size}.
    Without 'page', returns a plain array (backward-compatible).
    include_all=1 returns all products even those without a stock row.
    """
    include_all = request.args.get('include_all', '0') == '1'
    series = request.args.get('series', '').strip()
    q = request.args.get('q', '').strip().lower()

    page_param = request.args.get('page')
    paginated = page_param is not None
    if paginated:
        try:
            page      = max(1, int(page_param))
            page_size = min(500, max(1, int(request.args.get('page_size', 100))))
        except (ValueError, TypeError):
            return jsonify({'error': 'page and page_size must be integers'}), 400
        offset = (page - 1) * page_size

    con = get_db()
    store_id, store_code, err = _require_store_param(con)
    if err:
        con.close()
        return err

    cur = con.cursor()

    filters = []
    params = []
    if series:
        filters.append("p.ip_series = ?")
        params.append(series)
    if q:
        for token in q.split():
            filters.append("p.search_blob LIKE ?")
            params.append(f'%{token}%')

    where = ('AND ' + ' AND '.join(filters)) if filters else ''

    if store_code == 'ALL':
        if paginated:
            cur.execute(
                f'SELECT COUNT(*) FROM products p WHERE 1=1 {where}',
                params)
            total = cur.fetchone()[0]
        cur.execute(f'''
            SELECT p.id, p.sku, p.name_cn_en, p.jizhanming, p.price,
                   p.ip_series, p.product_type, p.boxes_per_dan,
                   p.stock_unit, p.identity_status,
                   COALESCE(SUM(s.upstairs_qty), 0) AS upstairs_qty,
                   COALESCE(SUM(s.instore_qty),  0) AS instore_qty,
                   COALESCE(MAX(s.last_updated), '') AS last_updated,
                   '' AS stock_notes
            FROM products p
            LEFT JOIN stock s ON s.product_id = p.id
            WHERE 1=1 {where}
            GROUP BY p.id, p.sku, p.name_cn_en, p.jizhanming, p.price,
                     p.ip_series, p.product_type, p.boxes_per_dan
            ORDER BY p.ip_series, p.sku DESC
            {'LIMIT ? OFFSET ?' if paginated else ''}
        ''', params + ([page_size, offset] if paginated else []))
    elif include_all:
        if paginated:
            cur.execute(
                f'SELECT COUNT(*) FROM products p'
                f' LEFT JOIN stock s ON s.product_id = p.id AND s.store_id = ?'
                f' WHERE 1=1 {where}',
                [store_id] + params)
            total = cur.fetchone()[0]
        cur.execute(f'''
            SELECT p.id, p.sku, p.name_cn_en, p.jizhanming, p.price,
                   p.ip_series, p.product_type, p.boxes_per_dan,
                   p.stock_unit, p.identity_status,
                   COALESCE(s.upstairs_qty, 0) AS upstairs_qty,
                   COALESCE(s.instore_qty,  0) AS instore_qty,
                   COALESCE(s.last_updated, '') AS last_updated,
                   COALESCE(s.notes, '')        AS stock_notes
            FROM products p
            LEFT JOIN stock s ON s.product_id = p.id AND s.store_id = ?
            WHERE 1=1 {where}
            ORDER BY p.ip_series, p.sku DESC
            {'LIMIT ? OFFSET ?' if paginated else ''}
        ''', [store_id] + params + ([page_size, offset] if paginated else []))
    else:
        if paginated:
            cur.execute(
                f'SELECT COUNT(*) FROM stock s JOIN products p ON p.id = s.product_id'
                f' WHERE s.store_id = ? {where}',
                [store_id] + params)
            total = cur.fetchone()[0]
        cur.execute(f'''
            SELECT p.id, p.sku, p.name_cn_en, p.jizhanming, p.price,
                   p.ip_series, p.product_type, p.boxes_per_dan,
                   p.stock_unit, p.identity_status,
                   s.upstairs_qty, s.instore_qty,
                   s.last_updated, COALESCE(s.notes, '') AS stock_notes
            FROM stock s
            JOIN products p ON p.id = s.product_id
            WHERE s.store_id = ? {where}
            ORDER BY p.ip_series, p.sku DESC
            {'LIMIT ? OFFSET ?' if paginated else ''}
        ''', [store_id] + params + ([page_size, offset] if paginated else []))

    items = [dict(r) for r in cur.fetchall()]
    con.close()
    if paginated:
        return jsonify({'items': items, 'total': total, 'page': page, 'page_size': page_size})
    return jsonify(items)


@bp.route('/api/stock/<int:product_id>')
@login_required
def get_stock(product_id):
    con = get_db()
    store_id, store_code, err = _require_store_param(con)
    if err:
        con.close()
        return err
    cur = con.cursor()
    cur.execute('''
        SELECT p.id, p.sku, p.name_cn_en, p.jizhanming, p.price,
               p.ip_series, p.product_type, p.boxes_per_dan,
               p.stock_unit, p.identity_status,
               COALESCE(s.upstairs_qty, 0) AS upstairs_qty,
               COALESCE(s.instore_qty,  0) AS instore_qty,
               COALESCE(s.last_updated, '') AS last_updated,
               COALESCE(s.notes, '') AS stock_notes
        FROM products p
        LEFT JOIN stock s ON s.product_id = p.id AND s.store_id = ?
        WHERE p.id = ?
    ''', (store_id, product_id))
    row = cur.fetchone()
    con.close()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(dict(row))


@bp.route('/api/stock/<int:product_id>', methods=['PATCH'])
@role_required('staff')
def patch_stock(product_id):
    data = request.get_json() or {}
    notes = data.get('notes', '')
    con = get_db()
    store_id, store_code, err = _require_store_body(con, data)
    if err:
        con.close()
        return err
    con.execute('UPDATE stock SET notes=? WHERE product_id=? AND store_id=?',
                (notes, product_id, store_id))
    con.commit()
    con.close()
    return jsonify({'ok': True})


@bp.route('/api/stock/ru_dian', methods=['POST'])
@role_required('staff')
def ru_dian():
    """Move stock from upstairs (2F) to in-store (1F)."""
    data = request.get_json()
    if not isinstance(data, dict):
        return jsonify({'error': 'Expected an object', 'code': 'invalid_input'}), 400
    try:
        pid = read_int(data['product_id'], 'product_id', minimum=1)
        qty = read_int(data['qty'], 'qty', minimum=1)
        d = read_date(data.get('date', str(date.today())))
    except KeyError as exc:
        return jsonify({'error': f'{exc.args[0]} is required',
                        'code': 'invalid_input', 'field': exc.args[0]}), 400
    except ValueError as exc:
        return jsonify(invalid_input(exc)), 400
    notes = data.get('notes', '')

    con = get_db()
    store_id, store_code, err = _require_store_body(con, data)
    if err:
        con.close()
        return err
    if _is_authoritative(con):
        try:
            return jsonify(_authoritative_stock_adapter(
                con, data, store_id, 'ru_dian', pid, qty, d, notes
            ))
        except PermissionError:
            return jsonify({'error': 'Inventory access denied',
                            'code': 'inventory_forbidden'}), 403
        except InventoryError as exc:
            return _inventory_error(exc)
        finally:
            con.close()
    con.execute('BEGIN IMMEDIATE')
    cur = con.cursor()
    if _missing_product(cur, pid):
        con.rollback()
        con.close()
        return jsonify(error='Product not found', code='not_found'), 404
    _ensure_stock_row(cur, pid, store_id)

    cur.execute('SELECT upstairs_qty, instore_qty FROM stock WHERE product_id = ? AND store_id = ?',
                (pid, store_id))
    row = cur.fetchone()
    upstairs = row['upstairs_qty'] if row else 0
    instore = row['instore_qty'] if row else 0
    if qty > upstairs:
        con.rollback()
        con.close()
        return jsonify({'error': f'楼上库存不足（现有 {upstairs}）',
                        'code': 'insufficient_stock'}), 409
    try:
        read_int(instore + qty, 'instore_qty')
    except ValueError as exc:
        con.rollback()
        con.close()
        return jsonify(invalid_input(exc)), 400

    cur.execute('''
        UPDATE stock
        SET upstairs_qty = upstairs_qty - ?,
            instore_qty  = instore_qty  + ?,
            last_updated = ?
        WHERE product_id = ? AND store_id = ?
          AND upstairs_qty >= ? AND instore_qty <= ?
    ''', (qty, qty, d, pid, store_id, qty, SQLITE_INTEGER_MAX - qty))
    if cur.rowcount != 1:
        con.rollback()
        con.close()
        return jsonify({'error': 'Stock changed before the operation completed',
                        'code': 'insufficient_stock'}), 409
    cur.execute('''
        INSERT INTO stock_transactions (product_id, txn_type, qty, location, date, notes, store_id)
        VALUES (?, 'ru_dian', ?, 'upstairs->instore', ?, ?, ?)
    ''', (pid, qty, d, notes, store_id))
    con.commit()

    cur.execute('SELECT upstairs_qty, instore_qty FROM stock WHERE product_id = ? AND store_id = ?',
                (pid, store_id))
    _row = cur.fetchone()
    s = dict(_row) if _row else {'upstairs_qty': 0, 'instore_qty': 0}
    con.close()
    return jsonify({'ok': True, 'upstairs_qty': s['upstairs_qty'], 'instore_qty': s['instore_qty']})


@bp.route('/api/stock/restock_upstairs', methods=['POST'])
@role_required('staff')
def restock_upstairs():
    """Receive new stock into upstairs (2F) storage."""
    data  = request.get_json()
    if not isinstance(data, dict):
        return jsonify({'error': 'Expected an object', 'code': 'invalid_input'}), 400
    try:
        pid = read_int(data['product_id'], 'product_id', minimum=1)
        qty = read_int(data['qty'], 'qty', minimum=1)
        d = read_date(data.get('date', str(date.today())))
    except KeyError as exc:
        return jsonify({'error': f'{exc.args[0]} is required',
                        'code': 'invalid_input', 'field': exc.args[0]}), 400
    except ValueError as exc:
        return jsonify(invalid_input(exc)), 400
    notes = data.get('notes', '')

    con = get_db()
    store_id, store_code, err = _require_store_body(con, data)
    if err:
        con.close()
        return err
    if _is_authoritative(con):
        try:
            return jsonify(_authoritative_stock_adapter(
                con, data, store_id, 'restock_upstairs', pid, qty, d, notes
            ))
        except PermissionError:
            return jsonify({'error': 'Inventory access denied',
                            'code': 'inventory_forbidden'}), 403
        except InventoryError as exc:
            return _inventory_error(exc)
        finally:
            con.close()
    con.execute('BEGIN IMMEDIATE')
    cur = con.cursor()
    if _missing_product(cur, pid):
        con.rollback()
        con.close()
        return jsonify(error='Product not found', code='not_found'), 404
    _ensure_stock_row(cur, pid, store_id)

    row = cur.execute(
        'SELECT upstairs_qty FROM stock WHERE product_id=? AND store_id=?',
        (pid, store_id),
    ).fetchone()
    try:
        read_int(row['upstairs_qty'] + qty, 'upstairs_qty')
    except ValueError as exc:
        con.rollback()
        con.close()
        return jsonify(invalid_input(exc)), 400

    cur.execute('''
        UPDATE stock
        SET upstairs_qty = upstairs_qty + ?,
            last_updated = ?
        WHERE product_id = ? AND store_id = ? AND upstairs_qty <= ?
    ''', (qty, d, pid, store_id, SQLITE_INTEGER_MAX - qty))
    if cur.rowcount != 1:
        con.rollback()
        con.close()
        return jsonify({'error': 'upstairs_qty exceeds the supported range',
                        'code': 'invalid_input', 'field': 'upstairs_qty'}), 400
    cur.execute('''
        INSERT INTO stock_transactions (product_id, txn_type, qty, location, date, notes, store_id)
        VALUES (?, 'restock_upstairs', ?, 'upstairs', ?, ?, ?)
    ''', (pid, qty, d, notes, store_id))
    con.commit()

    cur.execute('SELECT upstairs_qty, instore_qty FROM stock WHERE product_id = ? AND store_id = ?',
                (pid, store_id))
    _row = cur.fetchone()
    s = dict(_row) if _row else {'upstairs_qty': 0, 'instore_qty': 0}
    con.close()
    return jsonify({'ok': True, 'upstairs_qty': s['upstairs_qty'], 'instore_qty': s['instore_qty']})


@bp.route('/api/stock/adjust', methods=['POST'])
@role_required('staff')
def adjust_stock():
    """Manual adjustment (correction) of upstairs or instore count."""
    data     = request.get_json()
    if not isinstance(data, dict):
        return jsonify({'error': 'Expected an object', 'code': 'invalid_input'}), 400
    try:
        pid = read_int(data['product_id'], 'product_id', minimum=1)
        new_qty = read_int(data['new_qty'], 'new_qty')
        d = read_date(data.get('date', str(date.today())))
    except KeyError as exc:
        return jsonify({'error': f'{exc.args[0]} is required',
                        'code': 'invalid_input', 'field': exc.args[0]}), 400
    except ValueError as exc:
        return jsonify(invalid_input(exc)), 400
    location = data.get('location', 'upstairs')
    notes    = data.get('notes', '')

    if location not in ('upstairs', 'instore'):
        return jsonify({'error': 'location must be upstairs or instore'}), 400

    con = get_db()
    store_id, store_code, err = _require_store_body(con, data)
    if err:
        con.close()
        return err
    if _is_authoritative(con):
        try:
            return jsonify(_authoritative_stock_adapter(
                con, data, store_id, 'adjust', pid, 0, d, notes,
                location=location, new_quantity=new_qty,
            ))
        except PermissionError:
            return jsonify({'error': 'Inventory access denied',
                            'code': 'inventory_forbidden'}), 403
        except InventoryError as exc:
            return _inventory_error(exc)
        finally:
            con.close()
    con.execute('BEGIN IMMEDIATE')
    cur = con.cursor()
    if _missing_product(cur, pid):
        con.rollback()
        con.close()
        return jsonify(error='Product not found', code='not_found'), 404
    _ensure_stock_row(cur, pid, store_id)

    cur.execute('SELECT upstairs_qty, instore_qty FROM stock WHERE product_id = ? AND store_id = ?',
                (pid, store_id))
    _row = cur.fetchone()
    if _row is None:
        con.rollback()
        con.close()
        return jsonify({'error': 'Stock row not found'}), 404
    s = dict(_row)
    old_val = s[f'{location}_qty']
    delta   = new_qty - old_val

    if location == 'upstairs':
        cur.execute('UPDATE stock SET upstairs_qty = ?, last_updated = ? WHERE product_id = ? AND store_id = ?',
                    (new_qty, d, pid, store_id))
    else:
        cur.execute('UPDATE stock SET instore_qty = ?, last_updated = ? WHERE product_id = ? AND store_id = ?',
                    (new_qty, d, pid, store_id))

    cur.execute('''
        INSERT INTO stock_transactions (product_id, txn_type, qty, location, date, notes, store_id)
        VALUES (?, 'adjust', ?, ?, ?, ?, ?)
    ''', (pid, delta, location, d, notes or f'手动调整: {old_val}→{new_qty}', store_id))

    con.commit()
    cur.execute('SELECT upstairs_qty, instore_qty FROM stock WHERE product_id = ? AND store_id = ?',
                (pid, store_id))
    _row2 = cur.fetchone()
    s2 = dict(_row2) if _row2 else {'upstairs_qty': 0, 'instore_qty': 0}
    con.close()
    return jsonify({'ok': True, 'upstairs_qty': s2['upstairs_qty'], 'instore_qty': s2['instore_qty']})


@bp.route('/api/stock/transactions')
@login_required
def get_transactions():
    pid   = request.args.get('product_id')
    d     = request.args.get('date')
    try:
        limit = int(request.args.get('limit', 50))
    except (ValueError, TypeError):
        return jsonify({'error': 'limit must be an integer'}), 400

    pid_int = None
    if pid:
        try:
            pid_int = int(pid)
        except (ValueError, TypeError):
            return jsonify({'error': 'product_id must be an integer'}), 400

    con = get_db()
    store_id, store_code, err = _require_store_param(con)
    if err:
        con.close()
        return err
    cur = con.cursor()

    if store_code == 'ALL':
        conditions = []
        params = []
    else:
        conditions = ['t.store_id = ?']
        params = [store_id]
    if pid_int is not None:
        conditions.append('t.product_id = ?')
        params.append(pid_int)
    if d:
        conditions.append('t.date = ?')
        params.append(d)

    where = ('WHERE ' + ' AND '.join(conditions)) if conditions else ''
    params.append(limit)

    cur.execute(f'''
        SELECT t.id, t.product_id, t.txn_type, t.qty, t.location,
               t.date, t.notes, t.created_at, t.store_id,
               COALESCE(st.code, '') AS store_code,
               p.jizhanming, p.sku, p.name_cn_en, p.boxes_per_dan, p.product_type
        FROM stock_transactions t
        JOIN products p ON p.id = t.product_id
        LEFT JOIN stores st ON st.id = t.store_id
        {where}
        ORDER BY t.id DESC
        LIMIT ?
    ''', params)

    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return jsonify(rows)


@bp.route('/api/stock/summary')
@login_required
def stock_summary():
    con = get_db()
    store_id, store_code, err = _require_store_param(con)
    if err:
        con.close()
        return err
    cur = con.cursor()

    if _is_authoritative(con):
        if store_code == 'ALL':
            store_ids = [row['store_id'] for row in con.execute(
                'SELECT store_id FROM inventory_access WHERE auth0_sub=?',
                (request.jwt_payload.get('sub'),),
            )]
            if not store_ids:
                con.close()
                return jsonify({'error': 'Inventory access denied',
                                'code': 'inventory_forbidden'}), 403
        else:
            store_ids = [store_id]
            try:
                require_inventory_access(con, request.jwt_payload, store_ids, 'viewer')
            except PermissionError:
                con.close()
                return jsonify({'error': 'Inventory access denied',
                                'code': 'inventory_forbidden'}), 403
        placeholders = ','.join('?' * len(store_ids))
        unit_rows = [dict(row) for row in con.execute(f'''
            SELECT p.stock_unit AS unit,
                   SUM(CASE WHEN l.code='floor' THEN b.quantity ELSE 0 END) AS floor_qty,
                   SUM(CASE WHEN l.code IN ('upstairs','warehouse') THEN b.quantity ELSE 0 END) AS back_qty,
                   SUM(b.quantity) AS total_qty
            FROM inventory_balances b
            JOIN products p ON p.id=b.product_id
            JOIN inventory_locations l ON l.id=b.location_id
            WHERE l.store_id IN ({placeholders}) AND b.disposition='saleable'
            GROUP BY p.stock_unit ORDER BY p.stock_unit
        ''', store_ids)]
        product_totals = [row['quantity'] for row in con.execute(f'''
            SELECT SUM(b.quantity) AS quantity
            FROM inventory_balances b
            JOIN inventory_locations l ON l.id=b.location_id
            WHERE l.store_id IN ({placeholders}) AND b.disposition='saleable'
            GROUP BY b.product_id
        ''', store_ids)]
        incomplete = con.execute(f'''
            SELECT 1 FROM inventory_balances b
            JOIN products p ON p.id=b.product_id
            JOIN inventory_locations l ON l.id=b.location_id
            WHERE l.store_id IN ({placeholders})
              AND (p.identity_status!='verified' OR p.stock_unit IS NULL)
            LIMIT 1
        ''', store_ids).fetchone() is not None
        result = {
            'mode': 'authoritative', 'complete': not incomplete,
            'products_tracked': len(product_totals), 'unit_totals': unit_rows,
            'total_upstairs_qty': None, 'total_instore_qty': None,
            'total_stock_value': None,
            'low_stock_count': sum(0 < qty <= 3 for qty in product_totals),
            'out_of_stock_count': sum(qty == 0 for qty in product_totals),
        }
        con.close()
        return jsonify(result)

    if store_code == 'ALL':
        cur.execute('''
            SELECT
                COUNT(DISTINCT product_id) AS products_tracked,
                SUM(upstairs_qty) AS total_upstairs_qty,
                SUM(instore_qty)  AS total_instore_qty
            FROM stock
        ''')
        row = dict(cur.fetchone())
        cur.execute('''
            SELECT COUNT(DISTINCT product_id) FROM stock
            WHERE upstairs_qty = 0 AND instore_qty > 0
        ''')
        row['low_stock_count'] = cur.fetchone()[0]
        cur.execute('''
            SELECT COUNT(DISTINCT product_id) FROM stock
            WHERE upstairs_qty = 0 AND instore_qty = 0
        ''')
        row['out_of_stock_count'] = cur.fetchone()[0]
        cur.execute('''
            SELECT COALESCE(SUM(p.price * (s.upstairs_qty + s.instore_qty)), 0)
            FROM stock s
            JOIN products p ON p.id = s.product_id
            WHERE p.price IS NOT NULL
        ''')
        row['total_stock_value'] = cur.fetchone()[0]
    else:
        cur.execute('''
            SELECT
                COUNT(*) AS products_tracked,
                SUM(upstairs_qty) AS total_upstairs_qty,
                SUM(instore_qty)  AS total_instore_qty
            FROM stock
            WHERE store_id = ?
        ''', (store_id,))
        row = dict(cur.fetchone())
        cur.execute('''
            SELECT COUNT(*) FROM stock
            WHERE store_id = ? AND upstairs_qty = 0 AND instore_qty > 0
        ''', (store_id,))
        row['low_stock_count'] = cur.fetchone()[0]
        cur.execute('SELECT COUNT(*) FROM stock WHERE store_id = ? AND upstairs_qty = 0 AND instore_qty = 0',
                    (store_id,))
        row['out_of_stock_count'] = cur.fetchone()[0]
        cur.execute('''
            SELECT COALESCE(SUM(p.price * (s.upstairs_qty + s.instore_qty)), 0)
            FROM stock s
            JOIN products p ON p.id = s.product_id
            WHERE s.store_id = ? AND p.price IS NOT NULL
        ''', (store_id,))
        row['total_stock_value'] = cur.fetchone()[0]

    con.close()
    return jsonify(row)


@bp.route('/api/stock/export')
@role_required('manager')
def export_stock():
    series = request.args.get('series', '').strip()
    q      = request.args.get('q', '').strip().lower()

    con = get_db()
    store_id, store_code, err = _require_store_param(con)
    if err:
        con.close()
        return err
    cur = con.cursor()

    filters = []
    params  = [store_id]
    if series:
        filters.append("p.ip_series = ?")
        params.append(series)
    if q:
        for token in q.split():
            filters.append("p.search_blob LIKE ?")
            params.append(f'%{token}%')

    where = ('AND ' + ' AND '.join(filters)) if filters else ''

    cur.execute(f'''
        SELECT p.sku, p.jizhanming, p.name_cn_en, p.ip_series, p.product_type,
               p.price, p.boxes_per_dan,
               COALESCE(s.upstairs_qty, 0) AS upstairs_qty,
               COALESCE(s.instore_qty,  0) AS instore_qty,
               COALESCE(s.last_updated, '') AS last_updated,
               COALESCE(s.notes, '') AS stock_notes
        FROM stock s
        JOIN products p ON p.id = s.product_id
        WHERE s.store_id = ? {where}
        ORDER BY p.ip_series, p.sku DESC
    ''', params)
    rows = cur.fetchall()
    con.close()

    header = 'SKU,记账名,产品名称,系列,类型,单价,每端盒数,楼上(盒/件),店内(盒/件),更新时间,备注'
    lines  = ['﻿' + header]
    for r in rows:
        lines.append(','.join(esc_csv(v) for v in [
            r['sku'], r['jizhanming'], r['name_cn_en'], r['ip_series'],
            r['product_type'], r['price'], r['boxes_per_dan'],
            r['upstairs_qty'], r['instore_qty'], r['last_updated'], r['stock_notes']
        ]))

    fname = f'stock_{store_code}_{date.today()}.csv'
    return Response(
        '\n'.join(lines),
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="{fname}"'}
    )


@bp.route('/api/stock/rows', methods=['DELETE'])
@role_required('manager')
def delete_stock_rows():
    body = request.get_json()
    if isinstance(body, dict):
        store_code_raw = (body.get('store_code') or '').strip().upper()
        pids = body.get('product_ids', [])
    else:
        return jsonify({'error': 'Expected {"store_code": ..., "product_ids": [...]}'}), 400
    if not store_code_raw:
        return jsonify({'error': 'store_code is required'}), 400
    if store_code_raw == 'ALL':
        return jsonify({'error': 'Cannot write with store_code ALL. Select a specific store.'}), 400
    if not isinstance(pids, list) or not pids:
        return jsonify({'error': 'product_ids must be a non-empty list'}), 400
    con = get_db()
    resolved = _resolve_store(con, store_code_raw)
    if resolved is None:
        con.close()
        return jsonify({'error': 'Invalid store code'}), 400
    store_id, _ = resolved
    cur = con.cursor()

    ph  = ','.join('?' * len(pids))
    inventory_retained = cur.execute(f'''
        SELECT 1 FROM inventory_movements m
        JOIN inventory_locations l ON l.id=m.location_id
        WHERE l.store_id=? AND m.product_id IN ({ph}) LIMIT 1
    ''', [store_id] + pids).fetchone()
    if inventory_retained:
        con.close()
        return jsonify({
            'error': 'Stock rows with authoritative history are retained',
            'code': 'stock_row_retained',
        }), 409
    retained = cur.execute(f'''
        SELECT 1 FROM stock s
        WHERE s.store_id = ? AND s.product_id IN ({ph})
          AND (
            s.upstairs_qty != 0 OR s.instore_qty != 0 OR s.claw_qty != 0
            OR EXISTS (
              SELECT 1 FROM stock_transactions st
              WHERE st.store_id=s.store_id AND st.product_id=s.product_id
            )
          )
        LIMIT 1
    ''', [store_id] + pids).fetchone()
    if retained:
        con.close()
        return jsonify({
            'error': 'Stock rows with quantities or history are retained',
            'code': 'stock_row_retained',
        }), 409
    cur.execute(f'DELETE FROM stock WHERE store_id = ? AND product_id IN ({ph})',
                [store_id] + pids)
    deleted = cur.rowcount
    con.commit()
    con.close()
    return jsonify({'ok': True, 'deleted': deleted})
