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

## Local screens

Select a specific store and open **Inventory** for the series roster. Each design has **Receive** and **Count** actions; the Goods workflow routes remain available for receiving, transfers, and counts. Transfer opens unfinished incoming transfers across all dates; managers can create a transfer separately. Restock keeps its request and picking screens; the **Receive** tab accepts partial received or returned quantities, with 0 leaving an item in transit. An unconfirmed request keeps the original quantities and request key for Retry.

New count-create request keys hash the explicit observations rather than changing captured balance facts. Retrying after a lost response returns the original draft and its original captured versions; changed observations under the same key are rejected. Legacy keys remain replayable when their original captured facts are unchanged; older ambiguous requests whose stock has moved still require reconciliation.

Saved receipts, transfers, and counts keep their document IDs in the URL for reload and resuming. Draft counts and recounts allow observation edits; save these before submission. Submitted, approved, and returned observations remain frozen, and returned counts link to their replacement recount.

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
.\.venv\Scripts\python.exe popcore_app\tests\browser\check_store_day.py
Set-Location popcore_app\frontend
npm test
npm run build -- --outDir ../../.local/build-3/frontend
```

The browser check covers the goods routes at 390, 768, and 1440 pixels. Backend API tests use isolated SQLite databases, synthetic DT/MK data, test identities, and blocked external requests.

## Production gates

Real product identity review, opening counts, inventory access rows, scanner-device confirmation, Auth0 login, representative store workflow rehearsal, and production backup/recovery approval remain required. Build 4 closing reads these outcomes without reposting them; see `docs/sales-and-closing.md`. No real store data was read or changed by Build 3.
