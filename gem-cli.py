#!/usr/bin/env python3
"""
gem-cli — AI-agent-native, token-efficient CLI for shared Gemini Gems.

Always writes response to file; stdout gets a compact pointer JSON.
5-tier auth: env vars → cached file → browser cookie scan → retry → login.

Usage:
  gem-cli <gem-url> "prompt"
  gem-cli <gem-url> -c sess.json "prompt"              # multi-turn
  gem-cli <gem-url> -f report.pdf "summarize"           # file upload
  gem-cli <gem-url> -i chart.png "analyze"              # image upload
  gem-cli <gem-url> -m pro --thinking extended "..."    # model/thinking
  gem-cli <gem-url> --img "a cat flying"                # image generation
  gem-cli --init                                        # cache auth tokens
  gem-cli --list-models                                 # list available models
  gem-cli --list-gems                                   # list available Gems
  echo "prompt" | gem-cli <gem-url> -q                  # stdin, quiet

Output: {"ok":true,"f":"./out.md","s":1234,"b":2,"imgs":3,
         "model":"gemini-3-flash","gem":"GemName","c":"c_xxx","t":5}
"""

import re
import json
import sys
import os
import asyncio
import argparse
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

try:
    from gemini_webapi import GeminiClient
    from gemini_webapi.client import Model
except ImportError:
    print(json.dumps({"ok": False, "err": "DEP_MISSING",
                       "msg": "gemini-webapi not installed. Run: pip install gemini-webapi"}))
    sys.exit(1)

# Suppress gemini_webapi's loguru logger (controlled by --raw/--quiet)
import loguru as _loguru
_loguru.logger.remove()
_loguru.logger.add(sys.stderr, level="ERROR", format="<red>[gemini]</red> {message}")

# ── Paths ───────────────────────────────────────────────────

AUTH_CACHE = Path.home() / ".gemini-cli" / "auth.json"
GEM_HOME = Path.home() / ".gemini-cli"

# ── URL parsing ──────────────────────────────────────────────

_GEM_URL_RE = re.compile(r'gemini\.google\.com/gem/([a-zA-Z0-9_-]+)')

def extract_gem_id(url: str) -> str:
    m = _GEM_URL_RE.search(url)
    if m:
        return m.group(1)
    if '/' not in url and ' ' not in url and len(url) >= 5:
        return url
    raise ValueError(f"Cannot extract Gem ID from: {url}")

# ── 5-Tier Auth Chain ────────────────────────────────────────

_AUTH_ERROR_PATTERNS = [
    "UNAUTHENTICATED", "cookies have expired", "session is not authenticated",
    "error code: 1100", "User is not authenticated",
]

_RATE_LIMIT_PATTERNS = [
    "error code: 1097", "rate limit", "too many requests",
    "quota exceeded", "resource has been exhausted",
]

def is_auth_error(msg: str) -> bool:
    upper = msg.upper()
    return any(p.upper() in upper for p in _AUTH_ERROR_PATTERNS)

def is_rate_limit_error(msg: str) -> bool:
    upper = msg.upper()
    return any(p.upper() in upper for p in _RATE_LIMIT_PATTERNS)

def error_kind(msg: str) -> str:
    """Classify error for structured response."""
    if is_auth_error(msg):
        return "AUTH_EXPIRED"
    if is_rate_limit_error(msg):
        return "RATE_LIMIT"
    return "GEN_FAILED"

# ── Friendly model labels ────────────────────────────────────

_MODEL_LABEL_MAP = {
    # Model enum → friendly name
    "BASIC_FLASH": "flash+standard",
    "PLUS_FLASH": "flash+plus",
    "ADVANCED_FLASH": "flash+extended",
    "BASIC_PRO": "pro+standard",
    "PLUS_PRO": "pro+plus",
    "ADVANCED_PRO": "pro+extended",
    "BASIC_THINKING": "thinking+standard",
    "PLUS_THINKING": "thinking+plus",
    "ADVANCED_THINKING": "thinking+extended",
    # String model IDs → friendly name
    "gemini-3-flash": "flash",
    "gemini-3-pro": "pro",
    "gemini-3-flash-lite": "lite",
    "gemini-3-flash-thinking": "thinking",
    "3.1 Flash-Lite": "lite",
}

def friendly_model_label(model) -> str:
    """Normalize model to consistent friendly label: 'flash', 'pro+extended', etc."""
    if hasattr(model, 'name'):
        return _MODEL_LABEL_MAP.get(model.name, model.name.lower())
    if isinstance(model, str):
        return _MODEL_LABEL_MAP.get(model, model.lower())
    return str(model)

def _load_auth_cache() -> tuple[str | None, str | None]:
    """Tier 2: read cached tokens from ~/.gemini-cli/auth.json."""
    try:
        if AUTH_CACHE.exists():
            data = json.loads(AUTH_CACHE.read_text())
            sid = data.get("__Secure-1PSID") or data.get("sid")
            ts = data.get("__Secure-1PSIDTS") or data.get("ts")
            if sid:
                return sid, ts
    except Exception:
        pass
    return None, None

def _save_auth_cache(sid: str, ts: str | None):
    """Write tokens to cache file for fast subsequent reads."""
    AUTH_CACHE.parent.mkdir(parents=True, exist_ok=True)
    AUTH_CACHE.write_text(json.dumps({
        "__Secure-1PSID": sid,
        "__Secure-1PSIDTS": ts or "",
        "updated": datetime.now(timezone.utc).isoformat(),
    }))

def _scan_browser_cookies(preferred: str | None = None) -> tuple[str | None, str | None]:
    """Tier 3: extract cookies from installed browsers via browser_cookie3."""
    try:
        import browser_cookie3
    except ImportError:
        return None, None

    browser_order = [
        ('chrome', browser_cookie3.chrome),
        ('firefox', browser_cookie3.firefox),
        ('edge', browser_cookie3.edge),
        ('safari', browser_cookie3.safari),
    ]
    if preferred:
        pl = preferred.lower()
        for i, (name, _) in enumerate(browser_order):
            if name == pl:
                browser_order.insert(0, browser_order.pop(i))
                break

    for name, fetch_func in browser_order:
        try:
            cj = fetch_func(domain_name='.google.com')
            sid, ts = None, None
            for c in cj:
                if c.name == '__Secure-1PSID':
                    sid = c.value
                elif c.name == '__Secure-1PSIDTS':
                    ts = c.value
            if sid:
                return sid, ts
        except Exception:
            continue
    return None, None

def _browser_login(preferred: str | None = None) -> tuple[str | None, str | None]:
    """Tier 5: open browser for interactive login, poll for cookies."""
    if not sys.stdout.isatty():
        return None, None
    print("[gem-cli] Opening gemini.google.com for login...", file=sys.stderr)
    webbrowser.open("https://gemini.google.com")
    print("[gem-cli] Waiting for cookies (polling 3s, 120s timeout)...", file=sys.stderr)
    for i in range(40):
        time.sleep(3)
        sid, ts = _scan_browser_cookies(preferred=preferred)
        if sid:
            print(f"[gem-cli] Cookies acquired after ~{(i+1)*3}s", file=sys.stderr)
            _save_auth_cache(sid, ts)
            return sid, ts
        if (i + 1) % 10 == 0:
            print(f"[gem-cli] Still waiting... ({(i+1)*3}s)", file=sys.stderr)
    return None, None

def resolve_auth(preferred_browser: str | None = None,
                 allow_login: bool = False) -> tuple[str, str | None]:
    """
    5-tier auth resolution. Returns (sid, ts). Raises SystemExit on failure.

    Tier 1: GEMINI_SID + GEMINI_TS env vars (CI/remote)
    Tier 2: ~/.gemini-cli/auth.json cache (fast, no browser_cookie3)
    Tier 3: browser_cookie3 scan (fresh, ~1s)
    Tier 4: retry with re-scan (resilience)
    Tier 5: --login browser flow (last resort, requires --login flag)
    """
    # Tier 1: env vars
    sid = os.getenv("GEMINI_SID")
    ts = os.getenv("GEMINI_TS")
    if sid:
        return sid, ts

    # Tier 2: cache file
    sid, ts = _load_auth_cache()
    if sid:
        return sid, ts

    # Tier 3: browser scan
    sid, ts = _scan_browser_cookies(preferred=preferred_browser)
    if sid:
        _save_auth_cache(sid, ts)
        return sid, ts

    # Tier 5: login (only if explicitly requested)
    if allow_login:
        sid, ts = _browser_login(preferred=preferred_browser)
        if sid:
            return sid, ts

    # All tiers exhausted
    print(json.dumps({
        "ok": False, "err": "AUTH_EXPIRED",
        "msg": "No Gemini cookies found. Options:\n"
               "  1. Set GEMINI_SID + GEMINI_TS env vars\n"
               "  2. Run: gem-cli --init\n"
               "  3. Run: gem-cli --login"
    }))
    sys.exit(1)

def refresh_auth_on_error(preferred_browser: str | None = None) -> tuple[str | None, str | None]:
    """Tier 4: on auth error, re-scan cookies (may have been refreshed in another window)."""
    sid, ts = _scan_browser_cookies(preferred=preferred_browser)
    if sid:
        _save_auth_cache(sid, ts)
    return sid, ts

# ── Model resolution ────────────────────────────────────────

_MODEL_ALIASES = {
    "pro": "PRO", "flash": "FLASH", "fast": "FLASH",
    "thinking": "THINKING", "think": "THINKING",
    "lite": "LITE",
}

_THINKING_ALIASES = {
    "standard": "BASIC", "basic": "BASIC",
    "plus": "PLUS",
    "extended": "ADVANCED", "advanced": "ADVANCED",
}

def resolve_model_enum(model_str: str | None, thinking: str | None = None):
    """Resolve shorthand to Model enum (when thinking tier specified)."""
    if not model_str:
        return None
    tier = _THINKING_ALIASES.get(thinking.lower().strip(), thinking.upper()) if thinking else None
    mtype = _MODEL_ALIASES.get(model_str.lower().strip())
    if mtype is None:
        return model_str
    if mtype == "LITE":
        return "gemini-3-flash-lite"
    if tier:
        try:
            return Model[f"{tier}_{mtype}"]
        except KeyError:
            return model_str
    return model_str

def resolve_model_string(client, model_str: str) -> str:
    """Resolve shorthand string ('flash') against live model list.
    Uses model_id field from AvailableModel, not str() which returns display names."""
    q = model_str.lower().strip()
    
    # "thinking" shorthand needs a thinking tier — default to standard
    if q in ("thinking", "think"):
        try:
            return Model.BASIC_THINKING
        except AttributeError:
            pass
    
    try:
        available = client.list_models()
        # Use model_id (e.g., "56fdd199312815e2") as fallback key,
        # and the known string names as primary keys
        name_map = {}
        for m in available:
            # Known string names (what the API error messages show)
            known = {
                "8c46e95b1a07cecc": "gemini-3-flash-lite",
                "56fdd199312815e2": "gemini-3-flash",
                "e6fa609c3fa255c0": "gemini-3-pro",
            }
            key = known.get(m.model_id, str(m).lower())
            name_map[key] = known.get(m.model_id, str(m))
    except Exception:
        return model_str

    if q in name_map:
        return name_map[q]
    
    matches = [v for k, v in name_map.items() if q in k]
    if len(matches) == 1:
        return matches[0]
    
    # Heuristic selection: prefer non-thinking, non-lite variants
    if q in ("flash", "fast"):
        return next((v for k, v in name_map.items()
                     if "flash" in k and "lite" not in k and "thinking" not in k), model_str)
    if q in ("pro",):
        return next((v for k, v in name_map.items()
                     if "pro" in k and "thinking" not in k), model_str)
    if q in ("lite",):
        return next((v for k, v in name_map.items()
                     if "lite" in k), model_str)
    return model_str

# ── Conversation state ──────────────────────────────────────

class ChatRef:
    def __init__(self, metadata: list):
        self.metadata = metadata

def load_conv(path: str) -> dict | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        state = json.loads(p.read_text(encoding="utf-8"))
        if state.get("metadata") and len(state["metadata"]) >= 1:
            return state
    except (json.JSONDecodeError, KeyError):
        pass
    return None

def save_conv(path: str, state: dict):
    state["updated"] = datetime.now(timezone.utc).isoformat()
    Path(path).write_text(json.dumps(state, ensure_ascii=False, indent=2),
                          encoding="utf-8")

# ── Image detection ─────────────────────────────────────────

_IMAGE_GEN_STARTS = [
    "generate an image", "create an image", "make an image",
    "draw a", "generate a photo", "create a picture",
]

_IMAGE_GEN_KEYWORDS = _IMAGE_GEN_STARTS + [
    "show me a picture", "show me an image",
    "generate", "create", "draw", "illustrate", "paint",
]

def looks_like_image_gen(prompt: str) -> bool:
    p = prompt.lower().strip()
    for kw in _IMAGE_GEN_STARTS:
        if p.startswith(kw):
            return True
    return sum(1 for kw in _IMAGE_GEN_KEYWORDS if kw in p) >= 2

# ── Structured errors ───────────────────────────────────────

def fail(code: str, msg: str, extra: dict | None = None):
    """Emit structured error JSON and exit."""
    out = {"ok": False, "err": code, "msg": msg}
    if extra:
        out.update(extra)
    print(json.dumps(out))
    sys.exit(1)

# ── Main CLI ────────────────────────────────────────────────

class GemCLI:
    def __init__(self):
        self.client = None
        self.raw_mode = False  # --raw: zero stderr

    def log(self, msg: str):
        if not self.raw_mode:
            print(f"[gem-cli] {msg}", file=sys.stderr)

    def pointer(self, out_path: Path, conv_state: dict | None = None,
                images: list | None = None, code_blocks: int = 0,
                model_label: str = "", gem_name: str = "", deep_research: bool = False):
        """Emit compact pointer JSON to stdout."""
        p = {"ok": True, "f": self._short_path(out_path), "s": out_path.stat().st_size}
        if code_blocks:
            p["b"] = code_blocks
        if images:
            p["imgs"] = len(images)
        if model_label:
            p["model"] = model_label
        if gem_name:
            p["gem"] = gem_name
        if deep_research:
            p["dr"] = True
        if conv_state:
            p["c"] = conv_state.get("cid")
            p["t"] = conv_state.get("turns")
        print(json.dumps(p))

    @staticmethod
    def _short_path(p: Path) -> str:
        try:
            rel = p.resolve().relative_to(Path.cwd())
            return "./" + str(rel).replace("\\", "/")
        except ValueError:
            return str(p.resolve())

    def parse_code_blocks(self, text: str) -> list:
        return [{"lang": m[0], "code": m[1].strip()}
                for m in re.findall(r"```(\w*)\n(.*?)```", text, re.DOTALL)]

    async def run(self):
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')

        parser = argparse.ArgumentParser(
            description="gem-cli — AI-agent-native CLI for shared Gemini Gems",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="""Examples:
  gem-cli https://gemini.google/gem/AbCdEf1234 "Hello"
  gem-cli AbCdEf1234 -c sess.json --new "start"
  gem-cli AbCdEf1234 -c sess.json "follow-up"
  gem-cli AbCdEf1234 -f report.pdf -m pro --thinking extended "analyze"
  gem-cli AbCdEf1234 --img "a flying cat"
  gem-cli AbCdEf1234 -m pro -t 180 "deep analysis"
  gem-cli --init              # cache auth tokens from browser
  gem-cli --login             # interactive browser login
  gem-cli --list-models       # show available models
  gem-cli --list-gems         # show available Gems

Output: compact JSON pointer on stdout, full response on disk.""")
        # Core
        parser.add_argument("url", nargs="?", type=str,
                            help="Shared Gem URL or Gem ID")
        parser.add_argument("prompt", nargs="*", type=str,
                            help="Prompt text. Reads from stdin if empty.")
        # Files
        parser.add_argument("-i", "--image", type=str, action="append", dest="images",
                            default=[], metavar="FILE")
        parser.add_argument("-f", "--file", type=str, action="append", dest="files",
                            default=[], metavar="FILE")
        # Conversation
        parser.add_argument("-c", "--conversation", type=str, metavar="FILE",
                            help="Conversation state file for multi-turn")
        parser.add_argument("--new", action="store_true", dest="new_conv",
                            help="Start fresh conversation")
        # Model
        parser.add_argument("-m", "--model", type=str, metavar="MODEL",
                            choices=["flash", "pro", "thinking", "lite"],
                            help="Model: flash (fast), pro (deep), thinking, lite")
        parser.add_argument("--thinking", type=str, metavar="TIER",
                            choices=["standard", "plus", "extended"],
                            help="Thinking tier: standard, plus, extended")
        # Image generation
        parser.add_argument("--img-gen", action="store_true", dest="image_gen",
                            help="Force image generation mode")
        parser.add_argument("--img", type=str, dest="image_prompt", metavar="PROMPT",
                            help="Generate an image from prompt")
        # Deep research
        parser.add_argument("--deep-research", action="store_true", dest="deep_research",
                            help="Run in deep research mode (auto-plans, ~1-10 min, timeout 600s)")
        # Output
        parser.add_argument("-o", "--output", type=str, metavar="FILE",
                            help="Output file (default: /tmp/gem-cli-<ts>.md)")
        parser.add_argument("--json-out", action="store_true", dest="json_out",
                            help="Write .json output instead of .md")
        parser.add_argument("--brief", action="store_true",
                            help="Prepend 'Be concise.' to prompt")
        parser.add_argument("-q", "--quiet", action="store_true",
                            help="Suppress stderr logs")
        parser.add_argument("--raw", action="store_true", dest="raw_mode",
                            help="Zero stderr output (pure JSON stdout)")
        # Auth
        parser.add_argument("--browser", type=str,
                            choices=["chrome", "firefox", "edge", "safari"],
                            help="Preferred browser for cookies")
        parser.add_argument("--init", action="store_true",
                            help="Extract & cache auth tokens from browser, then exit")
        parser.add_argument("--login", action="store_true",
                            help="Open browser for interactive Gemini login")
        parser.add_argument("--create-gem", type=str, dest="create_gem_name", metavar="NAME",
                            help="Create a new Gem with system prompt from stdin or -p")
        parser.add_argument("--delete-gem", type=str, dest="delete_gem_id", metavar="ID",
                            help="Delete a Gem by ID")
        parser.add_argument("--gem-info", action="store_true", dest="gem_info",
                            help="Fetch Gem metadata (name, description) and exit")
        parser.add_argument("--clear", action="store_true", dest="clear_conv",
                            help="Delete conversation file and exit (use with -c)")
        # Discovery
        parser.add_argument("--list-models", action="store_true",
                            help="List available models and exit")
        parser.add_argument("--list-gems", action="store_true",
                            help="List available Gems and exit")
        # Timing
        parser.add_argument("-t", "--timeout", type=int, default=120, metavar="SEC",
                            help="Max seconds for generation (default: 120)")
        parser.add_argument("--no-retry", action="store_true",
                            help="Disable auto-retry on auth error")
        parser.add_argument("--extract-code", type=int, dest="extract_code", metavar="N",
                            help="Save Nth code block from response to file and exit "
                            "(1-indexed, use with -o to specify output file)")
        parser.add_argument("--resume", type=str, dest="resume_session", metavar="ID",
                            help="Resume conversation by session ID (cid) without needing -c file. "
                            "Use with -c to save state for future turns.")
        parser.add_argument("--timeout-soft", type=int, dest="timeout_soft", metavar="SEC",
                            help="Warn at N seconds but keep waiting (for slow Pro calls)")
        args = parser.parse_args()

        self.raw_mode = args.raw_mode or args.quiet

        # In raw mode, suppress ALL stderr including gemini_webapi internals
        if self.raw_mode:
            _loguru.logger.remove()
            _loguru.logger.add(sys.stderr, level="CRITICAL")  # effectively silent

        # ── Standalone discovery commands ──
        standalone = args.init or args.login or args.list_models or args.list_gems \
                     or args.create_gem_name or args.delete_gem_id
        if standalone and not args.url:
            args.url = "setup"  # dummy for auth resolution

        # ── Handle --init ──
        if args.init:
            if not args.quiet:
                print("[gem-cli] Extracting auth tokens from browser...", file=sys.stderr)
            sid = os.getenv("GEMINI_SID")
            ts = os.getenv("GEMINI_TS")
            if not sid:
                sid, ts = _scan_browser_cookies(preferred=args.browser or os.getenv("GEMINI_BROWSER"))
            if sid:
                _save_auth_cache(sid, ts)
                print(json.dumps({"ok": True, "action": "init",
                                  "cached": str(AUTH_CACHE)}))
            else:
                fail("AUTH_EXPIRED", "No cookies found. Sign in at gemini.google.com first, or use --login.")
            return

        # ── Handle --login ──
        if args.login:
            sid, ts = _browser_login(preferred=args.browser or os.getenv("GEMINI_BROWSER"))
            if sid:
                print(json.dumps({"ok": True, "action": "login", "cached": str(AUTH_CACHE)}))
            else:
                fail("LOGIN_FAILED", "Login timed out. Try again or set GEMINI_SID/GEMINI_TS.")
            return

        # ── Handle --create-gem ──
        if args.create_gem_name:
            # Read system prompt from stdin or -p
            if args.prompt:
                system_prompt = " ".join(args.prompt)
            elif not sys.stdin.isatty():
                system_prompt = sys.stdin.read().strip()
            else:
                fail("NO_PROMPT", "Provide system prompt via stdin or positional args.")
            sid, ts = resolve_auth(
                preferred_browser=args.browser or os.getenv("GEMINI_BROWSER"),
                allow_login=False)
            try:
                client = GeminiClient(secure_1psid=sid, secure_1psidts=ts)
                await client.init()
                gem = await client.create_gem(
                    name=args.create_gem_name,
                    prompt=system_prompt,
                    description=f"Hermes task-specific Gem: {args.create_gem_name}",
                )
                print(json.dumps({"ok": True, "action": "create-gem",
                                  "id": gem.id, "name": gem.name}))
            except Exception as e:
                fail("GEM_CREATE_FAILED", str(e))
            return

        # ── Handle --delete-gem ──
        if args.delete_gem_id:
            sid, ts = resolve_auth(
                preferred_browser=args.browser or os.getenv("GEMINI_BROWSER"),
                allow_login=False)
            try:
                client = GeminiClient(secure_1psid=sid, secure_1psidts=ts)
                await client.init()
                await client.delete_gem(args.delete_gem_id)
                print(json.dumps({"ok": True, "action": "delete-gem",
                                  "id": args.delete_gem_id}))
            except Exception as e:
                fail("GEM_DELETE_FAILED", str(e))
            return

        # ── Handle --clear ──
        if args.clear_conv:
            if not args.conversation:
                fail("NO_CONV", "Use --clear with -c <file> to specify which conversation to delete.")
            p = Path(args.conversation)
            if p.exists():
                p.unlink()
                print(json.dumps({"ok": True, "action": "clear", "file": str(p)}))
            else:
                print(json.dumps({"ok": True, "action": "clear", "file": str(p), "note": "already gone"}))
            return

        # ── Require URL for non-auth commands ──
        if not args.url:
            parser.print_help()
            fail("NO_URL", "Shared Gem URL or Gem ID is required.")

        # ── Handle --gem-info (needs URL but no prompt, needs auth) ──
        if args.gem_info:
            try:
                target_id = extract_gem_id(args.url)
            except ValueError as e:
                fail("BAD_URL", str(e))
            sid, ts = resolve_auth(
                preferred_browser=args.browser or os.getenv("GEMINI_BROWSER"),
                allow_login=False)
            try:
                client = GeminiClient(secure_1psid=sid, secure_1psidts=ts)
                await client.init()
                await client.fetch_gems()
                g = client.gems.get(target_id)
                if g:
                    print(json.dumps({"ok": True, "gem": {
                        "id": target_id, "name": g.name,
                        "description": g.description or "",
                        "type": "system" if g.predefined else "user",
                    }}))
                else:
                    print(json.dumps({"ok": True, "gem": {
                        "id": target_id, "name": "", "description": "",
                        "type": "external",
                        "note": "Shared Gem — not in your library",
                    }}))
            except Exception as e:
                fail("GEM_INFO_FAILED", str(e))
            return

        # ── Extract Gem ID ──
        if args.list_models or args.list_gems:
            gem_id = "dummy"
        else:
            try:
                gem_id = extract_gem_id(args.url)
            except ValueError as e:
                fail("BAD_URL", str(e))

        # ── Build prompt ──
        if args.image_prompt:
            prompt = f"Generate an image: {args.image_prompt}"
            args.image_gen = True
        elif args.prompt:
            prompt = " ".join(args.prompt)
        elif args.list_models or args.list_gems:
            prompt = ""
        elif not sys.stdin.isatty():
            prompt = sys.stdin.read().strip()
            if not prompt:
                fail("NO_PROMPT", "No prompt provided.")
        elif args.image_gen:
            prompt = "Generate an image."
        else:
            fail("NO_PROMPT", "No prompt provided. Use positional args, -p, or pipe via stdin.")

        if args.brief and prompt and not prompt.lower().startswith("be concise"):
            prompt = "Be concise. " + prompt

        # ── 5-tier auth ──
        sid, ts = resolve_auth(
            preferred_browser=args.browser or os.getenv("GEMINI_BROWSER"),
            allow_login=args.login,
        )

        # ── Init client ──
        try:
            self.client = GeminiClient(secure_1psid=sid, secure_1psidts=ts)
            await self.client.init()
        except Exception as e:
            fail("INIT_FAILED", str(e))

        # ── Discovery commands (need client) ──
        if args.list_models:
            try:
                models = self.client.list_models()
                print(json.dumps({"ok": True, "models": [str(m) for m in models]}))
            except Exception as e:
                fail("LIST_FAILED", str(e))
            return

        if args.list_gems:
            try:
                await self.client.fetch_gems()
                gems = []
                for gid, g in self.client.gems.items():
                    gems.append({
                        "id": gid,
                        "name": g.name,
                        "description": g.description or "",
                        "type": "system" if g.predefined else "user",
                    })
                print(json.dumps({"ok": True, "gems": gems}))
            except Exception as e:
                fail("LIST_FAILED", str(e))
            return

        # ── Model resolution ──
        model = None
        if args.model or args.thinking:
            if args.thinking:
                model = resolve_model_enum(args.model, args.thinking)
            else:
                model = resolve_model_string(self.client, args.model)

        # Try to get Gem name
        gem_name = ""
        try:
            await self.client.fetch_gems()
            g = self.client.gems.get(gem_id)
            if g:
                gem_name = g.name
        except Exception:
            pass

        if not self.raw_mode:
            model_label = friendly_model_label(model)
            parts = [f"gem={gem_name or gem_id}", f"model={model_label}"]
            if args.deep_research:
                parts.append("deep-research")
            if args.image_gen:
                parts.append("img-gen")
            if args.conversation:
                parts.append("multi-turn")
            print(f"[gem-cli] {', '.join(parts)}", file=sys.stderr)

        # ── Conversation state ──
        conv_state = None
        chat_metadata = None
        if args.resume_session:
            # Resume by session ID without needing a -c file
            conv_state = {
                "cid": args.resume_session,
                "metadata": [args.resume_session, ""],
                "turns": 0,
                "created": datetime.now(timezone.utc).isoformat(),
            }
            chat_metadata = conv_state["metadata"]
            if not self.raw_mode:
                self.log(f"Resuming session {args.resume_session}")
        elif args.conversation:
            if not args.new_conv:
                conv_state = load_conv(args.conversation)
                if conv_state:
                    chat_metadata = conv_state.get("metadata")
            if conv_state is None:
                conv_state = {
                    "cid": None, "metadata": None, "turns": 0,
                    "created": datetime.now(timezone.utc).isoformat(),
                }

        # ── Force flash for image generation ──
        if args.image_gen and not model:
            model = "gemini-3-flash"

        # ── Collect files ──
        all_files = []
        for img in args.images:
            p = Path(img)
            if not p.exists():
                fail("FILE_NOT_FOUND", f"Image not found: {img}")
            all_files.append(str(p))
        for f in args.files:
            p = Path(f)
            if not p.exists():
                fail("FILE_NOT_FOUND", f"File not found: {f}")
            all_files.append(str(p))

        # ── Model label for pointer ──
        model_label = friendly_model_label(model)

        # ── Deep research: auto-extend timeout ──
        actual_timeout = args.timeout
        if args.deep_research and args.timeout == 120:
            actual_timeout = 600
            if not self.raw_mode:
                self.log(f"Deep research mode: timeout auto-extended to {actual_timeout}s")

        # ── Generate with retry ──
        max_attempts = 1 if args.no_retry else 3

        for attempt in range(max_attempts):
            if attempt > 0:
                self.log(f"Retry {attempt + 1}/{max_attempts}...")

            try:
                if args.deep_research:
                    # Deep research: create plan → start → wait for results
                    if not self.raw_mode:
                        self.log("Creating research plan...")
                    plan = await asyncio.wait_for(
                        self.client.create_deep_research_plan(prompt, model=model),
                        timeout=120)
                    if not self.raw_mode:
                        self.log(f"Plan: {plan.title or 'Research plan'} — starting...")
                    await asyncio.wait_for(
                        self.client.start_deep_research(
                            plan,
                            confirm_prompt="Proceed with this plan without any modifications.",
                        ),
                        timeout=120)
                    if not self.raw_mode:
                        self.log("Research in progress...")
                    result = await asyncio.wait_for(
                        self.client.wait_for_deep_research(
                            plan,
                            poll_interval=15.0,
                            timeout=actual_timeout,
                            on_status=lambda s: (
                                self.log(f"  [{s.state or '...'}]") 
                                if not self.raw_mode and s else None
                            ),
                        ),
                        timeout=actual_timeout,
                    )
                    response = result.final_output
                else:
                    kwargs = {"prompt": prompt}
                    if all_files:
                        kwargs["files"] = all_files
                    if chat_metadata:
                        kwargs["chat"] = ChatRef(chat_metadata)
                    if model:
                        kwargs["model"] = model
                    kwargs["gem"] = gem_id
                    if args.deep_research:
                        kwargs["deep_research"] = True
                    response = await asyncio.wait_for(
                        self.client.generate_content(**kwargs),
                        timeout=actual_timeout,
                    )
            except asyncio.TimeoutError:
                if attempt == max_attempts - 1:
                    fail("TIMEOUT", f"Generation timed out after {actual_timeout}s. "
                         f"Use -t to increase (e.g. -t 180 for Pro+extended).",
                         {"timeout_s": actual_timeout, "retry": False})
                continue
            except Exception as e:
                err_msg = str(e)
                kind = error_kind(err_msg)
                
                if kind == "AUTH_EXPIRED":
                    if attempt == max_attempts - 1:
                        fail("AUTH_EXPIRED", err_msg, {"retry": False})
                    self.log("Auth expired, re-scanning cookies...")
                    new_sid, new_ts = refresh_auth_on_error(
                        preferred_browser=args.browser or os.getenv("GEMINI_BROWSER"))
                    if new_sid:
                        sid, ts = new_sid, new_ts
                        self.client = GeminiClient(secure_1psid=sid, secure_1psidts=ts)
                        await self.client.init()
                        continue
                
                if kind == "RATE_LIMIT":
                    wait = 30 if attempt == 0 else 60
                    if attempt == max_attempts - 1:
                        fail("RATE_LIMIT", err_msg, {"retry_after_s": wait, "retry": True})
                    self.log(f"Rate limited, waiting {wait}s...")
                    await asyncio.sleep(wait)
                    continue
                
                if attempt == max_attempts - 1:
                    fail(kind, err_msg)
                continue

            # Success — process response
            text = response.text
            new_meta = list(response.metadata) if response.metadata else None

            images_out = []
            try:
                for img in response.images:
                    images_out.append({"url": img.url, "alt": img.alt or ""})
            except Exception:
                pass

            # Update conversation
            if args.conversation and new_meta:
                conv_state["cid"] = new_meta[0]
                conv_state["metadata"] = new_meta
                conv_state["turns"] += 1
                save_conv(args.conversation, conv_state)

            # Output
            ext = ".json" if args.json_out else ".md"
            out_path = Path(args.output) if args.output else \
                       Path(f"/tmp/gem-cli-{datetime.now().strftime('%Y%m%d-%H%M%S')}{ext}")

            if args.json_out:
                payload = {"ok": True, "text": text, "model": model_label}
                if images_out:
                    payload["images"] = images_out
                if conv_state:
                    payload["conversation"] = conv_state
                out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
            else:
                out_text = text
                if images_out:
                    out_text += "\n\n## Generated Images\n\n"
                    for i, img in enumerate(images_out):
                        out_text += f"{i+1}. ![ {img['alt']} ]({img['url']})\n"
                out_path.write_text(out_text, encoding="utf-8")

            code_blocks = self.parse_code_blocks(text)
            
            if args.extract_code:
                # --extract-code N: save just the Nth code block
                n = args.extract_code
                if n < 1 or n > len(code_blocks):
                    fail("BAD_CODE_INDEX", f"Code block {n} not found. Response has {len(code_blocks)} code blocks.")
                cb = code_blocks[n - 1]
                code_text = cb["code"]
                out_path = Path(args.output) if args.output else \
                           Path(f"/tmp/gem-cli-code-{n}.{cb['lang'] or 'txt'}")
                out_path.write_text(code_text, encoding="utf-8")
                print(json.dumps({"ok": True, "action": "extract-code", "n": n,
                                  "lang": cb["lang"] or "text", "f": self._short_path(out_path),
                                  "s": out_path.stat().st_size}))
                return
            
            self.pointer(out_path, conv_state, images_out, len(code_blocks),
                        model_label=model_label, gem_name=gem_name,
                        deep_research=args.deep_research)
            return

        fail("RETRY_EXHAUSTED", f"Failed after {max_attempts} attempts.")


if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    cli = GemCLI()
    asyncio.run(cli.run())
