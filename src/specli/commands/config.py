"""Config commands -- view and modify global configuration.

Provides the ``specli config`` sub-command group for reading,
updating, and resetting the user's global configuration file
(:class:`~specli.models.GlobalConfig`). Settings are persisted in
the specli config directory and control defaults such as output
format, cache TTL, and active profile.
"""

from __future__ import annotations

import typer

from specli.output import error, format_response, info, success


config_app = typer.Typer(no_args_is_help=True)


@config_app.command("show")
def config_show() -> None:
    """Show current configuration.

    Loads the global config from disk and prints the config directory
    path followed by the full configuration as formatted output (table
    or JSON, depending on the active output mode).

    Example::

        specli config show
        specli config show --json
    """
    from specli.config import get_config_dir, load_global_config

    config = load_global_config()
    info(f"Config directory: {get_config_dir()}")
    format_response(config.model_dump(mode="json"))


@config_app.command("set")
def config_set(
    key: str = typer.Argument(
        help="Config key (dot notation, e.g., 'output.format')."
    ),
    value: str = typer.Argument(help="Value to set."),
) -> None:
    """Set a configuration value.

    Uses dot notation for nested keys. The value is automatically
    coerced to match the existing field's type (bool, int, or str).
    The updated config is validated against
    :class:`~specli.models.GlobalConfig` before saving.

    Args:
        key: Dot-separated config key path (e.g. ``output.format``).
        value: String value to set; coerced to the target field type.

    Raises:
        typer.Exit: With code 2 if the key path is invalid, the value
            cannot be coerced, or Pydantic validation fails.

    Example::

        specli config set default_profile myapi
        specli config set output.format json
        specli config set cache.ttl_seconds 600
    """
    from specli.config import load_global_config, save_global_config
    from specli.models import GlobalConfig

    config = load_global_config()
    data = config.model_dump(mode="json")

    # Navigate the dot-separated key path.
    keys = key.split(".")
    target = data
    for k in keys[:-1]:
        if k not in target or not isinstance(target[k], dict):
            error(f"Invalid config key: {key}")
            raise typer.Exit(code=2)
        target = target[k]

    final_key = keys[-1]
    if final_key not in target:
        error(f"Unknown config key: {key}")
        raise typer.Exit(code=2)

    # Type coerce the value to match the current field type.
    current = target[final_key]
    if isinstance(current, bool):
        coerced = value.lower() in ("true", "1", "yes")
    elif isinstance(current, int):
        try:
            coerced = int(value)
        except ValueError:
            error(f"Expected integer for {key}, got: {value}")
            raise typer.Exit(code=2) from None
    elif current is None:
        coerced = value  # type: ignore[assignment]
    else:
        coerced = value  # type: ignore[assignment]

    target[final_key] = coerced

    try:
        new_config = GlobalConfig.model_validate(data)
    except Exception as exc:
        error(f"Validation error: {exc}")
        raise typer.Exit(code=2) from None

    save_global_config(new_config)
    success(f"Set {key} = {coerced}")


@config_app.command("reset")
def config_reset(
    ctx: typer.Context,
) -> None:
    """Reset configuration to defaults.

    Replaces the persisted global config with a fresh
    :class:`~specli.models.GlobalConfig` instance containing all
    default values. Asks for confirmation unless ``--force`` is active.

    Args:
        ctx: Typer context carrying the ``force`` flag.

    Raises:
        typer.Exit: If the user declines confirmation.

    Example::

        specli config reset
        specli config reset --force
    """
    from specli.config import save_global_config
    from specli.models import GlobalConfig

    force = ctx.obj.get("force", False) if ctx.obj else False
    if not force:
        confirmed = typer.confirm("Reset all config to defaults?")
        if not confirmed:
            info("Cancelled.")
            raise typer.Exit()

    save_global_config(GlobalConfig())
    success("Configuration reset to defaults.")
