from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Any

from ..source_relative_materialization import explicit_vital_status
from ..workspace import stable_id
from .privacy import PrivacyContext


_DATE = re.compile(r"^\d{4}(?:-\d{2}(?:-\d{2})?)?$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
_VITAL_REVIEW_GATE = {
    "agent_adjudicated",
    "agent_approved",
    "human_reviewed",
    "human_verified",
    "confirmed",
    "documented",
}


@dataclass(frozen=True)
class TemporalProjection:
    constraints: list[dict[str, Any]]
    participant_details: dict[tuple[str, str], dict[str, Any]]


def _identity_status(person_status: str | None) -> str:
    if person_status is None or person_status == "candidate":
        return "unresolved"
    if person_status in {"confirmed", "resolved"}:
        return "resolved"
    return person_status


def _json_object(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        result = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        return {}
    return result if isinstance(result, dict) else {}


def _reviewed_occurrence_vital_status(
    row: sqlite3.Row,
    *,
    occurrence_event_ids: set[str],
) -> str | None:
    """Return a safely scoped compatibility status from occurrence evidence.

    Older records may carry a reviewed structured status before a typed source
    assertion was written.  Such a status is event-relative only when the
    evidence names this event or the occurrence participates in exactly one
    event.  Multi-event, unscoped evidence remains unknown.
    """

    evidence = _json_object(row["evidence_json"])
    review_status = str(
        evidence.get("vitalStatusFieldStatus")
        or evidence.get("fieldStatus")
        or row["verification_status"]
        or ""
    ).strip().casefold()
    if review_status not in _VITAL_REVIEW_GATE:
        return None
    scoped_event_id = evidence.get("vitalStatusEventId") or evidence.get("eventId")
    if scoped_event_id:
        if str(scoped_event_id) != str(row["event_id"]):
            return None
    elif len(occurrence_event_ids) != 1:
        return None
    return explicit_vital_status(evidence, [])


def project_temporal_constraints(
    connection: sqlite3.Connection,
    privacy: PrivacyContext,
    *,
    allowed_source_ids: set[str],
    allowed_occurrence_ids: set[str],
) -> TemporalProjection:
    """Project explicit life-state bounds without name matching.

    A date-only event can establish only an inclusive day boundary.  A strict
    ``before``/``after`` relation requires a structured timestamp.  The
    optional person link is copied solely from the exact source occurrence.
    """

    rows = connection.execute(
        """SELECT p.event_id,p.occurrence_id,p.status_text,p.source_assertion_id,
                  e.source_item_id,e.date_text,e.date_from,e.date_to,
                  po.person_id,po.evidence_json,po.verification_status,
                  person.status AS person_status,
                  assertion.verification_status AS assertion_status
             FROM participations p
             JOIN events e ON e.id=p.event_id
             JOIN person_occurrences po ON po.id=p.occurrence_id
             LEFT JOIN persons person ON person.id=po.person_id
             LEFT JOIN source_assertions assertion ON assertion.id=p.source_assertion_id
            ORDER BY p.event_id,p.occurrence_id,p.id"""
    ).fetchall()
    event_ids_by_occurrence: dict[str, set[str]] = {}
    for row in rows:
        event_ids_by_occurrence.setdefault(str(row["occurrence_id"]), set()).add(
            str(row["event_id"])
        )
    typed_by_participant: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for assertion in connection.execute(
        """SELECT id,source_item_id,subject_ref,object_text,verification_status,
                  metadata_json
             FROM source_assertions
            WHERE assertion_type='source-stated-vital-status'
              AND predicate='vital-status-at-event'
              AND verification_status NOT IN ('retracted','superseded')
            ORDER BY id"""
    ):
        try:
            metadata = json.loads(assertion["metadata_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        event_id = metadata.get("eventId") if isinstance(metadata, dict) else None
        if not event_id or assertion["object_text"] not in {"living", "deceased"}:
            continue
        typed_assertion = dict(assertion)
        typed_assertion["metadata"] = metadata
        typed_by_participant.setdefault(
            (str(event_id), str(assertion["subject_ref"])), []
        ).append(typed_assertion)
    constraints: list[dict[str, Any]] = []
    details: dict[tuple[str, str], dict[str, Any]] = {}
    seen_constraints: set[str] = set()
    for row in rows:
        source_id = row["source_item_id"]
        occurrence_id = row["occurrence_id"]
        if (
            source_id not in allowed_source_ids
            or occurrence_id not in allowed_occurrence_ids
            or privacy.blocked("event", row["event_id"])
        ):
            continue
        key = (row["event_id"], occurrence_id)
        typed = [
            item
            for item in typed_by_participant.get(key, [])
            if item["source_item_id"] == source_id
        ]
        typed_states = {item["object_text"] for item in typed}
        if len(typed_states) == 1:
            vital_status = next(iter(typed_states))
            typed_assertion = typed[0]
        elif typed_states:
            vital_status = None
            typed_assertion = None
        else:
            # Transitional compatibility for pre-backfill records.  A
            # reviewed structured occurrence status is accepted only when its
            # event scope is explicit or unambiguous.  Free status text is
            # deliberately not reinterpreted by the presentation layer.
            vital_status = _reviewed_occurrence_vital_status(
                row,
                occurrence_event_ids=event_ids_by_occurrence.get(
                    str(occurrence_id), set()
                ),
            )
            typed_assertion = None
        person_id = row["person_id"]
        detail = {"vitalStatus": vital_status}
        if typed_assertion:
            detail["vitalStatusSourceAssertionId"] = typed_assertion["id"]
        if person_id:
            detail.update(
                {
                    "personId": person_id,
                    "personStatus": row["person_status"],
                    "identityStatus": _identity_status(row["person_status"]),
                }
            )
        previous = details.get(key)
        if previous and previous["vitalStatus"] not in {None, vital_status}:
            detail["vitalStatus"] = None
        details[key] = detail
        if vital_status not in {"living", "deceased"}:
            continue

        if vital_status == "deceased":
            boundary = row["date_to"] or row["date_from"]
            relation = "before" if _TIMESTAMP.match(str(boundary or "")) else "on_or_before"
        else:
            boundary = row["date_from"] or row["date_to"]
            relation = "after" if _TIMESTAMP.match(str(boundary or "")) else "on_or_after"
        if not boundary or not (_DATE.match(str(boundary)) or _TIMESTAMP.match(str(boundary))):
            continue
        assertion_id = typed_assertion["id"] if typed_assertion else None
        field_status = (
            typed_assertion.get("metadata", {}).get("fieldStatus")
            if typed_assertion
            else None
        )
        status = field_status or (
            typed_assertion["verification_status"]
            if typed_assertion
            else row["verification_status"]
        )
        constraint_id = stable_id(
            "temporal_constraint",
            row["event_id"],
            occurrence_id,
            vital_status,
            relation,
            str(boundary),
        )
        if constraint_id in seen_constraints:
            continue
        seen_constraints.add(constraint_id)
        constraints.append(
            {
                "id": constraint_id,
                "occurrenceId": occurrence_id,
                "personId": person_id,
                "sourceId": source_id,
                "eventId": row["event_id"],
                "sourceAssertionId": assertion_id,
                "vitalEventType": "death",
                "relation": relation,
                "boundaryDate": boundary,
                "status": status,
                "vitalStatus": vital_status,
                "derivationStatus": "deterministic_explicit_vital_status",
            }
        )
    return TemporalProjection(constraints, details)


__all__ = ["TemporalProjection", "project_temporal_constraints"]
