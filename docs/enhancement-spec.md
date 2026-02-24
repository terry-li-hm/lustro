# Lustro Enhancement Spec

Add 5 features to the lustro package. Read existing source files to understand patterns before implementing.

## 1. `--version` flag

Add `--version` to the top-level parser in `src/lustro/cli.py`.

- Read version from `importlib.metadata.version("lustro")` with fallback to "dev"
- Add to `build_parser()`: `parser.add_argument("--version", action="version", version=f"%(prog)s {_get_version()}")`
- Test: `lustro --version` prints `lustro 0.1.0`

## 2. `lustro breaking` subcommand

Port the breaking news monitor into lustro. Reference script at `~/scripts/crons/ai-news-breaking.py` (344 lines). Create `src/lustro/breaking.py`.

**What it does:** Polls RSS sources for breaking AI news using keyword matching (entity + action patterns, minus negative patterns). Sends Telegram alerts via `tg-notify.sh`, cross-posts to news log. Has rate limiting (3 alerts/day max, 60min cooldown) and dedup via SHA-256 hashes.

**Key design decisions:**
- Sources list is hardcoded in the reference script (9 sources). In lustro, read from `sources.yaml` — use a `breaking_sources` top-level key, OR filter existing `web_sources` by tier 1 only. Prefer filtering tier 1 — no new config needed.
- State file: `~/.cache/lustro/breaking-state.json` (separate from main state)
- `tg-notify.sh` path: use `shutil.which("tg-notify.sh")` or `~/scripts/tg-notify.sh` as fallback
- Log path: from `cfg.log_path`

**Keyword patterns (copy from reference):**
- ENTITIES: anthropic, openai, deepmind, meta ai, mistral, xai, grok, hkma, mas, sec, eu ai act, pboc, gpt-N, claude-N, gemini-N, llama-N, o1-, sonnet, opus, haiku
- ACTIONS: launch, release, introduc, announc, unveil, open source, acquir, merg, shut down, ban, mandat
- NEGATIVE: partner, collaborat, hiring, podcast, interview, webinar, funding, series A-D

**CLI:**
```
lustro breaking [--dry-run]
```

**Wire in cli.py:**
- Add `p_breaking = sub.add_parser("breaking", help="Check for breaking AI news")`
- Add `p_breaking.add_argument("--dry-run", action="store_true")`
- Add `cmd_breaking` function that imports from `lustro.breaking`

**Test in `tests/test_breaking.py`:**
- Test `is_breaking()` with positive and negative examples
- Test state management (daily counter reset, cooldown)
- Test `cmd_breaking` dry-run with mock RSS data (no actual HTTP or Telegram)

## 3. `lustro discover` subcommand

Weekly X/Twitter discovery scan. Fetches `bird home` (For You feed), matches tweets against AI keywords from `sources.yaml` `x_discovery` section, surfaces new handles not already tracked.

Create `src/lustro/discover.py`.

**How it works:**
1. Read `x_discovery` config from sources.yaml (keywords list, count)
2. Read `x_accounts` from sources.yaml to get already-tracked handles
3. Run `bird home -n {count} --json` to get For You timeline
4. Parse JSON output — each tweet has `author.handle`, `text`, `created_at`
5. Filter tweets matching any keyword pattern (case-insensitive regex)
6. Group by author handle, exclude already-tracked handles
7. Output: list of new handles with tweet count and sample tweet

**CLI:**
```
lustro discover [--count N]
```

Default count from `x_discovery.count` in sources.yaml (default 50).

**Output format (to stderr):**
```
X Discovery: scanned 50 tweets, 12 matched keywords
New handles (not tracked):
  @newhandle1 (3 matches) — "sample tweet text..."
  @newhandle2 (1 match) — "sample tweet text..."
```

**Also append to news log** (cfg.log_path) under a `### X Discovery (For You)` heading with today's date, so the conversational `/ai-news` skill can see what was found.

**Graceful degradation:** If `bird` is not installed, print a message and return 0 (not an error).

**Test in `tests/test_discover.py`:**
- Test keyword matching
- Test handle filtering (already tracked excluded)
- Test output formatting
- Mock `bird` subprocess — don't run real bird

## 4. PyPI release workflow

Create `.github/workflows/release.yml`:
- Trigger on push of tags matching `v*`
- Build with hatchling
- Publish to PyPI using `pypa/gh-action-pypi-publish`
- Use `id-token: write` for trusted publishing (no API token needed)

Keep it simple — just the publish step. CI already handles tests.

```yaml
name: Release
on:
  push:
    tags: ["v*"]
jobs:
  publish:
    runs-on: ubuntu-latest
    permissions:
      id-token: write
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv build
      - uses: pypa/gh-action-pypi-publish@release/v1
```

## 5. Additional polish

### 5a. `lustro sources` subcommand

Quick list of configured sources without HTTP checks (faster than `lustro check`).

```
lustro sources [--tier N]
```

Output: table of name, type (rss/web/x), tier, cadence. Filter by tier if `--tier` given.

Add to cli.py. No new module needed — read from `cfg.sources` and `cfg.sources_data.get("x_accounts", [])`.

### 5b. Shell completion hint

Add epilog to the argparse parser showing how to enable shell completion:
```python
parser = argparse.ArgumentParser(
    prog="lustro",
    description="Survey and illuminate the AI/tech landscape",
    epilog="Shell completion: eval \"$(register-python-argcomplete lustro)\" (requires argcomplete)",
)
```

This is just a hint — don't add argcomplete as a dependency.

## General rules

- Follow existing patterns in the codebase (read files first)
- Use `from __future__ import annotations` in all new files
- Lazy imports for heavy deps in cli.py (already established pattern)
- Run `ruff check --fix src/ tests/` and `ruff format src/ tests/` after all changes
- Run `pytest tests/` — all tests must pass
- Keep line length under 100 chars (ruff config)
