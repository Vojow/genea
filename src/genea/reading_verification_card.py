from __future__ import annotations

from collections import defaultdict
from typing import Any, Literal, TypedDict

from .workspace import search_fold


CARD_SCHEMA = "genea.reading-verification-card/v1"

ComparisonStatus = Literal["agreed", "disputed", "incomplete", "not_compared"]


class ReadingVerificationCardError(ValueError):
    """Raised when a verification card cannot be projected safely."""


class VerificationCardSummary(TypedDict):
    fieldCount: int
    agreedCount: int
    disputedCount: int
    incompleteCount: int
    notComparedCount: int
    humanReviewRequiredCount: int


class ReadingVerificationCard(TypedDict):
    schema: str
    summary: VerificationCardSummary
    fields: list[dict[str, Any]]
    issues: list[dict[str, Any]]


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _field_path(value: dict[str, Any]) -> str | None:
    return _text(value.get("path") or value.get("fieldPath") or value.get("field"))


def _comparison_value(value: Any) -> str | None:
    text = _text(value)
    return search_fold(text) if text else None


def _blind_values(
    path: str, independent_readings: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for reading in independent_readings:
        field = next(
            (item for item in _list_of_dicts(reading.get("fields")) if item.get("path") == path),
            None,
        )
        if field is None:
            values.append(
                {
                    "readingId": _text(reading.get("id")),
                    "readerId": _text(reading.get("readerId")),
                    "value": None,
                }
            )
            continue
        values.append(
            {
                "readingId": _text(reading.get("id")),
                "readerId": _text(reading.get("readerId")),
                "value": _text(field.get("normalizedValue")) or _text(field.get("value")),
            }
        )
    return values


def _comparison_for(
    path: str,
    independent_readings: list[dict[str, Any]],
    comparison_by_path: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    stored = comparison_by_path.get(path)
    if stored is not None:
        stored_status = stored.get("status")
        difference_kind = _text(stored.get("differenceKind"))
        stored_values = _list_of_dicts(stored.get("values"))
        stored_value_ids = [_text(value.get("readingId")) for value in stored_values]
        stored_value_texts = [_comparison_value(value.get("value")) for value in stored_values]
        blind_ids = {_text(reading.get("id")) for reading in independent_readings}
        blind_ids.discard(None)
        stored_value_map = {
            reading_id: value
            for reading_id, value in zip(stored_value_ids, stored_value_texts, strict=True)
            if reading_id and value
        }
        blind_value_map = {
            _text(value.get("readingId")): _comparison_value(value.get("value"))
            for value in _blind_values(path, independent_readings)
            if _text(value.get("readingId")) and _comparison_value(value.get("value"))
        }
        agreement_is_grounded = (
            len(independent_readings) >= 2
            and len(stored_values) == len(independent_readings)
            and all(stored_value_ids)
            and len(set(stored_value_ids)) == len(stored_value_ids)
            and set(stored_value_ids) == blind_ids
            and all(stored_value_texts)
            and len(set(stored_value_texts)) == 1
            and stored_value_map == blind_value_map
        )
        if stored_status == "agreement" and agreement_is_grounded:
            status: ComparisonStatus = "agreed"
        elif stored_status == "agreement":
            has_conflicting_values = (
                len({value for value in stored_value_texts if value}) > 1
                or (
                    len(stored_value_map) >= 2
                    and len(blind_value_map) >= 2
                    and stored_value_map != blind_value_map
                )
            )
            status = "disputed" if has_conflicting_values else "incomplete"
            difference_kind = (
                "different_value" if has_conflicting_values else "missing_in_some"
            )
        elif stored_status == "disagreement" and difference_kind == "missing_in_some":
            status = "incomplete"
        elif stored_status == "disagreement":
            status = "disputed"
        else:
            status = "not_compared"
        result: dict[str, Any] = {
            "status": status,
            "values": stored_values,
        }
        if difference_kind:
            result["differenceKind"] = difference_kind
        return result

    values = _blind_values(path, independent_readings)
    present = [_comparison_value(item.get("value")) for item in values]
    present = [value for value in present if value]
    if not present:
        status = "not_compared"
        difference_kind = None
    elif len(independent_readings) < 2 or len(present) < len(independent_readings):
        status = "incomplete"
        difference_kind = "missing_in_some"
    elif len(set(present)) == 1:
        status = "agreed"
        difference_kind = "agreement"
    else:
        status = "disputed"
        difference_kind = "different_value"
    result = {"status": status, "values": values}
    if difference_kind:
        result["differenceKind"] = difference_kind
    return result


def _blind_fields_for(
    path: str, independent_readings: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for reading in independent_readings:
        for field in _list_of_dicts(reading.get("fields")):
            if field.get("path") != path:
                continue
            item: dict[str, Any] = {
                "readingId": _text(reading.get("id")),
                "readerId": _text(reading.get("readerId")),
                "value": _text(field.get("value")),
            }
            optional = {
                "normalizedValue": _text(field.get("normalizedValue")),
                "certainty": _text(field.get("certainty")),
                "contextDisclosed": reading.get("contextDisclosed")
                if isinstance(reading.get("contextDisclosed"), bool)
                else None,
            }
            item.update({key: value for key, value in optional.items() if value is not None})
            result.append(item)
    return result


def _review_fields(review_overlay: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    overlay = _dict(review_overlay)
    fields: dict[str, dict[str, Any]] = {}
    for field in _list_of_dicts(overlay.get("fields")):
        path = _field_path(field)
        if path:
            fields[path] = field
    return fields


def _project_issue(value: dict[str, Any]) -> dict[str, Any]:
    reason_code = _text(value.get("reasonCode"))
    if reason_code is None:
        raise ReadingVerificationCardError("Każdy issue musi mieć reasonCode")
    issue: dict[str, Any] = {
        "reasonCode": reason_code,
        "details": _dict(value.get("details")),
    }
    optional_text = {
        "id": _text(value.get("id")),
        "fieldPath": _text(value.get("fieldPath")),
        "recommendedRole": _text(value.get("recommendedRole")),
    }
    issue.update({key: item for key, item in optional_text.items() if item is not None})
    priority = value.get("priority")
    if isinstance(priority, int) and not isinstance(priority, bool):
        issue["priority"] = priority
    return issue


def _workflow_for(
    field: dict[str, Any], review_overlay: dict[str, Any] | None
) -> dict[str, Any]:
    overlay = _dict(review_overlay)
    review_field = _review_fields(overlay).get(str(field["path"]))
    if review_field is not None:
        requested = _text(review_field.get("status"))
        # Human completion is deliberately accepted only from a real, explicit
        # human decision.  A matching pair of blind readings never promotes it.
        valid_human_review = (
            requested == "human_reviewed"
            and overlay.get("status") == "human_reviewed"
            and overlay.get("decidedByKind") == "human"
        )
        allowed_agent = requested in {"agent_consensus", "agent_adjudicated"}
        allowed_hold = requested == "human_review_required"
        if valid_human_review or allowed_agent or allowed_hold:
            workflow: dict[str, Any] = {
                "status": requested,
                "basis": "review_decision",
            }
            decision_id = _text(overlay.get("id"))
            if decision_id:
                workflow["decisionId"] = decision_id
            revision = overlay.get("revision")
            if isinstance(revision, int) and not isinstance(revision, bool):
                workflow["revision"] = revision
            return workflow

    base_status = _text(field.get("reviewStatus")) or "machine_proposal"
    if base_status not in {"machine_proposal", "human_review_required", "missing"}:
        base_status = "machine_proposal"
    return {"status": base_status, "basis": "pipeline_assessment"}


def _proposal(field: dict[str, Any]) -> dict[str, Any]:
    return {
        "value": _text(field.get("value")),
        "normalizedValue": _text(field.get("normalizedValue")),
        "confidence": field.get("confidence")
        if isinstance(field.get("confidence"), (int, float))
        and not isinstance(field.get("confidence"), bool)
        else None,
        "certainty": _text(field.get("certainty")),
        "alternatives": [str(item) for item in field.get("alternatives", [])]
        if isinstance(field.get("alternatives"), list)
        else [],
        "materialAlternatives": [str(item) for item in field.get("materialAlternatives", [])]
        if isinstance(field.get("materialAlternatives"), list)
        else [],
        "sourceQuote": _text(field.get("sourceQuote")),
    }


def project_reading_verification_card(
    *,
    source_item_id: str,
    artifact_id: str,
    fields: list[dict[str, Any]],
    independent_readings: list[dict[str, Any]] | None = None,
    comparison: dict[str, Any] | None = None,
    issues: list[dict[str, Any]] | None = None,
    review_overlay: dict[str, Any] | None = None,
) -> ReadingVerificationCard:
    """Project a complete, presentation-only verification card.

    The function is deliberately pure.  It receives already loaded values and
    never opens SQLite, RAW material or package paths.  Comparison and review
    are separate axes: textual agreement cannot create a review decision.
    """

    source_id = _text(source_item_id)
    artifact = _text(artifact_id)
    if source_id is None or artifact is None:
        raise ReadingVerificationCardError("source_item_id i artifact_id są wymagane")

    main_by_path: dict[str, dict[str, Any]] = {}
    for raw_field in fields:
        field = _dict(raw_field)
        path = _field_path(field)
        if path is None:
            raise ReadingVerificationCardError("Każde pole główne musi mieć path")
        if path in main_by_path:
            raise ReadingVerificationCardError(f"Powtórzona ścieżka pola głównego: {path}")
        field["path"] = path
        main_by_path[path] = field

    blinds = _list_of_dicts(independent_readings or [])
    comparison_fields = _list_of_dicts(_dict(comparison).get("fields"))
    comparison_by_path = {
        path: item
        for item in comparison_fields
        if (path := _field_path(item)) is not None
    }
    all_issues = [_project_issue(issue) for issue in _list_of_dicts(issues or [])]
    issues_by_path: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unassigned_issues: list[dict[str, Any]] = []
    for issue in all_issues:
        path = _field_path(issue)
        if path in main_by_path:
            issues_by_path[str(path)].append(issue)
        else:
            unassigned_issues.append(issue)

    projected_fields: list[dict[str, Any]] = []
    for path in sorted(main_by_path):
        field = main_by_path[path]
        bbox = _dict(field.get("bbox"))
        backing: dict[str, Any] = {
            "sourceItemId": source_id,
            "artifactId": artifact,
        }
        source_segment_id = _text(field.get("sourceSegmentId"))
        if source_segment_id:
            backing["sourceSegmentId"] = source_segment_id
        if bbox:
            backing["bbox"] = bbox
        projected_fields.append(
            {
                "path": path,
                "role": _text(field.get("role")),
                "criticality": _text(field.get("criticality")),
                "proposal": _proposal(field),
                "blindReadings": _blind_fields_for(path, blinds),
                "comparison": _comparison_for(path, blinds, comparison_by_path),
                "workflow": _workflow_for(field, review_overlay),
                "issues": issues_by_path.get(path, []),
                "backing": backing,
            }
        )

    counts = defaultdict(int)
    human_review_required_count = 0
    for field in projected_fields:
        counts[field["comparison"]["status"]] += 1
        if field["workflow"]["status"] == "human_review_required":
            human_review_required_count += 1
    summary: VerificationCardSummary = {
        "fieldCount": len(projected_fields),
        "agreedCount": counts["agreed"],
        "disputedCount": counts["disputed"],
        "incompleteCount": counts["incomplete"],
        "notComparedCount": counts["not_compared"],
        "humanReviewRequiredCount": human_review_required_count,
    }
    return {
        "schema": CARD_SCHEMA,
        "summary": summary,
        "fields": projected_fields,
        # Issues that target a document/layer/expected role are preserved here
        # instead of being falsely attached to a reading field.
        "issues": unassigned_issues,
    }
