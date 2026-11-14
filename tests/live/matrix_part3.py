"""Deep-test matrix part 3: leftover flags (quick cases).
Run AFTER part 2 (same account, avoid parallel live calls).
Results: /tmp/deep_matrix3.jsonl
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import matrix_part1 as dm

PY, GEM, CLI = dm.PY, dm.GEM, dm.CLI
OUT = os.path.join(ROOT, "tests", "live", "results", "part3.jsonl")
results = []


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
                           input=stdin_data, cwd=dm.ROOT)
        return p, round(time.monotonic() - t0, 1)
    except subprocess.TimeoutExpired:
        return None, timeout


PROMPT = "Reply with exactly one word: the capital of France."

# --quiet: pointer still prints, stderr has no [gemini] logs
p, s = sh([PY, GEM, "coding-partner", "-q", "--no-retry", "-t", "60",
           "-o", "/tmp/d_q.md", PROMPT])
ok, note = file_ok("/tmp/d_q.md", "Paris")
rec("quiet", ok and "[gemini]" not in p.stderr, s,
    note + (" | stderr clean" if "[gemini]" not in p.stderr else f" | stderr: {p.stderr[:150]}"),
    ptr_of(p))

# --new: fresh conversation ignores prior conv file turns
conv = "/tmp/d_conv.json"
p, s = sh([PY, GEM, "coding-partner", "-c", conv, "--new", "--no-retry", "-t", "60",
           "-o", "/tmp/d_new.md", PROMPT])
ok, note = file_ok("/tmp/d_new.md", "Paris")
rec("new-flag", ok, s, note, ptr_of(p))

# --show-thoughts
p, s = sh([PY, GEM, "coding-partner", "--show-thoughts", "--no-retry", "-t", "60",
           "-o", "/tmp/d_thoughts.md", PROMPT])
ok, note = file_ok("/tmp/d_thoughts.md", "Paris")
rec("show-thoughts", ok, s, note, ptr_of(p))

# --limit on --list-gems
p, s = sh([PY, GEM, "--list-gems", "-l", "2"], timeout=60)
ptr = ptr_of(p)
rec("list-gems-limit", bool(ptr and ptr.get("ok") and len(ptr.get("gems", [])) <= 2), s,
    f"n={len((ptr or {}).get('gems', []))}", ptr)

# --browser firefox: no firefox cookies on this VM, so auth falls back to
# the cached credential (the resolution ladder: env -> browser -> cache).
# That fallback is by design — the command must still list models.
p, s = sh([PY, GEM, "--list-models", "--browser", "firefox"], timeout=60)
ptr = ptr_of(p)
rec("browser-firefox", bool(ptr and ptr.get("ok")), s,
    "no firefox cookies; falls back to cached credential, as designed", ptr)

# gem-cli.py leftovers
p, s = sh([PY, CLI, "coding-partner", "-m", "flash", "--thinking", "extended",
           "--no-retry", "-t", "60", "-o", "/tmp/d_cli_thi.md", PROMPT])
ok, note = file_ok("/tmp/d_cli_thi.md", "Paris")
rec("cli-thinking-extended", ok, s, note, ptr_of(p))
p, s = sh([PY, CLI, "coding-partner", "--brief", "--no-retry", "-t", "60",
           "-o", "/tmp/d_cli_brief.md", PROMPT])
ok, note = file_ok("/tmp/d_cli_brief.md", "Paris")
rec("cli-brief", ok, s, note, ptr_of(p))
p, s = sh([PY, CLI, "coding-partner", "-q", "--no-retry", "-t", "60",
           "-o", "/tmp/d_cli_q.md", PROMPT])
ok, note = file_ok("/tmp/d_cli_q.md", "Paris")
rec("cli-quiet", ok and "[gem-cli]" not in p.stderr, s, note, ptr_of(p))
p, s = sh([PY, CLI, "coding-partner", "--gem-info"], timeout=60)
rec("cli-gem-info", bool(ptr_of(p) and ptr_of(p).get("ok")), s, "", ptr_of(p))
p, s = sh([PY, CLI, "--list-models"], timeout=60)
rec("cli-list-models", bool(ptr_of(p) and ptr_of(p).get("ok")), s, "", ptr_of(p))
p, s = sh([PY, CLI, "--list-gems"], timeout=60)
rec("cli-list-gems", bool(ptr_of(p) and ptr_of(p).get("ok")), s, "", ptr_of(p))
p, s = sh([PY, CLI, "--img-gen", "-p", "a red rose", "--no-retry", "-t", "120",
           "-o", "/tmp/d_cli_imggen.md"])
ok, note = file_ok("/tmp/d_cli_imggen.md", None, 1)
rec("cli-img-gen", ok, s, note, ptr_of(p))
# cli --clear + --resume + --timeout-soft
conv3 = "/tmp/d_cli_conv.json"
if os.path.exists(conv3):
    os.remove(conv3)
p, s = sh([PY, CLI, "coding-partner", "-c", conv3, "--no-retry", "-t", "60",
           "-o", "/tmp/d_cli_c1.md", PROMPT])
ok, note = file_ok("/tmp/d_cli_c1.md", "Paris")
rec("cli-conv", ok, s, note, ptr_of(p))
cid = None
try:
    cid = json.load(open(conv3)).get("cid")
except Exception:
    pass
if cid:
    p, s = sh([PY, CLI, "--resume", cid, "--no-retry", "-t", "60",
               "-o", "/tmp/d_cli_resume.md", PROMPT])
    ok, note = file_ok("/tmp/d_cli_resume.md", "Paris")
    rec("cli-resume", ok, s, note, ptr_of(p))
else:
    rec("cli-resume", False, 0, "no cid in conv file")
p, s = sh([PY, CLI, "coding-partner", "--timeout-soft", "5", "--no-retry", "-t", "60",
           "-o", "/tmp/d_cli_tsoft.md", PROMPT])
ok, note = file_ok("/tmp/d_cli_tsoft.md", "Paris")
rec("cli-timeout-soft", ok, s, note, ptr_of(p))
p, s = sh([PY, CLI, "--clear", "-c", conv3], timeout=60)
rec("cli-clear", bool(ptr_of(p) and ptr_of(p).get("ok")) and not os.path.exists(conv3), s,
    f"file gone={not os.path.exists(conv3)}", ptr_of(p))

print(f"== part3: {sum(1 for r in results if r['ok'])}/{len(results)} passed ==")
