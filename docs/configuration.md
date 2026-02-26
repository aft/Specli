# Configuration

specli uses a layered configuration system with XDG-compliant paths, per-API profiles, project-local overrides, and environment variables.

## Config File Locations

### Linux / BSD (XDG Base Directory)

| Purpose | Path |
|---------|------|
| Global config | `$XDG_CONFIG_HOME/specli/config.json` (default: `~/.config/specli/config.json`) |
| Profiles | `$XDG_CONFIG_HOME/specli/profiles/<name>.json` |
| Cache | `$XDG_CACHE_HOME/specli/` (default: `~/.cache/specli/`) |
| Data / logs | `$XDG_DATA_HOME/specli/` (default: `~/.local/share/specli/`) |

### macOS / Windows

| Purpose | Path |
|---------|------|
| Global config | `~/.specli/config.json` |
| Profiles | `~/.specli/profiles/<name>.json` |
| Cache | `~/.specli/cache/` |
| Data / logs | `~/.specli/logs/` |

### Project-local config

| Purpose | Path |
|---------|------|
| Project config | `./specli.json` (current working directory) |

The project config is created automatically by `specli init`. It sets the default profile for the current project directory so you do not need to pass `--profile` every time.

## Global Config Schema

The global config file `config.json` controls defaults that apply across all profiles.

```json
{
  "default_profile": "myapi",
  "auto_select_single_profile": true,
  "output": {
    "format": "auto",
    "pager": true
  },
  "cache": {
    "enabled": true,
    "ttl_seconds": 300
  },
  "plugins": {
    "enabled": [],
    "disabled": []
  }
}
```

### Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `default_profile` | string or null | `null` | Name of the profile to use when `--profile` is not specified |
| `auto_select_single_profile` | bool | `true` | When true and only one profile exists, use it automatically without requiring `--profile` or `default_profile` |
| `output.format` | string | `"auto"` | Default output format: `auto`, `json`, `plain`, `rich`. `auto` selects `rich` for TTY, `plain` when piped |
| `output.pager` | bool | `true` | Use a pager (`$PAGER` or `less -FIRX`) for long output in interactive mode |
| `cache.enabled` | bool | `true` | Enable response caching |
| `cache.ttl_seconds` | int | `300` | Cache time-to-live in seconds |
| `plugins.enabled` | list of strings | `[]` | Explicit allowlist of plugin names to load. When non-empty, only these plugins are loaded |
| `plugins.disabled` | list of strings | `[]` | Blocklist of plugin names to skip. Ignored if `enabled` is non-empty |

### Managing global config

```bash
# View current config
specli config show

# Set a value (dot notation for nested keys)
specli config set default_profile myapi
specli config set output.format json
specli config set cache.ttl_seconds 600

# Reset to defaults
specli config reset
```

## Profile Schema

Each profile is stored as a separate JSON file in the profiles directory. Profiles hold per-API configuration: the spec location, base URL, auth settings, path rules, and request defaults.

```json
{
  "name": "myapi",
  "spec": "https://api.example.com/openapi.json",
  "base_url": "https://api.example.com/v3",
  "auth": {
    "type": "api_key",
    "header": "X-API-Key",
    "location": "header",
    "source": "env:MY_API_KEY"
  },
  "path_rules": {
    "auto_strip_prefix": true,
    "keep": [],
    "strip_prefix": null,
    "skip_segments": [],
    "collapse": {}
  },
  "request": {
    "timeout": 30,
    "verify_ssl": true,
    "max_retries": 3
  }
}
```

### Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | required | Profile identifier, used as the filename and in `--profile` |
| `spec` | string | required | URL or file path to the OpenAPI spec. Use `"-"` for stdin (init only) |
| `base_url` | string or null | `null` | Override the base URL from the spec's `servers` array |
| `auth` | object or null | `null` | Authentication configuration (see [auth.md](auth.md)) |
| `auth.type` | string | - | Auth type: `api_key`, `bearer`, `basic`, `oauth2_client_credentials`, `oauth2_auth_code`, `openid_connect` |
| `auth.header` | string or null | `null` | Header name for API key auth |
| `auth.param_name` | string or null | `null` | Query parameter name for API key auth |
| `auth.location` | string | `"header"` | Where to send credentials: `header`, `query`, `cookie` |
| `auth.source` | string | `"prompt"` | Credential source (see [auth.md](auth.md)) |
| `path_rules` | object | defaults | Path transformation rules (see [path-rules.md](path-rules.md)) |
| `path_rules.auto_strip_prefix` | bool | `true` | Auto-detect and strip the longest common path prefix |
| `path_rules.keep` | list of strings | `[]` | Segments to re-insert after stripping |
| `path_rules.strip_prefix` | string or null | `null` | Explicit prefix to strip (overrides auto-detection) |
| `path_rules.skip_segments` | list of strings | `[]` | Segments to remove wherever they appear |
| `path_rules.collapse` | dict | `{}` | Map full paths to flat command names |
| `request.timeout` | int | `30` | HTTP request timeout in seconds |
| `request.verify_ssl` | bool | `true` | Verify SSL certificates |
| `request.max_retries` | int | `3` | Maximum retry attempts for 5xx errors and connection failures |

## Precedence Chain

Configuration values are resolved through a precedence chain (highest wins):

```
1. CLI flags          --profile, --json, --plain, --dry-run, etc.
2. Environment vars   SPECLI_PROFILE, SPECLI_BASE_URL
3. Project config     ./specli.json
4. User config        ~/.config/specli/config.json
5. Defaults           Built-in Pydantic model defaults
```

### How precedence works in practice

Suppose you have:

- Global config with `default_profile: "staging"`
- Project config (`./specli.json`) with `default_profile: "dev"`
- Environment variable `SPECLI_PROFILE=production`

Then running `specli api users list` uses the **production** profile (env var wins over project config, which wins over global config).

Running `specli --profile local api users list` uses the **local** profile (CLI flag wins over everything).

## Environment Variables

| Variable | Overrides | Description |
|----------|-----------|-------------|
| `SPECLI_PROFILE` | `default_profile` | Profile name to use |
| `SPECLI_BASE_URL` | `profile.base_url` | Override the base URL |
| `NO_COLOR` | `--no-color` | Disable color output (any value) |
| `TERM=dumb` | `--no-color` | Disable color output |
| `PAGER` | pager command | Custom pager (default: `less -FIRX`) |

Additionally, any credential `source` can reference environment variables using the `env:VAR_NAME` syntax:

```json
{
  "auth": {
    "type": "bearer",
    "source": "env:GITHUB_TOKEN"
  }
}
```

## Project-Local Config

The project-local config file `./specli.json` is automatically created by `specli init`. It is a simple JSON object:

```json
{
  "default_profile": "myapi"
}
```

This file should be committed to version control so that everyone working in the project directory uses the same profile by default. Credentials are never stored in this file -- they live in the profile's `auth.source` field which points to an environment variable, file, or interactive prompt.

## Multiple Profiles

You can maintain multiple profiles for different APIs or environments:

```bash
# Initialize profiles for different environments
specli init --spec https://api.dev.example.com/openapi.json --name dev
specli init --spec https://api.staging.example.com/openapi.json --name staging
specli init --spec https://api.example.com/openapi.json --name production

# Switch between them
specli --profile dev api users list
specli --profile staging api users list
specli --profile production api users list

# Set a default
specli config set default_profile production
```

List all profiles and their auth status:

```bash
specli auth list
```

## Atomic Writes

All config and profile writes use an atomic write strategy: data is written to a temporary file in the same directory, flushed and fsynced, then renamed over the target file with `os.replace`. This guarantees that a crash or `Ctrl+C` during a write can never leave a corrupt config file on disk.
