#!/usr/bin/env python3
"""Offline unit tests for gemini.py — no network, no browser, no cookies.

Network calls (`_validate_auth`) and cookie stores (`_browser_cookie_jars`) are
monkeypatched so the tests pin the wrapper's own decisions: which credential is
chosen, how a cookie jar is assembled, and how pointer/error JSON is shaped.

Run:  python3 -m pytest tests/ -q      (or: python3 tests/test_gemini.py)
"""
import importlib.util
import inspect
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("gemini", ROOT / "gemini.py")
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)


def _jar(sid, ts=None, n=25):
    jar = {"__Secure-1PSID": sid, "SAPISID": "sap", "SID": "sid",
           "__Secure-1PAPISID": "pap"}
    if ts:
        jar["__Secure-1PSIDTS"] = ts
    return jar


class TestAuthSelection(unittest.TestCase):
    """A pair can satisfy StreamGenerate (chat) while failing every SNlM0e path
    (Gem CRUD, list-gems, list-chats). Selection must prefer a live one."""

    def setUp(self):
        self._orig = (g._auth_candidates, g._probe_live, g._save_auth_cache)
        g._save_auth_cache = lambda *a, **k: None
        g._PROBE_CACHE.clear()
        g._WARNED_NO_LIVE[0] = False

    def tearDown(self):
        g._auth_candidates, g._probe_live, g._save_auth_cache = self._orig

    def test_prefers_live_candidate_over_stale_cache(self):
        g._auth_candidates = lambda preferred=None: [
            ("cache", "dead-sid", "dead-ts"),
            ("browser:firefox", "live-sid", "live-ts"),
        ]
        g._probe_live = lambda sid, ts: sid == "live-sid"
        sid, ts = g.resolve_auth()
        self.assertEqual(sid, "live-sid")

    def test_falls_back_to_first_candidate_when_none_live(self):
        g._auth_candidates = lambda preferred=None: [("cache", "a", "b"),
                                                     ("browser:chrome", "c", "d")]
        g._probe_live = lambda sid, ts: False
        sid, ts = g.resolve_auth()
        self.assertEqual(sid, "a")           # chat may still work with it

    def test_env_short_circuits(self):
        os.environ["GEMINI_SID"] = "env-sid"
        os.environ["GEMINI_TS"] = "env-ts"
        try:
            g._auth_candidates = lambda preferred=None: [("cache", "x", "y")]
            self.assertEqual(g.resolve_auth(), ("env-sid", "env-ts"))
        finally:
            os.environ.pop("GEMINI_SID")
            os.environ.pop("GEMINI_TS")

    def test_no_candidates_exits(self):
        g._auth_candidates = lambda preferred=None: []
        with self.assertRaises(SystemExit):
            g.resolve_auth()


class TestCookieJarConsistency(unittest.TestCase):
    """Mixing one source's 1PSID with another's 1PSIDTS yields a jar the server
    treats as signed out — that broke --list-chats while chat kept working."""

    def setUp(self):
        self._orig = g._browser_cookie_jars

    def tearDown(self):
        g._browser_cookie_jars = self._orig

    def test_matching_jar_wins(self):
        g._browser_cookie_jars = lambda preferred=None: [
            ("browser:chrome", _jar("chrome-sid", "chrome-ts")),
            ("browser:firefox", _jar("ff-sid", "ff-ts")),
        ]
        src, jar = g._cookie_jar_for("ff-sid")
        self.assertEqual(src, "browser:firefox")
        self.assertEqual(jar["__Secure-1PSIDTS"], "ff-ts")

    def test_no_match_falls_back_to_a_whole_jar(self):
        g._browser_cookie_jars = lambda preferred=None: [("browser:firefox", _jar("ff-sid", "ff-ts"))]
        src, jar = g._cookie_jar_for("cache-sid")
        self.assertEqual(src, "browser:firefox")
        self.assertEqual(jar["__Secure-1PSID"], "ff-sid")
        self.assertEqual(jar["__Secure-1PSIDTS"], "ff-ts")   # pair stays together

    def test_no_jars_at_all(self):
        g._browser_cookie_jars = lambda preferred=None: []
        src, jar = g._cookie_jar_for("cache-sid")
        self.assertEqual(jar, {})


class TestLabelsAndHelpers(unittest.TestCase):
    def test_default_model_label_is_not_None_string(self):
        self.assertEqual(g.friendly_model_label(None), "default")

    def test_known_labels(self):
        self.assertEqual(g.friendly_model_label({"model_name": "gemini-3-flash"}), "flash")
        self.assertEqual(g.friendly_model_label("gemini-3-pro"), "pro")
        self.assertEqual(g.friendly_model_label({"model_name": "Flash-Lite"}), "lite")

    def test_supports_detects_missing_kwarg(self):
        def fn(a, b=1):
            return a
        self.assertTrue(g._supports(fn, "b"))
        self.assertFalse(g._supports(fn, "on_status"))   # the DR bug: kwarg does not exist

    def test_error_kind_classification(self):
        self.assertEqual(g.error_kind("UNAUTHENTICATED: session is not authenticated"),
                         "AUTH_EXPIRED")
        self.assertEqual(g.error_kind("error code: 1097 quota exceeded"), "RATE_LIMIT")
        self.assertEqual(g.error_kind("something else entirely"), "GEN_FAILED")

    def test_extract_gem_id(self):
        self.assertEqual(g.extract_gem_id("https://gemini.google.com/gem/abc123def456"),
                         "abc123def456")
        self.assertEqual(g.extract_gem_id("abc123def456"), "abc123def456")
        with self.assertRaises(ValueError):
            g.extract_gem_id("https://example.com/notagem")


class TestArgSurface(unittest.TestCase):
    """Guards the documented flags against accidental removal."""

    def test_flags_present_in_source(self):
        src = inspect.getsource(g)
        for flag in ("--doctor", "--list-gems", "--list-chats", "--read-chat",
                     "--deep-research-status", "--create-gem", "--delete-gem",
                     "--save-images", "--extract-canvas", "--show-thoughts",
                     "--temporary", "--stream", "--fetch-latest"):
            self.assertIn('"%s"' % flag, src, "missing flag %s" % flag)

    def test_dr_status_takes_a_chat_id_not_a_task_id(self):
        src = inspect.getsource(g.GeminiCLI.run)
        self.assertIn("fetch_latest_chat_response", src)
        self.assertNotIn("get_deep_research_status", src)   # method never existed

    def test_read_chat_emits_conversation_order(self):
        """The library returns turns NEWEST-first; the CLI must reverse whole turn
        pairs (flattened reversal would swap user/model inside each turn)."""
        src = inspect.getsource(g.GeminiCLI.run)
        self.assertIn("reversed(history.turns)", src)
        self.assertIn("reversed(pairs)", src)
        self.assertIn('"order": "oldest-first"', src)


class TestModelResolution(unittest.TestCase):
    """-m must resolve against the LIVE registry, not the old gemini-3-* names.

    Live-verified 2026-11-14 against server build 20261113: the registry
    lists only "gemini-自動", so every shorthand used to reach
    generate_content as an unregistered string and fail with
    "Unknown model name". The enum fallback lets the library map the
    shorthand onto the account's equivalent registered model."""

    class _FakeModel:
        def __init__(self, model_id, display):
            self.model_id = model_id
            self._display = display
        def __str__(self):
            return self._display

    class _StubClient:
        def __init__(self, models):
            self._models = models
        def list_models(self):
            return self._models

    def _auto_only(self):
        return self._StubClient([self._FakeModel("auto-id-1", "gemini-自動")])

    def test_shorthands_fall_back_to_enum_on_auto_only_registry(self):
        if g.GeminiModel is None:
            self.skipTest("gemini-webapi not importable here")
        self.assertEqual(g.resolve_model_string(self._auto_only(), "flash").name, "BASIC_FLASH")
        self.assertEqual(g.resolve_model_string(self._auto_only(), "pro").name, "BASIC_PRO")

    def test_thinking_has_no_enum_member_in_this_library(self):
        # gemini-webapi 2.1.1's Model enum has no *THINKING members, so
        # there is nothing to fall back to: the name passes through and
        # the library fails fast naming the available models, rather than
        # the CLI silently substituting a different model.
        self.assertEqual(g.resolve_model_string(self._auto_only(), "thinking"), "thinking")

    def test_lite_keeps_dict_spec_on_auto_only_registry(self):
        self.assertIsInstance(g.resolve_model_string(self._auto_only(), "lite"), dict)

    def test_unknown_name_passes_through_for_library_error(self):
        # A future name (e.g. a new model family) must not be silently rewritten:
        # either the registry lists it one day, or the library fails fast
        # naming the available models. ("hydrogen" used to be the example
        # here; it is now a known web name that resolves to the default.)
        self.assertEqual(g.resolve_model_string(self._auto_only(), "quantum"), "quantum")

    def test_registry_match_still_wins_when_registered(self):
        client = self._StubClient([self._FakeModel("56fdd199312815e2", "gemini-3-flash")])
        self.assertEqual(g.resolve_model_string(client, "flash"), "gemini-3-flash")

    def test_web_picker_names(self):
        # Web picker (2026-11-14): "Quick answers" = 3.5 Flash-Lite,
        # "Everyday help" = 4 Hydrogen. Hydrogen is not a registered WebAPI
        # name, so it resolves to the registry's single default (auto).
        if g.GeminiModel is None:
            self.skipTest("gemini-webapi not importable here")
        self.assertEqual(g.resolve_model_string(self._auto_only(), "3.5 flash-lite").name,
                         "BASIC_LITE")
        self.assertEqual(g.resolve_model_string(self._auto_only(), "hydrogen"), "gemini-自動")
        self.assertEqual(g.resolve_model_string(self._auto_only(), "4 hydrogen"), "gemini-自動")
        self.assertEqual(g.friendly_model_label("gemini-自動"), "auto")

    def test_thinking_level_web_names(self):
        # Web picker "Thinking level": Low / Medium / High.
        if g.GeminiModel is None:
            self.skipTest("gemini-webapi not importable here")
        self.assertEqual(g.resolve_model_enum("flash", "high").name, "ADVANCED_FLASH")
        self.assertEqual(g.resolve_model_enum("flash", "low").name, "BASIC_FLASH")
        self.assertEqual(g.resolve_model_enum("pro", "medium").name, "PLUS_PRO")


class TestStdinRead(unittest.TestCase):
    """_read_stdin_if_piped must not hang forever on an open pipe.

    Live-verified 2026-11-14: direct chat with -p hung with zero output
    when spawned with an open stdin pipe, because sys.stdin.read() waits
    for EOF; stdin=/dev/null answered in seconds."""

    def test_open_idle_pipe_returns_empty_fast(self):
        r, w = os.pipe()
        old = sys.stdin
        try:
            sys.stdin = os.fdopen(r, "r")
            t0 = __import__("time").monotonic()
            self.assertEqual(g._read_stdin_if_piped(timeout=0.2), "")
            self.assertLess(__import__("time").monotonic() - t0, 1.5)
        finally:
            sys.stdin = old
            os.close(w)

    def test_piped_data_is_still_consumed(self):
        r, w = os.pipe()
        os.write(w, b"piped words")
        os.close(w)
        old = sys.stdin
        try:
            sys.stdin = os.fdopen(r, "r")
            self.assertEqual(g._read_stdin_if_piped(timeout=0.2), "piped words")
        finally:
            sys.stdin = old


class TestDrStub(unittest.TestCase):
    """A stub is not a report: --deep-research must fail DR_FAILED instead
    of ok:true when plan creation fails and the fallback returns only a
    plan-acknowledgement sentence (live-verified 2026-11-14: 78 chars)."""

    def test_stub_detection(self):
        self.assertTrue(g._dr_is_stub(None))
        self.assertTrue(g._dr_is_stub(""))
        self.assertTrue(g._dr_is_stub("Here's my plan to tackle that topic. "
                                      "Let me know if you need to make changes."))
        self.assertFalse(g._dr_is_stub("x" * 200))
        self.assertFalse(g._dr_is_stub("## Report\n\n" + "detail. " * 100))


class TestSupports(unittest.TestCase):
    """Library signatures drift across gemini-webapi versions; callers must
    probe before passing optional kwargs (live 2026-11-14: gem-cli.py
    passed on_status to wait_for_deep_research, which 2.1.1 rejects)."""

    def test_supports(self):
        def f(a, b=1):
            pass
        self.assertTrue(g._supports(f, "a"))
        self.assertTrue(g._supports(f, "b"))
        self.assertFalse(g._supports(f, "on_status"))
        self.assertFalse(g._supports(None, "x"))


class TestRpcErrorMessage(unittest.TestCase):
    """A read-RPC that returns no payloads is a failed read, not 'no data':
    the server answers with a BardErrorInfo envelope (live 2026-11-14:
    MaZiqc 1041, Gemini Activity off)."""

    def test_activity_off_envelope(self):
        raw = ('[["wrb.fr","MaZiqc",null,null,null,[9,null,'
               '[["type.googleapis.com/assistant.boq.bard.application.BardErrorInfo",'
               '[1041,null,"Turn on Gemini Activity to use this feature. (1041)"]]]],'
               '"generic"]]')
        self.assertEqual(g._rpc_error_message(raw),
                         "Turn on Gemini Activity to use this feature. (1041) (1041)")

    def test_no_error(self):
        self.assertIsNone(g._rpc_error_message(")]}'\n\n[[\"wrb.fr\",\"MaZiqc\",\"ok\"]]"))


class TestImportSafety(unittest.TestCase):
    def test_import_without_optional_deps_does_not_exit(self):
        """The CLI exits(1) with DEP_MISSING when run without gemini-webapi, but
        importing the module for tooling/tests must not kill the process."""
        import subprocess
        code = (
            "import sys, importlib.util\n"
            "sys.modules['gemini_webapi'] = None\n"
            "sys.modules['gemini_webapi.client'] = None\n"
            "spec = importlib.util.spec_from_file_location('g2', %r)\n"
            "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
            "print('SURVIVED', m.GeminiClient is None)\n" % str(ROOT / "gemini.py")
        )
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        self.assertIn("SURVIVED True", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
