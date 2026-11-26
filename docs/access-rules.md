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
- The 网站 page: `/api/website/status`, `preview`, `orders`, price OK, retry, dismiss. The 网站订单 board stays for
  staff and the store computer.

## Managers keep

审核 of the day's 记一笔 (the whole day's list and totals), 收工 review, 损耗, 排班 and everyone's hours, 员工折扣
management with amounts, 异常提醒, products, 图鉴 editing, section counts.

## Own records only (below manager)

- 记一笔: `GET /api/quick-sales` returns the caller's own sales and totals from those.
- 员工折扣: the caller's own row and uses; `GET /api/staff-discounts/buyers` gives names only for the buyer picker.
- 排班: own shifts and availability; own month from `GET /api/schedule/reports/monthly/me`.
- 收工: no day totals, expected drawer or variance, and today only.
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

## Name + PIN

Only for the store computer: it says who is acting there. A PIN is never checked for another account, never sent
to a page (`people.public`), and an account can check only its own from a phone. Every wrong PIN after a lock locks
again at once. Nobody below admin changes their own name or email. An admin clears a PIN; anyone sets a new one
from their own phone.
