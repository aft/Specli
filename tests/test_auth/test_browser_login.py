"""Tests for the browser_login auth plugin (simple mode + OAuth mode)."""

from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from unittest.mock import MagicMock, patch

import httpx
import pytest

from specli.auth.credential_store import CredentialEntry, CredentialStore
from specli.exceptions import AuthError
from specli.models import AuthConfig
from specli.plugins.browser_login import BrowserLoginPlugin


def _make_config(**kwargs: object) -> AuthConfig:
    defaults: dict[str, object] = {
        "type": "browser_login",
        "source": "prompt",
        "login_url": "https://example.com/auth/login",
        "callback_capture": "query_param",
        "capture_name": "access_token",
        "location": "header",
        "credential_name": "Authorization",
    }
    defaults.update(kwargs)
    return AuthConfig(**defaults)  # type: ignore[arg-type]


def _make_oauth_config(**kwargs: object) -> AuthConfig:
    defaults: dict[str, object] = {
        "type": "browser_login",
        "source": "prompt",
        "authorization_url": "https://accounts.google.com/o/oauth2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "client_id_source": "env:GOOGLE_CLIENT_ID",
        "client_secret_source": "env:GOOGLE_CLIENT_SECRET",
        "scopes": ["openid", "email"],
        "location": "header",
        "credential_name": "Authorization",
        "persist": False,
    }
    defaults.update(kwargs)
    return AuthConfig(**defaults)  # type: ignore[arg-type]


def _mock_httpx_post(
    token_response: dict[str, object] | None = None,
    status_code: int = 200,
) -> MagicMock:
    """Create a mock for httpx.post that returns a token response."""
    if token_response is None:
        token_response = {"access_token": "test-token", "expires_in": 3600}

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = status_code
    mock_response.json.return_value = token_response
    mock_response.text = str(token_response)

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
def plugin() -> BrowserLoginPlugin:
    return BrowserLoginPlugin()


@pytest.fixture()
def _patch_store(tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "specli.auth.credential_store.get_data_dir",
        lambda: tmp_path,  # type: ignore[union-attr]
    )


def _simulate_callback(port: int, path: str, method: str = "GET", body: bytes = b"", headers: dict[str, str] | None = None) -> None:
    """Send a request to the local callback server."""
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path, body=body, headers=headers or {})
    conn.getresponse()
    conn.close()


# -------------------------------------------------------------------------
# Simple mode tests (backward compat)
# -------------------------------------------------------------------------


class TestBrowserLoginPlugin:
    def test_auth_type(self, plugin: BrowserLoginPlugin) -> None:
        assert plugin.auth_type == "browser_login"

    def test_no_tty_raises(self, plugin: BrowserLoginPlugin) -> None:
        config = _make_config()
        with patch("specli.plugins.browser_login.plugin.sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = False
            with pytest.raises(AuthError, match="interactive terminal"):
                plugin.authenticate(config)

    def test_missing_login_url_raises(self, plugin: BrowserLoginPlugin) -> None:
        config = _make_config(login_url=None)
        with patch("specli.plugins.browser_login.plugin.sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            with pytest.raises(AuthError, match="login_url"):
                plugin.authenticate(config)

    def test_missing_capture_name_raises(self, plugin: BrowserLoginPlugin) -> None:
        config = _make_config(capture_name=None)
        with patch("specli.plugins.browser_login.plugin.sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = True
            with pytest.raises(AuthError, match="capture_name"):
                plugin.authenticate(config)

    def test_capture_query_param(self, plugin: BrowserLoginPlugin) -> None:
        """Capture a credential from a query parameter in the callback URL."""
        config = _make_config(
            callback_capture="query_param",
            capture_name="access_token",
            location="header",
            credential_name="Authorization",
        )

        def mock_browser_login(auth_config: AuthConfig) -> str:
            return "tok_from_query"

        with patch.object(plugin, "_do_browser_login", side_effect=mock_browser_login):
            with patch("specli.plugins.browser_login.plugin.sys.stdin") as mock_stdin:
                mock_stdin.isatty.return_value = True
                result = plugin.authenticate(config)

        assert result.headers == {"Authorization": "tok_from_query"}

    def test_capture_cookie(self, plugin: BrowserLoginPlugin) -> None:
        config = _make_config(
            callback_capture="cookie",
            capture_name="session_token",
            location="cookie",
            credential_name="session_token",
        )

        with patch.object(plugin, "_do_browser_login", return_value="cookie_val"):
            with patch("specli.plugins.browser_login.plugin.sys.stdin") as mock_stdin:
                mock_stdin.isatty.return_value = True
                result = plugin.authenticate(config)

        assert result.cookies == {"session_token": "cookie_val"}

    def test_result_as_query(self, plugin: BrowserLoginPlugin) -> None:
        config = _make_config(
            location="query",
            credential_name="api_key",
        )

        with patch.object(plugin, "_do_browser_login", return_value="key123"):
            with patch("specli.plugins.browser_login.plugin.sys.stdin") as mock_stdin:
                mock_stdin.isatty.return_value = True
                result = plugin.authenticate(config)

        assert result.params == {"api_key": "key123"}

    @pytest.mark.usefixtures("_patch_store")
    def test_persist_saves_and_reuses(self, plugin: BrowserLoginPlugin) -> None:
        config = _make_config(persist=True, credential_name="session")

        # First call
        with patch.object(plugin, "_do_browser_login", return_value="persistent_tok"):
            with patch("specli.plugins.browser_login.plugin.sys.stdin") as mock_stdin:
                mock_stdin.isatty.return_value = True
                result1 = plugin.authenticate(config)

        assert result1.headers == {"session": "persistent_tok"}

        # Second call: should use stored credential
        result2 = plugin.authenticate(config)
        assert result2.headers == {"session": "persistent_tok"}

    def test_validate_valid(self, plugin: BrowserLoginPlugin) -> None:
        config = _make_config()
        assert plugin.validate_config(config) == []

    def test_validate_missing_login_url(self, plugin: BrowserLoginPlugin) -> None:
        config = _make_config(login_url=None)
        errors = plugin.validate_config(config)
        assert any("login_url" in e for e in errors)

    def test_validate_missing_capture_name(self, plugin: BrowserLoginPlugin) -> None:
        config = _make_config(capture_name=None)
        errors = plugin.validate_config(config)
        assert any("capture_name" in e for e in errors)

    def test_validate_invalid_capture(self, plugin: BrowserLoginPlugin) -> None:
        config = _make_config(callback_capture="invalid")
        errors = plugin.validate_config(config)
        assert any("callback_capture" in e for e in errors)

    def test_validate_invalid_location(self, plugin: BrowserLoginPlugin) -> None:
        config = _make_config(location="body")
        errors = plugin.validate_config(config)
        assert any("location" in e.lower() for e in errors)


class TestBrowserLoginCallbackServer:
    """Integration-style tests that spin up the local HTTP server."""

    def test_query_param_extraction(self, plugin: BrowserLoginPlugin) -> None:
        """The callback server extracts query params correctly."""
        from specli.plugins.browser_login.plugin import _find_free_port

        port = _find_free_port()

        # Don't actually open the browser
        with patch("specli.plugins.browser_login.plugin.webbrowser.open"):
            # Simulate the callback in a background thread
            def send_callback() -> None:
                import time
                time.sleep(0.3)
                _simulate_callback(port, "/callback?access_token=tok_abc123")

            t = threading.Thread(target=send_callback, daemon=True)
            t.start()

            credential = plugin._wait_for_callback(
                port, "http://example.com/login", "query_param", "access_token"
            )

        assert credential == "tok_abc123"

    def test_body_field_extraction(self, plugin: BrowserLoginPlugin) -> None:
        from specli.plugins.browser_login.plugin import _find_free_port

        port = _find_free_port()

        with patch("specli.plugins.browser_login.plugin.webbrowser.open"):
            def send_callback() -> None:
                import time
                time.sleep(0.3)
                body = json.dumps({"token": "body_tok"}).encode()
                _simulate_callback(
                    port, "/callback", method="POST", body=body,
                    headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
                )

            t = threading.Thread(target=send_callback, daemon=True)
            t.start()

            credential = plugin._wait_for_callback(
                port, "http://example.com/login", "body_field", "token"
            )

        assert credential == "body_tok"

    def test_missing_query_param_raises(self, plugin: BrowserLoginPlugin) -> None:
        from specli.plugins.browser_login.plugin import _find_free_port

        port = _find_free_port()

        with patch("specli.plugins.browser_login.plugin.webbrowser.open"):
            def send_callback() -> None:
                import time
                time.sleep(0.3)
                _simulate_callback(port, "/callback?wrong_param=val")

            t = threading.Thread(target=send_callback, daemon=True)
            t.start()

            with pytest.raises(AuthError, match="not found"):
                plugin._wait_for_callback(
                    port, "http://example.com/login", "query_param", "access_token"
                )


# -------------------------------------------------------------------------
# OAuth mode tests
# -------------------------------------------------------------------------


class TestBrowserLoginOAuthMode:
    """Tests for browser_login in OAuth mode (PKCE + code exchange)."""

    def test_oauth_mode_detected(self) -> None:
        """Config with authorization_url + token_url + client_id_source triggers OAuth mode."""
        from specli.plugins.browser_login.plugin import _is_oauth_mode

        oauth_config = _make_oauth_config()
        assert _is_oauth_mode(oauth_config) is True

        simple_config = _make_config()
        assert _is_oauth_mode(simple_config) is False

    def test_oauth_no_tty_raises(self, plugin: BrowserLoginPlugin) -> None:
        config = _make_oauth_config()
        with patch("specli.plugins.browser_login.plugin.sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = False
            with pytest.raises(AuthError, match="interactive terminal"):
                plugin.authenticate(config)

    def test_oauth_code_exchange(
        self, plugin: BrowserLoginPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OAuth mode: PKCE flow exchanges code for token."""
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")

        config = _make_oauth_config()
        mock_resp = _mock_httpx_post({
            "access_token": "oauth-access-token",
            "refresh_token": "oauth-refresh-token",
            "expires_in": 3600,
        })

        with patch.object(plugin, "_wait_for_auth_code", return_value="auth-code-123"):
            with patch("specli.plugins.browser_login.plugin.httpx.post", return_value=mock_resp) as mock_post:
                with patch("specli.plugins.browser_login.plugin.sys.stdin") as mock_stdin:
                    mock_stdin.isatty.return_value = True
                    result = plugin.authenticate(config)

        assert result.headers == {"Authorization": "Bearer oauth-access-token"}

        # Verify token exchange POST
        posted_data = mock_post.call_args.kwargs["data"]
        assert posted_data["grant_type"] == "authorization_code"
        assert posted_data["code"] == "auth-code-123"
        assert posted_data["client_id"] == "test-client-id"
        assert posted_data["client_secret"] == "test-client-secret"
        assert "code_verifier" in posted_data

    def test_oauth_result_as_cookie(
        self, plugin: BrowserLoginPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OAuth mode respects location=cookie."""
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "csecret")

        config = _make_oauth_config(location="cookie", credential_name="session")
        mock_resp = _mock_httpx_post({"access_token": "cookie-tok", "expires_in": 3600})

        with patch.object(plugin, "_wait_for_auth_code", return_value="code"):
            with patch("specli.plugins.browser_login.plugin.httpx.post", return_value=mock_resp):
                with patch("specli.plugins.browser_login.plugin.sys.stdin") as mock_stdin:
                    mock_stdin.isatty.return_value = True
                    result = plugin.authenticate(config)

        assert result.cookies == {"session": "cookie-tok"}

    def test_oauth_token_exchange_failure(
        self, plugin: BrowserLoginPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OAuth mode: HTTP error during code exchange raises AuthError."""
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "csecret")

        config = _make_oauth_config()
        mock_resp = _mock_httpx_post(status_code=400)

        with patch.object(plugin, "_wait_for_auth_code", return_value="bad-code"):
            with patch("specli.plugins.browser_login.plugin.httpx.post", return_value=mock_resp):
                with patch("specli.plugins.browser_login.plugin.sys.stdin") as mock_stdin:
                    mock_stdin.isatty.return_value = True
                    with pytest.raises(AuthError, match="Token exchange failed"):
                        plugin.authenticate(config)

    def test_oauth_missing_access_token(
        self, plugin: BrowserLoginPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OAuth mode: response without access_token raises AuthError."""
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "csecret")

        config = _make_oauth_config()
        mock_resp = _mock_httpx_post({"error": "invalid_grant"})

        with patch.object(plugin, "_wait_for_auth_code", return_value="code"):
            with patch("specli.plugins.browser_login.plugin.httpx.post", return_value=mock_resp):
                with patch("specli.plugins.browser_login.plugin.sys.stdin") as mock_stdin:
                    mock_stdin.isatty.return_value = True
                    with pytest.raises(AuthError, match="access_token"):
                        plugin.authenticate(config)

    @pytest.mark.usefixtures("_patch_store")
    def test_oauth_persist_and_reuse(
        self, plugin: BrowserLoginPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OAuth mode: persist=True saves token and reuses it."""
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "csecret")

        config = _make_oauth_config(persist=True)
        mock_resp = _mock_httpx_post({
            "access_token": "persisted-tok",
            "refresh_token": "refresh-tok",
            "expires_in": 3600,
        })

        # First call: interactive
        with patch.object(plugin, "_wait_for_auth_code", return_value="code"):
            with patch("specli.plugins.browser_login.plugin.httpx.post", return_value=mock_resp):
                with patch("specli.plugins.browser_login.plugin.sys.stdin") as mock_stdin:
                    mock_stdin.isatty.return_value = True
                    result1 = plugin.authenticate(config)

        assert result1.headers == {"Authorization": "Bearer persisted-tok"}

        # Second call: should use stored credential (no interactive flow)
        result2 = plugin.authenticate(config)
        assert result2.headers == {"Authorization": "Bearer persisted-tok"}

    @pytest.mark.usefixtures("_patch_store")
    def test_oauth_refresh_on_expired(
        self, plugin: BrowserLoginPlugin, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OAuth mode: expired token with refresh_token triggers refresh."""
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "csecret")

        config = _make_oauth_config(persist=True)

        # Manually persist an expired entry with a refresh_token
        from datetime import datetime, timezone, timedelta

        store = CredentialStore("Authorization")
        store.save(
            CredentialEntry(
                auth_type="browser_login",
                credential="expired-token",
                credential_name="access_token",
                expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
                metadata={"refresh_token": "my-refresh-token"},
            )
        )

        mock_resp = _mock_httpx_post({
            "access_token": "refreshed-token",
            "refresh_token": "new-refresh",
            "expires_in": 3600,
        })

        with patch("specli.plugins.browser_login.plugin.httpx.post", return_value=mock_resp) as mock_post:
            result = plugin.authenticate(config)

        assert result.headers == {"Authorization": "Bearer refreshed-token"}
        posted_data = mock_post.call_args.kwargs["data"]
        assert posted_data["grant_type"] == "refresh_token"
        assert posted_data["refresh_token"] == "my-refresh-token"

    def test_oauth_validate_valid(self, plugin: BrowserLoginPlugin) -> None:
        config = _make_oauth_config()
        assert plugin.validate_config(config) == []

    def test_oauth_validate_invalid_location(self, plugin: BrowserLoginPlugin) -> None:
        config = _make_oauth_config(location="body")
        errors = plugin.validate_config(config)
        assert any("location" in e.lower() for e in errors)

    def test_oauth_auth_code_callback_server(self, plugin: BrowserLoginPlugin) -> None:
        """Integration test: auth code callback server captures code correctly."""
        from specli.plugins.browser_login.plugin import _find_free_port

        port = _find_free_port()

        with patch("specli.plugins.browser_login.plugin.webbrowser.open"):
            def send_callback() -> None:
                import time
                time.sleep(0.3)
                _simulate_callback(port, "/callback?code=auth-code-xyz")

            t = threading.Thread(target=send_callback, daemon=True)
            t.start()

            code = plugin._wait_for_auth_code(port, "http://example.com/auth")

        assert code == "auth-code-xyz"

    def test_oauth_auth_code_error_callback(self, plugin: BrowserLoginPlugin) -> None:
        """Auth code callback with error raises AuthError."""
        from specli.plugins.browser_login.plugin import _find_free_port

        port = _find_free_port()

        with patch("specli.plugins.browser_login.plugin.webbrowser.open"):
            def send_callback() -> None:
                import time
                time.sleep(0.3)
                _simulate_callback(port, "/callback?error=access_denied&error_description=User+denied")

            t = threading.Thread(target=send_callback, daemon=True)
            t.start()

            with pytest.raises(AuthError, match="access_denied"):
                plugin._wait_for_auth_code(port, "http://example.com/auth")
