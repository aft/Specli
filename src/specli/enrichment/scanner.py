"""AST-based source scanner for extracting route documentation.

Walks Python files, parses them with :mod:`ast`, and extracts docstrings,
parameter documentation, and Pydantic ``Field(description=...)`` metadata
from FastAPI/Starlette route handler functions.

The scanner recognises ``@router.get("/path")``, ``@app.post("/path")``,
and similar decorator patterns. It resolves ``APIRouter(prefix=...)``
and ``app.include_router(router, prefix=...)`` assignments to compute
full endpoint paths. It also parses Google-style ``Args:`` sections
from handler docstrings to extract per-parameter descriptions.

See :class:`SourceScanner` for the main entry point.
"""

from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pathspec


@dataclass
class RouteDoc:
    """Documentation extracted from a single route handler function.

    Populated by :class:`SourceScanner` when it finds a function
    decorated with an HTTP-method route (e.g. ``@router.get(...)``).
    Each instance maps to one ``(method, path)`` operation in the
    OpenAPI spec.

    Attributes:
        method: HTTP method in lowercase (``get``, ``post``, etc.).
        path: Full API path including any router prefix
            (e.g. ``/api/campaigns/{campaign_id}``).
        summary: First line of the handler's docstring, or ``None``.
        description: Full docstring text, or ``None``.
        param_docs: Mapping of parameter name to its description,
            extracted from the ``Args:`` section and/or Pydantic
            ``Field(description=...)`` values.
        module_doc: Module-level docstring of the file containing
            the handler, or ``None``.
        source_file: Filesystem path to the source file (for
            debugging and logging).
    """

    method: str  # "get", "post", etc.
    path: str  # "/api/campaigns/{campaign_id}"
    summary: Optional[str] = None  # First line of docstring
    description: Optional[str] = None  # Full docstring
    param_docs: dict[str, str] = field(default_factory=dict)  # param_name → description
    module_doc: Optional[str] = None  # Module-level docstring
    source_file: str = ""  # For debug/logging


# HTTP methods recognised on decorator attributes.
_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options", "trace"})


def _load_gitignore(root: Path) -> pathspec.PathSpec | None:
    """Load ``.gitignore`` from *root* if it exists, returning a PathSpec matcher.

    Args:
        root: Directory in which to look for a ``.gitignore`` file.

    Returns:
        A :class:`pathspec.PathSpec` compiled from the gitignore rules,
        or ``None`` if no ``.gitignore`` file is present.
    """
    gitignore = root / ".gitignore"
    if not gitignore.is_file():
        return None
    lines = gitignore.read_text(encoding="utf-8", errors="replace").splitlines()
    return pathspec.PathSpec.from_lines("gitignore", lines)


class SourceScanner:
    """Scan Python source files for route handler documentation.

    Walks a directory tree, parses each ``.py`` file with :mod:`ast`,
    and extracts :class:`RouteDoc` instances for every function
    decorated with an HTTP-method route decorator (e.g.
    ``@router.get("/path")``).

    The scanner resolves ``APIRouter(prefix=...)`` and
    ``app.include_router(router, prefix=...)`` to compute full
    endpoint paths, and extracts parameter documentation from
    Google-style ``Args:`` docstring sections and Pydantic
    ``Field(description=...)`` annotations.
    """

    def scan(
        self,
        source_dir: str,
        include_patterns: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
    ) -> list[RouteDoc]:
        """Walk *source_dir* and extract route documentation.

        Discovers Python files using gitignore-compatible pattern
        matching (via :mod:`pathspec`), respecting ``.gitignore`` rules
        at the source root. Directories like ``__pycache__``,
        ``.git``, and ``.tox`` are always pruned.

        Files that cannot be parsed (``SyntaxError``,
        ``UnicodeDecodeError``) are silently skipped.

        Args:
            source_dir: Root directory to scan.
            include_patterns: Glob patterns to include.  Defaults to
                ``["**/*.py"]``.
            exclude_patterns: Glob patterns to exclude (e.g.
                ``["**/test_*"]``).

        Returns:
            List of :class:`RouteDoc` objects, one per discovered
            route handler, sorted by source file path.
        """
        root = Path(source_dir)
        if not root.is_dir():
            return []

        include = include_patterns or ["**/*.py"]
        exclude = exclude_patterns or []

        # Build pathspec matchers (gitignore-compatible pattern syntax).
        include_spec = pathspec.PathSpec.from_lines("gitignore", include)
        exclude_spec = pathspec.PathSpec.from_lines("gitignore", exclude) if exclude else None

        # Load .gitignore from the source root.
        gitignore_spec = _load_gitignore(root)

        # Directories that should always be pruned during traversal.
        always_skip = {"__pycache__", ".git", ".tox", ".mypy_cache", ".ruff_cache"}

        # Walk with directory pruning.
        py_files: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(str(root)):
            rel_dir = os.path.relpath(dirpath, str(root))

            # Prune always-skip directories in place.
            dirnames[:] = [
                d for d in dirnames
                if d not in always_skip
                and not (gitignore_spec and gitignore_spec.match_file(
                    (os.path.join(rel_dir, d) if rel_dir != "." else d) + "/",
                ))
            ]

            for fname in filenames:
                if not fname.endswith(".py"):
                    continue
                rel_path = os.path.join(rel_dir, fname) if rel_dir != "." else fname
                # Respect .gitignore.
                if gitignore_spec and gitignore_spec.match_file(rel_path):
                    continue
                # Check include patterns.
                if not include_spec.match_file(rel_path):
                    continue
                # Check explicit exclude patterns.
                if exclude_spec and exclude_spec.match_file(rel_path):
                    continue
                py_files.append(Path(dirpath) / fname)

        py_files.sort()

        results: list[RouteDoc] = []
        for path in sorted(py_files):
            if not path.is_file():
                continue
            try:
                docs = self._scan_file(path)
                results.extend(docs)
            except (SyntaxError, UnicodeDecodeError):
                # Skip files that can't be parsed.
                continue

        return results

    def _scan_file(self, path: Path) -> list[RouteDoc]:
        """Parse a single Python file and extract route docs.

        Reads the file, parses it with :func:`ast.parse`, resolves router
        prefixes, then iterates over all function definitions looking for
        HTTP-method route decorators.

        Args:
            path: Filesystem path to a ``.py`` file.

        Returns:
            List of :class:`RouteDoc` instances found in the file.

        Raises:
            SyntaxError: If the file contains invalid Python syntax.
            UnicodeDecodeError: If the file cannot be decoded as UTF-8.
        """
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(path))

        module_doc = ast.get_docstring(tree)

        # Resolve router prefixes: variable_name → prefix string.
        router_prefixes = self._resolve_router_prefixes(tree)

        # Also resolve include_router prefix overrides.
        include_prefixes = self._resolve_include_router_prefixes(tree)

        results: list[RouteDoc] = []

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            for decorator in node.decorator_list:
                route_info = self._parse_route_decorator(
                    decorator, router_prefixes, include_prefixes,
                )
                if route_info is None:
                    continue

                method, route_path = route_info
                docstring = ast.get_docstring(node)

                summary = None
                description = None
                param_docs: dict[str, str] = {}

                if docstring:
                    lines = docstring.strip().splitlines()
                    summary = lines[0].strip() if lines else None
                    description = docstring.strip()
                    param_docs = _parse_param_docs(docstring)

                # Also extract Pydantic Field descriptions from type hints.
                pydantic_docs = self._extract_pydantic_field_docs(tree, node)
                # Pydantic docs fill gaps in param_docs.
                for k, v in pydantic_docs.items():
                    if k not in param_docs:
                        param_docs[k] = v

                results.append(RouteDoc(
                    method=method,
                    path=route_path,
                    summary=summary,
                    description=description,
                    param_docs=param_docs,
                    module_doc=module_doc,
                    source_file=str(path),
                ))

        return results

    def _parse_route_decorator(
        self,
        decorator: ast.expr,
        router_prefixes: dict[str, str],
        include_prefixes: dict[str, str],
    ) -> tuple[str, str] | None:
        """Extract ``(method, full_path)`` from a route decorator, or ``None``.

        Recognises patterns like ``@router.get("/path")`` where the
        attribute name is one of :data:`_HTTP_METHODS`. Resolves the
        variable's prefix from *router_prefixes* or *include_prefixes*
        and prepends it to the route path.

        Args:
            decorator: An AST expression node from a function's
                decorator list.
            router_prefixes: Mapping of variable name to prefix from
                ``APIRouter(prefix=...)`` assignments.
            include_prefixes: Mapping of variable name to prefix from
                ``app.include_router(router, prefix=...)`` calls.

        Returns:
            A ``(method, full_path)`` tuple, or ``None`` if the
            decorator is not a recognised route decorator.
        """
        # We need: @something.get("/path") or @something.post("/path")
        if not isinstance(decorator, ast.Call):
            return None

        func = decorator.func
        if not isinstance(func, ast.Attribute):
            return None

        method = func.attr
        if method not in _HTTP_METHODS:
            return None

        # Extract the path argument (first positional arg).
        if not decorator.args:
            return None

        path_node = decorator.args[0]
        if not isinstance(path_node, ast.Constant) or not isinstance(path_node.value, str):
            return None

        route_path = path_node.value

        # Resolve the variable prefix (e.g., router = APIRouter(prefix="/api/v1")).
        var_name = None
        if isinstance(func.value, ast.Name):
            var_name = func.value.id

        prefix = ""
        if var_name:
            # Check include_router overrides first, then APIRouter(prefix=...).
            if var_name in include_prefixes:
                prefix = include_prefixes[var_name]
            elif var_name in router_prefixes:
                prefix = router_prefixes[var_name]

        full_path = prefix.rstrip("/") + "/" + route_path.lstrip("/") if prefix else route_path
        # Normalise double slashes.
        while "//" in full_path:
            full_path = full_path.replace("//", "/")

        return method, full_path

    def _resolve_router_prefixes(self, tree: ast.Module) -> dict[str, str]:
        """Find ``router = APIRouter(prefix="...")`` assignments.

        Walks the module AST looking for simple assignments where the
        right-hand side is a call to ``APIRouter(...)`` or
        ``Router(...)`` with a ``prefix`` keyword argument.

        Args:
            tree: The parsed AST module.

        Returns:
            Mapping of variable name to prefix string.
        """
        prefixes: dict[str, str] = {}

        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                continue

            var_name = node.targets[0].id
            call = node.value
            if not isinstance(call, ast.Call):
                continue

            # Check if it's APIRouter(...)
            func_name = _get_call_name(call)
            if func_name not in ("APIRouter", "Router"):
                continue

            # Extract prefix keyword argument.
            for kw in call.keywords:
                if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                    prefixes[var_name] = str(kw.value.value)
                    break

        return prefixes

    def _resolve_include_router_prefixes(self, tree: ast.Module) -> dict[str, str]:
        """Find ``app.include_router(router, prefix="...")`` calls.

        Walks the module AST looking for expression statements that call
        ``.include_router(variable, prefix="...")`` and records the
        prefix override for the given router variable.

        Args:
            tree: The parsed AST module.

        Returns:
            Mapping of router variable name to the overridden prefix.
        """
        prefixes: dict[str, str] = {}

        for node in ast.walk(tree):
            if not isinstance(node, ast.Expr):
                continue
            call = node.value
            if not isinstance(call, ast.Call):
                continue
            if not isinstance(call.func, ast.Attribute):
                continue
            if call.func.attr != "include_router":
                continue

            # First arg is the router variable.
            if not call.args or not isinstance(call.args[0], ast.Name):
                continue

            router_var = call.args[0].id
            for kw in call.keywords:
                if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                    prefixes[router_var] = str(kw.value.value)
                    break

        return prefixes

    def _extract_pydantic_field_docs(
        self,
        tree: ast.Module,
        func_node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> dict[str, str]:
        """Extract descriptions from Pydantic model fields referenced in the function.

        Inspects each of the function's parameter type annotations. When
        an annotation references a class defined in the same module, the
        class body is searched for ``Field(description=...)`` calls, and
        those descriptions are collected.

        Args:
            tree: The parsed AST module (used for class lookups).
            func_node: The function definition node whose parameters to
                inspect.

        Returns:
            Mapping of field name to its description string.
        """
        docs: dict[str, str] = {}

        # Collect all class definitions in the module for lookup.
        classes: dict[str, ast.ClassDef] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                classes[node.name] = node

        # Check function parameter annotations for class references.
        for arg in func_node.args.args:
            annotation = arg.annotation
            if annotation is None:
                continue

            class_name = None
            if isinstance(annotation, ast.Name):
                class_name = annotation.id
            elif isinstance(annotation, ast.Attribute):
                class_name = annotation.attr

            if class_name and class_name in classes:
                field_docs = self._extract_fields_from_class(classes[class_name])
                docs.update(field_docs)

        return docs

    def _extract_fields_from_class(self, cls: ast.ClassDef) -> dict[str, str]:
        """Extract ``Field(description=...)`` from class body assignments.

        Iterates over the class body looking for annotated assignments
        or plain assignments whose value is a ``Field(...)`` call with a
        ``description`` keyword argument.

        Args:
            cls: The class definition AST node to inspect.

        Returns:
            Mapping of field name to description string.
        """
        docs: dict[str, str] = {}

        for node in cls.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue

            # Get the field name.
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                field_name = node.target.id
                value = node.value
            elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                field_name = node.targets[0].id
                value = node.value
            else:
                continue

            if value is None or not isinstance(value, ast.Call):
                continue

            call_name = _get_call_name(value)
            if call_name != "Field":
                continue

            # Extract description keyword.
            for kw in value.keywords:
                if kw.arg == "description" and isinstance(kw.value, ast.Constant):
                    docs[field_name] = str(kw.value.value)
                    break

        return docs


def _get_call_name(call: ast.Call) -> str | None:
    """Return the simple name of a Call node's function."""
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _parse_param_docs(docstring: str) -> dict[str, str]:
    """Parse ``Args:`` or ``Parameters:`` section from a Google-style docstring.

    Recognises section headers ``Args:``, ``Arguments:``,
    ``Parameters:``, and ``Params:``. Parameter entries are expected to
    be indented lines of the form ``name: description`` or
    ``name (type): description``, with optional continuation lines at
    deeper indentation.

    Args:
        docstring: The full docstring text to parse.

    Returns:
        Mapping of parameter name to its description. Multi-line
        descriptions are joined with single spaces.

    Example::

        >>> _parse_param_docs('''Do something.
        ...
        ... Args:
        ...     name: The user's name.
        ...     age: How old they are.
        ... ''')
        {'name': "The user's name.", 'age': 'How old they are.'}
    """
    docs: dict[str, str] = {}
    lines = docstring.splitlines()

    in_params = False
    current_param: str | None = None
    current_desc_lines: list[str] = []
    param_indent: int | None = None

    for line in lines:
        stripped = line.strip()

        # Detect start of Args/Parameters section.
        if re.match(r"^(Args|Arguments|Parameters|Params)\s*:", stripped):
            in_params = True
            continue

        if not in_params:
            continue

        # Detect end of section (another section header or end of docstring).
        if stripped and re.match(r"^[A-Z][a-z]+\s*:", stripped) and ":" in stripped:
            # Could be a new section like "Returns:", "Raises:", etc.
            # But NOT a parameter line (those are indented).
            leading = len(line) - len(line.lstrip())
            if param_indent is not None and leading <= param_indent:
                # Save current param if any.
                if current_param:
                    docs[current_param] = " ".join(current_desc_lines).strip()
                in_params = False
                continue

        # Try to match a parameter line: "    param_name: description"
        # or "    param_name (type): description"
        m = re.match(r"^(\s+)(\w+)(?:\s*\([^)]*\))?\s*:\s*(.*)", line)
        if m:
            # Save previous param.
            if current_param:
                docs[current_param] = " ".join(current_desc_lines).strip()

            param_indent = len(m.group(1))
            current_param = m.group(2)
            desc = m.group(3).strip()
            current_desc_lines = [desc] if desc else []
            continue

        # Continuation line for the current parameter.
        if current_param and stripped:
            leading = len(line) - len(line.lstrip())
            if param_indent is not None and leading > param_indent:
                current_desc_lines.append(stripped)
                continue

        # Empty line or unindented line ends the param section.
        if current_param and not stripped:
            # Empty line — could still continue. Keep going.
            continue

    # Save the last parameter.
    if current_param:
        docs[current_param] = " ".join(current_desc_lines).strip()

    return docs
