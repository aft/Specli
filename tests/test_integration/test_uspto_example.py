"""Integration tests using the USPTO example spec.

Loads the real USPTO OpenAPI spec from the examples directory, builds a
command tree, and verifies that the generated CLI commands work correctly.
Only tests readonly (GET) commands — no live HTTP calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from specli.generator import build_command_tree
from specli.models import ParsedSpec, PathRulesConfig
from specli.parser.extractor import extract_spec
from specli.parser.loader import validate_openapi_version

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"


@pytest.fixture
def uspto_raw() -> dict[str, Any]:
    """Load the raw USPTO OpenAPI spec."""
    with open(EXAMPLES_DIR / "uspto-openapi.json") as f:
        return json.load(f)


@pytest.fixture
def uspto_spec(uspto_raw: dict[str, Any]) -> ParsedSpec:
    """Parsed USPTO spec."""
    version = validate_openapi_version(uspto_raw)
    return extract_spec(uspto_raw, version)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ---------------------------------------------------------------------------
# Command tree structure
# ---------------------------------------------------------------------------


class TestUsptoCommandTree:
    """Verify that the USPTO spec produces the expected command structure."""

    def test_help_shows_subcommands(
        self, uspto_spec: ParsedSpec, runner: CliRunner
    ) -> None:
        app = build_command_tree(uspto_spec, PathRulesConfig(), lambda *a: None)
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        output = result.stdout.lower()
        assert "root" in output
        assert "fields" in output
        assert "records" in output

    def test_root_subcommand_has_list(
        self, uspto_spec: ParsedSpec, runner: CliRunner
    ) -> None:
        app = build_command_tree(uspto_spec, PathRulesConfig(), lambda *a: None)
        result = runner.invoke(app, ["root", "--help"])
        assert result.exit_code == 0
        assert "list" in result.stdout.lower()

    def test_fields_subcommand_has_list(
        self, uspto_spec: ParsedSpec, runner: CliRunner
    ) -> None:
        app = build_command_tree(uspto_spec, PathRulesConfig(), lambda *a: None)
        result = runner.invoke(app, ["fields", "--help"])
        assert result.exit_code == 0
        assert "list" in result.stdout.lower()

    def test_records_subcommand_has_create(
        self, uspto_spec: ParsedSpec, runner: CliRunner
    ) -> None:
        app = build_command_tree(uspto_spec, PathRulesConfig(), lambda *a: None)
        result = runner.invoke(app, ["records", "--help"])
        assert result.exit_code == 0
        assert "create" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Callback invocation (GET commands only)
# ---------------------------------------------------------------------------


class TestUsptoGetCallbacks:
    """Verify GET commands invoke the callback with correct arguments."""

    def test_root_list_callback(
        self, uspto_spec: ParsedSpec, runner: CliRunner
    ) -> None:
        """GET / should invoke callback with method=get and path=/."""
        captured: dict[str, Any] = {}

        def cb(method: str, path: str, params: dict, body: str | None, content_type: str | None = None) -> None:
            captured.update(method=method, path=path, params=params, body=body)

        app = build_command_tree(uspto_spec, PathRulesConfig(), cb)
        result = runner.invoke(app, ["root", "list"])
        assert result.exit_code == 0
        assert captured["method"].upper() == "GET"
        assert captured["path"] == "/"
        assert captured["body"] is None

    def test_fields_list_passes_path_params(
        self, uspto_spec: ParsedSpec, runner: CliRunner
    ) -> None:
        """GET /{dataset}/{version}/fields should pass both path params."""
        captured: dict[str, Any] = {}

        def cb(method: str, path: str, params: dict, body: str | None, content_type: str | None = None) -> None:
            captured.update(method=method, path=path, params=params)

        app = build_command_tree(uspto_spec, PathRulesConfig(), cb)
        result = runner.invoke(app, ["fields", "list", "oa_citations", "v1"])
        assert result.exit_code == 0
        assert captured["method"].upper() == "GET"
        assert captured["path"] == "/{dataset}/{version}/fields"
        assert captured["params"]["dataset"] == "oa_citations"
        assert captured["params"]["version"] == "v1"

    def test_fields_list_help_shows_arguments(
        self, uspto_spec: ParsedSpec, runner: CliRunner
    ) -> None:
        """fields list --help should show dataset and version as arguments."""
        app = build_command_tree(uspto_spec, PathRulesConfig(), lambda *a: None)
        result = runner.invoke(app, ["fields", "list", "--help"])
        assert result.exit_code == 0
        output = result.stdout.lower()
        assert "dataset" in output
        assert "version" in output

    def test_fields_list_requires_arguments(
        self, uspto_spec: ParsedSpec, runner: CliRunner
    ) -> None:
        """fields list without arguments should fail."""
        app = build_command_tree(uspto_spec, PathRulesConfig(), lambda *a: None)
        result = runner.invoke(app, ["fields", "list"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Content type detection
# ---------------------------------------------------------------------------


class TestUsptoContentType:
    """Verify that content_type is correctly passed through for POST endpoints."""

    def test_records_create_sends_form_content_type(
        self, uspto_spec: ParsedSpec, runner: CliRunner
    ) -> None:
        """POST records should pass form-urlencoded content type from the spec."""
        captured: dict[str, Any] = {}

        def cb(method: str, path: str, params: dict, body: str | None, content_type: str | None = None) -> None:
            captured.update(method=method, path=path, content_type=content_type, body=body)

        app = build_command_tree(uspto_spec, PathRulesConfig(), cb)
        result = runner.invoke(app, ["records", "create", "v1", "oa_citations", "-b", '{"criteria": "*:*"}'])
        assert result.exit_code == 0
        assert captured["method"].upper() == "POST"
        assert captured["content_type"] is not None
        assert "form-urlencoded" in captured["content_type"]

    def test_get_endpoints_have_no_content_type(
        self, uspto_spec: ParsedSpec, runner: CliRunner
    ) -> None:
        """GET endpoints should pass content_type=None (no request body)."""
        captured: dict[str, Any] = {}

        def cb(method: str, path: str, params: dict, body: str | None, content_type: str | None = None) -> None:
            captured.update(content_type=content_type)

        app = build_command_tree(uspto_spec, PathRulesConfig(), cb)
        result = runner.invoke(app, ["root", "list"])
        assert result.exit_code == 0
        assert captured["content_type"] is None


# ---------------------------------------------------------------------------
# Help text quality
# ---------------------------------------------------------------------------


class TestUsptoHelpText:
    """Verify that help text from the spec is correctly propagated."""

    def test_root_help_contains_api_description(
        self, uspto_spec: ParsedSpec, runner: CliRunner
    ) -> None:
        app = build_command_tree(uspto_spec, PathRulesConfig(), lambda *a: None)
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "Data Set API" in result.stdout

    def test_fields_list_help_contains_summary(
        self, uspto_spec: ParsedSpec, runner: CliRunner
    ) -> None:
        app = build_command_tree(uspto_spec, PathRulesConfig(), lambda *a: None)
        result = runner.invoke(app, ["fields", "list", "--help"])
        assert result.exit_code == 0
        assert "general information" in result.stdout.lower()

    def test_records_create_help_contains_description(
        self, uspto_spec: ParsedSpec, runner: CliRunner
    ) -> None:
        app = build_command_tree(uspto_spec, PathRulesConfig(), lambda *a: None)
        result = runner.invoke(app, ["records", "create", "--help"])
        assert result.exit_code == 0
        assert "Solr" in result.stdout or "Lucene" in result.stdout

    def test_tag_descriptions_used_for_groups(
        self, uspto_spec: ParsedSpec, runner: CliRunner
    ) -> None:
        """Tag descriptions from the spec should appear as group help text."""
        app = build_command_tree(uspto_spec, PathRulesConfig(), lambda *a: None)
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        # "metadata" tag has description "Find out about the data sets"
        assert "data set" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Spec parsing
# ---------------------------------------------------------------------------


class TestUsptoSpecParsing:
    """Verify the USPTO spec is correctly parsed."""

    def test_spec_has_three_operations(self, uspto_spec: ParsedSpec) -> None:
        assert len(uspto_spec.operations) == 3

    def test_spec_title(self, uspto_spec: ParsedSpec) -> None:
        assert "USPTO" in uspto_spec.info.title

    def test_operations_have_summaries(self, uspto_spec: ParsedSpec) -> None:
        for op in uspto_spec.operations:
            assert op.summary, f"Operation {op.method} {op.path} missing summary"

    def test_post_operation_has_form_content_type(self, uspto_spec: ParsedSpec) -> None:
        post_ops = [op for op in uspto_spec.operations if op.method.value == "post"]
        assert len(post_ops) == 1
        assert post_ops[0].request_body is not None
        assert "application/x-www-form-urlencoded" in post_ops[0].request_body.content_types

    def test_get_operations_have_no_request_body(self, uspto_spec: ParsedSpec) -> None:
        get_ops = [op for op in uspto_spec.operations if op.method.value == "get"]
        assert len(get_ops) == 2
        for op in get_ops:
            assert op.request_body is None

    def test_fields_endpoint_has_path_params(self, uspto_spec: ParsedSpec) -> None:
        fields_op = next(
            op for op in uspto_spec.operations
            if op.path == "/{dataset}/{version}/fields"
        )
        param_names = [p.name for p in fields_op.parameters]
        assert "dataset" in param_names
        assert "version" in param_names

    def test_spec_has_tags(self, uspto_spec: ParsedSpec) -> None:
        all_tags = set()
        for op in uspto_spec.operations:
            all_tags.update(op.tags)
        assert "metadata" in all_tags
        assert "search" in all_tags
