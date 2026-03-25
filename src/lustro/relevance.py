from __future__ import annotations

"""Score news items for consulting relevance."""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.join(os.path.expanduser("~"), "code", "vivesca", "lib"))
from llm import query as _llm_query

AFFINITY_LOG = Path.home() / ".cache" / "lustro" / "relevance.jsonl"
RECYCLING_LOG = Path.home() / ".cache" / "lustro" / "engagement.jsonl"

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


def score_cargo(title: str, source: str, summary: str) -> dict[str, Any]:
    """Score a single cargo item using Gemini, with keyword fallback."""
    prompt = SCORING_PROMPT.format(title=title, source=source, summary=summary)

    try:
        text = _llm_query("gemini-flash", prompt, timeout=30)
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            payload = json.loads(text[start:end])
            if isinstance(payload, dict):
                result = _normalize_score_payload(payload)
                # Receptor recycling: post-adjust LLM score with engagement affinity signal
                boost = _engagement_boost(title, source)
                result["score"] = max(1, min(result["score"] + boost, 10))
                return result
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError, Exception):
        pass

    return _keyword_score(title, summary, source=source)


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


def _engagement_boost(title: str, source: str) -> int:
    """Receptor recycling: return a score adjustment based on prior engagement signals.

    After endocytosis (engagement), receptors return to the cell surface with
    updated affinity. Sources that generated genuine engagement get a +1 boost;
    sources with repeated false-positive signals (high-scored but never engaged)
    accumulate a -1 penalty.

    Args:
        title: Item title (used to check if this specific item was ever engaged).
        source: Feed source name (used to compute source-level affinity).

    Returns:
        +1 if the source has affinity (prior engagement on any item from this source),
        -1 if the source is a false-positive emitter (scored >=7 repeatedly, never engaged),
         0 otherwise.
    """
    scored_rows = _read_jsonl(AFFINITY_LOG)
    engaged_rows = _read_jsonl(RECYCLING_LOG)

    if not scored_rows:
        return 0

    # Build set of engaged titles for cross-referencing
    engaged_titles: set[str] = {str(r.get("title", "")) for r in engaged_rows if r.get("title")}

    # Source affinity: any previously engaged item came from this source
    source_engaged = any(
        str(r.get("source", "")) == source and str(r.get("title", "")) in engaged_titles
        for r in scored_rows
    )
    if source_engaged:
        return 1

    # False-positive signal: source has >=2 high-scored items (score>=7) with zero engagement
    source_high_scored = [
        r for r in scored_rows if str(r.get("source", "")) == source and int(r.get("score", 0)) >= 7
    ]
    source_high_engaged_count = sum(
        1 for r in source_high_scored if str(r.get("title", "")) in engaged_titles
    )
    if len(source_high_scored) >= 2 and source_high_engaged_count == 0:
        return -1

    return 0


def _keyword_score(title: str, summary: str, source: str = "") -> dict[str, Any]:
    """Simple keyword-based affinity scoring as fallback.

    Applies receptor recycling via _engagement_boost so the deterministic
    fallback accumulates affinity signal over time without needing the LLM.
    """
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

    # Receptor recycling: apply engagement-derived affinity boost before returning
    boost = _engagement_boost(title, source)
    score = max(1, min(score + boost, 10))

    return {"score": score, "banking_angle": "N/A", "talking_point": "N/A"}


def log_affinity(item: dict[str, Any], scores: dict[str, Any]) -> None:
    """Append scored cargo to the affinity log."""
    AFFINITY_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": item.get("timestamp", ""),
        "title": item.get("title", ""),
        "source": item.get("source", ""),
        "score": scores.get("score", 0),
        "banking_angle": scores.get("banking_angle", ""),
        "talking_point": scores.get("talking_point", ""),
    }
    with AFFINITY_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def log_recycling(title: str, action: str = "deepened") -> None:
    """Log when the user engages with cargo (endosomal recycling signal)."""
    RECYCLING_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "title": title,
        "action": action,
    }
    with RECYCLING_LOG.open("a", encoding="utf-8") as fh:
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


def get_receptor_signal_ratio(source: str, window_days: int = 30) -> float:
    """Measure the signal-to-noise ratio for a receptor (source) over a time window.

    Receptors chronically overstimulated by low-relevance ligands (articles)
    are candidates for downregulation — fetched less frequently so the cell is
    not flooded with noise.

    Returns the fraction of items from this source scoring >= 5 within the
    window.  Returns 1.0 (assume high signal) when fewer than 5 items have been
    scored — insufficient stimulus history to trigger downregulation.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    total = 0
    signal = 0
    for entry in _read_jsonl(AFFINITY_LOG):
        if entry.get("source") != source:
            continue
        raw_timestamp = entry.get("timestamp")
        try:
            timestamp = datetime.fromisoformat(str(raw_timestamp))
        except (ValueError, TypeError):
            continue
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        if timestamp < cutoff:
            continue
        total += 1
        score = entry.get("score", 0)
        try:
            if int(score) >= 5:
                signal += 1
        except (TypeError, ValueError):
            pass
    # Insufficient stimulus history: receptor stays at baseline sensitivity
    if total < 5:
        return 1.0
    return signal / total


def get_affinity_stats() -> dict[str, Any]:
    """Analyse affinity vs recycling to find scoring gaps."""
    scored_rows = _read_jsonl(AFFINITY_LOG)
    engaged_rows = _read_jsonl(RECYCLING_LOG)
    if not scored_rows or not engaged_rows:
        return {"status": "insufficient_data"}

    scored = {
        str(entry.get("title", "")): int(entry.get("score", 0))
        for entry in scored_rows
        if entry.get("title")
    }
    engaged = {str(entry.get("title", "")) for entry in engaged_rows if entry.get("title")}

    false_negatives = sorted(title for title in engaged if scored.get(title, 5) < 5)
    false_positives = sorted(
        title for title, score in scored.items() if score >= 7 and title not in engaged
    )

    return {
        "status": "ok",
        "total_scored": len(scored),
        "total_engaged": len(engaged),
        "false_negatives": false_negatives[:5],
        "false_positives_count": len(false_positives),
        "avg_engaged_score": sum(scored.get(title, 0) for title in engaged) / max(len(engaged), 1),
    }


def get_top_cargo(limit: int = 10, days: int = 7) -> list[dict[str, Any]]:
    """Return the highest-scored cargo in the recent window."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    items: list[dict[str, Any]] = []
    for entry in _read_jsonl(AFFINITY_LOG):
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
