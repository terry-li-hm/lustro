from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from lustro.config import default_sources_text, load_config
from lustro.state import load_state


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


def cmd_fetch(_args: argparse.Namespace) -> int:
    print("fetch is not implemented yet (Phase 2).")
    return 0


def cmd_check(_args: argparse.Namespace) -> int:
    print("check is not implemented yet (Phase 2).")
    return 0


def cmd_digest(_args: argparse.Namespace) -> int:
    print("digest is not implemented yet (Phase 3).")
    return 0


def cmd_log(args: argparse.Namespace) -> int:
    cfg = load_config()
    if not cfg.log_path.exists():
        print(f"Not found: {cfg.log_path}")
        return 1
    lines = cfg.log_path.read_text(encoding="utf-8").splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    if args.lines and args.lines < len(lines):
        lines = lines[-args.lines :]
    print("\n".join(lines))
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
            (
                datetime.fromisoformat(ts)
                for ts in state.values()
                if isinstance(ts, str)
            ),
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
    )
    sub = parser.add_subparsers(dest="command")

    p_fetch = sub.add_parser("fetch", help="Run daily fetch")
    p_fetch.add_argument("--no-archive", action="store_true", help="Reserved for Phase 2")

    sub.add_parser("check", help="Health-check configured sources")

    p_digest = sub.add_parser("digest", help="Monthly thematic digest")
    p_digest.add_argument("--month", help="Target month YYYY-MM")
    p_digest.add_argument("--dry-run", action="store_true", help="Show themes only")
    p_digest.add_argument("--themes", type=int, help="Max themes")
    p_digest.add_argument("--model", help="Model ID")

    p_log = sub.add_parser("log", help="Tail the news log")
    p_log.add_argument("--lines", "-n", type=int, default=50, help="Number of lines")

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
        "status": cmd_status,
        "init": cmd_init,
    }
    return dispatch[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
