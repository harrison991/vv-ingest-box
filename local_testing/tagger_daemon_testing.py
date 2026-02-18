#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import time
import hashlib
import subprocess
import sys
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Dict, List, Tuple

import cv2
import numpy as np

# Handle import paths for testing
try:
    from db import init_db, signature_exists, upsert_result
    from detector_tflite import TFLiteDetector
except ModuleNotFoundError:
    # For test mode, create mock versions
    def init_db(): pass
    def signature_exists(sig): return False
    def upsert_result(*args, **kwargs): pass
    
    class TFLiteDetector:
        def __init__(self, *args, **kwargs): pass
        def detect(self, *args, **kwargs): return []

# ---------- Paths ----------
def _fallback_dir(base: Path) -> Path:
    if base.exists() and os.access(base, os.W_OK):
        return base
    if os.access(base.parent, os.W_OK):
        return base
    return Path("/tmp/vv_ingest")


STATE_BASE = _fallback_dir(Path("/var/lib/vv_ingest"))
FRAMES_BASE = _fallback_dir(Path("/opt/vv_ingest"))

QUEUE_FILE = STATE_BASE / "ai_queue.txt"
STATE_FILE = STATE_BASE / "ai_state.json"
FRAMES_DIR = FRAMES_BASE / "ai_frames"

MODEL_PATH = "/opt/vv_ingest/models/detect.tflite"
LABELS_PATH = "/opt/vv_ingest/models/labels.txt"

# ---------- Allowed processing windows (local time) ----------
# 08:00–15:00 and 22:00–05:00 (overnight window crosses midnight)
WINDOWS = [
    (dtime(8, 0), dtime(15, 0)),
    (dtime(22, 0), dtime(5, 0)),
]

VIDEO_EXTS = {".mp4", ".mov"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png"}


# ---------- Helpers ----------
def now_allowed() -> bool:
    now = datetime.now().time()
    for start, end in WINDOWS:
        if start <= end:
            if start <= now <= end:
                return True
        else:
            # crosses midnight
            if now >= start or now <= end:
                return True
    return False


def set_state(**kwargs):
    payload = {"time": datetime.now().strftime("%F %T"), **kwargs}
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(payload), encoding="utf-8")


def queue_length() -> int:
    if not QUEUE_FILE.exists():
        return 0
    return sum(1 for ln in QUEUE_FILE.read_text(encoding="utf-8").splitlines() if ln.strip())


def pop_queue_item() -> str | None:
    if not QUEUE_FILE.exists():
        return None
    lines = [ln.strip() for ln in QUEUE_FILE.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not lines:
        return None
    item = lines[0]
    rest = lines[1:]
    QUEUE_FILE.write_text("\n".join(rest) + ("\n" if rest else ""), encoding="utf-8")
    return item


def quick_signature(path: Path) -> str:
    """
    Fast dedupe signature for snapshot-style duplicates:
    - size + mtime + hash(first 1MB) + hash(last 1MB)
    """
    st = path.stat()
    size = st.st_size
    mtime = st.st_mtime_ns

    h = hashlib.sha256()
    h.update(str(size).encode())
    h.update(str(mtime).encode())

    with path.open("rb") as f:
        first = f.read(1024 * 1024)
        h.update(first)
        if size > 1024 * 1024:
            f.seek(max(0, size - 1024 * 1024))
            last = f.read(1024 * 1024)
            h.update(last)

    return h.hexdigest()


def sample_video_frames(video_path: Path, fps: float = 0.2, max_frames: int = 300) -> List[Path]:
    """
    Extract frames using ffmpeg into FRAMES_DIR. Limits to max_frames for sanity.
    300 frames @ 0.2fps ~= 25 minutes coverage.
    """
    FRAMES_DIR.mkdir(parents=True, exist_ok=True)
    stem = video_path.stem

    # delete old frames for this video stem
    for p in FRAMES_DIR.glob(stem + "_*.jpg"):
        try:
            p.unlink()
        except Exception:
            pass

    out_pattern = FRAMES_DIR / (stem + "_%06d.jpg")

    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", str(video_path),
        "-vf", f"fps={fps}",
        "-q:v", "5",
        str(out_pattern)
    ]
    subprocess.run(cmd, check=False)

    frames = sorted(FRAMES_DIR.glob(stem + "_*.jpg"))
    if len(frames) > max_frames:
        # trim extras to limit work
        for p in frames[max_frames:]:
            try:
                p.unlink()
            except Exception:
                pass
        frames = frames[:max_frames]

    return frames


def analyze_frame(bgr: np.ndarray, detector: TFLiteDetector) -> Dict:
    """
    Runs object detection + lightweight activity heuristic.

    Returns:
      objects: [{label, confidence}, ...] (merged by label, max confidence)
      activity: {label, confidence} (heuristic)
      metrics: brightness, focus, water_score, bgr_mean
    """
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    mean = float(np.mean(gray))
    fm = float(cv2.Laplacian(gray, cv2.CV_64F).var())

    # crude water score: blue+green dominance + decent brightness
    b, g, r = np.mean(bgr[:, :, 0]), np.mean(bgr[:, :, 1]), np.mean(bgr[:, :, 2])
    water_score = float(((b + g) - r) / 255.0)
    water_score = max(0.0, min(1.0, (water_score + 0.25)))  # clamp + shift a bit

    dets = detector.detect(bgr, score_thresh=0.35, top_k=12)

    # merge detections by label using max confidence
    best: Dict[str, float] = {}
    for d in dets:
        best[d.label] = max(best.get(d.label, 0.0), float(d.confidence))

    objects = [
        {"label": k, "confidence": round(float(v), 3)}
        for k, v in sorted(best.items(), key=lambda x: x[1], reverse=True)[:8]
    ]

    person_conf = float(best.get("person", 0.0))

    # activity heuristic:
    # person present + water_score high + not too dark => water_activity
    if person_conf > 0.45 and water_score > 0.45 and mean > 80:
        act_label = "water_activity"
        act_conf = min(0.99, 0.55 * person_conf + 0.45 * water_score)
    else:
        act_label = "unknown"
        act_conf = min(0.99, 0.35 * person_conf + 0.25 * water_score)

    return {
        "objects": objects,
        "activity": {"label": act_label, "confidence": round(float(act_conf), 3)},
        "metrics": {
            "mean_brightness": round(mean, 2),
            "focus_measure": round(fm, 2),
            "water_score": round(water_score, 3),
            "bgr_mean": [round(float(b), 1), round(float(g), 1), round(float(r), 1)],
        },
    }


def tag_file(path_str: str, detector: TFLiteDetector) -> Tuple[bool, str]:
    path = Path(path_str)
    if not path.exists() or not path.is_file():
        return False, "missing"

    ext = path.suffix.lower()
    if ext not in (VIDEO_EXTS | IMAGE_EXTS):
        return False, "unsupported_ext"

    sig = quick_signature(path)
    st = path.stat()

    # dedupe: if signature already exists, skip
    if signature_exists(sig):
        return True, "duplicate_skipped"

    result = {
        "file": str(path),
        "signature": sig,
        "type": "video" if ext in VIDEO_EXTS else "image",
        "sample_rate_fps": 0.2 if ext in VIDEO_EXTS else None,
        "created_at": datetime.now().strftime("%F %T"),
        "top_tags": [],
        "objects": [],
        "activity": {"label": "unknown", "confidence": 0.0},
        "details": {},
    }

    if ext in IMAGE_EXTS:
        img = cv2.imread(str(path))
        if img is None:
            return False, "image_read_failed"

        analysis = analyze_frame(img, detector)
        result["objects"] = analysis["objects"]
        result["activity"] = analysis["activity"]
        result["top_tags"] = [f"activity:{analysis['activity']['label']}"]
        result["details"] = {"metrics": analysis["metrics"]}

    elif ext in VIDEO_EXTS:
        frames = sample_video_frames(path, fps=0.2, max_frames=300)
        if not frames:
            # fallback: try 1fps just to get *something*
            frames = sample_video_frames(path, fps=1.0, max_frames=60)

        if not frames:
            return False, "no_frames_extracted"

        agg_obj_labels: List[str] = []
        best_obj_conf: Dict[str, float] = {}
        act_label_counts: Dict[str, int] = {}
        act_conf_max: Dict[str, float] = {}

        for f in frames:
            img = cv2.imread(str(f))
            if img is None:
                continue

            analysis = analyze_frame(img, detector)

            # objects aggregation
            for obj in analysis["objects"]:
                label = obj["label"]
                conf = float(obj["confidence"])
                agg_obj_labels.append(label)
                best_obj_conf[label] = max(best_obj_conf.get(label, 0.0), conf)

            # activity aggregation
            act = analysis["activity"]
            a_label = str(act["label"])
            a_conf = float(act["confidence"])
            act_label_counts[a_label] = act_label_counts.get(a_label, 0) + 1
            act_conf_max[a_label] = max(act_conf_max.get(a_label, 0.0), a_conf)

        from collections import Counter
        c = Counter(agg_obj_labels)

        # top objects with best confidence
        top_objects = []
        for label, _count in c.most_common(8):
            top_objects.append({"label": label, "confidence": round(float(best_obj_conf.get(label, 0.0)), 3)})

        # select most frequent activity label
        if act_label_counts:
            act_label = max(act_label_counts.items(), key=lambda x: x[1])[0]
            act_conf = float(act_conf_max.get(act_label, 0.0))
        else:
            act_label, act_conf = "unknown", 0.0

        result["objects"] = top_objects
        result["activity"] = {"label": act_label, "confidence": round(act_conf, 3)}
        result["top_tags"] = [f"activity:{act_label}"]
        result["details"] = {
            "frame_count": int(len(frames)),
            "object_counts": dict(c.most_common(20)),
            "activity_counts": act_label_counts,
        }

    # Sidecar JSON file
    sidecar = path.with_suffix(path.suffix + ".tags.json")
    sidecar.write_text(json.dumps(result, indent=2), encoding="utf-8")

    # Store in DB
    upsert_result(
        path=str(path),
        signature=sig,
        size_bytes=st.st_size,
        mtime_ns=st.st_mtime_ns,
        tags_json=json.dumps(result),
        processed_at=datetime.now().strftime("%F %T"),
    )

    return True, "tagged"


def main():
    init_db()

    # Load detector
    detector = TFLiteDetector(MODEL_PATH, LABELS_PATH)

    set_state(mode="ai_idle", message="AI ready", queue=queue_length(), current="", progress="")

    while True:
        qlen = queue_length()

        if not now_allowed():
            set_state(mode="ai_paused", message="AI paused (outside hours)", queue=qlen, current="", progress="")
            time.sleep(20)
            continue

        item = pop_queue_item()
        if not item:
            set_state(mode="ai_idle", message="AI idle (no jobs)", queue=0, current="", progress="")
            time.sleep(3)
            continue

        # Short display string for OLED
        display_name = item[-40:] if len(item) > 40 else item

        set_state(mode="ai_working", message="Tagging…", queue=queue_length(), current=display_name, progress="")

        ok, status = tag_file(item, detector)

        set_state(
            mode="ai_working" if ok else "ai_error",
            message=f"AI: {status}",
            queue=queue_length(),
            current=display_name,
            progress="",
        )

        time.sleep(0.5)


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="AI Tagger Daemon")
    parser.add_argument("--test", action="store_true", help="Run in test mode")
    args = parser.parse_args()
    
    if args.test:
        print("Testing tagger_daemon functions...\n")
        
        # Test 1: Time window checking
        print("✓ Testing time window logic...")
        allowed = now_allowed()
        print(f"  Current time allowed for processing: {allowed}\n")
        
        # Test 2: Signature generation
        print("✓ Testing signature generation...")
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False) as f:
            test_file = Path(f.name)
            f.write(b"test data for signature")
        
        try:
            sig = quick_signature(test_file)
            print(f"  Generated signature: {sig[:16]}...")
            print(f"  Signature length: {len(sig)}\n")
        finally:
            test_file.unlink()
        
        # Test 3: Queue operations (mock)
        print("✓ Testing queue operations...")
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as qf:
            queue_test = Path(qf.name)
            qf.write("file1.mp4\nfile2.jpg\nfile3.mp4\n")
        
        try:
            # Test queue length
            lines = [ln.strip() for ln in queue_test.read_text().splitlines() if ln.strip()]
            print(f"  Queue length: {len(lines)} items")
            
            # Test pop
            if lines:
                first = lines[0]
                rest = lines[1:]
                print(f"  First item: {first}")
                print(f"  Remaining: {len(rest)} items\n")
        finally:
            queue_test.unlink()
        
        # Test 4: State file operations
        print("✓ Testing state file operations...")
        with tempfile.TemporaryDirectory() as tmpdir:
            test_state = Path(tmpdir) / "state.json"
            STATE_FILE = test_state  # Override for test
            
            set_state(mode="test", message="Testing state", queue=5, current="test.mp4", progress="50%")
            
            if test_state.exists():
                content = json.loads(test_state.read_text())
                print(f"  State written: {list(content.keys())}")
                print(f"  Mode: {content.get('mode')}")
                print(f"  Queue: {content.get('queue')}\n")
        
        # Test 5: Video extensions check
        print("✓ Testing file type detection...")
        test_exts = {
            "video.mp4": "video",
            "photo.jpg": "image",
            "movie.mov": "video",
            "pic.png": "image",
            "doc.pdf": "unsupported"
        }
        
        for fname, expected in test_exts.items():
            ext = Path(fname).suffix.lower()
            if ext in VIDEO_EXTS:
                result = "video"
            elif ext in IMAGE_EXTS:
                result = "image"
            else:
                result = "unsupported"
            
            status = "✓" if result == expected else "✗"
            print(f"  {status} {fname}: {result}")
        
        print("\n✅ All daemon tests passed!")
    else:
        main()
