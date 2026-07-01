---
name: gem-cli
description: AI-agent-native CLI for shared Gemini Gems via URL.
version: 3.0.0
author: Peter (lesterppo)
license: MIT
tags: [gemini, gem, cli, agent-native, token-efficient]
platforms: [linux, macos, wsl]
metadata:
  hermes:
    category: automation
    related_skills: [gemini-web-cli, gemini-api-integration]
---

# gem-cli Skill

Token-efficient CLI for interacting with any shared Gemini Gem (custom AI persona)
via its URL. Always writes response to file; stdout gets a compact JSON pointer.

## When to Use

- User shares a Gemini Gem link (`gemini.google.com/gem/...`)
- Need to interact with a Gem programmatically (no browser, subprocess-safe)
- Need token-efficient output for AI agent consumption (~60 chars stdout)
- Need to discover available Gems/models before calling
- Need multi-turn conversation or deep research with a Gem

## Prerequisites

```bash
pip install gemini-webapi browser-cookie3 loguru
```

Or: `./install.sh` from the repo.

Auth: sign in at `gemini.google.com` in Firefox or Chrome, then `gem-cli --init`.

## Quick Start

```bash
# One-time setup
gem-cli --init

# Single turn
gem-cli "https://gemini.google.com/gem/<ID>" "prompt"
gem-cli "<raw-gem-id>" "prompt"

# Agent-optimized invocation (always use these)
gem-cli "<gem-id>" -m flash --brief --raw "prompt"
echo "prompt" | gem-cli "<gem-id>" --raw
```

## How to Run

```bash
# Core interaction
gem-cli <gem-url-or-id> "prompt"                 # Single turn
gem-cli <gem-id> -c sess.json --new "start"      # New multi-turn
gem-cli <gem-id> -c sess.json "continue"         # Continue multi-turn

# Model + thinking
gem-cli <gem-id> -m flash "quick answer"
gem-cli <gem-id> -m pro --thinking extended "deep analysis"
gem-cli <gem-id> -m thinking "reason step by step"

# Deep research (web search + synthesis, Pro only)
gem-cli <gem-id> -m pro --deep-research "complex question"

# Image generation
gem-cli <gem-id> --img "a cat reading a book"

# File upload
gem-cli <gem-id> -f report.pdf "summarize this"
gem-cli <gem-id> -i chart.png "analyze this chart"

# Discovery
gem-cli --list-models     # Show available models
gem-cli --list-gems       # Show your Gems
gem-cli <url> --gem-info  # Check Gem metadata without calling

# Auth management
gem-cli --init            # Cache tokens from browser
gem-cli --login           # Interactive browser login
```

## Agent-Optimized Flags

Always use these for token-efficient agent interaction:

| Flag | Purpose |
|------|---------|
| `--raw` | Zero stderr, pure JSON stdout — safe for subprocess piping |
| `--brief` | Prepend "Be concise." — shorter responses |
| `-o FILE` | Response on disk, pointer JSON on stdout (~60 chars) |
| `--json-out` | JSON output format instead of markdown |
| `-t SEC` | Timeout (default 120s, auto-extends to 600s for `--deep-research`) |
| `--no-retry` | Fail fast on first error |

## Output Format

**Stdout (success):**
```json
{"ok":true,"f":"./out.md","s":1234,"model":"flash+extended","gem":"GemName","dr":true}
```

`f` = output file path, `s` = file size in bytes, `model` = normalized label,
`gem` = Gem name, `dr` = deep research flag (only when true),
`c`/`t` = conversation id and turn count (multi-turn only).

**Stdout (error):**
```json
{"ok":false,"err":"RATE_LIMIT","msg":"...","retry_after_s":30,"retry":true}
```

Error categories: `AUTH_EXPIRED` (retryable), `RATE_LIMIT` (retryable, has `retry_after_s`),
`TIMEOUT` (retryable), `GEN_FAILED` (not retryable), `BAD_URL`.

## Model Labels

All model labels in output are normalized to consistent format:

| Shorthand | Output Label |
|-----------|-------------|
| `-m flash` | `flash` |
| `-m flash --thinking extended` | `flash+extended` |
| `-m pro` | `pro` |
| `-m pro --thinking extended` | `pro+extended` |
| `-m thinking` | `thinking+standard` |
| `-m thinking --thinking extended` | `thinking+extended` |

## 5-Tier Auth Chain

Auth is resolved in priority order (no configuration needed):

1. `GEMINI_SID` + `GEMINI_TS` env vars (CI/remote)
2. `~/.gemini-cli/auth.json` cached file (fast)
3. Browser cookie scan via `browser_cookie3` (fresh)
4. Auto-retry with cookie re-scan on auth error (3 attempts)
5. `--login` interactive browser flow

## Pitfalls

- Deep research requires Pro model (Flash may hang during plan creation)
- Pro + extended thinking may exceed default 120s timeout — use `-t 180`
- Image generation requires Flash model (auto-selected with `--img`)
- Cookies expire ~30 days — run `gem-cli --init` to refresh cache
- `browser_cookie3` only needed for Tier 3; set env vars for CI/remote
- Shared Gems may be deleted by owner — check with `--gem-info` first
- Model list from `list_models()` returns display names, not model IDs; the CLI handles resolution automatically

## Verification

```bash
# 1. Auth works
gem-cli --init
# → {"ok":true,"action":"init","cached":"~/.gemini-cli/auth.json"}

# 2. Gem interaction with enriched pointer
gem-cli "<url>" -m flash --brief "hello"
# → {"ok":true,"f":"...","s":32,"model":"flash"}

# 3. Multi-turn memory preserved
gem-cli "<id>" -c /tmp/test.json --new -m flash "my name is Alex"
gem-cli "<id>" -c /tmp/test.json -m flash "what is my name?"
# → Response references "Alex"

# 4. Deep research
gem-cli "<url>" -m pro --deep-research "topic"
# → stderr: "Creating research plan... Plan: <title> — starting... Research in progress..."
# → {"ok":true,"dr":true,...}

# 5. Pure JSON piping
echo "prompt" | gem-cli "<id>" --raw
# → {"ok":true,"f":"...","model":"flash"}  (zero stderr bytes)
```
