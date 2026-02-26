"""Tests for OAuth2 and OpenID Connect auth plugins."""

from __future__ import annotations

import base64
import hashlib
import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

from specli.auth.base import AuthResult
from specli.auth.manager import create_default_manager
from specli.plugins.oauth2_auth_code import (
    OAuth2AuthCodePlugin,
    generate_pkce_pair,
)
from specli.plugins.oauth2_client_credentials import (
    OAuth2ClientCredentialsPlugin,
)
from specli.plugins.openid_connect import OpenIDConnectPlugin
from specli.exceptions import AuthError
from specli.models import AuthConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_auth_config(**kwargs: object) -> AuthConfig:
    """Build an AuthConfig with sensible defaults overridden by kwargs."""
    defaults: dict[str, object] = {"type": "oauth2_client_credentials", "source": "prompt"}
    defaults.update(kwargs)
    return AuthConfig(**defaults)  # type: ignore[arg-type]


def _make_token_response(
    access_token: str = "test-access-token",
    expires_in: int = 3600,
    refresh_token: str | None = None,
    token_type: str = "Bearer",
) -> dict[str, object]:
    """Build a mock token endpoint JSON response."""
    data: dict[str, object] = {
        "access_token": access_token,
        "expires_in": expires_in,
        "token_type": token_type,
    }
    if refresh_token is not None:
        data["refresh_token"] = refresh_token
    return data


def _mock_httpx_post(
    token_response: dict[str, object] | None = None,
    status_code: int = 200,
) -> MagicMock:
    """Create a mock for httpx.post that returns a token response."""
    if token_response is None:
        token_response = _make_token_response()

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


def _mock_httpx_get(
    json_response: dict[str, object] | None = None,
    status_code: int = 200,
) -> MagicMock:
    """Create a mock for httpx.get that returns a JSON response."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = status_code
    mock_response.json.return_value = json_response or {}
    mock_response.text = str(json_response)

    if status_code >= 400:
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            message=f"HTTP {status_code}",
            request=MagicMock(),
            response=mock_response,
        )
    else:
        mock_response.raise_for_status.return_value = None

    return mock_response


# ---------------------------------------------------------------------------
# OAuth2ClientCredentialsPlugin
# ---------------------------------------------------------------------------


class TestOAuth2ClientCredentialsPlugin:
    def test_auth_type(self) -> None:
        plugin = OAuth2ClientCredentialsPlugin()
        assert plugin.auth_type == "oauth2_client_credentials"

    def test_successful_token_fetch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that authenticate() posts to token_url and returns Bearer header."""
        monkeypatch.setenv("CLIENT_ID", "my-client-id")
        monkeypatch.setenv("CLIENT_SECRET", "my-client-secret")

        config = _make_auth_config(
            type="oauth2_client_credentials",
            token_url="https://auth.example.com/token",
            client_id_source="env:CLIENT_ID",
            client_secret_source="env:CLIENT_SECRET",
        )

        mock_resp = _mock_httpx_post(_make_token_response(access_token="fetched-token"))

        with patch("specli.plugins.oauth2_client_credentials.plugin.httpx.post", return_value=mock_resp) as mock_post:
            plugin = OAuth2ClientCredentialsPlugin()
            result = plugin.authenticate(config)

        assert result.headers == {"Authorization": "Bearer fetched-token"}
        assert result.params == {}
        assert result.cookies == {}

        # Verify the POST was called with correct parameters
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert call_kwargs.args[0] == "https://auth.example.com/token"
        posted_data = call_kwargs.kwargs["data"]
        assert posted_data["grant_type"] == "client_credentials"
        assert posted_data["client_id"] == "my-client-id"
        assert posted_data["client_secret"] == "my-client-secret"

    def test_token_caching_avoids_second_fetch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Second call should return cached token without hitting the endpoint."""
        monkeypatch.setenv("CLIENT_ID", "cid")
        monkeypatch.setenv("CLIENT_SECRET", "csecret")

        config = _make_auth_config(
            type="oauth2_client_credentials",
            token_url="https://auth.example.com/token",
            client_id_source="env:CLIENT_ID",
            client_secret_source="env:CLIENT_SECRET",
        )

        mock_resp = _mock_httpx_post(_make_token_response(access_token="cached-token", expires_in=3600))

        with patch("specli.plugins.oauth2_client_credentials.plugin.httpx.post", return_value=mock_resp) as mock_post:
            plugin = OAuth2ClientCredentialsPlugin()
            result1 = plugin.authenticate(config)
            result2 = plugin.authenticate(config)

        # Only one HTTP call should have been made
        assert mock_post.call_count == 1
        assert result1.headers == {"Authorization": "Bearer cached-token"}
        assert result2.headers == {"Authorization": "Bearer cached-token"}

    def test_token_refresh_fetches_new_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """refresh() should clear cache and fetch a new token."""
        monkeypatch.setenv("CLIENT_ID", "cid")
        monkeypatch.setenv("CLIENT_SECRET", "csecret")

        config = _make_auth_config(
            type="oauth2_client_credentials",
            token_url="https://auth.example.com/token",
            client_id_source="env:CLIENT_ID",
            client_secret_source="env:CLIENT_SECRET",
        )

        mock_resp1 = _mock_httpx_post(_make_token_response(access_token="token-1"))
        mock_resp2 = _mock_httpx_post(_make_token_response(access_token="token-2"))

        plugin = OAuth2ClientCredentialsPlugin()

        with patch("specli.plugins.oauth2_client_credentials.plugin.httpx.post", return_value=mock_resp1):
            result1 = plugin.authenticate(config)

        with patch("specli.plugins.oauth2_client_credentials.plugin.httpx.post", return_value=mock_resp2):
            result2 = plugin.refresh(config)

        assert result1.headers == {"Authorization": "Bearer token-1"}
        assert result2.headers == {"Authorization": "Bearer token-2"}

    def test_expired_token_refetches(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When the cached token is expired, authenticate() should fetch a new one."""
        monkeypatch.setenv("CLIENT_ID", "cid")
        monkeypatch.setenv("CLIENT_SECRET", "csecret")

        config = _make_auth_config(
            type="oauth2_client_credentials",
            token_url="https://auth.example.com/token",
            client_id_source="env:CLIENT_ID",
            client_secret_source="env:CLIENT_SECRET",
        )

        mock_resp1 = _mock_httpx_post(_make_token_response(access_token="old-token", expires_in=1))
        mock_resp2 = _mock_httpx_post(_make_token_response(access_token="new-token", expires_in=3600))

        plugin = OAuth2ClientCredentialsPlugin()

        with patch("specli.plugins.oauth2_client_credentials.plugin.httpx.post", return_value=mock_resp1):
            result1 = plugin.authenticate(config)
        assert result1.headers == {"Authorization": "Bearer old-token"}

        # Simulate expiry by backdating the token_expiry
        plugin._token_expiry = time.monotonic() - 10

        with patch("specli.plugins.oauth2_client_credentials.plugin.httpx.post", return_value=mock_resp2):
            result2 = plugin.authenticate(config)
        assert result2.headers == {"Authorization": "Bearer new-token"}

    def test_with_scopes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Scopes should be sent as space-separated 'scope' parameter."""
        monkeypatch.setenv("CLIENT_ID", "cid")
        monkeypatch.setenv("CLIENT_SECRET", "csecret")

        config = _make_auth_config(
            type="oauth2_client_credentials",
            token_url="https://auth.example.com/token",
            client_id_source="env:CLIENT_ID",
            client_secret_source="env:CLIENT_SECRET",
            scopes=["read", "write", "admin"],
        )

        mock_resp = _mock_httpx_post(_make_token_response())

        with patch("specli.plugins.oauth2_client_credentials.plugin.httpx.post", return_value=mock_resp) as mock_post:
            plugin = OAuth2ClientCredentialsPlugin()
            plugin.authenticate(config)

        posted_data = mock_post.call_args.kwargs["data"]
        assert posted_data["scope"] == "read write admin"

    def test_no_scopes_omits_scope_param(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When scopes is empty, 'scope' should not be in the posted data."""
        monkeypatch.setenv("CLIENT_ID", "cid")
        monkeypatch.setenv("CLIENT_SECRET", "csecret")

        config = _make_auth_config(
            type="oauth2_client_credentials",
            token_url="https://auth.example.com/token",
            client_id_source="env:CLIENT_ID",
            client_secret_source="env:CLIENT_SECRET",
            scopes=[],
        )

        mock_resp = _mock_httpx_post(_make_token_response())

        with patch("specli.plugins.oauth2_client_credentials.plugin.httpx.post", return_value=mock_resp) as mock_post:
            plugin = OAuth2ClientCredentialsPlugin()
            plugin.authenticate(config)

        posted_data = mock_post.call_args.kwargs["data"]
        assert "scope" not in posted_data

    def test_validate_config_missing_token_url(self) -> None:
        config = _make_auth_config(
            type="oauth2_client_credentials",
            token_url=None,
            client_id_source="env:CID",
            client_secret_source="env:CS",
        )
        errors = OAuth2ClientCredentialsPlugin().validate_config(config)
        assert any("token_url" in e for e in errors)

    def test_validate_config_missing_client_id_source(self) -> None:
        config = _make_auth_config(
            type="oauth2_client_credentials",
            token_url="https://auth.example.com/token",
            client_id_source=None,
            client_secret_source="env:CS",
        )
        errors = OAuth2ClientCredentialsPlugin().validate_config(config)
        assert any("client_id_source" in e for e in errors)

    def test_validate_config_missing_client_secret_source(self) -> None:
        config = _make_auth_config(
            type="oauth2_client_credentials",
            token_url="https://auth.example.com/token",
            client_id_source="env:CID",
            client_secret_source=None,
        )
        errors = OAuth2ClientCredentialsPlugin().validate_config(config)
        assert any("client_secret_source" in e for e in errors)

    def test_validate_config_all_missing(self) -> None:
        config = _make_auth_config(
            type="oauth2_client_credentials",
            token_url=None,
            client_id_source=None,
            client_secret_source=None,
        )
        errors = OAuth2ClientCredentialsPlugin().validate_config(config)
        assert len(errors) == 3

    def test_validate_config_valid(self) -> None:
        config = _make_auth_config(
            type="oauth2_client_credentials",
            token_url="https://auth.example.com/token",
            client_id_source="env:CID",
            client_secret_source="env:CS",
        )
        errors = OAuth2ClientCredentialsPlugin().validate_config(config)
        assert errors == []

    def test_http_error_raises_auth_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HTTP errors during token fetch should raise AuthError."""
        monkeypatch.setenv("CLIENT_ID", "cid")
        monkeypatch.setenv("CLIENT_SECRET", "csecret")

        config = _make_auth_config(
            type="oauth2_client_credentials",
            token_url="https://auth.example.com/token",
            client_id_source="env:CLIENT_ID",
            client_secret_source="env:CLIENT_SECRET",
        )

        mock_resp = _mock_httpx_post(status_code=401)

        with patch("specli.plugins.oauth2_client_credentials.plugin.httpx.post", return_value=mock_resp):
            plugin = OAuth2ClientCredentialsPlugin()
            with pytest.raises(AuthError, match="401"):
                plugin.authenticate(config)

    def test_missing_access_token_in_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A response without access_token should raise AuthError."""
        monkeypatch.setenv("CLIENT_ID", "cid")
        monkeypatch.setenv("CLIENT_SECRET", "csecret")

        config = _make_auth_config(
            type="oauth2_client_credentials",
            token_url="https://auth.example.com/token",
            client_id_source="env:CLIENT_ID",
            client_secret_source="env:CLIENT_SECRET",
        )

        mock_resp = _mock_httpx_post({"error": "invalid_client"})

        with patch("specli.plugins.oauth2_client_credentials.plugin.httpx.post", return_value=mock_resp):
            plugin = OAuth2ClientCredentialsPlugin()
            with pytest.raises(AuthError, match="access_token"):
                plugin.authenticate(config)

    def test_no_expires_in_defaults_to_one_hour(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When the token response omits expires_in, default to 1 hour expiry."""
        monkeypatch.setenv("CLIENT_ID", "cid")
        monkeypatch.setenv("CLIENT_SECRET", "csecret")

        config = _make_auth_config(
            type="oauth2_client_credentials",
            token_url="https://auth.example.com/token",
            client_id_source="env:CLIENT_ID",
            client_secret_source="env:CLIENT_SECRET",
        )

        token_resp = {"access_token": "no-expiry-token", "token_type": "Bearer"}
        mock_resp = _mock_httpx_post(token_resp)

        with patch("specli.plugins.oauth2_client_credentials.plugin.httpx.post", return_value=mock_resp):
            plugin = OAuth2ClientCredentialsPlugin()
            result = plugin.authenticate(config)

        assert result.headers == {"Authorization": "Bearer no-expiry-token"}
        # Token should be cached with ~1 hour expiry
        assert plugin._token_expiry > time.monotonic() + 3500


# ---------------------------------------------------------------------------
# OAuth2AuthCodePlugin -- PKCE generation
# ---------------------------------------------------------------------------


class TestPKCEGeneration:
    def test_code_verifier_length(self) -> None:
        """Code verifier should be between 43 and 128 characters (RFC 7636)."""
        verifier, _ = generate_pkce_pair()
        assert 43 <= len(verifier) <= 128

    def test_code_challenge_is_s256(self) -> None:
        """Code challenge should be the S256 hash of the verifier."""
        verifier, challenge = generate_pkce_pair()
        expected_digest = hashlib.sha256(verifier.encode("ascii")).digest()
        expected_challenge = base64.urlsafe_b64encode(expected_digest).rstrip(b"=").decode("ascii")
        assert challenge == expected_challenge

    def test_code_verifier_is_unique(self) -> None:
        """Each call should produce a unique verifier."""
        pairs = [generate_pkce_pair() for _ in range(10)]
        verifiers = [v for v, _ in pairs]
        assert len(set(verifiers)) == 10

    def test_code_verifier_url_safe(self) -> None:
        """Verifier should only contain URL-safe characters."""
        verifier, _ = generate_pkce_pair()
        allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
        assert all(c in allowed for c in verifier)

    def test_code_challenge_has_no_padding(self) -> None:
        """S256 challenge should not have base64 padding characters."""
        _, challenge = generate_pkce_pair()
        assert "=" not in challenge


# ---------------------------------------------------------------------------
# OAuth2AuthCodePlugin
# ---------------------------------------------------------------------------


class TestOAuth2AuthCodePlugin:
    def test_auth_type(self) -> None:
        plugin = OAuth2AuthCodePlugin()
        assert plugin.auth_type == "oauth2_auth_code"

    def test_validate_config_missing_authorization_url(self) -> None:
        config = _make_auth_config(
            type="oauth2_auth_code",
            authorization_url=None,
            token_url="https://auth.example.com/token",
        )
        errors = OAuth2AuthCodePlugin().validate_config(config)
        assert any("authorization_url" in e for e in errors)

    def test_validate_config_missing_token_url(self) -> None:
        config = _make_auth_config(
            type="oauth2_auth_code",
            authorization_url="https://auth.example.com/authorize",
            token_url=None,
        )
        errors = OAuth2AuthCodePlugin().validate_config(config)
        assert any("token_url" in e for e in errors)

    def test_validate_config_both_missing(self) -> None:
        config = _make_auth_config(
            type="oauth2_auth_code",
            authorization_url=None,
            token_url=None,
        )
        errors = OAuth2AuthCodePlugin().validate_config(config)
        assert len(errors) == 2

    def test_validate_config_valid(self) -> None:
        config = _make_auth_config(
            type="oauth2_auth_code",
            authorization_url="https://auth.example.com/authorize",
            token_url="https://auth.example.com/token",
        )
        errors = OAuth2AuthCodePlugin().validate_config(config)
        assert errors == []

    def test_login_interactive_raises_when_not_tty(self) -> None:
        """login_interactive should raise AuthError when stdin is not a TTY."""
        config = _make_auth_config(
            type="oauth2_auth_code",
            authorization_url="https://auth.example.com/authorize",
            token_url="https://auth.example.com/token",
        )

        plugin = OAuth2AuthCodePlugin()
        with patch("specli.plugins.oauth2_auth_code.plugin.sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = False
            with pytest.raises(AuthError, match="interactive terminal"):
                plugin.login_interactive(config)

    def test_authenticate_uses_cached_token(self) -> None:
        """When a valid cached token exists, authenticate returns it directly."""
        plugin = OAuth2AuthCodePlugin()
        plugin._cached_token = "cached-auth-code-token"
        plugin._token_expiry = time.monotonic() + 3600

        config = _make_auth_config(
            type="oauth2_auth_code",
            authorization_url="https://auth.example.com/authorize",
            token_url="https://auth.example.com/token",
        )

        result = plugin.authenticate(config)
        assert result.headers == {"Authorization": "Bearer cached-auth-code-token"}

    def test_authenticate_tries_refresh_when_expired(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When cached token is expired but refresh_token exists, try refreshing."""
        monkeypatch.setenv("CLIENT_ID", "cid")

        plugin = OAuth2AuthCodePlugin()
        plugin._cached_token = "expired-token"
        plugin._token_expiry = time.monotonic() - 100
        plugin._refresh_token = "my-refresh-token"

        config = _make_auth_config(
            type="oauth2_auth_code",
            authorization_url="https://auth.example.com/authorize",
            token_url="https://auth.example.com/token",
            client_id_source="env:CLIENT_ID",
        )

        mock_resp = _mock_httpx_post(
            _make_token_response(access_token="refreshed-token", refresh_token="new-refresh")
        )

        with patch("specli.plugins.oauth2_auth_code.plugin.httpx.post", return_value=mock_resp) as mock_post:
            result = plugin.authenticate(config)

        assert result.headers == {"Authorization": "Bearer refreshed-token"}
        # Should have posted with grant_type=refresh_token
        posted_data = mock_post.call_args.kwargs["data"]
        assert posted_data["grant_type"] == "refresh_token"

    def test_refresh_with_refresh_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """refresh() should use refresh_token grant when available."""
        monkeypatch.setenv("CLIENT_ID", "cid")
        monkeypatch.setenv("CLIENT_SECRET", "csecret")

        plugin = OAuth2AuthCodePlugin()
        plugin._refresh_token = "stored-refresh-token"

        config = _make_auth_config(
            type="oauth2_auth_code",
            authorization_url="https://auth.example.com/authorize",
            token_url="https://auth.example.com/token",
            client_id_source="env:CLIENT_ID",
            client_secret_source="env:CLIENT_SECRET",
        )

        mock_resp = _mock_httpx_post(
            _make_token_response(access_token="new-token-via-refresh")
        )

        with patch("specli.plugins.oauth2_auth_code.plugin.httpx.post", return_value=mock_resp) as mock_post:
            result = plugin.refresh(config)

        assert result.headers == {"Authorization": "Bearer new-token-via-refresh"}
        posted_data = mock_post.call_args.kwargs["data"]
        assert posted_data["grant_type"] == "refresh_token"
        assert posted_data["refresh_token"] == "stored-refresh-token"
        assert posted_data["client_id"] == "cid"
        assert posted_data["client_secret"] == "csecret"

    def test_exchange_code_http_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HTTP error during code exchange should raise AuthError."""
        monkeypatch.setenv("CLIENT_ID", "cid")

        plugin = OAuth2AuthCodePlugin()
        config = _make_auth_config(
            type="oauth2_auth_code",
            token_url="https://auth.example.com/token",
            client_id_source="env:CLIENT_ID",
        )

        mock_resp = _mock_httpx_post(status_code=400)

        with patch("specli.plugins.oauth2_auth_code.plugin.httpx.post", return_value=mock_resp):
            with pytest.raises(AuthError, match="Token exchange failed"):
                plugin._exchange_code(config, "auth-code-123", "verifier", "http://localhost:9999/callback")

    def test_exchange_code_missing_access_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Response without access_token should raise AuthError."""
        monkeypatch.setenv("CLIENT_ID", "cid")

        plugin = OAuth2AuthCodePlugin()
        config = _make_auth_config(
            type="oauth2_auth_code",
            token_url="https://auth.example.com/token",
            client_id_source="env:CLIENT_ID",
        )

        mock_resp = _mock_httpx_post({"error": "bad_code"})

        with patch("specli.plugins.oauth2_auth_code.plugin.httpx.post", return_value=mock_resp):
            with pytest.raises(AuthError, match="access_token"):
                plugin._exchange_code(config, "bad-code", "verifier", "http://localhost:9999/callback")


# ---------------------------------------------------------------------------
# OpenIDConnectPlugin
# ---------------------------------------------------------------------------


DISCOVERY_DOC = {
    "issuer": "https://accounts.example.com",
    "authorization_endpoint": "https://accounts.example.com/o/authorize",
    "token_endpoint": "https://accounts.example.com/o/token",
    "userinfo_endpoint": "https://accounts.example.com/o/userinfo",
    "jwks_uri": "https://accounts.example.com/.well-known/jwks.json",
    "response_types_supported": ["code"],
    "subject_types_supported": ["public"],
    "id_token_signing_alg_values_supported": ["RS256"],
}


class TestOpenIDConnectPlugin:
    def test_auth_type(self) -> None:
        plugin = OpenIDConnectPlugin()
        assert plugin.auth_type == "openid_connect"

    def test_validate_config_missing_openid_connect_url(self) -> None:
        config = _make_auth_config(
            type="openid_connect",
            openid_connect_url=None,
        )
        errors = OpenIDConnectPlugin().validate_config(config)
        assert any("openid_connect_url" in e for e in errors)

    def test_validate_config_valid(self) -> None:
        config = _make_auth_config(
            type="openid_connect",
            openid_connect_url="https://accounts.example.com/.well-known/openid-configuration",
        )
        errors = OpenIDConnectPlugin().validate_config(config)
        assert errors == []

    def test_discover_fetches_document(self) -> None:
        """discover() should fetch and parse the OpenID discovery document."""
        mock_resp = _mock_httpx_get(DISCOVERY_DOC)

        plugin = OpenIDConnectPlugin()

        with patch("specli.plugins.openid_connect.plugin.httpx.get", return_value=mock_resp) as mock_get:
            doc = plugin.discover("https://accounts.example.com/.well-known/openid-configuration")

        mock_get.assert_called_once_with(
            "https://accounts.example.com/.well-known/openid-configuration",
            headers={"Accept": "application/json"},
            timeout=30.0,
        )
        assert doc["authorization_endpoint"] == "https://accounts.example.com/o/authorize"
        assert doc["token_endpoint"] == "https://accounts.example.com/o/token"

    def test_discover_extracts_endpoints(self) -> None:
        """The resolved config should have authorization_url and token_url from discovery."""
        mock_resp = _mock_httpx_get(DISCOVERY_DOC)

        plugin = OpenIDConnectPlugin()
        config = _make_auth_config(
            type="openid_connect",
            openid_connect_url="https://accounts.example.com/.well-known/openid-configuration",
        )

        with patch("specli.plugins.openid_connect.plugin.httpx.get", return_value=mock_resp):
            resolved = plugin._resolve_config(config)

        assert resolved.authorization_url == "https://accounts.example.com/o/authorize"
        assert resolved.token_url == "https://accounts.example.com/o/token"

    def test_discover_caches_result(self) -> None:
        """Subsequent calls to _resolve_config should not re-fetch the discovery document."""
        mock_resp = _mock_httpx_get(DISCOVERY_DOC)

        plugin = OpenIDConnectPlugin()
        config = _make_auth_config(
            type="openid_connect",
            openid_connect_url="https://accounts.example.com/.well-known/openid-configuration",
        )

        with patch("specli.plugins.openid_connect.plugin.httpx.get", return_value=mock_resp) as mock_get:
            plugin._resolve_config(config)
            plugin._resolve_config(config)

        assert mock_get.call_count == 1

    def test_discover_user_config_takes_precedence(self) -> None:
        """User-set authorization_url and token_url should override discovered values."""
        mock_resp = _mock_httpx_get(DISCOVERY_DOC)

        plugin = OpenIDConnectPlugin()
        config = _make_auth_config(
            type="openid_connect",
            openid_connect_url="https://accounts.example.com/.well-known/openid-configuration",
            authorization_url="https://custom.example.com/authorize",
            token_url="https://custom.example.com/token",
        )

        with patch("specli.plugins.openid_connect.plugin.httpx.get", return_value=mock_resp):
            resolved = plugin._resolve_config(config)

        assert resolved.authorization_url == "https://custom.example.com/authorize"
        assert resolved.token_url == "https://custom.example.com/token"

    def test_discover_http_error_raises_auth_error(self) -> None:
        """HTTP error fetching discovery document should raise AuthError."""
        mock_resp = _mock_httpx_get(status_code=404)

        plugin = OpenIDConnectPlugin()

        with patch("specli.plugins.openid_connect.plugin.httpx.get", return_value=mock_resp):
            with pytest.raises(AuthError, match="404"):
                plugin.discover("https://bad.example.com/.well-known/openid-configuration")

    def test_discover_missing_authorization_endpoint(self) -> None:
        """Discovery document without authorization_endpoint should raise AuthError."""
        incomplete_doc = {"token_endpoint": "https://example.com/token"}
        mock_resp = _mock_httpx_get(incomplete_doc)

        plugin = OpenIDConnectPlugin()

        with patch("specli.plugins.openid_connect.plugin.httpx.get", return_value=mock_resp):
            with pytest.raises(AuthError, match="authorization_endpoint"):
                plugin.discover("https://example.com/.well-known/openid-configuration")

    def test_discover_missing_token_endpoint(self) -> None:
        """Discovery document without token_endpoint should raise AuthError."""
        incomplete_doc = {"authorization_endpoint": "https://example.com/authorize"}
        mock_resp = _mock_httpx_get(incomplete_doc)

        plugin = OpenIDConnectPlugin()

        with patch("specli.plugins.openid_connect.plugin.httpx.get", return_value=mock_resp):
            with pytest.raises(AuthError, match="token_endpoint"):
                plugin.discover("https://example.com/.well-known/openid-configuration")

    def test_resolve_config_without_url_raises(self) -> None:
        """_resolve_config should raise AuthError when openid_connect_url is missing."""
        config = _make_auth_config(
            type="openid_connect",
            openid_connect_url=None,
        )
        plugin = OpenIDConnectPlugin()
        with pytest.raises(AuthError, match="openid_connect_url"):
            plugin._resolve_config(config)

    def test_authenticate_delegates_to_auth_code_plugin(self) -> None:
        """authenticate() should resolve config then delegate to the auth code plugin."""
        mock_discovery_resp = _mock_httpx_get(DISCOVERY_DOC)

        plugin = OpenIDConnectPlugin()
        config = _make_auth_config(
            type="openid_connect",
            openid_connect_url="https://accounts.example.com/.well-known/openid-configuration",
        )

        # Pre-populate the inner auth_code_plugin with a cached token
        plugin._auth_code_plugin._cached_token = "oidc-token"
        plugin._auth_code_plugin._token_expiry = time.monotonic() + 3600

        with patch("specli.plugins.openid_connect.plugin.httpx.get", return_value=mock_discovery_resp):
            result = plugin.authenticate(config)

        assert result.headers == {"Authorization": "Bearer oidc-token"}

    def test_refresh_delegates_to_auth_code_plugin(self) -> None:
        """refresh() should resolve config then delegate to the auth code plugin's refresh."""
        mock_discovery_resp = _mock_httpx_get(DISCOVERY_DOC)
        mock_token_resp = _mock_httpx_post(
            _make_token_response(access_token="refreshed-oidc-token")
        )

        plugin = OpenIDConnectPlugin()
        plugin._auth_code_plugin._refresh_token = "oidc-refresh-token"

        config = _make_auth_config(
            type="openid_connect",
            openid_connect_url="https://accounts.example.com/.well-known/openid-configuration",
        )

        with patch("specli.plugins.openid_connect.plugin.httpx.get", return_value=mock_discovery_resp):
            with patch("specli.plugins.oauth2_auth_code.plugin.httpx.post", return_value=mock_token_resp):
                result = plugin.refresh(config)

        assert result.headers == {"Authorization": "Bearer refreshed-oidc-token"}


# ---------------------------------------------------------------------------
# create_default_manager -- updated registry
# ---------------------------------------------------------------------------


class TestCreateDefaultManagerWithOAuth2:
    def test_has_all_ten_types(self) -> None:
        manager = create_default_manager()
        types = manager.list_types()
        assert len(types) == 10
        assert "api_key" in types
        assert "api_key_gen" in types
        assert "basic" in types
        assert "bearer" in types
        assert "browser_login" in types
        assert "device_code" in types
        assert "manual_token" in types
        assert "oauth2_client_credentials" in types
        assert "oauth2_auth_code" in types
        assert "openid_connect" in types

    def test_types_sorted_alphabetically(self) -> None:
        manager = create_default_manager()
        types = manager.list_types()
        assert types == sorted(types)

    def test_oauth2_client_credentials_plugin_type(self) -> None:
        manager = create_default_manager()
        plugin = manager.get_plugin("oauth2_client_credentials")
        assert isinstance(plugin, OAuth2ClientCredentialsPlugin)

    def test_oauth2_auth_code_plugin_type(self) -> None:
        manager = create_default_manager()
        plugin = manager.get_plugin("oauth2_auth_code")
        assert isinstance(plugin, OAuth2AuthCodePlugin)

    def test_openid_connect_plugin_type(self) -> None:
        manager = create_default_manager()
        plugin = manager.get_plugin("openid_connect")
        assert isinstance(plugin, OpenIDConnectPlugin)
