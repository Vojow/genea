"""Fail-closed, read-only summary of explicitly recorded family recognition.

The private testimony is validated in place. Only a fixed typed summary leaves
this module; recognition never supplies an act identity or relationship decision.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from ..command_envelope import fingerprint
from ..workspace import stable_id
from .privacy import PrivacyContext


_HISTORICAL = {"deceased", "public_historical"}
_REASSESSMENTS = Path("research/records/reassessments")
_MAX_DECLARATION_BYTES = 128 * 1024
_SUMMARY = {
    "status": "family_known",
    "provenanceKind": "family_testimony",
    "basis": "user_reported_family_conversations",
    "confidenceDeclared": "100%",
    "confidenceSemantics": "user_declared",
    "recognitionScope": "family_person",
}


def sanitize_family_knowledge(value: Any) -> dict[str, str] | None:
    """Accept only the seven public v1 fields when copying a projected summary.

    This checks display shape only. SQL/file backing validation belongs to
    ``project_family_knowledge``; callers must not use this as proof validation.
    """
    if not isinstance(value, dict) or set(value) != set(_SUMMARY) | {"provenanceRef"}:
        return None
    if any(type(value[key]) is not str or value[key] != expected
           for key, expected in _SUMMARY.items()):
        return None
    reference = value["provenanceRef"]
    if not isinstance(reference, str) or re.fullmatch(r"family_knowledge_[0-9a-f]{64}", reference) is None:
        return None
    return {**_SUMMARY, "provenanceRef": reference}


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def _object_json(value: str | bytes) -> dict[str, Any]:
    def invalid_constant(_value: str) -> None:
        raise ValueError("Non-finite JSON value")

    result = json.loads(
        value, object_pairs_hook=_unique_object, parse_constant=invalid_constant
    )
    if not isinstance(result, dict):
        raise ValueError("Expected a structured object")
    return result


def _rows(connection: sqlite3.Connection, sql: str, args: tuple = ()) -> list[dict]:
    cursor = connection.execute(sql, args)
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _one(connection: sqlite3.Connection, sql: str, args: tuple) -> dict:
    rows = _rows(connection, sql, args)
    if len(rows) != 1:
        raise ValueError("Missing or ambiguous backing")
    return rows[0]


def _declaration(root: Path, metadata: dict) -> dict:
    reference = metadata["declarationRef"]
    expected_sha = metadata["declarationSHA256"]
    if not isinstance(reference, str) or "\\" in reference:
        raise ValueError("Invalid declaration reference")
    relative = Path(reference)
    if (
        relative.is_absolute()
        or relative.as_posix() != reference
        or ".." in relative.parts
        or relative.parent != _REASSESSMENTS
        or relative.suffix != ".json"
        or not isinstance(expected_sha, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_sha) is None
    ):
        raise ValueError("Declaration outside the bounded artifact directory")
    directory = (root / _REASSESSMENTS).resolve(strict=True)
    path = (root / relative).resolve(strict=True)
    if not directory.is_relative_to(root) or not path.is_relative_to(directory):
        raise ValueError("Declaration path escape")
    with path.open("rb") as handle:
        data = handle.read(_MAX_DECLARATION_BYTES + 1)
    if len(data) > _MAX_DECLARATION_BYTES or hashlib.sha256(data).hexdigest() != expected_sha:
        raise ValueError("Declaration changed")
    return _object_json(data)


def _validate_command(connection: sqlite3.Connection, note: dict) -> tuple[dict, dict]:
    command = _one(
        connection,
        "SELECT * FROM curation_commands WHERE object_type='family_note' AND object_id=?",
        (note["id"],),
    )
    if (
        command["command_type"] != "family_note_add"
        or command["projection_status"] != "not_required"
        or command["projection_error"] is not None
        or not command["actor"]
        or stable_id("family_note_command", command["command_id"]) != note["id"]
    ):
        raise ValueError("Invalid family note command")
    request = {
        "subjectType": note["subject_type"],
        "subjectId": note["subject_id"],
        "noteText": note["note_text"],
        "classification": note["classification"],
        "informant": note["informant"],
        "consentStatus": note["consent_status"],
        "recordedAt": note["recorded_at"],
        "reviewAfter": note["review_after"],
        "actor": command["actor"],
    }
    expected = fingerprint(request)
    if command["request_fingerprint"] != expected or command["input_fingerprint"] != expected:
        raise ValueError("Family note request changed")
    result = {
        **note,
        "note_text": "[REDACTED]",
        "commandId": command["command_id"],
        "projectionStatus": "not_required",
    }
    if _object_json(command["result_json"]) != result:
        raise ValueError("Family note command result changed")
    audit = _one(
        connection,
        """SELECT * FROM audit_log WHERE action='family_note_add'
           AND object_type='family_note' AND object_id=?""",
        (note["id"],),
    )
    if (
        audit["actor"] != command["actor"]
        or audit["before_json"] is not None
        or _object_json(audit["after_json"]) != {
            "subject_type": note["subject_type"],
            "subject_id": note["subject_id"],
            "classification": note["classification"],
            "informant": note["informant"],
            "noteFingerprint": fingerprint(note["note_text"]),
        }
    ):
        raise ValueError("Family note audit changed")
    return command, audit


def _project_one(
    connection: sqlite3.Connection, root: Path, privacy: PrivacyContext, source: dict
) -> tuple[str, dict[str, str]]:
    source_metadata = _object_json(source["metadata_json"])
    metadata = source_metadata["familyKnowledgeV1"]
    if not isinstance(metadata, dict):
        raise ValueError("Missing typed recognition")
    required = {
        "version": 1,
        "status": "family_known",
        "provenanceKind": "family_testimony",
        "knownFamilyPerson": True,
        "userDeclaredCertainty": "100%",
        "basis": "Rozmowy z teściami — relacja użytkownika",
        "actIdentityDecision": "separate_unresolved",
        "B43ChildIdentityStatus": "unresolved",
        "parentageDecision": "not_included",
        "privacy": "family_private",
        "noSourceOrPersonStatusPromotion": True,
        "projectionStatus": "pending_family_knowledge_projection",
    }
    if any(type(metadata.get(key)) is not type(value) or metadata[key] != value
           for key, value in required.items()):
        raise ValueError("Unsupported recognition semantics")
    profile_id, person_id = metadata["profileId"], metadata["personId"]
    tree_id = source_metadata["treeId"]
    if not all(isinstance(value, str) and value.strip() == value and value
               for value in (profile_id, person_id, tree_id)):
        raise ValueError("Invalid typed target")
    if (
        source_metadata.get("service") != "myheritage"
        or source_metadata.get("personId") != profile_id
        or source_metadata.get("externalTreeLead") is not True
        or source_metadata.get("noAutoMerge") is not True
        or source_metadata.get("noConfirmedPromotion") is not True
        or source["id"] != stable_id("item_mh_profile", tree_id, profile_id)
        or person_id != stable_id("person_mh_candidate", tree_id, profile_id)
        or source["verification_status"] in {"retracted", "superseded"}
        or privacy.labels.get(("person", person_id)) not in _HISTORICAL
        or privacy.labels.get(("source_item", source["id"])) not in _HISTORICAL | {"restricted"}
    ):
        raise ValueError("Target is not an eligible historical external profile")
    person = _one(connection, "SELECT * FROM persons WHERE id=?", (person_id,))
    if person["status"] in {"retracted", "superseded"} or privacy.blocked_reference(person_id):
        raise ValueError("Person is unavailable")
    for kind, object_id in (("person", person_id), ("source_item", source["id"])):
        live_label = _one(
            connection,
            "SELECT classification FROM privacy_labels WHERE object_type=? AND object_id=?",
            (kind, object_id),
        )
        if live_label["classification"] != privacy.labels.get((kind, object_id)):
            raise ValueError("Privacy context is stale")
    occurrence = _one(
        connection, "SELECT * FROM person_occurrences WHERE source_item_id=?", (source["id"],)
    )
    if (
        occurrence["id"] != stable_id("occ_mh_profile", tree_id, profile_id)
        or occurrence["person_id"] != person_id
        or occurrence["verification_status"] in {"retracted", "superseded"}
        or privacy.blocked("person_occurrence", occurrence["id"])
        or privacy.blocked("occurrence", occurrence["id"])
        or privacy.blocked_reference(occurrence["id"])
    ):
        raise ValueError("Occurrence is unavailable or conflicts with target")
    occurrence_labels = _rows(
        connection,
        """SELECT object_type,classification FROM privacy_labels
           WHERE object_type IN ('occurrence','person_occurrence') AND object_id=?""",
        (occurrence["id"],),
    )
    if any(label["classification"] not in _HISTORICAL
           or label["classification"] != privacy.labels.get((label["object_type"], occurrence["id"]))
           for label in occurrence_labels):
        raise ValueError("Occurrence privacy is unavailable or stale")
    evidence = _object_json(occurrence["evidence_json"])
    if any(type(evidence.get(key)) is not type(source_metadata[key])
           or evidence.get(key) != source_metadata[key]
           for key in ("service", "treeId", "personId", "externalTreeLead", "noAutoMerge", "noConfirmedPromotion")):
        raise ValueError("Occurrence profile backing conflicts")
    declaration = _declaration(root, metadata)
    expected_declaration = {
        "at": metadata["recordedAt"],
        "personId": person_id,
        "profileId": profile_id,
        "provenanceKind": "family_testimony",
        "familyKnowledgeStatus": "family_known",
        "userDeclaredCertainty": "100%",
        "basis": "reported conversations with parents-in-law",
    }
    if any(declaration.get(key) != value for key, value in expected_declaration.items()):
        raise ValueError("Declaration target or semantics conflicts")
    recorded_at = datetime.fromisoformat(metadata["recordedAt"])
    if recorded_at.tzinfo is None:
        raise ValueError("Missing declaration timestamp timezone")
    note = _one(connection, "SELECT * FROM family_notes WHERE id=?", (metadata["familyNoteId"],))
    if (
        note["subject_type"] != "person"
        or note["subject_id"] != person_id
        or note["classification"] != "family_private"
        or note["recorded_at"] != metadata["recordedAt"]
        or note["consent_status"] not in {"granted", "not_applicable"}
        or not note["informant"]
        or note["note_text"].strip() != note["note_text"]
        or _object_json(note["note_text"]) != declaration
    ):
        raise ValueError("Private note no longer backs this declaration")
    if note["review_after"] and datetime.fromisoformat(note["review_after"]).date() <= datetime.now().date():
        raise ValueError("Family note review is due")
    command, audit = _validate_command(connection, note)
    source_audit = _one(
        connection,
        """SELECT * FROM audit_log WHERE action='family_knowledge_source_metadata'
           AND object_type='source_item' AND object_id=?""",
        (source["id"],),
    )
    previous_metadata = {key: value for key, value in source_metadata.items() if key != "familyKnowledgeV1"}
    if (
        source_audit["actor"] != command["actor"]
        or _object_json(source_audit["before_json"]) != {"metadataSha256": fingerprint(previous_metadata)}
        or _object_json(source_audit["after_json"]) != {
            "familyNoteId": note["id"], "status": "family_known",
            "metadataSha256": fingerprint(source_metadata),
        }
    ):
        raise ValueError("Source recognition metadata lacks its unchanged audit backing")
    opaque_ref = fingerprint({
        "occurrenceId": occurrence["id"], "personId": person_id,
        "sourceId": source["id"], "familyNoteId": note["id"],
        "declarationSHA256": metadata["declarationSHA256"],
        "noteFingerprint": fingerprint(note["note_text"]),
        "metadataFingerprint": fingerprint(source_metadata),
        "commandId": command["command_id"], "requestFingerprint": command["request_fingerprint"],
        "auditId": audit["id"], "sourceAuditId": source_audit["id"],
    })
    return occurrence["id"], {**_SUMMARY, "provenanceRef": f"family_knowledge_{opaque_ref}"}


def project_family_knowledge(
    connection: sqlite3.Connection,
    workspace_root: Path | None,
    privacy: PrivacyContext,
) -> dict[str, dict[str, str]]:
    """Return sanitized recognition by exact external occurrence, without writes.

    The v1 adapter deliberately supports only the recorded conversations basis.
    Unknown, missing, changed or ambiguous backings produce no annotation.
    """
    if workspace_root is None:
        return {}
    try:
        root = Path(workspace_root).resolve(strict=True)
        sources = _rows(
            connection, "SELECT * FROM source_items WHERE item_type='external_tree_profile' ORDER BY id"
        )
    except (OSError, ValueError, TypeError, sqlite3.DatabaseError):
        return {}
    result: dict[str, dict[str, str]] = {}
    for source in sources:
        try:
            occurrence_id, summary = _project_one(connection, root, privacy, source)
        except (OSError, ValueError, TypeError, KeyError, OverflowError, sqlite3.DatabaseError):
            continue
        result[occurrence_id] = summary
    return result
