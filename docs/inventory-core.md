# Inventory core development runbook

Build 2 adds a local inventory core. It does not activate real store inventory, import droplet data, or change Schedule. Real activation still requires reviewed physical opening counts and explicit Auth0 subject-to-store access grants.

## Catalog identity

Every authoritative product has a stable existing `products.id` plus reviewed metadata: `series_id`, `stock_form`, `stock_unit`, optional `design_name`, and `identity_status=verified`. Supported forms are sealed set, random box, confirmed design, and ordinary item. Native units are set, box, and piece. Set-to-box conversions are versioned and become immutable once used.

Manufacturer barcodes may intentionally resolve to multiple confirmed designs. Internal barcodes remain unique. Barcode text is never converted to a number, so leading zeroes are preserved. Ambiguous confirmed-design results require an explicit selection.

Products → Import accepts catalog CSV or tab-separated rows with a header and an explicit SKU, including the columns from Products → Export. Managers review proposed creations and metadata updates before confirming. Existing products are identified by exact SKU; omitted columns stay unchanged, while blank supplied cells clear those catalog values. Stock/quantity columns and inventory identity fields are rejected. Confirmation is atomic, checks the preview against current catalog values, and never changes stock, inventory balances, or stock history.

### Bulk identity review sheet

Products → Export identity sheet and Import identity sheet (managers and admins, desktop and mobile) review identity in bulk before opening counts. They change identity fields only.

- `GET /api/products/identity-sheet` returns a CSV with `product_id, SKU, name, 记账名, legacy_series, series_id, series, stock_form, stock_unit, design_name, identity_status`. `store_code=DT` limits it to products with legacy stock at that store (`stock.instore_qty` or `upstairs_qty` above zero); `unverified=1` leaves out verified products.
- `POST /api/products/identity-import/preview` takes the edited sheet as CSV or tab-separated `text`. Rows match by `product_id` or exact SKU; when both are given they must agree. `name`, `记账名`, `legacy_series` and `identity_status` are reference-only and ignored; any other unknown column (including stock quantities) rejects the whole sheet. Omitted identity columns keep their current values; blank supplied cells clear them. A blank `stock_unit` takes the unit fixed by `stock_form`. A row with no identity entered is skipped. Every other row is marked `identity_status=verified`, so remove or blank rows you have not reviewed.
- Series are chosen by `series_id` or exact `series` name. A name that does not exist yet is proposed as a new series and listed in the preview; a name that differs from an existing or proposed series only by case, width or spacing is rejected (the same rule as series setup).
- The preview runs each row through `update_product_identity` in a transaction that is always rolled back, so rows are rejected with the same messages and codes as the single-product editor (`identity_locked`, `design_conflict`, missing conversion for a sealed set, form/unit mismatch). Later rows are checked against earlier accepted rows, as confirm will apply them. Rejected rows show their reason and are not sent to confirm.
- `POST /api/products/identity-import/confirm` requires an `Idempotency-Key` and the accepted preview rows. It re-checks each row's preview snapshot against current values (`stale_preview` otherwise; a row already at its target is counted unchanged), creates any new series, applies every row through `update_product_identity`, and commits all or nothing. The same key and rows return the stored result; the same key with different rows returns `idempotency_conflict`.
- Sealed sets still need a reviewed conversion before they can be verified, so verify them in the product editor after adding the conversion. The import never writes stock, balances, movements or stock history.

Local checks: `test_identity_sheet.py` and `tests/browser/check_identity_sheet.py` (real Flask API, disposable SQLite, mocked authentication).

Products → Sync from Google Sheet is available to managers and admins on desktop and mobile. It retains its separate preview and confirmation steps. Catalog controls remain available when the selected store's stock cannot be read; unavailable stock is shown as unknown.

## Series and confirmed designs

`/stock` groups explicit `product_series` membership. Each confirmed design is its own stable product ID, stocked in pieces. Blind boxes and sealed sets remain separate products in boxes and sets; the series has no additional stock balance. Existing unassigned products remain accessible in `/stock/products` and Products for reviewed identity assignment. Names and legacy `ip_series` text never silently map products into a reviewed series.

Managers can create a series with a named roster or add names to an existing series. The 6/9/12 name-entry presets do not imply a conversion factor, pack contents, or stock quantity; extra named designs are supported. Setup uses a single transaction and request key, rejects Unicode/case/whitespace-equivalent duplicate names, and writes no stock. Existing product IDs and history are not merged or rewritten.

`GET /api/inventory/series?store_code=DT` (or `ALL`) starts with the complete roster, including designs with no balance row. Quantities are trustworthy only in authoritative mode for a reviewed product at an active location with reviewed opening counts. Missing trusted balances are zero/version zero; all other quantities and versions are null. Saleable, hold, damaged, trade, display, and transit stay distinct. `ALL` includes only active authorized stores and offers no stock actions.

Series history filters immutable ledger movements by exact product and business-date range within the authorized store scope. It loads 100 rows per page with a movement-ID cursor. Document details include recorded actor, reason, source, native units and correction links; viewing a document requires access to every store it touches. New documents retain the posting reason; explicit consumed opened-set selections remain linked on document lines. The additive `add_inventory_audit_context` migration leaves historical fields null, since missing historical reasons or tray selections cannot be reconstructed safely. No prior movements or balances are rewritten.

The stock view filters unknown, out-of-stock, floor replenishment, and held/damaged products. Unknown quantities never match out-of-stock, and replenishment requires positive saleable back stock in the same store. Exact-product reference photos prefer the existing general image and load through authenticated requests, with retry for missing or unreadable images. Photos remain managed in Products.

Count series and Receive series collect quantities across confirmed designs at one reviewed location, then open the existing saved draft for review. Blank rows are omitted; an explicit zero count is retained. Stock details offer same-store saleable moves and set opening through an explicitly reviewed conversion. Loose-box moves exclude retained opened-set quantities. No action infers a conversion from a 6/9/12 design roster.

Product search, receiving, counts, transfers, checkout choices, and new sale snapshots use the named design identity. Receiving an already identified item adds stock to that design. Identifying an existing blind box instead consumes the source box and receives the chosen design atomically; see `goods-handling.md`. A refund or returned blind box never silently becomes an arbitrary confirmed design.

Local checks: `test_inventory_series.py`, `test_design_identification.py`, `test_design_inventory_flow.py`, `test_design_search.py`, and the `check_design_inventory.py`, `check_design_inventory_live.py`, `check_design_labels.py` browser scripts. Operations checks: `test_series_history.py`, `test_counts.py`, `inventoryAttention.test.ts`, and browser scripts `check_series_worksheet.py`, `check_series_stock_actions.py`, `check_series_history.py`, and `check_inventory_attention.py`. The live-named browser script uses a real local Flask API, disposable SQLite, and mocked authentication; it does not contact production.

## Locations and access

Inventory locations are separate from Schedule store membership. The migration creates DT floor/upstairs and MK floor/warehouse only. An Auth0 subject needs an explicit `inventory_access` row for each store, in addition to a sufficient JWT role. MT has no invented inventory locations.

Admins manage these grants under **Users → Store operations access**. Check DT or MK on the relevant user's row (including your own) to grant access; uncheck to revoke. Changes save immediately and the checkbox updates after the server confirms. Retry Today after granting access. **Scheduling stores** is a separate control and does not grant operations access. Granting access does not enable authoritative stock or verify opening counts.

`GET /api/inventory/access` lists operations-ready stores and existing grants for admins, including admins who have no store grant yet. The existing `POST` grants one `auth0_sub`/`store_id` pair; `DELETE` idempotently revokes just that pair. All three methods require the admin role. Failed reads show an error; ambiguous writes reread saved grants before allowing another change.

Local verification: `test_inventory_access.py` covers role boundaries, self-grants, per-store revocation, idempotency and validation. `tests/browser/check_user_access.py` uses disposable SQLite data and mocked authentication to check Users controls, grant/revoke effects on Today, error recovery and desktop/mobile layouts. This does not grant access to live accounts or verify live Auth0.

## Posting contract

`POST /api/inventory/commands` requires an `Idempotency-Key` header and accepts receipt, move, consume, open_set, correction, and restock_complete commands. The public route rejects opening commands. Each line names a product, positive native quantity, affected location/disposition, and expected balance versions. Version zero means the balance does not yet exist.

The command owns `BEGIN IMMEDIATE` through commit or rollback. Authorization, replay, validation, immutable document/line/movement writes, balance updates, provenance, compatibility projection, and restock lifecycle transitions happen in that transaction. The same key and same normalized intent returns the stored response. Reusing a key for changed intent returns `idempotency_conflict`. Busy responses use `inventory_busy`; retry only the unchanged draft with the same key.

Balances are derived from immutable movements and use optimistic versions. Corrections append exact compensating movements and require a manager and reason. Opening documents cannot be casually reversed. A successful full correction can occur only once. Fresh opened-set allocations are explicit provenance; generic consumption uses loose stock first and requires selection before consuming protected stock.

## Existing writer classifications

| Existing path | Authoritative behavior |
| --- | --- |
| stock `ru_dian` | Atomic move from reviewed back stock to floor; request key required. |
| stock `restock_upstairs` | Atomic receipt into reviewed back stock; request key required. |
| stock absolute adjustment | Converts the reviewed difference to a reasoned manager correction; staff is denied. |
| stock notes | Notes only; never changes an authoritative balance. |
| stock row delete | Rejects any quantity or legacy/authoritative history. |
| batch restock / `ru_dian` / `out_dian` | One multi-line receipt, move, or consume command. |
| batch `ru_dian_claw` | Blocked with `migration_required`; claw overlap has no reviewed physical meaning. |
| daily report sales sections | Remain aggregate reporting only; they do not create authoritative movements. |
| daily report stock sections | Blocked with `migration_required`; old pack and provenance assumptions are not trusted. |
| report resubmission with old stock history | Remains blocked with `reconciliation_required`. |
| restock completion | Authoritative sessions use the Build 3 delivery lifecycle: pick to owned transit, then receive, return, loss resolution, or reasoned short-close. Older completed `restock_complete` documents remain history. |
| completed restock deletion | Rejected; use a later correction. Draft session deletion remains available. |
| inventory check | Legacy checks remain observations. Build 3 goods counts capture a balance version, freeze on submit, and post a reviewed correction only on manager approval. |
| product deletion | Rejected when referenced by inventory movements, conversions, barcodes, or existing operational history. |
| database legacy unit migration | Remains one-time through `_migrations`; it cannot rerun on restart. |
| catalog import | Catalog only. It must preserve product IDs and reviewed identity fields. |

The compatibility projection updates only matching native product rows: floor maps to `instore_qty`, and upstairs/warehouse maps to `upstairs_qty`. It never folds sealed sets into random boxes and never treats `claw_qty` as a third physical balance. Compatibility transactions and movements carry `inventory_document_id`.

## Rehearsal

Run from `D:\dev\POPCORE` with D:-local temporary and browser paths:

```powershell
$env:TEMP = 'D:\dev\POPCORE\.local\tmp'
$env:TMP = $env:TEMP
$env:PLAYWRIGHT_BROWSERS_PATH = 'D:\dev\POPCORE\.local\ms-playwright'
.\.venv\Scripts\python.exe scripts\rehearse_inventory_migration.py --fixture representative --output-dir .local\build-2\rehearsal-new
```

Use a fresh output directory. The script accepts only `empty`, `representative`, or `ambiguous`, creates its own synthetic database, refuses an existing output database, and accepts no production path. It stores a pre-cutover SQLite backup and a JSON report. The ambiguous fixture records unresolved claw meaning and stays in legacy mode. The representative fixture activates DT and MK independently, exercises set opening, move, consumption, exact correction, retry, integrity, foreign keys, and movement-to-balance reconciliation.

## Opening counts (real activation)

Real activation goes through `scripts/inventory_opening.py` only. The public commands route keeps rejecting `opening`. The script posts one opening document for every active location (DT floor, DT upstairs, MK floor, MK warehouse) in a single transaction, so `inventory_mode` turns authoritative only once every location has a verified opening. Stores must move sales to Enter Sale or the in-app checkout at the same time, because daily-report stock lines are blocked in authoritative mode and its sales lines never deduct stock.

1. **Prerequisites.** Each product with legacy stock that will be counted must be identity-verified (`stock_form`, `stock_unit`, `identity_status=verified`). The approving admin needs an `inventory_access` grant for DT and MK.
2. **Count sheets.** Managers download `GET /api/products/count-sheet?store_code=DT&location=floor` (the manager role plus inventory access to that store is required; `scope=catalog` lists every product). On the server, run `python scripts/inventory_opening.py count-sheet --db <db> --store DT --location floor --output <file>.csv` instead. The default `legacy` scope lists every product with legacy floor or back stock at that store. Sheets are blind: they contain no expected quantities. Each row has the store, location, product ID, SKU, shop name (`记账名`), product name, design, barcode, stock form, native unit, identity status and empty `count`/`notes` columns.
3. **Counting.** Write whole native units (set, box or piece) in `count`. Write `0` when nothing was found; a blank row means not counted. Do not edit product_id, SKU or unit. If the spreadsheet reformats SKUs, clear the SKU column and keep product_id. Count only saleable stock. Damaged or display units are handled after activation with reasoned corrections. Save the file as CSV.
4. **Dry run (default).** `python scripts/inventory_opening.py intake --db <db> DT-floor.csv DT-upstairs.csv MK-floor.csv MK-warehouse.csv --business-date 2026-10-20 --actor-sub '<admin auth0 sub>' --report <report>.json`. The database is opened read-only. Each counted row must name exactly one product, by product_id or exact SKU (if both are given they must agree), plus an active store/location and the product's native unit. The script rejects unverified products, duplicates across files, names- or barcode-only rows and invalid counts. It also rejects verified products whose legacy stock at a counted location has no count, and any location with no rows. Use `--empty-location MK-warehouse` for a location that was counted and is empty. The report compares every count with legacy `instore_qty` (floor) or `upstairs_qty` (upstairs and warehouse). It also lists unverified legacy stock and `claw_qty` as warnings; both stay untrusted. The script exits non-zero while any error remains.
5. **Apply.** Stop the app (`systemctl stop popcore`) to freeze writers. Rerun the same command with `--apply --backup <new path>`. The script takes `BEGIN IMMEDIATE` and holds it through the review, a consistent SQLite backup (checked with `integrity_check`), the opening post through `post_inventory` (actor recorded, stable request key derived from the reviewed counts), marking every location's `inventory_scope_state.opening_verified`, zeroing compatibility rows for verified products counted as 0, and a reconciliation. The reconciliation covers integrity, new foreign-key errors, movement sums against balances in both directions, and verified `stock` rows against balances. Any failure rolls the whole cutover back, and the backup remains as the pre-cutover snapshot. Rerunning with the same files reports `already_applied` and writes nothing. Changed counts after activation are refused (`opening_closed`); fix them with corrections.
6. Keep the backup, the count files and both JSON reports. Restart the app only after sales entry has moved to Enter Sale or checkout.

Local checks: `test_inventory_opening.py`.

## Recovery

Before cutover, freeze writers, create a consistent SQLite backup, validate every opening by product/location/native unit, resolve all ambiguous identities, and confirm explicit access. A failed pre-cutover rehearsal may restore its disposable snapshot.

After real cutover, never restore away newer inventory documents. Freeze writes, retain the database, compare every movement sum against every balance in both directions, review compatibility rows, and record forward correction documents for confirmed differences. Keep the original documents and reasons.

## Current limits

Real opening counts, production access mappings, and a complete store-day pilot remain launch gates. No production database was read or changed. Build 3 scanner, delivery, partial receiving, transfer, and reviewed-count behavior is documented in `docs/goods-handling.md`. Build 4 sale allocation and closing now consume this ledger through immutable inventory documents; see `docs/sales-and-closing.md`. Legacy claw quantity remains visible only as unverified metadata. Unknown or unverified scopes do not become trustworthy zero balances.
