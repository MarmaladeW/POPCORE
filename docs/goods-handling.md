# Goods handling development runbook

Build 3 adds local receiving, delivery, transfer, restock, count, and floor-target workflows. It does not activate production inventory, import droplet data, change Schedule, deploy, or connect external stock channels.

## Workflow rules

- A receipt is saved as a draft, then posted once with its original idempotency key. Only actual saleable, damaged, and held quantities post; an expected quantity is reference data. A known discrepancy requires a note. Draft cancellation posts no inventory.
- A restock or transfer is a delivery. Dispatch or pick moves source saleable stock into delivery-owned transit. Receipt moves only that outstanding transit to the stored destination. Return restores the actual source. A manager may resolve confirmed transit loss with a reason.
- Unfilled requested quantities remain visible. `short-close` requires a reason and does not invent a stock movement. A delivery completes only when transit is zero and every requested unit is received, returned, lost, or short-closed.
- A selected opened set retains its opening document and purpose while allocated to a delivery. Partial receipt restores that provenance at the destination; return restores it at the source. Generic inventory commands cannot name transit.
- A physical count captures the current quantity and balance version. Submission freezes the observation. Manager approval posts only the reviewed difference through the inventory ledger. A stale balance requires recount, and a count cannot erase protected opened-set units.
- Returning a submitted count retains it as `returned` and creates a new draft recount. A zero-difference approval creates no fake stock movement.
- Floor min/max targets produce suggestions only. Suggestions subtract outstanding inbound delivery quantities and are bounded by loose back-stock saleable quantity. They never post stock automatically.

## Whole sets on the floor

整端 whole sets are kept upstairs or in the warehouse; a floor holds blind boxes as boxes only (owner, 2026-10-29), and a set is no longer tracked as a set once it is downstairs (owner, 2026-11-06). The daily report already opened an 出库入店 `12*1` line this way. Deliveries now follow the same rule.

- **A sealed set received saleable on a floor lands as its boxes.** The receive posts one `move` document: the set's line still takes the set out of delivery transit, and its destination is the blind box's floor balance with `output_per_input × N` boxes (`inventory_commands._land_sets_as_boxes`, named by `sets_open_at`). The document line keeps the set, the quantity in sets and the conversion it used; the delivery line still counts sets. The boxes are loose: no `inventory_open_sets` row is kept, so a sale or the daily report takes them without asking. This holds for a restock and for a transfer whose destination is a floor. A transfer into upstairs or the warehouse keeps the set whole, and a set received as held or damaged stays a set.
- A sealed set whose size was never recorded (no conversion to a verified blind box) is refused with `set_size_unknown`: the size is never assumed. It can go back to its source, and the other lines are received as usual.
- `restock_complete` (a live sheet completed without a delivery) lands sets the same way; its set line's `expected_versions.to` names the box balance on the floor.
- `delivery_detail` gives each line `boxes_per_set` (when it is a set heading to a floor) and `landed_boxes` (the boxes it already put there, 0 for sets received before this rule), which the restock and transfer screens show as `1 端 = 12 盒`.

**The picker records what came down.** `PATCH /api/restock/items/<id>/pick` accepts a found quantity above the requested one (up to 999). The sheet keeps what was asked; the delivery line's `requested_quantity` is the larger of the two, so the ledger's own checks still hold. Confirming the pick still refuses what the source does not hold. Picks stay locked once the goods are dispatched.

**The floor counts what it sees.** On a restock, `receive` accepts a line quantity above what is in transit, including a line the picker marked out of stock: the difference comes straight off the source in the same document (source saleable → transit → floor), the delivery line and the sheet's found quantity rise to what really came down, and a second `dispatch` event (reason `Counted on the floor above the recorded pick`) records who counted it. It is refused with `insufficient_stock` when the source does not hold the difference, and nothing moves. A count below the pick leaves the rest in transit, as before: it is received later or returned to the source. A transfer is never received above what was sent, because its receiver has no say over the sending store's stock.

**Sets already on a floor.** `scripts/open_floor_sets.py --db <db>` lists, read-only, every sealed set with saleable stock on a live store's floor and the boxes it becomes, and says which it must leave (size never recorded, product not verified). `--apply --backup <new path> --actor-sub <Auth0 subject>` takes the write lock, plans again under it, takes a checked SQLite backup, posts one `correction` document per store (each set off the floor, its boxes onto the same floor; source type `floor_sets_opened`), checks that every planned floor is what the plan said, that no balance drifted from its movements and that integrity and foreign keys hold, then commits. Any failed check rolls everything back and the backup stays. `--store DT` limits it to one store; a second run finds nothing left. The actor needs `inventory_access` for each store it changes.

## Local screens

Select a specific store and open **Inventory** for the series roster. Each design has **Receive** and **Count** actions; the Goods workflow routes remain available for receiving, transfers, and counts. Transfer opens unfinished incoming transfers across all dates; managers can create a transfer separately. Restock keeps its request and picking screens; the **Receive** tab accepts partial received or returned quantities, with 0 leaving an item in transit. An unconfirmed request keeps the original quantities and request key for Retry.

New count-create request keys hash the explicit observations rather than changing captured balance facts. Retrying after a lost response returns the original draft and its original captured versions; changed observations under the same key are rejected. Legacy keys remain replayable when their original captured facts are unchanged; older ambiguous requests whose stock has moved still require reconciliation.

Saved receipts, transfers, and counts keep their document IDs in the URL for reload and resuming. Draft counts and recounts allow observation edits; save these before submission. Submitted, approved, and returned observations remain frozen, and returned counts link to their replacement recount.

A receipt or transfer can hold several products. Scan a barcode or search to add each one; scanning or picking a product already listed adds to its line. Products with an unverified identity or no stock unit appear in search but cannot be added, and the server refuses them too. A draft receipt can add and remove lines until it is posted, keeping at least one. On a transfer each line has its own quantity, and **Dispatch all** / **Receive all** act on every open line in one request with one idempotency key and version check.

Barcode input is text, so leading zeroes are retained. Enter adds one configured `quantity_per_scan`. Unknown or ambiguous scans do not change the draft. The receipt screen warns before a browser close or reload when local input is unsaved.

## Identify an existing blind box

From a series, choose **Identify boxes**, the source random-box product, exact named design, and reviewed location. Specify loose boxes or the actual retained opened set, enter the number physically identified and a reason, and review the quantity effect. Already identified deliveries use **Receive** on the named design instead.

`POST /api/goods/identify` requires staff access, an idempotency key, verified matching series identities, and both current saleable balance versions. One transaction consumes N boxes and receives N pieces at the same location. It preserves the selected opened-set reference and reason in the ledger and records the paired documents for identical replay. Either both postings succeed or neither does. It creates no trade eligibility. Stale versions, insufficient stock, unreviewed openings, and mismatched identities reject the entire operation.

An uncertain response keeps the original body/key and prevents editing or changing stores until Retry confirms the result. Refresh inventory after a confirmed conflict before submitting a revised action.

## API and access

Goods endpoints are under `/api/goods`. Lifecycle writes require `Idempotency-Key` and `expected_version`. Staff also need explicit `inventory_access` for the store where they act. Transfer creation requires a manager with both store scopes. Source staff can dispatch the stored plan; destination staff can receive it without gaining general source-store access.

Restock lifecycle endpoints are `/api/restock/session/<id>/pick`, `/receive`, `/return`, `/short-close`, and manager-only `/resolve-loss`. Existing completed records remain history. Once a delivery starts, its picked quantities and parent session cannot be edited or deleted around the delivery ledger.

## Verification

From `D:\dev\POPCORE`, with temporary and Playwright paths on D::

```powershell
$env:DISABLE_SCHEDULER = '1'
$env:TEMP = 'D:\dev\POPCORE\.local\tmp'
$env:TMP = $env:TEMP
$env:PLAYWRIGHT_BROWSERS_PATH = 'D:\dev\POPCORE\.local\playwright-browsers'
.\.venv\Scripts\python.exe -m unittest discover -s popcore_app\tests -v
.\.venv\Scripts\python.exe popcore_app\tests\browser\check_foundation.py
.\.venv\Scripts\python.exe popcore_app\tests\browser\check_inventory_core.py
.\.venv\Scripts\python.exe popcore_app\tests\browser\check_goods_flow.py
.\.venv\Scripts\python.exe popcore_app\tests\browser\check_transfer_receiving.py
.\.venv\Scripts\python.exe popcore_app\tests\browser\check_restock_receiving.py
.\.venv\Scripts\python.exe popcore_app\tests\browser\check_restock_sets.py
.\.venv\Scripts\python.exe popcore_app\tests\browser\check_store_day.py
Set-Location popcore_app\frontend
npm test
npm run build -- --outDir ../../.local/build-3/frontend
```

The browser check covers the goods routes at 390, 768, and 1440 pixels. Backend API tests use isolated SQLite databases, synthetic DT/MK data, test identities, and blocked external requests.

## Production gates

Real product identity review, opening counts, inventory access rows, scanner-device confirmation, Auth0 login, representative store workflow rehearsal, and production backup/recovery approval remain required. Build 4 closing reads these outcomes without reposting them; see `docs/sales-and-closing.md`. No real store data was read or changed by Build 3.
