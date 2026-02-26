"""Bearer token authentication plugin.

This module provides :class:`BearerAuthPlugin`, which implements the
``bearer`` auth type. A pre-existing token is resolved from the
configured ``source`` (e.g. ``env:MY_TOKEN``, ``file:~/.token``) and
injected as an ``Authorization: Bearer <token>`` header.

This plugin does not perform any token exchange or refresh -- it is
intended for tokens that are already available. For OAuth2-based token
acquisition, see :mod:`specli.plugins.oauth2_auth_code` or
:mod:`specli.plugins.oauth2_client_credentials`.

See Also:
    :class:`specli.auth.base.AuthPlugin` for the base interface.
"""

from __future__ import annotations

from specli.auth.base import AuthPlugin, AuthResult
from specli.config import resolve_credential
from specli.models import AuthConfig


class BearerAuthPlugin(AuthPlugin):
    """Authenticate via Bearer token in the Authorization header.

    Resolves the token from ``auth_config.source`` and returns an
    ``Authorization: Bearer <token>`` header.
    """

    @property
    def auth_type(self) -> str:
        return "bearer"

    def authenticate(self, auth_config: AuthConfig) -> AuthResult:
        """Resolve the token and return a Bearer auth header.

        Args:
            auth_config: Profile auth configuration. The ``source`` field
                must resolve to a valid bearer token string.

        Returns:
            An :class:`~specli.auth.base.AuthResult` containing an
            ``Authorization: Bearer <token>`` header.
        """
        token = resolve_credential(auth_config.source)
        return AuthResult(headers={"Authorization": f"Bearer {token}"})

    def validate_config(self, auth_config: AuthConfig) -> list[str]:
        """Check that a token source is configured.

        Args:
            auth_config: The auth configuration to validate.

        Returns:
            A list of human-readable error strings. Empty if valid.
        """
        errors: list[str] = []
        if not auth_config.source:
            errors.append("Bearer auth requires a 'source' for the token")
        return errors
