"""Fail-first tests for table layout (B2/B3) and hyphen/T2 boundary cases.

Centered column headers, spanning group-heading rows (AMP:, Burndy:),
multi-line cells, superscript footnote marks, and CSV cells that merge a
header with the next group label are common in Technical Order tables.
A paragraph-final hyphen must not be glued onto the next heading, and a
word split across provenance boxes must not be flagged as a T2 fragment
when the compound is in the combined PDF boxes.

Do not edit these tests to make them pass.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "verified_pdf"))
sys.path.insert(0, str(ROOT / "scripts"))

import agent_tool as at  # noqa: E402
import verify_conversion as vc  # noqa: E402


def _checks(report):
    return {f["check"] for f in report["flags"]}


def _pdf_lines(path: Path, lines: list[str], width=500, height=300, start_y=80, leading=16):
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    y = start_y
    for line in lines:
        page.insert_text((40, y), line, fontsize=11, fontname="helv")
        y += leading
    doc.save(path)
    doc.close()
    return path


def _empty_docling(texts, tables):
    body_children = [{"$ref": t["self_ref"]} for t in texts] + [
        {"$ref": t["self_ref"]} for t in tables
    ]
    return {
        "body": {"self_ref": "#/body", "children": body_children},
        "texts": texts,
        "tables": tables,
        "pictures": [],
        "groups": [],
    }


def _write_conv(tmp_path, stem, doc, md, html, csv_rows=None):
    conv = tmp_path / stem
    conv.mkdir()
    (conv / f"{stem}.docling.json").write_text(json.dumps(doc), encoding="utf-8")
    (conv / f"{stem}.md").write_text(md, encoding="utf-8")
    (conv / f"{stem}.html").write_text(html, encoding="utf-8")
    if csv_rows is not None:
        (conv / "tables").mkdir()
        with (conv / "tables" / "table-1.csv").open("w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerows(csv_rows)
    return conv


# ---------------------------------------------------------------------------
# Self-check: do not invent a word by joining a hyphen to the next heading
# ---------------------------------------------------------------------------

def test_self_check_does_not_glue_hyphen_to_following_heading(tmp_path):
    """HTML tag stripping leaves 'clas- CHAPTER'. That must not become 'claschapter'."""
    pdf_path = _pdf_lines(
        tmp_path / "heading.pdf",
        [
            "classification of parts",
            "CHAPTER 5 PRINTED CIRCUIT BOARD REPAIR",
        ],
    )
    pdf = vc.Pdf(pdf_path)
    view = {
        "source_pdf": "synthetic-heading.pdf",
        "source_sha256": "a" * 64,
        "blocks": [
            {"id": "h", "status": "verified", "type": "heading", "text": "CHAPTER 5 PRINTED CIRCUIT BOARD REPAIR"},
            {"id": "p", "status": "verified", "type": "paragraph", "text": "classification of parts"},
        ],
        "document_flags": [],
    }
    md = "clas-\n\n# CHAPTER 5 PRINTED CIRCUIT BOARD REPAIR\n\nclassification of parts\n"
    htm = (
        "<p>clas-</p><h2>CHAPTER 5 PRINTED CIRCUIT BOARD REPAIR</h2>"
        "<p>classification of parts</p>"
    )
    leftover = at.self_check(view, md, htm, pdf)
    assert "claschapter" not in leftover
    assert "il2" not in leftover
    # "classification" is in the PDF; the heading must stay a real heading, not a compound.
    assert leftover == vc.Counter() or all(w in {"clas"} for w in leftover)


def test_self_check_still_joins_a_real_hyphenated_word(tmp_path):
    """'recom-mended' in markdown must still match PDF 'recommended' (existing contract)."""
    pdf_path = _pdf_lines(tmp_path / "hyphen.pdf", ["recommended practice"])
    pdf = vc.Pdf(pdf_path)
    view = {
        "source_pdf": "synthetic-hyphen.pdf",
        "source_sha256": "b" * 64,
        "blocks": [{"id": "p1", "status": "verified", "text": "recommended practice"}],
        "document_flags": [],
    }
    leftover = at.self_check(view, "recom-mended practice\n", "<p>recom-mended practice</p>", pdf)
    assert leftover == vc.Counter()


# ---------------------------------------------------------------------------
# T2: word split across provenance boxes
# ---------------------------------------------------------------------------

def test_t2_joins_word_split_across_provenance_boxes(tmp_path):
    """'Parag' + 'raph' across page boxes is 'Paragraph' in the PDF, not a T2 miss."""
    import pymupdf

    pdf_path = tmp_path / "splice.pdf"
    doc = pymupdf.open()
    p1 = doc.new_page(width=400, height=200)
    p1.insert_text((40, 80), "Use procedures described in Para-", fontsize=11, fontname="helv")
    p2 = doc.new_page(width=400, height=200)
    p2.insert_text((40, 80), "graph 4.10.2 for connecting.", fontsize=11, fontname="helv")
    doc.save(pdf_path)
    doc.close()

    text = "Use procedures described in Paragraph 4.10.2 for connecting."
    # Split mid-word the way a page-break charspan often does.
    split_at = text.index("Paragraph") + len("Parag")
    assert text[:split_at].endswith("Parag") and text[split_at:].startswith("raph")
    texts = [
        {
            "self_ref": "#/texts/0",
            "text": text,
            "orig": text,
            "label": "text",
            "prov": [
                {
                    "page_no": 1,
                    "bbox": {"l": 30, "t": 60, "r": 390, "b": 100, "coord_origin": "TOPLEFT"},
                    "charspan": [0, split_at],
                },
                {
                    "page_no": 2,
                    "bbox": {"l": 30, "t": 60, "r": 390, "b": 100, "coord_origin": "TOPLEFT"},
                    "charspan": [split_at, len(text)],
                },
            ],
        }
    ]
    docling = _empty_docling(texts, [])
    md = text + "\n"
    html = f"<p>{text}</p>"
    conv = _write_conv(tmp_path, "splice", docling, md, html)
    report = vc.verify(pdf_path, conv, only=("text",), write=False)
    t2 = [f for f in report["flags"] if f["check"] == "T2"]
    assert t2 == [], t2


# ---------------------------------------------------------------------------
# B2/B3: Technical Order table geometry and merged CSV group labels
# ---------------------------------------------------------------------------

def _table_4_2_fixture(tmp_path):
    """Synthetic table with centered headers, AMP:/Burndy: group rows, stacked PNs, asterisks."""
    import pymupdf

    pdf_path = tmp_path / "table42.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)

    # Centered headers (x is the left of the text, chosen so the header box sits
    # to the right of left-aligned data — the B2 failure mode on Table 4-2).
    headers = [
        (110, 80, "Splice wire size range"),
        (270, 80, "Hand crimping tools"),
        (440, 80, "Wire stripping lengths"),
    ]
    for x, y, t in headers:
        page.insert_text((x, y), t, fontsize=10, fontname="helv")

    # Group heading AMP: left-aligned with data in the tools column.
    page.insert_text((240, 100), "AMP:", fontsize=10, fontname="helv")

    # Row: 22-18 (Red) | stacked 48430 / 49556 * | 3/16 inch
    page.insert_text((76, 120), "22-18 (Red)", fontsize=10, fontname="helv")
    page.insert_text((240, 116), "48430", fontsize=10, fontname="helv")
    page.insert_text((240, 128), "49556", fontsize=10, fontname="helv")
    # superscript asterisk footnote mark
    page.insert_text((272, 124), "*", fontsize=7, fontname="helv")
    page.insert_text((384, 120), "3/16 inch", fontsize=10, fontname="helv")

    page.insert_text((240, 150), "Burndy:", fontsize=10, fontname="helv")

    page.insert_text((76, 170), "12-10 (White)", fontsize=10, fontname="helv")
    page.insert_text((240, 166), "MR8-1A", fontsize=10, fontname="helv")
    page.insert_text((240, 178), "MR8-33S", fontsize=10, fontname="helv")
    page.insert_text((272, 174), "*", fontsize=7, fontname="helv")
    page.insert_text((384, 170), "5/16 inch", fontsize=10, fontname="helv")

    doc.save(pdf_path)
    doc.close()

    def word_boxes(needle_prefix=None):
        """Return (l,t,r,b,word) in TOPLEFT for all words."""
        d = pymupdf.open(pdf_path)
        out = []
        for w in d[0].get_text("words"):
            out.append((w[0], w[1], w[2], w[3], w[4]))
        d.close()
        return out

    words = word_boxes()

    def box_for(parts):
        hits = []
        remaining = list(parts)
        for w in words:
            if remaining and w[4] == remaining[0]:
                hits.append(w)
                remaining = remaining[1:]
        if remaining:
            raise AssertionError(f"missing words {remaining} in { [w[4] for w in words] }")
        l = min(h[0] for h in hits)
        t = min(h[1] for h in hits)
        r = max(h[2] for h in hits)
        b = max(h[3] for h in hits)
        return {"l": l, "t": t, "r": r, "b": b, "coord_origin": "TOPLEFT"}

    def cell(r, c, text, parts, column_header=False, row_section=False):
        return {
            "bbox": box_for(parts),
            "row_span": 1,
            "col_span": 1,
            "start_row_offset_idx": r,
            "end_row_offset_idx": r + 1,
            "start_col_offset_idx": c,
            "end_col_offset_idx": c + 1,
            "text": text,
            "column_header": column_header,
            "row_header": False,
            "row_section": row_section,
        }

    cells = [
        cell(0, 0, "Splice wire size range", ["Splice", "wire", "size", "range"], column_header=True),
        cell(0, 1, "Hand crimping tools", ["Hand", "crimping", "tools"], column_header=True),
        cell(0, 2, "Wire stripping lengths", ["Wire", "stripping", "lengths"], column_header=True),
        cell(1, 1, "AMP:", ["AMP:"], column_header=True, row_section=True),
        cell(2, 0, "22-18 (Red)", ["22-18", "(Red)"]),
        cell(2, 1, "48430 49556 *", ["48430", "49556", "*"]),
        cell(2, 2, "3/16 inch", ["3/16", "inch"]),
        cell(3, 1, "Burndy:", ["Burndy:"], row_section=True),
        cell(4, 0, "12-10 (White)", ["12-10", "(White)"]),
        cell(4, 1, "MR8-1A * MR8-33S", ["MR8-1A", "MR8-33S", "*"]),
        cell(4, 2, "5/16 inch", ["5/16", "inch"]),
    ]
    # table region
    boxes = [c["bbox"] for c in cells]
    region = {
        "l": min(b["l"] for b in boxes) - 4,
        "t": max(b["b"] for b in boxes) + 4,  # unused; prov uses TOPLEFT below
        "r": max(b["r"] for b in boxes) + 4,
        "b": min(b["t"] for b in boxes) - 4,
        "coord_origin": "TOPLEFT",
    }
    # prov bbox in TOPLEFT: l, t (top), r, b (bottom)
    prov_bbox = {
        "l": min(b["l"] for b in boxes) - 4,
        "t": min(b["t"] for b in boxes) - 4,
        "r": max(b["r"] for b in boxes) + 4,
        "b": max(b["b"] for b in boxes) + 4,
        "coord_origin": "TOPLEFT",
    }
    table = {
        "self_ref": "#/tables/0",
        "prov": [{"page_no": 1, "bbox": prov_bbox, "charspan": [0, 0]}],
        "captions": [],
        "data": {"table_cells": cells, "grid": []},
        "label": "table",
    }
    texts = [
        {
            "self_ref": "#/texts/0",
            "text": "Table 4-2. Pre-Insulated Copper Splices and Crimping Tools",
            "orig": "Table 4-2. Pre-Insulated Copper Splices and Crimping Tools",
            "label": "caption",
            "prov": [{"page_no": 1, "bbox": prov_bbox, "charspan": [0, 0]}],
        }
    ]
    # Caption is not on the PDF; don't include it as a reachable text that T2 would check
    # when only=("tables",). Keep texts empty for table-only tests.
    docling = _empty_docling([], [table])

    model_rows = [
        ["Splice wire size range", "Hand crimping tools", "Wire stripping lengths"],
        ["", "AMP:", ""],
        ["22-18 (Red)", "48430 49556 *", "3/16 inch"],
        ["", "Burndy:", ""],
        ["12-10 (White)", "MR8-1A * MR8-33S", "5/16 inch"],
    ]
    # Docling-style CSV merges the first group label into the header cell.
    csv_rows = [
        ["Splice wire size range.", "Hand crimping tools.AMP:", "Wire stripping lengths."],
        ["22-18 (Red)", "48430 49556 *", "3/16 inch"],
        ["", "Burndy:", ""],
        ["12-10 (White)", "MR8-1A * MR8-33S", "5/16 inch"],
    ]
    md_lines = [
        "| Splice wire size range | Hand crimping tools | Wire stripping lengths |",
        "| --- | --- | --- |",
        "|  | AMP: |  |",
        "| 22-18 (Red) | 48430 49556 * | 3/16 inch |",
        "|  | Burndy: |  |",
        "| 12-10 (White) | MR8-1A * MR8-33S | 5/16 inch |",
        "",
    ]
    md = "\n".join(md_lines)
    html = (
        "<table>"
        + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in model_rows)
        + "</table>"
    )
    conv = _write_conv(tmp_path, "table42", docling, md, html, csv_rows)
    return pdf_path, conv


def test_b2_accepts_centered_headers_and_group_heading_rows(tmp_path):
    """Centered headers and AMP:/Burndy: group rows must not fail B2 column alignment."""
    pdf_path, conv = _table_4_2_fixture(tmp_path)
    report = vc.verify(pdf_path, conv, only=("tables",), write=False)
    b2 = [f for f in report["flags"] if f["check"] == "B2"]
    assert b2 == [], b2


def test_b3_accepts_csv_that_merges_header_and_group_label(tmp_path):
    """CSV 'Hand crimping tools.AMP:' still contains the model cells 'Hand crimping tools' and 'AMP:'."""
    pdf_path, conv = _table_4_2_fixture(tmp_path)
    report = vc.verify(pdf_path, conv, only=("tables",), write=False)
    b3 = [f for f in report["flags"] if f["check"] == "B3"]
    assert b3 == [], b3


def test_b1_accepts_stacked_part_numbers_and_superscript_asterisk(tmp_path):
    """A cell with two stacked part numbers and a footnote asterisk must match the PDF box."""
    pdf_path, conv = _table_4_2_fixture(tmp_path)
    report = vc.verify(pdf_path, conv, only=("tables",), write=False)
    b1 = [f for f in report["flags"] if f["check"] == "B1"]
    assert b1 == [], b1


def test_b3_still_flags_a_csv_cell_whose_value_is_not_in_the_model(tmp_path):
    """Fail closed: a substituted CSV value is still a B3 miss."""
    pdf_path, conv = _table_4_2_fixture(tmp_path)
    p = conv / "tables" / "table-1.csv"
    rows = list(csv.reader(p.open(encoding="utf-8")))
    # Replace a data cell with a value that cannot be partitioned into model cells.
    rows[1][1] = "NOT-IN-MODEL"
    with p.open("w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)
    report = vc.verify(pdf_path, conv, only=("tables",), write=False)
    assert "B3" in _checks(report)
