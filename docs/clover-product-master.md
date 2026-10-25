# Clover item list as the product master

Decided 2026-10-08: Clover's item list is the POPCORE product master. Its official names replace
the catalogue names; the existing 记账名 (`jizhanming`) stay as aliases so handwritten reports,
search and history keep working. Nothing here runs in production without an explicit go.

## Source

`.local/clover-catalog/clover-items-2026-10-08.csv` (git-ignored) holds 548 items parsed from the
three Clover "Items" page printouts of the live merchant: official name, price, cost and category.
The print truncated a few category labels; they were resolved from the items under them, and two
uncertain ones are flagged in the file. The print carries no Clover item IDs or product codes; those
are backfilled later from the Clover API once the production app is connected (the importer matches
by item ID when present, else by exact name, so a later rename is recognised by ID).

## Tools

- `scripts/import_clover_items.py --csv <file> [--db PATH] [--dry-run]` creates or updates one product
  per Clover item: `CL`-prefixed SKU, `name_cn_en` = official name, `brand` = category, price,
  `clover_item_name`, optional `clover_item_id`, product code → manufacturer barcode. Idempotent;
  never touches `jizhanming`, aliases, identity fields, stock or sales. Products carry two new
  columns, `clover_item_id` and `clover_item_name`, both unique when set.
- `scripts/merge_clover_products.py review --out review.csv` scores every legacy product (no
  `clover_item_name`) against the Clover products on 记账名, name and price and writes a review sheet
  with a `decision` column: confident matches are prefilled with the Clover product id; `check` rows
  need a human choice; `none` rows need a new Clover item or `keep`/`delete`. A decision may be the
  Clover product id or the exact Clover item name; the sheet lists the top three candidates per row.
- `scripts/merge_clover_products.py apply --csv review.csv [--dry-run]` applies each decided row in
  its own transaction: every table referencing the legacy product is re-pointed to the Clover
  product (stock, balances, sale lines, inventory documents, barcodes, report choices…), the legacy
  记账名 and name become aliases, identity fields the Clover product lacks are carried over, then
  the legacy row is deleted. A legacy product whose references would collide with the Clover
  product's own rows (for example both hold stock at one location) is refused and listed; resolve
  that stock first. `delete` works only for unreferenced products.
- The Clover checkout adapter now maps order lines by Clover item ID first, barcode second.
- `scripts/load_clover_match_review.py --sheet review.xlsx [--db PATH] [--dry-run] [--replace]` loads a
  filled review sheet into the in-app review (table `clover_match_reviews`, no foreign keys so a merge
  never re-points it). A row loads only when its legacy id still has the same 记账名 and name; an
  unknown or ambiguous Clover name loads as undecided with a note. Rows already edited or applied in
  the app are left alone (`--replace` overwrites edits, never applied rows).
- Products → **Clover matching** (`/products/clover-matching`, manager) lists every row with its
  status (no decision, ready, keep, applied, old product gone). Changing a decision saves at once and
  marks the row "edited here". Nothing merges until a manager selects ready rows and applies them;
  each row then runs the same merge as the script in its own transaction and records who applied it
  and what happened. A refused row changes nothing and stays open. Applied rows can no longer change.

## Production procedure (not yet executed)

1. Take a backup (`scripts/create_backup_package.py`) and keep it until the catalogue is verified.
2. Import: `import_clover_items.py --csv clover-items-2026-10-08.csv --dry-run`, then without `--dry-run`.
3. Review: `merge_clover_products.py review --out review.csv`; fill `decision` for every `check` and
   `none` row and spot-check the confident ones.
4. Load the sheet into the app (`load_clover_match_review.py --dry-run`, then for real), then correct
   and apply decisions from Products → Clover matching. Apply them all before the one full opening count.
   The script's `apply` remains for bulk use; both refuse colliding stock the same way.
5. Stores' opening counts and identity review (stock form/unit) proceed on the Clover products.

## Catalogue cutover (executed 2026-10-25)

`scripts/apply_master_catalogue.py --plan plan.json [--db PATH] [--dry-run]` applied the owner-reviewed
master product list in one transaction: every decided Clover match, the Downtown-only Clover items,
brand-prefixed IDs (`PM0001`, `DR0001`, `PK0006-01` for a design under `PK0006`) and the Google Sheet's
记账名 as each product's identity. Old SKUs and old 记账名 stay as search aliases. Undecided old products
were left unchanged. The run refuses as a whole if the product count or any review decision differs from
what the plan was built on. The Google Sheet sync is retired; POPCORE is the only 记账名 list.

Report matching trusts an exact 记账名: it is checked against the product's own type, not against the
long official Clover name.

Verified locally on 2026-10-08: importing the 548-item list into an empty database twice (548 created,
then 548 unchanged), and the merge tool's behaviour through `popcore_app/tests/test_clover_item_import.py`
and `test_clover_product_merge.py`.
