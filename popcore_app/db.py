"""
db.py — database helpers shared across all blueprints.
"""
import sqlite3
import os
from flask import g

BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
DB_PATH        = os.environ.get('POPCORE_DB_PATH', os.path.join(BASE_DIR, 'popcore.db'))
STATIC_DIR     = os.environ.get('POPCORE_STATIC_DIR', os.path.join(BASE_DIR, 'static'))
HIDDEN_IMG_DIR = os.environ.get('POPCORE_HIDDEN_IMG_DIR', os.path.join(BASE_DIR, 'uploads', 'hidden_imgs'))


def esc_csv(v):
    """Escape a value for CSV output (RFC 4180)."""
    s = str(v) if v is not None else ''
    if isinstance(v, str) and s.lstrip(' \t\r').startswith(('=', '+', '-', '@')):
        s = "'" + s
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


def _migration_add_ical_token_to_employees(con, cur):
    cur.execute("PRAGMA table_info(employees)")
    cols = {r['name'] for r in cur.fetchall()}
    if 'ical_token' not in cols:
        cur.execute("ALTER TABLE employees ADD COLUMN ical_token TEXT")
    cur.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_employees_ical_token
        ON employees(ical_token) WHERE ical_token IS NOT NULL
    ''')
    cur.execute("INSERT OR IGNORE INTO _migrations (name) VALUES ('add_ical_token_to_employees')")


def _migration_create_app_settings_table(con, cur):
    cur.execute('''
        CREATE TABLE IF NOT EXISTS app_settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    cur.execute("INSERT OR IGNORE INTO _migrations (name) VALUES ('create_app_settings_table')")


def _migration_add_position_to_shifts(con, cur):
    """Shifts can carry an in-store position (e.g. Front / Cashier / End)."""
    cur.execute("PRAGMA table_info(shifts)")
    cols = {r['name'] for r in cur.fetchall()}
    if 'position' not in cols:
        cur.execute("ALTER TABLE shifts ADD COLUMN position TEXT NOT NULL DEFAULT ''")
    cur.execute("INSERT OR IGNORE INTO _migrations (name) VALUES ('add_position_to_shifts')")


def _migration_add_is_trainee_to_employees(con, cur):
    """Trainees are employees rows without a real Auth0 login (synthetic
    auth0_id) so they can be scheduled like everyone else."""
    cur.execute("PRAGMA table_info(employees)")
    cols = {r['name'] for r in cur.fetchall()}
    if 'is_trainee' not in cols:
        cur.execute("ALTER TABLE employees ADD COLUMN is_trainee INTEGER NOT NULL DEFAULT 0")
    cur.execute("INSERT OR IGNORE INTO _migrations (name) VALUES ('add_is_trainee_to_employees')")


def _migration_add_is_schedulable_to_employees(con, cur):
    """Whether managers may create new shifts for an employee."""
    cur.execute("PRAGMA table_info(employees)")
    cols = {r['name'] for r in cur.fetchall()}
    if 'is_schedulable' not in cols:
        cur.execute(
            "ALTER TABLE employees "
            "ADD COLUMN is_schedulable INTEGER NOT NULL DEFAULT 1"
        )
    cur.execute(
        "INSERT OR IGNORE INTO _migrations (name) "
        "VALUES ('add_is_schedulable_to_employees')"
    )


def _migration_create_schedule_notes_table(con, cur):
    """Free-form manager notes attached to a schedule period (month/week/day)."""
    cur.execute('''
        CREATE TABLE IF NOT EXISTS schedule_notes (
            period_key TEXT PRIMARY KEY,
            content    TEXT NOT NULL DEFAULT '',
            updated_by TEXT NOT NULL DEFAULT '',
            updated_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    cur.execute("INSERT OR IGNORE INTO _migrations (name) VALUES ('create_schedule_notes_table')")


def _migration_create_schedule_checklist_table(con, cur):
    """Per-period 'considered' marks for people intentionally left unscheduled,
    so the coverage checklist can prove everyone was accounted for."""
    cur.execute('''
        CREATE TABLE IF NOT EXISTS schedule_checklist (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            period_key  TEXT    NOT NULL,
            employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
            considered  INTEGER NOT NULL DEFAULT 0,
            note        TEXT    NOT NULL DEFAULT '',
            updated_by  TEXT    NOT NULL DEFAULT '',
            updated_at  TEXT    DEFAULT (datetime('now')),
            UNIQUE(period_key, employee_id)
        )
    ''')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_sched_checklist_key ON schedule_checklist(period_key)')
    cur.execute("INSERT OR IGNORE INTO _migrations (name) VALUES ('create_schedule_checklist_table')")


def _migration_assign_curated_employee_colors(con, cur):
    """One-time reset of employee colors onto the curated calendar palette
    (replaces the old free-picked neons / near-whites that were unreadable).
    Keep in sync with EMPLOYEE_PALETTE in frontend/src/lib/palette.ts."""
    palette = [
        '#3D74C4', '#2E7FA3', '#2C8A86', '#2E8A5B', '#5E8A32', '#8B7C2A',
        '#C0762F', '#C75B54', '#B4508F', '#9455C8', '#5F63C2', '#8A5A3C',
    ]
    cur.execute('SELECT id FROM employees ORDER BY id')
    for i, row in enumerate(cur.fetchall()):
        cur.execute('UPDATE employees SET color = ? WHERE id = ?',
                    (palette[i % len(palette)], row['id']))
    cur.execute("INSERT OR IGNORE INTO _migrations (name) VALUES ('assign_curated_employee_colors')")


def _migration_create_catalog_identity(con, cur):
    """Add explicit catalog identity without interpreting legacy quantities."""
    cur.executescript('''
        CREATE TABLE IF NOT EXISTS product_series (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        );
    ''')
    cur.execute('PRAGMA table_info(products)')
    columns = {row['name'] for row in cur.fetchall()}
    additions = (
        ('series_id', 'INTEGER REFERENCES product_series(id)'),
        ('stock_form', "TEXT CHECK (stock_form IS NULL OR stock_form IN ('random_box','sealed_set','confirmed_design','ordinary'))"),
        ('stock_unit', "TEXT CHECK (stock_unit IS NULL OR stock_unit IN ('box','set','piece'))"),
        ('design_name', 'TEXT'),
        ('identity_status', "TEXT NOT NULL DEFAULT 'unverified' CHECK (identity_status IN ('unverified','verified'))"),
    )
    for column, definition in additions:
        if column not in columns:
            cur.execute(f'ALTER TABLE products ADD COLUMN {column} {definition}')

    cur.executescript('''
        CREATE INDEX IF NOT EXISTS idx_products_series_id
        ON products(series_id);

        CREATE TABLE IF NOT EXISTS product_conversions (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            source_product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
            target_product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
            output_per_input   INTEGER NOT NULL CHECK (output_per_input > 0),
            version            INTEGER NOT NULL CHECK (version > 0),
            CHECK (source_product_id != target_product_id),
            UNIQUE(source_product_id, version)
        );
        CREATE INDEX IF NOT EXISTS idx_product_conversions_target
        ON product_conversions(target_product_id);

        CREATE TABLE IF NOT EXISTS product_barcodes (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            code               TEXT NOT NULL CHECK (code != ''),
            product_id         INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
            code_kind          TEXT NOT NULL CHECK (code_kind IN ('manufacturer','internal')),
            input_unit         TEXT NOT NULL CHECK (input_unit IN ('box','set','piece')),
            quantity_per_scan  INTEGER NOT NULL CHECK (quantity_per_scan > 0),
            UNIQUE(code, product_id, code_kind, input_unit)
        );
        CREATE INDEX IF NOT EXISTS idx_product_barcodes_code
        ON product_barcodes(code);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_product_barcodes_internal
        ON product_barcodes(code) WHERE code_kind='internal';
    ''')

    cur.execute(
        "INSERT OR IGNORE INTO _migrations(name) "
        "VALUES ('create_catalog_identity')"
    )

def _migration_create_inventory_locations_and_access(con, cur):
    """Create reviewed physical locations and explicit operations scope."""
    cur.executescript('''
        CREATE TABLE IF NOT EXISTS inventory_locations (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id  INTEGER NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
            code      TEXT NOT NULL,
            name      TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
            UNIQUE(store_id, code)
        );

        CREATE TABLE IF NOT EXISTS inventory_access (
            auth0_sub TEXT NOT NULL,
            store_id  INTEGER NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
            PRIMARY KEY(auth0_sub, store_id)
        );

        CREATE TABLE IF NOT EXISTS inventory_scope_state (
            store_id            INTEGER NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
            location_id         INTEGER NOT NULL REFERENCES inventory_locations(id) ON DELETE RESTRICT,
            opening_verified    INTEGER NOT NULL DEFAULT 0 CHECK (opening_verified IN (0,1)),
            opening_document_id INTEGER,
            PRIMARY KEY(store_id, location_id),
            UNIQUE(location_id)
        );
    ''')
    reviewed = {
        'DT': (('floor', 'Floor'), ('upstairs', 'Upstairs')),
        'MK': (('floor', 'Floor'), ('warehouse', 'Warehouse')),
    }
    for store_code, locations in reviewed.items():
        store = cur.execute(
            'SELECT id FROM stores WHERE code=?', (store_code,)
        ).fetchone()
        if store is None:
            continue
        store_id = store['id']
        for code, name in locations:
            cur.execute(
                """INSERT OR IGNORE INTO inventory_locations
                   (store_id, code, name, is_active) VALUES (?, ?, ?, 1)""",
                (store_id, code, name),
            )
            location_id = cur.execute(
                'SELECT id FROM inventory_locations WHERE store_id=? AND code=?',
                (store_id, code),
            ).fetchone()['id']
            cur.execute(
                """INSERT OR IGNORE INTO inventory_scope_state
                   (store_id, location_id, opening_verified)
                   VALUES (?, ?, 0)""",
                (store_id, location_id),
            )
    cur.execute(
        "INSERT OR IGNORE INTO _migrations(name) "
        "VALUES ('create_inventory_locations_and_access')"
    )

def _migration_create_inventory_posting_core(con, cur):
    """Create the append-only inventory ledger and derived balances."""
    cur.executescript('''
        CREATE TABLE IF NOT EXISTS inventory_mode (
            id                 INTEGER PRIMARY KEY CHECK (id=1),
            mode               TEXT NOT NULL CHECK (mode IN ('legacy','authoritative')),
            cutover_identifier TEXT,
            cutover_at         TEXT
        );
        INSERT OR IGNORE INTO inventory_mode(id, mode) VALUES (1, 'legacy');

        CREATE TABLE IF NOT EXISTS inventory_documents (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            kind          TEXT NOT NULL CHECK (kind IN ('opening','receipt','move','consume','open_set','correction','restock_complete')),
            request_key   TEXT NOT NULL UNIQUE,
            payload_hash  TEXT NOT NULL,
            source_type   TEXT,
            source_id     TEXT,
            actor_sub     TEXT NOT NULL,
            business_date TEXT NOT NULL,
            posted_at     TEXT,
            correction_of INTEGER REFERENCES inventory_documents(id) ON DELETE RESTRICT,
            status        TEXT NOT NULL CHECK (status IN ('building','posted')),
            stored_result TEXT,
            CHECK ((source_type IS NULL) = (source_id IS NULL))
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_inventory_document_source
        ON inventory_documents(source_type, source_id)
        WHERE source_type IS NOT NULL AND source_id IS NOT NULL;

        CREATE TABLE IF NOT EXISTS inventory_document_lines (
            document_id       INTEGER NOT NULL REFERENCES inventory_documents(id) ON DELETE RESTRICT,
            line_no           INTEGER NOT NULL CHECK (line_no > 0),
            product_id        INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
            native_unit       TEXT NOT NULL CHECK (native_unit IN ('box','set','piece')),
            quantity          INTEGER NOT NULL CHECK (quantity > 0),
            from_location_id  INTEGER REFERENCES inventory_locations(id) ON DELETE RESTRICT,
            from_disposition  TEXT CHECK (from_disposition IS NULL OR from_disposition IN ('saleable','trade','display','hold','damaged','transit')),
            to_location_id    INTEGER REFERENCES inventory_locations(id) ON DELETE RESTRICT,
            to_disposition    TEXT CHECK (to_disposition IS NULL OR to_disposition IN ('saleable','trade','display','hold','damaged','transit')),
            from_version      INTEGER CHECK (from_version IS NULL OR from_version >= 0),
            to_version        INTEGER CHECK (to_version IS NULL OR to_version >= 0),
            conversion_id     INTEGER REFERENCES product_conversions(id) ON DELETE RESTRICT,
            conversion_factor INTEGER CHECK (conversion_factor IS NULL OR conversion_factor > 0),
            PRIMARY KEY(document_id, line_no)
        );

        CREATE TABLE IF NOT EXISTS inventory_movements (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id  INTEGER NOT NULL,
            line_no      INTEGER NOT NULL,
            product_id   INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
            location_id  INTEGER NOT NULL REFERENCES inventory_locations(id) ON DELETE RESTRICT,
            disposition  TEXT NOT NULL CHECK (disposition IN ('saleable','trade','display','hold','damaged','transit')),
            quantity     INTEGER NOT NULL CHECK (quantity != 0),
            FOREIGN KEY(document_id, line_no)
                REFERENCES inventory_document_lines(document_id, line_no)
                ON DELETE RESTRICT
        );
        CREATE INDEX IF NOT EXISTS idx_inventory_movements_balance
        ON inventory_movements(product_id, location_id, disposition, id);

        CREATE TABLE IF NOT EXISTS inventory_balances (
            product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
            location_id INTEGER NOT NULL REFERENCES inventory_locations(id) ON DELETE RESTRICT,
            disposition TEXT NOT NULL CHECK (disposition IN ('saleable','trade','display','hold','damaged','transit')),
            quantity    INTEGER NOT NULL CHECK (quantity >= 0),
            version     INTEGER NOT NULL CHECK (version >= 0),
            PRIMARY KEY(product_id, location_id, disposition)
        );

        CREATE TRIGGER IF NOT EXISTS inventory_documents_posted_no_update
        BEFORE UPDATE ON inventory_documents
        WHEN OLD.status='posted'
        BEGIN
            SELECT RAISE(ABORT, 'posted inventory document is immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS inventory_documents_posted_no_delete
        BEFORE DELETE ON inventory_documents
        WHEN OLD.status='posted'
        BEGIN
            SELECT RAISE(ABORT, 'posted inventory document is immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS inventory_lines_posted_no_update
        BEFORE UPDATE ON inventory_document_lines
        WHEN EXISTS (
            SELECT 1 FROM inventory_documents d
            WHERE d.id=OLD.document_id AND d.status='posted'
        )
        BEGIN
            SELECT RAISE(ABORT, 'posted inventory line is immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS inventory_lines_posted_no_delete
        BEFORE DELETE ON inventory_document_lines
        WHEN EXISTS (
            SELECT 1 FROM inventory_documents d
            WHERE d.id=OLD.document_id AND d.status='posted'
        )
        BEGIN
            SELECT RAISE(ABORT, 'posted inventory line is immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS inventory_movements_no_update
        BEFORE UPDATE ON inventory_movements
        BEGIN
            SELECT RAISE(ABORT, 'inventory movement is immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS inventory_movements_no_delete
        BEFORE DELETE ON inventory_movements
        BEGIN
            SELECT RAISE(ABORT, 'inventory movement is immutable');
        END;
    ''')
    cur.execute(
        "INSERT OR IGNORE INTO _migrations(name) "
        "VALUES ('create_inventory_posting_core')"
    )

def _migration_create_open_set_provenance(con, cur):
    """Track only provenance retained for opened reviewed sets."""
    if cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='product_conversions'"
    ).fetchone() is None:
        return
    cur.executescript('''
        CREATE TABLE IF NOT EXISTS inventory_open_sets (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            opening_document_id INTEGER NOT NULL REFERENCES inventory_documents(id) ON DELETE RESTRICT,
            random_product_id   INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
            location_id         INTEGER NOT NULL REFERENCES inventory_locations(id) ON DELETE RESTRICT,
            purpose             TEXT NOT NULL CHECK (purpose IN ('customer_tray','replenishment')),
            remaining_qty       INTEGER NOT NULL CHECK (remaining_qty >= 0),
            UNIQUE(opening_document_id, random_product_id, location_id, purpose)
        );
        CREATE INDEX IF NOT EXISTS idx_inventory_open_sets_balance
        ON inventory_open_sets(random_product_id, location_id, remaining_qty);

        CREATE UNIQUE INDEX IF NOT EXISTS idx_inventory_full_reversal
        ON inventory_documents(correction_of)
        WHERE kind='correction' AND correction_of IS NOT NULL;

        CREATE TRIGGER IF NOT EXISTS referenced_conversion_no_update
        BEFORE UPDATE ON product_conversions
        WHEN EXISTS (
            SELECT 1 FROM inventory_document_lines l
            WHERE l.conversion_id=OLD.id
        )
        BEGIN
            SELECT RAISE(ABORT, 'referenced product conversion is immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS referenced_conversion_no_delete
        BEFORE DELETE ON product_conversions
        WHEN EXISTS (
            SELECT 1 FROM inventory_document_lines l
            WHERE l.conversion_id=OLD.id
        )
        BEGIN
            SELECT RAISE(ABORT, 'referenced product conversion is immutable');
        END;
    ''')
    cur.execute(
        "INSERT OR IGNORE INTO _migrations(name) "
        "VALUES ('create_open_set_provenance')"
    )


def _migration_link_inventory_compatibility(con, cur):
    """Link legacy history rows to immutable inventory documents."""
    for table in ('stock_transactions', 'stock_movements'):
        columns = {row['name'] for row in cur.execute(f'PRAGMA table_info({table})')}
        if 'inventory_document_id' not in columns:
            cur.execute(
                f'ALTER TABLE {table} ADD COLUMN inventory_document_id INTEGER'
            )
    cur.executescript('''
        CREATE INDEX IF NOT EXISTS idx_stock_transactions_inventory_document
        ON stock_transactions(inventory_document_id);
        CREATE INDEX IF NOT EXISTS idx_stock_movements_inventory_document
        ON stock_movements(inventory_document_id);
    ''')
    cur.execute(
        "INSERT OR IGNORE INTO _migrations(name) "
        "VALUES ('link_inventory_compatibility')"
    )


def _migration_create_goods_workflows(con, cur):
    """Create versioned receiving, delivery, count, and target records."""
    cur.executescript('''
        CREATE TABLE IF NOT EXISTS operation_requests (
            request_key  TEXT PRIMARY KEY,
            operation    TEXT NOT NULL,
            resource_id  INTEGER,
            actor_sub    TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            stored_result TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS goods_receipts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id INTEGER NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
            destination_location_id INTEGER NOT NULL REFERENCES inventory_locations(id) ON DELETE RESTRICT,
            business_date TEXT NOT NULL,
            shipment_reference TEXT,
            supplier TEXT,
            status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','posted','cancelled')),
            version INTEGER NOT NULL DEFAULT 1 CHECK(version > 0),
            created_by TEXT NOT NULL,
            inventory_document_id INTEGER REFERENCES inventory_documents(id) ON DELETE RESTRICT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS goods_receipt_lines (
            receipt_id INTEGER NOT NULL REFERENCES goods_receipts(id) ON DELETE CASCADE,
            line_no INTEGER NOT NULL CHECK(line_no > 0),
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
            native_unit TEXT NOT NULL CHECK(native_unit IN ('box','set','piece')),
            expected_quantity INTEGER CHECK(expected_quantity IS NULL OR expected_quantity >= 0),
            saleable_quantity INTEGER NOT NULL DEFAULT 0 CHECK(saleable_quantity >= 0),
            damaged_quantity INTEGER NOT NULL DEFAULT 0 CHECK(damaged_quantity >= 0),
            hold_quantity INTEGER NOT NULL DEFAULT 0 CHECK(hold_quantity >= 0),
            discrepancy_note TEXT,
            PRIMARY KEY(receipt_id, line_no)
        );

        CREATE TABLE IF NOT EXISTS inventory_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL CHECK(kind IN ('restock','transfer')),
            restock_session_id INTEGER UNIQUE REFERENCES restock_sessions(id) ON DELETE RESTRICT,
            source_location_id INTEGER NOT NULL REFERENCES inventory_locations(id) ON DELETE RESTRICT,
            destination_location_id INTEGER NOT NULL REFERENCES inventory_locations(id) ON DELETE RESTRICT,
            business_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'planned' CHECK(status IN ('planned','active','completed','cancelled')),
            version INTEGER NOT NULL DEFAULT 1 CHECK(version > 0),
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            CHECK(source_location_id <> destination_location_id)
        );
        CREATE TABLE IF NOT EXISTS inventory_delivery_lines (
            delivery_id INTEGER NOT NULL REFERENCES inventory_deliveries(id) ON DELETE CASCADE,
            line_no INTEGER NOT NULL CHECK(line_no > 0),
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
            native_unit TEXT NOT NULL CHECK(native_unit IN ('box','set','piece')),
            requested_quantity INTEGER NOT NULL CHECK(requested_quantity > 0),
            dispatched_quantity INTEGER NOT NULL DEFAULT 0 CHECK(dispatched_quantity >= 0),
            received_quantity INTEGER NOT NULL DEFAULT 0 CHECK(received_quantity >= 0),
            returned_quantity INTEGER NOT NULL DEFAULT 0 CHECK(returned_quantity >= 0),
            loss_quantity INTEGER NOT NULL DEFAULT 0 CHECK(loss_quantity >= 0),
            short_quantity INTEGER NOT NULL DEFAULT 0 CHECK(short_quantity >= 0),
            PRIMARY KEY(delivery_id, line_no),
            CHECK(dispatched_quantity <= requested_quantity),
            CHECK(received_quantity + returned_quantity + loss_quantity <= dispatched_quantity),
            CHECK(short_quantity <= requested_quantity - dispatched_quantity)
        );
        CREATE TABLE IF NOT EXISTS inventory_delivery_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            delivery_id INTEGER NOT NULL REFERENCES inventory_deliveries(id) ON DELETE RESTRICT,
            action TEXT NOT NULL CHECK(action IN ('dispatch','receive','return','resolve_loss','short_close')),
            actor_sub TEXT NOT NULL,
            reason TEXT,
            inventory_document_id INTEGER REFERENCES inventory_documents(id) ON DELETE RESTRICT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS inventory_delivery_event_lines (
            event_id INTEGER NOT NULL REFERENCES inventory_delivery_events(id) ON DELETE RESTRICT,
            line_no INTEGER NOT NULL,
            delivery_line_no INTEGER NOT NULL,
            quantity INTEGER NOT NULL CHECK(quantity > 0),
            disposition TEXT NOT NULL CHECK(disposition IN ('saleable','hold','damaged')),
            PRIMARY KEY(event_id, line_no)
        );

        CREATE TABLE IF NOT EXISTS inventory_counts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id INTEGER NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
            location_id INTEGER NOT NULL REFERENCES inventory_locations(id) ON DELETE RESTRICT,
            disposition TEXT NOT NULL DEFAULT 'saleable' CHECK(disposition IN ('saleable','trade','display','hold','damaged')),
            business_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','submitted','approved','returned')),
            version INTEGER NOT NULL DEFAULT 1 CHECK(version > 0),
            created_by TEXT NOT NULL,
            reviewed_by TEXT,
            review_reason TEXT,
            inventory_document_id INTEGER REFERENCES inventory_documents(id) ON DELETE RESTRICT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS inventory_count_lines (
            count_id INTEGER NOT NULL REFERENCES inventory_counts(id) ON DELETE CASCADE,
            line_no INTEGER NOT NULL CHECK(line_no > 0),
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
            native_unit TEXT NOT NULL CHECK(native_unit IN ('box','set','piece')),
            expected_quantity INTEGER NOT NULL CHECK(expected_quantity >= 0),
            observed_quantity INTEGER NOT NULL CHECK(observed_quantity >= 0),
            captured_balance_version INTEGER NOT NULL CHECK(captured_balance_version >= 0),
            PRIMARY KEY(count_id, line_no)
        );
        CREATE TABLE IF NOT EXISTS inventory_floor_targets (
            location_id INTEGER NOT NULL REFERENCES inventory_locations(id) ON DELETE CASCADE,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            min_quantity INTEGER NOT NULL CHECK(min_quantity >= 0),
            max_quantity INTEGER NOT NULL CHECK(max_quantity >= min_quantity),
            version INTEGER NOT NULL DEFAULT 1 CHECK(version > 0),
            updated_by TEXT NOT NULL,
            PRIMARY KEY(location_id, product_id)
        );
        CREATE INDEX IF NOT EXISTS idx_delivery_open
        ON inventory_deliveries(status, source_location_id, destination_location_id);
        CREATE INDEX IF NOT EXISTS idx_receipt_store_date
        ON goods_receipts(store_id, business_date);
        CREATE INDEX IF NOT EXISTS idx_count_store_date
        ON inventory_counts(store_id, business_date);
    ''')
    cur.execute(
        "INSERT OR IGNORE INTO _migrations(name) VALUES ('create_goods_workflows')"
    )


def _migration_add_delivery_provenance(con, cur):
    """Retain selected opened-set identity while its units are in delivery transit."""
    columns = {
        row['name'] for row in cur.execute(
            'PRAGMA table_info(inventory_delivery_lines)'
        )
    }
    additions = (
        ('open_set_id', 'INTEGER REFERENCES inventory_open_sets(id) ON DELETE RESTRICT'),
        ('open_set_opening_document_id', 'INTEGER REFERENCES inventory_documents(id) ON DELETE RESTRICT'),
        ('open_set_purpose', "TEXT CHECK(open_set_purpose IS NULL OR open_set_purpose IN ('customer_tray','replenishment'))"),
    )
    for name, definition in additions:
        if name not in columns:
            cur.execute(
                f'ALTER TABLE inventory_delivery_lines ADD COLUMN {name} {definition}'
            )
    cur.execute(
        "INSERT OR IGNORE INTO _migrations(name) VALUES ('add_delivery_provenance')"
    )


def _migration_create_sale_documents(con, cur):
    """Create immutable individual sale facts and inventory allocations."""
    cur.executescript('''
        CREATE TABLE IF NOT EXISTS sale_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id INTEGER NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
            business_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','posted')),
            version INTEGER NOT NULL DEFAULT 1 CHECK(version > 0),
            entry_mode TEXT NOT NULL CHECK(entry_mode IN ('planned_entry','already_paid')),
            currency TEXT NOT NULL DEFAULT 'CAD' CHECK(currency='CAD'),
            subtotal_cents INTEGER CHECK(subtotal_cents IS NULL OR subtotal_cents >= 0),
            source_tax_cents INTEGER CHECK(source_tax_cents IS NULL OR source_tax_cents >= 0),
            gross_cents INTEGER CHECK(gross_cents IS NULL OR gross_cents >= 0),
            reduction_cents INTEGER CHECK(reduction_cents IS NULL OR reduction_cents >= 0),
            rounding_cents INTEGER,
            collected_cents INTEGER CHECK(collected_cents IS NULL OR collected_cents >= 0),
            financial_status TEXT NOT NULL DEFAULT 'draft' CHECK(financial_status IN ('draft','recorded')),
            allocation_status TEXT NOT NULL DEFAULT 'draft' CHECK(allocation_status IN ('draft','pending','allocated')),
            allocation_reason TEXT,
            inventory_document_id INTEGER REFERENCES inventory_documents(id) ON DELETE RESTRICT,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            posted_at TEXT
        );
        CREATE TABLE IF NOT EXISTS sale_lines (
            sale_id INTEGER NOT NULL REFERENCES sale_documents(id) ON DELETE RESTRICT,
            line_no INTEGER NOT NULL CHECK(line_no > 0),
            product_id INTEGER REFERENCES products(id) ON DELETE RESTRICT,
            raw_product_text TEXT,
            product_name_snapshot TEXT,
            stock_form_snapshot TEXT,
            native_unit TEXT CHECK(native_unit IS NULL OR native_unit IN ('box','set','piece')),
            quantity INTEGER NOT NULL CHECK(quantity > 0),
            unit_price_cents INTEGER CHECK(unit_price_cents IS NULL OR unit_price_cents >= 0),
            source_tax_cents INTEGER CHECK(source_tax_cents IS NULL OR source_tax_cents >= 0),
            location_id INTEGER REFERENCES inventory_locations(id) ON DELETE RESTRICT,
            captured_balance_version INTEGER CHECK(captured_balance_version IS NULL OR captured_balance_version >= 0),
            open_set_id INTEGER REFERENCES inventory_open_sets(id) ON DELETE RESTRICT,
            PRIMARY KEY(sale_id, line_no),
            CHECK(product_id IS NOT NULL OR raw_product_text IS NOT NULL)
        );
        CREATE TABLE IF NOT EXISTS sale_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sale_id INTEGER NOT NULL REFERENCES sale_documents(id) ON DELETE RESTRICT,
            source_system TEXT NOT NULL,
            source_account TEXT NOT NULL,
            source_reference TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(source_system, source_account, source_reference)
        );
        CREATE TABLE IF NOT EXISTS sale_allocations (
            sale_id INTEGER NOT NULL REFERENCES sale_documents(id) ON DELETE RESTRICT,
            line_no INTEGER NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending','allocated')),
            reason TEXT,
            inventory_document_id INTEGER REFERENCES inventory_documents(id) ON DELETE RESTRICT,
            PRIMARY KEY(sale_id, line_no),
            FOREIGN KEY(sale_id, line_no) REFERENCES sale_lines(sale_id, line_no) ON DELETE RESTRICT
        );
        CREATE INDEX IF NOT EXISTS idx_sale_store_date
        ON sale_documents(store_id, business_date, status);
    ''')
    cur.execute(
        "INSERT OR IGNORE INTO _migrations(name) VALUES ('create_sale_documents')"
    )


def _migration_add_sale_fresh_set_selection(con, cur):
    """Retain an explicit sealed-set choice for an atomic sale opening."""
    columns = {row['name'] for row in cur.execute('PRAGMA table_info(sale_lines)')}
    additions = (
        ('fresh_set_product_id', 'INTEGER REFERENCES products(id) ON DELETE RESTRICT'),
        ('fresh_set_conversion_id', 'INTEGER REFERENCES product_conversions(id) ON DELETE RESTRICT'),
        ('fresh_set_conversion_factor', 'INTEGER CHECK(fresh_set_conversion_factor IS NULL OR fresh_set_conversion_factor > 0)'),
        ('fresh_set_balance_version', 'INTEGER CHECK(fresh_set_balance_version IS NULL OR fresh_set_balance_version >= 0)'),
    )
    for name, definition in additions:
        if name not in columns:
            cur.execute(f'ALTER TABLE sale_lines ADD COLUMN {name} {definition}')
    cur.execute(
        "INSERT OR IGNORE INTO _migrations(name) VALUES ('add_sale_fresh_set_selection')"
    )


def _migration_create_sale_payments(con, cur):
    """Create tender, correction, return, and source-review facts."""
    cur.executescript('''
        CREATE TABLE IF NOT EXISTS sale_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sale_id INTEGER NOT NULL REFERENCES sale_documents(id) ON DELETE RESTRICT,
            tender TEXT NOT NULL CHECK(tender IN
                ('cash','card','e_transfer','wechat','alipay')),
            amount_cents INTEGER CHECK(amount_cents IS NULL OR amount_cents >= 0),
            source_system TEXT,
            source_account TEXT,
            source_reference TEXT,
            state TEXT NOT NULL DEFAULT 'recorded'
                CHECK(state IN ('recorded','verified','rejected')),
            recorded_by TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(source_system, source_account, source_reference)
        );
        CREATE INDEX IF NOT EXISTS idx_sale_payments_sale
        ON sale_payments(sale_id, id);
        CREATE TABLE IF NOT EXISTS payment_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payment_id INTEGER NOT NULL REFERENCES sale_payments(id) ON DELETE RESTRICT,
            event_type TEXT NOT NULL CHECK(event_type IN
                ('verify','reject','correction','refund')),
            direction TEXT NOT NULL CHECK(direction IN ('none','increase','decrease')),
            amount_cents INTEGER CHECK(amount_cents IS NULL OR amount_cents >= 0),
            reason TEXT NOT NULL,
            actor_sub TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS sale_reconciliations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id INTEGER NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
            business_date TEXT NOT NULL,
            intent TEXT NOT NULL CHECK(intent IN
                ('missing_transactions','summary_only','stock_already_posted')),
            source_system TEXT NOT NULL,
            source_account TEXT NOT NULL,
            source_reference TEXT NOT NULL,
            sale_id INTEGER REFERENCES sale_documents(id) ON DELETE RESTRICT,
            inventory_document_id INTEGER REFERENCES inventory_documents(id) ON DELETE RESTRICT,
            notes TEXT NOT NULL,
            reviewed_by TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(source_system, source_account, source_reference)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_sale_reconciliation_inventory
        ON sale_reconciliations(inventory_document_id)
        WHERE inventory_document_id IS NOT NULL;
        CREATE TABLE IF NOT EXISTS sale_returns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sale_id INTEGER NOT NULL REFERENCES sale_documents(id) ON DELETE RESTRICT,
            sale_line_no INTEGER NOT NULL,
            quantity INTEGER NOT NULL CHECK(quantity > 0),
            disposition TEXT NOT NULL CHECK(disposition IN ('saleable','damaged','hold')),
            reason TEXT NOT NULL,
            inventory_document_id INTEGER REFERENCES inventory_documents(id) ON DELETE RESTRICT,
            reviewed_by TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY(sale_id, sale_line_no)
                REFERENCES sale_lines(sale_id, line_no) ON DELETE RESTRICT
        );
    ''')
    cur.execute(
        "INSERT OR IGNORE INTO _migrations(name) VALUES ('create_sale_payments')"
    )


def _migration_create_payment_evidence(con, cur):
    """Create private immutable payment attachments and review history."""
    cur.executescript('''
        CREATE TABLE IF NOT EXISTS payment_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payment_id INTEGER NOT NULL REFERENCES sale_payments(id) ON DELETE RESTRICT,
            object_id TEXT NOT NULL UNIQUE,
            mime_type TEXT NOT NULL CHECK(mime_type IN
                ('image/jpeg','image/png','image/webp')),
            byte_size INTEGER NOT NULL CHECK(byte_size > 0),
            uploader_sub TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending','accepted','rejected')),
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_payment_evidence_payment
        ON payment_evidence(payment_id, id);
        CREATE TABLE IF NOT EXISTS payment_evidence_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            evidence_id INTEGER NOT NULL REFERENCES payment_evidence(id) ON DELETE RESTRICT,
            decision TEXT NOT NULL CHECK(decision IN ('accepted','rejected')),
            reason TEXT NOT NULL,
            reviewer_sub TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    ''')
    cur.execute(
        "INSERT OR IGNORE INTO _migrations(name) VALUES ('create_payment_evidence')"
    )


def _migration_create_closing(con, cur):
    """Create one versioned closing session and immutable cash facts per store-day."""
    cur.executescript('''
        CREATE TABLE IF NOT EXISTS closing_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id INTEGER NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
            business_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft'
                CHECK(status IN ('draft','submitted','closed')),
            version INTEGER NOT NULL DEFAULT 1 CHECK(version > 0),
            intake_complete INTEGER NOT NULL DEFAULT 0 CHECK(intake_complete IN (0,1)),
            source_token TEXT,
            created_by TEXT NOT NULL,
            submitted_by TEXT,
            submitted_at TEXT,
            reviewed_by TEXT,
            closed_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(store_id, business_date)
        );
        CREATE TABLE IF NOT EXISTS cash_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id INTEGER NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
            business_date TEXT NOT NULL,
            closing_session_id INTEGER REFERENCES closing_sessions(id) ON DELETE RESTRICT,
            event_type TEXT NOT NULL CHECK(event_type IN
                ('paid_in','refund','payout','removal')),
            amount_cents INTEGER NOT NULL CHECK(amount_cents > 0),
            reason TEXT NOT NULL,
            actor_sub TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_cash_event_store_date
        ON cash_events(store_id, business_date, id);
        CREATE TABLE IF NOT EXISTS closing_cash_counts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            closing_session_id INTEGER NOT NULL REFERENCES closing_sessions(id) ON DELETE RESTRICT,
            revision INTEGER NOT NULL CHECK(revision > 0),
            denomination_counts_json TEXT NOT NULL,
            opening_coin_cents INTEGER NOT NULL CHECK(opening_coin_cents >= 0),
            retained_coin_cents INTEGER NOT NULL CHECK(retained_coin_cents >= 0),
            counted_cents INTEGER NOT NULL CHECK(counted_cents >= 0),
            expected_cents INTEGER NOT NULL,
            variance_cents INTEGER NOT NULL,
            retained_cents INTEGER NOT NULL,
            removal_cents INTEGER,
            source_token TEXT NOT NULL,
            counted_by TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(closing_session_id, revision)
        );
        CREATE TABLE IF NOT EXISTS closing_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            closing_session_id INTEGER NOT NULL UNIQUE
                REFERENCES closing_sessions(id) ON DELETE RESTRICT,
            source_token TEXT NOT NULL,
            snapshot_json TEXT NOT NULL,
            cash_removal_event_id INTEGER UNIQUE REFERENCES cash_events(id) ON DELETE RESTRICT,
            reviewed_by TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS closing_adjustments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            closing_session_id INTEGER NOT NULL REFERENCES closing_sessions(id) ON DELETE RESTRICT,
            source_type TEXT NOT NULL,
            source_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            actor_sub TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(closing_session_id, source_type, source_id)
        );
    ''')
    cur.execute("INSERT OR IGNORE INTO _migrations(name) VALUES ('create_closing')")


def _migration_harden_build4_financial_facts(con, cur):
    """Enforce Build 4 financial bounds, count freshness, and exclusive links."""
    columns = {
        row['name'] for row in cur.execute('PRAGMA table_info(closing_cash_counts)')
    }
    if 'source_token' not in columns:
        cur.execute('ALTER TABLE closing_cash_counts ADD COLUMN source_token TEXT')
    cur.executescript('''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_sale_reconciliation_inventory
        ON sale_reconciliations(inventory_document_id)
        WHERE inventory_document_id IS NOT NULL;

        CREATE TRIGGER IF NOT EXISTS sale_documents_nonnegative_insert
        BEFORE INSERT ON sale_documents
        WHEN NEW.subtotal_cents < 0 OR NEW.source_tax_cents < 0
          OR NEW.gross_cents < 0 OR NEW.collected_cents < 0
        BEGIN
            SELECT RAISE(ABORT, 'sale amounts must be nonnegative');
        END;
        CREATE TRIGGER IF NOT EXISTS sale_documents_nonnegative_update
        BEFORE UPDATE ON sale_documents
        WHEN NEW.subtotal_cents < 0 OR NEW.source_tax_cents < 0
          OR NEW.gross_cents < 0 OR NEW.collected_cents < 0
        BEGIN
            SELECT RAISE(ABORT, 'sale amounts must be nonnegative');
        END;
        CREATE TRIGGER IF NOT EXISTS sale_lines_nonnegative_insert
        BEFORE INSERT ON sale_lines
        WHEN NEW.unit_price_cents < 0 OR NEW.source_tax_cents < 0
        BEGIN
            SELECT RAISE(ABORT, 'sale line amounts must be nonnegative');
        END;
        CREATE TRIGGER IF NOT EXISTS sale_lines_nonnegative_update
        BEFORE UPDATE ON sale_lines
        WHEN NEW.unit_price_cents < 0 OR NEW.source_tax_cents < 0
        BEGIN
            SELECT RAISE(ABORT, 'sale line amounts must be nonnegative');
        END;
    ''')
    cur.execute(
        "INSERT OR IGNORE INTO _migrations(name) VALUES ('harden_build4_financial_facts')"
    )


def _migration_create_trades(con, cur):
    """Add physical trade identity, slot history, and condition cases."""
    sale_line_columns = {
        row['name'] for row in cur.execute('PRAGMA table_info(sale_lines)')
    }
    if 'condition_disclosure' not in sale_line_columns:
        cur.execute('ALTER TABLE sale_lines ADD COLUMN condition_disclosure TEXT')
    cur.executescript('''
        CREATE TABLE IF NOT EXISTS trade_units (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            design_product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
            series_id INTEGER NOT NULL REFERENCES product_series(id) ON DELETE RESTRICT,
            origin TEXT NOT NULL CHECK (origin IN ('random_opening','inspected_trade','confirmed_purchase')),
            eligibility TEXT NOT NULL CHECK (eligibility IN ('eligible','ineligible')),
            location_id INTEGER REFERENCES inventory_locations(id) ON DELETE RESTRICT,
            custody TEXT NOT NULL CHECK (custody IN ('slot','customer','sold','review')),
            condition_disclosure TEXT NOT NULL,
            source_document_id INTEGER REFERENCES inventory_documents(id) ON DELETE RESTRICT,
            version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS trade_slots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id INTEGER NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
            series_id INTEGER NOT NULL REFERENCES product_series(id) ON DELETE RESTRICT,
            location_id INTEGER NOT NULL REFERENCES inventory_locations(id) ON DELETE RESTRICT,
            occupant_unit_id INTEGER REFERENCES trade_units(id) ON DELETE RESTRICT,
            version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
            is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(store_id, series_id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_trade_slots_occupant
        ON trade_slots(occupant_unit_id) WHERE occupant_unit_id IS NOT NULL;

        CREATE TABLE IF NOT EXISTS trade_inspections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slot_id INTEGER NOT NULL REFERENCES trade_slots(id) ON DELETE RESTRICT,
            incoming_unit_id INTEGER NOT NULL REFERENCES trade_units(id) ON DELETE RESTRICT,
            outgoing_unit_id INTEGER REFERENCES trade_units(id) ON DELETE RESTRICT,
            expected_slot_version INTEGER NOT NULL,
            proof_kind TEXT NOT NULL,
            proof_reference TEXT NOT NULL,
            check_notes TEXT,
            box_checked INTEGER NOT NULL CHECK (box_checked IN (0,1)),
            accessories_checked INTEGER NOT NULL CHECK (accessories_checked IN (0,1)),
            condition_observed TEXT NOT NULL,
            disclosure TEXT NOT NULL,
            decision TEXT NOT NULL CHECK (decision IN ('accepted','rejected')),
            actor_sub TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS trade_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slot_id INTEGER NOT NULL REFERENCES trade_slots(id) ON DELETE RESTRICT,
            event_type TEXT NOT NULL CHECK (event_type IN ('open','swap','sale')),
            slot_version_before INTEGER NOT NULL,
            slot_version_after INTEGER NOT NULL,
            incoming_unit_id INTEGER REFERENCES trade_units(id) ON DELETE RESTRICT,
            outgoing_unit_id INTEGER REFERENCES trade_units(id) ON DELETE RESTRICT,
            inspection_id INTEGER REFERENCES trade_inspections(id) ON DELETE RESTRICT,
            consume_document_id INTEGER REFERENCES inventory_documents(id) ON DELETE RESTRICT,
            receipt_document_id INTEGER REFERENCES inventory_documents(id) ON DELETE RESTRICT,
            sale_id INTEGER REFERENCES sale_documents(id) ON DELETE RESTRICT,
            actor_sub TEXT NOT NULL,
            reason TEXT,
            business_date TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS condition_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id INTEGER NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
            unit_id INTEGER REFERENCES trade_units(id) ON DELETE RESTRICT,
            sale_id INTEGER,
            sale_line_no INTEGER,
            observed_condition TEXT NOT NULL,
            disclosed_condition TEXT,
            status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved')),
            version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            CHECK (unit_id IS NOT NULL OR (sale_id IS NOT NULL AND sale_line_no IS NOT NULL)),
            FOREIGN KEY(sale_id, sale_line_no)
              REFERENCES sale_lines(sale_id, line_no) ON DELETE RESTRICT
        );
        CREATE TABLE IF NOT EXISTS condition_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id INTEGER NOT NULL REFERENCES condition_cases(id) ON DELETE RESTRICT,
            event_type TEXT NOT NULL CHECK (event_type IN ('note','decision')),
            disposition TEXT,
            reason TEXT NOT NULL,
            actor_sub TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS condition_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id INTEGER NOT NULL REFERENCES condition_cases(id) ON DELETE RESTRICT,
            object_id TEXT NOT NULL UNIQUE,
            mime_type TEXT NOT NULL,
            byte_size INTEGER NOT NULL CHECK (byte_size > 0),
            content_hash TEXT NOT NULL,
            uploaded_by TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    ''')
    cur.execute("INSERT OR IGNORE INTO _migrations(name) VALUES ('create_trades')")

def _migration_create_insight_runs(con, cur):
    cur.execute('''CREATE TABLE IF NOT EXISTS insight_runs (
        job TEXT NOT NULL, business_date TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('running','succeeded','failed')),
        started_at TEXT NOT NULL, finished_at TEXT, error TEXT,
        PRIMARY KEY(job,business_date)
    )''')
    cur.execute("INSERT OR IGNORE INTO _migrations(name) VALUES ('create_insight_runs')")


def _migration_availability_period_submissions(con, cur):
    # Rebuild only availability to remove its old employee/date uniqueness.
    con.commit()
    cur.execute('BEGIN IMMEDIATE')
    sequence = cur.execute("SELECT seq FROM sqlite_sequence WHERE name='availability'").fetchone()
    cur.execute("""CREATE TABLE availability_period_new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
        date TEXT NOT NULL, start_time TEXT NOT NULL, end_time TEXT NOT NULL,
        notes TEXT DEFAULT '', created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now')),
        store_id INTEGER NOT NULL DEFAULT 1 REFERENCES stores(id),
        status TEXT NOT NULL DEFAULT 'available' CHECK(status IN ('available','unavailable')),
        UNIQUE(employee_id, date, store_id)
    )""")
    cur.execute("""INSERT INTO availability_period_new
        (id,employee_id,date,start_time,end_time,notes,created_at,updated_at,store_id)
        SELECT id,employee_id,date,start_time,end_time,notes,created_at,updated_at,store_id
        FROM availability""")
    cur.execute('DROP TABLE availability')
    cur.execute('ALTER TABLE availability_period_new RENAME TO availability')
    if sequence:
        cur.execute("UPDATE sqlite_sequence SET seq=MAX(seq,?) WHERE name='availability'", (sequence['seq'],))
    cur.execute('CREATE INDEX idx_availability_date ON availability(date)')
    cur.execute("""CREATE TABLE IF NOT EXISTS availability_submissions (
        employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
        store_id INTEGER NOT NULL REFERENCES stores(id), period_start TEXT NOT NULL,
        version INTEGER NOT NULL, submitted_at TEXT,
        PRIMARY KEY(employee_id,store_id,period_start)
    )""")
    cur.execute("INSERT INTO _migrations(name) VALUES ('availability_period_submissions')")


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
        ('add_ical_token_to_employees',          _migration_add_ical_token_to_employees),
        ('add_position_to_shifts',               _migration_add_position_to_shifts),
        ('add_is_trainee_to_employees',          _migration_add_is_trainee_to_employees),
        ('add_is_schedulable_to_employees',      _migration_add_is_schedulable_to_employees),
        ('create_schedule_notes_table',          _migration_create_schedule_notes_table),
        ('create_schedule_checklist_table',      _migration_create_schedule_checklist_table),
        ('assign_curated_employee_colors',       _migration_assign_curated_employee_colors),
        ('create_catalog_identity',               _migration_create_catalog_identity),
        ('create_inventory_locations_and_access', _migration_create_inventory_locations_and_access),
        ('create_inventory_posting_core',          _migration_create_inventory_posting_core),
        ('create_open_set_provenance',             _migration_create_open_set_provenance),
        ('link_inventory_compatibility',            _migration_link_inventory_compatibility),
        ('create_goods_workflows',                   _migration_create_goods_workflows),
        ('add_delivery_provenance',                  _migration_add_delivery_provenance),
        ('create_sale_documents',                    _migration_create_sale_documents),
        ('add_sale_fresh_set_selection',             _migration_add_sale_fresh_set_selection),
        ('create_sale_payments',                      _migration_create_sale_payments),
        ('create_payment_evidence',                    _migration_create_payment_evidence),
        ('create_closing',                             _migration_create_closing),
        ('harden_build4_financial_facts',              _migration_harden_build4_financial_facts),
        ('create_trades',                               _migration_create_trades),
        ('create_insight_runs',                          _migration_create_insight_runs),
        ('availability_period_submissions',             _migration_availability_period_submissions),
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
