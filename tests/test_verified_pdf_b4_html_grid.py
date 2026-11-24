"""B4 compares the HTML table slot by slot with the verified grid. Adjacent cells with the same
value (equipment-manual change sheets: "2-125 thru 2-128" in both columns) must not be mistaken
for one merged cell, merged cells must still match, and any changed or dropped cell must fail."""
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.verified_pdf import verify_conversion as vc  # noqa: E402

GRID = [["Remove pages", "Remove pages", "Insert pages"],
        ["2-125 thru 2-128", "2-125 thru 2-128", "none"]]
MD = "| Remove pages | Remove pages | Insert pages |\n|---|---|---|\n| 2-125 thru 2-128 | 2-125 thru 2-128 | none |\n"


def _run(html_table):
    d = {"tables": [{"data": {"grid": [[{"text": t} for t in row] for row in GRID]}}]}
    flags, passed = [], Counter()
    vc._check_b4(d, MD, f"<html><body>{html_table}</body></html>", lambda *a, **k: flags.append(a[1]), passed)
    return flags


def _tr(cells):
    return "<tr>" + "".join(cells) + "</tr>"


def test_repeated_adjacent_values_written_separately_pass():
    t = "<table>" + "".join(_tr(f"<td>{x}</td>" for x in row) for row in GRID) + "</table>"
    assert _run(t) == []


def test_spanned_cells_written_merged_pass():
    t = ("<table>" + _tr(['<th colspan="2">Remove pages</th>', "<th>Insert pages</th>"])
         + _tr(['<td colspan="2">2-125 thru 2-128</td>', "<td>none</td>"]) + "</table>")
    assert _run(t) == []


def test_rowspan_fills_rows_below():
    global GRID, MD
    old = GRID, MD
    GRID = [["a", "x"], ["a", "y"]]
    MD = "| a | x |\n|---|---|\n| a | y |\n"
    try:
        t = "<table>" + _tr(['<td rowspan="2">a</td>', "<td>x</td>"]) + _tr(["<td>y</td>"]) + "</table>"
        assert _run(t) == []
    finally:
        GRID, MD = old


def test_dropped_duplicate_cell_is_caught():
    t = ("<table>" + _tr(f"<td>{x}</td>" for x in GRID[0])
         + _tr(["<td>2-125 thru 2-128</td>", "<td>none</td>"]) + "</table>")
    assert any("HTML" in f for f in _run(t))


def test_changed_cell_is_caught():
    t = ("<table>" + _tr(f"<td>{x}</td>" for x in GRID[0])
         + _tr(["<td>2-125 thru 2-128</td>", "<td>2-125 thru 2-129</td>", "<td>none</td>"]) + "</table>")
    assert any("HTML" in f for f in _run(t))
