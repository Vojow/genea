from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from typing import Any

from .common import event_group, unique_display_values
from .privacy import PrivacyContext


def participant_presence(event_type: str | None, participant: dict[str, Any]) -> str:
    if participant.get("informant"):
        return "direct"
    role = " ".join((participant.get("role") or "").casefold().split())
    group = event_group(event_type)
    if group == "census":
        return "direct"
    direct_patterns = {
        "marriage": r"pan młody|panna młoda|bride|groom|świadek|witness",
        "birth-baptism": r"dziecko|urodzon|ochrzcz|baptized|chrzestn|świadek|witness",
        "death-burial": r"zmarł|deceased|pochowan|świadek|witness|zgłasz",
    }
    pattern = direct_patterns.get(group)
    return "direct" if pattern and re.search(pattern, role) else "mentioned"


def _event_record_key(
    event: dict[str, Any],
    curation: dict[str, Any] | None,
) -> str:
    """Return the only key allowed to aggregate canonical event projections.

    Metadata, a shared act container and equal dates may nominate records for
    review, but they are not a curator decision.  Until ``resolved_same_record``
    exists, every source event keeps its own projection identity.
    """

    if curation and curation["status"] == "resolved_same_record":
        return f"event-record-candidate:{curation['candidateId']}"
    return f"event:{event['id']}"


def _event_grouping_proposal_key(event: dict[str, Any]) -> str:
    """Return a presentation-only key for variants inside one admitted act."""

    return f"act-source:{event.get('sourceId') or 'no-source'}:{event_group(event.get('type'))}"


def _event_record_curation_by_event(
    connection: sqlite3.Connection,
) -> dict[str, dict[str, Any]]:
    if connection.execute(
        """SELECT 1 FROM sqlite_master
           WHERE type='table' AND name='event_record_candidates'"""
    ).fetchone() is None:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for row in connection.execute(
        """SELECT m.event_id,c.id AS candidate_id,c.status,c.fingerprint,
                  c.updated_at
           FROM event_record_candidate_members m
           JOIN event_record_candidates c ON c.id=m.candidate_id
           WHERE c.status != 'superseded'
           ORDER BY c.updated_at DESC,c.id"""
    ):
        result.setdefault(
            row["event_id"],
            {
                "candidateId": row["candidate_id"],
                "status": row["status"],
                "fingerprint": row["fingerprint"],
            },
        )
    return result


def project_events(
    connection: sqlite3.Connection,
    privacy: PrivacyContext,
    *,
    allowed_source_ids: set[str],
    allowed_occurrence_ids: set[str],
    occurrences: list[dict[str, Any]],
    participant_details: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    act_source_ids = {
        row["id"]
        for row in connection.execute(
            "SELECT id FROM source_items WHERE item_type='act'"
        )
    }
    record_curation = _event_record_curation_by_event(connection)
    occurrence_by_id = {item["id"]: item for item in occurrences}
    participant_details = participant_details or {}
    participants: dict[str, list[dict[str, Any]]] = defaultdict(list)
    participation_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(participations)")
    }
    residence_place_select = (
        "residence_place_id"
        if "residence_place_id" in participation_columns
        else "NULL AS residence_place_id"
    )
    for row in connection.execute(
        f"""SELECT event_id,occurrence_id,role,age_text,occupation_text,
                   residence_text,{residence_place_select},status_text,is_informant,
                   source_assertion_id
              FROM participations ORDER BY event_id,role,occurrence_id"""
    ):
        if row["occurrence_id"] in allowed_occurrence_ids:
            details = participant_details.get(
                (row["event_id"], row["occurrence_id"]), {}
            )
            participant = {
                "occurrenceId": row["occurrence_id"],
                "role": row["role"],
                "age": row["age_text"],
                "occupation": row["occupation_text"],
                "residence": row["residence_text"],
                "residencePlaceId": row["residence_place_id"],
                "statusText": row["status_text"],
                "vitalStatus": details.get("vitalStatus"),
                "informant": bool(row["is_informant"]),
            }
            if row["source_assertion_id"]:
                participant.update(
                    {
                        "sourceAssertionId": row["source_assertion_id"],
                        "provenance": "source_assertion",
                    }
                )
            if details.get("vitalStatusSourceAssertionId"):
                participant["vitalStatusSourceAssertionId"] = details[
                    "vitalStatusSourceAssertionId"
                ]
            if details.get("personId"):
                participant.update(
                    {
                        "personId": details["personId"],
                        "personStatus": details.get("personStatus"),
                        "identityStatus": details.get("identityStatus"),
                        "candidatePersonStatus": (
                            details.get("personStatus")
                            if details.get("personStatus") == "candidate"
                            else None
                        ),
                        "personIdentityResolved": details.get("identityStatus")
                        == "resolved",
                    }
                )
            participants[row["event_id"]].append(participant)
    raw_events: list[dict[str, Any]] = []
    for row in connection.execute(
        """SELECT id,source_item_id,event_type,date_text,date_from,date_to,
                  place_id,place_text,verification_status,notes
           FROM events ORDER BY COALESCE(date_from,date_text),id"""
    ):
        if (
            row["source_item_id"] and row["source_item_id"] not in allowed_source_ids
        ) or privacy.blocked("event", row["id"]) or row["verification_status"] in {
            "retracted",
            "superseded",
        }:
            continue
        curation = record_curation.get(row["id"])
        raw_events.append(
            {
                "id": row["id"],
                "sourceId": row["source_item_id"],
                "type": row["event_type"],
                "date": row["date_text"],
                "dateFrom": row["date_from"],
                "dateTo": row["date_to"],
                "placeId": row["place_id"],
                "place": row["place_text"],
                "status": row["verification_status"],
                "notes": row["notes"],
                "participants": participants.get(row["id"], []),
            }
        )
        if curation:
            raw_events[-1].update(
                {
                    "groupingCandidateId": curation["candidateId"],
                    "groupingStatus": curation["status"],
                    "groupingFingerprint": curation["fingerprint"],
                }
            )
        elif row["source_item_id"] in act_source_ids:
            raw_events[-1].update(
                {
                    "groupingStatus": "presentation_only_unregistered",
                    "groupingProposalKey": _event_grouping_proposal_key(
                        raw_events[-1]
                    ),
                }
            )
        raw_events[-1]["recordKey"] = _event_record_key(
            raw_events[-1], curation
        )

    event_buckets: list[list[dict[str, Any]]] = []
    for event in raw_events:
        matching = next(
            (
                bucket
                for bucket in event_buckets
                if bucket[0]["recordKey"] == event["recordKey"]
                and event.get("groupingStatus") == "resolved_same_record"
            ),
            None,
        )
        if matching is None or event["sourceId"] is None:
            event_buckets.append([event])
        else:
            matching.append(event)

    generic_roles = {
        "related-person",
        "mieszkaniec domu nr 5",
        "mieszkanka domu nr 5",
        "uczestnik",
        "participant",
    }

    def participant_quality(event: dict[str, Any]) -> tuple[int, int, int]:
        occurrence_ids = {part["occurrenceId"] for part in event["participants"]}
        rated = sum(
            occurrence_by_id.get(occurrence_id, {}).get("confidence") is not None
            for occurrence_id in occurrence_ids
        )
        specific = sum(
            part["role"].casefold() not in generic_roles
            for part in event["participants"]
        )
        return rated, specific, len(occurrence_ids)

    events: list[dict[str, Any]] = []
    for bucket in event_buckets:
        group = event_group(bucket[0]["type"])
        participant_event = max(bucket, key=participant_quality)
        if group == "census":
            metadata_event = max(
                bucket,
                key=lambda event: (
                    event["type"].casefold() in {"księga ludności", "spis ludności"},
                    bool(event["dateFrom"] and len(event["dateFrom"]) == 4),
                    participant_quality(event),
                ),
            )
        else:
            metadata_event = max(bucket, key=participant_quality)

        grouped_participants: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for part in participant_event["participants"]:
            grouped_participants[part["occurrenceId"]].append(part)
        parts_by_occurrence_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
        other_parts_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for event in bucket:
            for part in event["participants"]:
                parts_by_occurrence_id[part["occurrenceId"]].append(part)
                occurrence = occurrence_by_id.get(part["occurrenceId"], {})
                name_key = str(
                    occurrence.get("normalizedName")
                    or occurrence.get("searchName")
                    or occurrence.get("name")
                    or ""
                ).casefold()
                if name_key:
                    other_parts_by_name[name_key].append(part)

        merged_participants = []
        for occurrence_id, parts in grouped_participants.items():
            occurrence = occurrence_by_id.get(occurrence_id, {})
            name_key = str(
                occurrence.get("normalizedName")
                or occurrence.get("searchName")
                or occurrence.get("name")
                or ""
            ).casefold()
            related = parts + other_parts_by_name.get(name_key, [])
            exact = parts_by_occurrence_id[occurrence_id]

            def first_value(field: str) -> Any:
                return next((part[field] for part in related if part.get(field)), None)

            def first_explicit_occurrence_value(field: str) -> Any:
                return next(
                    (
                        part[field]
                        for part in exact
                        if part.get(field)
                    ),
                    None,
                )

            role = occurrence.get("role") or "; ".join(
                dict.fromkeys(part["role"] for part in parts)
            )
            merged = {
                "occurrenceId": occurrence_id,
                "role": role,
                "age": first_value("age"),
                "occupation": first_value("occupation"),
                "residence": first_value("residence"),
                "residencePlaceId": first_explicit_occurrence_value(
                    "residencePlaceId"
                ),
                "statusText": first_explicit_occurrence_value("statusText"),
                "vitalStatus": first_explicit_occurrence_value("vitalStatus"),
                "informant": any(part["informant"] for part in exact),
                "birth": occurrence.get("birth"),
            }
            source_assertion_id = first_explicit_occurrence_value(
                "sourceAssertionId"
            )
            if source_assertion_id:
                merged.update(
                    {
                        "sourceAssertionId": source_assertion_id,
                        "provenance": first_explicit_occurrence_value("provenance")
                        or "source_assertion",
                    }
                )
            vital_assertion_id = first_explicit_occurrence_value(
                "vitalStatusSourceAssertionId"
            )
            if vital_assertion_id:
                merged["vitalStatusSourceAssertionId"] = vital_assertion_id
            person_id = first_explicit_occurrence_value("personId")
            if person_id:
                identity_status = first_explicit_occurrence_value("identityStatus")
                merged.update(
                    {
                        "personId": person_id,
                        "personStatus": first_explicit_occurrence_value(
                            "personStatus"
                        ),
                        "identityStatus": identity_status,
                        "candidatePersonStatus": first_explicit_occurrence_value(
                            "candidatePersonStatus"
                        ),
                        "personIdentityResolved": identity_status == "resolved",
                    }
                )
            merged_participants.append(merged)
        event = dict(metadata_event)
        event["participants"] = merged_participants
        event["mergedEventIds"] = [item["id"] for item in bucket]
        event["originalTypes"] = unique_display_values([item["type"] for item in bucket])
        date_sources = [metadata_event] if group == "census" else bucket
        event["originalDates"] = unique_display_values([item["date"] for item in date_sources])
        event["originalPlaces"] = unique_display_values([item["place"] for item in bucket])
        events.append(event)
    return events
