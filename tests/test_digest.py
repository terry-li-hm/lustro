from __future__ import annotations

import argparse
import json
import sys
from types import SimpleNamespace

import pytest

from lustro.cli import cmd_digest
from lustro.config import load_config
from lustro.digest import create_openai_client, run_digest


def _write_month_data(cfg, month: str):
    cfg.article_cache_dir.mkdir(parents=True, exist_ok=True)
    article = {
        "title": "Agent frameworks harden for enterprise adoption",
        "date": f"{month}-24",
        "source": "Example Source",
        "summary": "A short summary",
        "link": "https://example.com/post",
        "text": "Full text body for clustering.",
    }
    path = cfg.article_cache_dir / f"{month}-24_example_abc12345.json"
    path.write_text(json.dumps(article), encoding="utf-8")

    cfg.log_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.log_path.write_text(
        "\n".join(
            [
                f"## {month}-24 (Automated Daily Scan)",
                "### Example Log Source",
                "- **[AI regulation update](https://example.com/reg)**"
                " (2026-02-24) — New policy activity",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


class _FakeOpenAIClient:
    def __init__(self, outputs: list[str]):
        self._outputs = outputs
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create),
        )

    def _create(self, **_kwargs):
        content = self._outputs.pop(0)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )


def test_create_openai_client_missing_dependency(monkeypatch):
    original_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "openai":
            raise ImportError("missing openai")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    with pytest.raises(RuntimeError, match="digest dependencies missing"):
        create_openai_client("test-key")


def test_create_openai_client_sets_openrouter_base_url(monkeypatch):
    calls: dict[str, str] = {}

    class FakeOpenAI:
        def __init__(self, *, base_url: str, api_key: str):
            calls["base_url"] = base_url
            calls["api_key"] = api_key

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    create_openai_client("key-123")

    assert calls["base_url"] == "https://openrouter.ai/api/v1"
    assert calls["api_key"] == "key-123"


def test_run_digest_requires_api_key(xdg_env, monkeypatch):
    cfg = load_config()
    _write_month_data(cfg, "2026-02")
    monkeypatch.delenv("LUSTRO_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="Missing API key"):
        run_digest(cfg, month="2026-02", dry_run=True, themes=4, model=None)


def test_run_digest_dry_run_with_mock_llm(xdg_env, monkeypatch):
    cfg = load_config()
    _write_month_data(cfg, "2026-02")
    monkeypatch.setenv("LUSTRO_API_KEY", "test-key")

    fake_client = _FakeOpenAIClient(
        outputs=[
            json.dumps(
                [
                    {
                        "theme": "Agentic orchestration for enterprise ops",
                        "description": "Teams are moving from simple chat to workflow agents.",
                        "article_indices": [0, 1],
                        "banking_relevance": "Impacts ops and compliance design.",
                    }
                ]
            )
        ]
    )
    monkeypatch.setattr("lustro.digest.create_openai_client", lambda _key: fake_client)

    themes, output_path = run_digest(
        cfg,
        month="2026-02",
        dry_run=True,
        themes=5,
        model="google/gemini-3-flash-preview",
    )

    assert output_path is None
    assert len(themes) == 1
    assert themes[0]["theme"] == "Agentic orchestration for enterprise ops"


def test_cmd_digest_writes_output_file(xdg_env, monkeypatch):
    cfg = load_config()
    _write_month_data(cfg, "2026-02")
    monkeypatch.setenv("OPENROUTER_API_KEY", "router-key")

    fake_client = _FakeOpenAIClient(
        outputs=[
            json.dumps(
                [
                    {
                        "theme": "Regulatory pressure on model governance",
                        "description": "Banks need stronger controls and evidence trails.",
                        "article_indices": [0, 1],
                        "banking_relevance": "Model risk and governance requirements increase.",
                    }
                ]
            ),
            "## Regulatory pressure on model governance\n\n"
            "### Summary\nTighter controls are becoming mandatory.",
        ]
    )
    monkeypatch.setattr("lustro.digest.create_openai_client", lambda _key: fake_client)

    args = argparse.Namespace(
        month="2026-02",
        dry_run=False,
        themes=8,
        model="google/gemini-3-flash-preview",
    )
    exit_code = cmd_digest(args)

    assert exit_code == 0
    output_file = cfg.digest_output_dir / "2026-02 AI Thematic Digest.md"
    assert output_file.exists()
    content = output_file.read_text(encoding="utf-8")
    assert "# AI Thematic Digest — 2026-02" in content
    assert "## Regulatory pressure on model governance" in content
