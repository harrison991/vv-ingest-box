#!/usr/bin/env bash
# install.sh — VV Ingest Box setup script
# Run this ON the Raspberry Pi after copying files to /home/pi/
# Usage: bash /home/pi/install.sh
set -euo pipefail

INSTALL_DIR="/opt/vv_ingest"
DATA_DIR="/var/lib/vv_ingest"
LOG_DIR="/var/log/vv_ingest"
VENV="$INSTALL_DIR/venv"
SERVICE_USER="${SUDO_USER:-pi}"

# ── Colour helpers ────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }
section() { echo -e "\n${GREEN}━━━ $* ━━━${NC}"; }

# ── Root check ────────────────────────────────────────────────────────────────
[[ $EUID -ne 0 ]] && error "Run as root: sudo bash install.sh"

# ── Source files must exist in the same dir as this script ───────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REQUIRED_FILES=(ui.py ingest.sh config.env tagger_daemon.py enqueue_for_tagging.py db.py detector_tflite.py)
for f in "${REQUIRED_FILES[@]}"; do
    [[ -f "$SCRIPT_DIR/$f" ]] || error "Missing required file: $SCRIPT_DIR/$f"
done

# ── 1. System directories ─────────────────────────────────────────────────────
section "Creating system directories"
mkdir -p "$INSTALL_DIR/models"
mkdir -p "$INSTALL_DIR/ai_frames"
mkdir -p "$DATA_DIR"
mkdir -p "$LOG_DIR"
info "Directories created."

# ── 2. Copy files ─────────────────────────────────────────────────────────────
section "Installing application files"
CORE_FILES=(ui.py ingest.sh config.env tagger_daemon.py enqueue_for_tagging.py db.py detector_tflite.py)
for f in "${CORE_FILES[@]}"; do
    cp "$SCRIPT_DIR/$f" "$INSTALL_DIR/$f"
    info "  copied $f"
done

# Copy requirements.txt if present
[[ -f "$SCRIPT_DIR/requirements.txt" ]] && cp "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/requirements.txt"

# ── 3. Ownership ──────────────────────────────────────────────────────────────
section "Setting ownership to $SERVICE_USER"
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
chown -R "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR"
chown -R "$SERVICE_USER:$SERVICE_USER" "$LOG_DIR"

# ── 4. Executable bits ────────────────────────────────────────────────────────
section "Setting executable permissions"
chmod +x "$INSTALL_DIR/ingest.sh"
chmod +x "$INSTALL_DIR/ui.py"
chmod +x "$INSTALL_DIR/tagger_daemon.py"
chmod +x "$INSTALL_DIR/enqueue_for_tagging.py"

# ── 5. APT dependencies ───────────────────────────────────────────────────────
section "Installing APT packages"
apt-get update -qq
apt-get install -y \
    python3-pip \
    python3-venv \
    ffmpeg \
    sqlite3 \
    rsync \
    udisks2 \
    smartmontools \
    i2c-tools
info "APT packages installed."

# ── 6. Enable I2C ─────────────────────────────────────────────────────────────
section "Enabling I2C interface"
if ! grep -q "^dtparam=i2c_arm=on" /boot/config.txt 2>/dev/null && \
   ! grep -q "^dtparam=i2c_arm=on" /boot/firmware/config.txt 2>/dev/null; then
    # Try the newer firmware path first, fall back to legacy
    CONFIG_TXT="/boot/firmware/config.txt"
    [[ -f "$CONFIG_TXT" ]] || CONFIG_TXT="/boot/config.txt"
    echo "dtparam=i2c_arm=on" >> "$CONFIG_TXT"
    info "I2C enabled in $CONFIG_TXT (reboot required)"
else
    info "I2C already enabled."
fi
# Load i2c-dev module now (for current session)
modprobe i2c-dev 2>/dev/null || true

# ── 7. Python virtual environment ─────────────────────────────────────────────
section "Creating Python virtual environment"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip -q

REQUIREMENTS_FILE="$INSTALL_DIR/requirements.txt"
if [[ -f "$REQUIREMENTS_FILE" ]]; then
    info "Installing from requirements.txt"
    "$VENV/bin/pip" install -r "$REQUIREMENTS_FILE"
else
    info "Installing packages directly"
    "$VENV/bin/pip" install \
        adafruit-circuitpython-ssd1306 \
        pillow \
        gpiozero \
        opencv-python-headless \
        numpy \
        tflite-runtime
fi
info "Python environment ready."

# ── 8. systemd: OLED UI service ───────────────────────────────────────────────
section "Installing systemd service: vv-ingest-ui"
cat > /etc/systemd/system/vv-ingest-ui.service <<EOF
[Unit]
Description=VV Ingest OLED UI
After=network.target

[Service]
User=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$VENV/bin/python $INSTALL_DIR/ui.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# ── 9. systemd: AI Tagger service ─────────────────────────────────────────────
section "Installing systemd service: vv-ingest-ai"
cat > /etc/systemd/system/vv-ingest-ai.service <<EOF
[Unit]
Description=VV Ingest AI Tagger
After=network.target

[Service]
User=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$VENV/bin/python $INSTALL_DIR/tagger_daemon.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable vv-ingest-ui vv-ingest-ai
info "Services enabled."

# ── 10. udev auto-ingest rule ─────────────────────────────────────────────────
section "Installing udev rule"
cat > /etc/udev/rules.d/99-vv-ingest.rules <<EOF
ACTION=="add", SUBSYSTEM=="block", ENV{ID_FS_USAGE}=="filesystem", RUN+="$INSTALL_DIR/ingest.sh"
EOF
udevadm control --reload-rules
udevadm trigger
info "udev rule installed."

# ── 11. Log rotation ──────────────────────────────────────────────────────────
section "Configuring log rotation"
cat > /etc/logrotate.d/vv_ingest <<EOF
$LOG_DIR/*.log {
    daily
    rotate 14
    compress
    missingok
    notifempty
    copytruncate
}
EOF
info "Log rotation configured (14 days, daily)."

# ── 12. Model files check ─────────────────────────────────────────────────────
section "Checking AI model files"
MODEL_DIR="$INSTALL_DIR/models"
if [[ -f "$SCRIPT_DIR/detect.tflite" && -f "$SCRIPT_DIR/labels.txt" ]]; then
    cp "$SCRIPT_DIR/detect.tflite" "$MODEL_DIR/detect.tflite"
    cp "$SCRIPT_DIR/labels.txt"    "$MODEL_DIR/labels.txt"
    chown "$SERVICE_USER:$SERVICE_USER" "$MODEL_DIR/detect.tflite" "$MODEL_DIR/labels.txt"
    info "Model files installed."
else
    warn "Model files not found in $SCRIPT_DIR"
    warn "Copy detect.tflite and labels.txt to $MODEL_DIR before starting the AI service."
fi

# ── 13. Start services (only if model files present) ─────────────────────────
section "Starting services"
if [[ -f "$MODEL_DIR/detect.tflite" && -f "$MODEL_DIR/labels.txt" ]]; then
    systemctl start vv-ingest-ui vv-ingest-ai
    sleep 2
    systemctl is-active --quiet vv-ingest-ui && info "vv-ingest-ui: running" || warn "vv-ingest-ui: failed to start (check: journalctl -u vv-ingest-ui)"
    systemctl is-active --quiet vv-ingest-ai && info "vv-ingest-ai: running" || warn "vv-ingest-ai: failed to start (check: journalctl -u vv-ingest-ai)"
else
    warn "Skipping service start — add model files first, then run:"
    warn "  sudo systemctl start vv-ingest-ui vv-ingest-ai"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}━━━ Installation complete ━━━${NC}"
echo ""
echo "Next steps:"
echo "  1. Edit config:   nano $INSTALL_DIR/config.env"
echo "  2. Check I2C:     i2cdetect -y 1"
if [[ ! -f "$MODEL_DIR/detect.tflite" ]]; then
echo "  3. Add models:    copy detect.tflite + labels.txt to $MODEL_DIR/"
echo "  4. Start:         sudo systemctl start vv-ingest-ui vv-ingest-ai"
else
echo "  3. Check status:  sudo systemctl status vv-ingest-ui vv-ingest-ai"
fi
echo "  Logs:             tail -f $LOG_DIR/ingest.log"
echo ""

# Remind about reboot if I2C was just enabled
if dmesg 2>/dev/null | grep -q "i2c" 2>/dev/null; then
    true
else
    warn "A reboot may be required for I2C to become active."
fi
