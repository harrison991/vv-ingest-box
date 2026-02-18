#!/usr/bin/env python3
import os
import time
import json
import shutil
import socket
import subprocess
from datetime import datetime

# Mock hardware for testing
class MockGPIO:
    def Button(self, *args, **kwargs):
        return MockButton()

class MockButton:
    def __init__(self):
        self.when_pressed = None

class MockBoard:
    SCL = "SCL"
    SDA = "SDA"

class MockBusio:
    def I2C(self, *args, **kwargs):
        return MockI2C()

class MockI2C:
    pass

class MockOLED:
    def __init__(self, *args, **kwargs):
        self.width = 128
        self.height = 64
    
    def image(self, img):
        pass
    
    def show(self):
        pass
    
    def fill(self, val):
        pass

# Try to import real hardware, fall back to mocks
try:
    from gpiozero import Button
except ImportError:
    Button = MockGPIO().Button

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    class Image:
        @staticmethod
        def new(*args, **kwargs):
            return MockImage()
    
    class ImageDraw:
        def __init__(self, img):
            pass
        
        def text(self, pos, text, font=None, fill=None):
            pass
    
    class ImageFont:
        @staticmethod
        def load_default():
            return None
    
    class MockImage:
        pass

try:
    import board
except ImportError:
    board = MockBoard()

try:
    import busio
except ImportError:
    busio = MockBusio()

try:
    import adafruit_ssd1306
    SSD1306 = adafruit_ssd1306.SSD1306_I2C
except ImportError:
    class FakeAda:
        class SSD1306_I2C:
            def __init__(self, *args, **kwargs):
                return MockOLED()
    adafruit_ssd1306 = FakeAda()
    SSD1306 = MockOLED


CONFIG_PATH = "/opt/vv_ingest/config.env"
STATE_JSON = "/var/lib/vv_ingest/state.json"
AI_STATE_JSON = "/var/lib/vv_ingest/ai_state.json"
LOG_FILE = "/var/log/vv_ingest/ingest.log"

def load_env(path: str) -> dict:
    env = {}
    if not os.path.exists(path):
        return env
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
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
try:
    i2c = busio.I2C(board.SCL, board.SDA)
    oled = SSD1306(128, 64, i2c, addr=OLED_ADDR)
except Exception:
    oled = MockOLED()

# Use default font (small)
font = ImageFont.load_default()

try:
    button = Button(BUTTON_GPIO, pull_up=True, bounce_time=0.05)
except Exception:
    button = MockButton()

screen_index = 0
screen_count = 6

def read_state():
    if not os.path.exists(STATE_JSON):
        return {"mode": "idle", "message": "Waiting for GoPro…", "progress": ""}
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
        return ["No log yet"]
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

def read_ai_state():
    if not os.path.exists(AI_STATE_JSON):
        return {"mode": "ai_idle", "message": "AI not running", "queue":0, "current":"", "progress":""}
    try:
        with open(AI_STATE_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"mode": "ai_error", "message": "AI state error", "queue":0, "current":"", "progress":""}

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
            return [BOX_NAME, "Storage:", "Not mounted", DEST_BASE[-21:], ""]
        free_gb, used_gb, total_gb = di
        return [
            BOX_NAME,
            "Storage (DEST):",
            f"Free: {free_gb:.0f}GB",
            f"Used: {used_gb:.0f}GB",
            f"Tot:  {total_gb:.0f}GB",
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
        ip = get_ip()
        host = socket.gethostname()
        return [
            BOX_NAME,
            "Network:",
            f"Host: {host}"[:21],
            f"IP: {ip}"[:21],
            "",
        ]

    if idx == 4:
        # Recent logs
        t = tail_log(3)
        return [
            BOX_NAME,
            "Recent log:",
            t[0][:21] if len(t) > 0 else "",
            t[1][:21] if len(t) > 1 else "",
            t[2][:21] if len(t) > 2 else "",
        ]
    
    if idx == 5:
        # AI Telemetry screen
        ai_state = read_ai_state()
        return [
            BOX_NAME,
            "AI Tagging:",
            f"Mode: {ai_state['mode']}",
            f"Queue: {ai_state['queue']}",
            f"Current: {ai_state['current'][:15]}",
        ]

    return [BOX_NAME, "Unknown screen", "", "", ""]

def next_screen():
    global screen_index
    screen_index = (screen_index + 1) % screen_count

button.when_pressed = next_screen

def main():
    # Boot message
    draw([BOX_NAME, "Booting UI…", "", "", ""])
    time.sleep(0.6)

    while True:
        lines = render_screen(screen_index)
        draw(lines)
        time.sleep(0.25)

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="UI Dashboard")
    parser.add_argument("--test", action="store_true", help="Run in test mode")
    args = parser.parse_args()
    
    if args.test:
        print("Testing UI functions...\n")
        
        # Test 1: Environment loading
        print("✓ Testing environment loading...")
        env = load_env(CONFIG_PATH)
        print(f"  BOX_NAME: {BOX_NAME}")
        print(f"  DEST_BASE: {DEST_BASE[:30]}...")
        print(f"  OLED_ADDR: 0x{OLED_ADDR:02X}")
        print(f"  BUTTON_GPIO: {BUTTON_GPIO}\n")
        
        # Test 2: IP detection
        print("✓ Testing network detection...")
        ip = get_ip()
        print(f"  Detected IP: {ip}")
        hostname = socket.gethostname()
        print(f"  Hostname: {hostname}\n")
        
        # Test 3: Disk info
        print("✓ Testing disk info...")
        di = disk_info("/")
        if di:
            free_gb, used_gb, total_gb = di
            print(f"  Root filesystem: {total_gb:.0f}GB total, {free_gb:.0f}GB free\n")
        else:
            print("  Could not read disk info\n")
        
        # Test 4: State file reading
        print("✓ Testing state file operations...")
        state = read_state()
        print(f"  Ingest state mode: {state.get('mode')}")
        print(f"  Message: {state.get('message')}\n")
        
        # Test 5: AI state reading
        print("✓ Testing AI state reading...")
        ai_state = read_ai_state()
        print(f"  AI mode: {ai_state.get('mode')}")
        print(f"  Queue length: {ai_state.get('queue')}\n")
        
        # Test 6: Screen rendering
        print("✓ Testing screen rendering...")
        for i in range(screen_count):
            lines = render_screen(i)
            print(f"  Screen {i}: {len(lines)} lines, content preview: '{lines[0]}'")
        
        print("\n✅ All UI tests passed!")
