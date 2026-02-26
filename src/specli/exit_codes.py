"""Numeric process exit codes following `clig.dev <https://clig.dev/>`_ conventions.

Each constant maps to a specific error category and is referenced by the
corresponding :class:`~specli.exceptions.SpecliError` subclass.
External tooling (CI scripts, shell wrappers) can inspect the exit code to
determine the failure class without parsing stderr.

Example::

    $ specli api users list
    $ echo $?
    3   # EXIT_AUTH_FAILURE -- credentials were rejected
"""

EXIT_SUCCESS = 0
"""The command completed successfully."""

EXIT_GENERIC_FAILURE = 1
"""An unclassified error occurred."""

EXIT_INVALID_USAGE = 2
"""The command was invoked with invalid arguments or missing required parameters."""

EXIT_AUTH_FAILURE = 3
"""Authentication or authorisation failed."""

EXIT_NOT_FOUND = 4
"""The requested resource was not found (HTTP 404)."""

EXIT_SERVER_ERROR = 5
"""The remote API returned an HTTP 5xx server error."""

EXIT_CONNECTION_ERROR = 6
"""A network-level error occurred (timeout, DNS failure, connection refused)."""

EXIT_SPEC_PARSE_ERROR = 7
"""The OpenAPI specification could not be parsed or validated."""

EXIT_PLUGIN_ERROR = 10
"""A plugin failed to load, initialise, or execute."""
