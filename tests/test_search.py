from __future__ import annotations

from datetime import date

import pytest
from typer.testing import CliRunner

from lustro.cli import app
from lustro.search import SearchParams, filter_entries, parse_log

LOG = """# AI News Log

orphan PowerPoint text
- orphan PowerPoint bullet

## 2026-02-01 (Automated Daily Scan)
### X Accounts
- **[AI Teaching](https://example.com/one)** by @emollick — PowerPoint guidance
- **[Near miss](https://example.com/two)** by @emollicker — PowerPoint noise

### OpenAI News
- **[Launch](https://example.com/three)** — New agent platform

## 2026-02-10 (Automated Daily Scan)
### X Accounts
- **[Later note](https://example.com/four)** by @EMOLLICK — spreadsheet guidance

## not-a-date
### X Accounts
- **[Malformed section](https://example.com/five)** by @emollick — PowerPoint
"""


def _write_log(xdg_env):
    _, _, data_home = xdg_env
    log_path = data_home / "lustro" / "news.md"
    log_path.parent.mkdir(parents=True)
    log_path.write_text(LOG, encoding="utf-8")
    return log_path


def test_parse_log_ignores_entries_without_valid_context():
    entries = parse_log(LOG)

    assert [entry.scan_date for entry in entries] == [
        date(2026, 2, 1),
        date(2026, 2, 1),
        date(2026, 2, 1),
        date(2026, 2, 10),
    ]
    assert all("orphan" not in entry.bullet for entry in entries)
    assert all("Malformed" not in entry.bullet for entry in entries)


def test_filter_entries_combines_selectors_and_inclusive_dates():
    entries = parse_log(LOG)
    results = filter_entries(
        entries,
        SearchParams(
            keyword="GUIDANCE",
            handle="@EmOlLiCk",
            source="x accounts",
            since=date(2026, 2, 1),
            until=date(2026, 2, 10),
        ),
    )

    assert [entry.bullet for entry in results] == [entries[0].bullet, entries[3].bullet]


def test_handle_filter_does_not_match_longer_handle():
    results = filter_entries(
        parse_log(LOG),
        SearchParams(keyword=None, handle="emollick", source=None, since=None, until=None),
    )

    assert ["AI Teaching" in entry.bullet for entry in results] == [True, False]
    assert all("Near miss" not in entry.bullet for entry in results)


def test_search_cli_keyword_preserves_order_and_context(xdg_env):
    _write_log(xdg_env)

    result = CliRunner().invoke(app, ["search", "powerpoint"])

    assert result.exit_code == 0
    assert "## 2026-02-01" in result.output
    assert "### X Accounts" in result.output
    assert result.output.index("AI Teaching") < result.output.index("Near miss")
    assert "orphan" not in result.output


def test_search_cli_source_and_date_bounds_are_inclusive(xdg_env):
    _write_log(xdg_env)

    result = CliRunner().invoke(app, [
        "search",
        "--source",
        "OPENAI NEWS",
        "--since",
        "2026-02-01",
        "--until",
        "2026-02-01",
    ])

    assert result.exit_code == 0
    assert "Launch" in result.output


def test_search_cli_no_matches(xdg_env):
    _write_log(xdg_env)

    result = CliRunner().invoke(app, ["search", "absent"])

    assert result.exit_code == 1
    assert "No matching entries." in result.output


def test_search_cli_missing_log(xdg_env):
    result = CliRunner().invoke(app, ["search", "anything"])

    assert result.exit_code == 1
    assert "Not found:" in result.output
    assert "news.md" in result.output


@pytest.mark.parametrize(
    "arguments",
    [
        ["search"],
        ["search", "--since", "2026-02-01"],
        ["search", "x", "--since", "2026-2-1"],
        ["search", "x", "--until", "not-a-date"],
        ["search", "x", "--since", "2026-02-10", "--until", "2026-02-01"],
    ],
)
def test_search_cli_requires_selector_and_valid_dates(xdg_env, arguments):
    _write_log(xdg_env)

    result = CliRunner().invoke(app, arguments)

    assert result.exit_code == 2, (arguments, result.output, result.exception)
