"""Build plugin -- generate standalone CLI binaries or packages from OpenAPI profiles.

This module implements the ``specli build`` command group. It provides
two output modes:

* **compile** -- Uses PyInstaller to compile a self-contained binary with the
  OpenAPI spec, profile, and all specli dependencies baked in. The
  resulting binary exposes API commands at the top level (no ``api``
  sub-group, no ``init``/``config`` commands).
* **generate** -- Creates a pip-installable Python package directory with the
  spec and profile frozen into the source. Users can then ``pip install``
  the package or build a wheel for distribution.

Both commands share the :func:`_load_and_enrich` pipeline which handles
profile loading, spec parsing, source enrichment, string import/export,
and Claude Code skill generation.

The module also contains :data:`_ENTRY_TEMPLATE`, a large string template
for the generated CLI entry point that gets baked into the compiled binary
or package. This template includes frozen spec/profile data, a Typer
application with auth and dynamic API commands, and signal handling.

Usage::

    specli build compile --profile corelia --name corelia-cli
    specli build compile --profile corelia --name corelia-cli --onedir
    specli build generate --profile corelia --name corelia-cli
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

import typer

from specli.output import error, info, success, suggest


build_app = typer.Typer(no_args_is_help=True)


_ENTRY_TEMPLATE = '''\
#!/usr/bin/env python3
"""Auto-generated CLI entry point for {cli_name}.

Built by specli from profile "{profile_name}".
Spec: {spec_source}
"""

from __future__ import annotations

import json
import signal
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import typer

# ------------------------------------------------------------------ #
# Frozen spec + profile (baked in at build time)
# ------------------------------------------------------------------ #

_FROZEN_SPEC = json.loads({frozen_spec_repr})

_FROZEN_PROFILE = json.loads({frozen_profile_repr})

_CLI_NAME = {cli_name_repr}
_CLI_VERSION = {cli_version_repr}


# ------------------------------------------------------------------ #
# App
# ------------------------------------------------------------------ #

app = typer.Typer(
    name=_CLI_NAME,
    help={cli_help_repr},
    no_args_is_help=True,
    add_completion=True,
    rich_markup_mode="rich",
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"{{_CLI_NAME}} {{_CLI_VERSION}}")
        raise typer.Exit()


@app.callback()
def main_callback(
    ctx: typer.Context,
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True,
        help="Show version and exit.",
    ),
    json_output: bool = typer.Option(False, "--json", help="JSON output format."),
    plain_output: bool = typer.Option(False, "--plain", help="Plain text output."),
    no_color: bool = typer.Option(False, "--no-color", help="Disable color output."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress non-essential output."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug output."),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Preview without executing."),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmations."),
    output_file: Optional[str] = typer.Option(None, "-o", "--output", help="Output file path."),
) -> None:
    """{{_CLI_NAME}} -- Generated API CLI."""
    from specli.output import OutputFormat, OutputManager, set_output

    fmt = OutputFormat.AUTO
    if json_output:
        fmt = OutputFormat.JSON
    elif plain_output:
        fmt = OutputFormat.PLAIN

    output = OutputManager(
        format=fmt, no_color=no_color, quiet=quiet,
        verbose=verbose, output_file=output_file,
    )
    set_output(output)

    ctx.ensure_object(dict)
    ctx.obj["dry_run"] = dry_run
    ctx.obj["force"] = force
    ctx.obj["verbose"] = verbose


# ------------------------------------------------------------------ #
# Auth sub-command (login/add/list/test/remove)
# ------------------------------------------------------------------ #

auth_app = typer.Typer(no_args_is_help=True)


@auth_app.command("test")
def auth_test() -> None:
    """Test authentication against the API."""
    from specli.auth import create_default_manager
    from specli.client import SyncClient
    from specli.models import Profile
    from specli.output import error as err, info as inf, success as ok

    profile = Profile(**_FROZEN_PROFILE)
    if not profile.auth:
        err("No auth configured in this CLI.")
        raise typer.Exit(code=1)

    # Resolve the check endpoint from auth config extras.
    extras = profile.auth.model_extra or {{}}
    check_endpoint = extras.get("check_endpoint")

    if not check_endpoint:
        # No check endpoint -- just verify credentials resolve.
        am = create_default_manager()
        try:
            am.authenticate(profile)
            ok(f"Credentials OK (type: {{profile.auth.type}}).")
            inf("No check_endpoint configured -- credentials resolved but not verified against server.")
        except Exception as exc:
            err(f"Auth failed: {{exc}}")
            raise typer.Exit(code=3)
        return

    # Hit the check endpoint and inspect the status code.
    am = create_default_manager()
    try:
        am.authenticate(profile)
    except Exception as exc:
        err(f"Auth failed: {{exc}}")
        raise typer.Exit(code=3)

    with SyncClient(profile=profile, auth_manager=am) as client:
        response = client.request("GET", check_endpoint)
        if 200 <= response.status_code < 300:
            ok(f"Authenticated ({{profile.auth.type}}) -- {{response.status_code}}")
        elif response.status_code in (401, 403):
            err(f"Not authenticated -- {{response.status_code}}")
            raise typer.Exit(code=3)
        else:
            err(f"Unexpected status: {{response.status_code}}")
            raise typer.Exit(code=2)


app.add_typer(auth_app, name="auth", help="Authentication.")


# ------------------------------------------------------------------ #
# Dynamic API commands
# ------------------------------------------------------------------ #

def _load_commands() -> None:
    """Load dynamic commands from the frozen spec."""
    from specli.auth import create_default_manager
    from specli.client import SyncClient
    from specli.client.response import format_api_response
    from specli.generator import build_command_tree
    from specli.models import ParsedSpec, PathRulesConfig, Profile
    from specli.parser.extractor import extract_spec
    from specli.parser.loader import validate_openapi_version
    from specli.plugins.hooks import HookRunner

    version = validate_openapi_version(_FROZEN_SPEC)
    spec = extract_spec(_FROZEN_SPEC, version)
    profile = Profile(**_FROZEN_PROFILE)
    auth_manager = create_default_manager()

    def _make_request(method: str, path: str, params: dict, body: str | None, content_type: str | None = None) -> None:
        is_dry_run = "--dry-run" in sys.argv or "-n" in sys.argv

        with SyncClient(
            profile=profile,
            auth_manager=auth_manager,
            dry_run=is_dry_run,
        ) as client:
            actual_path = path
            query_params: dict[str, Any] = {{}}
            for key, value in params.items():
                placeholder = "{{" + key + "}}"
                if placeholder in actual_path:
                    actual_path = actual_path.replace(placeholder, str(value))
                elif value is not None:
                    query_params[key] = value

            json_body = None
            form_data = None
            if body is not None:
                try:
                    parsed = json.loads(body)
                except (json.JSONDecodeError, TypeError):
                    parsed = body
                if content_type and "form-urlencoded" in content_type:
                    form_data = parsed if isinstance(parsed, dict) else {{"body": body}}
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

    # Register API commands at the TOP LEVEL (not under "api" sub-group).
    # This is the key difference from specli -- the built binary
    # has API commands directly: corelia-cli users list, not corelia-cli api users list.
    for group in list(api_app.registered_groups):
        app.registered_groups.append(group)
    for command_info in list(api_app.registered_commands):
        app.registered_commands.append(command_info)


# ------------------------------------------------------------------ #
# Signal handling
# ------------------------------------------------------------------ #

def _setup_signals() -> None:
    def _handler(signum: int, frame: Any) -> None:
        sys.stderr.write("\\nCancelled.\\n")
        sys.exit(130)
    signal.signal(signal.SIGINT, _handler)


# ------------------------------------------------------------------ #
# Main
# ------------------------------------------------------------------ #

def main() -> None:
    _setup_signals()
    try:
        _load_commands()
        app()
    except SystemExit:
        raise
    except KeyboardInterrupt:
        sys.stderr.write("\\nCancelled.\\n")
        sys.exit(130)
    except Exception as exc:
        from specli.exceptions import SpecliError
        from specli.output import error as err

        if isinstance(exc, SpecliError):
            err(str(exc))
            sys.exit(exc.exit_code)
        else:
            sys.stderr.write(f"Unexpected error: {{exc}}\\n")
            sys.stderr.write(traceback.format_exc())
            sys.exit(1)


if __name__ == "__main__":
    main()
'''


# ------------------------------------------------------------------ #
# Shared enrichment pipeline
# ------------------------------------------------------------------ #


def _load_and_enrich(
    profile_name: str,
    source_dir: Optional[str],
    import_strings_path: Optional[str],
    export_strings_path: Optional[str],
    generate_skill_dir: Optional[str],
) -> tuple[Any, dict, str]:
    """Load a profile and its OpenAPI spec, then run the enrichment pipeline.

    The pipeline executes these steps in order:

    1. Load the named profile from the specli configuration.
    2. Load and validate the OpenAPI spec referenced by the profile.
    3. (Optional) Enrich operation descriptions from source code.
    4. (Optional) Import CLI string overrides from a JSON file.
    5. (Optional) Export CLI strings to a JSON file for editing.
    6. (Optional) Generate Claude Code skill files.

    This function is shared by both the ``compile`` and ``generate``
    sub-commands to avoid duplicating the enrichment logic.

    Args:
        profile_name: Name of the specli profile to load.
        source_dir: Path to source code directory for help-text enrichment,
            or ``None`` to skip source enrichment.
        import_strings_path: Path to a JSON file with CLI string overrides,
            or ``None`` to skip string import.
        export_strings_path: Path where CLI strings should be exported as
            JSON, or ``None`` to skip string export.
        generate_skill_dir: Directory where Claude Code skill files should
            be generated, or ``None`` to skip skill generation.

    Returns:
        A 3-tuple of ``(profile, raw_spec, openapi_version)`` where
        *profile* is the loaded :class:`~specli.models.Profile`,
        *raw_spec* is the raw OpenAPI dict (possibly enriched in-place),
        and *openapi_version* is the detected version string (e.g.
        ``"3.0"`` or ``"3.1"``).

    Raises:
        typer.Exit: If the profile or spec cannot be loaded (exit code 1).
    """
    from specli.config import load_profile
    from specli.exceptions import ConfigError
    from specli.parser import load_spec, validate_openapi_version

    try:
        profile = load_profile(profile_name)
    except ConfigError as exc:
        error(f"Profile not found: {exc}")
        raise typer.Exit(code=1)

    info(f"Loading spec from: {profile.spec}")
    try:
        raw_spec = load_spec(profile.spec)
        openapi_version = validate_openapi_version(raw_spec)
    except Exception as exc:
        error(f"Failed to load spec: {exc}")
        raise typer.Exit(code=1)

    info(f"Spec: {raw_spec.get('info', {}).get('title', 'Unknown')} "
         f"(OpenAPI {openapi_version})")

    # Source enrichment (patches raw_spec in place).
    enrichment_config = (profile.model_extra or {}).get("source_enrichment") or {}
    if source_dir:
        enrichment_config["source_dir"] = source_dir
    if enrichment_config.get("source_dir"):
        from specli.enrichment import enrich_spec_from_source

        info(f"Enriching spec from source: {enrichment_config['source_dir']}")
        enrich_spec_from_source(raw_spec, enrichment_config)
        info("Source enrichment complete.")

    # String import (highest priority — overrides spec + source).
    _import_str = import_strings_path or enrichment_config.get("strings_file")
    if _import_str:
        from specli.enrichment.strings import import_strings_from_file

        info(f"Importing strings from: {_import_str}")
        count = import_strings_from_file(raw_spec, _import_str)
        info(f"Imported strings for {count} operations.")

    # String export.
    if export_strings_path:
        from specli.enrichment.strings import export_strings_to_file

        count = export_strings_to_file(raw_spec, export_strings_path)
        success(f"Exported strings for {count} operations to: {export_strings_path}")

    # Skill generation.
    if generate_skill_dir:
        from specli.parser.extractor import extract_spec
        from specli.plugins.skill import generate_skill

        spec = extract_spec(raw_spec, openapi_version)
        result_path = generate_skill(spec, generate_skill_dir, profile)
        success(f"Skill generated at: {result_path}")

    return profile, raw_spec, openapi_version


def _load_build_config(profile_name: str) -> dict[str, Any]:
    """Load the ``build`` section from a profile's JSON, or empty dict.

    This pre-loads the profile just to read the build defaults, before
    the full enrichment pipeline runs.  Loading a profile is fast (JSON
    file read) and has no side-effects.
    """
    from specli.config import load_profile
    from specli.exceptions import ConfigError

    try:
        profile = load_profile(profile_name)
        return (profile.model_extra or {}).get("build") or {}
    except ConfigError:
        return {}


def _resolve_build_params(
    build_cfg: dict[str, Any],
    *,
    name: Optional[str],
    output_dir: Optional[str],
    cli_version: Optional[str],
    source_dir: Optional[str],
    import_strings: Optional[str],
    export_strings: Optional[str],
    generate_skill: Optional[str],
    default_output_dir: str,
) -> dict[str, Any]:
    """Merge CLI args with profile ``build`` defaults. CLI wins.

    Uses values from ``build_cfg`` (the ``"build"`` section of a profile)
    as fallbacks for any CLI parameter that was not explicitly provided
    (i.e. is ``None``).

    Args:
        build_cfg: The ``"build"`` dict from the profile, or ``{}``.
        name: CLI name from ``--name`` flag, or ``None``.
        output_dir: Output directory from ``--output`` flag, or ``None``.
        cli_version: Version from ``--cli-version`` flag, or ``None``.
        source_dir: Source dir from ``--source`` flag, or ``None``.
        import_strings: Import path from ``--import-strings``, or ``None``.
        export_strings: Export path from ``--export-strings``, or ``None``.
        generate_skill: Skill dir from ``--generate-skill``, or ``None``.
        default_output_dir: Hardcoded default for ``output_dir`` when
            neither the CLI flag nor the profile provides one.

    Returns:
        A dict with resolved values for each parameter.
    """
    return {
        "name": name or build_cfg.get("name"),
        "output_dir": output_dir or build_cfg.get("output_dir") or default_output_dir,
        "cli_version": cli_version or build_cfg.get("cli_version") or "1.0.0",
        "source_dir": source_dir or build_cfg.get("source_dir"),
        "import_strings": import_strings or build_cfg.get("import_strings"),
        "export_strings": export_strings or build_cfg.get("export_strings"),
        "generate_skill": generate_skill or build_cfg.get("generate_skill"),
    }


@build_app.command("compile")
def build_compile(
    profile_name: str = typer.Option(
        ..., "--profile", "-p",
        help="Profile to bake into the binary.",
    ),
    name: Optional[str] = typer.Option(
        None, "--name", "-n",
        help="Output binary name (e.g. corelia-cli). Falls back to profile build.name.",
    ),
    output_dir: Optional[str] = typer.Option(
        None, "--output", "-o",
        help="Output directory for the binary. [default: ./dist]",
    ),
    onedir: bool = typer.Option(
        False, "--onedir",
        help="Build as a directory bundle instead of a single file.",
    ),
    cli_version: Optional[str] = typer.Option(
        None, "--cli-version",
        help="Version string for the generated CLI. [default: 1.0.0]",
    ),
    clean: bool = typer.Option(
        True, "--clean/--no-clean",
        help="Remove build artifacts after compilation.",
    ),
    source_dir: Optional[str] = typer.Option(
        None, "--source", "-s",
        help="Source code directory for help text enrichment.",
    ),
    export_strings: Optional[str] = typer.Option(
        None, "--export-strings",
        help="Export all CLI strings to a JSON file for editing/translation.",
    ),
    import_strings: Optional[str] = typer.Option(
        None, "--import-strings",
        help="Import CLI strings from a JSON file (overrides all other sources).",
    ),
    generate_skill: Optional[str] = typer.Option(
        None, "--generate-skill",
        help="Generate Claude Code skill files to this directory.",
    ),
    no_build: bool = typer.Option(
        False, "--no-build",
        help="Run enrichment/export/skill only, skip binary compilation.",
    ),
) -> None:
    """Compile a standalone CLI binary from a profile using PyInstaller.

    Bakes the OpenAPI spec and profile config into a self-contained binary.
    The binary includes all specli dependencies and exposes API commands
    at the top level. Works on Linux and macOS.

    The build process:

    1. Runs the enrichment pipeline (source enrichment, string import/export,
       skill generation) via :func:`_load_and_enrich`.
    2. Generates a temporary Python entry-point script from
       :data:`_ENTRY_TEMPLATE` with the spec and profile frozen as literals.
    3. Invokes PyInstaller with hidden imports for all specli internals.
    4. Verifies the output binary exists and reports its size.

    Use ``--no-build`` to run the enrichment pipeline without compiling::

        specli build compile -p myapi -n myapi-cli --source ./src \\
            --export-strings strings.json --generate-skill ./skill --no-build

    Example:
        ::

            specli build compile --profile corelia --name corelia-cli
            specli build compile -p myapi -n myapi-cli --onedir
            specli build compile -p myapi -n myapi-cli --cli-version 2.0.0
    """
    # 0. Resolve build defaults from profile (CLI flags take precedence).
    build_cfg = _load_build_config(profile_name)
    bp = _resolve_build_params(
        build_cfg,
        name=name,
        output_dir=output_dir,
        cli_version=cli_version,
        source_dir=source_dir,
        import_strings=import_strings,
        export_strings=export_strings,
        generate_skill=generate_skill,
        default_output_dir="./dist",
    )
    name = bp["name"]
    output_dir = bp["output_dir"]
    cli_version = bp["cli_version"]

    if not name:
        error("No --name provided and no 'name' in profile build config.")
        raise typer.Exit(code=1)

    # 1. Enrichment pipeline (load, enrich, export, skill).
    profile, raw_spec, openapi_version = _load_and_enrich(
        profile_name,
        bp["source_dir"],
        bp["import_strings"],
        bp["export_strings"],
        bp["generate_skill"],
    )

    # Exit early if --no-build.
    if no_build:
        success("Pipeline complete (--no-build, skipping compilation).")
        raise typer.Exit()

    # 2. Check PyInstaller is available
    if not _check_pyinstaller():
        error("PyInstaller is not installed.")
        suggest("Install it: pip install pyinstaller")
        raise typer.Exit(code=1)

    # 3. Generate the entry point script
    spec_title = raw_spec.get("info", {}).get("title", name)
    spec_desc = raw_spec.get("info", {}).get("description", f"CLI for {spec_title}")
    cli_help = f"{spec_title} — {spec_desc}" if spec_desc else spec_title

    profile_data = profile.model_dump(mode="json")

    frozen_spec_json = json.dumps(raw_spec)
    frozen_profile_json = json.dumps(profile_data)

    entry_source = _ENTRY_TEMPLATE.format(
        cli_name=name,
        profile_name=profile_name,
        spec_source=profile.spec,
        frozen_spec_repr=repr(frozen_spec_json),
        frozen_profile_repr=repr(frozen_profile_json),
        cli_name_repr=repr(name),
        cli_version_repr=repr(cli_version),
        cli_help_repr=repr(cli_help),
    )

    # 4. Write temp entry point and run PyInstaller
    build_dir = Path(tempfile.mkdtemp(prefix=f"specli-build-{name}-"))
    entry_file = build_dir / f"{name.replace('-', '_')}_entry.py"
    entry_file.write_text(entry_source)

    info(f"Building {name}...")

    dist_path = Path(output_dir).resolve()
    dist_path.mkdir(parents=True, exist_ok=True)

    pyinstaller_args = [
        sys.executable, "-m", "PyInstaller",
        "--name", name,
        "--distpath", str(dist_path),
        "--workpath", str(build_dir / "build"),
        "--specpath", str(build_dir),
        "--noconfirm",
        "--clean",
        "--log-level", "WARN",
    ]

    if onedir:
        pyinstaller_args.append("--onedir")
    else:
        pyinstaller_args.append("--onefile")

    # Hidden imports for specli internals that PyInstaller may miss
    hidden_imports = [
        "specli",
        "specli.app",
        "specli.auth",
        "specli.auth.base",
        "specli.auth.manager",
        "specli.cache",
        "specli.client",
        "specli.client.sync_client",
        "specli.client.response",
        "specli.config",
        "specli.exceptions",
        "specli.exit_codes",
        "specli.generator",
        "specli.generator.command_tree",
        "specli.generator.param_mapper",
        "specli.generator.path_rules",
        "specli.models",
        "specli.output",
        "specli.parser",
        "specli.parser.extractor",
        "specli.parser.loader",
        "specli.parser.resolver",
        "specli.plugins",
        "specli.plugins.api_key",
        "specli.plugins.api_key.plugin",
        "specli.plugins.basic",
        "specli.plugins.basic.plugin",
        "specli.plugins.bearer",
        "specli.plugins.bearer.plugin",
        "specli.plugins.oauth2_auth_code",
        "specli.plugins.oauth2_auth_code.plugin",
        "specli.plugins.oauth2_client_credentials",
        "specli.plugins.oauth2_client_credentials.plugin",
        "specli.plugins.openid_connect",
        "specli.plugins.openid_connect.plugin",
        "specli.plugins.manual_token",
        "specli.plugins.manual_token.plugin",
        "specli.plugins.browser_login",
        "specli.plugins.browser_login.plugin",
        "specli.plugins.api_key_gen",
        "specli.plugins.api_key_gen.plugin",
        "specli.plugins.device_code",
        "specli.plugins.device_code.plugin",
        "specli.auth.credential_store",
        "specli.plugins.hooks",
        "specli.plugins.manager",
        # Typer/Click shell completion dependencies
        "shellingham",
        "shellingham.posix",
    ]

    for mod in hidden_imports:
        pyinstaller_args.extend(["--hidden-import", mod])

    # Collect submodules/data for packages with non-standard module names
    # (e.g. rich._unicode_data contains files like unicode17-0-0.py)
    collect_submodules = [
        "rich",
    ]
    for pkg in collect_submodules:
        pyinstaller_args.extend(["--collect-submodules", pkg])

    pyinstaller_args.append(str(entry_file))

    try:
        result = subprocess.run(
            pyinstaller_args,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        error("Build timed out after 5 minutes.")
        raise typer.Exit(code=1)
    except FileNotFoundError:
        error("PyInstaller binary not found.")
        suggest("Install it: pip install pyinstaller")
        raise typer.Exit(code=1)

    if result.returncode != 0:
        error("PyInstaller build failed:")
        # Show stderr for diagnostics
        for line in result.stderr.splitlines()[-20:]:
            error(f"  {line}")
        if result.stdout:
            for line in result.stdout.splitlines()[-10:]:
                info(f"  {line}")
        raise typer.Exit(code=1)

    # 5. Verify output
    if onedir:
        binary_path = dist_path / name / name
    else:
        binary_path = dist_path / name

    if not binary_path.exists():
        error(f"Expected binary not found at: {binary_path}")
        raise typer.Exit(code=1)

    # 6. Clean up build artifacts
    if clean:
        shutil.rmtree(build_dir, ignore_errors=True)

    binary_size = binary_path.stat().st_size
    size_mb = binary_size / (1024 * 1024)

    success(f"Built: {binary_path} ({size_mb:.1f} MB)")
    suggest(f"Test it: {binary_path} --help")
    suggest(f"Move to PATH: sudo mv {binary_path} /usr/local/bin/{name}")


@build_app.command("generate")
def build_generate(
    profile_name: str = typer.Option(
        ..., "--profile", "-p",
        help="Profile to bake into the package.",
    ),
    name: Optional[str] = typer.Option(
        None, "--name", "-n",
        help="CLI/package name (e.g. corelia-cli). Falls back to profile build.name.",
    ),
    output_dir: Optional[str] = typer.Option(
        None, "--output", "-o",
        help="Directory to create the package in. [default: .]",
    ),
    cli_version: Optional[str] = typer.Option(
        None, "--cli-version",
        help="Version string for the generated CLI. [default: 1.0.0]",
    ),
    source_dir: Optional[str] = typer.Option(
        None, "--source", "-s",
        help="Source code directory for help text enrichment.",
    ),
    export_strings: Optional[str] = typer.Option(
        None, "--export-strings",
        help="Export all CLI strings to a JSON file for editing/translation.",
    ),
    import_strings: Optional[str] = typer.Option(
        None, "--import-strings",
        help="Import CLI strings from a JSON file (overrides all other sources).",
    ),
    generate_skill: Optional[str] = typer.Option(
        None, "--generate-skill",
        help="Generate Claude Code skill files to this directory.",
    ),
    no_build: bool = typer.Option(
        False, "--no-build",
        help="Run enrichment/export/skill only, skip package generation.",
    ),
) -> None:
    """Generate a pip-installable Python package (no PyInstaller needed).

    Creates a standalone package directory with the OpenAPI spec and profile
    configuration baked into the source code. The package uses Hatch as its
    build backend and declares ``specli`` as its only dependency.

    The generated package structure::

        <name>/
            pyproject.toml
            src/<pkg_name>/
                __init__.py
                __main__.py
                cli.py          # Entry point with frozen spec/profile

    Install with ``pip install ./<name>`` or build a wheel with
    ``pip wheel ./<name>``.

    Use ``--no-build`` to run the enrichment pipeline without generating::

        specli build generate -p myapi -n myapi-cli --source ./src \\
            --generate-skill ./skill --no-build

    Example:
        ::

            specli build generate --profile corelia --name corelia-cli
            cd corelia-cli && pip install .
    """
    # 0. Resolve build defaults from profile (CLI flags take precedence).
    build_cfg = _load_build_config(profile_name)
    bp = _resolve_build_params(
        build_cfg,
        name=name,
        output_dir=output_dir,
        cli_version=cli_version,
        source_dir=source_dir,
        import_strings=import_strings,
        export_strings=export_strings,
        generate_skill=generate_skill,
        default_output_dir=".",
    )
    name = bp["name"]
    output_dir = bp["output_dir"]
    cli_version = bp["cli_version"]

    if not name:
        error("No --name provided and no 'name' in profile build config.")
        raise typer.Exit(code=1)

    # 1. Enrichment pipeline (load, enrich, export, skill)
    profile, raw_spec, openapi_version = _load_and_enrich(
        profile_name,
        bp["source_dir"],
        bp["import_strings"],
        bp["export_strings"],
        bp["generate_skill"],
    )

    # Exit early if --no-build.
    if no_build:
        success("Pipeline complete (--no-build, skipping package generation).")
        raise typer.Exit()

    # 2. Generate package
    spec_title = raw_spec.get("info", {}).get("title", name)
    spec_desc = raw_spec.get("info", {}).get("description", f"CLI for {spec_title}")
    cli_help = f"{spec_title} — {spec_desc}" if spec_desc else spec_title
    profile_data = profile.model_dump(mode="json")

    pkg_name = name.replace("-", "_")
    pkg_dir = Path(output_dir).resolve() / name
    src_dir = pkg_dir / "src" / pkg_name

    pkg_dir.mkdir(parents=True, exist_ok=True)
    src_dir.mkdir(parents=True, exist_ok=True)

    # Write entry point module
    frozen_spec_json = json.dumps(raw_spec)
    frozen_profile_json = json.dumps(profile_data)

    entry_source = _ENTRY_TEMPLATE.format(
        cli_name=name,
        profile_name=profile_name,
        spec_source=profile.spec,
        frozen_spec_repr=repr(frozen_spec_json),
        frozen_profile_repr=repr(frozen_profile_json),
        cli_name_repr=repr(name),
        cli_version_repr=repr(cli_version),
        cli_help_repr=repr(cli_help),
    )

    (src_dir / "__init__.py").write_text(f'"""Generated CLI: {name}."""\n')
    (src_dir / "__main__.py").write_text(
        f"from {pkg_name}.cli import main\n\nmain()\n"
    )
    (src_dir / "cli.py").write_text(entry_source)

    # Write pyproject.toml
    pyproject = f"""\
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "{name}"
version = "{cli_version}"
description = "CLI for {spec_title}"
requires-python = ">=3.10"
dependencies = [
    "specli>=0.1.0",
]

[project.scripts]
{name} = "{pkg_name}.cli:main"

[tool.hatch.build.targets.wheel]
packages = ["src/{pkg_name}"]
"""
    (pkg_dir / "pyproject.toml").write_text(pyproject)

    success(f"Package generated: {pkg_dir}")
    suggest(f"Install it: pip install {pkg_dir}")
    suggest(f"Or build a binary: cd {pkg_dir} && pip install pyinstaller && "
            f"pyinstaller --onefile src/{pkg_name}/cli.py --name {name}")


def _check_pyinstaller() -> bool:
    """Check whether PyInstaller is installed and importable.

    Spawns a subprocess to attempt ``import PyInstaller`` rather than
    importing directly, to avoid polluting the current process's module
    state.

    Returns:
        ``True`` if PyInstaller is available, ``False`` otherwise.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-c", "import PyInstaller"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False
