"""Tests for the api_key_gen auth plugin."""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest

from specli.auth.credential_store import CredentialEntry, CredentialStore
from specli.exceptions import AuthError
from specli.models import AuthConfig
from specli.plugins.api_key_gen import APIKeyGenPlugin


def _make_config(**kwargs: object) -> AuthConfig:
    defaults: dict[str, object] = {
        "type": "api_key_gen",
        "source": "prompt",
        "key_create_endpoint": "https://api.example.com/v1/api-keys",
        "key_response_field": "api_key",
        "location": "header",
        "header": "X-API-Key",
    }
    defaults.update(kwargs)
    return AuthConfig(**defaults)  # type: ignore[arg-type]


@pytest.fixture()
def plugin() -> APIKeyGenPlugin:
    return APIKeyGenPlugin()


@pytest.fixture()
def _patch_store(tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "specli.auth.credential_store.get_data_dir",
        lambda: tmp_path,  # type: ignore[union-attr]
    )


def _mock_response(data: dict, status_code: int = 200) -> httpx.Response:
    """Create a mock httpx.Response."""
    return httpx.Response(
        status_code=status_code,
        json=data,
        request=httpx.Request("POST", "https://api.example.com/v1/api-keys"),
    )


class TestAPIKeyGenPlugin:
    def test_auth_type(self, plugin: APIKeyGenPlugin) -> None:
        assert plugin.auth_type == "api_key_gen"

    def test_create_key_success(self, plugin: APIKeyGenPlugin) -> None:
        config = _make_config()
        response = _mock_response({"api_key": "generated_key_123"})

        with patch("specli.plugins.api_key_gen.plugin.httpx.post", return_value=response):
            result = plugin.authenticate(config)

        assert result.headers == {"X-API-Key": "generated_key_123"}

    def test_create_key_custom_response_field(self, plugin: APIKeyGenPlugin) -> None:
        config = _make_config(key_response_field="key")
        response = _mock_response({"key": "custom_key_456"})

        with patch("specli.plugins.api_key_gen.plugin.httpx.post", return_value=response):
            result = plugin.authenticate(config)

        assert result.headers == {"X-API-Key": "custom_key_456"}

    def test_create_key_sends_body(self, plugin: APIKeyGenPlugin) -> None:
        body = {"name": "cli", "permissions": ["read"]}
        config = _make_config(key_create_body=body)
        response = _mock_response({"api_key": "k"})

        with patch("specli.plugins.api_key_gen.plugin.httpx.post", return_value=response) as mock_post:
            plugin.authenticate(config)

        call_kwargs = mock_post.call_args
        assert call_kwargs.kwargs["json"] == body

    def test_create_key_with_bootstrap_auth(
        self, plugin: APIKeyGenPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BOOTSTRAP_TOKEN", "boot_tok_123")
        config = _make_config(key_create_auth_source="env:BOOTSTRAP_TOKEN")
        response = _mock_response({"api_key": "new_key"})

        with patch("specli.plugins.api_key_gen.plugin.httpx.post", return_value=response) as mock_post:
            plugin.authenticate(config)

        call_kwargs = mock_post.call_args
        assert "Authorization" in call_kwargs.kwargs["headers"]
        assert "boot_tok_123" in call_kwargs.kwargs["headers"]["Authorization"]

    def test_create_key_missing_field_raises(self, plugin: APIKeyGenPlugin) -> None:
        config = _make_config(key_response_field="api_key")
        response = _mock_response({"id": "123", "name": "cli"})

        with patch("specli.plugins.api_key_gen.plugin.httpx.post", return_value=response):
            with pytest.raises(AuthError, match="missing.*api_key"):
                plugin.authenticate(config)

    def test_create_key_http_error_raises(self, plugin: APIKeyGenPlugin) -> None:
        config = _make_config()
        response = _mock_response({"error": "unauthorized"}, status_code=401)

        with patch("specli.plugins.api_key_gen.plugin.httpx.post", return_value=response):
            with pytest.raises(AuthError, match="status 401"):
                plugin.authenticate(config)

    def test_create_key_connection_error_raises(self, plugin: APIKeyGenPlugin) -> None:
        config = _make_config()

        with patch(
            "specli.plugins.api_key_gen.plugin.httpx.post",
            side_effect=httpx.ConnectError("Connection refused"),
        ):
            with pytest.raises(AuthError, match="request failed"):
                plugin.authenticate(config)

    def test_missing_endpoint_raises(self, plugin: APIKeyGenPlugin) -> None:
        config = _make_config(key_create_endpoint=None)
        with pytest.raises(AuthError, match="key_create_endpoint"):
            plugin.authenticate(config)

    def test_location_query(self, plugin: APIKeyGenPlugin) -> None:
        config = _make_config(location="query", param_name="key")
        response = _mock_response({"api_key": "q_key"})

        with patch("specli.plugins.api_key_gen.plugin.httpx.post", return_value=response):
            result = plugin.authenticate(config)

        assert result.params == {"X-API-Key": "q_key"}

    def test_location_cookie(self, plugin: APIKeyGenPlugin) -> None:
        config = _make_config(location="cookie", credential_name="auth_key")
        response = _mock_response({"api_key": "c_key"})

        with patch("specli.plugins.api_key_gen.plugin.httpx.post", return_value=response):
            result = plugin.authenticate(config)

        assert result.cookies == {"auth_key": "c_key"}

    @pytest.mark.usefixtures("_patch_store")
    def test_persist_saves_and_reuses(self, plugin: APIKeyGenPlugin) -> None:
        config = _make_config(persist=True, credential_name="my-key")
        response = _mock_response({"api_key": "persisted_key"})

        # First call: creates key
        with patch("specli.plugins.api_key_gen.plugin.httpx.post", return_value=response) as mock_post:
            result1 = plugin.authenticate(config)
        mock_post.assert_called_once()

        assert result1.headers == {"my-key": "persisted_key"}

        # Second call: should NOT make HTTP request, uses store
        result2 = plugin.authenticate(config)
        assert result2.headers == {"my-key": "persisted_key"}

    def test_validate_valid(self, plugin: APIKeyGenPlugin) -> None:
        config = _make_config()
        assert plugin.validate_config(config) == []

    def test_validate_missing_endpoint(self, plugin: APIKeyGenPlugin) -> None:
        config = _make_config(key_create_endpoint=None)
        errors = plugin.validate_config(config)
        assert any("key_create_endpoint" in e for e in errors)

    def test_validate_invalid_location(self, plugin: APIKeyGenPlugin) -> None:
        config = _make_config(location="body")
        errors = plugin.validate_config(config)
        assert any("location" in e.lower() for e in errors)
