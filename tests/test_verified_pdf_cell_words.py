"""B1 cell text: a word belongs to a cell when most of its letters lie inside the cell box.
Glyph fragments of neighbouring lines that merely touch the box must not count (equipment-manual
tables with tight leading), and a word must not be cut at the box edge. Real errors still fail."""
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.verified_pdf import verify_conversion as vc  # noqa: E402

FX = ROOT / "tests" / "fixtures" / "verified_pdf"
PDF = FX / "sample_paper.pdf"
STEM = "sample_paper"


@pytest.fixture
def conv(tmp_path):
    d = tmp_path / STEM
    shutil.copytree(FX / "converted" / STEM, d)
    return d


def _cell(d, text):
    return next(c for c in d["tables"][0]["data"]["table_cells"] if c["text"] == text)


def _edit_cell(conv, text, fn):
    p = conv / f"{STEM}.docling.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    c = _cell(d, text)
    fn(c["bbox"])
    for row in d["tables"][0]["data"]["grid"]:      # keep grid copy in sync
        for g in row:
            if g["text"] == text:
                g["bbox"] = dict(c["bbox"])
    p.write_text(json.dumps(d), encoding="utf-8")
    return d


def _b1(conv):
    return [f for f in vc.verify(PDF, conv)["flags"] if f["check"] == "B1"]


def _row_pitch(d):
    rows = sorted({round(min(c["bbox"]["t"], c["bbox"]["b"]), 1) for c in d["tables"][0]["data"]["table_cells"]})
    return min(b - a for a, b in zip(rows, rows[1:]))


def test_box_touching_neighbour_rows_is_not_a_mismatch(conv):
    d = json.loads((conv / f"{STEM}.docling.json").read_text(encoding="utf-8"))
    grow = 0.45 * _row_pitch(d)          # reaches into the next rows' glyphs, not their centres

    def widen(bb):
        hi, lo = (("t", "b") if bb["t"] > bb["b"] else ("b", "t")) if bb["coord_origin"] == "BOTTOMLEFT" else (("b", "t") if bb["b"] > bb["t"] else ("t", "b"))
        bb[hi] += grow
        bb[lo] -= grow
    _edit_cell(conv, "412", widen)
    assert _b1(conv) == []


def test_box_cutting_a_word_keeps_the_whole_word(conv):
    d = json.loads((conv / f"{STEM}.docling.json").read_text(encoding="utf-8"))
    c = _cell(d, "Screening")
    w = c["bbox"]["r"] - c["bbox"]["l"]
    _edit_cell(conv, "Screening", lambda bb: bb.update(r=bb["l"] + 0.7 * w))
    assert _b1(conv) == []


def test_box_covering_part_of_a_word_keeps_the_word_in_that_cell(conv):
    # B1 checks which words belong to which cell; box tightness alone is not a wording error.
    d = json.loads((conv / f"{STEM}.docling.json").read_text(encoding="utf-8"))
    c = _cell(d, "Screening")
    w = c["bbox"]["r"] - c["bbox"]["l"]
    _edit_cell(conv, "Screening", lambda bb: bb.update(r=bb["l"] + 0.3 * w))
    assert _b1(conv) == []


def test_wrong_cell_value_is_still_caught(conv):
    p = conv / f"{STEM}.docling.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    for c in d["tables"][0]["data"]["table_cells"] + [g for r in d["tables"][0]["data"]["grid"] for g in r]:
        if c["text"] == "412":
            c["text"] = "421"
    p.write_text(json.dumps(d), encoding="utf-8")
    assert any("421" in f["detail"] for f in _b1(conv))
