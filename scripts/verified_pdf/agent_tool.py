"""Agent-safe PDF reading: an agent only receives content that has been checked against the PDF.

  python scripts/verified_pdf/agent_tool.py ingest paper.pdf [more.pdf] --out converted   # convert + verify + build verified view
  python scripts/verified_pdf/agent_tool.py blocks converted/paper [--include-held]        # JSON blocks for an agent
  python scripts/verified_pdf/agent_tool.py check-quote paper.pdf "exact text" [--page 8]  # is this text really in the PDF?
  python scripts/verified_pdf/agent_tool.py mcp                                             # same tools over MCP (stdio)

Rules the agent can rely on (see docs/verified_pdf/AGENT_CONTRACT.md):
  * Every block with "status": "verified" has passed every check that applies to it
    (its words are in the PDF at its stated page and box, table cells match the PDF cell by cell,
    figure images are pixel renders of the stated region), or a named human cleared the flag.
  * Anything a check could not confirm is "held": it is listed with the reason but its content
    is withheld unless --include-held is passed, and even then it is labelled.
  * Page completeness is reported separately: "complete": false means something on that page
    is missing from the output, even though what is present is correct.
  * The verified .md and .html are rebuilt only from verified blocks and are themselves
    re-checked (no word in them is absent from the PDF). If that self-check fails, nothing is
    published and the command exits non-zero.
"""
import argparse
import hashlib
import html
import unicodedata
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from . import verify_conversion as vc
    from . import inventory as inv_mod
except ImportError:  # run directly as `python scripts/verified_pdf/agent_tool.py`
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import verify_conversion as vc  # noqa: E402
    import inventory as inv_mod  # noqa: E402

# Flags that put the content of a specific block at risk -> that block is held.
CONTENT_RISK = {"T2", "T4", "B1", "B2", "B3", "F1", "E1"}
# Flags that need a human confirmation but do not change the block's content.
CONFIRM_ONLY = {"F3"}
# Page-level: content present is correct, but the page may be incomplete or unverifiable.
PAGE_LEVEL = {"T1", "T3", "T5"}
# Document-level, about the raw Docling .md/.html or order; the verified view is rebuilt from blocks.
DOC_LEVEL = {"M1", "B4", "F2", "O1"}
CLEARING = {"accept", "not_an_error"}
LABEL_TYPE = {"section_header": "heading", "title": "title", "text": "paragraph", "list_item": "list_item",
              "caption": "caption", "footnote": "footnote", "page_header": "page_header",
              "page_footer": "page_footer", "formula": "formula", "code": "code", "reference": "reference"}


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _decisions(conv_dir: Path):
    p = conv_dir / "decisions.json"
    if not p.exists():
        return {}
    out = {}
    for fid, v in json.loads(p.read_text(encoding="utf-8")).items():
        if v.get("decision") in CLEARING | {"corrected"} and v.get("reviewer"):
            out[fid] = v
    return out


def build_verified_view(pdf_path: Path, conv_dir: Path, report=None) -> dict:
    """`report` may be a review.json just produced for these exact outputs (parallel.py);
    otherwise the conversion is verified here."""
    shutil.rmtree(conv_dir / "verified", ignore_errors=True)   # never leave a stale view behind
    report = report or vc.verify(pdf_path, conv_dir)
    decisions = _decisions(conv_dir)
    open_flags = [f for f in report["flags"] if f["id"] not in decisions or decisions[f["id"]]["decision"] == "corrected"]
    cleared = [f for f in report["flags"] if f not in open_flags]
    stem, d = vc.load_doc(conv_dir)
    pdf = vc.Pdf(pdf_path)

    by_ref = {}
    for f in open_flags:
        if f.get("ref"):
            by_ref.setdefault(f["ref"], []).append(f)
    page_state = {p: {"page": p, "text_layer": True, "complete": True, "flags": []} for p in range(1, len(pdf.doc) + 1)}
    for f in open_flags:
        if f["check"] in PAGE_LEVEL and f.get("page"):
            ps = page_state[f["page"]]
            ps["flags"].append(f["id"])
            if f["check"] == "T1":
                ps["text_layer"] = False
            ps["complete"] = False

    def resolve(ref):
        kind, idx = ref.split("/")[1], int(ref.split("/")[2])
        return kind, d[kind][idx]

    order = []

    def walk(node_ref):
        kind, idx = node_ref.split("/")[1], node_ref.split("/")[2]
        node = d[kind][int(idx)] if kind != "body" else d["body"]
        if kind in ("texts", "tables", "pictures"):
            order.append(node_ref)
            if kind == "pictures":
                return  # labels inside figures are part of the image
        for ch in node.get("children", []):
            walk(ch["$ref"])

    try:
        for ch in d["body"]["children"]:
            walk(ch["$ref"])
    except (IndexError, KeyError, ValueError) as exc:
        raise RuntimeError(f"document model is internally inconsistent ({exc}); nothing published") from exc
    caption_refs = {c["$ref"] for k in ("tables", "pictures") for it in d[k] for c in it.get("captions", [])}

    blocks = []
    for ref in order:
        if ref in caption_refs:
            continue  # emitted with its table/figure
        kind, item = resolve(ref)
        prov = item.get("prov") or []
        where = [{"page": p["page_no"], "bbox": [round(p["bbox"][k], 2) for k in ("l", "t", "r", "b")],
                  "coord_origin": p["bbox"]["coord_origin"]} for p in prov]
        risk = [f["id"] for f in by_ref.get(ref, []) if f["check"] in CONTENT_RISK]
        confirm = [f["id"] for f in by_ref.get(ref, []) if f["check"] in CONFIRM_ONLY]
        blk = {"id": ref, "pages": sorted({w["page"] for w in where}), "provenance": where}
        if kind == "texts":
            if item.get("content_layer", "body") != "body":
                continue
            text = item.get("text") or ""
            if not text.strip() and item.get("label") != "formula":
                continue
            blk["type"] = LABEL_TYPE.get(item.get("label"), item.get("label"))
            marker = (item.get("marker") or "").strip()
            if marker and prov:
                src = vc.joined(pdf.box_text(prov[0]["page_no"], prov[0]["bbox"], prov[0]["bbox"]["coord_origin"]))
                marker = marker if vc.joined(marker) and vc.joined(marker) in src else ""
            if blk["type"] == "list_item" and not marker and blocks and blocks[-1].get("type") == "list_item" \
                    and blocks[-1]["status"] == "verified" and not risk:
                prev = blocks[-1]                       # wrapped continuation of the previous entry
                prev["text"] = prev["text"] + " " + text
                prev["merged_from"] = prev.get("merged_from", [prev["id"]]) + [ref]
                prev["provenance"] += where
                prev["pages"] = sorted(set(prev["pages"]) | set(blk["pages"]))
                prev["sha256"] = _sha(prev["text"])
                continue
            if blk["type"] == "formula" and not text.strip():
                risk = risk or ["E1"]
            blk.update(text=text, marker=marker or None, level=item.get("level"))
            blk["sha256"] = _sha(text)
        elif kind == "tables":
            blk["type"] = "table"
            grid = [[c["text"] for c in row] for row in item["data"]["grid"]]
            blk.update(rows=grid, caption=" ".join(resolve(c["$ref"])[1]["text"] for c in item.get("captions", [])) or None)
            blk["sha256"] = _sha(json.dumps(grid, ensure_ascii=False))
        else:
            blk["type"] = "figure"
            uri = (item.get("image") or {}).get("uri")
            blk.update(image=uri, caption=" ".join(resolve(c["$ref"])[1]["text"] for c in item.get("captions", [])) or None)
            if uri and (conv_dir / uri).exists():
                blk["sha256"] = hashlib.sha256((conv_dir / uri).read_bytes()).hexdigest()
            blk["note"] = "image is a pixel render of the stated PDF region; numbers inside charts are not text-verified"
        blk["status"] = "held" if risk else "verified"
        if risk:
            blk["held_reason"] = [f"{fid}: {next((f['detail'] for f in open_flags if f['id'] == fid), 'formula not decoded')}" for fid in risk]
        if confirm:
            blk["needs_confirmation"] = confirm
        blk["page_complete"] = all(page_state[p]["complete"] for p in blk["pages"]) if blk["pages"] else True
        blocks.append(blk)

    inv = inv_mod.write(conv_dir)                  # anchors.json: anchor + fingerprint per block
    fps = {}
    for x in inv["anchors"]:
        fps.setdefault(x["id"], []).append(x["fingerprint"])
    for b in blocks:
        b["fingerprints"] = [f for r in b.get("merged_from", [b["id"]]) for f in fps.get(r, [])]

    doc_flags = [f for f in open_flags if f["check"] in DOC_LEVEL]
    order_ok = not any(f["check"] == "O1" for f in doc_flags)
    view = {
        "schema": "verified-pdf-view/v1",
        "source_pdf": pdf_path.name,
        "source_sha256": report["source_sha256"],
        "inventory": {"file": "anchors.json", "schema": inv["schema"],
                      "document_fingerprint": inv["document_fingerprint"]},
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "document_status": ("VERIFIED" if not open_flags else "PARTIAL"),
        "reading_order_verified": order_ok,
        "counts": {"verified": sum(b["status"] == "verified" for b in blocks),
                   "held": sum(b["status"] == "held" for b in blocks),
                   "needs_confirmation": sum(bool(b.get("needs_confirmation")) for b in blocks)},
        "pages": list(page_state.values()),
        "document_flags": [{"id": f["id"], "check": f["check"], "detail": f["detail"]} for f in doc_flags],
        "human_cleared_flags": [{"id": f["id"], **decisions[f["id"]]} for f in cleared],
        "blocks": blocks,
    }
    out = conv_dir / "verified"
    tmp = conv_dir / "verified.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    leftover = None
    md = htm = None
    for _attempt in range(8):
        md, htm = render(view, conv_dir)
        leftover = self_check(view, md, htm, pdf)
        if not leftover:
            break
        def blob_of(b):
            return " ".join(filter(None, [b.get("text"), b.get("caption"),
                                          " ".join(c for row in (b.get("rows") or []) for c in row)]))

        def hold_block(b, why):
            if b.get("status") != "verified":
                return False
            b["status"] = "held"
            reasons = list(b.get("held_reason") or [])
            reasons.append(why)
            b["held_reason"] = reasons
            return True

        held_any = False
        for b in view["blocks"]:
            hit = set(vc.words(_join_hyphens(blob_of(b)))) & set(leftover)
            if hit:
                held_any = hold_block(b, "self-check: words not in the PDF text layer: " + " ".join(sorted(hit))) or held_any
        # Words invented only when two adjacent blocks are concatenated
        # (Docling often splits a hyphenated word across a paragraph break).
        if leftover:
            order = [b for b in view["blocks"] if b.get("status") == "verified"]
            for b1, b2 in zip(order, order[1:]):
                t1, t2 = blob_of(b1).rstrip(), blob_of(b2).lstrip()
                if not t1 or not t2:
                    continue
                joined = set(vc.words(_join_hyphens(t1 + "\n" + t2))) | set(vc.words(_join_hyphens(t1 + "-" + t2)))
                edge = joined - set(vc.words(_join_hyphens(t1))) - set(vc.words(_join_hyphens(t2)))
                hit = edge & set(leftover)
                if hit:
                    why = "self-check: hyphenation across adjacent blocks not in the PDF text layer: " + " ".join(sorted(hit))
                    held_any = hold_block(b1, why) or held_any
                    held_any = hold_block(b2, why) or held_any
        view["counts"] = {"verified": sum(b["status"] == "verified" for b in view["blocks"]),
                          "held": sum(b["status"] == "held" for b in view["blocks"]),
                          "needs_confirmation": sum(bool(b.get("needs_confirmation")) for b in view["blocks"])}
        view["document_status"] = "VERIFIED" if not any(view.get("document_flags")) and view["counts"]["held"] == 0 and not leftover else "PARTIAL"
        if not held_any:
            raise RuntimeError("self-check failed and no verified block could be held for leftover words: "
                               + " ".join(sorted(leftover))[:200])
    else:
        raise RuntimeError("self-check still has words not in the PDF after holding blocks: "
                           + " ".join(sorted(leftover or []))[:200])
    (tmp / f"{stem}.verified.md").write_text(md, encoding="utf-8")
    (tmp / f"{stem}.verified.html").write_text(htm, encoding="utf-8")
    view["outputs_sha256"] = {f"{stem}.verified.md": _sha(md), f"{stem}.verified.html": _sha(htm)}
    (tmp / f"{stem}.verified.json").write_text(json.dumps(view, indent=2, ensure_ascii=False), encoding="utf-8")
    shutil.rmtree(out, ignore_errors=True)
    tmp.rename(out)
    return view


def _md_table(rows):
    esc = lambda s: s.replace("|", "\\|").replace("\n", " ")
    head, *rest = rows or [[]]
    lines = ["| " + " | ".join(esc(c) for c in head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    lines += ["| " + " | ".join(esc(c) for c in r) + " |" for r in rest]
    return "\n".join(lines)


def render(view, conv_dir: Path):
    rel = lambda p: "../" + p if p else ""
    md = [f"<!-- verified-pdf-view/v1 | source {view['source_pdf']} sha256 {view['source_sha256']} | "
          f"status {view['document_status']} | reading order verified: {view['reading_order_verified']} -->", ""]
    hb = []
    for b in view["blocks"]:
        loc = f"p.{','.join(map(str, b['pages']))}"
        if b["status"] == "held":
            md.append(f"> HELD ({loc}): content withheld until reviewed. " + "; ".join(r.split(':')[0] for r in b["held_reason"]))
            hb.append(f'<div class="held">HELD ({loc}): content withheld until reviewed.</div>')
        elif b["type"] in ("heading", "title"):
            lvl = 1 if b["type"] == "title" else min(6, (b.get("level") or 1) + 1)
            md.append("#" * lvl + " " + b["text"])
            hb.append(f"<h{lvl}>{html.escape(b['text'])}</h{lvl}>")
        elif b["type"] == "list_item":
            md.append(f"- {b['marker'] + ' ' if b.get('marker') else ''}{b['text']}")
            hb.append(f"<p class=\"li\">{html.escape((b.get('marker') or '') + ' ' + b['text']).strip()}</p>")
        elif b["type"] == "table":
            if b.get("caption"):
                md.append(b["caption"]); md.append("")
                hb.append(f"<p class=\"cap\">{html.escape(b['caption'])}</p>")
            md.append(_md_table(b["rows"]))
            trs = "".join("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in r) + "</tr>" for r in b["rows"])
            hb.append(f"<table>{trs}</table>")
        elif b["type"] == "figure":
            md.append(f"![Figure ({loc})]({rel(b.get('image'))})")
            hb.append(f'<figure><img src="{html.escape(rel(b.get("image")))}" alt="Figure ({loc})">'
                      + (f"<figcaption>{html.escape(b['caption'])}</figcaption>" if b.get("caption") else "") + "</figure>")
            if b.get("caption"):
                md.append(""); md.append(b["caption"])
        else:
            md.append(b["text"])
            hb.append(f"<p>{html.escape(b['text'])}</p>")
        md.append("")
    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(view['source_pdf'])}</title>
<meta name="source-sha256" content="{view['source_sha256']}"><meta name="document-status" content="{view['document_status']}">
<style>body{{max-width:820px;margin:2rem auto;font-family:system-ui,sans-serif;line-height:1.55;color:#222;padding:0 1rem}}
table{{border-collapse:collapse}}td{{border:1px solid #bbb;padding:.25rem .5rem}}img{{max-width:100%}}
.held{{background:#fff4e5;border-left:4px solid #d97706;padding:.4rem .6rem}}.cap{{font-style:normal;color:#444}}</style></head>
<body>{''.join(hb)}</body></html>"""
    return "\n".join(md), page


def _join_hyphens(s: str) -> str:
    """Join hyphenated compounds the same way pdfium+norm_text joins U+FFFE hyphens."""
    s = vc.norm_text(s)
    while True:
        n = re.sub(r"(\w)[-–—]\s*(\w)", r"\1\2", s)
        if n == s:
            return n
        s = n


def _join_hyphens_known(s: str, bag) -> str:
    """Join a hyphen only when the compound is already a word in the PDF bag.

    Greedy joining of the full Markdown/HTML document would glue a paragraph-final
    hyphen onto the next heading once HTML tags are stripped ('clas-' + 'CHAPTER'
    -> 'claschapter'). Line-end joins must be bag-aware too: '012-\\n0528' is not
    '0120528' unless that compound is in the PDF. When the compound is unknown,
    replace the hyphen with a space so later norm_text/words() cannot invent it.
    """
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(r"[\u00ad\ufffe\x02]\s*", "", s)
    def repl(m):
        compound = m.group(1) + m.group(2)
        if bag[compound.lower()] > 0 or bag[compound] > 0:
            return compound
        return m.group(1) + " " + m.group(2)
    while True:
        n = re.sub(r"(\w)[-–—][ \t]*(?:\r?\n\s*)?(\w)", repl, s)
        if n == s:
            return n
        s = n


def _self_check_extras(txt, pdf_all, allowed):
    """Return (never_in_pdf, surplus_of_known_words) after hyphen and fragment joins.

    pdfium records discretionary hyphens as U+FFFE, which norm_text joins, while
    Docling markdown keeps ASCII hyphens or splits a word across a heading. This
    is a tokenizer reconciliation, not a waiver of invented vocabulary.
    """
    bag = (pdf_all + allowed).copy()
    seq = vc.words(_join_hyphens_known(txt, bag))
    leftover = []
    i = 0
    while i < len(seq):
        if i + 1 < len(seq):
            joined = seq[i] + seq[i + 1]
            if bag[joined] > 0 and (bag[seq[i]] == 0 or bag[seq[i + 1]] == 0):
                bag[joined] -= 1
                i += 2
                continue
        if bag[seq[i]] > 0:
            bag[seq[i]] -= 1
            i += 1
            continue
        leftover.append(seq[i])
        i += 1
    counts = vc.Counter(leftover)
    # Pair non-adjacent unknown fragments ("clas" + "sification" -> "classification").
    never_tokens = []
    for w, n in counts.items():
        if (pdf_all + allowed)[w] == 0:
            never_tokens.extend([w] * n)
    cancelled = vc.Counter()
    used = set()
    compounds = pdf_all + allowed
    for i, a in enumerate(never_tokens):
        if i in used:
            continue
        for j, b in enumerate(never_tokens):
            if j <= i or j in used:
                continue
            if compounds[a + b] > 0 or compounds[b + a] > 0:
                used.add(i)
                used.add(j)
                cancelled[a] += 1
                cancelled[b] += 1
                break
    counts -= cancelled
    never = vc.Counter({w: n for w, n in counts.items() if (pdf_all + allowed)[w] == 0 and n > 0})
    surplus = vc.Counter({w: n for w, n in counts.items() if (pdf_all + allowed)[w] > 0 and n > 0})
    return never, surplus


def self_check(view, md, htm, pdf):
    """Return a Counter of invented words, or an empty Counter if the view is clean.

    Known-word surplus (duplication / count mismatch, not a new word) is recorded on
    the view as a document flag and does not fail the check. Callers must hold any
    verified block that still introduces a never-in-PDF word and re-render.
    """
    pdf_all = vc.Counter()
    for p in range(1, len(pdf.doc) + 1):
        # Use the same tokenizer as markdown (norm_text + words). Greedy
        # mid-line hyphen joining would merge "MIL-STD-1285" into one PDF
        # token while markdown still has three, leaving "1285" as leftover
        # with no block to hold. Line-end and discretionary hyphens are
        # already joined by norm_text.
        pdf_all.update(vc.words(pdf.page_text(p)))
    allowed = vc.Counter(vc.words("HELD content withheld until reviewed Figure p verified pdf view v1 source sha256 status "
                                  "VERIFIED PARTIAL reading order true false " + view["source_pdf"] + " " + view["source_sha256"]))
    never_all, surplus_all = vc.Counter(), vc.Counter()
    for kind, txt in (("verified Markdown", re.sub(r"!\[[^\]]*\]\([^)]*\)|<!--.*?-->|> HELD.*", " ", md)),
                      ("verified HTML", html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<head.*?</head>|<div class=\"held\">.*?</div>", " ", htm))))):
        never, surplus = _self_check_extras(txt, pdf_all, allowed)
        never_all += never
        surplus_all += surplus
    if surplus_all:
        flag = {"id": "LOCAL-SC1", "check": "SC1",
                "detail": "self-check known-word surplus after hyphen reconciliation: "
                          + " ".join(f"{w}:{n}" for w, n in sorted(surplus_all.items()))}
        if flag not in view.setdefault("document_flags", []):
            view["document_flags"].append(flag)
    md_j = vc.joined(md)
    for b in view["blocks"]:
        if b["status"] == "verified" and b.get("text") and vc.joined(b["text"]) not in md_j:
            raise RuntimeError(f"self-check failed: verified block {b['id']} missing from verified Markdown")
    return never_all


def check_quote(pdf_path: Path, quote: str, page=None) -> dict:
    """Return where `quote` occurs in the PDF text layer (ignoring case, spacing and punctuation)."""
    pdf = vc.Pdf(pdf_path)
    q = vc.joined(quote)
    if not q:
        return {"found": False, "reason": "empty quote"}
    pages = [page] if page else range(1, len(pdf.doc) + 1)
    hits = [p for p in pages if q in vc.joined(pdf.page_text(p))]
    if not hits and not page:  # quote spanning a page break
        allj = [vc.joined(pdf.page_text(p)) for p in range(1, len(pdf.doc) + 1)]
        hits = [f"{p}-{p + 1}" for p in range(1, len(allj)) if q in allj[p - 1] + allj[p]]
    exact = [p for p in hits if isinstance(p, int) and re.sub(r"\s+", " ", vc.norm_text(quote)).strip() in
             re.sub(r"\s+", " ", vc.norm_text(pdf.page_text(p)))]
    return {"found": bool(hits), "pages": hits, "exact_on_pages": exact,
            "match": "exact" if exact else ("normalized" if hits else "none"),
            "note": "normalized = same letters and digits in the same order; punctuation/case/spacing may differ"}


def ingest(pdfs, out: Path, ocr=False, grobid=None, parallel=False, workers=None) -> list:
    try:
        from . import pdf_to_md_html as conv
    except ImportError:
        import pdf_to_md_html as conv
    results = []
    for pdf in pdfs:
        pdf = Path(pdf)
        try:
            if parallel:
                try:
                    from . import parallel as par
                except ImportError:
                    import parallel as par
                v = par.run_shards(pdf, out, ocr=ocr, grobid=grobid, workers=workers)["view"]
            else:
                conv.convert(pdf, out, ocr=ocr, grobid=grobid)
                v = build_verified_view(pdf, out / pdf.stem)
            results.append({"pdf": pdf.name, "status": v["document_status"], "counts": v["counts"],
                            "reading_order_verified": v["reading_order_verified"],
                            "incomplete_pages": [p["page"] for p in v["pages"] if not p["complete"]],
                            "verified_json": str(out / pdf.stem / "verified" / f"{pdf.stem}.verified.json"),
                            "review_page": str(out / pdf.stem / "review" / "review.html")})
        except Exception as exc:
            results.append({"pdf": pdf.name, "status": "FAILED", "error": str(exc)})
    return results


def get_blocks(conv_dir: Path, include_held=False) -> dict:
    v = json.loads(next((conv_dir / "verified").glob("*.verified.json")).read_text(encoding="utf-8"))
    if not include_held:
        v["blocks"] = [b if b["status"] == "verified" else {k: b[k] for k in ("id", "type", "pages", "status", "held_reason")}
                       for b in v["blocks"]]
    return v


def build_parser():
    """CLI parser. Extracted so tests can check flags without running ingest."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a1 = sub.add_parser("ingest"); a1.add_argument("pdfs", nargs="+"); a1.add_argument("--out", type=Path, default=Path("converted"))
    a1.add_argument("--ocr", action="store_true"); a1.add_argument("--grobid")
    a1.add_argument("--parallel", action="store_true", help="page shards in parallel (see parallel.py); same outputs and checks")
    a1.add_argument("--workers", type=int, default=None,
                    help="max parallel Docling shards for --parallel (default: cpu_count). Use 2 on a 15 GB machine.")
    a2 = sub.add_parser("blocks"); a2.add_argument("converted_dir", type=Path); a2.add_argument("--include-held", action="store_true")
    a3 = sub.add_parser("check-quote"); a3.add_argument("pdf", type=Path); a3.add_argument("quote"); a3.add_argument("--page", type=int)
    a4 = sub.add_parser("rebuild", help="re-verify and rebuild the verified view (e.g. after decisions.json)")
    a4.add_argument("pdf", type=Path); a4.add_argument("converted_dir", type=Path)
    sub.add_parser("mcp")
    return ap


def main():
    ap = build_parser()
    a = ap.parse_args()
    if a.cmd == "ingest":
        res = ingest(a.pdfs, a.out, a.ocr, a.grobid, a.parallel, getattr(a, "workers", None))
        print(json.dumps(res, indent=2))
        sys.exit(1 if any(r["status"] == "FAILED" for r in res) else 0)
    if a.cmd == "blocks":
        print(json.dumps(get_blocks(a.converted_dir, a.include_held), indent=2, ensure_ascii=False))
    if a.cmd == "check-quote":
        r = check_quote(a.pdf, a.quote, a.page)
        print(json.dumps(r, indent=2))
        sys.exit(0 if r["found"] else 3)
    if a.cmd == "rebuild":
        v = build_verified_view(a.pdf, a.converted_dir)
        print(json.dumps({"status": v["document_status"], "counts": v["counts"]}, indent=2))
    if a.cmd == "mcp":
        from mcp.server.fastmcp import FastMCP
        srv = FastMCP("verified-pdf")

        @srv.tool()
        def ingest_pdf(pdf_path: str, out_dir: str = "converted") -> str:
            """Convert a PDF and build its verified view. Returns status and where the verified JSON is."""
            return json.dumps(ingest([pdf_path], Path(out_dir)))

        @srv.tool()
        def get_verified_blocks(converted_dir: str, include_held: bool = False) -> str:
            """Return verified blocks (text, tables, figures) with page/box provenance. Held blocks are withheld by default."""
            return json.dumps(get_blocks(Path(converted_dir), include_held), ensure_ascii=False)

        @srv.tool()
        def check_quote_in_pdf(pdf_path: str, quote: str, page: int = 0) -> str:
            """Check whether a quote really occurs in the PDF; use before citing any text."""
            return json.dumps(check_quote(Path(pdf_path), quote, page or None))

        srv.run(transport="stdio")


if __name__ == "__main__":
    main()
