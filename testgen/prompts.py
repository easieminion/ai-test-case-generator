"""Prompt and tool-schema construction. Kept separate so they are easy to tune."""
from __future__ import annotations

SUITE_TOOL = "submit_test_suite"
DRAFT_TOOL = "submit_test"

_TEST_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {
            "type": "string",
            "enum": ["positive", "negative", "edge"],
            "description": "positive = valid input / happy path; negative = invalid input or "
                           "error conditions; edge = boundaries, empty/extreme/unusual values.",
        },
        "title": {"type": "string", "description": "Short, specific name (max ~10 words)."},
        "explanation": {
            "type": "string",
            "description": "1-3 sentences: what this test verifies and why it matters. "
                           "It is rendered as a code comment above the test.",
        },
        "requirement": {
            "type": "string",
            "description": "Which requirement this covers, e.g. 'R2 - uppercase letter'.",
        },
        "code": {
            "type": "string",
            "description": "BODY ONLY of the test (no def/test() wrapper, no decorator).",
        },
    },
    "required": ["category", "title", "explanation", "requirement", "code"],
}

SUITE_SCHEMA = {
    "type": "object",
    "properties": {
        "implementation": {
            "type": "string",
            "description": "Complete source of module `solution` (ONLY when asked to write it).",
        },
        "tests": {"type": "array", "items": _TEST_ITEM_SCHEMA},
    },
    "required": ["tests"],
}

LANGUAGE_RULES = {
    "python": """\
Language: Python 3 with pytest.
- The test file already contains `import pytest` and `import solution`.
- Reach the code under test as `solution.<name>`.
- Use plain `assert` statements and `pytest.raises(...)` for exceptions.
- Test bodies are plain statements, NOT indented and NOT wrapped in a function; the tool
  wraps each body in `def test_...():` for you.
- If you write the implementation, it is a Python module `solution.py` (top-level
  functions/classes, standard library only).""",
    "javascript": """\
Language: JavaScript (Node 22) with the built-in `node:test` runner.
- The test file already contains `const { test } = require('node:test')`,
  `const assert = require('node:assert/strict')` and `const solution = require('./solution')`.
- Reach the code under test as `solution.<name>`.
- Use `assert.equal`, `assert.deepEqual`, `assert.ok`, `assert.throws`, `assert.rejects`.
- Test bodies are plain statements that run inside an `async () => { ... }`; do NOT write
  the `test(...)` wrapper yourself.
- If you write the implementation, it is a CommonJS module `solution.js` that ends with
  `module.exports = { ... }` (standard library only).""",
}

SYSTEM_PROMPT = """\
You are a senior QA engineer who writes precise, readable, automated tests from written
requirements.

Quality bar
- Every test is independent, deterministic and fast: no network, no file system, no
  randomness, no reliance on the current time, no sleeping.
- Assert on specific, observable behaviour. Never write a test that cannot fail.
- Do not invent behaviour that the requirements do not state. If something is ambiguous,
  test only what is clearly specified.
- Categories:
  * positive - valid input and the expected happy path.
  * negative - invalid input, violated rules, error handling.
  * edge     - boundaries (min, max, min-1, max+1), empty values, very large values,
               unusual characters, repeated calls, and similar corner cases.
- Cover every requirement at least once across the suite; keep titles unique.
- The explanation of each test is plain English (1-3 sentences), not a restatement of
  the code.

Always answer by calling the provided tool. Never reply with prose."""


def build_suite_prompt(requirements: str, language: str, source_code: str, per_category: int) -> str:
    parts = [LANGUAGE_RULES[language], "", "=== REQUIREMENTS ===", requirements.strip(), ""]
    if source_code.strip():
        parts += [
            "=== CODE UNDER TEST (module `solution`) ===",
            source_code.rstrip(),
            "",
            "Write tests against this code's real public API. Do NOT return `implementation`.",
        ]
    else:
        parts += [
            "No code under test was supplied. First write a correct, clean reference "
            "implementation of the requirements as module `solution` and return it in "
            "`implementation`. Then write the tests against that implementation.",
        ]
    parts += [
        "",
        f"Write about {per_category} tests per category (positive, negative, edge); more only if "
        "the requirements clearly need it. Order does not matter, the tool sorts them.",
    ]
    return "\n".join(parts)


def build_draft_prompt(requirements: str, language: str, source_code: str, category: str,
                       description: str) -> str:
    return "\n".join([
        LANGUAGE_RULES[language], "",
        "=== REQUIREMENTS ===", requirements.strip(), "",
        "=== CODE UNDER TEST (module `solution`) ===", source_code.rstrip() or "(not available)", "",
        f"The reviewer found a gap and wants ONE additional {category} test:",
        description.strip(), "",
        "Return exactly one test in `tests`, with category "
        f"`{category}`.",
    ])


# ---------------------------------------------------------------- bug reports
BUG_TOOL = "submit_bug_report"

BUG_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "One-line bug title a developer understands "
                                                   "without context (max ~12 words)."},
        "summary": {"type": "string", "description": "2-4 sentences: what is wrong, for which "
                                                     "input, and which requirement it breaks."},
        "steps": {"type": "array", "items": {"type": "string"},
                  "description": "Numbered steps to reproduce, in plain language with the exact "
                                 "input values. One action per step. No numbering prefixes."},
        "expected": {"type": "string", "description": "Expected result according to the requirement."},
        "actual": {"type": "string", "description": "Actual result, taken from the error output."},
        "severity": {"type": "string", "enum": ["Critical", "High", "Medium", "Low"]},
        "severity_reason": {"type": "string", "description": "One sentence justifying the severity."},
    },
    "required": ["title", "summary", "steps", "expected", "actual", "severity", "severity_reason"],
}

BUG_SYSTEM_PROMPT = """\
You are a senior QA engineer writing a bug report for a developer, from one automated test
that failed. Be factual: use only the requirements, the code, the test and the error output
you are given. Never invent environments, versions, users or screenshots. Write steps that a
person can follow by calling the function with the exact values from the test. If the test
itself might be wrong, still describe the observed behaviour; do not speculate about fixes.

Always answer by calling the provided tool. Never reply with prose."""


def build_bug_prompt(ctx: dict) -> str:
    return "\n".join([
        f"Language: {ctx['language']}", "",
        "=== REQUIREMENTS ===", (ctx.get("requirements") or "").strip(), "",
        "=== CODE UNDER TEST (module `solution`) ===", (ctx.get("code_under_test") or "").rstrip(), "",
        f"=== FAILING {ctx.get('category', '').upper()} TEST (covers: {ctx.get('requirement', '')}) ===",
        ctx.get("test", ""), "",
        "=== ERROR OUTPUT ===", ctx.get("error_output", ""), "",
        "Write the bug report.",
    ])
