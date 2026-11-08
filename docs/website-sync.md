# Website stock sync (popcore.ca)

Status (2026-11-07): built on branch `claude/website-sync` (2026-11-01); items link by the website's POPCORE ID field since branch `claude/website-link-by-popcore-id` (2026-11-07). The unit tests and the browser check pass against a fake WooCommerce. The live website and the production database are untouched. The sync stays off until an admin turns it on (below).

POPCORE owns stock (owner, 2026-10-25). popcore.ca (WooCommerce) takes its stock numbers from POPCORE, and paid web orders take their items out of POPCORE stock. Everything runs on the POPCORE side through WooCommerce's REST API; there is no website plugin. Code: `popcore_app/website_sync.py`, routes in `blueprints/website.py`, page 网站 Website (`/website`).

Owner decisions, 2026-11-01:

- **Live stores only.** The website sells what the live stores hold: DT now. MK joins by itself on its count day. Until then MK is never offered for pickup, and every web order is filled from DT.
- **Stock only.** Prices and sale prices stay managed on the website. The website's POPCORE → Sales screen keeps working.
- **Web orders are separate Website orders.** They take stock out but stay out of the store's sales, closing and cash/card count.
- **Keeping pieces back for the shop follows the daily report** (owner, 2026-11-02). Until a shop day's report is in, each item holds back what the shop sold of it on its best day. See [The shop's sales since its last report](#the-shops-sales-since-its-last-report).

## How items are linked

Each website product and variation carries its POPCORE ID in its own field, `_popcore_id` (owner, 2026-11-05). The website's `popcore-catalog` plugin writes it. POPCORE reads it from each item's REST `meta_data` and links the item to the POPCORE product with that ID. POPCORE keeps its own IDs in `products.sku`. The match ignores case and surrounding spaces.

| Website item | POPCORE ID | POPCORE product |
|---|---|---|
| Simple product, or the Single Box option | `PM0123` | the product (a blind box counts loose boxes) |
| Whole Set option | `PM0123-SET` | its sealed set (整端), in sets |
| A design (Vault) | `PM0123-05` | the confirmed design, in pieces |
| A colour or size twin | its own ID | that product |

**The website's SKU is never read and never written.** On popcore.ca it holds barcodes (owner, 2026-11-07). An item whose SKU happens to look like a POPCORE ID still links only by its `_popcore_id`. Until 2026-11-07 the link went by SKU, and no website item had a POPCORE ID as its SKU, so it would have matched nothing.

**A variable parent never holds an ID;** its options do. POPCORE ignores any ID found on a parent. The parent only carries the product's pickup stores.

There are four cases where an item does not link:

- **A POPCORE ID that POPCORE does not have.** The item shows **sold out**, because POPCORE holds none of it. Most of these are Whole Sets nobody counted as sets: the floor keeps blind boxes as boxes, and a sealed set exists only once whole sets are counted upstairs. Once POPCORE has the product, the next catalogue read links it. The Website page lists these items.
- **The same POPCORE ID on two or more website items.** One ID is on at most one item (owner, 2026-11-05). If two items carry the same ID, both would show the same stock and could sell it twice, so POPCORE links neither. Both show **sold out** and are listed as "Another website item carries this POPCORE ID too" until the website is fixed.
- **No POPCORE ID.** The item is left as the website has it. The Website page lists these items with their SKU (a barcode), to help find them.
- **A variable parent,** as above.

### Drafts and disabled options

The catalogue read asks WooCommerce for `status=any`. WooCommerce 10.9.1 (the version on the review copy) runs that as a WordPress `any` query, which leaves out only trash and auto-drafts. So the read includes **draft, pending, scheduled and private products**, such as the Clover drafts (`_popcore_source`). The variations list uses the same default, so it includes **disabled (private) options** and options of draft parents. Each of these that carries a POPCORE ID is linked and written like a published item.

This is deliberate. The catalogue is reread only once a day, or when an admin presses **Read website products**. If POPCORE skipped drafts, an item published on the website would keep its old number until the next read. Writing to drafts keeps their number right from the moment they are published. Customers cannot see or buy a draft or a disabled option. The website's own observers act only on published products: restock emails, the Dex arrival and `popcore-catalog`'s publish step. One exception remains. Those checks look at an option's own status, and an option of a draft parent stays `publish`. WooCommerce also lets the Shop Manager POPCORE writes as buy unpublished items. So a write that brings such an option back in stock can email its followers. That matters only for a product that had followers and was then set back to draft; a new Clover draft has none.

Trashed products are not read, so the POPCORE ID on a trashed item is ignored.

### WooCommerce's meta_data

WooCommerce 10.9.1 returns custom meta in `meta_data` for products (`WC_REST_Products_V2_Controller::api_get_meta_data`) and for variations (`WC_REST_Product_Variations_Controller`). It includes keys that start with an underscore. It leaves out only its own internal keys (`_sku`, `_stock`, `_price`, …), keys starting with `wp_`, and, for variations, `attribute_*` (`WC_Data_Store_WP::exclude_internal_meta_keys`). `_popcore_id` and `_popcore_pickup_stores` come back as written. The website must not add a `woocommerce_data_store_wp_post_read_meta` filter or an `exclude_meta` default that drops them.

## The website's number

For each linked item, the website number is built like this:

1. Take the saleable stock at the live stores' **floor, upstairs and warehouse**. Display pieces and the claw machine never count.
2. For a blind box, leave out boxes kept in an opened set.
3. Take off what the shop may have sold since its last daily report (below), store by store.
4. Take off the units of paid web orders POPCORE could not record yet (below). The website already took those off its own number, and POPCORE must not put them back.
5. Take off `WEBSITE_KEEP_BACK`, an extra margin on every item (0 by default).
6. Never go below 0.

The sync sets `manage_stock` on every linked item and writes this number. An item with a POPCORE ID POPCORE does not have, or with an ID another item also carries, gets 0.

A product's pickup stores (`_popcore_pickup_stores`, on the parent) become the live stores that still have it after steps 3 and 5. It is empty when no store does, which leaves delivery only.

### The shop's sales since its last report

Until Clover is connected, a live store's sales reach POPCORE only when its daily report is pasted. Until then, the website cannot see what the shop sold today. So for each product, at each live store:

- **Best day:** the most of it the store's reports took off in one day, over the store's last 30 report days. A whole set sold counts as its boxes, as the report takes them off the floor.
- **Days not reported:** the shop days after the store's last reported business day. That is its latest daily report, or the day before its opening count when no report came since. Today counts from `WEBSITE_SHOP_OPENS`, 12:00 by default (DT opens at noon).
- **Held back:** best day × days not reported.

Once a day's report is pasted after closing, nothing is held back until the shop opens the next day, so the website is exact overnight. A late report holds back more: one day's worth for each day it is late. The Website page shows each store's last reported day and how much is held back.

On the 2026-11-01 data (395 in-stock linked items), one day not reported holds back 7 items completely. On their best recent day, none of the 395 would sell out in the shop while the website still showed it. A flat `WEBSITE_KEEP_BACK=1` hides 39 items and still leaves 4 at risk. The last piece of a slow seller can still sell twice. In that case the night's report shows a floor shortfall (日报差异) for it, and the web order needs a refund or a swap.

POPCORE writes an item only when its number or pickup list differs from what POPCORE last saw or wrote there. An edit made by hand on the website is corrected at the next full catalogue read: every day, and whenever an admin presses **Read website products**.

## Web orders

The sync reads orders changed since its last read, with two minutes of overlap, using the `_gmt` dates. A paid order is one with `date_paid`. Its status must not be pending, failed or checkout-draft.

- **Paid.** POPCORE keeps a Website order. It holds the order number, status, paid time, totals, payment title, Stripe transaction id, delivery or pickup, pickup store, and lines (POPCORE ID, name, quantity, line total, website product and variation). It never keeps names, emails, phones, addresses or notes.
  - Each line is linked by the POPCORE ID of the item it sold, as the last catalogue read found it. The line's SKU is a barcode and is not read. If that read found no ID on the item, POPCORE reads the ID from that one product or variation on the website. This covers an item added, or given its ID, since the last read. An item deleted from the website leaves its line unlinked.
  - Its items are taken out of the filling store in one `consume`, by the actor `popcore:website` (named "Website"), with the reason `网站订单 Website order #<n>`.
  - The filling store is the pickup store when it is live, otherwise `WEBSITE_DELIVERY_STORE` (DT).
  - Units are picked **upstairs and warehouse first, then the floor**, so the floor stays as the shop's daily report expects. Staff should pick web orders from upstairs.
  - Orders paid before the sync was turned on are ignored, because the opening count already holds them.
- **Cancelled after it was recorded.** The items go back exactly where they came from, as a `correction` of the order's consume (`网站订单取消 Website order cancelled`). WooCommerce restores its own number too.
  - If POPCORE cannot put them back, for example because it no longer has access, the order waits as *cancelled, not yet back in stock*.
- **Refunded.** The order is marked refunded, but no stock moves. WooCommerce does not restock a refund either, unless staff tick "Restock refunded items". A returned piece is received in POPCORE like any return.
- **Cannot be filled.** The order waits on the Website page with its reason, and its units stay off the website number. The reasons are:
  - an item with no POPCORE ID on the website, or with an ID POPCORE does not have (`unknown_item`);
  - the store does not have enough (`not_enough_stock`);
  - no live store can fill it (`no_live_store`);
  - the website has no stock access at the store (`no_store_access`);
  - the order was paid again after a cancel (`reopened`).

  A manager then either presses **Try again** (after counting, receiving, or setting the item's POPCORE ID on the website), or **Settled** with a reason. Try again reads a missing ID from the item on the website straight away. Settled takes nothing out of stock.

Web orders take stock but are not POPCORE sales. Closing leaves their documents out of the store day's source token, so a web order paid while the manager reviews closing never makes it stale. Store reports and closing totals do not include them.

The website's stock access is an ordinary **Users → Store operations access** grant for `popcore:website`. It is given once for each store when that store is first seen live. An admin can revoke it, and it is never granted again behind their back.

## Turning it on

This order matters: the first write reaches real customers and staff email.

1. **WordPress must see HTTPS** (website session).
   - The 2026-10-25 probe found that WooCommerce ignores a correct key, because WordPress believes requests come over plain HTTP (Cloudflare Flexible SSL).
   - Fix it with Cloudflare SSL/TLS mode **Full (strict)**, or the host's equivalent.
   - Then Tools → Site Health must say "Your site is using HTTPS".
   - Until then, POPCORE shows "The website refused POPCORE's key".
2. **A sync user and key** (website admin).
   - Users → Add New: `popcore-sync`, role **Shop Manager**, an owner-controlled email, and a long random password nobody uses.
   - WooCommerce → Settings → Advanced → REST API → Add key for that user, **Read/Write**.
   - Revoke keys that are no longer used ("Popmart Upload", maybe "Stallion Express").
3. **Stock emails** (website admin). The first write sets every linked item's stock.
   - WooCommerce → Settings → Products → Inventory: turn off or repoint the low-stock and out-of-stock emails. They go to a workshown.com address today; the 2026-11-01 dry run would send about 80 low-stock and 180 out-of-stock emails.
   - Restock and Dex followers are emailed when an item comes back in stock. That is about 10 items in the dry run.
4. **POPCORE IDs on the website** (website session). Each product and option POPCORE should keep in step carries its ID in `_popcore_id`, written by the `popcore-catalog` plugin. There is no SKU import: SKUs stay barcodes, and POPCORE never changes them.
5. **Server settings** (POPCORE admin, on the droplet). In `/root/POPCORE/popcore_app/.env`:
   - set `WEBSITE_URL=https://popcore.ca`, `WEBSITE_KEY` and `WEBSITE_SECRET`;
   - optionally set `WEBSITE_SHOP_OPENS` (12:00), `WEBSITE_KEEP_BACK` (0) and `WEBSITE_DELIVERY_STORE` (DT);
   - restart POPCORE.
   The background job starts only when the key is set. Never print the key lines.
6. **POPCORE → 网站 Website** (admin).
   - Press **Read website products**. It takes a minute or two.
   - Read **What turning on changes**: the items that sell out, come back or are low, the pickup changes, the POPCORE IDs not in POPCORE, any ID on two items, and the items without an ID.
   - Press **Turn on**. The first write follows within 30 seconds.
   - **Turn off** stops every write and order reading. The website keeps its last numbers.

## Limits

- **In-store sales at DT reach POPCORE only when the day's report is pasted,** until Clover is connected. The hold above covers each item's best day, not every day. Paste each day's report after closing: a report pasted during the day stops the hold for the rest of that day. Reading Clover alone would not close the gap, because 47% of DT's units are sold off the card machine (随手记: cash, transfer, WeChat, Alipay). POPCORE's Clover checkout, where every sale is rung through POPCORE, removes it.
- **Between the order read and the stock write** of one 30-second pass, a newly paid order can be overwritten by a number POPCORE just changed for the same item. WooCommerce's own 32-minute checkout hold still protects the moment of payment.
- **Only stock counted as sets sells as a Whole Set.** A Single Box counts loose boxes only, never boxes inside sealed sets.
- **Other ways to pay.** The PayPal gateway, WooCommerce POS and the website's own Special Orders "Arrived" button change stock outside POPCORE's checkout. Keep them off while the sync is on (probe questions 5 and 6).
- **Vault products** follow their POPCORE IDs like any item. Vault items without an ID keep their placeholder quantities.
- **Upgrading from the SKU link** (migration `website_items_popcore_id`) adds the `popcore_id` column. It also empties the saved catalogue read, because the links it held were made by SKU. Press **Read website products** before the preview or turning on.
- **Partial refunds** never move stock.

## Local checks

- `tests/test_website_sync.py`: linking by `_popcore_id` (any case, never by SKU, a parent's stray ID ignored, an ID on two items linking neither), drafts and disabled options, the upgrade from the SKU link, an order line whose item was added since the last read or deleted, the website number, keep-back, the hold until the daily report is in, opened sets, turning on, writes only on change, back stock first, closing exclusion, cancel, orders that wait and hold their units, MK pickup while MK is not live, orders from before the switch, and a refused key. It uses a fake WooCommerce and blocks every real request.
- `tests/browser/check_website_sync.py`: the page at 390 and 1440 px, against the real Flask app and the same fake. It covers reading products, the preview, turning on, an order that waits and is settled, managers unable to turn the sync on, and staff unable to open the page.
