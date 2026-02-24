from __future__ import annotations

from datetime import datetime, timedelta, timezone

from lustro.state import load_state, save_state, should_fetch


def test_load_save_roundtrip(tmp_path, sample_state):
    state_path = tmp_path / "state.json"
    save_state(state_path, sample_state)
    loaded = load_state(state_path)
    assert loaded == sample_state


def test_save_state_atomic_write(tmp_path, monkeypatch, sample_state):
    state_path = tmp_path / "state.json"
    calls: list[tuple[str, str]] = []
    original_replace = __import__("os").replace

    def tracking_replace(src, dst):
        calls.append((src, dst))
        return original_replace(src, dst)

    monkeypatch.setattr("lustro.state.os.replace", tracking_replace)
    save_state(state_path, sample_state)
    assert calls, "os.replace should be used for atomic writes"
    assert state_path.exists()


def test_should_fetch_by_cadence():
    now = datetime(2026, 2, 24, 12, 0, tzinfo=timezone.utc)
    old = (now - timedelta(days=8)).isoformat()
    recent = (now - timedelta(hours=12)).isoformat()
    state = {"weekly-source": old, "twice-weekly-source": recent}

    assert should_fetch({}, "new-source", "daily", now=now) is True
    assert should_fetch(state, "weekly-source", "weekly", now=now) is True
    assert should_fetch(state, "twice-weekly-source", "twice_weekly", now=now) is False
    assert should_fetch({"bad": "not-a-date"}, "bad", "weekly", now=now) is True
