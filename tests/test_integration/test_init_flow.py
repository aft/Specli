"""Integration tests for the init command flow.

Tests the full lifecycle: init from file, profile creation, profile
inspection via the config module, and idempotency. These tests exercise
the init command directly via its own Typer app and verify side effects
through the config API.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from specli.commands.init import init_command
from specli.config import (
    list_profiles,
    load_profile,
    profile_exists,
    save_profile,
)
from specli.models import AuthConfig, Profile
from specli.output import reset_output


def _build_init_app() -> typer.Typer:
    """Build a minimal Typer app with the init command registered as a sub-app.

    A no-op callback is registered so that Typer treats the app as a
    group (allowing ``["init", ...]`` in the args) rather than collapsing
    to single-command mode.
    """
    app = typer.Typer(
        name="specli",
        help="Generate CLI commands from OpenAPI 3.0/3.1 specs.",
        no_args_is_help=True,
        add_completion=False,
        invoke_without_command=True,
    )

    @app.callback()
    def _callback() -> None:
        """specli -- generate CLI commands from OpenAPI specs."""

    app.command("init")(init_command)
    return app


@pytest.fixture
def app() -> typer.Typer:
    """Typer app with init command registered."""
    return _build_init_app()


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ---------------------------------------------------------------------------
# Init flow
# ---------------------------------------------------------------------------


class TestInitFlow:
    """Test the full init flow: load spec, create profile, write project config."""

    def test_init_from_file(
        self,
        runner: CliRunner,
        app: typer.Typer,
        isolated_config: Path,
        petstore_30_raw: dict[str, Any],
    ) -> None:
        """init command creates a profile and project config from a local spec file."""
        spec_file = isolated_config / "spec.json"
        spec_file.write_text(json.dumps(petstore_30_raw))

        result = runner.invoke(app, ["init", "--spec", str(spec_file)])
        assert result.exit_code == 0, result.output

        # Check project config was created in the working directory.
        project_config_path = isolated_config / "specli.json"
        assert project_config_path.is_file()
        project_config = json.loads(project_config_path.read_text())
        assert "default_profile" in project_config

        # Check a profile was created.
        profiles = list_profiles()
        assert len(profiles) >= 1

    def test_init_with_custom_name(
        self,
        runner: CliRunner,
        app: typer.Typer,
        isolated_config: Path,
        petstore_30_raw: dict[str, Any],
    ) -> None:
        """init with --name creates a profile with the specified name."""
        spec_file = isolated_config / "spec.json"
        spec_file.write_text(json.dumps(petstore_30_raw))

        result = runner.invoke(
            app, ["init", "--spec", str(spec_file), "--name", "my-petstore"]
        )
        assert result.exit_code == 0, result.output
        assert profile_exists("my-petstore")

    def test_init_auto_detects_name_from_title(
        self,
        runner: CliRunner,
        app: typer.Typer,
        isolated_config: Path,
        petstore_30_raw: dict[str, Any],
    ) -> None:
        """init without --name derives the profile name from the spec title."""
        spec_file = isolated_config / "spec.json"
        spec_file.write_text(json.dumps(petstore_30_raw))

        result = runner.invoke(app, ["init", "--spec", str(spec_file)])
        assert result.exit_code == 0, result.output

        # The petstore spec title is "Petstore API" which slugifies to "petstore-api"
        assert profile_exists("petstore-api")

    def test_init_detects_security_schemes(
        self,
        runner: CliRunner,
        app: typer.Typer,
        isolated_config: Path,
        petstore_30_raw: dict[str, Any],
    ) -> None:
        """init should detect and report security schemes in stderr output."""
        spec_file = isolated_config / "spec.json"
        spec_file.write_text(json.dumps(petstore_30_raw))

        result = runner.invoke(app, ["init", "--spec", str(spec_file)])
        assert result.exit_code == 0, result.output
        # The init command writes security scheme info to stderr (captured
        # together with stdout by the CliRunner). The petstore spec has
        # apiKeyAuth and bearerAuth.
        combined = result.output.lower()
        assert "security" in combined or "auth" in combined

    def test_init_idempotent(
        self,
        runner: CliRunner,
        app: typer.Typer,
        isolated_config: Path,
        petstore_30_raw: dict[str, Any],
    ) -> None:
        """Running init twice with the same spec should succeed without error."""
        spec_file = isolated_config / "spec.json"
        spec_file.write_text(json.dumps(petstore_30_raw))

        result1 = runner.invoke(
            app, ["init", "--spec", str(spec_file), "--name", "myapi"]
        )
        assert result1.exit_code == 0, result1.output

        # Reset the global output to avoid stale stream references.
        reset_output()

        result2 = runner.invoke(
            app, ["init", "--spec", str(spec_file), "--name", "myapi"]
        )
        assert result2.exit_code == 0, result2.output

        # Profile should still exist and be loadable.
        profile = load_profile("myapi")
        assert profile.name == "myapi"

    def test_init_creates_project_config_with_profile_name(
        self,
        runner: CliRunner,
        app: typer.Typer,
        isolated_config: Path,
        petstore_30_raw: dict[str, Any],
    ) -> None:
        """Project config's default_profile should match the created profile name."""
        spec_file = isolated_config / "spec.json"
        spec_file.write_text(json.dumps(petstore_30_raw))

        result = runner.invoke(
            app, ["init", "--spec", str(spec_file), "--name", "test-api"]
        )
        assert result.exit_code == 0, result.output

        project_config = json.loads(
            (isolated_config / "specli.json").read_text()
        )
        assert project_config["default_profile"] == "test-api"

    def test_init_with_base_url_override(
        self,
        runner: CliRunner,
        app: typer.Typer,
        isolated_config: Path,
        petstore_30_raw: dict[str, Any],
    ) -> None:
        """init --base-url overrides the server URL from the spec."""
        spec_file = isolated_config / "spec.json"
        spec_file.write_text(json.dumps(petstore_30_raw))

        result = runner.invoke(
            app,
            [
                "init",
                "--spec", str(spec_file),
                "--name", "custom-base",
                "--base-url", "https://custom.example.com/api",
            ],
        )
        assert result.exit_code == 0, result.output

        profile = load_profile("custom-base")
        assert profile.base_url == "https://custom.example.com/api"

    def test_init_sets_spec_server_as_base_url(
        self,
        runner: CliRunner,
        app: typer.Typer,
        isolated_config: Path,
        petstore_30_raw: dict[str, Any],
    ) -> None:
        """Without --base-url, the first server URL from the spec is used."""
        spec_file = isolated_config / "spec.json"
        spec_file.write_text(json.dumps(petstore_30_raw))

        result = runner.invoke(
            app, ["init", "--spec", str(spec_file), "--name", "auto-base"]
        )
        assert result.exit_code == 0, result.output

        profile = load_profile("auto-base")
        assert profile.base_url == "https://api.petstore.example.com/v1"

    def test_init_invalid_spec_file(
        self,
        runner: CliRunner,
        app: typer.Typer,
        isolated_config: Path,
    ) -> None:
        """init with a nonexistent spec file exits with error."""
        result = runner.invoke(
            app, ["init", "--spec", "/nonexistent/spec.json"]
        )
        assert result.exit_code != 0

    def test_init_malformed_json(
        self,
        runner: CliRunner,
        app: typer.Typer,
        isolated_config: Path,
    ) -> None:
        """init with malformed JSON spec exits with error."""
        bad_spec = isolated_config / "bad.json"
        bad_spec.write_text("{not valid json!!!")

        result = runner.invoke(app, ["init", "--spec", str(bad_spec)])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Profile management after init
# ---------------------------------------------------------------------------


class TestProfileAfterInit:
    """Test that profiles created by init are properly persisted and loadable."""

    def test_profile_has_spec_path(
        self,
        runner: CliRunner,
        app: typer.Typer,
        isolated_config: Path,
        petstore_30_raw: dict[str, Any],
    ) -> None:
        """The saved profile references the original spec path."""
        spec_file = isolated_config / "spec.json"
        spec_file.write_text(json.dumps(petstore_30_raw))

        runner.invoke(
            app, ["init", "--spec", str(spec_file), "--name", "ref-test"]
        )

        profile = load_profile("ref-test")
        assert profile.spec == str(spec_file)

    def test_profile_has_path_rules(
        self,
        runner: CliRunner,
        app: typer.Typer,
        isolated_config: Path,
        petstore_30_raw: dict[str, Any],
    ) -> None:
        """The saved profile includes path_rules configuration."""
        spec_file = isolated_config / "spec.json"
        spec_file.write_text(json.dumps(petstore_30_raw))

        runner.invoke(
            app, ["init", "--spec", str(spec_file), "--name", "rules-test"]
        )

        profile = load_profile("rules-test")
        assert profile.path_rules is not None

    def test_profile_has_no_auth_by_default(
        self,
        runner: CliRunner,
        app: typer.Typer,
        isolated_config: Path,
        petstore_30_raw: dict[str, Any],
    ) -> None:
        """init does not set auth on the profile (left for auth commands)."""
        spec_file = isolated_config / "spec.json"
        spec_file.write_text(json.dumps(petstore_30_raw))

        runner.invoke(
            app, ["init", "--spec", str(spec_file), "--name", "no-auth"]
        )

        profile = load_profile("no-auth")
        assert profile.auth is None

    def test_multiple_inits_create_separate_profiles(
        self,
        runner: CliRunner,
        app: typer.Typer,
        isolated_config: Path,
        petstore_30_raw: dict[str, Any],
        petstore_31_raw: dict[str, Any],
    ) -> None:
        """Running init with different names creates distinct profiles."""
        spec30 = isolated_config / "spec30.json"
        spec30.write_text(json.dumps(petstore_30_raw))

        spec31 = isolated_config / "spec31.json"
        spec31.write_text(json.dumps(petstore_31_raw))

        runner.invoke(
            app, ["init", "--spec", str(spec30), "--name", "api-v30"]
        )
        reset_output()
        runner.invoke(
            app, ["init", "--spec", str(spec31), "--name", "api-v31"]
        )

        profiles = list_profiles()
        assert "api-v30" in profiles
        assert "api-v31" in profiles
        assert load_profile("api-v30").spec == str(spec30)
        assert load_profile("api-v31").spec == str(spec31)


# ---------------------------------------------------------------------------
# Config resolution after init
# ---------------------------------------------------------------------------


class TestConfigResolutionAfterInit:
    """Test that config resolution picks up the init-created profile."""

    def test_resolve_config_finds_single_profile(
        self,
        runner: CliRunner,
        app: typer.Typer,
        isolated_config: Path,
        petstore_30_raw: dict[str, Any],
    ) -> None:
        """With only one profile, resolve_config auto-selects it."""
        from specli.config import resolve_config

        spec_file = isolated_config / "spec.json"
        spec_file.write_text(json.dumps(petstore_30_raw))

        runner.invoke(
            app, ["init", "--spec", str(spec_file), "--name", "solo"]
        )

        _, profile = resolve_config()
        assert profile is not None
        assert profile.name == "solo"

    def test_resolve_config_uses_project_default(
        self,
        runner: CliRunner,
        app: typer.Typer,
        isolated_config: Path,
        petstore_30_raw: dict[str, Any],
    ) -> None:
        """resolve_config reads the project config written by init."""
        from specli.config import resolve_config

        spec_file = isolated_config / "spec.json"
        spec_file.write_text(json.dumps(petstore_30_raw))

        # Create two profiles so auto-select doesn't kick in.
        runner.invoke(
            app, ["init", "--spec", str(spec_file), "--name", "alpha"]
        )
        save_profile(Profile(name="beta", spec=str(spec_file)))

        # The project config written by the last init should point to "alpha".
        _, profile = resolve_config()
        assert profile is not None
        assert profile.name == "alpha"

    def test_resolve_config_cli_override(
        self,
        runner: CliRunner,
        app: typer.Typer,
        isolated_config: Path,
        petstore_30_raw: dict[str, Any],
    ) -> None:
        """CLI --profile flag overrides the project config default."""
        from specli.config import resolve_config

        spec_file = isolated_config / "spec.json"
        spec_file.write_text(json.dumps(petstore_30_raw))

        runner.invoke(
            app, ["init", "--spec", str(spec_file), "--name", "first"]
        )
        save_profile(Profile(name="second", spec=str(spec_file)))

        _, profile = resolve_config(cli_profile="second")
        assert profile is not None
        assert profile.name == "second"


# ---------------------------------------------------------------------------
# Auth config on profiles (direct API, not CLI)
# ---------------------------------------------------------------------------


class TestAuthOnProfile:
    """Test adding/removing auth on profiles created by init."""

    def test_add_api_key_auth(
        self,
        runner: CliRunner,
        app: typer.Typer,
        isolated_config: Path,
        petstore_30_raw: dict[str, Any],
    ) -> None:
        """Adding API key auth to an init-created profile persists correctly."""
        spec_file = isolated_config / "spec.json"
        spec_file.write_text(json.dumps(petstore_30_raw))

        runner.invoke(
            app, ["init", "--spec", str(spec_file), "--name", "auth-test"]
        )

        # Modify profile to add auth via config API.
        profile = load_profile("auth-test")
        profile.auth = AuthConfig(
            type="api_key",
            header="X-API-Key",
            source="env:TEST_KEY",
        )
        save_profile(profile)

        # Reload and verify.
        reloaded = load_profile("auth-test")
        assert reloaded.auth is not None
        assert reloaded.auth.type == "api_key"
        assert reloaded.auth.header == "X-API-Key"
        assert reloaded.auth.source == "env:TEST_KEY"

    def test_add_bearer_auth(
        self,
        runner: CliRunner,
        app: typer.Typer,
        isolated_config: Path,
        petstore_30_raw: dict[str, Any],
    ) -> None:
        """Adding bearer auth to a profile persists correctly."""
        spec_file = isolated_config / "spec.json"
        spec_file.write_text(json.dumps(petstore_30_raw))

        runner.invoke(
            app, ["init", "--spec", str(spec_file), "--name", "bearer-test"]
        )

        profile = load_profile("bearer-test")
        profile.auth = AuthConfig(type="bearer", source="env:TOKEN")
        save_profile(profile)

        reloaded = load_profile("bearer-test")
        assert reloaded.auth is not None
        assert reloaded.auth.type == "bearer"

    def test_remove_auth(
        self,
        runner: CliRunner,
        app: typer.Typer,
        isolated_config: Path,
        petstore_30_raw: dict[str, Any],
    ) -> None:
        """Removing auth from a profile persists correctly."""
        spec_file = isolated_config / "spec.json"
        spec_file.write_text(json.dumps(petstore_30_raw))

        runner.invoke(
            app, ["init", "--spec", str(spec_file), "--name", "rm-auth"]
        )

        # Add auth then remove it.
        profile = load_profile("rm-auth")
        profile.auth = AuthConfig(type="bearer", source="env:TOKEN")
        save_profile(profile)

        profile = load_profile("rm-auth")
        profile.auth = None
        save_profile(profile)

        reloaded = load_profile("rm-auth")
        assert reloaded.auth is None


# ---------------------------------------------------------------------------
# Spec parsing integration (init triggers full parse pipeline)
# ---------------------------------------------------------------------------


class TestSpecParsingViaInit:
    """Test that init properly invokes the parser pipeline."""

    def test_openapi_31_spec(
        self,
        runner: CliRunner,
        app: typer.Typer,
        isolated_config: Path,
        petstore_31_raw: dict[str, Any],
    ) -> None:
        """init handles OpenAPI 3.1 specs correctly."""
        spec_file = isolated_config / "spec31.json"
        spec_file.write_text(json.dumps(petstore_31_raw))

        result = runner.invoke(
            app, ["init", "--spec", str(spec_file), "--name", "v31"]
        )
        assert result.exit_code == 0, result.output
        assert profile_exists("v31")

    def test_complex_auth_spec(
        self,
        runner: CliRunner,
        app: typer.Typer,
        isolated_config: Path,
        complex_auth_raw: dict[str, Any],
    ) -> None:
        """init handles the complex auth fixture with many security schemes."""
        spec_file = isolated_config / "complex_auth.json"
        spec_file.write_text(json.dumps(complex_auth_raw))

        result = runner.invoke(
            app, ["init", "--spec", str(spec_file), "--name", "complex"]
        )
        assert result.exit_code == 0, result.output
        assert profile_exists("complex")
        # The complex auth spec has many security schemes; init should
        # report them in the output.
        assert "security" in result.output.lower() or "auth" in result.output.lower()

    def test_yaml_spec_via_file(
        self,
        runner: CliRunner,
        app: typer.Typer,
        isolated_config: Path,
        petstore_30_raw: dict[str, Any],
    ) -> None:
        """init handles a YAML-formatted spec file."""
        import yaml

        spec_file = isolated_config / "spec.yaml"
        spec_file.write_text(yaml.dump(petstore_30_raw, default_flow_style=False))

        result = runner.invoke(
            app, ["init", "--spec", str(spec_file), "--name", "yaml-api"]
        )
        assert result.exit_code == 0, result.output
        assert profile_exists("yaml-api")
