"""Tests for specli.config — XDG paths, atomic writes, profiles, precedence."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from specli.config import (
    _atomic_write,
    delete_profile,
    get_cache_dir,
    get_config_dir,
    get_data_dir,
    get_profiles_dir,
    list_profiles,
    load_global_config,
    load_profile,
    load_project_config,
    profile_exists,
    resolve_config,
    resolve_credential,
    save_global_config,
    save_profile,
)
from specli.exceptions import ConfigError
from specli.models import AuthConfig, GlobalConfig, OutputConfig, Profile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_json(path: Path, data: Any) -> None:
    """Write a dict as JSON to *path*, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _make_profile(name: str = "test", spec: str = "https://api.example.com/openapi.json") -> Profile:
    return Profile(name=name, spec=spec)


# ---------------------------------------------------------------------------
# XDG path resolution
# ---------------------------------------------------------------------------


class TestXDGPathsLinux:
    """XDG paths on Linux (the default XDG platform)."""

    def test_config_dir_xdg_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("specli.config._is_xdg_platform", lambda: True)
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

        result = get_config_dir()
        assert result == tmp_path / ".config" / "specli"
        assert result.is_dir()

    def test_config_dir_xdg_custom(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        custom = tmp_path / "custom_config"
        monkeypatch.setattr("specli.config._is_xdg_platform", lambda: True)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(custom))

        result = get_config_dir()
        assert result == custom / "specli"
        assert result.is_dir()

    def test_cache_dir_xdg_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("specli.config._is_xdg_platform", lambda: True)
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

        result = get_cache_dir()
        assert result == tmp_path / ".cache" / "specli"
        assert result.is_dir()

    def test_cache_dir_xdg_custom(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        custom = tmp_path / "custom_cache"
        monkeypatch.setattr("specli.config._is_xdg_platform", lambda: True)
        monkeypatch.setenv("XDG_CACHE_HOME", str(custom))

        result = get_cache_dir()
        assert result == custom / "specli"
        assert result.is_dir()

    def test_data_dir_xdg_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("specli.config._is_xdg_platform", lambda: True)
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

        result = get_data_dir()
        assert result == tmp_path / ".local" / "share" / "specli"
        assert result.is_dir()

    def test_data_dir_xdg_custom(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        custom = tmp_path / "custom_data"
        monkeypatch.setattr("specli.config._is_xdg_platform", lambda: True)
        monkeypatch.setenv("XDG_DATA_HOME", str(custom))

        result = get_data_dir()
        assert result == custom / "specli"
        assert result.is_dir()


class TestXDGPathsFallback:
    """Fallback paths on non-XDG platforms (macOS, Windows)."""

    def test_config_dir_fallback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("specli.config._is_xdg_platform", lambda: False)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

        result = get_config_dir()
        assert result == tmp_path / ".specli"
        assert result.is_dir()

    def test_cache_dir_fallback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("specli.config._is_xdg_platform", lambda: False)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

        result = get_cache_dir()
        assert result == tmp_path / ".specli" / "cache"
        assert result.is_dir()

    def test_data_dir_fallback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("specli.config._is_xdg_platform", lambda: False)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

        result = get_data_dir()
        assert result == tmp_path / ".specli" / "logs"
        assert result.is_dir()


class TestProfilesDir:
    def test_profiles_dir_is_inside_config_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("specli.config._is_xdg_platform", lambda: True)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        result = get_profiles_dir()
        assert result == tmp_path / "specli" / "profiles"
        assert result.is_dir()


# ---------------------------------------------------------------------------
# Atomic writes
# ---------------------------------------------------------------------------


class TestAtomicWrite:
    def test_creates_file_with_content(self, tmp_path: Path) -> None:
        target = tmp_path / "test.txt"
        _atomic_write(target, "hello world")
        assert target.read_text(encoding="utf-8") == "hello world"

    def test_overwrites_existing_file(self, tmp_path: Path) -> None:
        target = tmp_path / "test.txt"
        target.write_text("old content", encoding="utf-8")
        _atomic_write(target, "new content")
        assert target.read_text(encoding="utf-8") == "new content"

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        target = tmp_path / "a" / "b" / "c" / "test.txt"
        _atomic_write(target, "deep write")
        assert target.read_text(encoding="utf-8") == "deep write"

    def test_no_temp_files_left_on_success(self, tmp_path: Path) -> None:
        target = tmp_path / "test.txt"
        _atomic_write(target, "content")
        # Only the target file should exist
        files = list(tmp_path.iterdir())
        assert files == [target]

    def test_no_temp_files_left_on_error(self, tmp_path: Path) -> None:
        target = tmp_path / "test.txt"
        with patch("specli.config.os.fsync", side_effect=OSError("disk error")):
            with pytest.raises(OSError, match="disk error"):
                _atomic_write(target, "will fail")
        # No temp files should remain; original target should not exist
        files = list(tmp_path.iterdir())
        assert target not in files
        # Filter to only tmp files (the target shouldn't exist either)
        tmp_files = [f for f in files if ".tmp" in f.name]
        assert tmp_files == []

    def test_unicode_content(self, tmp_path: Path) -> None:
        target = tmp_path / "unicode.txt"
        content = "Hello \u4e16\u754c \U0001f30d \u00e9\u00e0\u00fc\u00f1"
        _atomic_write(target, content)
        assert target.read_text(encoding="utf-8") == content

    def test_empty_content(self, tmp_path: Path) -> None:
        target = tmp_path / "empty.txt"
        _atomic_write(target, "")
        assert target.read_text(encoding="utf-8") == ""


# ---------------------------------------------------------------------------
# Global config
# ---------------------------------------------------------------------------


class TestGlobalConfig:
    def test_load_returns_defaults_when_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("specli.config._is_xdg_platform", lambda: True)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        cfg = load_global_config()
        assert cfg == GlobalConfig()
        assert cfg.default_profile is None
        assert cfg.auto_select_single_profile is True
        assert cfg.output.format == "auto"

    def test_save_and_load_roundtrip(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("specli.config._is_xdg_platform", lambda: True)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        original = GlobalConfig(
            default_profile="my-api",
            auto_select_single_profile=False,
            output=OutputConfig(format="json", pager=False),
        )
        save_global_config(original)
        loaded = load_global_config()
        assert loaded == original

    def test_load_invalid_json_raises_config_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("specli.config._is_xdg_platform", lambda: True)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        config_dir = tmp_path / "specli"
        config_dir.mkdir(parents=True)
        (config_dir / "config.json").write_text("{invalid json!!!", encoding="utf-8")

        with pytest.raises(ConfigError, match="Invalid global config"):
            load_global_config()

    def test_load_invalid_schema_raises_config_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("specli.config._is_xdg_platform", lambda: True)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        config_dir = tmp_path / "specli"
        config_dir.mkdir(parents=True)
        # auto_select_single_profile must be bool
        _write_json(config_dir / "config.json", {"auto_select_single_profile": "not-a-bool"})

        # Pydantic coerces "not-a-bool" to truthy; use something that truly fails
        _write_json(config_dir / "config.json", {"output": "not-a-dict"})
        with pytest.raises(ConfigError, match="Invalid global config"):
            load_global_config()

    def test_saved_config_is_valid_json(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("specli.config._is_xdg_platform", lambda: True)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        save_global_config(GlobalConfig())
        path = tmp_path / "specli" / "config.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        assert "default_profile" in data


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------


class TestProfiles:
    def test_list_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("specli.config._is_xdg_platform", lambda: True)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        assert list_profiles() == []

    def test_save_and_list(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("specli.config._is_xdg_platform", lambda: True)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        save_profile(_make_profile("beta"))
        save_profile(_make_profile("alpha"))
        assert list_profiles() == ["alpha", "beta"]

    def test_save_and_load_roundtrip(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("specli.config._is_xdg_platform", lambda: True)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        original = Profile(
            name="github",
            spec="https://api.github.com/openapi.json",
            base_url="https://api.github.com",
            auth=AuthConfig(type="bearer", source="env:GITHUB_TOKEN"),
        )
        save_profile(original)
        loaded = load_profile("github")
        assert loaded == original

    def test_load_nonexistent_raises_config_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("specli.config._is_xdg_platform", lambda: True)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        with pytest.raises(ConfigError, match="not found"):
            load_profile("nonexistent")

    def test_delete_profile(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("specli.config._is_xdg_platform", lambda: True)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        save_profile(_make_profile("to-delete"))
        assert profile_exists("to-delete")
        delete_profile("to-delete")
        assert not profile_exists("to-delete")

    def test_delete_nonexistent_raises_config_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("specli.config._is_xdg_platform", lambda: True)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        with pytest.raises(ConfigError, match="not found"):
            delete_profile("ghost")

    def test_profile_exists_true(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("specli.config._is_xdg_platform", lambda: True)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        save_profile(_make_profile("exists"))
        assert profile_exists("exists") is True

    def test_profile_exists_false(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("specli.config._is_xdg_platform", lambda: True)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        assert profile_exists("nope") is False

    def test_save_overwrites_existing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("specli.config._is_xdg_platform", lambda: True)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        save_profile(Profile(name="api", spec="old.json"))
        save_profile(Profile(name="api", spec="new.json"))
        loaded = load_profile("api")
        assert loaded.spec == "new.json"

    def test_load_invalid_json_raises_config_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("specli.config._is_xdg_platform", lambda: True)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        profiles_dir = tmp_path / "specli" / "profiles"
        profiles_dir.mkdir(parents=True)
        (profiles_dir / "bad.json").write_text("not json!!!", encoding="utf-8")

        with pytest.raises(ConfigError, match="Invalid profile"):
            load_profile("bad")


# ---------------------------------------------------------------------------
# Project-local config
# ---------------------------------------------------------------------------


class TestProjectConfig:
    def test_load_returns_none_when_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        assert load_project_config() is None

    def test_load_valid_project_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        _write_json(tmp_path / "specli.json", {"default_profile": "local-api"})

        result = load_project_config()
        assert result is not None
        assert result["default_profile"] == "local-api"

    def test_load_invalid_json_raises_config_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "specli.json").write_text("broken{", encoding="utf-8")

        with pytest.raises(ConfigError, match="Invalid project config"):
            load_project_config()


# ---------------------------------------------------------------------------
# Precedence resolution
# ---------------------------------------------------------------------------


class TestResolveConfig:
    """Test the full precedence chain: CLI > env > project > global > defaults."""

    @pytest.fixture(autouse=True)
    def _isolate_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Redirect all config paths to tmp_path and clear relevant env vars."""
        self.tmp_path = tmp_path
        monkeypatch.setattr("specli.config._is_xdg_platform", lambda: True)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("SPECLI_PROFILE", raising=False)
        monkeypatch.delenv("SPECLI_BASE_URL", raising=False)

    def _create_profile(self, name: str, spec: str = "https://example.com/spec.json") -> None:
        save_profile(Profile(name=name, spec=spec))

    def test_defaults_no_profile(self) -> None:
        cfg, profile = resolve_config()
        assert isinstance(cfg, GlobalConfig)
        assert profile is None

    def test_global_default_profile(self) -> None:
        self._create_profile("global-api")
        save_global_config(GlobalConfig(default_profile="global-api"))

        cfg, profile = resolve_config()
        assert profile is not None
        assert profile.name == "global-api"

    def test_project_overrides_global(self) -> None:
        self._create_profile("global-api")
        self._create_profile("project-api")
        save_global_config(GlobalConfig(default_profile="global-api"))
        _write_json(self.tmp_path / "specli.json", {"default_profile": "project-api"})

        _, profile = resolve_config()
        assert profile is not None
        assert profile.name == "project-api"

    def test_env_overrides_project(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._create_profile("project-api")
        self._create_profile("env-api")
        _write_json(self.tmp_path / "specli.json", {"default_profile": "project-api"})
        monkeypatch.setenv("SPECLI_PROFILE", "env-api")

        _, profile = resolve_config()
        assert profile is not None
        assert profile.name == "env-api"

    def test_cli_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._create_profile("env-api")
        self._create_profile("cli-api")
        monkeypatch.setenv("SPECLI_PROFILE", "env-api")

        _, profile = resolve_config(cli_profile="cli-api")
        assert profile is not None
        assert profile.name == "cli-api"

    def test_cli_base_url_overrides_profile(self) -> None:
        self._create_profile("api")
        save_global_config(GlobalConfig(default_profile="api"))

        _, profile = resolve_config(cli_base_url="https://override.example.com")
        assert profile is not None
        assert profile.base_url == "https://override.example.com"

    def test_env_base_url_overrides_profile(self, monkeypatch: pytest.MonkeyPatch) -> None:
        save_profile(Profile(name="api", spec="spec.json", base_url="https://original.example.com"))
        save_global_config(GlobalConfig(default_profile="api"))
        monkeypatch.setenv("SPECLI_BASE_URL", "https://env-override.example.com")

        _, profile = resolve_config()
        assert profile is not None
        assert profile.base_url == "https://env-override.example.com"

    def test_cli_base_url_beats_env_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._create_profile("api")
        save_global_config(GlobalConfig(default_profile="api"))
        monkeypatch.setenv("SPECLI_BASE_URL", "https://env.example.com")

        _, profile = resolve_config(cli_base_url="https://cli.example.com")
        assert profile is not None
        assert profile.base_url == "https://cli.example.com"

    def test_cli_format_overrides_global(self) -> None:
        save_global_config(GlobalConfig(output=OutputConfig(format="plain")))

        cfg, _ = resolve_config(cli_format="json")
        assert cfg.output.format == "json"

    def test_auto_select_single_profile(self) -> None:
        self._create_profile("only-one")

        _, profile = resolve_config()
        assert profile is not None
        assert profile.name == "only-one"

    def test_auto_select_disabled(self) -> None:
        self._create_profile("only-one")
        save_global_config(GlobalConfig(auto_select_single_profile=False))

        _, profile = resolve_config()
        assert profile is None

    def test_auto_select_skipped_when_multiple(self) -> None:
        self._create_profile("alpha")
        self._create_profile("beta")

        _, profile = resolve_config()
        assert profile is None

    def test_nonexistent_profile_raises_config_error(self) -> None:
        with pytest.raises(ConfigError, match="not found"):
            resolve_config(cli_profile="does-not-exist")


# ---------------------------------------------------------------------------
# Credential resolution
# ---------------------------------------------------------------------------


class TestResolveCredential:
    def test_env_source(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_TOKEN", "secret123")
        assert resolve_credential("env:MY_TOKEN") == "secret123"

    def test_env_source_missing_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NONEXISTENT_VAR", raising=False)
        with pytest.raises(ConfigError, match="not set"):
            resolve_credential("env:NONEXISTENT_VAR")

    def test_file_source(self, tmp_path: Path) -> None:
        cred_file = tmp_path / "token.txt"
        cred_file.write_text("  my-secret-token  \n", encoding="utf-8")
        assert resolve_credential(f"file:{cred_file}") == "my-secret-token"

    def test_file_source_missing_raises(self) -> None:
        with pytest.raises(ConfigError, match="not found"):
            resolve_credential("file:/nonexistent/path/token.txt")

    def test_file_source_unreadable_raises(self, tmp_path: Path) -> None:
        cred_file = tmp_path / "unreadable.txt"
        cred_file.write_text("secret", encoding="utf-8")
        cred_file.chmod(0o000)
        try:
            with pytest.raises(ConfigError, match="Cannot read"):
                resolve_credential(f"file:{cred_file}")
        finally:
            # Restore permissions so pytest can clean up tmp_path
            cred_file.chmod(stat.S_IRUSR | stat.S_IWUSR)

    def test_prompt_source_with_tty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("getpass.getpass", lambda prompt: "user-typed-secret")

        assert resolve_credential("prompt") == "user-typed-secret"

    def test_prompt_source_non_tty_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)

        with pytest.raises(ConfigError, match="not a TTY"):
            resolve_credential("prompt")

    def test_keyring_source_raises(self) -> None:
        with pytest.raises(ConfigError, match="keyring plugin"):
            resolve_credential("keyring:myservice:myaccount")

    def test_unknown_source_raises(self) -> None:
        with pytest.raises(ConfigError, match="Unknown credential source"):
            resolve_credential("magic:wand")

    def test_env_source_empty_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An empty string is still a valid value from the environment."""
        monkeypatch.setenv("EMPTY_VAR", "")
        assert resolve_credential("env:EMPTY_VAR") == ""

    def test_file_source_home_expansion(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Tilde in file path should be expanded."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        cred_file = tmp_path / ".secret"
        cred_file.write_text("expanded-secret", encoding="utf-8")

        assert resolve_credential(f"file:~/.secret") == "expanded-secret"


# ---------------------------------------------------------------------------
# Edge cases / integration
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_profile_with_full_auth_roundtrip(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("specli.config._is_xdg_platform", lambda: True)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        original = Profile(
            name="complex",
            spec="https://api.example.com/spec.yaml",
            base_url="https://api.example.com/v2",
            auth=AuthConfig(
                type="oauth2_client_credentials",
                token_url="https://auth.example.com/token",
                scopes=["read", "write"],
                client_id_source="env:CLIENT_ID",
                client_secret_source="env:CLIENT_SECRET",
            ),
        )
        save_profile(original)
        loaded = load_profile("complex")
        assert loaded == original
        assert loaded.auth is not None
        assert loaded.auth.scopes == ["read", "write"]
        assert loaded.auth.client_id_source == "env:CLIENT_ID"

    def test_concurrent_save_does_not_corrupt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Saving the same profile rapidly should not produce a corrupt file."""
        monkeypatch.setattr("specli.config._is_xdg_platform", lambda: True)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        for i in range(20):
            save_profile(Profile(name="rapid", spec=f"spec-{i}.json"))

        loaded = load_profile("rapid")
        assert loaded.spec == "spec-19.json"

    def test_list_profiles_ignores_non_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("specli.config._is_xdg_platform", lambda: True)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        profiles_dir = get_profiles_dir()
        save_profile(_make_profile("valid"))
        (profiles_dir / "readme.txt").write_text("not a profile", encoding="utf-8")
        (profiles_dir / ".hidden.json").write_text("{}", encoding="utf-8")

        names = list_profiles()
        assert "valid" in names
        assert "readme" not in names
        # .hidden is technically a .json file, but with dotfile name
        # glob("*.json") does not match dotfiles on most systems
