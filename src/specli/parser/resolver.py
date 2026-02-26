"""Resolve ``$ref`` JSON Reference pointers in OpenAPI specifications.

OpenAPI documents commonly use ``$ref`` pointers (e.g.,
``{"$ref": "#/components/schemas/Pet"}``) to avoid repetition.  This module
performs a recursive deep-copy traversal of the spec, replacing every ``$ref``
with the actual referenced object.

Only **internal** references (those starting with ``#/``) are supported.
External file or URL references will raise
:class:`~specli.exceptions.SpecParseError`.

Circular references are detected via a ``seen`` set and left unresolved to
prevent infinite recursion.  This means a schema that references itself (common
in tree-like structures) will retain its ``$ref`` dict at the cycle point.

The single public function is :func:`resolve_refs`.
"""

from __future__ import annotations

import copy
from typing import Any

from specli.exceptions import SpecParseError


def resolve_refs(spec: dict[str, Any]) -> dict[str, Any]:
    """Resolve all ``$ref`` JSON Reference pointers in the spec.

    Creates a deep copy of the input and recursively replaces every
    ``{"$ref": "#/..."}`` dict with the object it points to.  Only internal
    references (those starting with ``#/``) are supported; external file or
    URL references raise :class:`~specli.exceptions.SpecParseError`.

    Circular references are detected and left unresolved (the ``$ref`` dict
    is kept as-is at the cycle point) to prevent infinite recursion.

    Args:
        spec: The raw OpenAPI spec dictionary, as returned by
            :func:`~specli.parser.loader.load_spec`.

    Returns:
        A **new** dictionary (deep copy) with all resolvable ``$ref``
        pointers replaced by their target objects.

    Raises:
        SpecParseError: If a ``$ref`` points to a non-existent path within
            the spec, or if an external (non-``#/``) reference is encountered.

    Example::

        raw = load_spec("petstore.yaml")
        resolved = resolve_refs(raw)
        # resolved["paths"]["/pets"]["get"]["responses"]["200"]["content"]
        # now contains the inlined schema instead of a $ref pointer.
    """
    root = copy.deepcopy(spec)
    return _deep_resolve(root, root, seen=None)


def _resolve_ref(ref: str, root: dict[str, Any]) -> Any:
    """Resolve a single ``$ref`` string against the root spec.

    Parses JSON Pointer references like ``#/components/schemas/Pet`` and
    navigates the root dict to locate the referenced value.  Handles
    RFC 6901 JSON Pointer escaping (``~0`` for ``~``, ``~1`` for ``/``).

    Args:
        ref: The ``$ref`` string (e.g., ``"#/components/schemas/Pet"``).
        root: The root spec dictionary to resolve against.

    Returns:
        The value found at the referenced path.

    Raises:
        SpecParseError: If the reference is external (does not start with
            ``#/``), or if any segment in the pointer path does not exist
            in the document.
    """
    if not ref.startswith("#/"):
        raise SpecParseError(
            f"External $ref not supported: {ref}. "
            "Only internal references (#/...) are handled."
        )

    # Strip '#/' prefix and split into path segments
    path_str = ref[2:]
    segments = path_str.split("/")

    # Navigate through the spec following the path
    current: Any = root
    for segment in segments:
        # Handle JSON Pointer escaping (RFC 6901)
        segment = segment.replace("~1", "/").replace("~0", "~")

        if isinstance(current, dict):
            if segment not in current:
                raise SpecParseError(
                    f"Cannot resolve $ref '{ref}': "
                    f"key '{segment}' not found at path"
                )
            current = current[segment]
        elif isinstance(current, list):
            try:
                index = int(segment)
                current = current[index]
            except (ValueError, IndexError) as exc:
                raise SpecParseError(
                    f"Cannot resolve $ref '{ref}': "
                    f"invalid array index '{segment}'"
                ) from exc
        else:
            raise SpecParseError(
                f"Cannot resolve $ref '{ref}': "
                f"cannot navigate into {type(current).__name__}"
            )

    return current


def _deep_resolve(obj: Any, root: dict[str, Any], seen: set[str] | None = None) -> Any:
    """Recursively resolve all ``$ref`` pointers within *obj*.

    Walks dicts and lists depth-first.  When a dict containing a ``$ref``
    key is found, the reference is resolved via :func:`_resolve_ref` and the
    result is recursively processed in turn (since resolved targets may
    themselves contain ``$ref`` pointers).

    Circular references are detected via a ``seen`` set of ``$ref`` strings
    currently on the resolution stack.  When a cycle is detected, the
    ``$ref`` dict is returned unmodified to break the recursion.  A **copy**
    of ``seen`` is created at each branch so that parallel sibling references
    do not interfere with each other.

    Args:
        obj: The current node to resolve -- may be a dict (potentially a
            ``$ref``), a list, or a scalar value.
        root: The root spec dictionary, used as the lookup target for all
            ``$ref`` resolution.
        seen: Set of ``$ref`` strings currently being resolved on this
            call stack.  ``None`` on the initial call (an empty set is
            created internally).

    Returns:
        The resolved object.  Dicts and lists are new objects; scalars are
        returned as-is.
    """
    if seen is None:
        seen = set()

    if isinstance(obj, dict):
        # Check if this dict IS a $ref
        if "$ref" in obj:
            ref = obj["$ref"]
            if ref in seen:
                # Circular reference -- return the $ref dict unresolved
                return obj
            seen = seen | {ref}  # Create new set to allow parallel branches
            resolved = _resolve_ref(ref, root)
            # The resolved value may itself contain $refs
            return _deep_resolve(resolved, root, seen)

        # Recursively resolve all values in the dict
        return {key: _deep_resolve(value, root, seen) for key, value in obj.items()}

    if isinstance(obj, list):
        return [_deep_resolve(item, root, seen) for item in obj]

    # Scalars pass through unchanged
    return obj
