#!/usr/bin/env python3
"""
gemini — AI-agent-native, token-efficient CLI for Gemini Web + Gems.
Combines gem-cli (shared Gems, token-efficient output) + gemini.py (Gem CRUD, chat history).

Always writes response to file; stdout gets a compact pointer JSON.
5-tier auth: env vars → cached file → browser cookie scan → retry → login.

Output: {"ok":true,"f":"./out.md","s":1234,"b":2,"imgs":3,
         "model":"gemini-3-flash","gem":"GemName","c":"c_xxx","t":5}

Repo: lesterppo/hermes-gem-cli (primary), lesterppo/gemini-web-cli (historical)
"""
import asyncio, argparse, json, os, re, subprocess, sys, time, webbrowser
from datetime import datetime, timezone
from pathlib import Path

# ── WSL2 fix: curl_cffi hangs on WSL2 → urllib-based session ──
# On WSL2, curl_cffi's async requests hang indefinitely. Native Linux
# and GitHub Actions work fine with curl_cffi. Detect at import time.
def _is_wsl2() -> bool:
    """Return True if running on WSL2 (where curl_cffi hangs)."""
    try:
        with open("/proc/version") as f:
            return "microsoft" in f.read().lower()
    except Exception:
        return False

# SNlM0e is dead (Jul 2026); tokens fetched from gemini.google.com/app.
def _wsl2_init_fix():
    try:
        import json, os, re, urllib.request, urllib.parse, random as _random
        from pathlib import Path

        # Import urllib session wrapper
        _here = Path(__file__).resolve().parent
        if str(_here) not in __import__("sys").path:
            __import__("sys").path.insert(0, str(_here))
        from urllib_session import UrllibSession

        import gemini_webapi.utils.get_access_token as _gat

        async def _patched_get_access_token(base_cookies, proxy=None, verbose=False, verify=True):
            s = UrllibSession(impersonate="chrome", timeout=30)
            if isinstance(base_cookies, dict):
                for k, v in base_cookies.items():
                    if v: s.cookies.set(k, v, domain=".google.com")
            else:
                try:
                    for c in base_cookies.jar:
                        s.cookies.set(c.name, c.value, domain=c.domain, path=c.path)
                except Exception:
                    for k, v in dict(base_cookies).items():
                        if v: s.cookies.set(k, v, domain=".google.com")
            try:
                r = await s.get("https://gemini.google.com/app")
                html = r.text
                bl = (re.search(r'"cfb2h":\s*"(.*?)"', html) or [None,None])[1]
                sid = (re.search(r'"FdrFJe":\s*"(.*?)"', html) or [None,None])[1]
                lang = (re.search(r'"TuX5cc":\s*"(.*?)"', html) or [None,None])[1]
                pid = (re.search(r'"qKIAYe":\s*"(.*?)"', html) or [None,None])[1]
                return (None, bl, sid, lang, pid or "feeds/mcudyrk2a4khkz", s)
            except Exception:
                return (None, None, None, None, None, s)

        _gat.get_access_token = _patched_get_access_token
        # Also patch the client module's reference (the one init() actually calls)
        try:
            import gemini_webapi.client as _client_mod
            _client_mod.get_access_token = _patched_get_access_token
        except Exception:
            pass
    except Exception:
        pass

if _is_wsl2():
    _wsl2_init_fix()

# ── Dependencies ─────────────────────────────────────────────

try:
    from gemini_webapi import GeminiClient
    from gemini_webapi.client import Model as GeminiModel
except ImportError:
    # Exiting here is right for the CLI, but it also kills anything that merely
    # IMPORTS this module (tests, tooling) — so only the entry point exits.
    if __name__ == "__main__":
        print(json.dumps({"ok": False, "err": "DEP_MISSING",
                          "msg": "gemini-webapi not installed. Run: pip install gemini-webapi"}))
        sys.exit(1)
    GeminiClient = None
    GeminiModel = None

try:
    import loguru as _loguru
    _loguru.logger.remove()
    _loguru.logger.add(sys.stderr, level="ERROR", format="<red>[gemini]</red> {message}")
except ImportError:                                   # optional at import time
    _loguru = None

# ── Paths ────────────────────────────────────────────────────

AUTH_CACHE = Path.home() / ".gemini-cli" / "auth.json"
GEM_HOME = Path.home() / ".gemini-cli"
SEARCH_GEM_PROMPT = Path(__file__).resolve().parent / "search-gem-prompt.txt"
SEARCH_GEM_NAME = "Gemini search"
SEARCH_GEM_DESC = "Headless Search Grounding Proxy — ultra-dense positional-array JSON for AI agents"

# ── Auth ─────────────────────────────────────────────────────

_GEM_URL_RE = re.compile(r'gemini\.google\.com/gem/([a-zA-Z0-9_-]+)')

_AUTH_ERROR_PATTERNS = [
    "UNAUTHENTICATED", "cookies have expired", "session is not authenticated",
    "error code: 1100", "User is not authenticated",
]
_RATE_LIMIT_PATTERNS = [
    "error code: 1097", "rate limit", "too many requests",
    "quota exceeded", "resource has been exhausted",
]

def extract_gem_id(url: str) -> str:
    m = _GEM_URL_RE.search(url)
    if m: return m.group(1)
    if '/' not in url and ' ' not in url and len(url) >= 5: return url
    raise ValueError(f"Cannot extract Gem ID from: {url}")

def is_auth_error(msg: str) -> bool:
    u = msg.upper()
    return any(p.upper() in u for p in _AUTH_ERROR_PATTERNS)

def is_rate_limit(msg: str) -> bool:
    u = msg.upper()
    return any(p.upper() in u for p in _RATE_LIMIT_PATTERNS)

def error_kind(msg: str) -> str:
    if is_auth_error(msg): return "AUTH_EXPIRED"
    if is_rate_limit(msg): return "RATE_LIMIT"
    return "GEN_FAILED"

# ── Model labels ─────────────────────────────────────────────

_MODEL_LABEL_MAP = {
    "BASIC_FLASH": "flash+standard",  "PLUS_FLASH": "flash+plus",
    "ADVANCED_FLASH": "flash+extended", "BASIC_PRO": "pro+standard",
    "PLUS_PRO": "pro+plus", "ADVANCED_PRO": "pro+extended",
    "BASIC_THINKING": "thinking+standard", "PLUS_THINKING": "thinking+plus",
    "ADVANCED_THINKING": "thinking+extended",
    "gemini-3-flash": "flash", "gemini-3-pro": "pro",
    "gemini-3-flash-lite": "lite", "gemini-3.5-flash-lite": "lite",
    "Flash-Lite": "lite", "gemini-flash-lite": "lite",
    "gemini-flash": "flash", "gemini-pro": "pro",
    "gemini-3-flash-thinking": "thinking", "3.5 Flash-Lite": "lite",
}

_LITE_MODEL_DICT = {
    "model_name": "Flash-Lite",
    "model_header": {
        "x-goog-ext-525001261-jspb": '[1,null,null,null,"8c46e95b1a07cecc",null,null,0,[4],null,null,1]',
        "x-goog-ext-73010989-jspb": "[0]",
        "x-goog-ext-73010990-jspb": "[0]",
    },
}

_MODEL_ALIASES = {"pro": "PRO", "flash": "FLASH", "fast": "FLASH",
                   "thinking": "THINKING", "think": "THINKING", "lite": "LITE"}
_THINKING_ALIASES = {"standard": "BASIC", "basic": "BASIC",
                      "plus": "PLUS", "extended": "ADVANCED", "advanced": "ADVANCED"}

def friendly_model_label(model) -> str:
    # No -m: the account default is used, and "None" in the pointer read like a bug
    # (it was one). Say what actually happened instead.
    if model is None:
        return "default"
    if isinstance(model, dict):
        return _MODEL_LABEL_MAP.get(model.get("model_name", ""), model.get("model_name", "lite"))
    if hasattr(model, 'name'):
        return _MODEL_LABEL_MAP.get(model.name, model.name.lower())
    if isinstance(model, str):
        return _MODEL_LABEL_MAP.get(model, model.lower())
    return str(model)

def _supports(fn, param: str) -> bool:
    """True when a library callable accepts `param` (lib versions drift)."""
    try:
        import inspect
        return param in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def resolve_model_enum(model_str: str | None, thinking: str | None = None):
    if not model_str: return None
    tier = _THINKING_ALIASES.get(thinking.lower().strip(), thinking.upper()) if thinking else None
    mtype = _MODEL_ALIASES.get(model_str.lower().strip())
    if mtype is None: return model_str
    if mtype == "LITE": return dict(_LITE_MODEL_DICT)
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
        known = {"8c46e95b1a07cecc": "Flash-Lite",
                 "56fdd199312815e2": "gemini-3-flash",
                 "e6fa609c3fa255c0": "gemini-3-pro"}
        name_map = {known.get(m.model_id, str(m).lower()):
                     known.get(m.model_id, str(m)) for m in available}
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
    if q in ("lite",):
        matches = [v for k, v in name_map.items() if "flash-lite" in k.lower() or "lite" in k.lower()]
        if matches: return matches[0]
        return dict(_LITE_MODEL_DICT)
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

def _cdp_cookies(cdp_url: str | None = None) -> tuple:
    """Live cookies from an already-running CDP browser.

    This is the preferred source: no browser launch (the daemon is already up),
    no user action, no decryption of the browser's on-disk cookie DB. Run in a
    subprocess because the sync Playwright API cannot start inside a running
    asyncio loop, and gemini.py drives its client from one.
    """
    url = (cdp_url or os.getenv("GEMINI_CDP_URL") or os.getenv("GEM_PW_CDP")
           or "http://127.0.0.1:9223")
    if os.getenv("GEMINI_NO_CDP"):
        return None, None
    snippet = (
        "import json,sys\n"
        "try:\n"
        "    from playwright.sync_api import sync_playwright\n"
        "except ImportError:\n"
        "    print('{}'); raise SystemExit\n"
        "out={'sid':None,'ts':None}\n"
        "try:\n"
        "    with sync_playwright() as p:\n"
        "        b=p.chromium.connect_over_cdp(sys.argv[1], timeout=8000)\n"
        "        for ctx in b.contexts:\n"
        "            try: cks=ctx.cookies('https://gemini.google.com')\n"
        "            except Exception: continue\n"
        "            for c in cks:\n"
        "                if c['name']=='__Secure-1PSID': out['sid']=c['value']\n"
        "                elif c['name']=='__Secure-1PSIDTS': out['ts']=c['value']\n"
        "            if out['sid']: break\n"
        "except Exception:\n"
        "    pass\n"
        "print(json.dumps(out))\n"
    )
    try:
        r = subprocess.run([sys.executable, "-c", snippet, url],
                           capture_output=True, text=True, timeout=30)
        d = json.loads((r.stdout or "").strip().splitlines()[-1])
        if d.get("sid"):
            return d["sid"], d.get("ts")
    except Exception:
        pass
    return None, None


# Which source produced the currently-resolved credential pair. Surfaced so a
# failure is diagnosable instead of "it just stopped working".
_LAST_AUTH_SRC = None


def _local_browser_candidates(preferred: str | None = None) -> list:
    """EVERY local browser profile that holds a 1PSID, not just the first hit.

    Returning only the first is why a dead Chrome profile could shadow a live
    Firefox one: nothing else was ever enumerated, so recovery had nothing to
    fall back to.
    """
    out = []
    try: import browser_cookie3
    except ImportError: return out
    order = [('chrome', browser_cookie3.chrome), ('firefox', browser_cookie3.firefox),
             ('edge', browser_cookie3.edge), ('safari', browser_cookie3.safari)]
    if preferred:
        for i, (n, _) in enumerate(order):
            if n == preferred.lower():
                order.insert(0, order.pop(i)); break
    for name, fn in order:
        try:
            cj = fn(domain_name='.google.com')
            sid = ts = None
            for c in cj:
                if c.name == '__Secure-1PSID': sid = c.value
                elif c.name == '__Secure-1PSIDTS': ts = c.value
            if sid:
                out.append((f"browser:{name}", sid, ts))
        except Exception:
            continue
    return out


def _scan_local_browsers(preferred: str | None = None) -> tuple:
    global _LAST_AUTH_SRC
    for src, sid, ts in _local_browser_candidates(preferred):
        _LAST_AUTH_SRC = src
        return sid, ts
    return None, None


def _scan_browser_cookies(preferred: str | None = None) -> tuple:
    """Local browser cookie DBs first, CDP browser as the fallback source.

    Ordering matters: a browser holding cookies is NOT proof the session is still
    valid server-side. The CDP profile in particular can sit on a dead session
    (Google invalidates it long before the cookie's nominal expiry), so it must
    never outrank a local profile that is actually working.
    """
    sid, ts = _scan_local_browsers(preferred)
    if sid:
        return sid, ts
    sid, ts = _cdp_cookies()
    if sid:
        globals()["_LAST_AUTH_SRC"] = "cdp"
    return sid, ts


def _auth_candidates(preferred: str | None = None) -> list:
    """Every DISTINCT credential pair available, with its source label.

    Distinctness is by 1PSID: the same session reached through two sources is one
    candidate, so the retry path never burns an attempt on a duplicate.
    """
    out = []

    def add(src, sid, ts):
        if sid and all(c[1] != sid for c in out):
            out.append((src, sid, ts))

    add("env", os.getenv("GEMINI_SID"), os.getenv("GEMINI_TS"))
    c_sid, c_ts = _load_auth_cache()
    add("cache", c_sid, c_ts)
    for src, sid, ts in _local_browser_candidates(preferred):
        add(src, sid, ts)
    d_sid, d_ts = _cdp_cookies()
    add("cdp", d_sid, d_ts)
    return out


# Credentials already proven dead this process. Tracking the whole set (not just
# the most recent failure) stops recovery from oscillating between two stale
# sources until the attempt budget runs out.
_FAILED_SIDS = set()


def _next_auth(failed_sid: str | None, preferred: str | None = None) -> tuple:
    """Next credential that has not already failed in this process.

    Retrying a known-dead pair is a guaranteed failure dressed up as recovery.
    """
    if failed_sid:
        _FAILED_SIDS.add(failed_sid)
    for src, sid, ts in _auth_candidates(preferred):
        if sid in _FAILED_SIDS:
            continue
        globals()["_LAST_AUTH_SRC"] = src
        return sid, ts
    return None, None

def _validate_auth(sid: str, ts: str | None = None, timeout: int = 20) -> bool:
    """Cheap real check: does gemini.google.com/app hand back a session token?

    Holding a cookie is not evidence the session is alive server-side, and the
    only reliable way to tell the difference is to ask the server.
    """
    if not sid:
        return False
    try:
        import urllib.request
        req = urllib.request.Request("https://gemini.google.com/app", headers={
            "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"),
            "Cookie": f"__Secure-1PSID={sid}" + (f"; __Secure-1PSIDTS={ts}" if ts else ""),
            "Accept-Language": "en",
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return "SNlM0e" in r.read(400000).decode("utf-8", "replace")
    except Exception:
        return False


def _init_validated(preferred: str | None = None) -> tuple:
    """Save the first candidate that actually authenticates. Returns (src, sid, ts)."""
    cands = _auth_candidates(preferred)
    for src, sid, ts in cands:
        if _validate_auth(sid, ts):
            _save_auth_cache(sid, ts)
            globals()["_LAST_AUTH_SRC"] = src
            return src, sid, ts
    return None, None, None


def _browser_login(preferred: str | None = None) -> tuple:
    if not sys.stdout.isatty(): return None, None
    print("[gemini] Opening gemini.google.com for login...", file=sys.stderr)
    webbrowser.open("https://gemini.google.com")
    for i in range(40):
        time.sleep(3)
        sid, ts = _scan_browser_cookies(preferred=preferred)
        if sid:
            _save_auth_cache(sid, ts); return sid, ts
    return None, None

# ── Session liveness ─────────────────────────────────────────
# A cookie PAIR can be chat-valid while being read-invalid: StreamGenerate accepts
# it (chat works, which is why this went unnoticed) but every SNlM0e-gated call —
# Gem CRUD, --list-gems, --list-chats, /app reads, account status — is rejected.
# Keep a per-process probe verdict so preferring a live credential costs one
# request per session at most.
_PROBE_CACHE: dict = {}
_WARNED_NO_LIVE = [False]


def _probe_live(sid: str, ts: str | None) -> bool:
    if not sid:
        return False
    if sid not in _PROBE_CACHE:
        _PROBE_CACHE[sid] = _validate_auth(sid, ts)
    return _PROBE_CACHE[sid]


def _browser_cookie_jars(preferred: str | None = None) -> list:
    """Every browser's FULL .google.com jar, labelled (preferred browser first).

    Full jars matter: the /app page hands back SNlM0e only when the rotating
    suite of cookies (SID/SAPISID/__Secure-1PAPISID/...) accompanies the pair.
    """
    try:
        import browser_cookie3
    except ImportError:
        return []
    order = [("chrome", browser_cookie3.chrome), ("firefox", browser_cookie3.firefox),
             ("edge", browser_cookie3.edge), ("safari", browser_cookie3.safari)]
    if preferred:
        for i, (n, _) in enumerate(order):
            if n == preferred.lower():
                order.insert(0, order.pop(i)); break
    out = []
    for name, fn in order:
        try:
            jar = {c.name: c.value for c in fn(domain_name=".google.com")}
        except Exception:
            continue
        if jar.get("__Secure-1PSID"):
            out.append((f"browser:{name}", jar))
    return out


def _cookie_jar_for(sid: str | None, preferred: str | None = None) -> tuple:
    """The full cookie jar that BELONGS to `sid` — never a mix of two sessions.

    Mixing a 1PSID from one source with a 1PSIDTS from another yields a jar the
    server treats as signed out; that single mistake is what broke --list-chats
    and --read-chat while chat kept working.
    """
    jars = _browser_cookie_jars(preferred)
    for src, jar in jars:
        if sid and jar.get("__Secure-1PSID") == sid:
            return src, jar
    if jars:
        return jars[0][0], jars[0][1]
    return "credential", {}


def resolve_auth(preferred_browser: str | None = None, allow_login: bool = False) -> tuple:
    """Pick a credential pair, preferring one whose session is actually live.

    Order is unchanged (env → cache → browsers → CDP) but a candidate has to pass
    the cheap /app probe before it wins; if none does, the first available pair is
    returned anyway so chat still has a chance. Without this, a stale cache entry
    silently shadowed the live browser session and every read/Gem-CRUD call failed.
    """
    global _LAST_AUTH_SRC
    sid = os.getenv("GEMINI_SID"); ts = os.getenv("GEMINI_TS")
    if sid:
        _LAST_AUTH_SRC = "env"
        return sid, ts

    cands = _auth_candidates(preferred_browser)
    if not cands and allow_login:
        sid, ts = _browser_login(preferred=preferred_browser)
        if sid:
            _LAST_AUTH_SRC = "login"
            return sid, ts
    if not cands:
        print(json.dumps({"ok": False, "err": "AUTH_EXPIRED",
                          "msg": "No Gemini cookies. Set GEMINI_SID/TS, run --init, or --login."}))
        sys.exit(1)

    for src, c_sid, c_ts in cands:
        if _probe_live(c_sid, c_ts):
            if src != "cache":
                # Promote the live credential so the next run skips the probe tail.
                _save_auth_cache(c_sid, c_ts)
            _LAST_AUTH_SRC = src
            return c_sid, c_ts

    if not _WARNED_NO_LIVE[0]:
        _WARNED_NO_LIVE[0] = True
        print("[gemini] warning: no live-session credential found — using the first "
              "available pair (chat may work; Gem/read APIs will fail)", file=sys.stderr)
    src, c_sid, c_ts = cands[0]
    _LAST_AUTH_SRC = src
    return c_sid, c_ts

def refresh_auth(preferred: str | None = None) -> tuple:
    sid, ts = _scan_browser_cookies(preferred=preferred)
    if sid: _save_auth_cache(sid, ts)
    return sid, ts

# ── Browser-shaped batchexecute (works around gemini_webapi 2.x header/payload gaps) ──
# Root cause found 2026-09-01: lib's _batch_execute omits Origin/Referer/X-Same-Domain
# headers and sends f.req single-wrapped; server then rejects read-RPCs (MaZiqc, hNvQHb,
# CNgdBe, oMH3Zd...) with [\"e\",4,null,null,NNN]. Browser-shaped POST returns 200.
_BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36")

class BrowserBatchRPC:
    """Direct batchexecute client with full browser cookie set + browser-shaped payload."""

    def __init__(self, sid: str, ts: str | None = None):
        self.sid = sid
        self.ts = ts
        self.session = None
        self.at = None
        self.bl = ""

    async def init(self):
        from curl_cffi.requests import AsyncSession
        self.session = AsyncSession(impersonate="chrome145")
        # Consistency beats coverage: take ONE source's full jar. A jar assembled
        # from two sessions (1PSID from the browser, 1PSIDTS from the cache) is
        # rejected as signed-out even though both halves are valid on their own.
        src, jar = _cookie_jar_for(self.sid, os.getenv("GEMINI_BROWSER"))
        if jar and (not self.sid or jar.get("__Secure-1PSID") == self.sid):
            use = jar
        elif jar:
            use = jar                      # browser session outranks a lone pair
            print(f"[gemini] browser-shaped RPC: using {src} session "
                  f"(requested credential had no full jar)", file=sys.stderr)
        else:
            use = {}
        for k, v in use.items():
            self.session.cookies.set(k, v, domain=".google.com", secure=True)
        if not use:
            if self.sid:
                self.session.cookies.set("__Secure-1PSID", self.sid, domain=".google.com", secure=True)
            if self.ts:
                self.session.cookies.set("__Secure-1PSIDTS", self.ts, domain=".google.com", secure=True)
        r = await self.session.get("https://gemini.google.com/app", headers={"User-Agent": _BROWSER_UA})
        m = re.search(r'"SNlM0e":"(.*?)"', r.text)
        self.at = m.group(1) if m else None
        mb = re.search(r'"cfb2h":"(.*?)"', r.text)
        self.bl = mb.group(1) if mb else ""
        if not self.at:
            raise RuntimeError("AUTH_EXPIRED: no SNlM0e in /app HTML (not signed in?)")
        return self

    async def rpc(self, rpcid: str, payload, retries: int = 2):
        from urllib.parse import urlencode
        freq = json.dumps([[["%s" % rpcid, payload if isinstance(payload, str) else json.dumps(payload), None, "generic"]]])
        body = urlencode({
            "rpcids": rpcid, "source-path": "/app", "bl": self.bl, "hl": "en",
            "f.req": freq, "at": self.at,
        })
        headers = {
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Origin": "https://gemini.google.com",
            "Referer": "https://gemini.google.com/app",
            "X-Same-Domain": "1",
            "User-Agent": _BROWSER_UA,
        }
        last = None
        for attempt in range(retries + 1):
            r = await self.session.post(
                "https://gemini.google.com/_/BardChatUi/data/batchexecute",
                data=body, headers=headers)
            if r.status_code == 200 and '["e",4' not in r.text[:2000]:
                return r.text
            last = r
            if attempt < retries:
                await asyncio.sleep(2 * (attempt + 1))
        raise RuntimeError(f"RPC {rpcid} failed: HTTP {last.status_code} {last.text[:120]}")

    async def close(self):
        try: await self.session.close()
        except Exception: pass

def _extract_rpc_payloads(rpc_text: str, rpcid: str) -> list:
    """Pull inner JSON payloads for target rpcid from batchexecute response."""
    from gemini_webapi.utils.parsing import extract_json_from_response, get_nested_value
    out = []
    try:
        for part in extract_json_from_response(rpc_text):
            if get_nested_value(part, [1]) != rpcid:
                continue
            body = get_nested_value(part, [2])
            if not body: continue
            try: out.append(json.loads(body))
            except (json.JSONDecodeError, TypeError): continue
    except Exception: pass
    return out


async def _fetch_gems_rpc(sid: str, ts: str | None, language: str = "en") -> list:
    """Gem list via browser-shaped LIST_BOTS (CNgdBe).

    gemini_webapi's fetch_gems() also calls _check_account_status() first, so a
    stale credential turns a perfectly answerable list request into
    "Permission denied" — this path skips that gate and needs only a live jar.
    Two calls, because the endpoint separates predefined (3) from custom (2).
    """
    rpc = await BrowserBatchRPC(sid, ts).init()
    out = []
    try:
        for kind, payload in (("system", f"[3,['{language}'],0]"),
                              ("custom", f"[2,['{language}'],0]")):
            text = await rpc.rpc("CNgdBe", payload)
            for pb in _extract_rpc_payloads(text, "CNgdBe"):
                gems = pb[2] if isinstance(pb, list) and len(pb) > 2 else None
                if not isinstance(gems, list):
                    continue
                for gem in gems:
                    if not (isinstance(gem, list) and gem and isinstance(gem[0], str)):
                        continue
                    meta = gem[1] if len(gem) > 1 and isinstance(gem[1], list) else []
                    out.append({
                        "id": gem[0],
                        "name": (meta[0] if len(meta) > 0 else "") or "",
                        "description": (meta[1] if len(meta) > 1 else "") or "",
                        "type": "system" if kind == "system" else "user",
                    })
    finally:
        await rpc.close()
    return out

# ── Image detection ──────────────────────────────────────────

_IMG_GEN_STARTS = ["generate an image", "create an image", "make an image",
                    "draw a", "generate a photo", "create a picture"]
_IMG_GEN_KW = _IMG_GEN_STARTS + ["show me a picture", "show me an image",
                                  "generate", "create", "draw", "illustrate", "paint"]

def looks_like_image_gen(prompt: str) -> bool:
    p = prompt.lower().strip()
    for kw in _IMG_GEN_STARTS:
        if p.startswith(kw): return True
    return sum(1 for kw in _IMG_GEN_KW if kw in p) >= 2

# ── Conversation state ───────────────────────────────────────

class ChatRef:
    def __init__(self, metadata: list):
        self.metadata = metadata

def load_conv(path: str) -> dict | None:
    p = Path(path)
    if not p.exists(): return None
    try:
        s = json.loads(p.read_text(encoding="utf-8"))
        if s.get("metadata") and len(s["metadata"]) >= 1: return s
    except (json.JSONDecodeError, KeyError): pass
    return None

def save_conv(path: str, state: dict):
    state["updated"] = datetime.now(timezone.utc).isoformat()
    Path(path).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

# ── Helpers ──────────────────────────────────────────────────

def fail(code: str, msg: str, extra: dict | None = None):
    out = {"ok": False, "err": code, "msg": msg}
    if extra: out.update(extra)
    print(json.dumps(out)); sys.exit(1)

# ── Main CLI class ───────────────────────────────────────────

class GeminiCLI:
    def __init__(self):
        self.client = None
        self.raw_mode = False

    def log(self, msg: str):
        if not self.raw_mode:
            print(f"[gemini] {msg}", file=sys.stderr)

    def pointer(self, out_path: Path, conv_state: dict | None = None,
                images: list | None = None, videos: list | None = None, media: list | None = None,
                code_blocks: int = 0, thoughts: bool = False,
                model_label: str = "", gem_name: str = "", deep_research: bool = False, temporary: bool = False):
        p = {"ok": True, "f": self._short(out_path), "s": out_path.stat().st_size}
        if code_blocks: p["b"] = code_blocks
        if images: p["imgs"] = len(images)
        if videos: p["vids"] = len(videos)
        if media: p["media"] = len(media)
        if thoughts: p["thoughts"] = True
        if model_label: p["model"] = model_label
        if gem_name: p["gem"] = gem_name
        if deep_research: p["dr"] = True
        if temporary: p["tmp"] = True
        if conv_state:
            p["c"] = conv_state.get("cid")
            p["t"] = conv_state.get("turns")
        print(json.dumps(p))

    @staticmethod
    def _short(p: Path) -> str:
        try: return "./" + str(p.resolve().relative_to(Path.cwd())).replace("\\", "/")
        except ValueError: return str(p.resolve())

    def parse_code_blocks(self, text: str) -> list:
        return [{"lang": m[0], "code": m[1].strip()}
                for m in re.findall(r"```(\w*)\n(.*?)```", text, re.DOTALL)]

    def _pw_fallback(self, gem_id: str, prompt: str, output: str | None = None):
        """Playwright browser fallback for Gem operations."""
        try:
            import subprocess
            pw = str(Path(__file__).resolve().parent / "gem-pw")
            args = [sys.executable, pw, gem_id]
            if output: args.extend(["-o", output])
            args.append(prompt)
            r = subprocess.run(args, capture_output=True, text=True, timeout=180)
            if r.returncode == 0 and r.stdout.strip():
                pwj = json.loads(r.stdout.strip())
                if pwj.get("ok"): return pwj
        except Exception: pass
        return None

    async def run(self):
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')

        p = argparse.ArgumentParser(
            description="gemini — AI-agent-native CLI for Gemini Web + Gems",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="""Examples:
  gemini AbCdEf1234 "Hello"                          # chat with a Gem
  gemini AbCdEf1234 -c sess.json --new "start"        # multi-turn
  gemini AbCdEf1234 -f report.pdf -m pro "analyze"    # file upload
  gemini AbCdEf1234 -i chart.png "explain trend"      # image upload
  gemini AbCdEf1234 --img "a cat flying"              # image generation
  gemini AbCdEf1234 --deep-research "topic"           # deep research
  gemini --init                                        # cache auth
  gemini --login                                       # browser login
  gemini --list-models                                 # available models
  gemini --list-gems                                   # your Gems
  gemini --create-gem MyGem -p "system prompt"        # create Gem
  gemini --edit-gem MyGem -n "NewName" -d "Desc"      # edit Gem
  gemini --delete-gem AbCdEf1234                       # delete Gem
  gemini --list-chats                                  # chat history
  gemini --read-chat c_xxx                             # read a chat
  gemini --fetch-latest c_xxx                          # fetch latest turn
  gemini --deep-research-status <research_id>          # research status
  gemini --account-status                              # check account
  gemini --setup-search-gem                            # create search Gem
  gemini -p "prompt" --temporary                       # temporary chat
  gemini -p "prompt" --show-thoughts -m thinking       # show thinking traces
  gemini -p "hi" --save-videos ./vids                  # save Veo videos

Output: compact JSON pointer on stdout, full response on disk.""")
        
        # Core
        p.add_argument("url", nargs="?", help="Shared Gem URL or Gem ID")
        p.add_argument("prompt", nargs="*", help="Prompt text (reads stdin if empty)")
        # Files
        p.add_argument("-i", "--image", action="append", dest="images", default=[], metavar="FILE")
        p.add_argument("-f", "--file", action="append", dest="files", default=[], metavar="FILE")
        # Conversation
        p.add_argument("-c", "--conversation", metavar="FILE", help="Conversation state file")
        p.add_argument("--new", action="store_true", dest="new_conv", help="Start fresh")
        # Model
        p.add_argument("-m", "--model", choices=["flash","pro","thinking","lite"],
                       help="Model: flash, pro, thinking, lite")
        p.add_argument("--thinking", choices=["standard","plus","extended"],
                       help="Thinking tier")
        # Image gen
        p.add_argument("--img-gen", action="store_true", dest="image_gen", help="Force image gen")
        p.add_argument("--img", dest="image_prompt", metavar="PROMPT", help="Generate image")
        # Deep research
        p.add_argument("--deep-research", action="store_true", dest="deep_research",
                       help="Deep research mode (~1-10 min)")
        # Streaming
        p.add_argument("--stream", action="store_true", dest="stream",
                       help="Stream tokens in real-time")
        # Output
        p.add_argument("-o", "--output", metavar="FILE", help="Output file")
        p.add_argument("--json-out", action="store_true", help="Write .json not .md")
        p.add_argument("--brief", action="store_true", help="Prepend 'Be concise.'")
        p.add_argument("-q", "--quiet", action="store_true", help="Suppress stderr")
        p.add_argument("--raw", action="store_true", dest="raw_mode", help="Zero stderr")
        # Auth
        p.add_argument("--browser", choices=["chrome","firefox","edge","safari"], help="Browser for cookies")
        p.add_argument("--init", action="store_true", help="Cache auth tokens from browser")
        p.add_argument("--login", action="store_true", help="Open browser for login")
        p.add_argument("-p", "--prompt-flag", dest="prompt_flag", help="Prompt (alt to positional/stdin)")
        # Gem CRUD
        p.add_argument("--create-gem", dest="create_gem_name", metavar="NAME", help="Create a new Gem")
        p.add_argument("--edit-gem", dest="edit_gem_id", metavar="ID_OR_NAME",
                       help="Edit an existing Gem")
        p.add_argument("-n", "--new-name", dest="edit_new_name", help="New name for --edit-gem")
        p.add_argument("-d", "--desc", dest="edit_new_desc", help="New description for --edit-gem")
        p.add_argument("-S", "--system-instruction", dest="edit_sys_instr",
                       help="System instruction for --create-gem or --edit-gem")
        p.add_argument("--delete-gem", dest="delete_gem_id", metavar="ID", help="Delete a Gem")
        p.add_argument("--gem-info", action="store_true", help="Fetch Gem metadata")
        p.add_argument("--clear", action="store_true", dest="clear_conv", help="Delete conv file")
        # Discovery
        p.add_argument("--list-models", action="store_true", help="List models")
        p.add_argument("--list-gems", action="store_true", help="List Gems")
        p.add_argument("--list-chats", action="store_true", help="List chat history")
        p.add_argument("--read-chat", dest="read_chat_id", metavar="CID", help="Read a chat by ID")
        p.add_argument("--delete-chat", dest="delete_chat_id", metavar="CID", help="Delete a chat")
        p.add_argument("-l", "--limit", type=int, default=50, help="Limit for list commands")
        # Account
        p.add_argument("--account-status", action="store_true", help="Check account status")
        p.add_argument("--doctor", action="store_true",
                       help="Diagnose auth sources (which cookie jar is live) + capability probe")
        # Search Gem
        p.add_argument("--setup-search-gem", action="store_true", help="Create search grounding Gem")
        # Save images/videos/media
        p.add_argument("--save-images", metavar="DIR", help="Save generated images to DIR")
        p.add_argument("--save-videos", metavar="DIR", help="Save generated videos to DIR")
        p.add_argument("--save-media", metavar="DIR", help="Save generated media (audio/video) to DIR")
        # Chat modes
        p.add_argument("--temporary", action="store_true", help="Temporary chat (not saved to history)")
        p.add_argument("--show-thoughts", action="store_true", help="Include thinking traces in output")
        # Additional fetchers
        p.add_argument("--fetch-latest", dest="fetch_latest_id", metavar="CID", help="Fetch latest turn for chat CID")
        p.add_argument("--deep-research-status", dest="deep_research_status_id", metavar="ID", help="Get deep research status by research ID")
        p.add_argument("--extract-canvas", metavar="FILE", help="Save Canvas/HTML artifact to FILE")
        # Timing
        p.add_argument("-t", "--timeout", type=int, default=120, help="Max seconds (default 120)")
        p.add_argument("--no-retry", action="store_true", help="Disable auto-retry")
        p.add_argument("--extract-code", type=int, dest="extract_code", metavar="N",
                       help="Save Nth code block to file")
        p.add_argument("--resume", dest="resume_session", metavar="ID",
                       help="Resume conversation by session ID")
        p.add_argument("--timeout-soft", type=int, dest="timeout_soft", metavar="SEC",
                       help="Warn at N seconds but keep waiting")
        # Gem target (without URL)
        p.add_argument("-g", "--gem", dest="gem_id", help="Gem ID for direct chat (no URL needed)")
        
        args = p.parse_intermixed_args()
        self.raw_mode = args.raw_mode or args.quiet
        
        if self.raw_mode:
            _loguru.logger.remove()
            _loguru.logger.add(sys.stderr, level="CRITICAL")

        # ── Standalone: --init ──
        if args.init:
            self.log("Extracting auth tokens from browser...")
            sid = os.getenv("GEMINI_SID")
            ts = os.getenv("GEMINI_TS")
            if sid:
                _save_auth_cache(sid, ts)
                print(json.dumps({"ok": True, "action": "init", "cached": str(AUTH_CACHE),
                                  "auth_src": "env", "sid_len": len(sid)}))
                return
            # Validate before saving: a profile that merely HOLDS cookies may be
            # sitting on a session Google already invalidated.
            src, sid, ts = _init_validated(args.browser or os.getenv("GEMINI_BROWSER"))
            if sid:
                print(json.dumps({"ok": True, "action": "init", "cached": str(AUTH_CACHE),
                                  "auth_src": src, "validated": True, "sid_len": len(sid)}))
            else:
                fail("AUTH_EXPIRED",
                     "No candidate authenticated. Sign in at gemini.google.com in a local "
                     "browser (or start the CDP browser signed in), then re-run --init.",
                     {"candidates_tried": [s for s, _, _ in _auth_candidates()],
                      "retry": False})
            return

        # ── Standalone: --login ──
        if args.login:
            sid, ts = _browser_login(preferred=args.browser or os.getenv("GEMINI_BROWSER"))
            if sid:
                print(json.dumps({"ok": True, "action": "login", "cached": str(AUTH_CACHE)}))
            else:
                fail("LOGIN_FAILED", "Login timed out.")
            return

        # ── Standalone: --account-status ──
        if args.account_status:
            sid, ts = resolve_auth(preferred_browser=args.browser or os.getenv("GEMINI_BROWSER"))
            try:
                from gemini_webapi.constants import GRPC as _GRPC, AccountStatus as _AS
                from gemini_webapi.client import RPCData as _RPCData
                from gemini_webapi.utils.parsing import extract_json_from_response as _extract, get_nested_value as _gv
                client = GeminiClient(secure_1psid=sid, secure_1psidts=ts)
                await client.init()
                
                result = {
                    "ok": True,
                    "status_code": int(client.account_status),
                    "status_name": client.account_status.name,
                    "status_desc": client.account_status.description,
                    "language": client.language,
                    "session_id": str(client.session_id),
                    "build": client.build_label,
                }
                
                # ── Email from HTML ──
                try:
                    r = await client.client.get("https://gemini.google.com/app")
                    emails = set(re.findall(r'[\w.+-]+@gmail\.com', r.text))
                    result["emails"] = sorted(emails)
                except Exception:
                    result["emails"] = []
                
                # ── Models ──
                try:
                    result["available_models"] = [str(m) for m in client.list_models()]
                except Exception:
                    result["available_models"] = []
                
                # ── Quota / usage limits ──
                try:
                    resp = await client._batch_execute([
                        _RPCData(rpcid=_GRPC.DEEP_RESEARCH_MODEL_STATE,
                                 payload="[[[1,11],[2,11],[6,11]]]"),
                        _RPCData(rpcid=_GRPC.DEEP_RESEARCH_MODEL_STATE,
                                 payload="[[[1,4],[2,4],[6,4]]]"),
                    ])
                    parts = _extract(resp.text)
                    quotas = []
                    for part in parts:
                        body_str = _gv(part, [2])
                        if not body_str: continue
                        body = json.loads(body_str)
                        # Format: [[[[None, model_id], ?, ?, [start_ts, end_ts], daily_limit, used], ...], '']
                        entries = body[0] if isinstance(body, list) and body else []
                        for entry in entries:
                            if not isinstance(entry, list) or len(entry) < 6: continue
                            quotas.append({
                                "model_type": entry[0][1] if entry[0] else None,
                                "model_hint": {4: "pro", 11: "flash"}.get(entry[0][1] if entry[0] else 0, "unknown"),
                                "daily_limit": entry[4],
                                "used": entry[5],
                                "remaining": entry[4] - entry[5] if entry[4] and entry[5] else None,
                            })
                    result["quota"] = quotas
                except Exception as e:
                    result["quota"] = []
                    result["quota_error"] = str(e)[:80]
                
                # ── Gems summary ──
                try:
                    await client.fetch_gems()
                    result["gem_count"] = len(client.gems)
                    if args.list_gems:
                        result["gems"] = [{"id": gid, "name": g.name}
                                          for gid, g in list(client.gems.items())[:args.limit]]
                except Exception:
                    # Stale-but-chat-valid credential: the lib path refuses, the
                    # browser-shaped LIST_BOTS still answers.
                    try:
                        _g = await _fetch_gems_rpc(sid, ts, language=result.get("language") or "en")
                        result["gem_count"] = len(_g)
                        if args.list_gems:
                            result["gems"] = _g[:args.limit]
                    except Exception:
                        result["gem_count"] = -1
                
                print(json.dumps(result, ensure_ascii=False))
            except Exception as e:
                fail("ACCOUNT_STATUS_FAILED", str(e))
            return

        # ── Standalone: --doctor ──
        # Answers "why did that call fail?" in one shot: which credential sources
        # exist, which of them actually opens /app (SNlM0e), which one would be
        # chosen, and whether the Gem/read paths work with it.
        if args.doctor:
            sources = []
            for src, c_sid, c_ts in _auth_candidates(args.browser or os.getenv("GEMINI_BROWSER")):
                entry = {"src": src, "sid": (c_sid or "")[:12], "has_ts": bool(c_ts),
                         "live": _probe_live(c_sid, c_ts)}
                if src.startswith("browser:"):
                    _, jar = _cookie_jar_for(c_sid)
                    entry["jar_cookies"] = len(jar)
                sources.append(entry)
            selected = next((s for s in sources if s["live"]), sources[0] if sources else None)
            out = {"ok": True, "auth_cache": str(AUTH_CACHE),
                   "auth_cache_exists": AUTH_CACHE.exists(),
                   "browser_flag": args.browser or os.getenv("GEMINI_BROWSER") or None,
                   "sources": sources,
                   "selected": selected["src"] if selected else None,
                   "live_available": bool(selected and selected["live"])}
            sid, ts = (resolve_auth(preferred_browser=args.browser or os.getenv("GEMINI_BROWSER"))
                       if sources else (None, None))
            if sid:
                try:
                    client = GeminiClient(secure_1psid=sid, secure_1psidts=ts)
                    await client.init()
                    try:
                        out["models"] = [str(m) for m in client.list_models()]
                    except Exception:
                        out["models"] = []
                    try:
                        out["account_status"] = client.account_status.name
                    except Exception:
                        out["account_status"] = None
                except Exception as e:
                    out["init_error"] = str(e)[:200]
                try:
                    gems = await _fetch_gems_rpc(sid, ts)
                    out["gems_via_rpc"] = len(gems)
                except Exception as e:
                    out["gems_via_rpc"] = -1
                    out["gems_rpc_error"] = str(e)[:160]
            if not out.get("live_available"):
                out["hint"] = ("No live session found. Sign in at gemini.google.com in a local "
                               "browser (then rerun with --init), or set GEMINI_SID/GEMINI_TS.")
            print(json.dumps(out, ensure_ascii=False))
            return


        # ── Standalone: --setup-search-gem ──
        if args.setup_search_gem:
            sid, ts = resolve_auth(preferred_browser=args.browser or os.getenv("GEMINI_BROWSER"))
            try:
                client = GeminiClient(secure_1psid=sid, secure_1psidts=ts)
                await client.init()
                if SEARCH_GEM_PROMPT.exists():
                    sys_prompt = SEARCH_GEM_PROMPT.read_text().strip()
                else:
                    sys_prompt = "You are a search grounding assistant. Return results as compact JSON."
                gem = await client.create_gem(name=SEARCH_GEM_NAME, prompt=sys_prompt,
                                               description=SEARCH_GEM_DESC)
                print(json.dumps({"ok": True, "action": "setup-search-gem",
                                  "id": gem.id, "name": gem.name}))
            except Exception as e:
                fail("SETUP_FAILED", str(e))
            return

        # ── Standalone: --create-gem ──
        if args.create_gem_name:
            if args.prompt_flag:
                sys_prompt = args.prompt_flag
                if not sys.stdin.isatty():
                    stdin_content = sys.stdin.read().strip()
                    if stdin_content:
                        sys_prompt = f"{sys_prompt}\n\n{stdin_content}"
            elif args.edit_sys_instr:
                sys_prompt = args.edit_sys_instr
            elif args.prompt:
                sys_prompt = " ".join(args.prompt)
            elif not sys.stdin.isatty():
                sys_prompt = sys.stdin.read().strip()
            else:
                fail("NO_PROMPT", "Provide system prompt via -p, -S, stdin, or positional args.")
            sid, ts = resolve_auth(preferred_browser=args.browser or os.getenv("GEMINI_BROWSER"))
            gem_id_out = None
            try:
                try:
                    client = GeminiClient(secure_1psid=sid, secure_1psidts=ts)
                    await client.init()
                    gem = await client.create_gem(
                        name=args.create_gem_name, prompt=sys_prompt,
                        description=f"Hermes task-specific Gem: {args.create_gem_name}")
                    gem_id_out = gem.id
                except Exception as lib_err:
                    # Fallback: browser-shaped RPC (lib headers get e,4 rejections)
                    rpc = await BrowserBatchRPC(sid, ts).init()
                    try:
                        payload = json.dumps([[args.create_gem_name,
                            f"Hermes task-specific Gem: {args.create_gem_name}",
                            sys_prompt, None, None, None, None, None, 0, None, 1,
                            None, None, None, []]])
                        text = await rpc.rpc("oMH3Zd", payload)
                        pl = _extract_rpc_payloads(text, "oMH3Zd")
                        gem_id_out = pl[0][0] if pl and pl[0] else None
                        if not gem_id_out:
                            raise RuntimeError(f"no gem id in response ({lib_err})")
                    finally:
                        await rpc.close()
                print(json.dumps({"ok": True, "action": "create-gem",
                                  "id": gem_id_out, "name": args.create_gem_name}))
            except Exception as e:
                fail("GEM_CREATE_FAILED", str(e))
            return

        # ── Standalone: --edit-gem ──
        if args.edit_gem_id:
            sid, ts = resolve_auth(preferred_browser=args.browser or os.getenv("GEMINI_BROWSER"))
            try:
                client = GeminiClient(secure_1psid=sid, secure_1psidts=ts)
                await client.init()
                await client.fetch_gems()
                g = client.gems.get(args.edit_gem_id)
                if not g:
                    for gid, gg in client.gems.items():
                        if gg.name.lower() == args.edit_gem_id.lower():
                            g = gg; break
                if not g:
                    fail("GEM_NOT_FOUND", f"Gem '{args.edit_gem_id}' not found. Use --list-gems.")
                new_name = args.edit_new_name or g.name
                new_desc = args.edit_new_desc if args.edit_new_desc is not None else (g.description or "")
                new_instr = args.edit_sys_instr if args.edit_sys_instr else None
                await client.update_gem(gem=g, name=new_name, description=new_desc,
                                      prompt=new_instr)
                print(json.dumps({"ok": True, "action": "edit-gem",
                                  "id": g.id, "name": new_name}))
            except Exception as e:
                fail("GEM_EDIT_FAILED", str(e))
            return

        # ── Standalone: --delete-gem ──
        if args.delete_gem_id:
            sid, ts = resolve_auth(preferred_browser=args.browser or os.getenv("GEMINI_BROWSER"))
            try:
                try:
                    client = GeminiClient(secure_1psid=sid, secure_1psidts=ts)
                    await client.init()
                    await client.delete_gem(args.delete_gem_id)
                except Exception:
                    # Fallback: browser-shaped RPC DELETE_BOT UXcSJb
                    rpc = await BrowserBatchRPC(sid, ts).init()
                    try:
                        await rpc.rpc("UXcSJb", json.dumps([args.delete_gem_id, [0, None, 0]]))
                    finally:
                        await rpc.close()
                print(json.dumps({"ok": True, "action": "delete-gem", "id": args.delete_gem_id}))
            except Exception as e:
                fail("GEM_DELETE_FAILED", str(e))
            return

        # ── Standalone: --clear ──
        if args.clear_conv:
            if not args.conversation:
                fail("NO_CONV", "Use --clear with -c <file>.")
            fp = Path(args.conversation)
            existed = fp.exists()
            if existed: fp.unlink()
            print(json.dumps({"ok": True, "action": "clear", "file": str(fp),
                              "was_present": existed}))
            return

        # ── Standalone: --list-chats ──
        if args.list_chats:
            sid, ts = resolve_auth(preferred_browser=args.browser or os.getenv("GEMINI_BROWSER"))
            try:
                # gemini_webapi list_chats returns empty (missing headers); use browser-shaped RPC
                rpc = await BrowserBatchRPC(sid, ts).init()
                try:
                    text = await rpc.rpc("MaZiqc", [args.limit, None, [0, None, 1]])
                    payloads = _extract_rpc_payloads(text, "MaZiqc")
                    chats = []
                    for pb in payloads:
                        # shape: [null, "<pageToken>", [[cid, title, ...], ...]] — chats at pb[2]
                        if not (isinstance(pb, list) and len(pb) > 2 and isinstance(pb[2], list)):
                            continue
                        for cd in pb[2]:
                            if isinstance(cd, list) and cd and isinstance(cd[0], str) and cd[0].startswith("c_"):
                                cid, title = cd[0], (cd[1] if isinstance(cd[1], str) else "") or ""
                                if not any(x["cid"] == cid for x in chats):
                                    chats.append({"cid": cid, "title": title})
                    print(json.dumps({"ok": True, "chats": chats[:args.limit], "total": len(chats)}))
                finally:
                    await rpc.close()
            except Exception as e:
                fail("LIST_CHATS_FAILED", str(e))
            return

        # ── Standalone: --read-chat ──
        if args.read_chat_id:
            sid, ts = resolve_auth(preferred_browser=args.browser or os.getenv("GEMINI_BROWSER"))
            try:
                # lib read_chat first; on failure fall back to browser-shaped hNvQHb RPC
                try:
                    client = GeminiClient(secure_1psid=sid, secure_1psidts=ts)
                    await client.init()
                    history = await client.read_chat(args.read_chat_id, limit=args.limit)
                    if history and history.turns:
                        # The library returns turns NEWEST-FIRST; emit conversation
                        # order (oldest-first) so a reader can follow it, and say so.
                        turns = [{"role": t.role, "text": t.text} for t in reversed(history.turns)]
                        print(json.dumps({"ok": True, "chat": {
                            "cid": history.cid, "turns": turns, "total": len(turns),
                            "order": "oldest-first"}}, ensure_ascii=False))
                        return
                    raise RuntimeError("lib read_chat empty — falling back")
                except (SystemExit,):
                    raise
                except Exception:
                    pass
                rpc = await BrowserBatchRPC(sid, ts).init()
                try:
                    text = await rpc.rpc("hNvQHb", [args.read_chat_id, args.limit, None, 1, [1], [4], None, 1])
                    payloads = _extract_rpc_payloads(text, "hNvQHb")
                    pairs = []          # one (user, model) pair per conv turn
                    for pb in payloads:
                        for conv_turn in (pb[0] if pb and isinstance(pb[0], list) else []):
                            if not isinstance(conv_turn, list): continue
                            user_text = ""
                            try: user_text = conv_turn[2][0][0] or ""
                            except Exception: pass
                            model_text = ""
                            try: model_text = conv_turn[3][0][0][1][0] or ""
                            except Exception: pass
                            pair = []
                            if user_text: pair.append({"role": "user", "text": user_text})
                            if model_text: pair.append({"role": "model", "text": model_text[:4000]})
                            if pair: pairs.append(pair)
                    # hNvQHb serves newest-first (same as the library's parser), so
                    # reverse whole TURN pairs — reversing the flattened list would
                    # swap the user/model order inside every turn.
                    turns = [t for pair in reversed(pairs) for t in pair]
                    if turns:
                        print(json.dumps({"ok": True, "chat": {
                            "cid": args.read_chat_id, "turns": len(turns),
                            "order": "oldest-first"}}))
                        for t in turns:
                            print(json.dumps(t, ensure_ascii=False))
                    else:
                        fail("CHAT_NOT_FOUND", f"Chat {args.read_chat_id} not found or empty.")
                finally:
                    await rpc.close()
            except Exception as e:
                fail("READ_CHAT_FAILED", str(e))
            return

        # ── Standalone: --delete-chat ──
        if args.delete_chat_id:
            sid, ts = resolve_auth(preferred_browser=args.browser or os.getenv("GEMINI_BROWSER"))
            try:
                client = GeminiClient(secure_1psid=sid, secure_1psidts=ts)
                await client.init()
                await client.delete_chat(args.delete_chat_id)
                print(json.dumps({"ok": True, "action": "delete-chat", "cid": args.delete_chat_id}))
            except Exception as e:
                fail("DELETE_CHAT_FAILED", str(e))
            return

        # ── Standalone: --fetch-latest ──
        if args.fetch_latest_id:
            sid, ts = resolve_auth(preferred_browser=args.browser or os.getenv("GEMINI_BROWSER"))
            try:
                client = GeminiClient(secure_1psid=sid, secure_1psidts=ts)
                await client.init()
                out = await client.fetch_latest_chat_response(args.fetch_latest_id)
                if out:
                    data = {"ok": True, "cid": args.fetch_latest_id, "text": out.text}
                    if out.thoughts: data["thoughts"] = out.thoughts
                    imgs = [{"url": i.url, "alt": i.alt or ""} for i in (out.images or [])]
                    if imgs: data["images"] = imgs
                    vids = [{"url": v.url, "title": v.title or ""} for v in (out.videos or [])]
                    if vids: data["videos"] = vids
                    print(json.dumps(data, ensure_ascii=False))
                else:
                    fail("FETCH_LATEST_FAILED", f"No response for {args.fetch_latest_id}")
            except Exception as e:
                fail("FETCH_LATEST_FAILED", str(e))
            return

        # ── Standalone: --deep-research-status <CID> ──
        # There is NO research-task entity to look up: Gemini keeps the task id at
        # the literal "agency-placeholder-task-id" from start to finish and both
        # task-listing RPCs stay empty (documented in gemini_webapi's
        # wait_for_deep_research). The only real signal is whether the report has
        # landed in the conversation, so this takes the CHAT id (the "c" field
        # printed by --deep-research) and reports that.
        if args.deep_research_status_id:
            cid = args.deep_research_status_id
            sid, ts = resolve_auth(preferred_browser=args.browser or os.getenv("GEMINI_BROWSER"))
            try:
                client = GeminiClient(secure_1psid=sid, secure_1psidts=ts)
                await client.init()
                out = await client.fetch_latest_chat_response(cid)
                if out is None:
                    fail("RESEARCH_STATUS_FAILED", f"No turn found for chat {cid}")
                doc = getattr(out, "deep_research_document", None)
                text = out.text or ""
                done = bool(doc) or len(text) > 400
                res = {"ok": True, "cid": cid,
                       "status": "completed" if done else "pending",
                       "chars": len(text), "has_document": bool(doc)}
                if done and text:
                    op = Path(args.output) if args.output else Path(
                        f"/tmp/gemini-dr-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md")
                    op.write_text(text, encoding="utf-8")
                    res["f"] = str(op)
                print(json.dumps(res, ensure_ascii=False))
            except (SystemExit,):
                raise
            except Exception as e:
                fail("RESEARCH_STATUS_FAILED", str(e))
            return

        # ── Resolve URL / Gem ID ──
        standalone = args.list_models or args.list_gems
        if standalone and not args.url:
            args.url = "setup"

        # If no Gem URL given but positional words exist, they are the prompt, not a URL.
        # (parse_intermixed_args dumps all positionals into args.url first.)
        if not args.gem_id and args.url and args.prompt:
            pass  # url + separate prompt words — keep as-is
        elif not args.gem_id and args.url and not args.prompt_flag \
                and not (args.url.startswith(("http://", "https://"))
                         or re.fullmatch(r"[A-Za-z0-9_-]{5,20}", args.url or "")):
            # multi-word or sentence-like positional = prompt text
            args.prompt = args.url.split() + list(args.prompt)
            args.url = None

        if args.gem_id:
            gem_id = args.gem_id
        elif args.list_models or args.list_gems or args.list_chats:
            gem_id = "dummy"
        elif args.url:
            try: gem_id = extract_gem_id(args.url)
            except ValueError as e: fail("BAD_URL", str(e))
        else:
            gem_id = None  # direct chat, no Gem required

        # ── Build prompt ──
        if args.image_prompt:
            prompt = f"Generate an image: {args.image_prompt}"
            args.image_gen = True
        elif args.prompt_flag:
            prompt = args.prompt_flag
            if not sys.stdin.isatty():
                stdin_content = sys.stdin.read().strip()
                if stdin_content:
                    prompt = f"{prompt}\n\n{stdin_content}"
        elif args.prompt:
            prompt = " ".join(args.prompt)
        elif args.list_models or args.list_gems or args.gem_info:
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

        # ── Handle --gem-info ──
        if args.gem_info:
            if not gem_id:
                fail("GEM_REQUIRED", "A Gem URL, ID, or -g <id> is required for --gem-info.")
            sid, ts = resolve_auth(preferred_browser=args.browser or os.getenv("GEMINI_BROWSER"))
            found = None
            try:
                client = GeminiClient(secure_1psid=sid, secure_1psidts=ts)
                await client.init()
                await client.fetch_gems()
                g = client.gems.get(gem_id)
                if g:
                    found = {"id": gem_id, "name": g.name, "description": g.description or "",
                             "type": "system" if g.predefined else "user"}
            except Exception:
                found = None
            if found is None:
                try:
                    for g in await _fetch_gems_rpc(sid, ts):
                        if g["id"] == gem_id:
                            found = g
                            break
                except Exception:
                    found = None
            if found:
                print(json.dumps({"ok": True, "gem": found}, ensure_ascii=False))
            else:
                print(json.dumps({"ok": True, "gem": {"id": gem_id, "name": "",
                    "description": "", "type": "external",
                    "note": "Shared Gem — not in library"}}))
            return

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
                print(json.dumps({"ok": True, "models": [str(m) for m in models]}))
            except Exception as e: fail("LIST_FAILED", str(e))
            return

        if args.list_gems:
            gems = []
            try:
                await self.client.fetch_gems()
                gems = [{"id": gid, "name": g.name, "description": g.description or "",
                         "type": "system" if g.predefined else "user"}
                        for gid, g in self.client.gems.items()]
            except Exception as lib_err:
                # The lib path gates on _check_account_status(), which a stale-but-
                # chat-valid credential fails; LIST_BOTS needs a live jar only.
                try:
                    gems = await _fetch_gems_rpc(sid, ts)
                except Exception as rpc_err:
                    fail("LIST_FAILED", "{} | browser RPC: {}".format(lib_err, rpc_err))
            if args.limit:
                gems = gems[:args.limit]
            print(json.dumps({"ok": True, "gems": gems, "n": len(gems)}, ensure_ascii=False))
            return

        # ── Model ──
        model = None
        if args.model or args.thinking:
            model = resolve_model_enum(args.model, args.thinking) if args.thinking \
                    else resolve_model_string(self.client, args.model)

        # ── Gem name ──
        # Also decides fail-fast behaviour: an id that is neither in the library nor
        # a recognisable Gem id makes the server accept the request and then never
        # answer, which used to burn the whole timeout with no explanation.
        gem_name = ""
        gem_unresolved = False
        if gem_id and gem_id != "dummy":
            try:
                await self.client.fetch_gems()
                g = self.client.gems.get(gem_id)
                if g: gem_name = g.name
            except Exception:
                if not gem_name:
                    try:
                        for _g in await _fetch_gems_rpc(sid, ts):
                            if _g["id"] == gem_id:
                                gem_name = _g["name"] or ""
                                break
                    except Exception:
                        pass
            if not gem_name and not re.fullmatch(r"[A-Za-z0-9_-]{5,40}", gem_id or ""):
                fail("GEM_NOT_FOUND", f"'{gem_id}' is not a valid Gem id or URL.")
            gem_unresolved = not gem_name

        if not self.raw_mode:
            model_label = friendly_model_label(model)
            parts = []
            if gem_name:
                parts.append(f"gem={gem_name}")
            elif gem_id and gem_id != "dummy":
                parts.append(f"gem={gem_id}")
            parts.append(f"model={model_label}")
            if args.deep_research: parts.append("deep-research")
            if args.image_gen: parts.append("img-gen")
            if args.conversation: parts.append("multi-turn")
            self.log(", ".join(parts))

        # ── Conversation ──
        conv_state = None; chat_metadata = None
        if args.resume_session:
            conv_state = {"cid": args.resume_session, "metadata": [args.resume_session, ""],
                          "turns": 0, "created": datetime.now(timezone.utc).isoformat()}
            chat_metadata = conv_state["metadata"]
            self.log(f"Resuming session {args.resume_session}")
        elif args.conversation:
            if not args.new_conv:
                conv_state = load_conv(args.conversation)
                if conv_state: chat_metadata = conv_state.get("metadata")
            if conv_state is None:
                conv_state = {"cid": None, "metadata": None, "turns": 0,
                              "created": datetime.now(timezone.utc).isoformat()}

        # ── Image gen force flash ──
        if args.image_gen and not model:
            model = "gemini-3-flash"

        # ── Files ──
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
            self.log(f"Deep research: timeout auto-extended to {actual_timeout}s")

        # An id that resolves to no library Gem is either a shared Gem or a typo —
        # for a typo the server accepts the request and simply never answers, so cap
        # the wait instead of burning the full timeout with no explanation.
        if gem_unresolved and not args.deep_research and actual_timeout > 60:
            actual_timeout = 60
            self.log("Gem id not found in your library (shared Gem?): probing with a 60s cap — raise with -t")

        # ── Generate with retry ──
        # An unresolvable Gem id never becomes resolvable on retry, so retrying it
        # just multiplies the wait; one capped attempt gives a fast, clear failure.
        max_attempts = 1 if (args.no_retry or gem_unresolved) else 3
        for attempt in range(max_attempts):
            if attempt > 0: self.log(f"Retry {attempt+1}/{max_attempts}...")
            try:
                if args.deep_research:
                    self.log("Creating research plan...")
                    # gemini_webapi's DR entry points have no on_status hook (progress
                    # is not reported at all while a task runs); passing it blindly
                    # raised TypeError and silently demoted every DR run to a plain
                    # generate_content call. Probe the signature instead.
                    _dr_status = (lambda s: (self.log(f"  [{getattr(s, 'state', '') or '...'}]")
                                             if not self.raw_mode and s else None))
                    try:
                        # Try high-level deep_research first (handles plan+start+wait internally)
                        self.log("Starting deep research...")
                        _dr_kwargs = {"poll_interval": 15.0, "timeout": actual_timeout}
                        if _supports(self.client.deep_research, "on_status"):
                            _dr_kwargs["on_status"] = _dr_status
                        result = await asyncio.wait_for(
                            self.client.deep_research(prompt, **_dr_kwargs),
                            timeout=actual_timeout)
                        response = result.final_output
                    except Exception as plan_err:
                        plan_msg = str(plan_err)
                        if "not eligible" in plan_msg.lower() or "rejected" in plan_msg.lower():
                            fail("DEEP_RESEARCH_REJECTED",
                                 f"Account not eligible for deep research. {plan_msg}",
                                 {"retry": False})
                        # Fallback: manual plan-based flow
                        self.log(f"High-level DR failed, trying manual plan: {plan_msg}")
                        try:
                            plan = await asyncio.wait_for(
                                self.client.create_deep_research_plan(prompt, model=model), timeout=120)
                            self.log(f"Plan: {plan.title or 'Research'} — starting...")
                            await asyncio.wait_for(
                                self.client.start_deep_research(
                                    plan, confirm_prompt="Proceed with this plan without modifications."),
                                timeout=120)
                            self.log("Research in progress...")
                            _wfw_kwargs = {"poll_interval": 15.0, "timeout": actual_timeout}
                            if _supports(self.client.wait_for_deep_research, "on_status"):
                                _wfw_kwargs["on_status"] = _dr_status
                            result = await asyncio.wait_for(
                                self.client.wait_for_deep_research(plan, **_wfw_kwargs),
                                timeout=actual_timeout)
                            response = result.final_output
                        except Exception as manual_err:
                            # Last resort: generate_content with deep_research=True
                            self.log(f"Manual plan failed, trying direct mode: {manual_err}")
                            kwargs = {"prompt": prompt, "deep_research": True}
                            if model: kwargs["model"] = model
                            response = await asyncio.wait_for(
                                self.client.generate_content(**kwargs), timeout=actual_timeout)
                else:
                    kwargs = {"prompt": prompt}
                    if all_files: kwargs["files"] = all_files
                    if chat_metadata: kwargs["chat"] = ChatRef(chat_metadata)
                    if model: kwargs["model"] = model
                    if gem_id and gem_id != "dummy":
                        kwargs["gem"] = gem_id
                    if args.temporary:
                        kwargs["temporary"] = True
                    if args.stream:
                        # Streaming mode — print only new tokens as they arrive
                        full_text = []
                        if not self.raw_mode:
                            sys.stderr.write("[streaming] ")
                            sys.stderr.flush()
                        async for chunk in self.client.generate_content_stream(**kwargs):
                            if hasattr(chunk, 'text') and chunk.text:
                                text = chunk.text
                                # Chunks are cumulative — only print new portion
                                if full_text:
                                    prev = full_text[-1]
                                    if text.startswith(prev) and len(text) > len(prev):
                                        new = text[len(prev):]
                                        full_text.append(text)
                                        if not self.raw_mode:
                                            sys.stderr.write(new)
                                            sys.stderr.flush()
                                else:
                                    full_text.append(text)
                                    if not self.raw_mode:
                                        sys.stderr.write(text)
                                        sys.stderr.flush()
                        if not self.raw_mode:
                            sys.stderr.write("\n")
                        # Use the last (most complete) chunk as response text
                        final_text = full_text[-1] if full_text else ""
                        class StreamResponse:
                            def __init__(self, text):
                                self.text = text
                                self.images = []
                                self.videos = []
                                self.media = []
                                self.thoughts = None
                                self.metadata = None
                        response = StreamResponse(final_text)
                    else:
                        response = await asyncio.wait_for(
                            self.client.generate_content(**kwargs), timeout=actual_timeout)
            except asyncio.TimeoutError:
                if attempt == max_attempts - 1:
                    # Playwright fallback
                    if gem_id and gem_id != "dummy" and not os.environ.get("GEMCLI_NO_PW"):
                        self.log("API timed out, trying browser fallback (gem-pw)...")
                        pwj = self._pw_fallback(gem_id, prompt, args.output)
                        if pwj:
                            self.log(f"Browser fallback OK: {pwj.get('s',0)} chars")
                            print(json.dumps(pwj)); return
                    _hint = (" Gem id was not found in your library — the id may be wrong, or "
                             "it is a shared Gem you cannot access.") if gem_unresolved else ""
                    fail("TIMEOUT", f"Timed out after {actual_timeout}s.{_hint}",
                         {"timeout_s": actual_timeout, "retry": False})
                continue
            except Exception as e:
                err_msg = str(e); kind = error_kind(err_msg)
                if kind == "AUTH_EXPIRED":
                    if attempt == max_attempts - 1:
                        fail("AUTH_EXPIRED", err_msg,
                             {"auth_src": _LAST_AUTH_SRC, "retry": False})
                    self.log(f"Auth expired (src={_LAST_AUTH_SRC}), trying next distinct source...")
                    new_sid, new_ts = _next_auth(sid, args.browser or os.getenv("GEMINI_BROWSER"))
                    # Never retry with the identical pair that just failed: that is a
                    # guaranteed second failure dressed up as recovery. Take the next
                    # DISTINCT credential, or stop with an actionable message.
                    if new_sid:
                        sid, ts = new_sid, new_ts
                        self.client = GeminiClient(secure_1psid=sid, secure_1psidts=ts)
                        await self.client.init()
                        # Converge the cache onto the credential that actually works, so
                        # later runs skip the walk instead of starting from a dead source.
                        try: _save_auth_cache(sid, ts)
                        except Exception: pass
                        continue
                    fail("AUTH_EXPIRED",
                         "session is dead and no other source holds a distinct credential. "
                         "Sign in at gemini.google.com in a local browser, then retry. "
                         f"(last source used: {_LAST_AUTH_SRC})",
                         {"auth_src": _LAST_AUTH_SRC, "recovered": False, "retry": False})
                if kind == "RATE_LIMIT":
                    wait = 30 if attempt == 0 else 60
                    if attempt == max_attempts - 1:
                        fail("RATE_LIMIT", err_msg, {"retry_after_s": wait, "retry": True})
                    self.log(f"Rate limited, waiting {wait}s...")
                    await asyncio.sleep(wait); continue
                if attempt == max_attempts - 1:
                    if gem_id and gem_id != "dummy" and not os.environ.get("GEMCLI_NO_PW"):
                        self.log("API failed, trying browser fallback (gem-pw)...")
                        pwj = self._pw_fallback(gem_id, prompt, args.output)
                        if pwj:
                            self.log(f"Browser fallback OK: {pwj.get('s',0)} chars")
                            print(json.dumps(pwj)); return
                    fail(kind, err_msg)
                continue

            # Success
            text = response.text
            new_meta = list(response.metadata) if response.metadata else None

            # Images / Videos / Media / Thoughts
            images_out, videos_out, media_out = [], [], []
            thoughts_text = None
            try:
                for img in response.images:
                    images_out.append({"url": img.url, "alt": img.alt or ""})
            except Exception: pass
            try:
                for v in response.videos:
                    videos_out.append({"url": v.url, "title": getattr(v, 'title', '') or ""})
            except Exception: pass
            try:
                for m in response.media:
                    media_out.append({"url": m.url, "title": getattr(m, 'title', '') or ""})
            except Exception: pass
            try:
                thoughts_text = response.thoughts
            except Exception: pass
            # include thoughts for thinking models even if --show-thoughts not set (stored in json)
            has_thoughts = bool(thoughts_text)

            # Save generated images to disk
            if args.save_images and images_out:
                import urllib.request as _ur
                sd = Path(args.save_images)
                sd.mkdir(parents=True, exist_ok=True)
                saved = []
                cookie_str = f"__Secure-1PSID={sid}; __Secure-1PSIDTS={ts}"
                for i, img in enumerate(images_out):
                    try:
                        fp = sd / f"gemini_img_{i}.png"
                        req = _ur.Request(img["url"], headers={"Cookie": cookie_str})
                        with _ur.urlopen(req, timeout=30) as resp:
                            fp.write_bytes(resp.read())
                        saved.append(str(fp))
                    except Exception as dl_err:
                        self.log(f"Image {i} download failed: {dl_err}")
                if saved:
                    self.log(f"Saved {len(saved)} image(s) to {sd}")

            # Save generated videos to disk
            if args.save_videos:
                if videos_out:
                    import urllib.request as _ur
                    sd = Path(args.save_videos)
                    sd.mkdir(parents=True, exist_ok=True)
                    saved = []
                    cookie_str = f"__Secure-1PSID={sid}; __Secure-1PSIDTS={ts}"
                    for i, v in enumerate(videos_out):
                        try:
                            fp = sd / f"gemini_video_{i}.mp4"
                            req = _ur.Request(v["url"], headers={"Cookie": cookie_str})
                            with _ur.urlopen(req, timeout=60) as resp:
                                data = resp.read()
                                # handle 206 polling placeholder (webapi GeneratedVideo retries)
                                if len(data) < 100 and b"206" in data[:10]:
                                    self.log(f"Video {i} still generating (206), skipping")
                                    continue
                                fp.write_bytes(data)
                            saved.append(str(fp))
                        except Exception as dl_err:
                            self.log(f"Video {i} download failed: {dl_err}")
                    if saved:
                        self.log(f"Saved {len(saved)} video(s) to {sd}")
                else:
                    self.log("No videos in response — Veo quota may be limited (3/day Pro, 5/day Ultra) or prompt didn't trigger video. Try --img for images.")

            # Save generated media (audio/video) to disk
            if args.save_media:
                if media_out:
                    import urllib.request as _ur
                    sd = Path(args.save_media)
                    sd.mkdir(parents=True, exist_ok=True)
                    saved = []
                    cookie_str = f"__Secure-1PSID={sid}; __Secure-1PSIDTS={ts}"
                    for i, m in enumerate(media_out):
                        try:
                            fp = sd / f"gemini_media_{i}.mp4"
                            req = _ur.Request(m["url"], headers={"Cookie": cookie_str})
                            with _ur.urlopen(req, timeout=60) as resp:
                                fp.write_bytes(resp.read())
                            saved.append(str(fp))
                        except Exception as dl_err:
                            self.log(f"Media {i} download failed: {dl_err}")
                    if saved:
                        self.log(f"Saved {len(saved)} media file(s) to {sd}")
                else:
                    self.log("No media in response — try --save-videos for Veo or --save-images for Imagen.")

            # Update conversation (skip if temporary)
            if args.temporary:
                self.log("Temporary chat — not saving to history")
            elif args.conversation and new_meta:
                conv_state["cid"] = new_meta[0]
                conv_state["metadata"] = new_meta
                conv_state["turns"] += 1
                save_conv(args.conversation, conv_state)

            # Output file
            ext = ".json" if args.json_out else ".md"
            out_path = Path(args.output) if args.output else \
                       Path(f"/tmp/gemini-{datetime.now().strftime('%Y%m%d-%H%M%S')}{ext}")

            if args.json_out:
                payload = {"ok": True, "text": text, "model": model_label}
                if images_out: payload["images"] = images_out
                if videos_out: payload["videos"] = videos_out
                if media_out: payload["media"] = media_out
                if has_thoughts: payload["thoughts"] = thoughts_text
                else:
                    if args.show_thoughts:
                        self.log("No thoughts returned — model may not provide thinking traces for this prompt/tier (deep research often does).")
                if args.temporary: payload["temporary"] = True
                if conv_state: payload["conversation"] = conv_state
                out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            else:
                out_text = text
                if has_thoughts and args.show_thoughts:
                    out_text += "\n\n## Thoughts\n\n" + thoughts_text
                elif args.show_thoughts and not has_thoughts:
                    self.log("No thoughts returned — model may not provide thinking traces for this prompt/tier (deep research often does).")
                if images_out:
                    out_text += "\n\n## Images\n\n"
                    for i, img in enumerate(images_out):
                        out_text += f"{i+1}. ![{img['alt']}]({img['url']})\n"
                if videos_out:
                    out_text += "\n\n## Videos\n\n"
                    for i, v in enumerate(videos_out):
                        out_text += f"{i+1}. [Video {v['title']}]({v['url']})\n"
                if media_out:
                    out_text += "\n\n## Media\n\n"
                    for i, m in enumerate(media_out):
                        out_text += f"{i+1}. [Media]({m['url']})\n"
                out_path.write_text(out_text, encoding="utf-8")

            # Extract Canvas artifact if requested
            if args.extract_canvas:
                try:
                    canvas_blocks = [cb for cb in self.parse_code_blocks(text) if cb["lang"] in ("html","xml","svg","canvas")]
                    # fallback: if text contains <!DOCTYPE or <html, dump whole text
                    canvas_content = None
                    if canvas_blocks:
                        canvas_content = canvas_blocks[0]["code"]
                    elif "<html" in text.lower() or "<!doctype" in text.lower():
                        canvas_content = text
                    if canvas_content:
                        Path(args.extract_canvas).write_text(canvas_content, encoding="utf-8")
                        self.log(f"Canvas artifact saved to {args.extract_canvas}")
                    else:
                        self.log("No Canvas/HTML block found for --extract-canvas")
                except Exception as ce:
                    self.log(f"Canvas extract failed: {ce}")

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

            self.pointer(out_path, conv_state if args.conversation and not args.temporary else None,
                         images_out, videos_out, media_out, len(code_blocks), has_thoughts,
                         model_label, gem_name, args.deep_research, args.temporary)
            return

# ── Entry point ──────────────────────────────────────────────

def main():
    asyncio.run(GeminiCLI().run())

if __name__ == "__main__":
    main()
