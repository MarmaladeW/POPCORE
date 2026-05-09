"""
blueprints/products.py — product catalogue, search, aliases, hidden images, export.
"""
import os
import re
import uuid
import sqlite3
from datetime import date
from flask import Blueprint, request, jsonify, Response
from werkzeug.utils import secure_filename

from db import get_db, esc_csv, HIDDEN_IMG_DIR
from auth import login_required, role_required
from matcher import match_jzm, batch_match_jzm, normalize as norm_jzm

bp = Blueprint('products', __name__)

ALLOWED_IMG_EXTS  = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.avif'}
ALLOWED_IMG_TYPES = {'general', 'small', 'large'}


def _score_product(product, tokens, q_full):
    """
    Return a relevance score (higher = better match).
    Optimised for Chinese text: bigram matching + per-field character hits.
    """
    blob = product.get('search_blob', '')
    jzm  = (product.get('jizhanming') or '').lower()
    sku  = (product.get('sku') or '').lower()
    name = (product.get('name_cn_en') or '').lower()

    score = 0

    if q_full in jzm:  score += 100
    if q_full in sku:  score += 80
    if q_full in name: score += 60

    for t in tokens:
        if t in jzm:  score += 30
        if t in sku:  score += 20
        if t in name: score += 10
        if t in blob: score += 5

    for i in range(len(q_full) - 1):
        bg = q_full[i:i+2]
        if not bg.strip():
            continue
        if bg in jzm:  score += 20
        if bg in name: score += 12
        if bg in blob: score += 6

    chars = [ch for ch in q_full if ch.strip()]
    if chars:
        score += sum(6 for ch in chars if ch in jzm)
        score += sum(3 for ch in chars if ch in name)
        score += sum(1 for ch in chars if ch in blob)
        ratio = sum(1 for ch in chars if ch in blob) / len(chars)
        if ratio >= 0.85:   score += 25
        elif ratio >= 0.65: score += 12

    return score


# ─── Product lookup & search ──────────────────────────────────────────────────

@bp.route('/api/products/by_jizhanming')
@login_required
def get_by_jizhanming():
    name = request.args.get('name', '').strip()
    if not name:
        return jsonify([])
    con = get_db()
    cur = con.cursor()
    cur.execute('SELECT alias_norm, product_id FROM product_aliases')
    aliases = {r['alias_norm']: r['product_id'] for r in cur.fetchall()}
    cur.execute('''
        SELECT id, sku, name_cn_en, jizhanming, price, ip_series, product_type
        FROM products
        WHERE jizhanming IS NOT NULL AND jizhanming != ''
    ''')
    all_products = [dict(r) for r in cur.fetchall()]
    con.close()
    matches = match_jzm(name, all_products, aliases=aliases, threshold=75, limit=5)
    return jsonify([p for _, p in matches])


@bp.route('/api/products/match', methods=['POST'])
@login_required
def batch_match_products():
    data = request.get_json(silent=True) or {}
    queries = data.get('queries', [])
    try:
        threshold = int(data.get('threshold', 75))
    except (ValueError, TypeError):
        return jsonify({'error': 'threshold must be an integer'}), 400
    if not queries:
        return jsonify({'results': []})

    con = get_db()
    cur = con.cursor()
    cur.execute('SELECT alias_norm, product_id FROM product_aliases')
    aliases = {r['alias_norm']: r['product_id'] for r in cur.fetchall()}
    cur.execute('''
        SELECT id, sku, name_cn_en, jizhanming, price, ip_series, product_type
        FROM products WHERE jizhanming IS NOT NULL AND jizhanming != ''
    ''')
    all_products = [dict(r) for r in cur.fetchall()]
    con.close()

    raw_results = batch_match_jzm(queries, all_products, aliases=aliases, threshold=threshold)
    return jsonify({'results': raw_results})


@bp.route('/api/products/search')
@login_required
def search_products():
    q             = request.args.get('q', '').strip().lower()
    series        = request.args.get('series', '').strip()
    product_type  = request.args.get('product_type', '').strip()
    limit_raw     = request.args.get('limit')
    limit         = int(limit_raw) if limit_raw else None
    include_stock = request.args.get('include_stock', '0') == '1'

    con = get_db()
    cur = con.cursor()

    filter_clauses = []
    filter_params  = []
    if series:
        filter_clauses.append("ip_series = ?")
        filter_params.append(series)
    if product_type:
        filter_clauses.append("product_type = ?")
        filter_params.append(product_type)
    filter_sql = ("AND " + " AND ".join(filter_clauses)) if filter_clauses else ""

    if not q:
        where        = ("WHERE " + " AND ".join(filter_clauses)) if filter_clauses else ""
        limit_clause = 'LIMIT ?' if limit else ''
        limit_param  = [limit] if limit else []
        cur.execute(f'''
            SELECT id, sku, name_cn_en, jizhanming, price, ip_series, product_type,
                   brand, notes, release_date, search_blob, is_bestseller
            FROM products
            {where}
            ORDER BY sku DESC
            {limit_clause}
        ''', filter_params + limit_param)
        rows = [dict(r) for r in cur.fetchall()]
        if include_stock and rows:
            pids = [r['id'] for r in rows]
            cur.execute(
                'SELECT product_id, COALESCE(upstairs_qty,0) AS upstairs_qty FROM stock'
                f' WHERE product_id IN ({",".join("?"*len(pids))})',
                pids,
            )
            sm = {r['product_id']: r['upstairs_qty'] for r in cur.fetchall()}
            for r in rows:
                r['upstairs_qty'] = sm.get(r['id'], 0)
        con.close()
        return jsonify(rows)

    tokens = q.split()

    and_conditions = " AND ".join("search_blob LIKE ?" for _ in tokens)
    and_params = [f'%{t}%' for t in tokens] + filter_params
    cur.execute(f'''
        SELECT id, sku, name_cn_en, jizhanming, price, ip_series, product_type,
               brand, notes, release_date, search_blob, is_bestseller
        FROM products
        WHERE {and_conditions} {filter_sql}
        LIMIT 200
    ''', and_params)
    and_rows = [dict(r) for r in cur.fetchall()]

    or_conditions = " OR ".join("search_blob LIKE ?" for _ in tokens)
    or_params = [f'%{t}%' for t in tokens] + filter_params
    cur.execute(f'''
        SELECT id, sku, name_cn_en, jizhanming, price, ip_series, product_type,
               brand, notes, release_date, search_blob, is_bestseller
        FROM products
        WHERE ({or_conditions}) {filter_sql}
        LIMIT 200
    ''', or_params)
    or_rows = [dict(r) for r in cur.fetchall()]

    char_conditions = " AND ".join("search_blob LIKE ?" for ch in q if ch.strip())
    char_params = [f'%{ch}%' for ch in q if ch.strip()] + filter_params
    char_rows = []
    if char_conditions:
        cur.execute(f'''
            SELECT id, sku, name_cn_en, jizhanming, price, ip_series, product_type,
                   brand, notes, release_date, search_blob
            FROM products
            WHERE {char_conditions} {filter_sql}
            LIMIT 200
        ''', char_params)
        char_rows = [dict(r) for r in cur.fetchall()]

    bigrams = [q[i:i+2] for i in range(len(q) - 1) if not q[i:i+2].isspace()]
    bi_rows = []
    if bigrams:
        bi_cond   = " OR ".join("search_blob LIKE ?" for _ in bigrams)
        bi_params = [f'%{b}%' for b in bigrams] + filter_params
        cur.execute(f'''
            SELECT id, sku, name_cn_en, jizhanming, price, ip_series, product_type,
                   brand, notes, release_date, search_blob
            FROM products
            WHERE ({bi_cond}) {filter_sql}
            LIMIT 200
        ''', bi_params)
        bi_rows = [dict(r) for r in cur.fetchall()]

    seen = {}
    for r in and_rows + or_rows + char_rows + bi_rows:
        if r['id'] not in seen:
            seen[r['id']] = r

    candidates = list(seen.values())
    for c in candidates:
        c['_score'] = _score_product(c, tokens, q)
    candidates.sort(key=lambda x: -x['_score'])

    final = candidates[:limit] if limit else candidates
    for c in final:
        c.pop('search_blob', None)
        c.pop('_score', None)

    if include_stock and final:
        pids = [c['id'] for c in final]
        cur.execute(
            'SELECT product_id, COALESCE(upstairs_qty,0) AS upstairs_qty FROM stock'
            f' WHERE product_id IN ({",".join("?"*len(pids))})',
            pids,
        )
        sm = {r['product_id']: r['upstairs_qty'] for r in cur.fetchall()}
        for c in final:
            c['upstairs_qty'] = sm.get(c['id'], 0)

    con.close()
    return jsonify(final)


@bp.route('/api/products/count')
@login_required
def products_count():
    con = get_db()
    cur = con.cursor()
    cur.execute('SELECT COUNT(*) FROM products')
    n = cur.fetchone()[0]
    con.close()
    return jsonify({'count': n})


@bp.route('/api/products/<int:pid>')
@login_required
def get_product(pid):
    con = get_db()
    cur = con.cursor()
    cur.execute('SELECT * FROM products WHERE id = ?', (pid,))
    row = cur.fetchone()
    if not row:
        con.close()
        return jsonify({'error': 'Not found'}), 404
    cur.execute('SELECT id, alias FROM product_aliases WHERE product_id = ? ORDER BY id', (pid,))
    aliases = [dict(r) for r in cur.fetchall()]
    con.close()
    return jsonify({**dict(row), 'aliases': aliases})


# ─── Aliases ──────────────────────────────────────────────────────────────────

@bp.route('/api/products/aliases', methods=['POST'])
@login_required
def save_alias():
    data = request.get_json(silent=True) or {}
    pid  = data.get('product_id')
    alias = (data.get('alias') or '').strip()
    if not pid or not alias:
        return jsonify({'error': 'product_id and alias required'}), 400
    alias_norm = norm_jzm(alias)
    if not alias_norm:
        return jsonify({'error': 'alias normalises to empty'}), 400
    con = get_db()
    try:
        con.execute(
            'INSERT OR REPLACE INTO product_aliases (product_id, alias, alias_norm) VALUES (?,?,?)',
            (pid, alias, alias_norm),
        )
        con.commit()
    finally:
        con.close()
    return jsonify({'ok': True})


@bp.route('/api/products/<int:pid>/aliases/<int:alias_id>', methods=['DELETE'])
@role_required('manager')
def delete_alias(pid, alias_id):
    con = get_db()
    con.execute('DELETE FROM product_aliases WHERE id = ? AND product_id = ?', (alias_id, pid))
    con.commit()
    con.close()
    return jsonify({'ok': True})


@bp.route('/api/section-aliases', methods=['GET'])
@login_required
def get_section_aliases():
    con = get_db()
    cur = con.cursor()
    cur.execute('SELECT id, alias_norm, section_type, created_at FROM section_aliases ORDER BY created_at DESC')
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return jsonify(rows)


@bp.route('/api/section-aliases', methods=['POST'])
@login_required
def save_section_alias():
    data         = request.get_json(silent=True) or {}
    alias        = (data.get('alias') or '').strip()
    section_type = (data.get('section_type') or '').strip()
    valid_types  = {'pos', 'cash', 'stock_in', 'stock_out', 'claw',
                    'sell_display', 'break_display', 'employee_discount', 'ignore'}
    if not alias or section_type not in valid_types:
        return jsonify({'error': 'alias and valid section_type required'}), 400
    alias_norm = re.sub(r'\s+', '', alias).lower()
    if not alias_norm:
        return jsonify({'error': 'alias normalises to empty'}), 400
    con = get_db()
    try:
        con.execute(
            'INSERT OR REPLACE INTO section_aliases (alias_norm, section_type) VALUES (?,?)',
            (alias_norm, section_type),
        )
        con.commit()
    finally:
        con.close()
    return jsonify({'ok': True})


@bp.route('/api/section-aliases/<int:alias_id>', methods=['DELETE'])
@role_required('manager')
def delete_section_alias(alias_id):
    con = get_db()
    con.execute('DELETE FROM section_aliases WHERE id = ?', (alias_id,))
    con.commit()
    con.close()
    return jsonify({'ok': True})


# ─── Hidden Images API ────────────────────────────────────────────────────────

@bp.route('/api/products/<int:pid>/hidden_images')
@login_required
def list_hidden_images(pid):
    con = get_db()
    cur = con.cursor()
    cur.execute('SELECT * FROM hidden_images WHERE product_id = ? ORDER BY id', (pid,))
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return jsonify(rows)


@bp.route('/api/products/<int:pid>/hidden_images', methods=['POST'])
@role_required('manager')
def upload_hidden_image(pid):
    if 'image' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    f = request.files['image']
    img_type = request.form.get('image_type', 'general')
    if img_type not in ALLOWED_IMG_TYPES:
        img_type = 'general'

    orig_name = secure_filename(f.filename or 'img.jpg')
    ext = os.path.splitext(orig_name)[1].lower()
    if ext not in ALLOWED_IMG_EXTS:
        ext = '.jpg'
    filename = f'{uuid.uuid4().hex}{ext}'

    save_dir = os.path.join(HIDDEN_IMG_DIR, str(pid))
    os.makedirs(save_dir, exist_ok=True)
    f.save(os.path.join(save_dir, filename))

    rel = f'{pid}/{filename}'
    con = get_db()
    cur = con.cursor()
    cur.execute(
        'INSERT INTO hidden_images (product_id, image_type, filename) VALUES (?, ?, ?)',
        (pid, img_type, rel)
    )
    new_id = cur.lastrowid
    con.commit()
    con.close()
    return jsonify({'ok': True, 'id': new_id, 'filename': rel,
                    'url': f'/hidden_imgs/{rel}', 'image_type': img_type}), 201


@bp.route('/api/products/<int:pid>/hidden_images/<int:img_id>', methods=['DELETE'])
@role_required('manager')
def delete_hidden_image(pid, img_id):
    con = get_db()
    cur = con.cursor()
    cur.execute('SELECT * FROM hidden_images WHERE id = ? AND product_id = ?', (img_id, pid))
    row = cur.fetchone()
    if not row:
        con.close()
        return jsonify({'error': 'Not found'}), 404
    filepath = os.path.join(HIDDEN_IMG_DIR, row['filename'])
    if os.path.exists(filepath):
        os.remove(filepath)
    cur.execute('DELETE FROM hidden_images WHERE id = ?', (img_id,))
    con.commit()
    con.close()
    return jsonify({'ok': True})


# ─── Product CRUD ─────────────────────────────────────────────────────────────

@bp.route('/api/products/<int:pid>', methods=['PATCH'])
@role_required('manager')
def update_product(pid):
    data = request.get_json()
    allowed = {'jizhanming', 'price', 'notes', 'name_cn_en', 'product_type',
               'brand', 'release_date', 'edition_size', 'channel', 'hidden',
               'style_notes', 'boxes_per_dan', 'ip_series',
               'hidden_count', 'hidden_has_small', 'hidden_has_large',
               'hidden_prob_small', 'hidden_prob_large', 'is_bestseller'}
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return jsonify({'error': 'Nothing to update'}), 400

    con = get_db()
    cur = con.cursor()
    cur.execute('SELECT * FROM products WHERE id = ?', (pid,))
    _row = cur.fetchone()
    if _row is None:
        con.close()
        return jsonify({'error': 'Product not found'}), 404
    product = dict(_row)
    product.update(updates)
    search_blob = ' '.join([
        (product.get('sku') or '').lower(),
        (product.get('jizhanming') or '').lower(),
        (product.get('name_cn_en') or '').lower(),
        (product.get('brand') or '').lower(),
        (product.get('product_type') or '').lower(),
        (product.get('ip_series') or '').lower(),
    ])
    updates['search_blob'] = search_blob

    set_clause = ', '.join(f'{k} = ?' for k in updates)
    values = list(updates.values()) + [pid]
    cur.execute(f'UPDATE products SET {set_clause} WHERE id = ?', values)
    con.commit()
    con.close()
    return jsonify({'ok': True})


@bp.route('/api/products', methods=['POST'])
@role_required('manager')
def create_product():
    data = request.get_json()

    sku          = (data.get('sku') or '').strip().upper()
    jizhanming   = (data.get('jizhanming') or '').strip()
    name_cn_en   = (data.get('name_cn_en') or '').strip()
    price_raw    = data.get('price')
    ip_series    = (data.get('ip_series') or '').strip()
    product_type = (data.get('product_type') or '').strip()
    brand        = (data.get('brand') or '').strip()
    release_date = (data.get('release_date') or '').strip()
    edition_size = (data.get('edition_size') or '').strip()
    channel      = (data.get('channel') or '').strip()
    hidden       = (data.get('hidden') or '').strip()
    style_notes  = (data.get('style_notes') or '').strip()
    notes        = (data.get('notes') or '').strip()
    boxes_per_dan_raw = data.get('boxes_per_dan')

    if not sku and not jizhanming and not name_cn_en:
        return jsonify({'error': '至少填写SKU、记账名或产品名称'}), 400

    try:
        price = float(price_raw) if price_raw not in (None, '', 'null') else None
    except (TypeError, ValueError):
        price = None

    search_blob = ' '.join([
        sku.lower(), jizhanming.lower(), name_cn_en.lower(),
        brand.lower(), product_type.lower(), ip_series.lower(),
    ])

    con = get_db()
    cur = con.cursor()

    if not sku:
        cur.execute("SELECT sku FROM products WHERE sku LIKE 'SP%' ORDER BY sku DESC LIMIT 1")
        row = cur.fetchone()
        if row:
            try:
                last_num = int(row[0].replace('SP', '').lstrip('0') or '0')
            except ValueError:
                con.close()
                return jsonify({'error': 'Could not auto-generate SKU: existing SKU format is non-numeric. Please provide a SKU manually.'}), 400
            sku = f'SP{last_num + 1:05d}'
        else:
            sku = 'SP00001'

    try:
        boxes_per_dan = int(boxes_per_dan_raw) if boxes_per_dan_raw not in (None, '', 'null') else None
    except (TypeError, ValueError):
        boxes_per_dan = None

    try:
        cur.execute('''
            INSERT INTO products (sku, name_cn_en, jizhanming, price, ip_series, product_type,
                                  brand, release_date, edition_size, channel, hidden,
                                  style_notes, notes, boxes_per_dan, search_blob)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (sku, name_cn_en, jizhanming, price, ip_series, product_type,
              brand, release_date, edition_size, channel, hidden, style_notes, notes,
              boxes_per_dan, search_blob))
        new_id = cur.lastrowid
        con.commit()
    except sqlite3.IntegrityError:
        con.close()
        return jsonify({'error': f'SKU {sku} 已存在'}), 409

    con.close()
    return jsonify({'ok': True, 'id': new_id, 'sku': sku}), 201


@bp.route('/api/series')
@login_required
def get_series():
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT DISTINCT ip_series FROM products WHERE ip_series != '' ORDER BY ip_series")
    rows = [r[0] for r in cur.fetchall()]
    con.close()
    return jsonify(rows)


@bp.route('/api/product_types')
@login_required
def get_product_types():
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT DISTINCT product_type FROM products WHERE product_type IS NOT NULL AND product_type != '' ORDER BY product_type")
    rows = [r[0] for r in cur.fetchall()]
    con.close()
    return jsonify(rows)


# ─── Export & bulk delete ─────────────────────────────────────────────────────

@bp.route('/api/products/export')
@role_required('manager')
def export_products():
    series = request.args.get('series', '').strip()
    q      = request.args.get('q', '').strip().lower()

    con = get_db()
    cur = con.cursor()

    filters = []
    params  = []
    if series:
        filters.append("ip_series = ?")
        params.append(series)
    if q:
        for token in q.split():
            filters.append("search_blob LIKE ?")
            params.append(f'%{token}%')

    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    cur.execute(f'''
        SELECT sku, jizhanming, name_cn_en, ip_series, product_type,
               brand, price, release_date, edition_size, channel, notes
        FROM products
        {where}
        ORDER BY ip_series, sku DESC
    ''', params)

    rows = cur.fetchall()
    con.close()

    header = 'SKU,记账名,产品名称,系列,类型,品牌,单价,发售时间,版本/限量,渠道,备注'
    lines  = ['﻿' + header]
    for r in rows:
        lines.append(','.join(esc_csv(v) for v in [
            r['sku'], r['jizhanming'], r['name_cn_en'], r['ip_series'],
            r['product_type'], r['brand'], r['price'],
            r['release_date'], r['edition_size'], r['channel'], r['notes']
        ]))

    fname = f'products_{date.today()}.csv'
    return Response(
        '\n'.join(lines),
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="{fname}"'}
    )


@bp.route('/api/products/bulk_delete', methods=['POST'])
@role_required('manager')
def bulk_delete_products():
    pids = request.get_json()
    if not isinstance(pids, list) or not pids:
        return jsonify({'error': 'Expected a list of product_ids'}), 400

    con = get_db()
    cur = con.cursor()
    ph  = ','.join('?' * len(pids))

    cur.execute(f'SELECT filename FROM hidden_images WHERE product_id IN ({ph})', pids)
    for row in cur.fetchall():
        fp = os.path.join(HIDDEN_IMG_DIR, row['filename'])
        if os.path.exists(fp):
            try:
                os.remove(fp)
            except OSError:
                pass

    cur.execute(f'DELETE FROM hidden_images     WHERE product_id IN ({ph})', pids)
    cur.execute(f'DELETE FROM daily_sales        WHERE product_id IN ({ph})', pids)
    cur.execute(f'DELETE FROM stock_transactions WHERE product_id IN ({ph})', pids)
    cur.execute(f'DELETE FROM stock              WHERE product_id IN ({ph})', pids)
    cur.execute(f'DELETE FROM products           WHERE id         IN ({ph})', pids)
    deleted = cur.rowcount

    con.commit()
    con.close()
    return jsonify({'ok': True, 'deleted': deleted})
