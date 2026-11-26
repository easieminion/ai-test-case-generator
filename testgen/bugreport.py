"""Bug reports for tests that failed because of a (likely) real defect.

A report is tracker neutral: a title, summary, environment, steps to reproduce, expected
vs actual result, the requirement it violates, the failing test and its error output, and
a suggested severity. It can be exported as Markdown (GitHub, GitLab, Azure DevOps,
Linear, ...) or as JIRA wiki markup, and copied into whatever tracker the team uses.

Two ways to build one:

* `template`  - deterministic, works offline and in the demo mode. Steps are derived from
                the statements of the failing test.
* `ai`        - the LLM rewrites title, summary, steps, expected/actual and severity in
                tester language. Facts that must not be invented (environment, test code,
                error output) always come from the run itself.

Nothing is sent to a tracker; the reviewer decides whether to file it.
"""
from __future__ import annotations

import ast
import re
import textwrap
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from .models import CATEGORY_LABELS, STATUS_ERROR, STATUS_FAILED, RunResult, Session, TestResult

SEVERITIES = ("Critical", "High", "Medium", "Low")
MAX_STEPS = 12

# Messages that mean the TEST is broken (or never ran), not the code under test.
_NOT_A_PRODUCT_BUG = (
    re.compile(r"^Invalid (Python|JavaScript)\b"),
    re.compile(r"^The test did not run"),
    re.compile(r"^Test run timed out"),
    re.compile(r"\b(SyntaxError|IndentationError|ReferenceError)\b"),
    re.compile(r"\bNameError\b"),
    re.compile(r"(module|object) '?solution'? has no attribute|solution\.\w+ is not a function"),
)


@dataclass
class BugReport:
    title: str
    summary: str
    severity: str
    severity_reason: str
    steps: List[str]
    expected: str
    actual: str
    requirement: str
    test_label: str
    test_title: str
    category: str
    test_code: str                    # the failing test exactly as it ran
    error_output: str
    rerun_command: str
    environment: Dict[str, str] = field(default_factory=dict)
    language: str = "python"
    source_file: str = "solution.py"
    test_file: str = "test_solution.py"
    generated_by: str = "template"    # template | ai
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------- eligibility
def reportable_reason(result: TestResult) -> Optional[str]:
    """None if a bug report makes sense for this result, otherwise why not."""
    if result.status not in (STATUS_FAILED, STATUS_ERROR):
        return "The test passed."
    msg = (result.message or "").strip()
    if result.status == STATUS_ERROR:
        for pat in _NOT_A_PRODUCT_BUG:
            if pat.search(msg):
                return ("This looks like a problem with the test itself (syntax, a missing name or "
                        "a run that never started), not with the code under test. Fix the test first.")
    return None


def is_reportable(result: TestResult) -> bool:
    return reportable_reason(result) is None


def find_result(run: RunResult, uid: str) -> TestResult:
    for t in run.tests:
        if t.uid == uid:
            return t
    raise KeyError(f"No result for test {uid} in run {run.run_id}")


# ---------------------------------------------------------------- helpers
def extract_test_block(test_source: str, label: str, language: str) -> str:
    """The comment block + function of one test, cut from the file that actually ran."""
    prefix = "#" if language == "python" else "//"
    lines = test_source.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.startswith(f"{prefix} [{label}] ")), None)
    if start is None:
        return ""
    out = []
    for ln in lines[start + 1:]:
        if ln.startswith(f"{prefix} [") or ln.startswith(f"{prefix} ==="):
            break
        out.append(ln)
    return "\n".join([lines[start]] + out).rstrip()


def _body_of(block: str, language: str) -> str:
    """Just the statements inside the test function."""
    lines = [ln for ln in block.splitlines() if not ln.lstrip().startswith("#" if language == "python" else "//")]
    if language == "python":
        lines = [ln for ln in lines if not re.match(r"\s*(async\s+)?def\s+test_", ln)]
    else:
        lines = [ln for ln in lines if not re.match(r"\s*test\(", ln) and ln.strip() != "});"]
    return textwrap.dedent("\n".join(lines)).strip("\n")


def _statements(body: str, language: str) -> List[str]:
    if language == "python":
        try:
            tree = ast.parse(body)
            src = body.splitlines()
            return ["\n".join(src[n.lineno - 1:n.end_lineno]).strip() for n in tree.body]
        except SyntaxError:
            pass
    stmts, buf, depth = [], [], 0
    for ln in body.splitlines():
        if not ln.strip():
            continue
        buf.append(ln)
        depth += ln.count("(") + ln.count("[") + ln.count("{") - ln.count(")") - ln.count("]") - ln.count("}")
        if depth <= 0:
            stmts.append(textwrap.dedent("\n".join(buf)).strip())
            buf, depth = [], 0
    if buf:
        stmts.append(textwrap.dedent("\n".join(buf)).strip())
    return stmts


def _is_check(stmt: str, language: str) -> bool:
    if language == "python":
        return stmt.startswith("assert ") or stmt.startswith("with pytest.raises")
    return stmt.startswith("assert")


def _inline(code: str, limit: int = 160) -> str:
    flat = " ".join(code.split())
    return flat if len(flat) <= limit else flat[:limit - 3] + "..."


def derive_steps(body: str, language: str, source_file: str) -> List[str]:
    steps = [f"Take the code under test exactly as it was in this run (`{source_file}`) "
             f"and import it as `solution`."]
    stmts = _statements(body, language)
    if not stmts:
        return steps + ["Run the failing test shown below."]
    for st in stmts:
        if len(steps) >= MAX_STEPS:
            steps.append("Continue with the remaining statements of the test shown below.")
            break
        if _is_check(st, language):
            shown = st[len("assert "):] if language == "python" and st.startswith("assert ") else st
            steps.append(f"Check that `{_inline(shown)}`")
        else:
            steps.append(f"Run `{_inline(st)}`")
    steps.append("Observe that the check fails (see Actual result).")
    return steps


def _first_lines(text: str, n: int = 3) -> str:
    lines = [ln.rstrip() for ln in (text or "").strip().splitlines() if ln.strip()]
    lines = [re.sub(r"\s*\[(test_solution\.py|solution\.test\.js)[:\d]*\]\s*$", "", ln) for ln in lines]
    return "\n".join(lines[:n]).strip()


def suggest_severity(result: TestResult) -> tuple:
    if result.status == STATUS_ERROR:
        return "High", "The code under test raised an unexpected error instead of returning a result."
    if result.category == "positive":
        return "High", "A valid, everyday input (happy path) does not work as specified."
    if result.category == "negative":
        return "Medium", "Invalid input is not handled as the requirements state."
    return "Medium", "A boundary or unusual input behaves differently from the requirements."


def _environment(session: Session, run: RunResult) -> Dict[str, str]:
    code_by = "supplied by the reviewer" if run.code_origin == "user" else (
        "reference implementation written by the LLM" if run.provider != "offline-demo" else
        "bundled demo implementation")
    env = {
        "Language": {"python": "Python", "javascript": "JavaScript"}.get(run.language, run.language),
        "Test runner": run.runner,
        "Code under test": code_by,
        "Run": f"{run.run_id} at {run.started_at}",
        "Session": session.id,
    }
    if run.provider:
        env["Tests generated by"] = run.provider + (f" ({run.model})" if run.model else "")
    return env


# ---------------------------------------------------------------- build
def build_template(session: Session, run: RunResult, result: TestResult) -> BugReport:
    lang = run.language
    source_file = "solution.py" if lang == "python" else "solution.js"
    test_file = "test_solution.py" if lang == "python" else "solution.test.js"
    block = extract_test_block(run.test_source, result.label, lang)
    body = _body_of(block, lang) if block else ""
    requirement = result.requirement or "Not linked to a requirement"
    cat = CATEGORY_LABELS.get(result.category, result.category)
    actual_msg = _first_lines(result.message) or "The test failed without a message."
    sev, why = suggest_severity(result)

    if lang == "python":
        fn = re.search(r"def (test_\w+)\(", block or "")
        rerun = f"pytest {test_file} -k {fn.group(1) if fn else result.label.lower()}"
    else:
        rerun = f"node --test --test-name-pattern=\"^{result.label}:\" {test_file}"

    what = "crashed with an unexpected error" if result.status == STATUS_ERROR else "failed"
    summary = (f"Automated {cat.lower()} test {result.label} (\"{result.title}\") {what}"
               + (f", so requirement {result.requirement} is not met." if result.requirement else "."))
    if result.explanation:
        summary += " " + _strip_demo_note(result.explanation)

    expected = _strip_demo_note(result.explanation) or f"The test \"{result.title}\" passes."
    checks = [s for s in _statements(body, lang) if _is_check(s, lang)]
    if checks:
        expected += f"\nThe check `{_inline(checks[-1])}` holds."

    notes = ["Before filing, confirm the test itself matches the requirement; the test was "
             "generated automatically and can be wrong."]
    if run.signature and run.signature != session.signature():
        notes.append("The tests or code changed after this run. The report describes the run, "
                     "not the current state; run again to confirm the bug still occurs.")

    return BugReport(
        title=_title(result),
        summary=summary,
        severity=sev,
        severity_reason=why,
        steps=derive_steps(body, lang, source_file),
        expected=expected,
        actual=f"The test {result.status}: {actual_msg}",
        requirement=requirement,
        test_label=result.label,
        test_title=result.title,
        category=cat,
        test_code=block or "(test source not available for this run)",
        error_output=(result.message or "").strip(),
        rerun_command=rerun,
        environment=_environment(session, run),
        language=lang,
        source_file=source_file,
        test_file=test_file,
        generated_by="template",
        notes=notes,
    )


def _title(result: TestResult) -> str:
    req = result.requirement.split(" - ")[0].strip() if result.requirement else ""
    base = f"{result.title} (test {result.label} fails)"
    return f"{req}: {base}" if req else base


def _strip_demo_note(text: str) -> str:
    """The bundled demo explains its deliberate bug in brackets; that is not bug-report text."""
    return re.sub(r"\s*\((The demo implementation|NOTE)[^)]*\)", "", text or "").strip()


def _ai_context(session: Session, run: RunResult, draft: BugReport) -> Dict[str, str]:
    return {
        "requirements": run.requirements or session.requirements,
        "language": run.language,
        "code_under_test": run.code_source,
        "test": draft.test_code,
        "error_output": draft.error_output,
        "category": draft.category,
        "requirement": draft.requirement,
    }


def build(session: Session, run: RunResult, uid: str, provider=None, use_ai: bool = True) -> BugReport:
    """Build a report for test `uid` in `run`. Falls back to the template when AI is not available."""
    result = find_result(run, uid)
    why_not = reportable_reason(result)
    if why_not:
        raise ValueError(why_not)
    report = build_template(session, run, result)
    if not use_ai or provider is None:
        return report
    if getattr(provider, "is_demo", False):
        report.notes.append("Written from a template (offline demo mode has no AI). "
                            "Set ANTHROPIC_API_KEY for an AI-written report.")
        return report
    from .llm import LLMError  # local import: llm imports models, keep this module light
    try:
        ai = provider.write_bug_report(_ai_context(session, run, report))
    except LLMError as exc:
        report.notes.append(f"AI write-up unavailable ({exc}); this report uses the template.")
        return report
    return merge_ai(report, ai)


def merge_ai(report: BugReport, ai: Dict[str, Any]) -> BugReport:
    """Take the LLM's wording, keep every fact that comes from the run."""
    def text(key: str) -> str:
        v = ai.get(key)
        return str(v).strip() if isinstance(v, (str, int, float)) else ""

    steps = [str(s).strip() for s in ai.get("steps") or [] if str(s).strip()]
    report.title = text("title")[:200] or report.title
    report.summary = text("summary") or report.summary
    report.steps = steps[:MAX_STEPS + 2] or report.steps
    report.expected = text("expected") or report.expected
    report.actual = text("actual") or report.actual
    sev = text("severity").capitalize()
    if sev in SEVERITIES:
        report.severity = sev
        report.severity_reason = text("severity_reason") or report.severity_reason
    report.generated_by = "ai"
    return report


# ---------------------------------------------------------------- export
def _md_escape_cell(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def _fence(code: str) -> str:
    longest = max((len(m) for m in re.findall(r"`+", code)), default=0)
    return "`" * max(3, longest + 1)


def to_markdown(r: BugReport, include_source: str = "") -> str:
    lang = "python" if r.language == "python" else "javascript"
    f_test, f_err = _fence(r.test_code), _fence(r.error_output)
    out = [
        f"# {r.title}", "",
        f"**Severity (suggested):** {r.severity} - {r.severity_reason}  ",
        f"**Requirement:** {r.requirement}  ",
        f"**Found by:** {r.category} test {r.test_label} \"{r.test_title}\"", "",
        "## Summary", r.summary, "",
        "## Environment", "| | |", "|---|---|",
        *[f"| {_md_escape_cell(k)} | {_md_escape_cell(v)} |" for k, v in r.environment.items()], "",
        "## Steps to reproduce",
        *[f"{i}. {s}" for i, s in enumerate(r.steps, 1)], "",
        "## Expected result", r.expected, "",
        "## Actual result", r.actual, "",
        "## Failing test", f"{f_test}{lang}", r.test_code, f_test, "",
        f"Re-run it: `{r.rerun_command}`", "",
        "## Error output", f_err, r.error_output or "(none)", f_err, "",
    ]
    if include_source:
        fs = _fence(include_source)
        out += [f"## Code under test (`{r.source_file}`)", f"{fs}{lang}", include_source.rstrip(), fs, ""]
    if r.notes:
        out += ["## Notes", *[f"- {n}" for n in r.notes], ""]
    out.append(f"_Generated by AI Test Case Generator ({'AI write-up' if r.generated_by == 'ai' else 'template'})._")
    return "\n".join(out).rstrip() + "\n"


def _jira_cell(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ") or " "


def _jira_inline(s: str) -> str:
    """Markdown `code` -> JIRA {{code}}."""
    return re.sub(r"`([^`]+)`", r"{{\1}}", s)


def to_jira(r: BugReport, include_source: str = "") -> str:
    lang = "python" if r.language == "python" else "javascript"
    out = [
        f"*Severity (suggested):* {r.severity} - {r.severity_reason}",
        f"*Requirement:* {r.requirement}",
        f"*Found by:* {r.category} test {r.test_label} \"{r.test_title}\"", "",
        "h2. Summary", _jira_inline(r.summary), "",
        "h2. Environment",
        *[f"||{_jira_cell(k)}|{_jira_cell(v)}|" for k, v in r.environment.items()], "",
        "h2. Steps to reproduce",
        *[f"# {_jira_inline(s)}" for s in r.steps], "",
        "h2. Expected result", _jira_inline(r.expected), "",
        "h2. Actual result", _jira_inline(r.actual), "",
        "h2. Failing test", f"{{code:{lang}}}", r.test_code, "{code}",
        f"Re-run it: {{{{{r.rerun_command}}}}}", "",
        "h2. Error output", "{noformat}", r.error_output or "(none)", "{noformat}", "",
    ]
    if include_source:
        out += [f"h2. Code under test ({r.source_file})", f"{{code:{lang}}}", include_source.rstrip(), "{code}", ""]
    if r.notes:
        out += ["h2. Notes", *[f"* {_jira_inline(n)}" for n in r.notes], ""]
    out.append(f"_Generated by AI Test Case Generator ({'AI write-up' if r.generated_by == 'ai' else 'template'})._")
    return "\n".join(out).rstrip() + "\n"


def filename(r: BugReport, ext: str = "md") -> str:
    return f"bug-{r.test_label.lower()}-{re.sub(r'[^a-z0-9]+', '-', r.test_title.lower()).strip('-')[:40]}.{ext}"


def export(session: Session, run: RunResult, report: BugReport, include_source: bool = True) -> Dict[str, Any]:
    src = run.code_source if include_source else ""
    return {"report": report.to_dict(), "markdown": to_markdown(report, src),
            "jira": to_jira(report, src), "filename": filename(report)}
