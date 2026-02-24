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
lustro init
lustro fetch
lustro log
lustro status
lustro check
lustro digest --dry-run
```

What these do:

- `init`: create config/cache/data directories and starter sources config.
- `fetch`: fetch sources, dedupe, append to log, archive tier-1 article text.
- `log`: show recent log lines.
- `status`: show config paths, state age, and cache stats.
- `check`: run health checks on configured sources.
- `digest --dry-run`: identify monthly themes without writing digest output.

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
```

### `config.yaml`

Create `~/.config/lustro/config.yaml` to override defaults:

```yaml
log_path: ~/.local/share/lustro/news.md
digest_model: google/gemini-3-flash-preview
max_log_lines: 500
digest_output_dir: ~/.local/share/lustro/digests
timezone: America/New_York
```

Supported options:

- `log_path`: markdown news log path.
- `digest_model`: OpenRouter model ID.
- `max_log_lines`: log rotation threshold.
- `digest_output_dir`: digest markdown output directory.
- `timezone`: timezone label for your setup.

## Source Format

Top-level keys are grouped lists; each source item is a mapping.

- `web_sources`: supports `name`, `tier`, `cadence`, and either `rss` or `url` (or both).
- `x_accounts`: supports `name`, `handle`, `tier`, `cadence`.

`tier` controls archival behavior during fetch (`tier: 1` enables full-text archive attempts).

## X / Twitter Support

X account fetching/checking requires the `bird` CLI available on `PATH`.

If `bird` is missing, `lustro` skips X operations gracefully and continues with web/RSS sources.

## Digest Support

Digest generation uses OpenRouter and requires:

- `LUSTRO_API_KEY` or `OPENROUTER_API_KEY`
- optional deps installed (`lustro[digest]`)

Without these, `lustro digest` exits with a clear error.
