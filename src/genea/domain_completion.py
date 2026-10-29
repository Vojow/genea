from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable


FULL_ACT = "full_act"
NO_DOMAIN_WRITEBACK_EXPECTED = "no_domain_writeback_expected"
DOMAIN_COMPLETION_MODES = {FULL_ACT, NO_DOMAIN_WRITEBACK_EXPECTED}
NO_WRITEBACK_SCOPE_KINDS = {
    "geometry_only",
    "name_only",
    "proposal_only",
    "source_structure_only",
}

_REQUIRED_PERSON_FIELDS = (
    "personKey",
    "fieldPath",
    "fieldReadingId",
    "name",
    "role",
    "transcriptSegmentId",
    "eventId",
    "occurrenceId",
    "participationId",
    "sourceAssertionId",
)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def normalize_domain_completion(
    value: Any,
    *,
    label: str = "domainCompletion",
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"Pole {label} musi być obiektem")
    mode = value.get("mode")
    if mode not in DOMAIN_COMPLETION_MODES:
        raise ValueError(
            f"Pole {label}.mode musi być full_act albo no_domain_writeback_expected"
        )
    rationale = value.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        raise ValueError(f"Pole {label}.rationale musi być niepustym tekstem")
    if mode == NO_DOMAIN_WRITEBACK_EXPECTED:
        scope_kind = value.get("scopeKind")
        if scope_kind not in NO_WRITEBACK_SCOPE_KINDS:
            raise ValueError(
                f"Pole {label}.scopeKind musi jawnie opisywać ograniczony zakres: "
                + ", ".join(sorted(NO_WRITEBACK_SCOPE_KINDS))
            )
        if value.get("acceptedNamedPeople") not in (None, []):
            raise ValueError(
                f"Pole {label}.acceptedNamedPeople musi być puste dla "
                "no_domain_writeback_expected"
            )
        return {
            "mode": mode,
            "scopeKind": scope_kind,
            "rationale": rationale.strip(),
        }

    accepted = value.get("acceptedNamedPeople")
    if not isinstance(accepted, list) or not accepted:
        raise ValueError(
            f"Pole {label}.acceptedNamedPeople musi być niepustą listą dla full_act"
        )
    normalized_people: list[dict[str, str]] = []
    seen_keys: set[str] = set()
    seen_role_bindings: set[tuple[str, str]] = set()
    seen_participations: set[str] = set()
    for index, item in enumerate(accepted):
        item_label = f"{label}.acceptedNamedPeople[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"Pole {item_label} musi być obiektem")
        unknown = set(item) - set(_REQUIRED_PERSON_FIELDS)
        if unknown:
            raise ValueError(
                f"Pole {item_label} zawiera niedozwolone klucze: "
                + ", ".join(sorted(unknown))
            )
        normalized: dict[str, str] = {}
        for field in _REQUIRED_PERSON_FIELDS:
            field_value = item.get(field)
            if not isinstance(field_value, str) or not field_value.strip():
                raise ValueError(f"Pole {item_label}.{field} musi być niepustym tekstem")
            normalized[field] = field_value.strip()
        if normalized["personKey"] in seen_keys:
            raise ValueError(
                f"Powtórzony personKey w {label}: {normalized['personKey']}"
            )
        role_binding = (normalized["occurrenceId"], normalized["role"])
        if role_binding in seen_role_bindings:
            raise ValueError(
                f"Powtórzone occurrenceId/role w {label}: "
                f"{normalized['occurrenceId']}/{normalized['role']}"
            )
        if normalized["participationId"] in seen_participations:
            raise ValueError(
                f"Powtórzony participationId w {label}: "
                f"{normalized['participationId']}"
            )
        seen_keys.add(normalized["personKey"])
        seen_role_bindings.add(role_binding)
        seen_participations.add(normalized["participationId"])
        normalized_people.append(normalized)
    normalized_people.sort(key=lambda item: item["personKey"])
    accepted_fingerprint = fingerprint(normalized_people)
    declared = value.get("acceptedNamedPeopleFingerprint")
    if declared is not None and declared != accepted_fingerprint:
        raise ValueError(
            f"Pole {label}.acceptedNamedPeopleFingerprint nie odpowiada frozen liście"
        )
    return {
        "mode": mode,
        "rationale": rationale.strip(),
        "acceptedNamedPeople": normalized_people,
        "acceptedNamedPeopleFingerprint": accepted_fingerprint,
    }


def overlay_from_changes(changes: Iterable[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    overlay: dict[tuple[str, str], dict[str, Any]] = {}
    for change in changes:
        if change.get("action") not in {"create", "update", "no_op"}:
            continue
        table = change.get("table")
        object_id = change.get("id")
        target = change.get("target")
        if isinstance(table, str) and isinstance(object_id, str) and isinstance(target, dict):
            overlay[(table, object_id)] = dict(target)
    return overlay


def _row(
    connection: Any,
    overlay: dict[tuple[str, str], dict[str, Any]],
    table: str,
    object_id: str,
) -> dict[str, Any] | None:
    current_row = connection.execute(
        f"SELECT * FROM {table} WHERE id=?", (object_id,)
    ).fetchone()
    current = dict(current_row) if current_row else None
    planned = overlay.get((table, object_id))
    if planned is None:
        return current
    merged = dict(current or {})
    merged.update(planned)
    return merged


def _source_lineage(connection: Any, source_item_id: str) -> set[str]:
    lineage: set[str] = set()
    current_id: str | None = source_item_id
    while current_id is not None and current_id not in lineage:
        lineage.add(current_id)
        row = connection.execute(
            "SELECT parent_id FROM source_items WHERE id=?", (current_id,)
        ).fetchone()
        current_id = str(row[0]) if row and row[0] is not None else None
    return lineage


def _reading_source(connection: Any, reading_version_id: str | None) -> str | None:
    if not reading_version_id:
        return None
    row = connection.execute(
        """SELECT r.source_item_id
             FROM reading_versions rv
             JOIN readings r ON r.id=rv.reading_id
            WHERE rv.id=?""",
        (reading_version_id,),
    ).fetchone()
    return str(row[0]) if row else None


def assess_domain_completion(
    connection: Any,
    *,
    source_item_id: str,
    declaration: dict[str, Any],
    overlay: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    normalized = normalize_domain_completion(declaration)
    if normalized["mode"] == NO_DOMAIN_WRITEBACK_EXPECTED:
        return {
            "state": NO_DOMAIN_WRITEBACK_EXPECTED,
            "mode": normalized["mode"],
            "scopeKind": normalized["scopeKind"],
            "sourceItemId": source_item_id,
            "acceptedNamedPeopleFingerprint": None,
            "expectedCount": 0,
            "completeCount": 0,
            "gaps": [],
        }

    planned = overlay or {}
    compatible_sources = _source_lineage(connection, source_item_id)
    gaps: list[dict[str, Any]] = []
    complete_count = 0
    for expected in normalized["acceptedNamedPeople"]:
        issues: list[dict[str, Any]] = []

        def issue(layer: str, code: str, object_id: str, **details: Any) -> None:
            issues.append(
                {
                    "layer": layer,
                    "code": code,
                    "objectId": object_id,
                    **details,
                }
            )

        event = _row(connection, planned, "events", expected["eventId"])
        if event is None:
            issue("event", "missing_event", expected["eventId"])
        elif event.get("source_item_id") != source_item_id:
            issue(
                "event",
                "event_source_mismatch",
                expected["eventId"],
                expectedSourceItemId=source_item_id,
                actualSourceItemId=event.get("source_item_id"),
            )

        occurrence = _row(
            connection, planned, "person_occurrences", expected["occurrenceId"]
        )
        if occurrence is None:
            issue("occurrence", "missing_occurrence", expected["occurrenceId"])
        else:
            if occurrence.get("source_item_id") != source_item_id:
                issue(
                    "occurrence",
                    "occurrence_source_mismatch",
                    expected["occurrenceId"],
                    expectedSourceItemId=source_item_id,
                    actualSourceItemId=occurrence.get("source_item_id"),
                )
            names = {occurrence.get("raw_name"), occurrence.get("normalized_name")}
            if expected["name"] not in names:
                issue(
                    "occurrence",
                    "occurrence_name_mismatch",
                    expected["occurrenceId"],
                    expectedName=expected["name"],
                    actualRawName=occurrence.get("raw_name"),
                    actualNormalizedName=occurrence.get("normalized_name"),
                )
            if occurrence.get("role_text") != expected["role"]:
                issue(
                    "occurrence",
                    "occurrence_role_mismatch",
                    expected["occurrenceId"],
                    expectedRole=expected["role"],
                    actualRole=occurrence.get("role_text"),
                )

        participation = _row(
            connection, planned, "participations", expected["participationId"]
        )
        if participation is None:
            issue(
                "participation",
                "missing_participation",
                expected["participationId"],
            )
        else:
            for field, expected_value, code in (
                ("event_id", expected["eventId"], "participation_event_mismatch"),
                (
                    "occurrence_id",
                    expected["occurrenceId"],
                    "participation_occurrence_mismatch",
                ),
                ("role", expected["role"], "participation_role_mismatch"),
                (
                    "source_assertion_id",
                    expected["sourceAssertionId"],
                    "participation_assertion_mismatch",
                ),
            ):
                if participation.get(field) != expected_value:
                    issue(
                        "participation",
                        code,
                        expected["participationId"],
                        expectedValue=expected_value,
                        actualValue=participation.get(field),
                    )

        segment = _row(
            connection, planned, "transcript_segments", expected["transcriptSegmentId"]
        )
        if segment is None:
            issue(
                "provenance",
                "missing_transcript_segment",
                expected["transcriptSegmentId"],
            )
        else:
            segment_source = _reading_source(connection, segment.get("reading_version_id"))
            if segment_source not in compatible_sources:
                issue(
                    "provenance",
                    "segment_source_mismatch",
                    expected["transcriptSegmentId"],
                    actualSourceItemId=segment_source,
                )

        field = _row(connection, planned, "field_readings", expected["fieldReadingId"])
        if field is None:
            issue("provenance", "missing_field_reading", expected["fieldReadingId"])
        else:
            if field.get("field_name") != expected["fieldPath"]:
                issue(
                    "provenance",
                    "field_path_mismatch",
                    expected["fieldReadingId"],
                    expectedFieldPath=expected["fieldPath"],
                    actualFieldPath=field.get("field_name"),
                )
            if field.get("value_text") != expected["name"]:
                issue(
                    "provenance",
                    "field_value_mismatch",
                    expected["fieldReadingId"],
                    expectedName=expected["name"],
                    actualValue=field.get("value_text"),
                )
            if field.get("transcript_segment_id") != expected["transcriptSegmentId"]:
                issue(
                    "provenance",
                    "field_segment_mismatch",
                    expected["fieldReadingId"],
                    expectedTranscriptSegmentId=expected["transcriptSegmentId"],
                    actualTranscriptSegmentId=field.get("transcript_segment_id"),
                )

        if occurrence is not None:
            if occurrence.get("transcript_segment_id") != expected["transcriptSegmentId"]:
                issue(
                    "provenance",
                    "occurrence_segment_mismatch",
                    expected["occurrenceId"],
                    expectedTranscriptSegmentId=expected["transcriptSegmentId"],
                    actualTranscriptSegmentId=occurrence.get("transcript_segment_id"),
                )
            if segment is not None and occurrence.get("reading_version_id") != segment.get(
                "reading_version_id"
            ):
                issue(
                    "provenance",
                    "occurrence_reading_mismatch",
                    expected["occurrenceId"],
                )

        assertion = _row(
            connection, planned, "source_assertions", expected["sourceAssertionId"]
        )
        if assertion is None:
            issue(
                "provenance",
                "missing_source_assertion",
                expected["sourceAssertionId"],
            )
        else:
            for field_name, expected_value, code in (
                ("source_item_id", source_item_id, "assertion_source_mismatch"),
                (
                    "transcript_segment_id",
                    expected["transcriptSegmentId"],
                    "assertion_segment_mismatch",
                ),
                ("subject_ref", expected["occurrenceId"], "assertion_subject_mismatch"),
            ):
                if assertion.get(field_name) != expected_value:
                    issue(
                        "provenance",
                        code,
                        expected["sourceAssertionId"],
                        expectedValue=expected_value,
                        actualValue=assertion.get(field_name),
                    )
            if segment is not None and assertion.get("reading_version_id") != segment.get(
                "reading_version_id"
            ):
                issue(
                    "provenance",
                    "assertion_reading_mismatch",
                    expected["sourceAssertionId"],
                )

        if issues:
            gaps.append(
                {
                    "personKey": expected["personKey"],
                    "fieldPath": expected["fieldPath"],
                    "name": expected["name"],
                    "role": expected["role"],
                    "missingLayers": sorted({item["layer"] for item in issues}),
                    "issues": issues,
                }
            )
        else:
            complete_count += 1

    return {
        "state": "complete" if not gaps else "incomplete",
        "mode": FULL_ACT,
        "sourceItemId": source_item_id,
        "acceptedNamedPeopleFingerprint": normalized[
            "acceptedNamedPeopleFingerprint"
        ],
        "expectedCount": len(normalized["acceptedNamedPeople"]),
        "completeCount": complete_count,
        "gaps": gaps,
    }


def gap_conflicts(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "kind": "domain_completion",
            "id": gap["personKey"],
            "reason": "domain_completion_gap",
            "fieldPath": gap["fieldPath"],
            "name": gap["name"],
            "role": gap["role"],
            "missingLayers": gap["missingLayers"],
            "issues": gap["issues"],
        }
        for gap in report.get("gaps", [])
    ]


__all__ = [
    "FULL_ACT",
    "NO_DOMAIN_WRITEBACK_EXPECTED",
    "assess_domain_completion",
    "fingerprint",
    "gap_conflicts",
    "normalize_domain_completion",
    "overlay_from_changes",
]
