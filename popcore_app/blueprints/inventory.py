"""
blueprints/inventory.py — inventory checks, bestseller management, history.
"""
import sqlite3
from datetime import date, timedelta
from flask import Blueprint, request, jsonify

from db import get_db
from auth import login_required, role_required
from blueprints.stores import _resolve_store

bp = Blueprint('inventory', __name__)


def _calc_theoretical_qty(cur, product_id: int, today: str,
                           store_id: int, store_code: str) -> tuple[int, str, bool]:
    """
    Calculate theoretical store stock for a bestseller product.
    Returns (theoretical_qty, base_check_date, is_base_abnormal).

    Logic:
      - Find most recent inventory_checks record for this product + store (base).
      - If no base: use current instore_qty for this store, base_check_date = today.
      - theoretical = base.actual_qty - SUM(sales since base.date for this store)
                    + SUM(restock_in movements since base.date)
      - is_base_abnormal = base exists but base.date != yesterday.

    Note: stock_movements has no store_id column; restock_in counts are unfiltered
    by store and will include movements from all stores until that column is added.
    """
    yesterday = (date.fromisoformat(today) - timedelta(days=1)).isoformat()

    cur.execute('''
        SELECT actual_qty, date AS check_date
        FROM inventory_checks
        WHERE product_id = ? AND store_id = ?
        ORDER BY date DESC
        LIMIT 1
    ''', (product_id, store_id))
    base = cur.fetchone()

    if not base:
        cur.execute(
            'SELECT COALESCE(instore_qty, 0) FROM stock WHERE product_id = ? AND store_id = ?',
            (product_id, store_id),
        )
        row = cur.fetchone()
        return (row[0] if row else 0), today, False

    base_date = base['check_date']
    theoretical = base['actual_qty']

    cur.execute('''
        SELECT COALESCE(SUM(qty_pos + qty_cash), 0)
        FROM daily_sales
        WHERE product_id = ? AND date > ? AND store = ?
    ''', (product_id, base_date, store_code))
    sales_since = cur.fetchone()[0] or 0

    # stock_movements has no store_id column — counts movements across all stores
    cur.execute('''
        SELECT COALESCE(SUM(qty_change), 0)
        FROM stock_movements
        WHERE product_id = ? AND movement_type = 'restock_in'
          AND location = 'store' AND date(created_at) > ?
    ''', (product_id, base_date))
    restock_since = cur.fetchone()[0] or 0

    theoretical = theoretical - sales_since + restock_since
    is_base_abnormal = base_date != yesterday
    return theoretical, base_date, is_base_abnormal


# ─── Inventory Check API ──────────────────────────────────────────────────────

@bp.route('/api/inventory-check/today')
@login_required
def inventory_check_today():
    today = date.today().isoformat()
    con   = get_db()
    store_code = (request.args.get('store_code') or '').strip().upper()
    if not store_code:
        con.close()
        return jsonify({'error': 'store_code is required'}), 400
    resolved = _resolve_store(con, store_code)
    if resolved is None:
        con.close()
        return jsonify({'error': 'Invalid store code'}), 400
    store_id, store_code = resolved
    cur = con.cursor()

    cur.execute('''
        SELECT p.id AS product_id, p.sku, p.jizhanming, p.name_cn_en,
               p.ip_series, p.product_type,
               COALESCE(s.instore_qty, 0) AS current_instore_qty,
               ic.id AS check_id, ic.actual_qty, ic.discrepancy,
               ic.base_check_date AS saved_base_check_date
        FROM products p
        LEFT JOIN stock s ON s.product_id = p.id AND s.store_id = ?
        LEFT JOIN inventory_checks ic
               ON ic.product_id = p.id AND ic.date = ? AND ic.store_id = ?
        WHERE p.is_bestseller = 1
        ORDER BY p.ip_series, p.sku DESC
    ''', (store_id, today, store_id))
    rows = [dict(r) for r in cur.fetchall()]

    result = []
    for row in rows:
        pid = row['product_id']
        theoretical_qty, base_check_date, is_base_abnormal = _calc_theoretical_qty(
            cur, pid, today, store_id, store_code
        )
        result.append({
            **row,
            'theoretical_qty':  theoretical_qty,
            'base_check_date':  base_check_date,
            'is_base_abnormal': is_base_abnormal,
        })

    con.close()
    return jsonify({'date': today, 'items': result})


@bp.route('/api/inventory-check/submit', methods=['POST'])
@role_required('staff')
def submit_inventory_check():
    """
    Batch-submit inventory check records.
    Body: { store_code: '...', checks: [{ product_id, actual_qty }] }
    (date, product_id, store_id) conflict returns 409 — no overwrite.
    """
    data   = request.get_json() or {}
    checks = data.get('checks', [])
    if not checks:
        return jsonify({'error': 'checks 不能为空'}), 400

    con        = get_db()
    store_code = (data.get('store_code') or '').strip().upper()
    if not store_code:
        con.close()
        return jsonify({'error': 'store_code is required'}), 400
    resolved = _resolve_store(con, store_code)
    if resolved is None:
        con.close()
        return jsonify({'error': 'Invalid store code'}), 400
    store_id, store_code = resolved

    today      = date.today().isoformat()
    created_by = request.jwt_payload.get('sub')
    cur = con.cursor()

    saved     = 0
    results   = []
    conflicts = []

    for chk in checks:
        pid        = chk.get('product_id')
        actual_qty = chk.get('actual_qty')
        if pid is None or actual_qty is None:
            continue

        theoretical_qty, base_check_date, _ = _calc_theoretical_qty(
            cur, int(pid), today, store_id, store_code
        )
        discrepancy = int(actual_qty) - theoretical_qty

        try:
            cur.execute('''
                INSERT INTO inventory_checks
                    (date, product_id, theoretical_qty, actual_qty, discrepancy,
                     base_check_date, created_by, store_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (today, int(pid), theoretical_qty, int(actual_qty),
                  discrepancy, base_check_date, created_by, store_id))
            saved += 1
            results.append({'product_id': pid, 'discrepancy': discrepancy})
        except sqlite3.IntegrityError:
            conflicts.append(pid)

    con.commit()
    con.close()

    if conflicts and saved == 0:
        return jsonify({'error': '今日该商品已提交晚盘，如需修改请联系管理员',
                        'conflicts': conflicts}), 409

    return jsonify({'ok': True, 'saved': saved, 'results': results,
                    'conflicts': conflicts}), 201


# ─── Bestseller Management API ────────────────────────────────────────────────

@bp.route('/api/bestsellers')
@login_required
def get_bestsellers():
    con = get_db()
    store_code = (request.args.get('store_code') or '').strip().upper()
    if not store_code:
        con.close()
        return jsonify({'error': 'store_code is required'}), 400
    resolved = _resolve_store(con, store_code)
    if resolved is None:
        con.close()
        return jsonify({'error': 'Invalid store code'}), 400
    store_id, store_code = resolved
    cur = con.cursor()
    cur.execute('''
        SELECT p.id, p.sku, p.jizhanming, p.name_cn_en, p.ip_series, p.product_type,
               COALESCE(s.instore_qty, 0) AS instore_qty
        FROM products p
        LEFT JOIN stock s ON s.product_id = p.id AND s.store_id = ?
        WHERE p.is_bestseller = 1
        ORDER BY p.ip_series, p.sku DESC
    ''', (store_id,))
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return jsonify(rows)


@bp.route('/api/products/<int:pid>/bestseller', methods=['PATCH'])
@role_required('staff')
def set_bestseller(pid):
    data = request.get_json() or {}
    if 'is_bestseller' not in data:
        return jsonify({'error': 'is_bestseller 必须填写'}), 400
    flag = 1 if data['is_bestseller'] else 0
    con = get_db()
    cur = con.cursor()
    cur.execute('UPDATE products SET is_bestseller=? WHERE id=?', (flag, pid))
    if cur.rowcount == 0:
        con.close()
        return jsonify({'error': 'Product not found'}), 404
    con.commit()
    con.close()
    return jsonify({'ok': True, 'is_bestseller': bool(flag)})


# ─── History API ──────────────────────────────────────────────────────────────

@bp.route('/api/history/restock')
@login_required
def history_restock():
    date_from = request.args.get('date_from', '')
    date_to   = request.args.get('date_to',   '')
    try:
        page      = max(1, int(request.args.get('page', 1)))
        page_size = min(100, max(1, int(request.args.get('page_size', 20))))
    except (ValueError, TypeError):
        return jsonify({'error': 'page and page_size must be integers'}), 400
    offset = (page - 1) * page_size

    con = get_db()
    store_code = (request.args.get('store_code') or '').strip().upper()
    if not store_code:
        con.close()
        return jsonify({'error': 'store_code is required'}), 400
    resolved = _resolve_store(con, store_code)
    if resolved is None:
        con.close()
        return jsonify({'error': 'Invalid store code'}), 400
    store_id, store_code = resolved

    filters = ['rs.store_id = ?']
    params  = [store_id]
    if date_from:
        filters.append('rs.date >= ?'); params.append(date_from)
    if date_to:
        filters.append('rs.date <= ?'); params.append(date_to)
    where = 'WHERE ' + ' AND '.join(filters)

    cur = con.cursor()
    cur.execute(f'SELECT COUNT(*) FROM restock_sessions rs {where}', params)
    total = cur.fetchone()[0]

    cur.execute(f'''
        SELECT rs.id, rs.date, rs.status, rs.created_at, rs.submitted_at, rs.completed_at,
               COUNT(ri.id)                                     AS item_count,
               COALESCE(SUM(ri.requested_qty), 0)               AS total_requested,
               COALESCE(SUM(CASE WHEN ri.pick_status='found' THEN ri.found_qty ELSE 0 END), 0)
                                                                AS total_found
        FROM restock_sessions rs
        LEFT JOIN restock_items ri ON ri.session_id = rs.id
        {where}
        GROUP BY rs.id
        ORDER BY rs.date DESC
        LIMIT ? OFFSET ?
    ''', params + [page_size, offset])
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return jsonify({'total': total, 'page': page, 'page_size': page_size, 'sessions': rows})


@bp.route('/api/history/inventory-check')
@login_required
def history_inventory_check():
    date_from        = request.args.get('date_from', '')
    date_to          = request.args.get('date_to',   '')
    product_id       = request.args.get('product_id', type=int)
    only_discrepancy = request.args.get('only_discrepancy', '0') == '1'

    con = get_db()
    store_code = (request.args.get('store_code') or '').strip().upper()
    if not store_code:
        con.close()
        return jsonify({'error': 'store_code is required'}), 400
    resolved = _resolve_store(con, store_code)
    if resolved is None:
        con.close()
        return jsonify({'error': 'Invalid store code'}), 400
    store_id, store_code = resolved

    filters = ['ic.store_id = ?']
    params  = [store_id]
    if date_from:
        filters.append('ic.date >= ?'); params.append(date_from)
    if date_to:
        filters.append('ic.date <= ?'); params.append(date_to)
    if product_id:
        filters.append('ic.product_id = ?'); params.append(product_id)
    if only_discrepancy:
        filters.append('ic.discrepancy != 0')
    where = 'WHERE ' + ' AND '.join(filters)

    cur = con.cursor()
    cur.execute(f'''
        SELECT ic.id, ic.date, ic.product_id,
               p.sku, p.jizhanming, p.name_cn_en, p.ip_series, p.product_type,
               ic.theoretical_qty, ic.actual_qty, ic.discrepancy,
               ic.base_check_date, ic.created_at
        FROM inventory_checks ic
        JOIN products p ON p.id = ic.product_id
        {where}
        ORDER BY ic.date DESC, p.sku DESC
    ''', params)
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return jsonify(rows)
