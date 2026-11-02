"""
Pass 2 Deep Analysis CLI

Runs Pass 2 text extraction ONLY on papers the researcher explicitly selects
with --id. Nothing is auto-selected, ranked or scored.

  - Extracts methodology / findings / limitation passages and candidate gap sentences
  - Generates Markdown cards in cache/markdown/, every one labelled
    "AI draft \u2014 needs researcher judgment"
  - Records the researcher's selection (the only way a paper gets a record)
  - Humans judge; the script only extracts text and checks whether records exist
"""

import os
import sys
import json
import re
import argparse
from datetime import datetime
import pymupdf
import yaml

try:
    from scripts.corpus_common import DEFAULT_CONFIG_PATH, load_config
    from scripts.tile_ids import AI_DRAFT_LABEL
except ImportError:  # run directly as `python scripts/<name>.py`
    from corpus_common import DEFAULT_CONFIG_PATH, load_config
    from tile_ids import AI_DRAFT_LABEL


def sanitize_filename(name):
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
    try:
        doc = pymupdf.open(pdf_path)
        total_pages = len(doc)
        full_text = ""
        for i in range(min(20, total_pages)):
            full_text += f"\n--- Page {i+1} ---\n" + doc[i].get_text("text")

        # Abstract
        m_abs = re.search(r"(?i)abstract[:\s\n]+(.*?)(?=\n\s*(?:1\.?|I\.?|introduction|keywords|\Z))", full_text, re.DOTALL)
        if m_abs:
            sections["abstract"] = re.sub(r"\s+", " ", m_abs.group(1).strip()[:1500])

        # Methodology / Architecture
        m_meth = re.search(r"(?i)(?:methodology|methods|method|system architecture|model architecture|approach)[:\s\n]+(.*?)(?=\n\s*(?:experiments|results|evaluation|discussion|\Z))", full_text, re.DOTALL)
        if m_meth:
            sections["methodology"] = re.sub(r"\s+", " ", m_meth.group(1).strip()[:2000])

        # Results / Findings
        m_res = re.search(r"(?i)(?:results|findings|empirical evaluation|experiments)[:\s\n]+(.*?)(?=\n\s*(?:discussion|related work|limitations|conclusion|\Z))", full_text, re.DOTALL)
        if m_res:
            sections["findings"] = re.sub(r"\s+", " ", m_res.group(1).strip()[:2000])

        # Limitations / Gaps
        m_lim = re.search(r"(?i)(?:limitations|threats to validity|future work|discussion)[:\s\n]+(.*?)(?=\n\s*(?:conclusion|references|acknowledgments|\Z))", full_text, re.DOTALL)
        if m_lim:
            sections["limitations"] = re.sub(r"\s+", " ", m_lim.group(1).strip()[:2000])

        # Conclusion
        m_concl = re.search(r"(?i)(?:conclusion|concluding remarks|summary)[:\s\n]+(.*?)(?=\n\s*(?:references|acknowledgments|\Z))", full_text, re.DOTALL)
        if m_concl:
            sections["conclusion"] = re.sub(r"\s+", " ", m_concl.group(1).strip()[:1500])

        # Scan for theoretical frameworks
        theories = [
            ("Cognitive Load Theory", r"(?i)cognitive load"),
            ("Human-AI Synergy", r"(?i)human-ai synergy|human[- ]in[- ]the[- ]loop|collaborative ai"),
            ("Activity Theory", r"(?i)activity theory"),
            ("Self-Regulated Learning", r"(?i)self-regulated|srl|metacognition"),
            ("Scaffolding Theory", r"(?i)scaffolding|zone of proximal development|zpd")
        ]
        for name, pattern in theories:
            if re.search(pattern, full_text):
                sections["theory_mentions"].append(name)

        # Candidate gaps heuristics
        gap_indicators = [
            "remains an open question", "remains unclear", "little is known",
            "future work should investigate", "lack of empirical evidence",
            "unexplored", "under-explored", "challenge that remains"
        ]
        for ind in gap_indicators:
            matches = re.findall(rf"([^.\n]*?{re.escape(ind)}[^.\n]*?\.)", full_text, re.IGNORECASE)
            for match in matches[:2]:
                clean_gap = re.sub(r"\s+", " ", match.strip())
                if len(clean_gap) > 30 and clean_gap not in sections["candidate_gaps"]:
                    sections["candidate_gaps"].append(clean_gap)

        doc.close()
    except Exception as e:
        sections["error"] = str(e)

    return sections

def run_pass2_analysis(cfg, target_ids, limit=None):
    if not target_ids:
        raise SystemExit("Pass 2 only runs on papers you select: pass one or more --id SRC-XXXX.")
    ledger_dir = cfg.get("ledger_dir", "ledger")
    cache_dir = cfg.get("cache_dir", "cache")
    ledger_path = os.path.join(ledger_dir, "source_ledger.json")
    cache_md_dir = os.path.join(cache_dir, "markdown")
    os.makedirs(cache_md_dir, exist_ok=True)

    if not os.path.exists(ledger_path):
        print(f"Error: Ledger file not found at {ledger_path}")
        sys.exit(1)

    with open(ledger_path, "r", encoding="utf-8") as f:
        ledger = json.load(f)

    sources = ledger.get("sources", [])
    by_id = {x.get("source_id", "").upper(): x for x in sources}
    targets = []
    for tid in target_ids or []:
        rec = by_id.get(tid.upper())
        if rec is None:
            print(f"Warning: {tid} is not in the ledger; skipping.", flush=True)
            continue
        targets.append(rec)
    targets = targets[:limit] if limit else targets

    print("=" * 60, flush=True)
    print("Executing Pass 2 extraction on researcher-selected papers", flush=True)
    print(f"Selected Targets: {len(targets)}", flush=True)
    print("=" * 60, flush=True)

    completed = 0
    for s in targets:
        sid = s.get("source_id")
        pdf_path = s.get("drive_path")
        title = s.get("title", "Untitled")

        print(f"\nProcessing [{sid}] {title[:60]}...", flush=True)
        if not pdf_path or not os.path.exists(pdf_path):
            print(f"  Warning: PDF file not found at {pdf_path}", flush=True)
            continue

        sections = extract_structured_sections(pdf_path)
        
        # Build Pass 2 Card
        slug = sanitize_filename(title)
        card_filename = f"pass2_{sid}_{slug}.md"
        card_path = os.path.join(cache_md_dir, card_filename)

        gaps_list = "\n".join(f"- {g}" for g in sections["candidate_gaps"]) if sections["candidate_gaps"] else "- None explicitly stated in abstract/conclusion."
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
theoretical_frameworks: [{', '.join(f'"{t}"' for t in sections['theory_mentions'])}]
candidate_gaps_count: {len(sections['candidate_gaps'])}
---

# Pass 2 Extraction: {title}

> **{AI_DRAFT_LABEL}.** Text below was extracted automatically from the PDF. It is not a finding, rating or judgment.

**Source ID**: `{sid}`  
**Authors**: {s.get('author', 'Unknown')}  
**Year**: {s.get('year', 'Unknown')}  
**Local PDF**: `{pdf_path}`  
**Theoretical Frameworks (keyword matches, AI draft)**: {theories_list}  

---

## 1. Abstract & Problem Context
{sections['abstract'] or s.get('abstract_excerpt') or 'No abstract extracted.'}

## 2. Core Methodology & Architecture
{sections['methodology'] or 'Methodology integrated across sections. See full document.'}

## 3. Key Findings & Empirical Results
{sections['findings'] or 'Empirical results integrated across sections. See full document.'}

## 4. Candidate Literature Gaps & Future Work ({AI_DRAFT_LABEL})
{gaps_list}

## 5. Limitations & Threats to Validity
{sections['limitations'] or 'Limitations not isolated to a discrete header.'}

## 6. Researcher Notes
_To be completed by the researcher. Whether and how this paper matters is your judgment._
"""

        with open(card_path, "w", encoding="utf-8") as f:
            f.write(card_md)

        # Update record
        s["researcher_selection"] = {
            "selected_by": "researcher",
            "selected_at": datetime.now().isoformat(),
            "pass": 2,
            "via": "run_pass2.py --id",
        }
        s["pass_status"] = "P2 done"
        s["pass2_ai_draft"] = True  # extraction only; researcher has not reviewed it yet
        s["pass2_card_path"] = card_path
        s["candidate_gaps"] = sections["candidate_gaps"]
        s["theoretical_frameworks"] = sections["theory_mentions"]
        s["provenance"] = f"{sid} | {pdf_path} | P2 done"
        s["last_synced_from_drive"] = datetime.now().isoformat()
        completed += 1
        print(f"  -> Generated Pass 2 card at {card_path}", flush=True)

    # Save ledger
    with open(ledger_path, "w", encoding="utf-8") as f:
        json.dump(ledger, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 60, flush=True)
    print(f"Pass 2 Deep Analysis Complete: {completed}/{len(targets)} papers analyzed.", flush=True)
    print("=" * 60, flush=True)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pass 2 Deep Analysis CLI")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="Path to corpus_config.yaml")
    parser.add_argument("--id", nargs="+", required=True, dest="ids", help="Researcher-selected Source ID(s) (e.g. SRC-0002)")
    parser.add_argument("--limit", type=int, default=None, help="Optional cap on number of papers")
    args = parser.parse_args()

    cfg = load_config(args.config)
    run_pass2_analysis(cfg, args.ids, limit=args.limit)
