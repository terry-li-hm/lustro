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


def test_engagement_boost_affinity(tmp_path, monkeypatch):
    """Source with prior engagement returns +1 (receptor recycled with affinity)."""
    relevance_log = tmp_path / "relevance.jsonl"
    engagement_log = tmp_path / "engagement.jsonl"
    monkeypatch.setattr(relevance, "RELEVANCE_LOG", relevance_log)
    monkeypatch.setattr(relevance, "ENGAGEMENT_LOG", engagement_log)

    relevance_log.write_text(
        json.dumps({"title": "Prior engaged item", "source": "TechSource", "score": 7}) + "\n",
        encoding="utf-8",
    )
    engagement_log.write_text(
        json.dumps({"title": "Prior engaged item", "action": "deepened"}) + "\n",
        encoding="utf-8",
    )

    boost = relevance._engagement_boost("New item from same source", "TechSource")
    assert boost == 1


def test_engagement_boost_false_positive_penalty(tmp_path, monkeypatch):
    """Source with repeated high scores but zero engagement returns -1 (false-positive signal)."""
    relevance_log = tmp_path / "relevance.jsonl"
    engagement_log = tmp_path / "engagement.jsonl"
    monkeypatch.setattr(relevance, "RELEVANCE_LOG", relevance_log)
    monkeypatch.setattr(relevance, "ENGAGEMENT_LOG", engagement_log)

    relevance_log.write_text(
        "\n".join([
            json.dumps({"title": "Hype piece one", "source": "HypeSource", "score": 8}),
            json.dumps({"title": "Hype piece two", "source": "HypeSource", "score": 9}),
        ]) + "\n",
        encoding="utf-8",
    )
    # No engagement entries — empty log
    engagement_log.write_text("", encoding="utf-8")

    boost = relevance._engagement_boost("Hype piece three", "HypeSource")
    assert boost == -1


def test_engagement_boost_neutral(tmp_path, monkeypatch):
    """Unknown source with no history returns 0 (no recycling signal)."""
    relevance_log = tmp_path / "relevance.jsonl"
    engagement_log = tmp_path / "engagement.jsonl"
    monkeypatch.setattr(relevance, "RELEVANCE_LOG", relevance_log)
    monkeypatch.setattr(relevance, "ENGAGEMENT_LOG", engagement_log)

    relevance_log.write_text("", encoding="utf-8")
    engagement_log.write_text("", encoding="utf-8")

    boost = relevance._engagement_boost("Some item", "BrandNewSource")
    assert boost == 0


def test_engagement_boost_single_high_no_penalty(tmp_path, monkeypatch):
    """Source with only one high-scored unengaged item does NOT get penalised (threshold is 2)."""
    relevance_log = tmp_path / "relevance.jsonl"
    engagement_log = tmp_path / "engagement.jsonl"
    monkeypatch.setattr(relevance, "RELEVANCE_LOG", relevance_log)
    monkeypatch.setattr(relevance, "ENGAGEMENT_LOG", engagement_log)

    relevance_log.write_text(
        json.dumps({"title": "One high item", "source": "MarginalSource", "score": 8}) + "\n",
        encoding="utf-8",
    )
    engagement_log.write_text("", encoding="utf-8")

    boost = relevance._engagement_boost("Another item", "MarginalSource")
    assert boost == 0


def test_keyword_score_applies_boost(tmp_path, monkeypatch):
    """_keyword_score clamps recycled score to [1, 10]."""
    relevance_log = tmp_path / "relevance.jsonl"
    engagement_log = tmp_path / "engagement.jsonl"
    monkeypatch.setattr(relevance, "RELEVANCE_LOG", relevance_log)
    monkeypatch.setattr(relevance, "ENGAGEMENT_LOG", engagement_log)

    # Seed affinity for "TrustedSource"
    relevance_log.write_text(
        json.dumps({"title": "Prior item", "source": "TrustedSource", "score": 6}) + "\n",
        encoding="utf-8",
    )
    engagement_log.write_text(
        json.dumps({"title": "Prior item", "action": "deepened"}) + "\n",
        encoding="utf-8",
    )

    base = relevance._keyword_score("Neutral title", "Neutral summary", source="")
    boosted = relevance._keyword_score("Neutral title", "Neutral summary", source="TrustedSource")
    assert boosted["score"] == base["score"] + 1


def test_get_source_signal_ratio_insufficient_data(tmp_path, monkeypatch):
    """Fewer than 5 items in window returns 1.0 (receptor stays at baseline sensitivity)."""
    log_path = tmp_path / "relevance.jsonl"
    monkeypatch.setattr(relevance, "RELEVANCE_LOG", log_path)

    now_str = "2026-03-25T10:00:00+00:00"
    # Only 3 items — below minimum sample threshold
    lines = [
        json.dumps({"timestamp": now_str, "source": "NoisyFeed", "score": 1}),
        json.dumps({"timestamp": now_str, "source": "NoisyFeed", "score": 2}),
        json.dumps({"timestamp": now_str, "source": "NoisyFeed", "score": 3}),
    ]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    ratio = relevance.get_source_signal_ratio("NoisyFeed", window_days=30)
    assert ratio == 1.0


def test_get_source_signal_ratio_high_signal(tmp_path, monkeypatch):
    """Source with mostly high scores returns a ratio close to 1.0."""
    log_path = tmp_path / "relevance.jsonl"
    monkeypatch.setattr(relevance, "RELEVANCE_LOG", log_path)

    now_str = "2026-03-25T10:00:00+00:00"
    # 5 items: 4 high (>=5), 1 low
    lines = [
        json.dumps({"timestamp": now_str, "source": "SignalFeed", "score": 8}),
        json.dumps({"timestamp": now_str, "source": "SignalFeed", "score": 7}),
        json.dumps({"timestamp": now_str, "source": "SignalFeed", "score": 6}),
        json.dumps({"timestamp": now_str, "source": "SignalFeed", "score": 9}),
        json.dumps({"timestamp": now_str, "source": "SignalFeed", "score": 2}),
    ]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    ratio = relevance.get_source_signal_ratio("SignalFeed", window_days=30)
    assert ratio == pytest.approx(0.8)


def test_get_source_signal_ratio_high_noise(tmp_path, monkeypatch):
    """Source consistently below threshold returns a low ratio (receptor downregulated)."""
    log_path = tmp_path / "relevance.jsonl"
    monkeypatch.setattr(relevance, "RELEVANCE_LOG", log_path)

    now_str = "2026-03-25T10:00:00+00:00"
    # 5 items: all score < 5 (pure noise)
    lines = [
        json.dumps({"timestamp": now_str, "source": "NoisyFeed", "score": 1}),
        json.dumps({"timestamp": now_str, "source": "NoisyFeed", "score": 2}),
        json.dumps({"timestamp": now_str, "source": "NoisyFeed", "score": 3}),
        json.dumps({"timestamp": now_str, "source": "NoisyFeed", "score": 2}),
        json.dumps({"timestamp": now_str, "source": "NoisyFeed", "score": 1}),
    ]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    ratio = relevance.get_source_signal_ratio("NoisyFeed", window_days=30)
    assert ratio == pytest.approx(0.0)


def test_get_source_signal_ratio_ignores_outside_window(tmp_path, monkeypatch):
    """Entries older than the window are not counted (receptor stimulus decays)."""
    log_path = tmp_path / "relevance.jsonl"
    monkeypatch.setattr(relevance, "RELEVANCE_LOG", log_path)

    # 5 old (outside 30-day window) + 5 recent noise items
    old_str = "2025-01-01T10:00:00+00:00"   # well outside window
    recent_str = "2026-03-25T10:00:00+00:00"
    lines = (
        [json.dumps({"timestamp": old_str, "source": "MixedFeed", "score": 9})] * 5
        + [json.dumps({"timestamp": recent_str, "source": "MixedFeed", "score": 1})] * 5
    )
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    ratio = relevance.get_source_signal_ratio("MixedFeed", window_days=30)
    # Only the 5 recent items count; all score 1 → ratio = 0.0
    assert ratio == pytest.approx(0.0)


def test_get_source_signal_ratio_ignores_other_sources(tmp_path, monkeypatch):
    """Items from other sources do not contaminate a source's signal ratio."""
    log_path = tmp_path / "relevance.jsonl"
    monkeypatch.setattr(relevance, "RELEVANCE_LOG", log_path)

    now_str = "2026-03-25T10:00:00+00:00"
    lines = [
        # Target source: 5 high-signal items
        json.dumps({"timestamp": now_str, "source": "TargetFeed", "score": 8}),
        json.dumps({"timestamp": now_str, "source": "TargetFeed", "score": 7}),
        json.dumps({"timestamp": now_str, "source": "TargetFeed", "score": 9}),
        json.dumps({"timestamp": now_str, "source": "TargetFeed", "score": 6}),
        json.dumps({"timestamp": now_str, "source": "TargetFeed", "score": 8}),
        # Other source: all noise — should not affect TargetFeed's ratio
        json.dumps({"timestamp": now_str, "source": "OtherFeed", "score": 1}),
        json.dumps({"timestamp": now_str, "source": "OtherFeed", "score": 1}),
    ]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    ratio = relevance.get_source_signal_ratio("TargetFeed", window_days=30)
    assert ratio == pytest.approx(1.0)
