"""HTTP client module for specli.

Provides synchronous and asynchronous HTTP clients that wrap :mod:`httpx`
with automatic auth injection, plugin hooks, response caching, dry-run
mode, and retry with exponential backoff.

Classes:
    :class:`SyncClient` -- blocking client backed by :class:`httpx.Client`.
    :class:`AsyncClient` -- non-blocking client backed by :class:`httpx.AsyncClient`.

Both clients are designed to be used as context managers and accept the
same core parameters: a :class:`~specli.models.Profile`, an optional
:class:`~specli.auth.manager.AuthManager`, an optional
:class:`~specli.plugins.hooks.HookRunner`, and a ``dry_run`` flag.

Example::

    from specli.client import SyncClient

    with SyncClient(profile, auth_manager=manager) as client:
        resp = client.get("/users")
"""

from specli.client.async_client import AsyncClient
from specli.client.sync_client import SyncClient

__all__ = ["SyncClient", "AsyncClient"]
