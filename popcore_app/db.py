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
    try:
        cur.execute('BEGIN')
        cur.execute('''
            ALTER TABLE restock_sessions
            ADD COLUMN store_id INTEGER NOT NULL DEFAULT 1 REFERENCES stores(id)
        ''')
        cur.execute("INSERT OR IGNORE INTO _migrations (name) VALUES ('add_store_id_to_restock_sessions')")
        cur.execute('COMMIT')
    except Exception:
        cur.execute('ROLLBACK')
        raise
    finally:
        con.isolation_level = ''


def _migration_add_store_id_to_stock_transactions(con, cur):
    con.commit()
    con.isolation_level = None
    try:
        cur.execute('BEGIN')
        cur.execute('''
            ALTER TABLE stock_transactions
            ADD COLUMN store_id INTEGER NOT NULL DEFAULT 1 REFERENCES stores(id)
        ''')
        cur.execute("INSERT OR IGNORE INTO _migrations (name) VALUES ('add_store_id_to_stock_transactions')")
        cur.execute('COMMIT')
    except Exception:
        cur.execute('ROLLBACK')
        raise
    finally:
        con.isolation_level = ''


def _migration_add_store_id_to_shifts(con, cur):
    con.commit()
    con.isolation_level = None
    try:
        cur.execute('BEGIN')
        cur.execute('''
            ALTER TABLE shifts
            ADD COLUMN store_id INTEGER NOT NULL DEFAULT 1 REFERENCES stores(id)
        ''')
        cur.execute("INSERT OR IGNORE INTO _migrations (name) VALUES ('add_store_id_to_shifts')")
        cur.execute('COMMIT')
    except Exception:
        cur.execute('ROLLBACK')
        raise
    finally:
        con.isolation_level = ''


def _migration_add_store_id_to_availability(con, cur):
    con.commit()
    con.isolation_level = None
    try:
        cur.execute('BEGIN')
        cur.execute('''
            ALTER TABLE availability
            ADD COLUMN store_id INTEGER NOT NULL DEFAULT 1 REFERENCES stores(id)
        ''')
        cur.execute("INSERT OR IGNORE INTO _migrations (name) VALUES ('add_store_id_to_availability')")
        cur.execute('COMMIT')
    except Exception:
        cur.execute('ROLLBACK')
        raise
    finally:
        con.isolation_level = ''


def _migration_add_store_id_to_stock_movements(con, cur):
    con.commit()
    con.isolation_level = None
    try:
        cur.execute('BEGIN')
        cur.execute('''
            ALTER TABLE stock_movements
            ADD COLUMN store_id INTEGER NOT NULL DEFAULT 1 REFERENCES stores(id)
        ''')
        cur.execute("INSERT OR IGNORE INTO _migrations (name) VALUES ('add_store_id_to_stock_movements')")
        cur.execute('COMMIT')
    except Exception:
        cur.execute('ROLLBACK')
        raise
    finally:
        con.isolation_level = ''


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

    con.commit()
    con.close()
