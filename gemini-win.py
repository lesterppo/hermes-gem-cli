#!/usr/bin/env python3
"""
gemini — AI-agent-native CLI for Gemini Web + Gems (Windows native version).

Single file — no WSL2 patches needed (curl_cffi works on Windows).
5-tier auth: env vars → cached file → browser cookie scan → retry → login.

Output: {"ok":true,"f":"./out.md","s":1234}
Repo: lesterppo/hermes-gem-cli
"""
import asyncio, argparse, json, os, re, sys, time, webbrowser
from datetime import datetime, timezone
from pathlib import Path

GEMINI_DIR = Path.home() / ".gemini-cli"
AUTH_CACHE = GEMINI_DIR / "auth.json"

# ── Dependencies ─────────────────────────────────────────────

try:
    from gemini_webapi import GeminiClient
    from gemini_webapi.client import Model as GeminiModel
except ImportError:
    print(json.dumps({"ok": False, "err": "DEP_MISSING",
                       "msg": "gemini-webapi not installed. Run: pip install gemini-webapi browser-cookie3 loguru"}))
    sys.exit(1)

import loguru as _loguru
_loguru.logger.remove()
_loguru.logger.add(sys.stderr, level="ERROR", format="<red>[gemini]</red> {message}")

# ── Auth ─────────────────────────────────────────────────────

_GEM_URL_RE = re.compile(r'gemini\.google\.com/gem/([a-zA-Z0-9_-]+)')

_AUTH_ERRORS = ["UNAUTHENTICATED", "cookies have expired", "session is not authenticated",
                 "error code: 1100", "User is not authenticated"]
_RATE_ERRORS = ["error code: 1097", "rate limit", "too many requests",
                 "quota exceeded", "resource has been exhausted"]

def extract_gem_id(url: str) -> str:
    m = _GEM_URL_RE.search(url)
    if m: return m.group(1)
    if '/' not in url and ' ' not in url and len(url) >= 5: return url
    raise ValueError(f"Cannot extract Gem ID from: {url}")

def is_auth_error(msg: str) -> bool:
    u = msg.upper(); return any(p.upper() in u for p in _AUTH_ERRORS)

def is_rate_limit(msg: str) -> bool:
    u = msg.upper(); return any(p.upper() in u for p in _RATE_ERRORS)

def error_kind(msg: str) -> str:
    if is_auth_error(msg): return "AUTH_EXPIRED"
    if is_rate_limit(msg): return "RATE_LIMIT"
    return "GEN_FAILED"

# ── Model labels ─────────────────────────────────────────────

_MODEL_LABELS = {
    "BASIC_FLASH": "flash+standard", "PLUS_FLASH": "flash+plus",
    "ADVANCED_FLASH": "flash+extended", "BASIC_PRO": "pro+standard",
    "PLUS_PRO": "pro+plus", "ADVANCED_PRO": "pro+extended",
    "BASIC_THINKING": "thinking+standard", "PLUS_THINKING": "thinking+plus",
    "ADVANCED_THINKING": "thinking+extended",
    "gemini-3-flash": "flash", "gemini-3-pro": "pro",
    "gemini-3-flash-lite": "lite", "3.5 Flash-Lite": "lite",
}

_LITE_MODEL = {
    "model_name": "gemini-3.5-flash-lite",
    "model_header": {
        "x-goog-ext-525001261-jspb": '[1,null,null,null,"8c46e95b1a07cecc",null,null,0,[4],null,null,1]',
        "x-goog-ext-73010989-jspb": "[0]",
        "x-goog-ext-73010990-jspb": "[0]",
    },
}

_ALIASES = {"pro": "PRO", "flash": "FLASH", "fast": "FLASH",
             "thinking": "THINKING", "think": "THINKING", "lite": "LITE"}
_THINKING = {"standard": "BASIC", "basic": "BASIC", "plus": "PLUS",
              "extended": "ADVANCED", "advanced": "ADVANCED"}

def friendly_model_label(model) -> str:
    if isinstance(model, dict):
        return _MODEL_LABELS.get(model.get("model_name", ""), model.get("model_name", "lite"))
    if hasattr(model, 'name'):
        return _MODEL_LABELS.get(model.name, model.name.lower())
    if isinstance(model, str):
        return _MODEL_LABELS.get(model, model.lower())
    return str(model)

def resolve_model_enum(model_str: str | None, thinking: str | None = None):
    if not model_str: return None
    tier = _THINKING.get(thinking.lower().strip(), thinking.upper()) if thinking else None
    mtype = _ALIASES.get(model_str.lower().strip())
    if mtype is None: return model_str
    if mtype == "LITE": return dict(_LITE_MODEL)
    if tier:
        try: return GeminiModel[f"{tier}_{mtype}"]
        except KeyError: return model_str
    return model_str

def resolve_model_string(client, model_str: str) -> str:
    q = model_str.lower().strip()
    if q in ("thinking", "think"):
        try: return GeminiModel.BASIC_THINKING
        except AttributeError: pass
    try:
        available = client.list_models()
        known = {"8c46e95b1a07cecc": "gemini-3-flash-lite",
                 "56fdd199312815e2": "gemini-3-flash",
                 "e6fa609c3fa255c0": "gemini-3-pro"}
        name_map = {known.get(m.model_id, str(m).lower()):
                     known.get(m.model_id, str(m)) for m in (available or [])}
    except Exception:
        return model_str
    if q in name_map: return name_map[q]
    matches = [v for k, v in name_map.items() if q in k]
    if len(matches) == 1: return matches[0]
    if q in ("flash", "fast"):
        return next((v for k, v in name_map.items()
                     if "flash" in k and "lite" not in k and "thinking" not in k), model_str)
    if q in ("pro",):
        return next((v for k, v in name_map.items()
                     if "pro" in k and "thinking" not in k), model_str)
    if q in ("lite",): return dict(_LITE_MODEL)
    return model_str

# ── 5-tier auth chain ────────────────────────────────────────

def _load_auth_cache() -> tuple:
    try:
        if AUTH_CACHE.exists():
            d = json.loads(AUTH_CACHE.read_text())
            sid = d.get("__Secure-1PSID") or d.get("sid")
            ts = d.get("__Secure-1PSIDTS") or d.get("ts")
            if sid: return sid, ts
    except Exception: pass
    return None, None

def _save_auth_cache(sid: str, ts: str | None):
    AUTH_CACHE.parent.mkdir(parents=True, exist_ok=True)
    AUTH_CACHE.write_text(json.dumps({
        "__Secure-1PSID": sid, "__Secure-1PSIDTS": ts or "",
        "updated": datetime.now(timezone.utc).isoformat(),
    }))

def _scan_browser_cookies(preferred: str | None = None) -> tuple:
    try: import browser_cookie3
    except ImportError: return None, None
    order = [('chrome', browser_cookie3.chrome), ('firefox', browser_cookie3.firefox),
             ('edge', browser_cookie3.edge)]
    if preferred:
        for i, (n, _) in enumerate(order):
            if n == preferred.lower(): order.insert(0, order.pop(i)); break
    for _, fn in order:
        try:
            cj = fn(domain_name='.google.com')
            sid = ts = None
            for c in cj:
                if c.name == '__Secure-1PSID': sid = c.value
                elif c.name == '__Secure-1PSIDTS': ts = c.value
            if sid: return sid, ts
        except Exception: continue
    return None, None

def _browser_login(preferred: str | None = None) -> tuple:
    if not sys.stdout.isatty(): return None, None
    print("[gemini] Opening gemini.google.com for login...", file=sys.stderr)
    webbrowser.open("https://gemini.google.com")
    for i in range(40):
        time.sleep(3)
        sid, ts = _scan_browser_cookies(preferred=preferred)
        if sid: _save_auth_cache(sid, ts); return sid, ts
    return None, None

def resolve_auth(preferred_browser: str | None = None, allow_login: bool = False) -> tuple:
    sid = os.getenv("GEMINI_SID"); ts = os.getenv("GEMINI_TS")
    if sid: return sid, ts
    sid, ts = _load_auth_cache()
    if sid: return sid, ts
    sid, ts = _scan_browser_cookies(preferred=preferred_browser)
    if sid: _save_auth_cache(sid, ts); return sid, ts
    if allow_login:
        sid, ts = _browser_login(preferred=preferred_browser)
        if sid: return sid, ts
    print(json.dumps({"ok": False, "err": "AUTH_EXPIRED",
                       "msg": "No Gemini cookies. Run: gemini --init"}))
    sys.exit(1)

def refresh_auth(preferred: str | None = None) -> tuple:
    sid, ts = _scan_browser_cookies(preferred=preferred)
    if sid: _save_auth_cache(sid, ts)
    return sid, ts

# ── Conversation state ───────────────────────────────────────

class ChatRef:
    def __init__(self, metadata: list): self.metadata = metadata

def load_conv(path: str) -> dict | None:
    p = Path(path)
    if not p.exists(): return None
    try:
        s = json.loads(p.read_text(encoding="utf-8"))
        if s.get("metadata") and len(s["metadata"]) >= 1: return s
    except Exception: pass
    return None

def save_conv(path: str, state: dict):
    state["updated"] = datetime.now(timezone.utc).isoformat()
    Path(path).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def fail(code: str, msg: str, extra: dict | None = None):
    out = {"ok": False, "err": code, "msg": msg}
    if extra: out.update(extra)
    print(json.dumps(out)); sys.exit(1)

# ── Main CLI ─────────────────────────────────────────────────

class GeminiCLI:
    def __init__(self):
        self.client = None
        self.raw_mode = False

    def log(self, msg: str):
        if not self.raw_mode: print(f"[gemini] {msg}", file=sys.stderr)

    def pointer(self, out_path: Path, conv_state: dict | None = None,
                images: list | None = None, code_blocks: int = 0,
                model_label: str = "", gem_name: str = "", deep_research: bool = False):
        p = {"ok": True, "f": str(out_path), "s": out_path.stat().st_size}
        if code_blocks: p["b"] = code_blocks
        if images: p["imgs"] = len(images)
        if model_label: p["model"] = model_label
        if gem_name: p["gem"] = gem_name
        if deep_research: p["dr"] = True
        if conv_state:
            p["c"] = conv_state.get("cid")
            p["t"] = conv_state.get("turns")
        print(json.dumps(p))

    def parse_code_blocks(self, text: str) -> list:
        return [{"lang": m[0], "code": m[1].strip()}
                for m in re.findall(r"```(\w*)\n(.*?)```", text, re.DOTALL)]

    async def run(self):
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')

        p = argparse.ArgumentParser(
            description="gemini — AI-agent-native CLI for Gemini Web (Windows)",
            formatter_class=argparse.RawDescriptionHelpFormatter)

        p.add_argument("url", nargs="?", help="Gem URL or ID")
        p.add_argument("prompt", nargs="*", help="Prompt (reads stdin if empty)")
        p.add_argument("-i", "--image", action="append", dest="images", default=[], metavar="FILE")
        p.add_argument("-f", "--file", action="append", dest="files", default=[], metavar="FILE")
        p.add_argument("-c", "--conversation", metavar="FILE", help="Conversation state file")
        p.add_argument("--new", action="store_true", dest="new_conv", help="Start fresh")
        p.add_argument("-m", "--model", choices=["flash","pro","thinking","lite"], help="Model")
        p.add_argument("--thinking", choices=["standard","plus","extended"], help="Thinking tier")
        p.add_argument("--img-gen", action="store_true", dest="image_gen", help="Force image gen")
        p.add_argument("--img", dest="image_prompt", metavar="PROMPT", help="Generate image")
        p.add_argument("--deep-research", action="store_true", dest="deep_research", help="Deep research")
        p.add_argument("-o", "--output", metavar="FILE", help="Output file")
        p.add_argument("--json-out", action="store_true", help="Write .json not .md")
        p.add_argument("--brief", action="store_true", help="Prepend 'Be concise.'")
        p.add_argument("-q", "--quiet", action="store_true", help="Suppress stderr")
        p.add_argument("--raw", action="store_true", dest="raw_mode", help="Zero stderr")
        p.add_argument("--browser", choices=["chrome","firefox","edge"], help="Browser for cookies")
        p.add_argument("--init", action="store_true", help="Cache auth tokens from browser")
        p.add_argument("--login", action="store_true", help="Open browser for login")
        p.add_argument("-p", "--prompt-flag", dest="prompt_flag", help="Prompt (alt to positional/stdin)")
        p.add_argument("--create-gem", dest="create_gem_name", metavar="NAME", help="Create a Gem")
        p.add_argument("--edit-gem", dest="edit_gem_id", metavar="ID_OR_NAME", help="Edit a Gem")
        p.add_argument("-n", "--new-name", dest="edit_new_name", help="New name")
        p.add_argument("-d", "--desc", dest="edit_new_desc", help="New description")
        p.add_argument("-S", "--system-instruction", dest="edit_sys_instr", help="System instruction")
        p.add_argument("--delete-gem", dest="delete_gem_id", metavar="ID", help="Delete a Gem")
        p.add_argument("--gem-info", action="store_true", help="Fetch Gem metadata")
        p.add_argument("--clear", action="store_true", dest="clear_conv", help="Delete conv file")
        p.add_argument("--list-models", action="store_true", help="List models")
        p.add_argument("--list-gems", action="store_true", help="List Gems")
        p.add_argument("--list-chats", action="store_true", help="List chat history")
        p.add_argument("--read-chat", dest="read_chat_id", metavar="CID", help="Read a chat")
        p.add_argument("--delete-chat", dest="delete_chat_id", metavar="CID", help="Delete a chat")
        p.add_argument("-l", "--limit", type=int, default=50, help="Limit for lists")
        p.add_argument("--account-status", action="store_true", help="Check account")
        p.add_argument("--setup-search-gem", action="store_true", help="Create search Gem")
        p.add_argument("--save-images", metavar="DIR", help="Save images to DIR")
        p.add_argument("-t", "--timeout", type=int, default=120, help="Timeout seconds")
        p.add_argument("--no-retry", action="store_true", help="Disable auto-retry")
        p.add_argument("--extract-code", type=int, dest="extract_code", metavar="N", help="Save Nth code block")
        p.add_argument("--resume", dest="resume_session", metavar="ID", help="Resume by session ID")
        p.add_argument("-g", "--gem", dest="gem_id", help="Gem ID for direct chat")

        args = p.parse_intermixed_args()
        self.raw_mode = args.raw_mode or args.quiet

        if self.raw_mode:
            _loguru.logger.remove()
            _loguru.logger.add(sys.stderr, level="CRITICAL")

        # ── --init ──
        if args.init:
            sid = os.getenv("GEMINI_SID")
            ts = os.getenv("GEMINI_TS")
            if not sid:
                sid, ts = _scan_browser_cookies(preferred=args.browser or os.getenv("GEMINI_BROWSER"))
            if sid:
                _save_auth_cache(sid, ts)
                print(json.dumps({"ok": True, "action": "init", "cached": str(AUTH_CACHE)}))
            else:
                fail("AUTH_EXPIRED", "No cookies found. Sign in at gemini.google.com first.")
            return

        # ── --login ──
        if args.login:
            sid, ts = _browser_login(preferred=args.browser or os.getenv("GEMINI_BROWSER"))
            if sid: print(json.dumps({"ok": True, "action": "login", "cached": str(AUTH_CACHE)}))
            else: fail("LOGIN_FAILED", "Login timed out.")
            return

        # ── --clear ──
        if args.clear_conv:
            if not args.conversation: fail("NO_CONV", "Use --clear with -c <file>.")
            Path(args.conversation).unlink(missing_ok=True)
            print(json.dumps({"ok": True, "action": "clear", "file": args.conversation}))
            return

        # ── Resolve Gem ID ──
        standalone = (args.list_models or args.list_gems or args.account_status or
                      args.list_chats or args.read_chat_id or args.delete_chat_id or
                      args.create_gem_name or args.edit_gem_id or args.delete_gem_id or
                      args.setup_search_gem)
        if standalone and not args.url:
            args.url = "setup"

        if args.gem_id:
            gem_id = args.gem_id
        elif args.list_models or args.list_gems:
            gem_id = "dummy"
        elif args.url:
            try: gem_id = extract_gem_id(args.url)
            except ValueError as e: fail("BAD_URL", str(e))
        else:
            p.print_help()
            fail("NO_URL", "Gem URL/ID or -g <id> required.")

        # ── Build prompt ──
        no_prompt = (args.list_models or args.list_gems or args.gem_info or
                     args.account_status or args.list_chats or args.read_chat_id or
                     args.delete_chat_id or args.create_gem_name or args.edit_gem_id or
                     args.delete_gem_id or args.setup_search_gem)

        if args.image_prompt:
            prompt = f"Generate an image: {args.image_prompt}"
            args.image_gen = True
        elif args.prompt_flag:
            prompt = args.prompt_flag
        elif args.prompt:
            prompt = " ".join(args.prompt)
        elif no_prompt:
            prompt = ""
        elif not sys.stdin.isatty():
            prompt = sys.stdin.read().strip()
            if not prompt: fail("NO_PROMPT", "No prompt provided.")
        elif args.image_gen:
            prompt = "Generate an image."
        else:
            fail("NO_PROMPT", "No prompt. Use positional, -p, or stdin.")

        if args.brief and prompt and not prompt.lower().startswith("be concise"):
            prompt = "Be concise. " + prompt

        # ── Auth ──
        sid, ts = resolve_auth(
            preferred_browser=args.browser or os.getenv("GEMINI_BROWSER"),
            allow_login=args.login)

        # ── Init client ──
        try:
            self.client = GeminiClient(secure_1psid=sid, secure_1psidts=ts)
            await self.client.init()
        except Exception as e:
            fail("INIT_FAILED", str(e))

        # ── Discovery ──
        if args.list_models:
            try:
                models = self.client.list_models()
                print(json.dumps({"ok": True, "models": [str(m) for m in (models or [])]}))
            except Exception as e: fail("LIST_FAILED", str(e))
            return

        if args.list_gems:
            try:
                await self.client.fetch_gems()
                gems = [{"id": gid, "name": g.name, "description": g.description or "",
                         "type": "system" if g.predefined else "user"}
                        for gid, g in self.client.gems.items()]
                print(json.dumps({"ok": True, "gems": gems}))
            except Exception as e: fail("LIST_FAILED", str(e))
            return

        if args.account_status:
            try:
                await self.client.fetch_gems()
                glist = [{"id": gid, "name": g.name} for gid, g in self.client.gems.items()]
                print(json.dumps({"ok": True, "authenticated": True, "gems": len(glist), "gem_list": glist}))
            except Exception as e:
                print(json.dumps({"ok": True, "authenticated": False, "error": str(e)}))
            return

        if args.list_chats:
            try:
                chats = self.client.list_chats()
                if chats is None:
                    fail("LIST_CHATS_FAILED", "No recent chats.")
                clist = [{"cid": c.cid, "title": c.title} for c in chats[:args.limit]]
                print(json.dumps({"ok": True, "chats": clist, "total": len(clist)}))
            except Exception as e: fail("LIST_CHATS_FAILED", str(e))
            return

        if args.read_chat_id:
            try:
                info = self.client.get_chat_info(args.read_chat_id)
                if info:
                    print(json.dumps({"ok": True, "chat": {"cid": info.cid, "title": info.title}}))
                else:
                    fail("CHAT_NOT_FOUND", f"Chat {args.read_chat_id} not found.")
            except Exception as e: fail("READ_CHAT_FAILED", str(e))
            return

        if args.delete_chat_id:
            try:
                self.client.delete_chat(args.delete_chat_id)
                print(json.dumps({"ok": True, "action": "delete-chat", "cid": args.delete_chat_id}))
            except Exception as e: fail("DELETE_CHAT_FAILED", str(e))
            return

        if args.gem_info:
            try:
                await self.client.fetch_gems()
                g = self.client.gems.get(gem_id)
                if g:
                    print(json.dumps({"ok": True, "gem": {"id": gem_id, "name": g.name,
                        "description": g.description or "", "type": "system" if g.predefined else "user"}}))
                else:
                    print(json.dumps({"ok": True, "gem": {"id": gem_id, "name": "",
                        "description": "", "type": "external", "note": "Shared Gem — not in library"}}))
            except Exception as e: fail("GEM_INFO_FAILED", str(e))
            return

        if args.create_gem_name:
            sys_prompt = args.prompt_flag or args.edit_sys_instr or " ".join(args.prompt) or ""
            if not sys_prompt and not sys.stdin.isatty():
                sys_prompt = sys.stdin.read().strip()
            if not sys_prompt: fail("NO_PROMPT", "Provide system prompt via -p, -S, stdin, or positional args.")
            try:
                gem = await self.client.create_gem(name=args.create_gem_name, prompt=sys_prompt,
                                                    description=f"Hermes task Gem: {args.create_gem_name}")
                print(json.dumps({"ok": True, "action": "create-gem", "id": gem.id, "name": gem.name}))
            except Exception as e: fail("GEM_CREATE_FAILED", str(e))
            return

        if args.edit_gem_id:
            try:
                await self.client.fetch_gems()
                g = self.client.gems.get(args.edit_gem_id)
                if not g:
                    for gid, gg in self.client.gems.items():
                        if gg.name.lower() == args.edit_gem_id.lower(): g = gg; break
                if not g: fail("GEM_NOT_FOUND", f"Gem '{args.edit_gem_id}' not found.")
                name = args.edit_new_name or g.name
                desc = args.edit_new_desc if args.edit_new_desc is not None else (g.description or "")
                instr = args.edit_sys_instr if args.edit_sys_instr else None
                await self.client.edit_gem(g=g, name=name, description=desc, prompt=instr)
                print(json.dumps({"ok": True, "action": "edit-gem", "id": g.id, "name": name}))
            except Exception as e: fail("GEM_EDIT_FAILED", str(e))
            return

        if args.delete_gem_id:
            try:
                await self.client.delete_gem(args.delete_gem_id)
                print(json.dumps({"ok": True, "action": "delete-gem", "id": args.delete_gem_id}))
            except Exception as e: fail("GEM_DELETE_FAILED", str(e))
            return

        if args.setup_search_gem:
            try:
                sp = Path(__file__).resolve().parent / "search-gem-prompt.txt"
                sys_prompt = sp.read_text().strip() if sp.exists() else "Search grounding assistant."
                gem = await self.client.create_gem(name="Gemini search", prompt=sys_prompt,
                                                    description="Search grounding proxy")
                print(json.dumps({"ok": True, "action": "setup-search-gem", "id": gem.id, "name": gem.name}))
            except Exception as e: fail("SETUP_FAILED", str(e))
            return

        # ── Model resolution ──
        model = None
        if args.model or args.thinking:
            model = resolve_model_enum(args.model, args.thinking) if args.thinking \
                    else resolve_model_string(self.client, args.model)

        gem_name = ""
        try:
            await self.client.fetch_gems()
            g = self.client.gems.get(gem_id)
            if g: gem_name = g.name
        except Exception: pass

        if not self.raw_mode:
            ml = friendly_model_label(model)
            parts = [f"gem={gem_name or gem_id}", f"model={ml}"]
            if args.deep_research: parts.append("deep-research")
            if args.image_gen: parts.append("img-gen")
            self.log(", ".join(parts))

        # ── Conversation ──
        conv_state = None; chat_metadata = None
        if args.resume_session:
            conv_state = {"cid": args.resume_session, "metadata": [args.resume_session, ""],
                          "turns": 0, "created": datetime.now(timezone.utc).isoformat()}
            chat_metadata = conv_state["metadata"]
        elif args.conversation:
            if not args.new_conv:
                conv_state = load_conv(args.conversation)
                if conv_state: chat_metadata = conv_state.get("metadata")
            if conv_state is None:
                conv_state = {"cid": None, "metadata": None, "turns": 0,
                              "created": datetime.now(timezone.utc).isoformat()}

        if args.image_gen and not model:
            model = "gemini-3-flash"

        all_files = []
        for img in args.images:
            if not Path(img).exists(): fail("FILE_NOT_FOUND", f"Image not found: {img}")
            all_files.append(str(Path(img)))
        for f in args.files:
            if not Path(f).exists(): fail("FILE_NOT_FOUND", f"File not found: {f}")
            all_files.append(str(Path(f)))

        model_label = friendly_model_label(model)

        # ── Deep research timeout ──
        actual_timeout = args.timeout
        if args.deep_research and args.timeout == 120:
            actual_timeout = 600

        # ── Generate with retry ──
        max_attempts = 1 if args.no_retry else 3
        for attempt in range(max_attempts):
            if attempt > 0: self.log(f"Retry {attempt+1}/{max_attempts}...")
            try:
                if args.deep_research:
                    plan = await asyncio.wait_for(
                        self.client.create_deep_research_plan(prompt, model=model), timeout=120)
                    await asyncio.wait_for(
                        self.client.start_deep_research(
                            plan, confirm_prompt="Proceed with this plan."), timeout=120)
                    result = await asyncio.wait_for(
                        self.client.wait_for_deep_research(
                            plan, poll_interval=15.0, timeout=actual_timeout),
                        timeout=actual_timeout)
                    response = result.final_output
                else:
                    kwargs = {"prompt": prompt}
                    if all_files: kwargs["files"] = all_files
                    if chat_metadata: kwargs["chat"] = ChatRef(chat_metadata)
                    if model: kwargs["model"] = model
                    kwargs["gem"] = gem_id
                    response = await asyncio.wait_for(
                        self.client.generate_content(**kwargs), timeout=actual_timeout)
            except asyncio.TimeoutError:
                if attempt == max_attempts - 1:
                    fail("TIMEOUT", f"Timed out after {actual_timeout}s.",
                         {"timeout_s": actual_timeout, "retry": False})
                continue
            except Exception as e:
                err_msg = str(e); kind = error_kind(err_msg)
                if kind == "AUTH_EXPIRED":
                    if attempt == max_attempts - 1: fail("AUTH_EXPIRED", err_msg)
                    new_sid, new_ts = refresh_auth(args.browser or os.getenv("GEMINI_BROWSER"))
                    if new_sid:
                        sid, ts = new_sid, new_ts
                        self.client = GeminiClient(secure_1psid=sid, secure_1psidts=ts)
                        await self.client.init()
                        continue
                if kind == "RATE_LIMIT":
                    wait = 30 if attempt == 0 else 60
                    if attempt == max_attempts - 1:
                        fail("RATE_LIMIT", err_msg, {"retry_after_s": wait, "retry": True})
                    await asyncio.sleep(wait); continue
                if attempt == max_attempts - 1: fail(kind, err_msg)
                continue

            # Success
            text = response.text
            new_meta = list(response.metadata) if response.metadata else None

            images_out = []
            try:
                for img in response.images:
                    images_out.append({"url": img.url, "alt": img.alt or ""})
            except Exception: pass

            if args.conversation and new_meta:
                conv_state["cid"] = new_meta[0]
                conv_state["metadata"] = new_meta
                conv_state["turns"] += 1
                save_conv(args.conversation, conv_state)

            ext = ".json" if args.json_out else ".md"
            out_path = Path(args.output) if args.output else \
                       Path(f"C:/Users/Peter/gemini-cli/gemini-{datetime.now().strftime('%Y%m%d-%H%M%S')}{ext}")

            if args.json_out:
                payload = {"ok": True, "text": text, "model": model_label}
                if images_out: payload["images"] = images_out
                if conv_state: payload["conversation"] = conv_state
                out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            else:
                out_text = text
                if images_out:
                    out_text += "\n\n## Images\n\n"
                    for i, img in enumerate(images_out):
                        out_text += f"{i+1}. ![{img['alt']}]({img['url']})\n"
                out_path.write_text(out_text, encoding="utf-8")

            code_blocks = self.parse_code_blocks(text)

            if args.extract_code:
                n = args.extract_code
                if n < 1 or n > len(code_blocks):
                    fail("BAD_CODE_INDEX", f"Block {n} not found ({len(code_blocks)} blocks).")
                cb = code_blocks[n - 1]
                if args.output:
                    Path(args.output).write_text(cb["code"], encoding="utf-8")
                    print(json.dumps({"ok": True, "action": "extract-code", "n": n,
                                      "lang": cb["lang"], "f": args.output}))
                else:
                    print(cb["code"])
                return

            self.pointer(out_path, conv_state if args.conversation else None,
                         images_out, len(code_blocks), model_label, gem_name,
                         args.deep_research)
            return

def main():
    asyncio.run(GeminiCLI().run())

if __name__ == "__main__":
    main()
