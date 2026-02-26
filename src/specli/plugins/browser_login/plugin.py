"""Browser login auth plugin -- open browser, capture credential from callback.

Supports two modes:

**OAuth mode** (when ``authorization_url`` + ``token_url`` + ``client_id_source``
are set): Full OAuth2 Authorization Code flow with PKCE, directly against the
OAuth provider (like ``gcloud auth login``). Tokens are persisted via
:class:`~specli.auth.credential_store.CredentialStore` and silently
refreshed when they expire.

**Simple mode** (when only ``login_url`` is set): Legacy behaviour -- open URL,
capture a credential from the redirect callback using one of four capture
strategies (cookie, header, query parameter, or JSON body field).

Both modes require an interactive terminal (TTY) because the user must
interact with their browser.

See Also:
    :class:`specli.auth.base.AuthPlugin` for the base interface.
    :func:`specli.plugins.oauth2_auth_code.plugin.generate_pkce_pair`
    for the PKCE helper used in OAuth mode.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone, timedelta
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from specli.auth.base import AuthPlugin, AuthResult
from specli.auth.credential_store import CredentialEntry, CredentialStore
from specli.config import resolve_credential
from specli.exceptions import AuthError
from specli.models import AuthConfig
from specli.plugins.oauth2_auth_code.plugin import generate_pkce_pair


def _find_free_port() -> int:
    """Find a free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _is_oauth_mode(auth_config: AuthConfig) -> bool:
    """Return True when the config has enough fields for OAuth mode."""
    return bool(
        auth_config.authorization_url
        and auth_config.token_url
        and auth_config.client_id_source
    )


class BrowserLoginPlugin(AuthPlugin):
    """Authenticate by opening a browser and capturing a credential from the callback.

    **OAuth mode** performs a full OAuth2 Authorization Code flow with PKCE --
    the CLI acts as the OAuth client, exchanges the code for tokens, persists
    the refresh_token, and refreshes silently.

    **Simple mode** captures credentials from:
    - ``cookie``: a Set-Cookie header in the callback response
    - ``header``: a custom header in the callback response
    - ``query_param``: a query parameter in the callback URL
    - ``body_field``: a field in a JSON body POSTed to the callback
    """

    @property
    def auth_type(self) -> str:
        return "browser_login"

    def authenticate(self, auth_config: AuthConfig) -> AuthResult:
        """Authenticate by opening the user's browser.

        Delegates to OAuth mode or simple mode based on which config
        fields are present. In both cases, persisted credentials are
        checked first and reused when still valid.

        Args:
            auth_config: Profile auth configuration. OAuth mode requires
                ``authorization_url``, ``token_url``, and ``client_id_source``.
                Simple mode requires ``login_url`` and ``capture_name``.

        Returns:
            An :class:`~specli.auth.base.AuthResult` with the credential
            placed at the configured ``location``.

        Raises:
            AuthError: If the terminal is not interactive (no TTY), or if
                the browser flow fails or times out.
        """
        if _is_oauth_mode(auth_config):
            return self._authenticate_oauth(auth_config)
        return self._authenticate_simple(auth_config)

    # ------------------------------------------------------------------
    # OAuth mode
    # ------------------------------------------------------------------

    def _authenticate_oauth(self, auth_config: AuthConfig) -> AuthResult:
        """OAuth2 Authorization Code + PKCE flow.

        Checks the credential store for a valid access token, attempts a
        silent refresh if a refresh token is available, and falls back to
        an interactive browser login as a last resort.

        Args:
            auth_config: Auth configuration with OAuth fields populated.

        Returns:
            An :class:`~specli.auth.base.AuthResult` with the access
            token.

        Raises:
            AuthError: If the terminal is not interactive and no cached or
                refreshable token is available.
        """
        # 1. Check credential store for valid token
        if auth_config.persist:
            store = self._get_store(auth_config)
            entry = store.load() if store.is_valid() else None
            if entry is not None:
                # Token still valid
                return self._build_oauth_result(auth_config, entry.credential)

            # Check for refresh_token in metadata
            if entry is None:
                # Load even expired entries to get refresh_token
                entry = store.load()
            if entry is not None and entry.metadata.get("refresh_token"):
                try:
                    return self._do_oauth_refresh(auth_config, entry.metadata["refresh_token"])
                except AuthError:
                    pass  # Fall through to interactive

        # 2. Interactive browser flow
        if not sys.stdin.isatty():
            raise AuthError(
                "browser_login OAuth mode requires an interactive terminal "
                "(stdin must be a TTY)"
            )

        return self._do_oauth_login(auth_config)

    def _do_oauth_login(self, auth_config: AuthConfig) -> AuthResult:
        """Run the full interactive OAuth2 auth code + PKCE flow.

        Generates a PKCE pair, starts a local HTTP callback server, opens
        the authorization URL in the browser, waits for the redirect,
        exchanges the code for tokens, and persists them.

        Args:
            auth_config: Auth configuration with ``authorization_url``,
                ``token_url``, and ``client_id_source``.

        Returns:
            An :class:`~specli.auth.base.AuthResult` with the access
            token.

        Raises:
            AuthError: If the code exchange or token request fails.
        """
        assert auth_config.authorization_url
        assert auth_config.token_url
        assert auth_config.client_id_source

        code_verifier, code_challenge = generate_pkce_pair()
        port = _find_free_port()
        redirect_uri = f"http://127.0.0.1:{port}/callback"

        # Build authorization URL
        params: dict[str, str] = {
            "response_type": "code",
            "client_id": resolve_credential(auth_config.client_id_source),
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        if auth_config.scopes:
            params["scope"] = " ".join(auth_config.scopes)

        auth_url = f"{auth_config.authorization_url}?{urlencode(params)}"

        # Wait for the authorization code callback
        auth_code = self._wait_for_auth_code(port, auth_url)

        # Exchange code for tokens
        token_data = self._exchange_code(
            auth_config, auth_code, code_verifier, redirect_uri
        )

        access_token: str = token_data["access_token"]
        refresh_token: str | None = token_data.get("refresh_token")
        expires_in: int | None = token_data.get("expires_in")

        # Persist
        if auth_config.persist:
            self._persist_oauth_token(
                auth_config, access_token, refresh_token, expires_in
            )

        return self._build_oauth_result(auth_config, access_token)

    def _wait_for_auth_code(self, port: int, auth_url: str) -> str:
        """Start a local HTTP server, open the browser, and capture the authorization code.

        Args:
            port: TCP port for the local callback server.
            auth_url: The fully-formed authorization URL to open in the browser.

        Returns:
            The authorization code extracted from the callback query string.

        Raises:
            AuthError: If the authorization fails or no code is received
                within the 120-second timeout.
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
                pass

        server = HTTPServer(("127.0.0.1", port), CallbackHandler)
        server.timeout = 120

        def open_browser() -> None:
            webbrowser.open(auth_url)

        browser_thread = threading.Thread(target=open_browser, daemon=True)
        browser_thread.start()

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
        """Exchange an authorization code for access and refresh tokens.

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
        assert auth_config.token_url

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

    def _do_oauth_refresh(self, auth_config: AuthConfig, refresh_token: str) -> AuthResult:
        """Refresh the access token using a refresh token.

        Args:
            auth_config: Auth configuration with ``token_url``.
            refresh_token: The refresh token to exchange for a new
                access token.

        Returns:
            An :class:`~specli.auth.base.AuthResult` with the new
            access token.

        Raises:
            AuthError: If the refresh request fails or the response does
                not contain an ``access_token``.
        """
        assert auth_config.token_url

        data: dict[str, str] = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
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

        access_token = token_data["access_token"]
        new_refresh = token_data.get("refresh_token", refresh_token)
        expires_in = token_data.get("expires_in")

        if auth_config.persist:
            self._persist_oauth_token(auth_config, access_token, new_refresh, expires_in)

        return self._build_oauth_result(auth_config, access_token)

    def _persist_oauth_token(
        self,
        auth_config: AuthConfig,
        access_token: str,
        refresh_token: str | None,
        expires_in: int | None,
    ) -> None:
        """Save OAuth tokens to the local credential store.

        Args:
            auth_config: Auth configuration used to determine the
                credential store profile ID.
            access_token: The access token to persist.
            refresh_token: Optional refresh token stored in entry metadata
                for future silent refreshes.
            expires_in: Token lifetime in seconds, or ``None`` if unknown.
        """
        store = self._get_store(auth_config)
        expires_at = None
        if expires_in is not None:
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

        metadata: dict[str, Any] = {}
        if refresh_token:
            metadata["refresh_token"] = refresh_token

        store.save(
            CredentialEntry(
                auth_type=self.auth_type,
                credential=access_token,
                credential_name=auth_config.credential_name or "access_token",
                expires_at=expires_at,
                metadata=metadata,
            )
        )

    def _build_oauth_result(self, auth_config: AuthConfig, access_token: str) -> AuthResult:
        """Build an :class:`~specli.auth.base.AuthResult` for OAuth mode.

        Args:
            auth_config: Auth configuration with ``location`` and
                ``credential_name``.
            access_token: The OAuth access token.

        Returns:
            An :class:`~specli.auth.base.AuthResult`. Defaults to an
            ``Authorization: Bearer <token>`` header unless ``location``
            specifies cookie or query.
        """
        name = auth_config.credential_name or "Authorization"
        location = auth_config.location

        if location == "cookie":
            return AuthResult(cookies={name: access_token})
        if location == "query":
            return AuthResult(params={name: access_token})
        # Default: header with Bearer prefix
        return AuthResult(headers={name: f"Bearer {access_token}"})

    # ------------------------------------------------------------------
    # Simple mode (backward-compatible)
    # ------------------------------------------------------------------

    def _authenticate_simple(self, auth_config: AuthConfig) -> AuthResult:
        """Legacy simple mode: open URL, capture credential from redirect.

        Checks the credential store first, then falls back to an
        interactive browser login that captures the credential from
        the callback using the configured ``callback_capture`` strategy.

        Args:
            auth_config: Auth configuration with ``login_url``,
                ``capture_name``, and ``callback_capture``.

        Returns:
            An :class:`~specli.auth.base.AuthResult` with the captured
            credential.

        Raises:
            AuthError: If the terminal is not interactive, required fields
                are missing, or the callback capture fails.
        """
        # 1. Check credential store
        if auth_config.persist:
            store = self._get_store(auth_config)
            if store.is_valid():
                entry = store.load()
                if entry is not None:
                    return self._build_result(auth_config, entry.credential)

        # 2. Interactive browser login
        if not sys.stdin.isatty():
            raise AuthError(
                "browser_login auth requires an interactive terminal "
                "(stdin must be a TTY)"
            )

        if not auth_config.login_url:
            raise AuthError("browser_login requires 'login_url'")

        if not auth_config.capture_name:
            raise AuthError("browser_login requires 'capture_name'")

        credential = self._do_browser_login(auth_config)

        # 3. Persist if requested
        if auth_config.persist:
            store = self._get_store(auth_config)
            store.save(
                CredentialEntry(
                    auth_type=self.auth_type,
                    credential=credential,
                    credential_name=auth_config.capture_name,
                )
            )

        return self._build_result(auth_config, credential)

    def validate_config(self, auth_config: AuthConfig) -> list[str]:
        """Validate browser login configuration for either OAuth or simple mode.

        In OAuth mode, checks for ``authorization_url``, ``token_url``, and
        ``client_id_source``. In simple mode, checks for ``login_url``,
        ``capture_name``, and a valid ``callback_capture`` strategy.

        Args:
            auth_config: The auth configuration to validate.

        Returns:
            A list of human-readable error strings. Empty if valid.
        """
        errors: list[str] = []

        if _is_oauth_mode(auth_config):
            # OAuth mode validation
            if not auth_config.authorization_url:
                errors.append("browser_login OAuth mode requires 'authorization_url'")
            if not auth_config.token_url:
                errors.append("browser_login OAuth mode requires 'token_url'")
            if not auth_config.client_id_source:
                errors.append("browser_login OAuth mode requires 'client_id_source'")
        else:
            # Simple mode validation
            if not auth_config.login_url:
                errors.append("browser_login requires 'login_url'")
            if not auth_config.capture_name:
                errors.append("browser_login requires 'capture_name'")
            valid_captures = ("cookie", "header", "query_param", "body_field")
            if auth_config.callback_capture not in valid_captures:
                errors.append(
                    f"Invalid callback_capture '{auth_config.callback_capture}': "
                    f"must be one of {valid_captures}"
                )

        if auth_config.location not in ("header", "query", "cookie"):
            errors.append(
                f"Invalid location '{auth_config.location}': "
                "must be 'header', 'query', or 'cookie'"
            )
        return errors

    def _do_browser_login(self, auth_config: AuthConfig) -> str:
        """Start a local server, open the browser, and wait for the callback.

        Appends a ``redirect_uri`` query parameter to the ``login_url`` so
        the remote server knows where to redirect after authentication.

        Args:
            auth_config: Auth configuration with ``login_url``,
                ``callback_capture``, and ``capture_name``.

        Returns:
            The captured credential string.

        Raises:
            AuthError: If the callback fails or times out.
        """
        port = _find_free_port()
        redirect_uri = f"http://127.0.0.1:{port}/callback"

        # Build the login URL with redirect_uri parameter
        login_url = auth_config.login_url
        assert login_url is not None
        separator = "&" if "?" in login_url else "?"
        full_url = f"{login_url}{separator}{urlencode({'redirect_uri': redirect_uri})}"

        credential = self._wait_for_callback(
            port, full_url, auth_config.callback_capture, auth_config.capture_name or ""
        )
        return credential

    def _wait_for_callback(
        self,
        port: int,
        login_url: str,
        capture_mode: str,
        capture_name: str,
    ) -> str:
        """Start a local HTTP server, open the browser, and capture the credential.

        The server handles both GET and POST callbacks. The credential is
        extracted according to ``capture_mode``:

        * ``"query_param"`` -- from the callback URL query string.
        * ``"cookie"`` -- from a ``Cookie`` header on the callback request.
        * ``"header"`` -- from a custom header on the callback request.
        * ``"body_field"`` -- from a JSON body POSTed to the callback.

        Args:
            port: TCP port for the local callback server.
            login_url: The login URL to open in the browser.
            capture_mode: One of ``"query_param"``, ``"cookie"``,
                ``"header"``, or ``"body_field"``.
            capture_name: The name of the parameter, cookie, header, or
                JSON field to extract.

        Returns:
            The extracted credential string.

        Raises:
            AuthError: If the credential cannot be extracted or the server
                times out after 120 seconds.
        """
        result: dict[str, str | None] = {"credential": None, "error": None}

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self._handle_callback()

            def do_POST(self) -> None:
                self._handle_callback()

            def _handle_callback(self) -> None:
                try:
                    credential = self._extract_credential()
                    result["credential"] = credential
                    body = (
                        "Login successful! You can close this window "
                        "and return to the terminal."
                    )
                except Exception as exc:
                    result["error"] = str(exc)
                    body = f"Login failed: {exc}"

                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    f"<html><body><h2>{body}</h2></body></html>".encode("utf-8")
                )

            def _extract_credential(self) -> str:
                if capture_mode == "query_param":
                    parsed = urlparse(self.path)
                    params = parse_qs(parsed.query)
                    values = params.get(capture_name)
                    if not values:
                        raise AuthError(
                            f"Query parameter '{capture_name}' not found in callback"
                        )
                    return values[0]

                if capture_mode == "cookie":
                    cookie_header = self.headers.get("Cookie", "")
                    cookie: SimpleCookie[str] = SimpleCookie()
                    cookie.load(cookie_header)
                    if capture_name not in cookie:
                        raise AuthError(
                            f"Cookie '{capture_name}' not found in callback"
                        )
                    return cookie[capture_name].value

                if capture_mode == "header":
                    value = self.headers.get(capture_name)
                    if value is None:
                        raise AuthError(
                            f"Header '{capture_name}' not found in callback"
                        )
                    return value

                if capture_mode == "body_field":
                    content_length = int(self.headers.get("Content-Length", "0"))
                    body_bytes = self.rfile.read(content_length)
                    try:
                        body_data: dict[str, Any] = json.loads(body_bytes)
                    except (json.JSONDecodeError, ValueError) as exc:
                        raise AuthError(
                            f"Callback body is not valid JSON: {exc}"
                        ) from exc
                    if capture_name not in body_data:
                        raise AuthError(
                            f"Field '{capture_name}' not found in callback body"
                        )
                    return str(body_data[capture_name])

                raise AuthError(f"Unknown capture mode: {capture_mode}")

            def log_message(self, format: str, *args: Any) -> None:
                pass  # Suppress default logging

        server = HTTPServer(("127.0.0.1", port), CallbackHandler)
        server.timeout = 120  # 2 minute timeout

        def open_browser() -> None:
            webbrowser.open(login_url)

        browser_thread = threading.Thread(target=open_browser, daemon=True)
        browser_thread.start()

        server.handle_request()
        server.server_close()

        if result["error"]:
            raise AuthError(f"Browser login failed: {result['error']}")
        if not result["credential"]:
            raise AuthError("No credential received from callback")

        return result["credential"]

    def _get_store(self, auth_config: AuthConfig) -> CredentialStore:
        profile_id = auth_config.credential_name or auth_config.capture_name or "browser_login"
        return CredentialStore(profile_id)

    def _build_result(self, auth_config: AuthConfig, credential: str) -> AuthResult:
        """Build an :class:`~specli.auth.base.AuthResult` for simple mode.

        Args:
            auth_config: Auth configuration with ``location`` and name fields.
            credential: The captured credential string.

        Returns:
            An :class:`~specli.auth.base.AuthResult` with the credential
            placed as a header, query parameter, or cookie.
        """
        name = (
            auth_config.credential_name
            or auth_config.header
            or auth_config.capture_name
            or "Authorization"
        )
        location = auth_config.location

        if location == "cookie":
            return AuthResult(cookies={name: credential})
        if location == "query":
            return AuthResult(params={name: credential})
        # Default: header
        return AuthResult(headers={name: credential})
