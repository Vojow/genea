from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..reading_verification_card import project_reading_verification_card
from ..review import READING_REVIEW_STATUSES
from .common import event_group, json_array, json_object
from .media import local_originals
from .privacy import PrivacyContext


@dataclass(frozen=True)
class SourcesProjection:
    items: list[dict[str, Any]]
    allowed_ids: set[str]


def _unique_text(values: list[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _field_review_summary(fields: Any) -> dict[str, Any]:
    """Count only explicit, recognized field-review backing.

    Legacy ``reviewStatus`` is usable only when ``status`` is absent. Unknown
    status strings (including pipeline ``missing``/``machine_proposal``),
    malformed fields or statuses, and conflicting dual keys remain unmapped.
    Literal absence such as ``absenceStatus=NotStated`` says nothing about
    review. Partial counts describe the mapped subset, never full coverage.
    """

    statuses: list[str] = []
    if isinstance(fields, list):
        for field in fields:
            if not isinstance(field, dict):
                continue
            status = field.get("status") if "status" in field else field.get("reviewStatus")
            if not isinstance(status, str) or status not in READING_REVIEW_STATUSES:
                continue
            if "status" in field and "reviewStatus" in field:
                legacy = field["reviewStatus"]
                if not isinstance(legacy, str) or legacy != status:
                    continue
            statuses.append(status)

    field_count = len(fields) if isinstance(fields, list) else None
    unmapped_count = field_count - len(statuses) if field_count is not None else None
    state = "unknown" if not statuses else "partial" if unmapped_count else "known"
    return {
        "fieldCount": field_count,
        "fieldReviewState": state,
        "agentApprovedFieldCount": (
            sum(status in {"agent_consensus", "agent_adjudicated"} for status in statuses)
            if statuses else None
        ),
        "singlePassFieldCount": statuses.count("agent_single_pass") if statuses else None,
        "humanReviewRequiredFieldCount": statuses.count("human_review_required") if statuses else None,
        "humanReviewedFieldCount": statuses.count("human_reviewed") if statuses else None,
        "unmappedFieldCount": unmapped_count,
    }


def enrich_source_record_summaries(
    sources: list[dict[str, Any]], events: list[dict[str, Any]]
) -> None:
    """Add an explicit, provenance-aware record summary for agents and UI.

    Missing canonical source fields stay missing.  A unique linked event may
    supply a display value, but its basis is exported so consumers cannot
    mistake a presentation fallback for a database assertion.
    """

    events_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        source_id = event.get("sourceId")
        if source_id:
            events_by_source[str(source_id)].append(event)

    for source in sources:
        linked_events = events_by_source.get(source["id"], [])
        metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
        event_dates = _unique_text(
            [event.get("dateFrom") or event.get("date") for event in linked_events]
        )
        date = source.get("date")
        date_basis = "source_item.date_text" if date else None
        if not date and len(event_dates) == 1:
            date = event_dates[0]
            date_basis = "linked_event.date_from"

        record_number = next(
            (
                str(metadata[key]).strip()
                for key in ("actNumber", "act_number", "recordNumber", "record_number")
                if metadata.get(key)
            ),
            None,
        )
        record_number_basis = "source_item.metadata" if record_number else None
        if not record_number:
            match = re.search(r"(?<!\d)(\d{1,5}\s*/\s*\d{4})(?!\d)", source.get("title") or "")
            if match:
                record_number = re.sub(r"\s+", "", match.group(1))
                record_number_basis = "source_item.title"

        places = _unique_text(
            [
                place
                for event in linked_events
                for place in (
                    event.get("originalPlaces")
                    if isinstance(event.get("originalPlaces"), list)
                    else [event.get("place")]
                )
            ]
        )
        event_types = _unique_text(
            [
                event_type
                for event in linked_events
                for event_type in (
                    event.get("originalTypes")
                    if isinstance(event.get("originalTypes"), list)
                    else [event.get("type")]
                )
            ]
        )
        event_groups = _unique_text([event_group(value) for value in event_types])
        citation_locators = _unique_text(
            [citation.get("locator") for citation in source.get("citations", [])]
        )
        participant_ids = {
            str(participant.get("occurrenceId"))
            for event in linked_events
            for participant in event.get("participants", [])
            if participant.get("occurrenceId")
        }
        repository = metadata.get("repository") or metadata.get("legacy_service")
        if not repository and source.get("url"):
            repository = urlparse(str(source["url"])).hostname

        missing_fields = []
        if not source.get("date"):
            missing_fields.append("source_date")
        if not source.get("locator"):
            missing_fields.append("source_locator")

        source["recordSummary"] = {
            "sourceType": source.get("type"),
            "eventGroups": event_groups,
            "eventTypes": event_types,
            "date": date,
            "dateBasis": date_basis,
            "recordNumber": record_number,
            "recordNumberBasis": record_number_basis,
            "places": places,
            "repository": repository,
            "canonicalLocator": source.get("locator"),
            "citationLocators": citation_locators,
            "eventCount": len(linked_events),
            "participantCount": len(participant_ids),
            "missingCanonicalFields": missing_fields,
        }


def project_sources(
    root: Path, connection: sqlite3.Connection, privacy: PrivacyContext
) -> SourcesProjection:
    source_rows = connection.execute(
        """SELECT id,parent_id,title,item_type,date_text,locator,source_url,citation_text,
                  source_quality,verification_status,metadata_json
           FROM source_items ORDER BY title,id"""
    ).fetchall()
    restricted_sources = privacy.restricted_ids("source_item")
    allowed_source_ids = {
        row["id"] for row in source_rows if row["id"] not in restricted_sources
    }
    originals, _ = local_originals(root, connection, allowed_source_ids)

    reading_rows = connection.execute(
        """SELECT r.id AS reading_id,r.source_item_id,r.reading_type,
                  r.status AS reading_status,r.model_or_reviewer,
                  rv.id AS version_id,rv.version_number,
                  rv.status AS version_status,rv.created_at,
                  ts.id AS segment_id,ts.artifact_id,ts.sequence_number,ts.language,
                  ts.page_label,ts.text
           FROM readings r
           JOIN reading_versions rv ON rv.reading_id=r.id
           LEFT JOIN transcript_segments ts ON ts.reading_version_id=rv.id
           ORDER BY r.source_item_id,r.reading_type,rv.version_number,
                    ts.sequence_number"""
    ).fetchall()
    versions: dict[str, dict[str, Any]] = {}
    source_version_ids: dict[str, list[str]] = defaultdict(list)
    for row in reading_rows:
        if row["source_item_id"] not in allowed_source_ids:
            continue
        version = versions.setdefault(
            row["version_id"],
            {
                "id": row["version_id"],
                "sourceItemId": row["source_item_id"],
                "readingId": row["reading_id"],
                "type": row["reading_type"],
                "readingStatus": row["reading_status"],
                "version": row["version_number"],
                "status": row["version_status"],
                "author": row["model_or_reviewer"],
                "createdAt": row["created_at"],
                "segments": [],
            },
        )
        if row["version_id"] not in source_version_ids[row["source_item_id"]]:
            source_version_ids[row["source_item_id"]].append(row["version_id"])
        if row["segment_id"]:
            version["segments"].append(
                {
                    "id": row["segment_id"],
                    "artifactId": row["artifact_id"],
                    "sequence": row["sequence_number"],
                    "language": row["language"],
                    "page": row["page_label"],
                    "text": row["text"],
                }
            )
    referenced_segments: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in connection.execute(
        """SELECT source_item_id,reading_version_id,transcript_segment_id
             FROM person_occurrences
            WHERE reading_version_id IS NOT NULL
           UNION
           SELECT source_item_id,reading_version_id,transcript_segment_id
             FROM source_assertions
            WHERE reading_version_id IS NOT NULL"""
    ):
        source_item_id = row["source_item_id"]
        version_id = row["reading_version_id"]
        if source_item_id not in allowed_source_ids or version_id not in versions:
            continue
        if version_id not in source_version_ids[source_item_id]:
            source_version_ids[source_item_id].append(version_id)
        if row["transcript_segment_id"]:
            referenced_segments[(source_item_id, version_id)].add(
                row["transcript_segment_id"]
            )
    artifact_lookup = {
        artifact["id"]: artifact
        for source_artifacts in originals.values()
        for artifact in source_artifacts
    }
    children: dict[str, list[str]] = defaultdict(list)
    for row in source_rows:
        if row["parent_id"] and row["id"] in allowed_source_ids:
            children[row["parent_id"]].append(row["id"])

    coverage: dict[str, list[dict[str, Any]]] = defaultdict(list)
    coverage_table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_coverage_assessments'"
    ).fetchone()
    if coverage_table:
        for row in connection.execute(
            """SELECT assessment.*
                 FROM source_coverage_assessments assessment
                WHERE NOT EXISTS (
                    SELECT 1 FROM source_coverage_assessments newer
                     WHERE newer.source_item_id=assessment.source_item_id
                       AND newer.coverage_key=assessment.coverage_key
                       AND newer.revision>assessment.revision
                )
                ORDER BY assessment.source_item_id,assessment.dimension,
                         assessment.coverage_level,assessment.coverage_key"""
        ):
            if row["source_item_id"] not in allowed_source_ids:
                continue
            coverage[row["source_item_id"]].append(
                {
                    "id": row["id"],
                    "key": row["coverage_key"],
                    "revision": row["revision"],
                    "dimension": row["dimension"],
                    "level": row["coverage_level"],
                    "recordType": row["record_type"],
                    "scope": row["scope_text"],
                    "unitKind": row["unit_kind"],
                    "completed": row["completed_units"],
                    "total": row["total_units"],
                    "status": row["status"],
                    "systematic": bool(row["systematic"]),
                    "qaStatus": row["qa_status"],
                    "basisType": row["basis_type"],
                    "basisRef": row["basis_ref"],
                    "metadata": json_object(row["metadata_json"]),
                    "rationale": row["rationale"],
                }
            )
    citations: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in connection.execute(
        """SELECT id,source_item_id,citation_text,locator,accessed_at
           FROM citations ORDER BY source_item_id,created_at,id"""
    ):
        if row["source_item_id"] in allowed_source_ids:
            citations[row["source_item_id"]].append(
                {
                    "id": row["id"],
                    "text": row["citation_text"],
                    "locator": row["locator"],
                    "accessedAt": row["accessed_at"],
                }
            )
    comparisons: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in connection.execute(
        """SELECT id,source_item_id,blind_version_id,contextual_version_id,
                  reviewer_version_id,differences_json,status,created_at,reviewed_at
             FROM reading_comparisons
            ORDER BY source_item_id,created_at,id"""
    ):
        if row["source_item_id"] in allowed_source_ids:
            comparisons[row["source_item_id"]].append(
                {
                    "id": row["id"],
                    "blindVersionId": row["blind_version_id"],
                    "contextualVersionId": row["contextual_version_id"],
                    "reviewerVersionId": row["reviewer_version_id"],
                    "differences": json_array(row["differences_json"]),
                    "status": row["status"],
                    "createdAt": row["created_at"],
                    "reviewedAt": row["reviewed_at"],
                }
            )
    review_workflows: dict[str, dict[str, Any]] = {}
    input_counts: dict[str, dict[str, int]] = defaultdict(
        lambda: {"inputCount": 0, "blindInputCount": 0, "agentInputCount": 0}
    )
    review_input_signatures: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in connection.execute(
        """SELECT i.decision_id,i.input_role,i.reading_version_id,i.reader_id,i.run_id
             FROM reading_review_inputs i
             JOIN reading_review_decisions d ON d.id=i.decision_id
            WHERE NOT EXISTS (
                SELECT 1 FROM reading_review_decisions newer
                 WHERE newer.source_item_id=d.source_item_id
                   AND newer.revision>d.revision
            )"""
    ):
        counts = input_counts[row["decision_id"]]
        review_input_signatures[row["decision_id"]].append(
            {
                "role": row["input_role"],
                "readingVersionId": row["reading_version_id"],
                "readerId": row["reader_id"],
                "runId": row["run_id"],
            }
        )
        counts["inputCount"] += 1
        if row["input_role"] in {"blind_primary", "blind_secondary"}:
            counts["blindInputCount"] += 1
        if row["input_role"] != "human_review":
            counts["agentInputCount"] += 1
    for row in connection.execute(
        """SELECT d.id,d.source_item_id,d.revision,d.status,d.decided_by_kind,
                  d.rationale,d.decision_json,d.created_at
             FROM reading_review_decisions d
            WHERE NOT EXISTS (
                SELECT 1 FROM reading_review_decisions newer
                 WHERE newer.source_item_id=d.source_item_id
                   AND newer.revision>d.revision
            )
            ORDER BY d.source_item_id,d.revision"""
    ):
        if row["source_item_id"] not in allowed_source_ids:
            continue
        decision = json_object(row["decision_json"])
        fields = decision.get("fields") if isinstance(decision.get("fields"), list) else []
        field_summary = _field_review_summary(decision.get("fields"))
        counts = input_counts[row["id"]]
        review_workflows[row["source_item_id"]] = {
            "id": row["id"],
            "revision": row["revision"],
            "status": row["status"],
            "decidedByKind": row["decided_by_kind"],
            "rationale": row["rationale"],
            "createdAt": row["created_at"],
            **(
                {"pipelineRunId": decision["pipelineRunId"]}
                if isinstance(decision.get("pipelineRunId"), str)
                and decision["pipelineRunId"].strip()
                else {}
            ),
            "_inputSignatures": review_input_signatures[row["id"]],
            "fields": fields,
            "summary": {
                **counts,
                **field_summary,
                "hasAgentApproval": (
                    (field_summary["agentApprovedFieldCount"] or 0) > 0
                    or row["status"] in {"agent_consensus", "agent_adjudicated"}
                ),
                "needsHumanReview": (
                    (field_summary["humanReviewRequiredFieldCount"] or 0) > 0
                    or row["status"] == "human_review_required"
                ),
            },
        }
    pipeline_runs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    pipeline_table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='reading_pipeline_runs'"
    ).fetchone()
    if pipeline_table:
        pipeline_fields: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for field in connection.execute(
            """SELECT pf.run_id,pr.source_item_id,pf.field_reading_id,pf.field_path,pf.role,
                      pf.normalized_value,pf.confidence,pf.source_quote,
                      pf.source_segment_id,pf.region_json,pf.review_status,
                      fr.value_text,fr.alternatives_json,fr.reading_certainty,fr.notes
                 FROM reading_pipeline_fields pf
                 JOIN reading_pipeline_runs pr ON pr.id=pf.run_id
                 JOIN field_readings fr ON fr.id=pf.field_reading_id
                ORDER BY pf.run_id,pf.field_path"""
        ):
            if field["source_item_id"] not in allowed_source_ids:
                continue
            notes = json_object(field["notes"])
            pipeline_fields[field["run_id"]].append(
                {
                    "path": field["field_path"],
                    "role": field["role"],
                    "value": field["value_text"],
                    "normalizedValue": field["normalized_value"],
                    "confidence": field["confidence"],
                    "certainty": field["reading_certainty"],
                    "alternatives": json_array(field["alternatives_json"]),
                    "materialAlternatives": notes.get("materialAlternatives", []),
                    "sourceQuote": field["source_quote"],
                    "sourceSegmentId": field["source_segment_id"],
                    "bbox": json_object(field["region_json"]),
                    "criticality": notes.get("criticality"),
                    "reviewStatus": field["review_status"],
                }
            )
        escalations: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for escalation in connection.execute(
            """SELECT e.id,e.run_id,e.field_path,e.reason_code,e.priority,
                      e.recommended_role,e.details_json,r.source_item_id
                 FROM reading_pipeline_escalations e
                 JOIN reading_pipeline_runs r ON r.id=e.run_id
                ORDER BY e.run_id,e.priority,e.reason_code,e.field_path,e.id"""
        ):
            if escalation["source_item_id"] not in allowed_source_ids:
                continue
            escalations[escalation["run_id"]].append(
                {
                    "id": escalation["id"],
                    "fieldPath": escalation["field_path"],
                    "reasonCode": escalation["reason_code"],
                    "priority": escalation["priority"],
                    "recommendedRole": escalation["recommended_role"],
                    "details": json_object(escalation["details_json"]),
                }
            )
        for run in connection.execute(
            """SELECT id,source_item_id,artifact_id,provider,model,pipeline_version,
                      document_type,source_language,status,completeness,metrics_json,
                      provenance_json,comparison_json,image_quality_json,
                      role_expectations_json,independent_readings_json,
                      command_id,created_at
                 FROM reading_pipeline_runs
                ORDER BY source_item_id,created_at DESC,id"""
        ):
            if run["source_item_id"] not in allowed_source_ids:
                continue
            comparison = json_object(run["comparison_json"])
            independent_readings = json_array(run["independent_readings_json"])
            run_escalations = escalations[run["id"]]
            candidate_review = review_workflows.get(run["source_item_id"])
            review_overlay = None
            if candidate_review is not None:
                explicit_pipeline_run_id = candidate_review.get("pipelineRunId")
                blind_inputs = [
                    item
                    for item in candidate_review.get("_inputSignatures", [])
                    if item.get("role") in {"blind_primary", "blind_secondary"}
                ]
                pipeline_signatures = {
                    (item.get("readerId"), item.get("runId"))
                    for item in independent_readings
                    if item.get("readerId") and item.get("runId")
                }
                review_signatures = {
                    (item.get("readerId"), item.get("runId"))
                    for item in blind_inputs
                    if item.get("readerId") and item.get("runId")
                }
                exact_signature_match = (
                    len(pipeline_signatures) >= 2
                    and pipeline_signatures == review_signatures
                    and len(blind_inputs) == len(review_signatures)
                )
                review_matches_run = (
                    explicit_pipeline_run_id == run["id"]
                    if explicit_pipeline_run_id
                    else exact_signature_match
                )
                if review_matches_run:
                    review_overlay = {
                        key: value
                        for key, value in candidate_review.items()
                        if not key.startswith("_")
                    }
            verification_card = project_reading_verification_card(
                source_item_id=run["source_item_id"],
                artifact_id=run["artifact_id"],
                fields=pipeline_fields[run["id"]],
                independent_readings=independent_readings,
                comparison=comparison,
                issues=run_escalations,
                review_overlay=review_overlay,
            )
            missing_field_count = sum(
                field.get("reviewStatus") == "missing"
                for field in pipeline_fields[run["id"]]
            )
            pipeline_human_review_required_count = sum(
                field.get("reviewStatus") == "human_review_required"
                for field in pipeline_fields[run["id"]]
            )
            pipeline_runs[run["source_item_id"]].append(
                {
                    "id": run["id"],
                    "artifactId": run["artifact_id"],
                    "provider": run["provider"],
                    "model": run["model"],
                    "pipelineVersion": run["pipeline_version"],
                    "documentType": run["document_type"],
                    "sourceLanguage": run["source_language"],
                    "status": run["status"],
                    "completeness": run["completeness"],
                    "metrics": json_object(run["metrics_json"]),
                    "provenance": json_object(run["provenance_json"]),
                    "comparison": comparison,
                    "imageQuality": json_object(run["image_quality_json"]),
                    "roleExpectations": json.loads(run["role_expectations_json"] or "[]"),
                    "independentReadings": independent_readings,
                    "commandId": run["command_id"],
                    "createdAt": run["created_at"],
                    "summary": {
                        "fieldCount": verification_card["summary"]["fieldCount"],
                        "humanReviewRequiredFieldCount": pipeline_human_review_required_count,
                        "missingFieldCount": missing_field_count,
                        "escalationCount": len(run_escalations),
                        "independentReadingCount": len(independent_readings),
                        "disagreementCount": verification_card["summary"]["disputedCount"],
                    },
                    "escalations": run_escalations,
                    "verificationCard": verification_card,
                }
            )
    items = []
    for row in source_rows:
        if row["id"] not in allowed_source_ids:
            continue
        projected_readings = []
        inherited_artifact_ids: set[str] = set()
        for version_id in source_version_ids.get(row["id"], []):
            version = versions[version_id]
            segment_ids = referenced_segments.get((row["id"], version_id))
            inherited = version["sourceItemId"] != row["id"]
            segments = version["segments"]
            if inherited and segment_ids:
                segments = [segment for segment in segments if segment["id"] in segment_ids]
            for segment in segments:
                if segment.get("artifactId"):
                    inherited_artifact_ids.add(segment["artifactId"])
            projected_readings.append(
                {
                    key: value
                    for key, value in version.items()
                    if key not in {"sourceItemId", "segments"}
                }
                | {
                    "segments": [
                        {key: value for key, value in segment.items() if key != "artifactId"}
                        for segment in segments
                    ],
                    **(
                        {"inheritedFromSourceId": version["sourceItemId"]}
                        if inherited
                        else {}
                    ),
                }
            )
        item_artifacts = list(originals.get(row["id"], []))
        own_artifact_ids = {artifact["id"] for artifact in item_artifacts}
        item_artifacts.extend(
            artifact_lookup[artifact_id]
            for artifact_id in sorted(inherited_artifact_ids - own_artifact_ids)
            if artifact_id in artifact_lookup
        )
        item = {
            "id": row["id"],
            "title": row["title"],
            "type": row["item_type"],
            "date": row["date_text"],
            "locator": row["locator"],
            "url": row["source_url"],
            "citation": row["citation_text"],
            "quality": row["source_quality"],
            "status": row["verification_status"],
            "metadata": json_object(row["metadata_json"]),
            "availableLocally": bool(item_artifacts),
            "artifactCount": len(item_artifacts),
            "artifacts": item_artifacts,
            "citations": citations.get(row["id"], []),
            "readings": projected_readings,
        }
        if row["parent_id"]:
            item["parentId"] = row["parent_id"]
        if children.get(row["id"]):
            item["childIds"] = sorted(children[row["id"]])
        if coverage.get(row["id"]):
            item["coverage"] = coverage[row["id"]]
        if comparisons.get(row["id"]):
            item["comparisons"] = comparisons[row["id"]]
        if review_workflows.get(row["id"]):
            item["reviewWorkflow"] = {
                key: value
                for key, value in review_workflows[row["id"]].items()
                if not key.startswith("_")
            }
        if pipeline_runs.get(row["id"]):
            item["readingPipelineRuns"] = pipeline_runs[row["id"]]
        items.append(item)
    return SourcesProjection(items, allowed_source_ids)
