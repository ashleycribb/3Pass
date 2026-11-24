"""Intentionally blank pages (common in equipment manuals) are complete, not 'scanned'.
A page is blank only if it has no text and renders as a single colour; any visible mark keeps
it on the scanned path. Built from the committed fixture conversion (no Docling needed)."""
import json
import shutil
import sys
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.verified_pdf import agent_tool, verify_conversion as vc  # noqa: E402
from scripts.verified_pdf import technical_manual as tm  # noqa: E402

FX = ROOT / "tests" / "fixtures" / "verified_pdf"
STEM = "sample_paper"


@pytest.fixture
def case(tmp_path):
    """sample_paper.pdf + page 3 blank + page 4 white with one dark speck (an image-only page)."""
    speck = Image.new("RGB", (612, 792), "white")
    speck.putpixel((300, 400), (0, 0, 0))
    speck.save(tmp_path / "speck.pdf", resolution=72)
    doc = pdfium.PdfDocument(FX / f"{STEM}.pdf")
    w, h = doc[0].get_size()
    doc.new_page(w, h)
    doc.import_pages(pdfium.PdfDocument(tmp_path / "speck.pdf"))
    pdf = tmp_path / f"{STEM}.pdf"
    doc.save(pdf)
    conv = tmp_path / STEM
    shutil.copytree(FX / "converted" / STEM, conv)
    return pdf, conv


def test_blank_detection_is_strict(case):
    pdf, _ = case
    p = vc.Pdf(pdf)
    assert [p.is_blank(n) for n in (1, 2, 3, 4)] == [False, False, True, False]


def test_blank_page_is_not_flagged_but_a_speck_page_is(case):
    pdf, conv = case
    rep = vc.verify(pdf, conv)
    t1 = sorted(f["page"] for f in rep["flags"] if f["check"] == "T1")
    assert t1 == [4]
    assert rep["checks_passed"].get("T1-blank") == 1


def test_triage_and_view_mark_blank_page_complete(case):
    pdf, conv = case
    assert [tm.triage_page(pdf, n)["class"] for n in (3, 4)] == ["blank", "scanned"]
    view = agent_tool.build_verified_view(pdf, conv, profile="technical_manual")
    pages = {ps["page"]: ps for ps in view["pages"]}
    assert pages[3]["complete"] and pages[3]["text_layer"] and pages[3]["triage"]["class"] == "blank"
    assert not pages[4]["complete"] and pages[4]["triage"]["class"] == "scanned"
