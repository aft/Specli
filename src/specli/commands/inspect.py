"""Inspect commands -- examine API spec details.

Provides the ``specli inspect`` sub-command group with read-only
commands for viewing the contents of an OpenAPI spec: paths (operations),
schemas, security schemes, and general API info. All sub-commands
resolve the active profile, load the associated spec, and present the
data in table or structured output format.
"""

from __future__ import annotations

from typing import Optional

import typer

from specli.output import debug, error, format_response, get_output, info


inspect_app = typer.Typer(no_args_is_help=True)


def _load_spec_from_profile(
    profile_name: Optional[str] = None,
):  # noqa: ANN202
    """Load the ParsedSpec and Profile for the active (or named) profile.

    Resolves the profile via :func:`~specli.config.resolve_config`,
    fetches and parses its OpenAPI spec, and returns both objects.

    Args:
        profile_name: Explicit profile name. When ``None``, the default
            profile from the project or global config is used.

    Returns:
        A ``(ParsedSpec, Profile)`` tuple.

    Raises:
        typer.Exit: With code 2 when no profile can be resolved or the
            spec cannot be loaded.
    """
    from specli.config import resolve_config
    from specli.parser import load_spec, validate_openapi_version
    from specli.parser.extractor import extract_spec

    try:
        _, profile = resolve_config(cli_profile=profile_name)
    except Exception as exc:
        error(f"Config error: {exc}")
        raise typer.Exit(code=2) from None

    if profile is None:
        error("No active profile. Run: specli init --spec <url>")
        raise typer.Exit(code=2)

    try:
        raw = load_spec(profile.spec)
        version = validate_openapi_version(raw)
        spec = extract_spec(raw, version)
    except Exception as exc:
        error(f"Failed to load spec: {exc}")
        raise typer.Exit(code=2) from None

    return spec, profile


@inspect_app.command("paths")
def inspect_paths(
    profile: Optional[str] = typer.Option(
        None, "--profile", "-p", help="Profile name."
    ),
) -> None:
    """List all API paths.

    Displays a table of every operation in the spec with its HTTP
    method, path, summary, and deprecation status.

    Args:
        profile: Optional profile name override.

    Example::

        specli inspect paths
        specli inspect paths --profile myapi
    """
    spec, _ = _load_spec_from_profile(profile)

    output = get_output()
    headers = ["Method", "Path", "Summary", "Deprecated"]
    rows: list[list[str]] = []
    for op in sorted(spec.operations, key=lambda o: (o.path, o.method.value)):
        rows.append([
            op.method.value.upper(),
            op.path,
            op.summary or "-",
            "Yes" if op.deprecated else "",
        ])

    output.print_table(
        headers, rows, title=f"{spec.info.title} -- Paths ({len(rows)})"
    )


@inspect_app.command("schemas")
def inspect_schemas(
    profile: Optional[str] = typer.Option(
        None, "--profile", "-p", help="Profile name."
    ),
) -> None:
    """List all schemas defined in the spec.

    Shows a table of ``components.schemas`` entries with their type and
    up to five property names.

    Args:
        profile: Optional profile name override.

    Example::

        specli inspect schemas
    """
    spec, _ = _load_spec_from_profile(profile)

    schemas: dict = {}
    if spec.raw_spec:
        schemas = spec.raw_spec.get("components", {}).get("schemas", {})

    if not schemas:
        info("No schemas defined in this spec.")
        return

    output = get_output()
    headers = ["Schema", "Type", "Properties"]
    rows: list[list[str]] = []
    for name, schema in sorted(schemas.items()):
        schema_type = schema.get("type", "object") if isinstance(schema, dict) else "unknown"
        props_dict = schema.get("properties", {}) if isinstance(schema, dict) else {}
        prop_names = list(props_dict.keys())
        props = ", ".join(prop_names[:5])
        if len(prop_names) > 5:
            props += "..."
        rows.append([name, schema_type, props])

    output.print_table(headers, rows, title=f"Schemas ({len(rows)})")


@inspect_app.command("auth")
def inspect_auth(
    profile: Optional[str] = typer.Option(
        None, "--profile", "-p", help="Profile name."
    ),
) -> None:
    """Show security schemes defined in the spec.

    Displays a table of all security schemes with their name, type,
    HTTP scheme, parameter location, and a truncated description.

    Args:
        profile: Optional profile name override.

    Example::

        specli inspect auth
    """
    spec, _ = _load_spec_from_profile(profile)

    if not spec.security_schemes:
        info("No security schemes defined.")
        return

    output = get_output()
    headers = ["Name", "Type", "Scheme", "Location", "Description"]
    rows: list[list[str]] = []
    for name, scheme in spec.security_schemes.items():
        desc = (scheme.description or "-")[:60]
        rows.append([
            name,
            scheme.type,
            scheme.scheme or "-",
            scheme.location or "-",
            desc,
        ])

    output.print_table(headers, rows, title="Security Schemes")


@inspect_app.command("info")
def inspect_info(
    profile: Optional[str] = typer.Option(
        None, "--profile", "-p", help="Profile name."
    ),
) -> None:
    """Show API info (title, version, description, etc.).

    Outputs a structured view of the API metadata: title, version,
    OpenAPI version, description, server URLs, operation count,
    security scheme names, and optional contact/license fields.

    Args:
        profile: Optional profile name override.

    Example::

        specli inspect info
    """
    spec, prof = _load_spec_from_profile(profile)

    data: dict = {
        "title": spec.info.title,
        "version": spec.info.version,
        "openapi_version": spec.openapi_version,
        "description": spec.info.description or "-",
        "servers": [s.url for s in spec.servers],
        "operations": len(spec.operations),
        "security_schemes": list(spec.security_schemes.keys()),
    }

    if spec.info.contact_email:
        data["contact"] = spec.info.contact_email
    if spec.info.license_name:
        data["license"] = spec.info.license_name

    format_response(data)
