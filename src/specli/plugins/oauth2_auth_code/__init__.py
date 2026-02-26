"""OAuth2 Authorization Code authentication plugin with PKCE.

Implements the ``oauth2_auth_code`` auth type, which performs a full
OAuth2 Authorization Code grant with Proof Key for Code Exchange (PKCE)
per :rfc:`7636`. A temporary local HTTP server captures the callback,
and the authorization code is exchanged for access and refresh tokens.

Exports:
    :class:`OAuth2AuthCodePlugin` -- the plugin class.
    :func:`generate_pkce_pair` -- utility to generate a PKCE
    ``code_verifier`` / ``code_challenge`` pair (also used by
    :mod:`specli.plugins.browser_login`).

See Also:
    :mod:`specli.auth.base` for the plugin interface contract.
"""

from specli.plugins.oauth2_auth_code.plugin import (
    OAuth2AuthCodePlugin,
    generate_pkce_pair,
)

__all__ = ["OAuth2AuthCodePlugin", "generate_pkce_pair"]
