# 网站订单 Web orders board

The shop computer's order fulfilment screen, at `/web-orders` (owner, 2026-11-19: "we need an account/screen to show
all of the delivery order/pickup order, to be on the computer in the store ... we just want to show the information
and not be able to change too many stuff"). It follows the popcore.ca website session's brief of 2026-11-19, which
replaced its pickup-code brief of 2026-11-16. Code: `website_board.py`, routes in `blueprints/website.py`, page
`frontend/src/pages/WebOrders/`.

## Who sees what

- Staff and above open it. Each user sees the orders of the stores they have **Users → Store operations access**
  for; with a store selected in the top bar, only that store's. A pickup belongs to its pickup store. Every delivery
  goes out of `WEBSITE_DELIVERY_STORE` (DT), so deliveries show on DT's board.
- The page reads the board every 30 seconds, the sync's pace, without reloading.

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

`popcore_app/tests/test_website_board.py` and `popcore_app/tests/browser/check_web_orders.py` (screenshots in
`.local/web-orders/`).
