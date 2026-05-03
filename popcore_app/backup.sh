#!/usr/bin/env bash
# Daily SQLite backup — run via cron: 0 2 * * * /path/to/popcore_app/backup.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DB="$SCRIPT_DIR/popcore.db"
BACKUP_DIR="${BACKUP_DIR:-$SCRIPT_DIR/backups}"
KEEP_DAYS="${KEEP_DAYS:-30}"

mkdir -p "$BACKUP_DIR"

DEST="$BACKUP_DIR/popcore_$(date +%Y%m%d_%H%M%S).db"
sqlite3 "$DB" ".backup '$DEST'"
echo "[backup] Saved to $DEST"

# Remove backups older than KEEP_DAYS days
find "$BACKUP_DIR" -name "popcore_*.db" -mtime +"$KEEP_DAYS" -delete
echo "[backup] Pruned backups older than $KEEP_DAYS days"
