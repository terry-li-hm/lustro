from __future__ import annotations

import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


def load_title_prefixes(log_path: Path) -> set[str]:
    if not log_path.exists():
        return set()

    content = log_path.read_text(encoding="utf-8")
    prefixes: set[str] = set()

    for match in re.finditer(r'\*\*["\u201c]?(?:\[)?(.+?)(?:\]\([^)]*\))?["\u201d]?\*\*', content):
        title = match.group(1).strip()
        prefix = _title_prefix(title)
        if prefix:
            prefixes.add(prefix)

    for match in re.finditer(r'["\u201c]([^"\u201d]{15,})["\u201d]', content):
        prefix = _title_prefix(match.group(1).strip())
        if prefix:
            prefixes.add(prefix)
    return prefixes


def _title_prefix(title: str) -> str:
    words = re.sub(r"[^\w\s]", "", title.lower()).split()
    sig = [w for w in words if len(w) > 2][:6]
    return " ".join(sig)


def is_junk(title: str) -> bool:
    norm = re.sub(r"[^\w\s]", "", title.lower()).strip()
    if len(norm) < 15:
        return True

    junk = {
        "current accounts",
        "crypto investigations",
        "crypto compliance",
        "crypto security fraud",
        "cumulative repo count over time",
        "cumulative star count over time",
        "subscribe",
        "sign up",
        "read more",
        "learn more",
        "load more",
        "all posts",
        "latest posts",
        "featured",
        "trending",
        "popular",
    }
    return norm in junk or norm.startswith("量子位编辑")


def _atomic_write(path: Path, content: str) -> None:
    """Write file atomically via tempfile + fsync + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(content)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _sanitize_text(text: str) -> str:
    """Strip newlines and leading markdown control chars to prevent log injection."""
    text = " ".join(text.split())
    if text.startswith(("#", "-", ">", "!")):
        text = "\\" + text
    return text


def format_markdown(results: dict[str, list[dict[str, str]]], date_str: str) -> str:
    lines = [f"## {date_str} (Automated Daily Scan)\n"]
    for source, articles in results.items():
        if not articles:
            continue
        lines.append(f"### {source}\n")
        for article in articles:
            date_part = f" ({article['date']})" if article.get("date") else ""
            raw_summary = article.get("summary", "")
            summary_part = f" — {_sanitize_text(raw_summary)}" if raw_summary else ""
            title = _sanitize_text(article.get("title", ""))
            title_part = f"[{title}]({article['link']})" if article.get("link") else title
            lines.append(f"- **{title_part}**{date_part}{summary_part}")
        lines.append("")
    return "\n".join(lines)


def append_to_log(log_path: Path, markdown: str) -> None:
    marker = "<!-- News entries below -->"
    if not log_path.exists():
        _atomic_write(log_path, markdown)
        return

    content = log_path.read_text(encoding="utf-8")
    if marker in content:
        content = content.replace(marker, f"{marker}\n\n{markdown}", 1)
    else:
        content += f"\n\n{markdown}"
    _atomic_write(log_path, content)


def rotate_log(
    log_path: Path,
    archive_dir: Path,
    max_lines: int,
    now: datetime | None = None,
) -> None:
    if now is None:
        now = datetime.now(timezone.utc)

    if not log_path.exists():
        return

    content = log_path.read_text(encoding="utf-8")
    lines = content.splitlines()
    if len(lines) <= max_lines:
        return

    cutoff = (now - timedelta(days=14)).strftime("%Y-%m-%d")
    keep_from = None
    for i, line in enumerate(lines):
        match = re.match(r"^## (\d{4}-\d{2}-\d{2})", line)
        if match and match.group(1) < cutoff:
            keep_from = i
            break

    if keep_from is None:
        return

    marker_line = next((i for i, line in enumerate(lines) if "<!-- News entries below" in line), 0)
    header = lines[: marker_line + 1]
    recent = lines[marker_line + 1 : keep_from]
    old = lines[keep_from:]

    month = now.strftime("%Y-%m")
    archive_name = f"{log_path.stem} - Archive {month}.md"
    archive_path = archive_dir / archive_name
    archive_dir.mkdir(parents=True, exist_ok=True)
    mode = "a" if archive_path.exists() else "w"
    with archive_path.open(mode, encoding="utf-8") as fh:
        if mode == "w":
            fh.write(f"# {log_path.stem} Archive - {month}\n\n")
        fh.write("\n".join(old) + "\n")

    _atomic_write(log_path, "\n".join(header + recent) + "\n")
    print(
        f"Rotated: archived {len(old)} lines to {archive_path.name}, kept {len(recent)} lines.",
        file=sys.stderr,
    )
