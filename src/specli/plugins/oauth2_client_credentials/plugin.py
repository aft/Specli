"""OAuth2 Client Credentials flow auth plugin.

This module provides :class:`OAuth2ClientCredentialsPlugin`, which
implements the ``oauth2_client_credentials`` auth type. It performs
the non-interactive Client Credentials grant (:rfc:`6749` section 4.4),
exchanging a ``client_id`` and ``client_secret`` for an access token
at the configured ``token_url``.

This flow is designed for server-to-server (machine-to-machine)
authentication where no user interaction is required.

Tokens are cached in memory with expiry tracking and a 30-second safety
margin to avoid using tokens that are about to expire.

See Also:
    :class:`specli.auth.base.AuthPlugin` for the base interface.
    :mod:`specli.plugins.oauth2_auth_code` for the interactive
    Authorization Code flow.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from specli.auth.base import AuthPlugin, AuthResult
from specli.config import resolve_credential
from specli.exceptions import AuthError
from specli.models import AuthConfig


class OAuth2ClientCredentialsPlugin(AuthPlugin):
    """Authenticate via OAuth2 Client Credentials grant.

    Fetches an access token from the token endpoint using client_id and
    client_secret, then returns an ``Authorization: Bearer <token>`` header.
    Tokens are cached in memory until they expire.
    """

    def __init__(self) -> None:
        """Initialize with empty in-memory token cache."""
        self._cached_token: str | None = None
        self._token_expiry: float = 0.0

    @property
    def auth_type(self) -> str:
        return "oauth2_client_credentials"

    def authenticate(self, auth_config: AuthConfig) -> AuthResult:
        """Fetch or reuse a client-credentials access token.

        Returns a cached token when still valid (with a 30-second safety
        margin). Otherwise, fetches a fresh token from the token endpoint.

        Args:
            auth_config: Profile auth configuration. Must include
                ``token_url``, ``client_id_source``, and
                ``client_secret_source``. Optionally ``scopes``.

        Returns:
            An :class:`~specli.auth.base.AuthResult` containing an
            ``Authorization: Bearer <token>`` header.

        Raises:
            AuthError: If required fields are missing or the token request
                fails.
        """
        # Return cached token if still valid (with 30s safety margin)
        if self._cached_token and time.monotonic() < (self._token_expiry - 30):
            return AuthResult(headers={"Authorization": f"Bearer {self._cached_token}"})

        token_data = self._fetch_token(auth_config)
        self._cache_token(token_data)
        return AuthResult(headers={"Authorization": f"Bearer {self._cached_token}"})

    def refresh(self, auth_config: AuthConfig) -> AuthResult:
        """Force-fetch a new token, discarding the cached one.

        Args:
            auth_config: Profile auth configuration (same requirements as
                :meth:`authenticate`).

        Returns:
            An :class:`~specli.auth.base.AuthResult` with a fresh
            ``Authorization: Bearer`` header.

        Raises:
            AuthError: If the token request fails.
        """
        self._cached_token = None
        self._token_expiry = 0.0
        return self.authenticate(auth_config)

    def validate_config(self, auth_config: AuthConfig) -> list[str]:
        """Validate that client credentials configuration fields are present.

        Args:
            auth_config: The auth configuration to validate.

        Returns:
            A list of human-readable error strings. Empty if valid.
        """
        errors: list[str] = []
        if not auth_config.token_url:
            errors.append(
                "OAuth2 client_credentials requires 'token_url'"
            )
        if not auth_config.client_id_source:
            errors.append(
                "OAuth2 client_credentials requires 'client_id_source'"
            )
        if not auth_config.client_secret_source:
            errors.append(
                "OAuth2 client_credentials requires 'client_secret_source'"
            )
        return errors

    def _fetch_token(self, auth_config: AuthConfig) -> dict[str, Any]:
        """POST to the token endpoint and return the JSON response.

        Sends ``grant_type=client_credentials`` along with the resolved
        ``client_id``, ``client_secret``, and optional ``scope``.

        Args:
            auth_config: Auth configuration with ``token_url``,
                ``client_id_source``, ``client_secret_source``, and
                optionally ``scopes``.

        Returns:
            The parsed JSON token response containing at least
            ``access_token``.

        Raises:
            AuthError: If required fields are missing, the HTTP request
                fails, or ``access_token`` is absent from the response.
        """
        if not auth_config.token_url:
            raise AuthError("token_url is required for OAuth2 client_credentials")
        if not auth_config.client_id_source:
            raise AuthError("client_id_source is required for OAuth2 client_credentials")
        if not auth_config.client_secret_source:
            raise AuthError("client_secret_source is required for OAuth2 client_credentials")

        client_id = resolve_credential(auth_config.client_id_source)
        client_secret = resolve_credential(auth_config.client_secret_source)

        data: dict[str, str] = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        }
        if auth_config.scopes:
            data["scope"] = " ".join(auth_config.scopes)

        try:
            response = httpx.post(
                auth_config.token_url,
                data=data,
                headers={"Accept": "application/json"},
                timeout=30.0,
            )
            response.raise_for_status()
            token_data: dict[str, Any] = response.json()
        except httpx.HTTPStatusError as exc:
            raise AuthError(
                f"Token request failed with status {exc.response.status_code}: "
                f"{exc.response.text}"
            ) from exc
        except httpx.HTTPError as exc:
            raise AuthError(f"Token request failed: {exc}") from exc

        if "access_token" not in token_data:
            raise AuthError(
                "Token response missing 'access_token' field"
            )

        return token_data

    def _cache_token(self, token_data: dict[str, Any]) -> None:
        """Cache the access token and compute its expiry time."""
        self._cached_token = token_data["access_token"]
        expires_in = token_data.get("expires_in")
        if expires_in is not None:
            self._token_expiry = time.monotonic() + float(expires_in)
        else:
            # Default to 1 hour if no expiry provided
            self._token_expiry = time.monotonic() + 3600.0
