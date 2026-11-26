"""Tiny JSON-file persistence for sessions (one file per session)."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import List

from .models import Session

DEFAULT_DATA_DIR = Path(os.environ.get("QA_DATA_DIR", Path.cwd() / "data"))
_ID = re.compile(r"^s_[0-9a-f]{8}$")


class SessionStore:
    def __init__(self, base: Path = DEFAULT_DATA_DIR):
        self.base = Path(base)
        (self.base / "sessions").mkdir(parents=True, exist_ok=True)

    def runs_dir(self, session_id: str) -> Path:
        self._check(session_id)
        return self.base / "runs" / session_id

    def reports_dir(self) -> Path:
        d = self.base / "reports"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def _check(session_id: str) -> None:
        if not _ID.match(session_id):
            raise KeyError("Invalid session id")

    def _path(self, session_id: str) -> Path:
        self._check(session_id)
        return self.base / "sessions" / f"{session_id}.json"

    def save(self, session: Session) -> None:
        tmp = self._path(session.id).with_suffix(".tmp")
        tmp.write_text(json.dumps(session.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(self._path(session.id))

    def load(self, session_id: str) -> Session:
        path = self._path(session_id)
        if not path.exists():
            raise KeyError("Session not found")
        return Session.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list(self) -> List[dict]:
        out = []
        for p in sorted((self.base / "sessions").glob("s_*.json"), key=lambda p: p.stat().st_mtime,
                        reverse=True):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                out.append({"id": d["id"], "language": d["language"], "updated_at": d["updated_at"],
                            "tests": len(d.get("tests", [])), "runs": len(d.get("runs", [])),
                            "title": d["requirements"].strip().splitlines()[0][:80]})
            except (ValueError, KeyError):
                continue
        return out
