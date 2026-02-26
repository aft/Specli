"""Manual token authentication plugin.

Implements the ``manual_token`` auth type, which prompts the user to
paste a token interactively. The token can optionally be persisted via
:class:`~specli.auth.credential_store.CredentialStore` so subsequent
invocations skip the prompt.

See Also:
    :class:`~specli.plugins.manual_token.plugin.ManualTokenPlugin`
    :mod:`specli.auth.base` for the plugin interface contract.
"""

from specli.plugins.manual_token.plugin import ManualTokenPlugin

__all__ = ["ManualTokenPlugin"]
