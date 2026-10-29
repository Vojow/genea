from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

from .command_envelope import (
    CommandEnvelopeConflict,
    fingerprint,
    record_command,
    replay_command,
)
from .db import connect, initialize
from .private_crypto import EncryptedObjectStore
from .research import require_tables
from .storage import SQLCipherBackend, configured_database_backend
from .workspace import research_root, sha256_file, stable_id, utc_now


PRIVATE_CLASSES = {"family_private", "strict_private"}
PROVENANCE_KINDS = {
    "family_testimony",
    "derived_record",
    "primary_document",
    "imported_unverified",
}
CONSENT_STATUSES = {
    "not_asked",
    "granted",
    "denied",
    "withdrawn",
    "not_applicable",
}
RELATIONSHIP_TYPES = {
    "parent",
    "child",
    "spouse",
    "partner",
    "sibling",
    "guardian",
    "other",
}
OBJECT_TABLES = {
    "person": "persons",
    "event": "events",
    "source_item": "source_items",
    "research_case": "research_cases",
    "research_question": "research_questions",
}


class PrivateDataConflict(ValueError):
    """A private mutation command was reused with different content."""


def _command_id(value: str | None, prefix: str) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else f"{prefix}-{secrets.token_hex(12)}"


def _audit_domain(
    connection,  # type: ignore[no-untyped-def]
    *,
    actor: str,
    action: str,
    object_type: str,
    object_id: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    rationale: str,
) -> int:
    cursor = connection.execute(
        """INSERT INTO audit_log(
               occurred_at,actor,action,object_type,object_id,before_json,after_json,rationale
           ) VALUES (?,?,?,?,?,?,?,?)""",
        (
            utc_now(),
            actor,
            action,
            object_type,
            object_id,
            json.dumps(before, ensure_ascii=False, sort_keys=True) if before else None,
            json.dumps(after, ensure_ascii=False, sort_keys=True) if after else None,
            rationale,
        ),
    )
    return int(cursor.lastrowid)


def _replay_private_command(
    connection,  # type: ignore[no-untyped-def]
    *,
    command_id: str,
    command_type: str,
    object_type: str,
    object_id: str,
    request_fingerprint: str,
) -> dict[str, Any] | None:
    try:
        return replay_command(
            connection,
            command_id=command_id,
            command_type=command_type,
            object_type=object_type,
            object_id=object_id,
            request_fingerprint=request_fingerprint,
        )
    except CommandEnvelopeConflict as error:
        raise PrivateDataConflict(str(error)) from error


def _validate_private_class(classification: str) -> None:
    if classification not in PRIVATE_CLASSES:
        raise ValueError("Pole prywatne wymaga klasy family_private albo strict_private")


def _assert_object(connection, object_type: str, object_id: str) -> None:  # type: ignore[no-untyped-def]
    table = OBJECT_TABLES.get(object_type)
    if table is None:
        raise ValueError("Nieobsługiwany typ obiektu prywatnego")
    if connection.execute(f'SELECT 1 FROM "{table}" WHERE id=?', (object_id,)).fetchone() is None:
        raise LookupError(f"Nie znaleziono obiektu {object_type}:{object_id}")


def set_private_field(
    root: Path,
    object_type: str,
    object_id: str,
    field_name: str,
    value: Any,
    *,
    classification: str = "family_private",
    provenance_kind: str = "family_testimony",
    informant: str | None = None,
    consent_status: str = "not_asked",
    retention_until: str | None = None,
    review_after: str | None = None,
    source_assertion_id: str | None = None,
    actor: str = "private-curator",
    command_id: str | None = None,
) -> dict[str, object]:
    _validate_private_class(classification)
    if provenance_kind not in PROVENANCE_KINDS:
        raise ValueError("Nieprawidłowy rodzaj pochodzenia prywatnego pola")
    if consent_status not in CONSENT_STATUSES:
        raise ValueError("Nieprawidłowy status zgody")
    if not field_name.strip():
        raise ValueError("Nazwa pola prywatnego nie może być pusta")
    try:
        value_json = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as error:
        raise ValueError("Wartość prywatnego pola musi być poprawnym JSON-em") from error
    initialize(root)
    command_id = _command_id(command_id, "private-field")
    now = utc_now()
    item = {
        "id": stable_id("private_field", object_type, object_id, field_name.strip()),
        "object_type": object_type,
        "object_id": object_id,
        "field_name": field_name.strip(),
        "value_json": value_json,
        "classification": classification,
        "provenance_kind": provenance_kind,
        "informant": informant,
        "consent_status": consent_status,
        "retention_until": retention_until,
        "review_after": review_after,
        "source_assertion_id": source_assertion_id,
        "created_at": now,
        "updated_at": now,
    }
    with connect(root) as connection:
        require_tables(connection, {"private_fields", "audit_log", "curation_commands"})
        connection.execute("BEGIN IMMEDIATE")
        _assert_object(connection, object_type, object_id)
        request_fingerprint = fingerprint(
            {
                "objectType": object_type,
                "objectId": object_id,
                "fieldName": field_name.strip(),
                "value": value,
                "classification": classification,
                "provenanceKind": provenance_kind,
                "informant": informant,
                "consentStatus": consent_status,
                "retentionUntil": retention_until,
                "reviewAfter": review_after,
                "sourceAssertionId": source_assertion_id,
                "actor": actor,
            }
        )
        replay = _replay_private_command(
            connection,
            command_id=command_id,
            command_type="private_field_set",
            object_type="private_field",
            object_id=str(item["id"]),
            request_fingerprint=request_fingerprint,
        )
        if replay is not None:
            return replay
        existing = connection.execute(
            """SELECT * FROM private_fields
               WHERE object_type=? AND object_id=? AND field_name=?""",
            (object_type, object_id, field_name.strip()),
        ).fetchone()
        connection.execute(
            """INSERT INTO private_fields(
                   id,object_type,object_id,field_name,value_json,classification,
                   provenance_kind,informant,consent_status,retention_until,
                   review_after,source_assertion_id,created_at,updated_at
               ) VALUES (
                   :id,:object_type,:object_id,:field_name,:value_json,:classification,
                   :provenance_kind,:informant,:consent_status,:retention_until,
                   :review_after,:source_assertion_id,:created_at,:updated_at
               ) ON CONFLICT(object_type,object_id,field_name) DO UPDATE SET
                   value_json=excluded.value_json,
                   classification=excluded.classification,
                   provenance_kind=excluded.provenance_kind,
                   informant=excluded.informant,
                   consent_status=excluded.consent_status,
                   retention_until=excluded.retention_until,
                   review_after=excluded.review_after,
                   source_assertion_id=excluded.source_assertion_id,
                   updated_at=excluded.updated_at""",
            item,
        )
        redacted_before = (
            {
                key: existing[key]
                for key in existing.keys()
                if key != "value_json"
            }
            if existing
            else None
        )
        if redacted_before is not None:
            redacted_before["valueFingerprint"] = fingerprint(existing["value_json"])
        redacted_after = {
            key: item[key]
            for key in item
            if key != "value_json"
        }
        redacted_after["valueFingerprint"] = fingerprint(value_json)
        result = {
            **redacted_after,
            "value_json": "[REDACTED]",
            "commandId": command_id,
            "projectionStatus": "not_required",
        }
        record_command(
            connection,
            command_id=command_id,
            command_type="private_field_set",
            object_type="private_field",
            object_id=str(item["id"]),
            input_fingerprint=request_fingerprint,
            request_fingerprint=request_fingerprint,
            actor=actor,
            rationale="Kontrolowany zapis prywatnego pola; wartość usunięta z audytu.",
            result=result,
            projection_status="not_required",
        )
        _audit_domain(
            connection,
            actor=actor,
            action="set_private_field",
            object_type="private_field",
            object_id=str(item["id"]),
            before=redacted_before,
            after=redacted_after,
            rationale="Kontrolowany zapis prywatnego pola; wartość usunięta z audytu.",
        )
    return {**result, "value": value, "alreadyApplied": False}


def create_private_person(
    root: Path,
    preferred_name: str,
    *,
    classification: str = "family_private",
    provenance_kind: str = "family_testimony",
    informant: str | None = None,
    consent_status: str = "not_asked",
    actor: str = "private-curator",
    command_id: str | None = None,
) -> dict[str, object]:
    _validate_private_class(classification)
    if not preferred_name.strip():
        raise ValueError("Imię osoby nie może być puste")
    initialize(root)
    command_id = _command_id(command_id, "private-person")
    person_id = f"person_private_{stable_id('private_person_command', command_id).split('_')[-1]}"
    now = utc_now()
    request_fingerprint = fingerprint(
        {
            "preferredName": preferred_name.strip(),
            "classification": classification,
            "provenanceKind": provenance_kind,
            "informant": informant,
            "consentStatus": consent_status,
            "actor": actor,
        }
    )
    with connect(root) as connection:
        require_tables(
            connection,
            {"persons", "privacy_labels", "private_fields", "audit_log", "curation_commands"},
        )
        connection.execute("BEGIN IMMEDIATE")
        replay = _replay_private_command(
            connection,
            command_id=command_id,
            command_type="private_person_create",
            object_type="person",
            object_id=person_id,
            request_fingerprint=request_fingerprint,
        )
        if replay is not None:
            return replay
        connection.execute(
            "INSERT INTO persons(id,preferred_name,status,created_at,notes) VALUES (?,NULL,'private',?,NULL)",
            (person_id, now),
        )
        connection.execute(
            """INSERT INTO privacy_labels(
                   id,object_type,object_id,classification,rationale,created_at,updated_at
               ) VALUES (?, 'person', ?, ?, ?, ?, ?)""",
            (
                stable_id("privacy", "person", person_id),
                person_id,
                classification,
                "Pełna osoba prywatna utworzona kontrolowanym formularzem.",
                now,
                now,
            ),
        )
        field_id = stable_id("private_field", "person", person_id, "preferredName")
        connection.execute(
            """INSERT INTO private_fields(
                   id,object_type,object_id,field_name,value_json,classification,
                   provenance_kind,informant,consent_status,created_at,updated_at
               ) VALUES (?,'person',?,'preferredName',?,?,?,?,?,?,?)""",
            (
                field_id,
                person_id,
                json.dumps(preferred_name.strip(), ensure_ascii=False),
                classification,
                provenance_kind,
                informant,
                consent_status,
                now,
                now,
            ),
        )
        result = {
            "id": person_id,
            "preferredName": preferred_name.strip(),
            "classification": classification,
            "commandId": command_id,
            "projectionStatus": "not_required",
        }
        record_command(
            connection,
            command_id=command_id,
            command_type="private_person_create",
            object_type="person",
            object_id=person_id,
            input_fingerprint=request_fingerprint,
            request_fingerprint=request_fingerprint,
            actor=actor,
            rationale="Utworzenie prywatnej osoby wraz z etykietą i chronioną nazwą.",
            result=result,
            projection_status="not_required",
        )
        _audit_domain(
            connection,
            actor=actor,
            action="private_person_create",
            object_type="person",
            object_id=person_id,
            before=None,
            after={"classification": classification, "preferredName": "[REDACTED]"},
            rationale="Atomowe utworzenie osoby, etykiety prywatności i pola preferredName.",
        )
    return {**result, "alreadyApplied": False}


def add_family_note(
    root: Path,
    subject_type: str,
    subject_id: str,
    note_text: str,
    *,
    informant: str,
    classification: str = "family_private",
    consent_status: str = "not_asked",
    recorded_at: str | None = None,
    review_after: str | None = None,
    actor: str = "private-curator",
    command_id: str | None = None,
) -> dict[str, object]:
    _validate_private_class(classification)
    if consent_status not in CONSENT_STATUSES:
        raise ValueError("Nieprawidłowy status zgody")
    if not note_text.strip() or not informant.strip():
        raise ValueError("Notatka rodzinna wymaga treści i informatora")
    initialize(root)
    command_id = _command_id(command_id, "family-note")
    now = utc_now()
    item = {
        "id": stable_id("family_note_command", command_id),
        "subject_type": subject_type,
        "subject_id": subject_id,
        "note_text": note_text.strip(),
        "classification": classification,
        "informant": informant.strip(),
        "consent_status": consent_status,
        "recorded_at": recorded_at or now,
        "review_after": review_after,
        "created_at": now,
    }
    with connect(root) as connection:
        require_tables(connection, {"family_notes", "audit_log", "curation_commands"})
        connection.execute("BEGIN IMMEDIATE")
        _assert_object(connection, subject_type, subject_id)
        request_fingerprint = fingerprint(
            {
                "subjectType": subject_type,
                "subjectId": subject_id,
                "noteText": note_text.strip(),
                "classification": classification,
                "informant": informant.strip(),
                "consentStatus": consent_status,
                "recordedAt": recorded_at,
                "reviewAfter": review_after,
                "actor": actor,
            }
        )
        replay = _replay_private_command(
            connection,
            command_id=command_id,
            command_type="family_note_add",
            object_type="family_note",
            object_id=str(item["id"]),
            request_fingerprint=request_fingerprint,
        )
        if replay is not None:
            return replay
        connection.execute(
            """INSERT INTO family_notes(
                   id,subject_type,subject_id,note_text,classification,informant,
                   consent_status,recorded_at,review_after,created_at
               ) VALUES (
                   :id,:subject_type,:subject_id,:note_text,:classification,:informant,
                   :consent_status,:recorded_at,:review_after,:created_at
               )""",
            item,
        )
        result = {
            **item,
            "note_text": "[REDACTED]",
            "commandId": command_id,
            "projectionStatus": "not_required",
        }
        record_command(
            connection,
            command_id=command_id,
            command_type="family_note_add",
            object_type="family_note",
            object_id=str(item["id"]),
            input_fingerprint=request_fingerprint,
            request_fingerprint=request_fingerprint,
            actor=actor,
            rationale="Dodanie rodzinnego świadectwa z zachowaniem prywatności.",
            result=result,
            projection_status="not_required",
        )
        _audit_domain(
            connection,
            actor=actor,
            action="family_note_add",
            object_type="family_note",
            object_id=str(item["id"]),
            before=None,
            after={
                "subject_type": subject_type,
                "subject_id": subject_id,
                "classification": classification,
                "informant": informant.strip(),
                "noteFingerprint": fingerprint(note_text.strip()),
            },
            rationale="Treść notatki usunięta z domenowego audytu.",
        )
    return {**result, "alreadyApplied": False}


def add_private_relationship(
    root: Path,
    subject_person_id: str,
    object_person_id: str,
    relationship_type: str,
    *,
    rationale: str,
    classification: str = "family_private",
    provenance_kind: str = "family_testimony",
    verification_status: str = "family_testimony",
    date_from: str | None = None,
    date_to: str | None = None,
    actor: str = "private-curator",
    command_id: str | None = None,
) -> dict[str, object]:
    _validate_private_class(classification)
    if relationship_type not in RELATIONSHIP_TYPES:
        raise ValueError("Nieprawidłowy typ relacji prywatnej")
    if provenance_kind not in PROVENANCE_KINDS:
        raise ValueError("Nieprawidłowy rodzaj pochodzenia relacji")
    if subject_person_id == object_person_id:
        raise ValueError("Osoba nie może mieć relacji sama ze sobą")
    if not rationale.strip():
        raise ValueError("Relacja prywatna wymaga uzasadnienia")
    initialize(root)
    command_id = _command_id(command_id, "private-relationship")
    now = utc_now()
    item = {
        "id": stable_id(
            "private_relationship",
            subject_person_id,
            object_person_id,
            relationship_type,
        ),
        "subject_person_id": subject_person_id,
        "object_person_id": object_person_id,
        "relationship_type": relationship_type,
        "date_from": date_from,
        "date_to": date_to,
        "classification": classification,
        "provenance_kind": provenance_kind,
        "verification_status": verification_status,
        "rationale": rationale.strip(),
        "created_at": now,
        "updated_at": now,
    }
    with connect(root) as connection:
        require_tables(
            connection,
            {
                "private_relationships",
                "private_relationship_revisions",
                "persons",
                "audit_log",
                "curation_commands",
            },
        )
        connection.execute("BEGIN IMMEDIATE")
        _assert_object(connection, "person", subject_person_id)
        _assert_object(connection, "person", object_person_id)
        request_fingerprint = fingerprint(
            {
                "subjectPersonId": subject_person_id,
                "objectPersonId": object_person_id,
                "relationshipType": relationship_type,
                "rationale": rationale.strip(),
                "classification": classification,
                "provenanceKind": provenance_kind,
                "verificationStatus": verification_status,
                "dateFrom": date_from,
                "dateTo": date_to,
                "actor": actor,
            }
        )
        replay = _replay_private_command(
            connection,
            command_id=command_id,
            command_type="private_relationship_set",
            object_type="private_relationship",
            object_id=str(item["id"]),
            request_fingerprint=request_fingerprint,
        )
        if replay is not None:
            return replay
        existing = connection.execute(
            "SELECT * FROM private_relationships WHERE id=?", (item["id"],)
        ).fetchone()
        if existing is None:
            connection.execute(
                """INSERT INTO private_relationships(
                       id,subject_person_id,object_person_id,relationship_type,date_from,date_to,
                       classification,provenance_kind,verification_status,rationale,created_at,updated_at
                   ) VALUES (
                       :id,:subject_person_id,:object_person_id,:relationship_type,:date_from,:date_to,
                       :classification,:provenance_kind,:verification_status,:rationale,:created_at,:updated_at
                   )""",
                item,
            )
        else:
            item["created_at"] = existing["created_at"]
            connection.execute(
                """UPDATE private_relationships
                   SET date_from=:date_from,date_to=:date_to,
                       classification=:classification,provenance_kind=:provenance_kind,
                       verification_status=:verification_status,rationale=:rationale,
                       updated_at=:updated_at WHERE id=:id""",
                item,
            )
        latest = connection.execute(
            """SELECT id,revision FROM private_relationship_revisions
               WHERE relationship_id=? ORDER BY revision DESC LIMIT 1""",
            (item["id"],),
        ).fetchone()
        revision = int(latest["revision"]) + 1 if latest else 1
        revision_id = stable_id(
            "private_relationship_revision", str(item["id"]), str(revision), command_id
        )
        result = {
            **item,
            "revision": revision,
            "revisionId": revision_id,
            "commandId": command_id,
            "projectionStatus": "not_required",
        }
        record_command(
            connection,
            command_id=command_id,
            command_type="private_relationship_set",
            object_type="private_relationship",
            object_id=str(item["id"]),
            input_fingerprint=request_fingerprint,
            request_fingerprint=request_fingerprint,
            actor=actor,
            rationale=rationale.strip(),
            result=result,
            projection_status="not_required",
        )
        connection.execute(
            """INSERT INTO private_relationship_revisions(
                   id,relationship_id,revision,supersedes_revision_id,
                   subject_person_id,object_person_id,relationship_type,date_from,date_to,
                   classification,provenance_kind,verification_status,rationale,
                   actor,command_id,created_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                revision_id,
                item["id"],
                revision,
                latest["id"] if latest else None,
                subject_person_id,
                object_person_id,
                relationship_type,
                date_from,
                date_to,
                classification,
                provenance_kind,
                verification_status,
                rationale.strip(),
                actor,
                command_id,
                now,
            ),
        )
        _audit_domain(
            connection,
            actor=actor,
            action="private_relationship_set",
            object_type="private_relationship",
            object_id=str(item["id"]),
            before={
                "revision": revision - 1 if existing else None,
                "verification_status": existing["verification_status"] if existing else None,
            }
            if existing
            else None,
            after={
                "revision": revision,
                "verification_status": verification_status,
                "classification": classification,
            },
            rationale="Relacja prywatna zapisana jako nowa rewizja append-only.",
        )
    return {**result, "alreadyApplied": False}


def link_private_occurrence(
    root: Path,
    person_id: str,
    occurrence_id: str,
    *,
    rationale: str,
) -> dict[str, object]:
    """Link a reviewed document occurrence without inferring any relationship."""

    if not rationale.strip():
        raise ValueError("Powiązanie wystąpienia wymaga uzasadnienia")
    initialize(root)
    now = utc_now()
    with connect(root) as connection:
        require_tables(connection, {"persons", "person_occurrences", "privacy_labels", "audit_log"})
        _assert_object(connection, "person", person_id)
        occurrence = connection.execute(
            "SELECT * FROM person_occurrences WHERE id=?", (occurrence_id,)
        ).fetchone()
        if occurrence is None:
            raise LookupError(f"Nie znaleziono wystąpienia {occurrence_id}")
        if occurrence["person_id"] not in {None, person_id}:
            raise ValueError("Wystąpienie jest już przypisane do innej osoby")
        label = connection.execute(
            "SELECT classification FROM privacy_labels WHERE object_type='person' AND object_id=?",
            (person_id,),
        ).fetchone()
        classification = label[0] if label and label[0] in PRIVATE_CLASSES else "family_private"
        connection.execute(
            "UPDATE person_occurrences SET person_id=? WHERE id=?",
            (person_id, occurrence_id),
        )
        connection.execute(
            """INSERT INTO privacy_labels(
                   id,object_type,object_id,classification,rationale,created_at,updated_at
               ) VALUES (?, 'person_occurrence', ?, ?, ?, ?, ?)
               ON CONFLICT(object_type,object_id) DO UPDATE SET
                   classification=excluded.classification,
                   rationale=excluded.rationale,
                   updated_at=excluded.updated_at""",
            (
                stable_id("privacy", "person_occurrence", occurrence_id),
                occurrence_id,
                classification,
                rationale.strip(),
                now,
                now,
            ),
        )
        connection.execute(
            """INSERT INTO audit_log(
                   occurred_at,actor,action,object_type,object_id,before_json,after_json,rationale
               ) VALUES (?,?,'link_private_occurrence','person_occurrence',?,?,?,?)""",
            (
                now,
                "private-curator",
                occurrence_id,
                json.dumps({"person_id": occurrence["person_id"]}),
                json.dumps({"person_id": person_id, "classification": classification}),
                rationale.strip(),
            ),
        )
    return {
        "personId": person_id,
        "occurrenceId": occurrence_id,
        "classification": classification,
    }


def add_private_attachment(
    root: Path,
    source: Path,
    object_type: str,
    object_id: str,
    *,
    media_type: str = "application/octet-stream",
    classification: str = "family_private",
) -> dict[str, object]:
    root = Path(root).resolve()
    _validate_private_class(classification)
    initialize(root)
    backend = configured_database_backend(root)
    if not isinstance(backend, SQLCipherBackend):
        raise RuntimeError("Prywatne załączniki wymagają aktywnej zaszyfrowanej bazy")
    key = backend.key_provider.load_key()
    if key is None:
        raise RuntimeError("Prywatny magazyn jest zablokowany")
    source = Path(source).expanduser().resolve()
    attachment_id = stable_id(
        "private_attachment", object_type, object_id, sha256_file(source)
    )
    store = EncryptedObjectStore(root, key)
    with connect(root) as connection:
        require_tables(connection, {"private_attachments"})
        _assert_object(connection, object_type, object_id)
    encrypted = store.encrypt_file(
        source,
        attachment_id,
        media_type=media_type,
    )
    item = {
        "id": attachment_id,
        "object_type": object_type,
        "object_id": object_id,
        "encrypted_path": encrypted.path.relative_to(root).as_posix(),
        "media_type": encrypted.media_type,
        "original_size": encrypted.size,
        "plaintext_sha256": encrypted.plaintext_sha256,
        "ciphertext_sha256": encrypted.ciphertext_sha256,
        "classification": classification,
        "source_name_hint": None,
        "created_at": utc_now(),
        "metadata_json": "{}",
    }
    try:
        with connect(root) as connection:
            connection.execute(
                """INSERT INTO private_attachments(
                       id,object_type,object_id,encrypted_path,media_type,original_size,
                       plaintext_sha256,ciphertext_sha256,classification,source_name_hint,
                       created_at,metadata_json
                   ) VALUES (
                       :id,:object_type,:object_id,:encrypted_path,:media_type,:original_size,
                       :plaintext_sha256,:ciphertext_sha256,:classification,:source_name_hint,
                       :created_at,:metadata_json
                   )""",
                item,
            )
    except Exception:
        encrypted.path.unlink(missing_ok=True)
        raise
    return item


def private_person_profile(
    root: Path,
    person_id: str,
    *,
    include_strict: bool = False,
) -> dict[str, object]:
    allowed = {"family_private"}
    if include_strict:
        allowed.add("strict_private")
    placeholders = ",".join("?" for _ in allowed)
    with connect(root) as connection:
        require_tables(
            connection,
            {"persons", "private_fields", "family_notes", "private_relationships", "private_attachments"},
        )
        _assert_object(connection, "person", person_id)
        fields = [
            {
                **dict(row),
                "value": json.loads(row["value_json"]),
                "value_json": None,
            }
            for row in connection.execute(
                f"""SELECT * FROM private_fields
                    WHERE object_type='person' AND object_id=?
                      AND classification IN ({placeholders}) ORDER BY field_name""",
                (person_id, *sorted(allowed)),
            )
        ]
        notes = [
            dict(row)
            for row in connection.execute(
                f"""SELECT * FROM family_notes
                    WHERE subject_type='person' AND subject_id=?
                      AND classification IN ({placeholders}) ORDER BY recorded_at""",
                (person_id, *sorted(allowed)),
            )
        ]
        relationships = [
            dict(row)
            for row in connection.execute(
                f"""SELECT * FROM private_relationships
                    WHERE (subject_person_id=? OR object_person_id=?)
                      AND classification IN ({placeholders}) ORDER BY relationship_type,id""",
                (person_id, person_id, *sorted(allowed)),
            )
        ]
        attachments = [
            dict(row)
            for row in connection.execute(
                f"""SELECT id,object_type,object_id,media_type,original_size,
                           classification,created_at
                    FROM private_attachments
                    WHERE object_type='person' AND object_id=?
                      AND classification IN ({placeholders}) ORDER BY created_at""",
                (person_id, *sorted(allowed)),
            )
        ]
    return {
        "id": person_id,
        "fields": fields,
        "notes": notes,
        "relationships": relationships,
        "attachments": attachments,
        "strictIncluded": include_strict,
    }


def list_private_people(
    root: Path,
    *,
    include_strict: bool = False,
) -> list[dict[str, object]]:
    allowed = {"family_private"}
    if include_strict:
        allowed.add("strict_private")
    placeholders = ",".join("?" for _ in allowed)
    with connect(root) as connection:
        require_tables(connection, {"persons", "private_fields", "privacy_labels"})
        rows = connection.execute(
            f"""SELECT p.id, pf.value_json, pl.classification
                  FROM persons p
                  JOIN privacy_labels pl
                    ON pl.object_type='person' AND pl.object_id=p.id
                  LEFT JOIN private_fields pf
                    ON pf.object_type='person' AND pf.object_id=p.id
                   AND pf.field_name='preferredName'
                 WHERE p.status='private'
                   AND pl.classification IN ({placeholders})
                 ORDER BY p.created_at,p.id""",
            tuple(sorted(allowed)),
        ).fetchall()
    result: list[dict[str, object]] = []
    for row in rows:
        preferred_name = json.loads(row["value_json"]) if row["value_json"] else row["id"]
        result.append(
            {
                "id": row["id"],
                "preferredName": preferred_name,
                "classification": row["classification"],
            }
        )
    return result


def link_private_tree_person(
    root: Path,
    person_id: str,
    tree_person_id: str,
) -> dict[str, object]:
    tree_path = research_root(root) / "presentation" / "family-tree.json"
    if not tree_path.is_file():
        raise FileNotFoundError("Brak kuratorowanego drzewa prezentacyjnego")
    tree = json.loads(tree_path.read_text(encoding="utf-8"))
    known_ids = {
        str(item.get("id"))
        for item in tree.get("people", [])
        if isinstance(item, dict) and item.get("id")
    }
    if tree_person_id not in known_ids:
        raise ValueError("Nieznany identyfikator karty drzewa")
    encoded_id = json.dumps(tree_person_id, ensure_ascii=False, sort_keys=True)
    with connect(root) as connection:
        require_tables(connection, {"persons", "private_fields"})
        _assert_object(connection, "person", person_id)
        conflict = connection.execute(
            """SELECT object_id FROM private_fields
                 WHERE object_type='person'
                   AND field_name='presentation.treePersonId'
                   AND value_json=? AND object_id<>?""",
            (encoded_id, person_id),
        ).fetchone()
        if conflict:
            raise ValueError("Karta drzewa jest już przypisana do innej osoby prywatnej")
    return set_private_field(
        root,
        "person",
        person_id,
        "presentation.treePersonId",
        tree_person_id,
        classification="family_private",
        provenance_kind="derived_record",
        informant="kuracja lokalna",
        consent_status="not_applicable",
    )


def private_tree_overlay(
    root: Path,
    *,
    include_strict: bool = False,
) -> dict[str, object]:
    allowed = {"family_private"}
    if include_strict:
        allowed.add("strict_private")
    placeholders = ",".join("?" for _ in allowed)
    field_names = {
        "presentation.treePersonId",
        "preferredName",
        "birthDate",
        "deathDate",
        "sex",
    }
    field_placeholders = ",".join("?" for _ in field_names)
    with connect(root) as connection:
        require_tables(connection, {"persons", "privacy_labels", "private_fields"})
        total = connection.execute(
            f"""SELECT COUNT(*)
                  FROM persons p
                  JOIN privacy_labels pl
                    ON pl.object_type='person' AND pl.object_id=p.id
                 WHERE p.status='private'
                   AND pl.classification IN ({placeholders})""",
            tuple(sorted(allowed)),
        ).fetchone()[0]
        rows = connection.execute(
            f"""SELECT p.id,pf.field_name,pf.value_json,pf.provenance_kind
                  FROM persons p
                  JOIN privacy_labels pl
                    ON pl.object_type='person' AND pl.object_id=p.id
                  JOIN private_fields pf
                    ON pf.object_type='person' AND pf.object_id=p.id
                 WHERE p.status='private'
                   AND pl.classification IN ({placeholders})
                   AND pf.classification IN ({placeholders})
                   AND pf.field_name IN ({field_placeholders})
                 ORDER BY p.id,pf.field_name""",
            (
                *sorted(allowed),
                *sorted(allowed),
                *sorted(field_names),
            ),
        ).fetchall()
    grouped: dict[str, dict[str, tuple[Any, str]]] = {}
    for row in rows:
        grouped.setdefault(row["id"], {})[row["field_name"]] = (
            json.loads(row["value_json"]),
            row["provenance_kind"],
        )
    people: list[dict[str, object]] = []
    used_tree_ids: set[str] = set()
    for fields in grouped.values():
        mapping = fields.get("presentation.treePersonId")
        if not mapping or not isinstance(mapping[0], str) or mapping[0] in used_tree_ids:
            continue
        preferred = fields.get("preferredName")
        if not preferred or not isinstance(preferred[0], str):
            continue
        tree_person_id = mapping[0]
        used_tree_ids.add(tree_person_id)
        birth = fields.get("birthDate", (None, ""))[0]
        death = fields.get("deathDate", (None, ""))[0]
        summary_parts = []
        if birth:
            summary_parts.append(f"ur. {birth}")
        if death:
            summary_parts.append(f"zm. {death}")
        people.append(
            {
                "treePersonId": tree_person_id,
                "name": preferred[0],
                "summary": " · ".join(summary_parts) or "odblokowane dane prywatne",
                "sex": fields.get("sex", (None, ""))[0],
                "privacy": "private_revealed",
                "evidenceStatus": (
                    "family_known"
                    if preferred[1] == "family_testimony"
                    else "private_unverified"
                ),
            }
        )
    people.sort(key=lambda item: str(item["treePersonId"]))
    return {
        "people": people,
        "mappedCount": len(people),
        "unmappedCount": max(0, total - len(people)),
        "totalPrivatePeople": total,
    }


__all__ = [
    "add_family_note",
    "add_private_attachment",
    "add_private_relationship",
    "create_private_person",
    "link_private_occurrence",
    "list_private_people",
    "link_private_tree_person",
    "private_person_profile",
    "private_tree_overlay",
    "set_private_field",
]
