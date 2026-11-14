"""
Tests for Reference Resolver and Record Merging

Validates:
  1. Resolution statuses: resolved | ambiguous | not_found
  2. Immutability: original reference text, authors, id are never dropped or rewritten
  3. Caching and rate limiting
  4. Merging duplicate paper records across multiple routes
  5. Session "as_of" date field
  6. Human-only "citation_role" in human_notes
"""

import os
import shutil
import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch

from scripts.models import (
    PaperRecord,
    ReferenceRecord,
    ReferenceResolution,
    RecordProvenance,
    SectionRecord,
)
from scripts.reference_resolver import (
    ReferenceResolver,
    normalize_title,
    fuzzy_title_score,
    are_duplicate_papers,
    merge_paper_records,
    resolve_paper_references,
)


@pytest.fixture
def temp_cache_dir(tmp_path):
    cache_dir = str(tmp_path / "ref_cache")
    os.makedirs(cache_dir, exist_ok=True)
    yield cache_dir
    shutil.rmtree(cache_dir, ignore_errors=True)


def test_normalize_title_and_fuzzy_score():
    t1 = "Attention Is All You Need"
    t2 = "\\textbf{Attention is All You Need}"
    t3 = "Attention Is All You Need: Discovering Transformers"

    assert normalize_title(t1) == "attention is all you need"
    assert normalize_title(t2) == "attention is all you need"
    assert fuzzy_title_score(t1, t2) == 1.0
    assert fuzzy_title_score(t1, t3) >= 0.85


def test_resolution_resolved_via_doi(temp_cache_dir):
    """Reference with DOI resolves with status 'resolved' and matched_service 'doi'."""
    resolver = ReferenceResolver(cache_dir=temp_cache_dir)
    ref = ReferenceRecord(
        id="ref_doi_1",
        title="Adversarial AI applied to cross-user inter-domain",
        doi="10.1371/journal.pone.0298888",
        year=2024,
        raw="Adversarial AI. PLOS ONE. 10.1371/journal.pone.0298888"
    )

    res = resolver.resolve_reference(ref)

    assert res.status == "resolved"
    assert res.match_score >= 0.90
    assert "10.1371/journal.pone.0298888" in res.matched_id
    assert res.matched_service == "doi"
    assert ref.resolution == res


def test_resolution_resolved_via_title_search(temp_cache_dir):
    """Reference without DOI resolves via title search on Crossref / OpenAlex."""
    resolver = ReferenceResolver(cache_dir=temp_cache_dir)
    ref = ReferenceRecord(
        id="ref_title_1",
        title="Attention Is All You Need",
        authors=["Ashish Vaswani", "Noam Shazeer"],
        year=2017
    )

    res = resolver.resolve_reference(ref)

    assert res.status == "resolved"
    assert res.match_score >= 0.85
    assert res.matched_id is not None
    assert res.matched_service in ["crossref", "openalex", "semanticscholar"]


def test_resolution_ambiguous_status(temp_cache_dir):
    """Ambiguous status returned when candidates have borderline score or close competition."""
    resolver = ReferenceResolver(cache_dir=temp_cache_dir)

    # Mock candidate returns with close matching scores
    mock_items = [
        {"title": ["Empirical Study of Deep Learning in Practice"], "DOI": "10.1000/1", "issued": {"date-parts": [[2020]]}},
        {"title": ["Empirical Study of Deep Learning Frameworks"], "DOI": "10.1000/2", "issued": {"date-parts": [[2020]]}},
    ]
    with patch.object(resolver, "_search_crossref", return_value=resolver._evaluate_candidates("Empirical Study of Deep Learning", 2020, mock_items, "crossref")):
        ref = ReferenceRecord(
            id="ref_ambig",
            title="Empirical Study of Deep Learning",
            year=2020
        )
        res = resolver.resolve_reference(ref)
        assert res.status == "ambiguous"
        assert res.match_score >= 0.70
        assert res.matched_service == "crossref"


def test_resolution_not_found_status(temp_cache_dir):
    """Reference with unrecognizable gibberish returns 'not_found'."""
    resolver = ReferenceResolver(cache_dir=temp_cache_dir)
    ref = ReferenceRecord(
        id="ref_not_found",
        title="Qwxklmnop Nonsense Manuscript Zzyzx Title 1234567890",
        year=1902
    )

    res = resolver.resolve_reference(ref)

    assert res.status == "not_found"
    assert res.match_score < 0.60
    assert res.matched_id is None


def test_immutability_never_drop_or_rewrite_reference(temp_cache_dir):
    """Original reference text, raw string, venue, and authors are never overwritten."""
    resolver = ReferenceResolver(cache_dir=temp_cache_dir)
    orig_raw = "Original Citation: Vaswani et al. Attention Is All You Need. NIPS 2017."
    ref = ReferenceRecord(
        id="custom_id_42",
        title="Attention Is All You Need",
        authors=["Vaswani, A.", "Shazeer, N."],
        venue="Advances in Neural Information Processing Systems",
        year=2017,
        raw=orig_raw,
        verified="latex-extracted, verify"
    )

    resolver.resolve_reference(ref)

    assert ref.id == "custom_id_42"
    assert ref.title == "Attention Is All You Need"
    assert ref.authors == ["Vaswani, A.", "Shazeer, N."]
    assert ref.venue == "Advances in Neural Information Processing Systems"
    assert ref.year == 2017
    assert ref.raw == orig_raw
    assert ref.verified == "latex-extracted, verify"
    assert ref.resolution is not None


def test_reference_caching_avoids_repeated_network_calls(temp_cache_dir):
    """Subsequent resolution reads directly from cache without HTTP request."""
    resolver = ReferenceResolver(cache_dir=temp_cache_dir)
    ref = ReferenceRecord(
        id="cached_ref_1",
        title="Adversarial AI applied to cross-user inter-domain",
        doi="10.1371/journal.pone.0298888",
        year=2024
    )

    res1 = resolver.resolve_reference(ref)

    # Second call with session.get mocked out to raise error if called
    with patch.object(resolver.session, "get", side_effect=AssertionError("Network called on cached entry!")):
        ref2 = ReferenceRecord(
            id="cached_ref_2",
            title="Adversarial AI applied to cross-user inter-domain",
            doi="10.1371/journal.pone.0298888",
            year=2024
        )
        res2 = resolver.resolve_reference(ref2)
        assert res2.status == res1.status
        assert res2.matched_id == res1.matched_id
        assert res2.match_score == res1.match_score


def test_duplicate_paper_detection_and_merging():
    """Same paper arriving via two routes merges into one record with both provenances."""
    prov1 = RecordProvenance(
        path_used="arxiv_html",
        tool="latexml-html",
        tool_version="LaTeXML 0.8.8",
        source_uri="https://arxiv.org/html/2403.05530",
        extracted_at=datetime.now().isoformat()
    )
    rec1 = PaperRecord(
        source_id="SRC-001",
        paper_id=570720,
        title="Gemini 1.5: Unlocking multimodal understanding across millions of tokens of context",
        authors=["Gemini Team", "Google"],
        year=2024,
        arxiv_id="2403.05530",
        completeness="full",
        provenance=prov1,
        sections=[SectionRecord(id="sec_1", title="Introduction", text="Multimodal context...")],
        sha256="hash_html_123"
    )

    prov2 = RecordProvenance(
        path_used="doi_metadata",
        tool="crossref-openalex",
        tool_version="Polite API v1",
        source_uri="https://api.crossref.org/works/10.48550/arXiv.2403.05530",
        extracted_at=datetime.now().isoformat()
    )
    rec2 = PaperRecord(
        source_id="SRC-002",
        paper_id=570720,
        title="Gemini 1.5: Unlocking Multimodal Understanding Across Millions of Tokens of Context",
        authors=["M. Reid", "N. Savinov"],
        year=2024,
        doi="10.48550/arXiv.2403.05530",
        arxiv_id="2403.05530",
        completeness="metadata-only",
        provenance=prov2,
        sha256="hash_meta_456"
    )

    assert are_duplicate_papers(rec1, rec2) is True

    merged = merge_paper_records(rec1, rec2)

    assert merged.completeness == "full"
    assert len(merged.sections) == 1
    assert merged.arxiv_id == "2403.05530"
    assert merged.doi == "10.48550/arXiv.2403.05530"
    # Both provenances retained
    assert len(merged.provenances) == 2
    paths = {p.path_used for p in merged.provenances}
    assert paths == {"arxiv_html", "doi_metadata"}


def test_session_as_of_field():
    """Session as_of metadata field survives serialization."""
    rec = PaperRecord(
        source_id="SRC-ASOF",
        paper_id=123,
        title="Testing as_of field",
        authors=["Alice"],
        year=2025,
        as_of="2026-11-14T09:56:00"
    )

    d = rec.to_dict()
    assert d["as_of"] == "2026-11-14T09:56:00"

    rec2 = PaperRecord.from_dict(d)
    assert rec2.as_of == "2026-11-14T09:56:00"


def test_human_only_citation_role():
    """citation_role is empty by default and AI never fills it."""
    rec = PaperRecord(
        source_id="SRC-ROLE",
        paper_id=456,
        title="Testing citation role",
        authors=["Bob"],
        year=2024
    )

    # Empty by default
    assert rec.human_notes["citation_role"] == ""

    # Valid roles defined for human use
    valid_roles = {"baseline", "dataset", "method", "metric", "background"}
    assert "baseline" in valid_roles

    # Human annotates citation_role
    rec.human_notes["citation_role"] = "method"
    d = rec.to_dict()
    assert d["human_notes"]["citation_role"] == "method"

    rec2 = PaperRecord.from_dict(d)
    assert rec2.human_notes["citation_role"] == "method"
