"""Output formatting system with strict stdout/stderr discipline.

Follows `clig.dev <https://clig.dev/>`_ conventions:

* **stdout** -- primary data only (API responses, JSON, tables). This is
  what downstream tools pipe and parse.
* **stderr** -- all diagnostics (progress, status, warnings, errors,
  suggestions). Never contaminates the data stream.
* **TTY detection** -- Rich formatting when stdout is an interactive
  terminal, plain text when piped to another process.
* **Colour control** -- respects ``NO_COLOR``, ``TERM=dumb``, and the
  ``--no-color`` CLI flag.

The module exposes two layers:

1. :class:`OutputManager` -- a stateful object holding format preferences,
   Rich consoles, and quiet/verbose flags. Created once in
   :func:`~specli.app.main_callback` and installed via :func:`set_output`.
2. Module-level convenience functions (:func:`info`, :func:`error`,
   :func:`debug`, etc.) that delegate to the global ``OutputManager``
   instance so callers do not need to pass the manager around.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from enum import Enum
from typing import Any, Optional

from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table


class OutputFormat(str, Enum):
    """Enumeration of supported output formats.

    ``AUTO`` resolves to ``RICH`` when stdout is an interactive TTY and colour
    is not disabled, or to ``PLAIN`` otherwise. Callers can force a specific
    format via the ``--json`` or ``--plain`` CLI flags.
    """

    AUTO = "auto"
    JSON = "json"
    PLAIN = "plain"
    RICH = "rich"


class OutputManager:
    """Central manager for all CLI output with stdout/stderr discipline.

    Maintains two Rich :class:`~rich.console.Console` instances -- one for
    stdout (data) and one for stderr (diagnostics) -- and routes every
    output call to the correct stream with appropriate formatting.

    Typically created once during CLI startup in
    :func:`~specli.app.main_callback` and installed globally via
    :func:`set_output`.

    Args:
        format: Desired output format. ``AUTO`` resolves based on TTY
            detection.
        no_color: Disable all colour and Rich markup.
        quiet: Suppress non-essential informational messages on stderr.
        verbose: Enable debug-level messages on stderr.
        use_pager: Pipe long output through the system pager when
            stdout is a TTY.
        output_file: If set, redirect primary data output to this file
            path instead of stdout.
    """

    def __init__(
        self,
        format: OutputFormat = OutputFormat.AUTO,
        no_color: bool = False,
        quiet: bool = False,
        verbose: bool = False,
        use_pager: bool = True,
        output_file: Optional[str] = None,
    ) -> None:
        self._no_color = no_color or _should_disable_color()
        self._quiet = quiet
        self._verbose = verbose
        self._use_pager = use_pager
        self._output_file = output_file

        # Resolve format: AUTO picks RICH for interactive TTY, PLAIN otherwise
        if format == OutputFormat.AUTO:
            self._format = (
                OutputFormat.RICH if _is_tty() and not self._no_color else OutputFormat.PLAIN
            )
        else:
            self._format = format

        # Console for stdout (data output)
        self._stdout = Console(
            file=sys.stdout,
            no_color=self._no_color,
            force_terminal=(self._format == OutputFormat.RICH),
        )

        # Console for stderr (diagnostics)
        self._stderr = Console(
            file=sys.stderr,
            no_color=self._no_color,
            stderr=True,
        )

    @property
    def format(self) -> OutputFormat:
        """The resolved output format."""
        return self._format

    @property
    def is_quiet(self) -> bool:
        """Whether quiet mode is enabled."""
        return self._quiet

    @property
    def is_verbose(self) -> bool:
        """Whether verbose mode is enabled."""
        return self._verbose

    # ------------------------------------------------------------------ #
    # Data output (stdout)
    # ------------------------------------------------------------------ #

    def format_response(self, data: Any, content_type: str = "application/json") -> None:
        """Format and output API response data to stdout.

        Dispatches to the appropriate renderer (JSON, plain, or Rich) based
        on the resolved :attr:`format`. When an ``output_file`` was
        configured, data is written there instead of stdout.

        Args:
            data: Response payload -- typically a dict, list, or raw string.
            content_type: MIME type hint used by the Rich renderer to choose
                syntax highlighting.
        """
        # If output file is set, write raw data and return
        if self._output_file:
            self._write_to_file(data)
            return

        if self._format == OutputFormat.JSON:
            self._print_json(data)
        elif self._format == OutputFormat.PLAIN:
            self._print_plain(data)
        else:
            # RICH
            self._print_rich(data, content_type)

    def print_data(self, text: str) -> None:
        """Print raw text to stdout (or to the configured output file).

        Args:
            text: The string to write. A trailing newline is appended if
                missing.
        """
        if self._output_file:
            with open(self._output_file, "a", encoding="utf-8") as f:
                f.write(text)
                if not text.endswith("\n"):
                    f.write("\n")
        else:
            print(text, file=sys.stdout, flush=True)

    def print_table(
        self,
        headers: list[str],
        rows: list[list[str]],
        title: Optional[str] = None,
    ) -> None:
        """Print tabular data to stdout in the active format.

        * **Rich mode** -- styled :class:`~rich.table.Table` with column
          headers.
        * **JSON mode** -- array of objects keyed by header names.
        * **Plain mode** -- tab-separated values, one row per line.

        Args:
            headers: Column header strings.
            rows: List of rows, where each row is a list of cell strings.
            title: Optional table title (Rich mode only).
        """
        if self._format == OutputFormat.JSON:
            records = [dict(zip(headers, row)) for row in rows]
            self.print_data(json.dumps(records, indent=2, ensure_ascii=False))

        elif self._format == OutputFormat.PLAIN:
            self.print_data("\t".join(headers))
            for row in rows:
                self.print_data("\t".join(row))

        else:
            # RICH
            table = Table(title=title, show_header=True, header_style="bold cyan")
            for h in headers:
                table.add_column(h)
            for row in rows:
                table.add_row(*row)
            self._stdout.print(table)

    # ------------------------------------------------------------------ #
    # Diagnostics (stderr)
    # ------------------------------------------------------------------ #

    def info(self, message: str) -> None:
        """Print an informational message to stderr. Suppressed by ``--quiet``.

        Args:
            message: The message text.
        """
        if not self._quiet:
            if self._no_color:
                print(message, file=sys.stderr, flush=True)
            else:
                self._stderr.print(message)

    def success(self, message: str) -> None:
        """Print a green success message to stderr. Suppressed by ``--quiet``.

        Args:
            message: The message text.
        """
        if not self._quiet:
            if self._no_color:
                print(message, file=sys.stderr, flush=True)
            else:
                self._stderr.print(f"[green]{message}[/green]")

    def warning(self, message: str) -> None:
        """Print a yellow warning to stderr. NOT suppressed by ``--quiet``.

        Args:
            message: The warning text.
        """
        if self._no_color:
            print(f"Warning: {message}", file=sys.stderr, flush=True)
        else:
            self._stderr.print(f"[yellow]Warning:[/yellow] {message}")

    def error(self, message: str) -> None:
        """Print a bold-red error to stderr. Never suppressed.

        Args:
            message: The error text.
        """
        if self._no_color:
            print(f"Error: {message}", file=sys.stderr, flush=True)
        else:
            self._stderr.print(f"[bold red]Error:[/bold red] {message}")

    def suggest(self, message: str) -> None:
        """Print a dimmed next-step suggestion to stderr. Suppressed by ``--quiet``.

        Args:
            message: The suggestion text (prefixed with an arrow on output).
        """
        if not self._quiet:
            formatted = f"\u2192 {message}"
            if self._no_color:
                print(formatted, file=sys.stderr, flush=True)
            else:
                self._stderr.print(f"[dim]{formatted}[/dim]")

    def debug(self, message: str) -> None:
        """Print a debug message to stderr. Only shown when ``--verbose`` is active.

        Args:
            message: The debug text (prefixed with ``[debug]`` on output).
        """
        if self._verbose:
            if self._no_color:
                print(f"[debug] {message}", file=sys.stderr, flush=True)
            else:
                self._stderr.print(f"[dim][debug] {message}[/dim]")

    def progress(self, message: str) -> None:
        """Print a dimmed progress message to stderr.

        Only displayed when stdout is a TTY. Suppressed by ``--quiet``.

        Args:
            message: The progress text.
        """
        if not self._quiet and _is_tty():
            if self._no_color:
                print(message, file=sys.stderr, flush=True)
            else:
                self._stderr.print(f"[dim]{message}[/dim]")

    # ------------------------------------------------------------------ #
    # Pager support
    # ------------------------------------------------------------------ #

    def paged_output(self, text: str) -> None:
        """Output text through the system pager when interactive.

        Uses ``$PAGER`` with a fallback to ``less -FIRX``. If stdout is not
        a TTY, the pager is disabled, or the pager process fails, the text
        is printed directly to stdout.

        Args:
            text: The full text to display.
        """
        if not self._use_pager or not _is_tty():
            print(text, file=sys.stdout, flush=True)
            return

        pager_cmd = os.environ.get("PAGER", "less -FIRX")

        try:
            proc = subprocess.Popen(
                pager_cmd,
                shell=True,
                stdin=subprocess.PIPE,
                encoding="utf-8",
            )
            proc.communicate(input=text)
        except (OSError, BrokenPipeError):
            # Pager not available or broken pipe -- fall back to direct print
            print(text, file=sys.stdout, flush=True)

    # ------------------------------------------------------------------ #
    # Private helpers
    # ------------------------------------------------------------------ #

    def _print_json(self, data: Any) -> None:
        """Print data as raw JSON to stdout."""
        if isinstance(data, (dict, list)):
            self.print_data(json.dumps(data, indent=2, ensure_ascii=False, default=str))
        elif isinstance(data, str):
            # Try to parse as JSON for re-formatting; otherwise output as-is
            try:
                parsed = json.loads(data)
                self.print_data(json.dumps(parsed, indent=2, ensure_ascii=False, default=str))
            except (json.JSONDecodeError, TypeError):
                self.print_data(data)
        else:
            self.print_data(json.dumps(data, indent=2, ensure_ascii=False, default=str))

    def _print_plain(self, data: Any) -> None:
        """Print data as plain text to stdout."""
        if isinstance(data, dict):
            for key, value in data.items():
                self.print_data(f"{key}\t{value}")
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    self.print_data(
                        "\t".join(str(v) for v in item.values())
                    )
                else:
                    self.print_data(str(item))
        elif isinstance(data, str):
            self.print_data(data)
        else:
            self.print_data(str(data))

    def _print_rich(self, data: Any, content_type: str) -> None:
        """Print data with Rich formatting to stdout."""
        if isinstance(data, (dict, list)):
            json_str = json.dumps(data, indent=2, ensure_ascii=False, default=str)
            syntax = Syntax(json_str, "json", theme="monokai", word_wrap=True)
            self._stdout.print(syntax)
        elif isinstance(data, str):
            # Try JSON first
            try:
                parsed = json.loads(data)
                json_str = json.dumps(parsed, indent=2, ensure_ascii=False, default=str)
                syntax = Syntax(json_str, "json", theme="monokai", word_wrap=True)
                self._stdout.print(syntax)
            except (json.JSONDecodeError, TypeError):
                self._stdout.print(data)
        else:
            self._stdout.print(str(data))

    def _write_to_file(self, data: Any) -> None:
        """Write data to the configured output file."""
        assert self._output_file is not None
        if isinstance(data, (dict, list)):
            content = json.dumps(data, indent=2, ensure_ascii=False, default=str)
        elif isinstance(data, str):
            content = data
        else:
            content = str(data)

        with open(self._output_file, "w", encoding="utf-8") as f:
            f.write(content)
            if not content.endswith("\n"):
                f.write("\n")


# ------------------------------------------------------------------ #
# Module-level helpers
# ------------------------------------------------------------------ #


def _is_tty() -> bool:
    """Check if stdout is a TTY."""
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def _should_disable_color() -> bool:
    """Check if color should be disabled per clig.dev.

    Returns True when NO_COLOR env var is set (any value) or TERM=dumb.
    """
    if os.environ.get("NO_COLOR") is not None:
        return True
    if os.environ.get("TERM") == "dumb":
        return True
    return False


# ------------------------------------------------------------------ #
# Global output instance (set during app startup)
# ------------------------------------------------------------------ #

_output: Optional[OutputManager] = None


def get_output() -> OutputManager:
    """Return the global :class:`OutputManager` instance.

    If no instance has been installed via :func:`set_output`, a default
    ``OutputManager`` with ``AUTO`` format is created lazily.

    Returns:
        The active :class:`OutputManager`.
    """
    global _output
    if _output is None:
        _output = OutputManager()
    return _output


def set_output(output: OutputManager) -> None:
    """Install *output* as the global :class:`OutputManager` instance.

    Called once during CLI startup from :func:`~specli.app.main_callback`.

    Args:
        output: The configured manager to install.
    """
    global _output
    _output = output


def reset_output() -> None:
    """Reset the global :class:`OutputManager` to ``None``.

    Primarily useful in test suites to ensure a clean state between tests.
    """
    global _output
    _output = None


# ------------------------------------------------------------------ #
# Convenience functions that use the global instance
# ------------------------------------------------------------------ #


def format_response(data: Any, content_type: str = "application/json") -> None:
    """Format and output API response data to stdout via the global :class:`OutputManager`.

    Args:
        data: Response payload (dict, list, or string).
        content_type: MIME type hint for syntax highlighting.
    """
    get_output().format_response(data, content_type)


def print_data(text: str) -> None:
    """Print raw data to stdout via the global OutputManager."""
    get_output().print_data(text)


def print_table(
    headers: list[str],
    rows: list[list[str]],
    title: Optional[str] = None,
) -> None:
    """Print tabular data to stdout via the global :class:`OutputManager`.

    Args:
        headers: Column header strings.
        rows: Row data as lists of cell strings.
        title: Optional table title (Rich mode only).
    """
    get_output().print_table(headers, rows, title)


def info(message: str) -> None:
    """Print info message to stderr via the global OutputManager."""
    get_output().info(message)


def error(message: str) -> None:
    """Print error to stderr via the global OutputManager."""
    get_output().error(message)


def success(message: str) -> None:
    """Print success message to stderr via the global OutputManager."""
    get_output().success(message)


def warning(message: str) -> None:
    """Print warning to stderr via the global OutputManager."""
    get_output().warning(message)


def suggest(message: str) -> None:
    """Print next-step suggestion to stderr via the global OutputManager."""
    get_output().suggest(message)


def debug(message: str) -> None:
    """Print debug message to stderr via the global OutputManager."""
    get_output().debug(message)


def progress(message: str) -> None:
    """Print progress message to stderr via the global OutputManager."""
    get_output().progress(message)


def paged_output(text: str) -> None:
    """Output text through pager via the global OutputManager."""
    get_output().paged_output(text)
