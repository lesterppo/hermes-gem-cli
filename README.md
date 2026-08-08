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
| Model selection | `-m flash` / `pro` / `lite` | — |
| Extended thinking | `--thinking extended` | adds ~30-60s |
| Temporary chat | `--temporary` | ephemeral, not in history |
| Thinking traces | `--show-thoughts` | adds thoughts to output |
| Video generation | `--save-videos DIR` | Veo 3, 3/day Pro |
| Canvas artifact | `--extract-canvas FILE` | saves html block |
| Fetch latest | `--fetch-latest c_xxx` | fast latest turn |
| Deep research status | `--deep-research-status ID` | poll by ID |
| Streaming output | `--stream` | real-time tokens |

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
