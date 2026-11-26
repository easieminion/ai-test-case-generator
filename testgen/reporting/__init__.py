"""Report generation. `build_report(run, fmt)` returns (bytes, filename, mimetype)."""
from __future__ import annotations

import re
from typing import Tuple

from ..models import RunResult

FORMATS = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "html": "text/html; charset=utf-8",
}
DEFAULT_FORMAT = "pdf"


def normalise_format(fmt: str | None) -> str:
    fmt = (fmt or DEFAULT_FORMAT).strip().lower().lstrip(".")
    aliases = {"word": "docx", "doc": "docx", "htm": "html", "": DEFAULT_FORMAT}
    fmt = aliases.get(fmt, fmt)
    if fmt not in FORMATS:
        raise ValueError(f"Unsupported report format {fmt!r}. Choose one of: {', '.join(FORMATS)}")
    return fmt


def build_report(run: RunResult, fmt: str | None = None, session_id: str = "") -> Tuple[bytes, str, str]:
    fmt = normalise_format(fmt)
    stamp = re.sub(r"[^0-9]", "", run.started_at)[:14]
    name = f"test-report-{session_id or 'run'}-{stamp}.{fmt}"
    if fmt == "pdf":
        from .pdf import build_pdf
        data = build_pdf(run, session_id)
    elif fmt == "docx":
        from .docx_report import build_docx
        data = build_docx(run, session_id)
    else:
        from .html_report import build_html
        data = build_html(run, session_id).encode("utf-8")
    return data, name, FORMATS[fmt]
