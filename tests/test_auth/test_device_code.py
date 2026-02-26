"""Tests for the device_code auth plugin (OAuth2 Device Authorization Grant, RFC 8628)."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import httpx
import pytest

from specli.auth.credential_store import CredentialEntry, CredentialStore
from specli.exceptions import AuthError
from specli.models import AuthConfig
from specli.plugins.device_code import DeviceCodePlugin


def _make_config(**kwargs: object) -> AuthConfig:
    defaults: dict[str, object] = {
        "type": "device_code",
        "source": "prompt",
        "device_authorization_url": "https://oauth2.googleapis.com/device/code",
        "token_url": "https://oauth2.googleapis.com/token",
        "client_id_source": "env:GOOGLE_CLIENT_ID",
        "scopes": ["openid", "email"],
        "location": "header",
        "credential_name": "Authorization",
        "persist": False,
    }
    defaults.update(kwargs)
    return AuthConfig(**defaults)  # type: ignore[arg-type]


def _mock_httpx_post(
    response_data: dict[str, object] | None = None,
    status_code: int = 200,
) -> MagicMock:
    """Create a mock for httpx.post."""
    if response_data is None:
        response_data = {"access_token": "test-token", "expires_in": 3600}

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = status_code
    mock_response.json.return_value = response_data
    mock_response.text = str(response_data)

    if status_code >= 400:
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            message=f"HTTP {status_code}",
            request=MagicMock(),
            response=mock_response,
        )
    else:
        mock_response.raise_for_status.return_value = None

    return mock_response


@pytest.fixture()
def plugin() -> DeviceCodePlugin:
    return DeviceCodePlugin()


@pytest.fixture()
def _patch_store(tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "specli.auth.credential_store.get_data_dir",
        lambda: tmp_path,  # type: ignore[union-attr]
    )


# -------------------------------------------------------------------------
# Basic plugin properties
# -------------------------------------------------------------------------


class TestDeviceCodePlugin:
    def test_auth_type(self, plugin: DeviceCodePlugin) -> None:
        assert plugin.auth_type == "device_code"

    def test_validate_valid_config(self, plugin: DeviceCodePlugin) -> None:
        config = _make_config()
        assert plugin.validate_config(config) == []

    def test_validate_missing_device_auth_url(self, plugin: DeviceCodePlugin) -> None:
        config = _make_config(device_authorization_url=None)
        errors = plugin.validate_config(config)
        assert any("device_authorization_url" in e for e in errors)

    def test_validate_missing_token_url(self, plugin: DeviceCodePlugin) -> None:
        config = _make_config(token_url=None)
        errors = plugin.validate_config(config)
        assert any("token_url" in e for e in errors)

    def test_validate_missing_client_id(self, plugin: DeviceCodePlugin) -> None:
        config = _make_config(client_id_source=None)
        errors = plugin.validate_config(config)
        assert any("client_id_source" in e for e in errors)

    def test_validate_invalid_location(self, plugin: DeviceCodePlugin) -> None:
        config = _make_config(location="body")
        errors = plugin.validate_config(config)
        assert any("location" in e.lower() for e in errors)

    def test_validate_all_missing(self, plugin: DeviceCodePlugin) -> None:
        config = _make_config(
            device_authorization_url=None,
            token_url=None,
            client_id_source=None,
        )
        errors = plugin.validate_config(config)
        assert len(errors) == 3


# -------------------------------------------------------------------------
# Device code request
# -------------------------------------------------------------------------


class TestDeviceCodeRequest:
    def test_request_device_code_success(
        self, plugin: DeviceCodePlugin
    ) -> None:
        """POST to device auth endpoint returns device_code + user_code."""
        mock_resp = _mock_httpx_post({
            "device_code": "dev-code-123",
            "user_code": "ABCD-1234",
            "verification_uri": "https://www.google.com/device",
            "interval": 5,
            "expires_in": 1800,
        })

        with patch("specli.plugins.device_code.plugin.httpx.post", return_value=mock_resp) as mock_post:
            result = plugin._request_device_code(
                "https://oauth2.googleapis.com/device/code",
                "my-client-id",
                ["openid", "email"],
            )

        assert result["device_code"] == "dev-code-123"
        assert result["user_code"] == "ABCD-1234"

        posted_data = mock_post.call_args.kwargs["data"]
        assert posted_data["client_id"] == "my-client-id"
        assert posted_data["scope"] == "openid email"

    def test_request_device_code_no_scopes(
        self, plugin: DeviceCodePlugin
    ) -> None:
        """When scopes is empty, scope param should not be sent."""
        mock_resp = _mock_httpx_post({
            "device_code": "dc",
            "user_code": "UC",
            "verification_uri": "https://example.com/device",
        })

        with patch("specli.plugins.device_code.plugin.httpx.post", return_value=mock_resp) as mock_post:
            plugin._request_device_code(
                "https://example.com/device/code", "cid", []
            )

        posted_data = mock_post.call_args.kwargs["data"]
        assert "scope" not in posted_data

    def test_request_device_code_http_error(
        self, plugin: DeviceCodePlugin
    ) -> None:
        mock_resp = _mock_httpx_post(status_code=400)

        with patch("specli.plugins.device_code.plugin.httpx.post", return_value=mock_resp):
            with pytest.raises(AuthError, match="Device authorization request failed"):
                plugin._request_device_code(
                    "https://example.com/device/code", "cid", []
                )

    def test_request_device_code_missing_device_code(
        self, plugin: DeviceCodePlugin
    ) -> None:
        mock_resp = _mock_httpx_post({"user_code": "UC"})

        with patch("specli.plugins.device_code.plugin.httpx.post", return_value=mock_resp):
            with pytest.raises(AuthError, match="device_code"):
                plugin._request_device_code(
                    "https://example.com/device/code", "cid", []
                )

    def test_request_device_code_missing_user_code(
        self, plugin: DeviceCodePlugin
    ) -> None:
        mock_resp = _mock_httpx_post({"device_code": "dc"})

        with patch("specli.plugins.device_code.plugin.httpx.post", return_value=mock_resp):
            with pytest.raises(AuthError, match="user_code"):
                plugin._request_device_code(
                    "https://example.com/device/code", "cid", []
                )


# -------------------------------------------------------------------------
# User code display
# -------------------------------------------------------------------------


class TestDisplayUserCode:
    def test_display_prints_to_stderr(self, plugin: DeviceCodePlugin) -> None:
        """User code should be printed to stderr."""
        with patch("specli.plugins.device_code.plugin.sys.stderr") as mock_stderr:
            plugin._display_user_code("https://google.com/device", "ABCD-1234")

        # Check that write was called with verification URI and user code
        calls = [c.args[0] for c in mock_stderr.write.call_args_list]
        full_output = "".join(calls)
        assert "https://google.com/device" in full_output
        assert "ABCD-1234" in full_output


# -------------------------------------------------------------------------
# Token polling
# -------------------------------------------------------------------------


class TestTokenPolling:
    def test_poll_success_on_first_try(
        self, plugin: DeviceCodePlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Token returned immediately on first poll."""
        config = _make_config()
        mock_resp = _mock_httpx_post({
            "access_token": "polled-token",
            "refresh_token": "polled-refresh",
            "expires_in": 3600,
        })

        with patch("specli.plugins.device_code.plugin.httpx.post", return_value=mock_resp):
            with patch("specli.plugins.device_code.plugin.time.sleep"):
                result = plugin._poll_for_token(config, "dev-code", "cid", 1, 30)

        assert result["access_token"] == "polled-token"

    def test_poll_authorization_pending_then_success(
        self, plugin: DeviceCodePlugin
    ) -> None:
        """Poll returns authorization_pending twice, then succeeds."""
        config = _make_config()

        pending_resp = _mock_httpx_post({"error": "authorization_pending"})
        success_resp = _mock_httpx_post({
            "access_token": "delayed-token",
            "expires_in": 3600,
        })

        call_count = {"n": 0}
        original_responses = [pending_resp, pending_resp, success_resp]

        def mock_post(*args: object, **kwargs: object) -> MagicMock:
            idx = min(call_count["n"], len(original_responses) - 1)
            call_count["n"] += 1
            return original_responses[idx]

        with patch("specli.plugins.device_code.plugin.httpx.post", side_effect=mock_post):
            with patch("specli.plugins.device_code.plugin.time.sleep"):
                result = plugin._poll_for_token(config, "dev-code", "cid", 1, 60)

        assert result["access_token"] == "delayed-token"
        assert call_count["n"] == 3

    def test_poll_slow_down_increases_interval(
        self, plugin: DeviceCodePlugin
    ) -> None:
        """slow_down error should increase the polling interval by 5 seconds."""
        config = _make_config()

        slow_resp = _mock_httpx_post({"error": "slow_down"})
        success_resp = _mock_httpx_post({"access_token": "tok", "expires_in": 3600})

        responses = [slow_resp, success_resp]
        call_count = {"n": 0}

        def mock_post(*args: object, **kwargs: object) -> MagicMock:
            idx = min(call_count["n"], len(responses) - 1)
            call_count["n"] += 1
            return responses[idx]

        sleep_calls: list[float] = []

        def mock_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        with patch("specli.plugins.device_code.plugin.httpx.post", side_effect=mock_post):
            with patch("specli.plugins.device_code.plugin.time.sleep", side_effect=mock_sleep):
                plugin._poll_for_token(config, "dev-code", "cid", 5, 60)

        # First sleep: 5s (original interval), second sleep: 10s (increased by 5)
        assert sleep_calls[0] == 5
        assert sleep_calls[1] == 10

    def test_poll_access_denied(self, plugin: DeviceCodePlugin) -> None:
        """access_denied error raises AuthError."""
        config = _make_config()
        mock_resp = _mock_httpx_post({"error": "access_denied"})

        with patch("specli.plugins.device_code.plugin.httpx.post", return_value=mock_resp):
            with patch("specli.plugins.device_code.plugin.time.sleep"):
                with pytest.raises(AuthError, match="denied"):
                    plugin._poll_for_token(config, "dev-code", "cid", 1, 30)

    def test_poll_expired_token(self, plugin: DeviceCodePlugin) -> None:
        """expired_token error raises AuthError."""
        config = _make_config()
        mock_resp = _mock_httpx_post({"error": "expired_token"})

        with patch("specli.plugins.device_code.plugin.httpx.post", return_value=mock_resp):
            with patch("specli.plugins.device_code.plugin.time.sleep"):
                with pytest.raises(AuthError, match="expired"):
                    plugin._poll_for_token(config, "dev-code", "cid", 1, 30)

    def test_poll_timeout(self, plugin: DeviceCodePlugin) -> None:
        """Polling past deadline raises AuthError."""
        config = _make_config()
        pending_resp = _mock_httpx_post({"error": "authorization_pending"})

        # Set expires_in very short so deadline passes immediately
        import time as time_mod
        original_monotonic = time_mod.monotonic

        call_count = {"n": 0}

        def mock_monotonic() -> float:
            call_count["n"] += 1
            if call_count["n"] <= 1:
                return original_monotonic()
            # Return far future so deadline is passed
            return original_monotonic() + 10000

        with patch("specli.plugins.device_code.plugin.httpx.post", return_value=pending_resp):
            with patch("specli.plugins.device_code.plugin.time.sleep"):
                with patch("specli.plugins.device_code.plugin.time.monotonic", side_effect=mock_monotonic):
                    with pytest.raises(AuthError, match="timed out"):
                        plugin._poll_for_token(config, "dev-code", "cid", 1, 1)


# -------------------------------------------------------------------------
# Full flow
# -------------------------------------------------------------------------


class TestDeviceCodeFullFlow:
    def test_full_flow(
        self, plugin: DeviceCodePlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """End-to-end: device code request → display → poll → result."""
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-cid")

        config = _make_config()

        device_resp = _mock_httpx_post({
            "device_code": "dc-123",
            "user_code": "WXYZ-9876",
            "verification_uri": "https://www.google.com/device",
            "interval": 1,
            "expires_in": 300,
        })

        token_resp = _mock_httpx_post({
            "access_token": "final-access-token",
            "refresh_token": "final-refresh",
            "expires_in": 3600,
        })

        call_count = {"n": 0}

        def mock_post(*args: object, **kwargs: object) -> MagicMock:
            call_count["n"] += 1
            if call_count["n"] == 1:
                return device_resp
            return token_resp

        with patch("specli.plugins.device_code.plugin.httpx.post", side_effect=mock_post):
            with patch("specli.plugins.device_code.plugin.time.sleep"):
                with patch("specli.plugins.device_code.plugin.sys.stderr"):
                    result = plugin.authenticate(config)

        assert result.headers == {"Authorization": "Bearer final-access-token"}

    def test_result_as_cookie(
        self, plugin: DeviceCodePlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Location=cookie returns cookies dict."""
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
        config = _make_config(location="cookie", credential_name="session")

        device_resp = _mock_httpx_post({
            "device_code": "dc", "user_code": "UC",
            "verification_uri": "https://example.com/device",
            "interval": 1, "expires_in": 30,
        })
        token_resp = _mock_httpx_post({"access_token": "tok", "expires_in": 3600})

        responses = [device_resp, token_resp]
        call_count = {"n": 0}

        def mock_post(*args: object, **kwargs: object) -> MagicMock:
            idx = min(call_count["n"], len(responses) - 1)
            call_count["n"] += 1
            return responses[idx]

        with patch("specli.plugins.device_code.plugin.httpx.post", side_effect=mock_post):
            with patch("specli.plugins.device_code.plugin.time.sleep"):
                with patch("specli.plugins.device_code.plugin.sys.stderr"):
                    result = plugin.authenticate(config)

        assert result.cookies == {"session": "tok"}

    def test_result_as_query(
        self, plugin: DeviceCodePlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Location=query returns params dict."""
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
        config = _make_config(location="query", credential_name="api_key")

        device_resp = _mock_httpx_post({
            "device_code": "dc", "user_code": "UC",
            "verification_uri": "https://example.com/device",
            "interval": 1, "expires_in": 30,
        })
        token_resp = _mock_httpx_post({"access_token": "tok", "expires_in": 3600})

        responses = [device_resp, token_resp]
        call_count = {"n": 0}

        def mock_post(*args: object, **kwargs: object) -> MagicMock:
            idx = min(call_count["n"], len(responses) - 1)
            call_count["n"] += 1
            return responses[idx]

        with patch("specli.plugins.device_code.plugin.httpx.post", side_effect=mock_post):
            with patch("specli.plugins.device_code.plugin.time.sleep"):
                with patch("specli.plugins.device_code.plugin.sys.stderr"):
                    result = plugin.authenticate(config)

        assert result.params == {"api_key": "tok"}


# -------------------------------------------------------------------------
# Persistence & refresh
# -------------------------------------------------------------------------


class TestDeviceCodePersistence:
    @pytest.mark.usefixtures("_patch_store")
    def test_persist_and_reuse(
        self, plugin: DeviceCodePlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Persisted token is reused without re-running device flow."""
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
        config = _make_config(persist=True)

        device_resp = _mock_httpx_post({
            "device_code": "dc", "user_code": "UC",
            "verification_uri": "https://example.com/device",
            "interval": 1, "expires_in": 30,
        })
        token_resp = _mock_httpx_post({
            "access_token": "persistent-tok",
            "refresh_token": "rf-tok",
            "expires_in": 3600,
        })

        responses = [device_resp, token_resp]
        call_count = {"n": 0}

        def mock_post(*args: object, **kwargs: object) -> MagicMock:
            idx = min(call_count["n"], len(responses) - 1)
            call_count["n"] += 1
            return responses[idx]

        # First call: full device flow
        with patch("specli.plugins.device_code.plugin.httpx.post", side_effect=mock_post):
            with patch("specli.plugins.device_code.plugin.time.sleep"):
                with patch("specli.plugins.device_code.plugin.sys.stderr"):
                    result1 = plugin.authenticate(config)

        assert result1.headers == {"Authorization": "Bearer persistent-tok"}

        # Second call: should reuse stored token (no httpx.post calls)
        result2 = plugin.authenticate(config)
        assert result2.headers == {"Authorization": "Bearer persistent-tok"}

    @pytest.mark.usefixtures("_patch_store")
    def test_refresh_on_expired(
        self, plugin: DeviceCodePlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Expired token with refresh_token triggers refresh."""
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
        config = _make_config(persist=True)

        # Manually persist an expired entry with refresh_token
        store = CredentialStore("Authorization")
        store.save(
            CredentialEntry(
                auth_type="device_code",
                credential="expired-tok",
                credential_name="access_token",
                expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
                metadata={"refresh_token": "stored-refresh"},
            )
        )

        mock_resp = _mock_httpx_post({
            "access_token": "refreshed-tok",
            "refresh_token": "new-refresh",
            "expires_in": 3600,
        })

        with patch("specli.plugins.device_code.plugin.httpx.post", return_value=mock_resp) as mock_post:
            result = plugin.authenticate(config)

        assert result.headers == {"Authorization": "Bearer refreshed-tok"}
        posted_data = mock_post.call_args.kwargs["data"]
        assert posted_data["grant_type"] == "refresh_token"
        assert posted_data["refresh_token"] == "stored-refresh"
