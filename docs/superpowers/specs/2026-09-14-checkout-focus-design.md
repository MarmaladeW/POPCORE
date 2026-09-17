# Checkout as the primary staff workspace

Approved through owner discussion on September 14, 2026. Implement locally in codex/popcore-checkout; no commit, deployment, live account change or real money movement.

## Experience

Remove Today from app navigation and redirect the staff home to Checkout; retain Schedule for shifts. Today/catalog analysis is outside this project. A cashier uses their own POPCORE login on their phone while operating Clover. Usually one register, sometimes two Downtown registers with different customers, one cashier at a time. The current order dominates. Order switching shows register label, total and item preview; incoming refreshes must not silently switch the selected order or erase editing/photo state.

Show actual methods Cash, Card, E-transfer, WeChat Pay, Alipay. For non-card methods suggest a whole-dollar payable around 2–3% below the original tax-inclusive total, editable by staff. Accepted examples: 4134→4000, 712→700, 9914→9700, 2000→1900 cents. An original whole-dollar total may round down farther. A discount greater than 20% warns without requiring approval or blocking a valid positive amount. Exactly 20% does not warn. Keep original subtotal, tax, customer pays and pre-tax discount distinct.

POPCORE gives a pre-tax discount estimate to enter manually into Clover; electronic alternative methods are entered as Cash in Clover but preserve their real method in POPCORE. Do not imply any processor integration exists. Exact tax applicability/rounding and current-register order discovery cannot be verified until Clover works. The calculator must be explicitly an estimate based on supplied original totals. Do not rewrite captured money based on this estimate: require the authoritative order's payable amount to match the chosen target before offering received-payment posting. While Clover is disconnected, unmatched targets explicitly await confirmed Clover totals. Test this with isolated synthetic orders, never publicly seeded fake orders.

For E-transfer, WeChat Pay and Alipay: prominent Take photo, preview retaining order and amount, Use photo / Retake. Use photo uploads immediately, reports success, and returns to the same order. Cash/card skip evidence. A photo is not payment verification. Missing evidence remains visible after payment; refunds and abandoned checkout resolution are secondary manager tools using the verified backend. Keep split payments supported without exposing backend 'attempt' terminology as normal UI copy. No new daily cashier sessions or takeover workflows.

## Permission policy

Use Toronto calendar date, not shift hours. Assigned shifts (not availability) at a location grant staff/manager live checkout access for that whole day. Staff history is own orders only, including off-duty days. Manager history is all staff orders at manager's location(s) assigned today, not locations on the historical order date. Admin can read live/history at all locations and dates without shift requirement. Staff and managers require an active employee profile and authenticated individual identity. Authenticated admins do not require a Schedule employee profile to read orders. Simple cashier attribution records the individual processing the order; viewing must never rewrite attribution. Preserve immutable original attribution and separately attributed payment/refund/photo actions.

Enforce policy in backend queues, direct checkout reads, photos, mutations and linked checkout-origin sale/payment/evidence/history paths. Shared Clover intern login is source metadata only; never guess which person from a shift. Do not weaken inventory posting safeguards or change unrelated operational permissions. New live orders cannot be processed off-duty; own historical records remain readable. Related payment/refund writes still require the appropriate roles and current location access. Reject stale/revoked access on replay. Clear frontend sensitive state on 401/403, user/store changes and calendar-date refresh.

## Appearance and accessibility

Existing restrained indigo/white POPCORE palette, system font, broad readable figures, clear named actions, 44px+ touch targets and visible focus. Desktop and phone structures, prominent persistent Checkout navigation, unobtrusive connection state. No decorative charts, carousel gestures or extra cashier assignment screens. Large photo preview with clear save/retry state. Errors preserve unsent evidence and edits without attaching them to another order.

## Verification and scope

Backend role/date/location/ownership/replay tests, arithmetic examples and boundaries, existing suite, TypeScript/build to private output, phone/desktop keyboard and browser workflow checks, actual API evidence upload/authorization where practical. Review code independently. No changes to Schedule behavior, production assets, dependencies, environments or Website code. No claim of live Auth0, Clover/device or production verification.
