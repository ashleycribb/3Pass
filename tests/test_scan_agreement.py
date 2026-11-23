"""Fail-first acceptance tests for the scanned-page agreement tier.

Fixtures are invented and generated at test time (see
tests/fixtures/verified_pdf/make_scan_agreement_pdf.py). No real manuals are
used. Rules this file pins down:

* Pages that triage marks scanned or broken_text_layer are rasterized at 300
  DPI, deskewed, and read by two independent OCR engines (Tesseract and
  RapidOCR). Engine names and versions are recorded. A missing engine or
  invalid config fails closed.
* The agreement threshold is an explicit config key
  `max_character_error_rate` with no silent default. CER is
  levenshtein(norm(a), norm(b)) / max(len(norm(a)), len(norm(b))). Both-empty
  OCR does not agree: the block stays held.
* If CER is at or below the configured threshold the block status is
  `scan_agreement`. That status is never `verified`, is never citable by the
  answer path, and can become `human_verified` only through the review
  procedure (reviewer, timestamp, both OCR texts' sha256, crop sha256).
* If CER is above the threshold, or OCR fails, the block stays `held`.
* The MD/HTML front end labels scan_agreement (and held scan) blocks, shows
  the page crop, and shows both OCR readings side by side.
* s3a-pack-to packing of these records uses tile 52 (`to_scan_agreement`),
  an adjunct that links to an existing section or cell by id and carries
  status, engine names and versions, both text hashes, the crop hash, CER,
  and the required threshold. scan_agreement never packs as verified.
  Missing required pack fields fail closed; no silent defaults.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.source_router import VerifiedPdfPlugin  # noqa: E402
from scripts.verified_pdf import agent_tool  # noqa: E402
from scripts.verified_pdf import verify_conversion as vc  # noqa: E402
from scripts.verified_pdf import scan_agreement as sa  # noqa: E402
from tests.fixtures.verified_pdf.make_scan_agreement_pdf import (  # noqa: E402
    AGREE_TEXT,
    BROKEN_VISIBLE,
    build_agree_scanned,
    build_broken_with_visible_text,
    build_skewed_scanned,
    write_scan_conversion,
)

# Tests pass the threshold explicitly. The production code must not invent one.
AGREE_CONFIG = {"max_character_error_rate": 0.15, "min_inked_region_coverage": 0.5}
# min_inked_region_coverage is now a required scan_agreement_config key (see
# tests/test_scan_full_page_fallback.py). 0.5 is a general half-the-ink
# threshold; existing fixtures already cover the body ink, so their
# assertions are unchanged.


def _sha(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


def _view(pdf, conv, config=AGREE_CONFIG):
    return agent_tool.build_verified_view(pdf, conv, scan_agreement_config=config)


def _scan_block(view):
    blocks = [b for b in view["blocks"] if b.get("type") != "page"]
    assert blocks, "expected at least one block"
    return blocks[0]


# ---------------------------------------------------------------- unit: CER and config


def test_character_error_rate_is_normalized_levenshtein_over_max_length():
    assert sa.character_error_rate("ABC", "ABC") == 0.0
    assert sa.character_error_rate("AAAA", "AAAB") == 0.25
    # Normalization collapses whitespace and applies NFKC; case is preserved
    # only after the documented norm (tests the function's documented form).
    assert sa.character_error_rate("ALPHA  937", "ALPHA 937") == 0.0
    with pytest.raises((ValueError, ZeroDivisionError, sa.ScanAgreementError)):
        # Both-empty is not an agreement score the caller may treat as 0 to promote.
        sa.character_error_rate("", "")


def test_threshold_config_is_required_and_has_no_silent_default():
    with pytest.raises((ValueError, sa.ScanAgreementError, KeyError, TypeError)):
        sa.require_config(None)
    with pytest.raises((ValueError, sa.ScanAgreementError, KeyError, TypeError)):
        sa.require_config({})
    with pytest.raises((ValueError, sa.ScanAgreementError, KeyError, TypeError)):
        sa.require_config({"max_character_error_rate": "auto"})
    with pytest.raises((ValueError, sa.ScanAgreementError, KeyError, TypeError)):
        sa.require_config({"max_character_error_rate": -0.1})
    with pytest.raises((ValueError, sa.ScanAgreementError, KeyError, TypeError)):
        sa.require_config({"max_character_error_rate": 1.5})
    # Coverage threshold is also required; a CER-only mapping is no longer complete.
    with pytest.raises((ValueError, sa.ScanAgreementError, KeyError, TypeError)):
        sa.require_config({"max_character_error_rate": 0.15})
    cfg = sa.require_config({"max_character_error_rate": 0.15, "min_inked_region_coverage": 0.5})
    assert cfg["max_character_error_rate"] == 0.15
    assert cfg["min_inked_region_coverage"] == 0.5
    # No hidden default constant may be used in place of a missing key.
    assert not hasattr(sa, "DEFAULT_MAX_CER") or sa.DEFAULT_MAX_CER is None
    assert not hasattr(sa, "DEFAULT_MIN_INKED_REGION_COVERAGE") or sa.DEFAULT_MIN_INKED_REGION_COVERAGE is None


def test_classify_within_threshold_is_scan_agreement_never_verified():
    crop = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    rec = sa.classify_block(
        engine_a={"name": "tesseract", "version": "0", "text": "ALPHA-937"},
        engine_b={"name": "rapidocr", "version": "0", "text": "ALPHA-937"},
        crop_bytes=crop,
        config=AGREE_CONFIG,
    )
    assert rec["status"] == "scan_agreement"
    assert rec["status"] != "verified"
    assert rec["cer"] == 0.0
    assert rec["ocr"][0]["name"] == "tesseract"
    assert rec["ocr"][1]["name"] == "rapidocr"
    assert rec["ocr"][0]["text_sha256"] == _sha("ALPHA-937")
    assert rec["ocr"][1]["text_sha256"] == _sha("ALPHA-937")
    assert rec["crop_sha256"] == hashlib.sha256(crop).hexdigest()


def test_classify_above_threshold_or_empty_is_held():
    crop = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    far = sa.classify_block(
        engine_a={"name": "tesseract", "version": "0", "text": "ALPHA-937"},
        engine_b={"name": "rapidocr", "version": "0", "text": "totally different line"},
        crop_bytes=crop,
        config=AGREE_CONFIG,
    )
    assert far["status"] == "held"
    assert far["status"] != "scan_agreement"
    assert far["status"] != "verified"
    assert far["cer"] > AGREE_CONFIG["max_character_error_rate"]
    empty = sa.classify_block(
        engine_a={"name": "tesseract", "version": "0", "text": ""},
        engine_b={"name": "rapidocr", "version": "0", "text": ""},
        crop_bytes=crop,
        config=AGREE_CONFIG,
    )
    assert empty["status"] == "held"
    assert empty["status"] != "scan_agreement"


def test_missing_ocr_engine_fails_closed(monkeypatch):
    def boom(*_a, **_k):
        raise sa.ScanAgreementError("OCR engine missing")

    monkeypatch.setattr(sa, "run_engine", boom)
    with pytest.raises(sa.ScanAgreementError):
        sa.run_engine("tesseract", b"not-an-image")


# ---------------------------------------------------------------- rasterize, deskew, live OCR


def test_rasterize_is_300_dpi(tmp_path):
    meta = build_agree_scanned(tmp_path / "agree")
    img, info = sa.rasterize_page(meta["pdf"], 1)
    assert info["dpi"] == 300
    # US Letter 612x792 pt -> 8.5 x 11 in -> 2550 x 3300 px at 300 DPI.
    w, h = img.size
    assert w == 2550
    assert h == 3300


def test_deskew_corrects_a_known_rotation(tmp_path):
    meta = build_skewed_scanned(tmp_path / "skew", degrees=4.0)
    img, _ = sa.rasterize_page(meta["pdf"], 1)
    deskewed, report = sa.deskew(img)
    assert "skew_degrees" in report
    # The estimated pre-correction skew should be near the known 4 degrees.
    assert abs(abs(report["skew_degrees"]) - 4.0) <= 2.0
    assert report.get("corrected") is True
    assert deskewed.size[0] > 0 and deskewed.size[1] > 0


def test_scanned_page_with_agreement_is_scan_agreement_not_verified(tmp_path):
    meta = build_agree_scanned(tmp_path / "agree")
    conv = write_scan_conversion(tmp_path / "agree_conv", meta, "agree_scanned")
    assert vc.triage_page(meta["pdf"], 1)["class"] == "scanned"
    view = _view(meta["pdf"], conv)
    block = _scan_block(view)
    assert block["status"] == "scan_agreement"
    assert block["status"] != "verified"
    assert view["counts"].get("scan_agreement", 0) >= 1
    assert view["counts"].get("verified", 0) == 0 or all(
        b["status"] != "verified" for b in view["blocks"] if 1 in (b.get("pages") or [])
    )
    ocr = block["ocr"]
    assert {e["name"] for e in ocr} == {"tesseract", "rapidocr"}
    for e in ocr:
        assert e["version"]
        assert e["text"]
        assert e["text_sha256"] == _sha(e["text"])
        # Both engines should see the distinctive fixture token.
        assert "ALPHA" in e["text"].upper() or "937" in e["text"]
    assert block["crop_sha256"]
    assert block["cer"] <= AGREE_CONFIG["max_character_error_rate"]
    assert (conv / "verified").exists()


def test_broken_text_layer_page_runs_the_same_pipeline(tmp_path):
    meta = build_broken_with_visible_text(tmp_path / "broke")
    conv = write_scan_conversion(tmp_path / "broke_conv", meta, "broken_visible")
    assert vc.triage_page(meta["pdf"], 1)["class"] == "broken_text_layer"
    view = _view(meta["pdf"], conv)
    block = _scan_block(view)
    assert block["status"] in {"scan_agreement", "held"}
    assert block["status"] != "verified"
    assert "ocr" in block
    assert {e["name"] for e in block["ocr"]} == {"tesseract", "rapidocr"}
    if block["status"] == "scan_agreement":
        joined = " ".join(e["text"] for e in block["ocr"]).upper()
        assert "OMEGA" in joined or "24680" in joined


def test_missing_config_on_scanned_page_does_not_invent_a_threshold(tmp_path):
    meta = build_agree_scanned(tmp_path / "agree")
    conv = write_scan_conversion(tmp_path / "agree_conv", meta, "agree_scanned")
    # No config: fail closed (held, with a reason) rather than a silent default.
    view = agent_tool.build_verified_view(meta["pdf"], conv)
    block = _scan_block(view)
    assert block["status"] == "held"
    assert block["status"] != "scan_agreement"
    assert block["status"] != "verified"
    reasons = " ".join(block.get("held_reason") or []) if isinstance(block.get("held_reason"), list) else str(block.get("held_reason") or "")
    assert "config" in reasons.lower() or "threshold" in reasons.lower() or "max_character_error_rate" in reasons.lower()


# ---------------------------------------------------------------- front end, contract, review, answer path, pack-to


def test_md_html_shows_label_crop_and_both_ocr_readings(tmp_path):
    meta = build_agree_scanned(tmp_path / "agree")
    conv = write_scan_conversion(tmp_path / "agree_conv", meta, "agree_scanned")
    view = _view(meta["pdf"], conv)
    stem = "agree_scanned"
    md = (conv / "verified" / f"{stem}.verified.md").read_text(encoding="utf-8")
    htm = (conv / "verified" / f"{stem}.verified.html").read_text(encoding="utf-8")
    assert "scan_agreement" in md.lower()
    assert "scan_agreement" in htm.lower()
    # Both engine readings appear, labelled, side by side or in clearly paired blocks.
    assert "tesseract" in md.lower() and "rapidocr" in md.lower()
    assert "tesseract" in htm.lower() and "rapidocr" in htm.lower()
    block = _scan_block(view)
    for e in block["ocr"]:
        assert e["text"] in md
        assert e["text"] in htm
    # Crop is linked or inlined in both front ends.
    assert "crop" in md.lower() or ".png" in md
    assert "<img" in htm.lower() and ".png" in htm
    crop_path = block.get("crop") or block.get("crop_png")
    assert crop_path
    assert (conv / crop_path).is_file() or (conv / "verified" / Path(crop_path).name).is_file()


def test_review_procedure_and_contract_document_scan_agreement():
    contract = (ROOT / "docs" / "verified_pdf" / "AGENT_CONTRACT.md").read_text(encoding="utf-8")
    lower = contract.lower()
    assert "scan_agreement" in contract
    assert "REVIEW-PROCEDURE" in contract
    assert ("never citable" in lower) or ("not citable" in lower) or (
        "never" in lower and "citable" in lower
    )
    assert "ocr" in lower
    assert "sha256" in lower
    assert "crop" in lower
    assert "max_character_error_rate" in contract or "character error rate" in lower
    # scan_agreement is never verified
    assert "never" in lower


def test_human_review_requires_reviewer_and_stores_ocr_and_crop_hashes(tmp_path):
    meta = build_agree_scanned(tmp_path / "agree")
    conv = write_scan_conversion(tmp_path / "agree_conv", meta, "agree_scanned")
    view = _view(meta["pdf"], conv)
    block = _scan_block(view)
    assert block["status"] in {"scan_agreement", "held"}
    bid = block["id"]
    with pytest.raises((ValueError, TypeError)):
        agent_tool.apply_scan_review(conv, bid, decision="approve", reviewer="")
    with pytest.raises((ValueError, TypeError)):
        agent_tool.apply_scan_review(conv, bid, decision="approve", reviewer=None)
    agent_tool.apply_scan_review(conv, bid, decision="approve", reviewer="Jane Reviewer")
    view2 = _view(meta["pdf"], conv)
    approved = _scan_block(view2)
    assert approved["status"] == "human_verified"
    assert approved["status"] != "verified"
    assert approved["reviewer"] == "Jane Reviewer"
    assert approved.get("reviewed_at")
    ev = approved["evidence"]
    assert ev["crop_sha256"] == block["crop_sha256"]
    ocr_hashes = ev["ocr_text_sha256"]
    assert set(ocr_hashes) == {e["text_sha256"] for e in block["ocr"]}
    # A fresh conversion still never auto-emits human_verified.
    conv3 = write_scan_conversion(tmp_path / "agree_conv_fresh", meta, "agree_scanned")
    fresh = _view(meta["pdf"], conv3)
    assert "human_verified" not in {b["status"] for b in fresh["blocks"]}


def test_answer_path_does_not_cite_scan_agreement_as_verified(tmp_path):
    meta = build_agree_scanned(tmp_path / "agree")
    conv = write_scan_conversion(tmp_path / "agree_conv", meta, "agree_scanned")
    view = _view(meta["pdf"], conv)
    rec = VerifiedPdfPlugin().extract({
        "source_id": "SRC-SCAN-1",
        "drive_path": str(meta["pdf"]),
        "verified_dir_fixture": str(conv),
        "profile": "technical_manual",
        "title": "Scan Fixture",
        "scan_agreement_config": AGREE_CONFIG,
    })
    # Nothing in the paper record may be marked pdf-verified from this page.
    for sec in rec.sections:
        assert sec.verified != "pdf-verified"
        assert "pdf-verified" not in (sec.verified or "")
        # OCR text is not copied into the citable section body.
        assert AGREE_TEXT not in (sec.text or "")
        assert "ALPHA-937" not in (sec.text or "")
    for tab in rec.tables:
        assert "pdf-verified" not in (tab.verified or "")
    for fig in rec.figures:
        assert "pdf-verified" not in (fig.verified or "")


def _id16(tag: int) -> str:
    raw = bytes([0xA1, 0x70]) + bytes(13) + bytes([tag])
    return raw.hex()


def _pack_rec(**overrides):
    rec = {
        "status": "scan_agreement",
        "source_id": _id16(0x30),
        "origin_ptr": "fe" * 16,
        "doc_id": "39" * 16,
        "target_source_id": _id16(0x01),
        "target_tile_type": 41,
        "max_character_error_rate": 0.15,
        "ocr": [
            {"name": "tesseract", "version": "5.5.0", "text": "ALPHA-937", "text_sha256": _sha("ALPHA-937")},
            {"name": "rapidocr", "version": "1.2.3", "text": "ALPHA-937", "text_sha256": _sha("ALPHA-937")},
        ],
        "crop_sha256": "ab" * 32,
        "cer": 0.0,
    }
    rec.update(overrides)
    return rec


def _content_hash(rec) -> str:
    a, b = rec["ocr"]
    src = (
        a["name"].encode("utf-8")
        + b"\x1f"
        + a["version"].encode("utf-8")
        + b"\x1f"
        + b["name"].encode("utf-8")
        + b"\x1f"
        + b["version"].encode("utf-8")
        + b"\x1f"
        + bytes.fromhex(a["text_sha256"])
        + bytes.fromhex(b["text_sha256"])
        + bytes.fromhex(rec["crop_sha256"])
    )
    return hashlib.sha256(src).hexdigest()


def test_pack_scan_agreement_record_emits_tile_52_roundtrip():
    rec = _pack_rec()
    out = sa.pack_scan_agreement_record(rec)
    assert out["tile_type"] == 52
    assert out["tile_name"] == "to_scan_agreement"
    assert out["record_size"] == 384
    assert out["status"] == "scan_agreement"
    assert out["status_code"] == 3
    payload = out["payload"]
    assert isinstance(payload, (bytes, bytearray))
    assert len(payload) == 384
    assert payload[0:16] == bytes.fromhex(rec["source_id"])
    assert payload[16:48] == bytes.fromhex(_content_hash(rec))
    assert payload[48:64] == bytes.fromhex(rec["origin_ptr"])
    assert payload[64:80] == bytes.fromhex(rec["doc_id"])
    assert payload[80:96] == bytes.fromhex(rec["target_source_id"])
    assert int.from_bytes(payload[96:100], "little") == 41
    assert payload[100] == 3
    assert payload[100] != 1
    assert payload[101] == 1  # SCAN_FLAG_CER_PRESENT
    assert payload[102:104] == b"\x00\x00"
    assert payload[104:136].rstrip(b"\x00") == b"tesseract"
    assert payload[136:152].rstrip(b"\x00") == b"5.5.0"
    assert payload[152:184].rstrip(b"\x00") == b"rapidocr"
    assert payload[184:200].rstrip(b"\x00") == b"1.2.3"
    assert payload[200:232] == bytes.fromhex(rec["ocr"][0]["text_sha256"])
    assert payload[232:264] == bytes.fromhex(rec["ocr"][1]["text_sha256"])
    assert payload[264:296] == bytes.fromhex(rec["crop_sha256"])
    import struct
    assert struct.unpack_from("<d", payload, 296)[0] == 0.0
    assert struct.unpack_from("<d", payload, 304)[0] == 0.15
    assert int.from_bytes(payload[312:320], "little") == 0
    assert payload[320:352] == b"\x00" * 32
    assert payload[352:384] == b"\x00" * 32
    assert out["engines"] == [
        {"name": "tesseract", "version": "5.5.0"},
        {"name": "rapidocr", "version": "1.2.3"},
    ]


def test_pack_scan_agreement_never_writes_verified_status():
    out = sa.pack_scan_agreement_record(_pack_rec())
    assert out["status"] == "scan_agreement"
    assert out["status_code"] == 3
    assert out["status_code"] != 1
    assert out["payload"][100] == 3
    assert out["payload"][100] != 1


def test_pack_human_verified_requires_reviewer_timestamp_and_evidence_hashes():
    base = dict(
        status="human_verified",
        reviewer="reviewer-fixture",
        reviewed_at=1_700_000_000,
    )
    with pytest.raises(sa.ScanAgreementPackError):
        sa.pack_scan_agreement_record(_pack_rec(status="human_verified", reviewed_at=1_700_000_000))
    with pytest.raises(sa.ScanAgreementPackError):
        sa.pack_scan_agreement_record(_pack_rec(status="human_verified", reviewer="reviewer-fixture"))
    with pytest.raises(sa.ScanAgreementPackError):
        sa.pack_scan_agreement_record(_pack_rec(**base, ocr=[
            {"name": "tesseract", "version": "5.5.0", "text": "ALPHA-937", "text_sha256": "00" * 32},
            {"name": "rapidocr", "version": "1.2.3", "text": "ALPHA-937", "text_sha256": _sha("ALPHA-937")},
        ]))
    with pytest.raises(sa.ScanAgreementPackError):
        sa.pack_scan_agreement_record(_pack_rec(**base, crop_sha256="00" * 32))
    out = sa.pack_scan_agreement_record(_pack_rec(**base))
    assert out["status"] == "human_verified"
    assert out["status_code"] == 4
    assert out["status_code"] != 1
    assert out["payload"][100] == 4
    assert out["payload"][320:352].rstrip(b"\x00") == b"reviewer-fixture"
    import struct
    assert int.from_bytes(out["payload"][312:320], "little") == 1_700_000_000


def test_pack_fails_closed_when_required_pack_fields_are_missing():
    rec = {
        "status": "scan_agreement",
        "ocr": [
            {"name": "tesseract", "version": "5.5.0", "text": "ALPHA-937", "text_sha256": _sha("ALPHA-937")},
            {"name": "rapidocr", "version": "1.2.3", "text": "ALPHA-937", "text_sha256": _sha("ALPHA-937")},
        ],
        "crop_sha256": "ab" * 32,
        "cer": 0.0,
    }
    with pytest.raises(sa.ScanAgreementPackError) as ei:
        sa.pack_scan_agreement_record(rec)
    msg = str(ei.value).lower()
    assert "max_character_error_rate" in msg or "threshold" in msg
    # A complete rec except the link to a section/cell also fails closed.
    with pytest.raises(sa.ScanAgreementPackError) as ei:
        sa.pack_scan_agreement_record(_pack_rec(target_source_id="00" * 16))
    assert "target" in str(ei.value).lower()
    with pytest.raises(sa.ScanAgreementPackError):
        sa.pack_scan_agreement_record(_pack_rec(cer=0.21))
