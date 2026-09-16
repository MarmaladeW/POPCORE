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
  • Exact catalog identity before fuzzy proposals; conflicting cues need review

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


def normalize_sku(s: str) -> str:
    """Stable SKU identity retains punctuation, internal spacing and every letter."""
    return unicodedata.normalize('NFKC', s or '').lower().strip()


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


_DIGIT_RE = re.compile(r'\d+')
_GEN_RE   = re.compile(r'[一二三四五六七八九十]+代')


def _variant_tokens(s: str) -> tuple:
    """Numeric / generation tokens that distinguish product variants
    ("cons12" vs "cons", "二代" vs "三代")."""
    return (tuple(sorted(_DIGIT_RE.findall(s))), tuple(sorted(_GEN_RE.findall(s))))


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
    · Variant guard: mismatched numeric/generation tokens cap the score at 75
      — "smiski cons12" can look like "smiski cons" but is likely a different
      product, so it must go through human review, never auto-confirm.
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

    # Variant guard: differing digit/generation tokens → likely a different
    # product variant. A human must review it.
    if _variant_tokens(qn) != _variant_tokens(cn):
        s = min(s, 75)

    return min(s, 99)   # 100 is reserved for exact match only


def _score_product_jzm(qn: str, product: dict) -> int:
    """Best score of qn against a product's jizhanming, name_cn_en, and sku.
    Kept for legacy use. Batch imports use match_jzm."""
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

def identity_conflicts(query: str, product: dict) -> bool:
    """Explicit type/variant clues must survive shorthand and alias matching."""
    qn = normalize(query)
    name = normalize(product.get('name_cn_en') or product.get('jizhanming') or '')
    pn = name + ' ' + normalize(product.get('product_type') or '')
    types = (r'figure|figurine|手办', r'plush|毛绒|绒毛',
             r'vinyl|搪胶', r'keychain|keyring|挂件|钥匙扣')
    qt = {pattern for pattern in types if re.search(pattern, qn)}
    pt = {pattern for pattern in types if re.search(pattern, pn)}
    if qt and pt and qt != pt:
        return True
    if _variant_tokens(qn) != _variant_tokens(name):
        return True
    forms = (r'fullset|sealedset|整盒|整套|端(?:$|\s)', r'singlebox|单盒|單盒|散盒')
    qf = {pattern for pattern in forms if re.search(pattern, qn)}
    pf = {pattern for pattern in forms if re.search(pattern, pn)}
    if qf and pf and qf != pf:
        return True
    secret = r'secret|隐藏|隱藏|秘密'
    return bool(re.search(secret, qn)) != bool(re.search(secret, name))


def _limit_hits(hits: list, limit: int) -> list:
    """Keep every exact tie and at least the runner-up for ambiguity checks."""
    hits.sort(key=lambda x: (-x[0], x[1]['id']))
    count = max(2, limit, sum(score == 100 for score, _ in hits))
    return hits[:count]


def load_matching_aliases(con) -> dict:
    """None marks disputed names: keep them reviewable in every matching entry point."""
    aliases = {r['alias_norm']: r['product_id'] for r in
               con.execute('SELECT alias_norm, product_id FROM product_aliases')}
    for row in con.execute("""SELECT DISTINCT pa.alias_norm FROM product_aliases pa
            JOIN match_corrections mc ON mc.norm_name=pa.alias_norm AND mc.product_id<>pa.product_id
            UNION SELECT norm_name FROM match_corrections
            GROUP BY norm_name HAVING COUNT(DISTINCT product_id)>1"""):
        aliases[row[0]] = None
    return aliases


def match_jzm(
    query: str,
    products: list,
    aliases: dict | None = None,
    threshold: int = 75,
    limit: int = 5,
) -> list:
    """Exact names, shorthand, SKUs and aliases across the entire catalog first.

    Return (score, product) hits; only a unique 100 is safe to auto-confirm.
    Fuzzy matches and contradictory identity clues always require review.
    limit is a soft limit: exact ties and the runner-up are never hidden.
    """
    qn = normalize(clean_name(query))
    if not qn:
        return []
    aliases = aliases or {}
    query_sku = normalize_sku(query)
    hits = []
    for p in products:
        fields = [normalize(clean_name(p.get(field) or ''))
                  for field in ('jizhanming', 'name_cn_en')]
        sku = normalize_sku(p.get('sku'))
        sku_exact = bool(sku) and sku == query_sku
        exact = sku_exact or qn in fields or aliases.get(qn) == p['id']
        score = 100 if exact else max(_score_pair_jzm(qn, field) for field in fields)
        if not sku_exact and identity_conflicts(query, p):
            score = min(score, 75)
        if qn in aliases and aliases[qn] is None:
            score = min(score, 99)
        if score >= threshold:
            hits.append((score, p))
    return _limit_hits(hits, limit)


def match_name(
    query: str,
    products: list,
    threshold: int = 60,
    limit: int = 3,
) -> list:
    """
    Match a full product name against products.name_cn_en ONLY.

    Used by the sheet sync to anchor row identity on the sheet's product-name
    column: full names compare against full names, so exact rows score 100 and
    shorthand hits on a different product cannot override this anchor.

    Returns [(score, product), ...] sorted by score desc; [] when nothing
    meets the threshold.
    """
    cleaned = clean_name(query)
    if not cleaned:
        return []
    qn = normalize(cleaned)
    if not qn:
        return []

    hits = []
    for p in products:
        name_n = normalize(p.get('name_cn_en') or '')
        if not name_n:
            continue
        s = _score_pair_jzm(qn, name_n)
        if s >= threshold:
            hits.append((s, p))
    return _limit_hits(hits, limit)


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
        elif hits[0][0] == 100 and (len(hits) == 1 or hits[1][0] < 100):
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
