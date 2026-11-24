"""--scan-config reaches the scan-agreement step from the CLI, and --profile is no longer dropped
by --parallel. The thresholds stay the caller's choice: no defaults, bad files stop ingest."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.verified_pdf import agent_tool as at  # noqa: E402

CFG = {"max_character_error_rate": 0.1, "min_inked_region_coverage": 0.5}   # test values only


def test_parser_accepts_scan_config(tmp_path):
    a = at.build_parser().parse_args(["ingest", "x.pdf", "--scan-config", str(tmp_path / "c.json")])
    assert a.scan_config == tmp_path / "c.json"
    r = at.build_parser().parse_args(["rebuild", "x.pdf", "d", "--profile", "technical_manual",
                                      "--scan-config", "c.json"])
    assert r.profile == "technical_manual" and r.scan_config == Path("c.json")


def test_no_scan_config_means_none():
    assert at.build_parser().parse_args(["ingest", "x.pdf"]).scan_config is None
    assert at.load_scan_config(None) is None


@pytest.mark.parametrize("bad", [{}, {"max_character_error_rate": 0.1}, {"min_inked_region_coverage": 0.5},
                                 {"max_character_error_rate": 2, "min_inked_region_coverage": 0.5}])
def test_bad_scan_config_stops_ingest(tmp_path, bad):
    p = tmp_path / "c.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    from scripts.verified_pdf import scan_agreement as sa
    with pytest.raises(sa.ScanAgreementError):
        at.load_scan_config(p)


def test_ingest_passes_config_and_profile(tmp_path, monkeypatch):
    seen = {}
    try:
        from scripts.verified_pdf import pdf_to_md_html as conv
    except ImportError:
        pytest.skip("docling not installed")
    monkeypatch.setattr(conv, "convert", lambda *a, **k: None)

    def fake_view(pdf, d, profile=None, scan_agreement_config=None, **k):
        seen.update(profile=profile, cfg=scan_agreement_config)
        return {"document_status": "PARTIAL", "counts": {}, "reading_order_verified": True, "pages": []}
    monkeypatch.setattr(at, "build_verified_view", fake_view)
    at.ingest([tmp_path / "x.pdf"], tmp_path, profile="technical_manual", scan_agreement_config=CFG)
    assert seen == {"profile": "technical_manual", "cfg": CFG}


def test_parallel_verify_worker_uses_profile(monkeypatch):
    par = pytest.importorskip("scripts.verified_pdf.parallel")
    seen = {}

    def fake_verify(pdf, d, only=None, write=True, profile=None):
        seen["profile"] = profile
        return {"flags": [], "checks_passed": {}}
    monkeypatch.setattr(par.vc, "verify", fake_verify)
    par.verify_worker("x.pdf", "d", ["document"], "technical_manual")
    assert seen["profile"] == "technical_manual"
