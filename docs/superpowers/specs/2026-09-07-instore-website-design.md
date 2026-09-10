# POPCORE in-store website: product design and requirements

**Date:** 2026-09-07
**Status:** Proposed design for review; no implementation authorized by this document.
**Repository:** D:/dev/POPCORE — MarmaladeW/POPCORE
**Baseline inspected:** main, 8630199ac780059f5cb185a85c6efd0ac4597b91
**Boundary:** popcore.store is the internal Flask/React application. popcore.ca is the separate WooCommerce sales channel. Schedule is outside the redesign.

## 1. Product decision

Build POPCORE around a complete store day: know what is physically present, receive and move goods, record what was sold and how it was paid, replenish the floor, and close with explainable totals.

The central product promise is: **every quantity and payment total can be traced to the operation that created it, and each operation is recorded once.** The home page should lead staff to unfinished work. Product, stock, sales, payment evidence, restocking, and closing must share identities and status rather than behave as separate spreadsheets.

Keep Flask, SQLite, React, Auth0, and the existing component system. Evolve the working application in phases. Do not introduce another platform, a separate inventory microservice, LocalWP, an automated accounting system, or a speculative generic workflow builder.

## 2. Sources and what counts as a requirement

1. Owner's operational statements in [Store Operations Overview](https://chatgpt.com/share/6a9f8309-c888-83ea-a3bb-f3bbe4a7e519), read directly from ChatGPT.
2. Current code in this checkout, including product sync, stock, restock, inventory checks, sales import, authentication, and frontend routes.
3. [Overall audit](../../audits/2026-09-07-overall-project-audit.md), including its evidence limitations.
4. Existing PRODUCT.md, AGENTS.md, and Windows development instructions.

The shared conversation contains owner statements, assistant suggestions, and pasted older technical reports. Only the owner's confirmed statements are treated as existing business policy. Assistant-generated discount algorithms, specific refund options, stock allocations, and wholesale terms are proposals, not approved rules. Older source descriptions must be checked against current code.

Current-source corrections relevant to this plan:

- Google Sheet sync already uses stored `sheet_ref`, column C name matching, conflict buckets, and an explicit fetch status. Preserve these improvements; do not rebuild the older B-only matcher described in the chat.
- The existing catalog also contains bookkeeping names, aliases, pricing, pack-size information, and images. The owner's description that Clover holds product entry identifies an operational source, not permission to discard this catalog.
- Restock session deletion is currently permitted to staff, not just managers. The audit's affected-role description is too narrow.
- Runtime uploads live under `popcore_app/uploads/hidden_imgs`; `/hidden_imgs` is a serving route.
- Production files are `popcore_app/nginx.conf`, `popcore_app/popcore.service`, `popcore_app/backup.sh`, `popcore_app/gunicorn.conf.py`, and root `setup_production.sh`; there is no `deployment/` directory.
- Use the Flask CLI with explicit `--env-file` from AGENTS.md. Direct `python app.py` does not load the local environment file.
- This planning pass rechecked source and Git, not the full test suite. The audit's 10 backend tests, 14 frontend tests, and build result remain historical evidence.

## 3. Confirmed store requirements

| Area | Owner's stated operation | Website requirement |
| --- | --- | --- |
| Stores | Downtown and Markham; a third location is possible | Configure real locations; do not treat seeded Midtown as operational without confirmation |
| Storage | Downtown floor/upstairs; Markham floor/warehouse | Distinct physical balances; every receipt, move, count, and sale names its location |
| Merchandise | Random blind boxes, larger designer figures, confirmed designs; sets commonly contain 6/9/12 boxes | Series hierarchy, clear selling/stock units, confirmed-design quantities, and packaging conversion |
| Receiving | Shipments can go to either location; confirmed designs arrive labeled; staff use notation such as 12*1 | Scan or enter quantities with a clear unit preview; record each confirmed design separately |
| Shelf practice | One customer selection display and an opened set below it used for replenishment; occasional loose singles/mixing | Distinguish the customer tray from the replenishment set; mark mixed/unknown provenance |
| Larger purchases | Staff can open a fresh set when a customer buys more than half its contents | Explicit fresh-set allocation, not an automatic guarantee triggered by quantity |
| Barcodes | Clover uses manufacturer barcodes; confirmed designs share the generic series barcode | Internal POPCORE identifiers for ambiguous variants; never infer a confirmed design from the generic code |
| Sales | Card through Clover; substantial cash/e-transfer/WeChat Pay/Alipay activity | Separate payment methods, original order references, and reconciliation; retain manual report intake during transition |
| Payment adjustment | Card has no payment discount; alternative methods receive a variable post-tax whole-dollar reduction | Record actual amounts, discount, rounding, and tax independently; approve the algorithm before automation |
| Evidence | Payment screenshots help resolve later order problems | Mobile evidence capture attached to a payment; access protected, pending evidence visible |
| Closing | After customers leave: floor request, upstairs picking, floor replenishment, till reconciliation, hot-item checks | One store/business-date closing session with parallel tasks, individual actors, and final review |
| Cash | Begin with $650 in bills; retain $650 in bills at close; count remaining bills and coins | Separate float, cash sales, opening coins, payouts, counted cash, removal, and variance |
| Trading | One trading option per series, same-series swap, original box/accessories and acceptable condition; sticker/receipt proof | One active trade slot per store/series is the proposed physical interpretation; record incoming/outgoing items and inspection |
| Trade lifecycle | Outgoing trade item remains eligible; open a new box at launch or after direct sale of the trading item | Reusable eligibility history; explicit replacement conversion only when physically opened |
| Confirmed/defect policy | Confirmed purchases are not tradeable; ordinary sales are final; rare undisclosed defects resolved case by case | Differentiate trade from retail return; record condition disclosure and manager-handled exceptional cases |
| Long-term inventory | Reports, scan-to-count, product management, min/max, multiple locations/channels, vendors, wholesale | Roadmap retains these requirements with sequencing; no unsupported purchasing/credit rules |
| Systems | popcore.store should become inventory authority; Clover remains POS; popcore.ca is online store | One inventory posting authority with stable channel mappings and duplicate prevention |

The shelf description means two roles—customer selection and replenishment supply. It does not justify modeling every loose blind box as a uniquely serialized collectible. Detailed item identity is needed for confirmed/trading/condition-specific units and provenance-sensitive allocations, not every ordinary sale.

## 4. What staff see

**Physical scene:** Staff use a bright retail floor, a phone while picking/photographing evidence, and a desktop at the till, often interrupted by customers. Use readable light work surfaces, the existing indigo action color and dark navigation, system typography, and obvious status text.

Primary navigation:

| Section | Main job | Reuse/change |
| --- | --- | --- |
| Today | See the selected store, open day, pending work, and next action | Evolve Dashboard from mostly metrics to actionable queues |
| Products | Search/scan products; see series, designs, units, images, prices, and identifiers | Evolve existing Products |
| Inventory | On-hand by location; Receive, Move, Restock, Count, and History | Unify existing Stock/Restock/Inventory surfaces under clear tasks |
| Sales | View/record sales, separate tenders, attach evidence, reconcile imports | Evolve existing Sales; keep staff entry separate from manager reporting |
| Closing | Complete today's restock, payment checks, cash reconciliation, counts, and sign-off | New connected closing workspace using existing restock/count components |
| Schedule | Existing scheduling experience | Preserve route, pages, interactions, permissions, exports, and employee settings |
| More / Management | Reports, trades, later purchasing/wholesale, access, settings, integrations | Show only available and authorized features |

On mobile, keep five main entries: Today, Inventory, Sales, Schedule, More. Closing has a prominent action on Today and an entry under More. Products are searchable from Inventory and More. Receiving/counting screens have a large context-specific Scan field; a scan never silently chooses between selling and receiving.

Every non-scheduling work screen shares:

- Explicit store and physical-location context; All Stores is read-only.
- Business date where relevant, pending/saved/posted state, actor, and last-updated information.
- Product identity using image, name/记账名, form/design, and unit—not a name alone.
- Visible loading, empty, failure, stale, no-access, validation, and conflict states.
- One primary action for the current step; bulk actions show item and unit totals before posting.
- Keyboard/scanner entry, approximately 44px primary touch targets, visible focus, descriptive labels, and reflow/local table scrolling at 200% zoom.
- Chinese operational terms such as 入店, 补货, 盘点, 汇总 alongside understandable English labels.
- Server-confirmed posting. A dropped connection does not mean success or authorize an automatic duplicate submission.

Preserve Schedule by scoping new layouts/styles to store-operation pages. Keep existing Schedule components and calendar CSS unchanged. Shared login/API/build changes require Schedule regression checks; do not fix its separate audit findings incidentally.

### Today highlights by role

**Added requirement, 2026-09-08:** Today must show different highlights for different roles. The role-specific experience is confirmed; the content below is the proposed default for review.

Keep the existing viewer, staff, manager, and admin roles. Use four defined views of the same underlying operations, not four independently maintained dashboards or a configurable widget builder. Do not introduce an Owner role merely to label this page.

| Existing role | Prioritized Today highlights | Visibility boundary |
| --- | --- | --- |
| Viewer | Read-only product/stock notices from data already permitted, and shortcuts to accessible pages | No operational write actions, daily revenue, cash discrepancies, payment evidence, costs, or management approvals |
| Staff | **Their assigned shifts first:** today's assignments and the next upcoming shift, with date, start/end time, store, and position when assigned; then restock requests/picking/receiving, counts due, their incomplete sale/evidence entries, and closing steps | Show amounts/details needed for an authorized task; omit management revenue summaries, other staff's private evidence, cost/margin data, and cross-store management comparisons |
| Manager | Authorized-store exceptions and progress: sales/tender summary, missing payment evidence, cash over/short, unreceived restocks/transfers, stock discrepancies, pending corrections, and closing approvals | Limit records and drill-downs to managed stores and permitted actions; purchasing costs/margins still require the separate approved cost-access policy |
| Admin | Overview across authorized stores: closing completion, important sales/stock exceptions and comparisons, unresolved identity mappings, and integration failures once integrations exist | Cross-store totals require explicit scope; technical/admin status does not automatically grant cost/margin access or make unknown financial data reliable |

Cashier, picker, and receiver are work responsibilities within Staff, not new Auth0 roles in this plan. Where existing workflow records identify an assignee or actor, prioritize that person's work; show unassigned store work as unassigned rather than inventing assignments.

For Staff, keep **My Shifts** as the first and most prominent Today highlight. Below it, order operational highlights by: blocking exceptions → actions due today → useful status. Other roles retain that operational ordering. Each actionable highlight links to the exact permitted document/step and shows its store, status, count, and last update where useful. Completed work becomes quiet. A view with nothing pending says that explicitly; a failed load is an error, not a zero count.

Enforce visibility when generating Today responses on the server. The frontend must not receive restricted totals, counts, evidence links, or records and merely hide their cards. Derive role from verified identity and store scope from approved operations permissions; a client-supplied role or All Stores selection cannot expand access. All Stores aggregates only authorized locations.

Refresh the view and discard prior sensitive data when the signed-in user, role, or store scope changes. Any cache is scoped to user/role/authorized stores/date. Deep-link destinations enforce their own permissions. The owner's 2026-09-08 clarification explicitly includes staff's own assigned-shift highlights on Today. Display existing Schedule information without changing Schedule itself.

**Staff shift highlight requirements:**

- Show today's assigned shift(s) and the next upcoming assignment. Include the actual date, start/end time, store/location, and assigned position when present; use the store's local date/time consistently.
- If there is no shift today, say "No shift today" and show the next assignment. If no future shift is assigned, say "No upcoming shifts assigned". A failed or stale fetch must not look like an unassigned day.
- Reuse the existing getMyShifts helper in frontend/src/pages/Schedule/scheduleApi.ts and GET /api/schedule/shifts/me, which scopes results to the authenticated employee. Query store_code=ALL for this personal highlight so selecting Downtown for operations does not hide their Markham assignment. This is access to their own shifts, not cross-store access to other staff or operational records.
- Provide "View Schedule" linking to the existing /schedule page. Do not add shift editing, availability entry, a second calendar, or duplicated schedule storage to Today.
- Refresh on load and window focus so assignment changes are picked up without introducing a live-notification service. Clear prior-user shift data on account changes and label stale/error states with a retry action.
- Verify fixtures for a shift today, next shift on another date/store, no shifts, changed assignment after refresh, timezone day boundary, failed request, and account switching. Another employee's shifts must never appear in the highlight or its responses.

**Acceptance:** Using viewer/staff/manager/admin fixtures for DT and MK, prove different highlights and ordering, permitted actions, denial of unauthorized totals/evidence in the raw API response, correct All Stores aggregation, and no data carried over after account/role/store changes. Confirm ordinary staff can still see information necessary to complete their own authorized task.


## 5. Inventory model that matches the merchandise

### Product identities and units

Keep existing product IDs wherever they represent real stock items. Add a clear series relationship and a form/design classification:

- Random blind box.
- Factory-sealed set of a defined size.
- Confirmed design.
- Ordinary non-blind merchandise.
- Condition-specific or trade unit where individual identity is necessary.

A series is an umbrella, not a bundle. Do not implement a general bundle/BOM engine for series/design relationships. Wholesale packs and real promotional bundles can be considered only when actual examples require them.

Each SKU has one integer stock unit. A set SKU is counted in sets; a random-box SKU in boxes; a confirmed design in pieces. For a 12-box series, opening one sealed-set SKU consumes one set and creates 12 random boxes in one atomic conversion. Equivalent-unit reports use the conversion factor; they never add “1 set + 12 boxes” as 13 comparable units or count the same stock twice.

Manufacturer barcode mappings include product and pack/unit meaning. A generic series barcode cannot authorize a confirmed-design deduction. POPCORE labels identify exact variants. Unknown or ambiguous scans enter an explicit resolution step; they do not create products or guess from fuzzy similarity.

### Locations and stock state

Start with DT floor, DT upstairs, MK floor, and MK warehouse. Add in-transit ownership for actual shipped transfers. Track saleable, trade, display, damaged/hold, and other relevant dispositions separately from geography. Stock on hold, in transit, or in trade display is not ordinary retail availability.

Do not blindly migrate `claw_qty` as extra physical inventory: its existing overlap with `instore_qty` must be resolved against physical practice before cutover. Preserve legacy data and flag ambiguous balances for counting.

### Posting rules

Introduce a small internal inventory-command module using the existing SQLite database. It is the only writer for new inventory movements and their balance projection.

Each posted document records type, source lines, product, explicit unit, from/to location/state, quantity, actor, business date, posting time, unique request/source identity, and reversal/correction linkage.

Rules:

1. Validate and authorize before posting; use a short transaction with a conditional stock check/update.
2. Update balances and append movements atomically. No API/network calls while holding the write transaction.
3. Same request key and identical payload returns the prior result. The same key with different content returns conflict.
4. Posted documents are retained. Corrections append compensating entries; they do not delete history or apply best-effort clamped reversal.
5. A shortage is visible. Do not silently clamp a requested movement to zero or invent available stock.
6. A real external payment/sale is still recorded if inventory cannot be allocated. Its inventory effect remains an explicit unresolved exception; never discard financial evidence to satisfy a stock constraint.
7. Counter-sales, imports, and channel events all resolve to one source transaction. Reconciliation never posts a second deduction for the same sale.
8. Transfers conserve equivalent units across source, transit, and destination; conversions conserve defined equivalent units.
9. Physical count adjustments record observed, expected, difference, reason, reviewer, and the count's balance version.
10. Preserve operational truth during concurrency: stale document edits/close attempts conflict rather than overwrite someone else's work.

This is a transaction module inside Flask, not a distributed event-sourcing platform. Add database tables alongside the workflow that needs them.

## 6. Complete workflows

### A. Receive a shipment

1. Choose destination and shipment/reference; supplier is selectable or initially unknown.
2. Scan products and choose sets/boxes/pieces explicitly. Show “1 set × 12 = 12 equivalent boxes” before confirmation.
3. Confirmed designs arriving as A×3 and B×4 remain those separate quantities.
4. Record shortages, damaged units, and optional source notes. Purchase cost can be unknown; do not fabricate zero cost or margin.
5. Review and post receipt once. Print internal labels for ambiguous confirmed designs.
6. Open the receipt from stock history to explain the balance increase.

Keep familiar pasted quantity input as an optional shortcut with parsed unit preview. In particular, 12*1 must become exactly 12 boxes for a 12-box set, not be multiplied by 12 a second time.

### B. Replenish and move stock

Reuse pending → submitted → picking states, then distinguish picked from physically received.

- Floor staff request quantities; back-stock staff scan what they actually find.
- A picked quantity is reserved or moved into an explicit transit state—it is not simultaneously available upstairs and downstairs.
- Receiver confirms delivered quantities. Partial delivery remains visible with a disposition for leftovers/shortages.
- Complete once. Repeated taps return the same result.
- Cancel unposted requests; posted movements require a reasoned correction/reversal with sufficient source stock.
- Inter-store transfers additionally record sender, source, destination, dispatch, partial receipt, and unresolved transit stock.

Restocking targets are per product/floor location. Suggested quantity is bounded by available back stock. Purchasing alerts consider the wider supply position later; they are not the same min/max calculation.

### C. Open a set and serve the shelf

- Keep the customer selection tray distinct from the opened replenishment set below it.
- Opening an outer carton preserves individually sealed random boxes and records the packaging conversion.
- Refilling the tray from mixed sets/loose singles marks duplicate provenance as mixed/unknown.
- A purchase of more than half a set offers an explicit “Use a fresh set” action. For size 12 the threshold is 7; for size 9 it is 5.
- Only a verified fresh/unmixed source can carry a same-set statement; ordinary mixed shelf sales make no no-duplicate promise.
- Deduct the purchased quantity and account for the leftover boxes. Never manufacture a sealed set by repacking mixed singles.
- Confirmed designs supplied directly are received directly, not created by an invented unboxing event.

### D. Record sales, tenders, and evidence

Clover remains the physical POS. The eventual path is Clover order → one POPCORE sale → one inventory deduction → payment evidence/reconciliation. Before the connector is ready, provide one clearly labeled manual intake path.

Store separate records for sale, sale lines, payments, and evidence. Preserve prices/taxes at time of sale; changing the product price must not change yesterday's revenue.

Payment methods: card, cash, e-transfer, WeChat Pay, Alipay. Historical `qty_cash` cannot be retroactively separated into cash versus transfers from quantities alone; label legacy records accordingly.

For manual intake:

1. Select source, store/date, products/units, and original receipt/order reference if available.
2. Record actual payment amount and method. Any missing source tax/amount remains marked unknown.
3. Review product matches; unresolved lines cannot silently post.
4. Submit once and show its document ID.
5. Attach evidence to the payment from a phone. A screenshot is supporting evidence, not automatic proof of settled funds.
6. Show payment confirmation and evidence completion as independent states.
7. If a later Clover import matches the existing sale, link the source and reconcile; do not create another sale or stock deduction.

Keep 汇总 paste intake during transition. Operator must identify whether the data is missing sales, a source summary, or movements already recorded. A summary used to compare Clover totals is read-only reconciliation. A reviewed missing-sale entry can post once. Do not deduplicate by product/date/quantity or text hash alone: two real identical sales can occur.

**Discount decision:** Preserve the examples 41.34→40, 7.12→7, and 99.14→97 as acceptance examples. They do not uniquely determine a formula. The assistant's proposed 2%/3% chooser in the shared chat is unapproved. Record actual pre-tax subtotal, tax, post-tax total, reduction, rounding, and collected amount separately. Use integer cents/decimal calculations. Do not automatically change tax or processor totals until the store approves a deterministic policy and its accounting treatment. Manual amount recording can proceed with explicit reason/authority.

Payment images need authenticated private access, size/type validation, minimal metadata, and excluded telemetry. Do not reuse the public product-image URL convention. Use short-lived, sale-bound phone handoff if later needed; do not expose payment proof through a guessable link.

### E. Close the store day

One closing session per store and business date, after customers leave. It coordinates work already done rather than repeating inventory movements.

1. Confirm sales intake completeness and identify pending/unmatched payments.
2. Finish floor restock request, picking, and receipt. Cash counting can proceed in parallel.
3. Cashier records bills/coins counted and compares them with expected cash.
4. After final sales and floor receipt, count configured hot items; show discrepancies and require recount/reason/review.
5. Show one closing summary with tenders, amounts/tax/adjustments where known, pending evidence, cash variance, restock shortages, and count differences.
6. Staff submit; authorized manager signs off or returns it for correction.
7. Preserve the closed snapshot. Later corrections are linked dated entries; show original close plus later adjustments.

Cash definitions, in cents:

```text
opening_cash = 65000 bill float + actual opening coins
expected_drawer = opening_cash + verified cash receipts + documented paid-ins
                  - authorized cash refunds - payouts - prior removals
cash_variance = counted_closing_drawer - expected_drawer
cash_removed = counted_closing_drawer - retained_next_opening_cash
```

Cash removed is not automatically sales revenue. E-transfer/WeChat/Alipay never enter the physical drawer calculation. Record opening/retained coins explicitly; do not assume zero. Profit remains unavailable when costs are unknown.

Use a store-day posting/version check during final counts and close. If a late sale or receipt changes counted stock, invalidate the affected comparison or require recount. Do not lock a SQLite transaction while staff counts. A late external paid sale is retained as an exception even when the day is closed.

### F. Count inventory and reconcile

Opening count before authority, then hot-item/cycle counts:

- Scan in a selected physical location and explicit unit.
- Unknown barcode blocks that line, not the entire unsaved count.
- Permit reviewable draft correction and recount; preserve posted observations.
- Compute expected stock from the central balance at a captured sequence/version.
- Apply an approved difference once; do not both replace stock directly and subtract historical sales again.
- A network failure preserves the draft and shows unsent state. Do not promise offline stock commits or automatic background replay.

### G. Trading and condition cases

Treat trading as an existing in-store workflow, not a conventional return/refund feature.

- One current trading option per series per store is the proposed slot rule; reject parallel swaps against a stale slot.
- Inspect original POPCORE proof, box/accessories, series, and condition. Document legacy sticker/receipt verification; do not assume existing stickers are unique digital IDs.
- Incoming and outgoing items must be in the same series. A swap atomically changes the slot occupant and records both items.
- Preserve future eligibility of the outgoing traded item.
- Confirmed-design purchases are not eligible for this trade path.
- Opening a new random box for the initial slot or after direct purchase is an explicit conversion. Direct purchase records a sale; replacement is separate and may be pending if no stock is available.
- Record actual damaged/discounted unit condition and what was disclosed at sale.
- Exceptional defects go to a manager case with evidence and an explicit decision. Do not auto-promise a refund or design a customer self-service return portal.
- Monetary correction and physical goods disposition are distinct: a refund alone never implies stock returned.

### H. Reports, alerts, vendors, and wholesale

First useful reports: stock by location/form, movements, receiving/transfer exceptions, sales by series/design/store/tender, missing evidence, cash over/short, hot-item discrepancy, and closed-day history.

Floor min/max suggestions may use configured targets and actual back stock. Do not invent reorder forecasts, cost assumptions, or profitability dashboards.

Later vendor/purchasing workflow: supplier → purchase order → partial receipt → optional landed cost. Costs/FX/duties are recorded only when supplied and visible only to approved roles.

Later wholesale workflow: customer → reviewed quote/order → reservation → pick/dispatch → actual payment state. Price tiers, minimums, deposits, credit, and cost visibility need boss decisions. No automated credit/accounting system is included.

## 7. Permissions and integration boundaries

Retain Auth0 roles; implement explicit server enforcement for each new operation.

Proposed store-operation policy: staff work in their assigned store(s); managers review their authorized stores and adjustments; admin configures identities and integrations. Viewer product visibility and existing Schedule permissions remain as they are until specifically changed. An owner-wide override is explicit, not inferred from a UI All Stores selection.

Employee store assignments currently serve scheduling. Do not silently change their meaning. Validate the intended operations access policy and migrate access records before enforcing it; do not alter scheduling membership through inventory screens.

Clover and WooCommerce connectors arrive after the internal workflows and identity mappings work:

- Map products/variants explicitly using system/account/item IDs. Never synchronize identity by fuzzy name alone.
- Do not import Clover stock as trusted opening inventory.
- Store incoming event identities, source version, status, and reconciliation outcome durably.
- Duplicate/out-of-order events must not repeat stock effects. Queue unknown mappings and ambiguous external outcomes.
- Send outbound stock updates after commit, with retry state; keep external traffic outside inventory transactions.
- Define online allocation/reservations before enabling stock publication. Aggregate All Stores quantity is not sellable online stock.
- Account for sealed-set versus singles availability as competing access to related stock; do not publish both as independent sellable balances.
- An asynchronous connector cannot promise zero overselling during stale/offline periods. Define reservation/safety-stock and exception behavior before go-live.

Clover feasibility depends on the actual merchant account, device, plan, region, and API/app capabilities. Official documentation distinguishes supported custom-tender and partial-payment configurations; it does not prove this store's post-tax workflow can be implemented as a small plugin. Validate in sandbox first. See [custom tenders](https://docs.clover.com/dev/docs/custom-tenders) and [creating a custom tender app](https://docs.clover.com/dev/docs/creating-custom-tender-apps).

## 8. Safe migration and launch

1. Inventory all existing writers and protect current data, environments, release assets, and Schedule.
2. Reproduce and contain audited stock/sales corruption before building new UI on those paths.
3. Add product/location mappings and document/movement tables additively. Preserve existing IDs and historical reports.
4. Move every active stock-changing path to the command module, or disable/retain it read-only in the new operating mode. One overlooked import/adjustment script would break authority.
5. Compare new movement-derived balances with the compatibility projection on synthetic/shadow workflows. This checks arithmetic, not physical truth.
6. Perform approved physical opening counts by location. Unknown historic sets, tenders, and costs remain explicitly unknown; do not manufacture provenance.
7. Pilot a complete receiving-to-close day for one store, then the other. Locations not yet reconciled remain visibly unverified.
8. Enable external connectors one channel/store at a time after duplicate/retry and failure tests pass.

Pre-cutover rollback can restore the isolated migration rehearsal. After new real postings, do not blindly restore an old database or flip back to a competing writer: freeze writes, preserve new documents, reconcile, and use a forward correction/recovery procedure.

Backups must include SQLite and necessary private attachments with consistent references. Test recovery before launch; source templates alone do not prove the droplet is ready.

## 9. Decisions that can wait for their phase

| Decision | Needed before | Work that can proceed |
| --- | --- | --- |
| Exact alternative-payment discount/rounding and accounting representation | Automatic calculation/Clover tender activation | Actual-amount capture, evidence, cash arithmetic, inventory |
| Opening/retained coin routine and approval thresholds | Store closing pilot | Fields, equation, counts, review states |
| Clover device, plan, region, merchant-per-store configuration | Clover connector/tender build | Internal source documents and manual intake |
| Store-operation access scope | Authorization rollout | Policy tests, role mapping proposal, isolated fixtures |
| Online fulfillment locations/reservation rules | WooCommerce stock publication | Mapping design and sandbox/read-only comparisons |
| Supplier costs, landed cost access, wholesale pricing/credit | Procurement/wholesale activation | Receiving with unknown costs |
| Existing claw/display bucket meaning | Opening-balance cutover | New physical-location model and count tools |

These decisions are phase gates, not reasons to delay the entire plan or make the owner repeat the shared conversation.

## 10. Acceptance of a cohesive first release

A staff member can, using only synthetic data during development:

- Find the right product from a scanner or name, including a confirmed design with a generic manufacturer barcode.
- Receive one set of 12 as exactly one set/12 equivalent boxes, convert it, and explain all remaining units.
- Request five units, pick four, receive three, and find the fourth in the correct remaining/transit disposition.
- Record a sale once, keep all five tenders separate, attach evidence, and reconcile an import without another deduction.
- Close with $650 retained bills, explicit coins, and an explainable cash variance.
- Count a hot item, review a discrepancy, and post one traceable adjustment.
- Complete a same-series trade and preserve repeat eligibility without changing historical retail sales.
- Recover from invalid input, expired login, timeout, duplicate tap, and stale edit without data loss or false success.
- Use the store screens on mobile, keyboard/scanner, and 200% zoom.
- Open Schedule and observe its current experience and behavior unchanged.

The first release is complete only when these workflows work together. Passing the old scheduling tests alone is not a release gate.
