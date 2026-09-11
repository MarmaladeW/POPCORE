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

Select a specific store, open **Stock**, then choose **Receive**, **Transfer**, or **Count**. Restock keeps its existing request and picking screen; physical pick confirmation now creates transit, and the new **Receive** tab confirms arrival or return.

Barcode input is text, so leading zeroes are retained. Enter adds one configured `quantity_per_scan`. Unknown or ambiguous scans do not change the draft. The receipt screen warns before a browser close or reload when local input is unsaved.

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
Set-Location popcore_app\frontend
npm test
npm run build -- --outDir ../../.local/build-3/frontend
```

The browser check covers the goods routes at 390, 768, and 1440 pixels. Backend API tests use isolated SQLite databases, synthetic DT/MK data, test identities, and blocked external requests.

## Production gates

Real product identity review, opening counts, inventory access rows, scanner-device confirmation, Auth0 login, representative store workflow rehearsal, and production backup/recovery approval remain required. Build 4 closing reads these outcomes without reposting them; see `docs/sales-and-closing.md`. No real store data was read or changed by Build 3.
