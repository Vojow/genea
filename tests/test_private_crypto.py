from __future__ import annotations

import secrets
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from genea.private_crypto import (
    EncryptedObjectStore,
    PrivateCryptoError,
    create_recovery_envelope,
    read_recovery_envelope,
    recover_data_key,
    write_recovery_envelope,
)


class PrivateCryptoTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_recovery_envelope_round_trip_and_wrong_passphrase(self):
        key = secrets.token_bytes(32)
        envelope = create_recovery_envelope(key, "poprawne bardzo długie hasło")
        self.assertEqual(recover_data_key(envelope, "poprawne bardzo długie hasło"), key)
        with self.assertRaisesRegex(PrivateCryptoError, "Nie można odzyskać"):
            recover_data_key(envelope, "niepoprawne bardzo długie hasło")
        serialized = str(envelope)
        self.assertNotIn(key.hex(), serialized)
        self.assertNotIn("poprawne bardzo długie hasło", serialized)

    def test_recovery_file_is_created_once_with_private_permissions(self):
        destination = self.root / "outside" / "recovery.json"
        envelope = create_recovery_envelope(
            secrets.token_bytes(32), "inne poprawne bardzo długie hasło"
        )
        write_recovery_envelope(destination, envelope)
        self.assertEqual(read_recovery_envelope(destination), envelope)
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            write_recovery_envelope(destination, envelope)

    def test_private_object_streaming_round_trip_and_no_plaintext(self):
        master_key = secrets.token_bytes(32)
        source = self.root / "family-note.txt"
        plaintext = ("prywatna notatka rodzinna\n" * 100_000).encode("utf-8")
        source.write_bytes(plaintext)
        store = EncryptedObjectStore(self.root, master_key)
        result = store.encrypt_file(source, "attachment-1", media_type="text/plain")
        ciphertext = result.path.read_bytes()
        self.assertNotIn(b"prywatna notatka", ciphertext)
        self.assertEqual(result.size, len(plaintext))
        self.assertEqual(store.decrypt_bytes(result.path, max_size=len(plaintext) + 1), plaintext)

    def test_wrong_key_and_tamper_never_leave_plaintext_destination(self):
        source = self.root / "source.bin"
        source.write_bytes(secrets.token_bytes(4096))
        store = EncryptedObjectStore(self.root, secrets.token_bytes(32))
        result = store.encrypt_file(source, "attachment-2")
        destination = self.root / "decrypted.bin"
        wrong_store = EncryptedObjectStore(self.root, secrets.token_bytes(32))
        with self.assertRaises(PrivateCryptoError):
            wrong_store.decrypt_to_path(result.path, destination)
        self.assertFalse(destination.exists())
        tampered = bytearray(result.path.read_bytes())
        tampered[-17] ^= 0x01
        result.path.write_bytes(tampered)
        with self.assertRaises(PrivateCryptoError):
            store.decrypt_to_path(result.path, destination)
        self.assertFalse(destination.exists())

    def test_object_id_cannot_escape_private_archive(self):
        source = self.root / "source.bin"
        source.write_bytes(b"data")
        store = EncryptedObjectStore(self.root, secrets.token_bytes(32))
        with self.assertRaisesRegex(ValueError, "identyfikator"):
            store.encrypt_file(source, "../escape")


if __name__ == "__main__":
    unittest.main()
