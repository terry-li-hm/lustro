from __future__ import annotations

import importlib.metadata
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import typer

from lustro.config import LustroConfig, default_sources_text, load_config
from lustro.state import load_state, lockfile

app = typer.Typer(help="Lustro — AI news aggregator")


def version_callback(value: bool) -> None:
    if value:
        typer.echo(f"lustro {_get_version()}")
        raise typer.Exit()


@app.callback()
def main_callback(
    version: bool = typer.Option(False, "--version", callback=version_callback, is_eager=True),
) -> None:
    pass


def _get_version() -> str:
    try:
        return importlib.metadata.version("lustro")
    except importlib.metadata.PackageNotFoundError:
        return "dev"


def _file_age(path: Path, now: datetime) -> str:
    if not path.exists():
        return "missing"
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=now.tzinfo)
    delta = now - modified
    if delta.total_seconds() < 60:
        return "just now"
    if delta.total_seconds() < 3600:
        return f"{int(delta.total_seconds() // 60)}m ago"
    if delta.total_seconds() < 86400:
        return f"{int(delta.total_seconds() // 3600)}h ago"
    return f"{delta.days}d ago"


def _parse_aware(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _get_last_scan_date(state: dict[str, str]) -> str:
    dates = []
    for value in state.values():
        dt = _parse_aware(value)
        if dt is not None:
            dates.append(dt)
    if dates:
        return max(dates).strftime("%Y-%m-%d")
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")


_CADENCE_LOOKBACK: dict[str, int] = {
    "daily": 2,
    "twice_weekly": 5,
    "weekly": 10,
    "biweekly": 20,
    "monthly": 35,
}


def _source_since_date(
    state: dict[str, str], name: str, fallback: str, cadence: str = "daily", now: datetime | None = None
) -> str:
    """Per-source since_date: own last-scan timestamp, or cadence-appropriate lookback for new sources."""
    val = state.get(name)
    if val:
        dt = _parse_aware(val)
        if dt is not None:
            return dt.strftime("%Y-%m-%d")
    # New source: use cadence-aware lookback instead of global since_date
    # (global date is "today" when other sources ran today, causing new sources to find nothing)
    lookback_days = _CADENCE_LOOKBACK.get(cadence, 2)
    if now is None:
        now = datetime.now(timezone.utc)
    lookback = (now - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    # Use whichever gives more history
    return min(fallback, lookback)


@app.command()
def fetch(
    no_archive: bool = typer.Option(False, "--no-archive", help="Skip archiving full article text"),
) -> None:
    cfg = load_config()
    with lockfile(cfg.state_path):
        _fetch_locked(cfg, no_archive)


def _fetch_locked(cfg: LustroConfig, no_archive: bool) -> None:
    state = load_state(cfg.state_path)
    from lustro.fetcher import archive_article, fetch_linkedin_company, fetch_rss, fetch_web, fetch_x_account, fetch_x_bookmarks, unbookmark_tweets
    from lustro.log import (
        _title_prefix,
        append_to_log,
        format_markdown,
        is_junk,
        load_title_prefixes,
        rotate_log,
    )
    from lustro.state import save_state, should_fetch

    now = datetime.now(timezone.utc)
    rotate_log(cfg.log_path, cfg.data_dir, cfg.config_data.get("max_log_lines", 500), now)

    global_since_date = _get_last_scan_date(state)
    title_prefixes = load_title_prefixes(cfg.log_path)
    results: dict[str, list[dict[str, str]]] = {}
    failed_sources: list[str] = []
    archived_count = 0
    bookmark_ids_to_clear: list[str] = []
    _nodriver_profile = Path(
        cfg.config_data.get(
            "nodriver_profile_dir",
            Path.home() / ".config" / "lustro" / "nodriver-profile",
        )
    )

    for source in cfg.sources:
        name = source["name"]
        cadence = source.get("cadence", "daily")
        tier = source.get("tier", 2)
        if not should_fetch(state, name, cadence, now=now):
            continue
        typer.echo(f"Fetching: {name}...", err=True)
        fetch_failed = False
        since_date = _source_since_date(state, name, global_since_date, cadence=cadence, now=now)
        if source.get("bookmarks"):
            articles = fetch_x_bookmarks(since_date, bird_path=cfg.resolve_bird())
        elif "rss" in source:
            articles = fetch_rss(
                source["rss"],
                since_date,
                full_fetch=bool(source.get("full_fetch", False)),
                stealth_fetch=bool(source.get("stealth_fetch", False)),
                profile_dir=_nodriver_profile,
            )
            if articles is None:
                if "url" in source:
                    typer.echo(f"  RSS dead, falling back to web: {source['url']}", err=True)
                    articles = fetch_web(
                        source["url"],
                        selector=source.get("selector"),
                        stealth=bool(source.get("stealth_web", False)),
                        profile_dir=_nodriver_profile,
                    )
                if articles is None:
                    fetch_failed = True
                    failed_sources.append(f"{name} (RSS dead)")
            articles = articles or []
        elif "handle" in source:
            articles = fetch_x_account(source["handle"], since_date, bird_path=cfg.resolve_bird())
        elif "linkedin" in source:
            articles = fetch_linkedin_company(
                source["linkedin"],
                since_date,
                agent_browser_bin=cfg.config_data.get("agent_browser_bin", "agent-browser"),
            )
            if articles is None:
                fetch_failed = True
                failed_sources.append(f"{name} (linkedin error)")
                articles = []
        else:
            articles = fetch_web(
                source.get("url", ""),
                selector=source.get("selector"),
                stealth=bool(source.get("stealth_web", False)),
                profile_dir=_nodriver_profile,
            )
            if articles is None:
                fetch_failed = True
                failed_sources.append(f"{name} (web error)")
                articles = []

        if fetch_failed:
            f_key = f"_fail:{name}"
            state[f_key] = str(int(state.get(f_key, 0)) + 1)

        new_articles = []
        for article in articles:
            if is_junk(article["title"]):
                continue
            prefix = _title_prefix(article["title"])
            if prefix in title_prefixes:
                continue
            new_articles.append(article)
            title_prefixes.add(prefix)

        if not no_archive:
            for article in new_articles:
                if article.get("link") and tier == 1:
                    archive_article(article, name, tier, cfg.article_cache_dir, now)
                    archived_count += 1

        if source.get("bookmarks"):
            for article in new_articles:
                tid = article.pop("_tweet_id", "")
                if tid:
                    bookmark_ids_to_clear.append(tid)

        if new_articles:
            results[name] = new_articles
            state[name] = now.isoformat()
            state.pop(f"_zeros:{name}", None)
        else:
            if name not in state:
                state[name] = now.isoformat()
            z_key = f"_zeros:{name}"
            zeros = int(state.get(z_key, 0)) + 1
            state[z_key] = str(zeros)
            _zero_threshold = {"daily": 5, "twice_weekly": 4, "weekly": 3, "biweekly": 2, "monthly": 2}.get(cadence, 5)
            if zeros >= _zero_threshold:
                typer.echo(
                    f"  Warning: {name} has {zeros} consecutive zero-article fetches",
                    err=True,
                )

    save_state(cfg.state_path, state)
    if failed_sources:
        typer.echo(f"Fetch errors ({len(failed_sources)}): {', '.join(failed_sources)}", err=True)
    if not results:
        typer.echo("No new articles found.", err=True)
        raise typer.Exit(code=0)
    today = now.strftime("%Y-%m-%d")
    md = format_markdown(results, today)
    append_to_log(cfg.log_path, md)
    total = sum(len(v) for v in results.values())
    typer.echo(f"Logged {total} new articles.", err=True)
    if bookmark_ids_to_clear:
        unbookmark_tweets(bookmark_ids_to_clear, bird_path=cfg.resolve_bird())
    raise typer.Exit(code=0)


@app.command()
def check() -> None:
    cfg = load_config()
    state = load_state(cfg.state_path)
    from lustro.fetcher import check_sources

    web_sources = [s for s in cfg.sources if "handle" not in s and not s.get("bookmarks")]
    x_accounts = [s for s in cfg.sources if "handle" in s]
    x_bookmarks = [s for s in cfg.sources if s.get("bookmarks")]
    check_sources(web_sources, x_accounts, state, bird_path=cfg.resolve_bird(), x_bookmarks=x_bookmarks)
    raise typer.Exit(code=0)


@app.command()
def digest(
    month: Optional[str] = typer.Option(None, "--month", help="Target month YYYY-MM"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show themes only"),
    themes: Optional[int] = typer.Option(None, "--themes", help="Max themes"),
    model: Optional[str] = typer.Option(None, "--model", help="Model ID"),
    tag: Optional[list[str]] = typer.Option(None, "--tag", "-t", help="Filter by tag (repeatable)"),
) -> None:
    cfg = load_config()
    from lustro.digest import run_digest

    try:
        themes_result, output_path = run_digest(
            cfg=cfg,
            month=month,
            dry_run=dry_run,
            themes=themes,
            model=model,
            tags=tag or [],
        )
    except RuntimeError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Found {len(themes_result)} themes.", err=True)
    for i, theme in enumerate(themes_result, 1):
        name = theme.get("theme", f"Theme {i}")
        count = len(theme.get("article_indices", []))
        typer.echo(f"{i}. {name} ({count} articles)", err=True)

    if dry_run:
        import json

        typer.echo(json.dumps(themes_result, indent=2, ensure_ascii=False))
        raise typer.Exit(code=0)

    if output_path is not None:
        typer.echo(f"Digest written: {output_path}", err=True)
    raise typer.Exit(code=0)


@app.command()
def log(
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines"),
) -> None:
    cfg = load_config()
    if not cfg.log_path.exists():
        typer.echo(f"Not found: {cfg.log_path}")
        raise typer.Exit(code=1)
    log_lines = cfg.log_path.read_text(encoding="utf-8").splitlines()
    while log_lines and not log_lines[-1].strip():
        log_lines.pop()
    n = max(0, lines) if lines else 0
    if n and n < len(log_lines):
        log_lines = log_lines[-n:]
    typer.echo("\n".join(log_lines))
    raise typer.Exit(code=0)


@app.command()
def breaking(
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    cfg = load_config()
    from lustro.breaking import run_breaking

    result = run_breaking(cfg=cfg, dry_run=dry_run)
    raise typer.Exit(code=result)


@app.command()
def discover(
    count: Optional[int] = typer.Option(None, "--count", help="Number of tweets to scan"),
) -> None:
    cfg = load_config()
    from lustro.discover import run_discover

    result = run_discover(cfg=cfg, count=count, bird_path=cfg.resolve_bird())
    raise typer.Exit(code=result)


@app.command()
def sources(
    tier: Optional[int] = typer.Option(None, "--tier", help="Filter sources by tier"),
) -> None:
    cfg = load_config()
    rows: list[tuple[str, str, int, str]] = []

    web_sources = cfg.sources_data.get("web_sources", [])
    if isinstance(web_sources, list):
        for source in web_sources:
            if not isinstance(source, dict):
                continue
            source_tier = int(source.get("tier", 2))
            if tier is not None and source_tier != tier:
                continue
            source_type = "rss" if source.get("rss") else "web"
            rows.append(
                (
                    str(source.get("name", "")),
                    source_type,
                    source_tier,
                    str(source.get("cadence", "-")),
                )
            )

    x_accounts = cfg.sources_data.get("x_accounts", [])
    if isinstance(x_accounts, list):
        for account in x_accounts:
            if not isinstance(account, dict):
                continue
            account_tier = int(account.get("tier", 2))
            if tier is not None and account_tier != tier:
                continue
            rows.append(
                (
                    str(account.get("name") or account.get("handle", "")),
                    "x",
                    account_tier,
                    str(account.get("cadence", "-")),
                )
            )

    x_bookmarks = cfg.sources_data.get("x_bookmarks", [])
    if isinstance(x_bookmarks, list):
        for bm in x_bookmarks:
            if not isinstance(bm, dict):
                continue
            bm_tier = int(bm.get("tier", 2))
            if tier is not None and bm_tier != tier:
                continue
            rows.append(
                (
                    str(bm.get("name", "X Bookmarks")),
                    "bkmk",
                    bm_tier,
                    str(bm.get("cadence", "-")),
                )
            )

    if not rows:
        typer.echo("No sources configured.")
        raise typer.Exit(code=0)

    typer.echo(f"{'Name':<36} {'Type':<4} {'Tier':>4} {'Cadence':<12}")
    typer.echo("-" * 64)
    for name, source_type, source_tier, cadence in rows:
        typer.echo(f"{name[:36]:<36} {source_type:<4} {source_tier:>4} {cadence:<12}")
    typer.echo(f"\nTotal: {len(rows)} sources")
    raise typer.Exit(code=0)


@app.command()
def status() -> None:
    cfg = load_config()
    now = datetime.now().astimezone()

    typer.echo(f"Lustro Status  ({now.strftime('%Y-%m-%d %H:%M %Z')})")
    typer.echo("=" * 44)

    typer.echo(f"\nConfig dir:    {cfg.config_dir}")
    typer.echo(f"Sources file:  {_file_age(cfg.sources_path, now)}")
    typer.echo(f"State file:    {_file_age(cfg.state_path, now)}")
    typer.echo(f"News log:      {_file_age(cfg.log_path, now)}")

    state = load_state(cfg.state_path)
    if state:
        typer.echo(f"Sources:       {len(state)} tracked")
        latest = max(
            (dt for ts in state.values() if isinstance(ts, str) for dt in [_parse_aware(ts)] if dt),
            default=None,
        )
        if latest is not None:
            typer.echo(f"Last fetch:    {latest.strftime('%Y-%m-%d %H:%M')}")

    if cfg.article_cache_dir.exists():
        files = list(cfg.article_cache_dir.glob("*.json"))
        size_kb = sum(f.stat().st_size for f in files) / 1024
        typer.echo(f"Article cache: {len(files)} files, {size_kb:.0f} KB")
    else:
        typer.echo(f"Article cache: missing ({cfg.article_cache_dir})")

    if not cfg.sources_path.exists():
        typer.echo("\nRun 'lustro init' to set up configuration.", err=True)
        raise typer.Exit(code=1)
    raise typer.Exit(code=0)


@app.command()
def init() -> None:
    cfg = load_config()
    cfg.config_dir.mkdir(parents=True, exist_ok=True)
    cfg.cache_dir.mkdir(parents=True, exist_ok=True)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    cfg.article_cache_dir.mkdir(parents=True, exist_ok=True)

    if not cfg.sources_path.exists():
        cfg.sources_path.write_text(default_sources_text(), encoding="utf-8")
        created = "created"
    else:
        created = "exists"

    typer.echo(f"Config directory: {cfg.config_dir}")
    typer.echo(f"Sources file: {cfg.sources_path} ({created})")
    typer.echo(f"Cache directory: {cfg.cache_dir}")
    typer.echo(f"Data directory: {cfg.data_dir}")
    raise typer.Exit(code=0)


def main() -> int:
    if len(sys.argv) == 1:
        sys.argv.append("fetch")
    try:
        app()
        return 0
    except typer.Exit as e:
        return e.exit_code if e.exit_code is not None else 0


if __name__ == "__main__":
    sys.exit(main())
