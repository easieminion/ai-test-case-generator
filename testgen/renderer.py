"""Turn the session's TestCase objects into a runnable test file.

Order is always Positive -> Negative -> Edge case. Each test gets a comment block with its
id, requirement and a plain-English explanation.
"""
from __future__ import annotations

import ast
import json
import re
import textwrap
from typing import Dict, Iterable, List, Set, Tuple

from .models import CATEGORY_LABELS, CATEGORY_ORDER, Session, TestCase

BAR = "=" * 72


def slugify(text: str, limit: int = 48) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return (slug or "test")[:limit].rstrip("_")


def _comment_lines(test: TestCase, label: str, prefix: str) -> List[str]:
    lines = [f"{prefix} [{label}] {test.title}"]
    if test.requirement:
        lines.append(f"{prefix} Requirement: {test.requirement}")
    if test.origin == "user":
        lines.append(f"{prefix} Added manually by the reviewer.")
    explanation = " ".join(test.explanation.split()) or "No explanation provided."
    wrapped = textwrap.wrap(explanation, width=84)
    lines.append(f"{prefix} Explanation: {wrapped[0]}")
    lines += [f"{prefix}    {w}" for w in wrapped[1:]]
    return [ln.rstrip() for ln in lines]


def _unwrap_python(code: str) -> str:
    """Accept a pasted full `def test_x():` function by extracting its body."""
    code = textwrap.dedent(code).strip("\n")
    if not re.match(r"(async\s+)?def\s+\w+\s*\(", code):
        return code
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code
    if len(tree.body) == 1 and isinstance(tree.body[0], (ast.FunctionDef, ast.AsyncFunctionDef)):
        fn = tree.body[0]
        lines = code.splitlines()
        body = lines[fn.body[0].lineno - 1: fn.end_lineno]
        return textwrap.dedent("\n".join(body))
    return code


def python_body(test: TestCase) -> str:
    return _unwrap_python(test.code)


def _py_header(session: Session) -> List[str]:
    return [
        '"""Automatically generated tests (AI Test Case Generator).',
        "",
        f"Session {session.id} | language: python | runner: pytest",
        "Order: Positive -> Negative -> Edge case. Each test is explained in a comment.",
        '"""',
        "import pytest  # noqa: F401  (used by some tests)",
        "",
        "import solution  # the code under test",
        "",
    ]


def render_python(session: Session, skip: Iterable[str] = ()) -> Tuple[str, Dict[str, str]]:
    """Return (source, {function_name: uid})."""
    skip = set(skip)
    labels = session.labels()
    out = _py_header(session)
    names: Dict[str, str] = {}
    for cat in CATEGORY_ORDER:
        group = [t for t in session.ordered_tests() if t.category == cat and t.uid not in skip]
        out += ["", f"# {BAR}", f"# {CATEGORY_LABELS[cat].upper()} TESTS ({len(group)})", f"# {BAR}", ""]
        if not group:
            out += ["# (none)", ""]
        for t in group:
            label = labels[t.uid]
            fn = f"test_{label.lower()}_{slugify(t.title)}"
            names[fn] = t.uid
            out += _comment_lines(t, label, "#")
            out.append(f"def {fn}():")
            out.append(textwrap.indent(python_body(t), "    "))
            out += ["", ""]
    return "\n".join(out).rstrip() + "\n", names


def render_javascript(session: Session, skip: Iterable[str] = ()) -> Tuple[str, Dict[str, str]]:
    """Return (source, {test_name: uid})."""
    skip = set(skip)
    labels = session.labels()
    out = [
        "'use strict';",
        "// Automatically generated tests (AI Test Case Generator).",
        f"// Session {session.id} | language: javascript | runner: node:test",
        "// Order: Positive -> Negative -> Edge case. Each test is explained in a comment.",
        "const { test } = require('node:test');",
        "const assert = require('node:assert/strict');",
        "const solution = require('./solution'); // the code under test",
        "",
    ]
    names: Dict[str, str] = {}
    for cat in CATEGORY_ORDER:
        group = [t for t in session.ordered_tests() if t.category == cat and t.uid not in skip]
        out += [f"// {BAR}", f"// {CATEGORY_LABELS[cat].upper()} TESTS ({len(group)})", f"// {BAR}", ""]
        if not group:
            out += ["// (none)", ""]
        for t in group:
            label = labels[t.uid]
            name = f"{label}: {t.title}"
            names[name] = t.uid
            out += _comment_lines(t, label, "//")
            out.append(f"test({json.dumps(name)}, async () => {{")
            out.append(textwrap.indent(textwrap.dedent(t.code).strip("\n"), "  "))
            out += ["});", ""]
    return "\n".join(out).rstrip() + "\n", names


def render(session: Session, skip: Iterable[str] = ()) -> Tuple[str, Dict[str, str]]:
    if session.language == "python":
        return render_python(session, skip)
    if session.language == "javascript":
        return render_javascript(session, skip)
    raise ValueError(f"Unsupported language {session.language!r}")


# ---------------------------------------------------------------- validation
def _validate_python(tests: List[TestCase]) -> Dict[str, str]:
    bad: Dict[str, str] = {}
    for t in tests:
        src = "def _t():\n" + textwrap.indent(python_body(t), "    ") + "\n"
        try:
            ast.parse(src)
        except SyntaxError as exc:
            bad[t.uid] = f"Invalid Python (line {max((exc.lineno or 2) - 1, 1)}): {exc.msg}"
    return bad


_JS_CHECK = r"""
const vm = require('node:vm');
const items = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const bad = {};
for (const it of items) {
  try { new vm.Script('(async () => {\n' + it.code + '\n})', { filename: 'test' }); }
  catch (e) { bad[it.uid] = 'Invalid JavaScript: ' + e.message; }
}
process.stdout.write(JSON.stringify(bad));
"""


def _validate_javascript(tests: List[TestCase], node: str = "node") -> Dict[str, str]:
    import subprocess
    payload = json.dumps([{"uid": t.uid, "code": textwrap.dedent(t.code)} for t in tests])
    try:
        proc = subprocess.run([node, "-e", _JS_CHECK], input=payload, capture_output=True,
                              text=True, timeout=30)
    except FileNotFoundError as exc:
        raise RuntimeError("Node.js was not found on PATH; it is required for JavaScript tests.") from exc
    if proc.returncode != 0:
        raise RuntimeError(f"JavaScript syntax check failed: {proc.stderr.strip()[:300]}")
    return json.loads(proc.stdout or "{}")


def validate(session: Session) -> Dict[str, str]:
    """Return {uid: error_message} for tests whose body does not even parse."""
    if session.language == "python":
        return _validate_python(session.tests)
    return _validate_javascript(session.tests)
