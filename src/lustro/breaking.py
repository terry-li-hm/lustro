from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from lustro.config import LustroConfig
from lustro.fetcher import internalize_rss, internalize_web
from lustro.log import append_to_log
from lustro.state import lockfile

ALERT_SIGNAL_LOG = Path.home() / ".cache" / "lustro" / "alert-signals.jsonl"

MAX_ALERTS_PER_DAY = 3
COOLDOWN_MINUTES = 60
MAX_SEEN_IDS = 200

ENTITIES = re.compile(
    r"(?i)\b("
    r"anthropic|openai|open\s?ai|google\s?deepmind|deepmind|meta\s?ai|"
    r"mistral|x\.?ai|grok|"
    r"hkma|mas|sec|eu\s?ai\s?act|pboc|"
    r"gpt[-\s]?\d|claude[-\s]?\d|gemini[-\s]?\d|llama[-\s]?\d|"
    r"o[1-9][-\s]|sonnet|opus|haiku|"
    r"codex"
    r")\b"
)
ACTIONS = re.compile(
    r"(?i)\b("
    r"launch|launches|launched|"
    r"release|releases|released|"
    r"introduc|announc|unveil|"
    r"open.?sourc|"
    r"acquir|merg|shut.?down|"
    r"ban[s\b]|mandat|"
    r"available|publishes|published|enters|entered"
    r")"
)
NEGATIVE = re.compile(
    r"(?i)\b("
    r"partner|collaborat|"
    r"hiring|hire[sd]|recrui|"
    r"podcast|interview|webinar|"
    r"round|funding|series\s[a-d]"
    r")\b"
)


BREAKING_FRESHNESS_HOURS = 2


def _article_is_fresh(
    article: dict[str, Any], now: datetime, max_hours: float = BREAKING_FRESHNESS_HOURS
) -> bool:
    """Return True if the article was published within ``max_hours`` of ``now``.

    Reads the ``published_at`` field (ISO 8601 UTC string) added by
    ``fetch_rss``.  If the field is absent or unparseable the article is
    treated as fresh so the breaking label is NOT suppressed — fail open.
    """
    raw = article.get("published_at", "")
    if not raw:
        return True  # no date available → don't suppress
    try:
        pub = datetime.fromisoformat(str(raw))
    except (ValueError, TypeError):
        return True  # malformed date → don't suppress
    if pub.tzinfo is None:
        pub = pub.replace(tzinfo=timezone.utc)
    age = (now - pub).total_seconds()
    return age <= max_hours * 3600


def is_breaking(title: str) -> bool:
    if not ENTITIES.search(title):
        return False
    if not ACTIONS.search(title):
        return False
    if NEGATIVE.search(title):
        return False
    return True


def article_hash(title: str, link: str, source: str) -> str:
    raw = f"{title}|{link}|{source}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def title_fingerprint(title: str) -> str:
    """Normalised fingerprint of a title for cross-source dedup.

    Lowercases, strips non-alphanumeric characters, and hashes.  Two titles
    that differ only in punctuation, case, or whitespace will share the same
    fingerprint.  Used within a single run to suppress duplicate alerts for
    the same story appearing in multiple feeds.
    """
    normalised = re.sub(r"[^a-z0-9]", "", title.lower())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:16]


def load_breaking_state(path: Path, now: datetime) -> dict[str, Any]:
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
        except (OSError, json.JSONDecodeError):
            pass
    return {
        "last_check": None,
        "seen_ids": [],
        "alerts_today": 0,
        "today_date": now.date().isoformat(),
        "last_alert_time": None,
    }


def save_breaking_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, indent=2, sort_keys=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(payload)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def reset_daily_counter(state: dict[str, Any], now: datetime) -> None:
    today = now.date().isoformat()
    if state.get("today_date") != today:
        state["alerts_today"] = 0
        state["today_date"] = today


def can_alert(state: dict[str, Any], now: datetime) -> bool:
    if int(state.get("alerts_today", 0)) >= MAX_ALERTS_PER_DAY:
        return False
    last = state.get("last_alert_time")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(str(last))
    except ValueError:
        return True
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    return (now - last_dt).total_seconds() >= COOLDOWN_MINUTES * 60


def _resolve_tg_notify(cfg_path: str | None = None) -> str | None:
    if cfg_path:
        return cfg_path if Path(cfg_path).is_file() else None
    found = shutil.which("tg-notify.sh")
    if found:
        return found
    fallback = Path.home() / "scripts" / "tg-notify.sh"
    return str(fallback) if fallback.is_file() else None


def _age_minutes(published_at: str, now: datetime) -> float | None:
    """Return article age in minutes from published_at ISO string, or None if unavailable."""
    if not published_at:
        return None
    try:
        pub = datetime.fromisoformat(str(published_at))
    except (ValueError, TypeError):
        return None
    if pub.tzinfo is None:
        pub = pub.replace(tzinfo=timezone.utc)
    return round((now - pub).total_seconds() / 60, 1)


def append_alert_signal(
    path: Path,
    *,
    timestamp: str,
    title: str,
    source: str,
    url: str,
    published_at: str,
    age_minutes: float | None,
    was_breaking: bool,
    alert_sent: bool,
    throttled: bool,
    suppressed_stale: bool,
) -> None:
    """Append one record to the alert-signals JSONL log (atomic line append).

    Schema:
      timestamp       – ISO 8601 UTC — when lustro breaking ran
      title           – article title
      source          – source name from sources.yaml
      url             – article link
      published_at    – article's own publication timestamp (ISO 8601) or ""
      age_minutes     – float minutes old at detection time, or null
      was_breaking    – passed is_breaking() title filter
      alert_sent      – Telegram alert was dispatched
      throttled       – matched breaking but held back by daily cap / cooldown
      suppressed_stale – matched breaking but failed freshness gate
    """
    record = {
        "timestamp": timestamp,
        "title": title,
        "source": source,
        "url": url,
        "published_at": published_at,
        "age_minutes": age_minutes,
        "was_breaking": was_breaking,
        "alert_sent": alert_sent,
        "throttled": throttled,
        "suppressed_stale": suppressed_stale,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _source_candidates(cfg: LustroConfig) -> list[dict[str, Any]]:
    web_sources = cfg.sources_data.get("web_sources", [])
    if not isinstance(web_sources, list):
        return []
    return [
        source
        for source in web_sources
        if isinstance(source, dict)
        and int(source.get("tier", 2)) == 1
        and (source.get("rss") or source.get("url"))
    ]


def _send_alert(
    title: str,
    link: str,
    source: str,
    now: datetime,
    dry_run: bool,
    tg_notify_path: str | None = None,
) -> None:
    if link:
        msg = f"🚨 *Breaking:* [{title}]({link})\nSource: {source} • {now.strftime('%H:%M')} UTC"
    else:
        msg = f"🚨 *Breaking:* {title}\nSource: {source} • {now.strftime('%H:%M')} UTC"

    if dry_run:
        print(f"[DRY RUN] {msg}", file=sys.stderr)
        return

    tg_notify = _resolve_tg_notify(tg_notify_path)
    if tg_notify is None:
        print("tg-notify.sh not found; skipping Telegram send.", file=sys.stderr)
        return

    try:
        subprocess.run(
            [tg_notify], input=msg, text=True, check=True, capture_output=True, timeout=30
        )
    except Exception as exc:
        print(f"Telegram error: {exc}", file=sys.stderr)


def _append_breaking_log(cfg: LustroConfig, matches: list[dict[str, str]], now: datetime) -> None:
    if not matches:
        return
    lines = [f"## {now.strftime('%Y-%m-%d')} (Breaking Alerts)\n", "### Breaking AI News\n"]
    for match in matches:
        title = match["title"]
        link = match.get("link", "")
        source = match["source"]
        title_part = f"[{title}]({link})" if link else title
        lines.append(f"- 🚨 **{title_part}** ({source})")
    append_to_log(cfg.log_path, "\n".join(lines) + "\n")


def run_breaking(
    cfg: LustroConfig,
    dry_run: bool = False,
    now: datetime | None = None,
    state_path: Path | None = None,
) -> int:
    if now is None:
        now = datetime.now(timezone.utc)
    if state_path is None:
        state_path = cfg.cache_dir / "breaking-state.json"

    with lockfile(state_path):
        return _run_breaking_locked(cfg, dry_run, now, state_path)


def _run_breaking_locked(
    cfg: LustroConfig,
    dry_run: bool,
    now: datetime,
    state_path: Path,
) -> int:
    state = load_breaking_state(state_path, now)
    reset_daily_counter(state, now)

    seen_list = [str(value) for value in state.get("seen_ids", []) if isinstance(value, str)]
    seen_set = set(seen_list)
    # Cross-source dedup: tracks normalised title fingerprints within this run.
    # If two feeds carry the same story (same title, different source), only the
    # first occurrence fires an alert.
    # TODO: persist title fingerprints across runs so that stories seen in a
    # previous wave are also suppressed (currently dedup is intra-run only).
    content_seen_this_run: set[str] = set()
    since_date = (now - timedelta(days=2)).strftime("%Y-%m-%d")
    matches: list[dict[str, str]] = []
    signal_log_path = ALERT_SIGNAL_LOG
    now_iso = now.isoformat()

    print(f"[{now.strftime('%Y-%m-%d %H:%M')} UTC] Breaking news check", file=sys.stderr)

    for source in _source_candidates(cfg):
        source_name = str(source.get("name", "Unknown Source"))
        if source.get("rss"):
            articles = internalize_rss(str(source["rss"]), since_date, max_items=10)
            if articles is None and source.get("url"):
                articles = internalize_web(str(source["url"]), max_items=8)
            articles = articles or []
        else:
            articles = internalize_web(str(source.get("url", "")), max_items=8)

        for article in articles:
            title = str(article.get("title", "")).strip()
            link = str(article.get("link", "")).strip()
            if not title:
                continue
            digest = article_hash(title, link, source_name)
            if digest in seen_set:
                continue
            seen_set.add(digest)
            seen_list.append(digest)
            published_at = str(article.get("published_at", ""))
            age_mins = _age_minutes(published_at, now)
            breaking = is_breaking(title)
            if breaking:
                # Cross-source dedup: suppress if an equivalent story already
                # matched from a different feed in this same run.
                fp = title_fingerprint(title)
                if fp in content_seen_this_run:
                    print(
                        f"  Cross-source dedup: suppressed duplicate story from {source_name}: {title}",
                        file=sys.stderr,
                    )
                    continue
                content_seen_this_run.add(fp)

                fresh = _article_is_fresh(article, now)
                if not fresh:
                    print(
                        f"  Freshness gate: suppressed breaking label for stale article "
                        f"(published_at={published_at or 'unknown'}): {title}",
                        file=sys.stderr,
                    )
                    append_alert_signal(
                        signal_log_path,
                        timestamp=now_iso,
                        title=title,
                        source=source_name,
                        url=link,
                        published_at=published_at,
                        age_minutes=age_mins,
                        was_breaking=True,
                        alert_sent=False,
                        throttled=False,
                        suppressed_stale=True,
                    )
                    continue
                matches.append(
                    {
                        "title": title,
                        "link": link,
                        "source": source_name,
                        "published_at": published_at,
                    }
                )

    if len(seen_list) > MAX_SEEN_IDS:
        seen_list = seen_list[-MAX_SEEN_IDS:]

    state["seen_ids"] = seen_list
    state["last_check"] = now.isoformat()

    if not matches:
        print("No breaking news.", file=sys.stderr)
        save_breaking_state(state_path, state)
        return 0

    print(f"{len(matches)} breaking match(es) found.", file=sys.stderr)
    sent_matches: list[dict[str, str]] = []
    for match in matches:
        match_pub = str(match.get("published_at", ""))
        match_age = _age_minutes(match_pub, now)
        throttled = not dry_run and not can_alert(state, now)
        if throttled:
            print(f"Throttled: {match['title']}", file=sys.stderr)
            append_alert_signal(
                signal_log_path,
                timestamp=now_iso,
                title=match["title"],
                source=match["source"],
                url=match.get("link", ""),
                published_at=match_pub,
                age_minutes=match_age,
                was_breaking=True,
                alert_sent=False,
                throttled=True,
                suppressed_stale=False,
            )
            continue
        _send_alert(
            match["title"],
            match.get("link", ""),
            match["source"],
            now,
            dry_run,
            tg_notify_path=cfg.tg_notify_path,
        )
        if not dry_run:
            state["alerts_today"] = int(state.get("alerts_today", 0)) + 1
            state["last_alert_time"] = now.isoformat()
            sent_matches.append(match)
            append_alert_signal(
                signal_log_path,
                timestamp=now_iso,
                title=match["title"],
                source=match["source"],
                url=match.get("link", ""),
                published_at=match_pub,
                age_minutes=match_age,
                was_breaking=True,
                alert_sent=True,
                throttled=False,
                suppressed_stale=False,
            )

    if not dry_run:
        _append_breaking_log(cfg, sent_matches, now)

    save_breaking_state(state_path, state)
    return 0
