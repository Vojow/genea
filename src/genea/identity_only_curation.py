from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .command_envelope import (
    CommandEnvelopeConflict,
    fingerprint,
    record_command,
    replay_command,
)
from .migrations import LATEST_SCHEMA_VERSION
from .research import require_tables
from .storage import configured_database_backend
from .workspace import sha256_file, stable_id, utc_now


SCHEMA_VERSION = 1
OPERATION = "identity_only_external_shell_merge"
EXTERNAL_CANDIDATE_DISPOSITION_OPERATION = (
    "identity_only_external_candidate_disposition"
)
RATIFICATION_OPERATION = "identity_only_agent_ratification"
HUMAN_CONFIRMATION_OPERATION = "identity_only_human_confirmation"
REQUIRED_TABLES = {
    "audit_log",
    "curation_commands",
    "external_sync_items",
    "family_notes",
    "household_memberships",
    "identity_candidates",
    "identity_decisions",
    "person_names",
    "person_occurrences",
    "persons",
    "privacy_labels",
    "private_attachments",
    "private_fields",
    "private_relationships",
    "research_cases",
    "research_question_targets",
    "source_items",
}

_TOP_LEVEL_FIELDS = {
    "version",
    "operation",
    "operationId",
    "reviewId",
    "caseId",
    "candidate",
    "external",
    "local",
    "humanReview",
    "decidedBy",
    "rationale",
}
_AGENT_TOP_LEVEL_FIELDS = (_TOP_LEVEL_FIELDS - {"humanReview"}) | {"agentReview"}
_CANDIDATE_FIELDS = {"id", "expectedStatus", "decision"}
_EXTERNAL_FIELDS = {
    "occurrenceId",
    "expectedPersonId",
    "expectedSourceItemId",
    "expectedVerificationStatus",
    "expectedSourceItemType",
    "expectedSourcePrivacy",
    "expectedOccurrenceCount",
}
_LOCAL_FIELDS = {"personId", "anchorOccurrenceId", "expectedPersonId"}
_HUMAN_REVIEW_FIELDS = {"artifactPath", "expectedSha256"}
_HUMAN_DECISION_FIELDS = {
    "version",
    "artifactType",
    "reviewId",
    "sourceReviewId",
    "sourceReviewSha256",
    "caseId",
    "candidateId",
    "decision",
    "decidedByKind",
    "decidedBy",
    "decidedAt",
    "rationale",
}
_INDEPENDENT_REVIEW_REF_FIELDS = {
    "artifactPath",
    "artifactSha256",
    "artifactId",
    "reviewerRole",
    "reviewerRunId",
    "reviewedCandidateIds",
    "verdict",
}
_AGENT_DECISION_FIELDS = _HUMAN_DECISION_FIELDS | {
    "identityDecisionTier",
    "independentReviewRefs",
}
_HUMAN_DECISION_TIER = "human_confirmed"
_AGENT_DECISION_TIER = "agent_approved_human_pending"
_AGENT_APPLY_MECHANISM = "fresh_identity_only_operation_after_agent_decision"
_AGENT_REVIEW_ROLES = {"source_auditor", "evidence_correlator"}
_AGENT_REVIEW_VERDICT = "approve_agent_identity_only"
_RATIFICATION_TOP_LEVEL_FIELDS = {
    "version",
    "operation",
    "operationId",
    "caseId",
    "candidateId",
    "priorCommandId",
    "expectedDecisionId",
    "ratificationReview",
    "ratifiedBy",
    "rationale",
}
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
_HUMAN_CONFIRMATION_TOP_LEVEL_FIELDS = {
    "version",
    "operation",
    "operationId",
    "caseId",
    "candidateId",
    "priorAgentCommandId",
    "priorAgentRatificationCommandId",
    "expectedDecisionId",
    "humanConfirmation",
    "confirmedBy",
    "rationale",
}
_SUPERSEDING_HUMAN_CONFIRMATION_TOP_LEVEL_FIELDS = (
    _HUMAN_CONFIRMATION_TOP_LEVEL_FIELDS
    | {"supersedesHumanConfirmationCommandId"}
)
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
_EXTERNAL_CANDIDATE_DISPOSITION_TOP_LEVEL_FIELDS = {
    "version",
    "operation",
    "operationId",
    "reviewId",
    "caseId",
    "candidate",
    "external",
    "local",
    "humanDisposition",
    "decidedBy",
    "rationale",
}
_EXTERNAL_CANDIDATE_DISPOSITION_CANDIDATE_FIELDS = {
    "id",
    "expectedStatus",
    "disposition",
}
_EXTERNAL_CANDIDATE_DISPOSITION_EXTERNAL_FIELDS = {
    "occurrenceId",
    "expectedPersonId",
    "expectedSourceItemId",
    "expectedVerificationStatus",
    "expectedSourceItemType",
    "expectedSourcePrivacy",
}
_EXTERNAL_CANDIDATE_DISPOSITION_LOCAL_FIELDS = {
    "occurrenceId",
    "expectedPersonId",
}
_EXTERNAL_CANDIDATE_DISPOSITION_ARTIFACT_FIELDS = {
    "version",
    "artifactType",
    "reviewId",
    "sourceReviewId",
    "sourceReviewSha256",
    "caseId",
    "candidateId",
    "externalOccurrenceId",
    "localOccurrenceId",
    "disposition",
    "decidedByKind",
    "decidedBy",
    "decidedAt",
    "rationale",
}
_EXTERNAL_CANDIDATE_DISPOSITION_READINESS = (
    "dismiss_redundant_external_anchor_candidate_ready"
)
_EXTERNAL_CANDIDATE_DISPOSITION_RECOMMENDATION = (
    "dismiss_candidate_preserve_history"
)
_EXTERNAL_CANDIDATE_DISPOSITION_UNCHANGED = {
    "persons": True,
    "occurrences": True,
    "personNames": True,
    "personFacts": True,
    "relationships": True,
    "households": True,
    "memberships": True,
    "preferredName": True,
    "searchName": True,
}
_UNCHANGED_IMPACT = {
    "personNames": True,
    "personFacts": True,
    "relationships": True,
    "households": True,
    "memberships": True,
    "preferredName": True,
    "localStatus": True,
    "searchName": True,
}


class IdentityOnlyCurationError(ValueError):
    """A fail-closed validation error for an identity-only operation."""


class IdentityOnlyCurationConflict(IdentityOnlyCurationError):
    """The accepted preview or command envelope no longer matches."""


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IdentityOnlyCurationError(f"Pole {label} musi być niepustym tekstem")
    return value.strip()


def _strict_object(value: Any, label: str, expected_fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise IdentityOnlyCurationError(f"Pole {label} musi być obiektem")
    actual_fields = set(value)
    missing = sorted(expected_fields - actual_fields)
    extra = sorted(actual_fields - expected_fields)
    if missing or extra:
        details = []
        if missing:
            details.append("brak: " + ", ".join(missing))
        if extra:
            details.append("nadmiarowe: " + ", ".join(extra))
        raise IdentityOnlyCurationError(
            f"Nieprawidłowy schemat {label} ({'; '.join(details)})"
        )
    return value


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise IdentityOnlyCurationError(f"Nie można odczytać {label}: {error}") from error
    if not isinstance(payload, dict):
        raise IdentityOnlyCurationError(f"{label} musi być obiektem JSON")
    return payload


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _sha256(value: Any, label: str) -> str:
    text = _text(value, label).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", text):
        raise IdentityOnlyCurationError(f"Pole {label} musi być pełnym SHA-256")
    return text


def _independent_review_refs(
    root: Path,
    value: Any,
    *,
    case_id: str,
    candidate_id: str,
    label: str,
    forbidden_paths: set[Path],
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) != 2:
        raise IdentityOnlyCurationError(
            f"Pole {label} musi zawierać dokładnie dwa niezależne review refs"
        )
    allowed_root = (root / "research/records/reassessments").resolve()
    normalized: list[dict[str, Any]] = []
    resolved_paths: set[Path] = set()
    artifact_sha256_values: set[str] = set()
    reviewer_run_ids: set[str] = set()
    for index, raw_reference in enumerate(value):
        reference_label = f"{label}[{index}]"
        reference = _strict_object(
            raw_reference,
            reference_label,
            _INDEPENDENT_REVIEW_REF_FIELDS,
        )
        artifact_path_text = _text(
            reference["artifactPath"], f"{reference_label}.artifactPath"
        )
        if Path(artifact_path_text).is_absolute():
            raise IdentityOnlyCurationError(
                f"{reference_label}.artifactPath musi być ścieżką względną repozytorium"
            )
        artifact_path = (root / artifact_path_text).resolve()
        if not _is_within(artifact_path, allowed_root):
            raise IdentityOnlyCurationError(
                "Niezależny artefakt review musi znajdować się "
                "w research/records/reassessments"
            )
        if artifact_path in forbidden_paths:
            raise IdentityOnlyCurationError(
                "Niezależny review ref nie może wskazywać decyzji ani strict review"
            )
        if artifact_path in resolved_paths:
            raise IdentityOnlyCurationError(
                "Niezależne review refs muszą wskazywać różne artefakty"
            )
        if not artifact_path.is_file():
            raise IdentityOnlyCurationError(
                f"Nie znaleziono niezależnego artefaktu review: {artifact_path_text}"
            )
        expected_sha256 = _sha256(
            reference["artifactSha256"], f"{reference_label}.artifactSha256"
        )
        if sha256_file(artifact_path) != expected_sha256:
            raise IdentityOnlyCurationConflict(
                "Hash niezależnego artefaktu review nie odpowiada referencji"
            )
        if expected_sha256 in artifact_sha256_values:
            raise IdentityOnlyCurationError(
                "Niezależne review refs muszą wiązać różne treści artefaktów"
            )
        reviewer_role = _text(
            reference["reviewerRole"], f"{reference_label}.reviewerRole"
        )
        if reviewer_role not in _AGENT_REVIEW_ROLES:
            raise IdentityOnlyCurationError(
                f"{reference_label}.reviewerRole musi wskazywać source_auditor "
                "albo evidence_correlator"
            )
        reviewer_run_id = _text(
            reference["reviewerRunId"], f"{reference_label}.reviewerRunId"
        )
        if reviewer_run_id in reviewer_run_ids:
            raise IdentityOnlyCurationError(
                "Niezależne review refs muszą mieć różne reviewerRunId"
            )
        artifact_id = _text(
            reference["artifactId"], f"{reference_label}.artifactId"
        )
        reviewed_candidate_ids = reference["reviewedCandidateIds"]
        if not isinstance(reviewed_candidate_ids, list) or not reviewed_candidate_ids:
            raise IdentityOnlyCurationError(
                f"{reference_label}.reviewedCandidateIds musi być niepustą listą"
            )
        normalized_candidate_ids = [
            _text(candidate, f"{reference_label}.reviewedCandidateIds")
            for candidate in reviewed_candidate_ids
        ]
        if len(set(normalized_candidate_ids)) != len(normalized_candidate_ids):
            raise IdentityOnlyCurationError(
                f"{reference_label}.reviewedCandidateIds zawiera duplikaty"
            )
        if candidate_id not in normalized_candidate_ids:
            raise IdentityOnlyCurationError(
                "Niezależny review ref nie obejmuje ratyfikowanej kandydatury"
            )
        if reference["verdict"] != _AGENT_REVIEW_VERDICT:
            raise IdentityOnlyCurationError(
                f"{reference_label}.verdict musi mieć wartość "
                f"{_AGENT_REVIEW_VERDICT}"
            )
        review_artifact = _load_json(
            artifact_path, f"niezależnego artefaktu review {artifact_path_text}"
        )
        expected_artifact_type = (
            "strict_identity_source_audit"
            if reviewer_role == "source_auditor"
            else "strict_identity_evidence_correlation"
        )
        if (
            review_artifact.get("version") != 1
            or review_artifact.get("artifactType") != expected_artifact_type
            or review_artifact.get("artifactId") != artifact_id
            or review_artifact.get("caseId") != case_id
            or review_artifact.get("reviewerRole") != reviewer_role
            or review_artifact.get("reviewerRunId") != reviewer_run_id
        ):
            raise IdentityOnlyCurationError(
                "Niezależny artefakt review nie odpowiada związanej referencji"
            )
        artifact_results = review_artifact.get("results")
        artifact_candidate_ids = (
            [
                result.get("candidateId")
                for result in artifact_results
                if isinstance(result, dict)
                and result.get("verdict") == _AGENT_REVIEW_VERDICT
            ]
            if isinstance(artifact_results, list)
            else []
        )
        if (
            artifact_candidate_ids != normalized_candidate_ids
            or len(set(artifact_candidate_ids)) != len(artifact_candidate_ids)
        ):
            raise IdentityOnlyCurationError(
                "reviewedCandidateIds nie odpowiadają wynikom niezależnego artefaktu"
            )
        matching_results = (
            [
                result
                for result in artifact_results
                if isinstance(result, dict)
                and result.get("candidateId") == candidate_id
            ]
            if isinstance(artifact_results, list)
            else []
        )
        if (
            len(matching_results) != 1
            or matching_results[0].get("verdict") != _AGENT_REVIEW_VERDICT
        ):
            raise IdentityOnlyCurationError(
                "Niezależny artefakt review nie zawiera jednoznacznej zgody "
                "dla kandydatury"
            )
        resolved_paths.add(artifact_path)
        artifact_sha256_values.add(expected_sha256)
        reviewer_run_ids.add(reviewer_run_id)
        normalized.append(
            {
                "artifactPath": artifact_path.relative_to(root.resolve()).as_posix(),
                "artifactSha256": expected_sha256,
                "artifactId": artifact_id,
                "reviewerRole": reviewer_role,
                "reviewerRunId": reviewer_run_id,
                "reviewedCandidateIds": normalized_candidate_ids,
                "verdict": _AGENT_REVIEW_VERDICT,
            }
        )
    if {reference["reviewerRole"] for reference in normalized} != _AGENT_REVIEW_ROLES:
        raise IdentityOnlyCurationError(
            "Niezależne review refs muszą zawierać source_auditor i evidence_correlator"
        )
    return normalized


def _review_gate(
    root: Path,
    payload: dict[str, Any],
    review_reference: dict[str, Any],
    *,
    approval_mode: str,
) -> dict[str, Any]:
    if approval_mode not in {"human", "agent"}:
        raise IdentityOnlyCurationError("Nieobsługiwany tryb akceptacji identity-only")
    review_label = "humanReview" if approval_mode == "human" else "agentReview"
    artifact_path_text = _text(
        review_reference["artifactPath"], f"{review_label}.artifactPath"
    )
    if Path(artifact_path_text).is_absolute():
        raise IdentityOnlyCurationError(
            f"{review_label}.artifactPath musi być ścieżką względną repozytorium"
        )
    allowed_root = (root / "research/records/reassessments").resolve()
    artifact_path = (root / artifact_path_text).resolve()
    if not _is_within(artifact_path, allowed_root):
        raise IdentityOnlyCurationError(
            "Artefakt decyzji identity-only musi znajdować się "
            "w research/records/reassessments"
        )
    if not artifact_path.is_file():
        raise IdentityOnlyCurationError("Nie znaleziono artefaktu decyzji identity-only")
    expected_sha256 = _sha256(
        review_reference["expectedSha256"], f"{review_label}.expectedSha256"
    )
    actual_sha256 = sha256_file(artifact_path)
    if actual_sha256 != expected_sha256:
        raise IdentityOnlyCurationConflict(
            "Hash artefaktu decyzji identity-only nie odpowiada manifestowi"
        )

    expected_decision_fields = (
        _HUMAN_DECISION_FIELDS
        if approval_mode == "human"
        else _AGENT_DECISION_FIELDS
    )
    decision = _strict_object(
        _load_json(artifact_path, "artefaktu decyzji identity-only"),
        f"{approval_mode} decision artifact",
        expected_decision_fields,
    )
    if decision["version"] != 1:
        raise IdentityOnlyCurationError("Artefakt decyzji musi mieć version=1")
    expected_artifact_type = (
        "strict_identity_human_decision"
        if approval_mode == "human"
        else "strict_identity_agent_decision"
    )
    if decision["artifactType"] != expected_artifact_type:
        raise IdentityOnlyCurationError(
            "Artefakt decyzji identity-only ma nieprawidłowy artifactType"
        )
    if decision["decision"] != "approve_same":
        raise IdentityOnlyCurationError(
            "Artefakt human review musi zawierać decision=approve_same"
        )
    if decision["decidedByKind"] != approval_mode:
        raise IdentityOnlyCurationError(
            "Artefakt decyzji identity-only musi zawierać "
            f"decidedByKind={approval_mode}"
        )
    if approval_mode == "agent" and (
        decision["identityDecisionTier"] != _AGENT_DECISION_TIER
    ):
        raise IdentityOnlyCurationError(
            "Artefakt decyzji agenta musi zawierać "
            f"identityDecisionTier={_AGENT_DECISION_TIER}"
        )
    for field in (
        "reviewId",
        "sourceReviewId",
        "caseId",
        "candidateId",
        "decidedBy",
        "decidedAt",
        "rationale",
    ):
        decision[field] = _text(decision[field], f"{approval_mode}Decision.{field}")
    decision["sourceReviewSha256"] = _sha256(
        decision["sourceReviewSha256"],
        f"{approval_mode}Decision.sourceReviewSha256",
    )
    try:
        decided_at = datetime.fromisoformat(decision["decidedAt"].replace("Z", "+00:00"))
    except ValueError as error:
        raise IdentityOnlyCurationError(
            f"{approval_mode}Decision.decidedAt musi być znacznikiem ISO-8601"
        ) from error
    if decided_at.tzinfo is None:
        raise IdentityOnlyCurationError(
            f"{approval_mode}Decision.decidedAt musi zawierać strefę czasową"
        )

    expected_matches = {
        "reviewId": payload["reviewId"],
        "caseId": payload["caseId"],
        "candidateId": payload["candidate"]["id"],
        "decidedBy": payload["decidedBy"],
        "rationale": payload["rationale"],
    }
    mismatches = [
        field for field, expected in expected_matches.items() if decision[field] != expected
    ]
    if mismatches:
        raise IdentityOnlyCurationError(
            "Artefakt decyzji identity-only nie odpowiada manifestowi: "
            + ", ".join(mismatches)
        )

    source_matches: list[tuple[Path, dict[str, Any], str]] = []
    strict_reviews: list[tuple[Path, dict[str, Any], str]] = []
    for candidate_path in allowed_root.rglob("*.json"):
        resolved = candidate_path.resolve()
        if resolved == artifact_path or not _is_within(resolved, allowed_root):
            continue
        candidate_sha256 = sha256_file(resolved)
        try:
            candidate_payload = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(candidate_payload, dict):
            continue
        if (
            candidate_payload.get("artifactType") == "strict_identity_review"
            and candidate_payload.get("caseId") == payload["caseId"]
        ):
            strict_reviews.append((resolved, candidate_payload, candidate_sha256))
        if (
            candidate_sha256 == decision["sourceReviewSha256"]
            and isinstance(candidate_payload.get("reviewId"), str)
            and candidate_payload["reviewId"].strip() == decision["sourceReviewId"]
        ):
            source_matches.append((resolved, candidate_payload, candidate_sha256))
    if len(source_matches) != 1:
        raise IdentityOnlyCurationError(
            "Nie znaleziono jednoznacznego źródłowego pakietu strict identity review"
        )
    source_path, source_review, _source_review_sha256 = source_matches[0]
    if (
        type(source_review.get("version")) is not int
        or source_review["version"] not in {2, 3}
        or source_review.get("artifactType") != "strict_identity_review"
        or source_review.get("caseId") != payload["caseId"]
        or source_review.get("operational") is not False
        or source_review.get("apply") != "forbidden"
    ):
        raise IdentityOnlyCurationError(
            "Źródłowy pakiet review nie jest oczekiwanym nieoperacyjnym pakietem strict identity review"
        )

    reviews_by_id: dict[str, list[tuple[Path, dict[str, Any], str]]] = {}
    normalized_supersedes_by_path: dict[Path, str | None] = {}
    for review_path, review, review_sha256 in strict_reviews:
        if (
            type(review.get("version")) is not int
            or review["version"] not in {2, 3}
            or review.get("operational") is not False
            or review.get("apply") != "forbidden"
        ):
            raise IdentityOnlyCurationError(
                "Każdy pakiet strict identity review dla case musi mieć version 2/3, "
                "operational=false i apply=forbidden"
            )
        review_id = _text(review.get("reviewId"), "strictReview.reviewId")
        reviews_by_id.setdefault(review_id, []).append(
            (review_path, review, review_sha256)
        )
        raw_supersedes_review_id = review.get("supersedesReviewId")
        normalized_supersedes_by_path[review_path] = (
            _text(
                raw_supersedes_review_id,
                "strictReview.supersedesReviewId",
            )
            if raw_supersedes_review_id is not None
            else None
        )
    duplicate_review_ids = sorted(
        review_id for review_id, matches in reviews_by_id.items() if len(matches) != 1
    )
    if duplicate_review_ids:
        raise IdentityOnlyCurationError(
            "Niejednoznaczne reviewId w pakietach strict identity review: "
            + ", ".join(duplicate_review_ids)
        )

    review_by_id = {
        review_id: matches[0] for review_id, matches in reviews_by_id.items()
    }
    successors_by_predecessor_id: dict[
        str, list[tuple[Path, dict[str, Any], str]]
    ] = {}
    for review_record in strict_reviews:
        review_path = review_record[0]
        supersedes_review_id = normalized_supersedes_by_path[review_path]
        if supersedes_review_id is not None:
            successors_by_predecessor_id.setdefault(
                supersedes_review_id, []
            ).append(review_record)

    for review_path, review, _review_sha256 in strict_reviews:
        supersedes_review_id = normalized_supersedes_by_path[review_path]
        if review.get("version") == 3 and (
            supersedes_review_id is None
            or review.get("supersededArtifactSha256") is None
        ):
            raise IdentityOnlyCurationError(
                "Pakiet strict identity review v3 wymaga supersedesReviewId "
                "i supersededArtifactSha256"
            )
        if supersedes_review_id is None:
            continue
        predecessor = review_by_id.get(supersedes_review_id)
        if predecessor is None:
            raise IdentityOnlyCurationError(
                "Pakiet strict identity review wskazuje brakujący predecessor "
                "w łańcuchu supersession"
            )
        predecessor_sha256 = _sha256(
            review.get("supersededArtifactSha256"),
            "strictReview.supersededArtifactSha256",
        )
        if predecessor[2] != predecessor_sha256:
            raise IdentityOnlyCurationConflict(
                "Hash poprzednika w łańcuchu strict identity review jest niezgodny"
            )

    for predecessor_review_id, successors in successors_by_predecessor_id.items():
        if len(successors) > 1:
            successor_ids = sorted(
                _text(successor.get("reviewId"), "strictReview.reviewId")
                for _path, successor, _sha256_value in successors
            )
            raise IdentityOnlyCurationError(
                "Łańcuch supersession strict identity review zawiera fork dla "
                + predecessor_review_id
                + ": "
                + ", ".join(successor_ids)
            )

    fully_validated_review_ids: set[str] = set()
    for review_id in review_by_id:
        chain_review_id = review_id
        current_path: set[str] = set()
        while chain_review_id not in fully_validated_review_ids:
            if chain_review_id in current_path:
                raise IdentityOnlyCurationError(
                    "Łańcuch supersession strict identity review zawiera cykl"
                )
            current_path.add(chain_review_id)
            chain_record = review_by_id[chain_review_id]
            predecessor_review_id = normalized_supersedes_by_path[chain_record[0]]
            if predecessor_review_id is None:
                break
            chain_review_id = predecessor_review_id
        fully_validated_review_ids.update(current_path)

    source_review_id = _text(source_review.get("reviewId"), "strictReview.reviewId")
    successors = successors_by_predecessor_id.get(source_review_id, [])
    if successors:
        successor_ids = sorted(
            _text(successor.get("reviewId"), "strictReview.reviewId")
            for _path, successor, _sha256_value in successors
        )
        raise IdentityOnlyCurationError(
            f"Decyzja {approval_mode} jest oparta na zastąpionym strict identity review; "
            "wymagany jest aktualny pakiet: " + ", ".join(successor_ids)
        )

    entries = source_review.get("entries")
    if not isinstance(entries, list):
        raise IdentityOnlyCurationError("Źródłowy pakiet review nie ma listy entries")
    matching_entries = [
        entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("candidateId") == payload["candidate"]["id"]
    ]
    if len(matching_entries) != 1:
        raise IdentityOnlyCurationError(
            "Źródłowy pakiet review nie wskazuje jednoznacznie kandydatury"
        )
    if matching_entries[0].get("humanDecision") != "pending":
        raise IdentityOnlyCurationError(
            "Źródłowy pakiet review nie zachowuje stanu humanDecision=pending"
        )
    if approval_mode == "agent" and source_review["version"] != 3:
        raise IdentityOnlyCurationError(
            "Decyzja agenta wymaga terminalnego strict identity review v3"
        )
    if source_review["version"] == 3 and (
        matching_entries[0].get("readiness") != "same_profile_shell_only_ready"
        or matching_entries[0].get("recommendedHumanDecision")
        != "same_profile_shell_only"
    ):
        raise IdentityOnlyCurationError(
            "Pakiet strict identity review v3 nie dopuszcza operacji identity-only "
            "dla tej kandydatury"
        )
    independent_review_refs: list[dict[str, Any]] = []
    if approval_mode == "agent":
        guardrails = source_review.get("guardrails")
        if not isinstance(guardrails, dict) or (
            guardrails.get("requiredApplyMechanism") != _AGENT_APPLY_MECHANISM
        ):
            raise IdentityOnlyCurationError(
                "Terminalny strict identity review nie dopuszcza agentowego "
                f"identity-only przez requiredApplyMechanism={_AGENT_APPLY_MECHANISM}"
            )
        independent_review_refs = _independent_review_refs(
            root,
            source_review.get("independentReviewRefs"),
            case_id=payload["caseId"],
            candidate_id=payload["candidate"]["id"],
            label="strictReview.independentReviewRefs",
            forbidden_paths={artifact_path, source_path},
        )
        decision_review_refs = _independent_review_refs(
            root,
            decision["independentReviewRefs"],
            case_id=payload["caseId"],
            candidate_id=payload["candidate"]["id"],
            label="agentDecision.independentReviewRefs",
            forbidden_paths={artifact_path, source_path},
        )
        if decision_review_refs != independent_review_refs:
            raise IdentityOnlyCurationError(
                "Decyzja agenta nie wiąże tych samych niezależnych review refs "
                "co terminalny strict identity review"
            )
    return {
        "artifactPath": artifact_path.relative_to(root.resolve()).as_posix(),
        "artifactSha256": actual_sha256,
        "decision": decision,
        "sourceReviewPath": source_path.relative_to(root.resolve()).as_posix(),
        "sourceReviewSha256": decision["sourceReviewSha256"],
        "sourceReview": source_review,
        "approvalMode": approval_mode,
        "decidedByKind": decision["decidedByKind"],
        "identityDecisionTier": (
            _HUMAN_DECISION_TIER
            if approval_mode == "human"
            else _AGENT_DECISION_TIER
        ),
        "independentReviewRefs": independent_review_refs,
    }


def _load_manifest_payload(path: Path) -> dict[str, Any]:
    payload = _load_json(path, "manifestu")

    actual_fields = set(payload)
    if actual_fields == _TOP_LEVEL_FIELDS:
        review_field = "humanReview"
    elif actual_fields == _AGENT_TOP_LEVEL_FIELDS:
        review_field = "agentReview"
    else:
        expected_fields = (
            _AGENT_TOP_LEVEL_FIELDS if "agentReview" in actual_fields else _TOP_LEVEL_FIELDS
        )
        payload = _strict_object(payload, "manifest", expected_fields)
        raise AssertionError("unreachable")
    candidate = _strict_object(payload["candidate"], "candidate", _CANDIDATE_FIELDS)
    external = _strict_object(payload["external"], "external", _EXTERNAL_FIELDS)
    local = _strict_object(payload["local"], "local", _LOCAL_FIELDS)
    _strict_object(
        payload[review_field], review_field, _HUMAN_REVIEW_FIELDS
    )

    if payload["version"] != SCHEMA_VERSION:
        raise IdentityOnlyCurationError("Manifest musi mieć version=1")
    if payload["operation"] != OPERATION:
        raise IdentityOnlyCurationError(
            f"Manifest musi mieć operation={OPERATION}"
        )
    for field in ("operationId", "reviewId", "caseId", "decidedBy", "rationale"):
        payload[field] = _text(payload[field], field)
    if len(payload["rationale"]) < 20:
        raise IdentityOnlyCurationError("Uzasadnienie musi mieć co najmniej 20 znaków")

    candidate["id"] = _text(candidate["id"], "candidate.id")
    if candidate["expectedStatus"] != "unresolved":
        raise IdentityOnlyCurationError("candidate.expectedStatus musi mieć wartość unresolved")
    if candidate["decision"] != "same":
        raise IdentityOnlyCurationError("Operacja identity-only dopuszcza wyłącznie decyzję same")

    for field in (
        "occurrenceId",
        "expectedPersonId",
        "expectedSourceItemId",
    ):
        external[field] = _text(external[field], f"external.{field}")
    if external["expectedVerificationStatus"] != "imported_unverified":
        raise IdentityOnlyCurationError(
            "external.expectedVerificationStatus musi mieć wartość imported_unverified"
        )
    if external["expectedSourceItemType"] != "external_tree_profile":
        raise IdentityOnlyCurationError(
            "external.expectedSourceItemType musi mieć wartość external_tree_profile"
        )
    if external["expectedSourcePrivacy"] != "restricted":
        raise IdentityOnlyCurationError(
            "external.expectedSourcePrivacy musi mieć wartość restricted"
        )
    if type(external["expectedOccurrenceCount"]) is not int or external[
        "expectedOccurrenceCount"
    ] != 1:
        raise IdentityOnlyCurationError("external.expectedOccurrenceCount musi mieć wartość 1")

    for field in ("personId", "anchorOccurrenceId", "expectedPersonId"):
        local[field] = _text(local[field], f"local.{field}")
    if local["personId"] != local["expectedPersonId"]:
        raise IdentityOnlyCurationError(
            "local.personId musi być zgodne z local.expectedPersonId"
        )
    if external["expectedPersonId"] == local["personId"]:
        raise IdentityOnlyCurationError(
            "Profil zewnętrzny i osoba lokalna muszą mieć różne personId przed operacją"
        )
    if external["occurrenceId"] == local["anchorOccurrenceId"]:
        raise IdentityOnlyCurationError(
            "Profil zewnętrzny i lokalny anchor muszą być różnymi wystąpieniami"
        )
    return payload


def _load_manifest(root: Path, path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = _load_manifest_payload(path)
    approval_mode = "agent" if "agentReview" in payload else "human"
    review_field = "agentReview" if approval_mode == "agent" else "humanReview"
    return payload, _review_gate(
        root,
        payload,
        payload[review_field],
        approval_mode=approval_mode,
    )


def _current_connection(root: Path, *, readonly: bool):  # type: ignore[no-untyped-def]
    backend = configured_database_backend(root)
    if not backend.path.is_file():
        raise IdentityOnlyCurationError("Nie znaleziono istniejącej bazy Genea")
    try:
        connection = backend.connect(readonly=readonly)
    except (OSError, RuntimeError) as error:
        raise IdentityOnlyCurationError(
            "Nie można otworzyć istniejącej bazy Genea"
        ) from error
    current = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if current != LATEST_SCHEMA_VERSION:
        connection.close()
        raise IdentityOnlyCurationError(
            "Operacja wymaga bieżącego schematu bazy; uruchom kontrolowaną migrację osobno"
        )
    return connection


def _row(connection, table: str, object_id: str):  # type: ignore[no-untyped-def]
    return connection.execute(f"SELECT * FROM {table} WHERE id=?", (object_id,)).fetchone()


def _rows(connection, query: str, parameters: tuple[Any, ...]) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
    return [dict(row) for row in connection.execute(query, parameters)]


def _protected_dependencies(
    connection,  # type: ignore[no-untyped-def]
    *,
    candidate_id: str,
    person_id: str,
    occurrence_id: str,
) -> dict[str, list[dict[str, Any]]]:
    checks = {
        "externalSync": (
            "SELECT * FROM external_sync_items WHERE object_type='person' AND object_id=? ORDER BY id",
            (person_id,),
        ),
        "researchQuestionPersonTargets": (
            "SELECT * FROM research_question_targets WHERE target_type='person' AND target_id=? ORDER BY id",
            (person_id,),
        ),
        "researchQuestionOccurrenceTargets": (
            "SELECT * FROM research_question_targets WHERE target_type='person_occurrence' AND target_id=? ORDER BY id",
            (occurrence_id,),
        ),
        "privatePersonFields": (
            "SELECT * FROM private_fields WHERE object_type='person' AND object_id=? ORDER BY id",
            (person_id,),
        ),
        "privateOccurrenceFields": (
            "SELECT * FROM private_fields WHERE object_type='person_occurrence' AND object_id=? ORDER BY id",
            (occurrence_id,),
        ),
        "familyNotes": (
            "SELECT * FROM family_notes WHERE (subject_type='person' AND subject_id=?) OR (subject_type='person_occurrence' AND subject_id=?) ORDER BY id",
            (person_id, occurrence_id),
        ),
        "privateAttachments": (
            "SELECT * FROM private_attachments WHERE (object_type='person' AND object_id=?) OR (object_type='person_occurrence' AND object_id=?) ORDER BY id",
            (person_id, occurrence_id),
        ),
        "privateRelationships": (
            "SELECT * FROM private_relationships WHERE subject_person_id=? OR object_person_id=? ORDER BY id",
            (person_id, person_id),
        ),
        "otherIdentityCandidates": (
            """SELECT * FROM identity_candidates
                 WHERE id<>?
                   AND status<>'dismissed'
                   AND (occurrence_a_id=? OR occurrence_b_id=?)
                 ORDER BY id""",
            (candidate_id, occurrence_id, occurrence_id),
        ),
    }
    return {
        label: rows
        for label, (query, parameters) in checks.items()
        if (rows := _rows(connection, query, parameters))
    }


def _operation_state(
    connection,  # type: ignore[no-untyped-def]
    payload: dict[str, Any],
    review_gate: dict[str, Any],
) -> dict[str, Any]:
    candidate_id = payload["candidate"]["id"]
    external_occurrence_id = payload["external"]["occurrenceId"]
    external_person_id = payload["external"]["expectedPersonId"]
    local_occurrence_id = payload["local"]["anchorOccurrenceId"]
    local_person_id = payload["local"]["personId"]
    source_id = payload["external"]["expectedSourceItemId"]
    dependencies = _protected_dependencies(
        connection,
        candidate_id=candidate_id,
        person_id=external_person_id,
        occurrence_id=external_occurrence_id,
    )
    return {
        "candidate": dict(row) if (row := _row(connection, "identity_candidates", candidate_id)) else None,
        "candidateDecisions": _rows(
            connection,
            "SELECT * FROM identity_decisions WHERE candidate_id=? ORDER BY id",
            (candidate_id,),
        ),
        "externalOccurrence": dict(row)
        if (row := _row(connection, "person_occurrences", external_occurrence_id))
        else None,
        "localOccurrence": dict(row)
        if (row := _row(connection, "person_occurrences", local_occurrence_id))
        else None,
        "externalPerson": dict(row)
        if (row := _row(connection, "persons", external_person_id))
        else None,
        "localPerson": dict(row) if (row := _row(connection, "persons", local_person_id)) else None,
        "externalSource": dict(row) if (row := _row(connection, "source_items", source_id)) else None,
        "sourcePrivacy": _rows(
            connection,
            "SELECT * FROM privacy_labels WHERE object_type='source_item' AND object_id=? ORDER BY id",
            (source_id,),
        ),
        "identityPrivacy": _rows(
            connection,
            """SELECT * FROM privacy_labels
                 WHERE (object_type='person' AND object_id IN (?,?))
                    OR (object_type='person_occurrence' AND object_id IN (?,?))
                 ORDER BY object_type,object_id,id""",
            (
                external_person_id,
                local_person_id,
                external_occurrence_id,
                local_occurrence_id,
            ),
        ),
        "externalPersonOccurrences": _rows(
            connection,
            "SELECT * FROM person_occurrences WHERE person_id=? ORDER BY id",
            (external_person_id,),
        ),
        "externalMemberships": _rows(
            connection,
            "SELECT * FROM household_memberships WHERE person_id=? OR occurrence_id=? ORDER BY id",
            (external_person_id, external_occurrence_id),
        ),
        "personNames": _rows(
            connection,
            "SELECT * FROM person_names WHERE person_id IN (?,?) ORDER BY id",
            (external_person_id, local_person_id),
        ),
        "protectedDependencies": dependencies,
        "case": dict(row) if (row := _row(connection, "research_cases", payload["caseId"])) else None,
        "humanReviewGate": review_gate,
    }


def _inspect(  # type: ignore[no-untyped-def]
    connection, payload: dict[str, Any], review_gate: dict[str, Any]
) -> dict[str, Any]:
    state = _operation_state(connection, payload, review_gate)
    candidate = state["candidate"]
    external_occurrence = state["externalOccurrence"]
    local_occurrence = state["localOccurrence"]
    external_person = state["externalPerson"]
    local_person = state["localPerson"]
    source = state["externalSource"]
    external = payload["external"]
    local = payload["local"]
    blockers: list[str] = []

    if state["case"] is None:
        blockers.append("missing_case")
    if candidate is None:
        blockers.append("missing_candidate")
    else:
        if candidate["status"] != payload["candidate"]["expectedStatus"]:
            blockers.append("candidate_status_mismatch")
        expected_endpoints = {
            external["occurrenceId"],
            local["anchorOccurrenceId"],
        }
        actual_endpoints = {candidate["occurrence_a_id"], candidate["occurrence_b_id"]}
        if actual_endpoints != expected_endpoints:
            blockers.append("candidate_endpoints_mismatch")
    if state["candidateDecisions"]:
        blockers.append("candidate_already_has_decision")

    if external_occurrence is None:
        blockers.append("missing_external_occurrence")
    else:
        if external_occurrence["person_id"] != external["expectedPersonId"]:
            blockers.append("external_occurrence_person_mismatch")
        if external_occurrence["source_item_id"] != external["expectedSourceItemId"]:
            blockers.append("external_occurrence_source_mismatch")
        if external_occurrence["verification_status"] != external["expectedVerificationStatus"]:
            blockers.append("external_occurrence_verification_mismatch")
    if local_occurrence is None:
        blockers.append("missing_local_anchor_occurrence")
    elif local_occurrence["person_id"] != local["expectedPersonId"]:
        blockers.append("local_anchor_person_mismatch")
    if external_person is None:
        blockers.append("missing_external_person")
    elif external_person["status"] == "merged":
        blockers.append("external_person_already_merged")
    if local_person is None:
        blockers.append("missing_local_person")

    if source is None:
        blockers.append("missing_external_source")
    else:
        if source["item_type"] != external["expectedSourceItemType"]:
            blockers.append("external_source_type_mismatch")
        if source["verification_status"] != external["expectedVerificationStatus"]:
            blockers.append("external_source_verification_mismatch")
    privacy = state["sourcePrivacy"]
    if len(privacy) != 1 or privacy[0]["classification"] != external["expectedSourcePrivacy"]:
        blockers.append("external_source_privacy_mismatch")
    if len(state["externalPersonOccurrences"]) != external["expectedOccurrenceCount"]:
        blockers.append("external_person_occurrence_count_mismatch")
    if state["externalMemberships"]:
        blockers.append("external_shell_has_memberships")
    if state["protectedDependencies"]:
        blockers.append("external_shell_has_protected_dependencies")

    state_fingerprint = fingerprint(state)
    impact = {
        "occurrenceUpdates": [
            {
                "occurrenceId": external["occurrenceId"],
                "fromPersonId": external["expectedPersonId"],
                "toPersonId": local["personId"],
            }
        ],
        "identityDecision": {
            "candidateId": payload["candidate"]["id"],
            "decision": "same",
            "identityDecisionTier": review_gate["identityDecisionTier"],
            "candidateStatus": "resolved_same",
        },
        "externalShell": {
            "personId": external["expectedPersonId"],
            "action": "mark_merged",
            "fromStatus": external_person["status"] if external_person else None,
            "toStatus": "merged",
        },
        "unchanged": dict(_UNCHANGED_IMPACT),
    }
    return {
        "operation": OPERATION,
        "operationId": payload["operationId"],
        "reviewId": payload["reviewId"],
        "caseId": payload["caseId"],
        "candidateId": payload["candidate"]["id"],
        "expectedStatus": payload["candidate"]["expectedStatus"],
        "approvalMode": review_gate["approvalMode"],
        "decidedByKind": review_gate["decidedByKind"],
        "identityDecisionTier": review_gate["identityDecisionTier"],
        "fingerprint": state_fingerprint,
        "commandIdRequired": True,
        "applyAllowed": not blockers,
        "blockers": blockers,
        "protectedDependencies": sorted(state["protectedDependencies"]),
        "impact": impact,
    }


def identity_only_external_shell_merge_preview(
    root: Path, manifest: Path
) -> dict[str, Any]:
    """Validate and preview one explicit identity-only shell merge."""

    payload, review_gate = _load_manifest(root, manifest)
    with _current_connection(root, readonly=True) as connection:
        require_tables(connection, REQUIRED_TABLES)
        return _inspect(connection, payload, review_gate)


def apply_identity_only_external_shell_merge(
    root: Path,
    manifest: Path,
    *,
    expected_fingerprint: str,
    command_id: str,
) -> dict[str, Any]:
    """Apply one reviewed identity-only operation without broad person merging."""

    payload = _load_manifest_payload(manifest)
    expected_fingerprint = _text(expected_fingerprint, "expectedFingerprint")
    command_id = _text(command_id, "commandId")
    manifest_fingerprint = fingerprint(payload)
    request_fingerprint = fingerprint(
        {
            "manifestFingerprint": manifest_fingerprint,
            "expectedFingerprint": expected_fingerprint,
        }
    )
    with _current_connection(root, readonly=False) as connection:
        require_tables(connection, REQUIRED_TABLES)
        connection.execute("BEGIN IMMEDIATE")
        try:
            replay = replay_command(
                connection,
                command_id=command_id,
                command_type=OPERATION,
                object_type="identity_candidate",
                object_id=payload["candidate"]["id"],
                request_fingerprint=request_fingerprint,
            )
        except CommandEnvelopeConflict as error:
            raise IdentityOnlyCurationConflict(str(error)) from error
        if replay is not None:
            return replay

        approval_mode = "agent" if "agentReview" in payload else "human"
        review_field = "agentReview" if approval_mode == "agent" else "humanReview"
        review_gate = _review_gate(
            root,
            payload,
            payload[review_field],
            approval_mode=approval_mode,
        )
        preview = _inspect(connection, payload, review_gate)
        if preview["fingerprint"] != expected_fingerprint:
            raise IdentityOnlyCurationConflict(
                "Stan operacji zmienił się po preview; odśwież fingerprint i ponów decyzję"
            )
        if not preview["applyAllowed"]:
            raise IdentityOnlyCurationError(
                "Operacja identity-only jest zablokowana: " + ", ".join(preview["blockers"])
            )

        candidate_id = payload["candidate"]["id"]
        external_occurrence_id = payload["external"]["occurrenceId"]
        external_person_id = payload["external"]["expectedPersonId"]
        local_person_id = payload["local"]["personId"]
        decided_by = payload["decidedBy"]
        rationale = payload["rationale"]
        decision_id = stable_id("identity_decision", OPERATION, candidate_id, command_id)
        now = utc_now()
        review_prefix = "agentReview" if approval_mode == "agent" else "humanReview"
        review_references = {
            f"{review_prefix}ArtifactPath": review_gate["artifactPath"],
            f"{review_prefix}Sha256": review_gate["artifactSha256"],
            "sourceReviewPath": review_gate["sourceReviewPath"],
            "sourceReviewSha256": review_gate["sourceReviewSha256"],
        }
        result = {
            "operation": OPERATION,
            "operationId": payload["operationId"],
            "reviewId": payload["reviewId"],
            "caseId": payload["caseId"],
            "candidateId": candidate_id,
            "status": "resolved_same",
            "decision": "same",
            "decidedByKind": review_gate["decidedByKind"],
            "identityDecisionTier": review_gate["identityDecisionTier"],
            "decisionId": decision_id,
            "commandId": command_id,
            "inputFingerprint": expected_fingerprint,
            "manifestFingerprint": manifest_fingerprint,
            "projectionStatus": "pending",
            "impact": preview["impact"],
            **review_references,
        }
        if approval_mode == "agent":
            result["independentReviewRefs"] = review_gate["independentReviewRefs"]
        record_command(
            connection,
            command_id=command_id,
            command_type=OPERATION,
            object_type="identity_candidate",
            object_id=candidate_id,
            input_fingerprint=expected_fingerprint,
            request_fingerprint=request_fingerprint,
            actor=decided_by,
            rationale=rationale,
            result=result,
        )
        occurrence_update = connection.execute(
            "UPDATE person_occurrences SET person_id=? WHERE id=? AND person_id=?",
            (local_person_id, external_occurrence_id, external_person_id),
        )
        if occurrence_update.rowcount != 1:
            raise IdentityOnlyCurationConflict(
                "Wskazane wystąpienie zewnętrzne zmieniło przypisanie personId"
            )
        candidate_update = connection.execute(
            "UPDATE identity_candidates SET status='resolved_same' WHERE id=? AND status='unresolved'",
            (candidate_id,),
        )
        if candidate_update.rowcount != 1:
            raise IdentityOnlyCurationConflict("Kandydatura nie jest już w stanie unresolved")
        shell_update = connection.execute(
            "UPDATE persons SET status='merged' WHERE id=? AND status<>'merged'",
            (external_person_id,),
        )
        if shell_update.rowcount != 1:
            raise IdentityOnlyCurationConflict("Profil zewnętrzny nie może zostać oznaczony jako merged")
        connection.execute(
            """INSERT INTO identity_decisions(
                   id,candidate_id,decision,decided_by,decided_at,rationale,
                   command_id,input_fingerprint
               ) VALUES (?,?,?,?,?,?,?,?)""",
            (
                decision_id,
                candidate_id,
                "same",
                decided_by,
                now,
                rationale,
                command_id,
                expected_fingerprint,
            ),
        )
        connection.execute(
            """INSERT INTO audit_log(
                   occurred_at,actor,action,object_type,object_id,
                   before_json,after_json,rationale
               ) VALUES (?,?,?,?,?,?,?,?)""",
            (
                now,
                decided_by,
                OPERATION,
                "identity_candidate",
                candidate_id,
                json.dumps(
                    {
                        "candidateStatus": "unresolved",
                        "externalOccurrencePersonId": external_person_id,
                        "externalShellStatus": preview["impact"]["externalShell"]["fromStatus"],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                json.dumps(
                    {
                        "candidateStatus": "resolved_same",
                        "decision": "same",
                        "decidedByKind": review_gate["decidedByKind"],
                        "identityDecisionTier": review_gate["identityDecisionTier"],
                        "decisionId": decision_id,
                        "externalOccurrencePersonId": local_person_id,
                        "externalShellStatus": "merged",
                        "unchanged": _UNCHANGED_IMPACT,
                        **review_references,
                        **(
                            {
                                "independentReviewRefs": review_gate[
                                    "independentReviewRefs"
                                ]
                            }
                            if approval_mode == "agent"
                            else {}
                        ),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                rationale,
            ),
        )
        return {**result, "alreadyApplied": False}


def _load_external_candidate_disposition_payload(path: Path) -> dict[str, Any]:
    payload = _strict_object(
        _load_json(path, "manifestu dyspozycji kandydatury zewnętrznej"),
        "external candidate disposition manifest",
        _EXTERNAL_CANDIDATE_DISPOSITION_TOP_LEVEL_FIELDS,
    )
    candidate = _strict_object(
        payload["candidate"],
        "candidate",
        _EXTERNAL_CANDIDATE_DISPOSITION_CANDIDATE_FIELDS,
    )
    external = _strict_object(
        payload["external"],
        "external",
        _EXTERNAL_CANDIDATE_DISPOSITION_EXTERNAL_FIELDS,
    )
    local = _strict_object(
        payload["local"],
        "local",
        _EXTERNAL_CANDIDATE_DISPOSITION_LOCAL_FIELDS,
    )
    _strict_object(
        payload["humanDisposition"],
        "humanDisposition",
        _HUMAN_REVIEW_FIELDS,
    )
    if payload["version"] != 1:
        raise IdentityOnlyCurationError("Manifest dyspozycji musi mieć version=1")
    if payload["operation"] != EXTERNAL_CANDIDATE_DISPOSITION_OPERATION:
        raise IdentityOnlyCurationError(
            "Manifest dyspozycji musi mieć operation="
            + EXTERNAL_CANDIDATE_DISPOSITION_OPERATION
        )
    for field in ("operationId", "reviewId", "caseId", "decidedBy", "rationale"):
        payload[field] = _text(payload[field], f"disposition.{field}")
    if len(payload["rationale"]) < 20:
        raise IdentityOnlyCurationError(
            "Uzasadnienie dyspozycji musi mieć co najmniej 20 znaków"
        )
    candidate["id"] = _text(candidate["id"], "candidate.id")
    if candidate["expectedStatus"] != "unresolved":
        raise IdentityOnlyCurationError(
            "candidate.expectedStatus musi mieć wartość unresolved"
        )
    if candidate["disposition"] != "dismissed":
        raise IdentityOnlyCurationError(
            "candidate.disposition musi mieć wartość dismissed"
        )
    for field in ("occurrenceId", "expectedPersonId", "expectedSourceItemId"):
        external[field] = _text(external[field], f"external.{field}")
    if external["expectedVerificationStatus"] != "imported_unverified":
        raise IdentityOnlyCurationError(
            "external.expectedVerificationStatus musi mieć wartość imported_unverified"
        )
    if external["expectedSourceItemType"] != "external_tree_profile":
        raise IdentityOnlyCurationError(
            "external.expectedSourceItemType musi mieć wartość external_tree_profile"
        )
    if external["expectedSourcePrivacy"] != "restricted":
        raise IdentityOnlyCurationError(
            "external.expectedSourcePrivacy musi mieć wartość restricted"
        )
    for field in ("occurrenceId", "expectedPersonId"):
        local[field] = _text(local[field], f"local.{field}")
    if external["occurrenceId"] == local["occurrenceId"]:
        raise IdentityOnlyCurationError(
            "Zewnętrzne i lokalne occurrence muszą być różne"
        )
    return payload


def _external_candidate_disposition_review_gate(
    root: Path, payload: dict[str, Any]
) -> dict[str, Any]:
    reference = payload["humanDisposition"]
    artifact_path_text = _text(
        reference["artifactPath"], "humanDisposition.artifactPath"
    )
    if Path(artifact_path_text).is_absolute():
        raise IdentityOnlyCurationError(
            "humanDisposition.artifactPath musi być ścieżką względną repozytorium"
        )
    allowed_root = (root / "research/records/reassessments").resolve()
    artifact_path = (root / artifact_path_text).resolve()
    if not _is_within(artifact_path, allowed_root):
        raise IdentityOnlyCurationError(
            "Artefakt dyspozycji musi znajdować się w research/records/reassessments"
        )
    if not artifact_path.is_file():
        raise IdentityOnlyCurationError("Nie znaleziono artefaktu dyspozycji")
    expected_sha256 = _sha256(
        reference["expectedSha256"], "humanDisposition.expectedSha256"
    )
    actual_sha256 = sha256_file(artifact_path)
    if actual_sha256 != expected_sha256:
        raise IdentityOnlyCurationConflict(
            "Hash artefaktu dyspozycji nie odpowiada manifestowi"
        )
    artifact = _strict_object(
        _load_json(artifact_path, "artefaktu dyspozycji kandydatury"),
        "human disposition artifact",
        _EXTERNAL_CANDIDATE_DISPOSITION_ARTIFACT_FIELDS,
    )
    if artifact["version"] != 1:
        raise IdentityOnlyCurationError("Artefakt dyspozycji musi mieć version=1")
    if artifact["artifactType"] != "strict_identity_human_disposition":
        raise IdentityOnlyCurationError(
            "Artefakt dyspozycji musi mieć artifactType=strict_identity_human_disposition"
        )
    if artifact["disposition"] != "dismissed":
        raise IdentityOnlyCurationError(
            "Artefakt dyspozycji musi mieć disposition=dismissed"
        )
    if artifact["decidedByKind"] != "human":
        raise IdentityOnlyCurationError(
            "Artefakt dyspozycji musi mieć decidedByKind=human"
        )
    for field in (
        "reviewId",
        "sourceReviewId",
        "caseId",
        "candidateId",
        "externalOccurrenceId",
        "localOccurrenceId",
        "decidedBy",
        "decidedAt",
        "rationale",
    ):
        artifact[field] = _text(artifact[field], f"humanDisposition.{field}")
    artifact["sourceReviewSha256"] = _sha256(
        artifact["sourceReviewSha256"], "humanDisposition.sourceReviewSha256"
    )
    try:
        decided_at = datetime.fromisoformat(artifact["decidedAt"].replace("Z", "+00:00"))
    except ValueError as error:
        raise IdentityOnlyCurationError(
            "humanDisposition.decidedAt musi być znacznikiem ISO-8601"
        ) from error
    if decided_at.tzinfo is None:
        raise IdentityOnlyCurationError(
            "humanDisposition.decidedAt musi zawierać strefę czasową"
        )
    expected_matches = {
        "reviewId": payload["reviewId"],
        "caseId": payload["caseId"],
        "candidateId": payload["candidate"]["id"],
        "externalOccurrenceId": payload["external"]["occurrenceId"],
        "localOccurrenceId": payload["local"]["occurrenceId"],
        "decidedBy": payload["decidedBy"],
        "rationale": payload["rationale"],
    }
    mismatches = [
        field
        for field, expected in expected_matches.items()
        if artifact[field] != expected
    ]
    if mismatches:
        raise IdentityOnlyCurationError(
            "Artefakt dyspozycji nie odpowiada manifestowi: "
            + ", ".join(mismatches)
        )

    strict_reviews: list[tuple[Path, dict[str, Any], str]] = []
    source_matches: list[tuple[Path, dict[str, Any], str]] = []
    for candidate_path in allowed_root.rglob("*.json"):
        resolved = candidate_path.resolve()
        if resolved == artifact_path or not _is_within(resolved, allowed_root):
            continue
        try:
            candidate_payload = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(candidate_payload, dict):
            continue
        candidate_sha256 = sha256_file(resolved)
        if (
            candidate_payload.get("artifactType") == "strict_identity_review"
            and candidate_payload.get("caseId") == payload["caseId"]
        ):
            strict_reviews.append((resolved, candidate_payload, candidate_sha256))
        if (
            candidate_sha256 == artifact["sourceReviewSha256"]
            and candidate_payload.get("reviewId") == artifact["sourceReviewId"]
        ):
            source_matches.append((resolved, candidate_payload, candidate_sha256))
    if len(source_matches) != 1:
        raise IdentityOnlyCurationError(
            "Nie znaleziono jednoznacznego źródłowego strict identity review dyspozycji"
        )
    source_path, source_review, source_sha256 = source_matches[0]
    if (
        source_review.get("version") != 3
        or source_review.get("artifactType") != "strict_identity_review"
        or source_review.get("caseId") != payload["caseId"]
        or source_review.get("operational") is not False
        or source_review.get("apply") != "forbidden"
    ):
        raise IdentityOnlyCurationError(
            "Dyspozycja wymaga nieoperacyjnego strict identity review v3"
        )

    reviews_by_id: dict[str, tuple[Path, dict[str, Any], str]] = {}
    successors: dict[str, list[str]] = {}
    predecessors: dict[str, str | None] = {}
    for review_path, review, review_sha256 in strict_reviews:
        if (
            type(review.get("version")) is not int
            or review["version"] not in {2, 3}
            or review.get("operational") is not False
            or review.get("apply") != "forbidden"
        ):
            raise IdentityOnlyCurationError(
                "Każdy strict identity review dla sprawy musi być poprawnym v2/v3"
            )
        review_id = _text(review.get("reviewId"), "strictReview.reviewId")
        if review_id in reviews_by_id:
            raise IdentityOnlyCurationError(
                "Niejednoznaczne reviewId w łańcuchu strict identity review"
            )
        reviews_by_id[review_id] = (review_path, review, review_sha256)
    for review_id, (_review_path, review, _review_sha256) in reviews_by_id.items():
        predecessor_id = review.get("supersedesReviewId")
        predecessor_sha256 = review.get("supersededArtifactSha256")
        if review["version"] == 3 and (
            predecessor_id is None or predecessor_sha256 is None
        ):
            raise IdentityOnlyCurationError(
                "Strict identity review v3 dyspozycji wymaga kompletnego predecessor ref"
            )
        if predecessor_id is None:
            predecessors[review_id] = None
            continue
        predecessor_id = _text(
            predecessor_id, "strictReview.supersedesReviewId"
        )
        predecessor = reviews_by_id.get(predecessor_id)
        if predecessor is None:
            raise IdentityOnlyCurationError(
                "Strict identity review dyspozycji wskazuje brakujący predecessor"
            )
        if predecessor[2] != _sha256(
            predecessor_sha256, "strictReview.supersededArtifactSha256"
        ):
            raise IdentityOnlyCurationConflict(
                "Hash predecessor strict identity review dyspozycji jest niezgodny"
            )
        predecessors[review_id] = predecessor_id
        successors.setdefault(predecessor_id, []).append(review_id)
    if any(len(ids) > 1 for ids in successors.values()):
        raise IdentityOnlyCurationError(
            "Łańcuch strict identity review dyspozycji zawiera fork"
        )
    for review_id in reviews_by_id:
        seen: set[str] = set()
        current: str | None = review_id
        while current is not None:
            if current in seen:
                raise IdentityOnlyCurationError(
                    "Łańcuch strict identity review dyspozycji zawiera cykl"
                )
            seen.add(current)
            current = predecessors.get(current)
    source_review_id = _text(source_review["reviewId"], "strictReview.reviewId")
    if successors.get(source_review_id):
        raise IdentityOnlyCurationError(
            "Dyspozycja musi wskazywać terminalny strict identity review v3"
        )
    entries = source_review.get("entries")
    matching_entries = (
        [
            entry
            for entry in entries
            if isinstance(entry, dict)
            and entry.get("candidateId") == payload["candidate"]["id"]
        ]
        if isinstance(entries, list)
        else []
    )
    if len(matching_entries) != 1:
        raise IdentityOnlyCurationError(
            "Strict review dyspozycji nie wskazuje jednoznacznie kandydatury"
        )
    entry = matching_entries[0]
    if entry.get("localPersonId") not in {
        None,
        payload["local"]["expectedPersonId"],
    }:
        raise IdentityOnlyCurationError(
            "Strict review dyspozycji wskazuje inną lokalną osobę"
        )
    if entry.get("currentStatus") not in {None, "unresolved"}:
        raise IdentityOnlyCurationError(
            "Strict review dyspozycji ma niezgodny currentStatus"
        )
    if entry.get("humanDecision") != "pending":
        raise IdentityOnlyCurationError(
            "Strict review dyspozycji nie zachowuje humanDecision=pending"
        )
    if (
        entry.get("readiness") != _EXTERNAL_CANDIDATE_DISPOSITION_READINESS
        or entry.get("recommendedHumanDecision")
        != _EXTERNAL_CANDIDATE_DISPOSITION_RECOMMENDATION
    ):
        raise IdentityOnlyCurationError(
            "Strict review nie dopuszcza dismissed z zachowaniem historii"
        )
    return {
        "artifactPath": artifact_path.relative_to(root.resolve()).as_posix(),
        "artifactSha256": actual_sha256,
        "artifact": artifact,
        "sourceReviewPath": source_path.relative_to(root.resolve()).as_posix(),
        "sourceReviewSha256": source_sha256,
        "sourceReview": source_review,
    }


def _external_candidate_disposition_inspect(  # type: ignore[no-untyped-def]
    connection, payload: dict[str, Any], review_gate: dict[str, Any]
) -> dict[str, Any]:
    candidate_id = payload["candidate"]["id"]
    external = payload["external"]
    local = payload["local"]
    candidate = _row(connection, "identity_candidates", candidate_id)
    external_occurrence = _row(
        connection, "person_occurrences", external["occurrenceId"]
    )
    local_occurrence = _row(connection, "person_occurrences", local["occurrenceId"])
    external_person = _row(connection, "persons", external["expectedPersonId"])
    local_person = _row(connection, "persons", local["expectedPersonId"])
    source = _row(connection, "source_items", external["expectedSourceItemId"])
    state = {
        "case": dict(row)
        if (row := _row(connection, "research_cases", payload["caseId"]))
        else None,
        "candidate": dict(candidate) if candidate else None,
        "candidateDecisions": _rows(
            connection,
            "SELECT * FROM identity_decisions WHERE candidate_id=? ORDER BY id",
            (candidate_id,),
        ),
        "externalOccurrence": dict(external_occurrence)
        if external_occurrence
        else None,
        "localOccurrence": dict(local_occurrence) if local_occurrence else None,
        "externalPerson": dict(external_person) if external_person else None,
        "localPerson": dict(local_person) if local_person else None,
        "externalSource": dict(source) if source else None,
        "sourcePrivacy": _rows(
            connection,
            "SELECT * FROM privacy_labels WHERE object_type='source_item' AND object_id=? ORDER BY id",
            (external["expectedSourceItemId"],),
        ),
        "identityPrivacy": _rows(
            connection,
            """SELECT * FROM privacy_labels
                 WHERE (object_type='person' AND object_id IN (?,?))
                    OR (object_type='person_occurrence' AND object_id IN (?,?))
                 ORDER BY object_type,object_id,id""",
            (
                external["expectedPersonId"],
                local["expectedPersonId"],
                external["occurrenceId"],
                local["occurrenceId"],
            ),
        ),
        "reviewGate": review_gate,
    }
    blockers: list[str] = []
    if state["case"] is None:
        blockers.append("missing_case")
    if candidate is None:
        blockers.append("missing_candidate")
    else:
        if candidate["status"] != payload["candidate"]["expectedStatus"]:
            blockers.append("candidate_status_mismatch")
        if {candidate["occurrence_a_id"], candidate["occurrence_b_id"]} != {
            external["occurrenceId"],
            local["occurrenceId"],
        }:
            blockers.append("candidate_endpoints_mismatch")
    if state["candidateDecisions"]:
        blockers.append("candidate_already_has_decision")
    if external_occurrence is None:
        blockers.append("missing_external_occurrence")
    else:
        if external_occurrence["person_id"] != external["expectedPersonId"]:
            blockers.append("external_occurrence_person_mismatch")
        if external_occurrence["source_item_id"] != external["expectedSourceItemId"]:
            blockers.append("external_occurrence_source_mismatch")
        if (
            external_occurrence["verification_status"]
            != external["expectedVerificationStatus"]
        ):
            blockers.append("external_occurrence_verification_mismatch")
    if local_occurrence is None:
        blockers.append("missing_local_occurrence")
    elif local_occurrence["person_id"] != local["expectedPersonId"]:
        blockers.append("local_occurrence_person_mismatch")
    if external_person is None:
        blockers.append("missing_external_person")
    if local_person is None:
        blockers.append("missing_local_person")
    if source is None:
        blockers.append("missing_external_source")
    else:
        if source["item_type"] != external["expectedSourceItemType"]:
            blockers.append("external_source_type_mismatch")
        if source["verification_status"] != external["expectedVerificationStatus"]:
            blockers.append("external_source_verification_mismatch")
    if (
        len(state["sourcePrivacy"]) != 1
        or state["sourcePrivacy"][0]["classification"]
        != external["expectedSourcePrivacy"]
    ):
        blockers.append("external_source_privacy_mismatch")
    write_set = [
        {
            "table": "identity_candidates",
            "action": "update_status",
            "id": candidate_id,
            "from": "unresolved",
            "to": "dismissed",
        },
        {"table": "identity_decisions", "action": "insert_one"},
        {"table": "curation_commands", "action": "insert_one"},
        {"table": "audit_log", "action": "insert_one"},
    ]
    impact = {
        "candidateStatus": {
            "candidateId": candidate_id,
            "from": "unresolved",
            "to": "dismissed",
        },
        "identityDecision": {
            "candidateId": candidate_id,
            "decision": "dismissed",
            "identityConclusion": None,
            "proofStatus": "not_identity_proof",
        },
        "unchanged": dict(_EXTERNAL_CANDIDATE_DISPOSITION_UNCHANGED),
    }
    return {
        "operation": EXTERNAL_CANDIDATE_DISPOSITION_OPERATION,
        "operationId": payload["operationId"],
        "reviewId": payload["reviewId"],
        "caseId": payload["caseId"],
        "candidateId": candidate_id,
        "expectedStatus": "unresolved",
        "disposition": "dismissed",
        "decidedByKind": "human",
        "fingerprint": fingerprint(state),
        "commandIdRequired": True,
        "applyAllowed": not blockers,
        "blockers": blockers,
        "writeSet": write_set,
        "impact": impact,
        "humanDispositionArtifactPath": review_gate["artifactPath"],
        "humanDispositionSha256": review_gate["artifactSha256"],
        "sourceReviewPath": review_gate["sourceReviewPath"],
        "sourceReviewSha256": review_gate["sourceReviewSha256"],
    }


def identity_only_external_candidate_disposition_preview(
    root: Path, manifest: Path
) -> dict[str, Any]:
    """Preview a human-only dismissal that preserves the candidate record."""

    payload = _load_external_candidate_disposition_payload(manifest)
    review_gate = _external_candidate_disposition_review_gate(root, payload)
    with _current_connection(root, readonly=True) as connection:
        require_tables(connection, REQUIRED_TABLES)
        return _external_candidate_disposition_inspect(
            connection, payload, review_gate
        )


def apply_identity_only_external_candidate_disposition(
    root: Path,
    manifest: Path,
    *,
    expected_fingerprint: str,
    command_id: str,
) -> dict[str, Any]:
    """Record a human-only dismissed disposition without identity inference."""

    payload = _load_external_candidate_disposition_payload(manifest)
    expected_fingerprint = _text(expected_fingerprint, "expectedFingerprint")
    command_id = _text(command_id, "commandId")
    manifest_fingerprint = fingerprint(payload)
    request_fingerprint = fingerprint(
        {
            "manifestFingerprint": manifest_fingerprint,
            "expectedFingerprint": expected_fingerprint,
        }
    )
    candidate_id = payload["candidate"]["id"]
    with _current_connection(root, readonly=False) as connection:
        require_tables(connection, REQUIRED_TABLES)
        connection.execute("BEGIN IMMEDIATE")
        try:
            replay = replay_command(
                connection,
                command_id=command_id,
                command_type=EXTERNAL_CANDIDATE_DISPOSITION_OPERATION,
                object_type="identity_candidate",
                object_id=candidate_id,
                request_fingerprint=request_fingerprint,
            )
        except CommandEnvelopeConflict as error:
            raise IdentityOnlyCurationConflict(str(error)) from error
        if replay is not None:
            return replay
        review_gate = _external_candidate_disposition_review_gate(root, payload)
        preview = _external_candidate_disposition_inspect(
            connection, payload, review_gate
        )
        if preview["fingerprint"] != expected_fingerprint:
            raise IdentityOnlyCurationConflict(
                "Stan dyspozycji zmienił się po preview; odśwież fingerprint"
            )
        if not preview["applyAllowed"]:
            raise IdentityOnlyCurationError(
                "Dyspozycja kandydatury jest zablokowana: "
                + ", ".join(preview["blockers"])
            )
        decided_by = payload["decidedBy"]
        rationale = payload["rationale"]
        now = utc_now()
        decision_id = stable_id(
            "identity_decision",
            EXTERNAL_CANDIDATE_DISPOSITION_OPERATION,
            candidate_id,
            command_id,
        )
        provenance = {
            "humanDispositionArtifactPath": review_gate["artifactPath"],
            "humanDispositionSha256": review_gate["artifactSha256"],
            "sourceReviewPath": review_gate["sourceReviewPath"],
            "sourceReviewSha256": review_gate["sourceReviewSha256"],
        }
        result = {
            "operation": EXTERNAL_CANDIDATE_DISPOSITION_OPERATION,
            "operationId": payload["operationId"],
            "reviewId": payload["reviewId"],
            "caseId": payload["caseId"],
            "candidateId": candidate_id,
            "status": "dismissed",
            "disposition": "dismissed",
            "decision": "dismissed",
            "identityConclusion": None,
            "proofStatus": "not_identity_proof",
            "decidedByKind": "human",
            "decisionId": decision_id,
            "commandId": command_id,
            "inputFingerprint": expected_fingerprint,
            "manifestFingerprint": manifest_fingerprint,
            "projectionStatus": "not_required",
            "writeSet": preview["writeSet"],
            "impact": preview["impact"],
            **provenance,
        }
        record_command(
            connection,
            command_id=command_id,
            command_type=EXTERNAL_CANDIDATE_DISPOSITION_OPERATION,
            object_type="identity_candidate",
            object_id=candidate_id,
            input_fingerprint=expected_fingerprint,
            request_fingerprint=request_fingerprint,
            actor=decided_by,
            rationale=rationale,
            result=result,
            projection_status="not_required",
        )
        updated = connection.execute(
            "UPDATE identity_candidates SET status='dismissed' WHERE id=? AND status='unresolved'",
            (candidate_id,),
        )
        if updated.rowcount != 1:
            raise IdentityOnlyCurationConflict(
                "Kandydatura nie jest już w stanie unresolved"
            )
        connection.execute(
            """INSERT INTO identity_decisions(
                   id,candidate_id,decision,decided_by,decided_at,rationale,
                   command_id,input_fingerprint
               ) VALUES (?,?,?,?,?,?,?,?)""",
            (
                decision_id,
                candidate_id,
                "dismissed",
                decided_by,
                now,
                rationale,
                command_id,
                expected_fingerprint,
            ),
        )
        connection.execute(
            """INSERT INTO audit_log(
                   occurred_at,actor,action,object_type,object_id,
                   before_json,after_json,rationale
               ) VALUES (?,?,?,?,?,?,?,?)""",
            (
                now,
                decided_by,
                EXTERNAL_CANDIDATE_DISPOSITION_OPERATION,
                "identity_candidate",
                candidate_id,
                json.dumps(
                    {"candidateStatus": "unresolved"},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                json.dumps(
                    {
                        "candidateStatus": "dismissed",
                        "decision": "dismissed",
                        "identityConclusion": None,
                        "proofStatus": "not_identity_proof",
                        "decidedByKind": "human",
                        "decisionId": decision_id,
                        "unchanged": _EXTERNAL_CANDIDATE_DISPOSITION_UNCHANGED,
                        **provenance,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                rationale,
            ),
        )
        return {**result, "alreadyApplied": False}


def _load_ratification_manifest(
    root: Path, path: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = _strict_object(
        _load_json(path, "manifestu ratyfikacji identity-only"),
        "ratification manifest",
        _RATIFICATION_TOP_LEVEL_FIELDS,
    )
    if payload["version"] != 1:
        raise IdentityOnlyCurationError("Manifest ratyfikacji musi mieć version=1")
    if payload["operation"] != RATIFICATION_OPERATION:
        raise IdentityOnlyCurationError(
            f"Manifest ratyfikacji musi mieć operation={RATIFICATION_OPERATION}"
        )
    for field in (
        "operationId",
        "caseId",
        "candidateId",
        "priorCommandId",
        "expectedDecisionId",
        "ratifiedBy",
        "rationale",
    ):
        payload[field] = _text(payload[field], f"ratification.{field}")
    if len(payload["rationale"]) < 20:
        raise IdentityOnlyCurationError(
            "Uzasadnienie ratyfikacji musi mieć co najmniej 20 znaków"
        )
    review_reference = _strict_object(
        payload["ratificationReview"],
        "ratificationReview",
        _HUMAN_REVIEW_FIELDS,
    )
    artifact_path_text = _text(
        review_reference["artifactPath"], "ratificationReview.artifactPath"
    )
    if Path(artifact_path_text).is_absolute():
        raise IdentityOnlyCurationError(
            "ratificationReview.artifactPath musi być ścieżką względną repozytorium"
        )
    allowed_root = (root / "research/records/reassessments").resolve()
    artifact_path = (root / artifact_path_text).resolve()
    if not _is_within(artifact_path, allowed_root):
        raise IdentityOnlyCurationError(
            "Artefakt ratyfikacji musi znajdować się w research/records/reassessments"
        )
    if not artifact_path.is_file():
        raise IdentityOnlyCurationError("Nie znaleziono artefaktu ratyfikacji")
    expected_sha256 = _sha256(
        review_reference["expectedSha256"],
        "ratificationReview.expectedSha256",
    )
    actual_sha256 = sha256_file(artifact_path)
    if actual_sha256 != expected_sha256:
        raise IdentityOnlyCurationConflict(
            "Hash artefaktu ratyfikacji nie odpowiada manifestowi"
        )
    artifact = _strict_object(
        _load_json(artifact_path, "artefaktu ratyfikacji identity-only"),
        "ratification artifact",
        _RATIFICATION_ARTIFACT_FIELDS,
    )
    if artifact["version"] != 1:
        raise IdentityOnlyCurationError("Artefakt ratyfikacji musi mieć version=1")
    if artifact["artifactType"] != "strict_identity_agent_ratification":
        raise IdentityOnlyCurationError(
            "Artefakt ratyfikacji ma nieprawidłowy artifactType"
        )
    if artifact["decision"] != "ratify_agent_identity_only":
        raise IdentityOnlyCurationError(
            "Artefakt ratyfikacji musi mieć decision=ratify_agent_identity_only"
        )
    if artifact["decidedByKind"] != "agent":
        raise IdentityOnlyCurationError(
            "Artefakt ratyfikacji musi mieć decidedByKind=agent"
        )
    if artifact["identityDecisionTier"] != _AGENT_DECISION_TIER:
        raise IdentityOnlyCurationError(
            "Artefakt ratyfikacji ma nieprawidłowy identityDecisionTier"
        )
    for field in (
        "ratificationId",
        "sourceReviewId",
        "caseId",
        "candidateId",
        "priorCommandId",
        "priorDecisionId",
        "decidedBy",
        "decidedAt",
        "rationale",
    ):
        artifact[field] = _text(artifact[field], f"ratificationArtifact.{field}")
    artifact["sourceReviewSha256"] = _sha256(
        artifact["sourceReviewSha256"],
        "ratificationArtifact.sourceReviewSha256",
    )
    try:
        decided_at = datetime.fromisoformat(artifact["decidedAt"].replace("Z", "+00:00"))
    except ValueError as error:
        raise IdentityOnlyCurationError(
            "ratificationArtifact.decidedAt musi być znacznikiem ISO-8601"
        ) from error
    if decided_at.tzinfo is None:
        raise IdentityOnlyCurationError(
            "ratificationArtifact.decidedAt musi zawierać strefę czasową"
        )
    expected_matches = {
        "ratificationId": payload["operationId"],
        "caseId": payload["caseId"],
        "candidateId": payload["candidateId"],
        "priorCommandId": payload["priorCommandId"],
        "priorDecisionId": payload["expectedDecisionId"],
        "decidedBy": payload["ratifiedBy"],
        "rationale": payload["rationale"],
    }
    mismatches = [
        field
        for field, expected in expected_matches.items()
        if artifact[field] != expected
    ]
    if mismatches:
        raise IdentityOnlyCurationError(
            "Artefakt ratyfikacji nie odpowiada manifestowi: "
            + ", ".join(mismatches)
        )
    return payload, {
        "artifact": artifact,
        "artifactPath": artifact_path.relative_to(root.resolve()).as_posix(),
        "artifactResolvedPath": artifact_path,
        "artifactSha256": actual_sha256,
    }


def _prior_agent_decision_gate(
    root: Path,
    payload: dict[str, Any],
    prior_result: dict[str, Any],
) -> dict[str, Any]:
    artifact_path_text = _text(
        prior_result.get("agentReviewArtifactPath"),
        "priorResult.agentReviewArtifactPath",
    )
    if Path(artifact_path_text).is_absolute():
        raise IdentityOnlyCurationError(
            "Wcześniejszy agentReviewArtifactPath musi być ścieżką względną"
        )
    allowed_root = (root / "research/records/reassessments").resolve()
    artifact_path = (root / artifact_path_text).resolve()
    if not _is_within(artifact_path, allowed_root) or not artifact_path.is_file():
        raise IdentityOnlyCurationError(
            "Nie znaleziono wcześniejszego artefaktu decyzji agenta"
        )
    expected_sha256 = _sha256(
        prior_result.get("agentReviewSha256"), "priorResult.agentReviewSha256"
    )
    if sha256_file(artifact_path) != expected_sha256:
        raise IdentityOnlyCurationConflict(
            "Hash wcześniejszego artefaktu decyzji agenta jest niezgodny"
        )
    raw_decision = _load_json(artifact_path, "wcześniejszej decyzji agenta")
    expected_fields = (
        _AGENT_DECISION_FIELDS
        if "independentReviewRefs" in raw_decision
        else _HUMAN_DECISION_FIELDS | {"identityDecisionTier"}
    )
    decision = _strict_object(
        raw_decision, "prior agent decision artifact", expected_fields
    )
    if (
        decision["version"] != 1
        or decision["artifactType"] != "strict_identity_agent_decision"
        or decision["decision"] != "approve_same"
        or decision["decidedByKind"] != "agent"
        or decision["identityDecisionTier"] != _AGENT_DECISION_TIER
        or decision["caseId"] != payload["caseId"]
        or decision["candidateId"] != payload["candidateId"]
    ):
        raise IdentityOnlyCurationError(
            "Wcześniejszy artefakt decyzji agenta nie odpowiada ratyfikacji"
        )
    source_review_path_text = _text(
        prior_result.get("sourceReviewPath"), "priorResult.sourceReviewPath"
    )
    source_review_path = (root / source_review_path_text).resolve()
    if (
        Path(source_review_path_text).is_absolute()
        or not _is_within(source_review_path, allowed_root)
        or not source_review_path.is_file()
    ):
        raise IdentityOnlyCurationError(
            "Nie znaleziono źródłowego strict review wcześniejszej decyzji"
        )
    source_review_sha256 = _sha256(
        prior_result.get("sourceReviewSha256"), "priorResult.sourceReviewSha256"
    )
    if (
        sha256_file(source_review_path) != source_review_sha256
        or decision["sourceReviewSha256"] != source_review_sha256
    ):
        raise IdentityOnlyCurationConflict(
            "Hash strict review wcześniejszej decyzji agenta jest niezgodny"
        )
    source_review = _load_json(
        source_review_path, "strict review wcześniejszej decyzji agenta"
    )
    if (
        source_review.get("artifactType") != "strict_identity_review"
        or source_review.get("reviewId") != decision["sourceReviewId"]
        or source_review.get("caseId") != payload["caseId"]
        or source_review.get("operational") is not False
        or source_review.get("apply") != "forbidden"
    ):
        raise IdentityOnlyCurationError(
            "Źródłowy strict review wcześniejszej decyzji jest nieprawidłowy"
        )
    return {
        "artifactPath": artifact_path_text,
        "artifactResolvedPath": artifact_path,
        "artifactSha256": expected_sha256,
        "decision": decision,
        "sourceReviewPath": source_review_path_text,
        "sourceReviewSha256": source_review_sha256,
        "sourceReview": source_review,
    }


def _terminal_strict_review_by_reference(
    root: Path,
    *,
    case_id: str,
    source_review_id: str,
    source_review_sha256: str,
) -> dict[str, Any]:
    allowed_root = (root / "research/records/reassessments").resolve()
    records: list[tuple[Path, dict[str, Any], str]] = []
    for candidate_path in allowed_root.rglob("*.json"):
        resolved = candidate_path.resolve()
        try:
            candidate = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            isinstance(candidate, dict)
            and candidate.get("artifactType") == "strict_identity_review"
            and candidate.get("caseId") == case_id
        ):
            records.append((resolved, candidate, sha256_file(resolved)))
    by_id: dict[str, list[tuple[Path, dict[str, Any], str]]] = {}
    for path, review, review_sha256 in records:
        if (
            review.get("version") not in {2, 3}
            or review.get("operational") is not False
            or review.get("apply") != "forbidden"
        ):
            raise IdentityOnlyCurationError(
                "Agentowa ratyfikacja wymaga poprawnego łańcucha strict review v3"
            )
        review_id = _text(review.get("reviewId"), "strictReview.reviewId")
        by_id.setdefault(review_id, []).append((path, review, review_sha256))
    if any(len(matches) != 1 for matches in by_id.values()):
        raise IdentityOnlyCurationError(
            "Łańcuch strict review ratyfikacji ma niejednoznaczne reviewId"
        )
    record_by_id = {review_id: matches[0] for review_id, matches in by_id.items()}
    source_record = record_by_id.get(source_review_id)
    if (
        source_record is None
        or source_record[1].get("version") != 3
        or source_record[2] != source_review_sha256
    ):
        raise IdentityOnlyCurationError(
            "Nie znaleziono jednoznacznego terminalnego strict review ratyfikacji"
        )
    successors: dict[str, list[str]] = {}
    predecessors: dict[str, str | None] = {}
    for review_id, (_path, review, _sha256_value) in record_by_id.items():
        predecessor_id = review.get("supersedesReviewId")
        predecessor_sha256 = review.get("supersededArtifactSha256")
        if review.get("version") == 2 and predecessor_id is None:
            predecessors[review_id] = None
            continue
        if predecessor_id is None or predecessor_sha256 is None:
            raise IdentityOnlyCurationError(
                "Strict review v3 ratyfikacji wymaga kompletnego predecessor ref"
            )
        predecessor_id = _text(
            predecessor_id, "strictReview.supersedesReviewId"
        )
        predecessor = record_by_id.get(predecessor_id)
        if predecessor is None:
            raise IdentityOnlyCurationError(
                "Strict review ratyfikacji wskazuje brakujący predecessor"
            )
        expected_predecessor_sha256 = _sha256(
            predecessor_sha256, "strictReview.supersededArtifactSha256"
        )
        if predecessor[2] != expected_predecessor_sha256:
            raise IdentityOnlyCurationConflict(
                "Hash predecessor strict review ratyfikacji jest niezgodny"
            )
        predecessors[review_id] = predecessor_id
        successors.setdefault(predecessor_id, []).append(review_id)
    if any(len(review_ids) > 1 for review_ids in successors.values()):
        raise IdentityOnlyCurationError("Łańcuch strict review ratyfikacji zawiera fork")
    if successors.get(source_review_id):
        raise IdentityOnlyCurationError(
            "Ratyfikacja musi wskazywać terminalny strict identity review"
        )
    for review_id in predecessors:
        seen: set[str] = set()
        current = review_id
        while current in predecessors:
            if current in seen:
                raise IdentityOnlyCurationError(
                    "Łańcuch strict review ratyfikacji zawiera cykl"
                )
            seen.add(current)
            current = predecessors[current] or ""
    ancestry: list[dict[str, str]] = []
    current_id: str | None = source_review_id
    while current_id is not None:
        current_record = record_by_id[current_id]
        ancestry.append(
            {"reviewId": current_id, "sha256": current_record[2]}
        )
        current_id = predecessors.get(current_id)
    return {
        "path": source_record[0],
        "review": source_record[1],
        "sha256": source_record[2],
        "ancestry": ancestry,
    }


def _ratification_inspect(  # type: ignore[no-untyped-def]
    root: Path,
    connection,
    payload: dict[str, Any],
    ratification_gate: dict[str, Any],
) -> dict[str, Any]:
    candidate_id = payload["candidateId"]
    prior_command_id = payload["priorCommandId"]
    prior_decision_id = payload["expectedDecisionId"]
    artifact = ratification_gate["artifact"]
    blockers: list[str] = []
    prior_command_row = connection.execute(
        "SELECT * FROM curation_commands WHERE command_id=?",
        (prior_command_id,),
    ).fetchone()
    prior_command = dict(prior_command_row) if prior_command_row else None
    prior_result: dict[str, Any] | None = None
    prior_decision_gate: dict[str, Any] | None = None
    terminal_review_gate: dict[str, Any] | None = None
    if prior_command is None:
        blockers.append("missing_prior_command")
    else:
        if (
            prior_command["command_type"] != OPERATION
            or prior_command["object_type"] != "identity_candidate"
            or prior_command["object_id"] != candidate_id
        ):
            blockers.append("prior_command_scope_mismatch")
        try:
            loaded_result = json.loads(prior_command["result_json"])
        except (TypeError, json.JSONDecodeError):
            raise IdentityOnlyCurationError(
                "Wynik wcześniejszej komendy identity-only nie jest poprawnym JSON"
            )
        if not isinstance(loaded_result, dict):
            raise IdentityOnlyCurationError(
                "Wynik wcześniejszej komendy identity-only musi być obiektem"
            )
        prior_result = loaded_result
        required_prior_result = {
            "candidateId": candidate_id,
            "decisionId": prior_decision_id,
            "decision": "same",
            "status": "resolved_same",
            "decidedByKind": "agent",
            "identityDecisionTier": _AGENT_DECISION_TIER,
        }
        if any(
            prior_result.get(field) != expected
            for field, expected in required_prior_result.items()
        ):
            blockers.append("prior_command_result_mismatch")
        if not isinstance(prior_result.get("agentReviewArtifactPath"), str) or not isinstance(
            prior_result.get("agentReviewSha256"), str
        ):
            blockers.append("prior_command_missing_agent_review")
        else:
            prior_decision_gate = _prior_agent_decision_gate(
                root, payload, prior_result
            )
            if (
                prior_result.get("sourceReviewPath")
                != prior_decision_gate["sourceReviewPath"]
                or prior_result.get("sourceReviewSha256")
                != prior_decision_gate["sourceReviewSha256"]
            ):
                blockers.append("prior_command_provenance_mismatch")

    terminal_review_gate = _terminal_strict_review_by_reference(
        root,
        case_id=payload["caseId"],
        source_review_id=artifact["sourceReviewId"],
        source_review_sha256=artifact["sourceReviewSha256"],
    )
    terminal_review = terminal_review_gate["review"]
    guardrails = terminal_review.get("guardrails")
    required_approval_modes = (
        guardrails.get("requiredApprovalModes")
        if isinstance(guardrails, dict)
        else None
    )
    required_independent_roles = (
        guardrails.get("agentDecisionRequiresIndependentRoles")
        if isinstance(guardrails, dict)
        else None
    )
    if not isinstance(guardrails, dict) or (
        guardrails.get("requiredApplyMechanism") != _AGENT_APPLY_MECHANISM
        or not isinstance(required_approval_modes, list)
        or set(required_approval_modes)
        != {_HUMAN_DECISION_TIER, _AGENT_DECISION_TIER}
        or not isinstance(required_independent_roles, list)
        or set(required_independent_roles)
        != _AGENT_REVIEW_ROLES
    ):
        raise IdentityOnlyCurationError(
            "Terminalny strict review ratyfikacji nie ma pełnych agentowych guardrails"
        )
    entries = terminal_review.get("entries")
    matching_entries = (
        [
            entry
            for entry in entries
            if isinstance(entry, dict) and entry.get("candidateId") == candidate_id
        ]
        if isinstance(entries, list)
        else []
    )
    if len(matching_entries) != 1 or (
        matching_entries[0].get("readiness") != "same_profile_shell_only_ready"
        or matching_entries[0].get("recommendedHumanDecision")
        != "same_profile_shell_only"
        or matching_entries[0].get("agentDecisionEligibility")
        != _AGENT_REVIEW_VERDICT
        or matching_entries[0].get("humanDecision") != "pending"
    ):
        raise IdentityOnlyCurationError(
            "Terminalny strict review nie dopuszcza agentowej ratyfikacji kandydatury"
        )

    decision_row = connection.execute(
        "SELECT * FROM identity_decisions WHERE id=?",
        (prior_decision_id,),
    ).fetchone()
    decision = dict(decision_row) if decision_row else None
    if decision is None:
        blockers.append("missing_prior_identity_decision")
    elif (
        decision["candidate_id"] != candidate_id
        or decision["decision"] != "same"
        or decision["command_id"] != prior_command_id
        or (
            prior_result is not None
            and decision["input_fingerprint"] != prior_result.get("inputFingerprint")
        )
    ):
        blockers.append("prior_identity_decision_mismatch")

    candidate_row = _row(connection, "identity_candidates", candidate_id)
    candidate = dict(candidate_row) if candidate_row else None
    if candidate is None:
        blockers.append("missing_candidate")
    elif candidate["status"] != "resolved_same":
        blockers.append("candidate_not_resolved_same")
    candidate_decisions = _rows(
        connection,
        "SELECT * FROM identity_decisions WHERE candidate_id=? ORDER BY id",
        (candidate_id,),
    )
    if len(candidate_decisions) != 1 or (
        candidate_decisions and candidate_decisions[0]["id"] != prior_decision_id
    ):
        blockers.append("candidate_decision_set_mismatch")

    external_occurrence = None
    external_shell = None
    local_anchor_occurrence = None
    protected_dependencies: dict[str, list[dict[str, Any]]] = {}
    if prior_result is not None:
        impact = prior_result.get("impact")
        occurrence_updates = impact.get("occurrenceUpdates") if isinstance(impact, dict) else None
        shell_impact = impact.get("externalShell") if isinstance(impact, dict) else None
        identity_impact = impact.get("identityDecision") if isinstance(impact, dict) else None
        unchanged_impact = impact.get("unchanged") if isinstance(impact, dict) else None
        if (
            not isinstance(occurrence_updates, list)
            or len(occurrence_updates) != 1
            or not isinstance(occurrence_updates[0], dict)
            or not isinstance(shell_impact, dict)
            or identity_impact
            != {
                "candidateId": candidate_id,
                "decision": "same",
                "identityDecisionTier": _AGENT_DECISION_TIER,
                "candidateStatus": "resolved_same",
            }
            or unchanged_impact != _UNCHANGED_IMPACT
        ):
            blockers.append("prior_command_impact_mismatch")
        else:
            occurrence_update = occurrence_updates[0]
            occurrence_id = occurrence_update.get("occurrenceId")
            external_person_id = occurrence_update.get("fromPersonId")
            local_person_id = occurrence_update.get("toPersonId")
            if (
                not isinstance(occurrence_id, str)
                or not isinstance(external_person_id, str)
                or not isinstance(local_person_id, str)
                or shell_impact.get("personId") != external_person_id
                or shell_impact.get("toStatus") != "merged"
            ):
                blockers.append("prior_command_impact_mismatch")
            else:
                occurrence_row = _row(connection, "person_occurrences", occurrence_id)
                shell_row = _row(connection, "persons", external_person_id)
                external_occurrence = dict(occurrence_row) if occurrence_row else None
                external_shell = dict(shell_row) if shell_row else None
                if (
                    external_occurrence is None
                    or external_occurrence["person_id"] != local_person_id
                ):
                    blockers.append("current_occurrence_assignment_mismatch")
                if external_shell is None or external_shell["status"] != "merged":
                    blockers.append("current_external_shell_status_mismatch")
                if candidate is not None:
                    candidate_endpoints = {
                        candidate["occurrence_a_id"], candidate["occurrence_b_id"]
                    }
                    if (
                        len(candidate_endpoints) != 2
                        or occurrence_id not in candidate_endpoints
                    ):
                        blockers.append("candidate_endpoints_mismatch")
                    else:
                        local_anchor_id = next(
                            endpoint
                            for endpoint in candidate_endpoints
                            if endpoint != occurrence_id
                        )
                        local_anchor_row = _row(
                            connection, "person_occurrences", local_anchor_id
                        )
                        local_anchor_occurrence = (
                            dict(local_anchor_row) if local_anchor_row else None
                        )
                        if (
                            local_anchor_occurrence is None
                            or local_anchor_occurrence["person_id"] != local_person_id
                        ):
                            blockers.append("current_local_anchor_mismatch")
                external_person_occurrences = _rows(
                    connection,
                    "SELECT * FROM person_occurrences WHERE person_id=? ORDER BY id",
                    (external_person_id,),
                )
                if external_person_occurrences:
                    blockers.append("external_shell_not_empty")
                external_memberships = _rows(
                    connection,
                    """SELECT * FROM household_memberships
                         WHERE person_id=? OR occurrence_id=? ORDER BY id""",
                    (external_person_id, occurrence_id),
                )
                if external_memberships:
                    blockers.append("external_shell_has_memberships")
                protected_dependencies = _protected_dependencies(
                    connection,
                    candidate_id=candidate_id,
                    person_id=external_person_id,
                    occurrence_id=occurrence_id,
                )
                if protected_dependencies:
                    blockers.append("external_shell_has_protected_dependencies")

    independent_review_refs: list[dict[str, Any]] = []
    forbidden_review_paths = {
        ratification_gate["artifactResolvedPath"],
        terminal_review_gate["path"],
    }
    if prior_decision_gate is not None:
        forbidden_review_paths.add(prior_decision_gate["artifactResolvedPath"])
    strict_review_refs = _independent_review_refs(
        root,
        terminal_review.get("independentReviewRefs"),
        case_id=payload["caseId"],
        candidate_id=candidate_id,
        label="strictReview.independentReviewRefs",
        forbidden_paths=forbidden_review_paths,
    )
    independent_review_refs = _independent_review_refs(
        root,
        artifact["independentReviewRefs"],
        case_id=payload["caseId"],
        candidate_id=candidate_id,
        label="ratificationArtifact.independentReviewRefs",
        forbidden_paths=forbidden_review_paths,
    )
    if independent_review_refs != strict_review_refs:
        blockers.append("ratification_independent_review_refs_mismatch")

    existing_ratifications = _rows(
        connection,
        """SELECT * FROM curation_commands
             WHERE command_type=? AND object_type='identity_candidate' AND object_id=?
             ORDER BY command_id""",
        (RATIFICATION_OPERATION, candidate_id),
    )
    if existing_ratifications:
        blockers.append("candidate_already_ratified")
    case_row = _row(connection, "research_cases", payload["caseId"])
    if case_row is None:
        blockers.append("missing_case")

    state = {
        "priorCommand": prior_command,
        "priorResult": prior_result,
        "priorDecision": decision,
        "candidate": candidate,
        "candidateDecisions": candidate_decisions,
        "externalOccurrence": external_occurrence,
        "externalShell": external_shell,
        "localAnchorOccurrence": local_anchor_occurrence,
        "protectedDependencies": protected_dependencies,
        "ratificationArtifactPath": ratification_gate["artifactPath"],
        "ratificationArtifactSha256": ratification_gate["artifactSha256"],
        "sourceReviewPath": terminal_review_gate["path"].relative_to(
            root.resolve()
        ).as_posix(),
        "sourceReviewSha256": terminal_review_gate["sha256"],
        "independentReviewRefs": independent_review_refs,
        "existingRatifications": existing_ratifications,
    }
    return {
        "operation": RATIFICATION_OPERATION,
        "operationId": payload["operationId"],
        "caseId": payload["caseId"],
        "candidateId": candidate_id,
        "priorCommandId": prior_command_id,
        "priorDecisionId": prior_decision_id,
        "identityDecisionTier": _AGENT_DECISION_TIER,
        "writeSet": [],
        "fingerprint": fingerprint(state),
        "commandIdRequired": True,
        "applyAllowed": not blockers,
        "blockers": blockers,
        "agentReviewArtifactPath": (
            prior_result.get("agentReviewArtifactPath") if prior_result else None
        ),
        "agentReviewSha256": (
            prior_result.get("agentReviewSha256") if prior_result else None
        ),
        "sourceReviewPath": (
            terminal_review_gate["path"].relative_to(root.resolve()).as_posix()
        ),
        "sourceReviewSha256": terminal_review_gate["sha256"],
        "independentReviewRefs": independent_review_refs,
    }


def identity_only_agent_ratification_preview(
    root: Path, manifest: Path
) -> dict[str, Any]:
    """Validate a zero-domain-write ratification of an existing agent decision."""

    payload, ratification_gate = _load_ratification_manifest(root, manifest)
    with _current_connection(root, readonly=True) as connection:
        require_tables(connection, REQUIRED_TABLES)
        return _ratification_inspect(root, connection, payload, ratification_gate)


def apply_identity_only_agent_ratification(
    root: Path,
    manifest: Path,
    *,
    expected_fingerprint: str,
    command_id: str,
) -> dict[str, Any]:
    """Record an append-only ratification without modifying identity domain rows."""

    payload, ratification_gate = _load_ratification_manifest(root, manifest)
    expected_fingerprint = _text(expected_fingerprint, "expectedFingerprint")
    command_id = _text(command_id, "commandId")
    manifest_fingerprint = fingerprint(payload)
    request_fingerprint = fingerprint(
        {
            "manifestFingerprint": manifest_fingerprint,
            "expectedFingerprint": expected_fingerprint,
        }
    )
    with _current_connection(root, readonly=False) as connection:
        require_tables(connection, REQUIRED_TABLES)
        connection.execute("BEGIN IMMEDIATE")
        try:
            replay = replay_command(
                connection,
                command_id=command_id,
                command_type=RATIFICATION_OPERATION,
                object_type="identity_candidate",
                object_id=payload["candidateId"],
                request_fingerprint=request_fingerprint,
            )
        except CommandEnvelopeConflict as error:
            raise IdentityOnlyCurationConflict(str(error)) from error
        if replay is not None:
            return replay

        preview = _ratification_inspect(
            root, connection, payload, ratification_gate
        )
        if preview["fingerprint"] != expected_fingerprint:
            raise IdentityOnlyCurationConflict(
                "Stan ratyfikacji zmienił się po preview; odśwież fingerprint"
            )
        if not preview["applyAllowed"]:
            raise IdentityOnlyCurationError(
                "Ratyfikacja identity-only jest zablokowana: "
                + ", ".join(preview["blockers"])
            )

        now = utc_now()
        result = {
            "operation": RATIFICATION_OPERATION,
            "status": "ratified_existing_agent_same",
            "candidateId": payload["candidateId"],
            "priorCommandId": payload["priorCommandId"],
            "priorDecisionId": payload["expectedDecisionId"],
            "identityDecisionTier": _AGENT_DECISION_TIER,
            "writeSet": [],
            "ratificationId": payload["operationId"],
            "ratificationReviewArtifactPath": ratification_gate["artifactPath"],
            "ratificationReviewSha256": ratification_gate["artifactSha256"],
            "agentReviewArtifactPath": preview["agentReviewArtifactPath"],
            "agentReviewSha256": preview["agentReviewSha256"],
            "sourceReviewPath": preview["sourceReviewPath"],
            "sourceReviewSha256": preview["sourceReviewSha256"],
            "independentReviewRefs": preview["independentReviewRefs"],
            "projectionStatus": "not_required",
        }
        record_command(
            connection,
            command_id=command_id,
            command_type=RATIFICATION_OPERATION,
            object_type="identity_candidate",
            object_id=payload["candidateId"],
            input_fingerprint=expected_fingerprint,
            request_fingerprint=request_fingerprint,
            actor=payload["ratifiedBy"],
            rationale=payload["rationale"],
            result=result,
            projection_status="not_required",
        )
        connection.execute(
            """INSERT INTO audit_log(
                   occurred_at,actor,action,object_type,object_id,
                   before_json,after_json,rationale
               ) VALUES (?,?,?,?,?,?,?,?)""",
            (
                now,
                payload["ratifiedBy"],
                RATIFICATION_OPERATION,
                "identity_candidate",
                payload["candidateId"],
                json.dumps(
                    {
                        "priorCommandId": payload["priorCommandId"],
                        "priorDecisionId": payload["expectedDecisionId"],
                        "identityDecisionTier": _AGENT_DECISION_TIER,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                json.dumps(result, ensure_ascii=False, sort_keys=True),
                payload["rationale"],
            ),
        )
        return {**result, "commandId": command_id, "alreadyApplied": False}


def _load_human_confirmation_manifest(
    root: Path, path: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    raw_payload = _load_json(path, "manifestu potwierdzenia identity-only")
    manifest_version = raw_payload.get("version")
    manifest_fields = (
        _HUMAN_CONFIRMATION_TOP_LEVEL_FIELDS
        if manifest_version == 1
        else _SUPERSEDING_HUMAN_CONFIRMATION_TOP_LEVEL_FIELDS
    )
    payload = _strict_object(
        raw_payload,
        "human confirmation manifest",
        manifest_fields,
    )
    if payload["version"] not in {1, 2}:
        raise IdentityOnlyCurationError(
            "Manifest potwierdzenia musi mieć version=1 albo version=2"
        )
    if payload["operation"] != HUMAN_CONFIRMATION_OPERATION:
        raise IdentityOnlyCurationError(
            "Manifest potwierdzenia musi mieć "
            f"operation={HUMAN_CONFIRMATION_OPERATION}"
        )
    for field in (
        "operationId",
        "caseId",
        "candidateId",
        "priorAgentCommandId",
        "priorAgentRatificationCommandId",
        "expectedDecisionId",
        "confirmedBy",
        "rationale",
    ):
        payload[field] = _text(payload[field], f"humanConfirmation.{field}")
    if payload["version"] == 2:
        payload["supersedesHumanConfirmationCommandId"] = _text(
            payload["supersedesHumanConfirmationCommandId"],
            "humanConfirmation.supersedesHumanConfirmationCommandId",
        )
    if len(payload["rationale"]) < 20:
        raise IdentityOnlyCurationError(
            "Uzasadnienie potwierdzenia musi mieć co najmniej 20 znaków"
        )
    review_reference = _strict_object(
        payload["humanConfirmation"],
        "humanConfirmation",
        _HUMAN_REVIEW_FIELDS,
    )
    artifact_path_text = _text(
        review_reference["artifactPath"], "humanConfirmation.artifactPath"
    )
    if Path(artifact_path_text).is_absolute():
        raise IdentityOnlyCurationError(
            "humanConfirmation.artifactPath musi być ścieżką względną repozytorium"
        )
    allowed_root = (root / "research/records/reassessments").resolve()
    artifact_path = (root / artifact_path_text).resolve()
    if not _is_within(artifact_path, allowed_root):
        raise IdentityOnlyCurationError(
            "Artefakt potwierdzenia musi znajdować się "
            "w research/records/reassessments"
        )
    if not artifact_path.is_file():
        raise IdentityOnlyCurationError("Nie znaleziono artefaktu potwierdzenia")
    expected_sha256 = _sha256(
        review_reference["expectedSha256"],
        "humanConfirmation.expectedSha256",
    )
    actual_sha256 = sha256_file(artifact_path)
    if actual_sha256 != expected_sha256:
        raise IdentityOnlyCurationConflict(
            "Hash artefaktu potwierdzenia nie odpowiada manifestowi"
        )
    raw_artifact = _load_json(
        artifact_path, "artefaktu potwierdzenia identity-only"
    )
    artifact_fields = (
        _HUMAN_CONFIRMATION_ARTIFACT_FIELDS
        if payload["version"] == 1
        else _SUPERSEDING_HUMAN_CONFIRMATION_ARTIFACT_FIELDS
    )
    artifact = _strict_object(
        raw_artifact,
        "human confirmation artifact",
        artifact_fields,
    )
    if artifact["version"] != payload["version"]:
        raise IdentityOnlyCurationError(
            "Wersja artefaktu potwierdzenia musi odpowiadać wersji manifestu"
        )
    if artifact["artifactType"] != "strict_identity_human_confirmation":
        raise IdentityOnlyCurationError(
            "Artefakt potwierdzenia ma nieprawidłowy artifactType"
        )
    if artifact["decision"] != "confirm_agent_identity_only":
        raise IdentityOnlyCurationError(
            "Artefakt potwierdzenia musi mieć decision=confirm_agent_identity_only"
        )
    if artifact["decidedByKind"] != "human":
        raise IdentityOnlyCurationError(
            "Artefakt potwierdzenia musi mieć decidedByKind=human"
        )
    if artifact["identityDecisionTier"] != _HUMAN_DECISION_TIER:
        raise IdentityOnlyCurationError(
            "Artefakt potwierdzenia musi mieć identityDecisionTier=human_confirmed"
        )
    for field in (
        "confirmationId",
        "sourceReviewId",
        "caseId",
        "candidateId",
        "priorAgentCommandId",
        "priorAgentRatificationCommandId",
        "priorDecisionId",
        "decidedBy",
        "decidedAt",
        "rationale",
    ):
        artifact[field] = _text(artifact[field], f"humanConfirmationArtifact.{field}")
    for field in (
        "sourceReviewSha256",
        "priorAgentCommandResultSha256",
        "priorAgentRatificationResultSha256",
    ):
        artifact[field] = _sha256(
            artifact[field], f"humanConfirmationArtifact.{field}"
        )
    if payload["version"] == 2:
        artifact["supersedesHumanConfirmationCommandId"] = _text(
            artifact["supersedesHumanConfirmationCommandId"],
            "humanConfirmationArtifact.supersedesHumanConfirmationCommandId",
        )
        artifact["supersededHumanConfirmationResultSha256"] = _sha256(
            artifact["supersededHumanConfirmationResultSha256"],
            "humanConfirmationArtifact.supersededHumanConfirmationResultSha256",
        )
    try:
        decided_at = datetime.fromisoformat(artifact["decidedAt"].replace("Z", "+00:00"))
    except ValueError as error:
        raise IdentityOnlyCurationError(
            "humanConfirmationArtifact.decidedAt musi być znacznikiem ISO-8601"
        ) from error
    if decided_at.tzinfo is None:
        raise IdentityOnlyCurationError(
            "humanConfirmationArtifact.decidedAt musi zawierać strefę czasową"
        )
    expected_matches = {
        "confirmationId": payload["operationId"],
        "caseId": payload["caseId"],
        "candidateId": payload["candidateId"],
        "priorAgentCommandId": payload["priorAgentCommandId"],
        "priorAgentRatificationCommandId": payload[
            "priorAgentRatificationCommandId"
        ],
        "priorDecisionId": payload["expectedDecisionId"],
        "decidedBy": payload["confirmedBy"],
        "rationale": payload["rationale"],
    }
    if payload["version"] == 2:
        expected_matches["supersedesHumanConfirmationCommandId"] = payload[
            "supersedesHumanConfirmationCommandId"
        ]
    mismatches = [
        field
        for field, expected in expected_matches.items()
        if artifact[field] != expected
    ]
    if mismatches:
        raise IdentityOnlyCurationError(
            "Artefakt potwierdzenia nie odpowiada manifestowi: "
            + ", ".join(mismatches)
        )
    return payload, {
        "artifact": artifact,
        "artifactPath": artifact_path.relative_to(root.resolve()).as_posix(),
        "artifactSha256": actual_sha256,
    }


def _command_result(row: Any, label: str) -> dict[str, Any]:
    try:
        result = json.loads(row["result_json"])
    except (TypeError, json.JSONDecodeError) as error:
        raise IdentityOnlyCurationError(f"{label} nie zawiera poprawnego result_json") from error
    if not isinstance(result, dict):
        raise IdentityOnlyCurationError(f"{label} result_json musi być obiektem")
    return result


def _prior_agent_ratification_gate(
    root: Path, payload: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any]:
    path_text = _text(
        result.get("ratificationReviewArtifactPath"),
        "priorRatification.ratificationReviewArtifactPath",
    )
    allowed_root = (root / "research/records/reassessments").resolve()
    artifact_path = (root / path_text).resolve()
    if (
        Path(path_text).is_absolute()
        or not _is_within(artifact_path, allowed_root)
        or not artifact_path.is_file()
    ):
        raise IdentityOnlyCurationError(
            "Nie znaleziono wcześniejszego artefaktu ratyfikacji agenta"
        )
    expected_sha256 = _sha256(
        result.get("ratificationReviewSha256"),
        "priorRatification.ratificationReviewSha256",
    )
    if sha256_file(artifact_path) != expected_sha256:
        raise IdentityOnlyCurationConflict(
            "Hash wcześniejszego artefaktu ratyfikacji agenta jest niezgodny"
        )
    artifact = _strict_object(
        _load_json(artifact_path, "wcześniejszej ratyfikacji agenta"),
        "prior agent ratification artifact",
        _RATIFICATION_ARTIFACT_FIELDS,
    )
    if (
        artifact["version"] != 1
        or artifact["artifactType"] != "strict_identity_agent_ratification"
        or artifact["decision"] != "ratify_agent_identity_only"
        or artifact["decidedByKind"] != "agent"
        or artifact["identityDecisionTier"] != _AGENT_DECISION_TIER
        or artifact["caseId"] != payload["caseId"]
        or artifact["candidateId"] != payload["candidateId"]
        or artifact["priorCommandId"] != payload["priorAgentCommandId"]
        or artifact["priorDecisionId"] != payload["expectedDecisionId"]
        or artifact["ratificationId"] != result.get("ratificationId")
    ):
        raise IdentityOnlyCurationError(
            "Wcześniejszy artefakt ratyfikacji agenta nie odpowiada łańcuchowi"
        )
    artifact_source_sha256 = _sha256(
        artifact["sourceReviewSha256"],
        "priorRatificationArtifact.sourceReviewSha256",
    )
    if (
        artifact_source_sha256 != result.get("sourceReviewSha256")
        or artifact.get("independentReviewRefs")
        != result.get("independentReviewRefs")
    ):
        raise IdentityOnlyCurationError(
            "Wcześniejsza ratyfikacja agenta ma niespójne provenance"
        )
    source_review_path_text = _text(
        result.get("sourceReviewPath"), "priorRatification.sourceReviewPath"
    )
    source_review_path = (root / source_review_path_text).resolve()
    if (
        Path(source_review_path_text).is_absolute()
        or not _is_within(source_review_path, allowed_root)
        or not source_review_path.is_file()
        or sha256_file(source_review_path) != artifact_source_sha256
    ):
        raise IdentityOnlyCurationConflict(
            "Strict review wcześniejszej ratyfikacji agenta jest niezgodny"
        )
    source_review = _load_json(
        source_review_path, "strict review wcześniejszej ratyfikacji agenta"
    )
    if (
        source_review.get("artifactType") != "strict_identity_review"
        or source_review.get("reviewId") != artifact.get("sourceReviewId")
        or source_review.get("caseId") != payload["caseId"]
        or source_review.get("operational") is not False
        or source_review.get("apply") != "forbidden"
    ):
        raise IdentityOnlyCurationError(
            "Strict review wcześniejszej ratyfikacji agenta jest nieprawidłowy"
        )
    return {
        "artifactPath": path_text,
        "artifactSha256": expected_sha256,
        "artifact": artifact,
        "sourceReviewPath": source_review_path_text,
        "sourceReviewSha256": artifact_source_sha256,
        "sourceReview": source_review,
    }


def _strict_review_candidate_confirmation_gate(
    review: dict[str, Any], *, candidate_id: str
) -> None:
    guardrails = review.get("guardrails")
    if (
        not isinstance(guardrails, dict)
        or not isinstance(guardrails.get("requiredApprovalModes"), list)
        or _HUMAN_DECISION_TIER
        not in guardrails["requiredApprovalModes"]
    ):
        raise IdentityOnlyCurationError(
            "Terminalny strict review nie dopuszcza trybu human_confirmed"
        )
    entries = review.get("entries")
    if not isinstance(entries, list):
        raise IdentityOnlyCurationError(
            "Terminalny strict review potwierdzenia nie zawiera listy entries"
        )
    matching_entries = [
        entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("candidateId") == candidate_id
    ]
    if len(matching_entries) != 1:
        raise IdentityOnlyCurationError(
            "Terminalny strict review potwierdzenia nie wskazuje jednoznacznie kandydatury"
        )
    entry = matching_entries[0]
    if (
        entry.get("readiness") != "same_profile_shell_only_ready"
        or entry.get("recommendedHumanDecision")
        != "same_profile_shell_only"
        or entry.get("humanDecision") != "pending"
    ):
        raise IdentityOnlyCurationError(
            "Terminalny strict review nie dopuszcza human confirmation identity-only"
        )


def _prior_human_confirmation_gate(
    root: Path,
    payload: dict[str, Any],
    row: Any,
    result: dict[str, Any],
) -> dict[str, Any]:
    candidate_id = payload["candidateId"]
    merge_command_id = payload["priorAgentCommandId"]
    ratification_command_id = payload["priorAgentRatificationCommandId"]
    decision_id = payload["expectedDecisionId"]
    if (
        row["command_type"] != HUMAN_CONFIRMATION_OPERATION
        or row["object_type"] != "identity_candidate"
        or row["object_id"] != candidate_id
        or row["projection_status"] != "not_required"
        or result.get("operation") != HUMAN_CONFIRMATION_OPERATION
        or result.get("status") != "human_confirmed_existing_agent_same"
        or result.get("candidateId") != candidate_id
        or result.get("priorAgentCommandId") != merge_command_id
        or result.get("priorAgentRatificationCommandId")
        != ratification_command_id
        or result.get("priorDecisionId") != decision_id
        or result.get("identityDecisionTier") != _HUMAN_DECISION_TIER
        or result.get("writeSet") != []
        or result.get("projectionStatus") != "not_required"
    ):
        raise IdentityOnlyCurationError(
            "Supersedowane human confirmation nie odpowiada bieżącemu łańcuchowi"
        )

    artifact_path_text = _text(
        result.get("humanConfirmationArtifactPath"),
        "priorHumanConfirmation.humanConfirmationArtifactPath",
    )
    allowed_root = (root / "research/records/reassessments").resolve()
    artifact_path = (root / artifact_path_text).resolve()
    if (
        Path(artifact_path_text).is_absolute()
        or not _is_within(artifact_path, allowed_root)
        or not artifact_path.is_file()
    ):
        raise IdentityOnlyCurationError(
            "Nie znaleziono supersedowanego artefaktu human confirmation"
        )
    artifact_sha256 = _sha256(
        result.get("humanConfirmationSha256"),
        "priorHumanConfirmation.humanConfirmationSha256",
    )
    if sha256_file(artifact_path) != artifact_sha256:
        raise IdentityOnlyCurationConflict(
            "Hash supersedowanego artefaktu human confirmation jest niezgodny"
        )
    raw_artifact = _load_json(
        artifact_path, "supersedowanego artefaktu human confirmation"
    )
    prior_artifact_fields = (
        _HUMAN_CONFIRMATION_ARTIFACT_FIELDS
        if raw_artifact.get("version") == 1
        else _SUPERSEDING_HUMAN_CONFIRMATION_ARTIFACT_FIELDS
    )
    artifact = _strict_object(
        raw_artifact,
        "prior human confirmation artifact",
        prior_artifact_fields,
    )
    if (
        artifact.get("version") not in {1, 2}
        or artifact.get("artifactType")
        != "strict_identity_human_confirmation"
        or artifact.get("decision") != "confirm_agent_identity_only"
        or artifact.get("decidedByKind") != "human"
        or artifact.get("identityDecisionTier") != _HUMAN_DECISION_TIER
        or artifact.get("caseId") != payload["caseId"]
        or artifact.get("candidateId") != candidate_id
        or artifact.get("priorAgentCommandId") != merge_command_id
        or artifact.get("priorAgentRatificationCommandId")
        != ratification_command_id
        or artifact.get("priorDecisionId") != decision_id
        or artifact.get("confirmationId") != result.get("confirmationId")
    ):
        raise IdentityOnlyCurationError(
            "Supersedowany artefakt human confirmation nie odpowiada bieżącemu łańcuchowi"
        )

    source_review_path_text = _text(
        result.get("sourceReviewPath"),
        "priorHumanConfirmation.sourceReviewPath",
    )
    source_review_path = (root / source_review_path_text).resolve()
    source_review_sha256 = _sha256(
        result.get("sourceReviewSha256"),
        "priorHumanConfirmation.sourceReviewSha256",
    )
    if (
        Path(source_review_path_text).is_absolute()
        or not _is_within(source_review_path, allowed_root)
        or not source_review_path.is_file()
        or sha256_file(source_review_path) != source_review_sha256
        or artifact.get("sourceReviewSha256") != source_review_sha256
    ):
        raise IdentityOnlyCurationConflict(
            "Provenance strict review supersedowanego confirmation jest niezgodne"
        )
    source_review = _load_json(
        source_review_path, "strict review supersedowanego human confirmation"
    )
    if (
        source_review.get("artifactType") != "strict_identity_review"
        or source_review.get("reviewId") != artifact.get("sourceReviewId")
        or source_review.get("caseId") != payload["caseId"]
        or source_review.get("operational") is not False
        or source_review.get("apply") != "forbidden"
    ):
        raise IdentityOnlyCurationError(
            "Strict review supersedowanego confirmation jest nieprawidłowy"
        )
    return {
        "artifact": artifact,
        "artifactPath": artifact_path_text,
        "artifactSha256": artifact_sha256,
        "sourceReviewPath": source_review_path_text,
        "sourceReviewSha256": source_review_sha256,
        "sourceReviewId": artifact["sourceReviewId"],
    }


def _human_confirmation_inspect(  # type: ignore[no-untyped-def]
    root: Path,
    connection,
    payload: dict[str, Any],
    confirmation_gate: dict[str, Any],
) -> dict[str, Any]:
    candidate_id = payload["candidateId"]
    merge_command_id = payload["priorAgentCommandId"]
    ratification_command_id = payload["priorAgentRatificationCommandId"]
    decision_id = payload["expectedDecisionId"]
    artifact = confirmation_gate["artifact"]
    superseding_confirmation = payload["version"] == 2
    blockers: list[str] = []

    merge_row = connection.execute(
        "SELECT * FROM curation_commands WHERE command_id=?", (merge_command_id,)
    ).fetchone()
    ratification_row = connection.execute(
        "SELECT * FROM curation_commands WHERE command_id=?",
        (ratification_command_id,),
    ).fetchone()
    merge_result: dict[str, Any] | None = None
    ratification_result: dict[str, Any] | None = None
    prior_decision_gate: dict[str, Any] | None = None
    prior_ratification_gate: dict[str, Any] | None = None
    if merge_row is None:
        blockers.append("missing_prior_agent_command")
    else:
        merge_result = _command_result(merge_row, "Komenda scalenia agenta")
        if (
            merge_row["command_type"] != OPERATION
            or merge_row["object_type"] != "identity_candidate"
            or merge_row["object_id"] != candidate_id
            or merge_result.get("candidateId") != candidate_id
            or merge_result.get("decisionId") != decision_id
            or merge_result.get("decision") != "same"
            or merge_result.get("status") != "resolved_same"
            or merge_result.get("decidedByKind") != "agent"
            or merge_result.get("identityDecisionTier") != _AGENT_DECISION_TIER
        ):
            blockers.append("prior_agent_command_mismatch")
        if fingerprint(merge_result) != artifact["priorAgentCommandResultSha256"]:
            blockers.append("prior_agent_command_result_hash_mismatch")
        if not isinstance(merge_result.get("agentReviewArtifactPath"), str):
            blockers.append("prior_agent_command_missing_agent_review")
        else:
            prior_decision_gate = _prior_agent_decision_gate(
                root, payload, merge_result
            )

    if ratification_row is None:
        blockers.append("missing_prior_agent_ratification")
    else:
        ratification_result = _command_result(
            ratification_row, "Komenda ratyfikacji agenta"
        )
        if (
            ratification_row["command_type"] != RATIFICATION_OPERATION
            or ratification_row["object_type"] != "identity_candidate"
            or ratification_row["object_id"] != candidate_id
            or ratification_row["projection_status"] != "not_required"
            or ratification_result.get("operation") != RATIFICATION_OPERATION
            or ratification_result.get("status") != "ratified_existing_agent_same"
            or ratification_result.get("candidateId") != candidate_id
            or ratification_result.get("priorCommandId") != merge_command_id
            or ratification_result.get("priorDecisionId") != decision_id
            or ratification_result.get("identityDecisionTier") != _AGENT_DECISION_TIER
            or ratification_result.get("writeSet") != []
            or ratification_result.get("projectionStatus") != "not_required"
        ):
            blockers.append("prior_agent_ratification_mismatch")
        if (
            fingerprint(ratification_result)
            != artifact["priorAgentRatificationResultSha256"]
        ):
            blockers.append("prior_agent_ratification_result_hash_mismatch")
        prior_ratification_gate = _prior_agent_ratification_gate(
            root, payload, ratification_result
        )

    terminal_review_gate = _terminal_strict_review_by_reference(
        root,
        case_id=payload["caseId"],
        source_review_id=artifact["sourceReviewId"],
        source_review_sha256=artifact["sourceReviewSha256"],
    )
    if superseding_confirmation:
        _strict_review_candidate_confirmation_gate(
            terminal_review_gate["review"], candidate_id=candidate_id
        )
    if prior_decision_gate is not None and {
        "reviewId": prior_decision_gate["decision"]["sourceReviewId"],
        "sha256": prior_decision_gate["sourceReviewSha256"],
    } not in terminal_review_gate["ancestry"]:
        blockers.append("ratification_review_does_not_supersede_agent_review")
    if superseding_confirmation:
        if prior_ratification_gate is not None and {
            "reviewId": prior_ratification_gate["artifact"].get("sourceReviewId"),
            "sha256": prior_ratification_gate["artifact"].get(
                "sourceReviewSha256"
            ),
        } not in terminal_review_gate["ancestry"]:
            blockers.append("ratification_review_not_in_terminal_ancestry")
    else:
        if ratification_result is not None and (
            ratification_result.get("sourceReviewSha256")
            != artifact["sourceReviewSha256"]
            or ratification_result.get("sourceReviewPath")
            != terminal_review_gate["path"].relative_to(root.resolve()).as_posix()
        ):
            blockers.append("ratification_terminal_review_mismatch")
        if prior_ratification_gate is not None and (
            prior_ratification_gate["artifact"].get("sourceReviewId")
            != artifact["sourceReviewId"]
            or prior_ratification_gate["artifact"].get("sourceReviewSha256")
            != artifact["sourceReviewSha256"]
        ):
            blockers.append("ratification_artifact_terminal_review_mismatch")

    decision_row = connection.execute(
        "SELECT * FROM identity_decisions WHERE id=?", (decision_id,)
    ).fetchone()
    decision = dict(decision_row) if decision_row else None
    candidate_row = _row(connection, "identity_candidates", candidate_id)
    candidate = dict(candidate_row) if candidate_row else None
    candidate_decisions = _rows(
        connection,
        "SELECT * FROM identity_decisions WHERE candidate_id=? ORDER BY id",
        (candidate_id,),
    )
    if decision is None:
        blockers.append("missing_prior_identity_decision")
    elif (
        decision["candidate_id"] != candidate_id
        or decision["decision"] != "same"
        or decision["command_id"] != merge_command_id
        or (
            merge_result is not None
            and decision["input_fingerprint"] != merge_result.get("inputFingerprint")
        )
    ):
        blockers.append("prior_identity_decision_mismatch")
    if candidate is None:
        blockers.append("missing_candidate")
    elif candidate["status"] != "resolved_same":
        blockers.append("candidate_not_resolved_same")
    if len(candidate_decisions) != 1 or (
        candidate_decisions and candidate_decisions[0]["id"] != decision_id
    ):
        blockers.append("candidate_decision_set_mismatch")

    external_occurrence = None
    external_shell = None
    local_anchor_occurrence = None
    protected_dependencies: dict[str, list[dict[str, Any]]] = {}
    if merge_result is not None:
        impact = merge_result.get("impact")
        occurrence_updates = (
            impact.get("occurrenceUpdates") if isinstance(impact, dict) else None
        )
        shell_impact = impact.get("externalShell") if isinstance(impact, dict) else None
        identity_impact = (
            impact.get("identityDecision") if isinstance(impact, dict) else None
        )
        unchanged_impact = impact.get("unchanged") if isinstance(impact, dict) else None
        if (
            not isinstance(occurrence_updates, list)
            or len(occurrence_updates) != 1
            or not isinstance(occurrence_updates[0], dict)
            or not isinstance(shell_impact, dict)
            or identity_impact
            != {
                "candidateId": candidate_id,
                "decision": "same",
                "identityDecisionTier": _AGENT_DECISION_TIER,
                "candidateStatus": "resolved_same",
            }
            or unchanged_impact != _UNCHANGED_IMPACT
        ):
            blockers.append("prior_agent_command_impact_mismatch")
        else:
            occurrence_update = occurrence_updates[0]
            occurrence_id = occurrence_update.get("occurrenceId")
            external_person_id = occurrence_update.get("fromPersonId")
            local_person_id = occurrence_update.get("toPersonId")
            if (
                not isinstance(occurrence_id, str)
                or not isinstance(external_person_id, str)
                or not isinstance(local_person_id, str)
                or shell_impact.get("personId") != external_person_id
                or shell_impact.get("toStatus") != "merged"
            ):
                blockers.append("prior_agent_command_impact_mismatch")
            else:
                occurrence_row = _row(connection, "person_occurrences", occurrence_id)
                shell_row = _row(connection, "persons", external_person_id)
                external_occurrence = dict(occurrence_row) if occurrence_row else None
                external_shell = dict(shell_row) if shell_row else None
                if (
                    external_occurrence is None
                    or external_occurrence["person_id"] != local_person_id
                ):
                    blockers.append("current_occurrence_assignment_mismatch")
                if external_shell is None or external_shell["status"] != "merged":
                    blockers.append("current_external_shell_status_mismatch")
                if candidate is not None:
                    candidate_endpoints = {
                        candidate["occurrence_a_id"], candidate["occurrence_b_id"]
                    }
                    if len(candidate_endpoints) != 2 or occurrence_id not in candidate_endpoints:
                        blockers.append("candidate_endpoints_mismatch")
                    else:
                        local_anchor_id = next(
                            endpoint
                            for endpoint in candidate_endpoints
                            if endpoint != occurrence_id
                        )
                        local_anchor_row = _row(
                            connection, "person_occurrences", local_anchor_id
                        )
                        local_anchor_occurrence = (
                            dict(local_anchor_row) if local_anchor_row else None
                        )
                        if (
                            local_anchor_occurrence is None
                            or local_anchor_occurrence["person_id"] != local_person_id
                        ):
                            blockers.append("current_local_anchor_mismatch")
                if _rows(
                    connection,
                    "SELECT * FROM person_occurrences WHERE person_id=? ORDER BY id",
                    (external_person_id,),
                ):
                    blockers.append("external_shell_not_empty")
                if _rows(
                    connection,
                    """SELECT * FROM household_memberships
                         WHERE person_id=? OR occurrence_id=? ORDER BY id""",
                    (external_person_id, occurrence_id),
                ):
                    blockers.append("external_shell_has_memberships")
                protected_dependencies = _protected_dependencies(
                    connection,
                    candidate_id=candidate_id,
                    person_id=external_person_id,
                    occurrence_id=occurrence_id,
                )
                if protected_dependencies:
                    blockers.append("external_shell_has_protected_dependencies")

    existing_confirmations = _rows(
        connection,
        """SELECT * FROM curation_commands
             WHERE command_type=? AND object_type='identity_candidate' AND object_id=?
             ORDER BY command_id""",
        (HUMAN_CONFIRMATION_OPERATION, candidate_id),
    )
    superseded_confirmation_result: dict[str, Any] | None = None
    superseded_confirmation_gate: dict[str, Any] | None = None
    if not superseding_confirmation:
        if existing_confirmations:
            blockers.append("candidate_already_human_confirmed")
    else:
        superseded_command_id = payload[
            "supersedesHumanConfirmationCommandId"
        ]
        superseded_row = next(
            (
                row
                for row in existing_confirmations
                if row["command_id"] == superseded_command_id
            ),
            None,
        )
        if superseded_row is None:
            blockers.append("missing_superseded_human_confirmation")
        else:
            superseded_confirmation_result = _command_result(
                superseded_row, "Supersedowane human confirmation"
            )
            if (
                fingerprint(superseded_confirmation_result)
                != artifact["supersededHumanConfirmationResultSha256"]
            ):
                blockers.append("superseded_human_confirmation_result_hash_mismatch")
            superseded_confirmation_gate = _prior_human_confirmation_gate(
                root,
                payload,
                superseded_row,
                superseded_confirmation_result,
            )
            superseded_review_ref = {
                "reviewId": superseded_confirmation_gate["sourceReviewId"],
                "sha256": superseded_confirmation_gate["sourceReviewSha256"],
            }
            if superseded_review_ref not in terminal_review_gate["ancestry"]:
                blockers.append(
                    "superseded_confirmation_review_not_in_terminal_ancestry"
                )
            elif superseded_review_ref == terminal_review_gate["ancestry"][0]:
                blockers.append("superseding_confirmation_review_not_newer")

        existing_results = {
            row["command_id"]: _command_result(
                row, "Istniejące human confirmation"
            )
            for row in existing_confirmations
        }
        superseded_ids = {
            result.get("supersedesHumanConfirmationCommandId")
            for result in existing_results.values()
            if isinstance(
                result.get("supersedesHumanConfirmationCommandId"), str
            )
        }
        leaf_confirmation_ids = set(existing_results) - superseded_ids
        if leaf_confirmation_ids != {superseded_command_id}:
            blockers.append("superseded_confirmation_is_not_current_leaf")
    if _row(connection, "research_cases", payload["caseId"]) is None:
        blockers.append("missing_case")

    state = {
        "mergeCommand": dict(merge_row) if merge_row else None,
        "mergeResult": merge_result,
        "ratificationCommand": dict(ratification_row) if ratification_row else None,
        "ratificationResult": ratification_result,
        "ratificationArtifact": (
            prior_ratification_gate["artifact"]
            if prior_ratification_gate is not None
            else None
        ),
        "identityDecision": decision,
        "candidate": candidate,
        "candidateDecisions": candidate_decisions,
        "externalOccurrence": external_occurrence,
        "externalShell": external_shell,
        "localAnchorOccurrence": local_anchor_occurrence,
        "protectedDependencies": protected_dependencies,
        "terminalReviewPath": terminal_review_gate["path"].relative_to(
            root.resolve()
        ).as_posix(),
        "terminalReviewSha256": terminal_review_gate["sha256"],
        "confirmationArtifactPath": confirmation_gate["artifactPath"],
        "confirmationArtifactSha256": confirmation_gate["artifactSha256"],
        "existingConfirmations": existing_confirmations,
        "supersededConfirmationResult": superseded_confirmation_result,
        "supersededConfirmationArtifact": (
            superseded_confirmation_gate["artifact"]
            if superseded_confirmation_gate is not None
            else None
        ),
    }
    return {
        "operation": HUMAN_CONFIRMATION_OPERATION,
        "operationId": payload["operationId"],
        "caseId": payload["caseId"],
        "candidateId": candidate_id,
        "priorAgentCommandId": merge_command_id,
        "priorAgentRatificationCommandId": ratification_command_id,
        "priorDecisionId": decision_id,
        "identityDecisionTier": _HUMAN_DECISION_TIER,
        "writeSet": [],
        "fingerprint": fingerprint(state),
        "commandIdRequired": True,
        "applyAllowed": not blockers,
        "blockers": blockers,
        "sourceReviewPath": terminal_review_gate["path"].relative_to(
            root.resolve()
        ).as_posix(),
        "sourceReviewSha256": terminal_review_gate["sha256"],
        "supersedesHumanConfirmationCommandId": payload.get(
            "supersedesHumanConfirmationCommandId"
        ),
    }


def identity_only_human_confirmation_preview(
    root: Path, manifest: Path
) -> dict[str, Any]:
    """Validate a human confirmation of an existing ratified agent identity."""

    payload, confirmation_gate = _load_human_confirmation_manifest(root, manifest)
    with _current_connection(root, readonly=True) as connection:
        require_tables(connection, REQUIRED_TABLES)
        return _human_confirmation_inspect(
            root, connection, payload, confirmation_gate
        )


def apply_identity_only_human_confirmation(
    root: Path,
    manifest: Path,
    *,
    expected_fingerprint: str,
    command_id: str,
) -> dict[str, Any]:
    """Append a human confirmation without modifying identity domain rows."""

    payload, confirmation_gate = _load_human_confirmation_manifest(root, manifest)
    expected_fingerprint = _text(expected_fingerprint, "expectedFingerprint")
    command_id = _text(command_id, "commandId")
    manifest_fingerprint = fingerprint(payload)
    request_fingerprint = fingerprint(
        {
            "manifestFingerprint": manifest_fingerprint,
            "expectedFingerprint": expected_fingerprint,
        }
    )
    with _current_connection(root, readonly=False) as connection:
        require_tables(connection, REQUIRED_TABLES)
        connection.execute("BEGIN IMMEDIATE")
        try:
            replay = replay_command(
                connection,
                command_id=command_id,
                command_type=HUMAN_CONFIRMATION_OPERATION,
                object_type="identity_candidate",
                object_id=payload["candidateId"],
                request_fingerprint=request_fingerprint,
            )
        except CommandEnvelopeConflict as error:
            raise IdentityOnlyCurationConflict(str(error)) from error
        if replay is not None:
            return replay
        preview = _human_confirmation_inspect(
            root, connection, payload, confirmation_gate
        )
        if preview["fingerprint"] != expected_fingerprint:
            raise IdentityOnlyCurationConflict(
                "Stan potwierdzenia zmienił się po preview; odśwież fingerprint"
            )
        if not preview["applyAllowed"]:
            raise IdentityOnlyCurationError(
                "Potwierdzenie identity-only jest zablokowane: "
                + ", ".join(preview["blockers"])
            )
        result = {
            "operation": HUMAN_CONFIRMATION_OPERATION,
            "status": "human_confirmed_existing_agent_same",
            "candidateId": payload["candidateId"],
            "priorAgentCommandId": payload["priorAgentCommandId"],
            "priorAgentRatificationCommandId": payload[
                "priorAgentRatificationCommandId"
            ],
            "priorDecisionId": payload["expectedDecisionId"],
            "identityDecisionTier": _HUMAN_DECISION_TIER,
            "writeSet": [],
            "confirmationId": payload["operationId"],
            "humanConfirmationArtifactPath": confirmation_gate["artifactPath"],
            "humanConfirmationSha256": confirmation_gate["artifactSha256"],
            "sourceReviewPath": preview["sourceReviewPath"],
            "sourceReviewSha256": preview["sourceReviewSha256"],
            "projectionStatus": "not_required",
        }
        if payload["version"] == 2:
            result.update(
                {
                    "supersedesHumanConfirmationCommandId": payload[
                        "supersedesHumanConfirmationCommandId"
                    ],
                    "supersededHumanConfirmationResultSha256": confirmation_gate[
                        "artifact"
                    ]["supersededHumanConfirmationResultSha256"],
                }
            )
        record_command(
            connection,
            command_id=command_id,
            command_type=HUMAN_CONFIRMATION_OPERATION,
            object_type="identity_candidate",
            object_id=payload["candidateId"],
            input_fingerprint=expected_fingerprint,
            request_fingerprint=request_fingerprint,
            actor=payload["confirmedBy"],
            rationale=payload["rationale"],
            result=result,
            projection_status="not_required",
        )
        connection.execute(
            """INSERT INTO audit_log(
                   occurred_at,actor,action,object_type,object_id,
                   before_json,after_json,rationale
               ) VALUES (?,?,?,?,?,?,?,?)""",
            (
                utc_now(),
                payload["confirmedBy"],
                HUMAN_CONFIRMATION_OPERATION,
                "identity_candidate",
                payload["candidateId"],
                json.dumps(
                    {
                        "priorAgentCommandId": payload["priorAgentCommandId"],
                        "priorAgentRatificationCommandId": payload[
                            "priorAgentRatificationCommandId"
                        ],
                        "priorDecisionId": payload["expectedDecisionId"],
                        "identityDecisionTier": _AGENT_DECISION_TIER,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                json.dumps(result, ensure_ascii=False, sort_keys=True),
                payload["rationale"],
            ),
        )
        return {**result, "commandId": command_id, "alreadyApplied": False}


__all__ = [
    "IdentityOnlyCurationConflict",
    "IdentityOnlyCurationError",
    "apply_identity_only_external_candidate_disposition",
    "apply_identity_only_external_shell_merge",
    "apply_identity_only_agent_ratification",
    "apply_identity_only_human_confirmation",
    "identity_only_agent_ratification_preview",
    "identity_only_external_candidate_disposition_preview",
    "identity_only_external_shell_merge_preview",
    "identity_only_human_confirmation_preview",
]
