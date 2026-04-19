"""Interactive API-login auth plugin.

Implements the ``api_login`` auth type: a prompt-verify-persist flow.
The user runs ``login`` once, pastes a key (and optional secret), the
credentials are verified by hitting a configured check endpoint, and on
success they are persisted to the credential store and reused on every
subsequent request until ``logout`` is called.

See Also:
    :class:`~specli.plugins.api_login.plugin.APILoginPlugin`
    :class:`~specli.plugins.api_key.plugin.APIKeyAuthPlugin` -- the
    non-interactive sibling that resolves credentials from env/file/plain
    on every request.
"""

from specli.plugins.api_login.plugin import APILoginPlugin

__all__ = ["APILoginPlugin"]
