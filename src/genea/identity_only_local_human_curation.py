from __future__ import annotations

import json
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
from .identity_only_local_curation import _endpoint_dependencies, _iso_timestamp
from .research import require_tables
from .workspace import sha256_file, stable_id, utc_now


SCHEMA_VERSION = 1
OPERATION = "identity_only_local_human_candidate_resolution"
IDENTITY_DECISION_TIER = "human_confirmed"

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
    "humanReview",
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
_HUMAN_DECISION_FIELDS = {
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


def _load_manifest_payload(path: Path) -> dict[str, Any]:
    payload = _strict_object(
        _load_json(path, "manifestu lokalnej decyzji człowieka identity-only"),
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
            "Lokalna decyzja człowieka wymaga expectedStatus=unresolved i decision=same"
        )
    local = _strict_object(payload["local"], "local", _LOCAL_FIELDS)
    for field in _LOCAL_FIELDS:
        local[field] = _text(local[field], f"local.{field}")
    if local["occurrenceAId"] == local["occurrenceBId"]:
        raise IdentityOnlyCurationError("Lokalne occurrences muszą być różne")
    if local["expectedPersonAId"] == local["expectedPersonBId"]:
        raise IdentityOnlyCurationError("Lokalne occurrences muszą należeć do różnych osób")
    _strict_object(payload["humanReview"], "humanReview", _ARTIFACT_REFERENCE_FIELDS)
    return payload


def _json_artifacts(root: Path) -> list[tuple[Path, dict[str, Any], str]]:
    allowed_root = (root / "research/records/reassessments").resolve()
    records: list[tuple[Path, dict[str, Any], str]] = []
    if not allowed_root.is_dir():
        return records
    for path in allowed_root.rglob("*.json"):
        try:
            artifact = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(artifact, dict):
            records.append((path.resolve(), artifact, sha256_file(path)))
    return records


def _reject_nonterminal_lineage(
    records: list[tuple[Path, dict[str, Any], str]],
    *,
    identifier: str,
    identifier_field: str,
    supersedes_fields: tuple[str, ...],
    current_path: Path,
    label: str,
) -> None:
    duplicates = [item for item in records if item[1].get(identifier_field) == identifier]
    if len(duplicates) != 1 or duplicates[0][0] != current_path:
        raise IdentityOnlyCurationError(f"{label} nie jest jednoznacznym artefaktem")
    successors = [
        item
        for item in records
        if any(item[1].get(field) == identifier for field in supersedes_fields)
    ]
    if successors:
        raise IdentityOnlyCurationError(f"{label} został zastąpiony")
    predecessor = next(
        (
            _text_value
            for field in supersedes_fields
            if isinstance((_text_value := duplicates[0][1].get(field)), str)
            and _text_value.strip()
        ),
        None,
    )
    if predecessor is not None:
        siblings = [
            item
            for item in records
            if item[0] != current_path
            and any(item[1].get(field) == predecessor for field in supersedes_fields)
        ]
        if siblings:
            raise IdentityOnlyCurationError(f"{label} ma konkurencyjny fork")


def _terminal_local_review(
    root: Path,
    *,
    records: list[tuple[Path, dict[str, Any], str]],
    case_id: str,
    review_id: str,
    review_sha256: str,
    candidate_id: str,
) -> dict[str, Any]:
    local_reviews = [
        item
        for item in records
        if item[1].get("artifactType") == "strict_local_identity_review"
        and item[1].get("caseId") == case_id
    ]
    matches = [item for item in local_reviews if item[1].get("reviewId") == review_id]
    if len(matches) != 1 or matches[0][2] != review_sha256:
        raise IdentityOnlyCurationError(
            "Nie znaleziono jednoznacznego strict local identity review"
        )
    path, review, actual_sha = matches[0]
    _reject_nonterminal_lineage(
        local_reviews,
        identifier=review_id,
        identifier_field="reviewId",
        supersedes_fields=("supersedesReviewId",),
        current_path=path,
        label="Strict local identity review",
    )
    if type(review.get("version")) is not int or review["version"] < 3:
        raise IdentityOnlyCurationError(
            "Terminalny strict local identity review musi mieć version>=3"
        )
    if review.get("operational") is not False or review.get("apply") != "forbidden":
        raise IdentityOnlyCurationError(
            "Strict local identity review musi być nieoperacyjny i mieć apply=forbidden"
        )
    guardrails = review.get("guardrails")
    if (
        not isinstance(guardrails, dict)
        or guardrails.get("requiredApplyMechanism") != OPERATION
        or guardrails.get("directOnly") is not True
        or guardrails.get("transitiveMerge") is not False
    ):
        raise IdentityOnlyCurationError(
            "Strict local identity review nie ma bezpiecznych guardrails dla decyzji człowieka"
        )
    entries = review.get("entries")
    matching_entries = (
        [entry for entry in entries if isinstance(entry, dict) and entry.get("candidateId") == candidate_id]
        if isinstance(entries, list)
        else []
    )
    if (
        len(matching_entries) != 1
        or matching_entries[0].get("adjudicatedRecommendation") != "same"
        or matching_entries[0].get("humanDecision") != "pending"
    ):
        raise IdentityOnlyCurationError(
            "Strict local identity review nie zawiera jednej decyzji same oczekującej na człowieka"
        )
    return {
        "path": path.relative_to(root.resolve()).as_posix(),
        "resolvedPath": path,
        "sha256": actual_sha,
        "review": review,
    }


def _human_review_packet(
    root: Path,
    *,
    records: list[tuple[Path, dict[str, Any], str]],
    path_text: Any,
    expected_sha256: Any,
    candidate_id: str,
    occurrence_ids: set[str],
) -> dict[str, Any]:
    path, relative, expected_sha = _relative_artifact(
        root,
        path_text,
        expected_sha256,
        label="humanReviewPacket",
    )
    packet = _load_json(path, "pakietu review człowieka")
    packet_version = packet.get("schemaVersion", packet.get("version"))
    if packet_version != 1:
        raise IdentityOnlyCurationError("Pakiet review człowieka musi mieć schemaVersion=1")
    packet_id = _text(packet.get("packetId"), "humanReviewPacket.packetId")
    if packet.get("candidateId") != candidate_id:
        raise IdentityOnlyCurationError("Pakiet review człowieka dotyczy innej kandydatury")
    if packet.get("status") != "awaiting_human_decision":
        raise IdentityOnlyCurationError(
            "Pakiet review człowieka musi mieć status=awaiting_human_decision"
        )
    allowed = packet.get("allowedDecisions")
    if (
        not isinstance(allowed, list)
        or not allowed
        or any(not isinstance(item, str) or not item.strip() for item in allowed)
        or len(allowed) != len(set(allowed))
        or "same" not in allowed
    ):
        raise IdentityOnlyCurationError(
            "Pakiet review człowieka musi jawnie dopuszczać decyzję same"
        )
    endpoints = packet.get("endpoints")
    packet_occurrences: list[str] = []
    endpoint_source_item_ids: dict[str, str] = {}
    if isinstance(endpoints, list):
        for index, item in enumerate(endpoints):
            if not isinstance(item, dict):
                raise IdentityOnlyCurationError(
                    "Pakiet review człowieka ma nieprawidłowy endpoint"
                )
            occurrence_id = _text(
                item.get("occurrenceId"),
                f"humanReviewPacket.endpoints[{index}].occurrenceId",
            )
            packet_occurrences.append(occurrence_id)
            endpoint_source_item_ids[occurrence_id] = _text(
                item.get("sourceItemId"),
                f"humanReviewPacket.endpoints[{index}].sourceItemId",
            )
    if (
        len(packet_occurrences) != 2
        or len(set(packet_occurrences)) != 2
        or set(packet_occurrences) != occurrence_ids
        or set(endpoint_source_item_ids) != occurrence_ids
    ):
        raise IdentityOnlyCurationError(
            "Pakiet review człowieka nie obejmuje dokładnie wskazanych endpoints"
        )
    packet_records = [
        item
        for item in records
        if item[1].get("packetId") is not None
    ]
    _reject_nonterminal_lineage(
        packet_records,
        identifier=packet_id,
        identifier_field="packetId",
        supersedes_fields=("supersedesPacketId", "supersedesReviewPacketId"),
        current_path=path,
        label="Pakiet review człowieka",
    )
    return {
        "path": relative,
        "resolvedPath": path,
        "sha256": expected_sha,
        "packetId": packet_id,
        "packet": packet,
        "endpointSourceItemIds": endpoint_source_item_ids,
    }


def _review_gate(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    reference = payload["humanReview"]
    decision_path, decision_relative, decision_sha = _relative_artifact(
        root,
        reference["artifactPath"],
        reference["expectedSha256"],
        label="humanReview",
    )
    decision = _strict_object(
        _load_json(decision_path, "lokalnej decyzji człowieka"),
        "local human decision",
        _HUMAN_DECISION_FIELDS,
    )
    if (
        decision["version"] != 1
        or decision["artifactType"] != "strict_local_identity_human_decision"
        or decision["decision"] != "approve_same"
        or decision["decidedByKind"] != "human"
        or decision["identityDecisionTier"] != IDENTITY_DECISION_TIER
    ):
        raise IdentityOnlyCurationError("Nieprawidłowy artefakt lokalnej decyzji człowieka")
    for field in (
        "reviewId",
        "sourceReviewId",
        "humanReviewPacketPath",
        "caseId",
        "candidateId",
        "decidedBy",
        "rationale",
    ):
        decision[field] = _text(decision[field], f"humanDecision.{field}")
    decision["decidedAt"] = _iso_timestamp(
        decision["decidedAt"], "humanDecision.decidedAt"
    )
    decision["sourceReviewSha256"] = _sha256(
        decision["sourceReviewSha256"], "humanDecision.sourceReviewSha256"
    )
    decision["humanReviewPacketSha256"] = _sha256(
        decision["humanReviewPacketSha256"],
        "humanDecision.humanReviewPacketSha256",
    )
    expected = {
        "reviewId": payload["reviewId"],
        "caseId": payload["caseId"],
        "candidateId": payload["candidate"]["id"],
        "decidedBy": payload["decidedBy"],
        "rationale": payload["rationale"],
    }
    if any(decision[field] != value for field, value in expected.items()):
        raise IdentityOnlyCurationError("Lokalna decyzja człowieka nie odpowiada manifestowi")

    records = _json_artifacts(root)
    terminal = _terminal_local_review(
        root,
        records=records,
        case_id=payload["caseId"],
        review_id=decision["sourceReviewId"],
        review_sha256=decision["sourceReviewSha256"],
        candidate_id=payload["candidate"]["id"],
    )
    packet = _human_review_packet(
        root,
        records=records,
        path_text=decision["humanReviewPacketPath"],
        expected_sha256=decision["humanReviewPacketSha256"],
        candidate_id=payload["candidate"]["id"],
        occurrence_ids={
            payload["local"]["occurrenceAId"],
            payload["local"]["occurrenceBId"],
        },
    )
    if len({decision_path, terminal["resolvedPath"], packet["resolvedPath"]}) != 3:
        raise IdentityOnlyCurationError(
            "Decyzja, strict review i pakiet człowieka muszą być rozłącznymi artefaktami"
        )
    return {
        "artifactPath": decision_relative,
        "artifactSha256": decision_sha,
        "decision": decision,
        "sourceReviewPath": terminal["path"],
        "sourceReviewSha256": terminal["sha256"],
        "humanReviewPacketPath": packet["path"],
        "humanReviewPacketSha256": packet["sha256"],
        "humanReviewPacketId": packet["packetId"],
        "humanReviewPacketEndpointSourceItemIds": packet[
            "endpointSourceItemIds"
        ],
    }


def _operation_state(
    connection, payload: dict[str, Any], review_gate: dict[str, Any]
) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    candidate_id = payload["candidate"]["id"]
    local = payload["local"]
    occurrence_ids = (local["occurrenceAId"], local["occurrenceBId"])
    person_ids = (local["expectedPersonAId"], local["expectedPersonBId"])
    occurrences = [_row(connection, "person_occurrences", item) for item in occurrence_ids]
    sources = [
        _row(connection, "source_items", row["source_item_id"])
        if row is not None
        else None
        for row in occurrences
    ]
    return {
        "case": dict(row) if (row := _row(connection, "research_cases", payload["caseId"])) else None,
        "candidate": dict(row) if (row := _row(connection, "identity_candidates", candidate_id)) else None,
        "candidateDecisions": _rows(
            connection,
            "SELECT * FROM identity_decisions WHERE candidate_id=? ORDER BY id",
            (candidate_id,),
        ),
        "occurrences": [dict(row) if row is not None else None for row in occurrences],
        "persons": [
            dict(row) if (row := _row(connection, "persons", person_id)) else None
            for person_id in person_ids
        ],
        "sources": [dict(row) if row is not None else None for row in sources],
        "privacy": _rows(
            connection,
            """SELECT * FROM privacy_labels
                 WHERE (object_type='person' AND object_id IN (?,?))
                    OR (object_type='person_occurrence' AND object_id IN (?,?))
                    OR (object_type='source_item' AND object_id IN (?,?))
                 ORDER BY object_type,object_id,id""",
            (
                *person_ids,
                *occurrence_ids,
                sources[0]["id"] if sources[0] is not None else "",
                sources[1]["id"] if sources[1] is not None else "",
            ),
        ),
        "dependencies": [
            _endpoint_dependencies(connection, person_id, occurrence_id)
            for person_id, occurrence_id in zip(person_ids, occurrence_ids)
        ],
        "sharedEndpointCandidates": _rows(
            connection,
            """SELECT * FROM identity_candidates
                 WHERE id<>? AND (occurrence_a_id IN (?,?) OR occurrence_b_id IN (?,?))
                 ORDER BY id""",
            (candidate_id, *occurrence_ids, *occurrence_ids),
        ),
        "reviewGate": {
            "artifactPath": review_gate["artifactPath"],
            "artifactSha256": review_gate["artifactSha256"],
            "sourceReviewPath": review_gate["sourceReviewPath"],
            "sourceReviewSha256": review_gate["sourceReviewSha256"],
            "humanReviewPacketPath": review_gate["humanReviewPacketPath"],
            "humanReviewPacketSha256": review_gate["humanReviewPacketSha256"],
            "humanReviewPacketId": review_gate["humanReviewPacketId"],
            "humanReviewPacketEndpointSourceItemIds": review_gate[
                "humanReviewPacketEndpointSourceItemIds"
            ],
            "decidedAt": review_gate["decision"]["decidedAt"],
        },
    }


def _inspect(
    connection, payload: dict[str, Any], review_gate: dict[str, Any]
) -> dict[str, Any]:  # type: ignore[no-untyped-def]
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
        zip(
            state["occurrences"],
            (local["expectedPersonAId"], local["expectedPersonBId"]),
        )
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
        packet_source_item_id = review_gate[
            "humanReviewPacketEndpointSourceItemIds"
        ].get(local[f"occurrence{label.upper()}Id"])
        if (
            packet_source_item_id is not None
            and state["sources"][index] is not None
            and packet_source_item_id != state["sources"][index]["id"]
        ):
            blockers.append(f"human_review_packet_source_{label}_mismatch")
    endpoints = [
        {
            "occurrenceId": local["occurrenceAId"],
            "personId": local["expectedPersonAId"],
            "sourceItemId": (
                state["sources"][0]["id"] if state["sources"][0] is not None else None
            ),
        },
        {
            "occurrenceId": local["occurrenceBId"],
            "personId": local["expectedPersonBId"],
            "sourceItemId": (
                state["sources"][1]["id"] if state["sources"][1] is not None else None
            ),
        },
    ]
    write_set = [
        {
            "table": "identity_candidates",
            "id": payload["candidate"]["id"],
            "action": "update_status",
        },
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
        "decidedByKind": "human",
        "identityDecisionTier": IDENTITY_DECISION_TIER,
        "occurrenceIds": [local["occurrenceAId"], local["occurrenceBId"]],
        "personIds": [local["expectedPersonAId"], local["expectedPersonBId"]],
        "endpoints": endpoints,
        "fingerprint": fingerprint(state),
        "commandIdRequired": True,
        "applyAllowed": not blockers,
        "blockers": blockers,
        "writeSet": write_set,
        "humanReviewArtifactPath": review_gate["artifactPath"],
        "humanReviewSha256": review_gate["artifactSha256"],
        "sourceReviewPath": review_gate["sourceReviewPath"],
        "sourceReviewSha256": review_gate["sourceReviewSha256"],
        "humanReviewPacketPath": review_gate["humanReviewPacketPath"],
        "humanReviewPacketSha256": review_gate["humanReviewPacketSha256"],
    }


def identity_only_local_human_candidate_resolution_preview(
    root: Path, manifest: Path
) -> dict[str, Any]:
    payload = _load_manifest_payload(manifest)
    review_gate = _review_gate(root, payload)
    with _current_connection(root, readonly=True) as connection:
        require_tables(connection, REQUIRED_TABLES)
        return _inspect(connection, payload, review_gate)


def apply_identity_only_local_human_candidate_resolution(
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

        review_gate = _review_gate(root, payload)
        preview = _inspect(connection, payload, review_gate)
        if preview["fingerprint"] != expected_fingerprint:
            raise IdentityOnlyCurationConflict(
                "Stan lokalnej decyzji człowieka identity-only zmienił się po preview"
            )
        if not preview["applyAllowed"]:
            raise IdentityOnlyCurationError(
                "Lokalna decyzja człowieka identity-only jest zablokowana: "
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
            "decidedByKind": "human",
            "identityDecisionTier": IDENTITY_DECISION_TIER,
            "decisionId": decision_id,
            "commandId": command_id,
            "inputFingerprint": expected_fingerprint,
            "manifestFingerprint": manifest_fingerprint,
            "occurrenceIds": preview["occurrenceIds"],
            "personIds": preview["personIds"],
            "endpoints": preview["endpoints"],
            "humanReviewArtifactPath": review_gate["artifactPath"],
            "humanReviewSha256": review_gate["artifactSha256"],
            "sourceReviewPath": review_gate["sourceReviewPath"],
            "sourceReviewSha256": review_gate["sourceReviewSha256"],
            "humanReviewPacketPath": review_gate["humanReviewPacketPath"],
            "humanReviewPacketSha256": review_gate["humanReviewPacketSha256"],
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
                json.dumps(
                    {"candidateStatus": "unresolved", "decisionCount": 0},
                    sort_keys=True,
                ),
                json.dumps(result, ensure_ascii=False, sort_keys=True),
                payload["rationale"],
            ),
        )
        return {**result, "alreadyApplied": False}


__all__ = [
    "IdentityOnlyCurationConflict",
    "IdentityOnlyCurationError",
    "apply_identity_only_local_human_candidate_resolution",
    "identity_only_local_human_candidate_resolution_preview",
]
