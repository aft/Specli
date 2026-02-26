"""OpenID Connect auth plugin -- discovers endpoints and delegates to auth code flow.

This module provides :class:`OpenIDConnectPlugin`, which implements the
``openid_connect`` auth type. It fetches the provider's discovery
document from the ``openid_connect_url`` (typically
``https://provider/.well-known/openid-configuration``), extracts
``authorization_endpoint`` and ``token_endpoint``, and delegates to
:class:`~specli.plugins.oauth2_auth_code.plugin.OAuth2AuthCodePlugin`
for the actual PKCE-based authorization code flow.

The discovery result is cached for the lifetime of the plugin instance
to avoid redundant HTTP requests.

See Also:
    :class:`specli.auth.base.AuthPlugin` for the base interface.
    :class:`specli.plugins.oauth2_auth_code.plugin.OAuth2AuthCodePlugin`
    for the delegated flow.
"""

from __future__ import annotations

from typing import Any

import httpx

from specli.auth.base import AuthPlugin, AuthResult
from specli.plugins.oauth2_auth_code import OAuth2AuthCodePlugin
from specli.exceptions import AuthError
from specli.models import AuthConfig


class OpenIDConnectPlugin(AuthPlugin):
    """Authenticate via OpenID Connect.

    Fetches the discovery document from ``auth_config.openid_connect_url``,
    extracts the ``authorization_endpoint`` and ``token_endpoint``, and
    delegates the actual authentication to the OAuth2 Authorization Code
    flow with PKCE.
    """

    def __init__(self) -> None:
        """Initialize with a fresh :class:`OAuth2AuthCodePlugin` delegate and empty cache."""
        self._auth_code_plugin = OAuth2AuthCodePlugin()
        self._discovered_config: AuthConfig | None = None

    @property
    def auth_type(self) -> str:
        return "openid_connect"

    def authenticate(self, auth_config: AuthConfig) -> AuthResult:
        """Discover endpoints and authenticate via OAuth2 Authorization Code + PKCE.

        Resolves the OIDC discovery document (cached after first call),
        then delegates to the underlying
        :class:`~specli.plugins.oauth2_auth_code.plugin.OAuth2AuthCodePlugin`.

        Args:
            auth_config: Profile auth configuration. Must include
                ``openid_connect_url``. Optionally ``client_id_source``,
                ``client_secret_source``, and ``scopes``.

        Returns:
            An :class:`~specli.auth.base.AuthResult` containing an
            ``Authorization: Bearer <token>`` header.

        Raises:
            AuthError: If the discovery document cannot be fetched or is
                invalid, or if the delegated auth code flow fails.
        """
        resolved = self._resolve_config(auth_config)
        return self._auth_code_plugin.authenticate(resolved)

    def refresh(self, auth_config: AuthConfig) -> AuthResult:
        """Refresh using the underlying auth code plugin's refresh logic.

        Args:
            auth_config: Profile auth configuration (same requirements as
                :meth:`authenticate`).

        Returns:
            An :class:`~specli.auth.base.AuthResult` with a refreshed
            ``Authorization: Bearer`` header.

        Raises:
            AuthError: If discovery or the delegated refresh fails.
        """
        resolved = self._resolve_config(auth_config)
        return self._auth_code_plugin.refresh(resolved)

    def validate_config(self, auth_config: AuthConfig) -> list[str]:
        """Validate that the OpenID Connect discovery URL is configured.

        Args:
            auth_config: The auth configuration to validate.

        Returns:
            A list of human-readable error strings. Empty if valid.
        """
        errors: list[str] = []
        if not auth_config.openid_connect_url:
            errors.append(
                "OpenID Connect requires 'openid_connect_url'"
            )
        return errors

    def discover(self, openid_connect_url: str) -> dict[str, Any]:
        """Fetch and return the OpenID Connect discovery document.

        Args:
            openid_connect_url: URL to the OpenID provider's discovery document
                (typically ``https://provider/.well-known/openid-configuration``).

        Returns:
            The parsed JSON discovery document.

        Raises:
            AuthError: If the document cannot be fetched or parsed.
        """
        try:
            response = httpx.get(
                openid_connect_url,
                headers={"Accept": "application/json"},
                timeout=30.0,
            )
            response.raise_for_status()
            doc: dict[str, Any] = response.json()
        except httpx.HTTPStatusError as exc:
            raise AuthError(
                f"OpenID discovery failed with status {exc.response.status_code}: "
                f"{exc.response.text}"
            ) from exc
        except httpx.HTTPError as exc:
            raise AuthError(f"OpenID discovery failed: {exc}") from exc

        if "authorization_endpoint" not in doc:
            raise AuthError(
                "OpenID discovery document missing 'authorization_endpoint'"
            )
        if "token_endpoint" not in doc:
            raise AuthError(
                "OpenID discovery document missing 'token_endpoint'"
            )

        return doc

    def _resolve_config(self, auth_config: AuthConfig) -> AuthConfig:
        """Build an AuthConfig with endpoints from the discovery document.

        Caches the discovery result so subsequent calls avoid extra HTTP requests.
        If the user has already set ``authorization_url`` and ``token_url`` on the
        config, those take precedence over discovered values.
        """
        if self._discovered_config is not None:
            return self._discovered_config

        if not auth_config.openid_connect_url:
            raise AuthError("openid_connect_url is required for OpenID Connect")

        doc = self.discover(auth_config.openid_connect_url)

        # Build a new AuthConfig merging discovery with the user's config.
        # User-set values take precedence over discovered ones.
        resolved = auth_config.model_copy(
            update={
                "authorization_url": (
                    auth_config.authorization_url
                    or doc["authorization_endpoint"]
                ),
                "token_url": (
                    auth_config.token_url
                    or doc["token_endpoint"]
                ),
            }
        )

        self._discovered_config = resolved
        return resolved
