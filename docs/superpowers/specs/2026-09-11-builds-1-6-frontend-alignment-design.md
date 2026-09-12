# Builds 1–6 frontend alignment design

Date: 2026-09-11. Status: proposed for review; no application changes authorized by this document alone.

## Outcome

Make the existing POPCORE operations frontend a coherent, understandable interface for the capabilities delivered in Builds 1–6. Staff should see what needs doing, understand the effect of an action, and resume real documents. Managers should be able to review exceptions and close a day without interpreting database fields.

This is an application redesign and completion of existing frontend connections, not a marketing site or a replacement backend. Reuse React, Ant Design, existing API clients, and current routes. No new UI framework, state framework, component library, or animation dependency.

## Evidence and baseline

- Inspected checkout: `/Users/marmalale/Developer/POPCORE`, branch `codex/builds-1-6`, HEAD `97c123a427c2b01c9e39bfffd76efb54ea3dd34f`. `origin` is `https://github.com/MarmaladeW/POPCORE.git`. Verify the current integration branch again before implementation.
- No tracked source changes were present at inspection. The PDF and both September 9 frontend proposal files are existing untracked user work; preserve them.
- Reuses the useful design direction from `docs/superpowers/specs/2026-09-09-frontend-usability-design.md`, but not its old execution baseline or restrictions specific to that earlier session.
- `PRODUCT.md` contains useful visual/accessibility principles but is Schedule-centric. Operations requirements below govern Today, stock, sales, goods, trades, and closing; Schedule's own requirements stay intact.
- Current source was inspected. September 9 screenshots in `.local/frontend-review/20260909-051143/` were also inspected as historical visual evidence, not a fresh live-site audit.
- `popcore_app/static/index.html` still references `index-CxILAsyJ.js`. Vite's production output is the intentionally committed `popcore_app/static/` directory. Source verification builds use a separate output directory and do not update that release artifact. An intentional release build is part of the eventual handoff.

## Users and visual direction

Staff use phones on the shop floor; managers also use tablets and desktops. The interface should feel calm, legible, practical, and recognizably POPCORE.

- Light operations shell: background `#F7F8FA`, white surfaces, primary text `#20242D`, secondary text `#596273`, borders `#DDE2EA`, action accent `#4F46E5`. Verify contrast in actual rendered states.
- System font with Chinese fallbacks; body 14–16px, page titles about 24px, section headings 16–18px, supporting text at least 12px. Do not introduce a font download.
- Spacing scale 4/8/12/16/24/32px; mobile page padding 16px, desktop 24px; restrained 8px corners. Forms 720–960px wide; data views can use available width.
- Lists, dividers, and section headings before extra cards. One clear primary action per workflow state; secondary and destructive actions separated.
- Readable status text plus restrained color; never rely on color alone. Keep product photos and useful bilingual names, not decorative artwork.
- Native links/buttons, persistent field labels, visible focus, roughly 44px touch controls, reduced motion, and browser zoom. Test 390/768/1440px and 200% zoom; at narrow effective widths, reflow page content and contain necessary table scrolling.
- Scope operations styling and theme overrides by route. Preserve Schedule source, layout, colors, calendar behavior, and its existing theme. Shared semantic/zoom fixes require Schedule regression checks.

## Information architecture

Desktop navigation groups existing destinations:

1. Daily work: Today, Inventory, Restock, Enter sale, Closing, Trades.
2. Review: Sales and Reports for managers.
3. Planning and administration: Schedule for existing authorized roles; Products, Settings, and Users retain their existing access rules.

Mobile retains Today / Inventory / Sales / Schedule / More. Sales leads staff to sale entry and managers to the sales overview. More contains the remaining authorized destinations. Do not hide Enter sale from managers.

Child routes select the correct parent: `/goods/*` belongs to Inventory; sale documents/evidence belong to Sales; condition cases belong to Trades. Navigation, document headers, and the store selector must make scope visible. A resumed document's store is authoritative; never silently relabel it with the currently selected store.

## Build-to-interface contract

| Build | Frontend outcome | Rules that must survive |
| --- | --- | --- |
| 1 — safe foundation | Consistent shell, accessible controls, useful loading/empty/error/denied states, safe retry messages | Authentication and role checks; errors never masquerade as empty data; completed history remains protected |
| 2 — inventory core | Clear verified product identity, native unit, location, disposition, mode and opening-readiness information | Box/set/piece remain separate; no opening command or invented stock; preserve versions and opened-set provenance |
| 3 — goods | Understandable receiving, transfers, restock, and count screens; document resume and lifecycle progress | Actual received quantities, transit ownership, discrepancy evidence, manager review and stale-count protection |
| 4 — store day | Dollar-based sale/payment forms, real review summaries, separate evidence/payment decisions, dependable closing | Exact cents at API boundary; unknown is not zero; refunds are not stock returns; source-token checks; immutable closed snapshot |
| 5 — trades and Today | Personal shifts first; actionable work list; guided inspection/swap/replacement and condition cases | Personal shifts independent of store filter; same-series verified designs, proof, explicit replacement, current slot/balance versions |
| 6 — operations and release | Nine readable reports with working filters/paging/export; closed-day review; verified release bundle | Server-scoped totals and manager permissions; incomplete totals marked; no fabricated links, aggregation, or production-readiness claims |

## Screen requirements

### Today and browsing

Keep My shifts first, across all of the person's stores. Then show their resumable work and authorized store work, with financial review only for managers. Replace raw type/status codes and Store IDs with names and clear actions: Resume sale, Review count, Receive transfer, Continue closing. Compact empty sections into useful quiet messages. Show loading, failed refresh, denied access and genuinely empty results distinctly. Do not invent urgency or counts absent from the API.

Products and Inventory prioritize name/SKU, verified identity, native unit, location and meaningful quantities. Keep legacy mode explicit. Server search/paging must not silently restrict selectors to the first 500 products. Retrieve a scanned or resumed product by ID if it is outside the search page. Separate opening-readiness information from permission and API-error states.

### Goods and restock

Use existing receipt/transfer/count detail endpoints to make the query-string resume links work. Show document status and saved line facts before actions. Receiving needs an actual review screen before posting; distinguish saved draft from posted receipt and retain the saved ID after a post failure.

Transfers show requested, dispatched, received, returned, lost and outstanding native units. Derive available actions from document state, remaining quantities and authorized store/role, not from `version > 1`. Include existing return/loss/short-close operations with their required quantities/reasons. Creation remains manager-only. Restock continues to own its delivery lifecycle; generic stock forms cannot alter delivery-owned stock.

Counts show saved observations, expected balance and discrepancies, with manager approval/return and actionable stale-count explanations. Floor suggestions use existing endpoints, remain explicitly advisory and require deliberate stock actions.

Known API boundary: count return creates a new draft, but the current count API has no edit-observation endpoint. Do not display an editable recount that cannot save, silently reuse old observations, or call this a completed editable recount flow. Surface the limitation and record a separately scoped backend prerequisite. Other frontend work does not depend on adding that API.

Two additional API boundaries: the suggestion response omits the current target version required to update an existing target, and the inspected frontend-facing routes do not provide a scoped opened-set choice list. Ship suggestions read-only and keep provenance-dependent actions safely blocked where the required facts are unavailable. Reliable target editing and opened-set selection need narrowly scoped read-contract additions before those UI controls can be completed; do not guess versions or IDs.

### Sales, evidence and closing

Dollar inputs are strings until exact conversion to integer cents. Blank means unknown where supported, `0.00` means known zero; reject negative values where not allowed, more than two decimals, non-decimal syntax and unsafe integer ranges. Never silently round.

Sale entry groups source/date, verified product/quantity, actual money and selected tender rows. Retain all five supported tenders and split tender capability. Review the saved draft's actual facts before recording. Separate sale-post success from payment-save success and support retrying only the unconfirmed step. No payment-processing behavior is implied.

Sale review keeps stock allocation, payment decisions, evidence decisions, monetary refunds, and physical returns separate. Each decision has its own reason. Select verified products instead of typing database IDs; required open-set provenance remains a clearly labelled prerequisite until an authorized API can supply the choice. Evidence previews use authenticated requests and short-lived object URLs; no public upload URLs or persisted private files.

Closing uses sections for source completeness, cash events/count, blockers, and review. Do not infer five-step completion from the existence of a cash count. Keep the fixed $650 bill float visible; opening/retained coins and denomination counts are deliberate inputs, not defaulted evidence. Restore saved counts when resuming. Refresh versions/source tokens after mutations and require re-review on stale sources. Closed sessions are read-only; managers see the saved snapshot and later adjustments separately, staff see only their authorized facts.

### Trades and reports

Trades separate slot selection, inspection, confirm swap, sale link and explicit replacement. Fetch the chosen sale's actual version for review instead of asking staff to type it. Preserve proof, condition, box/accessory checks and same-series identity. Request keys belong to a stable user intent, not a new API attempt. Condition cases remain distinct from refund/return approval.

Reports use explicit per-report columns, money formatting, date filters where meaningful, server pagination, scoped summary facts and safe record links. Current inventory is not a historical-date report. Export uses the same non-pagination filters as the screen and explains the 500-row limit. Avoid rendering `snapshot_json` or arbitrary object keys directly. A failed store/filter change must not leave another scope's results presented as current.

## Scope and delivery boundaries

In scope: existing routes/components, scoped theme/CSS, frontend API wrappers for existing endpoints, focused regression tests, frontend documentation, and preparation of an intentional release bundle after source acceptance.

Out of scope: Schedule redesign, inventory/schema/business-rule changes, new permission models, real opening balances, live data import, offline queues, automatic payment/refund execution, backend recount/target-version/opened-set read-contract changes without separate authorization, hosting migration, or broad refactoring.

Delivery A is an independently reviewable operations shell + Today/browsing + Reports. Delivery B completes the existing transactional UI and release verification. This is not another six-build backend rewrite.

Planning does not authorize commits, pushes, merges, production deployment, or data changes. During implementation, use isolated synthetic/disposable data. An eventual deployment needs an explicit release decision and rollback instructions.

## Acceptance

- Each row of the build-to-interface table has verified UI evidence and identified limitations.
- Actual task flows work on phone/tablet/desktop, including keyboard, zoom, long bilingual content, direct links and back navigation.
- Role/store changes and late responses do not disclose stale scoped data or carry an old draft into a new store.
- Unknown money, ambiguous scans, partial failures, 403/409 responses and closed records have truthful actionable states.
- Existing tests remain meaningful; add focused failures before fixes rather than merely changing snapshots/selectors to make checks green.
- The real disposable pilot verifies data effects, not only screenshots. It must distinguish its tested scope from live Auth0/device/opening-stock/recovery gates.
- The release artifact is built from the accepted source, inspected for test authentication/private data, served locally for smoke checks, and only deployed with authorization.
