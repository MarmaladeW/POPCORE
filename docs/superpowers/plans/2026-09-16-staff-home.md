# Staff home implementation plan

Goal: implement the approved 买单 / 入店 / 娃娃机 / 汇总 entrance, with punch-in until successful, and connect the existing checkout.

Base: codex/catalog-sales-rework at 32b7a9cf. Checkout prerequisite: copy the uncommitted implementation from popcore-checkout without modifying that workspace. Preserve Schedule, catalog work, release assets, environments. The initial implementation scope excluded commit, push and deployment; the later push request authorizes publishing this development branch.

## Task 1: Integrate checkout prerequisite
- [x] Apply checkout backend and screens with existing tests, preserving the newer Schedule and attendance.
- [x] Register checkout routes without redirecting home. Verify checkout, permissions and pricing tests.

## Task 2: Store event recording and summary API
- [x] Add isolated tests first for persisted claw prizes/replenishment/display arrivals and cash exchange, exact retries, invalid inputs, actor/date/store authorization, summary privacy and cash effect.
- [x] Add a small store_events table with actor, Toronto date, timestamp, kind, product snapshot/quantity or tender/amount, note and optional cash_event_id. Reuse operation_requests idempotency and cash_events payout for cash exchange, in one transaction. No merchandise revenue or stock posting for these records: claw/display mappings are unreviewed. Closed-day new writes are rejected.
- [x] GET /api/store-events?store_id=&business_date= returns {business_date, scope, events, checkouts, receipts, transfers, summary_text}. Staff sees own events/checkouts; managers assigned today and admins see store summary. Include completed checkouts in POS/non-POS groups once by tender; split orders form a separate group to avoid duplication. Existing historical import is linked separately, never reimport this generated summary.
- [x] POST /api/store-events accepts {store_id,kind,product_id,quantity,note} for claw_prize/claw_refill/display_in; or {store_id,kind:cash_exchange,tender:card|e_transfer,amount_cents,note}. Require todays assigned store or admin; cash posting also requires existing inventory access. Return created event. Validate all fields, no client attribution/date injection.

## Task 3: Home and connected screens
- [x] Add browser regression for home links, punch-in persistence, viewer restrictions, navigation, event recording return-to-home, errors/retry, store change and mobile layout.
- [x] Home uses PunchIn(home), current store/date, four named entry links, small own activity list; accessible home navigation retained on all screens.
- [x] 买单 routes existing Checkout; 入店 links receipts and transfers and display recording; 娃娃机 offers 出奖/补货/换现金 with immediate saved confirmation; 汇总 provides current records/copyable summary, closing link and manager historical-import link.
- [x] Keep standard system typography, indigo accent, clear focus, 44px touch targets, narrow-phone and desktop layout. Gate mutations on a selected real store. Do not change permission policy to make flows available.

## Task 4: Verification and review
- [x] Run full backend and frontend tests, separate production-mode build, existing attendance/checkout and new browser checks. Inspect phone/desktop screenshots. Review new API permission/cash boundaries and UI flow. Check diff whitespace and unchanged release artifacts.
