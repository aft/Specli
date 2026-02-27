"""Build a Typer command tree from a ParsedSpec.

This is the core algorithm of specli.  It takes a fully parsed OpenAPI
specification and produces a nested :class:`typer.Typer` application whose
sub-commands mirror the API resource hierarchy.

**Algorithm summary**

1. Apply path rules to obtain transformed paths.
2. Convert each transformed path to *command parts* (resource segments with
   path parameters stripped).
3. Group all operations by their command parts.
4. Build a tree of :class:`typer.Typer` sub-apps -- one per resource segment.
5. Attach leaf commands named after the HTTP verb (``list``, ``get``,
   ``create``, ``update``, ``delete``, ...).
6. Each leaf command is a dynamically generated function whose signature
   matches the operation's parameters plus an optional ``--body`` flag.
"""

from __future__ import annotations

import json
import textwrap
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Optional

import click
import typer

from specli.generator.param_mapper import (
    build_body_field_options,
    build_body_option,
    map_parameter_to_typer,
)
from specli.generator.path_rules import apply_path_rules, path_to_command_parts
from specli.models import (
    APIOperation,
    HTTPMethod,
    ParsedSpec,
    PathRulesConfig,
)

# ---------------------------------------------------------------------------
# HTTP method -> default CLI verb
# ---------------------------------------------------------------------------

METHOD_TO_VERB: dict[HTTPMethod, Optional[str]] = {
    HTTPMethod.GET: None,  # Resolved dynamically: "list" or "get"
    HTTPMethod.POST: "create",
    HTTPMethod.PUT: "update",
    HTTPMethod.PATCH: "patch",
    HTTPMethod.DELETE: "delete",
    HTTPMethod.HEAD: "head",
    HTTPMethod.OPTIONS: "options",
    HTTPMethod.TRACE: "trace",
}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_command_tree(
    spec: ParsedSpec,
    path_rules: PathRulesConfig,
    request_callback: Optional[Callable[..., Any]] = None,
) -> typer.Typer:
    """Build a nested :class:`typer.Typer` command tree from a parsed OpenAPI spec.

    This is the main public entry point of the generator.  It transforms API
    paths using the supplied rules, groups operations by resource hierarchy,
    materialises a tree of Typer sub-applications, and attaches a leaf command
    for each HTTP operation.

    Args:
        spec: A :class:`~specli.models.ParsedSpec` as produced by
            :func:`~specli.parser.extractor.extract_spec`.
        path_rules: A :class:`~specli.models.PathRulesConfig` controlling
            how API paths are stripped, collapsed, and segmented into CLI
            sub-command groups.  See :func:`~specli.generator.path_rules.apply_path_rules`.
        request_callback: Function called when a generated CLI command is
            invoked at runtime.  Expected signature::

                callback(method: str, path: str, params: dict,
                         body: str | None, content_type: str | None) -> Any

            When ``None``, commands print a dry-run summary to stdout instead
            of making real HTTP requests.

    Returns:
        A :class:`typer.Typer` app with all commands registered.  Call
        ``app()`` to start the CLI.

    Example::

        from specli.models import PathRulesConfig

        rules = PathRulesConfig(auto_strip_prefix=True)
        app = build_command_tree(parsed_spec, rules)
        app()
    """
    app = typer.Typer(
        name=_slugify(spec.info.title),
        help=spec.info.description or spec.info.title,
        no_args_is_help=True,
    )

    if not spec.operations:
        return app

    # Step 1 -- transform paths.
    original_paths = list({op.path for op in spec.operations})
    path_map = apply_path_rules(original_paths, path_rules)

    # Step 2+3 -- group operations by command parts.
    groups = _group_operations(spec.operations, path_map)

    # Step 4 -- derive group descriptions and materialise the sub-app tree.
    tag_descriptions = _extract_tag_descriptions(spec)
    group_help = _build_group_help(groups, tag_descriptions)
    sub_apps: dict[tuple[str, ...], typer.Typer] = {}

    for parts, op_entries in sorted(groups.items(), key=lambda kv: kv[0]):
        # Ensure parent sub-apps exist.
        parent_app = _ensure_sub_apps(app, parts, sub_apps, group_help)

        # Step 5 -- attach leaf commands.
        for operation, original_path in op_entries:
            if _is_html_only(operation):
                continue
            verb = _determine_verb(operation)
            cmd_fn = _build_command_function(
                operation, original_path, request_callback,
            )
            help_text = _build_help_text(operation)

            # Avoid duplicate command names within the same sub-app by
            # appending the HTTP method when a collision would occur.
            existing_names = {
                cmd.name
                for cmd in getattr(parent_app, "registered_commands", [])
            }
            final_verb = verb
            if final_verb in existing_names:
                final_verb = f"{verb}-{operation.method.value}"

            parent_app.command(name=final_verb, help=help_text)(cmd_fn)

    return app


# ---------------------------------------------------------------------------
# HTML-only endpoint filter
# ---------------------------------------------------------------------------


def _is_html_only(operation: APIOperation) -> bool:
    """Return True if *operation* only produces non-API content types.

    Endpoints that exclusively return ``text/html`` (or similar browser
    content) are web-UI pages and have no value as CLI commands.  They are
    silently skipped during command-tree generation.
    """
    if not operation.responses:
        return False
    all_content_types: set[str] = set()
    for resp in operation.responses:
        all_content_types.update(resp.content_types)
    if not all_content_types:
        return False
    api_types = {"application/json", "application/xml", "text/plain",
                 "application/octet-stream", "multipart/form-data"}
    return not all_content_types & api_types


# ---------------------------------------------------------------------------
# Verb determination
# ---------------------------------------------------------------------------


def _determine_verb(operation: APIOperation) -> str:
    """Choose the CLI verb for *operation*.

    * ``GET`` on a collection path (no trailing path parameter) -> ``list``
    * ``GET`` on a single-resource path (trailing ``{param}``)  -> ``get``
    * ``POST`` -> ``create``, ``PUT`` -> ``update``, etc.
    * If the operation has an ``operation_id`` that constitutes a sensible
      single-word verb it is preferred.
    """
    if operation.method == HTTPMethod.GET:
        return "get" if not _is_collection_endpoint(operation.path) else "list"

    verb = METHOD_TO_VERB.get(operation.method)
    if verb is not None:
        return verb

    # Fallback for any unmapped method.
    return operation.method.value


def _is_collection_endpoint(path: str) -> bool:
    """Return ``True`` when *path* represents a collection.

    A collection endpoint ends with a static resource name rather than a
    path parameter (e.g. ``/pets`` vs ``/pets/{petId}``).
    """
    segments = [s for s in path.split("/") if s]
    if not segments:
        return True
    last = segments[-1]
    return not (last.startswith("{") and last.endswith("}"))


# ---------------------------------------------------------------------------
# Operation grouping
# ---------------------------------------------------------------------------


def _group_operations(
    operations: list[APIOperation],
    path_map: dict[str, str],
) -> dict[tuple[str, ...], list[tuple[APIOperation, str]]]:
    """Group operations by their CLI command parts (resource segments).

    Each operation's path is looked up in *path_map* to get the transformed
    path, which is then split into command parts via
    :func:`~specli.generator.path_rules.path_to_command_parts` (path
    parameter segments like ``{id}`` are stripped).

    Args:
        operations: All :class:`~specli.models.APIOperation` instances
            from the parsed spec.
        path_map: Mapping from original API paths to transformed paths, as
            returned by :func:`~specli.generator.path_rules.apply_path_rules`.

    Returns:
        A dict mapping a tuple of command-path segments (e.g.,
        ``("users", "settings")``) to a list of ``(operation, original_path)``
        pairs that belong under that sub-command group.
    """
    groups: dict[tuple[str, ...], list[tuple[APIOperation, str]]] = defaultdict(list)

    for op in operations:
        if op.path not in path_map:
            continue  # Excluded by path rules (e.g. include_prefix).
        transformed = path_map[op.path]
        parts = tuple(path_to_command_parts(transformed))
        if not parts:
            # Operations that reduce to the root (rare) get a synthetic group.
            parts = ("root",)
        groups[parts].append((op, op.path))

    return dict(groups)


# ---------------------------------------------------------------------------
# Sub-app tree construction
# ---------------------------------------------------------------------------


def _ensure_sub_apps(
    root: typer.Typer,
    parts: tuple[str, ...],
    registry: dict[tuple[str, ...], typer.Typer],
    group_help: dict[tuple[str, ...], str] | None = None,
) -> typer.Typer:
    """Lazily create Typer sub-apps for every prefix of *parts*.

    Walks through the *parts* tuple depth-by-depth (e.g., for
    ``("users", "settings")`` it ensures both ``("users",)`` and
    ``("users", "settings")`` exist).  Missing sub-apps are created and
    registered both in the *registry* dict and as children of their parent
    Typer app via :meth:`typer.Typer.add_typer`.

    Args:
        root: The top-level :class:`typer.Typer` application.
        parts: Tuple of command-path segments identifying the target
            sub-app (e.g., ``("users", "settings")``).
        registry: Mutable mapping from segment tuples to their
            :class:`typer.Typer` instances, shared across all calls.
        group_help: Optional mapping from segment tuples to help strings
            derived from OpenAPI tags or operation summaries.

    Returns:
        The :class:`typer.Typer` instance for the full *parts* tuple --
        this is the app onto which leaf commands should be registered.
    """
    if not parts:
        return root

    # Walk through each prefix depth and create sub-apps lazily.
    for depth in range(1, len(parts) + 1):
        prefix = parts[:depth]
        if prefix in registry:
            continue

        parent_prefix = prefix[:-1]
        parent_app = registry.get(parent_prefix, root)

        help_text = (group_help or {}).get(prefix) or _humanize_group(prefix[-1])

        sub = typer.Typer(
            name=prefix[-1],
            help=help_text,
            no_args_is_help=True,
        )
        parent_app.add_typer(sub)
        registry[prefix] = sub

    return registry[parts]


# ---------------------------------------------------------------------------
# Dynamic command function builder
# ---------------------------------------------------------------------------


def _build_command_function(
    operation: APIOperation,
    original_path: str,
    request_callback: Optional[Callable[..., Any]],
) -> Callable[..., Any]:
    """Dynamically generate a Typer-compatible function for *operation*.

    Constructs a Python function whose signature mirrors the operation's
    parameters: path parameters become positional :func:`typer.Argument`
    values, query/header/cookie parameters become ``--option`` flags via
    :func:`typer.Option`, and operations with a request body get an
    additional ``--body`` / ``-b`` option.

    The function source is built as a string, compiled, and executed into
    a namespace so that :mod:`inspect` (which Typer relies on) can read its
    signature.  When invoked, the generated function collects all parameter
    values, resolves ``@filename`` references in ``--body``, and delegates
    to the *request_callback* (or prints a dry-run summary).

    Args:
        operation: The :class:`~specli.models.APIOperation` to generate
            a command for.
        original_path: The original (un-transformed) API path, passed through
            to the callback so it can construct the real HTTP request URL.
        request_callback: The callback to invoke when the command runs, or
            ``None`` for dry-run mode.

    Returns:
        A callable suitable for registration via
        :meth:`typer.Typer.command`.
    """
    # Map every parameter to a Typer descriptor.
    param_descriptors: list[dict[str, Any]] = [
        map_parameter_to_typer(p) for p in operation.parameters
    ]

    # Add --body when the operation expects a request body, or when the
    # HTTP method typically carries one (POST/PUT/PATCH).  Many specs omit
    # the requestBody definition even though the endpoint consumes JSON.
    methods_with_body = {"post", "put", "patch"}
    has_body = (
        operation.request_body is not None
        or operation.method.value in methods_with_body
    )

    # When the request body has a schema with properties, generate
    # individual --field-name options for each property.  The --body/-b
    # option is kept as a raw-JSON override.
    body_field_descriptors: list[dict[str, Any]] = []
    body_schema_required: list[str] = []
    if (
        has_body
        and operation.request_body is not None
        and operation.request_body.schema_
        and operation.request_body.schema_.get("properties")
    ):
        body_field_descriptors = build_body_field_options(
            operation.request_body.schema_
        )
        body_schema_required = list(
            operation.request_body.schema_.get("required", [])
        )

    if has_body:
        param_descriptors.append(build_body_option())
    # Append body field descriptors after the --body option.
    param_descriptors.extend(body_field_descriptors)

    # Determine the preferred content type for the request body.
    body_content_type: str | None = None
    if operation.request_body and operation.request_body.content_types:
        body_content_type = operation.request_body.content_types[0]
    elif has_body:
        # No schema defined but method implies a body — default to JSON.
        body_content_type = "application/json"

    # We need to build a real function object whose signature Typer can
    # inspect.  Typer reads parameter annotations and defaults from the
    # function via ``inspect.signature``.
    #
    # Strategy: construct the function source as a string, compile it,
    # and inject the correct default sentinel objects into its namespace.

    func_name = f"_cmd_{_slugify(original_path)}_{operation.method.value}"

    # --- Build the parameter list for the function signature. ---
    sig_parts: list[str] = []
    # Positional arguments first (path params), then keyword options.
    arguments = [d for d in param_descriptors if d["is_argument"]]
    options = [d for d in param_descriptors if not d["is_argument"]]

    namespace: dict[str, Any] = {}

    for idx, desc in enumerate(arguments):
        sentinel = f"_default_arg_{idx}"
        namespace[sentinel] = desc["default"]
        ann = f"_ann_arg_{idx}"
        namespace[ann] = desc["type"]
        sig_parts.append(
            f"{desc['name']}: {ann} = {sentinel}"
        )

    for idx, desc in enumerate(options):
        sentinel = f"_default_opt_{idx}"
        namespace[sentinel] = desc["default"]
        ann = f"_ann_opt_{idx}"
        namespace[ann] = desc["type"]
        sig_parts.append(
            f"{desc['name']}: {ann} = {sentinel}"
        )

    sig = ", ".join(sig_parts)

    # --- Build the function body. ---
    body_lines = [
        "    params = {}",
    ]

    for desc in param_descriptors:
        py_name = desc["name"]
        orig_name = desc["original_name"]
        if orig_name == "__body__" or orig_name.startswith("__body__."):
            continue
        body_lines.append(
            f"    params[{orig_name!r}] = {py_name}"
        )

    if body_field_descriptors:
        # Assemble body dict from individual field values, then merge
        # with --body JSON if provided (--body overrides fields).
        body_lines.append("    _body_fields = {}")
        for desc in body_field_descriptors:
            py_name = desc["name"]
            # Extract the original property name from __body__.prop_name
            prop_name = desc["original_name"].split(".", 1)[1]
            field_type = desc.get("body_field_type", "string")
            if field_type in ("object", "array"):
                # Complex types need JSON parsing back to native.
                body_lines.append(
                    f"    if {py_name} is not None:"
                )
                body_lines.append(
                    f"        try:"
                )
                body_lines.append(
                    f"            _body_fields[{prop_name!r}] = _json.loads({py_name})"
                )
                body_lines.append(
                    f"        except (ValueError, TypeError):"
                )
                body_lines.append(
                    f"            _body_fields[{prop_name!r}] = {py_name}"
                )
            else:
                body_lines.append(
                    f"    if {py_name} is not None:"
                    f" _body_fields[{prop_name!r}] = {py_name}"
                )
        # Merge with --body JSON override.
        body_lines.append("    if body is not None:")
        body_lines.append("        _raw = _resolve_body(body)")
        body_lines.append("        _merged = _body_fields.copy()")
        body_lines.append("        _merged.update(_json.loads(_raw))")
        body_lines.append("        resolved_body = _json.dumps(_merged)")
        body_lines.append("    elif _body_fields:")
        # Validate required fields are present before sending.
        if body_schema_required:
            req_list_repr = repr(body_schema_required)
            body_lines.append(
                f"        _missing = [f for f in {req_list_repr}"
                f" if f not in _body_fields]"
            )
            body_lines.append("        if _missing:")
            body_lines.append(
                "            _ctx = _click.get_current_context()"
            )
            body_lines.append(
                "            _typer.echo(_ctx.get_help())"
            )
            body_lines.append(
                "            _typer.echo("
                "'\\nError: missing required body fields: '"
                " + ', '.join(_missing) +"
                "'. Use individual flags or --body JSON.', err=True)"
            )
            body_lines.append("            raise _typer.Exit(code=1)")
        body_lines.append("        resolved_body = _json.dumps(_body_fields)")
        body_lines.append("    else:")
        # Validate required fields when nothing was provided.
        if body_schema_required:
            body_lines.append(
                f"        _ctx = _click.get_current_context()"
            )
            body_lines.append(
                f"        _typer.echo(_ctx.get_help())"
            )
            _req_names = ", ".join(body_schema_required)
            body_lines.append(
                f"        _typer.echo("
                f"'\\nError: missing required body fields: {_req_names}."
                f" Use individual flags or --body JSON.', err=True)"
            )
            body_lines.append("        raise _typer.Exit(code=1)")
        else:
            body_lines.append("        resolved_body = None")
    elif has_body:
        body_lines.append("    resolved_body = _resolve_body(body)")
    else:
        body_lines.append("    resolved_body = None")

    body_lines.append(
        "    return _dispatch("
        f"        {operation.method.value!r},"
        f"        {original_path!r},"
        "        params,"
        "        resolved_body,"
        f"        {body_content_type!r},"
        "    )"
    )

    func_body = "\n".join(body_lines)
    source = f"def {func_name}({sig}):\n{func_body}\n"

    # --- Inject helpers into the namespace. ---
    namespace["_resolve_body"] = _resolve_body
    namespace["_dispatch"] = _make_dispatch(request_callback)
    namespace["_json"] = json
    namespace["_typer"] = typer
    namespace["_click"] = click

    code = compile(source, f"<specli:{original_path}>", "exec")
    exec(code, namespace)  # noqa: S102 -- controlled code generation
    fn = namespace[func_name]

    # Attach metadata for Typer / documentation.
    fn.__doc__ = _build_help_text(operation)
    fn.__name__ = func_name
    fn.__qualname__ = func_name

    return fn


# ---------------------------------------------------------------------------
# Dispatch / callback helper
# ---------------------------------------------------------------------------


def _make_dispatch(
    callback: Optional[Callable[..., Any]],
) -> Callable[..., Any]:
    """Return a dispatch function that either calls *callback* or prints a dry-run summary.

    Args:
        callback: The user-supplied request callback, or ``None`` for
            dry-run mode where the HTTP method, path, parameters, and body
            are printed to stdout instead.

    Returns:
        A function with signature
        ``(method, path, params, body, content_type) -> Any``
        that can be injected into dynamically generated command functions.
    """

    if callback is not None:

        def _dispatch(
            method: str,
            path: str,
            params: dict[str, Any],
            body: Optional[str],
            content_type: Optional[str] = None,
        ) -> Any:
            return callback(method, path, params, body, content_type)

    else:

        def _dispatch(
            method: str,
            path: str,
            params: dict[str, Any],
            body: Optional[str],
            content_type: Optional[str] = None,
        ) -> None:
            summary = f"{method.upper()} {path}"
            if params:
                non_none = {k: v for k, v in params.items() if v is not None}
                if non_none:
                    summary += f"\n  params: {json.dumps(non_none, default=str)}"
            if body:
                summary += f"\n  body: {body[:200]}"
            typer.echo(summary)

    return _dispatch


# ---------------------------------------------------------------------------
# Body resolution
# ---------------------------------------------------------------------------


def _resolve_body(raw: Optional[str]) -> Optional[str]:
    """Resolve a ``--body`` value, supporting ``@filename`` file references.

    If *raw* starts with ``@``, the remainder is treated as a file path
    and its contents are read as UTF-8 text.  Otherwise *raw* is returned
    as-is (assumed to be an inline JSON string).

    Args:
        raw: The raw ``--body`` value from the CLI, or ``None`` if not
            provided.

    Returns:
        The resolved body string, or ``None`` if *raw* was ``None``.

    Raises:
        typer.Exit: If *raw* starts with ``@`` but the referenced file
            does not exist (exits with code 1).
    """
    if raw is None:
        return None
    if raw.startswith("@"):
        file_path = Path(raw[1:])
        if not file_path.is_file():
            typer.echo(f"Error: body file not found: {file_path}", err=True)
            raise typer.Exit(code=1)
        return file_path.read_text(encoding="utf-8")
    return raw


# ---------------------------------------------------------------------------
# Group description helpers
# ---------------------------------------------------------------------------


def _extract_tag_descriptions(spec: ParsedSpec) -> dict[str, str]:
    """Extract tag descriptions from the OpenAPI spec's top-level ``tags`` array.

    Args:
        spec: The :class:`~specli.models.ParsedSpec` whose ``raw_spec``
            is inspected for the ``tags`` array.

    Returns:
        A dict mapping **lowercase** tag name to its description string.
        Tags without a description are omitted.
    """
    raw = (spec.raw_spec or {}).get("tags", [])
    return {
        t["name"].lower(): t["description"]
        for t in raw
        if isinstance(t, dict) and t.get("name") and t.get("description")
    }


def _build_group_help(
    groups: dict[tuple[str, ...], list[tuple[APIOperation, str]]],
    tag_descriptions: dict[str, str],
) -> dict[tuple[str, ...], str]:
    """Derive a help string for each command group.

    Uses the following priority order (highest wins):

    1. **OpenAPI tag description** -- if the operations in a group share a tag
       whose name matches the group segment (or all operations share a single
       tagged description).
    2. **Inferred from summaries** -- when operation summaries follow a
       "Verb Resource" pattern (e.g., "List Campaigns", "Create Campaign"),
       the common noun is extracted to produce "Manage campaigns."
    3. **Humanized group name** -- handled as a fallback in
       :func:`_ensure_sub_apps` via :func:`_humanize_group`.

    Args:
        groups: Mapping from command-part tuples to lists of
            ``(operation, original_path)`` pairs, as produced by
            :func:`_group_operations`.
        tag_descriptions: Lowercase tag-name to description mapping, as
            produced by :func:`_extract_tag_descriptions`.

    Returns:
        A dict mapping command-part tuples to their help strings.  Groups
        that fall through to the humanized fallback are **not** included.
    """
    result: dict[tuple[str, ...], str] = {}

    for parts, op_entries in groups.items():
        group_name = parts[-1]
        operations = [op for op, _ in op_entries]

        # 1. Try OpenAPI tag description.
        tag_desc = _tag_description_for_group(operations, tag_descriptions, group_name)
        if tag_desc:
            result[parts] = tag_desc
            continue

        # 2. Infer from operation summaries.
        inferred = _infer_group_description(operations, group_name)
        if inferred:
            result[parts] = inferred
            continue

        # 3. Fallback handled by _humanize_group in _ensure_sub_apps.

    return result


def _tag_description_for_group(
    operations: list[APIOperation],
    tag_descriptions: dict[str, str],
    group_name: str,
) -> str | None:
    """Return a tag description if operations share a tag that matches the group."""
    # Direct match: tag name matches group name.
    desc = tag_descriptions.get(group_name.lower())
    if desc:
        return desc

    # Check if all operations share a single tag that has a description.
    tag_sets = [set(op.tags) for op in operations if op.tags]
    if tag_sets:
        common = tag_sets[0]
        for ts in tag_sets[1:]:
            common &= ts
        for tag in common:
            desc = tag_descriptions.get(tag.lower())
            if desc:
                return desc

    return None


def _infer_group_description(
    operations: list[APIOperation],
    group_name: str,
) -> str | None:
    """Infer a group description from operation summaries.

    Looks for a common resource noun across summaries like
    "List Campaigns", "Create Campaign", "Delete Campaign" → "Manage campaigns."
    """
    summaries = [op.summary for op in operations if op.summary]
    if not summaries:
        return None

    # If there's exactly one operation, use its summary directly.
    if len(summaries) == 1:
        return summaries[0]

    # Extract the last word(s) from each summary as candidate resource nouns.
    # Summaries typically follow "Verb Resource" pattern.
    nouns: list[str] = []
    for s in summaries:
        words = s.strip().split()
        if len(words) >= 2:
            # Take everything after the first word (the verb).
            nouns.append(" ".join(words[1:]).rstrip("."))

    if not nouns:
        return None

    # Find the most common noun (case-insensitive).
    from collections import Counter

    counts = Counter(n.lower() for n in nouns)
    most_common_noun, freq = counts.most_common(1)[0]

    # Only use if it appears in at least half the operations.
    if freq >= len(summaries) / 2:
        return f"Manage {most_common_noun.lower()}."

    # Fallback: use the first description if available.
    descs = [op.description for op in operations if op.description]
    if descs:
        first = descs[0].split("\n", 1)[0]
        return textwrap.shorten(first, width=80)

    return None


def _humanize_group(name: str) -> str:
    """Turn a group slug into a readable label.

    ``"generation-requests"`` → ``"Generation requests."``
    """
    return name.replace("-", " ").replace("_", " ").capitalize() + "."


# ---------------------------------------------------------------------------
# Help / display helpers
# ---------------------------------------------------------------------------


def _build_help_text(operation: APIOperation) -> str:
    """Compose a CLI help string for *operation*.

    Combines the deprecation flag, summary, and description into a single
    string suitable for Typer's ``help`` parameter.  Falls back to
    ``"METHOD /path"`` when neither summary nor description is available.

    Args:
        operation: The :class:`~specli.models.APIOperation` to
            describe.

    Returns:
        A help string, potentially multi-line when both summary and
        description are present.
    """
    parts: list[str] = []

    if operation.deprecated:
        parts.append("[DEPRECATED]")

    if operation.summary:
        parts.append(operation.summary)
    elif operation.description:
        # Use the first sentence of the description when summary is missing.
        first_line = operation.description.split("\n", 1)[0]
        parts.append(textwrap.shorten(first_line, width=80))

    if operation.description and operation.summary:
        # Append full description below the summary.
        parts.append("")
        parts.append(operation.description)

    return "\n".join(parts) if parts else f"{operation.method.value.upper()} {operation.path}"


def _slugify(value: str) -> str:
    """Turn *value* into a safe identifier slug."""
    result = value.lower()
    result = result.replace(" ", "_").replace("-", "_")
    result = "".join(c for c in result if c.isalnum() or c == "_")
    result = result.strip("_") or "api"
    return result
