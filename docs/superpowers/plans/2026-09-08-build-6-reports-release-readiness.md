# Build 6: operational reports and first-release readiness implementation plan

> **For agentic workers:** Use superpowers:executing-plans task by task only after implementation is requested. Read AGENTS.md, the store design, the verified Build 5 handoff, and this plan first. This plan does not authorize subagents, commits, pushes, deployment, or live data access.

**Goal:** Make all agreed first-release store work reachable, provide trustworthy operational reports, and prove a complete local store day with recovery and a reviewable pilot package.

**Architecture:** Keep Flask/SQLite and React. Reports read authoritative documents and frozen closing snapshots; existing operation services remain the only writers. Finish missing controls in existing screens. Verify the real frontend/API/database together using an isolated synthetic application.

**Tech Stack:** Existing Flask, SQLite WAL, Auth0, React/TypeScript, Ant Design, unittest, Node tests and Playwright; existing nginx/systemd/Gunicorn templates. Reuse standard-library backup/hash/CSV support and the installed D: environment.

**Spec:** [Store design](../specs/2026-09-07-instore-website-design.md), sections 6H, 7, 9 and 10; [roadmap](2026-09-07-instore-website-plan.md), remaining Phase E / Task 10 and Task 11.

**Status:** Planning only, 2026-09-08. Starts after [Build 5](2026-09-08-build-5-trades-today.md) is implemented and verified. Neither build is implemented by these documents.

## Scope and boundaries

Build 6A completes reports and connected review screens. Build 6B proves interface quality, migration/recovery and the synthetic pilot, then prepares deployment instructions. This is the end of the internal first-release scope, not Clover/WooCommerce integration.

Keep Schedule unchanged. Do not add a new design system, automation platform, forecasting engine, accounting ledger, purchasing/cost model, discount calculator, external connector, or customer portal. Do not turn a condition claim into an automatic refund.

Work in D:/dev/POPCORE, preserving all prerequisite uncommitted work, staged server.log deletion, source IDs, real databases and committed static assets. Keep test data, uploads, caches, manifests and production verification output on D:. All Stores remains read-only and restricted to explicitly authorized stores, including for admin.

Check weekly usage at entry, between tasks and before final proof. At 40% used / 60% remaining, stop starting work and hand off the exact checkpoint. Never redeem a reset.

Local synthetic success does not prove droplet configuration, owner policies, opening stock, real Auth0 settings, actual printer/scanner behavior or off-host recovery. Record each separately. Do not connect production services or download droplet data to complete a local test.

## Entry gate and current evidence

At planning, Builds 1–4 are uncommitted on codex/build-4-sales-payments-closing at 8630199ac780059f5cb185a85c6efd0ac4597b91. Recheck branch, remote, working tree and Build 5 files at execution. Do not start from clean HEAD and accidentally omit prerequisites.

Read docs/trades-and-today.md and rerun relevant Build 5 tests. Revalidate these source findings; remove a task only when the required behavior already exists and has evidence:

- check_store_day.py supplies canned API responses. Keep that UI regression check, but it cannot satisfy the real frontend/backend pilot gate.
- SaleDocument currently lacks complete controls for manager payment verification/rejection, source reconciliation and separate refund/return history, although backend operations exist.
- Closing currently passes default zero coin values and one reason across review exceptions. Its step labels do not by themselves prove the linked receiving/count/review workflow is reachable.
- backup.sh currently backs up SQLite only. Private payment images and Build 5 condition images must be covered by the recovery contract.
- insights.py uses legacy sales facts and UTC-related date handling; scheduler ownership/restart behavior needs proof. Do not show its quantity-based estimates as actual tender revenue.
- docs/sales-and-closing.md references .local/playwright-browsers, while the installed browser path is .local/ms-playwright. Correct that runbook reference during this build.
- Production templates and source code are local evidence only. No droplet readiness claim follows from reading them.

Capture Schedule/source/static hashes and baseline production-mode request/transfer measurements before changes. Run defects through a failing scenario and minimal correction. Avoid unrelated cleanup.

## File map

Paths are relative to the repository. New files are proposed and must not collide with prerequisite implementation.

| File | Responsibility |
| --- | --- |
| popcore_app/operational_reports.py; blueprints/reports.py (new) | Read-only scoped report queries and CSV responses |
| popcore_app/frontend/src/api/reports.ts; pages/Reports/index.tsx (new) | Filters, rows, totals/completeness and document drill-down |
| popcore_app/frontend/src/api/salesDocuments.ts; api/closing.ts | Expose existing review operations with versions and stable keys |
| popcore_app/frontend/src/pages/Sales/Entry.tsx; SaleDocument.tsx; PaymentEvidence.tsx | Recover drafts and complete permitted payment/source/correction work |
| popcore_app/frontend/src/pages/Closing/index.tsx | Real guided close, explicit coins, exception decisions and return-for-changes |
| popcore_app/frontend/src/pages/Stock/Goods.tsx; Receiving.tsx; Transfers.tsx; Counts.tsx | Existing document links/resume and unresolved-work access |
| popcore_app/frontend/src/pages/Dashboard/index.tsx; App.tsx; components/AppLayout.tsx | Finish Today drill-down/navigation and non-Schedule route loading |
| popcore_app/blueprints/sale_documents.py; closing.py; goods.py | Bounded scoped listing/read endpoints only where existing APIs cannot support required screens |
| popcore_app/closing_operations.py; sales_operations.py | Minimal defect corrections demonstrated by integrated tests; retain established transaction boundaries |
| popcore_app/app.py; tests/support.py | Reports registration; isolated harness support |
| popcore_app/tests/test_operational_reports.py; test_report_exports.py; test_store_day_integration.py (new) | Query grain, money, role/scope, exports and real operation sequence |
| popcore_app/tests/browser/local_app.py; check_release_pilot.py (new) | Disposable real backend and real-API browser rehearsal |
| popcore_app/tests/test_release_recovery.py; test_insight_schedule.py (new) | Migration/restore and Toronto daily job ownership |
| scripts/check_release_recovery.py (new); existing migration helpers | Synthetic recovery evidence under .local |
| popcore_app/backup.sh; popcore_app/nginx.conf; popcore_app/popcore.service; popcore_app/gunicorn.conf.py; popcore_app/logrotate.conf; setup_production.sh | Existing deployment templates and complete backup entry point |
| popcore_app/insights.py; app.py; db.py | Correct business-date scheduling and minimal durable daily-run ownership |
| .github/workflows/checks.yml | Repeatable relevant test/build gates |
| docs/operational-reports.md; docs/store-pilot-runbook.md; docs/release-and-recovery.md (new) | Report semantics, writer/cutover checklist, evidence, release and rollback |
| docs/sales-and-closing.md; README.md; AGENTS.md | Correct verified commands and local evidence limits |

Within grouped rows, blueprints and tests start under popcore_app; frontend api/pages/components paths start under popcore_app/frontend/src. setup_production.sh is at repository root. Add a helper only where necessary. For example, a small standard-library backup helper is justified if it lets backup.sh and the cross-platform recovery test execute the same SQLite-plus-attachments implementation; do not create a backup framework.

## Task 1 (6A): operational reports with correct facts and permissions

**Interfaces:** GET /api/reports/inventory, /movements, /goods-exceptions, /sales, /tenders, /evidence-exceptions, /cash-variance, /count-discrepancies, /closed-days. GET /api/reports/<known-report>/export.csv applies identical scope and filters. UI /reports, with report selection and shareable filter state.

Use a fixed allowlist of reports. Parameters: store_code, from/to business dates when relevant, verified series_id/product_id/location_id, page and page_size. Enforce bounded dates/pages and stable ordering; export a bounded complete result or explicitly refuse an oversized request. Never silently truncate.

Common response: scope, filters, generated_at, items, total_rows, relevant totals and completeness. query_report(con, report_name, *, actor, filters) -> dict reads only. Treat examples as proposed contracts, then type them explicitly in reports.ts before wiring UI.

- [ ] Write failing fixtures for unauthorized stores/IDs, All Stores, viewer/staff financial queries, unknown money, duplicate source links, mixed stock units, split tender and late adjustments.
- [ ] Financial/evidence/cash/closed-day reports require manager plus explicit inventory_access scope. Inventory/movement read access follows the already-approved inventory permissions. Staff see their actionable items through Today/documents; no new all-staff financial export.
- [ ] Scope SQL before aggregation. A manager of DT cannot infer MK row counts or export MK facts. Protect private evidence metadata and file bytes separately; reports link to permitted review screens, never publish image paths.
- [ ] Inventory rows retain SKU stock_unit, location, disposition and verified conversion. Show optional equivalent boxes only for compatible known conversions. Never add sets, boxes and pieces as one stock total or count both consumed sets and created boxes.
- [ ] Movement/receipt/transfer/count reports use immutable source document IDs and current outstanding quantities. Keep proposed, posted, in-transit, received, disputed and corrected states distinguishable.
- [ ] Sales group quantities by verified series/design/store using posted line facts. Monetary totals use recorded sale/payment snapshots, not current catalog price. Unknown total/tax/discount remains null with incomplete counts.
- [ ] Tender report keeps card, cash, e-transfer, WeChat Pay and Alipay separate. Sum payment entries at payment grain; do not multiply them by joined sale lines/evidence/source links. Show recorded and verified amounts, pending/unknown counts and linked refund events distinctly. Deduplicate reconciled source aliases.
- [ ] Do not allocate a split payment to particular designs without recorded allocation. Product sales and tender breakdown are separate views joined by sale detail, not an invented product-by-tender cross-tab.
- [ ] Cash variance reads actual counts and accepted closing facts. Closed-day history reads its immutable snapshot plus separately labeled later adjustments; current operational reports show current facts. Never silently restate the original close.
- [ ] Export through existing CSV-safe patterns: neutralize formula-prefixed text, quote values, enforce scope, avoid private image/secret columns and send private/no-store headers.
- [ ] Add a simple table/filter UI with known subtotals, completeness warnings, accessible labels and document links. Costs/profit remain absent.
- [ ] Run test_operational_reports.py and test_report_exports.py.

Required fixture result:

~~~text
One posted sale: 2 lines; recorded total 3000 cents.
Payments: cash 1000 + card 2000; two images; two linked source references.
Report: sales 3000, cash 1000, card 2000 once each, never 6000 or 12000.
Second sale total/tender amount unknown:
  known subtotal remains 3000; incomplete record count 1; total not claimed complete.
Refund 500 without physical return:
  refund event 500; no inventory receipt; original sale/snapshot retained.
One sealed set opened to 12 boxes:
  0 remaining sets + 12 boxes; no extra 1 set in equivalent availability.
~~~

**Gate:** Every total is explainable from scoped source records, and unknown values remain visible.

## Task 2 (6A): finish required work through existing screens

**Existing API families:** sale documents detail/update/post/allocate/payments/source-links/returns; payment verify/reject/events/evidence review; closing detail/update/cash-events/cash-counts/submit/return/close/adjustments. Confirm decorator paths and body schemas from source before creating typed wrappers; do not add duplicate operation endpoints.

- [ ] Create a route/action checklist for staff and managers: Today -> source document -> permitted action -> confirmed result -> refreshed Today/report. Cover every pending state, including older drafts and returned closings.
- [ ] Provide scoped document lists or resume-by-ID where absent. Preserve IDs and current draft input; returning after navigation/reload must recover server-saved work. List/search responses need the same row/field permissions as detail responses.
- [ ] Sales review: show immutable posted lines, tender/evidence state, allocation exception and source history. Staff repair their permitted incomplete work; managers verify/reject evidence/payment with individual reasons.
- [ ] Provide explicit source-reconciliation and allocation controls using server version checks. A generic Retry with reason is insufficient when a mapping or source identity is required.
- [ ] Keep refund and physical return as separate intentional actions using existing services. Show exact amount/unit/disposition and reason, existing eligibility constraints, original record and resulting event. No action inferred from a condition decision.
- [ ] Closing must show linked sales/evidence, cash, restock/transfer, hot-item count and review steps. Users can open and finish required source documents and return to the same closing; do not duplicate inventory/count posting inside Closing.
- [ ] Capture actual opening and retained coins explicitly, including a deliberately entered zero. Retain the agreed $650 bill float rule. Never send unconfirmed default zeros; distinguish missing input from zero.
- [ ] Record denomination counts and permitted paid-in/out/refund events. Show expected drawer, counted cash, retained next-opening cash, removable cash and variance using backend facts. Explain unknown cash as a blocker.
- [ ] Managers decide each review exception separately with a specific reason. Expose return-for-changes and its reason; staff can correct/resubmit. Hard blockers cannot be converted into accepted exceptions.
- [ ] A changed source token/stock version invalidates affected count/close approval and prompts refresh/recount. Closed days remain immutable with linked late adjustments.
- [ ] Keep request keys stable across lost response/retry; create a new key only for new intent. Refresh from authoritative result after success. No optimistic stock, payment verification or closing completion.
- [ ] Recheck auth/store scope at action time; discard delayed old-identity responses. All Stores has no mutation button and backend rejects an aggregate mutation.
- [ ] Add targeted browser tests and only necessary backend regressions; run existing payments, reconciliation, closing and goods tests.

Cash acceptance example from the agreed design:

~~~text
Opening: 65000 bill float + 1250 explicitly recorded coins.
Verified cash receipts: 20000. Paid-out: 1000. No other cash event.
Expected drawer: 85250.
Counted: 85000. Retained: 65000 bills + 1000 explicitly entered coins.
Variance: -250. Removal: 19000.
Unverified cash receipt or missing opening coins -> cannot claim final cash reconciliation.
Accepting variance needs its own manager reason; missing required proof stays a blocker.
~~~

**Gate:** Every first-release review task is reachable and completable with the correct role; no API-only step remains in the agreed store-day flow.

## Task 3 (6B): interface quality and measured loading

Keep the existing light work surfaces, dark navigation, indigo accents, typography and components. This task repairs workflow/accessibility defects in the affected store screens; it is not a redesign.

- [ ] Verify Today, Products/Inventory, Receiving, Transfers, Restock, Counts, Sale Entry/Review, Payment Evidence, Trades/Conditions, Closing and Reports at 390/768/1440px and 200% zoom with dense bilingual fixtures.
- [ ] Staff shifts stay first on Today. Verify today's assignments, the next later assignment, another-store shift, no assignments, failed request and focus refresh. Operational selection must not filter away personal shifts.
- [ ] Make table/detail actions reachable with keyboard, visible focus and meaningful names. Provide a compact mobile layout or deliberate table scrolling with actions still reachable. Show loading/error/no-access/empty/stale states accurately; retain form work on failures.
- [ ] Test scanner Enter, repeated scan, unknown/ambiguous barcode and camera evidence capture. Browser automation proves input handling; actual shop devices remain a pilot check.
- [ ] Use React.lazy/Suspense for non-Schedule page imports and existing route boundaries. Measure the production build before/after for initial requests, transferred JavaScript and duplicate API requests. Fix measured regressions; set no arbitrary performance claim.
- [ ] Keep Schedule imports/source/CSS and behavior outside optimization scope. Test unchanged Schedule navigation, calendar interactions, employee settings, role behavior and exports after any shared auth/layout change.
- [ ] Store UI evidence, screenshots and measurement output under .local/build6. State browser/network assumptions and any difference from real-device tests.

Use the existing page module as the lazy boundary, for example in App.tsx after replacing its eager import:

~~~tsx
const SaleEntryPage = lazy(() => import('./pages/Sales/Entry'))
// Place the existing permitted route element inside Suspense with the
// existing loading component. Preserve route paths and RoleRoute checks.
~~~

**Gate:** No inaccessible critical action, false empty state, stale cross-user data or Schedule regression. Report measured performance rather than inferred development-mode gains.

## Task 4 (6B): real frontend/API/database store-day proof

**Interfaces:** tests/browser/local_app.py owns a disposable database, private uploads and a loopback Flask server; check_release_pilot.py owns only the child processes it starts and prints the evidence directory. test_store_day_integration.py exercises the same operations with database assertions.

Reuse tests/support.py isolation and the current browser harness where possible. Do not create a production test-login route or environment flag that bypasses production authentication.

- [ ] Set DB/upload paths and DISABLE_SCHEDULER before module initialization; refuse paths outside the resolved .local test directory. Bind an unused loopback port and never stop an existing user service.
- [ ] Seed synthetic catalog identities, units, locations, store scopes, two staff and manager/admin/viewer identities and personal shifts. Register real operation blueprints, including unchanged Schedule. Test auth with locally signed fixture JWTs and a test-only local key provider; the browser Auth0 adapter only supplies those test identities/tokens.
- [ ] Route browser /api requests to that real server. Do not intercept successful API responses with fixture dictionaries. Block external effects; only owned loopback fixture services are allowed. Keep raw permission tests in the gate.
- [ ] Drive receiving -> floor restock -> partial transfer dispatch/receipt -> manual sale with multiple tenders -> evidence upload and manager review -> inventory/count discrepancy -> inspected trade -> direct trade sale and separate replacement -> cash count -> submit/return/resubmit -> manager close.
- [ ] Include the design's numeric cases: receive one 12-box set as one set, explicitly open to 12 boxes, request five/pick four/receive three with the fourth still accounted for, and record all five tender methods across the test day's sales. Assert each native/equivalent unit and tender result rather than merely showing a success screen.
- [ ] Add source reconciliation without another deduction, unknown tender/evidence blocking, same-key lost-response replay, stale count after late sale, duplicate scan, two competing last-unit/slot requests, condition claim, separate refund/return and post-close adjustment.
- [ ] Assert database results after each meaningful stage: balance/provenance, exact document/event counts, immutable original records, evidence access, cash equation and closing snapshot. Compare report/Today output with these expected facts.
- [ ] Test DT/MK scopes, All Stores, four roles, direct forbidden IDs, logout/account switch and a delayed previous-user response. Verify staff shifts are private and first.
- [ ] Preserve older mocked browser tests as focused UI regression evidence. Label them honestly and make this new check the real integration evidence.
- [ ] Add the integration/recovery checks to CI using existing dependencies. Run backend suite, frontend tests and isolated production build; capture exit statuses and failure evidence.

Pilot failure assertions:

~~~python
assert sale_retry["sale_id"] == first_sale["sale_id"]
assert inventory_deduction_count_for_sale == 1
assert reconciled_source_did_not_add_stock_movement
assert refund_without_return_did_not_receive_stock
assert closed_snapshot_before == closed_snapshot_after_late_adjustment
assert other_store_private_evidence_status in (403, 404)
assert staff_today_contains_no_manager_financial_totals
~~~

**Gate:** A real browser can complete the synthetic store day against real services and the resulting database reconciles. No mocks are presented as backend integration proof.

## Task 5 (6B): writer inventory, migration and complete recovery

**Interfaces:** scripts/check_release_recovery.py --output .local/build6/recovery creates its own fixtures, backup package and restore target. It refuses real database paths and exits nonzero on any mismatch. Its exact flags must be implemented and documented together.

- [ ] Inventory every stock/sale writer: API routes, legacy reports, bulk uploads/imports, CLI scripts, scheduled jobs and future connector placeholders. Record path/function, ownership mode and whether it posts through the authoritative service or is blocked/read-only. Search current code; do not trust an old audit list.
- [ ] Test each remaining legacy write path in authoritative mode. No administrative/CLI exception may bypass stock units, source IDs, scope or version rules. Add narrow adapters/rejections only for reproduced gaps.
- [ ] Rehearse additive migration on synthetic legacy fixtures, including unknown series/unit/conversion, duplicate source references and incomplete tender facts. Existing IDs/history remain; unresolved mappings prevent unsafe opening/posting instead of being guessed.
- [ ] Write a per-store/location physical opening and cutover runbook: approved access, verified catalog mappings, quiet write window, physical counts, opening provenance status, reconciliation and explicitly enabled authority. Do not import Clover stock as trusted opening stock.
- [ ] Extend the existing backup entry point to package a SQLite online-backup snapshot plus every image referenced by that snapshot, including product, payment and condition evidence. Read attachment references from the snapshot; copy immutable referenced bytes, record relative path, size and SHA-256, and fail on missing/changed evidence.
- [ ] Keep secrets/config values outside the package. Document how operators separately restore credentials. Reject absolute/traversal attachment paths; never serve private evidence publicly during restore.
- [ ] Test a write while the WAL-aware DB backup runs, successful isolated restore, integrity_check, foreign_key_check, row/source/version preservation, balances, closing snapshots and attachment hash/access checks.
- [ ] Restore to a new isolated directory only. Test missing/corrupt attachment and corrupted/incomplete manifest failures; never overwrite an existing real target.
- [ ] Retain existing backup behavior compatibility until migration is documented. Do not invent a new retention or deletion policy. Off-host destination, credentials, retention and live recovery are explicit owner/operator setup items; local rehearsal may use a separate local destination.
- [ ] Record evidence and actionable recovery steps in docs/release-and-recovery.md.

**Gate:** Synthetic pre-cutover and current fixtures restore correctly with their referenced evidence. A database-only copy cannot be called a complete backup.

## Task 6 (6B): production templates and daily insight scheduling

Use existing deployment files. Read actual settings before choosing a small correction; preserve working paths and explicitly document required runtime values.

- [ ] Align nginx upload limits with application's supported files and enforce API/static/private paths correctly. Verify security-header inheritance on success/error responses and that payment/condition files cannot be downloaded via static aliases.
- [ ] Prepare CSP in report-only mode covering actual Auth0/API/assets/camera use. Use browser evidence for local behavior; real tenant/domain testing is a deployment gate. Do not enforce an untested policy or add broad wildcards to silence failures.
- [ ] Set service/log/database/upload/cache writable paths and ownership explicitly with least privilege. Keep private directories outside public assets. Verify Gunicorn, log rotation and setup script agree; do not hard-code this Windows development path as a Linux install path.
- [ ] Correct scheduled insight business dates to America/Toronto for existing stores. Define once-per-business-day behavior including a missed scheduled time after restart; avoid exact-minute-only checks.
- [ ] Give the daily job one explicit process owner in the existing deployment mechanism and a minimal durable (job, business_date) uniqueness/claim record. Commit insight rows and successful completion together; define safe retry after failure without duplicate insights. No new job platform.
- [ ] Test two workers racing, restart before/after run, missed time, failed generation and Toronto spring/fall DST. These tests do not change Schedule scheduling behavior.
- [ ] Do not expose legacy quantity-times-price insights as actual money or authoritative stock. In authoritative mode use already-verified facts where directly available; keep unsupported legacy insight checks disabled/labeled outside Today rather than inventing forecasts. Preserve useful legacy-mode behavior with focused tests.
- [ ] Validate shell syntax, nginx configuration and systemd unit syntax in an available isolated Linux environment/CI. Execute no setup_production.sh against the user's machine or droplet. Missing tooling is recorded as unverified, not passed.
- [ ] Document necessary host-specific substitutions and the separate real Auth0/CSP/log/backup checks. Never write or display credentials in artifacts.

Business-date boundary to test, using timezone-aware injected time rather than the machine's local date:

~~~python
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

def insight_business_date(now: datetime) -> str:
    return now.astimezone(ZoneInfo('America/Toronto')).date().isoformat()

assert insight_business_date(datetime(2026, 9, 9, 2, tzinfo=timezone.utc)) == '2026-09-08'
~~~

Use this date in the durable daily-run key and query boundaries. The deployment's configured daily time determines when work is due; the date helper alone does not implement job ownership.

**Gate:** Local logic and available template checks pass; any unavailable Linux/live checks have named owners and commands. No production-ready claim until those gates are satisfied.

## Task 7 (6B): reviewable pilot candidate and handoff

- [ ] Reconcile all first-release acceptance scenarios in the store design against test/browser/recovery evidence. List any blocker and its exact remaining action; do not label incomplete work complete.
- [ ] Produce a local candidate directory under .local/build6/release-candidate with an explicit allowlist of required application files and the isolated frontend build, excluding .env, real databases, logs, caches, test JWT keys, evidence and dependencies.
- [ ] Record repository, branch, baseline HEAD, dirty/untracked state, candidate file hashes, build commands/runtime versions and evidence results. Builds 1–5 are currently uncommitted: a HEAD ID alone cannot identify the candidate. Include all approved required source bytes and flag pre-existing unrelated files; do not commit simply to create a release label.
- [ ] Run a package inventory/secret check and verify the built asset manifest resolves. Committed popcore_app/static remains unchanged during local candidate creation.
- [ ] Write the exact deployment/rollback sequence: preflight and backup, maintenance/write boundary, candidate identity, additive migrations, file permissions, service start, scoped smoke and reconciliation, rollback trigger and target. If new data has been posted, older code may not support the new schema; require reviewed reconciliation/forward recovery instead of silently restoring an old DB and losing writes.
- [ ] Name the real pilot gates: approved operational store access; physical opening counts; opening/retained-coin routine and approval thresholds; trade proof/condition handling policy; actual scanner/camera behavior; host/private storage/backup credentials and retention; real Auth0 callbacks/origins; prior working release and backup location.
- [ ] Preserve deferred Schedule audit findings and Clover/WooCommerce/purchasing work. A completed local pilot package does not approve the live cutover.
- [ ] Update runbooks, README and AGENTS with only verified project-specific commands and evidence distinctions. Correct the browser-path discrepancy.
- [ ] Finish with fresh diff/status, Schedule/static hash comparison, test results and candidate manifest. Stop before deployment and present the exact candidate for separate authorization.

**Gate:** A reviewable local candidate and recovery/runbook package exists with complete evidence or clearly named blockers. No commit, push or deployment occurs under this plan.

## Verification commands and completion rule

Run from the established D: environment. New script names below are contracts to implement in this build; they are not claimed to exist now. Check every exit status.

~~~powershell
Set-Location D:\dev\POPCORE
$env:TEMP = 'D:\dev\POPCORE\.local\tmp'
$env:TMP = $env:TEMP
$env:DISABLE_SCHEDULER = '1'
$env:PLAYWRIGHT_BROWSERS_PATH = 'D:\dev\POPCORE\.local\ms-playwright'
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m unittest discover -s popcore_app/tests -v
.\.venv\Scripts\python.exe popcore_app/tests/browser/check_foundation.py
.\.venv\Scripts\python.exe popcore_app/tests/browser/check_inventory_core.py
.\.venv\Scripts\python.exe popcore_app/tests/browser/check_goods_flow.py
.\.venv\Scripts\python.exe popcore_app/tests/browser/check_store_day.py
.\.venv\Scripts\python.exe popcore_app/tests/browser/check_trades_today.py
.\.venv\Scripts\python.exe popcore_app/tests/browser/check_release_pilot.py
.\.venv\Scripts\python.exe scripts/check_release_recovery.py --output .local/build6/recovery
Set-Location popcore_app\frontend
npm test
npm run build -- --outDir ../../.local/frontend-build-build6
Set-Location ..\..
git diff --check
git diff --exit-code -- popcore_app/static popcore_app/frontend/src/pages/Schedule popcore_app/blueprints/schedule.py
git status --short --branch
~~~

Use isolated output and compare baseline hashes even when git diff is empty, because prerequisite files may be untracked. Use the actual installed browser directory. Record applicable Linux template checks separately with their exact invocation/output.

**Build 6 exit gate:** Trusted reports, every required review action reachable, preserved staff shifts/role access, real synthetic store-day proof, successful migration and DB-plus-evidence restore, unchanged Schedule, and a traceable local pilot candidate. Report local readiness separately from the live approvals/configuration still required. Clover is the next roadmap phase only after this gate and a separate scoped plan.
