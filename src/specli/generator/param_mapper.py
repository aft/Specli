"""Map OpenAPI parameters to Typer CLI options and arguments.

This module bridges the gap between OpenAPI parameter definitions and
Typer's CLI interface.  It converts :class:`~specli.models.APIParameter`
models into descriptor dictionaries that
:func:`~specli.generator.command_tree._build_command_function` uses to
construct dynamically generated function signatures.

**Mapping rules:**

* **Path parameters** (``in: path``) become positional :func:`typer.Argument`
  values -- always required.
* **Query, header, and cookie parameters** become ``--option`` style flags
  via :func:`typer.Option`.  Required parameters use ``...`` (Typer's
  "required" sentinel); optional parameters use their declared default or
  ``None``.
* **OpenAPI types** are mapped to Python types: ``string`` to ``str``,
  ``integer`` to ``int``, ``number`` to ``float``, ``boolean`` to ``bool``.
  ``array`` and ``object`` types are serialised as JSON strings since Typer
  does not support complex types natively.
* **Parameter names** are sanitised to valid Python identifiers via
  :func:`sanitize_param_name` (CamelCase to snake_case, keyword escaping, etc.).
"""

from __future__ import annotations

import keyword
import re
from typing import Any, Optional

import typer

from specli.models import APIParameter, ParameterLocation


# ---------------------------------------------------------------------------
# Type mapping
# ---------------------------------------------------------------------------

_TYPE_MAP: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": str,  # Serialised as JSON string (Typer does not support list)
    "object": str,  # Serialised as JSON string
}

_FORMAT_OVERRIDES: dict[tuple[str, str], type] = {
    ("string", "binary"): bytes,
    ("string", "byte"): bytes,
    ("integer", "int32"): int,
    ("integer", "int64"): int,
    ("number", "float"): float,
    ("number", "double"): float,
}


def openapi_type_to_python(
    schema_type: str,
    schema_format: Optional[str] = None,
) -> type:
    """Map an OpenAPI schema type (with optional format) to a Python type.

    The base type is determined from ``_TYPE_MAP``.  When a ``schema_format``
    is provided, ``_FORMAT_OVERRIDES`` is checked first for a more specific
    mapping (e.g., ``string`` + ``binary`` yields ``bytes``).

    Args:
        schema_type: The OpenAPI ``type`` string (e.g., ``"string"``,
            ``"integer"``, ``"boolean"``).
        schema_format: Optional OpenAPI ``format`` string (e.g., ``"int64"``,
            ``"binary"``, ``"double"``).

    Returns:
        The corresponding Python type.  Defaults to ``str`` for unrecognised
        types.

    Example::

        >>> openapi_type_to_python("integer", "int64")
        <class 'int'>
        >>> openapi_type_to_python("string", "binary")
        <class 'bytes'>
        >>> openapi_type_to_python("array")
        <class 'str'>  # serialised as JSON
    """
    if schema_format:
        override = _FORMAT_OVERRIDES.get((schema_type, schema_format))
        if override is not None:
            return override
    return _TYPE_MAP.get(schema_type, str)


# ---------------------------------------------------------------------------
# Name sanitisation
# ---------------------------------------------------------------------------

# Matches any character that is not alphanumeric or underscore.
_INVALID_IDENT_RE = re.compile(r"[^a-zA-Z0-9_]")


def sanitize_param_name(name: str) -> str:
    """Convert an OpenAPI parameter name to a valid Python identifier.

    Applies the following transformations in order:

    1. CamelCase boundaries are split with underscores (``petId`` becomes
       ``pet_id``).
    2. The string is lowercased.
    3. Hyphens and dots are replaced with underscores.
    4. Any remaining non-alphanumeric/non-underscore characters are replaced.
    5. Consecutive and leading/trailing underscores are collapsed.
    6. An empty result defaults to ``"param"``.
    7. A leading digit gets an underscore prefix.
    8. Python keywords get a trailing underscore per PEP 8 convention
       (e.g., ``"class"`` becomes ``"class_"``).

    Args:
        name: The raw OpenAPI parameter name (e.g., ``"petId"``,
            ``"X-Request-ID"``, ``"filter.status"``).

    Returns:
        A valid Python identifier suitable for use as a function parameter
        name (e.g., ``"pet_id"``, ``"x_request_id"``, ``"filter_status"``).

    Example::

        >>> sanitize_param_name("petId")
        'pet_id'
        >>> sanitize_param_name("X-Request-ID")
        'x_request_id'
        >>> sanitize_param_name("class")
        'class_'
    """
    # Insert underscores at CamelCase boundaries before lowering.
    # e.g. "petId" -> "pet_Id", "XMLParser" -> "XML_Parser"
    result = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    result = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", result)
    # Lowercase everything.
    result = result.lower()
    # Replace common separators with underscores.
    result = result.replace("-", "_").replace(".", "_")
    # Strip any remaining invalid characters.
    result = _INVALID_IDENT_RE.sub("_", result)
    # Collapse multiple underscores.
    result = re.sub(r"_+", "_", result).strip("_")
    # Handle empty result.
    if not result:
        result = "param"
    # Leading digit -> prefix with underscore.
    if result[0].isdigit():
        result = f"_{result}"
    # Python keyword -> trailing underscore (PEP 8 convention).
    if keyword.iskeyword(result):
        result = f"{result}_"
    return result


# ---------------------------------------------------------------------------
# Parameter mapping
# ---------------------------------------------------------------------------


def map_parameter_to_typer(param: APIParameter) -> dict[str, Any]:
    """Map a single :class:`~specli.models.APIParameter` to a Typer descriptor dict.

    Determines whether the parameter should be a positional argument or a
    named option, computes the Python type, builds help text (including enum
    choices and deprecation markers), and constructs the appropriate
    :func:`typer.Argument` or :func:`typer.Option` default.

    Args:
        param: The :class:`~specli.models.APIParameter` to map.

    Returns:
        A dict with the following keys:

        * ``name`` (``str``) -- Python-safe parameter name (snake_case).
        * ``original_name`` (``str``) -- The original API parameter name,
          used to reconstruct the HTTP request.
        * ``type`` (``type``) -- Python type annotation for the parameter.
        * ``default`` -- A :func:`typer.Option` or :func:`typer.Argument`
          descriptor.
        * ``help`` (``str``) -- Help text for ``--help`` output.
        * ``is_argument`` (``bool``) -- ``True`` for path parameters
          (positional args), ``False`` for options.
        * ``location`` (:class:`~specli.models.ParameterLocation`) --
          The original parameter location.
    """
    py_name = sanitize_param_name(param.name)
    py_type = openapi_type_to_python(param.schema_type, param.schema_format)
    is_argument = param.location == ParameterLocation.PATH

    help_text = param.description or ""
    if param.enum_values:
        choices = ", ".join(param.enum_values)
        enum_hint = f"[choices: {choices}]"
        help_text = f"{help_text}  {enum_hint}" if help_text else enum_hint

    if param.deprecated if hasattr(param, "deprecated") else False:
        help_text = f"[DEPRECATED] {help_text}" if help_text else "[DEPRECATED]"

    if is_argument:
        # Path parameters are always required positional arguments.
        default = typer.Argument(..., help=help_text or None)
    elif param.required:
        # Required non-path parameter -> option that must be supplied.
        default = typer.Option(..., f"--{param.name}", help=help_text or None)
    else:
        # Optional parameter with possible default value.
        fallback = param.default
        if fallback is None:
            # Use Optional type for truly optional params.
            py_type = Optional[py_type]  # type: ignore[assignment]
        default = typer.Option(fallback, f"--{param.name}", help=help_text or None)

    return {
        "name": py_name,
        "original_name": param.name,
        "type": py_type,
        "default": default,
        "help": help_text,
        "is_argument": is_argument,
        "location": param.location,
    }


# ---------------------------------------------------------------------------
# Body option
# ---------------------------------------------------------------------------


def build_body_field_options(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Build per-field ``--option`` descriptors from a JSON Schema's properties.

    Each property in the schema becomes a named ``--option`` flag on the CLI.
    All fields are optional at the Typer level (since ``--body`` JSON can
    satisfy required fields), but required fields are marked in help text.

    Complex types (``object``, ``array``) are serialised as JSON strings since
    Typer does not support composite types natively.

    Args:
        schema: A JSON Schema dict with a ``properties`` mapping.

    Returns:
        A list of descriptor dicts with the same shape as
        :func:`map_parameter_to_typer`.  Each entry uses ``original_name``
        prefixed with ``__body__.`` so the command builder can distinguish
        body fields from regular parameters.  An additional ``body_field_type``
        key stores the schema type for body assembly (object/array values
        need JSON parsing).
    """
    properties = schema.get("properties", {})
    required_fields = set(schema.get("required", []))
    descriptors: list[dict[str, Any]] = []

    for prop_name, prop_schema in properties.items():
        if not isinstance(prop_schema, dict):
            continue

        py_name = sanitize_param_name(prop_name)
        raw_type = prop_schema.get("type", "string")
        # OpenAPI 3.1 allows type: ["string", "null"] — extract the
        # first non-null type for mapping purposes.
        if isinstance(raw_type, list):
            non_null = [t for t in raw_type if t != "null"]
            schema_type = non_null[0] if non_null else "string"
        else:
            schema_type = raw_type
        schema_format = prop_schema.get("format")
        py_type = openapi_type_to_python(schema_type, schema_format)
        is_required = prop_name in required_fields

        help_text = prop_schema.get("description", "")
        if prop_schema.get("enum"):
            choices = ", ".join(str(v) for v in prop_schema["enum"])
            enum_hint = f"[choices: {choices}]"
            help_text = f"{help_text}  {enum_hint}" if help_text else enum_hint

        prop_default = prop_schema.get("default")
        # Primary CLI name uses hyphens (standard convention);
        # Typer also accepts the underscore variant automatically.
        cli_name = f"--{prop_name.replace('_', '-')}"

        # All body fields are optional at the CLI level so that --body JSON
        # can satisfy required fields.  Required fields are marked in help.
        if is_required:
            req_help = f"{help_text}  [REQUIRED]" if help_text else "[REQUIRED]"
            py_type = Optional[py_type]  # type: ignore[assignment]
            default = typer.Option(None, cli_name, help=req_help)
        else:
            if prop_default is None:
                py_type = Optional[py_type]  # type: ignore[assignment]
            default = typer.Option(prop_default, cli_name, help=help_text or None)

        descriptors.append({
            "name": py_name,
            "original_name": f"__body__.{prop_name}",
            "type": py_type,
            "default": default,
            "help": help_text,
            "is_argument": False,
            "location": None,
            "body_field_type": schema_type,
        })

    return descriptors


def build_body_option() -> dict[str, Any]:
    """Build a ``--body`` / ``-b`` option descriptor for request body operations.

    The generated option accepts either an inline JSON string or a file
    reference prefixed with ``@`` (e.g., ``@request.json``).  File references
    are resolved at invocation time by
    :func:`~specli.generator.command_tree._resolve_body`.

    Returns:
        A dict with the same shape as :func:`map_parameter_to_typer`, where
        ``original_name`` is the sentinel ``"__body__"`` (used internally to
        distinguish the body option from regular parameters).
    """
    help_text = (
        "Request body as JSON string, or @filename to read from file."
    )
    return {
        "name": "body",
        "original_name": "__body__",
        "type": Optional[str],
        "default": typer.Option(None, "--body", "-b", help=help_text),
        "help": help_text,
        "is_argument": False,
        "location": None,
    }
