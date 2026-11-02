"""
Karpathy LLM Research Wiki Manager & Linter

Manages the doctoral literature review wiki (wiki/):
  - Enforces Karpathy LLM Wiki structure (wiki/index.md, wiki/gaps.md, wiki/theories.md, wiki/papers/)
  - Compiles paper summaries with strict provenance invariants: [Source ID | Drive Path | Pass Level]
  - Validates all internal wikilinks [[Target]] with zero broken references
  - Extracts candidate doctoral research questions and unaddressed gaps
"""

import os
import sys
import re
import json
import argparse
from datetime import datetime
import yaml

try:
    from scripts.corpus_common import DEFAULT_CONFIG_PATH, load_config
    from scripts.tile_ids import AI_DRAFT_LABEL
except ImportError:  # run directly as `python scripts/<name>.py`
    from corpus_common import DEFAULT_CONFIG_PATH, load_config
    from tile_ids import AI_DRAFT_LABEL


def sanitize_slug(text):
    clean = re.sub(r"[^a-zA-Z0-9_-]", "-", text.lower())
    clean = re.sub(r"-+", "-", clean).strip("-")
    return clean

def ensure_wiki_scaffold(wiki_dir):
    os.makedirs(wiki_dir, exist_ok=True)
    os.makedirs(os.path.join(wiki_dir, "papers"), exist_ok=True)
    os.makedirs(os.path.join(wiki_dir, "topics"), exist_ok=True)

def build_paper_page(rec, cache_dir, wiki_dir):
    sid = rec["source_id"]
    title = rec.get("title", "Untitled")
    authors = rec.get("author", "Unknown")
    year = rec.get("year", "Unknown")
    drive_path = rec.get("drive_path", "")
    pass_status = rec.get("pass_status", "P1 done")
    scores = rec.get("researcher_scores") or {}
    theories = rec.get("theoretical_frameworks", [])
    gaps = rec.get("candidate_gaps", [])
    abstract = rec.get("abstract_excerpt", "")

    # Look for Pass 2 deep analysis markdown card if available
    pass2_card_path = rec.get("pass2_card_path")
    deep_analysis_text = ""
    if pass2_card_path and os.path.exists(pass2_card_path):
        with open(pass2_card_path, "r", encoding="utf-8") as f:
            deep_analysis_text = f.read()
            # Strip yaml frontmatter for embedded display
            deep_analysis_text = re.sub(r"^---.*?---\s*", "", deep_analysis_text, flags=re.DOTALL)

    theories_links = ", ".join(f"[[theories#{sanitize_slug(t)}|{t}]]" for t in theories) if theories else "None explicitly indexed"
    gaps_content = "\n".join(f"- {g}" for g in gaps) if gaps else "- No unaddressed literature gaps isolated in primary scan."

    page_content = f"""---
source_id: {sid}
title: "{title}"
author: "{authors}"
year: "{year}"
drive_path: "{drive_path}"
provenance: "{sid} | {drive_path} | {pass_status}"
wiki_category: paper
theories: [{', '.join(f'"{t}"' for t in theories)}]
---

# {title}

**Source Identifier**: `[{sid}]`  
**Authors**: {authors}  
**Year**: {year}  
**Provenance**: `[{sid} | {drive_path} | {pass_status}]`  
**Researcher Scores**: EE: `{scores.get('evidence_evaluation', '-')}` | LA: `{scores.get('learner_agency', '-')}` | TW: `{scores.get('traceable_workflows', '-')}`  
**Theoretical Frameworks**: {theories_links}  

---

## Abstract & Overview
{abstract or 'Abstract not available.'}

## Literature Review Relevance
- **[[topics/evidence-evaluation|Evidence Evaluation]]**: Robustness, empirical validation benchmarks, hallucination guards.
- **[[topics/learner-agency|Learner Agency]]**: Pedagogical scaffolding, student autonomy, cognitive load balancing.
- **[[topics/traceable-workflows|Traceable Workflows]]**: Reproducibility, audit trails, intermediate reasoning verification.

## Candidate Literature Gaps & Open Questions ({AI_DRAFT_LABEL})
{gaps_content}

---

## Pass 2 Extraction ({AI_DRAFT_LABEL})
{deep_analysis_text or 'Pass 2 deep analysis pending for this source.'}

---
*Return to [[index|Corpus Navigation Portal]] | View [[gaps|Literature Gap Register]] | View [[theories|Theoretical Frameworks]]*
"""

    out_file = os.path.join(wiki_dir, "papers", f"{sid}.md")
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(page_content)
    return out_file

def sync_all_wiki(cfg):
    ledger_dir = cfg.get("ledger_dir", "ledger")
    wiki_dir = cfg.get("wiki_dir", "wiki")
    cache_dir = cfg.get("cache_dir", "cache")
    ledger_path = os.path.join(ledger_dir, "source_ledger.json")

    ensure_wiki_scaffold(wiki_dir)

    if not os.path.exists(ledger_path):
        print(f"Error: Ledger file not found at {ledger_path}")
        sys.exit(1)

    with open(ledger_path, "r", encoding="utf-8") as f:
        ledger = json.load(f)

    sources = ledger.get("sources", [])
    p2_sources = [s for s in sources if s.get("pass_status") == "P2 done"]
    included_sources = [s for s in sources if s.get("researcher_selection")]  # researcher-selected only
    gap_sources = [s for s in sources if s.get("candidate_gaps")]

    print(f"Building wiki pages for {len(p2_sources)} Pass 2 papers and {len(included_sources)} researcher-selected papers...", flush=True)

    paper_pages_created = 0
    target_set = p2_sources + included_sources + gap_sources
    seen_sids = set()

    for s in target_set:
        sid = s["source_id"]
        if sid in seen_sids:
            continue
        seen_sids.add(sid)
        build_paper_page(s, cache_dir, wiki_dir)
        paper_pages_created += 1

    # 1. Build wiki/topics/ pages
    topics = [
        ("Evidence Evaluation", "evidence-evaluation", "Benchmarks, empirical validation, hallucination detection, guardrails, error analysis."),
        ("Learner Agency", "learner-agency", "Student autonomy, self-regulation, pedagogical scaffolding, metacognition, inquiry."),
        ("Traceable Workflows", "traceable-workflows", "Step-by-step reasoning audits, provenance tracking, reproducible pipelines, event telemetry.")
    ]
    for t_name, t_slug, t_desc in topics:
        t_path = os.path.join(wiki_dir, "topics", f"{t_slug}.md")
        t_sources = []
        for s in sources:
            scores = s.get("researcher_scores") or {}
            score_key = t_slug.replace("-", "_")
            if scores.get(score_key, 0) and scores[score_key] >= 3:
                t_sources.append(s)

        papers_list = "\n".join(f"- [[papers/{s['source_id']}|{s['source_id']}]] - {s.get('title', 'Untitled')[:70]} ({s.get('year', 'Unknown')})" for s in t_sources[:25])
        t_content = f"""# Topic: {t_name}

**Description**: {t_desc}  
**Total Associated Papers**: {len(t_sources)}  

---

## Core Papers
{papers_list if papers_list else "No papers prioritized under this topic yet."}

---
*Return to [[index|Corpus Navigation Portal]] | View [[theories|Theoretical Frameworks]]*
"""
        with open(t_path, "w", encoding="utf-8") as f:
            f.write(t_content)

    # 2. Build wiki/gaps.md
    all_gaps = []
    for s in sources:
        for g in s.get("candidate_gaps", []):
            all_gaps.append((s["source_id"], s.get("title", "Untitled"), g))

    gaps_table_rows = []
    for sid, title, gap_text in all_gaps:
        gaps_table_rows.append(f"| `[[papers/{sid}|{sid}]]` | {title[:50]}... | {gap_text} | Open |")

    gaps_content = f"""# Literature Gap Register

> **{AI_DRAFT_LABEL}.** Rows below are sentences automatically extracted from papers. Whether a row is a real gap is the researcher's judgment.

This register collects candidate unexamined empirical questions, theoretical tensions, and methodological limitations from papers the researcher selected.

| Source ID | Paper Title | Candidate Gap (AI draft) | Status |
|-----------|-------------|------------------------------------------|--------|
{chr(10).join(gaps_table_rows) if gaps_table_rows else "| - | - | No candidate gaps extracted yet | - |"}

---

## Thematic Gap Clusters

### Cluster A: Human-AI Agency Trade-Offs
- How do agentic scaffolding systems reduce or enhance student self-regulation during ill-structured problem solving?
- Lack of longitudinal studies evaluating whether automated feedback induces learned helplessness or metacognitive growth.

### Cluster B: Verifiable Symbolic Grounding
- Current LLM evaluations rely heavily on closed benchmark accuracy rather than formal verification of reasoning traces.
- Open question: Can zero-copy local storage engines (S3A) verify reasoning chains without cloud latency?

### Cluster C: Multi-Agent Workflow Provenance
- Telemetry standards (e.g. xAPI, IEEE LTSC) currently lack formal representations for non-deterministic AI agent reasoning steps.

---
*Return to [[index|Corpus Navigation Portal]] | View [[theories|Theoretical Frameworks]]*
"""
    with open(os.path.join(wiki_dir, "gaps.md"), "w", encoding="utf-8") as f:
        f.write(gaps_content)

    # 3. Build wiki/theories.md
    theories_content = f"""# Theoretical Framework Registry

Maps foundational learning theories and cognitive architectures to empirical papers in the research corpus.

---

## 1. Cognitive Load Theory (CLT)
- **Description**: Analyzes mental effort split between intrinsic, germane, and extraneous load in educational interfaces.
- **Key Dimension**: [[topics/evidence-evaluation|Evidence Evaluation]] & [[topics/learner-agency|Learner Agency]].
- **Associated Papers**:
  - `[[papers/SRC-0796|SRC-0796]]`: Cognitive Pedagogy Platform
  - `[[papers/SRC-0239|SRC-0239]]`: Pedagogy-R1 Reasoning Model

## 2. Human-AI Synergy & Co-Intelligence
- **Description**: Mutual complementary capabilities between human researchers/students and AI autonomous agents.
- **Key Dimension**: [[topics/learner-agency|Learner Agency]].
- **Associated Papers**:
  - `[[papers/SRC-0003|SRC-0003]]`: Quantifying Human-AI Synergy via Item Response Theory
  - `[[papers/SRC-0368|SRC-0368]]`: Architecting Trust: Neuro-Symbolic Agentic Frameworks

## 3. Activity Theory & Sociocultural Learning
- **Description**: Mediated human action within institutional rules, tools, and community structures.
- **Key Dimension**: [[topics/traceable-workflows|Traceable Workflows]].
- **Associated Papers**:
  - `[[papers/SRC-0209|SRC-0209]]`: Shaping Generative AI Governance in Higher Education

## 4. Self-Regulated Learning (SRL) & Scaffolding
- **Description**: Metacognitive monitoring, goal orientation, and adaptive feedback fade-out.
- **Key Dimension**: [[topics/learner-agency|Learner Agency]].
- **Associated Papers**:
  - `[[papers/SRC-0002|SRC-0002]]`: Bridging LLMs and Symbolic Reasoning in Educational QA

---
*Return to [[index|Corpus Navigation Portal]] | View [[gaps|Literature Gap Register]]*
"""
    with open(os.path.join(wiki_dir, "theories.md"), "w", encoding="utf-8") as f:
        f.write(theories_content)

    # 4. Build wiki/index.md
    total_sources = len(sources)
    p2_count = len(p2_sources)
    p1_count = sum(1 for s in sources if s.get("pass_status") == "P1 done")
    inc_count = len(included_sources)

    index_content = f"""# Doctoral Research Corpus Wiki

Welcome to the interactive 3-pass literature review wiki. This repository compiles structured evidence, theoretical alignments, and literature gaps across the **{total_sources}** papers in the active Google Drive research corpus.

---

## Navigation & Master Indexes
- **[[gaps|Literature Gap Register]]**: Unexamined questions, empirical contradictions, and candidate doctoral research questions.
- **[[theories|Theoretical Framework Registry]]**: Theoretical grounding across Cognitive Load Theory, Human-AI Synergy, and Activity Theory.
- **Thematic Portals**:
  - [[topics/evidence-evaluation|Topic 1: Evidence Evaluation]]
  - [[topics/learner-agency|Topic 2: Learner Agency]]
  - [[topics/traceable-workflows|Topic 3: Traceable Workflows]]

---

## Corpus Status & Statistics
- **Total Registered Sources**: `{total_sources}`
- **Researcher-Selected Papers**: `{inc_count}`
- **Pass 2 Deep Analyzed**: `{p2_count}`
- **Active Backend**: Native S3A Storage Engine (`ledger/research_corpus.s3a`)

---

## Prioritized Paper Dossiers
| Source ID | Title | Year | Authors | Pass Status |
|-----------|-------|------|---------|-------------|
"""
    for s in target_set[:30]:
        title_trunc = (s.get("title") or "Untitled").replace("|", "-").strip()[:55]
        auth_trunc = (s.get("author") or "Unknown").replace("|", "-").strip()[:20]
        index_content += f"| `[[papers/{s['source_id']}|{s['source_id']}]]` | {title_trunc} | {s.get('year', 'Unknown')} | {auth_trunc} | `{s.get('pass_status')}` |\n"

    index_content += f"""
*(Showing top {min(30, len(target_set))} prioritized papers. See `ledger/source_ledger.json` for full corpus)*

---
*Maintained by the 3Pass Ingestion & Karpathy LLM Wiki Engine*
"""
    with open(os.path.join(wiki_dir, "index.md"), "w", encoding="utf-8") as f:
        f.write(index_content)

    print(f"Wiki generation complete. Created {paper_pages_created} paper pages + indexes.", flush=True)

def lint_wiki(cfg):
    wiki_dir = cfg.get("wiki_dir", "wiki")
    print("=" * 60, flush=True)
    print(f"Starting Wiki Integrity & Wikilink Linting: {wiki_dir}", flush=True)
    print("=" * 60, flush=True)

    if not os.path.exists(wiki_dir):
        print(f"Error: Wiki directory does not exist: {wiki_dir}")
        return False

    all_files = {}
    for root, _, files in os.walk(wiki_dir):
        for f in files:
            if f.endswith(".md"):
                rel_path = os.path.relpath(os.path.join(root, f), wiki_dir).replace("\\", "/")
                # Normalize key without extension and with extension
                base = os.path.splitext(rel_path)[0]
                all_files[rel_path] = os.path.join(root, f)
                all_files[base] = os.path.join(root, f)
                # Also allow direct filename
                all_files[f] = os.path.join(root, f)
                all_files[os.path.splitext(f)[0]] = os.path.join(root, f)

    broken_links = []
    missing_provenance = []
    total_links_checked = 0

    link_pattern = re.compile(r"\[\[(.*?)\]\]")

    for rel_key, fpath in list(all_files.items()):
        if not rel_key.endswith(".md"):
            continue

        with open(fpath, "r", encoding="utf-8") as fp:
            content = fp.read()

        # Check provenance in paper files
        if "papers/" in rel_key:
            if "provenance:" not in content and "**Provenance**:" not in content:
                missing_provenance.append((rel_key, "Missing provenance header/tag"))

        # Find wikilinks
        for match in link_pattern.finditer(content):
            total_links_checked += 1
            raw_target = match.group(1).strip()
            # Handle [[target|label]] and [[target#anchor|label]]
            target = raw_target.split("|")[0].strip()
            target_no_anchor = target.split("#")[0].strip()

            if not target_no_anchor:
                continue  # Local anchor link like [[#anchor]]

            # Normalization variants to check
            variants = [
                target_no_anchor,
                f"{target_no_anchor}.md",
                f"papers/{target_no_anchor}",
                f"papers/{target_no_anchor}.md",
                f"topics/{target_no_anchor}",
                f"topics/{target_no_anchor}.md",
                os.path.basename(target_no_anchor)
            ]

            found = any(v in all_files for v in variants)
            if not found:
                broken_links.append((rel_key, raw_target))

    print(f"Total Wiki Documents Scanned: {len(set(all_files.values()))}", flush=True)
    print(f"Total Wikilinks Checked:       {total_links_checked}", flush=True)
    print(f"Broken Wikilinks:              {len(broken_links)}", flush=True)
    print(f"Missing Provenance Headers:    {len(missing_provenance)}", flush=True)

    if broken_links:
        print("\nBroken Wikilinks Found:", flush=True)
        for doc, link in broken_links[:10]:
            print(f"  - In `{doc}`: [[{link}]]", flush=True)

    if missing_provenance:
        print("\nMissing Provenance Warnings:", flush=True)
        for doc, msg in missing_provenance[:10]:
            print(f"  - In `{doc}`: {msg}", flush=True)

    success = len(broken_links) == 0 and len(missing_provenance) == 0
    if success:
        print("\nLint Result: 100% PASS. All internal links and provenance headers valid.", flush=True)
    else:
        print(f"\nLint Result: FAILED with {len(broken_links)} broken links and {len(missing_provenance)} provenance issues.", flush=True)

    return success

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Wiki Manager & Linter")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="Path to corpus_config.yaml")
    parser.add_argument("--sync-all", action="store_true", help="Sync all P2 and top papers into wiki")
    parser.add_argument("--lint", action="store_true", help="Lint wiki links and provenance")
    parser.add_argument("--add-paper", help="Add specific Source ID to wiki")
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.sync_all:
        sync_all_wiki(cfg)
    if args.add_paper:
        ensure_wiki_scaffold(cfg.get("wiki_dir", "wiki"))
        # Load ledger and build
        with open(os.path.join(cfg.get("ledger_dir", "ledger"), "source_ledger.json"), "r", encoding="utf-8") as f:
            ledger = json.load(f)
        for s in ledger.get("sources", []):
            if s["source_id"].upper() == args.add_paper.upper():
                build_paper_page(s, cfg.get("cache_dir", "cache"), cfg.get("wiki_dir", "wiki"))
                print(f"Added {s['source_id']} to wiki.", flush=True)
                break
    if args.lint:
        ok = lint_wiki(cfg)
        sys.exit(0 if ok else 1)
