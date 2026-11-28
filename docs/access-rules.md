# Who sees what

Owner, 2026-11-26, before every employee got an account: only the admin sees 动态, sales, the value of stock and
summaries; each employee sees only their own schedule, monthly hours, 员工折扣 and 记一笔; employees do not see the
website section. This page is the rule for every screen and every API answer. Where an older document says
"managers and admins" about something listed under *Admin only* here, this page wins.

Roles: `viewer` (the shared store computer), `staff`, `manager` (店长, 副店长), `admin`. The server checks every
call; hiding a menu is never the check.

## Admin only

- 动态: `GET /api/activity` gives everyone else only the lines they made themselves.
- Sales: every route in `blueprints/sales.py` except `submit_daily_report` (the 店长's 审核 in 记一笔 calls it) and
  the staff stock route `/api/stock/batch_operation`; the five financial reports and `/api/reports/overview`; the
  `financial` and `report_stock` sections of `/api/today`; the whole-store scope of `GET /api/store-events`.
- Stock value: `GET /api/inventory/value`, the overview's `value` and its figures' `value_cents`,
  `total_stock_value` in `/api/stock/summary`, the Stock Value column in 库存 → All products.
- Sales figures in the 库存 overview: units sold, pace, days left, last sale, best sellers, 日报 dates. The refill,
  running-low and sold-out lists stay for everyone who sees the overview.
- The 网站 page: `/api/website/status`, `preview`, `orders`, price OK, retry, dismiss.
- 网站订单记录 Web order records (owner, 2026-11-28, popcore-work#157): `GET /api/website/records` (销售 → 网站订单)
  and the `web_orders` of `/api/store-events`, which only the whole-store (admin) scope carries. 每日汇总 shows the
  day's in-store orders and the store's web orders as two lines; a web order never adds to the store's total.

## Store computer and admin only

网站订单 (owner, 2026-11-26: "网站订单 pick board close to staff but available to viewer which is store computer"): the
board, the code box, Ready, Handed over and Packed (`/api/website/board…`) answer only a 查看者 Viewer or an admin with 门店操作权限
at the store. Staff and managers get 403 `board_store_computer_only`, their menus have no 网站订单, and `/web-orders`
shows the no-access page. A new web order is pushed only to the store computer's and admins' devices. Managers keep
a device switch on 异常提醒 for its alerts; staff cannot turn a device on (`notifications_not_for_staff`) but can turn
their own off.

## Managers keep

审核 of the day's 记一笔 (the whole day's list and totals), 收工 review, 损耗, a look at this wage month's 排班
and everyone's hours, 员工折扣 management with amounts, 异常提醒, products, 图鉴 editing, section counts.

### 排班: look only, this wage month only

Owner, 2026-11-28: 店长 and 副店长 must not edit the schedule, and see one month: the current wage month (now Nov 4 –
Dec 3; it moves on the 4th, the `schedule_month_start_day` setting). Replaces 2026-11-27's "this month + next", in which
they could still change shifts inside the window.

- Only an admin changes the team's 排班. `blueprints/schedule.py` `admin_changes` answers a `manager` 403
  `schedule_read_only` on adding, changing (times, store, position, notes, 实际下班 actual finish) and removing
  shifts, the conflict check (asked only before saving), period notes, the checklist, the shared reminder and
  schedule-only trainees. Staff get the plain 403 as before.
- `_manager_window` limits a manager's reads of the team's shifts, availability, notes and checklist to the wage
  month running today in Toronto: reads are cut to it, anything wholly outside is 403 `schedule_month_closed`.
  月工时 (`reports/monthly`, one person's month, `employees/<id>/hours`) opens only that same wage month.
  `GET /api/schedule/config` gives the page `manager_window` so its arrows (titled with the dates) and month picker
  stop there.
- The page shows a manager 只能查看 View only, a day's shifts without Assign or Edit buttons, no shift dialog and no
  checklist; a manager has 我的可上时间 to send their own availability, as staff do.
- One's own shifts, own availability, own 月工时, 打卡 and own calendar feed stay one's own in every month; admins see
  and change every month.

## Own records only (below manager)

- 记一笔: `GET /api/quick-sales` returns the caller's own sales and totals from those.
- 员工折扣: the caller's own row and uses; `GET /api/staff-discounts/buyers` gives names only for the buyer picker.
- 排班: own shifts and availability; own month from `GET /api/schedule/reports/monthly/me`.
- 收工: no day totals, expected drawer or variance, and today only. (收工 left the menus on 2026-11-27, see below;
  the rule still holds for its address.)
- 特殊订单: orders of stores the account has 门店操作权限 for, or its own.
- 移货 day list: today only, and 不在了 moves only for whoever recorded them.

## 记一笔 guards (below manager)

- A typed price only for a product with no list price (`price_needs_manager`).
- A "+ 优惠" amount only with a named 员工折扣 buyer (`discount_needs_manager`); the buyer must be eligible and
  have a use left this month (`staff_discount_none_left`).
- 补录 (`POST /api/sale-documents`) is for managers; a staff checkout is always dated today.

## 库存查看

Stock numbers need the tick at that store. 移货 stays open to everyone with 门店操作权限 (owner, 2026-11-24) but
answers only `short`, never how many, without the tick. Goods counts, 晚盘 expected numbers, the 晚盘 list's floor
numbers and trade-slot quantities are hidden without it. Ids are read the same way before the check and by the
command, so an id sent as text cannot skip it.

## The store computer asks nothing

Owner, 2026-11-26: "PIN not necessary", then "dont need the 谁在操作 on the store computer, we have cameras". The
查看者 counter account makes its changes (Ready, Handed over, Packed) as itself: 动态 and the website note name the shared
account ("Ready in popcore.store by DT 柜台"), and who was at the computer is the cameras' answer. There is no PIN
and no name list: their routes are gone and migration `employee_pins_cleared` wiped every kept PIN. Nobody below
admin changes their own name or email.

## 收工 is out of the menus

Owner, 2026-11-27: "Drop 收工 instead". A 收工 could not be submitted on a normal day: it wanted a manager-approved 盘点
of every bestseller for that day (the 晚盘 does not count) and every 记一笔 cash payment verified one by one, and none
was ever submitted on live. The day is closed by the 店长's 审核 in 记一笔, with 数到的现金. `/closing`, its API and
every record stay; no menu, tab or page links to it any more.

## A page someone cannot open

One page for every closed address (`pages/Unauthorized.tsx`): Chinese first, who the page is for (管理员, 店长和管理员,
or "网站订单在店铺电脑上处理") and one button, 回首页. It never says what the page holds. 库存 without the 库存查看 tick uses the
same look and offers 晚盘 and 移货.
