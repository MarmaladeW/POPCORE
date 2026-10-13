# Operational reports

Managers can open **Reports** for the stores assigned through `inventory_access`. Inventory and movement reports follow existing inventory read access. Sales, tender, evidence, cash variance, and closed-day reports require the manager role.

Report dates use ISO business dates (`YYYY-MM-DD`). Results are ordered, paged, and limited to 500 rows per request. CSV export uses the signed-in API request, applies the same store scope, escapes spreadsheet formulas, excludes evidence object paths and secrets, and refuses reports above 500 rows instead of truncating them.

Money comes from recorded sale and payment facts. Unknown totals remain unknown and contribute to the incomplete count. Tender totals are grouped directly from payment rows, so joined sale lines, evidence, and source links cannot multiply them. Inventory remains separated by product stock unit, location, and disposition.

Costs and profit estimates are intentionally absent.


## Manager insights and historical sales review

`/reports` opens a manager overview for an explicit store scope and a bounded Toronto business-date period (up to 366 days). Detailed report links retain the period. Posted sale documents supply gross amounts; incomplete amounts are labelled as a known subtotal. Completed checkouts linked to these documents are counted once. Open orders and unlinked completed orders are separate review items.

Historical `daily_sales` reports remain a separate source. Coverage lists every calendar date and distinguishes report rows, metadata-only notes, and no report. Missing dates do not establish zero sales. Historical price estimates use saved `unit_price` only; missing snapshots stay unknown and current catalogue prices never replace them. Historical quantities do not establish native inventory units or profit. Posted product rankings preserve recorded labels and native units, with up to 20 products per unit. Inventory exceptions are a current snapshot independent of the selected period; unreviewed stock stays unknown.

`/sales/matching` lets authorized managers review past names, notes, saved channel quantities, current product identity and unselected candidate products. A correction requires an exact target, a reason, and confirmation that the entire saved row belongs to it. Aggregated raw names cannot be split automatically because per-name quantity provenance is absent. The endpoint changes only `daily_sales.product_id`, recording immutable before/after row and identity snapshots. Original text, quantities, price and stock remain unchanged.

Corrections use a stable request key and checked source/target tokens. Replay rechecks current store access. Stale rows, changed target identities, incompatible known units, an existing target row for the same store/date, and report days with stock history are rejected. Stock-linked corrections require separate reconciliation. A correction does not teach a broad alias; reviewed parser choices remain scoped to the normalized name and identity-bearing notes. Packaging, generation, size, hidden-variant and explicit confirmed-design notes prevent an unreviewed exact-name auto-confirmation.

These changes are locally verified with synthetic records. They do not import, correct, or reconcile production history automatically.
