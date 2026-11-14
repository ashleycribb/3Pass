"""
Comprehensive Tests for Source Router, Unified Record Schema, Wiki Drafts, and S3A Integration
"""

import os
import sys
import json
import pytest

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_ROOT)

from scripts.models import PaperRecord
from scripts.source_router import (
    ArxivHtmlPlugin,
    LatexSourcePlugin,
    GrobidPdfPlugin,
    CrossrefOpenAlexPlugin,
    SourceRouter,
    create_default_router
)
from scripts.wiki_manager import (
    build_paper_page_from_record,
    lint_wiki,
    ensure_wiki_scaffold
)
from scripts.compile_s3a import compile_s3a_archive, round_trip_test, load_config

FIXTURES_DIR = os.path.join(WORKSPACE_ROOT, "tests", "fixtures")

@pytest.fixture
def arxiv_html():
    with open(os.path.join(FIXTURES_DIR, "arxiv_sample.html"), "r", encoding="utf-8") as fp:
        return fp.read()

@pytest.fixture
def latex_tex():
    with open(os.path.join(FIXTURES_DIR, "latex_sample.tex"), "r", encoding="utf-8") as fp:
        return fp.read()

@pytest.fixture
def grobid_tei():
    with open(os.path.join(FIXTURES_DIR, "grobid_sample.tei.xml"), "r", encoding="utf-8") as fp:
        return fp.read()

@pytest.fixture
def crossref_json():
    with open(os.path.join(FIXTURES_DIR, "crossref_sample.json"), "r", encoding="utf-8") as fp:
        return json.load(fp)

def test_arxiv_html_plugin(arxiv_html):
    """Path a: arXiv LaTeXML HTML -> completeness 'full', latexml-verified parts."""
    plugin = ArxivHtmlPlugin()
    paper_input = {
        "source_id": "SRC-1001",
        "title": "Default Title",
        "arxiv_id": "2402.12345",
        "html_fixture": arxiv_html,
        "fixture_uri": "https://arxiv.org/html/2402.12345"
    }
    assert plugin.can_handle(paper_input) is True
    rec = plugin.extract(paper_input)

    assert isinstance(rec, PaperRecord)
    assert rec.source_id == "SRC-1001"
    assert rec.completeness == "full"
    assert rec.provenance.path_used == "arxiv_html"
    assert rec.provenance.tool == "latexml-html"
    assert "Neuro-Symbolic Reasoning" in rec.title
    assert "Alice Scholar" in rec.authors
    assert len(rec.sections) >= 2
    assert len(rec.figures) >= 1
    assert len(rec.tables) >= 1
    assert len(rec.equations) >= 1
    assert len(rec.references) >= 1
    assert rec.sections[0].verified == "latexml-verified"
    assert rec.figures[0].verified == "latexml-verified"

def test_latex_source_plugin(latex_tex):
    """Path b: LaTeX source -> completeness 'full'."""
    plugin = LatexSourcePlugin()
    paper_input = {
        "source_id": "SRC-1002",
        "title": "LaTeX Paper",
        "latex_fixture": latex_tex,
        "fixture_uri": "tests/fixtures/latex_sample.tex"
    }
    assert plugin.can_handle(paper_input) is True
    rec = plugin.extract(paper_input)

    assert isinstance(rec, PaperRecord)
    assert rec.source_id == "SRC-1002"
    assert rec.completeness == "full"
    assert rec.provenance.path_used == "latex_source"
    assert "Formal Proof Verification" in rec.title
    assert "Carol Professor" in rec.authors
    assert len(rec.sections) >= 2
    assert len(rec.equations) >= 1
    assert len(rec.figures) >= 1
    assert len(rec.tables) >= 1
    assert len(rec.references) >= 1

def test_grobid_pdf_plugin(grobid_tei):
    """Path c: PDF only -> completeness 'pdf-extracted', all parts 'pdf-extracted, verify'."""
    plugin = GrobidPdfPlugin()
    paper_input = {
        "source_id": "SRC-1003",
        "title": "PDF Paper",
        "tei_fixture": grobid_tei,
        "fixture_uri": "tests/fixtures/grobid_sample.tei.xml"
    }
    assert plugin.can_handle(paper_input) is True
    rec = plugin.extract(paper_input)

    assert isinstance(rec, PaperRecord)
    assert rec.source_id == "SRC-1003"
    assert rec.completeness == "pdf-extracted"
    assert rec.provenance.path_used == "grobid_pdf"
    assert rec.provenance.tool == "grobid-tei"
    assert "Empirical Study of Student Agency" in rec.title
    assert "Eve Analyst" in rec.authors
    assert len(rec.sections) >= 2
    assert rec.sections[0].verified == "pdf-extracted, verify"
    assert rec.figures[0].verified == "pdf-extracted, verify"
    assert rec.tables[0].verified == "pdf-extracted, verify"
    assert rec.references[0].verified == "pdf-extracted, verify"

def test_crossref_metadata_plugin(crossref_json):
    """Path d: DOI/metadata only -> completeness 'metadata-only', no body."""
    plugin = CrossrefOpenAlexPlugin()
    paper_input = {
        "source_id": "SRC-1004",
        "doi": "10.1145/3491101.3503719",
        "metadata_fixture": crossref_json,
        "fixture_uri": "https://api.crossref.org/works/10.1145/3491101.3503719"
    }
    assert plugin.can_handle(paper_input) is True
    rec = plugin.extract(paper_input)

    assert isinstance(rec, PaperRecord)
    assert rec.source_id == "SRC-1004"
    assert rec.completeness == "metadata-only"
    assert rec.provenance.path_used == "doi_metadata"
    assert "Algorithmic Scaffolding" in rec.title
    assert "Frank Scientist" in rec.authors
    assert rec.year == 2024
    assert len(rec.sections) == 0, "Metadata-only must have no body sections"
    assert len(rec.figures) == 0
    assert len(rec.tables) == 0
    assert len(rec.references) >= 1

def test_unified_record_schema_across_all_paths(arxiv_html, latex_tex, grobid_tei, crossref_json):
    """All 4 paths must produce the identical PaperRecord schema."""
    router = create_default_router()

    inputs = [
        {"source_id": "SRC-A", "arxiv_id": "2402.12345", "html_fixture": arxiv_html},
        {"source_id": "SRC-B", "latex_fixture": latex_tex},
        {"source_id": "SRC-C", "tei_fixture": grobid_tei},
        {"source_id": "SRC-D", "doi": "10.1145/3491101.3503719", "metadata_fixture": crossref_json}
    ]

    records = [router.route(inp) for inp in inputs]
    expected_keys = {
        "source_id", "paper_id", "title", "authors", "year", "doi", "arxiv_id",
        "urls", "completeness", "provenance", "abstract", "sections",
        "figures", "tables", "equations", "references", "human_notes",
        "sha256", "created_at"
    }

    for rec in records:
        d = rec.to_dict()
        assert set(d.keys()) == expected_keys
        assert rec.human_notes is not None
        assert "problem_formulation" in rec.human_notes
        assert "methodology_evaluation" in rec.human_notes
        assert "findings_credibility" in rec.human_notes
        assert "dissertation_gaps" in rec.human_notes

def test_wiki_draft_page_facts_and_empty_researcher_notes(arxiv_html, tmp_path):
    """Wiki draft page contains facts and structure only, with empty researcher notes sections."""
    plugin = ArxivHtmlPlugin()
    rec = plugin.extract({
        "source_id": "SRC-TEST",
        "arxiv_id": "2402.12345",
        "html_fixture": arxiv_html,
        "fixture_uri": "https://arxiv.org/html/2402.12345"
    })

    wiki_dir = str(tmp_path / "wiki")
    ensure_wiki_scaffold(wiki_dir)
    page_path = build_paper_page_from_record(rec, wiki_dir)

    with open(page_path, "r", encoding="utf-8") as fp:
        content = fp.read()

    # Facts & Structure present
    assert "Neuro-Symbolic Reasoning" in content
    assert "Alice Scholar" in content
    assert "1. Introduction" in content
    assert "Figure 1: Neuro-symbolic pipeline overview." in content
    assert "Table 1: Accuracy comparison across architectures." in content
    assert "provenance:" in content

    # Researcher notes sections present and empty
    assert "## Researcher Notes & Critical Appraisal" in content
    assert "### Problem Formulation & Theoretical Alignment" in content
    assert "*(Researcher notes go here)*" in content
    assert "### Methodology Evaluation" in content
    assert "### Findings & Evidence Credibility" in content
    assert "### Gaps & Questions for Dissertation" in content

    # Automated judgments absent
    assert "Triage Scores:" not in content
    assert "EE:" not in content
    assert "LA:" not in content

def test_source_router_plugin_priority():
    """Router prioritizes arXiv HTML > LaTeX source > GROBID PDF > DOI metadata."""
    router = create_default_router()
    plugins = router.plugins

    assert plugins[0].name == "arxiv_html"
    assert plugins[1].name == "latex_source"
    assert plugins[2].name == "grobid_pdf"
    assert plugins[3].name == "doi_metadata"

def test_s3a_round_trip_export(tmp_path):
    """S3A round-trip test: exports records, builds .s3a, inspects headers and tiles."""
    cfg = load_config()
    round_trip_test(cfg)
