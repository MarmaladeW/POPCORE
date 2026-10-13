# Inventory core development runbook

Build 2 adds a local inventory core. It does not activate real store inventory, import droplet data, or change Schedule. Real activation still requires reviewed physical opening counts and explicit Auth0 subject-to-store access grants.

## Catalog identity

Every authoritative product has a stable existing `products.id` plus reviewed metadata: `series_id`, `stock_form`, `stock_unit`, optional `design_name`, and `identity_status=verified`. Supported forms are sealed set, random box, confirmed design, and ordinary item. Native units are set, box, and piece. Set-to-box conversions are versioned and become immutable once used.

Manufacturer barcodes may intentionally resolve to multiple confirmed designs. Internal barcodes remain unique. Barcode text is never converted to a number, so leading zeroes are preserved. Ambiguous confirmed-design results require an explicit selection.

Products → Import accepts catalog CSV or tab-separated rows with a header and an explicit SKU, including the columns from Products → Export. Managers review proposed creations and metadata updates before confirming. Existing products are identified by exact SKU; omitted columns stay unchanged, while blank supplied cells clear those catalog values. Stock/quantity columns and inventory identity fields are rejected. Confirmation is atomic, checks the preview against current catalog values, and never changes stock, inventory balances, or stock history.

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

## Recovery

Before cutover, freeze writers, create a consistent SQLite backup, validate every opening by product/location/native unit, resolve all ambiguous identities, and confirm explicit access. A failed pre-cutover rehearsal may restore its disposable snapshot.

After real cutover, never restore away newer inventory documents. Freeze writes, retain the database, compare every movement sum against every balance in both directions, review compatibility rows, and record forward correction documents for confirmed differences. Keep the original documents and reasons.

## Current limits

Real opening counts, production access mappings, and a complete store-day pilot remain launch gates. No production database was read or changed. Build 3 scanner, delivery, partial receiving, transfer, and reviewed-count behavior is documented in `docs/goods-handling.md`. Build 4 sale allocation and closing now consume this ledger through immutable inventory documents; see `docs/sales-and-closing.md`. Legacy claw quantity remains visible only as unverified metadata. Unknown or unverified scopes do not become trustworthy zero balances.
