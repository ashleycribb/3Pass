"""technical_manual profile helpers: triage, folio detect/strip, table export,
region-scoped text cross-reference, held-crop index, and human cell review.

Verification of a cell is only the text-layer check inside that cell's bbox.
Cross-reference produces ranked CANDIDATE paragraph links with token-overlap
evidence; they are not assertions. A human confirms or rejects each link.
Only a human-confirmed link is stored as evidence on human_verified.
The tool never auto-promotes a cell.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

try:
    from . import verify_conversion as vc
except ImportError:
    import verify_conversion as vc  # noqa: E402

# Documented thresholds (not silent defaults).
# Triage uses the same MIN_TEXT_CHARS as T1 (20). A usable born-digital page
# also has alphanumeric ratio >= 0.4 and no replacement or NUL characters.
ALNUM_RATIO_MIN = 0.4
# Folio bands, as a fraction of page height, looking inward from the top and
# bottom edges. Repeating lines must sit in the same band on nearby pages.
FOLIO_BAND_FRACTION = 0.08
# Table crop: pad after unioning caption + table + footnote, in output pixels,
# then clamp so a neighbour is never swallowed. The pad is inside 40–80 px.
CROP_PAD_PX = 48
CROP_PAD_BAND = (40, 80)
CROP_SCALE = 2.0
HELD_CROP_PAD_PX = 8

STOP = {
    "the", "and", "for", "are", "is", "to", "of", "a", "an", "in", "on", "or",
    "as", "by", "be", "used", "use", "see", "with", "from", "that", "this",
    "table", "tables", "tool", "tools", "size", "wire", "length", "lengths",
    "listed", "under", "appears", "required", "reading", "letter", "color",
    "codes", "code", "also", "not", "a", "an",
}

TOKEN_EXTRA = re.compile(r"\d+\s*[-/]\s*\d+|\b[A-Za-z][A-Za-z0-9]*-[A-Za-z][A-Za-z0-9]*\b")


def _sha(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


def distinctive_tokens(text: str) -> set[str]:
    compounds = {re.sub(r"\s+", "", m.group(0)).lower() for m in TOKEN_EXTRA.finditer(text or "")}
    parts_of_compounds = set()
    for c in compounds:
        parts_of_compounds.update(vc.words(re.sub(r"[-/]", " ", c)))
    toks = set(vc.words(text or "")) - parts_of_compounds
    toks |= compounds
    # Keep both sides of a letter-colour code so "R-red" overlaps a cell "red".
    for c in compounds:
        left, sep, right = c.partition("-")
        if sep and re.match(r"^[a-z]{1,3}$", left) and right.isalpha():
            if len(left) >= 2:
                toks.add(left)
            if len(right) >= 2:
                toks.add(right)
    return {t for t in toks if t not in STOP and len(t) >= 2}


# ---------------------------------------------------------------------------
# Page triage
# ---------------------------------------------------------------------------

def triage_page(pdf_path, page_no, pdf=None) -> dict:
    """Classify one 1-based page as born_digital, scanned, or broken_text_layer."""
    close = False
    if pdf is None:
        pdf = vc.Pdf(pdf_path)
        close = True
    page = pdf.doc[page_no - 1]
    tp = pdf.textpage(page_no)
    raw = tp.get_text_range() or ""
    stripped = raw.strip()
    n_chars = len(stripped)
    word_list = vc.words(raw)
    n_words = len(word_list)
    n_alnum = sum(ch.isalnum() for ch in stripped)
    alnum_ratio = (n_alnum / n_chars) if n_chars else 0.0
    n_replace = raw.count("\ufffd")
    n_null = raw.count("\x00")
    signals = {
        "n_chars": n_chars,
        "n_words": n_words,
        "alnum_ratio": round(alnum_ratio, 4),
        "replacement_chars": n_replace,
        "nul_chars": n_null,
        "min_text_chars_threshold": vc.MIN_TEXT_CHARS,
    }
    if n_chars < vc.MIN_TEXT_CHARS:
        klass = "scanned"
    elif alnum_ratio < ALNUM_RATIO_MIN or n_replace > 0 or n_null > 0:
        klass = "broken_text_layer"
    else:
        klass = "born_digital"
    return {"page": page_no, "class": klass, "signals": signals}


def triage_document(pdf) -> list[dict]:
    return [triage_page(None, p, pdf=pdf) for p in range(1, len(pdf.doc) + 1)]


# ---------------------------------------------------------------------------
# Running headers / footers
# ---------------------------------------------------------------------------

def _chars(pdf, page_no):
    tp = pdf.textpage(page_no)
    out = []
    for i in range(tp.count_chars()):
        ch = tp.get_text_range(i, 1)
        l, b, r, t = tp.get_charbox(i)
        out.append((ch, float(l), float(b), float(r), float(t)))
    return out


def _to_topleft(bbox_bl, height):
    return {
        "l": bbox_bl["l"],
        "t": height - bbox_bl["t"],
        "r": bbox_bl["r"],
        "b": height - bbox_bl["b"],
        "coord_origin": "TOPLEFT",
    }


def _band_chars(chars, y0, y1):
    return [c for c in chars if y0 <= (c[2] + c[4]) / 2 <= y1]


def _line_bbox(chars):
    return {
        "l": min(c[1] for c in chars),
        "b": min(c[2] for c in chars),
        "r": max(c[3] for c in chars),
        "t": max(c[4] for c in chars),
        "coord_origin": "BOTTOMLEFT",
    }


def _line_text(chars):
    return "".join(c[0] for c in chars).replace("\r", " ").replace("\n", " ").strip()


def detect_folios(pdf) -> dict:
    """Find repeating header/footer lines by y-band across the document pages."""
    n = len(pdf.doc)
    headers, footers = {}, {}
    for p in range(1, n + 1):
        h = pdf.height(p)
        chars = _chars(pdf, p)
        top = _band_chars(chars, h * (1 - FOLIO_BAND_FRACTION), h)
        bot = _band_chars(chars, 0.0, h * FOLIO_BAND_FRACTION)
        if top:
            headers[p] = {"text": _line_text(top), "bbox": _line_bbox(top)}
        if bot:
            footers[p] = {"text": _line_text(bot), "bbox": _line_bbox(bot)}
    # A running header is the same text in the top band on a majority of pages
    # that have a top-band line. A running footer is a short line in the bottom
    # band on a majority of those pages (the printed page number may change).
    def confirmed(hits, same_text):
        if len(hits) < max(1, (n + 1) // 2):
            return False
        if not same_text:
            return True
        counts = Counter(h["text"] for h in hits.values())
        top, k = counts.most_common(1)[0]
        return k >= max(2, (len(hits) + 1) // 2) if len(hits) > 1 else k == 1

    header_ok = confirmed(headers, same_text=True)
    # A running footer is a short line (printed page, change number) in the
    # bottom band on a majority of pages. A single page whose bottom band also
    # captured body text must not veto the document-wide footer.
    short_footers = {p: f for p, f in footers.items() if len(vc.words(f["text"])) <= 4}
    footer_ok = confirmed(short_footers, same_text=False)
    footers = short_footers
    pages = {}
    for p in range(1, n + 1):
        item = {"page": p, "running_header": None, "running_footer": None}
        hgt = pdf.height(p)
        if header_ok and p in headers:
            item["running_header"] = {
                "text": headers[p]["text"],
                "bbox": _to_topleft(headers[p]["bbox"], hgt),
            }
        if footer_ok and p in footers:
            item["running_footer"] = {
                "text": footers[p]["text"],
                "bbox": _to_topleft(footers[p]["bbox"], hgt),
            }
        pages[p] = item
    return {
        "pages": pages,
        "header_ok": header_ok,
        "footer_ok": footer_ok,
        "regions_bl": {
            p: [x["bbox"] for x in (headers.get(p), footers.get(p)) if x]
            for p in range(1, n + 1)
        } if (header_ok or footer_ok) else {},
    }


def text_excluding_regions(pdf, page_no, regions, y_tol=2.0) -> str:
    if not regions:
        return pdf.page_text(page_no)
    kept = []
    for ch, l, b, r, t in _chars(pdf, page_no):
        y = (b + t) / 2
        drop = False
        for reg in regions:
            if (reg["b"] - y_tol) <= y <= (reg["t"] + y_tol) and not (
                r < reg["l"] - 2 or l > reg["r"] + 2
            ):
                drop = True
                break
        if not drop:
            kept.append(ch)
    return "".join(kept)


def folio_regions_bl(folios, page_no):
    if not folios:
        return []
    return list(folios.get("regions_bl", {}).get(page_no) or [])


# ---------------------------------------------------------------------------
# Per-cell box check
# ---------------------------------------------------------------------------

def check_cell_box(pdf, page_no, cell: dict) -> dict:
    """Verify a cell only against text inside its own bbox. Never uses the page."""
    text = cell.get("text") or ""
    bbox = cell.get("bbox")
    result = {
        "row": int(cell.get("start_row_offset_idx", cell.get("row") or 0)),
        "col": int(cell.get("start_col_offset_idx", cell.get("col") or 0)),
        "text": text,
        "row_span": int(cell.get("row_span") or 1),
        "col_span": int(cell.get("col_span") or 1),
        "column_header": bool(cell.get("column_header")),
        "row_section": bool(cell.get("row_section")),
        "bbox": bbox,
        "quote_sha256": _sha(text) if text.strip() else None,
        "line_checks": [],
        "cross_references": [],
    }
    if not text.strip():
        result["status"] = "held"
        result["held_reason"] = "empty table cell (no value to hash)"
        return result
    if not bbox:
        result["status"] = "held"
        result["held_reason"] = "table cell has no bbox"
        return result
    src = pdf.cell_text(page_no, bbox, bbox.get("coord_origin") or "TOPLEFT")
    lines = [ln.strip() for ln in re.split(r"[\r\n]+", src) if ln.strip()]
    result["line_checks"] = [
        {"line": ln, "found": bool(vc.joined(ln)) and vc.joined(ln) in vc.joined(src),
         "quote_sha256": _sha(ln)}
        for ln in lines
    ]
    if vc.joined(src) != vc.joined(text):
        result["status"] = "held"
        result["held_reason"] = "table cell value not found in the cell bbox text layer"
        return result
    if lines and not all(lc["found"] for lc in result["line_checks"]):
        result["status"] = "held"
        result["held_reason"] = "stacked cell line is not a contiguous span inside the cell bbox"
        return result
    result["status"] = "verified"
    return result


# ---------------------------------------------------------------------------
# Table HTML, Markdown, padded+clamped crop
# ---------------------------------------------------------------------------

def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _as_topleft(bbox, page_h):
    if not bbox:
        return None
    if bbox.get("coord_origin") == "TOPLEFT":
        return bbox["l"], bbox["t"], bbox["r"], bbox["b"]
    # BOTTOMLEFT: t is the higher edge
    top = page_h - max(bbox["t"], bbox.get("b", 0))
    bot = page_h - min(bbox["t"], bbox.get("b", 0))
    return bbox["l"], top, bbox["r"], bot


def render_html_table(caption, cells, n_rows, n_cols, footnote=None) -> str:
    by = {(c["row"], c["col"]): c for c in cells}
    covered = set()
    rows_html = []
    for r in range(n_rows):
        tds = []
        for c in range(n_cols):
            if (r, c) in covered:
                continue
            cell = by.get((r, c))
            if cell is None:
                continue
            rs, cs = cell["row_span"], cell["col_span"]
            for dr in range(rs):
                for dc in range(cs):
                    if dr or dc:
                        covered.add((r + dr, c + dc))
            lines = [lc["line"] for lc in (cell.get("line_checks") or []) if lc.get("line")]
            if len(lines) > 1:
                inner = "<br>".join(_esc(x) for x in lines)
            else:
                inner = _esc(cell.get("text") or "")
            attrs = []
            if cs > 1:
                attrs.append(f'colspan="{cs}"')
            if rs > 1:
                attrs.append(f'rowspan="{rs}"')
            tag = "th" if cell.get("column_header") else "td"
            klass = (cell.get("status") or "held").split(",")[0]
            if cell.get("row_section"):
                klass += " group"
            attrs.append(f'class="{klass}"')
            if cell.get("quote_sha256"):
                attrs.append(f'data-quote-sha256="{cell["quote_sha256"]}"')
            tds.append(f"<{tag} {' '.join(attrs)}>{inner}</{tag}>")
        rows_html.append("<tr>" + "".join(tds) + "</tr>")
    cap = f"<caption>{_esc(caption or '')}</caption>" if caption else ""
    fn = f"<p class=\"fn\">{_esc(footnote)}</p>" if footnote else ""
    return (
        "<table>" + cap + "".join(rows_html) + "</table>" + fn
    )


def render_markdown_table(caption, cells, n_rows, n_cols, footnote=None) -> str:
    by = {(c["row"], c["col"]): c for c in cells}

    def cell_md(c):
        if not c:
            return ""
        lines = [lc["line"] for lc in (c.get("line_checks") or []) if lc.get("line")]
        if len(lines) > 1:
            return " / ".join(lines).replace("|", "\\|")
        return (c.get("text") or "").replace("|", "\\|")

    rows = [[cell_md(by.get((r, c))) for c in range(n_cols)] for r in range(n_rows)]
    lines = []
    if caption:
        lines += [f"# {caption}", ""]
    if rows:
        lines.append("| " + " | ".join(rows[0]) + " |")
        lines.append("| " + " | ".join("---" for _ in rows[0]) + " |")
        for row in rows[1:]:
            lines.append("| " + " | ".join(row) + " |")
    if footnote:
        lines += ["", footnote]
    return "\n".join(lines) + "\n"


def union_and_pad_crop(page_image, page_h, boxes, pad_px, scale, neighbors=None):
    rects = []
    for bbox in boxes:
        tl = _as_topleft(bbox, page_h)
        if tl:
            rects.append(tl)
    if not rects:
        raise RuntimeError("no boxes to crop")
    l = min(a[0] for a in rects)
    top = min(a[1] for a in rects)
    r = max(a[2] for a in rects)
    bot = max(a[3] for a in rects)
    gutter = 2
    avail = {"left": pad_px, "top": pad_px, "right": pad_px, "bottom": pad_px}
    clamped_by = {}
    for nb in neighbors or []:
        nl, nt, nr, nb_bot = nb["l"], nb["t"], nb["r"], nb["b"]
        overlaps_x = not (nr < l or nl > r)
        overlaps_y = not (nb_bot < top or nt > bot)
        if overlaps_y:
            if nr <= l:
                px = int((l - nr) * scale) - gutter
                if px < avail["left"]:
                    avail["left"] = max(0, px)
                    clamped_by["left"] = nb.get("id")
            if nl >= r:
                px = int((nl - r) * scale) - gutter
                if px < avail["right"]:
                    avail["right"] = max(0, px)
                    clamped_by["right"] = nb.get("id")
        if overlaps_x:
            if nb_bot <= top:
                px = int((top - nb_bot) * scale) - gutter
                if px < avail["top"]:
                    avail["top"] = max(0, px)
                    clamped_by["top"] = nb.get("id")
            if nt >= bot:
                px = int((nt - bot) * scale) - gutter
                if px < avail["bottom"]:
                    avail["bottom"] = max(0, px)
                    clamped_by["bottom"] = nb.get("id")
    x0 = max(0, int(l * scale) - avail["left"])
    y0 = max(0, int(top * scale) - avail["top"])
    x1 = min(page_image.width, int(r * scale) + avail["right"])
    y1 = min(page_image.height, int(bot * scale) + avail["bottom"])
    crop = page_image.crop((x0, y0, x1, y1))
    geom = {
        "requested_pad_px": pad_px,
        "requested_pad_band_px": list(CROP_PAD_BAND),
        "render_scale": scale,
        "applied_pad_px": avail,
        "clamped_by": clamped_by,
        "union_pt_topleft": [round(l, 2), round(top, 2), round(r, 2), round(bot, 2)],
        "crop_px": [x0, y0, x1, y1],
        "size_px": [crop.width, crop.height],
    }
    return crop, geom


def page_neighbors(docling, page_no, page_h, exclude_boxes):
    excluded = set()
    for b in exclude_boxes:
        tl = _as_topleft(b, page_h)
        if tl:
            excluded.add(tuple(round(x, 2) for x in tl))
    out = []
    for kind in ("texts", "tables", "pictures"):
        for obj in docling.get(kind) or []:
            for prov in obj.get("prov") or []:
                if prov.get("page_no") != page_no:
                    continue
                tl = _as_topleft(prov.get("bbox"), page_h)
                if not tl or tuple(round(x, 2) for x in tl) in excluded:
                    continue
                out.append({
                    "id": obj.get("self_ref") or "",
                    "l": tl[0], "t": tl[1], "r": tl[2], "b": tl[3],
                })
    return out


# ---------------------------------------------------------------------------
# Region-scoped text cross-reference
# ---------------------------------------------------------------------------

def _group_row_ranges(cells, n_rows):
    """Rows that start a group (row_section or a short label ending in ':')."""
    starts = []
    by_row = defaultdict(list)
    for c in cells:
        by_row[c["row"]].append(c)
    for r in range(n_rows):
        for c in by_row.get(r, []):
            text = (c.get("text") or "").strip()
            if c.get("row_section") or (text.endswith(":") and len(text) <= 40):
                starts.append(r)
                break
    ranges = []
    for i, s in enumerate(starts):
        end = (starts[i + 1] - 1) if i + 1 < len(starts) else n_rows - 1
        ranges.append((s, end))
    return ranges


def _region_of(matched_cells):
    rows = [c["row"] for c in matched_cells]
    cols = [c["col"] for c in matched_cells]
    return {
        "row_start": min(rows),
        "row_end": max(rows),
        "col_start": min(cols),
        "col_end": max(cols),
    }


def _is_conflict(para_tokens, matched, cells):
    """True when the paragraph pairs values from different columns that never share a row."""
    # Map token -> set of (row, col)
    locations = defaultdict(set)
    for c in cells:
        for tok in distinctive_tokens(c.get("text") or ""):
            locations[tok].add((c["row"], c["col"]))
    # Tokens in the paragraph that identify unique cells
    present = [t for t in para_tokens if t in locations]
    # Cross-column pairs
    for i, a in enumerate(present):
        for b in present[i + 1 :]:
            locs_a, locs_b = locations[a], locations[b]
            cols_a = {c for _, c in locs_a}
            cols_b = {c for _, c in locs_b}
            rows_a = {r for r, _ in locs_a}
            rows_b = {r for r, _ in locs_b}
            if cols_a.isdisjoint(cols_b) and rows_a.isdisjoint(rows_b):
                # They never co-occur in the same row
                same_row = any(r == r2 for r, _ in locs_a for r2, _ in locs_b if r == r2)
                if not same_row:
                    return True
    return False


def cross_reference_tables(texts, tables_spec, n_pages):
    """Attach region-scoped paragraph spans to cells. Never changes status.

    tables_spec: list of dicts with id, page, n_rows, n_cols, cells, caption.
    texts: list of {id, page, text, label} body paragraphs.
    """
    body_labels = {"text", "list_item", "paragraph", None, "footnote"}
    paragraphs = [
        t for t in texts
        if (t.get("label") or "text") in body_labels and (t.get("text") or "").strip()
    ]
    for tbl in tables_spec:
        cap = tbl.get("caption") or ""
        # Do not treat this table's own caption / footnote as citing body text.
        own = set(tbl.get("exclude_text_ids") or [])
        page = tbl["page"]
        nearby = [t for t in paragraphs
                  if t["id"] not in own and t["page"] in {page - 1, page, page + 1}
                  and 1 <= t["page"] <= n_pages]
        cells = tbl["cells"]
        for para in nearby:
            # A bare "see Table N" with no cell tokens does not attach to every cell.
            ptoks = distinctive_tokens(para["text"])
            if cap:
                ptoks -= distinctive_tokens(cap)  # "table", "1-1" from the caption alone is not enough
            # Restore compounds that also appear in the caption AND in a cell
            # (e.g. nothing here). Table number like "1-1" is in the caption;
            # drop it unless we also have cell tokens.
            matched = []
            for c in cells:
                ctoks = distinctive_tokens(c.get("text") or "")
                if ptoks & ctoks:
                    matched.append(c)
            if not matched:
                continue
            region = _region_of(matched)
            # If the overlap is confined to a labelled group, clip to that group.
            for start, end in _group_row_ranges(cells, tbl["n_rows"]):
                if all(start <= c["row"] <= end for c in matched) and any(
                    start <= c["row"] <= end for c in matched
                ):
                    region["row_start"] = max(region["row_start"], start)
                    region["row_end"] = min(region["row_end"], end)
                    break
            possible_conflict = _is_conflict(ptoks, matched, cells)
            quote = para["text"]
            for c in matched:
                overlap = sorted(ptoks & distinctive_tokens(c.get("text") or ""))
                ref = {
                    "block_id": para["id"],
                    "page": para["page"],
                    "quote": quote,
                    "quote_sha256": _sha(quote),
                    # Candidates, not assertions: a reviewer must confirm the link.
                    "candidate": True,
                    "role": "conflict" if possible_conflict else "mentions",
                    "overlap": overlap,
                    "overlap_count": len(overlap),
                    "region": region,
                }
                c.setdefault("cross_references", []).append(ref)
        # Rank each cell's candidates by token-overlap evidence (higher first).
        for c in cells:
            refs = c.get("cross_references") or []
            refs.sort(key=lambda r: (-int(r.get("overlap_count") or 0), r.get("block_id") or ""))
            for i, r in enumerate(refs, 1):
                r["rank"] = i


# ---------------------------------------------------------------------------
# Held-crop index and human review
# ---------------------------------------------------------------------------

def held_index_path(conv_dir: Path) -> Path:
    return Path(conv_dir) / "verified" / "held_blocks.json"


def write_held_crops(pdf, dest_dir, entries, page_images):
    """Write PNG crops for held blocks that have a bbox. entries mutate in place.

    dest_dir is the verified-view output directory (verified/ or verified.tmp/).
    """
    dest_dir = Path(dest_dir)
    out = dest_dir / "held_crops"
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for e in entries:
        if e.get("status") not in {"held", "held, review"}:
            continue
        if not e.get("bbox") or not e.get("page"):
            e["crop_png"] = None
            written.append(e)
            continue
        page_no = e["page"]
        img = page_images[page_no]
        h = pdf.height(page_no)
        crop, geom = union_and_pad_crop(img, h, [e["bbox"]], pad_px=HELD_CROP_PAD_PX, scale=CROP_SCALE)
        name = e["block_id"].replace("/", "-").replace("#", "") + ".png"
        dest = dest_dir / "held_crops" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        crop.save(dest)
        e["crop_png"] = str(Path("verified") / "held_crops" / name)
        e["geom"] = geom
        written.append(e)
    idx = dest_dir / "held_blocks.json"
    idx.write_text(json.dumps(written, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return written


def get_held_crop(conv_dir, block_id: str) -> Path:
    idx = held_index_path(conv_dir)
    if not idx.is_file():
        raise FileNotFoundError(f"{idx} does not exist; build the verified view first")
    entries = json.loads(idx.read_text(encoding="utf-8"))
    hit = next((e for e in entries if e.get("block_id") == block_id), None)
    if hit is None:
        raise KeyError(f"{block_id!r} is not a held block")
    if hit.get("status") not in {"held", "held, review"}:
        raise PermissionError(f"{block_id!r} is {hit.get('status')!r}, not held")
    if not hit.get("crop_png"):
        raise FileNotFoundError(
            f"{block_id!r} is held but has no bbox, so no crop was written; get_held_crop fails closed"
        )
    path = Path(conv_dir) / hit["crop_png"]
    if not path.is_file():
        raise FileNotFoundError(f"held crop missing: {path}")
    return path


def cell_decisions(conv_dir: Path) -> dict:
    p = Path(conv_dir) / "decisions.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def apply_cell_review(conv_dir, block_id, decision="approve", reviewer=None, **kwargs):
    if not reviewer or not str(reviewer).strip():
        raise ValueError("a human reviewer field is required; the tool never auto-promotes")
    conv_dir = Path(conv_dir)
    view_path = next((conv_dir / "verified").glob("*.verified.json"), None)
    if view_path is None:
        raise FileNotFoundError("verified view not found; build it before recording a review")
    view = json.loads(view_path.read_text(encoding="utf-8"))
    cell, table = _find_cell(view, block_id)
    if cell is None:
        raise KeyError(f"{block_id!r} is not a table cell")
    if any(r.get("role") == "conflict" for r in (cell.get("cross_references") or [])):
        raise PermissionError(f"{block_id!r} has a text conflict and stays held")
    if decision != "approve":
        raise ValueError(f"unsupported decision {decision!r}")
    # Approve confirms the candidate links that were shown to the reviewer,
    # minus any the reviewer rejected. There is no automatic evidence.
    rejected = set(kwargs.get("rejected_block_ids") or [])
    confirmed = [
        r for r in (cell.get("cross_references") or [])
        if r.get("candidate") and r.get("block_id") not in rejected and r.get("quote_sha256")
    ]
    if "confirmed_block_ids" in kwargs and kwargs["confirmed_block_ids"] is not None:
        allow = set(kwargs["confirmed_block_ids"])
        confirmed = [r for r in confirmed if r["block_id"] in allow]
    evidence = {
        "cell_quote_sha256": cell.get("quote_sha256") or _sha(cell.get("text") or ""),
        "paragraph_quote_sha256": [r["quote_sha256"] for r in confirmed],
        "confirmed_block_ids": [r["block_id"] for r in confirmed],
    }
    record = {
        "decision": "human_verified",
        "reviewer": str(reviewer).strip(),
        "reviewed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cell_quote_sha256": evidence["cell_quote_sha256"],
        "paragraph_quote_sha256": evidence["paragraph_quote_sha256"],
    }
    decisions = cell_decisions(conv_dir)
    decisions[block_id] = record
    (conv_dir / "decisions.json").write_text(json.dumps(decisions, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return record


def _find_cell(view, block_id):
    # block_id like #/tables/0/r9c1
    m = re.match(r"(#/tables/\d+)/r(\d+)c(\d+)$", block_id)
    if not m:
        return None, None
    tid, row, col = m.group(1), int(m.group(2)), int(m.group(3))
    for b in view.get("blocks") or []:
        if b.get("type") == "table" and b.get("id") == tid:
            for c in b.get("cells") or []:
                if c["row"] == row and c["col"] == col:
                    return c, b
    return None, None


def apply_human_decisions(cells, table_id, conv_dir):
    decisions = cell_decisions(conv_dir)
    for c in cells:
        bid = f"{table_id}/r{c['row']}c{c['col']}"
        rec = decisions.get(bid)
        if not rec or rec.get("decision") != "human_verified":
            continue
        if not rec.get("reviewer"):
            continue
        if any(r.get("role") == "conflict" for r in (c.get("cross_references") or [])):
            continue  # conflict stays held
        if c.get("status") != "held":
            continue
        c["status"] = "human_verified"
        c["reviewer"] = rec["reviewer"]
        c["reviewed_at"] = rec.get("reviewed_at")
        c["evidence"] = {
            "cell_quote_sha256": rec.get("cell_quote_sha256"),
            "paragraph_quote_sha256": rec.get("paragraph_quote_sha256") or [],
        }
        c.pop("held_reason", None)



def enrich_verified_view(view, pdf, conv_dir, docling, profile=None, out_dir=None,
                         pdf_path=None, scan_agreement_config=None):
    """Add triage, folio metadata, per-cell status, candidates, HTML/MD/crops."""
    out_dir = Path(out_dir or (Path(conv_dir) / "verified"))
    out_dir.mkdir(parents=True, exist_ok=True)
    n_pages = len(pdf.doc)
    triages = {t["page"]: t for t in triage_document(pdf)}
    for ps in view["pages"]:
        tr = triages[ps["page"]]
        ps["triage"] = {"class": tr["class"], "signals": tr["signals"]}
        if tr["class"] == "scanned":
            ps["text_layer"] = False
    if profile:
        view["profile"] = profile
    if profile == "technical_manual":
        folios = detect_folios(pdf)
        for ps in view["pages"]:
            info = folios["pages"].get(ps["page"]) or {}
            if info.get("running_header"):
                ps["running_header"] = info["running_header"]
            if info.get("running_footer"):
                ps["running_footer"] = info["running_footer"]

    unverifiable = {
        ps["page"] for ps in view["pages"]
        if (ps.get("triage") or {}).get("class") in {"scanned", "broken_text_layer"}
    }
    for b in view["blocks"]:
        if unverifiable & set(b.get("pages") or []):
            if b.get("status") != "held":
                b["status"] = "held"
                reasons = list(b.get("held_reason") or [])
                reasons.append("page triage: content on a scanned or broken-text-layer page is held")
                b["held_reason"] = reasons

    # Body texts for candidate cross-reference
    xref_texts = []
    for item in docling.get("texts") or []:
        prov = (item.get("prov") or [{}])[0]
        xref_texts.append({
            "id": item.get("self_ref"),
            "page": prov.get("page_no"),
            "text": item.get("text") or "",
            "label": item.get("label"),
        })

    table_specs = []
    page_images = {}

    def page_image(page_no):
        if page_no not in page_images:
            pil = pdf.doc[page_no - 1].render(scale=CROP_SCALE).to_pil().convert("RGB")
            page_images[page_no] = pil
        return page_images[page_no]

    held_entries = []
    by_id = {b["id"]: b for b in view["blocks"]}
    for i, tb in enumerate(docling.get("tables") or []):
        ref = tb.get("self_ref") or f"#/tables/{i}"
        blk = by_id.get(ref)
        if blk is None:
            continue
        pr = (tb.get("prov") or [{}])[0]
        page_no = pr.get("page_no") or (blk.get("pages") or [None])[0]
        grid = (tb.get("data") or {}).get("grid") or []
        raw_cells = (tb.get("data") or {}).get("table_cells") or []
        n_rows = (tb.get("data") or {}).get("num_rows") or len(grid)
        n_cols = (tb.get("data") or {}).get("num_cols") or (len(grid[0]) if grid else 0)
        checked = [check_cell_box(pdf, page_no, c) for c in raw_cells]
        # If a later rebuild applies human decisions, do it after candidates.

        def resolve_ref(cref):
            kind, idx = cref["$ref"].split("/")[1], int(cref["$ref"].split("/")[2])
            return docling[kind][idx]

        caption = blk.get("caption")
        if not caption:
            caption = " ".join(resolve_ref(c).get("text") or "" for c in tb.get("captions") or []) or None
            blk["caption"] = caption
        footnote = " ".join(resolve_ref(c).get("text") or "" for c in tb.get("footnotes") or []) or None
        exclude = [c["$ref"] for c in (tb.get("captions") or []) + (tb.get("footnotes") or [])]
        cap_bbox = None
        for c in tb.get("captions") or []:
            item = resolve_ref(c)
            if item.get("prov"):
                cap_bbox = item["prov"][0].get("bbox")
                break
        fn_bbox = None
        for c in tb.get("footnotes") or []:
            item = resolve_ref(c)
            if item.get("prov"):
                fn_bbox = item["prov"][0].get("bbox")
                break
        table_specs.append({
            "id": ref, "page": page_no, "n_rows": n_rows, "n_cols": n_cols,
            "cells": checked, "caption": caption or "", "exclude_text_ids": exclude,
        })
        blk["_pending_export"] = {
            "cells": checked, "n_rows": n_rows, "n_cols": n_cols,
            "caption": caption, "footnote": footnote,
            "table_bbox": pr.get("bbox"), "cap_bbox": cap_bbox, "fn_bbox": fn_bbox,
            "page": page_no,
        }

    cross_reference_tables(xref_texts, table_specs, n_pages)

    for blk in view["blocks"]:
        pend = blk.pop("_pending_export", None)
        if not pend:
            continue
        cells = pend["cells"]
        apply_human_decisions(cells, blk["id"], conv_dir)
        blk["cells"] = cells
        blk["html"] = render_html_table(pend["caption"], cells, pend["n_rows"], pend["n_cols"], pend["footnote"])
        blk["markdown"] = render_markdown_table(pend["caption"], cells, pend["n_rows"], pend["n_cols"], pend["footnote"])
        boxes = [pend["table_bbox"], pend["cap_bbox"], pend["fn_bbox"]]
        page_no = pend["page"]
        page_h = pdf.height(page_no)
        neighbors = page_neighbors(docling, page_no, page_h, [b for b in boxes if b])
        img = page_image(page_no)
        crop, geom = union_and_pad_crop(img, page_h, [b for b in boxes if b],
                                        pad_px=CROP_PAD_PX, scale=CROP_SCALE, neighbors=neighbors)
        name = f"{blk['id'].replace('/', '-').replace('#', '')}-crop.png"
        dest = Path(out_dir) / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        crop.save(dest)
        blk["crop"] = str(Path("verified") / name)
        blk["crop_geom"] = geom
        for c in cells:
            if c.get("status") in {"held", "held, review"}:
                held_entries.append({
                    "block_id": f"{blk['id']}/r{c['row']}c{c['col']}",
                    "status": c["status"],
                    "held_reason": c.get("held_reason"),
                    "text": c.get("text"),
                    "bbox": c.get("bbox"),
                    "page": page_no,
                    "quote_sha256": c.get("quote_sha256"),
                })

    write_held_crops(pdf, out_dir, held_entries, page_images)

    try:
        from . import scan_agreement as sa
    except ImportError:  # run as a script: python scripts/verified_pdf/agent_tool.py ...
        import scan_agreement as sa
    path = pdf_path or getattr(pdf, "path", None)
    sa.apply_scan_pipeline(view, path, conv_dir, out_dir, config=scan_agreement_config)

    view["counts"] = {
        "verified": sum(b["status"] == "verified" for b in view["blocks"]),
        "held": sum(b["status"] == "held" for b in view["blocks"]),
        "scan_agreement": sum(b["status"] == "scan_agreement" for b in view["blocks"]),
        "needs_confirmation": sum(bool(b.get("needs_confirmation")) for b in view["blocks"]),
        "human_verified_cells": sum(
            1 for b in view["blocks"] if b.get("type") == "table"
            for c in b.get("cells") or [] if c.get("status") == "human_verified"
        ),
    }
    if view["counts"]["held"] or view["counts"]["scan_agreement"] or view.get("document_flags"):
        if view["document_status"] == "VERIFIED":
            view["document_status"] = "PARTIAL"
