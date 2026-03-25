from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from lustro.config import LustroConfig

DEFAULT_THEME_COUNT = 8
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Score thresholds for weekly digest secretion.
# Transcytose (★): cargo crosses the membrane and reaches the client surface.
WEEKLY_TRANSCYTOSE_THRESHOLD = 7
# Store: cargo retained in the endosome for later reference.
WEEKLY_STORE_THRESHOLD = 5


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
            r"^- (?:\[★\] )?\*\*(?:\[([^\]]+)\]\(([^)]+)\)|([^*]+))\*\*"
            r"(?:\s*\(banking_angle: ([^)]+)\))?"
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
                    "date": article_match.group(5) or current_date,
                    "link": article_match.group(2) or "",
                    "summary": article_match.group(6) or "",
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
            f"[{i}] {article.get('date', '')} | {article.get('source', '')}"
            f" | {article.get('title', '')}\n"
            f"    Summary: {article.get('summary', '')}\n"
            f"    Preview: {text_preview}"
        )

    offset = len(articles)
    for i, entry in enumerate(log_entries):
        items.append(
            f"[{offset + i}] {entry.get('date', '')} | {entry.get('source', '')}"
            f" | {entry.get('title', '')}\n"
            f"    Summary: {entry.get('summary', '')}"
        )

    system = (
        "You identify thematic clusters in AI news for a consultant"
        " advising banks on AI strategy.\n\n"
        f"Rules:\n- Identify {max_themes} themes most relevant"
        " to AI in banking/financial services\n"
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
        "Articles:\n" + "\n\n".join(items)
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
        "Source articles:\n\n" + "\n\n---\n\n".join(context_parts)
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


def _build_source_tags_map(cfg: LustroConfig) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for source in cfg.sources:
        name = source.get("name", "")
        tags = source.get("tags", ["ai"])
        if name:
            result[name] = tags if isinstance(tags, list) else [tags]
    return result


def _filter_by_tags(
    items: list[dict[str, Any]],
    tags: list[str],
    source_tags: dict[str, list[str]],
    source_key: str = "source",
) -> list[dict[str, Any]]:
    tag_set = set(tags)
    return [
        item for item in items
        if tag_set & set(source_tags.get(item.get(source_key, ""), ["ai"]))
    ]


# ---------------------------------------------------------------------------
# Weekly digest — lightweight endosome sorting, no LLM required.
# Substrate was already scored during fetch; this is pure membrane secretion.
# ---------------------------------------------------------------------------


def _resolve_week_label(week_date: datetime | None = None) -> tuple[str, str, str]:
    """Return (iso_date_start, iso_date_end, week_label) for the past 7 days.

    week_date: anchor datetime (defaults to now). The window is [anchor-7d, anchor].
    week_label format: YYYY-WNN (ISO year + zero-padded ISO week number).
    """
    if week_date is None:
        week_date = datetime.now(timezone.utc)
    end_date = week_date
    start_date = end_date - timedelta(days=7)
    # ISO week label — use the end date's ISO calendar to label the digest
    iso_year, iso_week, _ = end_date.isocalendar()
    week_label = f"{iso_year}-W{iso_week:02d}"
    return (
        start_date.strftime("%Y-%m-%d"),
        end_date.strftime("%Y-%m-%d"),
        week_label,
    )


def load_log_entries_since(log_path: Path, since_date: str) -> list[dict[str, str]]:
    """Internalize log entries from since_date (inclusive) to present.

    Parses the markdown log format written by lustro.log.format_markdown.
    Returns a list of dicts with: title, source, date, link, banking_angle,
    summary, _transcytose ('1' if item was marked with [★], else '0').
    """
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

        if current_date < since_date:
            continue

        # Parse article line: - [★] **[title](link)** (banking_angle: ...) (date) — summary
        article_match = re.match(
            r"^- (?:\[★\] )?\*\*(?:\[([^\]]+)\]\(([^)]+)\)|([^*]+))\*\*"
            r"(?:\s*\(banking_angle: ([^)]+)\))?"
            r"(?:\s*\(([^)]*)\))?"
            r"(?:\s*—\s*(.+))?",
            line,
        )
        if article_match:
            title = (article_match.group(1) or article_match.group(3) or "").strip()
            if not title:
                continue
            transcytose = "[★]" in line
            entries.append(
                {
                    "title": title,
                    "source": current_source,
                    "date": article_match.group(5) or current_date,
                    "link": article_match.group(2) or "",
                    "banking_angle": article_match.group(4) or "",
                    "summary": article_match.group(6) or "",
                    # Reconstruct approximate score from transcytose marker:
                    # ★ items were logged at score >= WEEKLY_TRANSCYTOSE_THRESHOLD
                    "_transcytose": "1" if transcytose else "0",
                }
            )
    return entries


def load_affinity_entries_since(since_date: str) -> list[dict[str, Any]]:
    """Load scored cargo from the affinity log for the weekly window.

    Affinity log has richer metadata (score, banking_angle, talking_point)
    than the markdown log. Used to enrich weekly digest items.
    """
    from lustro.relevance import AFFINITY_LOG, _read_jsonl

    cutoff = datetime.strptime(since_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    items: list[dict[str, Any]] = []
    for entry in _read_jsonl(AFFINITY_LOG):
        raw_ts = entry.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(str(raw_ts))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
        if ts >= cutoff:
            items.append(entry)
    return items


def _build_affinity_index(affinity_entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index affinity log entries by title for O(1) enrichment lookups."""
    index: dict[str, dict[str, Any]] = {}
    for entry in affinity_entries:
        title = str(entry.get("title", "")).strip()
        if title and title not in index:
            index[title] = entry
    return index


def write_weekly_digest(
    output_path: Path,
    week_label: str,
    since_date: str,
    until_date: str,
    entries: list[dict[str, str]],
    affinity_index: dict[str, dict[str, Any]],
) -> Path:
    """Secrete the weekly digest to markdown.

    Groups entries by source. Transcytose items (★) are surfaced first with
    banking_angle and talking_point for client conversations.
    Items with score < WEEKLY_STORE_THRESHOLD from the affinity log are omitted
    (lysosomal fate — degraded, not secreted).
    """
    now = datetime.now().astimezone()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Separate transcytose (★) items for the headline section
    transcytose_entries = [e for e in entries if e.get("_transcytose") == "1"]

    # Group all entries by source, filtering by score threshold
    by_source: dict[str, list[dict[str, str]]] = {}
    for entry in entries:
        title = entry["title"]
        # Look up score in affinity index; fall back to transcytose marker
        affinity = affinity_index.get(title, {})
        score = int(affinity.get("score", 0))
        if score == 0 and entry.get("_transcytose") == "1":
            # ★ items scored >= 7 at log time; use threshold as floor
            score = WEEKLY_TRANSCYTOSE_THRESHOLD
        if score < WEEKLY_STORE_THRESHOLD:
            # Low-signal cargo: lysosomal fate, not secreted in weekly digest
            continue
        source = entry.get("source", "Unknown")
        by_source.setdefault(source, []).append({**entry, "_score": str(score)})

    # Sort each source's entries by score descending
    for source in by_source:
        by_source[source].sort(key=lambda e: int(e.get("_score", "0")), reverse=True)

    lines: list[str] = [
        f"# Weekly AI Digest — {week_label}",
        "",
        f"Period: {since_date} to {until_date}",
        f"Generated: {now.strftime('%Y-%m-%d %H:%M %Z')}",
        "",
        "---",
        "",
    ]

    # Headline section: transcytose items surface to the client membrane first
    if transcytose_entries:
        lines.append("## Transcytose — High Signal (score >= 7)")
        lines.append("")
        lines.append(
            "_Items that crossed the membrane: ready for client conversation._"
        )
        lines.append("")
        for entry in transcytose_entries:
            title = entry["title"]
            link = entry.get("link", "")
            source = entry.get("source", "")
            affinity = affinity_index.get(title, {})
            banking_angle = entry.get("banking_angle") or str(
                affinity.get("banking_angle", "")
            )
            talking_point = str(affinity.get("talking_point", ""))
            title_md = f"[{title}]({link})" if link else title
            lines.append(f"- **{title_md}** — _{source}_")
            if banking_angle and banking_angle not in ("N/A", ""):
                lines.append(f"  - Banking angle: {banking_angle}")
            if talking_point and talking_point not in ("N/A", ""):
                lines.append(f"  - Talking point: {talking_point}")
        lines.append("")
        lines.append("---")
        lines.append("")

    # Per-source sections: grouped cargo
    if by_source:
        lines.append("## By Source")
        lines.append("")
        for source, source_entries in sorted(by_source.items()):
            lines.append(f"### {source}")
            lines.append("")
            for entry in source_entries:
                title = entry["title"]
                link = entry.get("link", "")
                score = entry.get("_score", "0")
                marker = "★ " if entry.get("_transcytose") == "1" else ""
                title_md = f"[{title}]({link})" if link else title
                summary = entry.get("summary", "")
                summary_part = f" — {summary}" if summary else ""
                lines.append(f"- {marker}**{title_md}** [{score}/10]{summary_part}")
            lines.append("")
    else:
        lines.append("_No items met the score threshold this week._")
        lines.append("")

    # Scheduling note: wire for Sunday night so Monday brief has fresh signal
    lines.extend([
        "---",
        "",
        "<!-- Schedule: run Sunday ~22:00 HKT so Monday morning brief has fresh signal -->",
        "<!-- Command: lustro digest --weekly -->",
        "",
    ])

    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


def run_weekly_digest(
    cfg: LustroConfig,
    week_date: datetime | None = None,
    tags: list[str] | None = None,
) -> tuple[int, Path | None]:
    """Secrete the weekly digest — lightweight endosome sorting, no LLM call.

    Returns (item_count, output_path). No LLM is used: scoring was already
    performed during fetch. This is pure membrane secretion from cached signal.
    """
    since_date, until_date, week_label = _resolve_week_label(week_date)

    # Internalize log entries for the window
    entries = load_log_entries_since(cfg.log_path, since_date)

    if tags:
        source_tags = _build_source_tags_map(cfg)
        entries = [
            e for e in entries
            if set(tags) & set(source_tags.get(e.get("source", ""), ["ai"]))
        ]

    # Enrich with affinity log for score + talking_point data
    affinity_entries = load_affinity_entries_since(since_date)
    affinity_index = _build_affinity_index(affinity_entries)

    # Secrete to ~/notes/Reference/weekly-ai-digest-YYYY-WNN.md
    output_dir = Path.home() / "notes" / "Reference"
    output_path = output_dir / f"weekly-ai-digest-{week_label}.md"

    written_path = write_weekly_digest(
        output_path=output_path,
        week_label=week_label,
        since_date=since_date,
        until_date=until_date,
        entries=entries,
        affinity_index=affinity_index,
    )

    # Count items that made it through the score threshold
    item_count = sum(
        1 for e in entries
        if (
            int(affinity_index.get(e["title"], {}).get("score", 0)) >= WEEKLY_STORE_THRESHOLD
            or e.get("_transcytose") == "1"
        )
    )
    return item_count, written_path


def run_digest(
    cfg: LustroConfig,
    month: str | None,
    dry_run: bool,
    themes: int | None,
    model: str | None,
    tags: list[str] | None = None,
) -> tuple[list[dict[str, Any]], Path | None]:
    target_month = _resolve_month(month)
    max_themes = themes if themes is not None else DEFAULT_THEME_COUNT
    model_id = model or cfg.digest_model

    articles = load_archived_articles(cfg.article_cache_dir, target_month)
    log_entries = load_news_log_entries(cfg.log_path, target_month)

    if tags:
        source_tags = _build_source_tags_map(cfg)
        articles = _filter_by_tags(articles, tags, source_tags)
        log_entries = _filter_by_tags(log_entries, tags, source_tags)
    if not articles and not log_entries:
        raise RuntimeError(f"No data found for {target_month}. Run `lustro fetch` first.")

    api_key = _get_api_key()
    if not api_key:
        raise RuntimeError("Missing API key. Set `LUSTRO_API_KEY` or `OPENROUTER_API_KEY`.")
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
