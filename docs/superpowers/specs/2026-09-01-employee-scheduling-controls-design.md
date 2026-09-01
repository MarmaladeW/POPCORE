# Employee Scheduling Controls Design

Date: 2026-09-01
Status: Approved design

## Goal

Give managers two independent controls for each active employee:

1. Choose any readable calendar color with a full color picker, while retaining the existing curated colors as quick presets.
2. Enable or disable that employee for new shift assignment without disabling their login or changing their account role.

An employee who is not assignable must disappear from new-assignment controls, schedule employee filters, and the manual coverage checklist. Existing and historical shifts must remain visible, editable, and deletable.

## Product Behavior

### Employee color

- The Users settings page shows a full spectrum color picker for every local employee record.
- The existing twelve employee colors remain available as preset swatches.
- The picker displays the selected hexadecimal value and commits a color only when the user finishes a selection, avoiding an API request for every drag movement.
- The server continues to accept valid `#RGB` and `#RRGGBB` values and rejects malformed colors.
- The selected color drives employee chips and shift blocks in month, week, and day calendar views.
- If saving fails, the Users page restores the previous color and shows an error.

### Shift assignment eligibility

- Users settings adds a distinct `Shift assignment` switch for each employee.
- This switch is separate from Auth0 account activation. It does not affect login, role, permissions, or store assignment.
- Existing employees and trainees default to assignable.
- New employees and trainees also default to assignable.
- When disabled, an employee is excluded from:
  - employee filter chips in the manager schedule;
  - the manual coverage checklist;
  - employee options when creating a shift;
  - availability shortcuts used to select someone for a new shift.
- Existing shifts for a disabled employee remain visible in all calendar views.
- An existing shift for a disabled employee may still have its time, store, position, or notes edited, and it may be deleted.
- Disabling eligibility never deletes shifts, availability, checklist history, or the employee record.

## Data Model

Add a column to `employees`:

```sql
is_schedulable INTEGER NOT NULL DEFAULT 1
```

A registered, idempotent migration checks `PRAGMA table_info(employees)` before adding the column. The default makes the migration backward compatible without a data rewrite.

`is_active` retains its existing meaning: whether the local employee record is active. `is_schedulable` answers only whether managers may create new shifts for that employee.

## Backend API

### Employee listing

Both manager-facing employee responses include `is_schedulable`:

- `GET /api/schedule/employees`
- `GET /api/employees/stores`

The endpoints continue returning all active employees. Filtering is performed by the scheduling UI so existing shift data and user-management controls retain access to the full employee record.

### Update eligibility

Add:

```http
PATCH /api/employees/:employee_id/schedulable
Content-Type: application/json

{ "is_schedulable": true }
```

Rules:

- Requires manager role, matching employee color and store-assignment permissions.
- Accepts only a JSON boolean or integer `0`/`1`.
- Returns `404` for a missing or inactive employee.
- Returns the updated employee record on success.

### Shift creation enforcement

`POST /api/schedule/shifts` loads both `id` and `is_schedulable` for the requested employee. If eligibility is disabled, it returns `409 Conflict` with a clear error such as `Employee is disabled for shift assignment`.

This server-side check protects against stale browser state and direct API calls. Existing shift update and delete endpoints do not apply this restriction because they do not reassign the shift to another employee.

## Frontend Data Flow

### Types and API client

- Add `is_schedulable: number` to `Employee` and `EmployeeStoreAssignment`.
- Add a typed client function for the eligibility patch.
- Keep color updates typed and normalize picker output to a hexadecimal string.

### Users settings

- Replace the fixed `ColorSwatchPicker` with Ant Design's full `ColorPicker`.
- Supply the curated palette as presets.
- Disable alpha selection so every saved value remains a backend-compatible opaque hex color.
- Use `onChangeComplete` for persistence and optimistic UI with rollback.
- Add a dedicated shift-assignment column on desktop and a labeled switch row on mobile.
- The existing account-status switch remains unchanged and visually separate.
- If an Auth0 user has no corresponding local employee entry, both employee-only controls remain unavailable rather than silently creating schedule data.

### Manager schedule

`ManagerCalendar` retains the complete active employee array for event lookup and existing-shift rendering. It derives:

```ts
const assignableEmployees = employees.filter(employee => employee.is_schedulable !== 0)
```

The derived list is used for employee filter chips, the manual checklist, and new shift assignment. Existing event payloads already contain employee names and colors, so shifts remain visible even after eligibility is disabled.

### Shift modal

- Creating a shift shows only assignable employees.
- Editing an existing shift keeps its current employee visible even if that employee is no longer assignable.
- The employee itself is not reassigned by the existing edit flow.
- A `409` response from shift creation is surfaced using the server message and the modal remains open.

## Error Handling and Concurrency

- Color and eligibility switches update optimistically, then roll back on API failure.
- Reloading Users settings always reconciles local state with the server.
- The server is authoritative for eligibility during shift creation.
- If eligibility changes while a shift modal is open, the creation request fails with `409`; the manager receives the explanation and can choose another employee.
- Migration execution remains safe on databases that already contain the new column.

## Testing

### Backend

- Migration adds `is_schedulable` with default `1` and remains idempotent.
- Employee listing endpoints return the field.
- Eligibility patch accepts valid values and rejects invalid payloads.
- Shift creation succeeds for an assignable employee.
- Shift creation returns `409` for a non-assignable employee.
- Existing shift updates and deletes remain allowed for a non-assignable employee.

### Frontend

- Eligibility filtering removes disabled employees from manager filters, checklist input, and new-shift options.
- Existing-shift editing retains a disabled current employee.
- Color picker output is converted to a valid hex string and rollback behavior preserves the previous value after a failed save.
- Existing schedule presentation tests continue proving employee colors render in every view.

### Verification

- Run the backend test suite.
- Run the frontend schedule tests.
- Run TypeScript compilation and the production Vite build.
- Verify generated static asset references and a clean Git diff check.

## Rollout and Compatibility

- No destructive data migration is required.
- All existing employees become assignable after migration.
- Existing APIs gain fields but do not remove or rename fields.
- Historical shifts are untouched.
- The feature can be deployed with the normal application migration and frontend bundle process.

## Non-goals

- Disabling login accounts or changing Auth0 roles.
- Automatically deleting or hiding existing shifts.
- Automatically disabling availability submission.
- Store-specific assignability; eligibility applies to the employee across all stores.
- Reassigning an existing shift to another employee from the edit flow.
