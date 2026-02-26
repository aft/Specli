"""Skill generation plugin -- generate Claude Code skills from OpenAPI specs.

This plugin provides the ``specli skill`` command group with a
``generate`` sub-command that produces Claude Code skill files (SKILL.md
and reference documents) from the active profile's OpenAPI specification.

The plugin also exports :func:`generate_skill` for programmatic use by
other modules (e.g. the build plugin's ``--generate-skill`` flag).

Exports:
    skill_app: :class:`typer.Typer` instance for the ``skill`` command group.
    generate_skill: Function to generate skill files from a parsed spec.
"""

from specli.plugins.skill.generator import generate_skill
from specli.plugins.skill.plugin import skill_app

__all__ = ["generate_skill", "skill_app"]
