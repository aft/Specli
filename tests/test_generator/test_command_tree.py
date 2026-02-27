"""Tests for specli.generator.command_tree.

Covers:
- build_command_tree produces a Typer app
- Command tree has expected sub-commands from petstore spec
- Verb determination: collection GET -> list, single GET -> get, POST -> create
- Deprecated operations include deprecation notice
- Command help text comes from operation summary
- request_callback is called with correct args when command invoked
- Empty spec (no operations) produces an empty app
- Grouping of operations by path
- _is_collection_endpoint edge cases
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest
import typer
from typer.testing import CliRunner

from specli.generator.command_tree import (
    METHOD_TO_VERB,
    _build_help_text,
    _determine_verb,
    _group_operations,
    _is_collection_endpoint,
    build_command_tree,
)
from specli.models import (
    APIInfo,
    APIOperation,
    APIParameter,
    HTTPMethod,
    ParameterLocation,
    ParsedSpec,
    PathRulesConfig,
    RequestBodyInfo,
)
from specli.parser.extractor import extract_spec

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _load_fixture(name: str) -> dict[str, Any]:
    """Load a JSON fixture file."""
    path = FIXTURES_DIR / name
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture()
def petstore_spec() -> ParsedSpec:
    """Parsed petstore 3.0 spec."""
    raw = _load_fixture("petstore_3.0.json")
    return extract_spec(raw, "3.0.3")


@pytest.fixture()
def default_rules() -> PathRulesConfig:
    return PathRulesConfig()


def _minimal_spec(operations: list[APIOperation] | None = None) -> ParsedSpec:
    """Build a minimal ParsedSpec for testing."""
    return ParsedSpec(
        info=APIInfo(title="Test API", version="1.0.0"),
        servers=[],
        operations=operations or [],
        security_schemes={},
        openapi_version="3.0.3",
    )


def _make_operation(
    path: str,
    method: HTTPMethod,
    *,
    summary: str = "",
    description: str = "",
    operation_id: str | None = None,
    deprecated: bool = False,
    parameters: list[APIParameter] | None = None,
    request_body: RequestBodyInfo | None = None,
) -> APIOperation:
    """Shortcut to create an APIOperation for testing."""
    return APIOperation(
        path=path,
        method=method,
        summary=summary,
        description=description,
        operation_id=operation_id,
        deprecated=deprecated,
        parameters=parameters or [],
        request_body=request_body,
        tags=[],
        responses=[],
        security=[],
    )


# ------------------------------------------------------------------ #
# build_command_tree -- basic structure
# ------------------------------------------------------------------ #


class TestBuildCommandTree:
    """Test that build_command_tree produces a usable Typer app."""

    def test_returns_typer_app(
        self, petstore_spec: ParsedSpec, default_rules: PathRulesConfig,
    ) -> None:
        app = build_command_tree(petstore_spec, default_rules)
        assert isinstance(app, typer.Typer)

    def test_empty_spec(self, default_rules: PathRulesConfig) -> None:
        spec = _minimal_spec(operations=[])
        app = build_command_tree(spec, default_rules)
        assert isinstance(app, typer.Typer)

    def test_app_help_shows_no_args(
        self, petstore_spec: ParsedSpec, default_rules: PathRulesConfig,
    ) -> None:
        """Invoking the app with no arguments should show usage help."""
        app = build_command_tree(petstore_spec, default_rules)
        result = runner.invoke(app, [])
        # Typer group with no_args_is_help returns exit code 2 (Click convention)
        # but displays usage information.
        assert result.exit_code == 2
        assert "Usage" in result.stdout

    def test_petstore_has_pets_subcommand(
        self, petstore_spec: ParsedSpec, default_rules: PathRulesConfig,
    ) -> None:
        """The petstore spec should produce a 'pets' sub-command group."""
        app = build_command_tree(petstore_spec, default_rules)
        result = runner.invoke(app, ["pets"])
        # 'pets' is a sub-app, so invoking with no further args shows usage (exit 2).
        assert result.exit_code == 2
        # The help output should mention at least some verbs.
        combined = result.stdout.lower()
        assert "list" in combined or "get" in combined or "create" in combined

    def test_petstore_list_command_exists(
        self, petstore_spec: ParsedSpec, default_rules: PathRulesConfig,
    ) -> None:
        """GET /pets should produce a 'list' command under 'pets'."""
        app = build_command_tree(petstore_spec, default_rules)
        result = runner.invoke(app, ["pets", "list", "--help"])
        assert result.exit_code == 0
        assert "List all pets" in result.stdout or "list" in result.stdout.lower()

    def test_petstore_create_command_exists(
        self, petstore_spec: ParsedSpec, default_rules: PathRulesConfig,
    ) -> None:
        """POST /pets should produce a 'create' command under 'pets'."""
        app = build_command_tree(petstore_spec, default_rules)
        result = runner.invoke(app, ["pets", "create", "--help"])
        assert result.exit_code == 0

    def test_petstore_get_command_exists(
        self, petstore_spec: ParsedSpec, default_rules: PathRulesConfig,
    ) -> None:
        """GET /pets/{petId} should produce a 'get' command under 'pets'."""
        app = build_command_tree(petstore_spec, default_rules)
        result = runner.invoke(app, ["pets", "get", "--help"])
        assert result.exit_code == 0

    def test_petstore_delete_command_exists(
        self, petstore_spec: ParsedSpec, default_rules: PathRulesConfig,
    ) -> None:
        """DELETE /pets/{petId} should produce a 'delete' command under 'pets'."""
        app = build_command_tree(petstore_spec, default_rules)
        result = runner.invoke(app, ["pets", "delete", "--help"])
        assert result.exit_code == 0


# ------------------------------------------------------------------ #
# Verb determination
# ------------------------------------------------------------------ #


class TestDetermineVerb:
    """Test the verb selection logic."""

    def test_get_collection_returns_list(self) -> None:
        op = _make_operation("/pets", HTTPMethod.GET)
        assert _determine_verb(op) == "list"

    def test_get_single_resource_returns_get(self) -> None:
        op = _make_operation("/pets/{petId}", HTTPMethod.GET)
        assert _determine_verb(op) == "get"

    def test_post_returns_create(self) -> None:
        op = _make_operation("/pets", HTTPMethod.POST)
        assert _determine_verb(op) == "create"

    def test_put_returns_update(self) -> None:
        op = _make_operation("/pets/{petId}", HTTPMethod.PUT)
        assert _determine_verb(op) == "update"

    def test_patch_returns_patch(self) -> None:
        op = _make_operation("/pets/{petId}", HTTPMethod.PATCH)
        assert _determine_verb(op) == "patch"

    def test_delete_returns_delete(self) -> None:
        op = _make_operation("/pets/{petId}", HTTPMethod.DELETE)
        assert _determine_verb(op) == "delete"

    def test_head_returns_head(self) -> None:
        op = _make_operation("/pets", HTTPMethod.HEAD)
        assert _determine_verb(op) == "head"

    def test_options_returns_options(self) -> None:
        op = _make_operation("/pets", HTTPMethod.OPTIONS)
        assert _determine_verb(op) == "options"

    def test_get_root_is_collection(self) -> None:
        """GET / should be treated as a collection endpoint."""
        op = _make_operation("/", HTTPMethod.GET)
        assert _determine_verb(op) == "list"

    def test_get_nested_resource(self) -> None:
        """GET /users/{id}/settings is a collection (ends with static segment)."""
        op = _make_operation("/users/{id}/settings", HTTPMethod.GET)
        assert _determine_verb(op) == "list"

    def test_get_nested_single_resource(self) -> None:
        """GET /users/{user_id}/posts/{post_id} ends with {param}."""
        op = _make_operation("/users/{user_id}/posts/{post_id}", HTTPMethod.GET)
        assert _determine_verb(op) == "get"


# ------------------------------------------------------------------ #
# _is_collection_endpoint
# ------------------------------------------------------------------ #


class TestIsCollectionEndpoint:
    """Test the collection vs single-resource detection."""

    def test_collection_path(self) -> None:
        assert _is_collection_endpoint("/pets") is True

    def test_single_resource_path(self) -> None:
        assert _is_collection_endpoint("/pets/{petId}") is False

    def test_root_path(self) -> None:
        assert _is_collection_endpoint("/") is True

    def test_nested_collection(self) -> None:
        assert _is_collection_endpoint("/users/{id}/posts") is True

    def test_nested_single(self) -> None:
        assert _is_collection_endpoint("/users/{id}/posts/{postId}") is False

    def test_empty_path(self) -> None:
        assert _is_collection_endpoint("") is True


# ------------------------------------------------------------------ #
# Deprecated operations
# ------------------------------------------------------------------ #


class TestDeprecatedOperations:
    """Test that deprecated operations show a deprecation notice."""

    def test_deprecated_in_help(self, default_rules: PathRulesConfig) -> None:
        op = _make_operation(
            "/pets/{petId}",
            HTTPMethod.DELETE,
            summary="Delete a pet",
            deprecated=True,
            parameters=[
                APIParameter(
                    name="petId",
                    location=ParameterLocation.PATH,
                    required=True,
                    schema_type="string",
                ),
            ],
        )
        spec = _minimal_spec([op])
        app = build_command_tree(spec, default_rules)
        result = runner.invoke(app, ["pets", "delete", "--help"])
        assert result.exit_code == 0
        assert "DEPRECATED" in result.stdout

    def test_deprecated_in_help_text_builder(self) -> None:
        op = _make_operation(
            "/old", HTTPMethod.GET, summary="Old endpoint", deprecated=True,
        )
        text = _build_help_text(op)
        assert "[DEPRECATED]" in text
        assert "Old endpoint" in text


# ------------------------------------------------------------------ #
# Help text from summary
# ------------------------------------------------------------------ #


class TestHelpText:
    """Test that command help text comes from the operation summary."""

    def test_summary_used_as_help(self) -> None:
        op = _make_operation(
            "/pets", HTTPMethod.GET, summary="List all pets",
        )
        text = _build_help_text(op)
        assert text == "List all pets"

    def test_description_fallback(self) -> None:
        op = _make_operation(
            "/pets", HTTPMethod.GET,
            description="Returns a complete list of all pets in the store.",
        )
        text = _build_help_text(op)
        assert "Returns a complete list" in text

    def test_no_summary_no_description(self) -> None:
        op = _make_operation("/pets", HTTPMethod.GET)
        text = _build_help_text(op)
        # Fallback is "METHOD /path".
        assert "GET" in text
        assert "/pets" in text

    def test_summary_with_description(self) -> None:
        op = _make_operation(
            "/pets",
            HTTPMethod.GET,
            summary="List pets",
            description="Full description here.",
        )
        text = _build_help_text(op)
        assert "List pets" in text
        assert "Full description here." in text


# ------------------------------------------------------------------ #
# request_callback invocation
# ------------------------------------------------------------------ #


class TestRequestCallback:
    """Test that the request_callback is called correctly."""

    def test_callback_receives_method_and_path(
        self, default_rules: PathRulesConfig,
    ) -> None:
        callback = MagicMock()
        op = _make_operation("/items", HTTPMethod.GET, summary="List items")
        spec = _minimal_spec([op])
        app = build_command_tree(spec, default_rules, request_callback=callback)

        result = runner.invoke(app, ["items", "list"])
        assert result.exit_code == 0
        callback.assert_called_once()
        args = callback.call_args
        assert args[0][0] == "get"  # method
        assert args[0][1] == "/items"  # original path

    def test_callback_receives_params(
        self, default_rules: PathRulesConfig,
    ) -> None:
        callback = MagicMock()
        op = _make_operation(
            "/items",
            HTTPMethod.GET,
            summary="List items",
            parameters=[
                APIParameter(
                    name="limit",
                    location=ParameterLocation.QUERY,
                    required=False,
                    schema_type="integer",
                    default=10,
                ),
            ],
        )
        spec = _minimal_spec([op])
        app = build_command_tree(spec, default_rules, request_callback=callback)

        result = runner.invoke(app, ["items", "list", "--limit", "25"])
        assert result.exit_code == 0
        callback.assert_called_once()
        params = callback.call_args[0][2]
        assert params["limit"] == 25

    def test_callback_receives_path_param(
        self, default_rules: PathRulesConfig,
    ) -> None:
        callback = MagicMock()
        op = _make_operation(
            "/items/{item_id}",
            HTTPMethod.GET,
            summary="Get item",
            parameters=[
                APIParameter(
                    name="item_id",
                    location=ParameterLocation.PATH,
                    required=True,
                    schema_type="string",
                ),
            ],
        )
        spec = _minimal_spec([op])
        app = build_command_tree(spec, default_rules, request_callback=callback)

        result = runner.invoke(app, ["items", "get", "abc-123"])
        assert result.exit_code == 0
        callback.assert_called_once()
        params = callback.call_args[0][2]
        assert params["item_id"] == "abc-123"

    def test_callback_receives_body(
        self, default_rules: PathRulesConfig,
    ) -> None:
        callback = MagicMock()
        op = _make_operation(
            "/items",
            HTTPMethod.POST,
            summary="Create item",
            request_body=RequestBodyInfo(
                required=True,
                content_types=["application/json"],
                schema={"type": "object"},
            ),
        )
        spec = _minimal_spec([op])
        app = build_command_tree(spec, default_rules, request_callback=callback)

        body_json = '{"name": "widget"}'
        result = runner.invoke(app, ["items", "create", "--body", body_json])
        assert result.exit_code == 0
        callback.assert_called_once()
        body = callback.call_args[0][3]
        assert body == body_json

    def test_callback_body_none_when_no_body_sent(
        self, default_rules: PathRulesConfig,
    ) -> None:
        callback = MagicMock()
        op = _make_operation(
            "/items",
            HTTPMethod.POST,
            summary="Create item",
            request_body=RequestBodyInfo(
                required=False,
                content_types=["application/json"],
                schema={"type": "object"},
            ),
        )
        spec = _minimal_spec([op])
        app = build_command_tree(spec, default_rules, request_callback=callback)

        result = runner.invoke(app, ["items", "create"])
        assert result.exit_code == 0
        callback.assert_called_once()
        body = callback.call_args[0][3]
        assert body is None

    def test_no_callback_prints_summary(
        self, default_rules: PathRulesConfig,
    ) -> None:
        """When no callback is provided, commands print a dry-run summary."""
        op = _make_operation("/items", HTTPMethod.GET, summary="List items")
        spec = _minimal_spec([op])
        app = build_command_tree(spec, default_rules, request_callback=None)

        result = runner.invoke(app, ["items", "list"])
        assert result.exit_code == 0
        assert "GET" in result.stdout
        assert "/items" in result.stdout


# ------------------------------------------------------------------ #
# Operation grouping
# ------------------------------------------------------------------ #


class TestGroupOperations:
    """Test the operation grouping algorithm."""

    def test_same_resource_grouped(self) -> None:
        ops = [
            _make_operation("/pets", HTTPMethod.GET),
            _make_operation("/pets", HTTPMethod.POST),
            _make_operation("/pets/{petId}", HTTPMethod.GET),
            _make_operation("/pets/{petId}", HTTPMethod.DELETE),
        ]
        path_map = {
            "/pets": "/pets",
            "/pets/{petId}": "/pets/{petId}",
        }
        groups = _group_operations(ops, path_map)
        # Both /pets and /pets/{petId} map to command parts ("pets",).
        assert ("pets",) in groups
        assert len(groups[("pets",)]) == 4

    def test_different_resources_separate(self) -> None:
        ops = [
            _make_operation("/users", HTTPMethod.GET),
            _make_operation("/tasks", HTTPMethod.GET),
        ]
        path_map = {"/users": "/users", "/tasks": "/tasks"}
        groups = _group_operations(ops, path_map)
        assert ("users",) in groups
        assert ("tasks",) in groups
        assert len(groups[("users",)]) == 1
        assert len(groups[("tasks",)]) == 1

    def test_nested_resource_grouping(self) -> None:
        ops = [
            _make_operation("/users/{id}/settings", HTTPMethod.GET),
            _make_operation("/users/{id}/settings", HTTPMethod.PUT),
        ]
        path_map = {
            "/users/{id}/settings": "/users/{id}/settings",
        }
        groups = _group_operations(ops, path_map)
        assert ("users", "settings") in groups
        assert len(groups[("users", "settings")]) == 2

    def test_root_path_gets_synthetic_group(self) -> None:
        ops = [_make_operation("/", HTTPMethod.GET)]
        path_map = {"/": "/"}
        groups = _group_operations(ops, path_map)
        # Root path produces empty command parts, so it gets "root".
        assert ("root",) in groups


# ------------------------------------------------------------------ #
# Multiple operations on same resource
# ------------------------------------------------------------------ #


class TestMultipleVerbs:
    """Test that multiple HTTP methods on the same path produce distinct verbs."""

    def test_list_and_create_on_same_path(
        self, default_rules: PathRulesConfig,
    ) -> None:
        ops = [
            _make_operation("/widgets", HTTPMethod.GET, summary="List widgets"),
            _make_operation(
                "/widgets",
                HTTPMethod.POST,
                summary="Create widget",
                request_body=RequestBodyInfo(
                    required=True,
                    content_types=["application/json"],
                    schema={"type": "object"},
                ),
            ),
        ]
        spec = _minimal_spec(ops)
        app = build_command_tree(spec, default_rules)

        # Both 'list' and 'create' should exist.
        list_result = runner.invoke(app, ["widgets", "list"])
        create_result = runner.invoke(app, ["widgets", "create", "--help"])
        assert list_result.exit_code == 0
        assert create_result.exit_code == 0

    def test_get_and_delete_on_parameterised_path(
        self, default_rules: PathRulesConfig,
    ) -> None:
        ops = [
            _make_operation(
                "/widgets/{id}",
                HTTPMethod.GET,
                summary="Get widget",
                parameters=[
                    APIParameter(
                        name="id",
                        location=ParameterLocation.PATH,
                        required=True,
                        schema_type="string",
                    ),
                ],
            ),
            _make_operation(
                "/widgets/{id}",
                HTTPMethod.DELETE,
                summary="Delete widget",
                parameters=[
                    APIParameter(
                        name="id",
                        location=ParameterLocation.PATH,
                        required=True,
                        schema_type="string",
                    ),
                ],
            ),
        ]
        spec = _minimal_spec(ops)
        app = build_command_tree(spec, default_rules)

        get_result = runner.invoke(app, ["widgets", "get", "--help"])
        delete_result = runner.invoke(app, ["widgets", "delete", "--help"])
        assert get_result.exit_code == 0
        assert delete_result.exit_code == 0


# ------------------------------------------------------------------ #
# Path rules integration
# ------------------------------------------------------------------ #


class TestPathRulesIntegration:
    """Test that path rules are applied before building the command tree."""

    def test_auto_strip_prefix(self) -> None:
        ops = [
            _make_operation("/api/v1/items", HTTPMethod.GET, summary="List"),
            _make_operation("/api/v1/users", HTTPMethod.GET, summary="List"),
        ]
        spec = _minimal_spec(ops)
        rules = PathRulesConfig()  # auto_strip_prefix=True by default
        app = build_command_tree(spec, rules)

        # After stripping /api/v1, commands should be 'items' and 'users'.
        items_result = runner.invoke(app, ["items", "list"])
        users_result = runner.invoke(app, ["users", "list"])
        assert items_result.exit_code == 0
        assert users_result.exit_code == 0

    def test_explicit_strip_prefix(self) -> None:
        ops = [
            _make_operation("/api/v2/things", HTTPMethod.GET, summary="List"),
        ]
        spec = _minimal_spec(ops)
        rules = PathRulesConfig(strip_prefix="/api")
        app = build_command_tree(spec, rules)

        # After stripping /api, the path is /v2/things.
        result = runner.invoke(app, ["v2", "things", "list"])
        assert result.exit_code == 0


# ------------------------------------------------------------------ #
# Body file resolution
# ------------------------------------------------------------------ #


class TestBodyFileResolution:
    """Test the @file body resolution."""

    def test_body_from_file(
        self, default_rules: PathRulesConfig, tmp_path: Path,
    ) -> None:
        callback = MagicMock()
        body_file = tmp_path / "payload.json"
        body_file.write_text('{"key": "value"}', encoding="utf-8")

        op = _make_operation(
            "/things",
            HTTPMethod.POST,
            summary="Create",
            request_body=RequestBodyInfo(
                required=True,
                content_types=["application/json"],
                schema={"type": "object"},
            ),
        )
        spec = _minimal_spec([op])
        app = build_command_tree(spec, default_rules, request_callback=callback)

        result = runner.invoke(app, ["things", "create", "--body", f"@{body_file}"])
        assert result.exit_code == 0
        callback.assert_called_once()
        body = callback.call_args[0][3]
        assert json.loads(body) == {"key": "value"}

    def test_body_file_not_found(
        self, default_rules: PathRulesConfig,
    ) -> None:
        callback = MagicMock()
        op = _make_operation(
            "/things",
            HTTPMethod.POST,
            summary="Create",
            request_body=RequestBodyInfo(
                required=True,
                content_types=["application/json"],
                schema={"type": "object"},
            ),
        )
        spec = _minimal_spec([op])
        app = build_command_tree(spec, default_rules, request_callback=callback)

        result = runner.invoke(app, [
            "things", "create", "--body", "@/nonexistent/file.json",
        ])
        assert result.exit_code == 1
        callback.assert_not_called()


# ------------------------------------------------------------------ #
# Full petstore spec integration
# ------------------------------------------------------------------ #


class TestPetstoreIntegration:
    """Full integration test with the petstore fixture."""

    def test_all_four_operations_reachable(
        self, petstore_spec: ParsedSpec, default_rules: PathRulesConfig,
    ) -> None:
        """All 4 petstore operations should be reachable."""
        callback = MagicMock()
        app = build_command_tree(petstore_spec, default_rules, request_callback=callback)

        # 1. GET /pets -> list
        result = runner.invoke(app, ["pets", "list"])
        assert result.exit_code == 0

        # 2. POST /pets -> create  (needs --name, the required body field)
        result = runner.invoke(app, ["pets", "create", "--name", "Fido"])
        assert result.exit_code == 0

        # 3. GET /pets/{petId} -> get
        result = runner.invoke(app, ["pets", "get", "123"])
        assert result.exit_code == 0

        # 4. DELETE /pets/{petId} -> delete
        result = runner.invoke(app, ["pets", "delete", "123"])
        assert result.exit_code == 0

        assert callback.call_count == 4

    def test_list_passes_query_params(
        self, petstore_spec: ParsedSpec, default_rules: PathRulesConfig,
    ) -> None:
        callback = MagicMock()
        app = build_command_tree(petstore_spec, default_rules, request_callback=callback)

        result = runner.invoke(app, [
            "pets", "list", "--limit", "5", "--status", "available",
        ])
        assert result.exit_code == 0
        params = callback.call_args[0][2]
        assert params["limit"] == 5
        assert params["status"] == "available"

    def test_get_passes_path_param(
        self, petstore_spec: ParsedSpec, default_rules: PathRulesConfig,
    ) -> None:
        callback = MagicMock()
        app = build_command_tree(petstore_spec, default_rules, request_callback=callback)

        result = runner.invoke(app, ["pets", "get", "pet-42"])
        assert result.exit_code == 0
        params = callback.call_args[0][2]
        assert params["petId"] == "pet-42"

    def test_create_accepts_body(
        self, petstore_spec: ParsedSpec, default_rules: PathRulesConfig,
    ) -> None:
        callback = MagicMock()
        app = build_command_tree(petstore_spec, default_rules, request_callback=callback)

        body = '{"name": "Fido"}'
        result = runner.invoke(app, ["pets", "create", "--body", body])
        assert result.exit_code == 0
        assert callback.call_args[0][3] == body

    def test_delete_deprecated_help(
        self, petstore_spec: ParsedSpec, default_rules: PathRulesConfig,
    ) -> None:
        app = build_command_tree(petstore_spec, default_rules)
        result = runner.invoke(app, ["pets", "delete", "--help"])
        assert result.exit_code == 0
        assert "DEPRECATED" in result.stdout
