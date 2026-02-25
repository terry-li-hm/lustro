# lustro

Survey and illuminate the AI/tech landscape.

`lustro` is a command-line tool that fetches AI/tech news from RSS, web pages, and X accounts, logs new items, and generates monthly thematic digests.

## Installation

```bash
pip install lustro
```

```bash
uv tool install lustro
```

Install optional digest dependencies:

```bash
pip install "lustro[digest]"
# or
uv tool install "lustro[digest]"
```

## Quickstart

```bash
lustro init          # Create config dirs and starter sources
lustro fetch         # Fetch sources, dedupe, append to log
lustro log           # Show recent log lines
lustro status        # Show config paths, state, cache stats
lustro sources       # List all configured sources
lustro check         # Health-check configured sources
lustro breaking      # Check for breaking AI news
lustro discover      # Find new X handles from For You feed
lustro digest        # Generate monthly thematic digest
```

## Commands

| Command | Description |
|---------|-------------|
| `fetch [--no-archive]` | Fetch all sources, dedupe against log, append new items. `--no-archive` skips full-text caching. |
| `check` | HTTP health-check all configured sources. |
| `log [-n LINES]` | Tail the news log (default 50 lines). |
| `status` | Show config paths, state file ages, and article cache stats. |
| `sources [--tier N]` | List configured sources with type, tier, and cadence. Filter by tier. |
| `breaking [--dry-run]` | Poll tier-1 sources for breaking news (entity + action keyword match). Sends Telegram alerts with rate limiting (3/day, 60min cooldown). |
| `discover [--count N]` | Scan X/Twitter For You feed for AI-relevant tweets from untracked accounts. Requires `bird` CLI. |
| `digest [--month M] [--dry-run] [--themes N] [--model M]` | Monthly thematic digest via LLM. Clusters articles into themes, synthesizes evidence briefs. |
| `init` | Create config/cache/data directories and starter sources config. |
| `--version` | Print version and exit. |

## Configuration

`lustro` uses XDG paths by default:

- config: `~/.config/lustro`
- cache: `~/.cache/lustro`
- data: `~/.local/share/lustro`

You can override base directories with:

- `LUSTRO_CONFIG_DIR`
- `LUSTRO_CACHE_DIR`
- `LUSTRO_DATA_DIR`

### `sources.yaml`

`lustro init` writes `~/.config/lustro/sources.yaml` if missing.

Example:

```yaml
web_sources:
  - name: OpenAI News
    tier: 1
    cadence: daily
    rss: https://openai.com/news/rss.xml
    url: https://openai.com/news/
  - name: Anthropic News
    tier: 2
    cadence: weekly
    rss: https://www.anthropic.com/news/rss.xml
    url: https://www.anthropic.com/news

x_accounts:
  - name: OpenAI on X
    handle: openai
    tier: 2
    cadence: daily

x_discovery:
  enabled: true
  cadence: weekly
  count: 50
  keywords:
    - "\\bAI\\b"
    - "\\bLLM"
    - "\\bGPT"
```

### `config.yaml`

Create `~/.config/lustro/config.yaml` to override defaults:

```yaml
log_path: ~/.local/share/lustro/news.md
digest_model: google/gemini-3-flash-preview
max_log_lines: 500
digest_output_dir: ~/.local/share/lustro/digests
bird_path: /usr/local/bin/bird
tg_notify_path: /usr/local/bin/tg-notify.sh
```

Supported options:

- `log_path`: markdown news log path.
- `digest_model`: OpenRouter model ID.
- `max_log_lines`: log rotation threshold.
- `digest_output_dir`: digest markdown output directory.
- `bird_path`: absolute path to `bird` CLI (default: auto-detect from `PATH`).
- `tg_notify_path`: absolute path to `tg-notify.sh` (default: auto-detect from `PATH`).

## Source Format

Top-level keys are grouped lists; each source item is a mapping.

- `web_sources`: supports `name`, `tier`, `cadence`, and either `rss` or `url` (or both).
- `x_accounts`: supports `name`, `handle`, `tier`, `cadence`.
- `x_discovery`: `keywords` (regex patterns), `count`, `cadence`.

`tier` controls archival behavior during fetch (`tier: 1` enables full-text archive attempts) and breaking news monitoring (tier-1 sources only).

## X / Twitter Support

X account fetching, checking, and discovery require the [`bird`](https://github.com/example/bird) CLI available on `PATH`.

If `bird` is missing, `lustro` skips X operations gracefully and continues with web/RSS sources.

## Breaking News

`lustro breaking` polls tier-1 RSS/web sources and matches headlines against keyword patterns:

- **Entities**: major AI companies, regulators, model names
- **Actions**: launch, release, announce, open-source, acquire, ban
- **Negative filter**: partnership, hiring, podcast, funding

Alerts are sent via `tg-notify.sh` (Telegram) with rate limiting (3 alerts/day, 60-minute cooldown). Use `--dry-run` to preview matches without sending.

## Digest Support

Digest generation uses OpenRouter and requires:

- `LUSTRO_API_KEY` or `OPENROUTER_API_KEY`
- optional deps installed (`lustro[digest]`)

Without these, `lustro digest` exits with a clear error.

## License

MIT
