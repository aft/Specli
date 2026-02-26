"""Shared test fixtures for specli.

Provides reusable fixtures for loading spec fixtures, creating isolated
config environments, managing output state, and running CLI commands.
These fixtures are automatically discovered by pytest and available to
all test modules without explicit imports.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from specli.models import (
    AuthConfig,
    GlobalConfig,
    ParsedSpec,
    PathRulesConfig,
    Profile,
    RequestConfig,
)
from specli.output import OutputFormat, OutputManager, reset_output, set_output


FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Auto-reset global output state between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_output_between_tests() -> None:
    """Reset the global OutputManager after every test.

    The OutputManager caches references to sys.stdout/sys.stderr at
    creation time.  When Typer's CliRunner redirects those streams during
    a test and the test finishes, the cached references become stale
    ("I/O operation on closed file").  Resetting forces a fresh manager
    to be created on next use.
    """
    yield
    reset_output()


# ---------------------------------------------------------------------------
# Raw spec fixtures (plain dicts loaded from JSON files)
# ---------------------------------------------------------------------------


@pytest.fixture
def petstore_30_raw() -> dict[str, Any]:
    """Load raw petstore 3.0 spec dict."""
    with open(FIXTURES_DIR / "petstore_3.0.json") as f:
        return json.load(f)


@pytest.fixture
def petstore_31_raw() -> dict[str, Any]:
    """Load raw petstore 3.1 spec dict."""
    with open(FIXTURES_DIR / "petstore_3.1.json") as f:
        return json.load(f)


@pytest.fixture
def complex_auth_raw() -> dict[str, Any]:
    """Load raw complex auth spec dict."""
    with open(FIXTURES_DIR / "complex_auth.json") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Parsed spec fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def petstore_spec(petstore_30_raw: dict[str, Any]) -> ParsedSpec:
    """Parsed petstore 3.0 spec."""
    from specli.parser.extractor import extract_spec

    return extract_spec(petstore_30_raw, "3.0.3")


# ---------------------------------------------------------------------------
# Profile fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_profile(tmp_path: Path) -> Profile:
    """A sample profile pointing to a local spec file.

    Copies the petstore 3.0 fixture into tmp_path and creates a Profile
    referencing it, pre-configured with API key auth and relaxed request
    settings suitable for testing.
    """
    spec_path = tmp_path / "petstore.json"
    with open(FIXTURES_DIR / "petstore_3.0.json") as f:
        spec_path.write_text(f.read())

    return Profile(
        name="test-api",
        spec=str(spec_path),
        base_url="http://localhost:8080",
        auth=AuthConfig(
            type="api_key",
            header="X-API-Key",
            source="env:TEST_API_KEY",
        ),
        path_rules=PathRulesConfig(),
        request=RequestConfig(timeout=5, verify_ssl=False, max_retries=1),
    )


# ---------------------------------------------------------------------------
# Config isolation fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate configuration to a temporary directory.

    Sets XDG_CONFIG_HOME, XDG_CACHE_HOME, and XDG_DATA_HOME to
    subdirectories of tmp_path so that tests never touch real user
    config. Clears all SPECLI_* environment variables and changes
    the working directory to tmp_path.

    Returns:
        The tmp_path root directory for additional file creation.
    """
    config_dir = tmp_path / "config"
    cache_dir = tmp_path / "cache"
    data_dir = tmp_path / "data"

    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_dir))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_dir))
    monkeypatch.setenv("XDG_DATA_HOME", str(data_dir))

    # Clear any OPENAPI2CLI env vars that might leak into tests.
    for var in [
        "SPECLI_PROFILE",
        "SPECLI_BASE_URL",
        "SPECLI_SPEC",
        "SPECLI_CONFIG",
    ]:
        monkeypatch.delenv(var, raising=False)

    monkeypatch.chdir(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# Output fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def quiet_output() -> OutputManager:
    """Set up a quiet output manager for tests that don't care about output.

    Installs a PLAIN-format, quiet OutputManager as the global output
    and resets it after the test completes.
    """
    output = OutputManager(format=OutputFormat.PLAIN, quiet=True)
    set_output(output)
    yield output
    reset_output()


@pytest.fixture
def json_output() -> OutputManager:
    """Set up JSON output for tests that check JSON-formatted output.

    Installs a JSON-format OutputManager as the global output
    and resets it after the test completes.
    """
    output = OutputManager(format=OutputFormat.JSON)
    set_output(output)
    yield output
    reset_output()


# ---------------------------------------------------------------------------
# CLI runner fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def cli_runner():
    """Typer CLI test runner.

    Returns a CliRunner instance that captures stdout/stderr and
    provides a consistent interface for invoking Typer apps in tests.
    """
    from typer.testing import CliRunner

    return CliRunner()
