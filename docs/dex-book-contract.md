# The book contract: POPCORE and popcore.ca

Version 1.9.4, 16 Nov 2026. Written by the POPCORE session; the website session builds against it. Only the POPCORE
session changes it; answers change this file, not side notes. Version 1.8 is kept beside it as `contract-1.8.md`.

**1.9.4** (16 Nov): POPCORE's format work is built (pull request on branch `claude/dex-formats`, not merged): a product with no format from the list is held and not sent, with what sits under it; a script gives the book's products their formats by the section 4 rule and leaves the rest to the owner (on a copy of live: 885 of 932 settled, 47 for the owner); `aliases` and `tags` always go as lists (sections 4, 8, 13).

**1.9.3** (15 Nov): package 2 is live on popcore.ca (Dex 2.16.0 and theme 1.61.41, ~20:05 EST). `POST /book` exists; while the website's own switch is off it answers HTTP 503 `book_switch_off` and applies nothing (sections 3, 8, 10, 12). POPCORE's Dex switch stays off until switch-over day. The read, taken again after package 2, is unchanged (section 5).

**1.9.2** (15 Nov): the owner said yes to removing characters 57 Peach Riot and 71 Nommi; POPCORE removed its two rows on live (section 10, step 2). The format list is seven, not eight (slip in the change list below; section 4 was right).

**1.9.1** (correction from popcore.ca, 15 Nov): package 1 (Dex 2.15.0) added only `ref`/`version` and `GET /book`. The
IP-row label, company link and code, `full_name`, `ip_ref` on lines, price point times and the forwarding list come with
package 2 (section 12). Nothing changes for POPCORE's sender.

**What changed in 1.9** (the website's reply of 14–15 Nov, `~/Developer/popcore_plugins/out/dex-book-2026-11-14/reply-to-popcore.md`,
and POPCORE's answers of 15 Nov):
- The read route is live (popcore-dex 2.15.0) and was tested with POPCORE's key: section 5.
- Companies and partners are rows in the website's IP table with a label; their `website_id` is that row's number (section 2). Row 51 stays as the company POP MART; POPCAR 38 is the only IP row deleted (section 10).
- New refusals: `possible_duplicate` (rule 3.1), `name_taken` for characters (section 2), `format_unknown` (section 4).
- Page addresses: a rename never changes one; a move does and the old address forwards; a deleted page forwards to its parent (rule 3.5).
- `format` is sent on every product from the website's list of seven, with the type-word table (section 4). Items take only `format` and `price_history`.
- `price_history` points carry their time; on a product line (blind box) the website ignores the key for now (section 4).
- Listings: `set_id` is exactly `<popcore_id>-SET`; `completed` comes with package 3; `id_in_use` (section 7).
- Section 11 holds the answers to the twelve questions; section 12 the website's four packages; section 13 what is still open.

Changes before 1.9, shortly: 1.8 series listed by release date; 1.7 `has_children`, deletes only after every live entry was accepted; 1.6 a stocked product carries `price_history`; 1.5 a design carries `price_history`, replacing `market_prices`; 1.4 series or products straight under a partner, `full_name`, the naming standard; 1.3 `ip_ref` on products and designs of a series that spans several IPs; 1.0–1.2 the read, `item`, `parent_kind`, `parent_category`.

## 1. Who holds what

- **The owner's picture of the two systems** (14 Nov 2026): POPCORE is the core that runs everything; popcore.ca is the suit around it ("POPCORE being the core running component within popcore.ca, and popcore.ca is like iron man's hulk suit where POPCORE is like iron man"). Where this contract is silent, the fact lives in POPCORE and the website shows it.
- **POPCORE holds the book**: Company → IP → Series → Product → Design, a list of Partners, a list of Characters per IP, each entry's Dex name, order, picture choice and a refined copy of the collector facts (section 4).
- **popcore.ca holds a copy** and everything collectors and shoppers touch: the Dex pages, page addresses, collectors' marks, follows, votes and suggested edits, the picture files, and the shop listings.
- POPCORE calls the website. The website never calls POPCORE.

Owner decisions behind this (12 Nov 2026): the Dex lives in POPCORE; the pages stay on popcore.ca; picture files stay on the website and are seen and changed from POPCORE; POPCORE keeps a refined copy of the valuable collector facts; a listing's name starts from POPCORE and is then the website's; dropped IPs are deleted for good; an outside name is a partner, never an IP under a toy maker.

## 2. Entries

One shape for every level.

| kind | parent | On the website |
|---|---|---|
| `company` | none | a `wp_popcore_ips` row labelled company, carrying the old manufacturer code (`pop_mart`…) |
| `partner` | none | a `wp_popcore_ips` row labelled partner; a toy maker's series made with it names it in `series.collab_ip_id` |
| `ip` | `company` | a `wp_popcore_ips` row labelled ip, with its company |
| `character` | `ip` or `partner` | `wp_popcore_characters` |
| `series` | `ip`, `company` or `partner` | `wp_popcore_series` |
| `product` | `series`, `ip`, `company` or `partner` | `wp_popcore_product_lines`; with no series, an item figure |
| `design` | `product` | `wp_popcore_figures` with a line |

```json
{
  "ref": 1234,
  "website_id": 537,
  "kind": "series",
  "parent_ref": 88,
  "partner_ref": null,
  "character_ref": null,
  "ip_ref": null,
  "name": "Makeup Angel Series",
  "sort_order": 30,
  "picture_id": 9912,
  "blind_box": null,
  "item": false,
  "rarity": null,
  "popcore_id": null,
  "extras": {},
  "version": 17,
  "deleted": false
}
```

- `ref`: POPCORE's permanent number for the entry. Never reused, never changes.
- `website_id`: the row's number on the website when POPCORE knows it, else null. **Companies, partners and IPs share one numbering, the website's IP table** (Disney 39, Sanrio 22 are partners; POP MART 51 is a company). Line and item numbers overlap, so a product is found by `website_id` together with `item`.
- `parent_ref`, `partner_ref`, `character_ref`, `ip_ref`: other entries' `ref`. `partner_ref` on series and products. `character_ref` on series, products and designs; null clears the website's character.
- `ip_ref`: products and designs only, and only when no IP sits above the entry (its series is straight under a company, such as POP MART's anniversary sets, where each design is a different IP). Null everywhere else: the IP is then the one above. The website keeps it in `figures.ip_id` and, for a product, the new `ip_ref` column on lines.
- `name`: the Dex name, written to the owner's naming standard: IPs in capitals as their maker writes them, a series by its North American title, a product under a series by its type word alone (Figures, Vinyl Plush Pendant…), never "Blind Box".
- **Character names are unique across the whole website.** A character whose name another character already has is refused `name_taken`. The book holds 32 characters, all with different names (checked 15 Nov), and the website keeps those same 32 once the empty ones are removed (section 10).
- `full_name`: products and designs only. The name shoppers and staff see, put together by POPCORE from the entry's place in the book (HIRONO Echo Series Figures; a design adds " - " and its own name). The website stores and shows it as given and never builds its own.
- A series or product straight under a `partner` is goods that carry only that outside name (Pokémon figures by another maker, NARUTO goods): the owner groups them by that name. This is different from `partner_ref`, which marks a toy maker's own series made together with the partner.
- `picture_id`: a website media id (section 6), or null.
- `blind_box`: products only. `item`: products only; true for an item figure (no series), false for a product line. `rarity`: designs only, the website's own words (`normal`, `popular`, `secret`, `super_secret`, `special`).
- `popcore_id`: the POPCORE ID when the shop stocks it (`DR0133` on a product, `DR0133-03` on a design), else null. Information only; listings are made through section 7.
- `sort_order` is the order among siblings for every kind except series. **Series are listed by release date** (owner, 14 Nov 2026: "the dex should be listed in date, not on the date created but the date of the series was released"): newest `extras.release_date` first, the same day by name; a series with no release date comes after them, by name. This answers the website's ask for the series order rule: the website lists series by this rule everywhere a list of series is shown and needs no stored series order. A `sort_order` sent on a series carries no meaning.
- `version`: rises by one on every change in POPCORE.

## 3. Sending changes: `POST /wp-json/popcore/v1/book`

Request: `{"quiet": false, "entries": [ … up to 50 … ]}`, HTTP Basic over HTTPS with POPCORE's key (section 11, answer 1).
Answer, HTTP 200: `{"results": [{"ref": 1234, "website_id": 537, "status": "ok"}]}`, one result per entry, with `status` `ok`, `refused` or `error`, and `code` + `message` when not ok.
Any other HTTP status means nothing in the batch was applied, and POPCORE sends the whole batch again. While the website's own switch is off (section 8) the route answers HTTP 503, code `book_switch_off`, and applies nothing.

Rules for the website:

1. **Find the row** by `ref`. If none carries it, by `website_id` (then store the `ref` on it). If none, create it. Never two rows for one `ref`. **Doubles guard:** a create whose name equals (any case, spaces collapsed) an existing row of the kind under the same parent is refused `possible_duplicate`, and the message names that row's `website_id`, so a missed link never makes a second page. POPCORE checked its first send against the live read of 15 Nov: with the Makeup Angel numbers stored (section 10, step 1, done 15 Nov), no create meets an existing row.
2. **Apply only if** `version` is higher than the one stored for that row. An older or repeated send answers `ok` and changes nothing.
3. **Parents come first** in a batch. An unknown parent answers `refused`, code `parent_unknown`.
4. **POPCORE's fields** are overwritten on every accepted send: name, `full_name`, parent, partner, character, `ip_ref`, order, picture, `blind_box`, `rarity`, and the `extras` keys sent.
5. **The website's fields** are never touched by a send: the slug, image normaliser settings, category and sub-type, `wc_product_id` and every link to a listing, collectors' marks, follows, votes, wishlists and suggested edits. **Page addresses:** a rename never changes an address. A move does, because the address contains the parent (IP, company, partner, line or character); the old address then forwards (301) to the new one. A deleted page forwards to its parent.
6. **`quiet: true`** means no follower notices (`series_added`, weight recorded and the like: no bell, no email) for anything in the batch. POPCORE sets it for the first full send and for re-loads.
7. **Deletes** arrive as `"deleted": true`, children before parents. The website removes the row and what hangs on it (marks, follows, wishlist rows, weight votes and comments, events, price points, media rows, suggested edits), and unlinks listings (`_popcore_dex_figure_id`, `_popcore_figure_candidate`, `wc_product_id`, a series' `_popcore_dex_slug`) without deleting them. A row a published listing still points at answers `refused`, code `has_live_listing`. A row that still has child rows answers `refused`, code `has_children`, and nothing is removed. POPCORE deletes children first and sends deletes only after every live entry was accepted, so a child still there is one the book kept or moved. Two exceptions: a character row with no `ref` that nothing names does not count as a child (it goes with its IP), and a delete for a row the website no longer has answers `ok`.
8. After switch-over, wp-admin does not let staff edit POPCORE's fields ("Set by POPCORE"), on any row with a `ref`.

Limits: one batch at a time, the next only after the answer. The website answers within 30 seconds (50 entries take well under 5). POPCORE waits 60. The first full send is about 4,900 entries, about 100 batches.

## 4. The refined copy: `extras`

An object of named values. A key that is absent is left alone on the website. A key that is present with `null` is cleared. Unknown keys are ignored, so either side can add one later.

| kind | keys |
|---|---|
| `ip` | `description`, `aliases`, `is_original` |
| `character` | `description`, `aliases` |
| `series` | `release_date`, `flavour_text`, `description`, `aliases`, `tags`, `designer` |
| `product` (line) | `format`, `release_label`, `description`, `retail_price`, `retail_price_box`, `retail_currency`, `variants_normal`, `variants_secret`, `secret_odds`, `parent_category`, `price_history` |
| `product` (item, `item: true`) | `format`, `price_history` only; the website answers `ok` and ignores the other keys |
| `design` | `weight`, `weight_range`, `shake_level`, `shake_note`, `flavour_text`, `price_history` |

POPCORE's value is the official one. What collectors add themselves stays on the website.

- **`aliases`, `tags`**: POPCORE sends a list of texts; the website stores them as its comma strings. (The read gives the comma strings.)
- **`designer`**: a name; the website finds or makes the designer row.

**`format`** is sent on every product, always one of the website's list: `Figures`, `Plush Pendants`, `POP BEAN`, `Fragrance Blind Box`, `Blister Card`, `MEGA`, `Merchandise` (the last two added by package 2). Anything else is refused `format_unknown`. `Standalone` is a legacy value and is never sent. The website takes a listing's Type from it; POPCORE's type word (section 7) only checks the kind and gives MEGA its size.

How POPCORE decides a product's format, in this order:
1. A format a manager set on the entry in POPCORE.
2. The type word of the POPCORE item linked to the product:

| POPCORE type word | `format` | MEGA size |
|---|---|---|
| 盲盒 | from the product's name (step 3) | none |
| 盲盒（杂项） (blind boxes that are not figures) | from the product's name; goods such as magnets, charms, cups → `Merchandise` | none |
| 吊卡 | `Blister Card` | none |
| 大娃 | `MEGA` | listed for a manager |
| 大娃 400% | `MEGA` | 400 |
| 大娃 1000% | `MEGA` | 1000 |
| 周边 | `Merchandise` | none |
| 配件 | `Merchandise` | none |
| 积木 (blocks) | `Merchandise` | none |

3. The product's own name: POP BEAN → `POP BEAN`; fragrance or scented → `Fragrance Blind Box`; pendant → `Plush Pendants`; MEGA → `MEGA`; blister → `Blister Card`; figures, mini figures, dolls, HIPPERS, scene sets → `Figures`; other goods → `Merchandise`.
4. What none of these settles goes to the owner before the first send. Nothing is sent with a guess: POPCORE holds a product with no format, and what sits under it, until one is picked. A name with both a goods word and a figure word settles nothing, and neither does a name whose POPCORE item's name says something else (POP BEAN, MEGA 100%, hanging card): both go to the owner.

The website's own `format` values are not trusted as they stand: 13 lines named Plush Pendant, Vinyl Plush Pendant or Earphone Case hold `Figures` (the website's silent default). POPCORE's value replaces them.

**`price_history`** (owner, 14 Nov 2026: "the price history chart on popcore.ca should show the price change recorded in POPCORE"): every price POPCORE has recorded for the item, oldest first, as `{price, recorded_at}` with `recorded_at` the UTC date and time it was set. It is always sent whole. The website keeps each point's time (package 2 adds the column; two changes on one day then no longer clash). Its price chart shows exactly these points and nothing else. A design the shop has never priced sends an empty list. The website held 17 points of its own on item 39, all fake (owner, 15 Nov); they were deleted on popcore.ca on 15 Nov, so nothing is replaced.

A product the shop stocks carries the same key with its own recorded prices (owner, 14 Nov 2026: "Yes, every item"); POPCORE keeps these from the day that change went live (pull request 214), so an item's history begins with its price on that day. A product with no POPCORE item sends no key. On an **item figure** the chart shows it as it is. On a **product line** (a blind box, its Single Box price) the website has no place for points today, so it ignores the key until the owner asks for a Single Box chart; POPCORE keeps sending it, so nothing changes on POPCORE's side when that comes.

## 5. Reading the book: `GET /wp-json/popcore/v1/book`

**Live since 15 Nov 2026 ~03:55 EST** (popcore-dex 2.15.0). Every Dex row as an entry in the shape of section 2, paged (`page`, `per_page` up to 500, `X-WP-TotalPages`), with POPCORE's key.

**Tested 15 Nov 2026 19:26 UTC** with `scripts/fetch_dex_book.py`, run on POPCORE's server with the key the stock sync uses: 8 pages, 3,872 entries (32 IPs, 54 characters, 303 series, 422 products of which 80 items, 3,061 designs), in under 5 seconds. Compared field by field with the export file of 15 Nov 00:29 UTC: the same entries in the same order with the same fields; the one difference is item 39, whose 17 price points are gone (deleted on 15 Nov, as expected). No row carries a `ref` or `version` yet. Copy: `.local/dex-book/fetch/2026-11-15/book-live-20261115T192558Z.json`.

**Taken again 16 Nov 2026 01:12 UTC**, after package 2: the same 3,872 entries, field for field. Still no `ref` or `version`, and all 54 character rows are still there (section 10, step 2). Copy: `.local/dex-book/fetch/2026-11-16/book-live-20261116T011157Z.json`.

**Until switch-over it is the website's own view, unchanged**: kinds `ip`, `character`, `series`, `product` and `design`; no `company` or `partner` entries yet. Every row is reported, including rows POPCORE will delete.

Added per entry, beside the section 2 fields:
- `slug`, `picture_url`, `thumb_url`, `parent_website_id` and `parent_kind`.
- `character_website_id`, or `character_name` when the row holds only a name.
- On a design or an item: `ip_website_id`.
- On a series: `manufacturer` and `collab_website_id`.
- On a product: `parent_category`.
- `extras`: the section 4 keys with today's values (`aliases` and `tags` as comma strings; an item only `format` and `price_history`).
- `listing_ids`: the POPCORE IDs of listings tied to the row (a series' list includes its design drafts as well as its boxes).
- `ref` and `version` once the row has them, else null.
- No `full_name` (POPCORE builds it). Old price points had a date and no time.

POPCORE uses it for re-loads up to switch-over day and to check the copy afterwards. The export file (`~/Developer/popcore_plugins/out/dex-book-2026-11-14/book-export.json`) is no longer needed: the read gives the same.

## 6. Pictures: `POST /wp-json/popcore/v1/pictures`

Multipart: `file` (JPEG, PNG, WebP, AVIF or GIF, up to 10 MB) and `ref` (the entry it is for). Package 3.
Answer: `{"picture_id": 9912, "url": "…", "thumb_url": "…"}`. The file goes into the media library; the website makes its own sizes.
POPCORE then sends the entry with that `picture_id`. A replacement is a new upload. POPCORE never deletes a picture.
POPCORE's screens show pictures straight from `url` and `thumb_url`, so those must be public addresses.
The server must accept at least 11 MB uploads (`upload_max_filesize`, `post_max_size`); AVIF only where the server's image library reads it.
**Picture safety** (answer 9): today the only copies are the website's weekly backup (two kept, on the server) and an 11 Sep copy on the owner's Mac. Off-server storage is connected before switch-over (owner).

## 7. Listings: `POST /wp-json/popcore/v1/listings`

POPCORE states the facts of a stocked item that has no listing. The website builds the draft by its own rules. Package 3.

```json
{"items": [{
  "popcore_id": "DR0133",
  "kind": "blind_box",
  "name": "Sonny Angel x Shu Uemura Makeup Angel Series",
  "book_ref": 4410,
  "popcore_type": "盲盒",
  "set_id": "DR0133-SET",
  "boxes_per_set": 6,
  "price_cents": 2999,
  "set_price_cents": 17994
}]}
```

- `kind`: `blind_box`, `ordinary` or `confirmed_design`.
- `book_ref`: the book product (or, for a design, the book design) this item is. POPCORE asks only after that entry was accepted.
- `set_id`, `boxes_per_set`, `set_price_cents`: blind boxes only, null when POPCORE has no Whole Set. `set_id` is always exactly `<popcore_id>-SET` (all 301 Whole Sets in POPCORE are, checked 15 Nov).
- `price_cents`: the regular price. For a design, only a price a manager set in POPCORE, else null. Never the box's price.
- `popcore_type`: POPCORE's own type word (section 4 table). The listing's Type comes from the book product's `format`.

Answer, HTTP 200: `{"results": [{"popcore_id": "DR0133", "status": "created", "product_id": 8123}]}`, with `status`:
`created` (new draft), `completed` (the listing existed and a missing Single Box or Whole Set option was added), `exists` (nothing to do), `refused` or `error` with `code` + `message`.

Rules for the website:

1. **One ID, one item.** Every ID in the request is checked before anything is made. If any product or variation already carries the ID (trash ignored), answer `exists` or `completed`; an ID held by a different item answers `refused`, code `id_in_use`. Never a second one.
2. Always a **draft**. POPCORE never publishes and never changes visibility.
3. A blind box is a variable product with `Single Box` (`<ID>`) and `Whole Set` (`<ID>-SET`). The parent holds no ID.
4. A confirmed design is tied to its box by the ID prefix, as today, and to its Dex design through `book_ref` (as `_popcore_figure_candidate` until it is published, priced and in stock).
5. Series, IP, brand, categories and characters are filled by the website's own rules from where `book_ref` sits. When the catalog's lists lack the series, the draft is still `created` and listed for the owner.
6. `name` is a starting name only. POPCORE never sends a listing's name again.
7. Stock, regular price and pickup stores arrive through the existing sync after POPCORE's next catalogue read, which POPCORE asks for after a `created` or `completed`.

## 8. When, and when it fails

- POPCORE sends on its own 30-second pass, behind its own switch (图鉴同步 Dex sync), off until the owner turns it on. The website has its own switch for accepting the book (package 2, live 15 Nov), off until switch-over; while it is off, `POST /book` answers HTTP 503 `book_switch_off`. POPCORE treats that as a failed call (below): nothing is marked sent, the website's message shows as the last error, and the next pass tries again. The stock sync's switch is separate.
- **A call fails** (unreachable, timeout, 5xx, key refused): nothing is marked sent, the reason shows on POPCORE's Dex screen, and the next pass tries again.
- **A product has no format** (section 4): POPCORE does not send it, nor what sits under it, and the deletes wait; its Dex screen lists it as held until a manager picks one.
- **An entry or item is refused**: it is marked with the website's `code` and `message`, listed for a manager, and sent again when it changes or when the manager asks. It is not re-sent every 30 seconds.
- The till, stock, prices and pickup stores carry on whatever happens here.

## 9. Which side wins

| Thing | Wins |
|---|---|
| What is in the Dex, what sits under what, Dex names and full names, order, rarity, blind box or not, format, partner, company, character, which picture | POPCORE |
| The refined collector facts (section 4) | POPCORE |
| Stock, regular price, pickup stores, design prices | POPCORE, through the existing sync |
| A listing's name | starts from POPCORE, then the website |
| Everything else on a listing: text, photos, sale price, visibility, categories | website |
| Slugs and page addresses, forwarding | website |
| Collectors' marks, follows, votes, wishlists, suggested edits | website |
| Picture files | stored on the website; chosen in POPCORE |

## 10. Switch-over day, and what comes before it

Before switch-over:
1. **POPCORE stores what the website already has.** DONE on live 15 Nov 2026 19:47 UTC (owner: "fix now"; script `.local/dex-book/answers-2026-11-15/website_answers_15nov.py`, backup first): the Makeup Angel designs 3877–3881 (DR0133-01 to -05) take website numbers 3953–3957 on line 515; the 9 empty "Vinyl Figure Blind Box" lines (106, 127, 143, 157, 168, 189, 291, 293, 294) go on POPCORE's delete list; the company POP MART takes number 51 and the old IP row 51 leaves the delete list. POPCORE's first send now creates 1,075 rows, updates 3,809 and deletes 43, and among IP rows it deletes only POPCAR 38 (checked on a copy of live after the change).
2. **The website removes the empty character rows** by hand, with a backup first: the 17 (28 Instinctoy, 58 One Piece, 59 Minions, 60 Disney, 61 Barbie, 62 Beetlejuice, 63 SpongeBob, 64 Chibi Maruko Chan, 65 Digimon, 66 Fight For The Golden Spatula, 67 Jackson Wang, 68 Kimetsu no Yaiba, 69 League of Legends, 70 Marvel, 72 NARUTO, 73 MOON GELATO, 74 Chiikawa), so partners made by the first send get those page addresses. The book wants none of them. **Also 57 Peach Riot and 71 Nommi** (owner's yes, 15 Nov): nothing names them on either side, and they would hold the page addresses of the new IPs PEACH RIOT and Nommi. POPCORE removed its two rows on live 15 Nov 19:50 UTC (same script, `--drop-leftover-characters`, backup first; never sent, so no delete goes to the website); the website removes 57 and 71 with the 17. POPCORE's first send now creates 1,075, updates 3,807 and deletes 43.
3. **The website's packages 1 and 2 are live, with its own switch off.** DONE: package 1 on 15 Nov ~03:55 EST (Dex 2.15.0), package 2 on 15 Nov ~20:05 EST (Dex 2.16.0, theme 1.61.41).

On the day:
1. POPCORE re-reads the book from `GET /book`. The owner checks the list of what changed and what will be deleted.
2. The website takes a backup, turns its switch on and locks POPCORE's fields in wp-admin.
3. The owner turns on POPCORE's Dex switch. The first full send is `quiet`. It stores every `ref`, makes the other 9 companies (POP MART is row 51 already) and the 66 new partners, applies the differences and then the deletes.
   - The three character rows that are not characters, 41 POPCAR, 52 POP MART and 56 POP BEAN, become nothing: the 17 series that name 41 or 52 sit straight under the company POP MART in the book with no character, and their send clears it; items 2916 and 2921 (POP BEAN) sit straight under the company POP MART, with no IP and no character, so they are addressed `/dex/pop-mart/{item}/`. The website then removes rows 41, 52 and 56 (no `ref`, nothing names them; 41 also goes with POPCAR 38, rule 3.7).
   - POPCAR 38's series (Bumper Car, Happy Weekend, POP CAR Super Track) are under POP MART in the book; their old addresses forward.
4. The catch-up for stocked items with no listing runs from a preview list the owner approves. Sonny Angel Makeup Angel (DR0133, DR0133-SET, DR0133-01 to -05) is the test.

## 11. Answers to the twelve questions (website, 14–15 Nov)

1. **Key.** The same address, key and secret as the stock sync, HTTP Basic over HTTPS, on `/wp-json/popcore/v1/...`; each route also checks the user is `popcore-sync`. POPCORE's Dex sync already builds these addresses itself (proven by the read, section 5).
2. **Export.** Made 15 Nov from a database backup; now replaced by the live read.
3. **Designs with no line:** none. A design must arrive under a product, else `parent_unknown`. Moving a design to another line moves its page (rule 3.5).
4. **Items with no character:** addressed `/dex/{its IP, partner or company}/{item}/` from package 2.
5. **Companies and partners:** rows in the IP table with a label (section 2). The six IP rows that become partners (Disney 39, Harry Potter 45, Mousy little 42, Ne Zha 31, Sanrio 22, aespa 12) keep their numbers, pages, follows and search entries; the book holds them with those numbers. A company row carries the old manufacturer code, so series labels keep working.
6. **Collector edits:** staff type accepted edits into POPCORE (live: 0 suggested edits, 2 weight votes, 1 weight comment). From switch-over the website stops writing POPCORE's fields on rows with a `ref`.
7. **Live facts (14 Nov):** collected 0, follows 0, wishlist 0, suggested edits 0, weight votes 2, weight comments 1, Dex events 146; Dex 2.14.6 (now 2.15.0), catalog 1.3.1, Weight Guide 1.5.5, WooCommerce 10.9.1, Wordfence 8.2.2.
8. **Types:** section 4, the format list and the type-word table.
9. **Pictures:** section 6.
10. **What the website does differently:** taken into this version.
11. **A series straight under a company:** `/dex/{company or partner}/{series}/`, like any other. Row 51 becomes the company POP MART; POPCAR 38 is deleted, its series move under POP MART and forward.
12. **Price chart:** section 4.

## 12. The website's packages

Each package is one release, built on a branch, reviewed once, tested on a copy of the live database, uploaded by the owner.
1. **Numbers and the read** (live 15 Nov, 2.15.0): `ref` and `version` on the five Dex tables, and `GET /book`. Nothing else.
2. **Accepting the book** (live 15 Nov ~20:05 EST, Dex 2.16.0 and theme 1.61.41), behind the website's own switch, off: the IP-row label (ip, company, partner), the company link and code, `full_name` on lines and designs, `ip_ref` on lines, price point times and the list of forwarded addresses; then `POST /book` with every rule of section 3, companies and partners, addresses for series under a company or partner and items with no character, forwarding, and the formats MEGA and Merchandise.
3. **Prices, pictures and drafts**: the price chart from `price_history`, `POST /pictures`, `POST /listings` (with `completed`).
4. **Switch-over day**: the wp-admin locks; the catalog's IP and series lists become a copy of the book; then section 10.

POPCORE's sender can rehearse against a local copy of the website with packages 1 and 2, if the owner wants that.

## 13. Open

- **POPCORE, before package 2 is used:** every product's `format` (section 4). Built on branch `claude/dex-formats` (not merged): the format list is checked on every save, the editor asks for a format on a product, the sender holds a product with none, and `scripts/fill_dex_formats.py` gives the book's products theirs. On a copy of live (16 Nov) it settled 885 of 932 (the 13 wrong `Figures` included) and left 47 for the owner (30 with a suggestion from the item's name or the website). Then the owner's picks; nothing is sent before that.
- **POPCORE:** `aliases` and `tags` always go as lists. Built on the same branch (the one series holding a text is split on commas when sent).
- **Owner:** whether a blind box page shows its Single Box price chart (section 4); the website's suggestion is not in this job.
- **Owner:** off-server storage for pictures before switch-over (section 6).
- **Owner, from the website's list:** listings 28856 (PM0567-01 vs PM0568-07) and 8046 (PM0111, merged into PM0096).
