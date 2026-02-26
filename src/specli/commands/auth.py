"""Auth commands -- manage authentication profiles.

Provides the ``specli auth`` sub-command group with commands to
configure, test, list, and remove authentication credentials for API
profiles. Credentials can be sourced from environment variables, files,
or interactive prompts, and are persisted alongside the profile
configuration.

Typical workflow::

    specli auth login myapi      # interactive setup
    specli auth test myapi       # verify credentials
    specli auth store-show myapi # inspect stored credential
"""

from __future__ import annotations

from typing import Optional

import typer

from specli.output import error, get_output, info, success, suggest


auth_app = typer.Typer(no_args_is_help=True)


@auth_app.command("login")
def auth_login(
    profile_name: str = typer.Argument(help="Profile name to configure auth for."),
) -> None:
    """Interactive auth setup for a profile.

    Loads the OpenAPI spec associated with the profile, presents the
    available security schemes, and prompts the user to select one and
    provide a credential source. The resulting
    :class:`~specli.models.AuthConfig` is saved to the profile.

    Args:
        profile_name: Name of an existing profile to configure
            authentication for.

    Raises:
        typer.Exit: With code 2 if the profile or spec cannot be loaded,
            or the user provides an invalid selection.

    Example::

        specli auth login myapi
    """
    from specli.config import load_profile, save_profile
    from specli.parser import load_spec, validate_openapi_version
    from specli.parser.extractor import extract_spec

    try:
        profile = load_profile(profile_name)
    except Exception as exc:
        error(str(exc))
        raise typer.Exit(code=2) from None

    try:
        raw = load_spec(profile.spec)
        version = validate_openapi_version(raw)
        parsed = extract_spec(raw, version)
    except Exception as exc:
        error(f"Failed to load spec: {exc}")
        raise typer.Exit(code=2) from None

    if not parsed.security_schemes:
        info("This API does not define any security schemes.")
        return

    # Present available schemes.
    schemes = list(parsed.security_schemes.values())
    info("Available auth schemes:")
    for i, scheme in enumerate(schemes, 1):
        label = f"  {i}. {scheme.name} ({scheme.type}"
        if scheme.scheme:
            label += f", {scheme.scheme}"
        label += ")"
        info(label)

    # Auto-select for single scheme, prompt for multiple.
    if len(schemes) == 1:
        selected = schemes[0]
        info(f"Auto-selected: {selected.name}")
    else:
        try:
            choice = typer.prompt("Select scheme number", default="1")
            idx = int(choice) - 1
        except (ValueError, KeyboardInterrupt):
            error("Invalid selection.")
            raise typer.Exit(code=2) from None

        if idx < 0 or idx >= len(schemes):
            error(f"Selection must be between 1 and {len(schemes)}.")
            raise typer.Exit(code=2)
        selected = schemes[idx]

    # Map the selected security scheme to an AuthConfig.
    auth_config = _scheme_to_auth_config(selected)

    # Prompt for credential source.
    try:
        source = typer.prompt(
            "Credential source (env:VAR, file:/path, or 'prompt')",
            default="prompt",
        )
    except KeyboardInterrupt:
        info("\nCancelled.")
        raise typer.Exit() from None

    auth_config.source = source

    profile.auth = auth_config
    save_profile(profile)
    success(f'Auth configured for "{profile_name}".')
    suggest(f"Test it: specli auth test {profile_name}")


@auth_app.command("add")
def auth_add(
    profile_name: str = typer.Argument(help="Profile name."),
    auth_type: str = typer.Option(
        ..., "--type", "-t", help="Auth type: api_key, bearer, basic."
    ),
    header: Optional[str] = typer.Option(
        None, "--header", help="Header name (for api_key)."
    ),
    source: str = typer.Option(
        "prompt",
        "--source",
        "-s",
        help="Credential source: env:VAR, file:/path, prompt.",
    ),
) -> None:
    """Non-interactive auth setup.

    Creates an :class:`~specli.models.AuthConfig` directly from
    CLI options without inspecting the spec's security schemes. Useful
    for scripted or CI/CD environments.

    Args:
        profile_name: Name of an existing profile.
        auth_type: Authentication type -- ``api_key``, ``bearer``, or
            ``basic``.
        header: Header name to use for ``api_key`` auth (e.g.
            ``X-API-Key``).  Ignored for other auth types.
        source: Where to obtain the credential at request time.
            Accepts ``env:VAR_NAME``, ``file:/path/to/secret``, or
            ``prompt`` (ask interactively).

    Raises:
        typer.Exit: With code 2 if the profile cannot be loaded.

    Example::

        specli auth add myapi --type api_key --header X-API-Key --source env:MY_KEY
        specli auth add myapi --type bearer --source env:MY_TOKEN
    """
    from specli.config import load_profile, save_profile
    from specli.models import AuthConfig

    try:
        profile = load_profile(profile_name)
    except Exception as exc:
        error(str(exc))
        raise typer.Exit(code=2) from None

    auth_config = AuthConfig(type=auth_type, source=source)
    if header:
        auth_config.header = header

    profile.auth = auth_config
    save_profile(profile)
    success(f'Auth configured for "{profile_name}" ({auth_type}).')
    suggest(f"Test it: specli auth test {profile_name}")


@auth_app.command("list")
def auth_list() -> None:
    """List all configured profiles with auth status.

    Prints a table showing each profile's name, authentication type, and
    credential source. Profiles that fail to load are shown with an
    ``error`` status.

    Example::

        specli auth list
    """
    from specli.config import list_profiles, load_profile

    profiles = list_profiles()
    if not profiles:
        info("No profiles configured.")
        suggest("Create one: specli init --spec <url>")
        return

    output = get_output()
    headers = ["Profile", "Auth Type", "Source"]
    rows: list[list[str]] = []
    for name in profiles:
        try:
            profile = load_profile(name)
        except Exception:
            rows.append([name, "error", "-"])
            continue
        auth_type = profile.auth.type if profile.auth else "none"
        source = profile.auth.source if profile.auth else "-"
        rows.append([name, auth_type, source])

    output.print_table(headers, rows, title="Configured Profiles")


@auth_app.command("test")
def auth_test(
    profile_name: str = typer.Argument(help="Profile name to test."),
) -> None:
    """Test auth by making a request to the API.

    Creates a :class:`~specli.client.SyncClient` with the profile's
    auth config and sends a ``GET /`` request to the base URL. Reports
    the HTTP status code on success.

    Args:
        profile_name: Name of the profile whose auth to test.

    Raises:
        typer.Exit: With code 2 if the profile cannot be loaded, code 3
            if no auth is configured or the request fails.

    Example::

        specli auth test myapi
    """
    from specli.auth import create_default_manager
    from specli.client import SyncClient
    from specli.config import load_profile

    try:
        profile = load_profile(profile_name)
    except Exception as exc:
        error(str(exc))
        raise typer.Exit(code=2) from None

    if not profile.auth:
        error(f'Profile "{profile_name}" has no auth configured.')
        suggest(f"Set up auth: specli auth login {profile_name}")
        raise typer.Exit(code=3)

    auth_manager = create_default_manager()
    info(f"Testing auth for: {profile_name} ({profile.auth.type})")

    try:
        with SyncClient(profile=profile, auth_manager=auth_manager) as client:
            response = client.get("/")
            success(f"Auth successful! Status: {response.status_code}")
    except Exception as exc:
        error(f"Auth test failed: {exc}")
        raise typer.Exit(code=3) from None


@auth_app.command("remove")
def auth_remove(
    ctx: typer.Context,
    profile_name: str = typer.Argument(help="Profile name to remove auth from."),
) -> None:
    """Remove auth configuration from a profile.

    Sets the profile's ``auth`` field to ``None`` and saves. Asks for
    confirmation unless the ``--force`` flag is active.

    Args:
        ctx: Typer context carrying the ``force`` flag.
        profile_name: Name of the profile to strip auth from.

    Raises:
        typer.Exit: With code 2 if the profile cannot be loaded.

    Example::

        specli auth remove myapi
        specli auth remove myapi --force
    """
    from specli.config import load_profile, save_profile

    try:
        profile = load_profile(profile_name)
    except Exception as exc:
        error(str(exc))
        raise typer.Exit(code=2) from None

    if not profile.auth:
        info(f'Profile "{profile_name}" has no auth to remove.')
        return

    force = ctx.obj.get("force", False) if ctx.obj else False
    if not force:
        confirmed = typer.confirm(f'Remove auth from "{profile_name}"?')
        if not confirmed:
            info("Cancelled.")
            raise typer.Exit()

    profile.auth = None
    save_profile(profile)
    success(f'Auth removed from "{profile_name}".')


@auth_app.command("store-show")
def auth_store_show(
    profile_name: str = typer.Argument(help="Profile name to show stored credential for."),
) -> None:
    """Show stored credential info for a profile.

    Reads the credential store for the given profile and displays a
    table with the auth type, credential name, a truncated credential
    preview, expiration, and current validity.

    Args:
        profile_name: Name of the profile whose credential to inspect.

    Example::

        specli auth store-show myapi
    """
    from specli.auth.credential_store import CredentialStore

    store = CredentialStore(profile_name)
    entry = store.load()
    if entry is None:
        info(f'No stored credential for "{profile_name}".')
        return

    output = get_output()
    headers = ["Field", "Value"]
    rows = [
        ["Profile", profile_name],
        ["Auth Type", entry.auth_type],
        ["Credential Name", entry.credential_name or "-"],
        ["Credential", entry.credential[:8] + "..." if len(entry.credential) > 8 else entry.credential],
        ["Expires At", str(entry.expires_at) if entry.expires_at else "never"],
        ["Valid", str(store.is_valid())],
    ]
    output.print_table(headers, rows, title="Stored Credential")


@auth_app.command("store-clear")
def auth_store_clear(
    ctx: typer.Context,
    profile_name: str = typer.Argument(help="Profile name to clear stored credential for."),
) -> None:
    """Clear stored credential for a profile.

    Deletes the persisted credential entry from the
    :class:`~specli.auth.credential_store.CredentialStore`. Asks
    for confirmation unless the ``--force`` flag is active.

    Args:
        ctx: Typer context carrying the ``force`` flag.
        profile_name: Name of the profile whose credential to clear.

    Example::

        specli auth store-clear myapi
        specli auth store-clear myapi --force
    """
    from specli.auth.credential_store import CredentialStore

    store = CredentialStore(profile_name)
    entry = store.load()
    if entry is None:
        info(f'No stored credential for "{profile_name}".')
        return

    force = ctx.obj.get("force", False) if ctx.obj else False
    if not force:
        confirmed = typer.confirm(f'Clear stored credential for "{profile_name}"?')
        if not confirmed:
            info("Cancelled.")
            raise typer.Exit()

    store.clear()
    success(f'Stored credential cleared for "{profile_name}".')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scheme_to_auth_config(scheme):  # noqa: ANN001, ANN202
    """Convert a :class:`~specli.models.SecurityScheme` to an :class:`~specli.models.AuthConfig`.

    Maps OpenAPI security scheme types (``apiKey``, ``http``, ``oauth2``,
    ``openIdConnect``) to the corresponding ``AuthConfig`` type and
    populates scheme-specific fields such as ``header``, ``token_url``,
    and ``authorization_url``.

    Args:
        scheme: Parsed security scheme from the OpenAPI spec.

    Returns:
        A new :class:`~specli.models.AuthConfig` instance with
        type and transport fields populated from *scheme*.
    """
    from specli.models import AuthConfig

    if scheme.type == "apiKey":
        return AuthConfig(
            type="api_key",
            header=scheme.param_name,
            location=scheme.location or "header",
        )

    if scheme.type == "http":
        if scheme.scheme == "bearer":
            return AuthConfig(type="bearer")
        if scheme.scheme == "basic":
            return AuthConfig(type="basic")
        return AuthConfig(type=scheme.scheme or "bearer")

    if scheme.type == "oauth2":
        config = AuthConfig(type="oauth2_client_credentials")
        if scheme.flows:
            if "authorizationCode" in scheme.flows:
                config.type = "oauth2_auth_code"
                flow = scheme.flows["authorizationCode"]
                config.authorization_url = flow.get("authorizationUrl")
                config.token_url = flow.get("tokenUrl")
            elif "clientCredentials" in scheme.flows:
                flow = scheme.flows["clientCredentials"]
                config.token_url = flow.get("tokenUrl")
        return config

    if scheme.type == "openIdConnect":
        return AuthConfig(
            type="openid_connect",
            openid_connect_url=scheme.openid_connect_url,
        )

    return AuthConfig(type=scheme.type)
