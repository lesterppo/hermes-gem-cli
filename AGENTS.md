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

### Agent-optimized flags

Always use these for token-efficient agent interaction:

| Flag | Why |
|------|-----|
| `--raw` | Zero stderr, pure JSON stdout — safe for subprocess piping |
| `--brief` | Prepend "Be concise." — shorter responses |
| `-o FILE` | Response on disk, stdout gets pointer JSON only (~60 chars) |
| `--json-out` | JSON output format instead of markdown |
| `-t SEC` | Timeout (default 120s; auto-extends to 600s for deep research) |
| `--no-retry` | Fail fast on first error (don't loop) |

### Piping data with -p

When both `-p` and stdin are present, stdin content is appended to the
`-p` prompt (separated by a blank line). This lets you pipe data files
while giving instructions:

```bash
cat data.json | gemini.py -p "Analyze this JSON data." -o report.md --json
```

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

## New flags (Aug 2026)

| Flag | Purpose |
|------|---------|
| `--temporary` | Ephemeral chat — not saved to history, pointer `tmp:true` |
| `--show-thoughts` | Include thinking traces (`thoughts` field, `## Thoughts` in md) |
| `--save-videos DIR` | Save Veo videos (`GeneratedVideo`, 3/day Pro), pointer `vids` |
| `--save-media DIR` | Save audio/video media, pointer `media` |
| `--video PROMPT` / `--music PROMPT` | Generate video (Veo) / music (Lyria) via the media tool flag; prime → media turn → poll, pointer has `kind`, `cid`, `download_url`, `files` |
| `--aspect landscape\|portrait` | Video aspect ratio (default landscape) |
| `--fetch-latest CID` | Fetch latest turn for chat (faster than `read-chat`) |
| `--deep-research-status ID` | Poll research status by ID |
| `--extract-canvas FILE` | Save Canvas/HTML artifact (html/svg/canvas block) |
| `--read-chat` | Now returns `{cid, turns:[{role,text}], total}` (fixed) |

## Model + thinking combinations

Shorthands resolve against the account's **live** registered models first,
then fall back to the `Model` enum, which the library maps onto the
account's equivalent registered model (live-verified 2026-11-14: the server
registers only `gemini-自動`, so every shorthand lands on the account
default; no Hydrogen name is exposed via WebAPI).

```
-m flash                        → flash+standard (BASIC_FLASH)
-m flash --thinking extended    → flash+extended
-m pro                          → pro+standard (BASIC_PRO)
-m pro --thinking extended      → pro+extended
-m lite                         → lite+standard (BASIC_LITE)
-m flash-lite                   → lite+standard (web name for 3.5 Flash-Lite)
-m hydrogen                     → auto (web name for 4 Hydrogen; WebAPI has
                                  no Hydrogen name, resolves to the default)
-m hydrogen --thinking high     → auto + extended_thinking (fixed 2026-11-16:
                                  Hydrogen has no Model-enum variant, so the
                                  high tier is carried as the library's
                                  extended_thinking flag, not an enum;
                                  before the fix this failed GEN_FAILED
                                  "Unknown model name: 'hydrogen'")
-m thinking                     → FAILS FAST (GEN_FAILED: no *THINKING
                                  member in gemini-webapi 2.1.1's Model enum)
--thinking low / medium / high  → standard / plus / extended (web Thinking level)
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
gem-cli --list-models    # {"ok":true,"models":["gemini-自動"]} — the live registry; old gemini-3-* names are gone
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

Live status 2026-11-14 (ppoppo205, degraded session): plan creation is
flaky — sometimes it returns empty, sometimes the model refuses the plan
in Chinese. gem-cli.py's manual plan path got a plan twice with `-m pro`
("Gemini 4 Hydrogen Overview") and reached the wait stage; the research
itself takes longer than the 600s auto-extended timeout, so use
`-t 1500` for a full run. gemini-webapi 2.1.1's `wait_for_deep_research`
has no `on_status` — guard with `_supports` before passing it (fixed in
gem-cli.py; gemini.py already guards). When plan creation fails and the
fallback returns only a stub (<200 chars), both CLIs fail `DR_FAILED`
instead of reporting `ok:true`. `--deep-research` without a Gem runs
direct (gem-less) since the 4.4 fix.

Live correction 2026-11-16: the finished report is an immersive document
attached to the turn, NOT the reply text — the reply is only a short notice
("I've completed your research", ~91 chars). The run path used to report
that notice as `ok:true` (gemini.py) or fail `DR_FAILED` on it (gem-cli.py)
even when the report existed. Both CLIs now read the document via
`_dr_report_text` (DeepResearchResult `.document` / ModelOutput
`.deep_research_document`, reply text only as fallback), and
`--deep-research-status` writes the document to its file instead of the
notice. Live-verified: Hydrogen report 28,225 chars / 38 sources
(chat c_5e9dd188fd4c5e5b) and a fresh Carbon run 29,720 chars, both via the
fixed run path. A run that finishes with only the notice and no document
still fails `DR_FAILED`.

## Live server behaviours (verified 2026-11-14)

- `--list-chats` fails `LIST_CHATS_FAILED` when Gemini Activity is off:
  the MaZiqc read-RPC answers `BardErrorInfo 1041, "Turn on Gemini
  Activity to use this feature"`. Turn Activity on in Gemini settings.
- `--delete-chat` removes the conversation from history; the object
  stays readable by `--read-chat <cid>`. That is server behaviour, not
  a failed delete.
- `-g <gem> <prompt>` works in gemini.py (positional prompt used to fall
  through to `NO_PROMPT`).
- `--raw` / `-q` now produce zero stderr (module-level warning prints
  are suppressed in raw mode).
- `--img` / `--deep-research` without a Gem work in gem-cli.py (used to
  fail `NO_URL` / `BAD_URL`); a bare prompt without a Gem runs direct.
- Video/music: generation path added in 4.5 (`--video PROMPT` / `--music PROMPT`,
  both CLIs). The web's Create video/music modes are not exposed by
  gemini-webapi 2.1.1, so the CLI injects the media tool flag into the
  StreamGenerate payload (prime turn → media turn → poll `read_chat` for
  the finished file). Mechanism reverse-engineered from live web traffic;
  see the comment block above `_generate_media` in gemini.py. `--save-videos` /
  `--save-media` choose the download dir. Live 2026-11-14 on ppoppo205:
  **music GREEN** — real track in 66s (MP3 1.5 MB + MP4 4.3 MB + cover,
  `c_639f5a0bbff02910`, `ready_to_fly.mp4`), and again via gem-cli.py
  (`c_5dd338ecc4c4587b`, `we_are_alive_tonight.mp4`); **video pipeline
  verified but subscription-gated** — server verdict `MEDIA_QUOTA` in
  ~31s ("upgrade your subscription"), no clip producible on the free
  account; gem-cli.py's video leg stalled twice (fragment "_923") and
  is not verified. Poll reads only the newest model turn's text (a prime
  turn's "READY" leaked into the verdict before this fix); a failed
  media run now prints the conversation id for re-reading.

## Pitfalls

- Deep research requires Pro model (Flash may hang at plan creation)
- Pro + extended thinking can exceed default 120s timeout — use `-t 180`
- Image generation requires Flash (auto-selected with `--img`)
- Cookies expire ~30 days — run `gem-cli --init` to refresh
- `browser_cookie3` is only needed for Tier 3 auth; set env vars for CI
