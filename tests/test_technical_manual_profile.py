"""Fail-first acceptance tests for the technical_manual profile and cell-box checks.

Fixtures are invented and generated at test time (see
tests/fixtures/verified_pdf/make_technical_manual_pdf.py). No real manuals are
used. Classification and verification rules this file pins down:

* Page triage (pypdfium2): fewer than MIN_TEXT_CHARS (20) extractable characters
  is `scanned`; otherwise a layer with alphanumeric ratio below 0.4, or with
  replacement or NUL characters, is `broken_text_layer`; everything else is
  `born_digital`. Signals are recorded per page. Scanned pages are held; there
  is no OCR or VLM tier here.
* A table cell verifies only against text-layer characters inside its own bbox
  (plus the verifier's existing box tolerance). A stacked cell verifies when
  each of its lines is a contiguous span inside that box. Cells with no bbox,
  and empty cells, stay held. A combined quote that appears only in another
  row must not verify the cell.
* Text cross-reference is a structural guide: it records paragraph spans on
  the same or neighbouring pages that share distinctive tokens with a cell or
  cite the table, with quote_sha256, and a stored region (row/col range). A
  span is linked only to the rows or cells whose tokens it shares
  (group-aware). It never links a paragraph to the whole table by default, and
  it never promotes a cell to verified. A row/column assignment that conflicts
  with the text is flagged. Held cells keep the supporting spans for review.
* Human review (REVIEW-PROCEDURE): for a held cell, step 1 is the citing
  spans next to the cell crop. Approval records status `human_verified` with
  reviewer, timestamp, and evidence hashes. A conflict stays held. The tool
  never auto-promotes. Approval without a reviewer field is rejected.
"""
from __future__ import annotations

import hashlib
import re
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.source_router import VerifiedPdfPlugin  # noqa: E402
from scripts.verified_pdf import agent_tool  # noqa: E402
from scripts.verified_pdf import verify_conversion as vc  # noqa: E402
from tests.fixtures.verified_pdf.make_technical_manual_pdf import (  # noqa: E402
    build_broken,
    build_manual,
    build_scanned,
    write_conversion,
)

MIN_TEXT_CHARS = 20


def _sha(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


def _cell(table, row, col):
    matches = [c for c in table["cells"] if c["row"] == row and c["col"] == col]
    assert matches, f"no cell r{row}c{col} on {table.get('id')}"
    return matches[0]


def _refs_mentioning(cell, *needles):
    out = []
    for ref in cell.get("cross_references") or []:
        blob = (ref.get("quote") or "") + " " + (ref.get("block_id") or "")
        if all(n.lower() in blob.lower() or n.lower() in (ref.get("quote") or "").lower() for n in needles):
            out.append(ref)
    return out


def _refs_for_quote(cell, snippet):
    return [r for r in (cell.get("cross_references") or []) if snippet in (r.get("quote") or "")]


@pytest.fixture
def manual(tmp_path):
    meta = build_manual(tmp_path)
    conv = write_conversion(tmp_path / "manual_conv", meta)
    return {"pdf": meta["pdf"], "conv": conv, "meta": meta}


def _view(manual, profile="technical_manual"):
    return agent_tool.build_verified_view(manual["pdf"], manual["conv"], profile=profile)


def _main_table(view):
    tables = [b for b in view["blocks"] if b["type"] == "table"]
    assert tables, "no table blocks"
    # The official table has the "Table 1-1" caption.
    for t in tables:
        if t.get("caption") and "Table 1-1" in t["caption"]:
            return t
    return tables[0]


def _color_table(view):
    tables = [b for b in view["blocks"] if b["type"] == "table"]
    for t in tables:
        if not t.get("caption"):
            return t
    raise AssertionError("uncaptioned color-code table not found")


# ---------------------------------------------------------------- triage

def test_triage_classifies_born_digital_scanned_and_broken_text_layer(tmp_path, manual):
    scanned = build_scanned(tmp_path / "scan")
    broken = build_broken(tmp_path / "broke")
    born = vc.triage_page(manual["pdf"], 2)
    sc = vc.triage_page(scanned, 1)
    br = vc.triage_page(broken, 1)
    assert born["class"] == "born_digital"
    assert sc["class"] == "scanned"
    assert br["class"] == "broken_text_layer"
    for result in (born, sc, br):
        sig = result["signals"]
        for key in ("n_chars", "n_words", "alnum_ratio", "replacement_chars", "nul_chars"):
            assert key in sig, f"missing triage signal {key}"
    assert born["signals"]["n_chars"] >= MIN_TEXT_CHARS
    assert born["signals"]["alnum_ratio"] >= 0.4
    assert sc["signals"]["n_chars"] < MIN_TEXT_CHARS
    assert br["signals"]["n_chars"] >= MIN_TEXT_CHARS
    assert br["signals"]["alnum_ratio"] < 0.4 or br["signals"]["replacement_chars"] > 0 or br["signals"]["nul_chars"] > 0


def test_triage_is_recorded_per_page_and_scanned_blocks_are_held(tmp_path):
    scanned = build_scanned(tmp_path)
    conv = tmp_path / "scan_conv"
    conv.mkdir()
    # Minimal conversion: one body paragraph on the image-only page.
    text = "A label the converter invented for a scanned page."
    doc = {
        "schema_name": "DoclingDocument", "version": "1.12.0", "name": "scanned",
        "origin": {"mimetype": "application/pdf", "filename": "scanned.pdf"},
        "furniture": {"self_ref": "#/furniture", "children": [], "content_layer": "furniture", "name": "_root_", "label": "unspecified"},
        "body": {"self_ref": "#/body", "children": [{"$ref": "#/texts/0"}], "content_layer": "body", "name": "_root_", "label": "unspecified"},
        "groups": [],
        "texts": [{
            "self_ref": "#/texts/0", "parent": {"$ref": "#/body"}, "children": [],
            "content_layer": "body", "label": "text", "orig": text, "text": text,
            "prov": [{"page_no": 1, "bbox": {"l": 40, "t": 40, "r": 400, "b": 80, "coord_origin": "TOPLEFT"},
                      "charspan": [0, len(text)]}],
        }],
        "pictures": [], "tables": [], "key_value_items": [], "form_items": [],
        "pages": {"1": {"size": {"width": 612.0, "height": 792.0}, "page_no": 1}},
    }
    (conv / "scanned.docling.json").write_text(json.dumps(doc), encoding="utf-8")
    (conv / "scanned.md").write_text(text, encoding="utf-8")
    (conv / "scanned.html").write_text(f"<html><body><p>{text}</p></body></html>", encoding="utf-8")
    view = agent_tool.build_verified_view(scanned, conv)
    assert view["pages"][0]["triage"]["class"] == "scanned"
    page_blocks = [b for b in view["blocks"] if 1 in (b.get("pages") or [])]
    assert page_blocks, "expected a block on the scanned page"
    assert all(b["status"] == "held" for b in page_blocks)


# ---------------------------------------------------------------- folio / profile

def test_technical_manual_detects_folio_and_strips_it_from_body_keeps_metadata(manual):
    report = vc.verify(manual["pdf"], manual["conv"], profile="technical_manual")
    view = _view(manual)
    assert view["profile"] == "technical_manual"
    assert report["flag_counts"].get("T3", 0) == 0 or not any(
        "TM 99-01" in (f.get("detail") or "") or "2-2" in (f.get("detail") or "")
        for f in report["flags"] if f["check"] == "T3"
    )
    # Folio text must not leak into body or table text. T3 may still fire for
    # an intentional cell mismatch (fixture r6c1); that is not a folio leak.
    folio_strings = set()
    for page in view["pages"]:
        for key in ("running_header", "running_footer"):
            text = (page.get(key) or {}).get("text")
            if text:
                folio_strings.add(text)
    assert folio_strings, "expected detected running header and footer text"
    body_and_table = []
    for block in view["blocks"]:
        for key in ("text", "quote", "caption", "html", "markdown"):
            val = block.get(key)
            if val:
                body_and_table.append(str(val))
        for cell in block.get("cells") or []:
            if cell.get("text"):
                body_and_table.append(cell["text"])
    for folio in folio_strings:
        # Whole-token match so wire sizes like 22-18 do not count as footer 2-1.
        pat = re.compile(r"(?<![\w-])" + re.escape(folio) + r"(?![\w-])")
        leaks = [chunk for chunk in body_and_table if pat.search(chunk)]
        assert not leaks, (folio, leaks)
    for page in view["pages"]:
        header = page["running_header"]
        footer = page["running_footer"]
        assert header["text"] == "TM 99-01"
        assert "bbox" in header and "bbox" in footer
        assert footer["text"] in {"2-1", "2-2", "2-3"}
    # Odd pages place the folio on the right; even pages on the left.
    odd = next(p for p in view["pages"] if p["page"] == 1)
    even = next(p for p in view["pages"] if p["page"] == 2)
    assert odd["running_header"]["bbox"]["l"] > 300
    assert even["running_header"]["bbox"]["l"] < 200
    # Mid-page lookalike is kept as body text and is not stored as the running header.
    body = json.dumps(view)
    assert "TM 12-34" in body
    assert odd["running_header"]["text"] != "TM 12-34"


def test_technical_manual_skips_grobid_and_o1(manual):
    tei = """<TEI xmlns="http://www.tei-c.org/ns/1.0"><teiHeader><fileDesc><titleStmt>
    <title level="a" type="main">A GROBID Title That Is Not In This Manual</title>
    </titleStmt></fileDesc></teiHeader><text><back><div type="references"><listBibl>
    <biblStruct xml:id="b0"><analytic><title level="a" type="main">A reference that is not in this paper at all</title></analytic>
    </listBibl></div></back></text></TEI>"""
    (manual["conv"] / "manual.tei.xml").write_text(tei, encoding="utf-8")
    report = vc.verify(manual["pdf"], manual["conv"], profile="technical_manual")
    assert not any(f["check"] == "O1" for f in report["flags"])
    rec = VerifiedPdfPlugin().extract({
        "source_id": "SRC-TM-1",
        "drive_path": str(manual["pdf"]),
        "verified_dir_fixture": str(manual["conv"]),
        "profile": "technical_manual",
        "use_grobid": True,
        "tei_fixture": tei,
        "title": "Sample Technical Manual",
    })
    refs = rec.provenance.per_part["references"]
    assert "GROBID" not in (refs.get("tool") or "").upper()
    assert str(refs.get("status") or "").startswith("not-available")
    assert rec.references == []


def test_ingest_parser_accepts_profile():
    args = agent_tool.build_parser().parse_args(["ingest", "x.pdf", "--profile", "technical_manual"])
    assert args.profile == "technical_manual"


# ---------------------------------------------------------------- tables: crop, HTML, Markdown

def test_table_crop_is_union_of_caption_table_footnote_padded_and_clamped(manual):
    view = _view(manual)
    table = _main_table(view)
    assert table.get("crop")
    crop_path = manual["conv"] / table["crop"]
    assert crop_path.is_file() and crop_path.stat().st_size > 0
    geom = table["crop_geom"]
    assert geom["render_scale"]
    pad = geom.get("requested_pad_px") or geom.get("pad_px")
    assert 40 <= pad <= 80
    # Caption and footnote sit inside the crop; the figure caption above and
    # the 1.2 Procedure heading below must not.
    meta = manual["meta"]
    crop_px = geom["crop_px"]  # [x0, y0, x1, y1] in output pixels
    scale = geom["render_scale"]

    def in_crop(bbox):
        # bbox is TOPLEFT points; crop_px is top-left pixels on the rendered page.
        x0, y0, x1, y1 = bbox["l"] * scale, bbox["t"] * scale, bbox["r"] * scale, bbox["b"] * scale
        return crop_px[0] <= x0 and crop_px[1] <= y0 and crop_px[2] >= x1 and crop_px[3] >= y1

    def overlaps_crop(bbox):
        x0, y0, x1, y1 = bbox["l"] * scale, bbox["t"] * scale, bbox["r"] * scale, bbox["b"] * scale
        return not (x1 <= crop_px[0] or x0 >= crop_px[2] or y1 <= crop_px[1] or y0 >= crop_px[3])

    assert in_crop(meta["texts"]["tab_cap"]["bbox"])
    assert in_crop(meta["texts"]["footnote"]["bbox"])
    assert in_crop(meta["table_bbox"])
    assert not overlaps_crop(meta["texts"]["fig_cap"]["bbox"])
    assert not overlaps_crop(meta["texts"]["s12"]["bbox"])
    assert geom.get("clamped_by")


def test_table_html_keeps_real_spans_and_does_not_invent_them(manual):
    view = _view(manual)
    table = _main_table(view)
    html = table["html"]
    assert 'colspan="3"' in html
    assert "All lengths are inches." in html
    # The Maker-A group row is three cells (empty | Maker-A: | empty), not an invented span.
    # The origin cell of the real span is the only colspan=3 in the footer row.
    assert html.count("Maker-A:") == 1
    maker_idx = html.index("Maker-A:")
    window = html[max(0, maker_idx - 200): maker_idx + 80]
    assert "colspan=" not in window
    # Flattened Markdown has a row per grid row and no raw colspan markup.
    md = table["markdown"]
    assert "Maker-A:" in md
    assert "All lengths are inches." in md
    assert "colspan" not in md.lower()


# ---------------------------------------------------------------- cell-box check

def test_cell_is_not_verified_from_another_rows_text(manual):
    """r6c1 claims '55555 66666', which appears as one line in the previous data
    row, but this cell's own box contains only '77777'."""
    view = _view(manual)
    cell = _cell(_main_table(view), 6, 1)
    assert cell["text"] == "55555 66666"
    assert cell["status"] == "held"
    assert cell["status"] != "verified"
    assert cell.get("quote_sha256") == _sha("55555 66666")


def test_stacked_cell_verifies_when_each_line_is_inside_its_box(manual):
    view = _view(manual)
    cell = _cell(_main_table(view), 4, 1)
    assert cell["status"] == "verified"
    lines = cell.get("line_checks") or []
    assert len(lines) >= 2
    assert all(ln.get("found") for ln in lines)
    # The same tokens also appear as one line in the next row; verification
    # must still be from this cell's box (the previous test holds r6c1).
    assert _cell(_main_table(view), 5, 1)["status"] == "verified"


def test_cell_without_bbox_or_empty_value_is_held(manual):
    view = _view(manual)
    table = _main_table(view)
    empty = _cell(table, 1, 0)
    assert not empty.get("bbox")
    assert empty["status"] == "held"
    tight = _cell(table, 9, 1)
    assert tight["text"] == "88888"
    assert tight["status"] == "held"


# ---------------------------------------------------------------- get_held_crop

def test_get_held_crop_returns_png_for_held_and_rejects_others(manual):
    _view(manual)
    path = agent_tool.get_held_crop(manual["conv"], "#/tables/0/r9c1")
    assert Path(path).is_file() and Path(path).stat().st_size > 0
    with pytest.raises((KeyError, PermissionError)):
        agent_tool.get_held_crop(manual["conv"], "#/tables/0/r0c0")  # verified header
    with pytest.raises((KeyError, PermissionError, FileNotFoundError)):
        agent_tool.get_held_crop(manual["conv"], "#/texts/99999")
    with pytest.raises((FileNotFoundError, KeyError, PermissionError)):
        agent_tool.get_held_crop(manual["conv"], "#/tables/0/r1c0")  # held, no bbox


# ---------------------------------------------------------------- text cross-reference (guide only, region-scoped)

def test_crossref_is_region_scoped_and_never_promotes(manual):
    view = _view(manual)
    table = _main_table(view)
    colors = _color_table(view)

    def all_refs(tbl):
        for c in tbl["cells"]:
            for r in c.get("cross_references") or []:
                yield c, r

    # A table-only citation with no distinctive cell tokens does not attach to every cell.
    cite_only = "Table 1-1 is required reading."
    attached_cite = [(c, r) for c, r in all_refs(table) if cite_only in (r.get("quote") or "")]
    assert attached_cite == []

    # Group-A paragraph shares Maker-A tool tokens only: must not attach to Maker-B cells.
    group_a_snip = "Maker-A tools 11111"
    maker_b_cells = [c for c in table["cells"] if c["row"] >= 7]
    for c in maker_b_cells:
        assert _refs_for_quote(c, group_a_snip) == []
    group_a_hits = [(c, r) for c, r in all_refs(table) if group_a_snip in (r.get("quote") or "")]
    assert group_a_hits, "Maker-A paragraph should attach to Maker-A cells that share its tokens"
    for c, r in group_a_hits:
        assert c["row"] < 7
        region = r["region"]
        assert region["row_end"] < 7
        # Never store the whole table as the region.
        assert not (region["row_start"] == 0 and region["row_end"] >= 10
                    and region["col_start"] == 0 and region["col_end"] >= 2)
        assert r["quote_sha256"] == _sha(r["quote"])

    # Color-code paragraph: token overlap with colour names / letter codes, not with tool numbers.
    color_snip = "Letter color codes"
    color_hits_main = [(c, r) for c, r in all_refs(table) if color_snip in (r.get("quote") or "")]
    color_hits_grid = [(c, r) for c, r in all_refs(colors) if color_snip in (r.get("quote") or "")]
    assert color_hits_grid, "color paragraph should attach to the uncaptioned color-code grid"
    assert color_hits_main, "color paragraph should attach to colour-named wire-size cells"
    for c, r in color_hits_main:
        # Wire-size column (col 0) carries Red/Blue/Yellow/White; tool numbers must not be linked.
        assert c["col"] == 0
        assert c["row"] != 1 and c.get("text") not in {"11111", "22222", "44444", "88888", "Maker-A:", "Maker-B:"}
        assert r["quote_sha256"] == _sha(r["quote"])
        region = r["region"]
        assert not (region["row_start"] == 0 and region["row_end"] >= 10
                    and region["col_start"] == 0 and region["col_end"] >= 2)
    for c, r in color_hits_grid:
        assert r["quote_sha256"] == _sha(r["quote"])

    # Agreeing text is recorded on the 11111 cell and does not by itself verify anything extra.
    agree = _cell(table, 2, 1)
    assert agree["status"] == "verified"
    assert any("11111" in (r.get("quote") or "") and "22-18" in (r.get("quote") or "")
               for r in agree.get("cross_references") or [])

    # Conflict: text pairs 22222 with 12-10, but the table places them in different rows.
    conflict_cell = _cell(table, 3, 1)
    conflict_refs = [r for r in (conflict_cell.get("cross_references") or []) if r.get("role") == "conflict"]
    assert conflict_refs, "22222 should be flagged as a row/column conflict with the 12-10 wording"
    assert all(r["quote_sha256"] == _sha(r["quote"]) for r in conflict_refs)

    # Cross-reference never promotes: the tight-bbox 88888 cell stays held even though text cites it.
    held = _cell(table, 9, 1)
    assert held["status"] == "held"
    held_refs = [r for r in (held.get("cross_references") or []) if "88888" in (r.get("quote") or "")]
    assert held_refs, "supporting paragraph must be attached to the held cell for review"


def test_crossref_uses_neighbouring_pages(manual):
    view = _view(manual)
    table = _main_table(view)
    cell_931 = _cell(table, 8, 2)  # 9/32 on the 12-10 row
    refs = [r for r in (cell_931.get("cross_references") or []) if "9/32" in (r.get("quote") or "")]
    assert refs, "neighbouring-page paragraph should attach by token overlap"
    assert any(r.get("page") == 3 for r in refs)


# ---------------------------------------------------------------- human review procedure

def test_review_procedure_is_documented():
    contract = (ROOT / "docs" / "verified_pdf" / "AGENT_CONTRACT.md").read_text(encoding="utf-8")
    assert "REVIEW-PROCEDURE" in contract
    lower = contract.lower()
    assert "human_verified" in contract
    assert "quote_sha256" in contract
    assert "crop" in lower
    assert "reviewer" in lower
    assert "never" in lower and ("promot" in lower)


def test_human_approval_requires_reviewer_and_keeps_evidence_hashes(manual):
    view = _view(manual)
    held = _cell(_main_table(view), 9, 1)
    assert held["status"] == "held"
    supporting = [r for r in (held.get("cross_references") or []) if "88888" in (r.get("quote") or "")]
    assert supporting

    with pytest.raises((ValueError, TypeError)):
        agent_tool.apply_cell_review(manual["conv"], "#/tables/0/r9c1", decision="approve", reviewer="")
    with pytest.raises((ValueError, TypeError)):
        agent_tool.apply_cell_review(manual["conv"], "#/tables/0/r9c1", decision="approve", reviewer=None)

    agent_tool.apply_cell_review(
        manual["conv"], "#/tables/0/r9c1", decision="approve", reviewer="Jane Reviewer"
    )
    view2 = _view(manual)
    approved = _cell(_main_table(view2), 9, 1)
    assert approved["status"] == "human_verified"
    assert approved["reviewer"] == "Jane Reviewer"
    assert approved.get("reviewed_at")
    ev = approved["evidence"]
    assert ev["cell_quote_sha256"] == _sha("88888")
    para_hashes = ev["paragraph_quote_sha256"]
    assert para_hashes
    expected = {_sha(r["quote"]) for r in supporting}
    assert expected & set(para_hashes)

    # A genuinely held cell (r6c1 fails the cell-box check) stays held. The
    # r9c1 approval must not promote it. A conflict candidate on a verified
    # cell (r3c1) is a hint, not a demotion, so that cell is not the target.
    view3 = _view(manual)
    still = _cell(_main_table(view3), 6, 1)
    assert still["status"] == "held"
    assert still["status"] != "human_verified"
    reason = still.get("held_reason") or ""
    if isinstance(reason, list):
        reason = " ".join(reason)
    assert "bbox" in reason.lower()

    # The automated path still never emits human_verified on a fresh conversion.
    fresh = tmp_fresh_view(manual)
    statuses = {c["status"] for t in fresh["blocks"] if t["type"] == "table" for c in t.get("cells") or []}
    assert "human_verified" not in statuses


def tmp_fresh_view(manual):
    # Rebuild from a conversion that has no decisions (the same fixture dir may
    # now contain decisions.json from the approval above). Copy is unnecessary
    # if we pass a clean conv — re-read a sibling conversion.
    from shutil import copytree
    import tempfile
    # Use the original generated files without decisions by writing a new conv.
    # The fixture module is deterministic enough to rebuild beside the pdf.
    parent = manual["pdf"].parent
    clean = parent / "clean_conv"
    if not clean.exists():
        write_conversion(clean, manual["meta"])
    return agent_tool.build_verified_view(manual["pdf"], clean, profile="technical_manual")
