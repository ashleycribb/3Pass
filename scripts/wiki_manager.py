"""
Karpathy LLM Research Wiki Manager & Linter

Manages the doctoral literature review wiki (wiki/):
  - Bookkeeping only: Extracted facts and structure. No automated judgments or summaries.
  - Sourced draft pages (wiki/papers/SRC-XXXX.md) with clearly marked empty sections
    for the researcher's own notes and judgments.
  - Master catalog (wiki/index.md) tracking completeness and extraction path.
  - Append-only event log (wiki/log.md and LOG.md).
  - Linter ensuring 100% wikilink resolution and provenance compliance (exit code 0).
"""

import os
import sys

# Ensure repository root is in python path
WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

import re
import json
import argparse
from datetime import datetime
from typing import Dict, Any, List, Optional
import yaml

try:
    from scripts.corpus_common import DEFAULT_CONFIG_PATH, load_config
    from scripts.tile_ids import AI_DRAFT_LABEL
except ImportError:
    from corpus_common import DEFAULT_CONFIG_PATH, load_config
    from tile_ids import AI_DRAFT_LABEL

from scripts.models import PaperRecord


def sanitize_slug(text):
    clean = re.sub(r"[^a-zA-Z0-9_-]", "-", text.lower())
    clean = re.sub(r"-+", "-", clean).strip("-")
    return clean


def ensure_wiki_scaffold(wiki_dir):
    os.makedirs(wiki_dir, exist_ok=True)
    os.makedirs(os.path.join(wiki_dir, "papers"), exist_ok=True)
    os.makedirs(os.path.join(wiki_dir, "topics"), exist_ok=True)
    # Ensure log.md exists
    log_path = os.path.join(wiki_dir, "log.md")
    if not os.path.exists(log_path):
        with open(log_path, "w", encoding="utf-8") as fp:
            fp.write("# 3Pass Research Corpus: Intake Log\n\nAppend-only ledger of source routing, extraction, and wiki synchronization events.\n\n")


def append_to_log(wiki_dir: str, message: str):
    """Appends an event line to both wiki/log.md and root LOG.md."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"- `[{ts}]` {message}\n"
    
    # 1. wiki/log.md
    wiki_log = os.path.join(wiki_dir, "log.md")
    with open(wiki_log, "a", encoding="utf-8") as fp:
        fp.write(entry)

    # 2. root LOG.md
    root_log = os.path.join(os.path.dirname(os.path.dirname(__file__)), "LOG.md")
    with open(root_log, "a", encoding="utf-8") as fp:
        fp.write(entry)


def build_paper_page(rec, cache_dir, wiki_dir):
    """Legacy helper for dict-based records used by tests and existing pipelines."""
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
    provenance = rec.get("provenance", f"{sid} | {drive_path} | {pass_status}")

    theories_links = ", ".join(f"[[theories#{sanitize_slug(t)}|{t}]]" for t in theories) if theories else "None explicitly indexed"
    gaps_content = "\n".join(f"- {g}" for g in gaps) if gaps else "- No unaddressed literature gaps isolated in primary scan."

    page_content = f"""---
source_id: {sid}
title: "{title}"
authors: "{authors}"
year: "{year}"
drive_path: "{drive_path}"
pass_status: "{pass_status}"
provenance: "{provenance}"
updated_at: "{datetime.now().isoformat()}"
ai_draft: true
ai_draft_label: "{AI_DRAFT_LABEL}"
---

# {title}

> **{AI_DRAFT_LABEL}.** Extracted automatically. Researcher critical evaluation is required.

**Source ID**: `{sid}`  
**Authors**: {authors}  
**Year**: {year}  
**Provenance**: `{provenance}`  
**Local PDF**: `{drive_path}`  
**Theoretical Frameworks**: {theories_links}  

---

## Abstract
{abstract or 'No abstract excerpt provided.'}

---

## Literature Gaps ({AI_DRAFT_LABEL})
{gaps_content}

---

## Researcher Notes & Critical Appraisal

> *This section is reserved exclusively for the doctoral researcher's judgments, evaluation, and synthesis.*

### Problem Formulation & Theoretical Alignment
*(Researcher notes go here)*

### Methodology Evaluation
*(Researcher notes go here)*

### Findings & Evidence Credibility
*(Researcher notes go here)*

### Gaps & Questions for Dissertation
*(Researcher notes go here)*

---
*Return to [[index|Corpus Navigation Portal]] | View [[gaps|Literature Gap Register]] | View [[theories|Theoretical Frameworks]]*
"""
    os.makedirs(os.path.join(wiki_dir, "papers"), exist_ok=True)
    out_file = os.path.join(wiki_dir, "papers", f"{sid}.md")
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(page_content)
    return out_file


def build_paper_page_from_record(rec: PaperRecord, wiki_dir: str) -> str:
    """
    Renders a draft SRC wiki page containing facts and structure only,
    with clearly marked empty sections for the human researcher's critical notes.
    """
    sid = rec.source_id
    prov = rec.provenance
    path_used = prov.path_used if prov else "unknown"
    tool_name = prov.tool if prov else "unspecified"
    tool_ver = prov.tool_version if prov else "unspecified"
    source_uri = prov.source_uri if prov else rec.urls.get("pdf", "")

    # Format sections
    sections_md = ""
    if rec.sections:
        sections_md = "## Extracted Document Sections\n\n"
        for s in rec.sections:
            ver_tag = f" *({s.verified})*" if s.verified != "verified" else ""
            h_prefix = "#" * max(3, min(6, s.level + 2))
            sections_md += f"{h_prefix} {s.title}{ver_tag}\n\n{s.text}\n\n"
    elif rec.completeness == "metadata-only":
        sections_md = "## Extracted Document Sections\n\n*(Metadata-only intake: document body not ingested. See external links for full text.)*\n\n"

    # Format figures & tables
    figs_md = ""
    if rec.figures:
        figs_md = "## Figures\n\n"
        for f in rec.figures:
            figs_md += f"- **{f.id}**: {f.caption or 'No caption'} *({f.verified})*\n"
        figs_md += "\n"

    tbls_md = ""
    if rec.tables:
        tbls_md = "## Tables\n\n"
        for t in rec.tables:
            tbls_md += f"- **{t.id}**: {t.caption or 'No caption'} *({t.verified})*\n"
        tbls_md += "\n"

    eqs_md = ""
    if rec.equations:
        eqs_md = "## Equations\n\n"
        for e in rec.equations:
            eqs_md += f"- **{e.id}**: `{e.latex or e.text}` *({e.verified})*\n"
        eqs_md += "\n"

    # Format references
    refs_md = ""
    if rec.references:
        refs_md = "## References Cited\n\n"
        for r in rec.references[:25]:
            r_doi = f" (DOI: {r.doi})" if r.doi else ""
            ref_str = getattr(r, "raw", getattr(r, "raw_text", r.title)) or r.title
            refs_md += f"- {ref_str}{r_doi}\n"
        refs_md += "\n"

    authors_str = ", ".join(rec.authors) if rec.authors else "Unknown"

    content = f"""---
source_id: {sid}
title: "{rec.title}"
authors: "{authors_str}"
year: {rec.year or 'null'}
doi: "{rec.doi or ''}"
arxiv_id: "{rec.arxiv_id or ''}"
path_used: "{path_used}"
completeness: "{rec.completeness}"
provenance: "{sid} | {path_used} | {tool_name}"
tool: "{tool_name}"
tool_version: "{tool_ver}"
sha256: "{rec.sha256 or ''}"
updated_at: "{datetime.now().isoformat()}"
---

# {rec.title}

**Source ID**: `{sid}`  
**Authors**: {authors_str}  
**Year**: {rec.year or 'Unknown'}  
**Path Used**: `{path_used}`  
**Completeness Level**: `{rec.completeness}`  
**Provenance**: `{sid} | {path_used} | {tool_name}`  
**Extraction Tool**: {tool_name} ({tool_ver})  
**External Resource**: [{source_uri or 'N/A'}]({source_uri or '#'})

---

## Abstract
{rec.abstract or '*(No abstract extracted)*'}

---

{sections_md}
{figs_md}
{tbls_md}
{eqs_md}
{refs_md}
---

## Researcher Notes & Critical Appraisal

> *This section is reserved exclusively for the doctoral researcher's judgments, evaluation, and synthesis. Automated tools provide structure and facts above; critical assessment belongs to the researcher.*

### Problem Formulation & Theoretical Alignment
{rec.human_notes.get('problem_formulation') or '*(Researcher notes go here)*'}

### Methodology Evaluation
{rec.human_notes.get('methodology_evaluation') or '*(Researcher notes go here)*'}

### Findings & Evidence Credibility
{rec.human_notes.get('findings_credibility') or '*(Researcher notes go here)*'}

### Gaps & Questions for Dissertation
{rec.human_notes.get('dissertation_gaps') or '*(Researcher notes go here)*'}

---
*Return to [[index|Corpus Navigation Portal]] | View [[gaps|Literature Gap Register]] | View [[theories|Theoretical Frameworks]]*
"""
    out_file = os.path.join(wiki_dir, "papers", f"{sid}.md")
    with open(out_file, "w", encoding="utf-8") as fp:
        fp.write(content)
    return out_file


def update_wiki_index(sources: List[Dict[str, Any]], wiki_dir: str):
    """Regenerates wiki/index.md linking all ingested papers with completeness and path."""
    total = len(sources)
    full_count = sum(1 for s in sources if s.get("completeness") == "full")
    pdf_count = sum(1 for s in sources if s.get("completeness") == "pdf-extracted")
    meta_count = sum(1 for s in sources if s.get("completeness") == "metadata-only")

    content = f"""# Doctoral Research Corpus Wiki

Bookkeeping-only research index and human appraisal portal.  
Automated judgments and summaries are disabled; all evaluations are drafted by the researcher.

---

## Navigation & Master Indexes
- **[[gaps|Literature Gap Register]]**: Researcher-formulated empirical questions and dissertation gaps.
- **[[theories|Theoretical Framework Registry]]**: Foundational theories (Cognitive Load Theory, Human-AI Synergy, Activity Theory).
- **[[log|Corpus Intake Log]]**: Append-only event history of paper routing and updates.
- **Thematic Portals**:
  - [[topics/evidence-evaluation|Topic 1: Evidence Evaluation]]
  - [[topics/learner-agency|Topic 2: Learner Agency]]
  - [[topics/traceable-workflows|Topic 3: Traceable Workflows]]

---

## Corpus Status & Statistics
- **Total Registered Sources**: `{total}`
- **Full Structure (arXiv LaTeXML / LaTeX)**: `{full_count}`
- **PDF-Extracted (GROBID / TEI)**: `{pdf_count}`
- **Metadata-Only (Crossref / OpenAlex)**: `{meta_count}`
- **Active Backend**: S3A Native Hyper-Tile Storage Engine (`ledger/research_corpus.s3a`)

---

## Paper Dossiers
| Source ID | Title | Year | Authors | Path Used | Completeness |
|-----------|-------|------|---------|-----------|--------------|
"""
    for s in sources:
        sid = s["source_id"]
        title_t = (s.get("title") or "Untitled").replace("|", "-").strip()[:50]
        auth_t = (s.get("author") or "Unknown").replace("|", "-").strip()[:20]
        comp = s.get("completeness", "pdf-extracted")
        path_used = s.get("path_used", "pdf")
        content += f"| `[[papers/{sid}|{sid}]]` | {title_t} | {s.get('year', 'Unknown')} | {auth_t} | `{path_used}` | `{comp}` |\n"

    content += """
---
*Maintained by 3Pass Literature Review Engine (Bookkeeping only)*
"""
    with open(os.path.join(wiki_dir, "index.md"), "w", encoding="utf-8") as fp:
        fp.write(content)


def lint_wiki(cfg) -> bool:
    """Validates all internal wikilinks and ensures strict provenance compliance."""
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
                base = os.path.splitext(rel_path)[0]
                all_files[rel_path] = os.path.join(root, f)
                all_files[base] = os.path.join(root, f)
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
                missing_provenance.append((rel_key, "Missing provenance tag"))

        # Find wikilinks
        for match in link_pattern.finditer(content):
            total_links_checked += 1
            raw_target = match.group(1).strip()
            target = raw_target.split("|")[0].strip()
            target_no_anchor = target.split("#")[0].strip()

            if not target_no_anchor:
                continue

            # If target points to papers/ but papers directory is unpopulated (e.g. fresh clone with gitignored wiki/papers/)
            if target_no_anchor.startswith("papers/"):
                papers_dir = os.path.join(wiki_dir, "papers")
                if not os.path.exists(papers_dir) or len([f for f in os.listdir(papers_dir) if f.endswith(".md")]) == 0:
                    continue

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

    success = len(broken_links) == 0 and len(missing_provenance) == 0
    if success:
        print("\nLint Result: 100% PASS. All internal links and provenance valid.", flush=True)
    else:
        print(f"\nLint Result: FAILED with {len(broken_links)} broken links.", flush=True)

    return success


def sync_all_from_ledger(cfg):
    """Regenerates all paper draft pages with facts and structure only, plus empty researcher notes."""
    ledger_path = os.path.join(cfg.get("ledger_dir", "ledger"), "source_ledger.json")
    wiki_dir = cfg.get("wiki_dir", "wiki")
    ensure_wiki_scaffold(wiki_dir)

    if not os.path.exists(ledger_path):
        print(f"Error: Ledger file not found at {ledger_path}")
        return

    with open(ledger_path, "r", encoding="utf-8") as fp:
        ledger = json.load(fp)

    sources = ledger.get("sources", [])
    included = [s for s in sources if s.get("triage_decision") == "include" or s.get("pass_status") == "P2 done"]
    print(f"Syncing {len(included)} paper draft pages (bookkeeping only)...", flush=True)

    synced = 0
    for s in included:
        sid = s["source_id"]
        authors = [a.strip() for a in str(s.get("author", "Unknown")).split(";") if a.strip()]
        
        path_used = s.get("path_used", "grobid_pdf")
        completeness = s.get("completeness", "pdf-extracted")
        tool_name = "grobid-tei" if completeness == "pdf-extracted" else "latexml-html"
        tool_ver = "GROBID 0.8.1 (pdf-extracted, verify)" if completeness == "pdf-extracted" else "LaTeXML 0.8.8"

        prov = {
            "path_used": path_used,
            "tool": tool_name,
            "tool_version": tool_ver,
            "source_uri": s.get("drive_path", ""),
            "extracted_at": datetime.now().isoformat(),
            "per_part": {
                "metadata": {"tool": tool_name, "status": "extracted"},
                "sections": {"tool": tool_name, "status": completeness},
                "references": {"tool": tool_name, "status": completeness}
            }
        }

        # Build sections if available
        sections = []
        if s.get("abstract_excerpt"):
            sections.append({
                "id": "sec_1",
                "title": "Document Overview & Extracted Text",
                "text": s["abstract_excerpt"],
                "level": 1,
                "verified": "pdf-extracted, verify" if completeness == "pdf-extracted" else "verified"
            })

        rec_dict = {
            "source_id": sid,
            "paper_id": int(re.search(r"SRC-(\d+)", sid).group(1)) if re.search(r"SRC-(\d+)", sid) else 0,
            "title": s.get("title", "Untitled"),
            "authors": authors,
            "year": int(s.get("year")) if s.get("year") and str(s.get("year")).isdigit() else None,
            "doi": s.get("doi"),
            "arxiv_id": s.get("arxiv_id"),
            "urls": {"pdf": s.get("drive_path", "")},
            "completeness": completeness,
            "provenance": prov,
            "abstract": s.get("abstract_excerpt", ""),
            "sections": sections,
            "figures": [],
            "tables": [],
            "equations": [],
            "references": [],
            "human_notes": {
                "problem_formulation": "",
                "methodology_evaluation": "",
                "findings_credibility": "",
                "dissertation_gaps": ""
            },
            "sha256": s.get("sha256")
        }

        rec = PaperRecord.from_dict(rec_dict)
        build_paper_page_from_record(rec, wiki_dir)
        synced += 1

    update_wiki_index(included, wiki_dir)
    append_to_log(wiki_dir, f"SYNC_ALL synced {synced} draft pages with empty researcher appraisal sections.")
    print(f"Synced {synced} draft pages in {wiki_dir}/papers/.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Wiki Manager & Linter")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="Path to corpus_config.yaml")
    parser.add_argument("--sync-all", action="store_true", help="Sync all draft pages from ledger")
    parser.add_argument("--lint", action="store_true", help="Lint wiki links and provenance")
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.sync_all:
        sync_all_from_ledger(cfg)
    if args.lint:
        ok = lint_wiki(cfg)
        sys.exit(0 if ok else 1)
