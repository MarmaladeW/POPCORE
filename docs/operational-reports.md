# Operational reports

Managers can open **Reports** for the stores assigned through `inventory_access`. Inventory and movement reports follow existing inventory read access. Sales, tender, evidence, cash variance, and closed-day reports require the manager role.

Report dates use ISO business dates (`YYYY-MM-DD`). Results are ordered, paged, and limited to 500 rows per request. CSV export uses the signed-in API request, applies the same store scope, escapes spreadsheet formulas, excludes evidence object paths and secrets, and refuses reports above 500 rows instead of truncating them.

Money comes from recorded sale and payment facts. Unknown totals remain unknown and contribute to the incomplete count. Tender totals are grouped directly from payment rows, so joined sale lines, evidence, and source links cannot multiply them. Inventory remains separated by product stock unit, location, and disposition.

Costs and profit estimates are intentionally absent.
