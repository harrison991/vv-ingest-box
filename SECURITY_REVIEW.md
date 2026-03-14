# Security review findings

## 1) Arbitrary command execution via `source` on config file
`ingest.sh` executes `source /opt/vv_ingest/config.env`. If an attacker can modify that file, they can place arbitrary shell code and get it executed with the privileges of the ingest service.

## 2) Unsafe shared queue file handling (race/symlink risk)
Both enqueue and dequeue logic directly appends/rewrites `/var/lib/vv_ingest/ai_queue.txt` without file locking or symlink checks. A local attacker could exploit races or symlink redirection to corrupt or overwrite unintended files.

## 3) Queue poisoning enables processing arbitrary files
`tagger_daemon.py` trusts queue entries and processes whatever file path is present. There is no enforcement that queued files are inside the ingest destination, so a poisoned queue can make the daemon read unexpected files and write sidecar `.tags.json` files outside intended boundaries.

## 4) Pattern injection in frame cleanup can delete unintended files
Frame cleanup uses `FRAMES_DIR.glob(stem + "_*.jpg")` where `stem` comes from an untrusted filename. Glob metacharacters in the filename can broaden matches and delete other frame files.

## 5) JSON state file injection / denial of service risk
`ingest.sh` writes JSON with string interpolation and no escaping. User-controlled values (e.g., mount name / path derived data) containing quotes/newlines can produce invalid JSON and break state consumers (UI/monitoring).

## 6) Resource-exhaustion risk in recursive enqueue
`enqueue_for_tagging.py` recursively scans entire ingest folders and appends all media paths with no cap/rate limit. Very large or adversarial directory trees can cause high CPU/IO usage and unbounded queue growth.
