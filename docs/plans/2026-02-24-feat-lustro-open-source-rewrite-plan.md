---
title: "feat: Rewrite lustro as installable Python package"
type: feat
status: active
date: 2026-02-24
origin: docs/brainstorms/2026-02-24-lustro-open-source-rewrite-brainstorm.md
---

# Rewrite lustro as installable Python package

## Overview

Extract lustro from personal infrastructure (~1200 lines across 3 scripts) into a proper Python package installable via `uv tool install lustro` or `pip install lustro`. All current features (fetch, check, digest, log, status, X/Twitter) ship in v0.1.0. Terry's curated sources stay private; the public repo ships a minimal starter config.

(see brainstorm: docs/brainstorms/2026-02-24-lustro-open-source-rewrite-brainstorm.md)

## Problem Statement / Motivation

The current system works but is non-portable: hardcoded paths to `~/notes/`, `~/skills/`, `~/.cache/`; scripts scattered across two repos (agent-config, skills); PEP 723 inline deps tied to `uv run --script` invocations. It cannot be installed, shared, or maintained as a single unit. Extracting it into a package makes it:
- Installable with one command
- Testable (currently zero tests)
- A clean portfolio piece demonstrating CLI design and architecture

## Proposed Solution

Single Python package with `pyproject.toml`, XDG-aware config, and proper module structure.

### Architecture

```
lustro/
  src/
    lustro/
      __init__.py       # version
      cli.py            # argparse entry point
      config.py         # XDG paths, sources.yaml loading, defaults
      fetcher.py        # RSS (feedparser), web (bs4/trafilatura), X (bird)
      state.py          # JSON state with atomic writes
      log.py            # Markdown log append + rotation
      digest.py         # LLM thematic synthesis via OpenRouter
      sources/
        default.yaml    # Minimal starter (~10 generic sources)
  tests/
    test_config.py
    test_fetcher.py
    test_state.py
    test_log.py
    test_digest.py
    conftest.py         # fixtures: tmp dirs, mock sources.yaml, mock state
  docs/
    brainstorms/
    plans/
  pyproject.toml
  README.md
  LICENSE               # MIT
  .github/
    workflows/
      ci.yml            # ruff + pytest on push
```

### Module Responsibilities

| Module | Source | Key Changes from Current |
|--------|--------|--------------------------|
| `cli.py` | `~/bin/lustro` | Remove dispatcher pattern; import modules directly. Add `init` subcommand. |
| `config.py` | New | XDG path resolution, sources.yaml loading (user > built-in), env var overrides. |
| `fetcher.py` | `ai-news-daily.py:60-450` | Extract fetch_rss, fetch_web, fetch_x_account, archive_article. Remove hardcoded paths — accept config object. |
| `state.py` | `ai-news-daily.py:80-120` | Atomic writes (tempfile + os.replace). Same flat JSON format. |
| `log.py` | `ai-news-daily.py:450-640` | Log append, rotation, dedup logic. Configurable log path and rotation threshold. |
| `digest.py` | `ai-digest.py` | LLM synthesis. Accept config for API key, model, output dir. Graceful error if no key. |

### Config Model

```yaml
# ~/.config/lustro/config.yaml (optional, overrides defaults)
log_path: ~/notes/AI News Log.md          # default: ~/.local/share/lustro/news.md
archive_dir: ~/notes/                      # default: ~/.local/share/lustro/archive/
digest_output_dir: ~/notes/AI & Tech/     # default: ~/.local/share/lustro/digests/
digest_model: google/gemini-3-flash-preview
timezone: Asia/Hong_Kong                   # default: system tz
max_log_lines: 500
```

Sources stay in a separate `~/.config/lustro/sources.yaml` (same schema as today). `lustro init` copies the built-in default.yaml there for customization.

### Subcommands (unchanged from current)

| Command | Behavior |
|---------|----------|
| `lustro` | default = fetch |
| `lustro fetch [--no-archive]` | Fetch all sources, dedupe, append to log, archive tier 1 |
| `lustro check` | HTTP health-check all configured sources |
| `lustro digest [--month M] [--dry-run] [--themes N] [--model M]` | Monthly thematic digest via LLM |
| `lustro log [-n N]` | Tail the news log |
| `lustro status` | State ages, cache stats |
| `lustro init` | **New**: Create config dir, copy starter sources.yaml |

### Dependency Strategy

```toml
[project]
dependencies = [
  "feedparser>=6",
  "requests>=2.28",
  "trafilatura>=1.6",
  "pyyaml>=6",
  "beautifulsoup4>=4.12",
]

[project.optional-dependencies]
digest = ["openai>=1.0", "httpx>=0.24"]
```

Digest deps are optional — `lustro digest` prints a clear error if `openai` isn't installed. X/Twitter integration requires `bird` on PATH (documented, not packaged).

## Technical Considerations

### Atomic State Writes
All JSON state writes use `tempfile.mkstemp()` + `os.replace()` from day one. Current `Path.write_text()` can corrupt on interrupted SSH sessions (see brainstorm: learnings from garp-rai-cli-audit-fixes.md).

### Timezone Handling
Replace hardcoded `HKT = timezone(timedelta(hours=8))` with configurable timezone from config.yaml. Default to system timezone via `datetime.now().astimezone().tzinfo`. Fall back to UTC if detection fails.

### bird CLI (X/Twitter)
Resolve from PATH, not hardcoded `/opt/homebrew/bin/bird`. If missing, log a warning and skip all X accounts — don't crash. Document installation in README.

### WeChat/WeWe RSS
Not included in default sources. Documented in README as a "local extension" pattern for users running their own WeWe RSS proxy. Sources.yaml supports `localhost` URLs — they just won't work without the proxy.

### API Key Handling
`LUSTRO_API_KEY` or `OPENROUTER_API_KEY` env var for digest. Clear error message pointing to README setup section if missing. No keychain integration in the public package.

## Implementation Phases

### Phase 1: Scaffold + Config + State (foundation)

- [ ] `pyproject.toml` with entry point, dependencies, optional deps
- [ ] `config.py`: XDG path resolution, config.yaml + sources.yaml loading, env var overrides
- [ ] `state.py`: atomic read/write, same flat JSON schema
- [ ] `cli.py`: argparse skeleton with all subcommands (stubs)
- [ ] `lustro init` subcommand
- [ ] `sources/default.yaml`: curated starter (~10 sources)
- [ ] `tests/test_config.py`, `tests/test_state.py`

**Verify:** `uv tool install -e .` works, `lustro --help` shows all subcommands, `lustro init` creates config dir, `lustro status` reads state.

### Phase 2: Fetch + Log (core pipeline)

- [ ] `fetcher.py`: port fetch_rss, fetch_web, fetch_x_account, archive_article from ai-news-daily.py
- [ ] `log.py`: port log append, rotation, dedup, markdown formatting
- [ ] Wire `lustro fetch`, `lustro check`, `lustro log` to real implementations
- [ ] `tests/test_fetcher.py` (mock HTTP), `tests/test_log.py` (tmpdir)

**Verify:** `lustro fetch --no-archive` fetches from default sources, appends to log. `lustro check` prints source health table. `lustro log -n 10` tails correctly.

### Phase 3: Digest (LLM feature)

- [ ] `digest.py`: port theme identification + synthesis from ai-digest.py
- [ ] Optional dependency handling (clear error if `openai` not installed)
- [ ] Wire `lustro digest` with all flags
- [ ] `tests/test_digest.py` (mock LLM responses)

**Verify:** `lustro digest --dry-run` identifies themes from cached articles without LLM call. Full digest writes markdown to configured output dir.

### Phase 4: Polish + Ship

- [ ] README.md: installation, quickstart, configuration, source format, X/Twitter setup, WeChat extension pattern
- [ ] LICENSE (MIT)
- [ ] `.github/workflows/ci.yml`: ruff lint + pytest on push
- [ ] Publish v0.1.0 to PyPI (replaces placeholder 0.0.1)
- [ ] Create GitHub repo (`terry-li-hm/lustro`)
- [ ] Migrate Terry's personal config: sources.yaml + config.yaml to `~/.config/lustro/`, update LaunchAgent plists to call `lustro` instead of `uv run --script`

**Verify:** `uv tool install lustro` from PyPI works. CI green. `lustro status` on Terry's machine shows same output as current setup.

## Acceptance Criteria

- [ ] `pip install lustro` / `uv tool install lustro` works from PyPI
- [ ] All 7 subcommands functional (fetch, check, digest, log, status, init, default)
- [ ] XDG-aware paths with env var overrides
- [ ] Atomic state writes (no corruption on interrupt)
- [ ] Optional digest deps — clean error if missing
- [ ] bird CLI optional — skip X accounts with warning if missing
- [ ] Tests pass with >80% coverage on config, state, log, fetcher
- [ ] CI runs ruff + pytest on push
- [ ] README covers installation, quickstart, config, source format
- [ ] Terry's personal setup migrated and working identically

## Dependencies & Risks

| Risk | Mitigation |
|------|------------|
| trafilatura version breaks extraction | Pin `>=1.6,<2`; test with fixtures |
| bird CLI changes output format | Parse defensively; test with recorded JSON |
| OpenRouter API changes | openai client is stable; model ID is user-configurable |
| PyPI placeholder 0.0.1 confuses early installers | Replace quickly; include entry point even in placeholder (see learnings: pypi-placeholder-gotcha.md) |

## Sources & References

- **Origin brainstorm:** [docs/brainstorms/2026-02-24-lustro-open-source-rewrite-brainstorm.md](docs/brainstorms/2026-02-24-lustro-open-source-rewrite-brainstorm.md) — Key decisions: Python (not Rust), full feature set in v0.1.0, curated sources private, markdown output, XDG paths
- Current dispatcher: `~/bin/lustro`
- Current fetcher: `~/skills/ai-news/ai-news-daily.py` (641 lines)
- Current digest: `~/skills/ai-news/ai-digest.py` (389 lines)
- Current sources: `~/skills/ai-news/sources.yaml` (1220 lines)
- Learnings: `~/docs/solutions/package-registry-namespace-squatting.md`, `pypi-placeholder-gotcha.md`, `credential-isolation-keychain.md`, `garp-rai-cli-audit-fixes.md` (atomic writes), `uv-launchd-python-fallback.md`
