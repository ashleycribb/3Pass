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
import hashlib
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

    pictures, tables = [], []
    for item, _ in doc.iterate_items():
        prov = item.prov[0] if getattr(item, "prov", None) else None
        where = {"page": prov.page_no, "bbox": prov.bbox.as_tuple()} if prov else {}
        if isinstance(item, TableItem):
            n = len(tables) + 1
            df = item.export_to_dataframe(doc=doc)
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
