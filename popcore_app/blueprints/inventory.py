"""
blueprints/inventory.py — inventory checks, bestseller management, history.
"""
import sqlite3
from datetime import date, timedelta
from flask import Blueprint, request, jsonify

from db import get_db
from auth import login_required, role_required
from checkout_access import business_date
from inventory_commands import store_is_live
from store_access import legacy_store_denied
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

    """
    yesterday = (date.fromisoformat(today) - timedelta(days=1)).isoformat()

    if store_is_live(cur.connection, store_id):
        location = cur.execute(
            """SELECT l.id, ss.opening_verified
               FROM inventory_locations l
               JOIN inventory_scope_state ss ON ss.location_id=l.id
               WHERE l.store_id=? AND l.code='floor' AND l.is_active=1""",
            (store_id,),
        ).fetchone()
        if location is None or not location['opening_verified']:
            return None, today, True
        balance = cur.execute(
            """SELECT quantity FROM inventory_balances
               WHERE product_id=? AND location_id=? AND disposition='saleable'""",
            (product_id, location['id']),
        ).fetchone()
        return (balance['quantity'] if balance else 0), today, False

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

    cur.execute('''
        SELECT COALESCE(SUM(qty_change), 0)
        FROM stock_movements
        WHERE product_id = ? AND movement_type = 'restock_in'
          AND location = 'store' AND store_id = ? AND date(created_at) > ?
    ''', (product_id, store_id, base_date))
    restock_since = cur.fetchone()[0] or 0

    theoretical = theoretical - sales_since + restock_since
    is_base_abnormal = base_date != yesterday
    return theoretical, base_date, is_base_abnormal


# ─── Inventory Check API ──────────────────────────────────────────────────────

# ─── 晚盘 at a live store (owner, 2026-11-20/21) ───────────────────────────────
# Staff count each bestseller on the floor every night, the way they write it in WeChat: whole sets (端) and loose
# boxes (散) on the floor only ("because it is everyday so it only counts the stock on the floor"). POPCORE compares
# the boxes with what its floor holds; nothing moves (a 盘点 is what corrects stock). It may be saved again the same
# night; the latest count and who saved it are kept.

def _evening_places(con, store_id):
    return [row[0] for row in con.execute(
        "SELECT id FROM inventory_locations WHERE store_id=? AND is_active=1 AND code='floor'",
        (store_id,))]


def _evening_expected(con, store_id, product, day=None):
    """(boxes per set or None, sets, loose boxes) POPCORE holds of this bestseller on the store's floor, less what
    the day's 记一笔 sales sold from the floor while no 日报 for the day is saved yet: their stock comes off only when
    the manager reviews the 日报 (owner, 2026-11-21), but they already left the floor before the 晚盘."""
    per, sets, loose = _evening_held(con, store_id, product)
    if day:
        import quick_sale
        from report_stock import _set_for_box, _stock_product
        box = _stock_product(con, product)[0] if product['stock_form'] == 'sealed_set' else product
        whole = product if product['stock_form'] == 'sealed_set' else _set_for_box(con, product['id'])
        sold = quick_sale.unreviewed_units(con, store_id, day, {box['id'], *( [whole['id']] if whole is not None else [])})
        boxes = sold.get(box['id'], 0) + (sold.get(whole['id'], 0) * per if whole is not None and per else 0)
        if boxes:
            # Boxes leave loose boxes first, then whole sets opened for them.
            left = max(0, sets * (per or 0) + loose - boxes)
            sets, loose = (min(sets, left // per), left - min(sets, left // per) * per) if per else (0, left)
    return per, sets, loose


def _evening_held(con, store_id, product):
    """(boxes per set or None, sets, loose boxes) POPCORE holds of this bestseller on the store's floor."""
    from report_stock import _set_for_box, _stock_product
    if product['stock_form'] == 'sealed_set':
        whole, (box, per) = product, _stock_product(con, product)
    else:
        box, whole = product, _set_for_box(con, product['id'])
        per = whole['boxes_per_set'] if whole else None
    places = _evening_places(con, store_id)
    marks = ','.join('?' * len(places)) or 'NULL'
    held = lambda product_id: con.execute(  # noqa: E731
        f"""SELECT COALESCE(SUM(quantity), 0) FROM inventory_balances
             WHERE product_id=? AND disposition='saleable' AND location_id IN ({marks})""", (product_id, *places)).fetchone()[0]
    sets = held(whole['id']) if whole is not None and per else 0
    return per, sets, held(box['id']) if box['id'] != (whole['id'] if whole is not None else None) else 0


def _evening_today(con, store_id, today):
    from inventory_visibility import can_see_inventory
    visible = can_see_inventory(con, request.jwt_payload, (store_id,))
    items = []
    for product in con.execute('SELECT * FROM products WHERE is_bestseller=1 ORDER BY ip_series, sku DESC').fetchall():
        per, sets, loose = _evening_expected(con, store_id, product, today)
        saved = con.execute("""SELECT ic.actual_sets, ic.actual_loose, ic.actual_qty, ic.discrepancy, ic.created_by,
                                        COALESCE(ic.updated_at, ic.created_at) AS at, e.name
                                 FROM inventory_checks ic LEFT JOIN employees e ON e.auth0_id=ic.created_by
                                 WHERE ic.product_id=? AND ic.date=? AND ic.store_id=?""",
                            (product['id'], today, store_id)).fetchone()
        items.append({
            'product_id': product['id'], 'sku': product['sku'], 'jizhanming': product['jizhanming'],
            'name_cn_en': product['name_cn_en'], 'ip_series': product['ip_series'], 'per_set': per,
            # What POPCORE holds, only for those who may see the store's stock (库存查看).
            'expected_sets': sets if visible else None, 'expected_loose': loose if visible else None,
            'saved': None if saved is None else {
                'sets': saved['actual_sets'], 'loose': saved['actual_loose'], 'matches': saved['discrepancy'] == 0,
                'difference': saved['discrepancy'] if visible else None, 'by': saved['name'], 'at': saved['at']},
        })
    return items


@bp.route('/api/inventory-check/today')
@login_required
def inventory_check_today():
    today = business_date()
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
    if store_is_live(con, store_id):
        items = _evening_today(con, store_id, today)
        con.close()
        return jsonify({'date': today, 'store_code': store_code, 'live': True, 'items': items})
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
    denied = legacy_store_denied(con, request.jwt_payload, store_id)
    if denied:
        con.close()
        return denied

    today      = business_date()
    created_by = request.jwt_payload.get('sub')
    cur = con.cursor()

    if store_is_live(con, store_id):
        results = []
        for chk in checks:
            product = con.execute('SELECT * FROM products WHERE id=? AND is_bestseller=1',
                                  (chk.get('product_id'),)).fetchone() if isinstance(chk, dict) else None
            sets, loose = (chk.get('sets') or 0, chk.get('loose') or 0) if product else (None, None)
            if product is None or not all(isinstance(n, int) and 0 <= n <= 100000 for n in (sets, loose)):
                continue
            per, held_sets, held_loose = _evening_expected(con, store_id, product, today)
            actual, expected = sets * (per or 0) + loose, held_sets * (per or 0) + held_loose
            con.execute("""INSERT INTO inventory_checks(date, product_id, theoretical_qty, actual_qty, discrepancy,
                               base_check_date, created_by, store_id, actual_sets, actual_loose, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                           ON CONFLICT(date, product_id, store_id) DO UPDATE SET theoretical_qty=excluded.theoretical_qty,
                               actual_qty=excluded.actual_qty, discrepancy=excluded.discrepancy,
                               created_by=excluded.created_by, actual_sets=excluded.actual_sets,
                               actual_loose=excluded.actual_loose, updated_at=excluded.updated_at""",
                        (today, product['id'], expected, actual, actual - expected, today, created_by, store_id,
                         sets, loose))
            results.append({'product_id': product['id'], 'matches': actual == expected})
        con.commit()
        items = _evening_today(con, store_id, today)
        con.close()
        if not results:
            return jsonify({'error': '没有要保存的晚盘 Nothing to save'}), 400
        return jsonify({'ok': True, 'saved': len(results), 'live': True, 'items': items,
                        'differ': sum(1 for row in results if not row['matches'])}), 201

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
        if theoretical_qty is None:
            con.rollback()
            con.close()
            return jsonify({
                'error': 'Reviewed opening balance is required',
                'code': 'reconciliation_required',
            }), 409
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
