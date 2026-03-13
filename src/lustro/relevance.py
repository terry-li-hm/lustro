from __future__ import annotations

"""Score news items for consulting relevance."""

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

RELEVANCE_LOG = Path.home() / ".cache" / "lustro" / "relevance.jsonl"
ENGAGEMENT_LOG = Path.home() / ".cache" / "lustro" / "engagement.jsonl"

SCORING_PROMPT = """Rate this AI news item for relevance to a Principal Consultant / AI Solution Lead
advising bank clients. Score 1-10:

10 = Must-know for client meetings (regulatory change, major vendor announcement affecting banks)
7-9 = High relevance (new AI capability with clear banking/fintech application)
4-6 = Moderate (general AI development, might come up in conversation)
1-3 = Low (academic, consumer-focused, or not applicable to financial services)

Also provide:
- banking_angle: one sentence on why a bank client would care (or "N/A")
- talking_point: one sentence that could be used in a client meeting (or "N/A")

News item:
Title: {title}
Source: {source}
Summary: {summary}

Respond in JSON only:
{{"score": N, "banking_angle": "...", "talking_point": "..."}}
"""


def score_item(title: str, source: str, summary: str) -> dict[str, Any]:
    """Score a single news item using Gemini, with keyword fallback."""
    prompt = SCORING_PROMPT.format(title=title, source=source, summary=summary)

    try:
        result = subprocess.run(
            ["gemini", "-m", "gemini-2.0-flash", "-p", prompt, "--yolo"],
            capture_output=True,
            text=True,
            timeout=30,
            env={k: v for k, v in os.environ.items() if k != "CLAUDECODE"},
        )
        if result.returncode == 0:
            text = result.stdout.strip()
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                payload = json.loads(text[start:end])
                if isinstance(payload, dict):
                    return _normalize_score_payload(payload)
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        pass

    return _keyword_score(title, summary)


def _normalize_score_payload(payload: dict[str, Any]) -> dict[str, Any]:
    score = payload.get("score", 0)
    try:
        numeric_score = int(score)
    except (TypeError, ValueError):
        numeric_score = 0
    return {
        "score": max(1, min(numeric_score, 10)),
        "banking_angle": str(payload.get("banking_angle", "N/A") or "N/A"),
        "talking_point": str(payload.get("talking_point", "N/A") or "N/A"),
    }


def _keyword_score(title: str, summary: str) -> dict[str, Any]:
    """Simple keyword-based relevance scoring as fallback."""
    text = f"{title} {summary}".lower()
    score = 2

    high = [
        "bank",
        "banking",
        "financial services",
        "regulatory",
        "compliance",
        "hkma",
        "sfc",
        "aml",
        "kyc",
        "fraud",
        "model risk",
        "sr 11-7",
        "mas",
        "fintech",
        "wealth management",
        "insurance",
        "capital markets",
    ]
    medium = [
        "enterprise",
        "agent",
        "deployment",
        "production",
        "governance",
        "evaluation",
        "benchmark",
        "safety",
        "audit",
        "risk",
    ]
    low = [
        "consumer",
        "gaming",
        "smartphone",
        "photo filter",
        "shopping",
        "social media",
        "creator",
        "entertainment",
    ]

    for kw in high:
        if kw in text:
            score = min(score + 2, 10)
    for kw in medium:
        if kw in text:
            score = min(score + 1, 10)
    for kw in low:
        if kw in text:
            score = max(score - 1, 1)

    return {"score": score, "banking_angle": "N/A", "talking_point": "N/A"}


def log_score(item: dict[str, Any], scores: dict[str, Any]) -> None:
    """Append scored item to the relevance log."""
    RELEVANCE_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": item.get("timestamp", ""),
        "title": item.get("title", ""),
        "source": item.get("source", ""),
        "score": scores.get("score", 0),
        "banking_angle": scores.get("banking_angle", ""),
        "talking_point": scores.get("talking_point", ""),
    }
    with RELEVANCE_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def log_engagement(title: str, action: str = "deepened") -> None:
    """Log when the user engages with an item."""
    ENGAGEMENT_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "title": title,
        "action": action,
    }
    with ENGAGEMENT_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def get_stats() -> dict[str, Any]:
    """Analyse relevance vs engagement to find scoring gaps."""
    scored_rows = _read_jsonl(RELEVANCE_LOG)
    engaged_rows = _read_jsonl(ENGAGEMENT_LOG)
    if not scored_rows or not engaged_rows:
        return {"status": "insufficient_data"}

    scored = {str(entry.get("title", "")): int(entry.get("score", 0)) for entry in scored_rows if entry.get("title")}
    engaged = {str(entry.get("title", "")) for entry in engaged_rows if entry.get("title")}

    false_negatives = sorted(title for title in engaged if scored.get(title, 5) < 5)
    false_positives = sorted(title for title, score in scored.items() if score >= 7 and title not in engaged)

    return {
        "status": "ok",
        "total_scored": len(scored),
        "total_engaged": len(engaged),
        "false_negatives": false_negatives[:5],
        "false_positives_count": len(false_positives),
        "avg_engaged_score": sum(scored.get(title, 0) for title in engaged) / max(len(engaged), 1),
    }


def get_top_items(limit: int = 10, days: int = 7) -> list[dict[str, Any]]:
    """Return the highest-scored items in the recent window."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    items: list[dict[str, Any]] = []
    for entry in _read_jsonl(RELEVANCE_LOG):
        raw_timestamp = entry.get("timestamp")
        try:
            timestamp = datetime.fromisoformat(str(raw_timestamp))
        except ValueError:
            continue
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        if timestamp < cutoff:
            continue
        items.append(entry)
    items.sort(
        key=lambda item: (
            int(item.get("score", 0)),
            str(item.get("timestamp", "")),
        ),
        reverse=True,
    )
    return items[:limit]
