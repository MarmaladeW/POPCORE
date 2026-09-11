# Build 1: foundation repair implementation plan

> **For agentic workers:** Use superpowers:executing-plans task by task when implementation is requested. Read the linked design and repository AGENTS.md first. This document authorizes planning only; it does not authorize subagents, commits, pushes, deployment, or production-data access.

**Goal:** Make the current local application safe enough to build on by containing audited stock corruption, enforcing valid inputs and authentication, exposing failed loads honestly, and establishing repeatable checks.

**Architecture:** Keep the existing Flask blueprints, SQLite database, React pages, and Auth0. Apply small corrections to the current paths; the inventory command model belongs to Build 2. Reuse unittest, Node's test runner, and the already used Python Playwright package.

**Tech Stack:** Python 3.13, Flask, SQLite WAL, React 18, TypeScript, Vite, Ant Design, Auth0 RS256.

**Spec:** [In-store website design](../specs/2026-09-07-instore-website-design.md), sections 5, 7, 8; [master roadmap](2026-09-07-instore-website-plan.md), Phase A / Tasks 1–3; [audit](../../audits/2026-09-07-overall-project-audit.md).

**Status:** Planned, not implemented. Inspected 2026-09-08 at main / 8630199ac780059f5cb185a85c6efd0ac4597b91. Recheck the checkout before execution.

## Scope and completion

This is the first of the two foundation builds, not the first two screens. Deliver a working version of the existing app with the repairs below. Build 2 starts only after this build's completion gate.

Schedule stays unchanged: no redesign, shift-validation patch, calendar performance work, employee scheduling setting change, or calendar CSS change. Shared authentication and dependency changes require Schedule regression checks.

Role-specific Today, with staff's own assigned shifts first, remains required in roadmap Task 10. Do not bring that redesign, receiving screens, sales/tender redesign, closing, trades, integrations, or supplier work into this build.

## Global Constraints

- Work in D:/dev/POPCORE. Keep environments, dependencies, test databases, evidence, caches, and build output on D:.
- Preserve setup changes, source IDs, real data, existing environments, and committed production assets.
- No live service mutation, droplet data download, deployment, or external channel stock update during local implementation.
- Use synthetic data and blocked external network calls for mutation tests. Set DISABLE_SCHEDULER before app import; app import otherwise runs migrations/jobs.
- Preserve the reviewed audit as history. Revalidate each finding against actual files/functions before implementing.
- No forced dependency fixes, wholesale framework rewrites, accounting automation, or fabricated business policies.
- Files listed below are relative to D:/dev/POPCORE. New paths are proposed and must be checked for collisions.
- Capture existing staged and unstaged changes before execution. Do not stage broad directories or commit without an explicit request.

## Source corrections that govern implementation

| Verified source | Consequence |
| --- | --- |
| blueprints/restock.py: delete_restock_session is staff-accessible and removes completed history | Protect the actual staff path; do not test only manager access |
| sales.py: _revert_report_day explicitly permits clamped reversal overshoot | Never use its requested transaction quantity as proof of the actual old balance change |
| Report sales sections only update daily_sales; stock_in/out and break_display change stock | Preserve that separation in this build; do not start deducting aggregate sales |
| complete_restock_session uses min(found, available) | Shortage must leave the session uncompleted and unchanged, rather than quietly completing fewer units |
| stock_movements written during completion omits store_id | Include the actual session store in new history; do not relabel uncertain old rows |
| init_db.py currently imports catalog rows, not stock balances | Test catalog preservation; do not invent a stock-import path |
| app.py runs migrations on import; db.DB_PATH is fixed | Tests must bind disposable paths before importing the application |
| Product images are uploads/hidden_imgs; frontend API calls mostly live in pages | Use the existing paths, not fictitious routes or API files |
| The contention probe used an artificial barrier | Do not claim it proved ordinary production concurrency failure |

## Task 1: isolated reproductions and strict write boundaries

**Files:** create popcore_app/tests/support.py, test_sales_validation.py, test_stock_integrity.py; create popcore_app/validation.py only for validators reused by sales, stock, and restock. Modify those three blueprints. Read db.py and the existing test_employee_schedulable.py fixture pattern.

**Interfaces:**

- support.IsolatedApiCase provides self.client, self.product_id, self.store_id, self.headers(role='staff'), and self.snapshot(tables). snapshot returns ordered row tuples for the explicitly supplied tables.
- Each fixture gets a new database under .local/tmp. Register only the blueprints under test on Flask(__name__), run db.migrate_db against that fixture, and restore db.DB_PATH afterwards. Bind imported upload-path aliases to a temporary upload directory.
- validation.read_int(value, field, minimum=0) returns a bounded SQLite integer or raises ValueError with the field name. Missing optional fields receive explicit defaults at the route, not inside the validator.
- Accept JSON integers and whole decimal strings already supported by forms; reject booleans, floats including 1.0, empty strings, signs on strings, negative quantities, null, collections, and integers above 2**63-1. Check multiplication/aggregation overflow too.
- Mutation responses retain error for existing clients and add code, field, and line when useful: 400 invalid_input, 404 missing resource, 409 insufficient_stock. Busy behavior is specified in Build 2.

- [ ] Record branch, HEAD, remote, staged/unstaged diffs, and hashes for Schedule sources and popcore_app/static. Leave the pre-existing setup edits untouched.
- [ ] Create the disposable fixture and a guard that rejects a test database path outside the test directory. Patch external request entry points to fail on unexpected calls; mock Auth0 decoding only for route authorization tests.
- [ ] Add the quantity regression below to upsert, batch_upsert, report submission, stock receipt/adjustment, batch stock operations, and restock item/pick inputs.
- [ ] Run the focused tests against the baseline and record the expected failures, without running .local audit scripts against normal data.
- [ ] Implement the shared validator and validate every line before the first write. Validate ISO business dates for these non-Schedule mutations with date.fromisoformat; preserve Schedule validation.
- [ ] Remove silent continue/default-to-one behavior for malformed lines. A batch with one invalid line commits nothing and identifies that line.
- [ ] Rerun the focused tests and preserve positive cases: zero is allowed for a recorded sales quantity/count, but a movement quantity must be greater than zero.

Required test shape, using the fixture above:

~~~python
def test_negative_sale_leaves_all_rows_unchanged(self):
    before = self.snapshot(("daily_sales", "stock", "stock_transactions"))
    response = self.client.post(
        "/api/sales/upsert", headers=self.headers(),
        json={"product_id": self.product_id, "store_code": "DT",
              "date": "2026-09-08", "qty_sold": -5},
    )
    self.assertEqual(response.status_code, 400)
    self.assertEqual(response.get_json()["code"], "invalid_input")
    self.assertEqual(self.snapshot(
        ("daily_sales", "stock", "stock_transactions")), before)
~~~

Core validator implementation:

~~~python
def read_int(value, field, minimum=0):
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        value = int(value)
    if type(value) is not int or not minimum <= value <= 2**63 - 1:
        raise ValueError(f"{field} must be an integer >= {minimum}")
    return value
~~~

**Focused check, repository root:**

~~~powershell
.\.venv\Scripts\python.exe -m unittest discover -s popcore_app/tests -p "test_sales_validation.py" -v
~~~

**Gate:** mixed valid/invalid batches, missing products, invalid stores including ALL, bad dates, and overflow all fail before any persistent effect; legitimate existing submissions still work.

## Task 2: stop destructive stock changes and unsafe report replacement

**Files:** modify blueprints/restock.py (complete_restock_session, delete_restock_session), sales.py (batch_stock_operation, submit_daily_report, _revert_report_day, deletion paths), stock.py (movement paths, delete_stock_rows), products.py (bulk_delete_products). Extend test_stock_integrity.py. Make only the conflict-display changes needed in frontend Stock/BatchStockModal.tsx, Restock/HistoryTab.tsx, and Sales/DailyReportEntry.tsx.

**Decision:** In this short containment build, block replacement of any existing day with report-generated stock transactions. Current records do not reliably prove applied deltas. This also applies to stock-affecting reports created during Build 1; precise correction is delivered through Build 2's new documents. Sales-only report replacement remains available. Explain the limitation in the UI and runbook.

- [ ] Reproduce: upstairs 10, complete replenishment 5, consume floor 5, then delete the completed session. Baseline incorrectly returns total stock to 10; repaired behavior returns 409 completed_restock_retained and keeps total 5 and all history.
- [ ] Reproduce: floor 2, submit stock_out 5. Require 409 insufficient_stock and unchanged stock/report/history. Seed a legacy overdrawn report separately; replacement must return 409 reconciliation_required and change nothing.
- [ ] Begin the transaction before reading mutable balances/session status; use conditional SQL updates and check rowcount. Do not rely on read-then-write outside one transaction.
- [ ] Validate the full completion batch against availability; any shortage rolls back all changes and leaves the session picking/submitted. Include store_id on both new stock_movements rows.
- [ ] Reject completed session deletion before touching any table. Preserve draft deletion behavior and test pending/submitted/picking sessions with no posted movements.
- [ ] Inspect prior report transactions before deleting daily_sales. If any report stock transaction exists, reject replacement. If none exists, replace the sales-only aggregate atomically.
- [ ] Keep clear_day and individual sales-row deletion aggregate-only. They must not manufacture inverse stock operations. Prevent them from bypassing the report containment: when a day has report stock history, return reconciliation_required.
- [ ] Block deletion of a stock row with nonzero stock or history and product deletion with sales/stock/restock/count history. Validate an entire selected batch before deleting database rows or image files.
- [ ] Show server conflict text in the existing actions and retain drafts; no new correction screen in this build.

Conditional movement example (run both sides and history in the same transaction):

~~~python
con.execute("BEGIN IMMEDIATE")
changed = con.execute(
    """UPDATE stock
       SET upstairs_qty = upstairs_qty - ?, instore_qty = instore_qty + ?
       WHERE product_id = ? AND store_id = ? AND upstairs_qty >= ?""",
    (qty, qty, product_id, store_id, qty),
).rowcount
if changed != 1:
    con.rollback()
    return jsonify(error="Insufficient upstairs stock",
                   code="insufficient_stock"), 409
~~~

Required fixture assertion after completing, consuming, and requesting deletion:

~~~python
self.assertEqual(response.status_code, 409)
self.assertEqual(self.snapshot(
    ("stock", "restock_sessions", "restock_items",
     "stock_movements", "stock_transactions")), before_delete)
~~~

Also prove duplicate completion never moves stock twice, concurrent state changes cannot pass stale completion checks, and a failing second line rolls back the first. Future lifecycle improvements and idempotency contracts belong to Build 2.

**Check:** unittest discovery with pattern test_stock_integrity.py.

**Gate:** no stock creation through delete/replace, no clamping or silent partial completion, and every retained record remains accessible.

## Task 3: reliable Auth0 verification and recovery

**Files:** modify popcore_app/auth.py; frontend/src/auth/ProtectedRoute.tsx, src/api/client.ts, src/App.tsx. Create tests/test_auth.py. Browser coverage is in Task 5.

**Interfaces:** retain the existing role claim and role hierarchy. Make setTokenGetter accept a function or null so logout/unmount clears it. Token acquisition failure rejects the request locally; it never sends an anonymous fallback. Preserve Axios error information and error for existing consumers.

- [ ] Generate temporary RSA keys with the installed cryptography backend. Stub JWKS retrieval, not _decode_token, for JWT tests.
- [ ] Test valid, expired, wrong audience, missing/wrong issuer, wrong algorithm, malformed header, missing/unknown kid, rotation, and unavailable JWKS.
- [ ] Require issuer=https://{AUTH0_DOMAIN}/ as well as the existing audience and RS256 allowlist. Require expiration and subject claims for API identity.
- [ ] Keep one refresh for an unknown kid; classify provider transport failure separately from an invalid token. Invalid credentials return 401; authenticated wrong role returns 403; unavailable key retrieval returns a sanitized retriable 503, not a false session-expired response.
- [ ] Start login from a guarded effect, not render. A ref prevents duplicate StrictMode redirect starts. An explicit retry may reset that guard after an actionable error.
- [ ] Surface SDK consent/configuration/token/network errors and retain the current page/draft. Do not automatically reload on every 401. Show one actionable reauthentication notice for an expired login and a different message when the API rejects a fresh token.
- [ ] Clear token getter and sensitive loaded data when identity changes. Align audience with the existing public Auth0 configuration rather than adding another hardcoded audience.
- [ ] Prove blocked token acquisition produces zero /api requests; 403 and network failures cannot trigger a login loop.

JWT implementation boundary:

~~~python
return jose_jwt.decode(
    token, key, algorithms=["RS256"], audience=AUTH0_AUDIENCE,
    issuer=f"https://{AUTH0_DOMAIN}/",
    options={"require_exp": True, "require_sub": True, "require_iss": True},
)
~~~

Representative real-verifier test after creating a valid locally signed token:

~~~python
claims["iss"] = "https://another-tenant.invalid/"
token = jose_jwt.encode(claims, private_key, algorithm="RS256",
                        headers={"kid": "fixture-key"})
with self.assertRaises(Exception):
    auth._decode_token(token)
~~~

**Check:** unittest discovery with pattern test_auth.py; Task 5's browser auth checks.

**Gate:** no authentication bypass, tenant confusion, anonymous fallback, or repeated redirect loop. Existing viewer/staff/manager/admin Schedule access remains intact.

## Task 4: product images, spreadsheet export, and private input boundaries

**Files:** modify app.py, db.py:esc_csv, blueprints/products.py:upload_hidden_image, llm_parser.py and its sales.py caller; backend requirements if an image decoder is needed. Create tests/test_uploads.py, test_exports.py, test_private_inputs.py.

**Interfaces:** product-image upload retains its current response shape and route. Return 400 invalid_image, 413 image_too_large, or 404 missing_product without creating an attachment. CSV text safety is centralized in esc_csv; numeric values retain their numeric representation.

- [ ] Add fake-JPEG, truncated-image, valid format, oversized bytes, excessive dimensions, missing product, and failed database insert cases.
- [ ] Use a real image decoder. Add Pillow only if no supported decoder is already available; file signatures alone are insufficient. Preserve currently accepted valid JPEG/PNG/GIF/WebP/AVIF where the chosen supported build can decode them.
- [ ] Proposed limits: 10 MiB file, 40 million decoded pixels; 12 MiB multipart request limit scoped to this upload route. Check existing asset metadata for conflicts before applying the limits. Do not impose a 12 MiB limit on spreadsheet/report imports.
- [ ] Verify then fully decode within the limits; bound animation decoding too. Derive extension from detected format, generate the filename, and check the product before creating its directory. Use a temporary file in the upload directory; remove only this request's new file if validation/database insertion fails.
- [ ] Test text starting with =, +, -, @, tab, carriage return, and leading whitespace before a formula trigger. Prefix dangerous textual cells with a single quote before CSV quoting. Preserve real numeric -2 and 12.5; do not exempt a string merely because it looks numeric.
- [ ] Set send_default_pii=False. Strip authorization/cookie headers, user identity, report bodies, and query secrets from telemetry payloads/breadcrumbs in a narrow scrubber; verify captured events against canary secrets.
- [ ] Keep LLM parsing explicitly optional. Require deployment opt-in plus an explicit per-parse choice; default to the rule parser and manual review. Explain which raw report text would be sent before that choice; do not include images, credentials, or unrelated records.
- [ ] Test rule-parser fallback when disabled, no API key, timeout, invalid schema, or no usable model output. Unexpected external requests must fail the tests.

CSV assertions:

~~~python
self.assertEqual(db.esc_csv("=1+1"), "'=1+1")
self.assertEqual(db.esc_csv("  @SUM(A1:A2)"), "'  @SUM(A1:A2)")
self.assertEqual(db.esc_csv(-2), "-2")
self.assertEqual(db.esc_csv('name,"quoted"'), '"name,""quoted"""')
~~~

Decoder sequence: bounded read -> Image.open -> dimension/frame-budget check -> verify -> reopen/load -> persist validated bytes. Do not add an image transformation feature.

**Checks:** unittest discovery for each of the three new files; route tests cover every CSV exporter that calls esc_csv.

**Gate:** rejected uploads leave no row/file, formula text is inert, and credentials/report text are absent from default telemetry and disabled LLM calls.

## Task 5: honest failed loads and durable browser checks

**Files:** modify frontend/src/App.tsx and pages/Dashboard/index.tsx, Stock/index.tsx, Products/index.tsx, Sales/index.tsx plus Sales/DailyReportEntry.tsx only where draft retention/conflict messages require it. Create popcore_app/tests/browser/mock-auth.tsx, vite.config.mjs, fixtures.py, check_foundation.py. Reuse useful patterns from .local/audit/mock-auth.tsx, vite-audit.config.mjs, browser_audit.py; do not depend on ignored files.

**Interfaces:** the browser runner starts a separate loopback Vite process on port 5174 with strictPort, intercepts every /api request, and aborts external requests. Test-only Auth0 alias exists solely in the explicit test config, never the normal Vite config or application runtime.

- [ ] Create real assertions for initial failure, successful empty response, stale last-good data after refresh failure, and retry recovery on each of the four pages.
- [ ] Extend the fixture auth module with controlled loading, authenticated role, token rejection, and redirect counters. Use it to prove Task 3's browser behavior.
- [ ] On initial failure, render an error and retry; do not show operational zeros. On refresh failure, keep only data belonging to the same user/store/query and mark it stale with last-success time.
- [ ] On store/account change, clear old data and cancel/ignore obsolete responses. A delayed DT response must never overwrite an MK view.
- [ ] Handle bootstrap failures in App.tsx without inventing store lists or swallowing an unavailable store scope. Retrying refreshes the failed bootstrap request.
- [ ] Keep draft entry after 400/409/network failure. Disable repeated in-flight submission; show the actual outcome without automatically replaying a stock mutation.
- [ ] Use existing Alert/Result/Spinner/Button patterns and accessible retry controls. No dashboard redesign, new navigation, global CSS cleanup, or Schedule page edits.
- [ ] Add Schedule regression scenarios: role-gated navigation, personal shifts, manager existing shift operations, store switch, and existing layout at 390px and desktop. A test fixture is not proof of live Auth0; report the manual login smoke separately.

Representative browser assertion:

~~~python
page.goto(base_url + "/stock")
expect(page.get_by_role("alert")).to_contain_text("Unable to load")
expect(page.get_by_text("No stock", exact=True)).to_have_count(0)
page.get_by_role("button", name="Retry", exact=True).click()
expect(page.get_by_role("alert")).to_have_count(0)
~~~

The runner must terminate only the server process it started, fail if the port is occupied, launch its Windows helper without a visible console window, capture failed screenshots under .local/build-1/browser, and exit nonzero for failed assertions. A screenshot or printed metric alone is not a passing test.

**Check, repository root:**

~~~powershell
.\.venv\Scripts\python.exe popcore_app/tests/browser/check_foundation.py
~~~

**Gate:** failed/empty/stale states remain distinct, drafts survive recoverable failures, wrong-store responses are discarded, and test auth cannot enter a normal/release build.

## Task 6: dependency fixes, reproducibility, and CI

**Files:** frontend/package.json, package-lock.json; popcore_app/requirements.txt, requirements-dev.txt; create requirements-windows.constraints.txt and requirements-linux.constraints.txt for reproducible supported targets; create .github/workflows/checks.yml and docs/development-build-1.md. Update AGENTS.md/development-windows.md with verified commands.

- [ ] Capture fresh npm and Python advisory reports in .local/build-1. Record advisory IDs, versions, dependency chains, reachable use, and proposed resolution. The audit's historical counts are not a current verdict.
- [ ] Apply compatible security updates first and inspect lockfile differences. Treat a required Vite major update as its own review/test checkpoint; do not use npm audit fix --force.
- [ ] Investigate ecdsa through python-jose's actual RS256 backend. Do not uninstall a required transitive package or claim an advisory fixed merely because this app uses RSA. Document any remaining supported-resolution limitation and reachability evidence.
- [ ] Create fresh disposable D:-local environments to generate/test constraints; preserve the current .venv and node_modules until the replacement set passes. Use Linux CI for Linux resolution; Windows-only success does not prove Gunicorn deployment.
- [ ] Keep Node 24 for the direct TypeScript tests. Retain all existing Schedule tests; add explicit test paths to the npm script only when new Node test files actually exist.
- [ ] Add CI on pull_request and push using supported checkout/setup actions pinned to reviewed full SHAs at execution time. Grant contents: read only and provide no production secrets.
- [ ] Run pip check, unittest discovery, npm ci, npm test, and the separate frontend verification build on Windows and Linux. Run the isolated Python Playwright checks in one browser job with local fixtures. Store artifacts on failure; never publish a release build automatically.
- [ ] Record constraints used, supported Python/Node versions, exact check commands, remaining advisory exceptions, and the temporary report-correction limitation.

CI command sequence, with OS-appropriate local interpreter and temporary directories. Run the Python commands from the repository root and npm commands from popcore_app/frontend:

~~~text
python -m pip install -r requirements-dev.txt -c requirements-windows.constraints.txt
python -m pip check
python -m unittest discover -s popcore_app/tests -v
npm ci
npm test
npm run build -- --outDir ../../.local/frontend-build
python popcore_app/tests/browser/check_foundation.py
~~~

The Linux job uses requirements-linux.constraints.txt in the install command. Browser installation belongs to the test environment, never a new runtime dependency.

**Gate:** supported target installations are reproducible; no untriaged reachable high/critical advisory remains. Explicitly distinguish resolved advisories, accepted limitations awaiting a decision, and checks not yet run.

## Final verification and handoff

Set these variables before running tests so temporary files remain on D:

~~~powershell
Set-Location D:\dev\POPCORE
New-Item -ItemType Directory -Path .local\tmp -Force | Out-Null
$env:TEMP = 'D:\dev\POPCORE\.local\tmp'
$env:TMP = $env:TEMP
$env:DISABLE_SCHEDULER = '1'
$env:PLAYWRIGHT_BROWSERS_PATH = 'D:\dev\POPCORE\.local\ms-playwright'
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m unittest discover -s popcore_app/tests -v
.\.venv\Scripts\python.exe popcore_app/tests/browser/check_foundation.py
Set-Location popcore_app\frontend
npm test
npm run build -- --outDir ../../.local/frontend-build
Set-Location D:\dev\POPCORE
git -c safe.directory=D:/dev/POPCORE diff --check
git -c safe.directory=D:/dev/POPCORE status --short --branch
~~~

- [ ] Compare protected Schedule and static hashes to the baseline. Explain any shared-file differences; no generated release asset change is expected.
- [ ] Review every changed line against Tasks 1–6; leave unrelated audit findings on the master roadmap.
- [ ] Deliver docs/development-build-1.md with tests/exit results, failed-baseline evidence, dependency decisions, and unresolved production-only checks.
- [ ] Demonstrate: sign in -> load stock -> reject invalid/insufficient mutation -> preserve a completed restock -> recover from a failed page load -> open unchanged Schedule.
- [ ] Report source readiness separately from live Auth0, Linux CI, and production readiness. Do not declare Build 1 complete if a required local gate failed or was skipped.
- [ ] Hand off to [Build 2](2026-09-08-build-2-inventory-core.md). Do not implement later workflows as incidental cleanup.

## Requirement coverage

| Master scope | This plan |
| --- | --- |
| Task 1: corruption containment and sibling inputs | Tasks 1–2 |
| Task 2: auth, inputs, error states, privacy | Tasks 3–5 |
| Task 3: dependencies and runtime verification | Task 6 and final gate |
| Schedule freeze and preservation | Global constraints, Tasks 3/5, final hash check |
| Today role differences and staff shifts first | Retained in master Task 10; outside this build |
