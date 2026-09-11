# POPCORE Overall Project Audit Plan

**Goal:** Produce a prioritized, reproducible assessment of correctness, access control, data integrity, usability, performance, maintainability, and operational readiness before feature development.

**Execution:** This is an audit plan, not authorization to implement fixes. Execute the checklist in order when the user requests the audit. No commits, pushes, deployments, production data downloads, or live account changes are included. Use the writing-plans workflow as a review checklist; implementation and commit steps do not apply to this audit.

**Architecture:** Review the actual Flask APIs, SQLite migrations and transactions, React/TypeScript UI, Auth0 integration, and Linux deployment templates. Combine source inspection, existing tests, focused synthetic-data reproductions, and browser checks. Source review alone must not be presented as a working production check.

**Tech stack:** Flask, SQLite WAL, Python 3.13 development environment, React 18, TypeScript, Vite 5, Ant Design, Tailwind, Zustand, FullCalendar, Auth0, APScheduler, Playwright; Gunicorn/nginx/systemd in production.

**Requirements:** User requested an overall check before development, declined copying droplet data, and requires project work on D:. Follow AGENTS.md and PRODUCT.md. Use docs/development-windows.md for baseline commands, but recheck current environment values and running processes rather than assuming historical setup notes are current.

## Scope and baseline

- Checkout: D:\dev\POPCORE, origin https://github.com/MarmaladeW/POPCORE.git.
- Planning snapshot: main at 8630199ac780059f5cb185a85c6efd0ac4597b91. Existing uncommitted setup work: .gitignore, README.md, AGENTS.md, requirements-dev.txt, docs/development-windows.md, and staged index-only removal of popcore_app/server.log. The log remains on disk. Preserve these changes.
- Local login is working according to the user. The local database does not contain the droplet's business data. Do not mistake empty screens for evidence that populated workflows work.
- Earlier setup ran 10 Python and 14 frontend tests, plus a production build. Those are historical baseline results, not this audit's results. Existing tests focus on scheduling and are not whole-project coverage.
- Local Auth0 uses the existing tenant. User creation, deletion, role changes, and identity synchronization may reach real accounts. Exercise these against test doubles only. Do not use the running user's authenticated browser for destructive tests.
- Keep audit evidence and disposable fixtures in .local/audit/ on D:. Never put tokens, cookies, passwords, real employee records, or credential values in reports or screenshots.
- Use synthetic data in an isolated test database. App import runs migrations; modules also retain database-path aliases. Bind every database path before import and assert the actual resolved paths. If isolation cannot be demonstrated, use a disposable source copy under .local/audit/ with its own database. Never seed or reset the user's running database.
- Disable scheduler execution in tests, intercept external service calls, and reject unexpected network requests. Synthetic role tests must not weaken authentication in application source. JWT verification tests must exercise the real verifier with local test signing keys.
- Production access remains out of scope. Review deployment source locally and record separate live verification needs.

## 1. Reproducible baseline and coverage inventory

**Inspect:** README.md, AGENTS.md, requirements-dev.txt, .gitignore, popcore_app/requirements.txt, popcore_app/frontend/package.json, package-lock.json, vite.config.ts, tsconfig files, app.py, tests/, docs/, .claude/launch.json, and tracked environment templates.

- [ ] Capture branch, commit, remote, existing diffs, dependency/runtime versions, and current server identities. Do not stop or replace an unrelated process.
- [ ] Inventory every Flask route and HTTP method, blueprint, user-facing page, external service, background job, and persistent data store. Include public calendar feeds, uploads, import/export, bulk actions, and settings.
- [ ] Read existing product and review documents as context; verify their claims against code. Record obsolete guidance, including environment loading and stale editor paths.
- [ ] Run the existing tests and an isolated production build with the commands below. Record exits, counts, and warnings. Distinguish existing failures from problems introduced by any audit harness.
- [ ] Map the existing tests to the inventory and identify untested high-impact paths. There is no configured npm lint script or pytest suite; do not invent passing checks.

**Deliverable:** A coverage inventory and reproducible baseline. Every route and page is assigned a review status, even if deeper exercise is deferred.

## 2. Authentication, authorization, and exposed inputs

**Inspect:** popcore_app/auth.py, app.py, all blueprints, frontend/src/main.tsx, App.tsx, api/client.ts, auth/, components/RoleGuard.tsx, and deployment headers.

- [ ] Trace login, callback, token retrieval, API header injection, 401/403 handling, logout, refresh, and tenant/audience configuration end to end. Include stale backend environment, denied consent, failed token acquisition, page refresh, and repeated login loops.
- [ ] Exercise the actual JWT verifier with valid test signatures, expired and malformed tokens, wrong audience/issuer/algorithm, unknown signing key, and key refresh failure. Inspect caching and timeout behavior; do not replace the verifier with a mock for these cases.
- [ ] Build an endpoint permissions matrix for anonymous, viewer, staff, manager, and admin. Check each write endpoint plus sensitive reads and exports. Compare API enforcement with frontend restrictions.
- [ ] Attempt cross-employee and cross-store access using synthetic users and direct requests; check identifiers supplied in paths, query parameters, and bodies. Document unclear intended role policy instead of silently deciding it.
- [ ] Check calendar-feed token secrecy, revocation/reset, exposure scope, and escaping. Check image/file path handling, upload type and size handling, SQL parameterization, CSV/spreadsheet formula injection, and HTML/rendering injection surfaces.
- [ ] Inspect CORS configuration, secrets in tracked files, logging of sensitive values, and error responses. Use filenames/redacted findings when inspecting sensitive material.

**Verification:** Denied actions return the appropriate status and leave database state unchanged. Each suspected security defect has a concrete affected path and reproduction or a clearly stated evidence limit.

## 3. Database correctness and stock/sales integrity

**Inspect:** popcore_app/db.py, init_db.py, export_excel.py, blueprints/stock.py, inventory.py, restock.py, sales.py, products.py, stores.py.

- [ ] Apply migrations to an empty fixture and representative older schemas, then apply them again. Inspect transaction boundaries, schema constraints, preserved rows, indexes, WAL behavior, foreign keys, and failure recovery.
- [ ] Build two-store fixtures with ordinary products, missing optional fields, duplicate SKUs, inactive records, low/zero stock, and representative pack/unit conversions.
- [ ] Trace stock receipts, transfers, deductions, inventory counts, restock requests/picking/completion, and sales reporting. Establish the intended arithmetic for each path from the code and product rules; flag ambiguity.
- [ ] Check zero/negative/extreme quantities, numeric strings versus numbers, duplicate submissions, stale edits, invalid references, partial batches, repeated completion, and failures halfway through a transaction.
- [ ] Verify daily-report replacement, individual record deletion, clear-day operations, and retries reconcile stock movements and summary totals without double deductions or orphaned history.
- [ ] Run focused concurrent requests against a disposable SQLite database for operations that read then write shared stock or session state. Measure lock/failure behavior and check final values, not only HTTP status.
- [ ] Inspect Excel import/upsert and export paths: missing files, changed headings, duplicate entries, Unicode, empty rows, and preservation of existing values. Never run imports against the user's database.

**Verification:** Before/after database assertions show correct quantities and totals, atomic failure behavior, intact relationships, and repeat-safe behavior where required. Report any missing business rule as an open question, not a proven defect.

## 4. Scheduling, employee settings, and reporting

**Inspect:** popcore_app/blueprints/schedule.py, users.py, settings.py; frontend/src/pages/Schedule/, Users/, Settings/; existing scheduling tests.

- [ ] Exercise shift create/edit/delete, employee availability, store membership, coverage, conflicts, trainees, colors, and the schedulable toggle.
- [ ] Cover same-day/cross-store overlaps, invalid ranges, overnight shifts if supported, inactive or disabled employees with existing assignments, and deleted references.
- [ ] Verify configured opening hours, staffing requirements, positions, shift presets, period notes, and manual checklist persistence; ensure one setting does not silently change unrelated settings.
- [ ] Check monthly and wage-period boundaries, Toronto timezone/DST, month lengths, leap day, rounding, and agreement between displayed totals, API reports, and calendar exports. These are software calculation checks, not a legal payroll compliance audit.
- [ ] Check failed saves and optimistic rollback, repeated taps, stale UI state, reloading, and visibility differences between manager and employee views.

**Verification:** Defined examples produce consistent API/database/UI outcomes, and the existing employee-color and scheduling semantics remain intact.

## 5. External integrations, parsing, and background work

**Inspect:** popcore_app/scraper.py, matcher.py, ranker.py, llm_parser.py, insights.py, app.py scheduler, Auth0 Management helpers, products.py Google Sheets handling, users.py identity synchronization.

- [ ] Trace request construction, timeout/retry policy, result validation, partial success, cache invalidation, and database effects.
- [ ] Use recorded synthetic responses for service failures, rate limits, missing fields, malformed responses, slow requests, and unavailable credentials. Verify a failed optional service does not corrupt local records or silently report success.
- [ ] Review product matching and daily-report parsing for ambiguous names, aliases, duplicates, unit/pack interpretation, confidence handling, and rule-parser fallback.
- [ ] Check optional LLM parsing for schema validation and prompt-injection consequences; treat generated data as untrusted input to inventory operations.
- [ ] Check scheduler ownership, startup/restart behavior, duplicate work, date handling, error visibility, and whether jobs can block request handling.
- [ ] Record which behaviors remain unverified without a sandbox service account or real integration responses. Do not call real Management API mutations, synchronize business data, send messages, or scrape external stores as an incidental test.

**Verification:** Test-double scenarios demonstrate predictable failure/recovery behavior; live-only assumptions are listed separately.

## 6. Whole-interface usability, accessibility, and performance

**Inspect:** All routed frontend pages, components/AppLayout.tsx, shared UI components, api/client.ts, store/index.ts, index.css, and PRODUCT.md. Use Impeccable's audit/critique guidance during execution; preserve the existing product identity.

- [ ] Visit Dashboard, Products, Stock, Restock, Sales/day detail, Users, Settings, and Schedule, including role-denied and unknown routes.
- [ ] Test empty, populated, dense, loading, error, and offline states with synthetic fixtures. Separate fixtures/mocked-browser evidence from authenticated end-to-end evidence.
- [ ] Check desktop 1440px, tablet 768px, and mobile 390px layouts, plus 200% zoom. Include long product/employee names, bilingual text, large lists, and narrow calendar cells.
- [ ] Check keyboard access, focus order and modal focus return, accessible labels, contrast, touch targets, overflow, reduced motion, and information conveyed only by color. Supplement automated checks with manual review.
- [ ] Check discoverability of primary actions, destructive-action recovery, validation copy, notifications, and consistency between mobile navigation and desktop navigation. Prioritize operational speed and a visible schedule.
- [ ] Measure API request counts, repeated fetches, query plans, pagination, list rendering, and cold/warm page loads with documented synthetic dataset sizes. Inspect the existing large bundle warning, unnecessary eager imports, and runtime/console errors.
- [ ] Treat performance observations as local measurements with stated hardware/data limits. Do not infer droplet latency or capacity from the empty local app.

**Deliverable:** Page/state coverage with screenshots where useful, reproducible usability findings, and measured performance bottlenecks rather than aesthetic preferences or speculative optimizations.

## 7. Maintainability, dependencies, and operational readiness

**Inspect:** Large blueprints and frontend pages, shared validation, requirements, package lock, tests, setup_production.sh, popcore.service, gunicorn.conf.py, nginx.conf, backup.sh, logrotate.conf, and README deployment guidance.

- [ ] Check duplicated business rules, inconsistent validation/error formats, hidden module state, broad swallowed exceptions, unreachable behavior, and difficult-to-test boundaries. Suggest simplifications only where there is evidence of maintenance cost or bugs; no blanket rewrite.
- [ ] Run read-only dependency advisory checks for installed Python and locked npm dependencies. Record the advisory source/date, actual usage/reachability, and whether production or development is affected. Do not run automatic dependency fixes or update lockfiles.
- [ ] Review environment propagation, secret handling, service user permissions, scheduler/worker interaction, log rotation, restart/rollback instructions, HTTPS/proxy headers, static asset caching, and upload limits from source.
- [ ] Verify SQLite backup/restore semantics with disposable local fixtures, including WAL writes and post-restore integrity checks. Review shell retention/deletion paths without executing cleanup against real backups.
- [ ] Explicitly defer droplet OS/packages, running service configuration, real HTTPS headers, firewall, disk capacity, backup freshness/off-server copies, and production restore testing. Source templates do not prove these are deployed.

**Deliverable:** A development-readiness assessment and a separate production-verification checklist with prerequisites.

## 8. Findings, prioritization, and completion gate

- [ ] Consolidate duplicate findings and verify that each major conclusion follows from the supplied evidence.
- [ ] For every finding record: ID, priority, affected user/operation, exact file and line, reproduction, expected/actual result, impact, evidence type, confidence, proposed smallest fix, and a regression check.
- [ ] Use P0 for an immediate severe exposure/corruption supported by evidence; P1 for high-impact integrity/access/workflow defects; P2 for ordinary correctness/usability/reliability defects; P3 for lower-impact polish or maintainability. Keep severity separate from confidence and deployment exposure.
- [ ] Label evidence as source-confirmed, locally reproduced, fixture/mocked, or production verification required. Unsupported suspicions belong in the open-questions section.
- [ ] Finish the coverage matrix with checked, partial, or blocked status and a reason for each gap. Passing scheduling tests alone cannot close the overall audit.
- [ ] Recheck git diff and status against the captured baseline. Preserve setup changes, the user's database, environments, release assets, and active servers. Stop only audit-owned temporary processes.
- [ ] Deliver a concise top-priority action list, the full findings/coverage report, runnable evidence for important defects, and explicit production limits. Fixes and release work remain a later step.

**Completion:** Every route/page/subsystem in the inventory has a documented review disposition; important findings are reproducible or transparently limited; the final report distinguishes local readiness from deployment readiness. An audit does not guarantee the absence of bugs.

## Baseline commands for execution

Run these only when executing the audit, not merely writing this plan:

```powershell
Set-Location D:\dev\POPCORE
git status --short --branch
git remote -v
git rev-parse HEAD
New-Item -ItemType Directory -Path .local\audit, .local\tmp -Force | Out-Null
$env:TEMP = 'D:\dev\POPCORE\.local\tmp'
$env:TMP = $env:TEMP
$env:DISABLE_SCHEDULER = '1'
$env:PLAYWRIGHT_BROWSERS_PATH = 'D:\dev\POPCORE\.local\ms-playwright'
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m unittest discover -s popcore_app/tests -v
# Verify each command's exit before continuing; capture full logs under .local/audit.
Push-Location popcore_app\frontend
npm test
npm run build -- --outDir ../../.local/audit/frontend-build
npm audit --json --cache D:\dev\npm-cache
Pop-Location
git diff --check
git diff --cached --check
git status --short --branch
```

The normal npm run build overwrites tracked static assets, so the audit uses the output override above. An npm audit nonzero exit can indicate advisories; inspect the JSON rather than equating it with a build failure. If a Python advisory tool is needed, use an isolated D: environment and record that addition instead of changing production requirements.

## Planned artifacts

- Plan (this file): docs/superpowers/plans/2026-09-07-overall-project-audit.md.
- Execution report: docs/audits/2026-09-07-overall-project-audit.md; use the actual execution date if later.
- Local-only route/page coverage, redacted request evidence, screenshots, synthetic fixtures, logs, and focused reproduction scripts: .local/audit/.
- Application source, production assets, dependencies, live data, and external accounts: no changes as part of the audit.
