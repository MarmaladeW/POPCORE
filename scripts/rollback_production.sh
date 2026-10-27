#!/usr/bin/env bash
# rollback_production.sh: put the POPCORE droplet back on an earlier commit, optionally
# with a database backup. Run as root on the droplet:
#
#   rollback_production.sh [--restore-db BACKUP.db] [COMMIT]
#
# Without COMMIT it returns to the commit that was live before the last healthy deploy
# (from /root/popcore-backups/deploy-history.log, else git's reflog of main).
#
#   1. Preflight. Refuses unless /root/POPCORE is on main with no tracked changes and
#      COMMIT is an ancestor of the live commit (going forward is deploy_production.sh's job).
#   2. Compatibility. Lists migrations recorded in the live database that COMMIT's code
#      does not know. Old code usually ignores added tables and columns; if it does not,
#      run again with --restore-db and the matching pre-deploy backup (suggested below).
#   3. Code. git reset --keep COMMIT (tracked files only; untracked files stay) and a
#      restart of the popcore service.
#      With --restore-db the service (and Litestream, when running) is stopped first, the
#      current database files are saved to /root/popcore-backups/replaced-<UTC time>/,
#      BACKUP is checked with integrity_check and put in place, Litestream starts again
#      with fresh local state, and then popcore starts. Every write made after BACKUP was
#      taken is lost from the live database (it remains in the saved copy).
#   4. Polls /api/health until COMMIT answers ok twice (for releases older than
#      /api/health: an unauthenticated API route answering 401) and the app shell loads.
#
# Running it again without --restore-db is safe: an unchanged commit is not reset.
# --restore-db replaces the live database every time it runs.
# Exit codes: 0 healthy; 1 refused before anything changed; 3 NOT healthy: act now.
# Settings: the same POPCORE_* variables as deploy_production.sh, plus
# POPCORE_LITESTREAM_SERVICE (litestream).
set -euo pipefail

REPO_DIR="${POPCORE_REPO_DIR:-/root/POPCORE}"
APP_DIR="$REPO_DIR/popcore_app"
DB_PATH="${POPCORE_DB_PATH:-$APP_DIR/popcore.db}"
VENV="${POPCORE_VENV:-$APP_DIR/venv}"
PY="$VENV/bin/python"
BACKUP_DIR="${POPCORE_BACKUP_DIR:-/root/popcore-backups}"
SERVICE="${POPCORE_SERVICE:-popcore}"
LITESTREAM_SERVICE="${POPCORE_LITESTREAM_SERVICE:-litestream}"
HEALTH_URL="${POPCORE_HEALTH_URL:-http://127.0.0.1:5000/api/health}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-90}"
BRANCH="${POPCORE_BRANCH:-main}"
LOCK_FILE="${POPCORE_LOCK_FILE:-/run/lock/popcore-deploy.lock}"
HISTORY="$BACKUP_DIR/deploy-history.log"

HEAD_SHA="" TARGET_SHA="" RESTORE_FROM="" SAVED_DIR="" SERVICE_STOPPED=0 LITESTREAM_STOPPED=0

log()  { printf '%s  %s\n' "$(date -u +%H:%M:%S)" "$*"; }
step() { printf '\n== %s\n' "$*"; }
warn() { printf '%s  WARNING: %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }
die()  { printf '%s  STOP: %s\n' "$(date -u +%H:%M:%S)" "$1" >&2; exit "${2:-1}"; }
g()    { git -C "$REPO_DIR" "$@"; }
short() { printf '%s' "$1" | cut -c1-12; }

usage() { sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; }

pyhelp() {
  "$PY" - "$@" <<'PY'
import http.client, json, os, re, sqlite3, sys, urllib.error, urllib.request
from contextlib import closing

def connect(path):
    if not os.path.isfile(path):
        raise SystemExit(f'no database at {path}')
    return closing(sqlite3.connect(path, timeout=30))

def check(path):
    with connect(path) as con:
        result = [row[0] for row in con.execute('PRAGMA integrity_check')]
    if result != ['ok']:
        print(f'integrity_check failed: {result[:5]}', file=sys.stderr)
        return 1
    return 0

def unknown_migrations(path, source_file):
    # Every migration name is a string literal in db.py (in the list or an inline guard).
    with open(source_file, encoding='utf-8') as handle:
        source = handle.read()
    with connect(path) as con:
        live = [row[0] for row in con.execute('SELECT name FROM _migrations ORDER BY rowid')]
    print('\n'.join(name for name in live if f"'{name}'" not in source and f'"{name}"' not in source))
    return 0

def fetch(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=5) as response:
            return response.status, response.headers.get('Content-Type', ''), response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get('Content-Type', ''), error.read()

def health(url, expected):
    base = url.split('/api/health', 1)[0]
    try:
        status, _, body = fetch(url)
        if status == 404:
            # A release from before /api/health: an API route answering 401 shows the app is up.
            api, _, _ = fetch(base + '/api/stores')
            if api != 401:
                print(f'/api/health 404 and /api/stores {api}')
                return 1
            release = 'unknown (release without /api/health)'
        else:
            data = json.loads(body or b'{}')
            if status != 200 or data.get('ok') is not True:
                print(f'/api/health answered {status} {data}')
                return 1
            release = data.get('release') or 'unknown'
            if expected and re.fullmatch(r'[0-9a-f]{40}', release) and release != expected:
                print(f'still answering as {release[:12]}, waiting for {expected[:12]}')
                return 1
        shell, kind, _ = fetch(base + '/')
        if shell != 200 or 'text/html' not in kind:
            print(f'the app shell answered {shell} {kind}')
            return 1
    except (OSError, ValueError, http.client.HTTPException) as error:
        print(f'not answering ({error.__class__.__name__}: {error})')
        return 1
    print(f'ok, release {release}')
    return 0

command, args = sys.argv[1], sys.argv[2:]
sys.exit({'check': check, 'unknown-migrations': unknown_migrations, 'health': health}[command](*args))
PY
}

take_lock() {
  if command -v flock >/dev/null 2>&1; then
    mkdir -p "$(dirname "$LOCK_FILE")"
    exec 9>"$LOCK_FILE"
    flock -n 9 || die "Another deploy or rollback is running (lock $LOCK_FILE)."
  else
    warn "flock is not installed; running without the deploy lock."
  fi
}

record() {  # result
  mkdir -p "$BACKUP_DIR"
  printf '%s\trollback\tfrom=%s\tto=%s\trestored=%s\tsaved=%s\tresult=%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$HEAD_SHA" "$TARGET_SHA" "${RESTORE_FROM:--}" "${SAVED_DIR:--}" "$1" >>"$HISTORY"
}

wait_for_health() {  # expected_sha timeout_seconds
  local expected="$1" deadline passes=0 status=""
  deadline=$(( $(date +%s) + $2 ))
  while :; do
    if status="$(pyhelp health "$HEALTH_URL" "$expected" 2>&1)"; then
      passes=$((passes + 1))
      if [ "$passes" -ge 2 ]; then
        log "Healthy: $status"
        return 0
      fi
    else
      passes=0
    fi
    if [ "$(date +%s)" -ge "$deadline" ]; then
      log "Not healthy after $2s: $status"
      return 1
    fi
    if systemctl is-failed --quiet "$SERVICE" 2>/dev/null; then
      log "systemd reports $SERVICE as failed: $status"
      return 1
    fi
    sleep 2
  done
}

previous_commit() {  # prints the commit to go back to and where it came from
  local from=""
  if [ -f "$HISTORY" ]; then
    from="$(awk -F '\t' -v head="to=$HEAD_SHA" \
      '$2 == "deploy" && $4 == head && $7 == "result=healthy" { from = substr($3, 6) } END { print from }' "$HISTORY")"
  fi
  if [ -n "$from" ]; then
    printf '%s deploy-history\n' "$from"
    return
  fi
  from="$(g rev-parse --verify --quiet "$BRANCH@{1}" || true)"
  if [ -n "$from" ]; then
    printf '%s reflog\n' "$from"
  fi
}

self_command() {  # how to run this script again once the code is on TARGET_SHA
  case "$0" in
    /usr/*|/opt/*|/root/bin/*) printf '%s' "$0" ;;
    *)
      if g cat-file -e "$TARGET_SHA:scripts/rollback_production.sh" 2>/dev/null; then
        printf 'bash %s/scripts/rollback_production.sh' "$REPO_DIR"
      else
        printf 'git -C %s show %s:scripts/rollback_production.sh > /root/rollback_production.sh && bash /root/rollback_production.sh' \
          "$REPO_DIR" "$HEAD_SHA"
      fi ;;
  esac
}

backup_before() {  # commit: the pre-deploy backup taken while this commit was live, if recorded
  [ -f "$HISTORY" ] || return 0
  awk -F '\t' -v from="from=$1" '$2 == "deploy" && $3 == from && $5 != "backup=-" { b = substr($5, 8) } END { print b }' "$HISTORY"
}

check_compatibility() {
  step "Database compatibility with $(short "$TARGET_SHA")"
  local source unknown suggestion
  source="$(mktemp "${TMPDIR:-/tmp}/popcore-db-source.XXXXXX")"
  if ! g show "$TARGET_SHA:popcore_app/db.py" >"$source" 2>/dev/null; then
    rm -f "$source"
    warn "Could not read db.py at $(short "$TARGET_SHA"); skipping this check."
    return 0
  fi
  unknown="$(pyhelp unknown-migrations "$DB_PATH" "$source")" || unknown=""
  rm -f "$source"
  if [ -z "$unknown" ]; then
    log "Every migration recorded in the live database is known to $(short "$TARGET_SHA")."
    return 0
  fi
  warn "The live database has migrations from a newer release that $(short "$TARGET_SHA") does not know:"
  printf '%s\n' "$unknown" | sed 's/^/    /' >&2
  if [ -n "$RESTORE_FROM" ]; then
    return 0
  fi
  log "The old code usually ignores added tables and columns. If it misbehaves after this rollback,"
  log "put back a backup taken while $(short "$TARGET_SHA") was live (writes made since are lost):"
  suggestion="$(backup_before "$TARGET_SHA")"
  printf '\n    %s --restore-db %s %s\n\n' "$(self_command)" \
    "${suggestion:-$BACKUP_DIR/pre-deploy-<time>-$(short "$TARGET_SHA").db}" "$TARGET_SHA"
}

restore_database() {
  step "Restoring the database from $RESTORE_FROM"
  local stamp meta name
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  # Copy the backup next to the live file first, so the swap itself is a single rename.
  cp "$RESTORE_FROM" "$DB_PATH.restoring" || { rm -f "$DB_PATH.restoring"; die "Could not copy the backup next to the live database (disk space?); nothing changed."; }
  chmod 600 "$DB_PATH.restoring"
  log "Stopping $SERVICE"
  systemctl stop "$SERVICE" || { rm -f "$DB_PATH.restoring"; die "Could not stop $SERVICE; nothing changed." 3; }
  SERVICE_STOPPED=1
  if systemctl is-active --quiet "$LITESTREAM_SERVICE" 2>/dev/null; then
    log "Stopping $LITESTREAM_SERVICE (it must not replicate while the file is replaced)"
    systemctl stop "$LITESTREAM_SERVICE" || die "Could not stop $LITESTREAM_SERVICE; the database was not replaced." 3
    LITESTREAM_STOPPED=1
  fi
  SAVED_DIR="$BACKUP_DIR/replaced-$stamp"
  mkdir -p "$SAVED_DIR"
  chmod 700 "$SAVED_DIR"
  for name in "$DB_PATH" "$DB_PATH-wal" "$DB_PATH-shm"; do
    if [ -e "$name" ]; then
      cp -p "$name" "$SAVED_DIR/" || die "Could not save $name; the database was not replaced." 3
    fi
  done
  log "Saved the current database files to $SAVED_DIR"
  # A WAL left from the replaced database would be applied to the restored file.
  rm -f "$DB_PATH-wal" "$DB_PATH-shm"
  mv -f "$DB_PATH.restoring" "$DB_PATH"
  meta="$(dirname "$DB_PATH")/.$(basename "$DB_PATH")-litestream"
  if [ -d "$meta" ]; then
    mv "$meta" "$SAVED_DIR/litestream-meta"  # Litestream starts fresh for the restored file
  fi
  log "Restored $(basename "$RESTORE_FROM") as $DB_PATH"
  if [ "$LITESTREAM_STOPPED" -eq 1 ]; then
    systemctl start "$LITESTREAM_SERVICE" && LITESTREAM_STOPPED=0 && log "Started $LITESTREAM_SERVICE again"
  fi
}

on_exit() {  # never leave production stopped because this script stopped early
  rm -f "$DB_PATH.restoring"
  if [ "$LITESTREAM_STOPPED" -eq 1 ]; then
    systemctl start "$LITESTREAM_SERVICE" || warn "Could not start $LITESTREAM_SERVICE: run systemctl start $LITESTREAM_SERVICE"
  fi
  if [ "$SERVICE_STOPPED" -eq 1 ]; then
    warn "Stopped early while $SERVICE was down; starting it again."
    systemctl start "$SERVICE" || warn "Could not start $SERVICE: run systemctl start $SERVICE"
  fi
}

main() {
  local target="" source_note="" origin
  while [ $# -gt 0 ]; do
    case "$1" in
      --restore-db)
        [ $# -ge 2 ] || die "--restore-db needs a backup file."
        RESTORE_FROM="$2"
        shift ;;
      -h|--help) usage; exit 0 ;;
      -*) die "Unknown option $1 (see --help)." ;;
      *) [ -z "$target" ] || die "Give one COMMIT only."; target="$1" ;;
    esac
    shift
  done
  [ "$(id -u)" -eq 0 ] || die "Run as root."
  trap on_exit EXIT
  trap 'exit 130' INT TERM
  take_lock

  step "Preflight"
  [ -e "$REPO_DIR/.git" ] || die "$REPO_DIR is not a git checkout."
  [ -x "$PY" ] || die "The app venv's Python is missing: $PY"
  local branch dirty
  branch="$(g symbolic-ref --quiet --short HEAD || true)"
  [ "$branch" = "$BRANCH" ] || die "$REPO_DIR is on '${branch:-a detached HEAD}', not $BRANCH."
  dirty="$(g status --porcelain --untracked-files=no)"
  if [ -n "$dirty" ]; then
    printf '%s\n' "$dirty" >&2
    die "Tracked files in $REPO_DIR have local changes (above). Commit, move or discard them first."
  fi
  HEAD_SHA="$(g rev-parse --verify HEAD)"
  if [ -n "$target" ]; then
    TARGET_SHA="$(g rev-parse --verify --quiet "$target^{commit}")" || die "Unknown commit: $target"
    source_note="given"
  else
    origin="$(previous_commit)"
    [ -n "$origin" ] || die "No earlier commit found in the deploy history or reflog; give the COMMIT."
    TARGET_SHA="${origin%% *}"
    source_note="${origin#* }"
  fi
  if [ "$TARGET_SHA" != "$HEAD_SHA" ] && ! g merge-base --is-ancestor "$TARGET_SHA" "$HEAD_SHA"; then
    die "$(short "$TARGET_SHA") is not an earlier commit of the live $(short "$HEAD_SHA"); deploy forward with deploy_production.sh."
  fi
  log "Live commit:   $(g log -1 --format='%h %s (%cs)' "$HEAD_SHA")"
  log "Target commit: $(g log -1 --format='%h %s (%cs)' "$TARGET_SHA") [$source_note]"
  if [ -n "$RESTORE_FROM" ]; then
    [ -f "$RESTORE_FROM" ] || die "No backup file at $RESTORE_FROM"
    case "$RESTORE_FROM" in /*) ;; *) RESTORE_FROM="$(cd "$(dirname "$RESTORE_FROM")" && pwd -P)/$(basename "$RESTORE_FROM")" ;; esac
    [ "$RESTORE_FROM" != "$DB_PATH" ] || die "--restore-db points at the live database itself."
    pyhelp check "$RESTORE_FROM" || die "$RESTORE_FROM failed integrity_check; nothing changed."
    log "Backup to restore: $RESTORE_FROM (integrity_check ok)"
  fi
  if [ "$TARGET_SHA" = "$HEAD_SHA" ] && [ -z "$RESTORE_FROM" ]; then
    log "Production is already at this commit; checking its health only."
    wait_for_health "$TARGET_SHA" 30 && exit 0
    die "Production is at the target commit but not healthy. See docs/operations-resilience.md." 3
  fi
  check_compatibility

  if [ -n "$RESTORE_FROM" ]; then
    restore_database
  fi
  if [ "$TARGET_SHA" != "$HEAD_SHA" ]; then
    step "Code $(short "$HEAD_SHA") -> $(short "$TARGET_SHA")"
    if ! g reset --quiet --keep "$TARGET_SHA"; then
      record reset-failed
      die "git reset --keep $(short "$TARGET_SHA") failed; the code is unchanged." 3
    fi
  fi
  if [ -n "$RESTORE_FROM" ]; then
    log "Starting $SERVICE"
    systemctl start "$SERVICE" || warn "systemctl start $SERVICE reported an error."
    SERVICE_STOPPED=0
  else
    log "Restarting $SERVICE"
    systemctl restart "$SERVICE" || warn "systemctl restart $SERVICE reported an error."
  fi
  if wait_for_health "$TARGET_SHA" "$HEALTH_TIMEOUT"; then
    record healthy
    step "Production runs $(short "$TARGET_SHA")${RESTORE_FROM:+ on the restored database}"
    [ -z "$SAVED_DIR" ] || log "The replaced database files are kept in $SAVED_DIR"
    exit 0
  fi
  record unhealthy
  journalctl -u "$SERVICE" -n 40 --no-pager 2>/dev/null | sed 's/^/    /' || true
  die "Production is NOT healthy on $(short "$TARGET_SHA"). Check journalctl -u $SERVICE; see docs/operations-resilience.md." 3
}

main "$@"
exit $?
