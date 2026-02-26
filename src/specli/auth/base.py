"""Abstract base class for authentication plugins.

This module defines the two foundational types of the auth subsystem:

- :class:`AuthResult` -- a plain container for the HTTP headers, query
  parameters, and cookies that an auth plugin produces.
- :class:`AuthPlugin` -- the abstract base class that every authentication
  strategy must extend.

To implement a new auth strategy, subclass :class:`AuthPlugin`, set the
:attr:`~AuthPlugin.auth_type` property, and implement
:meth:`~AuthPlugin.authenticate`.  Optionally override
:meth:`~AuthPlugin.refresh` for token-refresh logic and
:meth:`~AuthPlugin.validate_config` for upfront config validation.

See Also:
    :mod:`specli.auth.manager` for plugin registration and dispatch.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from specli.models import AuthConfig


class AuthResult:
    """Container for authentication artifacts to inject into HTTP requests.

    After a plugin authenticates, the resulting headers, query parameters,
    and cookies are collected here and later merged into outgoing requests
    by :class:`~specli.client.sync_client.SyncClient` or
    :class:`~specli.client.async_client.AsyncClient`.

    Args:
        headers: HTTP headers to add (e.g. ``{"Authorization": "Bearer ..."}``).
        params: Query-string parameters to add (e.g. ``{"api_key": "..."}``).
        cookies: Cookies to add (serialised into a ``Cookie`` header by the client).

    Example::

        result = AuthResult(headers={"Authorization": "Bearer tok123"})
        assert result.headers["Authorization"] == "Bearer tok123"
    """

    def __init__(
        self,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
    ):
        self.headers = headers or {}
        self.params = params or {}
        self.cookies = cookies or {}


class AuthPlugin(ABC):
    """Abstract base class for authentication plugins.

    Every concrete auth strategy (API key, bearer token, OAuth2, etc.) must
    subclass this and provide:

    1. An :attr:`auth_type` property returning a unique string identifier
       (e.g. ``"api_key"``, ``"bearer"``, ``"oauth2_auth_code"``).
    2. An :meth:`authenticate` implementation that resolves credentials from
       the supplied :class:`~specli.models.AuthConfig` and returns an
       :class:`AuthResult`.

    Plugins are registered with :class:`~specli.auth.manager.AuthManager`
    and looked up by their ``auth_type`` at runtime.
    """

    @property
    @abstractmethod
    def auth_type(self) -> str:
        """Return the unique auth type identifier this plugin handles.

        Returns:
            A lowercase string such as ``"api_key"``, ``"bearer"``, or
            ``"oauth2_client_credentials"``.
        """
        ...

    @abstractmethod
    def authenticate(self, auth_config: AuthConfig) -> AuthResult:
        """Resolve credentials and return auth artifacts for HTTP requests.

        Implementations should use ``auth_config.resolve_credential()`` to
        turn the configured ``source`` value into an actual credential string,
        then wrap the result in an :class:`AuthResult`.

        Args:
            auth_config: The authentication section of the active profile.

        Returns:
            An :class:`AuthResult` containing headers, params, and/or cookies
            to inject into outgoing requests.

        Raises:
            AuthError: If credentials cannot be resolved or are invalid.
        """
        ...

    def refresh(self, auth_config: AuthConfig) -> AuthResult:
        """Refresh expired credentials and return updated auth artifacts.

        The default implementation simply re-authenticates from scratch.
        Subclasses that support token refresh (e.g. OAuth2) should override
        this to use a refresh token instead.

        Args:
            auth_config: The authentication section of the active profile.

        Returns:
            A fresh :class:`AuthResult`.
        """
        return self.authenticate(auth_config)

    def validate_config(self, auth_config: AuthConfig) -> list[str]:
        """Validate the auth configuration before use.

        Override this to perform upfront checks (e.g. required fields
        present, URLs well-formed) and return human-readable error messages.

        Args:
            auth_config: The authentication section to validate.

        Returns:
            A list of error message strings.  An empty list means the
            configuration is valid.
        """
        return []
