# 图鉴 Dex: the book in POPCORE

The owner decided on 2026-11-12 that "The Dex itself lives on POPCORE". The collector pages stay on popcore.ca and
are filled from POPCORE. The book lists companies and partners, then IPs, characters, series, products and
designs. A book product or design can point at the POPCORE item the shop stocks.

Step 1 added the book and its screens, empty; step 2 adds the one-time load and the "not in the book yet" list. Nothing is sent to the website yet, and the `products`
table and everything the till reads are unchanged. The contract with popcore.ca is in
[dex-book-contract.md](dex-book-contract.md).

## The table: `dex_entries`

Migration `dex_entries` in `popcore_app/db.py` adds this one table and its indexes, and nothing else, so older
code still runs on a migrated database. Migration `dex_entries_website_item` (step 2, contract 1.2) adds
`website_item` and replaces the unique index on `(kind, website_id)` by one on `(kind, website_item, website_id)`:
on the website a product line and an item figure can carry the same number. Older code never sets it and gets 0.
Migration `dex_entries_ip` (contract 1.3) adds the nullable `ip_id` and an index on it; older code never sets it.

| Column | Meaning |
|---|---|
| `id` | The contract's `ref`: permanent, never reused (AUTOINCREMENT) |
| `kind` | `company`, `partner`, `ip`, `character`, `series`, `product`, `design` |
| `parent_id`, `partner_id`, `character_id` | Other entries |
| `ip_id` | Products and designs with no IP above: the IP they belong to (contract 1.3, `ip_ref`) |
| `name`, `sort_order` | The Dex name (official English) and the order among siblings |
| `blind_box` | Products only, 0 or 1 |
| `rarity` | Designs only: `normal`, `popular`, `secret`, `super_secret`, `special` |
| `product_id` | The POPCORE item (`products.id`); one live entry per item |
| `picture_id`, `picture_url`, `thumb_url` | The website's media, shown by address (no upload yet) |
| `extras` | JSON, the refined copy (contract section 4) |
| `website_id` | The row's number on popcore.ca; unique per kind and `website_item` |
| `website_item` | 1 when the website holds the product as an item figure (no series), else 0 |
| `version`, `sent_version`, `send_error` | `version` rises on every change; the others are used from step 3 |
| `deleted_at` | Set on a deleted row the website still has, until the website confirms the delete |
| `created_at`, `updated_at`, `updated_by` | When, and who (Auth0 subject) |

## Rules (`popcore_app/dex_book.py`)

- Allowed parents: ip under a company; character under an IP or a partner (contract 1.2); series under an IP or a company; product under a
  series, an IP or a company; design under a product. Companies and partners have no parent.
- A name is required. Two live entries of one kind under one parent cannot share a name, in any case (409).
- `partner_id` only on series and products, and it must be a live partner. `character_id` only on series,
  products and designs, and it must be a live character. `blind_box` only on products. `rarity` only on designs.
- `ip_id` (owner, 2026-11-13: a series with several IPs, such as POP MART's 15th Anniversary set, stays straight
  under its company, and each design names its own IP, so it also shows on that IP's page): only on products and
  designs, only when no IP sits above the entry (its parents are walked), and it must be a live IP. With an IP
  above it must be empty (400 `ip_above`): its IP is the one above. It is an editable field and raises `version`.
  Only the entry being changed is checked: moving a series or product does not re-check what sits under it, so a
  design that named an IP and is now under one keeps it until its next change, which must clear it.
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
  when a live entry outside them names one as partner, character or IP (`dex_in_use`). Rows without a `website_id` are deleted;
  rows with one are kept with `deleted_at` so a later step can tell the website. No read ever returns a deleted row.

## Routes (`popcore_app/blueprints/dex.py`)

Any signed-in role reads. Managers and admins write.

| Route | What |
|---|---|
| `GET /api/dex/entries?kind=company` / `?kind=partner` | The two top lists |
| `GET /api/dex/entries?kind=ip` | Every live IP, by name, each with its path (its company), for the IP picker |
| `GET /api/dex/entries?named_ip=<id>` | The live products and designs that name this IP, products first, each with its path |
| `GET /api/dex/entries?parent=<id>` | Live children, by `sort_order` then name, with child count, linked POPCORE ID and name, and `named_ip` |
| `GET /api/dex/entries?q=<text>[&kinds=a,b]` | Name search, at most 50, each with its path of ancestors |
| `GET /api/dex/entries/<id>` | One entry: path, allowed child and parent kinds, linked item, partner, character, the IP above (`ip`), the IP it names (`named_ip`), and on an IP `named_by` (as `?named_ip=`) |
| `GET /api/dex/entries/<id>/removal` | What a delete would remove, and why it would be refused |
| `POST /api/dex/entries` | Add (`kind`, `name`, `parent_id`, and the fields above) |
| `PATCH /api/dex/entries/<id>` | Change any of `name`, `parent_id`, `partner_id`, `character_id`, `ip_id`, `blind_box`, `rarity`, `product_id`, `extras` |
| `DELETE /api/dex/entries/<id>` | Delete, as above |
| `POST /api/dex/reorder` | `{parent_id, ids}`: one kind's siblings in their new order (top lists: `parent_id: null`) |

## Screens

图鉴 Dex in the menu, for every signed-in role: `/dex` (companies, partners, search) and `/dex/:id` (breadcrumb,
picture, details, children by kind). Managers add, edit (drawer), move up and down, and delete with a confirm
that says what goes. A book product can open the Add Product form (新建商品 New POPCORE item) with its name, and
the new item is linked when the drawer is saved.

A series with several IPs (contract 1.3): the editor of a product or design with no IP above has an IP picker (all
IPs, searchable), and its character picker then offers the named IP's characters as well as the partner's. On a
page with no IP above, each product and design row shows its IP; managers set it in the row with a small picker
(one pick saves it, no drawer). An IP's page has a panel 也出现在 Also in listing the products and designs elsewhere
that name it, each with its path and a link.

## The load (step 2): `popcore_app/dex_load.py`, `scripts/load_dex_book.py`

A one-time load of the website's book into `dex_entries`, re-runnable until switch-over day. It writes only
`dex_entries`. Inputs: the website's `book-export.json` (contract section 5, the website's own view: IPs,
characters, series, products, designs, no companies or partners) and the owner's checked IP list
(`ip-list-checked.csv`, 12-13 Nov 2026).

```sh
POPCORE_DB_PATH=/path/popcore.db python scripts/load_dex_book.py --export book-export.json \
    --ip-list ip-list-checked.csv --out review-dir                  # dry run: reads only, writes the review sheet
POPCORE_DB_PATH=/path/popcore.db python scripts/load_dex_book.py ... --evidence pairing-evidence.csv   # with earlier linking work
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
3. **IPs** go under their checked company (else the company most of their series name). An own IP the list keeps
   that the website has no row for (Nommi, Peach Riot, Baby Three…) is made under its checked company with no
   website number, shown as "New to the Dex". Dropped IPs and
   everything under them are stored with `deleted_at` (never read), so step 3 can tell the website. A "not an IP"
   row is stored as deleted; its series go straight under the company their maker names.
4. **Series under a partner row** go under their home IP when it is plain (the series' character belongs to an own
   IP, or an own IP's name is a whole word of the series name), with the partner named; otherwise under the
   company their maker names, with the partner named, and flagged. A blank maker is flagged when the company had to
   come from it.
5. **Characters** go under their IP, or under the partner their IP row became, keeping their website number. A
   character whose IP is "not an IP" or none is not loaded and is flagged. The editor offers the characters of the
   entry's IP and of its partner.
6. **Products and designs** keep the website's number, name, order, pictures, rarity (`regular` read as `normal`),
   blind box flag and the `extras` values. The export's `item` (true for an item figure) and `parent_kind` say
   where a product sits, since series, IP, line and item numbers overlap; an item is found by `website_id` with
   `website_item=1`. Two entries of one kind under one
   parent with the same name (doubles on the website): the one with more under it loads; the other and what is
   under it are flagged, not loaded.
7. **Pairing**: a design by its own `listing_ids` (a confirmed design `XX0000-NN`); a product by its own listing,
   then by the box its designs' IDs share, then through its series' listings (straight when the series has one
   product and one box; by name when it has several); then designs by name inside their paired box (the POPCORE
   design's `design_name`). Close names that are not equal are flagged, not paired. A link already made (by the
   load or a person) is kept, and an item linked elsewhere is never taken.
   **Earlier linking work** (`--evidence PATH`, optional): a CSV of claims that a POPCORE ID is a given book entry,
   columns `popcore_id, entry_kind (product|design), website_id, item (true|false, products), series_slug, source,
   date, confidence (high|medium), note`, made from earlier linking work (POPCORE's 26 Oct Dex designs, the website
   catalog files). It runs after the pairing by the item's own listing IDs and before the steps through a product's
   designs, its series and names in a box, so a re-run pairs the same way. A `high` claim pairs the item, reported as
   "Earlier linking work (<source>)". Each of these goes to the Your pick tab instead, with the pairing written in
   Choices and the reason (and the claim's source and note) in "Why it needs you": a `medium` claim; a claim naming
   an entry the export lacks; sources that name several entries for one item, or several items for one entry; an
   entry whose own listing IDs name another POPCORE item of its kind; an item or entry already paired (by its IDs, a
   person or an earlier load); an item of the wrong kind (a design pairs only with a confirmed design, a product with
   a box or an item, never a sealed set). A claim a later step pairs the same way is not asked. A link a person made
   is never replaced. A POPCORE ID that no longer exists is counted and skipped. The summary's `evidence` counts
   claims paired, agreeing with a pairing already made, and asked.
8. **Re-runs**: rows are found by kind + `website_id` (companies and partners also by name). A row the load wrote
   last (`updated_by='load'`) is updated when the export differs (version + 1); a row a person changed is left
   alone and counted. An entry the export knows but with no website number (`website_id` null) is found by its
   parent and name, and is shown as "New (no website number known)". A hand-made entry of the same kind, parent and
   name is adopted: it gets the website number
   and pictures, fills what is blank, keeps what the person typed, and stays the person's row (later runs leave
   it alone). A second run with the same inputs changes nothing.

The review sheet (`dex-load-review-<time>.xlsx`) has tabs Summary, Loaded, Paired, Your pick (a blank column for
the owner), Not in the book (POPCORE items with no book entry, boxes and items first, with 记账名, group, stock on
hand, a suggested IP, the suggested book entry and how close it is; a design whose box is not in the book says
"Follows its box"), Deleted on the website, and Names not matched.

**Suggestions** (`dex_load.suggest`) never link anything themselves. For a box or an item: the book product with no
linked item whose name is closest, among the IP the item's group or name points to first, comparing names with the
IPs' own names, filler words ("series", "figure", "blind box"…) and POPCORE IDs removed (rapidfuzz token sort
ratio). For a confirmed design: the closest unlinked design under the book product its box is linked to. Below 60
out of 100 there is no suggestion. Close words are not enough: the numbers in the two names must agree (a year, 400% against 1000%, Series 2 against 3), a real word must be left to compare once the IP's name and filler words are gone, and an item that points at an IP is only ever matched inside that IP.

## Not in the book yet

`GET /api/dex/unlinked?q=&page=&per_page=` (any signed-in role; at most 100 a page): POPCORE items no live book entry
links, with `total`. Boxes, ordinary items, unsorted items and confirmed designs; never sealed sets. A design comes
with its box and the book product that box is linked to (`box_entry`). Every item carries `suggestion`
(`{id, path, score}`, or null).

Screen `/dex/unlinked`, reached from the Dex home with its count. Managers use 放入图鉴 Put in the book: a box or an
item becomes a book product under a series, IP or company picked by search, named by the item's name, blind box
when it is a random box, linked. A confirmed design becomes a book design under the book product its box is linked
to, named by its `design_name`; when the box is not in the book the dialog says "Put its box in first". An item
with a suggestion also shows it, with 连接 Link, which links the item to that entry (`PATCH /api/dex/entries/<id>`).
All of these use the step 1 routes, so every rule above holds.

## Later steps

3. POPCORE's half of the contract, behind its own switch: send changes and deletes, pictures.
4. Listings by themselves and the catch-up.

Tests: `popcore_app/tests/test_dex_book.py`, `popcore_app/tests/test_dex_load.py`,
`popcore_app/tests/browser/check_dex_book.py`, `popcore_app/tests/browser/check_dex_unlinked.py`.
