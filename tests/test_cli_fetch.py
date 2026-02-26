from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lustro.cli import _cmd_fetch_locked
from lustro.config import LustroConfig


@pytest.fixture
def mock_cfg(tmp_path):
    config_dir = tmp_path / "config"
    cache_dir = tmp_path / "cache"
    data_dir = tmp_path / "data"
    for d in [config_dir, cache_dir, data_dir]:
        d.mkdir(parents=True)
    
    cfg_data = {
        "web_sources": [
            {"name": "FallbackSource", "tier": 1, "rss": "https://dead.rss", "url": "https://live.web"}
        ]
    }
    
    return LustroConfig(
        config_dir=config_dir,
        cache_dir=cache_dir,
        data_dir=data_dir,
        config_path=config_dir / "config.yaml",
        sources_path=config_dir / "sources.yaml",
        state_path=data_dir / "state.json",
        log_path=data_dir / "news.md",
        article_cache_dir=cache_dir / "articles",
        digest_output_dir=data_dir / "digests",
        digest_model="test-model",
        sources_data=cfg_data
    )


def test_fetch_fallback_and_zeros(monkeypatch, mock_cfg, capsys):
    # Mock fetch functions
    # 1. First call: fetch_rss returns None, fetch_web returns results
    monkeypatch.setattr("lustro.fetcher.fetch_rss", lambda _url, _since: None)
    monkeypatch.setattr("lustro.fetcher.fetch_web", lambda _url: [{"title": "Web Article", "link": "https://live.web/1"}])
    
    # Mock other needed functions
    monkeypatch.setattr("lustro.state.should_fetch", lambda *args, **kwargs: True)
    monkeypatch.setattr("lustro.log.rotate_log", lambda *args: None)
    monkeypatch.setattr("lustro.log.load_title_prefixes", lambda _p: set())
    monkeypatch.setattr("lustro.log.is_junk", lambda _t: False)
    monkeypatch.setattr("lustro.log.format_markdown", lambda *args: "# News")
    monkeypatch.setattr("lustro.log.append_to_log", lambda *args: None)
    monkeypatch.setattr("lustro.fetcher.archive_article", lambda *args: None)

    args = argparse.Namespace(no_archive=True)
    
    # Run once for fallback success
    _cmd_fetch_locked(args, mock_cfg)
    
    stderr = capsys.readouterr().err
    assert "Falling back to web" in stderr

    # 2. Mock fetch_web to return nothing to test zeros
    monkeypatch.setattr("lustro.fetcher.fetch_web", lambda _url: [])
    
    # Run 5 times to see the warning
    for i in range(5):
        _cmd_fetch_locked(args, mock_cfg)
    
    stderr = capsys.readouterr().err
    assert "Warning: FallbackSource has 5 consecutive zero-article fetches" in stderr

    # Verify state file directly
    import json
    state = json.loads(mock_cfg.state_path.read_text())
    assert state["_zeros:FallbackSource"] == "5"
