"""B1 gives each word in a table to exactly one cell. Model cell boxes a few points off the
glyphs (equipment manuals), or a box tall enough to cover the next line, must not cause a
mismatch; a value in the wrong cell, or swapped between cells, still must."""
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


def _edit(conv, fn):
    p = conv / f"{STEM}.docling.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    tb = d["tables"][0]["data"]
    fn(tb["table_cells"])
    by = {(c["start_row_offset_idx"], c["start_col_offset_idx"]): c for c in tb["table_cells"]}
    for row in tb["grid"]:
        for g in row:
            src = by[(g["start_row_offset_idx"], g["start_col_offset_idx"])]
            g["text"], g["bbox"] = src["text"], dict(src["bbox"])
    p.write_text(json.dumps(d), encoding="utf-8")


def _b1(conv):
    return [f for f in vc.verify(PDF, conv, write=False)["flags"] if f["check"] == "B1"]


def _cell(cells, text):
    return next(c for c in cells if c["text"] == text)


def _down(bb, dy):  # move a box down the page by dy points
    s = -dy if bb["coord_origin"] == "BOTTOMLEFT" else dy
    bb["t"] += s
    bb["b"] += s


def test_box_offset_from_glyphs_still_matches(conv):
    def shift(cells):
        bb = _cell(cells, "412")["bbox"]
        _down(bb, 0.6 * abs(bb["t"] - bb["b"]))      # most glyph centres now outside the box
    _edit(conv, shift)
    assert _b1(conv) == []


def test_box_covering_next_line_does_not_steal_it(conv):
    def grow(cells):
        c = _cell(cells, "412")
        below = next(x for x in cells if x["start_col_offset_idx"] == c["start_col_offset_idx"]
                     and x["start_row_offset_idx"] == c["start_row_offset_idx"] + 1)
        bb, nb = c["bbox"], below["bbox"]
        if bb["coord_origin"] == "BOTTOMLEFT":
            bb["b"] = min(nb["b"], nb["t"])
        else:
            bb["b"] = max(nb["b"], nb["t"])
    _edit(conv, grow)
    assert _b1(conv) == []


def test_swapped_values_are_caught(conv):
    def swap(cells):
        a = _cell(cells, "412")
        b = next(x for x in cells if x["start_col_offset_idx"] == a["start_col_offset_idx"]
                 and x["start_row_offset_idx"] == a["start_row_offset_idx"] + 1)
        a["text"], b["text"] = b["text"], a["text"]
    _edit(conv, swap)
    assert len(_b1(conv)) == 2


def test_value_moved_to_wrong_cell_is_caught(conv):
    def move(cells):
        a = _cell(cells, "412")
        b = next(x for x in cells if x["start_col_offset_idx"] == a["start_col_offset_idx"]
                 and x["start_row_offset_idx"] == a["start_row_offset_idx"] + 1)
        b["text"] = b["text"] + " " + a["text"]
    _edit(conv, move)
    assert any("412" in f["detail"] for f in _b1(conv))
