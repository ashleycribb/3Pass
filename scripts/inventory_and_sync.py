import os
import sys
import shutil
import hashlib
import json
import re
import argparse
from datetime import datetime
import pymupdf  # PyMuPDF
import yaml

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

def extract_pdf_metadata(filepath):
    info = {
        "title": None,
        "author": None,
        "year": None,
        "page_count": 0,
        "first_page_text": ""
    }
    try:
        doc = pymupdf.open(filepath)
        info["page_count"] = len(doc)
        meta = doc.metadata or {}
        raw_title = meta.get("title", "").strip()
        raw_author = meta.get("author", "").strip()
        
        if raw_title and len(raw_title) > 3 and not raw_title.lower().endswith(".pdf") and not raw_title.lower().startswith("untitled"):
            info["title"] = raw_title
        if raw_author and len(raw_author) > 2:
            info["author"] = raw_author
            
        if len(doc) > 0:
            first_page = doc[0].get_text("text")
            info["first_page_text"] = first_page[:2000]
            
            if not info["title"]:
                lines = [line.strip() for line in first_page.split("\n") if line.strip()]
                candidate_lines = []
                for line in lines[:8]:
                    if len(line) < 4:
                        continue
                    if any(skip in line.lower() for skip in ["arxiv:", "doi:", "issn", "vol.", "journal", "http", "published"]):
                        continue
                    candidate_lines.append(line)
                    if len(" ".join(candidate_lines)) > 30:
                        break
                if candidate_lines:
                    info["title"] = " ".join(candidate_lines)
            
            years = re.findall(r"\b(19[89]\d|20[0-2]\d)\b", first_page[:1500])
            if years:
                info["year"] = years[0]
        doc.close()
    except Exception as e:
        info["error"] = str(e)
    return info

def reconcile_ledger(cfg):
    target_dir = require_config_value(cfg, "drive_local_path")
    ledger_dir = cfg.get("ledger_dir", "ledger")
    ledger_json_path = os.path.join(ledger_dir, "source_ledger.json")
    
    print("=" * 60, flush=True)
    print("Starting Corpus Reconciliation Mode", flush=True)
    print(f"Target Directory: {target_dir}", flush=True)
    print(f"Ledger Path:      {ledger_json_path}", flush=True)
    print("=" * 60, flush=True)

    if not os.path.exists(ledger_json_path):
        print(f"Error: Ledger file not found at {ledger_json_path}", flush=True)
        sys.exit(1)

    with open(ledger_json_path, "r", encoding="utf-8") as f:
        ledger_data = json.load(f)

    sources = ledger_data.get("sources", [])
    sha_map = {s["sha256"]: s for s in sources}
    by_name_size = {(s.get("original_filename"), s.get("size_bytes")): s for s in sources}
    print(f"Loaded {len(sources)} existing records from ledger.", flush=True)

    if not os.path.exists(target_dir):
        print(f"Error: Target directory does not exist: {target_dir}", flush=True)
        sys.exit(1)

    files = [f for f in os.listdir(target_dir) if f.lower().endswith(".pdf")]
    print(f"Found {len(files)} PDFs in target directory.", flush=True)

    matched_count = 0
    new_count = 0
    aliases_added = 0
    max_id_num = 0

    for s in sources:
        sid = s.get("source_id", "SRC-0000")
        match = re.search(r"SRC-(\d+)", sid)
        if match:
            num = int(match.group(1))
            if num > max_id_num:
                max_id_num = num

    for i, fname in enumerate(files, 1):
        fpath = os.path.join(target_dir, fname)
        if not os.path.isfile(fpath):
            continue
        try:
            fstat = os.stat(fpath)
            fsize = fstat.st_size
            mtime = datetime.fromtimestamp(fstat.st_mtime).isoformat()
        except Exception as e:
            print(f"Error reading {fname}: {e}", flush=True)
            continue

        # Fast path: check by original_filename + size_bytes
        if (fname, fsize) in by_name_size:
            rec = by_name_size[(fname, fsize)]
            rec["drive_path"] = fpath
            rec["drive_file_id"] = f"gdrive://Portfolio Hub/Research Corpus/{fname}"
            rec["provenance"] = f"{rec['source_id']} | {fpath} | {rec.get('pass_status', 'queued')}"
            rec["last_synced_from_drive"] = datetime.now().isoformat()
            matched_count += 1
            continue

        # Fallback path: compute SHA-256 for non-exact name/size matches
        try:
            fhash = compute_sha256(fpath)
        except Exception as e:
            print(f"Error hashing {fname}: {e}", flush=True)
            continue

        if fhash in sha_map:
            rec = sha_map[fhash]
            old_path = rec.get("drive_path", "")
            rec["drive_path"] = fpath
            rec["drive_file_id"] = f"gdrive://Portfolio Hub/Research Corpus/{fname}"
            rec["provenance"] = f"{rec['source_id']} | {fpath} | {rec.get('pass_status', 'queued')}"
            rec["last_synced_from_drive"] = datetime.now().isoformat()
            
            if old_path and os.path.abspath(old_path) != os.path.abspath(fpath):
                alias_entry = {
                    "source_id": rec["source_id"],
                    "alias_filename": os.path.basename(old_path),
                    "alias_source_dir": "Drive: Previous Location",
                    "alias_path": old_path,
                    "size_bytes": rec.get("size_bytes", fsize),
                    "modified_time": rec.get("modified_time", mtime)
                }
                if "aliases" not in rec:
                    rec["aliases"] = []
                if not any(a.get("alias_path") == old_path for a in rec["aliases"]):
                    rec["aliases"].append(alias_entry)
                    aliases_added += 1
            matched_count += 1
        else:
            # Genuine new source
            max_id_num += 1
            new_sid = f"SRC-{max_id_num:04d}"
            meta = extract_pdf_metadata(fpath)
            title = meta.get("title") or os.path.splitext(fname)[0]

            new_rec = {
                "source_id": new_sid,
                "drive_file_id": f"gdrive://Portfolio Hub/Research Corpus/{fname}",
                "drive_path": fpath,
                "original_filename": fname,
                "original_source_label": "Drive: Portfolio Hub Research Corpus",
                "original_path": fpath,
                "size_bytes": fsize,
                "modified_time": mtime,
                "sha256": fhash,
                "page_count": meta.get("page_count", 0),
                "title": title,
                "author": meta.get("author", "Unknown"),
                "year": meta.get("year", "Unknown"),
                "pass_status": "queued",
                "living_doc_sections": [],
                "provenance": f"{new_sid} | {fpath} | queued",
                "last_synced_from_drive": datetime.now().isoformat(),
                "aliases": []
            }
            sources.append(new_rec)
            sha_map[fhash] = new_rec
            new_count += 1

        if i % 100 == 0 or i == len(files):
            print(f"  Processed {i}/{len(files)} target files...", flush=True)

    total_aliases = sum(len(r.get("aliases", [])) for r in sources)
    print("\n" + "=" * 60, flush=True)
    print("Reconciliation Summary:", flush=True)
    print(f"  Matched & Updated:    {matched_count}", flush=True)
    print(f"  New Sources Added:    {new_count}", flush=True)
    print(f"  Total Sources in DB:  {len(sources)}", flush=True)
    print(f"  Total Aliases:        {total_aliases}", flush=True)
    print("=" * 60, flush=True)

    # Save JSON ledger
    ledger_data["generated_at"] = datetime.now().isoformat()
    ledger_data["master_drive_directory"] = target_dir
    ledger_data["total_sources"] = len(sources)
    ledger_data["total_aliases"] = total_aliases
    ledger_data["sources"] = sources

    with open(ledger_json_path, "w", encoding="utf-8") as f:
        json.dump(ledger_data, f, indent=2, ensure_ascii=False)
    print(f"Updated JSON ledger at {ledger_json_path}", flush=True)

    # Save Aliases tracking
    aliases_path = os.path.join(ledger_dir, "aliases.json")
    all_aliases = {s["source_id"]: s.get("aliases", []) for s in sources if s.get("aliases")}
    with open(aliases_path, "w", encoding="utf-8") as f:
        json.dump(all_aliases, f, indent=2, ensure_ascii=False)

    # Save Markdown Summary
    md_path = os.path.join(ledger_dir, "source_ledger.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# Canonical Source Ledger (Reconciled)\n\n")
        f.write(f"- **Master Drive Directory**: `{target_dir}`\n")
        f.write(f"- **Total Unique Sources**: {len(sources)}\n")
        f.write(f"- **Total Deduplicated Aliases**: {total_aliases}\n")
        f.write(f"- **Last Reconciled**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write("| Source ID | Title | Year | Authors | Pages | Pass Status |\n")
        f.write("|-----------|-------|------|---------|-------|-------------|\n")
        for r in sources[:50]:
            title_clean = (r.get("title") or "Untitled").replace("|", "-").strip()[:50]
            auth_clean = (r.get("author") or "Unknown").replace("|", "-").strip()[:25]
            f.write(f"| `{r['source_id']}` | {title_clean} | {r.get('year')} | {auth_clean} | {r.get('page_count')} | `{r.get('pass_status')}` |\n")
        if len(sources) > 50:
            f.write(f"\n*(Showing top 50 of {len(sources)} records. See `source_ledger.json` for full ledger)*\n")
    print(f"Updated Markdown ledger at {md_path}", flush=True)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Corpus Inventory and Reconciliation")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="Path to corpus_config.yaml")
    parser.add_argument("--reconcile", action="store_true", help="Reconcile existing ledger with drive_local_path")
    args = parser.parse_args()

    config = load_config(args.config)
    if args.reconcile:
        reconcile_ledger(config)
    else:
        print("Run with --reconcile or configure source directories.", flush=True)
