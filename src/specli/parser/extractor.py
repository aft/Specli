"""Extract operations, parameters, and security schemes from resolved OpenAPI specs.

This module walks a fully ``$ref``-resolved OpenAPI spec dictionary and builds
a :class:`~specli.models.ParsedSpec` containing every API operation,
parameter, request body, response definition, and security scheme declared in
the document.

The single public entry point is :func:`extract_spec`.  Internally it delegates
to private helpers that each handle one section of the OpenAPI structure:

* ``_extract_info`` -- the ``info`` object (title, version, contact, license).
* ``_extract_servers`` -- the ``servers`` array.
* ``_extract_operations`` -- the ``paths`` object, iterating over every
  path + HTTP method combination.
* ``_extract_security_schemes`` -- the ``components/securitySchemes`` map.

Parameter merging follows the OpenAPI specification: path-level parameters
provide defaults, and operation-level parameters override them when they share
the same ``name`` and ``in`` values.
"""

from __future__ import annotations

from typing import Any

from specli.models import (
    APIInfo,
    APIOperation,
    APIParameter,
    HTTPMethod,
    ParameterLocation,
    ParsedSpec,
    RequestBodyInfo,
    ResponseInfo,
    SecurityScheme,
    ServerInfo,
)
from specli.parser.resolver import resolve_refs

# HTTP methods recognized by OpenAPI
_HTTP_METHODS = frozenset(m.value for m in HTTPMethod)


def extract_spec(raw_spec: dict[str, Any], openapi_version: str) -> ParsedSpec:
    """Extract a :class:`~specli.models.ParsedSpec` from a raw OpenAPI dict.

    This is the main public entry point for the extraction pipeline.  It first
    resolves all ``$ref`` pointers via :func:`~specli.parser.resolver.resolve_refs`,
    then walks the resolved document to build the full :class:`~specli.models.ParsedSpec`.

    Args:
        raw_spec: The raw OpenAPI spec dictionary as returned by
            :func:`~specli.parser.loader.load_spec` (before ref resolution).
        openapi_version: The validated OpenAPI version string (e.g., ``"3.0.3"``),
            as returned by :func:`~specli.parser.loader.validate_openapi_version`.

    Returns:
        A fully populated :class:`~specli.models.ParsedSpec` instance
        containing extracted info, servers, operations, and security schemes.

    Example::

        raw = load_spec("petstore.yaml")
        version = validate_openapi_version(raw)
        parsed = extract_spec(raw, version)
        for op in parsed.operations:
            print(f"{op.method.value.upper()} {op.path}")
    """
    spec = resolve_refs(raw_spec)
    return ParsedSpec(
        info=_extract_info(spec),
        servers=_extract_servers(spec),
        operations=_extract_operations(spec),
        security_schemes=_extract_security_schemes(spec),
        openapi_version=openapi_version,
        raw_spec=raw_spec,
    )


def _extract_info(spec: dict[str, Any]) -> APIInfo:
    """Extract API metadata from the spec's ``info`` object.

    Reads the title, version, description, contact, and license fields
    defined in the `OpenAPI Info Object <https://spec.openapis.org/oas/v3.1.0#info-object>`_.

    Args:
        spec: The resolved spec dictionary.

    Returns:
        An :class:`~specli.models.APIInfo` instance.  Missing optional
        fields default to ``None``.
    """
    info = spec.get("info", {})
    contact = info.get("contact", {})
    license_info = info.get("license", {})

    return APIInfo(
        title=info.get("title", "Untitled API"),
        version=info.get("version", "0.0.0"),
        description=info.get("description"),
        terms_of_service=info.get("termsOfService"),
        contact_name=contact.get("name") if contact else None,
        contact_email=contact.get("email") if contact else None,
        contact_url=contact.get("url") if contact else None,
        license_name=license_info.get("name") if license_info else None,
        license_url=license_info.get("url") if license_info else None,
    )


def _extract_servers(spec: dict[str, Any]) -> list[ServerInfo]:
    """Extract server entries from the spec's ``servers`` array.

    Args:
        spec: The resolved spec dictionary.

    Returns:
        A list of :class:`~specli.models.ServerInfo` instances.
        Returns an empty list when no servers are declared.
    """
    servers = spec.get("servers", [])
    if not servers:
        return []

    return [
        ServerInfo(
            url=server.get("url", "/"),
            description=server.get("description"),
        )
        for server in servers
    ]


def _extract_operations(spec: dict[str, Any]) -> list[APIOperation]:
    """Extract all operations from the spec's ``paths`` object.

    Iterates over every path and recognised HTTP method (GET, POST, PUT, PATCH,
    DELETE, HEAD, OPTIONS, TRACE).  For each operation found, path-level
    parameters are merged with operation-level parameters -- operation-level
    takes precedence for parameters that share the same ``name`` and ``in``
    values, per the OpenAPI specification.

    Security requirements follow the same override rule: an operation-level
    ``security`` array replaces the global one; an explicit empty array
    ``[]`` means "no auth required".

    Args:
        spec: The resolved spec dictionary.

    Returns:
        A list of :class:`~specli.models.APIOperation` instances, one per
        path + HTTP method combination.
    """
    paths = spec.get("paths", {})
    global_security = spec.get("security", [])
    operations: list[APIOperation] = []

    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue

        # Path-level parameters apply to all operations under this path
        path_params = path_item.get("parameters", [])

        for method_str in _HTTP_METHODS:
            operation = path_item.get(method_str)
            if operation is None or not isinstance(operation, dict):
                continue

            # Merge parameters: operation-level overrides path-level
            op_params = operation.get("parameters", [])
            merged_params = _merge_parameters(path_params, op_params)

            # Extract request body
            request_body = _extract_request_body(operation.get("requestBody"))

            # Extract responses
            responses = _extract_responses(operation.get("responses", {}))

            # Security: operation-level overrides global, empty list means no auth
            op_security = operation.get("security")
            if op_security is not None:
                security = op_security
            else:
                security = global_security

            operations.append(
                APIOperation(
                    path=path,
                    method=HTTPMethod(method_str),
                    operation_id=operation.get("operationId"),
                    summary=operation.get("summary"),
                    description=operation.get("description"),
                    tags=operation.get("tags", []),
                    parameters=_extract_parameters(merged_params),
                    request_body=request_body,
                    responses=responses,
                    security=security,
                    deprecated=operation.get("deprecated", False),
                )
            )

    return operations


def _merge_parameters(
    path_params: list[dict[str, Any]],
    op_params: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge path-level and operation-level parameters.

    Operation-level parameters override path-level parameters with the same
    name and location (``in`` field), per the OpenAPI spec.

    Args:
        path_params: Parameters defined at the path level.
        op_params: Parameters defined at the operation level.

    Returns:
        A merged list of parameter dicts.
    """
    # Build a lookup for operation-level params keyed by (name, in)
    op_lookup: dict[tuple[str, str], dict[str, Any]] = {}
    for param in op_params:
        key = (param.get("name", ""), param.get("in", ""))
        op_lookup[key] = param

    # Start with path-level params, skip those overridden by operation-level
    merged: list[dict[str, Any]] = []
    for param in path_params:
        key = (param.get("name", ""), param.get("in", ""))
        if key not in op_lookup:
            merged.append(param)

    # Add all operation-level params
    merged.extend(op_params)

    return merged


def _extract_parameters(params_list: list[dict[str, Any]]) -> list[APIParameter]:
    """Convert raw OpenAPI parameter dicts into :class:`~specli.models.APIParameter` models.

    Handles schema type extraction (including OpenAPI 3.1 type arrays),
    enum values, defaults, and enforces the rule that path parameters are
    always required regardless of the ``required`` field in the source.

    Parameters with unrecognised ``in`` locations are silently skipped.

    Args:
        params_list: List of raw parameter dictionaries (already merged
            from path-level and operation-level).

    Returns:
        A list of :class:`~specli.models.APIParameter` instances.
    """
    parameters: list[APIParameter] = []

    for param in params_list:
        name = param.get("name", "")
        location_str = param.get("in", "query")

        try:
            location = ParameterLocation(location_str)
        except ValueError:
            # Skip parameters with unrecognized locations
            continue

        # Extract schema info
        schema = param.get("schema", {})
        schema_type = _extract_schema_type(schema)
        schema_format = schema.get("format") if isinstance(schema, dict) else None
        default = schema.get("default") if isinstance(schema, dict) else None
        enum_values = schema.get("enum") if isinstance(schema, dict) else None

        # Path parameters are always required per the spec
        required = param.get("required", False)
        if location == ParameterLocation.PATH:
            required = True

        parameters.append(
            APIParameter(
                name=name,
                location=location,
                required=required,
                description=param.get("description"),
                schema_type=schema_type,
                schema_format=schema_format,
                default=default,
                enum_values=enum_values,
                example=param.get("example"),
            )
        )

    return parameters


def _extract_schema_type(schema: Any) -> str:
    """Extract the type string from a schema object.

    Handles OpenAPI 3.1 type arrays (e.g., ["string", "null"]) by returning
    the first non-null type. Falls back to "string" if type is missing.

    Args:
        schema: A schema dict or other value.

    Returns:
        The extracted type string.
    """
    if not isinstance(schema, dict):
        return "string"

    type_value = schema.get("type", "string")

    # OpenAPI 3.1 allows type to be an array (e.g., ["string", "null"])
    if isinstance(type_value, list):
        # Return the first non-null type
        non_null = [t for t in type_value if t != "null"]
        return non_null[0] if non_null else "string"

    return str(type_value)


def _extract_request_body(body: dict[str, Any] | None) -> RequestBodyInfo | None:
    """Extract request body metadata from an operation's ``requestBody``.

    Collects the list of accepted content types and retrieves the JSON Schema
    from the first content type entry that declares one.

    Args:
        body: The raw ``requestBody`` dict, or ``None`` when the operation
            does not accept a request body.

    Returns:
        A :class:`~specli.models.RequestBodyInfo` instance, or ``None``
        if *body* is ``None``.
    """
    if body is None:
        return None

    content = body.get("content", {})
    content_types = list(content.keys())

    # Get schema from the first content type that has one
    schema: dict[str, Any] | None = None
    for ct_info in content.values():
        if isinstance(ct_info, dict) and "schema" in ct_info:
            schema = ct_info["schema"]
            break

    return RequestBodyInfo(
        required=body.get("required", False),
        description=body.get("description"),
        content_types=content_types,
        schema=schema,
    )


def _extract_responses(responses: dict[str, Any]) -> list[ResponseInfo]:
    """Extract response metadata for all declared status codes.

    For each response entry, collects the status code, description,
    content types, and the JSON Schema from the first content type that
    provides one.

    Args:
        responses: The raw ``responses`` dict, keyed by HTTP status code
            string (e.g., ``"200"``, ``"404"``, ``"default"``).

    Returns:
        A list of :class:`~specli.models.ResponseInfo` instances, one
        per status code entry.
    """
    result: list[ResponseInfo] = []

    for status_code, response in responses.items():
        if not isinstance(response, dict):
            continue

        content = response.get("content", {})
        content_types = list(content.keys())

        # Get schema from the first content type that has one
        schema: dict[str, Any] | None = None
        for ct_info in content.values():
            if isinstance(ct_info, dict) and "schema" in ct_info:
                schema = ct_info["schema"]
                break

        result.append(
            ResponseInfo(
                status_code=str(status_code),
                description=response.get("description"),
                content_types=content_types,
                schema=schema,
            )
        )

    return result


def _extract_security_schemes(spec: dict[str, Any]) -> dict[str, SecurityScheme]:
    """Extract security scheme definitions from ``components/securitySchemes``.

    Supports all OpenAPI security scheme types: ``apiKey``, ``http``,
    ``oauth2``, and ``openIdConnect``.

    Args:
        spec: The resolved spec dictionary.

    Returns:
        A dict mapping scheme name to :class:`~specli.models.SecurityScheme`
        instance.  Returns an empty dict when no security schemes are declared.
    """
    components = spec.get("components", {})
    schemes_raw = components.get("securitySchemes", {})
    schemes: dict[str, SecurityScheme] = {}

    for name, scheme_data in schemes_raw.items():
        if not isinstance(scheme_data, dict):
            continue

        scheme_type = scheme_data.get("type", "")

        schemes[name] = SecurityScheme(
            name=name,
            type=scheme_type,
            description=scheme_data.get("description"),
            in_name=scheme_data.get("name"),
            in_location=scheme_data.get("in"),
            scheme=scheme_data.get("scheme"),
            bearer_format=scheme_data.get("bearerFormat"),
            flows=scheme_data.get("flows"),
            openid_connect_url=scheme_data.get("openIdConnectUrl"),
        )

    return schemes
