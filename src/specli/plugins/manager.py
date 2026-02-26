"""Plugin manager -- discovery, loading, and lifecycle management.

This module contains :class:`PluginManager`, the central coordinator for the
plugin system. It discovers plugins registered as Python entry points,
applies enable/disable filtering from the global configuration, and provides
a lazily-cached :class:`~specli.plugins.hooks.HookRunner` instance for
executing hooks across all loaded plugins.

The entry-point group used for discovery is ``specli.plugins``.
Third-party packages register plugins by declaring an entry point under this
group in their ``pyproject.toml``::

    [project.entry-points."specli.plugins"]
    my-plugin = "my_package.plugin:MyPlugin"
"""

from __future__ import annotations

import importlib.metadata
import logging
from typing import Optional

from specli.exceptions import PluginError
from specli.models import GlobalConfig
from specli.plugins.base import Plugin
from specli.plugins.hooks import HookRunner

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "specli.plugins"
"""The setuptools entry-point group name used for plugin discovery."""


class PluginManager:
    """Discovers, loads, and manages the lifecycle of specli plugins.

    Plugin discovery uses the ``specli.plugins`` entry-point group so
    third-party packages can register plugins by adding an entry point in
    their ``pyproject.toml``.

    The *enabled* and *disabled* lists in
    :class:`~specli.models.PluginsConfig` (nested inside
    :class:`~specli.models.GlobalConfig`) act as an explicit
    allowlist/blocklist. When *enabled* is non-empty only those plugins are
    loaded; otherwise all discovered plugins that are **not** in *disabled*
    are loaded.

    Example:
        Typical usage::

            manager = PluginManager()
            loaded = manager.discover(global_config)
            print(f"Loaded {len(loaded)} plugins")
            runner = manager.get_hook_runner()
    """

    def __init__(self) -> None:
        self._plugins: dict[str, Plugin] = {}
        self._hook_runner: Optional[HookRunner] = None

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover(self, config: GlobalConfig) -> list[str]:
        """Discover and load available plugins via Python entry points.

        Iterates over all entry points in the ``specli.plugins`` group,
        filters them against the enabled/disabled lists in *config*, and loads
        each qualifying plugin by calling :meth:`load_plugin`.

        Args:
            config: The global configuration whose ``plugins.enabled`` and
                ``plugins.disabled`` lists control which plugins are loaded.

        Returns:
            A list of plugin names that were successfully loaded. Plugins
            that fail to load are logged as warnings and skipped.
        """
        loaded_names: list[str] = []
        enabled_set = set(config.plugins.enabled)
        disabled_set = set(config.plugins.disabled)

        entry_points = importlib.metadata.entry_points()
        # Python 3.12+ returns a SelectableGroups; 3.9+ returns a dict.
        if hasattr(entry_points, "select"):
            eps = entry_points.select(group=ENTRY_POINT_GROUP)
        else:
            eps = entry_points.get(ENTRY_POINT_GROUP, [])  # type: ignore[union-attr]

        for ep in eps:
            name = ep.name

            # Filtering: if an explicit enabled list exists, only load those.
            if enabled_set and name not in enabled_set:
                logger.debug("Plugin '%s' not in enabled list, skipping", name)
                continue
            if name in disabled_set:
                logger.debug("Plugin '%s' is disabled, skipping", name)
                continue

            try:
                plugin_cls = ep.load()
                plugin: Plugin = plugin_cls()
                self.load_plugin(name, plugin, config)
                loaded_names.append(name)
            except Exception as exc:
                logger.warning("Failed to load plugin '%s': %s", name, exc)

        return loaded_names

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_plugin(self, name: str, plugin: Plugin, config: GlobalConfig) -> None:
        """Load and initialize a single plugin instance.

        Calls :meth:`~specli.plugins.base.Plugin.on_init` on the plugin,
        registers it internally, and invalidates the cached
        :class:`~specli.plugins.hooks.HookRunner` so it will be rebuilt
        with the new plugin on next access.

        Args:
            name: The unique name to register the plugin under.
            plugin: The plugin instance to load.
            config: The global configuration passed to the plugin's
                ``on_init`` method.

        Raises:
            PluginError: If a plugin with the same *name* is already loaded.
        """
        if name in self._plugins:
            raise PluginError(f"Plugin '{name}' is already loaded")

        plugin.on_init(config)
        self._plugins[name] = plugin
        # Invalidate cached hook runner so it picks up the new plugin.
        self._hook_runner = None
        logger.info("Loaded plugin '%s' v%s", name, plugin.version)

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def get_plugin(self, name: str) -> Plugin:
        """Retrieve a loaded plugin by its registered name.

        Args:
            name: The unique plugin name to look up.

        Returns:
            The :class:`~specli.plugins.base.Plugin` instance.

        Raises:
            PluginError: If no plugin with the given *name* is loaded.
        """
        try:
            return self._plugins[name]
        except KeyError:
            raise PluginError(f"Plugin '{name}' is not loaded") from None

    def list_plugins(self) -> list[dict[str, str]]:
        """List all loaded plugins with their metadata.

        Returns:
            A list of dicts, each containing ``"name"``, ``"version"``, and
            ``"description"`` keys corresponding to the plugin's properties.
        """
        return [
            {
                "name": plugin.name,
                "version": plugin.version,
                "description": plugin.description,
            }
            for plugin in self._plugins.values()
        ]

    # ------------------------------------------------------------------
    # Hook runner
    # ------------------------------------------------------------------

    def get_hook_runner(self) -> HookRunner:
        """Return the :class:`~specli.plugins.hooks.HookRunner` for all loaded plugins.

        The runner is lazily created on first access and cached. The cache is
        automatically invalidated whenever :meth:`load_plugin` registers a
        new plugin, so subsequent calls rebuild the runner with the updated
        plugin list.

        Returns:
            A :class:`~specli.plugins.hooks.HookRunner` wrapping all
            currently loaded plugins in registration order.
        """
        if self._hook_runner is None:
            self._hook_runner = HookRunner(list(self._plugins.values()))
        return self._hook_runner

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def cleanup(self) -> None:
        """Clean up all loaded plugins and reset internal state.

        Calls :meth:`~specli.plugins.base.Plugin.cleanup` on every
        loaded plugin. Exceptions from individual plugins are logged and
        swallowed so that one plugin's failure does not prevent others from
        cleaning up. After all plugins are cleaned up, the internal registry
        and cached hook runner are cleared.
        """
        for name, plugin in self._plugins.items():
            try:
                plugin.cleanup()
            except Exception as exc:
                logger.warning("Error cleaning up plugin '%s': %s", name, exc)
        self._plugins.clear()
        self._hook_runner = None
