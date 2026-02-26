"""HTTP Basic authentication plugin.

Implements the ``basic`` auth type, which encodes a ``username:password``
credential pair using Base64 and sends it as an ``Authorization: Basic``
header per :rfc:`7617`.

See Also:
    :class:`~specli.plugins.basic.plugin.BasicAuthPlugin`
    :mod:`specli.auth.base` for the plugin interface contract.
"""

from specli.plugins.basic.plugin import BasicAuthPlugin

__all__ = ["BasicAuthPlugin"]
