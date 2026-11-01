"""
Comprehensive Test Suite for 3Pass Research Corpus Architecture
"""

import os
import sys
import json
import pytest
import subprocess

# Ensure workspace root is in python path
WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_ROOT)

from scripts.inventory_and_sync import load_config as load_inventory_config
from scripts.triage_wave import evaluate_relevance
from scripts.convert_markitdown import preserve_latex_math
from scripts.compile_s3a import hilbert_3d_index, hash_string_u64
from scripts.wiki_manager import lint_wiki
from scripts.query_engine import execute_rag_query, execute_dag_query

CONFIG_PATH = os.path.join(WORKSPACE_ROOT, "config", "corpus_config.yaml")

def test_config_loading():
    """Verify corpus_config.yaml loads properly with required keys."""
    assert os.path.exists(CONFIG_PATH), f"Config file missing at {CONFIG_PATH}"
    cfg = load_inventory_config(CONFIG_PATH)
    assert "drive_local_path" in cfg
    assert "s3a_binary_path" in cfg
    assert "archive_path" in cfg
    assert "wiki_dir" in cfg
    assert os.path.exists(cfg["s3a_binary_path"]), "s3a-cli binary must exist"

def test_triage_scoring_rubric():
    """Verify the 1-5 scoring rubric and decision threshold logic."""
    # Test 1: Evidence Evaluation focus
    ee_text = "This paper presents empirical benchmark validation, error analysis, and hallucination metrics."
    ee, la, tw, dec = evaluate_relevance(ee_text, "Empirical Validation of AI")
    assert ee >= 3
    assert dec == "include"

    # Test 2: Learner Agency focus
    la_text = "We investigate student autonomy, self-regulated learning, and scaffolding in the classroom."
    ee, la, tw, dec = evaluate_relevance(la_text, "Learner Agency in AI Tutoring")
    assert la >= 3
    assert dec == "include"

    # Test 3: Excluded document
    ex_text = "Office hours, grading breakdown, late policy."
    ee, la, tw, dec = evaluate_relevance(ex_text, "Fall 2024 Course Syllabus and Form")
    assert dec == "exclude"

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
    h3 = hilbert_3d_index(0.5, 0.5, 0.5)
    assert isinstance(h1, int) and h1 >= 0
    assert isinstance(h2, int) and h2 >= 0
    assert h1 != h2, "Distinct coordinates must yield distinct indices"

def test_wiki_linting():
    """Verify Karpathy wiki maintains 100% link integrity and provenance tags."""
    cfg = load_inventory_config(CONFIG_PATH)
    success = lint_wiki(cfg)
    assert success is True, "Wiki linter must pass with zero broken links"

def test_s3a_archive_integrity():
    """Verify S3A binary archive exists, has active tiles, and passes s3a-cli inspect."""
    cfg = load_inventory_config(CONFIG_PATH)
    archive_path = os.path.join(WORKSPACE_ROOT, cfg.get("archive_path", "ledger/research_corpus.s3a"))
    assert os.path.exists(archive_path), f"S3A archive missing at {archive_path}"
    assert os.path.getsize(archive_path) > 50000, "S3A archive size must reflect ingested Hyper-Tiles"

    s3a_bin = cfg["s3a_binary_path"]
    res = subprocess.run([s3a_bin, "inspect", archive_path], capture_output=True, text=True)
    assert res.returncode == 0, f"s3a-cli inspect failed: {res.stderr}"
    assert "Slot A" in res.stdout or "Slot B" in res.stdout
    assert "ACADEMIC_PAPERS" in res.stdout
    assert "Header CRC32" in res.stdout

def test_query_engine_rag():
    """Verify spatial RAG query execution returns formatted records with S3A coordinates."""
    cfg = load_inventory_config(CONFIG_PATH)
    results = execute_rag_query(cfg, "learner agency scaffolding", limit=3)
    assert len(results) > 0
    top_score, top_idx, top_rec = results[0]
    assert "source_id" in top_rec
    assert "provenance" in top_rec
