"""Export and import CLI help strings for editing, translation, or LLM context.

This module provides a round-trip workflow for user-visible strings
(summaries, descriptions, parameter docs) in an OpenAPI spec:

1. **Export** (:func:`export_strings` / :func:`export_strings_to_file`)
   extracts every string into a structured dict (or JSON file) keyed by
   ``"METHOD /path"``, with additional sections for ``info`` and ``tags``.

2. **Import** (:func:`import_strings` / :func:`import_strings_from_file`)
   reads strings back and force-applies them to the spec dict,
   unconditionally overriding all other sources.

This is the **highest-priority** layer in the enrichment pipeline::

    raw OpenAPI spec --> source enrichment --> imported strings (wins)

Typical use-cases include:

* Manual editing of auto-generated descriptions.
* Translation of CLI help text to other languages.
* Bulk LLM-assisted rewriting of help strings.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def export_strings(raw_spec: dict) -> dict[str, Any]:
    """Extract all user-visible strings from *raw_spec* into a dict.

    The returned dict has three top-level keys:

    - ``info`` -- API title and description.
    - ``tags`` -- Tag name to description mapping.
    - ``operations`` -- ``"METHOD /path"`` to operation strings.

    Each operation entry contains ``summary``, ``description``, and
    ``parameters`` (a mapping of parameter name to description).

    Args:
        raw_spec: Raw OpenAPI spec dict to extract strings from.

    Returns:
        A structured dict suitable for serialisation to JSON.
    """
    result: dict[str, Any] = {
        "info": _export_info(raw_spec),
        "tags": _export_tags(raw_spec),
        "operations": _export_operations(raw_spec),
    }
    return result


def export_strings_to_file(raw_spec: dict, output_path: str) -> int:
    """Export strings to a JSON file.

    Calls :func:`export_strings` and writes the result as indented,
    UTF-8 encoded JSON.

    Args:
        raw_spec: Raw OpenAPI spec dict to extract strings from.
        output_path: Filesystem path for the output JSON file.

    Returns:
        The number of operations exported.
    """
    data = export_strings(raw_spec)
    Path(output_path).write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return len(data.get("operations", {}))


def import_strings(raw_spec: dict, strings: dict[str, Any]) -> None:
    """Apply *strings* to *raw_spec* in place, overriding all existing values.

    This is the highest-priority layer in the enrichment pipeline:
    anything present in the *strings* dict unconditionally replaces
    whatever is currently in the spec. Only non-empty string values
    are applied; empty strings are skipped.

    Args:
        raw_spec: Raw OpenAPI spec dict (mutated in place).
        strings: Structured string dict as produced by
            :func:`export_strings`, with ``info``, ``tags``, and
            ``operations`` keys.
    """
    _import_info(raw_spec, strings.get("info", {}))
    _import_tags(raw_spec, strings.get("tags", {}))
    _import_operations(raw_spec, strings.get("operations", {}))


def import_strings_from_file(raw_spec: dict, input_path: str) -> int:
    """Import strings from a JSON file.

    Reads a JSON file produced by :func:`export_strings_to_file` and
    applies it to *raw_spec* via :func:`import_strings`.

    Args:
        raw_spec: Raw OpenAPI spec dict (mutated in place).
        input_path: Filesystem path to the JSON strings file.

    Returns:
        The number of operations imported.

    Raises:
        FileNotFoundError: If *input_path* does not exist.
        json.JSONDecodeError: If the file is not valid JSON.
    """
    data = json.loads(Path(input_path).read_text(encoding="utf-8"))
    import_strings(raw_spec, data)
    return len(data.get("operations", {}))


# ---------------------------------------------------------------------------
# Export helpers
# ---------------------------------------------------------------------------


def _export_info(raw_spec: dict) -> dict[str, str]:
    info = raw_spec.get("info", {})
    return {
        "title": info.get("title", ""),
        "description": info.get("description", ""),
    }


def _export_tags(raw_spec: dict) -> dict[str, str]:
    tags = raw_spec.get("tags", [])
    result: dict[str, str] = {}
    for tag in tags:
        if isinstance(tag, dict) and tag.get("name"):
            result[tag["name"]] = tag.get("description", "")
    return result


def _export_operations(raw_spec: dict) -> dict[str, dict[str, Any]]:
    paths = raw_spec.get("paths", {})
    result: dict[str, dict[str, Any]] = {}

    for path, methods in sorted(paths.items()):
        if not isinstance(methods, dict):
            continue
        for method in ("get", "post", "put", "patch", "delete", "head", "options"):
            operation = methods.get(method)
            if not isinstance(operation, dict):
                continue

            key = f"{method.upper()} {path}"
            entry: dict[str, Any] = {
                "summary": operation.get("summary", ""),
                "description": operation.get("description", ""),
                "parameters": _export_param_descriptions(operation),
            }
            result[key] = entry

    return result


def _export_param_descriptions(operation: dict) -> dict[str, str]:
    """Collect parameter descriptions from query/path/header params and request body properties.

    Args:
        operation: An operation dict from the spec.

    Returns:
        Mapping of parameter/property name to its description string.
    """
    docs: dict[str, str] = {}

    # Query/path/header parameters.
    for param in operation.get("parameters", []):
        if isinstance(param, dict) and param.get("name"):
            docs[param["name"]] = param.get("description", "")

    # Request body schema properties.
    request_body = operation.get("requestBody", {})
    if isinstance(request_body, dict):
        for _ct, media_obj in request_body.get("content", {}).items():
            if not isinstance(media_obj, dict):
                continue
            schema = media_obj.get("schema", {})
            if isinstance(schema, dict):
                _collect_schema_descriptions(schema, docs)

    return docs


def _collect_schema_descriptions(schema: dict, docs: dict[str, str]) -> None:
    """Recursively collect property descriptions from a JSON Schema."""
    for prop_name, prop_schema in schema.get("properties", {}).items():
        if isinstance(prop_schema, dict):
            docs[prop_name] = prop_schema.get("description", "")


# ---------------------------------------------------------------------------
# Import helpers
# ---------------------------------------------------------------------------


def _import_info(raw_spec: dict, info_strings: dict[str, str]) -> None:
    if not info_strings:
        return
    info = raw_spec.setdefault("info", {})
    if "title" in info_strings and info_strings["title"]:
        info["title"] = info_strings["title"]
    if "description" in info_strings and info_strings["description"]:
        info["description"] = info_strings["description"]


def _import_tags(raw_spec: dict, tag_strings: dict[str, str]) -> None:
    if not tag_strings:
        return

    tags = raw_spec.setdefault("tags", [])
    existing: dict[str, dict] = {}
    for tag in tags:
        if isinstance(tag, dict) and tag.get("name"):
            existing[tag["name"]] = tag

    for name, description in tag_strings.items():
        if not description:
            continue
        if name in existing:
            existing[name]["description"] = description
        else:
            tags.append({"name": name, "description": description})


def _import_operations(raw_spec: dict, op_strings: dict[str, dict[str, Any]]) -> None:
    if not op_strings:
        return

    paths = raw_spec.get("paths", {})

    for key, strings in op_strings.items():
        parts = key.split(" ", 1)
        if len(parts) != 2:
            continue
        method, path = parts[0].lower(), parts[1]

        methods = paths.get(path)
        if not isinstance(methods, dict):
            continue
        operation = methods.get(method)
        if not isinstance(operation, dict):
            continue

        if strings.get("summary"):
            operation["summary"] = strings["summary"]
        if strings.get("description"):
            operation["description"] = strings["description"]

        param_strings = strings.get("parameters", {})
        if param_strings:
            _import_param_descriptions(operation, param_strings)

        # Inject synthetic requestBody from body_schema when the spec has none.
        body_schema = strings.get("body_schema")
        if body_schema and not operation.get("requestBody"):
            properties = body_schema.get("properties", {})
            required_fields = body_schema.get("required_fields", [])
            operation["requestBody"] = {
                "required": bool(required_fields),
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": properties,
                            "required": required_fields,
                        }
                    }
                },
            }


def _import_param_descriptions(operation: dict, param_strings: dict[str, str]) -> None:
    """Apply parameter descriptions to an operation, overriding existing values.

    Updates both top-level ``parameters`` entries and ``requestBody``
    schema properties.

    Args:
        operation: An operation dict from the spec (mutated in place).
        param_strings: Mapping of parameter name to new description.
    """
    # Query/path/header parameters.
    for param in operation.get("parameters", []):
        if not isinstance(param, dict):
            continue
        name = param.get("name", "")
        if name in param_strings and param_strings[name]:
            param["description"] = param_strings[name]

    # Request body schema properties.
    request_body = operation.get("requestBody", {})
    if not isinstance(request_body, dict):
        return

    for _ct, media_obj in request_body.get("content", {}).items():
        if not isinstance(media_obj, dict):
            continue
        schema = media_obj.get("schema", {})
        if isinstance(schema, dict):
            _import_schema_descriptions(schema, param_strings)


def _import_schema_descriptions(schema: dict, param_strings: dict[str, str]) -> None:
    """Recursively apply property descriptions to a JSON Schema.

    Args:
        schema: A JSON Schema dict containing a ``properties`` mapping.
        param_strings: Mapping of property name to new description.
    """
    for prop_name, prop_schema in schema.get("properties", {}).items():
        if isinstance(prop_schema, dict) and prop_name in param_strings and param_strings[prop_name]:
            prop_schema["description"] = param_strings[prop_name]
