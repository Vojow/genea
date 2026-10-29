from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from typing import Any

from .workspace import stable_id, utc_now


EVENT_TYPE_GROUPS = {
    "birth-baptism": "birth-baptism",
    "birth": "birth-baptism",
    "urodzenie": "birth-baptism",
    "urodzenie dziecka": "birth-baptism",
    "urodzenie i chrzest": "birth-baptism",
    "chrzest": "birth-baptism",
    "death-burial": "death-burial",
    "death": "death-burial",
    "zgon": "death-burial",
    "pogrzeb": "death-burial",
    "marriage": "marriage",
    "małżeństwo": "marriage",
    "małżeństwo religijne": "marriage",
    "census": "census",
    "księga ludności": "census",
    "spis ludności": "census",
}


def event_group(event_type: str | None) -> str:
    folded = " ".join((event_type or "").casefold().split())
    return EVENT_TYPE_GROUPS.get(folded, folded or "unknown")


def event_record_fingerprint(
    source_item_id: str,
    group: str,
    events: list[dict[str, Any]],
) -> str:
    payload = {
        "sourceItemId": source_item_id,
        "eventGroup": group,
        "events": [
            {
                "id": str(event.get("id") or ""),
                "type": str(event.get("event_type") or ""),
                "dateText": event.get("date_text"),
                "dateFrom": event.get("date_from"),
                "dateTo": event.get("date_to"),
                "placeId": event.get("place_id"),
                "placeText": event.get("place_text"),
                "status": event.get("verification_status"),
            }
            for event in sorted(events, key=lambda item: str(item.get("id") or ""))
        ],
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def detect_event_record_candidates(
    connection: sqlite3.Connection,
    *,
    actor: str = "system:event-record-detector",
    detected_at: str | None = None,
) -> dict[str, int]:
    """Persist review candidates without deciding whether records are identical.

    Only act-level sources are eligible. A register, page or same-day match is
    never sufficient, because those containers may legitimately hold several
    distinct records of the same type.
    """

    now = detected_at or utc_now()
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    rows = connection.execute(
        """SELECT e.id,e.source_item_id,e.event_type,e.date_text,e.date_from,e.date_to,
                  e.place_id,e.place_text,e.verification_status
           FROM events e
           JOIN source_items s ON s.id=e.source_item_id
           WHERE s.item_type='act'
             AND e.verification_status NOT IN ('retracted','superseded')
           ORDER BY e.source_item_id,e.id"""
    ).fetchall()
    for row in rows:
        event = _row_dict(row)
        grouped[(str(row["source_item_id"]), event_group(row["event_type"]))].append(
            event
        )

    result = {"detected": 0, "existing": 0, "superseded": 0, "queueItems": 0}
    for (source_id, group), events in grouped.items():
        if len(events) < 2:
            continue
        fingerprint = event_record_fingerprint(source_id, group, events)
        existing = connection.execute(
            """SELECT id,status FROM event_record_candidates
               WHERE source_item_id=? AND event_group=? AND fingerprint=?""",
            (source_id, group, fingerprint),
        ).fetchone()
        if existing:
            candidate_id = str(existing["id"])
            result["existing"] += 1
        else:
            for stale in connection.execute(
                """SELECT id FROM event_record_candidates
                   WHERE source_item_id=? AND event_group=?
                     AND status IN ('proposed','deferred')""",
                (source_id, group),
            ).fetchall():
                connection.execute(
                    """UPDATE event_record_candidates
                       SET status='superseded',updated_at=? WHERE id=?""",
                    (now, stale["id"]),
                )
                connection.execute(
                    """UPDATE curation_queue
                       SET status='superseded',resolved_at=?,resolution_note=?
                       WHERE object_type='event_record_candidate' AND object_id=?
                         AND status IN ('open','deferred')""",
                    (
                        now,
                        "Skład rekordów zdarzenia zmienił się; utworzono nową kandydaturę.",
                        stale["id"],
                    ),
                )
                result["superseded"] += 1
            candidate_id = stable_id(
                "event_record_candidate", source_id, group, fingerprint
            )
            basis = {
                "method": "parallel_event_records_in_single_act",
                "automaticDecision": False,
                "eventIds": [event["id"] for event in events],
                "eventTypes": sorted({str(event["event_type"]) for event in events}),
                "caution": "Kandydatura nie oznacza jeszcze, że rekordy opisują to samo zdarzenie.",
            }
            connection.execute(
                """INSERT INTO event_record_candidates(
                       id,source_item_id,event_group,fingerprint,status,basis_json,
                       created_by,created_at,updated_at
                   ) VALUES (?,?,?,?, 'proposed', ?,?,?,?)""",
                (
                    candidate_id,
                    source_id,
                    group,
                    fingerprint,
                    json.dumps(basis, ensure_ascii=False, sort_keys=True),
                    actor,
                    now,
                    now,
                ),
            )
            for ordinal, event in enumerate(events, start=1):
                connection.execute(
                    """INSERT INTO event_record_candidate_members(
                           candidate_id,event_id,ordinal
                       ) VALUES (?,?,?)""",
                    (candidate_id, event["id"], ordinal),
                )
            result["detected"] += 1

        queue_id = stable_id(
            "curation_item",
            "event_record_candidate",
            candidate_id,
            "parallel_event_records_in_single_act",
        )
        payload = {
            "candidateId": candidate_id,
            "sourceItemId": source_id,
            "eventGroup": group,
            "fingerprint": fingerprint,
            "eventIds": [event["id"] for event in events],
            "automaticDecision": False,
        }
        before = connection.total_changes
        connection.execute(
            """INSERT INTO curation_queue(
                   id,object_type,object_id,reason_code,payload_json,status,created_at
               ) VALUES (?,?,?,?,?,'open',?)
               ON CONFLICT(object_type,object_id,reason_code) DO NOTHING""",
            (
                queue_id,
                "event_record_candidate",
                candidate_id,
                "parallel_event_records_in_single_act",
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                now,
            ),
        )
        if connection.total_changes > before:
            result["queueItems"] += 1
    return result


__all__ = [
    "EVENT_TYPE_GROUPS",
    "detect_event_record_candidates",
    "event_group",
    "event_record_fingerprint",
]
