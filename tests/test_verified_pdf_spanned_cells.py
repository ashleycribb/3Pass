"""A cell spanning several columns (manual tables of contents: "LIST OF TABLES" across all three
columns) is written once in Markdown/CSV, not repeated per column, so M1 sees no added words."""
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

w = pytest.importorskip("scripts.verified_pdf.pdf_to_md_html")


def _item():
    title = NS(text="LIST OF TABLES", row_span=1, col_span=3, column_header=False,
               start_row_offset_idx=0, start_col_offset_idx=0)
    body = [NS(text=t, row_span=1, col_span=1, column_header=False, start_row_offset_idx=1,
               start_col_offset_idx=k) for k, t in enumerate(["2-1", "Lubrication", "2-54"])]
    return NS(data=NS(table_cells=[title, *body], grid=[[title, title, title], body]))


def test_spanned_cell_detected_and_written_once():
    item = _item()
    assert w.header_rows(item) == 0 and w.has_spans(item)
    assert w.origin_grid(item)[0] == ["LIST OF TABLES", "", ""]


def test_table_without_spans_left_to_docling():
    item = _item()
    item.data.table_cells = item.data.table_cells[1:]
    assert not w.has_spans(item)
