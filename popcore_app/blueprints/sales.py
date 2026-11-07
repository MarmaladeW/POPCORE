"""
blueprints/sales.py — daily sales records, batch import, daily report, export.
"""
import os
import hashlib
import json
from collections import Counter
from decimal import Decimal
import re
import unicodedata
from datetime import date, timedelta
from flask import Blueprint, request, jsonify, Response

from db import get_db, esc_csv, _ensure_stock_row
from auth import login_required, role_required
from checkout_access import business_date
from store_access import can_write_legacy_store, legacy_store_denied
from validation import SQLITE_INTEGER_MAX, invalid_input, read_date, read_int
from blueprints.stores import _resolve_store
from matcher import (match_jzm, load_matching_aliases, normalize as _norm_jzm, _score_pair_jzm,
                     clean_name as _clean_jzm, report_match_key, report_identity_signature,
                     load_report_match_choices, load_report_picks, report_match_query)
from inventory_commands import InventoryError, InventoryConflict, post_inventory, require_inventory_access
from goods_operations import _begin, _hash, _replay, _remember
import report_checks
from report_stock import (LINE_SECTIONS, ReportLineError, check_notice, live_since, open_notices,
                          plan_report_lines, save_report_lines, sync_report_stock)
from inventory_visibility import inventory_hidden
from report_names import all_picks, log_names, place_of, report_days, report_names, save_pick, status as name_status
from blueprints.stock import _inventory_error, _inventory_location, _is_authoritative

bp = Blueprint('sales', __name__)


def _require_store_param(con):
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


@bp.route('/api/sales')
@role_required('manager')
def get_sales():
    d = request.args.get('date', business_date())
    con = get_db()
    store_id, store_code, err = _require_store_param(con)
    if err:
        con.close()
        return err
    cur = con.cursor()
    if store_code == 'ALL':
        cur.execute('''
            SELECT ds.id, ds.product_id, ds.date, ds.qty_sold, ds.qty_pos, ds.qty_cash,
                   ds.qty_claw, ds.qty_display, ds.qty_employee, ds.raw_name, ds.notes,
                   ds.store,
                   p.sku, p.name_cn_en, p.jizhanming,
                   ds.unit_price AS price, ds.unit_price, p.ip_series
            FROM daily_sales ds
            JOIN products p ON p.id = ds.product_id
            WHERE ds.date = ?
            ORDER BY p.ip_series, p.jizhanming
            LIMIT 500
        ''', (d,))
    else:
        cur.execute('''
            SELECT ds.id, ds.product_id, ds.date, ds.qty_sold, ds.qty_pos, ds.qty_cash,
                   ds.qty_claw, ds.qty_display, ds.qty_employee, ds.raw_name, ds.notes,
                   ds.store,
                   p.sku, p.name_cn_en, p.jizhanming,
                   ds.unit_price AS price, ds.unit_price, p.ip_series
            FROM daily_sales ds
            JOIN products p ON p.id = ds.product_id
            WHERE ds.date = ? AND ds.store = ?
            ORDER BY p.ip_series, p.jizhanming
            LIMIT 500
        ''', (d, store_code))
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return jsonify(rows)


# Historical summaries are corrected separately from sale and stock documents.
def _history_product(product):
    fields = ('id', 'sku', 'name_cn_en', 'jizhanming', 'series_id', 'series_name',
              'stock_form', 'stock_unit', 'design_name', 'identity_status',
              'product_type', 'ip_series', 'edition_size', 'hidden', 'boxes_per_dan')
    return {**{field: product.get(field) for field in fields},
            'target_identity_token': report_identity_signature(product)}


def _history_catalog(con):
    return {row['id']: dict(row) for row in con.execute(
        'SELECT p.*, s.name AS series_name FROM products p LEFT JOIN product_series s ON s.id=p.series_id')}


def _history_row_token(row, product):
    return _hash({'row': row, 'identity': report_identity_signature(product)})


def _history_stores(con, code):
    if not isinstance(code, str) or not code.strip():
        raise ValueError('store_code is required')
    code = code.strip().upper()
    stores = [dict(row) for row in con.execute('''
        SELECT s.id,s.code FROM stores s JOIN inventory_access a ON a.store_id=s.id
        WHERE a.auth0_sub=? AND s.is_active=1
          AND EXISTS (SELECT 1 FROM inventory_locations l WHERE l.store_id=s.id AND l.is_active=1)
    ''', (request.jwt_payload['sub'],)) if code == 'ALL' or row['code'] == code]
    require_inventory_access(con, request.jwt_payload, [row['id'] for row in stores], 'manager')
    return stores


def _history_stock_linked(con, day, store_id):
    return bool(con.execute(f'''SELECT 1 FROM stock_transactions WHERE date=? AND store_id=?
        AND txn_type IN ({','.join('?' * len(_REPORT_TXN_TYPES))}) LIMIT 1''',
        (day, store_id, *_REPORT_TXN_TYPES)).fetchone())


@bp.get('/api/sales/history-matches')
@role_required('manager')
def history_matches():
    con = get_db()
    try:
        stores = _history_stores(con, request.args.get('store_code'))
        today = date.fromisoformat(business_date())
        start = read_date(request.args.get('date_from', str(today - timedelta(days=30))), 'date_from')
        end = read_date(request.args.get('date_to', str(today)), 'date_to')
        if start > end:
            raise ValueError('date_from must be on or before date_to')
        page = read_int(request.args.get('page', '1'), 'page', minimum=1)
        size = min(read_int(request.args.get('page_size', '25'), 'page_size', minimum=1), 100)
        where = ['ds.date BETWEEN ? AND ?', f"ds.store IN ({','.join('?' * len(stores))})"]
        args = [start, end, *[store['code'] for store in stores]]
        if request.args.get('record_id'):
            where.append('ds.id=?'); args.append(read_int(request.args['record_id'], 'record_id', minimum=1))
        query = request.args.get('q', '').strip()
        if query:
            where.append("(instr(lower(ds.raw_name),lower(?))>0 OR instr(lower(ds.notes),lower(?))>0 OR instr(lower(p.jizhanming),lower(?))>0 OR instr(lower(p.name_cn_en),lower(?))>0 OR instr(lower(p.sku),lower(?))>0)")
            args.extend([query] * 5)
        source = 'FROM daily_sales ds JOIN products p ON p.id=ds.product_id WHERE ' + ' AND '.join(where)
        total = con.execute('SELECT COUNT(*) ' + source, args).fetchone()[0]
        rows = [dict(row) for row in con.execute('SELECT ds.* ' + source + ' ORDER BY ds.date DESC,ds.id DESC LIMIT ? OFFSET ?', [*args, size, read_int((page-1)*size, 'page offset')])]
        catalog = _history_catalog(con)
        target_id = read_int(request.args['target_product_id'], 'target_product_id', minimum=1) if request.args.get('target_product_id') else None
        if target_id is not None and target_id not in catalog:
            raise ValueError('target_product_id does not exist')
        aliases, choices = load_matching_aliases(con), load_report_match_choices(con)
        store_ids = {store['code']: store['id'] for store in stores}
        items = []
        for row in rows:
            product = catalog[row['product_id']]
            raw, note = row['raw_name'] or '', row['notes'] or ''
            key = report_match_key(raw, note)
            choice = choices.get(key)
            hits = [(100, catalog[choice])] if choice in catalog else match_jzm(report_match_query(raw, note), list(catalog.values()), aliases, threshold=60, limit=5) if raw.strip() else []
            if choice not in catalog and (key in choices or report_match_query(raw, note) != raw):
                hits = [(min(score, 99), candidate) for score, candidate in hits]
            if not hits:
                status = 'unmatched'
            elif len(hits)>1 and hits[0][0] == hits[1][0]:
                status = 'ambiguous'
            elif hits[0][0] == 100:
                status = 'matched_current' if hits[0][1]['id'] == row['product_id'] else 'suggested_change'
            else:
                status = 'review'
            candidates = [{**_history_product(candidate), 'score': score,
                           'reason': 'Previously reviewed name and note' if choice == candidate['id'] else 'Exact name or alias' if score == 100 else 'Name similarity; review required'} for score, candidate in hits]
            if target_id is not None:
                candidates = [candidate for candidate in candidates if candidate['id'] != target_id]
                candidates.append({**_history_product(catalog[target_id]), 'score': None, 'reason': 'Selected for review'})
            blocked = _history_stock_linked(con, row['date'], store_ids[row['store']])
            review = con.execute('''SELECT actor_sub,reason,created_at,from_product_id,to_product_id
                FROM daily_sales_match_audits WHERE daily_sales_id=? ORDER BY id DESC LIMIT 1''', (row['id'],)).fetchone()
            items.append({**row, 'price': row['unit_price'], 'row_token': _history_row_token(row, product),
                          'current_product': _history_product(product), 'candidates': candidates,
                          'match_status': status, 'match_label': {'unmatched':'No name candidate', 'ambiguous':'Multiple possible products', 'review':'Review name suggestions', 'matched_current':'Name matches current product', 'suggested_change':'Name suggests another product'}[status],
                          'blocked_code': 'reconciliation_required' if blocked else None,
                          'blocked_reason': 'This report day has stock history and requires reconciliation.' if blocked else None,
                          'last_review': dict(review) if review else None})
        return jsonify({'items': items, 'total_rows': total, 'page': page, 'page_size': size})
    except PermissionError:
        return jsonify({'error': 'Historical sales access denied', 'code': 'inventory_forbidden'}), 403
    except (ValueError, InventoryError) as exc:
        return _inventory_error(exc) if isinstance(exc, InventoryError) else (jsonify(invalid_input(exc)), 400)
    finally:
        con.close()


@bp.post('/api/sales/record/<int:record_id>/remap')
@role_required('manager')
def remap_historical_sale(record_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(invalid_input('body must be an object')), 400
    con = get_db()
    try:
        code = data.get('store_code')
        if not isinstance(code, str) or code.strip().upper() == 'ALL':
            raise ValueError('store_code must be one store')
        product_id = read_int(data.get('product_id'), 'product_id', minimum=1)
        for field in ('row_token', 'target_identity_token', 'reason'):
            if not isinstance(data.get(field), str) or not data[field].strip():
                raise ValueError(f'{field} is required')
        intent = {'record_id': record_id, 'store_code': code.strip().upper(), 'product_id': product_id,
                  **{field: data[field].strip() for field in ('row_token', 'target_identity_token', 'reason')}}
        _begin(con)
        store = _history_stores(con, code)[0]
        key, digest, prior = _replay(con, request.headers.get('Idempotency-Key'), 'sales_history_remap', request.jwt_payload, intent)
        if prior:
            con.commit(); return jsonify(prior)
        row = con.execute('SELECT * FROM daily_sales WHERE id=? AND store=?', (record_id, store['code'])).fetchone()
        if row is None:
            raise InventoryConflict('Historical row is no longer available. Refresh before reviewing.', 'stale_row')
        row = dict(row)
        catalog = _history_catalog(con)
        current, target = catalog[row['product_id']], catalog.get(product_id)
        if _history_row_token(row, current) != intent['row_token']:
            raise InventoryConflict('Historical row changed. Refresh before reviewing.', 'stale_row')
        if target is None or report_identity_signature(target) != intent['target_identity_token']:
            raise InventoryConflict('Target product identity changed. Choose it again.', 'stale_target')
        if product_id == row['product_id']:
            raise InventoryConflict('This row already uses the selected product.', 'unchanged_product')
        if _history_stock_linked(con, row['date'], store['id']):
            raise InventoryConflict('This report has stock history and requires reconciliation', 'reconciliation_required')
        if con.execute('SELECT 1 FROM daily_sales WHERE product_id=? AND date=? AND store=?', (product_id, row['date'], row['store'])).fetchone():
            raise InventoryConflict('That product already has a row for this day. Review both rows without merging them.', 'target_row_exists')
        if current.get('stock_unit') and target.get('stock_unit') and current['stock_unit'] != target['stock_unit']:
            raise InventoryConflict('Product units differ. Historical quantities cannot be converted by a name correction.', 'unit_mismatch')
        after = {**row, 'product_id': product_id}
        con.execute('UPDATE daily_sales SET product_id=? WHERE id=?', (product_id, record_id))
        audit_id = con.execute('''INSERT INTO daily_sales_match_audits
            (daily_sales_id,store_id,from_product_id,to_product_id,actor_sub,reason,before_data,after_data)
            VALUES (?,?,?,?,?,?,?,?)''', (record_id, store['id'], row['product_id'], product_id, request.jwt_payload['sub'], intent['reason'],
            json.dumps({'row': row, 'product': _history_product(current)}, ensure_ascii=False),
            json.dumps({'row': after, 'product': _history_product(target)}, ensure_ascii=False))).lastrowid
        result = {'id': record_id, 'audit_id': audit_id, 'row_token': _history_row_token(after, target), 'current_product': _history_product(target)}
        _remember(con, key, 'sales_history_remap', audit_id, request.jwt_payload, digest, result)
        con.commit()
        return jsonify(result)
    except PermissionError:
        con.rollback()
        return jsonify({'error': 'Historical sales access denied', 'code': 'inventory_forbidden'}), 403
    except (ValueError, InventoryError) as exc:
        con.rollback()
        return _inventory_error(exc) if isinstance(exc, InventoryError) else (jsonify(invalid_input(exc)), 400)
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


@bp.route('/api/sales/upsert', methods=['POST'])
@role_required('manager')
def upsert_sale():
    data  = request.get_json()
    if not isinstance(data, dict) or 'product_id' not in data:
        return jsonify({'error': 'product_id is required'}), 400
    try:
        pid      = read_int(data['product_id'], 'product_id', minimum=1)
        qty_pos  = read_int(data.get('qty_pos', 0), 'qty_pos')
        qty_cash = read_int(data.get('qty_cash', 0), 'qty_cash')
        if 'qty_pos' not in data and 'qty_cash' not in data:
            qty_cash = read_int(data.get('qty_sold', 0), 'qty_sold')
        read_int(qty_pos + qty_cash, 'qty_sold')
        d = read_date(data.get('date', business_date()))
    except ValueError as exc:
        return jsonify(invalid_input(exc)), 400
    notes    = data.get('notes', '')
    qty_sold = qty_pos + qty_cash

    con = get_db()
    store_id, store_code, err = _require_store_body(con, data)
    if err:
        con.close()
        return err
    denied = legacy_store_denied(con, request.jwt_payload, store_id)
    if denied:
        con.close()
        return denied
    cur = con.cursor()
    if cur.execute('SELECT 1 FROM products WHERE id = ?', (pid,)).fetchone() is None:
        con.close()
        return jsonify({'error': 'Product not found', 'code': 'not_found',
                        'field': 'product_id'}), 404
    try:
        con.execute('BEGIN IMMEDIATE')
        cur.execute('''
            INSERT INTO daily_sales (product_id, date, store, qty_pos, qty_cash, qty_sold, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(product_id, date, store) DO UPDATE SET
                qty_pos  = excluded.qty_pos,
                qty_cash = excluded.qty_cash,
                qty_sold = excluded.qty_sold,
                notes    = excluded.notes
        ''', (pid, d, store_code, qty_pos, qty_cash, qty_sold, notes))
        stock = sync_report_stock(con, request.jwt_payload, store_id, store_code, d)
        con.commit()
    except InventoryError as exc:
        con.rollback()
        return _inventory_error(exc)
    finally:
        con.close()
    return jsonify({'ok': True, 'stock': stock})


@bp.route('/api/sales/add_product', methods=['POST'])
@role_required('staff')
def add_product_to_sales():
    data = request.get_json()
    if not data or 'product_id' not in data:
        return jsonify({'error': 'product_id is required'}), 400
    try:
        pid = int(data['product_id'])
    except (ValueError, TypeError):
        return jsonify({'error': 'product_id must be an integer'}), 400
    d = data.get('date', business_date())

    con = get_db()
    store_id, store_code, err = _require_store_body(con, data)
    if err:
        con.close()
        return err
    denied = legacy_store_denied(con, request.jwt_payload, store_id)
    if denied:
        con.close()
        return denied
    cur = con.cursor()
    cur.execute('''
        INSERT OR IGNORE INTO daily_sales (product_id, date, store, qty_sold)
        VALUES (?, ?, ?, 0)
    ''', (pid, d, store_code))
    con.commit()
    con.close()
    return jsonify({'ok': True})


@bp.route('/api/sales/summary')
@role_required('manager')
def sales_summary():
    con = get_db()
    store_id, store_code, err = _require_store_param(con)
    if err:
        con.close()
        return err
    cur = con.cursor()
    if store_code == 'ALL':
        cur.execute('''
            SELECT date,
                   COUNT(*)      AS product_count,
                   SUM(qty_sold) AS total_sold,
                   SUM(qty_pos)  AS total_pos,
                   SUM(qty_cash) AS total_cash
            FROM daily_sales
            GROUP BY date
            ORDER BY date DESC
            LIMIT 60
        ''')
    else:
        cur.execute('''
            SELECT date,
                   COUNT(*)      AS product_count,
                   SUM(qty_sold) AS total_sold,
                   SUM(qty_pos)  AS total_pos,
                   SUM(qty_cash) AS total_cash
            FROM daily_sales
            WHERE store = ?
            GROUP BY date
            ORDER BY date DESC
            LIMIT 60
        ''', (store_code,))
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return jsonify(rows)


@bp.route('/api/sales/record/<int:record_id>', methods=['DELETE'])
@role_required('manager')
def delete_sales_record(record_id):
    con = get_db()
    con.execute('BEGIN IMMEDIATE')
    cur = con.cursor()
    record = cur.execute(
        'SELECT date, store FROM daily_sales WHERE id = ?', (record_id,)
    ).fetchone()
    if record:
        store = cur.execute(
            'SELECT id FROM stores WHERE code = ?', (record['store'],)
        ).fetchone()
        denied = legacy_store_denied(con, request.jwt_payload, store['id'] if store else None)
        if denied:
            con.rollback()
            con.close()
            return denied
        if store:
            prior_stock = cur.execute(f'''
                SELECT 1 FROM stock_transactions
                WHERE date = ? AND store_id = ?
                  AND txn_type IN ({','.join('?' * len(_REPORT_TXN_TYPES))})
                LIMIT 1
            ''', (record['date'], store['id'], *_REPORT_TXN_TYPES)).fetchone()
            if prior_stock:
                con.rollback()
                con.close()
                return jsonify({
                    'error': 'This report has stock history and requires reconciliation',
                    'code': 'reconciliation_required',
                }), 409
    cur.execute('DELETE FROM daily_sales WHERE id = ?', (record_id,))
    stock = None
    if record and store:
        try:
            stock = sync_report_stock(con, request.jwt_payload, store['id'], record['store'], record['date'])
        except InventoryError as exc:
            con.rollback()
            con.close()
            return _inventory_error(exc)
    con.commit()
    con.close()
    return jsonify({'ok': True, 'stock': stock})


@bp.route('/api/stock/batch_operation', methods=['POST'])
@role_required('staff')
def batch_stock_operation():
    """
    Batch stock operation (paste import).
    Body: { store_code: '...', operation: 'ru_dian'|'restock_upstairs'|'out_dian'|'ru_dian_claw',
            date: '...', items: [{product_id, qty, notes}] }
    """
    data      = request.get_json()
    if not isinstance(data, dict):
        return jsonify({'error': 'Expected an object', 'code': 'invalid_input'}), 400
    operation = data.get('operation', 'ru_dian')
    items     = data.get('items', [])

    if operation not in ('ru_dian', 'restock_upstairs', 'out_dian', 'ru_dian_claw'):
        return jsonify({'error': 'Invalid operation'}), 400
    if not isinstance(items, list):
        return jsonify({'error': 'items must be a list', 'code': 'invalid_input',
                        'field': 'items'}), 400
    try:
        d = read_date(data.get('date', business_date()))
    except ValueError as exc:
        return jsonify(invalid_input(exc)), 400

    validated_items = []
    for line, item in enumerate(items, 1):
        if not isinstance(item, dict):
            return jsonify({'error': 'Each item must be an object',
                            'code': 'invalid_input', 'line': line}), 400
        try:
            pid = read_int(item['product_id'], 'product_id', minimum=1)
            qty = read_int(item.get('qty'), 'qty', minimum=1)
        except KeyError:
            return jsonify({'error': 'product_id is required',
                            'code': 'invalid_input', 'field': 'product_id',
                            'line': line}), 400
        except ValueError as exc:
            return jsonify(invalid_input(exc, line=line)), 400
        validated_items.append((pid, qty, item.get('notes', '')))

    con = get_db()
    store_id, store_code, err = _require_store_body(con, data)
    if not err:
        err = inventory_hidden(con, request.jwt_payload, (store_id,), store_code)
    if err:
        con.close()
        return err
    cur = con.cursor()
    results = []

    product_ids = {row[0] for row in validated_items}
    if product_ids:
        placeholders = ','.join('?' * len(product_ids))
        found = {
            row[0] for row in cur.execute(
                f'SELECT id FROM products WHERE id IN ({placeholders})',
                tuple(product_ids),
            )
        }
        missing = product_ids - found
        if missing:
            con.close()
            return jsonify({'error': 'Product not found', 'code': 'not_found',
                            'field': 'product_id'}), 404

    if _is_authoritative(con, store_id):
        request_key = request.headers.get('Idempotency-Key', '').strip()
        if not request_key:
            con.close()
            return jsonify({'error': 'Idempotency-Key is required',
                            'code': 'idempotency_key_required'}), 400
        if operation == 'ru_dian_claw':
            con.close()
            return jsonify({'error': 'Claw stock meaning requires catalog review',
                            'code': 'migration_required'}), 409
        floor = _inventory_location(con, store_id, 'floor')
        back = _inventory_location(con, store_id, 'upstairs', 'warehouse')
        if floor is None or back is None:
            con.close()
            return jsonify({'error': 'Reviewed inventory locations are missing',
                            'code': 'inventory_store_missing'}), 409
        totals = {}
        for pid, qty, _notes in validated_items:
            totals[pid] = totals.get(pid, 0) + qty
        prior_versions = {row['product_id']: row for row in con.execute(
            """SELECT l.product_id, l.from_version, l.to_version
               FROM inventory_documents d
               JOIN inventory_document_lines l ON l.document_id=d.id
               WHERE d.request_key=?""", (request_key,)
        )}
        lines = []
        for pid, qty in totals.items():
            product = con.execute(
                'SELECT stock_unit FROM products WHERE id=?', (pid,)
            ).fetchone()
            unit = product['stock_unit'] if product else None
            def balance(location_id):
                return con.execute(
                    """SELECT version FROM inventory_balances
                       WHERE product_id=? AND location_id=?
                         AND disposition='saleable'""", (pid, location_id)
                ).fetchone()
            if operation == 'restock_upstairs':
                target = balance(back)
                prior = prior_versions.get(pid)
                line = {'product_id': pid, 'quantity': qty, 'unit': unit,
                        'to_location_id': back, 'to_disposition': 'saleable',
                        'expected_versions': {'to': (prior['to_version'] if prior
                                                     else target['version'] if target else 0)}}
                kind = 'receipt'
            elif operation == 'ru_dian':
                source, target = balance(back), balance(floor)
                prior = prior_versions.get(pid)
                line = {'product_id': pid, 'quantity': qty, 'unit': unit,
                        'from_location_id': back, 'from_disposition': 'saleable',
                        'to_location_id': floor, 'to_disposition': 'saleable',
                        'expected_versions': {
                            'from': (prior['from_version'] if prior
                                     else source['version'] if source else 0),
                            'to': (prior['to_version'] if prior
                                   else target['version'] if target else 0)}}
                kind = 'move'
            else:
                source = balance(floor)
                prior = prior_versions.get(pid)
                line = {'product_id': pid, 'quantity': qty, 'unit': unit,
                        'from_location_id': floor, 'from_disposition': 'saleable',
                        'expected_versions': {'from': (prior['from_version'] if prior
                                                       else source['version'] if source else 0)}}
                kind = 'consume'
            lines.append(line)
        try:
            result = post_inventory(
                con, {'kind': kind, 'business_date': d,
                      'reason': f'Batch {operation}', 'lines': lines},
                actor=request.jwt_payload, request_key=request_key,
            )
            con.close()
            return jsonify({**result, 'ok': True,
                            'results': [{'pid': pid, 'ok': True} for pid in totals]})
        except PermissionError:
            con.close()
            return jsonify({'error': 'Inventory access denied',
                            'code': 'inventory_forbidden'}), 403
        except InventoryError as exc:
            con.close()
            return _inventory_error(exc)

    denied = legacy_store_denied(con, request.jwt_payload, store_id)
    if denied:
        con.close()
        return denied
    con.execute('BEGIN IMMEDIATE')
    required = {}
    if operation in {'ru_dian', 'ru_dian_claw', 'out_dian'}:
        source_column = (
            'instore_qty' if operation == 'out_dian' else 'upstairs_qty'
        )
        for pid, qty, _notes in validated_items:
            try:
                required[pid] = read_int(
                    required.get(pid, 0) + qty, 'total_quantity', minimum=1
                )
            except ValueError as exc:
                con.rollback()
                con.close()
                return jsonify(invalid_input(exc)), 400
        for pid, qty in required.items():
            row = cur.execute(
                f'''SELECT {source_column} AS available FROM stock
                    WHERE product_id=? AND store_id=?''',
                (pid, store_id),
            ).fetchone()
            available = row['available'] if row else 0
            if qty > available:
                con.rollback()
                con.close()
                return jsonify({
                    'error': f'Insufficient stock ({available})',
                    'code': 'insufficient_stock',
                    'product_id': pid,
                }), 409

    totals = {}
    for pid, qty, _notes in validated_items:
        try:
            totals[pid] = read_int(
                totals.get(pid, 0) + qty, 'total_quantity', minimum=1
            )
        except ValueError as exc:
            con.rollback()
            con.close()
            return jsonify(invalid_input(exc)), 400
    for pid, qty in totals.items():
        row = cur.execute(
            '''SELECT upstairs_qty, instore_qty, claw_qty FROM stock
               WHERE product_id=? AND store_id=?''',
            (pid, store_id),
        ).fetchone()
        upstairs = row['upstairs_qty'] if row else 0
        instore = row['instore_qty'] if row else 0
        claw = row['claw_qty'] if row else 0
        try:
            if operation == 'restock_upstairs':
                read_int(upstairs + qty, 'upstairs_qty')
            elif operation == 'ru_dian':
                read_int(instore + qty, 'instore_qty')
            elif operation == 'ru_dian_claw':
                read_int(instore + qty, 'instore_qty')
                read_int(claw + qty, 'claw_qty')
        except ValueError as exc:
            con.rollback()
            con.close()
            return jsonify(invalid_input(exc)), 400

    for pid, qty, notes in validated_items:
        _ensure_stock_row(cur, pid, store_id)

        if operation == 'ru_dian':
            cur.execute('''
                UPDATE stock SET upstairs_qty = upstairs_qty - ?,
                                 instore_qty  = instore_qty  + ?,
                                 last_updated = ?
                WHERE product_id = ? AND store_id = ? AND upstairs_qty >= ?
                  AND instore_qty <= ?
            ''', (qty, qty, d, pid, store_id, qty, SQLITE_INTEGER_MAX - qty))
            loc = 'upstairs->instore'
        elif operation == 'out_dian':
            cur.execute('''
                UPDATE stock SET instore_qty = instore_qty - ?,
                                 last_updated = ?
                WHERE product_id = ? AND store_id = ? AND instore_qty >= ?
            ''', (qty, d, pid, store_id, qty))
            loc = 'instore_out'
        elif operation == 'ru_dian_claw':
            cur.execute('''
                UPDATE stock SET upstairs_qty = upstairs_qty - ?,
                                 instore_qty  = instore_qty  + ?,
                                 claw_qty     = claw_qty     + ?,
                                 last_updated = ?
                WHERE product_id = ? AND store_id = ? AND upstairs_qty >= ?
                  AND instore_qty <= ? AND claw_qty <= ?
            ''', (qty, qty, qty, d, pid, store_id, qty,
                  SQLITE_INTEGER_MAX - qty, SQLITE_INTEGER_MAX - qty))
            loc = 'upstairs->claw'
        else:  # restock_upstairs
            cur.execute('''
                UPDATE stock SET upstairs_qty = upstairs_qty + ?,
                                 last_updated = ?
                WHERE product_id = ? AND store_id = ? AND upstairs_qty <= ?
            ''', (qty, d, pid, store_id, SQLITE_INTEGER_MAX - qty))
            loc = 'upstairs'

        if cur.rowcount != 1:
            con.rollback()
            con.close()
            return jsonify({'error': 'Stock changed before the operation completed',
                            'code': 'insufficient_stock', 'product_id': pid}), 409

        cur.execute('''
            INSERT INTO stock_transactions (product_id, txn_type, qty, location, date, notes, store_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (pid, operation, qty, loc, d, notes, store_id))
        results.append({'pid': pid, 'ok': True})

    con.commit()
    con.close()
    return jsonify({'ok': True, 'results': results})


@bp.route('/api/sales/batch_upsert', methods=['POST'])
@role_required('manager')
def batch_upsert_sales():
    data = request.get_json()
    if not isinstance(data, dict):
        return jsonify({'error': 'Expected an object with store_code and items'}), 400

    con = get_db()
    store_id, store_code, err = _require_store_body(con, data)
    if err:
        con.close()
        return err
    denied = legacy_store_denied(con, request.jwt_payload, store_id)
    if denied:
        con.close()
        return denied

    items = data.get('items', [])
    if not isinstance(items, list):
        con.close()
        return jsonify({'error': 'items must be a list'}), 400

    rows = []
    for line, item in enumerate(items, 1):
        if not isinstance(item, dict):
            con.close()
            return jsonify({'error': 'Each item must be an object',
                            'code': 'invalid_input', 'line': line}), 400
        try:
            pid      = read_int(item['product_id'], 'product_id', minimum=1)
            qty_pos  = read_int(item.get('qty_pos', 0), 'qty_pos')
            qty_cash = read_int(item.get('qty_cash', 0), 'qty_cash')
            qty_sold = read_int(qty_pos + qty_cash, 'qty_sold')
        except KeyError:
            con.close()
            return jsonify({'error': 'product_id is required',
                            'code': 'invalid_input', 'field': 'product_id',
                            'line': line}), 400
        except ValueError as exc:
            con.close()
            return jsonify(invalid_input(exc, line=line)), 400
        try:
            item_date = read_date(item.get('date', business_date()))
        except ValueError as exc:
            con.close()
            return jsonify(invalid_input(exc, line=line)), 400
        rows.append((pid, item_date, store_code,
                     qty_pos, qty_cash, qty_sold, item.get('notes', '')))

    cur = con.cursor()
    for line, row in enumerate(rows, 1):
        if cur.execute('SELECT 1 FROM products WHERE id = ?', (row[0],)).fetchone() is None:
            con.close()
            return jsonify({'error': 'Product not found', 'code': 'not_found',
                            'field': 'product_id', 'line': line}), 404
    try:
        con.execute('BEGIN IMMEDIATE')
        cur.executemany('''
            INSERT INTO daily_sales (product_id, date, store, qty_pos, qty_cash, qty_sold, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(product_id, date, store) DO UPDATE SET
                qty_pos  = excluded.qty_pos,
                qty_cash = excluded.qty_cash,
                qty_sold = excluded.qty_sold,
                notes    = CASE WHEN excluded.notes != '' THEN excluded.notes ELSE notes END
        ''', rows)
        stock = [summary for day in sorted({row[1] for row in rows})
                 if (summary := sync_report_stock(con, request.jwt_payload, store_id, store_code, day))]
        con.commit()
    except InventoryError as exc:
        con.rollback()
        return _inventory_error(exc)
    finally:
        con.close()
    return jsonify({'ok': True, 'count': len(rows), 'stock': stock})


# Sections that count as sales, and the daily_sales quantity column each one fills.
SALES_SECTIONS = {'pos', 'cash', 'claw', 'sell_display', 'employee_discount'}
_SECTION_QTY_COL = {
    'pos':               'qty_pos',
    'cash':              'qty_cash',
    'claw':              'qty_claw',
    'sell_display':      'qty_display',
    'employee_discount': 'qty_employee',
}
# stock_transactions txn types written by report submission (reverted on re-submit)
_REPORT_TXN_TYPES = ('display_open', 'report_stock_in', 'report_stock_out')


def _revert_report_day(cur, d: str, store_code: str, store_id: int) -> tuple[int, int]:
    """Remove a previously submitted report for (date, store):
    delete its daily_sales rows and reverse its stock transactions.

    Reversal is best-effort: if the original update was clamped at 0, the
    reversal may overshoot slightly — acceptable, the evening count re-anchors.
    Returns (deleted_sales_rows, reverted_txns).
    """
    cur.execute('DELETE FROM daily_sales WHERE date = ? AND store = ?', (d, store_code))
    deleted_sales = cur.rowcount

    cur.execute(f'''
        SELECT product_id, txn_type, qty FROM stock_transactions
        WHERE date = ? AND store_id = ? AND txn_type IN ({','.join('?' * len(_REPORT_TXN_TYPES))})
    ''', (d, store_id, *_REPORT_TXN_TYPES))
    prior = cur.fetchall()
    for t in prior:
        pid_t, ttype, q = t['product_id'], t['txn_type'], t['qty']
        if ttype == 'display_open':
            # original: instore -= |q| (q is stored negative)
            cur.execute('''UPDATE stock SET instore_qty = instore_qty + ?
                           WHERE product_id = ? AND store_id = ?''', (abs(q), pid_t, store_id))
        elif ttype == 'report_stock_in':
            # original: upstairs -= q, instore += q
            cur.execute('''UPDATE stock SET upstairs_qty = upstairs_qty + ?,
                                            instore_qty  = MAX(0, instore_qty - ?)
                           WHERE product_id = ? AND store_id = ?''', (q, q, pid_t, store_id))
        elif ttype == 'report_stock_out':
            # original: instore -= q
            cur.execute('''UPDATE stock SET instore_qty = instore_qty + ?
                           WHERE product_id = ? AND store_id = ?''', (q, pid_t, store_id))
    if prior:
        cur.execute(f'''
            DELETE FROM stock_transactions
            WHERE date = ? AND store_id = ? AND txn_type IN ({','.join('?' * len(_REPORT_TXN_TYPES))})
        ''', (d, store_id, *_REPORT_TXN_TYPES))
    return deleted_sales, len(prior)


_REPORT_NOTE_FIELDS = ('employee_discounts', 'display_sales', 'claw_prizes',
                       'cash_exchanges', 'claw_stock_in', 'display_stock_in', 'display_stock_out',
                       'store_transfers')


def _report_money_cents(value, field):
    if value is None:
        return None
    if not isinstance(value, (str, int, float)) or not re.fullmatch(r'[0-9]+(?:\.[0-9]{1,2})?', str(value)):
        raise ValueError(f'{field} must be a nonnegative amount with at most two decimals')
    return read_int(int(Decimal(str(value)) * 100), field)


@bp.route('/api/sales/report-metadata')
@role_required('manager')
def report_metadata():
    try:
        d = read_date(request.args.get('date'))
    except ValueError as exc:
        return jsonify(invalid_input(exc)), 400
    con = get_db()
    store = (request.args.get('store') or '').strip().upper()
    if not _resolve_store(con, store):
        return jsonify({'error': 'Select a specific store', 'code': 'invalid_input'}), 400
    row = con.execute('SELECT * FROM daily_report_metadata WHERE date=? AND store=?',
                      (d, store)).fetchone()
    actual = row['cash_actual_cents'] if row else None
    expected = row['cash_expected_cents'] if row else None
    return jsonify({
        'cash_actual': actual / 100 if actual is not None else None,
        'cash_expected': expected / 100 if expected is not None else None,
        'cash_difference': (actual - expected) / 100 if actual is not None and expected is not None else None,
        **{key: json.loads(row[key]) if row else []
           for key in _REPORT_NOTE_FIELDS},
    })


@bp.route('/api/sales/submit_daily_report', methods=['POST'])
@role_required('manager')
def submit_daily_report():
    """
    Submit a parsed daily report in one atomic transaction.

    A submission IS the day: by default (mode='replace') any previously
    submitted report for the same (date, store) is removed first — its
    daily_sales rows deleted. Reports with stock history require reconciliation;
    their stock movements are never reversed by this endpoint.
    Pass mode='append' for the legacy accumulate behaviour.

    Body:
    {
      "date":       "2026-04-01",
      "store_code": "DT",
      "mode":       "replace" | "append"   (default "replace"),
      "items": [
        { "product_id": <int>, "section": "pos"|"cash"|"break_display"|"stock_in"|"stock_out",
          "qty_pos": <int>, "qty_cash": <int>, "qty": <int>,
          "box_size": <int>, "num_boxes": <int>, "notes": <str>, "raw_name": <str> }
      ]
    }
    """
    data  = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({'error': 'Expected an object', 'code': 'invalid_input'}), 400
    raw_date = data.get('date')
    items = data.get('items')
    mode  = (data.get('mode') or 'replace').strip().lower()

    if not raw_date or not isinstance(items, list):
        return jsonify({'error': 'date and items required'}), 400
    if mode not in ('replace', 'append'):
        return jsonify({'error': "mode must be 'replace' or 'append'"}), 400

    try:
        d = read_date(raw_date)
    except ValueError as exc:
        return jsonify(invalid_input(exc)), 400

    metadata = data.get('report_metadata')
    try:
        if metadata is not None:
            if not isinstance(metadata, dict):
                raise ValueError('report_metadata must be an object')
            if mode != 'replace':
                raise ValueError('report_metadata requires replace mode')
            cash_actual = _report_money_cents(metadata.get('cash_actual'), 'cash_actual')
            cash_expected = _report_money_cents(metadata.get('cash_expected'), 'cash_expected')
            annotation_lists = {}
            for key in _REPORT_NOTE_FIELDS:
                notes = metadata.get(key, [])
                if not isinstance(notes, list) or any(not isinstance(n, str) for n in notes):
                    raise ValueError(f'{key} must be a list of notes')
                annotation_lists[key] = json.dumps(notes, ensure_ascii=False)
    except ValueError as exc:
        return jsonify(invalid_input(exc)), 400

    section_choices = data.get('section_choices', [])
    if not isinstance(section_choices, list):
        return jsonify(invalid_input('section_choices must be a list')), 400
    learned_sections = {}
    for choice in section_choices:
        if not isinstance(choice, dict) or not isinstance(choice.get('header'), str):
            return jsonify(invalid_input('section_choices require an explicit heading')), 400
        header = unicodedata.normalize('NFKC', choice['header']).strip()
        section = choice.get('section')
        heading = _detect_section_type(header, {})
        if (not header or len(header.splitlines()) != 1 or not header.endswith(':')
                or not header[:-1].strip() or any(c in header[:-1] for c in ':*')
                or not isinstance(section, str)
                or section not in {'pos', 'cash', 'stock_in', 'stock_out', 'break_display', 'skip'}
                or heading != ('unknown', '')):
            return jsonify(invalid_input('section_choices require an unknown heading and supported section')), 400
        key = re.sub(r'\s+', '', header.rstrip(':').lower())
        stored_section = 'ignore' if section == 'skip' else section
        if key in learned_sections and learned_sections[key] != stored_section:
            return jsonify({'error': 'Conflicting section choices', 'code': 'section_choice_conflict'}), 409
        learned_sections[key] = stored_section

    dry_run = data.get('dry_run') is True
    validated_items = []
    valid_sections = (SALES_SECTIONS - {'employee_discount', 'sell_display', 'claw'}) | set(LINE_SECTIONS)
    for line, item in enumerate(items, 1):
        if not isinstance(item, dict):
            return jsonify({'error': 'Each item must be an object',
                            'code': 'invalid_input', 'line': line}), 400
        try:
            if item.get('flagged') or item.get('unknown_header'):
                raise ValueError('Resolve every report item before submitting')
            normalized = dict(item)
            for field in ('raw_name', 'notes', 'source_bucket'):
                if item.get(field) is not None and not isinstance(item[field], str):
                    raise ValueError(f'{field} must be text')
            normalized['product_id'] = read_int(
                item['product_id'], 'product_id', minimum=1
            )
            section = item.get('section')
            if not isinstance(section, str) or section.strip() not in valid_sections:
                raise ValueError('section must be a supported report section')
            normalized['section'] = section.strip()
            if normalized['section'] in SALES_SECTIONS:
                normalized['qty_pos'] = read_int(item.get('qty_pos', 0), 'qty_pos')
                normalized['qty_cash'] = read_int(item.get('qty_cash', 0), 'qty_cash')
                normalized['qty'] = read_int(item.get('qty', 0), 'qty')
                read_int((normalized['qty_pos'] + normalized['qty_cash']) or normalized['qty'], 'qty_sold', minimum=1)
            elif normalized['section'] not in {'stock_in', 'refill'}:
                normalized['qty'] = read_int(item.get('qty'), 'qty', minimum=1)
            else:
                normalized['box_size'] = read_int(
                    item.get('box_size'), 'box_size', minimum=1
                )
                normalized['num_boxes'] = read_int(
                    item.get('num_boxes'), 'num_boxes', minimum=1
                )
                normalized['loose_qty'] = read_int(item.get('loose_qty', 0), 'loose_qty')
                read_int(
                    normalized['box_size'] * normalized['num_boxes'] + normalized['loose_qty'],
                    'total_units',
                    minimum=1,
                )
            for flag in ('to_display', 'from_display'):
                if item.get(flag) is not None and not isinstance(item[flag], bool):
                    raise ValueError(f'{flag} must be true or false')
                normalized[flag] = item.get(flag) is True
        except KeyError:
            return jsonify({'error': 'product_id is required',
                            'code': 'invalid_input', 'field': 'product_id',
                            'line': line}), 400
        except ValueError as exc:
            return jsonify(invalid_input(exc, line=line)), 400
        validated_items.append(normalized)

    con = get_db()
    store_id, store_code, err = _require_store_body(con, data)
    if err:
        con.close()
        return err
    denied = legacy_store_denied(con, request.jwt_payload, store_id)
    if denied:
        con.close()
        return denied

    live = _is_authoritative(con, store_id)
    stock_items = [(line, item) for line, item in enumerate(validated_items, 1) if item['section'] in LINE_SECTIONS]
    store_mode = 'live' if live else 'legacy'
    if stock_items and ((data.get('stock_mode') or store_mode) != store_mode or not live and any(
            item['section'] not in {'stock_in', 'stock_out', 'break_display'} for _, item in stock_items)):
        con.close()
        # 入店 means new goods at a live store but upstairs → floor at a legacy one: read it again for this store.
        return jsonify({'error': f'This report was read for a store in the other stock mode; parse it again for {store_code}',
                        'code': 'report_store_mode_changed'}), 409
    if live and stock_items and mode != 'replace':
        con.close()
        return jsonify(invalid_input('A live store\'s report stock lines need replace mode')), 400

    cur = con.cursor()
    product_ids = {item['product_id'] for item in validated_items}
    if product_ids:
        placeholders = ','.join('?' * len(product_ids))
        found = {
            row[0] for row in cur.execute(
                f'SELECT id FROM products WHERE id IN ({placeholders})',
                tuple(product_ids),
            )
        }
        if found != product_ids:
            con.close()
            return jsonify({'error': 'Product not found', 'code': 'not_found',
                            'field': 'product_id'}), 404
    sales_count   = 0
    txn_count     = 0
    replaced_rows = 0
    reverted_txns = 0
    corrections   = []
    _price_cache: dict = {}

    def _unit_price(pid):
        if pid not in _price_cache:
            cur.execute('SELECT price FROM products WHERE id = ?', (pid,))
            row = cur.fetchone()
            _price_cache[pid] = row['price'] if row else None
        return _price_cache[pid]

    try:
        con.execute('BEGIN IMMEDIATE')
        for key, section in learned_sections.items():
            existing = _detect_section_type(key + ':', {
                row['alias_norm']: row['section_type']
                for row in cur.execute('SELECT alias_norm,section_type FROM section_aliases')
            })
            if existing != ('unknown', '') and existing != ('skip' if section == 'ignore' else section, ''):
                con.rollback()
                con.close()
                return jsonify({'error': 'Heading already has a different section',
                                'code': 'section_choice_conflict'}), 409
            cur.execute('INSERT OR IGNORE INTO section_aliases(alias_norm,section_type) VALUES (?,?)',
                        (key, section))
        if mode == 'replace':
            prior_stock = cur.execute(f'''
                SELECT 1 FROM stock_transactions
                WHERE date = ? AND store_id = ?
                  AND txn_type IN ({','.join('?' * len(_REPORT_TXN_TYPES))})
                LIMIT 1
            ''', (d, store_id, *_REPORT_TXN_TYPES)).fetchone()
            if prior_stock:
                con.rollback()
                con.close()
                return jsonify({
                    'error': 'This report has stock history and requires reconciliation',
                    'code': 'reconciliation_required',
                }), 409
            replaced_rows, reverted_txns = _revert_report_day(cur, d, store_code, store_id)

        required_instore = {}
        required_upstairs = {}
        for item in validated_items if not live else ():
            pid = item['product_id']
            if item['section'] in {'break_display', 'stock_out'}:
                required_instore[pid] = read_int(
                    required_instore.get(pid, 0) + item['qty'],
                    'total_quantity', minimum=1,
                )
            elif item['section'] == 'stock_in':
                qty = read_int(item['box_size'] * item['num_boxes'] + item['loose_qty'], 'total_units', minimum=1)
                required_upstairs[pid] = read_int(
                    required_upstairs.get(pid, 0) + qty,
                    'total_units', minimum=1,
                )
        for pid in set(required_instore) | set(required_upstairs):
            balance = cur.execute(
                '''SELECT upstairs_qty, instore_qty FROM stock
                   WHERE product_id=? AND store_id=?''',
                (pid, store_id),
            ).fetchone()
            upstairs = balance['upstairs_qty'] if balance else 0
            instore = balance['instore_qty'] if balance else 0
            if (required_instore.get(pid, 0) > instore
                    or required_upstairs.get(pid, 0) > upstairs):
                con.rollback()
                con.close()
                return jsonify({
                    'error': 'Insufficient stock for report',
                    'code': 'insufficient_stock',
                    'product_id': pid,
                }), 409
            try:
                read_int(
                    instore + required_upstairs.get(pid, 0), 'instore_qty'
                )
            except ValueError as exc:
                con.rollback()
                con.close()
                return jsonify(invalid_input(exc)), 400

        for item in validated_items:
            pid     = item.get('product_id')
            section = (item.get('section') or '').strip()
            notes   = (item.get('notes') or '').strip()
            raw     = (item.get('raw_name') or '').strip()
            if not pid or not section:
                continue

            if section in SALES_SECTIONS:
                qty_pos  = item['qty_pos']
                qty_cash = item['qty_cash']
                qty      = item['qty']
                base_qty = (qty_pos + qty_cash) or qty
                if base_qty <= 0:
                    continue

                quantities = {c: 0 for c in _SECTION_QTY_COL.values()}
                quantities[_SECTION_QTY_COL[section]] = base_qty

                cur.execute(f'''
                    INSERT INTO daily_sales
                        (product_id, date, store, qty_pos, qty_cash, qty_claw,
                         qty_display, qty_employee, qty_sold, unit_price, raw_name, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(product_id, date, store) DO UPDATE SET
                        qty_pos      = qty_pos      + excluded.qty_pos,
                        qty_cash     = qty_cash     + excluded.qty_cash,
                        qty_claw     = qty_claw     + excluded.qty_claw,
                        qty_display  = qty_display  + excluded.qty_display,
                        qty_employee = qty_employee + excluded.qty_employee,
                        qty_sold     = qty_sold     + excluded.qty_sold,
                        unit_price   = COALESCE(unit_price, excluded.unit_price),
                        raw_name     = CASE
                            WHEN raw_name = '' THEN excluded.raw_name
                            WHEN excluded.raw_name = '' OR instr(raw_name, excluded.raw_name) > 0 THEN raw_name
                            ELSE raw_name || '；' || excluded.raw_name
                        END,
                        notes        = CASE
                            WHEN notes = '' THEN excluded.notes
                            WHEN excluded.notes = '' THEN notes
                            ELSE notes || '; ' || excluded.notes
                        END
                    WHERE qty_pos <= {SQLITE_INTEGER_MAX} - excluded.qty_pos
                      AND qty_cash <= {SQLITE_INTEGER_MAX} - excluded.qty_cash
                      AND qty_claw <= {SQLITE_INTEGER_MAX} - excluded.qty_claw
                      AND qty_display <= {SQLITE_INTEGER_MAX} - excluded.qty_display
                      AND qty_employee <= {SQLITE_INTEGER_MAX} - excluded.qty_employee
                      AND qty_sold <= {SQLITE_INTEGER_MAX} - excluded.qty_sold
                ''', (pid, d, store_code,
                      quantities['qty_pos'], quantities['qty_cash'], quantities['qty_claw'],
                      quantities['qty_display'], quantities['qty_employee'],
                      base_qty, _unit_price(pid), raw, notes))
                if cur.rowcount != 1:
                    raise ValueError('qty_sold exceeds the supported range')
                sales_count += 1

            elif live:
                # A live store's stock lines move through report_stock below.
                continue

            elif section == 'break_display':
                qty = item['qty']
                _ensure_stock_row(cur, pid, store_id)
                cur.execute('''
                    INSERT INTO stock_transactions
                        (product_id, txn_type, qty, location, date, notes, store_id)
                    VALUES (?, 'display_open', ?, 'instore', ?, ?, ?)
                ''', (pid, -qty, d, notes or 'display opened', store_id))
                cur.execute('''
                    UPDATE stock SET instore_qty = instore_qty - ?,
                                     last_updated = datetime('now')
                    WHERE product_id = ? AND store_id = ? AND instore_qty >= ?
                ''', (qty, pid, store_id, qty))
                if cur.rowcount != 1:
                    con.rollback()
                    con.close()
                    return jsonify({'error': 'Stock changed before report submission',
                                    'code': 'insufficient_stock', 'product_id': pid}), 409
                txn_count += 1

            elif section == 'stock_out':
                qty = item['qty']
                _ensure_stock_row(cur, pid, store_id)
                cur.execute('''
                    INSERT INTO stock_transactions
                        (product_id, txn_type, qty, location, date, notes, store_id)
                    VALUES (?, 'report_stock_out', ?, 'instore→out', ?, ?, ?)
                ''', (pid, qty, d, notes or '出店', store_id))
                cur.execute('''
                    UPDATE stock SET instore_qty = instore_qty - ?,
                                     last_updated = datetime('now')
                    WHERE product_id = ? AND store_id = ? AND instore_qty >= ?
                ''', (qty, pid, store_id, qty))
                if cur.rowcount != 1:
                    con.rollback()
                    con.close()
                    return jsonify({'error': 'Stock changed before report submission',
                                    'code': 'insufficient_stock', 'product_id': pid}), 409
                txn_count += 1

            elif section == 'stock_in':
                box_size   = item['box_size']
                num_boxes  = item['num_boxes']
                total_units = read_int(box_size * num_boxes + item['loose_qty'], 'total_units', minimum=1)
                cur.execute('''
                    INSERT INTO stock_transactions
                        (product_id, txn_type, qty, location, date, notes, store_id)
                    VALUES (?, 'report_stock_in', ?, 'upstairs→instore', ?, ?, ?)
                ''', (pid, total_units, d, notes or f'{num_boxes}端', store_id))
                cur.execute('''
                    UPDATE stock
                    SET upstairs_qty = upstairs_qty - ?,
                        instore_qty = instore_qty + ?,
                        last_updated = datetime('now')
                    WHERE product_id = ? AND store_id = ? AND upstairs_qty >= ?
                      AND instore_qty <= ?
                ''', (total_units, total_units, pid, store_id, total_units,
                      SQLITE_INTEGER_MAX - total_units))
                if cur.rowcount != 1:
                    con.rollback()
                    con.close()
                    return jsonify({'error': 'Stock changed before report submission',
                                    'code': 'insufficient_stock', 'product_id': pid}), 409
                txn_count += 1

        # Record match corrections for manually-reviewed items (Issue 3)
        for item in validated_items:
            bucket = (item.get('source_bucket') or '').strip()
            if bucket not in ('review', 'failed'):
                continue
            raw_name_c = (item.get('raw_name') or '').strip()
            pid_c = item.get('product_id')
            if not raw_name_c or not pid_c:
                continue
            # Qualified review must not dispute an unrelated bare legacy alias.
            notes_c = (item.get('notes') or '').strip()
            correction_name = raw_name_c + ' (' + notes_c + ')' if notes_c else raw_name_c
            norm_name_c = _norm_jzm(_clean_jzm(correction_name))
            fuzzy_score_c = int(item.get('fuzzy_score', 0) or 0)
            top_score_c   = int(item.get('top_score',   0) or 0)
            was_top_c     = 1 if item.get('was_top') else 0
            corrections.append((raw_name_c, norm_name_c, pid_c,
                                 fuzzy_score_c, top_score_c, was_top_c, store_code))
        if corrections:
            cur.executemany('''
                INSERT INTO match_corrections
                    (raw_name, norm_name, product_id, fuzzy_score, top_score, was_top, store)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', corrections)

        if any(item['section'] in SALES_SECTIONS for item in validated_items) \
                and data.get('classification') != 'summary_only':
            con.rollback()
            con.close()
            return jsonify({
                'error': 'Classify this legacy report as a reconciliation summary before saving',
                'code': 'reconciliation_classification_required',
            }), 409

        # 名称记忆 (owner, 2026-11-03): a name reviewed here is remembered for every section, and confirms
        # by itself where it was reviewed (this store, this section). A different product replaces the old
        # pick; one report picking two products for a name keeps it disputed. Report-only exact choices
        # never widen into global aliases.
        reviewed = {}
        for item in validated_items:
            if item.get('source_bucket') in ('review', 'failed') and (item.get('raw_name') or '').strip():
                reviewed.setdefault(report_match_key(item['raw_name'], item.get('notes') or ''), []).append(item)
        for key, rows in reviewed.items():
            save_pick(cur, key, rows[0]['raw_name'].strip(), (rows[0].get('notes') or '').strip(),
                      sorted({row['product_id'] for row in rows}), request.jwt_payload.get('sub'), store_id,
                      {place_of(row) for row in rows})

        if mode == 'replace':
            cur.execute('DELETE FROM daily_report_metadata WHERE date=? AND store=?', (d, store_code))
        if metadata is not None:
            cur.execute(f"""INSERT INTO daily_report_metadata
                (date, store, cash_actual_cents, cash_expected_cents, {','.join(_REPORT_NOTE_FIELDS)})
                VALUES ({','.join('?' for _ in range(4 + len(_REPORT_NOTE_FIELDS)))})""",
                (d, store_code, cash_actual, cash_expected,
                 *(annotation_lists[key] for key in _REPORT_NOTE_FIELDS)))
        since = live_since(con, store_id) if live else None
        if stock_items and cur.execute(
                """SELECT 1 FROM inventory_documents WHERE source_type='daily_report' AND source_id LIKE ?
                   AND source_id LIKE '%move-%' AND reason LIKE ? LIMIT 1""",
                (f'{store_code}:{d}:%', f'日报 {d}%')).fetchone():
            # DT's Oct 29–31 stock lines were posted on the server by hand (source <store>:<day>:move-N, reason
            # "日报 <day> …"); the report must not move them a second time. The owner's own fixes posted the same
            # way ("店主 …") are not report lines.
            con.rollback()
            con.close()
            return jsonify({'error': f'{store_code} {d}: this day\'s stock lines were already recorded by hand; '
                                     'remove them from the report (its sales can still be saved)',
                            'code': 'report_moves_recorded'}), 409
        if since is not None and d >= since and mode == 'replace':
            # This report is now the day's stock lines too (owner, 2026-11-03); a line no longer on it is put back.
            save_report_lines(con, store_id, d, plan_report_lines(con, store_id, stock_items))
        # A live store's floor follows the day's sales (owner, 2026-10-29) and its stock lines.
        stock = sync_report_stock(con, request.jwt_payload, store_id, store_code, d)
        changes = [line for line in (stock or {}).get('lines', []) if line['moving'] or line['short']]
        digest = hashlib.sha256(json.dumps(sorted(
            (line['line_key'], line['moving'], line['short']) for line in changes)).encode()).hexdigest()[:16]
        if dry_run or (changes and data.get('stock_digest') != digest):
            # The manager confirms what the stock lines move before a report saves (owner, 2026-11-03).
            con.rollback()
            con.close()
            body = {'stock': stock, 'stock_digest': digest, 'needs_confirmation': bool(changes)}
            if dry_run:
                return jsonify({'ok': True, 'dry_run': True, **body})
            return jsonify({'error': 'Confirm what this report moves in stock before saving it',
                            'code': 'stock_confirmation_required', **body}), 409
        con.commit()
    except ReportLineError as exc:
        con.rollback()
        con.close()
        return jsonify({'error': str(exc), 'code': exc.code, 'line': exc.line}), exc.status
    except InventoryError as exc:
        con.rollback()
        con.close()
        return _inventory_error(exc)
    except ValueError as exc:
        con.rollback()
        con.close()
        return jsonify(invalid_input(exc)), 400
    except Exception as e:
        con.rollback()
        con.close()
        return jsonify({'error': str(e)}), 500

    if corrections:
        try:
            import ranker as _ranker
            _ranker.invalidate_cache()
        except Exception:
            pass

    con.close()
    return jsonify({
        'ok':                  True,
        'mode':                mode,
        'sales_upserted':      sales_count,
        'stock_transactions':  txn_count,
        'replaced_sales_rows': replaced_rows,
        'reverted_stock_txns': reverted_txns,
        'stock':               stock,
    })


@bp.route('/api/sales/export')
@role_required('manager')
def export_sales():
    today     = business_date()
    from_date = request.args.get('from', '')
    to_date   = request.args.get('to',   today)
    if not from_date:
        from_date = str(date.fromisoformat(today) - timedelta(days=30))

    con = get_db()
    store_id, store_code, err = _require_store_param(con)
    if err:
        con.close()
        return err
    cur = con.cursor()
    cur.execute('''
        SELECT ds.date, p.jizhanming, p.sku, p.ip_series, p.product_type,
               ds.unit_price AS price, ds.unit_price,
               ds.qty_pos, ds.qty_cash, ds.qty_claw, ds.qty_display, ds.qty_employee,
               ds.qty_sold, ds.raw_name, ds.notes
        FROM daily_sales ds
        JOIN products p ON p.id = ds.product_id
        WHERE ds.date BETWEEN ? AND ? AND ds.store = ?
        ORDER BY ds.date DESC, p.ip_series, p.jizhanming
    ''', (from_date, to_date, store_code))
    rows = cur.fetchall()
    con.close()

    header = '日期,记账名,SKU,系列,类型,单价,卡机数量,非卡机数量(现金/转账/微信/支付宝),娃娃机,卖Display,员工折扣,总销量,原始输入,备注'
    lines  = ['﻿' + header]
    for r in rows:
        lines.append(','.join(esc_csv(v) for v in [
            r['date'], r['jizhanming'], r['sku'], r['ip_series'], r['product_type'],
            r['price'], r['qty_pos'], r['qty_cash'], r['qty_claw'], r['qty_display'],
            r['qty_employee'], r['qty_sold'], r['raw_name'], r['notes']
        ]))

    csv_content = '\n'.join(lines)
    fname = f'sales_{store_code}_{from_date}_{to_date}.csv'
    return Response(
        csv_content,
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="{fname}"'}
    )


@bp.route('/api/sales/clear_day', methods=['DELETE'])
@role_required('manager')
def clear_sales_day():
    try:
        d = read_date(request.args.get('date', ''))
    except ValueError as exc:
        return jsonify(invalid_input(exc)), 400
    store_code_raw = (request.args.get('store_code') or '').strip().upper()
    if store_code_raw == 'ALL':
        return jsonify({'error': 'Cannot write with store_code ALL. Select a specific store.'}), 400
    con = get_db()
    con.execute('BEGIN IMMEDIATE')
    store_id, store_code, err = _require_store_param(con)
    if err:
        con.rollback()
        con.close()
        return err
    denied = legacy_store_denied(con, request.jwt_payload, store_id)
    if denied:
        con.rollback()
        con.close()
        return denied
    cur = con.cursor()
    prior_stock = cur.execute(f'''
        SELECT 1 FROM stock_transactions
        WHERE date = ? AND store_id = ?
          AND txn_type IN ({','.join('?' * len(_REPORT_TXN_TYPES))})
        LIMIT 1
    ''', (d, store_id, *_REPORT_TXN_TYPES)).fetchone()
    if prior_stock:
        con.rollback()
        con.close()
        return jsonify({
            'error': 'This report has stock history and requires reconciliation',
            'code': 'reconciliation_required',
        }), 409
    cur.execute('DELETE FROM daily_sales WHERE date = ? AND store = ?', (d, store_code))
    deleted = cur.rowcount
    cur.execute('DELETE FROM daily_report_metadata WHERE date=? AND store=?', (d, store_code))
    try:
        stock = sync_report_stock(con, request.jwt_payload, store_id, store_code, d)
    except InventoryError as exc:
        con.rollback()
        con.close()
        return _inventory_error(exc)
    con.commit()
    con.close()
    return jsonify({'ok': True, 'deleted': deleted, 'stock': stock})


def _report_stock_stores(con):
    """Stores whose report stock notices this manager may see: their inventory access, or one of them."""
    stores = [dict(row) for row in con.execute(
        """SELECT s.id, s.code FROM inventory_access a JOIN stores s ON s.id=a.store_id
           WHERE a.auth0_sub=? AND s.is_active=1 ORDER BY s.id""", (request.jwt_payload.get('sub'),))]
    code = (request.args.get('store_code') or 'ALL').strip().upper()
    return stores if code == 'ALL' else [store for store in stores if store['code'] == code]


@bp.get('/api/sales/report-stock')
@role_required('manager')
def report_stock_notices():
    """Mismatches live stores' daily reports found, open until settled or checked (owner, 2026-10-29)."""
    con = get_db()
    try:
        return jsonify({'notices': open_notices(con, [store['id'] for store in _report_stock_stores(con)])})
    finally:
        con.close()


@bp.post('/api/sales/report-stock/<int:notice_id>/check')
@role_required('manager')
def check_report_stock_notice(notice_id):
    con = get_db()
    try:
        con.execute('BEGIN IMMEDIATE')
        result = check_notice(con, request.jwt_payload, notice_id)
        con.commit()
        return jsonify(result)
    except InventoryError as exc:
        con.rollback()
        return _inventory_error(exc)
    finally:
        con.close()


@bp.post('/api/sales/report-stock/retry')
@role_required('manager')
def retry_report_stock():
    """Take a day's report off the floor again, after stock that was missing has been recorded."""
    data = request.get_json(silent=True) or {}
    try:
        d = read_date(data.get('date', ''))
    except ValueError as exc:
        return jsonify(invalid_input(exc)), 400
    con = get_db()
    try:
        store_id, store_code, err = _require_store_body(con, data)
        if err:
            return err
        con.execute('BEGIN IMMEDIATE')
        stock = sync_report_stock(con, request.jwt_payload, store_id, store_code, d)
        con.commit()
        return jsonify({'ok': True, 'stock': stock})
    except InventoryError as exc:
        con.rollback()
        return _inventory_error(exc)
    finally:
        con.close()


# ── 名称记忆 Name picks (owner, 2026-11-03) ─────────────────────────────────
_PICK_SECTIONS = {'pos', 'cash'} | set(LINE_SECTIONS)


def _name_pick_stores(con, code=None):
    """Stores whose pasted names this manager may answer: the ones they may save reports for."""
    code = (code or request.args.get('store_code') or 'ALL').strip().upper()
    return [dict(row) for row in con.execute('SELECT id, code FROM stores WHERE is_active=1 ORDER BY id')
            if (code == 'ALL' or row['code'] == code) and can_write_legacy_store(con, request.jwt_payload, row['id'])]


def _pick_key(data):
    """The exact report key from a written name and note, or from the page's own name_key/note_key."""
    if 'name_key' in data:
        key = (data.get('name_key'), data.get('note_key', ''))
    else:
        key = (data.get('raw_name'), data.get('note', ''))
    if not all(isinstance(value, str) and len(value) <= 500 for value in key):
        raise ValueError('name and note must be text')
    key = report_match_key(*key)
    if not key[0]:
        raise ValueError('name is required')
    return key


def _pick_replaces(cur, data, key):
    """The products the manager saw for this name; a pick changed since is not overwritten unseen."""
    replaces = data.get('replaces', [])
    if not isinstance(replaces, list) or any(type(pid) is not int for pid in replaces):
        raise ValueError('replaces must be a list of product ids')
    current = sorted(row[0] for row in cur.execute(
        'SELECT product_id FROM report_match_choices WHERE name_key=? AND note_key=?', key))
    if current != sorted(set(replaces)):
        raise InventoryConflict('This name was picked again meanwhile. Refresh to see it.', 'pick_changed')


@bp.get('/api/sales/name-picks')
@role_required('manager')
def name_picks():
    """Every remembered report name, and each store's pasted days with their names' statuses."""
    con = get_db()
    try:
        return jsonify({'picks': all_picks(con), 'days': report_days(con, _name_pick_stores(con))})
    finally:
        con.close()


@bp.get('/api/sales/name-picks/day')
@role_required('manager')
def name_picks_day():
    """One pasted day's names, in paste order."""
    con = get_db()
    try:
        day = read_date(request.args.get('date'))
        code = (request.args.get('store_code') or '').strip().upper()
        stores = _name_pick_stores(con, code) if code and code != 'ALL' else []
        if not stores:
            return jsonify({'error': 'Choose one store you can save reports for', 'code': 'inventory_forbidden'}), 403
        return jsonify({'store_code': stores[0]['code'], 'date': day, 'names': report_names(con, stores[0], day)})
    except ValueError as exc:
        return jsonify(invalid_input(exc)), 400
    finally:
        con.close()


@bp.post('/api/sales/name-picks')
@role_required('manager')
def save_name_pick():
    """Pick, confirm or change the product a report name means. A different product replaces the old
    pick. With store_code and section (a pasted line), the pick also confirms that line's situation."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(invalid_input('body must be an object')), 400
    con = get_db()
    try:
        key = _pick_key(data)
        product_id = read_int(data.get('product_id'), 'product_id', minimum=1)
        store_id, places = None, set()
        if data.get('store_code') is not None:
            stores = _name_pick_stores(con, data['store_code']) if isinstance(data['store_code'], str) else []
            if not stores or data['store_code'].strip().upper() == 'ALL':
                return jsonify({'error': 'Inventory access denied', 'code': 'inventory_forbidden'}), 403
            if data.get('section') not in _PICK_SECTIONS or not isinstance(data.get('display', False), bool):
                raise ValueError('section must be a report section and display true or false')
            store_id, places = stores[0]['id'], {(data['section'], data.get('display', False))}
        cur = con.cursor()
        con.execute('BEGIN IMMEDIATE')
        _pick_replaces(cur, data, key)
        if cur.execute('SELECT 1 FROM products WHERE id=?', (product_id,)).fetchone() is None:
            con.rollback()
            return jsonify({'error': 'Product not found', 'code': 'not_found', 'field': 'product_id'}), 404
        written = data.get('raw_name') if isinstance(data.get('raw_name'), str) else key[0]
        note = data.get('note') if isinstance(data.get('note'), str) else key[1]
        save_pick(cur, key, written.strip() or key[0], note.strip(), [product_id], request.jwt_payload.get('sub'),
                  store_id, places)
        con.commit()
        return jsonify({'ok': True, 'name_key': key[0], 'note_key': key[1]})
    except ValueError as exc:
        con.rollback()
        return jsonify(invalid_input(exc)), 400
    except InventoryError as exc:
        con.rollback()
        return _inventory_error(exc)
    finally:
        con.close()


@bp.delete('/api/sales/name-picks')
@role_required('manager')
def forget_name_pick():
    """Forget a pick: the next paste matches the name as if it had never been answered."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(invalid_input('body must be an object')), 400
    con = get_db()
    try:
        key = _pick_key(data)
        cur = con.cursor()
        con.execute('BEGIN IMMEDIATE')
        _pick_replaces(cur, data, key)
        cur.execute('DELETE FROM report_match_choices WHERE name_key=? AND note_key=?', key)
        con.commit()
        return jsonify({'ok': True})
    except ValueError as exc:
        con.rollback()
        return jsonify(invalid_input(exc)), 400
    except InventoryError as exc:
        con.rollback()
        return _inventory_error(exc)
    finally:
        con.close()


@bp.post('/api/sales/name-picks/ignore')
@role_required('manager')
def ignore_name():
    """Set a name aside (an outfit, a one-off order) or bring it back. Pastes still ask about it."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get('ignored'), bool):
        return jsonify(invalid_input('ignored must be true or false')), 400
    con = get_db()
    try:
        key = _pick_key(data)
        con.execute('BEGIN IMMEDIATE')
        if data['ignored']:
            con.execute("""INSERT INTO report_name_dismissals(name_key, note_key, dismissed_by) VALUES (?, ?, ?)
                           ON CONFLICT(name_key, note_key) DO NOTHING""", (*key, request.jwt_payload.get('sub')))
        else:
            con.execute('DELETE FROM report_name_dismissals WHERE name_key=? AND note_key=?', key)
        con.commit()
        return jsonify({'ok': True})
    except ValueError as exc:
        con.rollback()
        return jsonify(invalid_input(exc)), 400
    finally:
        con.close()


@bp.route('/api/sales/recorded-dates')
@role_required('manager')
def recorded_dates():
    """Return dates in a given month that have at least one daily_sales row."""
    month      = (request.args.get('month') or '').strip()      # YYYY-MM
    store_code = (request.args.get('store') or '').strip().upper()
    if not month or not store_code:
        return jsonify({'error': 'month and store are required'}), 400
    prefix = month + '-'   # match YYYY-MM-* date strings
    con = get_db()
    cur = con.cursor()
    if store_code == 'ALL':
        cur.execute(
            "SELECT date FROM daily_sales WHERE date LIKE ? UNION "
            "SELECT date FROM daily_report_metadata WHERE date LIKE ? ORDER BY date",
            (prefix + '%', prefix + '%'),
        )
    else:
        cur.execute(
            "SELECT date FROM daily_sales WHERE date LIKE ? AND store = ? UNION "
            "SELECT date FROM daily_report_metadata WHERE date LIKE ? AND store = ? ORDER BY date",
            (prefix + '%', store_code, prefix + '%', store_code),
        )
    dates = [r['date'] for r in cur.fetchall()]
    con.close()
    return jsonify(dates)


# ─────────────────────────────────────────────────────────────────────────────
# IMPORT PIPELINE  (Layers 1 – 5)
# ─────────────────────────────────────────────────────────────────────────────

# Layer 2 — section keyword map (longer/more-specific strings first, lowercase;
# comparison is case-insensitive)
_SECTION_MAP = [
    ('出库入店', 'refill'),
    ('卡机汇总', 'pos'), ('随手记汇总', 'cash'), ('随手机汇总', 'cash'),
    ('随手记', 'cash'), ('卡机', 'pos'),
    ('入店display', 'display_stock_in'), ('出店display', 'display_stock_out'),
    ('入display', 'display_stock_in'), ('出display', 'display_stock_out'),
    ('入娃娃机', 'claw_stock_in'), ('娃娃机入', 'claw_stock_in'),
    ('娃娃机出', 'claw'), ('出娃娃机', 'claw'),
    ('入店', 'stock_in'), ('出店', 'stock_out'), ('出库', 'store_transfer'),
    ('卖display', 'sell_display'), ('卖展示', 'sell_display'),
    ('拆display', 'break_display'), ('拆展示', 'break_display'),
    ('娃娃机', 'claw'), ('员工折扣', 'employee_discount'),
    ('晚盘', 'skip'), ('博主探店', 'skip'), ('现金', 'cash_total'),
]
_REPORT_ANNOTATIONS = {
    'employee_discount': 'employee_discounts', 'sell_display': 'display_sales',
    'claw': 'claw_prizes', 'claw_stock_in': 'claw_stock_in',
    'display_stock_in': 'display_stock_in', 'display_stock_out': 'display_stock_out',
    'store_transfer': 'store_transfers',
}
# At a live store these sections are stock lines rather than notes (owner, 2026-11-03), under the
# names report_stock moves them by. 出库 and 入娃娃机 stay notes: staff record them with 调拨
# Transfer and the claw screen.
_LIVE_LINE_SECTIONS = {'sell_display': 'display_sold', 'claw': 'claw_prize', 'display_stock_in': 'display_stock_in'}
_DISPLAY_NOTE = re.compile(r'\s*(?:display|展示)\s*', re.I)

# Fuzzy matches are suggestions only; unique exact identities can confirm.
_SCORE_REVIEW = 50

_DATE_RE = re.compile(r'(\d{4})[.\-\/年](\d{1,2})[.\-\/月](\d{1,2})')


def _preprocess_text(text: str) -> str:
    """Layer 1 — apply in strict order before any line splitting or parsing.

    Parentheticals are NOT stripped here anymore: they are extracted per-token
    in _parse_token so their content survives as the item note, and so an
    unclosed parenthesis can never swallow the following lines.
    """
    # 1. NFKC normalize (converts ＊→*, fullwidth chars incl. （）→(), etc.)
    text = unicodedata.normalize('NFKC', text)
    # 2. Normalize star separator: ∗ (U+2217) not handled by NFKC, strip spaces
    text = text.replace('∗', '*')
    text = re.sub(r'[^\S\n]*\*[^\S\n]*', '*', text)
    # 3. Per-line: strip leading/trailing whitespace, collapse internal spaces
    lines = [re.sub(r'  +', ' ', ln.strip()) for ln in text.splitlines()]
    text = '\n'.join(lines)
    # 4. Remove double commas
    text = text.replace(',,', ',').replace('，，', '，')
    return text



def _detect_section_type(line: str, section_aliases: dict) -> tuple[str, str] | None:
    """Headers need a delimiter or end-of-line; product quantities are not headers."""
    s = line.strip()
    for keyword, section in _SECTION_MAP:
        # Optional spaces allow "卖 display" without changing the item text.
        pattern = r'^(?:(?:今日|今天|[A-Za-z]{2,6})\s*)?' + r'\s*'.join(map(re.escape, keyword))
        match = re.match(pattern + r'(?:汇总)?(?:\s*[:：—-]\s*|\s*$)', s, re.I)
        if match:
            return section, s[match.end():].strip()
    line_norm = re.sub(r'\s+', '', s.rstrip(':：').lower())
    for alias_norm, stype in section_aliases.items():
        if alias_norm and alias_norm.rstrip(':：') == line_norm:
            return ('skip' if stype == 'ignore' else stype), ''
    if s.endswith((':', '：')) or re.fullmatch(_BARE_HEADINGS, s):
        return 'unknown', ''
    return None


# Headings staff write with no colon that POPCORE has no section for (MK, 2026-11-05: "入库" alone). Read as
# an item, its lines would join the section above it, so "入库" under 入店 put upstairs goods on the floor.
_BARE_HEADINGS = r'(?:入库|入仓库?)(?:汇总)?'


def _split_report_items(content: str) -> list[str]:
    """Use separators outside notes; spaces split only after a written quantity."""
    parts, start, depth = [], 0, 0
    for index, char in enumerate(content):
        if char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
        if depth != 0:
            continue
        separator = char in ',，;；'
        if char.isspace() and content[index + 1:].strip():
            head, _, balanced = _extract_note(content[start:index])
            rest = content[index + 1:].lstrip()
            separator = (balanced and rest[0] not in '(*+➕),，;；'
                         and bool(re.search(r'\*\d+(?:\s*[+➕]\s*\d+)?$', head)))
        if separator:
            parts.append(content[start:index].strip())
            start = index + 1
    parts.append(content[start:].strip())
    return [part for part in parts if part]


# Stores a report header names in Chinese: Markham's reports open "2026.11.2 太古汇总" (its lists say 太古店内, 太古仓库).
_STORE_WORDS = {'太古': 'MK'}


def _extract_date_store(first_line: str, fallback_store: str):
    """Parse date and store code from a report header line (best-effort)."""
    dm = _DATE_RE.search(first_line)
    detected_date = (
        f"{dm.group(1)}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}" if dm else None
    )
    suffix = r'(?:汇总|店|\s*$)' if dm else r'(?:汇总|店)'
    sm = re.search(r'(?<![A-Za-z])([A-Za-z]{2,6})' + suffix, first_line)
    named = next((code for word, code in _STORE_WORDS.items() if word in first_line), None)
    store = sm.group(1).upper() if sm else named or fallback_store
    return detected_date, store


def _extract_note(text: str) -> tuple[str, str, bool]:
    """Extract balanced, possibly nested notes without dropping trailing items."""
    body, notes, current = [], [], []
    depth, balanced = 0, True
    for char in text:
        if char == '(':
            if depth:
                current.append(char)
            depth += 1
        elif char == ')' and depth:
            depth -= 1
            if depth:
                current.append(char)
            else:
                notes.append(''.join(current).strip())
                current = []
        elif depth:
            current.append(char)
        else:
            body.append(char)
            if char == ')':
                balanced = False
    if current:
        notes.append(''.join(current).strip())
    return ''.join(body).strip(), '；'.join(filter(None, notes)), balanced and depth == 0


def _parse_token(token: str, section: str) -> dict | None:
    """Parse written units or receipt packs plus loose units; retain malformed input."""
    t, note, balanced = _extract_note(token.strip())
    if not t:
        # A standalone/unclosed annotation must remain visible for review.
        return {'raw_name': token.strip(), 'qty': 0, 'qty_pos': 0, 'qty_cash': 0,
                'box_size': None, 'loose_qty': 0, 'section': section,
                'flagged': True, 'note': note, 'unknown_header': None}
    if section == 'break_display':
        t = re.sub(r'^display\s*:\s*', '', t, flags=re.I)
    raw_name, star, qty_str = t.partition('*')
    raw_name = raw_name.strip()
    qty, flagged, loose_qty = 0, True, 0
    packed = section in ('stock_in', 'refill')
    box_size = 1 if packed else None
    out_pack = name_number = None
    if star:
        if section in ('stock_in', 'refill', 'stock_out'):
            pack = re.match(r'^(.*?)(\d+)$', raw_name)
            # "Smiski拍拍灯v2*1" is one v2 lamp, not one set of 2.
            if pack and pack.group(1).strip() and not re.search(
                r'(?<![A-Za-z])(?:s|v|ver|version|series)\s*$', pack.group(1), re.I
            ):
                raw_name = pack.group(1).strip().rstrip(':').strip()
                try:
                    size = read_int(pack.group(2), 'box_size', minimum=1)
                except ValueError:
                    size = None
                if packed:
                    box_size = size
                elif re.search(r'[㐀-鿿]$', pack.group(1)):
                    # "出店：最初的伙伴9*1" is one pack of 9; stock-out lines carry units only.
                    # Other trailing digits ("Alpha 12*2") may be part of the product name.
                    out_pack = size if size is not None else 0
                else:
                    raw_name = t.partition('*')[0].strip()
                    # Kept for the format check: it may have been a pack size after all.
                    name_number = size if size and size > 1 else None
        try:
            quantities = re.split(r'\s*[+➕]\s*', qty_str.strip()) if packed else [qty_str.strip()]
            if len(quantities) > 2:
                raise ValueError('Invalid quantity')
            qty = read_int(quantities[0], 'qty', minimum=1)
            loose_qty = read_int(quantities[1], 'loose_qty') if len(quantities) == 2 else 0
            read_int(qty * (box_size or 1) + loose_qty, 'total_units', minimum=1)
            if out_pack is not None:
                qty = read_int(qty * out_pack, 'qty', minimum=1)
                note = '；'.join(filter(None, [note, '原文: ' + token.strip()]))
            flagged = packed and box_size is None
        except ValueError:
            pass
    else:
        direct = re.fullmatch(r'(.+?)\s*:\s*(\d+)', t)
        if direct:
            raw_name = direct.group(1).strip()
            try:
                qty = read_int(direct.group(2), 'qty', minimum=1)
                flagged = False
            except ValueError:
                pass
    raw_name = raw_name.rstrip(':').strip()
    flagged = flagged or not balanced
    if flagged:
        note = '；'.join(filter(None, [note, '原文: ' + token.strip()]))
    return {
        'raw_name': raw_name, 'qty': qty,
        'qty_pos': qty if section != 'cash' else 0,
        'qty_cash': qty if section == 'cash' else 0,
        'box_size': box_size, 'loose_qty': loose_qty, 'section': section,
        'flagged': flagged, 'note': note, 'unknown_header': None,
        'inferred_split_name': None,
        **({'name_number': name_number} if name_number else {}),
    }


@bp.route('/api/sales/parse_report', methods=['POST'])
@role_required('manager')
def parse_daily_report():
    """
    Full five-layer daily report import pipeline.

    Body:  { text: <raw pasted text>, store_code: <str> }
    Returns confirmed unique exact matches, review suggestions, and unmatched items —
    plus detected_date, store, and any unknown section headers that need user classification.
    Nothing is written to daily_sales until the caller POSTs to /api/sales/submit_daily_report.
    """
    data       = request.get_json(silent=True) or {}
    if not isinstance(data, dict) or not isinstance(data.get('text'), str):
        return jsonify({'error': 'text is required', 'code': 'invalid_input'}), 400
    raw_text   = data.get('text', '')
    store_code = (data.get('store_code') or 'DT').strip().upper()
    # The day and store the manager opened, which the report's own header is checked against.
    page_date = data.get('date') if isinstance(data.get('date'), str) and _valid_day(data.get('date')) else None
    page_store = (data.get('store_code') or '').strip().upper() if isinstance(data.get('store_code'), str) else ''

    if not raw_text.strip():
        return jsonify({'error': 'text is required'}), 400

    # ── Layer 1: Pre-process ─────────────────────────────────────────────────
    text = _preprocess_text(raw_text)

    # ── Load products + aliases ──────────────────────────────────────────────
    con = get_db()
    cur = con.cursor()
    aliases = load_matching_aliases(con)
    report_picks = load_report_picks(con)
    cur.execute('SELECT alias_norm, section_type FROM section_aliases')
    section_aliases = {r['alias_norm']: r['section_type'] for r in cur.fetchall()}
    cur.execute('''
        SELECT id, sku, name_cn_en, jizhanming, price, ip_series, product_type
        FROM products
    ''')
    all_products = [dict(r) for r in cur.fetchall()]
    designs = [dict(r) for r in cur.execute('''
        SELECT d.id, d.design_name, s.name AS series_name, h.id AS head_id, h.jizhanming AS head_jzm, h.name_cn_en AS head_name
        FROM products d JOIN product_series s ON s.id=d.series_id LEFT JOIN products h ON h.id=s.product_id
        WHERE d.stock_form='confirmed_design' AND COALESCE(d.design_name, '') != ''
    ''')]

    # ── Engine selection: LLM front-end when configured, rules otherwise ─────
    # The LLM only reads the report (sections, names verbatim, quantities,
    # notes); product resolution below is identical for both engines. Any
    # LLM failure silently falls back to the rule parser.
    engine_req    = (data.get('engine') or '').strip().lower()
    parser_engine = 'rules'
    multi_day     = False
    llm_result    = None
    if engine_req == 'llm' and os.environ.get('ENABLE_LLM_PARSER') == '1':
        try:
            import llm_parser
            if llm_parser.available():
                llm_result = llm_parser.parse_report_llm(raw_text)
        except Exception:
            llm_result = None

    # Each line keeps its number in the paste, so a check can say where it is (owner, 2026-11-06).
    numbered = [(number, ln) for number, ln in enumerate(text.split('\n'), 1) if not re.fullmatch(r'[\s“”\"`]+', ln)]
    lines = [ln for _, ln in numbered]
    line_numbers = [number for number, _ in numbered]
    # A check quotes the line as written; the normalized line stands in if the paste splits differently.
    written = raw_text.splitlines()
    line_text = {number: (written[number - 1] if len(written) == len(text.split('\n')) else ln).strip()
                 for number, ln in numbered}

    # A report title can precede the date/store header. Prefer literal source facts.
    header_lines = [ln for ln in lines if re.fullmatch(
        _DATE_RE.pattern + r'日?\s*(?:[A-Za-z]{2,6}|' + '|'.join(_STORE_WORDS) + r')?\s*(?:汇总|店)?', ln.strip())]
    detected_date = None
    source_stores = {_extract_date_store(ln, '')[1] for ln in header_lines} - {''}
    detected_store = next(iter(source_stores)) if len(source_stores) == 1 else ''
    if header_lines:
        detected_date, _ = _extract_date_store(header_lines[0], '')
    store_code = detected_store or store_code
    # A live store's stock sections are stock lines; a legacy store keeps its old report stock paths.
    resolved_store = _resolve_store(con, store_code)
    live = bool(resolved_store) and _is_authoritative(con, resolved_store[0])
    annotations = ({key: field for key, field in _REPORT_ANNOTATIONS.items() if key not in _LIVE_LINE_SECTIONS}
                   if live else _REPORT_ANNOTATIONS)

    # Report-header/date lines ("2026.04.01 DT汇总") are metadata, not products —
    # skip them without disturbing the current section.
    skipped_lines = {
        i for i, line in enumerate(lines)
        if line in header_lines
    }
    # Multiple distinct dates in one paste → warn (submission records ONE date)
    distinct_dates = {
        f'{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}'
        for i in skipped_lines
        for m in [_DATE_RE.search(lines[i])] if m
    }
    multi_day = len(distinct_dates) > 1

    # ── Layer 2: Scan section boundaries ────────────────────────────────────
    # Boundary = (line_index, section_type, header_text, inline_content)
    boundaries: list[tuple[int, str, str, str]] = []
    for i, line in enumerate(lines):
        s = line.strip()
        if not s or i in skipped_lines:
            continue
        det = _detect_section_type(s, section_aliases)
        if det is not None:
            # A bare "入库" is answered as the heading "入库:", the form a section choice is saved under.
            heading = s + ':' if det[0] == 'unknown' and not s.endswith((':', '：')) else s
            boundaries.append((i, det[0], heading, det[1]))

    boundary_info = {bi: (bsec, bhdr, binline) for bi, bsec, bhdr, binline in boundaries}

    def section_at(idx: int) -> tuple[str, str]:
        # Default before first detected header → CASH_SALES (spec §Layer 2)
        cur_sec, cur_hdr = 'cash', ''
        for bi, bsec, bhdr, _ in boundaries:
            if bi <= idx:
                cur_sec, cur_hdr = bsec, bhdr
            else:
                break
        return cur_sec, cur_hdr

    # ── Layer 3: Parse lines ──────────────────────────────────────────────────
    raw_items:           list[dict] = []
    unknown_sections:    list[str] = []
    cash_total_reported: float | None = None
    cash_expected_reported: float | None = None
    report_notes = {key: [] for key in _REPORT_NOTE_FIELDS}
    metadata_errors: list[str] = []
    if len(source_stores) > 1:
        metadata_errors.append('Multiple stores detected; import each store separately')

    def _capture_cash_total(text_piece: str) -> None:
        nonlocal cash_total_reported, cash_expected_reported
        if not text_piece.strip():
            return
        parts = text_piece.strip().lstrip('$').split('/')
        try:
            if len(parts) > 2 or cash_total_reported is not None:
                raise ValueError('Cash must contain one actual/expected amount pair')
            actual = _report_money_cents(parts[0].strip(), 'cash_actual')
            expected = _report_money_cents(parts[1].strip().lstrip('$'), 'cash_expected') if len(parts) == 2 else None
            cash_total_reported = actual / 100
            cash_expected_reported = expected / 100 if expected is not None else None
        except ValueError:
            metadata_errors.append('Cannot read cash actual/expected: ' + text_piece)

    def _parse_content(content: str, sec: str, hdr: str | None = None) -> None:
        if sec in annotations:
            if not _extract_note(content)[2]:
                metadata_errors.append('Unclosed or unmatched note: ' + content)
            report_notes[annotations[sec]].append(content)
            return
        if live:
            sec = _LIVE_LINE_SECTIONS.get(sec, sec)
        elif sec == 'refill':
            # 出库入店 is upstairs → floor, which a legacy store's 入店 already records.
            sec = 'stock_in'
        discount_index = None
        for token in _split_report_items(content):
            item_text, annotation, balanced = _extract_note(token)
            if not balanced:
                metadata_errors.append('Unclosed or unmatched note: ' + token)
            elif item_text.count('*') > 1:
                metadata_errors.append('Cannot separate item quantities: ' + token)
            if re.search(r'员工折扣(?:购买|购入)', token):
                discount_index = len(report_notes['employee_discounts'])
                report_notes['employee_discounts'].append(token)
                continue
            if discount_index is not None and '*' not in token and re.match(
                r'^(?:EMT|cash|card|微信|支付宝|现金|卡机|本月|今月|剩余)', token, re.I
            ):
                report_notes['employee_discounts'][discount_index] += ', ' + token
                continue
            discount_index = None
            if live and balanced and sec in ('refill', 'stock_in', 'stock_out') and re.search(
                    r'\bdisplay\b|展示', annotation, re.I):
                _live_display_line(token, sec)
                continue
            if balanced and sec in ('stock_in', 'stock_out') and re.search(r'\bdisplay\b|展示', annotation, re.I):
                mixed = re.fullmatch(
                    r'(.+?)\s*:\s*(\d+)\s*[+➕]\s*\((?:display|展示)\)\s*\*(\d+)', token, re.I
                )
                if sec == 'stock_in' and mixed:
                    try:
                        sealed_qty = read_int(mixed.group(2), 'sealed_qty', minimum=1)
                        read_int(mixed.group(3), 'display_qty', minimum=1)
                    except ValueError:
                        pass
                    else:
                        sealed = _parse_token(f'{mixed.group(1)}:{sealed_qty}', sec)
                        sealed['note'] = '原文: ' + token
                        raw_items.append(sealed)
                        report_notes['display_stock_in'].append(token)
                        continue
                # "芭比*11+1(display)": 11 received into stock plus 1 opened for display.
                trailing = re.fullmatch(
                    r'(.+?)\s*\*\s*(\d+)\s*[+➕]\s*(\d+)\s*\((?:display|展示)\)', token, re.I
                )
                if sec == 'stock_in' and trailing:
                    try:
                        read_int(trailing.group(3), 'display_qty', minimum=1)
                    except ValueError:
                        pass
                    else:
                        sealed = _parse_token(f'{trailing.group(1)}*{trailing.group(2)}', sec)
                        if not sealed['flagged']:
                            sealed['note'] = '原文: ' + token
                            raw_items.append(sealed)
                            report_notes['display_stock_in'].append(token)
                            continue
                item = _parse_token(token, sec)
                if item['flagged'] or re.search(r'[+➕]|^[^*]*\d\s*\(', token):
                    # Unsupported mixed arithmetic must not disappear into notes.
                    item['flagged'] = True
                    item['note'] = '原文: ' + token
                    raw_items.append(item)
                    metadata_errors.append('Cannot read display stock quantities: ' + token)
                else:
                    report_notes['display_' + sec].append(token)
                continue
            if sec == 'cash' and re.fullmatch(r'娃娃机\s*\*\d+(?:\s*\([^()]*\))?', token):
                report_notes['cash_exchanges'].append(token)
                continue
            item = _parse_token(token, sec)
            if item:
                if hdr is not None:
                    item['unknown_header'] = hdr
                raw_items.append(item)
                notes = (item['note'] or '').split('；')
                if live and sec in ('pos', 'cash') and hdr is None and any(map(_DISPLAY_NOTE.fullmatch, notes)):
                    # "labubu喵喵毛绒*1（display）" among the sales (MK, 2026-11-02): this sale came off display,
                    # as a 卖display line says.
                    item['note'] = '；'.join(note for note in notes if not _DISPLAY_NOTE.fullmatch(note))
                    raw_items.append({**item, 'section': 'display_sold', 'qty_pos': item['qty'], 'qty_cash': 0})

    def _live_display_line(token: str, sec: str) -> None:
        """A (display) line at a live store: 出库入店/入店 put it on display, 出店 takes it off display.
        "芭比*11+1(display)" and "芭比:11+(display)*1" are 11 to the floor plus 1 to display."""
        shown = 'from_display' if sec == 'stock_out' else 'to_display'
        trailing = re.fullmatch(r'(.+?)\s*\*\s*(\d+)\s*[+➕]\s*(\d+)\s*\((?:display|展示)\)', token, re.I)
        mixed = re.fullmatch(r'(.+?)\s*:\s*(\d+)\s*[+➕]\s*\((?:display|展示)\)\s*\*(\d+)', token, re.I)
        split = trailing or mixed
        if split and sec != 'stock_out':
            parts = [(f'{split.group(1)}*{split.group(2)}' if trailing else f'{split.group(1)}:{split.group(2)}', False),
                     (f'{_extract_note(split.group(1))[0]}:{split.group(3)}', True)]
        else:
            parts = [(token, True)]
        for text, on_display in parts:
            item = _parse_token(text, sec)
            notes = [part for part in (item['note'] or '').split('；') if not _DISPLAY_NOTE.fullmatch(part)]
            item['note'] = '；'.join(notes)
            if split:
                item['note'] = '；'.join(filter(None, [item['note'], '原文: ' + token]))
            item[shown] = on_display
            raw_items.append(item)

    def _read_line(i: int, s: str) -> None:
        if i in boundary_info:
            bsec, bhdr, binline = boundary_info[i]
            if bsec == 'unknown' and bhdr not in unknown_sections:
                unknown_sections.append(bhdr)
            if bsec == 'cash_total':
                _capture_cash_total(binline)
            elif binline:
                # Inline content after the header ("出店：sa糖果*1") is real data
                if bsec == 'unknown':
                    _parse_content(binline, 'pos', hdr=bhdr)
                elif bsec != 'skip':
                    _parse_content(binline, bsec)
            return

        sec, hdr = section_at(i)

        if sec == 'skip':
            return
        if sec == 'cash_total':
            _capture_cash_total(s)
            return
        if sec == 'unknown':
            # Parse tentatively; mark as unknown_header so frontend can reclassify
            _parse_content(s, 'pos', hdr=hdr)
            return

        # Normal section: split on comma, parse each token individually
        _parse_content(s, sec)

    for i, line in enumerate(lines):
        s = line.strip()
        if not s or i in skipped_lines:
            continue
        items_before, errors_before = len(raw_items), len(metadata_errors)
        _read_line(i, s)
        # Everything this line produced names its line in the paste.
        for item in raw_items[items_before:]:
            item['line'] = line_numbers[i]
        metadata_errors[errors_before:] = [f'第{line_numbers[i]}行 Line {line_numbers[i]}: {error}'
                                           for error in metadata_errors[errors_before:]]

    # Format checks (owner, 2026-11-06): anything written so that it may move stock wrongly, by its line.
    header_at = next((line_numbers[i] for i, ln in enumerate(lines) if ln in header_lines), None)
    first_line = next(((number, ln.strip()) for number, ln in numbered if ln.strip()), None)
    format_checks = report_checks.header_checks(header_at, detected_date, detected_store, page_date, page_store,
                                                first_line=first_line)
    format_checks += report_checks.text_checks(
        [(line_numbers[bi], bsec, bhdr) for bi, bsec, bhdr, _ in boundaries],
        [item for item in raw_items if not item.get('unknown_header')],
        [item for item in raw_items if item.get('unknown_header')],
        handled={line for check in format_checks for line in check['lines']})

    if llm_result:
        parser_engine       = 'llm'
        detected_date       = detected_date or llm_result.get('date')
        llm_store = (llm_result.get('store') or '').upper()
        if detected_store and llm_store and detected_store != llm_store:
            metadata_errors.append('Store detections disagree; correct the report header')
        store_code = detected_store or llm_store or store_code
        multi_day           = multi_day or bool(llm_result.get('extra_dates'))

        rule_items = raw_items
        raw_items = []
        # Preserve unknown headers detected in the source even if the LLM omits them.
        for li in llm_result['items']:
            sec = li['section']
            if sec == 'skip' or sec in _REPORT_ANNOTATIONS:
                continue
            hdr = li.get('header_text') or ''
            if sec == 'unknown' and hdr and hdr not in unknown_sections:
                unknown_sections.append(hdr)
            q       = li.get('qty')
            try:
                qty = read_int(q, 'qty', minimum=1)
                flagged = False
            except ValueError:
                qty, flagged = 0, True
            raw_items.append({
                'raw_name':            li['name'],
                'qty':                 qty,
                'qty_pos':             qty if sec != 'cash' else 0,
                'qty_cash':            qty if sec == 'cash' else 0,
                'box_size':            li.get('box_size'),
                'section':             'pos' if sec == 'unknown' else sec,
                'flagged':             flagged,
                'note':                li.get('note') or '',
                'unknown_header':      hdr if sec == 'unknown' else None,
                'inferred_split_name': None,
            })

        def source_signature(items):
            return Counter((item['section'], unicodedata.normalize('NFKC', item['raw_name']).strip().casefold(),
                            item['qty'], item.get('box_size') or 1, item.get('loose_qty', 0),
                            item.get('note') or '', bool(item.get('flagged')))
                           for item in items)

        if source_signature(raw_items) != source_signature(rule_items):
            raw_items = rule_items
            parser_engine = 'rules'

    return _finish_parse(detected_date, store_code, raw_items, unknown_sections,
                         cash_total_reported, parser_engine, multi_day,
                         aliases, all_products, cash_expected_reported, metadata_errors, report_notes,
                         report_picks, live, designs, resolved_store[0] if resolved_store else None,
                         format_checks=format_checks, line_text=line_text)


_GENERIC_WORDS = {'series', 'figure', 'figures', 'figurine', 'mini', 'blind', 'box', 'plush', 'vinyl', 'doll',
                  'the', 'and', 'collection'}


def _design_key(text: str) -> str:
    return re.sub(r'[\W_]+', '', _norm_jzm(_clean_jzm(text or '')))


def _dash_design(raw_name: str, designs: list) -> int | None:
    """The owner's rule (2026-11-01): a line written "X - name" is the design `name` of series X, as in
    "SA Easter Series 2017 - Easter Lamb" or "小野clot figure-Silk Royale". Returns that design's id when
    the series and the design name written point to one design; a year or number written in X must be
    in the series' name ("2015" is never the 2016 series)."""
    head, dash, written = raw_name.rpartition('-')
    wanted = _design_key(written)
    if not dash or not _design_key(head) or len(wanted) < 2:
        return None
    numbers = re.findall(r'\d{2,}', head)
    pool = [d for d in designs if all(
        n in ' '.join(filter(None, (d['series_name'], d['head_jzm'], d['head_name']))) for n in numbers)]
    named = [d for d in pool if _design_key(d['design_name']) == wanted] or [
        d for d in pool if _score_pair_jzm(wanted, _design_key(d['design_name'])) >= 70]
    series = _norm_jzm(_clean_jzm(head))
    # "小野clot figure" names the Clot series by its own word even where the fuzzy score cannot see it.
    words = set(re.findall(r'[a-z]{3,}', head.lower())) - _GENERIC_WORDS

    def fits(design):
        names = [name for name in (design['series_name'], design['head_jzm'], design['head_name']) if name]
        score = max(_score_pair_jzm(series, _norm_jzm(_clean_jzm(name))) for name in names)
        shared = words & set(re.findall(r'[a-z]{3,}', ' '.join(names).lower()))
        return max(score, _SCORE_REVIEW if shared else 0)

    scored = sorted(((fits(d), d['id']) for d in named), reverse=True)
    scored = [(score, design_id) for score, design_id in scored if score >= _SCORE_REVIEW]
    if not scored or (len(scored) > 1 and scored[0][0] == scored[1][0]):
        return None
    return scored[0][1]


def _secret_design(raw_name: str, designs: list, all_products: list, aliases: dict) -> int | None:
    """"马卡龙隐藏" is the secret design of the series 马卡龙 names exactly, when that series has only one."""
    written = re.fullmatch(r'(.+?)\s*(?:隐藏款?|secret)', raw_name.strip(), re.I)
    if not written:
        return None
    heads = [product for score, product in match_jzm(written.group(1), all_products, aliases, threshold=100)]
    if len(heads) != 1:
        return None
    secrets = [d['id'] for d in designs
               if d['head_id'] == heads[0]['id'] and re.search(r'secret|隐藏', d['design_name'], re.I)]
    return secrets[0] if len(secrets) == 1 else None


def _valid_day(value) -> bool:
    try:
        return read_date(value) == value
    except ValueError:
        return False


def _finish_parse(detected_date, store_code, raw_items, unknown_sections,
                  cash_total_reported, parser_engine, multi_day,
                  aliases, all_products, cash_expected_reported=None,
                  metadata_errors=None, report_notes=None, report_picks=None, live=False, designs=None,
                  store_id=None, format_checks=None, line_text=None):
    """Layers 4+5 — shared by both engines: alias lookup + fuzzy match +
    bucketing, stock plausibility flags, and the response payload."""
    confirmed: list[dict] = []
    review:    list[dict] = []
    failed:    list[dict] = []
    picks = report_picks or {}
    reviewed_names = {key[0] for key in picks}
    products_by_id = {product['id']: product for product in all_products}
    store_codes = {row['id']: row['code'] for row in get_db().execute('SELECT id, code FROM stores')} if picks else {}
    names = []  # (key, row) for 名称记忆: the names this paste did not confirm by an exact catalog name

    def _bucket(item: dict, hits: list, confirm: bool | None = None) -> dict:
        """Place a resolved item into the correct bucket. A remembered pick decides `confirm` itself."""
        if not hits:
            failed.append({**item, 'reason': 'no_match', 'score': 0, 'candidates': []})
            return failed[-1]
        top_score, top_product = hits[0]
        runner_up  = hits[1][0] if len(hits) > 1 else 0
        unique_exact = top_score == 100 and runner_up < 100
        warn_blank = not (top_product.get('jizhanming') or '').strip()
        if confirm is None:
            confirm = unique_exact and item.get('section') in ('pos', 'cash')
        if confirm and not item.get('flagged') and parser_engine == 'rules':
            confirmed.append({
                **item,
                'score':          top_score,
                'product':        top_product,
                'warn_blank_jzm': warn_blank,
            })
            return confirmed[-1]
        review.append({
            **item,
            'score':          top_score,
            'product':        top_product,
            'candidates':     [{'score': s, **p} for s, p in hits],
            'warn_blank_jzm': warn_blank,
        })
        return review[-1]

    for item in raw_items:
        raw_name = item.get('raw_name', '')

        # Items from unknown sections go directly to failed — user must classify section first
        if item.get('unknown_header'):
            failed.append({**item, 'reason': 'unknown_section', 'score': 0, 'candidates': []})
            continue

        if not raw_name:
            failed.append({**item, 'reason': 'empty_name', 'score': 0, 'candidates': []})
            continue

        # Secret/hidden variants in notes are part of product identity.
        qualifier = item.get('note', '')
        query = report_match_query(raw_name, qualifier)
        key = report_match_key(raw_name, qualifier)
        hits = match_jzm(query, all_products, aliases, threshold=_SCORE_REVIEW, limit=5)
        if key[0] in reviewed_names or query != raw_name:
            # A name reviewed with another note, or a changed note, must be reviewed again.
            hits = [(min(score, 99), product) for score, product in hits]
        design = (_dash_design(raw_name, designs or [])
                  or _secret_design(raw_name, designs or [], all_products, aliases))
        if design in products_by_id and (not hits or hits[0][1]['id'] != design):
            # Suggested first, never confirmed by itself.
            hits = [(99, products_by_id[design])] + [hit for hit in hits if hit[1]['id'] != design]
        pick = picks.get(key)
        chosen = [pid for pid in (pick or {}).get('product_ids', []) if pid in products_by_id]
        if chosen:
            # 名称记忆 (owner, 2026-11-03): the remembered product leads. It confirms the line only where a
            # manager confirmed it (this store, this section, display or not); anywhere else, or once the
            # product changed, the paste asks again with it picked.
            state = name_status(pick, store_id, place_of(item), False)
            hits = [(100 if state in ('remembered', 'confirm') else 99, products_by_id[pid]) for pid in chosen] + [
                (min(score, 99), product) for score, product in hits if product['id'] not in chosen]
            item = {**item, 'pick': {'state': state, 'product_ids': chosen, 'places': [
                {'store_code': store_codes[sid], 'section': section, 'display': display}
                for sid, section, display in sorted(pick['situations']) if sid in store_codes]}}
            names.append((key, _bucket(item, hits, confirm=state == 'remembered')))
        else:
            row = _bucket(item, hits)
            if row is not (confirmed[-1] if confirmed else None):
                names.append((key, row))

    if store_id is not None and not multi_day and _valid_day(detected_date):
        # The day's latest paste replaces its names on the Name picks page (a preview saves nothing else).
        con4 = get_db()
        try:
            con4.execute('BEGIN IMMEDIATE')
            log_names(con4, store_id, detected_date, names, request.jwt_payload.get('sub'))
            con4.commit()
        except Exception:
            con4.rollback()
            raise

    # ── Stock plausibility: flag sales rows that exceed known in-store stock ──
    # Only products with an existing stock row are checked — absence of a row
    # means "stock untracked", not "zero on shelf".
    check_items = [it for it in confirmed + review
                   if it.get('section') in SALES_SECTIONS and it.get('product')]
    if check_items:
        con2 = get_db()
        resolved = _resolve_store(con2, store_code)
        if resolved:
            sid = resolved[0]
            pids = {it['product']['id'] for it in check_items}
            qmarks = ','.join('?' * len(pids))
            rows2 = con2.execute(
                f'SELECT product_id, instore_qty FROM stock '
                f'WHERE store_id = ? AND product_id IN ({qmarks})',
                (sid, *pids)).fetchall()
            instore = {r['product_id']: r['instore_qty'] for r in rows2}
            for it in check_items:
                pid_c = it['product']['id']
                if pid_c in instore and (it.get('qty') or 0) > instore[pid_c]:
                    it['warn_stock'] = {'instore': instore[pid_c]}

    # A live store's stock lines, by the products this paste matched.
    known = report_checks.product_checks(get_db(), confirmed + review, failed) if live else []
    # Where a product check points at the same lines as a text check, it says it better.
    same = {tuple(check['lines']) for check in known}
    checks = [check for check in format_checks or [] if tuple(check['lines']) not in same] + known
    for check in checks:
        check['text'] = [(line_text or {}).get(number, '') for number in check['lines']]
    checks.sort(key=lambda check: (check['lines'] or [0])[0])

    return jsonify({
        'detected_date':       detected_date,
        'store':               store_code,
        'confirmed':           confirmed,
        'review':              review,
        'failed':              failed,
        'unknown_sections':    unknown_sections,
        'cash_total_reported': cash_total_reported,
        'cash_expected_reported': cash_expected_reported,
        **(report_notes or {key: [] for key in _REPORT_NOTE_FIELDS}),
        'metadata_errors': metadata_errors or [],
        # What may be written wrong, by line: 'fix' most likely is, 'check' may be meant. Never blocks the save.
        'format_checks':       checks,
        'parser_engine':       parser_engine,
        'multi_day':           multi_day,
        # Which stock rules the lines were read by; submitting them to a store in the other mode is refused.
        'stock_mode':          'live' if live else 'legacy',
    })
