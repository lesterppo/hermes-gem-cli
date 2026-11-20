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
  gem-cli <gem-url> --show-thoughts -m flash --thinking extended "..."  # include chain of thought
  gem-cli <gem-url> --img "a cat flying"                # image generation
  gem-cli --video "a puppy in a sunny field"             # video generation (Veo)
  gem-cli --music "upbeat electronic track"              # music generation (Lyria)
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
import contextvars
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
    "BASIC_LITE": "lite+standard",
    "PLUS_LITE": "lite+plus",
    "ADVANCED_LITE": "lite+extended",
    "BASIC_THINKING": "thinking+standard",
    "PLUS_THINKING": "thinking+plus",
    "ADVANCED_THINKING": "thinking+extended",
    # String model IDs → friendly name
    "gemini-3-flash": "flash",
    "gemini-3-pro": "pro",
    "gemini-3-flash-lite": "lite",
    "gemini-3-flash-thinking": "thinking",
    "3.1 Flash-Lite": "lite",
    # Live web names (2026-11-14 picker): the WebAPI registry lists only
    # the account default, labelled "auto" in pointers.
    "gemini-自動": "auto",
    "4 Hydrogen": "hydrogen",
    "3.5 Flash-Lite": "lite",
}

def friendly_model_label(model) -> str:
    """Normalize model to consistent friendly label: 'flash', 'pro+extended', etc."""
    if hasattr(model, 'name'):
        return _MODEL_LABEL_MAP.get(model.name, model.name.lower())
    if isinstance(model, str):
        return _MODEL_LABEL_MAP.get(model, model.lower())
    return str(model)


def _supports(fn, param: str) -> bool:
    """True when a library callable accepts `param` (lib versions drift).

    gemini-webapi 2.1.1's wait_for_deep_research has no on_status — passing
    it raises TypeError and kills the whole DR run (live 2026-11-14).
    """
    try:
        import inspect
        return param in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def _read_stdin_if_piped(timeout: float = 2.0) -> str:
    """Read piped stdin without hanging forever on an open pipe.

    The CLI appends piped stdin to -p (and accepts a bare piped prompt),
    but a plain sys.stdin.read() blocks until EOF. An agent harness that
    spawns the CLI with an open stdin pipe hangs forever with no output
    and no timeout (live-verified 2026-11-14: direct chat with -p hung
    with zero output; stdin=/dev/null answered in seconds). Wait briefly
    for the first byte; if nothing arrives, treat stdin as empty. A real
    pipe (cat/echo) has its data buffered immediately, so it is still
    consumed; a tty is never read here.
    """
    if sys.stdin.isatty():
        return ""
    try:
        import select
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        if not ready:
            return ""
    except Exception:
        return ""
    try:
        return sys.stdin.read().strip()
    except Exception:
        return ""


def _dr_is_stub(text) -> bool:
    """True when a deep-research result is a stub, not a report.

    Live-verified 2026-11-14: with plan creation failing, the fallback
    generate returns a plan-acknowledgement sentence (~80 chars) instead
    of a report. Reporting ok:true for that misleads agents into thinking
    the research ran. Deep-research reports are long; anything under 200
    chars is a stub.
    """
    if not text:
        return True
    return len(str(text).strip()) < 200


def _dr_report_text(obj) -> str | None:
    """Best report text for a deep-research result.

    Live 2026-11-16 (chat c_5e9dd188fd4c5e5b): Gemini delivers the finished
    report as an immersive document attached to the turn; the reply text is
    only a short notice ("I've completed your research", 91 chars). The run
    path used to feed that notice to the DR_FAILED stub guard and fail a run
    that had actually produced a report — a CLI extraction miss, not a
    server failure. Prefer the document: gemini-webapi 2.1.1's
    DeepResearchResult carries it as .document (and its .text already prefers
    it); a ModelOutput carries it as .deep_research_document. Reply text is
    only the fallback for a run that produced no document.
    """
    try:
        doc = getattr(obj, "document", None)  # DeepResearchResult
        if doc is not None and getattr(doc, "content", ""):
            return doc.content
    except Exception:
        pass
    try:
        doc = getattr(obj, "deep_research_document", None)  # ModelOutput
        if doc is not None and getattr(doc, "content", ""):
            return doc.content
    except Exception:
        pass
    try:
        t = getattr(obj, "text", None)
        if isinstance(t, str) and t.strip():
            return t
    except Exception:
        pass
    return None


def _extract_thoughts(response) -> str | None:
    """Chain of thought for a generate response, or None.

    gemini-webapi 2.1.1 parses the model's thinking trace from the raw
    candidate (`candidate_data[37][0][0]`) onto
    `ModelOutput.thoughts` (chosen candidate's `.thoughts`). It is only
    populated for thinking models/tiers — a plain flash/pro reply, or a
    turn whose trace the server withheld, returns None, which is not an
    error. Mirrors gemini.py's `response.thoughts` read, with a
    candidates-list fallback in case the `.thoughts` property itself
    raises (e.g. an empty candidates list on a malformed turn).
    """
    try:
        t = getattr(response, "thoughts", None)
        if isinstance(t, str) and t.strip():
            return t
    except Exception:
        pass
    try:
        cands = getattr(response, "candidates", None) or []
        chosen = getattr(response, "chosen", 0) or 0
        if 0 <= chosen < len(cands):
            t = getattr(cands[chosen], "thoughts", None)
            if isinstance(t, str) and t.strip():
                return t
    except Exception:
        pass
    return None


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
    # Web picker names (2026-11-14): "Quick answers" is 3.5 Flash-Lite.
    "flash-lite": "LITE", "3.5 flash-lite": "LITE", "3.5-flash-lite": "LITE",
}

# Web picker names for "Everyday help" (4 Hydrogen). Hydrogen is NOT a
# registered WebAPI model name — the registry lists only the account
# default (auto) — so these resolve to that registered default instead
# of failing. See resolve_model_string.
_HYDROGEN_NAMES = {"hydrogen", "4 hydrogen", "hydrogen medium"}

_THINKING_ALIASES = {
    "standard": "BASIC", "basic": "BASIC",
    "plus": "PLUS",
    "extended": "ADVANCED", "advanced": "ADVANCED",
    # Web picker "Thinking level" names (2026-11-14).
    "low": "BASIC", "medium": "PLUS", "high": "ADVANCED",
}

def resolve_model_enum(model_str: str | None, thinking: str | None = None):
    """Resolve shorthand to Model enum (when thinking tier specified)."""
    if not model_str:
        return None
    tier = _THINKING_ALIASES.get(thinking.lower().strip(), thinking.upper()) if thinking else None
    mtype = _MODEL_ALIASES.get(model_str.lower().strip())
    if mtype is None:
        return model_str
    if mtype == "LITE" and not tier:
        # The old hardcoded "gemini-3-flash-lite" string is no longer a
        # registered model name; the enum lets the library map it onto the
        # account's equivalent registered model.
        try:
            return Model.BASIC_LITE
        except AttributeError:
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
        hit = next((v for k, v in name_map.items()
                    if "flash" in k and "lite" not in k and "thinking" not in k), None)
        if hit:
            return hit
    if q in ("pro",):
        hit = next((v for k, v in name_map.items()
                    if "pro" in k and "thinking" not in k), None)
        if hit:
            return hit
    if q in ("lite",):
        hit = next((v for k, v in name_map.items()
                    if "lite" in k), None)
        if hit:
            return hit
    # Web picker name for "Everyday help" (4 Hydrogen): not a registered
    # WebAPI model name. Accept it and use the registry's default (the
    # account's auto model — the same default a bare run uses), instead of
    # passing an unknown name to generate_content.
    if q.replace("-", " ") in _HYDROGEN_NAMES and len(name_map) == 1:
        return next(iter(name_map.values()))
    # No live-registry match: the server no longer registers the old
    # gemini-3-* names (live-verified 2026-11-14: the registry lists only
    # "gemini-自動"). Fall back to the Model enum — the library maps it
    # onto the account's equivalent registered model. Returning the raw
    # string fails in generate_content with "Unknown model name".
    mtype = _MODEL_ALIASES.get(q)
    if mtype is not None:
        try:
            return Model[f"BASIC_{mtype}"]
        except (AttributeError, KeyError):
            pass
    return model_str


def resolve_model(client, model_str: str | None, thinking: str | None = None):
    """Resolve -m/--thinking to (model, extended_thinking) for generate_content.

    `model` is whatever generate_content accepts (a Model enum, a registered
    name string, or None). `extended_thinking` is True only when the caller
    asked for the high/extended tier on a model that has no Model-enum
    thinking variant.

    Why this exists (2026-11-16): `-m hydrogen --thinking high` failed with
    GEN_FAILED "Unknown model name: 'hydrogen'". The thinking path went
    through resolve_model_enum, which returns the raw string for any name
    outside _MODEL_ALIASES — and Hydrogen is a web-picker name, not a
    registered WebAPI model, so it has no enum variant (there is no
    ADVANCED_HYDROGEN in gemini-webapi's Model). Fix: Hydrogen resolves to
    the registry default (the auto model) via resolve_model_string, and the
    high tier is carried as the library's extended_thinking flag on
    generate_content instead of as an enum. Non-Hydrogen models keep the
    exact enum path they had before (flash+high -> ADVANCED_FLASH, etc.).
    """
    if not model_str and not thinking:
        return None, False
    q = (model_str or "").lower().strip()
    if q.replace("-", " ") in _HYDROGEN_NAMES and thinking:
        tier = _THINKING_ALIASES.get(thinking.lower().strip(), thinking.upper())
        return resolve_model_string(client, model_str), tier == "ADVANCED"
    if thinking:
        return resolve_model_enum(model_str, thinking), False
    return resolve_model_string(client, model_str), False


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

# ── Video / music generation ─────────────────────────────────────
# Same mechanism as gemini.py (kept in sync; the two CLIs duplicate
# helpers by design). The web app's Create video / Create music tools
# are not exposed by gemini-webapi 2.1.1, but the underlying
# StreamGenerate request is the same one the library sends — with a
# "generate media" tool flag in message_content[9] and media selectors
# in the inner payload. Reverse-engineered from live gemini.google.com
# traffic; see FarisHijazi/gemini-web-api
# docs/devlog/claude_2026-07-08-0045-veo-video-cracked.md (Veo video
# verified end-to-end there on gemini-webapi 2.1.x).
#
#   * A media request as the FIRST turn of a fresh conversation is
#     rejected by the server (error 1053), so the pipeline primes the
#     conversation with a cheap text turn first, then sends the media
#     prompt on the second turn.
#   * message_content[9] = the media tool flag: trailing 1 = video,
#     trailing 2 = portrait video. inner[17] = [[1]] selects video
#     (the library hardcodes [[0]], i.e. image). For video,
#     inner[54] = [] and inner[55] = [[aspect]] (16 landscape,
#     17 portrait). Never set inner[49] — it is a per-conversation
#     turn counter; setting it also yields error 1053.
#   * The video turn uses the gemini-3-video model header. Music uses
#     the default model — the server routes by prompt + tool flag, and
#     the plain-prompt probe reached the music tool on the default model.
#   * The send returns fast with a pending chip; the finished media
#     appears on the turn afterwards, so we poll read_chat until
#     turn.model_output.videos / .media is populated.
#   * Injection: gemini_webapi.client does `import orjson as json` and
#     serialises the generate payload with json.dumps. We wrap that
#     module reference in a transparent proxy, gated by a contextvar
#     that is set only for the media turn — every other serialisation
#     passes straight through, and the proxy is installed lazily so a
#     plain chat never touches it.

_MEDIA_TOOL = [None, None, None, None, None, None, [[None, None, None, 1]]]
_MEDIA_TOOL_PORTRAIT = [None, None, None, None, None, None, [[None, None, None, 2]]]
_VIDEO_MODEL = {
    "model_name": "gemini-3-video",
    "model_header": {"x-goog-ext-525001261-jspb":
        '[1,null,null,null,"e6fa609c3fa255c0",null,null,0,[4,5,6,8],null,null,2,null,null,3,null,"00000000-0000-0000-0000-000000000000"]'},
}
_ASPECT_LANDSCAPE = 16
_ASPECT_PORTRAIT = 17

_media_ctx = contextvars.ContextVar("gemini_media_mode", default=None)   # "video" | "music" | None
_media_aspect = contextvars.ContextVar("gemini_media_aspect", default=_ASPECT_LANDSCAPE)

def _is_generate_payload(obj) -> bool:
    """True for the library's StreamGenerate inner payload: a list at
    least 69 wide whose first element is itself a list (message_content)."""
    return isinstance(obj, list) and len(obj) >= 69 and isinstance(obj[0], list)

class _MediaJsonProxy:
    """Transparent proxy over the `json` (orjson) reference inside
    gemini_webapi.client. Only rewrites the generate payload shape,
    only while the media contextvar is set."""
    def __init__(self, real): self._real = real
    def __getattr__(self, name): return getattr(self._real, name)
    def dumps(self, obj, *args, **kwargs):
        mode = _media_ctx.get()
        if mode and _is_generate_payload(obj):
            mc = obj[0]
            while len(mc) < 10: mc.append(None)
            mc[9] = _MEDIA_TOOL_PORTRAIT if _media_aspect.get() == _ASPECT_PORTRAIT else _MEDIA_TOOL
            obj[17] = [[1]]
            if mode == "video":
                obj[54] = []
                obj[55] = [[_media_aspect.get()]]
        return self._real.dumps(obj, *args, **kwargs)

def _install_media_proxy():
    # No-op when the library is absent (CI installs pytest only): the
    # media proxy only matters for a real library call, and
    # _generate_media's unit tests run with a fake client.
    try:
        import gemini_webapi.client as _gclient
    except ImportError:
        return
    if not isinstance(_gclient.json, _MediaJsonProxy):
        _gclient.json = _MediaJsonProxy(_gclient.json)

def _looks_like_quota(text: str) -> bool:
    t = (text or "").lower()
    return any(k in t for k in ("limit", "quota", "upgrade", "subscription",
                                "usage", "reset", "plan"))

class MediaStopped(RuntimeError):
    """Gemini ended the media turn with a verdict (quota / subscription /
    filter) instead of media. The message is the server's own text.
    Carries the conversation id so a stalled turn can be inspected
    afterwards with --read-chat (live 2026-11-14: a video turn stalled
    with only a "_923" fragment and no cid was printed, so the turn
    could not be re-read)."""
    def __init__(self, msg, cid=None):
        super().__init__(msg)
        self.cid = cid

async def _poll_media(client, cid, kind, timeout, interval=8.0):
    """Poll read_chat until the finished media appears on a model turn.
    Returns (media_obj, text). media_obj is a GeneratedVideo /
    GeneratedMedia or None; text is the latest model text — the server's
    verdict when the turn ends without media. Stops early when the same
    quota/subscription verdict repeats twice in a row (the turn is final)."""
    deadline = time.monotonic() + timeout
    last_text = ""
    stop_repeats = 0
    while time.monotonic() < deadline:
        try:
            hist = await client.read_chat(cid, limit=2)
        except Exception:
            hist = None
        if hist:
            turns = [t for t in (hist.turns or [])
                     if t.role == "model" and t.model_output]
            for turn in turns:
                items = (turn.model_output.videos if kind == "video"
                         else turn.model_output.media)
                if items:
                    return items[0], turn.text or ""
            # Verdict text comes only from the NEWEST model turn (turns
            # are newest-first); an older turn's text — e.g. the prime
            # turn's "READY" — is not the media turn's verdict.
            newest_text = turns[0].text if turns else ""
            if newest_text:
                if newest_text == last_text and _looks_like_quota(newest_text):
                    stop_repeats += 1
                    if stop_repeats >= 2:
                        return None, newest_text
                else:
                    stop_repeats = 0
                last_text = newest_text
            else:
                stop_repeats = 0
        await asyncio.sleep(interval)
    return None, last_text

async def _generate_media(client, prompt, kind, aspect=_ASPECT_LANDSCAPE, timeout=600.0):
    """Full media pipeline. Returns (media_obj, cid, text).
    1. Prime the conversation with a cheap text turn (a media request
       as the first turn is rejected, error 1053).
    2. Send the prompt with the media tool flag injected (contextvar).
    3. Poll read_chat until the finished media appears.
    Raises MediaStopped when the server ends the turn with a verdict
    instead of media; RuntimeError when no conversation could be opened.
    """
    _install_media_proxy()
    chat = client.start_chat()
    await asyncio.wait_for(
        chat.send_message("I want to create media. Reply with just: READY"),
        timeout=120)
    cid = chat.cid
    if not cid:
        raise RuntimeError("failed to open a conversation for media generation")
    # Video needs the video model header; music uses the default model.
    if kind == "video":
        chat.model = _VIDEO_MODEL
    tok = _media_ctx.set(kind)
    atok = _media_aspect.set(aspect)
    try:
        try:
            await asyncio.wait_for(chat.send_message(prompt), timeout=180)
        except asyncio.TimeoutError:
            # The send may still be running server-side; the media can
            # still land on the turn — poll the conversation either way.
            pass
    finally:
        _media_ctx.reset(tok)
        _media_aspect.reset(atok)
    media, text = await _poll_media(client, cid, kind, timeout=timeout)
    if media is None:
        raise MediaStopped(text or "Gemini ended the turn without media.", cid=cid)
    return media, cid, text

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
                thoughts: bool = False,
                model_label: str = "", gem_name: str = "", deep_research: bool = False):
        """Emit compact pointer JSON to stdout."""
        p = {"ok": True, "f": self._short_path(out_path), "s": out_path.stat().st_size}
        if code_blocks:
            p["b"] = code_blocks
        if images:
            p["imgs"] = len(images)
        if thoughts:
            p["thoughts"] = True
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
                            choices=["flash", "pro", "thinking", "lite",
                                     "hydrogen", "flash-lite"],
                            help="Model: flash, pro, thinking, lite, hydrogen (web default), flash-lite")
        parser.add_argument("--thinking", type=str, metavar="TIER",
                            choices=["standard", "plus", "extended",
                                     "low", "medium", "high"],
                            help="Thinking tier: standard/low, plus/medium, extended/high")
        # Image generation
        parser.add_argument("--img-gen", action="store_true", dest="image_gen",
                            help="Force image generation mode")
        parser.add_argument("--img", type=str, dest="image_prompt", metavar="PROMPT",
                            help="Generate an image from prompt")
        # Video / music gen (Veo / Lyria via the web media tool flag)
        parser.add_argument("--video", type=str, dest="video_prompt", metavar="PROMPT",
                            help="Generate video (Veo)")
        parser.add_argument("--music", type=str, dest="music_prompt", metavar="PROMPT",
                            help="Generate music (Lyria)")
        parser.add_argument("--aspect", choices=["landscape", "portrait"], default="landscape",
                            help="Video aspect ratio (default landscape)")
        parser.add_argument("--save-videos", type=str, metavar="DIR",
                            help="Save generated videos to DIR")
        parser.add_argument("--save-media", type=str, metavar="DIR",
                            help="Save generated media (audio/video) to DIR")
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
        parser.add_argument("--show-thoughts", action="store_true",
                            help="Include the model's chain of thought in output "
                            "('thoughts' in --json-out, '## Thoughts' in .md, "
                            "thoughts:true in the pointer; only populated for "
                            "thinking models/tiers)")
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
        parser.add_argument("-p", "--prompt-flag", type=str, dest="prompt_flag",
                            help="Prompt text (alternative to positional or stdin)")
        parser.add_argument("--create-gem", type=str, dest="create_gem_name", metavar="NAME",
                            help="Create a new Gem with system prompt from stdin, -p, or positional args")
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
        args = parser.parse_intermixed_args()

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
            if not self.raw_mode:
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
            # Read system prompt from stdin, -p, or positional args
            if args.prompt_flag:
                system_prompt = args.prompt_flag
                stdin_content = _read_stdin_if_piped()
                if stdin_content:
                    system_prompt = f"{system_prompt}\n\n{stdin_content}"
            elif args.prompt:
                system_prompt = " ".join(args.prompt)
            elif not sys.stdin.isatty():
                system_prompt = _read_stdin_if_piped()
                if not system_prompt:
                    fail("NO_PROMPT", "Provide system prompt via -p, stdin, or positional args.")
            else:
                fail("NO_PROMPT", "Provide system prompt via -p, stdin, or positional args.")
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
        # parse_intermixed_args dumps all positionals into args.url first:
        # a sentence-like positional is prompt text, not a Gem URL. Merge
        # it back into args.prompt (same rule as gemini.py) so a bare
        # prompt without a Gem runs direct instead of BAD_URL.
        if args.url and not args.prompt_flag \
                and not (args.url.startswith(("http://", "https://"))
                         or re.fullmatch(r"[A-Za-z0-9_-]{5,20}", args.url or "")):
            args.prompt = args.url.split() + list(args.prompt)
            args.url = None
        # --img / --img-gen / --video / --music / --deep-research / --resume are standalone
        # modes: they run direct (gem-less) when no Gem is given, the same
        # way gemini.py runs them. (Before this, `--resume <cid>` without
        # a Gem failed NO_URL even though resume needs no Gem.)
        if not args.url and not (args.image_prompt or args.image_gen or args.video_prompt or args.music_prompt or args.deep_research or args.resume_session):
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
        elif not args.url:
            gem_id = None  # direct (gem-less) run: --img / --deep-research
        else:
            try:
                gem_id = extract_gem_id(args.url)
            except ValueError as e:
                fail("BAD_URL", str(e))

        # ── Build prompt ──
        media_kind = None
        if args.video_prompt:
            prompt = args.video_prompt; media_kind = "video"
        elif args.music_prompt:
            prompt = args.music_prompt; media_kind = "music"
        elif args.image_prompt:
            prompt = f"Generate an image: {args.image_prompt}"
            args.image_gen = True
        elif args.prompt_flag:
            prompt = args.prompt_flag
            stdin_content = _read_stdin_if_piped()
            if stdin_content:
                prompt = f"{prompt}\n\n{stdin_content}"
        elif args.prompt:
            prompt = " ".join(args.prompt)
        elif args.list_models or args.list_gems:
            prompt = ""
        elif not sys.stdin.isatty():
            prompt = _read_stdin_if_piped()
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
        extended_thinking = False
        if args.model or args.thinking:
            model, extended_thinking = resolve_model(self.client, args.model, args.thinking)

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
            parts = [f"gem={gem_name or gem_id or 'direct'}", f"model={model_label}"]
            if args.deep_research:
                parts.append("deep-research")
            if args.image_gen:
                parts.append("img-gen")
            if media_kind:
                parts.append(media_kind)
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
        # (enum, not the old "gemini-3-flash" string: that name is no
        # longer registered — the library maps the enum onto the account's
        # equivalent model. If the enum is unavailable, leave the model
        # unset and let the server use its default.)
        if args.image_gen and not model:
            try:
                model = Model.BASIC_FLASH
            except AttributeError:
                pass

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
        # Video renders take minutes; music a couple of minutes.
        if media_kind == "video" and args.timeout == 120:
            actual_timeout = 600
            if not self.raw_mode:
                self.log(f"Video generation: timeout auto-extended to {actual_timeout}s")
        elif media_kind == "music" and args.timeout == 120:
            actual_timeout = 300
            if not self.raw_mode:
                self.log(f"Music generation: timeout auto-extended to {actual_timeout}s")

        # ── Video / music generation ──
        # Own pipeline (prime → media turn → poll), not the generic
        # generate loop: the media turn returns fast with a pending chip
        # and the finished file only lands on the conversation afterwards.
        if media_kind:
            if not self.raw_mode:
                self.log(f"Generating {media_kind}...")
            try:
                media_obj, media_cid, media_text = await _generate_media(
                    self.client, prompt, media_kind,
                    aspect=_ASPECT_PORTRAIT if args.aspect == "portrait" else _ASPECT_LANDSCAPE,
                    timeout=actual_timeout)
            except MediaStopped as ms:
                _extra = {"retry": False}
                if getattr(ms, "cid", None): _extra["cid"] = ms.cid
                fail("MEDIA_QUOTA" if _looks_like_quota(str(ms)) else "MEDIA_FAILED",
                     str(ms), _extra)
            except asyncio.TimeoutError:
                fail("TIMEOUT", f"Media generation timed out after {actual_timeout}s.",
                     {"timeout_s": actual_timeout, "retry": False})
            except Exception as e:
                fail("MEDIA_FAILED", f"{type(e).__name__}: {e}", {"retry": False})
            url = getattr(media_obj, "url", None)
            # Save: library first (handles 206 polling), urllib + SID/TS
            # cookies as fallback, else report the download URL.
            save_dir = Path(args.save_videos if media_kind == "video" and args.save_videos else
                            args.save_media if media_kind == "music" and args.save_media else
                            ("./videos" if media_kind == "video" else "./music"))
            save_dir.mkdir(parents=True, exist_ok=True)
            saved = []
            try:
                res = await media_obj.save(path=str(save_dir))
                if isinstance(res, dict):
                    saved = [v for v in res.values() if v and Path(v).exists()]
                elif isinstance(res, str) and Path(res).exists():
                    saved = [res]
            except Exception as save_err:
                if not self.raw_mode:
                    self.log(f"Library save failed ({save_err}); trying direct download")
            if not saved and url:
                import urllib.request as _ur
                cookie_str = f"__Secure-1PSID={sid}; __Secure-1PSIDTS={ts}"
                try:
                    fp = save_dir / f"gemini_{media_kind}_{datetime.now().strftime('%Y%m%d-%H%M%S')}.mp4"
                    req = _ur.Request(url, headers={"Cookie": cookie_str})
                    with _ur.urlopen(req, timeout=120) as resp:
                        fp.write_bytes(resp.read())
                    saved = [str(fp)]
                except Exception as dl_err:
                    if not self.raw_mode:
                        self.log(f"Direct download failed: {dl_err}")
            # Summary file + pointer (same contract as the text path).
            ext = ".json" if args.json_out else ".md"
            out_path = Path(args.output) if args.output else \
                       Path(f"/tmp/gem-cli-{datetime.now().strftime('%Y%m%d-%H%M%S')}{ext}")
            if args.json_out:
                payload = {"ok": True, "kind": media_kind, "cid": media_cid,
                           "prompt": prompt, "download_url": url, "files": saved,
                           "model_text": media_text or ""}
                out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            else:
                lines = [f"# {media_kind.title()} generation", "", f"Prompt: {prompt}", "",
                         f"Conversation: {media_cid}", f"Download URL: {url or '(none)'}", ""]
                if saved:
                    lines.append("Saved files:")
                    lines += [f"- {f}" for f in saved]
                if media_text:
                    lines += ["", "Model text:", "", media_text]
                out_path.write_text("\n".join(lines), encoding="utf-8")
            print(json.dumps({"ok": True, "kind": media_kind, "f": self._short_path(out_path),
                              "s": out_path.stat().st_size, "cid": media_cid,
                              "download_url": url, "files": saved}))
            return

        # ── Generate with retry ──
        max_attempts = 1 if args.no_retry else 3

        for attempt in range(max_attempts):
            if attempt > 0:
                self.log(f"Retry {attempt + 1}/{max_attempts}...")

            dr_report = None  # deep-research report text (document, not reply notice)
            try:
                if args.deep_research:
                    # Deep research: create plan → start → wait for results
                    # Plan creation is flaky on a degraded session (live
                    # 2026-11-14: empty/refusal ~3/5 attempts, success the
                    # other times) — retry the plan call, not the ladder.
                    plan = None
                    last_plan_err = None
                    for pi in range(3):
                        try:
                            if not self.raw_mode:
                                self.log("Creating research plan...")
                            plan = await asyncio.wait_for(
                                self.client.create_deep_research_plan(prompt, model=model),
                                timeout=120)
                            break
                        except Exception as pe:
                            last_plan_err = pe
                            if not self.raw_mode:
                                self.log(f"Plan attempt {pi + 1}/3 failed: {pe}")
                            if pi < 2:
                                await asyncio.sleep(15)
                    if plan is None:
                        raise last_plan_err
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
                    _wfw = {"poll_interval": 15.0, "timeout": actual_timeout}
                    if _supports(self.client.wait_for_deep_research, "on_status"):
                        _wfw["on_status"] = lambda s: (
                            self.log(f"  [{s.state or '...'}]")
                            if not self.raw_mode and s else None
                        )
                    result = await asyncio.wait_for(
                        self.client.wait_for_deep_research(plan, **_wfw),
                        timeout=actual_timeout,
                    )
                    response = result.final_output
                    dr_report = _dr_report_text(result)
                else:
                    kwargs = {"prompt": prompt}
                    if all_files:
                        kwargs["files"] = all_files
                    if chat_metadata:
                        kwargs["chat"] = ChatRef(chat_metadata)
                    if model:
                        kwargs["model"] = model
                    if extended_thinking and _supports(self.client.generate_content, "extended_thinking"):
                        kwargs["extended_thinking"] = True
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
            if args.deep_research and dr_report:
                # The report is the attached document, not the reply notice;
                # without this the stub guard below fails a run that worked.
                text = dr_report
            if args.deep_research and _dr_is_stub(text):
                fail("DR_FAILED",
                     "Deep research returned no report (plan creation failed and the "
                     "fallback returned only a stub). The account/session may not "
                     "support deep research on this build.",
                     {"retry": False})
            new_meta = list(response.metadata) if response.metadata else None

            images_out = []
            try:
                for img in response.images:
                    images_out.append({"url": img.url, "alt": img.alt or ""})
            except Exception:
                pass

            # Chain of thought (gemini-webapi ModelOutput.thoughts).
            # Extracted for every response, like gemini.py: --json-out
            # stores it whenever present, --show-thoughts controls the
            # .md rendering and the pointer flag. None is normal for
            # non-thinking models/tiers, not an error.
            thoughts_text = _extract_thoughts(response)
            has_thoughts = bool(thoughts_text)

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
                if has_thoughts:
                    payload["thoughts"] = thoughts_text
                elif args.show_thoughts:
                    self.log("No thoughts returned — model may not provide thinking traces for this prompt/tier.")
                if conv_state:
                    payload["conversation"] = conv_state
                out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
            else:
                out_text = text
                if has_thoughts and args.show_thoughts:
                    out_text += "\n\n## Thoughts\n\n" + thoughts_text
                elif args.show_thoughts and not has_thoughts:
                    self.log("No thoughts returned — model may not provide thinking traces for this prompt/tier.")
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
                        thoughts=has_thoughts,
                        model_label=model_label, gem_name=gem_name,
                        deep_research=args.deep_research)
            return

        fail("RETRY_EXHAUSTED", f"Failed after {max_attempts} attempts.")


if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    cli = GemCLI()
    asyncio.run(cli.run())
