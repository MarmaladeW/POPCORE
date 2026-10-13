# Design inventory implementation plan

## Goal and design
Make inventory usable as the store inventory authority with explicit series and one independently stocked product per confirmed design. Preserve the existing product/location/disposition ledger. Series membership is grouping, never another stock balance. Sealed sets, unidentified boxes, and confirmed pieces stay separate. A roster of 6/9/12 names does not imply a pack size, guaranteed contents, or on-hand quantities. Extra named designs use the same independent stock model.

The user authorized inventory implementation. The origin and standard/secret lineup questions were unanswered. Both already identified receiving and identifying existing boxes are supported; extra named designs stay explicit, without inventing stock or guaranteed contents. No automatic catalog mapping, opening balances, conversion, activation, integration, commit, or deployment.

## Tasks
- [x] Scoped series inventory API: verified quantities by active location/disposition, unknown for unreviewed scopes, explicit unassigned catalog, movement history from ledger. Test separate identities, trusted zero, unknown, and denied scope.
- [x] Catalog setup: atomic manager creation of a series and explicitly named design products, or addition to an existing series. Reject duplicate normalized design names, preserve IDs/history, require idempotency; no stock writes or inferred quantities. Support 6/9/12 name-entry presets without restricting extra designs or tying these to pack size.
- [x] Atomic identify-box workflow: source random_box and selected confirmed_design in same reviewed series/location; positive quantities, exact versions, request replay, preserved opened-set selection, one consume plus one receipt in one transaction. Never create trade eligibility. Verify rollback, access, stale state, retry, and quantity conservation.
- [x] Inventory UI: series list/details by location, distinct sealed/random/design sections, readable names and counts, setup, identify, stock history, and exact-product receive/count entry. Retain legacy product view for unmapped catalog. Explicit stale/error/unknown states.
- [x] Product search and transaction selection/snapshots identify the actual named design; verify a design sale does not consume another design or blind boxes.
- [x] Independent review, full backend/frontend tests, isolated production-mode build, mobile/desktop integrated browser flow. Keep release assets unchanged.

## API contract
GET /api/inventory/series?store_code=DT (or ALL), optional q and series_id. Returns mode, locations, unassigned_count, series array. A series has id,name,products. Each product has id,sku,name,series_id,stock_form,stock_unit,design_name,identity_status,balances. Each balance has location_id,disposition,quantity (null if untrusted),version (null if untrusted). Locations include id,store_id,store_code,name,code,opening_verified. Missing balance is zero only for verified products at reviewed locations in authoritative mode.
GET /api/inventory/series/<id>/history?store_code=DT: items containing movement id,document_id,kind,product_id,product_name,location_id,location_name,disposition,quantity,business_date,posted_at,reason. Only authorized scope.
POST /api/product-series/setup with Idempotency-Key and {series_id? or name,design_names:[string]} returns {id,name,product_ids:[number]}. Exactly supplied unique names, no stock.
POST /api/goods/identify with Idempotency-Key and {source_product_id,design_product_id,location_id,quantity,source_version,target_version,business_date,reason,open_set_id?} returns {consume_document_id,receipt_document_id}. Separate source consume and target receipt commit atomically.

## Verification and review (2026-09-30)

- Full backend suite: 580 tests passed after all inventory, stable sale retry, and additive audit migration changes; `.local/store-cleanup-checks/design-backend-final.log`.
- Frontend: 34 tests passed; production-mode build passed to `.local/frontend-build`. Existing Vite chunk-size warning remains. Committed static assets and package-lock remain unchanged.
- Browser: series explorer/setup/identify at390/1440,12design mobile layout, opened-set selection, trusted zero/unknown, scope changes, identical uncertain retries; exact labels for saved receipts/counts/transfers and sale allocation. Real Flask/browser identification confirms a committed action with a lost response replays without duplicate stock and refreshes exact history.
- Affected foundation, inventory core, goods flow, store day, and staff/manager navigation checks passed. The pre-existing Schedule save-button position failure remains reproducible on the unchanged base checkout; Schedule source was not edited. See the earlier cleanup plan and `.local/store-cleanup-checks/schedule-baseline.log`.
- Recovery rehearsal passed: integrity ok, zero foreign-key errors, all3 referenced attachments restored; no production data.
- Independent review accepted and verified the audit-context fix: posting reason and selected opened set persist in immutable ledger records. Repeated additive migration retains all historical facts and leaves unavailable old context null. A separate review verified sale retry normalization preserves permissions and mismatch rejection.
- Stock-sensitive integration verifies receive named designs, identify boxes, sell the exact design, then repeat every request. Other designs, damaged stock, and random-box balances remain independent; ledger totals reconcile.
- No commit, push, deployment, production activation, stock import, real opening count, or live Auth0/provider/device claim. Existing delayed legacy sale request keys with changed captured facts may still409; see sales runbook.
