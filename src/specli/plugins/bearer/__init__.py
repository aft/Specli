"""Bearer token authentication plugin.

Implements the ``bearer`` auth type, which resolves a static token from
the configured ``source`` and sends it as an ``Authorization: Bearer``
header.

See Also:
    :class:`~specli.plugins.bearer.plugin.BearerAuthPlugin`
    :mod:`specli.auth.base` for the plugin interface contract.
"""

from specli.plugins.bearer.plugin import BearerAuthPlugin

__all__ = ["BearerAuthPlugin"]
