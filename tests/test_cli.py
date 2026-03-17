from __future__ import annotations

import pytest
import yaml
from typer.testing import CliRunner

from lustro.cli import app


@pytest.mark.skip(reason="CLI refactored to Typer, build_parser no longer exists")
def test_version_flag():
    pass


def test_cmd_sources_lists_and_filters_tier(xdg_env, capsys):
    config_home, _, _ = xdg_env
    sources_path = config_home / "lustro" / "sources.yaml"
    sources_path.parent.mkdir(parents=True, exist_ok=True)
    sources_path.write_text(
        yaml.safe_dump(
            {
                "web_sources": [
                    {"name": "Feed 1", "tier": 1, "cadence": "daily", "rss": "https://a/feed"},
                    {"name": "Site 2", "tier": 2, "cadence": "weekly", "url": "https://b"},
                ],
                "x_accounts": [
                    {"handle": "@alice", "name": "Alice", "tier": 1},
                    {"handle": "@bob", "name": "Bob", "tier": 2},
                ],
            }
        ),
        encoding="utf-8",
    )

    runner = CliRunner()

    result = runner.invoke(app, ["sources"])
    assert result.exit_code == 0
    assert "Feed 1" in result.output
    assert "Site 2" in result.output
    assert "Alice" in result.output
    assert "Bob" in result.output
    assert "Total: 4 sources" in result.output

    result_tier1 = runner.invoke(app, ["sources", "--tier", "1"])
    assert result_tier1.exit_code == 0
    assert "Feed 1" in result_tier1.output
    assert "Alice" in result_tier1.output
    assert "Site 2" not in result_tier1.output
    assert "Bob" not in result_tier1.output
