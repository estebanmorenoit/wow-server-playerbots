#!/usr/bin/env bash
# Dumps every database in the ac-database container to a timestamped,
# gzip-compressed file under backups/, then deletes local backups older than
# BACKUP_RETENTION_DAYS. Meant to run daily via cron; safe to run by hand too.
# If the game stack is asleep (see wake-proxy's auto-sleep), wakes just the
# database for the duration of the backup and puts it back to sleep after —
# a fixed-time cron would otherwise fail most nights, since the stack sits
# stopped whenever nobody's playing.
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

# wake-proxy stops the whole stack (ac-database included) after 15 minutes
# idle, which is the normal state most of the day — a fixed-time cron would
# otherwise fail on any night nobody happens to be playing at that exact
# moment. Wake just the database here (not the whole game stack — no need
# to make worldserver/authserver think a player connected) and put it back
# exactly as found.
WE_STARTED_DB=0
if ! docker inspect -f '{{.State.Running}}' ac-database 2>/dev/null | grep -q true; then
  log "ac-database isn't running (stack is asleep) — starting it just for the backup ..."
  ( cd "$DEPLOY_DIR" && docker compose up -d ac-database )
  WE_STARTED_DB=1
  for _ in $(seq 1 30); do
    if docker inspect -f '{{.State.Health.Status}}' ac-database 2>/dev/null | grep -q healthy; then
      break
    fi
    sleep 2
  done
fi

stop_db_if_we_started_it() {
  if [ "$WE_STARTED_DB" = "1" ]; then
    log "Putting ac-database back to sleep (as found) ..."
    ( cd "$DEPLOY_DIR" && docker compose stop ac-database )
  fi
}
trap stop_db_if_we_started_it EXIT

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
