"""Block inventory: an anchor (where) plus a fingerprint (what) for every block in a conversion.

Usage:
  python scripts/verified_pdf/inventory.py CONVERTED_DIR                 # write CONVERTED_DIR/anchors.json
  python scripts/verified_pdf/inventory.py CONVERTED_DIR --compare OTHER # which pages/blocks differ

Built from <stem>.docling.json and images/ only (no Docling needed). For each anchor:
  fingerprint  sha256 of kind + content, cleaned only as Unicode NFKC + collapsed whitespace
               (case, punctuation and digits kept). Text: the block's text on that page.
               Table: the cell grid. Figure: the decoded RGB pixels of the image file.
  match_key    sha256 of kind + content reduced to lowercase letters and digits (the verifier's
               comparison form); used to match the same block across sources (e.g. LaTeX vs
               PDF) or converter versions where hyphenation, ligatures or spacing differ.
               Not defined for figures (null).
Position is not part of either hash, so a block keeps its fingerprint when other blocks move or
are renumbered; page, bbox and order sit beside it in the anchor.
  page fingerprint      sha256 of that page's anchor fingerprints, in reading order
  document fingerprint  sha256 of the page fingerprints, in page order
Equal document fingerprints mean the same blocks, with the same content, in the same order.
Identical content (e.g. a repeated running header) gives identical fingerprints by design;
`id` (the document ref) stays the unique key. A fingerprint says what the output contains,
not that it is correct: verification status lives in review/ and verified/.
"""
import argparse
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path

SCHEMA = "anchor-inventory/v1"


def _h(*parts) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def exact(s: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s or "")).strip()


def loose(s: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKC", s or "").lower() if ch.isalnum())


def text_fp(kind, text):
    return _h(kind, exact(text)), (_h(kind, loose(text)) if loose(text) else None)


def table_fp(grid):
    rows = [[c for c in r] for r in grid]
    return (_h("table", json.dumps([[exact(c) for c in r] for r in rows], ensure_ascii=False)),
            _h("table", json.dumps([[loose(c) for c in r] for r in rows])))


def image_fp(path: Path):
    from PIL import Image
    im = Image.open(path).convert("RGB")
    return _h("figure", f"{im.size[0]}x{im.size[1]}", hashlib.sha256(im.tobytes()).hexdigest())


def _reading_order(d):
    """Refs in reading order: depth-first over body, the same walk agent_tool uses for the view."""
    seen, out = set(), []
    index = {k: d.get(k, []) for k in ("texts", "tables", "pictures", "groups")}

    def get(ref):
        _, kind, i = ref.split("/")
        return kind, index[kind][int(i)]

    def walk(node):
        for c in node.get("children", []):
            ref = c["$ref"]
            if ref in seen:
                continue
            seen.add(ref)
            kind, item = get(ref)
            if kind != "groups":
                out.append((ref, kind, item))
            if kind != "pictures":          # labels inside figures are part of the image (as in the view)
                walk(item)
    walk(d["body"])
    return out


def build(conv_dir: Path) -> dict:
    conv_dir = Path(conv_dir)
    js = next(conv_dir.glob("*.docling.json"))
    d = json.loads(js.read_text(encoding="utf-8"))
    heights = {int(k): v["size"]["height"] for k, v in d.get("pages", {}).items()}
    anchors = []
    for order, (ref, kind, item) in enumerate(_reading_order(d)):
        if item.get("content_layer", "body") != "body":
            continue
        provs = item.get("prov") or []
        if kind == "texts":
            text = item.get("text") or ""
            label = item.get("label")
            if not text.strip() and label != "formula":
                continue
            akind = "formula" if label == "formula" else "text"
        elif kind == "tables":
            akind = "table"
            fp, mk = table_fp([[c["text"] for c in r] for r in item["data"]["grid"]])
        else:
            akind = "figure"
            uri = (item.get("image") or {}).get("uri")
            fp = image_fp(conv_dir / uri) if uri and (conv_dir / uri).exists() else None
            mk = None
        for pr in provs:
            if akind in ("text", "formula"):
                seg = text[pr["charspan"][0]:pr["charspan"][1]] if len(provs) > 1 else text
                fp, mk = text_fp(akind, seg)
            bb = pr["bbox"]
            h = heights.get(pr["page_no"])
            if bb["coord_origin"] == "BOTTOMLEFT" and h:
                box = [bb["l"], h - bb["t"], bb["r"], h - bb["b"]]
            else:
                box = [bb["l"], bb["t"], bb["r"], bb["b"]]
            anchors.append({"id": ref, "kind": akind, "label": item.get("label"), "order": order,
                            "page": pr["page_no"], "bbox": [round(x, 2) for x in box], "coord_origin": "TOPLEFT",
                            "captions": [c["$ref"] for c in item.get("captions", [])],
                            "fingerprint": fp, "match_key": mk})
    pages = {}
    for a in anchors:
        pages.setdefault(a["page"], []).append(a["fingerprint"] or "missing")
    n_pages = max([*heights, *pages], default=0)
    page_fps = [{"page": p, "blocks": len(pages.get(p, [])), "fingerprint": _h("page", *pages.get(p, []))}
                for p in range(1, n_pages + 1)]
    return {"schema": SCHEMA, "source": js.name,
            "document_fingerprint": _h("document", *[p["fingerprint"] for p in page_fps]),
            "pages": page_fps, "anchors": anchors}


def write(conv_dir: Path) -> dict:
    inv = build(conv_dir)
    (Path(conv_dir) / "anchors.json").write_text(json.dumps(inv, indent=1, ensure_ascii=False), encoding="utf-8")
    return inv


def compare(a: dict, b: dict) -> dict:
    """Pages whose fingerprints differ, and blocks present in one inventory but not the other.
    Blocks are matched by fingerprint (exact), then by match_key (same words, different form)."""
    if a["document_fingerprint"] == b["document_fingerprint"]:
        return {"identical": True, "pages_changed": [], "only_in_a": [], "only_in_b": [], "loose_matches": []}
    pa = {p["page"]: p["fingerprint"] for p in a["pages"]}
    pb = {p["page"]: p["fingerprint"] for p in b["pages"]}
    changed = sorted(p for p in set(pa) | set(pb) if pa.get(p) != pb.get(p))
    fb = {x["fingerprint"] for x in b["anchors"]}
    fa = {x["fingerprint"] for x in a["anchors"]}
    only_a = [x for x in a["anchors"] if x["fingerprint"] not in fb]
    only_b = [x for x in b["anchors"] if x["fingerprint"] not in fa]
    kb = {x["match_key"]: x for x in only_b if x["match_key"]}
    loose_m = [(x["id"], kb[x["match_key"]]["id"]) for x in only_a if x["match_key"] in kb]
    lm_a, lm_b = {i for i, _ in loose_m}, {j for _, j in loose_m}
    pick = lambda xs, ids: [{k: x[k] for k in ("id", "kind", "page", "fingerprint")} for x in xs if x["id"] not in ids]
    return {"identical": False, "pages_changed": changed, "only_in_a": pick(only_a, lm_a),
            "only_in_b": pick(only_b, lm_b), "loose_matches": loose_m}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("converted_dir", type=Path)
    ap.add_argument("--compare", type=Path, help="another converted dir to compare against")
    a = ap.parse_args()
    inv = write(a.converted_dir)
    if a.compare:
        other = build(a.compare)
        r = compare(inv, other)
        print(json.dumps(r, indent=2))
        sys.exit(0 if r["identical"] else 4)
    print(json.dumps({"document_fingerprint": inv["document_fingerprint"], "anchors": len(inv["anchors"]),
                      "pages": len(inv["pages"])}, indent=2))


if __name__ == "__main__":
    main()
