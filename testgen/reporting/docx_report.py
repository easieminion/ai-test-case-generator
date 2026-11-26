"""Word (.docx) report built with python-docx."""
from __future__ import annotations

import io

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from ..models import CATEGORY_LABELS
from . import charts
from .common import (CATEGORY_HEADERS, COVERAGE_HEADERS, STATUS_LABEL, all_missed, build_context,
                     category_rows, coverage_rows, failing_tests, missed_snippets, numbered_listing)

STATUS_RGB = {"passed": RGBColor(0x00, 0x63, 0x00), "failed": RGBColor(0xB0, 0x2A, 0x2A),
              "error": RGBColor(0xB4, 0x50, 0x1F)}
TILE_HEX = {"passed": "0CA30C", "failed": "D03B3B", "error": "EC835A"}
FONT = "Arial"
TEXT_WIDTH = 6.9  # inches (letter, 0.8in margins)


def _shade(cell, hex_fill):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")     # CLEAR, never SOLID (renders black)
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    tcPr.append(shd)


def _borders(table, color="E1E0D9"):
    tblPr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "bottom", "insideH"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "4")
        el.set(qn("w:color"), color)
        borders.append(el)
    tblPr.append(borders)


def _widths(table, widths):
    table.autofit = False
    tblPr = table._tbl.tblPr
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    tblPr.append(layout)
    for row in table.rows:
        for cell, w in zip(row.cells, widths):
            cell.width = Inches(w)
    for col, w in zip(table.columns, widths):
        col.width = Inches(w)


def _text(cell_or_par, text, size=8.5, bold=False, color=None, font=FONT):
    par = cell_or_par.paragraphs[0] if hasattr(cell_or_par, "paragraphs") else cell_or_par
    par.paragraph_format.space_after = Pt(0)
    run = par.add_run(str(text))
    run.font.size, run.bold, run.font.name = Pt(size), bold, font
    if color is not None:
        run.font.color.rgb = color
    return run


def _table(doc, rows, widths, status_col=None):
    t = doc.add_table(rows=len(rows), cols=len(rows[0]))
    t.alignment = WD_TABLE_ALIGNMENT.LEFT
    _borders(t)
    for r_i, row in enumerate(rows):
        for c_i, val in enumerate(row):
            cell = t.cell(r_i, c_i)
            if r_i == 0:
                _shade(cell, "0B0B0B")
                _text(cell, val, bold=True, color=RGBColor(255, 255, 255))
            else:
                color = None
                if status_col is not None and c_i == status_col:
                    color = STATUS_RGB.get(str(val).lower())
                    _text(cell, STATUS_LABEL.get(str(val), val), bold=True, color=color)
                else:
                    _text(cell, val)
                if r_i % 2 == 0:
                    _shade(cell, "F4F3EF")
    _widths(t, widths)
    return t


def _heading(doc, text, level=1):
    h = doc.add_heading(text, level=level)
    for r in h.runs:
        r.font.name, r.font.color.rgb = FONT, RGBColor(0x0B, 0x0B, 0x0B)
        r.font.size = Pt(15 if level == 1 else 11.5)
    h.paragraph_format.space_before = Pt(14 if level == 1 else 8)
    h.paragraph_format.space_after = Pt(5)
    h.paragraph_format.keep_with_next = True
    return h


def _para(doc, text, size=10, color=None, bold=False):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(4)
    _text(p, text, size=size, bold=bold, color=color)
    return p


def _code(doc, lines, size=6.8):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(6)
    p.paragraph_format.line_spacing = 1.0
    first = True
    for line in lines:
        if not first:
            p.add_run().add_break(WD_BREAK.LINE)
        first = False
        r = p.add_run(line)
        r.font.name, r.font.size = "Courier New", Pt(size)


def _picture(doc, png, width=6.4):
    doc.add_picture(io.BytesIO(png), width=Inches(width))
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.LEFT


def _page_number_footer(section):
    p = section.footer.paragraphs[0]
    _text(p, "SpecProof - Test Execution Report    |    Page ", size=8,
          color=RGBColor(0x52, 0x51, 0x4E))
    run = p.add_run()
    for kind, txt in (("begin", None), (None, "PAGE"), ("end", None)):
        if kind:
            el = OxmlElement("w:fldChar")
            el.set(qn("w:fldCharType"), kind)
        else:
            el = OxmlElement("w:instrText")
            el.set(qn("xml:space"), "preserve")
            el.text = txt
        run._r.append(el)
    run.font.size = Pt(8)


def build_docx(run, session_id: str = "") -> bytes:
    ctx = build_context(run, session_id)
    s = ctx.summary
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Inches(8.5), Inches(11)
    sec.left_margin = sec.right_margin = Inches(0.8)
    sec.top_margin, sec.bottom_margin = Inches(0.8), Inches(0.8)
    doc.styles["Normal"].font.name = FONT
    doc.styles["Normal"].font.size = Pt(10)
    _page_number_footer(sec)

    t = doc.add_paragraph()
    _text(t, ctx.title, size=26, bold=True)
    _para(doc, ctx.subtitle, size=11, color=RGBColor(0x52, 0x51, 0x4E))
    for note in ctx.notices:
        nt = doc.add_table(rows=1, cols=1)
        _shade(nt.cell(0, 0), "FFF4D6")
        _text(nt.cell(0, 0), note, size=9)
        _widths(nt, [TEXT_WIDTH])
        doc.add_paragraph().paragraph_format.space_after = Pt(2)

    cov = "n/a" if s["coverage_percent"] is None else f"{s['coverage_percent']:g}%"
    tiles = [("Total tests", str(s["total"]), "0B0B0B"), ("Passed", str(s["passed"]), TILE_HEX["passed"]),
             ("Failed", str(s["failed"]), TILE_HEX["failed"]), ("Errors", str(s["error"]), TILE_HEX["error"]),
             ("Pass rate", f"{s['pass_rate']:g}%", "0B0B0B"), ("Coverage", cov, "2A78D6")]
    kt = doc.add_table(rows=2, cols=6)
    for i, (lbl, val, hexcol) in enumerate(tiles):
        top, bottom = kt.cell(0, i), kt.cell(1, i)
        for c in (top, bottom):
            _shade(c, "F4F3EF")
        _text(top, val, size=20, bold=True).font.color.rgb = RGBColor(0x0B, 0x0B, 0x0B)
        top.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        _text(bottom, lbl, size=8, color=RGBColor(0x52, 0x51, 0x4E))
        bottom.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        tcPr = top._tc.get_or_add_tcPr()
        b = OxmlElement("w:tcBorders")
        el = OxmlElement("w:top")
        for k, v in (("val", "single"), ("sz", "24"), ("color", hexcol)):
            el.set(qn(f"w:{k}"), v)
        b.append(el)
        tcPr.append(b)
    _widths(kt, [TEXT_WIDTH / 6] * 6)
    doc.add_paragraph().paragraph_format.space_after = Pt(0)
    _para(doc, ctx.verdict, size=10.5)

    mt = doc.add_table(rows=len(ctx.meta), cols=2)
    _borders(mt)
    for i, (k, v) in enumerate(ctx.meta):
        _text(mt.cell(i, 0), k, bold=True)
        _text(mt.cell(i, 1), v)
    _widths(mt, [1.7, TEXT_WIDTH - 1.7])

    _heading(doc, "1. Results by test category")
    _picture(doc, charts.category_chart(run))
    _table(doc, [CATEGORY_HEADERS] + category_rows(ctx), [1.7] + [(TEXT_WIDTH - 1.7) / 5] * 5)

    _heading(doc, "2. Code coverage")
    cov_png = charts.coverage_chart(run)
    if cov_png:
        _picture(doc, cov_png)
        _table(doc, [COVERAGE_HEADERS] + coverage_rows(ctx), [1.1, 0.55, 0.7, 0.8, 0.8, 0.85, TEXT_WIDTH - 4.8])
        snips = missed_snippets(ctx)
        if snips:
            _heading(doc, "Lines of the code under test that no test executed", 2)
            _code(doc, [f"{n:>4}  {txt}"[:110] for n, txt in snips[:40]])
        else:
            _para(doc, "Every statement of the code under test was executed.")
        _para(doc, f"Measured with: {run.coverage_tool}. Comments, blank lines and docstrings are ignored.",
              size=8, color=RGBColor(0x52, 0x51, 0x4E))
    else:
        _para(doc, "Coverage data was not available for this run (see the runner log in the appendix).")

    _heading(doc, "3. Failed and errored tests")
    bad = failing_tests(ctx)
    if not bad:
        _para(doc, "No failures. Every test passed.")
    for t_ in bad:
        rows = [("Test", f"{t_.label}  {t_.title} ({CATEGORY_LABELS[t_.category]})"),
                ("Status", STATUS_LABEL[t_.status]), ("Requirement", t_.requirement or "-"),
                ("What it checks", t_.explanation or "-"), ("Failure detail", t_.message or "-")]
        tb = doc.add_table(rows=len(rows), cols=2)
        _borders(tb)
        for i, (k, v) in enumerate(rows):
            _shade(tb.cell(i, 0), "F4F3EF")
            _text(tb.cell(i, 0), k, bold=True)
            _text(tb.cell(i, 1), v, bold=(k == "Status"), color=STATUS_RGB[t_.status] if k == "Status" else None)
        _widths(tb, [1.3, TEXT_WIDTH - 1.3])
        doc.add_paragraph().paragraph_format.space_after = Pt(2)

    _heading(doc, "4. Detailed results")
    _para(doc, "Tests run in this order: positive, then negative, then edge cases.", size=8.5,
          color=RGBColor(0x52, 0x51, 0x4E))
    rows = [["ID", "Category", "Test", "Status", "Time", "Covers requirement"]]
    for t_ in run.tests:
        rows.append([t_.label, CATEGORY_LABELS[t_.category],
                     t_.title + ("  [manual]" if t_.origin == "user" else ""), t_.status,
                     f"{t_.duration_ms:.2f} ms", t_.requirement or "-"])
    _table(doc, rows, [0.4, 0.8, 2.45, 0.7, 0.75, TEXT_WIDTH - 5.1], status_col=3)

    _heading(doc, "5. Execution time")
    _picture(doc, charts.duration_chart(run), 6.2)

    _heading(doc, "6. What each test verifies")
    for t_ in run.tests:
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.keep_with_next = True
        _text(p, f"{t_.label} - {t_.title}", size=9, bold=True)
        _para(doc, t_.explanation or "No explanation provided.", size=8.5, color=RGBColor(0x52, 0x51, 0x4E))

    doc.add_page_break()
    _heading(doc, "Appendix A - Requirements")
    _code(doc, run.requirements.strip().splitlines())
    _heading(doc, "Appendix B - Code under test")
    _para(doc, "A '!' after the line number marks a line that no test executed.", size=8.5)
    _code(doc, numbered_listing(run.code_source, all_missed(ctx)))
    _heading(doc, "Appendix C - Generated test file")
    _code(doc, numbered_listing(run.test_source))
    _heading(doc, "Appendix D - Test runner log")
    _code(doc, (run.log_tail.strip() or "(no output)").splitlines())

    doc.core_properties.title = f"{ctx.title} - {ctx.subtitle}"
    doc.core_properties.author = "SpecProof"
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
