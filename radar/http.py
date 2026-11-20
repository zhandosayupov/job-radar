"""Polite HTTP client: fixed UA, robots.txt (RFC 9309), <=1 req/s per host, timeouts,
2 retries with backoff on transient errors. 403/429/captcha/robots-disallow -> Blocked.
Never bypasses anything."""
from __future__ import annotations

import re
import time
from collections import Counter
from typing import Callable
from urllib.parse import urlsplit

import httpx

from .robots import Robots

ROBOTS_TOKEN = "job-radar"
MAX_CRAWL_DELAY_S = 30.0  # honor Crawl-delay up to this; beyond it the source is just slow
CHALLENGE_TITLES = ("just a moment", "attention required", "captcha", "are you a robot", "access denied", "verify you are human")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


def is_challenge_page(html_text: str) -> bool:
    """Bot-wall pages (Cloudflare etc.). Only the <title> and challenge scripts count, so a
    normal page with an embedded reCAPTCHA on its apply form is not misread as blocked."""
    head = html_text[:30000]
    m = _TITLE_RE.search(head)
    title = (m.group(1) if m else "").strip().lower()
    if any(t in title for t in CHALLENGE_TITLES):
        return True
    return "/cdn-cgi/challenge-platform/" in head and "cf-chl" in head


class Blocked(Exception):
    """Source refuses us (403/429/captcha/robots). Back off; do not retry this run."""


class FetchError(Exception):
    """Other failures (404, persistent 5xx, network, parse)."""


class Http:
    def __init__(
        self,
        user_agent: str,
        min_interval_s: float = 1.0,
        timeout_s: float = 20.0,
        retries: int = 2,
        backoff_s: float = 2.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] | None = None,
        clock: Callable[[], float] | None = None,
        respect_robots: bool = True,
    ):
        self.user_agent = user_agent
        self.min_interval_s = min_interval_s
        self.retries = retries
        self.backoff_s = backoff_s
        # Resolved at call time so tests can patch radar.http.time.
        self.sleep = sleep or (lambda s: time.sleep(s))
        self.clock = clock or (lambda: time.monotonic())
        self.respect_robots = respect_robots
        self._client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=timeout_s,
            follow_redirects=True,
            transport=transport,
        )
        self._last: dict[str, float] = {}
        self._robots: dict[str, Robots | None] = {}
        self._interval: dict[str, float] = {}  # per-host, raised by Crawl-delay
        self.requests: Counter[str] = Counter()

    def close(self) -> None:
        self._client.close()

    # --- internals ---------------------------------------------------------

    def _throttle(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            wait = self._interval.get(host, self.min_interval_s) - (self.clock() - last)
            if wait > 0:
                self.sleep(wait)
        self._last[host] = self.clock()

    def _raw_get(self, url: str, params: dict | None = None) -> httpx.Response:
        host = urlsplit(url).netloc.lower()
        attempt = 0
        while True:
            self._throttle(host)
            self.requests[host] += 1
            try:
                resp = self._client.get(url, params=params)
            except httpx.TransportError as e:
                if attempt >= self.retries:
                    raise FetchError(f"{type(e).__name__} on {host}") from None
            else:
                if resp.status_code < 500:
                    return resp
                if attempt >= self.retries:
                    raise FetchError(f"HTTP {resp.status_code} on {host}")
            attempt += 1
            self.sleep(self.backoff_s * attempt)

    def _robots_for(self, url: str) -> Robots | None:
        parts = urlsplit(url)
        key = f"{parts.scheme}://{parts.netloc.lower()}"
        if key in self._robots:
            return self._robots[key]
        try:
            resp = self._raw_get(f"{key}/robots.txt")
        except FetchError:
            # RFC 9309: unreachable robots.txt -> assume complete disallow.
            raise Blocked(f"robots.txt unreachable on {parts.netloc}") from None
        rp: Robots | None = None
        if resp.status_code == 200:
            rp = Robots(resp.text, ROBOTS_TOKEN)
            if rp.crawl_delay:
                host = parts.netloc.lower()
                self._interval[host] = max(self.min_interval_s, min(rp.crawl_delay, MAX_CRAWL_DELAY_S))
        # RFC 9309: 4xx -> no restrictions.
        self._robots[key] = rp
        return rp

    # --- public ------------------------------------------------------------

    def get(self, url: str, params: dict | None = None) -> httpx.Response:
        if self.respect_robots:
            rp = self._robots_for(url)
            full = str(httpx.URL(url, params=params)) if params else url
            if rp is not None and not rp.can_fetch(full):
                raise Blocked(f"robots.txt disallows {urlsplit(url).path}")
        resp = self._raw_get(url, params)
        host = urlsplit(url).netloc
        if resp.status_code in (401, 403, 429):
            raise Blocked(f"HTTP {resp.status_code} on {host}")
        if resp.status_code >= 400:
            raise FetchError(f"HTTP {resp.status_code} on {host}")
        ctype = resp.headers.get("content-type", "")
        if "html" in ctype and is_challenge_page(resp.text):
            raise Blocked(f"captcha/challenge page on {host}")
        return resp

    def get_json(self, url: str, params: dict | None = None):
        resp = self.get(url, params)
        try:
            return resp.json()
        except ValueError:
            raise FetchError(f"invalid JSON from {urlsplit(url).netloc}") from None
