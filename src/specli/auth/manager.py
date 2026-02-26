"""Auth manager -- registry and dispatcher for auth plugins.

The :class:`AuthManager` is the central coordinator of the authentication
subsystem.  It maintains a mapping from auth-type strings (``"api_key"``,
``"bearer"``, ``"oauth2_client_credentials"``, etc.) to concrete
:class:`~specli.auth.base.AuthPlugin` instances and exposes a single
:meth:`~AuthManager.authenticate` method that the HTTP clients call.

For most use cases, call :func:`create_default_manager` to get a manager
pre-loaded with every built-in plugin.

See Also:
    :class:`~specli.auth.base.AuthPlugin` -- the plugin interface.
    :class:`~specli.client.sync_client.SyncClient` -- consumes the
    :class:`~specli.auth.base.AuthResult` produced here.
"""

from __future__ import annotations

from specli.auth.base import AuthPlugin, AuthResult
from specli.exceptions import AuthError
from specli.models import Profile


class AuthManager:
    """Registry and dispatcher for authentication plugins.

    Plugins are registered by their :attr:`~AuthPlugin.auth_type` string.
    When :meth:`authenticate` is called with a
    :class:`~specli.models.Profile`, the manager looks up the
    appropriate plugin and delegates credential resolution to it.

    Example::

        from specli.auth import AuthManager
        from specli.plugins.bearer import BearerAuthPlugin

        manager = AuthManager()
        manager.register(BearerAuthPlugin())
        result = manager.authenticate(profile)
    """

    def __init__(self) -> None:
        self._plugins: dict[str, AuthPlugin] = {}

    def register(self, plugin: AuthPlugin) -> None:
        """Register an auth plugin, keyed by its :attr:`~AuthPlugin.auth_type`.

        If a plugin for the same type is already registered it is silently
        replaced.

        Args:
            plugin: The plugin instance to register.
        """
        self._plugins[plugin.auth_type] = plugin

    def get_plugin(self, auth_type: str) -> AuthPlugin:
        """Retrieve a registered plugin by its auth type identifier.

        Args:
            auth_type: The auth type string (e.g. ``"bearer"``).

        Returns:
            The :class:`~specli.auth.base.AuthPlugin` registered for
            *auth_type*.

        Raises:
            AuthError: If no plugin is registered for *auth_type*.
        """
        plugin = self._plugins.get(auth_type)
        if plugin is None:
            available = ", ".join(sorted(self._plugins)) or "(none)"
            raise AuthError(
                f"No auth plugin registered for type '{auth_type}'. "
                f"Available types: {available}"
            )
        return plugin

    def authenticate(self, profile: Profile) -> AuthResult:
        """Authenticate using the profile's auth configuration.

        Looks up the plugin matching ``profile.auth.type`` and delegates to
        its :meth:`~AuthPlugin.authenticate` method.

        Args:
            profile: The active connection profile.

        Returns:
            An :class:`~specli.auth.base.AuthResult` with headers,
            params, and cookies to inject.  Returns an empty
            :class:`~specli.auth.base.AuthResult` when the profile
            has no auth section.

        Raises:
            AuthError: If the auth type has no registered plugin, or if the
                plugin itself raises an authentication error.
        """
        if profile.auth is None:
            return AuthResult()
        plugin = self.get_plugin(profile.auth.type)
        return plugin.authenticate(profile.auth)

    def list_types(self) -> list[str]:
        """Return the identifiers of all registered auth types.

        Returns:
            A sorted list of auth type strings (e.g.
            ``["api_key", "basic", "bearer"]``).
        """
        return sorted(self._plugins.keys())


def create_default_manager() -> AuthManager:
    """Create an :class:`AuthManager` pre-loaded with all built-in plugins.

    The following plugins are registered:

    - ``api_key`` -- static API key in header or query param.
    - ``api_key_gen`` -- generated/rotated API keys.
    - ``basic`` -- HTTP Basic authentication.
    - ``bearer`` -- static bearer token.
    - ``browser_login`` -- browser-based interactive login.
    - ``device_code`` -- OAuth2 device-code flow.
    - ``manual_token`` -- manually pasted token.
    - ``oauth2_auth_code`` -- OAuth2 authorization-code flow.
    - ``oauth2_client_credentials`` -- OAuth2 client-credentials flow.
    - ``openid_connect`` -- OpenID Connect discovery + auth-code flow.

    Returns:
        A fully initialised :class:`AuthManager`.
    """
    from specli.plugins.api_key import APIKeyAuthPlugin
    from specli.plugins.api_key_gen import APIKeyGenPlugin
    from specli.plugins.basic import BasicAuthPlugin
    from specli.plugins.bearer import BearerAuthPlugin
    from specli.plugins.browser_login import BrowserLoginPlugin
    from specli.plugins.device_code import DeviceCodePlugin
    from specli.plugins.manual_token import ManualTokenPlugin
    from specli.plugins.oauth2_auth_code import OAuth2AuthCodePlugin
    from specli.plugins.oauth2_client_credentials import (
        OAuth2ClientCredentialsPlugin,
    )
    from specli.plugins.openid_connect import OpenIDConnectPlugin

    manager = AuthManager()
    manager.register(APIKeyAuthPlugin())
    manager.register(BearerAuthPlugin())
    manager.register(BasicAuthPlugin())
    manager.register(OAuth2ClientCredentialsPlugin())
    manager.register(OAuth2AuthCodePlugin())
    manager.register(OpenIDConnectPlugin())
    manager.register(ManualTokenPlugin())
    manager.register(BrowserLoginPlugin())
    manager.register(APIKeyGenPlugin())
    manager.register(DeviceCodePlugin())
    return manager
