from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .command_envelope import (
    CommandEnvelopeConflict,
    fingerprint,
    record_command,
    replay_command,
)
from .db import connect, initialize
from .research import require_tables
from .workspace import search_fold, stable_id, utc_now


IDENTITY_REVIEW_THRESHOLD = 0.90

REQUIRED_TABLES = {
    "audit_log",
    "curation_commands",
    "external_sync_items",
    "family_notes",
    "household_memberships",
    "identity_candidates",
    "identity_decisions",
    "person_names",
    "person_occurrences",
    "persons",
    "privacy_labels",
    "private_attachments",
    "private_fields",
    "private_relationships",
    "research_question_targets",
}


class IdentityCurationError(ValueError):
    """A safe, user-facing identity curation validation error."""


class IdentityCurationConflict(IdentityCurationError):
    """The candidate changed after the UI preview was generated."""


_LEGACY_UNRESOLVED_PERSON_NOTE = (
    "One candidate per person named in one source document; "
    "identity across sources intentionally unresolved."
)


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IdentityCurationError(f"Pole {label} musi być niepustym tekstem")
    return value.strip()


def _candidate(connection, candidate_id: str):  # type: ignore[no-untyped-def]
    row = connection.execute(
        """SELECT ic.id,ic.occurrence_a_id,ic.occurrence_b_id,ic.score,
                  ic.basis_json,ic.status,
                  a.person_id AS person_a_id,a.raw_name AS raw_a,
                  a.normalized_name AS normalized_a,a.source_item_id AS source_a_id,
                  b.person_id AS person_b_id,b.raw_name AS raw_b,
                  b.normalized_name AS normalized_b,b.source_item_id AS source_b_id,
                  sa.title AS source_a_title,sb.title AS source_b_title,
                  pa.preferred_name AS person_a_name,pb.preferred_name AS person_b_name
           FROM identity_candidates ic
           JOIN person_occurrences a ON a.id=ic.occurrence_a_id
           JOIN person_occurrences b ON b.id=ic.occurrence_b_id
           JOIN source_items sa ON sa.id=a.source_item_id
           JOIN source_items sb ON sb.id=b.source_item_id
           LEFT JOIN persons pa ON pa.id=a.person_id
           LEFT JOIN persons pb ON pb.id=b.person_id
           WHERE ic.id=?""",
        (candidate_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"Nie znaleziono kandydatury {candidate_id}")
    return row


def _person_impact(connection, person_id: str | None) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    if not person_id:
        return {
            "personId": None,
            "occurrences": 0,
            "names": 0,
            "households": 0,
            "protectedDependencies": [],
        }
    protected: list[str] = []
    checks = (
        (
            "external_sync",
            "SELECT COUNT(*) FROM external_sync_items WHERE object_type='person' AND object_id=?",
            (person_id,),
        ),
        (
            "research_question",
            "SELECT COUNT(*) FROM research_question_targets WHERE target_type='person' AND target_id=?",
            (person_id,),
        ),
        (
            "private_field",
            "SELECT COUNT(*) FROM private_fields WHERE object_type='person' AND object_id=?",
            (person_id,),
        ),
        (
            "family_note",
            "SELECT COUNT(*) FROM family_notes WHERE subject_type='person' AND subject_id=?",
            (person_id,),
        ),
        (
            "private_attachment",
            "SELECT COUNT(*) FROM private_attachments WHERE object_type='person' AND object_id=?",
            (person_id,),
        ),
        (
            "private_relationship",
            "SELECT COUNT(*) FROM private_relationships WHERE subject_person_id=? OR object_person_id=?",
            (person_id, person_id),
        ),
    )
    for label, query, parameters in checks:
        count = int(connection.execute(query, parameters).fetchone()[0])
        if count:
            protected.append(f"{label}:{count}")
    return {
        "personId": person_id,
        "occurrences": int(
            connection.execute(
                "SELECT COUNT(*) FROM person_occurrences WHERE person_id=?", (person_id,)
            ).fetchone()[0]
        ),
        "names": int(
            connection.execute(
                "SELECT COUNT(*) FROM person_names WHERE person_id=?", (person_id,)
            ).fetchone()[0]
        ),
        "households": int(
            connection.execute(
                "SELECT COUNT(*) FROM household_memberships WHERE person_id=?", (person_id,)
            ).fetchone()[0]
        ),
        "protectedDependencies": protected,
    }


def _stable_rows(rows) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
    result: list[dict[str, Any]] = []
    for row in rows:
        item: dict[str, Any] = {}
        for key in row.keys():
            value = row[key]
            item[key] = {"sha256": fingerprint(value.hex())} if isinstance(value, bytes) else value
        result.append(item)
    return result


def _identity_state(connection, candidate_id: str) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """Return every row whose drift can change the accepted identity impact."""

    candidate = dict(_candidate(connection, candidate_id))
    occurrence_ids = [candidate["occurrence_a_id"], candidate["occurrence_b_id"]]
    person_ids = sorted(
        {
            str(item)
            for item in (candidate["person_a_id"], candidate["person_b_id"])
            if item
        }
    )
    linked_occurrences = list(occurrence_ids)
    if person_ids:
        placeholders = ",".join("?" for _ in person_ids)
        linked_occurrences.extend(
            str(row["id"])
            for row in connection.execute(
                f"SELECT id FROM person_occurrences WHERE person_id IN ({placeholders})",
                tuple(person_ids),
            )
        )
    linked_occurrences = sorted(set(linked_occurrences))

    def selected(table: str, column: str, values: list[str]) -> list[dict[str, Any]]:
        if not values:
            return []
        placeholders = ",".join("?" for _ in values)
        return _stable_rows(
            connection.execute(
                f"SELECT * FROM {table} WHERE {column} IN ({placeholders}) ORDER BY id",
                tuple(values),
            )
        )

    dependencies: dict[str, list[dict[str, Any]]] = {}
    if person_ids:
        placeholders = ",".join("?" for _ in person_ids)
        dependencies = {
            "externalSync": _stable_rows(
                connection.execute(
                    f"SELECT * FROM external_sync_items WHERE object_type='person' AND object_id IN ({placeholders}) ORDER BY id",
                    tuple(person_ids),
                )
            ),
            "questionTargets": _stable_rows(
                connection.execute(
                    f"SELECT * FROM research_question_targets WHERE target_type='person' AND target_id IN ({placeholders}) ORDER BY id",
                    tuple(person_ids),
                )
            ),
            "privateFields": _stable_rows(
                connection.execute(
                    f"SELECT * FROM private_fields WHERE object_type='person' AND object_id IN ({placeholders}) ORDER BY id",
                    tuple(person_ids),
                )
            ),
            "familyNotes": _stable_rows(
                connection.execute(
                    f"SELECT * FROM family_notes WHERE subject_type='person' AND subject_id IN ({placeholders}) ORDER BY id",
                    tuple(person_ids),
                )
            ),
            "privateAttachments": _stable_rows(
                connection.execute(
                    f"SELECT * FROM private_attachments WHERE object_type='person' AND object_id IN ({placeholders}) ORDER BY id",
                    tuple(person_ids),
                )
            ),
            "privateRelationships": _stable_rows(
                connection.execute(
                    f"SELECT * FROM private_relationships WHERE subject_person_id IN ({placeholders}) OR object_person_id IN ({placeholders}) ORDER BY id",
                    tuple(person_ids + person_ids),
                )
            ),
        }
    source_ids = sorted({candidate["source_a_id"], candidate["source_b_id"]})
    related_candidates: list[dict[str, Any]] = []
    if linked_occurrences:
        placeholders = ",".join("?" for _ in linked_occurrences)
        related_candidates = _stable_rows(
            connection.execute(
                f"SELECT * FROM identity_candidates WHERE occurrence_a_id IN ({placeholders}) OR occurrence_b_id IN ({placeholders}) ORDER BY id",
                tuple(linked_occurrences + linked_occurrences),
            )
        )
    return {
        "candidate": candidate,
        "persons": selected("persons", "id", person_ids),
        "occurrences": selected("person_occurrences", "id", linked_occurrences),
        "personNames": selected("person_names", "person_id", person_ids),
        "householdMemberships": selected("household_memberships", "person_id", person_ids),
        "privacyLabels": _stable_rows(
            connection.execute(
                "SELECT * FROM privacy_labels WHERE (object_type='person' AND object_id IN ({persons})) OR (object_type='person_occurrence' AND object_id IN ({occurrences})) ORDER BY id".format(
                    persons=",".join("?" for _ in person_ids) or "NULL",
                    occurrences=",".join("?" for _ in linked_occurrences) or "NULL",
                ),
                tuple(person_ids + linked_occurrences),
            )
        ),
        "sources": selected("source_items", "id", source_ids),
        "relatedCandidates": related_candidates,
        "dependencies": dependencies,
    }


def _preview(connection, candidate_id: str) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    row = _candidate(connection, candidate_id)
    state_fingerprint = fingerprint(_identity_state(connection, candidate_id))
    basis = json.loads(row["basis_json"] or "{}")
    person_ids = [item for item in (row["person_a_id"], row["person_b_id"]) if item]
    distinct_person_ids = list(dict.fromkeys(person_ids))
    impacts = [_person_impact(connection, person_id) for person_id in distinct_person_ids]
    blockers = sorted(
        {
            dependency
            for impact in impacts
            for dependency in impact["protectedDependencies"]
        }
    )
    return {
        "id": row["id"],
        "status": row["status"],
        "expectedStatus": row["status"],
        "fingerprint": state_fingerprint,
        "score": row["score"],
        "threshold": IDENTITY_REVIEW_THRESHOLD,
        "scoreSemantics": basis.get("score_semantics") or "research_priority_not_probability",
        "basis": basis.get("basis") or "Brak zapisanego uzasadnienia.",
        "from": {
            "occurrenceId": row["occurrence_a_id"],
            "personId": row["person_a_id"],
            "label": row["person_a_name"] or row["normalized_a"] or row["raw_a"],
            "rawName": row["raw_a"],
            "sourceId": row["source_a_id"],
            "sourceTitle": row["source_a_title"],
        },
        "to": {
            "occurrenceId": row["occurrence_b_id"],
            "personId": row["person_b_id"],
            "label": row["person_b_name"] or row["normalized_b"] or row["raw_b"],
            "rawName": row["raw_b"],
            "sourceId": row["source_b_id"],
            "sourceTitle": row["source_b_title"],
        },
        "canonicalOptions": [
            {
                "personId": person_id,
                "label": row["person_a_name"]
                if person_id == row["person_a_id"]
                else row["person_b_name"],
                "mergeBlockers": sorted(
                    {
                        dependency
                        for impact in impacts
                        if impact["personId"] != person_id
                        for dependency in impact["protectedDependencies"]
                    }
                ),
            }
            for person_id in distinct_person_ids
        ],
        "requiresCanonicalChoice": len(distinct_person_ids) > 1,
        "requiresPreferredName": not distinct_person_ids,
        "impact": impacts,
        "blockers": blockers,
        "canResolve": (
            row["status"] == "unresolved"
            and row["score"] is not None
            and float(row["score"]) >= IDENTITY_REVIEW_THRESHOLD
        ),
    }


def identity_candidate_preview(root: Path, candidate_id: str) -> dict[str, Any]:
    """Return a fresh, read-only impact preview for one identity candidate."""

    candidate_id = _required_text(candidate_id, "candidateId")
    initialize(root)
    with connect(root) as connection:
        require_tables(connection, REQUIRED_TABLES)
        connection.execute("PRAGMA query_only = ON")
        return _preview(connection, candidate_id)


def add_identity_candidate(
    root: Path,
    occurrence_a_id: str,
    occurrence_b_id: str,
    *,
    score: float,
    basis: str,
    actor: str,
    support: list[str] | None = None,
    against: list[str] | None = None,
    human_review_debt: list[str] | None = None,
    bundle_id: str | None = None,
) -> dict[str, Any]:
    """Persist one auditable, unresolved identity proposal.

    ``score`` orders curator review. It is never presented as a probability and
    this operation never links occurrences to a canonical person.
    """

    occurrence_a_id = _required_text(occurrence_a_id, "occurrenceAId")
    occurrence_b_id = _required_text(occurrence_b_id, "occurrenceBId")
    basis = _required_text(basis, "basis")
    actor = _required_text(actor, "actor")
    if occurrence_a_id == occurrence_b_id:
        raise IdentityCurationError("Kandydatura wymaga dwóch różnych wystąpień")
    if not 0 <= score <= 1:
        raise IdentityCurationError("Score kandydatury musi mieścić się w zakresie 0–1")

    initialize(root)
    with connect(root) as connection:
        require_tables(connection, REQUIRED_TABLES)
        rows = connection.execute(
            """SELECT id FROM person_occurrences WHERE id IN (?,?) ORDER BY id""",
            (occurrence_a_id, occurrence_b_id),
        ).fetchall()
        found = {row["id"] for row in rows}
        missing = sorted({occurrence_a_id, occurrence_b_id} - found)
        if missing:
            raise LookupError("Nie znaleziono wystąpień: " + ", ".join(missing))

        existing = connection.execute(
            """SELECT id,status,score,basis_json FROM identity_candidates
               WHERE (occurrence_a_id=? AND occurrence_b_id=?)
                  OR (occurrence_a_id=? AND occurrence_b_id=?)
               LIMIT 1""",
            (occurrence_a_id, occurrence_b_id, occurrence_b_id, occurrence_a_id),
        ).fetchone()
        if existing:
            return {
                "id": existing["id"],
                "status": existing["status"],
                "score": existing["score"],
                "alreadyExists": True,
                "basis": json.loads(existing["basis_json"] or "{}").get("basis"),
                "scoreSemantics": "research_priority_not_probability",
            }

        candidate_id = stable_id(
            "identity_candidate", *sorted((occurrence_a_id, occurrence_b_id))
        )
        now = utc_now()
        basis_payload = {
            "basis": basis,
            "score_semantics": "research_priority_not_probability",
            "created_by": actor,
            "support": [item.strip() for item in support or [] if item.strip()],
            "against": [item.strip() for item in against or [] if item.strip()],
            "human_review_debt": [
                item.strip() for item in human_review_debt or [] if item.strip()
            ],
        }
        if bundle_id:
            basis_payload["candidate_bundle_id"] = _required_text(bundle_id, "bundleId")
        connection.execute(
            """INSERT INTO identity_candidates(
                   id,occurrence_a_id,occurrence_b_id,score,basis_json,status,created_at
               ) VALUES (?,?,?,?,?,'unresolved',?)""",
            (
                candidate_id,
                occurrence_a_id,
                occurrence_b_id,
                score,
                json.dumps(basis_payload, ensure_ascii=False, sort_keys=True),
                now,
            ),
        )
        _audit(
            connection,
            actor=actor,
            action="identity_candidate_add",
            object_type="identity_candidate",
            object_id=candidate_id,
            before=None,
            after={
                "occurrenceAId": occurrence_a_id,
                "occurrenceBId": occurrence_b_id,
                "score": score,
                "status": "unresolved",
                "scoreSemantics": "research_priority_not_probability",
            },
            rationale=basis,
        )
        return {
            "id": candidate_id,
            "status": "unresolved",
            "score": score,
            "alreadyExists": False,
            "basis": basis,
            "scoreSemantics": "research_priority_not_probability",
        }


def _audit(
    connection,  # type: ignore[no-untyped-def]
    *,
    actor: str,
    action: str,
    object_type: str,
    object_id: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    rationale: str,
) -> None:
    connection.execute(
        """INSERT INTO audit_log(
               occurred_at,actor,action,object_type,object_id,
               before_json,after_json,rationale
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


def _merge_households(connection, canonical_id: str, merged_id: str) -> None:  # type: ignore[no-untyped-def]
    rows = connection.execute(
        "SELECT * FROM household_memberships WHERE person_id=? ORDER BY id",
        (merged_id,),
    ).fetchall()
    for row in rows:
        duplicate = connection.execute(
            """SELECT id FROM household_memberships
               WHERE household_id=? AND person_id=?
                 AND role IS ? AND date_from IS ? AND date_to IS ?
               LIMIT 1""",
            (
                row["household_id"],
                canonical_id,
                row["role"],
                row["date_from"],
                row["date_to"],
            ),
        ).fetchone()
        if duplicate:
            connection.execute("DELETE FROM household_memberships WHERE id=?", (row["id"],))
        else:
            connection.execute(
                "UPDATE household_memberships SET person_id=? WHERE id=?",
                (canonical_id, row["id"]),
            )


def _merge_person(
    connection, canonical_id: str, merged_id: str, *, actor: str, rationale: str  # type: ignore[no-untyped-def]
) -> dict[str, int]:
    impact = _person_impact(connection, merged_id)
    if impact["protectedDependencies"]:
        raise IdentityCurationError(
            "Profil przeznaczony do scalenia ma chronione zależności: "
            + ", ".join(impact["protectedDependencies"])
        )
    connection.execute(
        "UPDATE person_occurrences SET person_id=? WHERE person_id=?",
        (canonical_id, merged_id),
    )
    connection.execute(
        "UPDATE person_names SET person_id=? WHERE person_id=?", (canonical_id, merged_id)
    )
    _merge_households(connection, canonical_id, merged_id)
    row = connection.execute(
        "SELECT status,notes FROM persons WHERE id=?", (merged_id,)
    ).fetchone()
    note = f"Scalono przez kurację UI z osobą {canonical_id}."
    notes = "\n".join(filter(None, [row["notes"] if row else None, note]))
    connection.execute(
        "UPDATE persons SET status='merged',notes=? WHERE id=?", (notes, merged_id)
    )
    _audit(
        connection,
        actor=actor,
        action="identity_ui_person_merge",
        object_type="person",
        object_id=merged_id,
        before={"status": row["status"] if row else None, **impact},
        after={"status": "merged", "canonicalPersonId": canonical_id},
        rationale=rationale,
    )
    return {
        "occurrences": int(impact["occurrences"]),
        "names": int(impact["names"]),
        "households": int(impact["households"]),
    }


def _refresh_search_names(connection, person_id: str) -> None:  # type: ignore[no-untyped-def]
    person = connection.execute(
        "SELECT preferred_name FROM persons WHERE id=?", (person_id,)
    ).fetchone()
    values = [person["preferred_name"]] if person and person["preferred_name"] else []
    values.extend(
        row["name_text"]
        for row in connection.execute(
            "SELECT name_text FROM person_names WHERE person_id=? ORDER BY id", (person_id,)
        )
    )
    values.extend(
        row["normalized_name"] or row["raw_name"]
        for row in connection.execute(
            """SELECT normalized_name,raw_name FROM person_occurrences
               WHERE person_id=? ORDER BY id""",
            (person_id,),
        )
    )
    search_name = " | ".join(
        dict.fromkeys(folded for folded in (search_fold(value) for value in values) if folded)
    )
    connection.execute(
        "UPDATE person_occurrences SET search_name=? WHERE person_id=?",
        (search_name, person_id),
    )


def _mark_person_resolved(
    connection, person_id: str, *, actor: str, rationale: str  # type: ignore[no-untyped-def]
) -> bool:
    row = connection.execute(
        "SELECT status,notes FROM persons WHERE id=?", (person_id,)
    ).fetchone()
    if row is None:
        raise LookupError(f"Nie znaleziono osoby {person_id}")
    resolution_note = (
        "Co najmniej jedna kandydatura identity została rozstrzygnięta jako same "
        "przez kontrolowaną kurację; inne kandydatury tej osoby mogą pozostać unresolved."
    )
    note_lines = [
        line
        for line in (row["notes"] or "").splitlines()
        if line.strip() and line.strip() != _LEGACY_UNRESOLVED_PERSON_NOTE
    ]
    if resolution_note not in note_lines:
        note_lines.append(resolution_note)
    notes = "\n".join(note_lines)
    if row["status"] == "resolved" and notes == (row["notes"] or ""):
        return False
    connection.execute(
        "UPDATE persons SET status='resolved',notes=? WHERE id=?", (notes, person_id)
    )
    _audit(
        connection,
        actor=actor,
        action="identity_ui_person_resolve",
        object_type="person",
        object_id=person_id,
        before={"status": row["status"], "notes": row["notes"]},
        after={"status": "resolved", "notes": notes},
        rationale=rationale,
    )
    return True


def apply_identity_decision(
    root: Path,
    candidate_id: str,
    *,
    decision: str,
    actor: str,
    rationale: str,
    expected_fingerprint: str,
    command_id: str,
    expected_status: str = "unresolved",
    canonical_person_id: str | None = None,
    preferred_name: str | None = None,
) -> dict[str, Any]:
    """Apply one explicit same/different decision from the local curator UI."""

    candidate_id = _required_text(candidate_id, "candidateId")
    actor = _required_text(actor, "actor")
    rationale = _required_text(rationale, "rationale")
    expected_fingerprint = _required_text(expected_fingerprint, "expectedFingerprint")
    command_id = _required_text(command_id, "commandId")
    if len(rationale) < 20:
        raise IdentityCurationError("Uzasadnienie musi mieć co najmniej 20 znaków")
    if decision not in {"same", "different"}:
        raise IdentityCurationError("Decyzja musi mieć wartość same albo different")
    if expected_status != "unresolved":
        raise IdentityCurationError("UI może rozstrzygać wyłącznie stan unresolved")

    initialize(root)
    now = utc_now()
    with connect(root) as connection:
        require_tables(connection, REQUIRED_TABLES)
        connection.execute("BEGIN IMMEDIATE")
        request_fingerprint = fingerprint(
            {
                "candidateId": candidate_id,
                "decision": decision,
                "expectedFingerprint": expected_fingerprint,
                "expectedStatus": expected_status,
                "canonicalPersonId": canonical_person_id,
                "preferredName": preferred_name,
                "actor": actor,
                "rationale": rationale,
            }
        )
        try:
            replay = replay_command(
                connection,
                command_id=command_id,
                command_type="identity_decision",
                object_type="identity_candidate",
                object_id=candidate_id,
                request_fingerprint=request_fingerprint,
            )
        except CommandEnvelopeConflict as error:
            raise IdentityCurationConflict(str(error)) from error
        if replay is not None:
            return replay
        preview = _preview(connection, candidate_id)
        if preview["fingerprint"] != expected_fingerprint:
            raise IdentityCurationConflict(
                "Kandydatura lub jej zależności zmieniły się po podglądzie; odśwież widok"
            )
        if preview["status"] != expected_status:
            raise IdentityCurationConflict(
                f"Kandydatura zmieniła stan z {expected_status} na {preview['status']}; odśwież widok"
            )
        if preview["score"] is None or float(preview["score"]) < IDENTITY_REVIEW_THRESHOLD:
            raise IdentityCurationError(
                "Kandydatura nie przekracza progu kolejki UI; wymaga osobnego trybu kuratorskiego"
            )
        row = _candidate(connection, candidate_id)
        before = {
            "status": row["status"],
            "personAId": row["person_a_id"],
            "personBId": row["person_b_id"],
        }
        result: dict[str, Any] = {
            "candidateId": candidate_id,
            "decision": decision,
            "previousStatus": row["status"],
        }

        if decision == "different":
            target_status = "different"
        else:
            distinct = list(
                dict.fromkeys(
                    item for item in (row["person_a_id"], row["person_b_id"]) if item
                )
            )
            if len(distinct) > 1:
                if canonical_person_id not in distinct:
                    raise IdentityCurationError(
                        "Wybierz osobę kanoniczną spośród profili wskazanych w podglądzie"
                    )
                canonical_id = canonical_person_id
            elif distinct:
                canonical_id = distinct[0]
            else:
                name = _required_text(preferred_name, "preferredName")
                canonical_id = stable_id("person", "identity-ui", candidate_id)
                connection.execute(
                    """INSERT INTO persons(id,preferred_name,status,created_at,notes)
                       VALUES (?,?, 'resolved', ?, ?)""",
                    (canonical_id, name, now, f"Utworzono przez kurację UI: {candidate_id}"),
                )
                connection.execute(
                    """INSERT INTO privacy_labels(
                           id,object_type,object_id,classification,rationale,created_at,updated_at
                       ) VALUES (?, 'person', ?, 'deceased', ?, ?, ?)
                       ON CONFLICT(object_type,object_id) DO NOTHING""",
                    (
                        stable_id("privacy", "person", canonical_id),
                        canonical_id,
                        "Historyczna osoba utworzona przez kontrolowaną decyzję identity.",
                        now,
                        now,
                    ),
                )

            merged = {"occurrences": 0, "names": 0, "households": 0}
            for person_id in distinct:
                if person_id == canonical_id:
                    continue
                impact = _merge_person(
                    connection, canonical_id, person_id, actor=actor, rationale=rationale
                )
                for key, value in impact.items():
                    merged[key] += value
            for occurrence_id in (row["occurrence_a_id"], row["occurrence_b_id"]):
                connection.execute(
                    "UPDATE person_occurrences SET person_id=? WHERE id=?",
                    (canonical_id, occurrence_id),
                )
            canonical_status_changed = _mark_person_resolved(
                connection, canonical_id, actor=actor, rationale=rationale
            )
            _refresh_search_names(connection, canonical_id)
            target_status = "resolved_same"
            result.update(
                {
                    "canonicalPersonId": canonical_id,
                    "canonicalPersonStatus": "resolved",
                    "canonicalStatusChanged": canonical_status_changed,
                    "merged": merged,
                }
            )

        decision_id = stable_id("identity_decision", "ui", candidate_id, decision, command_id)
        result.update(
            {
                "status": target_status,
                "decisionId": decision_id,
                "commandId": command_id,
                "inputFingerprint": expected_fingerprint,
                "projectionStatus": "pending",
            }
        )
        record_command(
            connection,
            command_id=command_id,
            command_type="identity_decision",
            object_type="identity_candidate",
            object_id=candidate_id,
            input_fingerprint=expected_fingerprint,
            request_fingerprint=request_fingerprint,
            actor=actor,
            rationale=rationale,
            result=result,
        )
        connection.execute(
            """INSERT INTO identity_decisions(
                   id,candidate_id,decision,decided_by,decided_at,rationale,
                   command_id,input_fingerprint
               ) VALUES (?,?,?,?,?,?,?,?)""",
            (
                decision_id,
                candidate_id,
                decision,
                actor,
                now,
                rationale,
                command_id,
                expected_fingerprint,
            ),
        )
        connection.execute(
            "UPDATE identity_candidates SET status=? WHERE id=?",
            (target_status, candidate_id),
        )
        _audit(
            connection,
            actor=actor,
            action=f"identity_ui_resolve_{decision}",
            object_type="identity_candidate",
            object_id=candidate_id,
            before=before,
            after={"status": target_status, "decisionId": decision_id, **result},
            rationale=rationale,
        )
        return {**result, "alreadyApplied": False}


def reconcile_resolved_identity_person(
    root: Path,
    person_id: str,
    *,
    actor: str,
    rationale: str,
) -> dict[str, Any]:
    """Repair the canonical-person status after an earlier ``resolved_same`` decision."""

    person_id = _required_text(person_id, "personId")
    actor = _required_text(actor, "actor")
    rationale = _required_text(rationale, "rationale")
    initialize(root)
    with connect(root) as connection:
        require_tables(connection, REQUIRED_TABLES)
        connection.execute("BEGIN IMMEDIATE")
        resolved_count = int(
            connection.execute(
                """SELECT COUNT(DISTINCT ic.id)
                     FROM identity_candidates ic
                     JOIN person_occurrences a ON a.id=ic.occurrence_a_id
                     JOIN person_occurrences b ON b.id=ic.occurrence_b_id
                    WHERE ic.status='resolved_same'
                      AND (a.person_id=? OR b.person_id=?)""",
                (person_id, person_id),
            ).fetchone()[0]
        )
        if not resolved_count:
            raise IdentityCurationError(
                "Profil nie ma kandydatury resolved_same i nie może zostać uznany za resolved"
            )
        changed = _mark_person_resolved(
            connection, person_id, actor=actor, rationale=rationale
        )
        return {
            "personId": person_id,
            "status": "resolved",
            "resolvedSameCandidates": resolved_count,
            "changed": changed,
        }


__all__ = [
    "IDENTITY_REVIEW_THRESHOLD",
    "IdentityCurationConflict",
    "IdentityCurationError",
    "add_identity_candidate",
    "apply_identity_decision",
    "identity_candidate_preview",
    "reconcile_resolved_identity_person",
]
