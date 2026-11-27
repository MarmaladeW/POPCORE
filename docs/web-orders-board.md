# 网站订单 Web orders board

The shop computer's order fulfilment screen, at `/web-orders` (owner, 2026-11-19: "we need an account/screen to show
all of the delivery order/pickup order, to be on the computer in the store ... we just want to show the information
and not be able to change too many stuff"). It follows the popcore.ca website session's brief of 2026-11-19, which
replaced its pickup-code brief of 2026-11-16. Code: `website_board.py`, routes in `blueprints/website.py`, page
`frontend/src/pages/WebOrders/`.

## Who sees what

- The store computer (a 查看者 Viewer) and admins open it (owner, 2026-11-26: "网站订单 pick board close to staff but
  available to viewer which is store computer"). Staff and managers cannot: the server answers 403
  `board_store_computer_only`, their menus have no 网站订单 and the address shows the no-access page
  (docs/access-rules.md). Each of the two sees the orders of the stores they have **Users → Store operations access**
  for; with a store selected in the top bar, only that store's. A pickup belongs to its pickup store. A delivery
  belongs to the store it leaves from (see [Which store a delivery leaves from](#which-store-a-delivery-leaves-from)).
- The page reads the board every 30 seconds, the sync's pace, without reloading.

### The store computer's account

Owner, 2026-11-19: "they can also check the products and stocks for names, stocks, and check the dex for prices, but
they wont be able to edit anything from there." Owner, 2026-11-24: the 查看者 Viewer role is now this account: "specific
for the store computer, where it is able to see the order we need to prepare in store, see the stock, dex, no need for
shift, able to view products". An admin makes it in 员工 Users:

1. A new account, for example `dt-counter` named DT 柜台, with the position **查看者 Viewer · 店铺电脑**.
2. Tick **店铺电脑 Store computer: DT**. One tick saves both grants underneath: 门店操作权限 (opens 网站订单 for DT,
   Ready, Handed over and Packed included) and 库存查看 (opens 库存 to look at). A viewer with only one of the two shows the
   tick half filled; a click saves both.

A viewer is never offered for shifts: making one, or changing someone to viewer, turns its schedule listing off, and
opening Users turns it off for a viewer made before this change. Changing a viewer to a staff position turns it back on.

What it can do: it opens on 网站订单 (its buttons and notifications), 库存 to look at, 可疑人员 Watch list, 产品
Products and 图鉴 Dex (prices included) to look at. Its menus are 网站订单 · 库存 · 可疑人员 and 产品 · 图鉴; on a phone
网站订单 · 库存 · 产品 · 更多. What it cannot do: anything else, 排班 Schedule included. Every other change in POPCORE needs
Staff or above on the server, store operations access included: a viewer posting a stock move is refused. The 库存 pages
show their 入库, 调拨, 晚盘, 娃娃机, count and move buttons to staff only, and the menus give it no 买单 Checkout, 入店,
补货, 汇总 or 晚盘.

A viewer with no 店铺电脑 store ticked opens on a 店铺电脑 page that says so, with links to Products, the Dex and the
watch list; its board names the tick it needs. Its buttons ask nothing (owner, 2026-11-26: no PIN, and "dont
need the 谁在操作 on the store computer, we have cameras"), so the note on the website names the account ("Ready in
popcore.store by DT 柜台").

## The board

The code box sits at the top, with the name box beside it (under it on a narrower screen). Under them are five
columns, each with the oldest paid order first:

| Column | Orders |
| --- | --- |
| 自取 · 待拣货 Pickup · to pick | pickup, `processing`, with **Ready** |
| 自取 · 等顾客 Pickup · ready, waiting for the customer | pickup, `ready-pickup` |
| 配送 · 待打包 Delivery · to pack | delivery, `processing` or `dispatched`, not packed yet, with **Packed** |
| 配送 · 等快递 Delivery · packed, waiting for the courier | delivery, `processing` or `dispatched`, packed, with **Undo** |
| 配送 · 派送中 Delivery · out for delivery | delivery, `out-for-delivery`, packed or not |

Pickups go by the website's status. Deliveries go by packing (owner, 2026-11-27, popcore-work#139: "shouldnt it be
moved to 等快递？"): a delivery is in 待打包 until someone presses Packed, whether or not a courier is booked, and in
等快递 after it. FleetNow's pickup moves it to 派送中. In the data the 等快递 column keeps its old key `booked`, so a
page opened before the change still reads the board.

Under them:

- **需要管理员 Needs an admin** (需要经理 until 2026-11-27; `needs_manager` in the data), with the reason in staff words:
  - every open order whose status is none of the five, such as `awaiting-stock` (a special order waiting for stock);
  - every order POPCORE could not take out of stock (`needs_review`) or put back after a cancel (`cancel_failed`).
    These leave their column until an admin settles them on the Website page.
- **今天完成 Done today**, folded shut: orders completed, cancelled or refunded today (Toronto time), newest first.

Each card shows:

- the order number and how long ago it was paid;
- each line by 记账名 (else the official name, else the website's name), its quantity and POPCORE ID, and where it
  was taken from (store and place), from the order's consume document. Boxes taken from a sealed set the order
  opened (popcore-work#128) show the place the set was kept, such as 楼上 upstairs;
- pickup: the pickup store, and "Taken from DT: bring it to MK" only when some of the stock came from another store.
  A pickup comes off its own store first (owner, 2026-11-22), so this shows only when that store was short;
  a pickup that has waited 3 days or more stands out (the website never cancels an uncollected pickup);
- delivery: the store it leaves from ("配送 MK"); "收件人 Ship to" and the name the parcel goes to (see below); the
  window, Normal or door to door, and the zone the customer chose; once FleetNow is booked, its number, status, pickup
  window and delivery window; and what has to be moved first, only when something does (next section);
- delivery, once packed: "已打包 Packed · 14:05 · DT 柜台" (when, and the account that pressed it). Flagged when it
  needs saying: **快递未预约 Courier not booked yet** on a packed delivery no courier is booked for (an admin books it
  on popcore.ca), and **快递已预约,先打包 A courier is booked: pack it first** on a booked one not packed yet.

## Which store a delivery leaves from

Owner, 2026-11-26 (popcore-work#121): the FleetNow courier collects from the store the order was filled from, so
Markham's board shows the deliveries Markham packs. POPCORE tells the website which store that is
([website-sync.md](website-sync.md#where-the-courier-collects-a-delivery)), and the board follows the same facts:

| The delivery | Whose board | The card says |
| --- | --- | --- |
| Not booked yet, taken from one store | that store's | 配送 MK |
| Not booked yet, taken from DT and MK | both | 配送 DT + MK, and "bring it to one store" (the website books no courier for it until a manager dispatches it) |
| Booked: the website names the store (`_fleetnow_pickup_store`, FleetNow 1.2.13) | that store's | 配送 MK |
| Booked with no store named (before 1.2.13, or its switch off) | DT's | 配送 DT |
| Booked at one store, but some of it was taken from another | both | "Taken from MK: bring it to DT" |
| Waiting for a manager (not taken out of stock) | DT's | under Needs an admin, as before |

A new delivery's phone notification goes to the people of the store it leaves from. Until FleetNow 1.2.13 is on
popcore.ca with *Collect from the store popcore.store names* switched on, every booking still goes from Downtown: a
delivery Markham filled then shows on both boards once it is booked, with "Taken from MK: bring it to DT".

The pickup code is never on the board or in its data: the code proves the person at the counter is the customer. The
managers' Website page never receives the code either. The only personal thing on the board is each delivery's name;
a pickup's name stays behind its code.

## Names on deliveries

Owner, 2026-11-25: "for the popcore.store 网站订单 tab, we should be able to see the name for the delivery orders, as
well as searching up the name." Pickups were left as they are: their names show only after the code is typed.

- Every delivery card, in the three delivery columns, Needs an admin and Done today, shows the shipping first and
  last name, else the billing name.
- POPCORE reads them live from the website on each board refresh, in one request for all the board's deliveries
  (`GET /orders?include=<ids>&status=any&_fields=id,shipping.first_name,shipping.last_name,billing.first_name,billing.last_name`,
  100 orders a request), so only those four fields leave the website. It waits 15 seconds at most: if the website does
  not answer, the board shows without names and the name box says so.
- Names are never written to POPCORE's database, logs, activity, push notifications or the service worker. The board's
  answer is sent `Cache-Control: no-store`, and an error report (Sentry) drops the local variables of the board's code.
- **收件人 Delivery name**, the box beside the pickup code, looks through the deliveries on the board as staff type:
  part of a first or last name, any case, with or without accents ("zoe", "NG" and "zo n" all find Zoë Ng). Each match
  shows as a card with where it is on the board. It searches in the browser, so a typed name is never sent or put in
  a link. It is there on a board that has deliveries, and always on the delivery store's (`WEBSITE_DELIVERY_STORE`).

## The website's fields

The sync keeps these from each order's `meta_data` on every read (`website_orders.pickup_code` and the JSON
`website_orders.delivery`): `_popcore_pickup_code`, `_pcc_delivery_window`, `_pcc_delivery_service`,
`_pcc_delivery_zone`, `fleetnow_order_no`, `fleetnow_pickup_window`, `fleetnow_delivery_window`, `fleetnow_status`,
`_fleetnow_pickup_store` (the store FleetNow is booked from).
Open orders recorded before the board came read them once (`website_sync.read_board_facts`).

## The code box

- What staff type is cleaned: capitals, letters and digits only (`k7r-4mx` → `K7R4MX`).
- POPCORE finds the order by its code, then reads the order live from the website
  (`GET /orders/{id}?_fields=id,status,billing,meta_data`), so a cancel made a minute ago is seen. The website's code
  must still match.
- Completed, cancelled or refunded, not a pickup, not found: said plainly, with nothing about the order.
- Another store's order (by store operations access, or the selected store): says which store. No Handed over.
- Any other status than `processing` or `ready-pickup`: says so and asks for a manager. No Handed over.
- Otherwise the card shows the order, the customer's first name (`billing.first_name`) and "Collecting: <name>" when
  `_pcc_collector_name` is set, with **Handed over**. Only those two values are taken from the read; the rest of the
  billing details are dropped at once. They are never stored or logged, the answer is sent `Cache-Control: no-store`,
  and they leave the screen when the card closes (Close, Handed over, or after 3 minutes).

## Ready and Handed over

Both are pickup only; deliveries have Packed (next section).

- **Ready** on a `processing` card: `PUT /orders/{id}` with `{"status":"ready-pickup"}`. The website emails the
  customer the Ready email with the code.
- **Handed over**, from the code box only, on `ready-pickup` (or `processing`, when staff skipped Ready):
  `{"status":"completed"}`. The website sends the Completed email. The request carries the typed code, and POPCORE
  refuses it unless the code matches the order's, in POPCORE and on the website.
- Each first reads the order's status live. Already there counts as done ("already"), so a second press is safe. Any
  other status refuses, so a cancelled order is never put back.
- Nothing shows as done until the website answers 2xx. If the write fails, staff see the website's message.
- Then a private note: `POST /orders/{id}/notes` with `"Ready in popcore.store by <name>"` (or "Handed over …") and
  `customer_note: false`. `<name>` is the staff member's name in POPCORE, else their sign-in. A shared shop account
  makes every note name that account. If the note fails, the status still counts and the page says the note is missing.
- Stock does not move: the sync took the items out when the order was paid.

The sync's REST key needs Read/Write, which it already has for products. Confirm it on the first real Ready press.

## Packed and Undo

Owner, 2026-11-27 (popcore-work#139): "theres no option to say packed for the 打包 ones?" … "shouldnt it be moved to
等快递？". He chose an Undo and a phone notification to the admins.

- **打包好了 Packed** on a 待打包 card moves it to 等快递. **撤销 Undo** on a 等快递 card moves it back. One click
  each, no confirmation: neither reaches the customer.
- Who: the store computer and admins with store operations access for a store the delivery leaves from (either
  store while its stock is in two). Pickups are refused (`not_delivery`); an order waiting for an admin is settled
  on the Website page first (`needs_manager`).
- Each first reads the order's status live. Only `processing` and `dispatched` can be packed or unpacked; anything
  else (out for delivery, cancelled) refuses with `status_changed`. Already packed (or not) counts as done.
- POPCORE keeps `website_orders.packed_at` (website GMT time) and `packed_by` (the name of the account that pressed
  it); Undo clears both. Orders from before the change were not marked: they show as not packed.
- The website gets a private note only: "Packed in popcore.store by DT 柜台", or "Packed undone in popcore.store by
  …". POPCORE never sets a delivery's status and never books FleetNow. If the note fails, Packed still counts and
  the page says the note is missing.
- 动态 has a line for each: 网站订单 #703 打包好了 Packed, or 撤销打包 Undid Packed.
- **Packed with no courier booked** (no FleetNow number, status not `dispatched`) pushes to the admins' devices at
  the store it leaves from: "网站订单 #703 · 已打包 Packed", "配送 DT · 快递未预约 Courier not booked yet: book it on
  popcore.ca (Dispatch via Fleetnow)." Order number and store only. Not the store computer, which pressed it, and
  not for a delivery whose courier is already booked, nor for Undo. A failed notification never fails Packed.

## Notifications for new orders

Owner, 2026-11-19: "the order on woocommerce does not notify me, it only notifies the one email that is setted, so we
need our own notification thing." Two kinds, both for new paid web orders only (admins are also told when a
delivery is packed with no courier booked; see [Packed and Undo](#packed-and-undo)):

- **On a phone or computer, even with POPCORE closed** (`push_notifications.py`). On 网站订单, a person taps
  **在本设备开启 Notify me on this device** and allows notifications. From then on that device gets a notification for
  every new paid web order of the stores they have store operations access for (the store computer and admins only
  since 2026-11-26; each device keeps its owner's role, refreshed whenever they open POPCORE, and a device turned on
  before that keeps getting them until its owner's next visit): "网站订单 #35864 · 自取 DT pickup" with
  its lines (and "需要管理员 Needs an admin" first when POPCORE could not take it out of stock). Tapping it opens
  网站订单. **测试 Test** sends one to every device of that person; **关闭 Turn off** stops this device.
  - iPhone and iPad: only POPCORE added to the Home Screen (Share → Add to Home Screen, iOS 16.4 or later) and opened
    from there can turn them on. In Safari itself the page says so.
  - It is standard Web Push: no app store, no outside account. Each message is signed with POPCORE's own key (VAPID,
    RFC 8292) and encrypted for that one device (RFC 8291), so Google, Apple or Mozilla, who carry it, cannot read it.
    Messages hold the order number, store and lines, never anything about the customer, and expire after a day.
  - POPCORE posts only to the browsers' push services (`fcm.googleapis.com`, `*.push.apple.com`,
    `*.push.services.mozilla.com`, `*.notify.windows.com`); a page cannot make it post anywhere else. A device the push
    service says is gone is forgotten. A failed notification never holds up the sync.
  - The key is made on first use in `push-vapid-key.pem` beside the database (`PUSH_KEY_FILE` overrides; mode 600,
    ignored by git, not in the database or its backups). If it is lost, a new one is made and every device taps Notify
    me again.
  - The service worker (`/sw.js`) only shows these notifications. It caches nothing.
- **On the open board**: a new order pops up at the bottom right at the next refresh and stays until closed, with a
  chime. **提示音 Sound** turns the chime off for that browser. Browsers play sound only after someone has touched the
  page, so on the shop computer click anywhere on the board once after opening it.

## What the board never does

Cancel, refund or edit an order; send an email (the website sends them all); book FleetNow (a manager uses "Dispatch
via Fleetnow" on the website's order screen); move stock; set a delivery order's status.

## Limits

- An order reaches POPCORE only once it is paid (`date_paid`). An `on-hold` order the website never marked paid is not
  on the board.
- Delivery columns are only as fresh as the website's FleetNow status checks (every 15 minutes). FleetNow 1.2.9 must
  be on popcore.ca, or delivered orders stay in "booked".
- Automatic FleetNow booking was off on popcore.ca (last seen 2026-11-16), so a packed delivery waits in 等快递 with
  快递未预约 until an admin books it on the website.

## Checks

`popcore_app/tests/test_website_board.py`, `popcore_app/tests/test_web_order_notifications.py` and `popcore_app/tests/browser/check_web_orders.py` (screenshots in
`.local/web-orders/`).
