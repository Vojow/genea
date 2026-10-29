from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


RECOVERY_FORMAT = "genea-recovery-key"
RECOVERY_VERSION = 1
OBJECT_MAGIC = b"GENEA-PRIVATE-OBJECT\x00"
OBJECT_VERSION = 1
OBJECT_TAG_SIZE = 16
STREAM_CHUNK_SIZE = 1024 * 1024
MAX_HEADER_SIZE = 16 * 1024
SAFE_OBJECT_ID = re.compile(r"[A-Za-z0-9_.-]{1,160}")


class PrivateCryptoError(RuntimeError):
    """A deliberately redacted private-storage error."""


def _crypto():  # type: ignore[no-untyped-def]
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    except ImportError as error:
        raise RuntimeError(
            "Brak zależności cryptography; uruchom `uv sync --extra private`."
        ) from error
    return hashes, Cipher, algorithms, modes, AESGCM, HKDF, Scrypt


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _unb64(value: object, field: str) -> bytes:
    if not isinstance(value, str):
        raise PrivateCryptoError(f"Nieprawidłowe pole {field}")
    try:
        return base64.b64decode(value, validate=True)
    except Exception as error:
        raise PrivateCryptoError(f"Nieprawidłowe pole {field}") from error


def _recovery_aad() -> bytes:
    return _canonical_json({"format": RECOVERY_FORMAT, "version": RECOVERY_VERSION})


def create_recovery_envelope(data_key: bytes, passphrase: str) -> dict[str, object]:
    """Wrap a 256-bit data key with a passphrase-derived AES-GCM key."""

    if len(data_key) != 32:
        raise ValueError("Klucz danych musi mieć dokładnie 256 bitów")
    if len(passphrase) < 14:
        raise ValueError("Hasło recovery musi mieć co najmniej 14 znaków")
    _, _, _, _, AESGCM, _, Scrypt = _crypto()
    salt = os.urandom(16)
    nonce = os.urandom(12)
    parameters = {"n": 2**15, "r": 8, "p": 1, "length": 32}
    wrapping_key = Scrypt(salt=salt, **parameters).derive(passphrase.encode("utf-8"))
    try:
        ciphertext = AESGCM(wrapping_key).encrypt(nonce, data_key, _recovery_aad())
    finally:
        wrapping_key = b"\x00" * len(wrapping_key)
    return {
        "format": RECOVERY_FORMAT,
        "version": RECOVERY_VERSION,
        "kdf": {"name": "scrypt", "salt": _b64(salt), **parameters},
        "cipher": {"name": "AES-256-GCM", "nonce": _b64(nonce)},
        "wrappedKey": _b64(ciphertext),
    }


def recover_data_key(envelope: dict[str, object], passphrase: str) -> bytes:
    """Unwrap a data key without exposing whether password or envelope was wrong."""

    try:
        if set(envelope) != {"format", "version", "kdf", "cipher", "wrappedKey"}:
            raise PrivateCryptoError("Nieobsługiwany format recovery")
        if envelope["format"] != RECOVERY_FORMAT or envelope["version"] != RECOVERY_VERSION:
            raise PrivateCryptoError("Nieobsługiwany format recovery")
        kdf = envelope["kdf"]
        cipher = envelope["cipher"]
        if not isinstance(kdf, dict) or not isinstance(cipher, dict):
            raise PrivateCryptoError("Nieobsługiwany format recovery")
        expected_kdf = {"name", "salt", "n", "r", "p", "length"}
        if set(kdf) != expected_kdf or set(cipher) != {"name", "nonce"}:
            raise PrivateCryptoError("Nieobsługiwany format recovery")
        if kdf["name"] != "scrypt" or cipher["name"] != "AES-256-GCM":
            raise PrivateCryptoError("Nieobsługiwany format recovery")
        if (kdf["n"], kdf["r"], kdf["p"], kdf["length"]) != (2**15, 8, 1, 32):
            raise PrivateCryptoError("Nieobsługiwane parametry recovery")
        salt = _unb64(kdf["salt"], "salt")
        nonce = _unb64(cipher["nonce"], "nonce")
        ciphertext = _unb64(envelope["wrappedKey"], "wrappedKey")
        if len(salt) != 16 or len(nonce) != 12 or len(ciphertext) != 48:
            raise PrivateCryptoError("Nieprawidłowy format recovery")
        _, _, _, _, AESGCM, _, Scrypt = _crypto()
        wrapping_key = Scrypt(
            salt=salt,
            length=32,
            n=2**15,
            r=8,
            p=1,
        ).derive(passphrase.encode("utf-8"))
        try:
            key = AESGCM(wrapping_key).decrypt(nonce, ciphertext, _recovery_aad())
        finally:
            wrapping_key = b"\x00" * len(wrapping_key)
        if len(key) != 32:
            raise PrivateCryptoError("Nie można odzyskać klucza danych")
        return key
    except PrivateCryptoError:
        raise
    except Exception:
        raise PrivateCryptoError("Nie można odzyskać klucza danych") from None


def write_recovery_envelope(path: Path, envelope: dict[str, object]) -> Path:
    path = Path(path).expanduser().resolve()
    if path.exists():
        raise FileExistsError(f"Plik recovery już istnieje: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(
            json.dumps(envelope, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return path


def read_recovery_envelope(path: Path) -> dict[str, object]:
    path = Path(path).expanduser().resolve()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise PrivateCryptoError("Nie można odczytać pliku recovery") from error
    if len(raw) > MAX_HEADER_SIZE:
        raise PrivateCryptoError("Plik recovery jest zbyt duży")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise PrivateCryptoError("Plik recovery nie jest poprawnym JSON-em") from error
    if not isinstance(value, dict):
        raise PrivateCryptoError("Plik recovery musi być obiektem JSON")
    return value


def _derive_object_key(master_key: bytes, salt: bytes, object_id: str) -> bytes:
    if len(master_key) != 32:
        raise ValueError("Klucz główny musi mieć dokładnie 256 bitów")
    hashes, _, _, _, _, HKDF, _ = _crypto()
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=("genea-private-object:" + object_id).encode("utf-8"),
    ).derive(master_key)


def _read_prefix(handle: BinaryIO) -> tuple[dict[str, object], bytes, int]:
    magic = handle.read(len(OBJECT_MAGIC))
    if magic != OBJECT_MAGIC:
        raise PrivateCryptoError("Nieprawidłowy format prywatnego obiektu")
    encoded_length = handle.read(4)
    if len(encoded_length) != 4:
        raise PrivateCryptoError("Uszkodzony nagłówek prywatnego obiektu")
    header_length = struct.unpack(">I", encoded_length)[0]
    if not 1 <= header_length <= MAX_HEADER_SIZE:
        raise PrivateCryptoError("Nieprawidłowa długość nagłówka prywatnego obiektu")
    header_bytes = handle.read(header_length)
    if len(header_bytes) != header_length:
        raise PrivateCryptoError("Uszkodzony nagłówek prywatnego obiektu")
    try:
        header = json.loads(header_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PrivateCryptoError("Uszkodzony nagłówek prywatnego obiektu") from error
    if not isinstance(header, dict):
        raise PrivateCryptoError("Nieprawidłowy nagłówek prywatnego obiektu")
    return header, magic + encoded_length + header_bytes, header_length


@dataclass(frozen=True)
class EncryptedObjectResult:
    path: Path
    plaintext_sha256: str
    ciphertext_sha256: str
    size: int
    media_type: str


@dataclass(frozen=True)
class EncryptedObjectStore:
    root: Path
    master_key: bytes

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root).expanduser().resolve())
        if len(self.master_key) != 32:
            raise ValueError("Klucz główny musi mieć dokładnie 256 bitów")

    @property
    def directory(self) -> Path:
        return self.root / "research" / "private-archive"

    def path_for(self, object_id: str) -> Path:
        if not SAFE_OBJECT_ID.fullmatch(object_id):
            raise ValueError("Nieprawidłowy identyfikator prywatnego obiektu")
        return self.directory / f"{object_id}.gpa"

    def encrypt_file(
        self,
        source: Path,
        object_id: str,
        *,
        media_type: str = "application/octet-stream",
    ) -> EncryptedObjectResult:
        source = Path(source).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"Nie znaleziono pliku wejściowego: {source}")
        destination = self.path_for(object_id)
        if destination.exists():
            raise FileExistsError(f"Prywatny obiekt już istnieje: {object_id}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        salt = os.urandom(16)
        nonce = os.urandom(12)
        object_key = _derive_object_key(self.master_key, salt, object_id)
        header = {
            "algorithm": "AES-256-GCM",
            "kdf": "HKDF-SHA256",
            "mediaType": media_type,
            "nonce": _b64(nonce),
            "objectId": object_id,
            "salt": _b64(salt),
            "version": OBJECT_VERSION,
        }
        header_bytes = _canonical_json(header)
        prefix = OBJECT_MAGIC + struct.pack(">I", len(header_bytes)) + header_bytes
        _, Cipher, algorithms, modes, _, _, _ = _crypto()
        encryptor = Cipher(algorithms.AES(object_key), modes.GCM(nonce)).encryptor()
        encryptor.authenticate_additional_data(prefix)
        plaintext_hash = hashlib.sha256()
        size = 0
        temporary_handle = tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{object_id}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        )
        temporary = Path(temporary_handle.name)
        try:
            with temporary_handle as output, source.open("rb") as input_file:
                output.write(prefix)
                for chunk in iter(lambda: input_file.read(STREAM_CHUNK_SIZE), b""):
                    size += len(chunk)
                    plaintext_hash.update(chunk)
                    output.write(encryptor.update(chunk))
                output.write(encryptor.finalize())
                output.write(encryptor.tag)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary, 0o600)
            temporary.replace(destination)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        finally:
            object_key = b"\x00" * len(object_key)
        return EncryptedObjectResult(
            path=destination,
            plaintext_sha256=plaintext_hash.hexdigest(),
            ciphertext_sha256=self._sha256(destination),
            size=size,
            media_type=media_type,
        )

    def decrypt_to_path(self, encrypted: Path, destination: Path) -> Path:
        encrypted = Path(encrypted).expanduser().resolve()
        destination = Path(destination).expanduser().resolve()
        if destination.exists():
            raise FileExistsError(f"Plik docelowy już istnieje: {destination}")
        if not encrypted.is_file():
            raise FileNotFoundError("Nie znaleziono prywatnego obiektu")
        file_size = encrypted.stat().st_size
        with encrypted.open("rb") as input_file:
            header, prefix, _ = _read_prefix(input_file)
            expected = {
                "algorithm",
                "kdf",
                "mediaType",
                "nonce",
                "objectId",
                "salt",
                "version",
            }
            if set(header) != expected:
                raise PrivateCryptoError("Nieobsługiwany nagłówek prywatnego obiektu")
            if (
                header["algorithm"] != "AES-256-GCM"
                or header["kdf"] != "HKDF-SHA256"
                or header["version"] != OBJECT_VERSION
                or not isinstance(header["objectId"], str)
            ):
                raise PrivateCryptoError("Nieobsługiwany prywatny obiekt")
            nonce = _unb64(header["nonce"], "nonce")
            salt = _unb64(header["salt"], "salt")
            if len(nonce) != 12 or len(salt) != 16:
                raise PrivateCryptoError("Nieprawidłowy nagłówek prywatnego obiektu")
            ciphertext_size = file_size - len(prefix) - OBJECT_TAG_SIZE
            if ciphertext_size < 0:
                raise PrivateCryptoError("Prywatny obiekt jest ucięty")
            input_file.seek(file_size - OBJECT_TAG_SIZE)
            tag = input_file.read(OBJECT_TAG_SIZE)
            input_file.seek(len(prefix))
            object_key = _derive_object_key(self.master_key, salt, header["objectId"])
            _, Cipher, algorithms, modes, _, _, _ = _crypto()
            decryptor = Cipher(algorithms.AES(object_key), modes.GCM(nonce, tag)).decryptor()
            decryptor.authenticate_additional_data(prefix)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary_handle = tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{destination.name}.",
                suffix=".tmp",
                dir=destination.parent,
                delete=False,
            )
            temporary = Path(temporary_handle.name)
            remaining = ciphertext_size
            try:
                with temporary_handle as output:
                    while remaining:
                        chunk = input_file.read(min(STREAM_CHUNK_SIZE, remaining))
                        if not chunk:
                            raise PrivateCryptoError("Prywatny obiekt jest ucięty")
                        remaining -= len(chunk)
                        output.write(decryptor.update(chunk))
                    output.write(decryptor.finalize())
                    output.flush()
                    os.fsync(output.fileno())
                os.chmod(temporary, 0o600)
                temporary.replace(destination)
            except Exception:
                temporary.unlink(missing_ok=True)
                raise PrivateCryptoError("Nie można odszyfrować prywatnego obiektu") from None
            finally:
                object_key = b"\x00" * len(object_key)
        return destination

    def decrypt_bytes(self, encrypted: Path, *, max_size: int = 25 * 1024 * 1024) -> bytes:
        encrypted = Path(encrypted).expanduser().resolve()
        if encrypted.stat().st_size > max_size + MAX_HEADER_SIZE + 64:
            raise PrivateCryptoError("Prywatny obiekt przekracza limit podglądu")
        with tempfile.TemporaryDirectory(prefix="genea-private-view-") as directory:
            output = Path(directory) / "object"
            self.decrypt_to_path(encrypted, output)
            if output.stat().st_size > max_size:
                raise PrivateCryptoError("Prywatny obiekt przekracza limit podglądu")
            return output.read_bytes()

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(STREAM_CHUNK_SIZE), b""):
                digest.update(chunk)
        return digest.hexdigest()


__all__ = [
    "EncryptedObjectResult",
    "EncryptedObjectStore",
    "PrivateCryptoError",
    "create_recovery_envelope",
    "read_recovery_envelope",
    "recover_data_key",
    "write_recovery_envelope",
]
