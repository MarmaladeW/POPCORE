# 图鉴 Dex: the book in POPCORE

The owner decided on 2026-11-12 that "The Dex itself lives on POPCORE". The collector pages stay on popcore.ca and
are filled from POPCORE. The book lists companies and partners, then IPs, characters, series, products and
designs. A book product or design can point at the POPCORE item the shop stocks.

Step 1 (this page) adds the book and its screens, empty. Nothing is sent to the website yet, and the `products`
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

## Later steps

2. The one-time load from the website's book export, re-runnable, with a review sheet. It keeps the website's
   numbers and pictures.
3. POPCORE's half of the contract, behind its own switch: send changes and deletes, pictures.
4. Listings by themselves and the catch-up.

Tests: `popcore_app/tests/test_dex_book.py`, `popcore_app/tests/browser/check_dex_book.py`.
