# Codex instructions for POPCORE

## Scope and preservation

- This repository is MarmaladeW/POPCORE, the Flask/React inventory, sales, and scheduling application. It is separate from the WordPress repository at D:\popcore_plugins.
- The main development checkout is D:\dev\POPCORE. Keep project files, .venv, node_modules, local data, temporary files, browser downloads, and build output on D:. Existing system Python on C: may be used to create the D: virtual environment.
- Before editing, inspect git status --short --branch, git remote -v, relevant source, and existing diffs. Preserve user changes. Do not reset, clean, overwrite environments, or regenerate release assets as incidental cleanup.
- State assumptions and a short verification plan. Make the smallest change requested; no speculative abstractions, unrelated refactoring, or application behavior changes during setup.
- Verify the remote against https://github.com/MarmaladeW/POPCORE.git. Recheck the current branch; do not assume main remains checked out. New feature branches use codex/ unless instructed otherwise. Do not commit, push, or deploy without authorization.

## Architecture and locations

- popcore_app/app.py creates the Flask app, registers blueprints, runs SQLite migrations on import, and optionally starts APScheduler. It exports app; there is no create_app factory.
- popcore_app/blueprints/ contains APIs for products, stores, inventory, stock, sales, restock, schedule, users, insights, and settings.
- popcore_app/db.py owns WAL connections, schema, and migrations. The local default is popcore_app/popcore.db; production and isolated checks set POPCORE_DB_PATH and private upload roots before import. auth.py implements Auth0 JWT verification and roles.
- popcore_app/frontend/src/ is React 18 / TypeScript with Vite, Ant Design, Tailwind, Zustand, and FullCalendar. src/api/client.ts uses /api; src/auth/ holds frontend access checks.
- Vite proxies /api and /hidden_imgs to http://localhost:5000. Development frontend: http://localhost:5173.
- popcore_app/static/ is the intentionally committed production frontend. npm run build runs TypeScript and replaces this directory (emptyOutDir is true). Preserve this release convention; use the separate verification build below for setup work.
- popcore_app/tests/ contains Python unittest and isolated Playwright checks. Frontend tests are src/pages/Schedule/*.test.ts, run by Node's built-in test runner. There is no pytest or frontend lint script configured. `.github/workflows/checks.yml` runs backend checks on Windows/Linux plus frontend and browser checks on Linux.
- docs/ contains implementation context. README.md describes Linux deployment; docs/development-windows.md is the Windows development runbook. Gunicorn, nginx, systemd, backup.sh, and setup_production.sh are Linux production tooling, not Windows startup commands.

## Windows development

Use PowerShell from D:\dev\POPCORE. Use the virtual environment interpreter explicitly; activation is optional. Python 3.13 and Node 24 are the setup baseline (the frontend tests execute TypeScript directly).

```powershell
Set-Location D:\dev\POPCORE
New-Item -ItemType Directory -Path .local\tmp -Force | Out-Null
$env:TEMP = 'D:\dev\POPCORE\.local\tmp'
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = 'D:\dev\pip-cache'
$env:npm_config_cache = 'D:\dev\npm-cache'
$env:PLAYWRIGHT_BROWSERS_PATH = 'D:\dev\POPCORE\.local\ms-playwright'
# Create only if absent:
if (-not (Test-Path .venv)) { py -3.13 -m venv .venv }
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt -c requirements-windows.constraints.txt
.\.venv\Scripts\python.exe -m playwright install chromium
Set-Location popcore_app\frontend
npm ci
```

requirements-dev.txt includes the unchanged backend requirements plus python-dotenv for Flask CLI environment loading. Do not globally install packages or update package-lock.json just to install dependencies.

Backend (repository root; environment values must be configured):

```powershell
.\.venv\Scripts\python.exe -m flask --app popcore_app/app.py --env-file popcore_app/.env run --host 127.0.0.1 --port 5000
```

Frontend (second terminal, popcore_app/frontend): npm run dev -- --host localhost --port 5173 --strictPort.

- python app.py and start.bat do not load .env themselves. Prefer the Flask CLI command above. Do not add an authentication bypass to make development start.
- Backend local configuration is popcore_app/.env. Use APP_ENV=development, CORS_ORIGINS=http://localhost:5173, DISABLE_SCHEDULER=1, and blank optional external-service keys. Set real Auth0 settings locally; never print secrets.
- Frontend local configuration is popcore_app/frontend/.env.development.local, copied from .env.example only when absent. All VITE_* values are public browser configuration; never put secrets there.
- Preserve tracked frontend/.env.production: it contains public Auth0 identifiers. Development-only overrides avoid accidentally replacing production build settings. Existing .env.local files, if any, need review because they apply to production builds too.
- Startup can create an empty local database. For actual inventory import, init_db.py requires root-level files named exactly 'copy of 11.xlsx' and 'POP_CORE_v3.xlsx'. Missing inputs print errors but can still return exit 0; check files before running. Imports upsert existing products, so back up existing databases before importing. Do not fetch production data without permission.
- Chromium is used by the scraper. Install it only into the configured PLAYWRIGHT_BROWSERS_PATH; retain that environment variable whenever running the backend or scraper. See the runbook.

## Verification

Use D: TEMP/TMP as above so temporary test databases stay on D:.

```powershell
# Repository root
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m unittest discover -s popcore_app/tests -v
.\.venv\Scripts\python.exe popcore_app/tests/browser/check_foundation.py
.\.venv\Scripts\python.exe popcore_app/tests/browser/check_inventory_core.py
.\.venv\Scripts\python.exe popcore_app/tests/browser/check_goods_flow.py
.\.venv\Scripts\python.exe popcore_app/tests/browser/check_store_day.py
.\.venv\Scripts\python.exe popcore_app/tests/browser/check_trades_today.py
.\.venv\Scripts\python.exe popcore_app/tests/browser/check_release_pilot.py
.\.venv\Scripts\python.exe scripts/check_release_recovery.py --output .local/build6/recovery-new
# Frontend directory
npm test
# Production-mode verification without replacing committed static assets:
npm run build -- --outDir ../../.local/frontend-build
# Intentional release build only:
# npm run build
```

Inspect command exits and report failures; unit tests do not prove Auth0 login or external integrations. Check git diff --check and git status --short --branch after work, including tracked asset changes. Keep package-lock.json and release assets unchanged during setup unless a documented setup problem requires otherwise.

Build 1 containment behavior and its temporary report-correction limitation are documented in docs/development-build-1.md. Completed restocks and reports with stock history must remain intact; do not bypass their 409 responses. Schedule remains frozen during Build 1.

Build 2 inventory rules are documented in docs/inventory-core.md. Keep real stores in legacy mode until reviewed opening counts and explicit inventory access are approved. Public code must never post an opening command. In authoritative mode, all stock writes go through inventory_commands.post_inventory with a stable request key and API-returned balance versions; ambiguous legacy stock paths stay blocked.

Build 3 goods rules are documented in docs/goods-handling.md. Goods workflows compose their state transition and inventory posting in one transaction. Generic commands cannot use transit. Never edit delivery-owned stock outside its lifecycle, and preserve opened-set provenance through dispatch, receipt, and return. Schedule remains unchanged.

Build 4 sales/payment/closing rules are in docs/sales-and-closing.md. Keep refunds and physical returns separate, require individual evidence/payment/closing review reasons, and refresh version/source-token facts after every confirmed mutation.

Build 5 trade and Today rules are in docs/trades-and-today.md. Personal shifts remain first and private; store selection must not filter them. Trade proof, condition decisions and slot versions remain explicit.

Build 6 report, recovery and release rules are in docs/operational-reports.md, docs/build6-workflow-checklist.md, docs/release-and-recovery.md and docs/production-readiness.md. Financial reports are manager-only and scoped before aggregation. Complete backups include every referenced product, payment and condition attachment. Keep Schedule source outside Build 6 changes. The real pilot creates only disposable `.local/build6` services/data and does not prove live Auth0, devices, opening stock or recovery.

## Files that must remain local

.gitignore protects environment variants, keys, credentials, virtual environments, dependencies, SQLite files and journals, logs, uploads, backups, Excel source data, and .local verification output. Keep .env.example files and reviewed frontend/.env.production tracked. .gitignore does not protect already tracked files: inspect git ls-files as well as git check-ignore. Never stage broad directories without reviewing the staged diff for data and secrets.

Existing editor configuration: .claude/launch.json uses an older D:\hello checkout and system Python. Preserve it unless asked to configure that editor; use the verified commands above for this checkout.
