"""Build plugin -- compile standalone CLI binaries or pip-installable packages.

This plugin provides the ``specli build`` command group with two
sub-commands:

* ``build compile`` -- Produce a self-contained binary via PyInstaller with
  the OpenAPI spec and profile configuration baked in.
* ``build generate`` -- Produce a pip-installable Python package directory
  with the spec and profile baked in, installable via ``pip install ./pkg``.

Both sub-commands share an enrichment pipeline that can optionally:

* Enrich operation descriptions from source code.
* Import/export CLI string overrides for translation.
* Generate Claude Code skill files (SKILL.md + references).

The main export is :data:`build_app`, a :class:`typer.Typer` instance
registered as a sub-command group on the root CLI.
"""

from specli.plugins.build.plugin import build_app

__all__ = ["build_app"]
