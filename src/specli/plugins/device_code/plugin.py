"""OAuth2 Device Authorization Grant (:rfc:`8628`) auth plugin.

For headless terminals (SSH, Docker, CI) where a browser cannot be opened
locally. Works like ``gcloud auth login --no-browser``.

Flow:
    1. POST to ``device_authorization_url`` to obtain ``device_code`` +
       ``user_code``.
    2. Print instructions: "Go to {verification_uri} and enter code:
       {user_code}".
    3. Poll ``token_url`` until the user authorizes or the code expires.
    4. On success: persist ``access_token`` + ``refresh_token`` via
       :class:`~specli.auth.credential_store.CredentialStore`.

Subsequent invocations reuse the persisted token, refreshing silently
when a ``refresh_token`` is available.

See Also:
    :class:`specli.auth.base.AuthPlugin` for the base interface.
    :mod:`specli.plugins.browser_login` for the browser-based
    alternative.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx

from specli.auth.base import AuthPlugin, AuthResult
from specli.auth.credential_store import CredentialEntry, CredentialStore
from specli.config import resolve_credential
from specli.exceptions import AuthError
from specli.models import AuthConfig


class DeviceCodePlugin(AuthPlugin):
    """Authenticate via OAuth2 Device Authorization Grant (:rfc:`8628`).

    Designed for environments without a local browser. The user is
    presented with a short code to enter at a verification URI on any
    device. The plugin polls the token endpoint until authorization
    completes, then persists and reuses the tokens.
    """

    @property
    def auth_type(self) -> str:
        return "device_code"

    def authenticate(self, auth_config: AuthConfig) -> AuthResult:
        """Return auth artifacts, running the device flow if necessary.

        Checks the credential store for a valid token, attempts a silent
        refresh via ``refresh_token`` if the token has expired, and falls
        back to the full interactive device authorization flow.

        Args:
            auth_config: Profile auth configuration. Must include
                ``device_authorization_url``, ``token_url``, and
                ``client_id_source``.

        Returns:
            An :class:`~specli.auth.base.AuthResult` with the access
            token placed at the configured ``location``.

        Raises:
            AuthError: If required fields are missing, the user denies
                authorization, or the device code expires.
        """
        # 1. Check credential store for valid token
        if auth_config.persist:
            store = self._get_store(auth_config)
            entry = store.load() if store.is_valid() else None
            if entry is not None:
                return self._build_result(auth_config, entry.credential)

            # Check for refresh_token in expired entry
            if entry is None:
                entry = store.load()
            if entry is not None and entry.metadata.get("refresh_token"):
                try:
                    return self._do_refresh(auth_config, entry.metadata["refresh_token"])
                except AuthError:
                    pass  # Fall through to device code flow

        # 2. Run device code flow
        return self._do_device_flow(auth_config)

    def validate_config(self, auth_config: AuthConfig) -> list[str]:
        """Validate that device code flow configuration fields are present.

        Args:
            auth_config: The auth configuration to validate.

        Returns:
            A list of human-readable error strings. Empty if valid.
        """
        errors: list[str] = []
        if not auth_config.device_authorization_url:
            errors.append("device_code requires 'device_authorization_url'")
        if not auth_config.token_url:
            errors.append("device_code requires 'token_url'")
        if not auth_config.client_id_source:
            errors.append("device_code requires 'client_id_source'")
        if auth_config.location not in ("header", "query", "cookie"):
            errors.append(
                f"Invalid location '{auth_config.location}': "
                "must be 'header', 'query', or 'cookie'"
            )
        return errors

    def _do_device_flow(self, auth_config: AuthConfig) -> AuthResult:
        """Run the full device authorization flow.

        Requests a device code, displays the user code, polls for
        authorization, and persists the resulting tokens.

        Args:
            auth_config: Auth configuration with ``device_authorization_url``,
                ``token_url``, and ``client_id_source``.

        Returns:
            An :class:`~specli.auth.base.AuthResult` with the access token.

        Raises:
            AuthError: If required fields are missing, the device code request
                fails, the user denies access, or polling times out.
        """
        if not auth_config.device_authorization_url:
            raise AuthError("device_code requires 'device_authorization_url'")
        if not auth_config.token_url:
            raise AuthError("device_code requires 'token_url'")
        if not auth_config.client_id_source:
            raise AuthError("device_code requires 'client_id_source'")

        client_id = resolve_credential(auth_config.client_id_source)

        # Step 1: Request device code
        device_data = self._request_device_code(
            auth_config.device_authorization_url, client_id, auth_config.scopes
        )

        device_code: str = device_data["device_code"]
        user_code: str = device_data["user_code"]
        verification_uri: str = device_data.get(
            "verification_uri", device_data.get("verification_url", "")
        )
        interval: int = device_data.get("interval", 5)
        expires_in: int = device_data.get("expires_in", 1800)

        # Step 2: Display instructions
        self._display_user_code(verification_uri, user_code)

        # Step 3: Poll for token
        token_data = self._poll_for_token(
            auth_config, device_code, client_id, interval, expires_in
        )

        access_token: str = token_data["access_token"]
        refresh_token: str | None = token_data.get("refresh_token")
        token_expires_in: int | None = token_data.get("expires_in")

        # Step 4: Persist
        if auth_config.persist:
            self._persist_token(
                auth_config, access_token, refresh_token, token_expires_in
            )

        return self._build_result(auth_config, access_token)

    def _request_device_code(
        self, device_auth_url: str, client_id: str, scopes: list[str]
    ) -> dict[str, Any]:
        """POST to the device authorization endpoint.

        Args:
            device_auth_url: The device authorization endpoint URL.
            client_id: The OAuth2 client ID.
            scopes: Requested OAuth2 scopes (may be empty).

        Returns:
            The parsed JSON response containing ``device_code``,
            ``user_code``, ``verification_uri``, ``interval``, and
            ``expires_in``.

        Raises:
            AuthError: On HTTP errors or if ``device_code``/``user_code``
                are missing from the response.
        """
        data: dict[str, str] = {"client_id": client_id}
        if scopes:
            data["scope"] = " ".join(scopes)

        try:
            response = httpx.post(
                device_auth_url,
                data=data,
                headers={"Accept": "application/json"},
                timeout=30.0,
            )
            response.raise_for_status()
            result: dict[str, Any] = response.json()
        except httpx.HTTPStatusError as exc:
            raise AuthError(
                f"Device authorization request failed with status "
                f"{exc.response.status_code}: {exc.response.text}"
            ) from exc
        except httpx.HTTPError as exc:
            raise AuthError(f"Device authorization request failed: {exc}") from exc

        if "device_code" not in result:
            raise AuthError("Device authorization response missing 'device_code'")
        if "user_code" not in result:
            raise AuthError("Device authorization response missing 'user_code'")

        return result

    def _display_user_code(self, verification_uri: str, user_code: str) -> None:
        """Print the user code and verification URI to the terminal."""
        sys.stderr.write("\n")
        sys.stderr.write(f"Go to: {verification_uri}\n")
        sys.stderr.write(f"Enter code: {user_code}\n")
        sys.stderr.write("\nWaiting for authorization...\n")
        sys.stderr.flush()

    def _poll_for_token(
        self,
        auth_config: AuthConfig,
        device_code: str,
        client_id: str,
        interval: int,
        expires_in: int,
    ) -> dict[str, Any]:
        """Poll the token endpoint until the user authorizes or the code expires.

        Implements the polling logic from :rfc:`8628` section 3.4, including
        ``authorization_pending`` (keep polling), ``slow_down`` (increase
        interval), ``access_denied``, and ``expired_token`` error handling.

        Args:
            auth_config: Auth configuration with ``token_url``.
            device_code: The device code obtained from the authorization
                endpoint.
            client_id: The OAuth2 client ID.
            interval: Minimum polling interval in seconds.
            expires_in: Maximum time to poll before giving up, in seconds.

        Returns:
            The parsed JSON token response containing ``access_token``.

        Raises:
            AuthError: If the user denies access, the code expires, or an
                unexpected error is returned.
        """
        assert auth_config.token_url

        deadline = time.monotonic() + expires_in
        poll_interval = max(interval, 1)

        data: dict[str, str] = {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": device_code,
            "client_id": client_id,
        }

        while time.monotonic() < deadline:
            time.sleep(poll_interval)

            try:
                response = httpx.post(
                    auth_config.token_url,
                    data=data,
                    headers={"Accept": "application/json"},
                    timeout=30.0,
                )
                token_data: dict[str, Any] = response.json()
            except httpx.HTTPError as exc:
                raise AuthError(f"Token polling failed: {exc}") from exc

            # Check for success (2xx with access_token)
            if response.status_code == 200 and "access_token" in token_data:
                return token_data

            # Handle error responses per RFC 8628
            error = token_data.get("error", "")

            if error == "authorization_pending":
                continue
            elif error == "slow_down":
                poll_interval += 5
                continue
            elif error == "access_denied":
                raise AuthError("Authorization denied by user")
            elif error == "expired_token":
                raise AuthError("Device code expired -- please try again")
            elif error:
                desc = token_data.get("error_description", error)
                raise AuthError(f"Device code authorization failed: {desc}")

        raise AuthError("Device code flow timed out -- please try again")

    def _do_refresh(self, auth_config: AuthConfig, refresh_token: str) -> AuthResult:
        """Refresh the access token using a refresh token.

        Args:
            auth_config: Auth configuration with ``token_url``.
            refresh_token: The refresh token to exchange.

        Returns:
            An :class:`~specli.auth.base.AuthResult` with the new
            access token.

        Raises:
            AuthError: If ``token_url`` is missing, the refresh request
                fails, or ``access_token`` is absent from the response.
        """
        if not auth_config.token_url:
            raise AuthError("token_url is required for token refresh")

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
            self._persist_token(auth_config, access_token, new_refresh, expires_in)

        return self._build_result(auth_config, access_token)

    def _persist_token(
        self,
        auth_config: AuthConfig,
        access_token: str,
        refresh_token: str | None,
        expires_in: int | None,
    ) -> None:
        """Save tokens to the local credential store.

        Args:
            auth_config: Auth configuration used to determine the store
                profile ID.
            access_token: The access token to persist.
            refresh_token: Optional refresh token stored in entry metadata.
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

    def _get_store(self, auth_config: AuthConfig) -> CredentialStore:
        profile_id = auth_config.credential_name or "device_code"
        return CredentialStore(profile_id)

    def _build_result(self, auth_config: AuthConfig, access_token: str) -> AuthResult:
        """Build an :class:`~specli.auth.base.AuthResult` from the access token.

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
