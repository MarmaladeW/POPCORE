#!/usr/bin/env bash
# =============================================================================
#  POPCORE — one-command production setup
#
#  Run once on a fresh DigitalOcean Droplet (or re-run to update):
#
#    sudo bash /home/user/POPCORE/setup_production.sh
#
#  What it does:
#    1. Installs system packages (python3-venv, sqlite3)
#    2. Creates / updates the Python virtual environment
#    3. Installs Python dependencies from requirements.txt
#    4. Creates .env from .env.example if one doesn't exist yet
#    5. Installs and enables the systemd service (auto-start on reboot)
#    6. Installs the logrotate configuration
#    7. Wires backup.sh to a daily 2 AM cron job
#    8. Prints a checklist of manual steps still needed
#       (HTTPS / nginx, .env secrets, Auth0)
#
#  Safe to re-run: all steps are idempotent.
# =============================================================================

set -euo pipefail

# ── helpers ──────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✔${NC}  $*"; }
warn() { echo -e "${YELLOW}⚠${NC}  $*"; }
die()  { echo -e "${RED}✘${NC}  $*" >&2; exit 1; }
step() { echo -e "\n${GREEN}▶${NC}  $*"; }

# ── must run as root ──────────────────────────────────────────────────────────
[[ $EUID -eq 0 ]] || die "Please run as root: sudo bash $0"

# ── resolve paths ────────────────────────────────────────────────────────────
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$REPO_DIR/popcore_app"
VENV_DIR="$REPO_DIR/venv"
RUN_USER="${SUDO_USER:-root}"   # the non-root user who owns the repo, or root

# ── 1. system packages ───────────────────────────────────────────────────────
step "Checking system packages"
MISSING_PKGS=()
command -v python3      >/dev/null 2>&1 || MISSING_PKGS+=(python3)
python3 -m venv --help  >/dev/null 2>&1 || MISSING_PKGS+=(python3-venv)
command -v sqlite3      >/dev/null 2>&1 || MISSING_PKGS+=(sqlite3)
command -v crontab      >/dev/null 2>&1 || MISSING_PKGS+=(cron)

if [[ ${#MISSING_PKGS[@]} -gt 0 ]]; then
    warn "Installing missing packages: ${MISSING_PKGS[*]}"
    apt-get update -qq
    apt-get install -y -qq "${MISSING_PKGS[@]}"
fi
ok "System packages ready"

# ── 2. Python virtual environment ────────────────────────────────────────────
step "Setting up Python virtual environment at $VENV_DIR"
if [[ ! -d "$VENV_DIR" ]]; then
    python3 -m venv "$VENV_DIR"
    ok "Virtual environment created"
else
    ok "Virtual environment already exists"
fi

# ── 3. Python dependencies ───────────────────────────────────────────────────
step "Installing Python dependencies"
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
ok "Dependencies installed"

# ── 4. .env file ─────────────────────────────────────────────────────────────
step "Checking .env file"
if [[ ! -f "$APP_DIR/.env" ]]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    chmod 600 "$APP_DIR/.env"
    warn ".env created from .env.example — EDIT IT NOW before starting the service"
    warn "  nano $APP_DIR/.env"
else
    ok ".env already exists"
fi

# ── 5. systemd service ───────────────────────────────────────────────────────
step "Installing systemd service"
SERVICE_SRC="$APP_DIR/popcore.service"
SERVICE_DST="/etc/systemd/system/popcore.service"

sed \
    -e "s|__REPO_DIR__|$REPO_DIR|g" \
    -e "s|__VENV_DIR__|$VENV_DIR|g" \
    -e "s|__RUN_USER__|$RUN_USER|g" \
    "$SERVICE_SRC" > "$SERVICE_DST"

systemctl daemon-reload
systemctl enable popcore.service
ok "Service installed and enabled (will start on next boot)"

# Start or restart the service only if .env has been filled in
if grep -q 'your-tenant.us.auth0.com' "$APP_DIR/.env" 2>/dev/null; then
    warn "Service NOT started — .env still contains placeholder values"
    warn "Fill in $APP_DIR/.env then run:  systemctl start popcore"
else
    systemctl restart popcore.service
    sleep 2
    if systemctl is-active --quiet popcore.service; then
        ok "Service started successfully"
    else
        warn "Service failed to start — check logs with: journalctl -u popcore -n 50"
    fi
fi

# ── 6. logrotate ─────────────────────────────────────────────────────────────
step "Installing logrotate configuration"
LOGROTATE_SRC="$APP_DIR/logrotate.conf"
LOGROTATE_DST="/etc/logrotate.d/popcore"

sed \
    -e "s|__REPO_DIR__|$REPO_DIR|g" \
    -e "s|__RUN_USER__|$RUN_USER|g" \
    "$LOGROTATE_SRC" > "$LOGROTATE_DST"

# Validate the config
logrotate --debug "$LOGROTATE_DST" >/dev/null 2>&1 && ok "logrotate config valid" \
    || warn "logrotate config has warnings — check $LOGROTATE_DST"

# ── 7. cron — daily backup at 02:00 ─────────────────────────────────────────
step "Setting up daily backup cron job"
CRON_CMD="0 2 * * * $APP_DIR/backup.sh >> /var/log/popcore-backup.log 2>&1"
CRON_MARKER="popcore-backup"

# Add only if not already present
if crontab -l 2>/dev/null | grep -q "$CRON_MARKER"; then
    ok "Backup cron job already installed"
else
    ( crontab -l 2>/dev/null; echo "# $CRON_MARKER"; echo "$CRON_CMD" ) | crontab -
    ok "Backup cron job added (daily at 02:00)"
fi

# Ensure backup.sh is executable
chmod +x "$APP_DIR/backup.sh"

# ── 8. print summary ─────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════════"
echo "  POPCORE setup complete"
echo "════════════════════════════════════════════════════════════"
echo ""
echo "  Service management:"
echo "    systemctl status popcore"
echo "    systemctl restart popcore"
echo "    journalctl -u popcore -f          # live logs"
echo "    tail -f $APP_DIR/server.log       # file logs"
echo ""
echo "  Backup:"
echo "    $APP_DIR/backup.sh                # run manually"
echo "    cat /var/log/popcore-backup.log   # backup log"
echo "    ls $APP_DIR/backups/              # stored backups"
echo ""

if grep -q 'your-tenant.us.auth0.com' "$APP_DIR/.env" 2>/dev/null; then
    echo -e "${RED}  !! REQUIRED before going live !!${NC}"
    echo "    1. Edit .env:  nano $APP_DIR/.env"
    echo "       Fill in AUTH0_DOMAIN, AUTH0_AUDIENCE, AUTH0_MGMT_CLIENT_ID,"
    echo "       AUTH0_MGMT_CLIENT_SECRET, CORS_ORIGINS, SENTRY_DSN"
    echo "    2. Start the service:  systemctl start popcore"
    echo ""
fi

echo -e "${YELLOW}  HTTPS setup (do this once, requires a domain pointed at this server):${NC}"
echo "    apt install -y nginx python3-certbot-nginx"
echo "    cp $APP_DIR/nginx.conf /etc/nginx/sites-available/popcore"
echo "    # Edit /etc/nginx/sites-available/popcore — replace YOUR_DOMAIN"
echo "    ln -sf /etc/nginx/sites-available/popcore /etc/nginx/sites-enabled/popcore"
echo "    nginx -t && systemctl reload nginx"
echo "    certbot --nginx -d YOUR_DOMAIN"
echo ""
echo "  After HTTPS is live, set in .env:"
echo "    CORS_ORIGINS=https://YOUR_DOMAIN"
echo "    Then: systemctl restart popcore"
echo ""
