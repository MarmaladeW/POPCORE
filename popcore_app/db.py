"""
db.py — database helpers shared across all blueprints.
"""
import sqlite3
import os
from flask import g

BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
DB_PATH        = os.path.join(BASE_DIR, 'popcore.db')
STATIC_DIR     = os.path.join(BASE_DIR, 'static')
HIDDEN_IMG_DIR = os.path.join(BASE_DIR, 'uploads', 'hidden_imgs')


def esc_csv(v):
    """Escape a value for CSV output (RFC 4180)."""
    s = str(v) if v is not None else ''
    if ',' in s or '"' in s or '\n' in s:
        s = '"' + s.replace('"', '""') + '"'
    return s


def get_db():
    if 'db' not in g:
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('PRAGMA foreign_keys = ON')
        g.db = con
    return g.db


def close_db(error):
    db = g.pop('db', None)
    if db is not None:
        try:
            db.close()
        except Exception:
            pass


def _ensure_stock_row(cur, product_id):
    """Insert a stock row for product if it doesn't exist yet."""
    cur.execute('''
        INSERT OR IGNORE INTO stock (product_id, upstairs_qty, instore_qty)
        VALUES (?, 0, 0)
    ''', (product_id,))


def migrate_db():
    """Create tables and add new columns if they don't exist yet (safe to re-run)."""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA foreign_keys = ON')
    cur = con.cursor()

    cur.executescript('''
        CREATE TABLE IF NOT EXISTS products (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            sku          TEXT UNIQUE,
            name_cn_en   TEXT,
            jizhanming   TEXT,
            price        REAL,
            ip_series    TEXT,
            product_type TEXT,
            brand        TEXT,
            release_date TEXT,
            edition_size TEXT,
            channel      TEXT,
            hidden       TEXT,
            style_notes  TEXT,
            notes        TEXT DEFAULT '',
            search_blob  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_products_sku ON products(sku);
    ''')

    cur.execute("PRAGMA table_info(products)")
    existing = {r['name'] for r in cur.fetchall()}

    new_cols = [
        ('notes',             "TEXT    NOT NULL DEFAULT ''"),
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

    # Add claw_qty to stock table if missing.
    cur.execute("PRAGMA table_info(stock)")
    stock_cols = {r['name'] for r in cur.fetchall()}
    if 'claw_dan' not in stock_cols and 'claw_qty' not in stock_cols:
        try:
            cur.execute('ALTER TABLE stock ADD COLUMN claw_qty INTEGER NOT NULL DEFAULT 0')
        except Exception:
            pass

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
            upstairs_qty INTEGER NOT NULL DEFAULT 0,
            instore_qty  INTEGER NOT NULL DEFAULT 0,
            claw_qty     INTEGER NOT NULL DEFAULT 0,
            last_updated TEXT,
            notes        TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS stock_transactions (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL REFERENCES products(id),
            txn_type   TEXT NOT NULL,
            qty        INTEGER NOT NULL,
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
    if 'store' not in ds_cols:
        cur.execute("ALTER TABLE daily_sales ADD COLUMN store TEXT NOT NULL DEFAULT 'DT'")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ds_store ON daily_sales(store)")

    # Merge '盲盒毛绒' and '盲盒Figure' into '盲盒'
    cur.execute("UPDATE products SET product_type = '盲盒' WHERE product_type IN ('盲盒毛绒', '盲盒Figure')")

    # ── Product-type schema: add dan_per_xiang ─────────────────────────────
    cur.execute("PRAGMA table_info(products)")
    existing = {r['name'] for r in cur.fetchall()}
    if 'dan_per_xiang' not in existing:
        cur.execute('ALTER TABLE products ADD COLUMN dan_per_xiang INTEGER')

    # ── Stock column renames: *_dan → *_qty ────────────────────────────────
    cur.execute("PRAGMA table_info(stock)")
    stock_col_names = {r['name'] for r in cur.fetchall()}

    if 'upstairs_dan' in stock_col_names:
        cur.execute('ALTER TABLE stock RENAME COLUMN upstairs_dan TO upstairs_qty')
    if 'instore_dan' in stock_col_names:
        cur.execute('ALTER TABLE stock RENAME COLUMN instore_dan TO instore_qty')
    if 'claw_dan' in stock_col_names:
        cur.execute('ALTER TABLE stock RENAME COLUMN claw_dan TO claw_qty')

    cur.execute("PRAGMA table_info(stock_transactions)")
    txn_col_names = {r['name'] for r in cur.fetchall()}
    if 'dan_qty' in txn_col_names:
        cur.execute('ALTER TABLE stock_transactions RENAME COLUMN dan_qty TO qty')

    # ── Data migration: convert blind-box stock from 端 → 盒 ───────────────
    cur.execute('''
        CREATE TABLE IF NOT EXISTS _migrations (
            name TEXT PRIMARY KEY,
            applied_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    cur.execute("SELECT 1 FROM _migrations WHERE name='blind_box_stock_to_he'")
    if not cur.fetchone():
        cur.execute('''
            UPDATE stock SET
                upstairs_qty = upstairs_qty * p.boxes_per_dan,
                instore_qty  = instore_qty  * p.boxes_per_dan,
                claw_qty     = claw_qty     * p.boxes_per_dan
            FROM products p
            WHERE stock.product_id = p.id
              AND p.product_type = '盲盒'
              AND p.boxes_per_dan IS NOT NULL
              AND p.boxes_per_dan > 0
        ''')
        cur.execute('''
            UPDATE stock_transactions SET qty = qty * p.boxes_per_dan
            FROM products p
            WHERE stock_transactions.product_id = p.id
              AND p.product_type = '盲盒'
              AND p.boxes_per_dan IS NOT NULL
              AND p.boxes_per_dan > 0
        ''')
        cur.execute("INSERT OR IGNORE INTO _migrations (name) VALUES ('blind_box_stock_to_he')")

    # ── Market price tables ─────────────────────────────────────────────────
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

        CREATE TABLE IF NOT EXISTS section_aliases (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            alias_norm   TEXT    NOT NULL UNIQUE,
            section_type TEXT    NOT NULL,
            created_at   TEXT    DEFAULT (datetime('now'))
        );
    ''')

    # ── Restock & evening inventory tables ─────────────────────────────────
    # Remove UNIQUE constraint on restock_sessions.date (allow multiple sessions per day)
    cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='restock_sessions'")
    _ddl_row = cur.fetchone()
    if _ddl_row:
        _ddl = _ddl_row['sql'] if hasattr(_ddl_row, 'keys') else _ddl_row[0]
        if 'UNIQUE' in _ddl and 'date' in _ddl:
            cur.execute('PRAGMA foreign_keys = OFF')
            cur.execute('DROP TABLE IF EXISTS restock_sessions_new')
            cur.execute('''
                CREATE TABLE restock_sessions_new (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    date         TEXT    NOT NULL,
                    status       TEXT    NOT NULL DEFAULT 'pending',
                    created_at   TEXT    DEFAULT (datetime('now')),
                    submitted_at TEXT,
                    completed_at TEXT
                )
            ''')
            cur.execute('INSERT INTO restock_sessions_new SELECT * FROM restock_sessions')
            cur.execute('DROP TABLE restock_sessions')
            cur.execute('ALTER TABLE restock_sessions_new RENAME TO restock_sessions')
            con.commit()
            cur.execute('PRAGMA foreign_keys = ON')

    cur.executescript('''
        CREATE TABLE IF NOT EXISTS restock_sessions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            date         TEXT    NOT NULL,
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

    # ── Shift scheduling tables ─────────────────────────────────────────────
    cur.executescript('''
        CREATE TABLE IF NOT EXISTS employees (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            auth0_id   TEXT UNIQUE NOT NULL,
            name       TEXT NOT NULL DEFAULT '',
            email      TEXT DEFAULT '',
            is_active  INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS availability (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
            date        TEXT NOT NULL,
            start_time  TEXT NOT NULL,
            end_time    TEXT NOT NULL,
            notes       TEXT DEFAULT '',
            created_at  TEXT DEFAULT (datetime('now')),
            updated_at  TEXT DEFAULT (datetime('now')),
            UNIQUE(employee_id, date)
        );
        CREATE INDEX IF NOT EXISTS idx_availability_date ON availability(date);

        CREATE TABLE IF NOT EXISTS shifts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
            date        TEXT NOT NULL,
            start_time  TEXT NOT NULL,
            end_time    TEXT NOT NULL,
            assigned_by TEXT NOT NULL,
            notes       TEXT DEFAULT '',
            created_at  TEXT DEFAULT (datetime('now')),
            updated_at  TEXT DEFAULT (datetime('now')),
            UNIQUE(employee_id, date)
        );
        CREATE INDEX IF NOT EXISTS idx_shifts_date     ON shifts(date);
        CREATE INDEX IF NOT EXISTS idx_shifts_employee ON shifts(employee_id);
    ''')

    con.commit()
    con.close()
