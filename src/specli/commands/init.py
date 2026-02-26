"""Init command -- initialize a profile from an OpenAPI spec.

Implements the ``specli init`` top-level command. This is the
typical entry point for first-time setup: it fetches an OpenAPI spec
(from a URL, local file, or stdin), validates it, auto-detects settings
like the base URL and common path prefix, creates a
:class:`~specli.models.Profile`, and writes a project-local
``specli.json`` config file.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

import typer

from specli.output import debug, error, info, success, suggest


def init_command(
    spec: str = typer.Option(
        ...,
        "--spec",
        "-s",
        help="OpenAPI spec URL or file path (use '-' for stdin).",
    ),
    name: Optional[str] = typer.Option(
        None,
        "--name",
        "-n",
        help="Profile name (auto-detected from spec title if omitted).",
    ),
    base_url: Optional[str] = typer.Option(
        None, "--base-url", help="Override base URL."
    ),
) -> None:
    """Initialize a profile from an OpenAPI spec.

    Fetches the spec, validates the OpenAPI version, extracts operations
    and security schemes, auto-detects the base URL from servers, finds
    a common path prefix for path-rule configuration, creates a
    :class:`~specli.models.Profile`, and writes a project-local
    ``specli.json`` config file.

    Args:
        spec: URL, local file path, or ``-`` for stdin.
        name: Profile name. When omitted, the spec's ``info.title`` is
            slugified to produce a URL-safe default.
        base_url: Override for the API base URL. When omitted, the first
            ``servers[].url`` entry from the spec is used.

    Raises:
        typer.Exit: With code 2 if the spec cannot be fetched, parsed,
            or fails OpenAPI version validation.

    Example::

        specli init --spec https://api.example.com/openapi.json
        specli init --spec ./openapi.yaml --name myapi
        curl -s https://api.example.com/spec | specli init --spec -
    """
    from specli.config import profile_exists, save_profile
    from specli.generator.path_rules import find_common_prefix
    from specli.models import PathRulesConfig, Profile
    from specli.parser import load_spec, validate_openapi_version
    from specli.parser.extractor import extract_spec

    info(f"Fetching spec from: {spec}")
    try:
        raw = load_spec(spec)
    except Exception as exc:
        error(f"Failed to load spec: {exc}")
        raise typer.Exit(code=2) from None

    try:
        version = validate_openapi_version(raw)
    except Exception as exc:
        error(f"Invalid spec: {exc}")
        raise typer.Exit(code=2) from None

    parsed = extract_spec(raw, version)

    info(f"Validated: {parsed.info.title} v{parsed.info.version} (OpenAPI {version})")

    # Determine profile name.
    profile_name = name or _slugify(parsed.info.title)

    if profile_exists(profile_name):
        info(f'Profile "{profile_name}" already exists and will be overwritten.')

    # Determine base URL.
    detected_base_url = base_url
    if not detected_base_url and parsed.servers:
        detected_base_url = parsed.servers[0].url

    # Detect path rules.
    paths = [op.path for op in parsed.operations]
    common_prefix = find_common_prefix(paths)
    path_rules = PathRulesConfig(auto_strip_prefix=bool(common_prefix))

    if common_prefix:
        debug(f"Detected common prefix: {common_prefix}")

    # Create the profile.
    profile = Profile(
        name=profile_name,
        spec=spec,
        base_url=detected_base_url,
        path_rules=path_rules,
    )

    # Detect security schemes and suggest auth setup.
    if parsed.security_schemes:
        scheme_names = ", ".join(s.name for s in parsed.security_schemes.values())
        info(
            f"Detected {len(parsed.security_schemes)} security scheme(s): {scheme_names}"
        )
        suggest(f"Set up auth: specli auth login {profile_name}")

    save_profile(profile)

    # Write project-local config.
    project_config = {"default_profile": profile_name}
    project_config_path = Path("specli.json")
    project_config_path.write_text(json.dumps(project_config, indent=2) + "\n")

    success(f'Profile "{profile_name}" created.')
    suggest(f"Inspect API: specli inspect paths --profile {profile_name}")
    suggest(f"View auth: specli inspect auth --profile {profile_name}")


def _slugify(text: str) -> str:
    """Convert text to a URL-safe slug."""
    slug = text.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug or "default"
