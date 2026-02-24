from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any

from lustro.config import LustroConfig
from lustro.log import append_to_log


def _compile_keywords(patterns: list[str]) -> list[re.Pattern[str]]:
    compiled: list[re.Pattern[str]] = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern, re.IGNORECASE))
        except re.error:
            continue
    return compiled


def matches_keywords(text: str, compiled_keywords: list[re.Pattern[str]]) -> bool:
    return any(regex.search(text) for regex in compiled_keywords)


def _normalize_handle(value: str) -> str:
    return value.lstrip("@").strip().lower()


def _extract_handle(tweet: dict[str, Any]) -> str:
    author = tweet.get("author", {})
    if isinstance(author, dict):
        for key in ("handle", "username", "screen_name"):
            value = author.get(key)
            if isinstance(value, str) and value.strip():
                return _normalize_handle(value)
    for key in ("author_handle", "handle", "username"):
        value = tweet.get(key)
        if isinstance(value, str) and value.strip():
            return _normalize_handle(value)
    return ""


def _sample(text: str, limit: int = 100) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _append_discovery_log(
    cfg: LustroConfig,
    now: datetime,
    scanned: int,
    matched_count: int,
    new_handles: list[tuple[str, int, str]],
) -> None:
    lines = [
        f"## {now.strftime('%Y-%m-%d')} (X Discovery)\n",
        "### X Discovery (For You)\n",
        f"- Scanned {scanned} tweets; {matched_count} matched keywords.",
    ]
    if new_handles:
        for handle, count, sample in new_handles:
            lines.append(f'- @{handle} ({count} matches) — "{sample}"')
    else:
        lines.append("- No new handles found.")
    append_to_log(cfg.log_path, "\n".join(lines) + "\n")


def run_discover(cfg: LustroConfig, count: int | None = None) -> int:
    discovery_cfg = cfg.sources_data.get("x_discovery", {})
    if not isinstance(discovery_cfg, dict):
        discovery_cfg = {}
    keywords = discovery_cfg.get("keywords", [])
    if not isinstance(keywords, list):
        keywords = []
    compiled = _compile_keywords([str(pattern) for pattern in keywords if isinstance(pattern, str)])

    default_count = int(discovery_cfg.get("count", 50))
    tweet_count = int(count) if count is not None else default_count
    tweet_count = max(tweet_count, 1)

    bird_cli = shutil.which("bird")
    if bird_cli is None:
        print("bird CLI not found - skipping X discovery", file=sys.stderr)
        return 0

    try:
        proc = subprocess.run(
            [bird_cli, "home", "-n", str(tweet_count), "--json"],
            capture_output=True,
            text=True,
            timeout=45,
        )
    except subprocess.TimeoutExpired:
        print("bird home timed out", file=sys.stderr)
        return 1

    if proc.returncode != 0:
        message = proc.stderr.strip() or "unknown error"
        print(f"bird home failed: {message}", file=sys.stderr)
        return 1

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        print(f"bird output parse error: {exc}", file=sys.stderr)
        return 1
    if not isinstance(payload, list):
        print("bird output parse error: expected JSON list", file=sys.stderr)
        return 1

    x_accounts = cfg.sources_data.get("x_accounts", [])
    tracked = set()
    if isinstance(x_accounts, list):
        for account in x_accounts:
            if not isinstance(account, dict):
                continue
            handle = account.get("handle", "")
            if isinstance(handle, str) and handle.strip():
                tracked.add(_normalize_handle(handle))

    matched_count = 0
    grouped: dict[str, dict[str, Any]] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        if compiled and not matches_keywords(text, compiled):
            continue
        if not compiled:
            continue

        matched_count += 1
        handle = _extract_handle(item)
        if not handle or handle in tracked:
            continue
        row = grouped.setdefault(handle, {"count": 0, "sample": _sample(text)})
        row["count"] += 1

    new_handles = sorted(
        [(handle, int(data["count"]), str(data["sample"])) for handle, data in grouped.items()],
        key=lambda item: (-item[1], item[0]),
    )

    print(
        f"X Discovery: scanned {len(payload)} tweets, {matched_count} matched keywords",
        file=sys.stderr,
    )
    if new_handles:
        print("New handles (not tracked):", file=sys.stderr)
        for handle, handle_count, sample in new_handles:
            print(f'  @{handle} ({handle_count} matches) — "{sample}"', file=sys.stderr)
    else:
        print("New handles (not tracked): none", file=sys.stderr)

    now = datetime.now(timezone.utc)
    _append_discovery_log(cfg, now, len(payload), matched_count, new_handles)
    return 0
