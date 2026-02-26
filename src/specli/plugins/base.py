"""Abstract base class for specli plugins.

Every plugin must subclass :class:`Plugin` and implement the :attr:`name`
property. The remaining lifecycle hooks (``on_init``, ``on_pre_request``,
``on_post_response``, ``on_error``, ``cleanup``) are optional -- default
implementations are no-ops so plugins only override what they need.

Plugins are registered as entry points in the ``specli.plugins`` group
and discovered at runtime by :class:`~specli.plugins.manager.PluginManager`.

Example:
    Minimal plugin implementation::

        class MyPlugin(Plugin):
            @property
            def name(self) -> str:
                return "my-plugin"

            def on_pre_request(self, method, url, headers, params):
                headers["X-Custom"] = "value"
                return {"headers": headers, "params": params}
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from specli.models import GlobalConfig


class Plugin(ABC):
    """Base class for all specli plugins.

    Subclasses must implement the :attr:`name` property. All hook methods have
    default no-op implementations so plugins only need to override the
    hooks they care about.

    The plugin lifecycle is:

    1. Instantiation -- the :class:`PluginManager` calls the no-arg constructor.
    2. :meth:`on_init` -- called once with the global configuration.
    3. Hook methods -- called zero or more times during CLI execution.
    4. :meth:`cleanup` -- called once during shutdown.

    See Also:
        :class:`~specli.plugins.hooks.HookRunner` for details on how
        hooks are chained across multiple plugins.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the unique plugin name used for discovery and logging.

        Returns:
            A short, human-readable identifier (e.g. ``"api-key"``).
        """
        ...

    @property
    def version(self) -> str:
        """Return the plugin version string.

        Returns:
            A semver-compatible version string. Defaults to ``"0.1.0"``.
        """
        return "0.1.0"

    @property
    def description(self) -> str:
        """Return a brief description of what the plugin does.

        Returns:
            A one-line description string. Defaults to ``""``.
        """
        return ""

    def on_init(self, config: GlobalConfig) -> None:
        """Called once when the plugin is loaded by the :class:`PluginManager`.

        Override this to perform any initialization that depends on the global
        configuration (e.g. reading credentials, setting up caches).

        Args:
            config: The global specli configuration containing profile
                settings, output preferences, and plugin enable/disable lists.
        """

    def on_pre_request(
        self, method: str, url: str, headers: dict[str, str], params: dict[str, Any]
    ) -> dict[str, Any]:
        """Called before each HTTP request is sent to the API.

        Plugins can inspect or modify the request headers and parameters.
        The returned dict replaces the originals for subsequent plugins in
        the chain, allowing plugins to build on each other's modifications.

        Args:
            method: HTTP method (e.g. ``"GET"``, ``"POST"``).
            url: The fully resolved request URL.
            headers: Mutable request headers dict.
            params: Mutable query/path parameters dict.

        Returns:
            A dict with ``"headers"`` and ``"params"`` keys containing the
            (possibly modified) values to pass to the next plugin or the
            HTTP client.
        """
        return {"headers": headers, "params": params}

    def on_post_response(
        self, status_code: int, headers: dict[str, str], body: Any
    ) -> Any:
        """Called after each HTTP response is received from the API.

        Plugins can inspect or transform the response body. The returned
        value replaces the body for subsequent plugins in the chain.

        Args:
            status_code: The HTTP response status code.
            headers: The response headers dict.
            body: The parsed response body (typically a dict or list).

        Returns:
            The (possibly modified) response body.
        """
        return body

    def on_error(self, error: Exception) -> None:
        """Called when an HTTP request or response processing raises an error.

        Override for custom error handling such as logging, metrics, or retry
        logic. Exceptions raised inside this method are silently swallowed
        by the :class:`~specli.plugins.hooks.HookRunner` to prevent
        plugin errors from masking the original failure.

        Args:
            error: The exception that was raised.
        """

    def cleanup(self) -> None:
        """Called once during shutdown to release plugin resources.

        Override to close file handles, flush caches, or tear down
        connections established in :meth:`on_init`.
        """
