# Special Orders Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a dedicated Special Orders page that records customer requests, separate payments, creator/completer attribution, and a fully-paid customer handoff with Schedule-based phone privacy.

**Architecture:** Add two SQLite tables and one focused Flask operations/blueprint pair. The React page consumes the API directly, reuses existing money/auth/navigation utilities, and keeps balance presentation in a small pure module for Node testing. Server responses remove phone data unless the caller is a manager/admin or is assigned today's Cashier position.

**Tech Stack:** Flask, SQLite, Python `unittest`, React 18, TypeScript, Ant Design, Node test runner, Playwright.

**Spec:** `docs/superpowers/specs/2026-09-21-special-orders-design.md`

## Global Constraints

- Special orders are not tied to a store, inventory, regular sales, closing, or Clover.
- Customer phone is returned only to managers/admins and staff assigned the `Cashier` position today in Toronto; enforce this in the API.
- Use integer cents and append-only payment rows; reject overpayment.
- Require full payment before completion and record the signed-in completer and server time.
- Only admins may correct creator/completer identities or dates.
- Reuse existing transaction, idempotency, authentication, money, theme, and Schedule patterns; add no dependency.
- Preserve all existing untracked files and committed `popcore_app/static` assets.
- Do not commit, push, or deploy without separate authorization; each task ends with a scoped diff review instead of a commit.

## File Map

- Create `popcore_app/special_order_operations.py`: validation, phone projection, reads, and transactional mutations.
- Create `popcore_app/blueprints/special_orders.py`: authenticated HTTP routes and error mapping.
- Create `popcore_app/tests/test_special_orders.py`: backend lifecycle, privacy, idempotency, concurrency, and admin tests.
- Modify `popcore_app/db.py`: one migration for the two tables and indexes.
- Modify `popcore_app/app.py`: register the blueprint.
- Modify `popcore_app/tests/support.py`: register the blueprint in isolated API tests.
- Create `popcore_app/frontend/src/pages/SpecialOrders/specialOrders.ts`: types and pure balance/status helpers.
- Create `popcore_app/frontend/src/pages/SpecialOrders/specialOrders.test.ts`: focused money/status tests.
- Create `popcore_app/frontend/src/pages/SpecialOrders/index.tsx`: responsive page and admin correction form.
- Create `popcore_app/frontend/src/pages/SpecialOrders/SpecialOrders.css`: approved POPCORE layout.
- Modify `popcore_app/frontend/package.json`: include the new test directory in `npm test`.
- Modify `popcore_app/frontend/src/App.tsx`: lazy route.
- Modify `popcore_app/frontend/src/components/AppLayout.tsx`: Daily work navigation entry.
- Create `popcore_app/tests/browser/check_special_orders.py`: mocked responsive workflow check.
- Modify `docs/staff-home.md`: identify the new Daily work destination and privacy boundary.

---

### Task 1: Backend lifecycle and privacy boundary

**Files:**
- Create: `popcore_app/tests/test_special_orders.py`
- Create: `popcore_app/special_order_operations.py`
- Create: `popcore_app/blueprints/special_orders.py`
- Modify: `popcore_app/db.py` beside `_migration_report_match_choices` and `_get_migrations()`
- Modify: `popcore_app/app.py` blueprint imports/registration
- Modify: `popcore_app/tests/support.py` blueprint imports/registration

**Interfaces:**
- Consumes: `goods_operations._begin`, `_replay`, `_remember`; `validation.read_int`; `auth.ROLE_CLAIM`, `ROLE_HIERARCHY`; `checkout_access.business_date`.
- Produces: `list_orders(con, actor, status) -> list[dict]`, `create_order(con, data, actor, request_key) -> dict`, `add_payment(con, order_id, data, actor, request_key) -> dict`, `complete_order(con, order_id, data, actor, request_key) -> dict`, and `correct_order(con, order_id, data, actor, request_key) -> dict`.

- [ ] **Step 1: Write the failing lifecycle and privacy tests**

Create `test_special_orders.py` with one fixture that seeds active employees and dated shifts, plus focused test methods. The key setup and assertions are:

```python
from contextlib import closing
from unittest.mock import patch

from support import IsolatedApiCase


class SpecialOrderTests(IsolatedApiCase):
    def setUp(self):
        super().setUp()
        self.clock = patch('special_order_operations.business_date', return_value='2026-09-21')
        self.clock.start(); self.addCleanup(self.clock.stop)
        with closing(self.connect()) as con:
            for subject, name, position in (
                ('creator', 'Creator', 'Front'),
                ('cashier', 'Cashier', ' cashier '),
                ('manager', 'Manager', 'Front'),
                ('admin', 'Admin', ''),
            ):
                employee_id = con.execute(
                    'INSERT INTO employees(auth0_id,name) VALUES (?,?)',
                    (f'auth0|{subject}', name),
                ).lastrowid
                if position:
                    con.execute(
                        """INSERT INTO shifts
                           (employee_id,date,start_time,end_time,assigned_by,store_id,position)
                           VALUES (?,'2026-09-21','00:01','00:02','test',?,?)""",
                        (employee_id, self.store_id, position),
                    )
            con.commit()

    def post(self, path, body, key, role='staff:creator'):
        return self.client.post(
            '/api/special-orders' + path,
            json=body,
            headers={**self.headers(role), 'Idempotency-Key': key},
        )

    def create_order(self, initial_paid_cents=2000):
        response = self.post('', {
            'customer_name': 'Maya Chen',
            'customer_phone': '416-555-0148',
            'item_description': 'Smiski Museum Series - The Source',
            'total_cents': 10000,
            'initial_paid_cents': initial_paid_cents,
        }, 'create-order')
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    def test_separate_payments_require_full_payment_before_completion(self):
        order = self.create_order()
        blocked = self.post(f"/{order['id']}/complete", {
            'expected_version': order['version'],
        }, 'complete-too-soon')
        self.assertEqual(blocked.status_code, 409)
        paid = self.post(f"/{order['id']}/payments", {
            'expected_version': order['version'], 'amount_cents': 8000,
        }, 'final-payment').get_json()
        self.assertEqual([p['amount_cents'] for p in paid['payments']], [2000, 8000])
        completed = self.post(f"/{order['id']}/complete", {
            'expected_version': paid['version'],
        }, 'complete-paid').get_json()
        self.assertEqual(completed['status'], 'completed')
        self.assertEqual(completed['completed_by'], 'auth0|creator')

    def test_phone_is_projected_by_role_and_todays_cashier_position(self):
        order = self.create_order()
        path = f"/api/special-orders/{order['id']}"
        self.assertIsNone(self.client.get(path, headers=self.headers('staff:creator')).get_json()['customer_phone'])
        self.assertEqual(self.client.get(path, headers=self.headers('staff:cashier')).get_json()['customer_phone'], '416-555-0148')
        self.assertEqual(self.client.get(path, headers=self.headers('manager')).get_json()['customer_phone'], '416-555-0148')
        self.assertEqual(self.client.get(path, headers=self.headers('admin')).get_json()['customer_phone'], '416-555-0148')
```

Add these named test methods in the same class, using `self.post`,
`self.create_order`, and direct database assertions:

```python
def test_zero_initial_payment_is_valid(self):
    order = self.create_order(0)
    self.assertEqual(order['payments'], [])
    self.assertEqual(order['remaining_cents'], 10000)

def test_overpayment_and_completed_payment_leave_rows_unchanged(self):
    order = self.create_order()
    before = self.snapshot(('special_orders', 'special_order_payments'))
    response = self.post(f"/{order['id']}/payments", {
        'expected_version': order['version'], 'amount_cents': 8001,
    }, 'overpayment')
    self.assertEqual(response.status_code, 400)
    self.assertEqual(self.snapshot(('special_orders', 'special_order_payments')), before)

def test_retry_is_exactly_once_and_stale_version_conflicts(self):
    order = self.create_order()
    body = {'expected_version': order['version'], 'amount_cents': 1000}
    first = self.post(f"/{order['id']}/payments", body, 'same-payment')
    replay = self.post(f"/{order['id']}/payments", body, 'same-payment')
    self.assertEqual(first.get_json(), replay.get_json())
    changed = self.post(f"/{order['id']}/payments", {
        **body, 'amount_cents': 1001,
    }, 'same-payment')
    self.assertEqual(changed.status_code, 409)
    stale = self.post(f"/{order['id']}/payments", body, 'stale-payment')
    self.assertEqual(stale.status_code, 409)

def test_admin_correction_requires_admin_and_complete_field_pairing(self):
    order = self.create_order(10000)
    body = {
        'expected_version': order['version'],
        'created_by': 'auth0|cashier',
        'created_at': '2026-09-20T14:00:00Z',
        'completed_by': None,
        'completed_at': None,
    }
    denied = self.client.patch(
        f"/api/special-orders/{order['id']}", json=body,
        headers={**self.headers('manager'), 'Idempotency-Key': 'manager-correct'},
    )
    self.assertEqual(denied.status_code, 403)
    allowed = self.client.patch(
        f"/api/special-orders/{order['id']}", json=body,
        headers={**self.headers('admin'), 'Idempotency-Key': 'admin-correct'},
    )
    self.assertEqual(allowed.status_code, 200, allowed.get_json())
```

Add one input-validation test iterating empty/non-text customer, phone, and item
values plus non-integer/unsafe totals and payments. Add one privacy test that
changes the Cashier shift to yesterday and tomorrow and asserts the phone is
null in both responses. Add one `ThreadPoolExecutor` test that posts two
payments against the same version and asserts status codes `[200, 409]`, a
payment sum of exactly the order total, and `PRAGMA foreign_key_check == []`.

- [ ] **Step 2: Run the focused tests and confirm the missing feature fails**

Run:

```bash
.venv/bin/python -m unittest popcore_app.tests.test_special_orders -v
```

Expected: import or route failure because the special-order blueprint/tables do not exist.

- [ ] **Step 3: Add the two-table migration**

Add `_migration_special_orders(con, cur)` in `db.py` and register it last:

```python
def _migration_special_orders(con, cur):
    cur.execute("""CREATE TABLE special_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_name TEXT NOT NULL CHECK(length(trim(customer_name)) BETWEEN 1 AND 120),
        customer_phone TEXT NOT NULL CHECK(length(trim(customer_phone)) BETWEEN 1 AND 80),
        item_description TEXT NOT NULL CHECK(length(trim(item_description)) BETWEEN 1 AND 500),
        total_cents INTEGER NOT NULL CHECK(total_cents > 0),
        status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','completed')),
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        completed_by TEXT,
        completed_at TEXT,
        version INTEGER NOT NULL DEFAULT 1 CHECK(version > 0),
        CHECK((status='open' AND completed_by IS NULL AND completed_at IS NULL)
           OR (status='completed' AND completed_by IS NOT NULL AND completed_at IS NOT NULL))
    )""")
    cur.execute("""CREATE TABLE special_order_payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        special_order_id INTEGER NOT NULL REFERENCES special_orders(id),
        amount_cents INTEGER NOT NULL CHECK(amount_cents > 0),
        paid_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    )""")
    cur.execute('CREATE INDEX special_orders_status ON special_orders(status,id DESC)')
    cur.execute('CREATE INDEX special_order_payments_order ON special_order_payments(special_order_id,id)')
    cur.execute("INSERT INTO _migrations(name) VALUES ('special_orders')")
```

- [ ] **Step 4: Implement the minimal operations module**

Start `special_order_operations.py` with these concrete helpers:

```python
def _text(value, field, maximum):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise InventoryValidationError(f'{field} must contain 1 to {maximum} characters')
    return value.strip()


def _admin(actor):
    return ROLE_HIERARCHY.get(actor.get(ROLE_CLAIM), 0) >= ROLE_HIERARCHY['admin']


def can_see_phone(con, actor):
    role = actor.get(ROLE_CLAIM)
    if ROLE_HIERARCHY.get(role, 0) >= ROLE_HIERARCHY['manager']:
        return True
    return con.execute("""SELECT 1 FROM employees e JOIN shifts s ON s.employee_id=e.id
        WHERE e.auth0_id=? AND e.is_active=1 AND s.date=?
        AND lower(trim(s.position))='cashier' LIMIT 1""",
        (actor.get('sub'), business_date())).fetchone() is not None
```

`can_see_phone` must compare the current verified role first, then run this Schedule check for staff:

```sql
SELECT 1
FROM employees e
JOIN shifts s ON s.employee_id=e.id
WHERE e.auth0_id=? AND e.is_active=1 AND s.date=?
  AND lower(trim(s.position))='cashier'
LIMIT 1
```

Define `order_detail(con, order_id, actor)`, `list_orders(con, actor, status)`,
`create_order(con, data, actor, request_key)`, `add_payment(con, order_id,
data, actor, request_key)`, `complete_order(con, order_id, data, actor,
request_key)`, and `correct_order(con, order_id, data, actor, request_key)`.
Every mutation must call `_begin`, `_replay`, perform validation and version
checks inside that transaction, call `_remember`, and commit; all exceptions
roll back. `order_detail` must calculate `paid_cents` from payment rows, derive
`remaining_cents`, resolve `created_by_name` and `completed_by_name` from local
`employees` rows, and set `customer_phone` to `None` when `can_see_phone` is
false.

- [ ] **Step 5: Add and register the HTTP blueprint**

Create `blueprints/special_orders.py` with this route wiring after defining the
same `body()` and error-handler pattern used by `blueprints/checkout.py`:

```python
@bp.get('/api/special-orders')
@role_required('staff')
def index():
    return jsonify(list_orders(get_db(), request.jwt_payload, request.args.get('status', 'open')))

@bp.get('/api/special-orders/<int:order_id>')
@role_required('staff')
def detail(order_id):
    return jsonify(order_detail(get_db(), order_id, request.jwt_payload))

@bp.post('/api/special-orders')
@role_required('staff')
def create():
    result = create_order(get_db(), body(), request.jwt_payload, request.headers.get('Idempotency-Key'))
    return jsonify(result), 201

@bp.post('/api/special-orders/<int:order_id>/payments')
@role_required('staff')
def payment(order_id):
    return jsonify(add_payment(get_db(), order_id, body(), request.jwt_payload, request.headers.get('Idempotency-Key')))

@bp.post('/api/special-orders/<int:order_id>/complete')
@role_required('staff')
def complete(order_id):
    return jsonify(complete_order(get_db(), order_id, body(), request.jwt_payload, request.headers.get('Idempotency-Key')))

@bp.patch('/api/special-orders/<int:order_id>')
@role_required('staff')
def correct(order_id):
    return jsonify(correct_order(get_db(), order_id, body(), request.jwt_payload, request.headers.get('Idempotency-Key')))
```

Use the checkout blueprint's `InventoryError`/`ValueError`/`PermissionError` JSON mapping. Register the blueprint in `app.py` and `tests/support.py`.

- [ ] **Step 6: Run focused tests until all lifecycle/privacy cases pass**

Run:

```bash
.venv/bin/python -m unittest popcore_app.tests.test_special_orders -v
```

Expected: all tests pass with no external requests.

- [ ] **Step 7: Review the scoped backend diff**

Run:

```bash
git diff --check -- popcore_app/db.py popcore_app/app.py popcore_app/tests/support.py popcore_app/special_order_operations.py popcore_app/blueprints/special_orders.py popcore_app/tests/test_special_orders.py
git diff --stat -- popcore_app/db.py popcore_app/app.py popcore_app/tests/support.py popcore_app/special_order_operations.py popcore_app/blueprints/special_orders.py popcore_app/tests/test_special_orders.py
```

Expected: no whitespace errors; no unrelated files changed. Do not commit.

---

### Task 2: Responsive Special Orders page

**Files:**
- Create: `popcore_app/frontend/src/pages/SpecialOrders/specialOrders.ts`
- Create: `popcore_app/frontend/src/pages/SpecialOrders/specialOrders.test.ts`
- Create: `popcore_app/frontend/src/pages/SpecialOrders/index.tsx`
- Create: `popcore_app/frontend/src/pages/SpecialOrders/SpecialOrders.css`
- Modify: `popcore_app/frontend/package.json`
- Modify: `popcore_app/frontend/src/App.tsx`

**Interfaces:**
- Consumes: the Task 1 API; `parseMoneyToCents`, `formatCents`, `useHasRole`, `client`, and the established POPCORE operations theme.
- Produces: `/special-orders`; `SpecialOrder`, `SpecialOrderPayment`, `paidCents(order)`, `remainingCents(order)`, and `paymentState(order)`.

- [ ] **Step 1: Write the failing pure presentation test**

Create `specialOrders.test.ts`:

```typescript
import assert from 'node:assert/strict'
import test from 'node:test'
import { paidCents, paymentState, remainingCents } from './specialOrders.ts'

const order = {
  id: 1, total_cents: 10000,
  payments: [{ id: 1, amount_cents: 2000, paid_at: '2026-09-21T10:00:00Z' }],
} as any

test('payment totals are derived from separate entries', () => {
  assert.equal(paidCents(order), 2000)
  assert.equal(remainingCents(order), 8000)
  assert.equal(paymentState(order), 'remaining')
  assert.equal(paymentState({ ...order, payments: [{ ...order.payments[0], amount_cents: 10000 }] }), 'paid')
})

test('unsafe or overpaid data is rejected instead of displayed', () => {
  assert.throws(() => remainingCents({ ...order, total_cents: 1.5 }), RangeError)
  assert.throws(() => remainingCents({ ...order, payments: [{ ...order.payments[0], amount_cents: 10001 }] }), RangeError)
})
```

Extend the `npm test` glob with `src/pages/SpecialOrders/*.test.ts`.

- [ ] **Step 2: Run the frontend test and confirm the missing helper fails**

Run from `popcore_app/frontend`:

```bash
npm test
```

Expected: the new module import or exports fail.

- [ ] **Step 3: Implement types and exact balance helpers**

Create `specialOrders.ts` with API-aligned types and helpers that require safe, nonnegative integer cents. `remainingCents` must throw if payments exceed the total; `paymentState` returns `'remaining'` or `'paid'`.

- [ ] **Step 4: Implement the approved page**

Create `index.tsx` with:

- Open/Completed buttons that request `GET /special-orders?status=...`.
- A selected order list/detail layout matching the approved demo.
- Inline New special order form using native phone, text, textarea, and money inputs.
- Create body `{customer_name, customer_phone, item_description, total_cents, initial_paid_cents}`.
- Add-payment body `{expected_version, amount_cents}`.
- Complete body `{expected_version}` and a disabled action until `remaining_cents === 0`.
- Phone output showing the number only when non-null; otherwise `Restricted to today's cashier and managers`.
- Admin-only correction form using `GET /schedule/employees`, employee selects, `datetime-local` inputs, and `PATCH /special-orders/{id}` with `{expected_version, created_by, created_at, completed_by, completed_at}`.
- Loading skeleton, retryable read error, inline validation errors, disabled actions during writes, and preserved form values after failure.
- One fresh UUID idempotency key per user intent. Retain it for retry when a write outcome is uncertain.

Keep state local to this page; do not add a global store or generic form framework.

- [ ] **Step 5: Add page-specific responsive CSS**

Implement the approved two-column list/detail layout above 820px and a single-column flow below it. Reuse `#4F46E5`, existing borders/backgrounds, system typography, visible focus, and roughly 44px coarse-pointer targets. Do not add decorative cards, gradients, animation libraries, or fixed-height scrolling panels.

- [ ] **Step 6: Register the lazy route**

In `App.tsx`:

```typescript
const SpecialOrdersPage=lazy(()=>import('./pages/SpecialOrders'))
```

and:

```tsx
<Route path="/special-orders" element={<RoleRoute minRole="staff" element={<SpecialOrdersPage />} />} />
```

- [ ] **Step 7: Run the focused and full frontend checks**

Run from `popcore_app/frontend`:

```bash
npm test
npm run build -- --outDir ../../.local/frontend-build
```

Expected: all Node tests pass and the separate production-mode build succeeds without changing `popcore_app/static`.

- [ ] **Step 8: Review the scoped frontend diff**

Run from repository root:

```bash
git diff --check -- popcore_app/frontend/package.json popcore_app/frontend/src/App.tsx popcore_app/frontend/src/pages/SpecialOrders
git status --short -- popcore_app/static popcore_app/frontend/package-lock.json
```

Expected: no whitespace errors and no static or lockfile changes. Do not commit.

---

### Task 3: Navigation, browser workflow, and documentation

**Files:**
- Modify: `popcore_app/frontend/src/components/AppLayout.tsx`
- Create: `popcore_app/tests/browser/check_special_orders.py`
- Modify: `docs/staff-home.md`

**Interfaces:**
- Consumes: `/special-orders` from Task 2 and the API response fields from Task 1.
- Produces: Daily work navigation access plus repeatable desktop/mobile browser evidence.

- [ ] **Step 1: Add the Daily work navigation entry**

Import `FileSearchOutlined` from `@ant-design/icons` and add directly after Checkout:

```typescript
{ key: '/special-orders', icon: <FileSearchOutlined />, label: 'Special orders' },
```

Do not add it to the three-item mobile primary bar; it remains reachable through More so the existing Home, Checkout, and Schedule priorities stay intact.

- [ ] **Step 2: Write the mocked browser workflow**

Create `check_special_orders.py` using the existing Vite/Auth0 routing harness pattern. Its synthetic API must return one partially paid order and handle create/payment/complete writes by updating in-memory state. Assert at desktop and 390px widths:

```python
await page.goto(BASE + '/special-orders')
await expect(page.get_by_role('heading', name='Special orders')).to_be_visible()
await expect(page.get_by_text('$80.00 remaining')).to_be_visible()
await expect(page.get_by_text("Restricted to today's cashier and managers")).to_be_visible()
await page.get_by_label('Payment amount').fill('80.00')
await page.get_by_role('button', name='Add payment').click()
await expect(page.get_by_text('Paid in full')).to_be_visible()
await page.get_by_role('button', name='Mark customer received').click()
await expect(page.get_by_text('Customer received the item')).to_be_visible()
assert await page.evaluate('document.body.scrollWidth') <= viewport['width'] + 2
```

Also open mobile More and assert the Special orders link is present.

- [ ] **Step 3: Run the browser check**

Run from repository root with the configured browser cache:

```bash
.venv/bin/python popcore_app/tests/browser/check_special_orders.py
```

Expected: PASS for navigation, create/payment/completion, phone masking, and responsive width. The check is local/mocked and must not claim production Auth0 proof.

- [ ] **Step 4: Update staff documentation**

Add one paragraph to `docs/staff-home.md` stating that Special orders is a separate Daily work page, is not store-bound, and exposes phone numbers only to managers/admins or today's scheduled Cashier.

- [ ] **Step 5: Review the scoped integration diff**

Run:

```bash
git diff --check -- popcore_app/frontend/src/components/AppLayout.tsx popcore_app/tests/browser/check_special_orders.py docs/staff-home.md
```

Expected: no whitespace errors. Do not commit.

---

### Task 4: Full verification and handoff

**Files:**
- Verify only; no new files unless a failing check reveals an in-scope defect.

**Interfaces:**
- Consumes: all preceding tasks.
- Produces: evidence that the feature works locally without modifying release assets.

- [ ] **Step 1: Run backend verification**

```bash
.venv/bin/python -m unittest discover -s popcore_app/tests -v
```

Expected: the complete backend suite passes, including `test_special_orders.py`.

- [ ] **Step 2: Run frontend verification**

From `popcore_app/frontend`:

```bash
npm test
npm run build -- --outDir ../../.local/frontend-build
```

Expected: tests and the separate build pass; `popcore_app/static` remains unchanged.

- [ ] **Step 3: Run browser verification proportional to the change**

```bash
.venv/bin/python popcore_app/tests/browser/check_foundation.py
.venv/bin/python popcore_app/tests/browser/check_special_orders.py
```

Expected: both checks pass. If unrelated existing browser checks fail, report the exact failure without changing unrelated code.

- [ ] **Step 4: Inspect final repository state**

```bash
git diff --check
git status --short --branch
git diff --stat
```

Expected: only the approved spec/plan and Special Orders implementation files are new or modified; all pre-existing untracked Clover/PDF files remain untouched.

- [ ] **Step 5: Report the result without overstating evidence**

Report local tests/build/browser results separately. State explicitly that production Auth0, real staff schedules/devices, deployment, and live customer data remain unverified. Do not commit, push, deploy, or rebuild committed release assets.
