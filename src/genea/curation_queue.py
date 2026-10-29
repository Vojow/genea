from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .db import connect, initialize
from .research import require_tables
from .workspace import utc_now


DECISIONS = {"resolve", "defer", "dismiss"}
STATUS_BY_DECISION = {
    "resolve": "resolved",
    "defer": "deferred",
    "dismiss": "dismissed",
}
REQUIRED_TABLES = {"audit_log", "curation_queue"}


class CurationQueueError(ValueError):
    """A safe validation error for an administrative queue decision."""


class CurationQueueConflict(CurationQueueError):
    """The queue item changed after the curator generated a preview."""


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CurationQueueError(f"Pole {label} musi być niepustym tekstem")
    return value.strip()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _row(connection, queue_id: str) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    row = connection.execute(
        """SELECT id,object_type,object_id,reason_code,payload_json,status,
                  created_at,resolved_at,resolution_note
           FROM curation_queue WHERE id=?""",
        (queue_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"Nie znaleziono pozycji kolejki {queue_id}")
    return dict(row)


def _fingerprint(row: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(row).encode("utf-8")).hexdigest()


def curation_queue_preview(root: Path, queue_id: str) -> dict[str, Any]:
    queue_id = _required_text(queue_id, "queueId")
    initialize(root)
    with connect(root) as connection:
        require_tables(connection, REQUIRED_TABLES)
        connection.execute("PRAGMA query_only = ON")
        row = _row(connection, queue_id)
        return {
            "id": queue_id,
            "status": row["status"],
            "fingerprint": _fingerprint(row),
            "objectType": row["object_type"],
            "objectId": row["object_id"],
            "reasonCode": row["reason_code"],
            "payload": json.loads(row["payload_json"] or "{}"),
            "resolutionNote": row["resolution_note"],
            "allowedDecisions": sorted(DECISIONS),
            "canApply": row["status"] in {"open", "deferred"},
        }


def apply_curation_queue_decision(
    root: Path,
    queue_id: str,
    *,
    decision: str,
    expected_fingerprint: str,
    actor: str,
    rationale: str,
    command_id: str,
) -> dict[str, Any]:
    queue_id = _required_text(queue_id, "queueId")
    expected_fingerprint = _required_text(expected_fingerprint, "expectedFingerprint")
    actor = _required_text(actor, "actor")
    rationale = _required_text(rationale, "rationale")
    command_id = _required_text(command_id, "commandId")
    if len(rationale) < 20:
        raise CurationQueueError("Uzasadnienie musi mieć co najmniej 20 znaków")
    if decision not in DECISIONS:
        raise CurationQueueError("Decyzja musi mieć wartość resolve, defer albo dismiss")

    initialize(root)
    now = utc_now()
    with connect(root) as connection:
        require_tables(connection, REQUIRED_TABLES)
        connection.execute("BEGIN IMMEDIATE")
        prior = connection.execute(
            """SELECT object_id,after_json FROM audit_log
               WHERE object_type='curation_queue'
                 AND action='curation_queue_decision'
                 AND json_extract(after_json,'$.commandId')=?
               ORDER BY id LIMIT 1""",
            (command_id,),
        ).fetchone()
        if prior:
            if prior["object_id"] != queue_id:
                raise CurationQueueConflict(
                    "Ten commandId został już użyty dla innej pozycji kolejki"
                )
            after = json.loads(prior["after_json"])
            return {**after, "alreadyApplied": True}

        before = _row(connection, queue_id)
        current_fingerprint = _fingerprint(before)
        if current_fingerprint != expected_fingerprint:
            raise CurationQueueConflict(
                "Pozycja kolejki zmieniła się po podglądzie; odśwież preview"
            )
        if before["status"] not in {"open", "deferred"}:
            raise CurationQueueConflict(
                f"Pozycja ma status {before['status']} i nie przyjmuje nowej decyzji"
            )

        target_status = STATUS_BY_DECISION[decision]
        resolved_at = None if decision == "defer" else now
        connection.execute(
            """UPDATE curation_queue
               SET status=?,resolved_at=?,resolution_note=? WHERE id=?""",
            (target_status, resolved_at, rationale, queue_id),
        )
        after_row = _row(connection, queue_id)
        result = {
            "id": queue_id,
            "decision": decision,
            "status": target_status,
            "fingerprint": _fingerprint(after_row),
            "commandId": command_id,
        }
        cursor = connection.execute(
            """INSERT INTO audit_log(
                   occurred_at,actor,action,object_type,object_id,
                   before_json,after_json,rationale
               ) VALUES (?,?,'curation_queue_decision','curation_queue',?,?,?,?)""",
            (
                now,
                actor,
                queue_id,
                _canonical(before),
                _canonical(result),
                rationale,
            ),
        )
        return {**result, "auditId": int(cursor.lastrowid), "alreadyApplied": False}


__all__ = [
    "CurationQueueConflict",
    "CurationQueueError",
    "apply_curation_queue_decision",
    "curation_queue_preview",
]
