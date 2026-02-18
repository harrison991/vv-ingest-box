# VV Ingest Box — Full Deployment & Setup Guide

**Audience:** James (or anyone deploying the box at VV)
**Network Method:** Same Wi‑Fi as Raspberry Pi
**Includes:** Ingest + OLED UI + Button + UPS + AI Tagging + Database

---

# 📦 Files You Should Have From Harrison

You should have a folder containing:

Core System:

* `ui.py`
* `ingest.sh`
* `config.env`

AI System:

* `tagger_daemon.py`
* `enqueue_for_tagging.py`
* `db.py`
* `detector_tflite.py`

(Plus model files if supplied)

---

# 1️⃣ Connect Raspberry Pi to Wi‑Fi

Power the Pi on and ensure it is connected to the same network as your laptop.

Find the Pi IP address:

```bash
hostname -I
```

Example:

```
192.168.1.143
```

---

# 2️⃣ Enable SSH (if not already)

```bash
sudo raspi-config
```

Navigate:

```
Interface Options → SSH → Enable
```

---

# 3️⃣ Copy Files From Laptop → Pi

On your laptop terminal:

```bash
cd /path/to/files
```

Then copy everything:

```bash
scp *.py *.sh *.env pi@PI_IP:/home/pi/
```

Example:

```bash
scp *.py *.sh *.env pi@192.168.1.143:/home/pi/
```

---

# 4️⃣ SSH Into the Pi

```bash
ssh pi@PI_IP
```

---

# 5️⃣ Create System Directories

```bash
sudo mkdir -p /opt/vv_ingest
sudo mkdir -p /opt/vv_ingest/models
sudo mkdir -p /opt/vv_ingest/ai_frames

sudo mkdir -p /var/lib/vv_ingest
sudo mkdir -p /var/log/vv_ingest
```

---

# 6️⃣ Move Files Into System Folder

```bash
sudo mv ~/ui.py /opt/vv_ingest/
sudo mv ~/ingest.sh /opt/vv_ingest/
sudo mv ~/config.env /opt/vv_ingest/

sudo mv ~/tagger_daemon.py /opt/vv_ingest/
sudo mv ~/enqueue_for_tagging.py /opt/vv_ingest/
sudo mv ~/db.py /opt/vv_ingest/
sudo mv ~/detector_tflite.py /opt/vv_ingest/
```

Set ownership:

```bash
sudo chown -R pi:pi /opt/vv_ingest
sudo chown -R pi:pi /var/lib/vv_ingest
sudo chown -R pi:pi /var/log/vv_ingest
```

---

# 7️⃣ Make Scripts Executable

```bash
chmod +x /opt/vv_ingest/ingest.sh
chmod +x /opt/vv_ingest/ui.py
chmod +x /opt/vv_ingest/tagger_daemon.py
chmod +x /opt/vv_ingest/enqueue_for_tagging.py
```

---

# 8️⃣ Install System Dependencies

```bash
sudo apt update
sudo apt install -y \
python3-pip \
python3-venv \
ffmpeg \
sqlite3 \
rsync \
udisks2 \
smartmontools \
i2c-tools
```

---

# 9️⃣ Create Python Environment

```bash
python3 -m venv /opt/vv_ingest/venv
source /opt/vv_ingest/venv/bin/activate

pip install --upgrade pip
pip install \
adafruit-circuitpython-ssd1306 \
pillow \
gpiozero \
opencv-python-headless \
numpy \
tflite-runtime

deactivate
```

---

# 🔟 Install AI Model Files

Copy model + labels into:

```
/opt/vv_ingest/models/
```

Required:

* `detect.tflite`
* `labels.txt`

---

# 1️⃣1️⃣ Configure Backup Drive

Plug in HDD and check mount:

```bash
lsblk -f
```

Update path in:

```bash
nano /opt/vv_ingest/config.env
```

Example:

```
DEST_BASE="/media/pi/VVDRIVE/GoPro_Backups"
```

---

# 1️⃣2️⃣ Create OLED UI Service

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

[Install]
WantedBy=multi-user.target
```

Enable:

```bash
sudo systemctl daemon-reload
sudo systemctl enable vv-ingest-ui
sudo systemctl start vv-ingest-ui
```

---

# 1️⃣3️⃣ Create AI Tagger Service

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

[Install]
WantedBy=multi-user.target
```

Enable:

```bash
sudo systemctl daemon-reload
sudo systemctl enable vv-ingest-ai
sudo systemctl start vv-ingest-ai
```

---

# 1️⃣4️⃣ Create Auto‑Ingest Trigger

```bash
sudo nano /etc/udev/rules.d/99-vv-ingest.rules
```

Paste:

```bash
ACTION=="add", SUBSYSTEM=="block", ENV{ID_FS_USAGE}=="filesystem", RUN+="/opt/vv_ingest/ingest.sh"
```

Reload:

```bash
sudo udevadm control --reload-rules
sudo udevadm trigger
```

---

# 1️⃣5️⃣ Test the System

## Plug in GoPro

Expected OLED flow:

```
Detected → Copying → Complete
```

## AI Tagging Screen

Button → AI Screen shows:

```
AI Tagging
Q: 12
Processing GX01…
```

---

# 1️⃣6️⃣ Check Services

```bash
sudo systemctl status vv-ingest-ui
sudo systemctl status vv-ingest-ai
```

---

# 1️⃣7️⃣ Logs

```bash
tail -f /var/log/vv_ingest/ingest.log
```

---

# ✅ Deployment Complete

System now supports:

* Auto ingest
* OLED telemetry
* Button UI
* UPS protection
* AI tagging
* Duplicate detection database
* Time‑window processing

---

**If anything breaks → Contact Harrison**
