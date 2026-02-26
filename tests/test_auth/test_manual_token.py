"""Tests for the manual_token auth plugin."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from specli.auth.credential_store import CredentialEntry, CredentialStore
from specli.exceptions import AuthError
from specli.models import AuthConfig
from specli.plugins.manual_token import ManualTokenPlugin


def _make_config(**kwargs: object) -> AuthConfig:
    defaults: dict[str, object] = {"type": "manual_token", "source": "prompt"}
    defaults.update(kwargs)
    return AuthConfig(**defaults)  # type: ignore[arg-type]


@pytest.fixture()
def plugin() -> ManualTokenPlugin:
    return ManualTokenPlugin()


@pytest.fixture()
def _patch_store(tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "specli.auth.credential_store.get_data_dir",
        lambda: tmp_path,  # type: ignore[union-attr]
    )


class TestManualTokenPlugin:
    def test_auth_type(self, plugin: ManualTokenPlugin) -> None:
        assert plugin.auth_type == "manual_token"

    def test_prompt_flow_header(self, plugin: ManualTokenPlugin) -> None:
        """Prompts for token and returns it as a header."""
        config = _make_config(credential_name="X-Token", location="header")
        with patch("specli.plugins.manual_token.plugin.getpass.getpass", return_value="my-token"):
            with patch("specli.plugins.manual_token.plugin.sys.stdin") as mock_stdin:
                mock_stdin.isatty.return_value = True
                result = plugin.authenticate(config)

        assert result.headers == {"X-Token": "my-token"}
        assert result.cookies == {}
        assert result.params == {}

    def test_prompt_flow_cookie(self, plugin: ManualTokenPlugin) -> None:
        config = _make_config(credential_name="session_id", location="cookie")
        with patch("specli.plugins.manual_token.plugin.getpass.getpass", return_value="sess123"):
            with patch("specli.plugins.manual_token.plugin.sys.stdin") as mock_stdin:
                mock_stdin.isatty.return_value = True
                result = plugin.authenticate(config)

        assert result.cookies == {"session_id": "sess123"}

    def test_prompt_flow_query(self, plugin: ManualTokenPlugin) -> None:
        config = _make_config(credential_name="token", location="query")
        with patch("specli.plugins.manual_token.plugin.getpass.getpass", return_value="q-tok"):
            with patch("specli.plugins.manual_token.plugin.sys.stdin") as mock_stdin:
                mock_stdin.isatty.return_value = True
                result = plugin.authenticate(config)

        assert result.params == {"token": "q-tok"}

    def test_empty_token_raises(self, plugin: ManualTokenPlugin) -> None:
        config = _make_config()
        with patch("specli.plugins.manual_token.plugin.getpass.getpass", return_value=""):
            with patch("specli.plugins.manual_token.plugin.sys.stdin") as mock_stdin:
                mock_stdin.isatty.return_value = True
                with pytest.raises(AuthError, match="No token provided"):
                    plugin.authenticate(config)

    def test_no_tty_raises(self, plugin: ManualTokenPlugin) -> None:
        config = _make_config()
        with patch("specli.plugins.manual_token.plugin.sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = False
            with pytest.raises(AuthError, match="interactive terminal"):
                plugin.authenticate(config)

    @pytest.mark.usefixtures("_patch_store")
    def test_persist_saves_and_reuses(self, plugin: ManualTokenPlugin) -> None:
        config = _make_config(
            credential_name="my-token", location="header", persist=True
        )

        # First call: prompts
        with patch("specli.plugins.manual_token.plugin.getpass.getpass", return_value="persisted-tok"):
            with patch("specli.plugins.manual_token.plugin.sys.stdin") as mock_stdin:
                mock_stdin.isatty.return_value = True
                result1 = plugin.authenticate(config)

        assert result1.headers == {"my-token": "persisted-tok"}

        # Second call: should NOT prompt, reads from store
        result2 = plugin.authenticate(config)
        assert result2.headers == {"my-token": "persisted-tok"}

    @pytest.mark.usefixtures("_patch_store")
    def test_no_persist_always_prompts(self, plugin: ManualTokenPlugin) -> None:
        config = _make_config(credential_name="X-Key", location="header", persist=False)

        with patch("specli.plugins.manual_token.plugin.getpass.getpass", return_value="tok1"):
            with patch("specli.plugins.manual_token.plugin.sys.stdin") as mock_stdin:
                mock_stdin.isatty.return_value = True
                plugin.authenticate(config)

        # Should prompt again
        with patch("specli.plugins.manual_token.plugin.getpass.getpass", return_value="tok2") as mock_getpass:
            with patch("specli.plugins.manual_token.plugin.sys.stdin") as mock_stdin:
                mock_stdin.isatty.return_value = True
                result = plugin.authenticate(config)

        mock_getpass.assert_called_once()
        assert result.headers == {"X-Key": "tok2"}

    def test_validate_valid(self, plugin: ManualTokenPlugin) -> None:
        config = _make_config(location="header")
        assert plugin.validate_config(config) == []

    def test_validate_invalid_location(self, plugin: ManualTokenPlugin) -> None:
        config = _make_config(location="body")
        errors = plugin.validate_config(config)
        assert any("location" in e.lower() for e in errors)

    def test_fallback_name_uses_header(self, plugin: ManualTokenPlugin) -> None:
        """When credential_name is not set, falls back to header field."""
        config = _make_config(header="X-Auth", location="header")
        with patch("specli.plugins.manual_token.plugin.getpass.getpass", return_value="t"):
            with patch("specli.plugins.manual_token.plugin.sys.stdin") as mock_stdin:
                mock_stdin.isatty.return_value = True
                result = plugin.authenticate(config)
        assert "X-Auth" in result.headers
