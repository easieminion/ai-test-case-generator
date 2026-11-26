"""PDF report (default format) built with reportlab."""
from __future__ import annotations

import io
from pathlib import Path
from xml.sax.saxutils import escape

import matplotlib
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, Preformatted,
                                SimpleDocTemplate, Spacer, Table, TableStyle)

from ..models import CATEGORY_LABELS
from . import charts
from .common import (CATEGORY_HEADERS, COVERAGE_HEADERS, STATUS_LABEL, ReportContext, all_missed,
                     build_context, category_rows, coverage_rows, failing_tests, missed_snippets,
                     numbered_listing)

_FONT_DIR = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
_REGISTERED = False

INK = colors.HexColor("#0b0b0b")
INK_2 = colors.HexColor("#52514e")
GRID = colors.HexColor("#e1e0d9")
BAND = colors.HexColor("#f4f3ef")
BLUE = colors.HexColor("#2a78d6")
STATUS_COLOR = {"passed": colors.HexColor("#006300"), "failed": colors.HexColor("#b02a2a"),
                "error": colors.HexColor("#b4501f")}   # darker text steps, readable on white
TILE_COLOR = {"passed": colors.HexColor("#0ca30c"), "failed": colors.HexColor("#d03b3b"),
              "error": colors.HexColor("#ec835a")}


def _register_fonts():
    global _REGISTERED
    if _REGISTERED:
        return
    for name, file in (("Sans", "DejaVuSans.ttf"), ("Sans-Bold", "DejaVuSans-Bold.ttf"),
                       ("Sans-Oblique", "DejaVuSans-Oblique.ttf"), ("Mono", "DejaVuSansMono.ttf")):
        pdfmetrics.registerFont(TTFont(name, str(_FONT_DIR / file)))
    pdfmetrics.registerFontFamily("Sans", normal="Sans", bold="Sans-Bold", italic="Sans-Oblique",
                                  boldItalic="Sans-Bold")
    _REGISTERED = True


def _styles():
    base = dict(fontName="Sans", textColor=INK, leading=13, fontSize=9)
    return {
        "title": ParagraphStyle("title", **{**base, "fontName": "Sans-Bold", "fontSize": 24, "leading": 28}),
        "subtitle": ParagraphStyle("subtitle", **{**base, "fontSize": 11, "textColor": INK_2, "leading": 15}),
        "h1": ParagraphStyle("h1", **{**base, "fontName": "Sans-Bold", "fontSize": 14, "leading": 18,
                                      "spaceBefore": 14, "spaceAfter": 6, "keepWithNext": 1}),
        "h2": ParagraphStyle("h2", **{**base, "fontName": "Sans-Bold", "fontSize": 10.5, "spaceBefore": 8,
                                      "spaceAfter": 3, "keepWithNext": 1}),
        "body": ParagraphStyle("body", **base),
        "small": ParagraphStyle("small", **{**base, "fontSize": 8, "leading": 10.5, "textColor": INK_2}),
        "cell": ParagraphStyle("cell", **{**base, "fontSize": 8, "leading": 10}),
        "cellb": ParagraphStyle("cellb", **{**base, "fontName": "Sans-Bold", "fontSize": 8, "leading": 10}),
        "head": ParagraphStyle("head", **{**base, "fontName": "Sans-Bold", "fontSize": 8, "leading": 10,
                                          "textColor": colors.white}),
        "kpi": ParagraphStyle("kpi", **{**base, "fontName": "Sans-Bold", "fontSize": 20, "leading": 24,
                                        "alignment": TA_CENTER}),
        "kpil": ParagraphStyle("kpil", **{**base, "fontSize": 7.5, "leading": 9, "alignment": TA_CENTER,
                                          "textColor": INK_2}),
        "notice": ParagraphStyle("notice", **{**base, "fontSize": 8.5, "leading": 11.5}),
        "code": ParagraphStyle("code", fontName="Mono", fontSize=6.6, leading=8.1, textColor=INK),
    }


def _p(text, style):
    return Paragraph(escape(str(text)).replace("\n", "<br/>"), style)


def _table(rows, widths, st, header=True, align_right=(), zebra=True):
    data = []
    for r_i, row in enumerate(rows):
        cells = []
        for c_i, cell in enumerate(row):
            if hasattr(cell, "wrap"):
                cells.append(cell)
            else:
                style = st["head"] if (header and r_i == 0) else st["cell"]
                cells.append(_p(cell, style))
        data.append(cells)
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    style = [("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("LINEBELOW", (0, 0), (-1, -1), 0.4, GRID),
             ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
             ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5)]
    if header:
        style.append(("BACKGROUND", (0, 0), (-1, 0), INK))
    if zebra:
        for i in range(1 if header else 0, len(rows)):
            if i % 2 == 0:
                style.append(("BACKGROUND", (0, i), (-1, i), BAND))
    t.setStyle(TableStyle(style))
    return t


def _image(png: bytes, width: float):
    from PIL import Image as PILImage  # pillow is a matplotlib dependency
    w, h = PILImage.open(io.BytesIO(png)).size
    return Image(io.BytesIO(png), width=width, height=width * h / w)


def _status_para(status, st):
    s = ParagraphStyle("st", parent=st["cellb"], textColor=STATUS_COLOR.get(status, INK))
    return Paragraph(STATUS_LABEL.get(status, status.upper()), s)


def _wrap(text, width=104):
    import textwrap
    out = []
    for line in text.splitlines():
        out += textwrap.wrap(line, width, drop_whitespace=False) or [""]
    return "\n".join(out)


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Sans", 7.5)
    canvas.setFillColor(INK_2)
    canvas.drawString(0.7 * inch, 0.45 * inch, "SpecProof - Test Execution Report")
    canvas.drawRightString(letter[0] - 0.7 * inch, 0.45 * inch, f"Page {doc.page}")
    canvas.restoreState()


def build_pdf(run, session_id: str = "") -> bytes:
    _register_fonts()
    ctx = build_context(run, session_id)
    st = _styles()
    W = letter[0] - 1.4 * inch
    s = ctx.summary
    story = []

    # ---- title ---------------------------------------------------------
    story += [_p(ctx.title, st["title"]), Spacer(1, 3), _p(ctx.subtitle, st["subtitle"]), Spacer(1, 10)]
    for note in ctx.notices:
        box = Table([[_p(note, st["notice"])]], colWidths=[W])
        box.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fff4d6")),
                                 ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#c98500")),
                                 ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 5),
                                 ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
        story += [box, Spacer(1, 6)]

    # ---- KPI tiles -----------------------------------------------------
    cov = "n/a" if s["coverage_percent"] is None else f"{s['coverage_percent']:g}%"
    tiles = [("Total tests", str(s["total"]), INK), ("Passed", str(s["passed"]), TILE_COLOR["passed"]),
             ("Failed", str(s["failed"]), TILE_COLOR["failed"]), ("Errors", str(s["error"]), TILE_COLOR["error"]),
             ("Pass rate", f"{s['pass_rate']:g}%", INK), ("Coverage", cov, BLUE)]
    kp = Table([[Paragraph(v, ParagraphStyle("k", parent=st["kpi"], textColor=INK)) for _, v, _ in tiles],
                [_p(lbl, st["kpil"]) for lbl, _, _ in tiles]], colWidths=[W / 6] * 6)
    ts = [("BACKGROUND", (0, 0), (-1, -1), BAND), ("TOPPADDING", (0, 0), (-1, 0), 9),
          ("BOTTOMPADDING", (0, 1), (-1, 1), 8), ("LINEAFTER", (0, 0), (-2, -1), 1.5, colors.white)]
    for i, (_, _, col) in enumerate(tiles):
        ts.append(("LINEABOVE", (i, 0), (i, 0), 3, col))
    kp.setStyle(TableStyle(ts))
    story += [kp, Spacer(1, 8), _p(ctx.verdict, st["body"]), Spacer(1, 6)]

    meta = [[_p(k, st["cellb"]), _p(v, st["cell"])] for k, v in ctx.meta]
    mt = Table(meta, colWidths=[1.5 * inch, W - 1.5 * inch])
    mt.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.4, GRID), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5)]))
    story += [mt]

    # ---- results by category ------------------------------------------
    story += [_p("1. Results by test category", st["h1"]),
              _image(charts.category_chart(run), W * 0.92), Spacer(1, 6),
              _table([CATEGORY_HEADERS] + category_rows(ctx),
                     [1.5 * inch] + [(W - 1.5 * inch) / 5] * 5, st)]

    # ---- coverage ------------------------------------------------------
    story += [_p("2. Code coverage", st["h1"])]
    cov_png = charts.coverage_chart(run)
    if cov_png:
        story += [_image(cov_png, W * 0.92), Spacer(1, 6),
                  _table([COVERAGE_HEADERS] + coverage_rows(ctx),
                         [1.0 * inch, 0.55 * inch, 0.7 * inch, 0.8 * inch, 0.8 * inch, 0.85 * inch,
                          W - 4.7 * inch], st)]
        snips = missed_snippets(ctx)
        if snips:
            story += [_p("Lines of the code under test that no test executed", st["h2"]),
                      Preformatted("\n".join(f"{n:>4}  {t}"[:110] for n, t in snips[:40]), st["code"])]
        else:
            story += [Spacer(1, 4), _p("Every statement of the code under test was executed.", st["body"])]
        story += [Spacer(1, 3),
                  _p(f"Measured with: {run.coverage_tool}. Statement coverage counts executable "
                     "statements (comments, blank lines and docstrings are ignored).", st["small"])]
    else:
        story += [_p("Coverage data was not available for this run (see the runner log in the appendix).",
                     st["body"])]

    # ---- failures ------------------------------------------------------
    bad = failing_tests(ctx)
    story += [_p("3. Failed and errored tests", st["h1"])]
    if not bad:
        story += [_p("No failures. Every test passed.", st["body"])]
    for t in bad:
        rows = [[_p("Test", st["cellb"]), Paragraph(
                    f"<b>{escape(t.label)}</b> {escape(t.title)} ({CATEGORY_LABELS[t.category]})", st["cell"])],
                [_p("Status", st["cellb"]), _status_para(t.status, st)],
                [_p("Requirement", st["cellb"]), _p(t.requirement or "-", st["cell"])],
                [_p("What it checks", st["cellb"]), _p(t.explanation or "-", st["cell"])],
                [_p("Failure detail", st["cellb"]), _p(t.message or "-", st["cell"])]]
        tb = Table(rows, colWidths=[1.1 * inch, W - 1.1 * inch])
        tb.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, STATUS_COLOR.get(t.status, INK)),
                                ("LINEBELOW", (0, 0), (-1, -2), 0.3, GRID), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                ("BACKGROUND", (0, 0), (0, -1), BAND),
                                ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
        story += [KeepTogether([tb, Spacer(1, 6)])]

    # ---- detailed results ---------------------------------------------
    story += [_p("4. Detailed results", st["h1"]),
              _p("Tests run in this order: positive, then negative, then edge cases.", st["small"]),
              Spacer(1, 4)]
    rows = [["ID", "Category", "Test", "Status", "Time", "Covers requirement"]]
    for t in run.tests:
        title = t.title + ("  [manual]" if t.origin == "user" else "")
        rows.append([t.label, CATEGORY_LABELS[t.category], title, _status_para(t.status, st),
                     f"{t.duration_ms:.2f} ms", t.requirement or "-"])
    story += [_table(rows, [0.4 * inch, 0.75 * inch, 2.6 * inch, 0.65 * inch, 0.7 * inch,
                            W - 5.1 * inch], st)]

    story += [_p("5. Execution time", st["h1"]), _image(charts.duration_chart(run), W * 0.95),
              Spacer(1, 4)]
    if s["slowest"]:
        story += [_p(f"Slowest test: {s['slowest'].label} {s['slowest'].title} "
                     f"({s['slowest'].duration_ms:.2f} ms).", st["small"])]

    # ---- what each test explains --------------------------------------
    story += [_p("6. What each test verifies", st["h1"])]
    for t in run.tests:
        story += [KeepTogether([Paragraph(f"<b>{escape(t.label)} - {escape(t.title)}</b>", st["cell"]),
                                _p(t.explanation or "No explanation provided.", st["small"]), Spacer(1, 4)])]

    # ---- appendices -----------------------------------------------------
    story += [PageBreak(), _p("Appendix A - Requirements", st["h1"]),
              Preformatted(_wrap(run.requirements.strip()), st["code"]),
              _p("Appendix B - Code under test", st["h1"]),
              _p("A '!' after the line number marks a line that no test executed.", st["small"]),
              Spacer(1, 3),
              Preformatted("\n".join(numbered_listing(run.code_source, all_missed(ctx))), st["code"]),
              PageBreak(), _p("Appendix C - Generated test file", st["h1"]),
              Preformatted("\n".join(numbered_listing(run.test_source)), st["code"]),
              _p("Appendix D - Test runner log", st["h1"]),
              Preformatted(_wrap(run.log_tail.strip() or "(no output)"), st["code"])]

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=0.7 * inch, rightMargin=0.7 * inch,
                            topMargin=0.7 * inch, bottomMargin=0.75 * inch,
                            title=f"{ctx.title} - {ctx.subtitle}", author="SpecProof")
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()
