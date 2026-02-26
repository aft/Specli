"""Tests for the output formatting system.

Covers:
- OutputFormat resolution (auto -> rich/plain based on TTY)
- NO_COLOR / TERM=dumb color disabling
- stdout vs stderr discipline
- Quiet mode suppression rules
- Verbose mode debug output
- JSON format raw output
- Plain format tab-separated output
- format_response with dicts, lists, strings, and other types
- print_table in all three modes
- Pager invocation
- Output file redirection
- Global instance management
- Convenience functions
"""

from __future__ import annotations

import json
import os
import sys
from io import StringIO
from unittest.mock import patch

import pytest

from specli.output import (
    OutputFormat,
    OutputManager,
    _is_tty,
    _should_disable_color,
    get_output,
    reset_output,
    set_output,
)
from specli import output as output_module


# ------------------------------------------------------------------ #
# Fixtures
# ------------------------------------------------------------------ #


@pytest.fixture(autouse=True)
def _reset_global_output():
    """Ensure the global output instance is reset between tests."""
    reset_output()
    yield
    reset_output()


@pytest.fixture()
def non_tty(monkeypatch):
    """Patch stdout.isatty() to return False."""
    monkeypatch.setattr("specli.output._is_tty", lambda: False)


@pytest.fixture()
def tty(monkeypatch):
    """Patch stdout.isatty() to return True."""
    monkeypatch.setattr("specli.output._is_tty", lambda: True)


# ------------------------------------------------------------------ #
# OutputFormat resolution
# ------------------------------------------------------------------ #


class TestOutputFormatResolution:
    """Test that AUTO format resolves correctly based on environment."""

    def test_auto_resolves_to_plain_when_not_tty(self, non_tty):
        mgr = OutputManager(format=OutputFormat.AUTO)
        assert mgr.format == OutputFormat.PLAIN

    def test_auto_resolves_to_rich_when_tty(self, tty, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("TERM", raising=False)
        mgr = OutputManager(format=OutputFormat.AUTO)
        assert mgr.format == OutputFormat.RICH

    def test_auto_resolves_to_plain_when_tty_but_no_color(self, tty, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        mgr = OutputManager(format=OutputFormat.AUTO, no_color=True)
        assert mgr.format == OutputFormat.PLAIN

    def test_explicit_json_stays_json(self, non_tty):
        mgr = OutputManager(format=OutputFormat.JSON)
        assert mgr.format == OutputFormat.JSON

    def test_explicit_plain_stays_plain(self, tty, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        mgr = OutputManager(format=OutputFormat.PLAIN)
        assert mgr.format == OutputFormat.PLAIN

    def test_explicit_rich_stays_rich(self, non_tty):
        mgr = OutputManager(format=OutputFormat.RICH)
        assert mgr.format == OutputFormat.RICH


# ------------------------------------------------------------------ #
# NO_COLOR / TERM=dumb detection
# ------------------------------------------------------------------ #


class TestColorDisabling:
    """Test that NO_COLOR and TERM=dumb are respected."""

    def test_no_color_env_disables_color(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "")
        assert _should_disable_color() is True

    def test_no_color_env_any_value(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "yes")
        assert _should_disable_color() is True

    def test_term_dumb_disables_color(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("TERM", "dumb")
        assert _should_disable_color() is True

    def test_normal_term_keeps_color(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setenv("TERM", "xterm-256color")
        assert _should_disable_color() is False

    def test_no_env_vars_keeps_color(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("TERM", raising=False)
        assert _should_disable_color() is False

    def test_no_color_flag_overrides(self, tty, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("TERM", raising=False)
        mgr = OutputManager(format=OutputFormat.AUTO, no_color=True)
        assert mgr.format == OutputFormat.PLAIN


# ------------------------------------------------------------------ #
# stdout vs stderr discipline
# ------------------------------------------------------------------ #


class TestStdoutStderrDiscipline:
    """Test that data goes to stdout and diagnostics go to stderr."""

    def test_print_data_goes_to_stdout(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.PLAIN, no_color=True)
        mgr.print_data("hello world")
        captured = capfd.readouterr()
        assert "hello world" in captured.out
        assert captured.err == ""

    def test_info_goes_to_stderr(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.PLAIN, no_color=True)
        mgr.info("some info")
        captured = capfd.readouterr()
        assert captured.out == ""
        assert "some info" in captured.err

    def test_error_goes_to_stderr(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.PLAIN, no_color=True)
        mgr.error("something broke")
        captured = capfd.readouterr()
        assert captured.out == ""
        assert "something broke" in captured.err

    def test_warning_goes_to_stderr(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.PLAIN, no_color=True)
        mgr.warning("be careful")
        captured = capfd.readouterr()
        assert captured.out == ""
        assert "be careful" in captured.err

    def test_success_goes_to_stderr(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.PLAIN, no_color=True)
        mgr.success("done")
        captured = capfd.readouterr()
        assert captured.out == ""
        assert "done" in captured.err

    def test_suggest_goes_to_stderr(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.PLAIN, no_color=True)
        mgr.suggest("Next: specli auth test myapi")
        captured = capfd.readouterr()
        assert captured.out == ""
        assert "Next: specli auth test myapi" in captured.err

    def test_debug_goes_to_stderr(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.PLAIN, no_color=True, verbose=True)
        mgr.debug("debug info")
        captured = capfd.readouterr()
        assert captured.out == ""
        assert "debug info" in captured.err

    def test_format_response_dict_goes_to_stdout(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.JSON, no_color=True)
        mgr.format_response({"key": "value"})
        captured = capfd.readouterr()
        assert '"key"' in captured.out
        assert '"value"' in captured.out
        assert captured.err == ""


# ------------------------------------------------------------------ #
# Quiet mode
# ------------------------------------------------------------------ #


class TestQuietMode:
    """Test that --quiet suppresses info/success/suggest but not warning/error."""

    def test_quiet_suppresses_info(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.PLAIN, no_color=True, quiet=True)
        mgr.info("should not appear")
        captured = capfd.readouterr()
        assert captured.err == ""

    def test_quiet_suppresses_success(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.PLAIN, no_color=True, quiet=True)
        mgr.success("should not appear")
        captured = capfd.readouterr()
        assert captured.err == ""

    def test_quiet_suppresses_suggest(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.PLAIN, no_color=True, quiet=True)
        mgr.suggest("should not appear")
        captured = capfd.readouterr()
        assert captured.err == ""

    def test_quiet_does_not_suppress_warning(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.PLAIN, no_color=True, quiet=True)
        mgr.warning("important warning")
        captured = capfd.readouterr()
        assert "important warning" in captured.err

    def test_quiet_does_not_suppress_error(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.PLAIN, no_color=True, quiet=True)
        mgr.error("critical error")
        captured = capfd.readouterr()
        assert "critical error" in captured.err

    def test_quiet_does_not_suppress_stdout_data(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.PLAIN, no_color=True, quiet=True)
        mgr.print_data("important data")
        captured = capfd.readouterr()
        assert "important data" in captured.out

    def test_quiet_suppresses_progress(self, capfd, tty):
        mgr = OutputManager(format=OutputFormat.PLAIN, no_color=True, quiet=True)
        mgr.progress("loading...")
        captured = capfd.readouterr()
        assert captured.err == ""


# ------------------------------------------------------------------ #
# Verbose mode
# ------------------------------------------------------------------ #


class TestVerboseMode:
    """Test that --verbose enables debug output."""

    def test_debug_hidden_by_default(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.PLAIN, no_color=True)
        mgr.debug("should not appear")
        captured = capfd.readouterr()
        assert captured.err == ""

    def test_debug_shown_with_verbose(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.PLAIN, no_color=True, verbose=True)
        mgr.debug("debug details")
        captured = capfd.readouterr()
        assert "debug details" in captured.err

    def test_debug_prefix_in_no_color(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.PLAIN, no_color=True, verbose=True)
        mgr.debug("trace info")
        captured = capfd.readouterr()
        assert "[debug]" in captured.err

    def test_verbose_property(self, non_tty):
        mgr = OutputManager(verbose=True)
        assert mgr.is_verbose is True
        mgr2 = OutputManager()
        assert mgr2.is_verbose is False


# ------------------------------------------------------------------ #
# Progress output
# ------------------------------------------------------------------ #


class TestProgress:
    """Test progress messages (TTY only, suppressed by quiet)."""

    def test_progress_shown_in_tty(self, capfd, tty):
        mgr = OutputManager(format=OutputFormat.PLAIN, no_color=True)
        mgr.progress("Fetching spec...")
        captured = capfd.readouterr()
        assert "Fetching spec..." in captured.err

    def test_progress_hidden_when_not_tty(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.PLAIN, no_color=True)
        mgr.progress("Fetching spec...")
        captured = capfd.readouterr()
        assert captured.err == ""


# ------------------------------------------------------------------ #
# JSON format output
# ------------------------------------------------------------------ #


class TestJsonFormat:
    """Test that JSON format outputs valid JSON to stdout."""

    def test_dict_as_json(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.JSON, no_color=True)
        data = {"users": [{"id": 1, "name": "Alice"}]}
        mgr.format_response(data)
        captured = capfd.readouterr()
        parsed = json.loads(captured.out)
        assert parsed == data

    def test_list_as_json(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.JSON, no_color=True)
        data = [1, 2, 3]
        mgr.format_response(data)
        captured = capfd.readouterr()
        parsed = json.loads(captured.out)
        assert parsed == data

    def test_string_that_is_json(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.JSON, no_color=True)
        data = '{"key": "value"}'
        mgr.format_response(data)
        captured = capfd.readouterr()
        parsed = json.loads(captured.out)
        assert parsed == {"key": "value"}

    def test_plain_string_as_json(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.JSON, no_color=True)
        mgr.format_response("hello world")
        captured = capfd.readouterr()
        assert "hello world" in captured.out

    def test_number_as_json(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.JSON, no_color=True)
        mgr.format_response(42)
        captured = capfd.readouterr()
        parsed = json.loads(captured.out)
        assert parsed == 42

    def test_nested_dict_as_json(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.JSON, no_color=True)
        data = {"a": {"b": {"c": [1, 2, 3]}}}
        mgr.format_response(data)
        captured = capfd.readouterr()
        parsed = json.loads(captured.out)
        assert parsed == data

    def test_json_output_is_indented(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.JSON, no_color=True)
        mgr.format_response({"key": "value"})
        captured = capfd.readouterr()
        # Indented JSON should have newlines
        assert "\n" in captured.out

    def test_no_diagnostics_leak_to_stdout_in_json(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.JSON, no_color=True)
        mgr.info("loading...")
        mgr.format_response({"result": "ok"})
        mgr.success("done")
        captured = capfd.readouterr()
        # stdout should contain only valid JSON
        parsed = json.loads(captured.out)
        assert parsed == {"result": "ok"}
        # stderr should have the diagnostics
        assert "loading..." in captured.err
        assert "done" in captured.err


# ------------------------------------------------------------------ #
# Plain format output
# ------------------------------------------------------------------ #


class TestPlainFormat:
    """Test plain format outputs tab-separated or simple text."""

    def test_dict_as_key_value(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.PLAIN, no_color=True)
        data = {"name": "Alice", "age": "30"}
        mgr.format_response(data)
        captured = capfd.readouterr()
        lines = captured.out.strip().split("\n")
        assert len(lines) == 2
        assert "name\tAlice" in lines[0]
        assert "age\t30" in lines[1]

    def test_list_of_dicts_as_rows(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.PLAIN, no_color=True)
        data = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
        mgr.format_response(data)
        captured = capfd.readouterr()
        lines = captured.out.strip().split("\n")
        assert len(lines) == 2
        assert "1\tAlice" in lines[0]
        assert "2\tBob" in lines[1]

    def test_list_of_primitives(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.PLAIN, no_color=True)
        data = ["alpha", "beta", "gamma"]
        mgr.format_response(data)
        captured = capfd.readouterr()
        lines = captured.out.strip().split("\n")
        assert lines == ["alpha", "beta", "gamma"]

    def test_string_as_plain(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.PLAIN, no_color=True)
        mgr.format_response("plain text output")
        captured = capfd.readouterr()
        assert "plain text output" in captured.out

    def test_number_as_plain(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.PLAIN, no_color=True)
        mgr.format_response(42)
        captured = capfd.readouterr()
        assert "42" in captured.out


# ------------------------------------------------------------------ #
# Rich format output
# ------------------------------------------------------------------ #


class TestRichFormat:
    """Test Rich format outputs syntax-highlighted content."""

    def test_dict_produces_output(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.RICH, no_color=True)
        data = {"key": "value"}
        mgr.format_response(data)
        captured = capfd.readouterr()
        # Even without color, the JSON content should be present
        assert "key" in captured.out
        assert "value" in captured.out

    def test_json_string_produces_output(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.RICH, no_color=True)
        mgr.format_response('{"a": 1}')
        captured = capfd.readouterr()
        assert '"a"' in captured.out

    def test_plain_string_in_rich(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.RICH, no_color=True)
        mgr.format_response("just text")
        captured = capfd.readouterr()
        assert "just text" in captured.out

    def test_number_in_rich(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.RICH, no_color=True)
        mgr.format_response(99)
        captured = capfd.readouterr()
        assert "99" in captured.out


# ------------------------------------------------------------------ #
# format_response with various types
# ------------------------------------------------------------------ #


class TestFormatResponseTypes:
    """Test format_response handles dict, list, str, and other types."""

    def test_empty_dict(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.JSON, no_color=True)
        mgr.format_response({})
        captured = capfd.readouterr()
        assert json.loads(captured.out) == {}

    def test_empty_list(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.JSON, no_color=True)
        mgr.format_response([])
        captured = capfd.readouterr()
        assert json.loads(captured.out) == []

    def test_boolean(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.JSON, no_color=True)
        mgr.format_response(True)
        captured = capfd.readouterr()
        assert json.loads(captured.out) is True

    def test_none_value(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.JSON, no_color=True)
        mgr.format_response(None)
        captured = capfd.readouterr()
        assert json.loads(captured.out) is None

    def test_unicode_data(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.JSON, no_color=True)
        data = {"greeting": "Bonjour, le monde!"}
        mgr.format_response(data)
        captured = capfd.readouterr()
        parsed = json.loads(captured.out)
        assert parsed["greeting"] == "Bonjour, le monde!"


# ------------------------------------------------------------------ #
# print_table
# ------------------------------------------------------------------ #


class TestPrintTable:
    """Test print_table in all three output modes."""

    def test_table_json_mode(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.JSON, no_color=True)
        headers = ["id", "name", "role"]
        rows = [["1", "Alice", "admin"], ["2", "Bob", "user"]]
        mgr.print_table(headers, rows)
        captured = capfd.readouterr()
        parsed = json.loads(captured.out)
        assert len(parsed) == 2
        assert parsed[0] == {"id": "1", "name": "Alice", "role": "admin"}
        assert parsed[1] == {"id": "2", "name": "Bob", "role": "user"}

    def test_table_plain_mode(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.PLAIN, no_color=True)
        headers = ["id", "name"]
        rows = [["1", "Alice"], ["2", "Bob"]]
        mgr.print_table(headers, rows)
        captured = capfd.readouterr()
        lines = captured.out.strip().split("\n")
        assert len(lines) == 3  # header + 2 rows
        assert "id\tname" in lines[0]
        assert "1\tAlice" in lines[1]
        assert "2\tBob" in lines[2]

    def test_table_rich_mode(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.RICH, no_color=True)
        headers = ["id", "name"]
        rows = [["1", "Alice"]]
        mgr.print_table(headers, rows, title="Users")
        captured = capfd.readouterr()
        # Rich table should contain headers and data
        assert "id" in captured.out
        assert "name" in captured.out
        assert "Alice" in captured.out
        assert "Users" in captured.out

    def test_table_empty_rows(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.JSON, no_color=True)
        mgr.print_table(["col1", "col2"], [])
        captured = capfd.readouterr()
        parsed = json.loads(captured.out)
        assert parsed == []

    def test_table_no_diagnostics_on_stdout(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.JSON, no_color=True)
        mgr.print_table(["a"], [["1"], ["2"]])
        captured = capfd.readouterr()
        assert captured.err == ""
        # stdout should be valid JSON
        json.loads(captured.out)

    def test_table_plain_with_title_ignored(self, capfd, non_tty):
        """In plain mode, title is not printed -- only headers and rows."""
        mgr = OutputManager(format=OutputFormat.PLAIN, no_color=True)
        mgr.print_table(["x"], [["1"]], title="My Title")
        captured = capfd.readouterr()
        lines = captured.out.strip().split("\n")
        # header + 1 row
        assert len(lines) == 2
        assert "x" in lines[0]
        assert "1" in lines[1]


# ------------------------------------------------------------------ #
# Output file redirection
# ------------------------------------------------------------------ #


class TestOutputFile:
    """Test -o / --output file redirection."""

    def test_format_response_writes_to_file(self, tmp_path, capfd, non_tty):
        outfile = str(tmp_path / "out.json")
        mgr = OutputManager(format=OutputFormat.JSON, no_color=True, output_file=outfile)
        data = {"key": "value"}
        mgr.format_response(data)
        captured = capfd.readouterr()
        # Nothing on stdout
        assert captured.out == ""
        # Data in file
        with open(outfile) as f:
            parsed = json.loads(f.read())
        assert parsed == data

    def test_print_data_appends_to_file(self, tmp_path, capfd, non_tty):
        outfile = str(tmp_path / "out.txt")
        mgr = OutputManager(format=OutputFormat.PLAIN, no_color=True, output_file=outfile)
        mgr.print_data("line one")
        mgr.print_data("line two")
        captured = capfd.readouterr()
        assert captured.out == ""
        with open(outfile) as f:
            content = f.read()
        assert "line one" in content
        assert "line two" in content


# ------------------------------------------------------------------ #
# Pager support
# ------------------------------------------------------------------ #


class TestPager:
    """Test paged_output behaviour."""

    def test_pager_disabled_falls_through_to_stdout(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.PLAIN, no_color=True, use_pager=False)
        mgr.paged_output("some long text")
        captured = capfd.readouterr()
        assert "some long text" in captured.out

    def test_pager_skipped_when_not_tty(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.PLAIN, no_color=True, use_pager=True)
        mgr.paged_output("data for pager")
        captured = capfd.readouterr()
        assert "data for pager" in captured.out

    def test_pager_invoked_when_tty(self, tty, monkeypatch):
        """When TTY, pager subprocess should be invoked."""
        invoked_with: list[str] = []

        class FakeProc:
            def __init__(self, cmd, **kwargs):
                invoked_with.append(cmd)

            def communicate(self, input=None):
                pass

        monkeypatch.setattr("subprocess.Popen", FakeProc)
        mgr = OutputManager(format=OutputFormat.RICH, no_color=True, use_pager=True)
        mgr.paged_output("pager content")
        assert len(invoked_with) == 1
        assert "less" in invoked_with[0]

    def test_pager_respects_pager_env(self, tty, monkeypatch):
        """$PAGER env var should be used instead of default less."""
        invoked_with: list[str] = []

        class FakeProc:
            def __init__(self, cmd, **kwargs):
                invoked_with.append(cmd)

            def communicate(self, input=None):
                pass

        monkeypatch.setattr("subprocess.Popen", FakeProc)
        monkeypatch.setenv("PAGER", "more")
        mgr = OutputManager(format=OutputFormat.RICH, no_color=True, use_pager=True)
        mgr.paged_output("content")
        assert invoked_with[0] == "more"

    def test_pager_fallback_on_oserror(self, capfd, tty, monkeypatch):
        """If pager fails to spawn, fall back to direct print."""

        def broken_popen(*args, **kwargs):
            raise OSError("no such pager")

        monkeypatch.setattr("subprocess.Popen", broken_popen)
        mgr = OutputManager(format=OutputFormat.RICH, no_color=True, use_pager=True)
        mgr.paged_output("fallback text")
        captured = capfd.readouterr()
        assert "fallback text" in captured.out


# ------------------------------------------------------------------ #
# Suggest formatting
# ------------------------------------------------------------------ #


class TestSuggest:
    """Test the suggest method formatting."""

    def test_suggest_has_arrow(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.PLAIN, no_color=True)
        mgr.suggest("Next: specli auth test myapi")
        captured = capfd.readouterr()
        assert "\u2192" in captured.err
        assert "Next: specli auth test myapi" in captured.err


# ------------------------------------------------------------------ #
# Warning / Error formatting in no-color
# ------------------------------------------------------------------ #


class TestDiagnosticFormatting:
    """Test the prefix formatting for warning and error in no-color mode."""

    def test_warning_prefix(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.PLAIN, no_color=True)
        mgr.warning("disk almost full")
        captured = capfd.readouterr()
        assert captured.err.startswith("Warning:")

    def test_error_prefix(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.PLAIN, no_color=True)
        mgr.error("connection refused")
        captured = capfd.readouterr()
        assert captured.err.startswith("Error:")


# ------------------------------------------------------------------ #
# Properties
# ------------------------------------------------------------------ #


class TestProperties:
    """Test OutputManager properties."""

    def test_is_quiet_property(self, non_tty):
        mgr = OutputManager(quiet=True)
        assert mgr.is_quiet is True
        mgr2 = OutputManager()
        assert mgr2.is_quiet is False

    def test_format_property(self, non_tty):
        mgr = OutputManager(format=OutputFormat.JSON)
        assert mgr.format == OutputFormat.JSON


# ------------------------------------------------------------------ #
# Global instance management
# ------------------------------------------------------------------ #


class TestGlobalInstance:
    """Test get_output / set_output / reset_output."""

    def test_get_output_creates_default(self):
        instance = get_output()
        assert isinstance(instance, OutputManager)

    def test_set_output_overrides(self):
        custom = OutputManager(format=OutputFormat.JSON, no_color=True)
        set_output(custom)
        assert get_output() is custom

    def test_reset_output_clears(self):
        set_output(OutputManager(format=OutputFormat.JSON, no_color=True))
        reset_output()
        # Next call should create a new default
        instance = get_output()
        assert instance is not None

    def test_set_then_reset_then_get(self):
        first = OutputManager(format=OutputFormat.JSON, no_color=True)
        set_output(first)
        reset_output()
        second = get_output()
        assert second is not first


# ------------------------------------------------------------------ #
# Convenience functions
# ------------------------------------------------------------------ #


class TestConvenienceFunctions:
    """Test module-level convenience functions delegate to global instance."""

    def test_format_response_convenience(self, capfd, non_tty):
        set_output(OutputManager(format=OutputFormat.JSON, no_color=True))
        output_module.format_response({"a": 1})
        captured = capfd.readouterr()
        parsed = json.loads(captured.out)
        assert parsed == {"a": 1}

    def test_info_convenience(self, capfd, non_tty):
        set_output(OutputManager(format=OutputFormat.PLAIN, no_color=True))
        output_module.info("hello")
        captured = capfd.readouterr()
        assert "hello" in captured.err

    def test_error_convenience(self, capfd, non_tty):
        set_output(OutputManager(format=OutputFormat.PLAIN, no_color=True))
        output_module.error("bad")
        captured = capfd.readouterr()
        assert "bad" in captured.err

    def test_success_convenience(self, capfd, non_tty):
        set_output(OutputManager(format=OutputFormat.PLAIN, no_color=True))
        output_module.success("ok")
        captured = capfd.readouterr()
        assert "ok" in captured.err

    def test_warning_convenience(self, capfd, non_tty):
        set_output(OutputManager(format=OutputFormat.PLAIN, no_color=True))
        output_module.warning("watch out")
        captured = capfd.readouterr()
        assert "watch out" in captured.err

    def test_suggest_convenience(self, capfd, non_tty):
        set_output(OutputManager(format=OutputFormat.PLAIN, no_color=True))
        output_module.suggest("try this")
        captured = capfd.readouterr()
        assert "try this" in captured.err

    def test_debug_convenience(self, capfd, non_tty):
        set_output(OutputManager(format=OutputFormat.PLAIN, no_color=True, verbose=True))
        output_module.debug("trace")
        captured = capfd.readouterr()
        assert "trace" in captured.err

    def test_print_data_convenience(self, capfd, non_tty):
        set_output(OutputManager(format=OutputFormat.PLAIN, no_color=True))
        output_module.print_data("raw stuff")
        captured = capfd.readouterr()
        assert "raw stuff" in captured.out

    def test_print_table_convenience(self, capfd, non_tty):
        set_output(OutputManager(format=OutputFormat.JSON, no_color=True))
        output_module.print_table(["x"], [["1"]])
        captured = capfd.readouterr()
        parsed = json.loads(captured.out)
        assert parsed == [{"x": "1"}]

    def test_progress_convenience(self, capfd, tty):
        set_output(OutputManager(format=OutputFormat.PLAIN, no_color=True))
        output_module.progress("working...")
        captured = capfd.readouterr()
        assert "working..." in captured.err

    def test_paged_output_convenience(self, capfd, non_tty):
        set_output(OutputManager(format=OutputFormat.PLAIN, no_color=True, use_pager=False))
        output_module.paged_output("page me")
        captured = capfd.readouterr()
        assert "page me" in captured.out


# ------------------------------------------------------------------ #
# _is_tty helper
# ------------------------------------------------------------------ #


class TestIsTty:
    """Test the _is_tty helper function directly."""

    def test_is_tty_returns_bool(self):
        result = _is_tty()
        assert isinstance(result, bool)

    def test_is_tty_false_in_test_env(self):
        # pytest captures stdout, so isatty() should be False
        assert _is_tty() is False


# ------------------------------------------------------------------ #
# OutputFormat enum
# ------------------------------------------------------------------ #


class TestOutputFormatEnum:
    """Test the OutputFormat enum values."""

    def test_enum_values(self):
        assert OutputFormat.AUTO == "auto"
        assert OutputFormat.JSON == "json"
        assert OutputFormat.PLAIN == "plain"
        assert OutputFormat.RICH == "rich"

    def test_enum_is_str(self):
        assert isinstance(OutputFormat.JSON, str)

    def test_enum_from_string(self):
        assert OutputFormat("json") == OutputFormat.JSON
        assert OutputFormat("auto") == OutputFormat.AUTO


# ------------------------------------------------------------------ #
# Edge cases
# ------------------------------------------------------------------ #


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_string_format_response(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.PLAIN, no_color=True)
        mgr.format_response("")
        captured = capfd.readouterr()
        # Empty string still gets printed (as empty line)
        assert captured.out == "\n"

    def test_deeply_nested_json(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.JSON, no_color=True)
        data = {"a": {"b": {"c": {"d": {"e": "deep"}}}}}
        mgr.format_response(data)
        captured = capfd.readouterr()
        parsed = json.loads(captured.out)
        assert parsed["a"]["b"]["c"]["d"]["e"] == "deep"

    def test_large_list(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.JSON, no_color=True)
        data = list(range(1000))
        mgr.format_response(data)
        captured = capfd.readouterr()
        parsed = json.loads(captured.out)
        assert len(parsed) == 1000

    def test_special_chars_in_json(self, capfd, non_tty):
        mgr = OutputManager(format=OutputFormat.JSON, no_color=True)
        data = {"msg": 'line1\nline2\ttab "quotes"'}
        mgr.format_response(data)
        captured = capfd.readouterr()
        parsed = json.loads(captured.out)
        assert parsed["msg"] == 'line1\nline2\ttab "quotes"'

    def test_concurrent_stdout_stderr(self, capfd, non_tty):
        """Interleaved data and diagnostic calls keep separation."""
        mgr = OutputManager(format=OutputFormat.JSON, no_color=True)
        mgr.info("step 1")
        mgr.print_data('{"a":1}')
        mgr.warning("step 2")
        mgr.print_data('{"b":2}')
        mgr.error("step 3")
        captured = capfd.readouterr()
        # stdout has only data
        assert "step 1" not in captured.out
        assert "step 2" not in captured.out
        assert "step 3" not in captured.out
        assert '{"a":1}' in captured.out
        assert '{"b":2}' in captured.out
        # stderr has only diagnostics
        assert "step 1" in captured.err
        assert "step 2" in captured.err
        assert "step 3" in captured.err

    def test_output_file_with_string_data(self, tmp_path, non_tty):
        outfile = str(tmp_path / "out.txt")
        mgr = OutputManager(
            format=OutputFormat.PLAIN, no_color=True, output_file=outfile
        )
        mgr.format_response("hello file")
        with open(outfile) as f:
            content = f.read()
        assert "hello file" in content

    def test_output_file_ends_with_newline(self, tmp_path, non_tty):
        outfile = str(tmp_path / "out.txt")
        mgr = OutputManager(
            format=OutputFormat.JSON, no_color=True, output_file=outfile
        )
        mgr.format_response({"x": 1})
        with open(outfile) as f:
            content = f.read()
        assert content.endswith("\n")
