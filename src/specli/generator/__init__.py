"""CLI generator -- build a Typer command tree from a parsed OpenAPI spec.

This sub-package is responsible for the second half of the specli pipeline:
taking a :class:`~specli.models.ParsedSpec` (produced by the parser) and
constructing a nested :class:`typer.Typer` application whose sub-commands mirror
the API's resource hierarchy.

Typical usage::

    from specli.generator import build_command_tree, apply_path_rules
    from specli.models import PathRulesConfig

    rules = PathRulesConfig(auto_strip_prefix=True)
    app = build_command_tree(parsed_spec, rules, request_callback=my_http_fn)
    app()  # invoke the CLI

Sub-modules:

* :mod:`~specli.generator.path_rules` -- Transform raw API paths into
  clean CLI command segments by stripping common prefixes, collapsing paths,
  and skipping unwanted segments.
* :mod:`~specli.generator.param_mapper` -- Map OpenAPI parameters to
  Typer positional arguments and ``--option`` flags with correct Python types.
* :mod:`~specli.generator.command_tree` -- The core algorithm that groups
  operations, builds the sub-app tree, and attaches leaf commands with
  dynamically generated function signatures.
"""

from specli.generator.command_tree import build_command_tree
from specli.generator.path_rules import apply_path_rules

__all__ = ["build_command_tree", "apply_path_rules"]
