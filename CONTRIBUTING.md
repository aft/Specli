# Contributing to specli

Thank you for your interest in contributing. This guide covers the development workflow, project structure, and conventions used in the codebase.

## Development Setup

```bash
# Clone the repository
git clone https://github.com/aft/specli.git
cd specli

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate   # Windows

# Install in editable mode with dev dependencies
pip install -e ".[dev]"
```

### Optional extras

```bash
# Keyring support (for system credential storage)
pip install -e ".[keyring]"

# JWT support
pip install -e ".[jwt]"
```

## Running Tests

The test suite uses pytest and contains 725+ tests across unit, integration, and edge-case scenarios.

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run with coverage report
pytest --cov=specli --cov-report=term-missing

# Run a specific test file
pytest tests/test_config.py -v

# Run a specific test function
pytest tests/test_generator/test_path_rules.py::test_auto_strip_prefix -v

# Run only integration tests
pytest tests/test_integration/ -v
```

## Project Structure

```
specli/
  src/specli/        # Source code (src layout)
    __init__.py            # Package init, __version__
    __main__.py            # python -m specli support
    app.py                 # Typer app factory and entry point
    config.py              # XDG config, profiles, atomic writes
    models.py              # All Pydantic models (single source of truth)
    output.py              # Output system (Rich/JSON/plain, stdout/stderr)
    exit_codes.py          # Exit code constants (clig.dev conventions)
    exceptions.py          # Exception hierarchy
    parser/                # OpenAPI spec loading and extraction
      loader.py            # URL, file, stdin loading (JSON + YAML)
      resolver.py          # $ref resolution with cycle detection
      extractor.py         # Extract operations, params, security schemes
    generator/             # CLI command generation
      command_tree.py      # Build Typer command tree from parsed spec
      param_mapper.py      # Map OpenAPI params to Typer arguments/options
      path_rules.py        # Path transformation rules engine
    client/                # HTTP clients
      sync_client.py       # Synchronous httpx client
      async_client.py      # Async httpx client
      response.py          # Response formatting bridge
    auth/                  # Authentication system
      base.py              # AuthPlugin ABC and AuthResult
      manager.py           # Auth plugin registry
      plugins/             # Built-in auth plugins
        api_key.py         # API key (header, query, cookie)
        bearer.py          # Bearer token
        basic.py           # HTTP Basic
    plugins/               # Plugin system
      base.py              # Plugin ABC with hook methods
      hooks.py             # HookRunner and HookContext
      manager.py           # Entry-point discovery, enable/disable
    skill/                 # Claude Code skill generation
      generator.py         # Skill file generator
      templates/           # Jinja2 templates (skill.md.j2, etc.)
    commands/              # Built-in CLI commands
      init.py              # specli init
      auth.py              # specli auth (login, add, list, test, remove)
      config.py            # specli config (show, set, reset)
      inspect.py           # specli inspect (paths, schemas, auth, info)
      generate_skill.py    # specli generate-skill
  tests/                   # Test suite
    conftest.py            # Shared fixtures
    test_config.py         # Config unit tests
    test_output.py         # Output system tests
    test_parser/           # Parser tests (loader, resolver, extractor)
    test_generator/        # Generator tests (command_tree, param_mapper, path_rules)
    test_client/           # Client tests (sync_client, response)
    test_auth/             # Auth plugin tests
    test_plugins/          # Plugin system tests
    test_skill/            # Skill generator tests
    test_integration/      # Integration tests (init flow, dynamic commands)
```

## Key Design Decisions

### Single models file

All Pydantic models live in `src/specli/models.py`. Every other module imports from there. This prevents circular imports and makes the data contract easy to find.

### Output discipline

The output system follows [clig.dev](https://clig.dev) conventions:

- **stdout** is for primary data only (API responses, JSON, tables)
- **stderr** is for all diagnostics (progress, status, warnings, errors)
- The `OutputManager` class handles format selection, color detection, and pager support
- Module-level convenience functions (`info()`, `error()`, `debug()`, etc.) delegate to the global `OutputManager` instance

### Exit codes

Exit codes are defined in `exit_codes.py` and follow a structured scheme. Each exception class in `exceptions.py` carries its own exit code.

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Generic failure |
| 2 | Invalid usage / bad arguments |
| 3 | Auth failure |
| 4 | Not found |
| 5 | Server error |
| 6 | Connection error |
| 7 | Spec parse error |
| 10 | Plugin error |

### Atomic config writes

Config and profile files are written atomically using `tempfile + os.replace`. This prevents corruption if the process is interrupted mid-write.

## How to Create a Plugin

Plugins are discovered via Python entry points. Here is the complete process:

### 1. Create a package with a Plugin subclass

```python
# mypackage/plugin.py
from specli.plugins.base import Plugin
from specli.models import GlobalConfig
from typing import Any


class MyPlugin(Plugin):
    @property
    def name(self) -> str:
        return "my-plugin"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def description(self) -> str:
        return "Adds custom logging to every request"

    def on_init(self, config: GlobalConfig) -> None:
        # Called once when the plugin is loaded
        print(f"MyPlugin initialized with config: {config.default_profile}")

    def on_pre_request(
        self, method: str, url: str, headers: dict[str, str], params: dict[str, Any]
    ) -> dict[str, Any]:
        print(f"Request: {method} {url}")
        # Must return headers and params (possibly modified)
        return {"headers": headers, "params": params}

    def on_post_response(
        self, status_code: int, headers: dict[str, str], body: Any
    ) -> Any:
        print(f"Response: {status_code}")
        return body  # Return (possibly modified) body

    def on_error(self, error: Exception) -> None:
        print(f"Error: {error}")

    def cleanup(self) -> None:
        # Called on shutdown
        pass
```

### 2. Register the entry point

In your package's `pyproject.toml`:

```toml
[project.entry-points."specli.plugins"]
my-plugin = "mypackage.plugin:MyPlugin"
```

### 3. Install and use

```bash
pip install mypackage
specli api pets list  # Plugin hooks fire automatically
```

## How to Create an Auth Plugin

Auth plugins handle credential resolution for a specific authentication type.

```python
# mypackage/oauth2_plugin.py
from specli.auth.base import AuthPlugin, AuthResult
from specli.config import resolve_credential
from specli.models import AuthConfig


class OAuth2Plugin(AuthPlugin):
    @property
    def auth_type(self) -> str:
        return "oauth2_client_credentials"

    def authenticate(self, auth_config: AuthConfig) -> AuthResult:
        client_id = resolve_credential(auth_config.client_id_source or "prompt")
        client_secret = resolve_credential(auth_config.client_secret_source or "prompt")
        # Exchange credentials for a token (implementation omitted)
        token = self._get_token(client_id, client_secret, auth_config.token_url)
        return AuthResult(headers={"Authorization": f"Bearer {token}"})

    def validate_config(self, auth_config: AuthConfig) -> list[str]:
        errors = []
        if not auth_config.token_url:
            errors.append("OAuth2 requires 'token_url'")
        return errors

    def _get_token(self, client_id: str, client_secret: str, token_url: str | None) -> str:
        # Token exchange logic here
        ...
```

Register the auth plugin in your application code or via a regular plugin's `on_init` hook:

```python
from specli.auth.manager import AuthManager

manager = AuthManager()
manager.register(OAuth2Plugin())
```

## Code Style

### Tools

- **Formatter**: [ruff](https://github.com/astral-sh/ruff) (line length 100)
- **Linter**: ruff
- **Type checker**: mypy (strict mode, Python 3.10 target)

### Conventions

- All public functions and methods must have type annotations
- All modules use `from __future__ import annotations` for PEP 604 union syntax
- Docstrings use Google style (Args/Returns/Raises sections)
- Private functions are prefixed with `_`
- Constants are `UPPER_SNAKE_CASE`
- No version numbers in file names

### Running quality checks

```bash
# Lint
ruff check src/ tests/

# Format check
ruff format --check src/ tests/

# Type check
mypy src/specli/
```

## Pull Request Process

1. **Create a branch** from `main` with a descriptive name (e.g., `feat/oauth2-auth`, `fix/path-rules-edge-case`)
2. **Write tests** for any new functionality or bug fixes
3. **Run the full test suite** and ensure all 725+ tests pass
4. **Run linting and type checks** (`ruff check`, `mypy`)
5. **Update documentation** if your change affects user-facing behavior
6. **Write a clear PR description** explaining what changed and why
7. **Keep commits focused** -- one logical change per commit

### Commit message style

```
feat: add OAuth2 client credentials auth plugin
fix: handle empty path segments in path rules
docs: add plugin development guide
test: add edge cases for $ref circular resolution
```

## Reporting Issues

When filing a bug report, please include:

- Python version (`python --version`)
- specli version (`specli --version`)
- The OpenAPI spec (or a minimal reproduction) that triggers the issue
- Full command invocation and output
- The crash log path if one was generated (printed in the error message)
