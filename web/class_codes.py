"""Class codes: instructor-requested codes that give a class's jobs queue
priority during a set time window (see web/scheduler.py for how priority
jobs are ordered).

Codes are created on the admin page and stored in data/class_codes.json,
which is gitignored -- the repo is public, so codes can't live in it. Times
are stored as UTC ISO strings; the frontend shows them in local time.

A job submitted while its code is active keeps its priority even if the
window closes before the job starts -- the check happens once, at submission.
"""

import json
import os
import random
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_CUSTOM_CODE_RE = re.compile(r"^[A-Za-z0-9-]{4,40}$")
MAX_LABEL_LENGTH = 60


def _normalize(code: str) -> str:
    return code.strip().lower()


def _parse_utc(value: str) -> datetime:
    """Parse an ISO timestamp that carries a timezone, returned in UTC."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Times must include a timezone.")
    return dt.astimezone(timezone.utc)


def code_status(entry: dict, now: Optional[datetime] = None) -> str:
    """'upcoming', 'active', or 'expired'."""
    now = now or datetime.now(timezone.utc)
    if now < _parse_utc(entry["starts_at"]):
        return "upcoming"
    if now >= _parse_utc(entry["ends_at"]):
        return "expired"
    return "active"


class ClassCodeStore:
    def __init__(self, path: Path, words: list[str]) -> None:
        self._path = path
        self._words = words
        self._lock = threading.Lock()
        self._codes: list[dict] = []
        try:
            self._codes = json.loads(path.read_text())
        except FileNotFoundError:
            pass

    def _save(self) -> None:
        # Write-then-rename so a crash mid-write can't leave a truncated file.
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".class_codes.")
        with os.fdopen(fd, "w") as f:
            json.dump(self._codes, f, indent=2)
        os.replace(tmp, self._path)

    def _find(self, code: str) -> Optional[dict]:
        key = _normalize(code)
        return next((c for c in self._codes if _normalize(c["code"]) == key), None)

    def _generate_code(self) -> str:
        while True:
            a, b = random.sample(self._words, 2)
            code = f"{a}-{b}-{random.randint(10, 99)}"
            if self._find(code) is None:
                return code

    def get(self, code: str) -> Optional[dict]:
        with self._lock:
            entry = self._find(code)
            return dict(entry) if entry else None

    def list(self) -> list[dict]:
        now = datetime.now(timezone.utc)
        with self._lock:
            entries = [dict(c, status=code_status(c, now)) for c in self._codes]
        return sorted(entries, key=lambda c: c["starts_at"], reverse=True)

    def active_until(self) -> Optional[datetime]:
        """End of the latest-ending code that's active right now, or None."""
        now = datetime.now(timezone.utc)
        with self._lock:
            ends = [_parse_utc(c["ends_at"]) for c in self._codes if code_status(c, now) == "active"]
        return max(ends, default=None)

    def create(self, label: str, starts_at: str, ends_at: str, code: Optional[str] = None) -> dict:
        """Add a code. Raises ValueError with a user-facing message on bad input."""
        label = (label or "").strip()
        if not label:
            raise ValueError("A label is required (e.g. 'LING 340').")
        if len(label) > MAX_LABEL_LENGTH:
            raise ValueError(f"Label must be {MAX_LABEL_LENGTH} characters or fewer.")
        try:
            start = _parse_utc(starts_at)
            end = _parse_utc(ends_at)
        except (ValueError, TypeError):
            raise ValueError("Start and end must be valid times.")
        if end <= start:
            raise ValueError("The end time must be after the start time.")

        with self._lock:
            if code and code.strip():
                code = code.strip()
                if not _CUSTOM_CODE_RE.match(code):
                    raise ValueError("Custom codes must be 4–40 letters, digits, or hyphens.")
                if self._find(code):
                    raise ValueError("That code already exists.")
            else:
                code = self._generate_code()
            entry = {
                "code": code,
                "label": label,
                "starts_at": start.isoformat(),
                "ends_at": end.isoformat(),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._codes.append(entry)
            self._save()
            return dict(entry)

    def delete(self, code: str) -> bool:
        with self._lock:
            entry = self._find(code)
            if entry is None:
                return False
            self._codes.remove(entry)
            self._save()
            return True
