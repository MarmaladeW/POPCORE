# POPCORE Inventory System

Internal inventory, stock, sales, and scheduling tool for a Pop-Mart retail store.

**Stack:** Flask · SQLite (WAL) · React · TypeScript · Ant Design · Auth0 · Gunicorn · nginx

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Local Development](#local-development)
3. [Production Deployment](#production-deployment)
4. [Environment Variables](#environment-variables)
5. [Database](#database)
6. [Backups](#backups)
7. [Logs](#logs)
8. [Service Management](#service-management)
9. [Updating the App](#updating-the-app)

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.11+ | System Python is fine |
| Node.js | 18+ | For building the frontend |
| sqlite3 | any | Ships with most Linux distros |
| Auth0 tenant | — | Free tier works; see [Environment Variables](#environment-variables) |

---

## Local Development

```bash
# 1. Clone
git clone https://github.com/MarmaladeW/POPCORE.git
cd POPCORE

# 2. Backend — create venv and install deps
python3 -m venv venv
source venv/bin/activate
pip install -r popcore_app/requirements.txt

# 3. Environment — copy the example and fill in real values
cp popcore_app/.env.example popcore_app/.env
# Edit popcore_app/.env — at minimum set AUTH0_DOMAIN and the other Auth0 vars

# 4. Database — initialise from the Excel source files
#    (requires copy of 11.xlsx and POP_CORE_v3.xlsx in the repo root)
python popcore_app/init_db.py

# 5. Run the backend
cd popcore_app
python app.py          # dev server on http://localhost:5000
```

Frontend dev server (hot-reload, proxies /api to Flask):

```bash
cd popcore_app/frontend
cp .env.example .env.local
# Edit .env.local — set VITE_AUTH0_DOMAIN, VITE_AUTH0_CLIENT_ID, VITE_AUTH0_AUDIENCE
npm install
npm run dev            # http://localhost:5173
```

### Windows (quick start)

Double-click `popcore_app/start.bat`. It initialises the database if missing and starts the Flask dev server.

---

## Production Deployment

### First-time setup (run once on the Droplet as root)

```bash
# 1. Clone the repo
git clone https://github.com/MarmaladeW/POPCORE.git /home/user/POPCORE
cd /home/user/POPCORE

# 2. Run the setup script — creates venv, installs service, logrotate, and cron
sudo bash setup_production.sh

# 3. Fill in production secrets
nano popcore_app/.env
# Required: AUTH0_DOMAIN, AUTH0_AUDIENCE, AUTH0_MGMT_CLIENT_ID,
#           AUTH0_MGMT_CLIENT_SECRET, CORS_ORIGINS

# 4. Start the service
sudo systemctl start popcore
sudo systemctl status popcore     # should show "active (running)"
```

### HTTPS via nginx + Certbot (recommended)

```bash
# Install nginx and Certbot
sudo apt install -y nginx python3-certbot-nginx

# Install the included nginx config (replace YOUR_DOMAIN with your actual domain)
sudo cp popcore_app/nginx.conf /etc/nginx/sites-available/popcore
sudo sed -i 's/YOUR_DOMAIN/your.domain.com/g' /etc/nginx/sites-available/popcore
sudo ln -sf /etc/nginx/sites-available/popcore /etc/nginx/sites-enabled/popcore
sudo nginx -t && sudo systemctl reload nginx

# Obtain a TLS certificate (domain must point at this server first)
sudo certbot --nginx -d your.domain.com

# After HTTPS is live, update CORS_ORIGINS in .env
sudo nano popcore_app/.env
#   CORS_ORIGINS=https://your.domain.com
sudo systemctl restart popcore
```

The included `nginx.conf` already has:
- HTTP → HTTPS redirect
- Security headers (HSTS, X-Content-Type-Options, X-Frame-Options)
- Gzip compression (2.3 MB JS bundle → ~700 KB)
- Long-term caching for content-hashed static assets

---

## Environment Variables

All variables are read from `popcore_app/.env`. Copy `popcore_app/.env.example` as a starting point.

| Variable | Required | Description |
|---|---|---|
| `AUTH0_DOMAIN` | **Yes** | Auth0 tenant domain (`xxx.us.auth0.com`) — server refuses to start without it |
| `AUTH0_AUDIENCE` | **Yes** | API identifier in Auth0 (`https://popcore/api`) |
| `AUTH0_MGMT_CLIENT_ID` | **Yes** | Machine-to-machine app client ID (for user management) |
| `AUTH0_MGMT_CLIENT_SECRET` | **Yes** | Machine-to-machine app client secret |
| `CORS_ORIGINS` | **Yes** | Comma-separated allowed origins (`https://your.domain.com`) |
| `SENTRY_DSN` | No | Sentry DSN for error tracking — leave blank to disable |
| `APP_ENV` | No | `production` or `development` (default: `development`) |
| `APP_RELEASE` | No | Release version string shown in Sentry |
| `SERVER_NAME` | No | Hostname label for Sentry context |

**Frontend** (baked into the JS bundle at build time — stored in `popcore_app/frontend/.env.local`):

| Variable | Description |
|---|---|
| `VITE_AUTH0_DOMAIN` | Same value as `AUTH0_DOMAIN` above |
| `VITE_AUTH0_CLIENT_ID` | Auth0 SPA application client ID |
| `VITE_AUTH0_AUDIENCE` | Same value as `AUTH0_AUDIENCE` above |

> The pre-built bundle in `popcore_app/static/` already has these values baked in.
> Only rebuild if you change Auth0 tenants:
> ```bash
> cd popcore_app/frontend
> npm install && npm run build
> sudo systemctl restart popcore
> ```

---

## Database

- **Engine:** SQLite 3 with WAL mode and foreign-key enforcement
- **Path:** `popcore_app/popcore.db` (excluded from git)
- **Init:** Run `python popcore_app/init_db.py` with the Excel source files present
- **Schema migrations:** handled automatically on startup via `migrate_db()` in `app.py`

---

## Backups

`backup.sh` takes a hot WAL-safe snapshot and keeps 30 days of history.

```bash
# Manual run
bash popcore_app/backup.sh

# Check scheduled backups (set up by setup_production.sh at 02:00 daily)
crontab -l | grep popcore
cat /var/log/popcore-backup.log

# List stored snapshots
ls -lh popcore_app/backups/
```

To change retention (e.g., 60 days):
```bash
KEEP_DAYS=60 bash popcore_app/backup.sh
```

---

## Logs

| Log | Location | Description |
|---|---|---|
| Application | `popcore_app/server.log` | Gunicorn access + error logs |
| systemd journal | `journalctl -u popcore` | Same output, searchable |
| Backup | `/var/log/popcore-backup.log` | Daily backup cron output |
| nginx access | `/var/log/nginx/access.log` | Reverse-proxy access log |
| nginx error | `/var/log/nginx/error.log` | Reverse-proxy error log |

`server.log` is rotated daily (30-day retention, gzip compressed) by the logrotate config installed via `setup_production.sh`.

```bash
# Live log stream
journalctl -u popcore -f
# or
tail -f popcore_app/server.log
```

---

## Service Management

```bash
sudo systemctl status popcore      # current status
sudo systemctl start popcore       # start
sudo systemctl stop popcore        # stop
sudo systemctl restart popcore     # full restart (new workers)
sudo systemctl reload popcore      # graceful reload (drain, then restart workers)
```

---

## Updating the App

```bash
cd /home/user/POPCORE
git pull

# If Python dependencies changed:
venv/bin/pip install -r popcore_app/requirements.txt

# If the frontend was changed (pre-built bundle is committed, so usually skip this):
# cd popcore_app/frontend && npm install && npm run build && cd ../..

# Restart to pick up backend changes
sudo systemctl restart popcore
```

To update the systemd service or logrotate config after editing the repo templates:

```bash
sudo bash setup_production.sh   # idempotent — safe to re-run
```
