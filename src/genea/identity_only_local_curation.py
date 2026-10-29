from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .command_envelope import (
    CommandEnvelopeConflict,
    fingerprint,
    record_command,
    replay_command,
)
from .identity_only_curation import (
    IdentityOnlyCurationConflict,
    IdentityOnlyCurationError,
    _current_connection,
    _is_within,
    _load_json,
    _row,
    _rows,
    _sha256,
    _strict_object,
    _text,
)
from .research import require_tables
from .workspace import sha256_file, stable_id, utc_now


SCHEMA_VERSION = 1
OPERATION = "identity_only_local_candidate_resolution"
RATIFICATION_OPERATION = "identity_only_local_agent_ratification"
IDENTITY_DECISION_TIER = "agent_approved_human_pending"
APPLY_MECHANISM = OPERATION
AGENT_REVIEW_VERDICT = "approve_agent_identity_only"

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
    "local",
    "agentReview",
    "decidedBy",
    "rationale",
}
_CANDIDATE_FIELDS = {"id", "expectedStatus", "decision"}
_LOCAL_FIELDS = {
    "occurrenceAId",
    "expectedPersonAId",
    "occurrenceBId",
    "expectedPersonBId",
}
_ARTIFACT_REFERENCE_FIELDS = {"artifactPath", "expectedSha256"}
_REVIEW_REF_FIELDS = {
    "artifactPath",
    "artifactSha256",
    "artifactId",
    "reviewerRole",
    "reviewerRunId",
    "reviewedCandidateIds",
    "verdict",
}
_AGENT_DECISION_FIELDS = {
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
_INDEPENDENT_ROLES = {"source_auditor", "evidence_correlator"}


def _relative_artifact(
    root: Path,
    path_text: Any,
    expected_sha256: Any,
    *,
    label: str,
) -> tuple[Path, str, str]:
    relative = _text(path_text, f"{label}.artifactPath")
    if Path(relative).is_absolute():
        raise IdentityOnlyCurationError(
            f"{label}.artifactPath musi być ścieżką względną repozytorium"
        )
    allowed_root = (root / "research/records/reassessments").resolve()
    resolved = (root / relative).resolve()
    if not _is_within(resolved, allowed_root):
        raise IdentityOnlyCurationError(
            f"{label} musi znajdować się w research/records/reassessments"
        )
    if not resolved.is_file():
        raise IdentityOnlyCurationError(f"Nie znaleziono artefaktu {label}")
    expected = _sha256(expected_sha256, f"{label}.expectedSha256")
    if sha256_file(resolved) != expected:
        raise IdentityOnlyCurationConflict(
            f"Hash artefaktu {label} nie odpowiada manifestowi"
        )
    return resolved, resolved.relative_to(root.resolve()).as_posix(), expected


def _iso_timestamp(value: Any, label: str) -> str:
    text = _text(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise IdentityOnlyCurationError(
            f"{label} musi być znacznikiem ISO-8601"
        ) from error
    if parsed.tzinfo is None:
        raise IdentityOnlyCurationError(f"{label} musi zawierać strefę czasową")
    return text


def _load_manifest_payload(path: Path) -> dict[str, Any]:
    payload = _strict_object(
        _load_json(path, "manifestu lokalnej decyzji identity-only"),
        "manifest",
        _TOP_LEVEL_FIELDS,
    )
    if payload["version"] != SCHEMA_VERSION:
        raise IdentityOnlyCurationError("Manifest musi mieć version=1")
    if payload["operation"] != OPERATION:
        raise IdentityOnlyCurationError(f"Manifest musi mieć operation={OPERATION}")
    for field in ("operationId", "reviewId", "caseId", "decidedBy", "rationale"):
        payload[field] = _text(payload[field], field)
    if len(payload["rationale"]) < 20:
        raise IdentityOnlyCurationError("Uzasadnienie musi mieć co najmniej 20 znaków")

    candidate = _strict_object(payload["candidate"], "candidate", _CANDIDATE_FIELDS)
    candidate["id"] = _text(candidate["id"], "candidate.id")
    if candidate["expectedStatus"] != "unresolved" or candidate["decision"] != "same":
        raise IdentityOnlyCurationError(
            "Lokalna operacja identity-only wymaga expectedStatus=unresolved i decision=same"
        )
    local = _strict_object(payload["local"], "local", _LOCAL_FIELDS)
    for field in _LOCAL_FIELDS:
        local[field] = _text(local[field], f"local.{field}")
    if local["occurrenceAId"] == local["occurrenceBId"]:
        raise IdentityOnlyCurationError("Lokalne occurrences muszą być różne")
    if local["expectedPersonAId"] == local["expectedPersonBId"]:
        raise IdentityOnlyCurationError("Lokalne occurrences muszą należeć do różnych osób")
    _strict_object(payload["agentReview"], "agentReview", _ARTIFACT_REFERENCE_FIELDS)
    return payload


def _terminal_local_review(
    root: Path,
    *,
    case_id: str,
    review_id: str,
    review_sha256: str,
    candidate_id: str,
) -> dict[str, Any]:
    allowed_root = (root / "research/records/reassessments").resolve()
    records: list[tuple[Path, dict[str, Any], str]] = []
    for path in allowed_root.rglob("*.json"):
        try:
            artifact = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            isinstance(artifact, dict)
            and artifact.get("artifactType") == "strict_local_identity_review"
            and artifact.get("caseId") == case_id
        ):
            records.append((path.resolve(), artifact, sha256_file(path)))
    matches = [record for record in records if record[1].get("reviewId") == review_id]
    if len(matches) != 1 or matches[0][2] != review_sha256:
        raise IdentityOnlyCurationError(
            "Nie znaleziono jednoznacznego strict local identity review"
        )
    path, review, actual_sha = matches[0]
    successors = [
        item for item in records if item[1].get("supersedesReviewId") == review_id
    ]
    if successors:
        raise IdentityOnlyCurationError(
            "Decyzja wskazuje zastąpiony, nieterminalny strict local identity review"
        )
    if type(review.get("version")) is not int or review["version"] < 3:
        raise IdentityOnlyCurationError(
            "Terminalny strict local identity review musi mieć version>=3"
        )
    if review.get("operational") is not False:
        raise IdentityOnlyCurationError("Strict local identity review musi być nieoperacyjny")
    guardrails = review.get("guardrails")
    if (
        not isinstance(guardrails, dict)
        or guardrails.get("requiredApplyMechanism") != APPLY_MECHANISM
        or guardrails.get("directOnly") is not True
        or guardrails.get("transitiveMerge") is not False
    ):
        raise IdentityOnlyCurationError(
            "Strict local identity review nie ma bezpiecznych lokalnych guardrails"
        )
    entries = review.get("entries")
    matching_entries = (
        [entry for entry in entries if isinstance(entry, dict) and entry.get("candidateId") == candidate_id]
        if isinstance(entries, list)
        else []
    )
    if len(matching_entries) != 1 or matching_entries[0].get(
        "adjudicatedRecommendation"
    ) != "same":
        raise IdentityOnlyCurationError(
            "Strict local identity review nie zawiera jednej adjudykowanej decyzji same"
        )
    return {
        "path": path.relative_to(root.resolve()).as_posix(),
        "resolvedPath": path,
        "sha256": actual_sha,
        "review": review,
    }


def _validate_review_ref(
    root: Path,
    raw_ref: Any,
    *,
    role: str,
    case_id: str,
    candidate_id: str,
    source_review_id: str,
    source_review_sha256: str,
    forbidden_paths: set[Path],
) -> dict[str, Any]:
    ref = _strict_object(raw_ref, f"{role}Ref", _REVIEW_REF_FIELDS)
    if ref["reviewerRole"] != role:
        raise IdentityOnlyCurationError(f"Review ref musi mieć reviewerRole={role}")
    resolved, relative, expected_sha = _relative_artifact(
        root,
        ref["artifactPath"],
        ref["artifactSha256"],
        label=f"{role}Ref",
    )
    if resolved in forbidden_paths:
        raise IdentityOnlyCurationError("Review refs muszą być rozłączne od decyzji i strict review")
    artifact_id = _text(ref["artifactId"], f"{role}Ref.artifactId")
    reviewer_run_id = _text(ref["reviewerRunId"], f"{role}Ref.reviewerRunId")
    candidate_ids = ref["reviewedCandidateIds"]
    if not isinstance(candidate_ids, list) or not candidate_ids:
        raise IdentityOnlyCurationError("reviewedCandidateIds musi być niepustą listą")
    normalized_ids = [_text(value, "reviewedCandidateIds") for value in candidate_ids]
    if len(normalized_ids) != len(set(normalized_ids)) or candidate_id not in normalized_ids:
        raise IdentityOnlyCurationError("Review ref nie obejmuje jednoznacznie kandydatury")
    verdict = _text(ref["verdict"], f"{role}Ref.verdict")
    if verdict != AGENT_REVIEW_VERDICT:
        raise IdentityOnlyCurationError(
            f"Review {role} musi mieć verdict={AGENT_REVIEW_VERDICT}"
        )
    artifact = _load_json(resolved, f"artefaktu {role}")
    expected_type = {
        "source_auditor": "strict_identity_source_audit",
        "evidence_correlator": "strict_identity_evidence_correlation",
    }[role]
    if (
        artifact.get("version") != 1
        or artifact.get("artifactType") != expected_type
        or artifact.get("artifactId") != artifact_id
        or artifact.get("caseId") != case_id
        or artifact.get("reviewerRole") != role
        or artifact.get("reviewerRunId") != reviewer_run_id
        or artifact.get("sourceReviewId") not in {None, source_review_id}
        or artifact.get("sourceReviewSha256") not in {None, source_review_sha256}
    ):
        raise IdentityOnlyCurationError(f"Artefakt {role} nie odpowiada referencji")
    results = artifact.get("results")
    result_ids = (
        [item.get("candidateId") for item in results if isinstance(item, dict)]
        if isinstance(results, list)
        else []
    )
    matching = [item for item in results or [] if isinstance(item, dict) and item.get("candidateId") == candidate_id]
    if result_ids != normalized_ids or len(matching) != 1:
        raise IdentityOnlyCurationError(f"Artefakt {role} nie obejmuje dokładnego reviewedCandidateIds")
    artifact_verdict = matching[0].get("verdict")
    if artifact_verdict != verdict:
        raise IdentityOnlyCurationError(f"Verdict artefaktu {role} nie odpowiada referencji")
    return {
        "artifactPath": relative,
        "artifactSha256": expected_sha,
        "artifactId": artifact_id,
        "reviewerRole": role,
        "reviewerRunId": reviewer_run_id,
        "reviewedCandidateIds": normalized_ids,
        "verdict": verdict,
    }


def _review_gate(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    reference = payload["agentReview"]
    decision_path, decision_relative, decision_sha = _relative_artifact(
        root,
        reference["artifactPath"],
        reference["expectedSha256"],
        label="agentReview",
    )
    decision = _strict_object(
        _load_json(decision_path, "lokalnej decyzji agenta"),
        "local agent decision",
        _AGENT_DECISION_FIELDS,
    )
    if (
        decision["version"] != 1
        or decision["artifactType"] != "strict_local_identity_agent_decision"
        or decision["decision"] != "approve_same"
        or decision["decidedByKind"] != "agent"
        or decision["identityDecisionTier"] != IDENTITY_DECISION_TIER
    ):
        raise IdentityOnlyCurationError("Nieprawidłowy artefakt lokalnej decyzji agenta")
    for field in (
        "reviewId",
        "sourceReviewId",
        "caseId",
        "candidateId",
        "decidedBy",
        "rationale",
    ):
        decision[field] = _text(decision[field], f"agentDecision.{field}")
    decision["decidedAt"] = _iso_timestamp(decision["decidedAt"], "agentDecision.decidedAt")
    decision["sourceReviewSha256"] = _sha256(
        decision["sourceReviewSha256"], "agentDecision.sourceReviewSha256"
    )
    expected = {
        "reviewId": payload["reviewId"],
        "caseId": payload["caseId"],
        "candidateId": payload["candidate"]["id"],
        "decidedBy": payload["decidedBy"],
        "rationale": payload["rationale"],
    }
    if any(decision[field] != value for field, value in expected.items()):
        raise IdentityOnlyCurationError("Lokalna decyzja agenta nie odpowiada manifestowi")
    terminal = _terminal_local_review(
        root,
        case_id=payload["caseId"],
        review_id=decision["sourceReviewId"],
        review_sha256=decision["sourceReviewSha256"],
        candidate_id=payload["candidate"]["id"],
    )
    forbidden = {decision_path, terminal["resolvedPath"]}
    raw_independent = decision["independentReviewRefs"]
    if not isinstance(raw_independent, list) or len(raw_independent) != 2:
        raise IdentityOnlyCurationError("Decyzja wymaga source audit i evidence correlation")
    by_role = {
        item.get("reviewerRole"): item
        for item in raw_independent
        if isinstance(item, dict)
    }
    if set(by_role) != _INDEPENDENT_ROLES:
        raise IdentityOnlyCurationError("Decyzja wymaga rozłącznych source_auditor i evidence_correlator")
    independent_refs = [
        _validate_review_ref(
            root,
            item,
            role=item["reviewerRole"],
            case_id=payload["caseId"],
            candidate_id=payload["candidate"]["id"],
            source_review_id=decision["sourceReviewId"],
            source_review_sha256=decision["sourceReviewSha256"],
            forbidden_paths=forbidden,
        )
        for item in raw_independent
    ]
    if len({item["artifactSha256"] for item in independent_refs}) != 2 or len(
        {item["reviewerRunId"] for item in independent_refs}
    ) != 2:
        raise IdentityOnlyCurationError("Dwa review muszą mieć rozłączne treści i przebiegi")
    strict_review = terminal["review"]
    if strict_review.get("independentReviewRefs") != raw_independent:
        raise IdentityOnlyCurationError(
            "Decyzja agenta nie wiąże tych samych review refs co terminalny strict review"
        )
    return {
        "artifactPath": decision_relative,
        "artifactSha256": decision_sha,
        "decision": decision,
        "sourceReviewPath": terminal["path"],
        "sourceReviewSha256": terminal["sha256"],
        "independentReviewRefs": independent_refs,
    }


def _endpoint_dependencies(connection, person_id: str, occurrence_id: str) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    return {
        "personNames": _rows(connection, "SELECT * FROM person_names WHERE person_id=? ORDER BY id", (person_id,)),
        "memberships": _rows(connection, "SELECT * FROM household_memberships WHERE person_id=? OR occurrence_id=? ORDER BY id", (person_id, occurrence_id)),
        "questionTargets": _rows(connection, "SELECT * FROM research_question_targets WHERE (target_type='person' AND target_id=?) OR (target_type='person_occurrence' AND target_id=?) ORDER BY id", (person_id, occurrence_id)),
        "externalSync": _rows(connection, "SELECT * FROM external_sync_items WHERE object_type='person' AND object_id=? ORDER BY id", (person_id,)),
        "privateFields": _rows(connection, "SELECT * FROM private_fields WHERE (object_type='person' AND object_id=?) OR (object_type='person_occurrence' AND object_id=?) ORDER BY id", (person_id, occurrence_id)),
        "privateAttachments": _rows(connection, "SELECT * FROM private_attachments WHERE (object_type='person' AND object_id=?) OR (object_type='person_occurrence' AND object_id=?) ORDER BY id", (person_id, occurrence_id)),
        "privateRelationships": _rows(connection, "SELECT * FROM private_relationships WHERE subject_person_id=? OR object_person_id=? ORDER BY id", (person_id, person_id)),
        "familyNotes": _rows(connection, "SELECT * FROM family_notes WHERE (subject_type='person' AND subject_id=?) OR (subject_type='person_occurrence' AND subject_id=?) ORDER BY id", (person_id, occurrence_id)),
    }


def _operation_state(connection, payload: dict[str, Any], review_gate: dict[str, Any]) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    candidate_id = payload["candidate"]["id"]
    local = payload["local"]
    occurrence_ids = (local["occurrenceAId"], local["occurrenceBId"])
    person_ids = (local["expectedPersonAId"], local["expectedPersonBId"])
    occurrences = [_row(connection, "person_occurrences", item) for item in occurrence_ids]
    sources = [
        _row(connection, "source_items", row["source_item_id"]) if row is not None else None
        for row in occurrences
    ]
    return {
        "case": dict(row) if (row := _row(connection, "research_cases", payload["caseId"])) else None,
        "candidate": dict(row) if (row := _row(connection, "identity_candidates", candidate_id)) else None,
        "candidateDecisions": _rows(connection, "SELECT * FROM identity_decisions WHERE candidate_id=? ORDER BY id", (candidate_id,)),
        "occurrences": [dict(row) if row is not None else None for row in occurrences],
        "persons": [dict(row) if (row := _row(connection, "persons", person_id)) else None for person_id in person_ids],
        "sources": [dict(row) if row is not None else None for row in sources],
        "privacy": _rows(connection, """SELECT * FROM privacy_labels
             WHERE (object_type='person' AND object_id IN (?,?))
                OR (object_type='person_occurrence' AND object_id IN (?,?))
                OR (object_type='source_item' AND object_id IN (?,?))
             ORDER BY object_type,object_id,id""", (
                 *person_ids,
                 *occurrence_ids,
                 sources[0]["id"] if sources[0] is not None else "",
                 sources[1]["id"] if sources[1] is not None else "",
             )),
        "dependencies": [
            _endpoint_dependencies(connection, person_id, occurrence_id)
            for person_id, occurrence_id in zip(person_ids, occurrence_ids)
        ],
        "sharedEndpointCandidates": _rows(connection, """SELECT * FROM identity_candidates
             WHERE id<>? AND (occurrence_a_id IN (?,?) OR occurrence_b_id IN (?,?))
             ORDER BY id""", (candidate_id, *occurrence_ids, *occurrence_ids)),
        "reviewGate": review_gate,
    }


def _inspect(connection, payload: dict[str, Any], review_gate: dict[str, Any]) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    state = _operation_state(connection, payload, review_gate)
    blockers: list[str] = []
    candidate = state["candidate"]
    local = payload["local"]
    occurrence_ids = {local["occurrenceAId"], local["occurrenceBId"]}
    if state["case"] is None:
        blockers.append("missing_case")
    if candidate is None:
        blockers.append("missing_candidate")
    else:
        if candidate["status"] != "unresolved":
            blockers.append("candidate_status_mismatch")
        if {candidate["occurrence_a_id"], candidate["occurrence_b_id"]} != occurrence_ids:
            blockers.append("candidate_endpoints_mismatch")
    if state["candidateDecisions"]:
        blockers.append("candidate_already_has_decision")
    for index, (occurrence, person_id) in enumerate(
        zip(state["occurrences"], (local["expectedPersonAId"], local["expectedPersonBId"]))
    ):
        label = "a" if index == 0 else "b"
        if occurrence is None:
            blockers.append(f"missing_occurrence_{label}")
        elif occurrence["person_id"] != person_id:
            blockers.append(f"occurrence_{label}_person_mismatch")
        if state["persons"][index] is None:
            blockers.append(f"missing_person_{label}")
        elif state["persons"][index]["status"] == "merged":
            blockers.append(f"person_{label}_already_merged")
        if state["sources"][index] is None:
            blockers.append(f"missing_source_{label}")
        elif state["sources"][index]["item_type"] == "external_tree_profile":
            blockers.append(f"occurrence_{label}_is_external_profile")
    write_set = [
        {"table": "identity_candidates", "id": payload["candidate"]["id"], "action": "update_status"},
        {"table": "identity_decisions", "action": "insert_one"},
        {"table": "curation_commands", "action": "insert_one"},
        {"table": "audit_log", "action": "insert_one"},
    ]
    return {
        "operation": OPERATION,
        "operationId": payload["operationId"],
        "reviewId": payload["reviewId"],
        "caseId": payload["caseId"],
        "candidateId": payload["candidate"]["id"],
        "identityDecisionTier": IDENTITY_DECISION_TIER,
        "occurrenceIds": [local["occurrenceAId"], local["occurrenceBId"]],
        "personIds": [local["expectedPersonAId"], local["expectedPersonBId"]],
        "fingerprint": fingerprint(state),
        "commandIdRequired": True,
        "applyAllowed": not blockers,
        "blockers": blockers,
        "writeSet": write_set,
        "independentReviewRefs": review_gate["independentReviewRefs"],
    }


def identity_only_local_candidate_resolution_preview(root: Path, manifest: Path) -> dict[str, Any]:
    payload = _load_manifest_payload(manifest)
    review_gate = _review_gate(root, payload)
    with _current_connection(root, readonly=True) as connection:
        require_tables(connection, REQUIRED_TABLES)
        return _inspect(connection, payload, review_gate)


def apply_identity_only_local_candidate_resolution(
    root: Path,
    manifest: Path,
    *,
    expected_fingerprint: str,
    command_id: str,
) -> dict[str, Any]:
    payload = _load_manifest_payload(manifest)
    expected_fingerprint = _text(expected_fingerprint, "expectedFingerprint")
    command_id = _text(command_id, "commandId")
    manifest_fingerprint = fingerprint(payload)
    request_fingerprint = fingerprint({
        "manifestFingerprint": manifest_fingerprint,
        "expectedFingerprint": expected_fingerprint,
    })
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
        review_gate = _review_gate(root, payload)
        preview = _inspect(connection, payload, review_gate)
        if preview["fingerprint"] != expected_fingerprint:
            raise IdentityOnlyCurationConflict(
                "Stan lokalnej operacji identity-only zmienił się po preview"
            )
        if not preview["applyAllowed"]:
            raise IdentityOnlyCurationError(
                "Lokalna operacja identity-only jest zablokowana: "
                + ", ".join(preview["blockers"])
            )
        candidate_id = payload["candidate"]["id"]
        decision_id = stable_id("identity_decision", OPERATION, candidate_id, command_id)
        now = utc_now()
        result = {
            "operation": OPERATION,
            "operationId": payload["operationId"],
            "reviewId": payload["reviewId"],
            "caseId": payload["caseId"],
            "candidateId": candidate_id,
            "status": "resolved_same",
            "decision": "same",
            "decidedByKind": "agent",
            "identityDecisionTier": IDENTITY_DECISION_TIER,
            "decisionId": decision_id,
            "commandId": command_id,
            "inputFingerprint": expected_fingerprint,
            "manifestFingerprint": manifest_fingerprint,
            "occurrenceIds": preview["occurrenceIds"],
            "personIds": preview["personIds"],
            "agentReviewArtifactPath": review_gate["artifactPath"],
            "agentReviewSha256": review_gate["artifactSha256"],
            "sourceReviewPath": review_gate["sourceReviewPath"],
            "sourceReviewSha256": review_gate["sourceReviewSha256"],
            "independentReviewRefs": review_gate["independentReviewRefs"],
            "writeSet": preview["writeSet"],
            "projectionStatus": "pending",
        }
        record_command(
            connection,
            command_id=command_id,
            command_type=OPERATION,
            object_type="identity_candidate",
            object_id=candidate_id,
            input_fingerprint=expected_fingerprint,
            request_fingerprint=request_fingerprint,
            actor=payload["decidedBy"],
            rationale=payload["rationale"],
            result=result,
        )
        updated = connection.execute(
            "UPDATE identity_candidates SET status='resolved_same' WHERE id=? AND status='unresolved'",
            (candidate_id,),
        )
        if updated.rowcount != 1:
            raise IdentityOnlyCurationConflict("Kandydatura nie jest już unresolved")
        connection.execute(
            """INSERT INTO identity_decisions(
                   id,candidate_id,decision,decided_by,decided_at,rationale,
                   command_id,input_fingerprint
               ) VALUES (?,?,?,?,?,?,?,?)""",
            (
                decision_id,
                candidate_id,
                "same",
                payload["decidedBy"],
                now,
                payload["rationale"],
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
                payload["decidedBy"],
                OPERATION,
                "identity_candidate",
                candidate_id,
                json.dumps({"candidateStatus": "unresolved", "decisionCount": 0}, sort_keys=True),
                json.dumps(result, ensure_ascii=False, sort_keys=True),
                payload["rationale"],
            ),
        )
        return {**result, "alreadyApplied": False}


def _load_ratification_manifest(root: Path, path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = _strict_object(
        _load_json(path, "manifestu lokalnej ratyfikacji identity-only"),
        "ratification manifest",
        _RATIFICATION_TOP_LEVEL_FIELDS,
    )
    if payload["version"] != 1 or payload["operation"] != RATIFICATION_OPERATION:
        raise IdentityOnlyCurationError(
            f"Manifest ratyfikacji wymaga version=1 i operation={RATIFICATION_OPERATION}"
        )
    for field in (
        "operationId", "caseId", "candidateId", "priorCommandId",
        "expectedDecisionId", "ratifiedBy", "rationale",
    ):
        payload[field] = _text(payload[field], f"ratification.{field}")
    if len(payload["rationale"]) < 20:
        raise IdentityOnlyCurationError("Uzasadnienie ratyfikacji musi mieć co najmniej 20 znaków")
    reference = _strict_object(
        payload["ratificationReview"], "ratificationReview", _ARTIFACT_REFERENCE_FIELDS
    )
    artifact_path, relative, artifact_sha = _relative_artifact(
        root,
        reference["artifactPath"],
        reference["expectedSha256"],
        label="ratificationReview",
    )
    artifact = _strict_object(
        _load_json(artifact_path, "lokalnego artefaktu ratyfikacji"),
        "ratification artifact",
        _RATIFICATION_ARTIFACT_FIELDS,
    )
    if (
        artifact["version"] != 1
        or artifact["artifactType"] != "strict_identity_local_agent_ratification"
        or artifact["decision"] != "ratify_agent_identity_only"
        or artifact["decidedByKind"] != "agent"
        or artifact["identityDecisionTier"] != IDENTITY_DECISION_TIER
    ):
        raise IdentityOnlyCurationError("Nieprawidłowy lokalny artefakt ratyfikacji")
    for field in (
        "ratificationId", "caseId", "candidateId", "priorCommandId",
        "priorDecisionId", "sourceReviewId", "decidedBy", "rationale",
    ):
        artifact[field] = _text(artifact[field], f"ratificationArtifact.{field}")
    artifact["decidedAt"] = _iso_timestamp(artifact["decidedAt"], "ratificationArtifact.decidedAt")
    for field in (
        "priorCommandResultSha256", "agentReviewSha256", "sourceReviewSha256"
    ):
        artifact[field] = _sha256(artifact[field], f"ratificationArtifact.{field}")
    expected = {
        "ratificationId": payload["operationId"],
        "caseId": payload["caseId"],
        "candidateId": payload["candidateId"],
        "priorCommandId": payload["priorCommandId"],
        "priorDecisionId": payload["expectedDecisionId"],
        "decidedBy": payload["ratifiedBy"],
        "rationale": payload["rationale"],
    }
    if any(artifact[field] != value for field, value in expected.items()):
        raise IdentityOnlyCurationError("Artefakt ratyfikacji nie odpowiada manifestowi")
    return payload, {
        "artifact": artifact,
        "artifactPath": relative,
        "artifactSha256": artifact_sha,
    }


def _ratification_domain_state(
    connection,  # type: ignore[no-untyped-def]
    *,
    candidate_id: str,
    prior_result: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    blockers: list[str] = []
    occurrence_ids = prior_result.get("occurrenceIds") if prior_result else None
    person_ids = prior_result.get("personIds") if prior_result else None
    valid_occurrence_ids = (
        isinstance(occurrence_ids, list)
        and len(occurrence_ids) == 2
        and all(isinstance(value, str) and value.strip() for value in occurrence_ids)
        and len(set(occurrence_ids)) == 2
    )
    valid_person_ids = (
        isinstance(person_ids, list)
        and len(person_ids) == 2
        and all(isinstance(value, str) and value.strip() for value in person_ids)
        and len(set(person_ids)) == 2
    )
    candidate_row = _row(connection, "identity_candidates", candidate_id)
    candidate = dict(candidate_row) if candidate_row else None
    if not valid_occurrence_ids:
        blockers.append("prior_result_occurrence_endpoints_invalid")
    if not valid_person_ids:
        blockers.append("prior_result_person_endpoints_invalid")
    if not valid_occurrence_ids or not valid_person_ids:
        return {
            "candidate": candidate,
            "occurrenceIds": occurrence_ids,
            "personIds": person_ids,
            "occurrences": [],
            "persons": [],
            "sources": [],
            "privacy": [],
            "dependencies": [],
            "sharedEndpointCandidates": [],
        }, blockers

    normalized_occurrence_ids = [value.strip() for value in occurrence_ids]
    normalized_person_ids = [value.strip() for value in person_ids]
    if candidate is None:
        blockers.append("missing_candidate")
    elif {
        candidate["occurrence_a_id"],
        candidate["occurrence_b_id"],
    } != set(normalized_occurrence_ids):
        blockers.append("prior_result_candidate_endpoints_mismatch")

    occurrence_rows = [
        _row(connection, "person_occurrences", occurrence_id)
        for occurrence_id in normalized_occurrence_ids
    ]
    occurrences = [dict(row) if row is not None else None for row in occurrence_rows]
    persons = [
        dict(row) if (row := _row(connection, "persons", person_id)) else None
        for person_id in normalized_person_ids
    ]
    sources: list[dict[str, Any] | None] = []
    for index, occurrence in enumerate(occurrences):
        label = "a" if index == 0 else "b"
        if occurrence is None:
            blockers.append(f"missing_current_occurrence_{label}")
            sources.append(None)
        else:
            if occurrence["person_id"] != normalized_person_ids[index]:
                blockers.append(f"current_occurrence_{label}_person_mismatch")
            source_row = _row(
                connection,
                "source_items",
                occurrence["source_item_id"],
            )
            sources.append(dict(source_row) if source_row is not None else None)
            if source_row is None:
                blockers.append(f"missing_current_source_{label}")
        if persons[index] is None:
            blockers.append(f"missing_current_person_{label}")

    source_ids = [source["id"] if source is not None else "" for source in sources]
    privacy = _rows(
        connection,
        """SELECT * FROM privacy_labels
             WHERE (object_type='person' AND object_id IN (?,?))
                OR (object_type='person_occurrence' AND object_id IN (?,?))
                OR (object_type='source_item' AND object_id IN (?,?))
             ORDER BY object_type,object_id,id""",
        (
            *normalized_person_ids,
            *normalized_occurrence_ids,
            *source_ids,
        ),
    )
    dependencies = [
        _endpoint_dependencies(connection, person_id, occurrence_id)
        for person_id, occurrence_id in zip(
            normalized_person_ids,
            normalized_occurrence_ids,
        )
    ]
    shared_candidates = _rows(
        connection,
        """SELECT * FROM identity_candidates
             WHERE id<>? AND (occurrence_a_id IN (?,?) OR occurrence_b_id IN (?,?))
             ORDER BY id""",
        (
            candidate_id,
            *normalized_occurrence_ids,
            *normalized_occurrence_ids,
        ),
    )
    return {
        "candidate": candidate,
        "occurrenceIds": normalized_occurrence_ids,
        "personIds": normalized_person_ids,
        "occurrences": occurrences,
        "persons": persons,
        "sources": sources,
        "privacy": privacy,
        "dependencies": dependencies,
        "sharedEndpointCandidates": shared_candidates,
    }, blockers


def _ratification_inspect(root: Path, connection, payload: dict[str, Any], gate: dict[str, Any]) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    candidate_id = payload["candidateId"]
    command_row = connection.execute(
        "SELECT * FROM curation_commands WHERE command_id=?",
        (payload["priorCommandId"],),
    ).fetchone()
    command = dict(command_row) if command_row else None
    result: dict[str, Any] | None = None
    blockers: list[str] = []
    if command is None:
        blockers.append("missing_prior_command")
    else:
        if (
            command["command_type"] != OPERATION
            or command["object_type"] != "identity_candidate"
            or command["object_id"] != candidate_id
        ):
            blockers.append("prior_command_scope_mismatch")
        try:
            loaded = json.loads(command["result_json"])
        except (TypeError, json.JSONDecodeError) as error:
            raise IdentityOnlyCurationError("Wynik wcześniejszej komendy nie jest JSON") from error
        if not isinstance(loaded, dict):
            raise IdentityOnlyCurationError("Wynik wcześniejszej komendy musi być obiektem")
        result = loaded
    artifact = gate["artifact"]
    if result is not None:
        expected_result = {
            "operation": OPERATION,
            "candidateId": candidate_id,
            "decisionId": payload["expectedDecisionId"],
            "status": "resolved_same",
            "decision": "same",
            "decidedByKind": "agent",
            "identityDecisionTier": IDENTITY_DECISION_TIER,
        }
        if any(result.get(field) != value for field, value in expected_result.items()):
            blockers.append("prior_command_result_mismatch")
        if fingerprint(result) != artifact["priorCommandResultSha256"]:
            blockers.append("prior_command_result_hash_mismatch")
        if (
            result.get("agentReviewSha256") != artifact["agentReviewSha256"]
            or result.get("sourceReviewSha256") != artifact["sourceReviewSha256"]
        ):
            blockers.append("prior_review_chain_mismatch")
        decision_artifact_path = result.get("agentReviewArtifactPath")
        source_review_path = result.get("sourceReviewPath")
        decision_artifact: dict[str, Any] | None = None
        source_review: dict[str, Any] | None = None
        for path_text, expected_sha, blocker, artifact_kind in (
            (decision_artifact_path, artifact["agentReviewSha256"], "agent_review_drift", "decision"),
            (source_review_path, artifact["sourceReviewSha256"], "source_review_drift", "source"),
        ):
            if not isinstance(path_text, str) or Path(path_text).is_absolute():
                blockers.append(blocker)
                continue
            resolved = (root / path_text).resolve()
            if (
                not _is_within(resolved, (root / "research/records/reassessments").resolve())
                or not resolved.is_file()
                or sha256_file(resolved) != expected_sha
            ):
                blockers.append(blocker)
                continue
            loaded_artifact = _load_json(resolved, f"prior {artifact_kind} review")
            if artifact_kind == "decision":
                decision_artifact = loaded_artifact
            else:
                source_review = loaded_artifact
        if (
            decision_artifact is None
            or decision_artifact.get("artifactType") != "strict_local_identity_agent_decision"
            or decision_artifact.get("candidateId") != candidate_id
            or decision_artifact.get("sourceReviewId") != artifact["sourceReviewId"]
            or decision_artifact.get("sourceReviewSha256") != artifact["sourceReviewSha256"]
            or decision_artifact.get("independentReviewRefs")
            != result.get("independentReviewRefs")
            or source_review is None
            or source_review.get("artifactType") != "strict_local_identity_review"
            or source_review.get("reviewId") != artifact["sourceReviewId"]
        ):
            blockers.append("prior_review_exact_chain_mismatch")
        review_refs = result.get("independentReviewRefs")
        if not isinstance(review_refs, list) or len(review_refs) != 2:
            blockers.append("prior_independent_review_refs_mismatch")
        else:
            for review_ref in review_refs:
                if not isinstance(review_ref, dict):
                    blockers.append("prior_independent_review_refs_mismatch")
                    break
                path_text = review_ref.get("artifactPath")
                expected_sha = review_ref.get("artifactSha256")
                if not isinstance(path_text, str) or not isinstance(expected_sha, str):
                    blockers.append("prior_independent_review_refs_mismatch")
                    break
                resolved = (root / path_text).resolve()
                if (
                    Path(path_text).is_absolute()
                    or not _is_within(
                        resolved,
                        (root / "research/records/reassessments").resolve(),
                    )
                    or not resolved.is_file()
                    or sha256_file(resolved) != expected_sha
                ):
                    blockers.append("prior_independent_review_drift")
                    break
    domain_state, domain_blockers = _ratification_domain_state(
        connection,
        candidate_id=candidate_id,
        prior_result=result,
    )
    blockers.extend(domain_blockers)
    decision_row = connection.execute(
        "SELECT * FROM identity_decisions WHERE id=?",
        (payload["expectedDecisionId"],),
    ).fetchone()
    decision = dict(decision_row) if decision_row else None
    if decision is None:
        blockers.append("missing_prior_identity_decision")
    elif (
        decision["candidate_id"] != candidate_id
        or decision["decision"] != "same"
        or decision["command_id"] != payload["priorCommandId"]
        or (result is not None and decision["input_fingerprint"] != result.get("inputFingerprint"))
    ):
        blockers.append("prior_identity_decision_mismatch")
    candidate_row = _row(connection, "identity_candidates", candidate_id)
    candidate = dict(candidate_row) if candidate_row else None
    if candidate is None or candidate["status"] != "resolved_same":
        blockers.append("candidate_not_resolved_same")
    decisions = _rows(connection, "SELECT * FROM identity_decisions WHERE candidate_id=? ORDER BY id", (candidate_id,))
    if len(decisions) != 1 or (decisions and decisions[0]["id"] != payload["expectedDecisionId"]):
        blockers.append("candidate_decision_set_mismatch")
    existing = _rows(connection, """SELECT * FROM curation_commands
         WHERE command_type=? AND object_type='identity_candidate' AND object_id=?
         ORDER BY command_id""", (RATIFICATION_OPERATION, candidate_id))
    if existing:
        blockers.append("candidate_already_ratified")
    state = {
        "priorCommand": command,
        "priorResult": result,
        "priorDecision": decision,
        "candidate": candidate,
        "candidateDecisions": decisions,
        "domainState": domain_state,
        "ratificationArtifactPath": gate["artifactPath"],
        "ratificationArtifactSha256": gate["artifactSha256"],
        "existingRatifications": existing,
    }
    return {
        "operation": RATIFICATION_OPERATION,
        "operationId": payload["operationId"],
        "caseId": payload["caseId"],
        "candidateId": candidate_id,
        "priorCommandId": payload["priorCommandId"],
        "priorDecisionId": payload["expectedDecisionId"],
        "identityDecisionTier": IDENTITY_DECISION_TIER,
        "writeSet": [],
        "fingerprint": fingerprint(state),
        "commandIdRequired": True,
        "applyAllowed": not blockers,
        "blockers": blockers,
        "priorResult": result,
    }


def _ratification_preview(root: Path, payload: dict[str, Any], gate: dict[str, Any], connection) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    preview = _ratification_inspect(root, connection, payload, gate)
    preview.pop("priorResult", None)
    return preview


def identity_only_local_agent_ratification_preview(root: Path, manifest: Path) -> dict[str, Any]:
    payload, gate = _load_ratification_manifest(root, manifest)
    with _current_connection(root, readonly=True) as connection:
        require_tables(connection, REQUIRED_TABLES)
        return _ratification_preview(root, payload, gate, connection)


def apply_identity_only_local_agent_ratification(
    root: Path,
    manifest: Path,
    *,
    expected_fingerprint: str,
    command_id: str,
) -> dict[str, Any]:
    payload, gate = _load_ratification_manifest(root, manifest)
    expected_fingerprint = _text(expected_fingerprint, "expectedFingerprint")
    command_id = _text(command_id, "commandId")
    manifest_fingerprint = fingerprint(payload)
    request_fingerprint = fingerprint({
        "manifestFingerprint": manifest_fingerprint,
        "expectedFingerprint": expected_fingerprint,
    })
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
        preview = _ratification_preview(root, payload, gate, connection)
        if preview["fingerprint"] != expected_fingerprint:
            raise IdentityOnlyCurationConflict("Stan ratyfikacji zmienił się po preview")
        if not preview["applyAllowed"]:
            raise IdentityOnlyCurationError(
                "Lokalna ratyfikacja jest zablokowana: " + ", ".join(preview["blockers"])
            )
        result = {
            "operation": RATIFICATION_OPERATION,
            "status": "ratified_existing_agent_same",
            "candidateId": payload["candidateId"],
            "priorCommandId": payload["priorCommandId"],
            "priorDecisionId": payload["expectedDecisionId"],
            "identityDecisionTier": IDENTITY_DECISION_TIER,
            "writeSet": [],
            "ratificationId": payload["operationId"],
            "ratificationReviewArtifactPath": gate["artifactPath"],
            "ratificationReviewSha256": gate["artifactSha256"],
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
                utc_now(),
                payload["ratifiedBy"],
                RATIFICATION_OPERATION,
                "identity_candidate",
                payload["candidateId"],
                json.dumps({
                    "priorCommandId": payload["priorCommandId"],
                    "priorDecisionId": payload["expectedDecisionId"],
                }, sort_keys=True),
                json.dumps(result, ensure_ascii=False, sort_keys=True),
                payload["rationale"],
            ),
        )
        return {**result, "commandId": command_id, "alreadyApplied": False}


__all__ = [
    "IdentityOnlyCurationConflict",
    "IdentityOnlyCurationError",
    "apply_identity_only_local_agent_ratification",
    "apply_identity_only_local_candidate_resolution",
    "identity_only_local_agent_ratification_preview",
    "identity_only_local_candidate_resolution_preview",
]
