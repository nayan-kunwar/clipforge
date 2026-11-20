#!/usr/bin/env bash
# Nightly logical backup for the ClipForge Docker deployment.
# Usage: ./scripts/backup.sh
# Optional: BACKUP_DIR=/mnt/backups ./scripts/backup.sh
set -euo pipefail

cd "$(dirname "$0")/.."
STAMP="$(date -u +%Y%m%d-%H%M%S)"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR" 2>/dev/null || true

echo "[backup] sqlite -> $BACKUP_DIR/clipforge-$STAMP.db"
docker compose exec -T web python - <<'PY'
import pathlib
import sqlite3

source = pathlib.Path("/data/clipforge.db")
target = pathlib.Path("/data/clipforge-backup.db")
target.unlink(missing_ok=True)
with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
    src.backup(dst)
print("sqlite backup written")
PY
docker compose cp web:/data/clipforge-backup.db "$BACKUP_DIR/clipforge-$STAMP.db"
docker compose exec -T web rm -f /data/clipforge-backup.db
chmod 600 "$BACKUP_DIR/clipforge-$STAMP.db"

echo "[backup] manifests"
docker compose exec -T web sh -c \
  "cd /data/users && find . -name CLIPS_MANIFEST.json -print0 | tar -czf /data/manifests-backup.tar.gz --null -T - || true"
docker compose cp web:/data/manifests-backup.tar.gz "$BACKUP_DIR/manifests-$STAMP.tar.gz"
docker compose exec -T web rm -f /data/manifests-backup.tar.gz
chmod 600 "$BACKUP_DIR/manifests-$STAMP.tar.gz"

if [ -f .env ]; then
  echo "[backup] environment (contains secrets)"
  cp .env "$BACKUP_DIR/env-$STAMP.txt"
  chmod 600 "$BACKUP_DIR/env-$STAMP.txt"
fi

echo "[backup] done"
echo "Copy $BACKUP_DIR to offsite storage (restic/rclone/Storage Box) and test a restore."
