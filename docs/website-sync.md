# Website stock sync (popcore.ca)

Status (2026-11-01): built on branch `claude/website-sync`. The unit tests and the browser check pass against a fake WooCommerce. A dry run used popcore.ca's live public catalogue as if the SKU import had been run, priced against the 2026-11-01 nightly backup; nothing was sent to the website. The live website and the production database are untouched. The sync stays off until an admin turns it on (below).

POPCORE owns stock (owner, 2026-10-25). popcore.ca (WooCommerce) takes its stock numbers from POPCORE, and paid web orders take their items out of POPCORE stock. Everything runs on the POPCORE side through WooCommerce's REST API; there is no website plugin. Code: `popcore_app/website_sync.py`, routes in `blueprints/website.py`, page 网站 Website (`/website`).

Owner decisions, 2026-11-01:

- **Live stores only.** The website sells what the live stores hold: DT now. MK joins by itself on its count day. Until then MK is never offered for pickup, and every web order is filled from DT.
- **Stock only.** Prices and sale prices stay managed on the website. The website's POPCORE → Sales screen keeps working.
- **Web orders are separate Website orders.** They take stock out but stay out of the store's sales, closing and cash/card count.
- **Keeping pieces back for the shop follows the daily report** (owner, 2026-11-02). Until a shop day's report is in, each item holds back what the shop sold of it on its best day. See [The shop's sales since its last report](#the-shops-sales-since-its-last-report).

## How items are linked

Each website product and variation is linked by SKU to the POPCORE product with the same SKU:

| Website item | SKU | POPCORE product |
|---|---|---|
| Simple product, or the Single Box variation | `PM0123` | the product (a blind box counts loose boxes) |
| Whole Set variation | `PM0123-SET` | its sealed set (整端), in sets |
| A design (Vault) | `PM0123-05` | the confirmed design, in pieces |

A variable parent has no SKU, because WooCommerce refuses a duplicate SKU. Each SKU is set once on the website with WooCommerce's Product Importer (see Turning it on).

There are three cases where an item does not link:

- **A POPCORE-shaped ID POPCORE does not have.** The item shows **sold out**, because POPCORE holds none of it. Most of these are Whole Sets nobody counted as sets: the floor keeps blind boxes as boxes, and a sealed set exists only once whole sets are counted upstairs. Once POPCORE has the product, the next catalogue read links it. The Website page lists these items.
- **Any other SKU,** such as a barcode. The item is left as the website has it.
- **No SKU.** The item is left as the website has it.

## The website's number

For each linked item, the website number is built like this:

1. Take the saleable stock at the live stores' **floor, upstairs and warehouse**. Display pieces and the claw machine never count.
2. For a blind box, leave out boxes kept in an opened set.
3. Take off what the shop may have sold since its last daily report (below), store by store.
4. Take off the units of paid web orders POPCORE could not record yet (below). The website already took those off its own number, and POPCORE must not put them back.
5. Take off `WEBSITE_KEEP_BACK`, an extra margin on every item (0 by default).
6. Never go below 0.

The sync sets `manage_stock` on every linked item and writes this number.

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

- **Paid.** POPCORE keeps a Website order. It holds the order number, status, paid time, totals, payment title, Stripe transaction id, delivery or pickup, pickup store, and lines (SKU, name, quantity, line total). It never keeps names, emails, phones, addresses or notes.
  - Its items are taken out of the filling store in one `consume`, by the actor `popcore:website` (named "Website"), with the reason `网站订单 Website order #<n>`.
  - The filling store is the pickup store when it is live, otherwise `WEBSITE_DELIVERY_STORE` (DT).
  - Units are picked **upstairs and warehouse first, then the floor**, so the floor stays as the shop's daily report expects. Staff should pick web orders from upstairs.
  - Orders paid before the sync was turned on are ignored, because the opening count already holds them.
- **Cancelled after it was recorded.** The items go back exactly where they came from, as a `correction` of the order's consume (`网站订单取消 Website order cancelled`). WooCommerce restores its own number too.
  - If POPCORE cannot put them back, for example because it no longer has access, the order waits as *cancelled, not yet back in stock*.
- **Refunded.** The order is marked refunded, but no stock moves. WooCommerce does not restock a refund either, unless staff tick "Restock refunded items". A returned piece is received in POPCORE like any return.
- **Cannot be filled.** The order waits on the Website page with its reason, and its units stay off the website number. The reasons are:
  - an item with no POPCORE link (`unknown_item`);
  - the store does not have enough (`not_enough_stock`);
  - no live store can fill it (`no_live_store`);
  - the website has no stock access at the store (`no_store_access`);
  - the order was paid again after a cancel (`reopened`).

  A manager then either presses **Try again** (after counting, receiving or fixing the SKU), or **Settled** with a reason. Settled takes nothing out of stock.

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
4. **The SKU import** (website admin).
   - WooCommerce → Products → Import, tick "Update existing products", and map ID and SKU.
   - The file is `.local/clover-catalog/website-sku-import-2026-11-01.csv` in the main checkout: the 2026-10-25 linking sheet with the 19 post-merge updates applied.
   - `website-sku-not-in-popcore-2026-11-01.csv` lists the 76 SKUs POPCORE does not have today: 60 Whole Sets not counted as sets, 9 designs, and 7 products merged or renamed since 2026-10-25. Those show sold out until fixed.
5. **Server settings** (POPCORE admin, on the droplet). In `/root/POPCORE/popcore_app/.env`:
   - set `WEBSITE_URL=https://popcore.ca`, `WEBSITE_KEY` and `WEBSITE_SECRET`;
   - optionally set `WEBSITE_SHOP_OPENS` (12:00), `WEBSITE_KEEP_BACK` (0) and `WEBSITE_DELIVERY_STORE` (DT);
   - restart POPCORE.
   The background job starts only when the key is set. Never print the key lines.
6. **POPCORE → 网站 Website** (admin).
   - Press **Read website products**. It takes a minute or two.
   - Read **What turning on changes**: the items that sell out, come back or are low, the pickup changes, and the unknown IDs.
   - Press **Turn on**. The first write follows within 30 seconds.
   - **Turn off** stops every write and order reading. The website keeps its last numbers.

## Limits

- **In-store sales at DT reach POPCORE only when the day's report is pasted,** until Clover is connected. The hold above covers each item's best day, not every day. Paste each day's report after closing: a report pasted during the day stops the hold for the rest of that day. Reading Clover alone would not close the gap, because 47% of DT's units are sold off the card machine (随手记: cash, transfer, WeChat, Alipay). POPCORE's Clover checkout, where every sale is rung through POPCORE, removes it.
- **Between the order read and the stock write** of one 30-second pass, a newly paid order can be overwritten by a number POPCORE just changed for the same item. WooCommerce's own 32-minute checkout hold still protects the moment of payment.
- **Only stock counted as sets sells as a Whole Set.** A Single Box counts loose boxes only, never boxes inside sealed sets.
- **Other ways to pay.** The PayPal gateway, WooCommerce POS and the website's own Special Orders "Arrived" button change stock outside POPCORE's checkout. Keep them off while the sync is on (probe questions 5 and 6).
- **Vault products** follow their SKUs like any item. Unlinked Vault items keep their placeholder quantities.
- **Partial refunds** never move stock.

## Local checks

- `tests/test_website_sync.py`: linking, the website number, keep-back, the hold until the daily report is in, opened sets, turning on, writes only on change, back stock first, closing exclusion, cancel, orders that wait and hold their units, MK pickup while MK is not live, orders from before the switch, and a refused key. It uses a fake WooCommerce and blocks every real request.
- `tests/browser/check_website_sync.py`: the page at 390 and 1440 px, against the real Flask app and the same fake. It covers reading products, the preview, turning on, an order that waits and is settled, managers unable to turn the sync on, and staff unable to open the page.
