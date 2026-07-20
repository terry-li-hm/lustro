"""Search the Markdown AI News Log.

Parses the lustro news log into scan sections, source sections, and bullet
entries, then filters them by keyword, X handle, source heading, and inclusive
scan-date bounds. Each entry is emitted at most once and log order is preserved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Pattern

_SCAN_HEADING_RE = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2})(?:\s|$).*$")
_SOURCE_HEADING_RE = re.compile(r"^###\s+(\S.*)$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(slots=True)
class Entry:
    """A single bullet entry located within a scan + source section."""

    scan_heading: str
    scan_date: date
    source_heading: str
    bullet: str


@dataclass(slots=True)
class SearchParams:
    keyword: str | None
    handle: str | None
    source: str | None
    since: date | None
    until: date | None


def parse_iso_date(value: str) -> date:
    """Parse a strict YYYY-MM-DD string.

    Raises ValueError on any deviation (length, separators, calendar validity).
    """
    if not _DATE_RE.match(value):
        raise ValueError(f"invalid date: {value!r} (expected YYYY-MM-DD)")
    return date.fromisoformat(value)


def parse_log(text: str) -> list[Entry]:
    """Parse the news log into Entry records, preserving log order.

    Only ``## YYYY-MM-DD ...`` scan sections, ``### ...`` source sections, and
    ``- ...`` bullet entries are recognized. Text outside such structure is
    ignored. Bullets must appear inside a scan section to be emitted.
    """
    entries: list[Entry] = []
    scan_heading: str | None = None
    scan_date: date | None = None
    source_heading: str | None = None

    for line in text.splitlines():
        if line.startswith("## "):
            match = _SCAN_HEADING_RE.match(line)
            scan_heading = None
            scan_date = None
            source_heading = None
            if match:
                try:
                    scan_date = parse_iso_date(match.group(1))
                except ValueError:
                    continue
                scan_heading = line.rstrip()
            continue
        if line.startswith("### "):
            match = _SOURCE_HEADING_RE.match(line)
            source_heading = line.rstrip() if match and scan_heading else None
            continue
        if line.startswith("- "):
            if scan_heading is None or scan_date is None or source_heading is None:
                continue
            entries.append(
                Entry(
                    scan_heading=scan_heading,
                    scan_date=scan_date,
                    source_heading=source_heading,
                    bullet=line.rstrip(),
                )
            )
    return entries


def _handle_pattern(handle: str) -> Pattern[str]:
    """Build a word-boundary regex for an X handle.

    Accepts the handle with or without a leading ``@`` in the matched text, and
    refuses to match a longer handle (e.g. ``emollick`` must not match
    ``@emollicker``).
    """
    clean = re.escape(handle.lstrip("@"))
    return re.compile(r"(?<![\w@])@?" + clean + r"(?![\w@])", re.IGNORECASE)


def _matches(
    entry: Entry,
    params: SearchParams,
    handle_re: Pattern[str] | None,
) -> bool:
    if params.keyword is not None and params.keyword.lower() not in entry.bullet.lower():
        return False
    if params.source is not None:
        heading = entry.source_heading.removeprefix("###").strip()
        if params.source.strip().casefold() != heading.casefold():
            return False
    if handle_re is not None:
        haystack = f"{entry.source_heading}\n{entry.bullet}"
        if not handle_re.search(haystack):
            return False
    if params.since is not None:
        if entry.scan_date < params.since:
            return False
    if params.until is not None:
        if entry.scan_date > params.until:
            return False
    return True


def filter_entries(entries: list[Entry], params: SearchParams) -> list[Entry]:
    """Return matching entries in log order, deduplicated."""
    handle_re: Pattern[str] | None = None
    if params.handle:
        handle_re = _handle_pattern(params.handle)

    results: list[Entry] = []
    seen: set[tuple[str, str, str]] = set()
    for entry in entries:
        if not _matches(entry, params, handle_re):
            continue
        key = (entry.scan_heading, entry.source_heading, entry.bullet)
        if key in seen:
            continue
        seen.add(key)
        results.append(entry)
    return results


def render_results(results: list[Entry]) -> str:
    """Render matching entries grouped under their scan + source headings.

    Adjacent matches sharing the same context collapse under a single copy of
    each heading.
    """
    lines: list[str] = []
    last_scan: str | None = None
    last_source: str | None = None
    for entry in results:
        if entry.scan_heading != last_scan:
            lines.append(entry.scan_heading)
            lines.append("")
            last_scan = entry.scan_heading
            last_source = None
        if entry.source_heading != last_source:
            if entry.source_heading:
                lines.append(entry.source_heading)
                lines.append("")
            last_source = entry.source_heading
        lines.append(entry.bullet)
    if lines:
        lines.append("")
    return "\n".join(lines)


def run_search(log_path: Path, params: SearchParams) -> int:
    """Execute a search against ``log_path``. Returns the process exit code.

    - Missing log: report path and exit 1 (consistent with ``lustro log``).
    - No matches: print ``No matching entries.`` and exit 1.
    - Otherwise: print rendered entries and exit 0.
    """
    if not log_path.exists():
        print(f"Not found: {log_path}")
        return 1
    text = log_path.read_text(encoding="utf-8")
    entries = parse_log(text)
    matches = filter_entries(entries, params)
    if not matches:
        print("No matching entries.")
        return 1
    print(render_results(matches).rstrip("\n"))
    return 0
