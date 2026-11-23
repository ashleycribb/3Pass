"""Fail-first acceptance tests for the scanned-page full-page / ink-bbox fallback.

Do NOT edit these tests to make them pass.

When a page is triaged `scanned` (image-only) and the converter's detected
regions cover too little of the page's inked area, the scan tier must add a
full-page or ink-bbox crop block and run the same dual-OCR CER check on it.
The coverage threshold is the required config key `min_inked_region_coverage`
(a number in [0, 1]). There is no silent default; a missing key fails loudly.

Fixtures are invented and generated at test time. No real manuals are used.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.verified_pdf import agent_tool  # noqa: E402
from scripts.verified_pdf import verify_conversion as vc  # noqa: E402
from scripts.verified_pdf import scan_agreement as sa  # noqa: E402
from tests.fixtures.verified_pdf.make_scan_agreement_pdf import (  # noqa: E402
    FALLBACK_TEXT,
    build_covered_scanned,
    build_undercovered_broken,
    build_undercovered_scanned,
    write_scan_conversion,
)

# Both keys are required. 0.5 is a general half-the-ink threshold, not tuned
# to any real manual. The under-covered fixture is far below it; the covered
# fixture is far above it.
FALLBACK_CONFIG = {
    "max_character_error_rate": 0.15,
    "min_inked_region_coverage": 0.5,
}


def _sha(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


def _view(pdf, conv, config=FALLBACK_CONFIG):
    return agent_tool.build_verified_view(pdf, conv, scan_agreement_config=config)


def _page_scan_blocks(view):
    return [
        b for b in view["blocks"]
        if b.get("type") == "scan_page" or (b.get("id") or "").startswith("#/pages/")
    ]


def _id16(n: int) -> str:
    return f"{n:02x}" + ("11" * 15)


# ---------------------------------------------------------------- config


def test_coverage_threshold_is_required_and_has_no_silent_default():
    with pytest.raises((ValueError, sa.ScanAgreementError, KeyError, TypeError)):
        sa.require_config(None)
    with pytest.raises((ValueError, sa.ScanAgreementError, KeyError, TypeError)):
        sa.require_config({})
    # A CER threshold alone is not enough; the coverage key is also required.
    with pytest.raises((ValueError, sa.ScanAgreementError, KeyError, TypeError)):
        sa.require_config({"max_character_error_rate": 0.15})
    with pytest.raises((ValueError, sa.ScanAgreementError, KeyError, TypeError)):
        sa.require_config({"min_inked_region_coverage": 0.5})
    with pytest.raises((ValueError, sa.ScanAgreementError, KeyError, TypeError)):
        sa.require_config({
            "max_character_error_rate": 0.15,
            "min_inked_region_coverage": "auto",
        })
    with pytest.raises((ValueError, sa.ScanAgreementError, KeyError, TypeError)):
        sa.require_config({
            "max_character_error_rate": 0.15,
            "min_inked_region_coverage": -0.1,
        })
    with pytest.raises((ValueError, sa.ScanAgreementError, KeyError, TypeError)):
        sa.require_config({
            "max_character_error_rate": 0.15,
            "min_inked_region_coverage": 1.5,
        })
    with pytest.raises((ValueError, sa.ScanAgreementError, KeyError, TypeError)):
        sa.require_config({
            "max_character_error_rate": 0.15,
            "min_inked_region_coverage": True,
        })
    cfg = sa.require_config(FALLBACK_CONFIG)
    assert cfg["min_inked_region_coverage"] == 0.5
    assert cfg["max_character_error_rate"] == 0.15
    assert not hasattr(sa, "DEFAULT_MIN_INKED_REGION_COVERAGE") or sa.DEFAULT_MIN_INKED_REGION_COVERAGE is None


def test_missing_coverage_key_on_scanned_page_fails_loudly(tmp_path):
    meta = build_undercovered_scanned(tmp_path / "uc")
    conv = write_scan_conversion(tmp_path / "uc_conv", meta, "undercovered_scanned")
    assert vc.triage_page(meta["pdf"], 1)["class"] == "scanned"
    # CER key present, coverage key absent: do not invent a threshold and do
    # not silently skip the fallback.
    view = agent_tool.build_verified_view(
        meta["pdf"], conv, scan_agreement_config={"max_character_error_rate": 0.15}
    )
    # Fail closed: no invented fallback agreement, and the missing key is named.
    reasons = []
    for b in view["blocks"]:
        assert b["status"] != "verified" or 1 not in (b.get("pages") or [])
        r = b.get("held_reason") or []
        reasons.extend(r if isinstance(r, list) else [str(r)])
    blob = " ".join(reasons).lower()
    assert "min_inked_region_coverage" in blob
    # The body token must not show up as a silently agreed reading of a
    # fallback block when the required coverage key was missing.
    for b in _page_scan_blocks(view):
        assert b["status"] != "scan_agreement"
        assert b["status"] != "verified"


# ---------------------------------------------------------------- fallback behavior


def test_undercovered_scanned_page_adds_full_page_or_ink_bbox_block(tmp_path):
    meta = build_undercovered_scanned(tmp_path / "uc")
    conv = write_scan_conversion(tmp_path / "uc_conv", meta, "undercovered_scanned")
    assert vc.triage_page(meta["pdf"], 1)["class"] == "scanned"
    view = _view(meta["pdf"], conv)

    fallbacks = _page_scan_blocks(view)
    assert fallbacks, (
        "a scanned page whose detected regions cover too little ink must gain "
        "a full-page or ink-bbox scan_page block"
    )
    assert len(fallbacks) == 1
    fb = fallbacks[0]
    assert fb["type"] == "scan_page"
    assert 1 in (fb.get("pages") or [])
    assert fb["status"] in {"scan_agreement", "held"}
    assert fb["status"] != "verified"
    assert "ocr" in fb
    assert {e["name"] for e in fb["ocr"]} == {"tesseract", "rapidocr"}
    for e in fb["ocr"]:
        assert e["version"]
        assert e["text_sha256"] == _sha(e["text"])
    joined = " ".join(e["text"] for e in fb["ocr"]).upper()
    # The fallback crop must include the body ink the tiny region missed.
    assert "BRAVO" in joined or "5519" in joined
    assert fb.get("crop_sha256")
    crop_path = fb.get("crop") or fb.get("crop_png")
    assert crop_path
    crop_file = (conv / crop_path) if (conv / crop_path).is_file() else (conv / "verified" / Path(crop_path).name)
    assert crop_file.is_file()
    # The fallback crop is substantially larger than the tiny corner region
    # (24x24 pt at 300 DPI is on the order of 100 px, not a full text block).
    from PIL import Image
    w, h = Image.open(crop_file).size
    assert w * h > 20_000

    tiny = [b for b in view["blocks"] if b.get("id") == "#/pictures/0"]
    assert tiny, "the original tiny region block is still present"
    tiny_crop = tiny[0].get("crop")
    if tiny_crop:
        tfile = (conv / tiny_crop) if (conv / tiny_crop).is_file() else (conv / "verified" / Path(tiny_crop).name)
        if tfile.is_file():
            tw, th = Image.open(tfile).size
            assert w * h > tw * th

    if fb["status"] == "scan_agreement":
        assert fb["cer"] is not None
        assert fb["cer"] <= FALLBACK_CONFIG["max_character_error_rate"]
        assert fb.get("held_reason") in (None, [], "")
    else:
        assert fb["status"] == "held"
        reasons = fb.get("held_reason") or []
        assert reasons, "held fallback must name a reason; nothing is auto-promoted"


def test_covered_scanned_page_does_not_add_a_fallback_block(tmp_path):
    meta = build_covered_scanned(tmp_path / "ok")
    conv = write_scan_conversion(tmp_path / "ok_conv", meta, "covered_scanned")
    assert vc.triage_page(meta["pdf"], 1)["class"] == "scanned"
    view = _view(meta["pdf"], conv)
    # Detected region already covers the body ink; do not add a second page crop.
    assert _page_scan_blocks(view) == []
    pics = [b for b in view["blocks"] if b.get("id") == "#/pictures/0"]
    assert pics
    assert pics[0]["status"] in {"scan_agreement", "held"}
    assert pics[0]["status"] != "verified"


def test_undercovered_broken_text_layer_does_not_get_scanned_fallback(tmp_path):
    meta = build_undercovered_broken(tmp_path / "br")
    conv = write_scan_conversion(tmp_path / "br_conv", meta, "undercovered_broken")
    assert vc.triage_page(meta["pdf"], 1)["class"] == "broken_text_layer"
    view = _view(meta["pdf"], conv)
    # Fallback is for triage class `scanned` only.
    assert _page_scan_blocks(view) == []


def test_fallback_block_appears_in_md_html_review_output(tmp_path):
    meta = build_undercovered_scanned(tmp_path / "uc")
    conv = write_scan_conversion(tmp_path / "uc_conv", meta, "undercovered_scanned")
    view = _view(meta["pdf"], conv)
    fb = _page_scan_blocks(view)[0]
    stem = "undercovered_scanned"
    md = (conv / "verified" / f"{stem}.verified.md").read_text(encoding="utf-8")
    htm = (conv / "verified" / f"{stem}.verified.html").read_text(encoding="utf-8")
    if fb["status"] == "scan_agreement":
        assert "scan_agreement" in md.lower()
        assert "scan_agreement" in htm.lower()
    else:
        assert "held (scan)" in md.lower() or "held" in md.lower()
        assert "held" in htm.lower()
    assert "tesseract" in md.lower() and "rapidocr" in md.lower()
    assert "tesseract" in htm.lower() and "rapidocr" in htm.lower()
    for e in fb["ocr"]:
        if e["text"]:
            assert e["text"] in md
            assert e["text"] in htm
    assert "crop" in md.lower() or ".png" in md
    assert "<img" in htm.lower() and ".png" in htm
    # Nothing in the review output is labelled verified for this scanned page.
    for b in view["blocks"]:
        if 1 in (b.get("pages") or []):
            assert b["status"] != "verified"


def test_fallback_block_packs_as_tile_52_and_never_as_verified(tmp_path):
    meta = build_undercovered_scanned(tmp_path / "uc")
    conv = write_scan_conversion(tmp_path / "uc_conv", meta, "undercovered_scanned")
    view = _view(meta["pdf"], conv)
    fb = _page_scan_blocks(view)[0]
    rec = {
        "status": fb["status"],
        "source_id": _id16(0x30),
        "origin_ptr": "fe" * 16,
        "doc_id": "39" * 16,
        "target_source_id": _id16(0x01),
        "target_tile_type": 41,
        "max_character_error_rate": FALLBACK_CONFIG["max_character_error_rate"],
        "ocr": fb["ocr"],
        "crop_sha256": fb["crop_sha256"],
        "cer": fb.get("cer"),
    }
    out = sa.pack_scan_agreement_record(rec)
    assert out["tile_type"] == 52
    assert out["tile_name"] == "to_scan_agreement"
    assert out["record_size"] == 384
    assert len(out["payload"]) == 384
    assert out["status"] == fb["status"]
    assert out["status"] != "verified"
    assert out["status_code"] != 1
    if fb["status"] == "scan_agreement":
        assert out["status_code"] == 3
        assert out["payload"][100] == 3
    elif fb["status"] == "held":
        assert out["status_code"] == 2
        assert out["payload"][100] == 2
    assert out["payload"][100] != 1
    assert out["payload"][104:136].rstrip(b"\x00") == b"tesseract"
    assert out["payload"][152:184].rstrip(b"\x00") == b"rapidocr"


def test_contract_documents_the_coverage_threshold_and_fallback():
    contract = (ROOT / "docs" / "verified_pdf" / "AGENT_CONTRACT.md").read_text(encoding="utf-8")
    lower = contract.lower()
    assert "min_inked_region_coverage" in contract
    assert "fallback" in lower or "full-page" in lower or "ink-bbox" in lower or "inked" in lower
    assert "no silent default" in lower or "no default" in lower
    assert "never" in lower and "citable" in lower
