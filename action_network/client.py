"""Low-level HTTP client for Action Network's web API.

Depends only on `requests` + stdlib so the existing GitHub Action archiver can
reuse it without new dependencies. Handles:
  * a short-TTL in-memory cache (many agents asking about one game = one request)
  * a global minimum interval between upstream requests (be polite)
  * retries with backoff on 429 / 5xx / network errors
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional, Tuple
from urllib.parse import quote, urlencode

import requests

from . import config


class ActionNetworkError(RuntimeError):
    """Upstream request failed or returned something unusable."""

    def __init__(self, message: str, status_code: Optional[int] = None, url: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.url = url


class UnknownLeagueError(ValueError):
    pass


class _TTLCache:
    def __init__(self) -> None:
        self._data: Dict[str, Tuple[float, float, Any]] = {}  # key -> (expires, fetched_at, value)
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Tuple[float, Any]]:
        with self._lock:
            hit = self._data.get(key)
            if not hit:
                return None
            expires, fetched_at, value = hit
            if time.time() > expires:
                self._data.pop(key, None)
                return None
            return fetched_at, value

    def set(self, key: str, value: Any, ttl: float) -> float:
        now = time.time()
        with self._lock:
            self._data[key] = (now + ttl, now, value)
        return now

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


class ActionNetworkClient:
    def __init__(
        self,
        session: Optional[requests.Session] = None,
        min_interval: float = config.MIN_REQUEST_INTERVAL_SECONDS,
        timeout: float = config.REQUEST_TIMEOUT_SECONDS,
        max_retries: int = config.MAX_RETRIES,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.update(config.HEADERS)
        self.min_interval = min_interval
        self.timeout = timeout
        self.max_retries = max_retries
        self.cache = _TTLCache()
        self._rate_lock = threading.Lock()
        self._last_request = 0.0
        # Called with (url, payload, fetched_at) after every fresh upstream fetch.
        self.on_fetch = None

    # ------------------------------------------------------------------ core
    def get_json(self, path: str, params: Optional[dict] = None, ttl: float = 0.0) -> Tuple[Any, datetime, bool]:
        """GET `path` (relative to API_BASE). Returns (payload, fetched_at_utc, from_cache)."""
        url = f"{config.API_BASE}/{path.lstrip('/')}"
        if params:
            url = f"{url}?{urlencode(params, safe=',')}"

        if ttl > 0:
            hit = self.cache.get(url)
            if hit:
                fetched_at, payload = hit
                return payload, datetime.fromtimestamp(fetched_at, timezone.utc), True

        payload = self._fetch(url)
        fetched_at = self.cache.set(url, payload, ttl) if ttl > 0 else time.time()
        if self.on_fetch:
            try:
                self.on_fetch(url, payload, fetched_at)
            except Exception:  # history must never break a live answer
                pass
        return payload, datetime.fromtimestamp(fetched_at, timezone.utc), False

    def _throttle(self) -> None:
        with self._rate_lock:
            wait = self.min_interval - (time.time() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.time()

    def _fetch(self, url: str) -> Any:
        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                resp = self.session.get(url, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = exc
            else:
                if resp.status_code == 200:
                    try:
                        return resp.json()
                    except ValueError as exc:
                        raise ActionNetworkError("Upstream returned non-JSON", resp.status_code, url) from exc
                if resp.status_code not in (429, 500, 502, 503, 504):
                    raise ActionNetworkError(
                        f"Upstream returned HTTP {resp.status_code}", resp.status_code, url
                    )
                last_error = ActionNetworkError(f"Upstream returned HTTP {resp.status_code}", resp.status_code, url)
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    time.sleep(min(int(retry_after), 30))
            if attempt < self.max_retries:
                time.sleep(min(2 ** attempt, 8))
        if isinstance(last_error, ActionNetworkError):
            raise last_error
        raise ActionNetworkError(f"Request failed: {last_error}", None, url)

    # ------------------------------------------------------------ endpoints
    def scoreboard(
        self, league: str, date: str, book_ids: Optional[Iterable[int]] = None, ttl: Optional[float] = None
    ) -> Tuple[dict, datetime, bool]:
        """Raw scoreboard for one league on one date (YYYYMMDD)."""
        spec = league_spec(league)
        params = {
            "period": "game",
            "bookIds": ",".join(str(b) for b in (book_ids or config.DEFAULT_BOOK_IDS)),
            "date": date,
        }
        params.update(spec["params"])
        return self.get_json(
            f"scoreboard/{spec['path']}",
            params,
            ttl=config.SCOREBOARD_TTL_SECONDS if ttl is None else ttl,
        )

    def futures_available(self, league_id: int) -> Tuple[dict, datetime, bool]:
        return self.get_json(f"leagues/{league_id}/futures/available", ttl=config.FUTURES_TTL_SECONDS)

    def futures_market(self, league_id: int, bet_type: str) -> Tuple[dict, datetime, bool]:
        return self.get_json(
            f"leagues/{league_id}/futures/{quote(bet_type, safe='')}", ttl=config.FUTURES_TTL_SECONDS
        )


def league_spec(league: str) -> dict:
    key = (league or "").strip().lower()
    if key not in config.LEAGUES:
        raise UnknownLeagueError(f"Unknown league '{league}'. Known: {', '.join(sorted(config.LEAGUES))}")
    return config.LEAGUES[key]
