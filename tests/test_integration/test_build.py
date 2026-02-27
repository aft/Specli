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


# ---------------------------------------------------------------------------
# Profile build defaults
# ---------------------------------------------------------------------------


@pytest.fixture
def _profile_with_build(isolated_config: Path, tmp_path: Path) -> str:
    """Write a profile with a ``build`` section and return its name."""
    profiles_dir = isolated_config / "config" / "specli" / "profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)

    spec_path = isolated_config / "petstore.json"
    with open(FIXTURES_DIR / "petstore_3.0.json") as f:
        spec_path.write_text(f.read())

    profile = {
        "name": "build-test",
        "spec": str(spec_path),
        "base_url": "http://localhost:8080",
        "build": {
            "name": "my-cli",
            "output_dir": str(tmp_path / "build-output"),
            "cli_version": "2.5.0",
            "source_dir": None,
            "import_strings": None,
            "generate_skill": None,
        },
    }
    (profiles_dir / "build-test.json").write_text(json.dumps(profile))
    return "build-test"


class TestBuildDefaults:
    """Tests for reading build defaults from profile ``build`` section."""

    def test_generate_uses_profile_name(
        self, isolated_config: Path, _profile_with_build: str, tmp_path: Path
    ) -> None:
        """Should use build.name from profile when --name is not provided."""
        result = runner.invoke(
            app,
            [
                "build", "generate",
                "--profile", _profile_with_build,
                # No --name flag
            ],
        )
        assert result.exit_code == 0
        # Package dir should be named after the profile build name
        build_output = tmp_path / "build-output"
        assert (build_output / "my-cli").exists()

    def test_generate_cli_flag_overrides_profile(
        self, isolated_config: Path, _profile_with_build: str, tmp_path: Path
    ) -> None:
        """CLI --name should override build.name from profile."""
        output_dir = tmp_path / "override-output"
        output_dir.mkdir()

        result = runner.invoke(
            app,
            [
                "build", "generate",
                "--profile", _profile_with_build,
                "--name", "override-cli",
                "--output", str(output_dir),
            ],
        )
        assert result.exit_code == 0
        assert (output_dir / "override-cli").exists()
        # Should NOT have created "my-cli"
        assert not (output_dir / "my-cli").exists()

    def test_generate_version_from_profile(
        self, isolated_config: Path, _profile_with_build: str, tmp_path: Path
    ) -> None:
        """Should use build.cli_version from profile."""
        result = runner.invoke(
            app,
            [
                "build", "generate",
                "--profile", _profile_with_build,
            ],
        )
        assert result.exit_code == 0

        build_output = tmp_path / "build-output"
        cli_source = (build_output / "my-cli" / "src" / "my_cli" / "cli.py").read_text()
        assert "2.5.0" in cli_source

    def test_generate_missing_name_everywhere_errors(
        self, isolated_config: Path
    ) -> None:
        """Should exit 1 when name is in neither CLI nor profile build config."""
        # Create a profile without a build section
        profiles_dir = isolated_config / "config" / "specli" / "profiles"
        profiles_dir.mkdir(parents=True, exist_ok=True)

        spec_path = isolated_config / "petstore.json"
        with open(FIXTURES_DIR / "petstore_3.0.json") as f:
            spec_path.write_text(f.read())

        profile = {
            "name": "no-build",
            "spec": str(spec_path),
            "base_url": "http://localhost:8080",
        }
        (profiles_dir / "no-build.json").write_text(json.dumps(profile))

        result = runner.invoke(
            app,
            ["build", "generate", "--profile", "no-build"],
        )
        assert result.exit_code == 1
        assert "name" in result.output.lower()

    def test_generate_no_build_section_with_cli_flags(
        self, isolated_config: Path, _profile_on_disk: str, tmp_path: Path
    ) -> None:
        """Profile without build section should work when CLI flags provided."""
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
        assert (output_dir / "test-cli").exists()

    def test_compile_uses_profile_name(
        self, isolated_config: Path, _profile_with_build: str, tmp_path: Path
    ) -> None:
        """Compile should use build.name from profile when --name not provided."""
        dist_dir = tmp_path / "build-output"
        dist_dir.mkdir(parents=True, exist_ok=True)
        binary = dist_dir / "my-cli"
        binary.write_bytes(b"\x00" * 1024)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""
        mock_result.stdout = ""

        with (
            patch("specli.plugins.build.plugin._check_pyinstaller", return_value=True),
            patch("subprocess.run", return_value=mock_result),
            patch("shutil.rmtree"),
        ):
            result = runner.invoke(
                app,
                [
                    "build", "compile",
                    "--profile", _profile_with_build,
                    # No --name flag; uses profile build.name "my-cli"
                ],
            )
        assert result.exit_code == 0
        assert "my-cli" in result.output

    def test_resolve_build_params_helper(self) -> None:
        """Direct test of _resolve_build_params merge logic."""
        from specli.plugins.build.plugin import _resolve_build_params

        build_cfg = {
            "name": "profile-name",
            "output_dir": "/profile/out",
            "cli_version": "9.0.0",
            "source_dir": "/profile/src",
            "import_strings": "/profile/strings.json",
        }

        # All None — profile wins
        result = _resolve_build_params(
            build_cfg,
            name=None, output_dir=None, cli_version=None,
            source_dir=None, import_strings=None, export_strings=None,
            generate_skill=None, default_output_dir="./default",
        )
        assert result["name"] == "profile-name"
        assert result["output_dir"] == "/profile/out"
        assert result["cli_version"] == "9.0.0"
        assert result["source_dir"] == "/profile/src"
        assert result["import_strings"] == "/profile/strings.json"

        # CLI values override profile
        result = _resolve_build_params(
            build_cfg,
            name="cli-name", output_dir="/cli/out", cli_version="1.0.0",
            source_dir="/cli/src", import_strings="/cli/strings.json",
            export_strings=None, generate_skill=None,
            default_output_dir="./default",
        )
        assert result["name"] == "cli-name"
        assert result["output_dir"] == "/cli/out"
        assert result["cli_version"] == "1.0.0"
        assert result["source_dir"] == "/cli/src"
        assert result["import_strings"] == "/cli/strings.json"

        # Empty build_cfg — hardcoded defaults used
        result = _resolve_build_params(
            {},
            name=None, output_dir=None, cli_version=None,
            source_dir=None, import_strings=None, export_strings=None,
            generate_skill=None, default_output_dir="./default",
        )
        assert result["name"] is None
        assert result["output_dir"] == "./default"
        assert result["cli_version"] == "1.0.0"


# ---------------------------------------------------------------------------
# Body field options (per-field flags from request body schema)
# ---------------------------------------------------------------------------


def _make_minimal_spec(tmp_dir: Path) -> Path:
    """Create a minimal OpenAPI spec with a POST endpoint that has NO requestBody."""
    spec = {
        "openapi": "3.0.3",
        "info": {"title": "Minimal API", "version": "1.0.0"},
        "paths": {
            "/items": {
                "post": {
                    "operationId": "createItem",
                    "summary": "Create an item",
                    "responses": {"201": {"description": "Created"}},
                },
                "get": {
                    "operationId": "listItems",
                    "summary": "List items",
                    "responses": {"200": {"description": "OK"}},
                },
            }
        },
    }
    spec_path = tmp_dir / "minimal.json"
    spec_path.write_text(json.dumps(spec))
    return spec_path


@pytest.fixture
def _profile_with_strings(isolated_config: Path, tmp_path: Path) -> tuple[str, Path]:
    """Profile with a strings file containing body_schema. Returns (name, strings_path)."""
    profiles_dir = isolated_config / "config" / "specli" / "profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)

    spec_path = _make_minimal_spec(isolated_config)

    strings_path = tmp_path / "strings.json"
    strings_data = {
        "operations": {
            "POST /items": {
                "summary": "Create an item",
                "body_schema": {
                    "properties": {
                        "title": {"type": "string", "description": "Item title"},
                        "tag": {"type": "string", "description": "Item tag"},
                        "count": {"type": "integer", "description": "Item count"},
                    },
                    "required_fields": ["title"],
                },
            }
        }
    }
    strings_path.write_text(json.dumps(strings_data))

    profile = {
        "name": "body-test",
        "spec": str(spec_path),
        "base_url": "http://localhost:8080",
        "build": {
            "name": "body-cli",
            "output_dir": str(tmp_path / "build-output"),
            "import_strings": str(strings_path),
        },
    }
    (profiles_dir / "body-test.json").write_text(json.dumps(profile))
    return "body-test", strings_path


class TestBodyFieldOptions:
    """Tests for per-field body flags generated from body_schema in strings."""

    def test_generate_with_body_fields_creates_package(
        self,
        isolated_config: Path,
        _profile_with_strings: tuple[str, Path],
    ) -> None:
        """A profile with body_schema should produce a valid pip package."""
        profile_name, _ = _profile_with_strings

        result = runner.invoke(
            app,
            ["build", "generate", "--profile", profile_name],
        )
        assert result.exit_code == 0, result.output

    def test_frozen_spec_has_injected_request_body(
        self,
        isolated_config: Path,
        _profile_with_strings: tuple[str, Path],
        tmp_path: Path,
    ) -> None:
        """Frozen spec in generated cli.py should contain the injected requestBody."""
        profile_name, _ = _profile_with_strings

        result = runner.invoke(
            app,
            ["build", "generate", "--profile", profile_name],
        )
        assert result.exit_code == 0, result.output

        cli_source = (
            tmp_path / "build-output" / "body-cli" / "src" / "body_cli" / "cli.py"
        ).read_text()

        # The frozen spec is embedded as JSON. Parse it to verify the
        # injected requestBody from the strings file.
        import re as _re

        m = _re.search(r"_FROZEN_SPEC = json\.loads\('(.+?)'\)", cli_source, _re.DOTALL)
        assert m, "Could not find _FROZEN_SPEC in generated cli.py"
        frozen_spec = json.loads(m.group(1))

        post_items = frozen_spec["paths"]["/items"]["post"]
        assert "requestBody" in post_items
        schema = post_items["requestBody"]["content"]["application/json"]["schema"]
        assert "title" in schema["properties"]
        assert "tag" in schema["properties"]
        assert "count" in schema["properties"]
        assert "title" in schema.get("required", [])

    def test_generated_cli_is_valid_python(
        self,
        isolated_config: Path,
        _profile_with_strings: tuple[str, Path],
        tmp_path: Path,
    ) -> None:
        """Generated cli.py must compile without SyntaxError."""
        profile_name, _ = _profile_with_strings

        result = runner.invoke(
            app,
            ["build", "generate", "--profile", profile_name],
        )
        assert result.exit_code == 0, result.output

        cli_source = (
            tmp_path / "build-output" / "body-cli" / "src" / "body_cli" / "cli.py"
        ).read_text()
        compile(cli_source, "<test-body-fields>", "exec")

    def test_command_tree_generates_body_field_flags(self) -> None:
        """Command tree should create --field flags when requestBody has properties."""
        from unittest.mock import MagicMock

        from typer.testing import CliRunner as _Runner

        from specli.generator.command_tree import build_command_tree
        from specli.generator.path_rules import PathRulesConfig
        from specli.models import (
            APIInfo,
            APIOperation,
            HTTPMethod,
            ParsedSpec,
            RequestBodyInfo,
        )

        op = APIOperation(
            method=HTTPMethod.POST,
            path="/items",
            summary="Create an item",
            parameters=[],
            request_body=RequestBodyInfo(
                description="Item to create",
                required=True,
                content_types=["application/json"],
                schema_={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Item title"},
                        "count": {"type": "integer", "description": "Item count", "default": 1},
                    },
                    "required": ["title"],
                },
            ),
        )
        spec = ParsedSpec(
            info=APIInfo(title="Test API", version="1.0.0"),
            openapi_version="3.0.3",
            operations=[op],
        )

        captured: dict[str, Any] = {}

        def callback(method: str, path: str, params: dict, body: str | None, content_type: str | None = None) -> None:
            captured["body"] = body

        tree = build_command_tree(spec, PathRulesConfig(), callback)
        r = _Runner()

        # Invoke with individual fields.
        result = r.invoke(tree, ["items", "create", "--title", "Hello"])
        assert result.exit_code == 0, result.output
        body_data = json.loads(captured["body"])
        assert body_data["title"] == "Hello"

    def test_body_field_required_validation(self) -> None:
        """Missing required body field (without --body) should error."""
        from unittest.mock import MagicMock

        from typer.testing import CliRunner as _Runner

        from specli.generator.command_tree import build_command_tree
        from specli.generator.path_rules import PathRulesConfig
        from specli.models import (
            APIInfo,
            APIOperation,
            HTTPMethod,
            ParsedSpec,
            RequestBodyInfo,
        )

        op = APIOperation(
            method=HTTPMethod.POST,
            path="/items",
            summary="Create an item",
            parameters=[],
            request_body=RequestBodyInfo(
                description="Item to create",
                required=True,
                content_types=["application/json"],
                schema_={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Item title"},
                    },
                    "required": ["title"],
                },
            ),
        )
        spec = ParsedSpec(
            info=APIInfo(title="Test API", version="1.0.0"),
            openapi_version="3.0.3",
            operations=[op],
        )

        tree = build_command_tree(spec, PathRulesConfig(), MagicMock())
        r = _Runner()

        # Invoke with no body fields and no --body → should error.
        result = r.invoke(tree, ["items", "create"])
        assert result.exit_code != 0

    def test_body_flag_overrides_fields(self) -> None:
        """--body JSON should override individual field values."""
        from unittest.mock import MagicMock

        from typer.testing import CliRunner as _Runner

        from specli.generator.command_tree import build_command_tree
        from specli.generator.path_rules import PathRulesConfig
        from specli.models import (
            APIInfo,
            APIOperation,
            HTTPMethod,
            ParsedSpec,
            RequestBodyInfo,
        )

        op = APIOperation(
            method=HTTPMethod.POST,
            path="/items",
            summary="Create an item",
            parameters=[],
            request_body=RequestBodyInfo(
                description="Item to create",
                required=True,
                content_types=["application/json"],
                schema_={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Item title"},
                        "count": {"type": "integer", "description": "Count"},
                    },
                    "required": ["title"],
                },
            ),
        )
        spec = ParsedSpec(
            info=APIInfo(title="Test API", version="1.0.0"),
            openapi_version="3.0.3",
            operations=[op],
        )

        captured: dict[str, Any] = {}

        def callback(method: str, path: str, params: dict, body: str | None, content_type: str | None = None) -> None:
            captured["body"] = body

        tree = build_command_tree(spec, PathRulesConfig(), callback)
        r = _Runner()

        # --title via field, --body overrides with different title.
        result = r.invoke(
            tree,
            ["items", "create", "--title", "Field", "--body", '{"title":"Override"}'],
        )
        assert result.exit_code == 0, result.output
        body_data = json.loads(captured["body"])
        # --body should win over individual --title.
        assert body_data["title"] == "Override"

    def test_no_body_schema_keeps_plain_body(self) -> None:
        """Operations without request body schema still get --body."""
        from unittest.mock import MagicMock

        from typer.testing import CliRunner as _Runner

        from specli.generator.command_tree import build_command_tree
        from specli.generator.path_rules import PathRulesConfig
        from specli.models import (
            APIInfo,
            APIOperation,
            HTTPMethod,
            ParsedSpec,
        )

        op = APIOperation(
            method=HTTPMethod.POST,
            path="/items",
            summary="Create item",
            parameters=[],
        )
        spec = ParsedSpec(
            info=APIInfo(title="Test API", version="1.0.0"),
            openapi_version="3.0.3",
            operations=[op],
        )

        captured: dict[str, Any] = {}

        def callback(method: str, path: str, params: dict, body: str | None, content_type: str | None = None) -> None:
            captured["body"] = body

        tree = build_command_tree(spec, PathRulesConfig(), callback)
        r = _Runner()

        result = r.invoke(tree, ["items", "create", "--body", '{"x":1}'])
        assert result.exit_code == 0, result.output
        assert json.loads(captured["body"]) == {"x": 1}


class TestBodyFieldAssembly:
    """Tests that body field assembly and --body override logic works correctly."""

    def test_build_body_field_options_basic(self) -> None:
        """build_body_field_options returns correct descriptors."""
        from specli.generator.param_mapper import build_body_field_options

        schema = {
            "properties": {
                "model_id": {"type": "string", "description": "Model identifier"},
                "batch_count": {
                    "type": "integer",
                    "description": "How many",
                    "default": 1,
                },
            },
            "required": ["model_id"],
        }
        descriptors = build_body_field_options(schema)

        assert len(descriptors) == 2

        model_desc = next(d for d in descriptors if d["name"] == "model_id")
        assert model_desc["original_name"] == "__body__.model_id"
        # [REQUIRED] is baked into the Typer Option help, not the descriptor help.
        assert model_desc["default"].help is not None
        assert "[REQUIRED]" in model_desc["default"].help
        assert model_desc["body_field_type"] == "string"

        batch_desc = next(d for d in descriptors if d["name"] == "batch_count")
        assert batch_desc["original_name"] == "__body__.batch_count"
        assert batch_desc["body_field_type"] == "integer"

    def test_build_body_field_options_complex_types(self) -> None:
        """Object/array types produce str descriptors with correct body_field_type."""
        from specli.generator.param_mapper import build_body_field_options

        schema = {
            "properties": {
                "parameters": {
                    "type": "object",
                    "description": "Nested params as JSON",
                },
                "tags": {
                    "type": "array",
                    "description": "List of tags as JSON",
                },
            },
        }
        descriptors = build_body_field_options(schema)

        params_desc = next(d for d in descriptors if d["name"] == "parameters")
        assert params_desc["body_field_type"] == "object"
        # Complex types should map to str at the CLI level.
        assert params_desc["type"] is str or "str" in str(params_desc["type"])

        tags_desc = next(d for d in descriptors if d["name"] == "tags")
        assert tags_desc["body_field_type"] == "array"

    def test_build_body_field_options_openapi_31_nullable(self) -> None:
        """OpenAPI 3.1 type: ["string", "null"] should not crash."""
        from specli.generator.param_mapper import build_body_field_options

        schema = {
            "properties": {
                "nickname": {
                    "type": ["string", "null"],
                    "description": "Optional nickname",
                },
            },
        }
        descriptors = build_body_field_options(schema)
        assert len(descriptors) == 1
        assert descriptors[0]["body_field_type"] == "string"

    def test_build_body_field_options_enum_hint(self) -> None:
        """Fields with enum values get [choices: ...] in help text."""
        from specli.generator.param_mapper import build_body_field_options

        schema = {
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Current status",
                    "enum": ["active", "inactive", "pending"],
                },
            },
        }
        descriptors = build_body_field_options(schema)
        assert len(descriptors) == 1
        assert "choices:" in descriptors[0]["help"]
        assert "active" in descriptors[0]["help"]

    def test_body_schema_import_injects_request_body(self) -> None:
        """_import_operations should inject requestBody from body_schema."""
        from specli.enrichment.strings import _import_operations

        raw_spec = {
            "paths": {
                "/items": {
                    "post": {
                        "summary": "Create item",
                    }
                }
            }
        }
        op_strings = {
            "POST /items": {
                "body_schema": {
                    "properties": {
                        "title": {"type": "string"},
                    },
                    "required_fields": ["title"],
                }
            }
        }
        _import_operations(raw_spec, op_strings)

        req_body = raw_spec["paths"]["/items"]["post"]["requestBody"]
        assert req_body["required"] is True
        schema = req_body["content"]["application/json"]["schema"]
        assert "title" in schema["properties"]
        assert "title" in schema["required"]

    def test_body_schema_import_does_not_overwrite_existing(self) -> None:
        """_import_operations should NOT overwrite an existing requestBody."""
        from specli.enrichment.strings import _import_operations

        existing_body = {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {"type": "object", "properties": {"existing": {"type": "string"}}}
                }
            },
        }
        raw_spec = {
            "paths": {
                "/items": {
                    "post": {
                        "summary": "Create item",
                        "requestBody": existing_body,
                    }
                }
            }
        }
        op_strings = {
            "POST /items": {
                "body_schema": {
                    "properties": {
                        "new_field": {"type": "string"},
                    },
                }
            }
        }
        _import_operations(raw_spec, op_strings)

        # Should keep the original requestBody.
        schema = raw_spec["paths"]["/items"]["post"]["requestBody"]["content"][
            "application/json"
        ]["schema"]
        assert "existing" in schema["properties"]
        assert "new_field" not in schema.get("properties", {})
