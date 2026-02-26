"""Plugin system for specli -- discovery, loading, and lifecycle hooks.

This package provides the extensibility layer for specli. Third-party
packages can register plugins by declaring an entry point in the
``specli.plugins`` group. At runtime, :class:`PluginManager` discovers
and loads those entry points, and the :class:`HookRunner` orchestrates
pre-request, post-response, and error hooks across all active plugins.

Key classes:

* :class:`Plugin` -- Abstract base class that all plugins must extend.
* :class:`PluginManager` -- Discovers, loads, and manages plugin lifecycle.
* :class:`HookRunner` -- Executes hooks across loaded plugins in order.
* :class:`HookContext` -- Mutable dataclass carrying request/response state
  through the hook chain.

Example:
    Typical usage from the main CLI entry point::

        from specli.plugins import PluginManager

        manager = PluginManager()
        manager.discover(global_config)
        runner = manager.get_hook_runner()
        ctx = HookContext(method="GET", url="/pets")
        ctx = runner.run_pre_request(ctx)
"""

from specli.plugins.base import Plugin
from specli.plugins.hooks import HookContext, HookRunner
from specli.plugins.manager import PluginManager

__all__ = ["Plugin", "HookContext", "HookRunner", "PluginManager"]
