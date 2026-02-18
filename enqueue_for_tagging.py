import sys
from pathlib import Path

QUEUE_FILE = Path("/var/lib/vv_ingest/ai_queue.txt")

MEDIA_EXTS = {".mp4", ".mov", ".jpg", ".jpeg", ".png"}

def main():
    if len(sys.argv) != 2:
        print("Usage: enqueue_for_tagging.py /path/to/new_ingest_folder")
        sys.exit(2)
    
    folder = Path(sys.argv[1]).resolve()
    if not folder.exists() or not folder.is_dir():
        print(f"Not a folder: {folder}")
        sys.exit(1)
    
    items = []
    for p in folder.rglob("*"):
        if p.is_file() and p.suffix.lower() in MEDIA_EXTS:
            items.append(str(p))
    
    if not items:
        print("No media found to enqueue.")
        return
    
    QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with QUEUE_FILE.open("a", encoding="utf-8") as f:
        for item in items:
            f.write(item + "\n")
        
    print(f"Enqueued {len(items)} files.")

if __name__ == "__main__":
    main()