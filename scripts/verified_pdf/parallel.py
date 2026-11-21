"""Parallel conversion with the same outputs and the same checks as the serial path.

Usage:
  python scripts/verified_pdf/parallel.py paper.pdf --out out_dir [--mode shards|anchors]
         [--ocr] [--grobid URL] [--workers N]

Both modes end the same way: the check groups (text, tables, figures, document; see
verify_conversion.CHECK_GROUP) run in parallel, and their union is exactly the serial
verification. The verified view writes anchors.json (see inventory.py): every block with id,
kind, page, bounding box (top-left origin, PDF points), reading-order position and fingerprint.

--mode shards (default, faster)
  The PDF is cut into contiguous page ranges, one per worker. Each range goes through the
  unchanged serial Docling pipeline in its own process; the parts are joined in page order.

--mode anchors (anchor-and-workers)
  Stage 1  one Docling pass with layout + reading order only; text inside table regions is
           left to the table worker. Anchors are taken from this pass.
  Stage 2  in parallel: tables (TableFormer ACCURATE on the table pages only), figures
           (each anchor box rendered from the PDF with pypdfium2), text checks (T2, E1).
  Stage 3  table cells and figure images are attached to their anchors and written by the
           same writer as the serial converter.
  Fail-closed: a table anchor with no matching table gets no cells, so B1 flags it and it is
  held; if assembly changed any anchored text, the text checks run again on the final output.

Nothing is generated: every word still comes from Docling's reading of the PDF text layer, and
nothing reaches the verified view unless the verifier passes it.
"""
import argparse
import hashlib
import io
import json
import os
import shutil
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from importlib.metadata import version
from multiprocessing import get_context
from pathlib import Path

try:
    from . import verify_conversion as vc
    from . import agent_tool as at
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import verify_conversion as vc  # noqa: E402
    import agent_tool as at  # noqa: E402


def _conv():   # Docling is optional; import the converter only when converting
    try:
        from . import pdf_to_md_html
    except ImportError:
        import pdf_to_md_html
    return pdf_to_md_html

IOU_MATCH = 0.5


# ---------- stage 1: anchors ----------
def _layout_converter(ocr: bool, threads: int):
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    opts = PdfPipelineOptions()
    opts.do_ocr = ocr
    opts.do_table_structure = False
    opts.generate_picture_images = False
    opts.generate_page_images = False
    opts.accelerator_options.num_threads = threads
    return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})


def anchors_of(doc) -> list:
    from docling_core.types.doc import PictureItem, TableItem
    out = []
    for order, (item, _) in enumerate(doc.iterate_items()):
        kind = "table" if isinstance(item, TableItem) else "figure" if isinstance(item, PictureItem) else \
            "formula" if getattr(item, "label", "") == "formula" else "text"
        for pr in getattr(item, "prov", []) or []:
            bb = pr.bbox.to_top_left_origin(doc.pages[pr.page_no].size.height)
            out.append({"ref": item.self_ref, "kind": kind, "order": order, "page": pr.page_no,
                        "bbox": [bb.l, bb.t, bb.r, bb.b], "coord_origin": "TOPLEFT",
                        "captions": [c.cref for c in getattr(item, "captions", []) or []]})
    return out


def drop_table_interior_text(doc) -> int:
    """Without table structure, Docling also emits the words inside a table as body text blocks.
    In the serial pipeline those words belong to the table's cells, so remove them here (before
    anything is written or anchored) to match it. Captions and footnotes are kept."""
    tables = [(pr.page_no, pr.bbox.to_top_left_origin(doc.pages[pr.page_no].size.height))
              for tb in doc.tables for pr in tb.prov]
    drop = []
    for t in doc.texts:
        if str(getattr(t.label, "value", t.label)) in ("caption", "footnote", "page_header", "page_footer"):
            continue
        provs = [(pr.page_no, pr.bbox.to_top_left_origin(doc.pages[pr.page_no].size.height)) for pr in t.prov]
        if provs and all(any(pg == tp and bb.intersection_over_self(tbb) >= 0.9 for tp, tbb in tables)
                         for pg, bb in provs):
            drop.append(t)
    if drop:
        doc.delete_items(node_items=drop)
    return len(drop)


# ---------- stage 2: workers (top-level so they can run in other processes) ----------
def _runs(pages):
    runs, cur = [], []
    for p in sorted(set(pages)):
        if cur and p != cur[-1] + 1:
            runs.append((cur[0], cur[-1])); cur = []
        cur.append(p)
    if cur:
        runs.append((cur[0], cur[-1]))
    return runs


def table_worker(pdf: str, runs, ocr: bool, threads: int):
    """TableFormer on the given page ranges only. Returns [{page, bbox, origin, data}]."""
    t0 = time.time()
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
    from docling.document_converter import DocumentConverter, PdfFormatOption
    opts = PdfPipelineOptions()          # same table settings as pdf_to_md_html.build_converter
    opts.do_ocr = ocr
    opts.do_table_structure = True
    opts.table_structure_options.mode = TableFormerMode.ACCURATE
    opts.table_structure_options.do_cell_matching = True
    opts.accelerator_options.num_threads = threads
    conv_ = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
    found = []
    for a, b in runs:
        doc = conv_.convert(pdf, page_range=(a, b)).document
        for tb in doc.tables:
            pr = tb.prov[0]
            found.append({"page": pr.page_no, "bbox": list(pr.bbox.as_tuple()),
                          "origin": pr.bbox.coord_origin.value, "data": tb.data.model_dump(mode="json")})
    return {"tables": found, "seconds": round(time.time() - t0, 1)}


def figure_worker(pdf: str, figures, scale: float):
    """Render each figure anchor's box straight from the PDF. Returns {ref: png bytes}."""
    import pypdfium2 as pdfium
    t0 = time.time()
    d = pdfium.PdfDocument(pdf)
    out, pages = {}, {}
    for f in figures:
        p = f["page"]
        if p not in pages:
            pages[p] = d[p - 1].render(scale=scale).to_pil()
        img = pages[p]
        l, t, r, b = f["bbox"]          # anchors are TOPLEFT, in PDF points
        crop = img.crop((round(l * scale), round(t * scale), round(r * scale), round(b * scale)))
        buf = io.BytesIO(); crop.save(buf, "PNG")
        out[f["ref"]] = buf.getvalue()
    return {"images": out, "seconds": round(time.time() - t0, 1)}


def verify_worker(pdf: str, conv_dir: str, groups):
    t0 = time.time()
    r = vc.verify(Path(pdf), Path(conv_dir), only=groups, write=False)
    r["seconds"] = round(time.time() - t0, 1)
    return r


# ---------- stage 3: assembly ----------
def _iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    lo, hi = sorted((a[1], a[3])), sorted((b[1], b[3]))
    iy = max(0.0, min(lo[1], hi[1]) - max(lo[0], hi[0]))
    inter = ix * iy
    area = lambda x: abs(x[2] - x[0]) * abs(x[3] - x[1])
    return inter / (area(a) + area(b) - inter) if inter else 0.0


def attach_tables(doc, found, warnings):
    from docling_core.types.doc import TableData
    used = set()
    for n, tb in enumerate(doc.tables, 1):
        pr = tb.prov[0]
        box = pr.bbox.as_tuple()
        best = max(((i, _iou(box, f["bbox"])) for i, f in enumerate(found)
                    if i not in used and f["page"] == pr.page_no and f["origin"] == pr.bbox.coord_origin.value),
                   key=lambda x: x[1], default=(None, 0.0))
        if best[0] is not None and best[1] >= IOU_MATCH:
            used.add(best[0])
            tb.data = TableData.model_validate(found[best[0]]["data"])
        else:   # fail closed: no cells -> B1 flags the table and it is held
            tb.data = TableData(num_rows=0, num_cols=0, table_cells=[])
            warnings.append(f"table {n} (page {pr.page_no}): table worker found no matching table")
    for i, f in enumerate(found):
        if i not in used:
            warnings.append(f"table worker found a table on page {f['page']} with no layout anchor; not used")


def attach_figures(doc, images, scale, warnings):
    from PIL import Image
    from docling_core.types.doc import ImageRef
    for pic in doc.pictures:
        png = images.get(pic.self_ref)
        if png:
            pic.image = ImageRef.from_pil(Image.open(io.BytesIO(png)), dpi=round(72 * scale))
        else:
            warnings.append(f"figure {pic.self_ref}: no image rendered")   # F1 will flag it


def _text_fingerprint(doc):
    return hashlib.sha256(json.dumps(
        [(t.self_ref, t.text, str(t.parent.cref if t.parent else ""),
          [(p.page_no, p.bbox.as_tuple(), tuple(p.charspan)) for p in t.prov]) for t in doc.texts]
    ).encode()).hexdigest()


# ---------- page shards ----------
def shard_worker(pdf: str, first: int, last: int, ocr: bool, scale: float, threads: int):
    """The full serial Docling pipeline (same settings), on pages first..last only."""
    from docling.datamodel.base_models import InputFormat
    t0 = time.time()
    c = _conv().build_converter(ocr, scale)
    c.format_to_options[InputFormat.PDF].pipeline_options.accelerator_options.num_threads = threads
    res = c.convert(pdf, page_range=(first, last))
    return {"doc": res.document.export_to_dict(), "errors": [str(e) for e in (res.errors or [])],
            "seconds": round(time.time() - t0, 1)}


def run_shards(pdf: Path, out_root: Path, ocr=False, scale=2.0, grobid=None, workers=None) -> dict:
    """Split the PDF into contiguous page ranges, convert each with the unchanged serial pipeline in
    its own process, join them in page order, then run the check groups in parallel."""
    import pypdfium2 as pdfium
    from docling_core.types.doc import DoclingDocument
    pdf = Path(pdf).resolve()
    out = out_root / pdf.stem
    shutil.rmtree(out, ignore_errors=True)
    workers = workers or os.cpu_count() or 2
    n_pages = len(pdfium.PdfDocument(str(pdf)))
    n = max(1, min(workers, n_pages))
    cuts = [round(i * n_pages / n) for i in range(n + 1)]
    t0 = time.time()
    timings = {}
    ctx = get_context("spawn")
    with ProcessPoolExecutor(max_workers=n, mp_context=ctx) as pool:
        parts = list(pool.map(shard_worker, [str(pdf)] * n, [cuts[i] + 1 for i in range(n)],
                              [cuts[i + 1] for i in range(n)], [ocr] * n, [scale] * n, [1] * n))
    docs = [DoclingDocument.model_validate(p["doc"]) for p in parts]
    doc = DoclingDocument.concatenate(docs) if len(docs) > 1 else docs[0]
    doc.name = pdf.stem
    timings["1_shards"] = round(time.time() - t0, 1)
    timings["1_shard_seconds"] = [p["seconds"] for p in parts]
    warnings = [e for p in parts for e in p["errors"]]
    manifest = _conv().write_outputs(doc, pdf, out, warnings, t0, ocr, scale, grobid, extra={
        "mode": f"parallel (page shards: {[(cuts[i] + 1, cuts[i + 1]) for i in range(n)]})",
        "tools": {k: version(k) for k in ("docling", "docling-core", "docling-ibm-models", "pypdfium2")},
    })
    return _verify_and_view(pdf, out, manifest, timings, t0, ctx, workers, list(vc.GROUPS), [])


def _verify_and_view(pdf, out, manifest, timings, t0, ctx, workers, groups, extra_parts):
    t4 = time.time()
    with ProcessPoolExecutor(max_workers=min(len(groups), workers), mp_context=ctx) as pool:
        parts = list(pool.map(verify_worker, [str(pdf)] * len(groups), [str(out)] * len(groups),
                              [[g] for g in groups])) + extra_parts
    flags = vc.sort_flags([f for p in parts for f in p["flags"]])
    passed = sum((Counter(p["checks_passed"]) for p in parts), Counter())
    report = vc.write_report(pdf, out, pdf.stem, flags, passed)
    timings["4_verify"] = round(time.time() - t4, 1)
    view = at.build_verified_view(pdf, out, report=report)
    timings["total"] = round(time.time() - t0, 1)
    manifest["timings"] = timings
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"manifest": manifest, "report": report, "view": view}


# ---------- driver ----------
def run(pdf: Path, out_root: Path, ocr=False, scale=2.0, grobid=None, workers=None) -> dict:
    pdf = Path(pdf).resolve()
    out = out_root / pdf.stem
    shutil.rmtree(out, ignore_errors=True)
    stage = out_root / f".{pdf.stem}.stage1"
    shutil.rmtree(stage, ignore_errors=True)
    workers = workers or os.cpu_count() or 2
    t0 = time.time()
    timings = {}

    # stage 1
    res = _layout_converter(ocr, workers).convert(pdf)
    doc = res.document
    warnings = [str(e) for e in (res.errors or [])]
    dropped = drop_table_interior_text(doc)
    anchors = anchors_of(doc)
    fp1 = _text_fingerprint(doc)
    timings["1_anchors"] = round(time.time() - t0, 1)
    # the text checks only need the anchored text; give them a provisional copy of the outputs
    _conv().write_outputs(doc, pdf, stage, [], t0, ocr, scale)

    # stage 2
    t2 = time.time()
    table_pages = [a["page"] for a in anchors if a["kind"] == "table"]
    figures = [a for a in anchors if a["kind"] == "figure"]
    ctx = get_context("spawn")    # fresh processes: no torch state inherited from stage 1
    with ProcessPoolExecutor(max_workers=min(3, workers), mp_context=ctx) as pool:
        f_tab = pool.submit(table_worker, str(pdf), _runs(table_pages), ocr, max(1, workers - 1)) if table_pages else None
        f_fig = pool.submit(figure_worker, str(pdf), figures, scale)
        f_txt = pool.submit(verify_worker, str(pdf), str(stage), ["text"])
        tab = f_tab.result() if f_tab else {"tables": [], "seconds": 0.0}
        fig = f_fig.result()
        txt = f_txt.result()
    timings["2_workers"] = round(time.time() - t2, 1)
    timings["2_worker_seconds"] = {"tables": tab["seconds"], "figures": fig["seconds"], "text": txt["seconds"]}

    # stage 3
    t3 = time.time()
    attach_tables(doc, tab["tables"], warnings)
    attach_figures(doc, fig["images"], scale, warnings)
    text_unchanged = _text_fingerprint(doc) == fp1
    manifest = _conv().write_outputs(doc, pdf, out, warnings, t0, ocr, scale, grobid, extra={
        "mode": "parallel (anchor-and-workers)",
        "table_structure_pages": sorted(set(table_pages)),
        "table_interior_text_blocks_dropped": dropped,
        "tools": {k: version(k) for k in ("docling", "docling-core", "docling-ibm-models", "pypdfium2")},
    })
    crops = stage / "review" / "crops"
    if crops.exists():
        (out / "review" / "crops").mkdir(parents=True, exist_ok=True)
        for c in crops.iterdir():
            shutil.move(str(c), out / "review" / "crops" / c.name)
    shutil.rmtree(stage, ignore_errors=True)
    timings["3_assemble"] = round(time.time() - t3, 1)

    # stage 4
    groups = ["tables", "figures", "document"] + ([] if text_unchanged else ["text"])
    manifest["text_checks_reused_from_stage2"] = text_unchanged
    return _verify_and_view(pdf, out, manifest, timings, t0, ctx, workers, groups, [txt] if text_unchanged else [])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdfs", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, default=Path("converted"))
    ap.add_argument("--ocr", action="store_true")
    ap.add_argument("--scale", type=float, default=2.0)
    ap.add_argument("--grobid")
    ap.add_argument("--workers", type=int)
    ap.add_argument("--mode", choices=("shards", "anchors"), default="shards",
                    help="shards: page ranges through the unchanged pipeline (default); "
                         "anchors: layout pass, then table/figure/text workers")
    a = ap.parse_args()
    failed = 0
    for pdf in a.pdfs:
        try:
            r = (run_shards if a.mode == "shards" else run)(pdf, a.out, a.ocr, a.scale, a.grobid, a.workers)
            print(json.dumps({"pdf": pdf.name, "status": r["view"]["document_status"],
                              "counts": r["view"]["counts"], "timings": r["manifest"]["timings"]}))
        except Exception as exc:
            failed += 1
            print(f"{pdf.name}: FAILED: {exc}", file=sys.stderr)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
