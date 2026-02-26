"""Shell completion plugin -- install and display completion scripts.

This module implements the ``specli completion`` command group with two
sub-commands:

* ``completion install`` -- Auto-detect (or explicitly specify) the user's
  shell and write a completion script to the appropriate config directory.
* ``completion show`` -- Print the completion script to stdout for manual
  installation or piping to a file.

Supported shells: bash, zsh, fish, PowerShell.

The plugin first attempts to generate a rich completion script via Typer's
built-in ``--show-completion`` mechanism. If that fails (e.g. the binary is
not on ``PATH``), it falls back to a minimal ``eval``-based script that
generates completions at shell startup time.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional

import typer

from specli.output import error, info, success, suggest


completion_app = typer.Typer(no_args_is_help=True)
"""Typer application for the ``completion`` command group."""


def _try_show_completion(shell_name: str) -> str | None:
    """Attempt to generate a completion script by invoking the CLI binary.

    Runs ``specli --show-completion <shell_name>`` in a subprocess and
    returns the captured stdout if the command succeeds.

    Args:
        shell_name: The target shell (e.g. ``"bash"``, ``"zsh"``, ``"fish"``).

    Returns:
        The completion script as a string, or ``None`` if the binary was not
        found or the command returned a non-zero exit code.
    """
    try:
        result = subprocess.run(
            ["specli", "--show-completion", shell_name],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
    except FileNotFoundError:
        pass
    return None


@completion_app.command("install")
def completion_install(
    shell: Optional[str] = typer.Argument(
        None,
        help="Shell to install completion for (bash, zsh, fish, powershell). Auto-detected if omitted.",
    ),
) -> None:
    """Install shell completion for specli.

    Detects the current shell from the ``SHELL`` environment variable, or
    accepts an explicit shell name argument. Writes the completion script
    to the standard config directory for the target shell:

    * **bash**: ``~/.bash_completion.d/specli``
    * **zsh**: ``~/.zfunc/_specli``
    * **fish**: ``~/.config/fish/completions/specli.fish``
    * **powershell**: Prints manual installation instructions.

    Args:
        shell: Shell name (``bash``, ``zsh``, ``fish``, ``powershell``).
            Auto-detected from ``$SHELL`` if omitted.

    Raises:
        typer.Exit: With code 2 if the shell is unsupported.

    Example:
        ::

            specli completion install
            specli completion install bash
            specli completion install zsh
    """
    if shell is None:
        shell = os.path.basename(os.environ.get("SHELL", "bash"))

    shell = shell.lower()

    if shell == "bash":
        completion_dir = Path.home() / ".bash_completion.d"
        completion_dir.mkdir(exist_ok=True)
        script_path = completion_dir / "specli"

        generated = _try_show_completion("bash")
        if generated:
            script_path.write_text(generated)
        else:
            script = "_SPECLI_COMPLETE=bash_source specli"
            script_path.write_text(f'eval "$({script})"\n')

        success(f"Bash completion installed to {script_path}")
        suggest("Restart your shell or run: source ~/.bash_completion.d/specli")

    elif shell == "zsh":
        completion_dir = Path.home() / ".zfunc"
        completion_dir.mkdir(exist_ok=True)
        script_path = completion_dir / "_specli"

        generated = _try_show_completion("zsh")
        if generated:
            script_path.write_text(generated)
        else:
            script = "_SPECLI_COMPLETE=zsh_source specli"
            script_path.write_text(f'#compdef specli\neval "$({script})"\n')

        success(f"Zsh completion installed to {script_path}")
        suggest("Add to .zshrc: fpath+=~/.zfunc && autoload -Uz compinit && compinit")

    elif shell == "fish":
        completion_dir = Path.home() / ".config" / "fish" / "completions"
        completion_dir.mkdir(parents=True, exist_ok=True)
        script_path = completion_dir / "specli.fish"

        generated = _try_show_completion("fish")
        if generated:
            script_path.write_text(generated)
        else:
            script = "_SPECLI_COMPLETE=fish_source specli"
            script_path.write_text(f"eval ({script})\n")

        success(f"Fish completion installed to {script_path}")
        suggest("Restart your shell to activate completions.")

    elif shell == "powershell":
        info("For PowerShell, add this to your profile:")
        info(
            "  Register-ArgumentCompleter -Native -CommandName specli -ScriptBlock { ... }"
        )
        suggest("Run: specli --show-completion powershell")

    else:
        error(f"Unsupported shell: {shell}. Supported: bash, zsh, fish, powershell")
        raise typer.Exit(code=2)


@completion_app.command("show")
def completion_show(
    shell: str = typer.Argument(
        "bash",
        help="Shell to show completion for (bash, zsh, fish, powershell)",
    ),
) -> None:
    """Print the completion script for a shell to stdout.

    Useful for manual installation or piping directly into a file.
    Falls back to a minimal ``eval``-based script if the rich completion
    generator is unavailable.

    Args:
        shell: Shell name (``bash``, ``zsh``, ``fish``, ``powershell``).
            Defaults to ``"bash"``.

    Example:
        ::

            specli completion show bash
            specli completion show zsh > ~/.zfunc/_specli
    """
    generated = _try_show_completion(shell)
    if generated:
        print(generated)
    else:
        # Fallback: provide a basic eval-based script
        env_var = f"_SPECLI_COMPLETE={shell}_source"
        print(f'eval "$({env_var} specli)"')
