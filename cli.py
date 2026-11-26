#!/usr/bin/env python3
"""Command-line version of the workflow.

Interactive:      python cli.py
Scripted / CI:    python cli.py -r requirements.md -l python -s mycode.py -f pdf --yes
Try the sample:   python cli.py --sample --yes
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from testgen import generator, renderer, runner
from testgen.llm import LLMError, get_provider
from testgen.models import CATEGORY_LABELS, LANGUAGES, Session
from testgen.reporting import DEFAULT_FORMAT, FORMATS, build_report, normalise_format
from testgen.samples import SAMPLES
from testgen.store import SessionStore


# ----------------------------------------------------------------- small helpers
def ask(prompt: str, default: str = "") -> str:
    try:
        ans = input(f"{prompt}" + (f" [{default}]" if default else "") + ": ").strip()
    except EOFError:
        return default
    return ans or default


def read_multiline(prompt: str) -> str:
    print(f"{prompt} (finish with a line containing only a dot '.'):")
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip() == ".":
            break
        lines.append(line)
    return "\n".join(lines)


def edit_text(initial: str, prompt: str) -> str:
    """Open $EDITOR when interactive, otherwise read lines from stdin."""
    editor = os.environ.get("EDITOR")
    if editor and sys.stdin.isatty():
        with tempfile.NamedTemporaryFile("w+", suffix=".md", delete=False, encoding="utf-8") as fh:
            fh.write(initial)
            path = fh.name
        try:
            subprocess.call([*editor.split(), path])
            return Path(path).read_text(encoding="utf-8")
        finally:
            os.unlink(path)
    if initial:
        print("--- current text ---\n" + initial + "\n--------------------")
    return read_multiline(prompt)


def show_tests(s: Session) -> None:
    labels = s.labels()
    print()
    for cat in ("positive", "negative", "edge"):
        group = [t for t in s.ordered_tests() if t.category == cat]
        print(f"  {CATEGORY_LABELS[cat].upper()} ({len(group)})")
        for t in group:
            tag = " [manual]" if t.origin == "user" else ""
            req = f"  <{t.requirement}>" if t.requirement else ""
            print(f"    {labels[t.uid]:<4}{t.title}{req}{tag}")
    print()


def show_run(run) -> None:
    sm = runner.summarise(run)
    print(f"\n{run.runner} | coverage: {run.coverage_tool} | {run.duration_s}s\n")
    print(f"  {'Category':<11}{'Total':>6}{'Passed':>8}{'Failed':>8}{'Error':>7}{'Rate':>8}")
    for cat, v in sm["categories"].items():
        rate = "-" if v["pass_rate"] is None else f"{v['pass_rate']:g}%"
        print(f"  {CATEGORY_LABELS[cat]:<11}{v['total']:>6}{v['passed']:>8}{v['failed']:>8}{v['error']:>7}{rate:>8}")
    print(f"  {'All':<11}{sm['total']:>6}{sm['passed']:>8}{sm['failed']:>8}{sm['error']:>7}{sm['pass_rate']:>7g}%")
    for c in run.coverage:
        print(f"\n  Coverage {c.file}: {c.percent:g}% ({c.covered_statements}/{c.total_statements} statements)"
              + (f", missed lines: {', '.join(map(str, c.missed_lines))}" if c.missed_lines else ""))
    for t in run.tests:
        if t.status != "passed":
            first = (t.message or "").strip().splitlines()[0] if t.message else ""
            print(f"  ! {t.label} {t.status.upper()}: {t.title} -- {first[:110]}")
    print()


# ----------------------------------------------------------------- main flow
def review_loop(s: Session, provider, store: SessionStore) -> None:
    while True:
        store.save(s)
        choice = ask("[e]dit requirements  [a]dd test  [d]elete test  [v]iew test file  [r]un tests  [q]uit", "r").lower()
        try:
            if choice.startswith("e"):
                text = edit_text(s.requirements, "New requirements")
                keep = ask("Keep your manually added tests? (y/n)", "y").lower().startswith("y")
                warnings = generator.update_requirements(s, text, provider, True, keep)
                print("\n".join("  note: " + w for w in warnings))
                show_tests(s)
            elif choice.startswith("a"):
                cat = ask("Category (positive/negative/edge)", "positive")
                title = ask("Title")
                req = ask("Requirement it covers (optional)")
                expl = ask("Explanation (becomes a comment)")
                code = read_multiline("Test body only (use solution.<name>)")
                generator.add_test(s, cat, title, code, expl, req)
                show_tests(s)
            elif choice.startswith("d"):
                show_tests(s)
                label = ask("Label to delete (e.g. N2)").upper()
                uid = next((u for u, lab in s.labels().items() if lab == label), None)
                if not uid:
                    print("  no such test")
                else:
                    generator.delete_test(s, uid)
                    show_tests(s)
            elif choice.startswith("v"):
                print(renderer.render(s)[0])
            elif choice.startswith("q"):
                print(f"Session saved as {s.id}")
                sys.exit(0)
            else:
                return
        except (ValueError, LLMError, KeyError) as exc:
            print(f"  error: {exc}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AI test case generator with automated execution")
    ap.add_argument("-r", "--requirements", help="file with the requirements")
    ap.add_argument("--text", help="requirements given inline")
    ap.add_argument("--sample", action="store_true", help="use the bundled password-validator sample")
    ap.add_argument("-l", "--language", choices=LANGUAGES, help="language of the generated tests")
    ap.add_argument("-s", "--source", help="file with the code under test (otherwise the LLM writes it)")
    ap.add_argument("-n", "--per-category", type=int, default=4, help="tests per category")
    ap.add_argument("-f", "--format", help=f"report type: {', '.join(FORMATS)} (asked interactively if omitted)")
    ap.add_argument("-o", "--output-dir", default="reports", help="where to write the report")
    ap.add_argument("--provider", help="auto | anthropic | demo")
    ap.add_argument("-y", "--yes", action="store_true", help="no prompts: generate, run, report")
    ap.add_argument("--fail-on-test-failure", action="store_true", help="exit 1 if any test fails (for CI)")
    ap.add_argument("--data-dir", default=None)
    args = ap.parse_args(argv)

    interactive = not args.yes
    store = SessionStore(Path(args.data_dir)) if args.data_dir else SessionStore()
    try:
        provider = get_provider(args.provider)
    except LLMError as exc:
        print(f"error: {exc}")
        return 2
    d = provider.describe()
    print(f"LLM provider: {d['name']}" + (f" ({d['model']})" if d["model"] else "")
          + ("  -- canned demo data, not AI" if d["demo"] else ""))

    # 1. requirements ---------------------------------------------------
    if args.sample:
        requirements = SAMPLES["password_validator"]["requirements"]
    elif args.requirements:
        requirements = Path(args.requirements).read_text(encoding="utf-8")
    elif args.text:
        requirements = args.text
    else:
        requirements = edit_text("", "Enter the requirements")
    language = args.language or (ask("Test language (python/javascript)", "python").lower() if interactive else "python")
    source = Path(args.source).read_text(encoding="utf-8") if args.source else ""

    # 2-3. generate -------------------------------------------------------
    try:
        s = generator.create_session(requirements, language, source, args.per_category)
        print("Generating tests ...")
        for w in generator.generate(s, provider, keep_user_tests=False):
            print("  note:", w)
    except (ValueError, LLMError) as exc:
        print(f"error: {exc}")
        return 2
    show_tests(s)

    # user review (update requirements / add tests) -----------------------
    if interactive:
        review_loop(s, provider, store)

    # 4. run -----------------------------------------------------------
    print("Running tests with coverage ...")
    try:
        run = runner.run_session(s, base_dir=store.runs_dir(s.id))
    except runner.RunnerError as exc:
        print(f"error: {exc}")
        return 2
    s.runs.append(run)
    store.save(s)
    show_run(run)

    # 5. report ----------------------------------------------------------
    fmt = args.format
    if not fmt:
        fmt = ask(f"Report document type ({'/'.join(FORMATS)})", DEFAULT_FORMAT) if interactive else DEFAULT_FORMAT
    try:
        fmt = normalise_format(fmt)
    except ValueError as exc:
        print(f"{exc}; using {DEFAULT_FORMAT}")
        fmt = DEFAULT_FORMAT
    data, name, _ = build_report(run, fmt, s.id)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / name).write_bytes(data)
    print(f"Report written: {out / name}   (session {s.id})")

    failed = any(t.status != "passed" for t in run.tests)
    return 1 if (failed and args.fail_on_test_failure) else 0


if __name__ == "__main__":
    sys.exit(main())
