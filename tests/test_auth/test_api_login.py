"""Tests for the api_login auth plugin."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from specli.auth.credential_store import CredentialEntry, CredentialStore
from specli.exceptions import AuthError
from specli.models import AuthConfig, Profile
from specli.plugins.api_login import APILoginPlugin


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    """Force the credential store into a temp dir on all platforms."""
    monkeypatch.setattr("specli.config._is_xdg_platform", lambda: True)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return tmp_path


def _make_profile(
    *,
    check_endpoint: str = "/me",
    secret_name: str | None = None,
    credential_name: str | None = "test-api",
    base_url: str = "https://api.example.com",
) -> Profile:
    extras: dict[str, Any] = {"check_endpoint": check_endpoint}
    if secret_name:
        extras["secret_name"] = secret_name
    auth = AuthConfig(
        type="api_login",
        header="X-API-Key",
        location="header",
        credential_name=credential_name,
        **extras,
    )
    return Profile(
        name="test-api",
        spec="https://api.example.com/openapi.json",
        base_url=base_url,
        auth=auth,
    )


def _mock_response(status_code: int) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    return resp


# ---------------------------------------------------------------------------
# authenticate() — reads store, no prompting, fail-loud
# ---------------------------------------------------------------------------


class TestAuthenticate:
    def test_authenticate_reads_stored_credential(self, isolated_data_dir) -> None:
        profile = _make_profile()
        store = CredentialStore(profile.auth.credential_name)
        store.save(
            CredentialEntry(
                auth_type="api_login",
                credential="KEY123",
                credential_name="X-API-Key",
                metadata={},
            )
        )

        result = APILoginPlugin().authenticate(profile.auth)
        assert result.headers == {"X-API-Key": "KEY123"}

    def test_authenticate_reads_secret_from_metadata(self, isolated_data_dir) -> None:
        profile = _make_profile(secret_name="X-API-Secret")
        store = CredentialStore(profile.auth.credential_name)
        store.save(
            CredentialEntry(
                auth_type="api_login",
                credential="KEY",
                credential_name="X-API-Key",
                metadata={"secret": "SECRET", "secret_name": "X-API-Secret"},
            )
        )

        result = APILoginPlugin().authenticate(profile.auth)
        assert result.headers == {"X-API-Key": "KEY", "X-API-Secret": "SECRET"}

    def test_authenticate_fails_when_not_logged_in(self, isolated_data_dir) -> None:
        profile = _make_profile()

        with pytest.raises(AuthError, match="Not logged in"):
            APILoginPlugin().authenticate(profile.auth)

    def test_authenticate_never_prompts(self, isolated_data_dir, monkeypatch) -> None:
        profile = _make_profile()

        def _boom(*_args: Any, **_kwargs: Any) -> str:
            raise AssertionError("authenticate() must not prompt")

        monkeypatch.setattr("getpass.getpass", _boom)
        with pytest.raises(AuthError):
            APILoginPlugin().authenticate(profile.auth)


# ---------------------------------------------------------------------------
# login() — prompt, verify, persist
# ---------------------------------------------------------------------------


class TestLogin:
    def test_login_with_explicit_key_verifies_and_persists(
        self, isolated_data_dir
    ) -> None:
        profile = _make_profile()
        plugin = APILoginPlugin()

        with patch(
            "specli.plugins.api_login.plugin.httpx.request",
            return_value=_mock_response(200),
        ) as mock_req:
            plugin.login(profile, key="KEY", verify=True)

        # One verification call happened.
        assert mock_req.call_count == 1
        call = mock_req.call_args
        assert call.args[0] == "GET"
        assert call.args[1] == "https://api.example.com/me"
        assert call.kwargs["headers"] == {"X-API-Key": "KEY"}

        # Credential now persisted.
        assert plugin.is_logged_in(profile)
        entry = CredentialStore("test-api").load()
        assert entry.credential == "KEY"

    def test_login_rejects_on_401(self, isolated_data_dir) -> None:
        profile = _make_profile()
        plugin = APILoginPlugin()

        with patch(
            "specli.plugins.api_login.plugin.httpx.request",
            return_value=_mock_response(401),
        ):
            with pytest.raises(AuthError, match="Credentials rejected"):
                plugin.login(profile, key="BAD", verify=True)

        assert not plugin.is_logged_in(profile)

    def test_login_rejects_on_403(self, isolated_data_dir) -> None:
        profile = _make_profile()
        plugin = APILoginPlugin()

        with patch(
            "specli.plugins.api_login.plugin.httpx.request",
            return_value=_mock_response(403),
        ):
            with pytest.raises(AuthError, match="Credentials rejected"):
                plugin.login(profile, key="BAD", verify=True)

        assert not plugin.is_logged_in(profile)

    def test_login_surfaces_unexpected_status(self, isolated_data_dir) -> None:
        profile = _make_profile()
        plugin = APILoginPlugin()

        with patch(
            "specli.plugins.api_login.plugin.httpx.request",
            return_value=_mock_response(500),
        ):
            with pytest.raises(AuthError, match="Unexpected status 500"):
                plugin.login(profile, key="KEY", verify=True)

        assert not plugin.is_logged_in(profile)

    def test_login_no_verify_skips_request(self, isolated_data_dir) -> None:
        profile = _make_profile()
        plugin = APILoginPlugin()

        with patch(
            "specli.plugins.api_login.plugin.httpx.request"
        ) as mock_req:
            plugin.login(profile, key="KEY", verify=False)

        assert mock_req.call_count == 0
        assert plugin.is_logged_in(profile)

    def test_login_prompts_when_no_key_given_and_stdin_is_tty(
        self, isolated_data_dir
    ) -> None:
        profile = _make_profile()
        plugin = APILoginPlugin()

        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("getpass.getpass", return_value="PROMPTED"),
            patch(
                "specli.plugins.api_login.plugin.httpx.request",
                return_value=_mock_response(200),
            ),
        ):
            plugin.login(profile, verify=True)

        assert CredentialStore("test-api").load().credential == "PROMPTED"

    def test_login_without_key_on_non_tty_raises(self, isolated_data_dir) -> None:
        profile = _make_profile()
        plugin = APILoginPlugin()

        with patch("sys.stdin.isatty", return_value=False):
            with pytest.raises(AuthError, match="stdin is not a TTY"):
                plugin.login(profile)

    def test_login_dual_credentials_persist_secret(self, isolated_data_dir) -> None:
        profile = _make_profile(secret_name="X-API-Secret")
        plugin = APILoginPlugin()

        with patch(
            "specli.plugins.api_login.plugin.httpx.request",
            return_value=_mock_response(200),
        ) as mock_req:
            plugin.login(profile, key="KEY", secret="SECRET", verify=True)

        headers_sent = mock_req.call_args.kwargs["headers"]
        assert headers_sent == {"X-API-Key": "KEY", "X-API-Secret": "SECRET"}

        entry = CredentialStore("test-api").load()
        assert entry.credential == "KEY"
        assert entry.metadata.get("secret") == "SECRET"

    def test_login_rejects_profile_without_api_login_auth(
        self, isolated_data_dir
    ) -> None:
        profile = Profile(
            name="x",
            spec="https://example.com/o.json",
            auth=AuthConfig(type="bearer", source="env:TOK"),
        )
        plugin = APILoginPlugin()

        with pytest.raises(AuthError, match="not configured for api_login"):
            plugin.login(profile, key="KEY")

    def test_login_uses_absolute_check_endpoint_as_is(
        self, isolated_data_dir
    ) -> None:
        profile = _make_profile(
            check_endpoint="https://other.example.com/verify"
        )
        plugin = APILoginPlugin()

        with patch(
            "specli.plugins.api_login.plugin.httpx.request",
            return_value=_mock_response(200),
        ) as mock_req:
            plugin.login(profile, key="K", verify=True)

        assert mock_req.call_args.args[1] == "https://other.example.com/verify"

    def test_login_uses_custom_check_method(self, isolated_data_dir) -> None:
        profile = _make_profile()
        profile.auth = AuthConfig(
            type="api_login",
            header="X-API-Key",
            location="header",
            credential_name="test-api",
            check_endpoint="/me",
            check_method="HEAD",
        )
        plugin = APILoginPlugin()

        with patch(
            "specli.plugins.api_login.plugin.httpx.request",
            return_value=_mock_response(200),
        ) as mock_req:
            plugin.login(profile, key="K", verify=True)

        assert mock_req.call_args.args[0] == "HEAD"


# ---------------------------------------------------------------------------
# logout()
# ---------------------------------------------------------------------------


class TestLogout:
    def test_logout_clears_stored_credential(self, isolated_data_dir) -> None:
        profile = _make_profile()
        plugin = APILoginPlugin()
        plugin.login(profile, key="K", verify=False)
        assert plugin.is_logged_in(profile)

        plugin.logout(profile)
        assert not plugin.is_logged_in(profile)

    def test_logout_is_safe_when_not_logged_in(self, isolated_data_dir) -> None:
        profile = _make_profile()
        APILoginPlugin().logout(profile)  # must not raise


# ---------------------------------------------------------------------------
# validate_config()
# ---------------------------------------------------------------------------


class TestValidateConfig:
    def test_missing_check_endpoint_is_error(self) -> None:
        cfg = AuthConfig(type="api_login", header="X-API-Key")
        errors = APILoginPlugin().validate_config(cfg)
        assert any("check_endpoint" in e for e in errors)

    def test_bad_location_is_error(self) -> None:
        cfg = AuthConfig(
            type="api_login",
            header="X-API-Key",
            location="weird",
            check_endpoint="/me",
        )
        errors = APILoginPlugin().validate_config(cfg)
        assert any("location" in e for e in errors)

    def test_valid_config_has_no_errors(self) -> None:
        cfg = AuthConfig(
            type="api_login",
            header="X-API-Key",
            location="header",
            check_endpoint="/me",
        )
        assert APILoginPlugin().validate_config(cfg) == []


# ---------------------------------------------------------------------------
# Default manager registration + scheme mapping
# ---------------------------------------------------------------------------


class TestIntegration:
    def test_default_manager_registers_api_login(self) -> None:
        from specli.auth.manager import create_default_manager

        manager = create_default_manager()
        assert "api_login" in manager.list_types()
        plugin = manager.get_plugin("api_login")
        assert isinstance(plugin, APILoginPlugin)

    def test_apikey_scheme_maps_to_api_login(self) -> None:
        from specli.commands.auth import _scheme_to_auth_config
        from specli.models import SecurityScheme

        scheme = SecurityScheme(
            name="ApiKeyAuth",
            type="apiKey",
            in_name="X-API-Key",
            in_location="header",
        )
        cfg = _scheme_to_auth_config(scheme)
        assert cfg.type == "api_login"
        assert cfg.header == "X-API-Key"
