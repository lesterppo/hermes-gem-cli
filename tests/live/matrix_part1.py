"""Live+deep test matrix runner for gem-cli / gemini.py (WebAPI only).

Runs each case as a subprocess with a timeout, records a JSONL verdict
line per case: name, ok, secs, pointer/file evidence. Expected failures
are cases where the CORRECT outcome is a fast, clear error — those pass
when the error code matches.

Usage: python3 deep_matrix.py [--only SUBSTR] [--skip DR]
Results: /tmp/deep_matrix.jsonl
"""
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root
PY = os.path.join(ROOT, ".venv", "bin", "python")
OUT = os.path.join(ROOT, "tests", "live", "results", "part1.jsonl")
GEM = os.path.join(ROOT, "gemini.py")
CLI = os.path.join(ROOT, "gem-cli.py")
STATE = {}


def run_case(name, argv, timeout=90, stdin_data=None, check=None, expect_fail=None):
    t0 = time.monotonic()
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                           input=stdin_data, cwd=ROOT)
        secs = round(time.monotonic() - t0, 1)
        out, err = p.stdout.strip(), p.stderr.strip()
        ptr = None
        try:
            ptr = json.loads(out.splitlines()[-1]) if out else None
        except Exception:
            ptr = None
        rec = {"name": name, "rc": p.returncode, "secs": secs,
               "ptr": ptr, "out_tail": out[-300:] if out else "",
               "err_tail": err[-300:] if err else ""}
        if expect_fail:
            ok = bool(ptr and ptr.get("ok") is False and ptr.get("err") == expect_fail)
            rec["ok"] = ok
            rec["note"] = f"expected fail {expect_fail}"
        elif check:
            ok, note = check(p, ptr)
            rec["ok"] = ok
            rec["note"] = note
        else:
            rec["ok"] = bool(ptr and ptr.get("ok") is True)
            rec["note"] = ""
        return rec
    except subprocess.TimeoutExpired:
        return {"name": name, "rc": "TIMEOUT", "secs": timeout,
                "ok": False, "note": f"no output within {timeout}s"}


def file_has(path, needle=None, min_bytes=1):
    def _check(p, ptr):
        if not os.path.exists(path):
            return False, f"missing {path}"
        n = os.path.getsize(path)
        if n < min_bytes:
            return False, f"{path} only {n} bytes"
        if needle:
            txt = open(path, encoding="utf-8", errors="ignore").read()
            if needle not in txt:
                return False, f"{path} lacks {needle!r}"
        return True, f"{path} {n}B"
    return _check


def both(p, ptr):
    if not (ptr and ptr.get("ok") is True):
        return False, "pointer not ok"
    return True, ""


CASES = []


def case(name, argv, **kw):
    CASES.append((name, argv, kw))


# --- discovery / auth ---
case("doctor", [PY, GEM, "--doctor"], timeout=60)
case("account-status", [PY, GEM, "--account-status"], timeout=60)
case("list-models", [PY, GEM, "--list-models"], timeout=60)
case("list-gems", [PY, GEM, "--list-gems"], timeout=60)
case("list-chats", [PY, GEM, "--list-chats"], timeout=60)
case("gem-info", [PY, GEM, "coding-partner", "--gem-info"], timeout=60)
# --- chat core (gemini.py) ---
case("gem-flash", [PY, GEM, "coding-partner", "-m", "flash", "--no-retry", "-t", "60",
                   "-o", "/tmp/d_flash.md", "Reply with exactly one word: the capital of France."],
     check=file_has("/tmp/d_flash.md", "Paris"))
case("gem-pro", [PY, GEM, "coding-partner", "-m", "pro", "--no-retry", "-t", "60",
                 "-o", "/tmp/d_pro.md", "Reply with exactly one word: the capital of France."],
     check=file_has("/tmp/d_pro.md", "Paris"))
case("gem-lite", [PY, GEM, "coding-partner", "-m", "lite", "--no-retry", "-t", "60",
                  "-o", "/tmp/d_lite.md", "Reply with exactly one word: the capital of France."],
     check=file_has("/tmp/d_lite.md", "Paris"))
case("gem-hydrogen", [PY, GEM, "coding-partner", "-m", "hydrogen", "--no-retry", "-t", "60",
                      "-o", "/tmp/d_hyd.md", "Reply with exactly one word: the capital of France."],
     check=file_has("/tmp/d_hyd.md", "Paris"))
case("gem-flash-lite", [PY, GEM, "coding-partner", "-m", "flash-lite", "--no-retry", "-t", "60",
                        "-o", "/tmp/d_fl.md", "Reply with exactly one word: the capital of France."],
     check=file_has("/tmp/d_fl.md", "Paris"))
case("gem-thinking-failfast", [PY, GEM, "coding-partner", "-m", "thinking", "--no-retry", "-t", "60",
                               "-o", "/tmp/d_think.md", "Reply with exactly one word: the capital of France."],
     expect_fail="GEN_FAILED")
case("gem-thinking-high", [PY, GEM, "coding-partner", "-m", "flash", "--thinking", "high",
                           "--no-retry", "-t", "60", "-o", "/tmp/d_thi.md",
                           "Reply with exactly one word: the capital of France."],
     check=file_has("/tmp/d_thi.md", "Paris"))
case("direct", [PY, GEM, "-p", "Reply with exactly one word: the capital of France.",
                "--no-retry", "-t", "60", "-o", "/tmp/d_direct.md"],
     check=file_has("/tmp/d_direct.md", "Paris"), stdin_data="")
case("direct-open-pipe",
     ["bash", "-c",
      f"sleep 30 | {PY} {GEM} -p 'Reply with exactly one word: the capital of France.' "
      f"--no-retry -t 60 -o /tmp/d_pipe.md"],
     timeout=90, check=file_has("/tmp/d_pipe.md", "Paris"))
case("brief", [PY, GEM, "coding-partner", "--brief", "--no-retry", "-t", "60",
               "-o", "/tmp/d_brief.md", "Reply with exactly one word: the capital of France."],
     check=file_has("/tmp/d_brief.md", "Paris"))
case("json-out", [PY, GEM, "coding-partner", "--json-out", "--no-retry", "-t", "60",
                  "-o", "/tmp/d_json.md", "Reply with exactly one word: the capital of France."],
     check=file_has("/tmp/d_json.md", "Paris"))
case("raw-stderr-empty", [PY, GEM, "coding-partner", "--raw", "--no-retry", "-t", "60",
                          "-o", "/tmp/d_raw.md", "Reply with exactly one word: the capital of France."])
case("temporary", [PY, GEM, "coding-partner", "--temporary", "--no-retry", "-t", "60",
                   "-o", "/tmp/d_tmp.md", "Reply with exactly one word: the capital of France."],
     check=file_has("/tmp/d_tmp.md", "Paris"))
case("stream", [PY, GEM, "coding-partner", "--stream", "--no-retry", "-t", "60",
                "-o", "/tmp/d_stream.md", "Reply with exactly one word: the capital of France."],
     check=file_has("/tmp/d_stream.md", "Paris"))
case("gem-flag", [PY, GEM, "-g", "coding-partner", "--no-retry", "-t", "60",
                  "-o", "/tmp/d_gflag.md", "Reply with exactly one word: the capital of France."],
     check=file_has("/tmp/d_gflag.md", "Paris"))
case("bad-gem-id", [PY, GEM, "not-a-real-gem-xyz", "--no-retry", "-t", "70",
                    "-o", "/tmp/d_badgem.md", "hello"],
     expect_fail="TIMEOUT")
case("no-prompt", [PY, GEM, "coding-partner", "--no-retry", "-t", "30"],
     expect_fail="NO_PROMPT", stdin_data="")
case("timeout-soft", [PY, GEM, "coding-partner", "--timeout-soft", "5", "--no-retry", "-t", "60",
                      "-o", "/tmp/d_tsoft.md", "Reply with exactly one word: the capital of France."],
     check=file_has("/tmp/d_tsoft.md", "Paris"))


def main():
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    skip = sys.argv[sys.argv.index("--skip") + 1].split(",") if "--skip" in sys.argv else []
    with open(OUT, "w") as fh:
        for name, argv, kw in CASES:
            if only and only not in name:
                continue
            if name in skip:
                continue
            rec = run_case(name, argv, **kw)
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            print(f"{'PASS' if rec['ok'] else 'FAIL'} {name} ({rec.get('secs')}s) {rec.get('note','')}", flush=True)
    n = sum(1 for _ in open(OUT))
    ok = sum(1 for line in open(OUT) if json.loads(line)["ok"])
    print(f"== {ok}/{n} passed ==")


if __name__ == "__main__":
    main()
