"""Manual token auth plugin -- paste a token once, persist, and reuse.

This module provides :class:`ManualTokenPlugin`, which implements the
``manual_token`` auth type. On first use the user is prompted to paste
a token via :func:`getpass.getpass` (input is hidden). When
``persist=True``, the token is saved to a local
:class:`~specli.auth.credential_store.CredentialStore` and reused
without prompting on subsequent invocations.

This is the simplest interactive auth flow -- suitable for long-lived
tokens that cannot be resolved from environment variables or files.

See Also:
    :class:`specli.auth.base.AuthPlugin` for the base interface.
    :class:`specli.plugins.bearer.plugin.BearerAuthPlugin` for
    non-interactive static token auth.
"""

from __future__ import annotations

import getpass
import sys

from specli.auth.base import AuthPlugin, AuthResult
from specli.auth.credential_store import CredentialEntry, CredentialStore
from specli.exceptions import AuthError
from specli.models import AuthConfig


class ManualTokenPlugin(AuthPlugin):
    """Authenticate by manually pasting a token.

    If ``persist=True`` in the auth config, the token is saved to the
    credential store and reused on subsequent invocations without prompting.

    The ``credential_name`` field controls the header/cookie/param name.
    Falls back to ``header``, ``param_name``, or sensible defaults.
    """

    @property
    def auth_type(self) -> str:
        return "manual_token"

    def authenticate(self, auth_config: AuthConfig) -> AuthResult:
        """Return auth artifacts, prompting the user for a token if necessary.

        Checks the credential store first (when ``persist=True``). If no
        stored credential is found, prompts the user interactively via
        :func:`getpass.getpass` and optionally persists the result.

        Args:
            auth_config: Profile auth configuration. ``persist`` controls
                whether the token is saved. ``credential_name``, ``header``,
                and ``param_name`` control the name under which the token
                is sent.

        Returns:
            An :class:`~specli.auth.base.AuthResult` with the token
            placed at the configured ``location``.

        Raises:
            AuthError: If stdin is not a TTY (cannot prompt) or the user
                provides an empty token.
        """
        credential: str | None = None

        # 1. Check credential store
        if auth_config.persist:
            store = self._get_store(auth_config)
            if store.is_valid():
                entry = store.load()
                if entry is not None:
                    credential = entry.credential

        # 2. Prompt if no stored credential
        if credential is None:
            if not sys.stdin.isatty():
                raise AuthError(
                    "manual_token auth requires an interactive terminal to paste "
                    "the token (stdin must be a TTY)"
                )
            credential = getpass.getpass("Paste token: ")
            if not credential:
                raise AuthError("No token provided")

            # 3. Persist if requested
            if auth_config.persist:
                store = self._get_store(auth_config)
                store.save(
                    CredentialEntry(
                        auth_type=self.auth_type,
                        credential=credential,
                        credential_name=self._resolve_name(auth_config),
                    )
                )

        return self._build_result(auth_config, credential)

    def validate_config(self, auth_config: AuthConfig) -> list[str]:
        """Validate manual token configuration.

        Args:
            auth_config: The auth configuration to validate.

        Returns:
            A list of human-readable error strings. Empty if valid.
        """
        errors: list[str] = []
        if auth_config.location not in ("header", "query", "cookie"):
            errors.append(
                f"Invalid location '{auth_config.location}': "
                "must be 'header', 'query', or 'cookie'"
            )
        return errors

    def _get_store(self, auth_config: AuthConfig) -> CredentialStore:
        # Use the profile name from credential_name, or fall back to auth_type
        profile_id = auth_config.credential_name or "manual_token"
        return CredentialStore(profile_id)

    def _resolve_name(self, auth_config: AuthConfig) -> str:
        """Resolve the name for the credential (header/cookie/param)."""
        return (
            auth_config.credential_name
            or auth_config.header
            or auth_config.param_name
            or "Authorization"
        )

    def _build_result(self, auth_config: AuthConfig, credential: str) -> AuthResult:
        """Build an :class:`~specli.auth.base.AuthResult` based on location.

        Args:
            auth_config: Auth configuration with ``location`` and name fields.
            credential: The token string to inject.

        Returns:
            An :class:`~specli.auth.base.AuthResult` with the credential
            placed as a header, query parameter, or cookie.
        """
        name = self._resolve_name(auth_config)
        location = auth_config.location

        if location == "cookie":
            return AuthResult(cookies={name: credential})
        if location == "query":
            return AuthResult(params={name: credential})
        # Default: header
        return AuthResult(headers={name: credential})
