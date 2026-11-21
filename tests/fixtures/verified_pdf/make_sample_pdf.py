"""Generate the synthetic test paper used by tests/test_verified_pdf.py.

The content is invented for testing only (no real study). Regenerate with:
  python tests/fixtures/verified_pdf/make_sample_pdf.py
"""
import os
import pymupdf

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "sample_paper.pdf")

PARAS_1 = [
    "This synthetic paper exists only to test the verified PDF path in 3Pass. It contains headings, "
    "body text, a ruled table, a vector chart and a short reference list.",
    "Doctoral students reviewing literature need extracted text that matches the source exactly. "
    "Every block in this file can be checked against the PDF text layer and its page geometry.",
]
PARAS_2 = [
    "The table below reports invented counts for three review stages. The numbers have no meaning "
    "outside this test and were chosen so that each cell value is distinct.",
]
ROWS = [["Stage", "Papers", "Kept", "Rate"],
        ["Screening", "412", "118", "28.6%"],
        ["Full text", "118", "47", "39.8%"],
        ["Synthesis", "47", "31", "66.0%"]]
REFS = [
    "[1] S. Keshav. How to read a paper. ACM SIGCOMM Computer Communication Review, 2007.",
    "[2] H. Lightman et al. Let's verify step by step. arXiv preprint arXiv:2305.20050, 2023.",
    "[3] A. Example. A placeholder reference for parser testing. Test Journal, 2026.",
]


def text_block(page, y, text, size=10.5, font="helv", width=470):
    rect = pymupdf.Rect(72, y, 72 + width, y + 200)
    rc = page.insert_textbox(rect, text, fontsize=size, fontname=font)
    used = 200 - rc if rc >= 0 else 200
    return y + used + 8


def main():
    doc = pymupdf.open()
    p = doc.new_page(width=612, height=792)
    p.insert_text((72, 90), "Verified Extraction Test Paper", fontsize=20, fontname="hebo")
    p.insert_text((72, 114), "Test Author, Example University", fontsize=11, fontname="helv")
    y = 150
    p.insert_text((72, y), "Abstract", fontsize=13, fontname="hebo"); y += 10
    y = text_block(p, y, "We describe a small synthetic document used to check that extracted text, "
                         "tables and figures match the original PDF without additions or omissions.")
    p.insert_text((72, y + 10), "1 Introduction", fontsize=13, fontname="hebo"); y += 20
    for t in PARAS_1:
        y = text_block(p, y, t)
    p.insert_text((72, y + 10), "2 Method", fontsize=13, fontname="hebo"); y += 20
    for t in PARAS_2:
        y = text_block(p, y, t)
    # ruled table
    y += 6
    p.insert_text((72, y + 4), "Table 1: Invented counts per review stage.", fontsize=10, fontname="helv")
    y += 14
    col_w, row_h, x0 = [120, 90, 90, 90], 20, 72
    for r, row in enumerate(ROWS):
        x = x0
        for c, cell in enumerate(row):
            rect = pymupdf.Rect(x, y + r * row_h, x + col_w[c], y + (r + 1) * row_h)
            p.draw_rect(rect, color=(0, 0, 0), width=0.6)
            p.insert_text((x + 5, y + r * row_h + 14), cell, fontsize=10,
                          fontname="hebo" if r == 0 else "helv")
            x += col_w[c]

    # page 2: vector chart + references
    p2 = doc.new_page(width=612, height=792)
    ox, oy, w, h = 110, 300, 360, 200
    p2.draw_line((ox, oy), (ox + w, oy), color=(0, 0, 0), width=1)
    p2.draw_line((ox, oy), (ox, oy - h), color=(0, 0, 0), width=1)
    vals = [28.6, 39.8, 66.0]
    for i, v in enumerate(vals):
        bx = ox + 40 + i * 110
        p2.draw_rect(pymupdf.Rect(bx, oy - v * 2.6, bx + 60, oy), color=(0.1, 0.3, 0.7), fill=(0.3, 0.5, 0.9))
    p2.insert_text((ox - 5, oy + 18), "Screening      Full text      Synthesis", fontsize=9, fontname="helv")
    p2.insert_text((72, oy + 50), "Figure 1: Retention rate by stage (invented data).", fontsize=10, fontname="helv")
    y = oy + 90
    p2.insert_text((72, y), "3 Discussion", fontsize=13, fontname="hebo"); y += 10
    y = text_block(p2, y, "Because the chart is drawn with vector graphics, a converter must render the "
                          "figure region rather than copy an embedded bitmap.")
    p2.insert_text((72, y + 10), "References", fontsize=13, fontname="hebo"); y += 20
    for r in REFS:
        y = text_block(p2, y, r, size=9.5)
    doc.set_metadata({"title": "Verified Extraction Test Paper", "author": "Test Author",
                      "creationDate": "D:20261121000000Z", "modDate": "D:20261121000000Z"})
    doc.save(OUT, garbage=4, deflate=True, no_new_id=True)
    print(OUT)


if __name__ == "__main__":
    main()
