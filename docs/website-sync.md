# Website stock and price sync (popcore.ca)

Status (2026-11-10): built on branch `claude/website-sync` (2026-11-01); items link by the website's POPCORE ID field since branch `claude/website-link-by-popcore-id` (2026-11-07); regular prices since branch `claude/website-prices` (2026-11-10); Whole Sets also count the whole sets floor boxes are sure to have since branch `claude/website-whole-sets` (2026-11-10). The unit tests and the browser check pass against a fake WooCommerce. The live website and the production database are untouched. The sync stays off until an admin turns it on (below).

POPCORE owns stock (owner, 2026-10-25) and, since 2026-11-10, the regular prices of linked boxes, Whole Sets and items. popcore.ca (WooCommerce) takes its stock numbers and those prices from POPCORE, and paid web orders take their items out of POPCORE stock. Everything runs on the POPCORE side through WooCommerce's REST API; there is no website plugin. Code: `popcore_app/website_sync.py`, routes in `blueprints/website.py`, page 网站 Website (`/website`).

Owner decisions, 2026-11-01:

- **Live stores only.** The website sells what the live stores hold: DT now. MK joins by itself on its count day. Until then MK is never offered for pickup, and every web order is filled from DT.
- ~~**Stock only.** Prices and sale prices stay managed on the website.~~ Replaced on 2026-11-10, below.
- **Web orders are separate Website orders.** They take stock out but stay out of the store's sales, closing and cash/card count.
- **Keeping pieces back for the shop follows the daily report** (owner, 2026-11-02). Until a shop day's report is in, each item holds back what the shop sold of it on its best day. See [The shop's sales since its last report](#the-shops-sales-since-its-last-report).

Owner decision, 2026-11-10: the website's prices should "keep up with our clover price". Asked whether that should happen by itself, the owner said: "Yes, automatically, but it should be POPCORE automatically sending price to the website too". So POPCORE writes the **regular price** of linked boxes, Whole Sets and items. Sale prices and the prices of confirmed designs stay managed on the website: the owner sets design prices separately. The website's POPCORE → Sales screen keeps working. See [Prices](#prices).

## How items are linked

Each website product and variation carries its POPCORE ID in its own field, `_popcore_id` (owner, 2026-11-05). The website's `popcore-catalog` plugin writes it. POPCORE reads it from each item's REST `meta_data` and links the item to the POPCORE product with that ID. POPCORE keeps its own IDs in `products.sku`. The match ignores case and surrounding spaces.

| Website item | POPCORE ID | POPCORE product |
|---|---|---|
| Simple product, or the Single Box option | `PM0123` | the product (a blind box counts loose boxes) |
| Whole Set option | `PM0123-SET` | its sealed set (整端), in sets, plus the whole sets its floor boxes are sure to have ([Whole Sets](#whole-sets)) |
| A design (Vault) | `PM0123-05` | the confirmed design, in pieces |
| A colour or size twin | its own ID | that product |

**The website's SKU is never read and never written.** On popcore.ca it holds barcodes (owner, 2026-11-07). An item whose SKU happens to look like a POPCORE ID still links only by its `_popcore_id`. Until 2026-11-07 the link went by SKU, and no website item had a POPCORE ID as its SKU, so it would have matched nothing.

**A variable parent never holds an ID;** its options do. POPCORE ignores any ID found on a parent. The parent only carries the product's pickup stores.

There are four cases where an item does not link:

- **A POPCORE ID that POPCORE does not have.** The item shows **sold out**, because POPCORE holds none of it. Most of these are Whole Sets whose blind box has no `<box>-SET` sealed-set product yet: see [Sealed-set products for Whole Set options](#sealed-set-products-for-whole-set-options). Once POPCORE has the product, the next catalogue read links it. The Website page lists these items.
- **A 停用 retired product (owner, 2026-11-11).** POPCORE no longer sells it, so its item is treated as an ID POPCORE does not have from the next run on: it shows **sold out**, keeps the website's price, and is listed with those items. Un-retiring it links it again. See `docs/inventory-core.md`, Retired products.
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

A product's pickup stores (`_popcore_pickup_stores`, on the parent) become the live stores that still have it after steps 3 and 5. For a Whole Set, a store's sets of either kind count. It is empty when no store does, which leaves delivery only.

### Whole Sets

A Whole Set option (`<box>-SET`) counts two kinds of set (owner, 2026-11-10):

- **Sealed sets (整端)** at the live stores' floor, upstairs and warehouse, as above. A sealed set upstairs is certainly a whole set.
- **The whole sets the floor's single boxes are sure to have.** The floor keeps blind boxes only as single boxes, so a whole set is assumed there only from more than twice a set's worth of boxes. With N boxes per set and b loose boxes on a store's floor, the floor has **at least ceil(b / N) − 2** whole sets, never below 0. With 12 per set: 24 boxes, none for sure; 25 or 36, at least 1; 37, at least 2.
  - This is a minimum, not how many sets the floor has (owner, 2026-11-10: "25 doesnt mean 1 set, it means at least one set"). There may be more; only the sure ones count online.
  - b is the store's **floor** boxes of the blind box, after boxes kept in opened sets and after that store's hold for the shop's unreported sales. Loose boxes upstairs or in the warehouse are not part of this rule.
  - Each store is counted on its own: 25 boxes at DT and 25 at MK count at least 2 sets, not the at least 3 that 50 boxes in one place would be sure of.
  - N is the set's recorded size (its conversion, 1 set = N boxes). A sealed set with no conversion takes its blind box's boxes per set. With N unknown, the floor adds no sets.
- The website number is sealed sets + the floor's sure sets, summed over the live stores, less the held web-order units and `WEBSITE_KEEP_BACK`.

The same floor boxes still count for the Single Box, so a Whole Set and single boxes can both sell from them before the next 30-second write.

A web Whole Set order is filled from **sealed sets only**. When the store has none, the order waits for a manager (`not_enough_stock`) and holds its set off the website, as any order that cannot be filled (owner, 2026-11-10: "Ask a manager"). Nothing is taken from the floor boxes. The manager decides as for any waiting order: **Try again** once sealed sets are counted or received, or **Settled** with a reason.

The Website page shows, for each Whole Set, its sealed sets and the at-least number from floor boxes that make its number (`整端 2 sealed + at least 1 from floor boxes (12 per set)`), and its boxes per set. **What turning on changes** says how many Whole Sets count sure sets from floor boxes and how many have no boxes per set.

### Sealed-set products for Whole Set options

A Whole Set option links only to a `<box>-SET` sealed-set product. On 2026-11-10, 57 website Whole Set options belonged to blind boxes with no such product, so they showed sold out. `scripts/create_sealed_sets.py --boxes-file scripts/data/website-whole-sets-2026-11-10.txt` creates them (see `docs/inventory-core.md`, Sealed sets):

- The dry run (the default) opens the database read-only and lists the sets it would create.
  - It also lists each named ID that gets no set, and why: not a product (`not_found`); not a blind box, such as an `ordinary` item (`not_blind_box`); not verified as a blind box yet (`unverified`); no boxes per set recorded (`no_set_size`).
  - The script never retypes a product. Set those in Products: **子项 → 整端** verifies an untyped product as a blind box and adds its sealed set in one step.
- `--apply --backup <new path>` creates every listed set in one transaction, after a checked backup.
- Running it again creates nothing: a box that has its set is listed under `done`.

A new sealed set starts with no stock. Until whole sets are counted upstairs or received, its Whole Set shows only the sets the floor boxes are sure to have. Once the sets exist, the website session puts the `<box>-SET` IDs on the website options.

### The shop's sales since its last report

Until Clover is connected, a live store's sales reach POPCORE only when its daily report is pasted. Until then, the website cannot see what the shop sold today. So for each product, at each live store:

- **Best day:** the most of it the store's reports took off in one day, over the store's last 30 report days. A whole set sold counts as its boxes, as the report takes them off the floor.
- **Days not reported:** the shop days after the store's last reported business day. That is its latest daily report, or the day before its opening count when no report came since. Today counts from `WEBSITE_SHOP_OPENS`, 12:00 by default (DT opens at noon).
- **Held back:** best day × days not reported.

Once a day's report is pasted after closing, nothing is held back until the shop opens the next day, so the website is exact overnight. A late report holds back more: one day's worth for each day it is late. The Website page shows each store's last reported day and how much is held back.

On the 2026-11-01 data (395 in-stock linked items), one day not reported holds back 7 items completely. On their best recent day, none of the 395 would sell out in the shop while the website still showed it. A flat `WEBSITE_KEEP_BACK=1` hides 39 items and still leaves 4 at risk. The last piece of a slow seller can still sell twice. In that case the night's report shows a floor shortfall (日报差异) for it, and the web order needs a refund or a swap.

POPCORE writes an item only when its number, price or pickup list differs from what POPCORE last saw or wrote there. An edit made by hand on the website is corrected at the next full catalogue read: every day, and whenever an admin presses **Read website products**.

## Prices

POPCORE writes each linked item's regular price in the same push as its stock (owner, 2026-11-10):

| Website item | Regular price POPCORE writes |
|---|---|
| Simple product, or the Single Box option (`PM0123`) | the product's `price`: its Clover price |
| Whole Set option (`PM0123-SET`) | boxes per set × the single box's price. Clover never stores a set price, so the sealed set record's own `price` is never read (owner, 2026-11-10) |
| A design (`PM0123-05`), or any product the website marks Vault (`_popcore_is_vault`, or `_popcore_kind` = `confirmed_design`) | **none**: the owner prices designs on the website |

The 2026-11-09 check found POPCORE's prices to be Clover's. All 394 records with a Clover item name matched the 8 Oct Clover export to the cent, and every sealed set cost `boxes_per_dan` × its box (241 sets). Designs are different. POPCORE copies the box price into every design (1,487 of 1,820), while the website's Vault designs carry collector prices.

Boxes per set is the sealed set record's `boxes_per_dan`, else its box's. It is never taken from the set's opening conversion (`product_conversions.output_per_input`), which can disagree. DR0051 is 6 per set (owner, 2026-11-10): both records say 6, but its opening conversion said 12 on 2026-11-10, and that conversion is the wrong one.

POPCORE leaves the website's price alone for:

- an item with no POPCORE ID, an ID POPCORE does not have, or an ID on two items;
- an item whose POPCORE record is not a verified Clover item (an old record), or has no price;
- the **sale price**, always. POPCORE never writes `sale_price`.

The Website page's **价格 Prices** panel lists the changes that go by themselves and counts the items left alone. Its **Prices POPCORE wrote lately** lists the last 30 writes. Every write is kept in `website_price_writes` with the website's price before, the time, and who gave the OK, so a change can be explained or undone.

### Changes that wait for a manager's OK

A price change goes with the next write unless one of these holds:

- the website has **no price** for the item yet (or $0). A first price makes buyable an item the website could not sell;
- it is more than **30%** up or down;
- the box or item has **no Clover item name** in POPCORE, so its price was never checked against Clover. A Whole Set goes by its box's name;
- the website's Whole Set does not cost POPCORE's boxes per set times its Single Box, within 2%. The Single Box price can be the one the website shows, or the one POPCORE last replaced: if the website took the box but refused its set in the same batch, the set goes a pass later. For example, Marvel Deadpool is 9 boxes on the website and 12 in POPCORE;
- the item has a **sale price** that would stop or start discounting:
  - a sale at or above the new price would end. For example, #21007 had a $66.99 sale and Clover's $54.99. WooCommerce 10.9.4 deletes a sale price that is not below the regular price whenever either is saved (`WC_Product_Data_Store_CPT::handle_updated_props`), so the OK ends the sale;
  - a sale that does not discount today would start again. Then the Dex emails the product's followers that it is on sale. WooCommerce deletes such a sale on any price save, so only a sale saved some other way can be in this state.

Such a change waits under **价格待确认 Prices waiting for an OK**, with its reasons. The other options of the same website product wait with it, so a box and its Whole Set never part ways.

A manager presses **确认 OK** for the product. The OK covers exactly the change shown: each option's website price and sale price, and POPCORE's new price. If any of them changes before the write, the change waits again. An OK that was used once never covers a later change.

Fixing the cause also releases a change. A manager can:
- correct the price or the boxes per set in POPCORE, which counts at the next pass;
- clear the sale on the website (POPCORE → Sales → Clear), which counts at the next catalogue read.

On the 2026-11-10 data, the first write would change 147 prices. This used the owner's 10 Nov product export and POPCORE's 10 Nov backup, as a dry run of this code:
- 121 at once;
- 4 Daily Shop ones after the reset (Sonny Angel Snack and Hippers, box and set);
- 22 waiting for an OK:
  - the 17 "look first" rows of the 2026-11-09 comparison;
  - #21007's sale;
  - the first prices of two old drafts (PM0321, PM0334);
  - PM0278, box and set, whose website set ($260.91, 9 × $28.99) no longer matches its Single Box ($37.99).

### Read again just before the write

The catalogue is read once a day, so its prices, sale prices and Daily Shop marks can be a day old. Just before it writes any price, POPCORE reads those items and their products from the website again, by ID: one request for the products and one for each variable product's options. The checks above then run on what the website shows at that moment:
- a sale added since the daily read holds the change;
- a Daily Shop mark added since makes it wait for the reset;
- a website price that already equals POPCORE's means nothing is written.

If one of those items is gone, or carries another POPCORE ID than at the last read, no price is written. POPCORE rereads the whole catalogue first (the next pass), then judges again. Stock writes go on as before.

### Daily Shop products

popcore.ca's Daily Shop prices a customer's deal from the product's price at the moment it is read. So a price change during the day changes the price of a deal already in a customer's bag. popcore.ca's runbook (`docs/runbook/shop.md`, the Daily Shop invariants) asks for pooled products to be repriced right after the reset.

A product with `_popcore_daily_pool` = yes, and each of its options, is repriced only in the hour after the daily reset. The reset is `WEBSITE_DAILY_RESET`, 20:00 Toronto time by default, the Daily Shop's default `reset_time`. On 2026-11-10 popcore.ca's time zone was `America/Toronto`, so its 20:00 is POPCORE's 20:00.
- Until that hour, the change shows under Prices with "after the 20:00 reset".
- If the website cannot be reached in that hour, the change waits for the next day.
- If the Daily Shop's reset time is changed on the website, set `WEBSITE_DAILY_RESET` to match.

The pool mark is read again just before each price write (above), so a product added to the pool during the day waits too.

### What a price write does on the website

A price write saves the product through WooCommerce, so the website's own observers run:

- **Restock emails.** Followers waiting for the product are emailed when it is available. This is expected (owner, 2026-11-10).
- **Dex sale notice.** The Dex announces a sale when a save finds one it has not seen yet (`_popcore_on_sale_seen`). A sale that would start again waits for an OK. A sale that runs before and after the write is announced only if the Dex never saw it; on the 2026-11-10 data, no price that goes at once has a sale.
- **No other notices.** No Dex arrival is sent, since designs are never priced.

**Change a box's or an item's price in POPCORE (Clover), not on the website.** An edit made on the website is put back at the next full catalogue read.

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
   - optionally set `WEBSITE_SHOP_OPENS` (12:00), `WEBSITE_DAILY_RESET` (20:00), `WEBSITE_KEEP_BACK` (0) and `WEBSITE_DELIVERY_STORE` (DT);
   - restart POPCORE.
   The background job starts only when the key is set. Never print the key lines.
6. **POPCORE → 网站 Website** (admin).
   - Press **Read website products**. It takes a minute or two.
   - Read **What turning on changes**: the items that sell out, come back or are low, the pickup changes, the POPCORE IDs not in POPCORE, any ID on two items, and the items without an ID.
   - Read **价格 Prices**, the prices the first write changes, and **价格待确认 Prices waiting for an OK**. A manager can OK held prices before or after turning on.
   - Press **Turn on**. The first write follows within 30 seconds.
   - **Turn off** stops every write and order reading. The website keeps its last numbers and prices.

## Limits

- **In-store sales at DT reach POPCORE only when the day's report is pasted,** until Clover is connected. The hold above covers each item's best day, not every day. Paste each day's report after closing: a report pasted during the day stops the hold for the rest of that day. Reading Clover alone would not close the gap, because 47% of DT's units are sold off the card machine (随手记: cash, transfer, WeChat, Alipay). POPCORE's Clover checkout, where every sale is rung through POPCORE, removes it.
- **Between the order read and the stock write** of one 30-second pass, a newly paid order can be overwritten by a number POPCORE just changed for the same item. WooCommerce's own 32-minute checkout hold still protects the moment of payment.
- **A Single Box counts loose boxes only,** never boxes inside sealed sets. A Whole Set counts sealed sets and the whole sets the floor boxes are sure to have ([Whole Sets](#whole-sets)).
- **Sealed sets opened since the last report.** The daily report's 出库入店 lines open sealed sets upstairs (or in MK's warehouse) onto the floor as boxes. Until that day's report is pasted, POPCORE still counts those sealed sets, and the Whole Set still shows them. No hold covers this yet. Between 2026-11-01 and 2026-11-08, DT's reports opened 8 to 19 sealed sets a day, at most 3 of one set; MK's opened 4 to 23. The daily report records no Whole Set sold as a set (a set sold counts as its boxes, off the floor).
- **Other ways to pay.** The PayPal gateway, WooCommerce POS and the website's own Special Orders "Arrived" button change stock outside POPCORE's checkout. Keep them off while the sync is on (probe questions 5 and 6).
- **Vault products** follow their POPCORE IDs like any item. Vault items without an ID keep their placeholder quantities.
- **Upgrading from the SKU link** (migration `website_items_popcore_id`) adds the `popcore_id` column. It also empties the saved catalogue read, because the links it held were made by SKU. Press **Read website products** before the preview or turning on.
- **Upgrading to prices** (migration `website_prices`) adds the price, Daily Shop and Vault columns, the OK table `website_price_oks` and the log `website_price_writes`. It also empties the saved catalogue read, because that read holds no prices: without them POPCORE could neither tell a change nor judge it. Press **Read website products** before the preview or turning on. When the sync is on, the next pass rereads the catalogue before it writes anything.
- **A Whole Set POPCORE has no sealed set for** shows sold out (above) and keeps the website's price.
- **A batch the website takes only in part** can leave a box at its new price and its Whole Set at the old one. The rest is written in the next pass, or waits for an OK: a new OK is needed, because an OK covers only the change it was given for.
- **Partial refunds** never move stock.

## Local checks

- `tests/test_website_sync.py`: the floor-set rule (24, 25, 36, 37 boxes at 12 per set), Whole Sets counting sealed sets and the floor's sure sets store by store after opened sets and the shop hold, an unknown set size, a Whole-Set-only listing's pickup stores, a Whole Set order with only floor sets waiting for a manager and holding its set, linking by `_popcore_id` (any case, never by SKU, a parent's stray ID ignored, an ID on two items linking neither), drafts and disabled options, the upgrade from the SKU link, an order line whose item was added since the last read or deleted, the website number, keep-back, the hold until the daily report is in, opened sets, turning on, writes only on change, back stock first, closing exclusion, cancel, orders that wait and hold their units, MK pickup while MK is not live, orders from before the switch, and a refused key. It uses a fake WooCommerce and blocks every real request.
  - Prices: boxes and items get POPCORE's price, and Whole Sets boxes per set × the box, never the set record's own price. Designs, Vault products, old records and records without a price keep theirs. The sale price is never written. Only changes are written, and a website edit is put back at the next read. The log.
  - Changes held for being big, a first price, unchecked against Clover, or a set unlike its box, with the other option. A set the website refused goes once its box has.
  - The manager's OK, for exactly the change shown and used only once. Sales that would end or start.
  - The read just before a write: a new sale, a new Daily Shop mark, and swapped POPCORE IDs.
  - The Daily Shop hour, `WEBSITE_DAILY_RESET`, and the upgrade.
- `tests/browser/check_website_sync.py`: the page at 390 and 1440 px, against the real Flask app and the same fake. It covers reading products, the preview (including where a Whole Set number comes from), turning on, an order that waits and is settled, a price that goes and one a manager OKs, managers unable to turn the sync on, and staff unable to open the page.
