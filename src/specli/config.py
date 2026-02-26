"""Configuration management with XDG paths, atomic writes, and precedence resolution.

This module handles all persistent configuration for specli:

* **Directory layout** -- XDG Base Directory compliant on Linux/BSD,
  ``~/.specli/`` on macOS and Windows. See :func:`get_config_dir`,
  :func:`get_cache_dir`, :func:`get_data_dir`, :func:`get_profiles_dir`.
* **Global config** -- A single :class:`~specli.models.GlobalConfig`
  JSON file storing defaults (output format, cache settings, plugins).
* **Profiles** -- One JSON file per API target, each deserialised into a
  :class:`~specli.models.Profile`. Managed via :func:`load_profile`,
  :func:`save_profile`, :func:`delete_profile`.
* **Precedence resolution** -- :func:`resolve_config` merges CLI flags,
  environment variables, project-local config, and global config into the
  final effective configuration.
* **Credential resolution** -- :func:`resolve_credential` reads secrets
  from env vars, files, interactive prompts, or a credential store.

All file writes use an atomic temp-file-then-rename strategy
(:func:`_atomic_write`) to prevent data loss on crash or power failure.
"""

from __future__ import annotations

import getpass
import json
import os
import platform
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

from specli.exceptions import ConfigError
from specli.models import GlobalConfig, Profile

_APP_NAME = "specli"
_CONFIG_FILENAME = "config.json"
_PROJECT_CONFIG_FILENAME = "specli.json"


# --- XDG path resolution ---


def _is_xdg_platform() -> bool:
    """Return True if the platform supports XDG Base Directory spec (Linux/FreeBSD)."""
    return platform.system() == "Linux" or platform.system().endswith("BSD")


def _fallback_base_dir() -> Path:
    """Fallback base directory for non-XDG platforms (macOS, Windows)."""
    return Path.home() / f".{_APP_NAME}"


def _xdg_base(env_var: str, default_segments: tuple[str, ...]) -> Path:
    """Resolve an XDG base directory from an env var with fallback segments under $HOME."""
    env_value = os.environ.get(env_var, "")
    if env_value:
        return Path(env_value)
    base = Path.home()
    for seg in default_segments:
        base = base / seg
    return base


def get_config_dir() -> Path:
    """Return the configuration directory, creating it if necessary.

    On Linux/BSD: ``$XDG_CONFIG_HOME/specli/`` (default ``~/.config/specli/``).
    On macOS/Windows: ``~/.specli/``.

    Returns:
        Absolute path to the configuration directory (guaranteed to exist).
    """
    if _is_xdg_platform():
        base = _xdg_base("XDG_CONFIG_HOME", (".config",))
        path = base / _APP_NAME
    else:
        path = _fallback_base_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_cache_dir() -> Path:
    """Return the cache directory, creating it if necessary.

    Used to store HTTP response caches. Cached data can be safely deleted
    at any time.

    On Linux/BSD: ``$XDG_CACHE_HOME/specli/`` (default ``~/.cache/specli/``).
    On macOS/Windows: ``~/.specli/cache/``.

    Returns:
        Absolute path to the cache directory (guaranteed to exist).
    """
    if _is_xdg_platform():
        base = _xdg_base("XDG_CACHE_HOME", (".cache",))
        path = base / _APP_NAME
    else:
        path = _fallback_base_dir() / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_data_dir() -> Path:
    """Return the data directory (crash logs, credentials), creating it if necessary.

    On Linux/BSD: ``$XDG_DATA_HOME/specli/`` (default ``~/.local/share/specli/``).
    On macOS/Windows: ``~/.specli/logs/``.

    Returns:
        Absolute path to the data directory (guaranteed to exist).
    """
    if _is_xdg_platform():
        base = _xdg_base("XDG_DATA_HOME", (".local", "share"))
        path = base / _APP_NAME
    else:
        path = _fallback_base_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_profiles_dir() -> Path:
    """Return the profiles directory (``<config_dir>/profiles/``), creating it if necessary.

    Returns:
        Absolute path to the profiles directory (guaranteed to exist).
    """
    path = get_config_dir() / "profiles"
    path.mkdir(parents=True, exist_ok=True)
    return path


# --- Atomic file writes ---


def _atomic_write(path: Path, data: str) -> None:
    """Write data to file atomically using temp file + rename.

    The temporary file is created in the same directory as *path* so that
    ``os.replace`` is guaranteed to be an atomic rename on POSIX systems.
    On success the temp file is renamed over *path*; on any failure the temp
    file is cleaned up.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    fd = None
    tmp_path: Optional[str] = None
    try:
        fd = tempfile.NamedTemporaryFile(
            mode="w",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
            encoding="utf-8",
        )
        tmp_path = fd.name
        fd.write(data)
        fd.flush()
        os.fsync(fd.fileno())
        fd.close()
        fd = None  # prevent double-close in finally
        os.replace(tmp_path, path)
    except BaseException:
        # Clean up the temp file on any error (including KeyboardInterrupt).
        if fd is not None:
            fd.close()
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise


# --- Global config ---


def _global_config_path() -> Path:
    """Path to the global config file."""
    return get_config_dir() / _CONFIG_FILENAME


def load_global_config() -> GlobalConfig:
    """Load the global configuration from the XDG config directory.

    Returns:
        The deserialised :class:`~specli.models.GlobalConfig`. If the
        file does not exist, a default instance is returned.

    Raises:
        ConfigError: If the file exists but contains invalid JSON or fails
            Pydantic validation.
    """
    path = _global_config_path()
    if not path.is_file():
        return GlobalConfig()
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        return GlobalConfig.model_validate(data)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ConfigError(f"Invalid global config at {path}: {exc}") from exc


def save_global_config(config: GlobalConfig) -> None:
    """Persist the global configuration atomically to disk.

    Args:
        config: The configuration to save.
    """
    data = config.model_dump(mode="json")
    _atomic_write(_global_config_path(), json.dumps(data, indent=2) + "\n")


# --- Profiles ---


def _profile_path(name: str) -> Path:
    """Path to a named profile's JSON file."""
    return get_profiles_dir() / f"{name}.json"


def list_profiles() -> list[str]:
    """Return all profile names found in the profiles directory, sorted alphabetically.

    Returns:
        A list of profile name strings (file stems, without ``.json``).
    """
    profiles_dir = get_profiles_dir()
    return sorted(
        p.stem for p in profiles_dir.glob("*.json") if p.is_file()
    )


def load_profile(name: str) -> Profile:
    """Load and validate a profile from disk.

    Args:
        name: Profile name (corresponds to ``<name>.json`` in the profiles
            directory).

    Returns:
        The deserialised :class:`~specli.models.Profile`.

    Raises:
        ConfigError: If the profile file does not exist, contains invalid
            JSON, or fails Pydantic validation.
    """
    path = _profile_path(name)
    if not path.is_file():
        raise ConfigError(f"Profile '{name}' not found at {path}")
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        return Profile.model_validate(data)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ConfigError(f"Invalid profile '{name}' at {path}: {exc}") from exc


def save_profile(profile: Profile) -> None:
    """Persist a profile atomically to the profiles directory.

    Args:
        profile: The profile to save. The file name is derived from
            ``profile.name``.
    """
    data = profile.model_dump(mode="json")
    _atomic_write(_profile_path(profile.name), json.dumps(data, indent=2) + "\n")


def delete_profile(name: str) -> None:
    """Delete a profile's JSON file from disk.

    Args:
        name: Profile name to delete.

    Raises:
        ConfigError: If the profile does not exist.
    """
    path = _profile_path(name)
    if not path.is_file():
        raise ConfigError(f"Profile '{name}' not found at {path}")
    path.unlink()


def profile_exists(name: str) -> bool:
    """Check whether a profile file exists on disk.

    Args:
        name: Profile name to check.

    Returns:
        ``True`` if the profile's JSON file exists, ``False`` otherwise.
    """
    return _profile_path(name).is_file()


# --- Project-local config ---


def load_project_config() -> Optional[dict[str, Any]]:
    """Load project-local configuration from ``./specli.json``.

    Project-local config sits between global config and environment variables
    in the precedence chain. It typically sets ``default_profile`` so that
    a repository can pin which API profile to use.

    Returns:
        The parsed JSON as a dict, or ``None`` if the file does not exist.

    Raises:
        ConfigError: If the file exists but contains invalid JSON.
    """
    path = Path.cwd() / _PROJECT_CONFIG_FILENAME
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
        return json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ConfigError(f"Invalid project config at {path}: {exc}") from exc


# --- Precedence resolution ---


def resolve_config(
    cli_profile: Optional[str] = None,
    cli_base_url: Optional[str] = None,
    cli_format: Optional[str] = None,
) -> tuple[GlobalConfig, Optional[Profile]]:
    """Resolve config with full precedence chain.

    Precedence (high to low):
        1. CLI flags (``cli_profile``, ``cli_base_url``, ``cli_format``)
        2. Environment variables (``SPECLI_PROFILE``, ``SPECLI_BASE_URL``)
        3. Project config (``./specli.json``)
        4. User config (``~/.config/specli/config.json``)
        5. Defaults

    Returns:
        A tuple of ``(global_config, active_profile_or_None)``.
    """
    # 5 + 4. Load base global config (fills in defaults automatically)
    global_cfg = load_global_config()

    # 3. Layer in project-local config
    project = load_project_config()
    project_profile_name: Optional[str] = None
    if project is not None:
        project_profile_name = project.get("default_profile")

    # Determine profile name through precedence chain
    # 4. Global config default_profile (lowest precedence)
    resolved_profile_name: Optional[str] = global_cfg.default_profile
    # 3. Project-local default_profile
    if project_profile_name is not None:
        resolved_profile_name = project_profile_name
    # 2. Environment variable
    env_profile = os.environ.get("SPECLI_PROFILE")
    if env_profile:
        resolved_profile_name = env_profile
    # 1. CLI flag (highest precedence)
    if cli_profile is not None:
        resolved_profile_name = cli_profile

    # Auto-select if only one profile and the setting is enabled
    if resolved_profile_name is None and global_cfg.auto_select_single_profile:
        profiles = list_profiles()
        if len(profiles) == 1:
            resolved_profile_name = profiles[0]

    # Load the resolved profile
    profile: Optional[Profile] = None
    if resolved_profile_name is not None:
        profile = load_profile(resolved_profile_name)

    # Apply CLI overrides to the profile
    if profile is not None:
        # base_url: CLI > env > profile
        env_base_url = os.environ.get("SPECLI_BASE_URL")
        if cli_base_url is not None:
            profile.base_url = cli_base_url
        elif env_base_url:
            profile.base_url = env_base_url

    # Apply output format override
    if cli_format is not None:
        global_cfg.output.format = cli_format

    return global_cfg, profile


# --- Credential source resolution ---


def get_credentials_dir() -> Path:
    """Return the credentials directory, creating it if necessary.

    Stored under the data directory as ``get_data_dir() / "credentials"``.

    Returns:
        Absolute path to the credentials directory (guaranteed to exist).
    """
    path = get_data_dir() / "credentials"
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_credential(source: str) -> str:
    """Resolve a credential from its source descriptor.

    Supported formats:
        - ``"env:VAR_NAME"`` -- reads ``os.environ["VAR_NAME"]``
        - ``"file:/path/to/file"`` -- reads file content, stripped of whitespace
        - ``"prompt"`` -- prompts user interactively (requires a TTY)
        - ``"store:PROFILE"`` -- reads from credential store for the named profile
        - ``"keyring:service:account"`` -- reads from system keyring (requires plugin)

    Args:
        source: The source descriptor string.

    Returns:
        The resolved credential string.

    Raises:
        ConfigError: If the source can't be resolved.
    """
    if source.startswith("env:"):
        var_name = source[4:]
        value = os.environ.get(var_name)
        if value is None:
            raise ConfigError(
                f"Environment variable '{var_name}' is not set (source: {source})"
            )
        return value

    if source.startswith("file:"):
        file_path = source[5:]
        path = Path(file_path).expanduser()
        if not path.is_file():
            raise ConfigError(f"Credential file not found: {path} (source: {source})")
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ConfigError(f"Cannot read credential file {path}: {exc}") from exc

    if source == "prompt":
        if not sys.stdin.isatty():
            raise ConfigError(
                "Cannot prompt for credentials: stdin is not a TTY (source: prompt)"
            )
        return getpass.getpass("Enter credential: ")

    if source.startswith("store:"):
        profile_name = source[6:]
        from specli.auth.credential_store import CredentialStore

        store = CredentialStore(profile_name)
        if not store.is_valid():
            raise ConfigError(
                f"No valid credential in store for profile '{profile_name}' "
                f"(source: {source})"
            )
        entry = store.load()
        assert entry is not None  # is_valid() guarantees this
        return entry.credential

    if source.startswith("keyring:"):
        raise ConfigError("Keyring support requires the keyring plugin")

    raise ConfigError(f"Unknown credential source format: {source}")
