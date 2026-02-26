"""Load OpenAPI specifications from a URL, local file, or stdin.

This module handles all I/O for fetching raw OpenAPI documents and converting
them into Python dictionaries.  It supports both JSON and YAML formats with
automatic format detection, and validates that the document declares a
supported OpenAPI version (3.0.x or 3.1.x).

The two public functions are:

* :func:`load_spec` -- Load and parse a spec from any supported source.
* :func:`validate_openapi_version` -- Check and return the ``openapi`` version
  string, rejecting Swagger 2.x and unsupported versions.

After loading, the raw dict should be passed to
:func:`~specli.parser.extractor.extract_spec` which resolves ``$ref``
pointers and extracts a :class:`~specli.models.ParsedSpec`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import httpx
import yaml

from specli.exceptions import SpecParseError


def load_spec(source: str) -> dict[str, Any]:
    """Load an OpenAPI spec from URL, file path, or stdin ('-').

    Supports JSON and YAML formats.
    Auto-detects format from content/extension.

    Args:
        source: A URL (http/https), file path, or '-' for stdin.

    Returns:
        The parsed spec as a dictionary.

    Raises:
        SpecParseError: If the source cannot be loaded or parsed.
    """
    if source == "-":
        return _load_from_stdin()
    elif source.startswith(("http://", "https://")):
        return _load_from_url(source)
    else:
        return _load_from_file(source)


def _load_from_stdin() -> dict[str, Any]:
    """Read spec from stdin.

    Reads all available input and attempts to parse as JSON, then YAML.

    Returns:
        The parsed spec dictionary.

    Raises:
        SpecParseError: If stdin is empty or content cannot be parsed.
    """
    try:
        content = sys.stdin.read()
    except Exception as exc:
        raise SpecParseError(f"Failed to read from stdin: {exc}") from exc

    if not content.strip():
        raise SpecParseError("No input received from stdin")

    return _parse_content(content, hint="stdin")


def _load_from_url(url: str) -> dict[str, Any]:
    """Fetch spec from URL. Supports JSON and YAML responses.

    Args:
        url: The HTTP(S) URL to fetch.

    Returns:
        The parsed spec dictionary.

    Raises:
        SpecParseError: If the URL cannot be fetched or content cannot be parsed.
    """
    try:
        response = httpx.get(url, timeout=30.0, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise SpecParseError(
            f"HTTP {exc.response.status_code} fetching spec from {url}"
        ) from exc
    except httpx.RequestError as exc:
        raise SpecParseError(f"Failed to fetch spec from {url}: {exc}") from exc

    content = response.text
    # Use content-type as a hint for parsing
    content_type = response.headers.get("content-type", "")
    hint = ""
    if "json" in content_type:
        hint = "json"
    elif "yaml" in content_type or "yml" in content_type:
        hint = "yaml"

    return _parse_content(content, hint=hint)


def _load_from_file(path: str) -> dict[str, Any]:
    """Load spec from local file.

    Supports .json, .yaml, and .yml extensions. Falls back to content-based
    detection if the extension is not recognized.

    Args:
        path: Path to the local file.

    Returns:
        The parsed spec dictionary.

    Raises:
        SpecParseError: If the file cannot be read or content cannot be parsed.
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise SpecParseError(f"Spec file not found: {path}")

    try:
        content = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SpecParseError(f"Failed to read spec file {path}: {exc}") from exc

    if not content.strip():
        raise SpecParseError(f"Spec file is empty: {path}")

    # Determine hint from file extension
    suffix = file_path.suffix.lower()
    hint = ""
    if suffix == ".json":
        hint = "json"
    elif suffix in (".yaml", ".yml"):
        hint = "yaml"

    return _parse_content(content, hint=hint)


def _parse_content(content: str, hint: str = "") -> dict[str, Any]:
    """Parse content as JSON or YAML.

    Tries JSON first (unless hint is 'yaml'), then falls back to YAML.
    This order is chosen because valid JSON is also valid YAML, but JSON
    parsing is stricter and faster.

    Args:
        content: The raw string content.
        hint: Optional format hint ('json' or 'yaml').

    Returns:
        The parsed dictionary.

    Raises:
        SpecParseError: If the content cannot be parsed as either format.
    """
    json_error: Exception | None = None
    yaml_error: Exception | None = None

    # Try JSON first unless explicitly hinted as YAML
    if hint != "yaml":
        try:
            result = json.loads(content)
            if not isinstance(result, dict):
                raise SpecParseError(
                    "Spec must be a JSON/YAML object (got "
                    f"{type(result).__name__})"
                )
            return result
        except json.JSONDecodeError as exc:
            json_error = exc
            # If the hint was explicitly JSON, don't try YAML
            if hint == "json":
                raise SpecParseError(f"Invalid JSON: {exc}") from exc

    # Try YAML
    try:
        result = yaml.safe_load(content)
        if not isinstance(result, dict):
            raise SpecParseError(
                "Spec must be a JSON/YAML object (got "
                f"{type(result).__name__ if result is not None else 'empty document'})"
            )
        return result
    except yaml.YAMLError as exc:
        yaml_error = exc

    # Both failed
    msg = "Failed to parse spec as JSON or YAML"
    if json_error:
        msg += f"\n  JSON error: {json_error}"
    if yaml_error:
        msg += f"\n  YAML error: {yaml_error}"
    raise SpecParseError(msg)


def validate_openapi_version(spec: dict[str, Any]) -> str:
    """Validate and return the OpenAPI version string.

    Supports OpenAPI 3.0.x and 3.1.x. Raises SpecParseError for Swagger 2.x,
    missing version fields, or unsupported versions.

    Args:
        spec: The parsed spec dictionary.

    Returns:
        The OpenAPI version string (e.g., '3.0.3', '3.1.0').

    Raises:
        SpecParseError: If the version is missing, unsupported, or indicates Swagger 2.x.
    """
    # Check for Swagger 2.x first
    if "swagger" in spec:
        swagger_ver = str(spec["swagger"])
        raise SpecParseError(
            f"Swagger {swagger_ver} is not supported. "
            "Only OpenAPI 3.0.x and 3.1.x are supported. "
            "Consider converting with https://converter.swagger.io"
        )

    openapi_version = spec.get("openapi")
    if openapi_version is None:
        raise SpecParseError(
            "Missing 'openapi' field. Is this an OpenAPI 3.x document?"
        )

    version_str = str(openapi_version)

    if version_str.startswith("3.0.") or version_str.startswith("3.1."):
        return version_str

    if version_str.startswith("3."):
        # Future 3.x versions -- allow with a warning-compatible return
        return version_str

    raise SpecParseError(
        f"Unsupported OpenAPI version: {version_str}. "
        "Only OpenAPI 3.0.x and 3.1.x are supported."
    )
