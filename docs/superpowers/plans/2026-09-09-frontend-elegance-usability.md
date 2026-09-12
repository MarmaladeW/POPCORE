# POPCORE frontend elegance and usability implementation plan

> **For agentic workers:** Use superpowers:executing-plans after implementation is requested, task by task with evidence checkpoints. This plan does not authorize subagents, commits, pushes, deployment or live data access.

**Goal:** Deliver an elegant, minimal in-store frontend with readable reports and straightforward daily work, while preserving operational rules and Schedule.

**Architecture:** Retain React, Ant Design, existing routes and Flask contracts. Use a scoped operations theme and small presentation helpers where several screens need the same behavior. Implement two sequential deliveries; complete and verify the first before starting the second.

**Tech Stack:** Existing React/TypeScript, Ant Design, CSS, Node built-in tests and Python/Playwright fixture/real-service harnesses. No new dependencies planned.

**Spec:** [Frontend design](../specs/2026-09-09-frontend-usability-design.md), with [store business design](../specs/2026-09-07-instore-website-design.md) governing role, stock and money rules.

## Constraints and execution baseline

- Main checkout: `D:\dev\POPCORE`; keep source, dependencies, temporary files, screenshots and builds on D:.
- Expected remote: `https://github.com/MarmaladeW/POPCORE.git`; last observed branch `codex/build-4-sales-payments-closing`, baseline HEAD `8630199ac780059f5cb185a85c6efd0ac4597b91`. Recheck rather than assuming these remain current.
- Preserve the dirty Builds 1-6 tree and staged `popcore_app/server.log` deletion. Capture starting diffs/untracked files under `.local/frontend-improvement/baseline`; HEAD alone does not identify the current source.
- Stop starting new work when weekly usage reaches 50% used / 50% remaining. Check before each task and after long verification. At the limit save a precise handoff; do not redeem a reset or begin the next task.
- Only planning documents are created by this request. Implementation starts on the user's later instruction. No intermediate commits or worktree moves are implied by skill defaults.
- Do not change Schedule route/source/calendar CSS, employee colors, permissions or exports. Keep its visual theme/shell intact through scoped styling and a separate existing-theme branch. Verify shared semantic and zoom changes on Schedule.
- No business/schema changes, security shortcuts, reduced validation, extra dependencies, automatic currency rounding, invented report totals or guessed product mapping.
- Read PRODUCT.md, AGENTS.md and the two design documents. PRODUCT.md is Schedule-focused; do not treat its calendar emphasis as the priority for Today.
- Report reproduced API limitations individually. Finish independent presentation work, but leave the affected gate open; do not silently broaden this frontend task into another backend build.

## Deliveries and estimated effort

| Delivery | Tasks | Work | Estimate |
|---|---|---|---:|
| Frontend 1 | 1-4 | Shared theme/navigation, Today, Reports, browsing consistency and gate | 5-8 hours |
| Frontend 2 | 5-8 | Money/forms, review/Closing, goods/trades, loading and final gate | 7-12 hours |
| Total | | Focused implementation plus verification | 12-20 hours |

These are planning estimates, not model runtime or quota guarantees. Do not skip checks to meet them. Existing Build 6 business logic is reused; wider functionality gaps require a separately stated scope.

## Task 1: scoped visual foundation and accessible navigation

**Files:** Modify `popcore_app/frontend/src/App.tsx`, `src/components/AppLayout.tsx`, `src/index.css`, `frontend/index.html`. Create `src/theme/operationsTheme.ts`, `src/styles/operations.css`, `popcore_app/tests/browser/check_frontend_usability.py`. Existing `check_foundation.py` remains the Schedule regression harness. All `src/` paths in this plan are relative to `popcore_app/frontend/`.

**Interfaces:** Export `operationsTheme: ThemeConfig` from the existing Ant Design type. Apply it only on non-Schedule routes. Keep the existing theme object for `/schedule` and existing Schedule-related CSS rules byte-for-byte. Give the relevant layout root `pc-operations` or `pc-schedule` so sidebar, content, popup and mobile styles do not leak. Ant Design popup context must follow the route theme, not rely solely on CSS ancestry.

- [ ] Capture current desktop/mobile Schedule and representative operations screenshots, route/role behavior and existing production resource list before changing styles.
- [ ] Extend a new check using `context_with_api`, `wait_for_server` and owned loopback Vite processes. Reproduce missing keyboard access to More/collapse, unlabelled store selection and mobile Reports wrapping; keep fixture data under the harness and output under `.local/frontend-improvement`.
- [ ] Implement the design's neutral surfaces, type/spacing scale, solid accent and restrained shadows in the scoped theme. Remove only operation-page inline styles that conflict with it; no CSS rewrite of unrelated components.
- [ ] Use links for navigation and buttons for More/collapse/account actions. Add accessible names, active-route semantics, focus visibility and Escape/focus return for drawers. Preserve all role-gated destinations and mobile Sales behavior.
- [ ] Normalize non-Schedule navigation labels, group existing desktop destinations without adding new routes, and map `/goods/*` to Inventory and sale subroutes to Sales. Preserve old URL entry points.
- [ ] Remove `maximum-scale=1.0` and `user-scalable=no` from the viewport tag; preserve device width and safe-area support. Ensure fixed navigation does not obscure the final field or action when scrolling or using the keyboard.
- [ ] Verify the visible theme on operations and unchanged Schedule appearance at 390/768/1440px, keyboard traversal and popup layering. Contrast: 4.5:1 body text, 3:1 large text/control boundaries where required; no color-only status.

**Browser contract example:**
```python
await page.get_by_role('button', name='More', exact=True).focus()
await page.keyboard.press('Enter')
await expect(page.get_by_role('dialog')).to_be_visible()
await page.keyboard.press('Escape')
await expect(page.get_by_role('button', name='More', exact=True)).to_be_focused()
```

**Gate:** A coherent operations shell is usable with keyboard/touch; Schedule visuals and existing interactions are preserved. Shared styling does not change operational permission or mutation payloads.

## Task 2: Today hierarchy and browsing consistency

**Files:** Modify `src/pages/Dashboard/index.tsx`, `MyShifts.tsx`, `todayPresentation.ts`, `todayPresentation.test.ts`; targeted presentation edits in `src/pages/Products/index.tsx`, `ProductSearchBar.tsx`, `ProductDetailDrawer.tsx`, `src/pages/Stock/index.tsx`; extend `check_frontend_usability.py` and `check_trades_today.py`.

**Interfaces:** Consume `TodayPayload.sections`, `authorized_stores`, `business_date`, `generated_at` and the existing shift selector. Derive display labels only; keep server-provided links and scope. Add presentation functions next to existing Today helpers only where they support nontrivial ordering/label mapping.

- [ ] Add fixtures for staff with another-store shift, manager with exceptions and zero store work, viewer, no access, unknown store label, long bilingual task names and failed refresh. Assert My Shifts is before operational tasks.
- [ ] Replace generic “Open”/raw-type descriptions with specific action labels derived from known `type` and `status`; keep useful record numbers in secondary text. Unknown types retain a readable fallback and their original destination.
- [ ] Compact empty sections without converting missing, inaccessible or failed data into success. Put actionable manager work ahead of empty status. Only use urgency/assignment supported by payload facts.
- [ ] Keep shifts independent of operational store selection and refresh on focus. Show updated time and honest stale state. Discard late old-account/store responses before showing new content.
- [ ] Standardize browsing page headings, search, filters, row density and secondary action menus. Keep product search and physical Inventory distinct, preserve existing imports/edit permissions and native units. Do not redesign catalog forms or invent new bulk operations.
- [ ] Verify long labels do not obscure actions; clear filters restores the existing result scope. Product/stock selection and search behavior remain intact. Mobile Today reveals assigned shift and first available work item without large empty-card padding.

**Gate:** Staff see their shifts first; each permitted task has a recognizable action/store; empty sections are quiet; product/inventory browsing remains functional and readable.

## Task 3: readable Reports with correct filter and page behavior

**Files:** Modify `src/pages/Reports/index.tsx`, `src/api/reports.ts`; create `src/pages/Reports/reportPresentation.ts`, `reportPresentation.test.ts`, `src/utils/money.ts`, `money.test.ts`; modify `frontend/package.json` only to include these Node tests; extend `check_frontend_usability.py` and the real `check_release_pilot.py`.

**Interfaces:** Existing `getReport(name, params)` and `downloadReport(name, params)` retain their endpoints. Define local `ReportFilters = {store_code:string; from?:string; to?:string}` and `ReportQuery = ReportFilters & {page:number; page_size:number}`. `formatCents(value:number|null|undefined):string` returns currency or “Unknown”. Do not add a report-builder abstraction.

Report-specific display contract:

| Report | Main columns/context | Existing destination |
|---|---|---|
| inventory | SKU, native unit, location, disposition, quantity | Existing product detail only if a supported entry point exists; otherwise plain product reference |
| movements | Date, document, kind, item/unit, quantity, from/to | Supported existing source route only; no invented movement page |
| goods-exceptions | Receipt, store/date, shipment reference, status | `/goods/receiving?receipt_id=ID` after verifying resume support |
| sales | Store, series/design, native unit, quantity, incomplete identity; API known-money summary separately | Existing product detail, never a fabricated sale ID for aggregate rows |
| tenders | Named tender, recorded, verified, refunds, pending and unknown counts | No fabricated product-by-tender allocation |
| evidence-exceptions | Sale/payment/evidence reference, review state | `/sales/documents/ID`; no public evidence path |
| cash-variance | Closing/date, state, expected/count/variance | `/closing?closing_id=ID` |
| count-discrepancies | Count/date, item/unit, expected/observed/difference | `/goods/counts?count_id=ID` after verifying resume support |
| closed-days | Closing/date, saved close reference, later-adjustment count | Read-only saved closing view; task 6 completes its presentation |

- [ ] Test a dated 125-row report: page 1 requests 50 records; page 2 requests a different server page; `total_rows=125` controls pagination. Changing store/report/date resets to page 1. CSV gets the same dates and store, not the current page slice.
- [ ] Add From/To Apply/Clear controls with invalid-range validation and query-string persistence. Inventory is current stock and hides/disables historical date controls with a clear label. Validate report names against the existing nine-name allowlist.
- [ ] Replace arbitrary Object.keys columns with the table above, field-specific currency/unit/status formatting and valid links. Keep document IDs distinct from money. Display API-provided completeness warnings and known totals; do not sum a page as an overall total.
- [ ] Use readable minimum widths and local scrolling for wide tables; mobile common reports show key facts with a reachable details action. Do not let strings wrap character-by-character. Tables retain header/row semantics and meaningful names.
- [ ] Keep data tied to its request identity: store, role, user, report, dates and page. Clear old data on permission/context change, cancel or ignore delayed responses and disable export until context is valid. A failed report must not show the previous store's rows under the new heading.
- [ ] Show the existing 413 export limit as “Narrow the date range to export 500 rows or fewer.” Preserve authenticated blob download, CSV escaping, no-store behavior and unknown money semantics.
- [ ] Test real-service report filtering/export parameters in the existing disposable pilot for supported report facts; mock tests separately exercise large pages and narrow layouts. An unavailable resume route is an explicit failed link gate, not a simulated success.

**Test contract examples:**
```ts
assert.equal(formatCents(4520), '$45.20')
assert.equal(formatCents(0), '$0.00')
assert.equal(formatCents(null), 'Unknown')
```
```python
await page.get_by_role('button', name='Next Page').click()
# Inspect captured outgoing request parameters: page=2, page_size=50.
# With dates applied, export must include identical from/to and store_code.
await expect(page.get_by_role('columnheader', name='Quantity', exact=True)).to_be_visible()
```

**Gate:** Report values and labels remain readable at 390px; filters, paging, export and permissions agree; unknown amounts are visible. No misleading link or fabricated summary.

## Task 4: Frontend 1 acceptance checkpoint

**Files:** Extend `check_frontend_usability.py` and relevant existing browser fixtures; create `docs/frontend-usability.md` recording scope, test results and evidence paths. Update only verified commands in AGENTS.md if a new command is introduced.

- [ ] Run the shared shell/Today/report/browsing cases at 390/768/1440px and actual 200% browser zoom where supported; record any fallback instead of labelling CSS zoom as full accessibility proof.
- [ ] Inspect screenshot content, not only document width. Check report cell widths/readability, visible actions, text contrast, navigation focus, mixed Chinese/English text, empty/error and all-role cases.
- [ ] Run Node tests, TypeScript/isolated production build, foundation, inventory core, trades/Today, new usability and touched real-service pilot checks. Add meaningful assertions for reproduced defects, not tests that mirror style constants.
- [ ] Compare Schedule source/critical CSS with the execution baseline and review matching screenshots. Run Schedule behavior/role/export checks after shared changes.
- [ ] Document Frontend 1 completion, remaining Frontend 2 work and any failed local gate. Do not declare completion if report readability or role scope is unresolved.

**Gate:** Frontend 1 is independently usable and verified. Proceed to Frontend 2 only if requested scope and remaining usage permit it.

## Task 5: dollar inputs and straightforward Sale Entry

**Files:** Extend `src/utils/money.ts`, `money.test.ts`; modify `src/pages/Sales/Entry.tsx`, `PaymentEvidence.tsx`, `SaleDocument.tsx` money controls; optionally create `src/components/MoneyInput.tsx` only for the genuinely shared dollar-entry behavior; extend `check_store_day.py`, `check_frontend_usability.py`, `check_release_pilot.py`.

**Interfaces:** `parseDollars(value:string):number|null` parses optional currency input; blank returns null, explicit zero returns 0, malformed/overprecision/unsafe amounts throw a validation error. Use decimal-string splitting and integer arithmetic rather than `parseFloat(value)*100`. Field-specific rules enforce positive/required/signed restrictions; denomination quantities remain integers. API names and integer-cent payloads remain unchanged.

- [ ] Write Node cases for blank, 0, 0.01, 12.30, 45.20, leading/trailing spaces, malformed text, exponent input, more than two decimals and unsafe integer bounds. Do not silently round or turn missing into zero.
- [ ] Introduce labelled `$` entry with decimal keyboard and inline validation; preserve input text during editing. Display the submitted amount for review and apply the same converter to refunds and Closing coin/event amounts in task 6.
- [ ] Group receipt/date, item/quantity and payments using the existing form. Show all five payment choices but only the selected amount fields; “Add payment method” supports split tender. No assumed tender or auto-filled amount.
- [ ] Preserve draft, post, retry and allocation flow. When a request outcome is unresolved, keep its key and immutable body until reconciled; do not reuse it for edited payment amounts.
- [ ] Keep upload UI simple: selected image preview, upload progress, saved/error state and clear return to sale. Read/preview private files only through existing authorized mechanisms; clean up object URLs on replacement/unmount.
- [ ] Verify `$45.20` sends 4520 cents, `$0.00` remains 0 and blank fields retain their existing null/omitted semantics; all five tenders and split payment survive submit, retry and reload of saved work.

**Test examples:**
```ts
assert.equal(parseDollars(''), null)
assert.equal(parseDollars('0.00'), 0)
assert.equal(parseDollars('45.20'), 4520)
assert.equal(parseDollars('0.01'), 1)
assert.throws(() => parseDollars('1.005'))
assert.throws(() => parseDollars('1e3'))
```

**Gate:** Staff enter normal currency without changing recorded financial facts; saved/retried operations still occur once.

## Task 6: focused Sale Review and Closing

**Files:** Modify `src/pages/Sales/SaleDocument.tsx`, `src/pages/Closing/index.tsx`, and their API types only as needed to reflect existing responses; extend `check_store_day.py`, `check_frontend_usability.py`, `check_release_pilot.py`. Use the shared money converter from task 5.

- [ ] Reorder Sale Review: status/recorded items and amounts -> pending payment/evidence -> stock resolution when needed -> source/history/return/refund actions. Make secondary operations explicit expansions rather than simultaneous empty forms. Preserve all current capabilities.
- [ ] Present Verify/Reject, Refund and Physical return as different intents. Keep exact item, amount, unit, disposition, reason and resulting history visible. No condition decision automatically refunds or changes stock.
- [ ] Replace product-ID inputs with existing scoped verified product lookup; show name/SKU/unit and require deliberate mapping. Preserve stock versions, open-set constraints and ambiguous-identity blocking.
- [ ] Implement compact Closing sections with real status: source tasks, cash, review. Keep blockers with actionable descriptions; raw codes may appear in secondary diagnostic details. Do not claim a count/restock is complete from the presence of a link.
- [ ] Preserve `closing_id` on navigation/reload and a safe return link from source work. Verify actual receipt/count resume support; report missing source contracts separately instead of losing drafts or faking completion.
- [ ] Keep cash inputs mounted or form-managed while sections collapse. Require actual denomination counts and opening/retained coins including explicit zero. Preserve the $650 retained-bill rule and backend-calculated reconciliation; no duplicate cash-event creation.
- [ ] Closed records show saved facts read-only; returned drafts show the return reason and resume correction. Hard blockers remain blocking; managers supply a separate reason for every accepted exception. Old source tokens require refresh/recount.
- [ ] Clear/disable stale document actions on user, role, route ID or store mismatch and ignore delayed responses. Recoverable errors preserve typed work. Test source changes between load and submit, rejected approvals, double-click/lost response and safe refresh.
- [ ] Exercise dollar entry, evidence review and supported closing stages against the disposable real API. Existing backend closing/refund tests verify invariants; do not call a mock-only interaction full integration proof.

**Gate:** A staff member can identify the next closing action and a manager can review exceptions without technical IDs or losing required facts. Closed snapshots and operation counts remain unchanged by presentation work.

## Task 7: goods, restock, trades and secondary screens

**Files:** Targeted edits in `src/pages/Stock/Goods.tsx`, `Receiving.tsx`, `Transfers.tsx`, `Counts.tsx`; `src/pages/Restock/index.tsx`, `PickingStep.tsx`, `ReceivingStep.tsx`; `src/pages/Trades/index.tsx`, `TradeCase.tsx`; existing `src/components/OperationScanInput.tsx`. Presentation-only consistency review of `src/pages/Sales/index.tsx`, `DayDetail.tsx`, `src/pages/Settings/index.tsx`, `src/pages/Users/index.tsx`, `src/pages/Unauthorized.tsx`, `src/pages/NotFound.tsx`. Tests: goods, inventory, trades and usability browser harnesses.

- [ ] Apply the same page title/context/action layout. Read-only details use normal rows; active forms use labelled controls. Show store/location and native units next to quantity, with plain labels for disposition and status.
- [ ] Keep primary scan/search input easy to reach. Distinguish operation purpose, repeated scan, unknown barcode and ambiguous candidates. Do not silently map an unknown barcode.
- [ ] Display requested/picked/dispatched/received/outstanding quantities distinctly in existing flows, including partial transfer/restock. Do not collapse them into a generic Completed badge or sum incompatible units.
- [ ] Trades show actual item identity and condition before action; slot selection/version, proof, replacements and condition-case reasons remain explicit. Existing technical version numbers move into secondary details.
- [ ] Adjust legacy Sales, Settings and employee-management presentation only when needed for consistency; do not modify Schedule/employee-setting business behavior, role policy, legacy totals or imports under this task.
- [ ] Preserve stable saved IDs and only supported resume routes. UI cleanup must not hide known API-only workflow gaps; record any such gap with its route/service and affected acceptance case.
- [ ] Verify mobile and keyboard completion of affected controls, form error retention, permissions and last-field/action visibility with fixed bottom navigation.

**Gate:** Existing goods/trade operations use a consistent, understandable layout while maintaining physical stock, provenance and role boundaries.

## Task 8: measured loading and final handoff

**Files:** Modify `frontend/index.html`, `src/pages/Sales/DayDetail.tsx`, and only necessary route loading in `src/App.tsx`; update `docs/frontend-usability.md`, browser harnesses and CI commands in `.github/workflows/checks.yml` when local checks pass. No production static replacement or candidate overwrite.

- [ ] Capture production entry AND preloaded/dependent chunks, route chunks, API request counts, cold/warm navigation and stated local network conditions. Baseline reviewed HTML requests nine local JS files: 1,904,222 raw bytes / about 592,234 gzip, plus external Chart.js; 1.22 MB was entry-file-only.
- [ ] Move the existing Chart.js script from global HTML to the Sales Day Detail lifecycle, preserving the pinned URL and consumer. Reuse a single script/promise for repeated visits; render a clear chart-loading/error state and keep the table usable. No new chart library or removal of its current chart.
- [ ] Preserve non-Schedule lazy routes and eager Schedule. Improve loading skeletons for operation routes without changing Schedule fallback behavior. Fix duplicate requests only when reproduced, preserving context/auth guards.
- [ ] Run the verification commands below and inspect screenshots for every affected page at 390/768/1440px, long bilingual text and 200% zoom. Verify keyboard/scanner behavior, four roles, store selection, unknown values, private evidence and stale requests. List real-device/live checks separately.
- [ ] Inspect built asset references and diff hygiene. Compare Schedule and committed static to execution baseline, and preserve prior candidate as Build 6 evidence. A newly built UI does not update that candidate automatically.
- [ ] Record exact changed files, test counts, screenshot paths, observed performance, unresolved issues, current Git remote/branch/HEAD/dirty state and remaining weekly quota. No completion claim for failed readability, money, scope or Schedule gates.

**Gate:** Both deliveries match the design and pass their checks. No claim of complete Build 6 store-day/Linux/live validation without that separate evidence.

## Verification commands

Use PowerShell in `D:\dev\POPCORE`; all generated output remains local and ignored. New harness/test paths below must be created in their named tasks before running them.

```powershell
$env:TEMP = 'D:\dev\POPCORE\.local\tmp'
$env:TMP = $env:TEMP
$env:PLAYWRIGHT_BROWSERS_PATH = 'D:\dev\POPCORE\.local\ms-playwright'
$env:DISABLE_SCHEDULER = '1'
.\.venv\Scripts\python.exe popcore_app/tests/browser/check_frontend_usability.py
.\.venv\Scripts\python.exe popcore_app/tests/browser/check_foundation.py
.\.venv\Scripts\python.exe popcore_app/tests/browser/check_inventory_core.py
.\.venv\Scripts\python.exe popcore_app/tests/browser/check_goods_flow.py
.\.venv\Scripts\python.exe popcore_app/tests/browser/check_store_day.py
.\.venv\Scripts\python.exe popcore_app/tests/browser/check_trades_today.py
.\.venv\Scripts\python.exe popcore_app/tests/browser/check_release_pilot.py
Set-Location popcore_app\frontend
npm test
npm run build -- --outDir ../../.local/frontend-improvement/build
Set-Location ..\..
git diff --check
git diff --exit-code -- popcore_app/static popcore_app/frontend/src/pages/Schedule popcore_app/blueprints/schedule.py
git status --short --branch
```

Browser harnesses run sequentially because they share a test port. Each must refuse an occupied port, stop only its owned child processes and distinguish fixture screenshots from real-service evidence. The final git diff check proves tracked source preservation only when execution baseline confirms those paths started clean; use the captured baseline otherwise. Inspect Schedule-related sections of shared CSS and compare before/after screenshots separately.

Run focused backend payment/closing/report/goods tests if the real pilot identifies a contract problem; run the full backend suite if any authorized backend code eventually changes. A pure theme change does not require repeatedly rerunning unrelated backup/migration tests.

## Plan coverage review

| Requirement | Tasks |
|---|---|
| Elegant/minimal styling without a framework rewrite | 1, 2, 7 |
| Staff shifts first and role-specific Today | 2, 4 |
| Mobile-readable Reports and accurate export/paging | 3, 4 |
| Normal currency entry without changing cents/unknowns | 5, 6 |
| Clear review, cash count and safe exception handling | 6 |
| Goods/trades consistency without changing inventory rules | 7 |
| Keyboard, zoom, stale data and error recovery | 1-8 |
| Measured performance without misleading entry-only claims | 8 |
| Schedule, existing work and production assets preserved | 1, 4, 8 |
| Weekly stopping threshold and no unrequested release actions | All |
