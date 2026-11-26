"""Execute a session's tests (pytest or node:test) and collect results + coverage.

Security note: this runs generated and user-supplied code in a subprocess with a scrubbed
environment (no API keys), a temp working directory and a timeout. That is *not* a
sandbox. Run the tool in a container or VM if you do not trust the inputs.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional

from . import renderer
from .models import (STATUS_ERROR, STATUS_PASSED, FileCoverage, RunResult, Session,
                     TestResult, now_iso)

RUNTIME_DIR = Path(__file__).parent / "runtime"
DEFAULT_TIMEOUT = 120


class RunnerError(RuntimeError):
    """The run could not be performed at all (missing tool, no code, ...)."""


def _clean_env(workdir: Path, extra: Dict[str, str]) -> Dict[str, str]:
    keep = ("PATH", "LANG", "LC_ALL", "SYSTEMROOT", "TEMP", "TMP", "TMPDIR")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env.update({"HOME": str(workdir), "PYTHONDONTWRITEBYTECODE": "1", "NO_COLOR": "1"})
    env.update(extra)
    return env


def _tail(text: str, max_chars: int = 4000) -> str:
    text = text.strip()
    return text if len(text) <= max_chars else "...\n" + text[-max_chars:]


def _js_ignorable_lines(source: str) -> set:
    """Blank lines and comment-only lines, which V8/Node still reports as 'lines'."""
    ignore, in_block = set(), False
    for no, raw in enumerate(source.splitlines(), 1):
        s = raw.strip()
        if in_block:
            ignore.add(no)
            if "*/" in s:
                in_block = False
                if s.split("*/", 1)[1].strip():
                    ignore.discard(no)
            continue
        if not s or s.startswith("//"):
            ignore.add(no)
        elif s.startswith("/*"):
            if "*/" in s:
                if not s.split("*/", 1)[1].strip():
                    ignore.add(no)
            else:
                in_block = True
                ignore.add(no)
    return ignore


def run_session(session: Session, base_dir: Optional[Path] = None, timeout: int = DEFAULT_TIMEOUT,
                keep_files: bool = True) -> RunResult:
    if not session.tests:
        raise RunnerError("There are no tests to run. Generate or add some first.")
    if not session.source_code.strip():
        raise RunnerError("There is no code under test. Supply it or let the AI write it.")

    lang = session.language
    ext_src = "solution.py" if lang == "python" else "solution.js"
    ext_test = "test_solution.py" if lang == "python" else "solution.test.js"

    run_dir_parent = Path(base_dir) if base_dir else Path(tempfile.gettempdir())
    run_dir_parent.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="run_", dir=run_dir_parent)).resolve()

    invalid = renderer.validate(session)
    test_source, names = renderer.render(session, skip=invalid)
    (workdir / ext_src).write_text(session.source_code, encoding="utf-8")
    (workdir / ext_test).write_text(test_source, encoding="utf-8")
    results_path = workdir / "results.json"

    started = now_iso()
    t0 = time.perf_counter()
    log = ""
    timed_out = False
    if lang == "python":
        shutil.copy(RUNTIME_DIR / "qa_pytest_plugin.py", workdir / "qa_pytest_plugin.py")
        cmd = [sys.executable, "-m", "pytest", "-p", "qa_pytest_plugin", "-p", "no:cacheprovider",
               "-o", "addopts=", "-q", "--tb=short", "-W", "ignore", ext_test]
        env = _clean_env(workdir, {"PYTHONPATH": str(workdir),
                                   "QA_TARGET_FILE": str(workdir / ext_src),
                                   "QA_RESULTS_PATH": str(results_path)})
    else:
        if not shutil.which("node"):
            raise RunnerError("Node.js was not found on PATH; it is required for JavaScript tests.")
        shutil.copy(RUNTIME_DIR / "qa_node_reporter.mjs", workdir / "qa_node_reporter.mjs")
        cmd = ["node", "--test", "--experimental-test-coverage",
               "--test-reporter=./qa_node_reporter.mjs", ext_test]
        env = _clean_env(workdir, {"QA_RESULTS_PATH": str(results_path)})

    try:
        proc = subprocess.run(cmd, cwd=workdir, env=env, capture_output=True, text=True,
                              timeout=timeout)
        log = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        raw = (exc.stdout or b"") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        log = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    except FileNotFoundError as exc:
        raise RunnerError(f"Could not start the test runner: {exc}") from exc
    duration = time.perf_counter() - t0

    raw_results: Dict = {}
    if results_path.exists():
        try:
            raw_results = json.loads(results_path.read_text(encoding="utf-8"))
        except ValueError:
            raw_results = {}
    elif lang == "python" and "No module named pytest" in log:
        raise RunnerError("pytest is not installed in this Python environment (pip install pytest).")

    by_name = {t["name"]: t for t in raw_results.get("tests", [])}
    labels = session.labels()
    crash_note = ("Test run timed out after %ds." % timeout) if timed_out else \
        "The test did not run (the test file failed to load). See the log below."
    results: List[TestResult] = []
    for t in session.ordered_tests():
        label = labels[t.uid]
        if t.uid in invalid:
            status, msg, dur = STATUS_ERROR, invalid[t.uid], 0.0
        else:
            key = next((n for n, uid in names.items() if uid == t.uid), None)
            got = by_name.get(key)
            if got is None:
                status, msg, dur = STATUS_ERROR, crash_note, 0.0
            else:
                status, msg, dur = got["status"], got.get("message", ""), got.get("duration_ms", 0.0)
        results.append(TestResult(uid=t.uid, label=label, category=t.category, title=t.title,
                                  status=status, duration_ms=dur, message=msg,
                                  explanation=t.explanation, requirement=t.requirement,
                                  origin=t.origin))

    coverage = _parse_coverage(lang, raw_results.get("coverage"), session.source_code, ext_src)
    run = RunResult(
        started_at=started, duration_s=round(duration, 2), language=lang,
        runner=raw_results.get("runner") or ("pytest" if lang == "python" else "node:test"),
        coverage_tool=raw_results.get("coverage_tool") or "V8 coverage via node:test",
        tests=results, coverage=coverage, log_tail=_tail(log),
        requirements=session.requirements, code_source=session.source_code,
        code_origin=session.source_origin, test_source=test_source,
        provider=session.provider, model=session.model, signature=session.signature(),
    )
    if not keep_files:
        shutil.rmtree(workdir, ignore_errors=True)
    return run


def _parse_coverage(lang: str, raw, source: str, filename: str) -> List[FileCoverage]:
    if not raw:
        return []
    if lang == "python":
        if "error" in raw:
            return []
        return [FileCoverage(file=raw["file"], total_statements=raw["total_statements"],
                             covered_statements=raw["covered_statements"],
                             missed_lines=raw["missed_lines"])]
    ignore = _js_ignorable_lines(source)
    for f in raw.get("files", []):
        if Path(f.get("path", "")).name != filename:
            continue
        counted = [(ln["line"], ln["count"]) for ln in f.get("lines", []) if ln["line"] not in ignore]
        missed = [no for no, count in counted if count == 0]
        return [FileCoverage(
            file=filename, total_statements=len(counted),
            covered_statements=len(counted) - len(missed), missed_lines=missed,
            total_branches=f.get("totalBranchCount"), covered_branches=f.get("coveredBranchCount"),
            total_functions=f.get("totalFunctionCount"), covered_functions=f.get("coveredFunctionCount"))]
    return []


def summarise(run: RunResult) -> Dict:
    """Aggregate numbers used by every report format and the UI."""
    from .models import CATEGORY_ORDER
    cats = {}
    for c in CATEGORY_ORDER:
        group = [t for t in run.tests if t.category == c]
        passed = sum(t.status == "passed" for t in group)
        failed = sum(t.status == "failed" for t in group)
        errors = sum(t.status == "error" for t in group)
        cats[c] = {"total": len(group), "passed": passed, "failed": failed, "error": errors,
                   "pass_rate": round(100.0 * passed / len(group), 1) if group else None}
    total = len(run.tests)
    passed = sum(v["passed"] for v in cats.values())
    cov_total = sum(c.total_statements for c in run.coverage)
    cov_cov = sum(c.covered_statements for c in run.coverage)
    return {
        "categories": cats,
        "total": total,
        "passed": passed,
        "failed": sum(v["failed"] for v in cats.values()),
        "error": sum(v["error"] for v in cats.values()),
        "pass_rate": round(100.0 * passed / total, 1) if total else 0.0,
        "coverage_percent": round(100.0 * cov_cov / cov_total, 1) if cov_total else None,
        "slowest": max(run.tests, key=lambda t: t.duration_ms) if run.tests else None,
        "total_test_time_ms": round(sum(t.duration_ms for t in run.tests), 2),
    }
