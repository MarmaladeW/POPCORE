"""
app.py - POPCORE Inventory Management System
Flask backend serving the single-page app.
"""
import sqlite3
import os
import json
import uuid
import re
import threading
import time
import urllib.parse
from datetime import date, timedelta
from functools import wraps
from flask import Flask, request, jsonify, send_from_directory, Response
from flask_cors import CORS
from werkzeug.utils import secure_filename
from jose import jwt as jose_jwt
import requests as http_req
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration
from matcher import match_jzm, batch_match_jzm, normalize as norm_jzm

# ─── Sentry — error monitoring & performance tracing ──────────────────────────
sentry_sdk.init(
    dsn=os.environ.get("SENTRY_DSN", ""),
    integrations=[FlaskIntegration()],
    send_default_pii=True,
    traces_sample_rate=0.2,
    profiles_sample_rate=0.1,
    environment=os.environ.get("APP_ENV", "development"),
    release=os.environ.get("APP_RELEASE", "local"),
    server_name=os.environ.get("SERVER_NAME", "localhost"),
)

# ─── Scrape-job state (single background thread) ──────────────────────────────
_scrape_thread: threading.Thread | None = None
_scrape_lock   = threading.Lock()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'popcore.db')
STATIC_DIR = os.path.join(BASE_DIR, 'static')
HIDDEN_IMG_DIR = os.path.join(BASE_DIR, 'uploads', 'hidden_imgs')

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path='')

# ─── CORS (allow Vite dev server) ─────────────────────────────────────────────
CORS(app, origins=['http://localhost:5173', 'https://138.197.150.41'], supports_credentials=False)

# ─── Auth0 configuration ──────────────────────────────────────────────────────
AUTH0_DOMAIN             = os.environ.get('AUTH0_DOMAIN',   'dev-n0833ddaix42sr23.us.auth0.com')
AUTH0_AUDIENCE           = os.environ.get('AUTH0_AUDIENCE', 'https://popcore/api')
AUTH0_MGMT_CLIENT_ID     = os.environ.get('AUTH0_MGMT_CLIENT_ID', '')
AUTH0_MGMT_CLIENT_SECRET = os.environ.get('AUTH0_MGMT_CLIENT_SECRET', '')
AUTH0_MGMT_AUDIENCE      = f'https://{AUTH0_DOMAIN}/api/v2/'
AUTH0_CONNECTION         = 'Username-Password-Authentication'
ROLE_CLAIM               = 'https://popcore/role'
ALGORITHMS               = ['RS256']

os.makedirs(HIDDEN_IMG_DIR, exist_ok=True)


def esc_csv(v):
    """Escape a value for CSV output (RFC 4180)."""
    s = str(v) if v is not None else ''
    if ',' in s or '"' in s or '\n' in s:
        s = '"' + s.replace('"', '""') + '"'
    return s


def get_db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA foreign_keys = ON')
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
        ('is_bestseller',     'INTEGER NOT NULL DEFAULT 0'),
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

    # Merge '盲盒毛绒' and '盲盒Figure' into '盲盒'
    cur.execute("UPDATE products SET product_type = '盲盒' WHERE product_type IN ('盲盒毛绒', '盲盒Figure')")

    # ── Market price tables ────────────────────────────────────────────────
    cur.executescript('''
        CREATE TABLE IF NOT EXISTS market_prices (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            store_key        TEXT    NOT NULL,
            store_name       TEXT    NOT NULL,
            external_title   TEXT    NOT NULL,
            product_id       INTEGER REFERENCES products(id),
            sku              TEXT,
            price_cad        REAL,
            compare_at_price REAL,
            on_sale          INTEGER NOT NULL DEFAULT 0,
            in_stock         INTEGER NOT NULL DEFAULT 1,
            url              TEXT,
            match_score      INTEGER,
            scraped_at       TEXT,
            UNIQUE(store_key, external_title)
        );
        CREATE INDEX IF NOT EXISTS idx_mp_product ON market_prices(product_id);
        CREATE INDEX IF NOT EXISTS idx_mp_store   ON market_prices(store_key);

        CREATE TABLE IF NOT EXISTS scrape_log (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            store_key        TEXT    NOT NULL,
            status           TEXT    NOT NULL DEFAULT 'running',
            products_scraped INTEGER DEFAULT 0,
            products_matched INTEGER DEFAULT 0,
            error_msg        TEXT,
            started_at       TEXT    DEFAULT (datetime('now')),
            finished_at      TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_sl_store ON scrape_log(store_key);

        CREATE TABLE IF NOT EXISTS product_aliases (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            alias      TEXT    NOT NULL,
            alias_norm TEXT    NOT NULL,
            created_at TEXT    DEFAULT (datetime('now'))
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_aliases_norm ON product_aliases(alias_norm);
    ''')

    # ── Restock & evening inventory tables ────────────────────────────────
    cur.executescript('''
        CREATE TABLE IF NOT EXISTS restock_sessions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            date         TEXT    NOT NULL UNIQUE,
            status       TEXT    NOT NULL DEFAULT 'pending',
            created_at   TEXT    DEFAULT (datetime('now')),
            submitted_at TEXT,
            completed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS restock_items (
            id                       INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id               INTEGER NOT NULL REFERENCES restock_sessions(id),
            product_id               INTEGER NOT NULL REFERENCES products(id),
            requested_qty            INTEGER NOT NULL,
            warehouse_stock_snapshot INTEGER NOT NULL DEFAULT 0,
            found_qty                INTEGER,
            pick_status              TEXT    NOT NULL DEFAULT 'pending',
            created_by               INTEGER,
            UNIQUE(session_id, product_id)
        );
        CREATE INDEX IF NOT EXISTS idx_ri_session ON restock_items(session_id);

        CREATE TABLE IF NOT EXISTS stock_movements (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id    INTEGER NOT NULL REFERENCES products(id),
            session_id    INTEGER REFERENCES restock_sessions(id),
            movement_type TEXT    NOT NULL,
            qty_change    INTEGER NOT NULL,
            location      TEXT    NOT NULL,
            created_at    TEXT    DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_sm_product ON stock_movements(product_id);

        CREATE TABLE IF NOT EXISTS inventory_checks (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            date            TEXT    NOT NULL,
            product_id      INTEGER NOT NULL REFERENCES products(id),
            theoretical_qty INTEGER NOT NULL,
            actual_qty      INTEGER NOT NULL,
            discrepancy     INTEGER NOT NULL,
            base_check_date TEXT    NOT NULL,
            created_by      INTEGER,
            created_at      TEXT    DEFAULT (datetime('now')),
            UNIQUE(date, product_id)
        );
    ''')

    con.commit()
    con.close()


if os.path.exists(DB_PATH):
    migrate_db()


# ─── Auth0 JWT helpers ────────────────────────────────────────────────────────

ROLE_HIERARCHY = {'viewer': 0, 'staff': 1, 'manager': 2, 'admin': 3}

_jwks_cache: dict | None = None

def _get_jwks() -> dict:
    global _jwks_cache
    if _jwks_cache is None:
        resp = http_req.get(
            f'https://{AUTH0_DOMAIN}/.well-known/jwks.json', timeout=10
        )
        resp.raise_for_status()
        _jwks_cache = resp.json()
    return _jwks_cache

def _decode_token(token: str) -> dict:
    global _jwks_cache
    jwks = _get_jwks()
    header = jose_jwt.get_unverified_header(token)
    key = next((k for k in jwks['keys'] if k['kid'] == header['kid']), None)
    if key is None:
        # Refresh JWKS once in case of key rotation
        _jwks_cache = None
        jwks = _get_jwks()
        key = next((k for k in jwks['keys'] if k['kid'] == header['kid']), None)
    if key is None:
        raise ValueError(f'Unknown key id: {header.get("kid")}')
    return jose_jwt.decode(token, key, algorithms=ALGORITHMS, audience=AUTH0_AUDIENCE)


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get('Authorization', '')
        if not auth.startswith('Bearer '):
            return jsonify({'error': 'Unauthorized', 'login_required': True}), 401
        try:
            request.jwt_payload = _decode_token(auth[7:])
        except Exception:
            return jsonify({'error': 'Unauthorized', 'login_required': True}), 401
        return f(*args, **kwargs)
    return decorated

def role_required(*allowed_roles):
    min_level = min(ROLE_HIERARCHY.get(r, 99) for r in allowed_roles)
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            auth = request.headers.get('Authorization', '')
            if not auth.startswith('Bearer '):
                return jsonify({'error': 'Unauthorized', 'login_required': True}), 401
            try:
                payload = _decode_token(auth[7:])
                request.jwt_payload = payload
            except Exception:
                return jsonify({'error': 'Unauthorized', 'login_required': True}), 401
            role = payload.get(ROLE_CLAIM, 'viewer')
            if ROLE_HIERARCHY.get(role, 0) < min_level:
                return jsonify({'error': 'Forbidden'}), 403
            return f(*args, **kwargs)
        return decorated
    return decorator


# ─── Auth0 Management API helpers ─────────────────────────────────────────────

_mgmt_token_cache: dict = {'token': None, 'expiry': 0.0}
_role_ids_cache:   dict | None = None

def _get_mgmt_token() -> str:
    if _mgmt_token_cache['token'] and time.time() < _mgmt_token_cache['expiry']:
        return _mgmt_token_cache['token']
    resp = http_req.post(
        f'https://{AUTH0_DOMAIN}/oauth/token',
        json={
            'client_id':     AUTH0_MGMT_CLIENT_ID,
            'client_secret': AUTH0_MGMT_CLIENT_SECRET,
            'audience':      AUTH0_MGMT_AUDIENCE,
            'grant_type':    'client_credentials',
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    _mgmt_token_cache['token']  = data['access_token']
    _mgmt_token_cache['expiry'] = time.time() + data.get('expires_in', 86400) - 60
    return _mgmt_token_cache['token']

def _mgmt_headers() -> dict:
    return {'Authorization': f'Bearer {_get_mgmt_token()}',
            'Content-Type': 'application/json'}

def _mgmt_get(path: str, **kw):
    return http_req.get(
        f'{AUTH0_MGMT_AUDIENCE}{path}',
        headers={'Authorization': f'Bearer {_get_mgmt_token()}'},
        timeout=10, **kw,
    )

def _mgmt_post(path: str, **kw):
    return http_req.post(
        f'{AUTH0_MGMT_AUDIENCE}{path}', headers=_mgmt_headers(), timeout=10, **kw
    )

def _mgmt_patch(path: str, **kw):
    return http_req.patch(
        f'{AUTH0_MGMT_AUDIENCE}{path}', headers=_mgmt_headers(), timeout=10, **kw
    )

def _mgmt_delete(path: str, **kw):
    return http_req.delete(
        f'{AUTH0_MGMT_AUDIENCE}{path}',
        headers={'Authorization': f'Bearer {_get_mgmt_token()}'},
        timeout=10, **kw,
    )

def _get_role_map() -> dict:
    """Return {role_name: role_id} for our 4 roles (cached per process)."""
    global _role_ids_cache
    if _role_ids_cache:
        return _role_ids_cache
    resp = _mgmt_get('roles', params={'per_page': 100})
    resp.raise_for_status()
    _role_ids_cache = {r['name']: r['id'] for r in resp.json()
                       if r['name'] in ROLE_HIERARCHY}
    return _role_ids_cache


# ─── Serve hidden images (stored outside static/) ────────────────────────────

@app.route('/hidden_imgs/<path:filename>')
def serve_hidden_img(filename):
    safe = os.path.normpath(filename).lstrip(os.sep)
    return send_from_directory(HIDDEN_IMG_DIR, safe)


# ─── User Management API (Admin only — backed by Auth0 Management API) ────────

_ORDER = ['admin', 'manager', 'staff', 'viewer']

def _highest_role(user_roles: list, id_to_name: dict) -> str:
    for r in _ORDER:
        if any(id_to_name.get(ur['id']) == r for ur in user_roles):
            return r
    return 'viewer'


@app.route('/api/users')
@role_required('admin')
def list_users():
    resp = _mgmt_get('users', params={
        'q': f'identities.connection:"{AUTH0_CONNECTION}"',
        'search_engine': 'v3',
        'fields': 'user_id,username,nickname,blocked,created_at,last_login',
        'include_fields': 'true',
        'per_page': 100,
    })
    if not resp.ok:
        return jsonify({'error': 'Failed to fetch users from Auth0'}), 502
    users = resp.json()

    role_map   = _get_role_map()             # {name: id}
    id_to_name = {v: k for k, v in role_map.items()}

    result = []
    for u in users:
        uid = u['user_id']
        uid_enc = urllib.parse.quote(uid, safe='')
        roles_resp = _mgmt_get(f'users/{uid_enc}/roles')
        user_roles = roles_resp.json() if roles_resp.ok else []
        result.append({
            'id':         uid,
            'username':   u.get('username') or u.get('nickname') or uid,
            'role':       _highest_role(user_roles, id_to_name),
            'is_active':  0 if u.get('blocked') else 1,
            'created_at': u.get('created_at', ''),
            'last_login': u.get('last_login', ''),
        })
    return jsonify(result)


@app.route('/api/users', methods=['POST'])
@role_required('admin')
def create_user():
    data     = request.get_json() or {}
    username = (data.get('username') or '').strip()
    password = (data.get('password') or '').strip()
    role     = (data.get('role') or 'viewer').strip()
    if not username or not password:
        return jsonify({'error': '用户名和密码必填'}), 400
    if len(password) < 8:
        return jsonify({'error': '密码至少8位'}), 400
    if role not in ROLE_HIERARCHY:
        return jsonify({'error': '无效角色'}), 400

    create_resp = _mgmt_post('users', json={
        'connection':     AUTH0_CONNECTION,
        'username':       username,
        'email':          f'{username}@popcore.internal',
        'password':       password,
        'email_verified': True,
    })
    if not create_resp.ok:
        err = create_resp.json().get('message', 'Create failed')
        code = 409 if ('already exists' in err.lower() or create_resp.status_code == 409) else 502
        return jsonify({'error': f'用户名 {username} 已存在' if code == 409 else err}), code

    user_id = create_resp.json()['user_id']
    role_map = _get_role_map()
    role_id  = role_map.get(role)
    if role_id:
        _mgmt_post(
            f'users/{urllib.parse.quote(user_id, safe="")}/roles',
            json={'roles': [role_id]},
        )
    return jsonify({'ok': True, 'id': user_id}), 201


@app.route('/api/users/<string:uid>', methods=['PATCH'])
@role_required('admin')
def update_user(uid):
    uid_enc = urllib.parse.quote(uid, safe='')
    data    = request.get_json() or {}

    if 'role' in data:
        new_role = data['role']
        if new_role not in ROLE_HIERARCHY:
            return jsonify({'error': '无效角色'}), 400
        role_map    = _get_role_map()
        new_role_id = role_map.get(new_role)
        if not new_role_id:
            return jsonify({'error': 'Role not found in Auth0'}), 500
        cur_resp = _mgmt_get(f'users/{uid_enc}/roles')
        if cur_resp.ok:
            cur_ids = [r['id'] for r in cur_resp.json()]
            if cur_ids:
                _mgmt_delete(f'users/{uid_enc}/roles', json={'roles': cur_ids})
        _mgmt_post(f'users/{uid_enc}/roles', json={'roles': [new_role_id]})

    body = {}
    if 'is_active' in data:
        body['blocked'] = not bool(data['is_active'])
    if data.get('password'):
        if len(data['password']) < 8:
            return jsonify({'error': '密码至少8位'}), 400
        body['password']   = data['password']
        body['connection'] = AUTH0_CONNECTION
    if body:
        resp = _mgmt_patch(f'users/{uid_enc}', json=body)
        if not resp.ok:
            return jsonify({'error': resp.json().get('message', 'Update failed')}), 502
    return jsonify({'ok': True})


@app.route('/api/users/<string:uid>', methods=['DELETE'])
@role_required('admin')
def delete_user(uid):
    if uid == request.jwt_payload.get('sub', ''):
        return jsonify({'error': '不能删除当前登录账户'}), 400
    uid_enc = urllib.parse.quote(uid, safe='')
    resp    = _mgmt_delete(f'users/{uid_enc}')
    if not resp.ok and resp.status_code != 404:
        return jsonify({'error': 'Delete failed'}), 502
    return jsonify({'ok': True})


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


@app.route('/api/products/by_jizhanming')
@login_required
def get_by_jizhanming():
    """
    Look up products by 记账名.
    Phase 1: alias exact lookup — score 100.
    Phase 2: fuzzy match via matcher.match_jzm.
    Returns up to 5 candidates sorted by match score.
    """
    name = request.args.get('name', '').strip()
    if not name:
        return jsonify([])
    con = get_db()
    cur = con.cursor()

    # Load aliases
    cur.execute('SELECT alias_norm, product_id FROM product_aliases')
    aliases = {r['alias_norm']: r['product_id'] for r in cur.fetchall()}

    # Load all products for fuzzy match
    cur.execute('''
        SELECT id, sku, name_cn_en, jizhanming, price, ip_series, product_type
        FROM products
        WHERE jizhanming IS NOT NULL AND jizhanming != ''
    ''')
    all_products = [dict(r) for r in cur.fetchall()]
    con.close()

    matches = match_jzm(name, all_products, aliases=aliases, threshold=75, limit=5)
    return jsonify([p for _, p in matches])


@app.route('/api/products/match', methods=['POST'])
@login_required
def batch_match_products():
    """
    Batch-match multiple 记账名 strings against products in one request.
    Body: { "queries": ["SA草莓", "Dimoo花花", ...], "threshold": 75 }
    Returns: { "results": [{query, status, candidates: [{id,sku,jizhanming,...}]}] }
    status ∈ matched | fuzzy | unmatched | skipped
    """
    data = request.get_json(silent=True) or {}
    queries   = data.get('queries', [])
    threshold = int(data.get('threshold', 75))
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


@app.route('/api/products/aliases', methods=['POST'])
@login_required
def save_alias():
    """Save a manual alias mapping: { product_id, alias }."""
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


@app.route('/api/products/search')
@login_required
def search_products():
    q = request.args.get('q', '').strip().lower()
    series = request.args.get('series', '').strip()
    product_type = request.args.get('product_type', '').strip()
    limit = int(request.args.get('limit', 60))

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
        # No query — just return all (filtered by series/type if set)
        where = ("WHERE " + " AND ".join(filter_clauses)) if filter_clauses else ""
        cur.execute(f'''
            SELECT id, sku, name_cn_en, jizhanming, price, ip_series, product_type,
                   brand, notes, release_date, search_blob
            FROM products
            {where}
            ORDER BY sku DESC
            LIMIT ?
        ''', filter_params + [limit])
        rows = [dict(r) for r in cur.fetchall()]
        con.close()
        return jsonify(rows)

    tokens = q.split()

    # ── Strategy 1: AND match (all tokens must appear) ────────────────────
    and_conditions = " AND ".join("search_blob LIKE ?" for _ in tokens)
    and_params = [f'%{t}%' for t in tokens] + filter_params
    cur.execute(f'''
        SELECT id, sku, name_cn_en, jizhanming, price, ip_series, product_type,
               brand, notes, release_date, search_blob
        FROM products
        WHERE {and_conditions} {filter_sql}
        LIMIT 200
    ''', and_params)
    and_rows = [dict(r) for r in cur.fetchall()]

    # ── Strategy 2: OR match (any token appears) — broader net ────────────
    or_conditions = " OR ".join("search_blob LIKE ?" for _ in tokens)
    or_params = [f'%{t}%' for t in tokens] + filter_params
    cur.execute(f'''
        SELECT id, sku, name_cn_en, jizhanming, price, ip_series, product_type,
               brand, notes, release_date, search_blob
        FROM products
        WHERE ({or_conditions}) {filter_sql}
        LIMIT 200
    ''', or_params)
    or_rows = [dict(r) for r in cur.fetchall()]

    # ── Strategy 3: character-level — each char in query appears in blob ──
    # Useful for Chinese where user types individual chars without spaces
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

    # ── Strategy 4: bigram match — any consecutive 2-char pair of query in blob ──
    # Catches partial Chinese name matches even when not all chars present
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
@login_required
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
@login_required
def list_hidden_images(pid):
    con = get_db()
    cur = con.cursor()
    cur.execute('SELECT * FROM hidden_images WHERE product_id = ? ORDER BY id', (pid,))
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return jsonify(rows)


@app.route('/api/products/<int:pid>/hidden_images', methods=['POST'])
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


@app.route('/api/products/<int:pid>/hidden_images/<int:img_id>', methods=['DELETE'])
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


# ─── Products PATCH ───────────────────────────────────────────────────────────

@app.route('/api/products/<int:pid>', methods=['PATCH'])
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
@role_required('manager')
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
@login_required
def get_series():
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT DISTINCT ip_series FROM products WHERE ip_series != '' ORDER BY ip_series")
    rows = [r[0] for r in cur.fetchall()]
    con.close()
    return jsonify(rows)


@app.route('/api/product_types')
@login_required
def get_product_types():
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT DISTINCT product_type FROM products WHERE product_type IS NOT NULL AND product_type != '' ORDER BY product_type")
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
@login_required
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
@login_required
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


@app.route('/api/stock/<int:product_id>', methods=['PATCH'])
@role_required('staff')
def patch_stock(product_id):
    """Update notes for a stock row."""
    data = request.get_json() or {}
    notes = data.get('notes', '')
    con = get_db()
    con.execute('UPDATE stock SET notes=? WHERE product_id=?', (notes, product_id))
    con.commit()
    con.close()
    return jsonify({'ok': True})


@app.route('/api/stock/ru_dian', methods=['POST'])
@role_required('staff')
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
@role_required('staff')
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
@role_required('staff')
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
@login_required
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
@login_required
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
@login_required
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
@role_required('staff')
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
@role_required('staff')
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
@login_required
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


@app.route('/api/sales/record/<int:record_id>', methods=['DELETE'])
@role_required('manager')
def delete_sales_record(record_id):
    """Delete a single daily_sales row by its id."""
    con = get_db()
    cur = con.cursor()
    cur.execute('DELETE FROM daily_sales WHERE id = ?', (record_id,))
    con.commit()
    con.close()
    return jsonify({'ok': True})


@app.route('/api/stock/batch_operation', methods=['POST'])
@role_required('staff')
def batch_stock_operation():
    """
    Batch stock operation (paste import).
    Body: { operation: 'ru_dian'|'restock_upstairs', date: '...', items: [{product_id, qty, notes}] }
    """
    data      = request.get_json()
    operation = data.get('operation', 'ru_dian')
    d         = data.get('date', str(date.today()))
    items     = data.get('items', [])

    if operation not in ('ru_dian', 'restock_upstairs'):
        return jsonify({'error': 'Invalid operation'}), 400

    con = get_db()
    cur = con.cursor()
    results = []

    for item in items:
        pid   = int(item['product_id'])
        qty   = int(item.get('qty', 0))
        notes = item.get('notes', '')
        if qty <= 0:
            continue

        _ensure_stock_row(cur, pid)

        if operation == 'ru_dian':
            cur.execute('SELECT upstairs_dan FROM stock WHERE product_id = ?', (pid,))
            row = cur.fetchone()
            upstairs = row['upstairs_dan'] if row else 0
            if qty > upstairs:
                results.append({'pid': pid, 'ok': False,
                                 'error': f'楼上库存不足（{upstairs}端）'})
                continue
            cur.execute('''
                UPDATE stock SET upstairs_dan = upstairs_dan - ?,
                                 instore_dan  = instore_dan  + ?,
                                 last_updated = ?
                WHERE product_id = ?
            ''', (qty, qty, d, pid))
            loc = 'upstairs->instore'
        else:  # restock_upstairs
            cur.execute('''
                UPDATE stock SET upstairs_dan = upstairs_dan + ?,
                                 last_updated = ?
                WHERE product_id = ?
            ''', (qty, d, pid))
            loc = 'upstairs'

        cur.execute('''
            INSERT INTO stock_transactions (product_id, txn_type, dan_qty, location, date, notes)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (pid, operation, qty, loc, d, notes))
        results.append({'pid': pid, 'ok': True})

    con.commit()
    con.close()
    return jsonify({'ok': True, 'results': results})


@app.route('/api/sales/batch_upsert', methods=['POST'])
@role_required('staff')
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
@role_required('manager')
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


@app.route('/api/stock/export')
@role_required('manager')
def export_stock():
    """Export current stock as CSV (UTF-8 BOM for Excel)."""
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
               p.price, p.boxes_per_dan,
               COALESCE(s.upstairs_dan, 0) AS upstairs_dan,
               COALESCE(s.instore_dan,  0) AS instore_dan,
               COALESCE(s.last_updated, '') AS last_updated,
               COALESCE(s.notes, '') AS stock_notes
        FROM stock s
        JOIN products p ON p.id = s.product_id
        WHERE 1=1 {where}
        ORDER BY p.ip_series, p.sku DESC
    ''', params)
    rows = cur.fetchall()
    con.close()

    header = 'SKU,记账名,产品名称,系列,类型,单价,每端盒数,楼上(端),店内(端),更新时间,备注'
    lines  = ['\ufeff' + header]
    for r in rows:
        lines.append(','.join(esc_csv(v) for v in [
            r['sku'], r['jizhanming'], r['name_cn_en'], r['ip_series'],
            r['product_type'], r['price'], r['boxes_per_dan'],
            r['upstairs_dan'], r['instore_dan'], r['last_updated'], r['stock_notes']
        ]))

    fname = f'stock_{date.today()}.csv'
    return Response(
        '\n'.join(lines),
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="{fname}"'}
    )


@app.route('/api/stock/rows', methods=['DELETE'])
@role_required('manager')
def delete_stock_rows():
    """Remove products from stock tracking (delete their stock rows only)."""
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


@app.route('/api/sales/clear_day', methods=['DELETE'])
@role_required('manager')
def clear_sales_day():
    """Delete all daily_sales records for a given date."""
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


@app.route('/api/products/export')
@role_required('manager')
def export_products():
    """Export products as CSV, respecting series/q filters."""
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
    lines  = ['\ufeff' + header]
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


@app.route('/api/products/bulk_delete', methods=['POST'])
@role_required('manager')
def bulk_delete_products():
    """Delete multiple products with full cascade (images, sales, stock, transactions)."""
    pids = request.get_json()
    if not isinstance(pids, list) or not pids:
        return jsonify({'error': 'Expected a list of product_ids'}), 400

    con = get_db()
    cur = con.cursor()
    ph  = ','.join('?' * len(pids))

    # Delete physical image files first
    cur.execute(f'SELECT filename FROM hidden_images WHERE product_id IN ({ph})', pids)
    for row in cur.fetchall():
        fp = os.path.join(HIDDEN_IMG_DIR, row['filename'])
        if os.path.exists(fp):
            try:
                os.remove(fp)
            except OSError:
                pass

    # Cascade deletes
    cur.execute(f'DELETE FROM hidden_images     WHERE product_id IN ({ph})', pids)
    cur.execute(f'DELETE FROM daily_sales        WHERE product_id IN ({ph})', pids)
    cur.execute(f'DELETE FROM stock_transactions WHERE product_id IN ({ph})', pids)
    cur.execute(f'DELETE FROM stock              WHERE product_id IN ({ph})', pids)
    cur.execute(f'DELETE FROM products           WHERE id         IN ({ph})', pids)
    deleted = cur.rowcount

    con.commit()
    con.close()
    return jsonify({'ok': True, 'deleted': deleted})


# ══════════════════════════════════════════════════════════════════════════════
# MARKET PRICES API
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/api/market/scrape', methods=['POST'])
@role_required('manager')
def start_market_scrape():
    """
    Launch a background scrape job.
    Body (optional JSON): { "stores": ["popmart_ca", "mrpen", "whoopea"] }
    Returns { ok, status } or 409 if already running.
    """
    global _scrape_thread
    with _scrape_lock:
        if _scrape_thread and _scrape_thread.is_alive():
            return jsonify({'error': 'Scrape already running'}), 409

    data       = request.get_json(silent=True) or {}
    store_keys = data.get('stores') or None   # None → all stores

    def _run():
        try:
            from scraper import run_scrape
            run_scrape(store_keys, DB_PATH)
        except Exception as e:
            import traceback; traceback.print_exc()

    with _scrape_lock:
        _scrape_thread = threading.Thread(target=_run, daemon=True, name='scrape')
        _scrape_thread.start()

    return jsonify({'ok': True, 'status': 'running', 'stores': store_keys or 'all'})


@app.route('/api/market/status')
@login_required
def market_status():
    """
    Return per-store scrape status (last run only) + whether a job is running.
    """
    con = get_db()
    cur = con.cursor()
    cur.execute('''
        SELECT store_key, status, products_scraped, products_matched,
               error_msg, started_at, finished_at
        FROM scrape_log
        WHERE id IN (SELECT MAX(id) FROM scrape_log GROUP BY store_key)
        ORDER BY store_key
    ''')
    stores = {r['store_key']: dict(r) for r in cur.fetchall()}
    con.close()

    is_running = bool(_scrape_thread and _scrape_thread.is_alive())
    return jsonify({'stores': stores, 'running': is_running})


@app.route('/api/market/scrape_log')
@login_required
def market_scrape_log():
    """Return last 100 scrape_log rows ordered by started_at DESC."""
    con = get_db()
    cur = con.cursor()
    cur.execute('''
        SELECT id, store_key, status, products_scraped, products_matched,
               error_msg, started_at, finished_at
        FROM scrape_log
        ORDER BY started_at DESC
        LIMIT 100
    ''')
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return jsonify(rows)


@app.route('/api/market/prices')
@login_required
def market_prices():
    """
    Return market_prices rows.
    Query params:
      product_id=<int>  → prices for one internal product (all stores)
      store=<key>       → all prices for one store
      (none)            → all rows
    """
    product_id = request.args.get('product_id', type=int)
    store      = request.args.get('store', '').strip()

    con = get_db()
    cur = con.cursor()

    if product_id:
        cur.execute('''
            SELECT * FROM market_prices
            WHERE product_id = ?
            ORDER BY store_key
        ''', (product_id,))
    elif store:
        cur.execute('''
            SELECT * FROM market_prices
            WHERE store_key = ?
            ORDER BY external_title
        ''', (store,))
    else:
        cur.execute('''
            SELECT * FROM market_prices
            ORDER BY store_key, external_title
        ''')

    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return jsonify(rows)


@app.route('/api/market/overview')
@login_required
def market_overview():
    """
    Full market overview: all scraped items joined with internal product info.
    Used by the Market tab to build the comparison table.
    Query param: matched_only=1 to hide unmatched scraped items.
    """
    matched_only = request.args.get('matched_only', '0') == '1'
    con = get_db()
    cur = con.cursor()
    where = 'WHERE mp.product_id IS NOT NULL' if matched_only else ''
    cur.execute(f'''
        SELECT
            mp.id, mp.store_key, mp.store_name,
            mp.external_title, mp.price_cad, mp.compare_at_price,
            mp.on_sale, mp.in_stock, mp.url,
            mp.match_score, mp.scraped_at,
            mp.product_id, mp.sku,
            p.jizhanming, p.name_cn_en, p.price AS our_price,
            p.ip_series,  p.product_type
        FROM market_prices mp
        LEFT JOIN products p ON mp.product_id = p.id
        {where}
        ORDER BY mp.store_key, mp.external_title COLLATE NOCASE
    ''')
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return jsonify(rows)


# ─── Restock API ─────────────────────────────────────────────────────────────

@app.route('/api/restock/today')
@login_required
def restock_today():
    """Get or create today's restock session."""
    today = date.today().isoformat()
    con = get_db()
    cur = con.cursor()
    cur.execute('SELECT * FROM restock_sessions WHERE date = ?', (today,))
    row = cur.fetchone()
    if not row:
        cur.execute(
            "INSERT INTO restock_sessions (date, status) VALUES (?, 'pending')",
            (today,),
        )
        con.commit()
        cur.execute('SELECT * FROM restock_sessions WHERE date = ?', (today,))
        row = cur.fetchone()
    session = dict(row)
    con.close()
    return jsonify(session)


@app.route('/api/restock/sessions/<int:sid>')
@login_required
def get_restock_session(sid):
    """Get session details with all items (joined product + stock info)."""
    con = get_db()
    cur = con.cursor()
    cur.execute('SELECT * FROM restock_sessions WHERE id = ?', (sid,))
    row = cur.fetchone()
    if not row:
        con.close()
        return jsonify({'error': 'Session not found'}), 404
    session = dict(row)
    cur.execute('''
        SELECT ri.id, ri.product_id, ri.requested_qty, ri.warehouse_stock_snapshot,
               ri.found_qty, ri.pick_status,
               p.sku, p.jizhanming, p.name_cn_en, p.ip_series, p.product_type,
               COALESCE(s.upstairs_dan, 0) AS upstairs_dan,
               COALESCE(s.instore_dan,  0) AS instore_dan
        FROM restock_items ri
        JOIN products p ON p.id = ri.product_id
        LEFT JOIN stock s ON s.product_id = ri.product_id
        WHERE ri.session_id = ?
        ORDER BY ri.id
    ''', (sid,))
    session['items'] = [dict(r) for r in cur.fetchall()]
    con.close()
    return jsonify(session)


@app.route('/api/restock/sessions/<int:sid>/items', methods=['POST'])
@role_required('staff')
def add_restock_item(sid):
    """Add or update a restock item. Body: { product_id, requested_qty }."""
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT status FROM restock_sessions WHERE id = ?", (sid,))
    sess = cur.fetchone()
    if not sess:
        con.close()
        return jsonify({'error': 'Session not found'}), 404
    if sess['status'] != 'pending':
        con.close()
        return jsonify({'error': '只能在 pending 状态下修改清单'}), 400

    data = request.get_json() or {}
    pid = int(data.get('product_id', 0))
    qty = int(data.get('requested_qty', 0))
    if not pid or qty <= 0:
        con.close()
        return jsonify({'error': 'product_id 和 requested_qty 必须填写'}), 400

    # Snapshot current warehouse stock
    cur.execute('SELECT COALESCE(upstairs_dan, 0) FROM stock WHERE product_id = ?', (pid,))
    snap_row = cur.fetchone()
    snapshot = snap_row[0] if snap_row else 0

    cur.execute('''
        INSERT INTO restock_items (session_id, product_id, requested_qty, warehouse_stock_snapshot)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(session_id, product_id) DO UPDATE SET
            requested_qty = excluded.requested_qty,
            warehouse_stock_snapshot = excluded.warehouse_stock_snapshot
    ''', (sid, pid, qty, snapshot))
    con.commit()
    item_id = cur.lastrowid
    con.close()
    return jsonify({'ok': True, 'id': item_id}), 201


@app.route('/api/restock/sessions/<int:sid>/items/<int:iid>', methods=['DELETE'])
@role_required('staff')
def delete_restock_item(sid, iid):
    """Remove an item from a pending session."""
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT status FROM restock_sessions WHERE id = ?", (sid,))
    sess = cur.fetchone()
    if not sess or sess['status'] != 'pending':
        con.close()
        return jsonify({'error': '只能在 pending 状态下删除条目'}), 400
    cur.execute('DELETE FROM restock_items WHERE id = ? AND session_id = ?', (iid, sid))
    con.commit()
    con.close()
    return jsonify({'ok': True})


@app.route('/api/restock/sessions/<int:sid>/submit', methods=['POST'])
@role_required('staff')
def submit_restock_session(sid):
    """pending → submitted: freeze the picking list."""
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT status FROM restock_sessions WHERE id = ?", (sid,))
    sess = cur.fetchone()
    if not sess:
        con.close()
        return jsonify({'error': 'Session not found'}), 404
    if sess['status'] != 'pending':
        con.close()
        return jsonify({'error': f'当前状态 {sess["status"]} 不可提交'}), 400
    cur.execute(
        "UPDATE restock_sessions SET status='submitted', submitted_at=datetime('now') WHERE id=?",
        (sid,),
    )
    con.commit()
    con.close()
    return jsonify({'ok': True})


@app.route('/api/restock/sessions/<int:sid>/items/<int:iid>', methods=['PATCH'])
@role_required('staff')
def patch_restock_item(sid, iid):
    """Update picking result. Body: { found_qty, pick_status }."""
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT status FROM restock_sessions WHERE id = ?", (sid,))
    sess = cur.fetchone()
    if not sess or sess['status'] not in ('submitted', 'picking'):
        con.close()
        return jsonify({'error': '只能在 submitted/picking 状态下更新拣货结果'}), 400

    data = request.get_json() or {}
    allowed_status = {'pending', 'found', 'not_found'}
    pick_status = data.get('pick_status', 'pending')
    if pick_status not in allowed_status:
        con.close()
        return jsonify({'error': '无效的 pick_status'}), 400
    found_qty = data.get('found_qty')

    cur.execute(
        'UPDATE restock_items SET found_qty=?, pick_status=? WHERE id=? AND session_id=?',
        (found_qty, pick_status, iid, sid),
    )
    # Move session to picking once first update happens
    if sess['status'] == 'submitted':
        cur.execute("UPDATE restock_sessions SET status='picking' WHERE id=?", (sid,))
    con.commit()
    con.close()
    return jsonify({'ok': True})


@app.route('/api/restock/sessions/<int:sid>/complete', methods=['POST'])
@role_required('staff')
def complete_restock_session(sid):
    """
    picking → completed: sync stock for all found items.
    Decreases upstairs_dan (warehouse) and increases instore_dan (store).
    Logs entries in stock_movements and stock_transactions.
    """
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT status FROM restock_sessions WHERE id = ?", (sid,))
    sess = cur.fetchone()
    if not sess:
        con.close()
        return jsonify({'error': 'Session not found'}), 404
    if sess['status'] not in ('submitted', 'picking'):
        con.close()
        return jsonify({'error': f'当前状态 {sess["status"]} 不可完成'}), 400

    cur.execute(
        "SELECT * FROM restock_items WHERE session_id=? AND pick_status='found' AND found_qty > 0",
        (sid,),
    )
    items = [dict(r) for r in cur.fetchall()]
    today = date.today().isoformat()

    for item in items:
        pid = item['product_id']
        qty = item['found_qty']
        # Ensure stock row exists
        cur.execute('INSERT OR IGNORE INTO stock (product_id, upstairs_dan, instore_dan) VALUES (?,0,0)', (pid,))
        # Sync stock
        cur.execute('''
            UPDATE stock
            SET upstairs_dan = MAX(0, upstairs_dan - ?),
                instore_dan  = instore_dan + ?,
                last_updated = datetime('now')
            WHERE product_id = ?
        ''', (qty, qty, pid))
        # Log movements
        cur.execute('''
            INSERT INTO stock_movements (product_id, session_id, movement_type, qty_change, location)
            VALUES (?, ?, 'restock_out', ?, 'warehouse')
        ''', (pid, sid, -qty))
        cur.execute('''
            INSERT INTO stock_movements (product_id, session_id, movement_type, qty_change, location)
            VALUES (?, ?, 'restock_in', ?, 'store')
        ''', (pid, sid, qty))
        # Also log in legacy stock_transactions for compatibility
        cur.execute('''
            INSERT INTO stock_transactions (product_id, txn_type, dan_qty, location, date, notes)
            VALUES (?, 'ru_dian', ?, 'instore', ?, ?)
        ''', (pid, qty, today, f'补货入店 session#{sid}'))

    cur.execute(
        "UPDATE restock_sessions SET status='completed', completed_at=datetime('now') WHERE id=?",
        (sid,),
    )
    con.commit()
    con.close()
    return jsonify({'ok': True, 'synced': len(items)})


# ─── Inventory Checks API ─────────────────────────────────────────────────────

@app.route('/api/inventory_checks/today')
@login_required
def inventory_checks_today():
    """
    Return bestseller products with their theoretical stock (instore_dan)
    and any existing check record for today.
    """
    today = date.today().isoformat()
    con = get_db()
    cur = con.cursor()
    cur.execute('''
        SELECT p.id AS product_id, p.sku, p.jizhanming, p.name_cn_en,
               p.ip_series, p.product_type,
               COALESCE(s.instore_dan, 0) AS theoretical_qty,
               ic.actual_qty, ic.discrepancy, ic.base_check_date,
               ic.id AS check_id
        FROM products p
        LEFT JOIN stock s ON s.product_id = p.id
        LEFT JOIN inventory_checks ic
               ON ic.product_id = p.id AND ic.date = ?
        WHERE p.is_bestseller = 1
        ORDER BY p.ip_series, p.sku DESC
    ''', (today,))
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return jsonify({'date': today, 'items': rows})


@app.route('/api/inventory_checks', methods=['POST'])
@role_required('staff')
def create_inventory_check():
    """
    Submit one inventory check record.
    Body: { product_id, actual_qty, base_check_date }
    discrepancy is calculated server-side from instore_dan.
    """
    data = request.get_json() or {}
    pid  = data.get('product_id')
    actual_qty = data.get('actual_qty')
    base_check_date = (data.get('base_check_date') or '').strip()
    if pid is None or actual_qty is None:
        return jsonify({'error': 'product_id 和 actual_qty 必须填写'}), 400

    today = date.today().isoformat()
    con = get_db()
    cur = con.cursor()
    cur.execute('SELECT COALESCE(instore_dan, 0) FROM stock WHERE product_id = ?', (pid,))
    row = cur.fetchone()
    theoretical = row[0] if row else 0
    discrepancy = int(actual_qty) - theoretical

    created_by = request.jwt_payload.get('sub')
    cur.execute('''
        INSERT INTO inventory_checks
            (date, product_id, theoretical_qty, actual_qty, discrepancy, base_check_date, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date, product_id) DO UPDATE SET
            theoretical_qty = excluded.theoretical_qty,
            actual_qty      = excluded.actual_qty,
            discrepancy     = excluded.discrepancy,
            base_check_date = excluded.base_check_date
    ''', (today, pid, theoretical, int(actual_qty), discrepancy,
          base_check_date or today, created_by))
    con.commit()
    con.close()
    return jsonify({'ok': True, 'discrepancy': discrepancy}), 201


# ─── SPA fallback (React Router) ─────────────────────────────────────────────

@app.errorhandler(404)
def not_found(e):
    if request.path.startswith('/api/') or request.path.startswith('/hidden_imgs/'):
        return jsonify({'error': 'Not found'}), 404
    return send_from_directory(STATIC_DIR, 'index.html')


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
