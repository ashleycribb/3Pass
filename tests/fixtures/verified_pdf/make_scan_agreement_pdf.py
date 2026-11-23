"""Generate synthetic image-only and broken-layer PDFs for scan-agreement tests.

Invented content only. PDFs are written at test time and must not be committed.
Real manuals must not enter the repository.
"""
from __future__ import annotations

import json
from pathlib import Path

import pymupdf

PAGE_W, PAGE_H = 612.0, 792.0
# Distinctive invented phrases (not drawn from any real manual).
AGREE_TEXT = "The fixture code is ALPHA-937."
BROKEN_VISIBLE = "Broken layer sample OMEGA-24680."
NOISE_LABEL = "noise"


def _rect_dict(rect) -> dict:
    return {
        "l": float(rect.x0),
        "t": float(rect.y0),
        "r": float(rect.x1),
        "b": float(rect.y1),
        "coord_origin": "TOPLEFT",
    }


def _image_only_from_page(src_page, dest_path: Path, rotate_degrees: float = 0.0) -> None:
    """Paint src_page to a bitmap and write a PDF page that has no text layer."""
    pix = src_page.get_pixmap(dpi=150, alpha=False)
    if rotate_degrees:
        # Rotate the bitmap; the output page stays the original size.
        tmp = pymupdf.open()
        # Use a pixmap rotate via pillow if available, else pymupdf IRect clip.
        from PIL import Image, ImageDraw  # noqa: F401
        import io
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        img = img.rotate(rotate_degrees, expand=False, fillcolor=(255, 255, 255), resample=Image.Resampling.BICUBIC)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        out = pymupdf.open()
        page = out.new_page(width=src_page.rect.width, height=src_page.rect.height)
        page.insert_image(page.rect, stream=buf.read())
    else:
        out = pymupdf.open()
        page = out.new_page(width=src_page.rect.width, height=src_page.rect.height)
        page.insert_image(page.rect, pixmap=pix)
    out.set_metadata({"title": "Scan Agreement Fixture", "creationDate": "D:20261123000000Z",
                      "modDate": "D:20261123000000Z"})
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    out.save(dest_path, garbage=4, deflate=True, no_new_id=True)
    out.close()


def build_agree_scanned(out_dir: Path) -> dict:
    """One image-only page with a single clear line of invented text."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    src = pymupdf.open()
    page = src.new_page(width=PAGE_W, height=PAGE_H)
    rect = pymupdf.Rect(72, 200, 540, 280)
    page.insert_textbox(rect, AGREE_TEXT, fontsize=18, fontname="helv")
    pdf_path = out_dir / "agree_scanned.pdf"
    _image_only_from_page(page, pdf_path, rotate_degrees=0.0)
    src.close()
    return {
        "pdf": pdf_path,
        "text": AGREE_TEXT,
        "pages": [{
            "page": 1,
            "text": AGREE_TEXT,
            "bbox": _rect_dict(rect),
            "label": "text",
            "kind": "scanned_agree",
        }],
    }


def build_skewed_scanned(out_dir: Path, degrees: float = 4.0) -> dict:
    """Image-only page whose painted text is rotated by `degrees`."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    src = pymupdf.open()
    page = src.new_page(width=PAGE_W, height=PAGE_H)
    rect = pymupdf.Rect(72, 200, 540, 280)
    page.insert_textbox(rect, AGREE_TEXT, fontsize=18, fontname="helv")
    pdf_path = out_dir / "skewed_scanned.pdf"
    _image_only_from_page(page, pdf_path, rotate_degrees=degrees)
    src.close()
    return {
        "pdf": pdf_path,
        "text": AGREE_TEXT,
        "skew_degrees": degrees,
        "pages": [{
            "page": 1,
            "text": AGREE_TEXT,
            "bbox": _rect_dict(rect),
            "label": "text",
            "kind": "scanned_skewed",
        }],
    }


def build_broken_with_visible_text(out_dir: Path) -> dict:
    """Broken text layer (junk) plus an image of real invented text on the same page."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    # Visible glyphs as an image, then a junk text layer so triage is broken_text_layer.
    src = pymupdf.open()
    page = src.new_page(width=PAGE_W, height=PAGE_H)
    rect = pymupdf.Rect(72, 200, 540, 280)
    page.insert_textbox(rect, BROKEN_VISIBLE, fontsize=18, fontname="helv")
    pix = page.get_pixmap(dpi=150, alpha=False)
    src.close()

    out = pymupdf.open()
    op = out.new_page(width=PAGE_W, height=PAGE_H)
    op.insert_image(op.rect, pixmap=pix)
    junk = ("§•×¤‡" * 20) + "\ufffd\ufffd\ufffd" + ("///..." * 8)
    op.insert_textbox(pymupdf.Rect(72, 500, 540, 720), junk, fontsize=12, fontname="helv")
    pdf_path = out_dir / "broken_visible.pdf"
    out.set_metadata({"title": "Broken Layer Fixture", "creationDate": "D:20261123000000Z",
                      "modDate": "D:20261123000000Z"})
    out.save(pdf_path, garbage=4, deflate=True, no_new_id=True)
    out.close()
    return {
        "pdf": pdf_path,
        "text": BROKEN_VISIBLE,
        "pages": [{
            "page": 1,
            "text": BROKEN_VISIBLE,
            "bbox": _rect_dict(rect),
            "label": "text",
            "kind": "broken_visible",
        }],
    }


def write_scan_conversion(conv_dir: Path, meta: dict, stem: str) -> Path:
    """Write a minimal Docling-shaped conversion (no Docling run) for a scan fixture."""
    conv_dir = Path(conv_dir)
    conv_dir.mkdir(parents=True, exist_ok=True)
    texts = []
    body_children = []
    for i, item in enumerate(meta["pages"]):
        ref = f"#/texts/{i}"
        text = item["text"]
        texts.append({
            "self_ref": ref,
            "parent": {"$ref": "#/body"},
            "children": [],
            "content_layer": "body",
            "label": item.get("label") or "text",
            "orig": text,
            "text": text,
            "prov": [{
                "page_no": item["page"],
                "bbox": item["bbox"],
                "charspan": [0, len(text)],
            }],
        })
        body_children.append({"$ref": ref})
    pages = {}
    for item in meta["pages"]:
        pages[str(item["page"])] = {"size": {"width": PAGE_W, "height": PAGE_H}, "page_no": item["page"]}
    doc = {
        "schema_name": "DoclingDocument",
        "version": "1.12.0",
        "name": stem,
        "origin": {"mimetype": "application/pdf", "filename": Path(meta["pdf"]).name},
        "furniture": {"self_ref": "#/furniture", "children": [], "content_layer": "furniture", "name": "_root_", "label": "unspecified"},
        "body": {"self_ref": "#/body", "children": body_children, "content_layer": "body", "name": "_root_", "label": "unspecified"},
        "groups": [],
        "texts": texts,
        "pictures": [],
        "tables": [],
        "key_value_items": [],
        "form_items": [],
        "pages": pages,
    }
    (conv_dir / f"{stem}.docling.json").write_text(json.dumps(doc), encoding="utf-8")
    body_md = "\n\n".join(item["text"] for item in meta["pages"])
    (conv_dir / f"{stem}.md").write_text(body_md, encoding="utf-8")
    (conv_dir / f"{stem}.html").write_text(
        "<html><body>" + "".join(f"<p>{item['text']}</p>" for item in meta["pages"]) + "</body></html>",
        encoding="utf-8",
    )
    return conv_dir
