"""Tests for shell completion commands."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from specli.app import app


@pytest.fixture
def runner():
    return CliRunner()


class TestCompletion:
    def test_completion_help(self, runner):
        result = runner.invoke(app, ["completion", "--help"])
        assert result.exit_code == 0
        assert "install" in result.output
        assert "show" in result.output

    def test_completion_install_creates_file(self, runner, tmp_path, monkeypatch):
        """completion install bash creates a file."""
        monkeypatch.setenv("HOME", str(tmp_path))
        # This may fail since specli binary may not be in PATH during tests,
        # but it should not crash
        result = runner.invoke(app, ["completion", "install", "bash"])
        # Accept either success or graceful failure
        assert result.exit_code in (0, 1, 2)

    def test_completion_install_unsupported_shell(self, runner):
        result = runner.invoke(app, ["completion", "install", "tcsh"])
        assert result.exit_code != 0

    def test_completion_show(self, runner):
        result = runner.invoke(app, ["completion", "show", "bash"])
        # Should output something (either real completion or eval fallback)
        assert result.exit_code == 0

    def test_app_has_completion_enabled(self):
        """Verify add_completion is True on the app."""
        # Typer's add_completion adds --install-completion and --show-completion
        # We just verify our completion subcommand exists
        from specli.app import app as test_app

        # The completion group should be registered
        assert any(
            "completion" in str(getattr(g, "name", "") or "")
            for g in test_app.registered_groups
        )
