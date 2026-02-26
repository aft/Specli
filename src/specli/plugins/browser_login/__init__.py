"""Browser-based login authentication plugin.

Implements the ``browser_login`` auth type, which opens the user's web browser
to perform an interactive login and captures the resulting credential from
a local callback server.

Supports two modes:

* **OAuth mode** -- full OAuth2 Authorization Code flow with PKCE when
  ``authorization_url``, ``token_url``, and ``client_id_source`` are set.
* **Simple mode** -- opens a ``login_url`` and captures a credential from
  a redirect callback (cookie, header, query param, or JSON body field).

See Also:
    :class:`~specli.plugins.browser_login.plugin.BrowserLoginPlugin`
    :mod:`specli.auth.base` for the plugin interface contract.
"""

from specli.plugins.browser_login.plugin import BrowserLoginPlugin

__all__ = ["BrowserLoginPlugin"]
