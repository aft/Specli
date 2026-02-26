"""OAuth2 Client Credentials authentication plugin.

Implements the ``oauth2_client_credentials`` auth type, which performs
the OAuth2 Client Credentials grant -- a non-interactive,
machine-to-machine flow that exchanges a ``client_id`` and
``client_secret`` for an access token.

Tokens are cached in memory and automatically refreshed when they expire.

See Also:
    :class:`~specli.plugins.oauth2_client_credentials.plugin.OAuth2ClientCredentialsPlugin`
    :mod:`specli.auth.base` for the plugin interface contract.
"""

from specli.plugins.oauth2_client_credentials.plugin import (
    OAuth2ClientCredentialsPlugin,
)

__all__ = ["OAuth2ClientCredentialsPlugin"]
