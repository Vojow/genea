from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .common import birth_detail, json_object
from .privacy import PrivacyContext
from .source_profile_identities import project_source_profile_identities
from ..identity_source_only_curation import source_only_typed_ids
from .family_knowledge import project_family_knowledge


_IDENTITY_DECISION_TIERS = {
    "agent_approved_human_pending",
    "human_confirmed",
}
_HUMAN_CONFIRMATION_ARTIFACT_FIELDS = {
    "version",
    "artifactType",
    "confirmationId",
    "sourceReviewId",
    "sourceReviewSha256",
    "caseId",
    "candidateId",
    "priorAgentCommandId",
    "priorAgentCommandResultSha256",
    "priorAgentRatificationCommandId",
    "priorAgentRatificationResultSha256",
    "priorDecisionId",
    "decision",
    "decidedByKind",
    "identityDecisionTier",
    "decidedBy",
    "decidedAt",
    "rationale",
}
_SUPERSEDING_HUMAN_CONFIRMATION_ARTIFACT_FIELDS = (
    _HUMAN_CONFIRMATION_ARTIFACT_FIELDS
    | {
        "supersedesHumanConfirmationCommandId",
        "supersededHumanConfirmationResultSha256",
    }
)
_RATIFICATION_ARTIFACT_FIELDS = {
    "version",
    "artifactType",
    "ratificationId",
    "sourceReviewId",
    "sourceReviewSha256",
    "caseId",
    "candidateId",
    "priorCommandId",
    "priorDecisionId",
    "decision",
    "decidedByKind",
    "identityDecisionTier",
    "decidedBy",
    "decidedAt",
    "rationale",
    "independentReviewRefs",
}
_HUMAN_CONFIRMATION_RESULT_FIELDS = {
    "operation",
    "status",
    "candidateId",
    "priorAgentCommandId",
    "priorAgentRatificationCommandId",
    "priorDecisionId",
    "identityDecisionTier",
    "writeSet",
    "confirmationId",
    "humanConfirmationArtifactPath",
    "humanConfirmationSha256",
    "sourceReviewPath",
    "sourceReviewSha256",
    "projectionStatus",
}
_SUPERSEDING_HUMAN_CONFIRMATION_RESULT_FIELDS = (
    _HUMAN_CONFIRMATION_RESULT_FIELDS
    | {
        "supersedesHumanConfirmationCommandId",
        "supersededHumanConfirmationResultSha256",
    }
)
_LOCAL_RESOLUTION_RESULT_FIELDS = {
    "operation",
    "operationId",
    "reviewId",
    "caseId",
    "candidateId",
    "status",
    "decision",
    "decidedByKind",
    "identityDecisionTier",
    "decisionId",
    "commandId",
    "inputFingerprint",
    "manifestFingerprint",
    "occurrenceIds",
    "personIds",
    "agentReviewArtifactPath",
    "agentReviewSha256",
    "sourceReviewPath",
    "sourceReviewSha256",
    "independentReviewRefs",
    "writeSet",
    "projectionStatus",
}
_LOCAL_HUMAN_RESOLUTION_RESULT_FIELDS = {
    "operation",
    "operationId",
    "reviewId",
    "caseId",
    "candidateId",
    "status",
    "decision",
    "decidedByKind",
    "identityDecisionTier",
    "decisionId",
    "commandId",
    "inputFingerprint",
    "manifestFingerprint",
    "occurrenceIds",
    "personIds",
    "endpoints",
    "humanReviewArtifactPath",
    "humanReviewSha256",
    "sourceReviewPath",
    "sourceReviewSha256",
    "humanReviewPacketPath",
    "humanReviewPacketSha256",
    "writeSet",
    "projectionStatus",
}
_LOCAL_HUMAN_DECISION_FIELDS = {
    "version",
    "artifactType",
    "reviewId",
    "sourceReviewId",
    "sourceReviewSha256",
    "humanReviewPacketPath",
    "humanReviewPacketSha256",
    "caseId",
    "candidateId",
    "decision",
    "decidedByKind",
    "identityDecisionTier",
    "decidedBy",
    "decidedAt",
    "rationale",
}
_LOCAL_AGENT_DECISION_FIELDS = {
    "version",
    "artifactType",
    "reviewId",
    "sourceReviewId",
    "sourceReviewSha256",
    "caseId",
    "candidateId",
    "decision",
    "decidedByKind",
    "identityDecisionTier",
    "decidedBy",
    "decidedAt",
    "rationale",
    "independentReviewRefs",
}
_LOCAL_REVIEW_REF_FIELDS = {
    "artifactPath",
    "artifactSha256",
    "artifactId",
    "reviewerRole",
    "reviewerRunId",
    "reviewedCandidateIds",
    "verdict",
}
_LOCAL_RATIFICATION_RESULT_FIELDS = {
    "operation",
    "status",
    "candidateId",
    "priorCommandId",
    "priorDecisionId",
    "identityDecisionTier",
    "writeSet",
    "ratificationId",
    "ratificationReviewArtifactPath",
    "ratificationReviewSha256",
    "projectionStatus",
}
_LOCAL_RATIFICATION_ARTIFACT_FIELDS = {
    "version",
    "artifactType",
    "ratificationId",
    "caseId",
    "candidateId",
    "priorCommandId",
    "priorCommandResultSha256",
    "priorDecisionId",
    "agentReviewSha256",
    "sourceReviewId",
    "sourceReviewSha256",
    "decision",
    "decidedByKind",
    "identityDecisionTier",
    "decidedBy",
    "decidedAt",
    "rationale",
}
_LOCAL_REVIEW_ROLES = {
    "source_auditor": "strict_identity_source_audit",
    "evidence_correlator": "strict_identity_evidence_correlation",
}


def _canonical_fingerprint(value: Any) -> str:
    serialized = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _workspace_root(connection: sqlite3.Connection) -> Path | None:
    for row in connection.execute("PRAGMA database_list"):
        name = row[1]
        database_path = row[2]
        if name != "main" or not database_path:
            continue
        resolved = Path(database_path).resolve()
        if resolved.parent.name == "research":
            return resolved.parent.parent
    return None


def _load_hashed_artifact(
    workspace_root: Path | None,
    relative_path: Any,
    expected_sha256: Any,
    expected_fields: set[str] | None,
    *, context=None,
) -> dict[str, Any] | None:
    if (
        workspace_root is None
        or not isinstance(relative_path, str)
        or not relative_path
        or Path(relative_path).is_absolute()
        or not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
    ):
        return None
    allowed_root = (workspace_root / "research/records/reassessments").resolve()
    artifact_path = (workspace_root / relative_path).resolve()
    try:
        artifact_path.relative_to(allowed_root)
    except ValueError:
        return None
    try:
        if context is None:
            if not artifact_path.is_file() or _sha256_file(artifact_path) != expected_sha256:
                return None
            payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        else:
            payload = context.artifact(artifact_path, expected_sha256, "identity review")
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or (
        expected_fields is not None and set(payload) != expected_fields
    ):
        return None
    return payload


def _review_catalog_entries(workspace_root: Path, *, context=None):
    if context is not None:
        yield from context.catalog_entries(workspace_root)
        return
    records_root = (workspace_root / "research/records/reassessments").resolve()
    for path in records_root.rglob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        yield path, payload, None


def _terminal_review_ancestry(
    workspace_root: Path | None,
    target_path: Any,
    target_sha256: Any,
    *, context=None,
) -> tuple[dict[str, Any], set[tuple[str, str]]] | None:
    target = _load_hashed_artifact(
        workspace_root, target_path, target_sha256, None,
        context=context,
    )
    if workspace_root is None or target is None:
        return None
    target_case_id = target.get("caseId")
    if (
        target.get("artifactType") != "strict_identity_review"
        or not isinstance(target_case_id, str)
        or not target_case_id
    ):
        return None
    records: dict[str, tuple[dict[str, Any], str]] = {}
    successors: dict[str, list[str]] = {}
    for path, payload, catalog_sha256 in _review_catalog_entries(workspace_root, context=context):
        if (
            not isinstance(payload, dict)
            or payload.get("artifactType") != "strict_identity_review"
            or payload.get("caseId") != target_case_id
        ):
            continue
        review_id = payload.get("reviewId")
        if not isinstance(review_id, str) or not review_id or review_id in records:
            return None
        records[review_id] = (payload, catalog_sha256 if context is not None else _sha256_file(path))
    target_id = target.get("reviewId")
    if (
        not isinstance(target_id, str)
        or target_id not in records
        or records[target_id][1] != target_sha256
    ):
        return None
    predecessors: dict[str, str | None] = {}
    for review_id, (review, _review_sha256) in records.items():
        version = review.get("version")
        if review.get("operational") is not False or review.get("apply") != "forbidden":
            return None
        predecessor_id = review.get("supersedesReviewId")
        predecessor_sha256 = review.get("supersededArtifactSha256")
        if version == 2 and predecessor_id is None:
            predecessors[review_id] = None
            continue
        if (
            version != 3
            or not isinstance(predecessor_id, str)
            or predecessor_id not in records
            or not isinstance(predecessor_sha256, str)
            or records[predecessor_id][1] != predecessor_sha256
        ):
            return None
        predecessors[review_id] = predecessor_id
        successors.setdefault(predecessor_id, []).append(review_id)
    if any(len(children) > 1 for children in successors.values()):
        return None
    if successors.get(target_id):
        return None
    ancestry: set[tuple[str, str]] = set()
    current_id: str | None = target_id
    while current_id is not None:
        if any(item[0] == current_id for item in ancestry):
            return None
        ancestry.add((current_id, records[current_id][1]))
        current_id = predecessors.get(current_id)
    return target, ancestry


def _validated_human_confirmation_chain(
    connection: sqlite3.Connection,
    workspace_root: Path | None,
    row: sqlite3.Row,
    result: dict[str, Any],
    *, context=None,
) -> tuple[str, str, str] | None:
    result_fields = set(result)
    if result_fields == _HUMAN_CONFIRMATION_RESULT_FIELDS:
        confirmation_version = 1
        artifact_fields = _HUMAN_CONFIRMATION_ARTIFACT_FIELDS
    elif result_fields == _SUPERSEDING_HUMAN_CONFIRMATION_RESULT_FIELDS:
        confirmation_version = 2
        artifact_fields = _SUPERSEDING_HUMAN_CONFIRMATION_ARTIFACT_FIELDS
    else:
        return None
    candidate_id = result.get("candidateId")
    merge_command_id = result.get("priorAgentCommandId")
    ratification_command_id = result.get("priorAgentRatificationCommandId")
    decision_id = result.get("priorDecisionId")
    if not all(
        isinstance(value, str) and value
        for value in (
            candidate_id,
            merge_command_id,
            ratification_command_id,
            decision_id,
        )
    ):
        return None
    if (
        result.get("operation") != "identity_only_human_confirmation"
        or result.get("status") != "human_confirmed_existing_agent_same"
        or result.get("identityDecisionTier") != "human_confirmed"
        or result.get("writeSet") != []
        or result.get("projectionStatus") != "not_required"
        or candidate_id != row["object_id"]
    ):
        return None
    confirmation = _load_hashed_artifact(
        workspace_root,
        result.get("humanConfirmationArtifactPath"),
        result.get("humanConfirmationSha256"),
        artifact_fields,
        context=context,
    )
    if confirmation is None or (
        confirmation.get("version") != confirmation_version
        or confirmation.get("artifactType")
        != "strict_identity_human_confirmation"
        or confirmation.get("confirmationId") != result.get("confirmationId")
        or confirmation.get("candidateId") != candidate_id
        or confirmation.get("priorAgentCommandId") != merge_command_id
        or confirmation.get("priorAgentRatificationCommandId")
        != ratification_command_id
        or confirmation.get("priorDecisionId") != decision_id
        or confirmation.get("decision") != "confirm_agent_identity_only"
        or confirmation.get("decidedByKind") != "human"
        or confirmation.get("identityDecisionTier") != "human_confirmed"
        or confirmation.get("decidedBy") != row["actor"]
        or confirmation.get("rationale") != row["rationale"]
    ):
        return None
    if confirmation_version == 2 and (
        not isinstance(result.get("supersedesHumanConfirmationCommandId"), str)
        or not result["supersedesHumanConfirmationCommandId"]
        or result.get("supersedesHumanConfirmationCommandId")
        != confirmation.get("supersedesHumanConfirmationCommandId")
        or result.get("supersededHumanConfirmationResultSha256")
        != confirmation.get("supersededHumanConfirmationResultSha256")
    ):
        return None

    merge_row = connection.execute(
        "SELECT * FROM curation_commands WHERE command_id=?", (merge_command_id,)
    ).fetchone()
    ratification_row = connection.execute(
        "SELECT * FROM curation_commands WHERE command_id=?",
        (ratification_command_id,),
    ).fetchone()
    if merge_row is None or ratification_row is None:
        return None
    merge_result = json_object(merge_row["result_json"])
    ratification_result = json_object(ratification_row["result_json"])
    if (
        merge_row["command_type"] != "identity_only_external_shell_merge"
        or merge_row["object_type"] != "identity_candidate"
        or merge_row["object_id"] != candidate_id
        or merge_result.get("candidateId") != candidate_id
        or merge_result.get("decisionId") != decision_id
        or merge_result.get("decision") != "same"
        or merge_result.get("status") != "resolved_same"
        or merge_result.get("decidedByKind") != "agent"
        or merge_result.get("identityDecisionTier")
        != "agent_approved_human_pending"
        or _canonical_fingerprint(merge_result)
        != confirmation.get("priorAgentCommandResultSha256")
        or ratification_row["command_type"]
        != "identity_only_agent_ratification"
        or ratification_row["object_type"] != "identity_candidate"
        or ratification_row["object_id"] != candidate_id
        or ratification_row["projection_status"] != "not_required"
        or ratification_result.get("operation")
        != "identity_only_agent_ratification"
        or ratification_result.get("status") != "ratified_existing_agent_same"
        or ratification_result.get("candidateId") != candidate_id
        or ratification_result.get("priorCommandId") != merge_command_id
        or ratification_result.get("priorDecisionId") != decision_id
        or ratification_result.get("identityDecisionTier")
        != "agent_approved_human_pending"
        or ratification_result.get("writeSet") != []
        or ratification_result.get("projectionStatus") != "not_required"
        or _canonical_fingerprint(ratification_result)
        != confirmation.get("priorAgentRatificationResultSha256")
    ):
        return None

    ratification_artifact = _load_hashed_artifact(
        workspace_root,
        ratification_result.get("ratificationReviewArtifactPath"),
        ratification_result.get("ratificationReviewSha256"),
        _RATIFICATION_ARTIFACT_FIELDS,
        context=context,
    )
    terminal_chain = _terminal_review_ancestry(
        workspace_root,
        result.get("sourceReviewPath"),
        result.get("sourceReviewSha256"),
        context=context,
    )
    historical_review = _load_hashed_artifact(
        workspace_root,
        merge_result.get("sourceReviewPath"),
        merge_result.get("sourceReviewSha256"),
        None,
        context=context,
    )
    ratification_review = _load_hashed_artifact(
        workspace_root,
        ratification_result.get("sourceReviewPath"),
        ratification_result.get("sourceReviewSha256"),
        None,
        context=context,
    )
    if (
        terminal_chain is None
        or historical_review is None
        or ratification_review is None
        or ratification_artifact is None
    ):
        return None
    source_review, review_ancestry = terminal_chain
    ratification_review_ref = (
        ratification_artifact.get("sourceReviewId"),
        ratification_artifact.get("sourceReviewSha256"),
    )
    if confirmation_version == 1:
        ratification_review_valid = (
            ratification_result.get("sourceReviewPath")
            == result.get("sourceReviewPath")
            and ratification_result.get("sourceReviewSha256")
            == result.get("sourceReviewSha256")
            and ratification_review_ref
            == (
                confirmation.get("sourceReviewId"),
                confirmation.get("sourceReviewSha256"),
            )
        )
    else:
        ratification_review_valid = (
            ratification_review.get("artifactType")
            == "strict_identity_review"
            and ratification_review.get("reviewId")
            == ratification_artifact.get("sourceReviewId")
            and ratification_review.get("caseId") == confirmation.get("caseId")
            and ratification_review.get("operational") is False
            and ratification_review.get("apply") == "forbidden"
            and ratification_review_ref in review_ancestry
        )
    current_entry_matches = [
        entry
        for entry in source_review.get("entries", [])
        if isinstance(entry, dict) and entry.get("candidateId") == candidate_id
    ]
    current_guardrails = source_review.get("guardrails")
    if confirmation_version == 2 and (
        not isinstance(current_guardrails, dict)
        or not isinstance(current_guardrails.get("requiredApprovalModes"), list)
        or "human_confirmed"
        not in current_guardrails["requiredApprovalModes"]
        or len(current_entry_matches) != 1
        or current_entry_matches[0].get("readiness")
        != "same_profile_shell_only_ready"
        or current_entry_matches[0].get("recommendedHumanDecision")
        != "same_profile_shell_only"
        or current_entry_matches[0].get("humanDecision") != "pending"
    ):
        return None
    if (
        source_review.get("version") != 3
        or source_review.get("artifactType") != "strict_identity_review"
        or source_review.get("reviewId") != confirmation.get("sourceReviewId")
        or source_review.get("caseId") != confirmation.get("caseId")
        or source_review.get("operational") is not False
        or source_review.get("apply") != "forbidden"
        or historical_review.get("artifactType") != "strict_identity_review"
        or (
            historical_review.get("reviewId"),
            merge_result.get("sourceReviewSha256"),
        )
        not in review_ancestry
        or confirmation.get("sourceReviewSha256")
        != result.get("sourceReviewSha256")
        or not ratification_review_valid
        or ratification_artifact.get("version") != 1
        or ratification_artifact.get("artifactType")
        != "strict_identity_agent_ratification"
        or ratification_artifact.get("ratificationId")
        != ratification_result.get("ratificationId")
        or ratification_artifact.get("caseId") != confirmation.get("caseId")
        or ratification_artifact.get("candidateId") != candidate_id
        or ratification_artifact.get("priorCommandId") != merge_command_id
        or ratification_artifact.get("priorDecisionId") != decision_id
        or ratification_artifact.get("decision")
        != "ratify_agent_identity_only"
        or ratification_artifact.get("decidedByKind") != "agent"
        or ratification_artifact.get("identityDecisionTier")
        != "agent_approved_human_pending"
    ):
        return None
    if confirmation_version == 2:
        superseded_command_id = result[
            "supersedesHumanConfirmationCommandId"
        ]
        if superseded_command_id == row["command_id"]:
            return None
        superseded_row = connection.execute(
            "SELECT * FROM curation_commands WHERE command_id=?",
            (superseded_command_id,),
        ).fetchone()
        if superseded_row is None:
            return None
        superseded_result = json_object(superseded_row["result_json"])
        superseded_result_fields = set(superseded_result)
        if superseded_result_fields == _HUMAN_CONFIRMATION_RESULT_FIELDS:
            superseded_version = 1
            superseded_artifact_fields = _HUMAN_CONFIRMATION_ARTIFACT_FIELDS
        elif (
            superseded_result_fields
            == _SUPERSEDING_HUMAN_CONFIRMATION_RESULT_FIELDS
        ):
            superseded_version = 2
            superseded_artifact_fields = (
                _SUPERSEDING_HUMAN_CONFIRMATION_ARTIFACT_FIELDS
            )
        else:
            return None
        if (
            superseded_row["command_type"]
            != "identity_only_human_confirmation"
            or superseded_row["object_type"] != "identity_candidate"
            or superseded_row["object_id"] != candidate_id
            or superseded_row["projection_status"] != "not_required"
            or superseded_result.get("operation")
            != "identity_only_human_confirmation"
            or superseded_result.get("status")
            != "human_confirmed_existing_agent_same"
            or superseded_result.get("candidateId") != candidate_id
            or superseded_result.get("priorAgentCommandId") != merge_command_id
            or superseded_result.get("priorAgentRatificationCommandId")
            != ratification_command_id
            or superseded_result.get("priorDecisionId") != decision_id
            or superseded_result.get("identityDecisionTier")
            != "human_confirmed"
            or superseded_result.get("writeSet") != []
            or superseded_result.get("projectionStatus") != "not_required"
            or _canonical_fingerprint(superseded_result)
            != result.get("supersededHumanConfirmationResultSha256")
        ):
            return None
        superseded_artifact = _load_hashed_artifact(
            workspace_root,
            superseded_result.get("humanConfirmationArtifactPath"),
            superseded_result.get("humanConfirmationSha256"),
            superseded_artifact_fields,
            context=context,
        )
        superseded_review = _load_hashed_artifact(
            workspace_root,
            superseded_result.get("sourceReviewPath"),
            superseded_result.get("sourceReviewSha256"),
            None,
            context=context,
        )
        if superseded_artifact is None or superseded_review is None or (
            superseded_artifact.get("version") != superseded_version
            or superseded_artifact.get("artifactType")
            != "strict_identity_human_confirmation"
            or superseded_artifact.get("confirmationId")
            != superseded_result.get("confirmationId")
            or superseded_artifact.get("candidateId") != candidate_id
            or superseded_artifact.get("priorAgentCommandId")
            != merge_command_id
            or superseded_artifact.get("priorAgentRatificationCommandId")
            != ratification_command_id
            or superseded_artifact.get("priorDecisionId") != decision_id
            or superseded_artifact.get("decision")
            != "confirm_agent_identity_only"
            or superseded_artifact.get("decidedByKind") != "human"
            or superseded_artifact.get("identityDecisionTier")
            != "human_confirmed"
            or superseded_artifact.get("decidedBy")
            != superseded_row["actor"]
            or superseded_artifact.get("rationale")
            != superseded_row["rationale"]
            or superseded_artifact.get("sourceReviewSha256")
            != superseded_result.get("sourceReviewSha256")
            or superseded_review.get("artifactType")
            != "strict_identity_review"
            or superseded_review.get("reviewId")
            != superseded_artifact.get("sourceReviewId")
            or superseded_review.get("caseId") != confirmation.get("caseId")
            or (
                superseded_review.get("reviewId"),
                superseded_result.get("sourceReviewSha256"),
            )
            not in review_ancestry
            or superseded_result.get("sourceReviewSha256")
            == result.get("sourceReviewSha256")
        ):
            return None
        if superseded_version == 2 and (
            superseded_artifact.get("supersedesHumanConfirmationCommandId")
            != superseded_result.get("supersedesHumanConfirmationCommandId")
            or superseded_artifact.get(
                "supersededHumanConfirmationResultSha256"
            )
            != superseded_result.get(
                "supersededHumanConfirmationResultSha256"
            )
        ):
            return None

        chain_results: dict[str, dict[str, Any]] = {}
        for chain_row in connection.execute(
            """SELECT * FROM curation_commands
                 WHERE command_type='identity_only_human_confirmation'
                   AND object_type='identity_candidate' AND object_id=?
                   AND projection_status='not_required'""",
            (candidate_id,),
        ):
            chain_result = json_object(chain_row["result_json"])
            chain_result_fields = frozenset(chain_result)
            if chain_result_fields == frozenset(
                _HUMAN_CONFIRMATION_RESULT_FIELDS
            ):
                chain_version = 1
                chain_artifact_fields = _HUMAN_CONFIRMATION_ARTIFACT_FIELDS
            elif chain_result_fields == frozenset(
                _SUPERSEDING_HUMAN_CONFIRMATION_RESULT_FIELDS
            ):
                chain_version = 2
                chain_artifact_fields = (
                    _SUPERSEDING_HUMAN_CONFIRMATION_ARTIFACT_FIELDS
                )
            else:
                continue
            chain_artifact = _load_hashed_artifact(
                workspace_root,
                chain_result.get("humanConfirmationArtifactPath"),
                chain_result.get("humanConfirmationSha256"),
                chain_artifact_fields,
                context=context,
            )
            if chain_artifact is None or (
                chain_artifact.get("version") != chain_version
                or chain_artifact.get("artifactType")
                != "strict_identity_human_confirmation"
                or chain_artifact.get("confirmationId")
                != chain_result.get("confirmationId")
                or chain_artifact.get("candidateId") != candidate_id
                or chain_artifact.get("priorAgentCommandId")
                != merge_command_id
                or chain_artifact.get("priorAgentRatificationCommandId")
                != ratification_command_id
                or chain_artifact.get("priorDecisionId") != decision_id
                or chain_artifact.get("decision")
                != "confirm_agent_identity_only"
                or chain_artifact.get("decidedByKind") != "human"
                or chain_artifact.get("identityDecisionTier")
                != "human_confirmed"
                or chain_artifact.get("decidedBy") != chain_row["actor"]
                or chain_artifact.get("rationale") != chain_row["rationale"]
                or chain_artifact.get("sourceReviewSha256")
                != chain_result.get("sourceReviewSha256")
            ):
                continue
            if chain_version == 2 and (
                chain_artifact.get("supersedesHumanConfirmationCommandId")
                != chain_result.get("supersedesHumanConfirmationCommandId")
                or chain_artifact.get(
                    "supersededHumanConfirmationResultSha256"
                )
                != chain_result.get(
                    "supersededHumanConfirmationResultSha256"
                )
            ):
                continue
            if (
                chain_result.get("operation")
                == "identity_only_human_confirmation"
                and chain_result.get("status")
                == "human_confirmed_existing_agent_same"
                and chain_result.get("candidateId") == candidate_id
                and chain_result.get("priorAgentCommandId") == merge_command_id
                and chain_result.get("priorAgentRatificationCommandId")
                == ratification_command_id
                and chain_result.get("priorDecisionId") == decision_id
                and chain_result.get("identityDecisionTier")
                == "human_confirmed"
                and chain_result.get("writeSet") == []
                and chain_result.get("projectionStatus") == "not_required"
            ):
                chain_results[chain_row["command_id"]] = chain_result
        superseded_ids = {
            chain_result.get("supersedesHumanConfirmationCommandId")
            for chain_result in chain_results.values()
            if isinstance(
                chain_result.get("supersedesHumanConfirmationCommandId"), str
            )
        }
        if set(chain_results) - superseded_ids != {row["command_id"]}:
            return None
    decision_rows = connection.execute(
        "SELECT * FROM identity_decisions WHERE candidate_id=? ORDER BY id",
        (candidate_id,),
    ).fetchall()
    if len(decision_rows) != 1 or (
        decision_rows[0]["id"] != decision_id
        or decision_rows[0]["decision"] != "same"
        or decision_rows[0]["command_id"] != merge_command_id
        or decision_rows[0]["input_fingerprint"]
        != merge_result.get("inputFingerprint")
    ):
        return None
    return candidate_id, merge_command_id, decision_id


def _validated_local_review_refs(
    workspace_root: Path | None,
    raw_refs: Any,
    *,
    case_id: str,
    candidate_id: str,
    source_review_id: str,
    source_review_sha256: str,
    context=None,
) -> bool:
    if not isinstance(raw_refs, list) or len(raw_refs) != 2:
        return False
    if any(
        not isinstance(item, dict)
        or not isinstance(item.get("reviewerRole"), str)
        for item in raw_refs
    ):
        return False
    by_role = {
        item.get("reviewerRole"): item
        for item in raw_refs
    }
    if set(by_role) != set(_LOCAL_REVIEW_ROLES):
        return False
    artifact_hashes: set[str] = set()
    reviewer_runs: set[str] = set()
    artifact_paths: set[str] = set()
    for role, artifact_type in _LOCAL_REVIEW_ROLES.items():
        ref = by_role[role]
        if set(ref) != _LOCAL_REVIEW_REF_FIELDS:
            return False
        reviewed_candidate_ids = ref.get("reviewedCandidateIds")
        if (
            not isinstance(reviewed_candidate_ids, list)
            or not reviewed_candidate_ids
            or any(
                not isinstance(value, str) or not value
                for value in reviewed_candidate_ids
            )
            or len(reviewed_candidate_ids) != len(set(reviewed_candidate_ids))
            or reviewed_candidate_ids.count(candidate_id) != 1
            or ref.get("verdict") != "approve_agent_identity_only"
            or not isinstance(ref.get("artifactId"), str)
            or not ref["artifactId"]
            or not isinstance(ref.get("reviewerRunId"), str)
            or not ref["reviewerRunId"]
        ):
            return False
        artifact = _load_hashed_artifact(
            workspace_root,
            ref.get("artifactPath"),
            ref.get("artifactSha256"),
            None,
            context=context,
        )
        if artifact is None or (
            artifact.get("version") != 1
            or artifact.get("artifactType") != artifact_type
            or artifact.get("artifactId") != ref["artifactId"]
            or artifact.get("caseId") != case_id
            or artifact.get("reviewerRole") != role
            or artifact.get("reviewerRunId") != ref["reviewerRunId"]
            or artifact.get("sourceReviewId") not in {None, source_review_id}
            or artifact.get("sourceReviewSha256")
            not in {None, source_review_sha256}
        ):
            return False
        results = artifact.get("results")
        if not isinstance(results, list) or any(
            not isinstance(item, dict) for item in results
        ):
            return False
        if [item.get("candidateId") for item in results] != reviewed_candidate_ids:
            return False
        matching_results = [
            item for item in results if item.get("candidateId") == candidate_id
        ]
        if len(matching_results) != 1 or (
            matching_results[0].get("verdict") != ref["verdict"]
        ):
            return False
        artifact_hashes.add(ref["artifactSha256"])
        reviewer_runs.add(ref["reviewerRunId"])
        artifact_paths.add(ref["artifactPath"])
    return (
        len(artifact_hashes) == 2
        and len(reviewer_runs) == 2
        and len(artifact_paths) == 2
    )


def _validated_terminal_local_review(
    workspace_root: Path | None,
    target_path: Any,
    target_sha256: Any,
    *,
    case_id: str,
    candidate_id: str,
    review_id: str,
    independent_review_refs: Any,
    context=None,
) -> dict[str, Any] | None:
    target = _load_hashed_artifact(
        workspace_root, target_path, target_sha256, None,
        context=context,
    )
    if workspace_root is None or target is None:
        return None
    if (
        target.get("artifactType") != "strict_local_identity_review"
        or target.get("caseId") != case_id
        or target.get("reviewId") != review_id
        or type(target.get("version")) is not int
        or target["version"] < 3
        or target.get("operational") is not False
        or target.get("apply") != "forbidden"
    ):
        return None
    guardrails = target.get("guardrails")
    if not isinstance(guardrails, dict) or (
        guardrails.get("requiredApplyMechanism")
        != "identity_only_local_candidate_resolution"
        or guardrails.get("directOnly") is not True
        or guardrails.get("transitiveMerge") is not False
    ):
        return None
    entries = target.get("entries")
    matching_entries = (
        [
            entry
            for entry in entries
            if isinstance(entry, dict)
            and entry.get("candidateId") == candidate_id
        ]
        if isinstance(entries, list)
        else []
    )
    if (
        len(matching_entries) != 1
        or matching_entries[0].get("adjudicatedRecommendation") != "same"
        or target.get("independentReviewRefs") != independent_review_refs
    ):
        return None

    matching_review_ids = 0
    has_successor = False
    for path, payload, catalog_sha256 in _review_catalog_entries(workspace_root, context=context):
        if (
            not isinstance(payload, dict)
            or payload.get("artifactType") != "strict_local_identity_review"
            or payload.get("caseId") != case_id
        ):
            continue
        if payload.get("reviewId") == review_id:
            matching_review_ids += 1
        if payload.get("supersedesReviewId") == review_id:
            has_successor = True
    if matching_review_ids != 1 or has_successor:
        return None
    return target


def _validated_local_agent_chain(
    connection: sqlite3.Connection,
    workspace_root: Path | None,
    row: sqlite3.Row,
    result: dict[str, Any],
    *, context=None,
) -> tuple[str, str, str] | None:
    if set(result) != _LOCAL_RESOLUTION_RESULT_FIELDS:
        return None
    candidate_id = row["candidate_id"]
    command_id = row["command_id"]
    decision_id = row["decision_id"]
    occurrence_ids = result.get("occurrenceIds")
    person_ids = result.get("personIds")
    case_id = result.get("caseId")
    if (
        row["command_type"] != "identity_only_local_candidate_resolution"
        or row["object_type"] != "identity_candidate"
        or row["object_id"] != candidate_id
        or result.get("operation")
        != "identity_only_local_candidate_resolution"
        or result.get("candidateId") != candidate_id
        or result.get("commandId") != command_id
        or result.get("decisionId") != decision_id
        or result.get("status") != "resolved_same"
        or result.get("decision") != "same"
        or result.get("decidedByKind") != "agent"
        or result.get("identityDecisionTier")
        != "agent_approved_human_pending"
        or result.get("projectionStatus") != "pending"
        or not isinstance(result.get("writeSet"), list)
        or not isinstance(case_id, str)
        or not case_id
        or not isinstance(occurrence_ids, list)
        or len(occurrence_ids) != 2
        or any(
            not isinstance(occurrence_id, str) or not occurrence_id
            for occurrence_id in occurrence_ids
        )
        or len(set(occurrence_ids)) != 2
        or set(occurrence_ids)
        != {row["occurrence_a_id"], row["occurrence_b_id"]}
        or not isinstance(person_ids, list)
        or len(person_ids) != 2
        or any(
            not isinstance(person_id, str) or not person_id
            for person_id in person_ids
        )
        or len(set(person_ids)) != 2
    ):
        return None
    endpoint_rows = connection.execute(
        "SELECT id,person_id FROM person_occurrences WHERE id IN (?,?)",
        tuple(occurrence_ids),
    ).fetchall()
    person_by_occurrence = {item["id"]: item["person_id"] for item in endpoint_rows}
    if len(person_by_occurrence) != 2 or [
        person_by_occurrence.get(occurrence_id) for occurrence_id in occurrence_ids
    ] != person_ids:
        return None

    decision_rows = connection.execute(
        "SELECT * FROM identity_decisions WHERE candidate_id=? ORDER BY id",
        (candidate_id,),
    ).fetchall()
    if len(decision_rows) != 1 or (
        decision_rows[0]["id"] != decision_id
        or decision_rows[0]["decision"] != "same"
        or decision_rows[0]["command_id"] != command_id
        or decision_rows[0]["input_fingerprint"]
        != result.get("inputFingerprint")
    ):
        return None

    agent_decision = _load_hashed_artifact(
        workspace_root,
        result.get("agentReviewArtifactPath"),
        result.get("agentReviewSha256"),
        _LOCAL_AGENT_DECISION_FIELDS,
        context=context,
    )
    if agent_decision is None or (
        agent_decision.get("version") != 1
        or agent_decision.get("artifactType")
        != "strict_local_identity_agent_decision"
        or agent_decision.get("reviewId") != result.get("reviewId")
        or agent_decision.get("caseId") != case_id
        or agent_decision.get("candidateId") != candidate_id
        or agent_decision.get("decision") != "approve_same"
        or agent_decision.get("decidedByKind") != "agent"
        or agent_decision.get("identityDecisionTier")
        != "agent_approved_human_pending"
        or agent_decision.get("decidedBy") != row["actor"]
        or agent_decision.get("rationale") != row["rationale"]
        or agent_decision.get("sourceReviewSha256")
        != result.get("sourceReviewSha256")
        or agent_decision.get("independentReviewRefs")
        != result.get("independentReviewRefs")
    ):
        return None
    source_review_id = agent_decision.get("sourceReviewId")
    source_review_sha256 = result.get("sourceReviewSha256")
    if (
        not isinstance(source_review_id, str)
        or not source_review_id
        or not isinstance(source_review_sha256, str)
    ):
        return None
    source_review = _validated_terminal_local_review(
        workspace_root,
        result.get("sourceReviewPath"),
        source_review_sha256,
        case_id=case_id,
        candidate_id=candidate_id,
        review_id=source_review_id,
        independent_review_refs=result.get("independentReviewRefs"),
        context=context,
    )
    if source_review is None or not _validated_local_review_refs(
        workspace_root,
        result.get("independentReviewRefs"),
        case_id=case_id,
        candidate_id=candidate_id,
        source_review_id=source_review_id,
        source_review_sha256=source_review_sha256,
        context=context,
    ):
        return None

    ratification_rows = connection.execute(
        """SELECT * FROM curation_commands
            WHERE command_type='identity_only_local_agent_ratification'
              AND object_type='identity_candidate' AND object_id=?
            ORDER BY committed_at,command_id""",
        (candidate_id,),
    ).fetchall()
    if len(ratification_rows) != 1:
        return None
    ratification_row = ratification_rows[0]
    ratification_result = json_object(ratification_row["result_json"])
    if set(ratification_result) != _LOCAL_RATIFICATION_RESULT_FIELDS or (
        ratification_row["projection_status"] != "not_required"
        or ratification_result.get("operation")
        != "identity_only_local_agent_ratification"
        or ratification_result.get("status") != "ratified_existing_agent_same"
        or ratification_result.get("candidateId") != candidate_id
        or ratification_result.get("priorCommandId") != command_id
        or ratification_result.get("priorDecisionId") != decision_id
        or ratification_result.get("identityDecisionTier")
        != "agent_approved_human_pending"
        or ratification_result.get("writeSet") != []
        or ratification_result.get("projectionStatus") != "not_required"
    ):
        return None
    ratification_artifact = _load_hashed_artifact(
        workspace_root,
        ratification_result.get("ratificationReviewArtifactPath"),
        ratification_result.get("ratificationReviewSha256"),
        _LOCAL_RATIFICATION_ARTIFACT_FIELDS,
        context=context,
    )
    if ratification_artifact is None or (
        ratification_artifact.get("version") != 1
        or ratification_artifact.get("artifactType")
        != "strict_identity_local_agent_ratification"
        or ratification_artifact.get("ratificationId")
        != ratification_result.get("ratificationId")
        or ratification_artifact.get("caseId") != case_id
        or ratification_artifact.get("candidateId") != candidate_id
        or ratification_artifact.get("priorCommandId") != command_id
        or ratification_artifact.get("priorCommandResultSha256")
        != _canonical_fingerprint(result)
        or ratification_artifact.get("priorDecisionId") != decision_id
        or ratification_artifact.get("agentReviewSha256")
        != result.get("agentReviewSha256")
        or ratification_artifact.get("sourceReviewId") != source_review_id
        or ratification_artifact.get("sourceReviewSha256")
        != source_review_sha256
        or ratification_artifact.get("decision")
        != "ratify_agent_identity_only"
        or ratification_artifact.get("decidedByKind") != "agent"
        or ratification_artifact.get("identityDecisionTier")
        != "agent_approved_human_pending"
        or ratification_artifact.get("decidedBy") != ratification_row["actor"]
        or ratification_artifact.get("rationale")
        != ratification_row["rationale"]
    ):
        return None
    return candidate_id, command_id, decision_id


def _validated_local_human_chain(
    connection: sqlite3.Connection,
    workspace_root: Path | None,
    row: sqlite3.Row,
    result: dict[str, Any],
    *, context=None,
) -> tuple[str, str, str] | None:
    if set(result) != _LOCAL_HUMAN_RESOLUTION_RESULT_FIELDS:
        return None
    candidate_id = row["candidate_id"]
    command_id = row["command_id"]
    decision_id = row["decision_id"]
    occurrence_ids = result.get("occurrenceIds")
    person_ids = result.get("personIds")
    endpoints = result.get("endpoints")
    case_id = result.get("caseId")
    if (
        row["command_type"]
        != "identity_only_local_human_candidate_resolution"
        or row["object_type"] != "identity_candidate"
        or row["object_id"] != candidate_id
        or result.get("operation")
        != "identity_only_local_human_candidate_resolution"
        or result.get("candidateId") != candidate_id
        or result.get("commandId") != command_id
        or result.get("decisionId") != decision_id
        or result.get("status") != "resolved_same"
        or result.get("decision") != "same"
        or result.get("decidedByKind") != "human"
        or result.get("identityDecisionTier") != "human_confirmed"
        or result.get("projectionStatus") != "pending"
        or not isinstance(result.get("writeSet"), list)
        or not isinstance(case_id, str)
        or not case_id
        or not isinstance(occurrence_ids, list)
        or len(occurrence_ids) != 2
        or any(
            not isinstance(occurrence_id, str) or not occurrence_id
            for occurrence_id in occurrence_ids
        )
        or len(set(occurrence_ids)) != 2
        or set(occurrence_ids)
        != {row["occurrence_a_id"], row["occurrence_b_id"]}
        or not isinstance(person_ids, list)
        or len(person_ids) != 2
        or any(not isinstance(person_id, str) or not person_id for person_id in person_ids)
        or len(set(person_ids)) != 2
        or not isinstance(endpoints, list)
        or len(endpoints) != 2
    ):
        return None

    endpoint_rows = connection.execute(
        """SELECT po.id,po.person_id,po.source_item_id
             FROM person_occurrences po WHERE po.id IN (?,?)""",
        tuple(occurrence_ids),
    ).fetchall()
    endpoint_by_id = {item["id"]: item for item in endpoint_rows}
    expected_endpoints: list[dict[str, Any]] = []
    for occurrence_id, person_id in zip(occurrence_ids, person_ids):
        endpoint = endpoint_by_id.get(occurrence_id)
        if endpoint is None or endpoint["person_id"] != person_id:
            return None
        expected_endpoints.append(
            {
                "occurrenceId": occurrence_id,
                "personId": person_id,
                "sourceItemId": endpoint["source_item_id"],
            }
        )
    if endpoints != expected_endpoints:
        return None

    decision_rows = connection.execute(
        "SELECT * FROM identity_decisions WHERE candidate_id=? ORDER BY id",
        (candidate_id,),
    ).fetchall()
    if len(decision_rows) != 1 or (
        decision_rows[0]["id"] != decision_id
        or decision_rows[0]["decision"] != "same"
        or decision_rows[0]["command_id"] != command_id
        or decision_rows[0]["input_fingerprint"] != result.get("inputFingerprint")
    ):
        return None

    human_decision = _load_hashed_artifact(
        workspace_root,
        result.get("humanReviewArtifactPath"),
        result.get("humanReviewSha256"),
        _LOCAL_HUMAN_DECISION_FIELDS,
        context=context,
    )
    if human_decision is None or (
        human_decision.get("version") != 1
        or human_decision.get("artifactType")
        != "strict_local_identity_human_decision"
        or human_decision.get("reviewId") != result.get("reviewId")
        or human_decision.get("caseId") != case_id
        or human_decision.get("candidateId") != candidate_id
        or human_decision.get("decision") != "approve_same"
        or human_decision.get("decidedByKind") != "human"
        or human_decision.get("identityDecisionTier") != "human_confirmed"
        or human_decision.get("decidedBy") != row["actor"]
        or human_decision.get("rationale") != row["rationale"]
        or human_decision.get("sourceReviewSha256")
        != result.get("sourceReviewSha256")
        or human_decision.get("humanReviewPacketPath")
        != result.get("humanReviewPacketPath")
        or human_decision.get("humanReviewPacketSha256")
        != result.get("humanReviewPacketSha256")
    ):
        return None

    source_review_id = human_decision.get("sourceReviewId")
    source_review = _load_hashed_artifact(
        workspace_root,
        result.get("sourceReviewPath"),
        result.get("sourceReviewSha256"),
        None,
        context=context,
    )
    if source_review is None or (
        source_review.get("artifactType") != "strict_local_identity_review"
        or type(source_review.get("version")) is not int
        or source_review["version"] < 3
        or source_review.get("reviewId") != source_review_id
        or source_review.get("caseId") != case_id
        or source_review.get("operational") is not False
        or source_review.get("apply") != "forbidden"
    ):
        return None
    guardrails = source_review.get("guardrails")
    entries = source_review.get("entries")
    matching_entries = (
        [
            entry
            for entry in entries
            if isinstance(entry, dict)
            and entry.get("candidateId") == candidate_id
        ]
        if isinstance(entries, list)
        else []
    )
    if (
        not isinstance(guardrails, dict)
        or guardrails.get("requiredApplyMechanism")
        != "identity_only_local_human_candidate_resolution"
        or guardrails.get("directOnly") is not True
        or guardrails.get("transitiveMerge") is not False
        or len(matching_entries) != 1
        or matching_entries[0].get("adjudicatedRecommendation") != "same"
        or matching_entries[0].get("humanDecision") != "pending"
    ):
        return None

    packet = _load_hashed_artifact(
        workspace_root,
        result.get("humanReviewPacketPath"),
        result.get("humanReviewPacketSha256"),
        None,
        context=context,
    )
    packet_endpoints = packet.get("endpoints") if packet is not None else None
    packet_by_occurrence = (
        {
            item.get("occurrenceId"): item
            for item in packet_endpoints
            if isinstance(item, dict)
        }
        if isinstance(packet_endpoints, list)
        else {}
    )
    packet_id = packet.get("packetId") if packet is not None else None
    if packet is None or (
        packet.get("schemaVersion") != 1
        or not isinstance(packet_id, str)
        or not packet_id
        or packet.get("candidateId") != candidate_id
        or packet.get("status") != "awaiting_human_decision"
        or not isinstance(packet.get("allowedDecisions"), list)
        or "same" not in packet["allowedDecisions"]
        or set(packet_by_occurrence) != set(occurrence_ids)
        or any(
            packet_by_occurrence[endpoint["occurrenceId"]].get("sourceItemId")
            != endpoint["sourceItemId"]
            for endpoint in expected_endpoints
        )
    ):
        return None

    allowed_root = (
        (workspace_root / "research/records/reassessments").resolve()
        if workspace_root is not None
        else None
    )
    if allowed_root is None:
        return None
    matching_review_ids = 0
    has_successor = False
    review_has_sibling = False
    matching_packet_ids = 0
    packet_has_successor = False
    packet_has_sibling = False
    review_predecessor = source_review.get("supersedesReviewId")
    packet_predecessor = next(
        (
            value
            for value in (
                packet.get("supersedesPacketId"),
                packet.get("supersedesReviewPacketId"),
            )
            if isinstance(value, str) and value
        ),
        None,
    )
    for path, payload, catalog_sha256 in _review_catalog_entries(workspace_root, context=context):
        if not isinstance(payload, dict):
            continue
        if payload.get("packetId") == packet_id:
            matching_packet_ids += 1
        if packet_id in {
            payload.get("supersedesPacketId"),
            payload.get("supersedesReviewPacketId"),
        }:
            packet_has_successor = True
        if (
            packet_predecessor is not None
            and payload.get("packetId") != packet_id
            and packet_predecessor
            in {
                payload.get("supersedesPacketId"),
                payload.get("supersedesReviewPacketId"),
            }
        ):
            packet_has_sibling = True
        if (
            payload.get("artifactType") == "strict_local_identity_review"
            and payload.get("caseId") == case_id
        ):
            if payload.get("reviewId") == source_review_id:
                matching_review_ids += 1
            if payload.get("supersedesReviewId") == source_review_id:
                has_successor = True
            if (
                review_predecessor is not None
                and payload.get("reviewId") != source_review_id
                and payload.get("supersedesReviewId") == review_predecessor
            ):
                review_has_sibling = True
    if (
        matching_review_ids != 1
        or has_successor
        or review_has_sibling
        or matching_packet_ids != 1
        or packet_has_successor
        or packet_has_sibling
    ):
        return None
    return candidate_id, command_id, decision_id


@dataclass(frozen=True)
class OccurrencesProjection:
    occurrences: list[dict[str, Any]]
    people: list[dict[str, Any]]
    allowed_occurrence_ids: set[str]
    allowed_person_ids: set[str]


def project_occurrences(
    connection: sqlite3.Connection,
    privacy: PrivacyContext,
    allowed_source_ids: set[str],
    *,
    validation_context=None,
) -> OccurrencesProjection:
    workspace_root = _workspace_root(connection)
    if validation_context is not None:
        from ..identity_source_profile_curation import _check_projection_context

        _check_projection_context(workspace_root, connection, validation_context)
    restricted_occurrences = privacy.restricted_ids("person_occurrence")
    restricted_people = privacy.restricted_ids("person")
    occurrence_rows = connection.execute(
        """SELECT po.id,po.source_item_id,po.reading_version_id,po.transcript_segment_id,
                  po.person_id,po.raw_name,po.normalized_name,po.search_name,po.role_text,
                  po.reading_confidence,po.verification_status,po.evidence_json,
                  p.status AS person_status,p.preferred_name,
                  si.item_type AS source_item_type,
                  EXISTS(
                      SELECT 1
                        FROM identity_candidates ic
                        JOIN identity_decisions idc ON idc.candidate_id=ic.id
                       WHERE ic.status='resolved_same'
                         AND idc.decision='same'
                         AND (ic.occurrence_a_id=po.id OR ic.occurrence_b_id=po.id)
                  ) AS explicit_same_identity
           FROM person_occurrences po
           LEFT JOIN persons p ON p.id=po.person_id
           JOIN source_items si ON si.id=po.source_item_id
           ORDER BY po.raw_name,po.id"""
    ).fetchall()
    occurrence_notes: dict[str, str] = {}
    for row in connection.execute(
        """SELECT olm.occurrence_id,e.notes
           FROM occurrence_legacy_mentions olm
           JOIN mentions m ON m.id=olm.legacy_mention_id
           JOIN entities e ON e.id=m.entity_id
           WHERE e.notes IS NOT NULL AND TRIM(e.notes) != ''
           ORDER BY olm.occurrence_id,m.id"""
    ):
        occurrence_notes.setdefault(row["occurrence_id"], row["notes"])

    ratified_agent_decisions: set[tuple[str, str, str]] = set()
    for row in connection.execute(
        """SELECT command_id,object_id,result_json
             FROM curation_commands
            WHERE command_type='identity_only_agent_ratification'
              AND object_type='identity_candidate'
              AND projection_status='not_required'
            ORDER BY committed_at DESC,command_id DESC"""
    ):
        result = json_object(row["result_json"])
        candidate_id = result.get("candidateId")
        prior_command_id = result.get("priorCommandId")
        prior_decision_id = result.get("priorDecisionId")
        if (
            result.get("operation") == "identity_only_agent_ratification"
            and result.get("status") == "ratified_existing_agent_same"
            and result.get("identityDecisionTier")
            == "agent_approved_human_pending"
            and result.get("writeSet") == []
            and result.get("projectionStatus") == "not_required"
            and candidate_id == row["object_id"]
            and all(
                isinstance(value, str) and value
                for value in (candidate_id, prior_command_id, prior_decision_id)
            )
        ):
            chain = (candidate_id, prior_command_id, prior_decision_id)
            ratified_agent_decisions.add(chain)

    human_confirmed_agent_decisions: set[tuple[str, str, str]] = set()
    source_profile_identities = project_source_profile_identities(
        connection, workspace_root,
        **({"context": validation_context} if validation_context is not None else {}),
    )
    source_only_candidate_ids, source_only_occurrence_ids = source_only_typed_ids(connection)
    family_knowledge = project_family_knowledge(connection, workspace_root, privacy)
    for row in connection.execute(
        """SELECT command_id,object_id,result_json,actor,rationale
             FROM curation_commands
            WHERE command_type='identity_only_human_confirmation'
              AND object_type='identity_candidate'
              AND projection_status='not_required'
            ORDER BY committed_at DESC,command_id DESC"""
    ):
        result = json_object(row["result_json"])
        validated_chain = _validated_human_confirmation_chain(
            connection, workspace_root, row, result,
            context=validation_context,
        )
        if validated_chain is not None:
            human_confirmed_agent_decisions.add(validated_chain)

    identity_decision_tiers: dict[str, str] = {}
    local_identity_decision_tiers: dict[str, str] = {}
    for row in connection.execute(
        """SELECT ic.id AS candidate_id,
                  ic.occurrence_a_id,ic.occurrence_b_id,
                  idc.id AS decision_id,idc.command_id,
                  cc.command_type,cc.object_type,cc.object_id,cc.result_json,
                  cc.actor,cc.rationale
             FROM identity_candidates ic
             JOIN identity_decisions idc ON idc.candidate_id=ic.id
             JOIN curation_commands cc ON cc.command_id=idc.command_id
            WHERE ic.status='resolved_same' AND idc.decision='same'
            ORDER BY idc.decided_at DESC,idc.id DESC"""
    ):
        if (row["candidate_id"] in source_profile_identities.typed_candidate_ids
                or row["candidate_id"] in source_only_candidate_ids):
            continue
        chain = (
            row["candidate_id"],
            row["command_id"],
            row["decision_id"],
        )
        result = json_object(row["result_json"])
        tier = result.get("identityDecisionTier")
        if tier not in _IDENTITY_DECISION_TIERS:
            continue
        local_agent_resolution = row["command_type"] == (
            "identity_only_local_candidate_resolution"
        )
        local_human_resolution = row["command_type"] == (
            "identity_only_local_human_candidate_resolution"
        )
        local_resolution = local_agent_resolution or local_human_resolution
        if local_agent_resolution:
            if (
                tier != "agent_approved_human_pending"
                or _validated_local_agent_chain(
                    connection, workspace_root, row, result,
                    context=validation_context,
                )
                is None
            ):
                continue
        elif local_human_resolution:
            if (
                tier != "human_confirmed"
                or _validated_local_human_chain(
                    connection, workspace_root, row, result,
                    context=validation_context,
                )
                is None
            ):
                continue
        elif tier == "agent_approved_human_pending":
            if chain in human_confirmed_agent_decisions:
                tier = "human_confirmed"
            elif chain not in ratified_agent_decisions:
                continue
        tier_by_occurrence = (
            local_identity_decision_tiers
            if local_resolution
            else identity_decision_tiers
        )
        for occurrence_id in (row["occurrence_a_id"], row["occurrence_b_id"]):
            tier_by_occurrence.setdefault(occurrence_id, tier)

    occurrences: list[dict[str, Any]] = []
    allowed_occurrence_ids: set[str] = set()
    allowed_person_ids: set[str] = set()
    normal_person_ids: set[str] = set()
    redacted_external_person_ids: set[str] = set()
    for row in occurrence_rows:
        if (
            row["id"] in restricted_occurrences
            or row["person_id"] in restricted_people
            or row["verification_status"] in {"retracted", "superseded"}
        ):
            continue
        source_allowed = row["source_item_id"] in allowed_source_ids
        person_privacy = privacy.labels.get(("person", row["person_id"]))
        source_privacy = privacy.labels.get(("source_item", row["source_item_id"]))
        source_profile_guarded = (
            row["id"] in source_profile_identities.typed_occurrence_ids
        )
        source_profile_chain = source_profile_identities.chains_by_occurrence.get(
            row["id"]
        )
        explicit_external_identity_projection = (
            source_profile_chain is not None
            if source_profile_guarded
            else bool(row["explicit_same_identity"])
        )
        if row["id"] in source_only_occurrence_ids:
            explicit_external_identity_projection = False
        sanitized_external_occurrence = bool(
            not source_allowed
            and source_privacy == "restricted"
            and row["source_item_type"] == "external_tree_profile"
            and row["person_id"]
            and (
                row["person_status"] == "candidate"
                or explicit_external_identity_projection
                or source_profile_guarded
            )
            and person_privacy in {"public_historical", "deceased"}
            and row["preferred_name"]
        )
        if not source_allowed and not sanitized_external_occurrence:
            continue
        allowed_occurrence_ids.add(row["id"])
        projected_person_id = row["person_id"]
        if (
            source_profile_guarded
            and source_profile_chain is None
            and row["id"] not in source_profile_identities.external_occurrence_ids
        ):
            projected_person_id = None
        if projected_person_id:
            allowed_person_ids.add(projected_person_id)
        if sanitized_external_occurrence:
            redacted_external_person_ids.add(row["person_id"])
            occurrence = {
                "id": row["id"],
                "sourceId": None,
                "personId": row["person_id"],
                "name": row["preferred_name"],
                "status": "imported_unverified",
                "personStatus": row["person_status"],
                "identityStatus": (
                    "resolved_same"
                    if explicit_external_identity_projection
                    else "unresolved"
                ),
                "candidatePersonStatus": (
                    "candidate" if row["person_status"] == "candidate" else None
                ),
                "personIdentityResolved": explicit_external_identity_projection,
                "sourceRedacted": True,
                "externalTreeCandidate": True,
                "externalTreeIdentityProjection": (
                    "resolved_same"
                    if explicit_external_identity_projection
                    else "unresolved"
                ),
            }
            if source_profile_guarded:
                occurrence["sourceProfileIdentityGuarded"] = True
            identity_decision_tier = (
                source_profile_chain["identityDecisionTier"]
                if source_profile_chain is not None
                else None if source_profile_guarded
                else identity_decision_tiers.get(row["id"])
            )
            if explicit_external_identity_projection and identity_decision_tier:
                occurrence["identityDecisionTier"] = identity_decision_tier
            if source_profile_chain:
                occurrence["sourceProfileIdentityDirectChains"] = [
                    {"candidateId": candidate, "commandId": command, "decisionId": decision}
                    for candidate, command, decision in source_profile_identities.chain_ids_by_occurrence.get(row["id"], ())
                ]
            if row["id"] in family_knowledge:
                occurrence["familyKnowledge"] = family_knowledge[row["id"]]
            occurrences.append(occurrence)
            continue
        # A new typed identity link does not declassify the imported person's
        # wrapper notes/names. Only an independent ordinary source occurrence
        # can retain the existing normal-person metadata projection.
        if projected_person_id and not source_profile_guarded:
            normal_person_ids.add(projected_person_id)
        notes = occurrence_notes.get(row["id"])
        occurrence = {
            "id": row["id"],
            "sourceId": row["source_item_id"],
            "readingVersionId": row["reading_version_id"],
            "segmentId": row["transcript_segment_id"],
            "personId": projected_person_id,
            "name": row["raw_name"],
            "normalizedName": row["normalized_name"],
            "searchName": row["search_name"],
            "role": row["role_text"],
            "confidence": row["reading_confidence"],
            "status": row["verification_status"],
            "notes": notes,
            "birth": birth_detail(notes),
            "evidence": json_object(row["evidence_json"]),
        }
        # Identity semantics exist only when this exact occurrence has an
        # explicit person link.  An unlinked occurrence is not automatically a
        # candidate and must not be presented as a negative identity decision.
        if projected_person_id:
            occurrence.update(
                {
                    "personStatus": row["person_status"],
                    "identityStatus": (
                        "resolved"
                        if row["person_status"] in {"confirmed", "resolved"}
                        else "unresolved"
                    ),
                    "candidatePersonStatus": (
                        row["person_status"]
                        if row["person_status"] == "candidate"
                        else None
                    ),
                    "personIdentityResolved": row["person_status"]
                    in {"confirmed", "resolved"},
                }
            )
        if source_profile_guarded:
            occurrence.update(
                {
                    "sourceProfileIdentityGuarded": True,
                    "identityStatus": (
                        "resolved_same" if source_profile_chain else "unresolved"
                    ),
                    "personIdentityResolved": source_profile_chain is not None,
                }
            )
        identity_decision_tier = (
            source_profile_chain["identityDecisionTier"]
            if source_profile_chain is not None
            else None if source_profile_guarded
            else local_identity_decision_tiers.get(row["id"])
        )
        if row["id"] in source_only_occurrence_ids:
            identity_decision_tier = None
        if identity_decision_tier:
            occurrence["identityDecisionTier"] = identity_decision_tier
        if source_profile_chain:
            occurrence["sourceProfileIdentityDirectChains"] = [
                {"candidateId": candidate, "commandId": command, "decisionId": decision}
                for candidate, command, decision in source_profile_identities.chain_ids_by_occurrence.get(row["id"], ())
            ]
        if row["id"] in family_knowledge:
            occurrence["familyKnowledge"] = family_knowledge[row["id"]]
        occurrences.append(occurrence)
    names: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in connection.execute(
        """SELECT person_id,name_text,normalized_name,name_type,language,
                  date_from,date_to
           FROM person_names ORDER BY person_id,name_type,name_text"""
    ):
        if (
            row["person_id"] in allowed_person_ids
            and row["person_id"] in normal_person_ids
        ):
            names[row["person_id"]].append(
                {
                    "text": row["name_text"],
                    "normalized": row["normalized_name"],
                    "type": row["name_type"],
                    "language": row["language"],
                    "dateFrom": row["date_from"],
                    "dateTo": row["date_to"],
                }
            )
    people = [
        {
            "id": row["id"],
            "name": row["preferred_name"] or "[bez nazwy]",
            "status": row["status"],
            "notes": (
                None
                if row["id"] in redacted_external_person_ids
                and row["id"] not in normal_person_ids
                else row["notes"]
            ),
            "names": names.get(row["id"], []),
        }
        for row in connection.execute(
            "SELECT id,preferred_name,status,notes FROM persons ORDER BY preferred_name,id"
        )
        if row["id"] in allowed_person_ids and not privacy.blocked("person", row["id"])
    ]
    return OccurrencesProjection(
        occurrences, people, allowed_occurrence_ids, allowed_person_ids
    )
