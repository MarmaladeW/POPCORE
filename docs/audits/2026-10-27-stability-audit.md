# POPCORE stability audit

**Executed:** 2026-10-26 to 2026-10-27
**Revision audited:** `main` at `4134cdc6` (PR #172 merged), which is what production runs
**Fixes:** branch `claude/stability-audit-fixes`

The audit followed the shift-editor bug fixed in PR #172, where opening a shift to record its actual finish could rewrite its planned hours to another shift's type. The question was whether the rest of POPCORE could change or lose data the same way before the inventory moves over.

## Result

The core is sound. Money is integer cents everywhere. Every stock change goes through one ledger transaction with idempotency keys and version checks. There is no SQL injection, and uploads are safe. Production's data is consistent, and a rehearsal of both stores' go-live on a copy of the real data reconciled cleanly.

The audit still found real defects, most of them in the two places that matter next:

- Screens that reuse a form for different records, the same bug class as the shift editor.
- Workflows that only become live with the inventory cutover: restock, opening, closing and sale allocation.

- **Fixed:** 35 defects, each with a regression test.
  - 20 affect production today, while both stores are still legacy (S1–S20).
  - 15 would have bitten at or right after go-live (R1–R15).
- **Decided by you and implemented:** all 11 owner decisions (D1–D11), each with tests that fail on `main`.
- **Server checks:** two read-only checks of the server backups (D12–D13); one shift needs your confirmation.
- **Resilience:** health check, safe deploy with rollback, continuous off-site backup and staging, for about $1.20/month. The repo side is ready; nothing is installed yet.
- **Listed, not changed:** 18 lower-priority items (L1–L18).

## Method

- **Baseline on `main`:** 801 backend tests, 83 frontend tests, the production build, all 30 browser checks and `check_release_recovery.py` passed. A fresh build of the frontend source is byte-identical to the committed `popcore_app/static` (233 of 233 asset names, and the same `index.html`).
- **Five parallel audits**, each over one area: inventory core, goods and restock flows, sales and closing, platform and security, and frontend stale state.
  - Every finding was reproduced: against disposable SQLite databases through the real Flask routes, or against the real `.tsx` sources.
  - Each finding was then checked again before being accepted. One suspected shift-editor issue did not reproduce and was dropped. A browser test now pins that behaviour.
- **Every fix has a regression test** that fails on `main` and passes on the branch: a unit test, a browser-check step, or both. The fix commits change only what each defect needs.
- **No production access.** Data checks used the nightly backup already pulled to this Mac (`popcore_20261026_151002.tar.gz`), opened read-only from a scratch copy. Nothing was deployed, committed or pushed.

## The shift bug (PR #172)

- **Cause:** a Location effect in `ShiftModal.tsx` re-ran when the dialog reopened. It re-applied the shift type left over from the previous dialog to the new shift's times, and Save then wrote those hours.
- **Fix:** PR #172 removed that effect. The preset now re-applies only when the manager changes the Location. Since then, planned times change only through explicit actions: choosing a shift type, changing the Location or Employee, or editing the times.
- **This audit added:** a browser step that opens an MK shift and then a DT full day. It checks that the type is read with each shift's own store hours.
- **Effect on real data:** last night's backup holds 488 shifts, all well formed, and none of the 45 recorded finishes shows the bug's signature (planned hours far from the actual finish).
  - The bug existed from 2026-08-11 until the PR #172 deploy (2026-10-27 02:40 UTC).
  - One backup cannot prove that a planned time was never rewritten earlier.
  - Edits between the backup (2026-10-26 07:30 UTC) and the deploy are not in any backup on this Mac yet.

## Production data checks (backup of 2026-10-26 07:30 UTC)

- **Integrity:** `integrity_check` ok, `foreign_key_check` empty, 60 migrations.
- **Shifts:** no malformed rows, no end at or before start, no duplicate employee and date.
- **Stock:** no negative rows and no orphan rows. Both stores are legacy, and no opening has been applied.
- **`daily_sales`:** no negative quantities, no orphans and no duplicate product/date/store rows.
- **Server clock:** restock sheets started at 20:40 and 21:51 Toronto time were stored with the next day's date, so the server's local date is UTC. The fixes below switch those dates to Toronto.
- **Monthly hours report:** it was missing 7 shifts worked by 5 people who were later removed (May, August and September). None fall in the current pay period.

## Go-live rehearsal on a copy of the real data

- **Migrations:** the current migrations applied twice to the copy. Integrity stayed ok and every row count was preserved.
- **DT opening:** whole-catalog count sheets (2,361 rows per location) were filled with plausible counts. The DT dry run reported 292 counted rows and 273 products verified by counting, with no errors. Apply reconciled: no ledger differences and no stock-row differences.
- **Day one on live DT:**
  - Receive, move and consume all posted.
  - An exact retry returned the original result; the same key with a changed payload was refused.
  - A stale version and an overdraw were refused, with nothing changed.
  - A checkout cash sale deducted exactly once, and a retry returned the same sale.
  - MK legacy writes still worked and created no ledger rows. DT's legacy route demanded a key.
  - Reconciliation was clean.
- **MK opening second:** MK refused to treat its warehouse as empty while legacy stock sat there. Once that stock was counted it applied and reconciled.
- **Found during the rehearsal:** re-running an applied opening printed 20 errors instead of `already_applied`. This is fixed below (R3).

## Fixed on the branch

### Affects production today (both stores still legacy)

| # | What went wrong | Where | Regression |
|---|---|---|---|
| S1 | The monthly hours report dropped the worked hours of anyone later removed (a trainee removed, a user deleted). Real data: 7 shifts in May, August and September. | `blueprints/schedule.py` | `test_schedule_actual_finish` |
| S2 | The Stock 库存操作 dialog kept the previous product's operation and location. "Receive 6" on the next product could post as "set its in-store count to 6". A re-render could also swap the chosen product back to the row's. | `Stock/RestockModal.tsx`, `BatchStockModal.tsx` | `check_inventory_core.py` |
| S3 | 晚盘 kept one store's counts after a store switch and could submit them as the other store's. | `Restock/EveningCheckStep.tsx` | `check_restock_steps.py` |
| S4 | Special-order admin corrections could save one order's creator, dates and pickup onto another order at the same version. | `SpecialOrders/index.tsx` | `check_special_orders.py` |
| S5 | A migration that failed part way left its schema change unrecorded, so every later start failed until the database was repaired by hand. | `db.py` | `test_migration_atomicity` |
| S6 | Every restart rewrote 盲盒毛绒 / 盲盒Figure product types to 盲盒. It now runs once; production has no such types today. | `db.py` | `test_migration_atomicity` |
| S7 | One request carrying an unknown login key id emptied the cached login keys, so a short Auth0 outage then logged everyone out with errors. | `auth.py` | `test_auth` |
| S8 | Read-only (viewer) accounts could add product aliases and silently change a learned report heading's section. | `blueprints/products.py` | `test_product_alias_permissions` |
| S9 | Bulk product delete removed image files before the database refused the delete. That left broken images, and the repo's backup tool then aborted. | `blueprints/products.py` | `test_uploads` |
| S10 | The restock request screen showed another store's warehouse stock. | `blueprints/products.py`, `Restock/RequestStep.tsx` | `test_product_search_shop_names`, `check_restock_steps.py` |
| S11 | Merging a legacy product into its Clover product dropped claw, display and employee quantities from same-day sales rows, and silently kept one of two prices. Different prices are now refused for review. See D12. | `scripts/merge_clover_products.py` | `test_clover_product_merge` |
| S12 | Restock, 晚盘 and "hours this wage month" took the UTC server date, so anything after 20:00 (19:00 in winter) was dated tomorrow. | `blueprints/restock.py`, `blueprints/inventory.py`, `blueprints/schedule.py` | `test_restock_business_date`, `test_schedule_actual_finish` |
| S13 | A restock sheet could never be finished in the app while its store is legacy, and the open sheet then blocks closing. | `Restock/PickingStep.tsx`, `SessionModal.tsx` | `check_restock_steps.py` |
| S14 | The shift-conflict lookup was readable by any signed-in user. It is now manager only, like the shift editor that uses it. | `blueprints/schedule.py` | `test_schedule_actual_finish` |
| S15 | After a failed special-order request, the next action (even on another order) re-sent the failed one instead. | `SpecialOrders/index.tsx` | `check_special_orders.py` |
| S16 | The trade inspection form kept the previous customer's condition, disclosure and checks. | `Trades/index.tsx` | `check_trades_today.py` |
| S17 | If settings failed to load, Save wrote defaults over the real configuration: store hours, shift presets and the wage-month start. | `Settings/index.tsx` | `check_foundation.py` |
| S18 | A failed load of schedule period notes followed by Save overwrote the stored note; changing the period dropped unsaved notes. | `Schedule/CoveragePanel.tsx` | `check_schedule_calendar.py` |
| S19 | A late reply for the previously opened product could overwrite the product being edited. | `Products/ProductModal.tsx` | `check_inventory_core.py` |
| S20 | Product detail kept the previous product's facts and buttons after a late or failed load. | `Products/ProductDetail.tsx` | `check_inventory_core.py` (phone) |

### Would bite at or right after go-live

| # | What went wrong | Where | Regression |
|---|---|---|---|
| R1 | 确认拣货 stayed live after a pick. Pressing it again dispatched the found quantity a second time (4 carried, ledger moved 8). A dispatch after a short-close returned a 500. | `goods_operations.py`, `Restock/PickingStep.tsx` | `test_restock_lifecycle`, `check_restock_steps.py` |
| R2 | A restock sheet left open before go-live posted its old pick against the fresh count. The opening now refuses while such sheets exist. | `inventory_opening.py` | `test_inventory_opening` |
| R3 | Re-running an applied opening with the same files reported errors instead of `already_applied`. | `inventory_opening.py` | `test_inventory_opening` (through the CLI) |
| R4 | The public `restock_complete` command ignored a delivery in transit and moved the goods twice. | `inventory_commands.py` | `test_restock_lifecycle` |
| R5 | A refused first pick left a planned delivery behind, which locked the sheet against edits and deletion. | `goods_operations.py`, `blueprints/restock.py` | `test_restock_lifecycle` |
| R6 | Restock screens labelled every quantity 端, while stock moves in the product's own unit (盒, 个 or 端). | `Restock/*`, `blueprints/restock.py` | `check_restock_steps.py` |
| R7 | Picking lost the typed found quantity on each save and saved the requested quantity instead. | `Restock/SessionModal.tsx`, `PickingStep.tsx` | `check_restock_steps.py` |
| R8 | Verifying a product after its store went live left untrusted legacy numbers that showed as verified, and they blocked the next store's opening. | `catalog_identity.py` | `test_identity_after_golive` |
| R9 | A closing submitted at night could not be signed off once the store moved stock the next day. | `closing_operations.py` | `test_closing` |
| R10 | Allocating a pending sale took display pieces from floor stock and dropped a chosen opened set. | `sales_operations.py` | `test_sales_posting` |
| R11 | A pending sale with the same product from display and from the floor could never be allocated, so closing stayed blocked. | `sales_operations.py` | `test_sales_posting` |
| R12 | The Tenders report ignored manager payment corrections. | `operational_reports.py` | `test_operational_reports` |
| R13 | Closing never flagged a sale whose tenders don't add up to what was collected. It is now a `payment_mismatch` exception that needs a reason. | `closing_operations.py` | `test_closing` |
| R14 | Barcode scans in quick succession during receiving or counting dropped units, lines and typed edits. | `Stock/Receiving.tsx`, `Stock/Counts.tsx` | `check_goods_flow.py` |
| R15 | A refund, physical return or cash event could be recorded twice by pressing the button again after success. | `Sales/SaleDocument.tsx`, `Closing/index.tsx` | `check_store_day.py` |

## Owner decisions (2026-10-27)

| # | Finding | Decision | Done on the branch |
|---|---|---|---|
| D1 | A blind-box sale of more than half a set stuck in pending allocation, blocking closing | Ask the choice at checkout | Checkout (manual and Clover) and 补录 ask 开新端 / 已开的端 / 散盒 for such lines, and payment waits for the answer. The pending-sale page offers the same three answers, so a stuck sale can always be finished. 散盒 takes loose boxes only. |
| D2 | Cash not rounded to 5¢ | Round it | Physical cash only, Canadian rule ($7.47 → $7.45, $21.63 → $21.65). The difference is booked as rounding. E-transfer, WeChat, Alipay and card stay exact. |
| D3 | Cash refunds counted twice or on the wrong day | One path: refund on the sale | A refund on a sale carries the day it is recorded, and that day's closing subtracts it from the drawer. The sale's own day keeps its receipts, and a closed day gets a late adjustment instead of being rewritten. Closing's own "现金退款 Cash refund, no sale" now needs a note and says it is only for money with no sale in POPCORE. |
| D4 | Staff can't open closing on a two-cashier day | Staff can close, with as little information as possible | Staff with a shift at that store that day (and the store-operations grant) can open the closing, count cash and submit it. They see tender totals, the cash count and blockers as labels only: no other cashier's orders, references or links. The manager signs off as before. |
| D5 | Returns dated to the sale's day | Date them the day they happen | Physical returns are dated the day they happen, and a closed sale day gets a late adjustment. |
| D6 | Rejected payments counted as received | Don't count them | A rejected payment no longer counts in the sale total, closing's tenders or the Tenders report. It appears as "Not received" with the amount still owed (so closing raises `payment_mismatch`), and the report has its own Rejected column. |
| D7 | Cash price of small totals | Under $10, round down to the whole dollar | $5.10 → $5, $9.99 → $9, $7.12 → $7. A whole-dollar total keeps its price ($5.00 → $5.00): this is one named rule (`whole_dollar_under_ten`), in case you want $1 off instead. Under $1: about 2.5% off, as before. $10 and up: unchanged. |
| D8 | No store isolation before go-live | Turn it on now | Legacy stock, daily-report, restock and 晚盘 writes need staff role plus one of: admin, an operations grant for that store, a schedule assignment to that store, or a shift at that store today. Old sales reports are manager only. |
| D9 | Removed users keep access until their token expires | Delete the user but keep their shifts | A blocked or deleted account is refused on its next request (`account_disabled`), and its calendar link stops working. Deleting keeps every shift and its hours. The delete confirmation lists the person's future shifts so you can reassign them. Shortening the Auth0 token lifetime is still worth doing. |
| D10 | Clover payments that never reach POPCORE | Fix | Paid Clover orders with no items are imported for a manager to map, or to mark as a charge without stock. Orders that fail to import block closing until they import or a manager resolves them with a reason. |
| D11 | Clover orders from before go-live deducted again | Fix | Orders created before the store's opening are never imported. Runbook: connect Clover to a store only after it goes live. |
| D12 | Possible lost detail from the Oct 25 merges | Check the server backups | No loss; see below. |
| D13 | Shifts the bug may have rewritten | Check the server backups | Two likely cases; see below. |

## Server backup checks (2026-10-27, read-only)

All 20 snapshots on the server, from Oct 8 to the backup taken just before the PR #172 deploy, were copied read-only and compared.

**Oct 25 merges (D12).** No sales row was merged into another product's row for the same day, so the dropped-channel bug never fired. Day totals confirm it. The 143 sales rows that disappeared belong to the 29 old products deleted on purpose on Oct 25.

**Shift rewrites (D13).** Five planned-time changes happened after Oct 8.

- **Likely caused by the bug: #373, Mason, Sat Oct 10, DT.** In the same save that recorded a 21:29 finish, the shift went from 16:30–22:00 (the afternoon half; RU had the morning) to 11:00–22:00 (a full day). Unless Mason really started at 11:00, this adds **5.5 paid hours** in the current pay period.
- **Likely caused by the bug, no effect on pay: #353, Zimo, Oct 5.** 12:00–21:30 became 12:00–22:00 while a 21:30 finish was recorded. Hours count to the finish, so they are unchanged.
- **Probably deliberate:**
  - #359 (Mason, Oct 6): 17:00 became 16:00, which is not a preset time.
  - #497 (Mason, Oct 29) and #509 (JOYCE, Nov 1): future shifts changed to halves that complete the day's AM/PM split.

There is no snapshot before Oct 8. Of the 37 shifts edited between Aug 11 and Oct 8, 18 now show a preset time, which is the only kind the bug could produce. Most were edited days before the shift and form tidy AM/PM splits. The four edited after the shift date are the ones worth a glance: #174 Zimo, #175 Celia and #177 Kayla on Sep 4, and #359.

## Resilience (repo side; nothing installed yet)

| Layer | Protects against | Monthly cost |
|---|---|---|
| `GET /api/health` | Lets deploys and uptime monitors see that the app and its database answer | $0 |
| `scripts/deploy_production.sh` | A bad release or migration. It backs up and integrity-checks, rehearses the release's migrations on a copy of the real database, then deploys, health-checks, and returns the code to the previous commit automatically if needed. The database is never auto-restored; the script explains why. | $0 |
| `scripts/rollback_production.sh` | One command back to the previous release, with an optional database restore that saves the current one first | $0 |
| Litestream (`deployment/litestream.*`, `scripts/restore_from_litestream.sh`) | Losing the server or a bad data change. The database streams to an R2 or B2 bucket every 10 s and can be restored to any point. | $0 within the free tiers |
| Staging (`deployment/popcore-staging.*`, `scripts/refresh_staging.sh`) | Testing a release against a copy of real data before production. It runs one worker, is easy to stop, and stops itself after 12 h. | $0 |
| DigitalOcean weekly backups | Losing the whole server | about $1.20 |
| Uptime monitor on `/api/health` | An outage nobody notices | $0 on a free tier |
| Insights write lock | Staff writes failing with "database is locked". The longest lock went from about 8.8 s to about 13 ms. | $0 |

Owner actions: create an R2 or B2 bucket and key; enable DigitalOcean backups; add a `staging.popcore.store` DNS record and its Auth0 URLs; set up a free uptime monitor; approve the server installs. The runbook is `docs/operations-resilience.md`.

## Lower priority (not changed)

- **L1:** Today hides between-store transfers from both stores' staff. They are still listed under 入店 → 调入 and flagged at closing.
- **L2:** Today's manager financial rows still add up original payment amounts, including corrections, refunds and rejected payments. The sale page, closing and reports are correct.
- **L3:** Legacy stock writes check the store's mode before taking the write lock. Stopping the app during `--apply`, as the runbook says, covers this.
- **L4:** Batch import reads "3端" as 3 boxes. The review table does say "boxes".
- **L5:** The stock dialog treats 1端 as 1 box when the product's details fail to load, which happens for an account with no inventory grant.
- **L6:** `restock_complete` through the API doesn't release opened-set provenance. No screen uses it.
- **L7:** Malformed numbers sent to `/api/inventory/commands` return 500 instead of 400. Nothing is written.
- **L8:** The nightly insights job holds the write lock for about 9 s. An admin "generate" during business hours makes other writes fail.
- **L9:** gunicorn binds `0.0.0.0:5000`. Port 5000 is not reachable from outside today, but binding `127.0.0.1` would be safer.
- **L10:** 晚盘 still labels 理论 / 实际 with 端, and Today's restock row says "申请 N 箱".
- **L11:** A `/pick` racing a `/complete` of the same sheet is not prevented. The window is narrow.
- **L12:** Allocation replaces a line's saved fresh-set choice with the mapping's choice. This relates to D1.
- **L13:** A closing submitted before this deploy goes stale once and needs Return and resubmit. None are pending in production.
- **L14:** Stock and sales routes fall back to the UTC server date when a client sends no date. The screens always send one.
- **L15:** A transfer between a live store and a legacy store is refused. This is by design until both stores are live.
- **L16:** A trade-slot count cannot be approved (`trade_context_required`). This affects the API only.
- **L17:** The batch stock import shows a single fuzzy match with the same ✓ as an exact match.
- **L18:** After a confirmed "Return for changes" on a closing, the return reason stays filled in and reappears at the next review.

## Before each store's count day

These are in addition to docs/inventory-core.md "Opening counts":

1. **Give every staff member the store-operations grant for their store(s).** Use Users → Store operations access. A live store needs it for checkout, receiving and closing. Today only 4 accounts have one. Until a store goes live, its legacy screens also accept a schedule assignment to that store or a shift there that day. Six people with shifts (ANLIN, FULIN, JOYCE, RU, YC, Ziqi) have neither a grant nor an assignment, so they get in only on shift days.
2. **Turn on the off-site backup (Litestream) before the first count,** so the opening and day one are protected to within seconds.
3. **Finish or delete open restock sheets at that store.** The opening now refuses them (`open_restock_sessions`). Production has one: DT #14 (2026-04-05, pending, 1 item).
4. **Connect Clover to a store only after it goes live.** Alternatively, set `CLOVER_IMPORT_SINCE` to the go-live time (see D11).
5. **Stop the app during `--apply`,** as the runbook says. A legacy write already in flight can otherwise land after the cutover (L3).
6. **Re-running the same `--apply` after a dropped session is now safe.** It reports `already_applied` and writes nothing.

## What this does not prove

- No live Auth0 login, no Clover device or cloud traffic, and no real opening count.
- No deployment.
- Browser checks run against disposable data with mocked authentication.
- The Clover findings were reproduced with the tests' fake connector, not live Clover.
