"""ranker.py — Lightweight ML re-ranker for product match corrections.

Trains a LogisticRegression model on the match_corrections table.
Only activates when ≥30 corrections exist; returns candidates unchanged otherwise.
Model is cached per process and invalidated when new corrections are recorded.
"""
import os
import sqlite3
from collections import Counter
from datetime import datetime

_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'popcore.db')

# Cached state: None means untrained/invalidated.
# When trained: (model, aliases_dict, corrections_meta_dict)
_cache = None


def invalidate_cache() -> None:
    global _cache
    _cache = None


def rerank(
    candidates: list,
    norm_name: str,
    store: str,
) -> list:
    """Re-rank (score, product) pairs; returns them in the same format.

    When fewer than 30 corrections exist the list is returned unchanged.
    If sklearn is unavailable, returns unchanged.
    """
    if not candidates:
        return candidates

    state = _get_state()
    if state is None:
        return candidates

    model, aliases, corrections_meta = state

    try:
        import numpy as np
        feats = np.array([
            _features(norm_name, score, product, aliases, corrections_meta)
            for score, product in candidates
        ])
        probs = model.predict_proba(feats)[:, 1]
        order = sorted(range(len(probs)), key=lambda i: -probs[i])
        return [candidates[i] for i in order]
    except Exception:
        return candidates


# ─── Internal ─────────────────────────────────────────────────────────────────

def _get_state():
    global _cache
    if _cache is not None:
        return _cache
    _cache = _train()
    return _cache


def _features(
    norm_name: str,
    fuzzy_score: int,
    product: dict,
    aliases: dict,
    corrections_meta: dict,
) -> list:
    from matcher import normalize
    pid = product['id']
    jzm = normalize(product.get('jizhanming') or '')

    alias_match = 1 if aliases.get(norm_name) == pid else 0

    qlen = max(len(norm_name), 1)
    clen = max(len(jzm), 1)
    len_ratio = min(qlen, clen) / max(qlen, clen)

    q_cjk = Counter(c for c in norm_name if '一' <= c <= '鿿')
    c_cjk = Counter(c for c in jzm      if '一' <= c <= '鿿')
    if q_cjk or c_cjk:
        all_chars = set(q_cjk) | set(c_cjk)
        common = sum(min(q_cjk[c], c_cjk[c]) for c in all_chars)
        total  = sum((q_cjk | c_cjk).values())
        char_overlap = common / total if total else 0.0
    else:
        char_overlap = 0.0

    key = (norm_name, pid)
    meta = corrections_meta.get(key, {})
    correction_count    = meta.get('count', 0)
    last_corrected_days = meta.get('last_days', 999)

    return [fuzzy_score, alias_match, len_ratio, char_overlap,
            correction_count, last_corrected_days]


def _train():
    try:
        from sklearn.linear_model import LogisticRegression
        import numpy as np
    except ImportError:
        return None

    try:
        con = sqlite3.connect(_DB_PATH)
        con.row_factory = sqlite3.Row
        cur = con.cursor()

        cur.execute('SELECT COUNT(*) FROM match_corrections')
        if cur.fetchone()[0] < 30:
            con.close()
            return None

        cur.execute('''
            SELECT raw_name, norm_name, product_id, fuzzy_score, created_at
            FROM match_corrections
        ''')
        corrections = [dict(r) for r in cur.fetchall()]

        cur.execute('SELECT alias_norm, product_id FROM product_aliases')
        aliases = {r['alias_norm']: r['product_id'] for r in cur.fetchall()}

        cur.execute('''
            SELECT id, jizhanming, name_cn_en, sku
            FROM products
            WHERE jizhanming IS NOT NULL AND jizhanming != ''
        ''')
        products = [dict(r) for r in cur.fetchall()]
        pid_to_product = {p['id']: p for p in products}
        con.close()
    except Exception:
        return None

    # Build corrections_meta: (norm_name, product_id) → {count, last_days}
    today = datetime.now().date()
    corrections_meta: dict = {}
    for row in corrections:
        key = (row['norm_name'], row['product_id'])
        if key not in corrections_meta:
            corrections_meta[key] = {'count': 0, 'last_days': 999}
        corrections_meta[key]['count'] += 1
        try:
            d = datetime.fromisoformat(row['created_at']).date()
            days = (today - d).days
            if days < corrections_meta[key]['last_days']:
                corrections_meta[key]['last_days'] = days
        except Exception:
            pass

    # Build training data
    try:
        from matcher import match_jzm
    except ImportError:
        return None

    X: list = []
    y: list = []

    for row in corrections:
        chosen_pid = row['product_id']
        raw_name   = row['raw_name']
        norm_name  = row['norm_name']

        hits = match_jzm(raw_name, products, aliases, threshold=50, limit=10)
        if not hits:
            continue

        chosen_in_hits = any(p['id'] == chosen_pid for _, p in hits)

        for score, product in hits:
            feat  = _features(norm_name, score, product, aliases, corrections_meta)
            label = 1 if product['id'] == chosen_pid else 0
            X.append(feat)
            y.append(label)

        if not chosen_in_hits and chosen_pid in pid_to_product:
            chosen_product = pid_to_product[chosen_pid]
            feat = _features(norm_name, row['fuzzy_score'] or 0, chosen_product,
                             aliases, corrections_meta)
            X.append(feat)
            y.append(1)

    if len(X) < 2 or len(set(y)) < 2:
        return None

    try:
        model = LogisticRegression(max_iter=500)
        model.fit(np.array(X), np.array(y))
        return model, aliases, corrections_meta
    except Exception:
        return None
