#!/usr/bin/env bash
# refresh_staging.sh: give POPCORE staging a fresh copy of production's data and,
# optionally, a release to try. Run as root on the droplet:
#
#   refresh_staging.sh [--source live|nightly|FILE.db] [--commit COMMIT] [--start]
#
#   --source live     (default) a fresh online backup of the production database, taken
#                     with SQLite's backup API: production only reads, staff keep working.
#   --source nightly  the database inside /root/popcore-backups/nightly/latest.tar.gz.
#   --source FILE.db  any SQLite copy, e.g. one from restore_from_litestream.sh (the
#                     quarterly restore drill).
#   --commit COMMIT   also move the staging checkout (/opt/popcore-staging) to COMMIT after
#                     fetching origin, and install its Python requirements when they change
#                     (pinned to production's installed versions where they agree).
#   --start           start staging afterwards even if it was stopped.
#
# Product images are copied from production; payment and condition evidence are not, so
# staging starts with none. Staging is stopped while its files are swapped and started
# again if it was running (or with --start); then the script waits for its /api/health.
# Staging data lives in /var/lib/popcore-staging and belongs to the popcore-staging user.
#
# Settings: POPCORE_STAGING_REPO (/opt/popcore-staging), POPCORE_STAGING_DATA
# (/var/lib/popcore-staging), POPCORE_STAGING_USER (popcore-staging),
# POPCORE_STAGING_SERVICE (popcore-staging), POPCORE_STAGING_HEALTH_URL
# (http://127.0.0.1:5002/api/health), POPCORE_DB_PATH (/root/POPCORE/popcore_app/popcore.db),
# POPCORE_UPLOADS_DIR (/root/POPCORE/popcore_app/uploads), POPCORE_VENV
# (/root/POPCORE/popcore_app/venv), POPCORE_NIGHTLY (.../nightly/latest.tar.gz).
set -euo pipefail

STAGING_REPO="${POPCORE_STAGING_REPO:-/opt/popcore-staging}"
STAGING_VENV="$STAGING_REPO/venv"
STAGING_DATA="${POPCORE_STAGING_DATA:-/var/lib/popcore-staging}"
STAGING_DB="$STAGING_DATA/popcore.db"
STAGING_USER="${POPCORE_STAGING_USER:-popcore-staging}"
STAGING_SERVICE="${POPCORE_STAGING_SERVICE:-popcore-staging}"
HEALTH_URL="${POPCORE_STAGING_HEALTH_URL:-http://127.0.0.1:5002/api/health}"
PROD_DB="${POPCORE_DB_PATH:-/root/POPCORE/popcore_app/popcore.db}"
PROD_UPLOADS="${POPCORE_UPLOADS_DIR:-/root/POPCORE/popcore_app/uploads}"
PROD_VENV="${POPCORE_VENV:-/root/POPCORE/popcore_app/venv}"
NIGHTLY="${POPCORE_NIGHTLY:-/root/popcore-backups/nightly/latest.tar.gz}"
LOCK_FILE="${POPCORE_STAGING_LOCK_FILE:-/run/lock/popcore-staging.lock}"

WORK="" STOPPED=0

log()  { printf '%s  %s\n' "$(date -u +%H:%M:%S)" "$*"; }
step() { printf '\n== %s\n' "$*"; }
warn() { printf '%s  WARNING: %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }
die()  { printf '%s  STOP: %s\n' "$(date -u +%H:%M:%S)" "$1" >&2; exit "${2:-1}"; }
usage() { sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; }

python_bin() {
  if [ -x "$PROD_VENV/bin/python" ]; then printf '%s' "$PROD_VENV/bin/python"; else printf 'python3'; fi
}

pyhelp() {
  "$(python_bin)" - "$@" <<'PY'
import http.client, hashlib, json, os, sqlite3, sys, urllib.error, urllib.request
from contextlib import closing

def integrity(path):
    with closing(sqlite3.connect(path)) as con:
        return [row[0] for row in con.execute('PRAGMA integrity_check')]

def backup(src, dest):
    if not os.path.isfile(src):
        print(f'no database at {src}', file=sys.stderr)
        return 1
    with closing(sqlite3.connect(src, timeout=30)) as source, closing(sqlite3.connect(dest)) as target:
        source.backup(target)  # one consistent snapshot; production keeps writing
    return check(dest)

def check(path):
    result = integrity(path)
    if result != ['ok']:
        print(f'integrity_check failed: {result[:5]}', file=sys.stderr)
        return 1
    print(f'{os.path.getsize(path) / 1e6:.1f} MB, integrity_check ok', file=sys.stderr)
    return 0

def digest(path):
    with open(path, 'rb') as handle:
        print(hashlib.sha256(handle.read()).hexdigest())
    return 0

def health(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=5) as response:
            data = json.loads(response.read() or b'{}')
            if response.status == 200 and data.get('ok') is True:
                print(f'ok, release {data.get("release")}')
                return 0
            print(f'answered {response.status}')
    except urllib.error.HTTPError as error:
        print(f'answered {error.code}')
    except (OSError, ValueError, http.client.HTTPException) as error:
        print(f'not answering ({error.__class__.__name__})')
    return 1

command, args = sys.argv[1], sys.argv[2:]
sys.exit({'backup': backup, 'check': check, 'digest': digest, 'health': health}[command](*args))
PY
}

cleanup() {
  if [ -n "$WORK" ] && [ -d "$WORK" ]; then
    rm -rf "$WORK"  # it holds a copy of production data
  fi
  if [ "$STOPPED" -eq 1 ]; then
    warn "Stopped early; $STAGING_SERVICE stays stopped (production is not affected)."
  fi
}

take_lock() {
  if command -v flock >/dev/null 2>&1; then
    mkdir -p "$(dirname "$LOCK_FILE")"
    exec 9>"$LOCK_FILE"
    flock -n 9 || die "Another staging refresh is running (lock $LOCK_FILE)."
  else
    warn "flock is not installed; running without the staging lock."
  fi
}

prepare_database() {  # source
  step "New staging database from the $1 copy"
  if [ "$1" = live ]; then
    pyhelp backup "$PROD_DB" "$WORK/popcore.db" || die "The online backup of $PROD_DB failed; staging untouched."
  elif [ "$1" != nightly ]; then
    pyhelp backup "$1" "$WORK/popcore.db" || die "Could not copy $1; staging untouched."
  else
    [ -f "$NIGHTLY" ] || die "No nightly backup at $NIGHTLY."
    local member
    member="$(tar -tzf "$NIGHTLY" | grep -E '(^|/)popcore\.db$' | head -n 1 || true)"
    [ -n "$member" ] || die "$NIGHTLY has no popcore.db."
    tar -xzf "$NIGHTLY" -C "$WORK/nightly" "$member"
    mv "$WORK/nightly/$member" "$WORK/popcore.db"
    pyhelp check "$WORK/popcore.db" || die "The nightly copy failed integrity_check; staging untouched."
    if tar -tzf "$NIGHTLY" | grep '^\(\./\)\{0,1\}uploads/hidden_imgs/' >/dev/null; then
      tar -xzf "$NIGHTLY" -C "$WORK/nightly" --wildcards '*uploads/hidden_imgs*' 2>/dev/null \
        || tar -xzf "$NIGHTLY" -C "$WORK/nightly" uploads/hidden_imgs
    fi
  fi
}

update_code() {  # commit
  step "Staging code -> $1"
  [ -e "$STAGING_REPO/.git" ] || die "No staging checkout at $STAGING_REPO (see docs/operations-resilience.md, Staging)."
  local dirty sha requirements constraints want have
  dirty="$(git -C "$STAGING_REPO" status --porcelain --untracked-files=no)"
  [ -z "$dirty" ] || die "The staging checkout has local changes to tracked files; refusing."
  git -C "$STAGING_REPO" fetch --quiet --prune origin || die "git fetch in $STAGING_REPO failed."
  sha="$(git -C "$STAGING_REPO" rev-parse --verify --quiet "$1^{commit}")" || die "Unknown commit: $1"
  git -C "$STAGING_REPO" checkout --quiet --detach "$sha"
  log "Checked out $(git -C "$STAGING_REPO" log -1 --format='%h %s (%cs)')"
  requirements="$STAGING_REPO/popcore_app/requirements.txt"
  if [ ! -x "$STAGING_VENV/bin/python" ]; then
    log "Creating $STAGING_VENV"
    python3 -m venv "$STAGING_VENV"
    "$STAGING_VENV/bin/pip" install --quiet --upgrade pip
  fi
  want="$(pyhelp digest "$requirements")"
  have="$(cat "$STAGING_VENV/.popcore-requirements.sha256" 2>/dev/null || true)"
  if [ "$want" != "$have" ]; then
    log "Installing requirements (pinned to production's versions where possible)"
    constraints="$WORK/production-versions.txt"
    if [ -x "$PROD_VENV/bin/pip" ] && "$PROD_VENV/bin/pip" freeze >"$constraints" 2>/dev/null \
        && "$STAGING_VENV/bin/pip" install --quiet -r "$requirements" -c "$constraints"; then
      :
    else
      warn "Could not match production's versions; installing the release's requirements as they are."
      "$STAGING_VENV/bin/pip" install --quiet -r "$requirements" || die "pip install failed; staging is not started."
    fi
    printf '%s\n' "$want" >"$STAGING_VENV/.popcore-requirements.sha256"
  fi
}

copy_images() {  # source
  local from="$PROD_UPLOADS/hidden_imgs"
  if [ "$1" = nightly ]; then
    from="$(find "$WORK/nightly" -type d -path '*uploads/hidden_imgs' | head -n 1 || true)"
  fi
  mkdir -p "$STAGING_DATA/uploads/hidden_imgs" "$STAGING_DATA/uploads/payment_evidence" \
    "$STAGING_DATA/uploads/condition_evidence"
  if [ -n "$from" ] && [ -d "$from" ]; then
    if command -v rsync >/dev/null 2>&1; then
      rsync -a --delete "$from/" "$STAGING_DATA/uploads/hidden_imgs/"
    else
      rm -rf "$STAGING_DATA/uploads/hidden_imgs"
      cp -R "$from" "$STAGING_DATA/uploads/hidden_imgs"
    fi
    log "Product images copied from $from"
  else
    warn "No product images found to copy."
  fi
}

wait_for_health() {
  local deadline status=""
  deadline=$(( $(date +%s) + 90 ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if status="$(pyhelp health "$HEALTH_URL")"; then
      log "Staging is healthy: $status"
      return 0
    fi
    sleep 2
  done
  warn "Staging did not answer healthy within 90s ($status): journalctl -u $STAGING_SERVICE -n 50"
  return 1
}

main() {
  local source=live commit="" start=0 was_active=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --source) [ $# -ge 2 ] || die "--source needs live or nightly."; source="$2"; shift ;;
      --commit) [ $# -ge 2 ] || die "--commit needs a commit."; commit="$2"; shift ;;
      --start) start=1 ;;
      -h|--help) usage; exit 0 ;;
      *) die "Unknown argument $1 (see --help)." ;;
    esac
    shift
  done
  case "$source" in
    live|nightly) ;;
    *) [ -f "$source" ] || die "--source must be live, nightly or an existing .db file." ;;
  esac
  [ "$(id -u)" -eq 0 ] || die "Run as root."
  case "$STAGING_DATA/" in
    "$(dirname "$PROD_DB")/"*|/root/*) die "POPCORE_STAGING_DATA ($STAGING_DATA) must not be inside production's folders." ;;
  esac
  trap cleanup EXIT
  trap 'exit 130' INT TERM
  take_lock

  mkdir -p "$STAGING_DATA"
  chmod 700 "$STAGING_DATA"
  WORK="$(mktemp -d "$STAGING_DATA/refresh.XXXXXX")"
  mkdir -p "$WORK/nightly"
  prepare_database "$source"

  if systemctl is-active --quiet "$STAGING_SERVICE" 2>/dev/null; then
    was_active=1
    log "Stopping $STAGING_SERVICE while its files are swapped"
    systemctl stop "$STAGING_SERVICE"
    STOPPED=1
  fi
  [ -z "$commit" ] || update_code "$commit"

  step "Swapping in the new data"
  rm -f "$STAGING_DB-wal" "$STAGING_DB-shm"
  mv -f "$WORK/popcore.db" "$STAGING_DB"
  chmod 600 "$STAGING_DB"
  copy_images "$source"
  if id "$STAGING_USER" >/dev/null 2>&1; then
    chown -R "$STAGING_USER:$STAGING_USER" "$STAGING_DATA"
  else
    warn "User $STAGING_USER does not exist yet (see docs/operations-resilience.md, Staging)."
  fi
  log "Staging database: $STAGING_DB"

  if [ "$was_active" -eq 1 ] || [ "$start" -eq 1 ]; then
    local available
    available="$(awk '/^MemAvailable:/ { print int($2 / 1024) }' /proc/meminfo 2>/dev/null || true)"
    if [ -n "$available" ] && [ "$available" -lt 300 ]; then
      warn "Only ${available} MB of memory is available; staging may push production into swap."
    fi
    log "Starting $STAGING_SERVICE"
    systemctl start "$STAGING_SERVICE"
    STOPPED=0
    wait_for_health || exit 1
    log "Stop it when you are done: systemctl stop $STAGING_SERVICE"
  else
    STOPPED=0
    log "Staging is stopped; start it with: systemctl start $STAGING_SERVICE"
  fi
}

main "$@"
exit $?
