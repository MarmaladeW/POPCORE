# Inventory core development runbook

Build 2 adds a local inventory core. It does not activate real store inventory, import droplet data, or change Schedule. Real activation still requires reviewed physical opening counts and explicit Auth0 subject-to-store access grants.

## Catalog identity

Every authoritative product has a stable existing `products.id` plus reviewed metadata: `series_id`, `stock_form`, `stock_unit`, optional `design_name`, and `identity_status=verified`. Supported forms are sealed set, random box, confirmed design, and piece. Native units are set, box, and piece. Set-to-box conversions are versioned and become immutable once used.

Manufacturer barcodes may intentionally resolve to multiple confirmed designs. Internal barcodes remain unique. Barcode text is never converted to a number, so leading zeroes are preserved. Ambiguous confirmed-design results require an explicit selection.

## Locations and access

Inventory locations are separate from Schedule store membership. The migration creates DT floor/upstairs and MK floor/warehouse only. An Auth0 subject needs an explicit `inventory_access` row for each store, in addition to a sufficient JWT role. MT has no invented inventory locations.

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
