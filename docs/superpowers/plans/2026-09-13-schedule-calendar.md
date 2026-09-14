# Schedule calendar implementation plan

> Use subagent-driven-development for the isolated backend task and review; integrate the frontend in this session. The user subsequently authorized committing and pushing the Schedule changes. Deployment is not authorized.

**Goal:** Implement the approved calendar preview: two-week employee availability, manager day details and assignment, existing shift presets, and the current operations shell.
**Architecture:** Reuse FullCalendar, ShiftModal, current roles and store selection. Add complete, versioned availability submissions to the existing availability data. Keep existing shifts and calendar feeds intact.
**Spec:** User-approved calendar preview and shift-type follow-up in this task. Per-store availability is retained; cycles are anchored to Monday 2026-09-14 (corrected by the user). Draft edits stay in browser memory across app navigation until Submit or an identity change. A submission contains all 14 dates; missing is never unavailable. Deadline shown is the Friday before the cycle; no hard cutoff is introduced.
**Stack:** Existing Flask/SQLite, React/TypeScript, Ant Design and FullCalendar; no dependencies.

## Task 1: Availability persistence and assignment safeguards
- [x] Add failing isolated API tests for complete 14-day submission, explicit unavailable days, own-user scope, manager-only team reads, invalid dates/times, stale submissions, resubmission preserving shifts, store separation and existing data migration.
- [x] Add `status` (available/unavailable) to availability, preserve original rows/IDs, change uniqueness to employee/date/store; add submission metadata keyed by employee/store/period with version and submitted_at.
- [x] Add GET/PUT `/api/schedule/availability/period?period_start=YYYY-MM-DD&store_code=DT` (PUT carries those fields plus `version` and `days`). Response: `{period_start, period_end, store_code, version, submitted_at, days: Availability[]}`. GET returns existing legacy days as unsubmitted. PUT validates the complete cycle and writes all 14 days atomically, incrementing version.
- [x] Existing availability GET endpoints include status, submitted_at and submission_version; manager infers missing employees from its employee roster. Legacy single-day writes invalidate the affected submission. Preserve all other stores and shifts.
- [x] Prevent creating a shift from silently replacing an existing same-day shift (including other store). Validate dates/time ranges. Keep existing edit/delete and trainee assignment behavior.
- [x] Run targeted backend tests and migration repeat checks.

## Task 2: Calendar UI and shell
- [x] Add date-cycle and day-response helpers with Node tests (including DST and year boundaries).
- [x] Add employee two-week calendar with multi-date selection, available/unavailable hours, repeat first week, 14-day completion, submit/resubmit and errors without draft loss. Keep My shifts/calendar sync.
- [x] Add manager Two weeks view, Assigned shifts/Availability toggle, day details grouping assigned/available/other responses, and preselected employee assignment through existing ShiftModal.
- [x] Preserve presets from store hours/settings. Allow missing availability; show and enforce conflicts with submitted hours. Preserve shifts when availability changes and flag mismatch.
- [x] Use operations theme/navigation on Schedule. Keep employee colors and existing coverage, reports, trainees and personal shifts.

## Task 3: Verify and review
- [x] Run backend unit suite, frontend tests, TypeScript and verification build to `.local/schedule-build` (do not replace static assets).
- [x] Add/run isolated browser checks for staff submission, manager assignment/presets, responsive layouts and stale/error states. Inspect screenshots.
- [x] Request independent code review, address actionable findings, document operational behavior in `docs/scheduling.md`, check final diff/status.
