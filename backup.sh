#!/usr/bin/env bash
# Dumps every database in the running ac-database container to a timestamped,
# gzip-compressed file under backups/, then deletes local backups older than
# BACKUP_RETENTION_DAYS. Meant to run daily via cron; safe to run by hand too.
#
# Usage:
#   ./backup.sh
#
# Env vars:
#   DEPLOY_DIR              Where the stack is deployed (default: ../azerothcore-playerbots
#                            relative to this script, same default as deploy.sh)
#   BACKUP_DIR              Where dumps are written (default: ./backups next to this script)
#   BACKUP_RETENTION_DAYS   How many days of local dumps to keep (default: 30)
#
# Restoring a dump (see README's "Migrating to another host" for the full procedure):
#   gunzip -c backups/<file>.sql.gz | docker exec -i ac-database mysql -u root -p"$DB_ROOT_PASSWORD"

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="${DEPLOY_DIR:-$SCRIPT_DIR/../azerothcore-playerbots}"
BACKUP_DIR="${BACKUP_DIR:-$SCRIPT_DIR/backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"

log() { echo "==> $*"; }

ENV_FILE="$DEPLOY_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
  echo "No .env found at $ENV_FILE — is the stack deployed?" >&2
  exit 1
fi
# shellcheck disable=SC1090
source "$ENV_FILE"

if ! docker inspect -f '{{.State.Running}}' ac-database 2>/dev/null | grep -q true; then
  echo "ac-database container isn't running — nothing to back up." >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
OUT_FILE="$BACKUP_DIR/wow-backup-$TIMESTAMP.sql.gz"
TMP_FILE="$OUT_FILE.tmp"

log "Dumping all databases to $OUT_FILE ..."
docker exec ac-database mysqldump -u root -p"$DB_ROOT_PASSWORD" --all-databases --single-transaction --quick | gzip > "$TMP_FILE"
mv "$TMP_FILE" "$OUT_FILE"
log "Done: $(du -h "$OUT_FILE" | cut -f1)"

log "Pruning backups older than $RETENTION_DAYS days ..."
find "$BACKUP_DIR" -maxdepth 1 -name 'wow-backup-*.sql.gz' -mtime "+$RETENTION_DAYS" -print -delete
