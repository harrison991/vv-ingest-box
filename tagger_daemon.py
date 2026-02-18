import json
import os
import time
import hashlib
import subprocess
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Dict, List, Tuple
from detector_tflite import TFLiteDetector

import cv2
import numpy as np

from db import init_db, signature_exists, upsert_result

QUEUE_FILE = Path("/var/lib/vv_ingest/ai_queue.txt")
STATE_FILE = Path("/var/lib/vv_ingest/ai_state.json")
FRAMES_DIR = Path("/opt/vv_ingest/ai_frames")
MODEL_PATH = "/opt/vv_ingest/models/detect.tflite"
LABELS_PATH = "/opt/vv_ingest/models/labels.txt"

# Allowed processing windows (local time);
# 08:00-15:00 and 22:00-05:00 (overnight)
WINDOWS = [
    (dtime(8, 0), dtime(15, 0))
    (dtime(22, 0), dtime(5, 0)) # crosses midnight
]

VIDEO_EXTS = {".mp4", ".mov"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png"}

def now_allowed() -> bool:
    now = datetime.now().time()
    for start, end in WINDOWS:
        if start <= end :
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

def pop_queue_item() -> str | None:
    if not QUEUE_FILE.exists():
        return None
    lines = [ln.strip() for ln in QUEUE_FILE.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not lines:
        return None
    item = lines[0]
    rest = lines[1:]
    QUEUE_FILE.write_text("/n".join(rest) + ("\n" if rest else ""), encoding="utf-8")
    return item

def queue_length() -> int:
    if not QUEUE_FILE.exists():
        return 0
    return sum(1 for ln in QUEUE_FILE.read_text(encoding="utf-8").splitlines() if ln.strip())

def quick_signature(path: Path) -> str:
    """
    Dedupe signature designed for 'drive snapshot' duplicates without hashing entire files:
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
            last = f.read(1024*1024)
            h.update(last)
    return h.hexdigest()

def analyze_frame(bgr: np.ndarray, detector: TFLiteDetector) -> Dict:
    """
    Returns:
      - objects: [{label, confidence}, ...] (top unique labels)
      - activity: {label, confidence}
      - metrics: brightness + focus + water_score
    """
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    mean = float(np.mean(gray))
    fm = float(cv2.Laplacian(gray, cv2.CV_64F).var())

    # crude water score: blue+green dominance + decent brightness
    b, g, r = np.mean(bgr[:, :, 0]), np.mean(bgr[:, :, 1]), np.mean(bgr[:, :, 2])
    water_score = float(((b + g) - r) / 255.0)  # rough
    water_score = max(0.0, min(1.0, (water_score + 0.25)))  # clamp+shift

    dets = detector.detect(bgr, score_thresh=0.35, top_k=12)

    # merge by label with max confidence
    best: Dict[str, float] = {}
    for d in dets:
        best[d.label] = max(best.get(d.label, 0.0), float(d.confidence))

    objects = [{"label": k, "confidence": round(v, 3)} for k, v in sorted(best.items(), key=lambda x: x[1], reverse=True)[:8]]

    labels = set(best.keys())
    person_conf = best.get("person", 0.0)

    # Activity heuristic (Pi-friendly):
    # - if person present AND water_score high -> water_activity
    # - confidence derived from person_conf + water_score
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
            "bgr_mean": [round(float(b), 1), round(float(g), 1), round(float(r), 1)]
        }
    }

def sample_video_frames(video_path: Path, fps: float = 0.2) -> List[Path]:
    FRAMES_DIR.mkdir(parents=True, exist_ok=True)
    out_pattern = FRAMES_DIR / (video_path.stem + "_%06d.jpg")

    # clean previous frames for this video stem
    for p in FRAMES_DIR.glob(video_path.stem + "_*.jpg"):
        try:
            p.unlink
        except:
            pass
    
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", str(video_path),
        "-vf", f"fps={fps}",
        "-q:v", "5",
        str(out_pattern)
    ]
    subprocess.run(cmd, check=False)

    frames = sorted(FRAMES_DIR.glob(video_path.stem + "_*.jpg"))
    return frames

def tag_file(path_str: str) -> Tuple[bool, str]:
    path = Path(path_str)
    if not path.exists() or not path.is_file():
        return False, "missing"
    
    sig = quick_signature(path)
    st = path.stat()

    if signature_exists(sig):
        return True, "duplicate_skipped"
    
    ext = path.suffix.lower()
    result = {
        "file": str(path),
        "signature": sig,
        "type": "video" if ext in VIDEO_EXTS else "image",
        "sample_rate_fps": 0.2 if ext in VIDEO_EXTS else None,
        "created_at": datetime.now().strftime("%F %T"),
        "top_tags": [],
        "details": {},
    }

    if ext in IMAGE_EXTS:
        img = cv2.imread(str(path))
        if img is None:
            return False, "image_read_failed"
        tagged = basic_image_tags(img)
        result("top_tags") = tagged["tags"]
        result["details"] = tagged
    
    elif ext in VIDEO_EXTS:
        frames = smaple_video_frames(path, fps=0.2)
        if not frames:
            # fall back: attempt one fram at 1s
            frames = smaple_video_frames(path, fps=1)

        agg_tags = []
        frame_summaries = []

        # Limit max frames to avoid insane runs on long videos
        max_frames = 300 # 300 frames @ 0.2fps ~= 25 minutes coverage
        frames = frames[:max_frames]

        for i, f in enumerate(frames, start=1):
            img = cv2.imread(str(f))
            if img is None:
                continue
            tagged = basic_image_tags(img)
            agg_tags.extend(tagged("tags"))
            if i <= 8: # store a few sample frame analyses
                frame_summaries.append(tagged)
        
        from collections import Counter
        c = Counter(agg_tags)
        top = [t for t, _ in c.most_common(12)]
        result["top_tags"] = top
        result["details"] = {
            "frame_count": len(frames),
            "top_tag_counts": dict(c.most_common(20)),
            "sample_frames": frame_summaries
        }
    else:
        return False, "unsupproted_ext"
    
    # Sidecar JSON next to thr media file
    sidecar = path.with_suffix(path.suffix + ".tags.json")
    sidecar.write_text(json.dumps(result, indent=2), encoding="utf-8")

    # store in DB
    upsert_result(
        path=str(path),
        signature=sig,
        size_bytes=st.st_size,
        mtime_ns=st.st_mtime_ns,
        tags_json=json.dumps(result),
        processed_at=datetime.now().strftime("%F %T")
    )

    return True, "tagged"

def main():
    detector = TFLiteDetector(MODEL_PATH, LABELS_PATH)
    init_db()
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

        set_state(mode="ai_working", message="Tagging…", queue=queue_length(), current=item[-40:], progress="")

        ok, status = tag_file(item)
        set_state(
            mode="ai_working" if ok else "ai_error",
            message=f"AI: {status}",
            queue=queue_length(),
            current=item[-40:],
            progress=""
        )

        # small breather
        time.sleep(0.5)

if __name__ == "__main__":
    main()