# 网站订单 Web orders board

The shop computer's order fulfilment screen, at `/web-orders` (owner, 2026-11-19: "we need an account/screen to show
all of the delivery order/pickup order, to be on the computer in the store ... we just want to show the information
and not be able to change too many stuff"). It follows the popcore.ca website session's brief of 2026-11-19, which
replaced its pickup-code brief of 2026-11-16. Code: `website_board.py`, routes in `blueprints/website.py`, page
`frontend/src/pages/WebOrders/`.

## Who sees what

- Anyone with a role opens it. Each user sees the orders of the stores they have **Users → Store operations access**
  for; with a store selected in the top bar, only that store's. A pickup belongs to its pickup store. Every delivery
  goes out of `WEBSITE_DELIVERY_STORE` (DT), so deliveries show on DT's board.
- The page reads the board every 30 seconds, the sync's pace, without reloading.

### The store computer's account

Owner, 2026-11-19: "they can also check the products and stocks for names, stocks, and check the dex for prices, but
they wont be able to edit anything from there." An admin makes it in 员工 Users:

1. A new account, for example `dt-counter` named DT 柜台, with the role **查看者 Viewer**.
2. **门店操作权限 Store operations access: DT.** This opens 网站订单 for DT, Ready and Handed over included.
3. **库存查看 Inventory view: DT.** This opens 库存 (the overview, the series lists and All products) to look at.
4. Optionally turn off its schedule listing, so it does not appear among the staff to schedule.

What it can do: 网站订单 with its two buttons and notifications, 库存 to look at, 产品 Products and 图鉴 Dex (prices
included) to look at, and 排班 Schedule to look at, as any viewer. What it cannot do: anything else. Every other change
in POPCORE needs Staff or above on the server, store operations access included: a viewer posting a stock move is
refused. The 库存 pages show their 入库, 调拨, 晚盘, 娃娃机, count and move buttons to staff only, and the menus give it no
买单 Checkout, 入店, 补货, 汇总 or 晚盘.

A viewer without store operations access gets an empty board and no 网站订单 in the menu. Notes on the website name the
account ("Ready in popcore.store by DT 柜台"), so personal sign-ins remain the way to record who pressed.

## The board

The code box sits at the top. Under it are five columns, each with the oldest paid order first:

| Column | Orders |
| --- | --- |
| 自取 · 待拣货 Pickup · to pick | pickup, `processing`: the only cards with a button, **Ready** |
| 自取 · 等顾客 Pickup · ready, waiting for the customer | pickup, `ready-pickup` |
| 配送 · 待打包 Delivery · to pack | delivery, `processing` |
| 配送 · 等快递 Delivery · booked, waiting for the courier | delivery, `dispatched` |
| 配送 · 派送中 Delivery · out for delivery | delivery, `out-for-delivery` |

Under them:

- **需要经理 Needs a manager**, with the reason in staff words:
  - every open order whose status is none of the five, such as `awaiting-stock` (a special order waiting for stock);
  - every order POPCORE could not take out of stock (`needs_review`) or put back after a cancel (`cancel_failed`).
    These leave their column until a manager settles them on the Website page.
- **今天完成 Done today**, folded shut: orders completed, cancelled or refunded today (Toronto time), newest first.

Each card shows:

- the order number and how long ago it was paid;
- each line by 记账名 (else the official name, else the website's name), its quantity and POPCORE ID, and where it
  was taken from (store and place), from the order's consume document;
- pickup: the pickup store, and "Taken from DT: bring it to MK" when the stock came from another store;
  a pickup that has waited 3 days or more stands out (the website never cancels an uncollected pickup);
- delivery: the window, Normal or door to door, and the zone the customer chose; once FleetNow is booked, its
  number, status, pickup window and delivery window.

Nothing personal is on the board or in its data, and never the pickup code: the code proves the person at the
counter is the customer. The managers' Website page never receives the code either.

## The website's fields

The sync keeps these from each order's `meta_data` on every read (`website_orders.pickup_code` and the JSON
`website_orders.delivery`): `_popcore_pickup_code`, `_pcc_delivery_window`, `_pcc_delivery_service`,
`_pcc_delivery_zone`, `fleetnow_order_no`, `fleetnow_pickup_window`, `fleetnow_delivery_window`, `fleetnow_status`.
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

Both are pickup only; FleetNow moves deliveries.

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

## Notifications for new orders

Owner, 2026-11-19: "the order on woocommerce does not notify me, it only notifies the one email that is setted, so we
need our own notification thing." Two kinds, both for new paid web orders only:

- **On a phone or computer, even with POPCORE closed** (`push_notifications.py`). On 网站订单, a person taps
  **在本设备开启 Notify me on this device** and allows notifications. From then on that device gets a notification for
  every new paid web order of the stores they have store operations access for: "网站订单 #35864 · 自取 DT pickup" with
  its lines (and "需要经理 Needs a manager" first when POPCORE could not take it out of stock). Tapping it opens
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
- Automatic FleetNow booking was off on popcore.ca (last seen 2026-11-16), so a paid delivery stays in "to pack" until
  a manager books it on the website.

## Checks

`popcore_app/tests/test_website_board.py`, `popcore_app/tests/test_web_order_notifications.py` and `popcore_app/tests/browser/check_web_orders.py` (screenshots in
`.local/web-orders/`).
