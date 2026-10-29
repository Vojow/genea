from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ..workspace import research_root
from .privacy import PrivacyContext


_ENDPOINT_SCOPES = {"canonical", "presentation_hypothesis_group"}
_OPEN_HYPOTHESIS_STATUSES = {"open", "unresolved"}
_HIDDEN_STATUSES = {"masked", "masked_living", "private", "restricted"}


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _is_hidden(value: dict[str, Any]) -> bool:
    def hidden_status(raw: Any) -> bool:
        if isinstance(raw, dict):
            if raw.get("redacted") is True:
                return True
            raw = raw.get("status")
        return isinstance(raw, str) and raw.strip().casefold() in _HIDDEN_STATUSES

    return (
        value.get("sourceRedacted") is True
        or hidden_status(value.get("privacy"))
        or hidden_status(value.get("evidenceStatus"))
    )


def _manifest(root: Path) -> list[dict[str, Any]]:
    path = (
        research_root(root)
        / "presentation"
        / "conditional-parentage-hypotheses.json"
    )
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            "Manifest ustrukturyzowanego rodzicielstwa jest nieprawidlowy"
        ) from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schemaVersion") != 1
        or not isinstance(payload.get("hypotheses"), list)
        or not all(isinstance(item, dict) for item in payload["hypotheses"])
    ):
        raise ValueError(
            "Manifest ustrukturyzowanego rodzicielstwa ma nieobslugiwany schemat"
        )
    records = payload["hypotheses"]
    ids = [_text(item.get("id")) for item in records]
    if None in ids or len(ids) != len(set(ids)):
        raise ValueError(
            "Manifest ustrukturyzowanego rodzicielstwa wymaga unikalnych identyfikatorow"
        )
    return records


def load_structured_parentage(
    root: Path,
    connection: sqlite3.Connection,
    privacy: PrivacyContext,
    family_tree: dict[str, Any] | None,
    occurrences: list[dict[str, Any]],
    allowed_source_ids: set[str],
) -> list[dict[str, Any]]:
    """Load only exact, public and durably backed parentage hypotheses.

    The versioned manifest supplies endpoints; the database supplies the
    durable hypothesis rationale and evidence bindings.  No endpoint is
    inferred from names, prose, a person-context crosswalk or identity state.
    Rejected records are intentionally count-free and label-free.
    """

    records = _manifest(root)
    if not records:
        return []

    hypothesis_ids = sorted({_text(item.get("id")) for item in records})
    placeholders = ",".join("?" for _ in hypothesis_ids)
    rows = connection.execute(
        f"""
        SELECT h.id,h.status,h.rationale,
               he.source_id AS legacy_source_id,
               si.id AS source_item_id
          FROM hypotheses h
          LEFT JOIN hypothesis_evidence he ON he.hypothesis_id=h.id
          LEFT JOIN source_items si ON si.legacy_source_id=he.source_id
         WHERE h.id IN ({placeholders})
         ORDER BY h.id,si.id,he.source_id
        """,
        tuple(hypothesis_ids),
    ).fetchall()

    durable: dict[str, dict[str, Any]] = {}
    for row in rows:
        hypothesis_id = str(row["id"])
        item = durable.setdefault(
            hypothesis_id,
            {
                "status": row["status"],
                "rationale": row["rationale"],
                "sourceIds": set(),
            },
        )
        if row["source_item_id"]:
            item["sourceIds"].add(str(row["source_item_id"]))

    event_ids = sorted(
        {
            event_id.strip()
            for record in records
            for event_id in (
                record.get("backingEventIds")
                if isinstance(record.get("backingEventIds"), list)
                else []
            )
            if isinstance(event_id, str) and event_id.strip()
        }
    )
    event_bindings: dict[str, dict[str, Any]] = {}
    if event_ids:
        event_placeholders = ",".join("?" for _ in event_ids)
        event_rows = connection.execute(
            f"""
            SELECT e.id,e.source_item_id,p.occurrence_id
              FROM events e
              LEFT JOIN participations p ON p.event_id=e.id
             WHERE e.id IN ({event_placeholders})
             ORDER BY e.id,p.occurrence_id
            """,
            tuple(event_ids),
        ).fetchall()
        for row in event_rows:
            event_id = str(row["id"])
            binding = event_bindings.setdefault(
                event_id,
                {
                    "sourceId": row["source_item_id"],
                    "occurrenceIds": set(),
                },
            )
            if row["occurrence_id"]:
                binding["occurrenceIds"].add(str(row["occurrence_id"]))

    visible_children = {
        person["id"]: person
        for person in (family_tree or {}).get("people", [])
        if isinstance(person, dict)
        and _text(person.get("id")) is not None
        and not _is_hidden(person)
    }
    visible_occurrences = {
        occurrence["id"]: occurrence
        for occurrence in occurrences
        if isinstance(occurrence, dict)
        and _text(occurrence.get("id")) is not None
        and not _is_hidden(occurrence)
    }

    result: list[dict[str, Any]] = []
    for record in records:
        hypothesis_id = _text(record.get("id"))
        child_id = _text(record.get("childTreePersonId"))
        child_scope = _text(record.get("childEndpointScope"))
        parent_occurrence_id = _text(record.get("parentOccurrenceId"))
        basis = _text(record.get("basis"))
        raw_source_ids = record.get("sourceIds")
        raw_event_ids = record.get("backingEventIds")
        if (
            hypothesis_id is None
            or child_id is None
            or child_scope not in _ENDPOINT_SCOPES
            or parent_occurrence_id is None
            or basis is None
            or record.get("status") != "unresolved"
            or record.get("privacyReviewed") is not True
            or not isinstance(raw_source_ids, list)
            or not raw_source_ids
            or any(_text(value) is None for value in raw_source_ids)
            or not isinstance(raw_event_ids, list)
            or not raw_event_ids
            or any(_text(value) is None for value in raw_event_ids)
        ):
            continue
        source_ids = sorted(set(raw_source_ids))
        backing_event_ids = sorted(set(raw_event_ids))
        if (
            len(source_ids) != len(raw_source_ids)
            or len(backing_event_ids) != len(raw_event_ids)
        ):
            continue

        durable_record = durable.get(hypothesis_id)
        child = visible_children.get(child_id)
        parent = visible_occurrences.get(parent_occurrence_id)
        if (
            durable_record is None
            or durable_record["status"] not in _OPEN_HYPOTHESIS_STATUSES
            or _text(durable_record["rationale"]) != basis
            or child is None
            or parent is None
        ):
            continue

        parent_source_id = _text(parent.get("sourceId"))
        durable_source_ids = durable_record["sourceIds"]
        if (
            not set(source_ids).issubset(allowed_source_ids)
            or not set(source_ids).issubset(durable_source_ids)
            or parent_source_id is None
            or parent_source_id not in source_ids
        ):
            continue
        if any(
            event_id not in event_bindings
            or event_bindings[event_id]["sourceId"] not in allowed_source_ids
            or event_bindings[event_id]["sourceId"] not in source_ids
            or parent_occurrence_id not in event_bindings[event_id]["occurrenceIds"]
            for event_id in backing_event_ids
        ):
            continue
        if (
            privacy.blocked("hypothesis", hypothesis_id)
            or privacy.blocked_reference(hypothesis_id)
            or privacy.blocked("person", child_id)
            or privacy.blocked_reference(child_id)
            or privacy.blocked("person_occurrence", parent_occurrence_id)
            or privacy.blocked("occurrence", parent_occurrence_id)
            or privacy.blocked_reference(parent_occurrence_id)
            or any(
                privacy.blocked("source_item", source_id)
                or privacy.blocked_reference(source_id)
                for source_id in source_ids
            )
            or any(
                privacy.blocked("event", event_id)
                or privacy.blocked_reference(event_id)
                for event_id in backing_event_ids
            )
        ):
            continue

        result.append(
            {
                "id": hypothesis_id,
                "childTreePersonId": child_id,
                "childEndpointScope": child_scope,
                "parentOccurrenceId": parent_occurrence_id,
                "sourceIds": source_ids,
                "basis": basis,
                "status": "unresolved",
                "privacyReviewed": True,
                "provenance": {"kind": "structured_hypothesis"},
                "backingHypothesisIds": [hypothesis_id],
                "backingEventIds": backing_event_ids,
                "verificationStatus": "imported_unverified",
                "identityStatus": "unresolved",
            }
        )

    return sorted(result, key=lambda item: item["id"])
