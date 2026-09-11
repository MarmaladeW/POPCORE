# Inventory and sales writer inventory

This inventory was refreshed from the Build 6 source tree. Test fixture scripts write only disposable `.local` databases.

| Writer | Ownership in authoritative mode |
| --- | --- |
| `blueprints/stock.py` inventory commands and legacy adjust/restock routes | `post_inventory` is authoritative. Legacy stock routes use the narrow authoritative adapter with request keys, scope, units and versions; meaningless legacy operations are rejected. Notes-only edits do not change quantity. |
| `blueprints/restock.py` | Authoritative branches post through `post_inventory`; legacy tables remain compatibility/read history. |
| `blueprints/goods.py` and `goods_operations.py` | Receipts, transfers and counts own their workflow documents and post inventory atomically through the inventory service. |
| `blueprints/sale_documents.py`, `sales_operations.py`, `payments.py`, `closing_operations.py`, `trade_operations.py` | Own versioned sale, tender, closing and trade facts. Every stock change posts through the inventory service with an immutable source ID. |
| `blueprints/sales.py` legacy daily reports, batch operations and clear-day tools | Daily-report stock mutation is blocked in authoritative mode unless classified as reconciliation. Batch stock operations use the authoritative adapter. Historical sales rows remain legacy facts and are not relabeled as tender revenue. |
| `blueprints/inventory.py` legacy inventory-check submit | Rejected in authoritative mode; new counts use goods/count workflows. |
| Product/sheet/bulk catalog routes | Catalog metadata only. They cannot create authoritative opening quantities. Identity and conversion changes are separately validated. |
| Schedule, users, stores, settings and insight dismissal | Non-stock writers. Inventory access changes affect authorization but do not post quantity. |
| `scripts/rehearse_inventory_migration.py` | Synthetic `.local` rehearsal only; representative changes use `post_inventory`, while deliberately ambiguous legacy rows stay inactive. |
| `scripts/check_release_recovery.py`, browser `local_app.py`, tests | Synthetic `.local` fixtures only and never accepted as a production writer. |
| Future Clover, WooCommerce and purchasing connectors | No active connector writer exists. A future connector must use source IDs, scoped versioned services and reconciliation rules before enablement. |

Before activation, search again for direct writes to `stock`, `inventory_balances`, `inventory_movements`, `sale_documents`, `sale_payments`, `cash_events`, and trade tables. New direct writers block cutover review.
