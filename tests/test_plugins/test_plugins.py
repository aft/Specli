"""Comprehensive tests for the specli plugin system."""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import patch

import pytest

from specli.exceptions import PluginError
from specli.models import GlobalConfig, PluginsConfig
from specli.plugins.base import Plugin
from specli.plugins.hooks import HookContext, HookRunner
from specli.plugins.manager import ENTRY_POINT_GROUP, PluginManager


# ---------------------------------------------------------------------------
# Test helpers — concrete Plugin subclasses
# ---------------------------------------------------------------------------


class MinimalPlugin(Plugin):
    """Smallest valid plugin — only implements the required ``name`` property."""

    @property
    def name(self) -> str:
        return "minimal"


class HeaderInjectorPlugin(Plugin):
    """Injects a custom header into every request."""

    @property
    def name(self) -> str:
        return "header-injector"

    def on_pre_request(
        self, method: str, url: str, headers: dict[str, str], params: dict[str, Any]
    ) -> dict[str, Any]:
        headers = {**headers, "X-Injected": "true"}
        return {"headers": headers, "params": params}


class ParamAppenderPlugin(Plugin):
    """Appends a query param to every request."""

    @property
    def name(self) -> str:
        return "param-appender"

    def on_pre_request(
        self, method: str, url: str, headers: dict[str, str], params: dict[str, Any]
    ) -> dict[str, Any]:
        params = {**params, "appended": "yes"}
        return {"headers": headers, "params": params}


class BodyUpperPlugin(Plugin):
    """Upper-cases the response body if it is a string."""

    @property
    def name(self) -> str:
        return "body-upper"

    def on_post_response(
        self, status_code: int, headers: dict[str, str], body: Any
    ) -> Any:
        if isinstance(body, str):
            return body.upper()
        return body


class BodyWrapPlugin(Plugin):
    """Wraps the response body in a dict."""

    @property
    def name(self) -> str:
        return "body-wrap"

    def on_post_response(
        self, status_code: int, headers: dict[str, str], body: Any
    ) -> Any:
        return {"wrapped": body}


class ExplodingErrorPlugin(Plugin):
    """Raises inside on_error to verify cascade protection."""

    @property
    def name(self) -> str:
        return "exploding-error"

    def on_error(self, error: Exception) -> None:
        raise RuntimeError("boom in error handler")


class ErrorCollectorPlugin(Plugin):
    """Collects errors for assertion."""

    def __init__(self) -> None:
        self.collected: list[Exception] = []

    @property
    def name(self) -> str:
        return "error-collector"

    def on_error(self, error: Exception) -> None:
        self.collected.append(error)


class CleanupTracker(Plugin):
    """Tracks cleanup calls."""

    def __init__(self) -> None:
        self.cleaned_up = False

    @property
    def name(self) -> str:
        return "cleanup-tracker"

    def cleanup(self) -> None:
        self.cleaned_up = True


class ExplodingCleanupPlugin(Plugin):
    """Raises during cleanup to verify resilience."""

    @property
    def name(self) -> str:
        return "exploding-cleanup"

    def cleanup(self) -> None:
        raise RuntimeError("cleanup explosion")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config() -> GlobalConfig:
    """A default GlobalConfig."""
    return GlobalConfig()


@pytest.fixture
def manager() -> PluginManager:
    """A fresh PluginManager."""
    return PluginManager()


# ---------------------------------------------------------------------------
# Plugin ABC tests
# ---------------------------------------------------------------------------


class TestPluginABC:
    """Test that Plugin enforces the abstract contract."""

    def test_cannot_instantiate_without_name(self) -> None:
        """Plugin subclass without ``name`` raises TypeError on instantiation."""
        with pytest.raises(TypeError):

            class BadPlugin(Plugin):
                pass

            BadPlugin()  # type: ignore[abstract]

    def test_default_version(self) -> None:
        plugin = MinimalPlugin()
        assert plugin.version == "0.1.0"

    def test_default_description(self) -> None:
        plugin = MinimalPlugin()
        assert plugin.description == ""

    def test_default_on_init_is_noop(self, config: GlobalConfig) -> None:
        plugin = MinimalPlugin()
        # Should not raise
        plugin.on_init(config)

    def test_default_on_pre_request_passthrough(self) -> None:
        plugin = MinimalPlugin()
        headers = {"Accept": "application/json"}
        params = {"q": "test"}
        result = plugin.on_pre_request("GET", "http://example.com", headers, params)
        assert result == {"headers": headers, "params": params}

    def test_default_on_post_response_passthrough(self) -> None:
        plugin = MinimalPlugin()
        body = {"data": [1, 2, 3]}
        result = plugin.on_post_response(200, {}, body)
        assert result is body

    def test_default_on_error_is_noop(self) -> None:
        plugin = MinimalPlugin()
        plugin.on_error(ValueError("test"))

    def test_default_cleanup_is_noop(self) -> None:
        plugin = MinimalPlugin()
        plugin.cleanup()


# ---------------------------------------------------------------------------
# HookContext tests
# ---------------------------------------------------------------------------


class TestHookContext:
    """Test HookContext dataclass creation and defaults."""

    def test_default_values(self) -> None:
        ctx = HookContext()
        assert ctx.method == ""
        assert ctx.url == ""
        assert ctx.headers == {}
        assert ctx.params == {}
        assert ctx.body is None
        assert ctx.status_code == 0
        assert ctx.response_headers == {}
        assert ctx.response_body is None
        assert ctx.error is None

    def test_custom_values(self) -> None:
        err = ValueError("oops")
        ctx = HookContext(
            method="POST",
            url="http://api.example.com/v1/users",
            headers={"Authorization": "Bearer tok"},
            params={"page": 1},
            body={"name": "Alice"},
            status_code=201,
            response_headers={"Content-Type": "application/json"},
            response_body={"id": 42},
            error=err,
        )
        assert ctx.method == "POST"
        assert ctx.url == "http://api.example.com/v1/users"
        assert ctx.headers == {"Authorization": "Bearer tok"}
        assert ctx.params == {"page": 1}
        assert ctx.body == {"name": "Alice"}
        assert ctx.status_code == 201
        assert ctx.response_headers == {"Content-Type": "application/json"}
        assert ctx.response_body == {"id": 42}
        assert ctx.error is err

    def test_dict_fields_are_independent(self) -> None:
        """Default factory produces independent dicts per instance."""
        ctx1 = HookContext()
        ctx2 = HookContext()
        ctx1.headers["X-Custom"] = "one"
        assert "X-Custom" not in ctx2.headers


# ---------------------------------------------------------------------------
# HookRunner tests
# ---------------------------------------------------------------------------


class TestHookRunner:
    """Test HookRunner pre_request, post_response, and error hooks."""

    def test_pre_request_modifies_headers(self) -> None:
        runner = HookRunner([HeaderInjectorPlugin()])
        ctx = HookContext(method="GET", url="http://example.com", headers={"Accept": "*/*"})
        result = runner.run_pre_request(ctx)
        assert result.headers["X-Injected"] == "true"
        assert result.headers["Accept"] == "*/*"

    def test_pre_request_modifies_params(self) -> None:
        runner = HookRunner([ParamAppenderPlugin()])
        ctx = HookContext(method="GET", url="http://example.com", params={"q": "hello"})
        result = runner.run_pre_request(ctx)
        assert result.params["appended"] == "yes"
        assert result.params["q"] == "hello"

    def test_pre_request_chain_order_preserved(self) -> None:
        """Header injector runs first, then param appender."""
        runner = HookRunner([HeaderInjectorPlugin(), ParamAppenderPlugin()])
        ctx = HookContext(method="GET", url="http://example.com")
        result = runner.run_pre_request(ctx)
        assert result.headers.get("X-Injected") == "true"
        assert result.params.get("appended") == "yes"

    def test_post_response_modifies_body(self) -> None:
        runner = HookRunner([BodyUpperPlugin()])
        ctx = HookContext(status_code=200, response_body="hello world")
        result = runner.run_post_response(ctx)
        assert result.response_body == "HELLO WORLD"

    def test_post_response_chain_order_preserved(self) -> None:
        """BodyUpper runs first (uppercases), then BodyWrap wraps."""
        runner = HookRunner([BodyUpperPlugin(), BodyWrapPlugin()])
        ctx = HookContext(status_code=200, response_body="hello")
        result = runner.run_post_response(ctx)
        assert result.response_body == {"wrapped": "HELLO"}

    def test_post_response_chain_reverse_order(self) -> None:
        """BodyWrap runs first (wraps dict), then BodyUpper (no-op on dict)."""
        runner = HookRunner([BodyWrapPlugin(), BodyUpperPlugin()])
        ctx = HookContext(status_code=200, response_body="hello")
        result = runner.run_post_response(ctx)
        # BodyWrap wraps into dict, BodyUpper sees dict (not str) so passes through
        assert result.response_body == {"wrapped": "hello"}

    def test_error_handler_does_not_cascade(self) -> None:
        """An exploding error plugin must not prevent subsequent plugins from running."""
        collector = ErrorCollectorPlugin()
        runner = HookRunner([ExplodingErrorPlugin(), collector])
        original_error = ValueError("original")
        runner.run_error(original_error)
        # The collector should still have been called despite the explosion
        assert len(collector.collected) == 1
        assert collector.collected[0] is original_error

    def test_error_handler_called_for_all_plugins(self) -> None:
        collector1 = ErrorCollectorPlugin()
        collector2 = ErrorCollectorPlugin()
        runner = HookRunner([collector1, collector2])
        err = RuntimeError("test")
        runner.run_error(err)
        assert len(collector1.collected) == 1
        assert len(collector2.collected) == 1

    def test_empty_plugin_list(self) -> None:
        """Hook runner with no plugins is a no-op passthrough."""
        runner = HookRunner([])
        ctx = HookContext(method="GET", url="http://x.com", headers={"A": "1"}, params={"b": "2"})
        result = runner.run_pre_request(ctx)
        assert result.headers == {"A": "1"}
        assert result.params == {"b": "2"}

        ctx2 = HookContext(status_code=200, response_body="intact")
        result2 = runner.run_post_response(ctx2)
        assert result2.response_body == "intact"

        # Should not raise
        runner.run_error(ValueError("no plugins"))


# ---------------------------------------------------------------------------
# PluginManager tests
# ---------------------------------------------------------------------------


class TestPluginManager:
    """Test PluginManager load, query, hook runner, and cleanup."""

    def test_load_plugin(self, manager: PluginManager, config: GlobalConfig) -> None:
        plugin = MinimalPlugin()
        manager.load_plugin("minimal", plugin, config)
        assert manager.get_plugin("minimal") is plugin

    def test_load_plugin_calls_on_init(
        self, manager: PluginManager, config: GlobalConfig
    ) -> None:
        """Verify on_init is called with the config during load."""

        class InitTracker(Plugin):
            def __init__(self) -> None:
                self.init_config: GlobalConfig | None = None

            @property
            def name(self) -> str:
                return "init-tracker"

            def on_init(self, config: GlobalConfig) -> None:
                self.init_config = config

        plugin = InitTracker()
        manager.load_plugin("init-tracker", plugin, config)
        assert plugin.init_config is config

    def test_load_plugin_duplicate_raises(
        self, manager: PluginManager, config: GlobalConfig
    ) -> None:
        manager.load_plugin("minimal", MinimalPlugin(), config)
        with pytest.raises(PluginError, match="already loaded"):
            manager.load_plugin("minimal", MinimalPlugin(), config)

    def test_get_plugin_unknown_raises(self, manager: PluginManager) -> None:
        with pytest.raises(PluginError, match="not loaded"):
            manager.get_plugin("nonexistent")

    def test_list_plugins_empty(self, manager: PluginManager) -> None:
        assert manager.list_plugins() == []

    def test_list_plugins(self, manager: PluginManager, config: GlobalConfig) -> None:
        manager.load_plugin("minimal", MinimalPlugin(), config)
        result = manager.list_plugins()
        assert len(result) == 1
        assert result[0]["name"] == "minimal"
        assert result[0]["version"] == "0.1.0"
        assert result[0]["description"] == ""

    def test_list_plugins_multiple(
        self, manager: PluginManager, config: GlobalConfig
    ) -> None:
        manager.load_plugin("header-injector", HeaderInjectorPlugin(), config)
        manager.load_plugin("body-upper", BodyUpperPlugin(), config)
        result = manager.list_plugins()
        names = [p["name"] for p in result]
        assert "header-injector" in names
        assert "body-upper" in names

    def test_get_hook_runner(
        self, manager: PluginManager, config: GlobalConfig
    ) -> None:
        manager.load_plugin("header-injector", HeaderInjectorPlugin(), config)
        runner = manager.get_hook_runner()
        assert isinstance(runner, HookRunner)

    def test_get_hook_runner_cached(
        self, manager: PluginManager, config: GlobalConfig
    ) -> None:
        manager.load_plugin("minimal", MinimalPlugin(), config)
        runner1 = manager.get_hook_runner()
        runner2 = manager.get_hook_runner()
        assert runner1 is runner2

    def test_get_hook_runner_invalidated_after_load(
        self, manager: PluginManager, config: GlobalConfig
    ) -> None:
        manager.load_plugin("minimal", MinimalPlugin(), config)
        runner1 = manager.get_hook_runner()
        manager.load_plugin("header-injector", HeaderInjectorPlugin(), config)
        runner2 = manager.get_hook_runner()
        assert runner1 is not runner2

    def test_get_hook_runner_empty(self, manager: PluginManager) -> None:
        runner = manager.get_hook_runner()
        assert isinstance(runner, HookRunner)

    def test_cleanup(self, manager: PluginManager, config: GlobalConfig) -> None:
        tracker = CleanupTracker()
        manager.load_plugin("cleanup-tracker", tracker, config)
        manager.cleanup()
        assert tracker.cleaned_up is True
        assert manager.list_plugins() == []

    def test_cleanup_resilient_to_exceptions(
        self, manager: PluginManager, config: GlobalConfig
    ) -> None:
        """Cleanup completes even if one plugin explodes."""
        tracker = CleanupTracker()
        manager.load_plugin("exploding-cleanup", ExplodingCleanupPlugin(), config)
        manager.load_plugin("cleanup-tracker", tracker, config)
        # Should not raise
        manager.cleanup()
        assert tracker.cleaned_up is True
        assert manager.list_plugins() == []

    def test_cleanup_resets_hook_runner(
        self, manager: PluginManager, config: GlobalConfig
    ) -> None:
        manager.load_plugin("minimal", MinimalPlugin(), config)
        runner_before = manager.get_hook_runner()
        manager.cleanup()
        runner_after = manager.get_hook_runner()
        assert runner_before is not runner_after

    def test_hook_runner_functional_after_load(
        self, manager: PluginManager, config: GlobalConfig
    ) -> None:
        """End-to-end: load plugin, get runner, run hooks."""
        manager.load_plugin("header-injector", HeaderInjectorPlugin(), config)
        runner = manager.get_hook_runner()
        ctx = HookContext(method="GET", url="http://example.com")
        result = runner.run_pre_request(ctx)
        assert result.headers["X-Injected"] == "true"


# ---------------------------------------------------------------------------
# PluginManager.discover() tests
# ---------------------------------------------------------------------------


class TestPluginManagerDiscover:
    """Test entry-point-based plugin discovery."""

    def _make_entry_point(self, name: str, plugin_cls: type) -> Any:
        """Create a mock entry point."""

        class MockEP:
            def __init__(self, n: str, cls: type) -> None:
                self.name = n
                self._cls = cls

            def load(self) -> type:
                return self._cls

        return MockEP(name, plugin_cls)

    def _make_entry_points_result(self, eps: list[Any]) -> Any:
        """Create a mock entry_points() return value with select()."""

        class MockEPs:
            def __init__(self, items: list[Any]) -> None:
                self._items = items

            def select(self, group: str) -> list[Any]:
                if group == ENTRY_POINT_GROUP:
                    return self._items
                return []

        return MockEPs(eps)

    def test_discover_loads_plugins(self, config: GlobalConfig) -> None:
        manager = PluginManager()
        eps = [self._make_entry_point("minimal", MinimalPlugin)]
        mock_result = self._make_entry_points_result(eps)

        with patch("specli.plugins.manager.importlib.metadata.entry_points", return_value=mock_result):
            loaded = manager.discover(config)

        assert loaded == ["minimal"]
        assert manager.get_plugin("minimal").name == "minimal"

    def test_discover_respects_disabled(self) -> None:
        config = GlobalConfig(plugins=PluginsConfig(disabled=["minimal"]))
        manager = PluginManager()
        eps = [self._make_entry_point("minimal", MinimalPlugin)]
        mock_result = self._make_entry_points_result(eps)

        with patch("specli.plugins.manager.importlib.metadata.entry_points", return_value=mock_result):
            loaded = manager.discover(config)

        assert loaded == []
        with pytest.raises(PluginError):
            manager.get_plugin("minimal")

    def test_discover_respects_enabled_allowlist(self) -> None:
        config = GlobalConfig(plugins=PluginsConfig(enabled=["header-injector"]))
        manager = PluginManager()
        eps = [
            self._make_entry_point("minimal", MinimalPlugin),
            self._make_entry_point("header-injector", HeaderInjectorPlugin),
        ]
        mock_result = self._make_entry_points_result(eps)

        with patch("specli.plugins.manager.importlib.metadata.entry_points", return_value=mock_result):
            loaded = manager.discover(config)

        assert loaded == ["header-injector"]
        with pytest.raises(PluginError):
            manager.get_plugin("minimal")

    def test_discover_handles_broken_plugin(self, config: GlobalConfig) -> None:
        """A broken entry point should not prevent other plugins from loading."""

        class BrokenEP:
            name = "broken"

            def load(self) -> type:
                raise ImportError("missing dependency")

        manager = PluginManager()
        eps = [BrokenEP(), self._make_entry_point("minimal", MinimalPlugin)]
        mock_result = self._make_entry_points_result(eps)

        with patch("specli.plugins.manager.importlib.metadata.entry_points", return_value=mock_result):
            loaded = manager.discover(config)

        assert loaded == ["minimal"]

    def test_discover_no_entry_points(self, config: GlobalConfig) -> None:
        manager = PluginManager()
        mock_result = self._make_entry_points_result([])

        with patch("specli.plugins.manager.importlib.metadata.entry_points", return_value=mock_result):
            loaded = manager.discover(config)

        assert loaded == []
        assert manager.list_plugins() == []


# ---------------------------------------------------------------------------
# ExamplePlugin tests
# ---------------------------------------------------------------------------


class TestExamplePlugin:
    """Test the bundled ExamplePlugin."""

    def test_properties(self) -> None:
        # Import here so the test fails clearly if the module is missing
        from plugins.example_plugin.plugin import ExamplePlugin

        plugin = ExamplePlugin()
        assert plugin.name == "example"
        assert plugin.version == "0.1.0"
        assert "log" in plugin.description.lower() or "example" in plugin.description.lower()

    def test_on_init(self, config: GlobalConfig) -> None:
        from plugins.example_plugin.plugin import ExamplePlugin

        plugin = ExamplePlugin()
        assert plugin._initialized is False
        plugin.on_init(config)
        assert plugin._initialized is True

    def test_on_pre_request_logs(self, config: GlobalConfig, capsys: pytest.CaptureFixture[str]) -> None:
        from plugins.example_plugin.plugin import ExamplePlugin

        plugin = ExamplePlugin()
        result = plugin.on_pre_request("GET", "http://api.example.com/v1/users", {"Accept": "*/*"}, {"page": 1})
        captured = capsys.readouterr()
        assert "[example] GET http://api.example.com/v1/users" in captured.err
        assert result == {"headers": {"Accept": "*/*"}, "params": {"page": 1}}

    def test_on_post_response_logs(self, capsys: pytest.CaptureFixture[str]) -> None:
        from plugins.example_plugin.plugin import ExamplePlugin

        plugin = ExamplePlugin()
        body = {"data": "test"}
        result = plugin.on_post_response(200, {}, body)
        captured = capsys.readouterr()
        assert "[example] Response: 200" in captured.err
        assert result is body

    def test_cleanup(self, config: GlobalConfig) -> None:
        from plugins.example_plugin.plugin import ExamplePlugin

        plugin = ExamplePlugin()
        plugin.on_init(config)
        assert plugin._initialized is True
        plugin.cleanup()
        assert plugin._initialized is False


# ---------------------------------------------------------------------------
# Multiple plugins chain integration tests
# ---------------------------------------------------------------------------


class TestMultiPluginChain:
    """Integration tests for multi-plugin hook chains via PluginManager."""

    def test_pre_request_chain_via_manager(self, config: GlobalConfig) -> None:
        manager = PluginManager()
        manager.load_plugin("header-injector", HeaderInjectorPlugin(), config)
        manager.load_plugin("param-appender", ParamAppenderPlugin(), config)

        runner = manager.get_hook_runner()
        ctx = HookContext(
            method="POST",
            url="http://api.example.com",
            headers={"Content-Type": "application/json"},
            params={"existing": "value"},
        )
        result = runner.run_pre_request(ctx)

        assert result.headers["X-Injected"] == "true"
        assert result.headers["Content-Type"] == "application/json"
        assert result.params["appended"] == "yes"
        assert result.params["existing"] == "value"

    def test_post_response_chain_via_manager(self, config: GlobalConfig) -> None:
        manager = PluginManager()
        manager.load_plugin("body-upper", BodyUpperPlugin(), config)
        manager.load_plugin("body-wrap", BodyWrapPlugin(), config)

        runner = manager.get_hook_runner()
        ctx = HookContext(status_code=200, response_body="hello")
        result = runner.run_post_response(ctx)

        # BodyUpper uppercases, then BodyWrap wraps
        assert result.response_body == {"wrapped": "HELLO"}

    def test_error_chain_via_manager(self, config: GlobalConfig) -> None:
        collector = ErrorCollectorPlugin()
        manager = PluginManager()
        manager.load_plugin("exploding-error", ExplodingErrorPlugin(), config)
        manager.load_plugin("error-collector", collector, config)

        runner = manager.get_hook_runner()
        err = ValueError("test error")
        runner.run_error(err)

        assert len(collector.collected) == 1
        assert collector.collected[0] is err

    def test_full_lifecycle(self, config: GlobalConfig) -> None:
        """Load, run hooks, cleanup -- full lifecycle."""
        manager = PluginManager()
        tracker = CleanupTracker()
        manager.load_plugin("header-injector", HeaderInjectorPlugin(), config)
        manager.load_plugin("cleanup-tracker", tracker, config)

        # Run a hook
        runner = manager.get_hook_runner()
        ctx = HookContext(method="GET", url="http://example.com")
        result = runner.run_pre_request(ctx)
        assert result.headers.get("X-Injected") == "true"

        # Verify listing
        names = [p["name"] for p in manager.list_plugins()]
        assert "header-injector" in names
        assert "cleanup-tracker" in names

        # Cleanup
        manager.cleanup()
        assert tracker.cleaned_up is True
        assert manager.list_plugins() == []
