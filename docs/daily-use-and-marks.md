# Everyone on popcore.store: marks and daily shortcuts

Owner, 2026-11-20, before every staff member starts using the app: "since this is going to be a daily app, so compared
to perfect functions, I am more looking for handy to use and easy and quickly able to resolve all situation", and
"right now since everything is done in wechat so it is also super easy to check who did what … account
tracking/leaving mark is really important". Checkout stays parked until Clover is connected.

## Nothing counts twice

See [Inventory core](inventory-core.md): staff 买单 and 补录 wait for each store's Clover (`app_settings.staff_checkout`,
[checkout](popcore-checkout.md)); a 日报 line moves only what an app screen did not already record that day, with a
这是另外的 It's extra choice; a count after a report's day already includes it.

## Who did what: names on every record

- `people.py` names an account wherever the app says who did something: its staff profile name, `网站 Website` or
  `Clover register` for the system accounts, `没有员工资料 No staff profile` otherwise, never a raw login id.
- Series history and stock documents, receipts, transfers (who started, sent, received), counts (who counted and
  reviewed) and 补货 sheets (who started, submitted and finished: `restock_sessions.created_by/submitted_by/completed_by`,
  migration `restock_session_people`) show `name · M/D HH:MM` in Toronto time (`lib/marks.ts`).

## 动态 Activity

`activity.py`. Every successful change request (POST, PUT, PATCH, DELETE) leaves one `activity_log` row (migration
`activity_log`): who (and on which shared computer), the store when it can be told, a label from `LABELS` or the
endpoint's name, what it was about, a short detail and a link. A route can add to its line with `activity.note(...)`.
Previews, dry runs, lookups and phone notification settings leave none (`SKIP`); a retry with the same
Idempotency-Key from the same account is kept once; writing the row never fails the request. The in-between steps of
搬下来了 Done may ask to be quiet (`X-Popcore-Quiet: 1` on submit, item picks and the send); the arrival never is.

- `GET /api/activity?store_code=&date=&person=&kind=&before_id=&limit=`: newest first, one day, with the people and
  the kinds that day had. Since 2026-11-26 only admins read every line and everyone else reads only the lines they made themselves (docs/access-rules.md). Before that, managers and admins read every store and every line; staff and the store computer read the
  stores they work at (schedule stores, shifts, 门店操作权限), without the lines marked `audience='manager'` (money,
  people, settings, others' availability and punches) unless they did them.
- The app: 动态 in the menu for everyone who works at a store (staff and up, and the counter account), and 店里动态 on
  both home pages with the store's five newest lines. A line opens what it was about.

## Name + PIN on the store computer

`pins.py`, migration `employee_pins`. The store computer signs in as one 查看者 Viewer with 门店操作权限. Every change
it makes asks 谁在操作？: the person taps their name and types their own 4-digit PIN, the request is sent again with
`X-Popcore-Person: <employee id>:<pin>`, and 动态 and the website order note name that person (by way of the counter
account). The server refuses a counter change without one (`428 person_needed`); a PIN never adds a permission.
Looking around, the pickup-code lookup and notification settings need none. The person stays picked for two minutes
(a bar shows who, with 结束 Done) and is kept in memory only.

- Staff set their own PIN from 我的 PIN (the account menu, 更多 on the phone, and a prompt on the staff home while they
  have none). Four digits, not all the same and not 1234/4321/0123; stored salted and hashed (PBKDF2).
- Five wrong PINs in a row lock that PIN for five minutes. Every wrong PIN after that locks it again at once, until the right one is typed. A forgotten PIN is set again from 我的 PIN on the person's own phone; an admin can clear one in 员工 Users → 重设 PIN.

## Daily shortcuts

- **搬下来了 Done** (`pages/Restock/oneGo.ts`): on a pending 补货 sheet at a live store, one person who brought the goods
  down themselves finishes it in one tap: submit, pick what was asked, send and receive, by the same routes as by hand.
  If a step stops, the sheet waits at that step for the usual screens. 动态 shows one arrival line naming the goods.
- **晚盘 at a live store** (`blueprints/inventory.py`, migration `evening_count_sets`): until now saving 晚盘 at a live
  store could not work as staff write it. Each bestseller is now counted as WeChat writes it, whole sets (端) and loose
  boxes (散) on the floor only (owner, 2026-11-21: it is every day, so only the floor); saving compares the boxes with
  POPCORE's floor
  (✅ / 🥲, the difference only for those with 库存查看) and keeps who counted; it may be saved again the same night;
  nothing moves (盘点 corrects stock). 复制到微信 copies `2026.11.20 DT 晚盘` and one `name per*sets➕loose ✅` line each.
- **Staying signed in**: the Auth0 sign-in is kept on the device (`cacheLocation="localstorage"`) and renewed with a
  refresh token (`useRefreshTokens`, with the old way as fallback), and signing in returns to the page it started from
  (`lib/returnTo.ts`). **Owner action in the Auth0 dashboard:** Applications → the POPCORE app → Refresh Token Rotation
  on; APIs → the POPCORE API → Allow Offline Access on. Until then the kept sign-in lasts as long as its access token.

## 记一笔 Quick sale (in place of 随手记)

Owner, 2026-11-21: "right now we use something called 随手记, which is where we record all of the cash/emt/wechat/alipay
sales, and at the end of the day we record all of the POS sales on 随手记 too … popcore.store is capable of replacing that
part, especially we have existing product so we dont have to type everything and remember all of the jizhangming
verbatim. in that way we can also calculate the 晚盘 amount." Card machine (POS) sales are best recorded one by one as
they happen; the screen is quick enough that an end-of-day batch still works.

- **The screen** (`/sales/quick`, `pages/Sales/QuickSale.tsx`): the big tile on the staff home, the first tab of 买单 on
  a computer and its first row on the phone. Find a product by 记账名, name or code (whole sets are left out: on the floor
  they are boxes), set the quantity, mark a display piece, change a price or add a discount when needed, tap how the
  customer paid (each tender shows its amount; WeChat Pay and Alipay also in yuan) and Save. A whole set (整端) sells too.
  撤销 takes back a sale recorded by mistake. Below it: today's sales at the
  store with who recorded each, the day's 审核, and **复制汇总**, which copies the day as text for WeChat (`卡机汇总` one line
  per item over all card sales, `随手记汇总` one line per other sale, items joined by "，", a display piece marked （display）).
- **The server** (`quick_sale.py`, routes in `blueprints/checkout.py`): `POST /api/quick-sales/quote` prices a cart
  (products.price, a design without one takes its series' price; 13% HST; the cash price for every tender but card; cash
  rounded to 5¢; yuan at the checkout rate). `POST /api/quick-sales` (Idempotency-Key) creates and pays one manual
  checkout with the same code as 买单; a payment that fails cancels the unpaid order, and the same key again returns the
  same sale. `GET /api/quick-sales?store_id=&date=` lists a day's app sales and its 日报 text: staff on shift read today
  at their store, managers any day.
- **审核 Review the day, no 日报 pasting** (owner, 2026-11-21: "the stock change only at the end of the day at once
  after the report has been reviewed"; 2026-11-24: "once the 记一笔 is fully built, we dont need the 日报 pasting
  anymore"): a quick sale moves no stock when it is saved (`checkout_orders.stock_by_report`, migration
  `quick_sale_stock_by_report`; its sale document is allocated with reason `daily_report` and no inventory document), so
  it asks no blind-box question, never shows too few on record and never waits for a manager. At the end of the day a
  manager opens 记一笔, looks over 今天 and taps **审核并扣库存**: a preview shows what comes off (and what the floor is
  short of, a sealed set it would open, a display piece), **确认扣库存** saves it. The review posts
  `{source: 'quick_sale', date, store_code}` to `sales.submit_daily_report`, which builds the day's items on the server
  from the sales themselves (`quick_sale.report_body`: by product id, card as 卡机, other tenders as 随手记, a display
  piece also as 卖display; nothing the screen sends is used and no name is matched) and runs the same code a pasted
  日报 ran: `daily_sales` for the reports, stock off the floor once, shortfall notices. `quick_sale_reviews` keeps who
  reviewed which day. Sales recorded or undone after a review show the day as 审核后有改动; reviewing again moves only
  the difference. Earlier days nobody reviewed are listed for managers (还没审核), and until a day is reviewed the 晚盘
  takes its quick sales off what it expects on the floor (`quick_sale.unreviewed_units`, the last 14 days).
- **Reviewed or pasted, never both**: a day with a pasted 日报 cannot be reviewed (`day_pasted`; 今天 then lists where
  the pasted sales and 记一笔 differ, which is the comparison for a side-by-side trial with 随手记), and a reviewed day
  refuses a paste (`day_reviewed_in_app`). The rest of a 日报 (补货, 收货, 调拨, display, 娃娃机) is recorded on its own
  app screens.
- **Checked on real data** (copy of the Nov 20 nightly, DT's Nov 18 report as 48 quick sales): the review writes the
  same `daily_sales` as pasting that report and takes the same sales off stock; the only balances that differ are the
  report's non-sales lines (出库入店, 娃娃机出, display moves), which the review does not carry.
- **撤销** (owner, 2026-11-21): `POST /api/quick-sales/<id>/undo` takes back a sale recorded by mistake. The cashier may
  on the sale's day, a manager any day. Nothing is deleted: each payment gets a correction to zero with the reason
  (`记错了 Recorded by mistake` plus what was typed), `checkout_orders.undone_at/undone_by/undo_reason` mark it, and it
  leaves 今天's total, 复制汇总, the 汇总 page and the sales report (`operational_reports.NOT_UNDONE`; the tender report
  nets the correction). Undone after the day's review, the day shows 审核后有改动 and the next review puts its stock back.
- **No task pile-up**: quick-sale payments and their photos are checked as a day, in 今天, so they are left out of the
  home 待处理 list and Today's payment exceptions (`today_operations.NOT_QUICK`).
- **A retry is the same sale**: the same Idempotency-Key returns the saved sale before anything is priced again, so a
  price or rate that changed in between cannot make a second sale.
- **Who may**: staff with a shift at the store today and 门店操作权限 there, while 员工买单 is open for that store
  (Settings → 员工买单 → 所有店). The store computer's counter account cannot record sales: a PIN adds no permission.
- **Photos**: E-transfer, WeChat Pay and Alipay ask for a photo of the payment after saving. A sale saved without one
  shows 没有照片 No photo in today's list and its 动态 line until a photo is added there (owner: "they can save the order
  without image, but that order should be flagged and saying there isnt a picture").
- **动态**: `记一笔 Recorded a sale · DT-20261121-003` with the tender, amount and items; `加照片` when a photo comes later.
- **不计库存 Sold, not counted** (owner, 2026-11-21: "mark these one as reviewed, we are not going to count their stock.
  and yes we do sell them"): `products.stock_counted = 0` (migration `product_stock_counted`) on an ordinary product
  such as 娃衣, 娃鞋, 眼镜, 购物袋 or a keychain. It sells in 记一笔 and 买单 like any product, but a sale, a return and the
  日报 move no stock for it, and its shortfall notices are not listed. A manager sets it in 产品 →
  the product → 设为不计库存 (an unreviewed product with no stock form is reviewed as an ordinary piece at the same
  time; one with stock on record must be counted to 0 first; blind boxes, sets and designs are always counted) and can
  turn it back with 改回计库存. First marked: AC0012 娃衣 $15, AC0013 娃衣 $20, AC0023 Shoes, AC0009 Glasses, AC0019
  购物袋, TT0001 Hello Kitty 黑煤球, TT0004 Hello Kitty Classic.
- **员工折扣 in 记一笔** (owner, 2026-11-24: in the app, not the 日报): `+ 员工折扣` under the cart, then the name of the
  staff member who bought (正式员工 and above, each with what they have left this month); Save waits for the name. The
  price is whatever the cart says after 改价 or 优惠. The sale keeps the person (`checkout_orders.staff_discount_employee_id`,
  migration `quick_sale_staff_discount_cash`) and shows `员工折扣 <name>` in 今天. It is an ordinary sale: it counts in the
  day's sales and comes off stock at the review (a 日报 员工折扣 line was only a note and moved nothing). The 员工折扣 page
  counts it from the sale (`staff_discounts._sync_quick_sales`, one record per sale under `quick-sale:<checkout id>`, one
  use per different product); an undone sale's record stops counting with `这一笔已撤销`.
- **现金 in the review**: the review's preview has one box, 数到的现金, beside the day's cash sales; it may stay empty.
  The amount is kept on the review (`quick_sale_reviews.cash_counted_cents`) and where a pasted 日报's cash line was
  (`daily_report_metadata.cash_actual_cents`), so 汇总 shows it the same way. No expected amount is worked out: POPCORE
  does not know the float in the drawer.
- **A pasted 日报** (while any is still pasted) leaves out what a 买单 checkout or 补录 sale already took off that day
  (they take their stock when paid; see [Inventory core](inventory-core.md) and `report_stock.app_sales`).

## 移货 Move stock (one screen for every stock move)

Owner, 2026-11-24: "simple down all of the moving around of stock, because right now a lot of the different entries does
the same thing with the stock, what I want is a easy to use app. Keep all of the existing data."

- **Three answers and Save** (`/move`, `pages/Stock/Move.tsx`, `stock_moves.py`): 从哪里, 到哪里, then the goods and how
  many. The places are the store's own (楼上 or 仓库, 楼下, Display, 娃娃机), the other live store (到哪里 only), and two
  ends outside the stock: 新到货 (从哪里) and 不在了 (到哪里, with a reason: 娃娃机出奖, 坏了, 丢了, 送人 / 赠品, 其他 with a
  note). It opens on 楼上 → 楼下, the move made most. So 补货 is 楼上 → 楼下, a delivery is 新到货 → 楼上, a transfer is
  楼上 → MK 店, 入 display is 楼下 → Display, a claw prize is 娃娃机 → 不在了.
- **One step, at once**: `POST /api/stock-moves` saves every line or nothing, through the inventory ledger
  (`inventory_commands`, source `stock_move`), with an Idempotency-Key so the same tap twice is one move. Nothing waits
  for a second step: goods sent to the other store are in its back room as soon as the move is saved, with no request
  list and no receiving step. Unlike sales (which wait for the day's 审核), moves change stock right away.
- **整端**: whole sets stay whole in 楼上 and 仓库 (also the other store's back room) and land anywhere else as their
  boxes; a blind box is asked 端 and 盒, anything else one number. New goods may give a blind box its first whole sets
  (每端几盒).
- **Loose boxes from whole sets**: boxes taken from a back room that has only whole sets open as many sets as they
  need (a `correction` at the back room); the boxes not moved stay loose there. The screen says so before Save
  (`会拆开 2 端`).
- **POPCORE short at the source**: the move is what happened, so it moves what POPCORE has and the rest still arrives
  at the destination; the screen says so before Save (`电脑里楼上少 12，照样移过去`, from `POST /api/stock-moves/check`)
  and the move keeps `missing`. 不在了 takes only what POPCORE has.
- **不在了 is a loss, kept where losses were** (owner, 2026-11-03): each line writes a `stock_losses` row, so the
  manager's 损耗 page lists it with the person and the price that day. 坏了 sets the goods aside on the damaged pile at
  the place (a manager puts them back on sale or throws them away on 损耗); 丢了 (丢失), 送人 / 赠品 and 其他 (both 其他,
  with the note) leave stock at once. 娃娃机出奖 is not a loss and only leaves the claw. Such a record is undone on
  移货, where it was made (`recorded_on_move` on the 损耗 page).
- **撤销**: the person who recorded it, that day, or a manager puts a move back while the goods are still where it left
  them (`stock_move_gone` otherwise). Nothing is deleted; 今天 on the screen lists the day's moves with who and when.
- **Who**: staff with 门店操作权限 at the store. That now includes 不在了, which on the 损耗 screen is managers only;
  every move is in 动态 with the person's name (`移货 Moved stock · 楼上 → 楼下`), and every loss on the manager's 损耗 page.
- **Not moved**: a product not reviewed yet, or one marked 不计库存.
- **Menus**: 移货 replaces 到货 and 补货 on the rail, the phone tab bar, the home tiles and 库存's tabs (which keep 盘点,
  晚盘 and, for managers, 损耗); a product's stock details in 库存 open 移货 with it picked (`/move?product=`) and keep
  Open sealed set (a customer tray). The old screens (收货, 调拨, 补货) keep their addresses and every record they made;
  nothing was deleted or migrated.

## Checks

`tests/test_staff_rollout.py`; browser checks `check_activity.py`, `check_store_computer_pin.py`,
`check_restock_one_go.py`, `check_evening_count.py`, `check_quick_sale.py` and `check_staff_home.py` (staff waiting for
Clover); `tests/test_quick_sale.py` for 记一笔 and the paste leaving its sales out; `tests/test_stock_moves.py` and
`check_move_stock.py` for 移货.
