# Agent contract: verified PDF content

This is what an agent may rely on when it reads a PDF through `scripts/verified_pdf/agent_tool.py` (CLI or MCP), or through the 3Pass router path `verified_pdf`.
An agent never reads the raw converter output. It reads `verified/<name>.verified.json`
(or the `.verified.md` / `.verified.html` rebuilt from it).

## Guarantees

| Status / field | What it guarantees | What the agent may do |
|---|---|---|
| block `status: "verified"`, text | Every word is present in the PDF text layer inside the block's stated page and box. Nothing was added, and no word was changed. | Quote it, cite it with the page number. |
| block `status: "verified"`, table | Every cell's text equals the PDF text inside that cell's box. All PDF text in the table area belongs to a cell. Rows and columns line up in page order. | Use the numbers. Cite table and page. |
| block `status: "verified"`, figure | The image file is a pixel render of the stated PDF region, and it is not blank. | Show or describe the image. Numbers read off a chart are not text-verified. |
| block `status: "held"` | A check failed or could not run. Content is withheld by default. | Do not use the content. Report that it is held and why (`held_reason`). |
| `needs_confirmation` | The content is verified, but a person still has to confirm something (for example, a figure with no caption). | Use the content, and note that it is unconfirmed. |
| `page_complete: false` / `pages[].complete: false` | Something on that page is missing from the output (T3 recall, or T5: an item outside the reading tree). What is present is still correct. | Do not claim that the page says nothing about a topic. Ask for review or use `check-quote`. |
| `pages[].text_layer: false` | Scanned page. It cannot be verified automatically. | Treat everything on that page as unverified. |
| `pages[].triage.class: "blank"` | The page has no text and renders as a single colour: nothing is on it. It counts as complete. Any visible mark makes a page `scanned` instead. | Treat it as an intentionally blank page. |
| `reading_order_verified: false` | Block order may not match the page (for example, a title placed after section 2.1). Block contents are still correct. | Do not infer structure from order alone. |
| `document_status: "VERIFIED"` | No open flags anywhere in the document. | Use it all. |
| `document_status: "PARTIAL"` | At least one open flag. Each flag is listed. | Use only `verified` (or `human_verified`) blocks. `scan_agreement` is never citable. Respect the flags. |
| block `fingerprints`, `inventory.document_fingerprint` | An identity for the block's content (see `anchors.json`): equal fingerprints mean equal content. A fingerprint says nothing about correctness. | Use it as a stable ID to cite or cache a block and to detect changes between conversions. Trust only `status`. |

## Required behaviour for the agent

1. Before quoting any text, including text from memory or from another source, call
   `check_quote_in_pdf`. Use the quote only if `found` is true. Prefer `match: "exact"`.
2. Cite the page from the block's `provenance`, never from the agent's own estimate.
3. Never fill in a held block, never paraphrase it, and never guess its content.
4. A `FAILED` ingest result, or a missing `verified/` folder, means no content is available.
   Nothing older is kept: a failed rebuild deletes the previous view.

## What "correct" means here, and its limits

- The reference is the PDF's own text layer and page geometry, read with pdfium, which is
  independent of the layout and table models. If the PDF's text layer itself is wrong
  (broken font encoding), the output is faithful to that layer, not to the printed glyphs.
  Turn on `--ocr` for such files; they will then be flagged for review.
- Images are faithful by construction (they are renders of the page). Their meaning is not
  verified.
- Human decisions in `decisions.json` (`accept`, `not_an_error`) clear flags and are listed in
  `human_cleared_flags` together with the reviewer's name. Nothing is ever cleared automatically.

## How 3Pass records map to the view

`VerifiedPdfPlugin` (router path `verified_pdf`, priority 65) turns the verified view into a `PaperRecord`.

| Record part | Status values | Meaning |
|---|---|---|
| `completeness` | `pdf-verified` / `pdf-extracted` | `pdf-verified` only when `document_status` is `VERIFIED` (no open flag). |
| section `verified` | `pdf-verified`, `pdf-verified, confirm`, `pdf-verified, page incomplete`, `human_verified`, `scan_agreement`, `held, review` | Worst status of the blocks in the section. Held text is replaced by `[HELD p.N: ...]`. `scan_agreement` text is replaced by `[SCAN_AGREEMENT p.N: not citable until human review]` and is never citable. |
| table / figure / equation `verified` | same values | Held tables, figures and equations carry no content. |
| reference `verified` | `pdf-verified` / `pdf-extracted, verify` | References come from GROBID. A reference is `pdf-verified` only if its title is found in the PDF text layer. Authors, venue and year are still GROBID's parse. |
| `provenance.per_part.metadata` | | Title is checked against the PDF; authors, year and DOI are `pdf-extracted, verify`. |

## New statuses and fields (technical_manual profile and cell review)

| Status / field | What it guarantees | What the agent may do |
|---|---|---|
| `pages[].triage.class` | `born_digital`, `scanned`, or `broken_text_layer`, from pypdfium2 signals recorded in `pages[].triage.signals`. Classification only. | Treat `scanned` and `broken_text_layer` pages as unverifiable by the text layer. The scan-agreement tier may still run; its output is never `verified`. |
| `pages[].running_header` / `running_footer` | Repeating folio text and bbox, detected by position across nearby pages (`technical_manual` profile). Stripped from body and table text; kept as metadata. | Cite the printed page from this metadata. Do not treat the folio as body content. |
| `profile: "technical_manual"` | The academic GROBID path (including O1) was not run. | Do not expect GROBID references or a GROBID title check. |
| table `cells[]` | Per-cell status from the **cell-box check only**: the quote must match text-layer characters inside that cell's own bbox (plus the verifier's box tolerance). A stacked cell verifies when each line is a contiguous span inside the box. No bbox, or an empty value, stays `held`. A match elsewhere on the page is not enough. | Use a cell only when its `status` is `verified` or `human_verified`. |
| cell `status: "human_verified"` | A named human reviewed the held cell and confirmed it. The record includes `reviewer`, `reviewed_at`, and `evidence` (`cell_quote_sha256` plus **human-confirmed** `paragraph_quote_sha256` values). | Use the cell and cite the reviewer and evidence hashes. The tool never creates this status by itself. |
| cell `cross_references` | Ranked **candidate** paragraph links (`candidate: true`, `rank`, `overlap`, `region`, `quote_sha256`). Token overlap on the same or neighbouring pages produces candidates; they are **not** assertions that the paragraph is the correct match. A possible row/column conflict is labelled `role: "conflict"` as a hint for review, not a finding. Candidates never promote a cell to `verified`. | Show candidates to a human. Do not treat them as verified structure. |
| table `html` / `markdown` / `crop` | Faithful HTML keeps the model's real `colspan`/`rowspan` only (nothing is invented). Markdown is a flattened grid. `crop` is the union of caption, table and footnote, padded (40–80 px) and clamped so neighbouring blocks are not swallowed. | Display the crop and the marked-up table. Still use cell `status` for what may be quoted. |
| `get_held_crop(block_id)` | Returns the PNG crop of a **held** block. Rejects verified, unknown, and bbox-less blocks. | Show the crop in review. Do not invent a box. |

## REVIEW-PROCEDURE

This is the human procedure for checking table contents. Matching a paragraph to a table region is hard for automation, so the tool only proposes ranked candidates. A person decides.

1. **For each held table cell, show the cell crop next to the ranked candidate paragraph spans.** Candidates are ordered by token-overlap evidence (`overlap`, `overlap_count`, `rank`). Each candidate includes the quote, `quote_sha256`, and a stored region (row/col range of the cells that share those tokens), not the whole table by default. A possible conflict (`role: "conflict"`) is a hint to look carefully; it is not an automated assertion.
2. **The reviewer confirms or rejects which paragraph and region apply to that cell.** Unconfirmed candidates stay candidates. They are not evidence.
3. **If the reviewer approves the cell,** record `status: "human_verified"` with:
   - `reviewer` (required; approval without a reviewer field is rejected)
   - `reviewed_at` (timestamp)
   - `evidence.cell_quote_sha256`
   - `evidence.paragraph_quote_sha256` — only the **human-confirmed** candidate hashes, never an auto-selected paragraph
4. **A conflict stays held,** with the reason. It cannot be promoted to `human_verified`.
5. **The tool never auto-promotes.** Verification remains the text-layer cell-box check. Cross-reference does not change a cell from `held` to `verified`. Only a named human can set `human_verified`.

## Scan agreement (scanned and broken text-layer pages)

Pages whose triage class is `scanned` or `broken_text_layer` are rasterized at 300 DPI (`SCAN_RASTER_DPI`), deskewed, and read by two independent OCR engines: Tesseract and RapidOCR (`rapidocr-onnxruntime`). Engine names and versions are stored on the block. A missing engine or invalid configuration fails closed (the block stays `held` and the reason is reported). There is no VLM path.

The agreement threshold is the explicit config key `max_character_error_rate` (a number in `[0, 1]`). There is no silent default. Character error rate (CER) is `levenshtein(norm(a), norm(b)) / max(len(norm(a)), len(norm(b)))`, where `norm` is NFKC plus collapsed whitespace. Both-empty OCR is not agreement: the block stays `held`.

When a page is triaged `scanned` and the converter's detected regions cover too little of the page's inked area, the scan tier adds a full-page or ink-bbox `scan_page` block and runs the same dual-OCR CER check on it. The coverage threshold is the explicit config key `min_inked_region_coverage` (a number in `[0, 1]`): the minimum fraction of inked pixels (grayscale value ≤ 250 on the 300 DPI deskewed raster) that detected regions must cover. There is no silent default. A missing key fails closed. This fallback does not apply to `broken_text_layer` pages. The new block is `held` unless CER is within `max_character_error_rate`; it is never `verified` and is never auto-promoted. It appears in the MD/HTML review output and packs as tile 52 like any other scan block.

| Status / field | What it guarantees | What the agent may do |
|---|---|---|
| block `status: "scan_agreement"` | Two independent OCR engines agreed on the crop within `max_character_error_rate`. This is **never** `verified` and is **never citable** by the answer path. | Show the crop and both OCR readings for human review. Do not quote the text. Do not treat it as a text-layer verification. |
| block `ocr[]` | Each entry has `name`, `version`, `text`, and `text_sha256`. | Display both readings side by side. |
| block `crop` / `crop_sha256` | PNG crop of the block (or page) after the 300 DPI raster and deskew, with its SHA-256. | Show the crop in the MD/HTML front end. |
| block `status: "human_verified"` (scan) | A named human approved a `scan_agreement` or held scan block. Evidence is both OCR texts' sha256 and the crop sha256. | Cite the reviewer and evidence hashes. The tool never creates this status by itself. |

Packing a scanned block writes s3a-pack-to tile 52 (`to_scan_agreement`, 384 bytes), an adjunct that links to an existing section or cell record by id and carries `status`, both OCR engine names and versions, both text sha256s, the crop sha256, the CER, and the required `max_character_error_rate`. Existing tiles 41..=51 are not resized. `scan_agreement` is never packed as `verified`. `human_verified` requires a reviewer, a timestamp, and the three evidence hashes. Missing required pack fields fail closed; there is no silent default.

## REVIEW-PROCEDURE (scanned blocks)

This is the human procedure for scan-agreement and held scan blocks, in addition to the table-cell steps above.

1. **Show the page crop next to both OCR readings**, labelled `SCAN_AGREEMENT` or `HELD (scan)` in the MD/HTML front end. The readings are side by side, with engine name and version.
2. **The reviewer confirms or rejects the crop and the readings.** Unconfirmed OCR text is not evidence and is not citable.
3. **If the reviewer approves the block,** record `status: "human_verified"` with:
   - `reviewer` (required; approval without a reviewer field is rejected)
   - `reviewed_at` (timestamp)
   - `evidence.ocr_text_sha256` — both OCR texts' hashes
   - `evidence.crop_sha256`
4. **The tool never auto-promotes.** Dual-OCR agreement never sets `verified`. Only a named human can set `human_verified`.
