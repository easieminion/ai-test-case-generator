import io
import shutil
import subprocess

import pytest

from testgen import generator, runner
from testgen.llm import OfflineDemoProvider
from testgen.reporting import FORMATS, build_report, normalise_format
from testgen.samples import SAMPLES


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    s = generator.create_session(SAMPLES["password_validator"]["requirements"], "python")
    generator.generate(s, OfflineDemoProvider())
    generator.add_test(s, "edge", "Unicode title: éè 中文", "assert True",
                       "Unicode in titles must not break the report.", "R9")
    return s, runner.run_session(s, base_dir=tmp_path_factory.mktemp("runs"))


def test_default_format_is_pdf():
    assert normalise_format(None) == "pdf" and normalise_format("") == "pdf"
    assert normalise_format("Word") == "docx" and normalise_format(".HTML") == "html"
    with pytest.raises(ValueError):
        normalise_format("exe")
    assert set(FORMATS) == {"pdf", "docx", "html"}


def test_pdf_has_charts_numbers_and_every_test(run):
    s, r = run
    data, name, mime = build_report(r, None, s.id)          # None -> default -> PDF
    assert data.startswith(b"%PDF") and name.endswith(".pdf") and mime == "application/pdf"
    if not shutil.which("pdftotext"):
        pytest.skip("pdftotext not available")
    text = subprocess.run(["pdftotext", "-layout", "-", "-"], input=data, capture_output=True).stdout.decode()
    for needle in ("Test Execution Report", "Results by test category", "Positive", "Negative", "Edge case",
                   "Code coverage", "Failed and errored tests", "Exactly 64 characters is accepted",
                   "Appendix B", "Offline demo mode", "[manual]"):
        assert needle in text, needle
    images = subprocess.run(["pdfimages", "-list", "-"], input=data, capture_output=True).stdout.decode() \
        if shutil.which("pdfimages") else ""
    if images:
        assert len(images.strip().splitlines()) - 2 >= 3     # category, coverage, durations charts


def test_docx_opens_and_contains_sections_tables_and_pictures(run):
    from docx import Document
    s, r = run
    data, name, _ = build_report(r, "docx", s.id)
    doc = Document(io.BytesIO(data))
    headings = [p.text for p in doc.paragraphs if p.style.name.startswith("Heading")]
    assert "1. Results by test category" in headings and "Appendix D - Test runner log" in headings
    assert len(doc.inline_shapes) == 3
    assert len(doc.tables) >= 5
    assert any("Exactly 64 characters" in c.text for t in doc.tables for row in t.rows for c in row.cells)


def test_html_is_self_contained_and_escaped(run):
    s, r = run
    html = build_report(r, "html", s.id)[0].decode()
    assert html.count("data:image/png;base64,") == 3
    assert "<script" not in html and "http://" not in html and "https://" not in html
    r.tests[0].title = "<img src=x onerror=alert(1)>"
    assert "<img src=x onerror" not in build_report(r, "html", s.id)[0].decode()
    r.tests[0].title = "Strong password is accepted"


def test_report_counts_match_run(run):
    from testgen.reporting.common import build_context
    s, r = run
    ctx = build_context(r, s.id)
    assert ctx.summary["total"] == len(r.tests) == 17
    assert "15 of 17 tests passed; 1 failed" in ctx.verdict


def test_all_passing_run_and_no_coverage_still_build(tmp_path):
    s = generator.create_session("R1. Anything goes here for the test", "python", "x = 1", 2)
    generator.add_test(s, "positive", "t", "assert True")
    r = runner.run_session(s, base_dir=tmp_path)
    r.coverage = []                                         # e.g. coverage unavailable
    for fmt in FORMATS:
        data = build_report(r, fmt, s.id)[0]
        assert len(data) > 1000
