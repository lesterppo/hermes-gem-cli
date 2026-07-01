# hermes-gem-cli

AI-agent-native, token-efficient CLI for interacting with shared Gemini Gems.
Takes any shared Gem URL, extracts the Gem ID, and lets you chat with it.

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
gem-cli --init

# Chat with a shared Gem
gem-cli "https://gemini.google.com/gem/<GEM_ID>" "Hello, what can you help with?"

# Multi-turn conversation
gem-cli "<GEM_ID>" -c sess.json --new "first message"
gem-cli "<GEM_ID>" -c sess.json "follow-up question"

# With model selection and thinking tiers
gem-cli "<GEM_ID>" -m pro --thinking extended "deep analysis"

# Image generation
gem-cli "<GEM_ID>" --img "a cat reading a book"

# Deep research (auto-plans, web search, synthesis)
gem-cli "<GEM_ID>" -m pro --deep-research "complex research question"
```

## Design Principles

- **Token-efficient**: stdout is always a compact JSON pointer (~60-80 chars), full response on disk
- **AI-agent-native**: structured JSON output, clear error codes, subprocess-safe piping
- **URL-first**: paste any shared Gem URL, it extracts the ID automatically
- **5-tier auth**: env vars → cached file → browser scan → retry → login fallback

## Features

| Feature | Flag |
|---------|------|
| Multi-turn conversation | `-c FILE --new` |
| File upload (PDF, TXT, CSV) | `-f FILE` |
| Image upload | `-i FILE` |
| Image generation | `--img PROMPT` |
| Model switch | `-m flash/pro/thinking/lite` |
| Thinking tiers | `--thinking standard/plus/extended` |
| Deep research | `--deep-research` |
| Structured JSON output | `--json-out` |
| Pure JSON (zero stderr) | `--raw` |
| Brief responses | `--brief` |
| Timeout control | `-t SEC` |

## Output Format

**Stdout** (always compact JSON):
```json
{"ok":true,"f":"./out.md","s":1234,"b":2,"imgs":1,"model":"pro+extended","gem":"GemName","dr":true,"c":"c_xxx","t":3}
```

**Error** (structured):
```json
{"ok":false,"err":"RATE_LIMIT","msg":"...","retry_after_s":30,"retry":true}
```

## Requirements

- Python 3.10+
- Firefox or Chrome signed into gemini.google.com
- Dependencies: `gemini-webapi`, `browser-cookie3`, `loguru`

## Privacy

This tool runs entirely on your machine. No data is sent anywhere except to Google's Gemini API (same as using gemini.google.com in your browser). Auth tokens are cached locally at `~/.gemini-cli/auth.json`.

## License

MIT
