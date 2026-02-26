"""Plugin-based authentication system for specli.

This package provides a pluggable authentication framework that supports
multiple auth strategies -- API keys, bearer tokens, basic auth, OAuth2
(client credentials and authorization code), OpenID Connect, and more.

The main entry points are:

- :class:`AuthPlugin` -- abstract base class for implementing new auth strategies.
- :class:`AuthManager` -- registry that maps auth type strings to plugin instances
  and dispatches authentication for a given :class:`~specli.models.Profile`.
- :func:`create_default_manager` -- factory that returns an :class:`AuthManager`
  pre-loaded with all built-in plugins.
- :class:`CredentialStore` -- persistent, per-profile credential storage on disk.

Typical usage::

    from specli.auth import create_default_manager

    manager = create_default_manager()
    auth_result = manager.authenticate(profile)
    # auth_result.headers / .params / .cookies are ready to inject into requests.
"""

from specli.auth.base import AuthPlugin, AuthResult
from specli.auth.credential_store import CredentialEntry, CredentialStore
from specli.auth.manager import AuthManager, create_default_manager

__all__ = [
    "AuthPlugin",
    "AuthResult",
    "AuthManager",
    "CredentialEntry",
    "CredentialStore",
    "create_default_manager",
]
