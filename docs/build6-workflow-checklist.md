# Build 6 workflow checklist

Every operation starts from **Today**, opens the saved server document by ID, performs only role-permitted actions, refreshes from the API after success, and then appears with its new state in Today or Reports.

| Work | Staff | Manager | Confirmed result |
| --- | --- | --- | --- |
| Draft or incomplete sale | Open own sale, record it, add payment evidence | Link source identity, map verified product IDs and allocate stock | Sale detail reloads with posted, payment and allocation facts |
| Tender or evidence exception | Add private evidence to own payment | Verify or reject each payment/evidence item with its own reason | Sale detail and financial Today/report state refresh |
| Refund | View recorded tender events | Record an exact tender refund amount and reason | Refund event appears; sale snapshot remains |
| Physical return | View existing returns | Select sale line, quantity, disposition and reason | Inventory receipt and return history appear; no refund is inferred |
| Goods, count, transfer, restock | Open the linked saved document and finish its existing workflow | Review or approve through its existing screen | Today removes or updates the completed item |
| Draft or returned closing | Resume by `closing_id`, finish source documents, enter cash events and a deliberate denomination count, submit | Review every exception separately, return with a reason, or close | Closing reloads from current source token; closed snapshot remains immutable |

The **All Stores** selection never presents a closing mutation. Version and source-token conflicts require refresh and review; the UI does not assume stock, payment, evidence, or closing success before the authoritative response.
