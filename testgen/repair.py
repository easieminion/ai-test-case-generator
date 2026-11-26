"""Rule-based syntax repair used by the offline demo provider (NOT AI).

It tries a few common, mechanical fixes (missing colon, unclosed brackets, JS-isms in
Python) and keeps the first candidate that parses. Anything harder needs the real LLM.
"""
from __future__ import annotations

import itertools
import re
from typing import List, Optional

from .models import TestCase
from . import renderer

_PY_BLOCK = re.compile(r"^\s*(if|elif|else|for|while|with|try|except|finally|def|class|async\s+def|async\s+for|async\s+with)\b")
_PAIRS = {"(": ")", "[": "]", "{": "}"}


def _strip_strings(line: str) -> str:
    """Remove string literals and trailing comments so brackets inside them are ignored."""
    line = re.sub(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`(?:\\.|[^`\\])*`", "''", line)
    return re.split(r"\s#|\s//|^#|^//", line, maxsplit=1)[0]


def _close_brackets(code: str) -> str:
    out = []
    for line in code.splitlines():
        stack = []
        for ch in _strip_strings(line):
            if ch in _PAIRS:
                stack.append(_PAIRS[ch])
            elif ch in _PAIRS.values() and stack and stack[-1] == ch:
                stack.pop()
        tail = "".join(reversed(stack))
        if tail and line.rstrip().endswith(";"):
            line = line.rstrip()[:-1] + tail + ";"
        else:
            line = line.rstrip() + tail
        out.append(line)
    return "\n".join(out)


def _add_colons(code: str) -> str:
    return "\n".join(ln.rstrip() + ":" if _PY_BLOCK.match(ln) and not _strip_strings(ln).rstrip().endswith(":")
                     else ln for ln in code.splitlines())


def _pythonise(code: str) -> str:
    out = []
    for ln in code.splitlines():
        ln = re.sub(r"\s*;\s*$", "", ln)
        ln = ln.replace("===", "==").replace("!==", "!=").replace("&&", " and ").replace("||", " or ")
        ln = re.sub(r"\btrue\b", "True", re.sub(r"\bfalse\b", "False", re.sub(r"\bnull\b", "None", ln)))
        out.append(ln)
    return "\n".join(out)


RULES = {
    "python": [(_add_colons, "added a missing ':'"), (_close_brackets, "closed an unclosed bracket"),
               (_pythonise, "replaced JavaScript syntax with Python")],
    "javascript": [(_close_brackets, "closed an unclosed bracket")],
}


def quick_fix(code: str, language: str) -> Optional[tuple]:
    """Return (fixed_code, note) for the smallest rule combination that parses, else None."""
    rules: List[tuple] = RULES.get(language, [])
    candidates = []
    for n in range(1, len(rules) + 1):
        for combo in itertools.combinations(rules, n):
            fixed = code
            for fn, _ in combo:
                fixed = fn(fixed)
            if fixed != code:
                candidates.append((fixed, "; ".join(note for _, note in combo)))
    if not candidates:
        return None
    tests = [TestCase(category="positive", title="c", code=c, uid=f"c{i}") for i, (c, _) in enumerate(candidates)]
    bad = renderer.validate_tests(language, tests)
    for t, cand in zip(tests, candidates):
        if t.uid not in bad:
            return cand
    return None
