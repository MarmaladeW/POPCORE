# Build 2: inventory core implementation plan

> **For agentic workers:** Use superpowers:executing-plans task by task when implementation is requested. Read the design, master roadmap, Build 1 result, and repository AGENTS.md first. This planning document does not authorize subagents, commits, pushes, deployment, or production-data access.

**Goal:** Establish explicit product units and locations plus one atomic inventory posting path, demonstrated on synthetic store data and prepared for a controlled migration.

**Architecture:** Add a small inventory command module and additive tables to the existing Flask/SQLite app. Keep existing product IDs and use current pages as compatibility clients where their meaning is unambiguous. Route each active stock mutation through the command module, or explicitly disable it in authoritative mode.

**Tech Stack:** Existing Python 3.13, Flask, SQLite WAL, React 18, TypeScript, Vite, Ant Design, Auth0. Reuse Build 1's unittest and isolated browser fixtures.

**Spec:** [In-store website design](../specs/2026-09-07-instore-website-design.md), sections 5, 6A–C, 7, 8; [master roadmap](2026-09-07-instore-website-plan.md), Phase B / Tasks 4–5; prerequisite [Build 1](2026-09-08-build-1-foundation.md).

**Status:** Planned, not implemented. Source inspected 2026-09-08 at main / 8630199ac780059f5cb185a85c6efd0ac4597b91; execution begins from the verified Build 1 result, not a reset to this old commit.

## Scope and deliverable

Deliver catalog identity editing, explicit unit/barcode resolution, local stock posting with auditable history, adapters for supported current stock actions, and a migration rehearsal. This build proves arithmetic and transaction safety. It does not establish the droplet's physical stock truth.

Receiving/scanning workflow screens, partial restock/transfer lifecycle, guided physical counting, sales/payment entry, closing, trades, reports/Today, and channel integrations retain their later roadmap tasks. Core transfer/conversion primitives are in scope; their full workflow screens are not.

Role-specific Today remains required, with staff's own assigned shifts as the first highlight. It uses existing personal shifts across their assigned stores when Task 10 is built. Schedule itself stays unchanged.

## Global Constraints

- Work in D:/dev/POPCORE. Keep environments, dependencies, test databases, evidence, caches, and build output on D:.
- Preserve setup changes, source IDs, real data, existing environments, and committed production assets.
- No live service mutation, droplet data download, deployment, or external channel stock update during local implementation.
- Use synthetic data and blocked external network calls for mutation tests. Set DISABLE_SCHEDULER before app import; app import otherwise runs migrations/jobs.
- Keep Flask, SQLite, React, Auth0, and the existing component system. Do not introduce another platform, a separate inventory microservice, LocalWP, an automated accounting system, or a speculative generic workflow builder.
- A series is an umbrella, not a bundle. Do not implement a general bundle/BOM engine for series/design relationships.
- No blind-box serialization. Track only the provenance needed for an opened set versus a mixed pool, and exact confirmed designs.
- Unknown historic sets, claw/display meaning, costs, and tender identities remain unknown.
- File paths below are relative to D:/dev/POPCORE. Schema names and interfaces are proposed; check for collisions before creating them.

## Entry and activation gates

- Build 1's local tests, browser checks, dependency decisions, and Schedule preservation gate must be recorded before this build begins.
- Add an explicit database-owned inventory_mode value: legacy or authoritative. The migration defaults existing databases to legacy and never switches modes on application import.
- New identity metadata may be reviewed in legacy mode, but no shadow process independently posts real inventory. Command behavior is exercised in a separate authoritative fixture database.
- Switching a database to authoritative is an offline, explicitly invoked migration action: validate all mappings/opening counts, stop all old writers, post openings, reconcile projections, and enable new writers atomically.
- Build 2 only switches disposable synthetic databases. Real authority requires approved operations permissions, resolution of claw/display overlap, physical opening counts, and later pilot/recovery gates.
- Never support a casual switch from authoritative back to legacy after new postings. Freeze/reconcile/forward-correct instead.

## File boundaries

| File | Responsibility |
| --- | --- |
| popcore_app/db.py | Register additive migrations using existing migration registry; no wholesale migration-framework replacement |
| popcore_app/catalog_identity.py (new) | Explicit product form/unit, barcode resolution, fixed set-opening conversion rules |
| popcore_app/inventory_commands.py (new) | Validate/authorize, own transaction, record documents/movements, update balances/projection |
| popcore_app/blueprints/stock.py | Existing stock action adapters plus explicit command/history/balance routes |
| popcore_app/blueprints/products.py | Existing catalog CRUD/sync plus identity/barcode metadata endpoints |
| popcore_app/blueprints/stores.py | Location directory; preserve Schedule's store list and employee membership behavior |
| popcore_app/blueprints/sales.py, restock.py, inventory.py | Migrate or gate legacy stock effects; retain explicitly aggregate-only and observation-only paths |
| popcore_app/init_db.py | Preserve catalog import behavior and protect verified unit metadata |
| popcore_app/frontend/src/pages/Products/ProductModal.tsx, ProductDetailDrawer.tsx, index.tsx | Minimal identity controls and unknown/conflict labels |
| popcore_app/frontend/src/pages/Stock/, Restock/ and Dashboard/index.tsx | Add units, versions, request keys and conflicts; prevent existing summary cards from adding unlike units |
| popcore_app/tests/test_catalog_identity.py, test_inventory_units.py, test_inventory_posting.py, test_inventory_adapters.py, test_inventory_migration.py (new) | Domain, transaction, sibling-writer, and migration regressions |
| popcore_app/tests/browser/check_inventory_core.py (new) | Catalog/unit/stock compatibility browser demonstration using Build 1 harness |
| scripts/rehearse_inventory_migration.py (new), docs/inventory-core.md (new) | Explicit synthetic rehearsal and reviewed writer/migration record |

Do not add a generic service/repository/event bus layer. Small private helpers belong beside their real callers.

## Task 1: explicit identity and additive catalog migration

**Files:** db.py, catalog_identity.py, blueprints/products.py, tests/test_catalog_identity.py, tests/test_inventory_units.py.

**Schema contract:**

| Structure | Required fields and constraints |
| --- | --- |
| product_series | id, name; existing ip_series/brand text is preserved and is not blindly promoted to a series |
| products, additive metadata | series_id nullable FK; stock_form nullable enum random_box/sealed_set/confirmed_design/ordinary; stock_unit nullable enum box/set/piece; design_name nullable; identity_status unverified/verified |
| product_conversions | id, source_product_id, target_product_id, output_per_input positive integer, version positive integer; unique source/version; retained once referenced |
| product_barcodes | code TEXT, product_id, code_kind manufacturer/internal, input_unit, quantity_per_scan positive integer; multiple manufacturer candidates allowed; internal codes uniquely resolve |
| Existing products/aliases/sheet_ref | Retain IDs, values, uniqueness and historical references; add fields rather than replacing rows |

A sealed-set SKU contains sets, a random SKU boxes, and a confirmed-design SKU pieces. A conversion links one sealed-set SKU to random boxes in the same series. Ordinary items use pieces in this build. Do not infer form or split legacy quantities merely from boxes_per_dan or a name.

**Interfaces, catalog_identity.py:**

- resolve_barcode(con, code: str, purpose: str) -> dict. purpose is receive, move, or confirmed. Return status (exact/ambiguous/unknown) and candidates. Each candidate has product_id, stock_unit, quantity_per_scan, and code_kind.
- receipt_units(boxes_per_pack: int, pack_count: int) -> int. Both inputs are positive validated SQLite integers; checked multiplication returns the box count.

~~~python
def receipt_units(boxes_per_pack, pack_count):
    boxes_per_pack = read_int(boxes_per_pack, "boxes_per_pack", minimum=1)
    pack_count = read_int(pack_count, "pack_count", minimum=1)
    return read_int(boxes_per_pack * pack_count, "total_boxes", minimum=1)
~~~

Reuse read_int from Build 1's validation.py.

The return contract above is the public interface; implement the SQL lookup within Task 1, with explicit candidate ordering by product_id. Only return exact when one eligible mapping resolves the requested purpose. A generic manufacturer code never returns exact for a confirmed-design operation.

- [ ] Add migration fixtures for an empty database and legacy products with aliases, sheet_ref, sales references, and pack size 12.
- [ ] Write assertions that IDs/references survive, unverified rows stay unverified, and applying the migration twice changes no data.
- [ ] Add schema migration and validation for form/unit combinations. A verified sealed_set must have a reviewed conversion; a verified confirmed_design must have an explicit design name.
- [ ] Add barcode exact/ambiguous/unknown tests, preserving leading zeroes. Trim scanner terminators, but do not numerically coerce codes or silently strip meaningful characters.
- [ ] Implement barcode resolution. Repeated manufacturer codes return candidates; a conflicting internal label assignment returns 409.
- [ ] Add conversions for synthetic sizes 6, 9, and 12; unit tests must reject cross-series, zero/fractional factors, wrong forms, overflow, and same-product conversion.
- [ ] Once referenced by stock/documents, native units and conversion factors cannot be edited in place. If a quantity or history references a SKU, changing its native unit/form/series meaning is rejected. A genuinely different pack size gets a new reviewed SKU/conversion; old documents retain their snapshot.
- [ ] Preserve the current Sheet preview/confirm behavior: exact reference reuse, full-name matching, normalization, learned aliases, source-unavailable and duplicate conflicts. Sync/import must not overwrite verified identity fields.

Required assertions:

~~~python
self.assertEqual(receipt_units(12, 1), 12)
self.assertNotEqual(receipt_units(12, 1), 144)
result = resolve_barcode(con, "001234567890", "confirmed")
self.assertEqual(result["status"], "ambiguous")
self.assertEqual(con.execute(
    "SELECT id FROM products WHERE sku='LEGACY-1'").fetchone()[0], old_id)
~~~

For 12*1, preview explicitly shows 12 boxes. A sealed-set receipt instead selects unit=set, quantity=1. Never multiply both the reviewed explicit box count and boxes_per_dan.

**Check:** unittest discovery for test_catalog_identity.py and test_inventory_units.py.

**Gate:** identity/unit history is stable and unknown/ambiguous inputs never authorize a deduction.

## Task 2: physical locations, stock state, and scoped operations access

**Files:** db.py, catalog_identity.py, inventory_commands.py, blueprints/stores.py, tests/test_catalog_identity.py, test_inventory_posting.py.

**Schema contract:**

- inventory_locations: id, store_id FK, code, name, is_active; unique(store_id, code).
- Reviewed initial locations: DT floor/upstairs; MK floor/warehouse. Seed MT stays untouched for Schedule but receives no automatically verified inventory location/balance.
- Inventory disposition is a constrained value on balance/movement lines: saleable, trade, display, hold, damaged, transit. A transit balance remains owned by its source store/location until the receiving workflow explicitly moves it to the destination; its disposition excludes it from availability. Only saleable is retail availability.
- inventory_access: auth0_sub, store_id FK; unique pair. Access is explicit operations scope, independent of employee_stores scheduling membership.
- inventory_scope_state: store_id plus location_id, opening_verified flag and opening_document_id. Absence/unverified is not a trusted zero.

**Proposed policy for fixtures:** staff can receive/move in explicitly assigned operations stores; managers can make reasoned corrections within their assigned operations stores; admin configures catalog and access but still needs explicit inventory store scope. Viewer has no inventory write authority. Real-user rollout waits for owner approval of that policy; fixture tests proceed now.

**Interface:**

require_inventory_access(con, jwt_payload: dict, store_ids: tuple[int, ...], minimum_role: str) -> None. Raise PermissionError for insufficient verified role or any missing scope. Resolve all stores from database locations. Reuse auth.ROLE_HIERARCHY and auth.ROLE_CLAIM; a missing/unknown role cannot gain inventory access.

~~~python
for store_id in store_ids:
    granted = con.execute(
        "SELECT 1 FROM inventory_access WHERE auth0_sub=? AND store_id=?",
        (jwt_payload["sub"], store_id),
    ).fetchone()
    if not granted:
        raise PermissionError("Inventory access denied")
~~~

Perform the role-level check before this scope loop.

- [ ] Test staff DT access, denied MK mutation, manager scoped correction, viewer denied mutation, and explicit admin scope.
- [ ] Add locations and scope tables; do not copy employee_stores as implicit permission grants.
- [ ] Check role from verified JWT and scope from database on every command, including an idempotent replay. Request JSON cannot supply actor role or expand authority with ALL.
- [ ] Enforce permissions for balance/history responses, including legacy stock/summary/transactions/export and stock fields attached to catalog responses in authoritative mode: unauthorized balances, movement counts, or source documents must not be returned and merely hidden.
- [ ] Require source and destination scope for a cross-store transfer. Preserve Schedule's existing store endpoint behavior by using inventory-specific location/access endpoints.
- [ ] Test saleable versus hold/display/transit totals and explicit unknown opening status.

Expected denial test: a DT-only actor posting a transfer whose destination resolves to MK receives 403 and the document/movement/balance snapshot is unchanged. Changing store_code in JSON does not affect the actual location ownership.

**Gate:** location identity and raw-response visibility are correct on synthetic DT/MK users, without changing scheduling membership or access.

## Task 3: one document, transaction, and balance contract

**Files:** db.py, inventory_commands.py, tests/test_inventory_posting.py.

**Schema contract:**

| Table | Minimum durable information |
| --- | --- |
| inventory_documents | id, kind, request_key UNIQUE, payload_hash, source_type/source_id (unique when supplied), actor_sub, business_date, posted_at, correction_of FK nullable, internal status, stored_result |
| inventory_document_lines | document_id, line_no, product_id, native unit snapshot, quantity, from/to location and disposition, conversion_id/factor snapshot where applicable; unique(document_id,line_no) |
| inventory_movements | id as posting sequence, document_id/line_no reference, product_id, location_id, disposition, signed integer quantity; immutable |
| inventory_balances | product_id/location_id/disposition composite PK, quantity >= 0, version >= 0; every update increments version |
| inventory_mode | singleton value legacy/authoritative, cutover identifier and time; no automatic mode toggle |

Source lines/documents are retained. Use foreign keys with RESTRICT for referenced identities and history. A document has internal building/posted status solely to assemble its generated ID, lines, final balances, and stored result inside one transaction. Finalize to posted before commit; no partially built document may persist. Add triggers blocking UPDATE/DELETE of posted documents and their lines/movements. Replay never observes building state from another uncommitted connection. Avoid a mutable generic workflow engine.

**Public interface:**

post_inventory(con, payload: dict, *, actor: dict, request_key: str) -> dict owns BEGIN IMMEDIATE through commit/rollback. Return document_id (integer), posted_at (UTC ISO timestamp), and balances (list of product_id/location_id/disposition/quantity/version). An authorized replay returns the identical stored result.

Each successful request follows: begin -> authorize -> replay/source check -> validate current state -> write complete command effects -> finalize stored response -> commit. Map validation/conflict exceptions to the documented HTTP status at the route boundary, after rollback.

Payload fields: kind, business_date, source_type/source_id when known, reason, correction_of when relevant, lines. Each line provides product_id, positive integer quantity, unit, from/to location and disposition, and expected_versions for affected balance keys. Version 0 denotes an absent balance. Derive actor from verified JWT, never the body.

Kinds in this build: opening, receipt, move, consume, open_set, correction, restock_complete. The initial opening kind is callable only by the explicit offline activation/rehearsal entry point, never accepted by the public command route. That one multi-line opening command covers the reviewed activation scope and sets inventory_mode=authoritative in its own transaction after reconciliation; ordinary commands require authoritative mode. Do not wrap post_inventory in a second activation transaction. Do not expose arbitrary signed balance deltas as a staff API. Derive signed effects from these validated operations. No real sale/tender import is introduced here.

- [ ] Write the first test: opening 5 then consume 4 commits a document, movement -4, and balance 1; reconstruct the balance by summing movements.
- [ ] Create additive tables and constraints; opening is itself a document, not a bare balance insert.
- [ ] Canonicalize the validated domain payload with sorted JSON keys and SHA-256, preserving line order and explicit units/source identity. Include expected versions so changed intent conflicts; exclude transport-only noise.
- [ ] Reject a connection already inside a caller-owned write transaction. Start BEGIN IMMEDIATE, resolve authority, then check request_key before stale-balance validation.
- [ ] Same key/same payload returns stored result without new effects. Same key/changed content returns 409 idempotency_conflict. Different keys cannot duplicate a provided source identity.
- [ ] Validate all identities, locations, permissions, expected versions, units, and available source quantities while holding the transaction; use conditional UPDATE and check affected row count.
- [ ] Insert the document/lines/movements, update balances and compatibility projection, perform the specific source lifecycle transition, and commit as one unit. The module owns these SQL writes; no arbitrary callback may commit/reconnect.
- [ ] On any line, projection, or lifecycle failure, roll back everything. No network, file upload, LLM parsing, sleep, or retry loop while holding the transaction.
- [ ] Store the exact successful response in the document so a lost HTTP response is recoverable with the same key. Do not alter timestamps/results on replay.
- [ ] Keep a bounded SQLite busy timeout (start with the existing 5-second timeout). Return 503 inventory_busy with retry guidance; clients retry only the same key after a user-visible uncertain outcome.

Conditional update primitive:

~~~sql
UPDATE inventory_balances
SET quantity = quantity - :quantity, version = version + 1
WHERE product_id = :product_id AND location_id = :location_id
  AND disposition = :disposition
  AND version = :expected_version AND quantity >= :quantity;
~~~

If no row changes, distinguish current shortage from stale_version without applying a fallback update. Insert only legitimate absent destination balances at version 0; do not create source stock to satisfy a request.

Required regression shape after posting the same payload twice:

~~~python
first = post_inventory(con, payload, actor=actor, request_key="fixture-receipt-1")
second = post_inventory(con, payload, actor=actor, request_key="fixture-receipt-1")
self.assertEqual(second, first)
self.assertEqual(con.execute(
    "SELECT COUNT(*) FROM inventory_documents WHERE request_key=?",
    ("fixture-receipt-1",)).fetchone()[0], 1)
~~~

**Check:** unittest discovery with pattern test_inventory_posting.py.

**Gate:** every new balance is explained by immutable movements; no partial posting, guessed negative balance, duplicate source effect, or hidden second transaction.

## Task 4: conversion, provenance, and append-only correction

**Files:** inventory_commands.py, catalog_identity.py, tests/test_inventory_units.py, test_inventory_posting.py.

**Interfaces:** use post_inventory unchanged. open_set consumes a positive number of sets using a reviewed conversion_id and creates the exact random-box quantity. correction references an original document and records a reason and manager identity; it creates new movements.

- [ ] Test receipt of one sealed set: set quantity 1, box quantity 0, equivalent quantity 12.
- [ ] Test opening it: set 0, random boxes 12, equivalent quantity still 12; one transaction/document captures both products.
- [ ] Test repeated opening with the same key, insufficient sets, changed conversion factor, wrong product/design, and a forced failure between the two sides. No partial conversion may survive.
- [ ] Add only the opened-set provenance needed to distinguish a fresh replenishment set from a mixed pool: inventory_open_sets(id, opening_document_id, random_product_id, location_id, purpose, remaining_qty). purpose is customer_tray or replenishment. This remaining quantity is allocation metadata, not additional stock.
- [ ] Keep provenance consumption synchronized inside post_inventory. A loose/mixed pool has no fresh-set guarantee; moving units without retained provenance drops that guarantee explicitly rather than inventing it.
- [ ] Generic box consumption uses mixed units first and never silently consumes a guaranteed fresh set. A selected open_set_id identifies that allocation; remaining_qty cannot exceed the matching physical balance and cannot be consumed twice.
- [ ] Test the shelf distinction using one customer tray and one replenishment set. A fresh-set eligibility helper uses selected_quantity > set_size / 2 (7 of 12, 5 of 9), but does not automatically open stock or implement checkout UI.
- [ ] Implement exact compensating correction only from trustworthy new documents. The manager must review the inverse and current availability; already-consumed stock can make an exact undo impossible.
- [ ] Test completed transfer 5 followed by consumption 5: attempted exact inverse fails with insufficient_stock, original remains. A later physical adjustment is a separate reasoned document, not a forced undo.
- [ ] Enforce at most one full reversal per original document with a unique reference for that correction kind; multiple unrelated physical adjustments remain separate documents.
- [ ] Legacy Build 1 reports still lack trusted movement provenance. Keep their reconciliation_required block; do not relabel them as reversible merely because a ledger now exists.

Helper interface and acceptance:

~~~python
def qualifies_for_fresh_set(quantity: int, set_size: int) -> bool:
    return quantity * 2 > set_size

self.assertTrue(qualifies_for_fresh_set(7, 12))
self.assertFalse(qualifies_for_fresh_set(6, 12))
self.assertTrue(qualifies_for_fresh_set(5, 9))
~~~

This helper answers eligibility only; authorization, availability, and explicit staff confirmation govern any opening/allocation command.

**Gate:** native and equivalent quantities reconcile, fresh-set guarantees are based on recorded provenance, and corrections preserve both stock truth and history.

## Task 5: catalog controls and explicit stock feedback

**Files:** blueprints/products.py, stores.py, stock.py; frontend/src/pages/Products/ProductModal.tsx, ProductDetailDrawer.tsx, index.tsx; frontend/src/pages/Stock/RestockModal.tsx, BatchStockModal.tsx, index.tsx; tests/browser/check_inventory_core.py.

**Endpoints:**

| Endpoint | Contract |
| --- | --- |
| GET/PATCH /api/products/<id>/inventory-identity | Metadata, reviewed status, unit and linked conversion; edits require catalog manager/admin role and reject mutation of referenced identity |
| GET/POST /api/products/<id>/barcodes | Review/add exact mappings; no stock mutation |
| GET /api/inventory/resolve-barcode?code=001234567890&purpose=confirmed | exact/ambiguous/unknown and eligible candidates; authenticated |
| GET /api/inventory/locations | Only authorized inventory locations; independent of Schedule's directory |
| GET /api/inventory/balances | Authorized balances with unit, version, opening confidence; no guessed comparable totals |
| POST /api/inventory/commands | Kind-specific validated input routed to post_inventory; require Idempotency-Key |
| GET /api/inventory/documents/<id> | Authorized immutable lines, movements, actor, and correction links |

No barcode scanning workflow screen is required here. The catalog detail can show/test a mapping; keyboard-wedge receiving/counting UI remains Task 6 of the master roadmap.

- [ ] Add browser checks for an unverified legacy product, a reviewed random SKU, a sealed set, and two confirmed designs sharing a manufacturer code.
- [ ] Add focused fields to existing product editing/detail panels: series, form, unit, design, conversion, barcode mapping, verified/unverified. Keep price, aliases, images, and Sheet sync intact.
- [ ] Show unknown cost as unknown; do not calculate margins or silently treat missing cost as zero. Show identity/stock confidence separately from a numeric quantity.
- [ ] On stock actions, display the unit beside quantity and a pre-submit effect summary. Block actions with unresolved identity or unverified openings in authoritative mode.
- [ ] Keep one request key per submission intent in the draft. Double-click or lost response reuses it. A changed payload gets a new key after explicit review; stale conflicts never silently resubmit a recomputed quantity.
- [ ] Surface shortage, stale_version, idempotency_conflict, reconciliation_required, forbidden, and inventory_busy distinctly; retain the user's draft.
- [ ] Test the report shorthand preview displays 12 boxes for 12*1 and a confirmed barcode requires explicit design selection. No automatic fuzzy stock deduction.
- [ ] Scope UI changes to Products/Stock/Restock. Preserve Today layout and Schedule sources; check shared navigation and role behavior with the existing browser harness.

Use API-returned native units and balance versions. Do not duplicate pack-size arithmetic independently in several React components.

**Checks:** browser runner check_inventory_core.py; existing Node tests; TypeScript verification build.

**Gate:** a staff member can tell which item/unit/location a supported action affects before posting and can recover safely from a conflict.

## Task 6: close every legacy writer and preserve compatibility meaning

**Files:** inventory_commands.py, db.py, blueprints/stock.py, restock.py, sales.py, inventory.py, products.py; init_db.py; frontend/src/pages/Dashboard/index.tsx for existing quantity labels; the supported action callers under frontend/src/pages/Stock/ and Restock/; tests/test_inventory_adapters.py; docs/inventory-core.md.

Start with this verified writer map, then search again after Build 1. Search all Python/scripts, including dynamic SQL, INSERT OR REPLACE, cascades, and deletes. Every hit needs an explicit classification.

| Existing path | Authoritative-mode outcome |
| --- | --- |
| stock.ru_dian | move between reviewed back-stock and floor locations via post_inventory |
| stock.restock_upstairs | receipt into reviewed back-stock location via post_inventory |
| stock.adjust_stock | reasoned manager correction with expected version via post_inventory; no silent absolute overwrite |
| stock.patch_stock | Notes-only update may remain; never update balances here |
| stock.delete_stock_rows | Reject nonempty/referenced stock; no balance/history deletion |
| sales.batch_stock_operation | Map unambiguous existing operations to commands; ambiguous ru_dian_claw blocked pending reviewed meaning |
| sales.submit_daily_report stock sections | Block legacy unit/provenance-ambiguous mutations with migration_required; later reviewed document intake replaces them. Do not run the old clamped SQL |
| sales._revert_report_day | No authoritative stock writer; retain legacy reconciliation conflict |
| sales upsert/batch/row delete/clear-day | Explicit aggregate-only legacy reporting; no new stock deduction or reversal |
| restock.complete_restock_session | One restock_complete command, keyed by session/source, updates completion and all movements atomically |
| restock.delete_restock_session | Completed documents retained; draft-only deletion remains |
| inventory.submit_inventory_check | Observation-only legacy record, clearly labeled; no hidden adjustment |
| inventory._calc_theoretical_qty | In authoritative mode read verified balance/version; do not subtract daily_sales or add posted restocks again |
| products.bulk_delete_products | Referenced IDs cannot be deleted; preserve history/files on rejection |
| db._ensure_stock_row | Only compatibility metadata/zero projection under controlled posting; cannot become a new source of quantity |
| db legacy migrations | Run once against legacy data; guard against rewriting authoritative quantities on restart |
| init_db.py | Catalog-only import; preserve IDs and verified identity/unit metadata |
| Any newly discovered writer | Migrate to the command, disable in authoritative mode, or prove read-only; no unclassified exceptions |

Compatibility projection rules:

1. For each verified one-to-one legacy product mapping, project native quantities into the existing floor/back-stock fields inside the posting transaction.
2. Do not fold separate sealed-set and random SKUs into one legacy row or sum unlike native units.
3. Preserve claw_qty as legacy/unverified metadata until its overlap is resolved; never add it as a third physical balance automatically.
4. Existing summary endpoints return totals grouped by native unit (and equivalent quantities only for a reviewed compatible series). Update existing Stock/Dashboard quantity labels to consume those grouped totals; a mixed/unverified total displays unavailable or incomplete, never a manufactured single number. Product counts remain distinct item counts. No Today layout redesign.
5. Unmapped rows remain visibly unverified and read-only in authoritative mode. An aggregate over any unverified member is labeled incomplete.
6. Legacy sales aggregates do not represent a new sale transaction. Display their stock allocation as unverified/legacy; actual unified sale allocation belongs to master Task 7.

- [ ] Add one API regression per writer classification, including manager deletes, scripts/imports, restock retry, and report bypass attempts.
- [ ] Implement mode dispatch at the existing route boundaries. In authoritative mode no route may fall back to its old stock SQL after a conflict.
- [ ] Generate legacy transactions/movements needed by current history screens as a compatibility projection in the same command transaction; give each a durable link to its inventory document and actual store.
- [ ] Make restock completion's session status and stock posting inseparable. Retrying a completed session/source returns its recorded result, not another posting.
- [ ] Keep sales summaries and inventory observations honest without changing their financial meaning. Update theoretical stock only in authoritative mode; legacy mode keeps the Build 1 behavior.
- [ ] Review all frontend call sites for request keys and current balance versions. If an old screen cannot express a safe action, disable that action with an explanatory response rather than retaining another writer.
- [ ] Run the source writer search again and attach the final classified results to docs/inventory-core.md.

Useful search, repository root:

~~~powershell
rg -n -i '(insert|update|delete|replace).*(stock|inventory_balances|inventory_movements)' popcore_app scripts -g '*.py'
~~~

Do not treat absence of this literal regex as proof by itself; inspect dynamic SQL and foreign-key cascades in the edited call graph.

**Gate:** every active stock path is accounted for; adapter tests prove no double deduction and no fallback to legacy writes in authoritative mode.

## Task 7: real contention, migration rehearsal, and recovery

**Files:** tests/test_inventory_posting.py, test_inventory_migration.py; scripts/rehearse_inventory_migration.py; docs/inventory-core.md.

**Rehearsal command contract:**

~~~powershell
.\.venv\Scripts\python.exe scripts/rehearse_inventory_migration.py --fixture representative --output-dir .local/build-2/rehearsal
~~~

The script creates its own synthetic database and refuses an existing output database. It accepts fixture names empty/representative/ambiguous; it does not accept a production database path or contact the droplet. Use SQLite backup for its consistent rehearsal snapshot. Resolve and assert every output path remains inside the explicitly selected D:-local rehearsal directory.

- [ ] Test two independent connections consuming 4 from available 5 with different keys and the same expected version. One succeeds; one returns stale_version or shortage; final quantity 1. Re-read/review with a new key proves the remaining 1 cannot satisfy a new consumption of 4.
- [ ] Test two concurrent requests with the same key/body: one document/effect; the second returns the same stored result after the first commits.
- [ ] Hold a write lock before the second command starts; verify bounded inventory_busy, release the lock, and retry the same key. Never place a barrier after one worker already holds the lock.
- [ ] Inject failure after movement insertion, balance update, compatibility update, and source transition. Each failure rolls back the entire document.
- [ ] Simulate commit success followed by lost response and process restart. Same key yields the saved response with unchanged document counts.
- [ ] Migrate empty and representative legacy fixtures twice, checking _migrations, existing IDs, aliases, sheet_ref, image references, sales/count history, Schedule tables, foreign_key_check, and integrity_check.
- [ ] For ambiguous claw/set identity fixtures, print explicit unresolved mappings and refuse authoritative activation. Do not invent opening balances.
- [ ] For the fully reviewed synthetic fixture, freeze fixture writers, validate openings by product/location/unit, post opening documents, reconcile every projection, then set authoritative mode in the controlled cutover transaction.
- [ ] Restart the app against this fixture with all paths rebound; prove import-time migration does not reapply legacy unit multiplication or start scheduled jobs.
- [ ] Rehearse pre-cutover restore of the disposable snapshot. For post-cutover recovery, freeze writes, retain all new documents, reconstruct/check balances and apply reviewed forward corrections; never restore away new real postings.
- [ ] Run receive 1 set -> open to 12 boxes -> move 5 -> consume 2 -> correction with history. Assert every step's native and equivalent totals and retry behavior.
- [ ] Verify DT and MK independently; hold/display/transit do not appear as saleable, and unknown openings do not appear as trustworthy zeros.

Movement-to-balance reconciliation query:

~~~sql
SELECT product_id, location_id, disposition, SUM(quantity) AS quantity
FROM inventory_movements
GROUP BY product_id, location_id, disposition;
~~~

Compare both directions against inventory_balances, including missing and zero rows; checking only joined rows can hide an orphan.

**Gate:** fixture migrations are repeatable, invalid cutovers are blocked, ledger/projection totals reconcile, concurrency tests pass, and the recovery record explains what remains before real store activation.

## Final verification and handoff

~~~powershell
Set-Location D:\dev\POPCORE
New-Item -ItemType Directory -Path .local\tmp -Force | Out-Null
$env:TEMP = 'D:\dev\POPCORE\.local\tmp'
$env:TMP = $env:TEMP
$env:DISABLE_SCHEDULER = '1'
$env:PLAYWRIGHT_BROWSERS_PATH = 'D:\dev\POPCORE\.local\ms-playwright'
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m unittest discover -s popcore_app/tests -v
.\.venv\Scripts\python.exe popcore_app/tests/browser/check_foundation.py
.\.venv\Scripts\python.exe popcore_app/tests/browser/check_inventory_core.py
.\.venv\Scripts\python.exe scripts/rehearse_inventory_migration.py --fixture representative --output-dir .local/build-2/rehearsal
Set-Location popcore_app\frontend
npm test
npm run build -- --outDir ../../.local/frontend-build
Set-Location D:\dev\POPCORE
git -c safe.directory=D:/dev/POPCORE diff --check
git -c safe.directory=D:/dev/POPCORE status --short --branch
~~~

- [ ] Use a fresh output directory for each rehearsal; do not remove an existing result to make the command pass.
- [ ] Review the final writer map, transactions, access checks, unit constraints, and all changed files against this scope.
- [ ] Compare Schedule and committed static hashes with the entry snapshot. Shared dependency/auth checks must still pass.
- [ ] Deliver docs/inventory-core.md with schema/command contracts, runnable examples, writer classifications, fixture evidence, limitations, and pre-/post-cutover recovery instructions.
- [ ] Mark the deliverable accurately: local inventory core ready, real store authority not enabled. Physical opening counts, approved access mapping, and a complete store-day pilot remain launch gates.
- [ ] Hand off to master Task 6 for receiving, scanning, picked/received restock, partial transfers, and approved count workflows. Do not implement those screens under this build.

## Requirement coverage

| Master scope | This plan |
| --- | --- |
| Task 4: IDs, forms, native units, barcodes, Sheet behavior | Tasks 1 and 5 |
| Task 4: location/access and shelf provenance | Tasks 2 and 4 |
| Task 5: atomic commands, immutable history, idempotency | Tasks 3–4 |
| Task 5: every writer and compatibility | Task 6 |
| Task 5: migration, contention, recovery | Task 7 |
| Schedule freeze; staff shifts first on later Today | Global constraints; master Task 10 retained |
| Opening truth, partial workflows, payments, closing, integrations | Explicit later phase/activation gates; no fabricated legacy facts |
