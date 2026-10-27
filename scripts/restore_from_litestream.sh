#!/usr/bin/env bash
# restore_from_litestream.sh: restore the POPCORE database from the Litestream replica into
# a NEW file and check it. It never touches the live database. Run as root:
#
#   restore_from_litestream.sh [-t TIMESTAMP] [-o OUTPUT.db]
#
#   -t  a point in time in UTC, e.g. 2026-10-27T15:04:05Z (default: the latest copy)
#   -o  the new file to write (default /root/popcore-restore/popcore-<UTC time>.db);
#       it must not exist yet
#
# The replica comes from /etc/litestream.yml and the bucket credentials from
# /etc/litestream.env. After the restore it runs PRAGMA integrity_check and
# foreign_key_check and prints every table's row count with its newest created_at, so you
# can see how recent the copy is. Exit codes: 0 restored and intact; 1 nothing restored;
# 2 restored but integrity_check failed.
#
# Settings: LITESTREAM_CONFIG (/etc/litestream.yml), LITESTREAM_ENV_FILE
# (/etc/litestream.env), POPCORE_DB_PATH (the database path as listed in the config,
# /root/POPCORE/popcore_app/popcore.db), POPCORE_RESTORE_DIR (/root/popcore-restore),
# PYTHON (the app venv's python, else python3).
set -euo pipefail

CONFIG="${LITESTREAM_CONFIG:-/etc/litestream.yml}"
ENV_FILE="${LITESTREAM_ENV_FILE:-/etc/litestream.env}"
DB_PATH="${POPCORE_DB_PATH:-/root/POPCORE/popcore_app/popcore.db}"
RESTORE_DIR="${POPCORE_RESTORE_DIR:-/root/popcore-restore}"
PYTHON="${PYTHON:-}"

die() { printf 'STOP: %s\n' "$1" >&2; exit "${2:-1}"; }
usage() { sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; }

absolute() {  # the absolute path of a file that may not exist yet
  local dir
  dir="$(cd "$(dirname "$1")" 2>/dev/null && pwd -P)" || return 1
  printf '%s/%s' "$dir" "$(basename "$1")"
}

main() {
  local timestamp="" output="" live_abs output_abs
  while getopts ':t:o:h' option; do
    case "$option" in
      t) timestamp="$OPTARG" ;;
      o) output="$OPTARG" ;;
      h) usage; exit 0 ;;
      *) die "Unknown option (see -h)." ;;
    esac
  done
  shift $((OPTIND - 1))
  [ $# -eq 0 ] || die "Unexpected argument: $1 (see -h)."

  command -v litestream >/dev/null 2>&1 || die "litestream is not installed (see docs/operations-resilience.md)."
  [ -f "$CONFIG" ] || die "No Litestream config at $CONFIG."
  if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    set +a
  fi
  if [ -z "$PYTHON" ]; then
    if [ -x "$(dirname "$DB_PATH")/venv/bin/python" ]; then
      PYTHON="$(dirname "$DB_PATH")/venv/bin/python"
    else
      PYTHON="python3"
    fi
  fi

  if [ -z "$output" ]; then
    mkdir -p "$RESTORE_DIR"
    chmod 700 "$RESTORE_DIR"
    output="$RESTORE_DIR/popcore-$(date -u +%Y%m%dT%H%M%SZ).db"
  fi
  output_abs="$(absolute "$output")" || die "The folder for $output does not exist."
  live_abs="$(absolute "$DB_PATH" || printf '%s' "$DB_PATH")"
  [ "$output_abs" != "$live_abs" ] || die "Refusing to restore over the live database $DB_PATH."
  for suffix in "" -wal -shm; do
    [ ! -e "$output_abs$suffix" ] || die "$output_abs$suffix already exists; choose a new file."
  done

  printf 'Restoring %s%s into %s\n' "$DB_PATH" "${timestamp:+ as of $timestamp}" "$output_abs"
  if [ -n "$timestamp" ]; then
    litestream restore -config "$CONFIG" -o "$output_abs" -timestamp "$timestamp" "$DB_PATH" \
      || die "litestream restore failed; nothing was written over anything."
  else
    litestream restore -config "$CONFIG" -o "$output_abs" "$DB_PATH" \
      || die "litestream restore failed; nothing was written over anything."
  fi
  [ -f "$output_abs" ] || die "litestream finished but wrote no file (an empty replica?)."
  chmod 600 "$output_abs"

  local checks=0
  "$PYTHON" - "$output_abs" <<'PY' || checks=$?
import sqlite3, sys
from contextlib import closing

path = sys.argv[1]
with closing(sqlite3.connect(path)) as con:
    integrity = [row[0] for row in con.execute('PRAGMA integrity_check')]
    violations = con.execute('PRAGMA foreign_key_check').fetchall()
    tables = [row[0] for row in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    print(f'\nintegrity_check: {"ok" if integrity == ["ok"] else integrity[:10]}')
    print(f'foreign_key_check: {len(violations)} violation(s)')
    for table, rowid, parent, _ in violations[:10]:
        print(f'  {table} row {rowid} -> missing {parent}')
    print(f'\n{"table":40} {"rows":>9}  newest created_at')
    for table in tables:
        quoted = '"' + table.replace('"', '""') + '"'
        rows = con.execute(f'SELECT COUNT(*) FROM {quoted}').fetchone()[0]
        columns = {row[1] for row in con.execute(f'PRAGMA table_info({quoted})')}
        newest = con.execute(f'SELECT MAX(created_at) FROM {quoted}').fetchone()[0] if 'created_at' in columns else ''
        print(f'{table:40} {rows:>9}  {newest or ""}')
    try:
        last = con.execute('SELECT name, applied_at FROM _migrations ORDER BY rowid DESC LIMIT 1').fetchone()
        print(f'\nlatest migration: {last[0]} ({last[1]})')
    except sqlite3.Error:
        print('\nno _migrations table: this is not a POPCORE database')
sys.exit(0 if integrity == ['ok'] else 2)
PY
  printf '\nRestored copy: %s\n' "$output_abs"
  [ "$checks" -eq 0 ] || printf 'WARNING: the restored copy FAILED integrity_check; try an earlier -t time.\n' >&2
  printf 'The live database was not touched. To use this copy, see "Server lost" and\n'
  printf '"Data changed by mistake" in docs/operations-resilience.md.\n'
  return "$checks"
}

main "$@"
exit $?
