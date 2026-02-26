"""API Key authentication plugin.

Implements the ``api_key`` auth type, which injects a static API key into
outgoing requests as a header, query parameter, or cookie. Optionally
supports a secondary API secret for two-key authentication schemes.

See Also:
    :class:`~specli.plugins.api_key.plugin.APIKeyAuthPlugin`
    :mod:`specli.auth.base` for the plugin interface contract.
"""

from specli.plugins.api_key.plugin import APIKeyAuthPlugin

__all__ = ["APIKeyAuthPlugin"]
