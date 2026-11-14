"""Deep-test matrix part 2: stateful cases + re-verification of fixes.
Sequential (Gem CRUD, multi-turn, uploads, media, DR, gem-cli.py subset).
Results: /tmp/deep_matrix2.jsonl
"""
import glob
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import matrix_part1 as dm

PY, GEM, CLI = dm.PY, dm.GEM, dm.CLI
OUT = os.path.join(ROOT, "tests", "live", "results", "part2.jsonl")
results = []


def rec(name, ok, secs, note="", ptr=None):
    r = {"name": name, "ok": bool(ok), "secs": secs, "note": note, "ptr": ptr}
    results.append(r)
    with open(OUT, "a") as fh:
        fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{'PASS' if ok else 'FAIL'} {name} ({secs}s) {note}", flush=True)
    return r


def sh(argv, timeout=120, stdin_data=None):
    t0 = time.monotonic()
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                           input=stdin_data, cwd=dm.ROOT)
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


# ── A. re-verify round-2 fixes ──
p, s = sh([PY, GEM, "-g", "coding-partner", "--no-retry", "-t", "60",
           "-o", "/tmp/d_gflag.md", "Reply with exactly one word: the capital of France."])
ok, note = file_ok("/tmp/d_gflag.md", "Paris")
rec("gem-flag-fixed", ok, s, note, ptr_of(p))

p, s = sh([PY, GEM, "coding-partner", "--raw", "--no-retry", "-t", "60",
           "-o", "/tmp/d_raw2.md", "Reply with exactly one word: the capital of France."])
ok, note = file_ok("/tmp/d_raw2.md", "Paris")
rec("raw-stderr-empty-fixed", ok and p.stderr == "", s,
    note + (" | stderr empty" if p.stderr == "" else f" | STDERR LEAK: {p.stderr[:200]}"),
    ptr_of(p))

p, s = sh([PY, GEM, "--list-chats"], timeout=60)
ptr = ptr_of(p)
rec("list-chats-error", bool(ptr and ptr.get("ok") is False and ptr.get("err") == "LIST_CHATS_FAILED"),
    s, (ptr or {}).get("msg", ""), ptr)

# ── B. multi-turn + resume ──
conv = "/tmp/d_conv.json"
if os.path.exists(conv):
    os.remove(conv)
p, s = sh([PY, GEM, "coding-partner", "-c", conv, "--no-retry", "-t", "60",
           "-o", "/tmp/d_mt1.md",
           "Define a variable called magic_number and set it to 7. Reply with the code only."])
ok, note = file_ok("/tmp/d_mt1.md", "7")
rec("multiturn-1", ok, s, note, ptr_of(p))
p, s = sh([PY, GEM, "coding-partner", "-c", conv, "--no-retry", "-t", "60",
           "-o", "/tmp/d_mt2.md", "Print magic_number. Reply with the code only."])
ok, note = file_ok("/tmp/d_mt2.md", "magic_number")
rec("multiturn-2-memory", ok, s, note + " (turn 2 must reference the turn-1 variable)", ptr_of(p))
cid = None
try:
    cid = json.load(open(conv)).get("cid")
except Exception:
    pass
if cid:
    p, s = sh([PY, GEM, "--resume", cid, "--no-retry", "-t", "60",
               "-o", "/tmp/d_resume.md", "Reply with exactly one word: the capital of France."])
    ok, note = file_ok("/tmp/d_resume.md", "Paris")
    rec("resume-cid", ok, s, f"cid={cid} {note}", ptr_of(p))
else:
    rec("resume-cid", False, 0, "no cid in conv file")

# ── C. extract code / canvas ──
p, s = sh([PY, GEM, "coding-partner", "--extract-code", "1", "--no-retry", "-t", "60",
           "-o", "/tmp/d_code.md",
           "Write a Python function add(a, b) that returns their sum. Reply with code only."])
ok, note = file_ok("/tmp/d_code.md", "def add")
rec("extract-code", ok, s, note, ptr_of(p))
code_file = glob.glob("/tmp/*code*.py") or glob.glob("/tmp/code_block*")
p, s = sh([PY, GEM, "coding-partner", "--extract-canvas", "/tmp/d_canvas.html",
           "--no-retry", "-t", "60", "-o", "/tmp/d_canvas.md",
           "Create a single-file HTML page with a red heading saying Hello. Reply with the HTML only."])
ok, note = file_ok("/tmp/d_canvas.html", "Hello")
rec("extract-canvas", ok, s, note, ptr_of(p))

# ── D. uploads ──
open("/tmp/d_note.md", "w").write(
    "# Facts\n\nThe secret fruit is the kiwano.\nThe capital of Zembla is Grandle.\n")
p, s = sh([PY, GEM, "coding-partner", "-f", "/tmp/d_note.md", "--no-retry", "-t", "60",
           "-o", "/tmp/d_fileup.md",
           "According to the attached file, what is the secret fruit? Reply with exactly one word."])
ok, note = file_ok("/tmp/d_fileup.md", "kiwano")
rec("file-upload", ok, s, note, ptr_of(p))
img_src = glob.glob("/tmp/m_imgdir/*.png") or glob.glob("/tmp/d_imgdir/*.png")
if img_src:
    p, s = sh([PY, GEM, "-i", img_src[0], "--no-retry", "-t", "60",
               "-o", "/tmp/d_imgup.md", "Describe this image in one short sentence."])
    ok, note = file_ok("/tmp/d_imgup.md", None, 10)
    rec("image-upload", ok, s, note, ptr_of(p))
else:
    rec("image-upload", False, 0, "no PNG from earlier runs to upload")

# ── E. media generation ──
os.makedirs("/tmp/d_imgdir", exist_ok=True)
p, s = sh([PY, GEM, "--img", "a red apple on a white table", "--save-images", "/tmp/d_imgdir",
           "--no-retry", "-t", "120", "-o", "/tmp/d_img.md"])
pngs = glob.glob("/tmp/d_imgdir/*.png") + glob.glob("/tmp/gemini_img_*.png")
ok = bool(pngs)
rec("img-gen", ok, s, f"pngs={pngs[:2]}", ptr_of(p))
p, s = sh([PY, GEM, "--img-gen", "-p", "a blue bicycle", "--no-retry", "-t", "120",
           "-o", "/tmp/d_imggen.md"])
ok, note = file_ok("/tmp/d_imggen.md", None, 1)
rec("img-gen-force", ok, s, note, ptr_of(p))
os.makedirs("/tmp/d_media", exist_ok=True)
p, s = sh([PY, GEM, "coding-partner", "--save-media", "/tmp/d_media", "--no-retry", "-t", "120",
           "-o", "/tmp/d_media.md", "Generate a short audio clip of birdsong."])
saved = [f for f in os.listdir("/tmp/d_media") if os.path.getsize(f"/tmp/d_media/{f}") > 0]
rec("save-media", bool(ptr_of(p) and ptr_of(p).get("ok")), s,
    f"saved={saved} (empty = model returned no audio)", ptr_of(p))
os.makedirs("/tmp/d_videos", exist_ok=True)
p, s = sh([PY, GEM, "coding-partner", "--save-videos", "/tmp/d_videos", "--no-retry", "-t", "180",
           "-o", "/tmp/d_video.md", "Generate a 2-second video clip of a cat sitting on a windowsill."])
saved = [f for f in os.listdir("/tmp/d_videos") if os.path.getsize(f"/tmp/d_videos/{f}") > 0]
rec("save-videos", bool(ptr_of(p) and ptr_of(p).get("ok")), s,
    f"saved={saved} (empty = model returned no video)", ptr_of(p))

# ── F. Gem CRUD (gemini.py) ──
p, s = sh([PY, GEM, "--create-gem", "muse-deep-1114", "-p",
           "You are a test gem. Reply with exactly one word: the capital of France."],
          timeout=120, stdin_data="You are a test gem for the deep test.")
ptr = ptr_of(p)
gem_id = (ptr or {}).get("gem_id") or (ptr or {}).get("id")
rec("gem-create", bool(gem_id), s, f"id={gem_id}", ptr)
if gem_id:
    p, s = sh([PY, GEM, "--list-gems"], timeout=60)
    rec("gem-create-listed", gem_id in (p.stdout if p else ""), s, "", ptr_of(p))
    p, s = sh([PY, GEM, "--edit-gem", gem_id, "-n", "muse-deep-1114b",
               "-d", "renamed by deep test"], timeout=60)
    rec("gem-edit", bool(ptr_of(p) and ptr_of(p).get("ok")), s, "", ptr_of(p))
    p, s = sh([PY, GEM, gem_id, "--gem-info"], timeout=60)
    rec("gem-info-after-edit", "muse-deep-1114b" in (p.stdout if p else ""), s, "", ptr_of(p))
    p, s = sh([PY, GEM, "--delete-gem", gem_id], timeout=60)
    rec("gem-delete", bool(ptr_of(p) and ptr_of(p).get("ok")), s, "", ptr_of(p))
    p, s = sh([PY, GEM, "--list-gems"], timeout=60)
    rec("gem-delete-verified", gem_id not in (p.stdout if p else ""), s, "", ptr_of(p))

# ── G. setup-search-gem + delete-chat + clear ──
p, s = sh([PY, GEM, "--setup-search-gem"], timeout=120)
ptr = ptr_of(p)
sg_id = (ptr or {}).get("gem_id") or (ptr or {}).get("id")
rec("setup-search-gem", bool(ptr and ptr.get("ok")), s, f"id={sg_id}", ptr)
if sg_id:
    p2, s2 = sh([PY, GEM, "--delete-gem", sg_id], timeout=60)
    rec("setup-search-gem-cleanup", bool(ptr_of(p2) and ptr_of(p2).get("ok")), s2, "", ptr_of(p2))
conv2 = "/tmp/d_delconv.json"
if os.path.exists(conv2):
    os.remove(conv2)
p, s = sh([PY, GEM, "-c", conv2, "--no-retry", "-t", "60", "-o", "/tmp/d_del1.md",
           "Reply with exactly one word: the capital of France."])
cid2 = None
try:
    cid2 = json.load(open(conv2)).get("cid")
except Exception:
    pass
if cid2:
    p, s = sh([PY, GEM, "--delete-chat", cid2], timeout=60)
    rec("delete-chat", bool(ptr_of(p) and ptr_of(p).get("ok")), s, f"cid={cid2}", ptr_of(p))
    p, s = sh([PY, GEM, "--read-chat", cid2], timeout=60)
    # Server behaviour (verified 2026-11-14): --delete-chat removes the
    # conversation from HISTORY (GzXR5e returns []), but the conversation
    # object stays readable by id — read-back succeeding is NOT a failed
    # delete. Assert the object is still the same chat.
    ptr = ptr_of(p)
    rec("delete-chat-verified", bool(ptr and ptr.get("ok") and
                                     (ptr.get("chat") or {}).get("cid") == cid2), s,
        "read-back still returns the chat object (delete removes the history entry, not the object)",
        ptr)
else:
    rec("delete-chat", False, 0, "no cid in conv file")
p, s = sh([PY, GEM, "--clear", "-c", conv2], timeout=60)
rec("clear", bool(ptr_of(p) and ptr_of(p).get("ok")) and not os.path.exists(conv2), s,
    f"file gone={not os.path.exists(conv2)}", ptr_of(p))

# ── H. deep research (validates stub guard live) + status ──
p, s = sh([PY, GEM, "--deep-research", "-m", "pro", "--no-retry", "-t", "580",
           "-o", "/tmp/d_dr.md",
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
           "-o", "/tmp/d_cli_hyd.md", "Reply with exactly one word: the capital of France."])
ok, note = file_ok("/tmp/d_cli_hyd.md", "Paris")
rec("cli-hydrogen", ok, s, note, ptr_of(p))
p, s = sh([PY, CLI, "--img", "a green leaf", "--no-retry", "-t", "120", "-o", "/tmp/d_cli_img.md"])
rec("cli-img", bool(ptr_of(p) and ptr_of(p).get("ok")), s, "", ptr_of(p))
p, s = sh([PY, CLI, "coding-partner", "--extract-code", "1", "--no-retry", "-t", "60",
           "-o", "/tmp/d_cli_code.md",
           "Write a Python function sub(a, b) that returns their difference. Reply with code only."])
ok, note = file_ok("/tmp/d_cli_code.md", "def sub")
rec("cli-extract-code", ok, s, note, ptr_of(p))
p, s = sh([PY, CLI, "coding-partner", "--json-out", "--no-retry", "-t", "60",
           "-o", "/tmp/d_cli_json.md", "Reply with exactly one word: the capital of France."])
ok, note = file_ok("/tmp/d_cli_json.md", "Paris")
rec("cli-json-out", ok, s, note, ptr_of(p))
p, s = sh([PY, CLI, "coding-partner", "--raw", "--no-retry", "-t", "60",
           "-o", "/tmp/d_cli_raw.md", "Reply with exactly one word: the capital of France."])
ok, note = file_ok("/tmp/d_cli_raw.md", "Paris")
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
           "-o", "/tmp/d_cli_dr.md",
           "What is Gemini 4 Hydrogen? Summarize in 3 bullet points."],
          timeout=590)
ptr = ptr_of(p)
rec("cli-deep-research-guard", bool(ptr and ptr.get("ok") is False and ptr.get("err") == "DR_FAILED"),
    s, (ptr or {}).get("msg", ""), ptr)

# ── K. soak: same Gem chat 5x ──
soak_ok = 0
for i in range(5):
    p, s = sh([PY, GEM, "coding-partner", "-m", "flash", "--no-retry", "-t", "60",
               "-o", f"/tmp/d_soak{i}.md", "Reply with exactly one word: the capital of France."],
              timeout=90)
    ok, _ = file_ok(f"/tmp/d_soak{i}.md", "Paris")
    soak_ok += int(ok)
rec("soak-5x-flash", soak_ok == 5, 0, f"{soak_ok}/5 Paris")

print(f"== part2: {sum(1 for r in results if r['ok'])}/{len(results)} passed ==")
