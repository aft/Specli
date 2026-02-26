"""Typer application factory and CLI entry point for specli.

This module wires together the top-level Typer application, registers built-in
sub-commands (``init``, ``auth``, ``config``, ``inspect``, ``build``,
``completion``), and dynamically loads API commands from the active profile's
OpenAPI spec at startup.

The :func:`main` function is the console-script entry point declared in
``pyproject.toml``. It installs signal handlers, registers commands, attempts
to load dynamic API commands, and finally invokes the Typer app. Unhandled
exceptions are written to a crash log under the data directory.

See Also:
    :mod:`specli.config`: Profile and global configuration resolution.
    :mod:`specli.output`: Output formatting initialised in :func:`main_callback`.
"""

from __future__ import annotations

import signal
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import typer

from specli import __version__
from specli.exit_codes import EXIT_GENERIC_FAILURE, EXIT_SUCCESS


app = typer.Typer(
    name="specli",
    help="Generate CLI commands from OpenAPI 3.0/3.1 specs.",
    no_args_is_help=True,
    add_completion=True,
    rich_markup_mode="rich",
)

# ------------------------------------------------------------------ #
# Plugins
# ------------------------------------------------------------------ #

from specli.plugins.completion import completion_app  # noqa: E402
from specli.plugins.build import build_app  # noqa: E402
from specli.plugins.skill import skill_app  # noqa: E402

app.add_typer(completion_app, name="completion", help="Shell completion management.")
app.add_typer(build_app, name="build", help="Build standalone CLI binaries.")
app.add_typer(skill_app, name="skill", help="Generate Claude Code skills.")


def _version_callback(value: bool) -> None:
    """Print version and exit when --version is passed."""
    if value:
        typer.echo(f"specli {__version__}")
        raise typer.Exit()


@app.callback()
def main_callback(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
    profile: Optional[str] = typer.Option(
        None, "--profile", "-p", help="Profile name to use."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="JSON output format."
    ),
    plain_output: bool = typer.Option(
        False, "--plain", help="Plain text output."
    ),
    no_color: bool = typer.Option(
        False, "--no-color", help="Disable color output."
    ),
    quiet: bool = typer.Option(
        False, "--quiet", "-q", help="Suppress non-essential output."
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Enable debug output."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Preview without executing."
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Skip confirmations."
    ),
    no_input: bool = typer.Option(
        False, "--no-input", help="Disable interactive prompts."
    ),
    output_file: Optional[str] = typer.Option(
        None, "-o", "--output", help="Output file path."
    ),
) -> None:
    """Root callback executed before every sub-command.

    Initialises the global :class:`~specli.output.OutputManager` from
    CLI flags, and stores shared options (``profile``, ``dry_run``, ``force``,
    etc.) in the Typer context so that sub-commands can read them via
    ``ctx.obj``.

    Args:
        ctx: Typer invocation context.
        version: If ``True``, print the version string and exit.
        profile: Profile name override (highest precedence).
        json_output: Force JSON output format.
        plain_output: Force plain-text output format.
        no_color: Disable all colour and Rich markup.
        quiet: Suppress non-essential diagnostic output.
        verbose: Enable debug-level diagnostic output.
        dry_run: Preview actions without executing HTTP requests.
        force: Skip interactive confirmations.
        no_input: Disable all interactive prompts.
        output_file: Redirect primary data output to a file path.
    """
    from specli.output import OutputFormat, OutputManager, set_output

    fmt = OutputFormat.AUTO
    if json_output:
        fmt = OutputFormat.JSON
    elif plain_output:
        fmt = OutputFormat.PLAIN

    output = OutputManager(
        format=fmt,
        no_color=no_color,
        quiet=quiet,
        verbose=verbose,
        output_file=output_file,
    )
    set_output(output)

    ctx.ensure_object(dict)
    ctx.obj["profile"] = profile
    ctx.obj["dry_run"] = dry_run
    ctx.obj["force"] = force
    ctx.obj["no_input"] = no_input
    ctx.obj["verbose"] = verbose


def _setup_signal_handlers() -> None:
    """Install a SIGINT handler so Ctrl-C exits cleanly."""

    def _handler(signum: int, frame: Any) -> None:  # noqa: ANN401
        sys.stderr.write("\nCancelled.\n")
        sys.exit(130)

    signal.signal(signal.SIGINT, _handler)


def _write_crash_log(exc: Exception) -> str:
    """Write a crash traceback to disk and return the log file path.

    Args:
        exc: The unhandled exception to log.

    Returns:
        Absolute path to the written crash log file.
    """
    from specli.config import get_data_dir

    logs_dir = get_data_dir() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = logs_dir / f"crash-{timestamp}.log"
    log_path.write_text(traceback.format_exc())
    return str(log_path)


def _load_dynamic_commands() -> None:
    """Load dynamic API commands from the active profile's OpenAPI spec.

    Resolves the active profile via :func:`~specli.config.resolve_config`,
    parses and validates the linked OpenAPI spec, builds a Typer command tree
    mirroring the API's resource hierarchy, and attaches it as an ``api``
    sub-group on the root application.

    Failures are silently ignored so that built-in commands (``init``,
    ``auth``, ``config``, etc.) always remain available even when no profile
    is configured or the spec cannot be loaded.
    """
    try:
        from specli.auth import create_default_manager
        from specli.cache import ResponseCache
        from specli.client import SyncClient
        from specli.client.response import format_api_response
        from specli.config import get_cache_dir, resolve_config
        from specli.generator import build_command_tree
        from specli.output import debug
        from specli.parser import load_spec, validate_openapi_version
        from specli.parser.extractor import extract_spec
        from specli.plugins import PluginManager

        config, profile = resolve_config()
        if profile is None:
            return

        debug(f"Loading spec from profile: {profile.name}")
        raw = load_spec(profile.spec)
        version = validate_openapi_version(raw)
        spec = extract_spec(raw, version)

        auth_manager = create_default_manager()
        cache = ResponseCache(get_cache_dir(), config.cache)
        plugin_manager = PluginManager()
        plugin_manager.discover(config)

        def _make_request(
            method: str,
            path: str,
            params: dict[str, Any],
            body: str | None,
            content_type: str | None = None,
        ) -> None:
            """Callback invoked by dynamically generated commands."""
            # Determine dry-run from argv since ctx.obj is not
            # accessible inside the callback at command-tree load time.
            is_dry_run = "--dry-run" in sys.argv or "-n" in sys.argv

            with SyncClient(
                profile=profile,
                auth_manager=auth_manager,
                hook_runner=plugin_manager.get_hook_runner(),
                dry_run=is_dry_run,
                cache=cache,
            ) as client:
                # Substitute path parameters into the URL template.
                actual_path = path
                query_params: dict[str, Any] = {}
                for key, value in params.items():
                    placeholder = "{" + key + "}"
                    if placeholder in actual_path:
                        actual_path = actual_path.replace(placeholder, str(value))
                    elif value is not None:
                        query_params[key] = value

                # Choose body encoding based on the spec's content type.
                json_body = None
                form_data = None
                if body:
                    parsed = _parse_body(body)
                    if content_type and "form-urlencoded" in content_type:
                        # Form-encoded: pass as dict for httpx data= param.
                        form_data = parsed if isinstance(parsed, dict) else {"body": body}
                    else:
                        json_body = parsed

                response = client.request(
                    method=method.upper(),
                    path=actual_path,
                    params=query_params if query_params else None,
                    json_body=json_body,
                    data=form_data,
                )
                format_api_response(response)

        api_app = build_command_tree(spec, profile.path_rules, _make_request)

        # Add the generated API commands as an "api" sub-group so that
        # any Typer compilation failures are isolated to that sub-group
        # and do not prevent built-in commands from working.
        api_title = spec.info.title or "API"
        app.add_typer(
            api_app,
            name="api",
            help=f"Generated commands for {api_title}.",
        )

    except Exception:
        # Silent fail -- built-in commands must always work.
        pass


def _parse_body(body: str | None) -> Any:  # noqa: ANN401
    """Parse *body* as JSON if possible, returning the raw string on failure."""
    if body is None:
        return None
    import json

    try:
        return json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return body


def main() -> None:
    """CLI entry point invoked by the ``specli`` console script.

    Performs the following sequence:

    1. Install signal handlers for clean Ctrl-C behaviour.
    2. Register built-in sub-commands (``init``, ``auth``, ``config``,
       ``inspect``).
    3. Attempt to load dynamic API commands from the active profile.
    4. Invoke the Typer application.

    Unhandled :class:`~specli.exceptions.SpecliError` instances
    cause a clean exit with the error's ``exit_code``. All other exceptions
    produce a crash log and a generic failure exit.

    Raises:
        SystemExit: Always raised (either by Typer or explicitly).
    """
    _setup_signal_handlers()
    try:
        from specli.commands.auth import auth_app
        from specli.commands.config import config_app
        from specli.commands.init import init_command
        from specli.commands.inspect import inspect_app

        app.command("init")(init_command)
        app.add_typer(auth_app, name="auth", help="Authentication management.")
        app.add_typer(config_app, name="config", help="Configuration management.")
        app.add_typer(inspect_app, name="inspect", help="Inspect API spec details.")

        # Try to load dynamic commands from the active profile.
        _load_dynamic_commands()

        app()
    except SystemExit:
        raise
    except KeyboardInterrupt:
        sys.stderr.write("\nCancelled.\n")
        sys.exit(130)
    except Exception as exc:
        from specli.exceptions import SpecliError
        from specli.output import error

        if isinstance(exc, SpecliError):
            error(str(exc))
            sys.exit(exc.exit_code)
        else:
            log_path = _write_crash_log(exc)
            error(f"Unexpected error. Debug log: {log_path}")
            error("Please report: https://github.com/CoreliaOS/specli/issues")
            sys.exit(EXIT_GENERIC_FAILURE)
