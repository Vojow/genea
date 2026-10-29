from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .private_crypto import (
    PrivateCryptoError,
    create_recovery_envelope,
    read_recovery_envelope,
    recover_data_key,
    write_recovery_envelope,
)
from .storage import (
    Argon2PasswordVerifier,
    InMemoryKeyProvider,
    MacOSKeychainProvider,
    SQLCipherBackend,
    connect_plaintext_path,
    configured_database_backend,
)
from .workspace import dump_json, research_root, sha256_file, utc_now


AUTH_FILENAME = "private-auth.json"
STORAGE_FILENAME = "storage.json"
LOCK_FILENAME = "private-ui-lock.json"
ENCRYPTED_DATABASE_FILENAME = "genea.encrypted.sqlite3"


def _ensure_outside_workspace(root: Path, path: Path, label: str) -> Path:
    root = Path(root).resolve()
    path = Path(path).expanduser().resolve()
    if path == root or root in path.parents:
        raise ValueError(f"{label} musi znajdować się poza workspace i repozytorium")
    return path


def _write_private_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _read_auth(root: Path) -> dict[str, object]:
    path = research_root(root) / AUTH_FILENAME
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise RuntimeError("Brak konfiguracji prywatnego uwierzytelnienia") from error
    if len(raw) > 16_384:
        raise RuntimeError("Konfiguracja prywatnego uwierzytelnienia jest zbyt duża")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError("Uszkodzona konfiguracja prywatnego uwierzytelnienia") from error
    if not isinstance(value, dict) or set(value) != {
        "version",
        "algorithm",
        "verifier",
        "createdAt",
    }:
        raise RuntimeError("Nieobsługiwana konfiguracja prywatnego uwierzytelnienia")
    if value["version"] != 1 or value["algorithm"] != "argon2id":
        raise RuntimeError("Nieobsługiwana konfiguracja prywatnego uwierzytelnienia")
    return value


def verify_private_password(root: Path, password: str) -> bool:
    auth = _read_auth(root)
    verifier = auth.get("verifier")
    if not isinstance(verifier, str):
        return False
    return Argon2PasswordVerifier().verify(verifier, password)


def initialize_private_storage(
    root: Path,
    *,
    ui_password: str,
    recovery_passphrase: str,
    recovery_output: Path,
    keyring_adapter: Any | None = None,
) -> dict[str, object]:
    """Provision a data key, UI verifier and external recovery envelope.

    This deliberately does not migrate or activate the canonical database.
    """

    root = Path(root).resolve()
    auth_path = research_root(root) / AUTH_FILENAME
    storage_path = research_root(root) / STORAGE_FILENAME
    recovery_output = _ensure_outside_workspace(
        root, recovery_output, "Plik recovery"
    )
    if auth_path.exists() or storage_path.exists():
        raise FileExistsError("Prywatny magazyn jest już skonfigurowany")
    if recovery_output.exists():
        raise FileExistsError(f"Plik recovery już istnieje: {recovery_output}")
    if len(ui_password) < 12:
        raise ValueError("Hasło prywatnego UI musi mieć co najmniej 12 znaków")
    data_key = secrets.token_bytes(32)
    envelope = create_recovery_envelope(data_key, recovery_passphrase)
    verifier = Argon2PasswordVerifier().create(ui_password)
    write_recovery_envelope(recovery_output, envelope)
    _write_private_json(
        auth_path,
        {
            "version": 1,
            "algorithm": "argon2id",
            "verifier": verifier,
            "createdAt": utc_now(),
        },
    )
    provider = MacOSKeychainProvider(root, keyring_adapter=keyring_adapter)
    provider.provision(data_key)
    data_key = b"\x00" * len(data_key)
    return {
        "status": "private-key-provisioned",
        "databaseMigrated": False,
        "databaseActivated": False,
        "recoveryFile": str(recovery_output),
        "authFile": str(auth_path),
        "keyProvider": "macos-keychain",
    }


def recover_private_storage_key(
    root: Path,
    *,
    recovery_file: Path,
    recovery_passphrase: str,
    keyring_adapter: Any | None = None,
) -> dict[str, object]:
    root = Path(root).resolve()
    envelope = read_recovery_envelope(recovery_file)
    key = recover_data_key(envelope, recovery_passphrase)
    encrypted = research_root(root) / ENCRYPTED_DATABASE_FILENAME
    if encrypted.is_file():
        backend = SQLCipherBackend(root, InMemoryKeyProvider.unlocked(key))
        report = backend.integrity_report()
        if report["integrity_check"] != "ok" or report["foreign_key_issues"]:
            raise PrivateCryptoError("Odzyskany klucz nie otwiera poprawnego magazynu")
    provider = MacOSKeychainProvider(root, keyring_adapter=keyring_adapter)
    provider.provision(key)
    key = b"\x00" * len(key)
    return {
        "status": "private-key-recovered",
        "keyProvider": "macos-keychain",
        "databaseVerified": encrypted.is_file(),
    }


def _keychain_key(root: Path, keyring_adapter: Any | None = None) -> bytes:
    provider = MacOSKeychainProvider(root, keyring_adapter=keyring_adapter)
    provider.unlock()
    key = provider.load_key()
    if key is None or len(key) != 32:
        raise RuntimeError("Keychain nie zwrócił poprawnego klucza danych")
    return key


def _safe_value(value: object) -> object:
    if isinstance(value, bytes):
        return {"bytes": base64.b64encode(value).decode("ascii")}
    if isinstance(value, float):
        return {"float": value.hex()}
    return value


def _manifest_locator(root: Path, path: Path) -> str:
    """Keep workstation-specific absolute paths out of versioned manifests."""

    root = Path(root).resolve()
    path = Path(path).resolve()
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return f"external:{path.name}"


def semantic_database_manifest(connection) -> dict[str, object]:  # type: ignore[no-untyped-def]
    """Create a deterministic, content-oriented manifest for migration QA."""

    objects = [
        tuple(row)
        for row in connection.execute(
            """SELECT type,name,tbl_name,COALESCE(sql,'') FROM sqlite_master
               WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"""
        )
    ]
    schema_hash = hashlib.sha256(
        json.dumps(objects, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    tables = [
        row[0]
        for row in connection.execute(
            """SELECT name FROM sqlite_master
               WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"""
        )
    ]
    table_manifests: dict[str, object] = {}
    for table in tables:
        columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
        row_hashes: list[str] = []
        rows = connection.execute(f'SELECT * FROM "{table}"')
        for row in rows:
            encoded = json.dumps(
                [_safe_value(value) for value in tuple(row)],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            row_hashes.append(hashlib.sha256(encoded).hexdigest())
        row_hashes.sort()
        digest = hashlib.sha256("".join(row_hashes).encode("ascii")).hexdigest()
        table_manifests[table] = {
            "columns": columns,
            "rowCount": len(row_hashes),
            "rowsSha256": digest,
        }
    return {
        "userVersion": int(connection.execute("PRAGMA user_version").fetchone()[0]),
        "schemaSha256": schema_hash,
        "tables": table_manifests,
    }


def migrate_plaintext_copy(
    root: Path,
    *,
    destination: Path | None = None,
    keyring_adapter: Any | None = None,
) -> dict[str, object]:
    """Create and verify a new SQLCipher database without altering plaintext."""

    root = Path(root).resolve()
    source = research_root(root) / "genea.sqlite3"
    destination = Path(
        destination or research_root(root) / ENCRYPTED_DATABASE_FILENAME
    ).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Nie znaleziono bazy plaintext: {source}")
    if destination.exists():
        raise FileExistsError(f"Docelowy ciphertext już istnieje: {destination}")
    if destination == source:
        raise ValueError("Migracja in-place jest zabroniona")
    key = _keychain_key(root, keyring_adapter)
    sqlcipher = SQLCipherBackend._driver()
    source_connection = sqlcipher.connect(source)
    try:
        source_connection.row_factory = sqlcipher.Row
        source_integrity = source_connection.execute("PRAGMA integrity_check").fetchone()[0]
        source_foreign_keys = [
            list(row) for row in source_connection.execute("PRAGMA foreign_key_check")
        ]
        if source_integrity != "ok" or source_foreign_keys:
            raise RuntimeError("Baza źródłowa nie przeszła kontroli integralności")
        source_manifest = semantic_database_manifest(source_connection)
        destination.parent.mkdir(parents=True, exist_ok=True)
        key_literal = key.hex()
        try:
            source_connection.execute(
                f'''ATTACH DATABASE ? AS encrypted KEY "x'{key_literal}'"''',
                (str(destination),),
            )
            source_connection.execute("PRAGMA encrypted.cipher_compatibility = 4")
            source_connection.execute("SELECT sqlcipher_export('encrypted')")
            source_connection.execute(
                f"PRAGMA encrypted.user_version = {source_manifest['userVersion']}"
            )
            source_connection.execute("DETACH DATABASE encrypted")
        except Exception:
            destination.unlink(missing_ok=True)
            raise RuntimeError("Migracja SQLCipher nie powiodła się") from None
    finally:
        source_connection.close()
        key = b"\x00" * len(key)
    backend = SQLCipherBackend(
        root,
        InMemoryKeyProvider.unlocked(_keychain_key(root, keyring_adapter)),
        filename=destination.name,
    )
    report = backend.integrity_report()
    with backend.connect(readonly=True) as encrypted_connection:
        target_manifest = semantic_database_manifest(encrypted_connection)
    if source_manifest != target_manifest:
        destination.unlink(missing_ok=True)
        raise RuntimeError("Migracja zmieniła semantykę bazy; ciphertext usunięto")
    try:
        with connect_plaintext_path(destination) as plain_connection:
            plain_connection.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
    except Exception:
        plaintext_rejected = True
    else:
        destination.unlink(missing_ok=True)
        raise RuntimeError("Docelowa baza jest czytelna przez zwykłe sqlite3")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    manifest_path = research_root(root) / "private-migrations" / f"{timestamp}.json"
    manifest = {
        "version": 1,
        "createdAt": utc_now(),
        "status": "verified-copy",
        "source": {"path": _manifest_locator(root, source), "sha256": sha256_file(source)},
        "target": {"path": _manifest_locator(root, destination), "sha256": sha256_file(destination)},
        "semanticManifest": source_manifest,
        "integrity": report,
        "plaintextDriverRejectedTarget": plaintext_rejected,
    }
    dump_json(manifest_path, manifest)
    return {**manifest, "manifest": str(manifest_path)}


def _git_worktree_clean(root: Path) -> bool:
    if not (root / ".git").exists():
        return True
    result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return not result.stdout.strip()


def activate_encrypted_database(
    root: Path,
    *,
    recovery_file: Path,
    recovery_passphrase: str,
    plaintext_rollback_destination: Path,
    keyring_adapter: Any | None = None,
) -> dict[str, object]:
    """Switch to verified ciphertext and move plaintext outside the workspace."""

    root = Path(root).resolve()
    if not _git_worktree_clean(root):
        raise RuntimeError("Aktywacja wymaga czystego worktree i punktu kontrolnego")
    source = research_root(root) / "genea.sqlite3"
    encrypted = research_root(root) / ENCRYPTED_DATABASE_FILENAME
    rollback = _ensure_outside_workspace(
        root, plaintext_rollback_destination, "Kopia rollback"
    )
    if not source.is_file() or not encrypted.is_file():
        raise FileNotFoundError("Aktywacja wymaga plaintextu i zweryfikowanego ciphertextu")
    if rollback.exists():
        raise FileExistsError(f"Cel rollback już istnieje: {rollback}")
    for suffix in ("-wal", "-shm", "-journal"):
        if Path(str(source) + suffix).exists() or Path(str(encrypted) + suffix).exists():
            raise RuntimeError("Aktywacja z aktywnym WAL/SHM/journal jest zabroniona")
    recovered = recover_data_key(
        read_recovery_envelope(recovery_file), recovery_passphrase
    )
    current = _keychain_key(root, keyring_adapter)
    if not secrets.compare_digest(recovered, current):
        raise PrivateCryptoError("Recovery key nie odpowiada kluczowi w Keychain")
    recovered = b"\x00" * len(recovered)
    current = b"\x00" * len(current)
    backend = SQLCipherBackend(
        root,
        InMemoryKeyProvider.unlocked(_keychain_key(root, keyring_adapter)),
    )
    report = backend.integrity_report()
    if report["integrity_check"] != "ok" or report["foreign_key_issues"]:
        raise RuntimeError("Ciphertext nie przeszedł kontroli przed aktywacją")
    rollback.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    shutil.move(str(source), str(rollback))
    rollback.chmod(0o600)
    try:
        _write_private_json(
            research_root(root) / STORAGE_FILENAME,
            {
                "version": 1,
                "backend": "sqlcipher",
                "database": ENCRYPTED_DATABASE_FILENAME,
                "keyProvider": {"type": "macos-keychain"},
            },
        )
        if keyring_adapter is None:
            configured_report = configured_database_backend(root).integrity_report()
        else:
            configured_report = backend.integrity_report()
    except Exception:
        (research_root(root) / STORAGE_FILENAME).unlink(missing_ok=True)
        shutil.move(str(rollback), str(source))
        raise
    return {
        "status": "encrypted-database-active",
        "database": str(encrypted),
        "plaintextRollback": str(rollback),
        "integrity": configured_report,
    }


def rollback_encrypted_database(
    root: Path,
    *,
    plaintext_rollback_source: Path,
) -> dict[str, object]:
    root = Path(root).resolve()
    source = _ensure_outside_workspace(
        root, plaintext_rollback_source, "Kopia rollback"
    )
    destination = research_root(root) / "genea.sqlite3"
    if not source.is_file():
        raise FileNotFoundError("Nie znaleziono kopii rollback")
    if destination.exists():
        raise FileExistsError("Plaintextowa baza już istnieje w workspace")
    with connect_plaintext_path(source) as connection:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Kopia rollback nie przeszła integrity_check")
    (research_root(root) / STORAGE_FILENAME).unlink(missing_ok=True)
    shutil.move(str(source), str(destination))
    return {"status": "plaintext-rollback-active", "database": str(destination)}


def change_private_ui_password(
    root: Path,
    *,
    current_password: str,
    new_password: str,
) -> dict[str, object]:
    root = Path(root).resolve()
    if not verify_private_password(root, current_password):
        raise PermissionError("Nieprawidłowe dane odblokowania")
    if len(new_password) < 12:
        raise ValueError("Nowe hasło prywatnego UI musi mieć co najmniej 12 znaków")
    _write_private_json(
        research_root(root) / AUTH_FILENAME,
        {
            "version": 1,
            "algorithm": "argon2id",
            "verifier": Argon2PasswordVerifier().create(new_password),
            "createdAt": utc_now(),
        },
    )
    lock_private_ui(root)
    return {"status": "private-ui-password-changed", "sessionsLocked": True}


def rotate_private_data_key(
    root: Path,
    *,
    current_recovery_file: Path,
    current_recovery_passphrase: str,
    new_recovery_output: Path,
    new_recovery_passphrase: str,
    encrypted_backup: Path,
    keyring_adapter: Any | None = None,
) -> dict[str, object]:
    """Rotate SQLCipher key only after an encrypted backup and recovery proof."""

    root = Path(root).resolve()
    new_recovery_output = _ensure_outside_workspace(
        root, new_recovery_output, "Nowy plik recovery"
    )
    encrypted_backup = _ensure_outside_workspace(
        root, encrypted_backup, "Szyfrowany backup rotacji"
    )
    if new_recovery_output.exists() or encrypted_backup.exists():
        raise FileExistsError("Cel recovery lub backupu rotacji już istnieje")
    current_key = _keychain_key(root, keyring_adapter)
    recovered = recover_data_key(
        read_recovery_envelope(current_recovery_file),
        current_recovery_passphrase,
    )
    if not secrets.compare_digest(current_key, recovered):
        raise PrivateCryptoError("Bieżący recovery key nie odpowiada Keychain")
    recovered = b"\x00" * len(recovered)
    filename = ENCRYPTED_DATABASE_FILENAME
    backend = SQLCipherBackend(
        root,
        InMemoryKeyProvider.unlocked(current_key),
        filename=filename,
    )
    if not backend.path.is_file():
        raise FileNotFoundError("Nie znaleziono aktywnej zaszyfrowanej bazy")
    backend.backup(encrypted_backup)
    new_key = secrets.token_bytes(32)
    write_recovery_envelope(
        new_recovery_output,
        create_recovery_envelope(new_key, new_recovery_passphrase),
    )
    try:
        backend.rekey(new_key)
        provider = MacOSKeychainProvider(root, keyring_adapter=keyring_adapter)
        provider.provision(new_key)
        verification = SQLCipherBackend(
            root,
            InMemoryKeyProvider.unlocked(new_key),
            filename=filename,
        ).integrity_report()
        if verification["integrity_check"] != "ok" or verification["foreign_key_issues"]:
            raise RuntimeError("Kontrola po rotacji klucza nie przeszła")
    except Exception:
        try:
            SQLCipherBackend(
                root,
                InMemoryKeyProvider.unlocked(new_key),
                filename=filename,
            ).rekey(current_key)
            MacOSKeychainProvider(root, keyring_adapter=keyring_adapter).provision(current_key)
        finally:
            new_recovery_output.unlink(missing_ok=True)
        raise RuntimeError("Rotacja klucza nie powiodła się; podjęto rollback") from None
    finally:
        current_key = b"\x00" * len(current_key)
        new_key = b"\x00" * len(new_key)
    lock_private_ui(root)
    return {
        "status": "private-data-key-rotated",
        "encryptedBackup": str(encrypted_backup),
        "newRecoveryFile": str(new_recovery_output),
        "integrity": verification,
        "sessionsLocked": True,
    }


def private_lock_path(root: Path) -> Path:
    return research_root(root) / "tmp" / LOCK_FILENAME


def lock_private_ui(root: Path) -> dict[str, object]:
    marker = private_lock_path(root)
    _write_private_json(marker, {"lockedAt": utc_now(), "epoch": secrets.token_hex(16)})
    return {"status": "locked", "locked": True}


def unlock_private_ui(root: Path, password: str) -> dict[str, object]:
    if not verify_private_password(root, password):
        raise PermissionError("Nieprawidłowe dane odblokowania")
    private_lock_path(root).unlink(missing_ok=True)
    return {"status": "unlocked", "locked": False}


def private_status(root: Path, keyring_adapter: Any | None = None) -> dict[str, object]:
    root = Path(root).resolve()
    backend = None
    backend_name = "PlaintextSQLiteBackend"
    encrypted = False
    database_path = research_root(root) / "genea.sqlite3"
    try:
        backend = configured_database_backend(root)
        backend_name = type(backend).__name__
        encrypted = backend.encrypted
        database_path = backend.path
    except Exception:
        config_path = research_root(root) / STORAGE_FILENAME
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            config = {}
        if config.get("backend") == "sqlcipher":
            backend_name = "SQLCipherBackend"
            encrypted = True
            filename = config.get("database")
            if isinstance(filename, str) and Path(filename).name == filename:
                database_path = research_root(root) / filename
    key_available = False
    try:
        key_available = len(_keychain_key(root, keyring_adapter)) == 32
    except Exception:
        pass
    return {
        "configured": (research_root(root) / AUTH_FILENAME).is_file(),
        "backend": backend_name,
        "encrypted": encrypted,
        "databaseExists": database_path.is_file(),
        "keyAvailable": key_available,
        "uiLocked": private_lock_path(root).is_file(),
        "plaintextDatabasePresent": (research_root(root) / "genea.sqlite3").is_file(),
        "encryptedDatabasePresent": (
            research_root(root) / ENCRYPTED_DATABASE_FILENAME
        ).is_file(),
    }


__all__ = [
    "activate_encrypted_database",
    "change_private_ui_password",
    "initialize_private_storage",
    "lock_private_ui",
    "migrate_plaintext_copy",
    "private_status",
    "recover_private_storage_key",
    "rotate_private_data_key",
    "rollback_encrypted_database",
    "semantic_database_manifest",
    "unlock_private_ui",
    "verify_private_password",
]
