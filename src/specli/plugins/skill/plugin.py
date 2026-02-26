"""Skill generation plugin -- generate Claude Code skill files from an API spec.

This module implements the ``specli skill`` command group with a
``generate`` sub-command that loads the active profile's OpenAPI spec,
parses it, and delegates to :func:`~specli.plugins.skill.generate_skill` to
produce a ``SKILL.md`` file together with a ``references/`` directory
containing structured API documentation that Claude Code can consume as
context.

Usage::

    specli skill generate --output ./my-skill
    specli skill generate --profile myapi --output ./skills/myapi
"""

from __future__ import annotations

from typing import Optional

import typer

from specli.output import error, info, success, suggest


skill_app = typer.Typer(no_args_is_help=True)
"""Typer application for the ``skill`` command group."""


@skill_app.command("generate")
def skill_generate(
    output_dir: str = typer.Option(
        "./skill-output",
        "--output",
        "-o",
        help="Output directory for skill files.",
    ),
    profile: Optional[str] = typer.Option(
        None, "--profile", "-p", help="Profile name."
    ),
) -> None:
    """Generate a Claude Code skill from the API spec.

    Resolves the active profile, loads and parses its OpenAPI spec, then
    delegates to :func:`~specli.plugins.skill.generate_skill` to produce a
    ``SKILL.md`` and a ``references/`` directory with per-endpoint API
    documentation files.

    Args:
        output_dir: Filesystem path where the skill files will be
            written. Created if it does not exist.
        profile: Optional profile name override. When ``None``, the
            default profile from the project or global config is used.

    Raises:
        typer.Exit: With code 2 if no active profile or the spec fails
            to load; code 1 if skill generation encounters an error.

    Example::

        specli skill generate --output ./my-skill
        specli skill generate --profile myapi --output ./skills/myapi
    """
    from specli.config import resolve_config
    from specli.parser import load_spec, validate_openapi_version
    from specli.parser.extractor import extract_spec
    from specli.plugins.skill import generate_skill

    try:
        _, prof = resolve_config(cli_profile=profile)
    except Exception as exc:
        error(f"Config error: {exc}")
        raise typer.Exit(code=2) from None

    if prof is None:
        error("No active profile. Run: specli init --spec <url>")
        raise typer.Exit(code=2)

    info(f"Loading spec from profile: {prof.name}")

    try:
        raw = load_spec(prof.spec)
        version = validate_openapi_version(raw)
        spec = extract_spec(raw, version)
    except Exception as exc:
        error(f"Failed to load spec: {exc}")
        raise typer.Exit(code=2) from None

    info(f"Generating skill to: {output_dir}")

    try:
        result_path = generate_skill(spec, output_dir, prof)
    except Exception as exc:
        error(f"Skill generation failed: {exc}")
        raise typer.Exit(code=1) from None

    success(f"Skill generated at: {result_path}")
    suggest(f"Review: cat {result_path}/SKILL.md")
