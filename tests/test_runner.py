import os
import textwrap

import pytest

from testgen import generator, runner
from testgen.llm import OfflineDemoProvider
from testgen.models import Session, TestCase
from testgen.samples import SAMPLES

PY_CODE = textwrap.dedent('''\
    """Docstring is not a statement."""

    def classify(n):
        if n < 0:
            return "negative"
        if n == 0:
            return "zero"
        return "positive"

    def never_called():
        return 1
    ''')


def py_session(tests, code=PY_CODE):
    s = Session(requirements="R1. classify numbers", language="python", source_code=code, source_origin="user")
    for cat, title, body in tests:
        s.tests.append(TestCase(cat, title, body, explanation="e").normalise())
    return s


def test_python_coverage_numbers_are_exact(tmp_path):
    s = py_session([("positive", "pos", 'assert solution.classify(5) == "positive"')])
    run = runner.run_session(s, base_dir=tmp_path)
    cov = run.coverage[0]
    # statements: def classify, if, return, if, return, return, def never_called, return 1  => 8
    assert cov.total_statements == 8
    # executed: both defs, `if n < 0`, `if n == 0`, `return "positive"`  => 5 ; missed: 2 returns + body of never_called
    assert cov.missed_lines == [5, 7, 11]
    assert cov.covered_statements == 5


def test_full_coverage_when_all_paths_run(tmp_path):
    s = py_session([("positive", "p", 'assert solution.classify(5) == "positive"'),
                    ("negative", "n", 'assert solution.classify(-1) == "negative"'),
                    ("edge", "e", 'assert solution.classify(0) == "zero"'),
                    ("edge", "e2", "assert solution.never_called() == 1")])
    cov = runner.run_session(s, base_dir=tmp_path).coverage[0]
    assert cov.missed_lines == [] and cov.percent == 100.0


def test_failed_vs_error_vs_invalid_are_distinguished(tmp_path):
    s = py_session([("positive", "ok", "assert solution.classify(1) == 'positive'"),
                    ("negative", "assertion", "assert solution.classify(1) == 'zero'"),
                    ("negative", "crash", "solution.does_not_exist()"),
                    ("edge", "raises-expected", "with pytest.raises(TypeError):\n    solution.classify('a')"),
                    ("edge", "syntax", "assert (")])
    run = runner.run_session(s, base_dir=tmp_path)
    by = {t.title: t for t in run.tests}
    assert by["ok"].status == "passed"
    assert by["assertion"].status == "failed"
    assert by["crash"].status == "error" and "does_not_exist" in by["crash"].message
    assert by["raises-expected"].status == "passed"
    assert by["syntax"].status == "error" and "Invalid Python" in by["syntax"].message
    sm = runner.summarise(run)
    assert (sm["passed"], sm["failed"], sm["error"], sm["total"]) == (2, 1, 2, 5)
    assert [t.label for t in run.tests] == ["P1", "N1", "N2", "E1", "E2"]


def test_infinite_loop_times_out_and_everything_is_reported(tmp_path):
    s = py_session([("positive", "hang", "while True:\n    pass")])
    run = runner.run_session(s, base_dir=tmp_path, timeout=3)
    assert run.tests[0].status == "error" and "timed out" in run.tests[0].message


def test_secrets_are_not_passed_to_generated_code(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    s = py_session([("positive", "env", "import os\nassert 'ANTHROPIC_API_KEY' not in os.environ")])
    assert runner.run_session(s, base_dir=tmp_path).tests[0].status == "passed"


def test_import_error_in_code_under_test_is_reported_not_crashed(tmp_path):
    s = py_session([("positive", "p", "assert True")], code="def broken(:\n  pass\n")
    run = runner.run_session(s, base_dir=tmp_path)
    assert run.tests[0].status == "error"


def test_refuses_to_run_without_tests_or_code(tmp_path):
    with pytest.raises(runner.RunnerError):
        runner.run_session(Session(requirements="R1. x", language="python", source_code="x=1"), base_dir=tmp_path)
    with pytest.raises(runner.RunnerError):
        runner.run_session(py_session([("positive", "p", "assert True")], code=""), base_dir=tmp_path)


@pytest.mark.parametrize("language,expected_cov", [("python", 25), ("javascript", 30)])
def test_sample_end_to_end_both_languages(tmp_path, language, expected_cov):
    s = generator.create_session(SAMPLES["password_validator"]["requirements"], language)
    generator.generate(s, OfflineDemoProvider())
    run = runner.run_session(s, base_dir=tmp_path)
    sm = runner.summarise(run)
    cats = sm["categories"]
    assert cats["positive"]["passed"] == cats["positive"]["total"] == 4
    assert cats["negative"]["passed"] == cats["negative"]["total"] == 5
    assert cats["edge"]["total"] == 6 and cats["edge"]["failed"] == 1       # the planted bug is found
    failing = [t for t in run.tests if t.status != "passed"]
    assert [t.title for t in failing] == ["Exactly 64 characters is accepted"]
    assert run.coverage[0].covered_statements == expected_cov
    assert run.coverage[0].missed_lines                                       # describe_errors never called


def test_javascript_error_vs_assertion_and_comment_lines_ignored(tmp_path):
    code = "// comment\n\n/* block\n   comment */\nfunction f(x){\n  return x*2;\n}\nfunction g(){\n  return 1;\n}\nmodule.exports={f,g};\n"
    s = Session(requirements="R1. x", language="javascript", source_code=code, source_origin="user")
    for cat, title, body in (("positive", "ok", "assert.equal(solution.f(2), 4);"),
                             ("negative", "assertion", "assert.equal(solution.f(2), 5);"),
                             ("edge", "crash", "solution.nope();")):
        s.tests.append(TestCase(cat, title, body).normalise())
    run = runner.run_session(s, base_dir=tmp_path)
    assert [t.status for t in run.tests] == ["passed", "failed", "error"]
    cov = run.coverage[0]
    assert cov.total_statements == 7          # 11 lines minus 2 blank/comment lines and the 2 block-comment lines
    assert cov.missed_lines == [8, 9, 10]   # the whole uncalled function g()
