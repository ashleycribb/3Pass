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
    PublisherXmlPlugin,
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
from scripts.compile_s3a import compile_s3a_archive, round_trip_test, load_config, hilbert_3d_index, hash_string_u64
from scripts.query_engine import run_s3a_graph_cmd, execute_dag_query, execute_graph_query

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
def jats_xml():
    with open(os.path.join(FIXTURES_DIR, "jats_sample.xml"), "r", encoding="utf-8") as fp:
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

def test_publisher_xml_plugin(jats_xml):
    """Path publisher_xml: Publisher JATS XML -> completeness 'full', jats-verified parts."""
    plugin = PublisherXmlPlugin()
    paper_input = {
        "source_id": "SRC-1002B",
        "doi": "10.1371/journal.pone.0298888",
        "jats_fixture": jats_xml,
        "fixture_uri": "tests/fixtures/jats_sample.xml"
    }
    assert plugin.can_handle(paper_input) is True
    rec = plugin.extract(paper_input)

    assert isinstance(rec, PaperRecord)
    assert rec.source_id == "SRC-1002B"
    assert rec.completeness == "full"
    assert rec.provenance.path_used == "publisher_xml"
    assert rec.provenance.tool == "jats-xml"
    assert "Adversarial AI applied to cross-user inter-domain" in rec.title
    assert "Muhammad Hassan" in rec.authors
    assert "Tom Kelsey" in rec.authors
    assert "Fahrurrozi Rahman" in rec.authors
    assert rec.year == 2024
    assert len(rec.sections) >= 2
    assert rec.sections[0].verified == "jats-verified"
    assert len(rec.figures) >= 1
    assert rec.figures[0].verified == "jats-verified"
    assert len(rec.tables) >= 1
    assert rec.tables[0].verified == "jats-verified"
    assert len(rec.equations) >= 1
    assert rec.equations[0].verified == "jats-verified"
    assert len(rec.references) >= 2
    assert rec.references[0].verified == "jats-verified"

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
    assert rec.provenance.tool in ("grobid-tei", "tei-xml-fixture")
    assert "GROBID" not in rec.provenance.tool_version, "Fixture extraction must not claim a GROBID version"
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

def test_unified_record_schema_across_all_paths(arxiv_html, latex_tex, jats_xml, grobid_tei, crossref_json):
    """All 5 paths must produce the identical PaperRecord schema."""
    router = create_default_router()

    inputs = [
        {"source_id": "SRC-A", "arxiv_id": "2402.12345", "html_fixture": arxiv_html},
        {"source_id": "SRC-B", "latex_fixture": latex_tex},
        {"source_id": "SRC-C", "doi": "10.1371/journal.pone.0298888", "jats_fixture": jats_xml},
        {"source_id": "SRC-D", "tei_fixture": grobid_tei},
        {"source_id": "SRC-E", "doi": "10.1145/3491101.3503719", "metadata_fixture": crossref_json}
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
    """Router prioritizes arXiv HTML > LaTeX source > Publisher XML > GROBID PDF > DOI metadata."""
    router = create_default_router()
    plugins = router.plugins

    assert plugins[0].name == "arxiv_html"
    assert plugins[1].name == "latex_source"
    assert plugins[2].name == "publisher_xml"
    assert plugins[3].name == "grobid_pdf"
    assert plugins[4].name == "doi_metadata"

def test_s3a_round_trip_export(tmp_path):
    """S3A round-trip test: exports records, builds .s3a, inspects headers and tiles."""
    cfg = load_config()
    round_trip_test(cfg)

def test_python_and_s3a_cli_parity(tmp_path):
    """Item 2: Verifies bit-for-bit parity between Python hash/Hilbert functions and s3a-cli graph queries."""
    # 1. Exact mathematical parity for fixed inputs
    h_str = hash_string_u64("Learner_Agency")
    assert h_str == 10956731137806894464
    assert f"0x{h_str:016x}" == "0x980e20eb28710580"

    # Hilbert 3D index for normalized coordinates
    assert hilbert_3d_index(0.25, 0.25, 0.25) == 262143
    assert hilbert_3d_index(0.5, 0.5, 0.5) == 2097151
    assert hilbert_3d_index(0.0, 0.0, 0.0) == 0
    assert hilbert_3d_index(1.0, 1.0, 1.0) == 16777215

    # 2. End-to-end round trip parity through s3a-cli
    archive_file = str(tmp_path / "test_parity.s3a")
    inter_dir = str(tmp_path / "intermediate")
    cfg = load_config()
    cfg["archive_path"] = archive_file
    cfg["intermediate_dir"] = inter_dir

    sample_sources = [
        {
            "source_id": "SRC-9001",
            "title": "Empirical Evaluation of AI Reasoning in Educational Question Answering",
            "author": "Alice Test; Bob Sample",
            "year": 2025,
            "pass_status": "P2 done",
            "provenance": "SRC-9001 | test.pdf | P2 done",
            "abstract_excerpt": "We present a comprehensive empirical benchmark...",
            "researcher_selection": {"selected_by": "researcher"},
            "pass2_ai_draft": True
        }
    ]
    compile_s3a_archive(cfg, fresh=True, sources_list=sample_sources)

    # Query s3a-cli graph neighbors for node 0x0000000000002329 (SRC-9001)
    res, err = run_s3a_graph_cmd(cfg, "neighbors", "0x0000000000002329")
    assert err is None, f"s3a-cli error: {err}"
    assert res is not None
    assert res.get("ok") is True
    neighbors = res.get("neighbors", [])
    assert len(neighbors) >= 1

    # Find the edge to Learner_Agency
    target_edge = next((e for e in neighbors if e.get("to") == "0x980e20eb28710580"), None)
    assert target_edge is not None, "Edge to 0x980e20eb28710580 (Learner_Agency) must exist"
    assert target_edge["properties"]["hilbert"] == 262143
    assert target_edge["properties"]["predicate_id"] == 2

def test_query_engine_s3a_graph_delegation(tmp_path, capsys):
    """Item 1: Query engine delegates DAG and Graph queries to s3a-cli graph commands."""
    archive_file = str(tmp_path / "test_query.s3a")
    inter_dir = str(tmp_path / "intermediate")
    cfg = load_config()
    cfg["archive_path"] = archive_file
    cfg["intermediate_dir"] = inter_dir

    sample_sources = [
        {
            "source_id": "SRC-9001",
            "title": "Empirical Evaluation of AI Reasoning in Educational Question Answering",
            "author": "Alice Test; Bob Sample",
            "year": 2025,
            "pass_status": "P2 done",
            "provenance": "SRC-9001 | test.pdf | P2 done",
            "abstract_excerpt": "We present a comprehensive empirical benchmark...",
            "researcher_selection": {"selected_by": "researcher"},
            "pass2_ai_draft": True
        }
    ]
    compile_s3a_archive(cfg, fresh=True, sources_list=sample_sources)

    # Test DAG query delegation
    execute_dag_query(cfg, "SRC-9001")
    out = capsys.readouterr().out
    assert "S3A Native Graph & Provenance DAG Trace: `SRC-9001`" in out
    assert "S3A Tile 13 Graph Neighbors" in out
    assert "0x980e20eb28710580" in out
    assert "S3A Graph Traversal" in out

    # Test Graph query delegation
    execute_graph_query(cfg, "Learner_Agency")
    out = capsys.readouterr().out
    assert "S3A Knowledge Graph Subgraph: 'Learner_Agency'" in out
    assert "Found 1 associated S3A graph edge(s)" in out
    assert "0x0000000000002329" in out

def test_all_paths_populate_sha256_and_positive_paper_id(arxiv_html, latex_tex, jats_xml, grobid_tei, crossref_json):
    """Smoke fix 4: Asserts sha256 (64 hex characters) and positive paper_id (>0) are set on every record."""
    router = create_default_router()
    inputs = [
        {"source_id": "SRC-A", "arxiv_id": "2402.12345", "html_fixture": arxiv_html},
        {"source_id": "SRC-B", "latex_fixture": latex_tex},
        {"source_id": "SRC-C", "doi": "10.1371/journal.pone.0298888", "jats_fixture": jats_xml},
        {"source_id": "SRC-D", "tei_fixture": grobid_tei},
        {"source_id": "SRC-E", "doi": "10.1145/3491101.3503719", "metadata_fixture": crossref_json}
    ]
    for inp in inputs:
        rec = router.route(inp)
        assert isinstance(rec.sha256, str), f"sha256 must be a string for {rec.source_id}"
        assert len(rec.sha256) == 64, f"sha256 must be 64 characters for {rec.source_id}"
        assert int(rec.sha256, 16) > 0, f"sha256 must be valid non-zero hex for {rec.source_id}"
        assert isinstance(rec.paper_id, int), f"paper_id must be int for {rec.source_id}"
        assert rec.paper_id > 0, f"paper_id must be positive (> 0) for {rec.source_id}"

def test_path_a_fixture_metadata_inspection(arxiv_html):
    """Smoke fix 5: Path a fixture inspection (title, first author, year)."""
    plugin = ArxivHtmlPlugin()
    rec = plugin.extract({
        "source_id": "SRC-PATH-A",
        "arxiv_id": "2402.12345",
        "html_fixture": arxiv_html,
        "fixture_uri": "https://arxiv.org/html/2402.12345"
    })
    assert rec.title == "Neuro-Symbolic Reasoning in Educational Systems"
    assert rec.authors[0] == "Alice Scholar"
    assert rec.year == 2025

def test_path_b_fixture_metadata_inspection(latex_tex):
    """Smoke fix 5: Path b fixture inspection (title, first author, year)."""
    plugin = LatexSourcePlugin()
    rec = plugin.extract({
        "source_id": "SRC-PATH-B",
        "latex_fixture": latex_tex,
        "fixture_uri": "tests/fixtures/latex_sample.tex"
    })
    assert rec.title == "Formal Proof Verification in Undergraduate Mathematics"
    assert rec.authors[0] == "Carol Professor"
    assert rec.year == 2024

def test_path_publisher_xml_fixture_metadata_inspection(jats_xml):
    """Smoke fix: Publisher XML JATS fixture inspection (title, first author, year)."""
    plugin = PublisherXmlPlugin()
    rec = plugin.extract({
        "source_id": "SRC-PATH-JATS",
        "doi": "10.1371/journal.pone.0298888",
        "jats_fixture": jats_xml,
        "fixture_uri": "tests/fixtures/jats_sample.xml"
    })
    assert "Adversarial AI applied to cross-user inter-domain" in rec.title
    assert rec.authors[0] == "Muhammad Hassan"
    assert rec.year == 2024

def test_path_c_fixture_metadata_inspection(grobid_tei):
    """Smoke fix 5: Path c fixture inspection (title, first author, year)."""
    plugin = GrobidPdfPlugin()
    rec = plugin.extract({
        "source_id": "SRC-PATH-C",
        "tei_fixture": grobid_tei,
        "fixture_uri": "tests/fixtures/grobid_sample.tei.xml"
    })
    assert rec.title == "Empirical Study of Student Agency in Collaborative Inquiry"
    assert rec.authors[0] == "Eve Analyst"
    assert rec.year == 2024

def test_path_d_fixture_metadata_inspection(crossref_json):
    """Smoke fix 5: Path d fixture inspection (title, first author, year)."""
    plugin = CrossrefOpenAlexPlugin()
    rec = plugin.extract({
        "source_id": "SRC-PATH-D",
        "doi": "10.1145/3491101.3503719",
        "metadata_fixture": crossref_json,
        "fixture_uri": "https://api.crossref.org/works/10.1145/3491101.3503719"
    })
    assert rec.title == "Algorithmic Scaffolding in Higher Education: A Crossref Registered Record"
    assert rec.authors[0] == "Frank Scientist"
    assert rec.year == 2024

def test_no_false_grobid_tool_version_claim_without_request(grobid_tei):
    """Provenance check (Item 4): Fails if any record's tool_version claims GROBID when no GROBID request was made."""
    plugin = GrobidPdfPlugin()
    paper_input = {
        "source_id": "SRC-NO-GROBID",
        "title": "PDF Paper",
        "tei_fixture": grobid_tei,
        "fixture_uri": "tests/fixtures/grobid_sample.tei.xml"
    }
    rec = plugin.extract(paper_input)

    # When extracting from a fixture, tool_version must NEVER claim a real GROBID tool or version
    assert "GROBID" not in rec.provenance.tool_version, (
        f"tool_version claimed '{rec.provenance.tool_version}' but no live GROBID request was made!"
    )
    assert rec.provenance.tool != "grobid-tei", (
        f"tool claimed '{rec.provenance.tool}' without making a live GROBID request!"
    )

    # Attempting to forge a GROBID tool_version when is_live_request is False must raise ValueError
    with pytest.raises(ValueError, match="Never write a tool name or version that did not actually run"):
        plugin._parse_tei_xml(grobid_tei, paper_input, "fixture://dummy", is_live_request=False, grobid_version="0.8.1")

def test_grobid_pdf_plugin_fails_loudly_when_offline(tmp_path):
    """Smoke fix 3: GROBID plugin must fail loudly with RuntimeError when GROBID is unreachable."""
    dummy_pdf = tmp_path / "offline_test.pdf"
    dummy_pdf.write_bytes(b"%PDF-1.4 sample content")

    plugin = GrobidPdfPlugin(grobid_url="http://127.0.0.1:59999")
    paper_input = {
        "source_id": "SRC-OFFLINE",
        "original_path": str(dummy_pdf)
    }

    with pytest.raises(RuntimeError) as exc_info:
        plugin.extract(paper_input)

    assert "GROBID is unreachable" in str(exc_info.value)

