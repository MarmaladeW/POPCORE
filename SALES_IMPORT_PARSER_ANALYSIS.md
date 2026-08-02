# Sales Import / Parser — Code Map & Gap Report

**Scope:** read-only analysis of the daily sales import pipeline in `MarmaladeW/POPCORE`.
No code was changed.

**How the behaviour below was established:** every claim in Phase 2 and Phase 3 was
verified by running the real `blueprints/sales.py` code — the actual Flask blueprint,
mounted on a test app with only `db`, `auth`, and `blueprints.stores` stubbed so the
routes could run against an in-memory SQLite using the same DDL as `db.py`. Where a
conclusion depends on data I could not see, it is labelled **[unverified]**.

**Important limitation:** `popcore_app/popcore.db` and the two source spreadsheets
(`copy of 11.xlsx`, `POP_CORE_v3.xlsx`) are gitignored and absent from the repo, so the
real product catalogue was unavailable. Fuzzy-match *scores* below were produced against
a 17-product synthetic catalogue and will differ against the real catalogue (thousands of
rows, more near-neighbours competing). Structural behaviour — sectioning, tokenising,
quantity extraction, bucketing, what gets written where — does not depend on catalogue
size and is fully verified.

---

## PHASE 1 — Locate

### 1. Import route (frontend button → backend endpoint)

| Step | Location |
|---|---|
| "Re-import" button | `popcore_app/frontend/src/pages/Sales/index.tsx:565` → `setImportMode(true)` |
| Auto-shown when a day has no sales yet | `popcore_app/frontend/src/pages/Sales/index.tsx:527` |
| Import component mounted | `popcore_app/frontend/src/pages/Sales/index.tsx:534` |
| Paste textarea | `popcore_app/frontend/src/pages/Sales/DailyReportEntry.tsx:572` |
| "Parse Report" button | `popcore_app/frontend/src/pages/Sales/DailyReportEntry.tsx:579` → `handleParse()` at `:137` |
| API wrapper | `popcore_app/frontend/src/api/matcher.ts:191` `parseReportBackend()` → `POST /sales/parse_report` |
| **Parse endpoint** | `popcore_app/blueprints/sales.py:723` `POST /api/sales/parse_report` |
| "Confirm & Log N items" button | `popcore_app/frontend/src/pages/Sales/DailyReportEntry.tsx:630` → `handleSubmit()` at `:252` |
| **Write endpoint** | `popcore_app/blueprints/sales.py:344` `POST /api/sales/submit_daily_report` |

Parsing and writing are two separate calls. `parse_report` writes nothing; the frontend
holds the three buckets in React state, the user edits/accepts them, then
`submit_daily_report` commits in one transaction.

**Two other paste-import paths exist and are *not* part of the daily sales report** —
don't confuse them:
- `frontend/src/pages/Stock/BatchStockModal.tsx` → `POST /api/products/match` +
  `POST /api/stock/batch_operation` (`blueprints/sales.py:195`) — stock movements only.
- `frontend/src/pages/Products/PasteImportModal.tsx` → product catalogue import.

These two use the **frontend** parser in `api/matcher.ts` (`detectSectionHeader`,
`isHeaderLine`, `cleanName`). The daily sales report does **not** — it posts raw text to
the backend. See the divergence note in Phase 2.2.

### 2. Parsing logic

All in `popcore_app/blueprints/sales.py`, under the header comment
`IMPORT PIPELINE (Layers 1 – 5)` at `:585`:

| Layer | Function | Line |
|---|---|---|
| Section keyword table | `_SECTION_MAP` | `:590` |
| Score thresholds | `_SCORE_CONFIRMED = 80`, `_SCORE_REVIEW = 50` | `:609-610` |
| Layer 1 — preprocess | `_preprocess_text()` | `:615` |
| Layer 2 — header detect | `_detect_section_type()` | `:633` |
| Date/store from header | `_extract_date_store()` | `:655` |
| Layer 3 — tokenise one comma-token | `_parse_token()` | `:666` |
| Layers 2–5 orchestration | `parse_daily_report()` | `:723` |
| Layer 4/5 — resolve + bucket | `_resolve_hits()` / `_bucket()` (nested) | `:830` / `:846` |

Matching engine: `popcore_app/matcher.py` — `match_jzm()` `:187`, `normalize()` `:69`,
`clean_name()` `:47`, `_score_pair_jzm()` `:123`.

Optional ML re-ranker: `popcore_app/ranker.py` — `rerank()` `:24`. Inert until ≥30 rows
exist in `match_corrections` and returns candidates unchanged if `sklearn` is missing
(`ranker.py:31-39`); it is called inside a bare `try/except: pass` at `sales.py:839-843`.

### 3. Product name mapping / lookup

There is **one** canonical-name lookup: the `product_aliases` table.

| Item | Location |
|---|---|
| `product_aliases` DDL | `popcore_app/db.py:839` |
| Unique index on `alias_norm` (global) | `popcore_app/db.py:846` |
| Seed alias list (16 entries) | `popcore_app/db.py:398` `_migration_seed_product_aliases()` |
| `section_aliases` DDL (learned section headers) | `popcore_app/db.py:848` |
| REST: list / create / delete product alias | `blueprints/products.py:273`, `:291`, `:314` |
| REST: list / create / delete section alias | `blueprints/products.py:324`, `:335`, `:360` |
| Alias management UI | `frontend/src/pages/Sales/AliasManager.tsx` |
| Learned corrections (feeds `ranker.py`) | `match_corrections`, `db.py:561`; written at `sales.py:456-476` |

There is **no** JSON file, no hard-coded abbreviation dictionary, and no
`product_canonical` column. See Phase 2.5.

### 4. Database tables the parsed data lands in

| Table | DDL | Written by |
|---|---|---|
| `daily_sales` | `db.py:701`, cols added `db.py:714-725`, `UNIQUE(product_id, date, store)` added `db.py:776-804` | `sales.py:403` (sales sections) |
| `stock_transactions` | `db.py:691`, `store_id` added `db.py:217` | `sales.py:421` (`break_display`), `sales.py:441` (`stock_in`) |
| `stock` | `db.py:683` | `sales.py:426`, `:446` |
| `match_corrections` | `db.py:561` | `sales.py:472` |
| `products` (read-only source) | `db.py:628` / `init_db.py:100` | — |
| `stores` (DT / MK / MT) | `db.py:59` | — |

---

## PHASE 2 — Document the parsing logic

### 2.1 Input format

**Raw pasted text.** A plain `<Input.TextArea rows={14}>`
(`DailyReportEntry.tsx:572-578`), posted as JSON. No file upload, no structured form.

```python
# blueprints/sales.py:723-739
@bp.route('/api/sales/parse_report', methods=['POST'])
@role_required('staff')
def parse_daily_report():
    data       = request.get_json(silent=True) or {}
    raw_text   = data.get('text', '')
    store_code = (data.get('store_code') or 'DT').strip().upper()

    if not raw_text.strip():
        return jsonify({'error': 'text is required'}), 400
```

Layer 1 rewrites the text before anything is split:

```python
# blueprints/sales.py:615-630
def _preprocess_text(text: str) -> str:
    """Layer 1 — apply in strict order before any line splitting or parsing."""
    # 1. NFKC normalize (converts ＊→*, fullwidth chars, etc.)
    text = unicodedata.normalize('NFKC', text)
    # 2. Strip parentheticals — staff notes, never product data
    text = re.sub(r'（[^）]*）', '', text)
    text = re.sub(r'\([^)]*\)', '', text)
    # 3. Normalize star separator: ∗ (U+2217) not handled by NFKC, strip spaces
    text = text.replace('∗', '*')
    text = re.sub(r'\s*\*\s*', '*', text)
    # 4. Per-line: strip leading/trailing whitespace, collapse internal spaces
    lines = [re.sub(r'  +', ' ', ln.strip()) for ln in text.split('\n')]
    text = '\n'.join(lines)
    # 5. Remove double commas
    text = text.replace(',,', ',').replace('，，', '，')
    return text
```

Two notes on this function, both verified:

- Step 1 (NFKC) already converts `（`/`）` → `(`/`)`, so the full-width regex on the
  first line of step 2 can never match. Harmless dead code, but it means all
  paren-stripping is done by the ASCII regex.
- **Step 2 runs on the whole blob before line-splitting, and `[^)]` matches newlines.**
  An unclosed parenthesis therefore deletes every line up to the next closing paren.
  Verified — this input:

  ```
  比奇堡二代*3（漏了右括号
  smiski bed*5
  smiski Sunday*2）
  ```

  reduces to just `比奇堡二代*3`. `smiski bed*5` and `smiski Sunday*2` are gone —
  no failed row, no warning, no trace.

### 2.2 How input is split into sections

**It does recognise section headers** — it does not treat all lines the same way.

```python
# blueprints/sales.py:590-606
_SECTION_MAP = [
    ('卡机汇总',   'pos'),
    ('随手记汇总', 'cash'),
    ('随手记',     'cash'),
    ('卡机',       'pos'),
    ('入店',       'stock_in'),
    ('出店',       'stock_out'),
    ('卖display',  'sell_display'),
    ('卖Display',  'sell_display'),
    ('拆display',  'break_display'),
    ('拆Display',  'break_display'),
    ('娃娃机',     'skip'),        # CLAW_MACHINE — skip
    ('员工折扣',   'employee_discount'),
    ('晚盘',       'skip'),        # EVENING_CHECK — skip
    ('博主探店',   'skip'),        # INFLUENCER — skip
    ('现金',       'skip'),        # CASH_TOTAL — skip
]
```

Of the six headers asked about: **卡机汇总, 随手记汇总, 入店, 卖display, 出店 are
recognised. 入display is not in the map at all** — and neither is any other `入…`
variant besides `入店`.

Detection is **substring containment against the whole line**, not an anchored
header match:

```python
# blueprints/sales.py:633-652
def _detect_section_type(line: str, section_aliases: dict) -> str | None:
    lower = line.lower()
    for keyword, section in _SECTION_MAP:
        if keyword.lower() in lower:
            return section
    # User-saved section aliases (alias_norm → section_type)
    line_norm = re.sub(r'\s+', '', lower)
    for alias_norm, stype in section_aliases.items():
        if alias_norm in line_norm:
            return stype
    # Ends with colon → treat as unknown header
    stripped = line.rstrip()
    if stripped.endswith(':') or stripped.endswith('：'):
        return 'unknown'
    return None
```

Any line matching a keyword becomes a boundary and is **consumed** — its content is never
parsed for products:

```python
# blueprints/sales.py:793-801
    for i, line in enumerate(lines):
        s = line.strip()
        if not s:
            continue
        if i in boundary_line_idxs:
            sec = _detect_section_type(s, section_aliases)
            if sec == 'unknown' and s not in unknown_sections:
                unknown_sections.append(s)
            continue
```

Three consequences, all verified:

1. **Inline `header：product*qty` on one line loses the product entirely.** Verified for
   `入店`, `出店`, `卖display`, `拆display`: the line is classified as a header, `continue`
   fires, and the product appears in **no** bucket. This is Phase 2.4 pattern 6.
2. **A line that is not a keyword and does not end in a colon is not a boundary.** Verified:
   `入display` on its own line (no colon) returns `None`, so the section pointer does not
   move and the following product lines inherit the *previous* section. See Phase 4.2.
3. **Default section before the first header is `cash`** — i.e. real sales:

```python
# blueprints/sales.py:779-787
    def section_at(idx: int) -> tuple[str, str]:
        # Default before first detected header → CASH_SALES (spec §Layer 2)
        cur_sec, cur_hdr = 'cash', ''
        for bi, bsec, bhdr in boundaries:
            if bi <= idx:
                cur_sec, cur_hdr = bsec, bhdr
            else:
                break
        return cur_sec, cur_hdr
```

The report's own date/store header line is **not** excluded from product parsing. Verified:
`2026.04.01 DT汇总` is used for date extraction *and* then parsed as a product token,
landing in `failed` with `reason='no_match'` on every single import.

> **Backend/frontend divergence.** `frontend/src/api/matcher.ts:98-114` defines a second,
> *different* section table: it maps `娃娃机 → 'claw'` and `现金汇总 → 'ignore'`, where the
> backend maps `娃娃机 → 'skip'` and `现金 → 'skip'`. It also orders `卡机` before
> `随手记汇总`. That table is used only by `BatchStockModal.tsx` and
> `PasteImportModal.tsx`, not by the daily report — but it means the backend can never
> emit a `claw` item from pasted text, even though `claw` is a valid sales section at
> `sales.py:375` and has a UI tag at `DailyReportEntry.tsx:26`. `claw` can only arise from
> a saved `section_aliases` row or a manual reassignment in the Failed tab.

### 2.3 Does it distinguish sales from non-sales?

**Yes — at write time, in `submit_daily_report`.** The split is explicit:

```python
# blueprints/sales.py:375
    SALES_SECTIONS = {'pos', 'cash', 'claw', 'sell_display', 'employee_discount'}
```

Verified end-to-end by posting one item per section to `/api/sales/submit_daily_report`
(response: `{'ok': True, 'sales_upserted': 5, 'stock_transactions': 2}` for 8 items):

| Section | Header | Lands in | Counted as a sale? |
|---|---|---|---|
| `pos` | 卡机汇总 | `daily_sales.qty_pos` | ✅ yes |
| `cash` | 随手记汇总 | `daily_sales.qty_cash` | ✅ yes |
| `sell_display` | 卖display | `daily_sales.qty_pos`, note `display_sold` | ✅ yes |
| `claw` | (unreachable from text) | `daily_sales.qty_pos`, note `claw_machine` | ✅ yes |
| `employee_discount` | 员工折扣 | `daily_sales.qty_pos`, note `employee_discount` | ✅ yes |
| `stock_in` | 入店 | `stock_transactions` + `stock` | ❌ no — correct |
| `break_display` | 拆display | `stock_transactions` + `stock` | ❌ no — correct |
| **`stock_out`** | **出店** | **nothing at all** | ❌ no — **silently discarded** |
| `skip` | 娃娃机/晚盘/博主探店/现金 | dropped at parse time | ❌ no — correct |

So the three sales sections named in the task (卡机汇总, 随手记汇总, 卖display) are all
correctly treated as sales, and 入店 / 拆display are correctly treated as inventory.

**`stock_out` (出店) is the hole.** The `if/elif` chain at `sales.py:390-454` handles
`SALES_SECTIONS`, then `break_display`, then `stock_in`, and has no `else`. A
`stock_out` item matches none of them and falls off the end of the loop body. Verified:
submitting `{'product_id': 7, 'section': 'stock_out', 'qty_pos': 1}` produced **no row in
`daily_sales`, none in `stock_transactions`, and no stock adjustment** — while the UI had
counted it toward "Confirm & Log N items" (`DailyReportEntry.tsx:330,333`) and then
reported `${totalReady} items saved` (`:592`). The backend's own
`sales_upserted` / `stock_transactions` counts are returned but never displayed.

### 2.4 Single-line parsing — the six real patterns

```python
# blueprints/sales.py:666-720
def _parse_token(token: str, section: str) -> dict | None:
    t = token.strip()
    if not t:
        return None

    # Step 2: lastIndexOf('*') → quantity
    inferred_split_name = None
    star_idx = t.rfind('*')
    if star_idx > 0:
        raw_name = t[:star_idx].strip()
        qty_str  = t[star_idx + 1:].strip()
        # Check for trailing non-digit text after leading digits (e.g. "1星星人随心配粉")
        m = re.match(r'^(\d+)(.+)$', qty_str)
        if m:
            qty = max(1, int(m.group(1)))
            inferred_split_name = m.group(2).strip() or None
        else:
            try:
                qty = max(1, int(qty_str))
            except (ValueError, TypeError):
                qty = 1
        flagged = False
    else:
        raw_name = t
        qty      = 1
        flagged  = True  # no * found — no explicit quantity

    # Step 3: STOCK_IN trailing-number → box_size
    box_size = None
    if section == 'stock_in' and raw_name:
        m = re.match(r'^(.*\D)\s*(\d+)$', raw_name)
        if m and m.group(1).strip():
            box_size = int(m.group(2))
            raw_name = m.group(1).strip()
```

Line splitting into tokens happens just before this, on **both** comma widths:

```python
# blueprints/sales.py:818-823
        # Normal section: split on comma, parse each token individually
        sub_tokens = re.split(r'[,，]', s)
        for token in sub_tokens:
            item = _parse_token(token, sec)
            if item:
                raw_items.append(item)
```

Results, each run under a `卡机汇总：` header:

| # | Input | Verdict | Verified behaviour |
|---|---|---|---|
| 1 | `比奇堡二代*3` | ✅ **correct** | `raw_name='比奇堡二代'`, `qty=3`, `qty_pos=3`, `flagged=False` → `confirmed` |
| 2 | `smiski Sunday*2，smiski bed*1` | ✅ **correct** | full-width comma splits fine → two items, `qty=2` and `qty=1`, both `confirmed` |
| 3 | `smiski hipper` | ⚠️ **neither *1 nor error — silently withheld** | `qty=1` but **`flagged=True`**; item still resolves (alias hit, score 100) and lands in `confirmed`, but the frontend skips every flagged row at submit |
| 4 | `13周年聚光灯下*1（hacipupu）` | ✅ **correct** | paren stripped in Layer 1 → `raw_name='13周年聚光灯下'`, `qty=1` → `confirmed` |
| 5 | `smiski cons12*3` | ⚠️ **`12` silently absorbed into the name** | `raw_name='smiski cons12'`, `qty=3`. Not a case size — the `box_size` branch only runs for `section == 'stock_in'`. The `12` survives into the fuzzy matcher and is discarded there |
| 6 | `出店：sa糖果娃包粉*1（去太古）` | ❌ **dropped entirely** | line contains `出店` → classified as a header → consumed. Product appears in **no** bucket, no warning |

Detail on **#3**. `flagged=True` means "no quantity found". The frontend refuses to submit
such a row:

```tsx
// DailyReportEntry.tsx:265-268
    for (const r of confirmed) {
      if (r.removed || r.flagged || !r.product?.id) continue
      payload.push(buildPayloadItem(r, r.product, 'confirmed'))
    }
```

and renders a red `无数量` tag with a `value={0}` number input
(`DailyReportEntry.tsx:347-360`). The user must type a quantity, which clears `flagged`.
So it is **not** treated as `*1`, not ignored at parse time, and does not error — it is
surfaced but excluded from the write until a human intervenes. It is also excluded from
the `confirmedReady` count (`:330`), so the UI total is honest here. Reasonable design;
just note that a staff member clicking straight through loses the line.

Detail on **#5**. Against the synthetic catalogue, `smiski cons12` scored **exactly 80**
against `Smiski Cons` — the auto-confirm threshold (`_SCORE_CONFIRMED = 80`, `:609`) — so
it went to `confirmed` with no human review. Sitting exactly on the boundary is fragile:
**[unverified against the real catalogue]** whether the true nearest product still clears
80, and whether a different product outranks it. If the catalogue contains both a plain
and a numbered variant, this is a plausible mis-assignment that never surfaces for review.

Also note the "inferred split" path at `sales.py:885-902`, which handles the *reverse*
shape (`*1星星人随心配粉`): digits then trailing text after the star produce a second
synthetic item with `qty=1`, `flagged=True`, `reason='inferred_split'`, forced into
`review` regardless of score (`:854`). None of the six patterns trigger it.

### 2.5 Where name normalization happens

Normalization is string-level only, in `matcher.py`:

```python
# matcher.py:69-81
def normalize(s: str) -> str:
    s = (s or '').strip()
    s = unicodedata.normalize('NFKC', s)
    s = s.lower()
    s = re.sub(r'[\s　]+', '', s)   # remove ALL whitespace
    if s.endswith('s') and len(s) > 1:
        s = s[:-1]
    return s
```

Canonical resolution is a two-step in `parse_report`: **exact alias hit first**, then fuzzy.
Note this inverts `match_jzm`'s own waterfall, where alias is stage 3 *after* jizhanming
and name (`matcher.py:229-268`):

```python
# blueprints/sales.py:830-844
    def _resolve_hits(name: str, raw: str) -> list:
        """Alias lookup then fuzzy match; applies re-ranker when model is ready."""
        qn = _norm_jzm(_clean_jzm(name))
        if qn and qn in aliases:
            pid = aliases[qn]
            p = next((x for x in all_products if x['id'] == pid), None)
            return [(100, p)] if p else []
        fuzz_hits = match_jzm(raw, all_products, aliases, threshold=_SCORE_REVIEW, limit=5)
```

**Is there a canonical name lookup?** Yes, exactly one: the `product_aliases` DB table.
There is **no dictionary, no JSON file, and no abbreviation-expansion logic anywhere**.
`sa`, `smiski`, `dimoo` are *not* expanded — they are matched literally as substrings
against `products.jizhanming` by `_score_pair_jzm()` (`matcher.py:123`), with a length
penalty and a CJK-coverage penalty.

The complete seeded alias list — every alias the system recognises out of the box
(`db.py:406-425`):

| # | Alias | → canonical `jizhanming` |
|---|---|---|
| 1 | `sa hipper` | `SA Original Hipper` |
| 2 | `smiski hipper` | `Smiski Hipper` |
| 3 | `smiski hippers` | `Smiski Hipper` |
| 4 | `三丽鸥hippers` | `三丽鸥Hipper` |
| 5 | `smiski cheers` | `Smiski Cheer` |
| 6 | `随心配蓝` | `星星人随心配蓝` |
| 7 | `随心配粉` | `星星人随心配粉` |
| 8 | `crybaby度假` | `哭娃度假` |
| 9 | `sa tatto stick` | `SA Tattoo Sticker` |
| 10 | `sa original` | `SA Original Hipper` |
| 11 | `smiski原版` | `Smiski Hipper` |
| 12 | `smiski cheer` | `Smiski Cheer` |
| 13 | `crybaby假期` | `哭娃度假` |
| 14 | `随心配` | `星星人随心配蓝` (comment: "ambiguous — prefer blue variant") |
| 15 | `sa tattoo` | `SA Tattoo Sticker` |
| 16 | `sa sticker` | `SA Tattoo Sticker` |

That is **16 seeds but only 14 rows**, verified: `normalize()` strips a trailing `s`
(`matcher.py:79`), so #3 collides with #2 and #5 collides with #12 on `alias_norm`, and the
`INSERT OR IGNORE` at `db.py:438` drops the duplicates. Harmless (same target), but it
means "add the plural too" silently no-ops.

Each seed is also conditional on the product already existing —
`if not row: continue` (`db.py:436-437`) — so any seed whose `jizhanming` isn't in
`products` is skipped, permanently, since the migration is once-only.

Aliases also grow at runtime: picking a product manually in the Review or Failed tab calls
`saveAlias()` (`DailyReportEntry.tsx:242,247` → `products.py:291`). Two cautions there:
`alias_norm` is **globally unique** (`db.py:846`) so one spelling can map to only one
product catalogue-wide, and `products.py:305` uses `INSERT OR REPLACE`, so re-assigning an
existing alias to a different product overwrites the old mapping with no warning.

**Product strings that would NOT match anything.** Against the synthetic catalogue
(structure is real, scores are illustrative):

| Query | Result | Bucket |
|---|---|---|
| `sa` | no match (length penalty: `len(qn) < 4 and lr < 0.6` → `s = raw * lr`) | failed |
| `crybaby` | no match — no product has `crybaby` in `jizhanming`; the alias needs the full `crybaby度假` | failed |
| `hacipupu` | no match | failed |
| `smiski` | matched an arbitrary Smiski product at 76 | review |
| `dimoo` | matched an arbitrary Dimoo product at 69 | review |
| `sa糖果娃` (truncated) | 78 | review |
| `smiski新款`, `dimoo花花` | no match (CJK-coverage penalty zeroes the score) | failed |

The pattern that matters: **a bare brand token alone never resolves correctly** — it either
fails or attaches to a semi-random product of that brand at a review-level score. The
system is entirely dependent on the staff writing brand + descriptor.

### 2.6 What happens on a completely unrecognized line

**No crash, no exception, no log.** It becomes a `failed` bucket entry and the import
continues:

```python
# blueprints/sales.py:846-849
    def _bucket(item: dict, hits: list) -> None:
        """Place a resolved item into the correct bucket."""
        if not hits:
            failed.append({**item, 'reason': 'no_match', 'score': 0, 'candidates': []})
            return
```

Verified with `今天天气很好没什么事`, `zzzzz qqqq`, and a bare `*5` — all three landed in
`failed` with `reason='no_match'`, and the surrounding good rows parsed normally. The
frontend shows them in a red Failed tab with a manual product picker and a discard button
(`DailyReportEntry.tsx:498-557`), and `canSubmit` does **not** require clearing them
(`:338`) — so unresolved failures are simply left behind when the user submits.

Reason codes actually emitted by the backend: `no_match` (`:849`), `empty_name` (`:879`),
`unknown_section` (`:875`). The frontend also has a label for `low_score`
(`DailyReportEntry.tsx:517`) that the backend never sends — anything scoring ≥50 goes to
`review` instead. Dead label, cosmetic only.

**However**, "unrecognized" is not the same as "reported". Three inputs are dropped with
*no* row in any bucket and no warning of any kind:
1. text swallowed by an unclosed parenthesis (2.1);
2. a product on the same line as a recognised section keyword (2.2 / 2.4 #6);
3. any item whose section resolves to `skip` — `娃娃机`, `晚盘`, `博主探店`, `现金`
   (`sales.py:805-806`).

Only the third is intentional.

---

## PHASE 3 — Output schema

### 3.1 Fields actually stored

`daily_sales` (`db.py:782-793`, current shape after the `store` migration):

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `product_id` | INTEGER NOT NULL → `products(id)` | resolved product only |
| `date` | TEXT NOT NULL | `YYYY-MM-DD` |
| `store` | TEXT NOT NULL DEFAULT `'DT'` | store **code**, not FK |
| `qty_pos` | INTEGER NOT NULL DEFAULT 0 | 卡机 |
| `qty_cash` | INTEGER NOT NULL DEFAULT 0 | 随手记 |
| `qty_sold` | INTEGER NOT NULL DEFAULT 0 | written as `qty_pos + qty_cash` (`sales.py:393`) |
| `notes` | TEXT DEFAULT `''` | doubles as the section tag |
| `created_at` | TEXT DEFAULT `datetime('now')` | |
| | | `UNIQUE(product_id, date, store)` |

**There is no `product_raw` column and no `product_canonical` column.** The raw pasted
string is *not* persisted on the sales row — the canonical name lives only in
`products.jizhanming`, reached by join. The raw string survives in exactly one place, and
only for rows a human had to correct:

```python
# blueprints/sales.py:456-470 (abridged)
        # Record match corrections for manually-reviewed items (Issue 3)
        for item in items:
            bucket = (item.get('source_bucket') or '').strip()
            if bucket not in ('review', 'failed'):
                continue
            ...
            corrections.append((raw_name_c, norm_name_c, pid_c,
                                 fuzzy_score_c, top_score_c, was_top_c, store_code))
```

i.e. `match_corrections.raw_name` — auto-confirmed rows (the large majority) keep no
record of what was typed. For an audit trail on a historical backfill, that matters.

**There is no `category`/`type` column either.** Section identity is encoded as free text
in `notes`, and only for three of the five sales sections:

```python
# blueprints/sales.py:395-401
                tag = notes
                if section == 'employee_discount' and not tag:
                    tag = 'employee_discount'
                elif section == 'sell_display' and not tag:
                    tag = 'display_sold'
                elif section == 'claw' and not tag:
                    tag = 'claw_machine'
```

Note `tag = notes` first: **a staff note overwrites the section tag**. If the user typed
anything in the Notes box on a `sell_display` row, `display_sold` is never written and the
row becomes indistinguishable from a plain 卡机 sale. And `pos`/`cash` get no tag at all —
they're distinguished only by which quantity column is non-zero. So "was this a display
sale?" is answerable only by a substring search on a free-text field that a note can clobber.

On conflict the endpoint **accumulates**:

```python
# blueprints/sales.py:407-415
                    ON CONFLICT(product_id, date, store) DO UPDATE SET
                        qty_pos  = qty_pos  + excluded.qty_pos,
                        qty_cash = qty_cash + excluded.qty_cash,
                        qty_sold = qty_sold + excluded.qty_sold,
                        notes    = CASE
                            WHEN notes = '' THEN excluded.notes
                            WHEN excluded.notes = '' THEN notes
                            ELSE notes || '; ' || excluded.notes
                        END
```

This is deliberate — it lets the same product appear under 卡机汇总 *and* 卖display in one
report and sum correctly. It also makes re-import non-idempotent (Phase 4.4).

`stock_transactions` (`db.py:691` + `db.py:225-244`): `product_id`, `txn_type`, `qty`,
`location`, `date`, `notes`, `store_id`, `created_at`. Note `store_id` here is a numeric FK,
whereas `daily_sales.store` is a text code — the two tables key store differently.

### 3.2 Does the schema distinguish store location?

**Yes.** `stores` is seeded with three rows (`db.py:67-69`):

```python
        cur.execute("INSERT INTO stores (code, name, address) VALUES ('DT', 'Downtown Toronto', '')")
        cur.execute("INSERT INTO stores (code, name, address) VALUES ('MK', 'Markham', '')")
        cur.execute("INSERT INTO stores (code, name, address) VALUES ('MT', 'Midtown', '')")
```

`daily_sales.store` holds the code, it's part of the uniqueness key, it's indexed
(`db.py:725`), and every read/write path requires it — `_require_store_param` (`sales.py:17`)
and `_require_store_body` (`sales.py:30`), with `ALL` allowed for reads and rejected for
writes (`sales.py:35`).

One caveat on the import path specifically: the store is **guessed from the pasted text**
and silently overrides the store the user has selected in the UI:

```python
# blueprints/sales.py:655-663
def _extract_date_store(first_line: str, fallback_store: str):
    """Parse date and store code from a report header line (best-effort)."""
    dm = _DATE_RE.search(first_line)
    detected_date = (
        f"{dm.group(1)}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}" if dm else None
    )
    sm = re.search(r'([A-Za-z]{2,6})(?:汇总|店)', first_line)
    store = sm.group(1).upper() if sm else fallback_store
    return detected_date, store
```

The regex takes **any** 2–6 Latin letters before 汇总/店. It is not validated against the
`stores` table at parse time, and `parse_report` returns it as `store`, which the frontend
adopts as `parsedStore` (`DailyReportEntry.tsx:144`) and submits (`:260,298`). Validation
happens only later, in `_require_store_body` → `_resolve_store`, so a typo'd header yields
a 400 at submit rather than a warning at parse. Worth confirming
**[unverified]** how the two months of historical logs actually spell their store header.

### 3.3 Existing export-to-CSV path

**Yes** — `GET /api/sales/export`, `blueprints/sales.py:495`, manager-only, wired to the
"Export" button at `frontend/src/pages/Sales/index.tsx:581`. Defaults to the last 30 days
(`:501`).

```python
# blueprints/sales.py:520
    header = '日期,记账名,SKU,系列,类型,单价,卡机数量,现金/转账数量,总销量,备注'
```

Ten columns: date, `jizhanming`, SKU, `ip_series`, `product_type`, price, `qty_pos`,
`qty_cash`, `qty_sold`, notes. UTF-8 BOM prefixed for Excel (`:521`), filename
`sales_{store}_{from}_{to}.csv`.

Two limits: it requires a single concrete store — `ALL` reaches
`WHERE ds.store = ?` at `:514` and would match nothing rather than aggregating — and it
exports **no section/type column**, so the 卖display / 员工折扣 / 娃娃机 distinction is
only visible if it happens to have survived in `notes`.

`popcore_app/export_excel.py` is **not** a sales export — it's a full **product catalogue**
dump to `.xlsx` (`SELECT … FROM products p LEFT JOIN stock`, `export_excel.py:33-56`),
run manually from the CLI. There is no sales `.xlsx` export.

---

## PHASE 4 — Gap report

### 4.1 The six test patterns

| Pattern | Status |
|---|---|
| `比奇堡二代*3` | ✅ **Correct.** |
| `smiski Sunday*2，smiski bed*1` | ✅ **Correct.** Full-width comma is handled. |
| `smiski hipper` | ⚠️ **Held back, not counted.** Parsed and matched, but marked `flagged` (no explicit qty) and excluded from the write until a human types a number. Not treated as `*1`, not an error. A staff member who clicks straight through loses the line silently. |
| `13周年聚光灯下*1（hacipupu）` | ✅ **Correct.** Parenthetical stripped up front. |
| `smiski cons12*3` | ⚠️ **Qty right, name lossy.** `qty=3` correct; `12` stays glued to the name and is then discarded by the fuzzy matcher. Scored *exactly* 80 in testing — the auto-confirm threshold — so it commits with no human review. Case size vs. name is genuinely ambiguous here and the parser never asks. |
| `出店：sa糖果娃包粉*1（去太古）` | ❌ **Dropped entirely.** The line matches the `出店` keyword, is classified as a section header, and is consumed. The product reaches no bucket and produces no warning. |

**4 of 6 handled; 1 quietly withheld; 1 lost outright.** The two failures are the two most
important for correctness: an un-flagged transfer-out disappears, and an ambiguous case
size auto-commits.

### 4.2 Do 入店 / 入display / 出店 get counted as sales?

**入店 — no. Correct.** On its own line (colon optional, verified both ways) it maps to
`stock_in` and writes to `stock_transactions` + `stock`, never `daily_sales`.

**出店 — no, but silently discarded. Severity: MEDIUM-HIGH.** Not a phantom sale, so the
sales numbers stay clean. But `stock_out` is missing from every branch of
`submit_daily_report` (`sales.py:390-454`), so a 出店 item — which arrived in the
`confirmed` bucket and was counted in the UI's "Confirm & Log N items" — writes **nothing
anywhere**, and the UI then reports it as saved. Inventory silently drifts every time
stock moves between DT and Markham, and the operator has no way to notice. Written inline
(`出店：product*1`) it's worse: the product is destroyed at parse time.

**入display — not recognised at all. Severity: HIGH.** `入display` is absent from
`_SECTION_MAP`. Three distinct behaviours, all verified:

1. **`入display` with no colon → phantom sales.** The line isn't a keyword and doesn't end
   in a colon, so `_detect_section_type` returns `None`, no boundary is recorded, and every
   following product line **inherits the previous section**. Verified: with `随手记汇总：`
   above it, `smiski bed*1` and `smiski Sunday*2` — display stock-in, not sales — were
   written to `confirmed` as `section='cash'` at score 100 and would commit straight into
   `daily_sales.qty_cash` as real revenue. No warning; the only trace is a stray
   `入display` row in the Failed tab that reads as harmless noise. **This is the real bug
   in the task's hypothesis** — and note it's a general fault, not specific to
   `入display`: *any* unrecognised header without a colon leaks its contents into the
   preceding section.
2. **`入display：` with a colon → safe but blocked.** Returns `'unknown'`, items go to
   `failed` with `reason='unknown_section'`, and `canSubmit` requires the user to classify
   the header (`DailyReportEntry.tsx:253-257`). Safe. But the classify dropdown offers only
   the eight `SECTION_META` keys (`:21-30`) — there is no `入display` option, so the user
   must approximate with 入店 or 卖display. Whatever they pick is persisted to
   `section_aliases` and silently reused for every future import (`:223`).
3. **`入display：product*1` inline → phantom sale via "Accept all".** Verified: the whole
   string `入display:sa糖果娃包粉` was fuzzy-matched as a *product name*, scoring 55
   against `SA糖果娃包粉` under `section='pos'` → `review`. One click of "Accept all with
   match" (`DailyReportEntry.tsx:728-733`) turns it into a phantom POS sale.

**Summary of severity:**

| Finding | Severity | Effect |
|---|---|---|
| Unrecognised header without a colon leaks its contents into the previous section (`入display`, and any other) | **HIGH** | Non-sales quantities written to `daily_sales` as revenue. Inflates sales. Silent. |
| Inline `入display：product*qty` fuzzy-matches as a product under the previous section | **HIGH** | Phantom sale on "Accept all with match". |
| `stock_out` accepted, counted in the UI, written nowhere | **MEDIUM-HIGH** | Silent inventory drift + false "saved" report. |
| Inline `keyword：product*qty` destroys the product (入店/出店/卖display/拆display) | **MEDIUM-HIGH** | Silent loss, no bucket, no warning. |
| Unclosed parenthesis deletes all lines to the next `)` | **MEDIUM** | Silent multi-line loss. |
| `smiski cons12`-shaped names auto-confirm at exactly the 80 threshold | **MEDIUM** | Possible wrong product, never reviewed. |
| Section identity stored in `notes`, overwritten by any staff note | **MEDIUM** | 卖display / 员工折扣 / 娃娃机 become unattributable; no CSV column for it. |
| Report's own date/store line always lands in Failed | **LOW** | Cosmetic noise, trains staff to ignore the Failed tab — which is what hides the findings above. |

### 4.3 Product names with no canonical mapping today

The whole mapping layer is 14 alias rows (from 16 seeds) plus whatever staff have added by
hand. Structural gaps, verified:

- **Bare brand tokens never resolve correctly.** `sa` and `crybaby` match nothing; `smiski`
  and `dimoo` attach to a semi-arbitrary product of that brand at review-level scores.
- **`crybaby` and `hacipupu` have no product-level presence.** `crybaby` only resolves as
  part of `crybaby度假`/`crybaby假期`; `hacipupu` appears in the test data purely as a
  parenthetical note and is stripped before matching, so it maps to nothing at all.
- **Truncated names degrade to review, not failure** — `sa糖果娃` for `SA糖果娃包粉` scored
  78, one point under auto-confirm. Small catalogue changes flip such items between
  buckets.
- **`随心配` is knowingly ambiguous** — seeded to the blue variant with the comment
  `# ambiguous — prefer blue variant` (`db.py:422`). Any log line meaning the pink one gets
  silently attributed to blue.
- **Plural aliases silently no-op** (`normalize()` strips trailing `s`, so `smiski hippers`
  collapses onto `smiski hipper`).
- **Seeds for products missing from `products` are skipped permanently** (`db.py:436-437`,
  once-only migration).

**[unverified]** Which specific strings in the two months of real logs fail — that needs
the real `products` table and the actual log text. The right way to answer it is to run
`/api/sales/parse_report` over all ~60 days against production data and tally the
`failed` + `review` buckets. Nothing needs to be written to do that: the endpoint is
read-only.

### 4.4 Could this be reused as-is to backfill ~2 months of daily text logs?

**No — not as-is.** The matching engine and the three-bucket review UI are genuinely
reusable and are the hard part; the blockers are the section layer and the write semantics.

Two blockers are specific to backfilling, and neither shows up in day-to-day single-day use:

1. **One paste = one day.** `detected_date` is taken from the first non-empty line only
   (`sales.py:760-764`), and `submit_daily_report` applies a single `date` to every item
   (`sales.py:363,416`). Verified: pasting two dated reports together detected only
   `2026-04-01` and merged **day 2's products into day 1**, with `2026.04.02 DT汇总`
   landing in Failed as if it were a product. A 60-day log must be split into 60 pastes
   and driven through the review UI 60 times.
2. **Re-import double-counts.** `submit_daily_report` accumulates
   (`qty_pos = qty_pos + excluded.qty_pos`, `sales.py:408`). Verified: submitting the same
   item twice produced `qty_pos=3` then `qty_pos=6`. Any correction, retry, or accidental
   double-submit silently inflates that day. (`/api/sales/batch_upsert` at `sales.py:330`
   *replaces* instead — verified idempotent at `qty_pos=3` across two calls — so the
   codebase already contains the safer semantics, just on a different endpoint.)
   `DELETE /api/sales/clear_day` (`sales.py:537`, manager-only) exists as a manual
   mitigation, but nothing in the import flow calls it.

**Minimum changes before a backfill is trustworthy** (roughly in priority order — this is
scoping, not a plan I've implemented):

1. Stop unrecognised headers from leaking. Require a header match to be anchored at the
   start of a line (or otherwise treat any line ending in a colon *and* any line that
   fails to parse as a product as a boundary candidate) so `入display` can never inherit
   `cash`. Fixes the HIGH-severity phantom sales.
2. Add `入display` — and any other real section vocabulary in the logs — to `_SECTION_MAP`,
   plus a matching entry in `SECTION_META` so the classify dropdown can express it.
   **[unverified]** what the full real vocabulary is; that should be harvested from the two
   months of logs first.
3. Split header from content on inline `keyword：product*qty` lines instead of consuming the
   whole line, so 出店/入店 lines stop vanishing.
4. Handle `stock_out` in `submit_daily_report` — or reject it explicitly. Silently
   accepting and discarding is the worst of the three options.
5. Make the write idempotent per (date, store) for backfill — either replace rather than
   accumulate, or clear-then-insert per day. Without this, a 60-day backfill has no safe
   retry.
6. Accept an explicit date per report (or split a multi-day paste on date lines) so 60 days
   can be processed without 60 manual passes.
7. Persist the raw pasted string on every row, not just corrected ones, so a historical
   import is auditable after the fact.
8. Add a real section/type column (or at minimum stop `tag = notes` from overwriting the
   section tag at `sales.py:395`) and surface it in the CSV export.
9. Skip the report's own date/store header line so the Failed tab contains only genuine
   problems — otherwise staff learn to ignore the one surface that reveals items 1–4.

### Secondary finding, outside the sales path

`stock_in` quantities look double-multiplied. `_parse_token` reads the trailing number in
`入店` lines as `box_size`, and the UI labels it `盒/端` and displays `qty × box_size` 盒
(`DailyReportEntry.tsx:362-368`). But the backend multiplies by `products.boxes_per_dan`
— which is also 盒/端 ("Boxes per Display", `ProductModal.tsx:243`) — a second time:

```python
# blueprints/sales.py:434-440
                box_size   = int(item.get('box_size',  1) or 1)
                num_boxes  = int(item.get('num_boxes', 1) or 1)
                total_duan = box_size * num_boxes
                cur.execute('SELECT product_type, boxes_per_dan FROM products WHERE id=?', (pid,))
                prow = cur.fetchone()
                bpd  = (prow['boxes_per_dan'] or 1) if (prow and prow['product_type'] == '盲盒') else 1
                total_units = total_duan * bpd
```

Verified: `入店：dimoo奇遇小夜灯 6*2` with `boxes_per_dan=12` and `product_type='盲盒'`
wrote **144** units while the UI displayed **12盒**. The variable name `total_duan`
suggests `box_size * num_boxes` was *intended* to be a 端 count, which would make the third
multiplication correct — but then `box_size` cannot mean 盒/端 as the UI labels it. The two
readings disagree, and one of them is wrong. This affects inventory, not sales figures, so
it does not change the report above — flagging it because it sits in the same import
transaction. **[unverified]** which reading matches the operators' intent; that needs
someone who knows the 端/盒 convention in the source logs.

---

## Appendix — verification method

The probe imported the real blueprint and exercised it over HTTP via Flask's test client:

- stubs: `db.get_db` → shared in-memory SQLite; `auth.login_required` /
  `auth.role_required` → pass-through; `blueprints.stores._resolve_store` → `(1, code)`.
- schema: copied verbatim from `db.py` for `products`, `product_aliases`,
  `section_aliases`, `daily_sales`, `stock_transactions`, `stock`, `match_corrections`.
- aliases: seeded by re-running the exact seed list and `normalize()` from
  `db.py:398-443`, which is how the 16-seeds-to-14-rows collision was confirmed.
- catalogue: 17 synthetic products covering every product named in the six test patterns
  plus every alias target.
- `blueprints/sales.py`, `matcher.py`, and `db.py` were read but never modified; nothing was
  installed into the repo (`flask` and `rapidfuzz` went into a throwaway venv outside it).

Cases exercised: the six patterns; all six named section headers with and without colons;
headers inline with product content; a product line before any header; garbage and
bare-`*5` lines; an unclosed parenthesis spanning lines; a two-day paste; repeated submits
against both `submit_daily_report` and `batch_upsert`; and one item per section through
`submit_daily_report` with a full dump of the resulting rows.
