#!/usr/bin/env bash
# Daily SQLite backup — run via cron: 0 2 * * * /path/to/popcore_app/backup.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DB="${POPCORE_DB_PATH:-$SCRIPT_DIR/popcore.db}"
BACKUP_DIR="${BACKUP_DIR:-$SCRIPT_DIR/backups}"
KEEP_DAYS="${KEEP_DAYS:-30}"
PRODUCT_DIR="${POPCORE_HIDDEN_IMG_DIR:-$SCRIPT_DIR/uploads/hidden_imgs}"
PAYMENT_DIR="${POPCORE_PAYMENT_EVIDENCE_DIR:-$SCRIPT_DIR/uploads/payment_evidence}"
CONDITION_DIR="${POPCORE_CONDITION_EVIDENCE_DIR:-$SCRIPT_DIR/uploads/condition_evidence}"

mkdir -p "$BACKUP_DIR"

DEST="$BACKUP_DIR/popcore_$(date +%Y%m%d_%H%M%S)"
"${PYTHON_BIN:-python3}" "$SCRIPT_DIR/../scripts/create_backup_package.py" \
  --database "$DB" --product-dir "$PRODUCT_DIR" --payment-dir "$PAYMENT_DIR" \
  --condition-dir "$CONDITION_DIR" --output "$DEST"
echo "[backup] Saved to $DEST"

# Remove backups older than KEEP_DAYS days
find "$BACKUP_DIR" -maxdepth 1 -name "popcore_*" -mtime +"$KEEP_DAYS" -exec rm -rf -- {} +
echo "[backup] Pruned backups older than $KEEP_DAYS days"
