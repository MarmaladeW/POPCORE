# POPCORE in-store website implementation plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task by task after the user approves implementation. Use the design's workflow and data rules as acceptance criteria. No subagent execution, commit, push, or deployment is authorized by this planning document.

**Goal:** Turn the existing internal application into a connected inventory, sales, evidence, restocking, and closing tool, then connect its verified inventory to sales channels.

**Architecture:** Retain the Flask/SQLite and React/Auth0 application. Introduce one transactional inventory-command module with source documents and append-only movements; migrate existing writers to it before claiming inventory authority. Build connected user workflows on that foundation.

**Tech stack:** Existing Flask, SQLite WAL, Python 3.13 development venv, React 18, TypeScript, Vite, Ant Design, Tailwind, Zustand, Auth0. Reuse unittest and Node tests; use an explicit browser test harness for UI checks.

**Spec:** [In-store website product design](../specs/2026-09-07-instore-website-design.md)

**Status:** Proposed phased implementation; only planning artifacts were created.

## Global constraints

- Work in D:/dev/POPCORE. Keep environments, dependencies, test databases, evidence, caches, and build output on D:.
- Preserve setup changes, source IDs, real data, existing environments, and committed production assets.
- popcore.store is this internal application; popcore.ca is the separate WooCommerce channel. No WordPress/LocalWP work occurs in this checkout.
- Schedule stays unchanged: no redesign, shift-validation patch, calendar performance work, employee scheduling setting change, or calendar CSS change. Staff Today may display their existing assigned shifts as its primary highlight through the current personal-shifts API.
- Shared auth/API/layout/build changes must prove Schedule still works. Scope new store layouts and CSS outside Schedule.
- No live service mutation, droplet data download, deployment, or external channel stock update during local implementation.
- Use synthetic data and blocked external network calls for mutation tests. Set DISABLE_SCHEDULER before app import; app import otherwise runs migrations/jobs.
- Preserve the reviewed audit as history. Revalidate each finding against actual files/functions before implementing; the design lists corrected source locations.
- No forced dependency fixes, wholesale framework rewrites, accounting automation, or fabricated business policies.
- Defect work follows reproduce → minimal correction → relevant tests → reviewable diff. Tests belong to the workflow they protect.
- Do not build a new runtime test framework just to assert simple Python/TypeScript functions.
- File paths below are relative to D:/dev/POPCORE. New paths are proposed interfaces, not claims that they already exist.

## Delivery order and gates

| Phase / tasks | Deliverable | Gate before proceeding |
| --- | --- | --- |
| A / 1–3 | Contained defects and trustworthy local validation | Audit corruption cases fail safely; auth and error behavior reliable |
| B / 4–5 | Product/unit/location identity and inventory authority | Exact conversions, all writers mapped, transaction invariants pass |
| C / 6 | Receiving, restock, transfers, scanning, physical counts | Complete goods-flow demonstration with partial/duplicate/stale operations |
| D / 7–8 | Sales/tenders/evidence and guided closing | Full synthetic store day reconciles without double deductions |
| E / 9–11 | Trades, operational reports, UI quality, local pilot package | All first-release acceptance scenarios pass and Schedule preserved |
| F / 12–13 | Clover then WooCommerce integration | Sandbox proof, source ownership, deduplication, and required business decisions |
| G / 14 | Vendors/purchasing and wholesale | Owner-approved costs, access, pricing, and credit policies |

Phases A–E form the internal first release. F and G preserve the wider requirements from the shared chat without making external capabilities a prerequisite for local inventory development. A reliable first release is a complete store-day workflow, not merely a new dashboard.

## Detailed plans for the first two builds

The owner requested the first two builds on 2026-09-08. These expand Phase A and Phase B above, in that order:

1. [Build 1: foundation repair](2026-09-08-build-1-foundation.md) — contains Tasks 1–3: stock/sales defect containment, authentication and input safety, honest errors, dependencies, and repeatable verification.
2. [Build 2: inventory core](2026-09-08-build-2-inventory-core.md) — contains Tasks 4–5: product/unit/barcode/location identity, transactional posting, writer migration, and synthetic cutover/recovery rehearsal. Starts after Build 1 passes.

These are planning documents, not implementation or deployment authorization. They make the first two builds concrete without moving later workflows into their scope. Role-specific Today with staff's own assigned shifts first remains Task 10; Schedule itself remains unchanged. Real inventory authority still requires approved store access, physical opening counts, and the later store-day pilot.

## Detailed plans for Builds 3 and 4

The owner requested the next two build plans on 2026-09-08. They retain the existing delivery order:

3. [Build 3: goods handling](2026-09-08-build-3-goods-handling.md) — Phase C / Task 6: receiving drafts, scanning, partial restock and inter-store delivery, floor targets, and versioned count corrections. Its entry gate checks the actual Build 2 working tree and evidence.
4. [Build 4: sales, payments, and closing](2026-09-08-build-4-sales-payments-closing.md) — Phase D / Tasks 7–8: manual sale intake, five separate tenders, source reconciliation, private payment evidence, and guided closing. Deliver 4A sales/payments/evidence before 4B closing, after Build 3 passes.

These documents were checked against current source and specify future implementation; no application behavior changed during this planning step. The Build 1/2 implementation and verification are recorded in docs/development-build-1.md and docs/inventory-core.md; the original plan status headers are historical. Re-run the relevant prerequisite checks at execution time.

Schedule remains unchanged. Role-specific Today, with staff's own assigned shifts first, remains Phase E / Task 10 along with its existing permissions requirements. Trades, broader reports/interface work, and pilot/release preparation remain Phase E. Printer/device setup, live data, real openings/access approval, and deployment are separate pilot actions.

Both plans preserve the owner's stop point of 60% weekly usage remaining. Neither plan authorizes implementation, subagents, commits, pushes, or deployment by itself.

## Detailed plans for Builds 5 and 6

The owner requested these plans on 2026-09-08. They complete Phase E in order:

5. [Build 5: trades and role-specific Today](2026-09-08-build-5-trades-today.md) — Task 9 plus Today/navigation from Task 10. Deliver inspected same-series swaps, separate direct trade sales/replacement and condition cases, then role/store-filtered Today with staff's own assigned shifts first.
6. [Build 6: operational reports and first-release readiness](2026-09-08-build-6-reports-release-readiness.md) — the remaining Task 10 work and Task 11. Complete reports and required review screens, prove the real frontend/backend store day, rehearse complete recovery, and prepare a reviewable local pilot candidate.

Source review found that the existing Build 4 store-day browser test uses canned API responses and that some review actions remain API-only. Build 6 explicitly closes these evidence/interface gaps; prior test counts alone do not establish integrated first-release readiness. Recheck both findings at implementation entry.

These plans change documentation only. Schedule remains unchanged; viewing personal shifts uses its existing API. Keep all work on D:, preserve prerequisites, and stop starting work at 60% weekly usage remaining. Neither plan authorizes commits, pushes, deployment or live data access. Clover/WooCommerce and purchasing remain later phases.

## Task 1 — Baseline, containment, and durable defect tests

**Files:** existing AGENTS.md and docs/development-windows.md; popcore_app/tests/; blueprints/restock.py, sales.py; db.py. New focused tests: test_stock_integrity.py and test_sales_validation.py.

- [ ] Capture branch, HEAD, remote, status, existing diffs, and hashes of Schedule files/shared release assets. Create a codex/ branch at execution time only if it can be done without moving/discarding the current setup changes.
- [ ] Promote the useful scenarios from .local/audit/audit_probes.py and restock_probe.py into isolated unittest cases. Do not copy harness flaws or rely on real Auth0.
- [ ] Prove the tests expose the current bugs using the following exact fixture outcomes.
- [ ] Make the smallest immediate containment changes before the ledger migration: reject negative/non-integer sale quantities; reject insufficient-stock mutations rather than clamp; reject destructive deletion of completed restocks with a clear correction-required response.
- [ ] For legacy report reversal where actual applied movement cannot be reconstructed, preserve the record and return a reconciliation-required conflict. Do not guess or silently alter past quantities.
- [ ] Test every sibling path: upsert, batch upsert, report submit/replace, clear-day, row delete, stock receipt/adjustment, and completed restock deletion.
- [ ] Confirm invalid/partial requests leave all affected rows unchanged.

**Required assertions:**

```text
10 upstairs + restock 5 - consumption 5:
  delete completed session -> conflict; total remains 5; history remains.

2 in-store + requested stock-out 5:
  posting -> insufficient-stock conflict; stock remains 2.
Legacy overdrawn report:
  unsafe reversal -> reconciliation-required; no guessed increase.

qty_sold=-5, fractional, bool, nonnumeric:
  400 field error; no sale or stock change.

Batch [valid line, invalid line]:
  atomic rejection or explicitly reviewed partial draft;
  never overall success with silently skipped committed lines.
```

**Gate:** unittest defect cases pass after failing on the baseline; no normal DB or Schedule changes.

## Task 2 — Shared authentication, inputs, and error states

**Files:** popcore_app/auth.py, app.py, db.py, blueprints/products.py; frontend/src/auth/ProtectedRoute.tsx, api/client.ts, App.tsx, pages/Dashboard/index.tsx, pages/Stock/index.tsx, pages/Products/index.tsx, pages/Sales/index.tsx. Tests: test_auth.py, test_uploads.py, test_exports.py; browser scenarios for failed loads/login.

- [ ] Test real JWT verification with locally signed RSA tokens: issuer, audience, expiry, algorithm, malformed header, unknown kid, and JWKS refresh/failure. Require the configured canonical issuer.
- [ ] Move login initiation out of render into a guarded effect; ensure StrictMode does not launch duplicate redirects. Surface consent/configuration/network errors and do not send an unauthenticated fallback request after token failure.
- [ ] Show failed/stale/empty states separately on the non-scheduling pages. Preserve unsaved form data and last-known successful data. Prevent an old store's response from overwriting a newly selected store's view.
- [ ] Add explicit upload limits and decode/validate accepted product images. Verify corrupt/oversized images fail without leaving files. Pick the numeric size limit from existing assets and document it.
- [ ] Neutralize spreadsheet formulas in textual CSV fields while preserving typed numeric values and standard CSV quoting.
- [ ] Disable default PII in telemetry and redact credentials/request evidence. Keep optional LLM parsing opt-in with a documented payload boundary; rule parsing and manual entry must still work.
- [ ] Smoke-test existing Schedule login/navigation using unchanged scheduling source. Defer its page-specific failed-load treatment.

**Gate:** 401/403/error/retry distinctions work; invalid upload/export cases pass; no auth bypass and no Schedule behavior regression.

## Task 3 — Safe dependency and runtime gate

**Files:** popcore_app/frontend/package.json and package-lock.json; popcore_app/requirements.txt; requirements-dev.txt; proposed requirements production constraints; .github/workflows/checks.yml; development instructions.

- [ ] Refresh npm/pip advisory metadata at implementation time. The historical npm count represents reported vulnerable package entries; record IDs, dependency paths, actual versions, and runtime reachability.
- [ ] Apply compatible fixes first; isolate any Vite major upgrade. Verify React/Ant Design/FullCalendar compatibility rather than upgrading unrelated major versions.
- [ ] Do not simply uninstall ecdsa from a dependency that requires it. Determine actual RS256 backend use and whether a supported resolution exists; otherwise document the limited-reachability advisory exception.
- [ ] Produce separate compatible production/development constraints where Linux and Windows need different packages. Use fresh D:-local environments for reproducibility checks.
- [ ] Add minimal CI running the existing unit tests, new workflow tests, TypeScript/build, and dependency consistency. Expand the Node test command to enumerate intended non-Schedule test files while retaining all Schedule tests.
- [ ] Build into .local verification output; intentionally generated release assets belong to a later release gate.

**Gate:** resolved versions reproducible for supported targets; no untriaged reachable high/critical advisory; tests/build/Schedule smoke pass. A documented exception is visible, not called fixed.

## Task 4 — Product, packaging, barcode, and location identity

**Files:** db.py; blueprints/products.py, stores.py; frontend/src/pages/Products/ and frontend/src/api/client.ts; new barcode/location helpers as needed. Tests: test_catalog_identity.py and test_inventory_units.py.

- [ ] Add series/form/design/unit metadata while preserving existing product IDs, aliases, sheet_ref, and historical references.
- [ ] Add explicit physical locations and barcode mappings. Do not activate Midtown or infer operational authority from a seed row.
- [ ] Implement manufacturer generic code versus exact internal label resolution. A confirmed design cannot be silently resolved from the generic series code.
- [ ] Model sealed-set and random-box forms with a versioned conversion factor. Do not create a general bundle engine.
- [ ] Keep customer tray and replenishment set roles distinct; track only provenance necessary for fresh-set allocations, mixed pools, and condition/trade items.
- [ ] Keep existing Sheet sync improvements. Test conflicts, source unavailable, exact reference reuse, normalization, and aliases without recreating older fixed defects.
- [ ] Show unknown cost, stock confidence, and source mapping explicitly. Do not overwrite website product information using Clover fuzzy name matches.
- [ ] Define operations access separately from scheduling membership; document proposed staff/manager scope before its rollout.

**Acceptance examples:**

```text
Receive sealed set size 12, quantity 1 -> 1 set; equivalent boxes 12.
Open set -> sealed set -1, random boxes +12; equivalent total unchanged.
Receive Design A x3 and B x4 -> A=3, B=4; no generic +7.
Scan generic code in confirmed-item flow -> explicit resolution required.
Receive '12*1' -> preview 12 boxes, never 144.
Change pack size later -> historical receipt/conversion factor unchanged.
```

**Gate:** all identities/units resolve predictably on synthetic DT/MK data; no ambiguous automatic deductions.

## Task 5 — One inventory posting path and safe migration

**Files:** db.py; new popcore_app/inventory_commands.py; blueprints/stock.py, inventory.py, restock.py, sales.py; init_db.py and every other stock writer found by search. Tests: test_inventory_posting.py and test_inventory_migration.py.

**Interface:** an internal posting function receives a validated document, actor/store authority, expected version, and unique request key; returns committed document identity or a structured conflict. Transport/LLM/import code does not directly alter stock.

- [ ] Inventory all SQL writes to stock, stock_transactions, and stock_movements, including scripts. Keep a checklist matching each writer to migrate, disable, or preserve read-only.
- [ ] Add source-document/line, movement, and balance structures with explicit units/locations, actor, posting sequence, reversal link, and unique command identity plus payload fingerprint.
- [ ] In a short BEGIN IMMEDIATE transaction, claim the request key, validate current balances/version, conditionally update balances, append movements, update compatibility projection, and commit. Keep all external calls outside the transaction.
- [ ] The entire command owns its transaction; shared helpers must not silently commit or reconnect. Treat a timeout after commit as unknown-to-client and recover by the same key.
- [ ] Add nonnegative balance and referential constraints where the domain requires them. Exceptions awaiting allocation are documents, not fake negative saleable stock.
- [ ] Route all active operations through the module; there must be no independent legacy stock writer in authoritative mode.
- [ ] Rehearse additive migrations on empty and representative legacy fixtures. Do not transform uncertain claw quantities, provenance, or historic cash tenders by assumption.
- [ ] Compare movement-derived balances and compatibility results on synthetic workflows. Then require physical opening counts before real authority.
- [ ] Test contention with controlled real locks and no artificial barrier deadlock. Return bounded retriable busy/conflict responses, never replay committed effects automatically.

**Acceptance examples:**

```text
Same key + same payload, including retry after lost response -> same result; one posting.
Same key + changed payload -> conflict; unchanged database.
Two requests each consume 4 from available 5 -> one success, one shortage; final 1.
Failed line in multi-line document -> zero committed movements/balance effects.
Receipt/transfer/conversion/correction -> sum of movements explains every balance.
Legacy migration applied twice -> same rows, stable IDs, integrity checks pass.
```

**Gate:** all writers accounted for; invariants pass; migration/cutover procedure explains both pre-cutover rollback and post-cutover forward recovery.

## Task 6 — Receiving, scanner counts, transfers, and restocking

**Files:** blueprints/stock.py, restock.py, inventory.py; new receiving/transfer endpoints where needed; frontend/src/pages/Stock/, Restock/, new Inventory receiving/transfer views; reusable scanner input only after a second real use. Tests: test_receiving.py, test_transfers.py, test_restock_lifecycle.py, test_counts.py.

- [ ] Implement keyboard-wedge scanning inside an explicit operation. Scanner Enter adds one intended scan; a repeated code is a valid next unit unless the request key indicates a network retry.
- [ ] Build a receipt draft with destination, units, shipment reference, line discrepancies, and post preview. Supplier/cost are optional known/unknown facts.
- [ ] Extend restock to requested/picked/received quantities. Reserve or account for picked units; release unused reservation explicitly.
- [ ] Build source → transit → destination transfers with partial dispatch/receipt and actor history. Destination receipt cannot exceed dispatched remaining quantity.
- [ ] Reuse existing hot-item list and evening check UI where useful. Count expected quantity at a captured posting version; changed counted items require conflict/recount.
- [ ] Post approved count differences once through inventory_commands. Do not retain a parallel formula that subtracts already-posted sales again.
- [ ] Add floor min/max targets and reviewable restock suggestions, bounded by available back stock.
- [ ] Preserve count/receipt drafts across a failed submit or navigation warning; label unsent work and recover by document/key. No offline inventory posting.

**Gate:** receive → replenish → partial delivery → count → correction works with DT/MK isolation; duplicate scans versus duplicate network requests are distinguished.

## Task 7 — Unified sales intake, tenders, and protected evidence

**Files:** db.py; blueprints/sales.py; proposed blueprints/payments.py; new private evidence handler; frontend/src/pages/Sales/; new mobile payment-evidence view. Tests: test_sales_posting.py, test_sales_reconciliation.py, test_payments.py, test_payment_evidence.py.

- [ ] Add immutable posted sale/line snapshots and separate payment records using integer cents, tender, source identity, and settlement/evidence status.
- [ ] Keep cash, card, e-transfer, WeChat Pay, and Alipay separate. Do not derive cash revenue from historic unit quantities.
- [ ] Add staff entry access while retaining manager-only reporting/adjustment controls. Apply store-operation access rules server-side.
- [ ] Implement one explicit manual intake path while Clover integration is absent. Link original receipt/source when available.
- [ ] Classify 汇总 imports as missing transactions, reconciliation summaries, or already-posted stock movements before any commit. Keep the review parser and ambiguous-match correction flow.
- [ ] Make cross-source reconciliation link an existing sale. Do not deduplicate by product/date/amount alone; document identity and ambiguous matching require review.
- [ ] Persist externally confirmed paid sales even when inventory allocation fails; show pending allocation and required resolution rather than dropping the sale.
- [ ] Record actual discount/rounding and source tax amounts. Test the three owner examples as stored outcomes; do not turn the previous assistant's candidate algorithm into policy.
- [ ] Add private evidence upload with auth/store checks, type/size verification, metadata minimization, and no public product-image route. Support missing/pending/rejected evidence without falsely marking payment settled.
- [ ] Keep monetary corrections and stock returns separate and linked to the original sale.

**Gate:** all five tenders reconcile; duplicate intake does not repeat stock movement; evidence is inaccessible across unauthorized roles/stores; later price changes do not rewrite history.

## Task 8 — Guided store closing

**Files:** new blueprints/closing.py and frontend/src/pages/Closing/; db.py; existing Restock/EveningCheckStep.tsx and history components reused through explicit interfaces. Tests: test_closing.py and one browser full-day scenario.

- [ ] Add one close session per store/date with draft, submitted, reviewed/closed, and correction history.
- [ ] Reference posted sales, payment state, restock receipts, and count documents; do not re-execute their stock effects.
- [ ] Support parallel cash/restock tasks, but require final sales/restock before final hot-item comparisons.
- [ ] Capture $650 opening/retained bill float, actual opening/retained coins, paid-ins, payouts/removals, counted denominations, and computed variance.
- [ ] Use the design's equations and make unknown imports/tenders/evidence visible. Block final sign-off or require an explicit authorized exception rather than invent matching totals.
- [ ] Use posting/version checks to detect late operations; retain externally confirmed late sales as linked exceptions/corrections.
- [ ] Freeze closed snapshots; append corrections with reason/actor. Financial removal/stock return must not be inferred from one another.

**Concrete cash fixture:**

```text
Opening bills 650.00 + opening coins 20.00
Cash receipts 300.00, paid-ins 0, payouts 30.00, prior removal 0
Expected drawer = 940.00
Counted drawer 938.50 -> shortage -1.50
Retained next opening = 650.00 bills + 20.00 coins
Removed cash = 268.50 (not 300.00 cash-sales revenue)
Any e-transfer/card/WeChat/Alipay receipt leaves drawer expectation unchanged.
```

**Gate:** full day closes with explainable discrepancies and signatures; late events cannot silently rewrite a closed report.

## Task 9 — Trade display and condition workflow

**Files:** new blueprints/trades.py and frontend/src/pages/Trades/; product identity/condition fields and inventory_commands; private condition evidence. Tests: test_trades.py.

- [ ] Represent a current trade slot per store/series and item identity/history without serializing every ordinary random box.
- [ ] Record sticker/receipt proof, incoming/outgoing designs, box/accessory checklist, condition, and staff decision.
- [ ] Swap same-series items atomically with a slot-version check. Two simultaneous swaps cannot both receive the same outgoing item.
- [ ] Keep confirmed purchases excluded and outgoing traded items eligible for future trade.
- [ ] Handle direct purchase of the slot item as a sale. Opening its replacement is a separate explicit conversion; no stock means a visible empty/pending slot.
- [ ] Preserve disclosed defects at time of sale. Route exceptional claims to a manager decision; do not auto-create refund promises or return stock.

**Gate:** swap, repeated eligibility, rejection, direct purchase, missing replacement, and concurrent swap scenarios pass with no retail-history edits.

## Task 10 — Role-specific Today, navigation, reports, and interface verification

**Files:** new popcore_app/blueprints/today.py and popcore_app/tests/test_today_permissions.py; frontend/src/App.tsx, components/AppLayout.tsx or a store-scoped layout, pages/Dashboard/, Products/, Stock/, Restock/, Sales/, Closing/, Trades/; store-operation styles and browser role scenarios. Schedule files and calendar CSS are unchanged.

- [ ] Implement the design's navigation and Today queues using the real documents/statuses from Tasks 4–9.
- [ ] Implement role-specific Today highlights using the design's viewer/staff/manager/admin matrix. Keep four defined role views; do not add roles or a widget customization system.
- [ ] Add a read-only Today aggregation endpoint that derives role from verified identity and checks authorized store scope before calculating or returning fields, counts, or document links. Update the Today route guard to serve the restricted viewer view without widening access to other pages.
- [ ] Put My Shifts first on Staff Today: today's assignments and the next upcoming shift, including date, time, store, and position when assigned. Link View Schedule to the unchanged /schedule page. Reuse getMyShifts from frontend/src/pages/Schedule/scheduleApi.ts with store_code=ALL and the relevant local start date; do not duplicate scheduling logic or request other employees' schedules.
- [ ] Keep personal shift scope independent of the operations store selector so a DT selection cannot hide the employee's own MK assignment. Refresh on load/focus; distinguish no shift today, no upcoming assignments, stale data, and fetch failure.
- [ ] Below Staff's shift highlight, prioritize blocking exceptions, then today's actionable work, then status. Other roles keep this operational ordering. Staff work responsibility (cashier/picker/receiver) refines available tasks where recorded; it does not create an Auth0 role or invent assignments.
- [ ] Remove previously loaded sensitive data and refetch when account, role, or store scope changes. All Stores aggregates only authorized stores; direct links enforce the same underlying permissions.
- [ ] Add API and browser fixtures for all four roles across DT/MK: distinct highlights, correct action links, permitted task amounts, no restricted financial/evidence fields even in raw responses, role/account switching, stale/error/empty states, and unchanged Schedule.
- [ ] Verify Staff's My Shifts appears first on desktop/mobile; test today's shift, next assignment at another store, no assignments, changed assignment after refresh, local-date boundary, failed fetch, and account switching. Assert only the signed-in employee's shifts are returned and the link opens existing Schedule.

- [ ] Build reports from the same posted data: stock by unit/location, movement drill-down, tenders, evidence gaps, cash variance, transfer shortages, and count exceptions.
- [ ] Show revenue only from known actual financial records; unknown costs mean unavailable margin, not zero cost.
- [ ] Use consistent product identity, units, store/date context, validation, pending/saved states, and confirmed posting.
- [ ] Fix accessible names, keyboard flow, mobile targets, and product/table reflow in store-operation surfaces.
- [ ] Correct framework warnings in touched components only; retain any Schedule-only warnings for its deferred work.
- [ ] Lazy-load non-scheduling routes and measure production-mode request/transfer size. Optimize measured duplicate loads; do not use development StrictMode request counts as production proof.
- [ ] Use synthetic bilingual long names and dense data at 390/768/1440px, keyboard/scanner input, 200% zoom, error/stale/no-access, and slow-network states.
- [ ] Verify Schedule navigation, calendar interactions, employee settings, role behavior, and iCal still match baseline; scope any global CSS fix away from it.

**Gate:** all first-release tasks are reachable and complete end to end; Staff's assigned shifts are the primary Today highlight; role-specific Today content and its API are restricted to authorized information and actions; no false empty/error display, hidden critical action, stale cross-user data, or Schedule regression.

## Task 11 — Migration rehearsal, pilot, and release evidence

**Files:** proposed scripts for isolated migration/reconciliation checks; docs runbooks; popcore_app/backup.sh, nginx.conf, popcore.service, gunicorn.conf.py, logrotate.conf; setup_production.sh.

- [ ] Run additive migration and restore rehearsal with synthetic/representative legacy fixtures; verify SQLite WAL-aware backup, integrity, foreign keys, and attachment references.
- [ ] Create an explicit writer inventory and prove no old CLI/import/API bypass remains in authoritative mode.
- [ ] Produce opening-count and per-location cutover instructions. Preserve old reports and mark unverified stock/tender/provenance honestly.
- [ ] Prepare production-template corrections for upload limits, header inheritance, protected attachments, logs, and least-privilege writable data paths. Test CSP in report-only mode against Auth0 and used assets before enforcement.
- [ ] Verify scheduled insight timezone/business-date rules without changing Schedule. Keep one owner for scheduled work and test DST/restart behavior.
- [ ] Provide the off-host backup and recovery runbook. Actual backup destination, credentials, retention, and live restore execution are separate operations requiring the relevant access.
- [ ] Run the complete synthetic store day and correction/retry cases; inspect fresh diffs, test results, release-asset hashes, and Schedule preservation.
- [ ] Prepare a concrete reviewable release candidate and exact deployment/rollback instructions. Stop before deploying until the user authorizes that exact release and live verification.

**Gate:** local implementation can be called ready for pilot only with complete evidence. Droplet readiness remains separate; Schedule audit defects remain explicitly deferred.

## Task 12 — Clover integration after capability verification

**Boundary:** requires actual store device/plan/merchant/region facts and sandbox access. No production connection in the first internal release.

- [ ] Verify hardware/plan custom-tender behavior, permitted amounts/tax representation, merchant account mapping, and supported app distribution.
- [ ] Reconcile the Clover catalog against existing POPCORE IDs using stable external mappings; import preview only before approved changes.
- [ ] Establish one transaction identity linking Clover orders/payments, manual intake, and POPCORE sale documents.
- [ ] Implement durable input processing, duplicate/out-of-order handling, pending mappings, periodic reconciliation, and outbound stock publication after commit.
- [ ] Test lost responses, repeated events, partial payment, void/refund, offline arrival, and manual-entry overlap without duplicate sale/stock effects.
- [ ] Implement the approved discount policy only after its examples, edge cases, rounding, split tenders, and tax/processor representation are settled.
- [ ] Choose custom-tender extension versus another supported Clover approach based on sandbox proof; do not promise a plugin before feasibility.
- [ ] Enable one merchant/store at a time through a separately approved rollout.

References: [Clover custom tenders](https://docs.clover.com/dev/docs/custom-tenders), [partial-payment/device considerations](https://docs.clover.com/dev/docs/creating-custom-tender-apps).

## Task 13 — WooCommerce channel after allocation policy

**Boundary:** popcore.ca integration is a separate channel and checkout/repository; do not modify its WordPress code as an incidental POPCORE change.

- [ ] Confirm fulfillment source locations, reservations, protected floor stock, cancellation/expiry, and cross-location allocation.
- [ ] Map WooCommerce product/variation IDs to real stock variants. Sealed-set and single quantities must not expose competing availability twice.
- [ ] Validate source notifications, reconcile source orders, and deduplicate state transitions.
- [ ] Publish computed available-to-promise stock after local commit; do not allow bidirectional stock overwrites.
- [ ] Record billing/shipping country/province/postal/address and source tax snapshots as needed for order records with appropriate access/retention.
- [ ] Test reserve → pay → fulfill, cancel/expire, split shipment, refund without return, duplicate event, stale event, offline interval, and last-unit competition.
- [ ] Prove the chosen reservation strategy before claiming prevention of online/store overselling. Monitor unresolved events explicitly.

## Task 14 — Vendors, purchasing, and wholesale

- [ ] Confirm owner access to purchase costs, supplier details, FX/shipping/duties, wholesale customers, pricing, minimums, deposits, and credit.
- [ ] Extend Task 6 receipts with supplier/PO matching and partial receipt. Keep unknown cost explicit and restrict cost views.
- [ ] Implement wholesale quote/order → reservation → fulfillment → actual payment with the same inventory posting module.
- [ ] Test partial receipt, shortage, price snapshot, partial fulfillment/payment, and cancellation without negative or duplicated stock.
- [ ] Add profitability only when inputs are complete; credit/automated accounting remain excluded until separately specified.

## Audit finding disposition

All 23 audit findings have a destination. Revalidation can revise severity/technical remedy; it must not silently drop a finding.

| Audit ID | Disposition |
| --- | --- |
| P1-01 restock reversal | Tasks 1, 5, 6: containment then immutable correction |
| P1-02 report replacement | Tasks 1, 5, 7: no guessed reversal; source identity |
| P1-03 negative sales | Tasks 1, 7: API validation and typed lines |
| P1-04 malformed shifts | Deferred by explicit Schedule freeze |
| P1-05 failure shown as empty | Tasks 2, 10 for store pages; Schedule portion deferred |
| P1-06 npm advisories | Task 3 with reachability and compatibility checks |
| P1-07 zoom clipping | Task 10 store pages; Schedule portion deferred |
| P2-01 missing JWT issuer | Task 2, with Schedule auth smoke |
| P2-02 uploads | Task 2 product images; Task 7 private evidence |
| P2-03 CSV formulas | Task 2 and report export checks |
| P2-04 accessibility | Task 10 store pages; Schedule controls deferred |
| P2-05 mobile Schedule tabs | Deferred by explicit Schedule freeze |
| P2-06 SQLite contention | Task 5 real controlled contention; bounded conflict |
| P2-07 insights UTC | Task 11 explicit business timezone; no Schedule edit |
| P2-08 test/CI gaps | Tests within Tasks 1–10 and minimal Task 3 CI |
| P2-09 backup/hardening | Task 11 templates/rehearsal; live host gate separate |
| P2-10 telemetry/LLM data | Task 2 plus private evidence boundary in Task 7 |
| P2-11 login handling | Task 2 shared fix with Schedule regression gate |
| P2-12 bundle/request load | Task 10 non-Schedule routes; Schedule optimization deferred |
| P2-13 dependency reproducibility | Task 3 production/development constraints |
| P3-01 ECDSA advisory | Task 3 supported resolution or explicit exception |
| P3-02 database paths/dead code | Task 5 configuration isolation; dead employee-handler removal deferred as unnecessary to store workflows |
| P3-03 framework warnings | Task 10 touched components; Schedule warnings deferred |

## Verification commands and completion rules

Use current AGENTS.md for exact setup/environment loading. At execution, capture exit status after each command.

```powershell
Set-Location D:\dev\POPCORE
$env:TEMP = 'D:\dev\POPCORE\.local\tmp'
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = 'D:\dev\pip-cache'
$env:npm_config_cache = 'D:\dev\npm-cache'
$env:PLAYWRIGHT_BROWSERS_PATH = 'D:\dev\POPCORE\.local\ms-playwright'
$env:DISABLE_SCHEDULER = '1'

.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m unittest discover -s popcore_app/tests -v

# Backend when browser checks need the application:
.\.venv\Scripts\python.exe -m flask --app popcore_app/app.py --env-file popcore_app/.env run --host 127.0.0.1 --port 5000
```

In a second terminal:

```powershell
Set-Location D:\dev\POPCORE\popcore_app\frontend
npm test
npm run build -- --outDir ../../.local/instore-verification-build
# Start only on an unused port; never replace another running process.
npm run dev -- --host localhost --port 5173 --strictPort
```

Run browser tests against an isolated application/fixtures with external effects intercepted, not against the user's real database. Compare before/after Schedule behavior and source hashes. For pure ledger correctness, assert balances, source documents, and movements—not just HTTP status.

Finish each phase with reviewed diffs and relevant tests. The final gate includes all new tests, old Schedule tests, build, same-store/cross-store permission checks, backup/migration rehearsal, full synthetic day, duplicate/lost-response cases, and explicit unresolved production decisions. Do not claim the store's historical stock, tax accounting, external synchronization, or Schedule audit issues are fixed by this local work.
