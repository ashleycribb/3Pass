"""Verify a converted PDF (output of pdf_to_md_html.py) against the original PDF.

Usage:
  python scripts/verified_pdf/verify_conversion.py paper.pdf converted/paper            # writes review.json + review.html
  python scripts/verified_pdf/verify_conversion.py paper.pdf converted/paper --certify  # needs decisions.json for every flag

Principle: the extractor is not trusted. Every item is checked against evidence that
does not come from the extractor (the PDF's own text layer and page geometry, read with
pdfium). An item either PASSES a check or is marked
NEEDS_REVIEW with the page, the region and a crop of the PDF so a person can decide.
Nothing is auto-approved: a document is CERTIFIED only when every automated check
passed or a named human reviewer recorded a decision for that exact flag.

Checks
  T1 text-layer   page has an extractable text layer (scans cannot be verified automatically)
  T2 precision    every word of every text block is present in the PDF inside that block's box
  T3 recall       every word in the PDF text layer of a page is present in the output for that page
  T5 reachability every text/table/figure item is in the reading tree the verified view is built from
  T4 serialised   every text block actually appears in the .md and in the .html files
  B1 cell         every table cell's text equals the PDF text inside that cell's box
  B2 structure    all PDF text in the table area belongs to a cell; rows/columns line up in page order
  B3 csv          table CSV matches the table model
  F1 image        every figure file exists, decodes, is not blank
  F2 caption      every "Figure N"/"Table N" caption in the PDF appears in the output
  F3 uncaptioned  figures without a caption are listed for confirmation
  E1 formula      equations not decoded to text are listed
  O1 order        (with GROBID TEI) title comes before the first section heading
  B4 output tables  tables in the final .md and .html equal the verified cells
  M1 no additions   the final .md and .html contain no words that are absent from the PDF
"""
import argparse
import csv
import hashlib
import html
import json
import re
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageStat

WORD = re.compile(r"[^\W_]+", re.UNICODE)
CAPTION = re.compile(r"^\s*(Figure|Fig\.|Table)\s*(\d+)\s*[:.]", re.I | re.M)
PAD = 2.0          # points of tolerance around a box
MIN_TEXT_CHARS = 20


def norm_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(r"[\u00ad\ufffe\x02]\s*", "", s)        # soft/line-break hyphens (pdfium uses U+FFFE)
    s = re.sub(r"(\w)-[ \t]*\r?\n\s*(\w)", r"\1\2", s)  # visible hyphen at a line end
    return s


def words(s: str) -> list:
    return [w.lower() for w in WORD.findall(norm_text(s))]


def joined(s: str) -> str:
    return "".join(words(s))


class Pdf:
    def __init__(self, path):
        self.doc = pdfium.PdfDocument(str(path))
        self._tp = {}

    def height(self, page_no):
        return self.doc[page_no - 1].get_height()

    def textpage(self, page_no):
        if page_no not in self._tp:
            self._tp[page_no] = self.doc[page_no - 1].get_textpage()
        return self._tp[page_no]

    def page_text(self, page_no):
        return self.textpage(page_no).get_text_range()

    def is_blank(self, page_no, scale=0.25):
        """True only if the page has no text and renders as one single colour (nothing visible).
        Any mark at all (a speck, a stamp, a faint scan) makes it not blank."""
        if self.page_text(page_no).strip():
            return False
        img = self.doc[page_no - 1].render(scale=scale).to_pil().convert("L")
        lo, hi = img.getextrema()
        return lo == hi

    def box_text(self, page_no, bbox, origin):
        l, t, r, b = bbox["l"], bbox["t"], bbox["r"], bbox["b"]
        if origin == "TOPLEFT":
            h = self.height(page_no)
            t, b = h - t, h - b
        bottom, top = min(t, b), max(t, b)
        return self.textpage(page_no).get_text_bounded(l - PAD, bottom - PAD, r + PAD, top + PAD)

    def _words(self, page_no):
        """Words on the page in text order: (text, starts_new_line, [(x, y) char centres])."""
        cache = self.__dict__.setdefault("_word_cache", {})
        if page_no not in cache:
            tp = self.textpage(page_no)
            out, cur, newline = [], [], False
            for i in range(tp.count_chars()):
                ch = tp.get_text_range(i, 1)
                if not ch.strip():
                    if cur:
                        out.append(("".join(c for c, _ in cur), newline, [p for _, p in cur]))
                        cur, newline = [], False
                    newline = newline or ch in "\r\n"
                    continue
                l, b, r, t = tp.get_charbox(i)
                cur.append((ch, ((l + r) / 2, (b + t) / 2)))
            if cur:
                out.append(("".join(c for c, _ in cur), newline, [p for _, p in cur]))
            cache[page_no] = out
        return cache[page_no]

    def cell_text(self, page_no, bbox, origin):
        """PDF text that belongs to a table cell: every word with more than half of its character
        centres inside the cell box. Unlike box_text, glyph fragments of neighbouring lines that
        merely touch the box are not included, and a word is never cut at the box edge."""
        l, t, r, b = bbox["l"], bbox["t"], bbox["r"], bbox["b"]
        if origin == "TOPLEFT":
            h = self.height(page_no)
            t, b = h - t, h - b
        lo, hi = min(t, b), max(t, b)
        parts = []
        for word, newline, centres in self._words(page_no):
            inside = sum(1 for x, y in centres if l <= x <= r and lo <= y <= hi)
            if inside * 2 > len(centres):
                parts.append(("\n" if newline and parts else " " if parts else "") + word)
        return "".join(parts)

    def crop(self, page_no, bbox, origin, out_path, scale=2.0):
        page = self.doc[page_no - 1]
        w, h = page.get_width(), page.get_height()
        l, t, r, b = bbox["l"], bbox["t"], bbox["r"], bbox["b"]
        if origin == "BOTTOMLEFT":
            t, b = h - t, h - b
        top, bottom = min(t, b), max(t, b)
        img = page.render(scale=scale).to_pil()
        box = [max(0, (l - 6) * scale), max(0, (top - 6) * scale),
               min(w * scale, (r + 6) * scale), min(h * scale, (bottom + 6) * scale)]
        img.crop(box).save(out_path)


def load_doc(conv_dir: Path):
    js = next(conv_dir.glob("*.docling.json"))
    return js.stem.replace(".docling", ""), json.loads(js.read_text(encoding="utf-8"))


def reachable_refs(d) -> set:
    """Every item reachable from the document body (children + captions). Items outside this set are
    not part of the reading tree and therefore cannot appear in the verified view."""
    seen, stack = set(), [c["$ref"] for c in d["body"].get("children", [])]
    while stack:
        ref = stack.pop()
        if ref in seen:
            continue
        seen.add(ref)
        try:
            kind, idx = ref.split("/")[1], int(ref.split("/")[2])
            node = d[kind][idx]
        except (IndexError, KeyError, ValueError):
            continue  # dangling refs are reported by the agent view (fail closed)
        stack += [c["$ref"] for c in node.get("children", [])]
        stack += [c["$ref"] for c in node.get("captions", [])]
    return seen


GROUPS = ("text", "tables", "figures", "document")
CHECK_GROUP = {"T2": "text", "E1": "text",
               "B1": "tables", "B2": "tables", "B3": "tables", "B4": "tables",
               "F1": "figures", "F3": "figures",
               "T1": "document", "T1-blank": "document", "T3": "document", "T4": "document", "T4-in-image": "document",
               "T5": "document", "F2": "document", "M1": "document", "O1": "document"}
CHECK_ORDER = ["T1", "T5", "T2", "E1", "T4", "B1", "B2", "B3", "B4", "F1", "F3", "M1", "F2", "T3", "O1"]


def sort_flags(flags):
    """Deterministic order, so a parallel run (union of groups) equals a serial run."""
    return sorted(flags, key=lambda f: (CHECK_ORDER.index(f["check"]), f["page"] or 0, f["ref"] or "", f["id"]))


def _column_alignment_exempt(cell, cells) -> bool:
    """Centered column headers and single-cell group labels (AMP:, Burndy:) do not
    share an x-range with left-aligned data in the same column. They are still
    checked for leftover PDF text (B2) and per-cell wording (B1)."""
    if cell.get("column_header") or cell.get("row_section"):
        return True
    text = (cell.get("text") or "").strip()
    if text.endswith(":") and len(text) <= 40:
        row = cell["start_row_offset_idx"]
        nonempty = [c for c in cells
                    if c.get("start_row_offset_idx") == row and (c.get("text") or "").strip()]
        if len(nonempty) == 1:
            return True
    return False


def _peel_csv_cell(cell: str, parts: list):
    """Consume model leftovers that concatenate exactly to one CSV cell."""
    if cell == "":
        return True, parts
    for i, part in enumerate(parts):
        if not part:
            continue
        if cell == part:
            return True, parts[:i] + parts[i + 1:]
        if cell.startswith(part):
            ok, rest = _peel_csv_cell(cell[len(part):], parts[:i] + parts[i + 1:])
            if ok:
                return True, rest
    return False, parts


def _csv_covers_model(flat_model: Counter, flat_csv: Counter) -> bool:
    """Every model cell is present in the CSV, either as its own cell or as a
    contiguous piece of a longer CSV cell (header merged with a group label).
    A CSV value that cannot be partitioned into leftover model cells fails closed.
    """
    if not (flat_model - flat_csv):
        return True
    leftover_csv = list((flat_csv - flat_model).elements())
    leftover_model = list((flat_model - flat_csv).elements())
    parts = leftover_model
    for cell in leftover_csv:
        ok, parts = _peel_csv_cell(cell, parts)
        if not ok:
            return False
    return not parts



def verify(pdf_path: Path, conv_dir: Path, only=GROUPS, write=True, profile=None):
    """Run the checks. `only` selects check groups (see CHECK_GROUP) so they can run in
    parallel; the union of all groups is exactly the full verification."""
    only = set(only)
    want = lambda check: CHECK_GROUP[check] in only
    DOC = "document" in only
    stem, d = load_doc(conv_dir)
    reach = reachable_refs(d)
    pdf = Pdf(pdf_path)
    if profile == "technical_manual":
        try:
            from . import technical_manual as tm
        except ImportError:  # run as a script
            import technical_manual as tm
        folios = tm.detect_folios(pdf)
    else:
        folios = None
    md = (conv_dir / f"{stem}.md").read_text(encoding="utf-8")
    htm = (conv_dir / f"{stem}.html").read_text(encoding="utf-8")
    md_j = joined(html.unescape(md))   # Markdown escapes < and & as entities; they render correctly
    htm_plain = re.sub(r"list-style-type:\s*'([^']*)'[^>]*>", lambda m: ">" + m.group(1), htm)  # list markers
    html_j = joined(html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(style|script).*?</\1>", " ", htm_plain))))
    class _Passed(Counter):
        def __setitem__(self, k, v):
            if want(k):
                super().__setitem__(k, v)
    flags, passed = [], _Passed()
    crops = conv_dir / "review" / "crops"
    crops.mkdir(parents=True, exist_ok=True)

    def flag(check, detail, page=None, bbox=None, origin="BOTTOMLEFT", ref=None, extracted=None):
        if not want(check):
            return
        fid = f"{check}-" + hashlib.sha1(f"{check}|{ref}|{page}|{detail}".encode()).hexdigest()[:10]
        item = {"id": fid, "check": check, "page": page, "ref": ref, "detail": detail,
                "extracted": extracted, "status": "NEEDS_REVIEW"}
        if page and bbox:
            p = crops / f"{fid}.png"
            try:
                pdf.crop(page, bbox, origin, p)
                item["crop"] = str(p.relative_to(conv_dir))
            except Exception:
                pass
        flags.append(item)

    n_pages = len(pdf.doc)
    page_covered = {p: Counter() for p in range(1, n_pages + 1)}
    no_text_layer = set()

    # T1
    for p in range(1, n_pages + 1):
        if len(pdf.page_text(p).strip()) < MIN_TEXT_CHARS and pdf.is_blank(p):
            passed["T1-blank"] += 1      # intentionally blank page: nothing on it to lose
        elif len(pdf.page_text(p).strip()) < MIN_TEXT_CHARS:
            no_text_layer.add(p)
            flag("T1", "page has no usable text layer (scanned/image page); content cannot be verified automatically", p)
        else:
            passed["T1"] += 1

    # T5: items outside the reading tree would be silently dropped from the verified view
    for kind in ("texts", "tables", "pictures"):
        for it in d[kind]:
            if it["self_ref"] in reach or it.get("content_layer", "body") != "body":
                continue
            if kind == "texts" and not (it.get("text") or "").strip():
                continue
            pr = it["prov"][0] if it.get("prov") else None
            flag("T5", f"{kind[:-1]} is not in the document reading tree and would be omitted from the output",
                 pr and pr["page_no"], pr and pr["bbox"], pr and pr["bbox"]["coord_origin"], it["self_ref"],
                 (it.get("text") or "")[:300] or None)

    # Text blocks: T2 precision, T4 serialisation
    for t in d["texts"]:
        text = t.get("text") or ""
        if t["self_ref"] not in reach:
            continue  # reported by T5; its words do not count toward page recall (T3)
        if t.get("label") == "formula":
            if not text.strip():
                pr = t["prov"][0] if t.get("prov") else None
                flag("E1", "equation was not decoded to text", pr and pr["page_no"],
                     pr and pr["bbox"], pr and pr["bbox"]["coord_origin"], t["self_ref"])
            continue
        provs = t.get("prov", [])
        segs = []
        for pr in provs:
            seg = text[pr["charspan"][0]:pr["charspan"][1]] if len(provs) > 1 else text
            segs.append((pr, seg))
        for i, (pr, seg) in enumerate(segs):
            pg = pr["page_no"]
            page_covered[pg].update(words(seg) + words(t.get("marker") or ""))
            if pg in no_text_layer or not want("T2"):
                continue
            src_j = joined(pdf.box_text(pg, pr["bbox"], pr["bbox"]["coord_origin"]))
            missing = []
            seg_words = words(seg)
            for wi, w in enumerate(seg_words):
                if w in src_j:
                    continue
                # A word split across adjacent provenance boxes (page or column
                # break) is a fragment in this box and the rest in the neighbour.
                # Accept the edge fragment only when the compound is in the
                # combined PDF boxes — do not invent a completion.
                compound_ok = False
                if wi == len(seg_words) - 1 and i + 1 < len(segs):
                    nxt_pr, nxt_seg = segs[i + 1]
                    nxt_words = words(nxt_seg)
                    if nxt_words:
                        combined = src_j + joined(pdf.box_text(
                            nxt_pr["page_no"], nxt_pr["bbox"], nxt_pr["bbox"]["coord_origin"]))
                        if (w + nxt_words[0]) in combined:
                            compound_ok = True
                if wi == 0 and i > 0:
                    prev_pr, prev_seg = segs[i - 1]
                    prev_words = words(prev_seg)
                    if prev_words:
                        combined = src_j + joined(pdf.box_text(
                            prev_pr["page_no"], prev_pr["bbox"], prev_pr["bbox"]["coord_origin"]))
                        if (prev_words[-1] + w) in combined:
                            compound_ok = True
                if not compound_ok:
                    missing.append(w)
            if missing:
                flag("T2", f"words not found in the PDF at this location: {' '.join(missing[:25])}",
                     pg, pr["bbox"], pr["bbox"]["coord_origin"], t["self_ref"], seg[:500])
            else:
                passed["T2"] += 1
        in_image = (t.get("parent") or {}).get("$ref", "").startswith("#/pictures/")
        if in_image:
            passed["T4-in-image"] += 1   # chart/diagram label: shown inside the figure PNG (checked by F1)
        elif t.get("content_layer", "body") == "body" and text.strip():
            tj = joined(text)
            if tj and tj not in md_j:
                flag("T4", "text block missing from Markdown output", ref=t["self_ref"], extracted=text[:300])
            elif tj and tj not in html_j:
                flag("T4", "text block missing from HTML output", ref=t["self_ref"], extracted=text[:300])
            else:
                passed["T4"] += 1

    # Tables: B1 per-cell, B2 independent structure, B3 CSV
    for n, tb in enumerate(d["tables"], 1):
        if tb["self_ref"] not in reach:
            continue
        pr = tb["prov"][0]
        pg = pr["page_no"]
        cells = tb["data"]["table_cells"]
        for c in cells:
            page_covered[pg].update(words(c["text"]))
        if not want("B1"):
            continue
        bad = 0
        for c in cells:
            loc = f"table {n} cell r{c['start_row_offset_idx']}c{c['start_col_offset_idx']}"
            if not (c.get("text") or "").strip():
                # Empty cells are held at cell level in the verified view.
                # They are not a wording failure of the table as a whole.
                continue
            if not c.get("bbox"):
                bad += 1
                flag("B1", f"{loc}: table cell has no bbox",
                     pg, pr["bbox"], pr["bbox"]["coord_origin"], f"{tb['self_ref']}", c.get("text"))
                continue
            src = pdf.cell_text(pg, c["bbox"], c["bbox"]["coord_origin"])
            if joined(src) != joined(c["text"]):
                bad += 1
                flag("B1", f"{loc}: output '{c['text']}' vs PDF '{src.strip()}'",
                     pg, c["bbox"], c["bbox"]["coord_origin"], f"{tb['self_ref']}", c["text"])
        if not cells:
            flag("B1", f"table {n} has no recognised cells", pg, pr["bbox"], pr["bbox"]["coord_origin"], tb["self_ref"])
        elif not bad:
            passed["B1"] += 1
        # B2: grid checked against PDF geometry (independent of the table model's own text)
        h = pdf.height(pg)
        def tl(bb):  # -> (l, top, r, bottom) in top-left coordinates
            if bb["coord_origin"] == "BOTTOMLEFT":
                return bb["l"], h - bb["t"], bb["r"], h - bb["b"]
            return bb["l"], bb["t"], bb["r"], bb["b"]
        boxes = [tl(pr["bbox"])] + [tl(c["bbox"]) for c in cells if c.get("bbox")]
        region = {"l": min(b[0] for b in boxes), "t": min(b[1] for b in boxes),
                  "r": max(b[2] for b in boxes), "b": max(b[3] for b in boxes), "coord_origin": "TOPLEFT"}
        leftover = Counter(words(pdf.box_text(pg, region, "TOPLEFT"))) - Counter(w for c in cells for w in words(c["text"]))
        problems = []
        if leftover:
            problems.append(f"PDF text inside the table not assigned to any cell: {' '.join(sorted(leftover))[:200]}")
        def overlap(a, b):
            return min(a[1], b[1]) - max(a[0], b[0]) > 0
        lone_header = _lone_header_cells(cells)
        for axis, key, span in (("row", "start_row_offset_idx", "row_span"), ("column", "start_col_offset_idx", "col_span")):
            groups = {}
            for c in cells:
                if not (c.get("bbox") and c[span] == 1 and c["text"].strip()):
                    continue
                if axis == "column" and _column_alignment_exempt(c, cells):
                    continue
                if axis == "row" and id(c) in lone_header:
                    continue
                l, t0, r, b = tl(c["bbox"])
                groups.setdefault(c[key], []).append((t0, b) if axis == "row" else (l, r))
            for idx, iv in groups.items():
                # every cell must overlap the row's (column's) widest extent: a wrapped entry
                # (number on line 1, title on lines 1-2, page on line 2) lines up through its title
                big = max(iv, key=lambda x: x[1] - x[0])
                if any(not overlap(big, x) for x in iv if x is not big):
                    problems.append(f"cells in {axis} {idx} do not line up on the page")
            centres = [sum(a + b for a, b in groups[i]) / (2 * len(groups[i])) for i in sorted(groups)]
            if any(b <= a for a, b in zip(centres, centres[1:])):
                problems.append(f"{axis} order does not follow the page layout")
        if problems:
            flag("B2", f"table {n}: " + "; ".join(problems), pg, region, "TOPLEFT", tb["self_ref"])
        else:
            passed["B2"] += 1
        # B3
        csv_path = conv_dir / "tables" / f"table-{n}.csv"
        if csv_path.exists():
            rows = list(csv.reader(csv_path.open(encoding="utf-8")))
            flat_csv = Counter(joined(x) for r in rows for x in r if joined(x))
            flat_model = Counter(joined(c["text"]) for c in cells if joined(c["text"]))
            if not _csv_covers_model(flat_model, flat_csv):
                flag("B3", f"table {n}: CSV is missing cell values {list((flat_model - flat_csv))[:10]}", ref=tb["self_ref"])
            else:
                passed["B3"] += 1
        else:
            flag("B3", f"table {n}: CSV file missing", ref=tb["self_ref"])

    # Figures
    for n, pic in enumerate(d["pictures"], 1):
        pr = pic["prov"][0] if pic.get("prov") else None
        if DOC and pr and pic["self_ref"] in reach:
            page_covered[pr["page_no"]].update(words(pdf.box_text(pr["page_no"], pr["bbox"], pr["bbox"]["coord_origin"])))
        uri = (pic.get("image") or {}).get("uri")
        ok = False
        if uri and (conv_dir / uri).exists():
            try:
                im = Image.open(conv_dir / uri).convert("L")
                ok = min(im.size) >= 20 and max(ImageStat.Stat(im).stddev) > 2
            except Exception:
                ok = False
        if ok:
            passed["F1"] += 1
        else:
            flag("F1", f"figure {n}: image file missing, unreadable, tiny or blank", pr and pr["page_no"],
                 pr and pr["bbox"], pr and pr["bbox"]["coord_origin"], pic["self_ref"])
        if not pic.get("captions"):
            flag("F3", f"figure {n} has no caption attached; confirm it is a real figure and nothing is lost",
                 pr and pr["page_no"], pr and pr["bbox"], pr and pr["bbox"]["coord_origin"], pic["self_ref"])

    if want("B4"):
        _check_b4(d, md, htm, flag, passed)
    if DOC:
        _check_document(pdf, conv_dir, md, htm_plain, n_pages, no_text_layer, page_covered, flag, passed,
                        profile=profile, folios=folios)
    flags = sort_flags(flags)
    if not write:
        return {"flags": flags, "checks_passed": dict(passed)}
    return write_report(pdf_path, conv_dir, stem, flags, passed)


def _lone_header_cells(cells):
    """Header cells that are the only header text in their column of a stacked (multi-row) header.
    Such a cell labels its column whichever header row the model put it in (parts lists: "#",
    "Description", "Item Number" centred beside "Quantity Per Model" over model numbers), so it
    is left out of the row-alignment test. Its column, text and the leftover-text test still apply."""
    rows = sorted({c["start_row_offset_idx"] for c in cells})
    header = []
    for r in rows:
        if not any(c.get("column_header") for c in cells if c["start_row_offset_idx"] == r):
            break
        header.append(r)
    if len(header) < 2:
        return set()
    in_header = [c for c in cells if c["start_row_offset_idx"] in header and c["text"].strip()]
    per_col = Counter()
    for c in in_header:
        for k in range(c["start_col_offset_idx"], c["start_col_offset_idx"] + c.get("col_span", 1)):
            per_col[k] += 1
    return {id(c) for c in in_header if c.get("col_span", 1) == 1 and per_col[c["start_col_offset_idx"]] == 1}


def _html_grid(table):
    """An HTML table as a full grid: a cell with colspan/rowspan fills every grid slot it covers,
    so the result is comparable slot by slot with the verified grid whether or not the exporter
    merged spanned cells."""
    grid, pending = [], {}          # pending[(row, col)] = text carried down by rowspan
    for r, tr in enumerate(table.xpath(".//tr")):
        row, c = [], 0
        for td in tr.xpath("./th|./td"):
            while (r, c) in pending:
                row.append(pending.pop((r, c))); c += 1
            text = joined(td.text_content())
            cs, rs = int(td.get("colspan") or 1), int(td.get("rowspan") or 1)
            for dc in range(cs):
                row.append(text)
                for dr in range(1, rs):
                    pending[(r + dr, c + dc)] = text
            c += cs
        while (r, c) in pending:
            row.append(pending.pop((r, c))); c += 1
        grid.append(row)
    return grid


def _check_b4(d, md, htm, flag, passed):
    # B4: tables in the final .md and .html equal the verified table model, cell by cell
    model = [[[joined(c["text"]) for c in row] for row in tb["data"]["grid"]] for tb in d["tables"]]
    # Markdown has no spans: a table may also be written with each cell once, in its top-left slot
    origin = [[[joined(c["text"]) if (c.get("start_row_offset_idx"), c.get("start_col_offset_idx")) == (r, k) else ""
                for k, c in enumerate(row)] for r, row in enumerate(tb["data"]["grid"])] for tb in d["tables"]]
    md_tables, cur = [], []
    for line in md.splitlines() + [""]:
        if line.lstrip().startswith("|"):
            if not re.fullmatch(r"\s*\|[\s:|-]+\|\s*", line):
                cur.append([joined(html.unescape(x)) for x in line.strip().strip("|").split("|")])
        elif cur:
            md_tables.append(cur); cur = []
    from lxml import html as lh
    tree = lh.fromstring(htm)
    html_tables = [_html_grid(t) for t in tree.xpath("//table")]
    for kind, got in (("Markdown", md_tables), ("HTML", html_tables)):
        if len(got) != len(model):
            flag("B4", f"{kind} has {len(got)} tables but {len(model)} were verified")
        for n, (a, b) in enumerate(zip(model, got), 1):
            if a != b and not (kind == "Markdown" and origin[n - 1] == b):
                flag("B4", f"table {n} in the {kind} output differs from the verified cells", ref=f"#/tables/{n - 1}",
                     extracted=json.dumps(b)[:400])
            else:
                passed["B4"] += 1


def _check_document(pdf, conv_dir, md, htm_plain, n_pages, no_text_layer, page_covered, flag, passed,
                    profile=None, folios=None):
    # M1: the final .md / .html contain no words that are absent from the PDF
    pdf_all = Counter()
    for p in range(1, n_pages + 1):
        pdf_all.update(words(pdf.page_text(p)))
    md_plain = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", html.unescape(md))
    html_plain = html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(style|script|head).*?</\1>", " ", htm_plain)))
    for kind, txt in (("Markdown", md_plain), ("HTML", html_plain)):
        extra = Counter(words(txt)) - pdf_all
        if extra:
            flag("M1", f"{kind} contains {sum(extra.values())} word occurrence(s) not in the PDF: "
                 + " ".join(sorted(extra))[:300])
        else:
            passed["M1"] += 1

    # F2 caption numbering against the PDF text layer
    pdf_caps = set()
    for p in range(1, n_pages + 1):
        for m in CAPTION.finditer(norm_text(pdf.page_text(p))):
            pdf_caps.add((m.group(1).lower().startswith("tab"), int(m.group(2)), p))
    out_caps = {(m.group(1).lower().startswith("tab"), int(m.group(2))) for m in CAPTION.finditer(md)}
    out_caps |= {(m.group(1).lower().startswith("tab"), int(m.group(2)))
                 for m in re.finditer(r"(Figure|Fig\.|Table)\s*(\d+)\s*[:.]", md)}
    for is_tab, num, p in sorted(pdf_caps):
        if (is_tab, num) in out_caps:
            passed["F2"] += 1
        else:
            flag("F2", f"{'Table' if is_tab else 'Figure'} {num} caption is in the PDF but not in the output", p)

    # T3 recall per page. Under technical_manual, running folio text is page
    # metadata and is excluded from the page bag so it is not flagged as missing.
    try:
        from . import technical_manual as tm
    except ImportError:
        import technical_manual as tm  # noqa: F401
    for p in range(1, n_pages + 1):
        if p in no_text_layer:
            continue
        if profile == "technical_manual" and folios:
            layer = tm.text_excluding_regions(pdf, p, tm.folio_regions_bl(folios, p))
        else:
            layer = pdf.page_text(p)
        pdf_w = Counter(words(layer))
        missing = pdf_w - page_covered[p]
        if missing:
            listing = " ".join(f"{w}x{c}" if c > 1 else w for w, c in sorted(missing.items())[:40])
            flag("T3", f"page {p}: {sum(missing.values())} word occurrence(s) in the PDF are not in the output for this page: {listing}", p)
        else:
            passed["T3"] += 1

    # O1 reading order of title (needs GROBID TEI). Skipped for technical_manual.
    tei = next(conv_dir.glob("*.tei.xml"), None)
    if profile == "technical_manual":
        tei = None
    if tei:
        from lxml import etree
        ns = {"t": "http://www.tei-c.org/ns/1.0"}
        root = etree.parse(str(tei))
        title = "".join(root.xpath("//t:titleStmt/t:title[@type='main']//text()", namespaces=ns)).strip()
        heads = [m.start() for m in re.finditer(r"(?m)^#{1,6} ", md)]
        pos = md.lower().find(title.lower()) if title else -1
        body_heads = [h for h in heads if not re.match(r"#{1,6}\s*(abstract)\b", md[h:h + 20], re.I)]
        if title and pos >= 0 and body_heads and pos > body_heads[0] and pos > len(md) * 0.02:
            flag("O1", f"title '{title}' appears after the first section heading; reading order needs repair")
        elif title and pos < 0:
            flag("O1", f"title '{title}' (from GROBID) not found in output")
        else:
            passed["O1"] += 1


def write_report(pdf_path, conv_dir, stem, flags, passed):
    report = {
        "source_pdf": pdf_path.name,
        "source_sha256": hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
        "outputs_sha256": {f.name: hashlib.sha256(f.read_bytes()).hexdigest()
                           for f in [conv_dir / f"{stem}.md", conv_dir / f"{stem}.html"]},
        "verified_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "checks_passed": dict(sorted(passed.items())),
        "flags": flags,
        "flag_counts": dict(Counter(f["check"] for f in flags)),
        "status": "AUTO_VERIFIED" if not flags else "NEEDS_REVIEW",
        "note": "AUTO_VERIFIED means every automated check passed; it is not a human sign-off.",
    }
    (conv_dir / "review").mkdir(exist_ok=True)
    (conv_dir / "review" / "review.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_review_html(conv_dir, report)
    return report


def write_review_html(conv_dir: Path, rep):
    rows = []
    for f in rep["flags"]:
        img = f'<img src="{html.escape(Path(f["crop"]).relative_to("review").as_posix())}">' if f.get("crop") else ""
        rows.append(f"<tr><td>{f['id']}</td><td>{f['page'] or ''}</td><td>{html.escape(f['detail'])}"
                    f"<pre>{html.escape(f.get('extracted') or '')}</pre></td><td>{img}</td></tr>")
    body = "\n".join(rows) or "<tr><td colspan=4>No flags: every automated check passed.</td></tr>"
    (conv_dir / "review" / "review.html").write_text(f"""<!doctype html><meta charset="utf-8">
<title>Review: {html.escape(rep['source_pdf'])}</title>
<style>body{{font-family:system-ui,sans-serif;margin:2rem;color:#222}}table{{border-collapse:collapse;width:100%}}
td,th{{border:1px solid #ccc;padding:.4rem;vertical-align:top;font-size:14px}}img{{max-width:420px}}
pre{{white-space:pre-wrap;background:#f6f6f6;padding:.3rem}}</style>
<h1>Conversion review: {html.escape(rep['source_pdf'])}</h1>
<p>Status: <b>{rep['status']}</b>. Checks passed: {html.escape(json.dumps(rep['checks_passed']))}.
Flags: {html.escape(json.dumps(rep['flag_counts']))}.</p>
<p>For each flag, compare the PDF crop with the output, fix the .md/.html if needed, then record a decision
in <code>decisions.json</code> (accept, corrected, or not_an_error) with your name.</p>
<table><tr><th>ID</th><th>Page</th><th>Issue / extracted text</th><th>PDF region</th></tr>{body}</table>""",
                                                         encoding="utf-8")


def certify(pdf_path: Path, conv_dir: Path):
    rep = verify(pdf_path, conv_dir)   # always re-check the current files
    dec_path = conv_dir / "decisions.json"
    decisions = json.loads(dec_path.read_text(encoding="utf-8")) if dec_path.exists() else {}
    problems = []
    for f in rep["flags"]:
        dcs = decisions.get(f["id"])
        if not dcs:
            problems.append(f"{f['id']}: no human decision")
        elif dcs.get("decision") not in {"accept", "corrected", "not_an_error"} or not dcs.get("reviewer"):
            problems.append(f"{f['id']}: decision must be accept/corrected/not_an_error and name a reviewer")
    if problems:
        print("NOT CERTIFIED:\n  " + "\n  ".join(problems[:50]) + (f"\n  ... {len(problems) - 50} more" if len(problems) > 50 else ""))
        return 1
    stem = next(conv_dir.glob("*.docling.json")).stem.replace(".docling", "")
    cert = {"source_sha256": rep["source_sha256"], "certified_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "automated_checks_passed": rep["checks_passed"], "flags_resolved_by_human": len(rep["flags"]),
            "reviewers": sorted({v["reviewer"] for v in decisions.values()}),
            "final_outputs_sha256": {f.name: hashlib.sha256(f.read_bytes()).hexdigest()
                                     for f in [conv_dir / f"{stem}.md", conv_dir / f"{stem}.html"]}}
    (conv_dir / "certificate.json").write_text(json.dumps(cert, indent=2), encoding="utf-8")
    print("CERTIFIED", json.dumps(cert, indent=2))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf", type=Path)
    ap.add_argument("converted_dir", type=Path)
    ap.add_argument("--certify", action="store_true")
    ap.add_argument("--decisions-template", action="store_true",
                    help="write decisions.template.json listing every open flag for a reviewer to fill in")
    a = ap.parse_args()
    if a.decisions_template:
        rep = verify(a.pdf, a.converted_dir)
        tpl = {f["id"]: {"check": f["check"], "page": f["page"], "issue": f["detail"],
                         "decision": "", "reviewer": "", "note": ""} for f in rep["flags"]}
        (a.converted_dir / "decisions.template.json").write_text(json.dumps(tpl, indent=2), encoding="utf-8")
        print(f"wrote {len(tpl)} open flag(s) to decisions.template.json; copy to decisions.json after review")
        sys.exit(0)
    if a.certify:
        sys.exit(certify(a.pdf, a.converted_dir))
    rep = verify(a.pdf, a.converted_dir)
    print(json.dumps({k: rep[k] for k in ("source_pdf", "status", "checks_passed", "flag_counts")}, indent=2))
    sys.exit(0 if rep["status"] == "AUTO_VERIFIED" else 2)


if __name__ == "__main__":
    main()


def triage_page(pdf_path, page_no, pdf=None):
    """Classify one page: born_digital, scanned, or broken_text_layer."""
    try:
        from . import technical_manual as tm
    except ImportError:
        import technical_manual as tm
    return tm.triage_page(pdf_path, page_no, pdf=pdf)
