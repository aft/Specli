"""Tests for the ResponseCache module."""

from __future__ import annotations

import time

import pytest

from specli.cache import ResponseCache
from specli.models import CacheConfig


@pytest.fixture()
def cache(tmp_path):
    """Create a ResponseCache with default config pointing at tmp_path."""
    config = CacheConfig(enabled=True, ttl_seconds=300)
    c = ResponseCache(tmp_path, config)
    yield c
    c.close()


@pytest.fixture()
def disabled_cache(tmp_path):
    """Create a disabled ResponseCache."""
    config = CacheConfig(enabled=False, ttl_seconds=300)
    c = ResponseCache(tmp_path, config)
    yield c
    c.close()


def _make_response(status_code: int = 200) -> dict:
    """Build a minimal cached response dict."""
    return {
        "status_code": status_code,
        "headers": {"content-type": "application/json"},
        "body": {"id": 1, "name": "test"},
    }


# ------------------------------------------------------------------ #
# Core get/set behaviour
# ------------------------------------------------------------------ #


class TestGetSet:
    def test_set_and_get_for_get_request(self, cache: ResponseCache) -> None:
        """Cache stores and retrieves a GET response."""
        resp = _make_response()
        cache.set("GET", "https://api.example.com/users", None, resp)
        result = cache.get("GET", "https://api.example.com/users", None)
        assert result is not None
        assert result["status_code"] == 200
        assert result["body"] == {"id": 1, "name": "test"}

    def test_get_with_params(self, cache: ResponseCache) -> None:
        """Params are included in cache key."""
        resp = _make_response()
        params = {"page": 1, "limit": 10}
        cache.set("GET", "https://api.example.com/users", params, resp)
        result = cache.get("GET", "https://api.example.com/users", params)
        assert result is not None
        assert result["status_code"] == 200

    def test_cache_miss_returns_none(self, cache: ResponseCache) -> None:
        """A key that was never stored returns None."""
        result = cache.get("GET", "https://api.example.com/missing", None)
        assert result is None


# ------------------------------------------------------------------ #
# Method filtering
# ------------------------------------------------------------------ #


class TestMethodFiltering:
    def test_post_not_cached_on_set(self, cache: ResponseCache) -> None:
        """POST responses are silently ignored by set()."""
        resp = _make_response()
        cache.set("POST", "https://api.example.com/users", None, resp)
        result = cache.get("GET", "https://api.example.com/users", None)
        assert result is None

    def test_post_not_cached_on_get(self, cache: ResponseCache) -> None:
        """get() returns None for non-GET methods even if key exists."""
        resp = _make_response()
        cache.set("GET", "https://api.example.com/users", None, resp)
        result = cache.get("POST", "https://api.example.com/users", None)
        assert result is None

    @pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE", "HEAD"])
    def test_non_get_methods_not_cached(self, cache: ResponseCache, method: str) -> None:
        """Only GET requests should be cached."""
        resp = _make_response()
        cache.set(method, "https://api.example.com/users", None, resp)
        assert cache.get(method, "https://api.example.com/users", None) is None


# ------------------------------------------------------------------ #
# Status code filtering
# ------------------------------------------------------------------ #


class TestStatusFiltering:
    @pytest.mark.parametrize("status", [400, 401, 403, 404, 500, 502, 503])
    def test_non_2xx_not_cached(self, cache: ResponseCache, status: int) -> None:
        """Responses outside 2xx range are not stored."""
        resp = _make_response(status_code=status)
        cache.set("GET", "https://api.example.com/err", None, resp)
        assert cache.get("GET", "https://api.example.com/err", None) is None

    @pytest.mark.parametrize("status", [200, 201, 204, 299])
    def test_2xx_cached(self, cache: ResponseCache, status: int) -> None:
        """All 2xx responses should be cached."""
        resp = _make_response(status_code=status)
        cache.set("GET", "https://api.example.com/ok", None, resp)
        result = cache.get("GET", "https://api.example.com/ok", None)
        assert result is not None
        assert result["status_code"] == status


# ------------------------------------------------------------------ #
# TTL expiry
# ------------------------------------------------------------------ #


class TestTTL:
    def test_ttl_expiry(self, tmp_path) -> None:
        """Entries expire after ttl_seconds."""
        config = CacheConfig(enabled=True, ttl_seconds=1)
        c = ResponseCache(tmp_path, config)
        try:
            resp = _make_response()
            c.set("GET", "https://api.example.com/users", None, resp)
            # Immediately available
            assert c.get("GET", "https://api.example.com/users", None) is not None
            # Wait for expiry
            time.sleep(1.5)
            assert c.get("GET", "https://api.example.com/users", None) is None
        finally:
            c.close()


# ------------------------------------------------------------------ #
# Disabled cache
# ------------------------------------------------------------------ #


class TestDisabled:
    def test_disabled_get_returns_none(self, disabled_cache: ResponseCache) -> None:
        """A disabled cache always returns None from get()."""
        assert disabled_cache.get("GET", "https://api.example.com/users", None) is None

    def test_disabled_set_is_noop(self, disabled_cache: ResponseCache) -> None:
        """set() on a disabled cache does nothing (no error)."""
        resp = _make_response()
        disabled_cache.set("GET", "https://api.example.com/users", None, resp)
        assert disabled_cache.get("GET", "https://api.example.com/users", None) is None

    def test_disabled_stats(self, disabled_cache: ResponseCache) -> None:
        """Stats for a disabled cache report enabled=False."""
        s = disabled_cache.stats()
        assert s == {"enabled": False}


# ------------------------------------------------------------------ #
# Invalidate and clear
# ------------------------------------------------------------------ #


class TestInvalidateAndClear:
    def test_invalidate_removes_specific_entry(self, cache: ResponseCache) -> None:
        """invalidate() removes only the targeted entry."""
        resp = _make_response()
        cache.set("GET", "https://api.example.com/a", None, resp)
        cache.set("GET", "https://api.example.com/b", None, resp)

        cache.invalidate("GET", "https://api.example.com/a", None)

        assert cache.get("GET", "https://api.example.com/a", None) is None
        assert cache.get("GET", "https://api.example.com/b", None) is not None

    def test_clear_removes_all_entries(self, cache: ResponseCache) -> None:
        """clear() empties the entire cache."""
        resp = _make_response()
        cache.set("GET", "https://api.example.com/a", None, resp)
        cache.set("GET", "https://api.example.com/b", None, resp)

        cache.clear()

        assert cache.get("GET", "https://api.example.com/a", None) is None
        assert cache.get("GET", "https://api.example.com/b", None) is None

    def test_invalidate_nonexistent_key_no_error(self, cache: ResponseCache) -> None:
        """invalidate() on a missing key does not raise."""
        cache.invalidate("GET", "https://api.example.com/nope", None)


# ------------------------------------------------------------------ #
# Stats
# ------------------------------------------------------------------ #


class TestStats:
    def test_stats_empty_cache(self, cache: ResponseCache) -> None:
        """Stats for an empty cache show size=0."""
        s = cache.stats()
        assert s["enabled"] is True
        assert s["size"] == 0
        assert s["ttl_seconds"] == 300

    def test_stats_after_inserts(self, cache: ResponseCache) -> None:
        """Size increases as entries are added."""
        resp = _make_response()
        cache.set("GET", "https://api.example.com/a", None, resp)
        cache.set("GET", "https://api.example.com/b", None, resp)
        s = cache.stats()
        assert s["size"] == 2

    def test_stats_directory(self, cache: ResponseCache, tmp_path) -> None:
        """Stats include the expected directory path."""
        s = cache.stats()
        assert s["directory"] == str(tmp_path / "responses")


# ------------------------------------------------------------------ #
# Cache key determinism
# ------------------------------------------------------------------ #


class TestCacheKey:
    def test_key_deterministic(self, cache: ResponseCache) -> None:
        """Same inputs produce the same cache key."""
        key1 = cache._make_key("GET", "https://api.example.com/users", {"page": 1})
        key2 = cache._make_key("GET", "https://api.example.com/users", {"page": 1})
        assert key1 == key2

    def test_key_varies_with_different_params(self, cache: ResponseCache) -> None:
        """Different params produce different cache keys."""
        key1 = cache._make_key("GET", "https://api.example.com/users", {"page": 1})
        key2 = cache._make_key("GET", "https://api.example.com/users", {"page": 2})
        assert key1 != key2

    def test_key_varies_with_different_url(self, cache: ResponseCache) -> None:
        """Different URLs produce different cache keys."""
        key1 = cache._make_key("GET", "https://api.example.com/a", None)
        key2 = cache._make_key("GET", "https://api.example.com/b", None)
        assert key1 != key2

    def test_key_varies_with_different_method(self, cache: ResponseCache) -> None:
        """Different methods produce different cache keys."""
        key1 = cache._make_key("GET", "https://api.example.com/users", None)
        key2 = cache._make_key("POST", "https://api.example.com/users", None)
        assert key1 != key2

    def test_key_method_case_insensitive(self, cache: ResponseCache) -> None:
        """Method casing does not affect the cache key."""
        key1 = cache._make_key("get", "https://api.example.com/users", None)
        key2 = cache._make_key("GET", "https://api.example.com/users", None)
        assert key1 == key2

    def test_key_param_order_independent(self, cache: ResponseCache) -> None:
        """Param order does not affect the cache key (sorted internally)."""
        key1 = cache._make_key("GET", "https://api.example.com/users", {"a": 1, "b": 2})
        key2 = cache._make_key("GET", "https://api.example.com/users", {"b": 2, "a": 1})
        assert key1 == key2

    def test_key_none_params_vs_empty_dict(self, cache: ResponseCache) -> None:
        """None params and empty dict should produce different keys if dict is truthy."""
        key1 = cache._make_key("GET", "https://api.example.com/users", None)
        key2 = cache._make_key("GET", "https://api.example.com/users", {})
        # Empty dict is falsy, so both omit the params segment
        assert key1 == key2


# ------------------------------------------------------------------ #
# Close
# ------------------------------------------------------------------ #


class TestClose:
    def test_close_does_not_error(self, cache: ResponseCache) -> None:
        """Calling close() should not raise any exception."""
        cache.close()

    def test_close_disabled_cache(self, disabled_cache: ResponseCache) -> None:
        """close() on a disabled cache (no internal Cache object) is safe."""
        disabled_cache.close()

    def test_double_close(self, tmp_path) -> None:
        """Calling close() twice should not raise."""
        config = CacheConfig(enabled=True, ttl_seconds=300)
        c = ResponseCache(tmp_path, config)
        c.close()
        c.close()
