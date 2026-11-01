"""
Pass 1 Triage Wave CLI

Evaluates papers across 3 primary research dimensions on a 1-5 integer scale:
  1. Evidence Evaluation (EE):
     Benchmarks, empirical validation, hallucination detection, guardrails, error analysis.
  2. Learner Agency (LA):
     Student autonomy, self-regulation, pedagogical scaffolding, metacognition, inquiry.
  3. Traceable Workflows (TW):
     Step-by-step reasoning audits, provenance tracking, reproducible pipelines, event telemetry.

Scoring Rubric:
  - 1: Negligible or zero topical alignment (< 2 indicator keywords)
  - 2: Incidental mention (2-3 keywords)
  - 3: Moderate relevance / dedicated sub-section (4-5 keywords)
  - 4: Strong thematic alignment (6-7 keywords)
  - 5: Core disciplinary focus (>= 8 keywords)

Decision Thresholds:
  - 'include': Any dimension >= 3 (or sum >= 8)
  - 'exclude': Non-scholarly forms/syllabi or sum < 4 with zero relevance
  - 'maybe': Peripheral relevance requiring secondary inspection
"""

import os
import sys
import json
import re
import argparse
from datetime import datetime
import pymupdf
import yaml

DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "corpus_config.yaml")

def load_config(config_path=DEFAULT_CONFIG_PATH):
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    return {
        "ledger_dir": "ledger",
        "p1_threshold": 3,
        "batch_size": 50
    }

def extract_paper_text_summary(pdf_path):
    text_sample = ""
    abstract = ""
    try:
        doc = pymupdf.open(pdf_path)
        total_pages = len(doc)
        full_text_p1_3 = ""
        for i in range(min(3, total_pages)):
            full_text_p1_3 += doc[i].get_text("text") + "\n"
        
        abs_match = re.search(r"(?i)abstract[:\s\n]+(.*?)(?=\n\s*(?:1\.?|I\.?|introduction|keywords|index terms|\Z))", full_text_p1_3, re.DOTALL)
        if abs_match:
            abstract = abs_match.group(1).strip()[:1000]
            abstract = re.sub(r"\s+", " ", abstract)
        else:
            abstract = full_text_p1_3[:800].strip()
            abstract = re.sub(r"\s+", " ", abstract)
            
        doc.close()
    except Exception as e:
        return f"Error extracting text: {e}", ""
    return full_text_p1_3, abstract

def evaluate_relevance(text, title, threshold=3):
    text_lower = (text + " " + title).lower()

    # Dimension 1: Evidence Evaluation
    ee_keywords = [
        "evaluation", "benchmark", "validation", "empirical", "accuracy",
        "hallucination", "reliability", "metrics", "rigor", "verification",
        "ground truth", "guardrail", "error analysis", "robustness", "fidelity"
    ]
    ee_matches = sum(1 for kw in ee_keywords if kw in text_lower)
    score_ee = min(5, max(1, (ee_matches // 2) + 1))

    # Dimension 2: Learner Agency
    la_keywords = [
        "learner", "agency", "student", "pedagogy", "autonomy", "self-regulated",
        "scaffolding", "collaborative", "instructional", "education", "classroom",
        "learning", "engagement", "tutoring", "metacognition", "inquiry"
    ]
    la_matches = sum(1 for kw in la_keywords if kw in text_lower)
    score_la = min(5, max(1, (la_matches // 2) + 1))

    # Dimension 3: Traceable Workflows
    tw_keywords = [
        "workflow", "provenance", "traceability", "reproducible", "telemetry",
        "audit", "logging", "event", "pipeline", "lrs", "tracking", "transparent",
        "lineage", "artifact", "metadata", "chain", "reproducibility"
    ]
    tw_matches = sum(1 for kw in tw_keywords if kw in text_lower)
    score_tw = min(5, max(1, (tw_matches // 2) + 1))

    # Triage Decision
    if score_ee >= threshold or score_la >= threshold or score_tw >= threshold or (score_ee + score_la + score_tw) >= 8:
        decision = "include"
    elif any(non in title.lower() for non in ["syllabus", "receipt", "invoice", "form", "untitled", "schedule"]):
        decision = "exclude"
    else:
        decision = "maybe"

    return score_ee, score_la, score_tw, decision

def run_triage(cfg, limit=None, force=False):
    ledger_dir = cfg.get("ledger_dir", "ledger")
    ledger_path = os.path.join(ledger_dir, "source_ledger.json")
    threshold = cfg.get("p1_threshold", 3)

    if not os.path.exists(ledger_path):
        print(f"Error: Ledger file not found at {ledger_path}")
        sys.exit(1)

    with open(ledger_path, "r", encoding="utf-8") as f:
        ledger_data = json.load(f)

    sources = ledger_data.get("sources", [])
    print("=" * 60, flush=True)
    print(f"Starting Pass 1 Triage (Threshold: {threshold})", flush=True)
    print(f"Total Sources in Ledger: {len(sources)}", flush=True)
    print("=" * 60, flush=True)

    target_sources = []
    for s in sources:
        if force or s.get("pass_status") == "queued":
            target_sources.append(s)

    if limit:
        target_sources = target_sources[:limit]

    print(f"Papers selected for Pass 1 triage: {len(target_sources)}", flush=True)

    triaged_count = 0
    for i, s in enumerate(target_sources, 1):
        fpath = s.get("drive_path")
        if not fpath or not os.path.exists(fpath):
            continue

        text_sample, abstract = extract_paper_text_summary(fpath)
        ee, la, tw, decision = evaluate_relevance(text_sample, s.get("title", ""), threshold=threshold)

        s["relevance_scores"] = {
            "evidence_evaluation": ee,
            "learner_agency": la,
            "traceable_workflows": tw
        }
        s["triage_decision"] = decision
        if s.get("pass_status") == "queued":
            s["pass_status"] = "P1 done"
        s["provenance"] = f"{s['source_id']} | {s['drive_path']} | {s['pass_status']}"
        if abstract and not s.get("abstract_excerpt"):
            s["abstract_excerpt"] = abstract

        triaged_count += 1
        if i % 25 == 0 or i == len(target_sources):
            print(f"  Triaged {i}/{len(target_sources)} papers...", flush=True)

    # Save updated ledger
    ledger_data["sources"] = sources
    with open(ledger_path, "w", encoding="utf-8") as f:
        json.dump(ledger_data, f, indent=2, ensure_ascii=False)

    # Save Markdown Summary
    md_ledger_path = os.path.join(ledger_dir, "source_ledger.md")
    with open(md_ledger_path, "w", encoding="utf-8") as f:
        f.write("# Canonical Source Ledger\n\n")
        f.write(f"- **Master Drive Directory**: `{ledger_data.get('master_drive_directory')}`\n")
        f.write(f"- **Total Sources**: {len(sources)}\n")
        p2_count = sum(1 for s in sources if s.get("pass_status") == "P2 done")
        p1_count = sum(1 for s in sources if s.get("pass_status") == "P1 done")
        q_count = sum(1 for s in sources if s.get("pass_status") == "queued")
        inc_count = sum(1 for s in sources if s.get("triage_decision") == "include")
        may_count = sum(1 for s in sources if s.get("triage_decision") == "maybe")
        exc_count = sum(1 for s in sources if s.get("triage_decision") == "exclude")

        f.write(f"- **Status Breakdown**: P2 done: `{p2_count}` | P1 done: `{p1_count}` | Queued: `{q_count}`\n")
        f.write(f"- **Triage Decisions**: Include: `{inc_count}` | Maybe: `{may_count}` | Exclude: `{exc_count}`\n")
        f.write(f"- **Last Updated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write("| Source ID | Title | Year | Decision | EE | LA | TW | Pass Status |\n")
        f.write("|-----------|-------|------|----------|----|----|----|-------------|\n")
        for r in sources[:100]:
            scores = r.get("relevance_scores", {})
            ee = scores.get("evidence_evaluation") or "-"
            la = scores.get("learner_agency") or "-"
            tw = scores.get("traceable_workflows") or "-"
            title_clean = (r.get("title") or "Untitled").replace("|", "-").strip()[:50]
            f.write(f"| `{r['source_id']}` | {title_clean} | {r.get('year')} | `{r.get('triage_decision')}` | {ee} | {la} | {tw} | `{r.get('pass_status')}` |\n")

    print("\n" + "=" * 60, flush=True)
    print(f"Pass 1 Triage Complete. Updated {triaged_count} records.", flush=True)
    print(f"Include: {inc_count} | Maybe: {may_count} | Exclude: {exc_count}", flush=True)
    print("=" * 60, flush=True)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pass 1 Triage Wave")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="Path to corpus_config.yaml")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of papers to triage")
    parser.add_argument("--force", action="store_true", help="Re-triage all papers including completed ones")
    args = parser.parse_args()

    config = load_config(args.config)
    run_triage(config, limit=args.limit, force=args.force)
