"""Central client for the Koruxa Player API.

Every request goes through KoruxaAPI.get(), so rate limiting, caching, retries
and token errors are handled in one place.

There are no public docs; these facts come from the API's own responses:
- Auth: `Authorization: Bearer <token>`.
- Each response reports `rate_limit_per_hour` (240 per token) and
  `cache_seconds` (60 s for /me, 600 s for /me/actions).
- A bad or revoked token gets HTTP 401.
- Unknown routes redirect (303) to the login page.

Tokens are never logged; logs refer to a short SHA-256 fingerprint instead.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

BASE_URL = "https://koruxa.com/api/public/"
USER_AGENT = "koruxa-discord-bot (personal tracker)"

DEFAULT_LIMIT_PER_HOUR = 240
LIMIT_SAFETY = 0.9  # use at most 90% of the advertised per-token limit
BURST = 10  # requests a token may make back to back before pacing kicks in
MAX_ATTEMPTS = 4
DEFAULT_CACHE_SECONDS = 30
MAX_CACHE_SECONDS = 600


class KoruxaError(Exception):
    """The API returned an error or could not be reached."""


class TokenRevoked(KoruxaError):
    """HTTP 401/403 or an invalid-token body: the token is wrong or was revoked.

    Note: the clan endpoints also answer 401 to valid *personal* tokens, so only
    treat this as "revoked" for the /me endpoints.
    """


class RateLimited(KoruxaError):
    def __init__(self, retry_after: float):
        super().__init__(f"rate limited, retry in {retry_after:.0f}s")
        self.retry_after = retry_after


def fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:10]


def _backoff(attempt: int, base: float = 2.0) -> float:
    return min(base * 2**attempt, 300.0) * random.uniform(0.8, 1.2)


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(float(value), 0.0)
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
        return max((when - datetime.now(timezone.utc)).total_seconds(), 0.0)
    except (TypeError, ValueError):
        return None


class _Bucket:
    """Per-token rate limiter: refills at `per_hour`, holds at most BURST requests."""

    def __init__(self, per_hour: float):
        self.per_hour = per_hour
        self.tokens = float(BURST)
        self.updated = time.monotonic()
        self.blocked_until = 0.0
        self.lock = asyncio.Lock()

    def set_limit(self, advertised_per_hour: float) -> None:
        self.per_hour = max(1.0, advertised_per_hour * LIMIT_SAFETY)

    def block_for(self, seconds: float) -> None:
        self.blocked_until = max(self.blocked_until, time.monotonic() + seconds)

    async def acquire(self, max_wait: float) -> None:
        deadline = time.monotonic() + max_wait
        async with self.lock:
            while True:
                now = time.monotonic()
                self.tokens = min(BURST, self.tokens + (now - self.updated) * self.per_hour / 3600)
                self.updated = now
                if now < self.blocked_until:
                    wait = self.blocked_until - now
                elif self.tokens >= 1:
                    self.tokens -= 1
                    return
                else:
                    wait = (1 - self.tokens) * 3600 / self.per_hour
                if now + wait > deadline:
                    raise RateLimited(wait)
                await asyncio.sleep(wait)


@dataclass
class _CacheEntry:
    data: dict[str, Any]
    expires: float


class KoruxaAPI:
    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._buckets: dict[str, _Bucket] = {}
        self._cache: dict[tuple, _CacheEntry] = {}
        self._locks: defaultdict[tuple, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=20),
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    def forget(self, token: str) -> None:
        """Drop cached responses for a token (e.g. after /unlink)."""
        fp = fingerprint(token)
        for key in [k for k in self._cache if k[0] == fp]:
            del self._cache[key]

    async def get_public(self, path: str, *, max_wait: float = 15.0, fresh: bool = False) -> dict[str, Any]:
        """GET an endpoint that needs no token (e.g. /worldboss)."""
        return await self.get(None, path, max_wait=max_wait, fresh=fresh)

    async def get(
        self,
        token: str | None,
        path: str,
        params: dict[str, str] | None = None,
        *,
        max_wait: float = 15.0,
        fresh: bool = False,
    ) -> dict[str, Any]:
        """GET an endpoint, served from cache while the server says the data is unchanged.

        max_wait: how long to wait for a rate-limit slot before raising RateLimited.
        fresh: skip the cache (still stores the result).
        """
        fp = fingerprint(token) if token else "public"
        key = (fp, path, tuple(sorted((params or {}).items())))
        async with self._locks[key]:
            entry = self._cache.get(key)
            if entry and not fresh and entry.expires > time.monotonic():
                return entry.data
            data = await self._request(token, fp, path, params, max_wait)
            try:
                ttl = min(float(data.get("cache_seconds", DEFAULT_CACHE_SECONDS)), MAX_CACHE_SECONDS)
            except (TypeError, ValueError):
                ttl = DEFAULT_CACHE_SECONDS
            self._cache[key] = _CacheEntry(data, time.monotonic() + ttl)
            return data

    async def _send(self, path, params, headers) -> tuple[int, str | None, Any]:
        assert self._session is not None, "KoruxaAPI.start() was not called"
        async with self._session.get(
            BASE_URL + path, params=params, headers=headers, allow_redirects=False
        ) as resp:
            data = await resp.json(content_type=None) if resp.status == 200 else None
            return resp.status, resp.headers.get("Retry-After"), data

    async def _request(self, token, fp, path, params, max_wait) -> dict[str, Any]:
        bucket = self._buckets.setdefault(fp, _Bucket(DEFAULT_LIMIT_PER_HOUR * LIMIT_SAFETY))
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        last_error = "no attempts made"

        for attempt in range(MAX_ATTEMPTS):
            await bucket.acquire(max_wait)
            try:
                status, retry_after, data = await self._send(path, params, headers)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
                last_error = type(e).__name__
                log.warning("GET /%s failed (%s), attempt %d/%d", path, last_error, attempt + 1, MAX_ATTEMPTS)
                await asyncio.sleep(_backoff(attempt))
                continue

            if status == 200:
                return self._check_body(data, bucket)
            if status in (401, 403):
                raise TokenRevoked(f"HTTP {status}")
            if status == 429:
                delay = _parse_retry_after(retry_after) or _backoff(attempt, base=30.0)
                bucket.block_for(delay)
                last_error = "429"
                log.warning("429 on /%s (token %s); backing off %.0fs", path, fp, delay)
                continue  # acquire() waits out the block, or raises RateLimited if too long
            if status >= 500:
                last_error = f"HTTP {status}"
                log.warning("GET /%s returned %d, attempt %d/%d", path, status, attempt + 1, MAX_ATTEMPTS)
                await asyncio.sleep(_backoff(attempt))
                continue
            if 300 <= status < 400:
                raise KoruxaError(f"/{path} is not a Player API endpoint")
            raise KoruxaError(f"unexpected HTTP {status}")

        if last_error == "429":
            raise RateLimited(max(bucket.blocked_until - time.monotonic(), 1.0))
        raise KoruxaError(f"giving up on /{path}: {last_error}")

    @staticmethod
    def _check_body(data: Any, bucket: _Bucket) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise KoruxaError("unexpected response format")
        # /me uses "success", other endpoints use "ok".
        if data.get("success") is False or data.get("ok") is False:
            error = str(data.get("error") or "")
            if "token" in error.lower():
                raise TokenRevoked(error)
            raise KoruxaError(str(data.get("message") or error or "API returned an error"))
        limit = data.get("rate_limit_per_hour")
        if isinstance(limit, (int, float)) and limit > 0:
            bucket.set_limit(limit)
        return data
