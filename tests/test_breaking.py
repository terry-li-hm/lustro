from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone

import yaml

from lustro.breaking import can_alert, is_breaking, reset_daily_counter
from lustro.cli import cmd_breaking
from lustro.config import load_config


def test_is_breaking_positive_and_negative():
    assert is_breaking("OpenAI released GPT-5 with new reasoning features") is True
    assert is_breaking("Anthropic partners with startup on a webinar series") is False
    assert is_breaking("Random product update with no entities mentioned") is False


def test_state_counter_reset_and_cooldown():
    now = datetime(2026, 2, 24, 10, 0, tzinfo=timezone.utc)
    state = {
        "alerts_today": 2,
        "today_date": "2026-02-23",
        "last_alert_time": (now - timedelta(minutes=30)).isoformat(),
    }
    reset_daily_counter(state, now)
    assert state["alerts_today"] == 0
    assert state["today_date"] == "2026-02-24"

    state["alerts_today"] = 1
    state["last_alert_time"] = (now - timedelta(minutes=30)).isoformat()
    assert can_alert(state, now) is False

    state["last_alert_time"] = (now - timedelta(minutes=61)).isoformat()
    assert can_alert(state, now) is True

    state["alerts_today"] = 3
    assert can_alert(state, now) is False


def test_cmd_breaking_dry_run(monkeypatch, xdg_env, capsys):
    config_home, _, _ = xdg_env
    sources_path = config_home / "lustro" / "sources.yaml"
    sources_path.parent.mkdir(parents=True, exist_ok=True)
    sources_path.write_text(
        yaml.safe_dump(
            {
                "web_sources": [
                    {
                        "name": "Tier1 Feed",
                        "tier": 1,
                        "cadence": "daily",
                        "rss": "https://example.com/feed.xml",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "lustro.breaking.fetch_rss",
        lambda *_args, **_kwargs: [
            {
                "title": "OpenAI launches GPT-5 family",
                "link": "https://example.com/a",
                "date": "2026-02-24",
            },
            {
                "title": "General ecosystem update",
                "link": "https://example.com/b",
                "date": "2026-02-24",
            },
        ],
    )
    monkeypatch.setattr("lustro.breaking.fetch_web", lambda *_args, **_kwargs: [])

    exit_code = cmd_breaking(argparse.Namespace(dry_run=True))

    assert exit_code == 0
    stderr = capsys.readouterr().err
    assert "1 breaking match(es) found." in stderr
    assert "[DRY RUN]" in stderr

    cfg = load_config()
    state_path = cfg.cache_dir / "breaking-state.json"
    assert state_path.exists()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert len(state["seen_ids"]) == 2
    assert cfg.log_path.exists() is False
