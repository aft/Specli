"""Tests for specli.plugins.skill.generator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from specli.models import (
    APIInfo,
    APIOperation,
    APIParameter,
    HTTPMethod,
    ParameterLocation,
    ParsedSpec,
    Profile,
    RequestBodyInfo,
    ResponseInfo,
    SecurityScheme,
    ServerInfo,
)
from specli.parser.extractor import extract_spec
from specli.parser.loader import load_spec, validate_openapi_version
from specli.plugins.skill.generator import (
    _group_operations_by_resource,
    _operation_to_command,
    _slugify,
    generate_skill,
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


def _load_fixture(name: str) -> dict[str, Any]:
    """Load a JSON fixture file."""
    path = FIXTURES_DIR / name
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture()
def petstore_spec() -> ParsedSpec:
    """Parse the Petstore 3.0 fixture into a ParsedSpec."""
    raw = _load_fixture("petstore_3.0.json")
    version = validate_openapi_version(raw)
    return extract_spec(raw, version)


@pytest.fixture()
def complex_auth_spec() -> ParsedSpec:
    """Parse the complex auth fixture into a ParsedSpec."""
    raw = _load_fixture("complex_auth.json")
    version = validate_openapi_version(raw)
    return extract_spec(raw, version)


@pytest.fixture()
def minimal_spec() -> ParsedSpec:
    """A minimal ParsedSpec with no operations and no security."""
    return ParsedSpec(
        info=APIInfo(title="Minimal API", version="0.1.0"),
        servers=[],
        operations=[],
        security_schemes={},
        openapi_version="3.0.3",
    )


@pytest.fixture()
def no_auth_spec() -> ParsedSpec:
    """A spec with operations but no security schemes."""
    return ParsedSpec(
        info=APIInfo(
            title="Public API",
            version="1.0.0",
            description="A fully public API with no authentication",
        ),
        servers=[ServerInfo(url="https://api.public.example.com")],
        operations=[
            APIOperation(
                path="/health",
                method=HTTPMethod.GET,
                operation_id="healthCheck",
                summary="Health check endpoint",
                tags=["system"],
                parameters=[],
                responses=[
                    ResponseInfo(status_code="200", description="Healthy"),
                ],
            ),
            APIOperation(
                path="/items",
                method=HTTPMethod.GET,
                operation_id="listItems",
                summary="List all items",
                tags=["items"],
                parameters=[
                    APIParameter(
                        name="page",
                        location=ParameterLocation.QUERY,
                        required=False,
                        description="Page number",
                        schema_type="integer",
                    ),
                ],
                responses=[
                    ResponseInfo(status_code="200", description="Item list"),
                ],
            ),
            APIOperation(
                path="/items/{itemId}",
                method=HTTPMethod.GET,
                operation_id="getItem",
                summary="Get an item by ID",
                tags=["items"],
                parameters=[
                    APIParameter(
                        name="itemId",
                        location=ParameterLocation.PATH,
                        required=True,
                        description="The item ID",
                        schema_type="string",
                    ),
                ],
                responses=[
                    ResponseInfo(status_code="200", description="An item"),
                    ResponseInfo(status_code="404", description="Not found"),
                ],
            ),
        ],
        security_schemes={},
        openapi_version="3.0.3",
    )


# ---------------------------------------------------------------------------
# generate_skill - directory structure
# ---------------------------------------------------------------------------


class TestGenerateSkillDirectoryStructure:
    """Test that generate_skill creates the expected output directory structure."""

    def test_creates_output_directory(self, tmp_path: Path, petstore_spec: ParsedSpec) -> None:
        out = tmp_path / "skill_output"
        result = generate_skill(petstore_spec, out)
        assert result == out
        assert out.is_dir()

    def test_creates_skill_md(self, tmp_path: Path, petstore_spec: ParsedSpec) -> None:
        out = tmp_path / "skill_output"
        generate_skill(petstore_spec, out)
        assert (out / "SKILL.md").is_file()

    def test_creates_references_directory(self, tmp_path: Path, petstore_spec: ParsedSpec) -> None:
        out = tmp_path / "skill_output"
        generate_skill(petstore_spec, out)
        assert (out / "references").is_dir()

    def test_creates_api_reference(self, tmp_path: Path, petstore_spec: ParsedSpec) -> None:
        out = tmp_path / "skill_output"
        generate_skill(petstore_spec, out)
        assert (out / "references" / "api-reference.md").is_file()

    def test_creates_auth_setup(self, tmp_path: Path, petstore_spec: ParsedSpec) -> None:
        out = tmp_path / "skill_output"
        generate_skill(petstore_spec, out)
        assert (out / "references" / "auth-setup.md").is_file()

    def test_idempotent_output(self, tmp_path: Path, petstore_spec: ParsedSpec) -> None:
        """Running generate_skill twice on the same directory should not fail."""
        out = tmp_path / "skill_output"
        generate_skill(petstore_spec, out)
        # Run again -- should overwrite cleanly
        generate_skill(petstore_spec, out)
        assert (out / "SKILL.md").is_file()

    def test_creates_nested_output_dir(self, tmp_path: Path, petstore_spec: ParsedSpec) -> None:
        """Output dir with parents=True handles deep nesting."""
        out = tmp_path / "deep" / "nested" / "skill"
        generate_skill(petstore_spec, out)
        assert out.is_dir()
        assert (out / "SKILL.md").is_file()


# ---------------------------------------------------------------------------
# SKILL.md content
# ---------------------------------------------------------------------------


class TestSkillMdContent:
    """Test the generated SKILL.md file contents."""

    def test_contains_api_title(self, tmp_path: Path, petstore_spec: ParsedSpec) -> None:
        out = tmp_path / "skill"
        generate_skill(petstore_spec, out)
        content = (out / "SKILL.md").read_text(encoding="utf-8")
        assert "Petstore API" in content

    def test_contains_description(self, tmp_path: Path, petstore_spec: ParsedSpec) -> None:
        out = tmp_path / "skill"
        generate_skill(petstore_spec, out)
        content = (out / "SKILL.md").read_text(encoding="utf-8")
        assert "A sample API for managing pets" in content

    def test_contains_command_examples(self, tmp_path: Path, petstore_spec: ParsedSpec) -> None:
        out = tmp_path / "skill"
        generate_skill(petstore_spec, out)
        content = (out / "SKILL.md").read_text(encoding="utf-8")
        # Should contain specli command examples
        assert "specli" in content
        assert "pets" in content

    def test_contains_frontmatter(self, tmp_path: Path, petstore_spec: ParsedSpec) -> None:
        out = tmp_path / "skill"
        generate_skill(petstore_spec, out)
        content = (out / "SKILL.md").read_text(encoding="utf-8")
        assert content.startswith("---")
        assert "name:" in content
        assert "description:" in content

    def test_contains_quick_start(self, tmp_path: Path, petstore_spec: ParsedSpec) -> None:
        out = tmp_path / "skill"
        generate_skill(petstore_spec, out)
        content = (out / "SKILL.md").read_text(encoding="utf-8")
        assert "## Quick Start" in content
        assert "pip install specli" in content
        assert "specli init --spec" in content

    def test_contains_grouped_operations(self, tmp_path: Path, petstore_spec: ParsedSpec) -> None:
        out = tmp_path / "skill"
        generate_skill(petstore_spec, out)
        content = (out / "SKILL.md").read_text(encoding="utf-8")
        # Petstore operations are tagged "pets" so should appear under "Pets" group
        assert "### Pets" in content

    def test_contains_reference_links(self, tmp_path: Path, petstore_spec: ParsedSpec) -> None:
        out = tmp_path / "skill"
        generate_skill(petstore_spec, out)
        content = (out / "SKILL.md").read_text(encoding="utf-8")
        assert "references/auth-setup.md" in content
        assert "references/api-reference.md" in content

    def test_profile_name_in_skill(self, tmp_path: Path, petstore_spec: ParsedSpec) -> None:
        profile = Profile(name="my-petstore", spec="https://example.com/petstore.json")
        out = tmp_path / "skill"
        generate_skill(petstore_spec, out, profile=profile)
        content = (out / "SKILL.md").read_text(encoding="utf-8")
        assert "my-petstore" in content

    def test_spec_url_from_profile(self, tmp_path: Path, petstore_spec: ParsedSpec) -> None:
        profile = Profile(name="petstore", spec="https://example.com/petstore.yaml")
        out = tmp_path / "skill"
        generate_skill(petstore_spec, out, profile=profile)
        content = (out / "SKILL.md").read_text(encoding="utf-8")
        assert "https://example.com/petstore.yaml" in content

    def test_spec_url_falls_back_to_server(self, tmp_path: Path, petstore_spec: ParsedSpec) -> None:
        """Without a profile, spec_url should come from the first server URL."""
        out = tmp_path / "skill"
        generate_skill(petstore_spec, out)
        content = (out / "SKILL.md").read_text(encoding="utf-8")
        assert "https://api.petstore.example.com/v1" in content


# ---------------------------------------------------------------------------
# api-reference.md content
# ---------------------------------------------------------------------------


class TestApiReferenceContent:
    """Test the generated references/api-reference.md file."""

    def test_contains_all_operations(self, tmp_path: Path, petstore_spec: ParsedSpec) -> None:
        out = tmp_path / "skill"
        generate_skill(petstore_spec, out)
        content = (out / "references" / "api-reference.md").read_text(encoding="utf-8")
        # Petstore has 4 operations
        assert "GET /pets" in content
        assert "POST /pets" in content
        assert "GET /pets/{petId}" in content
        assert "DELETE /pets/{petId}" in content

    def test_contains_parameter_table(self, tmp_path: Path, petstore_spec: ParsedSpec) -> None:
        out = tmp_path / "skill"
        generate_skill(petstore_spec, out)
        content = (out / "references" / "api-reference.md").read_text(encoding="utf-8")
        # Should have parameter table headers
        assert "| Name | Location | Type | Required | Description |" in content
        # Should contain actual parameter names
        assert "`limit`" in content
        assert "`status`" in content
        assert "`petId`" in content

    def test_contains_request_body_section(self, tmp_path: Path, petstore_spec: ParsedSpec) -> None:
        out = tmp_path / "skill"
        generate_skill(petstore_spec, out)
        content = (out / "references" / "api-reference.md").read_text(encoding="utf-8")
        assert "### Request Body" in content
        assert "Pet object to create" in content
        assert "application/json" in content

    def test_contains_response_section(self, tmp_path: Path, petstore_spec: ParsedSpec) -> None:
        out = tmp_path / "skill"
        generate_skill(petstore_spec, out)
        content = (out / "references" / "api-reference.md").read_text(encoding="utf-8")
        assert "### Responses" in content
        assert "**200**" in content
        assert "**201**" in content
        assert "**404**" in content

    def test_contains_operation_summaries(self, tmp_path: Path, petstore_spec: ParsedSpec) -> None:
        out = tmp_path / "skill"
        generate_skill(petstore_spec, out)
        content = (out / "references" / "api-reference.md").read_text(encoding="utf-8")
        assert "List all pets" in content
        assert "Create a pet" in content
        assert "Get a pet by ID" in content

    def test_contains_title(self, tmp_path: Path, petstore_spec: ParsedSpec) -> None:
        out = tmp_path / "skill"
        generate_skill(petstore_spec, out)
        content = (out / "references" / "api-reference.md").read_text(encoding="utf-8")
        assert "Petstore API" in content
        assert "API Reference" in content


# ---------------------------------------------------------------------------
# auth-setup.md content
# ---------------------------------------------------------------------------


class TestAuthSetupContent:
    """Test the generated references/auth-setup.md file."""

    def test_contains_security_schemes(self, tmp_path: Path, petstore_spec: ParsedSpec) -> None:
        out = tmp_path / "skill"
        generate_skill(petstore_spec, out)
        content = (out / "references" / "auth-setup.md").read_text(encoding="utf-8")
        # Petstore has apiKeyAuth and bearerAuth
        assert "apiKeyAuth" in content
        assert "bearerAuth" in content

    def test_api_key_auth_instructions(self, tmp_path: Path, petstore_spec: ParsedSpec) -> None:
        out = tmp_path / "skill"
        generate_skill(petstore_spec, out)
        content = (out / "references" / "auth-setup.md").read_text(encoding="utf-8")
        assert "api_key" in content or "api key" in content.lower()
        assert "X-API-Key" in content

    def test_bearer_auth_instructions(self, tmp_path: Path, petstore_spec: ParsedSpec) -> None:
        out = tmp_path / "skill"
        generate_skill(petstore_spec, out)
        content = (out / "references" / "auth-setup.md").read_text(encoding="utf-8")
        assert "bearer" in content.lower()

    def test_testing_auth_section(self, tmp_path: Path, petstore_spec: ParsedSpec) -> None:
        out = tmp_path / "skill"
        generate_skill(petstore_spec, out)
        content = (out / "references" / "auth-setup.md").read_text(encoding="utf-8")
        assert "specli auth test" in content

    def test_complex_auth_all_scheme_types(
        self, tmp_path: Path, complex_auth_spec: ParsedSpec
    ) -> None:
        """Complex auth fixture has apiKey, basic, bearer, oauth2, and openIdConnect."""
        out = tmp_path / "skill"
        generate_skill(complex_auth_spec, out)
        content = (out / "references" / "auth-setup.md").read_text(encoding="utf-8")
        # All scheme types should be present
        assert "apiKeyHeader" in content
        assert "apiKeyQuery" in content
        assert "basicAuth" in content
        assert "bearerAuth" in content
        assert "oauth2ClientCreds" in content
        assert "oauth2AuthCode" in content
        assert "openIdConnect" in content

    def test_oauth2_flow_info(self, tmp_path: Path, complex_auth_spec: ParsedSpec) -> None:
        out = tmp_path / "skill"
        generate_skill(complex_auth_spec, out)
        content = (out / "references" / "auth-setup.md").read_text(encoding="utf-8")
        assert "Flows" in content
        assert "specli auth login" in content

    def test_openid_connect_url(self, tmp_path: Path, complex_auth_spec: ParsedSpec) -> None:
        out = tmp_path / "skill"
        generate_skill(complex_auth_spec, out)
        content = (out / "references" / "auth-setup.md").read_text(encoding="utf-8")
        assert "openid-configuration" in content or "Discovery URL" in content


# ---------------------------------------------------------------------------
# No security schemes
# ---------------------------------------------------------------------------


class TestNoSecuritySchemes:
    """Test generation with an API that has no security schemes."""

    def test_auth_setup_says_no_auth(self, tmp_path: Path, no_auth_spec: ParsedSpec) -> None:
        out = tmp_path / "skill"
        generate_skill(no_auth_spec, out)
        content = (out / "references" / "auth-setup.md").read_text(encoding="utf-8")
        assert "does not require authentication" in content

    def test_skill_md_still_generated(self, tmp_path: Path, no_auth_spec: ParsedSpec) -> None:
        out = tmp_path / "skill"
        generate_skill(no_auth_spec, out)
        content = (out / "SKILL.md").read_text(encoding="utf-8")
        assert "Public API" in content

    def test_api_reference_has_operations(self, tmp_path: Path, no_auth_spec: ParsedSpec) -> None:
        out = tmp_path / "skill"
        generate_skill(no_auth_spec, out)
        content = (out / "references" / "api-reference.md").read_text(encoding="utf-8")
        assert "GET /health" in content
        assert "GET /items" in content
        assert "GET /items/{itemId}" in content


# ---------------------------------------------------------------------------
# Empty operations list
# ---------------------------------------------------------------------------


class TestEmptyOperations:
    """Test generation with an empty operations list."""

    def test_skill_md_generated(self, tmp_path: Path, minimal_spec: ParsedSpec) -> None:
        out = tmp_path / "skill"
        generate_skill(minimal_spec, out)
        assert (out / "SKILL.md").is_file()

    def test_skill_md_contains_title(self, tmp_path: Path, minimal_spec: ParsedSpec) -> None:
        out = tmp_path / "skill"
        generate_skill(minimal_spec, out)
        content = (out / "SKILL.md").read_text(encoding="utf-8")
        assert "Minimal API" in content

    def test_api_reference_has_header_only(self, tmp_path: Path, minimal_spec: ParsedSpec) -> None:
        out = tmp_path / "skill"
        generate_skill(minimal_spec, out)
        content = (out / "references" / "api-reference.md").read_text(encoding="utf-8")
        assert "Minimal API" in content
        assert "API Reference" in content
        # No operations should be documented
        assert "## GET" not in content
        assert "## POST" not in content

    def test_auth_setup_no_auth(self, tmp_path: Path, minimal_spec: ParsedSpec) -> None:
        out = tmp_path / "skill"
        generate_skill(minimal_spec, out)
        content = (out / "references" / "auth-setup.md").read_text(encoding="utf-8")
        assert "does not require authentication" in content

    def test_skill_md_no_command_sections(self, tmp_path: Path, minimal_spec: ParsedSpec) -> None:
        """No groups should render when there are no operations."""
        out = tmp_path / "skill"
        generate_skill(minimal_spec, out)
        content = (out / "SKILL.md").read_text(encoding="utf-8")
        # "### " group headers should not appear
        assert "### " not in content.split("## Available Commands")[1].split("## Auth Setup")[0] or \
               content.split("## Available Commands")[1].split("## Auth Setup")[0].strip() == ""


# ---------------------------------------------------------------------------
# _group_operations_by_resource
# ---------------------------------------------------------------------------


class TestGroupOperationsByResource:
    """Test operation grouping logic."""

    def test_groups_by_first_tag(self) -> None:
        ops = [
            APIOperation(
                path="/pets",
                method=HTTPMethod.GET,
                tags=["pets"],
                summary="List pets",
            ),
            APIOperation(
                path="/pets/{id}",
                method=HTTPMethod.GET,
                tags=["pets"],
                summary="Get pet",
            ),
            APIOperation(
                path="/users",
                method=HTTPMethod.GET,
                tags=["users"],
                summary="List users",
            ),
        ]
        groups = _group_operations_by_resource(ops)
        assert "Pets" in groups
        assert "Users" in groups
        assert len(groups["Pets"]) == 2
        assert len(groups["Users"]) == 1

    def test_groups_by_path_when_no_tags(self) -> None:
        ops = [
            APIOperation(
                path="/orders",
                method=HTTPMethod.GET,
                tags=[],
                summary="List orders",
            ),
            APIOperation(
                path="/orders/{id}",
                method=HTTPMethod.GET,
                tags=[],
                summary="Get order",
            ),
        ]
        groups = _group_operations_by_resource(ops)
        assert "Orders" in groups
        assert len(groups["Orders"]) == 2

    def test_mixed_tags_and_no_tags(self) -> None:
        ops = [
            APIOperation(
                path="/pets",
                method=HTTPMethod.GET,
                tags=["pets"],
                summary="List pets",
            ),
            APIOperation(
                path="/health",
                method=HTTPMethod.GET,
                tags=[],
                summary="Health check",
            ),
        ]
        groups = _group_operations_by_resource(ops)
        assert "Pets" in groups
        assert "Health" in groups

    def test_empty_operations(self) -> None:
        groups = _group_operations_by_resource([])
        assert groups == {}

    def test_root_path_uses_general(self) -> None:
        """An operation at / with no tags should fall back to 'General'."""
        ops = [
            APIOperation(
                path="/",
                method=HTTPMethod.GET,
                tags=[],
                summary="Root",
            ),
        ]
        groups = _group_operations_by_resource(ops)
        assert "General" in groups

    def test_petstore_grouping(self, petstore_spec: ParsedSpec) -> None:
        groups = _group_operations_by_resource(petstore_spec.operations)
        # All petstore operations are tagged "pets" (some also "admin")
        assert "Pets" in groups
        assert len(groups["Pets"]) >= 3  # At least list, create, get


# ---------------------------------------------------------------------------
# _operation_to_command
# ---------------------------------------------------------------------------


class TestOperationToCommand:
    """Test CLI command string generation from operations."""

    def test_get_list(self) -> None:
        op = APIOperation(path="/pets", method=HTTPMethod.GET, summary="List pets")
        cmd = _operation_to_command(op)
        assert cmd == "specli pets list"

    def test_get_with_path_param(self) -> None:
        op = APIOperation(path="/pets/{petId}", method=HTTPMethod.GET, summary="Get pet")
        cmd = _operation_to_command(op)
        assert cmd == "specli pets get <petId>"

    def test_post_create(self) -> None:
        op = APIOperation(path="/pets", method=HTTPMethod.POST, summary="Create pet")
        cmd = _operation_to_command(op)
        assert cmd == "specli pets create"

    def test_put_update(self) -> None:
        op = APIOperation(path="/pets/{id}", method=HTTPMethod.PUT, summary="Update pet")
        cmd = _operation_to_command(op)
        assert cmd == "specli pets update <id>"

    def test_patch_method(self) -> None:
        op = APIOperation(path="/pets/{id}", method=HTTPMethod.PATCH, summary="Patch pet")
        cmd = _operation_to_command(op)
        assert cmd == "specli pets patch <id>"

    def test_delete_method(self) -> None:
        op = APIOperation(path="/pets/{id}", method=HTTPMethod.DELETE, summary="Delete pet")
        cmd = _operation_to_command(op)
        assert cmd == "specli pets delete <id>"

    def test_nested_path_with_intermediate_param(self) -> None:
        """Path params in the middle still count as params for verb selection."""
        op = APIOperation(
            path="/users/{userId}/pets",
            method=HTTPMethod.GET,
            summary="List user pets",
        )
        cmd = _operation_to_command(op)
        # {userId} is a path param, so verb is "get" and param appears as arg
        assert cmd == "specli users pets get <userId>"

    def test_nested_path_no_params(self) -> None:
        """GET on a nested resource with no path params uses 'list'."""
        op = APIOperation(
            path="/admin/reports",
            method=HTTPMethod.GET,
            summary="List admin reports",
        )
        cmd = _operation_to_command(op)
        assert cmd == "specli admin reports list"

    def test_nested_path_with_trailing_param(self) -> None:
        op = APIOperation(
            path="/users/{userId}/pets/{petId}",
            method=HTTPMethod.GET,
            summary="Get user pet",
        )
        cmd = _operation_to_command(op)
        assert cmd == "specli users pets get <userId> <petId>"

    def test_root_path(self) -> None:
        op = APIOperation(path="/", method=HTTPMethod.GET, summary="Root")
        cmd = _operation_to_command(op)
        assert cmd == "specli root list"

    def test_custom_profile_name(self) -> None:
        """Profile name is currently unused in command but should not break."""
        op = APIOperation(path="/pets", method=HTTPMethod.GET, summary="List pets")
        cmd = _operation_to_command(op, profile_name="my-api")
        assert cmd == "specli pets list"

    def test_head_method(self) -> None:
        op = APIOperation(path="/pets", method=HTTPMethod.HEAD, summary="Head pets")
        cmd = _operation_to_command(op)
        assert cmd == "specli pets head"

    def test_options_method(self) -> None:
        op = APIOperation(path="/pets", method=HTTPMethod.OPTIONS, summary="Options")
        cmd = _operation_to_command(op)
        assert cmd == "specli pets options"


# ---------------------------------------------------------------------------
# _slugify
# ---------------------------------------------------------------------------


class TestSlugify:
    """Test the _slugify helper."""

    def test_simple_title(self) -> None:
        assert _slugify("Petstore API") == "petstore-api"

    def test_special_characters(self) -> None:
        assert _slugify("My API (v2.0)") == "my-api-v2-0"

    def test_leading_trailing_hyphens_stripped(self) -> None:
        assert _slugify("---Hello World---") == "hello-world"

    def test_consecutive_specials_collapsed(self) -> None:
        assert _slugify("One    Two") == "one-two"

    def test_already_slug(self) -> None:
        assert _slugify("my-api") == "my-api"


# ---------------------------------------------------------------------------
# Full Petstore skill generation (integration)
# ---------------------------------------------------------------------------


class TestPetstoreFullGeneration:
    """Integration test: generate a complete skill from the Petstore fixture."""

    def test_full_petstore_skill(self, tmp_path: Path, petstore_spec: ParsedSpec) -> None:
        out = tmp_path / "petstore_skill"
        result = generate_skill(petstore_spec, out)

        # Directory structure
        assert result == out
        assert (out / "SKILL.md").is_file()
        assert (out / "references" / "api-reference.md").is_file()
        assert (out / "references" / "auth-setup.md").is_file()

        # SKILL.md
        skill_md = (out / "SKILL.md").read_text(encoding="utf-8")
        assert "Petstore API" in skill_md
        assert "A sample API for managing pets" in skill_md
        assert "specli" in skill_md
        assert "## Available Commands" in skill_md
        assert "## Auth Setup" in skill_md
        assert "## API Reference" in skill_md

        # api-reference.md
        api_ref = (out / "references" / "api-reference.md").read_text(encoding="utf-8")
        assert "GET /pets" in api_ref
        assert "POST /pets" in api_ref
        assert "GET /pets/{petId}" in api_ref
        assert "DELETE /pets/{petId}" in api_ref
        assert "### Parameters" in api_ref
        assert "### Responses" in api_ref

        # auth-setup.md
        auth_setup = (out / "references" / "auth-setup.md").read_text(encoding="utf-8")
        assert "apiKeyAuth" in auth_setup
        assert "bearerAuth" in auth_setup
        assert "specli auth test" in auth_setup

    def test_petstore_with_profile(self, tmp_path: Path, petstore_spec: ParsedSpec) -> None:
        profile = Profile(
            name="petstore-prod",
            spec="https://api.petstore.example.com/openapi.json",
            base_url="https://api.petstore.example.com/v1",
        )
        out = tmp_path / "petstore_skill"
        generate_skill(petstore_spec, out, profile=profile)

        skill_md = (out / "SKILL.md").read_text(encoding="utf-8")
        assert "petstore-prod" in skill_md
        assert "https://api.petstore.example.com/openapi.json" in skill_md

        auth_setup = (out / "references" / "auth-setup.md").read_text(encoding="utf-8")
        assert "petstore-prod" in auth_setup

    def test_petstore_api_reference_parameter_details(
        self, tmp_path: Path, petstore_spec: ParsedSpec
    ) -> None:
        """Verify parameter details are correctly rendered in the reference."""
        out = tmp_path / "skill"
        generate_skill(petstore_spec, out)
        content = (out / "references" / "api-reference.md").read_text(encoding="utf-8")

        # Check specific parameter attributes
        assert "`limit`" in content
        assert "query" in content
        assert "integer" in content
        assert "`petId`" in content
        assert "path" in content

    def test_complex_auth_full_generation(
        self, tmp_path: Path, complex_auth_spec: ParsedSpec
    ) -> None:
        """Generate skill from complex auth fixture and verify all scheme types."""
        out = tmp_path / "complex_auth_skill"
        generate_skill(complex_auth_spec, out)

        auth_setup = (out / "references" / "auth-setup.md").read_text(encoding="utf-8")

        # Verify each security scheme type has appropriate instructions
        assert "api_key" in auth_setup.lower() or "api key" in auth_setup.lower()
        assert "basic" in auth_setup.lower()
        assert "bearer" in auth_setup.lower()
        assert "oauth2" in auth_setup.lower() or "oauth" in auth_setup.lower()
        assert "openid" in auth_setup.lower() or "openidconnect" in auth_setup.lower()
