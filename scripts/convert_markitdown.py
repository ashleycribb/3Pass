"""
Markdown Extraction Engine using Microsoft MarkItDown + PyMuPDF

Extracts academic paper text, tables, and mathematical formulas into structured Markdown.
Preserves LaTeX blocks, isolates tables into discrete markdown blocks, and embeds
provenance headers for verifiable scholarly tracing.
"""

import os
import sys
import re
import json
import argparse
from datetime import datetime
import pymupdf
from markitdown import MarkItDown
import yaml

DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "corpus_config.yaml")

def load_config(config_path=DEFAULT_CONFIG_PATH):
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    return {
        "ledger_dir": "ledger",
        "cache_dir": "cache"
    }

def sanitize_filename(name):
    clean = re.sub(r'[\\/*?:"<>|]', "", name)
    clean = re.sub(r"\s+", "_", clean).strip("_")
    return clean[:60]

def extract_tables_from_pdf(pdf_path):
    """
    Extracts table bounding blocks and formats as Markdown tables using PyMuPDF.
    """
    tables = []
    try:
        doc = pymupdf.open(pdf_path)
        for page_num in range(len(doc)):
            page = doc[page_num]
            tabs = page.find_tables()
            if tabs and len(tabs.tables) > 0:
                for t_idx, tab in enumerate(tabs.tables):
                    df = tab.extract()
                    if df and len(df) > 1:
                        # Convert to markdown table format
                        headers = [str(c or "").strip() for c in df[0]]
                        lines = ["| " + " | ".join(headers) + " |"]
                        lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
                        for row in df[1:]:
                            row_cells = [str(c or "").strip().replace("\n", " ") for c in row]
                            lines.append("| " + " | ".join(row_cells) + " |")
                        md_table = "\n".join(lines)
                        tables.append({
                            "page": page_num + 1,
                            "index": t_idx + 1,
                            "markdown": md_table
                        })
        doc.close()
    except Exception as e:
        pass
    return tables

def preserve_latex_math(text):
    """
    Detects and normalizes LaTeX math expressions in text.
    Ensures equations are properly formatted with KaTeX-compatible fencing.
    """
    # Protect display math environments
    text = re.sub(r"(\\begin\{(?:equation|align|gather)\*?\}.*?\\end\{(?:equation|align|gather)\*?\})", r"\n$$\n\1\n$$\n", text, flags=re.DOTALL)
    # Normalize escaped brackets \( ... \) to $ ... $ and \[ ... \] to $$ ... $$
    text = re.sub(r"\\\[(.*?)\\\]", r"\n$$\n\1\n$$\n", text, flags=re.DOTALL)
    text = re.sub(r"\\\(\s*(.*?)\s*\\\)", r"$\1$", text)
    return text

def convert_single_paper(rec, md_engine, cache_md_dir, drive_md_dir=None):
    sid = rec["source_id"]
    pdf_path = rec.get("drive_path")
    title = rec.get("title", "paper")
    slug = sanitize_filename(title)
    
    md_filename = f"{sid}_{slug}.md"
    cache_path = os.path.join(cache_md_dir, md_filename)
    os.makedirs(cache_md_dir, exist_ok=True)
    os.makedirs(os.path.join(cache_md_dir, "tables"), exist_ok=True)

    print(f"Converting [{sid}] {os.path.basename(pdf_path or '')}...", flush=True)

    if not pdf_path or not os.path.exists(pdf_path):
        print(f"  Error: File not found at {pdf_path}", flush=True)
        return False

    try:
        # 1. MarkItDown text conversion
        res = md_engine.convert(pdf_path)
        raw_text = res.text_content

        # 2. LaTeX math preservation
        formatted_text = preserve_latex_math(raw_text)

        # 3. Table extraction
        extracted_tables = extract_tables_from_pdf(pdf_path)
        tables_section = ""
        if extracted_tables:
            tables_section = "\n\n## Extracted Tables\n\n"
            for t in extracted_tables:
                tables_section += f"### Table (Page {t['page']}, #{t['index']})\n\n{t['markdown']}\n\n"

        # 4. Prepend Provenance Metadata Header
        header = f"""---
source_id: {sid}
title: "{title}"
author: "{rec.get('author', 'Unknown')}"
year: "{rec.get('year', 'Unknown')}"
original_drive_path: "{pdf_path}"
converted_at: "{datetime.now().isoformat()}"
provenance: "{sid} | {pdf_path} | {rec.get('pass_status', 'P1 done')}"
tables_extracted: {len(extracted_tables)}
---

# {title}

"""
        full_md = header + formatted_text + tables_section

        # Save to local cache
        with open(cache_path, "w", encoding="utf-8") as f:
            f.write(full_md)

        # Optionally save to Google Drive mirror if directory exists
        if drive_md_dir and os.path.exists(os.path.dirname(drive_md_dir)):
            try:
                os.makedirs(drive_md_dir, exist_ok=True)
                drive_path = os.path.join(drive_md_dir, md_filename)
                with open(drive_path, "w", encoding="utf-8") as f:
                    f.write(full_md)
                rec["markdown_drive_path"] = drive_path
            except Exception:
                pass

        rec["markdown_local_path"] = cache_path
        rec["markdown_chars"] = len(full_md)
        rec["tables_count"] = len(extracted_tables)
        print(f"  -> Converted successfully ({len(full_md)} chars, {len(extracted_tables)} tables)", flush=True)
        return True
    except Exception as e:
        print(f"  Error converting {sid}: {e}", flush=True)
        return False

def main():
    parser = argparse.ArgumentParser(description="Convert corpus papers to Markdown using MarkItDown + PyMuPDF")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="Path to corpus_config.yaml")
    parser.add_argument("--id", help="Convert specific source ID (e.g. SRC-0002)")
    parser.add_argument("--status", help="Convert papers with specific pass status (e.g. 'P2 done')")
    parser.add_argument("--decision", help="Convert papers with specific triage decision (e.g. 'include')")
    parser.add_argument("--limit", type=int, default=10, help="Maximum number of papers to convert")
    args = parser.parse_args()

    cfg = load_config(args.config)
    ledger_dir = cfg.get("ledger_dir", "ledger")
    cache_dir = cfg.get("cache_dir", "cache")
    ledger_path = os.path.join(ledger_dir, "source_ledger.json")
    cache_md_dir = os.path.join(cache_dir, "markdown")
    drive_md_dir = r"G:\My Drive\AcademicPapers_Markdown"

    if not os.path.exists(ledger_path):
        print(f"Ledger not found at {ledger_path}")
        return

    with open(ledger_path, "r", encoding="utf-8") as f:
        ledger = json.load(f)

    sources = ledger["sources"]
    targets = []

    if args.id:
        targets = [s for s in sources if s["source_id"].upper() == args.id.upper()]
    elif args.status:
        targets = [s for s in sources if s.get("pass_status") == args.status]
    elif args.decision:
        targets = [s for s in sources if s.get("triage_decision") == args.decision]
    else:
        # Default: P2 done papers first, then include papers
        targets = [s for s in sources if s.get("pass_status") == "P2 done"]
        if not targets:
            targets = [s for s in sources if s.get("triage_decision") == "include"]

    targets = targets[:args.limit]
    print(f"Targeting {len(targets)} papers for Markdown conversion...", flush=True)

    md_engine = MarkItDown()
    converted_count = 0

    for rec in targets:
        if convert_single_paper(rec, md_engine, cache_md_dir, drive_md_dir):
            converted_count += 1

    # Save ledger updates
    with open(ledger_path, "w", encoding="utf-8") as f:
        json.dump(ledger, f, indent=2, ensure_ascii=False)

    print(f"\nConversion complete: {converted_count}/{len(targets)} papers converted to Markdown.", flush=True)

if __name__ == "__main__":
    main()
