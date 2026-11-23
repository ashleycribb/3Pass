"""Acceptance tests for hyphen / fragment self-check and ingest --workers (F5, F9).

These tests must fail on origin/main and pass only after the self-check
patch and the --workers flag are implemented. Do not edit them to make
them pass.
"""
from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "verified_pdf"))
sys.path.insert(0, str(ROOT / "scripts"))

import agent_tool as at  # noqa: E402
import verify_conversion as vc  # noqa: E402


def _pdf_with_text(path: Path, text: str) -> Path:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=400, height=200)
    page.insert_text((40, 80), text, fontsize=12, fontname="helv")
    doc.save(path)
    doc.close()
    return path


def _view():
    return {
        "source_pdf": "synthetic-hyphen.pdf",
        "source_sha256": "0" * 64,
        "blocks": [
            {"id": "p1", "status": "verified", "text": "recommended practice"},
        ],
        "document_flags": [],
    }


def test_self_check_joins_ascii_hyphen_like_pdfium_fffe(tmp_path):
    """Markdown 'recom-mended' must match a PDF page that contains 'recommended'.

    pdfium stores discretionary hyphens as U+FFFE (already stripped by
    norm_text). Docling often keeps an ASCII hyphen inside the word instead.
    """
    pdf_path = _pdf_with_text(tmp_path / "hyphen.pdf", "recommended practice")
    pdf = vc.Pdf(pdf_path)
    view = _view()
    md = "recom-mended practice\n"
    htm = "<p>recom-mended practice</p>"
    leftover = at.self_check(view, md, htm, pdf)
    assert leftover == vc.Counter()
    assert view["blocks"][0]["status"] == "verified"


def test_self_check_holds_never_in_pdf_words_instead_of_raising(tmp_path):
    """A word that is not in the PDF must be returned, not raised as a hard fail."""
    pdf_path = _pdf_with_text(tmp_path / "clean.pdf", "recommended practice")
    pdf = vc.Pdf(pdf_path)
    view = _view()
    view["blocks"] = [{"id": "p1", "status": "verified", "text": "recommended xyzzy practice"}]
    md = "recommended xyzzy practice\n"
    htm = "<p>recommended xyzzy practice</p>"
    leftover = at.self_check(view, md, htm, pdf)
    assert leftover["xyzzy"] >= 1
    assert "xyzzy" in leftover


def test_self_check_records_known_word_surplus_once_as_local_sc1(tmp_path):
    """Duplicating a PDF word is a LOCAL-SC1 flag, not a failure, and is not appended twice."""
    pdf_path = _pdf_with_text(tmp_path / "surplus.pdf", "black box")
    pdf = vc.Pdf(pdf_path)
    view = {
        "source_pdf": "synthetic-surplus.pdf",
        "source_sha256": "1" * 64,
        "blocks": [{"id": "p1", "status": "verified", "text": "black black box"}],
        "document_flags": [],
    }
    md = "black black box\n"
    htm = "<p>black black box</p>"
    leftover1 = at.self_check(view, md, htm, pdf)
    leftover2 = at.self_check(view, md, htm, pdf)
    assert leftover1 == vc.Counter()
    assert leftover2 == vc.Counter()
    sc1 = [f for f in view["document_flags"] if f.get("id") == "LOCAL-SC1"]
    assert len(sc1) == 1
    assert "black" in sc1[0]["detail"]


def test_self_check_pairs_cross_block_hyphen_fragments(tmp_path):
    """'compre-' plus 'hension' in markdown must reconcile with PDF 'comprehension'."""
    pdf_path = _pdf_with_text(tmp_path / "split.pdf", "comprehension required")
    pdf = vc.Pdf(pdf_path)
    view = {
        "source_pdf": "synthetic-split.pdf",
        "source_sha256": "2" * 64,
        "blocks": [
            {"id": "a", "status": "verified", "text": "compre-"},
            {"id": "b", "status": "verified", "text": "hension required"},
        ],
        "document_flags": [],
    }
    md = "compre-\n\nhension required\n"
    htm = "<p>compre-</p><p>hension required</p>"
    leftover = at.self_check(view, md, htm, pdf)
    assert leftover == vc.Counter()


def test_ingest_forwards_workers_to_run_shards(tmp_path, monkeypatch):
    called = {}

    def fake_run_shards(pdf, out, ocr=False, grobid=None, workers=None, **kwargs):
        called["workers"] = workers
        called["pdf"] = Path(pdf)
        return {
            "view": {
                "document_status": "VERIFIED",
                "counts": {"verified": 0, "held": 0, "needs_confirmation": 0},
                "reading_order_verified": True,
                "pages": [],
            }
        }

    import parallel as par

    monkeypatch.setattr(par, "run_shards", fake_run_shards)
    # agent_tool may have imported parallel as a relative module; patch both paths.
    monkeypatch.setattr(at, "ingest", at.ingest)
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.1\n%\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n")
    # Import-time binding: patch the name ingest will import.
    import types
    fake_mod = types.SimpleNamespace(run_shards=fake_run_shards)
    monkeypatch.setitem(sys.modules, "parallel", fake_mod)
    monkeypatch.setitem(sys.modules, "verified_pdf.parallel", fake_mod)

    results = at.ingest([pdf], tmp_path / "out", parallel=True, workers=2)
    assert called.get("workers") == 2
    assert results[0]["status"] == "VERIFIED"


def test_ingest_cli_exposes_workers_flag():
    parser = at.build_parser()
    args = parser.parse_args(
        ["ingest", "sample.pdf", "--out", "converted", "--parallel", "--workers", "2"]
    )
    assert args.cmd == "ingest"
    assert args.parallel is True
    assert args.workers == 2
