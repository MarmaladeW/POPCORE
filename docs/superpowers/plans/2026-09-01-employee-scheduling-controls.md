# Employee Scheduling Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let managers freely choose each employee's calendar color and independently enable or disable that employee for new shift assignment without affecting login access or historical shifts.

**Architecture:** Persist a new `employees.is_schedulable` flag with a safe SQLite migration, expose it through existing employee listings and a manager-only PATCH endpoint, and enforce it at shift creation. The frontend retains the complete employee collection for history but derives an assignable collection for manager filters, the manual checklist, availability shortcuts, and new-shift selection. Ant Design's opaque full-spectrum `ColorPicker` replaces the preset-only popover while retaining the curated palette as presets.

**Tech Stack:** Flask, SQLite, Python `unittest`, React 18, TypeScript, Ant Design 5, FullCalendar, Node test runner, Vite.

**Spec:** `docs/superpowers/specs/2026-09-01-employee-scheduling-controls-design.md`

## Global Constraints

- Keep `is_active` and Auth0 account activation behavior unchanged.
- Never delete or hide existing shifts, availability, checklist history, or employee records when eligibility changes.
- Only new shift creation is blocked for an ineligible employee; editing and deleting an existing shift remain allowed.
- Treat the server as authoritative and roll optimistic UI state back after failed color or eligibility requests.
- Use `is_schedulable !== 0` on the client so an older cached response that omits the new field remains assignable during rollout.
- Keep color values opaque and backend-compatible: uppercase `#RRGGBB` strings.

---

## Task 1: Persist and enforce shift-assignment eligibility

**Files:**

- Create: `popcore_app/tests/__init__.py`
- Create: `popcore_app/tests/test_employee_schedulable.py`
- Modify: `popcore_app/db.py`
- Modify: `popcore_app/blueprints/users.py`
- Modify: `popcore_app/blueprints/schedule.py`

- [ ] **Step 1: Write failing migration and API tests**

Create a built-in `unittest` suite that constructs a temporary SQLite database and a minimal Flask app registering the users and schedule blueprints. Patch authentication decoding to return a manager payload. Cover:

```python
def test_migration_adds_schedulable_default_and_is_idempotent(self): ...
def test_employee_store_listing_includes_schedulable(self): ...
def test_patch_schedulable_accepts_boolean_and_zero_or_one(self): ...
def test_patch_schedulable_rejects_invalid_values(self): ...
def test_create_shift_rejects_disabled_employee(self): ...
def test_existing_shift_can_still_be_updated_and_deleted(self): ...
```

The test schema must include only the columns referenced by these endpoints: `employees`, `stores`, `employee_stores`, and `shifts`. Set `db.DB_PATH` to the temporary file for each test and restore it in teardown.

- [ ] **Step 2: Run the tests and confirm the expected failures**

Run:

```powershell
python -m unittest discover -s popcore_app/tests -p "test_*.py" -v
```

Expected: failures because the migration, response field, endpoint, and shift-creation guard do not exist.

- [ ] **Step 3: Add the idempotent database migration**

Add this migration to `popcore_app/db.py` immediately after the trainee migration and register it after `add_is_trainee_to_employees`:

```python
def _migration_add_is_schedulable_to_employees(con, cur):
    """Whether managers may create new shifts for an employee."""
    cur.execute("PRAGMA table_info(employees)")
    cols = {row['name'] for row in cur.fetchall()}
    if 'is_schedulable' not in cols:
        cur.execute(
            "ALTER TABLE employees "
            "ADD COLUMN is_schedulable INTEGER NOT NULL DEFAULT 1"
        )
    cur.execute(
        "INSERT OR IGNORE INTO _migrations (name) "
        "VALUES ('add_is_schedulable_to_employees')"
    )
```

Do not rewrite existing rows; SQLite's default supplies `1`.

- [ ] **Step 4: Return and update eligibility through the manager API**

In `get_employee_stores`, select and return `e.is_schedulable`, and include it in `GROUP BY`:

```python
'is_schedulable': r['is_schedulable'],
```

Add the manager-protected endpoint:

```python
@bp.route('/api/employees/<int:employee_id>/schedulable', methods=['PATCH'])
@role_required('manager')
def patch_employee_schedulable(employee_id):
    data = request.get_json(silent=True) or {}
    value = data.get('is_schedulable')
    if isinstance(value, bool):
        normalized = int(value)
    elif isinstance(value, int) and value in (0, 1):
        normalized = value
    else:
        return jsonify({'error': 'is_schedulable must be a boolean or 0/1'}), 400

    con = get_db()
    employee = con.execute(
        'SELECT id FROM employees WHERE id = ? AND is_active = 1',
        (employee_id,),
    ).fetchone()
    if not employee:
        con.close()
        return jsonify({'error': 'Employee not found'}), 404
    con.execute(
        'UPDATE employees SET is_schedulable = ? WHERE id = ?',
        (normalized, employee_id),
    )
    con.commit()
    updated = con.execute(
        'SELECT * FROM employees WHERE id = ?', (employee_id,)
    ).fetchone()
    con.close()
    return jsonify(dict(updated))
```

`GET /api/schedule/employees` already uses `SELECT *`, so the migration makes the field available there without a separate query change.

- [ ] **Step 5: Enforce eligibility only when creating a shift**

Change the employee lookup in `schedule_shifts_create` to:

```python
emp = con.execute(
    'SELECT id, is_active, is_schedulable FROM employees WHERE id = ?',
    (employee_id,),
).fetchone()
if not emp or not emp['is_active']:
    con.close()
    return jsonify({'error': 'Employee not found'}), 404
if not emp['is_schedulable']:
    con.close()
    return jsonify({'error': 'Employee is disabled for shift assignment'}), 409
```

Leave PATCH and DELETE shift endpoints unchanged.

- [ ] **Step 6: Run backend tests to green**

Run:

```powershell
python -m unittest discover -s popcore_app/tests -p "test_*.py" -v
```

Expected: all new tests pass.

- [ ] **Step 7: Commit the backend slice**

```powershell
git add popcore_app/db.py popcore_app/blueprints/users.py popcore_app/blueprints/schedule.py popcore_app/tests
git commit -m "feat(schedule): enforce employee assignment eligibility"
```

---

## Task 2: Add typed frontend eligibility rules

**Files:**

- Create: `popcore_app/frontend/src/pages/Schedule/employeeScheduling.ts`
- Create: `popcore_app/frontend/src/pages/Schedule/employeeScheduling.test.ts`
- Modify: `popcore_app/frontend/src/pages/Schedule/scheduleApi.ts`

- [ ] **Step 1: Write failing domain-helper tests**

Test these invariants with Node's test runner:

```ts
test('only explicitly disabled employees are excluded from new assignments', () => {
  assert.deepEqual(
    assignableEmployees([
      employee(1, 1),
      employee(2, 0),
      employee(3, undefined),
    ]).map((e) => e.id),
    [1, 3],
  )
})

test('editing keeps a disabled current employee visible', () => {
  assert.deepEqual(
    shiftModalEmployees([employee(1, 1), employee(2, 0)], 2).map((e) => e.id),
    [1, 2],
  )
})

test('picker colors are normalized to an opaque uppercase hex value', () => {
  assert.equal(normalizeEmployeeColor('#3d74c4'), '#3D74C4')
})
```

- [ ] **Step 2: Run frontend tests and confirm the module is missing**

Run:

```powershell
npm test -- --test-name-pattern="assign|picker"
```

Run from `popcore_app/frontend`. Expected: failure because `employeeScheduling.ts` does not exist.

- [ ] **Step 3: Add the typed helpers**

Create:

```ts
import type { Employee } from './scheduleApi'

export function assignableEmployees<T extends { is_schedulable?: number }>(employees: T[]): T[] {
  return employees.filter((employee) => employee.is_schedulable !== 0)
}

export function shiftModalEmployees(employees: Employee[], currentEmployeeId?: number): Employee[] {
  return employees.filter(
    (employee) => employee.is_schedulable !== 0 || employee.id === currentEmployeeId,
  )
}

export function normalizeEmployeeColor(value: string): string {
  return value.toUpperCase()
}
```

- [ ] **Step 4: Extend API types and add the PATCH client**

In `scheduleApi.ts`:

```ts
export interface Employee {
  // existing fields
  is_schedulable: number
}

export interface EmployeeStoreAssignment {
  // existing fields
  is_schedulable: number
}

export const setEmployeeSchedulable = (employeeId: number, enabled: boolean) =>
  client.patch<Employee>(`/employees/${employeeId}/schedulable`, {
    is_schedulable: enabled,
  }).then((response) => response.data)
```

- [ ] **Step 5: Run the helper tests and TypeScript build**

Run from `popcore_app/frontend`:

```powershell
npm test
npm run build
```

Expected: tests and compilation pass before UI integration.

- [ ] **Step 6: Commit the typed domain slice**

```powershell
git add popcore_app/frontend/src/pages/Schedule/employeeScheduling.ts popcore_app/frontend/src/pages/Schedule/employeeScheduling.test.ts popcore_app/frontend/src/pages/Schedule/scheduleApi.ts
git commit -m "feat(schedule): add typed assignment eligibility rules"
```

---

## Task 3: Add full color and assignment controls to Users settings

**Files:**

- Modify: `popcore_app/frontend/src/pages/Users/index.tsx`

- [ ] **Step 1: Replace the preset-only color popover**

Remove `Popover` from Ant Design imports and add `ColorPicker`. Replace `ColorSwatchPicker` with a wrapper around:

```tsx
<ColorPicker
  value={value}
  disabledAlpha
  showText={(color) => normalizeEmployeeColor(color.toHexString())}
  presets={[{ label: 'Preset colors', colors: EMPLOYEE_PALETTE }]}
  onChangeComplete={(color) => onChange(normalizeEmployeeColor(color.toHexString()))}
/>
```

Keep a compact trigger on desktop and a visible hexadecimal value on mobile. Do not save during `onChange`; persist only on `onChangeComplete`.

- [ ] **Step 2: Extend the local employee settings state**

Add `isSchedulable: number` to `EmpStoreEntry` and map it from `getEmployeeStores()` with rollout-safe fallback:

```ts
isSchedulable: e.is_schedulable ?? 1,
```

- [ ] **Step 3: Add optimistic rollback for color updates**

Capture `previousColor`, update state immediately, call the existing color endpoint, and restore `previousColor` in `catch`. Surface the server error when available.

- [ ] **Step 4: Add optimistic eligibility updates with rollback**

Import `setEmployeeSchedulable` and implement:

```ts
async function handleSchedulableChange(auth0Id: string, enabled: boolean) {
  const entry = empStoreMap[auth0Id]
  if (!entry) return
  const previous = entry.isSchedulable
  setEmpStoreMap((current) => ({
    ...current,
    [auth0Id]: { ...current[auth0Id], isSchedulable: enabled ? 1 : 0 },
  }))
  try {
    await setEmployeeSchedulable(entry.empId, enabled)
  } catch (error) {
    setEmpStoreMap((current) => ({
      ...current,
      [auth0Id]: { ...current[auth0Id], isSchedulable: previous },
    }))
    message.error('Shift assignment setting failed to update')
  }
}
```

- [ ] **Step 5: Add a clearly separate desktop column and mobile row**

Desktop column:

```tsx
{
  title: '排班 / Shifts',
  key: 'is_schedulable',
  width: 120,
  align: 'center',
  render: (_, user) => {
    const entry = empStoreMap[user.id]
    return entry ? (
      <Switch
        size="small"
        checked={entry.isSchedulable !== 0}
        onChange={(enabled) => handleSchedulableChange(user.id, enabled)}
      />
    ) : null
  },
}
```

On mobile, place a labeled `Shift assignment` switch beside the color control or in its own compact row. Retain the existing Auth0 status switch and its `启用/禁用` label in a separate row.

- [ ] **Step 6: Verify Users settings compiles and the color helper tests remain green**

Run from `popcore_app/frontend`:

```powershell
npm test
npm run build
```

- [ ] **Step 7: Commit the settings UI slice**

```powershell
git add popcore_app/frontend/src/pages/Users/index.tsx
git commit -m "feat(users): add employee scheduling controls"
```

---

## Task 4: Filter manager scheduling controls without hiding history

**Files:**

- Modify: `popcore_app/frontend/src/pages/Schedule/ManagerCalendar.tsx`
- Modify: `popcore_app/frontend/src/pages/Schedule/ShiftModal.tsx`

- [ ] **Step 1: Derive the assignable list once in ManagerCalendar**

Import `assignableEmployees` under an alias to avoid shadowing and derive:

```ts
const schedulableEmployees = getAssignableEmployees(employees)
```

Keep `employees` unchanged for event color/name lookup and historical shift rendering.

- [ ] **Step 2: Use only assignable employees in manager controls**

Replace `employees` with `schedulableEmployees` only in:

- desktop employee filter select/options;
- mobile and desktop employee chips;
- `CoveragePanel`'s `employees` prop;
- new-shift `ShiftModal` options.

If the currently selected filter becomes disabled after a refresh, reset `filterEmpId` to `null` with an effect.

- [ ] **Step 3: Preserve a disabled employee while editing an existing shift**

Before rendering `ShiftModal`, derive:

```ts
const modalEmployees = shiftModalEmployees(employees, selectedShift?.employee_id)
```

Pass `modalEmployees` into `ShiftModal`. This yields only assignable employees for create mode and includes the current disabled employee in edit mode, where the select is already disabled.

- [ ] **Step 4: Remove disabled availability shortcuts**

Inside `ShiftModal`, derive eligible employee IDs from the received `employees` prop and filter `availForDate` before building `availByEmpId` and rendering the availability badges. Historical event rendering is unaffected.

- [ ] **Step 5: Surface the server's stale-state 409 message**

The existing conflict-check catch currently treats any create failure as a conflict-check error. Separate the create call error handling so `doCreateShift` catches Axios errors and shows:

```ts
const detail = axios.isAxiosError(error)
  ? error.response?.data?.error
  : undefined
msgApi.error(detail || 'Failed to save shift')
```

Keep the modal open and restore `savePhase` to `idle` after a rejected create. The conflict-confirmation path must use the same behavior.

- [ ] **Step 6: Run frontend tests and production compilation**

Run from `popcore_app/frontend`:

```powershell
npm test
npm run build
```

Expected: the new eligibility tests, existing mobile/checklist/color tests, TypeScript compilation, and Vite build all pass.

- [ ] **Step 7: Commit the schedule integration**

```powershell
git add popcore_app/frontend/src/pages/Schedule/ManagerCalendar.tsx popcore_app/frontend/src/pages/Schedule/ShiftModal.tsx
git commit -m "feat(schedule): hide disabled employees from assignment tools"
```

---

## Task 5: Build deployable assets and verify the complete change

**Files:**

- Modify: generated frontend assets under `popcore_app/static/` if this repository tracks production bundles
- Modify: `popcore_app/templates/index.html` if Vite updates hashed asset references

- [ ] **Step 1: Run all automated verification**

From the repository root:

```powershell
python -m unittest discover -s popcore_app/tests -p "test_*.py" -v
Set-Location popcore_app/frontend
npm test
npm run build
Set-Location ../..
```

- [ ] **Step 2: Confirm production assets reference real files**

Inspect `popcore_app/templates/index.html` and verify every generated `/static/assets/...` reference exists under `popcore_app/static/assets`.

- [ ] **Step 3: Review the final diff against the approved spec**

Run:

```powershell
git status --short
git diff --check
git diff HEAD~4 --stat
git diff HEAD~4 -- popcore_app/db.py popcore_app/blueprints/users.py popcore_app/blueprints/schedule.py popcore_app/frontend/src/pages/Users/index.tsx popcore_app/frontend/src/pages/Schedule
```

Verify explicitly:

- arbitrary colors persist and roll back on failure;
- presets remain available;
- account activation and shift assignment are separate controls;
- disabled employees are absent from every new-assignment surface;
- historical shifts remain visible and editable;
- backend rejects stale or direct creation requests with `409`;
- no unrelated user changes are included.

- [ ] **Step 4: Commit generated production assets if changed**

```powershell
git add popcore_app/static popcore_app/templates/index.html
git commit -m "build(frontend): update employee scheduling assets"
```

- [ ] **Step 5: Push only after the user explicitly requests it**

```powershell
git push origin codex/schedule-mobile-polish
```
