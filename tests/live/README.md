# Live deep-test harness

Live (WebAPI) test matrix for `gemini.py` / `gem-cli.py`. These scripts hit
the real Gemini WebAPI — they need a live session (`~/.gemini-cli/auth.json`,
see `--init` / the bridge in AGENTS.md) and the `coding-partner` Gem.
Run them in order; do not run two parts in parallel (same account).

- `matrix_part1.py` — stateless cases: discovery, chat core, model aliases,
  expected failures. ~5 min.
- `matrix_part2.py` — stateful cases: multi-turn, resume, extract,
  uploads, media, Gem CRUD, delete-chat, clear, deep research, gem-cli.py
  subset, soak 5x. ~15 min (two deep-research runs dominate).
- `matrix_part2_recovery.py` — the part-2 cases that never finished after
  a VM restart (DR guard, cli subset, soak) plus the two corrected checks.
- `matrix_part3.py` — leftover flags (`-q`, `--new`, `--show-thoughts`,
  `--limit`, `--browser`, gem-cli.py leftovers). ~3 min. Run after part 2.
- `diag_rpc.py` — in-process diagnosis: list-chats MaZiqc RPC + deep
  research plan creation. Prints structure only, no cookie values.

Results land in `results/*.jsonl` (one JSON object per case: name, ok,
secs, note, pointer). `results/DEEP_TEST_2026-11-14.md` is the
human-readable record of the 2026-11-14 run.

Known environment notes (2026-11-14): the ppoppo205 session is degraded
(`live: false`); Gemini Activity is off on that account, so `--list-chats`
fails `LIST_CHATS_FAILED` (1041) by design; deep research creates a plan
and runs but returns only a stub, so the CLI fails `DR_FAILED`.
