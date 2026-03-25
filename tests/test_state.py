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


def test_should_fetch_downregulation_moderate_noise():
    """Moderate noise (signal_ratio 0.2-0.5) extends refractory period by 2 days."""
    now = datetime(2026, 2, 24, 12, 0, tzinfo=timezone.utc)
    # 6 days ago — would normally qualify for weekly (5 days), but 2-day
    # downregulation extension makes effective threshold 7 days
    last = (now - timedelta(days=6)).isoformat()
    state = {"noisy-source": last}

    # Normal cadence: 6 days >= 5 → should fetch
    assert should_fetch(state, "noisy-source", "weekly", now=now, signal_ratio=1.0) is True
    # Moderate noise (+2): 6 days < 7 → should NOT fetch
    assert should_fetch(state, "noisy-source", "weekly", now=now, signal_ratio=0.3) is False
    # After 8 days the receptor is ready again
    old_enough = (now - timedelta(days=8)).isoformat()
    state2 = {"noisy-source": old_enough}
    assert should_fetch(state2, "noisy-source", "weekly", now=now, signal_ratio=0.3) is True


def test_should_fetch_downregulation_high_noise():
    """High noise (signal_ratio < 0.2) extends refractory period by 7 days."""
    now = datetime(2026, 2, 24, 12, 0, tzinfo=timezone.utc)
    # 9 days ago — weekly (5d) would normally trigger, but +7 = 12-day threshold
    last = (now - timedelta(days=9)).isoformat()
    state = {"very-noisy": last}

    # Normal cadence: 9 days >= 5 → would fetch
    assert should_fetch(state, "very-noisy", "weekly", now=now, signal_ratio=1.0) is True
    # High noise (+7): 9 days < 12 → internalized, not yet ready
    assert should_fetch(state, "very-noisy", "weekly", now=now, signal_ratio=0.1) is False
    # After 13 days the receptor has recovered
    recovered = (now - timedelta(days=13)).isoformat()
    state2 = {"very-noisy": recovered}
    assert should_fetch(state2, "very-noisy", "weekly", now=now, signal_ratio=0.1) is True


def test_should_fetch_new_source_always_fetches_regardless_of_signal_ratio():
    """New sources (not in state) always fetch — no prior stimulus to judge."""
    now = datetime(2026, 2, 24, 12, 0, tzinfo=timezone.utc)
    assert should_fetch({}, "brand-new", "weekly", now=now, signal_ratio=0.0) is True
