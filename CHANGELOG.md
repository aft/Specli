# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-04-20

### Added
- `api_login` auth plugin: interactive prompt + live `check_endpoint` verification + persistent credential store. User runs `login` once; credentials are reused until `logout`. Supports optional dual-credential (key + secret) via `secret_name`.
- Generated CLIs compiled/generated from profiles with `auth.type == "api_login"` now expose top-level `login` / `logout` subcommands. Login accepts `--key`, `--secret`, and `--no-verify` for CI.
- `-p` / profile reference now accepts a full filesystem path (absolute, relative, or `~`-expanded) in addition to a bare name. Saves round-trip back to the source path.
- `resolve_profile_ref()` helper and `Profile._source_path` private attribute for per-profile path tracking.

### Changed
- Interactive mapping of OpenAPI `apiKey` security schemes now defaults to `api_login` (was `api_key`). Existing profiles with `type: "api_key"` are unaffected.
- Mid-session 401 on `api_login` fails loudly and does not re-prompt; user must run `logout` + `login` again.

### Fixed
- Generated entry files are now written as UTF-8, preventing encoding errors on Windows when specs contain non-ASCII characters.
- Spec path in generated CLI docstrings is normalised to forward slashes so Windows paths do not collide with Python unicode escape syntax.

## [0.1.7] - 2026-02-26

### Fixed
- Strip Rich ANSI markup in build help tests (regex strip, color=False alone insufficient with rich_markup_mode)

## [0.1.6] - 2026-02-26

### Fixed
- Strip ANSI codes in build help tests (color=False on CliRunner)
- Include build plugin in wheel (anchor /build/ in .gitignore)
- Remove unused imports (ruff)

### Added
- GitHub Actions publish workflow (tag-triggered, OIDC trusted publisher)

## [0.1.5] - 2026-02-26

### Fixed
- Fixed Rich ANSI escape codes in build help tests (`color=False` on CliRunner)

## [0.1.4] - 2026-02-26

### Fixed
- Fixed `build/` gitignore pattern matching `src/specli/plugins/build/` — anchored to root `/build/`
- Removed 15 unused imports flagged by ruff (12 auto-fixed, 3 explicit re-exports)
- Added GitHub Actions publish workflow for automated PyPI releases on version tags

## [0.1.0] - 2026-02-25

### Added
- OpenAPI 3.0/3.1 spec parsing from URL, file, or stdin (JSON and YAML)
- `$ref` resolution with circular reference detection (internal refs only)
- Dynamic Typer CLI generation from spec operations
- Path rules engine with auto-strip prefix, keep, skip_segments, strip_prefix, and collapse
- HTTP method to CLI verb mapping (GET list/get, POST create, PUT update, DELETE delete)
- Parameter mapping: path params to positional arguments, query/header/cookie to `--options`
- `--body` option with `@filename` file reference support
- Built-in auth plugins: API key (header/query/cookie), bearer token, HTTP basic
- Credential source resolution: `env:VAR`, `file:/path`, `prompt`, `keyring:service:account`
- Plugin system with entry-point-based discovery and enable/disable configuration
- Pre-request, post-response, and error hooks via `HookRunner`
- Synchronous httpx client with auth injection, retry with exponential backoff, and dry-run mode
- Asynchronous httpx client mirroring the sync API
- Rich/JSON/plain output modes with automatic TTY detection
- stdout/stderr discipline following clig.dev conventions
- `NO_COLOR` and `TERM=dumb` environment variable support
- Pager support (`$PAGER` with `less -FIRX` fallback)
- XDG-compliant configuration on Linux/BSD (`~/.config/specli/`)
- macOS/Windows fallback to `~/.specli/`
- Atomic config file writes (temp file + rename)
- Profile system with per-API configuration (spec URL, base URL, auth, path rules, request settings)
- Config precedence chain: CLI flags > env vars > project config > user config > defaults
- Project-local config via `./specli.json`
- Built-in commands: `init`, `auth` (login, add, list, test, remove), `config` (show, set, reset), `inspect` (paths, schemas, auth, info)
- Plugins: `build` (compile/generate), `completion` (install/show), `skill` (generate)
- Claude Code skill generation with Jinja2 templates (SKILL.md, api-reference.md, auth-setup.md)
- Structured exit codes (0-10) following clig.dev conventions
- Custom exception hierarchy with per-type exit codes
- Crash log writing to `$XDG_DATA_HOME/specli/logs/`
- Global `--version`, `--profile`, `--json`, `--plain`, `--no-color`, `--quiet`, `--verbose`, `--dry-run`, `--force`, `--no-input`, `--output` flags
- Comprehensive test suite with 725+ tests
