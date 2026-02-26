"""OAuth2 Authorization Code flow with PKCE auth plugin.

This module provides :class:`OAuth2AuthCodePlugin`, which implements the
``oauth2_auth_code`` auth type. It performs the full OAuth2 Authorization
Code grant with PKCE (:rfc:`7636`):

1. Opens the authorization URL in the user's browser.
2. Listens on a temporary local HTTP server for the redirect callback.
3. Exchanges the authorization code for access and refresh tokens.
4. Caches tokens in memory and automatically refreshes them.

Also exports :func:`generate_pkce_pair`, a utility for generating a
``code_verifier`` / ``code_challenge`` pair used by this plugin and
:mod:`specli.plugins.browser_login`.

See Also:
    :class:`specli.auth.base.AuthPlugin` for the base interface.
    :mod:`specli.plugins.openid_connect` for OIDC discovery +
    delegation to this plugin.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import socket
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from specli.auth.base import AuthPlugin, AuthResult
from specli.config import resolve_credential
from specli.exceptions import AuthError
from specli.models import AuthConfig


def generate_pkce_pair() -> tuple[str, str]:
    """Generate a PKCE code_verifier and code_challenge (S256).

    Returns:
        A tuple of ``(code_verifier, code_challenge)``.
    """
    # RFC 7636: 43-128 characters from unreserved character set
    code_verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


def _find_free_port() -> int:
    """Find a free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class OAuth2AuthCodePlugin(AuthPlugin):
    """Authenticate via OAuth2 Authorization Code grant with PKCE.

    Opens a browser for user authorization, receives the callback on a
    temporary local HTTP server, exchanges the code for tokens, and caches
    the access_token and refresh_token.
    """

    def __init__(self) -> None:
        """Initialize with empty in-memory token cache."""
        self._cached_token: str | None = None
        self._refresh_token: str | None = None
        self._token_expiry: float = 0.0

    @property
    def auth_type(self) -> str:
        return "oauth2_auth_code"

    def authenticate(self, auth_config: AuthConfig) -> AuthResult:
        """Return auth artifacts, performing the auth code flow if needed.

        Returns a cached token when still valid (with a 30-second safety
        margin). If a refresh token is available, attempts a silent
        refresh. Falls back to the full interactive browser flow.

        Args:
            auth_config: Profile auth configuration with
                ``authorization_url``, ``token_url``, and optionally
                ``client_id_source``, ``client_secret_source``, and
                ``scopes``.

        Returns:
            An :class:`~specli.auth.base.AuthResult` containing an
            ``Authorization: Bearer <token>`` header.

        Raises:
            AuthError: If the terminal is not interactive and no cached
                or refreshable token is available.
        """
        # Return cached token if still valid
        if self._cached_token and time.monotonic() < (self._token_expiry - 30):
            return AuthResult(headers={"Authorization": f"Bearer {self._cached_token}"})

        # If we have a refresh token, try refreshing first
        if self._refresh_token and auth_config.token_url:
            try:
                return self._do_refresh(auth_config)
            except AuthError:
                # Refresh failed, fall through to interactive login
                self._refresh_token = None

        return self.login_interactive(auth_config)

    def login_interactive(self, auth_config: AuthConfig) -> AuthResult:
        """Run the full interactive authorization code flow.

        Requires a TTY (browser access). Raises ``AuthError`` if stdin is
        not a TTY.
        """
        if not sys.stdin.isatty():
            raise AuthError(
                "OAuth2 authorization code flow requires an interactive terminal "
                "(stdin must be a TTY)"
            )

        if not auth_config.authorization_url:
            raise AuthError("authorization_url is required for OAuth2 auth code flow")
        if not auth_config.token_url:
            raise AuthError("token_url is required for OAuth2 auth code flow")

        code_verifier, code_challenge = generate_pkce_pair()
        port = _find_free_port()
        redirect_uri = f"http://127.0.0.1:{port}/callback"

        # Build authorization URL
        params: dict[str, str] = {
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }

        # Add client_id if available
        if auth_config.client_id_source:
            params["client_id"] = resolve_credential(auth_config.client_id_source)

        if auth_config.scopes:
            params["scope"] = " ".join(auth_config.scopes)

        auth_url = f"{auth_config.authorization_url}?{urlencode(params)}"

        # Start callback server and open browser
        auth_code = self._wait_for_callback(port, auth_url)

        # Exchange code for tokens
        token_data = self._exchange_code(
            auth_config, auth_code, code_verifier, redirect_uri
        )
        self._cache_token(token_data)

        return AuthResult(headers={"Authorization": f"Bearer {self._cached_token}"})

    def refresh(self, auth_config: AuthConfig) -> AuthResult:
        """Refresh using the stored refresh_token, or re-authenticate."""
        if self._refresh_token and auth_config.token_url:
            return self._do_refresh(auth_config)
        return self.authenticate(auth_config)

    def validate_config(self, auth_config: AuthConfig) -> list[str]:
        """Validate that OAuth2 authorization code configuration is present.

        Args:
            auth_config: The auth configuration to validate.

        Returns:
            A list of human-readable error strings. Empty if valid.
        """
        errors: list[str] = []
        if not auth_config.authorization_url:
            errors.append(
                "OAuth2 auth_code requires 'authorization_url'"
            )
        if not auth_config.token_url:
            errors.append(
                "OAuth2 auth_code requires 'token_url'"
            )
        return errors

    def _wait_for_callback(self, port: int, auth_url: str) -> str:
        """Start a local HTTP server, open the browser, and wait for the callback.

        A single-request HTTP server is started on ``127.0.0.1:{port}``.
        The browser is opened in a daemon thread to avoid blocking. The
        server waits up to 120 seconds for the OAuth provider to redirect
        back with an authorization code.

        Args:
            port: TCP port for the local callback server.
            auth_url: The fully-formed authorization URL to open.

        Returns:
            The authorization code from the callback query string.

        Raises:
            AuthError: If the provider returns an error or no code is
                received within the timeout.
        """
        result: dict[str, str | None] = {"code": None, "error": None}

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                params = parse_qs(parsed.query)

                if "error" in params:
                    result["error"] = params["error"][0]
                    error_desc = params.get("error_description", [""])[0]
                    body = f"Authorization failed: {result['error']}"
                    if error_desc:
                        body += f" - {error_desc}"
                elif "code" in params:
                    result["code"] = params["code"][0]
                    body = (
                        "Authorization successful! You can close this window "
                        "and return to the terminal."
                    )
                else:
                    result["error"] = "no_code"
                    body = "No authorization code received."

                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    f"<html><body><h2>{body}</h2></body></html>".encode("utf-8")
                )

            def log_message(self, format: str, *args: Any) -> None:
                # Suppress default logging
                pass

        server = HTTPServer(("127.0.0.1", port), CallbackHandler)
        server.timeout = 120  # 2 minute timeout

        # Open browser in a separate thread to avoid blocking
        def open_browser() -> None:
            webbrowser.open(auth_url)

        browser_thread = threading.Thread(target=open_browser, daemon=True)
        browser_thread.start()

        # Handle one request (the callback)
        server.handle_request()
        server.server_close()

        if result["error"]:
            raise AuthError(f"OAuth2 authorization failed: {result['error']}")
        if not result["code"]:
            raise AuthError("No authorization code received from callback")

        return result["code"]

    def _exchange_code(
        self,
        auth_config: AuthConfig,
        code: str,
        code_verifier: str,
        redirect_uri: str,
    ) -> dict[str, Any]:
        """Exchange the authorization code for access and refresh tokens.

        Args:
            auth_config: Auth configuration with ``token_url`` and
                optional ``client_id_source`` / ``client_secret_source``.
            code: The authorization code received from the callback.
            code_verifier: The PKCE code verifier to prove possession.
            redirect_uri: The redirect URI used in the authorization request.

        Returns:
            The parsed JSON token response containing at least
            ``access_token``.

        Raises:
            AuthError: On HTTP errors or if ``access_token`` is missing
                from the response.
        """
        if not auth_config.token_url:
            raise AuthError("token_url is required")

        data: dict[str, str] = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        }

        if auth_config.client_id_source:
            data["client_id"] = resolve_credential(auth_config.client_id_source)
        if auth_config.client_secret_source:
            data["client_secret"] = resolve_credential(auth_config.client_secret_source)

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
                f"Token exchange failed with status {exc.response.status_code}: "
                f"{exc.response.text}"
            ) from exc
        except httpx.HTTPError as exc:
            raise AuthError(f"Token exchange failed: {exc}") from exc

        if "access_token" not in token_data:
            raise AuthError("Token response missing 'access_token' field")

        return token_data

    def _do_refresh(self, auth_config: AuthConfig) -> AuthResult:
        """Refresh the access token using the stored refresh token.

        Args:
            auth_config: Auth configuration with ``token_url``.

        Returns:
            An :class:`~specli.auth.base.AuthResult` with the new
            ``Authorization: Bearer`` header.

        Raises:
            AuthError: If no refresh token is available, ``token_url`` is
                missing, the request fails, or the response lacks
                ``access_token``.
        """
        if not auth_config.token_url:
            raise AuthError("token_url is required for token refresh")
        if not self._refresh_token:
            raise AuthError("No refresh token available")

        data: dict[str, str] = {
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
        }

        if auth_config.client_id_source:
            data["client_id"] = resolve_credential(auth_config.client_id_source)
        if auth_config.client_secret_source:
            data["client_secret"] = resolve_credential(auth_config.client_secret_source)

        try:
            response = httpx.post(
                auth_config.token_url,
                data=data,
                headers={"Accept": "application/json"},
                timeout=30.0,
            )
            response.raise_for_status()
            token_data: dict[str, Any] = response.json()
        except httpx.HTTPError as exc:
            raise AuthError(f"Token refresh failed: {exc}") from exc

        if "access_token" not in token_data:
            raise AuthError("Token refresh response missing 'access_token' field")

        self._cache_token(token_data)
        return AuthResult(headers={"Authorization": f"Bearer {self._cached_token}"})

    def _cache_token(self, token_data: dict[str, Any]) -> None:
        """Cache access and refresh tokens from the token response."""
        self._cached_token = token_data["access_token"]
        if "refresh_token" in token_data:
            self._refresh_token = token_data["refresh_token"]
        expires_in = token_data.get("expires_in")
        if expires_in is not None:
            self._token_expiry = time.monotonic() + float(expires_in)
        else:
            self._token_expiry = time.monotonic() + 3600.0
