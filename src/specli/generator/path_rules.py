"""Path transformation rules for CLI command generation.

This module converts raw API paths (e.g., ``/api/v1/users/{id}``) into clean
CLI command structures by applying a configurable pipeline of transformations
defined in a :class:`~specli.models.PathRulesConfig`.

The transformation pipeline, in order of precedence (highest wins):

1. **Collapse** -- Direct path-to-command mappings that bypass all other rules.
   Useful for irregular or deeply nested endpoints that should map to a flat
   command name.
2. **Strip prefix** -- Either an explicit ``strip_prefix`` string or an
   auto-detected longest common prefix across all paths.
3. **Keep** -- Re-inserts selected segments that were removed during prefix
   stripping (e.g., keep a version segment like ``v2``).
4. **Skip segments** -- Removes unwanted segments wherever they appear
   (e.g., remove ``internal`` from every path).

After transformation, :func:`path_to_command_parts` splits the result into
CLI sub-command segments, stripping path parameter placeholders (``{id}``,
``{user_id}``, etc.) since those become positional arguments rather than
sub-commands.
"""

from __future__ import annotations

from specli.models import PathRulesConfig


def apply_path_rules(paths: list[str], rules: PathRulesConfig) -> dict[str, str]:
    """Apply path transformation rules to convert API paths into CLI command paths.

    Processes every path through the rule pipeline described in the module
    docstring: collapse, prefix stripping (explicit or auto-detected), keep,
    and skip.  The result is a mapping from original paths to their
    transformed equivalents, ready for :func:`path_to_command_parts`.

    Args:
        paths: List of original API paths (e.g.,
            ``["/api/v1/users", "/api/v1/users/{id}"]``).
        rules: A :class:`~specli.models.PathRulesConfig` instance
            controlling which transformations to apply.

    Returns:
        A dict mapping each original path to its transformed path (e.g.,
        ``{"/api/v1/users": "/users", "/api/v1/users/{id}": "/users/{id}"}``).
        Collapsed paths are included as-is from the ``rules.collapse`` map.

    Example::

        from specli.models import PathRulesConfig

        rules = PathRulesConfig(auto_strip_prefix=True, skip_segments=["internal"])
        result = apply_path_rules(
            ["/api/v1/internal/users", "/api/v1/internal/tasks"],
            rules,
        )
        # result == {
        #     "/api/v1/internal/users": "/users",
        #     "/api/v1/internal/tasks": "/tasks",
        # }
    """
    if not paths:
        return {}

    # Filter to only include paths matching the prefix, if configured.
    if rules.include_prefix:
        prefixes = rules.include_prefix if isinstance(rules.include_prefix, list) else [rules.include_prefix]
        paths = [p for p in paths if any(p.startswith(pfx) for pfx in prefixes)]

    result: dict[str, str] = {}

    # Pre-compute the common prefix once (used when auto_strip is enabled
    # and no explicit strip_prefix is set).
    auto_prefix = ""
    if rules.auto_strip_prefix and rules.strip_prefix is None:
        auto_prefix = find_common_prefix(paths)

    # Pre-compute which segments were removed by the prefix so that _apply_keep
    # knows what to look for.
    stripped_segments: list[str] = []
    if rules.strip_prefix is not None:
        stripped_segments = _split_segments(rules.strip_prefix)
    elif auto_prefix:
        stripped_segments = _split_segments(auto_prefix)

    for path in paths:
        # --- 1. Collapse takes absolute precedence ----------------------
        if path in rules.collapse:
            collapsed = rules.collapse[path]
            # Ensure a leading slash for consistency.
            if not collapsed.startswith("/"):
                collapsed = "/" + collapsed
            result[path] = collapsed
            continue

        # --- 2. strip_prefix overrides auto_strip -----------------------
        if rules.strip_prefix is not None:
            transformed = _strip_prefix(path, rules.strip_prefix)
        elif rules.auto_strip_prefix and auto_prefix:
            transformed = _strip_prefix(path, auto_prefix)
        else:
            transformed = path

        # --- 3. Re-insert kept segments ---------------------------------
        if rules.keep:
            transformed = _apply_keep(transformed, rules.keep, stripped_segments)

        # --- 4. Remove skip segments ------------------------------------
        if rules.skip_segments:
            transformed = _apply_skip_segments(transformed, rules.skip_segments)

        result[path] = transformed

    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def find_common_prefix(paths: list[str]) -> str:
    """Find the longest common path prefix across all *paths*.

    Only strips at segment boundaries (never mid-segment).  A single path
    returns an empty prefix since there is nothing to compare against.
    The prefix is also shortened if it would completely consume any path --
    every path must retain at least one segment after stripping.

    Args:
        paths: List of API path strings (e.g.,
            ``["/api/v1/users", "/api/v1/tasks"]``).

    Returns:
        The longest common prefix as a path string with a leading ``/``
        (e.g., ``"/api/v1"``), or an empty string if there is no common
        prefix.

    Example::

        >>> find_common_prefix(["/api/v1/users", "/api/v1/tasks"])
        '/api/v1'
        >>> find_common_prefix(["/api/v1/users", "/api/v2/tasks"])
        '/api'
        >>> find_common_prefix(["/users", "/tasks"])
        ''
    """
    if not paths:
        return ""

    # Split each path into segments, ignoring leading empty string from
    # the leading ``/``.
    split_paths = [_split_segments(p) for p in paths]

    # Handle edge-case: a single path has no *common* prefix to strip
    # because there is nothing to compare against.
    if len(split_paths) == 1:
        return ""

    prefix_segments: list[str] = []
    for parts in zip(*split_paths):
        # All segments at this depth must match.
        if len(set(parts)) == 1:
            prefix_segments.append(parts[0])
        else:
            break

    if not prefix_segments:
        return ""

    # Trim the prefix so that no path is completely consumed.  If the
    # prefix equals (or is longer than) any path, shorten it by one
    # segment at a time until every path retains at least one segment
    # after stripping.
    while prefix_segments:
        prefix_len = len(prefix_segments)
        if all(len(segs) > prefix_len for segs in split_paths):
            break
        prefix_segments.pop()

    if not prefix_segments:
        return ""

    return "/" + "/".join(prefix_segments)


def _is_path_param(segment: str) -> bool:
    """Return ``True`` if *segment* is a path parameter (e.g., ``{id}``)."""
    return segment.startswith("{") and segment.endswith("}")


def _split_segments(path: str) -> list[str]:
    """Split a path into non-empty segments.

    ``"/api/v1/users"`` -> ``["api", "v1", "users"]``
    ``"/"``             -> ``[]``
    """
    return [s for s in path.split("/") if s]


def _strip_prefix(path: str, prefix: str) -> str:
    """Strip *prefix* from *path*. Returns path with leading ``/``.

    If *path* does not start with *prefix*, it is returned unchanged.
    """
    if not prefix:
        return path

    prefix_segments = _split_segments(prefix)
    path_segments = _split_segments(path)

    # Verify the path actually starts with the prefix segments.
    if path_segments[: len(prefix_segments)] != prefix_segments:
        return path

    remaining = path_segments[len(prefix_segments) :]
    if not remaining:
        return "/"
    return "/" + "/".join(remaining)


def _apply_keep(
    path: str,
    keep_segments: list[str],
    stripped_segments: list[str],
) -> str:
    """Re-insert *keep_segments* that were removed during prefix stripping.

    Only segments that were actually part of the stripped prefix are
    re-inserted, and they are prepended in the order they originally
    appeared within the prefix.
    """
    # Determine which kept segments were actually stripped.
    to_reinsert = [seg for seg in stripped_segments if seg in keep_segments]

    if not to_reinsert:
        return path

    current_segments = _split_segments(path)
    result_segments = to_reinsert + current_segments

    if not result_segments:
        return "/"
    return "/" + "/".join(result_segments)


def _apply_skip_segments(path: str, skip_segments: list[str]) -> str:
    """Remove *skip_segments* wherever they appear in *path*."""
    skip_set = set(skip_segments)
    segments = _split_segments(path)
    remaining = [seg for seg in segments if seg not in skip_set]

    if not remaining:
        return "/"
    return "/" + "/".join(remaining)


def path_to_command_parts(transformed_path: str) -> list[str]:
    """Convert a transformed path to CLI sub-command segments.

    Splits the path on ``/`` and removes path parameter segments (those
    wrapped in braces like ``{id}``).  Path parameters are not sub-commands;
    they become positional arguments on the leaf command instead.

    Args:
        transformed_path: A path string after rule transformation (e.g.,
            ``"/users/{id}/settings"``).

    Returns:
        A list of static resource segments (e.g., ``["users", "settings"]``).
        Returns an empty list for the root path ``"/"``.

    Example::

        >>> path_to_command_parts("/users/{id}/settings")
        ['users', 'settings']
        >>> path_to_command_parts("/users")
        ['users']
        >>> path_to_command_parts("/")
        []
    """
    segments = _split_segments(transformed_path)
    # Strip segments that are path parameters (wrapped in braces).
    return [seg for seg in segments if not _is_path_param(seg)]
