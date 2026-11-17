# 图鉴 Dex: the book in POPCORE

The owner decided on 2026-11-12 that "The Dex itself lives on POPCORE". The collector pages stay on popcore.ca and
are filled from POPCORE. The book lists companies and partners, then IPs, characters, series, products and
designs. A book product or design can point at the POPCORE item the shop stocks.

Step 1 added the book and its screens, empty; step 2 adds the one-time load and the "not in the book yet" list; step 3
sends the book to the website, behind its own switch that stays off until an admin turns it on (below). The `products`
table and everything the till reads are unchanged. The contract with popcore.ca is in
[dex-book-contract.md](dex-book-contract.md).

## The table: `dex_entries`

Migration `dex_entries` in `popcore_app/db.py` adds this one table and its indexes, and nothing else, so older
code still runs on a migrated database. Migration `dex_entries_website_item` (step 2, contract 1.2) adds
`website_item` and replaces the unique index on `(kind, website_id)` by one on `(kind, website_item, website_id)`:
on the website a product line and an item figure can carry the same number. Older code never sets it and gets 0.
Migration `dex_entries_ip` (contract 1.3) adds the nullable `ip_id` and an index on it; older code never sets it.
Migration `dex_entries_load_key` adds the nullable `load_key`: on an IP the load makes from the owner's IP list with no
website number, the list row it came from (`ip-list:<name>`), so later load runs find it whatever its name now.

| Column | Meaning |
|---|---|
| `id` | The contract's `ref`: permanent, never reused (AUTOINCREMENT) |
| `kind` | `company`, `partner`, `ip`, `character`, `series`, `product`, `design` |
| `parent_id`, `partner_id`, `character_id` | Other entries |
| `ip_id` | Products and designs with no IP above: the IP they belong to (contract 1.3, `ip_ref`) |
| `name`, `sort_order` | The Dex name (official English) and the order among siblings. Series are the exception: they are listed by release date (see Rules). IPs are the other: their number is one ranking across every company, since the website's Dex home shows the first eight IPs by it (a new IP goes last, a moved IP keeps its number, reordering a company's IPs shares out the numbers they hold) |
| `blind_box` | Products only, 0 or 1 |
| `rarity` | Designs only: `normal`, `popular`, `secret`, `super_secret`, `special` |
| `product_id` | The POPCORE item (`products.id`); one live entry per item |
| `picture_id`, `picture_url`, `thumb_url` | The website's media, shown by address (no upload yet) |
| `extras` | JSON, the refined copy (contract section 4) |
| `website_id` | The row's number on popcore.ca; unique per kind and `website_item` |
| `load_key` | On an IP the load made from the owner's IP list (no website number): the list row it came from |
| `website_item` | 1 when the website holds the product as an item figure (no series), else 0 |
| `version`, `sent_version`, `send_error` | `version` rises on every change; `sent_version` is the last version the website accepted; `send_error` the website's refusal (step 3) |
| `sync_price_id`, `sync_popcore_id` | Step 3: the newest `design_prices` row and the POPCORE ID that the current `version` carries |
| `deleted_at` | Set on a deleted row the website still has, until the website confirms the delete |
| `created_at`, `updated_at`, `updated_by` | When, and who (Auth0 subject) |

## Rules (`popcore_app/dex_book.py`)

- Allowed parents: ip under a company; character under an IP or a partner (contract 1.2); series under an IP, a
  company or a partner; product under a series, an IP, a company or a partner; design under a product. Companies and
  partners have no parent. Under a partner sit goods that carry only that outside name, grouped by it (owner,
  2026-11-14: "group it by IPs, Pokemon; NARUTO; Miffy…"): the Pokémon figures under Pokémon. A partner is deleted
  with what sits under it, like any parent, with the same refusals.
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
  design links a confirmed design; under a product that is not a blind box (`blind_box` 0), a design is a colour or
  version sold on its own, so it may also link a single item (an ordinary or unsorted item; owner, 2026-11-14, DR0089). One POPCORE item links to at most one live entry (409).
- A book link never holds the item back. Removing a design or deleting a product works as if the book did not
  link it (`product_id` is `ON DELETE SET NULL`, and `inventory_catalog._product_references` skips `dex_entries`).
  The entry stays in the book, unlinked. That unlink is made by the database and does not raise `version`, so
  step 3 must send `popcore_id` from the live link, never from what was last sent.
- A Clover merge (`scripts/merge_clover_products.py`, and Products → Clover matching) moves a book link to the kept
  item. When both items are linked, the merge is refused as a collision and rolled back whole; unlink one first.
- `extras` takes only the contract's keys for the kind (400 otherwise). A change merges: keys sent replace, keys
  not sent stay, `null` clears. `market_prices` is stored as given; the screen does not edit it.
- A product's `extras.format` is one of the website's list, `dex_book.FORMATS` (contract 1.9 section 4): Figures,
  Plush Pendants, POP BEAN, Fragrance Blind Box, Blister Card, MEGA, Merchandise. Anything else is refused (400
  `format_unknown`), the website's old `Standalone` too. A product may have none yet; the sync then holds it (step 3).
  The editor asks for one before it saves a product. See "Formats" below for how the book's products got theirs.
- Every accepted change raises `version` by one and sets `updated_at` and `updated_by`. A change that changes
  nothing keeps the version. Renaming or moving an entry also raises the version of every live entry under it (their
  full names changed), so step 3 sends them again.
- Moving (a new `parent_id`) follows the parent rule and the name rule. A moved entry goes last among its new siblings.
- Delete removes the entry and everything under it. It is refused (409) when any of them links a POPCORE item, or
  when a live entry outside them names one as partner, character or IP (`dex_in_use`). Rows without a `website_id` are deleted;
  rows the website has (a `website_id`, or sent before) are kept with `deleted_at` until the website confirms the delete
  (step 3). No read ever returns a deleted row.

## Series are listed by release date (owner, 2026-11-14)

"the dex should be listed in date, not on the date created but the date of the series was released". Wherever the book
lists what sits under an entry (`dex_book.children`), series come newest release first (`extras.release_date`), the same
day by name. A series with no release date comes after them, by name, never in the order it was made. Other kinds keep
`sort_order`. A series row shows its release date and has no move up or down; `sort_order` on a series is not used.
popcore.ca has no order column for series and sorts them itself, so the contract (1.8) gives it the same rule.

## Full names and the IP tag (owner, 2026-11-14)

The owner's naming standard (rule 7): a product under a series is stored by its type word alone (Figures, Vinyl Plush
Pendant…), and **the full name is put together, never typed or stored**: HIRONO Echo Series Figures. One function
builds it, `dex_book.full_name`, and every read returns it as `full_name` (contract 1.4):

| Entry | Full name |
|---|---|
| Series | Its IP (or the partner it sits under) and its name: HIRONO Echo Series. The IP is left out when the name already starts with it as whole words, in any case (HIRONO × Le Petit Prince Series). A series straight under a company (a set with several IPs) has none. |
| Product under a series | The series' full name and the product's own name: HIRONO Echo Series Figures; Celebrating the Moment POP MART 15th Anniversary Series Figures |
| Product with no series | Its own name, as it is (a single item under an IP, partner or company) |
| Design | Its product's full name, " - ", its own name: HIRONO Echo Series Figures - Sun |
| Company, partner, IP, character | Its own name |

A rename of an IP or series reaches every full name under it at once.

Some IPs of other makers stay in mixed case (Baby Three, Nommi, MayMei). So that it is plain which words are the IP,
every read also carries `ip`, `{id, kind, name}` or null: the IP above the entry; else the IP it names (`ip_id`, a
set with several IPs), or failing that the IP its product names; else the partner it sits under (`kind: 'partner'`).
Companies, partners and IPs themselves have none. The editor's own "IP above, or itself" is `ip_above` on a detail.

**Search** finds an entry whose own name holds at least one of the words and whose full name holds them all:
"hirono echo figures" finds HIRONO Echo Series Figures, and not its designs. Names holding the whole text come first.

## Formats (contract 1.9 section 4)

Every product goes to popcore.ca with a format from the website's list, and nothing is sent with a guess.
`scripts/fill_dex_formats.py` gives the book's products theirs once, by the contract's order: the owner's pick
(`--picks`, a JSON object of entry id to format); a format a person set in POPCORE (the entry was last changed by
someone other than the load, the owner's review scripts or this script); the type word of the linked POPCORE item
(吊卡 Blister Card; 大娃, 大娃 400%, 大娃 1000% MEGA; 周边, 配件, 积木 Merchandise; 盲盒 says nothing; 盲盒（杂项）
takes the name's answer, Merchandise when the name gives none, and goes to the owner when the name says Figures); the
product's own name (POP BEAN, MEGA or 400%/1000%, blister or hanging card, fragrance or scented, pendant, then goods
words, then figure words; a name with goods and figure words settles nothing), which stands only when the linked item's
name gives no other clear answer. What none of these settles is left with no format and listed for the owner, with the
item's answer or the website's value as a suggestion. The website's own values are not trusted as they stand (it
filled `Figures` silently); a product the rule settles gets the rule's value.

Dry run by default; `--apply --backup NEW` takes an online backup first and writes every change in one transaction,
as the actor `format-rule`. Re-running changes nothing. The report (`--out`) is a JSON file with every product (where it
sits, its item and type word, the value before, the format, why) and a Markdown table of the owner's picks. On a copy
of live (16 Nov 2026) it settled 885 of the 932 products and left 47 for the owner; the first send stayed 1,075
creates, 3,807 updates and 43 deletes.

## A design's price trend

Managers set design prices on 确认款价格 Design prices or in the design editor; POPCORE keeps every one with its date
and who set it (`design_prices`, `popcore_app/design_prices.py`, live since 13 Nov). The book reads them, never writes:
`dex_book.price_history` only SELECTs from `design_prices` (and the setter's name from `employees`). For a design
linked to a POPCORE item it returns every price set for that item, newest first (`cents`, `previous_cents`, `source`,
`set_at` UTC, `set_by`, `set_by_name`), and the item's price now (`current_cents`). It adds the website's market
prices kept in the design's `extras.market_prices` (`{price, recorded_at, source}`, price in dollars) as `website`.
`confirmed_design` says whether the linked item is a confirmed design (its prices are `design_prices`) or any other
item (its recorded changes, `product_price_history`).

The book keeps no price of its own. 改价 Change price on the panel (managers; owner, 17 Nov: "Yes, build it") saves the
item's one price where it already lives: a confirmed design's through `POST /api/design-prices` (as on Design
prices: kept in `design_prices`, sent to popcore.ca, refused with `prices_changed` when the price moved after the page
loaded), any other item's through `PATCH /api/products/<id>` (as the Products edit; the website sync judges it as
usual). Nothing in the book changes; the trend reads the new price back.

## Routes (`popcore_app/blueprints/dex.py`)

Any signed-in role reads. Managers and admins write. Every entry a read returns carries `full_name` and `ip` (above).

| Route | What |
|---|---|
| `GET /api/dex/entries?kind=company` / `?kind=partner` | The two top lists |
| `GET /api/dex/entries?kind=ip` | Every live IP, by name, each with its path (its company), for the IP picker |
| `GET /api/dex/entries?named_ip=<id>` | The live products and designs that name this IP, products first, each with its path |
| `GET /api/dex/entries?parent=<id>` | Live children, by `sort_order` then name, with child count, linked POPCORE ID and name, and `named_ip` |
| `GET /api/dex/entries?q=<text>[&kinds=a,b]` | Search by the words of the full name (above), at most 50, each with its path of ancestors |
| `GET /api/dex/entries/<id>` | One entry: path, allowed child and parent kinds, linked item, partner, character, the IP above or itself (`ip_above`), the IP it names (`named_ip`), and on an IP `named_by` (as `?named_ip=`) |
| `GET /api/dex/entries/<id>/prices` | A design's price trend (above): `{linked, confirmed_design, current_cents, points, website}`; 400 for other kinds |
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

Names (owner, 2026-11-14): a product's or design's card shows its full name as the title, with its own stored name
(the type word, or the design name) and kind beneath. Rows keep the short name, since they sit under their parent.
Search results, the "Also in" list and the "not in the book yet" suggestions show the full name. Wherever an entry
has one, a small tag shows "IP" then the IP's name (or the partner's, for goods under a partner): on the entry card,
in child rows (except where the row's own IP picker shows it), in search results and beside a suggestion. The tag
shrinks with an ellipsis and never widens a row.

价格走势 Price trend, on a design's page when it is linked to a POPCORE item (or the website gave it market prices),
for every signed-in role: the price now, a small plain SVG line (no chart library) when two or more prices were set,
with the website's prices as hollow dots on the same scale, then every price newest first with its Toronto date and
who set it; the website's are marked 网站 Website. When nothing has been set it says so in one line. A manager sees
改价 Change price in its header when the entry is linked: the price now, the new price, one line on what saving does
(the till's price, and how popcore.ca follows), then 保存 Save; a design's saved price shows what the website does
with it, as Design prices' summary does.

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
   parent and name, and is shown as "New (no website number known)". An IP new to the Dex is found first by its
   `load_key` (the IP-list row it came from), so once a person renames it, later runs leave it alone and never make it
   again. A hand-made entry of the same kind, parent and name is adopted: it gets the website number
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

## Applying review decisions: `popcore_app/dex_decisions.py`, `scripts/apply_dex_decisions.py`

The owner went through the loaded book in a guided review (13-14 Nov 2026) and decided card by card. The approved
decisions are written as one decisions file, outside the repo, and applied with this tool. It writes only
`dex_entries`, and every write goes through `dex_book`'s own `create`, `update` and `delete` as the actor
`owner-review`, so every rule above holds; it copies none of them.

```sh
POPCORE_DB_PATH=/path/popcore.db python scripts/apply_dex_decisions.py --decisions book-decisions.json --out report-dir
POPCORE_DB_PATH=/path/popcore.db python scripts/apply_dex_decisions.py ... --apply --backup /path/before-dex-decisions.db
```

A decisions file is a JSON list of operations (or `{"operations": [...]}`), run in order:

| `op` | Fields | What it does |
|---|---|---|
| `create` | `kind`, `parent`, `name`, optional `blind_box`, `rarity`, `partner`, `character`, `ip`, `extras`, `key`, `website_id`, `item` | Makes the entry; later operations refer to it as `{"key": ...}`. `website_id` and `item` give it the website's own number, for a row the website holds that the load did not load, so a later step joins the website's row instead of making it twice (`dex_book.set_website_number`, same transaction; 409 `website_number_taken` when another entry of the kind holds it) |
| `set` | `entry`, `fields` (any of `name`, `partner`, `character`, `ip`, `blind_box`, `rarity`, `extras`) | Changes those fields (`extras` merges, as in the editor) |
| `move` | `entry`, `parent` | Puts the entry under its new parent |
| `delete` | `entry` | Deletes it with everything under it (the book's delete rules) |
| `link` / `unlink` | `popcore_id`, `entry` | Links or unlinks the POPCORE item; a link never replaces another link (unlink first) |

Every operation carries `why`: the card or decision it comes from and a plain reason (`{"card": "book D-040",
"reason": "..."}`), shown in the report. An entry (and a parent, partner, character or IP) is referred to in one of three
ways: `{"kind", "website_id", "item"}` (the website's number and the item flag); `{"key"}` of an earlier `create`;
or `{"kind", "name", "parent"}` for entries with no website number (companies, partners, IPs new to the Dex), where
the name matches in any case and `parent` is itself a reference (or null at the top).

- **One transaction.** All operations run inside one `BEGIN IMMEDIATE` transaction. `dex_book.batch(con)` makes each
  of `dex_book`'s writes a savepoint inside it instead of committing, so each one keeps all its checks. A dry run
  (the default) rolls the whole transaction back, so the report shows exactly what each operation would do,
  operations later in the file seeing the effect of earlier ones.
- **Nothing half-applied.** An operation that cannot be done (an entry or item not found, a bad field, a rule that
  refuses) is reported with the reason and its own savepoint is rolled back; the run carries on, so a dry run lists
  every such operation. `--apply` commits only when no operation was refused; otherwise it writes nothing and exits 1.
- **Re-runs.** An operation whose result already holds is "already done" and changes nothing (no version change): a
  create finds its entry again by kind, parent and name (one made by `owner-review`; an entry someone else made with
  that name is refused, refer to it instead); a set whose values hold; a move already under its parent; a delete of
  a deleted entry; a link already made; an unlink of an item the entry does not link. Applying a file twice changes
  nothing the second time.
- **Backup.** `--apply` needs `--backup PATH`, a file that does not exist yet; the online backup is taken first.
- **Report.** `dex-decisions-<time>.json` and `.xlsx` in `--out`: each operation, its status (would do, done,
  already done, refused), what it did or would do, and why. The JSON summary goes to standard output.
- When a rule blocks what a decision needs, the decisions file does it with these operations in the right order:
  unlink before link; name a moved product so it does not clash with its new siblings, move it, then delete the
  emptied series.
- The load leaves alone rows a person changed, so a later load run does not undo what the decisions did: their rows
  carry `updated_by='owner-review'`.

## Not in the book yet

`GET /api/dex/unlinked?q=&page=&per_page=` (any signed-in role; at most 100 a page): POPCORE items no live book entry
links, with `total`. Boxes, ordinary items, unsorted items and confirmed designs; never sealed sets. A design comes
with its box and the book product that box is linked to (`box_entry`). Every item carries `suggestion`
(`{id, path, score}`, or null). Beside them, left as they are, `suggested_entry` and `box_entry_named` give those
entries' `{id, kind, name, full_name, ip}` (or null).

Screen `/dex/unlinked`, reached from the Dex home with its count. Managers use 放入图鉴 Put in the book: a box or an
item becomes a book product under a series, IP or company picked by search, named by the item's name, blind box
when it is a random box, linked. A confirmed design becomes a book design under the book product its box is linked
to, named by its `design_name`; when the box is not in the book the dialog says "Put its box in first". An item
with a suggestion also shows it, with 连接 Link, which links the item to that entry (`PATCH /api/dex/entries/<id>`).
All of these use the step 1 routes, so every rule above holds.

## Step 3: sending the book to popcore.ca (`popcore_app/dex_sync.py`)

The owner asked for it on 2026-11-14 ("yes build the POPCORE -> popcore.ca sync"). It is POPCORE's half of the
contract ([dex-book-contract.md](dex-book-contract.md), sections 2, 3, 4, 6 and 8). The website's half did not exist
when this was built, so it is tested against a pretend website (`tests/test_dex_sync.py`), and it does nothing until an
admin turns on its own switch. It never touches stock, prices, orders or the stock sync (`website_sync.py` is not
changed; the Dex sync only reads its `config()` for the website's address and key, and makes its own calls to
`{WEBSITE_URL}/wp-json/popcore/v1/…` with the same key).

**State.** Migration `dex_sync` adds the table `dex_sync_state(key, value)` (switch, who and when, last run, last
sent, last error, the last website check, `first_send_done`) and the two nullable columns `sync_price_id` and
`sync_popcore_id` on `dex_entries`, filled with today's values so turning on does not raise every linked entry's
version. Older code never reads them.

**What needs sending.** An entry whose `version` is above its `sent_version`; every entry starts at 0, so the first
run sends the whole book. Two changes do not raise a version by themselves, so each pass notices them first and raises
it: a new price recorded for a design's item (`design_prices`, read with SELECT only), and a new POPCORE ID on a
linked item.

**Order.** Parents before children (company, partner, IP, character, series, product, design). An entry goes only once
everything it points at (parent, partner, character, named IP) was accepted by the website, or goes earlier in the same
batch. Deletes go last, and only once every live entry was accepted: a refused entry holds them, because the website's
delete takes what still hangs under a row, so what the book moved out from under a deleted row must be on the website
first (the sync screen says the deletes are waiting). They go lowest level first, and a row another row still points at
waits, so a delete under it that the website refused (a published listing) is never taken with its parent. A deleted
row the website has (a `website_id`, or sent before) is sent with `deleted: true` and the row is removed once the
website answers `ok`; a deleted row the website never had is removed without sending.

A pass with nothing new only reads: it takes no write lock on the database the till writes to.

**Held** (contract 1.9 section 4). A live product with no format from `dex_book.FORMATS` is not sent: it is `held`,
worked out on every read and never stored, and what sits under it waits behind it (`behind`). Deletes wait too, since
every live entry must be on the website first. The sync screen lists the held products (缺形式 Held: no format) with
links to them; picking a format in the editor raises the version and the product goes with the next pass. Retry does
not apply to a held product (409 `not_refused`), and the first full send stays quiet until no product is held.

**Batches.** Up to 50 entries a call, one call at a time, as many calls as fit in about 20 seconds per pass.
`quiet: true` until the first full send has finished (a pass that finds nothing left to send), then `false`.

**What is sent** (contract section 2): `ref` (the entry's id), `website_id`, `kind`, `parent_ref`, `partner_ref`,
`character_ref`, `ip_ref` (products and designs), `name`, `sort_order`, `picture_id`, `blind_box` (products), `item`,
`rarity` (designs), `popcore_id` (the live link's POPCORE ID, products and designs), `extras`, `version`, `deleted`,
and `full_name` for products and designs. `item` is the stored `website_item` once the website has the row; for a
product new to the website it is true when it does not sit under a series (an item figure). `extras` holds only the
contract's keys for the kind. `market_prices` is never sent (contract 1.5 replaced it). `aliases` and `tags` always
go as lists (contract section 4): a value the load kept as the website's comma text is split on commas.

**Price history** (owner, 2026-11-14: "the price history chart on popcore.ca should show the price change recorded in
POPCORE"; contract 1.5). A design linked to a POPCORE item carries `extras.price_history`: every price recorded for that
item in `design_prices`, oldest first, each `{"price": 25.99, "recorded_at": "2026-11-14T07:28:00Z"}` (the row's
`set_at`, UTC). A design with no recorded price, or with no linked item, sends an empty list, so the website's chart
shows only POPCORE's prices (contract section 4).

A product linked to a POPCORE item carries `price_history` too (owner, 2026-11-14, asked whether every item's price
changes should be kept: "Yes, every item"). Its points come from `product_price_history`: one row each time
`products.price` really changes and an item's first price, written by three database triggers on `products` and by
nothing else, so every way a price is saved is covered (the Products edit, imports, scripts). Each item got a starting
point with its price on the day the table was added. A change to no price or to zero is not a point. The table has no
foreign key on purpose: `inventory_catalog._product_references` and the Clover merge script count every table that
points at `products`, and a price history must never hold an item back; its rows go with the item by a delete trigger.
The item's history is chosen by its form, as `dex_book.price_history` does: `design_prices` for a confirmed design,
`product_price_history` for any other item. A product with no linked item sends no `price_history` (the key is absent,
which leaves the website's own points alone). A new price makes the entry wait again; the status and the preview count it as waiting.

**Answers** (sections 3 and 8). Only HTTP 200 with a `results` list is read. `ok` stores the `website_id` (and
`website_item` when the row was new) and the version sent, and clears the error; an entry changed while it was being
sent stays waiting and goes again. `refused` or `error` stores the website's `code` and `message` with the version it was
for; that entry is not sent again until it changes or a manager presses Retry, and what points at it waits ("behind a
refused entry"). A child refused as `parent_unknown` only because its parent was refused in the same batch is not
marked; it waits behind the parent. A call that fails (unreachable, timeout, any other status, key refused, an answer
that cannot be read) marks nothing; the reason is kept as the last error and the next pass tries again.

**Scheduler.** A second job in `app.py`, `dex_sync`, its own scheduler beside the stock sync's, every 30 seconds,
`max_instances=1`. With the website settings missing, or the switch off, it returns at once: no call and no write. Any
failure is logged and kept as the last error; it never reaches the stock sync.

**Routes** (`blueprints/dex.py`):

| Route | Who | What |
|---|---|---|
| `GET /api/dex/sync` | manager | On or off, last run, last sent, last error, the last website check, counts (`waiting` with `deletes`, `sent`, `refused`, `behind`), `deletes_held`, and the refused entries with the website's reason |
| `GET /api/dex/sync/preview` | manager | What the next full send does: `create`, `update`, `delete`, `removed_quietly`, and the deletes by name with their place in the book |
| `GET /api/dex/sync/entries/<id>` | signed in | One entry's state: `sent`, `waiting`, `refused` (with `code`, `message`) or `behind` |
| `POST /api/dex/sync/switch` | admin | `{on, confirm_deletes}`. On first asks the website's book route for one entry; a 404 or a refused key answers 409 `website_half_missing` in plain words and the switch stays off. With deletes in the preview, `confirm_deletes` must be their count, typed by the admin (400 `confirm_deletes`) |
| `POST /api/dex/sync/retry` | manager | `{id}` or `{all: true}`: refused entries go again with the next pass |
| `POST /api/dex/entries/<id>/picture` | manager | Multipart `file`, one picture (JPEG, PNG, WebP, AVIF or GIF, 10 MB and 40 megapixels at most, the product-picture limits): passed to the website's `POST /pictures` with `ref`; the returned `picture_id`, `url` and `thumb_url` are stored on the entry and its version raised, so it is sent again with its picture. 409 `website_half_missing` while the website's half is not there |

**Screens.** 图鉴同步 Dex sync (`/dex/sync`, managers; a card on the Dex home): on or off; counts 待发送 Waiting,
已发送 Sent, 被拒 Refused, 等上级 Behind a refused entry; the last run and last error in plain words; the refused entries
with the website's reason, 重试 Retry and 全部重试 Retry all; until the first full send, what it will create, update and
delete, with the deletes listed by name and place. Admins turn it on in a dialog that types the count of deletes; while
the website's half is missing the page says so plainly. On an entry's page: its state with popcore.ca, and for managers
上传图片 Upload picture.

**Reading the website's book.** `scripts/fetch_dex_book.py --out book-export.json` pages through
`GET /wp-json/popcore/v1/book` (500 a page, with the stock sync's key from the environment) and writes the
`book-export.json` shape `scripts/load_dex_book.py` reads. It only reads; `--out` must not exist yet.

**Not in this step.** Listings (contract section 7: step 4, below); re-loads after switch-over.

## Step 4: listings on popcore.ca and the catch-up (`popcore_app/dex_sync.py`, end of the file)

POPCORE's half of contract section 7 ([dex-book-contract.md](dex-book-contract.md) 1.9.6). The website's half, `POST
/wp-json/popcore/v1/listings` in catalog 1.3.2, went live on 16 Nov 2026. Contract section 10 says the catch-up for
stocked items with no listing runs from a preview list the owner approves, with Sonny Angel Makeup Angel (DR0133,
DR0133-SET, DR0133-01 to -05) as the test. So nothing goes out by itself: a manager approves each item, or the
whole list, and nothing goes while the Dex switch is off. It is tested against the pretend website in
`tests/test_dex_sync.py` (`DexListingsTests`). `website_sync.py` is not changed: the listings read its last catalogue
read (`website_items`), its shelf stock and its `refresh_requested` state.

**The preview** (`dex_sync.listings`, read only). A POPCORE item is listed when:
- it is a blind box (`random_box`), an ordinary item or a confirmed design, with an ID (a sealed set is never an item
  of its own: it is its box's Whole Set option);
- it has stock a web order may take on a live store's shelves (`website_sync._shelf_stock`, as the stock sync counts
  it), or, for a blind box, its sealed set has;
- its ID is on no website item in the stock sync's last catalogue read (the website leaves trash out the same way), or,
  for a blind box with a Whole Set in POPCORE, the Whole Set's ID is missing (the website will then answer
  `completed`). With no catalogue read yet, nothing is listed.

Each item is `ready` (its book entry, a product for a box or an item, a design for a confirmed design, has been
accepted by the website, so `book_ref` works), `book_waiting` (in the book, not on the website yet: it can be
approved, and it waits) or `not_in_book` (no live book entry of its kind links it; it cannot be approved, and
the screen links to 不在图鉴里 Not in the book yet).

**What is sent** (`dex_sync.listing_item`): `popcore_id`, `kind` (`blind_box`, `ordinary`, `confirmed_design`),
`name`, `book_ref` (the book entry's id), `popcore_type` (`products.product_type`) and `price_cents`; a blind box adds
`set_id` = exactly `<ID>-SET` when POPCORE has that sealed set (else null), `boxes_per_set` and `set_price_cents`.
- `name` is the book entry's full name (contract section 2: the name shoppers and staff see; POPCORE's own
  `name_cn_en` when the book gives none). It is the starting name only and goes with the one call that makes the
  draft (owner, 12 Nov 2026: "a listing's name starts from POPCORE and is then the website's"): an answered item
  is never sent again.
- `price_cents` follows the stock sync's own rule, so the draft starts at the price the sync then keeps: a verified
  item's `products.price`; for a design only a price a manager set (`design_prices.confirmed`), never the box's price,
  else null. `set_price_cents` is the boxes per set (the set's, else its box's) times the box's price.

**Approving** (managers). One item, or all of them with the count the screen showed (409 `list_changed` when the list
changed meanwhile). An approval is a row in `dex_listings` (migration `dex_listings`: one row per POPCORE item, no
foreign key on purpose, like `product_price_history`, and removed with the item by a trigger). An approved or refused
item can be withdrawn; it then shows in the preview again.

**Sending.** On the Dex sync's 30-second pass, after the book, only while the switch is on: approved items whose book
entry the website has, boxes and items before designs (a design's box is made first), up to 50 a call, as many calls as
fit in about 20 seconds. An approved item whose book entry is not on the website yet waits (held for a format, refused,
or not sent yet). A pass with nothing approved reads one row and writes nothing.

**Answers** (contract sections 7 and 8). Matched by `popcore_id`, any case:
- `created`, `completed`, `exists`: kept with the website's product number and any note (a draft whose series the
  catalog's lists lack is still `created`, with a message). After a `created` or `completed` the stock sync is asked
  for its next catalogue read (`refresh_requested`), which brings the draft its stock, price and pickup stores
  (contract 7.7). An item made earlier whose ID a newer catalogue read still lacks (its draft was deleted) shows in the
  preview again.
- `refused` or `error` with `item_malformed`, `book_unknown`, `id_in_use`, `listing_not_variable`, `options_unusual` or
  `listing_failed`: kept with the code and message and listed for a manager with a plain hint. It is not sent again
  every pass: only when what POPCORE would send for it changes (a new manager price, a new book entry) or a manager
  presses Retry.
- An item the answer does not mention stays approved and goes again (the website answers `exists` if it made it).
- A call that fails marks nothing and is kept apart from the book's error (`listings_last_error`): HTTP 400
  `listings_malformed`, 503 `dex_missing`, a missing route (404), a refused key, unreachable, or an answer that cannot
  be read. The next pass tries again.

**Routes** (`blueprints/dex.py`, managers):

| Route | What |
|---|---|
| `GET /api/dex/listings` | The preview (`candidates`), the approved items with what each waits for, the refused ones, the answered ones, counts, the catalogue read's time and the last failed call |
| `POST /api/dex/listings/approve` | `{product_ids: [...]}`, or `{all: true, count}`; 409 `not_listable` for an item not on the list, `list_changed` for a stale count |
| `POST /api/dex/listings/withdraw` | `{product_id}`: an approved or refused item comes off the list |
| `POST /api/dex/listings/retry` | `{product_id}` or `{all: true}`: refused items go again with the next pass |

**Screens.** On 图鉴同步 Dex sync (`/dex/sync`): 网站上架 Listings on popcore.ca with the counts (可发送 Ready, 等图鉴
Waiting for the book send, 不在图鉴 Not in the book, 已批准 Approved, 被拒 Refused, 已完成 Done), the list to approve with
each item's options, prices and stock, 批准 Approve and 全部批准 Approve all; 已批准 Approved, not sent yet, with what each
waits for and 撤回 Withdraw; 网站拒收 Listings refused by popcore.ca with the reason, 重试 Retry, 全部重试 Retry all and
Withdraw; 已完成 Answered by popcore.ca. Browser check: `tests/browser/check_dex_listings.py`.

**Rehearsal** (16 Nov 2026, the nightly copy of live with the formats fill, `POPCORE_DB_PATH` on a scratch copy):
69 stocked items have no listing: 61 in the book (30 blind boxes, 16 items, 15 designs; 44 new listings and 25
boxes missing only their Whole Set), 8 not in the book. With the pretend website after a first full send, 59 of the 61
go (2 wait behind products held for a format), Makeup Angel's five designs are made, and 3 boxes whose ID sits on a
simple listing answer `listing_not_variable`.

Tests: `popcore_app/tests/test_dex_book.py`, `popcore_app/tests/test_dex_load.py`, `popcore_app/tests/test_dex_decisions.py`,
`popcore_app/tests/test_dex_sync.py`, `popcore_app/tests/browser/check_dex_book.py`, `popcore_app/tests/browser/check_dex_unlinked.py`,
`popcore_app/tests/browser/check_dex_sync.py`, `popcore_app/tests/browser/check_dex_listings.py`.
