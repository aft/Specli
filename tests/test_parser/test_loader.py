"""Tests for specli.parser.loader."""

from __future__ import annotations

import io
import json
import textwrap
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from specli.exceptions import SpecParseError
from specli.parser.loader import (
    _load_from_file,
    _load_from_stdin,
    _load_from_url,
    _parse_content,
    load_spec,
    validate_openapi_version,
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


# ---------------------------------------------------------------------------
# load_spec dispatch
# ---------------------------------------------------------------------------


class TestLoadSpec:
    """Test load_spec dispatcher routes to the correct loader."""

    def test_loads_from_file_json(self) -> None:
        result = load_spec(str(FIXTURES_DIR / "petstore_3.0.json"))
        assert result["openapi"] == "3.0.3"
        assert result["info"]["title"] == "Petstore API"

    def test_loads_from_file_yaml(self, tmp_path: Path) -> None:
        yaml_content = textwrap.dedent("""\
            openapi: "3.0.3"
            info:
              title: YAML Test
              version: "1.0.0"
            paths: {}
        """)
        yaml_file = tmp_path / "spec.yaml"
        yaml_file.write_text(yaml_content, encoding="utf-8")
        result = load_spec(str(yaml_file))
        assert result["openapi"] == "3.0.3"
        assert result["info"]["title"] == "YAML Test"

    def test_loads_from_yml_extension(self, tmp_path: Path) -> None:
        yaml_content = textwrap.dedent("""\
            openapi: "3.1.0"
            info:
              title: YML Extension
              version: "2.0.0"
            paths: {}
        """)
        yml_file = tmp_path / "spec.yml"
        yml_file.write_text(yaml_content, encoding="utf-8")
        result = load_spec(str(yml_file))
        assert result["openapi"] == "3.1.0"

    def test_loads_from_stdin(self) -> None:
        spec_json = json.dumps({"openapi": "3.0.3", "info": {"title": "stdin test", "version": "1.0"}})
        with patch("specli.parser.loader.sys") as mock_sys:
            mock_sys.stdin = io.StringIO(spec_json)
            result = load_spec("-")
        assert result["info"]["title"] == "stdin test"

    def test_loads_from_url(self) -> None:
        spec = {"openapi": "3.0.3", "info": {"title": "URL test", "version": "1.0"}}
        mock_response = httpx.Response(
            status_code=200,
            json=spec,
            request=httpx.Request("GET", "https://example.com/spec.json"),
        )
        with patch("specli.parser.loader.httpx.get", return_value=mock_response):
            result = load_spec("https://example.com/spec.json")
        assert result["info"]["title"] == "URL test"


# ---------------------------------------------------------------------------
# _load_from_file
# ---------------------------------------------------------------------------


class TestLoadFromFile:
    """Test loading specs from local files."""

    def test_load_json_file(self) -> None:
        result = _load_from_file(str(FIXTURES_DIR / "petstore_3.0.json"))
        assert isinstance(result, dict)
        assert "paths" in result

    def test_load_yaml_file(self, tmp_path: Path) -> None:
        content = textwrap.dedent("""\
            openapi: "3.0.0"
            info:
              title: Test
              version: "1.0"
            paths:
              /hello:
                get:
                  summary: Hello
                  responses:
                    "200":
                      description: OK
        """)
        yaml_file = tmp_path / "test.yaml"
        yaml_file.write_text(content, encoding="utf-8")
        result = _load_from_file(str(yaml_file))
        assert result["paths"]["/hello"]["get"]["summary"] == "Hello"

    def test_file_not_found_raises(self) -> None:
        with pytest.raises(SpecParseError, match="not found"):
            _load_from_file("/nonexistent/path/to/spec.json")

    def test_empty_file_raises(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty.json"
        empty.write_text("", encoding="utf-8")
        with pytest.raises(SpecParseError, match="empty"):
            _load_from_file(str(empty))

    def test_invalid_json_file_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("{invalid json", encoding="utf-8")
        with pytest.raises(SpecParseError, match="Invalid JSON"):
            _load_from_file(str(bad))

    def test_non_object_json_raises(self, tmp_path: Path) -> None:
        array_file = tmp_path / "array.json"
        array_file.write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(SpecParseError, match="must be a JSON/YAML object"):
            _load_from_file(str(array_file))


# ---------------------------------------------------------------------------
# _load_from_stdin
# ---------------------------------------------------------------------------


class TestLoadFromStdin:
    """Test loading specs from stdin."""

    def test_reads_json_from_stdin(self) -> None:
        spec_json = json.dumps({"openapi": "3.0.0", "info": {"title": "T", "version": "1"}})
        with patch("specli.parser.loader.sys") as mock_sys:
            mock_sys.stdin = io.StringIO(spec_json)
            result = _load_from_stdin()
        assert result["openapi"] == "3.0.0"

    def test_reads_yaml_from_stdin(self) -> None:
        yaml_content = textwrap.dedent("""\
            openapi: "3.0.0"
            info:
              title: YAML stdin
              version: "1.0"
        """)
        with patch("specli.parser.loader.sys") as mock_sys:
            mock_sys.stdin = io.StringIO(yaml_content)
            result = _load_from_stdin()
        assert result["info"]["title"] == "YAML stdin"

    def test_empty_stdin_raises(self) -> None:
        with patch("specli.parser.loader.sys") as mock_sys:
            mock_sys.stdin = io.StringIO("")
            with pytest.raises(SpecParseError, match="No input"):
                _load_from_stdin()

    def test_whitespace_only_stdin_raises(self) -> None:
        with patch("specli.parser.loader.sys") as mock_sys:
            mock_sys.stdin = io.StringIO("   \n\t\n  ")
            with pytest.raises(SpecParseError, match="No input"):
                _load_from_stdin()


# ---------------------------------------------------------------------------
# _load_from_url
# ---------------------------------------------------------------------------


class TestLoadFromUrl:
    """Test loading specs from URLs."""

    def test_loads_json_from_url(self) -> None:
        spec = {"openapi": "3.0.3", "info": {"title": "Remote", "version": "1.0"}}
        mock_response = httpx.Response(
            status_code=200,
            json=spec,
            request=httpx.Request("GET", "https://example.com/spec.json"),
        )
        with patch("specli.parser.loader.httpx.get", return_value=mock_response):
            result = _load_from_url("https://example.com/spec.json")
        assert result["info"]["title"] == "Remote"

    def test_loads_yaml_from_url(self) -> None:
        yaml_body = textwrap.dedent("""\
            openapi: "3.0.0"
            info:
              title: YAML Remote
              version: "1.0"
        """)
        mock_response = httpx.Response(
            status_code=200,
            text=yaml_body,
            headers={"content-type": "application/x-yaml"},
            request=httpx.Request("GET", "https://example.com/spec.yaml"),
        )
        with patch("specli.parser.loader.httpx.get", return_value=mock_response):
            result = _load_from_url("https://example.com/spec.yaml")
        assert result["info"]["title"] == "YAML Remote"

    def test_http_error_raises(self) -> None:
        mock_response = httpx.Response(
            status_code=404,
            request=httpx.Request("GET", "https://example.com/missing.json"),
        )
        with patch("specli.parser.loader.httpx.get", return_value=mock_response):
            with pytest.raises(SpecParseError, match="HTTP 404"):
                _load_from_url("https://example.com/missing.json")

    def test_connection_error_raises(self) -> None:
        with patch(
            "specli.parser.loader.httpx.get",
            side_effect=httpx.ConnectError("Connection refused"),
        ):
            with pytest.raises(SpecParseError, match="Failed to fetch"):
                _load_from_url("https://unreachable.example.com/spec.json")


# ---------------------------------------------------------------------------
# _parse_content
# ---------------------------------------------------------------------------


class TestParseContent:
    """Test content parsing with format detection."""

    def test_parses_json(self) -> None:
        result = _parse_content('{"key": "value"}')
        assert result == {"key": "value"}

    def test_parses_yaml(self) -> None:
        result = _parse_content("key: value\nnested:\n  a: 1")
        assert result == {"key": "value", "nested": {"a": 1}}

    def test_json_hint_forces_json_only(self) -> None:
        with pytest.raises(SpecParseError, match="Invalid JSON"):
            _parse_content("not: valid: json: {{{", hint="json")

    def test_yaml_hint_skips_json(self) -> None:
        # This is valid YAML but not valid JSON
        result = _parse_content("key: value", hint="yaml")
        assert result == {"key": "value"}

    def test_invalid_content_raises(self) -> None:
        with pytest.raises(SpecParseError, match="Failed to parse"):
            _parse_content("}{not valid at all][", hint="")

    def test_non_dict_content_raises(self) -> None:
        with pytest.raises(SpecParseError, match="must be a JSON/YAML object"):
            _parse_content('"just a string"')

    def test_null_yaml_raises(self) -> None:
        with pytest.raises(SpecParseError, match="must be a JSON/YAML object"):
            _parse_content("---\n", hint="yaml")


# ---------------------------------------------------------------------------
# validate_openapi_version
# ---------------------------------------------------------------------------


class TestValidateOpenAPIVersion:
    """Test OpenAPI version validation."""

    def test_accepts_3_0_0(self) -> None:
        assert validate_openapi_version({"openapi": "3.0.0"}) == "3.0.0"

    def test_accepts_3_0_3(self) -> None:
        assert validate_openapi_version({"openapi": "3.0.3"}) == "3.0.3"

    def test_accepts_3_1_0(self) -> None:
        assert validate_openapi_version({"openapi": "3.1.0"}) == "3.1.0"

    def test_accepts_3_1_1(self) -> None:
        assert validate_openapi_version({"openapi": "3.1.1"}) == "3.1.1"

    def test_rejects_swagger_2_0(self) -> None:
        with pytest.raises(SpecParseError, match="Swagger 2.0.*not supported"):
            validate_openapi_version({"swagger": "2.0"})

    def test_rejects_missing_openapi_field(self) -> None:
        with pytest.raises(SpecParseError, match="Missing 'openapi' field"):
            validate_openapi_version({"info": {"title": "test"}})

    def test_rejects_unsupported_version(self) -> None:
        with pytest.raises(SpecParseError, match="Unsupported OpenAPI version"):
            validate_openapi_version({"openapi": "4.0.0"})

    def test_rejects_version_2_0_without_swagger_key(self) -> None:
        with pytest.raises(SpecParseError, match="Unsupported OpenAPI version"):
            validate_openapi_version({"openapi": "2.0.0"})

    def test_accepts_future_3_2(self) -> None:
        # 3.2.x should be accepted (future-compatible)
        assert validate_openapi_version({"openapi": "3.2.0"}) == "3.2.0"

    def test_version_as_number(self) -> None:
        # Some specs may have version as a float -- should still work
        result = validate_openapi_version({"openapi": 3.0})
        assert result == "3.0"
