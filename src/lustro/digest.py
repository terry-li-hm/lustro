from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from lustro.config import LustroConfig


DEFAULT_THEME_COUNT = 8
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def _resolve_month(month: str | None) -> str:
    if month:
        return month
    return datetime.now().astimezone().strftime("%Y-%m")


def _get_api_key() -> str | None:
    return os.environ.get("LUSTRO_API_KEY") or os.environ.get("OPENROUTER_API_KEY")


def create_openai_client(api_key: str):
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "digest dependencies missing: install with `pip install 'lustro[digest]'` "
            "or `uv pip install 'lustro[digest]'`."
        ) from exc
    return OpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)


def _llm_call(client: Any, model: str, system: str, user: str, max_tokens: int) -> str:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content or ""


def load_archived_articles(article_cache_dir: Path, month: str) -> list[dict[str, Any]]:
    if not article_cache_dir.exists():
        return []
    articles: list[dict[str, Any]] = []
    for path in sorted(article_cache_dir.glob(f"{month}*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        payload["_file"] = path.name
        articles.append(payload)
    return articles


def load_news_log_entries(log_path: Path, month: str) -> list[dict[str, str]]:
    if not log_path.exists():
        return []

    entries: list[dict[str, str]] = []
    current_date = ""
    current_source = ""
    for line in log_path.read_text(encoding="utf-8").splitlines():
        date_match = re.match(r"^## (\d{4}-\d{2}-\d{2})", line)
        if date_match:
            current_date = date_match.group(1)
            continue

        source_match = re.match(r"^### (.+)", line)
        if source_match:
            current_source = source_match.group(1).strip()
            continue

        article_match = re.match(
            r"^- \*\*(?:\[([^\]]+)\]\(([^)]+)\)|([^*]+))\*\*"
            r"(?:\s*\(([^)]*)\))?"
            r"(?:\s*—\s*(.+))?",
            line,
        )
        if article_match and current_date.startswith(month):
            title = (article_match.group(1) or article_match.group(3) or "").strip()
            if not title:
                continue
            entries.append(
                {
                    "title": title,
                    "source": current_source,
                    "date": article_match.group(4) or current_date,
                    "link": article_match.group(2) or "",
                    "summary": article_match.group(5) or "",
                }
            )
    return entries


def _parse_theme_json(raw: str) -> list[dict[str, Any]]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    parsed = json.loads(text)
    if not isinstance(parsed, list):
        raise ValueError("theme response is not a list")
    return [item for item in parsed if isinstance(item, dict)]


def identify_themes(
    client: Any,
    model: str,
    articles: list[dict[str, Any]],
    log_entries: list[dict[str, str]],
    max_themes: int,
) -> list[dict[str, Any]]:
    items: list[str] = []
    for i, article in enumerate(articles):
        text_preview = ""
        text = article.get("text")
        if isinstance(text, str) and text:
            text_preview = " ".join(text.split()[:200])[:500]
        items.append(
            f"[{i}] {article.get('date', '')} | {article.get('source', '')} | {article.get('title', '')}\n"
            f"    Summary: {article.get('summary', '')}\n"
            f"    Preview: {text_preview}"
        )

    offset = len(articles)
    for i, entry in enumerate(log_entries):
        items.append(
            f"[{offset + i}] {entry.get('date', '')} | {entry.get('source', '')} | {entry.get('title', '')}\n"
            f"    Summary: {entry.get('summary', '')}"
        )

    system = (
        "You identify thematic clusters in AI news for a consultant advising banks on AI strategy.\n\n"
        f"Rules:\n- Identify {max_themes} themes most relevant to AI in banking/financial services\n"
        "- Each theme should have 3+ articles supporting it\n"
        '- Themes should be specific (not "AI progress")\n'
        "- Include cross-cutting themes (regulation, open-source vs proprietary, infrastructure)\n"
        "- Return valid JSON only, no markdown fences"
    )
    user = (
        f"Below are {len(articles)} archived articles (some with full text) and "
        f"{len(log_entries)} news log headlines from this month.\n\n"
        f"Identify up to {max_themes} thematic clusters. Return JSON:\n"
        "[\n"
        "  {\n"
        '    "theme": "Theme title",\n'
        '    "description": "2-3 sentence description",\n'
        '    "article_indices": [0, 3, 7],\n'
        '    "banking_relevance": "Why this matters for banks/fintech"\n'
        "  }\n"
        "]\n\n"
        "Articles:\n"
        + "\n\n".join(items)
    )
    raw = _llm_call(client, model, system, user, max_tokens=4000)
    return _parse_theme_json(raw)


def synthesize_theme(
    client: Any,
    model: str,
    theme: dict[str, Any],
    articles: list[dict[str, Any]],
    log_entries: list[dict[str, str]],
) -> str:
    all_items: list[dict[str, Any]] = [*articles, *log_entries]
    selected: list[dict[str, Any]] = []
    for raw_idx in theme.get("article_indices", []):
        if not isinstance(raw_idx, int):
            continue
        if 0 <= raw_idx < len(all_items):
            selected.append(all_items[raw_idx])

    context_parts: list[str] = []
    for item in selected:
        text = item.get("text")
        if isinstance(text, str) and text:
            text_block = " ".join(text.split()[:3000])
        else:
            text_block = str(item.get("summary", "(no text available)"))
        context_parts.append(
            f"### {item.get('source', 'Unknown')} — {item.get('title', 'Untitled')}\n"
            f"Date: {item.get('date', 'unknown')} | Link: {item.get('link', 'n/a')}\n\n"
            f"{text_block}"
        )

    system = (
        "You produce evidence briefs for an AI consultant advising banks.\n"
        "Ground every claim in provided sources. Mark [paraphrased] when needed.\n"
        "Focus on banking/financial-services implications."
    )
    user = (
        f"Theme: {theme.get('theme', 'Untitled Theme')}\n"
        f"Description: {theme.get('description', '')}\n"
        f"Banking relevance: {theme.get('banking_relevance', '')}\n\n"
        "Produce an evidence brief with sections:\n"
        "## Theme\n### Summary\n### Claims & Evidence\n### Open Questions\n"
        "### Banking & Fintech Implications\n### Key Quotes\n\n"
        "Source articles:\n\n"
        + "\n\n---\n\n".join(context_parts)
    )
    return _llm_call(client, model, system, user, max_tokens=6000)


def write_digest(
    output_dir: Path,
    month: str,
    themes: list[dict[str, Any]],
    theme_briefs: list[str],
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{month} AI Thematic Digest.md"
    now = datetime.now().astimezone()
    lines = [
        f"# AI Thematic Digest — {month}",
        "",
        f"Generated: {now.strftime('%Y-%m-%d %H:%M %Z')}",
        f"Themes: {len(themes)}",
        "",
        "---",
        "",
        "## Table of Contents",
        "",
    ]
    for i, theme in enumerate(themes, 1):
        name = str(theme.get("theme", f"Theme {i}"))
        anchor = re.sub(r"[^a-z0-9 ]", "", name.lower()).replace(" ", "-")
        lines.append(f"{i}. [{name}](#{anchor})")

    lines.extend(["", "---", ""])
    for brief in theme_briefs:
        lines.extend([brief, "", "---", ""])

    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


def run_digest(
    cfg: LustroConfig,
    month: str | None,
    dry_run: bool,
    themes: int | None,
    model: str | None,
) -> tuple[list[dict[str, Any]], Path | None]:
    target_month = _resolve_month(month)
    max_themes = themes if themes is not None else DEFAULT_THEME_COUNT
    model_id = model or cfg.digest_model

    articles = load_archived_articles(cfg.article_cache_dir, target_month)
    log_entries = load_news_log_entries(cfg.log_path, target_month)
    if not articles and not log_entries:
        raise RuntimeError(
            f"No data found for {target_month}. Run `lustro fetch` first."
        )

    api_key = _get_api_key()
    if not api_key:
        raise RuntimeError(
            "Missing API key. Set `LUSTRO_API_KEY` or `OPENROUTER_API_KEY`."
        )
    client = create_openai_client(api_key)

    identified_themes = identify_themes(
        client=client,
        model=model_id,
        articles=articles,
        log_entries=log_entries,
        max_themes=max_themes,
    )
    if dry_run:
        return identified_themes, None

    briefs = [
        synthesize_theme(client, model_id, theme, articles, log_entries)
        for theme in identified_themes
    ]
    output_path = write_digest(
        output_dir=cfg.digest_output_dir,
        month=target_month,
        themes=identified_themes,
        theme_briefs=briefs,
    )
    return identified_themes, output_path
