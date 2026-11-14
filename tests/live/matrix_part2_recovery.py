"""Deep-test matrix part 2b: RECOVERY run after the VM restart wiped /tmp
and interrupted part 2 mid-DR. Re-runs only what part 2 never finished
(plus the two cases whose checks were fixed after part 2 started).
Results go to the WORKSPACE (not /tmp) so a restart can't wipe them:
  ~/workspace/gem-cli-test/deep_results/part2b.jsonl
"""
import glob
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root
PY = os.path.join(ROOT, ".venv", "bin", "python")
GEM = os.path.join(ROOT, "gemini.py")
CLI = os.path.join(ROOT, "gem-cli.py")
OUTDIR = os.path.join(ROOT, "tests", "live", "results")
os.makedirs(OUTDIR, exist_ok=True)
OUT = os.path.join(OUTDIR, "part2b.jsonl")
results = []


def rec(name, ok, secs, note="", ptr=None):
    r = {"name": name, "ok": bool(ok), "secs": secs, "note": note, "ptr": ptr}
    results.append(r)
    with open(OUT, "a") as fh:
        fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{'PASS' if ok else 'FAIL'} {name} ({secs}s) {note}", flush=True)


def sh(argv, timeout=120, stdin_data=None):
    t0 = time.monotonic()
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                           input=stdin_data, cwd=ROOT)
        return p, round(time.monotonic() - t0, 1)
    except subprocess.TimeoutExpired:
        return None, timeout


def ptr_of(p):
    if p is None:
        return None
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        return None


def file_ok(path, needle=None, min_bytes=1):
    if not os.path.exists(path):
        return False, f"missing {path}"
    n = os.path.getsize(path)
    if n < min_bytes:
        return False, f"{path} only {n}B"
    if needle:
        txt = open(path, encoding="utf-8", errors="ignore").read()
        if needle not in txt:
            return False, f"lacks {needle!r}"
    return True, f"{n}B"


PROMPT = "Reply with exactly one word: the capital of France."

# ── corrected cases (checks fixed after part 2 started) ──
conv = "/tmp/d2b_conv.json"
if os.path.exists(conv):
    os.remove(conv)
p, s = sh([PY, GEM, "coding-partner", "-c", conv, "--no-retry", "-t", "60",
           "-o", "/tmp/d2b_mt1.md",
           "Define a variable called magic_number and set it to 7. Reply with the code only."])
ok, note = file_ok("/tmp/d2b_mt1.md", "7")
rec("multiturn-1", ok, s, note, ptr_of(p))
p, s = sh([PY, GEM, "coding-partner", "-c", conv, "--no-retry", "-t", "60",
           "-o", "/tmp/d2b_mt2.md", "Print magic_number. Reply with the code only."])
ok, note = file_ok("/tmp/d2b_mt2.md", "magic_number")
rec("multiturn-2-memory", ok, s, note + " (turn 2 must reference the turn-1 variable)", ptr_of(p))

conv2 = "/tmp/d2b_delconv.json"
if os.path.exists(conv2):
    os.remove(conv2)
p, s = sh([PY, GEM, "-c", conv2, "--no-retry", "-t", "60", "-o", "/tmp/d2b_del1.md", PROMPT])
cid2 = None
try:
    cid2 = json.load(open(conv2)).get("cid")
except Exception:
    pass
if cid2:
    p, s = sh([PY, GEM, "--delete-chat", cid2], timeout=60)
    rec("delete-chat", bool(ptr_of(p) and ptr_of(p).get("ok")), s, f"cid={cid2}", ptr_of(p))
    p, s = sh([PY, GEM, "--read-chat", cid2], timeout=60)
    ptr = ptr_of(p)
    rec("delete-chat-verified", bool(ptr and ptr.get("ok") and
                                     (ptr.get("chat") or {}).get("cid") == cid2), s,
        "read-back still returns the chat object (delete removes the history entry, not the object)",
        ptr)
else:
    rec("delete-chat", False, 0, "no cid in conv file")

# ── H. deep research (validates stub guard live) + status ──
p, s = sh([PY, GEM, "--deep-research", "-m", "pro", "--no-retry", "-t", "580",
           "-o", "/tmp/d2b_dr.md",
           "What is Gemini 4 Hydrogen? Summarize in 5 bullet points: announcement date, key capabilities, and how it differs from Gemini 3."],
          timeout=590)
ptr = ptr_of(p)
rec("deep-research-guard", bool(ptr and ptr.get("ok") is False and ptr.get("err") == "DR_FAILED"),
    s, (ptr or {}).get("msg", ""), ptr)
p, s = sh([PY, GEM, "--deep-research-status", "fake-research-id-123"], timeout=60)
rec("dr-status-bogus", bool(ptr_of(p) and not ptr_of(p).get("ok")), s,
    f"err={(ptr_of(p) or {}).get('err')}", ptr_of(p))

# ── I. --init (expected environmental fail) ──
p, s = sh([PY, GEM, "--init"], timeout=60)
rec("init-no-browser", bool(ptr_of(p) and not ptr_of(p).get("ok")), s,
    f"err={(ptr_of(p) or {}).get('err')}", ptr_of(p))

# ── J. gem-cli.py subset ──
p, s = sh([PY, CLI, "coding-partner", "-m", "hydrogen", "--no-retry", "-t", "60",
           "-o", "/tmp/d2b_cli_hyd.md", PROMPT])
ok, note = file_ok("/tmp/d2b_cli_hyd.md", "Paris")
rec("cli-hydrogen", ok, s, note, ptr_of(p))
p, s = sh([PY, CLI, "--img", "a green leaf", "--no-retry", "-t", "120", "-o", "/tmp/d2b_cli_img.md"])
rec("cli-img", bool(ptr_of(p) and ptr_of(p).get("ok")), s, "", ptr_of(p))
p, s = sh([PY, CLI, "coding-partner", "--extract-code", "1", "--no-retry", "-t", "60",
           "-o", "/tmp/d2b_cli_code.md",
           "Write a Python function sub(a, b) that returns their difference. Reply with code only."])
ok, note = file_ok("/tmp/d2b_cli_code.md", "def sub")
rec("cli-extract-code", ok, s, note, ptr_of(p))
p, s = sh([PY, CLI, "coding-partner", "--json-out", "--no-retry", "-t", "60",
           "-o", "/tmp/d2b_cli_json.md", PROMPT])
ok, note = file_ok("/tmp/d2b_cli_json.md", "Paris")
rec("cli-json-out", ok, s, note, ptr_of(p))
p, s = sh([PY, CLI, "coding-partner", "--raw", "--no-retry", "-t", "60",
           "-o", "/tmp/d2b_cli_raw.md", PROMPT])
ok, note = file_ok("/tmp/d2b_cli_raw.md", "Paris")
rec("cli-raw-stderr-empty", ok and p.stderr == "", s,
    note + (" | stderr empty" if p.stderr == "" else f" | STDERR LEAK: {p.stderr[:200]}"),
    ptr_of(p))
p, s = sh([PY, CLI, "--create-gem", "muse-cli-deep-1114", "-p",
           "You are a test gem. Reply with exactly one word: the capital of France."],
          timeout=120, stdin_data="You are a test gem for the deep test.")
ptr = ptr_of(p)
cg_id = (ptr or {}).get("gem_id") or (ptr or {}).get("id")
rec("cli-gem-create", bool(cg_id), s, f"id={cg_id}", ptr)
if cg_id:
    p, s = sh([PY, CLI, "--delete-gem", cg_id], timeout=60)
    rec("cli-gem-delete", bool(ptr_of(p) and ptr_of(p).get("ok")), s, "", ptr_of(p))
p, s = sh([PY, CLI, "--deep-research", "-m", "pro", "--no-retry", "-t", "580",
           "-o", "/tmp/d2b_cli_dr.md",
           "What is Gemini 4 Hydrogen? Summarize in 3 bullet points."],
          timeout=590)
ptr = ptr_of(p)
rec("cli-deep-research-guard", bool(ptr and ptr.get("ok") is False and ptr.get("err") == "DR_FAILED"),
    s, (ptr or {}).get("msg", ""), ptr)

# ── K. soak: same Gem chat 5x ──
soak_ok = 0
for i in range(5):
    p, s = sh([PY, GEM, "coding-partner", "-m", "flash", "--no-retry", "-t", "60",
               "-o", f"/tmp/d2b_soak{i}.md", PROMPT], timeout=90)
    ok, _ = file_ok(f"/tmp/d2b_soak{i}.md", "Paris")
    soak_ok += int(ok)
rec("soak-5x-flash", soak_ok == 5, 0, f"{soak_ok}/5 Paris")

print(f"== part2b: {sum(1 for r in results if r['ok'])}/{len(results)} passed ==")
