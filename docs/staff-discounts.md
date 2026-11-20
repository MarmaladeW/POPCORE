# 员工折扣 Staff discounts

The record of staff discount uses, at `/staff-discounts` (owner, 2026-11-19: "there should be a 员工折扣 discount
record, each 正式员工 and above have 4 discount per month, we wanna keep track of that, and sometimes in 汇总 there are
员工折扣, it is easier for us to track the revenue too."). Code: `staff_discounts.py`, routes in
`blueprints/staff_discounts.py`, page `frontend/src/pages/StaffDiscounts/`, migration `staff_discounts`.

## The rules

- **4 uses a month** for 正式员工 and above (`ranks.DISCOUNT_RANKS`: 店长, 副店长, 资深员工, 正式员工). They come back
  on the 1st of every calendar month; the page's month is Toronto's.
- **One use is one product** (owner: "purchasing ten smiski hipper is still one employee discount"). Ten of the same
  product in one purchase is 1 use; two different products are 2.
- 培训员工 and people without a rank are not eligible. They show on the page only when they have a use, marked
  不适用 Not eligible.

## Where records come from

- **The 日报.** The report paste already saves its 员工折扣 lines as text (`daily_report_metadata.employee_discounts`).
  The page reads them when it opens a month; nothing in the report save changed. Each line is keyed by store, day, its
  text and which copy of that text it is on the day, so:
  - a re-paste adds only lines that are new, and the same line written twice on one day is two purchases;
  - a line no longer on its report goes, unless a manager fixed it;
  - a manager's fix, pick or 不算 is never undone by a re-paste.
- **Reading a line**, for example `Celia使用员工折扣购入Angry Molly 迪士尼公主毛绒*2,emt 54,本月剩余0次`:
  - who: the text before `使用员工折扣` (`购入` or `购买` follows; under the 员工折扣 heading `staff购买…` also reads);
  - products: `name*qty` (also `×`, `x`, `X`), split by `,`, `，`, `、` or `+`;
  - payment and amount: a piece like `emt 54` or `卡机18.07` (EMT, 卡机 card, 现金 cash, 微信, 支付宝);
  - `本月剩余N次` is the staff's own count by hand and is not read.
  A line with no 购入/购买, no product, or a piece that is neither a product with its quantity nor a payment cannot be
  read and waits for a manager.
- **By hand.** A manager adds a use: person, day, store, products, amount and payment.

## Who a line names

The exact name of an active person, in any case, links the line. Otherwise it waits for a manager, who picks the
person; the pick is remembered (`staff_discount_aliases`), so `zm` is that person on every later line, and other lines
waiting on `zm` follow at once. A name two active people share also waits.

## Who sees what

- **Staff** see the page: everyone's uses this month out of 4 (at 4 in amber, over 4 in red), the month's records and
  what waits. They change nothing; the server refuses them (403). No amounts, report text or money totals reach them:
  financial figures are manager-only.
- **Managers** (店长, 副店长) and admins also see what was paid per store and in total ("easier for us to track the
  revenue"; records marked 不算 are left out, waiting ones are in), and can add a use, fix a record (person, uses
  counted, amount, payment), pick the person for a waiting line, and mark a record 不算 Ignored with a reason (算回
  Count again undoes it).
- **Viewers** (查看者) cannot open it.

## Menus

Managers find it in the rail's 查看 group and in 更多, next to 销售; staff at the end of 日常 and in 更多 after 网站订单.

## Checks

`tests/test_staff_discounts.py`; `tests/browser/check_staff_discounts.py` at 390 and 1440 px (screenshots in
`.local/staff-discounts/`); `frontend/src/pages/StaffDiscounts/staffDiscountsView.test.ts`.
