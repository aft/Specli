"""Tests for the build plugin (compile + generate commands)."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from specli.app import app

runner = CliRunner()

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def _profile_on_disk(isolated_config: Path) -> str:
    """Write a minimal profile to the isolated config dir and return its name."""
    profiles_dir = isolated_config / "config" / "specli" / "profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)

    spec_path = isolated_config / "petstore.json"
    with open(FIXTURES_DIR / "petstore_3.0.json") as f:
        spec_path.write_text(f.read())

    profile = {
        "name": "test-api",
        "spec": str(spec_path),
        "base_url": "http://localhost:8080",
    }
    (profiles_dir / "test-api.json").write_text(json.dumps(profile))
    return "test-api"


# ---------------------------------------------------------------------------
# build --help
# ---------------------------------------------------------------------------


class TestBuildHelp:
    """Verify the build sub-command is registered and shows help."""

    def test_build_help(self) -> None:
        result = runner.invoke(app, ["build", "--help"])
        assert result.exit_code == 0
        assert "compile" in result.output
        assert "generate" in result.output

    def test_build_compile_help(self) -> None:
        result = runner.invoke(app, ["build", "compile", "--help"])
        assert result.exit_code == 0
        text = _strip_ansi(result.output)
        assert "--profile" in text
        assert "--name" in text

    def test_build_generate_help(self) -> None:
        result = runner.invoke(app, ["build", "generate", "--help"])
        assert result.exit_code == 0
        text = _strip_ansi(result.output)
        assert "--profile" in text
        assert "--name" in text


# ---------------------------------------------------------------------------
# build compile
# ---------------------------------------------------------------------------


class TestBuildCompile:
    """Tests for the ``build compile`` command."""

    def test_compile_missing_pyinstaller(
        self, isolated_config: Path, _profile_on_disk: str
    ) -> None:
        """Should exit 1 with a helpful message when PyInstaller is absent."""
        with patch(
            "specli.plugins.build.plugin._check_pyinstaller", return_value=False
        ):
            result = runner.invoke(
                app,
                ["build", "compile", "--profile", _profile_on_disk, "--name", "test-cli"],
            )
        assert result.exit_code == 1
        assert "PyInstaller" in result.output

    def test_compile_bad_profile(self, isolated_config: Path) -> None:
        """Should exit 1 when profile doesn't exist."""
        with patch(
            "specli.plugins.build.plugin._check_pyinstaller", return_value=True
        ):
            result = runner.invoke(
                app,
                ["build", "compile", "--profile", "nonexistent", "--name", "test-cli"],
            )
        assert result.exit_code == 1
        assert "not found" in result.output.lower() or "Profile" in result.output

    def test_compile_pyinstaller_failure(
        self, isolated_config: Path, _profile_on_disk: str, tmp_path: Path
    ) -> None:
        """Should exit 1 when PyInstaller returns non-zero."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "Error: something went wrong\n"
        mock_result.stdout = ""

        with (
            patch(
                "specli.plugins.build.plugin._check_pyinstaller", return_value=True
            ),
            patch("subprocess.run", return_value=mock_result),
        ):
            result = runner.invoke(
                app,
                [
                    "build", "compile",
                    "--profile", _profile_on_disk,
                    "--name", "test-cli",
                    "--output", str(tmp_path / "dist"),
                ],
            )
        assert result.exit_code == 1
        assert "failed" in result.output.lower()

    def test_compile_pyinstaller_timeout(
        self, isolated_config: Path, _profile_on_disk: str, tmp_path: Path
    ) -> None:
        """Should exit 1 when PyInstaller times out."""
        with (
            patch(
                "specli.plugins.build.plugin._check_pyinstaller", return_value=True
            ),
            patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 300)),
        ):
            result = runner.invoke(
                app,
                [
                    "build", "compile",
                    "--profile", _profile_on_disk,
                    "--name", "test-cli",
                    "--output", str(tmp_path / "dist"),
                ],
            )
        assert result.exit_code == 1
        assert "timed out" in result.output.lower()

    def test_compile_success(
        self, isolated_config: Path, _profile_on_disk: str, tmp_path: Path
    ) -> None:
        """Should succeed when PyInstaller returns 0 and binary exists."""
        dist_dir = tmp_path / "dist"
        dist_dir.mkdir(parents=True, exist_ok=True)
        # Create fake binary
        binary = dist_dir / "test-cli"
        binary.write_bytes(b"\x00" * 1024)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""
        mock_result.stdout = ""

        with (
            patch(
                "specli.plugins.build.plugin._check_pyinstaller", return_value=True
            ),
            patch("subprocess.run", return_value=mock_result),
            patch("shutil.rmtree"),  # Don't actually clean
        ):
            result = runner.invoke(
                app,
                [
                    "build", "compile",
                    "--profile", _profile_on_disk,
                    "--name", "test-cli",
                    "--output", str(dist_dir),
                ],
            )
        assert result.exit_code == 0
        assert "Built" in result.output or "test-cli" in result.output

    def test_compile_onedir(
        self, isolated_config: Path, _profile_on_disk: str, tmp_path: Path
    ) -> None:
        """Should pass --onedir to PyInstaller and expect directory bundle."""
        dist_dir = tmp_path / "dist"
        bundle_dir = dist_dir / "test-cli"
        bundle_dir.mkdir(parents=True, exist_ok=True)
        binary = bundle_dir / "test-cli"
        binary.write_bytes(b"\x00" * 1024)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""
        mock_result.stdout = ""

        called_args: list[Any] = []

        def capture_run(*args: Any, **kwargs: Any) -> MagicMock:
            called_args.append(args[0])
            return mock_result

        with (
            patch(
                "specli.plugins.build.plugin._check_pyinstaller", return_value=True
            ),
            patch("subprocess.run", side_effect=capture_run),
            patch("shutil.rmtree"),
        ):
            result = runner.invoke(
                app,
                [
                    "build", "compile",
                    "--profile", _profile_on_disk,
                    "--name", "test-cli",
                    "--output", str(dist_dir),
                    "--onedir",
                ],
            )
        assert result.exit_code == 0
        # Verify --onedir was passed to PyInstaller
        assert any("--onedir" in arg for arg in called_args[0])


# ---------------------------------------------------------------------------
# build generate
# ---------------------------------------------------------------------------


class TestBuildGenerate:
    """Tests for the ``build generate`` command."""

    def test_generate_bad_profile(self, isolated_config: Path) -> None:
        """Should exit 1 when profile doesn't exist."""
        result = runner.invoke(
            app,
            ["build", "generate", "--profile", "nonexistent", "--name", "test-cli"],
        )
        assert result.exit_code == 1

    def test_generate_creates_package(
        self, isolated_config: Path, _profile_on_disk: str, tmp_path: Path
    ) -> None:
        """Should create a pip-installable package directory."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        result = runner.invoke(
            app,
            [
                "build", "generate",
                "--profile", _profile_on_disk,
                "--name", "test-cli",
                "--output", str(output_dir),
            ],
        )
        assert result.exit_code == 0

        pkg_dir = output_dir / "test-cli"
        assert pkg_dir.exists()
        assert (pkg_dir / "pyproject.toml").exists()
        assert (pkg_dir / "src" / "test_cli" / "__init__.py").exists()
        assert (pkg_dir / "src" / "test_cli" / "__main__.py").exists()
        assert (pkg_dir / "src" / "test_cli" / "cli.py").exists()

    def test_generate_pyproject_content(
        self, isolated_config: Path, _profile_on_disk: str, tmp_path: Path
    ) -> None:
        """Generated pyproject.toml should have correct name and entry point."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        result = runner.invoke(
            app,
            [
                "build", "generate",
                "--profile", _profile_on_disk,
                "--name", "my-api",
                "--cli-version", "2.0.0",
                "--output", str(output_dir),
            ],
        )
        assert result.exit_code == 0

        pyproject = (output_dir / "my-api" / "pyproject.toml").read_text()
        assert 'name = "my-api"' in pyproject
        assert 'version = "2.0.0"' in pyproject
        assert 'my-api = "my_api.cli:main"' in pyproject
        assert "specli" in pyproject  # should depend on specli

    def test_generate_cli_module_has_frozen_data(
        self, isolated_config: Path, _profile_on_disk: str, tmp_path: Path
    ) -> None:
        """Generated cli.py should contain the frozen spec and profile."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        result = runner.invoke(
            app,
            [
                "build", "generate",
                "--profile", _profile_on_disk,
                "--name", "test-cli",
                "--output", str(output_dir),
            ],
        )
        assert result.exit_code == 0

        cli_source = (output_dir / "test-cli" / "src" / "test_cli" / "cli.py").read_text()
        assert "_FROZEN_SPEC" in cli_source
        assert "_FROZEN_PROFILE" in cli_source
        assert "test-api" in cli_source  # profile name embedded
        assert "Petstore" in cli_source or "petstore" in cli_source.lower()

    def test_generate_custom_version(
        self, isolated_config: Path, _profile_on_disk: str, tmp_path: Path
    ) -> None:
        """Generated package should embed the specified CLI version."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        result = runner.invoke(
            app,
            [
                "build", "generate",
                "--profile", _profile_on_disk,
                "--name", "test-cli",
                "--cli-version", "3.5.0",
                "--output", str(output_dir),
            ],
        )
        assert result.exit_code == 0

        cli_source = (output_dir / "test-cli" / "src" / "test_cli" / "cli.py").read_text()
        assert "3.5.0" in cli_source


# ---------------------------------------------------------------------------
# _check_pyinstaller helper
# ---------------------------------------------------------------------------


class TestCheckPyInstaller:
    """Tests for the _check_pyinstaller helper."""

    def test_returns_true_when_importable(self) -> None:
        from specli.plugins.build.plugin import _check_pyinstaller

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            assert _check_pyinstaller() is True

    def test_returns_false_when_not_importable(self) -> None:
        from specli.plugins.build.plugin import _check_pyinstaller

        mock_result = MagicMock()
        mock_result.returncode = 1

        with patch("subprocess.run", return_value=mock_result):
            assert _check_pyinstaller() is False

    def test_returns_false_on_exception(self) -> None:
        from specli.plugins.build.plugin import _check_pyinstaller

        with patch("subprocess.run", side_effect=OSError("fail")):
            assert _check_pyinstaller() is False


# ---------------------------------------------------------------------------
# Entry template validity
# ---------------------------------------------------------------------------


class TestEntryTemplate:
    """Verify the entry template is valid Python after formatting."""

    def test_template_is_valid_python(
        self, isolated_config: Path, _profile_on_disk: str
    ) -> None:
        """The formatted entry template should be valid Python syntax."""
        from specli.config import load_profile
        from specli.parser import load_spec
        from specli.plugins.build.plugin import _ENTRY_TEMPLATE

        profile = load_profile(_profile_on_disk)
        raw_spec = load_spec(profile.spec)
        profile_data = profile.model_dump(mode="json")

        source = _ENTRY_TEMPLATE.format(
            cli_name="test-cli",
            profile_name=_profile_on_disk,
            spec_source=profile.spec,
            frozen_spec_repr=repr(json.dumps(raw_spec)),
            frozen_profile_repr=repr(json.dumps(profile_data)),
            cli_name_repr=repr("test-cli"),
            cli_version_repr=repr("1.0.0"),
            cli_help_repr=repr("Test CLI"),
        )

        # Should compile without SyntaxError
        compile(source, "<test-entry>", "exec")
