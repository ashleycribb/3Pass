"""Label-by-label scan agreement (comparison: "labels") for drawings and floor plans: the two OCR
engines' lines are matched by position and each label is compared on its own, so reading order
no longer decides agreement. Every label must be read by both engines and agree; anything else
keeps the block held. The original whole-text rule is unchanged and stays the default."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.verified_pdf import scan_agreement as sa  # noqa: E402

CFG = {"max_character_error_rate": 0.1, "min_inked_region_coverage": 0.5, "comparison": "labels"}  # test values


def eng(name, lines):
    return {"name": name, "version": "t", "config": "", "lines": lines}


def pts(box):
    return {"l": box[0], "t": box[1], "r": box[2], "b": box[3], "coord_origin": "TOPLEFT"}


def run(a, b, cfg=CFG):
    return sa.classify_labels(engine_a=eng("tesseract", a), engine_b=eng("rapidocr", b),
                              crop_bytes=b"x", config=cfg, to_points=pts)


ROOM = (100, 100, 200, 110, "MECHANICAL ROOM")
NUM = (500, 50, 540, 60, "1002")
AREA = (500, 62, 540, 72, "229sf")


def test_same_labels_in_different_order_agree():
    rec = run([ROOM, NUM, AREA], [AREA, NUM, ROOM])
    assert rec["status"] == "scan_agreement" and rec["cer"] == 0.0
    assert [x["text_a"] for x in rec["labels"]] == ["1002", "229sf", "MECHANICAL ROOM"]
    # whole-text rule on the same readings in engine order does not agree
    assert sa.character_error_rate("MECHANICAL ROOM 1002 229sf", "229sf 1002 MECHANICAL ROOM") > 0.1


def test_line_split_differently_is_one_label():
    merged = (500, 50, 540, 72, "1002 229sf")
    rec = run([merged], [NUM, AREA])
    assert rec["status"] == "scan_agreement" and len(rec["labels"]) == 1


def test_label_read_by_one_engine_holds_block():
    rec = run([ROOM, NUM], [ROOM])
    assert rec["status"] == "held" and rec["cer"] is None
    one = [x for x in rec["labels"] if x["status"] == "held"]
    assert len(one) == 1 and one[0]["held_reason"] == "read by tesseract only"
    assert "1 of 2 labels not agreed" in rec["held_reason"]


def test_misread_label_holds_block_and_keeps_good_labels():
    rec = run([ROOM, (500, 50, 540, 60, "1004")], [ROOM, (500, 50, 540, 60, "1001")])
    assert rec["status"] == "held" and rec["cer"] == 0.25
    assert {x["text_a"]: x["status"] for x in rec["labels"]} == {"1004": "held", "MECHANICAL ROOM": "scan_agreement"}


def test_nothing_read_is_held():
    assert run([], [])["status"] == "held"


def test_label_bbox_in_points_and_ocr_hashes():
    rec = run([NUM], [NUM])
    assert rec["labels"][0]["bbox"] == pts((500, 50, 540, 60))
    assert all(o["text_sha256"] == sa._sha(o["text"]) for o in rec["ocr"])


def test_comparison_validated_and_text_is_default():
    base = {"max_character_error_rate": 0.1, "min_inked_region_coverage": 0.5}
    assert sa.require_config(base)["comparison"] == "text"
    assert sa.require_config({**base, "comparison": "labels"})["comparison"] == "labels"
    with pytest.raises(sa.ScanAgreementError):
        sa.require_config({**base, "comparison": "words"})


def test_thresholds_still_required_in_labels_mode():
    with pytest.raises(sa.ScanAgreementError):
        sa.require_config({"comparison": "labels", "min_inked_region_coverage": 0.5})
