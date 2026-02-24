from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def load_title_prefixes(log_path: Path) -> set[str]:
    if not log_path.exists():
        return set()

    content = log_path.read_text(encoding="utf-8")
    prefixes: set[str] = set()

    for match in re.finditer(
        r'\*\*["\u201c]?(?:\[)?(.+?)(?:\]\([^)]*\))?["\u201d]?\*\*', content
    ):
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


def format_markdown(results: dict[str, list[dict[str, str]]], date_str: str) -> str:
    lines = [f"## {date_str} (Automated Daily Scan)\n"]
    for source, articles in results.items():
        if not articles:
            continue
        lines.append(f"### {source}\n")
        for article in articles:
            date_part = f" ({article['date']})" if article.get("date") else ""
            summary_part = f" — {article['summary']}" if article.get("summary") else ""
            title = article.get("title", "")
            title_part = f"[{title}]({article['link']})" if article.get("link") else title
            lines.append(f"- **{title_part}**{date_part}{summary_part}")
        lines.append("")
    return "\n".join(lines)


def append_to_log(log_path: Path, markdown: str) -> None:
    marker = "<!-- News entries below -->"
    if not log_path.exists():
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(markdown, encoding="utf-8")
        return

    content = log_path.read_text(encoding="utf-8")
    if marker in content:
        content = content.replace(marker, f"{marker}\n\n{markdown}", 1)
    else:
        content += f"\n\n{markdown}"
    log_path.write_text(content, encoding="utf-8")


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

    log_path.write_text("\n".join(header + recent) + "\n", encoding="utf-8")
    print(
        f"Rotated: archived {len(old)} lines to {archive_path.name}, kept {len(recent)} lines.",
        file=sys.stderr,
    )
