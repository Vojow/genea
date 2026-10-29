from __future__ import annotations

import ast
import base64
import importlib.util
import json
import secrets
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from genea.storage import (
    Argon2PasswordVerifier,
    InMemoryKeyProvider,
    MacOSKeychainProvider,
    MountedSecretKeyProvider,
    NoEncryptionKeyProvider,
    PlaintextSQLiteBackend,
    SQLCipherBackend,
    StorageLockedError,
    connect_derivative_cache,
    configured_database_backend,
    database_backend,
)


class StaticKeyProvider:
    def load_key(self) -> bytes:
        return b"not-for-plaintext"

    def is_locked(self) -> bool:
        return False

    def lock(self) -> None:
        return None


class FakeKeyring:
    def __init__(self):
        self.values: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, account: str, value: str) -> None:
        self.values[(service, account)] = value

    def get_password(self, service: str, account: str) -> str | None:
        return self.values.get((service, account))


class StorageFacadeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_derivative_cache_cannot_use_canonical_database_filename(self):
        for name in ("genea.sqlite3", "genea.encrypted.sqlite3"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Kanoniczna baza"):
                connect_derivative_cache(self.root / "research" / name)

    def test_plaintext_backend_has_explicit_safe_description(self):
        backend = database_backend(self.root)
        self.assertIsInstance(backend, PlaintextSQLiteBackend)
        self.assertIsInstance(backend.key_provider, NoEncryptionKeyProvider)
        self.assertEqual(
            backend.describe(),
            {
                "backend": "PlaintextSQLiteBackend",
                "driver": "stdlib-sqlite3",
                "encrypted": False,
                "locked": False,
                "database": str(
                    (self.root / "research" / "genea.sqlite3").resolve()
                ),
            },
        )

    def test_readonly_open_does_not_create_missing_workspace(self):
        backend = database_backend(self.root)
        with self.assertRaises(FileNotFoundError):
            backend.connect(readonly=True)
        self.assertFalse((self.root / "research").exists())

    def test_readonly_connection_rejects_writes(self):
        backend = database_backend(self.root)
        with backend.connect() as connection:
            connection.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY, value TEXT)")
            connection.execute("INSERT INTO sample(value) VALUES('test')")
        with backend.connect(readonly=True) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sample").fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("INSERT INTO sample(value) VALUES('blocked')")

    def test_backup_and_integrity_report_use_backend_boundary(self):
        backend = database_backend(self.root)
        with backend.connect() as connection:
            connection.execute("PRAGMA user_version = 7")
            connection.execute("CREATE TABLE parent(id INTEGER PRIMARY KEY)")
        destination = self.root / "backup" / "genea.sqlite3"
        self.assertEqual(backend.backup(destination), destination.resolve())
        with sqlite3.connect(destination) as backup:
            self.assertEqual(backup.execute("PRAGMA user_version").fetchone()[0], 7)
        self.assertEqual(
            backend.integrity_report(),
            {
                "integrity_check": "ok",
                "foreign_key_issues": [],
                "user_version": 7,
            },
        )
        with self.assertRaises(FileExistsError):
            backend.backup(destination)

    def test_plaintext_backend_refuses_key_material(self):
        with self.assertRaisesRegex(ValueError, "plaintext"):
            database_backend(self.root, key_provider=StaticKeyProvider())

    def test_in_memory_provider_requires_256_bits_and_can_be_locked(self):
        with self.assertRaisesRegex(ValueError, "256"):
            InMemoryKeyProvider.unlocked(b"too-short")
        key = secrets.token_bytes(32)
        provider = InMemoryKeyProvider.unlocked(key)
        self.assertEqual(provider.load_key(), key)
        provider.lock()
        self.assertTrue(provider.is_locked())
        self.assertIsNone(provider.load_key())

    def test_keychain_provider_only_loads_key_after_explicit_unlock(self):
        keyring = FakeKeyring()
        provider = MacOSKeychainProvider(self.root, keyring_adapter=keyring)
        with self.assertRaises(StorageLockedError):
            provider.unlock()
        key = secrets.token_bytes(32)
        provider.provision(key)
        provider.lock()
        self.assertTrue(provider.is_locked())
        provider.unlock()
        self.assertEqual(provider.load_key(), key)
        self.assertNotIn(key.hex(), repr(provider))

    def test_mounted_secret_provider_requires_absolute_path_and_can_be_locked(self):
        with self.assertRaisesRegex(ValueError, "bezwzględna"):
            MountedSecretKeyProvider(Path("relative-secret"))
        key = secrets.token_bytes(32)
        secret = self.root / "database-key"
        secret.write_bytes(base64.b64encode(key) + b"\n")
        provider = MountedSecretKeyProvider(secret.resolve())
        provider.unlock()
        self.assertEqual(provider.load_key(), key)
        self.assertNotIn(key.hex(), repr(provider))
        provider.lock()
        self.assertTrue(provider.is_locked())
        self.assertIsNone(provider.load_key())

    def test_mounted_secret_errors_do_not_expose_secret_content(self):
        secret = self.root / "database-key"
        secret.write_text("bardzo-tajny-ale-nieprawidlowy-klucz", encoding="utf-8")
        provider = MountedSecretKeyProvider(secret.resolve())
        with self.assertRaises(StorageLockedError) as raised:
            provider.unlock()
        self.assertNotIn("bardzo-tajny", str(raised.exception))

    def test_configured_backend_defaults_to_plaintext_without_configuration(self):
        backend = configured_database_backend(self.root)
        self.assertIsInstance(backend, PlaintextSQLiteBackend)

    def test_configured_backend_rejects_inline_or_unknown_key_material(self):
        research = self.root / "research"
        research.mkdir()
        (research / "storage.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "backend": "sqlcipher",
                    "database": "genea.encrypted.sqlite3",
                    "keyProvider": {
                        "type": "mounted-secret",
                        "path": "/run/secrets/genea_database_key",
                        "encoding": "base64",
                    },
                    "key": "must-never-be-accepted",
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "Nieznane pola"):
            configured_database_backend(self.root)

    @unittest.skipUnless(
        importlib.util.find_spec("sqlcipher3"),
        "brak opcjonalnego SQLCipher",
    )
    def test_configured_sqlcipher_backend_reads_only_mounted_secret_path(self):
        key = secrets.token_bytes(32)
        encrypted = SQLCipherBackend(self.root, InMemoryKeyProvider.unlocked(key))
        with encrypted.connect() as connection:
            connection.execute("CREATE TABLE sample(value TEXT)")
            connection.execute("INSERT INTO sample VALUES ('encrypted')")
        secret = self.root / "database-key"
        secret.write_bytes(base64.b64encode(key))
        config = self.root / "research" / "storage.json"
        config.write_text(
            json.dumps(
                {
                    "version": 1,
                    "backend": "sqlcipher",
                    "database": "genea.encrypted.sqlite3",
                    "keyProvider": {
                        "type": "mounted-secret",
                        "path": str(secret.resolve()),
                        "encoding": "base64",
                    },
                }
            ),
            encoding="utf-8",
        )
        configured = configured_database_backend(self.root)
        self.assertTrue(configured.encrypted)
        with configured.connect(readonly=True) as connection:
            self.assertEqual(connection.execute("SELECT value FROM sample").fetchone()[0], "encrypted")

    @unittest.skipUnless(importlib.util.find_spec("argon2"), "brak opcjonalnego argon2")
    def test_argon2_password_verifier_never_needs_plaintext_storage(self):
        verifier = Argon2PasswordVerifier()
        encoded = verifier.create("długie hasło testowe")
        self.assertTrue(encoded.startswith("$argon2id$"))
        self.assertTrue(verifier.verify(encoded, "długie hasło testowe"))
        self.assertFalse(verifier.verify(encoded, "inne hasło"))

    @unittest.skipUnless(
        importlib.util.find_spec("sqlcipher3"),
        "brak opcjonalnego SQLCipher",
    )
    def test_sqlcipher_backend_rejects_plain_sqlite_and_wrong_key(self):
        key = secrets.token_bytes(32)
        backend = SQLCipherBackend(self.root, InMemoryKeyProvider.unlocked(key))
        with backend.connect() as connection:
            connection.execute("CREATE TABLE sample(value TEXT)")
            connection.execute("INSERT INTO sample VALUES ('encrypted')")
        with self.assertRaises(sqlite3.DatabaseError):
            with sqlite3.connect(backend.path) as connection:
                connection.execute("SELECT * FROM sample").fetchall()
        wrong_backend = SQLCipherBackend(
            self.root,
            InMemoryKeyProvider.unlocked(secrets.token_bytes(32)),
        )
        with self.assertRaises(StorageLockedError):
            wrong_backend.connect(readonly=True)
        self.assertEqual(backend.integrity_report()["integrity_check"], "ok")

    def test_production_sqlite_connect_is_confined_to_storage_module(self):
        source_root = Path(__file__).resolve().parents[1] / "src" / "genea"
        violations: list[str] = []
        for path in sorted(source_root.glob("*.py")):
            if path.name == "storage.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                if (
                    node.func.attr == "connect"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "sqlite3"
                ):
                    violations.append(f"{path.name}:{node.lineno}")
        self.assertEqual(
            violations,
            [],
            "Produkcyjne sqlite3.connect musi przechodzić przez genea.storage",
        )


if __name__ == "__main__":
    unittest.main()
