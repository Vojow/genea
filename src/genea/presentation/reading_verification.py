from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import stat
import threading
import uuid
import webbrowser
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from ..reading_snippets import REGION_NAMES, verify_reading_snippet_package


API_PREFIX = "/api/presentation/reading-verification"
AUDIT_SCHEMA = "genea.reading-verification-audit-event/v1"
SESSION_SCHEMA = "genea.reading-verification-session/v1"
SUBMISSION_SCHEMA = "genea.reading-verification-submission/v1"
PACKAGE_BINDING_SCHEMA = "genea.reading-verification-package-binding/v1"
MAX_BODY_SIZE = 16 * 1024
SCALES = tuple(REGION_NAMES)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_FIELD_ROUTE = re.compile(
    rf"^{re.escape(API_PREFIX)}/fields/(f[0-9]{{3}})/(field|context|fullAct)$"
)
_COMMAND_ROUTE = re.compile(
    rf"^{re.escape(API_PREFIX)}/fields/(f[0-9]{{3}})/(freeze|reveal|submission)$"
)


class ReadingVerificationError(ValueError):
    """Safe validation or integrity failure in the isolated verification mode."""


class ReadingVerificationConflict(ReadingVerificationError):
    """A state transition or idempotency conflict that maps to HTTP 409."""


class ReadingVerificationStoreError(ReadingVerificationError):
    """A persistent audit or submission store failed replay and is locked."""


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise ReadingVerificationError(
            "Nie można obliczyć bindingu zweryfikowanego manifestu"
        ) from error
    return digest.hexdigest()


def _is_within(path: Path, directory: Path) -> bool:
    return path == directory or directory in path.parents


def _reject_symlink_components(path: Path) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if current.is_symlink():
            raise ReadingVerificationError(
                "Ścieżka nie może prowadzić przez dowiązanie symboliczne"
            )


def validate_reading_verification_bundle(
    root: Path, bundle_directory: Path
) -> tuple[Path, dict[str, Path]]:
    """Validate and allowlist the complete isolated static bundle."""

    candidate = bundle_directory.absolute()
    _reject_symlink_components(candidate)
    resolved = candidate.resolve(strict=False)
    dedicated_root = (root.resolve() / "output" / "blind-review").resolve()
    if resolved != dedicated_root:
        raise ReadingVerificationError(
            "Prebuilt assets muszą wskazywać dedykowany output/blind-review"
        )
    if not resolved.is_dir():
        raise FileNotFoundError(f"Nie znaleziono prebuilt assets: {resolved}")
    entries = list(resolved.rglob("*"))
    if any(entry.is_symlink() for entry in entries):
        raise ReadingVerificationError(
            "Prebuilt assets nie mogą zawierać dowiązań symbolicznych"
        )
    index = resolved / "blind-review" / "index.html"
    if not index.is_file():
        raise FileNotFoundError(
            f"Katalog prebuilt nie zawiera blind-review/index.html: {resolved}"
        )
    try:
        index_text = index.read_text(encoding="utf-8")
    except OSError as error:
        raise ReadingVerificationError("Nie można odczytać index.html blind review") from error
    references = set(
        re.findall(
            r"(?:src|href)=[\"'](/assets/[A-Za-z0-9][A-Za-z0-9._-]*)[\"']",
            index_text,
        )
    )
    if not references:
        raise ReadingVerificationError("Bundle blind review nie wskazuje żadnych assetów")
    static_files = {"/blind-review/index.html": index}
    for url in references:
        path = resolved / url.removeprefix("/")
        if not path.is_file():
            raise ReadingVerificationError(f"Bundle blind review nie zawiera assetu {url}")
        static_files[url] = path
    actual_files = {path for path in entries if path.is_file()}
    if actual_files != set(static_files.values()):
        raise ReadingVerificationError(
            "Bundle blind review zawiera pliki spoza statycznej allowlisty"
        )
    return resolved, static_files


def resolve_audit_output(
    root: Path,
    package_dir: Path,
    audit_output: Path,
) -> Path:
    """Resolve an audit destination without allowing writes into evidence inputs."""

    root_absolute = root.absolute()
    candidate = audit_output if audit_output.is_absolute() else root_absolute / audit_output
    _reject_symlink_components(candidate)
    resolved = candidate.resolve(strict=False)
    canonical_database = (root.resolve() / "research" / "genea.sqlite3").resolve()
    if resolved == canonical_database or resolved.suffix.lower() in {".sqlite", ".sqlite3", ".db"}:
        raise ReadingVerificationError("Audit output nie może wskazywać pliku bazy danych")
    operational_root = (
        root.resolve() / "research" / "tmp" / "reading-verification"
    ).resolve()
    if not _is_within(resolved, operational_root):
        raise ReadingVerificationError(
            "Audit output musi znajdować się w research/tmp/reading-verification"
        )
    protected = {
        "RAW": (root.resolve() / "research" / "archive" / "originals").resolve(),
        "sources": (root.resolve() / "sources").resolve(),
        "pakietu": package_dir.resolve(),
    }
    for label, directory in protected.items():
        if _is_within(resolved, directory):
            raise ReadingVerificationError(
                f"Audit output nie może znajdować się w chronionym katalogu {label}"
            )
    if candidate.exists() and not candidate.is_file():
        raise ReadingVerificationError("Audit output musi być zwykłym plikiem")
    return resolved


class ReadingVerificationBinding:
    """Private durable binding between a run's stores and one verified package."""

    def __init__(self, path: Path, expected: dict[str, Any], *, allow_create: bool):
        self.path = path
        self.expected = expected
        if self.path.exists():
            self.verify()
            return
        if not allow_create:
            raise ReadingVerificationStoreError(
                "Istniejące store nie mają trwałego bindingu pakietu"
            )
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(self.path, flags, 0o600)
        except OSError as error:
            raise ReadingVerificationStoreError(
                f"Nie można utworzyć bindingu pakietu: {error}"
            ) from error
        try:
            encoded = (
                json.dumps(expected, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                + "\n"
            ).encode("utf-8")
            if os.write(fd, encoded) != len(encoded):
                raise ReadingVerificationStoreError(
                    "Nie udało się atomowo zapisać bindingu pakietu"
                )
            os.fsync(fd)
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        self.verify()

    def verify(self) -> None:
        _reject_symlink_components(self.path)
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(self.path, flags)
        except OSError as error:
            raise ReadingVerificationStoreError(
                f"Nie można odczytać bindingu pakietu: {error}"
            ) from error
        try:
            file_stat = os.fstat(fd)
            if not stat.S_ISREG(file_stat.st_mode) or stat.S_IMODE(file_stat.st_mode) != 0o600:
                raise ReadingVerificationStoreError(
                    "Binding pakietu musi być zwykłym prywatnym plikiem 0600"
                )
            raw = ReadingVerificationAudit._read_bytes(fd)
        finally:
            os.close(fd)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ReadingVerificationStoreError(
                "Binding pakietu nie jest poprawnym JSON"
            ) from error
        if payload != self.expected:
            raise ReadingVerificationStoreError(
                "Store są związane z innym zweryfikowanym pakietem"
            )


def _initial_field_state() -> dict[str, Any]:
    return {"visibleScale": "field", "frozenScales": []}


def _available_scales(visible_scale: str) -> list[str]:
    return list(SCALES[: SCALES.index(visible_scale) + 1])


def _apply_transition(
    states: dict[str, dict[str, Any]],
    *,
    field_id: str,
    action: str,
    scale: str,
) -> None:
    state = states.get(field_id)
    if state is None:
        raise ReadingVerificationConflict("Nieznane pole weryfikacji")
    visible_scale = state["visibleScale"]
    frozen_scales = state["frozenScales"]
    if action == "freeze":
        if scale != visible_scale or scale in frozen_scales:
            raise ReadingVerificationConflict("Ta skala nie może zostać teraz zamrożona")
        frozen_scales.append(scale)
        return
    if action != "reveal":
        raise ReadingVerificationError("Nieobsługiwana akcja audytu")
    current_index = SCALES.index(visible_scale)
    if visible_scale not in frozen_scales or current_index + 1 >= len(SCALES):
        raise ReadingVerificationConflict("Bieżąca skala musi być zamrożona przed ujawnieniem")
    if scale != SCALES[current_index + 1]:
        raise ReadingVerificationConflict("Można ujawnić wyłącznie następną skalę")
    state["visibleScale"] = scale


class ReadingVerificationAudit:
    """Append-only, replay-verified audit log with process and thread locking."""

    def __init__(
        self,
        path: Path,
        *,
        run_id: str,
        field_ids: set[str],
        binding: ReadingVerificationBinding,
    ):
        self.path = path
        self.run_id = run_id
        self.field_ids = field_ids
        self.binding = binding
        self._thread_lock = threading.Lock()
        _reject_symlink_components(self.path)
        self.binding.verify()
        fd = self._open(create=not self.path.exists(), readonly=self.path.exists())
        try:
            fcntl.flock(fd, fcntl.LOCK_SH)
            try:
                self._read_and_replay(fd)
            except ReadingVerificationError as error:
                raise ReadingVerificationStoreError(str(error)) from error
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _open(self, *, create: bool = False, readonly: bool = False) -> int:
        if create and readonly:
            raise AssertionError("Nie można jednocześnie tworzyć i otwierać read-only")
        flags = os.O_RDONLY if readonly else os.O_RDWR | os.O_APPEND
        if create:
            flags |= os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(self.path, flags, 0o600)
        except OSError as error:
            raise ReadingVerificationError(f"Nie można otworzyć audytu: {error}") from error
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ReadingVerificationError("Audit output musi być zwykłym plikiem")
            mode = stat.S_IMODE(os.fstat(fd).st_mode)
            if create:
                os.fchmod(fd, 0o600)
            elif mode != 0o600:
                raise ReadingVerificationStoreError(
                    "Audit istnieje, ale nie jest prywatnym store 0600"
                )
            return fd
        except Exception:
            os.close(fd)
            raise

    @staticmethod
    def _read_bytes(fd: int) -> bytes:
        os.lseek(fd, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 64 * 1024)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)

    def _read_and_replay(
        self, fd: int
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        raw = self._read_bytes(fd)
        if not raw:
            return [], {field_id: _initial_field_state() for field_id in self.field_ids}
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ReadingVerificationError("Audit JSONL nie jest poprawnym UTF-8") from error
        if not text.endswith("\n"):
            raise ReadingVerificationError("Audit JSONL zawiera niepełny ostatni rekord")
        states = {field_id: _initial_field_state() for field_id in self.field_ids}
        events: list[dict[str, Any]] = []
        command_ids: set[str] = set()
        event_ids: set[str] = set()
        previous_hash: str | None = None
        for sequence, line in enumerate(text.splitlines(), start=1):
            if not line:
                raise ReadingVerificationError("Audit JSONL zawiera pusty rekord")
            try:
                event = json.loads(line)
            except json.JSONDecodeError as error:
                raise ReadingVerificationError("Audit JSONL zawiera uszkodzony rekord") from error
            if not isinstance(event, dict):
                raise ReadingVerificationError("Rekord audytu musi być obiektem JSON")
            action = event.get("action")
            required = {
                "schema",
                "sequence",
                "eventId",
                "commandId",
                "occurredAt",
                "runId",
                "fieldId",
                "action",
                "scale",
                "previousEventHash",
                "eventHash",
            }
            if action == "freeze":
                required.add("submissionSha256")
            if set(event) != required:
                raise ReadingVerificationError("Rekord audytu ma niepoprawny zestaw pól")
            if event.get("schema") != AUDIT_SCHEMA or event.get("sequence") != sequence:
                raise ReadingVerificationError("Schema albo sekwencja audytu jest niepoprawna")
            if event.get("runId") != self.run_id:
                raise ReadingVerificationError("Audit należy do innego runId")
            event_id = event.get("eventId")
            command_id = event.get("commandId")
            if (
                not isinstance(event_id, str)
                or not _OPAQUE_ID.fullmatch(event_id)
                or event_id in event_ids
                or not isinstance(command_id, str)
                or not _OPAQUE_ID.fullmatch(command_id)
                or command_id in command_ids
            ):
                raise ReadingVerificationError("Identyfikatory audytu są niepoprawne lub powtórzone")
            try:
                occurred_at = datetime.fromisoformat(str(event.get("occurredAt")))
            except ValueError as error:
                raise ReadingVerificationError("occurredAt audytu jest niepoprawne") from error
            if occurred_at.tzinfo is None:
                raise ReadingVerificationError("occurredAt audytu musi zawierać strefę czasową")
            field_id = event.get("fieldId")
            scale = event.get("scale")
            if field_id not in self.field_ids or scale not in SCALES:
                raise ReadingVerificationError("Rekord audytu wskazuje nieznane pole lub skalę")
            if action not in {"freeze", "reveal"}:
                raise ReadingVerificationError("Rekord audytu ma nieobsługiwaną akcję")
            if action == "freeze" and not _SHA256.fullmatch(
                str(event.get("submissionSha256", ""))
            ):
                raise ReadingVerificationError("submissionSha256 audytu jest niepoprawne")
            if event.get("previousEventHash") != previous_hash:
                raise ReadingVerificationError("Łańcuch hashy audytu jest przerwany")
            hash_input = dict(event)
            event_hash = hash_input.pop("eventHash", None)
            if not isinstance(event_hash, str) or not hmac.compare_digest(
                event_hash, _canonical_hash(hash_input)
            ):
                raise ReadingVerificationError("eventHash audytu jest niepoprawny")
            try:
                _apply_transition(
                    states,
                    field_id=field_id,
                    action=action,
                    scale=scale,
                )
            except ReadingVerificationConflict as error:
                raise ReadingVerificationError(
                    "Audit zawiera niepoprawną sekwencję ujawnień"
                ) from error
            events.append(event)
            command_ids.add(command_id)
            event_ids.add(event_id)
            previous_hash = event_hash
        return events, states

    def snapshot(self) -> dict[str, dict[str, Any]]:
        _, states = self.details()
        return states

    def details(
        self,
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        with self._thread_lock:
            self.binding.verify()
            fd = self._open()
            try:
                fcntl.flock(fd, fcntl.LOCK_SH)
                try:
                    return self._read_and_replay(fd)
                except ReadingVerificationError as error:
                    raise ReadingVerificationStoreError(str(error)) from error
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    def execute(
        self,
        *,
        command_id: str,
        field_id: str,
        action: str,
        scale: str,
        submission_sha256: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], bool]:
        if not _OPAQUE_ID.fullmatch(command_id):
            raise ReadingVerificationError("commandId ma niepoprawny format")
        if field_id not in self.field_ids or scale not in SCALES:
            raise ReadingVerificationError("Nieznane pole lub skala")
        if action == "freeze":
            if submission_sha256 is None or not _SHA256.fullmatch(submission_sha256):
                raise ReadingVerificationError("Freeze wymaga pełnego SHA-256 submission")
        elif action == "reveal":
            if submission_sha256 is not None:
                raise ReadingVerificationError("Reveal nie przyjmuje submissionSha256")
        else:
            raise ReadingVerificationError("Nieobsługiwana akcja")
        with self._thread_lock:
            self.binding.verify()
            fd = self._open()
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                try:
                    events, states = self._read_and_replay(fd)
                except ReadingVerificationError as error:
                    raise ReadingVerificationStoreError(str(error)) from error
                signature = (field_id, action, scale, submission_sha256)
                for existing in events:
                    if existing["commandId"] != command_id:
                        continue
                    existing_signature = (
                        existing["fieldId"],
                        existing["action"],
                        existing["scale"],
                        existing.get("submissionSha256"),
                    )
                    if existing_signature != signature:
                        raise ReadingVerificationConflict(
                            "commandId został wcześniej użyty z inną komendą"
                        )
                    return existing, states[field_id], True
                _apply_transition(
                    states,
                    field_id=field_id,
                    action=action,
                    scale=scale,
                )
                event: dict[str, Any] = {
                    "schema": AUDIT_SCHEMA,
                    "sequence": len(events) + 1,
                    "eventId": f"evt-{uuid.uuid4()}",
                    "commandId": command_id,
                    "occurredAt": datetime.now(timezone.utc).isoformat(),
                    "runId": self.run_id,
                    "fieldId": field_id,
                    "action": action,
                    "scale": scale,
                    "previousEventHash": events[-1]["eventHash"] if events else None,
                }
                if action == "freeze":
                    event["submissionSha256"] = submission_sha256
                event["eventHash"] = _canonical_hash(event)
                encoded = (
                    json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    + "\n"
                ).encode("utf-8")
                written = os.write(fd, encoded)
                if written != len(encoded):
                    raise ReadingVerificationError("Nie udało się atomowo dopisać audytu")
                os.fsync(fd)
                return event, states[field_id], False
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)


class ReadingVerificationSubmissions:
    """Append-only proposal values, deliberately separated from the audit chain."""

    def __init__(
        self,
        path: Path,
        *,
        run_id: str,
        field_ids: set[str],
        binding: ReadingVerificationBinding,
    ):
        self.path = path
        self.run_id = run_id
        self.field_ids = field_ids
        self.binding = binding
        self._thread_lock = threading.Lock()
        _reject_symlink_components(self.path)
        self.binding.verify()
        fd = self._open(create=not self.path.exists(), readonly=self.path.exists())
        try:
            fcntl.flock(fd, fcntl.LOCK_SH)
            try:
                self._read(fd)
            except ReadingVerificationError as error:
                raise ReadingVerificationStoreError(str(error)) from error
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _open(self, *, create: bool = False, readonly: bool = False) -> int:
        if create and readonly:
            raise AssertionError("Nie można jednocześnie tworzyć i otwierać read-only")
        flags = os.O_RDONLY if readonly else os.O_RDWR | os.O_APPEND
        if create:
            flags |= os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(self.path, flags, 0o600)
        except OSError as error:
            raise ReadingVerificationStoreError(
                f"Nie można otworzyć magazynu submissions: {error}"
            ) from error
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ReadingVerificationStoreError(
                    "Submission output musi być zwykłym plikiem"
                )
            mode = stat.S_IMODE(os.fstat(fd).st_mode)
            if create:
                os.fchmod(fd, 0o600)
            elif mode != 0o600:
                raise ReadingVerificationStoreError(
                    "Submissions istnieje, ale nie jest prywatnym store 0600"
                )
            return fd
        except Exception:
            os.close(fd)
            raise

    def _read(self, fd: int) -> list[dict[str, Any]]:
        raw = ReadingVerificationAudit._read_bytes(fd)
        if not raw:
            return []
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ReadingVerificationError(
                "Submissions JSONL nie jest poprawnym UTF-8"
            ) from error
        if not text.endswith("\n"):
            raise ReadingVerificationError(
                "Submissions JSONL zawiera niepełny ostatni rekord"
            )
        records: list[dict[str, Any]] = []
        command_ids: set[str] = set()
        submitted: set[tuple[str, str]] = set()
        required = {
            "schema",
            "sequence",
            "runId",
            "fieldId",
            "scale",
            "literalReading",
            "submissionSha256",
            "occurredAt",
            "commandId",
            "previousSubmissionHash",
            "submissionRecordHash",
        }
        previous_hash: str | None = None
        for sequence, line in enumerate(text.splitlines(), start=1):
            if not line:
                raise ReadingVerificationError("Submissions JSONL zawiera pusty rekord")
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ReadingVerificationError(
                    "Submissions JSONL zawiera uszkodzony rekord"
                ) from error
            if not isinstance(record, dict) or set(record) != required:
                raise ReadingVerificationError(
                    "Rekord submission ma niepoprawny zestaw pól"
                )
            if (
                record.get("schema") != SUBMISSION_SCHEMA
                or record.get("sequence") != sequence
                or record.get("runId") != self.run_id
            ):
                raise ReadingVerificationError(
                    "Rekord submission ma inną schema, sekwencję albo runId"
                )
            field_id = record.get("fieldId")
            scale = record.get("scale")
            command_id = record.get("commandId")
            literal = record.get("literalReading")
            digest = record.get("submissionSha256")
            if field_id not in self.field_ids or scale not in SCALES:
                raise ReadingVerificationError(
                    "Rekord submission wskazuje nieznane pole lub skalę"
                )
            if (
                not isinstance(command_id, str)
                or not _OPAQUE_ID.fullmatch(command_id)
                or command_id in command_ids
            ):
                raise ReadingVerificationError(
                    "commandId submission jest niepoprawny lub powtórzony"
                )
            if not isinstance(literal, str) or not literal or len(literal) > 10_000:
                raise ReadingVerificationError("literalReading submission jest niepoprawny")
            actual_digest = hashlib.sha256(literal.encode("utf-8")).hexdigest()
            if not isinstance(digest, str) or not hmac.compare_digest(digest, actual_digest):
                raise ReadingVerificationError("SHA-256 submission jest niepoprawny")
            try:
                occurred_at = datetime.fromisoformat(str(record.get("occurredAt")))
            except ValueError as error:
                raise ReadingVerificationError(
                    "occurredAt submission jest niepoprawne"
                ) from error
            if occurred_at.tzinfo is None:
                raise ReadingVerificationError(
                    "occurredAt submission musi zawierać strefę czasową"
                )
            if record.get("previousSubmissionHash") != previous_hash:
                raise ReadingVerificationError(
                    "Łańcuch hashy submissions jest przerwany"
                )
            hash_input = dict(record)
            record_hash = hash_input.pop("submissionRecordHash", None)
            if not isinstance(record_hash, str) or not hmac.compare_digest(
                record_hash, _canonical_hash(hash_input)
            ):
                raise ReadingVerificationError(
                    "submissionRecordHash jest niepoprawny"
                )
            key = (field_id, scale)
            if key in submitted:
                raise ReadingVerificationError(
                    "Submission zawiera więcej niż jedną propozycję tej skali"
                )
            submitted.add(key)
            command_ids.add(command_id)
            records.append(record)
            previous_hash = record_hash
        return records

    def records(self) -> list[dict[str, Any]]:
        with self._thread_lock:
            self.binding.verify()
            fd = self._open()
            try:
                fcntl.flock(fd, fcntl.LOCK_SH)
                try:
                    return self._read(fd)
                except ReadingVerificationError as error:
                    raise ReadingVerificationStoreError(str(error)) from error
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    def execute(
        self,
        *,
        command_id: str,
        field_id: str,
        scale: str,
        literal_reading: str,
        expected_sha256: str,
    ) -> tuple[dict[str, Any], bool]:
        if not _OPAQUE_ID.fullmatch(command_id):
            raise ReadingVerificationError("commandId ma niepoprawny format")
        if field_id not in self.field_ids or scale not in SCALES:
            raise ReadingVerificationError("Nieznane pole lub skala")
        if not literal_reading or len(literal_reading) > 10_000:
            raise ReadingVerificationError("literalReading nie może być pusty")
        actual_sha256 = hashlib.sha256(literal_reading.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(actual_sha256, expected_sha256):
            raise ReadingVerificationConflict(
                "literalReading nie odpowiada zamrożonemu submissionSha256"
            )
        with self._thread_lock:
            self.binding.verify()
            fd = self._open()
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                try:
                    records = self._read(fd)
                except ReadingVerificationError as error:
                    raise ReadingVerificationStoreError(str(error)) from error
                signature = (field_id, scale, literal_reading, actual_sha256)
                for record in records:
                    if record["commandId"] == command_id:
                        existing = (
                            record["fieldId"],
                            record["scale"],
                            record["literalReading"],
                            record["submissionSha256"],
                        )
                        if existing != signature:
                            raise ReadingVerificationConflict(
                                "commandId został wcześniej użyty z innym submission"
                            )
                        return record, True
                    if (record["fieldId"], record["scale"]) == (field_id, scale):
                        raise ReadingVerificationConflict(
                            "Ta skala ma już zachowaną propozycję odczytu"
                        )
                record = {
                    "schema": SUBMISSION_SCHEMA,
                    "sequence": len(records) + 1,
                    "runId": self.run_id,
                    "fieldId": field_id,
                    "scale": scale,
                    "literalReading": literal_reading,
                    "submissionSha256": actual_sha256,
                    "occurredAt": datetime.now(timezone.utc).isoformat(),
                    "commandId": command_id,
                    "previousSubmissionHash": (
                        records[-1]["submissionRecordHash"] if records else None
                    ),
                }
                record["submissionRecordHash"] = _canonical_hash(record)
                encoded = (
                    json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    + "\n"
                ).encode("utf-8")
                if os.write(fd, encoded) != len(encoded):
                    raise ReadingVerificationStoreError(
                        "Nie udało się atomowo dopisać submission"
                    )
                os.fsync(fd)
                return record, False
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)


class ReadingVerificationApplication:
    """Minimal projection and state machine for one verified snippet package."""

    def __init__(
        self,
        root: Path,
        package_dir: Path,
        *,
        run_id: str | None = None,
        audit_output: Path | None = None,
    ):
        self.root = root.resolve()
        self.run_id = run_id or f"run-{secrets.token_hex(12)}"
        if not _RUN_ID.fullmatch(self.run_id):
            raise ReadingVerificationError("runId ma niepoprawny format")
        raw_package = package_dir if package_dir.is_absolute() else root.absolute() / package_dir
        manifest = verify_reading_snippet_package(self.root, raw_package)
        self.package_dir = raw_package.resolve()
        self.csrf_token = secrets.token_urlsafe(32)
        self.fields: list[dict[str, str]] = []
        self.assets: dict[tuple[str, str], Path] = {}
        manifest_fields = manifest.get("fields")
        if not isinstance(manifest_fields, list) or not manifest_fields:
            raise ReadingVerificationError("Zweryfikowany pakiet nie zawiera pól")
        if len(manifest_fields) > 999:
            raise ReadingVerificationError("Pakiet może zawierać najwyżej 999 pól")
        for index, source_field in enumerate(manifest_fields, start=1):
            if not isinstance(source_field, dict) or not isinstance(source_field.get("kind"), str):
                raise ReadingVerificationError("Zweryfikowany pakiet zawiera niepoprawne pole")
            field_id = f"f{index:03d}"
            self.fields.append({"id": field_id, "kind": source_field["kind"]})
            regions = source_field.get("regions")
            if not isinstance(regions, dict):
                raise ReadingVerificationError("Zweryfikowany pakiet nie zawiera regionów")
            for scale in SCALES:
                asset = regions.get(scale)
                if not isinstance(asset, dict) or not isinstance(asset.get("path"), str):
                    raise ReadingVerificationError("Zweryfikowany pakiet nie zawiera assetu")
                candidate = (self.package_dir / asset["path"]).resolve()
                if not _is_within(candidate, self.package_dir) or not candidate.is_file():
                    raise ReadingVerificationError("Asset pakietu wychodzi poza allowlistę")
                self.assets[(field_id, scale)] = candidate
        config_fingerprint = manifest.get("configFingerprint")
        if not isinstance(config_fingerprint, str) or not _SHA256.fullmatch(
            config_fingerprint
        ):
            raise ReadingVerificationError(
                "Zweryfikowany pakiet nie zawiera poprawnego configFingerprint"
            )
        field_binding = {
            field["id"]: manifest_fields[index]["sourceSegmentId"]
            for index, field in enumerate(self.fields)
        }
        if not all(
            isinstance(backing_id, str) and backing_id
            for backing_id in field_binding.values()
        ):
            raise ReadingVerificationError(
                "Zweryfikowany pakiet nie zawiera backing identity pól"
            )
        manifest_sha256 = _sha256_file(self.package_dir / "manifest.json")
        requested_audit = audit_output or (
            self.root / "research" / "tmp" / "reading-verification" / self.run_id / "events.jsonl"
        )
        self.audit_path = resolve_audit_output(
            self.root, self.package_dir, Path(requested_audit)
        )
        self.submissions_path = resolve_audit_output(
            self.root,
            self.package_dir,
            self.audit_path.with_name("submissions.jsonl"),
        )
        if self.submissions_path == self.audit_path:
            raise ReadingVerificationError(
                "Audit output nie może używać nazwy submissions.jsonl"
            )
        self.binding_path = resolve_audit_output(
            self.root,
            self.package_dir,
            self.audit_path.with_name("package-binding.json"),
        )
        if self.binding_path == self.audit_path:
            raise ReadingVerificationError(
                "Audit output nie może używać nazwy package-binding.json"
            )
        self.audit_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        _reject_symlink_components(self.audit_path.parent)
        expected_binding = {
            "schema": PACKAGE_BINDING_SCHEMA,
            "runId": self.run_id,
            "configFingerprint": config_fingerprint,
            "manifestSha256": manifest_sha256,
            "neutralFieldMapSha256": _canonical_hash(field_binding),
        }
        expected_binding["bindingHash"] = _canonical_hash(expected_binding)
        stores_preexist = self.audit_path.exists() or self.submissions_path.exists()
        if stores_preexist and not self.binding_path.exists():
            raise ReadingVerificationStoreError(
                "Istniejący plik nie jest własnym store tego runId"
            )
        self.binding = ReadingVerificationBinding(
            self.binding_path,
            expected_binding,
            allow_create=not stores_preexist,
        )
        self.audit = ReadingVerificationAudit(
            self.audit_path,
            run_id=self.run_id,
            field_ids={field["id"] for field in self.fields},
            binding=self.binding,
        )
        self.submissions = ReadingVerificationSubmissions(
            self.submissions_path,
            run_id=self.run_id,
            field_ids={field["id"] for field in self.fields},
            binding=self.binding,
        )
        self._combined_state()

    @staticmethod
    def _public_state(
        field: dict[str, str],
        state: dict[str, Any],
        submitted_scales: set[str],
    ) -> dict[str, Any]:
        return {
            "id": field["id"],
            "kind": field["kind"],
            "visibleScale": state["visibleScale"],
            "frozenScales": list(state["frozenScales"]),
            "availableScales": _available_scales(state["visibleScale"]),
            "submittedScales": [scale for scale in SCALES if scale in submitted_scales],
        }

    def _combined_state(
        self,
    ) -> tuple[
        dict[str, dict[str, Any]],
        dict[tuple[str, str], dict[str, Any]],
        dict[tuple[str, str], str],
    ]:
        events, states = self.audit.details()
        records = self.submissions.records()
        submissions = {
            (record["fieldId"], record["scale"]): record for record in records
        }
        frozen_hashes = {
            (event["fieldId"], event["scale"]): event["submissionSha256"]
            for event in events
            if event["action"] == "freeze"
        }
        for key, record in submissions.items():
            expected = frozen_hashes.get(key)
            if expected is None or not hmac.compare_digest(
                expected, record["submissionSha256"]
            ):
                raise ReadingVerificationStoreError(
                    "Submission nie odpowiada zamrożonemu zdarzeniu audytu"
                )
        for event in events:
            if event["action"] != "reveal":
                continue
            target_index = SCALES.index(event["scale"])
            previous_key = (event["fieldId"], SCALES[target_index - 1])
            if previous_key not in submissions:
                raise ReadingVerificationStoreError(
                    "Audit ujawnia skalę bez zachowanej propozycji poprzedniej skali"
                )
        return states, submissions, frozen_hashes

    def session(self) -> dict[str, Any]:
        states, submissions, _ = self._combined_state()
        return {
            "schema": SESSION_SCHEMA,
            "runId": self.run_id,
            "csrfToken": self.csrf_token,
            "fields": [
                self._public_state(
                    field,
                    states[field["id"]],
                    {
                        scale
                        for field_id, scale in submissions
                        if field_id == field["id"]
                    },
                )
                for field in self.fields
            ],
        }

    def asset(self, field_id: str, scale: str) -> Path | None:
        path = self.assets.get((field_id, scale))
        if path is None:
            return None
        states, _, _ = self._combined_state()
        state = states.get(field_id)
        if state is None or scale not in _available_scales(state["visibleScale"]):
            return None
        return path

    def command(
        self, field_id: str, action: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        allowed = {"commandId", "scale"}
        if action == "freeze":
            allowed.add("submissionSha256")
        if set(payload) != allowed:
            raise ReadingVerificationError("Body komendy ma niepoprawny zestaw pól")
        command_id = payload.get("commandId")
        scale = payload.get("scale")
        if not isinstance(command_id, str) or not isinstance(scale, str):
            raise ReadingVerificationError("commandId i scale muszą być tekstem")
        submission = payload.get("submissionSha256")
        if submission is not None and not isinstance(submission, str):
            raise ReadingVerificationError("submissionSha256 musi być tekstem")
        if action == "reveal":
            if field_id not in {field["id"] for field in self.fields} or scale not in SCALES:
                raise ReadingVerificationError("Nieznane pole lub skala")
            _, submissions, _ = self._combined_state()
            target_index = SCALES.index(scale)
            if target_index == 0 or (field_id, SCALES[target_index - 1]) not in submissions:
                raise ReadingVerificationConflict(
                    "Reveal wymaga zachowanej propozycji bieżącej skali"
                )
        event, state, idempotent = self.audit.execute(
            command_id=command_id,
            field_id=field_id,
            action=action,
            scale=scale,
            submission_sha256=submission,
        )
        field = next(item for item in self.fields if item["id"] == field_id)
        _, submissions, _ = self._combined_state()
        submitted_scales = {
            stored_scale
            for stored_field, stored_scale in submissions
            if stored_field == field_id
        }
        return {
            "ok": True,
            "idempotent": idempotent,
            "event": {
                "sequence": event["sequence"],
                "eventId": event["eventId"],
                "commandId": event["commandId"],
                "action": event["action"],
                "scale": event["scale"],
            },
            "field": self._public_state(field, state, submitted_scales),
        }

    def submit(self, field_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if set(payload) != {"commandId", "scale", "literalReading"}:
            raise ReadingVerificationError("Body submission ma niepoprawny zestaw pól")
        command_id = payload.get("commandId")
        scale = payload.get("scale")
        literal = payload.get("literalReading")
        if not all(isinstance(value, str) for value in (command_id, scale, literal)):
            raise ReadingVerificationError(
                "commandId, scale i literalReading muszą być tekstem"
            )
        states, _, frozen_hashes = self._combined_state()
        expected_sha256 = frozen_hashes.get((field_id, scale))
        if expected_sha256 is None:
            raise ReadingVerificationConflict(
                "Submission wymaga wcześniejszego freeze tej skali"
            )
        record, idempotent = self.submissions.execute(
            command_id=command_id,
            field_id=field_id,
            scale=scale,
            literal_reading=literal,
            expected_sha256=expected_sha256,
        )
        _, submissions, _ = self._combined_state()
        field = next(item for item in self.fields if item["id"] == field_id)
        return {
            "ok": True,
            "idempotent": idempotent,
            "submission": {
                "commandId": record["commandId"],
                "fieldId": record["fieldId"],
                "scale": record["scale"],
                "submissionSha256": record["submissionSha256"],
            },
            "field": self._public_state(
                field,
                states[field_id],
                {
                    stored_scale
                    for stored_field, stored_scale in submissions
                    if stored_field == field_id
                },
            ),
        }


class ReadingVerificationRequestHandler(SimpleHTTPRequestHandler):
    server_version = "GeneaReadingVerification/1"

    def __init__(
        self,
        *args: Any,
        application: ReadingVerificationApplication,
        static_files: dict[str, Path] | None = None,
        **kwargs: Any,
    ):
        self.application = application
        self.static_files = static_files or {}
        super().__init__(*args, **kwargs)

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'none'; object-src 'none'; "
            "frame-ancestors 'none'; form-action 'self'; img-src 'self'; "
            "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'",
        )
        super().end_headers()

    def _request_origin_is_loopback(self, *, required: bool) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return not required
        try:
            parsed = urlsplit(origin)
            port = parsed.port
        except ValueError:
            return False
        return (
            parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            and (port if port is not None else 80) == self.server.server_port
            and not parsed.username
            and not parsed.password
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
        )

    def _host_is_loopback(self) -> bool:
        host = self.headers.get("Host")
        if not host:
            return False
        try:
            parsed = urlsplit(f"//{host}")
            port = parsed.port
        except ValueError:
            return False
        return (
            parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            and (port if port is not None else 80) == self.server.server_port
            and not parsed.username
            and not parsed.password
            and not parsed.path
            and not parsed.query
            and not parsed.fragment
        )

    def _send_api_json(self, status_code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "private, no-store")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _send_api_error(self, status_code: int, code: str) -> None:
        self._send_api_json(status_code, {"ok": False, "error": code})

    def _request_allowed(self, *, mutation: bool) -> bool:
        if not self._host_is_loopback() or not self._request_origin_is_loopback(required=mutation):
            self._send_api_error(403, "request_origin_rejected")
            return False
        if mutation and not hmac.compare_digest(
            self.headers.get("X-CSRF-Token", ""), self.application.csrf_token
        ):
            self._send_api_error(403, "csrf_rejected")
            return False
        return True

    def do_GET(self) -> None:
        path = unquote(urlsplit(self.path).path)
        if path.startswith("/api/"):
            if not self._request_allowed(mutation=False):
                return
            if path == f"{API_PREFIX}/session":
                try:
                    session = self.application.session()
                except ReadingVerificationStoreError:
                    self._send_api_error(423, "verification_store_locked")
                    return
                self._send_api_json(200, session)
                return
            match = _FIELD_ROUTE.fullmatch(path)
            if match:
                try:
                    asset = self.application.asset(match.group(1), match.group(2))
                except ReadingVerificationStoreError:
                    self._send_api_error(423, "verification_store_locked")
                    return
                if asset is None:
                    self._send_api_error(404, "not_found")
                    return
                try:
                    body = asset.read_bytes()
                except OSError:
                    self._send_api_error(404, "not_found")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "private, no-store")
                self.send_header("Pragma", "no-cache")
                self.end_headers()
                self.wfile.write(body)
                return
            self._send_api_error(404, "not_found")
            return
        if not self._host_is_loopback() or not self._request_origin_is_loopback(required=False):
            self.send_error(403)
            return
        super().do_GET()

    def do_POST(self) -> None:
        path = unquote(urlsplit(self.path).path)
        if not path.startswith("/api/"):
            self._send_api_error(404, "not_found")
            return
        if not self._request_allowed(mutation=True):
            return
        if not path.startswith(API_PREFIX):
            self._send_api_error(404, "not_found")
            return
        match = _COMMAND_ROUTE.fullmatch(path)
        if not match:
            self._send_api_error(404, "not_found")
            return
        if self.headers.get("Transfer-Encoding"):
            self._send_api_error(400, "invalid_body")
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._send_api_error(400, "invalid_body")
            return
        if length <= 0 or length > MAX_BODY_SIZE:
            self._send_api_error(413 if length > MAX_BODY_SIZE else 400, "invalid_body")
            return
        if self.headers.get_content_type() != "application/json":
            self._send_api_error(415, "json_required")
            return
        try:
            payload = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_api_error(400, "invalid_json")
            return
        if not isinstance(payload, dict):
            self._send_api_error(400, "invalid_body")
            return
        try:
            if match.group(2) == "submission":
                result = self.application.submit(match.group(1), payload)
            else:
                result = self.application.command(match.group(1), match.group(2), payload)
        except ReadingVerificationStoreError:
            self._send_api_error(423, "verification_store_locked")
            return
        except ReadingVerificationConflict:
            self._send_api_error(409, "command_conflict")
            return
        except ReadingVerificationError:
            self._send_api_error(400, "invalid_command")
            return
        self._send_api_json(200, result)

    def do_OPTIONS(self) -> None:
        self._send_api_error(405, "method_not_allowed")

    def send_head(self):  # type: ignore[no-untyped-def]
        path = unquote(urlsplit(self.path).path)
        blocked = (
            path == "/data/research.json"
            or path.startswith("/local-artifacts/")
            or path.startswith("/local-volume-index/")
            or path.startswith("/api/private/")
            or path.startswith("/api/presentation/volume-index/")
        )
        if blocked:
            self.send_error(404)
            return None
        expected_route = f"/blind-review/{self.application.run_id}"
        if path in {"", "/", expected_route, f"{expected_route}/"}:
            path = "/blind-review/index.html"
        elif path.startswith("/blind-review/"):
            self.send_error(404)
            return None
        static_file = self.static_files.get(path)
        if static_file is None:
            self.send_error(404)
            return None
        try:
            _reject_symlink_components(static_file)
            flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(static_file, flags)
            handle = os.fdopen(fd, "rb")
        except (OSError, ReadingVerificationError):
            self.send_error(404)
            return None
        file_stat = os.fstat(handle.fileno())
        media_type = mimetypes.guess_type(static_file.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(file_stat.st_size))
        self.send_header("Cache-Control", "private, no-store")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        return handle


def serve_reading_verification(
    root: Path,
    package_dir: Path,
    *,
    port: int = 8766,
    open_browser: bool = True,
    prebuilt_assets: Path | None = None,
    run_id: str | None = None,
    audit_output: Path | None = None,
) -> None:
    """Serve one verified package on loopback without frontend export or SQLite."""

    if not 1 <= port <= 65535:
        raise ValueError("port musi mieścić się w zakresie 1–65535")
    application = ReadingVerificationApplication(
        root,
        package_dir,
        run_id=run_id,
        audit_output=audit_output,
    )
    if prebuilt_assets is None:
        # Local import avoids coupling the isolated application to export_frontend.
        from .server import build_reading_verification_assets

        frontend_directory = build_reading_verification_assets(root)
    else:
        frontend_directory = Path(prebuilt_assets).expanduser().absolute()
    frontend_directory, static_files = validate_reading_verification_bundle(
        root, frontend_directory
    )
    handler = partial(
        ReadingVerificationRequestHandler,
        application=application,
        static_files=static_files,
        directory=str(frontend_directory),
    )
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    url = f"http://127.0.0.1:{port}/blind-review/{application.run_id}"
    print(f"Genea reading verification: {url} (Ctrl+C kończy serwer)")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
