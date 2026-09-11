# Build 1 development and verification

Build 1 contains the foundation repairs from the 2026-09-08 plan. It does not redesign Schedule, add role-specific Today content, or implement the Build 2 inventory command model.

## Behavior delivered

- Quantity and date writes now reject malformed, fractional, negative, missing, and overflowing values before mutation. Batch requests validate every line before the first write.
- Stock-moving reports and restock completions use one immediate transaction. Insufficient stock rejects the whole operation. New restock movements include the session store.
- Completed restocks remain as history. Draft, submitted, and picking sessions with no posted movement can still be removed.
- A daily report with stock history cannot be replaced, cleared, or deleted through the old aggregate-sales paths. It returns `reconciliation_required`. Build 2 will provide the correction workflow.
- Stock rows with quantities or history, and products with operational history, cannot be bulk deleted.
- Auth0 tokens require the configured issuer, audience, subject, expiry, and RS256. JWKS transport failures return a retriable 503. Browser token acquisition failure sends no anonymous API request, and login redirect starts from an effect once.
- Dashboard, Products, Stock, and Sales distinguish initial failure, successful empty data, and stale last-good data. Retry does not discard same-scope data; account, store, and query changes clear old scope and ignore late responses.
- Uploaded product images are decoded with Pillow, bounded to 10 MiB and 40 million frame-pixels, stored atomically, and removed if the database insert fails.
- CSV text that spreadsheet software can interpret as a formula is neutralized. Numeric values stay numeric.
- Sentry default personal data is disabled and the event scrubber removes credentials, identity, query secrets, and report bodies. LLM parsing requires both deployment opt-in and an explicit request; local rule parsing remains the default.

## Baseline reproductions

The source-level baseline and synthetic-data reproductions are retained in `docs/audits/2026-09-07-overall-project-audit.md`. Before Build 1, the audit reproduced completed-restock deletion increasing total stock from 5 back to 10, report replacement increasing stock from 2 to 5 after an overdraw, negative sales being stored with a 200 response, and failed initial loads appearing as valid zero or empty data. The Build 1 regression suite exercises those boundaries against disposable databases and mocked browser data.

## Install

Use PowerShell from `D:\dev\POPCORE`. Keep temporary files and caches on D:.

```powershell
Set-Location D:\dev\POPCORE
New-Item -ItemType Directory -Path .local\tmp -Force | Out-Null
$env:TEMP = 'D:\dev\POPCORE\.local\tmp'
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = 'D:\dev\pip-cache'
$env:npm_config_cache = 'D:\dev\npm-cache'
$env:PLAYWRIGHT_BROWSERS_PATH = 'D:\dev\POPCORE\.local\ms-playwright'

.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt -c requirements-windows.constraints.txt
.\.venv\Scripts\python.exe -m playwright install chromium
Push-Location popcore_app\frontend
npm ci
Pop-Location
```

Python 3.13 and Node 24 are the supported development versions. The Windows constraint file was resolved and installed in a fresh Python 3.13 environment. The Linux constraint file carries the same cross-platform versions and is validated by the Linux CI job.

## Start development

Backend:

```powershell
Set-Location D:\dev\POPCORE
$env:PLAYWRIGHT_BROWSERS_PATH = 'D:\dev\POPCORE\.local\ms-playwright'
.\.venv\Scripts\python.exe -m flask --app popcore_app/app.py --env-file popcore_app/.env run --host 127.0.0.1 --port 5000
```

Frontend, in a second terminal:

```powershell
Set-Location D:\dev\POPCORE\popcore_app\frontend
npm run dev -- --host localhost --port 5173 --strictPort
```

## Local checks

```powershell
Set-Location D:\dev\POPCORE
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
git diff --check
git status --short --branch
```

The browser runner owns a strict loopback Vite server on port 5174, intercepts all API calls, blocks external requests, and stores a failure screenshot and server log under `.local/build-1/browser`. It fails immediately if port 5174 is occupied.

## Dependency and deployment status

- A fresh Windows Python 3.13 resolution and `pip check` pass. The project `.venv` was recreated on D: after the Windows Store Python launcher path changed; the stale environment is preserved under `.local/build-1/venv-stale-2026-09-08`.
- The Python advisory report contains one accepted transitive limitation: `python-jose` installs `ecdsa` 0.19.2, reported as PYSEC-2026-1325 / CVE-2024-23342 / GHSA-wj6h-64fc-37mp, with no fixed release. The affected P-256 signing/key-generation/ECDH functions are not used by this application. The configured Auth0 RS256 verification path resolves to `jose.backends.cryptography_backend`; do not remove the required transitive package independently.
- The initial npm report contained 13 packages with findings: 7 high, 5 moderate, and 1 low. Compatible updates removed nine. Separate reviewed checkpoints upgraded Vite from 5.4.21 to 8.2.2 with `@vitejs/plugin-react` 6.1.1, and React Router DOM from 6.30.3 to 7.18.3. Unit tests, isolated production builds, and the browser regression suite passed after each required major upgrade.
- A fresh locked `npm ci` followed by `npm audit` on 2026-09-08 reports zero known vulnerabilities. Final reports are stored locally in `.local/build-1/npm-audit-final.json` and `.local/build-1/pip-audit-final.json`.
- GitHub Actions runs Python 3.13 tests on Windows and Linux, Node 24 tests/build on Linux, and the isolated Chromium gate. Actions are pinned to full release commit SHAs and receive read-only repository permission.
- Local checks do not prove live Auth0, production data, external integrations, Linux deployment, or CI results. No deployment, production data access, release build, commit, or push is part of Build 1.
