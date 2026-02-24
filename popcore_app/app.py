"""
app.py - POPCORE Inventory Management System
Flask backend serving the single-page app.
"""
import sqlite3
import os
import json
import uuid
from datetime import date, timedelta
from flask import Flask, request, jsonify, send_from_directory, Response
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'popcore.db')
STATIC_DIR = os.path.join(BASE_DIR, 'static')
HIDDEN_IMG_DIR = os.path.join(STATIC_DIR, 'hidden_imgs')

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path='')

os.makedirs(HIDDEN_IMG_DIR, exist_ok=True)


def get_db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    return con


def migrate_db():
    """Add new columns / tables if they don't exist yet (safe to re-run)."""
    con = get_db()
    cur = con.cursor()

    cur.execute("PRAGMA table_info(products)")
    existing = {r['name'] for r in cur.fetchall()}

    new_cols = [
        ('boxes_per_dan',     'INTEGER'),
        ('hidden_count',      "TEXT    NOT NULL DEFAULT '0'"),
        ('hidden_has_small',  'INTEGER NOT NULL DEFAULT 0'),
        ('hidden_has_large',  'INTEGER NOT NULL DEFAULT 0'),
        ('hidden_prob_small', "TEXT    NOT NULL DEFAULT ''"),
        ('hidden_prob_large', "TEXT    NOT NULL DEFAULT ''"),
    ]
    for col, defn in new_cols:
        if col not in existing:
            cur.execute(f'ALTER TABLE products ADD COLUMN {col} {defn}')

    cur.executescript('''
        CREATE TABLE IF NOT EXISTS hidden_images (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL REFERENCES products(id),
            image_type TEXT    NOT NULL DEFAULT 'general',
            filename   TEXT    NOT NULL,
            created_at TEXT    DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_hidden_imgs_pid ON hidden_images(product_id);
        CREATE TABLE IF NOT EXISTS stock (
            product_id   INTEGER PRIMARY KEY REFERENCES products(id),
            upstairs_dan INTEGER NOT NULL DEFAULT 0,
            instore_dan  INTEGER NOT NULL DEFAULT 0,
            last_updated TEXT,
            notes        TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS stock_transactions (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL REFERENCES products(id),
            txn_type   TEXT NOT NULL,
            dan_qty    INTEGER NOT NULL,
            location   TEXT,
            date       TEXT NOT NULL,
            notes      TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS daily_sales (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL REFERENCES products(id),
            date       TEXT NOT NULL,
            qty_sold   INTEGER NOT NULL DEFAULT 0,
            notes      TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(product_id, date)
        );
        CREATE INDEX IF NOT EXISTS idx_daily_sales_date ON daily_sales(date);
        CREATE INDEX IF NOT EXISTS idx_daily_sales_pid  ON daily_sales(product_id);
    ''')

    # Add qty_pos / qty_cash to daily_sales if not yet present
    cur.execute("PRAGMA table_info(daily_sales)")
    ds_cols = {r['name'] for r in cur.fetchall()}
    if 'qty_pos' not in ds_cols:
        cur.execute('ALTER TABLE daily_sales ADD COLUMN qty_pos  INTEGER NOT NULL DEFAULT 0')
    if 'qty_cash' not in ds_cols:
        cur.execute('ALTER TABLE daily_sales ADD COLUMN qty_cash INTEGER NOT NULL DEFAULT 0')
        # Backfill: treat existing qty_sold as qty_cash for all legacy rows
        cur.execute('UPDATE daily_sales SET qty_cash = qty_sold WHERE qty_sold > 0')

    con.commit()
    con.close()


if os.path.exists(DB_PATH):
    migrate_db()


# ─── Serve frontend ──────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory(STATIC_DIR, 'index.html')


# ─── Products API ─────────────────────────────────────────────────────────────

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

    # ── Exact full-query match → highest bonus ──
    if q_full in jzm:  score += 100
    if q_full in sku:  score += 80
    if q_full in name: score += 60

    # ── Per-token scoring ──
    for t in tokens:
        if t in jzm:  score += 30
        if t in sku:  score += 20
        if t in name: score += 10
        if t in blob: score += 5

    # ── Bigram scoring (consecutive 2-char pairs, great for Chinese) ──
    for i in range(len(q_full) - 1):
        bg = q_full[i:i+2]
        if not bg.strip():
            continue
        if bg in jzm:  score += 20
        if bg in name: score += 12
        if bg in blob: score += 6

    # ── Character-level: hits in key fields (weighted by field importance) ──
    chars = [ch for ch in q_full if ch.strip()]
    if chars:
        score += sum(6 for ch in chars if ch in jzm)
        score += sum(3 for ch in chars if ch in name)
        score += sum(1 for ch in chars if ch in blob)
        # Ratio bonus: reward high coverage
        ratio = sum(1 for ch in chars if ch in blob) / len(chars)
        if ratio >= 0.85: score += 25
        elif ratio >= 0.65: score += 12

    return score


@app.route('/api/products/search')
def search_products():
    q = request.args.get('q', '').strip().lower()
    series = request.args.get('series', '').strip()
    limit = int(request.args.get('limit', 60))

    con = get_db()
    cur = con.cursor()

    series_clause = "AND ip_series = ?" if series else ""
    series_param  = [series] if series else []

    if not q:
        # No query — just return all (filtered by series if set)
        where = f"WHERE ip_series = ?" if series else ""
        cur.execute(f'''
            SELECT id, sku, name_cn_en, jizhanming, price, ip_series, product_type,
                   brand, notes, release_date, search_blob
            FROM products
            {where}
            ORDER BY sku DESC
            LIMIT ?
        ''', series_param + [limit])
        rows = [dict(r) for r in cur.fetchall()]
        con.close()
        return jsonify(rows)

    tokens = q.split()

    # ── Strategy 1: AND match (all tokens must appear) ────────────────────
    and_conditions = " AND ".join("search_blob LIKE ?" for _ in tokens)
    and_params = [f'%{t}%' for t in tokens] + series_param
    cur.execute(f'''
        SELECT id, sku, name_cn_en, jizhanming, price, ip_series, product_type,
               brand, notes, release_date, search_blob
        FROM products
        WHERE {and_conditions} {series_clause}
        LIMIT 200
    ''', and_params)
    and_rows = [dict(r) for r in cur.fetchall()]

    # ── Strategy 2: OR match (any token appears) — broader net ────────────
    or_conditions = " OR ".join("search_blob LIKE ?" for _ in tokens)
    or_params = [f'%{t}%' for t in tokens] + series_param
    cur.execute(f'''
        SELECT id, sku, name_cn_en, jizhanming, price, ip_series, product_type,
               brand, notes, release_date, search_blob
        FROM products
        WHERE ({or_conditions}) {series_clause}
        LIMIT 200
    ''', or_params)
    or_rows = [dict(r) for r in cur.fetchall()]

    # ── Strategy 3: character-level — each char in query appears in blob ──
    # Useful for Chinese where user types individual chars without spaces
    char_conditions = " AND ".join("search_blob LIKE ?" for ch in q if ch.strip())
    char_params = [f'%{ch}%' for ch in q if ch.strip()] + series_param
    char_rows = []
    if char_conditions:
        cur.execute(f'''
            SELECT id, sku, name_cn_en, jizhanming, price, ip_series, product_type,
                   brand, notes, release_date, search_blob
            FROM products
            WHERE {char_conditions} {series_clause}
            LIMIT 200
        ''', char_params)
        char_rows = [dict(r) for r in cur.fetchall()]

    # ── Strategy 4: bigram match — any consecutive 2-char pair of query in blob ──
    # Catches partial Chinese name matches even when not all chars present
    bigrams = [q[i:i+2] for i in range(len(q) - 1) if not q[i:i+2].isspace()]
    bi_rows = []
    if bigrams:
        bi_cond   = " OR ".join("search_blob LIKE ?" for _ in bigrams)
        bi_params = [f'%{b}%' for b in bigrams] + series_param
        cur.execute(f'''
            SELECT id, sku, name_cn_en, jizhanming, price, ip_series, product_type,
                   brand, notes, release_date, search_blob
            FROM products
            WHERE ({bi_cond}) {series_clause}
            LIMIT 200
        ''', bi_params)
        bi_rows = [dict(r) for r in cur.fetchall()]

    con.close()

    # Merge all candidates (deduplicate by id)
    seen = {}
    for r in and_rows + or_rows + char_rows + bi_rows:
        if r['id'] not in seen:
            seen[r['id']] = r

    candidates = list(seen.values())

    # Score and sort
    for c in candidates:
        c['_score'] = _score_product(c, tokens, q)

    candidates.sort(key=lambda x: -x['_score'])

    # Strip internal field and return top N
    for c in candidates:
        c.pop('search_blob', None)
        c.pop('_score', None)

    return jsonify(candidates[:limit])


@app.route('/api/products/<int:pid>')
def get_product(pid):
    con = get_db()
    cur = con.cursor()
    cur.execute('SELECT * FROM products WHERE id = ?', (pid,))
    row = cur.fetchone()
    con.close()
    if not row:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(dict(row))


# ─── Hidden Images API ────────────────────────────────────────────────────────

ALLOWED_IMG_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.avif'}
ALLOWED_IMG_TYPES = {'general', 'small', 'large'}


@app.route('/api/products/<int:pid>/hidden_images')
def list_hidden_images(pid):
    con = get_db()
    cur = con.cursor()
    cur.execute('SELECT * FROM hidden_images WHERE product_id = ? ORDER BY id', (pid,))
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return jsonify(rows)


@app.route('/api/products/<int:pid>/hidden_images', methods=['POST'])
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


@app.route('/api/products/<int:pid>/hidden_images/<int:img_id>', methods=['DELETE'])
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


# ─── Products PATCH ───────────────────────────────────────────────────────────

@app.route('/api/products/<int:pid>', methods=['PATCH'])
def update_product(pid):
    data = request.get_json()
    allowed = {'jizhanming', 'price', 'notes', 'name_cn_en', 'product_type',
               'brand', 'release_date', 'edition_size', 'channel', 'hidden',
               'style_notes', 'boxes_per_dan', 'ip_series',
               'hidden_count', 'hidden_has_small', 'hidden_has_large',
               'hidden_prob_small', 'hidden_prob_large'}
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return jsonify({'error': 'Nothing to update'}), 400

    con = get_db()
    cur = con.cursor()

    # Rebuild search_blob if key fields changed
    cur.execute('SELECT * FROM products WHERE id = ?', (pid,))
    product = dict(cur.fetchone())
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


@app.route('/api/products', methods=['POST'])
def create_product():
    """Create a brand-new product. Body: { sku, jizhanming, name_cn_en, price, ip_series, ... }"""
    data = request.get_json()

    sku         = (data.get('sku') or '').strip().upper()
    jizhanming  = (data.get('jizhanming') or '').strip()
    name_cn_en  = (data.get('name_cn_en') or '').strip()
    price_raw   = data.get('price')
    ip_series   = (data.get('ip_series') or '').strip()
    product_type= (data.get('product_type') or '').strip()
    brand       = (data.get('brand') or '').strip()
    release_date= (data.get('release_date') or '').strip()
    edition_size= (data.get('edition_size') or '').strip()
    channel     = (data.get('channel') or '').strip()
    hidden      = (data.get('hidden') or '').strip()
    style_notes = (data.get('style_notes') or '').strip()
    notes       = (data.get('notes') or '').strip()

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

    # Auto-generate SKU if missing
    if not sku:
        cur.execute("SELECT sku FROM products WHERE sku LIKE 'SP%' ORDER BY sku DESC LIMIT 1")
        row = cur.fetchone()
        if row:
            last_num = int(row[0].replace('SP', '').lstrip('0') or '0')
            sku = f'SP{last_num + 1:05d}'
        else:
            sku = 'SP00001'

    try:
        cur.execute('''
            INSERT INTO products (sku, name_cn_en, jizhanming, price, ip_series, product_type,
                                  brand, release_date, edition_size, channel, hidden,
                                  style_notes, notes, search_blob)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (sku, name_cn_en, jizhanming, price, ip_series, product_type,
              brand, release_date, edition_size, channel, hidden, style_notes, notes, search_blob))
        new_id = cur.lastrowid
        con.commit()
    except sqlite3.IntegrityError:
        con.close()
        return jsonify({'error': f'SKU {sku} 已存在'}), 409

    con.close()
    return jsonify({'ok': True, 'id': new_id, 'sku': sku}), 201


@app.route('/api/series')
def get_series():
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT DISTINCT ip_series FROM products WHERE ip_series != '' ORDER BY ip_series")
    rows = [r[0] for r in cur.fetchall()]
    con.close()
    return jsonify(rows)


# ─── Stock API ────────────────────────────────────────────────────────────────
# Stock tracks quantities in 端 (display units) per location:
#   upstairs  = 2F storage
#   instore   = 1F in-store storage
# boxes_per_dan on the product tells you how many 盒 per 端.

def _ensure_stock_row(cur, product_id):
    """Insert a stock row for product if it doesn't exist yet."""
    cur.execute('''
        INSERT OR IGNORE INTO stock (product_id, upstairs_dan, instore_dan)
        VALUES (?, 0, 0)
    ''', (product_id,))


@app.route('/api/stock')
def get_all_stock():
    """
    Return all products that have a stock row (or all products if include_all=1),
    joined with their stock quantities and product info.
    """
    include_all = request.args.get('include_all', '0') == '1'
    series = request.args.get('series', '').strip()
    q = request.args.get('q', '').strip().lower()

    con = get_db()
    cur = con.cursor()

    # Build filters
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
        # Return every product, LEFT JOIN stock so zeros show for new products
        cur.execute(f'''
            SELECT p.id, p.sku, p.name_cn_en, p.jizhanming, p.price,
                   p.ip_series, p.product_type, p.boxes_per_dan,
                   COALESCE(s.upstairs_dan, 0) AS upstairs_dan,
                   COALESCE(s.instore_dan,  0) AS instore_dan,
                   COALESCE(s.last_updated, '') AS last_updated,
                   COALESCE(s.notes, '')        AS stock_notes
            FROM products p
            LEFT JOIN stock s ON s.product_id = p.id
            WHERE 1=1 {where}
            ORDER BY p.ip_series, p.sku DESC
        ''', params)
    else:
        # Only products with a stock row
        cur.execute(f'''
            SELECT p.id, p.sku, p.name_cn_en, p.jizhanming, p.price,
                   p.ip_series, p.product_type, p.boxes_per_dan,
                   s.upstairs_dan, s.instore_dan,
                   s.last_updated, COALESCE(s.notes, '') AS stock_notes
            FROM stock s
            JOIN products p ON p.id = s.product_id
            WHERE 1=1 {where}
            ORDER BY p.ip_series, p.sku DESC
        ''', params)

    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return jsonify(rows)


@app.route('/api/stock/<int:product_id>')
def get_stock(product_id):
    """Get stock for a single product."""
    con = get_db()
    cur = con.cursor()
    cur.execute('''
        SELECT p.id, p.sku, p.name_cn_en, p.jizhanming, p.price,
               p.ip_series, p.product_type, p.boxes_per_dan,
               COALESCE(s.upstairs_dan, 0) AS upstairs_dan,
               COALESCE(s.instore_dan,  0) AS instore_dan,
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


@app.route('/api/stock/ru_dian', methods=['POST'])
def ru_dian():
    """
    入店: Move 端 from upstairs (2F) to in-store (1F).
    Body: { product_id, dan_qty, date, notes }
    dan_qty must be positive.
    """
    data = request.get_json()
    pid     = int(data['product_id'])
    qty     = int(data.get('dan_qty', 0))
    d       = data.get('date', str(date.today()))
    notes   = data.get('notes', '')

    if qty <= 0:
        return jsonify({'error': '入店数量必须大于0'}), 400

    con = get_db()
    cur = con.cursor()
    _ensure_stock_row(cur, pid)

    # Check sufficient upstairs stock
    cur.execute('SELECT upstairs_dan FROM stock WHERE product_id = ?', (pid,))
    row = cur.fetchone()
    upstairs = row['upstairs_dan'] if row else 0
    if qty > upstairs:
        con.close()
        return jsonify({'error': f'楼上库存不足（现有 {upstairs} 端）'}), 400

    # Move: upstairs -= qty, instore += qty
    cur.execute('''
        UPDATE stock
        SET upstairs_dan  = upstairs_dan - ?,
            instore_dan   = instore_dan  + ?,
            last_updated  = ?
        WHERE product_id = ?
    ''', (qty, qty, d, pid))

    # Log transaction
    cur.execute('''
        INSERT INTO stock_transactions (product_id, txn_type, dan_qty, location, date, notes)
        VALUES (?, 'ru_dian', ?, 'upstairs->instore', ?, ?)
    ''', (pid, qty, d, notes))

    con.commit()

    cur.execute('SELECT upstairs_dan, instore_dan FROM stock WHERE product_id = ?', (pid,))
    s = dict(cur.fetchone())
    con.close()
    return jsonify({'ok': True, 'upstairs_dan': s['upstairs_dan'], 'instore_dan': s['instore_dan']})


@app.route('/api/stock/restock_upstairs', methods=['POST'])
def restock_upstairs():
    """
    Receive new stock into upstairs (2F) storage — e.g. order arrived.
    Body: { product_id, dan_qty, date, notes }
    """
    data  = request.get_json()
    pid   = int(data['product_id'])
    qty   = int(data.get('dan_qty', 0))
    d     = data.get('date', str(date.today()))
    notes = data.get('notes', '')

    if qty <= 0:
        return jsonify({'error': '入库数量必须大于0'}), 400

    con = get_db()
    cur = con.cursor()
    _ensure_stock_row(cur, pid)

    cur.execute('''
        UPDATE stock
        SET upstairs_dan = upstairs_dan + ?,
            last_updated = ?
        WHERE product_id = ?
    ''', (qty, d, pid))

    cur.execute('''
        INSERT INTO stock_transactions (product_id, txn_type, dan_qty, location, date, notes)
        VALUES (?, 'restock_upstairs', ?, 'upstairs', ?, ?)
    ''', (pid, qty, d, notes))

    con.commit()
    cur.execute('SELECT upstairs_dan, instore_dan FROM stock WHERE product_id = ?', (pid,))
    s = dict(cur.fetchone())
    con.close()
    return jsonify({'ok': True, 'upstairs_dan': s['upstairs_dan'], 'instore_dan': s['instore_dan']})


@app.route('/api/stock/adjust', methods=['POST'])
def adjust_stock():
    """
    Manual adjustment (correction) of upstairs or instore count.
    Body: { product_id, location ('upstairs'|'instore'), new_dan, date, notes }
    Sets the value directly (not delta) and logs the diff.
    """
    data     = request.get_json()
    pid      = int(data['product_id'])
    location = data.get('location', 'upstairs')  # 'upstairs' or 'instore'
    new_dan  = int(data.get('new_dan', 0))
    d        = data.get('date', str(date.today()))
    notes    = data.get('notes', '')

    if location not in ('upstairs', 'instore'):
        return jsonify({'error': 'location must be upstairs or instore'}), 400

    con = get_db()
    cur = con.cursor()
    _ensure_stock_row(cur, pid)

    cur.execute('SELECT upstairs_dan, instore_dan FROM stock WHERE product_id = ?', (pid,))
    s = dict(cur.fetchone())
    old_val = s[f'{location}_dan']
    delta   = new_dan - old_val

    if location == 'upstairs':
        cur.execute('UPDATE stock SET upstairs_dan = ?, last_updated = ? WHERE product_id = ?',
                    (new_dan, d, pid))
    else:
        cur.execute('UPDATE stock SET instore_dan = ?, last_updated = ? WHERE product_id = ?',
                    (new_dan, d, pid))

    cur.execute('''
        INSERT INTO stock_transactions (product_id, txn_type, dan_qty, location, date, notes)
        VALUES (?, 'adjust', ?, ?, ?, ?)
    ''', (pid, delta, location, d, notes or f'手动调整: {old_val}→{new_dan}'))

    con.commit()
    cur.execute('SELECT upstairs_dan, instore_dan FROM stock WHERE product_id = ?', (pid,))
    s2 = dict(cur.fetchone())
    con.close()
    return jsonify({'ok': True, 'upstairs_dan': s2['upstairs_dan'], 'instore_dan': s2['instore_dan']})


@app.route('/api/stock/transactions')
def get_transactions():
    """Recent transactions for a product or all products."""
    pid   = request.args.get('product_id')
    limit = int(request.args.get('limit', 50))
    d     = request.args.get('date')

    con = get_db()
    cur = con.cursor()

    conditions = []
    params = []
    if pid:
        conditions.append('t.product_id = ?')
        params.append(int(pid))
    if d:
        conditions.append('t.date = ?')
        params.append(d)

    where = ('WHERE ' + ' AND '.join(conditions)) if conditions else ''
    params.append(limit)

    cur.execute(f'''
        SELECT t.id, t.product_id, t.txn_type, t.dan_qty, t.location,
               t.date, t.notes, t.created_at,
               p.jizhanming, p.sku, p.name_cn_en, p.boxes_per_dan
        FROM stock_transactions t
        JOIN products p ON p.id = t.product_id
        {where}
        ORDER BY t.id DESC
        LIMIT ?
    ''', params)

    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return jsonify(rows)


@app.route('/api/stock/summary')
def stock_summary():
    """Overview stats: total products in stock, totals per location."""
    con = get_db()
    cur = con.cursor()
    cur.execute('''
        SELECT
            COUNT(*) AS products_tracked,
            SUM(upstairs_dan) AS total_upstairs_dan,
            SUM(instore_dan)  AS total_instore_dan
        FROM stock
    ''')
    row = dict(cur.fetchone())

    # Low stock alert: upstairs_dan = 0 but instore > 0 (need to reorder)
    cur.execute('''
        SELECT COUNT(*) FROM stock
        WHERE upstairs_dan = 0 AND instore_dan > 0
    ''')
    row['low_stock_count'] = cur.fetchone()[0]

    # Out of stock: both 0
    cur.execute('SELECT COUNT(*) FROM stock WHERE upstairs_dan = 0 AND instore_dan = 0')
    row['out_of_stock_count'] = cur.fetchone()[0]

    con.close()
    return jsonify(row)


# ─── Daily Sales API ──────────────────────────────────────────────────────────

@app.route('/api/sales')
def get_sales():
    """Get all sales records for a given date, joined with product info."""
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


@app.route('/api/sales/upsert', methods=['POST'])
def upsert_sale():
    """Upsert a single product's sales qty for a date."""
    data     = request.get_json()
    pid      = int(data['product_id'])
    d        = data.get('date', str(date.today()))
    notes    = data.get('notes', '')
    qty_pos  = int(data.get('qty_pos',  0) or 0)
    qty_cash = int(data.get('qty_cash', 0) or 0)
    # Backward compat: if caller only sends qty_sold, treat as qty_cash
    if 'qty_pos' not in data and 'qty_cash' not in data:
        qty_cash = int(data.get('qty_sold', 0) or 0)
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


@app.route('/api/sales/add_product', methods=['POST'])
def add_product_to_sales():
    """Add a product to a date's sales list with 0 qty (idempotent)."""
    data = request.get_json()
    pid  = int(data['product_id'])
    d    = data.get('date', str(date.today()))

    con = get_db()
    cur = con.cursor()
    cur.execute('''
        INSERT OR IGNORE INTO daily_sales (product_id, date, qty_sold)
        VALUES (?, ?, 0)
    ''', (pid, d))
    con.commit()
    con.close()
    return jsonify({'ok': True})


@app.route('/api/sales/summary')
def sales_summary():
    """Recent dates with total qty sold and product count."""
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


@app.route('/api/sales/batch_upsert', methods=['POST'])
def batch_upsert_sales():
    """Upsert multiple products' sales for a date at once (paste import)."""
    items = request.get_json()  # list of {product_id, date, qty_pos, qty_cash, notes}
    if not isinstance(items, list):
        return jsonify({'error': 'Expected a list'}), 400

    con = get_db()
    cur = con.cursor()
    for item in items:
        pid      = int(item['product_id'])
        d        = item.get('date', str(date.today()))
        qty_pos  = int(item.get('qty_pos',  0) or 0)
        qty_cash = int(item.get('qty_cash', 0) or 0)
        qty_sold = qty_pos + qty_cash
        notes    = item.get('notes', '')
        cur.execute('''
            INSERT INTO daily_sales (product_id, date, qty_pos, qty_cash, qty_sold, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(product_id, date) DO UPDATE SET
                qty_pos  = excluded.qty_pos,
                qty_cash = excluded.qty_cash,
                qty_sold = excluded.qty_sold,
                notes    = CASE WHEN excluded.notes != '' THEN excluded.notes ELSE notes END
        ''', (pid, d, qty_pos, qty_cash, qty_sold, notes))
    con.commit()
    con.close()
    return jsonify({'ok': True, 'count': len(items)})


@app.route('/api/sales/export')
def export_sales():
    """Return CSV of sales records for a date range (UTF-8 BOM for Excel)."""
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

    def esc_csv(v):
        s = str(v) if v is not None else ''
        if ',' in s or '"' in s or '\n' in s:
            s = '"' + s.replace('"', '""') + '"'
        return s

    header = '日期,记账名,SKU,系列,类型,单价,卡机数量,现金/转账数量,总销量,备注'
    lines  = ['\ufeff' + header]
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


if __name__ == '__main__':
    if not os.path.exists(DB_PATH):
        print('Database not found. Run init_db.py first.')
    else:
        import socket
        hostname = socket.gethostname()
        try:
            local_ip = socket.gethostbyname(hostname)
        except Exception:
            local_ip = '查询失败'
        print(f'Starting POPCORE Inventory System')
        print(f'  本机访问:  http://localhost:5000')
        print(f'  手机访问:  http://{local_ip}:5000')
        print(f'  (手机需连接同一WiFi)')
        app.run(debug=False, host='0.0.0.0', port=5000)
