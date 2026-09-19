# Checkout Focus Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the approved accessible checkout workspace and enforce location/day/private-history permissions.
**Architecture:** Existing Flask checkout/payment/sale services stay authoritative. React provides the focused current-order workspace and a non-mutating discount estimate; disconnected Clover never fabricates confirmed totals. Use existing transactions and image storage.
**Tech Stack:** Python/Flask/SQLite, React/TypeScript, Ant Design, existing CSS and unittest/Node/Playwright.
**Spec:** docs/superpowers/specs/2026-09-14-checkout-focus-design.md

## Global Constraints

- Work only in .local/worktrees/popcore-checkout. Preserve earlier uncommitted checkout/refund/backend work. No commit, push or deployment.
- No new dependencies or production static rebuild. No Today/catalog analytics. No Schedule behavior changes.
- All live/provider behaviour remains unverified and explicitly disconnected. Estimates never mutate captured payment totals.

### Task 1: Enforce checkout permissions and readable cashier attribution

Files: checkout_operations.py, blueprints/checkout.py, related sale/payment/evidence/report access paths as needed, tests/test_checkout.py and a focused permission test file. Own backend changes; parent owns frontend/docs. Preserve LF/CRLF styles.

Interfaces: GET /api/checkouts/access returns {business_date,role,live_stores:[{id,code,name}],history_stores:[{id,code,name}]}. Staff history_stores contains locations with their own orders; managers only today's assigned locations; admin all active stores. GET /api/checkouts?store_id=N&view=live|history filters server-side, default existing compatible view subject to policy. Detail includes cashier_name, cashier_sub, can_manage, can_process, can_refund, existing immutable order and attempts. can_process requires current eligible location, open order and allowed cashier. No viewer claims ownership. Derive names from employee directory, safe fallbacks. Existing method/complete/finalize/upload/refund actions must remain available only with correct permission. Existing assigned-photo history exception cannot expose another staff member's order.

- [x] Write failing tests: staff scheduled today despite different hours can read live at that store; availability-only cannot; yesterday/future cannot; own history remains readable off-duty; other staff's completed history and photos blocked; manager historical order access determined by today's assigned store; admin all locations without shifts/inventory access for READ; assigned-photo bypass blocked; unauthorized linked sale/payment reads blocked; mutation/replay denied after shift revocation; cashier name stable on viewing. Use fixed Toronto date clock patch, synthetic employees/shifts.
- [x] Implement minimal shared policy, apply it before data reads/replays and preserve financial write safeguards. Run focused checkout/access tests and relevant inherited financial/evidence tests. Update only checkout test fixtures for today's required shifts; don't loosen real policy for tests.
- [x] Report exact API shapes and changed files to parent and independent reviewer. No frontend edits.

### Task 2: Discount estimate arithmetic

Files: frontend/src/lib/checkoutPricing.ts and checkoutPricing.test.ts only. Independent from Task 1.

Interfaces: suggestPayable(totalCents:number):number; discountQuote(subtotalCents:number,taxCents:number,targetCents:number): {discountCents:number,predictedTotalCents:number,savingsCents:number,warning:boolean}|null. Integer cents, safe nonnegative bounds; positive target <= subtotal+tax. Estimate uniform effective tax from supplied totals, no hardcoded Ontario tax assumption, no network/provider claims.

- [x] Tests cover accepted example suggestions, 2000→1900, no zero/negative suggestions for small amounts, zero-tax quotes, target at original, >20% vs exactly20%, invalid and unsafe numeric inputs. Choose whole-dollar nearest 2.5% reduction, ties downward; if this would give no reduction for a whole-dollar total, use next lower positive dollar; sub-dollar totals preserve positive cents.
- [x] Implement estimate by integer arithmetic (BigInt where needed), search neighboring integer-cent discounts to find nearest predicted payable using rounded tax and deterministic tie handling. Return estimated predicted total; callers must show mismatch and cannot claim exact Clover results.
- [x] Run node --test src/lib/checkoutPricing.test.ts and report. No package edits.

### Task 3: Focused frontend and navigation

Files: App.tsx, components/AppLayout.tsx, pages/Sales/Checkout.tsx, pages/Sales/Checkout.css; focused browser test under tests/browser/check_checkout_focus.py. Parent-owned.

- [x] Remove Today navigation and home rendering; viewer home goes to Schedule, staff home Checkout. Keep explicit /checkout and /checkout/:id. Make Checkout persistent and prominent on phone/desktop.
- [x] Fetch access before scoped queue; automatically choose single permitted store; offer My history off-duty without leaking live data. Show disconnected/waiting empty state; no production fixtures.
- [x] Order switcher identifies register or honest fallback 'POPCORE order', reference, total and items. Explicit selection, stable edits per order, scoped refresh, no silent polling switch.
- [x] Current-order surface shows cashier and actual tender choices, large editable target, separate pre-tax estimate and customer pays. Use Task 2 helpers. Do not overwrite server amount; unmatched target shows waiting for confirmed total and cannot record target as receipt. Existing received attempts retain original facts. Card bypasses discount and photo; split collection stays available as a secondary option.
- [x] Photo component has native capture input, in-page preview, Use photo immediate private upload, Retake, success and retry. Use order/attempt keyed file state; do not clear selected file on error or send to another attempt. History has limited primary actions. Managers get secondary abandon/refund inputs matching existing backend confirmation/date/reference rules.
- [x] Browser-check 390px and desktop current/empty/history/forbidden/large discount/switching/photo preview and retake/error-retry. Use existing isolated test auth configuration and synthetic API fixture only in tests. Record screenshot artifacts under .local.

### Task 4: Review and verification

- [x] Independent review of access boundaries, money estimates, photo scoping and async stale responses. Fix concrete findings.
- [x] Full backend unittest suite; frontend npm test; production-mode build --outDir ../../.local/frontend-build; git diff --check. Inspect exits/log summaries. Browser verifies original API operations where practical; no live service claim.
- [x] Update checkout docs with actual completed work and clearly marked provider limitations. Report milestone percentages as estimates, not release readiness; newly expanded backend policy is not complete until tested.
