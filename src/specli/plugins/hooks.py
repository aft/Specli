"""Hook definitions, context dataclass, and runner for the plugin lifecycle.

This module provides two core components:

* :class:`HookContext` -- A mutable dataclass that carries request and response
  state through the hook chain. Fields are progressively populated as the
  request/response lifecycle advances.
* :class:`HookRunner` -- Executes ``on_pre_request``, ``on_post_response``,
  and ``on_error`` hooks across all loaded plugins in registration order.

The hook chain follows a pipeline pattern: each plugin receives the output
of the previous plugin, enabling additive transformations (e.g. injecting
auth headers, logging, response filtering).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from specli.plugins.base import Plugin


@dataclass
class HookContext:
    """Mutable context object threaded through the plugin hook chain.

    Fields are progressively filled depending on the lifecycle stage:

    * **Pre-request stage**: ``method``, ``url``, ``headers``, ``params``
      are populated before calling
      :meth:`~specli.plugins.base.Plugin.on_pre_request`.
    * **Post-response stage**: ``status_code``, ``response_headers``, and
      ``response_body`` are populated before calling
      :meth:`~specli.plugins.base.Plugin.on_post_response`.
    * **Error stage**: ``error`` is set when an exception occurs.

    Attributes:
        method: HTTP method (e.g. ``"GET"``).
        url: The fully resolved request URL.
        headers: Request headers dict (mutable).
        params: Request query/path parameters dict (mutable).
        body: Optional request body.
        status_code: HTTP response status code.
        response_headers: Response headers dict.
        response_body: Parsed response body.
        error: Exception instance if an error occurred, otherwise ``None``.
    """

    method: str = ""
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    body: Any = None
    status_code: int = 0
    response_headers: dict[str, str] = field(default_factory=dict)
    response_body: Any = None
    error: Optional[Exception] = None


class HookRunner:
    """Executes plugin hooks across all loaded plugins in registration order.

    The runner is created by
    :meth:`~specli.plugins.manager.PluginManager.get_hook_runner`
    and holds an immutable snapshot of the plugin list at creation time.
    If new plugins are loaded, a new runner must be obtained from the manager.
    """

    def __init__(self, plugins: list[Plugin]) -> None:
        """Initialize the hook runner with a list of plugins.

        Args:
            plugins: Ordered list of plugin instances. Hooks are executed
                in the order plugins appear in this list.
        """
        self._plugins = list(plugins)

    def run_pre_request(self, ctx: HookContext) -> HookContext:
        """Execute ``on_pre_request`` hooks across all plugins.

        Each plugin may modify ``ctx.headers`` and ``ctx.params``. The
        returned dict from each plugin replaces the context values for
        subsequent plugins, forming a pipeline.

        Args:
            ctx: The hook context with request fields populated.

        Returns:
            The same *ctx* instance with potentially modified headers
            and params.
        """
        for plugin in self._plugins:
            result = plugin.on_pre_request(ctx.method, ctx.url, ctx.headers, ctx.params)
            if isinstance(result, dict):
                ctx.headers = result.get("headers", ctx.headers)
                ctx.params = result.get("params", ctx.params)
        return ctx

    def run_post_response(self, ctx: HookContext) -> HookContext:
        """Execute ``on_post_response`` hooks across all plugins.

        Each plugin may inspect or transform the response body. The return
        value from each plugin replaces ``ctx.response_body`` for subsequent
        plugins.

        Args:
            ctx: The hook context with response fields populated.

        Returns:
            The same *ctx* instance with a potentially modified
            ``response_body``.
        """
        for plugin in self._plugins:
            ctx.response_body = plugin.on_post_response(
                ctx.status_code, ctx.response_headers, ctx.response_body
            )
        return ctx

    def run_error(self, error: Exception) -> None:
        """Execute ``on_error`` hooks across all plugins.

        Each plugin's ``on_error`` is called with the original exception.
        If a plugin's error handler itself raises, that secondary exception
        is silently swallowed to prevent plugin errors from masking the
        original failure.

        Args:
            error: The exception that triggered the error hook chain.
        """
        for plugin in self._plugins:
            try:
                plugin.on_error(error)
            except Exception:
                pass  # Don't let plugin errors cascade
