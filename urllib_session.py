"""
urllib_session — synchronous urllib-based replacement for curl_cffi.requests.AsyncSession.
curl_cffi hangs on WSL2. urllib works fine. This wrapper lets gemini-webapi
use urllib without modifying the library's internals.
"""
import json as _json
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar, Cookie
from typing import Any


class UrllibCookies:
    """Mimics curl_cffi.requests.Cookies for gemini-webapi compatibility.
    
    Uses a real CookieJar so gemini-webapi's cookie operations work.
    """

    def __init__(self):
        self._dict: dict[str, str] = {}
        self.jar: CookieJar = CookieJar()

    def set(self, name: str, value: str, domain: str = "", path: str = "/"):
        self._dict[name] = value
        c = Cookie(
            version=0, name=name, value=value,
            port=None, port_specified=False,
            domain=domain, domain_specified=bool(domain),
            domain_initial_dot=domain.startswith("."),
            path=path, path_specified=bool(path),
            secure=True, expires=None, discard=False,
            comment=None, comment_url=None,
            rest={}, rfc2109=False,
        )
        self.jar.set_cookie(c)

    def update(self, other: "UrllibCookies"):
        if hasattr(other, 'jar'):
            for c in other.jar:
                self.jar.set_cookie(c)
        if hasattr(other, '_dict'):
            self._dict.update(other._dict)

    def get(self, name: str, default: Any = None) -> Any:
        return self._dict.get(name, default)

    def clear(self):
        self._dict.clear()
        self.jar.clear()

    def __iter__(self):
        """Iterate cookies as Cookie objects (for curl_cffi compatibility)."""
        return iter(self.jar)


class UrllibResponse:
    """Mimics curl_cffi.requests.Response."""

    def __init__(self, status_code: int, text: str, url: str = ""):
        self.status_code = status_code
        self.text = text
        self.url = url
        self.cookies = UrllibCookies()

    def json(self):
        return _json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise urllib.error.HTTPError(
                self.url, self.status_code, "", None, None  # type: ignore
            )


class UrllibSession:
    """Synchronous urllib-based HTTP session that mimics AsyncSession.

    All methods are sync but gemini-webapi calls them with `await`.
    """

    def __init__(self, impersonate: str = "", timeout: int = 450,
                 proxy: str = "", allow_redirects: bool = True,
                 verify: bool = True):
        self.impersonate = impersonate
        self.timeout = timeout
        self.proxy = proxy
        self.allow_redirects = allow_redirects
        self.verify = verify
        self.cookies = UrllibCookies()
        self.headers: dict[str, str] = {}

    def _build_request(self, method: str, url: str, **kwargs) -> urllib.request.Request:
        headers = dict(self.headers)
        data = kwargs.get("data")
        params = kwargs.get("params", {})

        # Build cookie header
        cookie_parts = []
        for name, value in self.cookies._dict.items():
            cookie_parts.append(f"{name}={value}")
        if cookie_parts:
            headers["Cookie"] = "; ".join(cookie_parts)

        custom_headers = kwargs.get("headers", {})
        if isinstance(custom_headers, dict):
            headers.update(custom_headers)

        if params:
            url = url + "?" + urllib.parse.urlencode(params)

        body = None
        if data is not None:
            if isinstance(data, dict):
                body = urllib.parse.urlencode(data).encode()
                headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
            elif isinstance(data, str):
                body = data.encode()
            elif isinstance(data, bytes):
                body = data

        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        return req

    async def get(self, url: str, **kwargs) -> UrllibResponse:
        return self._request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs) -> UrllibResponse:
        return self._request("POST", url, **kwargs)

    def _request(self, method: str, url: str, **kwargs) -> UrllibResponse:
        req = self._build_request(method, url, **kwargs)
        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout)
            text = resp.read().decode(errors="replace")
            return UrllibResponse(resp.status, text, url)
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace") if e.fp else ""
            return UrllibResponse(e.code, body, url)

    async def close(self):
        pass
