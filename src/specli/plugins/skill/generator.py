"""Generate Claude Code skill files from a parsed OpenAPI specification.

This module is the core of the skill generation pipeline. It takes a
:class:`~specli.models.ParsedSpec` and produces a complete Claude Code
skill directory containing:

* ``SKILL.md`` -- Main skill file with grouped command examples and usage
  patterns.
* ``references/api-reference.md`` -- Full endpoint documentation including
  parameters, request bodies, and response schemas.
* ``references/auth-setup.md`` -- Authentication configuration guide derived
  from the spec's security schemes.

The generation process:

1. A Jinja2 environment is configured with templates from ``plugins/skill/templates/``.
2. Operations are grouped by their first path segment (or primary tag).
3. Each operation is converted to a human-readable CLI command string.
4. Templates are rendered with the assembled context and written to disk.

See Also:
    :mod:`specli.plugins.skill` for the package-level overview.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from specli.models import APIOperation, HTTPMethod, ParsedSpec, Profile


TEMPLATE_DIR = Path(__file__).parent / "templates"
"""Path to the Jinja2 template directory (``plugins/skill/templates/``)."""

_METHOD_VERB_MAP: dict[HTTPMethod, str] = {
    HTTPMethod.GET: "get",
    HTTPMethod.POST: "create",
    HTTPMethod.PUT: "update",
    HTTPMethod.PATCH: "patch",
    HTTPMethod.DELETE: "delete",
    HTTPMethod.HEAD: "head",
    HTTPMethod.OPTIONS: "options",
    HTTPMethod.TRACE: "trace",
}


def generate_skill(
    spec: ParsedSpec,
    output_dir: str | Path,
    profile: Optional[Profile] = None,
    workflows: Optional[list[dict]] = None,
) -> Path:
    """Generate a complete Claude Code skill directory from a parsed spec.

    Creates the following files inside *output_dir*:

    * ``SKILL.md`` -- Main skill file with grouped command examples.
    * ``references/api-reference.md`` -- Full endpoint documentation.
    * ``references/auth-setup.md`` -- Authentication setup guide.

    Directories are created automatically if they do not exist.

    Args:
        spec: A fully parsed OpenAPI specification containing operations,
            security schemes, servers, and API info.
        output_dir: Directory where skill files will be written. Created
            (including parents) if it does not exist.
        profile: Optional profile for customized naming and base URL.
            When ``None``, the API title is slugified for naming and the
            first server URL is used.
        workflows: Optional list of workflow dicts with ``title`` and
            ``steps`` keys to render in the SKILL.md workflows section.

    Returns:
        The resolved :class:`~pathlib.Path` of *output_dir*.

    Example:
        ::

            from specli.plugins.skill import generate_skill

            result = generate_skill(parsed_spec, "./my-skill", profile)
            print(f"Skill written to {result}")
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    refs_path = output_path / "references"
    refs_path.mkdir(exist_ok=True)

    env = _create_jinja_env()

    # Build template context
    context = _build_context(spec, profile)
    context["workflows"] = workflows or []

    # Render and write files
    _render_template(env, "skill.md.j2", output_path / "SKILL.md", context)
    _render_template(env, "reference.md.j2", refs_path / "api-reference.md", context)
    _render_template(env, "auth_setup.md.j2", refs_path / "auth-setup.md", context)

    return output_path


def _create_jinja_env() -> Environment:
    """Create and configure the Jinja2 environment for skill templates.

    The environment uses a :class:`~jinja2.FileSystemLoader` pointing at
    the ``skill/templates/`` directory alongside this module. Autoescape is
    enabled for all templates except ``.md.j2`` files (which produce
    Markdown, not HTML). Block trimming and lstrip are enabled for cleaner
    template authoring.

    Returns:
        A configured :class:`~jinja2.Environment` instance.
    """
    return Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(disabled_extensions=("md.j2",)),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def _build_context(spec: ParsedSpec, profile: Optional[Profile] = None) -> dict:
    """Build the full template context dict from a spec and optional profile.

    Assembles all variables needed by the Jinja2 skill templates:

    * Derives a URL-safe slug name from the API title.
    * Groups operations by resource (first path segment or first tag).
    * Converts each operation into a human-readable CLI command string
      via :func:`_operation_to_command`.

    Args:
        spec: The parsed OpenAPI specification containing operations,
            servers, security schemes, and info.
        profile: Optional profile for naming and URL customization.
            When ``None``, defaults are derived from the spec itself.

    Returns:
        A dict with keys including ``"name"``, ``"title"``,
        ``"description"``, ``"spec_url"``, ``"profile_name"``,
        ``"grouped_operations"``, ``"operations"``, ``"security_schemes"``,
        ``"servers"``, and ``"info"``.
    """
    profile_name = profile.name if profile else "default"
    spec_url = profile.spec if profile else (
        spec.servers[0].url if spec.servers else "https://api.example.com"
    )

    # Derive a slug-like name from the API title
    title = spec.info.title
    name = _slugify(title)
    description = spec.info.description or f"CLI for the {title}"

    # Build operation list with command strings and group them
    enriched_ops = []
    for op in spec.operations:
        command = _operation_to_command(op, profile_name)
        summary = op.summary or op.description or "No description"
        enriched_ops.append({
            "command": command,
            "summary": summary,
            "operation": op,
        })

    grouped_operations = _group_operations_by_resource(spec.operations)

    # Build grouped operations with command strings for the skill template
    grouped_with_commands: dict[str, list[dict]] = {}
    for group_name, ops in grouped_operations.items():
        group_entries = []
        for op in ops:
            command = _operation_to_command(op, profile_name)
            summary = op.summary or op.description or "No description"
            group_entries.append({
                "command": command,
                "summary": summary,
            })
        grouped_with_commands[group_name] = group_entries

    return {
        "name": name,
        "title": title,
        "description": description,
        "spec_url": spec_url,
        "profile_name": profile_name,
        "grouped_operations": grouped_with_commands,
        "operations": spec.operations,
        "security_schemes": spec.security_schemes,
        "servers": spec.servers,
        "info": spec.info,
    }


def _operation_to_command(operation: APIOperation, profile_name: str = "default") -> str:
    """Convert an operation to a CLI command string for examples.

    Uses the operation's path segments and HTTP method to build a readable
    command. Path parameters become <placeholder> arguments.

    Examples:
        GET /pets          -> specli pets list
        POST /pets         -> specli pets create
        GET /pets/{petId}  -> specli pets get <petId>
        DELETE /pets/{id}  -> specli pets delete <id>

    Args:
        operation: The API operation to convert.
        profile_name: The profile name (unused in command but available for
            future --profile flag injection).

    Returns:
        A CLI command string.
    """
    # Parse path into segments, filtering empty strings
    segments = [s for s in operation.path.strip("/").split("/") if s]

    # Separate resource segments from path parameters
    resource_parts: list[str] = []
    param_parts: list[str] = []

    for segment in segments:
        if segment.startswith("{") and segment.endswith("}"):
            # Extract parameter name from {paramName}
            param_name = segment[1:-1]
            param_parts.append(f"<{param_name}>")
        else:
            resource_parts.append(segment)

    # Build the resource path portion (e.g., "pets" or "users accounts")
    resource = " ".join(resource_parts) if resource_parts else "root"

    # Determine the verb from the HTTP method
    verb = _METHOD_VERB_MAP.get(operation.method, operation.method.value)

    # For GET with no path params, use "list"; for GET with params, use "get"
    if operation.method == HTTPMethod.GET:
        verb = "list" if not param_parts else "get"

    # Assemble command parts
    parts = ["specli", resource, verb]
    parts.extend(param_parts)

    return " ".join(parts)


def _group_operations_by_resource(operations: list[APIOperation]) -> dict[str, list[APIOperation]]:
    """Group operations by their first path segment or primary tag.

    If an operation has tags, the first tag is used as the group name.
    Otherwise, the first non-parameter path segment is used.
    Groups are title-cased for display.

    Args:
        operations: List of API operations to group.

    Returns:
        An ordered dict mapping group name to list of operations.
    """
    groups: dict[str, list[APIOperation]] = defaultdict(list)

    for op in operations:
        if op.tags:
            # Use the first tag as the group name
            group_name = op.tags[0].title()
        else:
            # Fall back to the first non-parameter path segment
            segments = [
                s for s in op.path.strip("/").split("/")
                if s and not (s.startswith("{") and s.endswith("}"))
            ]
            group_name = segments[0].title() if segments else "General"

        groups[group_name].append(op)

    # Return as a regular dict (preserves insertion order in Python 3.7+)
    return dict(groups)


def _render_template(
    env: Environment,
    template_name: str,
    output_path: Path,
    context: dict,
) -> None:
    """Render a template and write to file.

    Args:
        env: The Jinja2 Environment.
        template_name: Name of the template file to render.
        output_path: Path where the rendered output is written.
        context: Template variables.
    """
    template = env.get_template(template_name)
    rendered = template.render(**context)
    output_path.write_text(rendered, encoding="utf-8")


def _slugify(text: str) -> str:
    """Convert a title to a URL/filename-safe slug.

    Lowercases, replaces non-alphanumeric characters with hyphens,
    and collapses consecutive hyphens.

    Args:
        text: The text to slugify.

    Returns:
        A slug string.
    """
    slug = text.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug
