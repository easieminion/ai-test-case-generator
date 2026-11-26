"""pytest plugin copied into every run directory (loaded with `-p qa_pytest_plugin`).

It does two jobs and writes a single JSON file (QA_RESULTS_PATH):
  1. records the outcome, duration and failure message of every test;
  2. measures *statement coverage* of QA_TARGET_FILE with a tiny built-in line tracer, so
     no extra package (coverage.py / pytest-cov) is required.

Statements are found with `ast` (docstrings excluded), executed lines come from
`sys.settrace` line events and are mapped back to their enclosing statement.
"""
import ast
import json
import os
import platform
import sys
import threading

import pytest

TARGET = os.path.abspath(os.environ["QA_TARGET_FILE"])
OUT = os.environ["QA_RESULTS_PATH"]

_hit_lines = set()
_results = []


def _tracer(frame, event, arg):
    if frame.f_code.co_filename != TARGET:
        return None
    if event == "line":
        _hit_lines.add(frame.f_lineno)
    return _tracer


def _start_tracing():
    sys.settrace(_tracer)
    threading.settrace(_tracer)


def _stop_tracing():
    sys.settrace(None)
    threading.settrace(None)


def _statements(source):
    """Return (executable_start_lines, line -> start_line map)."""
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0]))
    spans = []
    for node in ast.walk(tree):
        if isinstance(node, ast.stmt) and id(node) not in docstrings:
            start = node.lineno
            for dec in getattr(node, "decorator_list", []):
                start = min(start, dec.lineno)
            spans.append((start, node.end_lineno or node.lineno))
    spans.sort()
    nlines = source.count("\n") + 2
    line_to_start = [0] * (nlines + 1)
    for start, end in spans:          # later (inner) spans overwrite the enclosing ones
        for line in range(start, min(end, nlines) + 1):
            line_to_start[line] = start
    return sorted({s for s, _ in spans}), line_to_start


def _coverage():
    try:
        with open(TARGET, encoding="utf-8") as fh:
            source = fh.read()
        starts, line_to_start = _statements(source)
    except (OSError, SyntaxError) as exc:
        return {"error": f"cannot analyse {os.path.basename(TARGET)}: {exc}"}
    executed = set()
    for line in _hit_lines:
        if 0 <= line < len(line_to_start) and line_to_start[line]:
            executed.add(line_to_start[line])
    missed = [s for s in starts if s not in executed]
    return {
        "file": os.path.basename(TARGET),
        "total_statements": len(starts),
        "covered_statements": len(starts) - len(missed),
        "missed_lines": missed,
    }


def pytest_configure(config):
    _start_tracing()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    kind = "error"
    if call.excinfo is not None:
        exc = call.excinfo.value
        if isinstance(exc, (AssertionError, pytest.fail.Exception)):
            kind = "failed"
    rep.qa_kind = kind


def _short_message(rep):
    crash = getattr(getattr(rep, "longrepr", None), "reprcrash", None)
    if crash is not None:
        msg = (crash.message or "").strip()
        loc = f"{os.path.basename(crash.path)}:{crash.lineno}"
        return f"{msg}  [{loc}]"[:1500]
    return str(getattr(rep, "longrepr", ""))[:1500]


def pytest_runtest_logreport(report):
    name = report.nodeid.split("::")[-1]
    if report.when == "call":
        if report.passed:
            status, msg = "passed", ""
        else:
            status, msg = getattr(report, "qa_kind", "error"), _short_message(report)
    elif report.when == "setup" and not report.passed:
        status, msg = "error", _short_message(report) or "setup failed"
    else:
        return
    _results.append({"name": name, "status": status,
                     "duration_ms": round(report.duration * 1000, 3), "message": msg})


def pytest_sessionfinish(session, exitstatus):
    _stop_tracing()
    data = {
        "runner": f"pytest {pytest.__version__} / Python {platform.python_version()}",
        "coverage_tool": "built-in statement tracer (ast + sys.settrace)",
        "tests": _results,
        "coverage": _coverage(),
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
