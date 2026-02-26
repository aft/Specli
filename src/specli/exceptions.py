"""Exception hierarchy for specli.

All exceptions inherit from :class:`SpecliError`, which carries an
``exit_code`` attribute mapped to a constant from :mod:`specli.exit_codes`.
The top-level error handler in :func:`specli.app.main` catches
``SpecliError`` and exits with the appropriate code, while unexpected
exceptions produce a crash log and exit with :data:`EXIT_GENERIC_FAILURE`.

Subclass hierarchy::

    SpecliError (exit 1)
    +-- InvalidUsageError   (exit 2)
    +-- AuthError           (exit 3)
    +-- NotFoundError       (exit 4)
    +-- ServerError         (exit 5)
    +-- ConnectionError_    (exit 6)
    +-- SpecParseError      (exit 7)
    +-- PluginError         (exit 10)
    +-- ConfigError         (exit 1)
"""

from specli.exit_codes import (
    EXIT_AUTH_FAILURE,
    EXIT_CONNECTION_ERROR,
    EXIT_GENERIC_FAILURE,
    EXIT_INVALID_USAGE,
    EXIT_NOT_FOUND,
    EXIT_PLUGIN_ERROR,
    EXIT_SERVER_ERROR,
    EXIT_SPEC_PARSE_ERROR,
)


class SpecliError(Exception):
    """Base exception for all specli errors.

    Every subclass sets a class-level ``exit_code`` corresponding to one of
    the constants in :mod:`specli.exit_codes`. The entry point catches
    this exception type and calls ``sys.exit(exc.exit_code)``.

    Args:
        message: Human-readable error description printed to stderr.
        exit_code: Optional override for the class-level exit code.
    """

    exit_code: int = EXIT_GENERIC_FAILURE

    def __init__(self, message: str, exit_code: int | None = None):
        super().__init__(message)
        if exit_code is not None:
            self.exit_code = exit_code


class InvalidUsageError(SpecliError):
    """Raised for invalid CLI arguments or missing required parameters."""

    exit_code = EXIT_INVALID_USAGE


class AuthError(SpecliError):
    """Raised when authentication or authorisation fails (e.g. invalid API key, expired token)."""

    exit_code = EXIT_AUTH_FAILURE


class NotFoundError(SpecliError):
    """Raised when the API returns HTTP 404 (resource not found)."""

    exit_code = EXIT_NOT_FOUND


class ServerError(SpecliError):
    """Raised when the API returns an HTTP 5xx server error."""

    exit_code = EXIT_SERVER_ERROR


class ConnectionError_(SpecliError):
    """Raised on network-level failures (timeout, DNS resolution, connection refused).

    Named with a trailing underscore to avoid shadowing the built-in
    ``ConnectionError``.
    """

    exit_code = EXIT_CONNECTION_ERROR


class SpecParseError(SpecliError):
    """Raised when the OpenAPI spec cannot be parsed or fails validation."""

    exit_code = EXIT_SPEC_PARSE_ERROR


class PluginError(SpecliError):
    """Raised when a plugin fails to load, initialise, or execute a hook."""

    exit_code = EXIT_PLUGIN_ERROR


class ConfigError(SpecliError):
    """Raised for configuration problems (missing profiles, invalid JSON, bad credential sources)."""

    exit_code = EXIT_GENERIC_FAILURE
