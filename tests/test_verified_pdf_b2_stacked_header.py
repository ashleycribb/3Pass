"""B2 with a stacked header (parts lists): "#", "Description", "Item Number" are centred beside
"Quantity Per Model" over model numbers, so the model may place them in either header row. A
header cell that is the only header text in its column is left out of the row test; columns
with header text in more than one row, and body rows, are still checked."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.verified_pdf import verify_conversion as vc  # noqa: E402


def cell(text, r, c, header=True, cs=1):
    return {"text": text, "start_row_offset_idx": r, "start_col_offset_idx": c, "col_span": cs,
            "row_span": 1, "column_header": header}


def parts_table():
    return [cell("#", 1, 0), cell("Description", 1, 1), cell("Item Number", 0, 2),
            cell("Quantity Per Model", 0, 3, cs=2), cell("12NL", 1, 3), cell("24NL", 1, 4),
            cell("1", 2, 0, False), cell("Trivet", 2, 1, False), cell("3511015096", 2, 2, False),
            cell("2", 2, 3, False), cell("4", 2, 4, False)]


def texts(cells, ids):
    return sorted(c["text"] for c in cells if id(c) in ids)


def test_lone_header_cells_are_exempt():
    cells = parts_table()
    assert texts(cells, vc._lone_header_cells(cells)) == ["#", "Description", "Item Number"]


def test_column_with_two_header_rows_is_not_exempt():
    cells = parts_table() + [cell("Part No.", 1, 2)]       # column 2 now has header text in both rows
    assert "Item Number" not in texts(cells, vc._lone_header_cells(cells))


def test_single_header_row_has_no_exemption():
    cells = [cell("A", 0, 0), cell("B", 0, 1), cell("1", 1, 0, False), cell("2", 1, 1, False)]
    assert vc._lone_header_cells(cells) == set()


def test_body_cells_never_exempt():
    cells = parts_table()
    assert not any(c["start_row_offset_idx"] == 2 for c in cells if id(c) in vc._lone_header_cells(cells))
