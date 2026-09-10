# Build 5: trades and role-specific Today implementation plan

> **For agentic workers:** Use superpowers:executing-plans task by task only after implementation is requested. Read AGENTS.md, the store design, the verified Build 4 handoff, and this plan first. This plan does not authorize subagents, commits, pushes, deployment, or live data access.

**Goal:** Complete same-series trading and condition cases, then make Today show each role its permitted work, with staff's own assigned shifts first.

**Architecture:** Extend the existing Flask/SQLite document and inventory transaction model for trade-owned items. Build one read-only role-filtered Today projection from existing source documents. Reuse the personal-shifts API without changing Schedule.

**Tech Stack:** Existing Flask, SQLite WAL, Auth0, React/TypeScript, Ant Design, Pillow, unittest, Node test runner, and Playwright. Use the existing D: environment; verify actual runtime versions at execution rather than replacing it to match an older setup note.

**Spec:** [Store design](../specs/2026-09-07-instore-website-design.md), sections 4, 6G, 7 and 10; [roadmap](2026-09-07-instore-website-plan.md), Phase E / Task 9 and the Today/navigation part of Task 10.

**Status:** Planning only, 2026-09-08. Proposed expansion of the agreed roadmap; no application code changed during this planning pass.

## Scope and boundaries

Build 5A is trades and condition history. Build 5B is Today and navigation. Build 6 completes operational reports, remaining review screens, interface proof, and local pilot/release preparation. External connectors, purchasing, wholesale, automated discounts, profit calculations, and new Schedule behavior remain outside these builds.

Use one active trade slot per verified series per store, as proposed in the store design. This is a proposed implementation rule, not a claim that legacy stickers encode a unique identity. Staff record how sticker/receipt proof was checked. Do not invent customer registration, sticker uniqueness, expiry, cash top-ups, monetary trade valuation, or eligibility for confirmed purchases.

## Global constraints

- Work in D:/dev/POPCORE. Keep environments, dependencies, test databases, private evidence, caches, and build output on D:.
- Preserve all prerequisite uncommitted work, existing IDs, staged server.log deletion, real environments/data, and committed popcore_app/static assets.
- Schedule source, calendar CSS, route behavior, employee settings, availability and exports remain unchanged. Reading getMyShifts is allowed.
- All mutations use synthetic data in tests and block external network calls. Set DISABLE_SCHEDULER=1 before app import; app.py performs migrations on import.
- Operations access comes from inventory_access and verified identity, independently of scheduling employee-store membership. Admin requires explicit store scope too.
- All Stores is read-only and includes only the current user's authorized stores. A personal shift at MK does not grant MK stock/financial access.
- Never infer financial amounts from quantity times current price or duplicate Build 4 allocation, payments, refunds, or closing.
- Use existing component styles, familiar controls, bilingual operational labels, visible focus, and readable statuses. No widget builder, new roles, task-assignment engine, or decorative redesign.
- Check weekly usage before implementation, between tasks, and before final proof. At 40% used / 60% remaining, stop starting new work and report the exact checkpoint. Never redeem a reset.
- Planning is authorization to create these documents only. Preserve the no-commit/no-push/no-deploy boundary during implementation unless separately authorized.

## Entry gate: source and proof that must be checked

Current branch at planning: codex/build-4-sales-payments-closing, HEAD 8630199ac780059f5cb185a85c6efd0ac4597b91, origin MarmaladeW/POPCORE. Builds 1–4 are uncommitted. Recheck before execution; do not switch away from prerequisite work or use a clean HEAD checkout that omits it.

The previous Build 4 run recorded 287 backend tests, 14 frontend tests, a separate production build, and review. This planning pass did not rerun those tests. Source inspection found these concrete integration points:

- goods_operations._begin/_replay/_remember and inventory_commands._post_inventory_in_transaction support one outer transaction. No network or image decoding inside it.
- product_series and products.series_id exist; use verified series IDs, not ip_series name matching.
- Inventory supports trade/hold/damaged/display dispositions, but does not yet own physical trade slots.
- open_set currently converts sealed sets to random boxes; it does not convert a random box into a known design. A trade opening needs an explicit composed consume/receipt conversion, not a fake one-to-one open_set command.
- sales_operations.post_sale currently owns its transaction and assumes saleable floor allocation. Direct sale of a trade unit requires a bounded internal transaction composition change.
- Dashboard currently requests legacy sales/stock summaries. Replace its aggregate requests for Today; do not merely hide revenue cards.
- GET /api/schedule/shifts/me filters by signed-in employee; getMyShifts({start, store_code:'ALL'}) is already exported by Schedule/scheduleApi.ts. The backend can create the employee record on first read; this plan does not alter that existing behavior.
- check_store_day.py is a frontend test with canned API responses. It does not prove a full real-backend store day. Retain it as UI proof and add the integrated proof in Build 6.
- Some existing manager actions are API-only; Today must link to existing authorized document views. Build 6 connects missing review controls before first-release acceptance.

Before changing code, capture source/static/Schedule hashes and run the relevant prerequisite tests. A reproduced prerequisite failure must be repaired narrowly or reported as blocking; do not mark it passed from old commentary.

## File map

Paths below are relative to the repository; proposed new paths are marked new.

| File | Responsibility |
| --- | --- |
| popcore_app/trade_operations.py (new) | Slot, inspection, swap, condition decision and transaction composition |
| popcore_app/blueprints/trades.py (new) | Scoped trade/condition APIs and attachment access |
| popcore_app/db.py | Additive slots, units, inspections, swaps, condition and evidence facts |
| popcore_app/inventory_commands.py | Reject generic writes to trade-owned quantities; internal composed posting only |
| popcore_app/sales_operations.py | Private sale-posting body and explicit trade-unit allocation; ordinary sales behavior retained |
| popcore_app/catalog_identity.py | Prevent relabeling referenced trade designs/series |
| popcore_app/payment_evidence.py; trade_operations.py | Reuse prepare_image; retain separate payment and condition storage/access |
| popcore_app/closing_operations.py | Include trade/condition source versions in close freshness and snapshots |
| popcore_app/blueprints/today.py (new) | Role-filtered Today projection and authorized store set |
| frontend/src/pages/Trades/index.tsx; TradeCase.tsx (new) | Inspection, swap, direct sale, replacement and condition screens |
| frontend/src/pages/Dashboard/index.tsx; MyShifts.tsx; todayPresentation.ts (last two new) | Today and personal shift ordering/date/error states |
| frontend/src/api/trades.ts; today.ts (new) | Typed requests and stable mutation keys |
| frontend/src/App.tsx; components/AppLayout.tsx | Today/Trades routes and agreed navigation |
| popcore_app/app.py; tests/support.py | Blueprint registration and isolated identities/evidence |
| tests/test_trades.py; test_trade_conditions.py; test_today_permissions.py; test_today_shifts.py (new) | Domain, permission and Schedule-read regressions |
| frontend/src/pages/Dashboard/todayPresentation.test.ts (new); frontend/package.json | Pure presentation tests using existing Node runner |
| tests/browser/check_trades_today.py (new); .github/workflows/checks.yml | UI regression gate |
| docs/trades-and-today.md (new) | Actual workflows, roles, proof limits and recovery |

Frontend and test shorthand paths in this table start under popcore_app/. Do not extract a generic document/evidence framework; share only genuinely common image decoding and the existing transaction body.

## Task 1 (5A): inspected trade units and one versioned slot

**Interfaces:** GET /api/trade-slots?store_id=<id>; GET /api/trade-slots/<id>; POST /api/trade-slots (manager, store + series); POST /api/trade-slots/<id>/open (staff, explicit physical opening); POST /api/trade-slots/<id>/inspections (staff). Every mutation carries Idempotency-Key and expected_version on an existing resource. Proposed service signatures:

~~~python
def create_slot(con, data: dict, *, actor: dict, request_key: str) -> dict: ...
def open_slot(con, slot_id: int, data: dict, *, actor: dict, request_key: str) -> dict: ...
def record_inspection(con, slot_id: int, data: dict, *, actor: dict, request_key: str) -> dict: ...
~~~

Each result identifies its slot and current version. Opening also returns event_id, unit_id and inventory_document_ids; inspection returns inspection_id and its accepted/rejected decision. Signatures describe interfaces; implementations belong to the later build.

**Data contract:**

| Table | Required facts and invariants |
| --- | --- |
| trade_slots | id, store_id, series_id, location_id, occupant_unit_id nullable, version; unique(store_id, series_id); floor location must belong to store |
| trade_units | id, verified design_product_id (piece), series snapshot, origin random_opening / inspected_trade / confirmed_purchase, eligibility eligible / ineligible, location/custody state, version; track only relevant individual units |
| trade_inspections | id, slot_id, incoming_unit_id, outgoing_unit_id snapshot, expected slot version, proof kind/reference/check notes, box/accessory checks, condition/disclosure, decision accepted/rejected, actor/time |
| trade_events | immutable event ID/type, slot version before/after, incoming/outgoing unit IDs, inspection ID, source inventory document IDs, actor/time/reason |
| condition_cases / condition_events | unit/sale-line reference, observed vs disclosed condition, open/resolved status and version, append-only staff note/manager decision |
| condition_evidence | case ID, private object ID, MIME/size, uploader/time; images separate from payment evidence |

Proof may be staff-reviewed legacy sticker, original receipt, or prior recorded trade. A previous digitally tracked unit reuses its identity; legacy proof does not create a fake retail receipt. Confirmed purchase origin is ineligible even if its catalog product is the same known design as an eligible opened random box. Unknown series/design or unclear proof remains unresolved; staff cannot force eligibility by changing a flag.

- [ ] Write failing tests for duplicate slot, unauthorized store, generic barcode ambiguity, confirmed-purchase rejection, missing proof/box/accessory/condition inputs, and immutable prior inspection.
- [ ] Add additive migrations and enforce one occupant/one custody location; retain inactive/history rows rather than deleting.
- [ ] Implement explicit opening: select one available random box, observed verified design from the same series, source location/provenance and balance versions. Inside one outer transaction, consume one box through the existing inventory body, receipt one design piece into trade disposition, insert unit/event and update slot. Both document identities derive from the same request key. A sealed set must first be explicitly opened through the existing goods flow.
- [ ] Reject generic inventory edits/count corrections to trade-owned balances unless the trade service supplies a private, server-validated context. Do not expose an allow_trade flag in request JSON. Count discrepancies affecting slots become manager condition/reconciliation cases.
- [ ] Reuse the existing reference-protection checks so catalog edits cannot change the historical series/form of referenced trade units.
- [ ] Run test_trades.py and inventory/provenance tests. Prove source shortage or a failure between consume/receipt rolls back documents, unit, slot and balances.

Fixture assertions:

~~~python
assert before_random_boxes == 5
assert after_open_random_boxes == 4
assert after_open_trade_pieces == 1
assert slot["occupant_unit_id"] == opened_unit_id
assert replay["event_id"] == first["event_id"]
assert opening_without_stock["code"] == "insufficient_stock"
~~~

**Gate:** One inspected physical unit can occupy a slot, and its stock origin is explainable without changing legacy sales or serializing ordinary inventory.

## Task 2 (5A): same-series swap and separate direct sale

**Interfaces:** POST /api/trade-slots/<id>/swap -> {slot_id, version, event_id, incoming_unit_id, outgoing_unit_id, inventory_document_ids}; POST /api/trade-slots/<id>/sale -> ordinary Build 4 sale result plus slot version/event. Functions swap_slot and sell_slot(con, slot_id, data, *, actor, request_key). The sale request names an existing reviewed draft sale and both sale/slot expected versions.

- [ ] Write failures for stale slot, two simultaneous swaps, same incoming unit used in two stores, mismatched series, rejected inspection, repeated same request key, and another sale competing for the occupant.
- [ ] Re-read slot, incoming custody, inspection and store scope inside BEGIN IMMEDIATE. An accepted inspection authorizes exactly its recorded incoming/outgoing pair at its slot version.
- [ ] Compose outgoing trade consume and incoming trade receipt with slot/custody changes in the same transaction. When designs match, still retain the two physical identities and event; total store trade quantity remains one. No financial facts are created for a swap.
- [ ] Keep the outgoing unit eligible and in customer custody so its later inspected trade can re-enter; never create multiple active custodians or reuse the same physical unit concurrently.
- [ ] Extract only the necessary private sale-posting body from post_sale; the public wrapper retains its existing transaction, authorization and replay behavior. sell_slot invokes the private body inside the trade transaction using the exact unit's trade disposition. A public sale request cannot forge this internal context or fall back to saleable stock.
- [ ] Record direct sale, immutable condition disclosure on its line, inventory consumption and empty slot together. Keep financial capture and payment verification under Build 4 rules. A direct confirmed-design purchase is not made trade-eligible by its former display origin.
- [ ] Replacement uses the separate /open action after physical opening. No stock leaves the slot empty with a clear replacement-needed status. Do not deduct replacement inventory when selling.
- [ ] Add trade events/condition versions to closing source facts; late trade activity must be visible as linked adjustment where it affects a closed day. This reads events; Closing never posts the trade again.
- [ ] Run test_trades.py, test_sales_posting.py, test_payments.py and test_closing.py.

~~~text
Swap slot A for incoming B in same series -> B in slot; A with customer and eligible.
Replay -> same event; no repeated receipt or consumption.
Trade A back after a new inspection -> valid; original retail history unchanged.
Two workers use slot version 4 -> one succeeds; one slot_stale; one physical swap.
Direct sale B -> one sale, one consumption, empty slot.
Replacement stock 0 -> no automatic opening; empty slot persists.
~~~

**Gate:** Swap and direct sale are mutually consistent, history is retained, repeat eligibility works, and replacement never creates stock.

## Task 3 (5A): condition evidence and usable trade screens

**Interfaces:** POST /api/condition-cases; GET /api/condition-cases/<id>; POST /<id>/evidence; GET /api/condition-evidence/<id>/content; POST /api/condition-cases/<id>/decision (manager, reason, expected_version). UI routes /trades and /trades/cases/:id.

- [ ] Write tests for staff creator, another staff member, scoped manager, other-store manager, viewer and unauthenticated access, including raw metadata and file bytes.
- [ ] Staff can inspect store slot/disclosure facts needed for their work. Private proof/claim evidence belongs to its submitting staff member and scoped managers; Today and general trade listings omit it.
- [ ] Reuse prepare_image(upload) decoding, 10 MiB/40M pixel ceilings, still JPEG/PNG/WebP restriction and metadata stripping. Publish opaque files to ignored uploads/condition_evidence outside static, with separate case ownership checks, no-store/nosniff and upload replay. Decode before locking; clean up only this upload's unreferenced file after failed metadata commit.
- [ ] Freeze disclosure on direct sale. Staff record a claim; managers append a specific disposition such as declined with reason or referred for separate refund/return review. Decisions never automatically credit payment or receipt stock; link separately authorized Build 4 events if they occur.
- [ ] Build slot inspection -> confirm swap -> server-confirmed result, plus direct sale -> empty slot -> explicit replacement. Show current occupant/version and stock unit; preserve draft input on failure.
- [ ] Add accessible labels, ~44px primary touch targets, keyboard/scanner behavior, long bilingual names, stale/rejected/missing-stock states, retry and private photo capture.
- [ ] Run test_trade_conditions.py and the trade portions of check_trades_today.py; extend backup manifest expectations for condition evidence without implementing a deletion/retention policy.

~~~python
assert rejected_swap["inventory_document_ids"] == []
assert evidence_upload["case_id"] == case_id
assert other_staff_download.status_code == 403
assert manager_decision_did_not_change_payment_or_stock
~~~

**Gate:** Staff can complete trading through the interface and managers can record condition decisions without implied refund promises.

## Task 4 (5B): one server-filtered Today projection

**Interfaces:** GET /api/today?store_code=DT|MK|ALL&business_date=YYYY-MM-DD -> role-specific JSON. today.get_today(con, *, actor, store_code, business_date) -> dict; omitting date uses America/Toronto today for the existing DT/MK locations. No caller-supplied role.

Common fields: business_date, generated_at (UTC), authorized stores, scope, and sections. Each queue row has a stable source identity, type, status, store, current version/updated time where available, and an authorized link. Use deterministic ordering and limited rows with total count; do not load all attachment data or create stored dashboard totals.

| Role | Today contents |
| --- | --- |
| Viewer | Existing permitted catalog notices; stock notices only with explicit inventory access and an accessible read-only destination; no access means a clear state |
| Staff | Own incomplete sales/evidence; scoped receiving/restock/count/closing tasks and trade cases; unassigned store work labeled unassigned |
| Manager | Authorized-store posted-sales/tender completeness, allocation/evidence exceptions, cash variance, goods/count issues and closing approvals |
| Admin | Same trusted facts across explicitly authorized stores plus unresolved catalog identity; no invented integrations/failures before connectors exist |

- [ ] Write raw API tests for all four roles with DT/MK scopes, no scope, forged role/store, empty authorized set, cross-store IDs, revoked access and All Stores.
- [ ] Resolve store scope before calculating counts or totals. Build separate allowed projections on the server; do not return a superset and hide cards.
- [ ] Derive queues from current receipt/delivery/count/restock/sale/payment/condition/closing facts. Pending work from earlier dates remains visible as overdue. No new assignee service; creators and recorded actors only.
- [ ] Exclude other staff's evidence IDs, private payloads, management totals, cash variance, costs and margin from staff/viewer Today. Staff-required count arithmetic remains on its authorized Closing task, not a management summary.
- [ ] Manager/admin financial highlights read posted Build 4 facts with unknowns explicitly counted and no legacy quantity-based money. Keep closed snapshot and later adjustments separate. Build 6 provides broader report drill-down.
- [ ] Skip the legacy InsightFeed for roles lacking its data permissions; never carry its legacy revenue estimates into the new Today response. Any reused insight endpoint must enforce the same scope first.
- [ ] Return private/no-store responses. Avoid persistent caches; read a consistent SQLite snapshot, release it promptly, and return a failure instead of fabricated zero work.
- [ ] Run test_today_permissions.py.

~~~python
for role in ("viewer", "staff"):
    assert "tender_totals_cents" not in payloads[role]
    assert "cash_variance_cents" not in payloads[role]
    assert "other_staff_evidence" not in str(payloads[role])
assert manager_all["store_ids"] == [authorized_dt_id]
assert no_access_all["store_ids"] == []
~~~

**Gate:** Four roles see different actionable facts and no unauthorized information leaves the server.

## Task 5 (5B): staff shifts first, clear navigation and Build 5 proof

**Files:** Dashboard/MyShifts.tsx, todayPresentation.ts/test.ts, Dashboard/index.tsx, App.tsx, AppLayout.tsx, api/today.ts; test_today_shifts.py, check_trades_today.py, CI, docs/trades-and-today.md.

- [ ] Write failing pure tests for selecting today's shifts and the next later assignment in Toronto local time, tied dates sorted by start time/ID, no shifts, invalid time data and independent operation-store selection. Include Toronto midnight with a different browser timezone and DST dates.
- [ ] Implement MyShifts by importing the existing getMyShifts function. Fetch from local today with store_code=ALL and no arbitrary 7/30-day horizon that would hide the next recorded shift.

~~~typescript
const shifts = await getMyShifts({ start: localToday, store_code: 'ALL' })
// Sort a copy by date, start_time, id; show all today's assignments,
// then the next assignment after today's date. Keep raw Schedule rows unchanged.
~~~

- [ ] Always render My Shifts first for staff, desktop and mobile: date, start/end time, store name/code, position if supplied, and View Schedule -> /schedule. If today's shifts have finished, still show today's assignments and the next later assignment.
- [ ] Refresh on initial load and focus; no interval service. Distinguish No shift today, No upcoming shifts assigned, stale data and failed fetch. Do not turn malformed/failed data into no-assignment claims.
- [ ] Keep personal shifts independent of operation-store scope and failure. A user with no approved inventory access can still see their own assignments.
- [ ] Clear/remount Today and its request state on user/role/store changes. Discard late responses from old identities; clear personal shifts on identity/role change, not just store change. Logout clears private local state.
- [ ] Replace Dashboard's legacy aggregate fetches; render staff operations below shifts and other roles in blockers -> due actions -> status order. Update only the / home guard for the restricted viewer view.
- [ ] Adopt existing-design navigation: mobile Today, Inventory, Sales, Schedule, More; Sales opens staff entry or manager sales workspace as appropriate. Products, Closing and Trades remain reachable through permitted entries. Preserve existing URLs and Schedule menu behavior.
- [ ] Define document deep links explicitly: sales /sales/documents/:id; closing /closing?closing_id=<id>; receipts /goods/receiving?receipt_id=<id>; transfers /goods/transfers?transfer_id=<id>; counts /goods/counts?count_id=<id>; restock /restock?session_id=<id>. The query parameters are proposed resume wiring, not existing functionality. Add only the read/resume wiring needed by Today; never silently switch a mutation's selected store. Unknown/deleted/unauthorized IDs produce a clear error.
- [ ] Update the existing Node test command to include Dashboard presentation tests while still running every unchanged Schedule test. Add check_trades_today.py to CI.
- [ ] Browser proof: four roles, DT/MK/ALL, staff shift block first, MK shift with DT operations selected, next shift beyond 30 days, no shifts, focus refresh, failed request, delayed old account response, trade stale conflict, 390/768/1440 widths, keyboard, 200% zoom and View Schedule. No raw forbidden data.
- [ ] Run the commands below; write the exact results and limitations in docs/trades-and-today.md. Reconcile review findings before declaring the build complete.

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
Set-Location popcore_app\frontend
npm test
npm run build -- --outDir ../../.local/frontend-build-build5
Set-Location ..\..
git diff --check
git diff --exit-code -- popcore_app/static popcore_app/frontend/src/pages/Schedule popcore_app/blueprints/schedule.py
git status --short --branch
~~~

Check each process exit before proceeding. A failed prerequisite test is not waived by the new tests passing.

**Build 5 exit gate:** Staff can inspect/swap/sell/replace a trade unit without double stock or lost history, condition claims stay separate from refunds, Today obeys role/store scope, and staff's shifts are always their primary highlight. Schedule and committed assets match the entry baseline. Build 6 remains necessary for reports, all review-screen reachability and integrated pilot proof.
