"""API Key Generation auth plugin -- create a key via API, persist, and reuse.

This module provides :class:`APIKeyGenPlugin`, which generates an API key by
calling a remote creation endpoint. Once generated, the key is persisted in
the local :class:`~specli.auth.credential_store.CredentialStore` and
reused on subsequent invocations without hitting the creation endpoint again.

Typical use case: an API that requires key provisioning before first use,
where the key does not expire (or expires far in the future).

See Also:
    :class:`specli.auth.base.AuthPlugin` for the base interface.
    :class:`specli.plugins.api_key.plugin.APIKeyAuthPlugin` for
    pre-existing (non-generated) API key auth.
"""

from __future__ import annotations

from typing import Any

import httpx

from specli.auth.base import AuthPlugin, AuthResult
from specli.auth.credential_store import CredentialEntry, CredentialStore
from specli.config import resolve_credential
from specli.exceptions import AuthError
from specli.models import AuthConfig


class APIKeyGenPlugin(AuthPlugin):
    """Authenticate by generating an API key via a creation endpoint.

    On first use, POSTs to ``key_create_endpoint`` with ``key_create_body``,
    extracts the key from ``key_response_field`` in the JSON response, persists
    it, and uses it for all subsequent requests.

    If a bootstrap credential is needed for the key creation request itself,
    ``key_create_auth_source`` resolves it via the standard credential sources.
    """

    @property
    def auth_type(self) -> str:
        return "api_key_gen"

    def authenticate(self, auth_config: AuthConfig) -> AuthResult:
        """Return an :class:`~specli.auth.base.AuthResult` with the generated API key.

        On first call (or when no persisted key exists), POSTs to the
        ``key_create_endpoint`` to generate a new key. On subsequent calls,
        returns the persisted key directly.

        Args:
            auth_config: Profile auth configuration. Must include
                ``key_create_endpoint`` and ``key_response_field``.
                Optionally ``key_create_body``, ``key_create_auth_source``,
                and ``persist``.

        Returns:
            An :class:`~specli.auth.base.AuthResult` with the key placed
            at the configured ``location`` (header, query, or cookie).

        Raises:
            AuthError: If the key creation endpoint is missing, the HTTP
                request fails, or the response does not contain the
                expected field.
        """
        # 1. Check credential store
        if auth_config.persist:
            store = self._get_store(auth_config)
            if store.is_valid():
                entry = store.load()
                if entry is not None:
                    return self._build_result(auth_config, entry.credential)

        # 2. Generate a new key
        if not auth_config.key_create_endpoint:
            raise AuthError("api_key_gen requires 'key_create_endpoint'")

        credential = self._create_key(auth_config)

        # 3. Persist (API keys typically don't expire)
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
        """Validate that key generation configuration fields are present.

        Args:
            auth_config: The auth configuration to validate.

        Returns:
            A list of human-readable error strings. Empty if valid.
        """
        errors: list[str] = []
        if not auth_config.key_create_endpoint:
            errors.append("api_key_gen requires 'key_create_endpoint'")
        if not auth_config.key_response_field:
            errors.append("api_key_gen requires 'key_response_field'")
        if auth_config.location not in ("header", "query", "cookie"):
            errors.append(
                f"Invalid location '{auth_config.location}': "
                "must be 'header', 'query', or 'cookie'"
            )
        return errors

    def _create_key(self, auth_config: AuthConfig) -> str:
        """POST to the key creation endpoint and extract the key.

        If ``key_create_auth_source`` is configured, its resolved value
        is sent as a ``Bearer`` token in the ``Authorization`` header of
        the creation request (bootstrap auth).

        Args:
            auth_config: Auth configuration containing endpoint URL,
                optional request body, and optional bootstrap auth source.

        Returns:
            The generated API key string extracted from the JSON response.

        Raises:
            AuthError: On HTTP errors or if the expected field is missing
                from the response payload.
        """
        headers: dict[str, str] = {"Accept": "application/json"}

        # Bootstrap auth if needed
        if auth_config.key_create_auth_source:
            bootstrap_cred = resolve_credential(auth_config.key_create_auth_source)
            headers["Authorization"] = f"Bearer {bootstrap_cred}"

        body = auth_config.key_create_body or {}

        try:
            response = httpx.post(
                auth_config.key_create_endpoint,  # type: ignore[arg-type]
                json=body,
                headers=headers,
                timeout=30.0,
            )
            response.raise_for_status()
            data: dict[str, Any] = response.json()
        except httpx.HTTPStatusError as exc:
            raise AuthError(
                f"Key creation failed with status {exc.response.status_code}: "
                f"{exc.response.text}"
            ) from exc
        except httpx.HTTPError as exc:
            raise AuthError(f"Key creation request failed: {exc}") from exc

        field = auth_config.key_response_field
        if field not in data:
            raise AuthError(
                f"Key creation response missing '{field}' field. "
                f"Available fields: {', '.join(data.keys())}"
            )

        return str(data[field])

    def _get_store(self, auth_config: AuthConfig) -> CredentialStore:
        profile_id = auth_config.credential_name or "api_key_gen"
        return CredentialStore(profile_id)

    def _resolve_name(self, auth_config: AuthConfig) -> str:
        return (
            auth_config.credential_name
            or auth_config.header
            or auth_config.param_name
            or "X-API-Key"
        )

    def _build_result(self, auth_config: AuthConfig, credential: str) -> AuthResult:
        """Build an :class:`~specli.auth.base.AuthResult` based on location.

        Args:
            auth_config: Auth configuration with ``location`` and name fields.
            credential: The API key string to inject.

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
