"""popcore_app/matcher.py
──────────────────────────────────────────────────────────────────────────────
Central product matching engine for POPCORE.

Used by:
  • app.py  /api/products/by_jizhanming  (legacy single-query lookup)
  • app.py  /api/products/match          (new batch endpoint)
  • scraper.py  run_scrape()             (market price title matching)

Two matching modes
──────────────────
match_jzm(query, products, aliases, threshold=75)
  For paste-import (sales / stock batch).
  Matches a user's informal 记账名 against product.jizhanming.
  • Strips ALL whitespace  ("SA 草莓" == "SA草莓")
  • Length penalty: short queries ("SA") don't over-match long candidates
  • CJK coverage: "SA草莓" won't match "SA宇航员" (no shared Chinese chars)
  • Alias exact lookup → score 100 always

batch_match_jzm(queries, products, aliases, threshold=75)
  Same as match_jzm but for multiple queries at once (loads products once).
  Also filters header lines ("卡机汇总:", "现金:") with is_header_line().

match_title(scraped_title, products, threshold=65)
  For web-scraper market price matching.
  Matches English store titles against name_cn_en / jizhanming.
  Uses space-preserving normalisation (better for English token matching).
"""

import re
import unicodedata
from collections import Counter


# ─── Noise cleaning ───────────────────────────────────────────────────────────

_TRAILING_PUNCT = re.compile(r'[*＊:：、。！!～~]+$')

# Whitelist of section header keywords (substring match, order matters: longer first)
_SECTION_KEYWORDS = [
    '卡机汇总', '随手记汇总', '随手记',
    '入店', '出店', '娃娃机',
    '卖display', '拆display', '员工折扣', '晚盘', '博主探店', '现金',
]


def clean_name(raw: str) -> str:
    """Strip trailing noise chars (* : ： etc.) from a pasted product name."""
    return _TRAILING_PUNCT.sub('', (raw or '').strip()).strip()


def is_header_line(raw: str) -> bool:
    """
    Return True if the line is a known section header, total row, or empty.
    Uses substring containment against the whitelist — no colon required.
    Examples that return True: "卡机汇总", "随手记汇总:", "入店", "现金汇总"
    """
    s = clean_name(raw)
    if not s:
        return True
    if s.endswith((':', '：')):
        return True
    low = raw.lower()
    return any(kw.lower() in low for kw in _SECTION_KEYWORDS)


# ─── Normalisation ────────────────────────────────────────────────────────────

def normalize(s: str) -> str:
    """
    Normalize a 记账名 for jzm matching.
    Strips ALL whitespace — "SA 草莓" and "SA草莓" become identical.
    NFKC converts fullwidth chars (ＳＡ → SA).
    """
    s = (s or '').strip()
    s = unicodedata.normalize('NFKC', s)
    s = s.lower()
    s = re.sub(r'[\s\u3000]+', '', s)   # remove ALL whitespace
    if s.endswith('s') and len(s) > 1:
        s = s[:-1]
    return s


def normalize_spaced(s: str) -> str:
    """
    Normalize a product title for scraper matching.
    Preserves single spaces — better for English token comparison.
    """
    s = (s or '').strip()
    s = unicodedata.normalize('NFKC', s)
    s = s.lower()
    s = re.sub(r'[\s\u3000]+', ' ', s).strip()
    if s.endswith('s') and len(s) > 2:
        s = s[:-1]
    return s


# ─── Core similarity ──────────────────────────────────────────────────────────

def _bigram_jaccard(a: str, b: str) -> int:
    """Bigram-Jaccard similarity (0-100), used as rapidfuzz fallback."""
    if len(a) == 1 or len(b) == 1:
        return 80 if (a in b or b in a) else 0
    bg_a = {a[i:i+2] for i in range(len(a) - 1)}
    bg_b = {b[i:i+2] for i in range(len(b) - 1)}
    union = len(bg_a | bg_b)
    return int(100 * len(bg_a & bg_b) / union) if union else 0


def _raw_sim(a: str, b: str) -> int:
    """rapidfuzz WRatio (0-100), falling back to bigram-Jaccard."""
    if not a or not b:
        return 0
    if a == b:
        return 100
    try:
        from rapidfuzz import fuzz
        return int(fuzz.WRatio(a, b))
    except ImportError:
        return _bigram_jaccard(a, b)


def _score_pair_jzm(qn: str, cn: str) -> int:
    """
    Similarity between two normalize()-d (no-spaces) strings, returns 0-100.

    Key behaviours vs the old WRatio-only approach:
    · Length penalty: short queries don't over-match long candidates.
        "sa" (2) vs "sa草莓" (4) → lr=0.5, heavy penalty → well below threshold
        "sa草莓" (4) vs "sa草莓" (4) → exact → 100
    · CJK coverage: query's Chinese chars must appear in candidate.
        "sa草莓" vs "sa宇航员" → coverage=0 → score=0
        "dimoo花花" vs "dimoo花园" → coverage=0.5 → heavy penalty (花花 needs two 花)
    """
    if not qn or not cn:
        return 0
    if qn == cn:
        return 100

    shorter = min(len(qn), len(cn))
    longer  = max(len(qn), len(cn))
    lr      = shorter / longer   # 1.0 = equal length, < 1 = different

    # Substring hit
    if qn in cn or cn in qn:
        base = 92 if shorter >= 4 else 68
        s    = int(base * (0.5 + 0.5 * lr))
    else:
        raw = _raw_sim(qn, cn)
        # Length penalty — aggressive for very short queries vs much longer candidates
        if len(qn) < 4 and lr < 0.6:
            s = int(raw * lr)
        else:
            s = int(raw * (0.65 + 0.35 * lr))

    # CJK coverage penalty (multiset-aware: "花花" requires two 花 in candidate)
    q_cjk = [ch for ch in qn if '\u4e00' <= ch <= '\u9fff']
    if q_cjk:
        q_cnt = Counter(q_cjk)
        c_cnt = Counter(ch for ch in cn if '\u4e00' <= ch <= '\u9fff')
        matched = sum(min(n, c_cnt.get(ch, 0)) for ch, n in q_cnt.items())
        coverage = matched / len(q_cjk)
        if coverage < 1.0:
            s = int(s * coverage)

    return min(s, 99)   # 100 is reserved for exact match only


def _score_product_jzm(qn: str, product: dict) -> int:
    """Best score of qn against a product's jizhanming, name_cn_en, and sku.
    Kept for scraper/legacy use. Prefer the waterfall in match_jzm for batch import."""
    jzm_n  = normalize(product.get('jizhanming') or '')
    name_n = normalize(product.get('name_cn_en') or '')
    sku    = (product.get('sku') or '').lower().replace('-', '')

    # Exact SKU reference in query (e.g. user typed "sp00123")
    if sku and sku in qn:
        return 95

    s_jzm  = _score_pair_jzm(qn, jzm_n)             if jzm_n  else 0
    s_name = int(_score_pair_jzm(qn, name_n) * 0.9) if name_n else 0
    return max(s_jzm, s_name)


# ─── Public API: jzm (batch import) ──────────────────────────────────────────

def match_jzm(
    query: str,
    products: list,
    aliases: dict | None = None,
    threshold: int = 75,
    limit: int = 5,
) -> list:
    """
    Match a raw 记账名 string against a product list using a strict waterfall.

    Stages (stops at the first stage that yields results):
      1. Jizhanming — score query against product.jizhanming
      2. Name       — score query against product.name_cn_en
      3. Alias      — exact lookup in the alias table (score 100)
      4. Not found  — return []

    This prevents cross-stage false matches (e.g. "哭娃度假" matching
    "哭娃度假吸管杯") because each stage only returns results when the
    similarity is clearly above the threshold.

    Args:
        query:     Raw input (may contain *, spaces, colons, etc.)
        products:  List of product dicts (id, jizhanming, name_cn_en, sku, …)
        aliases:   {alias_norm → product_id} for exact alias lookup (stage 3).
        threshold: Minimum score to include (default 75).
        limit:     Max results returned per stage, sorted by score desc.

    Returns:
        List of (score: int, product: dict) sorted by score descending.
        Empty list if query is empty or no candidates meet threshold.
    """
    if aliases is None:
        aliases = {}

    cleaned = clean_name(query)
    if not cleaned:
        return []

    qn = normalize(cleaned)
    if not qn:
        return []

    # Stage 1 — Jizhanming match
    jzm_hits = []
    for p in products:
        jzm_n = normalize(p.get('jizhanming') or '')
        if not jzm_n:
            continue
        # SKU exact hit: treat as jizhanming-level match
        sku = (p.get('sku') or '').lower().replace('-', '')
        if sku and sku in qn:
            jzm_hits.append((95, p))
            continue
        s = _score_pair_jzm(qn, jzm_n)
        if s >= threshold:
            jzm_hits.append((s, p))
    if jzm_hits:
        jzm_hits.sort(key=lambda x: -x[0])
        return jzm_hits[:limit]

    # Stage 2 — Name (name_cn_en) match
    name_hits = []
    for p in products:
        name_n = normalize(p.get('name_cn_en') or '')
        if not name_n:
            continue
        s = _score_pair_jzm(qn, name_n)
        if s >= threshold:
            name_hits.append((s, p))
    if name_hits:
        name_hits.sort(key=lambda x: -x[0])
        return name_hits[:limit]

    # Stage 3 — Alias exact match
    if qn in aliases:
        pid = aliases[qn]
        for p in products:
            if p['id'] == pid:
                return [(100, p)]

    # Stage 4 — Not found
    return []


def batch_match_jzm(
    queries: list,
    products: list,
    aliases: dict | None = None,
    threshold: int = 75,
) -> list:
    """
    Match multiple raw name strings against products (loads products only once).

    Returns list of dicts:
        {
          query: str,
          status: 'matched' | 'fuzzy' | 'unmatched' | 'skipped',
          candidates: [product_dict, ...]   # ordered by score
        }

    status meanings:
        matched   — exactly one clear best match
        fuzzy     — multiple candidates, user should pick
        unmatched — no candidates met threshold
        skipped   — line is a header/summary row, not a product
    """
    if aliases is None:
        aliases = {}

    results = []
    for raw in queries:
        if is_header_line(raw):
            results.append({'query': raw, 'status': 'skipped', 'candidates': []})
            continue

        hits = match_jzm(raw, products, aliases, threshold)

        if not hits:
            results.append({'query': raw, 'status': 'unmatched', 'candidates': []})
        elif len(hits) == 1:
            results.append({'query': raw, 'status': 'matched', 'candidates': [hits[0][1]]})
        else:
            top_score = hits[0][0]
            runner_up = hits[1][0]
            # Clearly dominant result → treat as matched
            if top_score == 100 or (top_score >= 90 and top_score - runner_up >= 15):
                results.append({'query': raw, 'status': 'matched', 'candidates': [hits[0][1]]})
            else:
                results.append({'query': raw, 'status': 'fuzzy', 'candidates': [p for _, p in hits]})

    return results


# ─── Public API: title (scraper) ─────────────────────────────────────────────

def _english_part(text: str) -> str:
    """Extract ASCII/Latin portion from a mixed CN-EN string."""
    parts = re.findall(
        r'[^\u4e00-\u9fff\u3040-\u30ff\uff00-\uffef\u3000-\u303f]+',
        text or '',
    )
    return ' '.join(p.strip() for p in parts if p.strip())


def match_title(scraped_title: str, products: list, threshold: int = 65) -> tuple:
    """
    Match a scraped store title against products.

    Returns (product_id, sku, score) or (None, None, best_score).

    Checks (highest priority first):
      1. English portion of name_cn_en  — best for English-language stores
      2. Full name_cn_en
      3. jizhanming
    """
    qt = normalize_spaced(scraped_title)
    if not qt:
        return None, None, 0

    best_score, best_pid, best_sku = 0, None, None
    for p in products:
        eng  = normalize_spaced(_english_part(p.get('name_cn_en') or ''))
        full = normalize_spaced(p.get('name_cn_en') or '')
        jzm  = normalize_spaced(p.get('jizhanming')  or '')

        s = max(
            _raw_sim(qt, eng)  if eng  else 0,
            _raw_sim(qt, full) if full else 0,
            _raw_sim(qt, jzm)  if jzm  else 0,
        )
        if s > best_score:
            best_score = s
            best_pid   = p['id']
            best_sku   = p['sku']

    if best_score >= threshold:
        return best_pid, best_sku, best_score
    return None, None, best_score
