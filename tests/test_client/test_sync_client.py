"""Tests for the synchronous HTTP client."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from specli.auth.base import AuthResult
from specli.auth.manager import AuthManager
from specli.client.sync_client import SyncClient
from specli.exceptions import AuthError, ConnectionError_, NotFoundError, ServerError
from specli.models import AuthConfig, Profile, RequestConfig
from specli.output import OutputManager, reset_output, set_output
from specli.plugins.hooks import HookContext, HookRunner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_profile(
    base_url: str = "https://api.example.com",
    auth: AuthConfig | None = None,
    max_retries: int = 3,
    timeout: int = 30,
) -> Profile:
    return Profile(
        name="test",
        spec="https://example.com/spec.json",
        base_url=base_url,
        auth=auth,
        request=RequestConfig(timeout=timeout, max_retries=max_retries),
    )


def _transport_from_handler(handler):
    """Create an httpx.MockTransport from a handler function."""
    return httpx.MockTransport(handler)


def _json_response(data: Any, status_code: int = 200) -> httpx.Response:
    """Build a mock httpx.Response with JSON content."""
    return httpx.Response(
        status_code=status_code,
        headers={"content-type": "application/json"},
        json=data,
        request=httpx.Request("GET", "https://api.example.com/test"),
    )


@pytest.fixture(autouse=True)
def _clean_output():
    """Reset the global output manager between tests."""
    set_output(OutputManager(no_color=True, quiet=True))
    yield
    reset_output()


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


class TestContextManager:
    def test_enter_creates_client(self) -> None:
        profile = _make_profile()
        client = SyncClient(profile)
        assert client._client is None
        with client:
            assert client._client is not None
        assert client._client is None

    def test_exit_closes_client(self) -> None:
        profile = _make_profile()
        with SyncClient(profile) as client:
            inner_client = client._client
            assert inner_client is not None
        assert client._client is None

    def test_enter_authenticates_when_auth_present(self) -> None:
        auth_config = AuthConfig(type="bearer", source="env:TOKEN")
        profile = _make_profile(auth=auth_config)

        mock_auth_manager = MagicMock(spec=AuthManager)
        mock_auth_manager.authenticate.return_value = AuthResult(
            headers={"Authorization": "Bearer test-token"}
        )

        with SyncClient(profile, auth_manager=mock_auth_manager) as client:
            assert client._auth_result is not None
            assert client._auth_result.headers == {"Authorization": "Bearer test-token"}
            mock_auth_manager.authenticate.assert_called_once_with(profile)

    def test_enter_skips_auth_when_no_auth_config(self) -> None:
        profile = _make_profile(auth=None)
        mock_auth_manager = MagicMock(spec=AuthManager)

        with SyncClient(profile, auth_manager=mock_auth_manager) as client:
            assert client._auth_result is None
            mock_auth_manager.authenticate.assert_not_called()

    def test_enter_skips_auth_when_no_auth_manager(self) -> None:
        auth_config = AuthConfig(type="bearer", source="env:TOKEN")
        profile = _make_profile(auth=auth_config)

        with SyncClient(profile, auth_manager=None) as client:
            assert client._auth_result is None


# ---------------------------------------------------------------------------
# GET request
# ---------------------------------------------------------------------------


class TestGetRequest:
    def test_simple_get(self) -> None:
        profile = _make_profile(max_retries=0)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"users": [{"id": 1}]},
                headers={"content-type": "application/json"},
            )

        with SyncClient(profile) as client:
            client._client = httpx.Client(
                base_url="https://api.example.com",
                transport=_transport_from_handler(handler),
            )
            response = client.get("/users")
            assert response.status_code == 200
            assert response.json() == {"users": [{"id": 1}]}

    def test_get_with_params(self) -> None:
        profile = _make_profile(max_retries=0)

        def handler(request: httpx.Request) -> httpx.Response:
            assert b"page=2" in bytes(str(request.url), "utf-8")
            return httpx.Response(200, json={"page": 2})

        with SyncClient(profile) as client:
            client._client = httpx.Client(
                base_url="https://api.example.com",
                transport=_transport_from_handler(handler),
            )
            response = client.get("/users", params={"page": "2"})
            assert response.status_code == 200

    def test_get_with_headers(self) -> None:
        profile = _make_profile(max_retries=0)

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers.get("x-custom") == "value"
            return httpx.Response(200, json={"ok": True})

        with SyncClient(profile) as client:
            client._client = httpx.Client(
                base_url="https://api.example.com",
                transport=_transport_from_handler(handler),
            )
            response = client.get("/test", headers={"x-custom": "value"})
            assert response.status_code == 200


# ---------------------------------------------------------------------------
# POST request with JSON body
# ---------------------------------------------------------------------------


class TestPostRequest:
    def test_post_with_json_body(self) -> None:
        profile = _make_profile(max_retries=0)

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert body == {"name": "test", "active": True}
            return httpx.Response(201, json={"id": 42})

        with SyncClient(profile) as client:
            client._client = httpx.Client(
                base_url="https://api.example.com",
                transport=_transport_from_handler(handler),
            )
            response = client.post(
                "/items", json_body={"name": "test", "active": True}
            )
            assert response.status_code == 201
            assert response.json() == {"id": 42}

    def test_post_with_raw_body(self) -> None:
        profile = _make_profile(max_retries=0)

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.content == b"raw body content"
            return httpx.Response(200, json={"ok": True})

        with SyncClient(profile) as client:
            client._client = httpx.Client(
                base_url="https://api.example.com",
                transport=_transport_from_handler(handler),
            )
            response = client.post("/data", body="raw body content")
            assert response.status_code == 200


# ---------------------------------------------------------------------------
# Auth injection
# ---------------------------------------------------------------------------


class TestAuthInjection:
    def test_auth_headers_injected(self) -> None:
        auth_config = AuthConfig(type="bearer", source="env:TOKEN")
        profile = _make_profile(auth=auth_config, max_retries=0)

        mock_auth_manager = MagicMock(spec=AuthManager)
        mock_auth_manager.authenticate.return_value = AuthResult(
            headers={"Authorization": "Bearer injected-token"}
        )

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers.get("authorization") == "Bearer injected-token"
            return httpx.Response(200, json={"ok": True})

        with SyncClient(profile, auth_manager=mock_auth_manager) as client:
            client._client = httpx.Client(
                base_url="https://api.example.com",
                transport=_transport_from_handler(handler),
            )
            response = client.get("/protected")
            assert response.status_code == 200

    def test_auth_params_injected(self) -> None:
        auth_config = AuthConfig(type="api_key", location="query", source="env:KEY")
        profile = _make_profile(auth=auth_config, max_retries=0)

        mock_auth_manager = MagicMock(spec=AuthManager)
        mock_auth_manager.authenticate.return_value = AuthResult(
            params={"api_key": "my-secret-key"}
        )

        def handler(request: httpx.Request) -> httpx.Response:
            assert "api_key=my-secret-key" in str(request.url)
            return httpx.Response(200, json={"ok": True})

        with SyncClient(profile, auth_manager=mock_auth_manager) as client:
            client._client = httpx.Client(
                base_url="https://api.example.com",
                transport=_transport_from_handler(handler),
            )
            response = client.get("/protected")
            assert response.status_code == 200

    def test_auth_cookies_injected_as_header(self) -> None:
        auth_config = AuthConfig(type="api_key", location="cookie", source="env:KEY")
        profile = _make_profile(auth=auth_config, max_retries=0)

        mock_auth_manager = MagicMock(spec=AuthManager)
        mock_auth_manager.authenticate.return_value = AuthResult(
            cookies={"session": "abc123", "token": "xyz"}
        )

        def handler(request: httpx.Request) -> httpx.Response:
            cookie_header = request.headers.get("cookie", "")
            assert "session=abc123" in cookie_header
            assert "token=xyz" in cookie_header
            return httpx.Response(200, json={"ok": True})

        with SyncClient(profile, auth_manager=mock_auth_manager) as client:
            client._client = httpx.Client(
                base_url="https://api.example.com",
                transport=_transport_from_handler(handler),
            )
            response = client.get("/protected")
            assert response.status_code == 200

    def test_caller_headers_override_auth_headers(self) -> None:
        """Caller-supplied headers take priority over auth-injected headers."""
        auth_config = AuthConfig(type="bearer", source="env:TOKEN")
        profile = _make_profile(auth=auth_config, max_retries=0)

        mock_auth_manager = MagicMock(spec=AuthManager)
        mock_auth_manager.authenticate.return_value = AuthResult(
            headers={"Authorization": "Bearer auth-token", "X-From": "auth"}
        )

        def handler(request: httpx.Request) -> httpx.Response:
            # Caller override should win
            assert request.headers.get("authorization") == "Bearer caller-token"
            # Auth header that wasn't overridden should still be present
            assert request.headers.get("x-from") == "auth"
            return httpx.Response(200, json={"ok": True})

        with SyncClient(profile, auth_manager=mock_auth_manager) as client:
            client._client = httpx.Client(
                base_url="https://api.example.com",
                transport=_transport_from_handler(handler),
            )
            response = client.get(
                "/protected",
                headers={"Authorization": "Bearer caller-token"},
            )
            assert response.status_code == 200


# ---------------------------------------------------------------------------
# No-auth request
# ---------------------------------------------------------------------------


class TestNoAuthRequest:
    def test_request_without_auth_config(self) -> None:
        profile = _make_profile(auth=None, max_retries=0)

        def handler(request: httpx.Request) -> httpx.Response:
            # No auth header should be present
            assert "authorization" not in request.headers
            return httpx.Response(200, json={"public": True})

        with SyncClient(profile) as client:
            client._client = httpx.Client(
                base_url="https://api.example.com",
                transport=_transport_from_handler(handler),
            )
            response = client.get("/public")
            assert response.status_code == 200
            assert response.json() == {"public": True}

    def test_request_without_auth_manager(self) -> None:
        auth_config = AuthConfig(type="bearer", source="env:TOKEN")
        profile = _make_profile(auth=auth_config, max_retries=0)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True})

        # No auth_manager passed: auth config exists but no manager to process it
        with SyncClient(profile, auth_manager=None) as client:
            client._client = httpx.Client(
                base_url="https://api.example.com",
                transport=_transport_from_handler(handler),
            )
            assert client._auth_result is None
            response = client.get("/test")
            assert response.status_code == 200


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_does_not_execute(self) -> None:
        """Dry-run should not send any HTTP request."""
        profile = _make_profile(max_retries=0)
        executed = False

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal executed
            executed = True
            return httpx.Response(200, json={"ok": True})

        # Use verbose output to capture dry-run messages
        set_output(OutputManager(no_color=True, verbose=True))

        with SyncClient(profile, dry_run=True) as client:
            client._client = httpx.Client(
                base_url="https://api.example.com",
                transport=_transport_from_handler(handler),
            )
            response = client.get("/test", params={"q": "hello"})

        assert not executed
        assert response.status_code == 200
        assert response.json()["dry_run"] is True

    def test_dry_run_with_json_body(self) -> None:
        profile = _make_profile(max_retries=0)
        set_output(OutputManager(no_color=True, verbose=True))

        with SyncClient(profile, dry_run=True) as client:
            response = client.post(
                "/items", json_body={"name": "test"}
            )
        assert response.status_code == 200
        assert response.json()["dry_run"] is True

    def test_dry_run_with_raw_body(self) -> None:
        profile = _make_profile(max_retries=0)
        set_output(OutputManager(no_color=True, verbose=True))

        with SyncClient(profile, dry_run=True) as client:
            response = client.post("/data", body="raw content")
        assert response.status_code == 200

    def test_dry_run_with_auth(self) -> None:
        """Dry-run should still show auth-injected headers."""
        auth_config = AuthConfig(type="bearer", source="env:TOKEN")
        profile = _make_profile(auth=auth_config, max_retries=0)

        mock_auth_manager = MagicMock(spec=AuthManager)
        mock_auth_manager.authenticate.return_value = AuthResult(
            headers={"Authorization": "Bearer dry-token"}
        )
        set_output(OutputManager(no_color=True, verbose=True))

        with SyncClient(profile, auth_manager=mock_auth_manager, dry_run=True) as client:
            response = client.get("/protected")

        assert response.status_code == 200
        assert response.json()["dry_run"] is True


# ---------------------------------------------------------------------------
# Retry on 5xx
# ---------------------------------------------------------------------------


class TestRetryOn5xx:
    @patch("specli.client.sync_client.time.sleep")
    def test_retry_on_500_then_success(self, mock_sleep: MagicMock) -> None:
        profile = _make_profile(max_retries=2)
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                return httpx.Response(500, json={"error": "Internal Server Error"})
            return httpx.Response(200, json={"ok": True})

        with SyncClient(profile) as client:
            client._client = httpx.Client(
                base_url="https://api.example.com",
                transport=_transport_from_handler(handler),
            )
            response = client.get("/test")

        assert response.status_code == 200
        assert call_count == 2
        mock_sleep.assert_called_once_with(1)  # 2^0 = 1

    @patch("specli.client.sync_client.time.sleep")
    def test_retry_on_503_exponential_backoff(self, mock_sleep: MagicMock) -> None:
        profile = _make_profile(max_retries=3)
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count < 4:
                return httpx.Response(503, json={"error": "Service Unavailable"})
            return httpx.Response(200, json={"ok": True})

        with SyncClient(profile) as client:
            client._client = httpx.Client(
                base_url="https://api.example.com",
                transport=_transport_from_handler(handler),
            )
            response = client.get("/test")

        assert response.status_code == 200
        assert call_count == 4
        # Backoff: 2^0=1, 2^1=2, 2^2=4
        assert mock_sleep.call_count == 3
        mock_sleep.assert_any_call(1)
        mock_sleep.assert_any_call(2)
        mock_sleep.assert_any_call(4)

    @patch("specli.client.sync_client.time.sleep")
    def test_max_retries_exhausted_returns_last_response(self, mock_sleep: MagicMock) -> None:
        """When all retries produce 5xx, the last response is returned and error mapping raises."""
        profile = _make_profile(max_retries=2)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "Internal Server Error"})

        with SyncClient(profile) as client:
            client._client = httpx.Client(
                base_url="https://api.example.com",
                transport=_transport_from_handler(handler),
            )
            with pytest.raises(ServerError, match="500"):
                client.get("/test")

    def test_no_retry_on_4xx(self) -> None:
        """4xx errors should NOT be retried."""
        profile = _make_profile(max_retries=3)
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(404, json={"error": "Not Found"})

        with SyncClient(profile) as client:
            client._client = httpx.Client(
                base_url="https://api.example.com",
                transport=_transport_from_handler(handler),
            )
            with pytest.raises(NotFoundError):
                client.get("/missing")

        assert call_count == 1  # No retries


# ---------------------------------------------------------------------------
# Retry on connection error
# ---------------------------------------------------------------------------


class TestRetryOnConnectionError:
    @patch("specli.client.sync_client.time.sleep")
    def test_retry_on_connect_error_then_success(self, mock_sleep: MagicMock) -> None:
        profile = _make_profile(max_retries=2)
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise httpx.ConnectError("Connection refused")
            return httpx.Response(200, json={"ok": True})

        with SyncClient(profile) as client:
            client._client = httpx.Client(
                base_url="https://api.example.com",
                transport=_transport_from_handler(handler),
            )
            response = client.get("/test")

        assert response.status_code == 200
        assert call_count == 2

    @patch("specli.client.sync_client.time.sleep")
    def test_max_retries_exhausted_raises_connection_error(
        self, mock_sleep: MagicMock
    ) -> None:
        profile = _make_profile(max_retries=2)

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused")

        with SyncClient(profile) as client:
            client._client = httpx.Client(
                base_url="https://api.example.com",
                transport=_transport_from_handler(handler),
            )
            with pytest.raises(ConnectionError_, match="Connection failed after 3 attempts"):
                client.get("/test")

    @patch("specli.client.sync_client.time.sleep")
    def test_retry_on_timeout(self, mock_sleep: MagicMock) -> None:
        profile = _make_profile(max_retries=1)
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise httpx.ReadTimeout("Read timed out")
            return httpx.Response(200, json={"ok": True})

        with SyncClient(profile) as client:
            client._client = httpx.Client(
                base_url="https://api.example.com",
                transport=_transport_from_handler(handler),
            )
            response = client.get("/test")

        assert response.status_code == 200
        assert call_count == 2


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


class TestErrorMapping:
    def test_401_raises_auth_error(self) -> None:
        profile = _make_profile(max_retries=0)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"message": "Unauthorized"})

        with SyncClient(profile) as client:
            client._client = httpx.Client(
                base_url="https://api.example.com",
                transport=_transport_from_handler(handler),
            )
            with pytest.raises(AuthError, match="401"):
                client.get("/protected")

    def test_403_raises_auth_error(self) -> None:
        profile = _make_profile(max_retries=0)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"error": "Forbidden"})

        with SyncClient(profile) as client:
            client._client = httpx.Client(
                base_url="https://api.example.com",
                transport=_transport_from_handler(handler),
            )
            with pytest.raises(AuthError, match="403"):
                client.get("/admin")

    def test_404_raises_not_found_error(self) -> None:
        profile = _make_profile(max_retries=0)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"detail": "Resource not found"})

        with SyncClient(profile) as client:
            client._client = httpx.Client(
                base_url="https://api.example.com",
                transport=_transport_from_handler(handler),
            )
            with pytest.raises(NotFoundError, match="404"):
                client.get("/nonexistent")

    def test_500_raises_server_error(self) -> None:
        profile = _make_profile(max_retries=0)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "Internal Server Error"})

        with SyncClient(profile) as client:
            client._client = httpx.Client(
                base_url="https://api.example.com",
                transport=_transport_from_handler(handler),
            )
            with pytest.raises(ServerError, match="500"):
                client.get("/broken")

    def test_error_message_extracted_from_json(self) -> None:
        profile = _make_profile(max_retries=0)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"message": "User not found"})

        with SyncClient(profile) as client:
            client._client = httpx.Client(
                base_url="https://api.example.com",
                transport=_transport_from_handler(handler),
            )
            with pytest.raises(NotFoundError, match="User not found"):
                client.get("/users/999")

    def test_error_with_plain_text_body(self) -> None:
        profile = _make_profile(max_retries=0)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                502, text="Bad Gateway", headers={"content-type": "text/plain"}
            )

        with SyncClient(profile) as client:
            client._client = httpx.Client(
                base_url="https://api.example.com",
                transport=_transport_from_handler(handler),
            )
            with pytest.raises(ServerError, match="502"):
                client.get("/test")

    def test_2xx_does_not_raise(self) -> None:
        profile = _make_profile(max_retries=0)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True})

        with SyncClient(profile) as client:
            client._client = httpx.Client(
                base_url="https://api.example.com",
                transport=_transport_from_handler(handler),
            )
            response = client.get("/test")
            assert response.status_code == 200

    def test_3xx_does_not_raise(self) -> None:
        profile = _make_profile(max_retries=0)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(301, headers={"location": "/new-location"})

        with SyncClient(profile) as client:
            client._client = httpx.Client(
                base_url="https://api.example.com",
                transport=_transport_from_handler(handler),
            )
            response = client.get("/old")
            assert response.status_code == 301

    def test_422_raises_server_error(self) -> None:
        """Other 4xx errors are raised as ServerError."""
        profile = _make_profile(max_retries=0)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(422, json={"detail": "Validation Error"})

        with SyncClient(profile) as client:
            client._client = httpx.Client(
                base_url="https://api.example.com",
                transport=_transport_from_handler(handler),
            )
            with pytest.raises(ServerError, match="422"):
                client.post("/items")


# ---------------------------------------------------------------------------
# Plugin hooks
# ---------------------------------------------------------------------------


class TestPluginPreRequestHook:
    def test_pre_request_hook_modifies_headers(self) -> None:
        profile = _make_profile(max_retries=0)

        # Create a mock plugin that adds a custom header
        mock_plugin = MagicMock()
        mock_plugin.on_pre_request.return_value = {
            "headers": {"X-Plugin-Added": "yes", "X-Original": "kept"},
            "params": {},
        }
        hook_runner = HookRunner([mock_plugin])

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers.get("x-plugin-added") == "yes"
            return httpx.Response(200, json={"ok": True})

        with SyncClient(profile, hook_runner=hook_runner) as client:
            client._client = httpx.Client(
                base_url="https://api.example.com",
                transport=_transport_from_handler(handler),
            )
            response = client.get("/test")
            assert response.status_code == 200

    def test_pre_request_hook_modifies_params(self) -> None:
        profile = _make_profile(max_retries=0)

        mock_plugin = MagicMock()
        mock_plugin.on_pre_request.return_value = {
            "headers": {},
            "params": {"injected": "true"},
        }
        hook_runner = HookRunner([mock_plugin])

        def handler(request: httpx.Request) -> httpx.Response:
            assert "injected=true" in str(request.url)
            return httpx.Response(200, json={"ok": True})

        with SyncClient(profile, hook_runner=hook_runner) as client:
            client._client = httpx.Client(
                base_url="https://api.example.com",
                transport=_transport_from_handler(handler),
            )
            response = client.get("/test")
            assert response.status_code == 200


class TestPluginPostResponseHook:
    def test_post_response_hook_receives_context(self) -> None:
        profile = _make_profile(max_retries=0)
        captured_ctx: list[Any] = []

        mock_plugin = MagicMock()

        def capture_response(status_code, headers, body):
            captured_ctx.append(
                {"status_code": status_code, "body": body}
            )
            return body

        mock_plugin.on_pre_request.return_value = {
            "headers": {},
            "params": {},
        }
        mock_plugin.on_post_response.side_effect = capture_response
        hook_runner = HookRunner([mock_plugin])

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": "test"})

        with SyncClient(profile, hook_runner=hook_runner) as client:
            client._client = httpx.Client(
                base_url="https://api.example.com",
                transport=_transport_from_handler(handler),
            )
            response = client.get("/test")

        assert response.status_code == 200
        assert len(captured_ctx) == 1
        assert captured_ctx[0]["status_code"] == 200
        assert captured_ctx[0]["body"] == {"data": "test"}


class TestPluginErrorHook:
    @patch("specli.client.sync_client.time.sleep")
    def test_error_hook_called_on_connection_failure(self, mock_sleep: MagicMock) -> None:
        profile = _make_profile(max_retries=0)

        mock_plugin = MagicMock()
        mock_plugin.on_pre_request.return_value = {"headers": {}, "params": {}}
        hook_runner = HookRunner([mock_plugin])

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused")

        with SyncClient(profile, hook_runner=hook_runner) as client:
            client._client = httpx.Client(
                base_url="https://api.example.com",
                transport=_transport_from_handler(handler),
            )
            with pytest.raises(ConnectionError_):
                client.get("/test")

        mock_plugin.on_error.assert_called_once()

    def test_error_hook_called_on_http_error(self) -> None:
        profile = _make_profile(max_retries=0)

        mock_plugin = MagicMock()
        mock_plugin.on_pre_request.return_value = {"headers": {}, "params": {}}
        mock_plugin.on_post_response.side_effect = lambda s, h, b: b
        hook_runner = HookRunner([mock_plugin])

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"message": "Unauthorized"})

        with SyncClient(profile, hook_runner=hook_runner) as client:
            client._client = httpx.Client(
                base_url="https://api.example.com",
                transport=_transport_from_handler(handler),
            )
            with pytest.raises(AuthError):
                client.get("/protected")

        mock_plugin.on_error.assert_called_once()


# ---------------------------------------------------------------------------
# Convenience methods
# ---------------------------------------------------------------------------


class TestConvenienceMethods:
    def test_put(self) -> None:
        profile = _make_profile(max_retries=0)

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "PUT"
            return httpx.Response(200, json={"updated": True})

        with SyncClient(profile) as client:
            client._client = httpx.Client(
                base_url="https://api.example.com",
                transport=_transport_from_handler(handler),
            )
            response = client.put("/items/1", json_body={"name": "updated"})
            assert response.status_code == 200

    def test_patch(self) -> None:
        profile = _make_profile(max_retries=0)

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "PATCH"
            return httpx.Response(200, json={"patched": True})

        with SyncClient(profile) as client:
            client._client = httpx.Client(
                base_url="https://api.example.com",
                transport=_transport_from_handler(handler),
            )
            response = client.patch("/items/1", json_body={"name": "patched"})
            assert response.status_code == 200

    def test_delete(self) -> None:
        profile = _make_profile(max_retries=0)

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "DELETE"
            return httpx.Response(204)

        with SyncClient(profile) as client:
            client._client = httpx.Client(
                base_url="https://api.example.com",
                transport=_transport_from_handler(handler),
            )
            response = client.delete("/items/1")
            assert response.status_code == 204
