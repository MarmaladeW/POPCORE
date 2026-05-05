"""
blueprints/stock.py — stock queries, movements, adjustments, export.
"""
from datetime import date
from flask import Blueprint, request, jsonify, Response

from db import get_db, esc_csv, _ensure_stock_row
from auth import login_required, role_required

bp = Blueprint('stock', __name__)


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

    if include_all:
        if paginated:
            cur.execute(
                f'SELECT COUNT(*) FROM products p LEFT JOIN stock s ON s.product_id = p.id WHERE 1=1 {where}',
                params)
            total = cur.fetchone()[0]
        cur.execute(f'''
            SELECT p.id, p.sku, p.name_cn_en, p.jizhanming, p.price,
                   p.ip_series, p.product_type, p.boxes_per_dan, p.dan_per_xiang,
                   COALESCE(s.upstairs_qty, 0) AS upstairs_qty,
                   COALESCE(s.instore_qty,  0) AS instore_qty,
                   COALESCE(s.last_updated, '') AS last_updated,
                   COALESCE(s.notes, '')        AS stock_notes
            FROM products p
            LEFT JOIN stock s ON s.product_id = p.id
            WHERE 1=1 {where}
            ORDER BY p.ip_series, p.sku DESC
            {'LIMIT ? OFFSET ?' if paginated else ''}
        ''', params + ([page_size, offset] if paginated else []))
    else:
        if paginated:
            cur.execute(
                f'SELECT COUNT(*) FROM stock s JOIN products p ON p.id = s.product_id WHERE 1=1 {where}',
                params)
            total = cur.fetchone()[0]
        cur.execute(f'''
            SELECT p.id, p.sku, p.name_cn_en, p.jizhanming, p.price,
                   p.ip_series, p.product_type, p.boxes_per_dan, p.dan_per_xiang,
                   s.upstairs_qty, s.instore_qty,
                   s.last_updated, COALESCE(s.notes, '') AS stock_notes
            FROM stock s
            JOIN products p ON p.id = s.product_id
            WHERE 1=1 {where}
            ORDER BY p.ip_series, p.sku DESC
            {'LIMIT ? OFFSET ?' if paginated else ''}
        ''', params + ([page_size, offset] if paginated else []))

    items = [dict(r) for r in cur.fetchall()]
    con.close()
    if paginated:
        return jsonify({'items': items, 'total': total, 'page': page, 'page_size': page_size})
    return jsonify(items)


@bp.route('/api/stock/<int:product_id>')
@login_required
def get_stock(product_id):
    con = get_db()
    cur = con.cursor()
    cur.execute('''
        SELECT p.id, p.sku, p.name_cn_en, p.jizhanming, p.price,
               p.ip_series, p.product_type, p.boxes_per_dan, p.dan_per_xiang,
               COALESCE(s.upstairs_qty, 0) AS upstairs_qty,
               COALESCE(s.instore_qty,  0) AS instore_qty,
               COALESCE(s.last_updated, '') AS last_updated,
               COALESCE(s.notes, '') AS stock_notes
        FROM products p
        LEFT JOIN stock s ON s.product_id = p.id
        WHERE p.id = ?
    ''', (product_id,))
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
    con.execute('UPDATE stock SET notes=? WHERE product_id=?', (notes, product_id))
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
        qty = int(data.get('qty') or data.get('dan_qty', 0))
    except (KeyError, TypeError, ValueError) as e:
        return jsonify({'error': f'Invalid input: {e}'}), 400
    d     = data.get('date', str(date.today()))
    notes = data.get('notes', '')

    if qty <= 0:
        return jsonify({'error': '入店数量必须大于0'}), 400

    con = get_db()
    cur = con.cursor()
    _ensure_stock_row(cur, pid)

    cur.execute('SELECT upstairs_qty FROM stock WHERE product_id = ?', (pid,))
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
        WHERE product_id = ?
    ''', (qty, qty, d, pid))
    cur.execute('''
        INSERT INTO stock_transactions (product_id, txn_type, qty, location, date, notes)
        VALUES (?, 'ru_dian', ?, 'upstairs->instore', ?, ?)
    ''', (pid, qty, d, notes))
    con.commit()

    cur.execute('SELECT upstairs_qty, instore_qty FROM stock WHERE product_id = ?', (pid,))
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
        qty = int(data.get('qty') or data.get('dan_qty', 0))
    except (KeyError, TypeError, ValueError) as e:
        return jsonify({'error': f'Invalid input: {e}'}), 400
    d     = data.get('date', str(date.today()))
    notes = data.get('notes', '')

    if qty <= 0:
        return jsonify({'error': '入库数量必须大于0'}), 400

    con = get_db()
    cur = con.cursor()
    _ensure_stock_row(cur, pid)

    cur.execute('''
        UPDATE stock
        SET upstairs_qty = upstairs_qty + ?,
            last_updated = ?
        WHERE product_id = ?
    ''', (qty, d, pid))
    cur.execute('''
        INSERT INTO stock_transactions (product_id, txn_type, qty, location, date, notes)
        VALUES (?, 'restock_upstairs', ?, 'upstairs', ?, ?)
    ''', (pid, qty, d, notes))
    con.commit()

    cur.execute('SELECT upstairs_qty, instore_qty FROM stock WHERE product_id = ?', (pid,))
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
        new_qty = int(data.get('new_qty') if data.get('new_qty') is not None else data.get('new_dan', 0))
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
    cur = con.cursor()
    _ensure_stock_row(cur, pid)

    cur.execute('SELECT upstairs_qty, instore_qty FROM stock WHERE product_id = ?', (pid,))
    _row = cur.fetchone()
    if _row is None:
        con.close()
        return jsonify({'error': 'Stock row not found'}), 404
    s = dict(_row)
    old_val = s[f'{location}_qty']
    delta   = new_qty - old_val

    if location == 'upstairs':
        cur.execute('UPDATE stock SET upstairs_qty = ?, last_updated = ? WHERE product_id = ?',
                    (new_qty, d, pid))
    else:
        cur.execute('UPDATE stock SET instore_qty = ?, last_updated = ? WHERE product_id = ?',
                    (new_qty, d, pid))

    cur.execute('''
        INSERT INTO stock_transactions (product_id, txn_type, qty, location, date, notes)
        VALUES (?, 'adjust', ?, ?, ?, ?)
    ''', (pid, delta, location, d, notes or f'手动调整: {old_val}→{new_qty}'))

    con.commit()
    cur.execute('SELECT upstairs_qty, instore_qty FROM stock WHERE product_id = ?', (pid,))
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
    cur = con.cursor()

    conditions = []
    params = []
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
               t.date, t.notes, t.created_at,
               p.jizhanming, p.sku, p.name_cn_en, p.boxes_per_dan, p.dan_per_xiang, p.product_type
        FROM stock_transactions t
        JOIN products p ON p.id = t.product_id
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
    cur = con.cursor()
    cur.execute('''
        SELECT
            COUNT(*) AS products_tracked,
            SUM(upstairs_qty) AS total_upstairs_qty,
            SUM(instore_qty)  AS total_instore_qty
        FROM stock
    ''')
    row = dict(cur.fetchone())

    cur.execute('''
        SELECT COUNT(*) FROM stock
        WHERE upstairs_qty = 0 AND instore_qty > 0
    ''')
    row['low_stock_count'] = cur.fetchone()[0]

    cur.execute('SELECT COUNT(*) FROM stock WHERE upstairs_qty = 0 AND instore_qty = 0')
    row['out_of_stock_count'] = cur.fetchone()[0]

    cur.execute('''
        SELECT COALESCE(SUM(p.price * (s.upstairs_qty + s.instore_qty)), 0)
        FROM stock s
        JOIN products p ON p.id = s.product_id
        WHERE p.price IS NOT NULL
    ''')
    row['total_stock_value'] = cur.fetchone()[0]

    con.close()
    return jsonify(row)


@bp.route('/api/stock/export')
@role_required('manager')
def export_stock():
    series = request.args.get('series', '').strip()
    q      = request.args.get('q', '').strip().lower()

    con = get_db()
    cur = con.cursor()

    filters = []
    params  = []
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
               p.price, p.boxes_per_dan, p.dan_per_xiang,
               COALESCE(s.upstairs_qty, 0) AS upstairs_qty,
               COALESCE(s.instore_qty,  0) AS instore_qty,
               COALESCE(s.last_updated, '') AS last_updated,
               COALESCE(s.notes, '') AS stock_notes
        FROM stock s
        JOIN products p ON p.id = s.product_id
        WHERE 1=1 {where}
        ORDER BY p.ip_series, p.sku DESC
    ''', params)
    rows = cur.fetchall()
    con.close()

    header = 'SKU,记账名,产品名称,系列,类型,单价,每端盒数,每箱端数,楼上(盒/件),店内(盒/件),更新时间,备注'
    lines  = ['﻿' + header]
    for r in rows:
        lines.append(','.join(esc_csv(v) for v in [
            r['sku'], r['jizhanming'], r['name_cn_en'], r['ip_series'],
            r['product_type'], r['price'], r['boxes_per_dan'], r['dan_per_xiang'],
            r['upstairs_qty'], r['instore_qty'], r['last_updated'], r['stock_notes']
        ]))

    fname = f'stock_{date.today()}.csv'
    return Response(
        '\n'.join(lines),
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="{fname}"'}
    )


@bp.route('/api/stock/rows', methods=['DELETE'])
@role_required('manager')
def delete_stock_rows():
    pids = request.get_json()
    if not isinstance(pids, list) or not pids:
        return jsonify({'error': 'Expected a list of product_ids'}), 400
    con = get_db()
    cur = con.cursor()
    ph  = ','.join('?' * len(pids))
    cur.execute(f'DELETE FROM stock WHERE product_id IN ({ph})', pids)
    deleted = cur.rowcount
    con.commit()
    con.close()
    return jsonify({'ok': True, 'deleted': deleted})
