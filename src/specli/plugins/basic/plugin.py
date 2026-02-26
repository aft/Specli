"""HTTP Basic authentication plugin.

This module provides :class:`BasicAuthPlugin`, which implements the
``basic`` auth type. The credential ``source`` is resolved to a
``"username:password"`` string, Base64-encoded, and sent as an
``Authorization: Basic <encoded>`` header per :rfc:`7617`.

See Also:
    :class:`specli.auth.base.AuthPlugin` for the base interface.
"""

from __future__ import annotations

import base64

from specli.auth.base import AuthPlugin, AuthResult
from specli.config import resolve_credential
from specli.exceptions import AuthError
from specli.models import AuthConfig


class BasicAuthPlugin(AuthPlugin):
    """Authenticate via HTTP Basic authentication.

    The credential source must resolve to a ``"username:password"`` string.
    The combined value is Base64-encoded and sent as an
    ``Authorization: Basic <encoded>`` header.
    """

    @property
    def auth_type(self) -> str:
        return "basic"

    def authenticate(self, auth_config: AuthConfig) -> AuthResult:
        """Resolve the credential and return a Basic auth header.

        The credential must be in ``"username:password"`` format. It is
        Base64-encoded and returned as an ``Authorization: Basic <encoded>``
        header.

        Args:
            auth_config: Profile auth configuration. The ``source`` field
                must resolve to a ``"username:password"`` string.

        Returns:
            An :class:`~specli.auth.base.AuthResult` containing the
            ``Authorization`` header.

        Raises:
            AuthError: If the resolved credential does not contain a colon
                separator.
        """
        raw = resolve_credential(auth_config.source)
        if ":" not in raw:
            raise AuthError(
                "Basic auth credential must be in 'username:password' format "
                "(colon separator is required)"
            )
        encoded = base64.b64encode(raw.encode("utf-8")).decode("ascii")
        return AuthResult(headers={"Authorization": f"Basic {encoded}"})

    def validate_config(self, auth_config: AuthConfig) -> list[str]:
        """Check that a credential source is configured.

        Args:
            auth_config: The auth configuration to validate.

        Returns:
            A list of human-readable error strings. Empty if valid.
        """
        errors: list[str] = []
        if not auth_config.source:
            errors.append("Basic auth requires a 'source' for the credential")
        return errors
