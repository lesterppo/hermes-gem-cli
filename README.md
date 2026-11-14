# gem-cli — Gemini Gem CLI via WebAPI (No API Key, Fast)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/lesterppo/hermes-gem-cli/blob/main/LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-green.svg)](https://www.python.org/)
[![Platform: Linux](https://img.shields.io/badge/Platform-Linux%20%7C%20macOS-lightgrey.svg)]()

**AI-agent-native CLI for Google Gemini Gems — chat, image generation, deep
research, Gem CRUD. No API key. Uses browser cookies via the `gemini-webapi`
library. 3-10x faster than browser automation on native Linux (~4s per chat).**

```text
gemini-cli -p "prompt"                           → Direct chat (no Gem, fastest)
gemini-cli <gem-id> "prompt"                      → Chat with a Gem
gemini-cli <gem-id> -c sess.json --new "msg"      → Multi-turn conversation
gemini-cli <gem-id> --img "description"            → Image generation (Imagen)
gemini-cli <gem-id> -m pro --deep-research "q"     → Deep research
gemini-cli --create-gem "Name" -p "instructions"  → Create a Gem
gemini-cli --delete-gem <id>                       → Delete a Gem
gemini-cli --list-gems                             → List all your Gems
```

> **Looking for the browser-based alternative?** See
> [hermes-gem-pw](https://github.com/lesterppo/hermes-gem-pw) — drives a real
> Chromium via Playwright/CDP. Works on WSL2, supports knowledge upload at Gem
> creation. Slower (~15-40s) but universally reliable.

---

## Why WebAPI?

`gemini-webapi` is a Python library that calls Google's internal Gemini API
directly over HTTP. On **native Linux**, `curl_cffi` enables fast, reliable
requests (~4s chat). This is 3-10x faster than browser automation.

**Platform compatibility:**
- **Native Linux**: Full support. All features work. ~4s chat.
- **WSL2**: `curl_cffi` hangs on POST. Use [hermes-gem-pw](https://github.com/lesterppo/hermes-gem-pw) instead.
- **macOS**: Untested (likely works, curl_cffi has native macOS support).

## Features

| Feature | Command | Speed |
|---------|---------|-------|
| Direct chat (no Gem) | `gemini-cli -p "prompt"` | ~4s |
| Gem chat | `gemini-cli <id> "prompt"` | ~4s |
| Multi-turn conversation | `gemini-cli <id> -c sess.json --new "msg"` | ~4-30s |
| Image generation | `gemini-cli <id> --img "description"` | ~10s |
| Deep research | `gemini-cli <id> -m pro --deep-research "q"` | ~5-10min |
| File upload (PDF, TXT) | `gemini-cli <id> -f file.pdf "summarize"` | ~5s |
| Image upload | `gemini-cli <id> -i photo.png "describe"` | ~5s |
| Gem CRUD | `--create-gem` / `--delete-gem` / `--list-gems` | <1s |
| Model selection | `-m flash` / `pro` / `lite` (resolved against the account's live registered models) | — |
| Extended thinking | `--thinking extended` | adds ~30-60s |
| Temporary chat | `--temporary` | ephemeral, not in history |
| Thinking traces | `--show-thoughts` | adds thoughts to output |
| Video generation | `--save-videos DIR` | Veo 3, 3/day Pro |
| Canvas artifact | `--extract-canvas FILE` | saves html block |
| Fetch latest | `--fetch-latest c_xxx` | fast latest turn |
| Deep research status | `--deep-research-status c_xxx` | is the report in the chat yet? |
| Auth diagnostics | `--doctor` | which cookie jar is live + capability probe |
| Streaming output | `--stream` | real-time tokens |

### Web Gemini vs WebAPI (mapped 2026-11-14, account cyc236ha Plus)

Models — the web picker has two modes plus a Thinking level. WebAPI exposes
only the account default (`gemini-自動`), so every CLI shorthand lands on it:

| Web picker | CLI | Status |
|------------|-----|--------|
| Quick answers — 3.5 Flash-Lite | `-m lite` / `-m flash-lite` | works |
| Everyday help — 4 Hydrogen | `-m hydrogen` | accepted; resolves to the account default (auto) — no Hydrogen name exists in WebAPI |
| Thinking level Low / Medium / High | `--thinking low` / `medium` / `high` | works (= standard / plus / extended) |
| — | `--list-models` | returns only `["gemini-自動"]` |

Functions:

| Web function | CLI | Status |
|--------------|-----|--------|
| Create image | `--img` / `--img-gen` | works (live: 1 PNG saved) |
| Create video / Create music | `--save-videos` / `--save-media` | save-only: ask in a prompt, media is saved from the response |
| Canvas | `--extract-canvas FILE` | save-only |
| Deep research | `--deep-research` | DEGRADED on the current build: plan creation returns empty, the fallback returns a stub — now fails `DR_FAILED` instead of `ok:true` |
| Guided Learning | — | web-only, not exposed via WebAPI |
| Notebooks | — | web-only |
| Scheduled / Daily brief | — | web-only |
| Personal Intelligence (Labs), Skills, Avatar, Developer Mode | — | settings; web-only |
| Add from Drive / Google Photos | — | web-only (CLI: local `-f` / `-i` upload only) |
| Gems | Gem chat + `--create-gem` / `--delete-gem` / `--list-gems` | works |
| Temporary chat | `--temporary` | works |
| Chat history | `--list-chats` | returns `[]` on a degraded session; `--read-chat <cid>` / `--fetch-latest <cid>` work |
| Dictate | — | N/A for a CLI |

### Fixed in 4.3 (live-verified 2026-11-14, server build 20261113)

1. **Web model names accepted.** `-m hydrogen` and `-m flash-lite`, plus
   `--thinking low` / `medium` / `high` (the web picker's Thinking level
   names). Hydrogen resolves to the registry's single default (`auto`);
   Flash-Lite resolves to the `LITE` enum. Full-suite live test: 23/23
   offline, and every CLI function verified live except deep research —
   Gem chat (`-m flash`/`pro`/`lite`/`hydrogen`/`flash-lite`), direct
   chat, multi-turn (memory across turns verified with a coding question),
   `--temporary`, `--stream`, `--show-thoughts`, `--img` (1 PNG),
   Gem create+delete, `--read-chat`, `--fetch-latest`, `--gem-info`.
   `-m thinking` still fails fast (no `*THINKING` in the library enum).
2. **Deep research no longer reports success for a stub.** With plan
   creation returning empty, the fallback emitted a ~80-char
   plan-acknowledgement and the CLI printed `ok:true` (once with a 0-byte
   file). Both CLIs now fail `DR_FAILED` when the result is a stub.
3. **`--list-chats` returns `[]`** on the degraded (non-live) session
   even though chats exist — `--read-chat` by id works. Likely
   SNlM0e-gated; needs a fully live session to confirm.

### Fixed in 4.2 (live-verified 2026-11-14, server build 20261113)

The web Gemini picker now defaults to **Hydrogen Medium** (Gemini 4 Hydrogen),
but the WebAPI exposes **no Hydrogen model name**: `--list-models` returns only
`["gemini-自動"]`, and the old `gemini-3-*` names are gone from the registry.
Three fixes, all live-verified against that build:

1. **`-m` was broken for every choice.** `resolve_model_string()` returned the
   raw shorthand (`flash`/`pro`/`lite`/`thinking`), which `generate_content`
   rejects with `GEN_FAILED: Unknown model name`. Shorthands now resolve
   against the live registry first, and fall back to the `Model` enum member
   (`BASIC_FLASH`/`BASIC_PRO`/`BASIC_LITE`), which the library maps onto the
   account's equivalent registered model. `-m thinking` still fails fast:
   `gemini-webapi` 2.1.1's `Model` enum has no `*THINKING` members, so there
   is nothing to map it to — the library error names the available models
   instead of the CLI silently substituting a different model. Image
   generation no longer hardcodes `gemini-3-flash` either (same enum).
2. **Direct chat hung forever when spawned with an open stdin pipe.** `-p`
   (and bare piped prompts) did a plain `sys.stdin.read()`, which blocks
   until EOF — an agent harness with an open stdin pipe hung with zero
   output and no timeout. Stdin is now read only if data arrives within
   2s (`_read_stdin_if_piped`); a real pipe (`cat`/`echo`) is still
   consumed, an idle pipe is treated as empty, and a tty is never read.
3. **Direct chat now shares the 60s / single-attempt cap** already used for
   unresolvable Gem ids, so a request the server never answers fails fast
   with a clear `TIMEOUT` hint instead of burning the full timeout across
   retries.

Open: the CLI cannot select or report the underlying model behind `auto`
(Hydrogen or otherwise) — that routing is server-side. `--account-status`
also surfaces a non-fatal `quota_error` (`GRPC has no attribute
'DEEP_RESEARCH_MODEL_STATE'`): a library/server drift, quota itself is
unaffected.

### Fixed in 4.1 (live-verified 2026-09-20)

A cookie *pair* can be chat-valid while being read-invalid: StreamGenerate answers
(so chat "works") while every SNlM0e-gated call — Gem CRUD, `--list-gems`,
`--list-chats`, `--account-status` — fails. Two root causes, both fixed:

1. **Stale cache shadowed the live browser session.** `resolve_auth()` returned
   `~/.gemini-cli/auth.json` without validating it, so Gem CRUD died with
   `AUTH_EXPIRED: no SNlM0e in /app HTML` and `--list-gems` returned
   `Permission denied … UNAUTHENTICATED` even though the browser session was fine.
   Candidates are now probed once per run with a cheap `/app` request and the first
   live one wins (falling back to the old behaviour only if none is live).
2. **Mixed cookie jars.** The browser-shaped batchexecute client took the full jar
   from Firefox and then overwrote `__Secure-1PSIDTS` with the *cache's* value —
   a jar assembled from two sessions that the server treats as signed out. The jar
   is now taken whole from one source that owns the selected `1PSID`.

Also fixed: `--list-gems`/`--gem-info` fall back to the browser-shaped `LIST_BOTS`
RPC; `--deep-research-status` takes a CHAT id (there is no research-task entity to
look up) and reports whether the report has landed; the pointer's `model` field says
`default` instead of `None` when no `-m` was given; an unresolvable Gem id fails once
with a clear message instead of retrying into a longer hang.

### Pitfalls worth knowing (they cost real debugging time)

- **A chat-valid cookie pair is not a valid session for reads.** StreamGenerate
  accepts the pair even when the session is degraded, so chat keeps working while
  Gem CRUD / `--list-gems` / `--list-chats` fail. `--doctor` tells you which source
  is live; `resolve_auth()` now prefers it automatically.
- **Never assemble a cookie jar from two sessions** (1PSID from one source plus
  1PSIDTS from another is treated as signed out).
- **`gemini_webapi`'s deep-research entry points have no `on_status` hook** —
  passing it raises `TypeError`, which the fallback chain swallowed, so every DR run
  silently degraded to a plain `generate_content` call.
- **An unresolvable Gem id hangs rather than 404s**; unresolved ids now get one
  capped 60s attempt with a message naming both causes (typo vs. inaccessible shared Gem).
- Tests are offline: `python3 -m pytest tests/ -q` (network + cookie stores stubbed).

## Install

```bash
git clone https://github.com/lesterppo/hermes-gem-cli.git
cd hermes-gem-cli
./install.sh
```

Or one-liner:
```bash
curl -fsSL https://raw.githubusercontent.com/lesterppo/hermes-gem-cli/main/install.sh | bash
```

## Quick Start

```bash
# One-time: cache auth tokens from browser
gemini-cli --init

# Direct chat (fastest, no Gem needed)
gemini-cli -p "What is the capital of France?"

# Chat with a Gem
gemini-cli abc123def456 "Explain quantum computing in one sentence"

# Multi-turn (Gem remembers context)
gemini-cli abc123def456 -c /tmp/session.json --new "My name is Alex"
gemini-cli abc123def456 -c /tmp/session.json "What is my name?"

# Image generation
gemini-cli abc123def456 --img "a sunset over mountains"

# Deep research (Pro model recommended)
gemini-cli abc123def456 -m pro --deep-research "Latest CRISPR advancements"

# Create + delete a Gem
gemini-cli --create-gem "MyBot" -p "You are a helpful assistant"
gemini-cli --delete-gem abc123def456
```

## Design Principles

- **Token-efficient**: stdout is always compact JSON (~60-80 chars), full
  response on disk
- **AI-agent-native**: structured JSON output, clear error codes, subprocess-safe
- **URL-first**: paste any shared Gem URL, ID extracted automatically
- **5-tier auth**: env vars → cached file → browser scan → retry → login fallback

## Output Format

**Success:**
```json
{"ok":true,"f":"./out.md","s":1234,"b":2,"imgs":1,"model":"flash","gem":"GemName","dr":true,"c":"c_xxx","t":3}
```

**Error:**
```json
{"ok":false,"err":"RATE_LIMIT","msg":"...","retry_after_s":30,"retry":true}
```

## Related Projects

- **[hermes-gem-pw](https://github.com/lesterppo/hermes-gem-pw)** — CDP/browser
  Gemini CLI. Drives real Chromium via Playwright. Works on WSL2 (where
  webapi hangs). Supports knowledge upload at Gem creation (PDF, GitHub repo,
  folder). Slower (~15-40s) but universally reliable.

## Requirements

- Python 3.10+
- Firefox or Chrome signed into gemini.google.com
- `pip install gemini-webapi browser-cookie3 loguru`
- Native Linux (or macOS — untested). For WSL2, use gem-pw.

## Privacy

Runs entirely on your machine. Auth tokens cached locally at
`~/.gemini-cli/auth.json`. No data sent anywhere except Google's Gemini API.

## License

MIT
