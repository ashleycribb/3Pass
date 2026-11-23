"""Scanned / broken-text-layer page agreement: rasterize, deskew, dual OCR.

Pages that triage marks `scanned` or `broken_text_layer` are rasterized at
300 DPI (SCAN_RASTER_DPI), deskewed, and read by two independent OCR
engines (Tesseract and RapidOCR). Per-block character error rate (CER) is

    levenshtein(norm(a), norm(b)) / max(len(norm(a)), norm(b)))

with NFKC + whitespace collapse. A block becomes `scan_agreement` only when
both readings are non-empty and CER is at or below the caller-supplied
`max_character_error_rate`. That threshold is required configuration: there
is no silent default and no DEFAULT_MAX_CER.

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
    """Return a validated config. Missing or invalid threshold fails closed."""
    if not isinstance(config, dict):
        raise ScanAgreementError("scan_agreement_config is required and must be a mapping")
    if "max_character_error_rate" not in config:
        raise ScanAgreementError("scan_agreement_config.max_character_error_rate is required; there is no default")
    try:
        thr = float(config["max_character_error_rate"])
    except (TypeError, ValueError) as exc:
        raise ScanAgreementError("max_character_error_rate must be a number in [0, 1]") from exc
    if not (0.0 <= thr <= 1.0) or isinstance(config["max_character_error_rate"], bool):
        raise ScanAgreementError("max_character_error_rate must be a number in [0, 1]")
    return {"max_character_error_rate": thr}


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
        crop = img
    crop_bytes = _crop_to_bytes(crop)
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
    covered = {p for b in view.get("blocks") or [] for p in (b.get("pages") or []) if b.get("ocr") or (b.get("held_reason") and "scan agreement" in " ".join(b.get("held_reason") or []))}
    if cfg is not None:
        for page_no in sorted(scan_pages):
            if page_no in covered:
                continue
            try:
                rec = process_block(pdf_path, page_no, None, cfg, page_cache=cache)
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
