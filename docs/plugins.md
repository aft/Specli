# Plugins

specli has a plugin system that lets you hook into the request/response lifecycle, modify headers, transform response data, or add custom error handling. Plugins are discovered via Python entry points so they can be distributed as standalone packages.

## Plugin Architecture

```
specli command invoked
    |
    v
PluginManager.discover()        # Finds plugins via entry points
    |
    v
Plugin.on_init(config)          # Each plugin initializes
    |
    v
[User runs an API command]
    |
    v
HookRunner.run_pre_request()    # All plugins modify headers/params
    |
    v
httpx sends the HTTP request
    |
    v
HookRunner.run_post_response()  # All plugins process the response
    |
    v
[On error]
HookRunner.run_error()          # All plugins handle the error
    |
    v
Plugin.cleanup()                # Each plugin cleans up on exit
```

Plugins run in registration order. Each plugin in the chain receives the output of the previous plugin, so modifications accumulate.

## Creating a Plugin

### Step 1: Define your Plugin class

Create a Python class that extends `Plugin` and overrides the hooks you need:

```python
# my_specli_plugin/plugin.py
from specli.plugins.base import Plugin
from specli.models import GlobalConfig
from typing import Any


class RequestLoggerPlugin(Plugin):
    """Logs every request and response to a file."""

    @property
    def name(self) -> str:
        return "request-logger"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def description(self) -> str:
        return "Logs all HTTP requests and responses to a file"

    def on_init(self, config: GlobalConfig) -> None:
        """Called once when the plugin is loaded.

        Use this to set up resources, read additional config,
        or validate prerequisites.
        """
        self._log_file = open("/tmp/specli-requests.log", "a")

    def on_pre_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Called before every HTTP request.

        You can inspect and modify headers and params. You MUST return
        a dict with 'headers' and 'params' keys.
        """
        self._log_file.write(f">>> {method} {url}\n")
        self._log_file.flush()

        # Add a custom header
        headers["X-Request-Source"] = "specli"

        return {"headers": headers, "params": params}

    def on_post_response(
        self,
        status_code: int,
        headers: dict[str, str],
        body: Any,
    ) -> Any:
        """Called after every HTTP response.

        You can inspect and modify the response body. Return the
        (possibly modified) body.
        """
        self._log_file.write(f"<<< {status_code}\n")
        self._log_file.flush()
        return body

    def on_error(self, error: Exception) -> None:
        """Called when an error occurs.

        Plugin exceptions in this hook are silently swallowed to prevent
        plugin errors from masking the original error.
        """
        self._log_file.write(f"!!! Error: {error}\n")
        self._log_file.flush()

    def cleanup(self) -> None:
        """Called when specli exits.

        Use this to release resources.
        """
        if hasattr(self, "_log_file"):
            self._log_file.close()
```

### Step 2: Register the entry point

In your package's `pyproject.toml`, add an entry point in the `specli.plugins` group:

```toml
[project]
name = "my-specli-plugin"
version = "1.0.0"
dependencies = ["specli>=0.1.0"]

[project.entry-points."specli.plugins"]
request-logger = "my_specli_plugin.plugin:RequestLoggerPlugin"
```

The entry point name (left side) is the plugin name used in enable/disable lists. The value (right side) is the `module:ClassName` path.

### Step 3: Install and verify

```bash
# Install your plugin package
pip install -e ./my-specli-plugin

# The plugin is automatically discovered and loaded
specli api users list
# Check /tmp/specli-requests.log for entries
```

## Plugin Base Class API

The `Plugin` abstract base class defines all available hooks:

```python
class Plugin(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """Unique plugin name. Required."""
        ...

    @property
    def version(self) -> str:
        """Plugin version. Default: '0.1.0'."""
        return "0.1.0"

    @property
    def description(self) -> str:
        """Plugin description. Default: ''."""
        return ""

    def on_init(self, config: GlobalConfig) -> None:
        """Called when plugin is loaded. Override to initialize."""

    def on_pre_request(
        self, method: str, url: str, headers: dict[str, str], params: dict[str, Any]
    ) -> dict[str, Any]:
        """Modify request before sending.
        Return a dict with 'headers' and 'params' keys."""
        return {"headers": headers, "params": params}

    def on_post_response(
        self, status_code: int, headers: dict[str, str], body: Any
    ) -> Any:
        """Process response after receiving. Return (possibly modified) body."""
        return body

    def on_error(self, error: Exception) -> None:
        """Handle errors. Exceptions are silently swallowed."""

    def cleanup(self) -> None:
        """Called on shutdown. Override for cleanup."""
```

## Hook Lifecycle

### `on_init(config)`

Called once when the plugin is loaded during startup. Receives the `GlobalConfig` instance. Use it to:

- Initialize logging or connections
- Read plugin-specific configuration
- Validate prerequisites

If `on_init` raises an exception, the plugin is not loaded and a warning is logged.

### `on_pre_request(method, url, headers, params)`

Called before every HTTP request, after auth injection. Receives:

- `method` -- HTTP method string (e.g., `"GET"`, `"POST"`)
- `url` -- Full URL including base URL and path
- `headers` -- Mutable dict of request headers
- `params` -- Mutable dict of query parameters

Must return a dict with `headers` and `params` keys. The returned values replace the originals for the next plugin in the chain.

Common use cases:
- Add custom headers (correlation IDs, tracing)
- Transform parameter values
- Log request details

### `on_post_response(status_code, headers, body)`

Called after every HTTP response, before error mapping. Receives:

- `status_code` -- HTTP status code (int)
- `headers` -- Response headers (dict)
- `body` -- Response body (parsed JSON or raw text)

Must return the body (possibly modified). The returned value is passed to the next plugin in the chain.

Common use cases:
- Transform response data
- Log response details
- Collect metrics

### `on_error(error)`

Called when an error occurs (after all retries for connection errors, or on HTTP 4xx/5xx). Receives the exception instance.

This hook is for observation only -- the error is re-raised after all plugins have been notified. Exceptions raised inside `on_error` are silently swallowed to prevent plugin bugs from masking the original error.

Common use cases:
- Error reporting / alerting
- Metrics collection
- Debug logging

### `cleanup()`

Called when specli exits. Use it to close file handles, flush buffers, or release other resources. Exceptions in `cleanup` are caught and logged as warnings.

## Hook Runner

The `HookRunner` class executes hooks across all loaded plugins in registration order:

```python
from specli.plugins.hooks import HookRunner, HookContext

# Plugins are chained: each receives the output of the previous
runner = HookRunner([plugin_a, plugin_b, plugin_c])

# Pre-request: each plugin can modify headers and params
ctx = HookContext(method="GET", url="https://api.example.com/users", headers={}, params={})
ctx = runner.run_pre_request(ctx)

# Post-response: each plugin can modify the body
ctx.status_code = 200
ctx.response_headers = {"content-type": "application/json"}
ctx.response_body = [{"id": 1, "name": "Alice"}]
ctx = runner.run_post_response(ctx)

# Error: all plugins are notified
runner.run_error(ConnectionError("timeout"))
```

## Enabling and Disabling Plugins

By default, all discovered plugins are loaded. You can control which plugins are active through the global config:

### Allowlist (load only these)

```json
{
  "plugins": {
    "enabled": ["request-logger", "metrics"]
  }
}
```

When `enabled` is non-empty, only the listed plugins are loaded. All others are skipped.

### Blocklist (skip these)

```json
{
  "plugins": {
    "disabled": ["request-logger"]
  }
}
```

When `enabled` is empty, all discovered plugins except those in `disabled` are loaded.

### Precedence

If both `enabled` and `disabled` are set, only `enabled` is used. The `disabled` list is ignored when an explicit `enabled` list exists.

## Example: Rate Limit Plugin

A practical example that tracks rate limit headers and pauses before exceeding the limit:

```python
import time
from specli.plugins.base import Plugin
from specli.models import GlobalConfig
from typing import Any


class RateLimitPlugin(Plugin):
    @property
    def name(self) -> str:
        return "rate-limiter"

    @property
    def description(self) -> str:
        return "Respects X-RateLimit-Remaining headers"

    def on_init(self, config: GlobalConfig) -> None:
        self._remaining = None
        self._reset_at = None

    def on_pre_request(
        self, method: str, url: str, headers: dict[str, str], params: dict[str, Any]
    ) -> dict[str, Any]:
        if self._remaining is not None and self._remaining <= 1:
            if self._reset_at:
                wait = max(0, self._reset_at - time.time())
                if wait > 0:
                    import sys
                    print(
                        f"Rate limit reached, waiting {wait:.0f}s...",
                        file=sys.stderr,
                    )
                    time.sleep(wait)
        return {"headers": headers, "params": params}

    def on_post_response(
        self, status_code: int, headers: dict[str, str], body: Any
    ) -> Any:
        remaining = headers.get("x-ratelimit-remaining")
        if remaining is not None:
            self._remaining = int(remaining)
        reset = headers.get("x-ratelimit-reset")
        if reset is not None:
            self._reset_at = float(reset)
        return body
```

Register in `pyproject.toml`:

```toml
[project.entry-points."specli.plugins"]
rate-limiter = "my_plugin:RateLimitPlugin"
```
