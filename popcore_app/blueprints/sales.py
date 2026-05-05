"""
blueprints/sales.py — daily sales records, batch import, daily report, export.
"""
from datetime import date, timedelta
from flask import Blueprint, request, jsonify, Response

from db import get_db, esc_csv, _ensure_stock_row
from auth import login_required, role_required

bp = Blueprint('sales', __name__)


@bp.route('/api/sales')
@login_required
def get_sales():
    d = request.args.get('date', str(date.today()))
    con = get_db()
    cur = con.cursor()
    cur.execute('''
        SELECT ds.id, ds.product_id, ds.date, ds.qty_sold, ds.qty_pos, ds.qty_cash, ds.notes,
               p.sku, p.name_cn_en, p.jizhanming, p.price, p.ip_series
        FROM daily_sales ds
        JOIN products p ON p.id = ds.product_id
        WHERE ds.date = ?
        ORDER BY p.ip_series, p.jizhanming
    ''', (d,))
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
    cur = con.cursor()
    cur.execute('''
        INSERT INTO daily_sales (product_id, date, qty_pos, qty_cash, qty_sold, notes)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(product_id, date) DO UPDATE SET
            qty_pos  = excluded.qty_pos,
            qty_cash = excluded.qty_cash,
            qty_sold = excluded.qty_sold,
            notes    = excluded.notes
    ''', (pid, d, qty_pos, qty_cash, qty_sold, notes))
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
    cur = con.cursor()
    cur.execute('''
        INSERT OR IGNORE INTO daily_sales (product_id, date, qty_sold)
        VALUES (?, ?, 0)
    ''', (pid, d))
    con.commit()
    con.close()
    return jsonify({'ok': True})


@bp.route('/api/sales/summary')
@login_required
def sales_summary():
    con = get_db()
    cur = con.cursor()
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
    Body: { operation: 'ru_dian'|'restock_upstairs'|'out_dian'|'ru_dian_claw',
            date: '...', items: [{product_id, qty, notes}] }
    """
    data      = request.get_json()
    operation = data.get('operation', 'ru_dian')
    d         = data.get('date', str(date.today()))
    items     = data.get('items', [])

    if operation not in ('ru_dian', 'restock_upstairs', 'out_dian', 'ru_dian_claw'):
        return jsonify({'error': 'Invalid operation'}), 400

    con = get_db()
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

        _ensure_stock_row(cur, pid)

        if operation == 'ru_dian':
            cur.execute('SELECT upstairs_qty FROM stock WHERE product_id = ?', (pid,))
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
                WHERE product_id = ?
            ''', (qty, qty, d, pid))
            loc = 'upstairs->instore'
        elif operation == 'out_dian':
            cur.execute('SELECT instore_qty FROM stock WHERE product_id = ?', (pid,))
            row = cur.fetchone()
            instore = row['instore_qty'] if row else 0
            if qty > instore:
                results.append({'pid': pid, 'ok': False,
                                 'error': f'店内库存不足（{instore}）'})
                continue
            cur.execute('''
                UPDATE stock SET instore_qty = instore_qty - ?,
                                 last_updated = ?
                WHERE product_id = ?
            ''', (qty, d, pid))
            loc = 'instore_out'
        elif operation == 'ru_dian_claw':
            cur.execute('SELECT upstairs_qty FROM stock WHERE product_id = ?', (pid,))
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
                WHERE product_id = ?
            ''', (qty, qty, qty, d, pid))
            loc = 'upstairs->claw'
        else:  # restock_upstairs
            cur.execute('''
                UPDATE stock SET upstairs_qty = upstairs_qty + ?,
                                 last_updated = ?
                WHERE product_id = ?
            ''', (qty, d, pid))
            loc = 'upstairs'

        cur.execute('''
            INSERT INTO stock_transactions (product_id, txn_type, qty, location, date, notes)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (pid, operation, qty, loc, d, notes))
        results.append({'pid': pid, 'ok': True})

    con.commit()
    con.close()
    return jsonify({'ok': True, 'results': results})


@bp.route('/api/sales/batch_upsert', methods=['POST'])
@role_required('staff')
def batch_upsert_sales():
    items = request.get_json()
    if not isinstance(items, list):
        return jsonify({'error': 'Expected a list'}), 400

    rows = []
    for item in items:
        try:
            pid      = int(item['product_id'])
            qty_pos  = int(item.get('qty_pos',  0) or 0)
            qty_cash = int(item.get('qty_cash', 0) or 0)
        except (KeyError, ValueError, TypeError):
            return jsonify({'error': 'Each item must have an integer product_id, qty_pos, and qty_cash'}), 400
        rows.append((pid, item.get('date', str(date.today())),
                     qty_pos, qty_cash, qty_pos + qty_cash, item.get('notes', '')))

    con = get_db()
    cur = con.cursor()
    cur.executemany('''
        INSERT INTO daily_sales (product_id, date, qty_pos, qty_cash, qty_sold, notes)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(product_id, date) DO UPDATE SET
            qty_pos  = excluded.qty_pos,
            qty_cash = excluded.qty_cash,
            qty_sold = excluded.qty_sold,
            notes    = CASE WHEN excluded.notes != '' THEN excluded.notes ELSE notes END
    ''', rows)
    con.commit()
    con.close()
    return jsonify({'ok': True, 'count': len(rows)})


@bp.route('/api/sales/submit_daily_report', methods=['POST'])
@role_required('staff')
def submit_daily_report():
    """
    Submit a parsed daily report in one atomic transaction.

    Body:
    {
      "date":  "2026-04-01",
      "store": "DT",
      "items": [
        { "product_id": <int>, "section": "pos"|"cash"|"claw"|"sell_display"
                               |"employee_discount"|"break_display"|"stock_in",
          "qty_pos": <int>, "qty_cash": <int>, "qty": <int>,
          "box_size": <int>, "num_boxes": <int>, "notes": <str> }
      ]
    }
    """
    data  = request.get_json(silent=True) or {}
    d     = (data.get('date') or '').strip()
    store = (data.get('store') or 'DT').strip()
    items = data.get('items')

    if not d or not isinstance(items, list):
        return jsonify({'error': 'date and items required'}), 400

    SALES_SECTIONS = {'pos', 'cash', 'claw', 'sell_display', 'employee_discount'}

    con = get_db()
    cur = con.cursor()
    sales_count = 0
    txn_count   = 0

    try:
        for item in items:
            pid     = item.get('product_id')
            section = (item.get('section') or '').strip()
            notes   = (item.get('notes') or '').strip()
            if not pid or not section:
                continue

            if section in SALES_SECTIONS:
                qty_pos  = int(item.get('qty_pos',  0) or 0)
                qty_cash = int(item.get('qty_cash', 0) or 0)
                qty_sold = qty_pos + qty_cash

                tag = notes
                if section == 'employee_discount' and not tag:
                    tag = 'employee_discount'
                elif section == 'sell_display' and not tag:
                    tag = 'display_sold'
                elif section == 'claw' and not tag:
                    tag = 'claw_machine'

                cur.execute('''
                    INSERT INTO daily_sales
                        (product_id, date, store, qty_pos, qty_cash, qty_sold, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(product_id, date) DO UPDATE SET
                        qty_pos  = qty_pos  + excluded.qty_pos,
                        qty_cash = qty_cash + excluded.qty_cash,
                        qty_sold = qty_sold + excluded.qty_sold,
                        notes    = CASE
                            WHEN notes = '' THEN excluded.notes
                            WHEN excluded.notes = '' THEN notes
                            ELSE notes || '; ' || excluded.notes
                        END
                ''', (pid, d, store, qty_pos, qty_cash, qty_sold, tag))
                sales_count += 1

            elif section == 'break_display':
                qty = int(item.get('qty', 1) or 1)
                cur.execute('''
                    INSERT INTO stock_transactions
                        (product_id, txn_type, qty, location, date, notes)
                    VALUES (?, 'display_open', ?, 'instore', ?, ?)
                ''', (pid, -qty, d, notes or 'display opened'))
                cur.execute('''
                    UPDATE stock SET instore_qty = MAX(0, instore_qty - ?),
                                     last_updated = datetime('now')
                    WHERE product_id = ?
                ''', (qty, pid))
                txn_count += 1

            elif section == 'stock_in':
                box_size  = int(item.get('box_size',  1) or 1)
                num_boxes = int(item.get('num_boxes', 1) or 1)
                total_dan = box_size * num_boxes
                cur.execute('SELECT product_type, boxes_per_dan FROM products WHERE id=?', (pid,))
                prow = cur.fetchone()
                bpd  = (prow['boxes_per_dan'] or 1) if (prow and prow['product_type'] == '盲盒') else 1
                total_units = total_dan * bpd
                cur.execute('''
                    INSERT INTO stock_transactions
                        (product_id, txn_type, qty, location, date, notes)
                    VALUES (?, 'report_stock_in', ?, 'upstairs→instore', ?, ?)
                ''', (pid, total_units, d, notes or f'{num_boxes}箱×{box_size}端'))
                cur.execute('''
                    INSERT INTO stock (product_id, upstairs_qty, instore_qty)
                    VALUES (?, 0, ?)
                    ON CONFLICT(product_id) DO UPDATE SET
                        upstairs_qty = MAX(0, upstairs_qty - ?),
                        instore_qty  = instore_qty + ?,
                        last_updated = datetime('now')
                ''', (pid, total_units, total_units, total_units))
                txn_count += 1

        con.commit()
    except Exception as e:
        con.rollback()
        con.close()
        return jsonify({'error': str(e)}), 500

    con.close()
    return jsonify({'ok': True, 'sales_upserted': sales_count, 'stock_transactions': txn_count})


@bp.route('/api/sales/export')
@role_required('manager')
def export_sales():
    from_date = request.args.get('from', '')
    to_date   = request.args.get('to',   str(date.today()))
    if not from_date:
        from_date = str(date.today() - timedelta(days=30))

    con = get_db()
    cur = con.cursor()
    cur.execute('''
        SELECT ds.date, p.jizhanming, p.sku, p.ip_series, p.product_type,
               p.price, ds.qty_pos, ds.qty_cash, ds.qty_sold, ds.notes
        FROM daily_sales ds
        JOIN products p ON p.id = ds.product_id
        WHERE ds.date BETWEEN ? AND ?
        ORDER BY ds.date DESC, p.ip_series, p.jizhanming
    ''', (from_date, to_date))
    rows = cur.fetchall()
    con.close()

    header = '日期,记账名,SKU,系列,类型,单价,卡机数量,现金/转账数量,总销量,备注'
    lines  = ['﻿' + header]
    for r in rows:
        lines.append(','.join(esc_csv(v) for v in [
            r['date'], r['jizhanming'], r['sku'], r['ip_series'], r['product_type'],
            r['price'], r['qty_pos'], r['qty_cash'], r['qty_sold'], r['notes']
        ]))

    csv_content = '\n'.join(lines)
    fname = f'sales_{from_date}_{to_date}.csv'
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
    con = get_db()
    cur = con.cursor()
    cur.execute('DELETE FROM daily_sales WHERE date = ?', (d,))
    deleted = cur.rowcount
    con.commit()
    con.close()
    return jsonify({'ok': True, 'deleted': deleted})
