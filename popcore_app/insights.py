"""
insights.py — nightly rule-based insight generation.

Called by:
  - APScheduler (nightly at 02:00 server time)
  - POST /api/insights/generate  (admin manual trigger)

Thresholds are read from insight_thresholds at runtime so managers can tune
them without a code deploy.  Two checks have adaptive overrides:

  VELOCITY_SPIKE — when ≥10 rolling-ratio data points exist for a store,
                   effective thresholds are mean ± 2σ (cfg value is the floor/ceiling).

  DEAD_STOCK     — when a product has ≥5 recorded sale events, the effective
                   threshold is max(cfg, median_inter_sale_interval × 1.5).
"""
import json
import sqlite3
import statistics
from datetime import datetime, timedelta

from db import DB_PATH


def _get_db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    return con


def _get_thresholds(cur) -> dict:
    cur.execute('SELECT key, value FROM insight_thresholds')
    return {r['key']: r['value'] for r in cur.fetchall()}


def _get_app_settings(cur) -> dict:
    """Read insight-related keys from app_settings; return defaults when missing."""
    defaults = {
        'insight_generate_time':        '02:00',
        'insight_high_price_threshold': '100',
        'insight_dead_stock_days':      '14',
        'insight_stockout_days':        '7',
        'insight_velocity_ratio':       '2.0',
    }
    try:
        cur.execute("SELECT key, value FROM app_settings WHERE key LIKE 'insight_%'")
        for row in cur.fetchall():
            if row['key'] in defaults:
                defaults[row['key']] = row['value']
    except Exception:
        pass
    return defaults


def _has_min_history(cur, store: str, days: int = 7) -> bool:
    cur.execute(
        'SELECT COUNT(DISTINCT date) AS cnt FROM daily_sales WHERE store = ?',
        (store,),
    )
    return (cur.fetchone()['cnt'] or 0) >= days


def _get_active_stores(cur) -> list[str]:
    cur.execute('SELECT code FROM stores WHERE is_active = 1')
    return [r['code'] for r in cur.fetchall()]


# ─── Adaptive helpers ─────────────────────────────────────────────────────────

def _compute_velocity_thresholds(cur, store: str, cfg_spike: float, cfg_drop: float):
    """
    Compute adaptive spike/drop thresholds from the last 30 days of 7d-vs-prior-7d
    rolling ratios (store-level).  Returns (effective_spike, effective_drop).
    Falls back to configured values when fewer than 10 ratio data points exist.
    """
    today = datetime.utcnow().date()
    lookback = (today - timedelta(days=44)).isoformat()

    cur.execute('''
        SELECT date, SUM(qty_sold) AS total
        FROM daily_sales
        WHERE store = ? AND date >= ?
        GROUP BY date
        ORDER BY date DESC
    ''', (store, lookback))
    daily = {r['date']: r['total'] for r in cur.fetchall()}

    dates = sorted(daily.keys(), reverse=True)[:30]
    ratios = []
    for d_str in dates:
        d = datetime.strptime(d_str, '%Y-%m-%d').date()
        window7 = sum(daily.get((d - timedelta(days=i)).isoformat(), 0) for i in range(1, 8))
        prior7  = sum(daily.get((d - timedelta(days=7 + i)).isoformat(), 0) for i in range(1, 8))
        if prior7 > 0:
            ratios.append(window7 / prior7)

    if len(ratios) < 10:
        return cfg_spike, cfg_drop

    mean = statistics.mean(ratios)
    std  = statistics.stdev(ratios)
    return max(cfg_spike, mean + 2 * std), min(cfg_drop, max(0.0, mean - 2 * std))


def _median_inter_sale_interval(cur, product_id: int, store: str):
    """
    Return median days between consecutive sale events for this product+store.
    Returns None when fewer than 5 sale events are recorded.
    """
    cur.execute('''
        SELECT date FROM daily_sales
        WHERE product_id = ? AND store = ? AND qty_sold > 0
        ORDER BY date
    ''', (product_id, store))
    dates = [r['date'] for r in cur.fetchall()]
    if len(dates) < 5:
        return None
    intervals = [
        (datetime.strptime(dates[i], '%Y-%m-%d') - datetime.strptime(dates[i - 1], '%Y-%m-%d')).days
        for i in range(1, len(dates))
    ]
    return statistics.median(intervals)


# ─── Check functions ──────────────────────────────────────────────────────────

def _check_velocity_spike(cur, store: str, thresholds: dict) -> list:
    cfg_spike        = float(thresholds.get('velocity_spike_ratio', 2.0))
    cfg_drop         = float(thresholds.get('velocity_drop_ratio',  0.3))
    high_price_limit = float(thresholds.get('high_price_threshold', 0))
    eff_spike, eff_drop = _compute_velocity_thresholds(cur, store, cfg_spike, cfg_drop)

    today    = datetime.utcnow().date()
    today_s  = today.isoformat()
    d7_s     = (today - timedelta(days=7)).isoformat()
    d14_s    = (today - timedelta(days=14)).isoformat()

    cur.execute('''
        WITH recent AS (
            SELECT product_id, SUM(qty_sold) AS qty7
            FROM daily_sales
            WHERE store = ? AND date > ? AND date <= ?
            GROUP BY product_id
        ),
        prior AS (
            SELECT product_id, SUM(qty_sold) AS qty14
            FROM daily_sales
            WHERE store = ? AND date > ? AND date <= ?
            GROUP BY product_id
        )
        SELECT r.product_id, p.jizhanming, p.sku,
               r.qty7, COALESCE(pr.qty14, 0) AS qty14
        FROM recent r
        JOIN products p ON p.id = r.product_id
        LEFT JOIN prior pr ON pr.product_id = r.product_id
        WHERE r.qty7 > 0 AND COALESCE(pr.qty14, 0) > 0
          AND (? <= 0 OR p.price IS NULL OR p.price <= ?)
    ''', (store, d7_s, today_s, store, d14_s, d7_s, high_price_limit, high_price_limit))

    insights = []
    for row in cur.fetchall():
        ratio = row['qty7'] / row['qty14']
        name  = row['jizhanming'] or row['sku']
        if ratio >= eff_spike:
            insights.append({
                'store':      store,
                'check_type': 'VELOCITY_SPIKE',
                'severity':   'alert',
                'title':      f'{name} — sales spike',
                'body':       (f"Sold {row['qty7']} units in last 7d vs {row['qty14']} prior 7d "
                               f"(×{ratio:.1f}). Threshold: ×{eff_spike:.1f}."),
                'product_id': row['product_id'],
                'meta':       json.dumps({'ratio': ratio, 'qty7': row['qty7'],
                                          'qty14': row['qty14'], 'threshold': eff_spike}),
            })
        elif ratio <= eff_drop:
            insights.append({
                'store':      store,
                'check_type': 'VELOCITY_SPIKE',
                'severity':   'warning',
                'title':      f'{name} — sales drop',
                'body':       (f"Sold {row['qty7']} units in last 7d vs {row['qty14']} prior 7d "
                               f"(×{ratio:.1f}). Threshold: ×{eff_drop:.1f}."),
                'product_id': row['product_id'],
                'meta':       json.dumps({'ratio': ratio, 'qty7': row['qty7'],
                                          'qty14': row['qty14'], 'threshold': eff_drop}),
            })
    return insights


def _check_dead_stock(cur, store: str, thresholds: dict) -> list:
    cfg_days         = int(thresholds.get('dead_stock_days', 14))
    high_price_limit = float(thresholds.get('high_price_threshold', 0))
    today    = datetime.utcnow().date()
    cutoff_s = (today - timedelta(days=cfg_days)).isoformat()

    cur.execute('''
        SELECT s.product_id, p.jizhanming, p.sku,
               (s.upstairs_qty + s.instore_qty) AS total_stock,
               MAX(ds.date) AS last_sale_date
        FROM stock s
        JOIN stores st  ON st.id = s.store_id AND st.code = ?
        JOIN products p ON p.id = s.product_id
        LEFT JOIN daily_sales ds
            ON ds.product_id = s.product_id AND ds.store = ? AND ds.qty_sold > 0
        WHERE (s.upstairs_qty + s.instore_qty) > 0
          AND (? <= 0 OR p.price IS NULL OR p.price <= ?)
        GROUP BY s.product_id
        HAVING last_sale_date IS NULL OR last_sale_date < ?
    ''', (store, store, high_price_limit, high_price_limit, cutoff_s))

    insights = []
    for row in cur.fetchall():
        med = _median_inter_sale_interval(cur, row['product_id'], store)
        eff_days = max(cfg_days, int(med * 1.5)) if med is not None else cfg_days

        if row['last_sale_date']:
            last = datetime.strptime(row['last_sale_date'], '%Y-%m-%d').date()
            days_since = (today - last).days
        else:
            days_since = 9999

        if days_since < eff_days:
            continue

        name  = row['jizhanming'] or row['sku']
        body  = (f"{row['total_stock']} units in stock, "
                 f"no sales for {'unknown' if days_since == 9999 else days_since} days.")
        if med is not None:
            body += f" Typical interval: {med:.0f}d (threshold: {eff_days}d)."

        insights.append({
            'store':      store,
            'check_type': 'DEAD_STOCK',
            'severity':   'warning',
            'title':      f'{name} — dead stock',
            'body':       body,
            'product_id': row['product_id'],
            'meta':       json.dumps({'days_since': days_since if days_since < 9999 else None,
                                      'stock': row['total_stock'], 'threshold': eff_days}),
        })
    return insights


def _check_revenue_gap(cur, store: str, thresholds: dict) -> list:
    gap_pct  = float(thresholds.get('revenue_gap_pct', 20.0))
    today_s  = datetime.utcnow().date().isoformat()
    d7_s     = (datetime.utcnow().date() - timedelta(days=7)).isoformat()

    cur.execute('''
        SELECT COALESCE(SUM(ds.qty_sold * p.price), 0) AS revenue
        FROM daily_sales ds
        JOIN products p ON p.id = ds.product_id
        WHERE ds.store = ? AND ds.date = ?
    ''', (store, today_s))
    today_rev = cur.fetchone()['revenue'] or 0.0

    cur.execute('''
        SELECT COALESCE(SUM(ds.qty_sold * p.price), 0) / 7.0 AS avg_rev
        FROM daily_sales ds
        JOIN products p ON p.id = ds.product_id
        WHERE ds.store = ? AND ds.date > ? AND ds.date < ?
    ''', (store, d7_s, today_s))
    avg_rev = cur.fetchone()['avg_rev'] or 0.0

    if avg_rev <= 0:
        return []

    gap = (avg_rev - today_rev) / avg_rev * 100
    if gap < gap_pct:
        return []

    severity = 'alert' if gap >= gap_pct * 1.5 else 'warning'
    return [{
        'store':      store,
        'check_type': 'REVENUE_GAP',
        'severity':   severity,
        'title':      f'Revenue gap — {store}',
        'body':       (f"Today CA${today_rev:.0f} is {gap:.0f}% below "
                       f"7d avg CA${avg_rev:.0f}. Threshold: {gap_pct:.0f}%."),
        'product_id': None,
        'meta':       json.dumps({'today_rev': today_rev, 'avg_rev': avg_rev, 'gap_pct': gap}),
    }]


def _check_stockout_risk(cur, store: str, thresholds: dict) -> list:
    days_runway      = float(thresholds.get('stockout_days_runway', 7.0))
    high_price_limit = float(thresholds.get('high_price_threshold', 0))
    today_s          = datetime.utcnow().date().isoformat()
    d30_s            = (datetime.utcnow().date() - timedelta(days=30)).isoformat()

    cur.execute('''
        SELECT s.product_id, p.jizhanming, p.sku,
               (s.upstairs_qty + s.instore_qty) AS total_stock,
               COALESCE(SUM(ds.qty_sold), 0) * 1.0 / 30 AS avg_daily
        FROM stock s
        JOIN stores st  ON st.id = s.store_id AND st.code = ?
        JOIN products p ON p.id = s.product_id
        LEFT JOIN daily_sales ds
            ON ds.product_id = s.product_id AND ds.store = ?
           AND ds.date > ? AND ds.date <= ?
        WHERE (s.upstairs_qty + s.instore_qty) > 0
          AND (? <= 0 OR p.price IS NULL OR p.price <= ?)
        GROUP BY s.product_id
        HAVING avg_daily > 0
    ''', (store, store, d30_s, today_s, high_price_limit, high_price_limit))

    insights = []
    for row in cur.fetchall():
        runway   = row['total_stock'] / row['avg_daily']
        if runway >= days_runway:
            continue
        name     = row['jizhanming'] or row['sku']
        severity = 'alert' if runway < days_runway / 2 else 'warning'
        insights.append({
            'store':      store,
            'check_type': 'STOCKOUT_RISK',
            'severity':   severity,
            'title':      f'{name} — stockout risk',
            'body':       (f"{row['total_stock']} units, {row['avg_daily']:.1f}/day avg "
                           f"→ {runway:.1f}d runway. Threshold: {days_runway:.0f}d."),
            'product_id': row['product_id'],
            'meta':       json.dumps({'stock': row['total_stock'],
                                      'avg_daily': row['avg_daily'], 'runway': runway}),
        })
    return insights


def _check_data_quality(cur, store: str, thresholds: dict) -> list:
    stale_days = int(thresholds.get('data_quality_days', 14))
    cutoff_s   = (datetime.utcnow().date() - timedelta(days=stale_days)).isoformat()

    cur.execute('''
        SELECT s.product_id, p.jizhanming, p.sku,
               (s.upstairs_qty + s.instore_qty) AS total_stock,
               s.last_updated
        FROM stock s
        JOIN stores st  ON st.id = s.store_id AND st.code = ?
        JOIN products p ON p.id = s.product_id
        WHERE (s.upstairs_qty + s.instore_qty) > 0
          AND (s.last_updated IS NULL OR s.last_updated < ?)
    ''', (store, cutoff_s))

    return [
        {
            'store':      store,
            'check_type': 'DATA_QUALITY',
            'severity':   'info',
            'title':      f"{row['jizhanming'] or row['sku']} — stale stock record",
            'body':       (f"Stock ({row['total_stock']} units) last updated: "
                           f"{row['last_updated'] or 'never'}. Please verify."),
            'product_id': row['product_id'],
            'meta':       json.dumps({'last_updated': row['last_updated'],
                                      'stock': row['total_stock']}),
        }
        for row in cur.fetchall()
    ]


# ─── Orchestrator ─────────────────────────────────────────────────────────────

def generate_daily_insights() -> int:
    """
    Run all checks for every active store and write results to insights.
    Undismissed insights older than 48h are pruned first.
    Returns the number of new insights written.
    """
    con = _get_db()
    cur = con.cursor()
    try:
        cur.execute('''
            DELETE FROM insights
            WHERE dismissed_at IS NULL
              AND generated_at < datetime('now', '-48 hours')
        ''')

        thresholds  = _get_thresholds(cur)
        app_cfg     = _get_app_settings(cur)
        # App settings override insight_thresholds table values
        thresholds['velocity_spike_ratio'] = float(app_cfg.get('insight_velocity_ratio',   thresholds.get('velocity_spike_ratio', 2.0)))
        thresholds['dead_stock_days']      = float(app_cfg.get('insight_dead_stock_days',  thresholds.get('dead_stock_days', 14)))
        thresholds['stockout_days_runway'] = float(app_cfg.get('insight_stockout_days',    thresholds.get('stockout_days_runway', 7)))
        thresholds['high_price_threshold'] = float(app_cfg.get('insight_high_price_threshold', 0))
        stores     = _get_active_stores(cur)
        now        = datetime.utcnow().isoformat()
        all_ins    = []

        for store in stores:
            has_history = _has_min_history(cur, store, days=7)
            if has_history:
                all_ins.extend(_check_velocity_spike(cur, store, thresholds))
                all_ins.extend(_check_dead_stock(cur, store, thresholds))
                all_ins.extend(_check_revenue_gap(cur, store, thresholds))
            all_ins.extend(_check_stockout_risk(cur, store, thresholds))
            all_ins.extend(_check_data_quality(cur, store, thresholds))

        for ins in all_ins:
            cur.execute('''
                INSERT INTO insights
                    (store, check_type, severity, title, body, product_id, meta, generated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (ins['store'], ins['check_type'], ins['severity'],
                  ins['title'], ins['body'], ins.get('product_id'),
                  ins.get('meta', '{}'), now))

        con.commit()
        return len(all_ins)
    finally:
        con.close()
