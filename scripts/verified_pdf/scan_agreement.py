"""Scanned / broken-text-layer page agreement: rasterize, deskew, dual OCR.

Pages that triage marks `scanned` or `broken_text_layer` are rasterized at
300 DPI (SCAN_RASTER_DPI), deskewed, and read by two independent OCR
engines (Tesseract and RapidOCR). Per-block character error rate (CER) is

    levenshtein(norm(a), norm(b)) / max(len(norm(a)), norm(b)))

with NFKC + whitespace collapse. A block becomes `scan_agreement` only when
both readings are non-empty and CER is at or below the caller-supplied
`max_character_error_rate`. That threshold is required configuration: there
is no silent default and no DEFAULT_MAX_CER.

When a page is triaged `scanned` and the converter's detected regions cover
less of the page's inked area than the required `min_inked_region_coverage`
(a number in [0, 1]; no silent default), a full-page or ink-bbox `scan_page`
block is added and run through the same dual-OCR CER check. A missing
coverage key fails closed. The fallback is not auto-promoted: it is
`scan_agreement` only within the CER threshold, otherwise `held`, and never
`verified`. It does not apply to `broken_text_layer` pages.

`scan_agreement` is never `verified` and is not citable by the answer path.
It can become `human_verified` only through apply_scan_review (reviewer,
timestamp, both OCR text sha256 values, crop sha256).

pack_scan_agreement_record emits a tile 52 (`to_scan_agreement`) payload
that links to an existing section or cell by id and carries status, both
OCR engine names and versions, both text hashes, the crop hash, CER, and
the required `max_character_error_rate`. Missing required fields fail
closed; there is no silent default. `scan_agreement` is never packed as
`verified`.
"""
from __future__ import annotations

import hashlib
import math
import struct
import io
import json
import re
import unicodedata
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version as pkg_version
from pathlib import Path

from PIL import Image

SCAN_RASTER_DPI = 300
ENGINE_A = "tesseract"
ENGINE_B = "rapidocr"
# A raster pixel is inked when its grayscale value is at most INK_GRAY_MAX.
# This is the definition of "ink", not a coverage threshold and not a default
# for min_inked_region_coverage.
INK_GRAY_MAX = 250
# How the two readings are compared. "text" (the original rule): the whole crop as one string.
# "labels": each text line is matched to the other engine's lines by position and compared on its
# own, for drawings, floor plans and other pages of scattered labels (see classify_labels).
COMPARISONS = {"text", "labels"}
# In "labels" mode Tesseract runs in sparse-text mode ("find as much text as possible in no
# particular order"), the mode Tesseract documents for this kind of page.
TESSERACT_LABELS_CONFIG = "--psm 11"


class ScanAgreementError(Exception):
    """A required scan-agreement input or dependency is missing or invalid."""


class ScanAgreementPackError(ScanAgreementError):
    """s3a-pack-to cannot carry the required scan_agreement fields."""


def _sha(data) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _norm(text: str) -> str:
    s = unicodedata.normalize("NFKC", text or "")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            ins, delete, sub = cur[j - 1] + 1, prev[j] + 1, prev[j - 1] + (ca != cb)
            cur.append(min(ins, delete, sub))
        prev = cur
    return prev[-1]


def character_error_rate(a: str, b: str) -> float:
    """Normalized Levenshtein distance. Both-empty is not a scorable agreement."""
    na, nb = _norm(a), _norm(b)
    if not na and not nb:
        raise ScanAgreementError("character error rate is undefined when both strings are empty")
    denom = max(len(na), len(nb))
    return _levenshtein(na, nb) / denom


def require_config(config) -> dict:
    """Return a validated config. Missing or invalid thresholds fail closed."""
    if not isinstance(config, dict):
        raise ScanAgreementError("scan_agreement_config is required and must be a mapping")
    if "max_character_error_rate" not in config:
        raise ScanAgreementError("scan_agreement_config.max_character_error_rate is required; there is no default")
    if "min_inked_region_coverage" not in config:
        raise ScanAgreementError("scan_agreement_config.min_inked_region_coverage is required; there is no default")
    try:
        thr = float(config["max_character_error_rate"])
    except (TypeError, ValueError) as exc:
        raise ScanAgreementError("max_character_error_rate must be a number in [0, 1]") from exc
    if not (0.0 <= thr <= 1.0) or isinstance(config["max_character_error_rate"], bool):
        raise ScanAgreementError("max_character_error_rate must be a number in [0, 1]")
    try:
        cov = float(config["min_inked_region_coverage"])
    except (TypeError, ValueError) as exc:
        raise ScanAgreementError("min_inked_region_coverage must be a number in [0, 1]") from exc
    if not (0.0 <= cov <= 1.0) or isinstance(config["min_inked_region_coverage"], bool):
        raise ScanAgreementError("min_inked_region_coverage must be a number in [0, 1]")
    comparison = config.get("comparison", "text")
    if comparison not in COMPARISONS:
        raise ScanAgreementError(f"comparison must be one of {sorted(COMPARISONS)}")
    return {
        "max_character_error_rate": thr,
        "min_inked_region_coverage": cov,
        "comparison": comparison,
    }


def classify_block(*, engine_a, engine_b, crop_bytes: bytes, config) -> dict:
    cfg = require_config(config)
    ta = (engine_a or {}).get("text") or ""
    tb = (engine_b or {}).get("text") or ""
    if not _norm(ta) or not _norm(tb):
        cer = None
        status = "held"
        reason = "one or both OCR engines produced empty text"
    else:
        cer = character_error_rate(ta, tb)
        if cer <= cfg["max_character_error_rate"]:
            status = "scan_agreement"
            reason = None
        else:
            status = "held"
            reason = (
                f"OCR character error rate {cer:.4f} exceeds "
                f"max_character_error_rate {cfg['max_character_error_rate']}"
            )
    rec = {
        "status": status,
        "cer": cer,
        "ocr": [
            {
                "name": engine_a["name"],
                "version": engine_a.get("version") or "",
                "text": ta,
                "text_sha256": _sha(ta),
            },
            {
                "name": engine_b["name"],
                "version": engine_b.get("version") or "",
                "text": tb,
                "text_sha256": _sha(tb),
            },
        ],
        "crop_sha256": _sha(crop_bytes),
    }
    if reason:
        rec["held_reason"] = reason
    return rec


def tesseract_version() -> str:
    try:
        import pytesseract
    except ImportError as exc:
        raise ScanAgreementError("pytesseract is not installed") from exc
    try:
        ver = pytesseract.get_tesseract_version()
    except Exception as exc:
        raise ScanAgreementError(f"tesseract binary is not available: {exc}") from exc
    return str(ver)


def rapidocr_version() -> str:
    try:
        return pkg_version("rapidocr-onnxruntime")
    except PackageNotFoundError as exc:
        raise ScanAgreementError("rapidocr-onnxruntime is not installed") from exc


def run_engine(name: str, image) -> dict:
    """Run one named OCR engine on a PIL image. Missing engines fail closed."""
    if name == ENGINE_A:
        try:
            import pytesseract
        except ImportError as exc:
            raise ScanAgreementError("pytesseract is not installed") from exc
        try:
            text = pytesseract.image_to_string(image) or ""
            ver = tesseract_version()
        except Exception as exc:
            raise ScanAgreementError(f"tesseract failed: {exc}") from exc
        return {"name": ENGINE_A, "version": ver, "text": text.strip()}
    if name == ENGINE_B:
        try:
            import numpy as np
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:
            raise ScanAgreementError("rapidocr-onnxruntime is not installed") from exc
        try:
            result = RapidOCR()(np.array(image.convert("RGB")))
        except Exception as exc:
            raise ScanAgreementError(f"rapidocr failed: {exc}") from exc
        text = _parse_rapidocr(result)
        return {"name": ENGINE_B, "version": rapidocr_version(), "text": text.strip()}
    raise ScanAgreementError(f"unknown OCR engine {name!r}")


def engine_lines(name: str, image) -> dict:
    """Run one OCR engine and return its text lines with pixel boxes:
    {"name", "version", "config", "lines": [(x0, y0, x1, y1, text), ...]}. Missing engines fail closed."""
    if name == ENGINE_A:
        try:
            import pytesseract
        except ImportError as exc:
            raise ScanAgreementError("pytesseract is not installed") from exc
        try:
            d = pytesseract.image_to_data(image, config=TESSERACT_LABELS_CONFIG,
                                          output_type=pytesseract.Output.DICT)
            ver = tesseract_version()
        except Exception as exc:
            raise ScanAgreementError(f"tesseract failed: {exc}") from exc
        groups = {}
        for i, t in enumerate(d["text"]):
            if not (t or "").strip():
                continue
            key = (d["block_num"][i], d["par_num"][i], d["line_num"][i])
            x, y, w, h = d["left"][i], d["top"][i], d["width"][i], d["height"][i]
            groups.setdefault(key, []).append((x, y, x + w, y + h, t.strip()))
        lines = []
        for ws in groups.values():
            ws.sort()
            lines.append((min(w[0] for w in ws), min(w[1] for w in ws), max(w[2] for w in ws),
                          max(w[3] for w in ws), " ".join(w[4] for w in ws)))
        return {"name": ENGINE_A, "version": ver, "config": TESSERACT_LABELS_CONFIG, "lines": lines}
    if name == ENGINE_B:
        try:
            import numpy as np
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:
            raise ScanAgreementError("rapidocr-onnxruntime is not installed") from exc
        try:
            result = RapidOCR()(np.array(image.convert("RGB")))
        except Exception as exc:
            raise ScanAgreementError(f"rapidocr failed: {exc}") from exc
        data = result[0] if isinstance(result, tuple) else result
        lines = []
        for item in data or []:
            quad, text = item[0], item[1]
            if not str(text).strip():
                continue
            xs, ys = [float(p[0]) for p in quad], [float(p[1]) for p in quad]
            lines.append((min(xs), min(ys), max(xs), max(ys), str(text).strip()))
        return {"name": ENGINE_B, "version": rapidocr_version(), "config": "", "lines": lines}
    raise ScanAgreementError(f"unknown OCR engine {name!r}")


def _boxes_intersect(a, b) -> bool:
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def _reading_order(lines):
    return " ".join(x[4] for x in sorted(lines, key=lambda z: (z[1], z[0])))


def match_labels(lines_a, lines_b) -> list:
    """Group the two engines' lines into labels: lines whose boxes intersect a line of the other
    engine join one label (connected components, so one engine's "1001 137sf" can meet the other's
    "1001" + "137sf"). No overlap threshold: boxes either intersect or not. Returns
    [{"box": (x0, y0, x1, y1), "a": [...lines], "b": [...lines]}] in reading order."""
    nodes = [("a", x) for x in lines_a] + [("b", x) for x in lines_b]
    parent = list(range(len(nodes)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            if nodes[i][0] != nodes[j][0] and _boxes_intersect(nodes[i][1], nodes[j][1]):
                parent[find(i)] = find(j)
    comps = {}
    for i, n in enumerate(nodes):
        comps.setdefault(find(i), []).append(n)
    out = []
    for comp in comps.values():
        boxes = [x for _, x in comp]
        out.append({"box": (min(x[0] for x in boxes), min(x[1] for x in boxes),
                            max(x[2] for x in boxes), max(x[3] for x in boxes)),
                    "a": [x for k, x in comp if k == "a"], "b": [x for k, x in comp if k == "b"]})
    return sorted(out, key=lambda c: (c["box"][1], c["box"][0]))


def classify_labels(*, engine_a, engine_b, crop_bytes: bytes, config, to_points) -> dict:
    """Label-by-label agreement. Every label must be read by both engines and agree within
    max_character_error_rate; a label read by only one engine, or none at all, keeps the block
    held. Each label carries its own status. The block's cer is the worst label CER (None when a
    label is one-sided). to_points maps a pixel box to a TOPLEFT PDF-point bbox dict."""
    cfg = require_config(config)
    thr = cfg["max_character_error_rate"]
    labels = []
    for comp in match_labels(engine_a["lines"], engine_b["lines"]):
        ta, tb = _reading_order(comp["a"]), _reading_order(comp["b"])
        if _norm(ta) and _norm(tb):
            cer = character_error_rate(ta, tb)
            status = "scan_agreement" if cer <= thr else "held"
            reason = None if status == "scan_agreement" else f"CER {cer:.4f} exceeds {thr}"
        else:
            cer, status = None, "held"
            reason = f"read by {engine_a['name'] if _norm(ta) else engine_b['name']} only"
        lab = {"bbox": to_points(comp["box"]), "text_a": ta, "text_b": tb, "cer": cer, "status": status}
        if reason:
            lab["held_reason"] = reason
        labels.append(lab)
    ta = _reading_order(engine_a["lines"])
    tb = _reading_order(engine_b["lines"])
    held = [x for x in labels if x["status"] != "scan_agreement"]
    if not labels:
        status, reason, cer = "held", "one or both OCR engines produced empty text", None
    elif held:
        one_sided = sum(1 for x in held if x["cer"] is None)
        status = "held"
        reason = (f"{len(held)} of {len(labels)} labels not agreed "
                  f"({one_sided} read by one engine only, {len(held) - one_sided} over "
                  f"max_character_error_rate {thr})")
        cer = None if one_sided else max(x["cer"] for x in labels)
    else:
        status, reason, cer = "scan_agreement", None, max(x["cer"] for x in labels)
    rec = {
        "status": status,
        "cer": cer,
        "comparison": "labels",
        "labels": labels,
        "ocr": [
            {"name": e["name"], "version": e.get("version") or "", "config": e.get("config") or "",
             "text": t, "text_sha256": _sha(t)}
            for e, t in ((engine_a, ta), (engine_b, tb))
        ],
        "crop_sha256": _sha(crop_bytes),
    }
    if reason:
        rec["held_reason"] = reason
    return rec


def _parse_rapidocr(result) -> str:
    data = result[0] if isinstance(result, tuple) else result
    if not data:
        return ""
    texts = []
    for item in data:
        if isinstance(item, (list, tuple)) and len(item) >= 2 and isinstance(item[1], str):
            texts.append(item[1])
        elif isinstance(item, dict) and item.get("text"):
            texts.append(str(item["text"]))
    return " ".join(texts)


def rasterize_page(pdf_path, page_no: int):
    """Rasterize a 1-based PDF page at exactly 300 DPI."""
    import pymupdf

    path = Path(pdf_path)
    if not path.is_file():
        raise ScanAgreementError(f"PDF not found: {path}")
    doc = pymupdf.open(path)
    try:
        if page_no < 1 or page_no > len(doc):
            raise ScanAgreementError(f"page {page_no} is out of range")
        page = doc[page_no - 1]
        pix = page.get_pixmap(dpi=SCAN_RASTER_DPI, alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    finally:
        doc.close()
    return img, {"dpi": SCAN_RASTER_DPI, "width": img.size[0], "height": img.size[1]}


def deskew(img: Image.Image):
    """Estimate skew and rotate to upright. Missing OpenCV fails closed."""
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise ScanAgreementError("OpenCV (cv2) is required to deskew scanned pages") from exc
    if img.mode != "L":
        gray = np.array(img.convert("L"))
    else:
        gray = np.array(img)
    _thr, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    coords = cv2.findNonZero(bw)
    if coords is None or len(coords) < 16:
        return img, {"skew_degrees": 0.0, "corrected": False}
    rect = cv2.minAreaRect(coords)
    (width, height), angle = rect[1], float(rect[2])
    if width < height:
        angle = angle + 90.0 if angle < 0 else angle - 90.0
    while angle <= -45:
        angle += 90.0
    while angle > 45:
        angle -= 90.0
    report = {"skew_degrees": angle, "corrected": abs(angle) >= 0.5}
    if not report["corrected"]:
        return img, report
    # Rotate the opposite way to correct.
    rotated = img.rotate(-angle, expand=False, fillcolor=(255, 255, 255) if img.mode == "RGB" else 255,
                         resample=Image.Resampling.BICUBIC)
    return rotated, report


def _crop_to_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _bbox_to_px(bbox, scale: float, page_height_pt: float) -> tuple[int, int, int, int]:
    l, t, r, b = float(bbox["l"]), float(bbox["t"]), float(bbox["r"]), float(bbox["b"])
    origin = (bbox.get("coord_origin") or "TOPLEFT").upper()
    if origin == "BOTTOMLEFT":
        t, b = page_height_pt - t, page_height_pt - b
    x0, x1 = min(l, r), max(l, r)
    y0, y1 = min(t, b), max(t, b)
    return (
        int(round(x0 * scale)),
        int(round(y0 * scale)),
        int(round(x1 * scale)),
        int(round(y1 * scale)),
    )


def process_block(pdf_path, page_no, bbox, config, page_cache=None) -> dict:
    """Rasterize, deskew, crop, OCR, and classify one block on a scan page."""
    require_config(config)
    cache = page_cache if page_cache is not None else {}
    if page_no not in cache:
        raster, info = rasterize_page(pdf_path, page_no)
        deskewed, skew = deskew(raster)
        cache[page_no] = {"image": deskewed, "info": info, "skew": skew}
    page = cache[page_no]
    img = page["image"]
    scale = SCAN_RASTER_DPI / 72.0
    if bbox:
        page_h_pt = img.size[1] / scale
        x0, y0, x1, y1 = _bbox_to_px(bbox, scale, page_h_pt)
        pad = int(round(8 * scale / 2))  # a few pixels so glyphs are not clipped
        x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
        x1, y1 = min(img.size[0], x1 + pad), min(img.size[1], y1 + pad)
        if x1 <= x0 or y1 <= y0:
            raise ScanAgreementError(f"block crop is empty on page {page_no}")
        crop = img.crop((x0, y0, x1, y1))
    else:
        x0 = y0 = 0
        crop = img
    crop_bytes = _crop_to_bytes(crop)
    if require_config(config)["comparison"] == "labels":
        def to_points(box, ox=x0, oy=y0):
            return {"l": (box[0] + ox) / scale, "t": (box[1] + oy) / scale, "r": (box[2] + ox) / scale,
                    "b": (box[3] + oy) / scale, "coord_origin": "TOPLEFT"}
        rec = classify_labels(engine_a=engine_lines(ENGINE_A, crop), engine_b=engine_lines(ENGINE_B, crop),
                              crop_bytes=crop_bytes, config=config, to_points=to_points)
    else:
        engine_a = run_engine(ENGINE_A, crop)
        engine_b = run_engine(ENGINE_B, crop)
        rec = classify_block(engine_a=engine_a, engine_b=engine_b, crop_bytes=crop_bytes, config=config)
    rec["crop_image"] = crop
    rec["crop_bytes"] = crop_bytes
    rec["skew"] = page["skew"]
    rec["dpi"] = page["info"]["dpi"]
    return rec



def block_bbox(block) -> dict | None:
    """Return a TOPLEFT {l,t,r,b} box from a view block, or None."""
    box = block.get("bbox")
    if isinstance(box, dict) and "l" in box:
        return box
    prov = (block.get("provenance") or [{}])[0]
    box = prov.get("bbox") if isinstance(prov, dict) else None
    if isinstance(box, dict) and "l" in box:
        return box
    if isinstance(box, (list, tuple)) and len(box) == 4:
        return {"l": float(box[0]), "t": float(box[1]), "r": float(box[2]), "b": float(box[3]),
                "coord_origin": (prov or {}).get("coord_origin") or "TOPLEFT"}
    return None



def _gray(img: Image.Image):
    """Return a 2-D uint8 grayscale array for ink measurements."""
    import numpy as np
    return np.asarray(img.convert("L"))


def inked_region_coverage(img: Image.Image, region_boxes_px) -> float:
    """Fraction of inked pixels that fall inside the union of region boxes.

    Inked means grayscale value <= INK_GRAY_MAX. Boxes are (x0, y0, x1, y1)
    in raster pixels, clipped to the image. If the page has no ink, coverage
    is 1.0 (there is no missed ink to recover).
    """
    import numpy as np
    arr = _gray(img)
    ink = arr <= INK_GRAY_MAX
    total = int(ink.sum())
    if total == 0:
        return 1.0
    mask = np.zeros(arr.shape, dtype=bool)
    h, w = arr.shape
    for box in region_boxes_px or []:
        x0, y0, x1, y1 = box
        x0, x1 = int(max(0, min(w, x0))), int(max(0, min(w, x1)))
        y0, y1 = int(max(0, min(h, y0))), int(max(0, min(h, y1)))
        if x1 > x0 and y1 > y0:
            mask[y0:y1, x0:x1] = True
    return float((ink & mask).sum() / total)


def ink_bbox_pdf_points(img: Image.Image, scale: float) -> dict | None:
    """TOPLEFT PDF-point bbox of inked pixels, or None when the page has no ink."""
    import numpy as np
    arr = _gray(img)
    ink = arr <= INK_GRAY_MAX
    if not ink.any():
        return None
    ys, xs = np.where(ink)
    pad = int(round(8 * scale / 2))
    h, w = arr.shape
    x0 = max(0, int(xs.min()) - pad)
    y0 = max(0, int(ys.min()) - pad)
    x1 = min(w, int(xs.max()) + 1 + pad)
    y1 = min(h, int(ys.max()) + 1 + pad)
    return {
        "l": x0 / scale,
        "t": y0 / scale,
        "r": x1 / scale,
        "b": y1 / scale,
        "coord_origin": "TOPLEFT",
    }


def _page_triage_class(view, page_no: int) -> str | None:
    for ps in view.get("pages") or []:
        if ps.get("page") == page_no:
            return (ps.get("triage") or {}).get("class")
    return None


def _has_page_scan_block(view, page_no: int) -> bool:
    bid = f"#/pages/{page_no}/scan"
    for b in view.get("blocks") or []:
        if page_no not in (b.get("pages") or []):
            continue
        if b.get("type") == "scan_page" or b.get("id") == bid:
            return True
    return False


def _detected_region_boxes(view, page_no: int, img: Image.Image) -> list:
    """Converter region bboxes on this page, in raster pixels (not scan_page)."""
    scale = SCAN_RASTER_DPI / 72.0
    page_h_pt = img.size[1] / scale
    boxes = []
    for b in view.get("blocks") or []:
        if b.get("type") == "scan_page" or (b.get("id") or "").startswith("#/pages/"):
            continue
        if page_no not in (b.get("pages") or []):
            continue
        bbox = block_bbox(b)
        if not bbox:
            continue
        boxes.append(_bbox_to_px(bbox, scale, page_h_pt))
    return boxes


def apply_scan_pipeline(view, pdf_path, conv_dir, out_dir, config=None) -> None:
    """Run the scan pipeline on held blocks that sit on scanned/broken pages.

    If config is missing, those blocks stay held and the reason names the
    missing threshold. No default is invented.
    """
    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir)
    scan_pages = {
        ps["page"]
        for ps in view.get("pages") or []
        if (ps.get("triage") or {}).get("class") in {"scanned", "broken_text_layer"}
    }
    if not scan_pages:
        return
    cache = {}
    cfg = None
    cfg_error = None
    try:
        cfg = require_config(config)
    except (ScanAgreementError, TypeError, ValueError, KeyError) as exc:
        cfg_error = str(exc)
    for b in view.get("blocks") or []:
        pages = [p for p in (b.get("pages") or []) if p in scan_pages]
        if not pages:
            continue
        if cfg is None:
            b["status"] = "held"
            reasons = list(b.get("held_reason") or [])
            msg = f"scan agreement config missing or invalid: {cfg_error}"
            if msg not in reasons:
                reasons.append(msg)
            b["held_reason"] = reasons
            continue
        page_no = pages[0]
        bbox = block_bbox(b)
        try:
            rec = process_block(pdf_path, page_no, bbox, cfg, page_cache=cache)
        except ScanAgreementError as exc:
            b["status"] = "held"
            reasons = list(b.get("held_reason") or [])
            msg = f"scan agreement failed closed: {exc}"
            if msg not in reasons:
                reasons.append(msg)
            b["held_reason"] = reasons
            continue
        name = f"{b['id'].replace('/', '-').replace('#', '')}-scan-crop.png"
        dest = out_dir / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        rec["crop_image"].save(dest)
        b["status"] = rec["status"]
        b["ocr"] = rec["ocr"]
        b["cer"] = rec["cer"]
        b["crop_sha256"] = rec["crop_sha256"]
        b["crop"] = str(Path("verified") / name)
        if rec.get("labels") is not None:
            b["labels"] = rec["labels"]
        b["scan"] = {
            "dpi": rec["dpi"],
            "skew": rec["skew"],
            "engines": [
                {"name": e["name"], "version": e["version"]} for e in rec["ocr"]
            ],
        }
        if rec["status"] == "held":
            reasons = list(b.get("held_reason") or [])
            if rec.get("held_reason") and rec["held_reason"] not in reasons:
                reasons.append(rec["held_reason"])
            b["held_reason"] = reasons
        else:
            # Agreement is not text-layer verification; drop the triage hold reason
            # so the block is labelled scan_agreement only.
            b.pop("held_reason", None)
        del rec["crop_image"]
        del rec["crop_bytes"]
    # A scanned/broken page with no converter blocks still gets a full-page
    # crop so the page itself is rasterized, deskewed, and read.
    # A scanned page whose detected regions cover too little of the ink also
    # gets a full-page or ink-bbox crop. Coverage is compared to the required
    # min_inked_region_coverage; there is no silent default.
    covered = {p for b in view.get("blocks") or [] for p in (b.get("pages") or []) if b.get("ocr") or (b.get("held_reason") and "scan agreement" in " ".join(b.get("held_reason") or []))}
    if cfg is not None:
        for page_no in sorted(scan_pages):
            if _has_page_scan_block(view, page_no):
                continue
            need_page_crop = page_no not in covered
            if not need_page_crop and _page_triage_class(view, page_no) == "scanned":
                if page_no not in cache:
                    raster, info = rasterize_page(pdf_path, page_no)
                    deskewed, skew = deskew(raster)
                    cache[page_no] = {"image": deskewed, "info": info, "skew": skew}
                img = cache[page_no]["image"]
                boxes = _detected_region_boxes(view, page_no, img)
                coverage = inked_region_coverage(img, boxes)
                if coverage < cfg["min_inked_region_coverage"]:
                    need_page_crop = True
            if not need_page_crop:
                continue
            if page_no not in cache:
                raster, info = rasterize_page(pdf_path, page_no)
                deskewed, skew = deskew(raster)
                cache[page_no] = {"image": deskewed, "info": info, "skew": skew}
            scale = SCAN_RASTER_DPI / 72.0
            ink_box = ink_bbox_pdf_points(cache[page_no]["image"], scale)
            try:
                rec = process_block(pdf_path, page_no, ink_box, cfg, page_cache=cache)
            except ScanAgreementError as exc:
                rec = {"status": "held", "held_reason": f"scan agreement failed closed: {exc}",
                       "ocr": [], "cer": None, "crop_sha256": None, "crop_image": None,
                       "dpi": SCAN_RASTER_DPI, "skew": None}
            bid = f"#/pages/{page_no}/scan"
            blk = {
                "id": bid,
                "type": "scan_page",
                "pages": [page_no],
                "status": rec["status"],
                "text": "",
            }
            if rec.get("ocr"):
                name = f"pages-{page_no}-scan-crop.png"
                dest = out_dir / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                rec["crop_image"].save(dest)
                blk["ocr"] = rec["ocr"]
                blk["cer"] = rec["cer"]
                blk["crop_sha256"] = rec["crop_sha256"]
                blk["crop"] = str(Path("verified") / name)
                if rec.get("labels") is not None:
                    blk["labels"] = rec["labels"]
                blk["scan"] = {"dpi": rec.get("dpi"), "skew": rec.get("skew"),
                               "engines": [{"name": e["name"], "version": e["version"]} for e in rec["ocr"]]}
            if rec["status"] == "held":
                reasons = []
                if rec.get("held_reason"):
                    reasons.append(rec["held_reason"])
                blk["held_reason"] = reasons or ["scan agreement failed closed"]
            if rec.get("crop_image") is not None:
                del rec["crop_image"]
            view["blocks"].append(blk)

    apply_scan_decisions(view, conv_dir)


def apply_scan_review(conv_dir, block_id, decision="approve", reviewer=None, **kwargs):
    if not reviewer or not str(reviewer).strip():
        raise ValueError("a human reviewer field is required; the tool never auto-promotes")
    conv_dir = Path(conv_dir)
    view_path = next((conv_dir / "verified").glob("*.verified.json"), None)
    if view_path is None:
        raise FileNotFoundError("verified view not found; build it before recording a review")
    view = json.loads(view_path.read_text(encoding="utf-8"))
    block = next((b for b in view.get("blocks") or [] if b.get("id") == block_id), None)
    if block is None:
        raise KeyError(f"{block_id!r} is not a block")
    if block.get("status") not in {"scan_agreement", "held"}:
        raise PermissionError(f"{block_id!r} is {block.get('status')!r}, not a scan review target")
    if not block.get("ocr") or not block.get("crop_sha256"):
        raise PermissionError(f"{block_id!r} has no OCR/crop evidence to confirm")
    if decision != "approve":
        raise ValueError(f"unsupported decision {decision!r}")
    record = {
        "decision": "human_verified",
        "kind": "scan_agreement",
        "reviewer": str(reviewer).strip(),
        "reviewed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ocr_text_sha256": [e["text_sha256"] for e in block["ocr"]],
        "crop_sha256": block["crop_sha256"],
    }
    path = conv_dir / "decisions.json"
    decisions = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    decisions[block_id] = record
    path.write_text(json.dumps(decisions, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return record


def apply_scan_decisions(view, conv_dir) -> None:
    path = Path(conv_dir) / "decisions.json"
    if not path.exists():
        return
    decisions = json.loads(path.read_text(encoding="utf-8"))
    for b in view.get("blocks") or []:
        rec = decisions.get(b.get("id"))
        if not rec or rec.get("decision") != "human_verified" or rec.get("kind") != "scan_agreement":
            continue
        if not rec.get("reviewer"):
            continue
        if b.get("status") not in {"scan_agreement", "held"}:
            continue
        if rec.get("crop_sha256") and rec["crop_sha256"] != b.get("crop_sha256"):
            continue
        expected = rec.get("ocr_text_sha256") or []
        actual = [e.get("text_sha256") for e in (b.get("ocr") or [])]
        if expected and expected != actual:
            continue
        b["status"] = "human_verified"
        b["reviewer"] = rec["reviewer"]
        b["reviewed_at"] = rec.get("reviewed_at")
        b["evidence"] = {
            "ocr_text_sha256": expected or actual,
            "crop_sha256": rec.get("crop_sha256") or b.get("crop_sha256"),
        }
        b.pop("held_reason", None)


TO_SCAN_AGREEMENT_TILE = 52
TO_SCAN_AGREEMENT_NAME = "to_scan_agreement"
TO_SCAN_AGREEMENT_SIZE = 384
_SCAN_STATUS_CODES = {
    "verified": 1,
    "held": 2,
    "scan_agreement": 3,
    "human_verified": 4,
}
_TO_TARGET_TILES = frozenset({41, 42, 43, 44, 45, 46, 49, 50, 51})
_SCAN_FLAG_CER_PRESENT = 1


def _require_id16(value, field: str) -> bytes:
    if value is None or value == "":
        raise ScanAgreementPackError(f"missing {field}")
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
    else:
        text = str(value).replace("0x", "").replace("-", "")
        try:
            raw = bytes.fromhex(text)
        except ValueError as exc:
            raise ScanAgreementPackError(f"invalid {field}") from exc
    if len(raw) != 16 or raw == b"\x00" * 16:
        raise ScanAgreementPackError(f"missing or invalid {field}")
    return raw


def _require_sha32(value, field: str) -> bytes:
    if value is None or value == "":
        raise ScanAgreementPackError(f"missing {field}")
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
    else:
        try:
            raw = bytes.fromhex(str(value))
        except ValueError as exc:
            raise ScanAgreementPackError(f"invalid {field}") from exc
    if len(raw) != 32 or raw == b"\x00" * 32:
        raise ScanAgreementPackError(f"missing {field}")
    return raw


def _pad_utf8(value, size: int, field: str) -> bytes:
    if value is None or value == "":
        raise ScanAgreementPackError(f"missing {field}")
    raw = value.encode("utf-8") if isinstance(value, str) else bytes(value)
    if not raw:
        raise ScanAgreementPackError(f"missing {field}")
    if len(raw) > size:
        raise ScanAgreementPackError(f"{field} exceeds {size} bytes")
    return raw.ljust(size, b"\x00")


def pack_scan_agreement_record(rec) -> dict:
    """Encode a tile-52 to_scan_agreement payload. Fail closed on missing fields."""
    if not isinstance(rec, dict):
        raise ScanAgreementPackError("missing pack record")
    status_name = rec.get("status")
    if status_name not in _SCAN_STATUS_CODES:
        raise ScanAgreementPackError("missing or unknown status")
    status_code = _SCAN_STATUS_CODES[status_name]
    if "max_character_error_rate" not in rec or rec.get("max_character_error_rate") is None:
        raise ScanAgreementPackError("missing required max_character_error_rate threshold")
    try:
        threshold = float(rec["max_character_error_rate"])
    except (TypeError, ValueError) as exc:
        raise ScanAgreementPackError("invalid max_character_error_rate threshold") from exc
    if not math.isfinite(threshold) or not 0.0 <= threshold <= 1.0:
        raise ScanAgreementPackError("invalid max_character_error_rate threshold")

    ocr = rec.get("ocr") or []
    if len(ocr) != 2:
        raise ScanAgreementPackError("both OCR engine readings are required")
    names_versions = []
    hashes = []
    for i, engine in enumerate(ocr, start=1):
        if not isinstance(engine, dict):
            raise ScanAgreementPackError("invalid OCR engine record")
        name = engine.get("name")
        version = engine.get("version")
        if not name or not version:
            raise ScanAgreementPackError("missing OCR engine name or version")
        names_versions.append((str(name), str(version)))
        hashes.append(_require_sha32(engine.get("text_sha256"), f"ocr text sha256 {i}"))
    crop = _require_sha32(rec.get("crop_sha256"), "crop sha256")
    source_id = _require_id16(rec.get("source_id"), "source_id")
    origin_ptr = _require_id16(rec.get("origin_ptr"), "origin_ptr")
    doc_id = _require_id16(rec.get("doc_id"), "doc_id")
    target_source_id = _require_id16(rec.get("target_source_id"), "target_source_id")
    try:
        target_tile_type = int(rec.get("target_tile_type"))
    except (TypeError, ValueError) as exc:
        raise ScanAgreementPackError("missing or invalid target_tile_type") from exc
    if target_tile_type not in _TO_TARGET_TILES:
        raise ScanAgreementPackError(f"target_tile_type {target_tile_type} is not a pack-to section or cell tile")

    cer = rec.get("cer")
    if cer is None:
        if status_code == 3:
            raise ScanAgreementPackError("scan_agreement requires a CER")
        flags = 0
        cer_bits = b"\x00" * 8
    else:
        try:
            cer_value = float(cer)
        except (TypeError, ValueError) as exc:
            raise ScanAgreementPackError("invalid CER") from exc
        if not math.isfinite(cer_value):
            raise ScanAgreementPackError("invalid CER")
        if status_code == 3 and cer_value > threshold:
            raise ScanAgreementPackError("CER exceeds max_character_error_rate threshold")
        flags = _SCAN_FLAG_CER_PRESENT
        cer_bits = struct.pack("<d", cer_value)

    reviewer = rec.get("reviewer")
    reviewed_at = rec.get("reviewed_at")
    if status_code == 4:
        if not reviewer:
            raise ScanAgreementPackError("human_verified requires a reviewer")
        if reviewed_at is None or reviewed_at == "":
            raise ScanAgreementPackError("human_verified requires a timestamp")
        try:
            reviewed_at_v = int(reviewed_at)
        except (TypeError, ValueError) as exc:
            raise ScanAgreementPackError("human_verified requires a timestamp") from exc
        if reviewed_at_v == 0:
            raise ScanAgreementPackError("human_verified requires a timestamp")
        reviewer_f = _pad_utf8(reviewer, 32, "reviewer")
    else:
        if reviewer not in (None, "", b"") or reviewed_at not in (None, "", 0):
            raise ScanAgreementPackError("reviewer evidence is only valid on human_verified")
        reviewed_at_v = 0
        reviewer_f = b"\x00" * 32

    (a_name, a_ver), (b_name, b_ver) = names_versions
    content = (
        a_name.encode("utf-8")
        + b"\x1f"
        + a_ver.encode("utf-8")
        + b"\x1f"
        + b_name.encode("utf-8")
        + b"\x1f"
        + b_ver.encode("utf-8")
        + b"\x1f"
        + hashes[0]
        + hashes[1]
        + crop
    )
    payload = (
        source_id
        + bytes.fromhex(_sha(content))
        + origin_ptr
        + doc_id
        + target_source_id
        + int(target_tile_type).to_bytes(4, "little")
        + bytes([status_code, flags, 0, 0])
        + _pad_utf8(a_name, 32, "engine A name")
        + _pad_utf8(a_ver, 16, "engine A version")
        + _pad_utf8(b_name, 32, "engine B name")
        + _pad_utf8(b_ver, 16, "engine B version")
        + hashes[0]
        + hashes[1]
        + crop
        + cer_bits
        + struct.pack("<d", threshold)
        + int(reviewed_at_v).to_bytes(8, "little")
        + reviewer_f
        + b"\x00" * 32
    )
    if len(payload) != TO_SCAN_AGREEMENT_SIZE:
        raise ScanAgreementPackError(
            f"internal pack size {len(payload)} != {TO_SCAN_AGREEMENT_SIZE}"
        )
    return {
        "tile_type": TO_SCAN_AGREEMENT_TILE,
        "tile_name": TO_SCAN_AGREEMENT_NAME,
        "record_size": TO_SCAN_AGREEMENT_SIZE,
        "status": status_name,
        "status_code": status_code,
        "payload": payload,
        "engines": [
            {"name": a_name, "version": a_ver},
            {"name": b_name, "version": b_ver},
        ],
    }


def refresh_counts(view) -> None:
    blocks = view.get("blocks") or []
    counts = view.setdefault("counts", {})
    counts["verified"] = sum(b.get("status") == "verified" for b in blocks)
    counts["held"] = sum(b.get("status") == "held" for b in blocks)
    counts["scan_agreement"] = sum(b.get("status") == "scan_agreement" for b in blocks)
    counts["needs_confirmation"] = sum(bool(b.get("needs_confirmation")) for b in blocks)
