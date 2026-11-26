"""Self-contained HTML report (charts embedded as base64 PNG)."""
from __future__ import annotations

import base64
from html import escape

from ..models import CATEGORY_LABELS
from . import charts
from .common import (CATEGORY_HEADERS, COVERAGE_HEADERS, STATUS_LABEL, all_missed, build_context,
                     category_rows, coverage_rows, failing_tests, missed_snippets, numbered_listing)

CSS = """
:root{--ink:#0b0b0b;--ink2:#52514e;--grid:#e1e0d9;--band:#f4f3ef;--ok:#006300;--bad:#b02a2a;--err:#b4501f}
*{box-sizing:border-box}body{font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:var(--ink);
max-width:980px;margin:0 auto;padding:32px 20px 60px;background:#fff}
h1{font-size:34px;margin:0 0 4px}h2{font-size:21px;margin:36px 0 10px;padding-top:6px;border-top:1px solid var(--grid)}
h3{font-size:15px;margin:18px 0 6px}.sub{color:var(--ink2);margin:0 0 16px}
.notice{background:#fff4d6;border:1px solid #c98500;border-radius:6px;padding:8px 12px;margin:8px 0;font-size:14px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;margin:16px 0}
.kpi{background:var(--band);border-top:4px solid var(--ink);padding:12px 8px;text-align:center;border-radius:2px}
.kpi b{display:block;font-size:28px;line-height:1.1}.kpi span{font-size:12px;color:var(--ink2)}
table{border-collapse:collapse;width:100%;font-size:13.5px;margin:10px 0}
th{background:var(--ink);color:#fff;text-align:left;padding:6px 8px}td{padding:6px 8px;border-bottom:1px solid var(--grid);vertical-align:top}
tbody tr:nth-child(even){background:var(--band)}img{max-width:100%;height:auto;border-radius:4px}
.passed{color:var(--ok);font-weight:700}.failed{color:var(--bad);font-weight:700}.error{color:var(--err);font-weight:700}
pre{background:var(--band);padding:12px;overflow:auto;font:12px/1.45 ui-monospace,Menlo,Consolas,monospace;border-radius:4px}
.meta td:first-child{font-weight:700;width:190px}.fail{border:1px solid var(--bad);border-radius:6px;margin:10px 0;overflow:hidden}
.fail td:first-child{background:var(--band);font-weight:700;width:140px}.small{color:var(--ink2);font-size:13px}
details{margin:10px 0}summary{cursor:pointer;font-weight:700;font-size:16px;padding:6px 0}
@media print{details{display:block}body{padding:0}}
"""


def _img(png: bytes, alt: str) -> str:
    return f'<img alt="{escape(alt)}" src="data:image/png;base64,{base64.b64encode(png).decode()}">'


def _table(headers, rows, status_col=None) -> str:
    out = ["<table><thead><tr>" + "".join(f"<th>{escape(h)}</th>" for h in headers) + "</tr></thead><tbody>"]
    for r in rows:
        cells = []
        for i, c in enumerate(r):
            if status_col is not None and i == status_col:
                cells.append(f'<td class="{escape(str(c))}">{STATUS_LABEL.get(str(c), escape(str(c)))}</td>')
            else:
                cells.append(f"<td>{escape(str(c))}</td>")
        out.append("<tr>" + "".join(cells) + "</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def build_html(run, session_id: str = "") -> str:
    ctx = build_context(run, session_id)
    s = ctx.summary
    cov = "n/a" if s["coverage_percent"] is None else f"{s['coverage_percent']:g}%"
    tiles = [("Total tests", s["total"], "#0b0b0b"), ("Passed", s["passed"], "#0ca30c"),
             ("Failed", s["failed"], "#d03b3b"), ("Errors", s["error"], "#ec835a"),
             ("Pass rate", f"{s['pass_rate']:g}%", "#0b0b0b"), ("Coverage", cov, "#2a78d6")]
    h = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
         f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
         f"<title>{escape(ctx.title)} - {escape(ctx.subtitle)}</title><style>{CSS}</style></head><body>",
         f"<h1>{escape(ctx.title)}</h1><p class='sub'>{escape(ctx.subtitle)}</p>"]
    h += [f"<div class='notice'>{escape(n)}</div>" for n in ctx.notices]
    h.append("<div class='kpis'>" + "".join(
        f"<div class='kpi' style='border-top-color:{c}'><b>{escape(str(v))}</b><span>{escape(l)}</span></div>"
        for l, v, c in tiles) + "</div>")
    h.append(f"<p>{escape(ctx.verdict)}</p>")
    h.append("<table class='meta'><tbody>" + "".join(
        f"<tr><td>{escape(k)}</td><td>{escape(v)}</td></tr>" for k, v in ctx.meta) + "</tbody></table>")

    h.append("<h2>1. Results by test category</h2>")
    h.append(_img(charts.category_chart(run), "Passed, failed and errored tests by category"))
    h.append(_table(CATEGORY_HEADERS, category_rows(ctx)))

    h.append("<h2>2. Code coverage</h2>")
    cov_png = charts.coverage_chart(run)
    if cov_png:
        h.append(_img(cov_png, "Statement coverage"))
        h.append(_table(COVERAGE_HEADERS, coverage_rows(ctx)))
        snips = missed_snippets(ctx)
        if snips:
            h.append("<h3>Lines of the code under test that no test executed</h3><pre>" + escape(
                "\n".join(f"{n:>4}  {t}" for n, t in snips[:60])) + "</pre>")
        else:
            h.append("<p>Every statement of the code under test was executed.</p>")
        h.append(f"<p class='small'>Measured with: {escape(run.coverage_tool)}.</p>")
    else:
        h.append("<p>Coverage data was not available for this run.</p>")

    h.append("<h2>3. Failed and errored tests</h2>")
    bad = failing_tests(ctx)
    if not bad:
        h.append("<p>No failures. Every test passed.</p>")
    for t in bad:
        h.append("<table class='fail'><tbody>"
                 f"<tr><td>Test</td><td><b>{escape(t.label)}</b> {escape(t.title)} ({CATEGORY_LABELS[t.category]})</td></tr>"
                 f"<tr><td>Status</td><td class='{t.status}'>{STATUS_LABEL[t.status]}</td></tr>"
                 f"<tr><td>Requirement</td><td>{escape(t.requirement or '-')}</td></tr>"
                 f"<tr><td>What it checks</td><td>{escape(t.explanation or '-')}</td></tr>"
                 f"<tr><td>Failure detail</td><td><pre style='margin:0'>{escape(t.message or '-')}</pre></td></tr>"
                 "</tbody></table>")

    h.append("<h2>4. Detailed results</h2><p class='small'>Run order: positive, then negative, then edge cases.</p>")
    rows = [[t.label, CATEGORY_LABELS[t.category], t.title + ("  [manual]" if t.origin == "user" else ""),
             t.status, f"{t.duration_ms:.2f} ms", t.requirement or "-"] for t in run.tests]
    h.append(_table(["ID", "Category", "Test", "Status", "Time", "Covers requirement"], rows, status_col=3))

    h.append("<h2>5. Execution time</h2>")
    h.append(_img(charts.duration_chart(run), "Execution time per test"))

    h.append("<h2>6. What each test verifies</h2>")
    for t in run.tests:
        h.append(f"<p><b>{escape(t.label)} - {escape(t.title)}</b><br>"
                 f"<span class='small'>{escape(t.explanation or 'No explanation provided.')}</span></p>")

    h.append("<h2>Appendix</h2>")
    h.append(f"<details><summary>A - Requirements</summary><pre>{escape(run.requirements.strip())}</pre></details>")
    h.append("<details><summary>B - Code under test (! = never executed)</summary><pre>"
             + escape("\n".join(numbered_listing(run.code_source, all_missed(ctx), width=200))) + "</pre></details>")
    h.append("<details><summary>C - Generated test file</summary><pre>"
             + escape("\n".join(numbered_listing(run.test_source, width=200))) + "</pre></details>")
    h.append(f"<details><summary>D - Test runner log</summary><pre>{escape(run.log_tail.strip() or '(no output)')}</pre></details>")
    h.append("</body></html>")
    return "".join(h)
