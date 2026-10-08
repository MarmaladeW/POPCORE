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
  need a human choice; `none` rows need a new Clover item or `keep`/`delete`.
- `scripts/merge_clover_products.py apply --csv review.csv [--dry-run]` applies each decided row in
  its own transaction: every table referencing the legacy product is re-pointed to the Clover
  product (stock, balances, sale lines, inventory documents, barcodes, report choices…), the legacy
  记账名 and name become aliases, identity fields the Clover product lacks are carried over, then
  the legacy row is deleted. A legacy product whose references would collide with the Clover
  product's own rows (for example both hold stock at one location) is refused and listed; resolve
  that stock first. `delete` works only for unreferenced products.
- The Clover checkout adapter now maps order lines by Clover item ID first, barcode second.

## Production procedure (not yet executed)

1. Take a backup (`scripts/create_backup_package.py`) and keep it until the catalogue is verified.
2. Import: `import_clover_items.py --csv clover-items-2026-10-08.csv --dry-run`, then without `--dry-run`.
3. Review: `merge_clover_products.py review --out review.csv`; the owner fills `decision` for every
   `check` and `none` row and spot-checks the confident ones.
4. Apply with `--dry-run` first, read the refusals, then apply for real. Re-run review until no
   legacy rows remain or the remainder is deliberately kept.
5. Stores' opening counts and identity review (stock form/unit) proceed on the Clover products.

Verified locally on 2026-10-08: importing the 548-item list into an empty database twice (548 created,
then 548 unchanged), and the merge tool's behaviour through `popcore_app/tests/test_clover_item_import.py`
and `test_clover_product_merge.py`.
