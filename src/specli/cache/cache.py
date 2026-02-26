"""Disk-based response caching for GET requests.

Uses :mod:`diskcache` to persist HTTP GET responses on the filesystem with
a configurable time-to-live (TTL).  Only successful (2xx) GET responses
are cached; all other methods and error responses are passed through.

Cache keys are SHA-256 hashes of ``METHOD|URL|sorted_params`` so that
identical requests always resolve to the same entry regardless of
parameter ordering.

See Also:
    :class:`~specli.models.CacheConfig` -- the Pydantic model that
    controls ``enabled`` and ``ttl_seconds``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Optional

import diskcache

from specli.models import CacheConfig


class ResponseCache:
    """Disk-backed cache for HTTP GET responses.

    Stores serialised response dicts (``status_code``, ``headers``,
    ``body``) in a :class:`diskcache.Cache` directory.  Only GET requests
    that returned a 2xx status code are cached.  Entries expire after
    :attr:`~specli.models.CacheConfig.ttl_seconds`.

    Args:
        cache_dir: Root directory for the cache.  A ``responses/``
            subdirectory is created inside it.
        config: Cache configuration (``enabled`` flag and ``ttl_seconds``).

    Example::

        from specli.cache import ResponseCache
        from specli.models import CacheConfig

        cache = ResponseCache("/tmp/api-cache", CacheConfig(enabled=True, ttl_seconds=300))
        cache.set("GET", "https://api.example.com/users", None, {
            "status_code": 200, "headers": {}, "body": [{"id": 1}]
        })
        hit = cache.get("GET", "https://api.example.com/users")
    """

    def __init__(self, cache_dir: str | Path, config: CacheConfig) -> None:
        self._config = config
        self._cache: Optional[diskcache.Cache] = None
        self._cache_dir = Path(cache_dir)
        if config.enabled:
            self._cache = diskcache.Cache(str(self._cache_dir / "responses"))

    def get(self, method: str, url: str, params: Optional[dict] = None) -> Optional[dict]:
        """Look up a cached response.

        Args:
            method: HTTP method.  Non-GET methods always return ``None``.
            url: The full request URL.
            params: Query parameters used to form the cache key.

        Returns:
            A ``dict`` with ``status_code``, ``headers``, and ``body``
            keys on a cache hit, or ``None`` on a miss or when caching is
            disabled.
        """
        if not self._config.enabled or self._cache is None:
            return None
        if method.upper() != "GET":
            return None

        key = self._make_key(method, url, params)
        return self._cache.get(key)

    def set(
        self,
        method: str,
        url: str,
        params: Optional[dict],
        response_data: dict,
    ) -> None:
        """Store a response in the cache.

        Only caches GET requests that returned a 2xx status code.  All
        other methods and error responses are silently ignored.

        Args:
            method: HTTP method.  Non-GET methods are silently skipped.
            url: The full request URL.
            params: Query parameters used to form the cache key.
            response_data: A ``dict`` with ``status_code``, ``headers``,
                and ``body`` keys.
        """
        if not self._config.enabled or self._cache is None:
            return
        if method.upper() != "GET":
            return
        # Only cache 2xx responses
        status = response_data.get("status_code", 0)
        if not (200 <= status < 300):
            return

        key = self._make_key(method, url, params)
        self._cache.set(key, response_data, expire=self._config.ttl_seconds)

    def invalidate(self, method: str, url: str, params: Optional[dict] = None) -> None:
        """Remove a specific cache entry by its key components.

        Args:
            method: HTTP method used to form the cache key.
            url: The full request URL.
            params: Query parameters used to form the cache key.
        """
        if self._cache is None:
            return
        key = self._make_key(method, url, params)
        self._cache.delete(key)

    def clear(self) -> None:
        """Remove all entries from the cache."""
        if self._cache is not None:
            self._cache.clear()

    def stats(self) -> dict[str, Any]:
        """Return cache statistics.

        Returns:
            A ``dict`` with ``enabled`` (bool), and when enabled:
            ``size`` (number of entries), ``directory`` (str path), and
            ``ttl_seconds`` (int).
        """
        if self._cache is None:
            return {"enabled": False}
        return {
            "enabled": True,
            "size": len(self._cache),
            "directory": str(self._cache_dir / "responses"),
            "ttl_seconds": self._config.ttl_seconds,
        }

    def close(self) -> None:
        """Close the underlying :class:`diskcache.Cache` and release resources."""
        if self._cache is not None:
            self._cache.close()

    def _make_key(self, method: str, url: str, params: Optional[dict]) -> str:
        """Generate a cache key from method, URL, and sorted params."""
        parts = [method.upper(), url]
        if params:
            parts.append(json.dumps(params, sort_keys=True))
        raw = "|".join(parts)
        return hashlib.sha256(raw.encode()).hexdigest()
