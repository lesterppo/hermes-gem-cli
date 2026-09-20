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
