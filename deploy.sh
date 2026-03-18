#!/usr/bin/env bash
# deploy.sh — VV Ingest Box deploy script
# Run this ON YOUR LAPTOP from the project directory.
# Copies all files to the Pi and (optionally) runs install.sh remotely.
#
# Usage:
#   bash deploy.sh <pi-ip>              # copy + prompt to install
#   bash deploy.sh <pi-ip> --install    # copy + run install.sh automatically
#   bash deploy.sh <pi-ip> --restart    # copy + restart services only (update deploy)
#
# Example:
#   bash deploy.sh 192.168.1.143 --install
set -euo pipefail

# ── Colour helpers ────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ── Args ──────────────────────────────────────────────────────────────────────
PI_IP="${1:-}"
MODE="${2:-}"

[[ -z "$PI_IP" ]] && { echo "Usage: bash deploy.sh <pi-ip> [--install|--restart]"; exit 1; }

PI_USER="${PI_USER:-pi}"
PI_HOST="$PI_USER@$PI_IP"
REMOTE_DIR="/home/$PI_USER"

# ── Files to copy ─────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COPY_FILES=(
    ui.py
    ingest.sh
    config.env
    tagger_daemon.py
    enqueue_for_tagging.py
    db.py
    detector_tflite.py
    requirements.txt
    install.sh
)

# Model files (optional — only copy if present)
OPTIONAL_FILES=(detect.tflite labels.txt)

# ── Check source files exist ──────────────────────────────────────────────────
info "Checking source files in $SCRIPT_DIR ..."
MISSING=()
for f in "${COPY_FILES[@]}"; do
    [[ -f "$SCRIPT_DIR/$f" ]] || MISSING+=("$f")
done
if [[ ${#MISSING[@]} -gt 0 ]]; then
    error "Missing files: ${MISSING[*]}"
fi

# ── Test SSH connection ───────────────────────────────────────────────────────
info "Testing SSH connection to $PI_HOST ..."
ssh -o ConnectTimeout=5 -o BatchMode=yes "$PI_HOST" "echo ok" > /dev/null 2>&1 \
    || error "Cannot SSH to $PI_HOST — check IP, user, and SSH keys"
info "SSH connection OK."

# ── Copy files ────────────────────────────────────────────────────────────────
info "Copying files to $PI_HOST:$REMOTE_DIR ..."
PATHS_TO_COPY=()
for f in "${COPY_FILES[@]}"; do
    PATHS_TO_COPY+=("$SCRIPT_DIR/$f")
done
for f in "${OPTIONAL_FILES[@]}"; do
    [[ -f "$SCRIPT_DIR/$f" ]] && PATHS_TO_COPY+=("$SCRIPT_DIR/$f") || warn "  optional not found, skipping: $f"
done

scp "${PATHS_TO_COPY[@]}" "$PI_HOST:$REMOTE_DIR/"
info "Files copied."

# ── Mode handling ─────────────────────────────────────────────────────────────
if [[ "$MODE" == "--install" ]]; then
    info "Running install.sh on Pi (this may take a few minutes)..."
    ssh -t "$PI_HOST" "sudo bash $REMOTE_DIR/install.sh"
    info "Install complete."

elif [[ "$MODE" == "--restart" ]]; then
    info "Updating installed files and restarting services..."
    ssh "$PI_HOST" "
        set -e
        sudo cp ~/ui.py ~/tagger_daemon.py ~/enqueue_for_tagging.py ~/db.py ~/detector_tflite.py ~/ingest.sh /opt/vv_ingest/
        sudo chown -R \${USER}:\${USER} /opt/vv_ingest
        chmod +x /opt/vv_ingest/ingest.sh /opt/vv_ingest/ui.py /opt/vv_ingest/tagger_daemon.py /opt/vv_ingest/enqueue_for_tagging.py
        sudo systemctl restart vv-ingest-ui vv-ingest-ai
        sleep 1
        sudo systemctl is-active vv-ingest-ui && echo 'vv-ingest-ui: running' || echo 'vv-ingest-ui: FAILED'
        sudo systemctl is-active vv-ingest-ai && echo 'vv-ingest-ai: running' || echo 'vv-ingest-ai: FAILED'
    "
    info "Restart complete."

else
    echo ""
    echo "Files copied to Pi. To finish setup, SSH in and run:"
    echo ""
    echo "  ssh $PI_HOST"
    echo "  sudo bash ~/install.sh"
    echo ""
    echo "Or re-run this script with --install to do it automatically:"
    echo ""
    echo "  bash deploy.sh $PI_IP --install"
    echo ""
fi
