# 图鉴 Dex: the book in POPCORE

The owner decided on 2026-11-12 that "The Dex itself lives on POPCORE". The collector pages stay on popcore.ca and
are filled from POPCORE. The book lists companies and partners, then IPs, characters, series, products and
designs. A book product or design can point at the POPCORE item the shop stocks.

Step 1 added the book and its screens, empty; step 2 adds the one-time load and the "not in the book yet" list. Nothing is sent to the website yet, and the `products`
table and everything the till reads are unchanged. The contract with popcore.ca is in
[dex-book-contract.md](dex-book-contract.md).

## The table: `dex_entries`

Migration `dex_entries` in `popcore_app/db.py` adds this one table and its indexes, and nothing else, so older
code still runs on a migrated database.

| Column | Meaning |
|---|---|
| `id` | The contract's `ref`: permanent, never reused (AUTOINCREMENT) |
| `kind` | `company`, `partner`, `ip`, `character`, `series`, `product`, `design` |
| `parent_id`, `partner_id`, `character_id` | Other entries |
| `name`, `sort_order` | The Dex name (official English) and the order among siblings |
| `blind_box` | Products only, 0 or 1 |
| `rarity` | Designs only: `normal`, `popular`, `secret`, `super_secret`, `special` |
| `product_id` | The POPCORE item (`products.id`); one live entry per item |
| `picture_id`, `picture_url`, `thumb_url` | The website's media, shown by address (no upload yet) |
| `extras` | JSON, the refined copy (contract section 4) |
| `website_id` | The row's number on popcore.ca; unique per kind |
| `version`, `sent_version`, `send_error` | `version` rises on every change; the others are used from step 3 |
| `deleted_at` | Set on a deleted row the website still has, until the website confirms the delete |
| `created_at`, `updated_at`, `updated_by` | When, and who (Auth0 subject) |

## Rules (`popcore_app/dex_book.py`)

- Allowed parents: ip under a company; character under an IP; series under an IP or a company; product under a
  series, an IP or a company; design under a product. Companies and partners have no parent.
- A name is required. Two live entries of one kind under one parent cannot share a name, in any case (409).
- `partner_id` only on series and products, and it must be a live partner. `character_id` only on series,
  products and designs, and it must be a live character. `blind_box` only on products. `rarity` only on designs.
- `product_id`: a book product links a box or an ordinary item, never a confirmed design or a sealed set. A book
  design links only a confirmed design. One POPCORE item links to at most one live entry (409).
- A book link never holds the item back. Removing a design or deleting a product works as if the book did not
  link it (`product_id` is `ON DELETE SET NULL`, and `inventory_catalog._product_references` skips `dex_entries`).
  The entry stays in the book, unlinked. That unlink is made by the database and does not raise `version`, so
  step 3 must send `popcore_id` from the live link, never from what was last sent.
- A Clover merge (`scripts/merge_clover_products.py`, and Products → Clover matching) moves a book link to the kept
  item. When both items are linked, the merge is refused as a collision and rolled back whole; unlink one first.
- `extras` takes only the contract's keys for the kind (400 otherwise). A change merges: keys sent replace, keys
  not sent stay, `null` clears. `market_prices` is stored as given; the screen does not edit it.
- Every accepted change raises `version` by one and sets `updated_at` and `updated_by`. A change that changes
  nothing keeps the version.
- Moving (a new `parent_id`) follows the parent rule and the name rule. A moved entry goes last among its new siblings.
- Delete removes the entry and everything under it. It is refused (409) when any of them links a POPCORE item, or
  when a live entry outside them names one as partner or character. Rows without a `website_id` are deleted;
  rows with one are kept with `deleted_at` so a later step can tell the website. No read ever returns a deleted row.

## Routes (`popcore_app/blueprints/dex.py`)

Any signed-in role reads. Managers and admins write.

| Route | What |
|---|---|
| `GET /api/dex/entries?kind=company` / `?kind=partner` | The two top lists |
| `GET /api/dex/entries?parent=<id>` | Live children, by `sort_order` then name, with child count and linked POPCORE ID and name |
| `GET /api/dex/entries?q=<text>[&kinds=a,b]` | Name search, at most 50, each with its path of ancestors |
| `GET /api/dex/entries/<id>` | One entry: path, allowed child and parent kinds, linked item, partner, character, its IP |
| `GET /api/dex/entries/<id>/removal` | What a delete would remove, and why it would be refused |
| `POST /api/dex/entries` | Add (`kind`, `name`, `parent_id`, and the fields above) |
| `PATCH /api/dex/entries/<id>` | Change any of `name`, `parent_id`, `partner_id`, `character_id`, `blind_box`, `rarity`, `product_id`, `extras` |
| `DELETE /api/dex/entries/<id>` | Delete, as above |
| `POST /api/dex/reorder` | `{parent_id, ids}`: one kind's siblings in their new order (top lists: `parent_id: null`) |

## Screens

图鉴 Dex in the menu, for every signed-in role: `/dex` (companies, partners, search) and `/dex/:id` (breadcrumb,
picture, details, children by kind). Managers add, edit (drawer), move up and down, and delete with a confirm
that says what goes. A book product can open the Add Product form (新建商品 New POPCORE item) with its name, and
the new item is linked when the drawer is saved.

## The load (step 2): `popcore_app/dex_load.py`, `scripts/load_dex_book.py`

A one-time load of the website's book into `dex_entries`, re-runnable until switch-over day. It writes only
`dex_entries`. Inputs: the website's `book-export.json` (contract section 5, the website's own view: IPs,
characters, series, products, designs, no companies or partners) and the owner's checked IP list
(`ip-list-checked.csv`, 12-13 Nov 2026).

```sh
POPCORE_DB_PATH=/path/popcore.db python scripts/load_dex_book.py --export book-export.json \
    --ip-list ip-list-checked.csv --out review-dir                  # dry run: reads only, writes the review sheet
POPCORE_DB_PATH=/path/popcore.db python scripts/load_dex_book.py ... --apply --backup /path/before-dex-load.db
```

`--apply` refuses without `--backup`, or when that file already exists. It takes an online backup there first, then
applies the plan in one transaction (any refusal writes nothing), then prints the summary. The database needs its
migrations (start the app once).

What the plan does:

1. **The IP list decides.** A row is dropped only when the owner's decision starts with "delete" (this includes
   "deleted 13 Nov"). A decision starting "collaboration", or a blank or "keep" decision on a row whose kind is
   "collaboration partner", makes a partner. A decision starting "not an IP", or a blank decision on a "not an IP"
   row, makes a "not an IP" row. Anything else keeps the row as an own IP. "rename to X" or "renamed X" renames.
   List and website IPs match case-insensitively on the name before " = " in the list's IP column; rows on either
   side that do not match are listed.
2. **Companies** come from the checked companies of own IPs and "not an IP" rows (pseudo companies such as
   "(no toy maker: …)" are not companies). A company whose only IPs are dropped is stored as a deleted row, only to
   hold them. **Partners**: one per partner row, named by the checked English name; when the website has that IP
   row, the partner keeps its number (`website_id`) and picture. Two rows renamed to one name are one partner.
3. **IPs** go under their checked company (else the company most of their series name). Dropped IPs and
   everything under them are stored with `deleted_at` (never read), so step 3 can tell the website. A "not an IP"
   row is stored as deleted; its series go straight under the company their maker names.
4. **Series under a partner row** go under their home IP when it is plain (the series' character belongs to an own
   IP, or an own IP's name is a whole word of the series name), with the partner named; otherwise under the
   company their maker names, with the partner named, and flagged. A blank maker is flagged when the company had to
   come from it.
5. **Characters** go under their IP. A character whose IP is a partner, "not an IP" or none is not loaded and is
   flagged.
6. **Products and designs** keep the website's number, name, order, pictures, rarity (`regular` read as `normal`),
   blind box flag and the `extras` values. Series and IP numbers overlap in the export, so a product designs sit
   under is a product line; otherwise the parent number that exists decides. Two entries of one kind under one
   parent with the same name (doubles on the website): the one with more under it loads; the other and what is
   under it are flagged, not loaded. A product whose website number another product already uses is flagged.
7. **Pairing**: a design by its own `listing_ids` (a confirmed design `XX0000-NN`); a product by its own listing,
   then by the box its designs' IDs share, then through its series' listings (straight when the series has one
   product and one box; by name when it has several); then designs by name inside their paired box (the POPCORE
   design's `design_name`). Close names that are not equal are flagged, not paired. A link already made (by the
   load or a person) is kept, and an item linked elsewhere is never taken.
8. **Re-runs**: rows are found by kind + `website_id` (companies and partners also by name). A row the load wrote
   last (`updated_by='load'`) is updated when the export differs (version + 1); a row a person changed is left
   alone and counted. A hand-made entry of the same kind, parent and name is adopted: it gets the website number
   and pictures, fills what is blank, keeps what the person typed, and stays the person's row (later runs leave
   it alone). A second run with the same inputs changes nothing.

The review sheet (`dex-load-review-<time>.xlsx`) has tabs Summary, Loaded, Paired, Your pick (a blank column for
the owner), Not in the book (POPCORE items with no book entry, with 记账名, group, stock on hand and a suggested
IP), Deleted on the website, and Names not matched.

## Not in the book yet

`GET /api/dex/unlinked?q=&page=&per_page=` (any signed-in role; at most 100 a page): POPCORE items no live book entry
links, with `total`. Boxes, ordinary items, unsorted items and confirmed designs; never sealed sets. A design comes
with its box and the book product that box is linked to (`box_entry`).

Screen `/dex/unlinked`, reached from the Dex home with its count. Managers use 放入图鉴 Put in the book: a box or an
item becomes a book product under a series, IP or company picked by search, named by the item's name, blind box
when it is a random box, linked. A confirmed design becomes a book design under the book product its box is linked
to, named by its `design_name`; when the box is not in the book the dialog says "Put its box in first". Both use
`POST /api/dex/entries`, so every rule above holds.

## Later steps

3. POPCORE's half of the contract, behind its own switch: send changes and deletes, pictures.
4. Listings by themselves and the catch-up.

Tests: `popcore_app/tests/test_dex_book.py`, `popcore_app/tests/test_dex_load.py`,
`popcore_app/tests/browser/check_dex_book.py`, `popcore_app/tests/browser/check_dex_unlinked.py`.
