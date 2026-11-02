import os
import sys
import json
import hashlib
import argparse
from datetime import datetime

try:
    from scripts.corpus_common import DEFAULT_CONFIG_PATH, load_config, require_config_value
except ImportError:  # run directly as `python scripts/<name>.py`
    from corpus_common import DEFAULT_CONFIG_PATH, load_config, require_config_value

def compute_sha256(filepath):
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()

def detect_deltas(cfg):
    TARGET_DIR = require_config_value(cfg, "drive_delta_path")
    LEDGER_PATH = os.path.join(cfg.get("ledger_dir", "ledger"), "source_ledger.json")
    if not os.path.exists(LEDGER_PATH):
        print("No existing ledger found. Run full inventory first.")
        return

    with open(LEDGER_PATH, "r", encoding="utf-8") as f:
        ledger = json.load(f)

    existing_by_path = {s["drive_path"]: s for s in ledger.get("sources", [])}
    existing_by_sha = {s["sha256"]: s for s in ledger.get("sources", [])}

    current_files = set()
    new_files = []
    modified_files = []

    for fname in os.listdir(TARGET_DIR):
        if not fname.lower().endswith(".pdf"):
            continue
        fpath = os.path.join(TARGET_DIR, fname)
        current_files.add(fpath)

        if fpath not in existing_by_path:
            # Check if content matches an existing record under another name
            fhash = compute_sha256(fpath)
            if fhash in existing_by_sha:
                print(f"[Alias detected] {fname} matches {existing_by_sha[fhash]['source_id']}")
            else:
                new_files.append((fpath, fhash))
        else:
            rec = existing_by_path[fpath]
            fstat = os.stat(fpath)
            mtime = datetime.fromtimestamp(fstat.st_mtime).isoformat()
            if mtime != rec.get("modified_time"):
                # File modified on Drive
                fhash = compute_sha256(fpath)
                if fhash != rec.get("sha256"):
                    modified_files.append((rec, fpath, fhash, mtime))

    deleted_files = [p for p in existing_by_path if p not in current_files]

    print("=" * 60)
    print("Re-sync Delta Detection")
    print(f"New papers to queue: {len(new_files)}")
    print(f"Modified papers requiring re-pass: {len(modified_files)}")
    print(f"Deleted / removed papers: {len(deleted_files)}")
    print("=" * 60)

    # Queue new files
    if new_files:
        source_counter = len(ledger["sources"]) + 1
        for fpath, fhash in new_files:
            sid = f"SRC-{source_counter:04d}"
            source_counter += 1
            fstat = os.stat(fpath)
            record = {
                "source_id": sid,
                "drive_file_id": f"gdrive://Scholar Explorer/papers/{os.path.basename(fpath)}",
                "drive_path": fpath,
                "original_filename": os.path.basename(fpath),
                "original_source_label": "Drive: Delta Sync",
                "original_path": fpath,
                "size_bytes": fstat.st_size,
                "modified_time": datetime.fromtimestamp(fstat.st_mtime).isoformat(),
                "sha256": fhash,
                "title": os.path.splitext(os.path.basename(fpath))[0],
                "author": "Unknown",
                "year": "Unknown",
                "pass_status": "queued",
                "living_doc_sections": [],
                "provenance": f"{sid} | {fpath} | queued",
                "last_synced_from_drive": datetime.now().isoformat(),
                "aliases": []
            }
            ledger["sources"].append(record)

    # Mark modified files for re-pass
    for rec, fpath, fhash, mtime in modified_files:
        rec["sha256"] = fhash
        rec["modified_time"] = mtime
        rec["pass_status"] = "queued (re-pass required)"
        rec["last_synced_from_drive"] = datetime.now().isoformat()

    # Mark deleted
    for dp in deleted_files:
        existing_by_path[dp]["drive_status"] = "removed_from_drive"  # file existence only, not a judgment

    if new_files or modified_files or deleted_files:
        with open(LEDGER_PATH, "w", encoding="utf-8") as f:
            json.dump(ledger, f, indent=2, ensure_ascii=False)
        print("Updated ledger with delta changes.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Detect new/modified/removed PDFs in the delta folder")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="Path to corpus_config.yaml")
    args = parser.parse_args()
    detect_deltas(load_config(args.config))
