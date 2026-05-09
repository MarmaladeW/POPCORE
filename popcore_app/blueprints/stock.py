"""
blueprints/stock.py — stock queries, movements, adjustments, export.
"""
from datetime import date
from flask import Blueprint, request, jsonify, Response

from db import get_db, esc_csv, _ensure_stock_row
from auth import login_required, role_required
from blueprints.stores import _resolve_store

bp = Blueprint('stock', __name__)


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
    try:
        pid = int(data['product_id'])
        qty = int(data['qty'])
    except (KeyError, TypeError, ValueError) as e:
        return jsonify({'error': f'Invalid input: {e}'}), 400
    d     = data.get('date', str(date.today()))
    notes = data.get('notes', '')

    if qty <= 0:
        return jsonify({'error': '入店数量必须大于0'}), 400

    con = get_db()
    store_id, store_code, err = _require_store_body(con, data)
    if err:
        con.close()
        return err
    cur = con.cursor()
    _ensure_stock_row(cur, pid, store_id)

    cur.execute('SELECT upstairs_qty FROM stock WHERE product_id = ? AND store_id = ?',
                (pid, store_id))
    row = cur.fetchone()
    upstairs = row['upstairs_qty'] if row else 0
    if qty > upstairs:
        con.close()
        return jsonify({'error': f'楼上库存不足（现有 {upstairs}）'}), 400

    cur.execute('''
        UPDATE stock
        SET upstairs_qty = upstairs_qty - ?,
            instore_qty  = instore_qty  + ?,
            last_updated = ?
        WHERE product_id = ? AND store_id = ?
    ''', (qty, qty, d, pid, store_id))
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
    try:
        pid = int(data['product_id'])
        qty = int(data['qty'])
    except (KeyError, TypeError, ValueError) as e:
        return jsonify({'error': f'Invalid input: {e}'}), 400
    d     = data.get('date', str(date.today()))
    notes = data.get('notes', '')

    if qty <= 0:
        return jsonify({'error': '入库数量必须大于0'}), 400

    con = get_db()
    store_id, store_code, err = _require_store_body(con, data)
    if err:
        con.close()
        return err
    cur = con.cursor()
    _ensure_stock_row(cur, pid, store_id)

    cur.execute('''
        UPDATE stock
        SET upstairs_qty = upstairs_qty + ?,
            last_updated = ?
        WHERE product_id = ? AND store_id = ?
    ''', (qty, d, pid, store_id))
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
    try:
        pid     = int(data['product_id'])
        new_qty = int(data['new_qty'])
    except (KeyError, TypeError, ValueError) as e:
        return jsonify({'error': f'Invalid input: {e}'}), 400
    if new_qty < 0:
        return jsonify({'error': '库存不能为负数'}), 400
    location = data.get('location', 'upstairs')
    d        = data.get('date', str(date.today()))
    notes    = data.get('notes', '')

    if location not in ('upstairs', 'instore'):
        return jsonify({'error': 'location must be upstairs or instore'}), 400

    con = get_db()
    store_id, store_code, err = _require_store_body(con, data)
    if err:
        con.close()
        return err
    cur = con.cursor()
    _ensure_stock_row(cur, pid, store_id)

    cur.execute('SELECT upstairs_qty, instore_qty FROM stock WHERE product_id = ? AND store_id = ?',
                (pid, store_id))
    _row = cur.fetchone()
    if _row is None:
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
    cur.execute(f'DELETE FROM stock WHERE store_id = ? AND product_id IN ({ph})',
                [store_id] + pids)
    deleted = cur.rowcount
    con.commit()
    con.close()
    return jsonify({'ok': True, 'deleted': deleted})
