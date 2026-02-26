"""Canonical Pydantic models shared across all specli modules.

This is the single source of truth for data shapes in the project. Every other
module imports from here rather than defining its own models. The models fall
into two groups:

**Configuration models** -- serialised as JSON in the user's config directory:
    :class:`AuthConfig`, :class:`PathRulesConfig`, :class:`RequestConfig`,
    :class:`OutputConfig`, :class:`CacheConfig`, :class:`PluginsConfig`,
    :class:`GlobalConfig`, and :class:`Profile`.

**Parser output models** -- produced by the OpenAPI spec parser and consumed by
the command-tree generator:
    :class:`HTTPMethod`, :class:`ParameterLocation`, :class:`APIParameter`,
    :class:`RequestBodyInfo`, :class:`ResponseInfo`, :class:`SecurityScheme`,
    :class:`APIOperation`, :class:`APIInfo`, :class:`ServerInfo`, and
    :class:`ParsedSpec`.

All models use Pydantic v2 with ``model_config`` where needed. Configuration
models that accept plugin-defined extensions use ``extra="allow"`` so that
unknown keys are preserved in ``model_extra``.
"""

from __future__ import annotations

import enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


# --- Auth Config ---


class AuthConfig(BaseModel):
    """Authentication configuration embedded in a :class:`Profile`.

    Declares how the generated CLI authenticates with the target API. The
    ``type`` field selects the authentication strategy (e.g. ``api_key``,
    ``bearer``, ``oauth2_client_credentials``), and the remaining fields
    supply strategy-specific parameters.

    Plugins may define their own fields beyond the ones declared here.
    Extra fields are preserved and accessible via ``model_extra``.

    Example::

        AuthConfig(
            type="api_key",
            header="X-API-Key",
            location="header",
            source="env:MY_API_KEY",
        )
    """

    model_config = ConfigDict(extra="allow")

    type: str = Field(
        description="Auth type: api_key, bearer, basic, oauth2_client_credentials, "
        "oauth2_auth_code, openid_connect, manual_token, browser_login, api_key_gen"
    )
    header: Optional[str] = Field(
        default=None, description="Header name for api_key auth"
    )
    param_name: Optional[str] = Field(
        default=None, description="Query parameter name for api_key auth"
    )
    location: str = Field(
        default="header", description="Where to send: header, query, cookie"
    )
    source: str = Field(
        default="prompt",
        description="Credential source: env:VAR, file:/path, prompt, store:PROFILE, "
        "keyring:service:account",
    )
    # OAuth2 fields
    token_url: Optional[str] = None
    authorization_url: Optional[str] = None
    scopes: list[str] = Field(default_factory=list)
    client_id_source: Optional[str] = None
    client_secret_source: Optional[str] = None
    # OpenID Connect
    openid_connect_url: Optional[str] = None
    # Browser login
    login_url: Optional[str] = Field(
        default=None, description="URL to open in browser for login"
    )
    callback_capture: str = Field(
        default="cookie",
        description="What to capture from callback: cookie, header, query_param, body_field",
    )
    capture_name: Optional[str] = Field(
        default=None,
        description="Name of cookie/header/param to capture from callback",
    )
    # Device code flow (RFC 8628)
    device_authorization_url: Optional[str] = Field(
        default=None,
        description="Device authorization endpoint for RFC 8628 device code flow",
    )
    # API key generation
    key_create_endpoint: Optional[str] = Field(
        default=None, description="POST endpoint to create an API key"
    )
    key_create_body: Optional[dict[str, Any]] = Field(
        default=None, description="JSON body for key creation request"
    )
    key_response_field: str = Field(
        default="api_key",
        description="Field in JSON response containing the generated key",
    )
    key_create_auth_source: Optional[str] = Field(
        default=None,
        description="Credential source for bootstrapping key creation auth",
    )
    # Shared: credential name and persistence
    credential_name: Optional[str] = Field(
        default=None, description="Name for the stored credential"
    )
    persist: bool = Field(
        default=False, description="Whether to persist credential to store"
    )


class PathRulesConfig(BaseModel):
    """Rules for transforming API URL paths into CLI command names.

    When the command-tree generator maps ``/api/v1/users/{id}/orders`` to a
    nested Typer command, these rules control which path segments are stripped,
    kept, skipped, or collapsed into flat names. Tuning these rules keeps the
    generated CLI's command hierarchy clean and intuitive.

    See Also:
        :class:`Profile`: Parent model that holds a ``path_rules`` field.
    """

    auto_strip_prefix: bool = Field(
        default=True, description="Auto-detect and strip longest common prefix"
    )
    keep: list[str] = Field(
        default_factory=list, description="Segments to keep even if auto-stripped"
    )
    strip_prefix: Optional[str] = Field(
        default=None, description="Explicit prefix to strip (overrides auto)"
    )
    skip_segments: list[str] = Field(
        default_factory=list, description="Segments to remove wherever found"
    )
    collapse: dict[str, str] = Field(
        default_factory=dict, description="Map specific paths to flat command names"
    )
    include_prefix: Optional[list[str] | str] = Field(
        default=None,
        description="Only include paths starting with this prefix (e.g. '/api/' or ['/api/', '/auth/'])",
    )


class RequestConfig(BaseModel):
    """Default HTTP request settings applied to every API call in a profile."""

    timeout: int = Field(default=30, description="Request timeout in seconds")
    verify_ssl: bool = Field(default=True, description="Verify SSL certificates")
    max_retries: int = Field(default=3, description="Max retry attempts")


class OutputConfig(BaseModel):
    """Default output format preferences stored in :class:`GlobalConfig`."""

    format: str = Field(
        default="auto", description="Output format: auto, json, plain, rich"
    )
    pager: bool = Field(
        default=True, description="Use pager for long output in TTY mode"
    )


class CacheConfig(BaseModel):
    """HTTP response cache settings stored in :class:`GlobalConfig`."""

    enabled: bool = Field(default=True, description="Enable response caching")
    ttl_seconds: int = Field(default=300, description="Cache TTL in seconds")


class PluginsConfig(BaseModel):
    """Explicit plugin allow/deny lists stored in :class:`GlobalConfig`."""

    enabled: list[str] = Field(default_factory=list)
    disabled: list[str] = Field(default_factory=list)


class GlobalConfig(BaseModel):
    """User-wide configuration persisted at ``~/.config/specli/config.json``.

    Loaded and saved by :func:`~specli.config.load_global_config` and
    :func:`~specli.config.save_global_config`. Fields here have the
    lowest precedence and can be overridden by project config, environment
    variables, or CLI flags. See :func:`~specli.config.resolve_config`
    for the full precedence chain.
    """

    default_profile: Optional[str] = None
    auto_select_single_profile: bool = True
    output: OutputConfig = Field(default_factory=OutputConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    plugins: PluginsConfig = Field(default_factory=PluginsConfig)


class Profile(BaseModel):
    """Per-API profile stored as JSON under the ``profiles/`` config directory.

    Each profile points to a single OpenAPI spec (local file or URL) and
    bundles the authentication, path-rule, and request settings needed to
    talk to that API. Profiles are created with ``specli init`` and
    managed with ``specli config``.

    Extra fields (e.g. ``source_enrichment``) are preserved and accessible
    via ``model_extra`` so that plugins can attach their own configuration
    without requiring model changes.

    See Also:
        :func:`~specli.config.load_profile`: Deserialise a profile by name.
        :func:`~specli.config.save_profile`: Persist a profile to disk.
    """

    model_config = ConfigDict(extra="allow")

    name: str
    spec: str = Field(description="URL or file path to OpenAPI spec")
    base_url: Optional[str] = Field(
        default=None, description="Override base URL from spec"
    )
    auth: Optional[AuthConfig] = None
    path_rules: PathRulesConfig = Field(default_factory=PathRulesConfig)
    request: RequestConfig = Field(default_factory=RequestConfig)


# --- Parser Output Models ---


class HTTPMethod(str, enum.Enum):
    """HTTP methods recognised by OpenAPI 3.x path-item objects.

    Used as the ``method`` field on :class:`APIOperation` to indicate which
    HTTP verb an operation represents.
    """

    GET = "get"
    POST = "post"
    PUT = "put"
    PATCH = "patch"
    DELETE = "delete"
    HEAD = "head"
    OPTIONS = "options"
    TRACE = "trace"


class ParameterLocation(str, enum.Enum):
    """Locations where an API parameter can appear, per OpenAPI ``in`` field."""

    QUERY = "query"
    HEADER = "header"
    PATH = "path"
    COOKIE = "cookie"


class APIParameter(BaseModel):
    """A single parameter extracted from an OpenAPI operation.

    Maps to an OpenAPI *Parameter Object*. Each parameter becomes a CLI
    option or argument on the generated Typer command; path parameters
    become positional arguments, while query/header/cookie parameters
    become ``--flag`` options.
    """

    name: str
    location: ParameterLocation
    required: bool = False
    description: Optional[str] = None
    schema_type: str = Field(default="string", description="JSON Schema type")
    schema_format: Optional[str] = None
    default: Any = None
    enum_values: Optional[list[str]] = None
    example: Any = None


class RequestBodyInfo(BaseModel):
    """Parsed request body metadata for an :class:`APIOperation`.

    In the generated CLI, the request body is exposed as a ``--body`` option
    accepting a JSON string (or form-encoded data if the content type is
    ``application/x-www-form-urlencoded``).
    """

    required: bool = False
    description: Optional[str] = None
    content_types: list[str] = Field(default_factory=list)
    schema_: Optional[dict[str, Any]] = Field(default=None, alias="schema")

    model_config = {"populate_by_name": True}


class ResponseInfo(BaseModel):
    """Parsed response metadata for a single HTTP status code."""

    status_code: str
    description: Optional[str] = None
    content_types: list[str] = Field(default_factory=list)
    schema_: Optional[dict[str, Any]] = Field(default=None, alias="schema")

    model_config = {"populate_by_name": True}


class SecurityScheme(BaseModel):
    """An OpenAPI *Security Scheme Object* extracted from the spec.

    The ``type`` field discriminates between ``apiKey``, ``http``, ``oauth2``,
    and ``openIdConnect`` schemes. Only the fields relevant to the active
    scheme type are populated; the rest remain ``None``.
    """

    name: str
    type: str  # apiKey, http, oauth2, openIdConnect
    description: Optional[str] = None
    # apiKey
    param_name: Optional[str] = Field(default=None, alias="in_name")
    location: Optional[str] = Field(
        default=None, alias="in_location"
    )  # header, query, cookie
    # http
    scheme: Optional[str] = None  # bearer, basic
    bearer_format: Optional[str] = None
    # oauth2
    flows: Optional[dict[str, Any]] = None
    # openIdConnect
    openid_connect_url: Optional[str] = None

    model_config = {"populate_by_name": True}


class APIOperation(BaseModel):
    """A single parsed API operation (one URL path + HTTP method pair).

    Each operation is derived from an OpenAPI *Operation Object* and
    corresponds to exactly one generated Typer command in the output CLI.
    """

    path: str
    method: HTTPMethod
    operation_id: Optional[str] = None
    summary: Optional[str] = None
    description: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    parameters: list[APIParameter] = Field(default_factory=list)
    request_body: Optional[RequestBodyInfo] = None
    responses: list[ResponseInfo] = Field(default_factory=list)
    security: list[dict[str, list[str]]] = Field(
        default_factory=list, description="Security requirements"
    )
    deprecated: bool = False


class APIInfo(BaseModel):
    """API metadata extracted from the OpenAPI spec's *Info Object*."""

    title: str
    version: str
    description: Optional[str] = None
    terms_of_service: Optional[str] = None
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    contact_url: Optional[str] = None
    license_name: Optional[str] = None
    license_url: Optional[str] = None


class ServerInfo(BaseModel):
    """A server entry from the OpenAPI spec's ``servers`` array.

    The first server's ``url`` is used as the default ``base_url`` when
    a :class:`Profile` does not specify an explicit override.
    """

    url: str
    description: Optional[str] = None


class ParsedSpec(BaseModel):
    """Complete parsed representation of an OpenAPI specification.

    Produced by the parser subsystem and consumed by the command-tree
    generator. Holds every piece of information needed to emit a fully
    functional Typer CLI: API metadata, server URLs, operations (each
    becoming a CLI command), and security schemes.

    See Also:
        :class:`APIOperation`: Individual operation within the spec.
        :class:`APIInfo`: Metadata (title, version, etc.).
    """

    info: APIInfo
    servers: list[ServerInfo] = Field(default_factory=list)
    operations: list[APIOperation] = Field(default_factory=list)
    security_schemes: dict[str, SecurityScheme] = Field(default_factory=dict)
    openapi_version: str = Field(
        description="Original OpenAPI version string (e.g., '3.0.3', '3.1.0')"
    )
    raw_spec: Optional[dict[str, Any]] = Field(
        default=None, description="Original spec dict for reference"
    )
