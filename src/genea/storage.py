from __future__ import annotations

import sqlite3
import base64
import binascii
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .workspace import ensure_workspace, research_root


def connect_derivative_cache(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    """Open a rebuildable local SQLite cache without selecting canonical storage.

    This is intentionally separate from ``configured_database_backend``: callers
    must provide the exact derivative path and must never pass the research
    database or a private encrypted database.
    """

    path = Path(path).expanduser().resolve()
    if path.name in {"genea.sqlite3", "genea.encrypted.sqlite3"}:
        raise ValueError("Kanoniczna baza nie może zostać otwarta jako cache pochodny")
    if readonly:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    return sqlite3.connect(path)


class StorageLockedError(RuntimeError):
    """Raised when an encrypted backend is used before its key is available."""


@runtime_checkable
class KeyProvider(Protocol):
    """Return a database key without exposing it through configuration or logs."""

    def load_key(self) -> bytes | None:
        """Return key material for the current process, or ``None`` when unused."""

    def is_locked(self) -> bool:
        """Return whether the protected storage is currently locked."""

    def lock(self) -> None:
        """Discard any key material cached by the provider."""


@dataclass(frozen=True)
class NoEncryptionKeyProvider:
    """Explicit marker for the current plaintext compatibility backend."""

    def load_key(self) -> None:
        return None

    def is_locked(self) -> bool:
        return False

    def lock(self) -> None:
        return None


@dataclass
class InMemoryKeyProvider:
    """Ephemeral key holder for one unlocked process or an isolated test."""

    _key: bytearray | None = field(default=None, repr=False)

    @classmethod
    def unlocked(cls, key: bytes) -> "InMemoryKeyProvider":
        if len(key) != 32:
            raise ValueError("Klucz danych musi mieć dokładnie 256 bitów")
        return cls(bytearray(key))

    def load_key(self) -> bytes | None:
        return bytes(self._key) if self._key is not None else None

    def is_locked(self) -> bool:
        return self._key is None

    def lock(self) -> None:
        if self._key is not None:
            self._key[:] = b"\x00" * len(self._key)
        self._key = None


@dataclass
class MountedSecretKeyProvider:
    """Load a base64-encoded 256-bit key from a mounted runtime secret.

    The path is configuration, not a secret. The file contents are never
    returned in descriptions, exceptions or representations. This provider is
    intended for Docker/Podman secrets and other read-only secret mounts.
    """

    path: Path
    _key: bytearray | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.path = Path(self.path).expanduser()
        if not self.path.is_absolute():
            raise ValueError("Ścieżka montowanego sekretu musi być bezwzględna")

    def unlock(self) -> None:
        try:
            encoded = self.path.read_bytes()
        except OSError as error:
            raise StorageLockedError("Nie można odczytać montowanego sekretu") from error
        if len(encoded) > 256:
            raise StorageLockedError("Montowany sekret ma nieprawidłowy format")
        try:
            key = base64.b64decode(encoded.strip(), validate=True)
        except (ValueError, binascii.Error) as error:
            raise StorageLockedError("Montowany sekret ma nieprawidłowy format") from error
        if len(key) != 32:
            raise StorageLockedError("Montowany sekret ma nieprawidłową długość")
        self._key = bytearray(key)

    def load_key(self) -> bytes | None:
        return bytes(self._key) if self._key is not None else None

    def is_locked(self) -> bool:
        return self._key is None

    def lock(self) -> None:
        if self._key is not None:
            self._key[:] = b"\x00" * len(self._key)
        self._key = None


@dataclass
class MacOSKeychainProvider:
    """Load a data key from macOS Keychain only into the current process.

    The provider never accepts a key through command-line arguments or an
    environment variable.  ``provision`` is an administrative operation used
    by the future guarded ``private-init`` workflow.
    """

    root: Path
    account: str = "database-key"
    keyring_adapter: Any | None = field(default=None, repr=False)
    _key: bytearray | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.root = Path(self.root).expanduser().resolve()

    @property
    def service(self) -> str:
        workspace_id = hashlib.sha256(str(self.root).encode("utf-8")).hexdigest()[:16]
        return f"pl.genea.local.{workspace_id}"

    def _adapter(self):  # type: ignore[no-untyped-def]
        if self.keyring_adapter is not None:
            return self.keyring_adapter
        try:
            import keyring
        except ImportError as error:
            raise RuntimeError(
                "Brak opcjonalnej zależności keyring; uruchom `uv sync --extra private`."
            ) from error
        return keyring

    def provision(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("Klucz danych musi mieć dokładnie 256 bitów")
        encoded = base64.b64encode(key).decode("ascii")
        self._adapter().set_password(self.service, self.account, encoded)
        self._key = bytearray(key)

    def unlock(self) -> None:
        encoded = self._adapter().get_password(self.service, self.account)
        if not encoded:
            raise StorageLockedError("Brak klucza danych w macOS Keychain")
        try:
            key = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as error:
            raise StorageLockedError("Klucz danych w Keychain ma nieprawidłowy format") from error
        if len(key) != 32:
            raise StorageLockedError("Klucz danych w Keychain ma nieprawidłową długość")
        self._key = bytearray(key)

    def load_key(self) -> bytes | None:
        return bytes(self._key) if self._key is not None else None

    def is_locked(self) -> bool:
        return self._key is None

    def lock(self) -> None:
        if self._key is not None:
            self._key[:] = b"\x00" * len(self._key)
        self._key = None


class Argon2PasswordVerifier:
    """Create and verify an Argon2id password verifier without storing secrets."""

    @staticmethod
    def _hasher():  # type: ignore[no-untyped-def]
        try:
            from argon2 import PasswordHasher
        except ImportError as error:
            raise RuntimeError(
                "Brak opcjonalnej zależności argon2-cffi; uruchom `uv sync --extra private`."
            ) from error
        return PasswordHasher()

    def create(self, password: str) -> str:
        if not password:
            raise ValueError("Hasło nie może być puste")
        return self._hasher().hash(password)

    def verify(self, encoded_hash: str, password: str) -> bool:
        try:
            return bool(self._hasher().verify(encoded_hash, password))
        except Exception as error:
            try:
                from argon2.exceptions import VerificationError
            except ImportError:
                raise
            if isinstance(error, VerificationError):
                return False
            raise


@runtime_checkable
class DatabaseBackend(Protocol):
    """Storage facade used by all production database access."""

    @property
    def path(self) -> Path: ...

    @property
    def encrypted(self) -> bool: ...

    @property
    def driver(self) -> str: ...

    def connect(self, *, readonly: bool = False) -> sqlite3.Connection: ...

    def backup(self, destination: Path) -> Path: ...

    def integrity_report(self) -> dict[str, object]: ...

    def describe(self) -> dict[str, object]: ...


@dataclass(frozen=True)
class PlaintextSQLiteBackend:
    """Current SQLite implementation behind the future encryption boundary.

    This backend intentionally does not accept a key.  It exists to make the
    storage boundary explicit before SQLCipher is selected and the canonical
    database is migrated on a verified copy.
    """

    root: Path
    key_provider: KeyProvider = field(default_factory=NoEncryptionKeyProvider)

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root).expanduser().resolve())
        if not isinstance(self.key_provider, NoEncryptionKeyProvider):
            raise ValueError("Backend plaintext nie może przyjmować klucza szyfrującego")

    @property
    def path(self) -> Path:
        return research_root(self.root) / "genea.sqlite3"

    @property
    def encrypted(self) -> bool:
        return False

    @property
    def driver(self) -> str:
        return "stdlib-sqlite3"

    def connect(self, *, readonly: bool = False) -> sqlite3.Connection:
        if readonly:
            if not self.path.is_file():
                raise FileNotFoundError(f"Nie znaleziono bazy: {self.path}")
            connection = sqlite3.connect(
                self.path.resolve().as_uri() + "?mode=ro",
                uri=True,
            )
            connection.execute("PRAGMA query_only = ON")
        else:
            ensure_workspace(self.root)
            connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def backup(self, destination: Path) -> Path:
        destination = Path(destination).expanduser().resolve()
        if destination.exists():
            raise FileExistsError(f"Plik backupu już istnieje: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.connect(readonly=True) as source, sqlite3.connect(destination) as target:
                source.backup(target)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return destination

    def integrity_report(self) -> dict[str, object]:
        with self.connect(readonly=True) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_keys = [
                list(row) for row in connection.execute("PRAGMA foreign_key_check")
            ]
            user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        return {
            "integrity_check": integrity,
            "foreign_key_issues": foreign_keys,
            "user_version": user_version,
        }

    def describe(self) -> dict[str, object]:
        return {
            "backend": type(self).__name__,
            "driver": self.driver,
            "encrypted": self.encrypted,
            "locked": self.key_provider.is_locked(),
            "database": str(self.path),
        }


@dataclass(frozen=True)
class SQLCipherBackend:
    """SQLCipher 4 backend for encrypted copies and the future canonical store."""

    root: Path
    key_provider: KeyProvider
    filename: str = "genea.encrypted.sqlite3"

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root).expanduser().resolve())
        if not self.filename or Path(self.filename).name != self.filename:
            raise ValueError("Nazwa zaszyfrowanej bazy musi być pojedynczą nazwą pliku")

    @property
    def path(self) -> Path:
        return research_root(self.root) / self.filename

    @property
    def encrypted(self) -> bool:
        return True

    @property
    def driver(self) -> str:
        return "sqlcipher3"

    @staticmethod
    def _driver():  # type: ignore[no-untyped-def]
        try:
            from sqlcipher3 import dbapi2 as sqlcipher
        except ImportError as error:
            raise RuntimeError(
                "Brak opcjonalnej zależności sqlcipher3; uruchom `uv sync --extra private`."
            ) from error
        return sqlcipher

    def _require_key(self) -> bytes:
        key = self.key_provider.load_key()
        if key is None or self.key_provider.is_locked():
            raise StorageLockedError("Zaszyfrowany magazyn jest zablokowany")
        if len(key) != 32:
            raise StorageLockedError("Dostawca zwrócił klucz o nieprawidłowej długości")
        return key

    @staticmethod
    def _apply_key(connection: Any, key: bytes) -> None:
        connection.execute(f'''PRAGMA key = "x'{key.hex()}'"''')
        connection.execute("PRAGMA cipher_compatibility = 4")
        connection.execute("PRAGMA cipher_memory_security = ON")

    def connect(self, *, readonly: bool = False):  # type: ignore[no-untyped-def]
        key = self._require_key()
        sqlcipher = self._driver()
        if readonly:
            if not self.path.is_file():
                raise FileNotFoundError(f"Nie znaleziono zaszyfrowanej bazy: {self.path}")
            connection = sqlcipher.connect(
                self.path.resolve().as_uri() + "?mode=ro",
                uri=True,
            )
        else:
            ensure_workspace(self.root)
            connection = sqlcipher.connect(self.path)
        try:
            self._apply_key(connection, key)
            connection.row_factory = sqlcipher.Row
            connection.execute("PRAGMA foreign_keys = ON")
            if readonly:
                connection.execute("PRAGMA query_only = ON")
            connection.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
        except Exception:
            connection.close()
            raise StorageLockedError("Nie można otworzyć zaszyfrowanego magazynu") from None
        return connection

    def backup(self, destination: Path) -> Path:
        destination = Path(destination).expanduser().resolve()
        if destination.exists():
            raise FileExistsError(f"Plik backupu już istnieje: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        key = self._require_key()
        sqlcipher = self._driver()
        try:
            with self.connect(readonly=True) as source:
                target = sqlcipher.connect(destination)
                try:
                    self._apply_key(target, key)
                    source.backup(target)
                    target.commit()
                finally:
                    target.close()
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return destination

    def rekey(self, new_key: bytes) -> None:
        if len(new_key) != 32:
            raise ValueError("Nowy klucz danych musi mieć dokładnie 256 bitów")
        with self.connect() as connection:
            connection.execute(f'''PRAGMA rekey = "x'{new_key.hex()}'"''')
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def integrity_report(self) -> dict[str, object]:
        with self.connect(readonly=True) as connection:
            cipher_integrity = [
                row[0] for row in connection.execute("PRAGMA cipher_integrity_check")
            ]
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_keys = [
                list(row) for row in connection.execute("PRAGMA foreign_key_check")
            ]
            user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            cipher_version = connection.execute("PRAGMA cipher_version").fetchone()[0]
        return {
            "cipher_integrity_check": cipher_integrity,
            "integrity_check": integrity,
            "foreign_key_issues": foreign_keys,
            "user_version": user_version,
            "cipher_version": cipher_version,
        }

    def describe(self) -> dict[str, object]:
        return {
            "backend": type(self).__name__,
            "driver": self.driver,
            "encrypted": self.encrypted,
            "locked": self.key_provider.is_locked(),
            "database": str(self.path),
        }


def database_backend(
    root: Path,
    *,
    key_provider: KeyProvider | None = None,
    backend: str = "plaintext",
    filename: str | None = None,
) -> DatabaseBackend:
    """Return the configured backend without opening or migrating the database."""

    if backend == "sqlcipher":
        if key_provider is None:
            raise ValueError("Backend SQLCipher wymaga jawnego dostawcy klucza")
        return SQLCipherBackend(
            Path(root),
            key_provider=key_provider,
            filename=filename or "genea.encrypted.sqlite3",
        )
    if backend != "plaintext":
        raise ValueError(f"Nieznany backend magazynu: {backend}")
    return PlaintextSQLiteBackend(
        Path(root),
        key_provider=key_provider or NoEncryptionKeyProvider(),
    )


def connect_plaintext_path(path: Path, *, readonly: bool = True) -> sqlite3.Connection:
    """Open an explicit plaintext path for migration verification and rollback only."""

    path = Path(path).expanduser().resolve()
    if readonly:
        if not path.is_file():
            raise FileNotFoundError(f"Nie znaleziono bazy plaintext: {path}")
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        connection.execute("PRAGMA query_only = ON")
    else:
        connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def configured_database_backend(
    root: Path,
    *,
    config_path: Path | None = None,
) -> DatabaseBackend:
    """Load a strict, non-secret storage configuration for this workspace.

    An absent configuration preserves the current plaintext compatibility
    backend. SQLCipher configuration must point to a mounted secret file; the
    key itself is never accepted in JSON or an environment variable.
    """

    root = Path(root).expanduser().resolve()
    path = (
        Path(config_path).expanduser().resolve()
        if config_path is not None
        else research_root(root) / "storage.json"
    )
    if not path.exists():
        return database_backend(root)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise RuntimeError("Nie można odczytać konfiguracji magazynu") from error
    if len(raw) > 16_384:
        raise ValueError("Konfiguracja magazynu jest zbyt duża")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("Konfiguracja magazynu nie jest poprawnym JSON-em") from error
    if not isinstance(payload, dict):
        raise ValueError("Konfiguracja magazynu musi być obiektem JSON")
    allowed = {"version", "backend", "database", "keyProvider"}
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"Nieznane pola konfiguracji magazynu: {', '.join(unknown)}")
    if payload.get("version") != 1:
        raise ValueError("Nieobsługiwana wersja konfiguracji magazynu")
    backend_name = payload.get("backend")
    if backend_name == "plaintext":
        if set(payload) != {"version", "backend"}:
            raise ValueError("Backend plaintext nie może zawierać konfiguracji klucza")
        return database_backend(root)
    if backend_name != "sqlcipher":
        raise ValueError("Nieobsługiwany backend w konfiguracji magazynu")
    filename = payload.get("database")
    if not isinstance(filename, str) or Path(filename).name != filename:
        raise ValueError("Pole database musi być pojedynczą nazwą pliku")
    provider_config = payload.get("keyProvider")
    if not isinstance(provider_config, dict):
        raise ValueError("Backend SQLCipher wymaga konfiguracji keyProvider")
    provider_allowed = {"type", "path", "encoding"}
    provider_unknown = sorted(set(provider_config) - provider_allowed)
    if provider_unknown:
        raise ValueError(
            f"Nieznane pola konfiguracji keyProvider: {', '.join(provider_unknown)}"
        )
    provider_type = provider_config.get("type")
    if provider_type == "mounted-secret":
        if set(provider_config) != {"type", "path", "encoding"}:
            raise ValueError("Niepełna konfiguracja montowanego sekretu")
        if provider_config.get("encoding") != "base64":
            raise ValueError("Montowany sekret musi używać kodowania base64")
        secret_path = provider_config.get("path")
        if not isinstance(secret_path, str) or not secret_path:
            raise ValueError("keyProvider.path musi być niepustą ścieżką")
        provider: KeyProvider = MountedSecretKeyProvider(Path(secret_path))
        provider.unlock()
    elif provider_type == "macos-keychain":
        if set(provider_config) != {"type"}:
            raise ValueError("Konfiguracja Keychain nie przyjmuje dodatkowych pól")
        keychain = MacOSKeychainProvider(root)
        keychain.unlock()
        provider = keychain
    else:
        raise ValueError("Nieobsługiwany typ keyProvider")
    return database_backend(
        root,
        backend="sqlcipher",
        key_provider=provider,
        filename=filename,
    )


__all__ = [
    "DatabaseBackend",
    "Argon2PasswordVerifier",
    "InMemoryKeyProvider",
    "KeyProvider",
    "MacOSKeychainProvider",
    "MountedSecretKeyProvider",
    "NoEncryptionKeyProvider",
    "PlaintextSQLiteBackend",
    "SQLCipherBackend",
    "StorageLockedError",
    "configured_database_backend",
    "connect_plaintext_path",
    "database_backend",
]
