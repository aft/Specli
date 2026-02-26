"""Source-code enrichment pipeline for CLI help text.

This package provides a build-time pipeline that improves the quality of
user-visible strings in an OpenAPI spec by mining Python source code:

1. **Scanning** (:mod:`~specli.enrichment.scanner`) -- walks a
   Python source tree using :mod:`ast`, locating FastAPI/Starlette route
   handlers and extracting their docstrings, ``Args:`` parameter docs,
   and Pydantic ``Field(description=...)`` metadata.

2. **Enrichment** (:mod:`~specli.enrichment.enricher`) -- patches
   the raw OpenAPI spec dict in place, filling missing or thin summaries,
   descriptions, and parameter descriptions with the source-extracted
   documentation.  Existing substantive spec content is preserved.

3. **String export/import** (:mod:`~specli.enrichment.strings`) --
   serialises all user-visible strings from the spec to an editable JSON
   file (for translation, LLM context, or manual overrides) and applies
   them back at the highest priority, unconditionally overriding all
   other sources.

Priority chain (lowest to highest):
    raw OpenAPI spec --> source enrichment --> imported string overrides

The convenience function :func:`enrich_spec_from_source` combines steps
1 and 2 into a single call suitable for build scripts.
"""

from __future__ import annotations

from specli.enrichment.enricher import enrich_raw_spec
from specli.enrichment.scanner import RouteDoc as RouteDoc, SourceScanner
from specli.enrichment.strings import (
    export_strings_to_file as export_strings_to_file,
    import_strings_from_file as import_strings_from_file,
)


def enrich_spec_from_source(
    raw_spec: dict,
    config: dict,
) -> None:
    """Enrich a raw OpenAPI spec dict with documentation from Python source.

    Convenience wrapper that creates a :class:`SourceScanner`, scans the
    source directory specified in *config*, and applies the extracted
    :class:`RouteDoc` objects to *raw_spec* via :func:`enrich_raw_spec`.

    No-ops silently when ``config["source_dir"]`` is missing or falsy.

    Args:
        raw_spec: Raw OpenAPI spec dict (mutated in place).
        config: Enrichment configuration with keys:

            - ``source_dir`` (str, required): Root path to the Python
              source tree to scan.
            - ``include`` (list[str], optional): Glob patterns for files
              to include.  Defaults to ``["**/*.py"]``.
            - ``exclude`` (list[str], optional): Glob patterns for files
              to exclude.  Defaults to common non-source directories
              (tests, venv, node_modules, etc.).

    Example::

        enrich_spec_from_source(raw, {"source_dir": "./src"})
    """
    source_dir = config.get("source_dir")
    if not source_dir:
        return

    include = config.get("include", ["**/*.py"])
    exclude = config.get("exclude", [
        "**/test_*", "**/__pycache__/**",
        "**/venv/**", "**/.venv/**", "**/env/**",
        "**/node_modules/**", "**/.git/**",
        "**/site-packages/**",
    ])

    scanner = SourceScanner()
    route_docs = scanner.scan(source_dir, include_patterns=include, exclude_patterns=exclude)

    if route_docs:
        enrich_raw_spec(raw_spec, route_docs)
