"""Tests for the credential store."""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timedelta, timezone

import pytest

from specli.auth.credential_store import CredentialEntry, CredentialStore


@pytest.fixture()
def store(tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> CredentialStore:
    """Create a CredentialStore that writes to a temp directory."""
    # Point get_data_dir() to tmp_path so files land in a disposable location
    monkeypatch.setattr(
        "specli.auth.credential_store.get_data_dir",
        lambda: tmp_path,  # type: ignore[union-attr]
    )
    return CredentialStore("test-profile")


class TestCredentialEntry:
    def test_minimal(self) -> None:
        entry = CredentialEntry(auth_type="manual_token", credential="abc123")
        assert entry.auth_type == "manual_token"
        assert entry.credential == "abc123"
        assert entry.credential_name is None
        assert entry.expires_at is None
        assert entry.metadata == {}

    def test_full(self) -> None:
        expires = datetime(2030, 1, 1, tzinfo=timezone.utc)
        entry = CredentialEntry(
            auth_type="browser_login",
            credential="tok_xyz",
            credential_name="session_token",
            expires_at=expires,
            metadata={"scopes": ["read", "write"]},
        )
        assert entry.credential_name == "session_token"
        assert entry.expires_at == expires
        assert entry.metadata == {"scopes": ["read", "write"]}

    def test_roundtrip_json(self) -> None:
        entry = CredentialEntry(
            auth_type="api_key_gen",
            credential="key_123",
            expires_at=datetime(2030, 6, 15, 12, 0, tzinfo=timezone.utc),
        )
        data = json.loads(entry.model_dump_json())
        restored = CredentialEntry.model_validate(data)
        assert restored.auth_type == entry.auth_type
        assert restored.credential == entry.credential


class TestCredentialStore:
    def test_load_returns_none_when_no_file(self, store: CredentialStore) -> None:
        assert store.load() is None

    def test_is_valid_returns_false_when_no_file(self, store: CredentialStore) -> None:
        assert store.is_valid() is False

    def test_save_and_load(self, store: CredentialStore) -> None:
        entry = CredentialEntry(auth_type="manual_token", credential="secret")
        store.save(entry)

        loaded = store.load()
        assert loaded is not None
        assert loaded.auth_type == "manual_token"
        assert loaded.credential == "secret"

    def test_is_valid_no_expiry(self, store: CredentialStore) -> None:
        entry = CredentialEntry(auth_type="api_key_gen", credential="key1")
        store.save(entry)
        assert store.is_valid() is True

    def test_is_valid_future_expiry(self, store: CredentialStore) -> None:
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        entry = CredentialEntry(
            auth_type="browser_login", credential="tok", expires_at=future
        )
        store.save(entry)
        assert store.is_valid() is True

    def test_is_valid_past_expiry(self, store: CredentialStore) -> None:
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        entry = CredentialEntry(
            auth_type="browser_login", credential="tok", expires_at=past
        )
        store.save(entry)
        assert store.is_valid() is False

    def test_is_valid_naive_expiry_treated_as_utc(self, store: CredentialStore) -> None:
        """A naive datetime should be treated as UTC."""
        future = datetime.utcnow() + timedelta(hours=1)  # noqa: DTZ003
        entry = CredentialEntry(
            auth_type="browser_login", credential="tok", expires_at=future
        )
        store.save(entry)
        assert store.is_valid() is True

    def test_clear(self, store: CredentialStore) -> None:
        entry = CredentialEntry(auth_type="manual_token", credential="x")
        store.save(entry)
        assert store.is_valid() is True

        store.clear()
        assert store.load() is None
        assert store.is_valid() is False

    def test_clear_nonexistent(self, store: CredentialStore) -> None:
        """Clearing when no file exists should not raise."""
        store.clear()  # no-op

    def test_file_permissions(self, store: CredentialStore) -> None:
        """Credential files should have 0o600 permissions."""
        entry = CredentialEntry(auth_type="manual_token", credential="secret")
        store.save(entry)

        mode = os.stat(store.path).st_mode
        file_perms = stat.S_IMODE(mode)
        assert file_perms == 0o600

    def test_overwrite(self, store: CredentialStore) -> None:
        store.save(CredentialEntry(auth_type="a", credential="first"))
        store.save(CredentialEntry(auth_type="b", credential="second"))

        loaded = store.load()
        assert loaded is not None
        assert loaded.auth_type == "b"
        assert loaded.credential == "second"

    def test_corrupted_file_returns_none(self, store: CredentialStore) -> None:
        """If the credential file is corrupted, load() returns None gracefully."""
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text("not valid json {{{", encoding="utf-8")
        assert store.load() is None

    def test_separate_profiles(
        self, tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Different profiles should not interfere with each other."""
        monkeypatch.setattr(
            "specli.auth.credential_store.get_data_dir",
            lambda: tmp_path,  # type: ignore[union-attr]
        )
        store_a = CredentialStore("profile-a")
        store_b = CredentialStore("profile-b")

        store_a.save(CredentialEntry(auth_type="a", credential="cred-a"))
        store_b.save(CredentialEntry(auth_type="b", credential="cred-b"))

        loaded_a = store_a.load()
        loaded_b = store_b.load()
        assert loaded_a is not None and loaded_a.credential == "cred-a"
        assert loaded_b is not None and loaded_b.credential == "cred-b"
