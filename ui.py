import os
import time
import json
import shutil
import socket
import subprocess
from datetime import datetime

from gpiozero import Button
from PIL import Image, ImageDraw, ImageFont

import board
import busio
import adafruit_ssd1306

CONFIG_PATH = "/opt/vv_ingest/config.env"
STATE_JSON = "/var/lib/vv_ingest/state.json"
LOG_FILE = "/var/log/vv_ingest/ingest.log"

def load_env(path: str) -> dict:
    env = {}
    if not os.path.exists(path):
        return env
    with open(path, "r", encoding="utf-8") as f:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
        return env

ENV = load_env(CONFIG_PATH)

BOX_NAME = ENV.get("BOX_NAME", "VV INGEST")
DEST_BASE = ENV.get("DEST_BASE", "/mnt/vvdrive/GoPro_Backups")
OLED_ADDR = int(ENV.get("OLED_ADDR", "0x3C"), 16)
BUTTON_GPIO = int(ENV.get("BUTTON_GPIO", "17"))
LOW_SPACE_GB = int(ENV.get("LOW_SPACE_GB", "50"))

# OLED init
i2c = busio.I2C(board.SCL, board.SDA)
oled = adafruit_ssd1306.SSD1206_I2C(128, 64, i2c, addr=OLED_ADDR)

# Use default font (small)
font = ImageFont.load_default()

button = Button(BUTTON_GPIO, pull_up=True, bounce_time=0.05)

screen_index = 0
screen_count = 5

def read_state():
    if not os.path.exists(SATE_JSON):
        return {"mode": "idle", "message": "Waiting for GoPro...", "progress": ""}
    try:
        with open(STATE_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
        except Exception:
            return {"mode": "error", "message": "State read error", "progress": ""}

def disk_info(path: str):
    try:
        usage = shutil.disk_usage(path)
        free_gb = usage.free / (1024**3)
        total_gb = usage.total / (1024**3)
        used_gb = (usage.total - usage.free) / (1024**3)
        return free_gb, used_gb, total_gb
    except Exception:
        return None

def get_ip():
    # Find IP used for outbound traffic
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("1.1.1.1", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "No IP"

def tail_log(lines=3):
    if not os.path.exists(LOG_FILE):
        return["No log yet"]
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
            data = f.readlines()
        return [x.strip() for x in data[-lines:]] if data else ["Log empty"]
    except Exception:
        return ["Log read error"]

def drive_health():
    # Quick SMART summary (works for USB drives *sometimes* depending on enclosure)
    # We'll try /dev/sda. If it fails, show "Unavailable".
    try:
        out = subprocess.check_output(["bash", "-lc", "sudo smartctl -H /dev/sda 2>/dev/null | tail -n 2"], text=True).strip()
        if not out:
            return "SMART: Unavailable"
        return out[-32:]
    except Exception:
        return "SMART: Unavailable"

def draw(lines):
    oled.fill(0)
    image = Image.new("1", (oled.width, oled.height))
    draw = ImageDraw.Draw(image)

    y = 0
    for line in lines:
        draw.text((0, y), line[:21], font=font, fill=255)
        y += 12
    
    oled.image(image)
    oled.show()

def render_screen(idx):
    state = read_state()
    now = datetime.now().strftime("%H:%M:%S")

    if idx == 0:
        # Main status
        mode = state.get("mode", "idle")
        msg = state.get("message", "")
        prog = state.get("progress", "")
        lines = [
            BOX_NAME,
            f"{now}  [{mode}]",
            msg,
            f"{prog}",
            "",
        ]
        # Low space warning
        di = disk_info(DEST_BASE)
        if di:
            free_gb, _, _ = di
            if free_gb < LOW_SPACE_GB:
                lines[4] = f"LOW SPACE: {free_gb:.0f}GB"
        return lines
    
    if idx == 1:
        # Storage
        di = disk_info(DEST_BASE)
        if not di:
            return [BOX_NAME, "Storage:", "Not mounted", DEST_BASE[-21:]]
        free_gb, used_gb, total_gb = di
        return [
            BOX_NAME,
            "Storage (DEST)",
            f"Free: {free_gb:.0f}GB",
            f"Used: {used_gb:.0f}GB",
            f"Tot: {total_gb:.0f}GB",
        ]
    
    if idx == 2:
        # Health
        h = drive_health()
        return [
            BOX_NAME, 
            "Health:",
            h,
            "",
            "Btn: next screen",
        ]
    
    if idx == 3:
        # Network
        if = get_ip()
        host = socket.gethostname()
        return [
            BOX_NAME,
            "Network:",
            f"Host: {host}"[:21],
            f"IP: {ip}"[:21],
            ""
        ]

    if idx == 4:
        # Recent logs
        t = tail.log(3)
        return [
            BOX_NAME,
            "Recent log:",
            t[0][:21] if len(t) > 0 else "",
            t[1][:21] if len(t) > 1 else "",
            t[2][:21] if len(t) > 2 else "",
        ]
    
    return [BOX_NAME, "Unknown screen", "", "", ""]

def next_screen():
    global screen_index
    screen_index = (screen_index +1) % screen_count

button.when_pressed = next_screen

def main():
    # Boot message
    draw([BOX_NAME, "Booting UI…", "", "", ""])
    time.sleep(0.6)

    while True:
        lines = render_screen(screen_index)
        draw(lines)
        time.sleep(0.25)

if __name__ = "__main__":
    main()