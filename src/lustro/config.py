from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def _expand_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


def _env_path(env_name: str, default: Path) -> Path:
    return _expand_path(os.getenv(env_name, str(default)))


def _xdg_base(env_name: str, fallback_suffix: str) -> Path:
    default = Path.home() / fallback_suffix
    return _expand_path(os.getenv(env_name, str(default)))


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        return {}
    return data


def default_sources_path() -> Path:
    return Path(__file__).with_name("sources") / "default.yaml"


def default_sources_text() -> str:
    return default_sources_path().read_text(encoding="utf-8")


@dataclass(slots=True)
class LustroConfig:
    config_dir: Path
    cache_dir: Path
    data_dir: Path
    config_path: Path
    sources_path: Path
    state_path: Path
    log_path: Path
    article_cache_dir: Path
    digest_output_dir: Path
    digest_model: str
    config_data: dict[str, Any] = field(default_factory=dict)
    sources_data: dict[str, Any] = field(default_factory=dict)

    @property
    def sources(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for section in self.sources_data.values():
            if isinstance(section, list):
                result.extend(item for item in section if isinstance(item, dict))
        return result


def load_config() -> LustroConfig:
    xdg_config = _xdg_base("XDG_CONFIG_HOME", ".config")
    xdg_cache = _xdg_base("XDG_CACHE_HOME", ".cache")
    xdg_data = _xdg_base("XDG_DATA_HOME", ".local/share")

    config_dir = _env_path("LUSTRO_CONFIG_DIR", xdg_config / "lustro")
    cache_dir = _env_path("LUSTRO_CACHE_DIR", xdg_cache / "lustro")
    data_dir = _env_path("LUSTRO_DATA_DIR", xdg_data / "lustro")

    config_path = config_dir / "config.yaml"
    sources_path = config_dir / "sources.yaml"
    state_path = cache_dir / "state.json"
    article_cache_dir = cache_dir / "articles"

    config_data = _load_yaml(config_path)
    sources_data = _load_yaml(sources_path)
    if not sources_data:
        sources_data = _load_yaml(default_sources_path())

    log_path_raw = config_data.get("log_path", str(data_dir / "news.md"))
    log_path = _expand_path(str(log_path_raw))
    digest_output_raw = config_data.get("digest_output_dir", str(data_dir / "digests"))
    digest_output_dir = _expand_path(str(digest_output_raw))
    digest_model = str(
        config_data.get("digest_model", "google/gemini-3-flash-preview")
    )

    return LustroConfig(
        config_dir=config_dir,
        cache_dir=cache_dir,
        data_dir=data_dir,
        config_path=config_path,
        sources_path=sources_path,
        state_path=state_path,
        log_path=log_path,
        article_cache_dir=article_cache_dir,
        digest_output_dir=digest_output_dir,
        digest_model=digest_model,
        config_data=config_data,
        sources_data=sources_data,
    )
