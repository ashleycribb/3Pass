"""Tests for the anchor inventory (scripts/verified_pdf/inventory.py): fingerprints per anchor,
page and document fingerprints, and comparison between two conversions."""
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.verified_pdf import agent_tool, inventory as inv  # noqa: E402

FX = ROOT / "tests" / "fixtures" / "verified_pdf"
PDF = FX / "sample_paper.pdf"
CONVERTED = FX / "converted" / "sample_paper"
STEM = "sample_paper"


@pytest.fixture
def conv(tmp_path):
    d = tmp_path / STEM
    shutil.copytree(CONVERTED, d)
    return d


def _edit(conv, fn):
    p = conv / f"{STEM}.docling.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    fn(d)
    p.write_text(json.dumps(d), encoding="utf-8")


def _text(d, needle):
    return next(t for t in d["texts"] if needle in (t.get("text") or ""))


def test_every_anchor_has_a_fingerprint_and_build_is_deterministic(conv):
    a, b = inv.build(conv), inv.build(conv)
    assert a == b
    assert a["anchors"] and all(x["fingerprint"] for x in a["anchors"])
    assert {x["kind"] for x in a["anchors"]} >= {"text", "table", "figure"}
    assert [p["page"] for p in a["pages"]] == [1, 2]
    assert sum(p["blocks"] for p in a["pages"]) == len(a["anchors"])
    assert [x["order"] for x in a["anchors"]] == sorted(x["order"] for x in a["anchors"])


def test_verified_view_carries_fingerprints(conv):
    view = agent_tool.build_verified_view(PDF, conv)
    on_disk = json.loads((conv / "anchors.json").read_text(encoding="utf-8"))
    assert view["inventory"]["document_fingerprint"] == on_disk["document_fingerprint"]
    known = {x["fingerprint"] for x in on_disk["anchors"]}
    assert all(b["fingerprints"] and set(b["fingerprints"]) <= known for b in view["blocks"])


def test_changed_word_changes_only_that_block_and_page(conv, tmp_path):
    before = inv.build(conv)
    other = tmp_path / "other" / STEM
    shutil.copytree(conv, other)
    _edit(other, lambda d: _text(d, "Doctoral students").update(
        text=_text(d, "Doctoral students")["text"].replace("exactly", "approximately")))
    r = inv.compare(before, inv.build(other))
    assert not r["identical"] and len(r["pages_changed"]) == 1
    assert len(r["only_in_a"]) == len(r["only_in_b"]) == 1 and r["only_in_a"][0]["kind"] == "text"
    assert r["loose_matches"] == []


def test_whitespace_is_ignored_but_case_is_not(conv):
    base = inv.build(conv)
    d0 = json.loads((conv / f"{STEM}.docling.json").read_text(encoding="utf-8"))
    i = d0["texts"].index(_text(d0, "Doctoral students"))
    t0 = d0["texts"][i]["text"]
    _edit(conv, lambda d: d["texts"][i].update(text=t0.replace(" ", "   \n ")))
    assert inv.build(conv)["document_fingerprint"] == base["document_fingerprint"]
    _edit(conv, lambda d: d["texts"][i].update(text=t0.upper()))
    changed = inv.build(conv)
    r = inv.compare(base, changed)
    assert not r["identical"] and len(r["loose_matches"]) == 1      # same words, different form
    assert r["only_in_a"] == [] and r["only_in_b"] == []


def test_position_is_not_part_of_the_fingerprint(conv):
    base = inv.build(conv)

    def shift(d):
        for p in _text(d, "Doctoral students")["prov"]:
            p["bbox"]["l"] += 3
            p["bbox"]["r"] += 3
    _edit(conv, shift)
    moved = inv.build(conv)
    assert moved["document_fingerprint"] == base["document_fingerprint"]
    assert [x["bbox"] for x in moved["anchors"]] != [x["bbox"] for x in base["anchors"]]


def test_changed_table_cell_and_figure_pixels_change_fingerprints(conv):
    base = {x["id"]: x["fingerprint"] for x in inv.build(conv)["anchors"]}

    def cell(d):
        for c in [c for r in d["tables"][0]["data"]["grid"] for c in r]:
            if c["text"] == "412":
                c["text"] = "421"
    _edit(conv, cell)
    from PIL import Image
    uri = json.loads((conv / f"{STEM}.docling.json").read_text(encoding="utf-8"))["pictures"][0]["image"]["uri"]
    img = Image.open(conv / uri).convert("RGB")
    img.putpixel((0, 0), (1, 2, 3))
    img.save(conv / uri)
    after = {x["id"]: x["fingerprint"] for x in inv.build(conv)["anchors"]}
    changed = {k for k in base if base[k] != after[k]}
    assert changed == {"#/tables/0", "#/pictures/0"}


def test_cli_compare_exit_codes(conv, tmp_path):
    import subprocess
    other = tmp_path / "copy" / STEM
    shutil.copytree(conv, other)
    cmd = [sys.executable, str(ROOT / "scripts" / "verified_pdf" / "inventory.py"), str(conv), "--compare", str(other)]
    assert subprocess.run(cmd, capture_output=True).returncode == 0
    _edit(other, lambda d: _text(d, "Doctoral students").update(text="Doctoral students differ."))
    assert subprocess.run(cmd, capture_output=True).returncode == 4
