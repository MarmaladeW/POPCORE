# POPCORE frontend usability and visual design

Status: proposed for user review; planning only. This does not authorize implementation, commits, pushes or deployment.

## Goal and evidence

Make the existing in-store website elegant, minimalistic and easy to use for store staff and managers. Preserve operational truth, role permissions and the existing Schedule experience.

The 9 September source review and synthetic screenshots are at `.local/frontend-review/20260909-051143/`. They show unreadable mobile Reports columns, large empty Today cards, cents-based money entry, numeric product-ID allocation and crowded review forms. This design supplements `2026-09-07-instore-website-design.md`; its inventory, payment, evidence, closing and role rules remain authoritative.

Assumption: staff use phones on a lit shop floor; managers use desktop and tablet for review. Use a light workspace with readable contrast. This is a redesign of existing frontend presentation and task flow, not a new feature platform.

## Chosen approach

Use the existing Ant Design controls, React routes and operation APIs. Refine their theme and compose clearer screens. A theme-only refresh would leave the difficult forms unchanged; replacing Ant Design would expand regression risk without solving additional store needs.

Two deliveries:
1. Shared visual foundation, navigation, Today, Reports and read-only product/inventory browsing.
2. Sale entry/review, payment evidence, Closing, goods/trade forms and measured loading improvements.

## Visual language

- Neutral background `#F7F8FA`, white surfaces, primary text `#20242D`, secondary text `#596273`, border `#DDE2EA`, restrained indigo action/selection color `#4F46E5`. Verify rendered contrast rather than relying on these seed values alone.
- One existing system sans-serif stack with appropriate Chinese fallbacks. Body 14-16px; page title 24px; section title 16-18px; supporting labels at least 12px. Avoid light-gray operational text and all-caps decoration.
- Spacing scale 4/8/12/16/24/32px. Mobile page padding 16px; desktop 24px. Forms use a readable 720-960px maximum width; data screens use the available width.
- Subtle 1px borders, consistent 8px corners and minimal shadows. Cards separate genuinely different tasks; empty states do not need a full-size card.
- On non-Schedule pages, use a light neutral sidebar/header/bottom bar with solid brand mark and understated active selection. Use accent color for actions and selection; retain semantic status colors plus text.
- Desktop controls at least 36px high, primary/coarse-pointer targets at least 44px. Real labels remain visible after typing; placeholders are examples, not labels.
- Motion only communicates state, approximately 150-200ms with reduced-motion support. No decorative page entrances, new animation library or dark-mode project.
- Scope new tokens and CSS to non-Schedule routes. Schedule keeps its existing theme values, calendar CSS, employee colors, shell appearance and interaction layout. Shared semantic navigation improvements may apply if visuals and behavior remain equivalent. Global zoom restriction removal is an accessibility correction and must pass Schedule checks.

## Navigation and Today

- Preserve route URLs and existing role gating. Use Inventory consistently for `/stock` outside the preserved Schedule shell; keep catalog Products distinct from physical stock.
- Desktop separates daily work (Today, Inventory, Restock, Enter Sale, Closing, Trades) from manager review (Sales, Reports). Keep Schedule visible and Settings admin-only. No empty or unauthorized groups.
- Mobile retains Today, Inventory, Sales, Schedule, More for staff/management with the existing role-specific Sales destination. Products, Restock, Closing, Trades, Reports and Settings remain available under More where permitted. Do not add a second shortcut bar.
- Active navigation must recognize child routes: receiving/transfers/counts belong to Inventory; sale review belongs to Sales. Preserve browser Back and query-string resume links.
- Today keeps personal shifts first for staff and other eligible roles. Show today's assignments followed by the next upcoming assignment; a selected operational store never hides another-store personal shift.
- Below shifts, show permitted blocking work, work due today and useful status using facts actually returned by Today. Do not infer urgency, cash totals, assignment or ownership absent from the payload.
- Use task labels such as “Finish sale #41” and readable store names from authorized context, not “sale 41 / Store 1”. Unknown names have an honest fallback. Preserve document IDs in secondary text for traceability.
- Collapse zero-item sections into a compact “No outstanding store work” line, separately from loading, failed and no-access states. Manager exceptions remain visible ahead of empty status.

## Reports and browsing

- Define display columns per report, with human labels, native units, formatted recorded currency and meaningful status text. Hide storage versions from the main table; keep useful document references.
- Desktop uses deliberate column widths and local table scrolling. Mobile uses compact rows for common inventory/sales/tender facts; exceptionally wide movement data can use a readable horizontal table. Never force every field into the viewport.
- Add visible From/To controls for dated reports, Apply/Clear, controlled server pagination and filtered CSV export. Inventory is current stock: label it “Current stock” and do not imply a historical balance by applying date controls.
- Table requests use `page`, `page_size`, `store_code`, `from`, `to`. Export uses the same store/date/report selection but no page restriction; retain the server's existing 500-row export limit and tell users to narrow filters when exceeded.
- Show known monetary subtotals only when the API supplies them. Unknown amounts, incomplete counts, quantities by native unit, refunds and original closing facts remain distinct. Never manufacture a total from one displayed page.
- Links resolve by document type and actual existing routes. A product ID must not accidentally link to a sale because the row also contains `sale_id`. No invented destination or private attachment path.
- Products/Inventory prioritize search or scan, readable item identity/unit/location and one primary task; less-used import/edit operations remain accessible in a secondary menu with existing permission checks.

## Transactions

- Present currency in dollars, including tax, tender, refund and opening/retained coins; convert decimal strings exactly to safe integer cents at the API boundary. Blank means unknown/missing according to the existing field contract; explicitly entered zero remains zero. Reject excess decimal places and values outside the existing permitted range instead of silently rounding.
- Sale Entry groups receipt/date, item/quantity and payment. Begin with the payment method the user selects; expose additional methods through “Add payment method”. Do not preselect a method or invent money values. All five existing tenders and split payments remain supported.
- Retain the saved-draft -> review -> record sequence. Show confirmation using server-returned facts. Do not add payment collection, POS integration, multi-item sales functionality or pricing logic under this visual plan.
- Sale Review shows recorded items/amounts, pending payment/evidence work and the current primary action first. Source reconciliation, refund and physical return are separately labelled expandable actions. Keep individual review reasons, histories and permitted evidence access. Use existing authenticated file retrieval if available; if it is absent, identify the exact endpoint gap rather than simulating a preview.
- Replace manual allocation product IDs with a scoped verified-product search showing SKU, name and unit; preserve mapping/version checks and any explicit set selection. Never auto-resolve an ambiguous identity.
- Closing shows real progress through sales/evidence, goods/counts, cash and review. Progress comes from returned facts, not a hardcoded step counter or a button click. Unavailable completion evidence stays unverified.
- Keep a compact blocker list with links to the exact supported source documents. Open the active section and keep other sections reachable; do not mount/unmount in a way that discards typed cash counts.
- Cash shows denomination counts, opening coins, retained coins, the existing $650 bill float and API-returned reconciliation. Zero counts require deliberate entry. Keep paid-in/out, refund and cash removal distinct; no inferred events.
- Closed days render read-only saved facts and separately labelled later adjustments. Staff can resume returned drafts; managers decide each exception and return-for-changes with a reason. No layout change removes hard blockers.
- Receiving, transfers, restock, counts and trades use consistent store/location context, product identity, native-unit quantity, plain-language state, focused action and confirmation. Preserve scanner semantics, partial quantities, provenance, trade proof and separate condition/refund decisions.

## Reliability and completion

Clear previous-identity/store data on context changes, ignore delayed old responses, and disable actions while identity or facts are unresolved. Preserve typed work on recoverable failures. Keep request keys stable for retries of the same immutable intent; a new amount/decision creates a new intent after unresolved previous requests are reconciled. No optimistic stock/payment/closing success.

Success requires 390/768/1440px review, 200% zoom, keyboard navigation, long bilingual text, all four roles, no-access/empty/error/stale states and actual readable report text. Test touched operations through real services in the disposable pilot where supported. Existing mocked UI tests alone do not prove complete store-day integration. Physical shop devices and real Auth0 remain separate checks.

Out of scope: Schedule redesign, new roles, new business operations, backend schema changes, droplet data, external integrations, a new UI framework, broad refactors and release deployment. Previously recorded Build 6 integration/Linux gaps remain open until separately verified; this frontend plan must not relabel them complete.
