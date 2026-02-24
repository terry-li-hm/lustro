---
title: Lustro Open-Source Rewrite
date: 2026-02-24
status: decided
participants: [terry, claude]
---

# Lustro Open-Source Rewrite

## What We're Building

A proper Python package (`pip install lustro` / `uv tool install lustro`) that aggregates AI/tech content from RSS feeds, X/Twitter accounts, and web scraping — dedupes, archives full text, and produces LLM-powered thematic digests. Markdown output, configurable sources, XDG-aware paths.

Rewrite of existing personal infrastructure (~1200 lines across 3 scripts + 1220-line sources.yaml) into a clean, installable CLI.

## Why This Approach

### Audience: "Just me, but clean"

Personal tool extracted cleanly enough that someone *could* use it. No marketing, no community support, no hypothetical-user over-engineering. Portfolio value comes from architecture and code quality, not adoption.

### Python, not Rust

Considered Rust for single-binary distribution, but:
- No users to distribute to — `uv tool install` is equally ergonomic for personal use
- The interesting code is all I/O and text processing — Python's ecosystem (feedparser, trafilatura, beautifulsoup4) is mature; Rust equivalents are thin or nonexistent
- Portfolio signal is in the architecture, not the language
- Can always port later if it takes off; design decisions (config, state, schema) transfer 1:1
- crates.io name is reserved regardless

### Full feature set from v0.1.0

Ship everything: fetch, check, digest, log, status, X/Twitter integration. No staged rollout — this is a rewrite of working code, not a new product.

## Key Decisions

### 1. Language: Python
Proper `pyproject.toml` package with entry points. Installable via `uv tool install lustro` or `pip install lustro`.

### 2. Scope: All current features
fetch + check + digest + log + status + X/Twitter. Digest requires LLM API key (OpenRouter/Gemini). X requires `bird` CLI. Both are opt-in features that degrade gracefully if deps are missing.

### 3. Config: Curated sources are private
- Ship a minimal skeleton config (~5-10 generic tech sources) as built-in defaults
- Terry's 1220-line sources.yaml lives in `~/.config/lustro/sources.yaml` (never in repo)
- Tool loads: user config > built-in defaults
- `lustro init` creates config dir and copies skeleton for customization

### 4. Output: Markdown
Keep the current Obsidian-friendly markdown format with `## YYYY-MM-DD` date headers. Human-readable, works with any PKM tool or just `cat`. No JSONL layer — YAGNI.

### 5. Paths: XDG-aware with sensible defaults
- Config: `$XDG_CONFIG_HOME/lustro/` (~/.config/lustro/)
- Cache: `$XDG_CACHE_HOME/lustro/` (~/.cache/lustro/)
- Data/log: `$XDG_DATA_HOME/lustro/` (~/.local/share/lustro/)
- All overridable via env vars (`LUSTRO_CONFIG_DIR`, etc.)

### 6. State management: Keep flat JSON
Current flat dict (source_name → timestamp) works fine. Add atomic writes (tempfile + os.replace) from day one per learnings.

### 7. bird CLI: Optional dependency
X/Twitter integration requires `bird` binary on PATH. If missing, skip X accounts with a warning. Don't bundle or wrap it. Document in README.

### 8. WeChat/WeWe RSS: Not shipped
localhost:4000 proxy URLs are non-portable. Remove from default config. Document as a "local extension" pattern in README for users running their own WeWe RSS instance.

### 9. API keys: Env vars with clear errors
`OPENROUTER_API_KEY` (or `LUSTRO_API_KEY`) for digest. Clear error message pointing to setup docs if missing. No keychain integration in the public package (that's Terry-specific).

## Architecture Sketch

```
lustro/
  __init__.py
  cli.py          # argparse entry point
  config.py       # XDG paths, sources.yaml loading, defaults
  fetcher.py      # RSS (feedparser), web scraping (bs4/trafilatura), X (bird subprocess)
  state.py        # JSON state with atomic writes
  log.py          # Markdown log append + rotation
  digest.py       # LLM thematic synthesis (OpenRouter)
  sources/
    default.yaml  # Minimal starter sources
```

```toml
[project.scripts]
lustro = "lustro.cli:main"
```

## Open Questions

*None — all resolved during brainstorm.*

## Sources

- Repo research: existing scripts at ~/skills/ai-news/ (ai-news-daily.py, ai-digest.py, sources.yaml)
- Learnings: ~/docs/solutions/package-registry-namespace-squatting.md, pypi-placeholder-gotcha.md, credential-isolation-keychain.md, garp-rai-cli-audit-fixes.md
- Name registered: PyPI (lustro), crates.io (lustro), npm (@terry-li/lustro)
