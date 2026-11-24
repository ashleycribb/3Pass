"""Tables with stacked headers (parts manuals: "Quantity Per Model" over three model numbers).
Docling's Markdown/CSV export folds them into one row and repeats header text; the writer now
writes the grid rows as they are, each cell once in its top-left slot, and B4 accepts exactly
that form. Changed values must still fail."""
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.verified_pdf import verify_conversion as vc  # noqa: E402

try:
    from scripts.verified_pdf import pdf_to_md_html as w
except ImportError:  # docling not installed
    w = None
import pytest  # noqa: E402

# 2 header rows: "Quantity Per Model" spans columns 1-2 of row 0
CELLS = [
    dict(text="Part", r=0, c=0, h=True), dict(text="Quantity Per Model", r=0, c=1, h=True),
    dict(text="", r=1, c=0, h=True), dict(text="12NL", r=1, c=1, h=True), dict(text="24NL", r=1, c=2, h=True),
    dict(text="Burner", r=2, c=0, h=False), dict(text="2", r=2, c=1, h=False), dict(text="4", r=2, c=2, h=False),
]


def _grid():
    by = {(x["r"], x["c"]): x for x in CELLS}
    span = by[(0, 1)]
    g = [[by[(0, 0)], span, span], [by[(1, 0)], by[(1, 1)], by[(1, 2)]], [by[(2, 0)], by[(2, 1)], by[(2, 2)]]]
    return [[{"text": x["text"], "start_row_offset_idx": x["r"], "start_col_offset_idx": x["c"],
              "column_header": x["h"]} for x in row] for row in g]


MD_ORIGIN = ("| Part | Quantity Per Model |  |\n|---|---|---|\n|  | 12NL | 24NL |\n| Burner | 2 | 4 |\n")
HTML = ("<table><tr><th>Part</th><th colspan='2'>Quantity Per Model</th></tr>"
        "<tr><th></th><th>12NL</th><th>24NL</th></tr><tr><td>Burner</td><td>2</td><td>4</td></tr></table>")


def _b4(md):
    d = {"tables": [{"data": {"grid": _grid()}}]}
    flags = []
    vc._check_b4(d, md, f"<html><body>{HTML}</body></html>", lambda *a, **k: flags.append(a[1]), Counter())
    return flags


def test_b4_accepts_cells_written_once():
    assert _b4(MD_ORIGIN) == []


def test_b4_still_accepts_repeated_span_form():
    md = "| Part | Quantity Per Model | Quantity Per Model |\n|---|---|---|\n|  | 12NL | 24NL |\n| Burner | 2 | 4 |\n"
    assert _b4(md) == []


def test_b4_catches_changed_value_in_origin_form():
    assert any("Markdown" in f for f in _b4(MD_ORIGIN.replace("| 2 |", "| 3 |")))


def test_b4_catches_folded_header():
    md = "| Part | Quantity Per Model - 12NL | Quantity Per Model - 24NL |\n|---|---|---|\n| Burner | 2 | 4 |\n"
    assert any("Markdown" in f for f in _b4(md))


@pytest.mark.skipif(w is None, reason="docling not installed")
def test_writer_writes_each_cell_once():
    item = NS(data=NS(grid=[[NS(text=c["text"], column_header=c["column_header"],
                                start_row_offset_idx=c["start_row_offset_idx"],
                                start_col_offset_idx=c["start_col_offset_idx"]) for c in row] for row in _grid()]))
    assert w.header_rows(item) == 2
    g = w.origin_grid(item)
    assert g[0] == ["Part", "Quantity Per Model", ""]
    assert w.markdown_table(g) + "\n" == MD_ORIGIN


@pytest.mark.skipif(w is None, reason="docling not installed")
def test_replace_markdown_tables_only_touches_selected_block():
    md = "text\n\n| a |\n|---|\n| 1 |\n\nmore\n\n| b |\n|---|\n| 2 |\n"
    out = w.replace_markdown_tables(md, {1: "| B |\n|---|\n| 2 |"})
    assert out == "text\n\n| a |\n|---|\n| 1 |\n\nmore\n\n| B |\n|---|\n| 2 |\n"
