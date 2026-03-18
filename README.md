# VV Ingest Box

Automated GoPro footage ingestion, OLED telemetry, and AI tagging on a Raspberry Pi.

Plug in a GoPro (or any USB camera drive) — the box copies footage to the backup drive, tags it with object detection, and shows live status on a small OLED screen. Everything runs headlessly as systemd services.

**Audience:** James (or anyone deploying/maintaining the box at VV)

---

## Table of Contents

1. [What It Does](#what-it-does)
2. [Hardware](#hardware)
3. [File Overview](#file-overview)
4. [Quick Start (Automated)](#quick-start-automated)
5. [Manual Setup](#manual-setup)
6. [config.env Reference](#configenv-reference)
7. [OLED Screens](#oled-screens)
8. [AI Tagging](#ai-tagging)
9. [Updating the System](#updating-the-system)
10. [Logs & Monitoring](#logs--monitoring)
11. [Troubleshooting](#troubleshooting)

---

## What It Does

| Feature | Description |
|---|---|
| **Auto Ingest** | udev detects camera/drive insertion and triggers `ingest.sh` automatically |
| **rsync Copy** | Files are copied with resume support; progress shown live on OLED |
| **Duplicate Detection** | SHA-256 signature check skips files already processed |
| **AI Tagging** | TFLite object detection runs on video frames and images, writes `.tags.json` sidecar files and stores results in SQLite |
| **OLED UI** | 6-screen button-cycled display: status, storage, drive health, network, logs, AI queue |
| **UPS Protection** | Designed to work with a UPS HAT — configure shutdown behaviour separately |
| **Time-Window Processing** | AI tagging only runs during configured hours to avoid throttling during shoots |

---

## Hardware

<!-- TODO: Fill in exact part numbers/models used at VV -->

| Component | Details |
|---|---|
| **Raspberry Pi** | <!-- e.g. Raspberry Pi 4 Model B 4GB --> |
| **OLED Display** | SSD1306 128×64 I2C — default address `0x3C` |
| **UPS HAT** | <!-- e.g. Waveshare UPS HAT (C), PiSugar, etc. --> |
| **Backup Drive** | <!-- e.g. 4TB Seagate Portable HDD, drive label --> |
| **Button** | Momentary push-button on GPIO **17** (default) |
| **I2C Wiring** | OLED SDA → Pi Pin 3 (GPIO 2), SCL → Pi Pin 5 (GPIO 3) |
| **Button Wiring** | One leg → GPIO 17 (Pin 11), other leg → GND (Pin 9) |

> If your OLED uses address `0x3D` or your button is on a different GPIO pin, update `config.env` accordingly.

---

## File Overview

```
vv-ingest-box/
├── config.env              # All runtime configuration
├── ui.py                   # OLED display + button service
├── ingest.sh               # Triggered by udev on drive insertion
├── tagger_daemon.py        # AI tagging background service
├── enqueue_for_tagging.py  # Adds ingested files to AI queue
├── db.py                   # SQLite helper (dedup + tag storage)
├── detector_tflite.py      # TFLite inference wrapper
├── requirements.txt        # Python pip dependencies
├── install.sh              # Pi-side automated setup script
└── deploy.sh               # Laptop-side copy + deploy script
```

**Model files** (not in repo — copy separately):

```
/opt/vv_ingest/models/
├── detect.tflite           # TFLite object detection model
└── labels.txt              # One label per line
```

---

## Quick Start (Automated)

This is the recommended path for fresh installs or re-deploys.

### Prerequisites

- Pi is on the same Wi-Fi as your laptop
- SSH is enabled on the Pi (see [Enable SSH](#1-connect--enable-ssh) below if not)
- You have SSH key access, or know the Pi password

### 1. Find the Pi's IP

On the Pi (or via your router's device list):

```bash
hostname -I
```

### 2. Deploy from your laptop

From the project folder on your laptop:

```bash
# Copy files and run full install automatically:
bash deploy.sh 192.168.1.143 --install
```

That single command:
- Copies all source files to the Pi
- Creates system directories
- Installs APT and Python dependencies
- Enables I2C
- Installs both systemd services
- Sets up the udev auto-ingest rule
- Configures log rotation

### 3. Add AI model files

```bash
scp detect.tflite labels.txt pi@192.168.1.143:/opt/vv_ingest/models/
```

### 4. Edit config

```bash
ssh pi@192.168.1.143
nano /opt/vv_ingest/config.env
```

See [config.env Reference](#configenv-reference) for all options.

### 5. Start services

```bash
sudo systemctl start vv-ingest-ui vv-ingest-ai
sudo systemctl status vv-ingest-ui vv-ingest-ai
```

---

## Manual Setup

Use these steps if you prefer to set things up by hand or if `install.sh` fails partway through.

### 1. Connect & Enable SSH

Power on the Pi and connect it to Wi-Fi. Find its IP:

```bash
hostname -I
```

If SSH is not enabled:

```bash
sudo raspi-config
# → Interface Options → SSH → Enable
```

### 2. Enable I2C (for OLED)

```bash
sudo raspi-config
# → Interface Options → I2C → Enable
```

Reboot, then confirm the OLED is detected:

```bash
i2cdetect -y 1
# Should show 3c (or 3d) in the grid
```

### 3. Copy Files to Pi

From your laptop:

```bash
cd /path/to/vv-ingest-box
scp *.py *.sh *.env requirements.txt pi@PI_IP:/home/pi/
```

### 4. SSH Into the Pi

```bash
ssh pi@PI_IP
```

### 5. Create System Directories

```bash
sudo mkdir -p /opt/vv_ingest/models
sudo mkdir -p /opt/vv_ingest/ai_frames
sudo mkdir -p /var/lib/vv_ingest
sudo mkdir -p /var/log/vv_ingest
```

### 6. Move Files Into System Folder

```bash
sudo mv ~/ui.py ~/ingest.sh ~/config.env \
        ~/tagger_daemon.py ~/enqueue_for_tagging.py \
        ~/db.py ~/detector_tflite.py \
        ~/requirements.txt \
        /opt/vv_ingest/

sudo chown -R pi:pi /opt/vv_ingest /var/lib/vv_ingest /var/log/vv_ingest
```

### 7. Make Scripts Executable

```bash
chmod +x /opt/vv_ingest/ingest.sh \
          /opt/vv_ingest/ui.py \
          /opt/vv_ingest/tagger_daemon.py \
          /opt/vv_ingest/enqueue_for_tagging.py
```

### 8. Install System Dependencies

```bash
sudo apt update
sudo apt install -y \
    python3-pip python3-venv ffmpeg sqlite3 \
    rsync udisks2 smartmontools i2c-tools
```

### 9. Create Python Environment

```bash
python3 -m venv /opt/vv_ingest/venv
source /opt/vv_ingest/venv/bin/activate
pip install --upgrade pip
pip install -r /opt/vv_ingest/requirements.txt
deactivate
```

### 10. Install AI Model Files

```bash
# Copy from wherever you have them
scp detect.tflite labels.txt pi@PI_IP:/opt/vv_ingest/models/
```

Required files:

- `detect.tflite` — TFLite object detection model
- `labels.txt` — one label per line, matching model output indices

<!-- TODO: Add link or instructions for obtaining/training the model -->

### 11. Configure Backup Drive

Plug in the HDD and check its mount point:

```bash
lsblk -f
```

Update `DEST_BASE` in config:

```bash
nano /opt/vv_ingest/config.env
```

Example:

```
DEST_BASE="/media/pi/VVDRIVE/GoPro_Backups"
```

### 12. Install systemd Services

```bash
sudo nano /etc/systemd/system/vv-ingest-ui.service
```

Paste:

```ini
[Unit]
Description=VV Ingest OLED UI
After=network.target

[Service]
User=pi
WorkingDirectory=/opt/vv_ingest
ExecStart=/opt/vv_ingest/venv/bin/python /opt/vv_ingest/ui.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo nano /etc/systemd/system/vv-ingest-ai.service
```

Paste:

```ini
[Unit]
Description=VV Ingest AI Tagger
After=network.target

[Service]
User=pi
WorkingDirectory=/opt/vv_ingest
ExecStart=/opt/vv_ingest/venv/bin/python /opt/vv_ingest/tagger_daemon.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable vv-ingest-ui vv-ingest-ai
sudo systemctl start vv-ingest-ui vv-ingest-ai
```

### 13. Install udev Auto-Ingest Rule

```bash
sudo nano /etc/udev/rules.d/99-vv-ingest.rules
```

Paste:

```
ACTION=="add", SUBSYSTEM=="block", ENV{ID_FS_USAGE}=="filesystem", RUN+="/opt/vv_ingest/ingest.sh"
```

Reload:

```bash
sudo udevadm control --reload-rules && sudo udevadm trigger
```

### 14. Configure Log Rotation

```bash
sudo nano /etc/logrotate.d/vv_ingest
```

Paste:

```
/var/log/vv_ingest/*.log {
    daily
    rotate 14
    compress
    missingok
    notifempty
    copytruncate
}
```

---

## config.env Reference

Located at `/opt/vv_ingest/config.env`.

| Variable | Default | Description |
|---|---|---|
| `BOX_NAME` | `VV INGEST v1.0` | Display name shown on OLED header |
| `DEST_BASE` | `mnt/vvdrive/GoPro_Backups` | Absolute path on the backup drive where footage folders are created |
| `SOURCE_PARENT` | `/media/pi` | Parent directory where the Pi auto-mounts USB devices |
| `OLED_ADDR` | `0x3C` | I2C address of the SSD1306 display (use `i2cdetect -y 1` to confirm) |
| `BUTTON_GPIO` | `17` | BCM GPIO pin number for the screen-cycle button |
| `LOW_SPACE_GB` | `50` | Free space threshold (GB) — OLED shows a warning below this |

Example fully configured file:

```bash
BOX_NAME="VV INGEST v1.0"
DEST_BASE="/media/pi/VVDRIVE/GoPro_Backups"
SOURCE_PARENT="/media/pi"
OLED_ADDR="0x3C"
BUTTON_GPIO="17"
LOW_SPACE_GB="50"
```

---

## OLED Screens

Press the button to cycle through screens:

| # | Screen | Shows |
|---|---|---|
| 0 | **Status** | Current mode, last message, progress, low-space warning |
| 1 | **Storage** | Free / Used / Total GB on the backup drive |
| 2 | **Drive Health** | SMART status for `/dev/sda` (may show "Unavailable" on some USB enclosures) |
| 3 | **Network** | Hostname and IP address |
| 4 | **Recent Logs** | Last 3 lines of `ingest.log` |
| 5 | **AI Tagging** | Tagger mode, queue depth, currently processing file |

---

## AI Tagging

The tagger daemon processes ingested media during configured time windows:

- **08:00 – 15:00** (daytime window)
- **22:00 – 05:00** (overnight window)

Outside these windows the service pauses automatically and shows `AI paused (outside hours)` on the OLED.

**What it does per file:**

1. Computes a fast dedup signature (size + mtime + first/last 1 MB hash) — skips if already seen
2. For **videos**: extracts frames at 0.2 fps via ffmpeg (up to 300 frames), runs TFLite detection on each
3. For **images**: runs TFLite detection directly
4. Aggregates detections across frames, scores a `water_activity` heuristic
5. Writes a `.tags.json` sidecar file alongside the original
6. Stores the result in `/var/lib/vv_ingest/tagger.db` (SQLite)

**To change the processing windows**, edit the `WINDOWS` list near the top of `tagger_daemon.py`.

---

## Updating the System

After code changes, deploy from your laptop:

```bash
# Copies updated files and restarts services — does NOT re-run full install
bash deploy.sh 192.168.1.143 --restart
```

To update config only:

```bash
ssh pi@192.168.1.143
nano /opt/vv_ingest/config.env
sudo systemctl restart vv-ingest-ui vv-ingest-ai
```

---

## Logs & Monitoring

### Live ingest log

```bash
tail -f /var/log/vv_ingest/ingest.log
```

### Service logs

```bash
journalctl -u vv-ingest-ui -f
journalctl -u vv-ingest-ai -f
```

### Service status

```bash
sudo systemctl status vv-ingest-ui
sudo systemctl status vv-ingest-ai
```

### AI queue depth

```bash
wc -l /var/lib/vv_ingest/ai_queue.txt
```

### Tagged files database

```bash
sqlite3 /var/lib/vv_ingest/tagger.db "SELECT path, processed_at FROM media ORDER BY processed_at DESC LIMIT 20;"
```

---

## Troubleshooting

### OLED not displaying anything

1. Confirm I2C is enabled: `sudo raspi-config → Interface Options → I2C`
2. Scan for the device: `i2cdetect -y 1` — should show `3c` or `3d`
3. If address differs from `0x3C`, update `OLED_ADDR` in `config.env`
4. Check service logs: `journalctl -u vv-ingest-ui -n 50`

### Camera/drive plugged in but no ingest starts

1. Check the udev rule exists: `cat /etc/udev/rules.d/99-vv-ingest.rules`
2. Manually trigger to test: `sudo bash /opt/vv_ingest/ingest.sh`
3. Check the ingest log: `tail -50 /var/log/vv_ingest/ingest.log`
4. Confirm `SOURCE_PARENT` in `config.env` matches where the Pi mounts the device (`lsblk -f`)

### AI service not processing files

1. Check it's within the processing windows (08:00–15:00 or 22:00–05:00)
2. Confirm model files exist: `ls /opt/vv_ingest/models/`
3. Check queue has items: `cat /var/lib/vv_ingest/ai_queue.txt`
4. Check logs: `journalctl -u vv-ingest-ai -n 50`

### "SMART: Unavailable" on drive health screen

Normal for many USB enclosures — they don't pass SMART commands through. The drive is likely fine; this is a display limitation.

### Services fail to start after reboot

1. Check for Python errors: `journalctl -u vv-ingest-ui -n 30`
2. Verify the venv is intact: `/opt/vv_ingest/venv/bin/python --version`
3. Re-run install if needed: `sudo bash ~/install.sh`

### Backup drive not mounted / "Not mounted" on storage screen

1. `lsblk -f` to see what's connected
2. Update `DEST_BASE` in `config.env` to match the actual mount path
3. Restart UI service: `sudo systemctl restart vv-ingest-ui`

### Low disk space warning on OLED

The backup drive has less than `LOW_SPACE_GB` (default 50 GB) free. Either:
- Delete old footage from the drive
- Increase the threshold in `config.env` if the warning is a false alarm

---

> For persistent issues, contact Harrison.
