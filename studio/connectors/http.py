"""The only place that talks to the internet. Hosts are fixed, redirects are refused, and keys never reach logs or exceptions."""
from __future__ import annotations

import os
import re
from urllib.parse import urlsplit

import httpx

ALLOWED = {
    "api.elevenlabs.io",
    "generativelanguage.googleapis.com",
    "api.openai.com",
    "api.bfl.ai", "api.eu.bfl.ai", "api.us.bfl.ai",
}
ALLOWED_DOWNLOAD = re.compile(r"^delivery[a-z0-9.-]*\.bfl\.ai$")   # image results only, no key is sent there
MAX_BYTES = 80_000_000


class NetError(Exception):
    """kind: before_send (nothing left this machine) | after_send (reply lost, may have been charged) | http
    actual: the cost, when a provider that refused the work says what it charged."""

    def __init__(self, kind, message, status=None, code=None, body=None, actual=None, retry_after=None):
        super().__init__(message)
        self.kind, self.status, self.code, self.body, self.actual, self.retry_after = kind, status, code, body, actual, retry_after


# The provider answered that it did not start: bad key, bad request, no credit, too many requests.
# Everything else that is not a success (5xx, 408, 409, redirects) may hide finished work.
REFUSED_BEFORE_WORK = {400, 401, 402, 403, 404, 405, 406, 413, 415, 422, 429, 451}


def _test_base(host):
    """Tests point a provider at a local fake server. Only loopback is accepted."""
    v = os.environ.get("STUDIO_FAKE_PROVIDERS")
    if not v:
        return None
    assert urlsplit(v).hostname == "127.0.0.1", "fake providers must be local"
    return v.rstrip("/") + "/" + host


def request(method: str, url: str, *, key_header: tuple[str, str] | None = None, json=None, data=None, files=None, params=None,
            timeout=60.0, headers=None, download=False) -> httpx.Response:
    u = urlsplit(url)
    ok = u.scheme == "https" and (u.hostname in ALLOWED or (download and key_header is None and ALLOWED_DOWNLOAD.match(u.hostname or "")))
    if not ok:
        raise NetError("before_send", f"blocked: {u.hostname} is not an approved provider host")
    base = _test_base(u.hostname)
    target = base + u.path + (f"?{u.query}" if u.query else "") if base else url
    h = dict(headers or {})
    if key_header:
        h[key_header[0]] = key_header[1]
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=15.0), follow_redirects=False, verify=True) as c:
            r = c.request(method, target, json=json, data=data, files=files, params=params, headers=h)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as e:
        raise NetError("before_send", f"could not reach {u.hostname}: {type(e).__name__}") from None
    except httpx.HTTPError as e:
        raise NetError("after_send", f"connection to {u.hostname} was lost after sending: {type(e).__name__}") from None
    if len(r.content) > MAX_BYTES:
        raise NetError("after_send", "the reply was larger than the studio accepts")
    if r.status_code >= 300:
        code, msg = _error(r)
        raise NetError("http", scrub(msg, key_header[1] if key_header else None), r.status_code, code, None, retry_after=_seconds(r.headers.get("retry-after")))
    return r


def _seconds(v):
    try:
        return max(0.0, min(float(v), 3600.0)) if v else None
    except ValueError:
        return None


def _error(r):
    try:
        j = r.json()
    except Exception:
        return None, f"HTTP {r.status_code}"
    d = j.get("detail") or j.get("error") or j if isinstance(j, dict) else j
    if isinstance(d, list):
        return "validation_error", f"HTTP {r.status_code}: " + "; ".join(str(x.get("msg", x))[:160] for x in d[:3] if isinstance(x, dict))
    if isinstance(d, dict):
        code = d.get("code") if isinstance(d.get("code"), str) else d.get("status") or d.get("type")
        return code, f"HTTP {r.status_code}: {str(d.get('message') or d)[:400]}"
    return None, f"HTTP {r.status_code}: {str(d)[:400]}"


def scrub(text: str, key: str | None = None) -> str:
    if key and len(key) > 6:
        text = text.replace(key, "[key]")
    return re.sub(r"(sk[-_][A-Za-z0-9_-]{8,}|AIza[A-Za-z0-9_-]{10,}|Bearer\s+\S+)", "[key]", text)
