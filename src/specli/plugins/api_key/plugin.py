"""API Key auth plugin -- supports header, query parameter, and cookie placement.

This module provides the :class:`APIKeyAuthPlugin`, which resolves a
credential from the configured ``source`` (e.g. ``env:MY_API_KEY``) and
injects it into requests at the configured ``location`` (header, query,
or cookie). An optional second secret credential can be sent alongside
the primary key for APIs that require dual-key authentication.

See Also:
    :class:`specli.auth.base.AuthPlugin` for the base interface.
    :func:`specli.config.resolve_credential` for how ``source`` values
    are resolved.
"""

from __future__ import annotations

from specli.auth.base import AuthPlugin, AuthResult
from specli.config import resolve_credential
from specli.models import AuthConfig


class APIKeyAuthPlugin(AuthPlugin):
    """Authenticate via API key placed in a header, query parameter, or cookie.

    The key name is taken from ``auth_config.header`` (for header/cookie) or
    ``auth_config.param_name`` (for query).  The credential value is resolved
    from ``auth_config.source``.

    Supports an optional API secret via plugin-specific extra fields:
        - ``secret_source``: credential source for the secret (e.g. ``env:MY_SECRET``)
        - ``secret_header``: header/param name for the secret (defaults to ``X-API-Secret``)
    """

    @property
    def auth_type(self) -> str:
        return "api_key"

    @staticmethod
    def _extra(auth_config: AuthConfig, key: str, default: str | None = None) -> str | None:
        """Read a plugin-specific extra field from auth_config."""
        extras = auth_config.model_extra or {}
        return extras.get(key, default)

    def authenticate(self, auth_config: AuthConfig) -> AuthResult:
        """Resolve the API key and build an :class:`~specli.auth.base.AuthResult`.

        The key is placed according to ``auth_config.location``:

        * ``"header"`` -- sent as a request header (default key name ``X-API-Key``).
        * ``"query"``  -- sent as a query-string parameter (default name ``api_key``).
        * ``"cookie"`` -- sent as a cookie (default name ``api_key``).

        If ``secret_source`` is present in the config extras, a second
        credential is resolved and sent alongside the primary key.

        Args:
            auth_config: Profile auth configuration containing ``source``,
                ``location``, ``header``/``param_name``, and optional
                plugin-specific extras (``secret_source``, ``secret_header``).

        Returns:
            An :class:`~specli.auth.base.AuthResult` with the appropriate
            headers, params, or cookies populated.
        """
        credential = resolve_credential(auth_config.source)
        location = auth_config.location

        # Resolve optional API secret (for two-header auth like X-API-Key + X-API-Secret)
        secret = None
        secret_source = self._extra(auth_config, "secret_source")
        if secret_source:
            secret = resolve_credential(secret_source)

        if location == "header":
            key_name = auth_config.header or auth_config.param_name
            if not key_name:
                key_name = "X-API-Key"
            headers = {key_name: credential}
            if secret:
                secret_name = self._extra(auth_config, "secret_header") or "X-API-Secret"
                headers[secret_name] = secret
            return AuthResult(headers=headers)

        if location == "query":
            key_name = auth_config.param_name or auth_config.header
            if not key_name:
                key_name = "api_key"
            params = {key_name: credential}
            if secret:
                secret_name = self._extra(auth_config, "secret_header") or "api_secret"
                params[secret_name] = secret
            return AuthResult(params=params)

        if location == "cookie":
            key_name = auth_config.header or auth_config.param_name
            if not key_name:
                key_name = "api_key"
            cookies = {key_name: credential}
            if secret:
                secret_name = self._extra(auth_config, "secret_header") or "api_secret"
                cookies[secret_name] = secret
            return AuthResult(cookies=cookies)

        # Fallback: treat unknown location as header
        key_name = auth_config.header or auth_config.param_name or "X-API-Key"
        headers = {key_name: credential}
        if secret:
            secret_name = self._extra(auth_config, "secret_header") or "X-API-Secret"
            headers[secret_name] = secret
        return AuthResult(headers=headers)

    def validate_config(self, auth_config: AuthConfig) -> list[str]:
        """Check that required API key configuration fields are present.

        Args:
            auth_config: The auth configuration to validate.

        Returns:
            A list of human-readable error strings. Empty if valid.
        """
        errors: list[str] = []
        if not auth_config.header and not auth_config.param_name:
            errors.append(
                "API key auth requires 'header' or 'param_name' to specify the key name"
            )
        if not auth_config.source:
            errors.append("API key auth requires a 'source' for the credential")
        if auth_config.location not in ("header", "query", "cookie"):
            errors.append(
                f"Invalid location '{auth_config.location}': "
                "must be 'header', 'query', or 'cookie'"
            )
        return errors
