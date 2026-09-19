# Checkout backend verification — 2026-09-14

Scope: verify and harden the existing native checkout backend before further frontend changes. Work remains local on `codex/popcore-checkout`. No frontend files, production data, deployments, commits or pushes changed during this verification pass.

## Fixes from reproducible failures

- Invalid tender objects/lists and oversized path IDs now return client errors without writing records. Checkout references must be text.
- Opened-set selection is checked for existence, product, store and applicability before a checkout can accept money. Simultaneous opened-set and fresh-set choices are rejected.
- Public manual sale creation/update, source linking and payment entry cannot claim the `popcore_checkout` source namespace. Native finalization owns those order/payment IDs.
- Existing sale lines and open checkout snapshots count as catalog references. A referenced, already-typed product cannot be retyped by marking it unverified first; previously untyped legacy products can still be classified.
- Sale allocation checks both the sold product and its selected sealed-set source/conversion against the captured identity. Historical/imported drift retains the paid financial sale with `product_mapping_required`, without deducting stock.

## Verified behavior

- Simultaneous retry of the same received-payment request changes the checkout once. Competing finalizers produce one sale, one payment and one stock deduction.
- Failure while attaching evidence after stock posting rolls back sale, payment and stock together. Received payment attempts and their staged photos survive for a retry.
- Insufficient stock preserves finance and exposes pending allocation as a closing blocker.
- Access revocation blocks previous assignments, private photo reads and upload replays. Anonymous and viewer access is denied.
- Snapshot backup/restore preserves request keys; retry after restore does not duplicate money or stock.
- Refund recording does not restore inventory. A separately reviewed physical return does.
- Independent fresh-set HTTP verification opened one 12-box set, sold seven boxes and retained five protected boxes with clean foreign keys.
- The actual Flask application starts on a disposable database, registers checkout routes, denies anonymous requests and reruns migrations safely. External HTTP was blocked during this check.
- Dependency consistency and the release recovery check passed. Recovery checked database integrity, foreign keys, snapshot preservation and referenced attachments, including rejection of corrupt/missing files.

The initial audit passed 423 backend tests, including 19 checkout tests. The completed refund workflow now passes **432 backend tests**, including **28 checkout tests**. The full-suite log is `.local/refunds-backend-suite.log`; focused checks are in `.local/refunds-final-focused.log`. Dependency consistency, actual Flask startup with anonymous refund-route denial, repeat migrations, and release recovery passed. Recovery evidence is in `.local/refunds-recovery.log`.

## Abandoned checkout refunds completed

- A scoped manager marks paid, unfinalized checkouts abandoned with a reason. Pending attempts cancel; further collection/finalization stops.
- Each append-only refund records its original attempt/tender, bounded cents, actual date, unique store refund reference, reason, manager and explicit confirmation of original receipt and actual refund. All received money returned automatically cancels the order without a sale or stock movement.
- Repeated requests, competing refund requests, conflicting keys, duplicate external references, stale versions, cross-checkout attempts, staff/downgraded roles and revoked access are covered. Injected failure after refund insertion rolls back the refund, cancellation and request key together.
- Cash/electronic separation, same-day and later-day refunds, closing blockers and stale counts, tender reports, and signed snapshots on both receipt and refund dates are covered through the API. Late entries preserve both snapshots and create dated adjustments.
- Migration from an existing paid checkout preserves the original attempts. Backup/restore preserves partially refunded balances, replay keys and staged private evidence; the remaining refund can complete after restore.
- Independent code review found and reproduced a missing refund/receipt tender total in signed closing snapshots. Live and snapshot totals now share the same aggregation; the regression tests pass. Final review reported no remaining blockers.

## Readiness boundary

The complete-sale workflow is verified in isolated tests. This is not live Auth0, Clover, store-device or production verification.

The abandoned partial-payment backend gap is resolved. Refunds are recorded facts of money actually returned outside POPCORE; no processor refund is initiated. See [checkout refund API](popcore-checkout.md#abandoned-checkout-refund-api) for the exact request fields and accounting rules. The checkout implementation is included on `codex/staff-home`; no deployment was performed.

The subsequent checkout interface pass is implemented and verified locally. The expanded location/day/private-history policy brings the full backend suite to **444 passed**, including **40 checkout/access tests** (`.local/checkout-access-review-full.log`). Staff see only their own history; managers are limited by today's assigned locations; admin reads need no Schedule profile. Linked sale/payment/evidence/report/closing paths share the policy, and closing writes recheck under the transaction before replay.

The new browser check exercises real local Flask photo upload, payment, finalization, history and abandoned-checkout refund with a disposable database. It verifies exactly one sale/stock deduction and no sale/stock effect for cancellation after refund. Frontend has 31 passing tests, a passing separate build and passing checkout/foundation browser checks. See [checkout workspace](popcore-checkout.md) for the interface and current provider limitations. These checks do not establish live Clover/Auth0/device or production readiness.
