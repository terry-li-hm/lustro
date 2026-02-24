from __future__ import annotations

import argparse

import pytest
import yaml

from lustro.cli import build_parser, cmd_sources


def test_version_flag(monkeypatch, capsys):
    monkeypatch.setattr("lustro.cli._get_version", lambda: "0.1.0")
    parser = build_parser()

    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["--version"])
    assert excinfo.value.code == 0

    stdout = capsys.readouterr().out
    assert stdout.strip() == "lustro 0.1.0"


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

    all_code = cmd_sources(argparse.Namespace(tier=None))
    all_stdout = capsys.readouterr().out

    assert all_code == 0
    assert "Feed 1" in all_stdout
    assert "Site 2" in all_stdout
    assert "Alice" in all_stdout
    assert "Bob" in all_stdout
    assert "Total: 4 sources" in all_stdout

    tier1_code = cmd_sources(argparse.Namespace(tier=1))
    tier1_stdout = capsys.readouterr().out

    assert tier1_code == 0
    assert "Feed 1" in tier1_stdout
    assert "Alice" in tier1_stdout
    assert "Site 2" not in tier1_stdout
    assert "Bob" not in tier1_stdout
