"""Tests for specli.parser.extractor."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from specli.models import (
    APIOperation,
    HTTPMethod,
    ParameterLocation,
    ParsedSpec,
)
from specli.parser.extractor import (
    _extract_info,
    _extract_operations,
    _extract_parameters,
    _extract_request_body,
    _extract_responses,
    _extract_security_schemes,
    _extract_servers,
    _merge_parameters,
    extract_spec,
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


def _load_fixture(name: str) -> dict[str, Any]:
    """Load a JSON fixture file."""
    path = FIXTURES_DIR / name
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Full extraction from petstore 3.0
# ---------------------------------------------------------------------------


class TestExtractSpecPetstore30:
    """Test full spec extraction from the Petstore 3.0 fixture."""

    @pytest.fixture()
    def parsed(self) -> ParsedSpec:
        raw = _load_fixture("petstore_3.0.json")
        return extract_spec(raw, "3.0.3")

    def test_openapi_version(self, parsed: ParsedSpec) -> None:
        assert parsed.openapi_version == "3.0.3"

    def test_info(self, parsed: ParsedSpec) -> None:
        assert parsed.info.title == "Petstore API"
        assert parsed.info.version == "1.0.0"
        assert parsed.info.description == "A sample API for managing pets"
        assert parsed.info.terms_of_service == "https://example.com/terms"
        assert parsed.info.contact_name == "API Support"
        assert parsed.info.contact_email == "support@example.com"
        assert parsed.info.contact_url == "https://example.com/support"
        assert parsed.info.license_name == "MIT"
        assert parsed.info.license_url == "https://opensource.org/licenses/MIT"

    def test_servers(self, parsed: ParsedSpec) -> None:
        assert len(parsed.servers) == 2
        assert parsed.servers[0].url == "https://api.petstore.example.com/v1"
        assert parsed.servers[0].description == "Production server"
        assert parsed.servers[1].url == "https://staging.petstore.example.com/v1"

    def test_operation_count(self, parsed: ParsedSpec) -> None:
        # GET /pets, POST /pets, GET /pets/{petId}, DELETE /pets/{petId}
        assert len(parsed.operations) == 4

    def test_list_pets_operation(self, parsed: ParsedSpec) -> None:
        op = _find_operation(parsed, "GET", "/pets")
        assert op is not None
        assert op.operation_id == "listPets"
        assert op.summary == "List all pets"
        assert op.description == "Returns a list of all pets in the store"
        assert op.tags == ["pets"]
        assert op.deprecated is False

    def test_list_pets_parameters(self, parsed: ParsedSpec) -> None:
        op = _find_operation(parsed, "GET", "/pets")
        assert op is not None
        # 2 operation-level params + 1 path-level param = 3 total
        assert len(op.parameters) == 3

        # Check limit param
        limit = _find_param(op, "limit")
        assert limit is not None
        assert limit.location == ParameterLocation.QUERY
        assert limit.required is False
        assert limit.schema_type == "integer"
        assert limit.schema_format == "int32"
        assert limit.default == 20
        assert limit.example == 10

        # Check status param with enum
        status = _find_param(op, "status")
        assert status is not None
        assert status.enum_values == ["available", "pending", "sold"]

        # Check path-level header param
        req_id = _find_param(op, "X-Request-Id")
        assert req_id is not None
        assert req_id.location == ParameterLocation.HEADER

    def test_create_pet_request_body(self, parsed: ParsedSpec) -> None:
        op = _find_operation(parsed, "POST", "/pets")
        assert op is not None
        assert op.request_body is not None
        assert op.request_body.required is True
        assert op.request_body.description == "Pet object to create"
        assert "application/json" in op.request_body.content_types
        # Schema should be resolved from $ref
        assert op.request_body.schema_ is not None
        assert op.request_body.schema_.get("type") == "object"

    def test_create_pet_responses(self, parsed: ParsedSpec) -> None:
        op = _find_operation(parsed, "POST", "/pets")
        assert op is not None
        assert len(op.responses) == 2
        r201 = _find_response(op, "201")
        assert r201 is not None
        assert r201.description == "Pet created successfully"
        assert "application/json" in r201.content_types

        r400 = _find_response(op, "400")
        assert r400 is not None
        assert r400.description == "Invalid input"
        assert r400.content_types == []

    def test_get_pet_path_parameter(self, parsed: ParsedSpec) -> None:
        op = _find_operation(parsed, "GET", "/pets/{petId}")
        assert op is not None
        pet_id = _find_param(op, "petId")
        assert pet_id is not None
        assert pet_id.location == ParameterLocation.PATH
        assert pet_id.required is True  # Path params are always required

    def test_deprecated_operation(self, parsed: ParsedSpec) -> None:
        op = _find_operation(parsed, "DELETE", "/pets/{petId}")
        assert op is not None
        assert op.deprecated is True

    def test_operation_level_security_override(self, parsed: ParsedSpec) -> None:
        # DELETE /pets/{petId} has security: [] (no auth)
        op = _find_operation(parsed, "DELETE", "/pets/{petId}")
        assert op is not None
        assert op.security == []

    def test_global_security_inherited(self, parsed: ParsedSpec) -> None:
        # GET /pets should inherit global security
        op = _find_operation(parsed, "GET", "/pets")
        assert op is not None
        assert len(op.security) == 1
        assert "apiKeyAuth" in op.security[0]

    def test_security_schemes(self, parsed: ParsedSpec) -> None:
        assert len(parsed.security_schemes) == 2
        assert "apiKeyAuth" in parsed.security_schemes
        assert "bearerAuth" in parsed.security_schemes

        api_key = parsed.security_schemes["apiKeyAuth"]
        assert api_key.type == "apiKey"
        assert api_key.param_name == "X-API-Key"
        assert api_key.location == "header"
        assert api_key.description == "API key passed in the X-API-Key header"

        bearer = parsed.security_schemes["bearerAuth"]
        assert bearer.type == "http"
        assert bearer.scheme == "bearer"
        assert bearer.bearer_format == "JWT"

    def test_raw_spec_preserved(self, parsed: ParsedSpec) -> None:
        assert parsed.raw_spec is not None
        assert parsed.raw_spec["openapi"] == "3.0.3"


# ---------------------------------------------------------------------------
# Full extraction from petstore 3.1
# ---------------------------------------------------------------------------


class TestExtractSpecPetstore31:
    """Test full spec extraction from the Petstore 3.1 fixture."""

    @pytest.fixture()
    def parsed(self) -> ParsedSpec:
        raw = _load_fixture("petstore_3.1.json")
        return extract_spec(raw, "3.1.0")

    def test_openapi_version(self, parsed: ParsedSpec) -> None:
        assert parsed.openapi_version == "3.1.0"

    def test_info(self, parsed: ParsedSpec) -> None:
        assert parsed.info.title == "Petstore API"
        assert parsed.info.version == "1.0.0"

    def test_operation_count(self, parsed: ParsedSpec) -> None:
        assert len(parsed.operations) == 4

    def test_type_array_handling(self, parsed: ParsedSpec) -> None:
        """OpenAPI 3.1 uses type arrays like ["string", "null"]."""
        op = _find_operation(parsed, "GET", "/pets")
        assert op is not None
        # The X-Request-Id header has type: ["string", "null"]
        req_id = _find_param(op, "X-Request-Id")
        assert req_id is not None
        # Should extract first non-null type
        assert req_id.schema_type == "string"

    def test_all_operations_present(self, parsed: ParsedSpec) -> None:
        methods_paths = {(op.method.value, op.path) for op in parsed.operations}
        assert ("get", "/pets") in methods_paths
        assert ("post", "/pets") in methods_paths
        assert ("get", "/pets/{petId}") in methods_paths
        assert ("delete", "/pets/{petId}") in methods_paths


# ---------------------------------------------------------------------------
# _extract_info
# ---------------------------------------------------------------------------


class TestExtractInfo:
    """Test info extraction."""

    def test_full_info(self) -> None:
        spec = _load_fixture("petstore_3.0.json")
        info = _extract_info(spec)
        assert info.title == "Petstore API"
        assert info.version == "1.0.0"
        assert info.description is not None
        assert info.contact_name == "API Support"
        assert info.license_name == "MIT"

    def test_minimal_info(self) -> None:
        spec = {"info": {"title": "Minimal", "version": "0.1"}}
        info = _extract_info(spec)
        assert info.title == "Minimal"
        assert info.version == "0.1"
        assert info.description is None
        assert info.contact_name is None
        assert info.license_name is None

    def test_missing_info_uses_defaults(self) -> None:
        info = _extract_info({})
        assert info.title == "Untitled API"
        assert info.version == "0.0.0"


# ---------------------------------------------------------------------------
# _extract_servers
# ---------------------------------------------------------------------------


class TestExtractServers:
    """Test server extraction."""

    def test_extracts_servers(self) -> None:
        spec = _load_fixture("petstore_3.0.json")
        servers = _extract_servers(spec)
        assert len(servers) == 2
        assert servers[0].url == "https://api.petstore.example.com/v1"

    def test_no_servers_returns_empty(self) -> None:
        servers = _extract_servers({})
        assert servers == []

    def test_server_without_description(self) -> None:
        spec = {"servers": [{"url": "https://api.example.com"}]}
        servers = _extract_servers(spec)
        assert len(servers) == 1
        assert servers[0].url == "https://api.example.com"
        assert servers[0].description is None


# ---------------------------------------------------------------------------
# _extract_operations
# ---------------------------------------------------------------------------


class TestExtractOperations:
    """Test operation extraction."""

    def test_extracts_all_operations(self) -> None:
        spec = _load_fixture("petstore_3.0.json")
        from specli.parser.resolver import resolve_refs
        resolved = resolve_refs(spec)
        ops = _extract_operations(resolved)
        assert len(ops) == 4

    def test_operation_with_no_parameters(self) -> None:
        spec = {
            "paths": {
                "/health": {
                    "get": {
                        "operationId": "healthCheck",
                        "summary": "Health check",
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            }
        }
        ops = _extract_operations(spec)
        assert len(ops) == 1
        assert ops[0].parameters == []

    def test_operation_with_no_request_body(self) -> None:
        spec = {
            "paths": {
                "/items": {
                    "get": {
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            }
        }
        ops = _extract_operations(spec)
        assert len(ops) == 1
        assert ops[0].request_body is None

    def test_empty_paths(self) -> None:
        ops = _extract_operations({"paths": {}})
        assert ops == []

    def test_missing_paths(self) -> None:
        ops = _extract_operations({})
        assert ops == []

    def test_ignores_non_method_keys(self) -> None:
        """Keys like 'summary', 'parameters' at path level are not methods."""
        spec = {
            "paths": {
                "/test": {
                    "summary": "Test path",
                    "description": "Path description",
                    "get": {
                        "responses": {"200": {"description": "OK"}},
                    },
                }
            }
        }
        ops = _extract_operations(spec)
        assert len(ops) == 1
        assert ops[0].method == HTTPMethod.GET


# ---------------------------------------------------------------------------
# _merge_parameters
# ---------------------------------------------------------------------------


class TestMergeParameters:
    """Test parameter merging logic."""

    def test_operation_overrides_path_param(self) -> None:
        path_params = [
            {"name": "limit", "in": "query", "schema": {"type": "integer"}}
        ]
        op_params = [
            {"name": "limit", "in": "query", "schema": {"type": "string"}, "required": True}
        ]
        merged = _merge_parameters(path_params, op_params)
        assert len(merged) == 1
        assert merged[0]["schema"]["type"] == "string"
        assert merged[0]["required"] is True

    def test_combines_different_params(self) -> None:
        path_params = [
            {"name": "X-Request-Id", "in": "header"}
        ]
        op_params = [
            {"name": "limit", "in": "query"}
        ]
        merged = _merge_parameters(path_params, op_params)
        assert len(merged) == 2

    def test_same_name_different_location_kept(self) -> None:
        """Parameters with same name but different location are distinct."""
        path_params = [
            {"name": "id", "in": "header"}
        ]
        op_params = [
            {"name": "id", "in": "query"}
        ]
        merged = _merge_parameters(path_params, op_params)
        assert len(merged) == 2

    def test_empty_params(self) -> None:
        merged = _merge_parameters([], [])
        assert merged == []


# ---------------------------------------------------------------------------
# _extract_parameters
# ---------------------------------------------------------------------------


class TestExtractParameters:
    """Test parameter extraction."""

    def test_extracts_query_param(self) -> None:
        params = [
            {
                "name": "limit",
                "in": "query",
                "required": False,
                "description": "Max results",
                "schema": {"type": "integer", "format": "int32", "default": 20},
                "example": 10,
            }
        ]
        result = _extract_parameters(params)
        assert len(result) == 1
        p = result[0]
        assert p.name == "limit"
        assert p.location == ParameterLocation.QUERY
        assert p.required is False
        assert p.description == "Max results"
        assert p.schema_type == "integer"
        assert p.schema_format == "int32"
        assert p.default == 20
        assert p.example == 10

    def test_path_param_always_required(self) -> None:
        """Path parameters must be required=True regardless of what the spec says."""
        params = [
            {"name": "id", "in": "path", "required": False, "schema": {"type": "string"}}
        ]
        result = _extract_parameters(params)
        assert result[0].required is True

    def test_enum_values_extracted(self) -> None:
        params = [
            {
                "name": "status",
                "in": "query",
                "schema": {"type": "string", "enum": ["a", "b", "c"]},
            }
        ]
        result = _extract_parameters(params)
        assert result[0].enum_values == ["a", "b", "c"]

    def test_skips_unknown_location(self) -> None:
        params = [
            {"name": "x", "in": "unknown_location", "schema": {"type": "string"}}
        ]
        result = _extract_parameters(params)
        assert result == []

    def test_missing_schema(self) -> None:
        params = [{"name": "x", "in": "query"}]
        result = _extract_parameters(params)
        assert len(result) == 1
        assert result[0].schema_type == "string"  # default


# ---------------------------------------------------------------------------
# _extract_request_body
# ---------------------------------------------------------------------------


class TestExtractRequestBody:
    """Test request body extraction."""

    def test_extracts_request_body(self) -> None:
        body = {
            "required": True,
            "description": "Data to create",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                    }
                }
            },
        }
        result = _extract_request_body(body)
        assert result is not None
        assert result.required is True
        assert result.description == "Data to create"
        assert "application/json" in result.content_types
        assert result.schema_ is not None
        assert result.schema_["type"] == "object"

    def test_none_returns_none(self) -> None:
        assert _extract_request_body(None) is None

    def test_multiple_content_types(self) -> None:
        body = {
            "content": {
                "application/json": {"schema": {"type": "object"}},
                "application/xml": {"schema": {"type": "object"}},
            }
        }
        result = _extract_request_body(body)
        assert result is not None
        assert len(result.content_types) == 2
        assert "application/json" in result.content_types
        assert "application/xml" in result.content_types

    def test_no_schema_in_content(self) -> None:
        body = {"content": {"text/plain": {}}}
        result = _extract_request_body(body)
        assert result is not None
        assert result.schema_ is None


# ---------------------------------------------------------------------------
# _extract_responses
# ---------------------------------------------------------------------------


class TestExtractResponses:
    """Test response extraction."""

    def test_extracts_responses(self) -> None:
        responses = {
            "200": {
                "description": "Success",
                "content": {
                    "application/json": {
                        "schema": {"type": "object"}
                    }
                },
            },
            "404": {"description": "Not found"},
        }
        result = _extract_responses(responses)
        assert len(result) == 2

        r200 = next(r for r in result if r.status_code == "200")
        assert r200.description == "Success"
        assert "application/json" in r200.content_types
        assert r200.schema_ is not None

        r404 = next(r for r in result if r.status_code == "404")
        assert r404.description == "Not found"
        assert r404.content_types == []

    def test_empty_responses(self) -> None:
        assert _extract_responses({}) == []


# ---------------------------------------------------------------------------
# _extract_security_schemes
# ---------------------------------------------------------------------------


class TestExtractSecuritySchemes:
    """Test security scheme extraction."""

    def test_petstore_schemes(self) -> None:
        spec = _load_fixture("petstore_3.0.json")
        from specli.parser.resolver import resolve_refs
        resolved = resolve_refs(spec)
        schemes = _extract_security_schemes(resolved)
        assert len(schemes) == 2
        assert "apiKeyAuth" in schemes
        assert "bearerAuth" in schemes

    def test_complex_auth_all_schemes(self) -> None:
        spec = _load_fixture("complex_auth.json")
        from specli.parser.resolver import resolve_refs
        resolved = resolve_refs(spec)
        schemes = _extract_security_schemes(resolved)

        assert len(schemes) == 7

        # API Key in header
        api_header = schemes["apiKeyHeader"]
        assert api_header.type == "apiKey"
        assert api_header.param_name == "X-API-Key"
        assert api_header.location == "header"

        # API Key in query
        api_query = schemes["apiKeyQuery"]
        assert api_query.type == "apiKey"
        assert api_query.param_name == "api_key"
        assert api_query.location == "query"

        # Basic auth
        basic = schemes["basicAuth"]
        assert basic.type == "http"
        assert basic.scheme == "basic"

        # Bearer auth
        bearer = schemes["bearerAuth"]
        assert bearer.type == "http"
        assert bearer.scheme == "bearer"
        assert bearer.bearer_format == "JWT"

        # OAuth2 client credentials
        oauth_cc = schemes["oauth2ClientCreds"]
        assert oauth_cc.type == "oauth2"
        assert oauth_cc.flows is not None
        assert "clientCredentials" in oauth_cc.flows

        # OAuth2 authorization code
        oauth_ac = schemes["oauth2AuthCode"]
        assert oauth_ac.type == "oauth2"
        assert oauth_ac.flows is not None
        assert "authorizationCode" in oauth_ac.flows
        auth_code_flow = oauth_ac.flows["authorizationCode"]
        assert auth_code_flow["authorizationUrl"] == "https://auth.example.com/authorize"
        assert auth_code_flow["tokenUrl"] == "https://auth.example.com/token"

        # OpenID Connect
        oidc = schemes["openIdConnect"]
        assert oidc.type == "openIdConnect"
        assert oidc.openid_connect_url == "https://auth.example.com/.well-known/openid-configuration"

    def test_no_security_schemes(self) -> None:
        schemes = _extract_security_schemes({})
        assert schemes == {}

    def test_empty_components(self) -> None:
        schemes = _extract_security_schemes({"components": {}})
        assert schemes == {}


# ---------------------------------------------------------------------------
# Complex auth fixture full extraction
# ---------------------------------------------------------------------------


class TestComplexAuthFullExtraction:
    """Test full extraction from the complex auth fixture."""

    @pytest.fixture()
    def parsed(self) -> ParsedSpec:
        raw = _load_fixture("complex_auth.json")
        return extract_spec(raw, "3.0.3")

    def test_public_endpoint_no_auth(self, parsed: ParsedSpec) -> None:
        op = _find_operation(parsed, "GET", "/public")
        assert op is not None
        assert op.security == []

    def test_basic_auth_endpoint(self, parsed: ParsedSpec) -> None:
        op = _find_operation(parsed, "GET", "/basic-protected")
        assert op is not None
        assert len(op.security) == 1
        assert "basicAuth" in op.security[0]

    def test_multi_auth_or_logic(self, parsed: ParsedSpec) -> None:
        op = _find_operation(parsed, "GET", "/multi-auth")
        assert op is not None
        # 3 alternatives (OR logic)
        assert len(op.security) == 3

    def test_combined_auth_and_logic(self, parsed: ParsedSpec) -> None:
        op = _find_operation(parsed, "GET", "/combined-auth")
        assert op is not None
        # 1 requirement with 2 schemes (AND logic)
        assert len(op.security) == 1
        req = op.security[0]
        assert "apiKeyHeader" in req
        assert "bearerAuth" in req

    def test_oauth_scopes(self, parsed: ParsedSpec) -> None:
        op = _find_operation(parsed, "GET", "/oauth-protected")
        assert op is not None
        assert len(op.security) == 1
        assert op.security[0]["oauth2AuthCode"] == ["read", "write"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_operation(
    parsed: ParsedSpec, method: str, path: str
) -> APIOperation | None:
    """Find an operation by method and path."""
    for op in parsed.operations:
        if op.method.value == method.lower() and op.path == path:
            return op
    return None


def _find_param(op: APIOperation, name: str) -> Any:
    """Find a parameter by name within an operation."""
    for param in op.parameters:
        if param.name == name:
            return param
    return None


def _find_response(op: APIOperation, status_code: str) -> Any:
    """Find a response by status code within an operation."""
    for resp in op.responses:
        if resp.status_code == status_code:
            return resp
    return None
