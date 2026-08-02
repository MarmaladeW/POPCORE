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


def _ensure_stock_row(cur, product_id, store_id=1):
    """Insert a stock row for (product, store) if it doesn't exist yet."""
    cur.execute('''
        INSERT OR IGNORE INTO stock (product_id, store_id, upstairs_qty, instore_qty, claw_qty)
        VALUES (?, ?, 0, 0, 0)
    ''', (product_id, store_id))


# ---------------------------------------------------------------------------
# Registered migrations (run once, guarded by _migrations table)
# ---------------------------------------------------------------------------

def _migration_create_stores_table(con, cur):
    con.commit()
    con.isolation_level = None
    try:
        cur.execute('BEGIN')
        cur.execute('''
            CREATE TABLE stores (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                code      TEXT NOT NULL UNIQUE,
                name      TEXT NOT NULL,
                address   TEXT DEFAULT '',
                is_active INTEGER NOT NULL DEFAULT 1
            )
        ''')
        cur.execute("INSERT INTO stores (code, name, address) VALUES ('DT', 'Downtown Toronto', '')")
        cur.execute("INSERT INTO stores (code, name, address) VALUES ('MK', 'Markham', '')")
        cur.execute("INSERT INTO stores (code, name, address) VALUES ('MT', 'Midtown', '')")
        cur.execute("INSERT OR IGNORE INTO _migrations (name) VALUES ('create_stores_table')")
        cur.execute('COMMIT')
    except Exception:
        cur.execute('ROLLBACK')
        raise
    finally:
        con.isolation_level = ''


def _migration_create_employee_stores_table(con, cur):
    con.commit()
    con.isolation_level = None
    try:
        cur.execute('BEGIN')
        cur.execute('''
            CREATE TABLE employee_stores (
                employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
                store_id    INTEGER NOT NULL REFERENCES stores(id)    ON DELETE CASCADE,
                PRIMARY KEY (employee_id, store_id)
            )
        ''')
        cur.execute("INSERT OR IGNORE INTO _migrations (name) VALUES ('create_employee_stores_table')")
        cur.execute('COMMIT')
    except Exception:
        cur.execute('ROLLBACK')
        raise
    finally:
        con.isolation_level = ''


def _migration_migrate_stock_to_per_store(con, cur):
    con.commit()
    con.isolation_level = None
    cur.execute('PRAGMA foreign_keys = OFF')
    try:
        cur.execute('BEGIN')
        cur.execute('DROP TABLE IF EXISTS stock_new')
        cur.execute('''
            CREATE TABLE stock_new (
                product_id   INTEGER NOT NULL REFERENCES products(id),
                store_id     INTEGER NOT NULL REFERENCES stores(id),
                upstairs_qty INTEGER NOT NULL DEFAULT 0,
                instore_qty  INTEGER NOT NULL DEFAULT 0,
                claw_qty     INTEGER NOT NULL DEFAULT 0,
                last_updated TEXT,
                notes        TEXT DEFAULT '',
                PRIMARY KEY (product_id, store_id)
            )
        ''')
        cur.execute('''
            INSERT INTO stock_new
                (product_id, store_id, upstairs_qty, instore_qty, claw_qty, last_updated, notes)
            SELECT s.product_id,
                   (SELECT st.id FROM stores st WHERE st.code = 'DT'),
                   s.upstairs_qty, s.instore_qty, s.claw_qty, s.last_updated, s.notes
            FROM stock s
        ''')
        cur.execute('DROP TABLE stock')
        cur.execute('ALTER TABLE stock_new RENAME TO stock')
        cur.execute("INSERT OR IGNORE INTO _migrations (name) VALUES ('migrate_stock_to_per_store')")
        cur.execute('COMMIT')
    except Exception:
        cur.execute('ROLLBACK')
        raise
    finally:
        cur.execute('PRAGMA foreign_keys = ON')
        con.isolation_level = ''


def _migration_add_store_id_to_inventory_checks(con, cur):
    con.commit()
    con.isolation_level = None
    cur.execute('PRAGMA foreign_keys = OFF')
    try:
        cur.execute('BEGIN')
        cur.execute('DROP TABLE IF EXISTS inventory_checks_new')
        cur.execute('''
            CREATE TABLE inventory_checks_new (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                date            TEXT    NOT NULL,
                product_id      INTEGER NOT NULL REFERENCES products(id),
                theoretical_qty INTEGER NOT NULL,
                actual_qty      INTEGER NOT NULL,
                discrepancy     INTEGER NOT NULL,
                base_check_date TEXT    NOT NULL,
                created_by      INTEGER,
                created_at      TEXT    DEFAULT (datetime('now')),
                store_id        INTEGER NOT NULL DEFAULT 1 REFERENCES stores(id),
                UNIQUE(date, product_id, store_id)
            )
        ''')
        cur.execute('''
            INSERT INTO inventory_checks_new
                (id, date, product_id, theoretical_qty, actual_qty, discrepancy,
                 base_check_date, created_by, created_at, store_id)
            SELECT id, date, product_id, theoretical_qty, actual_qty, discrepancy,
                   base_check_date, created_by, created_at, 1
            FROM inventory_checks
        ''')
        cur.execute('DROP TABLE inventory_checks')
        cur.execute('ALTER TABLE inventory_checks_new RENAME TO inventory_checks')
        cur.execute("INSERT OR IGNORE INTO _migrations (name) VALUES ('add_store_id_to_inventory_checks')")
        cur.execute('COMMIT')
    except Exception:
        cur.execute('ROLLBACK')
        raise
    finally:
        cur.execute('PRAGMA foreign_keys = ON')
        con.isolation_level = ''


def _migration_add_store_id_to_restock_sessions(con, cur):
    con.commit()
    con.isolation_level = None
    cur.execute('PRAGMA foreign_keys = OFF')
    try:
        cur.execute('BEGIN')
        cur.execute('DROP TABLE IF EXISTS restock_sessions_new')
        cur.execute('''
            CREATE TABLE restock_sessions_new (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                date         TEXT    NOT NULL,
                status       TEXT    NOT NULL DEFAULT 'pending',
                created_at   TEXT    DEFAULT (datetime('now')),
                submitted_at TEXT,
                completed_at TEXT,
                store_id     INTEGER NOT NULL DEFAULT 1 REFERENCES stores(id)
            )
        ''')
        cur.execute('''
            INSERT INTO restock_sessions_new
                (id, date, status, created_at, submitted_at, completed_at, store_id)
            SELECT id, date, status, created_at, submitted_at, completed_at, 1
            FROM restock_sessions
        ''')
        cur.execute('DROP TABLE restock_sessions')
        cur.execute('ALTER TABLE restock_sessions_new RENAME TO restock_sessions')
        cur.execute("INSERT OR IGNORE INTO _migrations (name) VALUES ('add_store_id_to_restock_sessions')")
        cur.execute('COMMIT')
    except Exception:
        cur.execute('ROLLBACK')
        raise
    finally:
        cur.execute('PRAGMA foreign_keys = ON')
        con.isolation_level = ''


def _migration_add_store_id_to_stock_transactions(con, cur):
    con.commit()
    con.isolation_level = None
    cur.execute('PRAGMA foreign_keys = OFF')
    try:
        cur.execute('BEGIN')
        cur.execute('DROP TABLE IF EXISTS stock_transactions_new')
        cur.execute('''
            CREATE TABLE stock_transactions_new (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL REFERENCES products(id),
                txn_type   TEXT    NOT NULL,
                qty        INTEGER NOT NULL,
                location   TEXT,
                date       TEXT    NOT NULL,
                notes      TEXT    DEFAULT '',
                created_at TEXT    DEFAULT (datetime('now')),
                store_id   INTEGER NOT NULL DEFAULT 1 REFERENCES stores(id)
            )
        ''')
        cur.execute('''
            INSERT INTO stock_transactions_new
                (id, product_id, txn_type, qty, location, date, notes, created_at, store_id)
            SELECT id, product_id, txn_type, qty, location, date, notes, created_at, 1
            FROM stock_transactions
        ''')
        cur.execute('DROP TABLE stock_transactions')
        cur.execute('ALTER TABLE stock_transactions_new RENAME TO stock_transactions')
        cur.execute("INSERT OR IGNORE INTO _migrations (name) VALUES ('add_store_id_to_stock_transactions')")
        cur.execute('COMMIT')
    except Exception:
        cur.execute('ROLLBACK')
        raise
    finally:
        cur.execute('PRAGMA foreign_keys = ON')
        con.isolation_level = ''


def _migration_add_store_id_to_shifts(con, cur):
    con.commit()
    con.isolation_level = None
    cur.execute('PRAGMA foreign_keys = OFF')
    try:
        cur.execute('BEGIN')
        cur.execute('DROP TABLE IF EXISTS shifts_new')
        cur.execute('''
            CREATE TABLE shifts_new (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
                date        TEXT    NOT NULL,
                start_time  TEXT    NOT NULL,
                end_time    TEXT    NOT NULL,
                assigned_by TEXT    NOT NULL,
                notes       TEXT    DEFAULT '',
                created_at  TEXT    DEFAULT (datetime('now')),
                updated_at  TEXT    DEFAULT (datetime('now')),
                store_id    INTEGER NOT NULL DEFAULT 1 REFERENCES stores(id),
                UNIQUE(employee_id, date)
            )
        ''')
        cur.execute('''
            INSERT INTO shifts_new
                (id, employee_id, date, start_time, end_time, assigned_by,
                 notes, created_at, updated_at, store_id)
            SELECT id, employee_id, date, start_time, end_time, assigned_by,
                   notes, created_at, updated_at, 1
            FROM shifts
        ''')
        cur.execute('DROP TABLE shifts')
        cur.execute('ALTER TABLE shifts_new RENAME TO shifts')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_shifts_date     ON shifts(date)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_shifts_employee ON shifts(employee_id)')
        cur.execute("INSERT OR IGNORE INTO _migrations (name) VALUES ('add_store_id_to_shifts')")
        cur.execute('COMMIT')
    except Exception:
        cur.execute('ROLLBACK')
        raise
    finally:
        cur.execute('PRAGMA foreign_keys = ON')
        con.isolation_level = ''


def _migration_add_store_id_to_availability(con, cur):
    con.commit()
    con.isolation_level = None
    cur.execute('PRAGMA foreign_keys = OFF')
    try:
        cur.execute('BEGIN')
        cur.execute('DROP TABLE IF EXISTS availability_new')
        cur.execute('''
            CREATE TABLE availability_new (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
                date        TEXT    NOT NULL,
                start_time  TEXT    NOT NULL,
                end_time    TEXT    NOT NULL,
                notes       TEXT    DEFAULT '',
                created_at  TEXT    DEFAULT (datetime('now')),
                updated_at  TEXT    DEFAULT (datetime('now')),
                store_id    INTEGER NOT NULL DEFAULT 1 REFERENCES stores(id),
                UNIQUE(employee_id, date)
            )
        ''')
        cur.execute('''
            INSERT INTO availability_new
                (id, employee_id, date, start_time, end_time,
                 notes, created_at, updated_at, store_id)
            SELECT id, employee_id, date, start_time, end_time,
                   notes, created_at, updated_at, 1
            FROM availability
        ''')
        cur.execute('DROP TABLE availability')
        cur.execute('ALTER TABLE availability_new RENAME TO availability')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_availability_date ON availability(date)')
        cur.execute("INSERT OR IGNORE INTO _migrations (name) VALUES ('add_store_id_to_availability')")
        cur.execute('COMMIT')
    except Exception:
        cur.execute('ROLLBACK')
        raise
    finally:
        cur.execute('PRAGMA foreign_keys = ON')
        con.isolation_level = ''


def _migration_add_store_id_to_stock_movements(con, cur):
    con.commit()
    con.isolation_level = None
    cur.execute('PRAGMA foreign_keys = OFF')
    try:
        cur.execute('BEGIN')
        cur.execute('DROP TABLE IF EXISTS stock_movements_new')
        cur.execute('''
            CREATE TABLE stock_movements_new (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id    INTEGER NOT NULL REFERENCES products(id),
                session_id    INTEGER REFERENCES restock_sessions(id),
                movement_type TEXT    NOT NULL,
                qty_change    INTEGER NOT NULL,
                location      TEXT    NOT NULL,
                created_at    TEXT    DEFAULT (datetime('now')),
                store_id      INTEGER NOT NULL DEFAULT 1 REFERENCES stores(id)
            )
        ''')
        cur.execute('''
            INSERT INTO stock_movements_new
                (id, product_id, session_id, movement_type,
                 qty_change, location, created_at, store_id)
            SELECT id, product_id, session_id, movement_type,
                   qty_change, location, created_at, 1
            FROM stock_movements
        ''')
        cur.execute('DROP TABLE stock_movements')
        cur.execute('ALTER TABLE stock_movements_new RENAME TO stock_movements')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_sm_product ON stock_movements(product_id)')
        cur.execute("INSERT OR IGNORE INTO _migrations (name) VALUES ('add_store_id_to_stock_movements')")
        cur.execute('COMMIT')
    except Exception:
        cur.execute('ROLLBACK')
        raise
    finally:
        cur.execute('PRAGMA foreign_keys = ON')
        con.isolation_level = ''


def _migration_add_color_to_stores(con, cur):
    cur.execute("PRAGMA table_info(stores)")
    cols = {r['name'] for r in cur.fetchall()}
    if 'color' not in cols:
        cur.execute("ALTER TABLE stores ADD COLUMN color TEXT NOT NULL DEFAULT '#6366f1'")
    cur.execute("INSERT OR IGNORE INTO _migrations (name) VALUES ('add_color_to_stores')")


def _migration_add_created_by_to_aliases(con, cur):
    """Add created_by column to product_aliases for auditing."""
    cur.execute("PRAGMA table_info(product_aliases)")
    cols = {r['name'] for r in cur.fetchall()}
    if 'created_by' not in cols:
        cur.execute("ALTER TABLE product_aliases ADD COLUMN created_by TEXT")
    cur.execute("INSERT OR IGNORE INTO _migrations (name) VALUES ('add_created_by_to_aliases')")


def _migration_seed_product_aliases(con, cur):
    """
    Seed known product aliases from production logs.
    Only runs once; only inserts if the matching product exists and alias is not duplicate.
    """
    from matcher import normalize as _norm  # local import — only used here

    # (alias_raw, jizhanming) — jizhanming must match products.jizhanming exactly (case-insensitive)
    seeds = [
        # Spec-provided seeds
        ('sa hipper',         'SA Original Hipper'),
        ('smiski hipper',     'Smiski Hipper'),
        ('smiski hippers',    'Smiski Hipper'),
        ('三丽鸥hippers',    '三丽鸥Hipper'),
        ('smiski cheers',     'Smiski Cheer'),
        ('随心配蓝',         '星星人随心配蓝'),
        ('随心配粉',         '星星人随心配粉'),
        ('crybaby度假',      '哭娃度假'),
        ('sa tatto stick',    'SA Tattoo Sticker'),
        # Additional aliases from common shorthand patterns
        ('sa original',       'SA Original Hipper'),
        ('smiski原版',        'Smiski Hipper'),
        ('smiski cheer',      'Smiski Cheer'),
        ('crybaby假期',      '哭娃度假'),
        ('随心配',           '星星人随心配蓝'),   # ambiguous — prefer blue variant
        ('sa tattoo',         'SA Tattoo Sticker'),
        ('sa sticker',        'SA Tattoo Sticker'),
    ]

    for alias_raw, jizhanming in seeds:
        alias_norm = _norm(alias_raw)
        if not alias_norm:
            continue
        cur.execute(
            'SELECT id FROM products WHERE LOWER(jizhanming) = LOWER(?)',
            (jizhanming,)
        )
        row = cur.fetchone()
        if not row:
            continue
        cur.execute('''
            INSERT OR IGNORE INTO product_aliases (product_id, alias, alias_norm, created_by)
            VALUES (?, ?, ?, 'system_seed')
        ''', (row['id'], alias_raw, alias_norm))

    cur.execute("INSERT OR IGNORE INTO _migrations (name) VALUES ('seed_product_aliases')")


def _migration_create_insights_tables(con, cur):
    cur.executescript('''
        CREATE TABLE IF NOT EXISTS insights (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            store        TEXT    NOT NULL,
            check_type   TEXT    NOT NULL,
            severity     TEXT    NOT NULL,
            title        TEXT    NOT NULL,
            body         TEXT    NOT NULL,
            product_id   INTEGER REFERENCES products(id),
            meta         TEXT    DEFAULT '{}',
            generated_at TEXT    DEFAULT (datetime('now')),
            dismissed_at TEXT,
            dismissed_by TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_insights_store   ON insights(store);
        CREATE INDEX IF NOT EXISTS idx_insights_type    ON insights(check_type);
        CREATE INDEX IF NOT EXISTS idx_insights_product ON insights(product_id);

        CREATE TABLE IF NOT EXISTS insight_thresholds (
            key         TEXT PRIMARY KEY,
            value       REAL NOT NULL,
            description TEXT,
            updated_at  TEXT DEFAULT (datetime('now'))
        );
    ''')
    cur.execute("INSERT OR IGNORE INTO _migrations (name) VALUES ('create_insights_tables')")


def _migration_seed_insight_thresholds(con, cur):
    defaults = [
        ('velocity_spike_ratio', 2.0,  'Flag if last 7d sales / prior 7d >= this (adapted by 2σ when ≥10 data points)'),
        ('velocity_drop_ratio',  0.3,  'Flag if last 7d sales / prior 7d <= this (adapted by 2σ when ≥10 data points)'),
        ('dead_stock_days',      14.0, 'Flag products in stock with no sales for this many days (adapted per-product when ≥5 sale events)'),
        ('stockout_days_runway', 7.0,  'Flag if estimated days of stock remaining < this'),
        ('revenue_gap_pct',      20.0, 'Flag if today revenue is this % below 7d rolling avg'),
        ('data_quality_days',    14.0, 'Flag stock records not updated in this many days'),
    ]
    for key, value, desc in defaults:
        cur.execute(
            'INSERT OR IGNORE INTO insight_thresholds (key, value, description) VALUES (?, ?, ?)',
            (key, value, desc),
        )
    cur.execute("INSERT OR IGNORE INTO _migrations (name) VALUES ('seed_insight_thresholds')")


def _migration_add_color_to_employees(con, cur):
    cur.execute("PRAGMA table_info(employees)")
    cols = {r['name'] for r in cur.fetchall()}
    if 'color' not in cols:
        cur.execute("ALTER TABLE employees ADD COLUMN color TEXT NOT NULL DEFAULT '#6366f1'")
    cur.execute("INSERT OR IGNORE INTO _migrations (name) VALUES ('add_color_to_employees')")


def _migration_drop_dan_per_xiang_column(con, cur):
    """Remove dan_per_xiang from products — carton (箱) level removed, hierarchy is now 盒/端 only."""
    cur.execute("PRAGMA table_info(products)")
    cols = {r['name'] for r in cur.fetchall()}
    if 'dan_per_xiang' not in cols:
        cur.execute("INSERT OR IGNORE INTO _migrations (name) VALUES ('drop_dan_per_xiang_column')")
        return
    con.isolation_level = None
    try:
        cur.execute('PRAGMA foreign_keys = OFF')
        cur.execute('BEGIN')
        cur.execute('DROP TABLE IF EXISTS products_new')
        cur.execute('''
            CREATE TABLE products_new (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                sku               TEXT UNIQUE,
                name_cn_en        TEXT,
                jizhanming        TEXT,
                price             REAL,
                ip_series         TEXT,
                product_type      TEXT,
                brand             TEXT,
                release_date      TEXT,
                edition_size      TEXT,
                channel           TEXT,
                hidden            TEXT,
                style_notes       TEXT,
                notes             TEXT DEFAULT '',
                search_blob       TEXT,
                boxes_per_dan     INTEGER,
                hidden_count      TEXT    NOT NULL DEFAULT '0',
                hidden_has_small  INTEGER NOT NULL DEFAULT 0,
                hidden_has_large  INTEGER NOT NULL DEFAULT 0,
                hidden_prob_small TEXT    NOT NULL DEFAULT '',
                hidden_prob_large TEXT    NOT NULL DEFAULT '',
                is_bestseller     INTEGER NOT NULL DEFAULT 0
            )
        ''')
        cur.execute('''
            INSERT INTO products_new
            SELECT id, sku, name_cn_en, jizhanming, price, ip_series, product_type,
                   brand, release_date, edition_size, channel, hidden, style_notes, notes,
                   search_blob, boxes_per_dan, hidden_count, hidden_has_small, hidden_has_large,
                   hidden_prob_small, hidden_prob_large, is_bestseller
            FROM products
        ''')
        cur.execute('DROP TABLE products')
        cur.execute('ALTER TABLE products_new RENAME TO products')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_products_sku ON products(sku)')
        cur.execute("INSERT OR IGNORE INTO _migrations (name) VALUES ('drop_dan_per_xiang_column')")
        cur.execute('COMMIT')
    except Exception:
        cur.execute('ROLLBACK')
        raise
    finally:
        cur.execute('PRAGMA foreign_keys = ON')
        con.isolation_level = ''


def _migration_create_match_corrections(con, cur):
    cur.executescript('''
        CREATE TABLE IF NOT EXISTS match_corrections (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_name     TEXT NOT NULL,
            norm_name    TEXT NOT NULL,
            product_id   INTEGER NOT NULL,
            fuzzy_score  INTEGER,
            top_score    INTEGER,
            was_top      INTEGER DEFAULT 0,
            store        TEXT,
            created_at   TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_mc_norm ON match_corrections(norm_name);
        CREATE INDEX IF NOT EXISTS idx_mc_pid  ON match_corrections(product_id);
    ''')
    cur.execute("INSERT OR IGNORE INTO _migrations (name) VALUES ('create_match_corrections')")


def _migration_create_app_settings_table(con, cur):
    cur.execute('''
        CREATE TABLE IF NOT EXISTS app_settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    cur.execute("INSERT OR IGNORE INTO _migrations (name) VALUES ('create_app_settings_table')")


def _get_migrations():
    return [
        ('create_stores_table',                 _migration_create_stores_table),
        ('create_employee_stores_table',         _migration_create_employee_stores_table),
        ('migrate_stock_to_per_store',           _migration_migrate_stock_to_per_store),
        ('add_store_id_to_inventory_checks',     _migration_add_store_id_to_inventory_checks),
        ('add_store_id_to_restock_sessions',     _migration_add_store_id_to_restock_sessions),
        ('add_store_id_to_stock_transactions',   _migration_add_store_id_to_stock_transactions),
        ('add_store_id_to_shifts',               _migration_add_store_id_to_shifts),
        ('add_store_id_to_availability',         _migration_add_store_id_to_availability),
        ('add_store_id_to_stock_movements',      _migration_add_store_id_to_stock_movements),
        ('drop_dan_per_xiang_column',            _migration_drop_dan_per_xiang_column),
        ('add_color_to_stores',                  _migration_add_color_to_stores),
        ('add_color_to_employees',               _migration_add_color_to_employees),
        ('add_created_by_to_aliases',            _migration_add_created_by_to_aliases),
        ('seed_product_aliases',                 _migration_seed_product_aliases),
        ('create_insights_tables',               _migration_create_insights_tables),
        ('seed_insight_thresholds',              _migration_seed_insight_thresholds),
        ('create_match_corrections',             _migration_create_match_corrections),
        ('create_app_settings_table',            _migration_create_app_settings_table),
    ]


def _run_migrations(con, cur):
    for name, fn in _get_migrations():
        cur.execute('SELECT 1 FROM _migrations WHERE name = ?', (name,))
        if cur.fetchone():
            continue
        fn(con, cur)


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

    # ── daily_sales: widen UNIQUE to (product_id, date, store) ────────────
    cur.execute("SELECT 1 FROM _migrations WHERE name='daily_sales_unique_add_store'")
    if not cur.fetchone():
        cur.execute('PRAGMA foreign_keys = OFF')
        cur.execute('DROP TABLE IF EXISTS daily_sales_new')
        cur.execute('''
            CREATE TABLE daily_sales_new (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL REFERENCES products(id),
                date       TEXT    NOT NULL,
                qty_sold   INTEGER NOT NULL DEFAULT 0,
                notes      TEXT    DEFAULT '',
                created_at TEXT    DEFAULT (datetime('now')),
                qty_pos    INTEGER NOT NULL DEFAULT 0,
                qty_cash   INTEGER NOT NULL DEFAULT 0,
                store      TEXT    NOT NULL DEFAULT 'DT',
                UNIQUE(product_id, date, store)
            )
        ''')
        cur.execute('INSERT INTO daily_sales_new SELECT * FROM daily_sales')
        cur.execute('DROP TABLE daily_sales')
        cur.execute('ALTER TABLE daily_sales_new RENAME TO daily_sales')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_daily_sales_date ON daily_sales(date)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_daily_sales_pid  ON daily_sales(product_id)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_ds_store ON daily_sales(store)')
        con.commit()
        cur.execute('PRAGMA foreign_keys = ON')
        cur.execute("INSERT OR IGNORE INTO _migrations (name) VALUES ('daily_sales_unique_add_store')")
        con.commit()

    # ── daily_sales: per-channel quantities, price snapshot, raw input ──────
    # (added AFTER the unique-key rebuild so fresh-DB SELECT * copies line up)
    cur.execute("PRAGMA table_info(daily_sales)")
    ds_cols_v2 = {r['name'] for r in cur.fetchall()}
    for col, defn in (
        ('qty_claw',     "INTEGER NOT NULL DEFAULT 0"),
        ('qty_display',  "INTEGER NOT NULL DEFAULT 0"),
        ('qty_employee', "INTEGER NOT NULL DEFAULT 0"),
        ('unit_price',   "REAL"),
        ('raw_name',     "TEXT NOT NULL DEFAULT ''"),
    ):
        if col not in ds_cols_v2:
            cur.execute(f'ALTER TABLE daily_sales ADD COLUMN {col} {defn}')

    # One-time backfill: rows whose notes are exactly the old channel tag were
    # display/claw/employee sales stored in qty_pos — move them to their column.
    cur.execute("SELECT 1 FROM _migrations WHERE name='split_channel_columns_backfill'")
    if not cur.fetchone():
        for tag, col in (('display_sold', 'qty_display'),
                         ('claw_machine', 'qty_claw'),
                         ('employee_discount', 'qty_employee')):
            cur.execute(f'''
                UPDATE daily_sales
                SET {col} = qty_pos, qty_pos = 0
                WHERE notes = ? AND qty_pos > 0 AND {col} = 0
            ''', (tag,))
        cur.execute("INSERT OR IGNORE INTO _migrations (name) VALUES ('split_channel_columns_backfill')")

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

    _run_migrations(con, cur)

    # ── products.sheet_ref: learned stable key to the Google Sheet's 编号 ────
    # Added after _run_migrations so the legacy products-table rebuild
    # (drop_dan_per_xiang_column) can never drop it.
    cur.execute("PRAGMA table_info(products)")
    if 'sheet_ref' not in {r['name'] for r in cur.fetchall()}:
        cur.execute("ALTER TABLE products ADD COLUMN sheet_ref TEXT")
    cur.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_products_sheet_ref
        ON products(sheet_ref) WHERE sheet_ref IS NOT NULL AND sheet_ref != ''
    ''')

    con.commit()
    con.close()
