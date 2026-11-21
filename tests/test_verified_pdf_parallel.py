"""Tests for the parallel paths (scripts/verified_pdf/parallel.py: page shards and anchor-and-workers).

The key property: running the check groups separately and merging them gives exactly the
serial verification (same flag IDs in the same order, same pass counts), on clean and on
corrupted conversions. The end-to-end test (needs Docling) checks that the parallel
conversion yields the same verified blocks as the serial one.
"""
import importlib.util
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.source_router import verified_pdf_available  # noqa: E402
from scripts.verified_pdf import agent_tool, parallel, verify_conversion as vc  # noqa: E402

FX = ROOT / "tests" / "fixtures" / "verified_pdf"
PDF = FX / "sample_paper.pdf"
CONVERTED = FX / "converted" / "sample_paper"
STEM = "sample_paper"


@pytest.fixture
def conv(tmp_path):
    d = tmp_path / STEM
    shutil.copytree(CONVERTED, d)
    return d


def _doc(conv):
    return json.loads((conv / f"{STEM}.docling.json").read_text(encoding="utf-8"))


def _save(conv, d):
    (conv / f"{STEM}.docling.json").write_text(json.dumps(d), encoding="utf-8")


def _merged(conv):
    parts = [vc.verify(PDF, conv, only=[g], write=False) for g in vc.GROUPS]
    flags = vc.sort_flags([f for p in parts for f in p["flags"]])
    passed = sum((Counter(p["checks_passed"]) for p in parts), Counter())
    return [f["id"] for f in flags], dict(passed)


def _serial(conv):
    r = vc.verify(PDF, conv)
    return [f["id"] for f in r["flags"]], r["checks_passed"]


def _change_word(conv):
    d = _doc(conv)
    t = next(t for t in d["texts"] if "Doctoral students" in (t.get("text") or ""))
    t["text"] = t["orig"] = t["text"].replace("exactly", "approximately")
    _save(conv, d)


def _change_cell(conv):
    d = _doc(conv)
    for c in d["tables"][0]["data"]["table_cells"] + [c for r in d["tables"][0]["data"]["grid"] for c in r]:
        if c["text"] == "412":
            c["text"] = "421"
    _save(conv, d)


def _blank_figure(conv):
    from PIL import Image
    uri = _doc(conv)["pictures"][0]["image"]["uri"]
    img = Image.open(conv / uri)
    Image.new("RGB", img.size, "white").save(conv / uri)


def _drop_paragraph(conv):
    d = _doc(conv)
    i = next(i for i, t in enumerate(d["texts"]) if "Because the chart is drawn" in (t.get("text") or ""))
    d["body"]["children"] = [c for c in d["body"]["children"] if c["$ref"] != f"#/texts/{i}"]
    for g in d.get("groups", []):
        g["children"] = [c for c in g.get("children", []) if c["$ref"] != f"#/texts/{i}"]
    _save(conv, d)


def _invent_md_word(conv):
    p = conv / f"{STEM}.md"
    p.write_text(p.read_text(encoding="utf-8") + "\nInventedword appears here.\n", encoding="utf-8")


@pytest.mark.parametrize("mutate", [None, _change_word, _change_cell, _blank_figure, _drop_paragraph, _invent_md_word])
def test_check_groups_union_equals_serial_verification(conv, mutate):
    if mutate:
        mutate(conv)
    merged, serial = _merged(conv), _serial(conv)
    assert merged == serial
    if mutate:
        assert merged[0], "the corruption must be flagged"


def test_every_check_belongs_to_exactly_one_group():
    assert set(vc.CHECK_GROUP.values()) == set(vc.GROUPS)
    assert set(vc.CHECK_ORDER) <= set(vc.CHECK_GROUP)


def test_precomputed_report_gives_same_view(conv):
    a = agent_tool.build_verified_view(PDF, conv)
    report = vc.verify(PDF, conv)
    b = agent_tool.build_verified_view(PDF, conv, report=report)
    assert a["blocks"] == b["blocks"] and a["document_status"] == b["document_status"]


def test_page_runs_and_iou():
    assert parallel._runs([7, 10, 16, 17, 17, 20]) == [(7, 7), (10, 10), (16, 17), (20, 20)]
    assert parallel._iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert parallel._iou((0, 10, 10, 0), (0, 0, 10, 10)) == 1.0      # bottom-left tuples (b < t)
    assert parallel._iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0


@pytest.mark.skipif(importlib.util.find_spec("docling_core") is None, reason="docling-core not installed")
def test_unmatched_table_fails_closed():
    from docling_core.types.doc import DoclingDocument
    d = DoclingDocument.model_validate_json((CONVERTED / f"{STEM}.docling.json").read_text(encoding="utf-8"))
    warnings = []
    parallel.attach_tables(d, [], warnings)          # table worker returned nothing
    assert d.tables[0].data.table_cells == [] and warnings


@pytest.mark.skipif(not verified_pdf_available(), reason="Docling not installed (requirements-verified-pdf.txt)")
@pytest.mark.parametrize("mode", ["shards", "anchors"])
def test_parallel_conversion_matches_serial(tmp_path, mode):
    serial = agent_tool.ingest([PDF], tmp_path / "serial")[0]
    fn = parallel.run_shards if mode == "shards" else parallel.run
    r = fn(PDF, tmp_path / mode, workers=2)
    assert r["view"]["document_status"] == serial["status"]
    s_view = json.loads(Path(serial["verified_json"]).read_text(encoding="utf-8"))
    strip = lambda v: [{k: b.get(k) for k in ("type", "status", "text", "rows", "pages")} for b in v["blocks"]]
    assert strip(r["view"]) == strip(s_view)
    full = vc.verify(PDF, tmp_path / mode / STEM, write=False)  # merged report == one serial pass
    assert [f["id"] for f in full["flags"]] == [f["id"] for f in r["report"]["flags"]]
    assert dict(full["checks_passed"]) == r["report"]["checks_passed"]
    assert (tmp_path / mode / STEM / "anchors.json").exists()
