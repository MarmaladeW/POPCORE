#!/usr/bin/env bash
# deploy_production.sh: deploy one commit to the POPCORE droplet so that a bad release
# cannot take production down. Run as root on the droplet:
#
#   deploy_production.sh [--rehearse-only] [COMMIT]        COMMIT defaults to origin/main
#
# Nothing touches the running app before step 4:
#   1. Preflight. Refuses unless /root/POPCORE is on main with no tracked changes (the
#      owner's untracked files are ignored and left alone) and COMMIT fast-forwards HEAD.
#   2. Backup. A consistent online copy of the live database through SQLite's backup API,
#      run with the app venv's Python while staff keep working, checked with
#      integrity_check and kept as
#      /root/popcore-backups/pre-deploy-<UTC time>-<current commit>.db.
#   3. Rehearsal. COMMIT is checked out in a temporary git worktree and its app is
#      imported against a COPY of that backup, with the scheduler off and no secrets, so
#      the release's own migrations run on real data. An import error, a failed
#      migration, a failed integrity_check or new foreign-key violations stop the deploy
#      here. The schema changes and the migration time are reported.
#   4. Fast-forward /root/POPCORE to COMMIT and restart the popcore service.
#   5. Poll http://127.0.0.1:5000/api/health until the new commit answers ok twice and
#      the app shell loads. If that does not happen within HEALTH_TIMEOUT seconds, the
#      code goes back to the previous commit and is restarted, and the script prints how
#      to restore the pre-deploy backup if the release changed the schema.
#
# Why the database is never restored automatically: once the new release has started it
# may have run its migrations and taken real writes (sales, counts, shifts). Copying the
# pre-deploy backup back would silently discard everything written since the backup.
# Most failed releases fail at import or before they write anything, and the previous
# code then runs on the current database as before, so this script rolls back the code
# only and keeps every write. If the release changed the schema and the previous code
# misbehaves on it, restoring is a deliberate decision: rollback_production.sh
# --restore-db saves the current database aside first, and you accept losing the writes
# made after the backup.
#
# --rehearse-only stops after step 3 and needs no root: a dry run of a release.
#
# Exit codes: 0 deployed (or already deployed) and healthy; 1 refused or stopped before
# production changed; 2 the release failed and the previous code is back and healthy;
# 3 production is NOT healthy: act now (see docs/operations-resilience.md).
#
# Settings (environment, for a dry run elsewhere): POPCORE_REPO_DIR (/root/POPCORE),
# POPCORE_DB_PATH (<repo>/popcore_app/popcore.db), POPCORE_VENV (<repo>/popcore_app/venv),
# POPCORE_BACKUP_DIR (/root/popcore-backups), POPCORE_SERVICE (popcore),
# POPCORE_HEALTH_URL (http://127.0.0.1:5000/api/health), HEALTH_TIMEOUT (90),
# POPCORE_REMOTE (origin), POPCORE_BRANCH (main), POPCORE_SKIP_FETCH=1 (no git fetch),
# POPCORE_DEPLOY_TMP (<backup dir>/tmp), POPCORE_LOCK_FILE (/run/lock/popcore-deploy.lock).
set -euo pipefail

REPO_DIR="${POPCORE_REPO_DIR:-/root/POPCORE}"
APP_DIR="$REPO_DIR/popcore_app"
DB_PATH="${POPCORE_DB_PATH:-$APP_DIR/popcore.db}"
VENV="${POPCORE_VENV:-$APP_DIR/venv}"
PY="$VENV/bin/python"
BACKUP_DIR="${POPCORE_BACKUP_DIR:-/root/popcore-backups}"
SERVICE="${POPCORE_SERVICE:-popcore}"
HEALTH_URL="${POPCORE_HEALTH_URL:-http://127.0.0.1:5000/api/health}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-90}"
REMOTE="${POPCORE_REMOTE:-origin}"
BRANCH="${POPCORE_BRANCH:-main}"
TMP_BASE="${POPCORE_DEPLOY_TMP:-$BACKUP_DIR/tmp}"
LOCK_FILE="${POPCORE_LOCK_FILE:-/run/lock/popcore-deploy.lock}"
HISTORY="$BACKUP_DIR/deploy-history.log"
SAFE_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

FROM_SHA="" TARGET_SHA="" BACKUP_PATH="" SCHEMA_CHANGED="unknown" NEW_MIGRATIONS="" SCHEMA_SUMMARY=""
REHEARSAL_DIR="" WORKTREE=""

log()  { printf '%s  %s\n' "$(date -u +%H:%M:%S)" "$*"; }
step() { printf '\n== %s\n' "$*"; }
warn() { printf '%s  WARNING: %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }
die()  { printf '%s  STOP: %s\n' "$(date -u +%H:%M:%S)" "$1" >&2; exit "${2:-1}"; }
g()    { git -C "$REPO_DIR" "$@"; }
short() { printf '%s' "$1" | cut -c1-12; }

usage() { sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; }

# One Python helper for the SQLite and HTTP work (the app venv's Python, standard library only).
pyhelp() {
  "$PY" - "$@" <<'PY'
import http.client, json, os, re, sqlite3, sys, time, urllib.error, urllib.request
from contextlib import closing

def connect(path):
    if not os.path.isfile(path):
        raise SystemExit(f'no database at {path}')
    return closing(sqlite3.connect(path, timeout=30))

def integrity(path):
    with connect(path) as con:
        return [row[0] for row in con.execute('PRAGMA integrity_check')]

def fk_violations(path):
    with connect(path) as con:
        return len(con.execute('PRAGMA foreign_key_check').fetchall())

def backup(src, dest):
    partial = dest + '.partial'
    if os.path.exists(partial):
        os.remove(partial)
    started = time.time()
    with connect(src) as source, closing(sqlite3.connect(partial)) as target:
        source.backup(target)  # one step: a consistent snapshot while WAL writers carry on
        target.execute('PRAGMA journal_mode=DELETE')  # a self-contained single file
    os.chmod(partial, 0o600)
    result = integrity(partial)
    if result != ['ok']:
        os.replace(partial, dest + '.failed-integrity')
        print(f'integrity_check failed on the backup: {result[:5]}', file=sys.stderr)
        return 1
    os.replace(partial, dest)
    print(f'{os.path.getsize(dest) / 1e6:.1f} MB in {time.time() - started:.1f}s, integrity_check ok', file=sys.stderr)
    return 0

def schema(path):
    with connect(path) as con:
        objects = {(kind, name): sql or '' for kind, name, sql in con.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")}
        try:
            migrations = [row[0] for row in con.execute('SELECT name FROM _migrations ORDER BY rowid')]
        except sqlite3.Error:
            migrations = []
    return objects, migrations

def verdict(before, after):
    result = integrity(after)
    if result != ['ok']:
        print(f'integrity_check failed after the migrations: {result[:5]}', file=sys.stderr)
        return 2
    fk_before, fk_after = fk_violations(before), fk_violations(after)
    if fk_after > fk_before:
        print(f'the migrations add {fk_after - fk_before} foreign-key violations', file=sys.stderr)
        return 3
    if fk_before:
        print(f'note: {fk_before} foreign-key violations already exist in the live database', file=sys.stderr)
    old, old_migrations = schema(before)
    new, new_migrations = schema(after)
    added = sorted(f'{kind} {name}' for kind, name in new.keys() - old.keys())
    removed = sorted(f'{kind} {name}' for kind, name in old.keys() - new.keys())
    changed = sorted(f'{kind} {name}' for kind, name in new.keys() & old.keys() if new[kind, name] != old[kind, name])
    migrations = [name for name in new_migrations if name not in set(old_migrations)]
    for label, items in (('new migrations', migrations), ('added', added), ('removed', removed), ('changed', changed)):
        if items:
            print(f'  {label}: {", ".join(items)}', file=sys.stderr)
    if not (added or removed or changed or migrations):
        print('  schema unchanged', file=sys.stderr)
    summary = '; '.join(f'{label}: {", ".join(items)}' for label, items in (
        ('new migrations', migrations), ('added', added), ('removed', removed), ('changed', changed)) if items)
    print('schema_changed=' + ('yes' if summary else 'no'))
    print('new_migrations=' + ','.join(migrations))
    print('schema_summary=' + summary)
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
sys.exit({'backup': backup, 'verdict': verdict, 'health': health}[command](*args))
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

cleanup() {
  if [ -n "$WORKTREE" ] && [ -e "$WORKTREE" ]; then
    g worktree remove --force "$WORKTREE" >/dev/null 2>&1 || { rm -rf "$WORKTREE"; g worktree prune >/dev/null 2>&1 || true; }
  fi
  if [ -n "$REHEARSAL_DIR" ] && [ -d "$REHEARSAL_DIR" ]; then
    rm -rf "$REHEARSAL_DIR"  # it holds a copy of the live data
  fi
}

record() {  # result
  mkdir -p "$BACKUP_DIR"
  printf '%s\tdeploy\tfrom=%s\tto=%s\tbackup=%s\tschema=%s:%s\tresult=%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$FROM_SHA" "$TARGET_SHA" "${BACKUP_PATH:--}" \
    "$SCHEMA_CHANGED" "${NEW_MIGRATIONS:--}" "$1" >>"$HISTORY"
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

rollback_command() {
  if command -v popcore-rollback >/dev/null 2>&1; then
    printf 'popcore-rollback'
  elif [ -f "$REPO_DIR/scripts/rollback_production.sh" ]; then
    printf 'bash %s/scripts/rollback_production.sh' "$REPO_DIR"
  else
    printf 'git -C %s show %s:scripts/rollback_production.sh > /root/rollback_production.sh && bash /root/rollback_production.sh' \
      "$REPO_DIR" "$TARGET_SHA"
  fi
}

print_restore_advice() {
  printf '\n'
  if [ "$SCHEMA_CHANGED" = "no" ]; then
    log "The release did not change the schema, so the previous code runs on the current database"
    log "as before and nothing needs restoring. The pre-deploy backup stays at $BACKUP_PATH."
    return
  fi
  log "The release changed the schema: ${SCHEMA_SUMMARY:-unknown changes}."
  log "Its migrations may already have run on the live database during the failed start. The previous"
  log "code is running on that database now; added tables and columns are normally harmless to it."
  log "Only if the previous release now misbehaves, put the pre-deploy backup back. Everything"
  log "written after the backup is lost (the current database is saved aside first):"
  printf '\n    %s --restore-db %s %s\n\n' "$(rollback_command)" "$BACKUP_PATH" "$FROM_SHA"
}

preflight() {  # target
  [ -e "$REPO_DIR/.git" ] || die "$REPO_DIR is not a git checkout."
  [ -x "$PY" ] || die "The app venv's Python is missing: $PY"
  [ -f "$DB_PATH" ] || die "The live database is missing: $DB_PATH"
  local branch dirty
  branch="$(g symbolic-ref --quiet --short HEAD || true)"
  [ "$branch" = "$BRANCH" ] || die "$REPO_DIR is on '${branch:-a detached HEAD}', not $BRANCH."
  dirty="$(g status --porcelain --untracked-files=no)"
  if [ -n "$dirty" ]; then
    printf '%s\n' "$dirty" >&2
    die "Tracked files in $REPO_DIR have local changes (above). Commit, move or discard them first."
  fi
  if [ -e "$(g rev-parse --git-path MERGE_HEAD)" ] || [ -e "$(g rev-parse --git-path rebase-merge)" ] \
      || [ -e "$(g rev-parse --git-path rebase-apply)" ]; then
    die "A git merge or rebase is in progress in $REPO_DIR."
  fi
  if [ "${POPCORE_SKIP_FETCH:-0}" != 1 ]; then
    log "Fetching $REMOTE"
    g fetch --quiet --prune "$REMOTE" || die "git fetch $REMOTE failed."
  fi
  FROM_SHA="$(g rev-parse --verify HEAD)"
  TARGET_SHA="$(g rev-parse --verify --quiet "$1^{commit}")" || die "Unknown commit: $1"
  if [ "$FROM_SHA" != "$TARGET_SHA" ] && ! g merge-base --is-ancestor "$FROM_SHA" "$TARGET_SHA"; then
    die "$1 does not fast-forward from the live commit $(short "$FROM_SHA"). To go back, use rollback_production.sh."
  fi
  if g rev-parse --verify --quiet "$REMOTE/$BRANCH" >/dev/null \
      && ! g merge-base --is-ancestor "$TARGET_SHA" "$REMOTE/$BRANCH"; then
    warn "$(short "$TARGET_SHA") is not on $REMOTE/$BRANCH; later deploys of $REMOTE/$BRANCH fast-forward only if it is merged."
  fi
}

check_space() {
  local db_kb avail_kb
  db_kb=$(( ($(wc -c <"$DB_PATH") + 1023) / 1024 ))
  avail_kb="$(df -Pk "$BACKUP_DIR" | awk 'NR == 2 { print $4 }')"
  if [ "$avail_kb" -lt $(( db_kb * 3 + 204800 )) ]; then
    die "Only $(( avail_kb / 1024 )) MB free in $BACKUP_DIR; the backup and rehearsal need about $(( (db_kb * 3 + 204800) / 1024 )) MB."
  fi
}

backup_live_db() {
  local stamp dest
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  dest="$BACKUP_DIR/pre-deploy-$stamp-$(short "$FROM_SHA").db"
  step "Backup of the live database"
  pyhelp backup "$DB_PATH" "$dest" || die "The pre-deploy backup failed; production untouched."
  BACKUP_PATH="$dest"
  log "Saved $BACKUP_PATH"
}

rehearse() {
  step "Rehearsal: $(short "$TARGET_SHA") against a copy of the backup"
  mkdir -p "$TMP_BASE"
  chmod 700 "$TMP_BASE"
  REHEARSAL_DIR="$(mktemp -d "$TMP_BASE/rehearsal.XXXXXX")"
  WORKTREE="$REHEARSAL_DIR/code"
  g worktree add --quiet --detach "$WORKTREE" "$TARGET_SHA" >/dev/null \
    || die "Could not check out $(short "$TARGET_SHA") into a temporary worktree."
  cp "$BACKUP_PATH" "$REHEARSAL_DIR/rehearsal.db"
  mkdir -p "$REHEARSAL_DIR/home" "$REHEARSAL_DIR/tmp"
  local out="$REHEARSAL_DIR/import.log" seconds verdict rc=0
  # A clean environment: no production secrets, no scheduler, no Clover, no Sentry.
  if ! (cd "$WORKTREE/popcore_app" && env -i PATH="$SAFE_PATH" HOME="$REHEARSAL_DIR/home" LANG=C.UTF-8 \
        TMPDIR="$REHEARSAL_DIR/tmp" PYTHONDONTWRITEBYTECODE=1 \
        POPCORE_DB_PATH="$REHEARSAL_DIR/rehearsal.db" \
        POPCORE_HIDDEN_IMG_DIR="$REHEARSAL_DIR/uploads/hidden_imgs" \
        POPCORE_PAYMENT_EVIDENCE_DIR="$REHEARSAL_DIR/uploads/payment_evidence" \
        POPCORE_CONDITION_EVIDENCE_DIR="$REHEARSAL_DIR/uploads/condition_evidence" \
        DISABLE_SCHEDULER=1 AUTH0_DOMAIN=rehearsal.invalid APP_ENV=rehearsal SENTRY_DSN= \
        "$PY" -c 'import time; t = time.time(); import app; print("%.1f" % (time.time() - t))' \
        >"$out" 2>&1); then
    tail -n 40 "$out" >&2
    die "The release failed to start on a copy of the live data (above); production untouched. Backup: $BACKUP_PATH"
  fi
  seconds="$(tail -n 1 "$out")"
  log "The release imports and migrates a copy of the live data in ${seconds}s (about the restart downtime)."
  verdict="$(pyhelp verdict "$BACKUP_PATH" "$REHEARSAL_DIR/rehearsal.db")" || rc=$?
  [ "$rc" -eq 0 ] || die "The rehearsed database failed its checks (above); production untouched."
  SCHEMA_CHANGED="$(printf '%s\n' "$verdict" | sed -n 's/^schema_changed=//p')"
  NEW_MIGRATIONS="$(printf '%s\n' "$verdict" | sed -n 's/^new_migrations=//p')"
  SCHEMA_SUMMARY="$(printf '%s\n' "$verdict" | sed -n 's/^schema_summary=//p')"
  cleanup
  WORKTREE="" REHEARSAL_DIR=""
}

main() {
  local rehearse_only=0 target=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --rehearse-only) rehearse_only=1 ;;
      -h|--help) usage; exit 0 ;;
      -*) die "Unknown option $1 (see --help)." ;;
      *) [ -z "$target" ] || die "Give one COMMIT only."; target="$1" ;;
    esac
    shift
  done
  target="${target:-$REMOTE/$BRANCH}"
  if [ "$rehearse_only" -eq 0 ] && [ "$(id -u)" -ne 0 ]; then
    die "Run as root (or add --rehearse-only for a dry run)."
  fi
  trap cleanup EXIT
  trap 'exit 130' INT TERM
  if [ ! -d "$BACKUP_DIR" ]; then
    mkdir -p "$BACKUP_DIR"
    chmod 700 "$BACKUP_DIR"
  fi
  take_lock

  step "Preflight"
  preflight "$target"
  log "Live commit:   $(g log -1 --format='%h %s (%cs)' "$FROM_SHA")"
  log "Target commit: $(g log -1 --format='%h %s (%cs)' "$TARGET_SHA")"
  if [ "$FROM_SHA" = "$TARGET_SHA" ]; then
    log "Production is already at this commit; checking its health only."
    [ "$rehearse_only" -eq 1 ] && exit 0
    wait_for_health "$TARGET_SHA" 30 && exit 0
    die "Production is at the target commit but not healthy. See docs/operations-resilience.md." 3
  fi
  log "$(g rev-list --count "$FROM_SHA..$TARGET_SHA") new commits:"
  g log --oneline --no-decorate -n 25 "$FROM_SHA..$TARGET_SHA" | sed 's/^/    /'
  if ! g diff --quiet "$FROM_SHA" "$TARGET_SHA" -- popcore_app/requirements.txt; then
    warn "popcore_app/requirements.txt changes. The rehearsal uses the current venv, so a missing package stops it. Install first with:"
    printf '    git -C %s show %s:popcore_app/requirements.txt > /root/requirements-next.txt && %s/bin/pip install -r /root/requirements-next.txt\n' \
      "$REPO_DIR" "$(short "$TARGET_SHA")" "$VENV" >&2
  fi
  check_space

  backup_live_db
  rehearse
  if [ "$rehearse_only" -eq 1 ]; then
    step "Rehearsal passed; production untouched (--rehearse-only)"
    exit 0
  fi

  step "Deploy $(short "$FROM_SHA") -> $(short "$TARGET_SHA")"
  if ! g merge --ff-only --quiet "$TARGET_SHA"; then
    record merge-failed
    die "git merge --ff-only failed. HEAD is $(g rev-parse --short HEAD); the service was not restarted."
  fi
  log "Restarting $SERVICE"
  systemctl restart "$SERVICE" || warn "systemctl restart $SERVICE reported an error."
  if wait_for_health "$TARGET_SHA" "$HEALTH_TIMEOUT"; then
    record healthy
    step "Deployed $(short "$TARGET_SHA"). Pre-deploy backup: $BACKUP_PATH"
    exit 0
  fi

  step "The release is not healthy: rolling the code back to $(short "$FROM_SHA")"
  journalctl -u "$SERVICE" -n 40 --no-pager 2>/dev/null | sed 's/^/    /' || true
  if ! g reset --quiet --keep "$FROM_SHA"; then
    record rollback-failed
    die "git reset --keep $FROM_SHA failed; production runs the broken release. Run: $(rollback_command) $FROM_SHA" 3
  fi
  systemctl restart "$SERVICE" || warn "systemctl restart $SERVICE reported an error."
  if wait_for_health "$FROM_SHA" "$HEALTH_TIMEOUT"; then
    record rolled-back
    print_restore_advice
    die "Release $(short "$TARGET_SHA") failed its health check; production is back on $(short "$FROM_SHA") and healthy." 2
  fi
  record rollback-unhealthy
  print_restore_advice
  die "Production is NOT healthy on the previous commit either. Check journalctl -u $SERVICE; see docs/operations-resilience.md." 3
}

main "$@"
exit $?
