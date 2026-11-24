"""The README runs the verified-PDF tools as scripts (python scripts/verified_pdf/<tool>.py).
In that mode relative imports fail, so every lazy `from . import x` needs a script fallback.
These tests run the CLIs as subprocesses on the committed fixture conversion (no Docling)."""
import ast
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VP = ROOT / "scripts" / "verified_pdf"
FX = ROOT / "tests" / "fixtures" / "verified_pdf"
PDF = FX / "sample_paper.pdf"


def _run(*args):
    return subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True, cwd=ROOT)


def test_every_relative_import_has_a_script_fallback():
    """A `from . import x` must sit in a try whose `except ImportError` imports x directly."""
    bad = []
    for f in sorted(VP.glob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        guarded = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Try) and any(
                    isinstance(h.type, ast.Name) and h.type.id == "ImportError" for h in node.handlers):
                for stmt in ast.walk(ast.Module(body=node.body, type_ignores=[])):
                    if isinstance(stmt, ast.ImportFrom):
                        guarded.add(id(stmt))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level and id(node) not in guarded:
                bad.append(f"{f.name}:{node.lineno}")
    assert not bad, f"relative imports without a script-mode fallback: {bad}"


def test_rebuild_and_blocks_run_as_scripts(tmp_path):
    conv = tmp_path / "sample_paper"
    shutil.copytree(FX / "converted" / "sample_paper", conv)
    r = _run(VP / "agent_tool.py", "rebuild", PDF, conv)
    assert r.returncode == 0, r.stderr[-2000:]
    assert '"status": "VERIFIED"' in r.stdout
    r = _run(VP / "agent_tool.py", "blocks", conv)
    assert r.returncode == 0, r.stderr[-2000:]
    r = _run(VP / "verify_conversion.py", PDF, conv)
    assert r.returncode in (0, 1), r.stderr[-2000:]
    assert "ImportError" not in r.stderr
    r = _run(VP / "inventory.py", conv)
    assert r.returncode == 0, r.stderr[-2000:]
