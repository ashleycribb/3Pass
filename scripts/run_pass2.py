"""
Pass 2 Bookkeeping & Intake CLI (No Automated Judgments)

Processes researcher-selected papers through the Source Router:
  - Selects the best available path (arXiv HTML, LaTeX, GROBID PDF, or DOI metadata)
  - Extracts structured facts (sections, figures, tables, equations, references)
  - Creates/updates draft SRC pages with empty sections for researcher notes
  - Updates wiki/index.md and appends to log.md
  - Bookkeeping only: zero automated evaluations or summaries.
"""

import os
import sys

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

import json
import argparse
from datetime import datetime
import yaml

try:
    from scripts.corpus_common import DEFAULT_CONFIG_PATH, load_config
    from scripts.tile_ids import AI_DRAFT_LABEL
except ImportError:
    from corpus_common import DEFAULT_CONFIG_PATH, load_config
    from tile_ids import AI_DRAFT_LABEL

from scripts.models import PaperRecord
from scripts.source_router import create_default_router
from scripts.wiki_manager import (
    ensure_wiki_scaffold,
    build_paper_page_from_record,
    update_wiki_index,
    append_to_log
)


def sanitize_filename(name):
    import re
    clean = re.sub(r'[\\/*?:"<>|]', "", name)
    clean = re.sub(r"\s+", "_", clean).strip("_")
    return clean[:60]


def extract_structured_sections(pdf_path):
    sections = {
        "abstract": "",
        "methodology": "",
        "findings": "",
        "limitations": "",
        "conclusion": "",
        "candidate_gaps": [],
        "theory_mentions": []
    }
    if not os.path.exists(pdf_path):
        return sections
    try:
        import pymupdf
        doc = pymupdf.open(pdf_path)
        full_text = ""
        for i in range(min(20, len(doc))):
            full_text += f"\n--- Page {i+1} ---\n" + doc[i].get_text("text")
        sections["abstract"] = full_text[:1500]
    except Exception:
        pass
    return sections


def run_pass2_analysis(cfg, ids, limit=None):
    """Legacy entrypoint for Pass 2 analysis; enforces researcher selection."""
    if not ids:
        raise SystemExit("Pass 2 requires explicit researcher selection with --id.")

    ledger_path = os.path.join(cfg.get("ledger_dir", "ledger"), "source_ledger.json")
    if not os.path.exists(ledger_path):
        raise SystemExit(f"Ledger file not found at {ledger_path}")

    with open(ledger_path, "r", encoding="utf-8") as f:
        ledger = json.load(f)

    sources = ledger.get("sources", [])
    targets = [s for s in sources if s.get("source_id") in ids]
    if limit:
        targets = targets[:limit]

    cache_md_dir = os.path.join(cfg.get("cache_dir", "cache"), "markdown")
    os.makedirs(cache_md_dir, exist_ok=True)

    for s in targets:
        sid = s.get("source_id")
        pdf_path = s.get("drive_path", "")
        title = s.get("title", "Untitled")

        sections = extract_structured_sections(pdf_path)
        slug = sanitize_filename(title)
        card_filename = f"pass2_{sid}_{slug}.md"
        card_path = os.path.join(cache_md_dir, card_filename)

        gaps_list = "\n".join(f"- {g}" for g in sections["candidate_gaps"]) if sections["candidate_gaps"] else "- None explicitly stated."
        theories_list = ", ".join(sections["theory_mentions"]) if sections["theory_mentions"] else "None explicitly cited."

        card_md = f"""---
source_id: {sid}
title: "{title}"
author: "{s.get('author', 'Unknown')}"
year: "{s.get('year', 'Unknown')}"
drive_path: "{pdf_path}"
provenance: "{sid} | {pdf_path} | P2 done"
analyzed_at: "{datetime.now().isoformat()}"
ai_draft: true
ai_draft_label: "{AI_DRAFT_LABEL}"
---

# Pass 2 Extraction: {title}

> **{AI_DRAFT_LABEL}.** Text below was extracted automatically from the PDF.

**Source ID**: `{sid}`  
**Authors**: {s.get('author', 'Unknown')}  
**Year**: {s.get('year', 'Unknown')}  
**Local PDF**: `{pdf_path}`  
**Theoretical Frameworks**: {theories_list}  

---

## 1. Abstract & Problem Context
{sections['abstract'] or s.get('abstract_excerpt') or 'No abstract extracted.'}

## 2. Core Methodology & Architecture
{sections['methodology'] or 'Methodology integrated across sections.'}

## 3. Key Findings & Empirical Results
{sections['findings'] or 'Empirical results integrated across sections.'}

## 4. Candidate Literature Gaps & Future Work ({AI_DRAFT_LABEL})
{gaps_list}

## 5. Limitations & Threats to Validity
{sections['limitations'] or 'Limitations not isolated.'}

## 6. Researcher Notes
_To be completed by the researcher._
"""
        with open(card_path, "w", encoding="utf-8") as f:
            f.write(card_md)

        s["researcher_selection"] = {
            "selected_by": "researcher",
            "selected_at": datetime.now().isoformat(),
            "pass": 2,
            "via": "run_pass2.py --id",
        }
        s["pass_status"] = "P2 done"
        s["pass2_ai_draft"] = True
        s["pass2_card_path"] = card_path
        s["candidate_gaps"] = sections["candidate_gaps"]
        s["theoretical_frameworks"] = sections["theory_mentions"]
        s["provenance"] = f"{sid} | {pdf_path} | P2 done"

    with open(ledger_path, "w", encoding="utf-8") as f:
        json.dump(ledger, f, indent=2, ensure_ascii=False)


def intake_paper(paper_input: dict, router, wiki_dir: str) -> PaperRecord:
    """Routes and extracts a paper record, creating the draft wiki page and logging."""
    record = router.route(paper_input)
    page_path = build_paper_page_from_record(record, wiki_dir)
    
    prov = record.provenance
    path_used = prov.path_used if prov else "unknown"
    tool_name = prov.tool if prov else "unspecified"
    
    msg = f"INTAKE {record.source_id} | path: {path_used} | tool: {tool_name} | completeness: {record.completeness} | title: {record.title[:60]}"
    append_to_log(wiki_dir, msg)
    print(f"  -> {msg}", flush=True)
    return record


def main():
    parser = argparse.ArgumentParser(description="Pass 2 Intake & Bookkeeping CLI")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="Path to corpus_config.yaml")
    parser.add_argument("--id", help="Process specific Source ID (e.g. SRC-0002)")
    parser.add_argument("--doi", help="Process by DOI")
    parser.add_argument("--limit", type=int, default=5, help="Number of papers to process")
    args = parser.parse_args()

    cfg = load_config(args.config)
    ledger_dir = cfg.get("ledger_dir", "ledger")
    wiki_dir = cfg.get("wiki_dir", "wiki")
    ensure_wiki_scaffold(wiki_dir)

    ledger_path = os.path.join(ledger_dir, "source_ledger.json")
    if not os.path.exists(ledger_path):
        print(f"Error: Ledger file not found at {ledger_path}")
        sys.exit(1)

    with open(ledger_path, "r", encoding="utf-8") as fp:
        ledger = json.load(fp)

    sources = ledger.get("sources", [])
    targets = []

    if args.id:
        targets = [s for s in sources if s.get("source_id") == args.id]
        if not targets:
            print(f"Source ID {args.id} not found in ledger.")
            sys.exit(1)
    elif args.doi:
        targets = [s for s in sources if s.get("doi") == args.doi]
        if not targets:
            # If not in ledger, synthesize an input record
            targets = [{
                "source_id": f"SRC-NEW-{abs(hash(args.doi)) % 10000:04d}",
                "title": f"Paper for DOI {args.doi}",
                "doi": args.doi
            }]
    else:
        # Default: take first un-intaken papers up to limit
        targets = [s for s in sources if s.get("pass_status") != "draft"][:args.limit]

    print("=" * 60, flush=True)
    print("3Pass Pass 2 Intake & Bookkeeping (Zero Automated Judgments)", flush=True)
    print(f"Targets to process: {len(targets)}", flush=True)
    print("=" * 60, flush=True)

    router = create_default_router(cache_dir=cfg.get("cache_dir", "cache"))
    records_processed = []

    for s in targets:
        sid = s.get("source_id", "SRC-0000")
        print(f"\nProcessing [{sid}] {s.get('title', '')[:50]}...", flush=True)
        try:
            rec = intake_paper(s, router, wiki_dir)
            records_processed.append(rec)
            # Update ledger entry
            s["completeness"] = rec.completeness
            s["path_used"] = rec.provenance.path_used if rec.provenance else "unknown"
            s["pass_status"] = "draft"
        except Exception as e:
            print(f"  Warning: Intake error on {sid}: {e}", flush=True)

    # Save ledger
    with open(ledger_path, "w", encoding="utf-8") as fp:
        json.dump(ledger, fp, indent=2, ensure_ascii=False)

    print("\nUpdating wiki index...", flush=True)
    update_wiki_index(sources, wiki_dir)

    print("=" * 60, flush=True)
    print(f"Pass 2 Intake complete. Processed {len(records_processed)} papers.", flush=True)
    print("=" * 60, flush=True)


if __name__ == "__main__":
    main()
