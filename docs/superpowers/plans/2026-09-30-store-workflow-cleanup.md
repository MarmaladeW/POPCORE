# Store workflow cleanup implementation plan

## Approved scope
Fix the concrete usability and recovery problems from the September 30 review: role-aware home and simpler navigation, a visible manual checkout entry, an outstanding-transfer receiving chooser, and actionable closing blockers. Reuse existing APIs, components, and permissions. Keep the four staff home actions and personal shifts. Also finish editable draft/recount observations, saved goods URLs, and partial restock receipt with identical-request retries. No provider integration, inventory activation, deployment, or unrelated Schedule changes.

## Execution and verification
- [x] Home/navigation: group secondary tools without losing access; managers see existing Today work on home, staff keep four actions. Verify role-specific links, mobile access, and private shifts using the browser fixtures.
- [x] Checkout: expose existing manual entry for permitted stores; preserve no-shift and history scope. Add a browser regression and run checkout checks.
- [x] Receiving: provide outstanding incoming transfers scoped by authorized destination, with direct resume links and separate manager creation. Test permissions and cross-store/empty/error states.
- [x] Closing: resolve existing blocker identifiers into safe, authorized links and readable context where available. Test supported blocker paths without loosening review or posting safeguards.
- [x] Recovery: save receipt/transfer/count IDs in their URLs; edit draft recount observations using existing version/idempotency rules; receive partial restock quantities and preserve uncertain requests. Test refresh, stale updates, unchanged submitted observations, and duplicate-write prevention.
- [x] Review combined diff; run frontend tests, separate production-mode build, affected backend and browser checks. Keep committed static assets unchanged until an intentional release is requested.

## Review focus
Role changes must not retain manager data. All-store selection must not allow writes. Staff retain private history off shift. Empty or disconnected services need honest next actions. URL navigation must retain the correct store/document and pending-write safeguards.

## Decisions
The user authorized implementation after reviewing the proposed first release; proceed without another design approval. Existing untracked files belong to the original checkout. No commit, push, or deployment is included in this request.

## Verified result
- Full backend suite: 551 tests passed.
- Frontend: 33 tests passed; production-mode verification build passed into `.local/frontend-build`. Committed static assets and package lock are unchanged.
- Browser checks passed: foundation, attendance, staff home, store navigation, checkout focus (including actual Flask payment/stock flow), goods flow, transfer receiving, partial restock receiving (actual Flask, lost reply after commit), store day/count recovery, and trades/Today. New browser scripts are included in CI.
- Independent combined code review found no outstanding actionable issues; `git diff --check` passed.
- The Schedule browser check reaches its final Save notes positioning assertion and fails at y=16.28125 rather than y>=64. The unchanged original checkout reproduces the same bounds exactly. Schedule source is unchanged; this is an existing failure, not a passing check. Logs and screenshots are under `.local/store-cleanup-checks`.
- Local branch: `codex/store-workflow-cleanup`. No commit, push, deployment, production data changes, or live Auth0/provider/device verification.
