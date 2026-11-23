"""Generate synthetic technical-manual PDFs for tests (invented content only).

Call the `build_*` functions from tests and write into a temporary directory.
Do not commit the resulting PDFs; real manuals must not enter the repository.
"""
from __future__ import annotations

from pathlib import Path

import pymupdf

PAGE_W, PAGE_H = 612.0, 792.0
HEADER = "TM 99-01"
# Printed page numbers (change per page; same y-band).
PRINTED = {1: "2-1", 2: "2-2", 3: "2-3"}


def _rect_dict(rect) -> dict:
    return {
        "l": float(rect.x0),
        "t": float(rect.y0),
        "r": float(rect.x1),
        "b": float(rect.y1),
        "coord_origin": "TOPLEFT",
    }


def _search(page, text):
    hits = page.search_for(text)
    if not hits:
        raise RuntimeError(f"text not found on page {page.number + 1}: {text!r}")
    return hits[0]


def _add_folio(page, page_no: int) -> dict:
    """Repeating header/footer in a shared y-band; x flips with odd/even pages."""
    odd = page_no % 2 == 1
    hx = 480 if odd else 52
    fx = 530 if odd else 52
    page.insert_text((hx, 40), HEADER, fontsize=9, fontname="helv")
    page.insert_text((fx, 768), PRINTED[page_no], fontsize=9, fontname="helv")
    return {
        "header": {"text": HEADER, "bbox": _rect_dict(_search(page, HEADER)), "odd": odd},
        "footer": {"text": PRINTED[page_no], "bbox": _rect_dict(_search(page, PRINTED[page_no])), "odd": odd},
    }


def _textbox(page, rect, text, size=10, font="helv"):
    # Use the allocated wrap box so a multi-line paragraph's provenance
    # covers every line, not just the first search hit.
    page.insert_textbox(rect, text, fontsize=size, fontname=font)
    return rect


def build_manual(out_dir: Path) -> dict:
    """Three-page invented technical manual with a grouped table, folios, and body citations."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / "manual.pdf"
    doc = pymupdf.open()
    # Access pages via the document (a list of new_page() return values can
    # drop the parent pointer in some PyMuPDF builds).
    for _ in range(3):
        doc.new_page(width=PAGE_W, height=PAGE_H)
    folios = {}
    texts = {}

    # --- page 1 ---
    p1 = doc[0]
    folios[1] = _add_folio(p1, 1)
    p1.insert_text((72, 90), "Sample Technical Manual", fontsize=18, fontname="hebo")
    texts["title"] = {"page": 1, "text": "Sample Technical Manual", "bbox": _rect_dict(_search(p1, "Sample Technical Manual")), "label": "section_header", "level": 1}
    p1.insert_text((72, 130), "1.1 General", fontsize=13, fontname="hebo")
    texts["s11"] = {"page": 1, "text": "1.1 General", "bbox": _rect_dict(_search(p1, "1.1 General")), "label": "section_header", "level": 2}
    mid = (
        "This invented manual is used only for tests. Procedures also appear in TM 12-34, "
        "which is a mid-page reference and not a running header."
    )
    texts["p1_body"] = {"page": 1, "text": mid, "bbox": _rect_dict(_textbox(p1, pymupdf.Rect(72, 150, 540, 230), mid)), "label": "text"}

    # --- page 2: figure caption, table, footnote, nearby heading, citing paragraphs ---
    p2 = doc[1]
    folios[2] = _add_folio(p2, 2)
    p2.insert_text((72, 232), "Figure 1-1. Overview.", fontsize=10, fontname="helv")
    texts["fig_cap"] = {"page": 2, "text": "Figure 1-1. Overview.", "bbox": _rect_dict(_search(p2, "Figure 1-1. Overview.")), "label": "caption"}
    p2.insert_text((72, 250), "Table 1-1. Crimping tools.", fontsize=10, fontname="hebo")
    texts["tab_cap"] = {"page": 2, "text": "Table 1-1. Crimping tools.", "bbox": _rect_dict(_search(p2, "Table 1-1. Crimping tools.")), "label": "caption"}

    # Ruled table. Row 4 is stacked (two lines in the tool cell) and taller.
    headers = ["Wire size", "Tool", "Length"]
    # rows: list of (row_kind, cells) where cells is 3 strings; stacked tool uses "\n"
    data_rows = [
        ("header", headers),
        ("group_a", ["", "Maker-A:", ""]),
        ("data", ["22-18 (Red)", "11111", "3/16"]),
        ("data", ["16-14 (Blue)", "22222", "3/16"]),
        ("stacked", ["20-16 (Red)", "55555\n66666", "1/4"]),
        ("data", ["18-16 (Blue)", "55555 66666", "5/16"]),
        ("false_page", ["14-12 (Blue)", "77777", "5/16"]),  # PDF shows 77777; model will claim 55555 66666
        ("group_b", ["", "Maker-B:", ""]),
        ("data", ["12-10 (Yellow)", "44444", "9/32"]),
        ("tight", ["10-8 (White)", "88888", "1/4"]),  # model bbox will be too tight so the cell is held
        ("span", ["All lengths are inches.", "", ""]),
    ]
    col_w = [140.0, 170.0, 120.0]
    x0, y0 = 72.0, 262.0
    row_heights = []
    for kind, _ in data_rows:
        row_heights.append(34.0 if kind == "stacked" else 18.0)

    cells_meta = []
    y = y0
    for r, ((kind, vals), rh) in enumerate(zip(data_rows, row_heights)):
        x = x0
        for c, val in enumerate(vals):
            rect = pymupdf.Rect(x, y, x + col_w[c], y + rh)
            p2.draw_rect(rect, color=(0, 0, 0), width=0.5)
            display = val
            font = "hebo" if kind in ("header", "group_a", "group_b") else "helv"
            if kind == "span":
                if c == 0:
                    p2.insert_textbox(pymupdf.Rect(x + 4, y + 2, x + sum(col_w) - 4, y + rh),
                                      display, fontsize=9, fontname=font)
            elif "\n" in display:
                p2.insert_text((x + 5, y + 13), display.split("\n")[0], fontsize=9, fontname=font)
                p2.insert_text((x + 5, y + 26), display.split("\n")[1], fontsize=9, fontname=font)
            elif display:
                p2.insert_text((x + 5, y + 13), display, fontsize=9, fontname=font)
            # Default model text and bbox from the cell rectangle (inset slightly).
            model_text = display.replace("\n", " ")
            model_bbox = _rect_dict(rect + (2, 1, -2, -1))  # inset
            if kind == "tight" and c == 1:
                # Too tight to capture the painted glyphs; box-text will not match "88888".
                model_bbox = _rect_dict(pymupdf.Rect(rect.x0 + 2, rect.y0 + 1, rect.x0 + 8, rect.y0 + 6))
            if kind == "false_page" and c == 1:
                model_text = "55555 66666"  # appears as one line in the next-up row, not in this box
            if kind == "span":
                model_text = "All lengths are inches." if c == 0 else ""
            no_bbox = kind in ("group_a", "group_b") and c != 1
            cells_meta.append({
                "row": r,
                "col": c,
                "kind": kind,
                "text": model_text,
                "pdf_text": display,
                "bbox": None if no_bbox else model_bbox,
                "drawn": _rect_dict(rect),
                "row_span": 1,
                "col_span": 3 if (kind == "span" and c == 0) else 1,
                "column_header": kind == "header",
                "row_section": kind in ("group_a", "group_b"),
                "cover": kind == "span" and c > 0,  # covered by colspan; omitted from table_cells
            })
            x += col_w[c]
        y += rh
    table_box = _rect_dict(pymupdf.Rect(x0, y0, x0 + sum(col_w), y))

    p2.insert_text((72, y + 14), "* Remove stop before use.", fontsize=9, fontname="helv")
    texts["footnote"] = {
        "page": 2,
        "text": "* Remove stop before use.",
        "bbox": _rect_dict(_search(p2, "* Remove stop before use.")),
        "label": "text",
    }
    # Immediate neighbour below the footnote (padding must be clamped against this).
    p2.insert_text((72, y + 36), "1.2 Procedure.", fontsize=13, fontname="hebo")
    texts["s12"] = {"page": 2, "text": "1.2 Procedure.", "bbox": _rect_dict(_search(p2, "1.2 Procedure.")), "label": "section_header", "level": 2}

    para_agree = "See Table 1-1. Tool 11111 is used for 22-18 wire."
    para_conflict = "Tool 22222 is used for 12-10 wire."
    para_group_a = "Maker-A tools 11111, 22222, 55555 and 66666 are listed under Maker-A."
    para_cite_only = "Table 1-1 is required reading."
    para_held = "Tool 88888 appears in Table 1-1."
    para_colors = "Letter color codes to be used are: R-red, BL-blue, Y-yellow, W-white."
    body_y = y + 56
    # Color-code grid on the right; citing paragraphs on the left so nothing
    # runs into the footer band.
    color_rows = [("R", "red"), ("BL", "blue"), ("Y", "yellow"), ("W", "white")]
    color_x0, color_y0, color_cw, color_rh = 400.0, body_y, 70.0, 16.0
    color_cells = []
    for cr, (code, name) in enumerate(color_rows):
        for cc, val in enumerate((code, name)):
            crect = pymupdf.Rect(color_x0 + cc * color_cw, color_y0 + cr * color_rh,
                                 color_x0 + (cc + 1) * color_cw, color_y0 + (cr + 1) * color_rh)
            p2.draw_rect(crect, color=(0, 0, 0), width=0.4)
            p2.insert_text((crect.x0 + 4, crect.y0 + 11), val, fontsize=8, fontname="helv")
            color_cells.append({
                "row": cr, "col": cc, "text": val,
                "bbox": _rect_dict(crect + (1, 1, -1, -1)),
                "row_span": 1, "col_span": 1,
                "column_header": False, "row_section": False,
            })
    color_table_bbox = _rect_dict(pymupdf.Rect(color_x0, color_y0, color_x0 + 2 * color_cw,
                                               color_y0 + len(color_rows) * color_rh))

    for key, para in (
        ("para_agree", para_agree),
        ("para_conflict", para_conflict),
        ("para_group_a", para_group_a),
        ("para_cite_only", para_cite_only),
        ("para_held", para_held),
        ("para_colors", para_colors),
    ):
        rect = pymupdf.Rect(72, body_y, 390, body_y + 28)
        texts[key] = {"page": 2, "text": para, "bbox": _rect_dict(_textbox(p2, rect, para, size=9)), "label": "text"}
        body_y += 32

    # --- page 3: neighbouring-page citation of the table ---
    p3 = doc[2]
    folios[3] = _add_folio(p3, 3)
    p3.insert_text((72, 90), "1.3 Notes", fontsize=13, fontname="hebo")
    texts["s13"] = {"page": 3, "text": "1.3 Notes", "bbox": _rect_dict(_search(p3, "1.3 Notes")), "label": "section_header", "level": 2}
    p3_para = "As listed in Table 1-1, length 9/32 applies to 12-10."
    texts["para_neighbor"] = {
        "page": 3,
        "text": p3_para,
        "bbox": _rect_dict(_textbox(p3, pymupdf.Rect(72, 110, 540, 180), p3_para)),
        "label": "text",
    }

    doc.set_metadata({
        "title": "Sample Technical Manual",
        "author": "Test Author",
        "creationDate": "D:20261123000000Z",
        "modDate": "D:20261123000000Z",
    })
    doc.save(pdf_path, garbage=4, deflate=True, no_new_id=True)
    doc.close()
    return {
        "pdf": pdf_path,
        "folios": folios,
        "texts": texts,
        "cells": cells_meta,
        "table_bbox": table_box,
        "page_size": [PAGE_W, PAGE_H],
        "header_text": HEADER,
        "midpage_lookalike": "TM 12-34",
        "color_cells": color_cells,
        "color_table_bbox": color_table_bbox,
    }


def build_scanned(out_dir: Path) -> Path:
    """Image-only page: no extractable text layer."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "scanned.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    page.draw_rect(pymupdf.Rect(40, 40, 572, 752), color=(0.2, 0.2, 0.2), fill=(0.75, 0.75, 0.75), width=1)
    doc.set_metadata({"title": "Scanned Page", "creationDate": "D:20261123000000Z", "modDate": "D:20261123000000Z"})
    doc.save(path, garbage=4, deflate=True, no_new_id=True)
    doc.close()
    return path


def build_broken(out_dir: Path) -> Path:
    """A text layer exists but is not usable (low alphanumeric ratio, replacement chars)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "broken.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    # Well over 20 characters, almost no letters/digits, plus U+FFFD.
    junk = ("§•×¤‡" * 20) + "\ufffd\ufffd\ufffd" + ("///..." * 8)
    page.insert_textbox(pymupdf.Rect(72, 100, 540, 400), junk, fontsize=12, fontname="helv")
    doc.set_metadata({"title": "Broken Layer", "creationDate": "D:20261123000000Z", "modDate": "D:20261123000000Z"})
    doc.save(path, garbage=4, deflate=True, no_new_id=True)
    doc.close()
    return path


def write_conversion(conv_dir: Path, meta: dict) -> Path:
    """Write a minimal Docling-shaped conversion for the invented manual (no Docling run)."""
    import json

    conv_dir = Path(conv_dir)
    conv_dir.mkdir(parents=True, exist_ok=True)
    (conv_dir / "tables").mkdir(exist_ok=True)
    texts = []
    body_children = []
    order = [
        "title", "s11", "p1_body", "fig_cap", "tab_cap", "footnote",
        "s12", "para_agree", "para_conflict", "para_group_a", "para_cite_only",
        "para_held", "para_colors", "s13", "para_neighbor",
    ]
    for i, key in enumerate(order):
        t = meta["texts"][key]
        item = {
            "self_ref": f"#/texts/{i}",
            "parent": {"$ref": "#/body"},
            "children": [],
            "content_layer": "body",
            "label": t["label"],
            "prov": [{"page_no": t["page"], "bbox": t["bbox"], "charspan": [0, len(t["text"])]}],
            "orig": t["text"],
            "text": t["text"],
        }
        if t["label"] == "section_header":
            item["level"] = t.get("level", 1)
        texts.append(item)
        body_children.append({"$ref": f"#/texts/{i}"})

    cap_idx = order.index("tab_cap")
    fn_idx = order.index("footnote")
    table_cells = []
    grid = [[None] * 3 for _ in range(11)]
    for c in meta["cells"]:
        if c.get("cover"):
            # Positions covered by the colspan are still present in the grid as the origin cell.
            origin = next(x for x in meta["cells"] if x["row"] == c["row"] and x["col"] == 0)
            cell = _model_cell(origin)
            grid[c["row"]][c["col"]] = cell
            continue
        cell = _model_cell(c)
        table_cells.append(cell)
        grid[c["row"]][c["col"]] = cell

    table = {
        "self_ref": "#/tables/0",
        "parent": {"$ref": "#/body"},
        "children": [],
        "content_layer": "body",
        "label": "table",
        "prov": [{"page_no": 2, "bbox": meta["table_bbox"], "charspan": [0, 0]}],
        "captions": [{"$ref": f"#/texts/{cap_idx}"}],
        "references": [],
        "footnotes": [{"$ref": f"#/texts/{fn_idx}"}],
        "data": {
            "table_cells": table_cells,
            "num_rows": 11,
            "num_cols": 3,
            "grid": grid,
        },
    }
    # Uncaptioned color-code grid (second table).
    color_grid = [[None, None] for _ in range(4)]
    color_table_cells = []
    for c in meta["color_cells"]:
        cell = {
            "bbox": c["bbox"], "row_span": 1, "col_span": 1,
            "start_row_offset_idx": c["row"], "end_row_offset_idx": c["row"] + 1,
            "start_col_offset_idx": c["col"], "end_col_offset_idx": c["col"] + 1,
            "text": c["text"], "column_header": False, "row_header": False,
            "row_section": False, "fillable": False,
        }
        color_table_cells.append(cell)
        color_grid[c["row"]][c["col"]] = cell
    color_table = {
        "self_ref": "#/tables/1",
        "parent": {"$ref": "#/body"},
        "children": [],
        "content_layer": "body",
        "label": "table",
        "prov": [{"page_no": 2, "bbox": meta["color_table_bbox"], "charspan": [0, 0]}],
        "captions": [],
        "references": [],
        "footnotes": [],
        "data": {"table_cells": color_table_cells, "num_rows": 4, "num_cols": 2, "grid": color_grid},
    }

    # Insert the tables into the reading order: main table after its caption, color grid before para_colors.
    insert_at = next(i for i, c in enumerate(body_children) if c["$ref"] == f"#/texts/{cap_idx}") + 1
    body_children.insert(insert_at, {"$ref": "#/tables/0"})
    colors_idx = order.index("para_colors")
    insert_color = next(i for i, c in enumerate(body_children) if c["$ref"] == f"#/texts/{colors_idx}")
    body_children.insert(insert_color, {"$ref": "#/tables/1"})

    doc = {
        "schema_name": "DoclingDocument",
        "version": "1.12.0",
        "name": "manual",
        "origin": {"mimetype": "application/pdf", "filename": "manual.pdf"},
        "furniture": {"self_ref": "#/furniture", "children": [], "content_layer": "furniture", "name": "_root_", "label": "unspecified"},
        "body": {"self_ref": "#/body", "children": body_children, "content_layer": "body", "name": "_root_", "label": "unspecified"},
        "groups": [],
        "texts": texts,
        "pictures": [],
        "tables": [table, color_table],
        "key_value_items": [],
        "form_items": [],
        "pages": {str(p): {"size": {"width": PAGE_W, "height": PAGE_H}, "page_no": p} for p in (1, 2, 3)},
    }
    (conv_dir / "manual.docling.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")

    # Converter Markdown/HTML include body and table text, but not the running folio.
    md_lines = ["# Sample Technical Manual", "", "## 1.1 General", "", meta["texts"]["p1_body"]["text"], "",
                "Figure 1-1. Overview.", "", "Table 1-1. Crimping tools.", ""]
    rows = []
    for r in range(11):
        rows.append([(grid[r][c] or {}).get("text") or "" for c in range(3)])
    md_lines.append("| " + " | ".join(rows[0]) + " |")
    md_lines.append("| " + " | ".join("---" for _ in rows[0]) + " |")
    for row in rows[1:]:
        md_lines.append("| " + " | ".join(row) + " |")
    md_lines += ["", meta["texts"]["footnote"]["text"], "", "### 1.2 Procedure.", ""]
    for key in ("para_agree", "para_conflict", "para_group_a", "para_cite_only", "para_held", "para_colors"):
        md_lines += [meta["texts"][key]["text"], ""]
    md_lines += ["", "R red", "BL blue", "Y yellow", "W white", ""]
    md_lines += ["### 1.3 Notes", "", meta["texts"]["para_neighbor"]["text"], ""]
    md = "\n".join(md_lines)
    (conv_dir / "manual.md").write_text(md, encoding="utf-8")

    html_rows = []
    for r, row in enumerate(rows):
        tds = []
        if r == 10:
            tds.append(f'<td colspan="3">{_esc(row[0])}</td>')
        else:
            tag = "th" if r == 0 else "td"
            tds.extend(f"<{tag}>{_esc(v)}</{tag}>" for v in row)
        html_rows.append("<tr>" + "".join(tds) + "</tr>")
    html = (
        "<html><body>"
        "<h1>Sample Technical Manual</h1>"
        "<h2>1.1 General</h2>"
        f"<p>{_esc(meta['texts']['p1_body']['text'])}</p>"
        f"<p>{_esc(meta['texts']['fig_cap']['text'])}</p>"
        f"<p>{_esc(meta['texts']['tab_cap']['text'])}</p>"
        "<table>" + "".join(html_rows) + "</table>"
        f"<p>{_esc(meta['texts']['footnote']['text'])}</p>"
        "<h3>1.2 Procedure.</h3>"
        + "".join(f"<p>{_esc(meta['texts'][k]['text'])}</p>" for k in
                  ("para_agree", "para_conflict", "para_group_a", "para_cite_only", "para_held", "para_colors"))
        + "<table><tr><td>R</td><td>red</td></tr><tr><td>BL</td><td>blue</td></tr>"
          "<tr><td>Y</td><td>yellow</td></tr><tr><td>W</td><td>white</td></tr></table>"
        + "<h3>1.3 Notes</h3>"
        f"<p>{_esc(meta['texts']['para_neighbor']['text'])}</p>"
        "</body></html>"
    )
    (conv_dir / "manual.html").write_text(html, encoding="utf-8")

    import csv
    with (conv_dir / "tables" / "table-1.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        for row in rows:
            w.writerow(row)
    with (conv_dir / "tables" / "table-2.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        for c0, c1 in (("R", "red"), ("BL", "blue"), ("Y", "yellow"), ("W", "white")):
            w.writerow([c0, c1])
    return conv_dir


def _model_cell(c):
    cell = {
        "bbox": c["bbox"],
        "row_span": c["row_span"],
        "col_span": c["col_span"],
        "start_row_offset_idx": c["row"],
        "end_row_offset_idx": c["row"] + c["row_span"],
        "start_col_offset_idx": c["col"],
        "end_col_offset_idx": c["col"] + c["col_span"],
        "text": c["text"],
        "column_header": c["column_header"],
        "row_header": False,
        "row_section": c["row_section"],
        "fillable": False,
    }
    return cell


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
