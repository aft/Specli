"""Synchronous HTTP client with auth, hooks, dry-run, cache, and retry.

This module provides :class:`SyncClient`, the primary blocking HTTP client
used by specli CLI commands.  It wraps :class:`httpx.Client` and
layers on:

- **Auth injection** -- credentials from :class:`~specli.auth.base.AuthResult`
  are merged into every outgoing request.
- **Plugin hooks** -- pre-request and post-response hooks via
  :class:`~specli.plugins.hooks.HookRunner`.
- **Dry-run mode** -- prints the request to stderr and returns a synthetic
  200 response without sending traffic.
- **Response caching** -- optional disk-based cache for GET requests via
  :class:`~specli.cache.ResponseCache`.
- **Retry with backoff** -- retries on 5xx and network errors with
  exponential delay (1 s, 2 s, 4 s, ...).

See Also:
    :class:`~specli.client.async_client.AsyncClient` for the
    equivalent non-blocking implementation.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Optional

import httpx

from specli.auth.base import AuthResult
from specli.auth.manager import AuthManager
from specli.exceptions import AuthError, ConnectionError_, NotFoundError, ServerError
from specli.models import Profile
from specli.output import get_output
from specli.plugins.hooks import HookContext, HookRunner

if TYPE_CHECKING:
    from specli.cache import ResponseCache


class SyncClient:
    """Synchronous HTTP client for API calls.

    Wraps :class:`httpx.Client` with auth injection, plugin hooks,
    dry-run mode, response caching, and automatic retry with exponential
    backoff.  Must be used as a context manager so that the underlying
    transport is properly opened and closed.

    Args:
        profile: The connection profile containing ``base_url``, auth
            config, and request settings (timeout, retries, SSL verify).
        auth_manager: Optional manager that resolves credentials before
            the first request.  When ``None``, no auth is injected.
        hook_runner: Optional plugin hook runner for pre-request and
            post-response hooks.
        dry_run: When ``True``, requests are printed to stderr and a
            synthetic 200 response is returned without network I/O.
        cache: Optional disk-based response cache.  Only GET requests
            with 2xx status codes are cached.

    Example::

        with SyncClient(profile, auth_manager=am) as client:
            response = client.get("/users")
    """

    def __init__(
        self,
        profile: Profile,
        auth_manager: Optional[AuthManager] = None,
        hook_runner: Optional[HookRunner] = None,
        dry_run: bool = False,
        cache: Optional[ResponseCache] = None,
    ) -> None:
        self._profile = profile
        self._auth_manager = auth_manager
        self._hook_runner = hook_runner
        self._dry_run = dry_run
        self._cache = cache
        self._auth_result: Optional[AuthResult] = None
        self._client: Optional[httpx.Client] = None

    # ------------------------------------------------------------------ #
    # Context manager
    # ------------------------------------------------------------------ #

    def __enter__(self) -> SyncClient:
        config = self._profile.request
        self._client = httpx.Client(
            base_url=self._profile.base_url or "",
            timeout=config.timeout,
            verify=config.verify_ssl,
            follow_redirects=True,
        )
        # Pre-authenticate if an auth manager and auth config are present.
        if self._auth_manager and self._profile.auth:
            self._auth_result = self._auth_manager.authenticate(self._profile)
        return self

    def __exit__(self, *args: object) -> None:
        if self._client:
            self._client.close()
            self._client = None

    # ------------------------------------------------------------------ #
    # Public request methods
    # ------------------------------------------------------------------ #

    def request(
        self,
        method: str,
        path: str,
        params: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        json_body: Optional[Any] = None,
        body: Optional[str] = None,
        data: Optional[dict[str, Any]] = None,
    ) -> httpx.Response:
        """Make an HTTP request with auth injection, hooks, retry, and error mapping.

        In dry-run mode the request details are printed to stderr and a
        synthetic 200 response is returned without sending any traffic.

        Args:
            method: HTTP method (GET, POST, PUT, PATCH, DELETE).
            path: URL path (appended to the profile's base_url).
            params: Query parameters.
            headers: Extra request headers.
            json_body: JSON-serialisable body (sets Content-Type automatically).
            body: Raw string body.
            data: Form-encoded body (application/x-www-form-urlencoded).

        Returns:
            The :class:`httpx.Response` from the server.

        Raises:
            AuthError: On 401 / 403.
            NotFoundError: On 404.
            ServerError: On 5xx after all retries are exhausted.
            ConnectionError_: On network / timeout errors after all retries.
        """
        merged_headers: dict[str, str] = {"Accept": "application/json"}
        merged_headers.update(headers or {})
        merged_params: dict[str, Any] = dict(params or {})

        # 1. Auth injection
        merged_headers, merged_params = self._inject_auth(merged_headers, merged_params)

        # 2. Build full URL for display / hooks
        base = self._profile.base_url or ""
        url = f"{base}{path}" if base else path

        # 3. Pre-request hooks
        merged_headers, merged_params = self._run_pre_request_hooks(
            method, url, merged_headers, merged_params,
        )

        # 4. Dry-run shortcut
        if self._dry_run:
            return self._print_dry_run(method, url, merged_headers, merged_params, json_body, body, data)

        # 5. Cache lookup (GET only)
        cached = self._cache_get(method, url, merged_params)
        if cached is not None:
            output = get_output()
            output.debug(f"Cache hit: {method.upper()} {path}")
            return httpx.Response(
                status_code=cached["status_code"],
                headers=cached.get("headers", {}),
                json=cached.get("body"),
                request=httpx.Request(method=method, url=url),
            )

        # 6. Execute with retry
        response = self._execute_with_retry(
            method, path, merged_headers, merged_params, json_body, body, data,
        )

        # 7. Post-response hooks
        response = self._run_post_response_hooks(response, method, url, merged_headers, merged_params)

        # 8. Cache store (GET 2xx only)
        self._cache_set(method, url, merged_params, response)

        # 9. Error mapping (raises on 4xx/5xx)
        self._map_response_error(response)

        return response

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        """Send a GET request.

        Args:
            path: URL path appended to the profile's ``base_url``.
            **kwargs: Forwarded to :meth:`request`.

        Returns:
            The :class:`httpx.Response`.
        """
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        """Send a POST request.

        Args:
            path: URL path appended to the profile's ``base_url``.
            **kwargs: Forwarded to :meth:`request`.

        Returns:
            The :class:`httpx.Response`.
        """
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> httpx.Response:
        """Send a PUT request.

        Args:
            path: URL path appended to the profile's ``base_url``.
            **kwargs: Forwarded to :meth:`request`.

        Returns:
            The :class:`httpx.Response`.
        """
        return self.request("PUT", path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> httpx.Response:
        """Send a PATCH request.

        Args:
            path: URL path appended to the profile's ``base_url``.
            **kwargs: Forwarded to :meth:`request`.

        Returns:
            The :class:`httpx.Response`.
        """
        return self.request("PATCH", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> httpx.Response:
        """Send a DELETE request.

        Args:
            path: URL path appended to the profile's ``base_url``.
            **kwargs: Forwarded to :meth:`request`.

        Returns:
            The :class:`httpx.Response`.
        """
        return self.request("DELETE", path, **kwargs)

    # ------------------------------------------------------------------ #
    # Private helpers
    # ------------------------------------------------------------------ #

    def _inject_auth(
        self,
        headers: dict[str, str],
        params: dict[str, Any],
    ) -> tuple[dict[str, str], dict[str, Any]]:
        """Merge auth credentials into *headers* and *params*."""
        if self._auth_result is None:
            return headers, params

        # Auth headers / params are applied first so that caller-supplied
        # values can override them if needed.
        merged_headers = {**self._auth_result.headers, **headers}
        merged_params = {**self._auth_result.params, **params}

        # Cookies are injected as a ``Cookie`` header.  If the auth result
        # contains cookies we build the header value from all key=value pairs.
        if self._auth_result.cookies:
            cookie_str = "; ".join(
                f"{k}={v}" for k, v in self._auth_result.cookies.items()
            )
            # Preserve any existing Cookie header from the caller.
            existing = merged_headers.get("Cookie")
            if existing:
                cookie_str = f"{existing}; {cookie_str}"
            merged_headers["Cookie"] = cookie_str

        return merged_headers, merged_params

    def _run_pre_request_hooks(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        params: dict[str, Any],
    ) -> tuple[dict[str, str], dict[str, Any]]:
        """Run plugin pre-request hooks, returning possibly-modified headers/params."""
        if self._hook_runner is None:
            return headers, params

        ctx = HookContext(
            method=method,
            url=url,
            headers=dict(headers),
            params=dict(params),
        )
        ctx = self._hook_runner.run_pre_request(ctx)
        return ctx.headers, ctx.params

    def _run_post_response_hooks(
        self,
        response: httpx.Response,
        method: str,
        url: str,
        headers: dict[str, str],
        params: dict[str, Any],
    ) -> httpx.Response:
        """Run plugin post-response hooks."""
        if self._hook_runner is None:
            return response

        response_headers: dict[str, str] = dict(response.headers)
        try:
            response_body = response.json()
        except Exception:
            response_body = response.text

        ctx = HookContext(
            method=method,
            url=url,
            headers=headers,
            params=params,
            status_code=response.status_code,
            response_headers=response_headers,
            response_body=response_body,
        )
        self._hook_runner.run_post_response(ctx)
        return response

    def _execute_with_retry(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        params: dict[str, Any],
        json_body: Any,
        body: str | None,
        data: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Execute the HTTP request with exponential-backoff retry.

        Retries on 5xx status codes and connection / timeout errors up to
        ``max_retries`` times. The delay doubles each attempt: 1 s, 2 s, 4 s, ...
        """
        assert self._client is not None, "Client not initialised -- use as context manager"

        max_retries = self._profile.request.max_retries
        output = get_output()
        last_error: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                # Build request kwargs
                kwargs: dict[str, Any] = {
                    "method": method,
                    "url": path,
                    "headers": headers,
                    "params": params,
                }
                if data is not None:
                    kwargs["data"] = data
                elif json_body is not None:
                    kwargs["json"] = json_body
                elif body is not None:
                    kwargs["content"] = body

                response = self._client.request(**kwargs)

                # Only retry on 5xx (server errors)
                if response.status_code >= 500 and attempt < max_retries:
                    delay = 2 ** attempt  # 1, 2, 4, ...
                    output.debug(
                        f"Server error {response.status_code}, retrying in {delay}s "
                        f"(attempt {attempt + 1}/{max_retries})"
                    )
                    time.sleep(delay)
                    continue

                return response

            except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                if attempt < max_retries:
                    delay = 2 ** attempt
                    output.debug(
                        f"Connection error: {exc}, retrying in {delay}s "
                        f"(attempt {attempt + 1}/{max_retries})"
                    )
                    time.sleep(delay)
                    continue

                # Notify error hooks before raising
                if self._hook_runner:
                    self._hook_runner.run_error(exc)

                raise ConnectionError_(
                    f"Connection failed after {max_retries + 1} attempts: {exc}"
                ) from exc

        # Should only be reached if the last retry was a 5xx response.
        # The response is returned above; this is a safety fallback that
        # re-raises the last connection error if somehow we get here.
        if last_error is not None:  # pragma: no cover
            raise ConnectionError_(str(last_error)) from last_error
        raise ServerError("Request failed after all retries")  # pragma: no cover

    def _map_response_error(self, response: httpx.Response) -> None:
        """Raise a typed exception for error HTTP status codes."""
        status = response.status_code
        if status < 400:
            return

        # Try to extract an error message from the response body.
        try:
            detail = response.json()
            if isinstance(detail, dict):
                msg = detail.get("message") or detail.get("error") or detail.get("detail") or ""
            else:
                msg = str(detail)
        except Exception:
            msg = response.text[:200] if response.text else ""

        prefix = f"HTTP {status}"
        full_msg = f"{prefix}: {msg}" if msg else prefix

        if self._hook_runner:
            exc: Exception
            if status in (401, 403):
                exc = AuthError(full_msg)
            elif status == 404:
                exc = NotFoundError(full_msg)
            elif status >= 500:
                exc = ServerError(full_msg)
            else:
                exc = ServerError(full_msg)
            self._hook_runner.run_error(exc)

        if status in (401, 403):
            raise AuthError(full_msg)
        if status == 404:
            raise NotFoundError(full_msg)
        if status >= 500:
            raise ServerError(full_msg)
        # Other 4xx -- raise as generic server error with the status info.
        raise ServerError(full_msg)

    def _cache_get(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | None,
    ) -> dict | None:
        """Look up a cached response for a GET request."""
        if self._cache is None:
            return None
        return self._cache.get(method, url, params)

    def _cache_set(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | None,
        response: httpx.Response,
    ) -> None:
        """Store a successful GET response in the cache."""
        if self._cache is None:
            return
        try:
            body = response.json()
        except Exception:
            body = response.text

        response_data = {
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "body": body,
        }
        self._cache.set(method, url, params, response_data)

    def _print_dry_run(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        params: dict[str, Any],
        json_body: Any,
        body: str | None,
        data: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Print request details to stderr and return a synthetic 200 response."""
        import json as json_mod

        output = get_output()
        output.info(f"[dry-run] {method} {url}")

        if headers:
            for key, value in headers.items():
                output.info(f"  Header: {key}: {value}")

        if params:
            for key, value in params.items():
                output.info(f"  Param: {key}={value}")

        if data is not None:
            output.info(f"  Body (form): {json_mod.dumps(data, indent=2)}")
        elif json_body is not None:
            output.info(f"  Body (JSON): {json_mod.dumps(json_body, indent=2)}")
        elif body is not None:
            output.info(f"  Body: {body}")

        # Return a synthetic 200 response so callers can continue without
        # special-casing None.
        return httpx.Response(
            status_code=200,
            headers={"content-type": "application/json"},
            json={"dry_run": True, "message": "Request was not sent"},
            request=httpx.Request(method=method, url=url),
        )
