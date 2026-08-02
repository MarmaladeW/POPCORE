"""
blueprints/sales.py — daily sales records, batch import, daily report, export.
"""
import re
import unicodedata
from datetime import date, timedelta
from flask import Blueprint, request, jsonify, Response

from db import get_db, esc_csv, _ensure_stock_row
from auth import login_required, role_required
from blueprints.stores import _resolve_store
from matcher import match_jzm, normalize as _norm_jzm, clean_name as _clean_jzm

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
    if not data or 'product_id' not in data:
        return jsonify({'error': 'product_id is required'}), 400
    try:
        pid      = int(data['product_id'])
        qty_pos  = int(data.get('qty_pos',  0) or 0)
        qty_cash = int(data.get('qty_cash', 0) or 0)
        if 'qty_pos' not in data and 'qty_cash' not in data:
            qty_cash = int(data.get('qty_sold', 0) or 0)
    except (ValueError, TypeError):
        return jsonify({'error': 'product_id and qty fields must be integers'}), 400
    d        = data.get('date', str(date.today()))
    notes    = data.get('notes', '')
    qty_sold = qty_pos + qty_cash

    con = get_db()
    store_id, store_code, err = _require_store_body(con, data)
    if err:
        con.close()
        return err
    cur = con.cursor()
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
    cur = con.cursor()
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
    operation = data.get('operation', 'ru_dian')
    d         = data.get('date', str(date.today()))
    items     = data.get('items', [])

    if operation not in ('ru_dian', 'restock_upstairs', 'out_dian', 'ru_dian_claw'):
        return jsonify({'error': 'Invalid operation'}), 400

    con = get_db()
    store_id, store_code, err = _require_store_body(con, data)
    if err:
        con.close()
        return err
    cur = con.cursor()
    results = []

    for item in items:
        try:
            pid = int(item['product_id'])
            qty = int(item.get('qty', 0))
        except (KeyError, ValueError, TypeError):
            con.close()
            return jsonify({'error': f'Each item must have an integer product_id and qty'}), 400
        notes = item.get('notes', '')
        if qty <= 0:
            continue

        _ensure_stock_row(cur, pid, store_id)

        if operation == 'ru_dian':
            cur.execute('SELECT upstairs_qty FROM stock WHERE product_id = ? AND store_id = ?',
                        (pid, store_id))
            row = cur.fetchone()
            upstairs = row['upstairs_qty'] if row else 0
            if qty > upstairs:
                results.append({'pid': pid, 'ok': False,
                                 'error': f'楼上库存不足（{upstairs}）'})
                continue
            cur.execute('''
                UPDATE stock SET upstairs_qty = upstairs_qty - ?,
                                 instore_qty  = instore_qty  + ?,
                                 last_updated = ?
                WHERE product_id = ? AND store_id = ?
            ''', (qty, qty, d, pid, store_id))
            loc = 'upstairs->instore'
        elif operation == 'out_dian':
            cur.execute('SELECT instore_qty FROM stock WHERE product_id = ? AND store_id = ?',
                        (pid, store_id))
            row = cur.fetchone()
            instore = row['instore_qty'] if row else 0
            if qty > instore:
                results.append({'pid': pid, 'ok': False,
                                 'error': f'店内库存不足（{instore}）'})
                continue
            cur.execute('''
                UPDATE stock SET instore_qty = instore_qty - ?,
                                 last_updated = ?
                WHERE product_id = ? AND store_id = ?
            ''', (qty, d, pid, store_id))
            loc = 'instore_out'
        elif operation == 'ru_dian_claw':
            cur.execute('SELECT upstairs_qty FROM stock WHERE product_id = ? AND store_id = ?',
                        (pid, store_id))
            row = cur.fetchone()
            upstairs = row['upstairs_qty'] if row else 0
            if qty > upstairs:
                results.append({'pid': pid, 'ok': False,
                                 'error': f'楼上库存不足（{upstairs}）'})
                continue
            cur.execute('''
                UPDATE stock SET upstairs_qty = upstairs_qty - ?,
                                 instore_qty  = instore_qty  + ?,
                                 claw_qty     = claw_qty     + ?,
                                 last_updated = ?
                WHERE product_id = ? AND store_id = ?
            ''', (qty, qty, qty, d, pid, store_id))
            loc = 'upstairs->claw'
        else:  # restock_upstairs
            cur.execute('''
                UPDATE stock SET upstairs_qty = upstairs_qty + ?,
                                 last_updated = ?
                WHERE product_id = ? AND store_id = ?
            ''', (qty, d, pid, store_id))
            loc = 'upstairs'

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
    for item in items:
        try:
            pid      = int(item['product_id'])
            qty_pos  = int(item.get('qty_pos',  0) or 0)
            qty_cash = int(item.get('qty_cash', 0) or 0)
        except (KeyError, ValueError, TypeError):
            con.close()
            return jsonify({'error': 'Each item must have an integer product_id, qty_pos, and qty_cash'}), 400
        rows.append((pid, item.get('date', str(date.today())), store_code,
                     qty_pos, qty_cash, qty_pos + qty_cash, item.get('notes', '')))

    cur = con.cursor()
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


@bp.route('/api/sales/submit_daily_report', methods=['POST'])
@role_required('staff')
def submit_daily_report():
    """
    Submit a parsed daily report in one atomic transaction.

    A submission IS the day: by default (mode='replace') any previously
    submitted report for the same (date, store) is removed first — its
    daily_sales rows deleted and its stock transactions reversed — so
    re-importing a corrected report is always safe and never double-counts.
    Pass mode='append' for the legacy accumulate behaviour.

    Body:
    {
      "date":       "2026-04-01",
      "store_code": "DT",
      "mode":       "replace" | "append"   (default "replace"),
      "items": [
        { "product_id": <int>, "section": "pos"|"cash"|"claw"|"sell_display"
                               |"employee_discount"|"break_display"|"stock_in"|"stock_out",
          "qty_pos": <int>, "qty_cash": <int>, "qty": <int>,
          "box_size": <int>, "num_boxes": <int>, "notes": <str>, "raw_name": <str> }
      ]
    }
    """
    data  = request.get_json(silent=True) or {}
    d     = (data.get('date') or '').strip()
    items = data.get('items')
    mode  = (data.get('mode') or 'replace').strip().lower()

    if not d or not isinstance(items, list):
        return jsonify({'error': 'date and items required'}), 400
    if mode not in ('replace', 'append'):
        return jsonify({'error': "mode must be 'replace' or 'append'"}), 400

    con = get_db()
    store_id, store_code, err = _require_store_body(con, data)
    if err:
        con.close()
        return err

    cur = con.cursor()
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
        if mode == 'replace':
            replaced_rows, reverted_txns = _revert_report_day(cur, d, store_code, store_id)

        for item in items:
            pid     = item.get('product_id')
            section = (item.get('section') or '').strip()
            notes   = (item.get('notes') or '').strip()
            raw     = (item.get('raw_name') or '').strip()
            if not pid or not section:
                continue

            if section in SALES_SECTIONS:
                qty_pos  = int(item.get('qty_pos',  0) or 0)
                qty_cash = int(item.get('qty_cash', 0) or 0)
                qty      = int(item.get('qty',      0) or 0)
                base_qty = (qty_pos + qty_cash) or qty
                if base_qty <= 0:
                    continue

                quantities = {c: 0 for c in _SECTION_QTY_COL.values()}
                quantities[_SECTION_QTY_COL[section]] = base_qty

                cur.execute('''
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
                ''', (pid, d, store_code,
                      quantities['qty_pos'], quantities['qty_cash'], quantities['qty_claw'],
                      quantities['qty_display'], quantities['qty_employee'],
                      base_qty, _unit_price(pid), raw, notes))
                sales_count += 1

            elif section == 'break_display':
                qty = int(item.get('qty', 1) or 1)
                _ensure_stock_row(cur, pid, store_id)
                cur.execute('''
                    INSERT INTO stock_transactions
                        (product_id, txn_type, qty, location, date, notes, store_id)
                    VALUES (?, 'display_open', ?, 'instore', ?, ?, ?)
                ''', (pid, -qty, d, notes or 'display opened', store_id))
                cur.execute('''
                    UPDATE stock SET instore_qty = MAX(0, instore_qty - ?),
                                     last_updated = datetime('now')
                    WHERE product_id = ? AND store_id = ?
                ''', (qty, pid, store_id))
                txn_count += 1

            elif section == 'stock_out':
                qty = int(item.get('qty', 0) or 0) or int(item.get('qty_pos', 0) or 0) or 1
                _ensure_stock_row(cur, pid, store_id)
                cur.execute('''
                    INSERT INTO stock_transactions
                        (product_id, txn_type, qty, location, date, notes, store_id)
                    VALUES (?, 'report_stock_out', ?, 'instore→out', ?, ?, ?)
                ''', (pid, qty, d, notes or '出店', store_id))
                cur.execute('''
                    UPDATE stock SET instore_qty = MAX(0, instore_qty - ?),
                                     last_updated = datetime('now')
                    WHERE product_id = ? AND store_id = ?
                ''', (qty, pid, store_id))
                txn_count += 1

            elif section == 'stock_in':
                box_size   = int(item.get('box_size',  1) or 1)
                num_boxes  = int(item.get('num_boxes', 1) or 1)
                total_duan = box_size * num_boxes
                cur.execute('SELECT product_type, boxes_per_dan FROM products WHERE id=?', (pid,))
                prow = cur.fetchone()
                bpd  = (prow['boxes_per_dan'] or 1) if (prow and prow['product_type'] == '盲盒') else 1
                total_units = total_duan * bpd
                cur.execute('''
                    INSERT INTO stock_transactions
                        (product_id, txn_type, qty, location, date, notes, store_id)
                    VALUES (?, 'report_stock_in', ?, 'upstairs→instore', ?, ?, ?)
                ''', (pid, total_units, d, notes or f'{num_boxes}端', store_id))
                cur.execute('''
                    INSERT INTO stock (product_id, store_id, upstairs_qty, instore_qty)
                    VALUES (?, ?, 0, ?)
                    ON CONFLICT(product_id, store_id) DO UPDATE SET
                        upstairs_qty = MAX(0, upstairs_qty - ?),
                        instore_qty  = instore_qty + ?,
                        last_updated = datetime('now')
                ''', (pid, store_id, total_units, total_units, total_units))
                txn_count += 1

        # Record match corrections for manually-reviewed items (Issue 3)
        for item in items:
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

        con.commit()
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

    header = '日期,记账名,SKU,系列,类型,单价,卡机数量,现金/转账数量,娃娃机,卖Display,员工折扣,总销量,原始输入,备注'
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
    d = request.args.get('date', '')
    if not d:
        return jsonify({'error': 'date param required'}), 400
    store_code_raw = (request.args.get('store_code') or '').strip().upper()
    if store_code_raw == 'ALL':
        return jsonify({'error': 'Cannot write with store_code ALL. Select a specific store.'}), 400
    con = get_db()
    store_id, store_code, err = _require_store_param(con)
    if err:
        con.close()
        return err
    cur = con.cursor()
    cur.execute('DELETE FROM daily_sales WHERE date = ? AND store = ?', (d, store_code))
    deleted = cur.rowcount
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
            "SELECT DISTINCT date FROM daily_sales WHERE date LIKE ? ORDER BY date",
            (prefix + '%',),
        )
    else:
        cur.execute(
            "SELECT DISTINCT date FROM daily_sales WHERE date LIKE ? AND store = ? ORDER BY date",
            (prefix + '%', store_code),
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
    ('卡机汇总',   'pos'),
    ('随手记汇总', 'cash'),
    ('随手记',     'cash'),
    ('卡机',       'pos'),
    ('入店',       'stock_in'),
    ('出店',       'stock_out'),
    ('入display',  'break_display'),  # putting a unit into display — same stock effect as 拆display
    ('卖display',  'sell_display'),
    ('拆display',  'break_display'),
    ('娃娃机',     'skip'),        # CLAW_MACHINE — skip
    ('员工折扣',   'employee_discount'),
    ('晚盘',       'skip'),        # EVENING_CHECK — skip
    ('博主探店',   'skip'),        # INFLUENCER — skip
    ('现金',       'cash_total'),  # CASH_TOTAL — captured as a checksum, items skipped
]

# Layers 5 — score thresholds
_SCORE_CONFIRMED = 80
_SCORE_REVIEW    = 50
# Auto-confirm additionally requires the top hit to beat the runner-up by
# this margin (exact/alias hits at 100 are exempt) — near-twin names like
# "smiski sunday"/"smiski sundae" must go to review, not silently pick one.
_MARGIN_CONFIRM  = 10

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
    text = re.sub(r'\s*\*\s*', '*', text)
    # 3. Per-line: strip leading/trailing whitespace, collapse internal spaces
    lines = [re.sub(r'  +', ' ', ln.strip()) for ln in text.split('\n')]
    text = '\n'.join(lines)
    # 4. Remove double commas
    text = text.replace(',,', ',').replace('，，', '，')
    return text


# Separator that may follow a header keyword on the same line: optional 汇总
# suffix, then optional colon/dash, e.g. "入店汇总：", "出店:", "卡机 —"
_HEADER_SEP_RE = re.compile(r'^(?:汇总)?\s*[:：\-—]*\s*')
# Split on commas that are not inside a (single-level) parenthetical
_COMMA_SPLIT_RE = re.compile(r'[,，](?![^()]*\))')


def _detect_section_type(line: str, section_aliases: dict) -> tuple[str, str] | None:
    """
    Return (section_type, inline_content) if the line contains a section header,
    None if it's a product line.

    inline_content is any text following the header on the same line
    (e.g. "出店：sa糖果*1" → ('stock_out', 'sa糖果*1')) so header lines can no
    longer swallow product data.

    'skip'    → known section to ignore entirely.
    'unknown' → looks like a header but no keyword matched.
    """
    s = line.strip()
    lower = s.lower()
    for keyword, section in _SECTION_MAP:
        idx = lower.find(keyword.lower())
        if idx == -1:
            continue
        prefix = s[:idx]
        # A keyword inside a product token ("xxx*2出店"?) is not a header
        if '*' in prefix:
            continue
        rest = _HEADER_SEP_RE.sub('', s[idx + len(keyword):], count=1).strip()
        # Keyword mid-line: only a header when the line is clearly header-shaped —
        # nothing but the keyword after a short prefix ("DT卡机汇总"), or an
        # explicit colon right after the keyword ("今日出店：sa糖果*1").
        if idx > 0:
            has_colon_after = bool(re.match(r'^(?:汇总)?\s*[:：]', s[idx + len(keyword):]))
            if not (rest == '' or (has_colon_after and len(prefix.strip()) <= 6)):
                continue
            # 现金 appears inside product names too — require line start unless bare
            if section == 'cash_total' and rest != '':
                continue
        return section, rest
    # User-saved section aliases (alias_norm → section_type); whole-line headers only
    line_norm = re.sub(r'\s+', '', lower)
    for alias_norm, stype in section_aliases.items():
        if alias_norm and alias_norm in line_norm:
            return stype, ''
    # Ends with colon → treat as unknown header
    if s.endswith(':') or s.endswith('：'):
        return 'unknown', ''
    return None


def _extract_date_store(first_line: str, fallback_store: str):
    """Parse date and store code from a report header line (best-effort)."""
    dm = _DATE_RE.search(first_line)
    detected_date = (
        f"{dm.group(1)}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}" if dm else None
    )
    sm = re.search(r'([A-Za-z]{2,6})(?:汇总|店)', first_line)
    store = sm.group(1).upper() if sm else fallback_store
    return detected_date, store


_PAREN_RE = re.compile(r'\(([^()]*)\)')


def _extract_note(t: str) -> tuple[str, str]:
    """Pull parenthetical annotations out of a token.

    Returns (token_without_parens, note). An unclosed '(' captures to the end
    of the token — never beyond the line it is on.
    """
    notes: list[str] = []

    def _cap(m):
        inner = m.group(1).strip()
        if inner:
            notes.append(inner)
        return ''

    t = _PAREN_RE.sub(_cap, t)
    if '(' in t:
        head, _, tail = t.partition('(')
        tail = tail.strip()
        if tail:
            notes.append(tail)
        t = head
    return t.strip(), '；'.join(notes)


def _parse_token(token: str, section: str) -> dict | None:
    """
    Layer 3 — parse a single comma-split token.

    Returns None for empty tokens; always returns a dict for non-empty input.
    flagged=True means no quantity was found (requires user input before commit).
    Parenthetical content is captured into 'note' rather than discarded.
    """
    t, note = _extract_note(token.strip())
    if not t:
        return None

    # Step 2: lastIndexOf('*') → quantity
    inferred_split_name = None
    star_idx = t.rfind('*')
    if star_idx > 0:
        raw_name = t[:star_idx].strip()
        qty_str  = t[star_idx + 1:].strip()
        # Check for trailing non-digit text after leading digits (e.g. "1星星人随心配粉")
        m = re.match(r'^(\d+)(.+)$', qty_str)
        if m:
            qty = max(1, int(m.group(1)))
            inferred_split_name = m.group(2).strip() or None
        else:
            try:
                qty = max(1, int(qty_str))
            except (ValueError, TypeError):
                qty = 1
        flagged = False
    else:
        raw_name = t
        qty      = 1
        flagged  = True  # no * found — no explicit quantity

    # Step 3: STOCK_IN trailing-number → box_size
    box_size = None
    if section == 'stock_in' and raw_name:
        m = re.match(r'^(.*\D)\s*(\d+)$', raw_name)
        if m and m.group(1).strip():
            box_size = int(m.group(2))
            raw_name = m.group(1).strip()

    qty_pos  = qty if section != 'cash' else 0
    qty_cash = qty if section == 'cash' else 0

    return {
        'raw_name':            raw_name,
        'qty':                 qty,
        'qty_pos':             qty_pos,
        'qty_cash':            qty_cash,
        'box_size':            box_size,
        'section':             section,
        'flagged':             flagged,
        'note':                note,
        'unknown_header':      None,
        'inferred_split_name': inferred_split_name,
    }


@bp.route('/api/sales/parse_report', methods=['POST'])
@role_required('staff')
def parse_daily_report():
    """
    Full five-layer daily report import pipeline.

    Body:  { text: <raw pasted text>, store_code: <str> }
    Returns three buckets — confirmed (score≥80), review (50–79), failed (<50 or no match) —
    plus detected_date, store, and any unknown section headers that need user classification.
    Nothing is written to daily_sales until the caller POSTs to /api/sales/submit_daily_report.
    """
    data       = request.get_json(silent=True) or {}
    raw_text   = data.get('text', '')
    store_code = (data.get('store_code') or 'DT').strip().upper()

    if not raw_text.strip():
        return jsonify({'error': 'text is required'}), 400

    # ── Layer 1: Pre-process ─────────────────────────────────────────────────
    text = _preprocess_text(raw_text)

    # ── Load products + aliases ──────────────────────────────────────────────
    con = get_db()
    cur = con.cursor()
    cur.execute('SELECT alias_norm, product_id FROM product_aliases')
    aliases = {r['alias_norm']: r['product_id'] for r in cur.fetchall()}
    cur.execute('SELECT alias_norm, section_type FROM section_aliases')
    section_aliases = {r['alias_norm']: r['section_type'] for r in cur.fetchall()}
    cur.execute('''
        SELECT id, sku, name_cn_en, jizhanming, price, ip_series, product_type
        FROM products WHERE jizhanming IS NOT NULL AND jizhanming != ''
    ''')
    all_products = [dict(r) for r in cur.fetchall()]
    con.close()

    # ── Engine selection: LLM front-end when configured, rules otherwise ─────
    # The LLM only reads the report (sections, names verbatim, quantities,
    # notes); product resolution below is identical for both engines. Any
    # LLM failure silently falls back to the rule parser.
    engine_req    = (data.get('engine') or '').strip().lower()
    parser_engine = 'rules'
    multi_day     = False
    llm_result    = None
    if engine_req != 'rules':
        try:
            import llm_parser
            if llm_parser.available():
                llm_result = llm_parser.parse_report_llm(raw_text)
        except Exception:
            llm_result = None

    if llm_result:
        parser_engine       = 'llm'
        detected_date       = llm_result.get('date')
        store_code          = (llm_result.get('store') or store_code).upper()
        cash_total_reported = llm_result.get('cash_total')
        multi_day           = bool(llm_result.get('extra_dates'))

        raw_items:        list[dict] = []
        unknown_sections: list[str] = []
        for li in llm_result['items']:
            sec = li['section']
            if sec == 'skip':
                continue
            hdr = li.get('header_text') or ''
            if sec == 'unknown' and hdr and hdr not in unknown_sections:
                unknown_sections.append(hdr)
            q       = li.get('qty')
            flagged = q is None
            qty     = q if q else 1
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
        return _finish_parse(detected_date, store_code, raw_items, unknown_sections,
                             cash_total_reported, parser_engine, multi_day,
                             aliases, all_products)

    lines = text.split('\n')

    # Extract date/store from first non-empty line
    detected_date = None
    first_line    = next((ln.strip() for ln in lines if ln.strip()), '')
    if first_line:
        detected_date, store_code = _extract_date_store(first_line, store_code)

    # Report-header/date lines ("2026.04.01 DT汇总") are metadata, not products —
    # skip them without disturbing the current section.
    skipped_lines = {
        i for i, line in enumerate(lines)
        if line.strip() and _DATE_RE.search(line) and '*' not in line
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

    def _capture_cash_total(text_piece: str) -> None:
        nonlocal cash_total_reported
        m = re.search(r'(\d+(?:\.\d+)?)', text_piece)
        if m:
            cash_total_reported = float(m.group(1))

    def _parse_content(content: str, sec: str, hdr: str | None = None) -> None:
        for token in _COMMA_SPLIT_RE.split(content):
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

    return _finish_parse(detected_date, store_code, raw_items, unknown_sections,
                         cash_total_reported, parser_engine, multi_day,
                         aliases, all_products)


def _finish_parse(detected_date, store_code, raw_items, unknown_sections,
                  cash_total_reported, parser_engine, multi_day,
                  aliases, all_products):
    """Layers 4+5 — shared by both engines: alias lookup + fuzzy match +
    bucketing, stock plausibility flags, and the response payload."""
    confirmed: list[dict] = []
    review:    list[dict] = []
    failed:    list[dict] = []

    def _resolve_hits(name: str, raw: str) -> list:
        """Alias lookup then fuzzy match; applies re-ranker when model is ready."""
        qn = _norm_jzm(_clean_jzm(name))
        if qn and qn in aliases:
            pid = aliases[qn]
            p = next((x for x in all_products if x['id'] == pid), None)
            return [(100, p)] if p else []
        fuzz_hits = match_jzm(raw, all_products, aliases, threshold=_SCORE_REVIEW, limit=5)
        if fuzz_hits:
            try:
                import ranker as _ranker
                fuzz_hits = _ranker.rerank(fuzz_hits, qn, store_code)
            except Exception:
                pass
        return fuzz_hits

    def _bucket(item: dict, hits: list) -> None:
        """Place a resolved item into the correct bucket."""
        if not hits:
            failed.append({**item, 'reason': 'no_match', 'score': 0, 'candidates': []})
            return
        top_score, top_product = hits[0]
        runner_up  = hits[1][0] if len(hits) > 1 else 0
        dominant   = top_score == 100 or (top_score - runner_up) >= _MARGIN_CONFIRM
        warn_blank = not (top_product.get('jizhanming') or '').strip()
        is_inferred = item.get('reason') == 'inferred_split'
        if top_score >= _SCORE_CONFIRMED and dominant and not is_inferred:
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

        hits = _resolve_hits(raw_name, raw_name)
        _bucket(item, hits)

        # Issue 2: inferred split — trailing text after qty digits
        split_name = item.get('inferred_split_name')
        if split_name:
            split_hits = _resolve_hits(split_name, split_name)
            if split_hits and split_hits[0][0] >= 60:
                split_item = {
                    'raw_name':            split_name,
                    'qty':                 1,
                    'qty_pos':             0 if item.get('section') == 'cash' else 1,
                    'qty_cash':            1 if item.get('section') == 'cash' else 0,
                    'box_size':            None,
                    'section':             item.get('section'),
                    'flagged':             True,
                    'note':                '',
                    'unknown_header':      None,
                    'inferred_split_name': None,
                    'reason':              'inferred_split',
                }
                _bucket(split_item, split_hits)

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
        con2.close()

    return jsonify({
        'detected_date':       detected_date,
        'store':               store_code,
        'confirmed':           confirmed,
        'review':              review,
        'failed':              failed,
        'unknown_sections':    unknown_sections,
        'cash_total_reported': cash_total_reported,
        'parser_engine':       parser_engine,
        'multi_day':           multi_day,
    })
