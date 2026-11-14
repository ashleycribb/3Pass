"""
Smoke Test for 4 Real Papers (One per Source Router Path)

Executes extraction across:
  1. Path a: Real arXiv with HTML (arxiv.org/html/2403.05530 Gemini 1.5)
     - Must show all authors (>1000), year 2024, no COLING DOI from bibliography.
  2. Path b: Real LaTeX source (1706.03762 Attention Is All You Need)
     - Asserts title matches arxiv_id, extracts figures, tables, and equations from .tex.
  3. Path c: Real PDF-only journal paper (PLOS ONE 10.1371/journal.pone.0298888)
     - Tests GROBID unreachable failure (fails loudly without fallback).
     - Re-runs with authoritative TEI extraction, verifying complete authors and references.
  4. Path d: Real DOI/metadata-only (ACM SIGCSE 10.1145/3491101.3503719)
     - Crossref polite pool, metadata-only (no body).

Saves output records in `tests/real/` (PDFs are gitignored; JSON records are tracked).
"""

import os
import sys
import json
import tarfile
import io
import re
import hashlib
import requests
import xml.etree.ElementTree as ET
from typing import Dict, Any, Optional, List

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from scripts.source_router import (
    ArxivHtmlPlugin,
    LatexSourcePlugin,
    PublisherXmlPlugin,
    GrobidPdfPlugin,
    CrossrefOpenAlexPlugin,
    SourceRouter,
    TEI_CACHE_DIR,
)
from scripts.models import PaperRecord

REAL_DIR = os.path.join(WORKSPACE_ROOT, "tests", "real")
os.makedirs(REAL_DIR, exist_ok=True)


def run_path_a_arxiv_html() -> PaperRecord:
    print("=" * 60)
    print("Path a: Real arXiv Paper with HTML (arxiv.org/html/2403.05530 - Gemini 1.5)")
    print("=" * 60)
    plugin = ArxivHtmlPlugin()
    paper_input = {
        "source_id": "SRC-REAL-A",
        "arxiv_id": "2403.05530",
        "title": "Gemini 1.5: Unlocking multimodal understanding across millions of tokens of context",
    }
    rec = plugin.extract(paper_input)

    # Assertions for Item 1
    assert "Gemini 1.5" in rec.title, f"Title mismatch: {rec.title}"
    assert len(rec.authors) >= 1000, f"Expected all Gemini 1.5 authors (>1000), got {len(rec.authors)}"
    assert rec.year == 2024, f"Expected year 2024, got {rec.year}"
    assert rec.doi is None, f"Expected no COLING DOI, got {rec.doi}"
    assert rec.sha256 and len(rec.sha256) == 64, f"Invalid sha256: {rec.sha256}"
    assert rec.paper_id > 0, f"Invalid paper_id: {rec.paper_id}"

    out_path = os.path.join(REAL_DIR, "path_a_arxiv_html.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rec.to_dict(), f, indent=2, ensure_ascii=False)

    print(f"Extracted: '{rec.title}' by {len(rec.authors)} authors ({rec.year})")
    print(f"DOI: {rec.doi} (COLING DOI successfully excluded)")
    print(f"Sections: {len(rec.sections)}, Figures: {len(rec.figures)}, Tables: {len(rec.tables)}, Equations: {len(rec.equations)}, Refs: {len(rec.references)}")
    print(f"SHA-256: {rec.sha256[:16]}..., Paper ID: {rec.paper_id}")
    print(f"Saved record to {out_path}")
    return rec


def run_path_b_latex_source() -> PaperRecord:
    print("\n" + "=" * 60)
    print("Path b: Real Paper with LaTeX Source (Attention Is All You Need - 1706.03762)")
    print("=" * 60)
    eprint_url = "https://arxiv.org/e-print/1706.03762"
    r = requests.get(eprint_url, headers={"User-Agent": "3PassResearchCorpus/1.0"}, timeout=20)
    r.raise_for_status()

    # Extract all .tex files into map for recursive input expansion
    tar = tarfile.open(fileobj=io.BytesIO(r.content))
    latex_files: Dict[str, str] = {}
    for member in tar.getmembers():
        if member.name.endswith(".tex"):
            f = tar.extractfile(member)
            if f:
                content = f.read().decode("utf-8", errors="replace")
                latex_files[member.name] = content
                latex_files[member.name.replace(".tex", "")] = content

    ms_tex = latex_files.get("ms.tex", "")
    assert ms_tex, "ms.tex not found in 1706.03762 e-print bundle"

    plugin = LatexSourcePlugin()
    paper_input = {
        "source_id": "SRC-REAL-B",
        "arxiv_id": "1706.03762",
        "latex_fixture": ms_tex,
        "latex_files": latex_files,
        "fixture_uri": eprint_url,
        "sha256": hashlib.sha256(r.content).hexdigest()
    }
    rec = plugin.extract(paper_input)

    # Assertions for Item 2
    assert "Attention Is All You Need" in rec.title, f"Title mismatch: {rec.title}"
    assert rec.arxiv_id == "1706.03762", f"Expected arxiv_id 1706.03762, got {rec.arxiv_id}"
    assert rec.year == 2017, f"Expected year 2017, got {rec.year}"
    assert len(rec.authors) >= 8, f"Expected authors, got {len(rec.authors)}"
    assert len(rec.figures) >= 1, f"Expected extracted figures, got {len(rec.figures)}"
    assert len(rec.tables) >= 1, f"Expected extracted tables, got {len(rec.tables)}"
    assert len(rec.equations) >= 1, f"Expected extracted equations, got {len(rec.equations)}"
    assert len(rec.references) >= 20, f"Expected extracted references, got {len(rec.references)}"
    assert rec.sha256 and len(rec.sha256) == 64, f"Invalid sha256: {rec.sha256}"
    assert rec.paper_id > 0, f"Invalid paper_id: {rec.paper_id}"

    out_path = os.path.join(REAL_DIR, "path_b_latex_source.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rec.to_dict(), f, indent=2, ensure_ascii=False)

    print(f"Extracted: '{rec.title}' by {len(rec.authors)} authors ({rec.year})")
    print(f"Sections: {len(rec.sections)}, Figures: {len(rec.figures)}, Tables: {len(rec.tables)}, Equations: {len(rec.equations)}, Refs: {len(rec.references)}")
    print(f"SHA-256: {rec.sha256[:16]}..., Paper ID: {rec.paper_id}")
    print(f"Saved record to {out_path}")
    return rec


def run_path_c_publisher_xml() -> PaperRecord:
    print("\n" + "=" * 60)
    print("Path c: Real Publisher JATS XML (PLOS ONE - 10.1371/journal.pone.0298888)")
    print("=" * 60)
    plugin = PublisherXmlPlugin()
    paper_input = {
        "source_id": "SRC-REAL-JATS",
        "doi": "10.1371/journal.pone.0298888"
    }
    rec = plugin.extract(paper_input)

    assert "Adversarial AI applied to cross-user inter-domain" in rec.title, f"Title mismatch: {rec.title}"
    assert len(rec.authors) >= 3, f"Expected 3 authors, got {len(rec.authors)}: {rec.authors}"
    assert rec.year == 2024, f"Expected year 2024, got {rec.year}"
    assert rec.completeness == "full", f"Expected completeness full, got {rec.completeness}"
    assert rec.provenance.path_used == "publisher_xml"
    assert rec.provenance.tool == "jats-xml"
    assert rec.provenance.tool_version == "JATS XML (Publisher)"
    assert len(rec.sections) >= 6, f"Expected >= 6 sections, got {len(rec.sections)}"
    assert len(rec.references) >= 40, f"Expected >= 40 references, got {len(rec.references)}"
    assert rec.sections[0].verified == "jats-verified"
    assert rec.references[0].verified == "jats-verified"
    assert rec.sha256 and len(rec.sha256) == 64
    assert rec.paper_id > 0

    out_path = os.path.join(REAL_DIR, "path_c_publisher_xml.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rec.to_dict(), f, indent=2, ensure_ascii=False)

    print(f"Extracted: '{rec.title[:60]}...' by {len(rec.authors)} authors ({rec.year})")
    print(f"Sections: {len(rec.sections)}, Figures: {len(rec.figures)}, Tables: {len(rec.tables)}, Equations: {len(rec.equations)}, Refs: {len(rec.references)}")
    print(f"Completeness: {rec.completeness}, Provenance: {rec.provenance.path_used} ({rec.provenance.tool})")
    print(f"SHA-256: {rec.sha256[:16]}..., Paper ID: {rec.paper_id}")
    print(f"Saved record to {out_path}")
    return rec


def run_path_d_grobid_pdf() -> Optional[PaperRecord]:
    print("\n" + "=" * 60)
    print("Path d: Real PDF-Only Paper via Live GROBID (PMLR 119 - SimCLR chen20j.pdf)")
    print("=" * 60)
    pdf_url = "https://proceedings.mlr.press/v119/chen20j/chen20j.pdf"
    local_pdf = os.path.join(REAL_DIR, "simclr_chen20j.pdf")
    if not os.path.exists(local_pdf):
        print(f"Downloading real open-access PDF from {pdf_url}...")
        r = requests.get(pdf_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        r.raise_for_status()
        with open(local_pdf, "wb") as f:
            f.write(r.content)
        print(f"Saved {os.path.getsize(local_pdf)} bytes to {local_pdf} (gitignored)")

    with open(local_pdf, "rb") as f:
        pdf_bytes = f.read()
    pdf_hash = hashlib.sha256(pdf_bytes).hexdigest()

    grobid_url = os.environ.get("GROBID_URL", "http://localhost:8070")
    plugin = GrobidPdfPlugin(grobid_url=grobid_url)

    # 1. Loud failure test on unreachable endpoint
    print("Verifying that offline GROBID fails loudly without silent fallback...")
    test_offline_plugin = GrobidPdfPlugin(grobid_url="http://127.0.0.1:59999")
    try:
        test_offline_plugin._call_grobid(local_pdf)
        raise AssertionError("Expected _call_grobid to fail loudly when offline!")
    except RuntimeError as e:
        print(f"Confirmed loud failure: {str(e)[:100]}...")

    # 2. Live GROBID check
    is_live = False
    try:
        r = requests.get(f"{grobid_url}/api/isalive", timeout=3)
        if r.status_code == 200:
            is_live = True
    except Exception:
        pass

    if not is_live:
        if os.environ.get("CI"):
            raise RuntimeError(f"GROBID service container at {grobid_url} is unreachable in CI environment!")
        print(f"Notice: GROBID service at {grobid_url} is offline locally.")
        print("Live GROBID extraction with /api/version verification executes in GitHub Actions CI (service container).")
        return None

    # 3. Live extraction via GROBID
    grobid_version = plugin.get_grobid_version()
    print(f"Live GROBID server detected. Version from /api/version: {grobid_version}")

    paper_input = {
        "source_id": "SRC-REAL-GROBID",
        "title": "A Simple Framework for Contrastive Learning of Visual Representations",
        "original_path": local_pdf,
        "drive_path": local_pdf,
        "sha256": pdf_hash
    }
    rec = plugin.extract(paper_input)

    print(f"Extracted from GROBID: '{rec.title}' by {len(rec.authors)} authors ({rec.year})")
    print(f"Sections: {len(rec.sections)}, Figures: {len(rec.figures)}, Tables: {len(rec.tables)}, Refs: {len(rec.references)}")

    # Assertions
    assert "Contrastive Learning" in rec.title or "Visual Representations" in rec.title or "SimCLR" in rec.title, f"Extracted title: {rec.title}"
    assert len(rec.authors) >= 2, f"Expected authors, got {len(rec.authors)}: {rec.authors}"
    assert len(rec.sections) >= 3, f"Expected sections, got {len(rec.sections)}"
    assert len(rec.references) >= 10, f"Expected references, got {len(rec.references)}"
    assert rec.completeness == "pdf-extracted"
    assert rec.provenance.path_used == "grobid_pdf"
    assert rec.provenance.tool == "grobid-tei"
    assert rec.provenance.tool_version.startswith("GROBID ")
    assert grobid_version in rec.provenance.tool_version
    assert rec.sections[0].verified == "pdf-extracted, verify"
    assert rec.references[0].verified == "pdf-extracted, verify"
    assert rec.sha256 == pdf_hash
    assert rec.paper_id > 0

    out_path = os.path.join(REAL_DIR, "path_c_grobid_pdf.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rec.to_dict(), f, indent=2, ensure_ascii=False)

    print(f"Extracted: '{rec.title}' by {len(rec.authors)} authors: {rec.authors}")
    print(f"Sections: {len(rec.sections)}, References: {len(rec.references)}")
    print(f"Completeness: {rec.completeness}, Provenance: {rec.provenance.path_used} ({rec.provenance.tool_version})")
    print(f"SHA-256: {rec.sha256[:16]}..., Paper ID: {rec.paper_id}")
    print(f"Saved record to {out_path}")
    return rec


def run_path_e_doi_metadata() -> PaperRecord:
    print("\n" + "=" * 60)
    print("Path e: Real DOI-Only Metadata (ACM SIGCSE - 10.1145/3491101.3503719)")
    print("=" * 60)
    plugin = CrossrefOpenAlexPlugin()
    paper_input = {
        "source_id": "SRC-REAL-D",
        "doi": "10.1145/3491101.3503719",
    }
    rec = plugin.extract(paper_input)

    # Assertions for metadata-only
    assert "GenAICHI" in rec.title, f"Expected GenAICHI in title, got {rec.title}"
    assert len(rec.authors) >= 1, f"Expected authors, got {len(rec.authors)}"
    assert rec.year == 2022, f"Expected year 2022, got {rec.year}"
    assert len(rec.sections) == 0, "Metadata-only must have no body sections"
    assert len(rec.references) >= 1, f"Expected references, got {len(rec.references)}"
    assert rec.completeness == "metadata-only"
    assert rec.sha256 and len(rec.sha256) == 64, f"Invalid sha256: {rec.sha256}"
    assert rec.paper_id > 0, f"Invalid paper_id: {rec.paper_id}"

    out_path = os.path.join(REAL_DIR, "path_d_doi_metadata.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rec.to_dict(), f, indent=2, ensure_ascii=False)

    print(f"Extracted: '{rec.title}' by {rec.authors} ({rec.year})")
    print(f"Sections: {len(rec.sections)} (must be 0 for metadata-only), Refs: {len(rec.references)}")
    print(f"Completeness: {rec.completeness}, Provenance: {rec.provenance.path_used}")
    print(f"SHA-256: {rec.sha256[:16]}..., Paper ID: {rec.paper_id}")
    print(f"Saved record to {out_path}")
    return rec


if __name__ == "__main__":
    r_a = run_path_a_arxiv_html()
    r_b = run_path_b_latex_source()
    r_c = run_path_c_publisher_xml()
    r_d = run_path_d_grobid_pdf()
    r_e = run_path_e_doi_metadata()
    print("\n" + "=" * 60)
    print("Real-paper smoke tests completed successfully!")
    print("=" * 60)
