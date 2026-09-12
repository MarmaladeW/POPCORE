# Builds 1–6 Frontend Alignment Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` to implement this plan task-by-task in the current task. Steps use checkbox (`- [ ]`) syntax. Do not delegate, commit, push, merge, or deploy unless separately authorized. This document is a plan, not execution approval.

**Goal:** Make POPCORE's frontend visibly and functionally match the existing Builds 1–6 operations model, without rewriting its backend or changing Schedule.

**Architecture:** Evolve existing React pages and Ant Design controls in place. Add an operations-scoped theme, a small exact-money helper, explicit report definitions and typed wrappers only for endpoints that already exist. Reuse existing browser fixtures and the real disposable Flask/SQLite pilot; keep release assets unchanged until the intentional release-build task.

**Tech Stack:** React 18, TypeScript, Ant Design, existing Tailwind/Zustand/React Router, Vite, Node's test runner, Python unittest and Playwright. No new runtime or test framework.

**Spec:** `docs/superpowers/specs/2026-09-11-builds-1-6-frontend-alignment-design.md`.

## Global constraints

- Work from the verified integration branch in `/Users/marmalale/Developer/POPCORE`; new feature branches use `codex/`. Do not assume the already-inspected branch remains the correct starting point.
- Preserve the existing untracked PDF and both September 9 frontend proposals. Do not carry over that older plan's Windows paths, branch baseline or session-specific limits.
- Reuse existing dependencies and API contracts. No Schedule source/layout/theme/behavior changes. Shared shell/accessibility changes must pass Schedule regressions.
- Operations palette: `#F7F8FA`, `#FFFFFF`, `#20242D`, `#596273`, `#DDE2EA`, accent `#4F46E5`. System font with Chinese fallbacks; spacing 4/8/12/16/24/32px; mobile padding 16px, desktop 24px; forms 720–960px.
- Preserve role and inventory-store scope, native box/set/piece units, ambiguity, protected history, opening restrictions, provenance, stable request keys and API-returned versions/source tokens.
- Dollars in UI, exact integer cents at API boundary. Unknown is not zero. Refunds and physical returns remain separate.
- Scope changes invalidate old data and pending UI; do not mislabel resumed documents with the selected store. Keep personal shifts store-independent.
- No opening-stock command, production data, real transaction, global package installation, new auth bypass, or deployment during implementation checks.
- Use isolated verification builds until Task 9. Preserve `frontend/.env.production`, `package-lock.json`, and `static/` except the explicitly reviewed release artifact.

## Baseline findings and execution order

Inspected HEAD: `97c123a427c2b01c9e39bfffd76efb54ea3dd34f` on `codex/builds-1-6`; origin verified. No tracked modifications. Node on this Mac reports v26.8.2; CI uses Node 24 and Python 3.13. A ready project Python environment was not established in this planning pass. Do not assume the Windows environment migrated into an executable Mac environment.

| Finding in current source | Owning task |
| --- | --- |
| Dark inline shell, clickable div controls, zoom lock, incorrect child-route selection | 1 |
| Today raw types/Store IDs/generic Open links and large empty cards | 2 |
| First-page-only product selectors; need explicit native-unit/mode/readiness presentation | 2, 4 |
| Reports infer columns from objects, locally paginate one server page, export omits dates | 3 |
| Goods/Restock query-string resume links are not consumed by their current pages | 4 |
| Receiving combines draft creation and posting behind a button labelled Review | 4 |
| Transfer controls use version numbers instead of lifecycle/remaining-quantity facts | 4 |
| Sale-post success can be shown alongside unsaved payment facts; cents inputs leak backend units | 5 |
| Sale review exposes numeric product IDs, no authenticated evidence preview | 5 |
| Closing infers step progress; resumed closed state does not suppress all mutation forms | 6 |
| Trades generate new idempotency keys per API call and ask for a sale version | 7 |
| Committed static HTML references older hashed assets; isolated builds do not update deployment | 9 |

Delivery A: Tasks 1–3, a coherent visible foundation. Delivery B: Tasks 4–8, transactional workflows and complete verification. Task 9 is release preparation after source review, followed by a separately authorized deployment.

Dependencies: 1 → 2 → 3; 2 → 4 → 5 → 6; 4/5 → 7; all → 8 → 9. Review each task before moving on; do not add a second application framework or a generic workflow engine.

## Task 1: Establish the baseline and operations shell

**Files:** Modify `popcore_app/frontend/src/App.tsx`, `src/components/AppLayout.tsx`, `frontend/index.html`; create `src/theme/operations.ts`, `src/styles/operations.css` under `popcore_app/frontend/`. Extend `popcore_app/tests/browser/check_foundation.py`. Update `PRODUCT.md` only to add operations context while preserving its Schedule guidance.

**Interfaces:** Existing routes/RoleRoute remain unchanged. Export `operationsTheme: ThemeConfig` from `src/theme/operations.ts`. Use an operations-only wrapper/class and route-sensitive provider; Schedule retains the current theme. Semantic fixes to shared controls apply without changing Schedule styling or behavior.

- [x] Re-read AGENTS.md, the spec, the six build docs and the current diff. Record actual branch/remote/source SHA before editing; if starting implementation after integration, verify current main first and create the authorized `codex/` branch there. Preserve all unrelated changes.

```sh
git status --short --branch
git remote -v
git diff --stat
git rev-parse HEAD
node --version
npm --prefix popcore_app/frontend test
```

- [x] Resolve the Mac test environment before baseline browser checks. Reuse an existing verified project interpreter if present; otherwise create a local environment under `.local/frontend-venv` with Python 3.13 and install `requirements-dev.txt` without changing lockfiles or globally installing. Do not apply Windows/Linux constraints to macOS by assumption. CI's Linux/Windows constrained environments remain the platform authority. Keep scheduler disabled and temporary/browser output local. Record the interpreter path and baseline failures in the eventual verification note.
- [x] Add a failing keyboard/navigation check to the existing foundation harness. Example body, using its fixture-backed `page`:

```python
await page.goto(BASE + '/goods/receiving')
await expect(page.get_by_role('link', name='Inventory', exact=True)).to_have_attribute('aria-current', 'page')
await page.get_by_role('button', name='More', exact=True).focus()
await page.keyboard.press('Enter')
await expect(page.get_by_role('dialog')).to_be_visible()
await page.keyboard.press('Escape')
await expect(page.get_by_role('button', name='More', exact=True)).to_be_focused()
```

- [x] Replace action divs with buttons, navigation divs with links, and label the store selector/account/collapse controls. More has a title, close affordance, focus return and safe-area padding. Group desktop navigation and preserve mobile's four tabs + More. Match child routes explicitly; retain every authorized existing destination.
- [x] Apply the operations theme without global `.ant-*` overrides. Use the existing ConfigProvider/Ant Design context for popup content; do not assume portals inherit ancestor CSS. Keep Schedule's existing theme and shell appearance intact.

```tsx
// Existing route/layout composition; no new routing architecture.
const isSchedule = pathname === '/schedule' || pathname.startsWith('/schedule/')
// Schedule uses the existing provider tokens; operations use operationsTheme.
// Place the .operations class only on operations layout content.
```

- [x] Remove `maximum-scale=1.0, user-scalable=no` from source HTML, retaining `viewport-fit=cover`. Do not manually edit `static/index.html` now. Use system fonts, compact page headers and consistent spacing; no global calendar CSS cleanup.
- [x] Run foundation checks at 390/768/1440px, keyboard traversal and Schedule baseline comparison; run `npm test` and the isolated build. Capture before/after evidence under `.local/frontend-alignment/`. Review the exact diff before moving on.

## Task 2: Make Today, Products and Inventory useful daily entry points

**Files:** Modify `src/pages/Dashboard/index.tsx`, `src/pages/Dashboard/MyShifts.tsx` only if required by an identified regression, `src/pages/Products/index.tsx`, `src/pages/Products/ProductDetailDrawer.tsx`, `src/pages/Stock/index.tsx`; use existing `src/api/today.ts`, `src/api/client.ts`, store and product search patterns. Extend `check_inventory_core.py` and `check_trades_today.py`.

**Interfaces:** Preserve `TodayPayload.sections`, `authorized_stores`, and `MyShifts`' all-store query. Native stock fields, identity data and `/inventory/locations` are display facts, not permission to initialize inventory. Existing `/products/search`, `/products/:id`, `/products/:id/inventory-identity` support explicit product selection/details; verify result shapes before reusing them in later forms.

- [x] Add a regression with a DT selected store and a personal MK shift: changing selected store leaves the MK personal shift visible. Assert a delayed first-scope Today response cannot overwrite the newer scope or reappear after a role downgrade.

```python
# Extend the existing Today fixture with a DT draft sale and the existing MK personal shift.
await page.goto(BASE + '/')
await expect(page.get_by_text('My shifts / 我的班次', exact=True)).to_be_visible()
await expect(page.get_by_role('link', name='Resume sale #41', exact=True)).to_have_attribute('href', '/sales/documents/41')
await expect(page.get_by_text('Store 1', exact=True)).to_have_count(0)
```

- [x] Map known work types/statuses to readable labels with a small page-local map; resolve store names from the response's authorized stores. Preserve IDs as useful document references. Show unknown codes conservatively rather than inventing a workflow state.
- [x] Keep My shifts first, then personal work, store work and authorized manager review. Replace oversized empty cards with compact messages. Add retry and last-success/refresh feedback; never show failure as “nothing needs attention.” Use existing request cancellation/sequence patterns, not a new query library.
- [x] Improve product/stock rows and mobile detail layout: readable bilingual name/SKU, identity label, native unit, location/disposition and separate quantities. Keep long names readable; prevent whole-page horizontal overflow. Mode/readiness warnings must come from actual API facts and keep legacy paths distinguishable.
- [x] Reuse server search/pagination patterns for subsequent transaction selectors. Keep selected item details independently of the current results page; exact scans/resumed product IDs must resolve even outside the first page. Never infer identity from display names or autocomplete the first ambiguous match.
- [x] Keep business dates store-local using the existing `torontoDate()` helper where an operations form currently uses the device's date. Do not change Schedule date handling. Verify that a device outside Toronto still proposes the Toronto business date and that an explicit user-selected date is preserved.
- [x] Verify viewer/staff/manager/admin and DT/MK/ALL behavior, legacy/readiness/403/error states, an item outside the first 500, and no opening-command request. Update the relevant browser checks without weakening stock/provenance assertions.

## Task 3: Replace generic reports with an explicit, trustworthy reporting UI

**Files:** Modify `src/pages/Reports/index.tsx`, `src/api/reports.ts`; create `src/pages/Reports/definitions.ts`, `src/lib/money.ts`, `src/lib/money.test.ts`. Add the explicit money-test path to `frontend/package.json`'s existing test command; retain Schedule/Dashboard tests. Extend `check_store_day.py` and `check_release_pilot.py` for formatted reports.

**Interfaces:** Export `parseMoneyToCents(value: string): number | null` and `formatCents(value: number | null | undefined): string`. Blank parses to null, nonnegative exact decimal dollars to safe integer cents; invalid input throws `RangeError`. Formatting supports signed integer cent deltas, and null/undefined yields `Unknown`. Export `reportDefinitions` keyed by the existing `ReportName` union; do not derive columns from returned row keys.

- [x] Write the exact-money test first and run it to confirm failure:

```ts
import assert from 'node:assert/strict'
import test from 'node:test'
import { parseMoneyToCents, formatCents } from './money.ts'
test('money preserves unknown, zero and exact cents', () => {
  assert.equal(parseMoneyToCents(''), null)
  assert.equal(parseMoneyToCents('0.00'), 0)
  assert.equal(parseMoneyToCents('12.30'), 1230)
  assert.equal(parseMoneyToCents(' 12.3 '), 1230)
  for (const value of ['-1', '1.001', '1e2', 'NaN', '1,200', '90071992547409.92']) {
    assert.throws(() => parseMoneyToCents(value), RangeError)
  }
  assert.equal(formatCents(null), 'Unknown')
  assert.equal(formatCents(0), '$0.00')
  assert.equal(formatCents(-125), '-$1.25')
})
```

- [x] Implement parsing from decimal string parts, using integer/BigInt arithmetic and checking against `Number.MAX_SAFE_INTEGER` before conversion. No `Math.round(parseFloat(value) * 100)`. Use `Intl.NumberFormat` with an explicit dollar display policy for known signed cents.
- [x] Add a browser regression with 125 server rows: page 1 requests 50, page 2 requests 50 and different rows; total remains 125. Export retains store/from/to but omits page/page_size. A 413 export response shows the actionable limit rather than downloading its error JSON.
- [x] Implement the following explicit columns using the existing backend field names. Resolve display names only from authorized data; otherwise use a clearly labelled reference, never a guessed name.

| Report | Columns / summary | Record link |
| --- | --- | --- |
| inventory | Store, SKU, location, disposition, quantity + stock_unit | No invented document link; label as current inventory and omit dates |
| movements | Business date, kind, product reference, quantity + native_unit, movement locations/dispositions | No link to a sale merely because an ID exists |
| goods-exceptions | Receipt reference, store, date, status, shipment_reference | `/goods/receiving?receipt_id=<id>` |
| sales | Store, series/design or unverified_product, quantity + native_unit, identity completeness | Aggregated rows do not represent one sale; no sale link |
| tenders | Tender, recorded_cents, verified_cents, refund_cents, unknown_count, pending_count | None |
| evidence-exceptions | Evidence, payment, store, status, created_at | `/sales/documents/<sale_id>` |
| cash-variance | Store/date/status, expected/count/variance dollars | `/closing?closing_id=<closing_id>` |
| count-discrepancies | Store/date, product, expected/observed/discrepancy + native_unit | `/goods/counts?count_id=<count_id>` |
| closed-days | Store/date, closed timestamp, later_adjustment_count | `/closing?closing_id=<closing_id>`; do not print snapshot_json |

- [x] Bind report/date/page to validated state, accepting only existing report names and positive page values. Preserve URL/back navigation, reset page on filters, reject inverted dates. Fetch with `{store_code, from, to, page, page_size: 50}` where dates apply; configure Ant Design pagination with `total_rows`, never slice the returned page again.
- [x] Show `known_gross_cents` only as known gross when incomplete; show `incomplete_count`/`gross_complete`. Do not sum mixed units or infer money from prices. Clear old-scope results during load/error and ignore stale responses. Show loading, empty, error and denied states.
- [x] Preserve authenticated CSV download and decode blob errors when needed. Run money tests, report regressions and the isolated build. Review Delivery A screenshots for Today, Inventory and Reports before continuing.

## Task 4: Connect and clarify goods, restock and count workflows

**Files:** Modify `src/pages/Stock/Goods.tsx`, `Receiving.tsx`, `Transfers.tsx`, `Counts.tsx`, `src/api/goods.ts`, `src/pages/Restock/index.tsx`, `SessionModal.tsx`, `PickingStep.tsx`, `ReceivingStep.tsx`. Change `RequestStep.tsx`, `HistoryTab.tsx`, `BestsellerManage.tsx` only where labels/actions or existing target controls require it. Extend `check_goods_flow.py`; read `goods_operations.py` and `blueprints/goods.py` as contracts, not automatic backend-edit scope.

**Interfaces:** Add typed detail wrappers for `GET /goods/receipts/:id`, `/goods/transfers/:id`, `/goods/counts/:id`; preserve API `id`, `version`, `status`, `lines` and native-unit fields. Read `receipt_id`, `transfer_id`, `count_id`, and Restock `session_id` from existing query strings. Use receipt PATCH/cancel, transfer actions and count return only with their real endpoint bodies. Normalize receipt `unit` versus returned `native_unit` at the boundary, not by guessing.

- [x] Add resume tests that seed receipt 31, transfer 51, count 71 and a restock session. Direct navigation renders saved facts, performs GET, and performs no POST merely on opening the route.

```python
await page.goto(BASE + '/goods/transfers?transfer_id=51')
await expect(page.get_by_text('Transfer #51', exact=True)).to_be_visible()
await expect(page.get_by_text('Outstanding: 3 pieces', exact=True)).to_be_visible()
# Fixture: requested=5, dispatched=5, received=2, returned=0, loss=0.
# A receive request for 4 must not be sent; receiving 3 uses the saved version.
```

- [x] Fetch details with the same stale-response protection as Today. Display the document's actual scope even under ALL or a different selected store; make changing scope explicit and only allow actions for authorized locations. Invalid IDs and 403/404 must not show a new-document form as though the original record never existed.
- [x] Split receiving into editable input → saved draft review → post → receipt result. Support existing multi-line receipt facts and editable drafts through the existing PATCH API. Show expected/actual saleable/damaged/hold quantities and discrepancy notes. Preserve draft ID and exact post intent on uncertain results; do not create a second draft. Exact scans increment only the verified product/native unit, unknown/ambiguous scans do not mutate quantities.
- [x] Replace transfer `version === 1` / `version > 1` action rules with saved status and remaining quantities. Use typed actions `dispatch | receive | return | resolve_loss | short_close`; creation manager-only, loss manager-only, receive scoped to destination, other actions to source. Display explicit effects before confirmation:

```ts
// Existing transfer endpoint payload; use the latest fetched document.
{ expected_version: transfer.version,
  lines: [{ line_no: 1, quantity: 3, disposition: 'saleable' }],
  reason: 'Delivery discrepancy reviewed' }
// API detail provides outstanding_transit; do not infer it from local form inputs.
```

- [x] Make Restock deep links open the existing SessionModal. Preserve request/picking/receiving ownership and completed history; do not auto-close a resumed completed document before it can be read. Reuse delivery remaining quantities/version facts and keep stock effects explicit.
- [x] Show counts as observations followed by submission/review; render submitted/approved lines read-only and display server stale/provenance conflicts. Manager return uses the existing endpoint and shows the returned record/new recount ID. Do not label copied observations as newly counted.
- [x] Document the verified recount limitation: no PATCH count-observation endpoint exists. No editable recount or automatic reapproval is included in this frontend-only task. A full editable recount requires a separate, reviewed backend change with immutable-history/version tests. Mark this limitation in handoff rather than hiding it.
- [x] Surface existing floor suggestions read-only in Restock. GET `/goods/restock-suggestions?location_id=<floor>` returns min/max, floor quantity, available back quantity, outstanding inbound and suggested quantity. It does not return target version, although PUT `/goods/targets` requires `expected_version`; therefore editing existing targets is a separately scoped read-contract prerequisite, not a control backed by guessed version zero. Suggestions never post stock or automatically create shipments. Missing MT/back-stock configuration is an explicit unavailable state.
- [x] Test exact/unknown/ambiguous scan, partial delivery, return/loss/short close, count 409, protected history, store change, double click and lost response. Verify no generic command accesses transit and no protected open-set quantity is silently used. Run goods/inventory browser checks plus existing goods transaction tests in the verified environment.

## Task 5: Make sale entry, payment evidence and sale review safe and readable

**Files:** Modify `src/pages/Sales/Entry.tsx`, `SaleDocument.tsx`, `PaymentEvidence.tsx`, `src/api/salesDocuments.ts`; reuse Task 3's `src/lib/money.ts` and Task 2's product-search approach. Extend `check_store_day.py` and `check_release_pilot.py`. Add no generic form/workflow framework.

**Interfaces:** Existing `SaleInput`/`SaleResult` and mutations remain authoritative. Read private payment image content via authenticated `GET /payment-evidence/:id/content`. Use `allocateSale`'s existing `open_set_id` support only with verified provenance provided by the current inventory APIs. No new payment context endpoint is assumed.

- [x] Add a regression for successful sale posting followed by failed payment saving: the UI says the sale is recorded but payment facts are not confirmed; retry replays only the identical payment request and does not repost stock.

```python
await page.get_by_label('Actual collected total ($)').fill('20.00')
# Fixture the payment request to fail after sale posting, then succeed on replay.
await page.get_by_role('button', name='Record sale', exact=True).click()
await expect(page.get_by_text('Sale recorded; payment facts not confirmed.', exact=True)).to_be_visible()
await expect(page.get_by_role('button', name='Retry payment save', exact=True)).to_be_visible()
# Assert captured payment bodies and Idempotency-Key are equal across retries.
# Assert the sale-post call count remains one.
```

- [x] Group entry into source/date, product/quantity and actual money. Keep existing supported entry modes, all five tender types and split tender rows. Show dollar labels/prefixes with `inputMode="decimal"`, using exact parse/format helpers. Preserve optional unknown values, known zero, reduction/rounding semantics and server validation.
- [x] Show saved draft facts before Record sale: source, store/date, line quantities/native units, known/unknown totals, selected actual tenders and expected stock effect. Do not invent an allocation outcome before the backend confirms it.
- [x] Keep request key plus immutable submitted payload for each unresolved operation. Disable edits to that intent until its result is resolved; retry identical payload/key on uncertain transport results. For a confirmed 409, fetch fresh facts and require deliberate re-review before creating a new intent. After mutation, await detail refresh; if refresh fails, state “Saved; refresh needed” and block dependent versioned actions. Do not introduce blind automatic retry or an offline queue.
- [x] During a mutation, prevent accidental store/record changes. For unsaved edits, offer a deliberate discard warning. For an unconfirmed mutation, require resolution or an explicit reconciliation-pending acknowledgement before leaving; never silently discard its replay key and offer a fresh duplicate operation. Clear displayed scoped data on an authorized context/account change, and ignore delayed responses from the old context. Do not persist private payloads as a speculative offline queue. Preserve existing create/post/payment separation and avoid claiming everything saved because one step succeeded.
- [x] Replace raw product-ID allocation fields with verified search/details. The inspected routes do not expose a scoped list of available opened sets; do not claim a functioning open-set selector until that read contract is separately authorized and implemented. Keep allocation that requires unavailable provenance blocked with a clear explanation, not a fabricated ID or loose-stock fallback. Keep all manager decision reasons separate.
- [x] Display authenticated evidence previews with a visible pending/accepted/rejected status, loading/error handling and object URL cleanup on replacement/unmount/logout. Direct evidence-upload links remain valid without invented payment metadata; links opened from a sale can carry a sale reference that is verified by fetching that sale. Back navigation gets a safe Sales fallback rather than relying exclusively on `navigate(-1)`.
- [x] Separate payment verification/rejection, evidence review, monetary refund and physical stock return visually. Dollar refund fields convert to cents; physical returns display product/native unit/disposition and say they do not refund money. Disable competing mutations while awaiting confirmation.
- [x] Test unknown/zero/split tenders, 409/retry, lost response, denied evidence, allocation ambiguity, separate reasons, refund versus return and cross-store delayed results. Run exact-money tests, store-day checks and the real pilot; update its $20.00 labels/assertions while retaining database effect assertions.

## Task 6: Make closing a trustworthy review of the store day

**Files:** Modify `src/pages/Closing/index.tsx`, `src/api/closing.ts`; extend `check_store_day.py`, `check_release_pilot.py`. Consult `closing_operations.py` and `docs/sales-and-closing.md` without changing their rules.

**Interfaces:** Expand `ClosingSession.latest_cash_count` to the existing opening/retained coin and denomination fields. Include manager-only optional `snapshot`, `late_adjustments`, `tender_totals_cents` and `unknown_payment_ids` from GET closing. `snapshot` uses the existing saved counted/expected/variance/retained/removal facts and accepted exceptions, not recalculated current facts.

- [x] Add fixtures for draft, submitted, closed-manager and closed-staff; add a source-token conflict. A directly resumed closed session renders no editable declaration/count/sign-off controls:

```python
await page.goto(BASE + '/closing?closing_id=91')
await expect(page.get_by_text('Store day closed', exact=True)).to_be_visible()
await expect(page.get_by_role('button', name='Save cash count', exact=True)).to_have_count(0)
await expect(page.get_by_role('button', name='Close store day', exact=True)).to_have_count(0)
# Manager fixture includes snapshot + one late_adjustment; staff fixture omits both.
```

- [x] Replace the inferred five-step progress bar with source completeness, cash events/count, blockers and review sections using actual status/permissions. Keep a clear primary action for draft/submitted/closed. Missing staff-only review facts are not “zero blockers.”
- [x] Dollar-format opening/retained coins and cash events, preserving explicit required input. Denomination quantities stay integer counts; do not default unentered counts to counted zero. Restore saved values on resume. Show expected, counted, variance, retained float and removal with plain-language float-shortfall explanation.
- [x] Make each known blocker/exception readable with the correct source-document link when available. Do not mark all sources complete merely because one declaration is checked or a count exists. Manager acceptance needs one reason per returned exception; return-for-changes needs its own reason.
- [x] After mutation, refresh current session facts; on changed source token/version, explain the conflict and require review before retrying as a new intent. Retain identical payload/key for genuinely uncertain responses. Do not show optimistic signed-off success when the close response is unresolved.
- [x] On confirmed close, fetch and render the persisted closed detail. Managers see the immutable snapshot and a separate later-adjustments list; staff see only permitted closed facts. Adding new late-adjustment authoring controls is not required for this alignment.
- [x] Verify the $650 bill float plus explicit coins, unknown/unverified cash, stale source facts, separate exception reasons, return-to-draft, closed reload and no duplicate removal. Expand the real pilot through cash count, submit and manager sign-off with a database assertion for one snapshot and at most the expected single removal.

## Task 7: Align trades and condition cases with the real lifecycle

**Files:** Modify `src/pages/Trades/index.tsx`, `TradeCase.tsx`, `src/api/trades.ts`; extend `check_trades_today.py`. Read existing trade endpoints and tests before changing wrapper arguments.

**Interfaces:** Change the existing trade mutation wrappers to accept a caller-owned stable request key instead of generating one per call. Update every caller in the two pages together. Keep all existing endpoint bodies, slot/balance versions and proof requirements. Fetch a selected sale with existing `fetchSale(id)` to obtain its actual version; do not invent a search endpoint.

- [ ] Add a regression that simulates a lost swap response, then asserts identical request body and Idempotency-Key on retry. Include a confirmed slot-version conflict that requires refreshed facts and a new inspection/review where appropriate.

```ts
// Minimum wrapper change pattern; use the existing newRequestKey at the page's intent boundary.
export const swapTrade = async (id: number, body: object, key: string) =>
  (await client.post(`/trade-slots/${id}/swap`, body,
    { headers: { 'Idempotency-Key': key } })).data
```

- [ ] Group slot selection/current occupant, inspection/proof, swap confirmation and explicit replacement. Use known series/design names from setup/details, with labelled ID fallback. A successful sale/swap leaves the actual returned slot state visible; never automatically open replacement stock.
- [ ] Replace the manually entered sale version with a fetch-and-review step for an existing sale reference. Validate store/document applicability from fetched facts and let the backend remain authoritative. Show linked sale status before Sell current unit.
- [ ] Reset preserved form/inspection state on slot/store changes so a prior design or proof cannot leak into a different slot. Keep required proof, observed condition/disclosure, box/accessory checks and verified same-series design selection explicit.
- [ ] Condition cases show actual status, observed/disclosed condition, evidence and distinct reasoned decisions. Use existing private condition-evidence content endpoint if provided by the current response; otherwise retain protected metadata without manufacturing public image URLs. No automatic refund or physical return.
- [ ] Run trade/Today checks for empty/occupied slot, wrong series, missing proof, stale versions, replacement, evidence denial, separate case decision and repeated request. Confirm personal shifts remain unaffected.

## Task 8: Complete usability, integration and regression verification

**Files:** Extend existing browser checks; create `popcore_app/tests/browser/check_frontend_usability.py` only for cross-page layout/accessibility scenarios not already covered. Modify `.github/workflows/checks.yml` to run that check and retain existing jobs/artifacts. Save results in `docs/frontend-alignment-verification.md` during execution; update relevant build UI notes and `docs/build6-workflow-checklist.md` with tested scope/limitations.

**Interfaces:** Reuse `check_foundation.context_with_api`, `BASE`, `FRONTEND`, `HERE`, existing Playwright startup/cleanup patterns and test-only authentication config. The real pilot continues browser → Flask → SQLite, not fabricated API responses. Browser checks share port 5174 and must run sequentially unless explicitly isolated to different ports.

- [ ] Review screenshots at 390/768/1440px for Today, Products/Inventory, goods, sale entry/review, Closing, Trades, Reports and Schedule. Include long bilingual names, dense rows, blank/zero/unknown money, errors, ALL/DT/MK, all four roles and denied access. Review popup/dropdown/evidence previews as well as page content.
- [ ] Add the smallest reusable geometry check to the cross-page harness, plus keyboard, 200% browser zoom, touch/safe-area and reduced-motion checks. Device scale factor is not a substitute for browser zoom.

```python
overflow = await page.evaluate('document.documentElement.scrollWidth > window.innerWidth + 1')
assert not overflow, page.url
await expect(page.get_by_role('main')).to_be_visible()
# Tables may scroll inside a labelled region; the page itself must reflow.
```

- [ ] Keep focused browser/database assertions for all newly surfaced mutation flows. Extend the real disposable pilot beyond its original sale/payment/closing-start scope: receipt creation/post/resume, partial transfer lifecycle, reviewed count, sale/evidence/review, closing sign-off, and report resume links. Add trade/refund/return real-API scenarios only with isolated data and explicit effect assertions. Report any scenario not executed as unverified, never “covered by screenshots.”
- [ ] Run the full verification set with the interpreter selected in Task 1. The following uses `.local/frontend-venv/bin/python`; substitute only the actual verified equivalent and record it. No app import may use the real local database/upload directory.

```sh
.local/frontend-venv/bin/python -m pip check
DISABLE_SCHEDULER=1 .local/frontend-venv/bin/python -m unittest discover -s popcore_app/tests -v
.local/frontend-venv/bin/python popcore_app/tests/browser/check_foundation.py
.local/frontend-venv/bin/python popcore_app/tests/browser/check_inventory_core.py
.local/frontend-venv/bin/python popcore_app/tests/browser/check_goods_flow.py
.local/frontend-venv/bin/python popcore_app/tests/browser/check_store_day.py
.local/frontend-venv/bin/python popcore_app/tests/browser/check_trades_today.py
.local/frontend-venv/bin/python popcore_app/tests/browser/check_release_pilot.py
.local/frontend-venv/bin/python popcore_app/tests/browser/check_frontend_usability.py
npm --prefix popcore_app/frontend test
npm --prefix popcore_app/frontend run build -- --outDir ../../.local/frontend-build
git diff --check
git status --short --branch
```

- [ ] Preserve failure artifacts and compare baseline versus changed behavior. Confirm no Schedule source diff, no unexpected package-lock/environment/static changes, no test auth in production source, no leaked private fixtures. Review test changes for lost assertions; pass/fail is not determined only by renamed labels.
- [ ] Record exact source SHA, commands/exits, browser dimensions, fixture versus real-API evidence and known limitations. Explicitly retain the editable-recount, target-version and opened-set-choice prerequisites and live Auth0/device/opening-stock/recovery gates. If commit/push is later authorized, verify all existing Windows/Linux/backend/frontend/browser CI checks on that exact revision.

## Task 9: Prepare the actual frontend release and handoff

**Files:** Intentionally regenerate `popcore_app/static/` only after source acceptance; update `docs/release-and-recovery.md` and `docs/frontend-alignment-verification.md` with artifact and rollback evidence. No production mutation follows automatically.

**Interfaces:** `npm run build` uses production configuration and Vite's existing `../static` output with `emptyOutDir: true`. That replaces tracked release assets, unlike the isolated command. Flask serves this bundle. No hosting/layout migration or automatic `setup_production.sh` run is part of this task.

- [ ] Before generating the release, inspect tracked static files and the current diff, check that no user assets will be accidentally lost, and review public build-variable names without printing secret values. Record the previous release revision/assets so rollback can restore a coherent bundle, not only an HTML file.
- [ ] When release-asset preparation is approved, run the intentional build:

```sh
npm --prefix popcore_app/frontend run build
git diff --stat -- popcore_app/static
git diff --check
git status --short --branch
```

- [ ] Serve the generated production bundle through an isolated local Flask instance with a temporary DB/uploads and scheduler off. Check root and deep-link HTML plus referenced assets return successfully, verify the expected asset hashes, and ensure the bundle contains no fixture auth, local tokens, private images or source environment files. A protected Auth0 screen is not proof that live login is configured; distinguish bundle smoke from real sign-in.
- [ ] Write the release handoff: source revision, bundle hashes, verification results, intentional tracked-file list, known limitations, install prerequisites and reversible deploy steps. Do not change systemd/nginx layout or live data to ship CSS/JS.
- [ ] Stop for the user's release decision before commit/push/merge/deployment unless they have explicitly authorized those actions. After a separately authorized deployment, verify the site serves the new asset hashes, assets and deep links load, and authorized-role smoke checks succeed. Keep the prior coherent release available for rollback.

## Plan self-review

- Build 1: Tasks 1, 2, 5, 8; Build 2: Tasks 2, 4, 5; Build 3: Task 4; Build 4: Tasks 5, 6; Build 5: Tasks 2, 7; Build 6: Tasks 3, 6, 8, 9.
- Shared money contract is defined before consumers; report names match the current nine-name union. Existing routes and payload field names are retained.
- No new count-edit API, target version/opened-set lookup response, public evidence endpoint, report-level sale link, native-unit conversion or live-readiness result is assumed.
- Deliberate limits: no new framework, global store refactor, speculative offline support, whole-repo visual cleanup, Schedule redesign, or blanket backend rewrite.
- Planning deliverables are only this plan and its companion design. All implementation checkboxes start unchecked; test examples are proposed checks, not claims of execution.
