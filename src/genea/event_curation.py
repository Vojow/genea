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
from .event_records import detect_event_record_candidates, event_record_fingerprint
from .research import require_tables
from .workspace import stable_id, utc_now


REQUIRED_TABLES = {
    "audit_log",
    "curation_commands",
    "curation_queue",
    "event_record_candidate_members",
    "event_record_candidates",
    "event_record_decisions",
    "events",
    "source_items",
}
DECISIONS = {"same_record", "distinct_records", "defer"}
STATUS_BY_DECISION = {
    "same_record": "resolved_same_record",
    "distinct_records": "resolved_distinct",
    "defer": "deferred",
}


class EventRecordCurationError(ValueError):
    """A safe validation error for event-record curation."""


class EventRecordCurationConflict(EventRecordCurationError):
    """The candidate changed after a preview was generated."""


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EventRecordCurationError(f"Pole {label} musi być niepustym tekstem")
    return value.strip()


def _candidate(connection, candidate_id: str):  # type: ignore[no-untyped-def]
    row = connection.execute(
        """SELECT c.*,s.title AS source_title,s.item_type AS source_type
           FROM event_record_candidates c
           JOIN source_items s ON s.id=c.source_item_id
           WHERE c.id=?""",
        (candidate_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"Nie znaleziono kandydatury zdarzenia {candidate_id}")
    return row


def _members(connection, candidate_id: str) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
    return [
        dict(row)
        for row in connection.execute(
            """SELECT e.id,e.source_item_id,e.event_type,e.date_text,e.date_from,e.date_to,
                      e.place_id,e.place_text,e.verification_status,m.ordinal,
                      COUNT(p.id) AS participant_count
               FROM event_record_candidate_members m
               JOIN events e ON e.id=m.event_id
               LEFT JOIN participations p ON p.event_id=e.id
               WHERE m.candidate_id=?
               GROUP BY e.id,m.ordinal
               ORDER BY m.ordinal,e.id""",
            (candidate_id,),
        )
    ]


def _current_fingerprint(row, members: list[dict[str, Any]]) -> str:  # type: ignore[no-untyped-def]
    return event_record_fingerprint(
        str(row["source_item_id"]), str(row["event_group"]), members
    )


def _latest_decision(connection, candidate_id: str) -> dict[str, Any] | None:  # type: ignore[no-untyped-def]
    row = connection.execute(
        """SELECT id,revision,supersedes_decision_id,decision,decided_by,
                  decided_at,rationale,impact_json,command_id
           FROM event_record_decisions
           WHERE candidate_id=? ORDER BY revision DESC LIMIT 1""",
        (candidate_id,),
    ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["impact"] = json.loads(result.pop("impact_json") or "{}")
    return result


def _preview(connection, candidate_id: str) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    row = _candidate(connection, candidate_id)
    members = _members(connection, candidate_id)
    current_fingerprint = _current_fingerprint(row, members)
    stale = current_fingerprint != row["fingerprint"]
    return {
        "id": row["id"],
        "sourceItemId": row["source_item_id"],
        "sourceTitle": row["source_title"],
        "sourceType": row["source_type"],
        "eventGroup": row["event_group"],
        "status": row["status"],
        "fingerprint": row["fingerprint"],
        "currentFingerprint": current_fingerprint,
        "stale": stale,
        "basis": json.loads(row["basis_json"] or "{}"),
        "members": members,
        "latestDecision": _latest_decision(connection, candidate_id),
        "allowedDecisions": sorted(DECISIONS),
        "risk": "R2",
        "impact": {
            "canonicalEventsChanged": 0,
            "sourceRowsDeleted": 0,
            "presentationRecordsBefore": len(members),
            "presentationRecordsAfterSameRecord": 1,
            "affectedViews": ["akt", "oś czasu", "profil osoby", "mapa"],
        },
        "canApply": row["status"] != "superseded" and not stale,
    }


def refresh_event_record_candidates(
    root: Path, *, actor: str
) -> dict[str, int]:
    """Detect new candidates after imports; never resolve them automatically."""

    actor = _required_text(actor, "actor")
    initialize(root)
    with connect(root) as connection:
        require_tables(connection, REQUIRED_TABLES)
        connection.execute("BEGIN IMMEDIATE")
        return detect_event_record_candidates(connection, actor=actor)


def list_event_record_candidates(
    root: Path, *, status: str | None = None
) -> list[dict[str, Any]]:
    initialize(root)
    with connect(root) as connection:
        require_tables(connection, REQUIRED_TABLES)
        connection.execute("PRAGMA query_only = ON")
        query = "SELECT id FROM event_record_candidates"
        parameters: tuple[Any, ...] = ()
        if status:
            query += " WHERE status=?"
            parameters = (status,)
        query += " ORDER BY updated_at DESC,id"
        return [_preview(connection, row["id"]) for row in connection.execute(query, parameters)]


def event_record_candidate_preview(root: Path, candidate_id: str) -> dict[str, Any]:
    candidate_id = _required_text(candidate_id, "candidateId")
    initialize(root)
    with connect(root) as connection:
        require_tables(connection, REQUIRED_TABLES)
        connection.execute("PRAGMA query_only = ON")
        return _preview(connection, candidate_id)


def _audit(
    connection,  # type: ignore[no-untyped-def]
    *,
    actor: str,
    action: str,
    candidate_id: str,
    before: dict[str, Any],
    after: dict[str, Any],
    rationale: str,
) -> int:
    cursor = connection.execute(
        """INSERT INTO audit_log(
               occurred_at,actor,action,object_type,object_id,
               before_json,after_json,rationale
           ) VALUES (?,?,?,?,?,?,?,?)""",
        (
            utc_now(),
            actor,
            action,
            "event_record_candidate",
            candidate_id,
            json.dumps(before, ensure_ascii=False, sort_keys=True),
            json.dumps(after, ensure_ascii=False, sort_keys=True),
            rationale,
        ),
    )
    return int(cursor.lastrowid)


def apply_event_record_decision(
    root: Path,
    candidate_id: str,
    *,
    decision: str,
    expected_fingerprint: str,
    actor: str,
    rationale: str,
    command_id: str,
) -> dict[str, Any]:
    """Append a curator decision without deleting or rewriting source events."""

    candidate_id = _required_text(candidate_id, "candidateId")
    expected_fingerprint = _required_text(expected_fingerprint, "expectedFingerprint")
    actor = _required_text(actor, "actor")
    rationale = _required_text(rationale, "rationale")
    command_id = _required_text(command_id, "commandId")
    if len(rationale) < 20:
        raise EventRecordCurationError("Uzasadnienie musi mieć co najmniej 20 znaków")
    if decision not in DECISIONS:
        raise EventRecordCurationError(
            "Decyzja musi mieć wartość same_record, distinct_records albo defer"
        )

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
                "actor": actor,
                "rationale": rationale,
            }
        )
        try:
            replay = replay_command(
                connection,
                command_id=command_id,
                command_type="event_record_decision",
                object_type="event_record_candidate",
                object_id=candidate_id,
                request_fingerprint=request_fingerprint,
            )
        except CommandEnvelopeConflict as error:
            raise EventRecordCurationConflict(str(error)) from error
        if replay is not None:
            return replay
        previous_command = connection.execute(
            """SELECT id,candidate_id,revision,decision FROM event_record_decisions
               WHERE command_id=?""",
            (command_id,),
        ).fetchone()
        if previous_command:
            if previous_command["candidate_id"] != candidate_id:
                raise EventRecordCurationConflict(
                    "Ten commandId został już użyty dla innej kandydatury"
                )
            legacy_result = {
                "candidateId": candidate_id,
                "decisionId": previous_command["id"],
                "revision": previous_command["revision"],
                "decision": previous_command["decision"],
                "commandId": command_id,
                "alreadyApplied": True,
            }
            return {**legacy_result, "projectionStatus": "unknown_legacy"}

        preview = _preview(connection, candidate_id)
        if preview["status"] == "superseded":
            raise EventRecordCurationConflict(
                "Kandydatura została zastąpiona; odśwież kolejkę kuratora"
            )
        if preview["stale"] or preview["currentFingerprint"] != expected_fingerprint:
            raise EventRecordCurationConflict(
                "Rekordy zdarzenia zmieniły się po podglądzie; odśwież kandydaturę"
            )
        if preview["fingerprint"] != expected_fingerprint:
            raise EventRecordCurationConflict(
                "Fingerprint nie odpowiada zapisanej kandydaturze"
            )

        latest = preview["latestDecision"]
        revision = int(latest["revision"]) + 1 if latest else 1
        target_status = STATUS_BY_DECISION[decision]
        impact = {
            **preview["impact"],
            "presentationRecordsAfter": 1
            if decision == "same_record"
            else len(preview["members"]),
        }
        decision_id = stable_id(
            "event_record_decision", candidate_id, str(revision), command_id
        )
        connection.execute(
            """INSERT INTO event_record_decisions(
                   id,candidate_id,revision,supersedes_decision_id,decision,
                   decided_by,decided_at,rationale,impact_json,command_id
               ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                decision_id,
                candidate_id,
                revision,
                latest["id"] if latest else None,
                decision,
                actor,
                now,
                rationale,
                json.dumps(impact, ensure_ascii=False, sort_keys=True),
                command_id,
            ),
        )
        connection.execute(
            "UPDATE event_record_candidates SET status=?,updated_at=? WHERE id=?",
            (target_status, now, candidate_id),
        )
        if decision == "defer":
            connection.execute(
                """UPDATE curation_queue
                   SET status='deferred',resolved_at=NULL,resolution_note=?
                   WHERE object_type='event_record_candidate' AND object_id=?""",
                (rationale, candidate_id),
            )
        else:
            connection.execute(
                """UPDATE curation_queue
                   SET status='resolved',resolved_at=?,resolution_note=?
                   WHERE object_type='event_record_candidate' AND object_id=?""",
                (now, rationale, candidate_id),
            )
        after = {
            "status": target_status,
            "decision": decision,
            "decisionId": decision_id,
            "revision": revision,
            "fingerprint": expected_fingerprint,
            "commandId": command_id,
            "projectionStatus": "pending",
        }
        stored_result = {
            "candidateId": candidate_id,
            **after,
            "sourceEventsChanged": 0,
        }
        record_command(
            connection,
            command_id=command_id,
            command_type="event_record_decision",
            object_type="event_record_candidate",
            object_id=candidate_id,
            input_fingerprint=expected_fingerprint,
            request_fingerprint=request_fingerprint,
            actor=actor,
            rationale=rationale,
            result=stored_result,
        )
        audit_id = _audit(
            connection,
            actor=actor,
            action=f"event_record_resolve_{decision}",
            candidate_id=candidate_id,
            before={
                "status": preview["status"],
                "latestDecision": latest,
                "fingerprint": preview["fingerprint"],
            },
            after=after,
            rationale=rationale,
        )
        return {
            **stored_result,
            "auditId": audit_id,
            "alreadyApplied": False,
        }


__all__ = [
    "EventRecordCurationConflict",
    "EventRecordCurationError",
    "apply_event_record_decision",
    "event_record_candidate_preview",
    "list_event_record_candidates",
    "refresh_event_record_candidates",
]
