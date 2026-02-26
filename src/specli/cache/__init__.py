"""Disk-based response caching for specli.

This package provides :class:`ResponseCache`, a transparent caching layer
that stores successful HTTP GET responses to disk using :mod:`diskcache`.
Cached entries are keyed by HTTP method, URL, and query parameters with a
configurable TTL.

The cache is consumed by :class:`~specli.client.sync_client.SyncClient`
and is controlled by the ``cache`` section of a profile's configuration
(:class:`~specli.models.CacheConfig`).
"""

from specli.cache.cache import ResponseCache

__all__ = ["ResponseCache"]
