from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import yaml


@pytest.fixture
def xdg_env(monkeypatch: pytest.MonkeyPatch, tmp_path):
    config_home = tmp_path / "config"
    cache_home = tmp_path / "cache"
    data_home = tmp_path / "data"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_home))
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    monkeypatch.delenv("LUSTRO_CONFIG_DIR", raising=False)
    monkeypatch.delenv("LUSTRO_CACHE_DIR", raising=False)
    monkeypatch.delenv("LUSTRO_DATA_DIR", raising=False)
    return config_home, cache_home, data_home


@pytest.fixture
def sample_state():
    now = datetime.now(timezone.utc)
    return {
        "Source A": (now - timedelta(days=2)).isoformat(),
        "Source B": (now - timedelta(hours=12)).isoformat(),
    }


@pytest.fixture
def sample_sources():
    return {
        "web_sources": [
            {
                "name": "Test Feed",
                "tier": 1,
                "cadence": "daily",
                "rss": "https://example.com/feed.xml",
                "url": "https://example.com",
            }
        ]
    }


@pytest.fixture
def write_sources_file(xdg_env, sample_sources):
    config_home, _, _ = xdg_env
    target = config_home / "lustro" / "sources.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(sample_sources), encoding="utf-8")
    return target
