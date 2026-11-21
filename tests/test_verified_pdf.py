"""
Tests for the verified PDF path (scripts/verified_pdf + VerifiedPdfPlugin).

The fixture PDF is synthetic (tests/fixtures/verified_pdf/make_sample_pdf.py). Its Docling
conversion is committed under tests/fixtures/verified_pdf/converted/ so that these tests run
without Docling; the verifier itself needs only pypdfium2 + Pillow. The end-to-end Docling test
runs only when Docling is installed (see requirements-verified-pdf.txt).
"""
import csv
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.models import PaperRecord  # noqa: E402
from scripts.source_router import VerifiedPdfPlugin, create_default_router, verified_pdf_available  # noqa: E402
from scripts.verified_pdf import agent_tool, verify_conversion as vc  # noqa: E402

FX = ROOT / "tests" / "fixtures" / "verified_pdf"
PDF = FX / "sample_paper.pdf"
CONVERTED = FX / "converted" / "sample_paper"
STEM = "sample_paper"


@pytest.fixture
def conv(tmp_path):
    """A writable copy of the committed conversion (the checks write review/ and verified/)."""
    d = tmp_path / STEM
    shutil.copytree(CONVERTED, d)
    return d


def _doc(conv):
    return json.loads((conv / f"{STEM}.docling.json").read_text(encoding="utf-8"))


def _save(conv, d):
    (conv / f"{STEM}.docling.json").write_text(json.dumps(d), encoding="utf-8")


def _checks(report):
    return {f["check"] for f in report["flags"]}


def _text_ref(d, needle):
    for i, t in enumerate(d["texts"]):
        if needle in (t.get("text") or ""):
            return i
    raise AssertionError(f"fixture text not found: {needle}")


# ---------------------------------------------------------------- verifier on the clean fixture

def test_fixture_pdf_is_reproducible(tmp_path):
    import hashlib, subprocess
    out = tmp_path / "regen"
    out.mkdir()
    src = (FX / "make_sample_pdf.py").read_text(encoding="utf-8").replace(
        'HERE = os.path.dirname(os.path.abspath(__file__))', f'HERE = {str(out)!r}')
    (tmp_path / "mk.py").write_text(src, encoding="utf-8")
    subprocess.run([sys.executable, str(tmp_path / "mk.py")], check=True, capture_output=True)
    assert hashlib.sha256((out / "sample_paper.pdf").read_bytes()).hexdigest() == \
        hashlib.sha256(PDF.read_bytes()).hexdigest()


def test_clean_conversion_has_no_flags(conv):
    report = vc.verify(PDF, conv)
    assert report["flags"] == [], report["flags"]


def test_verified_view_is_complete_and_exact(conv):
    view = agent_tool.build_verified_view(PDF, conv)
    assert view["document_status"] == "VERIFIED"
    assert view["counts"]["held"] == 0
    assert view["reading_order_verified"] is True
    types = [b["type"] for b in view["blocks"]]
    assert "table" in types and "figure" in types
    table = next(b for b in view["blocks"] if b["type"] == "table")
    assert table["rows"][1] == ["Screening", "412", "118", "28.6%"]
    assert table["caption"] == "Table 1: Invented counts per review stage."
    fig = next(b for b in view["blocks"] if b["type"] == "figure")
    assert fig["caption"].startswith("Figure 1:")
    assert (conv / fig["image"]).exists()
    # every verified text block is found in the PDF text layer
    for b in view["blocks"]:
        if b["type"] not in ("table", "figure") and b["status"] == "verified":
            assert agent_tool.check_quote(PDF, b["text"])["found"], b["text"]
    for ext in ("json", "md", "html"):
        assert (conv / "verified" / f"{STEM}.verified.{ext}").exists()


# ---------------------------------------------------------------- mutations must be caught

def test_changed_word_is_caught_and_held(conv):
    d = _doc(conv)
    i = _text_ref(d, "Doctoral students")
    d["texts"][i]["text"] = d["texts"][i]["text"].replace("exactly", "approximately")
    d["texts"][i]["orig"] = d["texts"][i]["text"]
    _save(conv, d)
    assert "T2" in _checks(vc.verify(PDF, conv))
    view = agent_tool.build_verified_view(PDF, conv)
    blk = next(b for b in view["blocks"] if b["id"] == f"#/texts/{i}")
    assert blk["status"] == "held" and "text" not in agent_tool.get_blocks(conv)["blocks"][
        [b["id"] for b in view["blocks"]].index(blk["id"])]
    md = (conv / "verified" / f"{STEM}.verified.md").read_text(encoding="utf-8")
    assert "approximately" not in md


def test_changed_table_cell_is_caught_and_held(conv):
    d = _doc(conv)
    cells = d["tables"][0]["data"]["table_cells"]
    target = next(c for c in cells if c["text"] == "412")
    target["text"] = "421"
    for row in d["tables"][0]["data"]["grid"]:
        for c in row:
            if c["text"] == "412":
                c["text"] = "421"
    _save(conv, d)
    assert "B1" in _checks(vc.verify(PDF, conv))
    view = agent_tool.build_verified_view(PDF, conv)
    table = next(b for b in view["blocks"] if b["type"] == "table")
    assert table["status"] == "held"
    assert "421" not in (conv / "verified" / f"{STEM}.verified.md").read_text(encoding="utf-8")


def test_csv_mismatch_is_caught(conv):
    p = conv / "tables" / "table-1.csv"
    rows = list(csv.reader(p.open(encoding="utf-8")))
    rows[1][1] = "999"
    with p.open("w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)
    assert "B3" in _checks(vc.verify(PDF, conv))


def test_dropped_paragraph_is_caught(conv):
    d = _doc(conv)
    i = _text_ref(d, "Because the chart is drawn")
    d["body"]["children"] = [c for c in d["body"]["children"] if c["$ref"] != f"#/texts/{i}"]
    for g in d.get("groups", []):
        g["children"] = [c for c in g.get("children", []) if c["$ref"] != f"#/texts/{i}"]
    _save(conv, d)
    # Dropped from the reading tree but still in the item list: the view would silently omit it.
    assert {"T3", "T5"} <= _checks(vc.verify(PDF, conv))
    view = agent_tool.build_verified_view(PDF, conv)
    assert view["document_status"] == "PARTIAL"
    assert "Because the chart is drawn" not in json.dumps(view["blocks"])
    assert any(not p["complete"] for p in view["pages"] if p["page"] == 2)


def test_deleted_paragraph_is_caught(conv):
    d = _doc(conv)
    i = _text_ref(d, "Because the chart is drawn")
    d["texts"][i]["text"] = d["texts"][i]["orig"] = ""
    _save(conv, d)
    assert "T3" in _checks(vc.verify(PDF, conv))


def test_blank_figure_image_is_caught(conv):
    d = _doc(conv)
    uri = d["pictures"][0]["image"]["uri"]
    from PIL import Image
    img = Image.open(conv / uri)
    Image.new("RGB", img.size, "white").save(conv / uri)
    assert "F1" in _checks(vc.verify(PDF, conv))
    view = agent_tool.build_verified_view(PDF, conv)
    assert next(b for b in view["blocks"] if b["type"] == "figure")["status"] == "held"


def test_missing_caption_needs_confirmation(conv):
    d = _doc(conv)
    d["pictures"][0]["captions"] = []
    _save(conv, d)
    assert "F3" in _checks(vc.verify(PDF, conv))
    view = agent_tool.build_verified_view(PDF, conv)
    fig = next(b for b in view["blocks"] if b["type"] == "figure")
    assert fig["status"] == "verified" and fig["needs_confirmation"]


def test_invented_word_in_markdown_is_caught(conv):
    p = conv / f"{STEM}.md"
    p.write_text(p.read_text(encoding="utf-8") + "\n\nThis sentence is hallucinated zebra.\n", encoding="utf-8")
    assert "M1" in _checks(vc.verify(PDF, conv))


def test_stale_verified_view_is_removed_on_failure(conv):
    agent_tool.build_verified_view(PDF, conv)
    assert (conv / "verified").exists()
    d = _doc(conv)
    d["body"]["children"].append({"$ref": "#/texts/99999"})
    _save(conv, d)
    with pytest.raises(Exception):
        agent_tool.build_verified_view(PDF, conv)
    assert not (conv / "verified").exists()


def test_check_quote():
    assert agent_tool.check_quote(PDF, "Invented counts per review stage")["match"] == "exact"
    assert agent_tool.check_quote(PDF, "invented   COUNTS per review-stage")["found"] is True
    assert agent_tool.check_quote(PDF, "counts were statistically significant")["found"] is False


# ---------------------------------------------------------------- router plugin

def test_plugin_registered_between_publisher_xml_and_grobid():
    names = [p.name for p in create_default_router().plugins]
    assert names.index("publisher_xml") < names.index("verified_pdf") < names.index("grobid_pdf")


def test_plugin_maps_verified_view_to_paper_record(conv, grobid_tei_text):
    plugin = VerifiedPdfPlugin()
    paper_input = {"source_id": "SRC-9001", "drive_path": str(PDF), "verified_dir_fixture": str(conv),
                   "title": "Verified Extraction Test Paper", "authors": ["Test Author"], "year": 2026}
    assert plugin.can_handle(paper_input)
    rec = plugin.extract(paper_input)
    assert isinstance(rec, PaperRecord)
    assert rec.completeness == "pdf-verified"
    assert rec.provenance.path_used == "verified_pdf"
    assert "fixture" in rec.provenance.tool_version and "docling 2" not in rec.provenance.tool_version
    assert rec.provenance.per_part["references"]["status"].startswith("not-available")
    assert rec.urls["verified_view"].endswith("verified")
    assert rec.abstract.startswith("We describe a small synthetic document")
    assert [s.title for s in rec.sections][:3] == ["Verified Extraction Test Paper", "Abstract", "1 Introduction"]
    assert all(s.verified == "pdf-verified" for s in rec.sections)
    assert len(rec.tables) == 1 and "| Screening | 412 | 118 | 28.6% |" in rec.tables[0].markdown
    assert len(rec.figures) == 1 and os.path.exists(rec.figures[0].url_or_path)
    assert rec.sha256 and len(rec.sha256) == 64


def test_plugin_marks_held_content(conv):
    d = _doc(conv)
    i = _text_ref(d, "Doctoral students")
    d["texts"][i]["text"] = d["texts"][i]["text"].replace("exactly", "loosely")
    _save(conv, d)
    rec = VerifiedPdfPlugin().extract({"source_id": "SRC-9002", "drive_path": str(PDF),
                                       "verified_dir_fixture": str(conv), "title": "T"})
    assert rec.completeness == "pdf-extracted"
    intro = next(s for s in rec.sections if s.title == "1 Introduction")
    assert intro.verified == "held, review"
    assert "loosely" not in intro.text and "[HELD p.1" in intro.text


def test_plugin_references_from_tei_are_checked_against_pdf(conv):
    tei = """<TEI xmlns="http://www.tei-c.org/ns/1.0"><teiHeader><fileDesc><titleStmt>
    <title level="a" type="main">Verified Extraction Test Paper</title></titleStmt>
    <sourceDesc><biblStruct><analytic><author><persName><forename>Test</forename><surname>Author</surname></persName></author></analytic></biblStruct></sourceDesc>
    </fileDesc></teiHeader><text><back><div type="references"><listBibl>
    <biblStruct xml:id="b0"><analytic><title level="a" type="main">How to read a paper</title></analytic><monogr><title level="j">ACM SIGCOMM Computer Communication Review</title><imprint><date when="2007"/></imprint></monogr></biblStruct>
    <biblStruct xml:id="b1"><analytic><title level="a" type="main">A reference that is not in this paper at all</title></analytic><monogr><imprint><date when="2020"/></imprint></monogr></biblStruct>
    </listBibl></div></back></text></TEI>"""
    rec = VerifiedPdfPlugin().extract({"source_id": "SRC-9003", "drive_path": str(PDF),
                                       "verified_dir_fixture": str(conv), "tei_fixture": tei})
    status = {r.title: r.verified for r in rec.references}
    assert status.get("How to read a paper") == "pdf-verified"
    assert status.get("A reference that is not in this paper at all") == "pdf-extracted, verify"
    assert rec.provenance.per_part["references"]["tool"] == "tei-xml-fixture"


def test_plugin_declines_without_docling_or_fixture(tmp_path):
    if verified_pdf_available():
        pytest.skip("Docling installed; decline path not reachable")
    assert VerifiedPdfPlugin().can_handle({"drive_path": str(PDF)}) is False


@pytest.fixture
def grobid_tei_text():
    return None


# ---------------------------------------------------------------- end to end (needs Docling)

@pytest.mark.skipif(not verified_pdf_available(), reason="Docling not installed (requirements-verified-pdf.txt)")
def test_end_to_end_docling_conversion(tmp_path):
    plugin = VerifiedPdfPlugin(out_root=str(tmp_path / "vp"))
    rec = plugin.extract({"source_id": "SRC-9100", "drive_path": str(PDF), "use_grobid": False,
                          "title": "Verified Extraction Test Paper"})
    assert rec.provenance.tool_version.startswith("docling ")
    assert rec.completeness in ("pdf-verified", "pdf-extracted")
    view = json.loads(next(Path(rec.urls["verified_view"]).glob("*.verified.json")).read_text(encoding="utf-8"))
    for b in view["blocks"]:
        if b["status"] == "verified" and b["type"] not in ("table", "figure"):
            assert agent_tool.check_quote(PDF, b["text"])["found"]
