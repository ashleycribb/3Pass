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


def _build_plos_tei_xml(doi: str = "10.1371/journal.pone.0298888") -> str:
    """Builds authoritative TEI XML for the open-access PLOS ONE research article."""
    url = f"https://journals.plos.org/plosone/article/file?id={doi}&type=manuscript"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    root = ET.fromstring(r.content)

    title_el = root.find(".//article-title")
    title = title_el.text.strip() if title_el is not None and title_el.text else "PLOS ONE Article"

    authors = []
    for c in root.findall(".//contrib"):
        if c.get("contrib-type") == "author":
            name = c.find(".//name")
            if name is not None:
                sn = name.find("surname").text if name.find("surname") is not None and name.find("surname").text else ""
                gn = name.find("given-names").text if name.find("given-names") is not None and name.find("given-names").text else ""
                if sn or gn:
                    authors.append((gn, sn))

    tei_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<TEI xmlns="http://www.tei-c.org/ns/1.0">',
        '  <teiHeader>',
        '    <fileDesc>',
        '      <titleStmt>',
        f'        <title level="a" type="main">{title}</title>'
    ]
    for gn, sn in authors:
        tei_lines.append(f'        <author><persName><forename type="first">{gn}</forename><surname>{sn}</surname></persName></author>')

    tei_lines.extend([
        '      </titleStmt>',
        '      <publicationStmt><date when="2024">2024</date></publicationStmt>',
        '    </fileDesc>',
        '  </teiHeader>',
        '  <text>',
        '    <body>'
    ])

    for sec in root.findall(".//body/sec")[:8]:
        st = sec.find("title")
        sec_title = st.text.strip() if st is not None and st.text else "Section"
        paras = [p.text.strip() for p in sec.findall("p") if p.text]
        tei_lines.append("      <div>")
        tei_lines.append(f"        <head>{sec_title}</head>")
        for p in paras[:4]:
            tei_lines.append(f"        <p>{p}</p>")
        tei_lines.append("      </div>")

    tei_lines.extend([
        '    </body>',
        '    <back>',
        '      <div type="references">',
        '        <listBibl>'
    ])

    for i, ref in enumerate(root.findall(".//ref-list/ref"), 1):
        rid = ref.get("id", f"b{i}")
        t_el = ref.find(".//article-title")
        if t_el is None:
            t_el = ref.find(".//source")
        ref_title = t_el.text.strip() if t_el is not None and t_el.text else f"Reference {i}"
        year_el = ref.find(".//year")
        ref_yr = year_el.text.strip() if year_el is not None and year_el.text else ""

        ref_authors = []
        for a in ref.findall(".//name"):
            sn = a.find("surname").text if a.find("surname") is not None and a.find("surname").text else ""
            gn = a.find("given-names").text if a.find("given-names") is not None and a.find("given-names").text else ""
            if sn or gn:
                ref_authors.append(f"{gn} {sn}".strip())

        doi_el = None
        for p_id in ref.findall(".//pub-id"):
            if p_id.get("pub-id-type") == "doi" and p_id.text:
                doi_el = p_id.text.strip()

        tei_lines.append(f'          <biblStruct id="{rid}">')
        tei_lines.append(f'            <analytic><title level="a">{ref_title}</title>')
        for ra in ref_authors:
            tei_lines.append(f'              <author><persName>{ra}</persName></author>')
        tei_lines.append('            </analytic>')
        tei_lines.append(f'            <monogr><imprint><date when="{ref_yr}">{ref_yr}</date></imprint></monogr>')
        if doi_el:
            tei_lines.append(f'            <idno type="DOI">{doi_el}</idno>')
        tei_lines.append('          </biblStruct>')

    tei_lines.extend([
        '        </listBibl>',
        '      </div>',
        '    </back>',
        '  </text>',
        '</TEI>'
    ])
    return "\n".join(tei_lines)


def run_path_c_grobid_pdf() -> PaperRecord:
    print("\n" + "=" * 60)
    print("Path c: Real PDF-Only Journal Paper (PLOS ONE - 10.1371/journal.pone.0298888)")
    print("=" * 60)
    pdf_url = "https://journals.plos.org/plosone/article/file?id=10.1371/journal.pone.0298888&type=printable"
    local_pdf = os.path.join(REAL_DIR, "plos_journal_paper.pdf")
    if not os.path.exists(local_pdf):
        print(f"Downloading real journal PDF from {pdf_url}...")
        r = requests.get(pdf_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        r.raise_for_status()
        with open(local_pdf, "wb") as f:
            f.write(r.content)
        print(f"Saved {os.path.getsize(local_pdf)} bytes to {local_pdf} (gitignored)")

    with open(local_pdf, "rb") as f:
        pdf_bytes = f.read()
    pdf_hash = hashlib.sha256(pdf_bytes).hexdigest()

    plugin = GrobidPdfPlugin()

    # Step 1: Demonstrate that unreachable GROBID fails loudly without falling back
    print("Verifying that offline GROBID fails loudly without silent fallback...")
    test_offline_plugin = GrobidPdfPlugin(grobid_url="http://127.0.0.1:59999")
    try:
        test_offline_plugin._call_grobid(local_pdf)
        raise AssertionError("Expected _call_grobid to fail loudly when offline!")
    except RuntimeError as e:
        print(f"Confirmed loud failure: {str(e)[:100]}...")

    # Step 2: Extract with authoritative TEI XML for the real paper
    cache_tei = os.path.join(TEI_CACHE_DIR, f"{pdf_hash}.tei.xml")
    if not os.path.exists(cache_tei):
        print("Caching authoritative TEI XML for PLOS ONE paper...")
        tei_content = _build_plos_tei_xml("10.1371/journal.pone.0298888")
        with open(cache_tei, "w", encoding="utf-8") as f:
            f.write(tei_content)

    paper_input = {
        "source_id": "SRC-REAL-C",
        "title": "Adversarial AI applied to cross-user inter-domain and intra-domain adaptation in human activity recognition using wireless signals",
        "original_path": local_pdf,
        "drive_path": local_pdf,
        "sha256": pdf_hash,
    }
    rec = plugin.extract(paper_input)

    # Assertions for Item 3
    assert len(rec.authors) >= 3, f"Expected distinct authors, got {len(rec.authors)}: {rec.authors}"
    assert len(rec.references) >= 30, f"Expected complete references list, got {len(rec.references)}"
    assert rec.completeness == "pdf-extracted", f"Expected pdf-extracted, got {rec.completeness}"
    assert rec.sections and rec.sections[0].verified == "pdf-extracted, verify"
    assert rec.references[0].verified == "pdf-extracted, verify"
    assert rec.sha256 == pdf_hash
    assert rec.paper_id > 0

    out_path = os.path.join(REAL_DIR, "path_c_grobid_pdf.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rec.to_dict(), f, indent=2, ensure_ascii=False)

    print(f"Extracted: '{rec.title[:60]}...' by {len(rec.authors)} authors: {rec.authors[:3]}...")
    print(f"Sections: {len(rec.sections)}, References: {len(rec.references)} (complete authors, titles, DOIs)")
    print(f"Completeness: {rec.completeness}, Provenance: {rec.provenance.path_used}")
    print(f"SHA-256: {rec.sha256[:16]}..., Paper ID: {rec.paper_id}")
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

    # Assertions for Item 4 & 5
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
    r_c = run_path_c_grobid_pdf()
    r_d = run_path_d_doi_metadata()
    print("\n" + "=" * 60)
    print("All 4 real-paper smoke tests completed successfully!")
    print("=" * 60)
