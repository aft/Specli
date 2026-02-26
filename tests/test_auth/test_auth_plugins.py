"""Tests for auth plugins, AuthManager, and AuthResult."""

from __future__ import annotations

import base64

import pytest

from specli.auth.base import AuthPlugin, AuthResult
from specli.auth.manager import AuthManager, create_default_manager
from specli.plugins.api_key import APIKeyAuthPlugin
from specli.plugins.basic import BasicAuthPlugin
from specli.plugins.bearer import BearerAuthPlugin
from specli.exceptions import AuthError
from specli.models import AuthConfig, Profile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_auth_config(**kwargs: object) -> AuthConfig:
    """Build an AuthConfig with sensible defaults overridden by kwargs."""
    defaults: dict[str, object] = {"type": "api_key", "source": "env:TEST_TOKEN"}
    defaults.update(kwargs)
    return AuthConfig(**defaults)  # type: ignore[arg-type]


def _make_profile(auth: AuthConfig | None = None) -> Profile:
    return Profile(name="test", spec="https://example.com/spec.json", auth=auth)


# ---------------------------------------------------------------------------
# AuthResult
# ---------------------------------------------------------------------------


class TestAuthResult:
    def test_defaults_are_empty_dicts(self) -> None:
        result = AuthResult()
        assert result.headers == {}
        assert result.params == {}
        assert result.cookies == {}

    def test_custom_values(self) -> None:
        result = AuthResult(
            headers={"Authorization": "Bearer x"},
            params={"key": "val"},
            cookies={"session": "abc"},
        )
        assert result.headers == {"Authorization": "Bearer x"}
        assert result.params == {"key": "val"}
        assert result.cookies == {"session": "abc"}

    def test_none_arguments_become_empty_dicts(self) -> None:
        result = AuthResult(headers=None, params=None, cookies=None)
        assert result.headers == {}
        assert result.params == {}
        assert result.cookies == {}


# ---------------------------------------------------------------------------
# APIKeyAuthPlugin
# ---------------------------------------------------------------------------


class TestAPIKeyAuthPlugin:
    def test_auth_type(self) -> None:
        plugin = APIKeyAuthPlugin()
        assert plugin.auth_type == "api_key"

    def test_api_key_in_header(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_API_KEY", "secret-key-123")
        config = _make_auth_config(
            type="api_key",
            header="X-Custom-Key",
            location="header",
            source="env:MY_API_KEY",
        )
        result = APIKeyAuthPlugin().authenticate(config)
        assert result.headers == {"X-Custom-Key": "secret-key-123"}
        assert result.params == {}
        assert result.cookies == {}

    def test_api_key_in_query(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_API_KEY", "query-key-456")
        config = _make_auth_config(
            type="api_key",
            param_name="apiKey",
            location="query",
            source="env:MY_API_KEY",
        )
        result = APIKeyAuthPlugin().authenticate(config)
        assert result.params == {"apiKey": "query-key-456"}
        assert result.headers == {}
        assert result.cookies == {}

    def test_api_key_in_cookie(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_API_KEY", "cookie-key-789")
        config = _make_auth_config(
            type="api_key",
            header="session_id",
            location="cookie",
            source="env:MY_API_KEY",
        )
        result = APIKeyAuthPlugin().authenticate(config)
        assert result.cookies == {"session_id": "cookie-key-789"}
        assert result.headers == {}
        assert result.params == {}

    def test_api_key_header_defaults_to_x_api_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TOKEN", "val")
        config = _make_auth_config(
            type="api_key", location="header", source="env:TOKEN"
        )
        result = APIKeyAuthPlugin().authenticate(config)
        assert "X-API-Key" in result.headers

    def test_api_key_query_defaults_to_api_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TOKEN", "val")
        config = _make_auth_config(
            type="api_key", location="query", source="env:TOKEN"
        )
        result = APIKeyAuthPlugin().authenticate(config)
        assert "api_key" in result.params

    def test_api_key_uses_param_name_for_header(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When only param_name is set and location is header, use param_name."""
        monkeypatch.setenv("TOKEN", "val")
        config = _make_auth_config(
            type="api_key",
            param_name="X-My-Param",
            location="header",
            source="env:TOKEN",
        )
        result = APIKeyAuthPlugin().authenticate(config)
        assert result.headers == {"X-My-Param": "val"}

    def test_api_key_uses_header_for_query(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When only header is set and location is query, fall back to header name."""
        monkeypatch.setenv("TOKEN", "val")
        config = _make_auth_config(
            type="api_key",
            header="my_key",
            location="query",
            source="env:TOKEN",
        )
        result = APIKeyAuthPlugin().authenticate(config)
        assert result.params == {"my_key": "val"}

    def test_validate_missing_header_and_param_name(self) -> None:
        config = _make_auth_config(type="api_key")
        errors = APIKeyAuthPlugin().validate_config(config)
        assert any("header" in e and "param_name" in e for e in errors)

    def test_validate_invalid_location(self) -> None:
        config = _make_auth_config(
            type="api_key", header="X-Key", location="body"
        )
        errors = APIKeyAuthPlugin().validate_config(config)
        assert any("location" in e.lower() for e in errors)

    def test_validate_valid_config(self) -> None:
        config = _make_auth_config(
            type="api_key", header="X-Key", location="header"
        )
        errors = APIKeyAuthPlugin().validate_config(config)
        assert errors == []

    def test_file_source(self, tmp_path: pytest.TempPathFactory) -> None:
        """Credential resolved from a file."""
        cred_file = tmp_path / "key.txt"  # type: ignore[union-attr]
        cred_file.write_text("  file-secret  \n", encoding="utf-8")
        config = _make_auth_config(
            type="api_key",
            header="X-Key",
            location="header",
            source=f"file:{cred_file}",
        )
        result = APIKeyAuthPlugin().authenticate(config)
        assert result.headers == {"X-Key": "file-secret"}

    def test_refresh_delegates_to_authenticate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TOKEN", "refreshed")
        config = _make_auth_config(
            type="api_key", header="X-Key", location="header", source="env:TOKEN"
        )
        plugin = APIKeyAuthPlugin()
        result = plugin.refresh(config)
        assert result.headers == {"X-Key": "refreshed"}

    def test_api_key_with_secret_in_header(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MY_KEY", "key-123")
        monkeypatch.setenv("MY_SECRET", "secret-456")
        config = _make_auth_config(
            type="api_key",
            header="X-API-Key",
            location="header",
            source="env:MY_KEY",
            secret_header="X-API-Secret",
            secret_source="env:MY_SECRET",
        )
        result = APIKeyAuthPlugin().authenticate(config)
        assert result.headers == {
            "X-API-Key": "key-123",
            "X-API-Secret": "secret-456",
        }

    def test_api_key_with_secret_in_query(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MY_KEY", "key-123")
        monkeypatch.setenv("MY_SECRET", "secret-456")
        config = _make_auth_config(
            type="api_key",
            param_name="api_key",
            location="query",
            source="env:MY_KEY",
            secret_header="api_secret",
            secret_source="env:MY_SECRET",
        )
        result = APIKeyAuthPlugin().authenticate(config)
        assert result.params == {"api_key": "key-123", "api_secret": "secret-456"}

    def test_api_key_with_secret_defaults_header_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """secret_header defaults to X-API-Secret when not specified."""
        monkeypatch.setenv("MY_KEY", "key-123")
        monkeypatch.setenv("MY_SECRET", "secret-456")
        config = _make_auth_config(
            type="api_key",
            header="X-API-Key",
            location="header",
            source="env:MY_KEY",
            secret_source="env:MY_SECRET",
        )
        result = APIKeyAuthPlugin().authenticate(config)
        assert result.headers == {
            "X-API-Key": "key-123",
            "X-API-Secret": "secret-456",
        }

    def test_api_key_without_secret_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When no secret_source, only one header is sent (backward compat)."""
        monkeypatch.setenv("MY_KEY", "key-only")
        config = _make_auth_config(
            type="api_key",
            header="X-API-Key",
            location="header",
            source="env:MY_KEY",
        )
        result = APIKeyAuthPlugin().authenticate(config)
        assert result.headers == {"X-API-Key": "key-only"}


# ---------------------------------------------------------------------------
# BearerAuthPlugin
# ---------------------------------------------------------------------------


class TestBearerAuthPlugin:
    def test_auth_type(self) -> None:
        assert BearerAuthPlugin().auth_type == "bearer"

    def test_bearer_token_in_header(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_abc123")
        config = _make_auth_config(type="bearer", source="env:GITHUB_TOKEN")
        result = BearerAuthPlugin().authenticate(config)
        assert result.headers == {"Authorization": "Bearer ghp_abc123"}
        assert result.params == {}
        assert result.cookies == {}

    def test_bearer_from_file(self, tmp_path: pytest.TempPathFactory) -> None:
        cred_file = tmp_path / "token.txt"  # type: ignore[union-attr]
        cred_file.write_text("file-bearer-token\n", encoding="utf-8")
        config = _make_auth_config(type="bearer", source=f"file:{cred_file}")
        result = BearerAuthPlugin().authenticate(config)
        assert result.headers == {"Authorization": "Bearer file-bearer-token"}

    def test_validate_valid(self) -> None:
        config = _make_auth_config(type="bearer", source="env:TOKEN")
        assert BearerAuthPlugin().validate_config(config) == []

    def test_validate_missing_source(self) -> None:
        config = _make_auth_config(type="bearer", source="")
        errors = BearerAuthPlugin().validate_config(config)
        assert any("source" in e.lower() for e in errors)

    def test_refresh_delegates_to_authenticate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TOKEN", "refreshed-bearer")
        config = _make_auth_config(type="bearer", source="env:TOKEN")
        result = BearerAuthPlugin().refresh(config)
        assert result.headers == {"Authorization": "Bearer refreshed-bearer"}


# ---------------------------------------------------------------------------
# BasicAuthPlugin
# ---------------------------------------------------------------------------


class TestBasicAuthPlugin:
    def test_auth_type(self) -> None:
        assert BasicAuthPlugin().auth_type == "basic"

    def test_basic_auth_encoding(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BASIC_CRED", "alice:s3cret")
        config = _make_auth_config(type="basic", source="env:BASIC_CRED")
        result = BasicAuthPlugin().authenticate(config)

        expected = base64.b64encode(b"alice:s3cret").decode("ascii")
        assert result.headers == {"Authorization": f"Basic {expected}"}
        assert result.params == {}
        assert result.cookies == {}

    def test_basic_auth_with_empty_password(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """username: with empty password is valid (colon present)."""
        monkeypatch.setenv("BASIC_CRED", "user:")
        config = _make_auth_config(type="basic", source="env:BASIC_CRED")
        result = BasicAuthPlugin().authenticate(config)

        expected = base64.b64encode(b"user:").decode("ascii")
        assert result.headers == {"Authorization": f"Basic {expected}"}

    def test_basic_auth_with_colon_in_password(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Password containing colons should work (split on first colon only)."""
        monkeypatch.setenv("BASIC_CRED", "user:pass:with:colons")
        config = _make_auth_config(type="basic", source="env:BASIC_CRED")
        result = BasicAuthPlugin().authenticate(config)

        expected = base64.b64encode(b"user:pass:with:colons").decode("ascii")
        assert result.headers == {"Authorization": f"Basic {expected}"}

    def test_basic_auth_no_colon_raises_auth_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BASIC_CRED", "no-colon-here")
        config = _make_auth_config(type="basic", source="env:BASIC_CRED")
        with pytest.raises(AuthError, match="username:password"):
            BasicAuthPlugin().authenticate(config)

    def test_basic_auth_unicode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BASIC_CRED", "user:\u00fcbersecret")
        config = _make_auth_config(type="basic", source="env:BASIC_CRED")
        result = BasicAuthPlugin().authenticate(config)

        expected = base64.b64encode("user:\u00fcbersecret".encode("utf-8")).decode("ascii")
        assert result.headers == {"Authorization": f"Basic {expected}"}

    def test_validate_valid(self) -> None:
        config = _make_auth_config(type="basic", source="env:CRED")
        assert BasicAuthPlugin().validate_config(config) == []

    def test_validate_missing_source(self) -> None:
        config = _make_auth_config(type="basic", source="")
        errors = BasicAuthPlugin().validate_config(config)
        assert any("source" in e.lower() for e in errors)

    def test_basic_from_file(self, tmp_path: pytest.TempPathFactory) -> None:
        cred_file = tmp_path / "creds.txt"  # type: ignore[union-attr]
        cred_file.write_text("admin:password123\n", encoding="utf-8")
        config = _make_auth_config(type="basic", source=f"file:{cred_file}")
        result = BasicAuthPlugin().authenticate(config)

        expected = base64.b64encode(b"admin:password123").decode("ascii")
        assert result.headers == {"Authorization": f"Basic {expected}"}


# ---------------------------------------------------------------------------
# AuthManager
# ---------------------------------------------------------------------------


class TestAuthManager:
    def test_register_and_get_plugin(self) -> None:
        manager = AuthManager()
        plugin = APIKeyAuthPlugin()
        manager.register(plugin)
        assert manager.get_plugin("api_key") is plugin

    def test_get_unknown_type_raises_auth_error(self) -> None:
        manager = AuthManager()
        with pytest.raises(AuthError, match="No auth plugin registered.*'oauth2'"):
            manager.get_plugin("oauth2")

    def test_get_unknown_type_lists_available(self) -> None:
        manager = AuthManager()
        manager.register(APIKeyAuthPlugin())
        manager.register(BearerAuthPlugin())
        with pytest.raises(AuthError, match="api_key, bearer"):
            manager.get_plugin("oauth2")

    def test_get_unknown_type_empty_registry(self) -> None:
        manager = AuthManager()
        with pytest.raises(AuthError, match="\\(none\\)"):
            manager.get_plugin("anything")

    def test_register_overwrites_existing(self) -> None:
        manager = AuthManager()
        plugin_a = APIKeyAuthPlugin()
        plugin_b = APIKeyAuthPlugin()
        manager.register(plugin_a)
        manager.register(plugin_b)
        assert manager.get_plugin("api_key") is plugin_b

    def test_list_types_sorted(self) -> None:
        manager = AuthManager()
        manager.register(BearerAuthPlugin())
        manager.register(APIKeyAuthPlugin())
        manager.register(BasicAuthPlugin())
        assert manager.list_types() == ["api_key", "basic", "bearer"]

    def test_list_types_empty(self) -> None:
        manager = AuthManager()
        assert manager.list_types() == []

    def test_authenticate_with_profile_auth(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MY_TOKEN", "tok123")
        auth = _make_auth_config(type="bearer", source="env:MY_TOKEN")
        profile = _make_profile(auth=auth)

        manager = AuthManager()
        manager.register(BearerAuthPlugin())
        result = manager.authenticate(profile)
        assert result.headers == {"Authorization": "Bearer tok123"}

    def test_authenticate_with_no_auth_returns_empty(self) -> None:
        profile = _make_profile(auth=None)
        manager = AuthManager()
        result = manager.authenticate(profile)
        assert result.headers == {}
        assert result.params == {}
        assert result.cookies == {}

    def test_authenticate_with_unknown_type_raises(self) -> None:
        auth = _make_auth_config(type="oauth2_magic")
        profile = _make_profile(auth=auth)
        manager = AuthManager()
        with pytest.raises(AuthError, match="oauth2_magic"):
            manager.authenticate(profile)


# ---------------------------------------------------------------------------
# create_default_manager
# ---------------------------------------------------------------------------


class TestCreateDefaultManager:
    def test_has_all_builtin_types(self) -> None:
        manager = create_default_manager()
        assert manager.list_types() == [
            "api_key",
            "api_key_gen",
            "basic",
            "bearer",
            "browser_login",
            "device_code",
            "manual_token",
            "oauth2_auth_code",
            "oauth2_client_credentials",
            "openid_connect",
        ]

    def test_api_key_plugin_works(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KEY", "test-key")
        manager = create_default_manager()
        plugin = manager.get_plugin("api_key")
        assert isinstance(plugin, APIKeyAuthPlugin)

    def test_bearer_plugin_works(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TOKEN", "test-token")
        manager = create_default_manager()
        plugin = manager.get_plugin("bearer")
        assert isinstance(plugin, BearerAuthPlugin)

    def test_basic_plugin_works(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CRED", "user:pass")
        manager = create_default_manager()
        plugin = manager.get_plugin("basic")
        assert isinstance(plugin, BasicAuthPlugin)

    def test_full_authenticate_flow(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("API_KEY", "my-key")
        manager = create_default_manager()
        auth = _make_auth_config(
            type="api_key",
            header="X-Custom",
            location="header",
            source="env:API_KEY",
        )
        profile = _make_profile(auth=auth)
        result = manager.authenticate(profile)
        assert result.headers == {"X-Custom": "my-key"}


# ---------------------------------------------------------------------------
# Integration: credential sources
# ---------------------------------------------------------------------------


class TestCredentialSourceIntegration:
    """Test auth plugins with various credential sources."""

    def test_env_source(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SECRET", "env-value")
        config = _make_auth_config(type="bearer", source="env:SECRET")
        result = BearerAuthPlugin().authenticate(config)
        assert "env-value" in result.headers["Authorization"]

    def test_file_source(self, tmp_path: pytest.TempPathFactory) -> None:
        cred = tmp_path / "cred.txt"  # type: ignore[union-attr]
        cred.write_text("  file-value  \n", encoding="utf-8")
        config = _make_auth_config(type="bearer", source=f"file:{cred}")
        result = BearerAuthPlugin().authenticate(config)
        assert "file-value" in result.headers["Authorization"]

    def test_missing_env_raises_config_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("NONEXISTENT_VAR", raising=False)
        config = _make_auth_config(type="bearer", source="env:NONEXISTENT_VAR")
        # resolve_credential raises ConfigError, which propagates
        from specli.exceptions import ConfigError

        with pytest.raises(ConfigError, match="not set"):
            BearerAuthPlugin().authenticate(config)

    def test_missing_file_raises_config_error(self) -> None:
        config = _make_auth_config(
            type="bearer", source="file:/nonexistent/path/token.txt"
        )
        from specli.exceptions import ConfigError

        with pytest.raises(ConfigError, match="not found"):
            BearerAuthPlugin().authenticate(config)
