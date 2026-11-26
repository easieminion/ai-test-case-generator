"""Matplotlib charts used by the PDF, DOCX and HTML reports and by the web UI.

Colour choices follow a status palette: passed = green, failed = red, error = orange.
Meaning is never carried by colour alone - every segment is labelled with its count and the
legend spells out the status names.
"""
from __future__ import annotations

import io
from typing import Dict, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from ..models import CATEGORY_LABELS, CATEGORY_ORDER, RunResult  # noqa: E402
from ..runner import summarise  # noqa: E402

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e1e0d9"
STATUS = {  # fill, ink used for a label sitting on that fill
    "passed": ("#0ca30c", "#0b0b0b"),
    "failed": ("#d03b3b", "#ffffff"),
    "error": ("#ec835a", "#0b0b0b"),
}
STATUS_NAMES = {"passed": "Passed", "failed": "Failed", "error": "Error"}
BLUE = "#2a78d6"
DPI = 200


def _fig(w, h):
    fig, ax = plt.subplots(figsize=(w, h), dpi=DPI)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=8, length=0)
    return fig, ax


def _png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor(), bbox_inches="tight", pad_inches=0.15)
    plt.close(fig)
    return buf.getvalue()


def category_chart(run: RunResult) -> bytes:
    """Passed / failed / error counts for Positive, Negative and Edge-case tests."""
    s = summarise(run)["categories"]
    fig, ax = _fig(6.8, 3.5)
    xs = range(len(CATEGORY_ORDER))
    top = max([s[c]["total"] for c in CATEGORY_ORDER] + [1])
    for i, cat in zip(xs, CATEGORY_ORDER):
        bottom = 0
        for status in ("passed", "failed", "error"):
            n = s[cat][status]
            if not n:
                continue
            fill, ink = STATUS[status]
            ax.bar(i, n, bottom=bottom, width=0.46, color=fill, edgecolor=SURFACE, linewidth=1.5)
            ax.text(i, bottom + n / 2, str(n), ha="center", va="center", fontsize=10,
                    fontweight="bold", color=ink)
            bottom += n
        total = s[cat]["total"]
        rate = s[cat]["pass_rate"]
        label = f"{s[cat]['passed']}/{total} passed" + (f"  ({rate:g}%)" if rate is not None else "")
        ax.text(i, total + top * 0.04, label, ha="center", va="bottom", fontsize=9, color=INK,
                fontweight="bold")
    ax.set_xticks(list(xs))
    ax.set_xticklabels([CATEGORY_LABELS[c] for c in CATEGORY_ORDER], fontsize=10, color=INK)
    ax.set_ylim(0, top * 1.28)
    ax.set_ylabel("Number of tests", fontsize=8, color=INK_2)
    ax.yaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True))
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    handles = [plt.Rectangle((0, 0), 1, 1, color=STATUS[k][0]) for k in STATUS]
    ax.legend(handles, [STATUS_NAMES[k] for k in STATUS], ncol=3, loc="upper center",
              bbox_to_anchor=(0.5, -0.1), frameon=False, fontsize=8, labelcolor=INK_2)
    ax.set_title("Test outcome by category", loc="left", fontsize=11, color=INK, fontweight="bold", pad=10)
    return _png(fig)


def coverage_chart(run: RunResult) -> Optional[bytes]:
    if not run.coverage:
        return None
    fig, ax = _fig(6.8, 0.9 + 0.55 * len(run.coverage))
    for i, cov in enumerate(run.coverage):
        covered, missed = cov.covered_statements, cov.total_statements - cov.covered_statements
        ax.barh(i, covered, height=0.38, color=BLUE, edgecolor=SURFACE, linewidth=1.5)
        if missed:
            ax.barh(i, missed, left=covered, height=0.38, color=GRID, edgecolor=SURFACE, linewidth=1.5)
        ax.text(cov.total_statements * 1.02, i, f"{cov.percent:g}%  ({covered}/{cov.total_statements})",
                va="center", fontsize=9, color=INK, fontweight="bold")
    ax.set_yticks(range(len(run.coverage)))
    ax.set_yticklabels([c.file for c in run.coverage], fontsize=9, color=INK)
    ax.set_xlim(0, max(c.total_statements for c in run.coverage) * 1.32)
    ax.invert_yaxis()
    ax.xaxis.set_visible(False)
    ax.spines["bottom"].set_visible(False)
    handles = [plt.Rectangle((0, 0), 1, 1, color=BLUE), plt.Rectangle((0, 0), 1, 1, color=GRID)]
    ax.legend(handles, ["Covered", "Not covered"], ncol=2, loc="upper left",
              bbox_to_anchor=(0, -0.05), frameon=False, fontsize=8, labelcolor=INK_2)
    ax.set_title("Statement coverage of the code under test", loc="left", fontsize=11, color=INK,
                 fontweight="bold", pad=8)
    return _png(fig)


def duration_chart(run: RunResult) -> Optional[bytes]:
    tests = run.tests
    if not tests:
        return None
    fig, ax = _fig(6.8, 0.9 + 0.24 * len(tests))
    top = max(t.duration_ms for t in tests) or 1.0
    for i, t in enumerate(tests):
        ax.barh(i, t.duration_ms, height=0.5, color=STATUS.get(t.status, STATUS["error"])[0],
                edgecolor=SURFACE, linewidth=1)
        ax.text(t.duration_ms + top * 0.015, i, f"{t.duration_ms:.2f} ms", va="center", fontsize=7,
                color=INK_2)
    labels = [f"{t.label}  {t.title}" if len(t.title) <= 40 else f"{t.label}  {t.title[:39]}..."
              for t in tests]
    ax.set_yticks(range(len(tests)))
    ax.set_yticklabels(labels, fontsize=7, color=INK)
    ax.set_xlim(0, top * 1.18)
    ax.invert_yaxis()
    ax.xaxis.set_visible(False)
    ax.spines["bottom"].set_visible(False)
    handles = [plt.Rectangle((0, 0), 1, 1, color=STATUS[k][0]) for k in STATUS]
    ax.legend(handles, [STATUS_NAMES[k] for k in STATUS], ncol=3, loc="upper left",
              bbox_to_anchor=(0, -0.02), frameon=False, fontsize=8, labelcolor=INK_2)
    ax.set_title("Execution time per test (run order: positive, negative, edge)", loc="left",
                 fontsize=11, color=INK, fontweight="bold", pad=8)
    return _png(fig)


def all_charts(run: RunResult) -> Dict[str, Optional[bytes]]:
    return {"categories": category_chart(run), "coverage": coverage_chart(run),
            "durations": duration_chart(run)}
