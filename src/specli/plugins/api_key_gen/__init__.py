"""API Key Generation authentication plugin.

Implements the ``api_key_gen`` auth type, which auto-generates an API key
by POSTing to a key-creation endpoint, persists the returned key via
:class:`~specli.auth.credential_store.CredentialStore`, and reuses
it for subsequent requests.

This is useful for APIs that require you to create a key through a signup
or provisioning endpoint before you can call the actual API.

See Also:
    :class:`~specli.plugins.api_key_gen.plugin.APIKeyGenPlugin`
    :mod:`specli.auth.base` for the plugin interface contract.
"""

from specli.plugins.api_key_gen.plugin import APIKeyGenPlugin

__all__ = ["APIKeyGenPlugin"]
