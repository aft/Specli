"""specli -- Generate standalone CLI binaries from OpenAPI 3.0/3.1 specs.

This package converts an OpenAPI specification into a fully-functional Typer CLI
application with commands that mirror the API's resource hierarchy. Users create
a *profile* pointing to an OpenAPI spec, then build and compile the CLI into a
standalone binary.

Typical workflow::

    specli init --spec openapi.json   # create a profile
    specli build compile              # produce a standalone binary

The generated CLI includes authentication, caching, output formatting, and shell
completion out of the box.

Modules:
    app: Typer application factory and CLI entry point.
    models: Pydantic models shared across the entire package.
    config: XDG-aware configuration and profile management.
    exceptions: Exception hierarchy with exit-code mapping.
    exit_codes: Numeric exit codes following clig.dev conventions.
    output: stdout/stderr formatting system with Rich support.
"""

__version__ = "0.2.3"
