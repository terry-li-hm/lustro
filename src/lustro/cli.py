from __future__ import annotations

import argparse
import importlib.metadata
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lustro.config import LustroConfig, default_sources_text, load_config
from lustro.state import load_state, lockfile


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


def cmd_fetch(args: argparse.Namespace) -> int:
    cfg = load_config()
    with lockfile(cfg.state_path):
        return _cmd_fetch_locked(args, cfg)


def _cmd_fetch_locked(args: argparse.Namespace, cfg: LustroConfig) -> int:
    state = load_state(cfg.state_path)
    from lustro.fetcher import archive_article, fetch_rss, fetch_web, fetch_x_account, fetch_x_bookmarks
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

    since_date = _get_last_scan_date(state)
    title_prefixes = load_title_prefixes(cfg.log_path)
    results: dict[str, list[dict[str, str]]] = {}
    archived_count = 0

    for source in cfg.sources:
        name = source["name"]
        cadence = source.get("cadence", "daily")
        tier = source.get("tier", 2)
        if not should_fetch(state, name, cadence, now=now):
            continue
        print(f"Fetching: {name}...", file=sys.stderr)
        if source.get("bookmarks"):
            articles = fetch_x_bookmarks(since_date, bird_path=cfg.resolve_bird())
        elif "rss" in source:
            articles = fetch_rss(source["rss"], since_date)
            if articles is None and "url" in source:
                print(f"  Falling back to web: {source['url']}", file=sys.stderr)
                articles = fetch_web(source["url"])
            articles = articles or []
        elif "handle" in source:
            articles = fetch_x_account(source["handle"], since_date, bird_path=cfg.resolve_bird())
        else:
            articles = fetch_web(source.get("url", ""))

        new_articles = []
        for article in articles:
            if is_junk(article["title"]):
                continue
            prefix = _title_prefix(article["title"])
            if prefix in title_prefixes:
                continue
            new_articles.append(article)
            title_prefixes.add(prefix)

        if not args.no_archive:
            for article in new_articles:
                if article.get("link") and tier == 1:
                    archive_article(article, name, tier, cfg.article_cache_dir, now)
                    archived_count += 1

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
            if zeros >= 5:
                print(
                    f"  Warning: {name} has {zeros} consecutive zero-article fetches",
                    file=sys.stderr,
                )

    save_state(cfg.state_path, state)
    if not results:
        print("No new articles found.", file=sys.stderr)
        return 0
    today = now.strftime("%Y-%m-%d")
    md = format_markdown(results, today)
    append_to_log(cfg.log_path, md)
    total = sum(len(v) for v in results.values())
    print(f"Logged {total} new articles.", file=sys.stderr)
    return 0


def cmd_check(_args: argparse.Namespace) -> int:
    cfg = load_config()
    state = load_state(cfg.state_path)
    from lustro.fetcher import check_sources

    web_sources = [s for s in cfg.sources if "handle" not in s and not s.get("bookmarks")]
    x_accounts = [s for s in cfg.sources if "handle" in s]
    x_bookmarks = [s for s in cfg.sources if s.get("bookmarks")]
    check_sources(web_sources, x_accounts, state, bird_path=cfg.resolve_bird(), x_bookmarks=x_bookmarks)
    return 0


def cmd_digest(args: argparse.Namespace) -> int:
    cfg = load_config()
    from lustro.digest import run_digest

    try:
        themes, output_path = run_digest(
            cfg=cfg,
            month=args.month,
            dry_run=bool(args.dry_run),
            themes=args.themes,
            model=args.model,
        )
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Found {len(themes)} themes.", file=sys.stderr)
    for i, theme in enumerate(themes, 1):
        name = theme.get("theme", f"Theme {i}")
        count = len(theme.get("article_indices", []))
        print(f"{i}. {name} ({count} articles)", file=sys.stderr)

    if args.dry_run:
        import json

        print(json.dumps(themes, indent=2, ensure_ascii=False))
        return 0

    if output_path is not None:
        print(f"Digest written: {output_path}", file=sys.stderr)
    return 0


def cmd_log(args: argparse.Namespace) -> int:
    cfg = load_config()
    if not cfg.log_path.exists():
        print(f"Not found: {cfg.log_path}")
        return 1
    lines = cfg.log_path.read_text(encoding="utf-8").splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    n = max(0, args.lines) if args.lines else 0
    if n and n < len(lines):
        lines = lines[-n:]
    print("\n".join(lines))
    return 0


def cmd_breaking(args: argparse.Namespace) -> int:
    cfg = load_config()
    from lustro.breaking import run_breaking

    return run_breaking(cfg=cfg, dry_run=bool(args.dry_run))


def cmd_discover(args: argparse.Namespace) -> int:
    cfg = load_config()
    from lustro.discover import run_discover

    return run_discover(cfg=cfg, count=args.count, bird_path=cfg.resolve_bird())


def cmd_sources(args: argparse.Namespace) -> int:
    cfg = load_config()
    rows: list[tuple[str, str, int, str]] = []

    web_sources = cfg.sources_data.get("web_sources", [])
    if isinstance(web_sources, list):
        for source in web_sources:
            if not isinstance(source, dict):
                continue
            tier = int(source.get("tier", 2))
            if args.tier is not None and tier != args.tier:
                continue
            source_type = "rss" if source.get("rss") else "web"
            rows.append(
                (
                    str(source.get("name", "")),
                    source_type,
                    tier,
                    str(source.get("cadence", "-")),
                )
            )

    x_accounts = cfg.sources_data.get("x_accounts", [])
    if isinstance(x_accounts, list):
        for account in x_accounts:
            if not isinstance(account, dict):
                continue
            tier = int(account.get("tier", 2))
            if args.tier is not None and tier != args.tier:
                continue
            rows.append(
                (
                    str(account.get("name") or account.get("handle", "")),
                    "x",
                    tier,
                    str(account.get("cadence", "-")),
                )
            )

    x_bookmarks = cfg.sources_data.get("x_bookmarks", [])
    if isinstance(x_bookmarks, list):
        for bm in x_bookmarks:
            if not isinstance(bm, dict):
                continue
            tier = int(bm.get("tier", 2))
            if args.tier is not None and tier != args.tier:
                continue
            rows.append(
                (
                    str(bm.get("name", "X Bookmarks")),
                    "bkmk",
                    tier,
                    str(bm.get("cadence", "-")),
                )
            )

    if not rows:
        print("No sources configured.")
        return 0

    print(f"{'Name':<36} {'Type':<4} {'Tier':>4} {'Cadence':<12}")
    print("-" * 64)
    for name, source_type, tier, cadence in rows:
        print(f"{name[:36]:<36} {source_type:<4} {tier:>4} {cadence:<12}")
    print(f"\nTotal: {len(rows)} sources")
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    cfg = load_config()
    now = datetime.now().astimezone()

    print(f"Lustro Status  ({now.strftime('%Y-%m-%d %H:%M %Z')})")
    print("=" * 44)

    print(f"\nConfig dir:    {cfg.config_dir}")
    print(f"Sources file:  {_file_age(cfg.sources_path, now)}")
    print(f"State file:    {_file_age(cfg.state_path, now)}")
    print(f"News log:      {_file_age(cfg.log_path, now)}")

    state = load_state(cfg.state_path)
    if state:
        print(f"Sources:       {len(state)} tracked")
        latest = max(
            (dt for ts in state.values() if isinstance(ts, str) for dt in [_parse_aware(ts)] if dt),
            default=None,
        )
        if latest is not None:
            print(f"Last fetch:    {latest.strftime('%Y-%m-%d %H:%M')}")

    if cfg.article_cache_dir.exists():
        files = list(cfg.article_cache_dir.glob("*.json"))
        size_kb = sum(f.stat().st_size for f in files) / 1024
        print(f"Article cache: {len(files)} files, {size_kb:.0f} KB")
    else:
        print(f"Article cache: missing ({cfg.article_cache_dir})")

    if not cfg.sources_path.exists():
        print("\nRun 'lustro init' to set up configuration.", file=sys.stderr)
        return 1
    return 0


def cmd_init(_args: argparse.Namespace) -> int:
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

    print(f"Config directory: {cfg.config_dir}")
    print(f"Sources file: {cfg.sources_path} ({created})")
    print(f"Cache directory: {cfg.cache_dir}")
    print(f"Data directory: {cfg.data_dir}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lustro",
        description="Survey and illuminate the AI/tech landscape",
        epilog=(
            'Shell completion: eval "$(register-python-argcomplete lustro)" (requires argcomplete)'
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {_get_version()}",
    )
    sub = parser.add_subparsers(dest="command")

    p_fetch = sub.add_parser("fetch", help="Run daily fetch")
    p_fetch.add_argument(
        "--no-archive", action="store_true", help="Skip archiving full article text"
    )

    sub.add_parser("check", help="Health-check configured sources")

    p_digest = sub.add_parser("digest", help="Monthly thematic digest")
    p_digest.add_argument("--month", help="Target month YYYY-MM")
    p_digest.add_argument("--dry-run", action="store_true", help="Show themes only")
    p_digest.add_argument("--themes", type=int, help="Max themes")
    p_digest.add_argument("--model", help="Model ID")

    p_log = sub.add_parser("log", help="Tail the news log")
    p_log.add_argument("--lines", "-n", type=int, default=50, help="Number of lines")

    p_breaking = sub.add_parser("breaking", help="Check for breaking AI news")
    p_breaking.add_argument("--dry-run", action="store_true")

    p_discover = sub.add_parser("discover", help="Find new X handles from For You feed")
    p_discover.add_argument("--count", type=int, help="Number of tweets to scan")

    p_sources = sub.add_parser("sources", help="List configured sources")
    p_sources.add_argument("--tier", type=int, help="Filter sources by tier")

    sub.add_parser("status", help="Show paths and state ages")
    sub.add_parser("init", help="Create config/cache/data dirs and starter sources")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command is None:
        args = parser.parse_args(["fetch"])

    dispatch = {
        "fetch": cmd_fetch,
        "check": cmd_check,
        "digest": cmd_digest,
        "log": cmd_log,
        "breaking": cmd_breaking,
        "discover": cmd_discover,
        "sources": cmd_sources,
        "status": cmd_status,
        "init": cmd_init,
    }
    return dispatch[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
