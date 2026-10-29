from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import unquote, urlsplit

from .db import connect
from .archive_requests import list_archive_requests
from .private_crypto import EncryptedObjectStore, PrivateCryptoError
from .private_data import (
    PrivateDataConflict,
    add_family_note,
    add_private_relationship,
    create_private_person,
    link_private_occurrence,
    list_private_people,
    private_person_profile,
    private_tree_overlay,
    set_private_field,
)
from .private_lifecycle import (
    lock_private_ui,
    private_lock_path,
    unlock_private_ui,
    verify_private_password,
)
from .identity_curation import (
    IdentityCurationConflict,
    IdentityCurationError,
    apply_identity_decision,
    identity_candidate_preview,
)
from .command_envelope import set_projection_status
from .event_curation import (
    EventRecordCurationConflict,
    EventRecordCurationError,
    apply_event_record_decision,
    event_record_candidate_preview,
)
from .storage import SQLCipherBackend, configured_database_backend
from .workspace import utc_now
from .volume_index_workflow import review_volume_region


SESSION_COOKIE = "genea_private_session"
MAX_BODY_SIZE = 16 * 1024


@dataclass
class _Session:
    csrf: str
    last_seen: float
    strict_until: float | None = None


@dataclass(frozen=True)
class PrivateResponse:
    status: int
    body: bytes
    content_type: str = "application/json; charset=utf-8"
    headers: tuple[tuple[str, str], ...] = ()


class PrivateSessionManager:
    def __init__(
        self,
        root: Path,
        *,
        timeout_seconds: int = 15 * 60,
        strict_seconds: int = 5 * 60,
        clock=time.monotonic,
    ):
        if timeout_seconds < 60 or strict_seconds < 30:
            raise ValueError("Timeout prywatnej sesji jest zbyt krótki")
        self.root = Path(root).resolve()
        self.timeout_seconds = timeout_seconds
        self.strict_seconds = strict_seconds
        self.clock = clock
        self._sessions: dict[str, _Session] = {}
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.RLock()

    def _purge(self) -> None:
        now = self.clock()
        expired = [
            token
            for token, session in self._sessions.items()
            if now - session.last_seen >= self.timeout_seconds
        ]
        for token in expired:
            self._sessions.pop(token, None)

    def login(self, password: str, client: str) -> tuple[str, _Session]:
        now = self.clock()
        with self._lock:
            failures = [stamp for stamp in self._failures.get(client, []) if now - stamp < 300]
            self._failures[client] = failures
            if len(failures) >= 5:
                raise PermissionError("Zbyt wiele prób; spróbuj później")
            if not verify_private_password(self.root, password):
                failures.append(now)
                raise PermissionError("Nieprawidłowe dane odblokowania")
            unlock_private_ui(self.root, password)
            self._failures.pop(client, None)
            token = secrets.token_urlsafe(32)
            session = _Session(csrf=secrets.token_urlsafe(32), last_seen=now)
            self._sessions[token] = session
            return token, session

    def authenticate(self, token: str | None) -> _Session | None:
        if not token or private_lock_path(self.root).is_file():
            return None
        with self._lock:
            self._purge()
            session = self._sessions.get(token)
            if session is None:
                return None
            session.last_seen = self.clock()
            return session

    def require_csrf(self, session: _Session, supplied: str | None) -> None:
        if not supplied or not secrets.compare_digest(session.csrf, supplied):
            raise PermissionError("Nieprawidłowy token CSRF")

    def elevate_strict(self, session: _Session, password: str) -> None:
        if not verify_private_password(self.root, password):
            raise PermissionError("Nieprawidłowe dane odblokowania")
        session.strict_until = self.clock() + self.strict_seconds

    def strict_allowed(self, session: _Session) -> bool:
        return bool(session.strict_until and session.strict_until > self.clock())

    def logout(self, token: str | None) -> None:
        if token:
            with self._lock:
                self._sessions.pop(token, None)

    def lock_all(self) -> None:
        with self._lock:
            self._sessions.clear()
        lock_private_ui(self.root)

    def expires_in(self, session: _Session) -> int:
        return max(0, int(self.timeout_seconds - (self.clock() - session.last_seen)))


class PrivateApiApplication:
    def __init__(
        self,
        root: Path,
        manager: PrivateSessionManager,
        *,
        refresh_presentation: Callable[[], object] | None = None,
    ):
        self.root = Path(root).resolve()
        self.manager = manager
        self.refresh_presentation = refresh_presentation
        backend = configured_database_backend(self.root)
        if not isinstance(backend, SQLCipherBackend):
            raise RuntimeError("Prywatne API wymaga aktywnej zaszyfrowanej bazy")

    @staticmethod
    def _json(status: int, value: object, headers: tuple[tuple[str, str], ...] = ()) -> PrivateResponse:
        return PrivateResponse(
            status,
            json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            headers=headers,
        )

    @staticmethod
    def _token(headers: Mapping[str, str]) -> str | None:
        cookie = SimpleCookie()
        try:
            cookie.load(headers.get("Cookie", ""))
        except Exception:
            return None
        morsel = cookie.get(SESSION_COOKIE)
        return morsel.value if morsel else None

    @staticmethod
    def _payload(body: bytes) -> dict[str, object]:
        if len(body) > MAX_BODY_SIZE:
            raise ValueError("Żądanie jest zbyt duże")
        try:
            value = json.loads(body or b"{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Nieprawidłowy JSON") from error
        if not isinstance(value, dict):
            raise ValueError("Payload musi być obiektem JSON")
        return value

    @staticmethod
    def _cookie(token: str, *, secure: bool) -> str:
        flags = "Path=/; HttpOnly; SameSite=Strict"
        if secure:
            flags += "; Secure"
        return f"{SESSION_COOKIE}={token}; {flags}"

    @staticmethod
    def _clear_cookie(*, secure: bool) -> str:
        flags = "Path=/; HttpOnly; SameSite=Strict; Max-Age=0"
        if secure:
            flags += "; Secure"
        return f"{SESSION_COOKIE}=; {flags}"

    def _audit(
        self,
        action: str,
        result: str,
        token: str | None,
        *,
        object_type: str | None = None,
        object_id: str | None = None,
    ) -> None:
        fingerprint = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16] if token else None
        try:
            with connect(self.root) as connection:
                connection.execute(
                    """INSERT INTO private_access_audit(
                           occurred_at,action,object_type,object_id,session_fingerprint,result,metadata_json
                       ) VALUES (?,?,?,?,?,?,'{}')""",
                    (utc_now(), action, object_type, object_id, fingerprint, result),
                )
        except Exception:
            # Access must not leak payloads through an error path. Operational
            # audits separately report an unavailable audit sink.
            return

    def handle(
        self,
        method: str,
        raw_path: str,
        headers: Mapping[str, str],
        body: bytes,
        client: str,
    ) -> PrivateResponse:
        path = unquote(urlsplit(raw_path).path)
        secure = headers.get("X-Forwarded-Proto", "").casefold() == "https"
        token = self._token(headers)
        session = self.manager.authenticate(token)
        try:
            if method == "GET" and path == "/api/private/status":
                return self._json(
                    200,
                    {
                        "enabled": True,
                        "authenticated": session is not None,
                        "strict": bool(session and self.manager.strict_allowed(session)),
                        "csrf": session.csrf if session else None,
                        "expiresIn": self.manager.expires_in(session) if session else 0,
                        "locked": private_lock_path(self.root).is_file(),
                    },
                )
            if method == "POST" and path == "/api/private/unlock":
                payload = self._payload(body)
                password = payload.get("password")
                if not isinstance(password, str):
                    raise PermissionError("Nieprawidłowe dane odblokowania")
                token, session = self.manager.login(password, client)
                self._audit("unlock", "success", token)
                return self._json(
                    200,
                    {"authenticated": True, "csrf": session.csrf, "expiresIn": self.manager.expires_in(session)},
                    (("Set-Cookie", self._cookie(token, secure=secure)),),
                )
            if session is None:
                self._audit("access", "denied", token)
                return self._json(423, {"error": "private-session-locked"})
            if method == "GET" and path == "/api/private/archive-requests":
                try:
                    requests = list_archive_requests(
                        self.root, include_strict_private=self.manager.strict_allowed(session)
                    )
                except LookupError:
                    return self._json(503, {"error": "archive-requests-unavailable"})
                requests["strictExpiresIn"] = max(
                    0, int((session.strict_until or 0) - self.manager.clock())
                )
                self._audit("archive-requests-list", "success", token)
                return self._json(200, requests)
            if method == "POST" and path == "/api/private/strict-unlock":
                self.manager.require_csrf(session, headers.get("X-CSRF-Token"))
                payload = self._payload(body)
                password = payload.get("password")
                if not isinstance(password, str):
                    raise PermissionError("Nieprawidłowe dane odblokowania")
                self.manager.elevate_strict(session, password)
                self._audit("strict-unlock", "success", token)
                return self._json(200, {"strict": True})
            if method == "POST" and path == "/api/private/logout":
                self.manager.require_csrf(session, headers.get("X-CSRF-Token"))
                self.manager.logout(token)
                self._audit("logout", "success", token)
                return self._json(
                    200,
                    {"authenticated": False},
                    (("Set-Cookie", self._clear_cookie(secure=secure)),),
                )
            if method == "POST" and path == "/api/private/lock":
                self.manager.require_csrf(session, headers.get("X-CSRF-Token"))
                self.manager.lock_all()
                self._audit("lock", "success", token)
                return self._json(
                    200,
                    {"authenticated": False, "locked": True},
                    (("Set-Cookie", self._clear_cookie(secure=secure)),),
                )
            identity_prefix = "/api/private/identity-candidate/"
            if method == "GET" and path.startswith(identity_prefix):
                candidate_id = path.removeprefix(identity_prefix).rstrip("/")
                if not candidate_id or "/" in candidate_id:
                    raise LookupError("Nie znaleziono kandydatury identity")
                preview = identity_candidate_preview(self.root, candidate_id)
                self._audit(
                    "identity-preview",
                    "success",
                    token,
                    object_type="identity_candidate",
                    object_id=candidate_id,
                )
                return self._json(200, preview)
            if method == "POST" and path.startswith(identity_prefix) and path.endswith("/resolve"):
                self.manager.require_csrf(session, headers.get("X-CSRF-Token"))
                if not self.manager.strict_allowed(session):
                    raise PermissionError("Rozstrzygnięcie identity wymaga ponownego potwierdzenia hasła")
                candidate_id = (
                    path.removeprefix(identity_prefix).removesuffix("/resolve").rstrip("/")
                )
                if not candidate_id or "/" in candidate_id:
                    raise LookupError("Nie znaleziono kandydatury identity")
                payload = self._payload(body)
                result = apply_identity_decision(
                    self.root,
                    candidate_id,
                    decision=str(payload.get("decision") or ""),
                    actor=str(payload.get("actor") or "local-ui-curator"),
                    rationale=str(payload.get("rationale") or ""),
                    expected_fingerprint=str(payload.get("expectedFingerprint") or ""),
                    command_id=str(payload.get("commandId") or ""),
                    expected_status=str(payload.get("expectedStatus") or "unresolved"),
                    canonical_person_id=(
                        str(payload["canonicalPersonId"])
                        if payload.get("canonicalPersonId")
                        else None
                    ),
                    preferred_name=(
                        str(payload["preferredName"]) if payload.get("preferredName") else None
                    ),
                )
                command_id = str(result["commandId"])
                if self.refresh_presentation is not None:
                    try:
                        self.refresh_presentation()
                    except Exception as error:  # projection failure must not hide a committed decision
                        set_projection_status(
                            self.root, command_id, status="failed", error=str(error)
                        )
                        result["projectionStatus"] = "failed"
                        result["projectionError"] = str(error)
                    else:
                        set_projection_status(self.root, command_id, status="refreshed")
                        result["projectionStatus"] = "refreshed"
                else:
                    set_projection_status(self.root, command_id, status="not_required")
                    result["projectionStatus"] = "not_required"
                self._audit(
                    "identity-resolve",
                    "success",
                    token,
                    object_type="identity_candidate",
                    object_id=candidate_id,
                )
                return self._json(200, result)
            event_record_prefix = "/api/private/event-record-candidate/"
            if method == "GET" and path.startswith(event_record_prefix):
                candidate_id = path.removeprefix(event_record_prefix).rstrip("/")
                if not candidate_id or "/" in candidate_id:
                    raise LookupError("Nie znaleziono kandydatury rekordu zdarzenia")
                preview = event_record_candidate_preview(self.root, candidate_id)
                self._audit(
                    "event-record-preview",
                    "success",
                    token,
                    object_type="event_record_candidate",
                    object_id=candidate_id,
                )
                return self._json(200, preview)
            if method == "POST" and path.startswith(event_record_prefix) and path.endswith("/resolve"):
                self.manager.require_csrf(session, headers.get("X-CSRF-Token"))
                candidate_id = (
                    path.removeprefix(event_record_prefix)
                    .removesuffix("/resolve")
                    .rstrip("/")
                )
                if not candidate_id or "/" in candidate_id:
                    raise LookupError("Nie znaleziono kandydatury rekordu zdarzenia")
                payload = self._payload(body)
                result = apply_event_record_decision(
                    self.root,
                    candidate_id,
                    decision=str(payload.get("decision") or ""),
                    expected_fingerprint=str(payload.get("expectedFingerprint") or ""),
                    actor=str(payload.get("actor") or "local-ui-curator"),
                    rationale=str(payload.get("rationale") or ""),
                    command_id=str(payload.get("commandId") or ""),
                )
                command_id = str(result["commandId"])
                if self.refresh_presentation is not None:
                    try:
                        self.refresh_presentation()
                    except Exception as error:
                        set_projection_status(
                            self.root, command_id, status="failed", error=str(error)
                        )
                        result["projectionStatus"] = "failed"
                        result["projectionError"] = str(error)
                    else:
                        set_projection_status(self.root, command_id, status="refreshed")
                        result["projectionStatus"] = "refreshed"
                else:
                    set_projection_status(self.root, command_id, status="not_required")
                    result["projectionStatus"] = "not_required"
                self._audit(
                    "event-record-resolve",
                    "success",
                    token,
                    object_type="event_record_candidate",
                    object_id=candidate_id,
                )
                return self._json(200, result)
            volume_prefix = "/api/private/volume-index/"
            if method == "POST" and path.startswith(volume_prefix) and path.endswith("/review"):
                from .presentation.volume_indexes import resolve_registered_volume_index

                self.manager.require_csrf(session, headers.get("X-CSRF-Token"))
                index_id = path.removeprefix(volume_prefix).removesuffix("/review").rstrip("/")
                if not index_id or "/" in index_id:
                    raise LookupError("Nie znaleziono indeksu woluminu")
                payload = self._payload(body)
                region_id = payload.get("regionId")
                decision = payload.get("decision")
                if not isinstance(region_id, str) or not isinstance(decision, str):
                    raise ValueError("Review wymaga regionId i decision")
                result = review_volume_region(
                    resolve_registered_volume_index(self.root, index_id),
                    region_id,
                    decision,
                    reviewer=str(payload.get("reviewer") or "local-ui-curator"),
                    corrected_text=(
                        str(payload["correctedText"])
                        if payload.get("correctedText") is not None
                        else None
                    ),
                    note=str(payload["note"]) if payload.get("note") else None,
                )
                if self.refresh_presentation is not None:
                    self.refresh_presentation()
                self._audit(
                    "volume-index-review",
                    "success",
                    token,
                    object_type="volume_index_region",
                    object_id=region_id,
                )
                return self._json(200, result)
            if method == "POST" and path == "/api/private/person":
                self.manager.require_csrf(session, headers.get("X-CSRF-Token"))
                payload = self._payload(body)
                classification = str(payload.get("classification") or "family_private")
                if classification == "strict_private" and not self.manager.strict_allowed(session):
                    raise PermissionError("Wymagany zakres strict_private")
                preferred_name = payload.get("preferredName")
                if not isinstance(preferred_name, str):
                    raise ValueError("Brak preferredName")
                result = create_private_person(
                    self.root,
                    preferred_name,
                    classification=classification,
                    provenance_kind=str(payload.get("provenanceKind") or "family_testimony"),
                    informant=str(payload["informant"]) if payload.get("informant") else None,
                    consent_status=str(payload.get("consentStatus") or "not_asked"),
                    actor="local-ui-private-curator",
                    command_id=str(payload.get("commandId") or ""),
                )
                self._audit("person-create", "success", token, object_type="person", object_id=str(result["id"]))
                return self._json(201, result)
            if method == "POST" and path.startswith("/api/private/person/") and path.endswith("/field"):
                self.manager.require_csrf(session, headers.get("X-CSRF-Token"))
                person_id = path.removeprefix("/api/private/person/").removesuffix("/field").rstrip("/")
                if not person_id or "/" in person_id:
                    raise LookupError("Nie znaleziono prywatnego profilu")
                payload = self._payload(body)
                classification = str(payload.get("classification") or "family_private")
                if classification == "strict_private" and not self.manager.strict_allowed(session):
                    raise PermissionError("Wymagany zakres strict_private")
                field_name = payload.get("fieldName")
                if not isinstance(field_name, str) or "value" not in payload:
                    raise ValueError("Brak pola lub wartości")
                result = set_private_field(
                    self.root,
                    "person",
                    person_id,
                    field_name,
                    payload["value"],
                    classification=classification,
                    provenance_kind=str(payload.get("provenanceKind") or "family_testimony"),
                    informant=str(payload["informant"]) if payload.get("informant") else None,
                    consent_status=str(payload.get("consentStatus") or "not_asked"),
                    review_after=str(payload["reviewAfter"]) if payload.get("reviewAfter") else None,
                    actor="local-ui-private-curator",
                    command_id=str(payload.get("commandId") or ""),
                )
                self._audit("private-field-set", "success", token, object_type="person", object_id=person_id)
                return self._json(200, result)
            if method == "POST" and path.startswith("/api/private/person/") and path.endswith("/note"):
                self.manager.require_csrf(session, headers.get("X-CSRF-Token"))
                person_id = path.removeprefix("/api/private/person/").removesuffix("/note").rstrip("/")
                if not person_id or "/" in person_id:
                    raise LookupError("Nie znaleziono prywatnego profilu")
                payload = self._payload(body)
                classification = str(payload.get("classification") or "family_private")
                if classification == "strict_private" and not self.manager.strict_allowed(session):
                    raise PermissionError("Wymagany zakres strict_private")
                note = payload.get("note")
                informant = payload.get("informant")
                if not isinstance(note, str) or not isinstance(informant, str):
                    raise ValueError("Notatka wymaga treści i informatora")
                result = add_family_note(
                    self.root,
                    "person",
                    person_id,
                    note,
                    informant=informant,
                    classification=classification,
                    consent_status=str(payload.get("consentStatus") or "not_asked"),
                    recorded_at=str(payload["recordedAt"]) if payload.get("recordedAt") else None,
                    review_after=str(payload["reviewAfter"]) if payload.get("reviewAfter") else None,
                    actor="local-ui-private-curator",
                    command_id=str(payload.get("commandId") or ""),
                )
                self._audit("family-note-add", "success", token, object_type="person", object_id=person_id)
                return self._json(201, result)
            if method == "POST" and path.startswith("/api/private/person/") and path.endswith("/occurrence"):
                self.manager.require_csrf(session, headers.get("X-CSRF-Token"))
                person_id = path.removeprefix("/api/private/person/").removesuffix("/occurrence").rstrip("/")
                if not person_id or "/" in person_id:
                    raise LookupError("Nie znaleziono prywatnego profilu")
                payload = self._payload(body)
                occurrence_id = payload.get("occurrenceId")
                rationale = payload.get("rationale")
                if not isinstance(occurrence_id, str) or not isinstance(rationale, str):
                    raise ValueError("Powiązanie wymaga occurrenceId i rationale")
                result = link_private_occurrence(
                    self.root,
                    person_id,
                    occurrence_id,
                    rationale=rationale,
                )
                self._audit("private-occurrence-link", "success", token, object_type="person_occurrence", object_id=occurrence_id)
                return self._json(200, result)
            if method == "POST" and path == "/api/private/relationship":
                self.manager.require_csrf(session, headers.get("X-CSRF-Token"))
                payload = self._payload(body)
                classification = str(payload.get("classification") or "family_private")
                if classification == "strict_private" and not self.manager.strict_allowed(session):
                    raise PermissionError("Wymagany zakres strict_private")
                required = ("subjectPersonId", "objectPersonId", "relationshipType", "rationale")
                if any(not isinstance(payload.get(name), str) for name in required):
                    raise ValueError("Niepełna relacja prywatna")
                result = add_private_relationship(
                    self.root,
                    str(payload["subjectPersonId"]),
                    str(payload["objectPersonId"]),
                    str(payload["relationshipType"]),
                    rationale=str(payload["rationale"]),
                    classification=classification,
                    provenance_kind=str(payload.get("provenanceKind") or "family_testimony"),
                    verification_status=str(payload.get("verificationStatus") or "family_testimony"),
                    date_from=str(payload["dateFrom"]) if payload.get("dateFrom") else None,
                    date_to=str(payload["dateTo"]) if payload.get("dateTo") else None,
                    actor="local-ui-private-curator",
                    command_id=str(payload.get("commandId") or ""),
                )
                self._audit("private-relationship-set", "success", token, object_type="private_relationship", object_id=str(result["id"]))
                return self._json(201, result)
            if method == "GET" and path == "/api/private/persons":
                people = list_private_people(
                    self.root,
                    include_strict=self.manager.strict_allowed(session),
                )
                self._audit("person-list", "success", token)
                return self._json(200, {"people": people})
            if method == "GET" and path == "/api/private/tree-overlay":
                overlay = private_tree_overlay(
                    self.root,
                    include_strict=self.manager.strict_allowed(session),
                )
                self._audit("tree-overlay", "success", token)
                return self._json(200, overlay)
            if method == "GET" and path.startswith("/api/private/person/"):
                person_id = path.removeprefix("/api/private/person/")
                if not person_id or "/" in person_id:
                    raise LookupError("Nie znaleziono prywatnego profilu")
                include_strict = self.manager.strict_allowed(session)
                profile = private_person_profile(
                    self.root, person_id, include_strict=include_strict
                )
                self._audit("person-profile", "success", token, object_type="person", object_id=person_id)
                return self._json(200, profile)
            if method == "GET" and path.startswith("/api/private/attachment/"):
                attachment_id = path.removeprefix("/api/private/attachment/")
                if not attachment_id or "/" in attachment_id:
                    raise LookupError("Nie znaleziono prywatnego załącznika")
                with connect(self.root) as connection:
                    row = connection.execute(
                        "SELECT * FROM private_attachments WHERE id=?", (attachment_id,)
                    ).fetchone()
                if row is None:
                    raise LookupError("Nie znaleziono prywatnego załącznika")
                if row["classification"] == "strict_private" and not self.manager.strict_allowed(session):
                    raise PermissionError("Wymagany zakres strict_private")
                backend = configured_database_backend(self.root)
                if not isinstance(backend, SQLCipherBackend):
                    raise RuntimeError("Prywatny magazyn jest niedostępny")
                key = backend.key_provider.load_key()
                if key is None:
                    raise RuntimeError("Prywatny magazyn jest zablokowany")
                encrypted = self.root / row["encrypted_path"]
                data = EncryptedObjectStore(self.root, key).decrypt_bytes(encrypted)
                self._audit("attachment-view", "success", token, object_type="private_attachment", object_id=attachment_id)
                return PrivateResponse(200, data, row["media_type"])
            return self._json(404, {"error": "not-found"})
        except PermissionError:
            self._audit("access", "denied", token)
            return self._json(403, {"error": "access-denied"})
        except IdentityCurationConflict as error:
            return self._json(409, {"error": "stale-identity-candidate", "detail": str(error)})
        except IdentityCurationError as error:
            return self._json(400, {"error": "invalid-identity-decision", "detail": str(error)})
        except EventRecordCurationConflict as error:
            return self._json(409, {"error": "stale-event-record-candidate", "detail": str(error)})
        except EventRecordCurationError as error:
            return self._json(400, {"error": "invalid-event-record-decision", "detail": str(error)})
        except PrivateDataConflict as error:
            return self._json(409, {"error": "private-command-conflict", "detail": str(error)})
        except LookupError:
            return self._json(404, {"error": "not-found"})
        except (PrivateCryptoError, RuntimeError, ValueError):
            return self._json(400, {"error": "invalid-private-request"})


__all__ = [
    "PrivateApiApplication",
    "PrivateResponse",
    "PrivateSessionManager",
    "SESSION_COOKIE",
]
