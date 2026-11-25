# Staff ranks and shift positions

Owner, 2026-11-19.

## Ranks

Five ranks, top first: **店长** Store manager, **副店长** Assistant manager, **资深员工** Senior staff, **正式员工** Regular
staff, **培训员工** Trainee (`popcore_app/ranks.py`, `frontend/src/lib/ranks.ts`).

- One picker in 员工 Users → 职位 Position sets both the rank and the Auth0 role:
  - 店长 and 副店长 get manager rights;
  - 资深员工, 正式员工 and 培训员工 get staff rights;
  - **管理员 Admin** and **查看者 Viewer** stay their own roles, without a rank. A viewer (such as the store computer's
    account) is never a 培训员工.
- A rank lives on the person's `employees.rank`. Setting one before their first sign-in makes their row.
- 培训员工 is the old 培训中 status, and 正式员工 and above are 转正. The 转正 date (`regular_since`) is set the day someone
  first becomes 正式员工 or above, and can be moved in Users. The schedule's 训 and 正 marks and the monthly hours keep
  working from it.
- The upgrade made people marked 培训中 into 培训员工 and people 转正 into 正式员工. 店长, 副店长 and 资深员工 are picked in
  Users. The upgrade could not see Auth0 roles, so the first time an admin opens Users, any 管理员 or 查看者 account it
  ranked loses its rank again.
- Every list of people shows them in rank order, 店长 first, then by name: Users, the schedule's people, coverage and the
  monthly hours. Each person shows a badge: 长 副 资 正 训.
- In 排班 Schedule (owner, 2026-11-22), shifts on or after someone's 转正 date show their rank: 长, 副, 资 or 正 (正 for
  anyone 转正 without a higher rank). 培训员工 shows 训. The same mark is on the people chips, the day panel, coverage,
  the shift dialog and 月工时. Shifts with the same start and length list 店长 first; otherwise they keep time order.

## Shift positions

- The position "End" is now **Back**; shifts saved with End were renamed.
- Markham's positions are **Cashier** and **Sales**. Downtown keeps Front, Cashier and Back. Settings → Scheduling still
  edits them.
- Each position shows with its emoji, as a white chip on the shift: 👋 Front, 💰 Cashier, 📦 Back, 🛍️ Sales (📍 for any
  other). On phones the month view shows the emoji.

Checks: `tests/test_staff_ranks.py`, `src/lib/ranks.test.ts`, `tests/browser/check_employee_hours.py`,
`tests/browser/check_schedule_calendar.py`.
