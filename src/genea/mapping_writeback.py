from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from .db import connect, initialize
from .domain_completion import (
    FULL_ACT,
    assess_domain_completion,
    gap_conflicts,
    overlay_from_changes,
)
from .research import require_tables
from .workspace import stable_id, utc_now


REQUIRED_TABLES = {
    "addresses",
    "artifacts",
    "audit_log",
    "citations",
    "document_extraction_states",
    "events",
    "field_readings",
    "household_memberships",
    "households",
    "identity_candidates",
    "identity_decisions",
    "local_occurrences",
    "mapping_writeback_manifests",
    "mapping_writeback_runs",
    "occurrence_readings",
    "participations",
    "person_occurrences",
    "persons",
    "place_studies",
    "places",
    "privacy_labels",
    "research_question_targets",
    "research_questions",
    "next_actions",
    "reading_review_decisions",
    "reading_versions",
    "readings",
    "reassessment_batch_states",
    "reassessment_batches",
    "reassessment_impacts",
    "reassessment_item_results",
    "reassessment_items",
    "source_assertions",
    "source_items",
    "external_sync_items",
    "transcript_segments",
}

CREATE_SPECS: dict[str, tuple[str, tuple[str, ...]]] = {
    "transcriptSegments": (
        "transcript_segments",
        (
            "id",
            "reading_version_id",
            "artifact_id",
            "sequence_number",
            "language",
            "page_label",
            "region_json",
            "text",
        ),
    ),
    "events": (
        "events",
        (
            "id",
            "source_item_id",
            "event_type",
            "date_text",
            "date_from",
            "date_to",
            "place_id",
            "place_text",
            "verification_status",
            "notes",
        ),
    ),
    "sourceAssertions": (
        "source_assertions",
        (
            "id",
            "source_item_id",
            "reading_version_id",
            "transcript_segment_id",
            "assertion_type",
            "subject_ref",
            "predicate",
            "object_text",
            "information_quality",
            "reading_certainty",
            "verification_status",
            "metadata_json",
        ),
    ),
    "personOccurrences": (
        "person_occurrences",
        (
            "id",
            "source_item_id",
            "reading_version_id",
            "transcript_segment_id",
            "person_id",
            "raw_name",
            "normalized_name",
            "search_name",
            "role_text",
            "reading_confidence",
            "verification_status",
            "evidence_json",
        ),
    ),
    "occurrenceReadings": (
        "occurrence_readings",
        ("id", "occurrence_id", "reading_version_id", "role_text", "evidence_json"),
    ),
    "participations": (
        "participations",
        (
            "id",
            "event_id",
            "occurrence_id",
            "role",
            "age_text",
            "occupation_text",
            "residence_text",
            "status_text",
            "is_informant",
            "source_assertion_id",
        ),
    ),
    "households": (
        "households",
        (
            "id",
            "place_id",
            "label",
            "date_from",
            "date_to",
            "verification_status",
            "notes",
        ),
    ),
    "addresses": (
        "addresses",
        (
            "id",
            "household_id",
            "place_id",
            "address_text",
            "house_number",
            "date_from",
            "date_to",
            "source_item_id",
        ),
    ),
    "householdMemberships": (
        "household_memberships",
        (
            "id",
            "household_id",
            "person_id",
            "occurrence_id",
            "role",
            "date_from",
            "date_to",
            "source_assertion_id",
        ),
    ),
    "localOccurrences": (
        "local_occurrences",
        (
            "id",
            "study_id",
            "occurrence_id",
            "household_id",
            "source_item_id",
            "event_date_text",
            "event_type",
            "role",
            "surname_variant",
            "given_names",
            "search_name",
            "house_number",
            "citation_id",
            "verification_status",
            "metadata_json",
        ),
    ),
}

PATCH_SPECS: dict[str, tuple[str, tuple[str, ...]]] = {
    "personOccurrencePatches": (
        "person_occurrences",
        (
            "reading_version_id",
            "transcript_segment_id",
            "raw_name",
            "normalized_name",
            "search_name",
            "role_text",
            "reading_confidence",
            "verification_status",
            "evidence_json",
        ),
    ),
    "eventPatches": (
        "events",
        (
            "event_type",
            "date_text",
            "date_from",
            "date_to",
            "place_id",
            "place_text",
            "verification_status",
            "notes",
        ),
    ),
    "fieldReadingPatches": (
        "field_readings",
        ("transcript_segment_id",),
    ),
}

PERSON_OCCURRENCE_QUARANTINE_GROUP = "personOccurrenceQuarantines"
PERSON_OCCURRENCE_QUARANTINE_EXPECTED_FIELDS = (
    "id",
    "source_item_id",
    "reading_version_id",
    "transcript_segment_id",
    "person_id",
    "legacy_mention_id",
    "raw_name",
    "normalized_name",
    "search_name",
    "role_text",
    "reading_confidence",
    "verification_status",
    "evidence_json",
)
PERSON_OCCURRENCE_QUARANTINE_TARGET_FIELDS = {
    "person_id",
    "reading_version_id",
    "transcript_segment_id",
    "raw_name",
    "normalized_name",
    "search_name",
    "role_text",
    "reading_confidence",
    "verification_status",
    "evidence_json",
}
QUARANTINE_IMPACT_CATEGORIES = {
    "source_item",
    "occurrence_projection",
    "prior_person",
    "identity_candidate",
    "identity_decision",
    "question_next_action",
    "ui",
    "controlled_export",
    "external_sync",
}
TERMINAL_IMPACT_STATUSES = {"applied", "deferred", "not_affected"}
EXTRACTION_STATUSES = {
    "not_started",
    "target_only",
    "partial",
    "all_named_people_extracted",
    "reviewed",
    "blocked",
}
DOMAIN_OPERATION_GROUPS = {
    "events",
    "eventPatches",
    "sourceAssertions",
    "personOccurrences",
    "personOccurrencePatches",
    PERSON_OCCURRENCE_QUARANTINE_GROUP,
    "occurrenceReadings",
    "participations",
    "households",
    "addresses",
    "householdMemberships",
    "localOccurrences",
}


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Pole {label} musi być niepustym tekstem")
    return value.strip()


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"Pole {label} musi być obiektem")
    return value


def _list(value: Any, label: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"Pole {label} musi być listą obiektów")
    return value


def _load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Manifest mapowania musi być obiektem JSON")
    if payload.get("schemaVersion") != 1:
        raise ValueError("Nieobsługiwana wersja manifestu mapowania")
    return payload


def _row(connection, table: str, object_id: str) -> dict[str, Any] | None:
    result = connection.execute(f"SELECT * FROM {table} WHERE id=?", (object_id,)).fetchone()
    return dict(result) if result else None


def _subset(row: dict[str, Any], fields: Iterable[str]) -> dict[str, Any]:
    return {field: row.get(field) for field in fields}


def _normalize_create(entry: dict[str, Any], fields: tuple[str, ...], label: str) -> dict:
    unknown = set(entry) - set(fields)
    if unknown:
        raise ValueError(f"{label} zawiera niedozwolone pola: {', '.join(sorted(unknown))}")
    object_id = _required_text(entry.get("id"), f"{label}.id")
    result = {field: entry.get(field) for field in fields}
    result["id"] = object_id
    return result


def _normalize_patch(
    entry: dict[str, Any], allowed: tuple[str, ...], label: str
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    unknown = set(entry) - {"id", "expected", "set"}
    if unknown:
        raise ValueError(f"{label} zawiera niedozwolone pola: {', '.join(sorted(unknown))}")
    object_id = _required_text(entry.get("id"), f"{label}.id")
    expected = _object(entry.get("expected", {}), f"{label}.expected")
    target = _object(entry.get("set"), f"{label}.set")
    if not target:
        raise ValueError(f"{label}.set nie może być pusty")
    for name, value in (("expected", expected), ("set", target)):
        invalid = set(value) - set(allowed)
        if invalid:
            raise ValueError(
                f"{label}.{name} zawiera niedozwolone pola: {', '.join(sorted(invalid))}"
            )
    return object_id, expected, target


def _normalize_person_occurrence_quarantine(
    entry: dict[str, Any], label: str
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    unknown = set(entry) - {"id", "expected", "set"}
    if unknown:
        raise ValueError(f"{label} zawiera niedozwolone pola: {', '.join(sorted(unknown))}")
    object_id = _required_text(entry.get("id"), f"{label}.id")
    expected = _object(entry.get("expected"), f"{label}.expected")
    missing = set(PERSON_OCCURRENCE_QUARANTINE_EXPECTED_FIELDS) - set(expected)
    unknown_expected = set(expected) - set(PERSON_OCCURRENCE_QUARANTINE_EXPECTED_FIELDS)
    if missing:
        raise ValueError(
            f"{label}.expected nie zawiera pełnego snapshotu: {', '.join(sorted(missing))}"
        )
    if unknown_expected:
        raise ValueError(
            f"{label}.expected zawiera niedozwolone pola: "
            f"{', '.join(sorted(unknown_expected))}"
        )
    if expected["id"] != object_id:
        raise ValueError(f"{label}.expected.id musi odpowiadać {label}.id")
    _required_text(expected.get("source_item_id"), f"{label}.expected.source_item_id")
    _required_text(expected.get("person_id"), f"{label}.expected.person_id")
    target = _object(entry.get("set"), f"{label}.set")
    invalid_target = set(target) - PERSON_OCCURRENCE_QUARANTINE_TARGET_FIELDS
    if invalid_target:
        raise ValueError(
            f"{label}.set zawiera niedozwolone pola: {', '.join(sorted(invalid_target))}"
        )
    if "person_id" not in target or target["person_id"] is not None:
        raise ValueError(f"{label}.set.person_id musi być jawnie null")
    return object_id, expected, target


def _normalize_quarantine_impact_assessment(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    entries = _list(payload.get("impactAssessment"), "impactAssessment")
    for index, entry in enumerate(entries):
        label = f"impactAssessment[{index}]"
        unknown = set(entry) - {
            "category",
            "objectType",
            "objectId",
            "impactType",
            "plannedAction",
            "writebackStatus",
            "rationale",
            "preview",
        }
        if unknown:
            raise ValueError(
                f"{label} zawiera niedozwolone pola: {', '.join(sorted(unknown))}"
            )
        category = _required_text(entry.get("category"), f"{label}.category")
        if category not in QUARANTINE_IMPACT_CATEGORIES:
            raise ValueError(f"{label}.category ma nieobsługiwaną wartość: {category}")
        normalized = {
            "category": category,
            "objectType": _required_text(entry.get("objectType"), f"{label}.objectType"),
            "objectId": _required_text(entry.get("objectId"), f"{label}.objectId"),
            "impactType": _required_text(entry.get("impactType"), f"{label}.impactType"),
            "plannedAction": _required_text(
                entry.get("plannedAction"), f"{label}.plannedAction"
            ),
            "writebackStatus": _required_text(
                entry.get("writebackStatus"), f"{label}.writebackStatus"
            ),
            "rationale": _required_text(entry.get("rationale"), f"{label}.rationale"),
            "preview": _object(entry.get("preview", {}), f"{label}.preview"),
        }
        if normalized["writebackStatus"] not in TERMINAL_IMPACT_STATUSES:
            raise ValueError(
                f"{label}.writebackStatus musi być terminalny: "
                "applied, deferred albo not_affected"
            )
        if (
            normalized["writebackStatus"] == "applied"
            and category != "occurrence_projection"
        ):
            raise ValueError(
                f"{label}.writebackStatus=applied jest dozwolony wyłącznie dla "
                "occurrence_projection zapisywanej przez tę operację"
            )
        key = (
            category,
            normalized["objectType"],
            normalized["objectId"],
            normalized["impactType"],
        )
        if key in seen:
            raise ValueError(f"Powtórzony impact kwarantanny: {'/'.join(key)}")
        seen.add(key)
        result.append(normalized)
    missing_categories = QUARANTINE_IMPACT_CATEGORIES - {
        item["category"] for item in result
    }
    if missing_categories:
        raise ValueError(
            "impactAssessment nie pokrywa kategorii kwarantanny: "
            + ", ".join(sorted(missing_categories))
        )
    return result


def _source_lineage(connection, source_item_id: str) -> list[str]:
    """Return the source itself followed by its ancestors, without trusting the tree blindly."""
    lineage: list[str] = []
    current_id: str | None = source_item_id
    while current_id is not None:
        if current_id in lineage:
            raise RuntimeError("Wykryto cykl w hierarchii source_items")
        lineage.append(current_id)
        row = connection.execute(
            "SELECT parent_id FROM source_items WHERE id=?", (current_id,)
        ).fetchone()
        if row is None:
            break
        current_id = row[0]
    return lineage


def _source_input(connection, source_item_id: str) -> dict[str, Any]:
    source = connection.execute(
        """SELECT id,legacy_source_id,title,item_type,date_text,locator,source_url,
                  source_quality,verification_status,metadata_json
             FROM source_items WHERE id=?""",
        (source_item_id,),
    ).fetchone()
    if not source:
        raise KeyError(f"Nie znaleziono source_item: {source_item_id}")
    evidence_source_ids = _source_lineage(connection, source_item_id)
    placeholders = ",".join("?" for _ in evidence_source_ids)
    artifacts = [
        dict(row)
        for row in connection.execute(
            f"""SELECT id,source_item_id,artifact_type,path,sha256,immutable,metadata_json
                  FROM artifacts
                 WHERE source_item_id IN ({placeholders})
                 ORDER BY source_item_id,id""",
            tuple(evidence_source_ids),
        ).fetchall()
    ]
    versions = [
        dict(row)
        for row in connection.execute(
            f"""SELECT r.id reading_id,r.source_item_id,r.reading_type,r.model_or_reviewer,
                      r.metadata_json,rv.id reading_version_id,rv.version_number,
                      rv.status,rv.result_path,rv.payload_json
                 FROM readings r JOIN reading_versions rv ON rv.reading_id=r.id
                WHERE r.source_item_id IN ({placeholders})
                ORDER BY r.source_item_id,r.id,rv.version_number,rv.id""",
            tuple(evidence_source_ids),
        ).fetchall()
    ]
    review = connection.execute(
        """SELECT id,revision,status,decided_by,decided_by_kind,decided_by_run_id,
                  rationale,decision_json
             FROM reading_review_decisions WHERE source_item_id=?
             ORDER BY revision DESC LIMIT 1""",
        (source_item_id,),
    ).fetchone()
    return {
        "sourceItem": dict(source),
        "evidenceSourceIds": evidence_source_ids,
        "artifacts": artifacts,
        "readingVersions": versions,
        "latestReview": dict(review) if review else None,
    }


def source_input_fingerprint(root: Path, source_item_id: str) -> str:
    initialize(root)
    with connect(root) as connection:
        require_tables(connection, REQUIRED_TABLES)
        connection.execute("PRAGMA query_only = ON")
        return _fingerprint(_source_input(connection, source_item_id))


def _manifest_fingerprint(payload: dict[str, Any]) -> str:
    clean = dict(payload)
    clean.pop("manifestFingerprint", None)
    return _fingerprint(clean)


def _database_fingerprint(connection, source_item_id: str) -> str:
    payload = {
        "schema": int(connection.execute("PRAGMA user_version").fetchone()[0]),
        "sourceInput": _fingerprint(_source_input(connection, source_item_id)),
        "counts": {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "addresses",
                "events",
                "household_memberships",
                "households",
                "local_occurrences",
                "person_occurrences",
                "participations",
                "transcript_segments",
                "source_assertions",
            )
        },
    }
    return _fingerprint(payload)


def _validate_header(connection, payload: dict[str, Any]) -> dict[str, Any]:
    source_item_id = _required_text(payload.get("sourceItemId"), "sourceItemId")
    if payload.get("privacyReviewed") is not True:
        raise RuntimeError("Manifest wymaga jawnego privacyReviewed=true")
    privacy = connection.execute(
        """SELECT classification FROM privacy_labels
           WHERE object_type='source_item' AND object_id=?""",
        (source_item_id,),
    ).fetchone()
    privacy_classification = privacy[0] if privacy else None
    if privacy_classification in {"living", "restricted", "family_private", "strict_private"}:
        if payload.get("allowRestricted") is not True:
            raise RuntimeError("Źródło chronione wymaga jawnego allowRestricted=true")
    protected_classifications = {
        "living",
        "restricted",
        "family_private",
        "strict_private",
    }
    operations = _object(payload.get("operations", {}), "operations")
    quarantine_entries = _list(
        operations.get(PERSON_OCCURRENCE_QUARANTINE_GROUP),
        f"operations.{PERSON_OCCURRENCE_QUARANTINE_GROUP}",
    )
    protected_objects: list[tuple[str, str, str]] = []
    for index, entry in enumerate(quarantine_entries):
        object_id, expected, _ = _normalize_person_occurrence_quarantine(
            entry, f"operations.{PERSON_OCCURRENCE_QUARANTINE_GROUP}[{index}]"
        )
        person_ids = {str(expected["person_id"])}
        current = _row(connection, "person_occurrences", object_id)
        if current and current.get("person_id"):
            person_ids.add(str(current["person_id"]))
        refs = [("person_occurrence", object_id)] + [
            ("person", person_id) for person_id in sorted(person_ids)
        ]
        for object_type, referenced_id in refs:
            labels = connection.execute(
                """SELECT classification FROM privacy_labels
                   WHERE object_type=? AND object_id=?""",
                (object_type, referenced_id),
            ).fetchall()
            protected_objects.extend(
                (object_type, referenced_id, str(label[0]))
                for label in labels
                if label[0] in protected_classifications
            )
    if protected_objects and payload.get("allowRestricted") is not True:
        object_type, object_id, classification = protected_objects[0]
        raise RuntimeError(
            "Kwarantanna chronionego obiektu wymaga jawnego allowRestricted=true: "
            f"{object_type}/{object_id} ({classification})"
        )
    batch = _object(payload.get("batch"), "batch")
    for field in ("id", "batchKey", "inputFingerprint", "stoppingRule", "createdBy"):
        _required_text(batch.get(field), f"batch.{field}")
    expected_schema = payload.get("expectedDatabaseSchema")
    current_schema = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if expected_schema != current_schema:
        raise RuntimeError(
            f"Konflikt schema: manifest oczekuje {expected_schema}, baza ma {current_schema}"
        )
    latest = connection.execute(
        "SELECT revision FROM reading_review_decisions WHERE source_item_id=? ORDER BY revision DESC LIMIT 1",
        (source_item_id,),
    ).fetchone()
    current_review_revision = int(latest[0]) if latest else None
    if payload.get("expectedReviewRevision") != current_review_revision:
        raise RuntimeError(
            "Konflikt rewizji review: "
            f"oczekiwano {payload.get('expectedReviewRevision')!r}, "
            f"jest {current_review_revision!r}"
        )
    input_fingerprint = _fingerprint(_source_input(connection, source_item_id))
    if payload.get("inputFingerprint") != input_fingerprint:
        raise RuntimeError("Konflikt fingerprintu wejścia source/readings/review")
    if batch.get("inputFingerprint") != input_fingerprint:
        raise RuntimeError("Fingerprint batcha nie odpowiada wejściu dokumentu")
    computed = _manifest_fingerprint(payload)
    declared = payload.get("manifestFingerprint")
    if declared is not None and declared != computed:
        raise RuntimeError("Deklarowany manifestFingerprint jest niezgodny z treścią")
    return {
        "sourceItemId": source_item_id,
        "inputFingerprint": input_fingerprint,
        "manifestFingerprint": computed,
        "databaseSchema": current_schema,
        "reviewRevision": current_review_revision,
        "privacyClassification": privacy_classification,
    }


def _quarantine_impact_conflicts(
    connection,
    source_item_id: str,
    quarantines: list[tuple[str, dict[str, Any]]],
    impacts: list[dict[str, Any]],
    batch_question_id: str | None,
) -> list[dict[str, Any]]:
    by_category: dict[str, list[dict[str, Any]]] = {
        category: [] for category in QUARANTINE_IMPACT_CATEGORIES
    }
    for impact in impacts:
        by_category[impact["category"]].append(impact)
    conflicts: list[dict[str, Any]] = []
    backed_impact_tables = {
        "source_items",
        "person_occurrences",
        "persons",
        "identity_candidates",
        "identity_decisions",
        "research_questions",
        "next_actions",
        "external_sync_items",
    }
    for impact in impacts:
        if (
            impact["objectType"] in backed_impact_tables
            and _row(connection, impact["objectType"], impact["objectId"]) is None
        ):
            conflicts.append(
                {
                    "group": PERSON_OCCURRENCE_QUARANTINE_GROUP,
                    "table": "reassessment_impacts",
                    "id": impact["objectId"],
                    "reason": "declared_impact_object_missing",
                    "category": impact["category"],
                    "objectType": impact["objectType"],
                }
            )

    def require_pairs(
        category: str,
        required: set[tuple[str, str]],
        required_status: str | None = None,
    ) -> None:
        declared_items = {
            (item["objectType"], item["objectId"]): item
            for item in by_category[category]
        }
        declared = set(declared_items)
        for object_type, object_id in sorted(required - declared):
            conflicts.append(
                {
                    "group": PERSON_OCCURRENCE_QUARANTINE_GROUP,
                    "table": "reassessment_impacts",
                    "id": object_id,
                    "reason": "missing_declared_impact",
                    "category": category,
                    "objectType": object_type,
                }
            )
        if required_status is not None:
            for object_type, object_id in sorted(required & declared):
                if declared_items[(object_type, object_id)]["writebackStatus"] != required_status:
                    conflicts.append(
                        {
                            "group": PERSON_OCCURRENCE_QUARANTINE_GROUP,
                            "table": "reassessment_impacts",
                            "id": object_id,
                            "reason": "declared_impact_status_mismatch",
                            "category": category,
                            "objectType": object_type,
                            "expectedStatus": required_status,
                        }
                    )

    occurrence_ids = {object_id for object_id, _ in quarantines}
    person_ids = {str(expected["person_id"]) for _, expected in quarantines}
    require_pairs("source_item", {("source_items", source_item_id)})
    require_pairs(
        "occurrence_projection",
        {("person_occurrences", object_id) for object_id in occurrence_ids},
        "applied",
    )
    require_pairs("prior_person", {("persons", person_id) for person_id in person_ids})

    placeholders = ",".join("?" for _ in occurrence_ids)
    candidate_ids = {
        str(row[0])
        for row in connection.execute(
            f"""SELECT id FROM identity_candidates
                  WHERE occurrence_a_id IN ({placeholders})
                     OR occurrence_b_id IN ({placeholders})""",
            tuple(occurrence_ids) + tuple(occurrence_ids),
        ).fetchall()
    }
    require_pairs(
        "identity_candidate",
        {("identity_candidates", candidate_id) for candidate_id in candidate_ids},
    )
    if candidate_ids:
        candidate_placeholders = ",".join("?" for _ in candidate_ids)
        decision_ids = {
            str(row[0])
            for row in connection.execute(
                f"SELECT id FROM identity_decisions WHERE candidate_id IN ({candidate_placeholders})",
                tuple(candidate_ids),
            ).fetchall()
        }
    else:
        decision_ids = set()
    require_pairs(
        "identity_decision",
        {("identity_decisions", decision_id) for decision_id in decision_ids},
    )

    occurrence_placeholders = ",".join("?" for _ in occurrence_ids)
    person_placeholders = ",".join("?" for _ in person_ids)
    question_ids = {
        str(row[0])
        for row in connection.execute(
            f"""SELECT DISTINCT question_id FROM research_question_targets
                  WHERE (target_type='person_occurrence' AND target_id IN ({occurrence_placeholders}))
                     OR (target_type='person' AND target_id IN ({person_placeholders}))
                     OR (target_type='source_item' AND target_id=?)""",
            tuple(occurrence_ids) + tuple(person_ids) + (source_item_id,),
        ).fetchall()
    }
    if batch_question_id:
        question_ids.add(batch_question_id)
    question_dependencies = {
        ("research_questions", question_id) for question_id in question_ids
    }
    if question_ids:
        question_placeholders = ",".join("?" for _ in question_ids)
        question_dependencies.update(
            ("next_actions", str(row[0]))
            for row in connection.execute(
                f"SELECT id FROM next_actions WHERE question_id IN ({question_placeholders})",
                tuple(question_ids),
            ).fetchall()
        )
    require_pairs("question_next_action", question_dependencies)
    require_pairs("ui", {("ui_projection_scope", source_item_id)})
    require_pairs(
        "controlled_export", {("controlled_export_scope", source_item_id)}
    )

    person_external_placeholders = ",".join("?" for _ in person_ids)
    external_ids = {
        str(row[0])
        for row in connection.execute(
            f"""SELECT id FROM external_sync_items
                  WHERE (object_type='person' AND object_id IN ({person_external_placeholders}))
                     OR (object_type='source_item' AND object_id=?)""",
            tuple(person_ids) + (source_item_id,),
        ).fetchall()
    }
    require_pairs(
        "external_sync",
        {("external_sync_items", external_id) for external_id in external_ids},
    )

    for category, dependencies in (
        ("identity_candidate", candidate_ids),
        ("identity_decision", decision_ids),
        ("question_next_action", question_dependencies),
        ("external_sync", external_ids),
    ):
        if not dependencies and not any(
            item["writebackStatus"] == "not_affected"
            for item in by_category[category]
        ):
            conflicts.append(
                {
                    "group": PERSON_OCCURRENCE_QUARANTINE_GROUP,
                    "table": "reassessment_impacts",
                    "id": source_item_id,
                    "reason": "empty_impact_scope_not_acknowledged",
                    "category": category,
                }
            )
    return conflicts


def _quarantine_replay_conflicts(
    connection, payload: dict[str, Any]
) -> list[dict[str, Any]]:
    operations = _object(payload.get("operations", {}), "operations")
    entries = _list(
        operations.get(PERSON_OCCURRENCE_QUARANTINE_GROUP),
        f"operations.{PERSON_OCCURRENCE_QUARANTINE_GROUP}",
    )
    source_item_id = payload["sourceItemId"]
    compatible_sources = set(_source_lineage(connection, source_item_id))
    conflicts: list[dict[str, Any]] = []
    snapshots: list[tuple[str, dict[str, Any]]] = []
    for index, entry in enumerate(entries):
        object_id, expected, target = _normalize_person_occurrence_quarantine(
            entry, f"operations.{PERSON_OCCURRENCE_QUARANTINE_GROUP}[{index}]"
        )
        snapshots.append((object_id, expected))
        current = _row(connection, "person_occurrences", object_id)
        applied_target = dict(expected)
        applied_target.update(target)
        if current is None or _subset(
            current, PERSON_OCCURRENCE_QUARANTINE_EXPECTED_FIELDS
        ) != applied_target:
            conflicts.append(
                {
                    "group": PERSON_OCCURRENCE_QUARANTINE_GROUP,
                    "table": "person_occurrences",
                    "id": object_id,
                    "reason": "stale_applied_quarantine_state",
                    "current": _subset(
                        current or {}, PERSON_OCCURRENCE_QUARANTINE_EXPECTED_FIELDS
                    ),
                    "expectedApplied": applied_target,
                }
            )
            continue
        if current["source_item_id"] != source_item_id:
            conflicts.append(
                {
                    "group": PERSON_OCCURRENCE_QUARANTINE_GROUP,
                    "table": "person_occurrences",
                    "id": object_id,
                    "reason": "source_scope_mismatch",
                }
            )
        reading_version_id = current.get("reading_version_id")
        transcript_segment_id = current.get("transcript_segment_id")
        reading = connection.execute(
            """SELECT r.source_item_id FROM reading_versions rv
               JOIN readings r ON r.id=rv.reading_id WHERE rv.id=?""",
            (reading_version_id,),
        ).fetchone()
        segment = connection.execute(
            """SELECT ts.reading_version_id,r.source_item_id
                 FROM transcript_segments ts
                 JOIN reading_versions rv ON rv.id=ts.reading_version_id
                 JOIN readings r ON r.id=rv.reading_id
                WHERE ts.id=?""",
            (transcript_segment_id,),
        ).fetchone()
        if (
            not reading_version_id
            or not transcript_segment_id
            or reading is None
            or segment is None
            or reading["source_item_id"] not in compatible_sources
            or segment["source_item_id"] not in compatible_sources
            or segment["reading_version_id"] != reading_version_id
        ):
            conflicts.append(
                {
                    "group": PERSON_OCCURRENCE_QUARANTINE_GROUP,
                    "table": "person_occurrences",
                    "id": object_id,
                    "reason": "occurrence_provenance_mismatch",
                }
            )
    conflicts.extend(
        _quarantine_impact_conflicts(
            connection,
            source_item_id,
            snapshots,
            _normalize_quarantine_impact_assessment(payload),
            str(payload["batch"]["questionId"])
            if payload["batch"].get("questionId")
            else None,
        )
    )
    return conflicts


def _build_diff(connection, payload: dict[str, Any]) -> dict[str, Any]:
    operations = _object(payload.get("operations", {}), "operations")
    unknown = (
        set(operations)
        - set(CREATE_SPECS)
        - set(PATCH_SPECS)
        - {PERSON_OCCURRENCE_QUARANTINE_GROUP}
    )
    if unknown:
        raise ValueError("Nieznane grupy operacji: " + ", ".join(sorted(unknown)))
    changes: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    quarantine_snapshots: list[tuple[str, dict[str, Any]]] = []
    for group, (table, fields) in CREATE_SPECS.items():
        for index, entry in enumerate(_list(operations.get(group), f"operations.{group}")):
            target = _normalize_create(entry, fields, f"operations.{group}[{index}]")
            key = (table, target["id"])
            if key in seen:
                raise ValueError(f"Powtórzony obiekt w manifeście: {table}/{target['id']}")
            seen.add(key)
            current = _row(connection, table, target["id"])
            if current is None:
                changes.append({"group": group, "table": table, "id": target["id"], "action": "create", "target": target})
            elif _subset(current, fields) == target:
                changes.append({"group": group, "table": table, "id": target["id"], "action": "no_op", "target": target})
            else:
                conflicts.append({"group": group, "table": table, "id": target["id"], "reason": "existing_row_differs", "current": _subset(current, fields), "target": target})
    for group, (table, allowed) in PATCH_SPECS.items():
        for index, entry in enumerate(_list(operations.get(group), f"operations.{group}")):
            object_id, expected, target = _normalize_patch(entry, allowed, f"operations.{group}[{index}]")
            key = (table, object_id)
            if key in seen:
                raise ValueError(f"Powtórzony obiekt w manifeście: {table}/{object_id}")
            seen.add(key)
            current = _row(connection, table, object_id)
            if current is None:
                conflicts.append({"group": group, "table": table, "id": object_id, "reason": "missing_row"})
            elif _subset(current, target) == target:
                changes.append({"group": group, "table": table, "id": object_id, "action": "no_op", "expected": expected, "target": target})
            elif _subset(current, expected) == expected:
                changes.append({"group": group, "table": table, "id": object_id, "action": "update", "expected": expected, "target": target})
            else:
                conflicts.append({"group": group, "table": table, "id": object_id, "reason": "optimistic_concurrency", "current": _subset(current, set(expected) | set(target)), "expected": expected, "target": target})
    for index, entry in enumerate(
        _list(
            operations.get(PERSON_OCCURRENCE_QUARANTINE_GROUP),
            f"operations.{PERSON_OCCURRENCE_QUARANTINE_GROUP}",
        )
    ):
        label = f"operations.{PERSON_OCCURRENCE_QUARANTINE_GROUP}[{index}]"
        object_id, expected, target = _normalize_person_occurrence_quarantine(entry, label)
        quarantine_snapshots.append((object_id, expected))
        table = "person_occurrences"
        key = (table, object_id)
        if key in seen:
            raise ValueError(f"Powtórzony obiekt w manifeście: {table}/{object_id}")
        seen.add(key)
        current = _row(connection, table, object_id)
        if current is None:
            conflicts.append(
                {
                    "group": PERSON_OCCURRENCE_QUARANTINE_GROUP,
                    "table": table,
                    "id": object_id,
                    "reason": "missing_row",
                }
            )
            continue
        if _subset(current, expected) == expected:
            changes.append(
                {
                    "group": PERSON_OCCURRENCE_QUARANTINE_GROUP,
                    "table": table,
                    "id": object_id,
                    "action": "update",
                    "expected": expected,
                    "target": target,
                }
            )
        else:
            conflicts.append(
                {
                    "group": PERSON_OCCURRENCE_QUARANTINE_GROUP,
                    "table": table,
                    "id": object_id,
                    "reason": "optimistic_concurrency",
                    "current": _subset(
                        current, PERSON_OCCURRENCE_QUARANTINE_EXPECTED_FIELDS
                    ),
                    "expected": expected,
                    "target": target,
                }
            )
    if quarantine_snapshots:
        conflicts.extend(
            _quarantine_impact_conflicts(
                connection,
                payload["sourceItemId"],
                quarantine_snapshots,
                _normalize_quarantine_impact_assessment(payload),
                str(payload["batch"]["questionId"])
                if payload["batch"].get("questionId")
                else None,
            )
        )
    source_item_id = payload["sourceItemId"]
    compatible_evidence_sources = set(_source_lineage(connection, source_item_id))
    creates = {
        (item["table"], item["id"]): item["target"]
        for item in changes
        if item["action"] == "create"
    }

    def will_exist(table: str, object_id: str | None) -> bool:
        if object_id is None:
            return True
        return (table, object_id) in creates or _row(connection, table, object_id) is not None

    def object_source(table: str, object_id: str | None) -> str | None:
        if object_id is None:
            return None
        created = creates.get((table, object_id))
        if created and "source_item_id" in created:
            return created["source_item_id"]
        if table == "transcript_segments":
            target = created or _row(connection, table, object_id)
            return object_source("reading_versions", target.get("reading_version_id") if target else None)
        if table == "reading_versions":
            row = connection.execute(
                """SELECT r.source_item_id FROM reading_versions rv
                   JOIN readings r ON r.id=rv.reading_id WHERE rv.id=?""",
                (object_id,),
            ).fetchone()
            return row[0] if row else None
        if table == "artifacts":
            row = _row(connection, table, object_id)
            return row.get("source_item_id") if row else None
        row = created or _row(connection, table, object_id)
        return row.get("source_item_id") if row else None

    def add_conflict(table: str, object_id: str, reason: str) -> None:
        conflicts.append({"table": table, "id": object_id, "reason": reason})

    def evidence_is_compatible(table: str, object_id: str | None) -> bool:
        return object_source(table, object_id) in compatible_evidence_sources

    for change in changes:
        is_occurrence_quarantine = (
            change["group"] == PERSON_OCCURRENCE_QUARANTINE_GROUP
        )
        if change["action"] not in {"create", "update"} and not is_occurrence_quarantine:
            continue
        table = change["table"]
        target = change["target"]
        object_id = change["id"]
        if table in {"events", "source_assertions", "person_occurrences"}:
            effective_source = target.get("source_item_id")
            if effective_source is None:
                current = _row(connection, table, object_id) or {}
                effective_source = current.get("source_item_id")
            if effective_source != source_item_id:
                conflicts.append({"table": table, "id": object_id, "reason": "source_scope_mismatch"})
        if table == "transcript_segments":
            if not will_exist("reading_versions", target.get("reading_version_id")):
                conflicts.append({"table": table, "id": object_id, "reason": "missing_reading_version"})
            if not will_exist("artifacts", target.get("artifact_id")):
                conflicts.append({"table": table, "id": object_id, "reason": "missing_artifact"})
            if not evidence_is_compatible(
                "reading_versions", target.get("reading_version_id")
            ):
                conflicts.append({"table": table, "id": object_id, "reason": "reading_source_mismatch"})
            if not evidence_is_compatible("artifacts", target.get("artifact_id")):
                conflicts.append({"table": table, "id": object_id, "reason": "artifact_source_mismatch"})
        if table == "person_occurrences":
            current = _row(connection, table, object_id) or {}
            reading_version_id = target.get(
                "reading_version_id", current.get("reading_version_id")
            )
            segment_id = target.get(
                "transcript_segment_id", current.get("transcript_segment_id")
            )
            if not reading_version_id or not segment_id:
                conflicts.append({"table": table, "id": object_id, "reason": "occurrence_provenance_required"})
            elif (
                not will_exist("reading_versions", reading_version_id)
                or not will_exist("transcript_segments", segment_id)
                or not evidence_is_compatible("reading_versions", reading_version_id)
                or not evidence_is_compatible("transcript_segments", segment_id)
            ):
                conflicts.append({"table": table, "id": object_id, "reason": "occurrence_provenance_mismatch"})
            elif is_occurrence_quarantine:
                segment = _row(connection, "transcript_segments", segment_id) or {}
                if segment.get("reading_version_id") != reading_version_id:
                    conflicts.append(
                        {
                            "group": PERSON_OCCURRENCE_QUARANTINE_GROUP,
                            "table": table,
                            "id": object_id,
                            "reason": "occurrence_provenance_mismatch",
                        }
                    )
        if table == "field_readings":
            segment_id = target.get("transcript_segment_id")
            current = _row(connection, table, object_id) or {}
            version_id = current.get("reading_version_id")
            if not segment_id or not will_exist("transcript_segments", segment_id):
                conflicts.append({"table": table, "id": object_id, "reason": "field_segment_required"})
            elif not evidence_is_compatible("reading_versions", version_id):
                conflicts.append({"table": table, "id": object_id, "reason": "field_reading_source_mismatch"})
        if table == "source_assertions":
            for ref_table, ref_field in (
                ("reading_versions", "reading_version_id"),
                ("transcript_segments", "transcript_segment_id"),
            ):
                if not will_exist(ref_table, target.get(ref_field)):
                    conflicts.append({"table": table, "id": object_id, "reason": f"missing_{ref_field}"})
                elif not evidence_is_compatible(ref_table, target.get(ref_field)):
                    conflicts.append(
                        {
                            "table": table,
                            "id": object_id,
                            "reason": f"{ref_field}_source_mismatch",
                        }
                    )
        if table == "occurrence_readings":
            if not will_exist("person_occurrences", target.get("occurrence_id")):
                conflicts.append({"table": table, "id": object_id, "reason": "missing_occurrence"})
            if not will_exist("reading_versions", target.get("reading_version_id")):
                conflicts.append({"table": table, "id": object_id, "reason": "missing_reading_version"})
            elif not evidence_is_compatible(
                "reading_versions", target.get("reading_version_id")
            ):
                conflicts.append(
                    {"table": table, "id": object_id, "reason": "reading_source_mismatch"}
                )
        if table == "participations":
            for ref_table, ref_field in (
                ("events", "event_id"),
                ("person_occurrences", "occurrence_id"),
                ("source_assertions", "source_assertion_id"),
            ):
                if not will_exist(ref_table, target.get(ref_field)):
                    conflicts.append({"table": table, "id": object_id, "reason": f"missing_{ref_field}"})
        if table == "households":
            place_id = target.get("place_id")
            if place_id and not will_exist("places", place_id):
                add_conflict(table, object_id, "missing_place_id")
            provenance_found = False
            for related_table, foreign_key in (
                ("addresses", "household_id"),
                ("household_memberships", "household_id"),
                ("local_occurrences", "household_id"),
            ):
                provenance_found = provenance_found or any(
                    related_target.get(foreign_key) == object_id
                    for (created_table, _), related_target in creates.items()
                    if created_table == related_table
                )
            if not provenance_found:
                add_conflict(table, object_id, "household_provenance_required")
        if table == "addresses":
            household_id = target.get("household_id")
            if not household_id or not will_exist("households", household_id):
                add_conflict(table, object_id, "missing_household_id")
            place_id = target.get("place_id")
            if not place_id or not will_exist("places", place_id):
                add_conflict(table, object_id, "missing_place_id")
            if target.get("source_item_id") != source_item_id:
                add_conflict(table, object_id, "address_provenance_mismatch")
        if table == "household_memberships":
            household_id = target.get("household_id")
            if not household_id or not will_exist("households", household_id):
                add_conflict(table, object_id, "missing_household_id")
            person_id = target.get("person_id")
            if person_id and not will_exist("persons", person_id):
                add_conflict(table, object_id, "missing_person_id")
            occurrence_id = target.get("occurrence_id")
            assertion_id = target.get("source_assertion_id")
            if not occurrence_id and not assertion_id:
                add_conflict(table, object_id, "membership_provenance_required")
            if occurrence_id:
                if not will_exist("person_occurrences", occurrence_id):
                    add_conflict(table, object_id, "missing_occurrence_id")
                elif object_source("person_occurrences", occurrence_id) != source_item_id:
                    add_conflict(table, object_id, "membership_occurrence_source_mismatch")
            if assertion_id:
                if not will_exist("source_assertions", assertion_id):
                    add_conflict(table, object_id, "missing_source_assertion_id")
                elif object_source("source_assertions", assertion_id) != source_item_id:
                    add_conflict(table, object_id, "membership_assertion_source_mismatch")
        if table == "local_occurrences":
            if target.get("source_item_id") != source_item_id:
                add_conflict(table, object_id, "local_occurrence_source_mismatch")
            study_id = target.get("study_id")
            if not study_id or not will_exist("place_studies", study_id):
                add_conflict(table, object_id, "missing_study_id")
            occurrence_id = target.get("occurrence_id")
            if occurrence_id:
                if not will_exist("person_occurrences", occurrence_id):
                    add_conflict(table, object_id, "missing_occurrence_id")
                elif object_source("person_occurrences", occurrence_id) != source_item_id:
                    add_conflict(table, object_id, "local_occurrence_provenance_mismatch")
            household_id = target.get("household_id")
            if household_id and not will_exist("households", household_id):
                add_conflict(table, object_id, "missing_household_id")
            citation_id = target.get("citation_id")
            if citation_id:
                if not will_exist("citations", citation_id):
                    add_conflict(table, object_id, "missing_citation_id")
                elif object_source("citations", citation_id) != source_item_id:
                    add_conflict(table, object_id, "citation_source_mismatch")

    extraction = _object(payload.get("extractionState"), "extractionState")
    extraction_status = _required_text(
        extraction.get("status"), "extractionState.status"
    )
    if extraction_status not in EXTRACTION_STATUSES:
        raise ValueError(f"Nieznany extractionState.status: {extraction_status}")
    declaration = extraction.get("domainCompletion")
    domain_completion: dict[str, Any] | None = None
    if extraction_status == "all_named_people_extracted" and declaration is None:
        domain_completion = {
            "state": "incomplete",
            "mode": FULL_ACT,
            "sourceItemId": source_item_id,
            "acceptedNamedPeopleFingerprint": None,
            "expectedCount": None,
            "completeCount": 0,
            "gaps": [],
        }
        conflicts.append(
            {
                "group": "domainCompletion",
                "table": "document_extraction_states",
                "id": source_item_id,
                "reason": "domain_completion_declaration_required",
            }
        )
    elif declaration is not None:
        domain_completion = assess_domain_completion(
            connection,
            source_item_id=source_item_id,
            declaration=declaration,
            overlay=overlay_from_changes(changes),
        )
        declared_domain_groups = sorted(
            group
            for group in DOMAIN_OPERATION_GROUPS
            if _list(operations.get(group), f"operations.{group}")
        )
        if (
            domain_completion["mode"] != FULL_ACT
            and declared_domain_groups
        ):
            domain_completion = {
                **domain_completion,
                "state": "incomplete",
                "declaredState": "no_domain_writeback_expected",
                "gaps": [
                    {
                        "reason": "domain_operations_declared_for_no_writeback_scope",
                        "operationGroups": declared_domain_groups,
                    }
                ],
            }
            conflicts.append(
                {
                    "group": "domainCompletion",
                    "table": "document_extraction_states",
                    "id": source_item_id,
                    "reason": "no_domain_writeback_expected_has_domain_operations",
                    "operationGroups": declared_domain_groups,
                }
            )
        if (
            extraction_status == "all_named_people_extracted"
            and domain_completion["mode"] != FULL_ACT
        ):
            conflicts.append(
                {
                    "group": "domainCompletion",
                    "table": "document_extraction_states",
                    "id": source_item_id,
                    "reason": "full_extraction_requires_domain_writeback",
                    "state": domain_completion["state"],
                }
            )
        elif extraction_status == "all_named_people_extracted":
            conflicts.extend(
                {"group": "domainCompletion", "table": "domain_completion", **item}
                for item in gap_conflicts(domain_completion)
            )

    summary = {
        action: sum(item["action"] == action for item in changes)
        for action in ("create", "update", "no_op")
    }
    summary["conflict"] = len(conflicts)
    return {
        "summary": summary,
        "changes": changes,
        "conflicts": conflicts,
        "domainCompletion": domain_completion,
    }


def mapping_writeback_preview(root: Path, manifest_path: Path) -> dict[str, Any]:
    payload = _load_manifest(manifest_path)
    initialize(root)
    with connect(root) as connection:
        require_tables(connection, REQUIRED_TABLES)
        connection.execute("PRAGMA query_only = ON")
        header = _validate_header(connection, payload)
        operations = _object(payload.get("operations", {}), "operations")
        if _list(
            operations.get(PERSON_OCCURRENCE_QUARANTINE_GROUP),
            f"operations.{PERSON_OCCURRENCE_QUARANTINE_GROUP}",
        ):
            prior = connection.execute(
                """SELECT id,diff_json FROM mapping_writeback_runs
                   WHERE manifest_id=? AND mode='apply' AND status='applied'
                   ORDER BY created_at LIMIT 1""",
                (stable_id("mapping_manifest", header["manifestFingerprint"]),),
            ).fetchone()
            if prior:
                replay_conflicts = _quarantine_replay_conflicts(connection, payload)
                if replay_conflicts:
                    return {
                        "mode": "preview",
                        "status": "conflict",
                        **header,
                        "priorRunId": prior["id"],
                        "databaseFingerprint": _database_fingerprint(
                            connection, header["sourceItemId"]
                        ),
                        "diff": {
                            "summary": {
                                "create": 0,
                                "update": 0,
                                "no_op": 0,
                                "conflict": len(replay_conflicts),
                            },
                            "changes": [],
                            "conflicts": replay_conflicts,
                        },
                    }
                applied_diff = json.loads(prior["diff_json"])
                replay_changes = [
                    {**change, "action": "no_op"}
                    for change in applied_diff.get("changes", [])
                ]
                diff = {
                    "summary": {
                        "create": 0,
                        "update": 0,
                        "no_op": len(replay_changes),
                        "conflict": 0,
                    },
                    "changes": replay_changes,
                    "conflicts": [],
                }
                return {
                    "mode": "preview",
                    "status": "ready",
                    **header,
                    "priorRunId": prior["id"],
                    "databaseFingerprint": _database_fingerprint(
                        connection, header["sourceItemId"]
                    ),
                    "diff": diff,
                }
        diff = _build_diff(connection, payload)
        return {
            "mode": "preview",
            "status": "conflict" if diff["conflicts"] else "ready",
            **header,
            "databaseFingerprint": _database_fingerprint(connection, header["sourceItemId"]),
            "diff": diff,
        }


def _insert(connection, table: str, target: dict[str, Any]) -> None:
    columns = tuple(target)
    placeholders = ",".join("?" for _ in columns)
    connection.execute(
        f"INSERT INTO {table}({','.join(columns)}) VALUES ({placeholders})",
        tuple(target[column] for column in columns),
    )


def _update(connection, table: str, object_id: str, target: dict[str, Any]) -> None:
    columns = tuple(target)
    connection.execute(
        f"UPDATE {table} SET {','.join(f'{column}=?' for column in columns)} WHERE id=?",
        tuple(target[column] for column in columns) + (object_id,),
    )


def _audit(connection, actor: str, action: str, object_type: str, object_id: str, before: object, after: object, rationale: str) -> None:
    connection.execute(
        """INSERT INTO audit_log(
               occurred_at,actor,action,object_type,object_id,before_json,after_json,rationale
           ) VALUES (?,?,?,?,?,?,?,?)""",
        (
            utc_now(), actor, action, object_type, object_id,
            _canonical(before) if before is not None else None,
            _canonical(after) if after is not None else None,
            rationale,
        ),
    )


def _ensure_batch(connection, payload: dict[str, Any], header: dict[str, Any], now: str) -> None:
    batch = payload["batch"]
    batch_id = batch["id"]
    existing = _row(connection, "reassessment_batches", batch_id)
    target = {
        "id": batch_id,
        "batch_key": batch["batchKey"],
        "case_id": batch.get("caseId"),
        "question_id": batch.get("questionId"),
        "input_fingerprint": header["inputFingerprint"],
        "scope_json": _canonical(batch.get("scope", {"sourceItemId": header["sourceItemId"]})),
        "stopping_rule": batch["stoppingRule"],
        "supersedes_batch_id": batch.get("supersedesBatchId"),
        "created_by": batch["createdBy"],
        "created_at": now,
    }
    if existing is None:
        _insert(connection, "reassessment_batches", target)
        connection.execute(
            """INSERT INTO reassessment_batch_states(
                   id,batch_id,revision,status,rationale,created_by,created_at
               ) VALUES (?,?,?,?,?,?,?)""",
            (stable_id("reassessment_state", batch_id, "1"), batch_id, 1, "in_review", "Manifest przyjęty do transakcyjnego writebacku.", batch["createdBy"], now),
        )
    else:
        immutable = {key: value for key, value in target.items() if key != "created_at"}
        if _subset(existing, immutable) != immutable:
            raise RuntimeError("Istniejący batch ma inną niezmienną treść")


def apply_mapping_writeback(root: Path, manifest_path: Path) -> dict[str, Any]:
    payload = _load_manifest(manifest_path)
    initialize(root)
    now = utc_now()
    actor = _required_text(payload.get("actor"), "actor")
    rationale = _required_text(payload.get("rationale"), "rationale")
    with connect(root) as connection:
        require_tables(connection, REQUIRED_TABLES)
        connection.execute("BEGIN IMMEDIATE")
        try:
            header = _validate_header(connection, payload)
            prior = connection.execute(
                "SELECT id,status,diff_json FROM mapping_writeback_runs WHERE manifest_id=? AND mode='apply' AND status='applied' ORDER BY created_at LIMIT 1",
                (stable_id("mapping_manifest", header["manifestFingerprint"]),),
            ).fetchone()
            if prior:
                operations = _object(payload.get("operations", {}), "operations")
                if _list(
                    operations.get(PERSON_OCCURRENCE_QUARANTINE_GROUP),
                    f"operations.{PERSON_OCCURRENCE_QUARANTINE_GROUP}",
                ):
                    replay_conflicts = _quarantine_replay_conflicts(
                        connection, payload
                    )
                    if replay_conflicts:
                        raise RuntimeError(
                            "Wcześniej zastosowana kwarantanna ma drift stanu; "
                            "idempotentny replay został przerwany"
                        )
                connection.rollback()
                return {
                    "mode": "apply",
                    "status": "no_op",
                    **header,
                    "priorRunId": prior["id"],
                    "diff": json.loads(prior["diff_json"]),
                }
            diff = _build_diff(connection, payload)
            if diff["conflicts"]:
                raise RuntimeError("Writeback ma konflikty; apply został przerwany")
            quarantine_entries = _list(
                _object(payload.get("operations", {}), "operations").get(
                    PERSON_OCCURRENCE_QUARANTINE_GROUP
                ),
                f"operations.{PERSON_OCCURRENCE_QUARANTINE_GROUP}",
            )
            declared_quarantine_impacts = (
                _normalize_quarantine_impact_assessment(payload)
                if quarantine_entries
                else []
            )
            _ensure_batch(connection, payload, header, now)
            batch_id = payload["batch"]["id"]
            manifest_id = stable_id("mapping_manifest", header["manifestFingerprint"])
            connection.execute(
                """INSERT INTO mapping_writeback_manifests(
                       id,batch_id,source_item_id,schema_version,manifest_fingerprint,
                       input_fingerprint,expected_database_schema,expected_review_revision,
                       manifest_json,created_by,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (manifest_id, batch_id, header["sourceItemId"], 1, header["manifestFingerprint"], header["inputFingerprint"], header["databaseSchema"], header["reviewRevision"], _canonical(payload), actor, now),
            )
            for change in diff["changes"]:
                if change["action"] == "create":
                    _insert(connection, change["table"], change["target"])
                    _audit(connection, actor, "mapping_writeback_create", change["table"], change["id"], None, change["target"], rationale)
                elif change["action"] == "update":
                    before = _row(connection, change["table"], change["id"])
                    _update(connection, change["table"], change["id"], change["target"])
                    if change["group"] == PERSON_OCCURRENCE_QUARANTINE_GROUP:
                        after = dict(before or {})
                        after.update(change["target"])
                        _audit(
                            connection,
                            actor,
                            "mapping_writeback_person_occurrence_quarantine",
                            change["table"],
                            change["id"],
                            before,
                            after,
                            rationale,
                        )
                    else:
                        _audit(connection, actor, "mapping_writeback_update", change["table"], change["id"], _subset(before or {}, change["target"]), change["target"], rationale)
                impact_id = stable_id("reassessment_impact", batch_id, change["table"], change["id"])
                connection.execute(
                    """INSERT INTO reassessment_impacts(
                           id,batch_id,revision,object_type,object_id,impact_type,
                           planned_action,writeback_status,preview_json,rationale,
                           created_by,created_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (impact_id, batch_id, 1, change["table"], change["id"], "mapping", change["action"], "not_affected" if change["action"] == "no_op" else "applied", _canonical(change), rationale, actor, now),
                )
            for impact in declared_quarantine_impacts:
                impact_id = stable_id(
                    "reassessment_impact_declared",
                    batch_id,
                    impact["category"],
                    impact["objectType"],
                    impact["objectId"],
                    impact["impactType"],
                )
                connection.execute(
                    """INSERT INTO reassessment_impacts(
                           id,batch_id,revision,object_type,object_id,impact_type,
                           planned_action,writeback_status,preview_json,rationale,
                           created_by,created_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        impact_id,
                        batch_id,
                        1,
                        impact["objectType"],
                        impact["objectId"],
                        impact["impactType"],
                        impact["plannedAction"],
                        impact["writebackStatus"],
                        _canonical(
                            {
                                "category": impact["category"],
                                **impact["preview"],
                            }
                        ),
                        impact["rationale"],
                        actor,
                        now,
                    ),
                )
            source_item_id = header["sourceItemId"]
            run_id = stable_id("mapping_run", manifest_id, "apply")
            writeback_link = {
                "kind": "mapping_writeback",
                "runId": run_id,
                "manifestFingerprint": header["manifestFingerprint"],
                "domainCompletionFingerprint": (
                    diff["domainCompletion"].get("acceptedNamedPeopleFingerprint")
                    if diff.get("domainCompletion")
                    else None
                ),
            }
            item_id = stable_id("reassessment_item", batch_id, "source_item", source_item_id)
            connection.execute(
                """INSERT INTO reassessment_items(
                       id,batch_id,item_type,object_type,object_id,source_item_id,
                       inclusion_reason,risk_level,cost_class,input_fingerprint,
                       metadata_json,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (item_id, batch_id, "document_mapping", "source_item", source_item_id, source_item_id, "Pilot pełnego mapowania dokumentu po agent-first review.", payload.get("riskLevel", "R2"), payload.get("costClass", "B"), header["inputFingerprint"], "{}", now),
            )
            connection.execute(
                """INSERT INTO reassessment_item_results(
                       id,item_id,revision,status,result_json,rationale,created_by,created_at
                   ) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    stable_id("reassessment_result", item_id, "1"),
                    item_id,
                    1,
                    "completed",
                    _canonical(
                        {
                            "summary": diff["summary"],
                            "domainCompletion": diff.get("domainCompletion"),
                            "writebackLink": writeback_link,
                        }
                    ),
                    rationale,
                    actor,
                    now,
                ),
            )
            extraction = _object(payload.get("extractionState"), "extractionState")
            extraction_status = _required_text(extraction.get("status"), "extractionState.status")
            prior_extraction = connection.execute(
                "SELECT id,revision FROM document_extraction_states WHERE source_item_id=? ORDER BY revision DESC LIMIT 1",
                (source_item_id,),
            ).fetchone()
            extraction_revision = int(prior_extraction["revision"]) + 1 if prior_extraction else 1
            connection.execute(
                """INSERT INTO document_extraction_states(
                       id,source_item_id,revision,supersedes_state_id,batch_id,
                       reading_review_decision_id,status,source_fingerprint,
                       extraction_fingerprint,rationale,created_by,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (stable_id("extraction_state", source_item_id, str(extraction_revision), header["manifestFingerprint"]), source_item_id, extraction_revision, prior_extraction["id"] if prior_extraction else None, batch_id, extraction.get("readingReviewDecisionId"), extraction_status, header["inputFingerprint"], _fingerprint({"operations": payload.get("operations", {}), "status": extraction_status, "domainCompletion": extraction.get("domainCompletion")}), _required_text(extraction.get("rationale"), "extractionState.rationale"), actor, now),
            )
            first_state = connection.execute(
                "SELECT id,revision FROM reassessment_batch_states WHERE batch_id=? ORDER BY revision DESC LIMIT 1",
                (batch_id,),
            ).fetchone()
            connection.execute(
                """INSERT INTO reassessment_batch_states(
                       id,batch_id,revision,supersedes_state_id,status,rationale,
                       created_by,created_at
                   ) VALUES (?,?,?,?,?,?,?,?)""",
                (stable_id("reassessment_state", batch_id, str(int(first_state["revision"]) + 1)), batch_id, int(first_state["revision"]) + 1, first_state["id"], "completed", "Transakcyjny writeback zakończony; zależne impacts mają jawne statusy terminalne i nie wykonano decyzji identity.", actor, now),
            )
            connection.execute(
                """INSERT INTO mapping_writeback_runs(
                       id,manifest_id,mode,status,database_fingerprint,diff_json,
                       actor,created_at
                   ) VALUES (?,?, 'apply','applied',?,?,?,?)""",
                (run_id, manifest_id, _database_fingerprint(connection, source_item_id), _canonical(diff), actor, now),
            )
            _audit(connection, actor, "mapping_writeback_apply", "mapping_manifest", manifest_id, None, {"batchId": batch_id, "diff": diff["summary"]}, rationale)
            connection.commit()
            return {"mode": "apply", "status": "applied", **header, "runId": run_id, "diff": diff}
        except Exception:
            connection.rollback()
            raise


__all__ = [
    "apply_mapping_writeback",
    "mapping_writeback_preview",
    "source_input_fingerprint",
]
