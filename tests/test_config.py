from __future__ import annotations

from pathlib import Path

from lustro.config import load_config


def test_xdg_paths(xdg_env):
    config_home, cache_home, data_home = xdg_env
    cfg = load_config()
    assert cfg.config_dir == config_home / "lustro"
    assert cfg.cache_dir == cache_home / "lustro"
    assert cfg.data_dir == data_home / "lustro"


def test_env_overrides(monkeypatch, tmp_path, xdg_env):
    monkeypatch.setenv("LUSTRO_CONFIG_DIR", str(tmp_path / "custom-config"))
    monkeypatch.setenv("LUSTRO_CACHE_DIR", str(tmp_path / "custom-cache"))
    monkeypatch.setenv("LUSTRO_DATA_DIR", str(tmp_path / "custom-data"))
    cfg = load_config()
    assert cfg.config_dir == Path(tmp_path / "custom-config").resolve()
    assert cfg.cache_dir == Path(tmp_path / "custom-cache").resolve()
    assert cfg.data_dir == Path(tmp_path / "custom-data").resolve()


def test_sources_fallback_to_builtin(xdg_env):
    cfg = load_config()
    names = [source.get("name") for source in cfg.sources]
    assert "Simon Willison" in names
    assert len(cfg.sources) >= 5


def test_sources_loaded_from_user_file(write_sources_file):
    cfg = load_config()
    assert len(cfg.sources) == 1
    assert cfg.sources[0]["name"] == "Test Feed"
