"""B2 row alignment for wrapped entries (equipment-manual tables of contents): the number sits on
line 1, the title spans lines 1-2, the page number sits on line 2. The cells line up through the
title. A cell taken from a neighbouring row still fails."""
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


def _b2(conv):
    return [f for f in vc.verify(PDF, conv, write=False)["flags"] if f["check"] == "B2"]


def _edit(conv, fn):
    p = conv / f"{STEM}.docling.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    fn(d["tables"][0]["data"]["table_cells"])
    p.write_text(json.dumps(d), encoding="utf-8")


def _row(cells, r):
    return sorted((c for c in cells if c["start_row_offset_idx"] == r), key=lambda c: c["start_col_offset_idx"])


def _height(bb):
    return abs(bb["t"] - bb["b"])


def _shift(bb, dy):
    # dy in top-left terms (positive = further down the page)
    s = -dy if bb["coord_origin"] == "BOTTOMLEFT" else dy
    bb["t"] += s
    bb["b"] += s


def _grow_down(bb, dy):
    if bb["coord_origin"] == "BOTTOMLEFT":
        bb[min(("t", "b"), key=lambda k: bb[k])] -= dy
    else:
        bb[max(("t", "b"), key=lambda k: bb[k])] += dy


def test_wrapped_row_lines_up(conv):
    def wrap(cells):
        row = _row(cells, 1)
        h = _height(row[0]["bbox"])
        _grow_down(row[1]["bbox"], h * 1.1)     # title wraps onto a second line
        for c in row[2:]:
            _shift(c["bbox"], h * 1.1)           # remaining cells sit on that second line
    _edit(conv, wrap)
    assert not any("row 1 do not line up" in f["detail"] for f in _b2(conv))


def test_cell_from_another_row_still_fails(conv):
    def move(cells):
        row = _row(cells, 1)
        _shift(row[-1]["bbox"], 3 * _height(row[0]["bbox"]))
    _edit(conv, move)
    assert any("row 1 do not line up" in f["detail"] for f in _b2(conv))
