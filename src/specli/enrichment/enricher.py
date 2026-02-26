"""Patch a raw OpenAPI spec dict with source-extracted documentation.

This module applies :class:`~specli.enrichment.scanner.RouteDoc`
objects to a raw OpenAPI spec dictionary, filling in missing or
auto-generated summaries, descriptions, and parameter documentation.

**Enrichment rule:** source-extracted data fills gaps but never
overwrites existing spec content that is already substantive. A summary
is considered "thin" if it is missing, very short (< 15 characters), or
appears to be derived from the ``operationId``.

The module also propagates module-level docstrings to top-level tag
descriptions when tags lack their own description.
"""

from __future__ import annotations

import re

from specli.enrichment.scanner import RouteDoc


def enrich_raw_spec(
    raw_spec: dict,
    route_docs: list[RouteDoc],
) -> None:
    """Patch *raw_spec* in place with documentation from *route_docs*.

    Builds a lookup from ``(method, normalised_path)`` to
    :class:`~specli.enrichment.scanner.RouteDoc`, then iterates
    over every operation in ``raw_spec["paths"]``, enriching summaries,
    descriptions, and parameter docs where gaps exist. Also collects
    module-level docstrings and uses them to populate top-level tag
    descriptions via :func:`_enrich_tags`.

    Args:
        raw_spec: Raw OpenAPI spec dict (mutated in place).
        route_docs: Route documentation extracted by
            :class:`~specli.enrichment.scanner.SourceScanner`.
    """
    # Build lookup: (method, normalised_path) → RouteDoc
    lookup: dict[tuple[str, str], RouteDoc] = {}
    for doc in route_docs:
        key = (doc.method.lower(), _normalise_path(doc.path))
        lookup[key] = doc

    paths = raw_spec.get("paths", {})

    # Also collect module docs for tag descriptions.
    module_docs_by_tag: dict[str, str] = {}

    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue

        norm_path = _normalise_path(path)

        for method, operation in methods.items():
            if method.startswith("x-") or not isinstance(operation, dict):
                continue

            key = (method.lower(), norm_path)
            doc = lookup.get(key)
            if doc is None:
                continue

            _enrich_operation(operation, doc)

            # Collect module docs for potential tag enrichment.
            if doc.module_doc:
                tags = operation.get("tags", [])
                for tag in tags:
                    if tag not in module_docs_by_tag:
                        module_docs_by_tag[tag] = doc.module_doc

    # Enrich top-level tag descriptions from module docstrings.
    if module_docs_by_tag:
        _enrich_tags(raw_spec, module_docs_by_tag)


def _enrich_operation(operation: dict, doc: RouteDoc) -> None:
    """Enrich a single operation dict from a RouteDoc.

    Applies summary, description, and parameter documentation from *doc*
    to *operation*. Summaries are only replaced when they are thin (see
    :func:`_is_thin`); descriptions are only replaced when the source
    version is longer.

    Args:
        operation: An operation object from ``paths[path][method]``.
        doc: Source-extracted documentation for this route.
    """
    # Enrich summary: only if missing or looks auto-generated.
    current_summary = operation.get("summary", "")
    if doc.summary and _is_thin(current_summary, operation.get("operationId")):
        operation["summary"] = doc.summary

    # Enrich description: only if source is longer / more detailed.
    current_desc = operation.get("description", "")
    if doc.description and len(doc.description) > len(current_desc or ""):
        operation["description"] = doc.description

    # Enrich parameter descriptions.
    if doc.param_docs:
        _enrich_parameters(operation, doc.param_docs)


def _enrich_parameters(operation: dict, param_docs: dict[str, str]) -> None:
    """Fill missing parameter descriptions from source docs.

    Iterates over ``parameters`` (path, query, header params) and
    ``requestBody`` schema properties, setting their ``description``
    from *param_docs* when the field is currently empty.

    Args:
        operation: An operation object from the spec.
        param_docs: Mapping of parameter name to description string
            extracted from source code.
    """
    params = operation.get("parameters", [])
    for param in params:
        if not isinstance(param, dict):
            continue
        name = param.get("name", "")
        if name in param_docs and not param.get("description"):
            param["description"] = param_docs[name]

    # Also check request body schema properties.
    request_body = operation.get("requestBody", {})
    if not isinstance(request_body, dict):
        return

    content = request_body.get("content", {})
    for media_type, media_obj in content.items():
        if not isinstance(media_obj, dict):
            continue
        schema = media_obj.get("schema", {})
        if not isinstance(schema, dict):
            continue
        _enrich_schema_properties(schema, param_docs)


def _enrich_schema_properties(schema: dict, param_docs: dict[str, str]) -> None:
    """Recursively fill missing descriptions on schema properties.

    Args:
        schema: A JSON Schema object (from ``requestBody.content.*.schema``).
        param_docs: Mapping of property name to description string.
    """
    properties = schema.get("properties", {})
    for prop_name, prop_schema in properties.items():
        if not isinstance(prop_schema, dict):
            continue
        if prop_name in param_docs and not prop_schema.get("description"):
            prop_schema["description"] = param_docs[prop_name]


def _enrich_tags(raw_spec: dict, module_docs: dict[str, str]) -> None:
    """Add tag descriptions from module docstrings.

    For existing tags that lack a description, uses the first line of
    the corresponding module docstring. Also creates new tag entries
    for tags referenced in operations but absent from the top-level
    ``tags`` array.

    Args:
        raw_spec: Raw OpenAPI spec dict (mutated in place).
        module_docs: Mapping of tag name to module-level docstring.
    """
    tags = raw_spec.get("tags", [])
    existing_tags = {t["name"] for t in tags if isinstance(t, dict) and "name" in t}

    for tag in tags:
        if not isinstance(tag, dict):
            continue
        name = tag.get("name", "")
        if not tag.get("description") and name in module_docs:
            # Use first line of module doc as tag description.
            first_line = module_docs[name].strip().splitlines()[0]
            tag["description"] = first_line

    # Add entries for tags that exist in operations but not in the top-level array.
    for tag_name, mod_doc in module_docs.items():
        if tag_name not in existing_tags:
            first_line = mod_doc.strip().splitlines()[0]
            tags.append({"name": tag_name, "description": first_line})

    if tags and "tags" not in raw_spec:
        raw_spec["tags"] = tags


def _normalise_path(path: str) -> str:
    """Normalise an API path for fuzzy matching.

    Strips trailing slashes and replaces all path parameter names with a
    generic ``{_}`` placeholder so that ``/api/items/{item_id}`` and
    ``/api/items/{itemId}`` produce the same normalised string.

    Args:
        path: Raw API path string (e.g. ``/api/items/{item_id}``).

    Returns:
        Normalised path with generic parameter placeholders.
    """
    path = path.rstrip("/") or "/"
    # Replace parameter names with a generic placeholder for matching.
    return re.sub(r"\{[^}]+\}", "{_}", path)


def _is_thin(summary: str | None, operation_id: str | None) -> bool:
    """Return ``True`` if *summary* is missing, empty, or looks auto-generated.

    A summary is considered thin when:

    * It is ``None`` or empty.
    * It is shorter than 15 characters.
    * It matches the title-cased form of *operation_id* (e.g.
      ``"Upload Asset"`` from ``"upload_asset"``).

    Args:
        summary: The current operation summary, possibly ``None``.
        operation_id: The operation's ``operationId``, used to detect
            auto-generated summaries.

    Returns:
        ``True`` if the summary should be replaced by source-extracted
        text; ``False`` if it is substantive enough to keep.
    """
    if not summary:
        return True

    # Very short summaries (< 15 chars) are considered thin.
    if len(summary.strip()) < 15:
        return True

    # If summary matches the operation_id pattern, consider it auto-generated.
    if operation_id:
        # Convert operationId to title case and compare.
        normalised_op_id = operation_id.replace("_", " ").replace("-", " ").strip()
        if summary.strip().lower() == normalised_op_id.lower():
            return True

    return False
