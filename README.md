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
| Video generation | `--video PROMPT [--aspect portrait]` | Veo 3, primes a chat then polls for the finished clip; saves to `--save-videos DIR` |
| Music generation | `--music PROMPT` | Lyria, same prime + poll pipeline; saves MP3+MP4 to `--save-media DIR` |
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
| Create video / Create music | `--video PROMPT` / `--music PROMPT` | generation path added in 4.5: media tool flag injected into the StreamGenerate payload (prime turn → media turn → poll `read_chat` for the finished file); `--save-videos` / `--save-media` choose the download dir |
| Canvas | `--extract-canvas FILE` | save-only |
| Deep research | `--deep-research` | DEGRADED on the current build: plan creation returns empty, the fallback returns a stub — now fails `DR_FAILED` instead of `ok:true` |
| Guided Learning | — | web-only, not exposed via WebAPI |
| Notebooks | — | web-only |
| Scheduled / Daily brief | — | web-only |
| Personal Intelligence (Labs), Skills, Avatar, Developer Mode | — | settings; web-only |
| Add from Drive / Google Photos | — | web-only (CLI: local `-f` / `-i` upload only) |
| Gems | Gem chat + `--create-gem` / `--delete-gem` / `--list-gems` | works |
| Temporary chat | `--temporary` | works |
| Chat history | `--list-chats` | fails `LIST_CHATS_FAILED` when Gemini Activity is off (live: ppoppo205, error 1041 "Turn on Gemini Activity to use this feature"); turn Activity on in Gemini settings and it lists; `--read-chat <cid>` / `--fetch-latest <cid>` work regardless |
| Dictate | — | N/A for a CLI |

### Fixed in 4.5 (live-verified 2026-11-14, server build 20261113)

Video/music generation now works through the CLI. gemini-webapi 2.1.1
exposes no Create video/music mode, so both CLIs inject the web's media
tool flag into the StreamGenerate payload (prime turn → media turn with
the flag → poll `read_chat` for the finished file). New flags:
`--video PROMPT` / `--music PROMPT` / `--aspect portrait`,
`--save-videos` / `--save-media` choose the download dir. Timeout
auto-extends (video 600s, music 300s); a quota/subscription verdict on
the newest turn ends the poll early instead of burning the timeout.

1. **Music is GREEN.** `--music` produced a real track on ppoppo205 at
   20:41 HKT (66s: MP3 1.5 MB 192 kbps + MP4 4.3 MB + cover art,
   conversation `c_639f5a0bbff02910`, file `ready_to_fly.mp4`). The
   first attempt failed only because the account quota was exhausted
   (0/600 credits, reset 20:40:02) — the retry after the reset worked.
2. **Video pipeline verified, account-gated.** `--video` reaches the
   Veo tool and the server's verdict surfaces cleanly in ~31s
   (`MEDIA_QUOTA`: "If you upgrade your subscription, I can create
   that video for you today"). A finished clip is unverifiable on the
   free account — it needs Pro/Ultra. The mechanism is the same one
   music proved, with the gemini-3-video model header. Caveat: the
   video turn is nondeterministic on this account — gem-cli.py's two
   attempts stalled instead (400s cut off, then a full 600s poll ending
   on a "_923" fragment, no verdict), so its video leg is not verified
   even though it runs identical code. A failed media run now prints
   the conversation id (`cid`) so the stalled turn can be re-read.
3. **Poll-verdict bug fixed.** The first runs reported `MEDIA_FAILED
   "READY"` — the poll read the prime turn's text as the verdict. Only
   the newest model turn's text now counts. Offline suite 39/39
   (10 new media tests, incl. this regression and the cid test).

Still blocked (server/session, not CLI): **deep research produces no
report on this session** — the plan is created and the research runs
(~14 min), but the final output is a stub, so the CLI fails `DR_FAILED`
instead of reporting success. Needs a fully live session to confirm
whether it works there. **Image generation quota was exhausted at the
time of the probe** (`--img` path itself works — the deep test saved
1 PNG earlier). **`-m thinking` still fails fast**
(no `*THINKING` in the library enum).

### Fixed in 4.4 (live deep test 2026-11-14, server build 20261113)

Full-function live+deep test: ~80 cases across both CLIs (every flag,
uploads, media, Gem CRUD, multi-turn, resume, soak 5x). 29/29 offline.
Fixes from the deep test:

1. **`-g <gem> <prompt>` works** (gemini.py). Positional prompt words
   dumped into `args.url` by `parse_intermixed_args` were never merged
   back, so the run failed `NO_PROMPT`. Verified quoted and unquoted
   (unquoted `Double the number 21 ...` → `42`, proving the first word
   survives even when it matches the Gem-id pattern).
2. **`--raw` is now truly silent** (gemini.py). The no-live-session
   warning and the BrowserBatchRPC session notice printed directly to
   stderr from module helpers, before any CLI object existed. Both are
   now gated on a module-level raw flag. (gem-cli.py `--init` message
   gated the same way.)
3. **`--list-chats` fails honestly.** See 4.3 item 3 — the root cause is
   Gemini Activity off (1041), not a degraded session.
4. **gem-cli.py standalone modes work without a Gem.** `--img`,
   `--deep-research`, `--resume` used to fail `NO_URL` / `BAD_URL`;
   they now run direct (gem-less). A bare prompt merges back into
   `args.prompt` but then fails `NO_URL` unless a standalone mode is
   given (live 2026-11-20); gemini.py runs bare prompts direct.
   `--resume <cid>` verified live.
5. **Deep-research `on_status` crash fixed** (gem-cli.py). The ladder
   passed `on_status` to `wait_for_deep_research`, which
   gemini-webapi 2.1.1 does not accept — every DR run died with
   `GEN_FAILED` after the plan was created and started. Now guarded by
   `_supports`, like gemini.py.
6. **Plan-creation retries.** The plan endpoint is flaky on a degraded
   session (~3/5 attempts return empty or a refusal). Both CLIs now
   retry plan creation up to 3 times (15s apart) instead of failing the
   whole run on the first miss.

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
3. **`--list-chats` no longer prints an empty list as success.** The
   MaZiqc read-RPC was returning a server error, not an empty history:
   `BardErrorInfo 1041, "Turn on Gemini Activity to use this feature"`
   (Gemini Activity is off on the ppoppo205 account). The CLI now fails
   `LIST_CHATS_FAILED` with the server's message. Turn Activity on in
   Gemini settings and the command lists chats.

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
