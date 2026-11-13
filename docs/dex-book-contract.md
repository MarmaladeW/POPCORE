# The book contract: POPCORE and popcore.ca

Version 1, draft, 12 Nov 2026. Written by the POPCORE session for the website session to build against.
Nothing here is built on either side. Route names are proposals; the shapes and rules are the contract.
Questions for the website session are in section 10. Answers change this file, not side notes.

## 1. Who holds what

- **POPCORE holds the book**: Company → IP → Series → Product → Design, a list of Partners, a list of Characters per IP, each entry's Dex name, order, picture choice and a refined copy of the collector facts (section 4).
- **popcore.ca holds a copy** and everything collectors and shoppers touch: the Dex pages, page addresses, collectors' marks, follows, votes and suggested edits, the picture files, and the shop listings.
- POPCORE calls the website. The website never calls POPCORE.

Owner decisions behind this (12 Nov 2026): the Dex lives in POPCORE; the pages stay on popcore.ca; picture files stay on the website and are seen and changed from POPCORE; POPCORE keeps a refined copy of the valuable collector facts; a listing's name starts from POPCORE and is then the website's; dropped IPs are deleted for good; an outside name is a partner, never an IP under a toy maker.

## 2. Entries

One shape for every level.

| kind | parent | On the website today |
|---|---|---|
| `company` | none | the fixed `manufacturer` codes on series |
| `partner` | none | ordinary IP rows / `series.collab_ip_id` (unused) |
| `ip` | `company` | `wp_popcore_ips` |
| `character` | `ip` | `wp_popcore_characters` |
| `series` | `ip` or `company` | `wp_popcore_series` |
| `product` | `series`, `ip` or `company` | `wp_popcore_product_lines`; with no series, an item figure |
| `design` | `product` | `wp_popcore_figures` with a series |

```json
{
  "ref": 1234,
  "website_id": 537,
  "kind": "series",
  "parent_ref": 88,
  "partner_ref": null,
  "character_ref": null,
  "name": "Makeup Angel Series",
  "sort_order": 30,
  "picture_id": 9912,
  "blind_box": null,
  "rarity": null,
  "popcore_id": null,
  "extras": {},
  "version": 17,
  "deleted": false
}
```

- `ref`: POPCORE's permanent number for the entry. Never reused, never changes.
- `website_id`: the row's number on the website when POPCORE knows it (every loaded entry does), else null.
- `parent_ref`, `partner_ref`, `character_ref`: other entries' `ref`. `partner_ref` on series and products. `character_ref` on series, products and designs.
- `name`: the Dex name (the official English name).
- `picture_id`: a website media id (section 6), or null.
- `blind_box`: products only. `rarity`: designs only, the website's own words (`normal`, `popular`, `secret`, `super_secret`, `special`).
- `popcore_id`: the POPCORE ID when the shop stocks it (`DR0133` on a product, `DR0133-03` on a design), else null. It is information for the website; the listing itself is made through section 7.
- `version`: rises by one on every change in POPCORE.

## 3. Sending changes: `POST /wp-json/popcore/v1/book`

Request: `{"quiet": false, "entries": [ … up to 50 … ]}`.
Answer, HTTP 200: `{"results": [{"ref": 1234, "website_id": 537, "status": "ok"}]}`, one result per entry, with `status` `ok`, `refused` or `error`, and `code` + `message` when not ok.
Any other HTTP status means nothing in the batch was applied, and POPCORE sends the whole batch again.

Rules for the website:

1. **Find the row** by `ref`. If none carries it, by `website_id` (then store the `ref` on it). If none, create it. Never two rows for one `ref`.
2. **Apply only if** `version` is higher than the one stored for that row. An older or repeated send answers `ok` and changes nothing.
3. **Parents come first** in a batch. An unknown parent answers `refused`, code `parent_unknown`.
4. **POPCORE's fields** are overwritten on every accepted send: name, parent, partner, character, order, picture, `blind_box`, `rarity`, and the `extras` keys sent.
5. **The website's fields** are never touched by a send: the slug and page address (made once; a rename does not change it), image normaliser settings, category and sub-type, `wc_product_id` and every link to a listing, collectors' marks, follows, votes, wishlists and suggested edits.
6. **`quiet: true`** means no follower notices (`series_added` and the like) for anything in the batch. POPCORE sets it for the first full send and for re-loads.
7. **Deletes** arrive as `"deleted": true`, children before parents. The website removes the row and what hangs on it (marks, follows, wishlist rows, weight votes and comments, events, price points, media rows), and unlinks listings without deleting them. A row a published listing still points at answers `refused`, code `has_live_listing`. What a removed page address does is the website's choice (a redirect to the parent is suggested).
8. After switch-over, wp-admin must not let staff edit POPCORE's fields ("Set by POPCORE").

Limits: one batch at a time, the next only after the answer. The website answers within 30 seconds. POPCORE waits 60.

## 4. The refined copy: `extras`

An object of named values. A key that is absent is left alone on the website. A key that is present with `null` is cleared. Unknown keys are ignored, so either side can add one later.

| kind | keys |
|---|---|
| `ip` | `description`, `aliases`, `is_original` |
| `character` | `description`, `aliases` |
| `series` | `release_date`, `flavour_text`, `description`, `aliases`, `tags`, `designer` |
| `product` | `format`, `release_label`, `description`, `retail_price`, `retail_price_box`, `retail_currency`, `variants_normal`, `variants_secret`, `secret_odds` |
| `design` | `weight`, `weight_range`, `shake_level`, `shake_note`, `flavour_text`, `market_prices` (list of `{price, recorded_at, source}`) |

POPCORE's value is the official one. What collectors add themselves stays on the website.

## 5. Reading the book: `GET /wp-json/popcore/v1/book`

Returns every entry in the shape of section 2, paged (`page`, `per_page` up to 500), with these added per entry:
`slug`, `picture_url`, `thumb_url`, `parent_website_id`, and `listing_ids`: the POPCORE IDs of listings tied to the row (through `_popcore_dex_figure_id`, `_popcore_figure_candidate`, `figures.wc_product_id` for a design; through `_popcore_dex_slug` for a series).

POPCORE uses it for the one-time load, for re-loads up to switch-over day, and to check the copy afterwards.

**Needed before the website's release:** the same thing as one file (`book-export.json`), made from a fresh copy of the live database. The first load is built and reviewed from that file.

## 6. Pictures: `POST /wp-json/popcore/v1/pictures`

Multipart: `file` (JPEG, PNG, WebP, AVIF or GIF, up to 10 MB) and `ref` (the entry it is for).
Answer: `{"picture_id": 9912, "url": "…", "thumb_url": "…"}`. The file goes into the media library; the website makes its own sizes.
POPCORE then sends the entry with that `picture_id`. A replacement is a new upload. POPCORE never deletes a picture.
POPCORE's screens show pictures straight from `url` and `thumb_url`, so those must be public addresses.

## 7. Listings: `POST /wp-json/popcore/v1/listings`

POPCORE states the facts of a stocked item that has no listing. The website builds the draft by its own rules.

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
- `book_ref`: the book product (or, for a design, the book design) this item is. POPCORE asks only after that entry was accepted, so the website knows the row.
- `set_id`, `boxes_per_set`, `set_price_cents`: blind boxes only, null when POPCORE has no Whole Set.
- `price_cents`: the regular price. For a design, only a price a manager set in POPCORE, else null. Never the box's price.
- `popcore_type`: POPCORE's own type word, for the website to map to its types.

Answer, HTTP 200: `{"results": [{"popcore_id": "DR0133", "status": "created", "product_id": 8123}]}`, with `status`:
`created` (new draft), `completed` (the listing existed and a missing Single Box or Whole Set option was added), `exists` (nothing to do), `refused` or `error` with `code` + `message`.

Rules for the website:

1. **One ID, one item.** If any product or variation already carries the ID (trash ignored), answer `exists` or `completed`. Never a second one.
2. Always a **draft**. POPCORE never publishes and never changes visibility.
3. A blind box is a variable product with `Single Box` (`<ID>`) and `Whole Set` (`<ID>-SET`). The parent holds no ID.
4. A confirmed design is tied to its box by the ID prefix, as today, and to its Dex design through `book_ref` (as `_popcore_figure_candidate` until it is published, priced and in stock).
5. Series, IP, brand, categories and characters are filled by the website's own rules from where `book_ref` sits.
6. `name` is a starting name only. POPCORE never sends a listing's name again.
7. Stock, regular price and pickup stores arrive through the existing sync after POPCORE's next catalogue read, which POPCORE asks for after a `created` or `completed`.

## 8. When, and when it fails

- POPCORE sends on its existing 30-second pass, behind a switch of its own (图鉴同步 Dex sync) that is off until the owner turns it on. The stock sync's switch is separate.
- **A call fails** (unreachable, timeout, 5xx, key refused): nothing is marked sent, the reason shows on POPCORE's Dex screen, and the next pass tries again.
- **An entry or item is refused**: it is marked with the website's `code` and `message`, listed for a manager, and sent again when it changes or when the manager asks. It is not re-sent every 30 seconds.
- The till, stock, prices and pickup stores carry on whatever happens here.

## 9. Which side wins

| Thing | Wins |
|---|---|
| What is in the Dex, what sits under what, Dex names, order, rarity, blind box or not, partner, company, character, which picture | POPCORE |
| The refined collector facts (section 4) | POPCORE |
| Stock, regular price, pickup stores, design prices | POPCORE, through the existing sync |
| A listing's name | starts from POPCORE, then the website |
| Everything else on a listing: text, photos, sale price, visibility, categories | website |
| Slugs and page addresses | website |
| Collectors' marks, follows, votes, wishlists, suggested edits | website |
| Picture files | stored on the website; chosen in POPCORE |

## 10. Switch-over day

1. The website's release with these routes is live, doing nothing yet.
2. POPCORE re-runs the load from `GET /book`. The owner checks the list of what changed and what will be deleted (the 17 dropped IPs and everything under them).
3. The website takes a backup and locks POPCORE's fields in wp-admin.
4. The owner turns on POPCORE's Dex switch. The first full send is `quiet`. It stores every `ref`, applies the differences and the deletes.
5. The catch-up for stocked items with no listing runs from a preview list the owner approves. Sonny Angel Makeup Angel (DR0133, DR0133-SET, DR0133-01 to -05) is the test.

## 11. Questions for the website session

1. **Key.** Can the new routes accept the WooCommerce key POPCORE already uses (user `popcore-sync`)? If not, which credential?
2. **The export file** (section 5): can it be made now, before the release? When?
3. **Designs with no product line**: how many are there, and how should they appear in the read so every design has a product parent without changing its page address?
4. **Items with no series**: POPCORE gives the IP and, when known, the character. Where does an item with no character go?
5. **Companies and partners**: companies replace the fixed manufacturer codes, and partners leave the IP list. Any objection, and how will the 35 partner names that are IP rows today be moved?
6. **Collector edits after switch-over**: POPCORE's value wins for the keys in section 4, so the website should stop writing them itself. How should an edit staff accept on the website reach POPCORE? The simple start: staff type it into POPCORE. Say if the volume makes that unworkable.
7. **Live facts**: how many collected marks, follows, wishlist rows and suggested edits exist today, and which catalog and Dex plugin versions are live?
8. **Types**: the table from POPCORE's type words (盲盒, 吊卡, 大娃, 大娃 400%, 大娃 1000%, 周边, 配件) to the website's types and sizes.
9. **Picture safety**: the owner says the pictures are hard to get. Are the originals in the website's backups, and where is a second copy kept?
10. Anything in this contract that the website cannot do, or would do differently.
