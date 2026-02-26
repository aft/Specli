"""OAuth2 Device Authorization Grant (:rfc:`8628`) authentication plugin.

Implements the ``device_code`` auth type, designed for headless or
browserless terminals (SSH sessions, Docker containers, CI runners).
The user is shown a URL and a short code to enter on another device,
then the CLI polls for authorization.

See Also:
    :class:`~specli.plugins.device_code.plugin.DeviceCodePlugin`
    :mod:`specli.auth.base` for the plugin interface contract.
"""

from specli.plugins.device_code.plugin import DeviceCodePlugin

__all__ = ["DeviceCodePlugin"]
