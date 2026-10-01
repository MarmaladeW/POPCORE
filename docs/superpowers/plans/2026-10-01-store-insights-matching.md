# Store insights and historical matching implementation plan

Goal: make POPCORE's daily operations, historical sales review, and management reporting one coherent workflow without inventing sales facts or rewriting inventory history.

Architecture: reuse the existing reports, daily_sales, checkout lifecycle, scoped store access, inventory ledger, matcher, and request-key mutation hook. New overview reads aggregate existing facts; a separate manager review explicitly remaps an entire saved historical row with an immutable audit. No automatic bulk remapping, new external integration, production data access, commit, push or deploy.

- [x] Reports overview: bounded business-date range, posted sales and their money completeness, checkout lifecycle coverage, separate pasted-report coverage/trend/top products, current inventory exceptions and links. Verify source separation, scope, missing days versus zero, immutable labels/units and unknown opening stock.
- [x] Historical name matching: scoped paged search, original raw names and notes, exact current identity and review-only candidate details; explicit reason and whole-row confirmation. Transactional remap uses source and target tokens, request key and audit. Reject stale reads, stock-linked history, incompatible known units and target collisions. Preserve original quantities, notes and price snapshot.
- [x] Shared parser: identity qualifiers in notes (packaging, generation, size, hidden variants) must constrain exact matches. Human-reviewed exact choices remain narrowly scoped; fuzzy similarity never confirms identity.
- [x] Historical Sales and day detail: snapshot-price estimates with unknown coverage, honest dated chart, explicit row Save/Cancel, scope-safe requests and drafts, stable product search and accessible alias management.
- [x] App integration: direct links from manager overview/navigation to insights, reports and past-name review; readable product/location names in reports. Preserve phone staff workflows and Schedule behavior.
- [x] Verify focused backend/browser regressions, full backend suite, frontend tests and isolated production build; update local sample demo and docs.

Review focus: no dual-counting finalized checkout plus its sale document; no catalogue price fallback masquerading as old revenue; no missing-day zero; no silent merge of aggregated names or stock units; no stale user/store data or pending request edits. Test authentication and disposable SQLite prove local logic only, not live providers or production opening counts.


Verified locally (2026-10-01): 606 backend tests, 38 frontend tests, isolated production build, historical-sales browser checks (390/1440), real-Flask insight and historical-remap browser checks (390/1440), store-day reports, role/navigation, foundation and Schedule browser checks. The remap browser test loses the first committed response and confirms an identical-key retry produces one audit with unchanged original facts and stock.

Independent review corrections: report period preservation; search field/history synchronization; current product labels alongside original historical names; confirmed-design identity validation; top-20 selection per native unit; explicit confirmed-design note markers. Calendar loading now follows the existing lazy-route pattern: the main JavaScript chunk fell from 1,329.33 kB (404.42 kB gzip) to 475.67 kB (145.68 kB gzip). A pre-existing browser coordinate assumption was replaced with actual full visibility and unobstructed hit-testing after eager/lazy A/B evidence showed identical behavior; Schedule source is unchanged.

Disposable preview: http://127.0.0.1:5192/reports?from=2026-09-01&to=2026-09-30 . Existing inventory sample data preserved; explicitly synthetic past-report and posted-sale examples added. No production records accessed or corrected. No commit, push, deployment, release-asset regeneration or dependency changes.
