"""
blueprints/products.py — product catalogue, search, aliases, hidden images, export.
"""
import os
import re
import uuid
import sqlite3
from datetime import date, datetime
from flask import Blueprint, request, jsonify, Response
from werkzeug.utils import secure_filename

from db import get_db, esc_csv, HIDDEN_IMG_DIR
from auth import login_required, role_required
from matcher import match_jzm, batch_match_jzm, match_name, normalize as norm_jzm, clean_name as _clean_jzm, _score_pair_jzm

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

@bp.route('/api/products/aliases', methods=['GET'])
@login_required
def list_all_aliases():
    """Return all product aliases joined with product info, for the alias management UI."""
    con = get_db()
    cur = con.cursor()
    cur.execute('''
        SELECT pa.id, pa.alias, pa.alias_norm, pa.created_by, pa.created_at,
               p.id AS product_id, p.jizhanming, p.sku
        FROM product_aliases pa
        JOIN products p ON p.id = pa.product_id
        ORDER BY pa.created_at DESC
    ''')
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return jsonify(rows)


@bp.route('/api/products/aliases', methods=['POST'])
@login_required
def save_alias():
    data = request.get_json(silent=True) or {}
    pid  = data.get('product_id')
    alias = (data.get('alias') or '').strip()
    if not pid or not alias:
        return jsonify({'error': 'product_id and alias required'}), 400
    alias_norm = norm_jzm(_clean_jzm(alias))
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


# ─── Google Sheet Sync ────────────────────────────────────────────────────────

_SHEET_ID = '1bUXTNiFH0iGd4YLrhQ1KJjeGTsbtAyDwp7hVEGFWFwM'


def _get_sheet_token():
    """Return a short-lived Bearer token from the service account credentials."""
    sa_path = os.environ.get('GOOGLE_SERVICE_ACCOUNT_PATH', '')
    if not sa_path or not os.path.exists(sa_path):
        raise RuntimeError('GOOGLE_SERVICE_ACCOUNT_PATH is not set or file does not exist')
    from google.oauth2.service_account import Credentials
    from google.auth.transport.requests import Request as GoogleAuthRequest
    creds = Credentials.from_service_account_file(
        sa_path,
        scopes=['https://www.googleapis.com/auth/spreadsheets.readonly'],
    )
    creds.refresh(GoogleAuthRequest())
    return creds.token


def _run_duplicate_scan(all_products):
    """
    Scan all_products for near-duplicate jizhanming entries.

    ≤500 products: O(n²) pairwise with _score_pair_jzm on normalised strings.
    >500 products: query-per-product via match_jzm to avoid comparing all pairs.

    Returns [{product_a, product_b, score, severity}] sorted by score desc.
    """
    jzm_products = [p for p in all_products if (p.get('jizhanming') or '').strip()]
    if len(jzm_products) < 2:
        return []

    dup_pairs = []

    if len(jzm_products) <= 500:
        norms = [(norm_jzm(p['jizhanming']), p) for p in jzm_products]
        for i in range(len(norms)):
            qn_i, p_i = norms[i]
            for j in range(i + 1, len(norms)):
                qn_j, p_j = norms[j]
                score = _score_pair_jzm(qn_i, qn_j)
                if score >= 70:
                    dup_pairs.append({
                        'product_a': {'id': p_i['id'], 'sku': p_i['sku'], 'jizhanming': p_i['jizhanming']},
                        'product_b': {'id': p_j['id'], 'sku': p_j['sku'], 'jizhanming': p_j['jizhanming']},
                        'score':     score,
                        'severity':  'likely' if score >= 85 else 'possible',
                    })
    else:
        seen = set()
        for p_i in jzm_products:
            hits = match_jzm(p_i['jizhanming'], jzm_products, threshold=70, limit=10)
            for score, candidate in hits:
                if candidate['id'] == p_i['id']:
                    continue
                pair_key = (min(p_i['id'], candidate['id']), max(p_i['id'], candidate['id']))
                if pair_key in seen:
                    continue
                seen.add(pair_key)
                dup_pairs.append({
                    'product_a': {'id': p_i['id'], 'sku': p_i['sku'], 'jizhanming': p_i['jizhanming']},
                    'product_b': {'id': candidate['id'], 'sku': candidate['sku'], 'jizhanming': candidate.get('jizhanming', '')},
                    'score':     score,
                    'severity':  'likely' if score >= 85 else 'possible',
                })

    dup_pairs.sort(key=lambda x: -x['score'])
    return dup_pairs


def _fetch_sheet_rows():
    """Fetch A:D from the sheet. Returns (status, rows) where status is
    'ok' | 'unavailable' (no credentials) | 'error' (API/transport failure).
    A failed fetch must never masquerade as an empty-but-successful sync."""
    import requests as http_req
    try:
        token = _get_sheet_token()
    except Exception:
        return 'unavailable', []
    try:
        url  = f'https://sheets.googleapis.com/v4/spreadsheets/{_SHEET_ID}/values/A:D'
        resp = http_req.get(url, headers={'Authorization': f'Bearer {token}'}, timeout=30)
        if not resp.ok:
            return 'error', []
        return 'ok', resp.json().get('values', [])
    except Exception:
        return 'error', []


@bp.route('/api/products/sync-sheet', methods=['POST'])
@role_required('admin')
def sync_sheet_preview():
    """
    C-anchored sheet sync preview.

    The sheet is the 记账名 mapping table: col A = 编号 (sheet's stable id),
    col B = 记账名 (bookkeeping shorthand), col C = actual product name.
    Identity is resolved per row in this order:
      1. stored sheet_ref == col A            → exact, learned key
      2. col C fuzzy-matched vs name_cn_en    → the name column carries identity
      3. col B fuzzy-matched (legacy fallback, only when C is empty/unmatched)
    and col B is what gets WRITTEN (into products.jizhanming).

    Buckets: changed (rename, prechecked when high-confidence), review
    (rename, human must opt in), conflicts (multiple rows claim one product,
    or stored ref disagrees), new_products (row matches nothing — candidate
    to create), ref_learns (matched, nothing to rename, but the 编号 can be
    remembered), unchanged. Comparison is normalize()-based so case/spacing
    differences don't churn.
    """
    con = get_db()
    cur = con.cursor()
    cur.execute('''SELECT id, sku, name_cn_en, jizhanming, sheet_ref, price,
                          ip_series, product_type FROM products''')
    all_products = [dict(r) for r in cur.fetchall()]
    cur.execute('SELECT alias_norm, product_id FROM product_aliases')
    aliases = {r['alias_norm']: r['product_id'] for r in cur.fetchall()}
    con.close()

    # Duplicate scan always runs, even when the sheet is unreachable
    duplicates = _run_duplicate_scan(all_products)

    sheet_status, rows = _fetch_sheet_rows()

    changed:      list = []
    review:       list = []
    conflicts:    list = []
    new_products: list = []
    ref_learns:   list = []
    unchanged     = 0

    if sheet_status == 'ok':
        ref_map = {p['sheet_ref']: p for p in all_products
                   if (p.get('sheet_ref') or '').strip()}
        per_pid: dict[int, list] = {}

        for i, row in enumerate(rows[1:], start=2):   # skip header; key = sheet row no.
            ref    = (str(row[0]).strip() if len(row) > 0 and row[0] is not None else '')
            jzm_b  = (str(row[1]).strip() if len(row) > 1 and row[1] is not None else '')
            name_c = (str(row[2]).strip() if len(row) > 2 and row[2] is not None else '')
            if not jzm_b and not name_c:
                continue

            target, via, score = None, None, 0
            if ref and ref in ref_map:
                target, via, score = ref_map[ref], 'ref', 100
            if target is None and name_c:
                hits = match_name(name_c, all_products, threshold=60, limit=1)
                if hits:
                    score, target = hits[0]
                    via = 'name'
            if target is None and jzm_b:
                hits = match_jzm(jzm_b, all_products, aliases=aliases, threshold=60, limit=1)
                if hits:
                    score, target = hits[0]
                    via = 'jzm_b'

            if target is None:
                new_products.append({'key': i, 'ref': ref,
                                     'jizhanming': jzm_b, 'name': name_c})
                continue

            per_pid.setdefault(target['id'], []).append({
                'key':              i,
                'ref':              ref,
                'sheet_jizhanming': jzm_b,
                'sheet_name':       name_c,
                'match_via':        via,
                'score':            score,
                'target':           target,
            })

        for pid, entries in per_pid.items():
            target = entries[0]['target']
            base = {'product_id': pid, 'sku': target['sku'],
                    'product_jizhanming': (target.get('jizhanming') or '').strip()}

            if len(entries) > 1:
                conflicts.append({
                    'key':    f'multi-{pid}',
                    'reason': 'multi_row',
                    **base,
                    'rows': [{k: e[k] for k in
                              ('key', 'ref', 'sheet_jizhanming', 'sheet_name', 'match_via', 'score')}
                             for e in entries],
                })
                continue

            e = entries[0]
            stored_ref = (target.get('sheet_ref') or '').strip()
            if e['ref'] and stored_ref and e['ref'] != stored_ref:
                conflicts.append({
                    'key':    f'ref-{pid}',
                    'reason': 'ref_mismatch',
                    **base,
                    'stored_ref': stored_ref,
                    'rows': [{k: e[k] for k in
                              ('key', 'ref', 'sheet_jizhanming', 'sheet_name', 'match_via', 'score')}],
                })
                continue

            old_jzm       = base['product_jizhanming']
            rename_needed = bool(e['sheet_jizhanming']) and \
                norm_jzm(_clean_jzm(old_jzm)) != norm_jzm(_clean_jzm(e['sheet_jizhanming']))
            high_conf   = e['match_via'] == 'ref' or e['score'] >= 95
            ref_learnable = bool(e['ref']) and not stored_ref

            if rename_needed:
                entry = {
                    'key':              e['key'],
                    'ref':              e['ref'],
                    'sheet_jizhanming': e['sheet_jizhanming'],
                    'sheet_name':       e['sheet_name'],
                    'product_id':       pid,
                    'sku':              target['sku'],
                    'old_jizhanming':   old_jzm,
                    'new_jizhanming':   e['sheet_jizhanming'],
                    'match_via':        e['match_via'],
                    'score':            e['score'],
                    'prechecked':       high_conf,
                    'sheet_ref':        e['ref'] if ref_learnable else '',
                }
                (changed if high_conf else review).append(entry)
            else:
                unchanged += 1
                if ref_learnable and high_conf:
                    ref_learns.append({
                        'product_id': pid,
                        'sku':        target['sku'],
                        'jizhanming': old_jzm,
                        'sheet_ref':  e['ref'],
                    })

    return jsonify({
        'sheet_status': sheet_status,
        'changed':      changed,
        'review':       review,
        'conflicts':    conflicts,
        'new_products': new_products,
        'ref_learns':   ref_learns,
        'unchanged':    unchanged,
        'duplicates':   duplicates,
    })


def _apply_sheet_ref(con, product_id: int, sheet_ref: str) -> bool:
    """Set products.sheet_ref when the product has none yet. The partial
    unique index rejects a ref already claimed by another product."""
    if not sheet_ref:
        return False
    try:
        cur = con.execute(
            "UPDATE products SET sheet_ref = ? WHERE id = ? "
            "AND (sheet_ref IS NULL OR sheet_ref = '')",
            (sheet_ref, product_id))
        return cur.rowcount > 0
    except sqlite3.IntegrityError:
        return False


@bp.route('/api/products/sync-sheet/confirm', methods=['POST'])
@role_required('admin')
def sync_sheet_confirm():
    """
    Apply a previewed sync: rename checked rows (learning their 编号 and
    keeping the OLD jizhanming as an alias so historical reports still
    match), remember 编号 for ref_learns, and create checked new products
    with auto-generated SKUs.
    """
    data            = request.get_json(silent=True) or {}
    changes         = data.get('changes', []) or []
    review_accepted = data.get('review_accepted', []) or []
    ref_learns      = data.get('ref_learns', []) or []
    creates         = data.get('create_products', []) or []
    all_changes     = list(changes) + list(review_accepted)

    con = get_db()
    cur = con.cursor()
    updated = created = refs_learned = 0

    for change in all_changes:
        product_id = change.get('product_id')
        new_jzm    = (change.get('new_jizhanming') or '').strip()
        if not product_id or not new_jzm:
            continue
        cur.execute('SELECT * FROM products WHERE id = ?', (product_id,))
        row = cur.fetchone()
        if not row:
            continue
        product = dict(row)
        old_jzm = (product.get('jizhanming') or '').strip()
        search_blob = ' '.join([
            (product.get('sku')          or '').lower(),
            new_jzm.lower(),
            (product.get('name_cn_en')   or '').lower(),
            (product.get('brand')        or '').lower(),
            (product.get('product_type') or '').lower(),
            (product.get('ip_series')    or '').lower(),
        ])
        con.execute(
            'UPDATE products SET jizhanming = ?, search_blob = ? WHERE id = ?',
            (new_jzm, search_blob, product_id),
        )
        # Keep the old shorthand alive as an alias — historical daily reports
        # written with the old 记账名 must keep matching this product.
        old_norm = norm_jzm(_clean_jzm(old_jzm))
        if old_norm and old_norm != norm_jzm(_clean_jzm(new_jzm)):
            con.execute('''
                INSERT OR IGNORE INTO product_aliases (product_id, alias, alias_norm, created_by)
                VALUES (?, ?, ?, 'sheet_sync')
            ''', (product_id, old_jzm, old_norm))
        if _apply_sheet_ref(con, product_id, (change.get('sheet_ref') or '').strip()):
            refs_learned += 1
        updated += 1

    for rl in ref_learns:
        pid = rl.get('product_id')
        ref = (rl.get('sheet_ref') or '').strip()
        if pid and ref and _apply_sheet_ref(con, pid, ref):
            refs_learned += 1

    if creates:
        cur.execute("SELECT sku FROM products WHERE sku LIKE 'SP%' ORDER BY sku DESC LIMIT 1")
        row = cur.fetchone()
        try:
            next_num = int((row[0].replace('SP', '').lstrip('0') or '0')) + 1 if row else 1
        except ValueError:
            next_num = None   # non-numeric SKU scheme — cannot auto-create
        for cp in creates:
            if next_num is None:
                break
            jzm  = (cp.get('jizhanming') or '').strip()
            name = (cp.get('name_cn_en') or cp.get('name') or '').strip()
            ref  = (cp.get('sheet_ref') or cp.get('ref') or '').strip()
            if not jzm and not name:
                continue
            sku = f'SP{next_num:05d}'
            next_num += 1
            search_blob = ' '.join([sku.lower(), jzm.lower(), name.lower()])
            try:
                con.execute('''
                    INSERT INTO products (sku, name_cn_en, jizhanming, search_blob, notes)
                    VALUES (?, ?, ?, ?, '')
                ''', (sku, name, jzm, search_blob))
                created += 1
                if ref:
                    pid_new = con.execute('SELECT id FROM products WHERE sku = ?', (sku,)).fetchone()
                    if pid_new and _apply_sheet_ref(con, pid_new['id'], ref):
                        refs_learned += 1
            except sqlite3.IntegrityError:
                continue

    now_str = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
    con.execute(
        "INSERT OR REPLACE INTO app_settings (key, value) VALUES ('last_sheet_sync_at', ?)",
        (now_str,),
    )
    con.execute(
        "INSERT OR REPLACE INTO app_settings (key, value) VALUES ('last_sheet_sync_count', ?)",
        (str(updated + created),),
    )
    con.commit()
    con.close()

    try:
        import ranker
        ranker.invalidate_cache()
    except Exception:
        pass

    return jsonify({'ok': True, 'updated': updated, 'created': created,
                    'refs_learned': refs_learned})


@bp.route('/api/products/sync-sheet/last-sync')
@role_required('admin')
def sync_sheet_last_sync():
    con = get_db()
    cur = con.cursor()
    cur.execute(
        "SELECT key, value FROM app_settings WHERE key IN ('last_sheet_sync_at', 'last_sheet_sync_count')"
    )
    rows = {r['key']: r['value'] for r in cur.fetchall()}
    con.close()
    return jsonify({
        'last_sync_at':    rows.get('last_sheet_sync_at'),
        'last_sync_count': rows.get('last_sheet_sync_count'),
    })


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
