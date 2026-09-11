# Build 4: sales, payments, and closing implementation plan

> **For agentic workers:** Use superpowers:executing-plans task by task only when implementation is requested. Read AGENTS.md, the store design, the verified Build 3 result, and this plan first. This document does not authorize subagents, commits, pushes, production-data access, or deployment.

**Goal:** Record each sale and its actual tenders once, attach private payment evidence, and close a store day with explainable stock and cash totals.

**Architecture:** Add immutable sale/payment facts and closing snapshots to the existing Flask/SQLite application. Reuse Build 3's transaction composition, successful request records, deliveries, and versioned counts. Sales reference inventory documents; Closing reads those outcomes and never repeats their movements.

**Tech Stack:** Existing Python 3.13, Flask, SQLite WAL, Pillow, React 18, TypeScript, Ant Design, Auth0, unittest, Node test runner, and isolated Playwright harness. Use integer cents and explicit unknown values.

**Spec:** [Store design](../specs/2026-09-07-instore-website-design.md), sections 6C–F, 7–8; [master roadmap](2026-09-07-instore-website-plan.md), Phase D / Tasks 7–8; prerequisite [Build 3](2026-09-08-build-3-goods-handling.md).

**Status:** Proposed plan, based on source inspected 2026-09-08 and Build 3's proposed contracts. Build 3 must actually pass its exit gate before implementation. No sale, payment, closing code, or live data was changed during planning.

## Scope and boundaries

Build 4 has two sequential checkpoints within one build: 4A, manual sales/tenders/evidence; then 4B, guided closing. Closing depends on actual 4A documents and Build 3 goods/count outcomes. Do not implement them as independent dashboards backed by invented sample totals.

Include staff sale entry, all five payment methods, source reconciliation, explicit stock allocation, monetary corrections separate from physical returns, authenticated phone evidence upload, and store-day cash/stock review.

Clover remains the physical POS. The application records actual completed/manual transactions; it does not process payments, apply a new discount/tax policy, or replace Clover checkout. Connectors, automatic refunds, trades, purchasing, margin reports, accounting automation, and broad navigation redesign remain outside this build.

Role-specific Today, including My Shifts first for staff, stays in roadmap Task 10. Build 4 adds only the Sales/Closing links and permissions needed to use these workflows. Schedule remains unchanged.

## Global Constraints

- Work in D:/dev/POPCORE. Keep environments, dependencies, test databases, evidence, caches, and build output on D:.
- Preserve setup changes, source IDs, real data, existing environments, and committed production assets.
- No live service mutation, droplet data download, deployment, or external channel stock update during local implementation.
- Use synthetic data and blocked external network calls for mutation tests. Set DISABLE_SCHEDULER before app import; app import otherwise runs migrations/jobs.
- Never infer cash revenue or individual transactions from legacy qty_cash, qty_pos, or current catalog prices.
- Keep cash, card, e-transfer, WeChat Pay, and Alipay separate. A screenshot is supporting evidence, not proof that funds settled.
- Every new API enforces verified role plus explicit operations-store scope; check raw responses, not just hidden frontend controls.
- No client-controlled authorization bypass or automatic cross-store access. Admin also needs explicit scope.
- Posted financial facts and closed snapshots are retained. Corrections append reason/actor/time and links; do not delete or rewrite prior truth.
- Check weekly usage before execution, between tasks, and before the final verification batch. At 40% consumed / 60% remaining, stop starting work and report the completed/pending checkpoint. Do not redeem a reset.
- No commit, push, deployment, live financial action, or production attachment access without separate authorization.

## Entry gate and source facts

Build 3's goods/transaction/concurrency/migration/browser evidence must pass before 4A. Read the actual implementations of _post_inventory_in_transaction and operation_requests; resolve any interface drift in this plan before coding rather than invent a second posting mechanism.

Current pre-Build-3 source has daily_sales aggregates, a staff-capable report parser, and a manager-only Sales page route/navigation. It does not have immutable individual sale/payment documents or guided closing. Keep historical aggregates explicitly labeled Legacy summary. Broadly changing /sales from manager to staff while retaining its management queries would expose data; build a restricted staff entry route instead.

The source database uses a global inventory_mode plus per-location verification. Only synthetic verified scopes run the new allocating workflows during development. Do not quietly activate real stores to make Sales or Closing usable.

Capture fresh branch/status/remote and pre-existing diffs. Preserve all uncommitted prerequisite work and the pre-existing staged server.log deletion. Keep a Schedule/static baseline for the final regression check.

## File map

Paths are relative to D:/dev/POPCORE. All new names are proposed.

| File | Responsibility |
| --- | --- |
| popcore_app/sales_operations.py (new) | Sale posting, source linking, allocation retry, and explicit corrections |
| popcore_app/blueprints/sale_documents.py (new) | Restricted individual sale/intake APIs; keep legacy sales.py aggregate parser intact |
| popcore_app/blueprints/sales.py | Classify report intake and prevent old endpoints creating competing new stock/financial authority |
| popcore_app/blueprints/payments.py (new) | Payment/evidence access and recorded verification/correction actions |
| popcore_app/payment_evidence.py (new) | Private image validation, normalization, storage, and authenticated retrieval |
| popcore_app/closing_operations.py; blueprints/closing.py (new) | Cash arithmetic, source-token checks, staff submission, manager snapshot/sign-off |
| popcore_app/db.py | Additive sale/line/source/payment/evidence/cash/closing migrations |
| popcore_app/inventory_commands.py | Reuse composed posting; scoped source linkage and sale-return/provenance protection only as required |
| popcore_app/app.py; tests/support.py | Register new blueprints and isolated evidence storage |
| frontend/src/api/salesDocuments.ts, closing.ts (new) | Typed, scoped requests using the existing authenticated client |
| frontend/src/pages/Sales/Entry.tsx, SaleDocument.tsx, PaymentEvidence.tsx (new) | Staff-safe intake, document view, and authenticated phone capture |
| frontend/src/pages/Sales/index.tsx, DailyReportEntry.tsx, DayDetail.tsx | Manager history plus explicit legacy/reconciliation views |
| frontend/src/pages/Closing/index.tsx (new) | Step-based closing workspace with role-specific field projection |
| frontend/src/App.tsx, components/AppLayout.tsx | Separate staff entry and Closing links; retain manager report guard and Schedule |
| tests/test_sales_posting.py, test_sales_reconciliation.py, test_payments.py, test_payment_evidence.py, test_closing.py (new) | Financial/stock/permission/concurrency invariants |
| tests/browser/check_store_day.py (new); .github/workflows/checks.yml | Full-day browser proof and CI gate |
| docs/sales-and-closing.md (new), docs/inventory-core.md, docs/goods-handling.md | Actual flows, correction/recovery procedure, and remaining pilot decisions |

Frontend/test shorthand paths start under popcore_app/. No new authentication system, task assignment service, or generic accounting module.

## Data and API contracts

### Sale and tender facts

| Structure | Required fields and constraints |
| --- | --- |
| sale_documents / sale_lines | Store, business date, creator, draft version, posted timestamp, entry_mode, immutable product/name/form/unit/quantity/price/tax snapshots, currency CAD, subtotal/tax/gross/reduction/rounding/collected cents nullable where unknown |
| sale_sources | sale_id, source_system, source_account/store, original order/reference; exact source tuple unique; original manual UUID always retained |
| sale_allocations | Sale/line mapping to product/location/provenance and inventory document; pending/resolved outcome and reason; no duplicate line allocation |
| sale_payments | Sale, tender, actual amount cents, source identity, recorded_by; independent recorded/verified/rejected state history; split-tender components do not duplicate the sale |
| payment_events | Append-only verification and actual recorded correction/refund events, actor/reason/time; amount direction explicit; no processor refund action |
| payment_evidence | Payment FK, private object ID, MIME/size, uploader, created time, pending/accepted/rejected status history; replacement appends an attachment |
| sale_reconciliations | Reviewed source-summary/link decisions with actor/reason, original report/reference, classified intent, and linked existing sale/movement identities |

Products and financial snapshots freeze when posted. Keep allocation/settlement/evidence states separate so a paid sale can remain pending allocation, or a verified payment can lack evidence. Quantities remain positive native-unit integers; cents are bounded integers, never booleans, floating-point accumulation, or negative quantities masquerading as a refund.

Use null for unknown monetary values. Where all components are known:

~~~text
gross_cents = subtotal_cents + source_tax_cents
collected_cents = gross_cents - reduction_cents + rounding_cents
payment_total_cents = sum(actual tender component amounts)
payment_difference_cents = payment_total_cents - collected_cents
~~~

Rounding is an explicit signed adjustment; reduction is nonnegative. A nonzero payment difference is visible, never auto-balanced. Stored source amounts need not be fabricated to fill the equation. Record actual supplied tax; do not calculate tax or change processor totals without a separately approved policy.

Owner examples remain actual recorded outcomes:

~~~python
self.assertEqual(4134 - 134, 4000)
self.assertEqual(712 - 12, 700)
self.assertEqual(9914 - 214, 9700)
~~~

These assertions test exact cents capture, not a discount algorithm. Keep card payment discounts at zero under the stated store rule; record other actual amount corrections explicitly rather than pretending they are payment discounts.

### Transaction interface and allocation outcome

sales_operations.post_sale(con, payload, *, actor, request_key) -> dict owns one short write transaction and reuses Build 3 operation_requests plus the private inventory transaction body. It returns sale_id, financial_status, allocation_status, inventory_document_id (nullable), and unresolved reasons. Add source links and snapshots in that same transaction.

entry_mode is planned_entry or already_paid. Planned entries with an inventory conflict retain the draft and commit no sale/movement. already_paid explicitly records an externally completed sale, its source and actual supplied tenders even if product mapping or stock allocation is unresolved.

~~~python
# Contract for already-paid allocation, inside the sale transaction:
con.execute("SAVEPOINT sale_allocation")
try:
    # Validate all mapped lines and post through the inventory engine.
    allocation = _post_inventory_in_transaction(
        con, allocation_payload, actor=actor, request_key=allocation_key
    )
    con.execute("RELEASE sale_allocation")
except InventoryConflict as exc:
    if exc.code not in pending_allocation_codes:
        raise
    con.execute("ROLLBACK TO sale_allocation")
    con.execute("RELEASE sale_allocation")
    # Persist the valid financial facts and pending allocation reason.
# Unexpected exceptions still roll back the whole request and report failure.
~~~

The snippet defines transaction ordering. allocation_payload is the reviewed mapped consume command, allocation_key is derived from the sale identity and allocation attempt, and pending_allocation_codes is an explicit set of the existing inventory conflict codes for shortage, stale balance, mapping/unverified scope and fresh-set conflicts, verified against the engine at implementation time. Validate unresolved product mappings before this block and persist their pending reason without calling the engine. Validation errors in the financial payload still reject it. Never catch every exception and report a saved sale.

For the initial build, allocate all sale lines atomically or leave all pending. Do not introduce partial line allocation complexity. An authorized manager resolves mappings and retries allocation using current reviewed versions; successful resolution links the original sale and posts once, even after restart. Unknown-source product text remains an unresolved sale line; never invent a product to force it through.

A manually recorded receipt later matched to another source adds a unique source link to the existing sale. Same request key replays; distinct identical real sales remain distinct. A newly supplied duplicate source reference conflicts for explicit reconciliation rather than silently returning another person's sale.

### Closing and cash facts

| Structure | Fields/invariants |
| --- | --- |
| closing_sessions | Unique store/business_date, version, draft/submitted/closed state, creator, submitted/reviewed actors, source token |
| closing_cash_counts | Append-only count revisions, bill and coin denomination counts, explicit opening/retained coin amounts, expected/observed totals, cashier actor/time |
| cash_events | Positive amount plus paid-in/payout/refund/removal event type, authorization actor/reason, business date, source/payment link; immutable |
| closing_snapshots / closing_adjustments | Frozen reviewed JSON/source IDs, actual tender totals/unknowns, count/restock/evidence exceptions, manager decision; later dated linked adjustments never replace the snapshot |

Use America/Toronto business dates for DT/MK; store UTC posting timestamps and explicit local business date separately. Reuse installed date/time support, verify Windows zone data and DST in tests, and add only a direct timezone-data dependency if the environment actually lacks it. Do not touch Schedule's date logic.

~~~text
opening_cash = 65000 bills + explicitly recorded opening coins
expected_drawer = opening_cash + verified cash receipts + paid-ins
                  - authorized cash refunds - payouts - prior removals
cash_variance = counted_drawer - expected_drawer
retained_next_opening_cash = 65000 bills + explicitly recorded retained coins
cash_removed = counted_drawer - retained_next_opening_cash
~~~

The $650 values are opening/retained bill float, not revenue. Do not assume opening or retained coins are zero. If counted cash cannot support the retained float, show the shortfall; do not invent a negative removal. Final close removal is recorded once and excluded from its own pre-removal drawer comparison. Repeated sign-off cannot create another removal.

One consistent source token covers the closing session version, included sales/payment/evidence/cash event IDs and states, delivery/count versions, and current versions of balances used by final counts. Build it from ordered canonical source data and recompute inside BEGIN IMMEDIATE during submit/sign-off. No lock remains open while staff counts. Any relevant change returns closing_stale; an unrelated store's activity does not invalidate the close.

Closed snapshots are immutable. Late paid facts are retained with an explicit late adjustment/exception linked to the original store day; never silently added to its frozen totals. Queries show original close and subsequent corrections separately.

### Staff and management visibility

| Action/data | Access |
| --- | --- |
| Enter sale/tender; see own sale documents and upload own evidence | Staff with explicit store scope; manager/admin within their scopes |
| Resolve duplicate/source mappings, pending allocations, financial corrections | Manager/admin with affected store scope and reason |
| Verify/reject evidence and payments; see other staff's private evidence | Manager/admin within that store |
| Store financial totals, exports and close approval | Manager/admin within explicitly authorized stores |
| Participate in restock/count/cash closing tasks | Scoped staff; return only task-required information, never other staff's evidence or management reports |
| Existing legacy sales reports | Retain current manager UI guard; new financial records are never returned through unscoped old aggregate endpoints |
| Viewer | No sale/payment/evidence/closing write access or new financial totals |

No new Auth0 role or custom widget system. Scope every document lookup and file download on the server. Staff cash tasks can see the numbers needed to perform the count/reconciliation; this does not grant management sales reporting. Clear previous account/store data on identity/scope changes.

## Task 1 (4A): immutable manual sale and one inventory allocation

**Files:** db.py, sales_operations.py, blueprints/sale_documents.py, app.py, tests/support.py; Sales/Entry.tsx, SaleDocument.tsx, api/salesDocuments.ts, App.tsx, AppLayout.tsx; tests/test_sales_posting.py.

**Interfaces:** POST /api/sale-documents creates/saves a draft with request key; GET/PATCH /api/sale-documents/<id> uses expected_version; POST /<id>/post records one sale; POST /<id>/allocate is the manager resolution action. Staff entry route: /sales/entry. Keep /sales management reporting guarded.

- [ ] Write regressions for known sale+stock posting, planned-entry shortage rollback, already-paid shortage retained without movement, mixed-validity multi-line rollback, duplicate source, same-key lost response, and cross-store denial.
- [ ] Add the schemas and transaction flow above. Preserve existing product/history IDs and leave daily_sales aggregates intact.
- [ ] Freeze actual sale-line and money snapshots at posting. Changing catalog name/price afterward cannot change the sale.
- [ ] For already-paid unknown product or fresh-set allocation conflict, retain raw source details and valid money facts, show pending allocation, and let a manager explicitly resolve them.
- [ ] Add explicit fresh-set selection for random-box sales exceeding half a set (7 of 12, 5 of 9). Reuse reviewed conversion/provenance; opening and consuming the selected source must commit together. Account for leftovers; mixed stock makes no same-set promise.
- [ ] Distinguish draft, saved sale, paid status and allocated stock in the UI. A financial save with pending allocation must not show stock-posted success.
- [ ] Keep the request key and draft on timeout; recover by explicit retry or document lookup, never automatic repeated stock submission.
- [ ] Test retries after allocation resolution so the original stored pending response leads to the current document view without a second posting.

~~~text
Floor 5; planned sale 6 -> conflict; floor 5; no posted sale.
Floor 5; externally paid sale 6 -> financial record saved, allocation pending, floor 5.
Later receive enough stock and resolve -> one allocation linked to that sale.
Two genuinely separate identical receipts -> two sale IDs, not one fuzzy deduplication.
~~~

Run: .\.venv\Scripts\python.exe -m unittest discover -s popcore_app/tests -p "test_sales_posting.py" -v

**Gate:** A complete sale is recorded once, and missing stock cannot erase an actual paid transaction.

## Task 2 (4A): tenders and explicit source reconciliation

**Files:** sales_operations.py, blueprints/sale_documents.py, blueprints/payments.py, blueprints/sales.py, db.py; Sales/Entry.tsx, SaleDocument.tsx, DailyReportEntry.tsx, DayDetail.tsx; tests/test_payments.py, test_sales_reconciliation.py.

**Interfaces:** POST /api/sale-documents/<id>/payments; POST /api/payments/<id>/verify or /reject; POST /api/sale-documents/<id>/source-links; POST /api/sale-reconciliations. Mutations use Idempotency-Key and expected document version.

- [ ] Write exact cents/split-tender/unknown-tax/price-snapshot tests, and prove card/e-transfer/WeChat/Alipay never enter physical cash calculations.
- [ ] Record five separate tender values and actual adjustments/reasons. Do not reclassify historical qty_cash as cash payments or silently default unknown amounts to zero.
- [ ] Keep money correction/refund recording manager-only, append-only, and linked to original payment. Bound recorded refunds by remaining recorded collected amount; uncertain legacy amounts require review, not a guessed maximum.
- [ ] Record a physical return separately through a reviewed inventory document with product/unit/condition and original allocation link; bound total returned quantity by allocated quantity minus prior returns. A refund does not return stock, and a return does not prove refund.
- [ ] Keep parser preview read-only. Before any 汇总 commit, require missing transactions, reconciliation summary, or stock already posted classification.
- [ ] Missing transactions require an explicit stable import document and reviewed transaction/line grouping. Where original individual receipts are not known, keep the data as an unresolved/legacy summary; do not manufacture individual tenders or blindly deduct the batch.
- [ ] A reconciliation summary compares known totals without posting sales/stock. Already-posted stock requires an explicit reviewed movement link proving product/unit/quantity/source coverage; it must not consume again or link one movement to competing sales.
- [ ] Link a later source reference to the existing manual sale only through exact identity or manager-reviewed matching. Identical product/day/amount or pasted text is not sufficient identity.
- [ ] Re-test old upsert, batch, submit/replace, clear-day, row-delete and export paths: none can mutate new immutable documents, undo linked allocations, bypass financial access, or double-count legacy summaries in new totals.

~~~text
One sale collected $100: cash $40 + card $60 -> revenue $100, cash receipts $40.
Link another source reference to that sale -> still one sale/stock deduction.
Legacy qty_cash=7 with no actual tender amount -> cash amount unknown, never 7*current_price.
41.34->40, 7.12->7, 99.14->97 persist exactly; no automatic formula.
~~~

Run each named test module through unittest discovery.

**Gate:** All five tenders and source comparisons reconcile without duplicated revenue, guessed tax, or duplicated inventory effects.

## Task 3 (4A): protected phone evidence capture

**Files:** payment_evidence.py, blueprints/payments.py, db.py, app.py, tests/support.py, telemetry.py only if required for exclusion; Sales/PaymentEvidence.tsx, SaleDocument.tsx; tests/test_payment_evidence.py.

**Interfaces:** POST /api/payments/<id>/evidence multipart upload; GET /api/payment-evidence/<id>/content authenticated download; POST /api/payment-evidence/<id>/review manager decision. Phone route /sales/payments/<id>/evidence requires normal Auth0 login and server-side payment/store/ownership checks.

- [ ] Write fixtures for uploader, another staff member, scoped manager, other-store manager, viewer, and unauthenticated requests, including guessed IDs and direct static/public routes.
- [ ] Accept JPEG/PNG/WebP still images only; use the existing 10 MiB input / 40 million pixel product-image ceilings and Pillow decoding pattern. Reject corrupt, animated, oversized, SVG/HTML/PDF input; enforce the normalized output byte ceiling too.
- [ ] Strip EXIF and unnecessary metadata by re-encoding. Generate opaque server file IDs; never trust an uploaded path or original filename.
- [ ] Keep files below D:/dev/POPCORE/popcore_app/uploads/payment_evidence locally, excluded by .gitignore and outside public static/hidden image serving.
- [ ] Decode/stage outside the DB write transaction. Publish an immutable private file before committing its metadata reference; on failure remove only this upload's unreferenced file. A crash may leave a private orphan, never a successful row pointing to an absent file.
- [ ] Record upload replay so retry returns the prior attachment; do not deduplicate evidence between unrelated payments or expose an existing file through a content hash.
- [ ] Send authenticated responses with private, no-store cache control and nosniff. Exclude images/credentials/payment payloads from telemetry and LLM parsing.
- [ ] Keep evidence pending/accepted/rejected independent of payment recorded/verified/rejected. Replacing rejected evidence preserves the older attachment/history.
- [ ] Provide the authenticated mobile upload page and retry states. Use the regular login flow; no public QR bearer link, cross-device token service, or automatic evidence verification.
- [ ] Document backup ordering and private-orphan recovery. No automatic retention purge or deletion policy is introduced.

~~~text
Valid screenshot + unverified payment -> evidence pending, payment still unverified.
Another staff member or other-store manager -> no image bytes or private metadata.
Rejected upload / failed metadata commit -> no published success.
Duplicate network retry -> same attachment identity.
~~~

Run: .\.venv\Scripts\python.exe -m unittest discover -s popcore_app/tests -p "test_payment_evidence.py" -v

**4A gate:** Sale intake, payment/source reconciliation, private phone evidence and permissions pass before Closing is built.

## Task 4 (4B): exact cash count and one closing session

**Files:** closing_operations.py, blueprints/closing.py, db.py, app.py, tests/support.py; Closing/index.tsx, api/closing.ts; tests/test_closing.py.

**Interfaces:** POST /api/closing creates/returns the one store-date session; GET/PATCH /api/closing/<id>; POST /<id>/cash-counts and /cash-events. Return expected_version/source_token and a role-filtered task DTO.

- [ ] Write the cash fixture below using integer cents before implementing forms. Include explicit missing coins, unknown cash amounts, split tender, payout/refund/removal, float shortfall, and duplicate event cases.
- [ ] Add unique store-date session and append-only cash event/count revisions. Collect explicit bill/coin denomination counts; zero count is valid, omitted opening/retained coins remain incomplete.
- [ ] Use $650 opening/retained bills and explicit actual coins. A mismatch or insufficient float stays visible for manager review; no silent float correction.
- [ ] Include only verified physical cash receipts in expected drawer; show unverified/unknown cash separately as closing exceptions.
- [ ] Expose cashier task numbers to authorized staff, excluding management-only sales totals and others' private evidence. Verify the raw API response and direct endpoint permissions.
- [ ] Treat cash removals/payouts/refunds as distinct actual recorded events with reason/authority. Do not describe closing removal as sales revenue.
- [ ] Implement draft save/version/retry behavior so two cashiers cannot overwrite each other's latest count.

~~~python
opening = 65000 + 2000
expected = opening + 30000 - 3000
counted = 93850
retained = 65000 + 2000
self.assertEqual(expected, 94000)
self.assertEqual(counted - expected, -150)
self.assertEqual(counted - retained, 26850)
~~~

**Gate:** The drawer calculation reproduces expected $940, counted $938.50, shortage $1.50, and removal $268.50; non-cash payments change none of those drawer amounts.

## Task 5 (4B): connected close, review, and late corrections

**Files:** closing_operations.py, blueprints/closing.py; Closing/index.tsx; Build 3 count/restock components via existing APIs; tests/test_closing.py.

**Interfaces:** POST /api/closing/<id>/submit, /return, /close, /adjustments with request key, expected_version and source_token. Manager returns submitted work to draft with a reason; closed snapshots never return to mutable draft.

- [ ] Write cases for partial outstanding restock, final count stale after a sale/receipt, evidence changed during review, simultaneous close, duplicate close/removal, and late paid activity after close.
- [ ] Build Sales completeness → Restock/receipt → Cash → Hot-item counts → Review steps. Restock and cash can run in parallel; final counts follow final sales/floor receipt.
- [ ] Read posted Build 3 deliveries/counts and 4A sale/payment documents. Closing must not call receipt/consume/restock operations merely to aggregate or mark a checklist complete.
- [ ] Require an explicit staff declaration of intake completeness before submission; the application cannot infer it from absent imports while Clover is unconnected.
- [ ] Use the consistent source token at submission and final review; changed source facts produce closing_stale with refresh/review, and changed counted balances require recount of affected items.
- [ ] Keep unresolved allocation, unresolved transit, missing required counts/coin input, and invalid cash arithmetic as hard blockers. Financial/evidence discrepancies may only be accepted as named manager exceptions with reason; no automatic tolerance threshold.
- [ ] Record reviewer, accepted exception list, full source identities and frozen summary. Manager review is always required in development; monetary thresholds/coin routine and which exceptions are allowed need owner approval before a real closing pilot.
- [ ] Insert the final counted-cash removal exactly once in the same closing transaction, using counted-before-removal minus retained cash. Preserve its link and prevent counting it twice on retry.
- [ ] Retain late externally paid sales/payment corrections after close with explicit dated links; show original snapshot plus later adjustments. Do not silently backfill a closed total or delete its signature.
- [ ] Add the minimal Closing link in the existing desktop menu and mobile More. Do not bring forward Today widgets or Schedule changes.

~~~text
Submit at source token A; a floor receipt changes the counted balance.
Close with A -> 409 closing_stale, no signature/removal.
Refresh and recount, resubmit, manager reviews -> one closed snapshot.
Retry close -> same snapshot and one cash-removal event.
Later paid sale for that date -> retained linked exception, original close unchanged.
~~~

**Gate:** A manager can explain every close figure and exception from its source documents; stale/duplicate/late actions cannot rewrite the day.

## Task 6: full store-day proof and local handoff

**Files:** tests/browser/check_store_day.py, test_closing.py, migration fixtures, .github/workflows/checks.yml, docs/sales-and-closing.md, existing goods/inventory docs.

- [ ] Add migrations with real-shaped synthetic legacy aggregates, Build 2 movements, Build 3 unfinished transfers/counts, and unknown tender/cost data. Apply twice without relabeling old money or damaging references.
- [ ] Rehearse a complete synthetic day: receipt → set opening → sale with explicit provenance → all five tenders including a split payment → private evidence → source reconciliation → partial restock resolution → final hot-item count → manager correction → cash close.
- [ ] Assert sale/payment totals, movement-derived balances, transit ownership, allocation references, evidence access and the concrete cash fixture. Include deliberate unknown and stale cases, not only the happy path.
- [ ] Use the isolated browser harness with real local endpoints and test identities. Verify staff entry/own evidence, manager review, denied stores, failed upload/save, lost response, account switching, 390/768/1440px, keyboard use, and 200% zoom.
- [ ] Rehearse SQLite backup/restore plus a manifest of referenced private attachments using synthetic files. Validate all referenced files after restore; reconcile private orphan files without touching real evidence.
- [ ] Add the full store-day browser check to CI alongside foundation/inventory/goods checks. Keep Schedule tests and baseline hashes.
- [ ] Run the full commands below; record exit results and limitations in the handoff. Do not call a local synthetic run droplet readiness or a production pilot.

~~~powershell
Set-Location D:\dev\POPCORE
$env:TEMP = 'D:\dev\POPCORE\.local\tmp'
$env:TMP = $env:TEMP
$env:PLAYWRIGHT_BROWSERS_PATH = 'D:\dev\POPCORE\.local\ms-playwright'
$env:DISABLE_SCHEDULER = '1'
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m unittest discover -s popcore_app/tests -v
.\.venv\Scripts\python.exe popcore_app/tests/browser/check_foundation.py
.\.venv\Scripts\python.exe popcore_app/tests/browser/check_inventory_core.py
.\.venv\Scripts\python.exe popcore_app/tests/browser/check_goods_flow.py
.\.venv\Scripts\python.exe popcore_app/tests/browser/check_store_day.py
Set-Location popcore_app\frontend
npm test
npm run build -- --outDir ../../.local/frontend-build-build4
Set-Location ..\..
git diff --check
git status --short --branch
git status --short -- popcore_app/static popcore_app/frontend/src/pages/Schedule popcore_app/blueprints/schedule.py
~~~

**Build 4 exit gate:** A complete synthetic store day reconciles inventory, actual tenders, private evidence, and the $650 bill-float close; source reconciliation never duplicates stock; uncertain/late financial truth is retained; role/store protection and concurrency are proven; Schedule and release assets remain at their baseline.

Trades, role-specific Today with staff shifts first, broader operational reports/UI verification, and release/pilot preparation remain Phase E. Live opening/access approval, the coin routine/exception policy, attachment retention/backup destination, and later connector policy decisions remain explicit launch gates.
