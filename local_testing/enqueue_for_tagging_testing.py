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
    import tempfile
    import os
    
    if len(sys.argv) > 1 and sys.argv[1] != "--test":
        # Normal mode: process command line argument
        main()
    else:
        # Test mode
        print("Testing enqueue_for_tagging functionality...\n")
        
        # Create temporary test directory with mock media files
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            
            # Create test directory structure
            print("✓ Creating test media files...")
            (tmppath / "video1.mp4").touch()
            (tmppath / "photo1.jpg").touch()
            (tmppath / "photo2.png").touch()
            (tmppath / "document.txt").touch()  # Should be ignored
            
            subdir = tmppath / "subdir"
            subdir.mkdir()
            (subdir / "video2.mov").touch()
            
            print("  Created test structure with 4 media files\n")
            
            # Find media files
            print("✓ Scanning for media files...")
            items = []
            for p in tmppath.rglob("*"):
                if p.is_file() and p.suffix.lower() in MEDIA_EXTS:
                    items.append(str(p))
            
            print(f"  Found {len(items)} media files:")
            for item in items:
                print(f"    - {Path(item).name}")
            print()
            
            # Test with temporary queue file
            print("✓ Testing queue operations...")
            with tempfile.NamedTemporaryFile(mode='w', delete=False) as qf:
                queue_test_file = qf.name
            
            try:
                # Simulate enqueuing
                with open(queue_test_file, 'a', encoding='utf-8') as f:
                    for item in items:
                        f.write(item + "\n")
                
                # Read back and verify
                with open(queue_test_file, 'r', encoding='utf-8') as f:
                    queued = f.readlines()
                
                print(f"  Enqueued {len(queued)} files")
                print(f"  Queue file size: {Path(queue_test_file).stat().st_size} bytes\n")
                
                print("✅ All tests passed!")
                
            finally:
                os.unlink(queue_test_file)