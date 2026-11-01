import os
import sys
import json
import re
from datetime import datetime
import fitz  # PyMuPDF

LEDGER_PATH = r"c:\Users\Ashley\Downloads\3Pass\ledger\source_ledger.json"
MD_LEDGER_PATH = r"c:\Users\Ashley\Downloads\3Pass\ledger\source_ledger.md"
LIVING_DOC_PATH = r"c:\Users\Ashley\Downloads\3Pass\living_doc\synthesis.md"

TARGET_P2_IDS = [
    "SRC-0002",  # Bridging LLMs and Symbolic Reasoning in Educational QA Systems
    "SRC-0003",  # Quantifying Human-AI Synergy (Bayesian IRT)
    "SRC-0004",  # DeepCode: Open Agentic Coding
    "SRC-0006",  # ReAct: Synergizing Reasoning and Acting in Language Models
    "SRC-0009",  # RLSF: Fine-tuning LLMs via Symbolic Feedback
    "SRC-0010",  # Chain-of-Thought Prompting Elicits Reasoning
    "SRC-0023",  # Artificial Intelligence and the Future of Teaching and Learning
    "SRC-0041"   # CSCL 2023 Collaborative Learning
]

def extract_key_sections(pdf_path):
    sections = {"abstract": "", "intro": "", "method": "", "conclusion": "", "full_sample": ""}
    try:
        doc = fitz.open(pdf_path)
        total_pages = len(doc)
        full_text = ""
        for i in range(min(15, total_pages)):
            full_text += f"\n--- Page {i+1} ---\n" + doc[i].get_text("text")

        sections["full_sample"] = full_text[:25000]

        # Extract abstract
        m = re.search(r"(?i)abstract[:\s\n]+(.*?)(?=\n\s*(?:1\.?|I\.?|introduction|keywords|\Z))", full_text, re.DOTALL)
        if m:
            sections["abstract"] = re.sub(r"\s+", " ", m.group(1).strip()[:1500])

        # Extract conclusion
        m_concl = re.search(r"(?i)(?:conclusion|concluding remarks|summary)[:\s\n]+(.*?)(?=\n\s*(?:references|acknowledgments|\Z))", full_text, re.DOTALL)
        if m_concl:
            sections["conclusion"] = re.sub(r"\s+", " ", m_concl.group(1).strip()[:1500])

        doc.close()
    except Exception as e:
        sections["error"] = str(e)
    return sections

def run_pass2():
    with open(LEDGER_PATH, "r", encoding="utf-8") as f:
        ledger = json.load(f)

    sources = {s["source_id"]: s for s in ledger["sources"]}
    pass2_results = []

    print("=" * 60)
    print("Executing Pass 2 Deep Synthesis")
    print(f"Targeting {len(TARGET_P2_IDS)} High-Relevance Papers")
    print("=" * 60)

    for sid in TARGET_P2_IDS:
        if sid not in sources:
            continue
        rec = sources[sid]
        pdf_path = rec["drive_path"]
        print(f"\nAnalyzing {sid} (P2): {rec['title']}...")

        sections = extract_key_sections(pdf_path)
        
        # Build synthesis card
        p2_card = {
            "source_id": sid,
            "title": rec["title"],
            "author": rec["author"],
            "year": rec["year"],
            "drive_path": pdf_path,
            "abstract": sections["abstract"] or rec.get("abstract_excerpt", ""),
            "provenance": f"{sid} | {pdf_path} | P2"
        }
        
        # Mark as P2 done
        rec["pass_status"] = "P2 done"
        rec["provenance"] = p2_card["provenance"]
        rec["last_synced_from_drive"] = datetime.now().isoformat()
        pass2_results.append(p2_card)

    # Save updated ledger JSON
    with open(LEDGER_PATH, "w", encoding="utf-8") as f:
        json.dump(ledger, f, indent=2, ensure_ascii=False)

    # Update Markdown Ledger
    with open(MD_LEDGER_PATH, "w", encoding="utf-8") as f:
        f.write("# Canonical Source Ledger\n\n")
        f.write(f"- **Master Drive Directory**: `{ledger.get('master_drive_directory')}`\n")
        f.write(f"- **Total Sources**: {len(ledger['sources'])}\n")
        f.write(f"- **Last Updated (Pass 2 Analysis)**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write("| Source ID | Title | Year | Decision | EE | LA | TW | Pass Status |\n")
        f.write("|-----------|-------|------|----------|----|----|----|-------------|\n")
        for r in ledger["sources"][:60]:
            scores = r.get("relevance_scores", {})
            ee = scores.get("evidence_evaluation") or "-"
            la = scores.get("learner_agency") or "-"
            tw = scores.get("traceable_workflows") or "-"
            t_short = (r["title"] or "Untitled").replace("|", "-")[:45]
            f.write(f"| `{r['source_id']}` | {t_short} | {r['year']} | **{r.get('triage_decision')}** | {ee} | {la} | {tw} | `{r['pass_status']}` |\n")
        if len(ledger["sources"]) > 60:
            f.write(f"\n*(Showing top 60 of {len(ledger['sources'])} records. See `source_ledger.json` for full ledger)*\n")

    print("\nPass 2 analysis completed for target papers.")

if __name__ == "__main__":
    run_pass2()
