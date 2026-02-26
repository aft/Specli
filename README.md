# specli

**The CLI generator for the age of LLMs.**

[![PyPI version](https://img.shields.io/pypi/v/specli)](https://pypi.org/project/specli/)
[![Python 3.10+](https://img.shields.io/pypi/pyversions/specli)](https://pypi.org/project/specli/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-1009%20passing-brightgreen)]()

Point specli at any OpenAPI 3.x spec and get a production-ready CLI with auth, retries, rich output, and tab completion. Then enrich it from your source code, export strings for LLM rewriting, and generate Claude Code skills -- all in one pipeline.

```bash
specli init --spec examples/uspto-openapi.json
specli api root list                # list available datasets
specli api fields list oa_citations v1   # searchable fields
```

---

## Why specli?

Traditional API clients are either hand-coded or generated as lifeless SDK stubs. specli takes a different approach: it reads your OpenAPI spec, understands your source code, and produces a CLI that feels hand-crafted -- with help text pulled from your docstrings, descriptions polished by LLMs, and skill files that teach AI assistants how to use your API.

**For API authors:** Ship a CLI alongside your API with zero maintenance. Source enrichment means your Python docstrings become CLI help text automatically.

**For API consumers:** Turn any OpenAPI spec into a working CLI in seconds. No code generation, no boilerplate.

**For AI-assisted workflows:** Generate Claude Code skills so AI assistants can discover and use your API with full context -- endpoints, parameters, auth setup, and examples.

---

## Table of Contents

- [Install](#install)
- [Quick Start](#quick-start)
- [Building Standalone CLIs](#building-standalone-clis)
- [Global Flags](#global-flags)
- [Advanced Usage](#advanced-usage)
- [Plugins](#plugins)
  - [Auth Plugins](#auth-plugins)
  - [Build Plugin](#build-plugin)
  - [Skill Plugin](#skill-plugin)
  - [Completion Plugin](#completion-plugin)
- [Source Code Enrichment](#source-code-enrichment)
- [String Export / Import](#string-export--import)
- [Using Generated Skills](#using-generated-skills)
- [Path Rules](#path-rules)
- [Configuration](#configuration)
- [Development Setup](#development-setup)
- [License](#license)

---

## Install

```bash
pip install specli
```


---

## Quick Start

```bash
# 1. Initialize from any OpenAPI 3.x spec (URL, file, or stdin)
specli init --spec examples/uspto-openapi.json

# 2. Fix the base URL (the spec uses a server variable that needs resolving)
#    Edit ~/.config/specli/profiles/uspto-data-set-api.json and change:
#      "base_url": "{scheme}://developer.uspto.gov/ds-api"
#    to:
#      "base_url": "https://developer.uspto.gov/ds-api"

# 3. Explore the API
specli inspect paths
specli inspect info

# 4. Make API calls (the USPTO API is public -- no auth needed)
specli api root list                            # list available datasets
specli api fields list oa_citations v1          # searchable fields for a dataset

# 5. POST with a request body (form-encoded, auto-detected from spec)
specli api records create v1 oa_citations \
  --body '{"criteria": "*:*", "start": 0, "rows": 5}'

# 6. JSON output for scripting
specli --json api records create v1 oa_citations \
  --body '{"criteria": "Patent_PGPub:US*", "rows": 3}' | jq '.[] | keys'
```

---

## Building Standalone CLIs

specli can compile your API into a standalone binary or a pip-installable package. The OpenAPI spec, profile config, and all dependencies are baked in -- the result is a single command that needs no setup.

### Compile a binary (PyInstaller)

```bash
specli build compile -p uspto-data-set-api -n uspto --output ./dist

# Output: ./dist/uspto (about 19 MB)
./dist/uspto root list
./dist/uspto --help
```

### Generate a pip-installable package

```bash
specli build generate -p uspto-data-set-api -n uspto

# Output: ./uspto/ (package directory)
pip install ./uspto
uspto root list
```

Both modes support the full enrichment pipeline. See [Build Plugin](#build-plugin) for all flags.

---

## Global Flags

These flags work with every specli command:

| Flag | Short | Description |
|------|-------|-------------|
| `--version` | | Show version and exit |
| `--profile` | `-p` | Profile name override |
| `--json` | | Force JSON output format |
| `--plain` | | Force plain-text output |
| `--no-color` | | Disable color and Rich markup |
| `--quiet` | `-q` | Suppress non-essential output |
| `--verbose` | `-v` | Enable debug output |
| `--dry-run` | `-n` | Preview HTTP requests without sending |
| `--force` | `-f` | Skip interactive confirmations |
| `--no-input` | | Disable all interactive prompts |
| `--output` | `-o` | Redirect primary output to a file |

---

## Advanced Usage

### Dry-run mode

Preview the exact HTTP request without sending it:

```bash
specli -n api records create v1 oa_citations \
  --body '{"criteria": "*:*", "rows": 1}'
# [dry-run] POST https://developer.uspto.gov/ds-api/oa_citations/v1/records
#   Content-Type: application/x-www-form-urlencoded
#   Body: criteria=*:*&rows=1
```

### Load body from a file

```bash
echo '{"criteria": "*:*", "rows": 10}' > query.json
specli api records create v1 oa_citations --body @query.json
```

### Override the base URL

```bash
SPECLI_BASE_URL=https://developer.uspto.gov/ds-api specli api root list
```

### Build with full enrichment pipeline

```bash
specli build compile \
  -p uspto-data-set-api \
  -n uspto \
  --source ./src \
  --import-strings ./strings.json \
  --generate-skill ./skill \
  --cli-version 2.1.0
```

### Enrichment-only (no build)

Export strings and generate skills without compiling a binary:

```bash
specli build compile \
  -p uspto-data-set-api -n uspto \
  --export-strings ./strings.json \
  --generate-skill ./skill \
  --no-build
```

---

## Plugins

specli ships with four plugin groups: **auth**, **build**, **skill**, and **completion**.

---

### Auth Plugins

Authentication is configured per-profile in the `auth` section. specli includes 10 auth plugins covering every common pattern.

All credential sources use the format `<type>:<value>`:

| Source | Example | Description |
|--------|---------|-------------|
| `env:VAR` | `env:MY_API_KEY` | Read from environment variable |
| `file:/path` | `file:~/.token` | Read first line of file |
| `plain:VALUE` | `plain:sk_abc123` | Literal value embedded in profile |
| `prompt` | `prompt` | Interactive hidden input |
| `store:PROFILE` | `store:myapi` | Local credential store |
| `keyring:svc:acct` | `keyring:myapp:token` | System keyring |

#### API Key (`api_key`)

Injects an API key via header, query parameter, or cookie.

```json
{
  "auth": {
    "type": "api_key",
    "header": "X-API-Key",
    "location": "header",
    "source": "env:MY_API_KEY"
  }
}
```

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `header` / `param_name` | yes | | Key name |
| `location` | no | `"header"` | `"header"`, `"query"`, or `"cookie"` |
| `source` | yes | | Credential source |
| `secret_source` | no | | Second credential for dual-key auth |
| `secret_header` / `secret_param_name` | no | `X-API-Secret` | Name for the secret key |
| `check_endpoint` | no | | API path for auth verification (see below) |

**Auth verification (`check_endpoint`):** When set, the generated CLI's `auth test` command makes a real HTTP GET request to this endpoint to verify credentials work. A 2xx response means authentication succeeded; 401/403 means it failed. This replaces the default local-only credential check with a live server round-trip.

```json
{
  "auth": {
    "type": "api_key",
    "header": "X-API-Key",
    "source": "plain:my_key_value",
    "check_endpoint": "/api/settings"
  }
}
```

```bash
$ mycli auth test
Auth OK — /api/settings returned 200
```

#### HTTP Basic (`basic`)

Sends `Authorization: Basic <base64>` per RFC 7617.

```json
{
  "auth": {
    "type": "basic",
    "source": "env:BASIC_CREDS"
  }
}
```

The source must resolve to `"username:password"`.

| Field | Required | Description |
|-------|----------|-------------|
| `source` | yes | Credential source (must be `user:pass` format) |

#### Bearer Token (`bearer`)

Sends `Authorization: Bearer <token>`. Static injection, no refresh logic.

```json
{
  "auth": {
    "type": "bearer",
    "source": "env:MY_TOKEN"
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `source` | yes | Credential source for the token |

#### OAuth2 Client Credentials (`oauth2_client_credentials`)

Non-interactive server-to-server auth. Exchanges client ID and secret for an access token. Tokens cached with auto-refresh.

```json
{
  "auth": {
    "type": "oauth2_client_credentials",
    "token_url": "https://provider.example.com/token",
    "client_id_source": "env:CLIENT_ID",
    "client_secret_source": "env:CLIENT_SECRET",
    "scopes": ["read:api", "write:api"]
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `token_url` | yes | Token endpoint URL |
| `client_id_source` | yes | Credential source for client ID |
| `client_secret_source` | yes | Credential source for client secret |
| `scopes` | no | List of OAuth scope strings |

#### OAuth2 Authorization Code + PKCE (`oauth2_auth_code`)

Interactive browser-based OAuth2 with PKCE. Opens browser, receives callback on local server, exchanges code for tokens. Supports automatic refresh.

```json
{
  "auth": {
    "type": "oauth2_auth_code",
    "authorization_url": "https://provider.example.com/authorize",
    "token_url": "https://provider.example.com/token",
    "client_id_source": "env:CLIENT_ID",
    "scopes": ["openid", "profile"]
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `authorization_url` | yes | Authorization endpoint |
| `token_url` | yes | Token endpoint |
| `client_id_source` | no | Credential source for client ID |
| `client_secret_source` | no | Credential source for client secret |
| `scopes` | no | List of OAuth scopes |

#### OpenID Connect (`openid_connect`)

Auto-discovers OAuth2 endpoints from a `.well-known/openid-configuration` document, then runs the authorization code flow.

```json
{
  "auth": {
    "type": "openid_connect",
    "openid_connect_url": "https://provider.example.com/.well-known/openid-configuration",
    "client_id_source": "env:CLIENT_ID"
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `openid_connect_url` | yes | OIDC discovery URL |
| `client_id_source` | no | Credential source for client ID |
| `client_secret_source` | no | Credential source for client secret |
| `scopes` | no | List of scopes |

#### Browser Login (`browser_login`)

Opens a browser for interactive login. Two modes:

**OAuth mode** (when `authorization_url` + `token_url` + `client_id_source` are set): Full OAuth2 with PKCE and persistent refresh tokens.

```json
{
  "auth": {
    "type": "browser_login",
    "authorization_url": "https://provider.example.com/authorize",
    "token_url": "https://provider.example.com/token",
    "client_id_source": "env:CLIENT_ID",
    "persist": true
  }
}
```

**Simple mode** (when `login_url` is set): Opens a login page and captures a credential from the redirect callback.

```json
{
  "auth": {
    "type": "browser_login",
    "login_url": "https://provider.example.com/login",
    "capture_name": "session_id",
    "callback_capture": "cookie",
    "persist": true
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `authorization_url` | OAuth mode | Authorization endpoint |
| `token_url` | OAuth mode | Token endpoint |
| `client_id_source` | OAuth mode | Credential source for client ID |
| `login_url` | simple mode | Login page URL |
| `capture_name` | simple mode | Name of the credential to capture |
| `callback_capture` | simple mode | `"cookie"`, `"header"`, `"query_param"`, or `"body_field"` |
| `location` | no | `"header"` (default), `"query"`, `"cookie"` |
| `persist` | no | Save credential for reuse |

#### Device Code (`device_code`)

OAuth2 Device Authorization Grant (RFC 8628) for headless environments -- SSH sessions, Docker containers, CI pipelines. Displays a code, user authorizes on any device.

```json
{
  "auth": {
    "type": "device_code",
    "device_authorization_url": "https://provider.example.com/device_authorization",
    "token_url": "https://provider.example.com/token",
    "client_id_source": "env:CLIENT_ID",
    "persist": true
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `device_authorization_url` | yes | Device authorization endpoint |
| `token_url` | yes | Token endpoint |
| `client_id_source` | yes | Credential source for client ID |
| `scopes` | no | List of scopes |
| `persist` | no | Save refresh token for reuse |

#### Manual Token (`manual_token`)

Prompts for a token via hidden input. Simplest interactive auth. Optionally persists the token so subsequent runs skip the prompt.

```json
{
  "auth": {
    "type": "manual_token",
    "persist": true,
    "credential_name": "X-Custom-Token"
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `location` | no | `"header"` (default), `"query"`, `"cookie"` |
| `credential_name` | no | Stored credential name |
| `persist` | no | Save token for reuse |

#### API Key Generation (`api_key_gen`)

Calls a remote endpoint to provision an API key, then persists and reuses it. For APIs that require key creation before use.

```json
{
  "auth": {
    "type": "api_key_gen",
    "key_create_endpoint": "https://api.example.com/keys",
    "key_response_field": "api_key",
    "key_create_auth_source": "env:BOOTSTRAP_TOKEN",
    "persist": true
  }
}
```

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `key_create_endpoint` | yes | | POST endpoint to create the key |
| `key_response_field` | no | `"api_key"` | Field name in the response JSON |
| `key_create_body` | no | `{}` | JSON body for the creation request |
| `key_create_auth_source` | no | | Bootstrap auth for key creation |
| `location` | no | `"header"` | Where to inject the key |
| `header` / `param_name` | no | `"X-API-Key"` | Key name |
| `persist` | no | `true` | Save the key for reuse |

---

### Build Plugin

The `build` plugin compiles your API profile into a standalone CLI.

#### Build defaults from profile

Build parameters can be stored in the profile's `build` section so you don't have to repeat flags every time. CLI flags always override profile defaults.

```json
{
  "name": "myapi",
  "spec": "openapi.json",
  "build": {
    "name": "myapi",
    "output_dir": "/tmp/myapi-build",
    "cli_version": "2.0.0",
    "import_strings": "/path/to/strings.json",
    "generate_skill": "/path/to/skill",
    "source_dir": "/path/to/source"
  }
}
```

With a `build` section configured, a full build becomes:

```bash
specli build generate -p myapi
```

Resolution order (highest wins): CLI flag > profile `build` section > hardcoded default.

#### `specli build compile`

Produces a self-contained PyInstaller binary.

| Flag | Short | Required | Default | Description |
|------|-------|----------|---------|-------------|
| `--profile` | `-p` | yes | | Profile to bake in |
| `--name` | `-n` | no* | | Output binary name |
| `--output` | `-o` | no | `./dist` | Output directory |
| `--cli-version` | | no | `1.0.0` | Version string |
| `--onedir` | | no | `false` | Directory bundle instead of single file |
| `--clean/--no-clean` | | no | `true` | Remove build artifacts after |
| `--source` | `-s` | no | | Source dir for enrichment |
| `--export-strings` | | no | | Export strings to JSON |
| `--import-strings` | | no | | Import strings from JSON |
| `--generate-skill` | | no | | Generate skill files to dir |
| `--no-build` | | no | `false` | Run pipeline only, skip compilation |

*\* Required unless provided in the profile's `build.name` field.*

#### `specli build generate`

Produces a pip-installable Python package.

| Flag | Short | Required | Default | Description |
|------|-------|----------|---------|-------------|
| `--profile` | `-p` | yes | | Profile to bake in |
| `--name` | `-n` | no* | | Package/CLI name |
| `--output` | `-o` | no | `.` | Directory to create package in |
| `--cli-version` | | no | `1.0.0` | Package version |
| `--source` | `-s` | no | | Source dir for enrichment |
| `--export-strings` | | no | | Export strings to JSON |
| `--import-strings` | | no | | Import strings from JSON |
| `--generate-skill` | | no | | Generate skill files to dir |
| `--no-build` | | no | `false` | Run pipeline only, skip generation |

*\* Required unless provided in the profile's `build.name` field.*

**The generated CLI** has API commands at the top level (no `api` sub-group), an `auth test` command (which uses `check_endpoint` if configured), and all global flags.

---

### Skill Plugin

Generates Claude Code skill files from the active profile's OpenAPI spec.

#### `specli skill generate`

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--output` | `-o` | `./skill-output` | Output directory |
| `--profile` | `-p` | default profile | Profile name |

**Generated files:**

```
skill-output/
  SKILL.md                    # Main skill: grouped commands, examples, quick start
  references/
    api-reference.md          # Full endpoint docs: params, bodies, responses
    auth-setup.md             # Auth configuration guide from security schemes
```

---

### Completion Plugin

Installs shell tab-completion scripts.

#### `specli completion install [SHELL]`

Auto-detects shell if omitted. Supported: `bash`, `zsh`, `fish`, `powershell`.

#### `specli completion show SHELL`

Prints the completion script to stdout for manual installation.

---

## Source Code Enrichment

specli can scan your Python source code at build time and pull documentation into the CLI's help text. This means your FastAPI docstrings, Pydantic field descriptions, and module docs automatically become `--help` output.

### How it works

```
OpenAPI spec  ──────────────────────────┐
                                        ▼
Python source ── AST scan ── match ── enrich ── build
  (docstrings,     routes      by        │
   Field(desc))   + params    method     ▼
                  + prefix    + path   Enriched spec
                                        │
                                        ▼
                                   CLI with rich help text
```

The scanner:
1. Walks Python files matching glob patterns
2. Finds route handler functions (`@app.get(...)`, `@router.post(...)`)
3. Resolves `APIRouter(prefix=...)` to build full paths
4. Extracts function docstrings (first line as summary, rest as description)
5. Parses `Args:` / `Parameters:` sections for parameter descriptions
6. Reads Pydantic `Field(description=...)` for request body fields

### Configuration

Add `source_enrichment` to your profile:

```json
{
  "name": "uspto-data-set-api",
  "spec": "examples/uspto-openapi.json",
  "source_enrichment": {
    "source_dir": "/path/to/your/app",
    "include": ["**/*.py"],
    "exclude": ["**/test_*", "**/__pycache__/**"]
  }
}
```

Or pass it at build time:

```bash
specli build compile -p uspto-data-set-api -n uspto --source /path/to/your/app
```

### Enrichment rules

- Source data **fills gaps only** -- it never overwrites substantive spec content
- A summary is considered "thin" if it's missing, very short, or auto-derived from operationId
- Source descriptions replace spec descriptions only when they're longer

---

## String Export / Import

For full control over every user-facing string in the generated CLI, specli supports a JSON-based export/import workflow. This is the **highest-priority** layer -- imported strings override both the spec and source enrichment.

### Export

```bash
specli build compile -p uspto-data-set-api -n uspto \
  --export-strings ./strings.json \
  --no-build
```

This produces a structured JSON file:

```json
{
  "info": {
    "title": "USPTO Data Set API",
    "description": "The Data Set API (DSAPI) allows the public users to discover and search USPTO exported data sets..."
  },
  "tags": {
    "metadata": "Find out about the data sets",
    "search": "Search a data set"
  },
  "operations": {
    "GET /": {
      "summary": "List available data sets",
      "description": "",
      "parameters": {}
    },
    "GET /{dataset}/{version}/fields": {
      "summary": "Provides the general information about the API and the list of fields that can be used to query the dataset.",
      "description": "This GET API returns the list of all the searchable field names...",
      "parameters": {
        "dataset": "Name of the dataset.",
        "version": "Version of the dataset."
      }
    },
    "POST /{dataset}/{version}/records": {
      "summary": "Provides search capability for the data set with the given search criteria.",
      "description": "This API is based on Solr/Lucene Search...",
      "parameters": {
        "version": "Version of the dataset.",
        "dataset": "Name of the dataset. In this case, the default value is oa_citations"
      }
    }
  }
}
```

### Edit

Edit the JSON manually, or pass it to an LLM:

> "Rewrite every summary to be concise and action-oriented. Rewrite every description to be clear for a developer who hasn't read the source code."

### Import

```bash
specli build compile -p uspto-data-set-api -n uspto \
  --import-strings ./strings.json
```

The priority chain (lowest to highest):

```
Raw OpenAPI spec  →  Source enrichment  →  Imported strings (wins)
```

---

## Using Generated Skills

specli generates [Claude Code](https://claude.ai/code) skill files that teach AI assistants how to use your API.

### Generate

```bash
# Standalone
specli skill generate -p uspto-data-set-api --output ./skills/uspto

# Or as part of a build
specli build compile -p uspto-data-set-api -n uspto --generate-skill ./skills/uspto
```

### What you get

| File | Purpose |
|------|---------|
| `SKILL.md` | Main skill file. Grouped commands, usage examples, quick start guide. |
| `references/api-reference.md` | Every endpoint with parameters, request bodies, and response schemas. |
| `references/auth-setup.md` | Step-by-step auth setup derived from the spec's security schemes. |

### Use with Claude Code

Copy the generated skill directory into your project's `.claude/skills/` or register it as a Claude Code skill:

```bash
cp -r ./skills/uspto ~/.claude/skills/uspto-skill
```

Claude Code will discover the skill and use it as context when working with your API -- it knows every endpoint, every parameter, and how to authenticate.

---

## Path Rules

Path rules transform API URL paths into clean CLI command hierarchies.

Configure in your profile's `path_rules` section:

```json
{
  "path_rules": {
    "auto_strip_prefix": true,
    "strip_prefix": "/api/v1",
    "keep": ["v2"],
    "skip_segments": ["internal"],
    "collapse": {
      "/api/v1/users/me": "profile"
    }
  }
}
```

| Field | Default | Description |
|-------|---------|-------------|
| `auto_strip_prefix` | `true` | Auto-detect and strip the common path prefix |
| `strip_prefix` | | Explicit prefix to strip (overrides auto) |
| `keep` | `[]` | Segments to preserve even if auto-stripped |
| `skip_segments` | `[]` | Segments to remove from all paths |
| `collapse` | `{}` | Map specific paths to flat command names |
| `include_prefix` | | Only include paths starting with this prefix (string or list) |

`include_prefix` is useful when a spec contains non-API paths (HTML pages, webhooks) that shouldn't become CLI commands. It accepts a string or a list:

```json
{
  "path_rules": {
    "strip_prefix": "/api",
    "include_prefix": "/api/"
  }
}
```

This includes only paths starting with `/api/`, strips the `/api` prefix, and produces clean command names. Paths like `/assets` or `/login` are excluded.

**Example transformations (USPTO spec):**

```
Spec paths:                               CLI commands:
/                                      →  root list
/{dataset}/{version}/fields            →  fields list <dataset> <version>
/{dataset}/{version}/records (POST)    →  records create <version> <dataset>
```

---

## Configuration

### Config locations

| Platform | Config | Cache | Data |
|----------|--------|-------|------|
| Linux/BSD | `~/.config/specli/` | `~/.cache/specli/` | `~/.local/share/specli/` |
| macOS/Windows | `~/.specli/` | `~/.specli/` | `~/.specli/` |

Overridable via `XDG_CONFIG_HOME`, `XDG_CACHE_HOME`, `XDG_DATA_HOME`.

### Config files

**Project config** (`./specli.json`): Created by `specli init`. Sets the default profile for the current directory.

**Profile** (`~/.config/specli/profiles/<name>.json`): Per-API configuration.

```json
{
  "name": "uspto-data-set-api",
  "spec": "examples/uspto-openapi.json",
  "base_url": "https://developer.uspto.gov/ds-api",
  "path_rules": {
    "auto_strip_prefix": true
  },
  "request": {
    "timeout": 30,
    "verify_ssl": true,
    "max_retries": 3
  }
}
```

### Environment variables

| Variable | Description |
|----------|-------------|
| `SPECLI_PROFILE` | Override default profile |
| `SPECLI_BASE_URL` | Override profile's base URL |

### Precedence (lowest to highest)

1. Global config defaults
2. Project config (`./specli.json`)
3. Environment variables (`SPECLI_*`)
4. CLI flags (`--profile`, etc.)

---

## Development Setup

```bash
# Clone
git clone https://github.com/nicholasgasior/specli.git
cd specli

# Create virtualenv and install with dev dependencies
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,build]"

# Run tests
pytest

# Run tests with coverage
pytest --cov=specli

# Type checking
mypy src/specli

# Linting
ruff check src/ tests/
```

### Project structure

```
src/specli/
  app.py                  # Typer app factory and entry point
  config.py               # XDG config, profiles, precedence
  models.py               # Pydantic models (profiles, specs, operations)
  output.py               # Rich/JSON/plain output formatting
  exceptions.py           # Exception hierarchy with exit codes
  parser/                 # OpenAPI spec loading, $ref resolution, extraction
  generator/              # Typer command tree builder, param mapper, path rules
  client/                 # Sync + async httpx clients with auth, retry, dry-run
  auth/                   # Auth plugin base, manager, credential store
  enrichment/             # Source code scanner, spec enricher, string I/O
  commands/               # Built-in commands: init, auth, config, inspect
  plugins/                # Plugin system + built-in plugins
    build/                # Binary compilation and package generation
    skill/                # Claude Code skill file generation
    completion/           # Shell tab-completion
    api_key/              # API key auth plugin
    bearer/               # Bearer token auth plugin
    basic/                # HTTP basic auth plugin
    oauth2_auth_code/     # OAuth2 authorization code + PKCE
    oauth2_client_credentials/  # OAuth2 client credentials
    openid_connect/       # OpenID Connect discovery + auth
    browser_login/        # Browser-based login (OAuth + simple modes)
    device_code/          # OAuth2 device code flow
    manual_token/         # Interactive token prompt
    api_key_gen/          # Remote API key provisioning
```

---

## License

[MIT](LICENSE)
