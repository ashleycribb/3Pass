"""
Smoke Test for 4 Real Papers (One per Source Router Path)

Executes extraction across:
  1. Path a: arXiv with HTML at arxiv.org/html/<id> -> parses LaTeXML classes (completeness: full)
  2. Path b: LaTeX source -> parses LaTeX structure (completeness: full)
  3. Path c: PDF-only journal paper -> GROBID / TEI (completeness: pdf-extracted, verify)
  4. Path d: DOI/metadata-only -> Crossref polite pool (completeness: metadata-only)

Saves output records in `tests/real/` (PDFs are gitignored; JSON records are tracked).
"""

import os
import sys
import json
import tarfile
import io
import requests
from typing import Dict, Any

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from scripts.source_router import (
    ArxivHtmlPlugin,
    LatexSourcePlugin,
    GrobidPdfPlugin,
    CrossrefOpenAlexPlugin,
    SourceRouter,
)
from scripts.models import PaperRecord

REAL_DIR = os.path.join(WORKSPACE_ROOT, "tests", "real")
os.makedirs(REAL_DIR, exist_ok=True)


def run_path_a_arxiv_html() -> PaperRecord:
    print("=" * 60)
    print("Path a: Real arXiv Paper with HTML (arxiv.org/html/2403.05530)")
    print("=" * 60)
    plugin = ArxivHtmlPlugin()
    paper_input = {
        "source_id": "SRC-REAL-A",
        "arxiv_id": "2403.05530",
        "title": "Gemini 1.5: Unlocking multimodal understanding across millions of tokens of context",
    }
    rec = plugin.extract(paper_input)
    out_path = os.path.join(REAL_DIR, "path_a_arxiv_html.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rec.to_dict(), f, indent=2, ensure_ascii=False)
    print(f"Extracted: '{rec.title}' by {len(rec.authors)} authors")
    print(f"Sections: {len(rec.sections)}, Figures: {len(rec.figures)}, Tables: {len(rec.tables)}, Equations: {len(rec.equations)}, Refs: {len(rec.references)}")
    print(f"Completeness: {rec.completeness}, Provenance: {rec.provenance.path_used}")
    print(f"Saved record to {out_path}")
    return rec


def run_path_b_latex_source() -> PaperRecord:
    print("\n" + "=" * 60)
    print("Path b: Real Paper with LaTeX Source (Attention Is All You Need - 1706.03762)")
    print("=" * 60)
    # Download arXiv e-print source tar
    eprint_url = "https://arxiv.org/e-print/1706.03762"
    r = requests.get(eprint_url, headers={"User-Agent": "3PassResearchCorpus/1.0"}, timeout=15)
    r.raise_for_status()

    # Find main tex file in tarball
    tar = tarfile.open(fileobj=io.BytesIO(r.content))
    tex_content = ""
    for member in tar.getmembers():
        if member.name.endswith(".tex"):
            f = tar.extractfile(member)
            if f:
                content = f.read().decode("utf-8", errors="replace")
                if "\\begin{document}" in content or "\\section" in content:
                    tex_content = content
                    break
    if not tex_content:
        # Fallback to first tex file
        for member in tar.getmembers():
            if member.name.endswith(".tex"):
                f = tar.extractfile(member)
                if f:
                    tex_content = f.read().decode("utf-8", errors="replace")
                    break

    plugin = LatexSourcePlugin()
    paper_input = {
        "source_id": "SRC-REAL-B",
        "title": "Attention Is All You Need",
        "latex_fixture": tex_content,
        "fixture_uri": eprint_url,
    }
    rec = plugin.extract(paper_input)
    out_path = os.path.join(REAL_DIR, "path_b_latex_source.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rec.to_dict(), f, indent=2, ensure_ascii=False)
    print(f"Extracted: '{rec.title}'")
    print(f"Sections: {len(rec.sections)}, Figures: {len(rec.figures)}, Tables: {len(rec.tables)}, Equations: {len(rec.equations)}, Refs: {len(rec.references)}")
    print(f"Completeness: {rec.completeness}, Provenance: {rec.provenance.path_used}")
    print(f"Saved record to {out_path}")
    return rec


def run_path_c_grobid_pdf() -> PaperRecord:
    print("\n" + "=" * 60)
    print("Path c: Real PDF-Only Journal Paper (PLOS ONE - 10.1371/journal.pone.0298888)")
    print("=" * 60)
    pdf_url = "https://journals.plos.org/plosone/article/file?id=10.1371/journal.pone.0298888&type=printable"
    local_pdf = os.path.join(REAL_DIR, "plos_journal_paper.pdf")
    if not os.path.exists(local_pdf):
        print(f"Downloading real journal PDF from {pdf_url}...")
        r = requests.get(pdf_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        r.raise_for_status()
        with open(local_pdf, "wb") as f:
            f.write(r.content)
        print(f"Saved {os.path.getsize(local_pdf)} bytes to {local_pdf} (gitignored)")

    plugin = GrobidPdfPlugin()
    paper_input = {
        "source_id": "SRC-REAL-C",
        "title": "PLOS ONE Research Article",
        "original_path": local_pdf,
        "drive_path": local_pdf,
    }
    rec = plugin.extract(paper_input)
    out_path = os.path.join(REAL_DIR, "path_c_grobid_pdf.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rec.to_dict(), f, indent=2, ensure_ascii=False)
    print(f"Extracted: '{rec.title}' by {rec.authors}")
    print(f"Sections: {len(rec.sections)}, Figures: {len(rec.figures)}, Tables: {len(rec.tables)}, Refs: {len(rec.references)}")
    print(f"Completeness: {rec.completeness}, Provenance: {rec.provenance.path_used}")
    print(f"Part verification labels: {rec.sections[0].verified if rec.sections else 'N/A'}")
    print(f"Saved record to {out_path}")
    return rec


def run_path_d_doi_metadata() -> PaperRecord:
    print("\n" + "=" * 60)
    print("Path d: Real DOI-Only Metadata (ACM SIGCSE - 10.1145/3491101.3503719)")
    print("=" * 60)
    plugin = CrossrefOpenAlexPlugin()
    paper_input = {
        "source_id": "SRC-REAL-D",
        "doi": "10.1145/3491101.3503719",
    }
    rec = plugin.extract(paper_input)
    out_path = os.path.join(REAL_DIR, "path_d_doi_metadata.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rec.to_dict(), f, indent=2, ensure_ascii=False)
    print(f"Extracted: '{rec.title}' by {rec.authors} ({rec.year})")
    print(f"Sections: {len(rec.sections)} (must be 0 for metadata-only), Refs: {len(rec.references)}")
    print(f"Completeness: {rec.completeness}, Provenance: {rec.provenance.path_used}")
    print(f"Saved record to {out_path}")
    return rec


if __name__ == "__main__":
    r_a = run_path_a_arxiv_html()
    r_b = run_path_b_latex_source()
    r_c = run_path_c_grobid_pdf()
    r_d = run_path_d_doi_metadata()
    print("\n" + "=" * 60)
    print("All 4 real-paper smoke tests completed successfully!")
    print("=" * 60)
