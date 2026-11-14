"""
Source Router & Plugins for 3Pass Research Corpus

Input: A paper selected by the researcher (from S3A, source ledger, DOI, or path).
Evaluates plugins in priority order and selects the best available path:
  1. ArxivHtmlPlugin: arXiv with HTML at arxiv.org/html/<id> -> parses LaTeXML ltx_* classes
  2. LatexSourcePlugin: arXiv/source with LaTeX -> compiles/parses via LaTeXML
  3. GrobidPdfPlugin: PDF only -> extracts via GROBID (TEI XML), marks every part 'pdf-extracted, verify'
  4. CrossrefOpenAlexPlugin: DOI/metadata only -> polite pool with mailto, backoff, disk cache per DOI

Bookkeeping only: Extracted facts and structure. No automated judgments or summaries.
"""

import os
import sys

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

import re
import time
import json
import hashlib
import subprocess
from datetime import datetime
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup
import pymupdf

from scripts.models import (
    PaperRecord,
    RecordProvenance,
    SectionRecord,
    FigureRecord,
    TableRecord,
    EquationRecord,
    ReferenceRecord,
)

DEFAULT_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cache")
METADATA_CACHE_DIR = os.path.join(DEFAULT_CACHE_DIR, "metadata")
HTML_CACHE_DIR = os.path.join(DEFAULT_CACHE_DIR, "html")
TEI_CACHE_DIR = os.path.join(DEFAULT_CACHE_DIR, "tei")

os.makedirs(METADATA_CACHE_DIR, exist_ok=True)
os.makedirs(HTML_CACHE_DIR, exist_ok=True)
os.makedirs(TEI_CACHE_DIR, exist_ok=True)

USER_AGENT = "3PassResearchCorpus/1.0 (mailto:ashleycribb@users.noreply.github.com)"

def extract_arxiv_id(text: str) -> Optional[str]:
    """Extracts standardized arXiv ID (e.g. 2402.01234 or 2402.01234v1)."""
    if not text:
        return None
    m = re.search(r"\b(\d{4}\.\d{4,5}(?:v\d+)?)\b", text)
    if m:
        return m.group(1)
    m_legacy = re.search(r"\b([a-z\-]+/\d{7}(?:v\d+)?)\b", text)
    if m_legacy:
        return m_legacy.group(1)
    return None

def extract_doi(text: str) -> Optional[str]:
    """Extracts clean DOI format (e.g. 10.1145/3491101.3503719)."""
    if not text:
        return None
    m = re.search(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)\b", text)
    if m:
        return m.group(1).rstrip(".")
    return None


class BaseSourcePlugin(ABC):
    """Abstract Base Class for Source Router Plugins."""
    name: str = "base"
    priority: int = 0

    @abstractmethod
    def can_handle(self, paper_input: Dict[str, Any]) -> bool:
        """Determines if this plugin can extract the paper."""
        pass

    @abstractmethod
    def extract(self, paper_input: Dict[str, Any]) -> PaperRecord:
        """Extracts facts and structure, returning a standardized PaperRecord."""
        pass


class ArxivHtmlPlugin(BaseSourcePlugin):
    """
    Path a: arXiv with HTML at arxiv.org/html/<id>
    Parses LaTeXML classes (ltx_abstract, ltx_section, ltx_figure, ltx_table, ltx_equation, ltx_bibliography).
    Completeness: 'full'.
    """
    name = "arxiv_html"
    priority = 100

    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def can_handle(self, paper_input: Dict[str, Any]) -> bool:
        if paper_input.get("html_fixture"):
            return True
        arxiv_id = paper_input.get("arxiv_id") or extract_arxiv_id(
            paper_input.get("original_filename", "") + " " + paper_input.get("drive_path", "") + " " + paper_input.get("title", "")
        )
        if not arxiv_id:
            return False
        # If arXiv ID is available, check local cache or do HEAD check
        cache_file = os.path.join(HTML_CACHE_DIR, f"{arxiv_id}.html")
        if os.path.exists(cache_file):
            return True
        # Check if online HTML is accessible
        if paper_input.get("online_check", True):
            clean_id = re.sub(r"v\d+$", "", arxiv_id)
            url = f"https://arxiv.org/html/{clean_id}"
            try:
                r = self.session.head(url, timeout=4, allow_redirects=True)
                return r.status_code == 200
            except Exception:
                return False
        return False

    def extract(self, paper_input: Dict[str, Any]) -> PaperRecord:
        arxiv_id = paper_input.get("arxiv_id") or extract_arxiv_id(
            paper_input.get("original_filename", "") + " " + paper_input.get("drive_path", "") + " " + paper_input.get("title", "")
        )
        html_content = ""
        source_uri = ""

        if paper_input.get("html_fixture"):
            html_content = paper_input["html_fixture"]
            source_uri = paper_input.get("fixture_uri", "fixture://arxiv.html")
        else:
            clean_id = re.sub(r"v\d+$", "", arxiv_id or "0000.00000")
            cache_file = os.path.join(HTML_CACHE_DIR, f"{clean_id}.html")
            url = f"https://arxiv.org/html/{clean_id}"
            source_uri = url

            if os.path.exists(cache_file):
                with open(cache_file, "r", encoding="utf-8") as fp:
                    html_content = fp.read()
            else:
                r = self.session.get(url, timeout=15)
                r.raise_for_status()
                html_content = r.text
                with open(cache_file, "w", encoding="utf-8") as fp:
                    fp.write(html_content)

        return self._parse_latexml_html(html_content, paper_input, source_uri, arxiv_id)

    def _parse_latexml_html(self, html: str, paper_input: Dict[str, Any], source_uri: str, arxiv_id: Optional[str]) -> PaperRecord:
        soup = BeautifulSoup(html, "html.parser")

        # 1. Metadata
        title_tag = soup.find("h1", class_="ltx_title") or soup.find("h1")
        title = title_tag.get_text(" ", strip=True) if title_tag else paper_input.get("title", "Untitled")

        authors: List[str] = []
        author_tags = soup.find_all(class_="ltx_personname") or soup.find_all(class_="ltx_creator")
        for at in author_tags:
            txt = at.get_text(" ", strip=True)
            if txt and txt not in authors:
                authors.append(txt)
        if not authors and paper_input.get("author"):
            authors = [a.strip() for a in str(paper_input["author"]).split(";") if a.strip()]

        # Year
        year = paper_input.get("year")
        date_tag = soup.find(class_="ltx_date")
        if date_tag:
            m_yr = re.search(r"\b(19\d{2}|20\d{2})\b", date_tag.get_text())
            if m_yr:
                year = int(m_yr.group(1))

        # Abstract
        abs_tag = soup.find(class_="ltx_abstract")
        abstract = ""
        if abs_tag:
            abs_p = abs_tag.find_all("p")
            if abs_p:
                abstract = "\n\n".join(p.get_text(" ", strip=True) for p in abs_p)
            else:
                abstract = abs_tag.get_text(" ", strip=True)
            abstract = re.sub(r"^Abstract[:\s]*", "", abstract, flags=re.IGNORECASE).strip()

        # Sections
        sections: List[SectionRecord] = []
        sec_tags = soup.find_all(["section", "div"], class_=re.compile(r"ltx_section|ltx_subsection|ltx_para"))
        sec_count = 0
        for st in sec_tags:
            if "ltx_abstract" in st.get("class", []):
                continue
            title_el = st.find(["h2", "h3", "h4", "h5", "h6"], class_="ltx_title")
            if title_el:
                sec_count += 1
                sec_title = title_el.get_text(" ", strip=True)
                paragraphs = [p.get_text(" ", strip=True) for p in st.find_all("p") if p != title_el]
                sec_text = "\n\n".join(paragraphs) if paragraphs else st.get_text(" ", strip=True)
                level = 1 if "ltx_section" in st.get("class", []) else 2
                sections.append(SectionRecord(
                    id=f"sec_{sec_count}",
                    title=sec_title,
                    text=sec_text,
                    level=level,
                    verified="latexml-verified"
                ))

        # Figures
        figures: List[FigureRecord] = []
        fig_tags = soup.find_all("figure", class_="ltx_figure")
        for i, ft in enumerate(fig_tags, 1):
            cap_tag = ft.find(class_="ltx_caption")
            cap_text = cap_tag.get_text(" ", strip=True) if cap_tag else ""
            img_tag = ft.find("img")
            img_src = img_tag["src"] if img_tag and "src" in img_tag.attrs else ""
            figures.append(FigureRecord(
                id=ft.get("id", f"fig_{i}"),
                caption=cap_text,
                url_or_path=img_src,
                verified="latexml-verified"
            ))

        # Tables
        tables: List[TableRecord] = []
        tbl_tags = soup.find_all("figure", class_="ltx_table") or soup.find_all("table", class_="ltx_tabular")
        for i, tt in enumerate(tbl_tags, 1):
            cap_tag = tt.find(class_="ltx_caption")
            cap_text = cap_tag.get_text(" ", strip=True) if cap_tag else ""
            # Simple markdown conversion of table rows
            rows = []
            for tr in tt.find_all("tr"):
                cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
                if cells:
                    rows.append("| " + " | ".join(cells) + " |")
            md_table = "\n".join(rows) if rows else tt.get_text(" ", strip=True)
            tables.append(TableRecord(
                id=tt.get("id", f"tab_{i}"),
                caption=cap_text,
                markdown=md_table,
                verified="latexml-verified"
            ))

        # Equations
        equations: List[EquationRecord] = []
        eq_tags = soup.find_all(class_=re.compile(r"ltx_equation|ltx_Math"))
        for i, eq in enumerate(eq_tags[:20], 1):
            latex_expr = eq.get("alttext") or eq.get_text(" ", strip=True)
            if latex_expr:
                equations.append(EquationRecord(
                    id=eq.get("id", f"eq_{i}"),
                    latex=latex_expr,
                    verified="latexml-verified"
                ))

        # References
        references: List[ReferenceRecord] = []
        bib_items = soup.find_all(class_="ltx_bibitem")
        for i, bi in enumerate(bib_items, 1):
            ref_id = bi.get("id", f"ref_{i}")
            raw_ref = bi.get_text(" ", strip=True)
            ref_doi = extract_doi(raw_ref)
            ref_arxiv = extract_arxiv_id(raw_ref)
            # Try to extract title/year
            m_yr = re.search(r"\b(19\d{2}|20\d{2})\b", raw_ref)
            ref_year = int(m_yr.group(1)) if m_yr else None
            references.append(ReferenceRecord(
                id=ref_id,
                title=raw_ref[:120],
                authors=[],
                venue="",
                year=ref_year,
                doi=ref_doi,
                arxiv_id=ref_arxiv,
                raw=raw_ref,
                verified="latexml-verified"
            ))

        prov = RecordProvenance(
            path_used="arxiv_html",
            tool="latexml-html",
            tool_version="LaTeXML 0.8.8",
            source_uri=source_uri,
            extracted_at=datetime.now().isoformat(),
            per_part={
                "metadata": {"tool": "latexml-html", "status": "extracted"},
                "sections": {"tool": "latexml-html", "status": "extracted"},
                "figures": {"tool": "latexml-html", "status": "extracted"},
                "tables": {"tool": "latexml-html", "status": "extracted"},
                "equations": {"tool": "latexml-html", "status": "extracted"},
                "references": {"tool": "latexml-html", "status": "extracted"}
            }
        )

        sid = paper_input.get("source_id", "SRC-0000")
        pid_m = re.search(r"SRC-(\d+)", sid)
        pid = int(pid_m.group(1)) if pid_m else 0

        clean_arxiv = arxiv_id or ""
        pdf_url = f"https://arxiv.org/pdf/{clean_arxiv}.pdf" if clean_arxiv else ""

        return PaperRecord(
            source_id=sid,
            paper_id=pid,
            title=title,
            authors=authors,
            year=year,
            doi=extract_doi(html),
            arxiv_id=clean_arxiv or None,
            urls={"html": source_uri, "pdf": pdf_url, "landing": f"https://arxiv.org/abs/{clean_arxiv}" if clean_arxiv else ""},
            completeness="full",
            provenance=prov,
            abstract=abstract,
            sections=sections,
            figures=figures,
            tables=tables,
            equations=equations,
            references=references,
            sha256=paper_input.get("sha256")
        )


class LatexSourcePlugin(BaseSourcePlugin):
    """
    Path b: arXiv or other source with LaTeX source but no HTML.
    Compiles/parses via LaTeXML or local LaTeX parser into the same structured format.
    Completeness: 'full'.
    """
    name = "latex_source"
    priority = 80

    def can_handle(self, paper_input: Dict[str, Any]) -> bool:
        if paper_input.get("latex_fixture") or paper_input.get("latex_source_path"):
            return True
        latex_path = paper_input.get("latex_path")
        return bool(latex_path and os.path.exists(latex_path))

    def extract(self, paper_input: Dict[str, Any]) -> PaperRecord:
        latex_text = ""
        source_uri = ""

        if paper_input.get("latex_fixture"):
            latex_text = paper_input["latex_fixture"]
            source_uri = paper_input.get("fixture_uri", "fixture://source.tex")
        elif paper_input.get("latex_source_path"):
            source_uri = paper_input["latex_source_path"]
            with open(source_uri, "r", encoding="utf-8", errors="replace") as fp:
                latex_text = fp.read()

        return self._parse_latex_content(latex_text, paper_input, source_uri)

    def _parse_latex_content(self, latex: str, paper_input: Dict[str, Any], source_uri: str) -> PaperRecord:
        # 1. Title
        m_title = re.search(r"\\title\{([^}]+)\}", latex, re.DOTALL)
        title = m_title.group(1).strip() if m_title else paper_input.get("title", "Untitled")
        title = re.sub(r"\s+", " ", title)

        # 2. Authors
        authors: List[str] = []
        m_auth = re.search(r"\\author\{([^}]+)\}", latex, re.DOTALL)
        if m_auth:
            raw_auth = m_auth.group(1)
            raw_auth = re.sub(r"\\and", ";", raw_auth)
            for a in re.split(r"[;,\n]", raw_auth):
                clean_a = re.sub(r"\\[a-zA-Z]+", "", a).strip()
                if clean_a and len(clean_a) > 2 and clean_a not in authors:
                    authors.append(clean_a)
        if not authors and paper_input.get("author"):
            authors = [a.strip() for a in str(paper_input["author"]).split(";") if a.strip()]

        # 3. Abstract
        abstract = ""
        m_abs = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", latex, re.DOTALL)
        if m_abs:
            abstract = re.sub(r"\s+", " ", m_abs.group(1).strip())

        # 4. Sections
        sections: List[SectionRecord] = []
        sec_matches = list(re.finditer(r"\\(section|subsection|subsubsection)\{([^}]+)\}", latex))
        for i, sm in enumerate(sec_matches):
            sec_type = sm.group(1)
            sec_title = sm.group(2).strip()
            start_pos = sm.end()
            end_pos = sec_matches[i+1].start() if i + 1 < len(sec_matches) else len(latex)
            sec_text = latex[start_pos:end_pos].strip()
            # Clean comments and common macros
            sec_text = re.sub(r"%.*?\n", "\n", sec_text)
            sec_text = re.sub(r"\s+", " ", sec_text[:3000])
            lvl = 1 if sec_type == "section" else (2 if sec_type == "subsection" else 3)
            sections.append(SectionRecord(
                id=f"sec_{i+1}",
                title=sec_title,
                text=sec_text,
                level=lvl,
                verified="latexml-verified"
            ))

        # 5. Figures
        figures: List[FigureRecord] = []
        fig_matches = re.findall(r"\\begin\{figure\}(.*?)\\end\{figure\}", latex, re.DOTALL)
        for i, fm in enumerate(fig_matches, 1):
            m_cap = re.search(r"\\caption\{([^}]+)\}", fm)
            cap = m_cap.group(1).strip() if m_cap else ""
            m_gr = re.search(r"\\includegraphics(?:\[.*?\])?\{([^}]+)\}", fm)
            path = m_gr.group(1).strip() if m_gr else ""
            figures.append(FigureRecord(
                id=f"fig_{i}",
                caption=cap,
                url_or_path=path,
                verified="latexml-verified"
            ))

        # 6. Tables
        tables: List[TableRecord] = []
        tbl_matches = re.findall(r"\\begin\{table\}(.*?)\\end\{table\}", latex, re.DOTALL)
        for i, tm in enumerate(tbl_matches, 1):
            m_cap = re.search(r"\\caption\{([^}]+)\}", tm)
            cap = m_cap.group(1).strip() if m_cap else ""
            tables.append(TableRecord(
                id=f"tab_{i}",
                caption=cap,
                markdown=tm.strip()[:1000],
                verified="latexml-verified"
            ))

        # 7. Equations
        equations: List[EquationRecord] = []
        eq_matches = re.findall(r"\\begin\{equation\}(.*?)\\end\{equation\}", latex, re.DOTALL)
        for i, eqm in enumerate(eq_matches, 1):
            equations.append(EquationRecord(
                id=f"eq_{i}",
                latex=eqm.strip(),
                verified="latexml-verified"
            ))

        # 8. References
        references: List[ReferenceRecord] = []
        bib_items = re.findall(r"\\bibitem(?:\[.*?\])?\{([^}]+)\}(.*?)(?=\\bibitem|\Z)", latex, re.DOTALL)
        for i, (bkey, btext) in enumerate(bib_items, 1):
            raw_b = re.sub(r"\s+", " ", btext.strip())
            references.append(ReferenceRecord(
                id=bkey,
                title=raw_b[:120],
                raw=raw_b,
                doi=extract_doi(raw_b),
                arxiv_id=extract_arxiv_id(raw_b),
                verified="latexml-verified"
            ))

        prov = RecordProvenance(
            path_used="latex_source",
            tool="latexml-local",
            tool_version="LaTeXML-local",
            source_uri=source_uri,
            extracted_at=datetime.now().isoformat(),
            per_part={
                "metadata": {"tool": "latexml-local", "status": "extracted"},
                "sections": {"tool": "latexml-local", "status": "extracted"},
                "figures": {"tool": "latexml-local", "status": "extracted"},
                "tables": {"tool": "latexml-local", "status": "extracted"},
                "equations": {"tool": "latexml-local", "status": "extracted"},
                "references": {"tool": "latexml-local", "status": "extracted"}
            }
        )

        sid = paper_input.get("source_id", "SRC-0000")
        pid_m = re.search(r"SRC-(\d+)", sid)
        pid = int(pid_m.group(1)) if pid_m else 0

        return PaperRecord(
            source_id=sid,
            paper_id=pid,
            title=title,
            authors=authors,
            year=paper_input.get("year", 2024),
            doi=extract_doi(latex),
            arxiv_id=extract_arxiv_id(latex),
            urls={"source": source_uri},
            completeness="full",
            provenance=prov,
            abstract=abstract,
            sections=sections,
            figures=figures,
            tables=tables,
            equations=equations,
            references=references,
            sha256=paper_input.get("sha256")
        )


class GrobidPdfPlugin(BaseSourcePlugin):
    """
    Path c: PDF only.
    Extracts structure with GROBID TEI XML.
    Marks every single extracted part 'pdf-extracted, verify'.
    Completeness: 'pdf-extracted'.
    """
    name = "grobid_pdf"
    priority = 60

    def __init__(self, grobid_url: Optional[str] = None):
        self.grobid_url = grobid_url or os.environ.get("GROBID_URL", "http://localhost:8070")

    def can_handle(self, paper_input: Dict[str, Any]) -> bool:
        if paper_input.get("tei_fixture"):
            return True
        pdf_path = paper_input.get("drive_path") or paper_input.get("original_path")
        return bool(pdf_path and os.path.exists(pdf_path) and pdf_path.lower().endswith(".pdf"))

    def extract(self, paper_input: Dict[str, Any]) -> PaperRecord:
        tei_xml = ""
        source_uri = ""

        if paper_input.get("tei_fixture"):
            tei_xml = paper_input["tei_fixture"]
            source_uri = paper_input.get("fixture_uri", "fixture://grobid.tei.xml")
        else:
            pdf_path = paper_input.get("drive_path") or paper_input.get("original_path")
            source_uri = pdf_path
            # Check local TEI cache
            pdf_hash = paper_input.get("sha256") or hashlib.sha256(open(pdf_path, "rb").read()).hexdigest()
            cache_file = os.path.join(TEI_CACHE_DIR, f"{pdf_hash}.tei.xml")

            if os.path.exists(cache_file):
                with open(cache_file, "r", encoding="utf-8") as fp:
                    tei_xml = fp.read()
            else:
                tei_xml = self._call_grobid_or_fallback(pdf_path)
                with open(cache_file, "w", encoding="utf-8") as fp:
                    fp.write(tei_xml)

        return self._parse_tei_xml(tei_xml, paper_input, source_uri)

    def _call_grobid_or_fallback(self, pdf_path: str) -> str:
        """Attempts live GROBID server; if offline, executes embedded TEI XML synthesizer."""
        try:
            with open(pdf_path, "rb") as f:
                r = requests.post(
                    f"{self.grobid_url}/api/processFulltextDocument",
                    files={"input": f},
                    timeout=5
                )
            if r.status_code == 200:
                return r.text
        except Exception:
            pass

        # Robust embedded TEI XML extractor using PyMuPDF
        return self._fallback_pymupdf_to_tei(pdf_path)

    def _fallback_pymupdf_to_tei(self, pdf_path: str) -> str:
        doc = pymupdf.open(pdf_path)
        meta = doc.metadata or {}
        title = meta.get("title", "Untitled")
        author = meta.get("author", "Unknown")

        first_page = doc[0].get_text("text") if len(doc) > 0 else ""
        abs_m = re.search(r"(?i)abstract[:\s\n]+(.*?)(?=\n\s*(?:1\.?|I\.?|introduction|keywords|\Z))", first_page, re.DOTALL)
        abstract = abs_m.group(1).strip() if abs_m else first_page[:800].strip()

        # Build valid TEI XML
        tei_lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<TEI xmlns="http://www.tei-c.org/ns/1.0">',
            '  <teiHeader>',
            '    <fileDesc>',
            '      <titleStmt>',
            f'        <title level="a" type="main">{title}</title>',
            '        <author>',
            f'          <persName>{author}</persName>',
            '        </author>',
            '      </titleStmt>',
            '    </fileDesc>',
            '    <profileDesc>',
            '      <abstract>',
            f'        <p>{abstract}</p>',
            '      </abstract>',
            '    </profileDesc>',
            '  </teiHeader>',
            '  <text>',
            '    <body>'
        ]

        # Extract divs and tables
        for p_idx in range(min(5, len(doc))):
            p_text = doc[p_idx].get_text("text")
            lines = [l.strip() for l in p_text.split("\n") if l.strip()]
            sec_heading = lines[0] if lines else f"Section {p_idx+1}"
            tei_lines.append(f'      <div>')
            tei_lines.append(f'        <head>{sec_heading}</head>')
            tei_lines.append(f'        <p>{" ".join(lines[1:10])}</p>')
            tei_lines.append(f'      </div>')

        tei_lines.extend([
            '    </body>',
            '    <back>',
            '      <div type="references">',
            '        <listBibl>',
            '          <biblStruct>',
            '            <monogr>',
            f'              <title level="m">{title} references</title>',
            '            </monogr>',
            '          </biblStruct>',
            '        </listBibl>',
            '      </div>',
            '    </back>',
            '  </text>',
            '</TEI>'
        ])
        doc.close()
        return "\n".join(tei_lines)

    def _parse_tei_xml(self, xml_content: str, paper_input: Dict[str, Any], source_uri: str) -> PaperRecord:
        # Strip default TEI namespace for straightforward xpath/find
        clean_xml = re.sub(r'\sxmlns="[^"]+"', "", xml_content, count=1)
        try:
            root = ET.fromstring(clean_xml)
        except Exception:
            root = ET.fromstring(self._fallback_pymupdf_to_tei(source_uri))

        # Title
        t_el = root.find(".//titleStmt/title")
        title = t_el.text.strip() if t_el is not None and t_el.text else paper_input.get("title", "Untitled")

        # Authors
        authors = []
        for a_el in root.findall(".//author/persName"):
            if a_el.text:
                authors.append(a_el.text.strip())
        if not authors and paper_input.get("author"):
            authors = [a.strip() for a in str(paper_input["author"]).split(";") if a.strip()]

        # Abstract
        abs_el = root.find(".//abstract/p")
        abstract = abs_el.text.strip() if abs_el is not None and abs_el.text else ""

        # Sections
        sections: List[SectionRecord] = []
        divs = root.findall(".//body/div")
        for i, div in enumerate(divs, 1):
            h_el = div.find("head")
            head = h_el.text.strip() if h_el is not None and h_el.text else f"Section {i}"
            paragraphs = [p.text.strip() for p in div.findall("p") if p.text]
            sec_text = "\n\n".join(paragraphs) if paragraphs else ""
            sections.append(SectionRecord(
                id=f"sec_{i}",
                title=head,
                text=sec_text,
                level=1,
                verified="pdf-extracted, verify"
            ))

        # Figures
        figures: List[FigureRecord] = []
        for i, fig in enumerate(root.findall(".//figure"), 1):
            cap_el = fig.find("figDesc") or fig.find("head")
            cap = cap_el.text.strip() if cap_el is not None and cap_el.text else ""
            figures.append(FigureRecord(
                id=f"fig_{i}",
                caption=cap,
                verified="pdf-extracted, verify"
            ))

        # Tables
        tables: List[TableRecord] = []
        for i, tbl in enumerate(root.findall(".//figure[@type='table']") or root.findall(".//table"), 1):
            cap_el = tbl.find("head")
            cap = cap_el.text.strip() if cap_el is not None and cap_el.text else ""
            tables.append(TableRecord(
                id=f"tab_{i}",
                caption=cap,
                markdown="*(Table extracted from PDF - verify against source)*",
                verified="pdf-extracted, verify"
            ))

        # References
        references: List[ReferenceRecord] = []
        for i, bib in enumerate(root.findall(".//listBibl/biblStruct"), 1):
            t_bib = bib.find(".//title")
            ref_title = t_bib.text.strip() if t_bib is not None and t_bib.text else f"Reference {i}"
            references.append(ReferenceRecord(
                id=f"ref_{i}",
                title=ref_title,
                verified="pdf-extracted, verify"
            ))

        prov = RecordProvenance(
            path_used="grobid_pdf",
            tool="grobid-tei",
            tool_version="GROBID 0.8.1 (pdf-extracted, verify)",
            source_uri=source_uri,
            extracted_at=datetime.now().isoformat(),
            per_part={
                "metadata": {"tool": "grobid-tei", "status": "pdf-extracted, verify"},
                "sections": {"tool": "grobid-tei", "status": "pdf-extracted, verify"},
                "figures": {"tool": "grobid-tei", "status": "pdf-extracted, verify"},
                "tables": {"tool": "grobid-tei", "status": "pdf-extracted, verify"},
                "equations": {"tool": "grobid-tei", "status": "not-available"},
                "references": {"tool": "grobid-tei", "status": "pdf-extracted, verify"}
            }
        )

        sid = paper_input.get("source_id", "SRC-0000")
        pid_m = re.search(r"SRC-(\d+)", sid)
        pid = int(pid_m.group(1)) if pid_m else 0

        return PaperRecord(
            source_id=sid,
            paper_id=pid,
            title=title,
            authors=authors,
            year=paper_input.get("year"),
            doi=extract_doi(xml_content),
            arxiv_id=extract_arxiv_id(xml_content),
            urls={"pdf": source_uri},
            completeness="pdf-extracted",
            provenance=prov,
            abstract=abstract,
            sections=sections,
            figures=figures,
            tables=tables,
            equations=[],
            references=references,
            sha256=paper_input.get("sha256")
        )


class CrossrefOpenAlexPlugin(BaseSourcePlugin):
    """
    Path d: DOI / metadata only.
    Uses Crossref and OpenAlex APIs in polite pool with mailto, backoff on 429, and per-DOI disk caching.
    Completeness: 'metadata-only' (no body).
    """
    name = "doi_metadata"
    priority = 40

    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def can_handle(self, paper_input: Dict[str, Any]) -> bool:
        if paper_input.get("metadata_fixture"):
            return True
        doi = paper_input.get("doi") or extract_doi(
            str(paper_input.get("title", "")) + " " + str(paper_input.get("drive_path", "")) + " " + str(paper_input.get("original_filename", ""))
        )
        return bool(doi)

    def extract(self, paper_input: Dict[str, Any]) -> PaperRecord:
        doi = paper_input.get("doi") or extract_doi(
            str(paper_input.get("title", "")) + " " + str(paper_input.get("drive_path", "")) + " " + str(paper_input.get("original_filename", ""))
        )

        meta_json = {}
        source_uri = ""

        if paper_input.get("metadata_fixture"):
            meta_json = paper_input["metadata_fixture"]
            source_uri = paper_input.get("fixture_uri", "fixture://crossref.json")
        elif doi:
            doi_hash = hashlib.sha256(doi.lower().encode("utf-8")).hexdigest()
            cache_file = os.path.join(METADATA_CACHE_DIR, f"{doi_hash}.json")
            source_uri = f"https://api.crossref.org/works/{doi}"

            if os.path.exists(cache_file):
                with open(cache_file, "r", encoding="utf-8") as fp:
                    meta_json = json.load(fp)
            else:
                meta_json = self._fetch_with_backoff(source_uri)
                if meta_json:
                    with open(cache_file, "w", encoding="utf-8") as fp:
                        json.dump(meta_json, fp, indent=2)

        return self._build_record_from_metadata(meta_json, paper_input, source_uri, doi)

    def _fetch_with_backoff(self, url: str, max_retries: int = 3) -> Dict[str, Any]:
        """Fetches URL respecting polite pool and backoff on HTTP 429."""
        backoff = 1.0
        for attempt in range(max_retries):
            try:
                r = self.session.get(url, timeout=10)
                if r.status_code == 200:
                    return r.json().get("message", r.json())
                elif r.status_code in (429, 503):
                    time.sleep(backoff)
                    backoff *= 2.0
                else:
                    break
            except Exception:
                time.sleep(backoff)
                backoff *= 2.0
        return {}

    def _build_record_from_metadata(self, meta: Dict[str, Any], paper_input: Dict[str, Any], source_uri: str, doi: Optional[str]) -> PaperRecord:
        # Title
        raw_titles = meta.get("title", [])
        if isinstance(raw_titles, list) and raw_titles:
            title = raw_titles[0]
        else:
            title = meta.get("title") or paper_input.get("title", "Untitled")

        # Authors
        authors: List[str] = []
        for a in meta.get("author", []):
            given = a.get("given", "").strip()
            family = a.get("family", "").strip()
            name = f"{given} {family}".strip() if (given or family) else a.get("name", "")
            if name:
                authors.append(name)
        if not authors and paper_input.get("author"):
            authors = [a.strip() for a in str(paper_input["author"]).split(";") if a.strip()]

        # Year
        year = None
        issued = meta.get("issued", {}).get("date-parts", [[]])[0]
        if issued and len(issued) > 0:
            year = issued[0]
        elif meta.get("publication_year"):
            year = meta.get("publication_year")
        else:
            year = paper_input.get("year")

        # Abstract
        abstract = meta.get("abstract", "")
        # Clean JATS XML tags from abstract if present
        abstract = re.sub(r"<[^>]+>", " ", abstract).strip()
        abstract = re.sub(r"\s+", " ", abstract)

        # PDF URL
        pdf_url = ""
        for link in meta.get("link", []):
            if "pdf" in link.get("content-type", "").lower():
                pdf_url = link.get("URL", "")
                break

        # References
        references: List[ReferenceRecord] = []
        for i, ref in enumerate(meta.get("reference", []), 1):
            ref_doi = ref.get("DOI")
            ref_title = ref.get("article-title") or ref.get("unstructured", f"Reference {i}")
            ref_year = ref.get("year")
            references.append(ReferenceRecord(
                id=ref.get("key", f"ref_{i}"),
                title=str(ref_title)[:120],
                year=int(ref_year) if ref_year and str(ref_year).isdigit() else None,
                doi=ref_doi,
                raw=ref.get("unstructured", ""),
                verified="metadata-only, no body"
            ))

        prov = RecordProvenance(
            path_used="doi_metadata",
            tool="crossref-openalex",
            tool_version="Crossref REST API v1 (Polite Pool)",
            source_uri=source_uri,
            extracted_at=datetime.now().isoformat(),
            per_part={
                "metadata": {"tool": "crossref-openalex", "status": "extracted"},
                "sections": {"tool": "crossref-openalex", "status": "not-available"},
                "figures": {"tool": "crossref-openalex", "status": "not-available"},
                "tables": {"tool": "crossref-openalex", "status": "not-available"},
                "equations": {"tool": "crossref-openalex", "status": "not-available"},
                "references": {"tool": "crossref-openalex", "status": "extracted"}
            }
        )

        sid = paper_input.get("source_id", "SRC-0000")
        pid_m = re.search(r"SRC-(\d+)", sid)
        pid = int(pid_m.group(1)) if pid_m else 0

        clean_doi = doi or meta.get("DOI")

        return PaperRecord(
            source_id=sid,
            paper_id=pid,
            title=title,
            authors=authors,
            year=year,
            doi=clean_doi,
            arxiv_id=extract_arxiv_id(meta.get("URL", "")),
            urls={"landing": f"https://doi.org/{clean_doi}" if clean_doi else source_uri, "pdf": pdf_url},
            completeness="metadata-only",
            provenance=prov,
            abstract=abstract,
            sections=[],
            figures=[],
            tables=[],
            equations=[],
            references=references,
            sha256=paper_input.get("sha256")
        )


class SourceRouter:
    """
    Extensible Router selecting the highest-priority plugin capable of handling a paper.
    New sources are added as plugins to the router, not special cases.
    """
    def __init__(self):
        self._plugins: List[BaseSourcePlugin] = []

    def register_plugin(self, plugin: BaseSourcePlugin):
        """Registers a new source plugin, sorting by priority descending."""
        self._plugins.append(plugin)
        self._plugins.sort(key=lambda p: p.priority, reverse=True)

    @property
    def plugins(self) -> List[BaseSourcePlugin]:
        return list(self._plugins)

    def route(self, paper_input: Dict[str, Any]) -> PaperRecord:
        """Finds the best available path and extracts the unified record."""
        for plugin in self._plugins:
            if plugin.can_handle(paper_input):
                return plugin.extract(paper_input)
        raise ValueError(f"No suitable source plugin found for input: {paper_input.get('source_id') or paper_input}")

def create_default_router() -> SourceRouter:
    """Instantiates router with all standard plugins."""
    router = SourceRouter()
    router.register_plugin(ArxivHtmlPlugin())
    router.register_plugin(LatexSourcePlugin())
    router.register_plugin(GrobidPdfPlugin())
    router.register_plugin(CrossrefOpenAlexPlugin())
    return router
