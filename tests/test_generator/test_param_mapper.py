"""Tests for specli.generator.param_mapper.

Covers:
- OpenAPI type -> Python type mapping (string, integer, number, boolean, etc.)
- sanitize_param_name: kebab-case, dots, special chars, Python keywords
- map_parameter_to_typer for query params (Option), path params (Argument)
- map_parameter_to_typer with default values and enum values
- build_body_option
"""

from __future__ import annotations

from typing import Optional

import pytest
import typer

from specli.generator.param_mapper import (
    build_body_option,
    map_parameter_to_typer,
    openapi_type_to_python,
    sanitize_param_name,
)
from specli.models import APIParameter, ParameterLocation


# ------------------------------------------------------------------ #
# openapi_type_to_python
# ------------------------------------------------------------------ #


class TestOpenapiTypeToPython:
    """Test the OpenAPI type -> Python type mapping."""

    def test_string(self) -> None:
        assert openapi_type_to_python("string") is str

    def test_integer(self) -> None:
        assert openapi_type_to_python("integer") is int

    def test_number(self) -> None:
        assert openapi_type_to_python("number") is float

    def test_boolean(self) -> None:
        assert openapi_type_to_python("boolean") is bool

    def test_array(self) -> None:
        """Arrays are passed as JSON strings (Typer does not support list type)."""
        assert openapi_type_to_python("array") is str

    def test_object(self) -> None:
        """Objects are passed as JSON strings."""
        assert openapi_type_to_python("object") is str

    def test_string_binary_format(self) -> None:
        assert openapi_type_to_python("string", "binary") is bytes

    def test_string_byte_format(self) -> None:
        assert openapi_type_to_python("string", "byte") is bytes

    def test_string_datetime_format(self) -> None:
        """date-time has no special override; stays str."""
        assert openapi_type_to_python("string", "date-time") is str

    def test_integer_int32_format(self) -> None:
        assert openapi_type_to_python("integer", "int32") is int

    def test_integer_int64_format(self) -> None:
        assert openapi_type_to_python("integer", "int64") is int

    def test_number_float_format(self) -> None:
        assert openapi_type_to_python("number", "float") is float

    def test_number_double_format(self) -> None:
        assert openapi_type_to_python("number", "double") is float

    def test_unknown_type_defaults_to_str(self) -> None:
        assert openapi_type_to_python("custom_type") is str

    def test_unknown_format_ignored(self) -> None:
        """An unrecognised format for a known type falls back to the base."""
        assert openapi_type_to_python("string", "custom-format") is str


# ------------------------------------------------------------------ #
# sanitize_param_name
# ------------------------------------------------------------------ #


class TestSanitizeParamName:
    """Test parameter name sanitisation."""

    def test_snake_case_passthrough(self) -> None:
        assert sanitize_param_name("user_id") == "user_id"

    def test_kebab_case_to_snake(self) -> None:
        assert sanitize_param_name("user-id") == "user_id"

    def test_dots_to_underscores(self) -> None:
        assert sanitize_param_name("filter.name") == "filter_name"

    def test_special_chars_stripped(self) -> None:
        assert sanitize_param_name("my$param!") == "my_param"

    def test_leading_digit_prefixed(self) -> None:
        assert sanitize_param_name("3dview") == "_3dview"

    def test_python_keyword_suffixed(self) -> None:
        assert sanitize_param_name("class") == "class_"
        assert sanitize_param_name("return") == "return_"
        assert sanitize_param_name("import") == "import_"

    def test_multiple_hyphens(self) -> None:
        assert sanitize_param_name("X-Request-Id") == "x_request_id"

    def test_consecutive_separators_collapsed(self) -> None:
        assert sanitize_param_name("a--b..c") == "a_b_c"

    def test_empty_string_fallback(self) -> None:
        assert sanitize_param_name("") == "param"

    def test_all_special_chars_fallback(self) -> None:
        assert sanitize_param_name("$$$") == "param"

    def test_camel_case_to_snake(self) -> None:
        """CamelCase is converted to snake_case."""
        assert sanitize_param_name("camelCase") == "camel_case"
        assert sanitize_param_name("petId") == "pet_id"
        assert sanitize_param_name("XMLParser") == "xml_parser"

    def test_is_not_keyword(self) -> None:
        """Normal names should not get a trailing underscore."""
        result = sanitize_param_name("limit")
        assert result == "limit"
        assert not result.endswith("_")


# ------------------------------------------------------------------ #
# map_parameter_to_typer -- query params
# ------------------------------------------------------------------ #


class TestMapQueryParameter:
    """Test mapping of query parameters to Typer options."""

    def test_basic_query_param(self) -> None:
        param = APIParameter(
            name="limit",
            location=ParameterLocation.QUERY,
            required=False,
            description="Max results",
            schema_type="integer",
            schema_format="int32",
        )
        result = map_parameter_to_typer(param)
        assert result["name"] == "limit"
        assert result["original_name"] == "limit"
        assert result["is_argument"] is False
        assert result["location"] == ParameterLocation.QUERY
        assert result["help"] == "Max results"

    def test_required_query_param(self) -> None:
        param = APIParameter(
            name="api-key",
            location=ParameterLocation.QUERY,
            required=True,
            schema_type="string",
        )
        result = map_parameter_to_typer(param)
        assert result["name"] == "api_key"
        assert result["is_argument"] is False
        # Required options use ... (Ellipsis) as the Typer default marker.
        # The default is a typer.Option, but we verify the mapping is valid
        # by checking the name and type.
        assert result["type"] is str

    def test_optional_with_default(self) -> None:
        param = APIParameter(
            name="page",
            location=ParameterLocation.QUERY,
            required=False,
            schema_type="integer",
            default=1,
        )
        result = map_parameter_to_typer(param)
        assert result["name"] == "page"
        assert result["type"] is int

    def test_optional_without_default_uses_optional_type(self) -> None:
        param = APIParameter(
            name="tag",
            location=ParameterLocation.QUERY,
            required=False,
            schema_type="string",
        )
        result = map_parameter_to_typer(param)
        # Type should be Optional[str] when no default is provided.
        assert result["type"] is Optional[str]

    def test_enum_values_in_help(self) -> None:
        param = APIParameter(
            name="status",
            location=ParameterLocation.QUERY,
            required=False,
            description="Filter by status",
            schema_type="string",
            enum_values=["available", "pending", "sold"],
        )
        result = map_parameter_to_typer(param)
        assert "[choices:" in result["help"]
        assert "available" in result["help"]
        assert "pending" in result["help"]
        assert "sold" in result["help"]

    def test_enum_without_description(self) -> None:
        param = APIParameter(
            name="format",
            location=ParameterLocation.QUERY,
            schema_type="string",
            enum_values=["json", "xml"],
        )
        result = map_parameter_to_typer(param)
        assert result["help"].startswith("[choices:")


# ------------------------------------------------------------------ #
# map_parameter_to_typer -- path params
# ------------------------------------------------------------------ #


class TestMapPathParameter:
    """Test mapping of path parameters to Typer arguments."""

    def test_path_param_is_argument(self) -> None:
        param = APIParameter(
            name="petId",
            location=ParameterLocation.PATH,
            required=True,
            description="The ID of the pet",
            schema_type="string",
        )
        result = map_parameter_to_typer(param)
        assert result["is_argument"] is True
        assert result["name"] == "pet_id"  # CamelCase -> snake_case
        assert result["original_name"] == "petId"  # Original preserved
        assert result["type"] is str

    def test_path_param_integer_type(self) -> None:
        param = APIParameter(
            name="user_id",
            location=ParameterLocation.PATH,
            required=True,
            schema_type="integer",
        )
        result = map_parameter_to_typer(param)
        assert result["is_argument"] is True
        assert result["type"] is int


# ------------------------------------------------------------------ #
# map_parameter_to_typer -- header / cookie params
# ------------------------------------------------------------------ #


class TestMapHeaderCookieParameter:
    """Test mapping of header and cookie parameters."""

    def test_header_param(self) -> None:
        param = APIParameter(
            name="X-Request-Id",
            location=ParameterLocation.HEADER,
            required=False,
            schema_type="string",
        )
        result = map_parameter_to_typer(param)
        assert result["name"] == "x_request_id"
        assert result["is_argument"] is False
        assert result["location"] == ParameterLocation.HEADER

    def test_cookie_param(self) -> None:
        param = APIParameter(
            name="session_id",
            location=ParameterLocation.COOKIE,
            required=False,
            schema_type="string",
        )
        result = map_parameter_to_typer(param)
        assert result["is_argument"] is False
        assert result["location"] == ParameterLocation.COOKIE


# ------------------------------------------------------------------ #
# build_body_option
# ------------------------------------------------------------------ #


class TestBuildBodyOption:
    """Test the --body option builder."""

    def test_returns_dict(self) -> None:
        result = build_body_option()
        assert isinstance(result, dict)

    def test_name_is_body(self) -> None:
        result = build_body_option()
        assert result["name"] == "body"

    def test_is_not_argument(self) -> None:
        result = build_body_option()
        assert result["is_argument"] is False

    def test_type_is_optional_str(self) -> None:
        result = build_body_option()
        assert result["type"] is Optional[str]

    def test_help_mentions_json(self) -> None:
        result = build_body_option()
        assert "JSON" in result["help"]

    def test_help_mentions_file(self) -> None:
        result = build_body_option()
        assert "@" in result["help"] or "file" in result["help"].lower()

    def test_original_name_is_dunder(self) -> None:
        result = build_body_option()
        assert result["original_name"] == "__body__"

    def test_location_is_none(self) -> None:
        result = build_body_option()
        assert result["location"] is None
