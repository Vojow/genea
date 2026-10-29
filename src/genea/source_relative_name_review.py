"""Exact, opt-in source name bindings; reading review never proves identity.

The curator supplies the same-act actor mapping. Roles, names, notes and input
version membership cannot infer that mapping. This module reads provenance only.
"""
from __future__ import annotations

import json
from typing import Any

from .workspace import stable_id


class NameBindingError(ValueError):
    pass


def _object(value: Any) -> dict:
    try:
        value = json.loads(value) if isinstance(value, str) else value
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _exact_field(fields: Any, path: str) -> dict | None:
    """Read both persisted review shapes without guessing or choosing duplicates."""
    if isinstance(fields, dict):
        value = fields.get(path)
        return value if isinstance(value, dict) else None
    if isinstance(fields, list):
        matches = [item for item in fields
                   if isinstance(item, dict) and item.get("fieldPath") == path]
        if len(matches) > 1:
            raise NameBindingError("Review zawiera powtórzone dokładne pole nazwy")
        return matches[0] if matches else None
    return None


def normalize_name_bindings(value: Any) -> dict[str, dict[str, str]]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise NameBindingError("name_bindings musi być jawną mapą wystąpień")
    result = {}
    fields = set()
    for occurrence_id, binding in value.items():
        if not isinstance(occurrence_id, str) or not occurrence_id.strip():
            raise NameBindingError("Nieprawidłowy occurrence_id w name_bindings")
        if not isinstance(binding, dict) or set(binding) != {
            "field_reading_id", "reading_review_decision_id"
        }:
            raise NameBindingError("Wiązanie wymaga dokładnego pola i decyzji review")
        if any(not isinstance(item, str) or not item.strip() for item in binding.values()):
            raise NameBindingError("Wiązanie wymaga niepustych identyfikatorów")
        if binding["field_reading_id"] in fields:
            raise NameBindingError("Jedno pole nazwy nie może wiązać wielu wystąpień")
        fields.add(binding["field_reading_id"])
        result[occurrence_id] = dict(binding)
    return result


def _validate_metadata(value: dict, source_id: str, occurrence_id: str, field_id: str,
                       raw_source_ids: set[str]) -> None:
    """Check typed backing only; free text is never interpreted as an actor ID."""
    for key, item in value.items():
        if key in {"sourceItemId", "source_item_id"} and item != source_id:
            raise NameBindingError("Pole nazwy ma inne źródło w metadanych")
        if key == "sourceId" and item not in {source_id, *raw_source_ids}:
            raise NameBindingError("Pole nazwy ma inne źródło RAW w metadanych")
        if key in {"occurrenceId", "occurrence_id"} and item != occurrence_id:
            raise NameBindingError("Pole nazwy wskazuje inne wystąpienie")
        if key in {"fieldReadingId", "field_reading_id"} and item != field_id:
            raise NameBindingError("Review wskazuje inne pole nazwy")
        if key == "backingId" and (not isinstance(item, str) or item not in {source_id, occurrence_id, field_id}):
            raise NameBindingError("Niepasujący backingId pola nazwy")
        if key in {"sourceItemIds", "occurrenceIds", "fieldReadingIds"}:
            expected = {"sourceItemIds": source_id, "occurrenceIds": occurrence_id,
                        "fieldReadingIds": field_id}[key]
            if not isinstance(item, list) or item != [expected]:
                raise NameBindingError("Niepasujące backing IDs pola nazwy")
        if isinstance(item, dict) and key in {"semanticBounds", "backingIds", "metadata"}:
            _validate_metadata(item, source_id, occurrence_id, field_id, raw_source_ids)


def resolve_name_bindings(connection, source_id: str, bindings: dict, occurrences: list[dict]) -> dict:
    occurrences_by_id = {item["id"]: item for item in occurrences}
    result = {}
    for occurrence_id, binding in sorted(bindings.items()):
        if occurrence_id not in occurrences_by_id:
            raise NameBindingError("Wiązanie nazwy dotyczy wystąpienia spoza źródła")
        field_id = binding["field_reading_id"]
        field_row = connection.execute(
            """SELECT f.*,rv.reading_id,rv.payload_json,rv.status AS version_status,
                      r.source_item_id,r.metadata_json AS reading_metadata_json
                 FROM field_readings f JOIN reading_versions rv ON rv.id=f.reading_version_id
                 JOIN readings r ON r.id=rv.reading_id WHERE f.id=?""", (field_id,)
        ).fetchone()
        review_row = connection.execute(
            "SELECT * FROM reading_review_decisions WHERE id=?",
            (binding["reading_review_decision_id"],),
        ).fetchone()
        if field_row is None or review_row is None:
            raise NameBindingError("Nie znaleziono dokładnego pola lub decyzji review")
        field, review = dict(field_row), dict(review_row)
        if field["source_item_id"] != source_id or review["source_item_id"] != source_id:
            raise NameBindingError("Odczyt i review muszą należeć do źródła wystąpienia")
        latest = connection.execute(
            "SELECT id FROM reading_review_decisions WHERE source_item_id=? ORDER BY revision DESC LIMIT 1",
            (source_id,),
        ).fetchone()
        if latest["id"] != review["id"]:
            raise NameBindingError("Wiązanie wymaga bieżącej decyzji review")
        path = field["field_name"]
        decision = _object(review["decision_json"])
        reviewed_field = _exact_field(decision.get("fields"), path)
        if not isinstance(path, str) or not path.endswith(".name") or len(path) <= 5 or not isinstance(reviewed_field, dict):
            raise NameBindingError("Wiązanie wymaga dokładnego nazwanego pola .name w review")
        if reviewed_field.get("fieldPath", path) != path:
            raise NameBindingError("Review ma inny fieldPath")
        literal = reviewed_field.get("literal")
        if not isinstance(literal, str) or not literal.strip() or field["value_text"] != literal:
            raise NameBindingError("Literal pola musi dokładnie odpowiadać literalowi review")
        inputs = [dict(row) for row in connection.execute(
            "SELECT * FROM reading_review_inputs WHERE decision_id=? ORDER BY id", (review["id"],)
        )]
        explicit_field = reviewed_field.get("fieldReadingId") or reviewed_field.get("field_reading_id")
        if not explicit_field and reviewed_field.get("backingId") == field_id:
            explicit_field = field_id
        extraction_pointer = decision.get("extractionReadingVersionId")
        exact_version = extraction_pointer == field["reading_version_id"]
        if extraction_pointer and not exact_version:
            raise NameBindingError("Review wskazuje inną dokładną wersję ekstrakcji")
        if not exact_version and not (
            explicit_field == field_id
            and any(item["reading_version_id"] == field["reading_version_id"] for item in inputs)
        ):
            raise NameBindingError("Samo członkostwo wersji w wejściach review nie wiąże pola nazwy")
        if not explicit_field:
            count = connection.execute(
                "SELECT COUNT(*) FROM field_readings WHERE reading_version_id=? AND field_name=?",
                (field["reading_version_id"], path),
            ).fetchone()[0]
            if count != 1:
                raise NameBindingError("Niejednoznaczne dokładne pole w wersji ekstrakcji")
        notes = _object(field["notes"])
        payload_field = _exact_field(_object(field["payload_json"]).get("fields"), path)
        # sourceId in older field metadata denotes RAW, not a source_item ID.
        # Accept it only through the exact reading artifact's persisted linkage.
        raw_sources = [dict(row) for row in connection.execute(
            """SELECT s.id,s.archive_path,s.sha256,a.id AS artifact_id,a.path AS artifact_path
                 FROM readings r JOIN artifacts a ON a.id=r.artifact_id
                 JOIN sources s ON s.id=a.legacy_source_id OR s.archive_path=a.path
                WHERE r.id=? AND a.source_item_id=?""", (field["reading_id"], source_id)
        )]
        for metadata in (reviewed_field, notes, _object(payload_field)):
            _validate_metadata(metadata, source_id, occurrence_id, field_id,
                               {item["id"] for item in raw_sources})
        segment = None
        if field["transcript_segment_id"]:
            segment_row = connection.execute(
                "SELECT * FROM transcript_segments WHERE id=?", (field["transcript_segment_id"],)
            ).fetchone()
            if segment_row is None or segment_row["reading_version_id"] != field["reading_version_id"]:
                raise NameBindingError("Segment pola nie należy do dokładnego odczytu")
            segment = dict(segment_row)
        try:
            alternatives = json.loads(field["alternatives_json"])
        except (ValueError, TypeError):
            raise NameBindingError("Nieprawidłowe alternatywy pola nazwy") from None
        evidence = {
            "sourceItemId": source_id, "occurrenceId": occurrence_id,
            "fieldReadingId": field_id, "readingReviewDecisionId": review["id"],
            "readingVersionId": field["reading_version_id"], "readingId": field["reading_id"],
            "transcriptSegmentId": field["transcript_segment_id"], "fieldPath": path,
            "literal": literal, "acceptedValue": reviewed_field.get("acceptedValue", reviewed_field.get("value")),
            "reviewStatus": reviewed_field.get("reviewStatus") or review["status"],
            "decisionStatus": review["status"], "readingCertainty": field["reading_certainty"],
            "reviewReadingCertainty": reviewed_field.get("readingCertainty"),
            "alternatives": reviewed_field.get("alternatives", alternatives),
            "fieldAlternatives": alternatives, "reviewedField": reviewed_field,
            "identityStatus": "unresolved", "bindingBasis": "explicit_curator_same_act_actor",
            "reviewProvesIdentity": False,
            "backingIds": {
                "sourceItemIds": [source_id], "occurrenceIds": [occurrence_id],
                "fieldReadingIds": [field_id], "readingReviewDecisionIds": [review["id"]],
                "readingVersionIds": [field["reading_version_id"]], "readingIds": [field["reading_id"]],
                "transcriptSegmentIds": [field["transcript_segment_id"]] if field["transcript_segment_id"] else [],
            },
        }
        assertion_id = stable_id("assertion", "source-profile-name-view", source_id, occurrence_id, field_id, review["id"])
        evidence["sourceAssertionId"] = assertion_id
        evidence["backingIds"]["sourceAssertionIds"] = [assertion_id]
        assertion = {
            "id": assertion_id, "source_item_id": source_id,
            "reading_version_id": field["reading_version_id"],
            "transcript_segment_id": field["transcript_segment_id"],
            "assertion_type": "source-profile-name-view", "subject_ref": occurrence_id,
            "predicate": "qualified-source-name-view", "object_text": literal,
            "information_quality": "Indeterminable", "reading_certainty": field["reading_certainty"],
            "verification_status": evidence["reviewStatus"],
            "metadata_json": json.dumps(evidence, ensure_ascii=False, sort_keys=True),
        }
        current = connection.execute("SELECT * FROM source_assertions WHERE id=?", (assertion_id,)).fetchone()
        if current and any(current[key] != value for key, value in assertion.items()):
            raise NameBindingError("Kolizja istniejącej asercji widoku nazwy")
        result[occurrence_id] = {
            "evidence": evidence, "assertion": assertion, "assertionExists": current is not None,
            "snapshot": {"field": field, "review": review, "inputs": inputs, "segment": segment,
                         "rawSourceBindings": raw_sources,
                         "existingAssertion": dict(current) if current else None},
        }
    return result
