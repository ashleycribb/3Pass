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
| `reading_order_verified: false` | Block order may not match the page (for example, a title placed after section 2.1). Block contents are still correct. | Do not infer structure from order alone. |
| `document_status: "VERIFIED"` | No open flags anywhere in the document. | Use it all. |
| `document_status: "PARTIAL"` | At least one open flag. Each flag is listed. | Use only `verified` blocks. Respect the flags. |

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
| section `verified` | `pdf-verified`, `pdf-verified, confirm`, `pdf-verified, page incomplete`, `held, review` | Worst status of the blocks in the section. Held text is replaced by `[HELD p.N: ...]`. |
| table / figure / equation `verified` | same values | Held tables, figures and equations carry no content. |
| reference `verified` | `pdf-verified` / `pdf-extracted, verify` | References come from GROBID. A reference is `pdf-verified` only if its title is found in the PDF text layer. Authors, venue and year are still GROBID's parse. |
| `provenance.per_part.metadata` | | Title is checked against the PDF; authors, year and DOI are `pdf-extracted, verify`. |
