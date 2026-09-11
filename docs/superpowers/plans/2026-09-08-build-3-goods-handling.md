# Build 3: goods handling implementation plan

> **For agentic workers:** Use superpowers:executing-plans task by task only when implementation is requested. Read AGENTS.md, the design, Build 2 results, and this plan first. This document does not authorize subagents, commits, pushes, production-data access, or deployment.

**Goal:** Let store staff receive, scan, pick, deliver, transfer, and count physical goods while every stock change remains traceable and posts once.

**Architecture:** Extend the existing Flask/SQLite inventory engine and current Stock/Restock pages. Receipt and delivery documents hold workflow state; only inventory_commands writes movements and balances. Use the existing transit disposition for picked/dispatched goods, with ownership tracked per delivery line.

**Tech Stack:** Existing Python 3.13, Flask, SQLite WAL, React 18, TypeScript, Ant Design, Auth0, unittest, Node test runner, and isolated Playwright harness. No new service, queue, scanner framework, or workflow platform.

**Spec:** [Store design](../specs/2026-09-07-instore-website-design.md), sections 5, 6A–C, 6F, 7–8; [master roadmap](2026-09-07-instore-website-plan.md), Phase C / Task 6; prerequisite [Build 2](2026-09-08-build-2-inventory-core.md) and [implemented inventory core](../../inventory-core.md).

**Status:** Proposed implementation plan, source inspected 2026-09-08. No application changes made for this plan. Existing Build 1/2 changes are uncommitted on codex/build-2-inventory-core; HEAD alone does not contain them. Earlier plan headers are historical, not fresh implementation evidence.

## Scope and boundaries

Build 3 covers shipment receipt drafts, keyboard-wedge scans, partial restock picking/receiving, inter-store transfers, floor min/max suggestions, and reviewed physical count adjustments. It completes the physical goods workflow before Build 4 adds sale/payment/close documents.

Keep Schedule unchanged. Role-specific Today, with staff's own assigned shifts first, stays in roadmap Task 10. Only add the links necessary to reach these goods screens; do not redesign the application shell.

Payment entry, cash closing, trades, purchasing/POs, landed costs, wholesale, Clover/WooCommerce connectors, printer integration, and offline posting are outside this build. Existing internal barcode identities are usable with a scanner; printer/device selection remains pilot preparation. Do not mark the entire first release complete after this build.

## Global Constraints

- Work in D:/dev/POPCORE. Keep environments, dependencies, test databases, evidence, caches, and build output on D:.
- Preserve setup changes, source IDs, real data, existing environments, and committed production assets.
- No live service mutation, droplet data download, deployment, or external channel stock update during local implementation.
- Use synthetic data and blocked external network calls for mutation tests. Set DISABLE_SCHEDULER before app import; app import otherwise runs migrations/jobs.
- A series is an umbrella, not a bundle. Do not implement a general bundle/BOM engine for series/design relationships.
- Never infer inventory authority from Schedule membership, an admin role alone, or All Stores.
- Quantities are positive integers in native units; zero is valid for observations and explicit not-found decisions. No clamping, guessed product match, or guessed claw stock.
- Preserve stable request keys through retry and lost responses. A changed submission needs a new key; a stale snapshot requires review before a new posting.
- Check weekly usage before execution, between tasks, and before the final verification batch. At 40% consumed / 60% remaining, stop starting work and report the exact completed/pending checkpoint. Do not redeem a reset.
- No commit, push, or deployment without separate authorization.

## Entry gate and current source facts

Before implementation, capture branch/status/remote, existing diffs, and hashes for Schedule and committed static files. Continue from the working tree containing Builds 1–2; do not create a worktree from bare HEAD and lose uncommitted prerequisites. Preserve the existing staged server.log deletion.

Re-run the relevant Build 2 inventory, migration, and browser checks as the entry gate. Planning inspected source; it did not re-run the prior 128 backend / 14 frontend tests.

Current constraints requiring explicit changes:

1. inventory_commands.post_inventory(con, payload, *, actor, request_key) owns BEGIN IMMEDIATE and commit. It cannot currently be nested in a receipt/delivery/count transaction.
2. restock.py complete currently moves found quantities directly from back stock to floor and marks completed. Picked and received are not separate physical stages.
3. inventory-check observations do not post adjustments or retain a captured balance version for a later approval.
4. inventory_mode is one database-wide flag; opening verification is per location. Do not claim per-store mode switching or auto-activate newly seen scopes.
5. Generic inventory commands can name transit. Delivery-owned transit must be protected from unrelated consume/move/correction requests.
6. npm test currently selects Schedule tests only; CI's browser job currently runs the foundation check only. Add the new checks explicitly.

## File map

Paths are relative to D:/dev/POPCORE. New names below are proposed, not existing code.

| File | Responsibility |
| --- | --- |
| popcore_app/inventory_commands.py | Preserve the single ledger writer; add transaction composition and delivery-owned transit checks |
| popcore_app/goods_operations.py (new) | Receipt, delivery, target, and count transitions; begin/commit once around state and ledger |
| popcore_app/blueprints/goods.py (new) | Scoped receipt/delivery/count APIs, thin adapters to goods_operations |
| popcore_app/db.py | Additive receipt/delivery/event/count/target and request-record migrations |
| popcore_app/blueprints/restock.py | Adapt current restock sessions to request → pick → receive |
| popcore_app/blueprints/inventory.py | Reuse bestseller configuration; keep historical checks readable |
| popcore_app/blueprints/stock.py | Guard low-level transit bypass; link stock history to goods documents |
| popcore_app/app.py; popcore_app/tests/support.py | Register the goods blueprint in runtime and isolated tests |
| frontend/src/pages/Stock/Receiving.tsx, Transfers.tsx, Counts.tsx (new) | Scoped goods forms and document history, reachable from Stock |
| frontend/src/pages/Restock/RequestStep.tsx, PickingStep.tsx, SessionModal.tsx, HistoryTab.tsx, index.tsx; ReceivingStep.tsx (new) | Extend existing restock flow |
| frontend/src/pages/Restock/EveningCheckStep.tsx | Use versioned count workflow for authoritative data |
| frontend/src/components/OperationScanInput.tsx (new); api/goods.ts (new) | Shared receiving/count scan input and typed goods calls |
| frontend/src/pages/Stock/index.tsx; App.tsx | Minimal entry links/routes, with existing role guards |
| tests/test_goods_transactions.py, test_receiving.py, test_restock_lifecycle.py, test_transfers.py, test_counts.py (new) | Real rollback, lifecycle, access, and concurrent-write checks |
| tests/browser/check_goods_flow.py (new); .github/workflows/checks.yml | Browser workflow and CI inclusion |
| docs/goods-handling.md (new); docs/inventory-core.md | Actual workflow, migration disposition, and recovery documentation |

Frontend paths in this table start under popcore_app/. Backend test paths start under popcore_app/. Keep focused functions in existing modules; do not reorganize unrelated code.

## Shared implementation contracts

### Transaction and request identity

Introduce a private _post_inventory_in_transaction(con, payload, *, actor, request_key, delivery_event=None) -> dict. It requires an active transaction, performs the existing normalization/authorization/replay/ledger/projection work, and never begins, commits, rolls back, or reconnects. Keep the public post_inventory signature and behavior unchanged; its wrapper owns the transaction and rejects caller-owned transactions as before.

Only a goods workflow may supply delivery_event. The engine loads its delivery/line/action from the same database transaction, validates allowed endpoints/quantities and participant authority, and rejects a fabricated/mismatched context. This is a narrow delivery interface, not a skip-authorization flag. The public JSON endpoint cannot supply this context.

Add operation_requests with request_key unique, operation, resource_id, actor_sub, payload_hash, and stored_result. It records successful lifecycle actions including those with zero stock effects. Same actor/key/normalized intent returns the stored result after rechecking current access; another actor or changed intent conflicts. Include document version and captured balance versions in the intent. Failed/rolled-back requests leave no success record.

~~~python
# Required composition shape; the public inventory wrapper remains supported.
con.execute("BEGIN IMMEDIATE")
try:
    # Authorize, load current workflow version, check replay, validate.
    # Post ledger effects through _post_inventory_in_transaction if nonzero.
    # Save workflow version/event and operation_requests result.
    con.commit()
except Exception:
    con.rollback()
    raise
~~~

Use the existing bounded busy timeout and structured inventory errors. No filesystem/network work while holding the write lock. Repeated lines affecting one balance must be coalesced or use a single captured version correctly; do not let two lines invalidate each other.

### Documents and state

| Structure | Fields/invariants |
| --- | --- |
| goods_receipts / goods_receipt_lines | Store/destination, business date, shipment reference (optional), supplier text or unknown, creator, revision, draft/posted/cancelled; product/native unit; expected quantity nullable; saleable/damaged/hold received quantities; discrepancy note; linked inventory document |
| inventory_deliveries / inventory_delivery_lines | Kind restock/transfer, optional unique restock_session_id, source/destination, creator, version, requested quantities and terminal short reasons; planned/active/completed/cancelled; actual totals derived from events |
| inventory_delivery_events / event lines | Append-only pick/dispatch/receive/return/resolve-loss/short-close facts, per-line quantity/disposition, actor/time/reason, inventory_document_id nullable; unique operation request identity |
| inventory_counts / count lines | Store/location/disposition/date, creator, version, draft/submitted/approved/returned; each observation revision retains expected quantity, observed quantity, captured balance version and posting sequence; reason/reviewer and correction document |
| inventory_floor_targets | Unique floor-location/product; min/max in native units, version, updater; 0 <= min <= max |
| operation_requests | Successful idempotent action identity and stored result, shared with Build 4; no background replay worker |

Posted receipts, delivery events, submitted count observations, and correction links are immutable. Draft edits use expected_version. Returns for recount create a new observation revision rather than erase the submitted one.

Transit lives at the source location with disposition transit, allocated to its delivery line. Dispatch/pick reduces source saleable and increases transit. Receipt reduces that same allocated transit and increases the chosen destination saleable/hold/damaged balance. Returns restore source stock explicitly. A reasoned manager loss resolution consumes only that delivery's remaining transit; no automatic loss recognition.

For each line:

~~~text
outstanding_transit = picked_or_dispatched - received - returned - approved_loss
outstanding_transit >= 0
received + returned + approved_loss <= picked_or_dispatched <= requested
~~~

An unfilled requested quantity is a visible shortage; terminal short-close needs a reason. Completion requires outstanding_transit=0 and each unfulfilled request explicitly closed. Pending transit is never available for sale or another delivery.

### Permission boundary

Staff need explicit inventory_access for the store in which they act. Manager approval also needs that scope. Viewers get no new operations access. Neither roles nor scheduling memberships grant implicit cross-store access.

A transfer plan identifying two stores is authorized by a manager with both scopes. Source staff dispatch only that stored plan. Destination staff receive only its outstanding lines without obtaining general access to source-store stock. A participant-scoped delivery DTO contains the source/destination labels and shared shipment lines/history only. Arbitrary source balances, other transfers, and management data remain denied. Source-side return/loss decisions require source authority; changing the destination after dispatch is rejected.

Generic commands keep the current both-store rule for ordinary cross-store moves and cannot spend delivery-owned transit. Test direct low-level requests as well as the UI.

## Task 1: composable posting and protected transit

**Files:** inventory_commands.py, db.py, goods_operations.py, tests/test_goods_transactions.py, existing test_inventory_posting.py and test_inventory_concurrency.py.

**Produces:** The private posting interface and operation_requests contract above. Existing public inventory calls still behave identically.

- [ ] Write a regression that posts a ledger movement, then forces workflow-state failure before commit; balances, movements, compatibility rows, workflow rows, and success request records must all remain unchanged.
- [ ] Run that regression and confirm the current transaction-owning function cannot satisfy composition.
- [ ] Extract only the transaction body and preserve existing standalone transaction tests, response shape, replay checks, access checks, and busy behavior.
- [ ] Add delivery tables and constraints additively. Do not change existing inventory document kinds; receipt/move/consume/correction cover these operations.
- [ ] Add per-delivery transit ownership validation. A direct command cannot steal transit, forge a delivery event, or correct a posted delivery independently of its lifecycle.
- [ ] Preserve opened-set provenance when a selected protected source moves through pick/dispatch/receipt. Carry the source relationship with the delivery allocation; ordinary loose/mixed stock stays mixed. Do not silently drop protection during transit or reconstruct a fresh set on receipt. Test full/partial receipt and return of the selected source.
- [ ] Test same-key replay after process reconnect, changed-intent conflict, unauthorized replay, multi-line atomicity, and two deliveries competing for the same remaining unit.

**Acceptance assertion:**

~~~python
self.assertEqual(after_failed_transition, before_transition)
self.assertEqual(first_result, retried_result)
self.assertEqual(committed_effect_count, 1)
~~~

Run: .\.venv\Scripts\python.exe -m unittest discover -s popcore_app/tests -p "test_goods_transactions.py" -v

**Gate:** Stock and lifecycle cannot commit separately; all earlier inventory tests still pass.

## Task 2: receiving drafts and explicit scanning

**Files:** goods_operations.py, blueprints/goods.py, db.py, app.py, tests/support.py; Stock/Receiving.tsx, Stock/index.tsx, App.tsx, api/goods.ts; tests/test_receiving.py.

**Interfaces:** POST /api/goods/receipts creates a draft with Idempotency-Key; GET/PATCH /api/goods/receipts/<id> reads/saves with expected_version; POST .../<id>/post or /cancel performs one transition. All responses carry id, version, status and linked inventory_document_id when posted.

- [ ] Write API cases for one 12-box set, directly received Design A x3/B x4, partial shipment quantities, damaged/hold quantities, unknown supplier/cost, invalid units, duplicate post, and forbidden store.
- [ ] Add receipt schema, explicit server-side unit/location validation, and draft/post/cancel handlers. Never create purchased stock from expected-but-not-received quantities.
- [ ] Preserve unknown expected quantity as unknown; require a discrepancy reason when known expected and actual differ. Keep receipt corrections as linked manager documents, never delete the posted receipt.
- [ ] Build destination → scan/search → actual quantities/dispositions → review → post. Use Build 2 barcode resolution and quantity parser; ambiguous codes require explicit candidate selection; unknown scans leave other draft lines intact.
- [ ] One Enter on a focused scan input adds quantity_per_scan once. Another identical scan adds another physical unit; a retry of the same submitted request adds none. Ignore IME composition Enter, and prevent double form submission.
- [ ] Preserve leading zeroes, native units, and set-equivalent preview. Aggregate scanning happens in the draft, never as an immediate stock write.
- [ ] Add server-saved drafts plus an unsaved navigation warning. Failed saves retain the current form and mark it unsent; explicit retry uses the same key. Clear user-scoped data on account changes.
- [ ] Link posted receipt to inventory history and disable posting in legacy or unverified scopes with a clear explanation.

~~~text
Input one sealed set size 12 -> +1 set, equivalent +12 boxes.
Input 12*1 as box shorthand -> +12 boxes, never +144.
Shipment expected 10 pieces; 7 saleable +1 damaged -> receipt 8; shortage 2.
Two scans of internal code 00123 -> draft +2 pieces.
Retry that receipt after lost response -> one receipt, +2 pieces total.
~~~

Run: .\.venv\Scripts\python.exe -m unittest discover -s popcore_app/tests -p "test_receiving.py" -v

**Gate:** A staff member can receive reviewed goods with visible identity, unit, destination, discrepancy, and one traceable stock effect.

## Task 3: separate restock request, picking, and receipt

**Files:** goods_operations.py, blueprints/restock.py, db.py; existing Restock components plus ReceivingStep.tsx; tests/test_restock_lifecycle.py.

**Interfaces:** Existing request creation/submission remains. Add POST /api/restock/session/<sid>/pick, /receive, /return, /short-close with request key, expected session/delivery version, line deltas, and captured balance versions. List/detail exposes requested, picked, received, returned, loss, outstanding and shortage per line. Reuse the delivery model from Task 1.

- [ ] Write the request-5/pick-4/receive-3 regression before adding transitions. Assert exactly one remains in that delivery's transit and nothing becomes saleable twice.
- [ ] Link new authoritative restock sessions to deliveries. Preserve pending/submitted/picking behavior as drafts; physical pick confirmation posts back-saleable → transit. Editing a found quantity is not physical dispatch.
- [ ] Add explicit receiver confirmation and partial receipt. Cap additional picks by remaining requested and additional receipt by that delivery's outstanding quantity.
- [ ] Add return and reasoned short-close actions; a manager resolves confirmed transit loss. A cancellation after a movement cannot delete it or simply release imaginary stock.
- [ ] Replace the authoritative one-click /complete path with lifecycle-aware completion. Existing completed sessions and their Build 2 documents remain historical; open pre-upgrade sessions require review and never infer prior transit.
- [ ] Guard old item edit/delete, session delete, completion, batch movement, and generic command paths so none can bypass a delivery's ownership/state.
- [ ] Display actors and actual quantities at each step. A zero-picked/not-found session closes with a reason and stable replay result even though there is no inventory document.
- [ ] Exercise concurrent picks, duplicate receive, receive beyond remaining, changed draft after stale conflict, and failure between movement and session update.

~~~text
Back 10, floor 2. Request 5 -> unchanged.
Pick 4 -> back saleable 6, allocated transit 4, floor 2.
Receive 3 -> back 6, transit 1, floor 5; incomplete.
Return 1 and short-close missing 2 -> back 7, transit 0, floor 5.
Total physical stock remains 12; repeated receipt cannot make floor 8.
~~~

Run: .\.venv\Scripts\python.exe -m unittest discover -s popcore_app/tests -p "test_restock_lifecycle.py" -v

**Gate:** Picked goods remain accounted for until received, returned, or explicitly resolved.

## Task 4: inter-store transfer with partial delivery

**Files:** goods_operations.py, blueprints/goods.py; Stock/Transfers.tsx, api/goods.ts; tests/test_transfers.py.

**Interfaces:** POST /api/goods/transfers creates a manager-approved plan; GET /api/goods/transfers and /<id> are participant scoped; POST /<id>/dispatch, /receive, /return, /resolve-loss, /short-close use the same versioned action envelope.

- [ ] Write source-only sender / destination-only receiver / unrelated user cases, including raw API data checks and forged endpoint/body IDs.
- [ ] Reuse delivery transitions, validating the planned source and destination every time. Never grant broader access as a side effect of participating in a transfer.
- [ ] Support partial dispatch and receipt, with sender/receiver actors and per-line outstanding quantities. Destination receipt does not deduct source saleable again.
- [ ] Keep unresolved transit visible; returns go back to the actual source, and shortage/loss requires the authorized decision defined above.
- [ ] Show source → in transit → destination with remaining quantities and linked movement documents. Do not introduce carrier APIs or tracking automation.
- [ ] Test two transfers holding the same product, a receiver trying to claim the other transfer's stock, canceled/unverified destination, and retries across reconnect.

~~~text
DT upstairs 10, MK warehouse 0; plan 5.
Dispatch 4 -> DT saleable 6, this transfer transit 4.
Receive 3 -> MK 3, this transfer transit 1.
Another transfer cannot receive that last 1.
Total source + transit + destination =10 throughout.
~~~

Run: .\.venv\Scripts\python.exe -m unittest discover -s popcore_app/tests -p "test_transfers.py" -v

**Gate:** Two staff members with different store scopes complete a transfer without cross-store permission leakage or lost stock.

## Task 5: versioned counts and bounded restock suggestions

**Files:** goods_operations.py, blueprints/goods.py, blueprints/inventory.py, db.py; Stock/Counts.tsx, Restock/EveningCheckStep.tsx, RequestStep.tsx; OperationScanInput.tsx; tests/test_counts.py.

**Interfaces:** POST /api/goods/counts; GET/PATCH /api/goods/counts/<id>; POST /<id>/submit, /return, /approve. GET/PUT /api/goods/targets for manager target editing; GET /api/goods/restock-suggestions for authorized staff.

- [ ] Write stale-count, repeated-approval, no-change, unknown-barcode, invalid-unit, and cross-store tests. Capture expected quantity/version at observation time, not approval time.
- [ ] Store immutable submitted observations and new revisions for recount. Manager approval posts only observed-minus-expected through the inventory engine; no direct stock replacement.
- [ ] Recheck the counted product/location/disposition balance version in the approval transaction. Any intervening movement on that balance, even net-zero, requires recount. An unrelated product's movement does not invalidate the observation.
- [ ] A zero-difference approval produces an approved observation and idempotent result without fake movements. A difference needs reason and reviewer; approval also respects protected opened-set quantities/provenance.
- [ ] Counts of generic boxes must not erase fresh-set provenance. If the observation cannot reconcile protected units, return a provenance/reconciliation conflict for manager resolution.
- [ ] Reuse the receiving scan input after this second real use. Keep count draft data when one barcode fails and allow explicit correction before submission.
- [ ] Add min/max per product/floor. If floor < min, suggest min(max - floor - outstanding_inbound_for_this_floor, available_back_saleable), bounded below by zero. Exclude held/transit/protected unavailable units and do not convert sets silently.
- [ ] Reuse existing bestseller selection for hot-item counts. Existing legacy observations remain readable and cannot be relabeled as approved ledger corrections.

~~~python
self.assertEqual(9 - 10, -1)  # observed minus captured expected
# floor=2, min=4, max=8, outstanding inbound=1, available back=3
self.assertEqual(max(0, min(8 - 2 - 1, 3)), 3)
~~~

Run: .\.venv\Scripts\python.exe -m unittest discover -s popcore_app/tests -p "test_counts.py" -v

**Gate:** One approved count creates at most one adjustment; suggestions cannot over-request or automatically post stock.

## Task 6: integrated proof, migration, and handoff

**Files:** tests/browser/check_goods_flow.py, existing test_inventory_migration.py, .github/workflows/checks.yml, frontend/package.json if a real TypeScript helper test is added, docs/goods-handling.md, docs/inventory-core.md.

- [ ] Add repeatable migration fixtures containing completed Build 2 restocks, unfinished legacy restocks, existing counts, and unresolved mappings. Apply migration twice and preserve IDs/history.
- [ ] Demonstrate receipt → set opening through existing core → partial restock → partial inter-store transfer → count → approved correction on synthetic DT/MK data. Reconcile every balance against movements, in both directions.
- [ ] Run browser checks with actual isolated Flask endpoints and test identity, not success-only mocked APIs. Inject lost response after real commit, stale edits, 403, unavailable network, and concurrent session actions.
- [ ] Check 390/768/1440px, keyboard/scanner use, long bilingual product names, 200% zoom, and unsaved draft preservation. Scope styling to touched goods pages.
- [ ] Add goods and existing inventory browser checks to CI; keep foundation and all Schedule tests. Update npm test only if new TypeScript tests need inclusion; do not add another test framework.
- [ ] Run the full verification below and inspect protected file hashes/status. Document actual passing evidence and remaining real-store gates.
- [ ] Update the existing writer disposition record for every changed adapter. Do not call generic SQL stock writes from new scripts or endpoints.

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
Set-Location popcore_app\frontend
npm test
npm run build -- --outDir ../../.local/frontend-build-build3
Set-Location ..\..
git diff --check
git status --short --branch
git status --short -- popcore_app/static popcore_app/frontend/src/pages/Schedule popcore_app/blueprints/schedule.py
~~~

**Build 3 exit gate:** The integrated goods scenario passes; source/transit/destination reconcile; rejected/duplicate/stale operations have the specified effects; role scopes and drafts are verified; Schedule and release assets retain their baseline. Record evidence before starting Build 4. Physical opening counts, access approval, live hardware, and pilot activation remain separate.
