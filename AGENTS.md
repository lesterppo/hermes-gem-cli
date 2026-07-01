# AGENTS.md — Instructions for AI Coding Agents

This repo provides `gem-cli`, an AI-agent-native CLI for interacting with shared
Gemini Gems. If you are an AI agent and you found this repo, here is how to use it.

## What this tool does

Takes any shared Gemini Gem URL (e.g., `https://gemini.google.com/gem/<ID>`),
extracts the Gem ID, authenticates via browser cookies or env vars, and sends
prompts to the Gem. All responses go to disk; stdout is a compact JSON pointer.

## How to invoke

```bash
gem-cli "<gem-url-or-id>" "prompt"
gem-cli "<gem-url>" -m flash --brief --raw "prompt"   # agent-optimized
echo "prompt" | gem-cli "<gem-id>" --raw               # stdin, pure JSON
```

## Agent-optimized flags

Always use these for token-efficient agent interaction:

| Flag | Why |
|------|-----|
| `--raw` | Zero stderr, pure JSON stdout — safe for subprocess piping |
| `--brief` | Prepend "Be concise." — shorter responses |
| `-o FILE` | Response on disk, stdout gets pointer JSON only (~60 chars) |
| `--json-out` | JSON output format instead of markdown |
| `-t SEC` | Timeout (default 120s; auto-extends to 600s for deep research) |
| `--no-retry` | Fail fast on first error (don't loop) |

## Output parsing

Stdout is always valid JSON:
```json
{"ok":true,"f":"./out.md","s":1234,"model":"flash","gem":"GemName"}
{"ok":false,"err":"AUTH_EXPIRED","msg":"...","retry":false}
```

Read the output file at path `f` for the full response. The field `s` is the file
size in bytes — use it to decide if you need the full content.

## Auth setup

First time:
```bash
# Option A: env vars (CI/remote)
export GEMINI_SID="..." GEMINI_TS="..."

# Option B: browser cache (local)
gem-cli --init     # extracts cookies from Firefox/Chrome
gem-cli --login    # opens browser for interactive sign-in
```

Auth is resolved in this priority order:
1. `GEMINI_SID` + `GEMINI_TS` env vars (CI/remote)
2. `~/.gemini-cli/auth.json` cached file
3. Browser cookie scan (Firefox → Chrome → Edge → Safari)
4. Auto-retry with cookie re-scan (3 attempts)
5. `--login` browser flow

## Model + thinking combinations

```
-m flash                        → flash
-m flash --thinking extended    → flash+extended
-m pro                          → pro
-m pro --thinking extended      → pro+extended
-m thinking                     → thinking+standard
-m thinking --thinking extended → thinking+extended
```

## Error handling

Check `err` field for error category:
- `AUTH_EXPIRED` — cookies expired, retryable with re-auth
- `RATE_LIMIT` — rate limited, has `retry_after_s` field
- `TIMEOUT` — request timed out, has `timeout_s` field
- `GEN_FAILED` — bad prompt/model/Gem, NOT retryable
- `BAD_URL` — couldn't extract Gem ID from URL

## Conversation management

```bash
gem-cli "<id>" -c sess.json --new "start"    # new conversation
gem-cli "<id>" -c sess.json "continue"       # continues same session
gem-cli "<id>" -c sess.json --clear           # delete conversation
```

Conversation state is a JSON file — portable between machines.

## Discovery

```bash
gem-cli --list-models    # {"ok":true,"models":["gemini-3-flash",...]}
gem-cli --list-gems      # {"ok":true,"gems":[{"id":"...","name":"...","type":"user"},...]}
gem-cli "<url>" --gem-info  # {"ok":true,"gem":{"id":"...","name":"...","type":"external"}}
```

## Deep research

```bash
gem-cli "<url>" -m pro --deep-research "research question"
# → auto-creates plan, searches web, synthesizes report
# → pointer includes "dr":true
# → timeout auto-extends to 600s
```

## Pitfalls

- Deep research requires Pro model (Flash may hang at plan creation)
- Pro + extended thinking can exceed default 120s timeout — use `-t 180`
- Image generation requires Flash (auto-selected with `--img`)
- Cookies expire ~30 days — run `gem-cli --init` to refresh
- `browser_cookie3` is only needed for Tier 3 auth; set env vars for CI
