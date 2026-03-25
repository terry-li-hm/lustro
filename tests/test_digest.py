from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from lustro.config import load_config
from lustro.digest import (
    _resolve_week_label,
    create_openai_client,
    load_log_entries_since,
    run_digest,
    run_weekly_digest,
    write_weekly_digest,
)


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
                "- [★] **[AI regulation update](https://example.com/reg)**"
                " (banking_angle: Material for bank governance discussions)"
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
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


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

    themes_result, output_path = run_digest(
        cfg=cfg,
        month="2026-02",
        dry_run=False,
        themes=8,
        model="google/gemini-3-flash-preview",
    )

    assert output_path is not None
    output_file = cfg.digest_output_dir / "2026-02 AI Thematic Digest.md"
    assert output_file.exists()
    content = output_file.read_text(encoding="utf-8")
    assert "# AI Thematic Digest — 2026-02" in content
    assert "## Regulatory pressure on model governance" in content


# ---------------------------------------------------------------------------
# Weekly digest tests
# ---------------------------------------------------------------------------


def _write_weekly_log(cfg, log_date: str) -> None:
    """Seed the news log with a mix of transcytose (★) and plain items."""
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    cfg.log_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.log_path.write_text(
        "\n".join([
            f"## {log_date} (Automated Daily Scan)",
            "",
            "### Anthropic Blog",
            "- [★] **[Claude 3.7 Sonnet released](https://anthropic.com/news/claude-3-7)**"
            " (banking_angle: Major model upgrade for enterprise deployments)"
            f" ({log_date}) — New extended thinking capability",
            "",
            "### The Batch",
            f"- **[Weekly AI roundup](https://deeplearning.ai/batch)** ({log_date})"
            " — Summary of this week in AI",
            "",
            "### Simon Willison",
            "- [★] **[LLM tool use patterns](https://simonwillison.net/llm-tool-use)**"
            f" (banking_angle: Agentic workflows for operations automation) ({log_date})"
            " — Practical patterns for tool-calling agents",
        ]) + "\n",
        encoding="utf-8",
    )


def test_resolve_week_label_returns_correct_format():
    """_resolve_week_label returns YYYY-WNN label and 7-day window."""
    anchor = datetime(2026, 3, 25, 12, 0, 0, tzinfo=timezone.utc)  # Wednesday W13
    since, until, label = _resolve_week_label(anchor)
    assert label == "2026-W13"
    assert until == "2026-03-25"
    assert since == "2026-03-18"


def test_load_log_entries_since_parses_transcytose_and_plain(xdg_env):
    """load_log_entries_since distinguishes ★ (transcytose) from plain entries."""
    cfg = load_config()
    _write_weekly_log(cfg, "2026-03-24")

    entries = load_log_entries_since(cfg.log_path, "2026-03-20")
    assert len(entries) == 3

    transcytose = [e for e in entries if e["_transcytose"] == "1"]
    plain = [e for e in entries if e["_transcytose"] == "0"]
    assert len(transcytose) == 2
    assert len(plain) == 1

    titles = [e["title"] for e in transcytose]
    assert "Claude 3.7 Sonnet released" in titles
    assert "LLM tool use patterns" in titles


def test_load_log_entries_since_filters_by_date(xdg_env):
    """load_log_entries_since excludes entries before since_date."""
    cfg = load_config()
    cfg.log_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.log_path.write_text(
        "\n".join([
            "## 2026-03-10 (Automated Daily Scan)",
            "### Old Source",
            "- **[Old article](https://example.com/old)** (2026-03-10) — Old news",
            "",
            "## 2026-03-22 (Automated Daily Scan)",
            "### New Source",
            "- [★] **[New article](https://example.com/new)** (2026-03-22) — Fresh signal",
        ]) + "\n",
        encoding="utf-8",
    )
    # Only entries on or after 2026-03-20 should be returned
    entries = load_log_entries_since(cfg.log_path, "2026-03-20")
    assert len(entries) == 1
    assert entries[0]["title"] == "New article"


def test_write_weekly_digest_creates_file_with_transcytose_section(tmp_path):
    """write_weekly_digest secretes transcytose section and per-source grouping."""
    output_path = tmp_path / "weekly-ai-digest-2026-W13.md"
    entries = [
        {
            "title": "Claude 3.7 Sonnet released",
            "source": "Anthropic Blog",
            "date": "2026-03-24",
            "link": "https://anthropic.com/news/claude-3-7",
            "banking_angle": "Major model upgrade for enterprise deployments",
            "summary": "New extended thinking capability",
            "_transcytose": "1",
        },
        {
            "title": "Weekly AI roundup",
            "source": "The Batch",
            "date": "2026-03-24",
            "link": "https://deeplearning.ai/batch",
            "banking_angle": "",
            "summary": "Summary of this week",
            "_transcytose": "0",
        },
    ]
    # Affinity index provides scores — transcytose item scores 8, plain scores 6
    affinity_index = {
        "Claude 3.7 Sonnet released": {
            "score": 8,
            "banking_angle": "Major model upgrade for enterprise deployments",
            "talking_point": "Ask clients about their model upgrade cadence.",
        },
        "Weekly AI roundup": {
            "score": 6,
            "banking_angle": "N/A",
            "talking_point": "N/A",
        },
    }

    result = write_weekly_digest(
        output_path=output_path,
        week_label="2026-W13",
        since_date="2026-03-18",
        until_date="2026-03-25",
        entries=entries,
        affinity_index=affinity_index,
    )

    assert result == output_path
    assert output_path.exists()
    content = output_path.read_text(encoding="utf-8")

    assert "# Weekly AI Digest — 2026-W13" in content
    assert "Period: 2026-03-18 to 2026-03-25" in content
    assert "## Transcytose" in content
    assert "Claude 3.7 Sonnet released" in content
    assert "Ask clients about their model upgrade cadence." in content
    assert "## By Source" in content
    assert "### Anthropic Blog" in content
    assert "### The Batch" in content
    # Scheduling comment is present
    assert "Sunday" in content


def test_write_weekly_digest_omits_low_score_items(tmp_path):
    """Items with score < WEEKLY_STORE_THRESHOLD are dropped (lysosomal fate)."""
    from lustro.digest import WEEKLY_STORE_THRESHOLD

    output_path = tmp_path / "weekly-ai-digest-2026-W13.md"
    entries = [
        {
            "title": "Low signal item",
            "source": "Noisy Blog",
            "date": "2026-03-24",
            "link": "https://example.com/noisy",
            "banking_angle": "",
            "summary": "Not relevant",
            "_transcytose": "0",
        },
    ]
    affinity_index = {
        "Low signal item": {
            "score": WEEKLY_STORE_THRESHOLD - 1,  # Below threshold — lysosomal fate
            "banking_angle": "N/A",
            "talking_point": "N/A",
        },
    }

    write_weekly_digest(
        output_path=output_path,
        week_label="2026-W13",
        since_date="2026-03-18",
        until_date="2026-03-25",
        entries=entries,
        affinity_index=affinity_index,
    )

    content = output_path.read_text(encoding="utf-8")
    assert "Low signal item" not in content
    assert "No items met the score threshold" in content


def test_run_weekly_digest_returns_count_and_path(xdg_env, monkeypatch, tmp_path):
    """run_weekly_digest writes file and returns correct item count."""
    cfg = load_config()
    _write_weekly_log(cfg, "2026-03-24")

    # Point output to tmp_path (run_weekly_digest uses ~/notes/Reference)
    notes_ref = tmp_path / "notes" / "Reference"
    notes_ref.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("lustro.digest.Path.home", lambda: tmp_path)

    # Stub out affinity log loading so no external files needed
    monkeypatch.setattr("lustro.digest.load_affinity_entries_since", lambda _since: [])

    item_count, output_path = run_weekly_digest(cfg=cfg)

    # Two ★ items were logged; without affinity scores they pass via transcytose flag
    assert item_count >= 2
    assert output_path is not None
    assert output_path.exists()
    content = output_path.read_text(encoding="utf-8")
    assert "# Weekly AI Digest" in content


def test_cli_digest_weekly_flag(xdg_env, monkeypatch, tmp_path):
    """lustro digest --weekly routes to the weekly secretion pathway."""
    from typer.testing import CliRunner

    from lustro.cli import app

    cfg = load_config()
    _write_weekly_log(cfg, "2026-03-24")

    called: dict[str, bool] = {"weekly": False}

    def fake_run_weekly(cfg, week_date=None, tags=None):
        called["weekly"] = True
        out = tmp_path / "weekly-ai-digest-2026-W13.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("# Weekly AI Digest — 2026-W13\n", encoding="utf-8")
        return 3, out

    import lustro.digest as _digest_mod

    monkeypatch.setattr(_digest_mod, "run_weekly_digest", fake_run_weekly)

    runner = CliRunner()
    result = runner.invoke(app, ["digest", "--weekly"])

    assert result.exit_code == 0, result.output
    assert called["weekly"]
    assert "Weekly digest" in result.output or "Written" in result.output
