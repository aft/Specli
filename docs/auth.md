# Authentication

specli uses a plugin-based authentication system. Auth plugins handle credential resolution for specific authentication types and inject the appropriate headers, query parameters, or cookies into every request.

## How Auth Works

The authentication flow has four stages:

1. **Detection** -- when you run `specli init`, the spec's `securitySchemes` are detected and reported
2. **Configuration** -- you set up auth for a profile using `specli auth login` (interactive) or `specli auth add` (non-interactive)
3. **Resolution** -- when a command runs, the configured credential `source` is resolved to an actual value (from env var, file, prompt, or keyring)
4. **Injection** -- the resolved credential is injected into the HTTP request as a header, query parameter, or cookie before sending

Auth configuration is stored in the profile JSON under the `auth` key. Credentials themselves are never stored in the profile -- only a `source` descriptor that tells specli where to find them at runtime.

## Built-in Auth Plugins

### API Key

Sends a credential value in a header, query parameter, or cookie.

**Profile configuration:**

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

**Fields:**

| Field | Required | Description |
|-------|----------|-------------|
| `type` | yes | Must be `"api_key"` |
| `header` | yes (or `param_name`) | Header name when `location` is `"header"` or `"cookie"` |
| `param_name` | yes (or `header`) | Query parameter name when `location` is `"query"` |
| `location` | no | Where to send the key: `"header"` (default), `"query"`, or `"cookie"` |
| `source` | yes | Credential source descriptor |

**Examples:**

```bash
# API key in a custom header
specli auth add myapi --type api_key --header X-API-Key --source env:MY_KEY

# API key as a query parameter
specli auth add myapi --type api_key --header api_key --source env:MY_KEY
```

When `location` is `"header"`, the key is sent as:
```
X-API-Key: <resolved_value>
```

When `location` is `"query"`, it is appended to the URL:
```
GET /users?api_key=<resolved_value>
```

When `location` is `"cookie"`, it is sent as a `Cookie` header:
```
Cookie: api_key=<resolved_value>
```

### Bearer Token

Sends a token in the `Authorization: Bearer` header.

**Profile configuration:**

```json
{
  "auth": {
    "type": "bearer",
    "source": "env:MY_TOKEN"
  }
}
```

**Fields:**

| Field | Required | Description |
|-------|----------|-------------|
| `type` | yes | Must be `"bearer"` |
| `source` | yes | Credential source descriptor |

**Example:**

```bash
specli auth add myapi --type bearer --source env:GITHUB_TOKEN
```

Produces the header:
```
Authorization: Bearer <resolved_token>
```

### HTTP Basic

Sends Base64-encoded `username:password` in the `Authorization: Basic` header.

**Profile configuration:**

```json
{
  "auth": {
    "type": "basic",
    "source": "env:MY_CREDENTIALS"
  }
}
```

The credential source must resolve to a string in `username:password` format (with a colon separator).

**Fields:**

| Field | Required | Description |
|-------|----------|-------------|
| `type` | yes | Must be `"basic"` |
| `source` | yes | Credential source descriptor. Must resolve to `"user:pass"` format |

**Example:**

```bash
# Credentials from environment variable
export MY_CREDENTIALS="admin:s3cret"
specli auth add myapi --type basic --source env:MY_CREDENTIALS

# Credentials from a file
echo "admin:s3cret" > ~/.secrets/myapi-creds
chmod 600 ~/.secrets/myapi-creds
specli auth add myapi --type basic --source file:~/.secrets/myapi-creds
```

Produces the header:
```
Authorization: Basic YWRtaW46czNjcmV0
```

## Credential Sources

The `source` field in auth configuration tells specli where to find the actual credential value at runtime. Four source types are supported:

### `env:VAR_NAME` -- Environment Variable

Reads the credential from the named environment variable. Fails with an error if the variable is not set.

```json
{"source": "env:MY_API_KEY"}
```

This is the recommended approach for CI/CD and production use. Set the variable in your shell, `.env` file, or secrets manager.

### `file:/path/to/file` -- File

Reads the credential from a file on disk. The file content is stripped of leading and trailing whitespace. Supports `~` expansion.

```json
{"source": "file:~/.secrets/api-token.txt"}
```

Make sure the file has restricted permissions (`chmod 600`).

### `prompt` -- Interactive Prompt

Prompts the user to enter the credential interactively using `getpass` (input is hidden). Fails with an error if stdin is not a TTY (e.g., in a pipe or CI).

```json
{"source": "prompt"}
```

This is the default source and is useful for one-off testing.

### `keyring:service:account` -- System Keyring

Reads the credential from the operating system's keyring (macOS Keychain, GNOME Keyring, Windows Credential Manager). Requires the `keyring` optional dependency:

```bash
pip install specli[keyring]
```

```json
{"source": "keyring:myapi:api_key"}
```

The format is `keyring:<service_name>:<account_name>`.

## Auth Commands

### `auth login <profile>` -- Interactive Setup

Reads the spec's security schemes and walks you through selecting one and providing a credential source:

```bash
specli auth login myapi
```

Output:

```
Available auth schemes:
  1. api_key (apiKey, header)
  2. petstore_auth (oauth2)
Auto-selected: api_key
Credential source (env:VAR, file:/path, or 'prompt'): env:PETSTORE_KEY
Auth configured for "myapi".
--> Test it: specli auth test myapi
```

### `auth add <profile>` -- Non-Interactive Setup

Configure auth directly with flags, useful for scripting:

```bash
specli auth add myapi --type api_key --header X-API-Key --source env:MY_KEY
specli auth add myapi --type bearer --source env:MY_TOKEN
specli auth add myapi --type basic --source file:~/.secrets/creds.txt
```

### `auth list` -- Show All Profiles

List all profiles with their auth type and source:

```bash
specli auth list
```

Output:

```
Profile     Auth Type  Source
myapi       api_key    env:MY_KEY
github      bearer     env:GITHUB_TOKEN
internal    basic      file:~/.secrets/creds.txt
staging     none       -
```

### `auth test <profile>` -- Test Auth

Make a test request (GET /) to verify that auth is working:

```bash
specli auth test myapi
```

### `auth remove <profile>` -- Remove Auth

Remove auth configuration from a profile:

```bash
specli auth remove myapi
# Prompts for confirmation unless --force is used
specli --force auth remove myapi
```

## Security Considerations

### Credentials are never stored in profiles

Profile JSON files contain only a `source` descriptor (e.g., `"env:MY_KEY"`), never the credential value itself. The value is resolved at runtime from the specified source. This means profile files are safe to commit to version control (though the project-local `specli.json` only contains the profile name, not auth details).

### File credential permissions

When using `file:` sources, ensure the credential file has restricted permissions:

```bash
chmod 600 ~/.secrets/api-token.txt
```

### Environment variable hygiene

Avoid hardcoding credentials in shell history. Use a `.env` file or a secrets manager:

```bash
# Good: load from .env
export $(grep -v '^#' .env | xargs)
specli api users list

# Bad: visible in shell history
MYKEY=sk-abc123 specli api users list
```

### Dry-run reveals auth headers

The `--dry-run` flag prints request details to stderr, including auth headers. Be aware of this when using dry-run in shared terminals or CI logs.

## Extending Auth

To add support for a new authentication type (e.g., OAuth2, mutual TLS), implement the `AuthPlugin` abstract base class:

```python
from specli.auth.base import AuthPlugin, AuthResult
from specli.models import AuthConfig


class MyAuthPlugin(AuthPlugin):
    @property
    def auth_type(self) -> str:
        return "my_custom_auth"

    def authenticate(self, auth_config: AuthConfig) -> AuthResult:
        # Resolve credentials and return headers/params/cookies
        ...

    def validate_config(self, auth_config: AuthConfig) -> list[str]:
        # Return a list of error messages (empty = valid)
        ...
```

Register it with the `AuthManager` in your plugin's `on_init` hook. See [CONTRIBUTING.md](../CONTRIBUTING.md) for the complete walkthrough.
