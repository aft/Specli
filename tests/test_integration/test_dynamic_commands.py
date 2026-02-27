"""Integration tests for dynamic command generation.

Tests the full pipeline from raw spec dicts through parsing, path rule
application, and command tree construction.  Verifies that generated
commands are invocable and that callbacks receive the expected arguments.
Also tests the skill generator end-to-end.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from specli.generator import build_command_tree
from specli.models import (
    APIInfo,
    APIOperation,
    APIParameter,
    HTTPMethod,
    ParameterLocation,
    ParsedSpec,
    PathRulesConfig,
    RequestBodyInfo,
    ServerInfo,
)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ---------------------------------------------------------------------------
# Dynamic command generation from petstore spec
# ---------------------------------------------------------------------------


class TestDynamicCommandGeneration:
    """Test that dynamic commands are properly generated and invocable."""

    def test_petstore_commands_exist(
        self, petstore_spec: ParsedSpec
    ) -> None:
        """Commands are generated from the petstore spec and help is accessible."""
        app = build_command_tree(petstore_spec, PathRulesConfig(), lambda *a: None)

        runner = CliRunner()
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0

    def test_petstore_pets_subcommand(
        self, petstore_spec: ParsedSpec
    ) -> None:
        """A 'pets' sub-command group is created from the petstore spec."""
        app = build_command_tree(petstore_spec, PathRulesConfig(), lambda *a: None)

        runner = CliRunner()
        result = runner.invoke(app, ["pets"])
        # Sub-app with no_args_is_help shows usage (exit 2).
        assert result.exit_code == 2
        combined = result.stdout.lower()
        assert "list" in combined or "create" in combined

    def test_get_command_invokes_callback(
        self, petstore_spec: ParsedSpec
    ) -> None:
        """Invoking a GET command calls the callback with the correct method."""
        captured: dict[str, Any] = {}

        def callback(method: str, path: str, params: dict, body: str | None, content_type: str | None = None) -> None:
            captured["method"] = method
            captured["path"] = path
            captured["params"] = params

        app = build_command_tree(petstore_spec, PathRulesConfig(), callback)
        runner = CliRunner()

        result = runner.invoke(app, ["pets", "list"])
        assert result.exit_code == 0
        assert "method" in captured
        assert captured["method"].upper() == "GET"

    def test_post_command_invokes_callback(
        self, petstore_spec: ParsedSpec
    ) -> None:
        """Invoking a POST command calls the callback with 'post' method."""
        captured: dict[str, Any] = {}

        def callback(method: str, path: str, params: dict, body: str | None, content_type: str | None = None) -> None:
            captured["method"] = method
            captured["path"] = path

        app = build_command_tree(petstore_spec, PathRulesConfig(), callback)
        runner = CliRunner()

        result = runner.invoke(app, ["pets", "create", "--name", "Fido"])
        assert result.exit_code == 0
        assert captured["method"].upper() == "POST"

    def test_get_single_passes_path_param(
        self, petstore_spec: ParsedSpec
    ) -> None:
        """GET /pets/{petId} passes the path parameter to the callback."""
        captured: dict[str, Any] = {}

        def callback(method: str, path: str, params: dict, body: str | None, content_type: str | None = None) -> None:
            captured["params"] = params

        app = build_command_tree(petstore_spec, PathRulesConfig(), callback)
        runner = CliRunner()

        result = runner.invoke(app, ["pets", "get", "pet-42"])
        assert result.exit_code == 0
        assert captured["params"]["petId"] == "pet-42"

    def test_delete_command_exists(
        self, petstore_spec: ParsedSpec
    ) -> None:
        """DELETE /pets/{petId} is reachable via 'pets delete'."""
        callback = MagicMock()
        app = build_command_tree(petstore_spec, PathRulesConfig(), callback)
        runner = CliRunner()

        result = runner.invoke(app, ["pets", "delete", "123"])
        assert result.exit_code == 0
        callback.assert_called_once()
        assert callback.call_args[0][0] == "delete"

    def test_path_rules_strip_prefix(
        self, petstore_spec: ParsedSpec
    ) -> None:
        """Path rules transform command structure."""
        rules = PathRulesConfig(
            auto_strip_prefix=True,
            skip_segments=["api"],
        )

        app = build_command_tree(petstore_spec, rules, lambda *a: None)
        runner = CliRunner()
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0

    def test_empty_spec_produces_empty_app(self) -> None:
        """Empty spec (no operations) produces a Typer app with no commands."""
        import typer as typer_mod

        empty_spec = ParsedSpec(
            info=APIInfo(title="Empty", version="1.0"),
            operations=[],
            security_schemes={},
            openapi_version="3.0.0",
        )

        app = build_command_tree(empty_spec, PathRulesConfig(), lambda *a: None)
        assert isinstance(app, typer_mod.Typer)
        # No registered commands or groups since the spec is empty.
        assert len(app.registered_commands) == 0
        assert len(app.registered_groups) == 0

    def test_callback_receives_body(
        self, petstore_spec: ParsedSpec
    ) -> None:
        """POST with --body passes the body string to the callback."""
        captured: dict[str, Any] = {}

        def callback(method: str, path: str, params: dict, body: str | None, content_type: str | None = None) -> None:
            captured["body"] = body

        app = build_command_tree(petstore_spec, PathRulesConfig(), callback)
        runner = CliRunner()

        body_json = '{"name": "Fido"}'
        result = runner.invoke(app, ["pets", "create", "--body", body_json])
        assert result.exit_code == 0
        assert captured["body"] == body_json

    def test_query_params_passed(
        self, petstore_spec: ParsedSpec
    ) -> None:
        """Query parameters are correctly forwarded to the callback."""
        captured: dict[str, Any] = {}

        def callback(method: str, path: str, params: dict, body: str | None, content_type: str | None = None) -> None:
            captured["params"] = params

        app = build_command_tree(petstore_spec, PathRulesConfig(), callback)
        runner = CliRunner()

        result = runner.invoke(
            app, ["pets", "list", "--limit", "10", "--status", "available"]
        )
        assert result.exit_code == 0
        assert captured["params"]["limit"] == 10
        assert captured["params"]["status"] == "available"


# ---------------------------------------------------------------------------
# Custom spec command generation
# ---------------------------------------------------------------------------


class TestCustomSpecCommands:
    """Test command generation from hand-crafted spec objects."""

    @staticmethod
    def _make_spec(operations: list[APIOperation]) -> ParsedSpec:
        return ParsedSpec(
            info=APIInfo(title="Test API", version="1.0.0"),
            operations=operations,
            security_schemes={},
            openapi_version="3.0.0",
        )

    def test_multiple_resources(self, runner: CliRunner) -> None:
        """Multiple resource paths produce distinct sub-command groups."""
        spec = self._make_spec([
            APIOperation(
                path="/users",
                method=HTTPMethod.GET,
                summary="List users",
            ),
            APIOperation(
                path="/orders",
                method=HTTPMethod.GET,
                summary="List orders",
            ),
        ])

        app = build_command_tree(spec, PathRulesConfig(), lambda *a: None)

        users_result = runner.invoke(app, ["users", "list"])
        orders_result = runner.invoke(app, ["orders", "list"])
        assert users_result.exit_code == 0
        assert orders_result.exit_code == 0

    def test_nested_resources(self, runner: CliRunner) -> None:
        """Nested paths like /users/{id}/posts produce nested sub-command groups."""
        spec = self._make_spec([
            APIOperation(
                path="/users/{userId}/posts",
                method=HTTPMethod.GET,
                summary="List user posts",
                parameters=[
                    APIParameter(
                        name="userId",
                        location=ParameterLocation.PATH,
                        required=True,
                        schema_type="string",
                    ),
                ],
            ),
        ])

        app = build_command_tree(spec, PathRulesConfig(), lambda *a: None)
        result = runner.invoke(app, ["users", "posts", "list", "user-1"])
        assert result.exit_code == 0

    def test_crud_operations_on_same_resource(self, runner: CliRunner) -> None:
        """All CRUD verbs on the same resource produce distinct commands."""
        callback = MagicMock()
        spec = self._make_spec([
            APIOperation(path="/items", method=HTTPMethod.GET, summary="List"),
            APIOperation(
                path="/items",
                method=HTTPMethod.POST,
                summary="Create",
                request_body=RequestBodyInfo(
                    required=True,
                    content_types=["application/json"],
                    schema={"type": "object"},
                ),
            ),
            APIOperation(
                path="/items/{id}",
                method=HTTPMethod.GET,
                summary="Get",
                parameters=[
                    APIParameter(
                        name="id",
                        location=ParameterLocation.PATH,
                        required=True,
                        schema_type="string",
                    ),
                ],
            ),
            APIOperation(
                path="/items/{id}",
                method=HTTPMethod.PUT,
                summary="Update",
                parameters=[
                    APIParameter(
                        name="id",
                        location=ParameterLocation.PATH,
                        required=True,
                        schema_type="string",
                    ),
                ],
                request_body=RequestBodyInfo(
                    required=True,
                    content_types=["application/json"],
                    schema={"type": "object"},
                ),
            ),
            APIOperation(
                path="/items/{id}",
                method=HTTPMethod.DELETE,
                summary="Delete",
                parameters=[
                    APIParameter(
                        name="id",
                        location=ParameterLocation.PATH,
                        required=True,
                        schema_type="string",
                    ),
                ],
            ),
        ])

        app = build_command_tree(spec, PathRulesConfig(), callback)

        runner.invoke(app, ["items", "list"])
        runner.invoke(app, ["items", "create", "--body", '{"x":1}'])
        runner.invoke(app, ["items", "get", "abc"])
        runner.invoke(app, ["items", "update", "abc", "--body", '{"x":2}'])
        runner.invoke(app, ["items", "delete", "abc"])

        assert callback.call_count == 5
        methods = [call.args[0] for call in callback.call_args_list]
        assert "get" in methods
        assert "post" in methods
        assert "put" in methods
        assert "delete" in methods

    def test_no_callback_prints_dry_run(self, runner: CliRunner) -> None:
        """Without a callback, commands print a dry-run summary."""
        spec = self._make_spec([
            APIOperation(path="/items", method=HTTPMethod.GET, summary="List"),
        ])

        app = build_command_tree(spec, PathRulesConfig(), request_callback=None)
        result = runner.invoke(app, ["items", "list"])
        assert result.exit_code == 0
        assert "GET" in result.stdout
        assert "/items" in result.stdout

    def test_body_from_file(self, runner: CliRunner, tmp_path: Path) -> None:
        """--body @file reads body content from a file."""
        callback = MagicMock()
        body_file = tmp_path / "payload.json"
        body_file.write_text('{"key": "value"}')

        spec = self._make_spec([
            APIOperation(
                path="/things",
                method=HTTPMethod.POST,
                summary="Create",
                request_body=RequestBodyInfo(
                    required=True,
                    content_types=["application/json"],
                    schema={"type": "object"},
                ),
            ),
        ])

        app = build_command_tree(spec, PathRulesConfig(), callback)
        result = runner.invoke(app, ["things", "create", "--body", f"@{body_file}"])
        assert result.exit_code == 0
        body = callback.call_args[0][3]
        assert json.loads(body) == {"key": "value"}


# ---------------------------------------------------------------------------
# Path rules integration with command generation
# ---------------------------------------------------------------------------


class TestPathRulesIntegration:
    """Test path rules applied during command tree building."""

    @staticmethod
    def _make_spec(operations: list[APIOperation]) -> ParsedSpec:
        return ParsedSpec(
            info=APIInfo(title="Versioned API", version="2.0"),
            operations=operations,
            security_schemes={},
            openapi_version="3.0.0",
        )

    def test_auto_strip_common_prefix(self, runner: CliRunner) -> None:
        """auto_strip_prefix removes the common prefix from all paths."""
        spec = self._make_spec([
            APIOperation(
                path="/api/v1/items", method=HTTPMethod.GET, summary="List"
            ),
            APIOperation(
                path="/api/v1/users", method=HTTPMethod.GET, summary="List"
            ),
        ])

        app = build_command_tree(spec, PathRulesConfig(), lambda *a: None)

        items_result = runner.invoke(app, ["items", "list"])
        users_result = runner.invoke(app, ["users", "list"])
        assert items_result.exit_code == 0
        assert users_result.exit_code == 0

    def test_explicit_strip_prefix(self, runner: CliRunner) -> None:
        """strip_prefix explicitly removes a prefix segment."""
        spec = self._make_spec([
            APIOperation(
                path="/api/v2/things", method=HTTPMethod.GET, summary="List"
            ),
        ])

        rules = PathRulesConfig(strip_prefix="/api")
        app = build_command_tree(spec, rules, lambda *a: None)

        result = runner.invoke(app, ["v2", "things", "list"])
        assert result.exit_code == 0

    def test_skip_segments(self, runner: CliRunner) -> None:
        """skip_segments removes named segments wherever they appear."""
        spec = self._make_spec([
            APIOperation(
                path="/api/internal/items",
                method=HTTPMethod.GET,
                summary="List",
            ),
        ])

        rules = PathRulesConfig(
            auto_strip_prefix=False,
            skip_segments=["api", "internal"],
        )
        app = build_command_tree(spec, rules, lambda *a: None)

        result = runner.invoke(app, ["items", "list"])
        assert result.exit_code == 0

    def test_collapse_mapping(self, runner: CliRunner) -> None:
        """collapse maps a specific path to a flat command name."""
        spec = self._make_spec([
            APIOperation(
                path="/api/v1/system/health",
                method=HTTPMethod.GET,
                summary="Health check",
            ),
        ])

        rules = PathRulesConfig(
            collapse={"/api/v1/system/health": "/health"},
        )
        app = build_command_tree(spec, rules, lambda *a: None)

        result = runner.invoke(app, ["health", "list"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Full spec round-trip: raw dict -> parse -> commands
# ---------------------------------------------------------------------------


class TestFullRoundTrip:
    """Test the complete pipeline from raw spec dict to invocable commands."""

    def test_petstore_30_round_trip(
        self,
        petstore_30_raw: dict[str, Any],
        runner: CliRunner,
    ) -> None:
        """Raw petstore 3.0 dict -> parse -> command tree -> invoke all commands."""
        from specli.parser import load_spec, validate_openapi_version
        from specli.parser.extractor import extract_spec

        version = validate_openapi_version(petstore_30_raw)
        spec = extract_spec(petstore_30_raw, version)

        callback = MagicMock()
        app = build_command_tree(spec, PathRulesConfig(), callback)

        runner.invoke(app, ["pets", "list"])
        runner.invoke(app, ["pets", "create", "--name", "Fido"])
        runner.invoke(app, ["pets", "get", "123"])
        runner.invoke(app, ["pets", "delete", "456"])

        assert callback.call_count == 4

    def test_petstore_31_round_trip(
        self,
        petstore_31_raw: dict[str, Any],
        runner: CliRunner,
    ) -> None:
        """Raw petstore 3.1 dict -> parse -> command tree -> invoke."""
        from specli.parser import validate_openapi_version
        from specli.parser.extractor import extract_spec

        version = validate_openapi_version(petstore_31_raw)
        spec = extract_spec(petstore_31_raw, version)

        callback = MagicMock()
        app = build_command_tree(spec, PathRulesConfig(), callback)

        # At minimum, pets list should work.
        result = runner.invoke(app, ["pets", "list"])
        assert result.exit_code == 0
        assert callback.call_count >= 1


# ---------------------------------------------------------------------------
# Skill generation integration
# ---------------------------------------------------------------------------


class TestGenerateSkillIntegration:
    """Test the skill generator end-to-end with parsed specs."""

    def test_generate_skill_creates_all_files(
        self, petstore_spec: ParsedSpec, tmp_path: Path
    ) -> None:
        """generate_skill creates SKILL.md and reference files."""
        from specli.plugins.skill import generate_skill

        skill_output = tmp_path / "skill-output"
        result = generate_skill(petstore_spec, skill_output)

        assert result == skill_output
        assert (skill_output / "SKILL.md").exists()
        assert (skill_output / "references" / "api-reference.md").exists()
        assert (skill_output / "references" / "auth-setup.md").exists()

    def test_skill_md_has_api_title(
        self, petstore_spec: ParsedSpec, tmp_path: Path
    ) -> None:
        """SKILL.md contains the API title from the spec."""
        from specli.plugins.skill import generate_skill

        skill_output = tmp_path / "skill-output"
        generate_skill(petstore_spec, skill_output)

        content = (skill_output / "SKILL.md").read_text()
        assert "Petstore API" in content

    def test_api_reference_lists_endpoints(
        self, petstore_spec: ParsedSpec, tmp_path: Path
    ) -> None:
        """api-reference.md lists all endpoints from the spec."""
        from specli.plugins.skill import generate_skill

        skill_output = tmp_path / "skill-output"
        generate_skill(petstore_spec, skill_output)

        content = (skill_output / "references" / "api-reference.md").read_text()
        assert "GET /pets" in content
        assert "POST /pets" in content
        assert "GET /pets/{petId}" in content
        assert "DELETE /pets/{petId}" in content

    def test_auth_setup_lists_schemes(
        self, petstore_spec: ParsedSpec, tmp_path: Path
    ) -> None:
        """auth-setup.md lists the security schemes from the spec."""
        from specli.plugins.skill import generate_skill

        skill_output = tmp_path / "skill-output"
        generate_skill(petstore_spec, skill_output)

        content = (skill_output / "references" / "auth-setup.md").read_text()
        assert "apiKeyAuth" in content
        assert "bearerAuth" in content

    def test_skill_with_profile(
        self, petstore_spec: ParsedSpec, tmp_path: Path
    ) -> None:
        """generate_skill with a profile uses the profile name and spec URL."""
        from specli.models import Profile
        from specli.plugins.skill import generate_skill

        profile = Profile(
            name="my-petstore",
            spec="https://example.com/petstore.json",
        )

        skill_output = tmp_path / "skill-output"
        generate_skill(petstore_spec, skill_output, profile=profile, cli_name="my-petstore")

        content = (skill_output / "SKILL.md").read_text()
        assert "my-petstore" in content

    def test_skill_from_complex_auth_spec(
        self, complex_auth_raw: dict[str, Any], tmp_path: Path
    ) -> None:
        """Skill generation works with a complex auth spec."""
        from specli.parser import validate_openapi_version
        from specli.parser.extractor import extract_spec
        from specli.plugins.skill import generate_skill

        version = validate_openapi_version(complex_auth_raw)
        spec = extract_spec(complex_auth_raw, version)

        skill_output = tmp_path / "skill-output"
        generate_skill(spec, skill_output)

        auth_setup = (skill_output / "references" / "auth-setup.md").read_text()
        # Complex auth has many scheme types.
        assert "apiKeyHeader" in auth_setup
        assert "basicAuth" in auth_setup
        assert "bearerAuth" in auth_setup

    def test_skill_generation_idempotent(
        self, petstore_spec: ParsedSpec, tmp_path: Path
    ) -> None:
        """Running generate_skill twice on the same directory succeeds."""
        from specli.plugins.skill import generate_skill

        skill_output = tmp_path / "skill-output"
        generate_skill(petstore_spec, skill_output)
        generate_skill(petstore_spec, skill_output)

        assert (skill_output / "SKILL.md").exists()

    def test_skill_after_init(
        self,
        isolated_config: Path,
        petstore_30_raw: dict[str, Any],
    ) -> None:
        """Full flow: init creates profile, then skill generation uses it."""
        import typer as typer_mod

        from specli.commands.init import init_command
        from specli.config import load_profile
        from specli.parser import validate_openapi_version
        from specli.parser.extractor import extract_spec
        from specli.plugins.skill import generate_skill

        # Set up init app with a callback so Typer treats it as a group.
        init_app = typer_mod.Typer(invoke_without_command=True)

        @init_app.callback()
        def _cb() -> None:
            pass

        init_app.command("init")(init_command)
        cli_runner = CliRunner()

        spec_file = isolated_config / "spec.json"
        spec_file.write_text(json.dumps(petstore_30_raw))

        result = cli_runner.invoke(
            init_app, ["init", "--spec", str(spec_file), "--name", "skill-test"]
        )
        assert result.exit_code == 0, result.output

        # Load the profile and parse the spec.
        profile = load_profile("skill-test")
        from specli.parser import load_spec

        raw = load_spec(profile.spec)
        version = validate_openapi_version(raw)
        spec = extract_spec(raw, version)

        # Generate skill.
        skill_output = isolated_config / "skill-output"
        generate_skill(spec, skill_output, profile=profile)

        assert (skill_output / "SKILL.md").exists()
        content = (skill_output / "SKILL.md").read_text()
        assert "skill-test" in content
        assert "Petstore API" in content
