from __future__ import annotations

import json

import pytest

from lustro import relevance


@pytest.fixture(autouse=True)
def force_keyword_fallback(monkeypatch):
    def fake_run(*_args, **_kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(relevance.subprocess, "run", fake_run)


def test_keyword_scoring():
    result = relevance.score_item(
        "Enterprise agent governance benchmark released",
        "Example Source",
        "Production evaluation and governance patterns for enterprise AI teams.",
    )

    assert result["score"] >= 5
    assert result["banking_angle"] == "N/A"
    assert result["talking_point"] == "N/A"


def test_log_score(tmp_path, monkeypatch):
    log_path = tmp_path / "relevance.jsonl"
    monkeypatch.setattr(relevance, "RELEVANCE_LOG", log_path)

    relevance.log_score(
        {
            "timestamp": "2026-03-13T10:00:00+00:00",
            "title": "HKMA updates AML guidance",
            "source": "HKMA",
        },
        {
            "score": 9,
            "banking_angle": "Banks need to adapt AML controls.",
            "talking_point": "This will affect compliance roadmaps.",
        },
    )

    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert rows == [
        {
            "timestamp": "2026-03-13T10:00:00+00:00",
            "title": "HKMA updates AML guidance",
            "source": "HKMA",
            "score": 9,
            "banking_angle": "Banks need to adapt AML controls.",
            "talking_point": "This will affect compliance roadmaps.",
        }
    ]


def test_log_engagement(tmp_path, monkeypatch):
    log_path = tmp_path / "engagement.jsonl"
    monkeypatch.setattr(relevance, "ENGAGEMENT_LOG", log_path)

    relevance.log_engagement("Anthropic banking release", action="read_full")

    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["title"] == "Anthropic banking release"
    assert rows[0]["action"] == "read_full"
    assert rows[0]["timestamp"]


def test_get_stats(tmp_path, monkeypatch):
    relevance_log = tmp_path / "relevance.jsonl"
    engagement_log = tmp_path / "engagement.jsonl"
    monkeypatch.setattr(relevance, "RELEVANCE_LOG", relevance_log)
    monkeypatch.setattr(relevance, "ENGAGEMENT_LOG", engagement_log)

    relevance_log.write_text(
        "\n".join(
            [
                json.dumps({"timestamp": "2026-03-10T10:00:00+00:00", "title": "Low but engaged", "source": "A", "score": 4}),
                json.dumps({"timestamp": "2026-03-10T11:00:00+00:00", "title": "High ignored", "source": "B", "score": 8}),
                json.dumps({"timestamp": "2026-03-10T12:00:00+00:00", "title": "High engaged", "source": "C", "score": 9}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    engagement_log.write_text(
        "\n".join(
            [
                json.dumps({"timestamp": "2026-03-10T13:00:00+00:00", "title": "Low but engaged", "action": "deepened"}),
                json.dumps({"timestamp": "2026-03-10T14:00:00+00:00", "title": "High engaged", "action": "deepened"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    stats = relevance.get_stats()

    assert stats["status"] == "ok"
    assert stats["total_scored"] == 3
    assert stats["total_engaged"] == 2
    assert stats["false_negatives"] == ["Low but engaged"]
    assert stats["false_positives_count"] == 1
    assert stats["avg_engaged_score"] == 6.5


def test_score_banking_item_high():
    result = relevance.score_item(
        "HKMA issues new AML guidance for banks using AI",
        "HKMA",
        "The update covers compliance, fraud detection, and model risk expectations for banks.",
    )

    assert result["score"] >= 7


def test_score_consumer_item_low():
    result = relevance.score_item(
        "Consumer photo app adds fun AI selfie filters",
        "App Store Blog",
        "A new creator-focused entertainment feature for social media sharing.",
    )

    assert result["score"] <= 4
