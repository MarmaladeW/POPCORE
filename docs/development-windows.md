# Windows development on D:

Repository: https://github.com/MarmaladeW/POPCORE

Use D:\dev\POPCORE as the working folder. The original empty C:\Users\mason\Documents\ChatGPT\POPCORE STORE folder is not this checkout. Open the D: folder for future development sessions.

## Install or refresh dependencies

PowerShell, repository root:

```powershell
Set-Location D:\dev\POPCORE
New-Item -ItemType Directory -Path .local\tmp -Force | Out-Null
$env:TEMP = 'D:\dev\POPCORE\.local\tmp'
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = 'D:\dev\pip-cache'
$env:npm_config_cache = 'D:\dev\npm-cache'
$env:PLAYWRIGHT_BROWSERS_PATH = 'D:\dev\POPCORE\.local\ms-playwright'
if (-not (Test-Path .venv)) { py -3.13 -m venv .venv }
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt -c requirements-windows.constraints.txt
.\.venv\Scripts\python.exe -m playwright install chromium
Push-Location popcore_app\frontend
npm ci
Pop-Location
```

Python 3.13 and Node 24 are the baseline used for this setup. Node's native TypeScript execution is required by npm test; the old README's Node 18 minimum is insufficient for that test command. Python itself may use the existing C: installation; its project packages are installed in D:\dev\POPCORE\.venv. The environment variables above apply to the current terminal; repeat them in new terminals when installing or running tests. No machine-wide settings are changed.

## Local configuration

The setup creates these ignored files only if absent:

- popcore_app/.env: backend template, adjusted for development, localhost CORS, and disabled background scheduling.
- popcore_app/frontend/.env.development.local: frontend development template.

Replace the Auth0 placeholder values in both files with the correct tenant, API audience, and SPA client ID. Management client ID/secret are needed for user-management features. Never put the management secret into a VITE_* variable. Configure the SPA's Auth0 callback, logout, and web origins for http://localhost:5173. Optional Sentry, Anthropic, and Google integration credentials are not needed for basic local startup.

The committed frontend/.env.production contains public browser identifiers and remains unchanged. Using .env.development.local keeps local settings out of production builds. Backend application code does not load .env; requirements-dev.txt adds python-dotenv so Flask CLI --env-file can load it without changing application code.

## Start development

Terminal 1:

```powershell
Set-Location D:\dev\POPCORE
$env:PLAYWRIGHT_BROWSERS_PATH = 'D:\dev\POPCORE\.local\ms-playwright'
.\.venv\Scripts\python.exe -m flask --app popcore_app/app.py --env-file popcore_app/.env run --host 127.0.0.1 --port 5000
```

Terminal 2:

```powershell
Set-Location D:\dev\POPCORE\popcore_app\frontend
npm run dev -- --host localhost --port 5173 --strictPort
```

Open http://localhost:5173. Vite proxies API and hidden image requests to Flask on port 5000. Stop each server with Ctrl+C. The existing start.bat uses system Python and does not load .env; use the commands above for the project virtual environment. Gunicorn and the Linux deployment scripts are not Windows development servers.

## Data and optional browser installation

Flask startup runs migrations and can create an empty database at popcore_app/popcore.db. Real inventory needs the untracked root-level spreadsheets copy of 11.xlsx and POP_CORE_v3.xlsx. The shorter name 11.xlsx in the older README is inaccurate. Only after both inputs are present, and after backing up any existing database, run:

```powershell
.\.venv\Scripts\python.exe popcore_app\init_db.py
```

This imports/upserts products. Do not run it casually against existing data: missing files print errors without reliably producing a failing exit code.

For browser-backed scraping, in the repository root:

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = 'D:\dev\POPCORE\.local\ms-playwright'
$env:TEMP = 'D:\dev\POPCORE\.local\tmp'
$env:TMP = $env:TEMP
.\.venv\Scripts\python.exe -m playwright install chromium
```

Keep PLAYWRIGHT_BROWSERS_PATH set when using scraping. This avoids the default browser download folder under the C: user profile.

## Tests and production build

With D: TEMP/TMP configured as in the installation block:

```powershell
Set-Location D:\dev\POPCORE
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m unittest discover -s popcore_app/tests -v
.\.venv\Scripts\python.exe popcore_app/tests/browser/check_foundation.py
Set-Location popcore_app\frontend
npm test
npm run build -- --outDir ../../.local/frontend-build
```

The final command runs the real TypeScript and Vite production build but writes to ignored .local/frontend-build. The normal release command is:

```powershell
npm run build
```

It empties and regenerates the committed popcore_app/static directory. Use it when intentionally preparing frontend release assets; review those changes before committing. There is no configured npm lint script or pytest suite. The existing tests cover scheduling, not the entire app or live integrations.

## Git hygiene

The origin remote should be https://github.com/MarmaladeW/POPCORE.git. Check git status --short --branch and git remote -v before work. No commits or pushes are part of this setup.

Local secrets, environments, virtual environments, node_modules, SQLite databases and journals, logs, uploads, backups, source spreadsheets, and .local output are ignored. Existing committed static assets are intentionally retained. The previously tracked popcore_app/server.log is removed from the Git index only and retained on disk; the staged removal must be committed with a future reviewed setup commit to stop tracking it upstream. Existing Git history is unchanged.

## Setup verification (2026-09-07)

- Cloned main at 8630199ac780059f5cb185a85c6efd0ac4597b91; origin/main matched at setup time. No commit, push, or application-source change.
- Installed Python 3.13.14 virtual environment dependencies, Node 24.12.0 / npm 11.6.2 frontend dependencies, and Playwright Chromium on D:. pip check passed.
- Existing tests passed: 10 Python tests and 14 frontend tests. TypeScript and the isolated Vite production build passed. The committed static directory and package lock were unchanged.
- Both local servers returned HTTP 200; protected API requests returned 401 directly and through Vite; an unknown API route returned 404; backend localhost CORS loaded from .env. Chromium launched successfully. Temporary verification servers were stopped.
- Verified 22 representative ignore paths, plus the retained template/public-configuration exceptions. No log remains tracked in the current Git index; the original log still exists on disk.
- Local Auth0 files still contain placeholders. Real login, Management API access, external integrations, and inventory imports were not validated. The new local database has no imported product inventory.
- Build warnings: an oversized JavaScript chunk and an outdated Browserslist dataset. These did not fail the build; no dependency upgrades or application refactoring were made.
- Existing .claude/launch.json points to D:\hello\popcore_app\popcore_app. It was preserved; use the commands in this guide instead.
- Restricted Codex tool execution may be unable to read the global Git ignore file or launch the existing Microsoft Store Python interpreter. The approved setup execution successfully ran Git and the D: virtual environment; do not relocate the project to C: to work around that sandbox restriction.

## Build 1 note

Build 1 adds backend foundation tests and an isolated browser gate while retaining the existing Schedule tests. See docs/development-build-1.md for the temporary rule that prevents changing or deleting a report after it has posted stock history.
