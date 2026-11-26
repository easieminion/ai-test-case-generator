"""Bug reports for real failures: eligibility, template content, AI merge, exports, API, CLI."""
import pytest

import cli
from app import create_app
from testgen import bugreport, generator, runner
from testgen.llm import LLMError, OfflineDemoProvider
from testgen.models import Session, TestCase, TestResult
from testgen.samples import SAMPLES

REQ = SAMPLES["password_validator"]["requirements"]


def sample_run(language="python"):
    s = generator.create_session(REQ, language)
    generator.generate(s, OfflineDemoProvider())
    run = runner.run_session(s, keep_files=False)
    s.runs.append(run)
    failed = next(t for t in run.tests if t.status == "failed")   # E3: deliberate off-by-one
    return s, run, failed


@pytest.fixture(scope="module")
def py():
    return sample_run("python")


def result(status, message, category="edge"):
    return TestResult(uid="t_1", label="E1", category=category, title="x", status=status, message=message)


def test_only_real_failures_are_reportable():
    assert bugreport.is_reportable(result("failed", "AssertionError: assert 1 == 2"))
    assert bugreport.is_reportable(result("error", "TypeError: unsupported operand"))   # crash in the code
    assert not bugreport.is_reportable(result("passed", ""))
    for broken_test in ("Invalid Python (line 1): invalid syntax",
                        "Invalid JavaScript: Unexpected token ')'",
                        "The test did not run (the test file failed to load). See the log below.",
                        "NameError: name 'pasword' is not defined",
                        "AttributeError: module 'solution' has no attribute 'validate'"):
        assert bugreport.reportable_reason(result("error", broken_test)), broken_test


def test_template_report_for_the_sample_bug(py):
    s, run, failed = py
    rep = bugreport.build(s, run, failed.uid, provider=OfflineDemoProvider())
    assert rep.generated_by == "template" and failed.label in rep.title and "R1" in rep.title
    assert rep.severity == "Medium" and rep.requirement == "R1 upper bound"
    # steps replay the test's own statements with the exact input values
    assert any('"x" * 60' in st for st in rep.steps) and rep.steps[-1].startswith("Observe")
    assert "64-character password must be valid" in rep.expected
    assert "too_long" in rep.actual and "too_long" in rep.error_output
    assert "def test_e3_" in rep.test_code and rep.rerun_command.startswith("pytest test_solution.py -k test_e3_")
    assert rep.environment["Language"] == "Python" and rep.environment["Run"].startswith(run.run_id)
    assert "deliberate" not in rep.expected                       # demo-only aside is stripped
    assert any("offline demo" in n for n in rep.notes)


def test_markdown_and_jira_exports(py):
    s, run, failed = py
    out = bugreport.export(s, run, bugreport.build(s, run, failed.uid))
    md, jira = out["markdown"], out["jira"]
    for h in ("## Steps to reproduce", "## Expected result", "## Actual result", "## Environment",
              "## Failing test", "## Error output", "## Code under test"):
        assert h in md
    assert "```python" in md and "1. Take the code under test" in md
    assert "h2. Steps to reproduce" in jira and "{code:python}" in jira and "{noformat}" in jira
    assert "# Take the code under test" in jira and "{{solution.py}}" in jira and "`" not in jira.split("{code")[0]
    assert out["filename"] == "bug-e3-exactly-64-characters-is-accepted.md"
    assert "## Code under test" not in bugreport.export(s, run, bugreport.build(s, run, failed.uid),
                                                     include_source=False)["markdown"]


def test_javascript_report():
    s, run, failed = sample_run("javascript")
    rep = bugreport.build(s, run, failed.uid)
    assert "test(\"E3:" in rep.test_code and "--test-name-pattern" in rep.rerun_command
    assert any("validatePassword" in st for st in rep.steps)
    assert "{code:javascript}" in bugreport.to_jira(rep)


class FakeAI:
    is_demo = False

    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.seen = reply, error, None

    def write_bug_report(self, ctx):
        self.seen = ctx
        if self.error:
            raise self.error
        return self.reply


def test_ai_wording_is_merged_but_facts_come_from_the_run(py):
    s, run, failed = py
    ai = FakeAI({"title": "64-char password rejected as too_long", "summary": "Off by one.",
                 "steps": ["Call validate_password with a 64-character password"], "expected": "valid",
                 "actual": "invalid, too_long", "severity": "high", "severity_reason": "Blocks users",
                 "environment": "made up"})
    rep = bugreport.build(s, run, failed.uid, provider=ai)
    assert rep.generated_by == "ai" and rep.title.startswith("64-char") and rep.severity == "High"
    assert rep.steps == ["Call validate_password with a 64-character password"]
    assert rep.environment["Run"].startswith(run.run_id) and "too_long" in rep.error_output
    assert "too_long" in ai.seen["error_output"] and "validate_password" in ai.seen["code_under_test"]


def test_bad_ai_severity_is_ignored_and_ai_errors_fall_back(py):
    s, run, failed = py
    rep = bugreport.build(s, run, failed.uid, provider=FakeAI({"title": "t", "severity": "Blocker"}))
    assert rep.severity == "Medium" and rep.steps and rep.generated_by == "ai"
    rep = bugreport.build(s, run, failed.uid, provider=FakeAI(error=LLMError("HTTP 529")))
    assert rep.generated_by == "template" and any("529" in n for n in rep.notes)
    assert bugreport.build(s, run, failed.uid, provider=FakeAI(error=AssertionError()), use_ai=False)


def test_passing_test_is_refused(py):
    s, run, _ = py
    passed = next(t for t in run.tests if t.status == "passed")
    with pytest.raises(ValueError, match="passed"):
        bugreport.build(s, run, passed.uid)


def test_stale_run_is_flagged():
    s = Session(requirements="R1. classify numbers", language="python",
                source_code="def f():\n    return 1\n", source_origin="user")
    s.tests.append(TestCase("positive", "returns two", "assert solution.f() == 2", requirement="R1").normalise())
    run = runner.run_session(s, keep_files=False)
    s.tests[0].title = "changed"
    rep = bugreport.build(s, run, s.tests[0].uid)
    assert any("changed after this run" in n for n in rep.notes)
    assert rep.severity == "High"                                   # happy path broken


def test_api_bug_report_endpoint(tmp_path):
    c = create_app(tmp_path).test_client()
    sid = c.post("/api/sessions", json={"requirements": REQ, "language": "python"}).get_json()["session"]["id"]
    assert c.post(f"/api/sessions/{sid}/bug-report", json={"uid": "t_x"}).status_code == 400   # not run yet
    sess = c.post(f"/api/sessions/{sid}/run").get_json()["session"]
    tests = sess["runs"][-1]["tests"]
    failed = next(t for t in tests if t["status"] == "failed")
    assert failed["bug_reportable"] and not any(t["bug_reportable"] for t in tests if t["status"] == "passed")
    r = c.post(f"/api/sessions/{sid}/bug-report?run={sess['runs'][-1]['run_id']}", json={"uid": failed["uid"]})
    assert r.status_code == 200
    d = r.get_json()
    assert d["report"]["test_label"] == failed["label"] and "## Steps to reproduce" in d["markdown"]
    assert "h2. Steps to reproduce" in d["jira"] and d["filename"].endswith(".md")
    passed = next(t for t in tests if t["status"] == "passed")
    assert c.post(f"/api/sessions/{sid}/bug-report", json={"uid": passed["uid"]}).status_code == 400
    assert c.post(f"/api/sessions/{sid}/bug-report", json={"uid": "t_nope"}).status_code == 404
    assert c.post(f"/api/sessions/{sid}/bug-report", json={}).status_code == 400


def test_cli_writes_bug_reports(py, tmp_path, capsys):
    s, run, _ = py
    written = cli.write_bug_reports(s, run, OfflineDemoProvider(), tmp_path / "bugs")
    assert len(written) == 1 and written[0].read_text().startswith("# R1 upper bound: Exactly 64")
    assert written[0].with_suffix(".jira.txt").exists()
