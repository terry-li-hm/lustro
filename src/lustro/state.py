from __future__ import annotations

import contextlib
import fcntl
import json
import os
import sys
import tempfile
from collections.abc import Generator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping


@contextlib.contextmanager
def lockfile(path: Path) -> Generator[None, None, None]:
    """Advisory file lock to prevent concurrent execution."""
    lock_path = path.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_WRONLY)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print(f"Another lustro process is running (lock: {lock_path})", file=sys.stderr)
            raise SystemExit(1)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        with contextlib.suppress(OSError):
            lock_path.unlink()


_CADENCE_DAYS = {
    "daily": 0,
    "twice_weekly": 2,
    "weekly": 5,
    "biweekly": 10,
    "monthly": 25,
}


def load_state(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in data.items()
        if isinstance(key, str) and isinstance(value, str)
    }


def save_state(path: Path, state: Mapping[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(dict(state), indent=2, sort_keys=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(payload)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def should_fetch(
    state: Mapping[str, str],
    source_name: str,
    cadence: str,
    now: datetime | None = None,
) -> bool:
    cadence_days = _CADENCE_DAYS.get(cadence, 1)
    last_seen_raw = state.get(source_name)
    if not last_seen_raw:
        return True
    try:
        last_seen = datetime.fromisoformat(last_seen_raw)
    except ValueError:
        return True
    if now is None:
        now = datetime.now(timezone.utc)
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    return now - last_seen >= timedelta(days=cadence_days)
