from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import yaml

from lustro.breaking import can_alert, is_breaking, reset_daily_counter, run_breaking, title_fingerprint
from lustro.config import load_config


def test_is_breaking_positive_and_negative():
    assert is_breaking("OpenAI released GPT-5 with new reasoning features") is True
    assert is_breaking("Anthropic partners with startup on a webinar series") is False
    assert is_breaking("Random product update with no entities mentioned") is False


def test_is_breaking_new_action_verbs():
    # "available" — e.g. "Claude 3.7 now available"
    assert is_breaking("Claude 3.7 Sonnet now available to developers") is True
    # "publishes" — e.g. "OpenAI publishes safety report"
    assert is_breaking("OpenAI publishes new safety guidelines") is True
    # "published" — past tense companion
    assert is_breaking("Anthropic published its model spec update") is True
    # "enters" — e.g. "Mistral enters enterprise market"
    assert is_breaking("Mistral enters the enterprise AI market") is True
    # "entered" — past tense; "partnership" does not trigger NEGATIVE (\bpartner\b
    # requires a word boundary after "partner", which "partnership" lacks), so
    # this headline correctly fires as breaking.
    assert is_breaking("OpenAI entered a new deal in healthcare") is True
    # NEGATIVE does block "partner" when used as a standalone word
    assert is_breaking("OpenAI is a partner in a new healthcare webinar") is False


def test_is_breaking_codex_entity():
    # "Codex" without version qualifier should match as an ENTITY
    assert is_breaking("OpenAI announces Codex for software engineering") is True
    assert is_breaking("Codex launches as a standalone agent product") is True
    # "codex" + action verb but no recognised AI entity context: codex IS now in
    # ENTITIES, so it fires even in a general sentence — which is the intended
    # behaviour (we want to catch Codex releases).
    assert is_breaking("Codex enters general availability") is True
    # No entity at all → False
    assert is_breaking("A new legal statute entered the books today") is False


def test_title_fingerprint_cross_source_dedup():
    # Same title → same fingerprint
    fp1 = title_fingerprint("OpenAI launches GPT-5 family")
    fp2 = title_fingerprint("OpenAI launches GPT-5 family")
    assert fp1 == fp2

    # Case/punctuation variants → same fingerprint
    fp3 = title_fingerprint("openai launches gpt-5 family!")
    assert fp1 == fp3

    # Different title → different fingerprint
    fp4 = title_fingerprint("Anthropic releases Claude 4")
    assert fp1 != fp4


def test_cross_source_dedup_in_run(monkeypatch, xdg_env, capsys):
    """Same story from two sources in one run should produce only one alert."""
    config_home, _, _ = xdg_env
    sources_path = config_home / "lustro" / "sources.yaml"
    sources_path.parent.mkdir(parents=True, exist_ok=True)
    sources_path.write_text(
        yaml.safe_dump(
            {
                "web_sources": [
                    {
                        "name": "Feed A",
                        "tier": 1,
                        "cadence": "daily",
                        "rss": "https://feed-a.example.com/feed.xml",
                    },
                    {
                        "name": "Feed B",
                        "tier": 1,
                        "cadence": "daily",
                        "rss": "https://feed-b.example.com/feed.xml",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    call_count = {"n": 0}

    def mock_fetch_rss(url: str, *_args, **_kwargs):
        call_count["n"] += 1
        # Both feeds return the same story with identical title
        return [
            {
                "title": "Anthropic launches Claude 4 with extended context",
                "link": f"https://example.com/story-from-feed-{call_count['n']}",
                "date": "2026-03-20",
            }
        ]

    monkeypatch.setattr("lustro.breaking.internalize_rss", mock_fetch_rss)
    monkeypatch.setattr("lustro.breaking.internalize_web", lambda *_args, **_kwargs: [])

    cfg = load_config()
    exit_code = run_breaking(cfg=cfg, dry_run=True)

    assert exit_code == 0
    stderr = capsys.readouterr().err
    # Only one match should be reported, not two
    assert "1 breaking match(es) found." in stderr
    assert "Cross-source dedup" in stderr


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
        "lustro.breaking.internalize_rss",
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
    monkeypatch.setattr("lustro.breaking.internalize_web", lambda *_args, **_kwargs: [])

    cfg = load_config()
    exit_code = run_breaking(cfg=cfg, dry_run=True)

    assert exit_code == 0
    stderr = capsys.readouterr().err
    assert "1 breaking match(es) found." in stderr
    assert "[DRY RUN]" in stderr

    state_path = cfg.cache_dir / "breaking-state.json"
    assert state_path.exists()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert len(state["seen_ids"]) == 2
    assert cfg.log_path.exists() is False
