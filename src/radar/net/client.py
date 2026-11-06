"""THE WALL, outbound layer: the only HTTP client the crawler may use.

- GET and HEAD only. Any other method raises before a socket opens.
- Host allowlist from config/sources.yaml. MANUAL / DROP / HARD_REJECT hosts and
  unlisted hosts are refused.
- robots.txt honored literally for our declared UA, and also for the Anthropic
  agent tokens in sources.yaml `robots_also_honor` (fetched text goes to an LLM).
- Per-host delay = max(robots Crawl-delay, source crawl_delay, RADAR_MIN_HOST_DELAY_S).
- Bot walls (challenge pages, 403 walls, CAPTCHAs) raise BotWall. The caller marks the
  host MANUAL and stops. There is no retry path that changes identity or evades.
- Every request, allowed or refused, is written to egress_log.
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from radar import db
from radar.settings import get_settings, sources_config, wall_config

AUTOMATABLE = {"CRAWL", "BULK", "API", "RSS"}
MAX_BYTES = 60 * 1024 * 1024
CHALLENGE_MARKERS = (
    "cf-chl", "challenge-platform", "attention required", "just a moment...",
    "captcha", "recaptcha", "hcaptcha", "turnstile", "access denied", "request unsuccessful. incapsula",
    "_incapsula_resource", "akamai", "you have been blocked",
)


class RefusedRequest(Exception):
    """Policy refusal: method, host, or robots. Nothing was sent (except robots.txt)."""


class BotWall(Exception):
    """The host presented a bot wall. Mark MANUAL; never evade."""


class HostThrottled(Exception):
    """429 or repeated 5xx: stop this host for this run."""


@dataclass
class Response:
    url: str
    status: int
    content: bytes
    content_type: str
    sha256: str

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


def _host_verdicts() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for src in sources_config()["verdicts"]:
        for h in src.get("hosts", []):
            out[h.lower()] = src
    return out


def host_verdict(host: str) -> dict | None:
    return _host_verdicts().get(host.lower())


def _log(method: str, url: str, status: int | None, n: int | None, sha: str | None,
         robots: str | None, purpose: str | None, error: str | None) -> None:
    if urlsplit(url).scheme not in ("http", "https"):
        url = f"[{urlsplit(url).scheme or 'unknown'} link refused]"  # never store what a mailto/tel link pointed at
    with db.connect() as con:
        con.execute(
            "INSERT INTO egress_log VALUES (now(), ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [method, url, urlsplit(url).hostname or "", status, n, sha, robots, purpose, error],
        )


class PoliteClient:
    def __init__(self, purpose: str = "crawl") -> None:
        s = get_settings()
        self.purpose = purpose
        self.ua = s.user_agent
        self.ua_token = self.ua.split("/")[0]
        self.min_delay = s.min_host_delay_s
        self._robots: dict[str, RobotFileParser | None] = {}
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()
        self._http = httpx.Client(
            headers={"User-Agent": self.ua, "Accept": "*/*"},
            follow_redirects=False,
            timeout=httpx.Timeout(60.0, connect=20.0),
        )

    def close(self) -> None:
        self._http.close()

    # ---------------- policy ----------------
    def _check_method(self, method: str) -> None:
        if method.upper() not in wall_config()["crawler_methods"]:
            raise RefusedRequest(f"method {method} is not allowed (GET/HEAD only)")

    def _check_host(self, url: str) -> dict:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            raise RefusedRequest(f"scheme {parts.scheme!r} refused")
        v = host_verdict(parts.hostname or "")
        if v is None:
            raise RefusedRequest(f"host {parts.hostname} is not on the allowlist (config/sources.yaml)")
        if v["verdict"] not in AUTOMATABLE:
            raise RefusedRequest(f"host {parts.hostname} verdict is {v['verdict']}")
        return v

    def _robots_for(self, url: str) -> RobotFileParser | None:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin in self._robots:
            return self._robots[origin]
        robots_url = origin + "/robots.txt"
        rp: RobotFileParser | None = RobotFileParser()
        try:
            self._wait(parts.hostname or "", 0.0)
            r = self._http.get(robots_url)
            body = r.content[:512_000]
            _log("GET", robots_url, r.status_code, len(body), hashlib.sha256(body).hexdigest(), "robots", self.purpose, None)
            if r.status_code in (401, 403):
                rp.disallow_all = True  # RFC 9309: unreachable due to auth -> assume disallow
            elif r.status_code >= 500 or r.status_code == 429:
                rp.disallow_all = True  # server error -> assume complete disallow
            elif r.status_code >= 400:
                rp.allow_all = True  # 4xx (not found) -> no restrictions
            elif r.status_code in (301, 302, 303, 307, 308):
                rp.allow_all = True  # robots redirect off-origin: treat as absent, but host checks still apply
            else:
                rp.parse(body.decode("utf-8", errors="replace").splitlines())
                rp.modified()
        except httpx.HTTPError as e:
            _log("GET", robots_url, None, None, None, "robots", self.purpose, repr(e)[:300])
            rp = None  # unreachable: refuse this run
        self._robots[origin] = rp
        return rp

    def robots_allows(self, url: str) -> tuple[bool, float]:
        rp = self._robots_for(url)
        if rp is None:
            return False, self.min_delay
        tokens = [self.ua_token] + list(sources_config().get("robots_also_honor", []))
        allowed = all(rp.can_fetch(t, url) for t in tokens)
        delays = [rp.crawl_delay(t) for t in [self.ua_token, "*"]]
        delay = max([float(d) for d in delays if d] + [0.0])
        return allowed, delay

    def _wait(self, host: str, delay: float) -> None:
        with self._lock:
            last = self._last.get(host, 0.0)
            now = time.monotonic()
            wait = max(0.0, last + delay - now)
            self._last[host] = now + wait
        if wait:
            time.sleep(wait)

    # ---------------- request ----------------
    def request(self, method: str, url: str, *, max_redirects: int = 5) -> Response:
        self._check_method(method)
        for _ in range(max_redirects + 1):
            try:
                v = self._check_host(url)
            except RefusedRequest as e:
                _log(method, url, None, None, None, None, self.purpose, f"refused: {e}")
                raise
            allowed, robots_delay = self.robots_allows(url)
            if not allowed:
                _log(method, url, None, None, None, "disallowed", self.purpose, "refused: robots")
                raise RefusedRequest(f"robots.txt disallows {url}")
            delay = max(self.min_delay, robots_delay, float(v.get("crawl_delay") or 0))
            host = urlsplit(url).hostname or ""
            self._wait(host, delay)
            try:
                with self._http.stream(method, url) as r:
                    chunks, size = [], 0
                    for chunk in r.iter_bytes():
                        size += len(chunk)
                        if size > MAX_BYTES:
                            raise RefusedRequest(f"{url} exceeds {MAX_BYTES} bytes")
                        chunks.append(chunk)
                    body = b"".join(chunks)
                    status, headers = r.status_code, r.headers
            except httpx.HTTPError as e:
                _log(method, url, None, None, None, "allowed", self.purpose, repr(e)[:300])
                raise
            digest = hashlib.sha256(body).hexdigest()
            _log(method, url, status, len(body), digest, "allowed", self.purpose, None)
            if status in (301, 302, 303, 307, 308) and "location" in headers:
                url = urljoin(url, headers["location"])
                continue
            ctype = headers.get("content-type", "")
            if status == 403 or ("html" in ctype and _looks_like_wall(body)):
                raise BotWall(f"{host} returned {status} with a bot wall")
            if status == 429 or status >= 500:
                raise HostThrottled(f"{host} returned {status}")
            return Response(url=url, status=status, content=body, content_type=ctype, sha256=digest)
        raise RefusedRequest(f"too many redirects for {url}")

    def get(self, url: str) -> Response:
        return self.request("GET", url)

    def head(self, url: str) -> Response:
        return self.request("HEAD", url)


def _looks_like_wall(body: bytes) -> bool:
    head = body[:20_000].decode("utf-8", errors="ignore").lower()
    if len(body) > 200_000:
        return False  # real content pages are large; challenge pages are small
    hits = [m for m in CHALLENGE_MARKERS if m in head]
    # "akamai"/"captcha" alone appear in normal pages' scripts; require a challenge shape.
    strong = {"cf-chl", "challenge-platform", "just a moment...", "you have been blocked",
              "_incapsula_resource", "request unsuccessful. incapsula", "attention required"}
    return bool(strong.intersection(hits)) or ("access denied" in hits and len(body) < 10_000)
