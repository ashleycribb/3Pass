"""Convert a PDF into Markdown and HTML, keeping text, tables and images.

Usage:
  python scripts/verified_pdf/pdf_to_md_html.py paper.pdf --out out_dir [--ocr] [--grobid http://localhost:8070]

Writes, under out_dir/<pdf stem>/:
  <stem>.md            Markdown; images referenced from images/
  <stem>.html          standalone HTML; same images referenced from images/
  <stem>.docling.json  lossless document model (page number + bounding box per item)
  images/              one PNG per picture/figure (vector charts are rendered crops)
  tables/              each table as CSV; tables are also real tables in the .md and .html
  manifest.json        counts, SHA-256 of the input PDF, tool versions, and warnings
  <stem>.tei.xml       (only with --grobid) GROBID TEI for header and reference metadata

Layout, reading order, tables (TableFormer) and figure regions come from Docling,
so vector charts are captured as rendered crops, not just embedded bitmaps.
Nothing is generated or summarised: all text is what the PDF contains.
"""
import argparse
import csv
import hashlib
import html
import json
import sys
import time
from importlib.metadata import version
from pathlib import Path

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc import ImageRefMode, PictureItem, TableItem


def build_converter(ocr: bool, scale: float) -> DocumentConverter:
    opts = PdfPipelineOptions()
    opts.do_ocr = ocr                      # off for born-digital PDFs; on for scans
    opts.do_table_structure = True
    opts.table_structure_options.mode = TableFormerMode.ACCURATE
    opts.table_structure_options.do_cell_matching = True
    opts.generate_picture_images = True
    opts.generate_table_images = True
    opts.images_scale = scale              # 2.0 is about 144 dpi
    return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})


def grobid_tei(pdf: Path, url: str):
    import httpx
    with pdf.open("rb") as fh:
        r = httpx.post(f"{url.rstrip('/')}/api/processFulltextDocument",
                       files={"input": fh}, data={"consolidateHeader": "0"}, timeout=180)
    r.raise_for_status()
    return r.text


def convert(pdf: Path, out_root: Path, ocr=False, scale=2.0, grobid=None) -> dict:
    out = out_root / pdf.stem
    (out / "images").mkdir(parents=True, exist_ok=True)
    (out / "tables").mkdir(exist_ok=True)
    t0 = time.time()
    result = build_converter(ocr, scale).convert(pdf)
    warnings = [str(e) for e in (result.errors or [])]
    return write_outputs(result.document, pdf, out, warnings, t0, ocr, scale, grobid)


def header_rows(item) -> int:
    """Number of leading grid rows that hold column headers."""
    n = 0
    for row in item.data.grid:
        if not any(c.column_header for c in row):
            break
        n += 1
    return n


def has_spans(item) -> bool:
    """True when any cell spans more than one row or column."""
    return any(c.row_span > 1 or c.col_span > 1 for c in item.data.table_cells)


def origin_grid(item) -> list:
    """The table grid with each cell's text written once, in its top-left slot; slots a spanned
    cell merely covers are empty. Used where the format has no spans (Markdown, CSV) and the
    table has a stacked header, so no header text is repeated, joined or invented."""
    return [[c.text if (c.start_row_offset_idx, c.start_col_offset_idx) == (r, k) else ""
             for k, c in enumerate(row)] for r, row in enumerate(item.data.grid)]


def _md_cell(text: str) -> str:
    return html.escape(" ".join(text.split()), quote=False).replace("|", "&#124;")


def markdown_table(grid: list) -> str:
    lines = ["| " + " | ".join(_md_cell(x) for x in row) + " |" for row in grid]
    lines.insert(1, "|" + "|".join("---" for _ in grid[0]) + "|")
    return "\n".join(lines)


def replace_markdown_tables(md: str, replacements: dict) -> str:
    """Replace the n-th Markdown table block (0-based) with replacements[n]."""
    out, block, n = [], [], 0
    for line in md.split("\n") + [None]:
        if line is not None and line.lstrip().startswith("|"):
            block.append(line)
            continue
        if block:
            out.extend([replacements[n]] if n in replacements else block)
            block, n = [], n + 1
        if line is not None:
            out.append(line)
    return "\n".join(out)


def _md_table_blocks(md: str):
    prev = False
    for line in md.split("\n"):
        cur = line.lstrip().startswith("|")
        if cur and not prev:
            yield line
        prev = cur


def write_outputs(doc, pdf: Path, out: Path, warnings, t0, ocr, scale, grobid=None, extra=None) -> dict:
    """Write .md, .html, .docling.json, images/, tables/*.csv and manifest.json for a DoclingDocument.
    Shared by the serial converter and the anchor-and-workers path (parallel.py)."""
    (out / "images").mkdir(parents=True, exist_ok=True)
    (out / "tables").mkdir(exist_ok=True)

    # Markdown + HTML share the same referenced image files.
    doc.save_as_markdown(out / f"{pdf.stem}.md", artifacts_dir=Path("images"),
                         image_mode=ImageRefMode.REFERENCED)
    doc.save_as_html(out / f"{pdf.stem}.html", artifacts_dir=Path("images"),
                     image_mode=ImageRefMode.REFERENCED)
    doc.save_as_json(out / f"{pdf.stem}.docling.json", artifacts_dir=Path("images"),
                     image_mode=ImageRefMode.REFERENCED)

    pictures, tables, md_fix = [], [], {}
    for item, _ in doc.iterate_items():
        prov = item.prov[0] if getattr(item, "prov", None) else None
        where = {"page": prov.page_no, "bbox": prov.bbox.as_tuple()} if prov else {}
        if isinstance(item, TableItem):
            n = len(tables) + 1
            df = item.export_to_dataframe(doc=doc)
            if header_rows(item) > 1 or has_spans(item):
                # Docling folds stacked headers into one row ("Quantity Per Model - 351RCPG12NL") and
                # repeats a spanned cell's text in every slot it covers ("LIST OF TABLES" x3), so the
                # output holds words the PDF prints once. Write each cell once instead.
                grid = origin_grid(item)
                with (out / "tables" / f"table-{n}.csv").open("w", encoding="utf-8", newline="") as fh:
                    csv.writer(fh).writerows(grid)
                md_fix[n - 1] = markdown_table(grid)
            else:
                df.to_csv(out / "tables" / f"table-{n}.csv", index=False)
            caption = item.caption_text(doc)
            tables.append({"n": n, **where, "rows": int(df.shape[0]), "cols": int(df.shape[1]),
                           "caption": caption})
            if df.empty:
                warnings.append(f"table {n} (page {where.get('page')}) has no recognised cells")
        elif isinstance(item, PictureItem):
            pictures.append({"n": len(pictures) + 1, **where, "caption": item.caption_text(doc)})

    if grobid:
        try:
            (out / f"{pdf.stem}.tei.xml").write_text(grobid_tei(pdf, grobid), encoding="utf-8")
        except Exception as exc:  # GROBID is optional enrichment
            warnings.append(f"GROBID step failed: {exc}")

    md = (out / f"{pdf.stem}.md").read_text(encoding="utf-8")
    if md_fix:
        if sum(1 for _ in _md_table_blocks(md)) == len(tables):
            md = replace_markdown_tables(md, md_fix)
            (out / f"{pdf.stem}.md").write_text(md, encoding="utf-8")
        else:
            warnings.append("stacked table headers left as Docling wrote them: Markdown table count differs")
    manifest = {
        "source_pdf": pdf.name,
        "source_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
        "pages": doc.num_pages(),
        "text_items": sum(1 for t in doc.texts),
        "markdown_chars": len(md),
        "tables": tables,
        "pictures": pictures,
        "ocr": ocr,
        "image_scale": scale,
        "seconds": round(time.time() - t0, 1),
        "tools": {"docling": version("docling"), "docling-core": version("docling-core")},
        **(extra or {}),
        "warnings": warnings,
        "outputs": sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdfs", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, default=Path("converted"))
    ap.add_argument("--ocr", action="store_true", help="run OCR (needed for scanned PDFs)")
    ap.add_argument("--scale", type=float, default=2.0, help="image render scale")
    ap.add_argument("--grobid", help="optional GROBID URL to also save TEI metadata")
    a = ap.parse_args()
    failed = 0
    for pdf in a.pdfs:
        try:
            m = convert(pdf, a.out, a.ocr, a.scale, a.grobid)
            print(f"{pdf.name}: {m['pages']} pages, {len(m['tables'])} tables, "
                  f"{len(m['pictures'])} pictures, {m['seconds']}s, warnings={len(m['warnings'])}")
        except Exception as exc:
            failed += 1
            print(f"{pdf.name}: FAILED: {exc}", file=sys.stderr)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
