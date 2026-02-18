#!/usr/bin/env bash
set -euo pipefail

# Load config
source /opt/vv_ingest/config.env

STATE_JSON="/var/lib/vv_ingest/state.json"
LOG_FILE="/var/log/vv_ingest/ingest.log"
LOCK_FILE="/var/lib/vv_ingest/ingest.lock"

log() {
    echo "[$(date '+%F %T')] $*" | tee -a "$LOG_FILE"
}

set_state() {
    # Minimal JSON writer
    # Usage: set_state "mode" "message" "progress"
    local mode="$1"
    local msg="$2"
    local prog="${3:-}"
    cat > "$STATE_JSON" <<EOF
{"time":"$(date '+%F %T')","mode":"$mode","message":"$msg","progress":"$prog"}
EOF
}

# Prevent overlapping ingests
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    log "Ingest already running; exiting"
    exit 0
fi

log "==== Ingest triggered ===="
set_state "detect" "Detecting source..." ""

# Find newest mount inside SOURCE_PARENT
# We pick the most recently modified directory (common for auto-mount)
if [[ ! -d "$SOURCE_PARENT" ]]; then
    log "SOURCE_PARENT not found: $SOURCE_PARENT"
    set_state "error" "No /media mount" ""
    exit 1
fi

SOURCE="$(ls -td "$SOURCE_PARENT"/* 2>/dev/null | head -n 1 || true)"
if [[ -z "${SOURCE}" || ! -d "${SOURCE}" ]]; then
    log "No mounted source found under $SOURCE_PARENT"
    set_state "idle" "Waiting for GoPro" ""
    exit 0
fi

log "Source mount: $SOURCE"
set_state "mount" "Source detected" "$(basename "$SOURCE")"

# Create destination for folder by date
STAMP="(date '+%Y-%m-%d_%H-%M-%S')"
DEST_FOLDER="$DEST_BASE/$STAMP"
mkdir -p "$DEST_FOLDER"

log "Destination: $DEST_FOLDER"
set_state "copy" "Copying..." "0%"

# Copy with rsync (keeps original structure + resumes if interrupted)
# --info=progress2 prints a continuous progress line; we parse it
RSYNC_OUT="/var/lib/vv_ingest/rsync.out"
: > "$RSYNC_OUT"

# Run rsync and capture output for progress
# NOTE: checksumming (-c) is slower; instead do a normal copy then a quick sync
( rsync -a --no-perms --no-owner --no-group --info=progress2 "$SOURCE"/ "$DEST_FOLDER"/ ) 2>&1 | tee "$RSYNC_OUT" | while read -r line; do
    # try and extract % from lines like: " 1,234,567,890 12% ..."
    if [[ "$line" =~ ([0-9]{1,3})% ]]; then
        pct="${BASH_REMATCH[1]}&"
        set_state "copy" "Copying..." "$pct"
    fi
done

sync
set_state "done" "Backup complete" "Safe to unplug"
log "Copy complete"

# try and unmount the drive safely
set_state "eject" "Ejecting..." ""
log "Attempting unmount: $SOURCE"
if unmount "$SOURCE" 2>/dev/null; then
    log "Unmounted $SOURCE"
    set_state "done" "Backup complete" "Safe to unplug"
else
    log "Unmount failed (may be MTP or busy)"
    set_state "warn" "Backup done" "Unplug camera safely"
fi

log "==== Ingest finished ===="
exit 0