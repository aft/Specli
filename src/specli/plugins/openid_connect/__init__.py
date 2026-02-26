"""OpenID Connect authentication plugin.

Implements the ``openid_connect`` auth type, which discovers OAuth2
endpoints from an OpenID Connect discovery document
(``/.well-known/openid-configuration``) and delegates the actual
authentication to the OAuth2 Authorization Code flow with PKCE.

See Also:
    :class:`~specli.plugins.openid_connect.plugin.OpenIDConnectPlugin`
    :mod:`specli.plugins.oauth2_auth_code` for the underlying flow.
    :mod:`specli.auth.base` for the plugin interface contract.
"""

from specli.plugins.openid_connect.plugin import OpenIDConnectPlugin

__all__ = ["OpenIDConnectPlugin"]
