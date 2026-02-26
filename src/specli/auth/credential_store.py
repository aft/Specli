"""Persistent credential store scoped per profile.

Stores credentials in ``~/.local/share/specli/credentials/<profile>.json``
(XDG) or the platform-equivalent directory.  Files are written atomically
via :func:`tempfile.NamedTemporaryFile` and ``os.replace`` with ``0o600``
permissions so that secrets are never world-readable, even momentarily.

Each profile maps to exactly one JSON file.  The file contains a serialised
:class:`CredentialEntry` produced by whichever :class:`~specli.auth.base.AuthPlugin`
authenticated the profile.

See Also:
    :class:`~specli.auth.base.AuthPlugin` -- plugins that produce credential entries.
    :class:`~specli.auth.manager.AuthManager` -- orchestrates authentication.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from specli.config import get_data_dir


class CredentialEntry(BaseModel):
    """A single stored credential produced by an auth plugin.

    Instances are serialised to JSON and persisted by :class:`CredentialStore`.
    The :attr:`auth_type` field records which plugin created the entry so that
    it can be refreshed or validated later.

    Attributes:
        auth_type: Identifier of the auth plugin that created this entry
            (e.g. ``"bearer"``, ``"oauth2_auth_code"``).
        credential: The secret value -- a token, API key, cookie, etc.
        credential_name: Optional name of the HTTP header, cookie, or query
            parameter this credential should be injected as.
        expires_at: Optional UTC expiry time.  ``None`` means the credential
            never expires.
        metadata: Arbitrary plugin-specific context such as ``token_type``,
            ``scopes``, or ``refresh_token``.
    """

    auth_type: str = Field(description="Auth plugin type that created this entry")
    credential: str = Field(description="The credential value (token, key, cookie, etc.)")
    credential_name: Optional[str] = Field(
        default=None,
        description="Name of the header, cookie, or parameter this credential is for",
    )
    expires_at: Optional[datetime] = Field(
        default=None,
        description="When this credential expires (None = never)",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Plugin-specific context (e.g. token_type, scopes)",
    )


def _credentials_dir() -> Path:
    """Return the credentials directory, creating it if needed."""
    path = get_data_dir() / "credentials"
    path.mkdir(parents=True, exist_ok=True)
    return path


class CredentialStore:
    """Read/write credentials for a single profile.

    Each profile gets its own JSON file under the credentials directory
    (typically ``~/.local/share/specli/credentials/<profile>.json``).

    All writes are atomic: content is written to a temporary file in the
    same directory, fsynced, then renamed into place.  This prevents
    partial writes from corrupting stored credentials.

    Args:
        profile_name: The profile identifier used to derive the file name.

    Example::

        store = CredentialStore("my-api")
        store.save(CredentialEntry(auth_type="bearer", credential="tok123"))
        entry = store.load()
        assert entry.credential == "tok123"
    """

    def __init__(self, profile_name: str) -> None:
        self._profile_name = profile_name
        self._path = _credentials_dir() / f"{profile_name}.json"

    @property
    def path(self) -> Path:
        """The filesystem path to this profile's credential file."""
        return self._path

    def save(self, entry: CredentialEntry) -> None:
        """Persist a credential entry atomically with ``0o600`` permissions.

        Args:
            entry: The credential entry to write.

        Raises:
            OSError: If the file cannot be written (permissions, disk full, etc.).
        """
        data = entry.model_dump(mode="json")
        text = json.dumps(data, indent=2) + "\n"

        self._path.parent.mkdir(parents=True, exist_ok=True)

        fd = None
        tmp_path: Optional[str] = None
        try:
            fd = tempfile.NamedTemporaryFile(
                mode="w",
                dir=self._path.parent,
                prefix=f".{self._path.name}.",
                suffix=".tmp",
                delete=False,
                encoding="utf-8",
            )
            tmp_path = fd.name
            # Set restrictive permissions before writing content
            os.chmod(tmp_path, 0o600)
            fd.write(text)
            fd.flush()
            os.fsync(fd.fileno())
            fd.close()
            fd = None
            os.replace(tmp_path, self._path)
        except BaseException:
            if fd is not None:
                fd.close()
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            raise

    def load(self) -> Optional[CredentialEntry]:
        """Load the stored credential entry from disk.

        Returns:
            The deserialised :class:`CredentialEntry`, or ``None`` if the
            file does not exist or cannot be parsed.
        """
        if not self._path.is_file():
            return None
        try:
            text = self._path.read_text(encoding="utf-8")
            data = json.loads(text)
            return CredentialEntry.model_validate(data)
        except (json.JSONDecodeError, ValueError, OSError):
            return None

    def is_valid(self) -> bool:
        """Check whether a non-expired credential exists on disk.

        Returns:
            ``True`` if a credential file exists, can be loaded, and has
            not yet expired.  ``False`` otherwise.
        """
        entry = self.load()
        if entry is None:
            return False
        if entry.expires_at is None:
            return True
        now = datetime.now(timezone.utc)
        expires = entry.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return now < expires

    def clear(self) -> None:
        """Delete the stored credential file if it exists.

        This is a no-op when the file has already been removed.
        """
        if self._path.is_file():
            self._path.unlink()
