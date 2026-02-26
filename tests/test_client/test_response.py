"""Tests for the response formatting bridge."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, call

import httpx
import pytest

from specli.client.response import extract_response_data, format_api_response
from specli.output import OutputManager, reset_output, set_output


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(
    status_code: int = 200,
    content: bytes | None = None,
    json_data: object | None = None,
    headers: dict[str, str] | None = None,
    text: str | None = None,
) -> httpx.Response:
    """Build a mock httpx.Response."""
    if headers is None:
        headers = {}
    if json_data is not None:
        headers.setdefault("content-type", "application/json")
        return httpx.Response(
            status_code=status_code,
            json=json_data,
            headers=headers,
            request=httpx.Request("GET", "https://api.example.com/test"),
        )
    if text is not None:
        headers.setdefault("content-type", "text/plain")
        return httpx.Response(
            status_code=status_code,
            text=text,
            headers=headers,
            request=httpx.Request("GET", "https://api.example.com/test"),
        )
    if content is not None:
        return httpx.Response(
            status_code=status_code,
            content=content,
            headers=headers,
            request=httpx.Request("GET", "https://api.example.com/test"),
        )
    # Empty body
    return httpx.Response(
        status_code=status_code,
        content=b"",
        headers=headers,
        request=httpx.Request("GET", "https://api.example.com/test"),
    )


@pytest.fixture(autouse=True)
def _clean_output():
    """Reset the global output manager between tests."""
    set_output(OutputManager(no_color=True, quiet=False))
    yield
    reset_output()


# ---------------------------------------------------------------------------
# format_api_response
# ---------------------------------------------------------------------------


class TestFormatApiResponse:
    def test_json_response_formatting(self) -> None:
        """JSON response body is passed to output.format_response."""
        mock_output = MagicMock(spec=OutputManager)
        set_output(mock_output)

        response = _make_response(
            status_code=200,
            json_data={"users": [{"id": 1, "name": "Alice"}]},
        )
        format_api_response(response)

        # info called with status line
        mock_output.info.assert_called()
        status_call = mock_output.info.call_args_list[0]
        assert "200" in status_call.args[0]

        # format_response called with the parsed data
        mock_output.format_response.assert_called_once()
        data_arg = mock_output.format_response.call_args.args[0]
        assert data_arg == {"users": [{"id": 1, "name": "Alice"}]}

    def test_plain_text_response_formatting(self) -> None:
        """Non-JSON response falls back to text."""
        mock_output = MagicMock(spec=OutputManager)
        set_output(mock_output)

        response = _make_response(
            status_code=200,
            text="Hello, plain text response",
            headers={"content-type": "text/plain"},
        )
        format_api_response(response)

        mock_output.format_response.assert_called_once()
        data_arg = mock_output.format_response.call_args.args[0]
        assert data_arg == "Hello, plain text response"

        content_type = mock_output.format_response.call_args.args[1]
        assert content_type == "text/plain"

    def test_empty_response_body(self) -> None:
        """Empty response body should not call format_response."""
        mock_output = MagicMock(spec=OutputManager)
        set_output(mock_output)

        response = _make_response(status_code=204, content=b"")
        format_api_response(response)

        # info is called for the status line
        mock_output.info.assert_called()
        # format_response is NOT called for empty body
        mock_output.format_response.assert_not_called()

    def test_status_line_includes_status_code(self) -> None:
        mock_output = MagicMock(spec=OutputManager)
        set_output(mock_output)

        response = _make_response(status_code=201, json_data={"id": 42})
        format_api_response(response)

        status_call = mock_output.info.call_args_list[0]
        assert "201" in status_call.args[0]

    def test_content_type_passed_from_headers(self) -> None:
        """Content type should be read from the response headers."""
        mock_output = MagicMock(spec=OutputManager)
        set_output(mock_output)

        response = _make_response(
            status_code=200,
            text="<html></html>",
            headers={"content-type": "text/html"},
        )
        format_api_response(response)

        mock_output.format_response.assert_called_once()
        content_type = mock_output.format_response.call_args.args[1]
        assert content_type == "text/html"

    def test_default_content_type_is_json(self) -> None:
        """When no content-type header, default to application/json."""
        mock_output = MagicMock(spec=OutputManager)
        set_output(mock_output)

        response = _make_response(
            status_code=200,
            content=b'{"key": "value"}',
            headers={},
        )
        format_api_response(response)

        mock_output.format_response.assert_called_once()
        content_type = mock_output.format_response.call_args.args[1]
        assert content_type == "application/json"


# ---------------------------------------------------------------------------
# extract_response_data
# ---------------------------------------------------------------------------


class TestExtractResponseData:
    def test_json_parse(self) -> None:
        response = _make_response(
            status_code=200,
            json_data={"key": "value", "nested": {"a": 1}},
        )
        data = extract_response_data(response)
        assert data == {"key": "value", "nested": {"a": 1}}

    def test_json_list_parse(self) -> None:
        response = _make_response(
            status_code=200,
            json_data=[1, 2, 3],
        )
        data = extract_response_data(response)
        assert data == [1, 2, 3]

    def test_fallback_to_text(self) -> None:
        response = _make_response(
            status_code=200,
            text="This is not JSON",
        )
        data = extract_response_data(response)
        assert data == "This is not JSON"

    def test_empty_body_returns_none(self) -> None:
        response = _make_response(status_code=204, content=b"")
        data = extract_response_data(response)
        assert data is None

    def test_json_string_in_bytes(self) -> None:
        """Content that is valid JSON but provided as bytes."""
        response = _make_response(
            status_code=200,
            content=b'{"parsed": true}',
            headers={"content-type": "application/json"},
        )
        data = extract_response_data(response)
        assert data == {"parsed": True}

    def test_malformed_json_falls_back_to_text(self) -> None:
        response = _make_response(
            status_code=200,
            content=b'{"broken": json',
            headers={"content-type": "application/json"},
        )
        data = extract_response_data(response)
        assert isinstance(data, str)
        assert '{"broken": json' in data
