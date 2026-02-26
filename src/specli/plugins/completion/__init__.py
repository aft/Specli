"""Shell completion plugin -- install and manage tab-completion scripts.

This plugin provides the ``specli completion`` command group with
sub-commands for installing and displaying shell completion scripts for
bash, zsh, fish, and PowerShell.

The main export is :data:`completion_app`, a :class:`typer.Typer` instance
registered as a sub-command group on the root CLI.
"""

from specli.plugins.completion.plugin import completion_app

__all__ = ["completion_app"]
