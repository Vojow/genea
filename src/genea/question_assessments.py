"""Read-only projection of versioned canonical question assessments.

The embedded log envelope is historical provenance, not a live matrix or proof.
Only the newest structured candidate is checked; an invalid newer candidate
never revives an older ready assessment. No referenced artifact is opened.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import PurePosixPath
from typing import Any


DISPOSITIONS = frozenset({"defer_specific_material", "identity_linked_biography_partial",
    "ready_known_source_followup", "source_supported_probable"})
PUBLIC_PRIVACY = frozenset({"public_historical", "deceased"})
WORK_TYPES = frozenset({"problem", "work_package", "task", "review", "waiting"})
WORK_STATUSES = frozenset({"planned", "ready", "in_progress", "blocked", "completed"})


def _json(summary: str) -> Any:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate assessment JSON key")
            result[key] = value
        return result
    return json.loads(summary, object_pairs_hook=unique)


def assessment_candidate(summary: str | None) -> bool:
    """Recognize schema-bearing logs, including malformed structured envelopes."""
    if not isinstance(summary, str):
        return False
    try:
        value = _json(summary)
    except ValueError:
        return summary.lstrip().startswith(("{", "["))
    return isinstance(value, list) or isinstance(value, dict) and ("assessment" in value or value.get("version") in (60, 69))


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _sha(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _reference(value: Any) -> bool:
    if not _text(value):
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts and "\\" not in value and path.parts[:3] == ("research", "records", "reassessments") and len(path.parts) > 3


def _binding(envelope: dict[str, Any], version: int) -> bool:
    binding = envelope.get("binding")
    if not isinstance(binding, dict) or not _sha(binding.get("writerSHA256")):
        return False
    hashes = binding.get("inputHashes" if version == 60 else "inputs")
    if not isinstance(hashes, dict) or not hashes or not all(_reference(ref) and _sha(sha) for ref, sha in hashes.items()):
        return False
    if version == 69:
        return _text(binding.get("auditRunId")) and _reference(envelope.get("matrixRef")) and envelope["matrixRef"] in hashes
    reviews = binding.get("reviewRefs")
    return isinstance(reviews, dict) and set(reviews) == {"audit", "correlation"} and all(
        isinstance(ref, dict) and _reference(ref.get("ref")) and _sha(ref.get("sha256")) and _text(ref.get("runId"))
        for ref in reviews.values()) and reviews["audit"]["runId"] != reviews["correlation"]["runId"]


def latest_question_assessment(connection: sqlite3.Connection, question: dict[str, Any]) -> dict[str, Any]:
    """Return additive item fields: absent candidate {}, invalid {assessment: null}."""
    logs = connection.execute("""SELECT * FROM research_log_entries WHERE question_id=?
        ORDER BY searched_at DESC,created_at DESC,id DESC""", (question["id"],))
    candidate = next((dict(row) for row in logs if assessment_candidate(row["result_summary"])), None)
    if candidate is None:
        return {}
    return {"assessment": _validated_assessment(connection, question, candidate)}


def _validated_assessment(connection: sqlite3.Connection, question: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any] | None:
    try:
        envelope = _json(candidate["result_summary"])
    except ValueError:
        return None
    if not isinstance(envelope, dict) or type(envelope.get("version")) is not int or envelope["version"] not in (60, 69):
        return None
    version = envelope["version"]
    assessment = envelope.get("assessment")
    if not isinstance(assessment, dict) or candidate["case_id"] != question["case_id"]:
        return None
    expected = {"questionId": question["id"], "question": question["question"],
        "originalCriterion": question["completion_criterion"], "questionStatus": question["status"]}
    if not all(assessment.get(k) == v for k, v in expected.items()) or assessment.get("originalCriterionPreserved") is not True or envelope.get("questionStateUnchanged") is not True:
        return None
    if not _text(assessment.get("disposition")) or assessment["disposition"] not in DISPOSITIONS or not _text(assessment.get("privacy")) or assessment["privacy"] not in PUBLIC_PRIVACY or not _binding(envelope, version):
        return None
    if not all(_text(assessment.get(k)) for k in ("missingEvidence", "nextAction", "returnCondition")):
        return None
    refs = assessment.get("basisRefs")
    if not isinstance(refs, list) or not refs or not all(_reference(ref) for ref in refs):
        return None
    if "currentSourceContextRef" in assessment and not _reference(assessment["currentSourceContextRef"]):
        return None
    work_id = assessment.get("canonicalWorkId")
    if not _text(work_id) or assessment.get("workId") != work_id:
        return None
    work = connection.execute("SELECT * FROM research_work_items WHERE id=?", (work_id,)).fetchone()
    if work is None or work["question_id"] != question["id"] or work["completion_criterion"] != question["completion_criterion"] or work["item_type"] not in WORK_TYPES or work["status"] not in WORK_STATUSES:
        return None
    scope = connection.execute("""SELECT s.* FROM research_scopes s JOIN research_question_scopes qs ON qs.scope_id=s.id
        WHERE qs.question_id=? AND s.id=?""", (question["id"], work["scope_id"])).fetchone()
    if scope is None or scope["legacy_case_id"] not in (None, question["case_id"]) or scope["status"] not in {"planned", "active", "on_hold", "completed"}:
        return None
    if work["parent_work_item_id"]:
        parent = connection.execute("SELECT scope_id,question_id FROM research_work_items WHERE id=?", (work["parent_work_item_id"],)).fetchone()
        if parent is None or parent["scope_id"] != work["scope_id"] or parent["question_id"] not in (None, question["id"]):
            return None
    return {"schemaVersion": 1, "disposition": assessment["disposition"],
        "missingEvidence": assessment["missingEvidence"], "nextAction": assessment["nextAction"],
        "returnCondition": assessment["returnCondition"],
        "source": {"logEntryId": candidate["id"], "assessmentVersion": version,
            "assessedAt": candidate["searched_at"], "summarySha256": hashlib.sha256(candidate["result_summary"].encode("utf-8")).hexdigest()},
        "scopedWork": {"id": work["id"], "itemType": work["item_type"], "status": work["status"],
            "scopeId": work["scope_id"], "parentWorkItemId": work["parent_work_item_id"]}}
