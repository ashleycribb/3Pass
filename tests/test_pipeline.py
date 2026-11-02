"""
Test suite for the 3Pass pipeline.

Tests run entirely on the small fake corpus in tests/fixtures/ (copied to a temp
dir). They never need the real ledger, wiki papers, S3A archive, Drive mount, or
local config, because those are gitignored data.
"""

import os
import sys
import json
import shutil
import pytest
import subprocess

# Ensure workspace root is in python path
WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_ROOT)

from scripts.corpus_common import load_config, DEFAULT_CONFIG_PATH, require_config_value
from scripts.tile_ids import (
    DEFAULT_TILE_IDS, load_tile_ids, AI_DRAFT_LABEL, TILE_HUMAN_LRS, TILE_AI_TRACE_LOG,
)
from scripts import triage_wave, run_pass2
from scripts.convert_markitdown import preserve_latex_math
from scripts.compile_s3a import hilbert_3d_index, hash_string_u64, generate_intermediate_files
from scripts.wiki_manager import lint_wiki, build_paper_page, ensure_wiki_scaffold
from scripts.query_engine import execute_rag_query, execute_dag_query

FIXTURES = os.path.join(WORKSPACE_ROOT, "tests", "fixtures")
EXAMPLE_CONFIG = os.path.join(WORKSPACE_ROOT, "config", "corpus_config.example.yaml")


@pytest.fixture
def cfg(tmp_path):
    """Config pointing at a temp copy of the fake fixture corpus."""
    ledger_dir = tmp_path / "ledger"
    ledger_dir.mkdir()
    shutil.copy(os.path.join(FIXTURES, "sample_manifest.json"), ledger_dir / "source_ledger.json")
    c = load_config(EXAMPLE_CONFIG)
    c.update({
        "ledger_dir": str(ledger_dir),
        "wiki_dir": str(tmp_path / "wiki"),
        "cache_dir": str(tmp_path / "cache"),
        "intermediate_dir": str(tmp_path / "cache" / "s3a_intermediate"),
        "archive_path": str(tmp_path / "ledger" / "research_corpus.s3a"),
    })
    return c


def read_ledger(cfg):
    with open(os.path.join(cfg["ledger_dir"], "source_ledger.json"), encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- config

def test_example_config_loads_without_machine_paths():
    """The committed example config has the required keys and no hardcoded machine paths."""
    cfg = load_config(EXAMPLE_CONFIG)
    for key in ("drive_local_path", "s3a_binary_path", "archive_path", "wiki_dir"):
        assert key in cfg
    raw = open(EXAMPLE_CONFIG, encoding="utf-8").read()
    assert "G:\\" not in raw and "Users\\" not in raw


def test_missing_config_value_is_a_clear_error():
    with pytest.raises(SystemExit) as exc:
        require_config_value(load_config(None), "drive_local_path")
    assert "corpus_config.example.yaml" in str(exc.value)


def test_no_hardcoded_machine_paths_in_scripts():
    """Drive letters and user profile paths must come from config, never from source."""
    bad = []
    scripts_dir = os.path.join(WORKSPACE_ROOT, "scripts")
    for name in os.listdir(scripts_dir):
        if name.endswith(".py"):
            text = open(os.path.join(scripts_dir, name), encoding="utf-8").read()
            if "G:\\" in text or "Users\\" in text or "My Drive" in text:
                bad.append(name)
    assert bad == []


# ---------------------------------------------------------------- tile ids

def test_tile_ids_centralized_and_overridable():
    assert load_tile_ids()["HUMAN_LRS"] == TILE_HUMAN_LRS == 10
    assert load_tile_ids({"tile_ids": {"human_lrs": 70}})["HUMAN_LRS"] == 70
    with pytest.raises(ValueError):
        load_tile_ids({"tile_ids": {"nonsense": 1}})
    assert sorted(DEFAULT_TILE_IDS.values()) == [10, 11, 12, 13, 14, 15]


def test_compile_uses_registry_tile_ids(cfg):
    """Tile IDs written to intermediate files come from tile_ids.py (config override works)."""
    cfg["tile_ids"] = {"AI_TRACE_LOG": 99}
    generate_intermediate_files(cfg)
    inter = cfg["intermediate_dir"]
    rows = [json.loads(l) for l in open(os.path.join(inter, "human_lrs.jsonl"), encoding="utf-8")]
    assert rows and all(r["ai_tile"] == 99 for r in rows)


# ---------------------------------------------------------------- pass 1 (human skim)

def test_triage_lists_candidates_only_and_never_writes(cfg):
    """Pass 1 lists papers with no researcher record; it must not score or modify the ledger."""
    ledger_path = os.path.join(cfg["ledger_dir"], "source_ledger.json")
    before = open(ledger_path, "rb").read()
    candidates = triage_wave.run_listing(cfg)
    assert [c["source_id"] for c in candidates] == ["SRC-9002", "SRC-9003"]  # 9001 already has a record
    assert open(ledger_path, "rb").read() == before
    assert not hasattr(triage_wave, "evaluate_relevance"), "AI scoring must not exist in Pass 1"


def test_triage_skipped_papers_are_never_recorded(cfg):
    triage_wave.run_listing(cfg)
    triage_wave.run_listing(cfg)
    for s in read_ledger(cfg)["sources"]:
        assert "relevance_scores" not in s and "triage_decision" not in s
        if s["source_id"] != "SRC-9001":
            assert not s.get("researcher_selection")


# ---------------------------------------------------------------- pass 2 (researcher-selected only)

def _fake_sections(_pdf):
    return {"abstract": "Fake abstract.", "methodology": "Fake method.", "findings": "Fake findings.",
            "limitations": "Fake limits.", "conclusion": "", "theory_mentions": ["Activity Theory"],
            "candidate_gaps": ["It remains unclear how this works in practice at scale."]}


def test_pass2_requires_researcher_selection(cfg):
    with pytest.raises(SystemExit):
        run_pass2.run_pass2_analysis(cfg, [])


def test_pass2_records_selection_and_labels_ai_output(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(run_pass2, "extract_structured_sections", _fake_sections)
    pdf = tmp_path / "fake.pdf"
    pdf.write_bytes(b"%PDF-fake")
    ledger_path = os.path.join(cfg["ledger_dir"], "source_ledger.json")
    ledger = read_ledger(cfg)
    for s in ledger["sources"]:
        if s["source_id"] == "SRC-9002":
            s["drive_path"] = str(pdf)
    json.dump(ledger, open(ledger_path, "w", encoding="utf-8"))

    run_pass2.run_pass2_analysis(cfg, ["SRC-9002"])

    by_id = {s["source_id"]: s for s in read_ledger(cfg)["sources"]}
    assert by_id["SRC-9002"]["researcher_selection"]["selected_by"] == "researcher"
    assert by_id["SRC-9002"]["pass2_ai_draft"] is True
    assert not by_id["SRC-9003"].get("researcher_selection"), "unselected papers must stay unrecorded"
    card = open(by_id["SRC-9002"]["pass2_card_path"], encoding="utf-8").read()
    assert AI_DRAFT_LABEL in card
    assert "Researcher Notes" in card


# ---------------------------------------------------------------- S3A intermediate records

def test_human_lrs_only_for_selected_papers_and_ai_traces_only_for_ai_drafts(cfg):
    generate_intermediate_files(cfg)
    inter = cfg["intermediate_dir"]
    count = lambda name: sum(1 for _ in open(os.path.join(inter, name), encoding="utf-8"))
    assert count("academic_papers.jsonl") == 3
    assert count("human_lrs.jsonl") == 1   # only SRC-9001, which the researcher selected
    assert count("ai_traces.jsonl") == 1   # only SRC-9001, which has an AI draft


# ---------------------------------------------------------------- unchanged helpers

def test_latex_preservation():
    """Verify LaTeX math normalization preserves equations."""
    raw = r"The loss is given by \[ \mathcal{L} = \sum_{i=1}^n (y_i - \hat{y}_i)^2 \] where \( x \in \mathbb{R} \)."
    out = preserve_latex_math(raw)
    assert "$$" in out, "Display math must be fenced with $$"
    assert "$x \\in \\mathbb{R}$" in out, "Inline math must be normalized to $...$"


def test_hilbert_3d_index():
    """Verify 3D Hilbert spatial hashing generates valid coordinate indices."""
    h1 = hilbert_3d_index(0.0, 0.0, 0.0)
    h2 = hilbert_3d_index(1.0, 1.0, 1.0)
    assert isinstance(h1, int) and h1 >= 0
    assert h1 != h2, "Distinct coordinates must yield distinct indices"
    assert hash_string_u64("x") == hash_string_u64("x")


# ---------------------------------------------------------------- wiki

def _build_fixture_wiki(cfg):
    wiki = cfg["wiki_dir"]
    ensure_wiki_scaffold(wiki)
    for s in read_ledger(cfg)["sources"]:
        build_paper_page(s, cfg["cache_dir"], wiki)
    links = "\n".join(f"- [[papers/{s['source_id']}|{s['source_id']}]]" for s in read_ledger(cfg)["sources"])
    with open(os.path.join(wiki, "index.md"), "w", encoding="utf-8") as f:
        f.write(f"# Index\n{links}\n")
    # Stub the portal pages that paper pages link to
    for rel in ("gaps", "theories", "topics/evidence-evaluation", "topics/learner-agency", "topics/traceable-workflows"):
        path = os.path.join(wiki, rel + ".md")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# {rel}\n")
    return wiki


def test_wiki_linting_passes_on_generated_fixture_wiki(cfg):
    _build_fixture_wiki(cfg)
    assert lint_wiki(cfg) is True


def test_wiki_linting_catches_broken_links(cfg):
    wiki = _build_fixture_wiki(cfg)
    with open(os.path.join(wiki, "index.md"), "a", encoding="utf-8") as f:
        f.write("- [[papers/SRC-0000-does-not-exist]]\n")
    assert lint_wiki(cfg) is False


def test_wiki_paper_pages_label_ai_output(cfg):
    wiki = _build_fixture_wiki(cfg)
    page = open(os.path.join(wiki, "papers", "SRC-9001.md"), encoding="utf-8").read()
    assert AI_DRAFT_LABEL in page


# ---------------------------------------------------------------- query engine

def test_query_engine_rag(cfg):
    """Spatial RAG query returns formatted records."""
    results = execute_rag_query(cfg, "learner agency scaffolding", limit=3)
    assert len(results) > 0
    top_score, top_idx, top_rec = results[0]
    assert "source_id" in top_rec
    assert "provenance" in top_rec


def test_query_engine_dag(cfg, capsys):
    execute_dag_query(cfg, "SRC-9001")
    out = capsys.readouterr().out
    assert "SRC-9001" in out


# ---------------------------------------------------------------- real s3a-cli (optional)

S3A_BIN = shutil.which(load_config().get("s3a_binary_path") or "s3a-cli")


@pytest.mark.skipif(S3A_BIN is None, reason="s3a-cli not installed/configured on this machine")
def test_s3a_archive_roundtrip_with_real_cli(cfg):
    """Compile the fixture corpus with the real s3a-cli and inspect the result."""
    cfg["s3a_binary_path"] = S3A_BIN
    from scripts.compile_s3a import compile_s3a_archive
    compile_s3a_archive(cfg)
    res = subprocess.run([S3A_BIN, "inspect", cfg["archive_path"]], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    assert "Header CRC32" in res.stdout
