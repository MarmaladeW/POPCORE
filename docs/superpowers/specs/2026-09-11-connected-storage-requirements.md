# Connected storage requirements — working draft

Status: requirements and proposed architecture for owner review. No connector has been implemented or activated by this document.

## Outcome and confirmed scope

POPCORE must maintain usable physical inventory across Downtown and Markham through four real operating flows:

1. Offline store sales recorded through Clover, the current primary sales channel.
2. WooCommerce online orders, including confirmed designs allocated exclusively to online sale. Online selling has not yet launched.
3. Overseas shipment receiving, the source of new supplies.
4. Daily replenishment from upstairs/warehouse to the store floor at either location.

An accurate inventory screen depends on these operations being captured. UI changes and a standalone ledger do not satisfy this scope. Both Clover and WooCommerce connections are required deliverables.

## Existing foundation and gaps

Reviewed local sources: `inventory_commands.py`, `sales_operations.py`, `blueprints/sale_documents.py`, and the inventory, goods, sales, and writer-inventory runbooks.

The repository contains atomic inventory posting, movement history, balance versions, duplicate-request protection, manual sales with source links, and documented receiving/delivery/count workflows. The source-link API is a staff/manager workflow, not an external connector. There is no active Clover or WooCommerce connector in the reviewed application source.

The required additions include external account authorization, product and store mapping, automated sale intake, durable incoming notifications, catch-up reconciliation, outbound availability publication, online allocation/reservations, and a visible connection-health/exception workflow. Existing foundations need end-to-end verification; their presence does not establish production readiness.

## Proposed ownership

- POPCORE owns physical balances, movements, allocations, and inventory history.
- Clover owns the original in-store order/payment facts. A Clover stock change and its underlying sale must never both deduct POPCORE inventory.
- WooCommerce owns the original online order and checkout facts. Its local reservation/stock behavior must be coordinated with POPCORE, not overwritten blindly.
- Overseas shipment plans describe expected supply. Only physically accepted receipts add local on-hand stock.
- Restock requests describe demand. Dispatch and receipt describe physical movements.
- One stable POPCORE product identity maps to the exact Clover merchant/item and WooCommerce site/product/variation IDs. Product names and shared series barcodes are not sufficient identifiers.

For each product, show physical quantity by location, saleable quantity, online allocation, order commitments, available-to-sell quantity, and unresolved exceptions. Online allocation is an entitlement to stock, not an extra physical quantity. Reservations consume that entitlement rather than being subtracted from physical stock twice.

## Four complete workflows

### Clover

Connect each real merchant account to its correct POPCORE store. Import catalog identities for reviewed mapping, then consume item-level completed sales with original order, line, and payment references. Payment retries and split tenders do not represent extra units sold. Order creation alone does not establish a completed sale.

Handle edits, voids, refunds, partial payments, delayed/offline sales, and duplicate/out-of-order notifications explicitly. Record actual paid sales even if their item mapping or stock allocation needs review. A refund does not automatically restore physical inventory.

The background connection uses Clover REST/OAuth. The newly confirmed tender-selection-to-phone workflow also requires a Clover checkout integration; evaluate the custom-tender app path against the actual devices and plan. Ordinary Register tender selection is not exposed as a pre-completion broadcast or REST notification. Other checkout information needing validation includes the exact confirmed design or fresh opened set; the connector cannot infer those physical choices from a generic series line item.

If all sales are not currently entered in Clover, agree how the remaining sales will enter the system before claiming complete capture. Any Clover-side custom-tender or checkout changes must be scoped against the actual Canadian merchant accounts and devices.

#### Confirmed Clover-to-phone evidence workflow

The owner describes card payments, with a credit-card surcharge, and discounted alternative payments: cash, e-transfer, WeChat Pay, and Alipay. Cash needs no evidence photo. E-transfer, WeChat Pay, and Alipay require a photo showing the customer's evidence of sending payment.

When staff chooses an alternative method in Clover, the same order must become available on the employee's phone without entering its order number, products, or amounts again. For methods requiring evidence, staff opens the matching order, takes/uploads a photo, and attaches it to that payment. Cash still synchronizes as a sale but creates no photo task. Preserve the original subtotal, tax, payment discount, rounding, credit-card surcharge when applicable, and actual amount as distinct facts; the phone displays the authoritative calculation rather than recalculating it independently. Exact surcharge configuration is not yet specified.

Proposed phone interaction: an authenticated, store-scoped list of payments awaiting evidence, showing method, amount, register/time, and enough order detail to prevent selecting the wrong sale. A paired-register phone can receive the current payment directly if that pairing matches actual staffing. Automatic linkage uses the merchant/order/payment-attempt identity, never an amount/time guess. Do not automatically open the camera or route private evidence to every staff phone.

Evidence upload status and actual payment status remain separate. A customer-side photo documents what staff saw; uploading it does not independently verify receipt of funds. The owner confirms checkout may complete before the photo is attached. Every non-card, non-cash payment requires a photo; its order remains visibly flagged as missing evidence until the required photo is successfully saved. Card and cash payments are exempt. A completed sale remains completed while evidence is pending. Switching tenders or cancelling must retire the old pending task without attaching its evidence to a replacement payment. Repeated handoffs or uploads must not create another sale or inventory deduction.

Earlier owner statements recovered from the task “Store Operations Overview” specify a roughly 2–3% post-tax reduction toward a whole-dollar payment without excessive rounding down, with examples $41.34 → $40, $7.12 → $7, and $99.14 → $97. The owner confirms Clover should suggest and prefill the discounted payable amount, and staff may change it before completing payment. Retain the suggestion and final staff-confirmed amount with the acting employee; synchronize the final amount to the phone and sale record. The earlier assistant proposed comparing 2% and 3% candidates and choosing the one closest to a whole dollar; that exact suggestion algorithm remains unconfirmed. Staff override is allowed without an invented manager-approval requirement.

### WooCommerce

Map each sellable confirmed design to its exact product or variation. Publish only the quantity assigned to online selling from approved fulfillment locations. Prevent ordinary in-store sales and restock suggestions from silently consuming stock allocated exclusively to online sale; reassignment is an explicit operation.

Define reservation, payment, cancellation, expiration, picking, shipment, and return transitions. A reservation reduces availability; dispatch reduces physical on-hand. Changing an order from processing to completed must not deduct the same goods again. A pre-dispatch cancellation releases the appropriate commitment; a shipped order does not regain stock merely because money was refunded.

Coordinate WooCommerce's own checkout holds and stock deductions with publication. An older POPCORE balance must never overwrite newer WooCommerce orders that POPCORE has not yet processed. Start with dedicated online stock and versioned publication; shared last-unit inventory requires a reviewed reservation protocol and outage policy before enablement.

Use WooCommerce APIs and signed webhooks for standard operations. Add a small companion plugin for checkout/reservation coordination if standard interfaces cannot enforce the agreed policy. Inspect the separate WordPress repository before choosing plugin placement or changing existing behavior.

### Overseas receiving

Record supplier/reference, expected products and native units, shipment status, and receiving destination. Keep expected or overseas-in-transit supply separate from locally received and saleable inventory.

Support partial arrivals, shortages, excess items, damaged/held goods, and separate confirmed designs. Review scanned quantities and set-to-box conversions before posting. Retrying one receipt must not add the shipment again. Remaining expected supply stays visible until received or explicitly closed with a reason.

### Daily restock

Use floor request → actual picking/dispatch → actual floor receipt. Picked units leave available back stock and remain assigned to that delivery until received, returned, or otherwise resolved. Partial fulfillment remains visible.

Restocking preserves total physical merchandise across source, transit, and destination. It creates no purchase receipt or sale. Opening a set during picking must record its conversion and preserve the remaining boxes and any protected opened-set identity.

## Reliability requirements

- Store authenticated incoming notifications durably before acknowledging acceptance; process external requests outside inventory write transactions.
- Reuse the inventory service through explicit, store-scoped integration authorization. Do not fabricate a human JWT or grant a connector unrestricted administrator access.
- Identify each external business transaction independently of its notification deliveries; fetch current source facts when needed and process changes without repeating prior effects.
- Keep durable retry state and perform periodic source reconciliation to recover missed notifications. Expose last successful synchronization, backlog, unmapped items, unresolved allocations, and mismatches.
- Publish availability only after local commit, using ordering/version safeguards and protection against feedback loops.
- Surface stale connections. Define online checkout behavior during outages instead of promising instantaneous accuracy across disconnected services.
- Establish reviewed opening counts and a cutover boundary so importing old Clover history cannot deduct sales already reflected in those counts.
- Preserve history and perform reviewed forward corrections. Verify backups and recovery with connector checkpoints and queued work included.

## Approaches and recommendation

1. **Connect the existing POPCORE ledger directly — recommended.** Reuse the transaction foundation and add the missing Clover/WooCommerce connections and operational rules. This preserves the blind-box/set model while making POPCORE the authority.
2. **Adopt an external inventory platform.** This changes the authority and requires evaluating its support for sets, confirmed designs, dedicated online stock, and store replenishment. No suitability or feature claim is made without that evaluation.
3. **Use pairwise Clover/WooCommerce quantity synchronization.** This may exchange channel numbers but cannot by itself represent overseas receiving, storage-to-floor movement, or opened-set provenance. It does not satisfy the stated scope.

## Delivery sequence and evidence

1. Confirm Clover recording practice and merchant/store identities; inspect representative item-level sales and catalog data through an authorized connection or provided export. Establish product/location/opening-count mappings and verify the receiving/restock cycle on isolated data.
2. Build and validate the Clover connection, including retries, amendments, delayed transactions, and source reconciliation. Compare against a representative store day before enabling real deductions.
3. Implement dedicated online allocation, WooCommerce order/reservation coordination, fulfillment, and availability publication. Verify the complete online order cycle in staging before online launch.
4. Run a combined store/online pilot and recovery exercise. Enable real stock posting only with reviewed opening counts and reconciled channel boundaries.

These are sequencing steps within one scope; WooCommerce is not an optional future feature.

Acceptance requires runnable checks demonstrating:

- Receiving 10 sealed 12-box sets adds 10 sets once, including after retry. Receiving only 8 leaves 2 expected and unavailable.
- Opening 1 set and moving its 12 boxes to the floor leaves 9 sealed sets in back stock and 12 boxes on the floor, after delivery receipt.
- A Clover sale of 3 mapped floor boxes leaves 9; duplicate notifications, multiple tenders, and reconciliation do not deduct again.
- Five confirmed pieces allocated exclusively online remain physically counted once and unavailable to ordinary floor replenishment. One online order reserves one; shipment removes it physically once and leaves four.
- Cancellation before shipment releases the reservation once. Refund after shipment creates no physical return automatically.
- An online order arriving between a stock read and stock publication cannot be erased by the publication.
- Selecting e-transfer, WeChat Pay, or Alipay in Clover exposes the exact order/payment on an authorized phone without re-entry; cash creates no photo task. Two simultaneous equal-value orders remain distinguishable. Tender cancellation, retries, and evidence uploads neither misattach a photo nor deduct inventory again.
- Checkout completes without a photo, but an order with any non-card, non-cash payment remains flagged until its required evidence is saved. A failed upload does not clear the flag; adding evidence later does not repost the sale. Card/cash-only orders are exempt.
- Clover prefills the suggested discounted payable amount; staff can edit it before payment completion. The final confirmed amount is identical in Clover, the linked phone view, and POPCORE's sale record, with the original suggestion retained separately.
- A delayed Clover sale crossing cutover, an unknown product, a stale count, and an interrupted connection produce explicit outcomes rather than silent balance corruption.
- Movement totals reconcile to balances, and restored data retains pending connector work without duplicate stock effects.

## Decisions still needed

The intended workflow records every in-store tender through Clover. Confirm current recording coverage before importing or cutting over historical data. Photo upload may follow checkout with mandatory missing-evidence flags, and staff may override the prefilled discount suggestion; both are confirmed. The exact formula generating that suggestion remains to be confirmed.

Owner update, September 12: Downtown uses a Clover Station Duo, and employees sign in with individual accounts. The supplied screenshot shows the Clover merchant App Market for POPCORE LTD. It does not establish the subscribed software plan, Duo generation, custom-app installation permission, developer account readiness, or the Markham merchant setup.

Preserve the Clover cashier identity separately from the employee who later uploads evidence on the phone. Individual Clover sign-ins do not automatically establish a mapping to POPCORE/Auth0 users or prove that the current API integration can retrieve the active cashier; validate that mapping during the test flow.

Before implementing account-specific behavior: Downtown software plan and device generation where relevant, Markham device and merchant-account relationship, developer app access, representative order/item structure, and required Clover-side checkout actions.

Before online stock publication: actual WooCommerce site/staging configuration, location of online-only merchandise, fulfillment locations, and checkout reservation/outage policy.

## Primary documentation checked

- Clover web integrations: https://docs.clover.com/dev/docs/clover-development-basics-web-app
- Clover OAuth: https://docs.clover.com/dev/docs/use-oauth
- Clover merchant notifications: https://docs.clover.com/dev/docs/webhooks
- Clover custom-tender app: https://docs.clover.com/dev/docs/creating-custom-tender-apps
- Clover tender-selection limitation: https://docs.clover.com/dev/docs/general-faqs
- WooCommerce webhooks and delivery failures: https://developer.woocommerce.com/docs/best-practices/urls-and-routing/webhooks/

Documentation establishes available integration mechanisms. It does not verify POPCORE's merchant permissions, device workflows, live WooCommerce configuration, or production synchronization.
