# Special Orders Design

## Goal

Add a dedicated Special Orders workspace for customer-requested items that are
different from ordinary checkout sales. It records who created the order, the
customer request, separate payments, and the final customer handoff without
creating inventory or regular-sale records.

## Workflow

1. An authenticated staff member creates a special order with:
   - customer name;
   - phone number;
   - free-text item description;
   - total price;
   - amount paid now, including zero;
   - creator and creation time taken from the signed-in user and server clock.
2. Later payments are appended as separate dated entries. Existing payments
   are not replaced when another payment is received.
3. POPCORE calculates amount paid and remaining balance from the payment rows.
4. The order cannot be completed while any balance remains.
5. Completion means the customer has received the item. POPCORE records the
   signed-in completing employee and server time.
6. Completed orders remain available in history.

## Page

Add `/special-orders` as a dedicated staff page and navigation entry. The page
has Open and Completed views, a compact order list, and one selected-order
detail area. It follows the approved demo:

- New special order opens an inline form.
- Each order shows customer name, item, creator, creation date, and payment
  state.
- Detail shows total, paid amount, remaining balance, and separate payment
  entries.
- Add payment is available while the order is open and a balance remains.
- Mark customer received is disabled until the balance is zero.
- Completed detail shows completion date and completing employee.
- Admin correction controls are shown only to admins.

The layout uses the existing POPCORE operations theme, familiar native form
controls, visible focus, and touch targets suitable for staff phones. No store
selector affects special-order data because special orders are not store-bound.

## Data Model

### `special_orders`

- `id` integer primary key
- `customer_name` required text
- `customer_phone` required text
- `item_description` required text
- `total_cents` positive integer
- `status` constrained to `open` or `completed`
- `created_by` required authenticated subject
- `created_at` required timestamp
- `completed_by` nullable authenticated subject
- `completed_at` nullable timestamp
- `version` positive integer for concurrent-write checks

The completion fields must either both be null for an open order or both be
present for a completed order.

### `special_order_payments`

- `id` integer primary key
- `special_order_id` required foreign key
- `amount_cents` positive integer
- `paid_at` required timestamp

The order creator remains the visible employee attribution. Payment entries do
not replace the creator.

## Permissions and Phone Privacy

- Authenticated staff, managers, and admins may list special orders, create an
  order, add payments, and complete a fully paid order.
- Managers and admins always receive the customer phone number.
- Staff receive the phone number only when their active employee record has a
  shift dated today in Toronto whose trimmed position is `Cashier`, compared
  case-insensitively. Shift hours and store do not affect this phone access.
- The original creator receives no phone exception unless they are today's
  cashier.
- For unauthorized staff, the API omits or nulls the phone field. Browser-only
  hiding is insufficient.
- Only admins may correct `created_by`, `created_at`, `completed_by`, or
  `completed_at`. Corrections must preserve the open/completed field pairing.

## Server Operations

Use one small Flask blueprint and an operations module following existing
POPCORE transaction, validation, authentication, and idempotency patterns.

- `GET /api/special-orders?status=open|completed` lists orders and calculated
  payment totals with the server-enforced phone projection.
- `POST /api/special-orders` creates an order and optional initial payment in
  one transaction.
- `POST /api/special-orders/{id}/payments` appends one payment.
- `POST /api/special-orders/{id}/complete` records the customer handoff.
- `PATCH /api/special-orders/{id}` applies admin-only attribution/date
  corrections.

Money is accepted and stored as integer cents. Mutations require an
idempotency key and expected version where an existing order is changed.
Within the same write transaction, the server must:

- reject empty customer, phone, or item values;
- reject invalid or nonpositive totals;
- reject negative initial payment;
- reject zero or negative later payments;
- reject any payment that would make total paid exceed the order total;
- reject payment changes to completed orders;
- reject completion unless the sum of payments exactly equals the total;
- reject stale expected versions.

The server derives creator, completer, and ordinary timestamps rather than
trusting client-supplied identity or time. Only the admin correction action may
change those fields.

## Failure Behaviour

- A rejected or failed payment leaves the previous order and payment rows
  unchanged.
- A retried mutation with the same idempotency key and body returns the first
  result without duplicating a payment or completion.
- A stale screen receives a conflict response and refreshes before retrying.
- Forms preserve entered values after correctable validation or network errors.
- Phone access is recalculated for every request so a Schedule change takes
  effect without copying permissions into special-order records.

## Verification

Add focused backend tests proving:

- creation records the signed-in creator and optional initial payment;
- later payments remain separate;
- zero/negative payments and overpayment are rejected atomically;
- an unpaid order cannot be completed;
- a fully paid order records the signed-in completer and completion time;
- a retry does not duplicate a payment or completion;
- stale versions conflict;
- managers/admins receive phone numbers;
- only staff assigned the Cashier position today in Toronto receive phone
  numbers, regardless of store or shift hours;
- creators without today's Cashier position do not receive phone numbers;
- admin corrections work and non-admin corrections are rejected.

Add one focused frontend test for money/balance presentation and one browser
check covering create, add payment, blocked completion, paid completion,
completed history, responsive layout, and phone masking.

Run the repository's existing backend suite, frontend tests, separate frontend
build, `git diff --check`, and final status inspection. These local checks do
not prove production Auth0, devices, or deployed behavior.

## Explicitly Excluded

- Store assignment
- Catalog or inventory linkage
- Regular checkout, sale, closing, or Clover integration
- Payment method tracking
- Refunds, cancellation, or deletion
- Photos or document uploads
- Customer notifications
