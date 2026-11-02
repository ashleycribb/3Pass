"""
Pass 1 candidate lister (read-only).

Pass 1 is the researcher's own ~15 second skim. This script does NOT score,
rank, include/exclude, or record anything about any paper, and it never writes
to the ledger. It only:

  1. lists papers that have no researcher record yet (candidates to skim), and
  2. optionally prints the raw first-page text extracted from the PDF so the
     researcher can skim faster. The extract is raw source text, not a judgment.

A paper only gets a researcher record when the researcher selects it for
Pass 2/3 (see `run_pass2.py --id`). Papers that are skimmed and skipped are
never recorded. The only thing this tool checks is whether a record exists.
"""

import os
import sys
import json
import re
import argparse

try:
    from scripts.corpus_common import DEFAULT_CONFIG_PATH, load_config
except ImportError:  # run directly as `python scripts/<name>.py`
    from corpus_common import DEFAULT_CONFIG_PATH, load_config


def has_researcher_record(source):
    """True if the researcher has already selected this paper (existence check only)."""
    return bool(source.get("researcher_selection"))


def list_candidates(sources, limit=None):
    """Return sources with no researcher record, in ledger order. Pure function."""
    candidates = [s for s in sources if not has_researcher_record(s)]
    return candidates[:limit] if limit else candidates


def extract_first_page_text(pdf_path, max_chars=800):
    """Raw first-page text for the skim. No interpretation."""
    try:
        import pymupdf
        doc = pymupdf.open(pdf_path)
        text = doc[0].get_text("text") if len(doc) else ""
        doc.close()
        return re.sub(r"\s+", " ", text).strip()[:max_chars]
    except Exception as e:  # noqa: BLE001 - report and continue
        return f"(could not extract text: {e})"


def run_listing(cfg, limit=None, show_text=False, out_path=None):
    ledger_path = os.path.join(cfg.get("ledger_dir", "ledger"), "source_ledger.json")
    if not os.path.exists(ledger_path):
        print(f"Error: Ledger file not found at {ledger_path}")
        sys.exit(1)

    with open(ledger_path, "r", encoding="utf-8") as f:
        sources = json.load(f).get("sources", [])

    candidates = list_candidates(sources, limit)
    lines = [
        f"Pass 1 candidates for the researcher's skim: {len(candidates)} of {len(sources)} "
        f"papers have no researcher record.",
        "Nothing is scored or recorded here. Select papers with: run_pass2.py --id SRC-XXXX",
        "",
    ]
    for s in candidates:
        lines.append(
            f"{s.get('source_id')} | {s.get('title') or 'Untitled'} | "
            f"{s.get('author') or 'Unknown'} | {s.get('year') or 'Unknown'} | "
            f"{s.get('page_count', '?')} pp"
        )
        if show_text and s.get("drive_path") and os.path.exists(s["drive_path"]):
            lines.append(f"    raw first-page text: {extract_first_page_text(s['drive_path'])}")

    output = "\n".join(lines)
    print(output)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(output + "\n")
    return candidates


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pass 1: list candidate papers for the researcher's skim (read-only)")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="Path to corpus_config.yaml")
    parser.add_argument("--limit", type=int, default=None, help="Maximum candidates to list")
    parser.add_argument("--show-text", action="store_true", help="Print raw first-page text for each candidate")
    parser.add_argument("--out", default=None, help="Also write the listing to this file (e.g. under cache/)")
    args = parser.parse_args()

    run_listing(load_config(args.config), limit=args.limit, show_text=args.show_text, out_path=args.out)
