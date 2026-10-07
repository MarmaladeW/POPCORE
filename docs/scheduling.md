# Schedule and biweekly availability

Schedule uses the current operations navigation and theme. Managers open Team schedule; other employees open My availability. My shifts remains available to everyone and includes assignments across stores, independent of the selected operations store.

## Employee workflow

- Select a store and a two-week period. Shared cycles start on Mondays, anchored to September 14, 2026: September 14–27, then September 28–October 11, repeating every 14 days in both directions.
- Select one or more calendar dates, enter available hours or mark unavailable, then apply. Repeat first week copies all seven responses into the second week, including dates not yet set.
- Complete all 14 dates and submit. Missing responses are distinct from unavailable. The Friday before the period is displayed as guidance; there is no enforced deadline.
- Drafts stay in browser memory while switching stores, periods, tabs or app pages. They are cleared when the signed-in identity changes and do not survive reloading or closing the app. The browser receives an unsaved-changes warning while any draft is dirty.
- Resubmission changes availability only. Assigned shifts remain unchanged. If another session changes the period, the stale submission returns a conflict and preserves the draft; the employee can explicitly discard edits and reload.

## Manager workflow

- Team schedule defaults to Two weeks, with Month, Week and Day still available. Employee colors and existing store calendars, coverage notes, trainees and monthly reports are retained.
- The Availability tab has a searchable employee switcher. My availability keeps the signed-in employee's editor; selecting another employee shows their submitted two-week calendar, hours, unavailable dates, notes and submission timestamp in read-only mode. Store and period navigation apply to that employee. Unsubmitted or historical daily data is labeled Not submitted. Refresh reloads declarations; a failed request shows a retry action instead of an empty calendar.
- Toggle Assigned shifts / Availability, then select a date to see assignments, submitted available employees, unavailable responses and employees who have not submitted. Date buttons also support keyboard selection.
- Assign opens the existing shift editor with the employee selected. Full day, both Half shifts, configured custom slots and Custom remain available; times come from store hours and existing settings.
- Managers and admins can assign shifts when the employee has not submitted availability. The editor explains this and permits saving. If availability was submitted, new non-trainee assignments must fit those hours; the server rechecks atomically when saving. Existing same-day assignments, including another store, cannot be silently replaced.
- Mobile team and personal calendars show Full day, Half day (AM/PM), or Custom labels rather than initials or dots alone.
- Existing shifts may still be edited or deleted. Availability changes that no longer cover a shift show a Review warning. Trainee assignment retains its existing behavior.
- Actual finish: once a shift's Toronto date has arrived, the shift editor shows an optional Actual finish time (exact minute) for managers and admins. A time up to 01:00 that is not after the start counts as the next day and is labelled `(next day)`. The planned start and end are kept; the day panel and calendar tooltip show `finished HH:MM`. Employees cannot record it.

## Persistence and compatibility

The `availability_period_submissions` migration preserves existing availability IDs, timestamps and the autoincrement high-water mark; uniqueness becomes employee/date/store. It adds available/unavailable status and an `availability_submissions` table containing a version and submission timestamp for each employee/store/period. Historical daily rows remain readable but count as unsubmitted until a complete period is submitted.

`GET /api/schedule/availability/period` reads the authenticated employee's period. `PUT` takes `period_start`, `store_code`, `version` and exactly 14 explicit daily responses. Writes are atomic and reject stale versions with HTTP 409. Team availability reads remain manager-only. Legacy daily endpoints remain compatible and invalidate the affected period's submission marker. Shift callers using `require_availability: true` receive the server-side submission/hours check; legacy callers retain their existing assignment policy.

After correcting the cycle from September 21 to September 14, existing daily hours and assigned shifts are preserved. Old cycle submission markers do not confirm the new split: employees must complete and resubmit the corrected periods. Managers can assign shifts while those submissions are pending. `require_availability: true` now checks submitted hours when a submission exists; missing submissions do not block assignment.

The `add_actual_end_time_to_shifts` migration adds a nullable `shifts.actual_end_time` (`HH:MM`; `NULL` means not recorded). `PATCH /api/schedule/shifts/<id>` (manager and admin) accepts `actual_end_time` as a time after the shift start or up to `01:00` the next day, or `null` to clear it, and rejects it for future-dated shifts. The monthly report and per-employee hours count each shift from its planned start to the actual finish when one is recorded, otherwise to the planned end. Calendar layout, Full/Half labels, understaffing overlays, availability checks and the iCal feed keep using the planned end. A finish later than 01:00 the next day cannot be recorded, a next-day finish's hours are counted on the shift's own date, and deleting a shift deletes its recorded finish.

## Local verification

On September 13, 2026:

- Backend: 397 unittest checks passed, including migration preservation, role boundaries, store separation, stale submissions, read consistency and assignment conflicts.
- Frontend: 23 Node tests passed. TypeScript and the production-mode verification build passed, output to `.local/schedule-build`; committed static assets were not replaced.
- `check_schedule_calendar.py` passed using disposable SQLite data and real Flask schedule endpoints with mocked authentication. It covers submission, draft navigation/store changes, stale conflict/reload, keyboard date selection, preset availability restrictions and saved shift data, plus 390/768/1440 px and zoom checks. Screenshots are under `.local/schedule-browser`.
- Existing `check_foundation.py` passed, including shift edit/delete and shared navigation.

The subsequent user-authorized push includes the rebuilt `popcore_app/static/` bundle alongside the source, as required by the README release convention. These checks do not verify live Auth0 or a deployment; live data is unchanged.
