"""Interactive API-login auth plugin.

``api_login`` provides a prompt-verify-persist authentication flow:

1. The user runs the generated CLI's ``login`` command once.
2. They paste a key (and optional secret) at a hidden prompt.
3. The plugin verifies the credentials by making an HTTP request to
   ``check_endpoint``; a 2xx response accepts them, 401/403 rejects
   them, and anything else is surfaced as an error.
4. On success the credentials are persisted to the
   :class:`~specli.auth.credential_store.CredentialStore`.
5. Every subsequent CLI invocation reads the store directly.
   There is no re-prompt, no silent refresh, and no attempt to recover
   from a 401 mid-session -- the user must ``logout`` and ``login``
   again.

See Also:
    :class:`~specli.plugins.api_key.plugin.APIKeyAuthPlugin`: the
    non-interactive sibling that resolves credentials from
    env/file/plain on every request.
"""

from __future__ import annotations

import getpass
import sys
from typing import Optional

import httpx

from specli.auth.base import AuthPlugin, AuthResult
from specli.auth.credential_store import CredentialEntry, CredentialStore
from specli.exceptions import AuthError
from specli.models import AuthConfig, Profile


class APILoginPlugin(AuthPlugin):
    """Interactive, stateful API-key + optional-secret authentication.

    Unlike :class:`~specli.plugins.api_key.plugin.APIKeyAuthPlugin`, this
    plugin owns its credential lifecycle: credentials are collected once
    via an interactive prompt, verified against a live endpoint, stored
    on disk, and reused until the user explicitly logs out.
    """

    @property
    def auth_type(self) -> str:
        return "api_login"

    # ------------------------------------------------------------------ #
    # AuthPlugin interface
    # ------------------------------------------------------------------ #

    def authenticate(self, auth_config: AuthConfig) -> AuthResult:
        """Load persisted credentials from the store and build an :class:`AuthResult`.

        This method never prompts and never hits the network. If no valid
        entry is stored, it raises :class:`AuthError` instructing the user
        to run the ``login`` subcommand. This is the explicit fail-loud
        behaviour requested by the design: a mid-session credential
        failure is not auto-corrected.

        Args:
            auth_config: The auth section from the active profile.

        Returns:
            An :class:`AuthResult` populated with the key (and optional
            secret) placed at the configured ``location``.

        Raises:
            AuthError: If no valid stored credential exists.
        """
        store = self._get_store(auth_config)
        entry = store.load() if store.is_valid() else None
        if entry is None:
            raise AuthError(
                "Not logged in. Run the 'login' command to authenticate."
            )

        key = entry.credential
        secret = (entry.metadata or {}).get("secret")
        return self._build_result(auth_config, key, secret)

    def validate_config(self, auth_config: AuthConfig) -> list[str]:
        """Validate that the plugin has what it needs.

        Args:
            auth_config: The auth configuration to check.

        Returns:
            A list of human-readable error strings; empty when valid.
        """
        errors: list[str] = []
        extras = auth_config.model_extra or {}
        if not extras.get("check_endpoint"):
            errors.append(
                "api_login requires 'check_endpoint' to verify credentials "
                "during login."
            )
        if auth_config.location not in ("header", "query", "cookie"):
            errors.append(
                f"Invalid location '{auth_config.location}': "
                "must be 'header', 'query', or 'cookie'."
            )
        return errors

    # ------------------------------------------------------------------ #
    # Login / logout (called from CLI subcommands, not AuthManager)
    # ------------------------------------------------------------------ #

    def login(
        self,
        profile: Profile,
        *,
        key: Optional[str] = None,
        secret: Optional[str] = None,
        verify: bool = True,
    ) -> None:
        """Collect, verify, and persist credentials for *profile*.

        Args:
            profile: The active profile. Used for ``base_url`` and the
                credential store path.
            key: Pre-supplied key. When ``None`` the user is prompted via
                :func:`getpass.getpass`.
            secret: Pre-supplied secret. When ``None`` and the profile's
                auth config has a ``secret_name`` extra, the user is
                prompted; otherwise no secret is collected.
            verify: When ``True`` (default) the credentials are verified
                by calling the ``check_endpoint``. Setting this to
                ``False`` skips verification -- useful for CI bootstrap
                when the endpoint is not yet reachable.

        Raises:
            AuthError: If the profile has no ``api_login`` auth, the
                credentials cannot be collected (non-TTY), or verification
                fails (non-2xx response).
        """
        auth_config = self._require_auth_config(profile)
        extras = auth_config.model_extra or {}
        secret_name = extras.get("secret_name")

        if key is None:
            if not sys.stdin.isatty():
                raise AuthError(
                    "Cannot prompt for credentials: stdin is not a TTY. "
                    "Pass --key (and --secret) for non-interactive login."
                )
            key = getpass.getpass("Paste key: ")
            if not key:
                raise AuthError("No key provided.")

        if secret_name and secret is None:
            if not sys.stdin.isatty():
                raise AuthError(
                    "Cannot prompt for secret: stdin is not a TTY. "
                    "Pass --secret for non-interactive login."
                )
            secret = getpass.getpass("Paste secret: ")
            if not secret:
                raise AuthError("No secret provided.")

        if verify:
            self._verify(profile, auth_config, key, secret)

        # Persist on success.
        store = self._get_store(auth_config)
        metadata: dict[str, object] = {}
        if secret is not None:
            metadata["secret"] = secret
        if secret_name:
            metadata["secret_name"] = secret_name
        store.save(
            CredentialEntry(
                auth_type=self.auth_type,
                credential=key,
                credential_name=self._key_name(auth_config),
                metadata=metadata,
            )
        )

    def logout(self, profile: Profile) -> None:
        """Delete persisted credentials for *profile*.

        Safe to call when nothing is stored.

        Args:
            profile: The profile whose credentials should be cleared.
        """
        auth_config = self._require_auth_config(profile)
        self._get_store(auth_config).clear()

    def is_logged_in(self, profile: Profile) -> bool:
        """Return ``True`` when *profile* has a valid persisted credential."""
        auth_config = self._require_auth_config(profile)
        return self._get_store(auth_config).is_valid()

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _require_auth_config(profile: Profile) -> AuthConfig:
        if profile.auth is None or profile.auth.type != "api_login":
            raise AuthError(
                "Profile is not configured for api_login auth."
            )
        return profile.auth

    def _verify(
        self,
        profile: Profile,
        auth_config: AuthConfig,
        key: str,
        secret: Optional[str],
    ) -> None:
        """Make a live call to ``check_endpoint`` and raise on non-2xx.

        2xx status codes accept the credentials, 401/403 reject them, and
        anything else is surfaced verbatim so the user can diagnose
        server problems distinct from bad credentials.
        """
        extras = auth_config.model_extra or {}
        endpoint = extras.get("check_endpoint")
        if not endpoint:
            raise AuthError(
                "api_login requires 'check_endpoint' to verify credentials."
            )
        method = (extras.get("check_method") or "GET").upper()

        url = self._resolve_endpoint(profile, endpoint)
        trial = self._build_result(auth_config, key, secret)
        try:
            response = httpx.request(
                method,
                url,
                headers=trial.headers or None,
                params=trial.params or None,
                cookies=trial.cookies or None,
                timeout=15.0,
                follow_redirects=True,
            )
        except httpx.RequestError as exc:
            raise AuthError(
                f"Verification request failed: {exc}"
            ) from exc

        if 200 <= response.status_code < 300:
            return
        if response.status_code in (401, 403):
            raise AuthError(
                f"Credentials rejected by {url} (status {response.status_code})."
            )
        raise AuthError(
            f"Unexpected status {response.status_code} from {url}. "
            "Cannot confirm credentials."
        )

    @staticmethod
    def _resolve_endpoint(profile: Profile, endpoint: str) -> str:
        """Expand a relative ``check_endpoint`` against ``profile.base_url``."""
        if endpoint.startswith(("http://", "https://")):
            return endpoint
        base = (profile.base_url or "").rstrip("/")
        if not base:
            raise AuthError(
                "Profile has no base_url; cannot resolve relative check_endpoint."
            )
        return f"{base}/{endpoint.lstrip('/')}"

    @staticmethod
    def _key_name(auth_config: AuthConfig) -> str:
        extras = auth_config.model_extra or {}
        return (
            extras.get("key_name")
            or auth_config.header
            or auth_config.param_name
            or "X-API-Key"
        )

    def _get_store(self, auth_config: AuthConfig) -> CredentialStore:
        profile_id = auth_config.credential_name or "api_login"
        return CredentialStore(profile_id)

    def _build_result(
        self,
        auth_config: AuthConfig,
        key: str,
        secret: Optional[str],
    ) -> AuthResult:
        """Place key (and optional secret) at the configured location."""
        extras = auth_config.model_extra or {}
        location = auth_config.location
        key_name = self._key_name(auth_config)
        secret_name = extras.get("secret_name")

        if location == "query":
            params = {key_name: key}
            if secret and secret_name:
                params[secret_name] = secret
            return AuthResult(params=params)
        if location == "cookie":
            cookies = {key_name: key}
            if secret and secret_name:
                cookies[secret_name] = secret
            return AuthResult(cookies=cookies)
        headers = {key_name: key}
        if secret and secret_name:
            headers[secret_name] = secret
        return AuthResult(headers=headers)
