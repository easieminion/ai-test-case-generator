"""Data models shared by the generator, runner, reports and web UI.

Everything is a plain dataclass that round-trips through JSON so a session can be
saved to disk, sent to the browser, and reloaded later.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

LANGUAGES = ("python", "javascript")

# The order tests must appear in: Positive -> Negative -> Edge case.
CATEGORY_ORDER = ("positive", "negative", "edge")
CATEGORY_LABELS = {"positive": "Positive", "negative": "Negative", "edge": "Edge case"}
CATEGORY_PREFIX = {"positive": "P", "negative": "N", "edge": "E"}

STATUS_PASSED = "passed"
STATUS_FAILED = "failed"   # an assertion did not hold
STATUS_ERROR = "error"     # crashed, invalid code, or never ran


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def new_id(prefix: str = "") -> str:
    return prefix + uuid.uuid4().hex[:8]


@dataclass
class TestCase:
    """One test. `code` is the *body only*; the renderer wraps it in a function."""

    __test__ = False  # stop pytest from trying to collect this class

    category: str                       # positive | negative | edge
    title: str
    code: str
    explanation: str = ""               # rendered as a comment above the test
    requirement: str = ""               # which requirement this test covers
    origin: str = "ai"                  # ai | user
    uid: str = field(default_factory=lambda: new_id("t_"))

    def normalise(self) -> "TestCase":
        self.category = (self.category or "positive").strip().lower()
        if self.category in ("edge case", "edge-case", "edgecase", "boundary"):
            self.category = "edge"
        if self.category not in CATEGORY_ORDER:
            raise ValueError(f"Unknown category {self.category!r}; use positive, negative or edge")
        self.title = (self.title or "").strip() or "Untitled test"
        self.code = (self.code or "").rstrip()
        self.explanation = (self.explanation or "").strip()
        self.requirement = (self.requirement or "").strip()
        if not self.code.strip():
            raise ValueError("A test needs code in its body")
        return self


@dataclass
class TestResult:
    __test__ = False

    uid: str
    label: str            # P1, N2, E3 ...
    category: str
    title: str
    status: str
    duration_ms: float = 0.0
    message: str = ""     # failure / error detail
    explanation: str = ""
    requirement: str = ""
    origin: str = "ai"


@dataclass
class FileCoverage:
    file: str
    total_statements: int
    covered_statements: int
    missed_lines: List[int] = field(default_factory=list)
    total_branches: Optional[int] = None
    covered_branches: Optional[int] = None
    total_functions: Optional[int] = None
    covered_functions: Optional[int] = None

    @property
    def percent(self) -> float:
        if not self.total_statements:
            return 100.0
        return round(100.0 * self.covered_statements / self.total_statements, 1)


@dataclass
class RunResult:
    started_at: str
    duration_s: float
    language: str
    runner: str                       # e.g. "pytest 9.1.1 / Python 3.13"
    coverage_tool: str                # e.g. "built-in line tracer"
    tests: List[TestResult] = field(default_factory=list)
    coverage: List[FileCoverage] = field(default_factory=list)
    log_tail: str = ""                # last lines of runner output
    run_id: str = field(default_factory=lambda: new_id("r_"))
    # Snapshots so a report always matches exactly what was executed.
    requirements: str = ""
    code_source: str = ""
    code_origin: str = "ai"
    test_source: str = ""
    provider: str = ""
    model: str = ""
    signature: str = ""               # hash of tests + code at run time (detects later edits)


@dataclass
class Session:
    requirements: str
    language: str = "python"
    source_code: str = ""             # code under test supplied by the user ("" => AI writes it)
    source_origin: str = "ai"         # ai | user
    tests: List[TestCase] = field(default_factory=list)
    provider: str = ""                # e.g. "anthropic" / "offline-demo"
    model: str = ""
    tests_per_category: int = 4
    runs: List[RunResult] = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    id: str = field(default_factory=lambda: new_id("s_"))

    # ---- helpers -------------------------------------------------------
    def ordered_tests(self) -> List[TestCase]:
        """Positive first, then negative, then edge. Stable inside a category."""
        rank = {c: i for i, c in enumerate(CATEGORY_ORDER)}
        return sorted(self.tests, key=lambda t: rank[t.category])

    def labels(self) -> Dict[str, str]:
        """uid -> P1/N2/E3 label, based on the ordered position inside the category."""
        counters = {c: 0 for c in CATEGORY_ORDER}
        out: Dict[str, str] = {}
        for t in self.ordered_tests():
            counters[t.category] += 1
            out[t.uid] = f"{CATEGORY_PREFIX[t.category]}{counters[t.category]}"
        return out

    def signature(self) -> str:
        """Content hash of everything that influences a run (tests, code, language)."""
        import hashlib
        import json
        payload = [self.language, self.source_code,
                   [(t.uid, t.category, t.title, t.code, t.explanation) for t in self.ordered_tests()]]
        return hashlib.sha1(json.dumps(payload).encode("utf-8")).hexdigest()[:12]

    def touch(self) -> None:
        self.updated_at = now_iso()

    # ---- (de)serialisation --------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Session":
        d = dict(d)
        tests = [TestCase(**t) for t in d.pop("tests", [])]
        runs = []
        for r in d.pop("runs", []):
            r = dict(r)
            r["tests"] = [TestResult(**t) for t in r.get("tests", [])]
            r["coverage"] = [FileCoverage(**c) for c in r.get("coverage", [])]
            runs.append(RunResult(**r))
        return cls(tests=tests, runs=runs, **d)
