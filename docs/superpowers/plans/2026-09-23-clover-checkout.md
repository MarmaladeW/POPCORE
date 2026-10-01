# Clover sandbox checkout Implementation Plan

> Execute inline. User explicitly requests implementation now, with all testing deferred until we test together. Do not commit, publish, or deploy in this turn.

**Goal:** Use the existing phone checkout for user-operated Android Clover sandbox orders at Midtown, and reduce redundant navigation.

**Architecture:** Option 1 only. A disabled-by-default authenticated adapter reads the existing isolated Clover sandbox probe. It keeps order snapshots, cashier ownership and evidence in a separate private SQLite file, never the production sales/inventory tables. Existing checkout components display the sandbox queue and automatically follow Clover payment results.

**Tech Stack:** Existing Flask, SQLite, requests, React/TypeScript and Ant Design. No new dependencies.

## Scope and implementation

1. Add `popcore_app/blueprints/clover_sandbox.py` and register it in `app.py`. Expose access, queue, detail, claim and private evidence endpoints. Share existing shift/role checks, use an explicitly configured store ID, and restrict the upstream to the existing loopback sandbox probe. Keep the probe password server-side. Reject production source responses.
2. Adapt `pages/Sales/Checkout.tsx` to an explicit sandbox source, preserving the normal manual workflow. Poll without overlapping/aborting slow requests; aim for updates within three seconds, report freshness rather than promise provider latency. Never automatically select newly arrived orders. Claim when tapped; derive completion only from successful Clover payments. Keep tender pricing, split guidance, photos and history in the existing screen. Never substitute Check for custom tenders. Do not offer local completion/refund/cancellation on Clover orders.
3. Simplify `components/AppLayout.tsx`: short single-language navigation labels, five daily links (Home, Checkout, Receive goods, Claw machine, Summary), grouped Inventory and More; retain special orders, tasks and trades. History remains inside checkout, closing inside summary; keep manual sale entry reachable as an explicit fallback. Preserve existing routes and permissions.
4. Add a runbook for disabled-by-default configuration, Midtown mapping, bounded feed limitations, private data and cutover cleanup. All sandbox database contents, including evidence, are disposable. Do not delete real data or execute cleanup now.

## Deferred verification (with user)

- Android-created order appears in Midtown queue; measure device-to-phone latency rather than equating the polling interval with latency.
- A new queue entry does not interrupt the order in progress or photo preview.
- E-transfer/custom tenders, taxable discount, card and split totals; only successful Clover payments finish checkout.
- Photo attachment/read/retry, same-order history, cashier ownership, shift changes and permission denial.
- Provider timeout, malformed payload, late payment, first load, unknown tender and orders outside the upstream latest-20 window. Never interpret a missing order as cancelled.
- Navigate every regrouped destination on phone and desktop, including direct links and history selection.
- Confirm production sales/stock untouched; run existing regression checks and frontend build only when testing is authorized.

## Decisions

- The existing probe returns its latest 20 modified orders. This is a single-device rehearsal adapter, not production ingestion or a historical import. Retain observed orders locally and explicitly show older snapshots as stale. Production needs durable webhook reconciliation separately.
- All test execution, build verification and live transactions are deferred at the user's request. Source review only in this turn; implementation must not be described as tested or deployed.

## Implementation record

- Adapter, source-aware checkout, private evidence/history, grouped navigation and deployment/cutover runbook added locally on `codex/clover-sandbox-checkout`, based on released main `b7fb814c`.
- Existing isolated probe source incorporated from the earlier Clover worktree without changing that worktree; only payment-detail parallelization changed.
- Independent read-only review found browser freshness could survive failed/hung requests. Added local five-second expiry and bounded reads, retaining the last snapshot and unsaved photo. No tests run to verify the correction.
- A small offline check was authored for the later testing session, not executed. No commit, push, release build, deployment, source transaction or cleanup performed.
