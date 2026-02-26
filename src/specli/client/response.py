"""Response formatting bridge -- maps :class:`httpx.Response` to the output system.

This module bridges the HTTP client layer and the output/formatting layer.
After an HTTP call completes, :func:`format_api_response` routes the
response body through :meth:`~specli.output.OutputManager.format_response`
(which applies ``--output`` formatting like JSON, table, CSV, etc.) while
emitting the status line to stderr.

See Also:
    :mod:`specli.output` -- the output manager that renders data.
"""

from __future__ import annotations

from typing import Any

import httpx

from specli.output import get_output


def format_api_response(response: httpx.Response) -> None:
    """Format and print an API response using the global output system.

    Writes the HTTP status line (e.g. ``HTTP 200 OK``) to stderr via
    :meth:`~specli.output.OutputManager.info`, then extracts the
    response body and renders it to stdout via
    :meth:`~specli.output.OutputManager.format_response`.

    Args:
        response: The :class:`httpx.Response` to format and display.
    """
    output = get_output()

    # Status line to stderr
    output.info(f"HTTP {response.status_code} {response.reason_phrase or ''}")

    # Determine content type from headers
    content_type = response.headers.get("content-type", "application/json")

    # Extract data and format to stdout
    data = extract_response_data(response)
    if data is not None:
        output.format_response(data, content_type)


def extract_response_data(response: httpx.Response) -> Any:
    """Extract the body from an HTTP response.

    Attempts to parse the body as JSON first.  If that fails (e.g. the
    response is HTML or plain text), returns the raw text.  Returns
    ``None`` for responses with no content.

    Args:
        response: The :class:`httpx.Response` to extract data from.

    Returns:
        A JSON-decoded object (``dict``, ``list``, etc.), a ``str`` of raw
        text, or ``None`` if the body is empty.
    """
    # Handle empty body
    if not response.content:
        return None

    # Try JSON first
    try:
        return response.json()
    except Exception:
        pass

    # Fall back to text
    return response.text
