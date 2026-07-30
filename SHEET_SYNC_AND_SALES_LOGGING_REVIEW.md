# Sheet Sync & Sales Logging — Quality Review

**Scope:** read-only assessment of (1) the Google Sheet → product sync and (2) the daily
sales logging routine, answering: are they smart and accurate, and is the data in the
best format possible? Companion to `SALES_IMPORT_PARSER_ANALYSIS.md`, which maps the
import parser in detail — findings from there are referenced, not re-derived.

**Method:** same as the first report. The sync's per-row matching loop
(`products.py:734-767`) was replicated verbatim and run against the synthetic catalogue;
frontend behaviour read from `Products/index.tsx`. Scores are illustrative (synthetic
catalogue), structural behaviour is verified. Anything depending on the real sheet or
production DB is marked **[unverified]**.

---

## Part 1 — Product sync from sheet

### What it actually is

Admin-only, manually triggered (`POST /api/products/sync-sheet`, `products.py:694`;
button in `Products/index.tsx:152`). It:

1. Reads columns A:D of one hard-coded Google Sheet
   (`_SHEET_ID` at `products.py:625`) via a service account
   (`GOOGLE_SERVICE_ACCOUNT_PATH`, `products.py:628-640`).
2. Per row, takes col B as the desired 记账名, falling back to col C
   (`products.py:735-739`).
3. Fuzzy-matches each row against the catalogue:
   `match_jzm(jzm_sheet, all_products, threshold=50, limit=1)` (`products.py:741`).
   Score ≥ 80 → `changed` (or `unchanged` if the string is identical); 50–79 → `review`;
   < 50 → `not_found` (informational only).
4. Also runs a duplicate scan over the catalogue (`_run_duplicate_scan`,
   `products.py:643` — O(n²) pairwise at ≤500 products, per-product `match_jzm` above).
5. Shows a preview modal; on confirm, updates **only** `products.jizhanming` (+ rebuilt
   `search_blob`) for the checked rows (`products.py:778-834`), stamps
   `last_sheet_sync_at/count` in `app_settings`, and invalidates the ranker cache.

So despite the name, it is not a product sync: it is a **one-directional,
single-field rename tool**. It cannot create products (`not_found` has no action path),
cannot deactivate them, and ignores price/series/type entirely — it fetches A:D but reads
only B and C.

### What's genuinely smart

- Preview-then-confirm; nothing writes without an admin looking at a diff.
- The pivot away from SKU joins was right — per commit `150ffcf`, POPCORE SKUs are
  internally generated and don't correspond to the sheet's 编号, so a key join was
  matching garbage.
- The duplicate scan piggybacking on the same pass is a nice touch, with a sensible
  O(n²) cutoff at 500 products.
- Review rows (50–79) default **unchecked** (`index.tsx:159`) — low-confidence matches
  need an explicit opt-in.
- Audit stamp (`last_sheet_sync_at`) and ranker invalidation after renames are both
  correct hygiene.

### Where it is not accurate

Verified by replicating the exact per-row loop:

| # | Scenario | Result | Why it's wrong |
|---|---|---|---|
| 1 | Sheet contains a **new** product near an existing one (`Smiski Cheer 2`, not in DB) | scored 88 → **`changed`, pre-checked** | `limit=1` + `≥80 → changed` + `setCheckedChangedKeys(data.changed.map(…))` (`index.tsx:158`) means one un-vigilant click of Confirm **renames the wrong existing product** — and the new product still doesn't exist afterwards. This is the central correctness trap: without a stable key, high similarity is treated as identity. |
| 2 | Sheet renames a product by **adding CJK** (`Smiski Bed二代`) | **`not_found`** | The CJK-coverage penalty (`matcher.py:157-164`) zeroes the score when query CJK chars are absent from the candidate. The one job a rename-sync exists for — following real renames — fails precisely when the rename adds Chinese characters, and the row dies in the informational `not_found` list. |
| 3 | Sheet differs only in case/spacing/plural (`SMISKI  bed`, `Smiski Hippers`) | score 100 → **`changed`, pre-checked** | `old_jzm != jzm_sheet` at `products.py:750` is a **raw string compare** after a *normalized* match, so cosmetic differences churn as updates. Confirming rewrites clean catalogue names to the sheet's formatting (`'Smiski Bed'` → `'SMISKI  bed'`, double space included). Inverse asymmetry of #2: cosmetic changes sync eagerly, real renames can't. |
| 4 | **Two sheet rows hit the same product** (`Smiski Hippers` + `smiski hipper` → same pid, both 100) | both in `changed`, both pre-checked | No collision detection anywhere (`products.py:729-767` tracks nothing per pid); confirm applies sequentially (`products.py:791`), **last row silently wins**. The `changed` table is also keyed by `sheet_jizhanming` (`index.tsx`), so two identical B-values collide as one row in the UI. |
| 5 | Sheets API token missing/expired, or non-2xx response | empty result → **"所有记账名已是最新 / All up to date"** | `products.py:712-726` returns empty `changed/review/not_found` on failure, and the modal's only empty-state is the green all-up-to-date message (`index.tsx:517-523`). **A broken credential is indistinguishable from a clean sync.** [unverified how often this fires in production — but the code path is unambiguous] |
| 6 | Col B empty → col C (full product name) used as the 记账名 | usually `not_found` (length penalty), but a short C **would be written into `jizhanming`** | Fallback at `products.py:739` conflates "display name" with "bookkeeping name" — pollutes the very field the sales matcher depends on. |

Two more structural problems:

- **Three competing sources of truth, last writer wins.** `jizhanming` is written by
  (a) `init_db.py` from the Excel masters — and `refresh_db.bat` re-runs it, whose
  `ON CONFLICT(sku) DO UPDATE SET jizhanming = excluded.jizhanming`
  (`init_db.py:196-208`) **clobbers every sheet-synced rename**; (b) this sheet sync;
  (c) manual UI edits. No history, no timestamps per field, no conflict detection
  between the three.
- **The sync learns nothing.** Unlike the sales import — which records
  `match_corrections` and grows `product_aliases` from every manual fix — a corrected
  sync match is forgotten immediately. Next sync re-derives identity fuzzily from
  scratch and will make the same mistake again.

### Verdict & the format question

**Smart in UX shape, not smart in its identity model, and one bad default away from
corrupting the catalogue.** The accuracy of the whole feature is bounded by fuzzy name
matching because there is no stable join key — and that is fixable without touching the
sheet's workflow: on first confirmed match, store the sheet's 编号 (col A is already
fetched and discarded) as e.g. `products.sheet_ref`. Identity then becomes exact and
learned-once; fuzzy matching remains only for never-seen rows. Alongside that, the
minimum safe changes would be: distinguish "sheet unreachable" from "no changes" in the
API response; compare normalized (not raw) strings before emitting cosmetic `changed`
rows; detect multi-row → same-pid collisions in the preview; and default `changed` rows
**unchecked** (or at least uncheck sub-95 scores). Given finding #2, expect the real
value of the current implementation to be low: the renames it can follow are mostly the
cosmetic ones that don't matter. **[unverified against the real sheet — worth running
one preview and counting the buckets before investing further.]**

---

## Part 2 — Sales logging

### What's genuinely smart

Credit where due — the design has real learning loops that most small internal tools
lack:

- The five-layer parse with a three-bucket triage (auto ≥80 / review 50–79 / failed) is
  the right human-in-the-loop shape, and thresholds sit in named constants.
- Manual picks in Review/Failed **write back aliases** (`DailyReportEntry.tsx:242,247`),
  and corrected matches feed `match_corrections` → the sklearn re-ranker
  (`ranker.py`), which stays safely inert below 30 samples and fails open. The system
  genuinely gets better with use.
- Unknown section headers are learned once into `section_aliases` and reused.
- Writes are atomic per report, keyed `UNIQUE(product_id, date, store)`, with a
  manager-only `clear_day` escape hatch and a recorded-dates calendar.

### Accuracy

Covered in depth in `SALES_IMPORT_PARSER_ANALYSIS.md` — summarising the standing
answer: **accurate for the happy path, with silent failure modes at the edges.** The
open items that bear directly on "is the data accurate": unrecognised colon-less headers
leak following lines into the previous (sales) section; `stock_out` items are counted in
the UI then written nowhere; inline `header：product` lines are destroyed; re-import
**accumulates** rather than replaces, so any retry double-counts a day; and the UI
reports the item count it *sent*, not what the backend wrote.

One addition from this review, and it is the biggest accuracy issue outside the parser:

**Revenue is computed against the live price, everywhere.** `daily_sales` stores
quantities only — no unit price at time of sale. Every revenue figure in the product
joins `products.price` as of *today*:

- Sales page total: `sales.reduce((s, r) => s + (r.price ?? 0) * r.qty_sold, 0)`
  (`Sales/index.tsx:204`, per-row at `:274,:646`)
- Day detail: `DayDetail.tsx:124`
- Dashboard: `Dashboard/index.tsx:124`
- CSV export: joins `p.price` (`sales.py:510`)

Consequences: any price change silently **rewrites all historical revenue**; a
seasonal discount applied in `products.price` retroactively shrinks last quarter.
And because every read path is an `INNER JOIN products` (`sales.py:58,514`), deleting a
product makes its entire sales history **vanish** from every view and export while the
rows still sit in the table.

### Is the format the best possible?

The **grain is right** — one row per (product, date, store) matches the input, which is
an end-of-day tally, not a transaction stream. Transaction-level rows would be
over-engineering here. But four columns are missing or misplaced, and each is a real
reporting loss:

1. **No `unit_price` snapshot** (above). One REAL column written at submit time fixes
   revenue history permanently.
2. **The sales-channel dimension collapses into a free-text note.** `claw`,
   `sell_display`, and `employee_discount` all land in `qty_pos`, distinguished only by
   a tag in `notes` — which (a) is **overwritten by any staff note** (`tag = notes`
   first, `sales.py:395-401`), and (b) merges by string-concat while quantities sum on
   conflict (`sales.py:407-415`). Verified earlier: a product sold via 卡机 *and*
   卖display the same day becomes one row whose split is unrecoverable. The CSV export
   has no channel column, so 卖display/员工折扣/娃娃机 sales are unreportable except by
   substring-searching notes. Channel columns (or a small `(sale_id, channel, qty)`
   child table) fix this; `notes` goes back to being notes.
3. **No raw-input audit on auto-confirmed rows** — only human-corrected items keep the
   pasted string (`match_corrections`). For ≥80-score auto-commits (the majority), what
   the staff actually wrote is unrecoverable, which is exactly what you want when
   auditing a suspect day or a backfill.
4. **Store keyed two ways** — `daily_sales.store` is a TEXT code while
   `stock_transactions.store_id` is a numeric FK, so any sales-vs-stock reconciliation
   must route through the `stores` table. Works, but it's a standing invitation for a
   join bug.

Minor: `qty_sold` is denormalized (`= qty_pos + qty_cash`, maintained consistently at
every write site — fine, just redundant).

### Verdict

**Sales logging:** smart architecture, good learning loops, right grain — undermined by
the parser edge cases already reported, a non-idempotent write path, and two format
gaps (price snapshot, channel column) that quietly degrade every report built on top.
Fixing those two columns plus the accumulate-vs-replace semantics would make the data
trustworthy both forward and for the planned backfill.

**Sheet sync:** the weaker of the two. Correct instincts (preview, review bucket,
duplicate scan), but fuzzy-match-as-identity with pre-checked auto-renames, a
failure mode that reports success, and an unresolved three-way fight over who owns
`jizhanming`. Learn a stable key from the sheet's own 编号 column and most of its
problems disappear.
