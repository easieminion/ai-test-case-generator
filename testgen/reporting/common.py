"""Format-independent report content shared by the PDF, DOCX and HTML builders."""
from __future__ import annotations

import textwrap
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Tuple

from ..models import CATEGORY_LABELS, CATEGORY_ORDER, RunResult
from ..runner import summarise

STATUS_LABEL = {"passed": "PASSED", "failed": "FAILED", "error": "ERROR"}


@dataclass
class ReportContext:
    run: RunResult
    summary: Dict
    title: str
    subtitle: str
    meta: List[Tuple[str, str]]
    verdict: str
    notices: List[str]


def pretty_time(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d %b %Y, %H:%M:%S %Z").strip()
    except ValueError:
        return iso


def build_context(run: RunResult, session_id: str = "") -> ReportContext:
    s = summarise(run)
    first_line = next((ln.strip() for ln in run.requirements.splitlines() if ln.strip()), "Requirements")
    demo = run.provider == "offline-demo"
    llm = f"{run.provider or 'n/a'}" + (f" / {run.model}" if run.model else "")
    meta = [
        ("Run started", pretty_time(run.started_at)),
        ("Session", session_id or "-"),
        ("Language", run.language.capitalize()),
        ("Test runner", run.runner),
        ("Coverage tool", run.coverage_tool),
        ("Test generator", llm + (" (canned demo data, not AI)" if demo else "")),
        ("Code under test", "supplied by the reviewer" if run.code_origin == "user"
         else ("written by the demo provider" if demo else "written by the LLM")),
        ("Wall-clock duration", f"{run.duration_s:.2f} s"),
        ("Summed test time", f"{s['total_test_time_ms']:.2f} ms"),
    ]
    if s["total"] == 0:
        verdict = "No tests were executed."
    elif s["failed"] == 0 and s["error"] == 0:
        verdict = f"All {s['total']} tests passed."
    else:
        parts = []
        if s["failed"]:
            parts.append(f"{s['failed']} failed")
        if s["error"]:
            parts.append(f"{s['error']} errored")
        verdict = f"{s['passed']} of {s['total']} tests passed; " + " and ".join(parts) + "."
    if s["coverage_percent"] is not None:
        verdict += f" Statement coverage of the code under test: {s['coverage_percent']:g}%."
    notices = []
    if demo:
        notices.append("Offline demo mode: the tests and code in this report are canned sample data. "
                       "Set ANTHROPIC_API_KEY to generate tests from your own requirements.")
    if any(t.origin == "user" for t in run.tests):
        n = sum(t.origin == "user" for t in run.tests)
        notices.append(f"{n} test(s) were added or edited manually by the reviewer (marked 'manual').")
    return ReportContext(run=run, summary=s, title="Test Execution Report", subtitle=first_line.replace("`", "")[:140],
                         meta=meta, verdict=verdict, notices=notices)


def category_rows(ctx: ReportContext) -> List[List[str]]:
    rows = []
    for c in CATEGORY_ORDER:
        v = ctx.summary["categories"][c]
        rate = "-" if v["pass_rate"] is None else f"{v['pass_rate']:g}%"
        rows.append([CATEGORY_LABELS[c], str(v["total"]), str(v["passed"]), str(v["failed"]),
                     str(v["error"]), rate])
    s = ctx.summary
    rows.append(["All tests", str(s["total"]), str(s["passed"]), str(s["failed"]), str(s["error"]),
                 f"{s['pass_rate']:g}%"])
    return rows


CATEGORY_HEADERS = ["Category", "Total", "Passed", "Failed", "Error", "Pass rate"]


def coverage_rows(ctx: ReportContext) -> List[List[str]]:
    rows = []
    for c in ctx.run.coverage:
        branches = "-" if c.total_branches is None else f"{c.covered_branches}/{c.total_branches}"
        funcs = "-" if c.total_functions is None else f"{c.covered_functions}/{c.total_functions}"
        rows.append([c.file, str(c.total_statements), str(c.covered_statements),
                     f"{c.percent:g}%", branches, funcs,
                     ", ".join(_ranges(c.missed_lines)) or "none"])
    return rows


COVERAGE_HEADERS = ["File", "Stmts", "Covered", "Coverage", "Branches", "Functions", "Missed lines"]


def _ranges(nums: List[int]) -> List[str]:
    out, nums = [], sorted(nums)
    i = 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(str(nums[i]) if i == j else f"{nums[i]}-{nums[j]}")
        i = j + 1
    return out


def missed_snippets(ctx: ReportContext) -> List[Tuple[int, str]]:
    """(line number, source text) for every line of the code under test that never ran."""
    missed = set()
    for c in ctx.run.coverage:
        missed.update(c.missed_lines)
    lines = ctx.run.code_source.splitlines()
    return [(n, lines[n - 1]) for n in sorted(missed) if 0 < n <= len(lines)]


def numbered_listing(source: str, missed: List[int] | set = (), width: int = 100) -> List[str]:
    """Source with line numbers; lines that never executed are flagged with '!'."""
    missed = set(missed)
    out = []
    for no, line in enumerate(source.splitlines(), 1):
        flag = "!" if no in missed else " "
        chunks = textwrap.wrap(line.expandtabs(4), width, drop_whitespace=False,
                               replace_whitespace=False) or [""]
        out.append(f"{no:>4}{flag} {chunks[0]}")
        out += [f"     {' '}  {c}" for c in chunks[1:]]
    return out


def all_missed(ctx: ReportContext) -> set:
    s = set()
    for c in ctx.run.coverage:
        s.update(c.missed_lines)
    return s


def failing_tests(ctx: ReportContext):
    return [t for t in ctx.run.tests if t.status != "passed"]
