"""Built-in CLI sub-commands for specli.

This package groups all Typer sub-command modules that form the CLI's
top-level command tree:

* :mod:`~specli.commands.init` -- create a profile from an OpenAPI spec.
* :mod:`~specli.commands.auth` -- configure and test authentication.
* :mod:`~specli.commands.config` -- view and modify global settings.
* :mod:`~specli.commands.inspect` -- examine paths, schemas, and auth
  defined in a spec.

Each module either exports a :class:`typer.Typer` sub-application (for
multi-command groups like ``auth`` and ``config``) or a plain callback
function registered directly on the root app (for single commands like
``init``).
"""
