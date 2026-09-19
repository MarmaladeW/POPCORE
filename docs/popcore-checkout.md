# Native POPCORE checkout

The `/checkout` workspace is the 买单 entry from the four-option staff home at `/`. It connects per-order cashier work, payment photos and the existing sale/stock/closing flow. Home, Checkout and Schedule remain directly accessible on mobile; Order history and the previous Today workspace (Store tasks) remain in navigation. See [Staff home](staff-home.md) for the September 16 entrance and event-recording flow.

## Use

The queue uses today's permitted locations and identifies each order by register (when supplied), amount, items and reference. The cashier selects the customer's order; background refresh never switches that selection. Each order displays its original individual POPCORE cashier. The application does not use the shared Clover intern login as a personal identity.

Customer pays is prominent and editable. POPCORE suggests a whole-dollar amount near a 2.5% reduction, including the approved $20 to $19 exception. Discounts above 20% of the original tax-inclusive total produce a warning, without requiring manager approval. Card uses the recorded total. The pre-tax Clover discount is explicitly an estimate from the original subtotal and tax; differing targets wait for a confirmed Clover total and cannot overwrite the captured payment total.

Staff choose the actual payment method. E-transfer, WeChat Pay and Alipay provide a prominent phone camera action: take photo, preview, Use photo or Retake. Use photo uploads immediately to that exact order/payment. Cash and card require no photo. A photo can arrive after payment and does not verify receipt. Per-payment photo drafts and edited amounts survive switching orders and recoverable refresh failures. Ambiguous writes retain their original request key/body for Retry; switching stores, orders or history dates is disabled until resolved. Access denial clears sensitive details and drafts.

The cashier or an authorized manager explicitly records money actually received. Split payments remain separate. Once the received total matches the order, the screen finalizes the existing sale/payment/stock flow. Failed finalization retains received money and offers retry; it never records the receipt again. Managers can expand the secondary cancel/refund section to abandon a partially paid checkout and record actual refunds. Entirely unpaid checkouts can be cancelled by their authorized cashier.

The compatibility form at `/checkout/new` still creates native orders using exact catalog items and captured prices/tax/discount. It is not a substitute for the pending Clover order feed. Captured totals/items are immutable; cancel an entirely unpaid native checkout and create a replacement to correct them. Unpaid orders do not reserve stock. An inventory shortage preserves the financial sale with pending allocation for manager resolution.

## Access

- Staff: live orders at locations with an assigned shift today, for the entire Toronto calendar day. Availability alone grants no access. Order history contains only their own orders and stays available off duty.
- Managers: live orders and staff history only at their locations assigned today, including when looking at historical dates.
- Admin: all active locations and historical dates for reads, without requiring a Schedule profile or shift. Existing inventory and financial write guards still apply.
- These checks run on the server, including linked sale/payment/evidence/report/closing paths and transactional rechecks before replay or mutation. Viewing an order never changes its cashier. Assigned photo helpers cannot use assignment to read another employee's completed history.

## Boundaries

- Clover is deliberately disconnected. No provider credentials, simulator actor picker, public fake webhook, or provider verification bypass is included in the application. Real Clover authorization, order mapping, webhook processing and reconciliation remain separate follow-up work after access is restored. A verified connector must use exact merchant/order/payment identities and reconcile with existing POPCORE sales before posting.
- Clover item-level tax applicability and rounding are unverified. The pre-tax estimate is not a confirmed provider amount; there is no hardcoded assumption that all items have Ontario tax.
- A partially collected checkout requires a scoped manager to mark it abandoned, then record actual refunds against the original payments. Full repayment automatically cancels it. Original receipts and evidence remain intact; cancellation creates no sale and moves no stock. Ordinary refunds and separate physical returns remain available after a completed sale.
- The visible queue refreshes every 15 seconds and on window focus, pausing during pending writes. It lists open orders, including older unresolved orders, in pages of 100. History supports date filtering and pagination. These refreshes read POPCORE; they are not a working Clover feed.
- Photo files use the existing private normalized image storage. Staged and cancelled-attempt images are included in backup/restore; only completed-attempt images attach to sale payments. A photo never verifies payment. Existing review permissions remain in force.
- The checkout implementation is included on `codex/staff-home`. It has not been deployed or built into the committed production bundle.

## Abandoned checkout refund API

This backend flow records refunds already made outside POPCORE. It does not send money or call Clover. Secondary manager controls in the checkout screen submit this flow.

1. `POST /api/checkouts/{id}/abandon` with `expected_version` and a nonempty `reason`. Requires a manager with inventory access to the checkout store. It cancels pending attempts and stops further collection/finalization; original completed receipts remain unchanged. Entirely unpaid orders still use `cancel`.
2. `POST /api/checkouts/{id}/refund` with the latest `expected_version`, original completed `attempt_id`, positive integer `amount_cents`, actual `business_date`, a store-unique refund `reference` (1–120 characters), `reason`, and `confirmed_received_and_refunded: true`. The manager must check both the original receipt and the refund actually made. The tender is inherited from that original attempt. The refund date must be on or after the original checkout business date and no later than today in Toronto.
3. Both actions require an `Idempotency-Key`. Reuse the same key and identical body for a retry. A different refund needs a new key and external reference. Refunds cannot exceed the unrefunded amount of their exact payment attempt. The checkout cancels automatically after all received money has been refunded.

Checkout detail returns abandonment reason/actor/time, refund history, `refunded_cents`, `refund_due_cents`, each attempt's `refundable_cents`, and manager `can_refund`. `received_cents` remains the gross receipt history; `remaining_cents` remains the original purchase balance. Use `refund_due_cents` for abandoned checkout work. Existing attempt evidence can be added before final cancellation and remains readable afterward and through backup/restore.

Receipts retain the checkout business date; refunds use their actual refund date. Manager confirmation verifies that original receipt for closing. Cash drawer totals include only cash; tender reports retain gross receipts and refunds separately. An unfinished abandonment blocks closing on its original date and any date with refunds against it. Changes invalidate affected closing source tokens and cash counts; entries recorded after closing create late adjustments while the signed snapshot remains unchanged. A refund-day float shortage still requires replenishment through the existing cash workflow before closing.

## Verification (2026-09-14)

- Backend: **444 unittest cases passed**, including **40 checkout/access cases**. Tests cover money/stock idempotency, refund lifecycle, backup/recovery, private history, current-day location scope, linked read/write boundaries and revocation while waiting for a closing write lock. The full log is `.local/checkout-access-review-full.log`.
- Frontend: **31 Node tests passed**, including seven integer-cent pricing checks. The separate production-mode build passed without changing committed static assets. Existing large-chunk warnings remain.
- Checkout browser: mobile/desktop navigation, slow order switching, independent payment photo drafts, retake, 503 upload retry with the same request key, transient-refresh preservation, off-duty history and access-denial clearing passed. Screenshots are under `.local/checkout-focus/browser`.
- Actual local Flask/browser: real multipart upload, receipt, automatic finalization, historical reopening and manager abandonment/refund passed using disposable data and test authentication. Database assertions verified one sale, the exact received amount and one stock deduction; abandoned refund created no extra sale or stock movement.
- Shared foundation browser checks passed, including authentication failures, navigation, store switching and Schedule. The mobile Inventory assertion now opens More because Checkout and Order history occupy the primary navigation.
- Independent reviews cleared the identified access, rounding and async-state findings. `git diff --check` passed.

Repeat backend checks: `python -m unittest discover -s popcore_app/tests -v`. From `popcore_app/frontend`, run `npm test` and `npm run build -- --outDir ../../.local/frontend-build`. Run `python popcore_app/tests/browser/check_checkout_focus.py` with the configured Playwright browser cache; it uses disposable localhost ports 5174, 5177 and 5057. Never deploy the test authentication.

Clover authorization, real merchant/order/payment mapping, live register synchronization, exact provider discount behavior, real Auth0 and phone-camera hardware remain unverified. No production data, commit, push, deployment or release-asset rebuild was performed. See [backend verification](backend-checkout-verification.md) for earlier audit and recovery evidence.
