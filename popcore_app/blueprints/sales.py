"""
blueprints/sales.py — daily sales records, batch import, daily report, export.
"""
import os
import json
from collections import Counter
from decimal import Decimal
import re
import unicodedata
from datetime import date, timedelta
from flask import Blueprint, request, jsonify, Response

from db import get_db, esc_csv, _ensure_stock_row
from auth import login_required, role_required
from validation import SQLITE_INTEGER_MAX, invalid_input, read_date, read_int
from blueprints.stores import _resolve_store
from matcher import match_jzm, identity_conflicts, load_matching_aliases, normalize as _norm_jzm, clean_name as _clean_jzm
from inventory_commands import InventoryError, post_inventory
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
@login_required
def get_sales():
    d = request.args.get('date', str(date.today()))
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
                   COALESCE(ds.unit_price, p.price) AS price, p.ip_series
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
                   COALESCE(ds.unit_price, p.price) AS price, p.ip_series
            FROM daily_sales ds
            JOIN products p ON p.id = ds.product_id
            WHERE ds.date = ? AND ds.store = ?
            ORDER BY p.ip_series, p.jizhanming
            LIMIT 500
        ''', (d, store_code))
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return jsonify(rows)


@bp.route('/api/sales/upsert', methods=['POST'])
@role_required('staff')
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
        d = read_date(data.get('date', str(date.today())))
    except ValueError as exc:
        return jsonify(invalid_input(exc)), 400
    notes    = data.get('notes', '')
    qty_sold = qty_pos + qty_cash

    con = get_db()
    store_id, store_code, err = _require_store_body(con, data)
    if err:
        con.close()
        return err
    cur = con.cursor()
    if cur.execute('SELECT 1 FROM products WHERE id = ?', (pid,)).fetchone() is None:
        con.close()
        return jsonify({'error': 'Product not found', 'code': 'not_found',
                        'field': 'product_id'}), 404
    cur.execute('''
        INSERT INTO daily_sales (product_id, date, store, qty_pos, qty_cash, qty_sold, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(product_id, date, store) DO UPDATE SET
            qty_pos  = excluded.qty_pos,
            qty_cash = excluded.qty_cash,
            qty_sold = excluded.qty_sold,
            notes    = excluded.notes
    ''', (pid, d, store_code, qty_pos, qty_cash, qty_sold, notes))
    con.commit()
    con.close()
    return jsonify({'ok': True})


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
    d = data.get('date', str(date.today()))

    con = get_db()
    store_id, store_code, err = _require_store_body(con, data)
    if err:
        con.close()
        return err
    cur = con.cursor()
    cur.execute('''
        INSERT OR IGNORE INTO daily_sales (product_id, date, store, qty_sold)
        VALUES (?, ?, ?, 0)
    ''', (pid, d, store_code))
    con.commit()
    con.close()
    return jsonify({'ok': True})


@bp.route('/api/sales/summary')
@login_required
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
    con.commit()
    con.close()
    return jsonify({'ok': True})


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
        d = read_date(data.get('date', str(date.today())))
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

    if _is_authoritative(con):
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
@role_required('staff')
def batch_upsert_sales():
    data = request.get_json()
    if not isinstance(data, dict):
        return jsonify({'error': 'Expected an object with store_code and items'}), 400

    con = get_db()
    store_id, store_code, err = _require_store_body(con, data)
    if err:
        con.close()
        return err

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
            item_date = read_date(item.get('date', str(date.today())))
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
    cur.executemany('''
        INSERT INTO daily_sales (product_id, date, store, qty_pos, qty_cash, qty_sold, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(product_id, date, store) DO UPDATE SET
            qty_pos  = excluded.qty_pos,
            qty_cash = excluded.qty_cash,
            qty_sold = excluded.qty_sold,
            notes    = CASE WHEN excluded.notes != '' THEN excluded.notes ELSE notes END
    ''', rows)
    con.commit()
    con.close()
    return jsonify({'ok': True, 'count': len(rows)})


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
                       'cash_exchanges', 'claw_stock_in', 'display_stock_in', 'display_stock_out')


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
@role_required('staff')
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

    validated_items = []
    valid_sections = (SALES_SECTIONS - {'employee_discount', 'sell_display', 'claw'}) | {'break_display', 'stock_in', 'stock_out'}
    for line, item in enumerate(items, 1):
        if not isinstance(item, dict):
            return jsonify({'error': 'Each item must be an object',
                            'code': 'invalid_input', 'line': line}), 400
        try:
            if item.get('flagged') or item.get('unknown_header'):
                raise ValueError('Resolve every report item before submitting')
            normalized = dict(item)
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
            elif normalized['section'] in {'break_display', 'stock_out'}:
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

    if _is_authoritative(con) and any(
        item['section'] in {'break_display', 'stock_in', 'stock_out'}
        for item in validated_items
    ):
        con.close()
        return jsonify({
            'error': 'Legacy report stock sections require reviewed document intake',
            'code': 'migration_required',
        }), 409

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
        for item in validated_items:
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
            norm_name_c = _norm_jzm(_clean_jzm(raw_name_c))
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

        # Teach only a reviewed, unambiguous name, inside the successful submission.
        # Conflicting review history revokes our learned alias instead of picking a winner.
        if corrections:
            catalog = [dict(row) for row in cur.execute('SELECT * FROM products')]
            products_by_id = {p['id']: p for p in catalog}
            for raw_name_c, norm_name_c, pid_c, *_ in corrections:
                owners = {row[0] for row in cur.execute(
                    'SELECT DISTINCT product_id FROM match_corrections WHERE norm_name=?', (norm_name_c,))}
                owners.update(item['product_id'] for item in validated_items
                              if _norm_jzm(_clean_jzm(item.get('raw_name') or '')) == norm_name_c)
                if len(owners) != 1:
                    cur.execute("DELETE FROM product_aliases WHERE alias_norm=? AND created_by='report-review'",
                                (norm_name_c,))
                    continue
                if not norm_name_c or identity_conflicts(raw_name_c, products_by_id[pid_c]):
                    continue
                if any(item.get('notes') for item in validated_items
                       if _norm_jzm(_clean_jzm(item.get('raw_name') or '')) == norm_name_c):
                    continue  # A character/variant note must not teach the unqualified name.
                if any(p['id'] != pid_c for _, p in match_jzm(raw_name_c, catalog, threshold=100)):
                    continue
                cur.execute("""INSERT OR IGNORE INTO product_aliases
                    (product_id, alias, alias_norm, created_by) VALUES (?, ?, ?, 'report-review')""",
                    (pid_c, raw_name_c, norm_name_c))

        if mode == 'replace':
            cur.execute('DELETE FROM daily_report_metadata WHERE date=? AND store=?', (d, store_code))
        if metadata is not None:
            cur.execute(f"""INSERT INTO daily_report_metadata
                (date, store, cash_actual_cents, cash_expected_cents, {','.join(_REPORT_NOTE_FIELDS)})
                VALUES ({','.join('?' for _ in range(4 + len(_REPORT_NOTE_FIELDS)))})""",
                (d, store_code, cash_actual, cash_expected,
                 *(annotation_lists[key] for key in _REPORT_NOTE_FIELDS)))
        con.commit()
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
    })


@bp.route('/api/sales/export')
@role_required('manager')
def export_sales():
    from_date = request.args.get('from', '')
    to_date   = request.args.get('to',   str(date.today()))
    if not from_date:
        from_date = str(date.today() - timedelta(days=30))

    con = get_db()
    store_id, store_code, err = _require_store_param(con)
    if err:
        con.close()
        return err
    cur = con.cursor()
    cur.execute('''
        SELECT ds.date, p.jizhanming, p.sku, p.ip_series, p.product_type,
               COALESCE(ds.unit_price, p.price) AS price,
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
    con.commit()
    con.close()
    return jsonify({'ok': True, 'deleted': deleted})


@bp.route('/api/sales/recorded-dates')
@login_required
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
    ('卡机汇总', 'pos'), ('随手记汇总', 'cash'), ('随手机汇总', 'cash'),
    ('随手记', 'cash'), ('卡机', 'pos'),
    ('入店display', 'display_stock_in'), ('出店display', 'display_stock_out'),
    ('入display', 'display_stock_in'), ('出display', 'display_stock_out'),
    ('入娃娃机', 'claw_stock_in'), ('娃娃机入', 'claw_stock_in'),
    ('娃娃机出', 'claw'), ('出娃娃机', 'claw'),
    ('入店', 'stock_in'), ('出店', 'stock_out'),
    ('卖display', 'sell_display'), ('卖展示', 'sell_display'),
    ('拆display', 'break_display'), ('拆展示', 'break_display'),
    ('娃娃机', 'claw'), ('员工折扣', 'employee_discount'),
    ('晚盘', 'skip'), ('博主探店', 'skip'), ('现金', 'cash_total'),
]
_REPORT_ANNOTATIONS = {
    'employee_discount': 'employee_discounts', 'sell_display': 'display_sales',
    'claw': 'claw_prizes', 'claw_stock_in': 'claw_stock_in',
    'display_stock_in': 'display_stock_in', 'display_stock_out': 'display_stock_out',
}

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
    if s.endswith((':', '：')):
        return 'unknown', ''
    return None


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


def _extract_date_store(first_line: str, fallback_store: str):
    """Parse date and store code from a report header line (best-effort)."""
    dm = _DATE_RE.search(first_line)
    detected_date = (
        f"{dm.group(1)}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}" if dm else None
    )
    suffix = r'(?:汇总|店|\s*$)' if dm else r'(?:汇总|店)'
    sm = re.search(r'(?<![A-Za-z])([A-Za-z]{2,6})' + suffix, first_line)
    store = sm.group(1).upper() if sm else fallback_store
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
    box_size = 1 if section == 'stock_in' else None
    if star:
        if section == 'stock_in':
            pack = re.match(r'^(.*?)(\d+)$', raw_name)
            if pack and pack.group(1).strip() and not re.search(
                r'(?<![A-Za-z])(?:s|ver|version|series)\s*$', pack.group(1), re.I
            ):
                raw_name = pack.group(1).strip().rstrip(':').strip()
                try:
                    box_size = read_int(pack.group(2), 'box_size', minimum=1)
                except ValueError:
                    box_size = None
        try:
            quantities = re.split(r'\s*[+➕]\s*', qty_str.strip()) if section == 'stock_in' else [qty_str.strip()]
            if len(quantities) > 2:
                raise ValueError('Invalid quantity')
            qty = read_int(quantities[0], 'qty', minimum=1)
            loose_qty = read_int(quantities[1], 'loose_qty') if len(quantities) == 2 else 0
            read_int(qty * (box_size or 1) + loose_qty, 'total_units', minimum=1)
            flagged = section == 'stock_in' and box_size is None
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
    }


@bp.route('/api/sales/parse_report', methods=['POST'])
@role_required('staff')
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

    if not raw_text.strip():
        return jsonify({'error': 'text is required'}), 400

    # ── Layer 1: Pre-process ─────────────────────────────────────────────────
    text = _preprocess_text(raw_text)

    # ── Load products + aliases ──────────────────────────────────────────────
    con = get_db()
    cur = con.cursor()
    aliases = load_matching_aliases(con)
    cur.execute('SELECT alias_norm, section_type FROM section_aliases')
    section_aliases = {r['alias_norm']: r['section_type'] for r in cur.fetchall()}
    cur.execute('''
        SELECT id, sku, name_cn_en, jizhanming, price, ip_series, product_type
        FROM products
    ''')
    all_products = [dict(r) for r in cur.fetchall()]

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

    lines = [ln for ln in text.split('\n') if not re.fullmatch(r'[\s“”\"`]+', ln)]

    # A report title can precede the date/store header. Prefer literal source facts.
    header_lines = [ln for ln in lines if re.fullmatch(
        _DATE_RE.pattern + r'日?\s*(?:[A-Za-z]{2,6})?\s*(?:汇总|店)?', ln.strip())]
    detected_date = None
    source_stores = {_extract_date_store(ln, '')[1] for ln in header_lines} - {''}
    detected_store = next(iter(source_stores)) if len(source_stores) == 1 else ''
    if header_lines:
        detected_date, _ = _extract_date_store(header_lines[0], '')
    store_code = detected_store or store_code

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
            boundaries.append((i, det[0], s, det[1]))

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
        if sec in _REPORT_ANNOTATIONS:
            if not _extract_note(content)[2]:
                metadata_errors.append('Unclosed or unmatched note: ' + content)
            report_notes[_REPORT_ANNOTATIONS[sec]].append(content)
            return
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

    for i, line in enumerate(lines):
        s = line.strip()
        if not s or i in skipped_lines:
            continue

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
            continue

        sec, hdr = section_at(i)

        if sec == 'skip':
            continue
        if sec == 'cash_total':
            _capture_cash_total(s)
            continue
        if sec == 'unknown':
            # Parse tentatively; mark as unknown_header so frontend can reclassify
            _parse_content(s, 'pos', hdr=hdr)
            continue

        # Normal section: split on comma, parse each token individually
        _parse_content(s, sec)

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
                         aliases, all_products, cash_expected_reported, metadata_errors, report_notes)


def _finish_parse(detected_date, store_code, raw_items, unknown_sections,
                  cash_total_reported, parser_engine, multi_day,
                  aliases, all_products, cash_expected_reported=None,
                  metadata_errors=None, report_notes=None):
    """Layers 4+5 — shared by both engines: alias lookup + fuzzy match +
    bucketing, stock plausibility flags, and the response payload."""
    confirmed: list[dict] = []
    review:    list[dict] = []
    failed:    list[dict] = []

    def _bucket(item: dict, hits: list) -> None:
        """Place a resolved item into the correct bucket."""
        if not hits:
            failed.append({**item, 'reason': 'no_match', 'score': 0, 'candidates': []})
            return
        top_score, top_product = hits[0]
        runner_up  = hits[1][0] if len(hits) > 1 else 0
        unique_exact = top_score == 100 and runner_up < 100
        warn_blank = not (top_product.get('jizhanming') or '').strip()
        if (unique_exact and not item.get('flagged')
                and item.get('section') in ('pos', 'cash') and parser_engine == 'rules'):
            confirmed.append({
                **item,
                'score':          top_score,
                'product':        top_product,
                'warn_blank_jzm': warn_blank,
            })
        else:
            review.append({
                **item,
                'score':          top_score,
                'product':        top_product,
                'candidates':     [{'score': s, **p} for s, p in hits],
                'warn_blank_jzm': warn_blank,
            })

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
        query = raw_name + ' ' + qualifier if re.search(r'秘密|隐藏|secret|hidden', qualifier, re.I) else raw_name
        hits = match_jzm(query, all_products, aliases, threshold=_SCORE_REVIEW, limit=5)
        _bucket(item, hits)

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
        'parser_engine':       parser_engine,
        'multi_day':           multi_day,
    })
