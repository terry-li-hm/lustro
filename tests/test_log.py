from __future__ import annotations

from datetime import datetime, timezone

from lustro.log import (
    _title_prefix,
    append_to_log,
    format_markdown,
    is_junk,
    rotate_log,
)


def test_title_prefix():
    result = _title_prefix("The Future of Agent Systems: A Practical Guide")
    assert result == "the future agent systems practical guide"


def test_is_junk():
    assert is_junk("subscribe") is True
    assert is_junk("tiny") is True
    assert is_junk("A substantial technical update about inference latency") is False


def test_format_markdown():
    results = {
        "Example": [
            {
                "title": "Article 1",
                "date": "2026-02-24",
                "summary": "Summary text",
                "link": "https://example.com/a1",
            }
        ]
    }

    md = format_markdown(results, "2026-02-24")

    assert "## 2026-02-24 (Automated Daily Scan)" in md
    assert "### Example" in md
    assert "- **[Article 1](https://example.com/a1)** (2026-02-24) — Summary text" in md


def test_append_to_log_with_marker(tmp_path):
    log_path = tmp_path / "news.md"
    log_path.write_text("# Header\n\n<!-- News entries below -->\n", encoding="utf-8")

    append_to_log(log_path, "## 2026-02-24 (Automated Daily Scan)")

    content = log_path.read_text(encoding="utf-8")
    assert "<!-- News entries below -->\n\n## 2026-02-24 (Automated Daily Scan)" in content


def test_append_to_log_without_marker(tmp_path):
    log_path = tmp_path / "news.md"
    log_path.write_text("# Header\n", encoding="utf-8")

    append_to_log(log_path, "## 2026-02-24 (Automated Daily Scan)")

    content = log_path.read_text(encoding="utf-8")
    assert content.endswith("\n\n## 2026-02-24 (Automated Daily Scan)")


def test_rotate_log(tmp_path):
    log_path = tmp_path / "AI News Log.md"
    archive_dir = tmp_path / "archive"

    content = "\n".join(
        [
            "# AI News Log",
            "<!-- News entries below -->",
            "## 2026-02-20 (Automated Daily Scan)",
            "- old",
            "## 2026-02-09 (Automated Daily Scan)",
            "- older",
        ]
    )
    log_path.write_text(content + "\n", encoding="utf-8")

    now = datetime(2026, 2, 24, 12, 0, tzinfo=timezone.utc)
    rotate_log(log_path, archive_dir, max_lines=4, now=now)

    rotated = log_path.read_text(encoding="utf-8")
    assert "## 2026-02-09" not in rotated

    archive_file = archive_dir / "AI News Log - Archive 2026-02.md"
    assert archive_file.exists()
    archived = archive_file.read_text(encoding="utf-8")
    assert "## 2026-02-09" in archived
