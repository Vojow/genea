from __future__ import annotations

import hashlib
import json
from typing import Any

from .workspace import utc_now


class CommandEnvelopeConflict(ValueError):
    """A command identifier was reused for a different semantic request."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def replay_command(
    connection,  # type: ignore[no-untyped-def]
    *,
    command_id: str,
    command_type: str,
    object_type: str,
    object_id: str,
    request_fingerprint: str,
) -> dict[str, Any] | None:
    row = connection.execute(
        """SELECT command_type,object_type,object_id,request_fingerprint,
                  result_json,projection_status,projection_error
           FROM curation_commands WHERE command_id=?""",
        (command_id,),
    ).fetchone()
    if row is None:
        return None
    expected = (command_type, object_type, object_id, request_fingerprint)
    actual = (
        row["command_type"],
        row["object_type"],
        row["object_id"],
        row["request_fingerprint"],
    )
    if actual != expected:
        raise CommandEnvelopeConflict(
            "Ten commandId został już użyty dla innej operacji lub innej treści"
        )
    result = json.loads(row["result_json"])
    return {
        **result,
        "alreadyApplied": True,
        "projectionStatus": row["projection_status"],
        "projectionError": row["projection_error"],
    }


def record_command(
    connection,  # type: ignore[no-untyped-def]
    *,
    command_id: str,
    command_type: str,
    object_type: str,
    object_id: str,
    input_fingerprint: str,
    request_fingerprint: str,
    actor: str,
    rationale: str,
    result: dict[str, Any],
    projection_status: str = "pending",
) -> None:
    connection.execute(
        """INSERT INTO curation_commands(
               command_id,command_type,object_type,object_id,input_fingerprint,
               request_fingerprint,result_json,actor,rationale,committed_at,
               projection_status
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            command_id,
            command_type,
            object_type,
            object_id,
            input_fingerprint,
            request_fingerprint,
            canonical_json(result),
            actor,
            rationale,
            utc_now(),
            projection_status,
        ),
    )


def update_projection_status(
    connection,  # type: ignore[no-untyped-def]
    command_id: str,
    *,
    status: str,
    error: str | None = None,
) -> None:
    if status not in {"pending", "refreshed", "failed", "not_required"}:
        raise ValueError(f"Nieobsługiwany status projekcji: {status}")
    connection.execute(
        """UPDATE curation_commands
           SET projection_status=?,projection_updated_at=?,projection_error=?
           WHERE command_id=?""",
        (status, utc_now(), error, command_id),
    )


def set_projection_status(
    root,  # type: ignore[no-untyped-def]
    command_id: str,
    *,
    status: str,
    error: str | None = None,
) -> None:
    from .db import connect, initialize

    initialize(root)
    with connect(root) as connection:
        update_projection_status(connection, command_id, status=status, error=error)


__all__ = [
    "CommandEnvelopeConflict",
    "canonical_json",
    "fingerprint",
    "record_command",
    "replay_command",
    "set_projection_status",
    "update_projection_status",
]
