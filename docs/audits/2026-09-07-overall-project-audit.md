# POPCORE Overall Project Audit

**Executed:** 2026-09-07
**Repository:** `D:\dev\POPCORE`
**Revision:** `8630199ac780059f5cb185a85c6efd0ac4597b91` (`main`)
**Remote:** `https://github.com/MarmaladeW/POPCORE.git`

This audit used local source, isolated synthetic databases, test doubles, dependency metadata, and a mocked browser. It did not access the droplet, copy production data, mutate external accounts, deploy, commit, or change application behavior.

## Result

The project is usable for local development: the D:-local Python environment is consistent, all 10 backend tests and 14 frontend tests pass, the production frontend builds, empty and representative legacy database migrations are repeatable, protected API routes reject anonymous requests, and the tested role hierarchy and store isolation work.

The project is **not ready for a production release without remediation**. The audit reproduced four high-impact data/validation failures: deleting a completed restock can recreate stock already consumed, replacing a daily report can inflate stock, negative sales quantities are accepted, and malformed shifts are stored. Some API failures appear as valid zero/empty UI data, 200% zoom clips core content, and the locked frontend tree has 13 known advisories, including 7 high-severity advisories. No fixes were applied.

Recommended order for later work:

1. Fix the two stock-reconciliation defects and add transaction-level regression tests.
2. Reject invalid sales quantities and schedule date/time values at the API boundary.
3. Make failed data loads visible and repair 200% zoom/mobile Schedule clipping.
4. Triage dependency upgrades package by package; test the required Vite major upgrade separately.
5. Add automated stock, sales, restock, permission, migration, and production-build coverage.

## Architecture and important folders

- **Backend:** Flask 3.1 with blueprints for insights, inventory, products, restock, sales, schedule, settings, stock, stores, and users.
- **Persistence:** SQLite in WAL mode with 28 application-managed migrations. Normal data is `popcore_app/popcore.db`; product images are under `popcore_app/hidden_imgs`.
- **Frontend:** React 18, TypeScript, Vite 5, Ant Design, Tailwind, Zustand, FullCalendar, Recharts, and Chart.js. Ten client routes cover all main screens and the wildcard route.
- **Identity/integrations:** Auth0 SPA login and JWT verification, Auth0 Management helpers, Google Sheets, optional Anthropic parsing, external scraping, Sentry, and scheduled insights.
- **Production templates:** Gunicorn, nginx, systemd, logrotate, setup, and SQLite backup scripts.

| Path | Purpose |
| --- | --- |
| `popcore_app/` | Flask app, database code, integrations, and built frontend assets |
| `popcore_app/blueprints/` | APIs and business workflows |
| `popcore_app/frontend/` | React/Vite source and locked npm dependencies |
| `popcore_app/tests/` | Backend unit tests |
| `deployment/` | Production service, proxy, backup, and setup templates |
| `docs/` | Development notes, product context, plans, and audit reports |
| `.local/audit/` | Ignored D:-local fixtures, probes, logs, screenshots, and isolated build |

## Reproducible baseline

| Check | Result |
| --- | --- |
| Git | `main` at `8630199ac780059f5cb185a85c6efd0ac4597b91`; origin fetch/push is `https://github.com/MarmaladeW/POPCORE.git` |
| Python | Python 3.13 project venv at `D:\dev\POPCORE\.venv`; `pip check` passed |
| Backend tests | 10/10 passed |
| Frontend tests | 14/14 passed |
| Isolated build | Passed, 5,616 modules; JS 2,673.28 kB raw / 821.18 kB gzip |
| Build warnings | Main chunk exceeds 500 kB; Browserslist data is 7 months old |
| Migrations | Empty schema: 28/28 and repeat-safe. Legacy fixture preserved product/stock/sale, `quick_check=ok`, zero foreign-key violations |
| API inventory | 110 Flask rules; 108 protected API method/rule combinations returned 401 anonymously; invalid public iCal token returned 404 |
| UI inventory | 81 mocked screens across populated, empty, error, denied-role, three viewport, and 200% zoom states |
| Final listeners | Ports 5000, 5173, and 5174 were not listening; the audit stopped no user process |

There are 132 tracked files, including 24 Python and 66 TypeScript/TSX files. Tests comprise one backend module and two frontend modules. No CI workflow, npm lint script, or pytest suite is configured.

## Coverage matrix

| Area | Status | Evidence and limit |
| --- | --- | --- |
| Repository/setup/Git | Checked | Branch, revision, remote, diffs, runtimes, environment templates, ignores, and listeners inspected. |
| Flask routes | Checked | All rules inventoried; every protected API rule/method anonymously probed. |
| Authentication/JWT | Partial | Real verifier tested with local RSA keys. Live Auth0 refresh, consent, and Management API mutation were excluded. |
| Authorization | Partial | Representative viewer/staff/manager/admin hierarchy and anonymous gates passed; every role/endpoint pair and policy intent were not exhaustive. |
| SQLite migrations | Checked | Empty and representative legacy schemas migrated twice with integrity checks; exact droplet schema/data was unavailable. |
| Stock/inventory/sales/restock | Partial | Store isolation, rollback, replacement, restock lifecycle, negative input, and forced contention exercised with synthetic data. |
| Scheduling/reporting | Partial | Existing tests passed and malformed shift input was reproduced; full DST, wage-period, and cross-store-policy coverage remains. |
| Upload/export inputs | Partial | Fake image and CSV formula cases checked; all import formats and large-file behavior were not exhaustive. |
| External integrations | Partial | Source/failure boundaries reviewed; Google, Anthropic, Auth0 Management, Sentry, and scraper were not called live. |
| Scheduler | Partial | Ownership and date logic reviewed; production restart/multi-worker behavior not exercised. |
| Frontend | Partial | All routes covered synthetically at three widths; selected failures, denied roles, and zoom checked. Live populated data and full assistive-technology testing remain. |
| Performance | Partial | Local mocked request counts and build size measured; not representative of droplet latency/data/caching. |
| Dependencies | Checked | Locked npm and installed Python trees audited on 2026-09-07; reachability assessed where possible. |
| Deployment/backup | Partial | Templates reviewed and disposable SQLite integrity checked; active host configuration and a production restore remain blocked by scope. |

## Findings

Priority reflects impact; confidence and production exposure are stated separately.

### P1-01 — Deleting a completed restock can recreate consumed stock

- **User/operation:** Manager deletes a completed restock after moved units were consumed.
- **Location:** `popcore_app/blueprints/restock.py:66-108`.
- **Reproduction:** Start at 10 upstairs/0 in-store, complete a move of 5, consume 5 in-store, then delete the completed session. All API steps succeed.
- **Expected/actual:** Expected total 5; actual stock is 10 upstairs/0 in-store, total 10.
- **Impact:** Inventory is silently increased.
- **Evidence/confidence:** Locally reproduced, high confidence; `.local/audit/restock-results.json`. Production frequency is unknown.
- **Smallest fix:** Atomically reverse only remaining attributable movement, or reject deletion when dependent consumption exists.
- **Regression:** Test never/partially/fully consumed completed restocks and assert conserved totals/history.

### P1-02 — Daily-report replacement can inflate stock

- **User/operation:** Staff or manager corrects an overdrawn daily sales report.
- **Location:** `popcore_app/blueprints/sales.py:361-397` and replacement call near line 458.
- **Reproduction:** Start in-store at 2, submit quantity 5, replace the report with an empty report.
- **Expected/actual:** Expected stock 2; deduction clamps at zero, then replacement adds 5, producing 5.
- **Impact:** Report correction creates inventory.
- **Evidence/confidence:** Locally reproduced, high confidence; `.local/audit/probe-results.json`.
- **Smallest fix:** Persist and reverse the quantity actually deducted.
- **Regression:** Requested sales below/equal/above stock followed by replacement, deletion, and retry.

### P1-03 — Sales APIs accept negative quantities

- **User/operation:** Daily sale upsert and batch submission.
- **Location:** `popcore_app/blueprints/sales.py:86-127` and `sales.py:305-359`.
- **Reproduction:** Submit `qty_sold=-5` to the batch endpoint.
- **Expected/actual:** Expected 400/no row; actual is 200 and `-5` is stored.
- **Impact:** Sales totals and stock/report arithmetic can be corrupted.
- **Evidence/confidence:** Locally reproduced and source-confirmed, high confidence; `.local/audit/probe-results.json`.
- **Smallest fix:** Share one integer/nonnegative validator across sale writes.
- **Regression:** Negative, decimal, string, huge, empty, and mixed valid/invalid batches.

### P1-04 — Schedule API stores malformed dates and times

- **User/operation:** Manager creates or updates shifts.
- **Location:** `popcore_app/blueprints/schedule.py:540-610`.
- **Reproduction:** Submit date `not-a-date`, start `garbage`, end `also-garbage`.
- **Expected/actual:** Expected 400 field errors; actual is 201 and values persist.
- **Impact:** Calendars/reports can fail or calculate invalid hours later.
- **Evidence/confidence:** Locally reproduced, high confidence; `.local/audit/probe-results.json`.
- **Smallest fix:** Validate date/time format and supported range before writes; reuse on update.
- **Regression:** Invalid formats/ranges, overnight policy, DST, leap day, and valid presets/custom shifts.

### P1-05 — API failures appear as valid zero or empty data

- **User/operation:** Dashboard, Stock, or Schedule during auth/network/server failure.
- **Location:** `frontend/src/App.tsx:43-55`, `frontend/src/pages/Dashboard/index.tsx:100-114`, `frontend/src/pages/Stock/index.tsx:122-135`.
- **Reproduction:** Reject initial page requests in the mocked error state at desktop/tablet/mobile widths.
- **Expected/actual:** Expected error/retry state; Dashboard shows zero values, Stock says no records, and Schedule is empty while async errors reach the page.
- **Impact:** Staff may act on false operational information.
- **Evidence/confidence:** Fixture/mocked browser, high confidence for client behavior; `.local/audit/ui/error-*`.
- **Smallest fix:** Separate loading, empty, and failed states; retain last-known data and expose retry.
- **Regression:** Fail each initial request independently and assert error text, retry, and prior-data retention.

### P1-06 — Locked frontend tree has high-severity advisories

- **User/operation:** Development/build toolchain and affected browser/runtime dependency paths.
- **Location:** `popcore_app/frontend/package.json` and `package-lock.json`.
- **Reproduction:** `npm audit --json` on 2026-09-07 reports 13 advisories: 7 high, 5 moderate, 1 low, 0 critical.
- **Expected/actual:** Expected release triage of high advisories; affected paths include direct Axios/PostCSS/React Router dependencies, Vite, and transitives. Vite's proposed resolution is 8.2.2, a major upgrade.
- **Impact:** Reachability varies by context, but the unresolved high findings are a release risk.
- **Evidence/confidence:** Locally reproduced metadata, high advisory confidence; exploit reachability is not established for every path. See `.local/audit/npm-audit.json`.
- **Smallest fix:** Upgrade compatible direct packages first and test the Vite major change separately; avoid an unreviewed force fix.
- **Regression:** Tests, TypeScript/build, asset references, and browser smoke after each dependency group.

### P1-07 — 200% zoom clips Products and Schedule

- **User/operation:** Low-vision/zoom users.
- **Location:** `frontend/src/index.css:56-75` (`overflow-x: hidden`) and wide Products/Schedule layouts.
- **Reproduction:** Render selected routes at 200% zoom.
- **Expected/actual:** Expected reflow or reachable overflow; Products breaks its title and hides table columns, while Schedule controls/content become clipped.
- **Impact:** Core inventory/scheduling information becomes unreadable or unreachable.
- **Evidence/confidence:** Fixture/mocked browser, high confidence; `.local/audit/ui/zoom200-desktop-products.png` and `zoom200-desktop-schedule.png`.
- **Smallest fix:** Remove global clipping and provide local scrolling/responsive alternatives.
- **Regression:** All routes at 200% plus keyboard access to every control/table column.

### P2-01 — JWT verification omits issuer validation

- **User/operation:** Backend access-token acceptance.
- **Location:** `popcore_app/auth.py:51`.
- **Reproduction:** Use the accepted local test key and audience but a wrong `iss`.
- **Expected/actual:** Expected rejection; verifier accepts because decode receives algorithms/audience but no issuer.
- **Impact:** Tenant-boundary validation is weaker, though exploitation still requires a key trusted by configured JWKS.
- **Evidence/confidence:** Locally reproduced against real verifier with local keys; high behavior confidence, medium practical exploitability.
- **Smallest fix:** Require the canonical configured HTTPS issuer.
- **Regression:** Correct accepted; wrong/missing/look-alike issuers rejected; retain audience/algorithm tests.

### P2-02 — Image upload trusts extension and lacks a Flask size limit

- **User/operation:** Manager product-image upload.
- **Location:** `popcore_app/blueprints/products.py:385-414`; no `MAX_CONTENT_LENGTH` in `app.py`.
- **Reproduction:** Upload text bytes named `.jpg`.
- **Expected/actual:** Expected rejection; actual is 201 and file storage.
- **Impact:** Invalid or oversized content can consume disk and enter image paths. Manager-only access reduces exposure.
- **Evidence/confidence:** Locally reproduced plus source-confirmed, high confidence. Active nginx size behavior needs production verification.
- **Smallest fix:** Decode/validate images, write a safe derivative, and set explicit Flask/nginx limits.
- **Regression:** Valid images, renamed text, malformed headers, decompression bomb, oversized and duplicate uploads.

### P2-03 — CSV exports permit spreadsheet formulas

- **User/operation:** Staff opens exported user-controlled names/notes in a spreadsheet.
- **Location:** `popcore_app/db.py:14-20` and CSV call sites.
- **Reproduction:** `esc_csv('=1+1')` remains a formula; quoting `@...` does not neutralize it.
- **Expected/actual:** Expected formula-prefix neutralization; actual escaping only follows CSV syntax.
- **Impact:** Crafted fields can execute formulas when exports open.
- **Evidence/confidence:** Locally reproduced and source-confirmed, high confidence.
- **Smallest fix:** Prefix dangerous leading characters after normalizing leading whitespace.
- **Regression:** All formula prefixes, ordinary negative values under policy, Unicode, quotes, commas, and newlines.

### P2-04 — Core controls lack accessible names and some touch targets are small

- **User/operation:** Keyboard, screen-reader, and touch use.
- **Location:** `frontend/src/components/AppLayout.tsx:315-386`, product actions at `frontend/src/pages/Products/index.tsx:358-387`, sales quantities near `frontend/src/pages/Sales/DailyReport.tsx:243-264`, and `frontend/src/index.css:69-75`.
- **Reproduction:** Source confirms unlabeled store select, clickable avatar without button semantics, icon-only actions without names, and 32 px Schedule controls.
- **Expected/actual:** Expected names/keyboard semantics/visible focus and about 44 px primary touch targets.
- **Impact:** Assistive-technology operation and touch accuracy suffer.
- **Evidence/confidence:** Source-confirmed examples plus mocked-browser heuristics; high confidence for cited controls, no WCAG-conformance claim.
- **Smallest fix:** Use buttons/labels, add contextual names, and adopt the existing 44 px target convention.
- **Regression:** Keyboard pass, accessibility-tree name checks, modal focus return, and 390 px measurements.

### P2-05 — Mobile Schedule navigation is clipped

- **User/operation:** Schedule at 390 px.
- **Location:** `frontend/src/pages/Schedule/index.tsx` tabs and global clipping in `frontend/src/index.css:56-75`.
- **Reproduction:** Render populated Schedule at 390 px.
- **Expected/actual:** Expected all views/report action reachable; left tab and Monthly Report control are clipped.
- **Impact:** A primary workflow is harder to discover/use.
- **Evidence/confidence:** Fixture/mocked browser, high confidence; `.local/audit/ui/dense-mobile-schedule.png`.
- **Smallest fix:** Use a locally scrollable or compact selector with visible focus/selection.
- **Regression:** 320/375/390/768 px with long labels and keyboard/touch navigation.

### P2-06 — Forced SQLite contention yields unhandled 500s

- **User/operation:** Concurrent stock/session writes.
- **Location:** Stock-row creation and read-modify-write paths in `db.py` and inventory/restock blueprints.
- **Reproduction:** Force two requests to overlap while a shared-state lock is held; both return 500 `database is locked`, stock unchanged.
- **Expected/actual:** Expected bounded retry or explicit busy/conflict response; actual is internal error.
- **Impact:** Sustained contention can fail valid work. This deliberately forced lock does not prove normal simultaneous requests fail.
- **Evidence/confidence:** Fixture/forced contention; medium confidence; `.local/audit/concurrency-results.json`.
- **Smallest fix:** Define busy timeout/retry behavior and a retriable response without replaying committed work.
- **Regression:** Short/long lock holds; assert final quantities, single history rows, and bounded response time.

### P2-07 — Scheduler compares business-time setting with UTC

- **User/operation:** Scheduled insight generation.
- **Location:** `popcore_app/app.py:87-116` and date logic in `insights.py`.
- **Reproduction:** Source uses `datetime.utcnow()` for `INSIGHT_GENERATE_TIME` while business scheduling uses Toronto time.
- **Expected/actual:** Expected documented timezone alignment; 02:00 UTC is 22:00/21:00 Toronto on the preceding local date.
- **Impact:** Insights may run for an unintended business day around normal days/DST.
- **Evidence/confidence:** Source-confirmed, high code confidence; business impact and active configuration require production verification.
- **Smallest fix:** Parse the setting and report date with one explicit IANA timezone.
- **Regression:** Toronto winter/summer/DST dates, restart, and one run per intended local day.

### P2-08 — High-impact workflows lack automated gates

- **User/operation:** Future stock, sales, restock, permission, migration, or build changes.
- **Location:** `popcore_app/tests/test_employee_schedulable.py`, two frontend Schedule tests, and absent `.github/workflows/`.
- **Reproduction:** 10 backend and 14 frontend tests are scheduling-focused; no CI, npm lint script, or pytest suite exists.
- **Expected/actual:** Expected release-critical arithmetic and access boundaries to be guarded; P1-01 through P1-04 are untested.
- **Impact:** Regressions can merge without a repeatable gate.
- **Evidence/confidence:** Source-confirmed and local test execution, high confidence.
- **Smallest fix:** Add focused tests for reproduced defects and minimal CI for backend/frontend tests, type/build, and `pip check`.
- **Regression:** Mutation proof for each new guard.

### P2-09 — Production backup/hardening needs a live gate

- **User/operation:** Recovery and host containment.
- **Location:** `deployment/backup.sh`, `nginx.conf`, `popcore.service`, and `setup_production.sh`.
- **Reproduction:** Templates keep default backups on-host with pruning and no scripted restore test; no CSP/Permissions-Policy; location-specific nginx headers may suppress parent header inheritance; service can write the repo and has limited hardening.
- **Expected/actual:** Expected tested restore, off-host copy, active header verification, and least privilege; templates do not prove deployment.
- **Impact:** Host/disk loss can defeat backups and active protections may differ from intent.
- **Evidence/confidence:** Source-confirmed templates; production verification required, live exposure unknown.
- **Smallest fix:** Add isolated restore verification/off-host copy, then inspect active nginx/systemd before tightening.
- **Regression:** Restore recent backup, run SQLite integrity/FK checks, start isolated app, capture active headers/service properties.

### P2-10 — Telemetry and optional LLM parsing may transmit sensitive data

- **User/operation:** Error telemetry and report parsing.
- **Location:** `popcore_app/app.py:20-24` (`send_default_pii=True`) and `llm_parser.py:169-190` (raw report text).
- **Reproduction:** Trace payload construction and Sentry initialization.
- **Expected/actual:** Expected data classification/minimization/retention decision; source can send identity/business data to third parties.
- **Impact:** Employee or operational data may leave the application boundary.
- **Evidence/confidence:** Source-confirmed potential; enabled keys and actual production payloads require verification.
- **Smallest fix:** Disable default PII, redact sensitive fields, and document approval before enabling LLM parsing.
- **Regression:** Capture sanitized test events/requests and assert excluded fields never leave the process.

### P2-11 — Authentication handling can amplify redirect loops and mask causes

- **User/operation:** Callback, consent, token, network, or stale-session failure.
- **Location:** `frontend/src/auth/ProtectedRoute.tsx:7-34` and `frontend/src/api/client.ts`.
- **Reproduction:** Source starts `loginWithRedirect()` during render; failed token acquisition falls through to an unauthenticated request whose 401 is labeled Session Expired.
- **Expected/actual:** Expected one controlled redirect and specific error; actual paths can repeat and lose cause.
- **Impact:** Troubleshooting and recovery are harder. The earlier local callback correction was preserved.
- **Evidence/confidence:** Source-confirmed risk, medium confidence; live recurrence not reproduced.
- **Smallest fix:** Redirect from an effect with a one-flight guard and retain the token error for display.
- **Regression:** Callback mismatch, denied consent, offline token fetch, stale session, StrictMode render, successful refresh.

### P2-12 — Frontend is one large eager bundle and Schedule is request-heavy

- **User/operation:** Cold load on constrained device/network.
- **Location:** Eager route imports in `frontend/src/App.tsx:14-23` and Schedule loading effects.
- **Reproduction:** Build emits 2,673.28 kB JS/821.18 kB gzip. Mocked cold Schedule issued 28 requests desktop/tablet and 30 mobile.
- **Expected/actual:** Expected route-level loading and bounded fan-out; all pages load eagerly and Schedule performs many calls.
- **Impact:** Slower initial load and API pressure are likely. Counts include development StrictMode duplication.
- **Evidence/confidence:** Local build high confidence; mocked request-count production relevance medium.
- **Smallest fix:** Lazy-load routes, then profile and batch/cache Schedule using production-mode traces.
- **Regression:** Compare chunks, cold transfer, production request count, and route behavior.

### P2-13 — Production Python installs are not fully reproducible

- **User/operation:** Deployment/rebuild/recovery.
- **Location:** `popcore_app/requirements.txt`.
- **Reproduction:** Flask-Cors, python-jose, requests, APScheduler, scikit-learn, and google-auth are not exactly pinned.
- **Expected/actual:** Expected reviewed lock/constraints; future installs can resolve a different tree.
- **Impact:** Behavior can change without a source change and exact rollback is harder.
- **Evidence/confidence:** Source-confirmed, high confidence.
- **Smallest fix:** Generate a reviewed production constraints/lock file while retaining readable direct requirements.
- **Regression:** Two clean installs resolve identically and pass tests/build/smoke.

### P3-01 — Installed ECDSA dependency has a timing advisory

- **User/operation:** Transitive `ecdsa==0.19.2` from python-jose extras.
- **Location:** Installed D:-local environment resolved from `popcore_app/requirements.txt`.
- **Reproduction:** `pip-audit` reports `PYSEC-2026-1325` / CVE-2024-23342 / GHSA-wj6h-64fc-37mp with no fixed version.
- **Expected/actual:** Expected no vulnerable unused backend; inspected app JWT use is RS256 via cryptography, so vulnerable ECDSA signing appears unreachable.
- **Impact:** Low currently; changes if ECDSA signing is added/selected.
- **Evidence/confidence:** Local advisory plus source reachability; high presence confidence, medium-high current non-reachability.
- **Smallest fix:** Remove the extra if unnecessary or monitor upstream.
- **Regression:** Enumerate backend, run RS256 verifier tests, rerun `pip-audit`.

### P3-02 — Hidden database paths and dead code increase maintenance risk

- **User/operation:** Tests, tools, and database relocation.
- **Location:** Module-level `DB_PATH` values in `db.py`, `export_excel.py`, `ranker.py`, `scraper.py`, `insights.py`; unused `blueprints/users.py:370-387`.
- **Reproduction:** Safe fixtures must rebind several aliases; `cleanup_employees` has auth decoration but no route/caller.
- **Expected/actual:** Expected one explicit DB boundary and no unreachable handler-like function.
- **Impact:** Tests can target the wrong DB and dead code misleads maintenance.
- **Evidence/confidence:** Source-confirmed, high confidence.
- **Smallest fix:** Remove the unused ~18 lines and consolidate DB access only when touching those modules.
- **Regression:** Run app/tools on a temporary configured DB and assert normal DB untouched.

### P3-03 — Frontend emits recurring framework warnings

- **User/operation:** Development and future framework upgrades.
- **Location:** Modal/Card/Form/InputNumber usages across frontend pages.
- **Reproduction:** Browser run records deprecated `Modal.destroyOnClose`, `Card.bodyStyle`, disconnected `useForm`, `InputNumber.addonAfter`, and `findDOMNode` warnings.
- **Expected/actual:** Expected a clean console; pages render with repeated warnings.
- **Impact:** Upgrade risk and noisy diagnostics.
- **Evidence/confidence:** Fixture/mocked browser, high confidence.
- **Smallest fix:** Replace each deprecated API when its component is next changed; prioritize disconnected form lifecycle errors.
- **Regression:** Promote warnings to failures for migrated component smoke tests.

## Confirmed strengths

- Empty and representative legacy migrations are repeatable and preserved tested rows.
- A mixed valid/invalid daily report rolled back stock and history atomically.
- A two-store transfer changed only the selected store.
- Every registered protected API rule/method rejected anonymous access.
- Representative viewer-to-staff, staff-to-manager, and manager-to-admin escalations returned 403.
- Valid JWTs were accepted; expired, wrong-audience, and wrong-algorithm tokens were rejected.
- Invalid public iCal token returned 404; token generation/reset uses strong random values in source.
- Populated and empty synthetic pages rendered at three widths without page-level crashes or body-wide overflow.

## Open business-rule questions

- Alias creation is open to any authenticated role while deletion is manager-only. Confirm who may change global matching behavior.
- A unique employee/date shift can be moved across stores by upsert while conflict logic discusses cross-store overlap. Confirm whether multiple same-day store shifts are supported.
- Confirm whether sales outside cash/POS categories belong in theoretical inventory.
- Confirm the intended timezone/business date for scheduled insights.
- Confirm which fields may be sent to Anthropic and Sentry.

## Production verification required

- Record deployed commit, working directory, runtime/package versions, and environment variable names without exposing values.
- Compare active systemd/Gunicorn/nginx/TLS/proxy/header/upload/scheduler settings with templates.
- Check firewall, disk/inodes, SQLite/WAL permissions, logs, restarts, and scheduler ownership.
- Verify backup freshness, encrypted off-host copies, retention, and isolated restore integrity.
- Test Auth0 login/refresh/logout and issuer enforcement with a non-destructive account.
- Verify integration failure behavior using sandbox accounts/recorded responses before live mutations.
- Measure representative production-shaped synthetic data; local mocked timings are not a droplet capacity result.

## Exact development commands

```powershell
Set-Location D:\dev\POPCORE

# First-time backend setup
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r popcore_app\requirements.txt -r requirements-dev.txt

# Frontend dependencies
Set-Location popcore_app\frontend
npm ci
Set-Location ..\..

# Backend terminal
$env:FLASK_DEBUG = '1'
.\.venv\Scripts\python.exe popcore_app\app.py

# Frontend terminal
Set-Location D:\dev\POPCORE\popcore_app\frontend
npm run dev

# Tests
Set-Location D:\dev\POPCORE
.\.venv\Scripts\python.exe -m unittest discover -s popcore_app\tests -v
Set-Location popcore_app\frontend
npm test

# Normal production frontend build
npm run build
```

The frontend needs valid local Auth0 variables and callback/logout/web-origin entries for `http://localhost:5173`. The backend needs its Auth0 domain/audience and normal environment settings. Real values remain in ignored local environment files. The local database may remain empty while development uses synthetic fixtures.

Detailed probes, redacted results, screenshots, and isolated build are under `.local/audit/`. The audit did not change application source, dependency locks, built release assets, live data, external accounts, or production infrastructure.
