"""OpenAPI spec parser -- load, resolve ``$ref`` pointers, and extract operations.

This sub-package is responsible for the first half of the specli pipeline:
turning a raw OpenAPI 3.x document (JSON or YAML, local file or remote URL) into
a :class:`~specli.models.ParsedSpec` that the generator can consume.

Typical usage::

    from specli.parser import load_spec, validate_openapi_version, extract_spec

    raw = load_spec("https://petstore3.swagger.io/api/v3/openapi.json")
    version = validate_openapi_version(raw)
    parsed = extract_spec(raw, version)

Sub-modules:

* :mod:`~specli.parser.loader` -- I/O layer (URL, file, stdin) plus format
  detection and OpenAPI version validation.
* :mod:`~specli.parser.resolver` -- Recursive ``$ref`` resolution with
  circular-reference detection.
* :mod:`~specli.parser.extractor` -- Walks the resolved spec tree and
  produces :class:`~specli.models.ParsedSpec` containing
  :class:`~specli.models.APIOperation` objects.
"""

from specli.parser.extractor import extract_spec
from specli.parser.loader import load_spec, validate_openapi_version

__all__ = ["load_spec", "validate_openapi_version", "extract_spec"]
