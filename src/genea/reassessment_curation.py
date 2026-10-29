from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .command_envelope import (
    CommandEnvelopeConflict,
    canonical_json,
    fingerprint,
    record_command,
    replay_command,
)
from .db import connect, initialize
from .domain_completion import (
    FULL_ACT,
    NO_DOMAIN_WRITEBACK_EXPECTED,
    normalize_domain_completion,
)
from .research import require_tables
from .workspace import stable_id, utc_now


REQUIRED_TABLES = {
    "audit_log",
    "curation_commands",
    "reassessment_batches",
    "reassessment_batch_states",
    "reassessment_triggers",
    "reassessment_items",
    "reassessment_item_results",
    "reassessment_impacts",
    "reassessment_reviews",
    "mapping_writeback_manifests",
    "mapping_writeback_runs",
    "research_cases",
    "research_questions",
    "source_items",
    "source_structure_manifests",
    "source_structure_runs",
}
STATE_STATUSES = {"planned", "in_review", "waiting", "completed", "inconclusive", "superseded"}
RESULT_STATUSES = {"pending", "in_review", "completed", "inconclusive", "blocked", "deferred", "not_affected"}
WRITEBACK_STATUSES = {"planned", "ready", "applied", "deferred", "not_affected", "blocked", "conflict"}


class ReassessmentCurationError(ValueError):
    pass


class ReassessmentCurationConflict(ReassessmentCurationError):
    pass


def _canonical(value: Any) -> str:
    return canonical_json(value)


def _fingerprint(value: Any) -> str:
    return fingerprint(value)


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReassessmentCurationError(f"Pole {label} musi być niepustym tekstem")
    return value.strip()


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
        raise ReassessmentCurationError("Manifest reassessmentu musi być obiektem schemaVersion=1")
    _text(payload.get("actor"), "actor")
    _text(payload.get("rationale"), "rationale")
    _text(payload.get("commandId"), "commandId")
    declared = payload.get("manifestFingerprint")
    fingerprint_payload = dict(payload)
    fingerprint_payload.pop("manifestFingerprint", None)
    calculated = _fingerprint(fingerprint_payload)
    if declared is not None and declared != calculated:
        raise ReassessmentCurationError("manifestFingerprint nie odpowiada treści manifestu")
    payload["manifestFingerprint"] = calculated
    return payload


def _exists(connection, table: str, object_id: str) -> bool:  # type: ignore[no-untyped-def]
    return connection.execute(f"SELECT 1 FROM {table} WHERE id=?", (object_id,)).fetchone() is not None


def _rows(connection, query: str, parameters: tuple[Any, ...]) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
    return [dict(row) for row in connection.execute(query, parameters)]


def _source_state(connection, source_item_id: str) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    source = connection.execute(
        "SELECT * FROM source_items WHERE id=?", (source_item_id,)
    ).fetchone()
    reading_rows = _rows(
        connection,
        "SELECT * FROM readings WHERE source_item_id=? ORDER BY id",
        (source_item_id,),
    )
    reading_ids = [str(row["id"]) for row in reading_rows]
    version_rows: list[dict[str, Any]] = []
    if reading_ids:
        placeholders = ",".join("?" for _ in reading_ids)
        version_rows = _rows(
            connection,
            f"SELECT * FROM reading_versions WHERE reading_id IN ({placeholders}) ORDER BY id",
            tuple(reading_ids),
        )
    version_ids = [str(row["id"]) for row in version_rows]
    dependent: dict[str, list[dict[str, Any]]] = {}
    if version_ids:
        placeholders = ",".join("?" for _ in version_ids)
        for label, table, column in (
            ("segments", "transcript_segments", "reading_version_id"),
            ("fieldReadings", "field_readings", "reading_version_id"),
            ("occurrences", "person_occurrences", "reading_version_id"),
            ("assertions", "source_assertions", "reading_version_id"),
        ):
            dependent[label] = _rows(
                connection,
                f"SELECT * FROM {table} WHERE {column} IN ({placeholders}) ORDER BY id",
                tuple(version_ids),
            )
    return {
        "source": dict(source) if source else None,
        "artifacts": _rows(
            connection,
            "SELECT * FROM artifacts WHERE source_item_id=? ORDER BY id",
            (source_item_id,),
        ),
        "citations": _rows(
            connection,
            "SELECT * FROM citations WHERE source_item_id=? ORDER BY id",
            (source_item_id,),
        ),
        "readings": reading_rows,
        "readingVersions": version_rows,
        "events": _rows(
            connection,
            "SELECT * FROM events WHERE source_item_id=? ORDER BY id",
            (source_item_id,),
        ),
        "sourceOccurrences": _rows(
            connection,
            "SELECT * FROM person_occurrences WHERE source_item_id=? ORDER BY id",
            (source_item_id,),
        ),
        "sourceAssertions": _rows(
            connection,
            "SELECT * FROM source_assertions WHERE source_item_id=? ORDER BY id",
            (source_item_id,),
        ),
        **dependent,
    }


def _dependency_state(connection, payload: dict[str, Any]) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    batch = payload["batch"]
    source_ids = {
        str(item)
        for item in (batch.get("scope") or {}).get("sourceItemIds", [])
        if item
    }
    for group_name in ("triggers", "items", "impacts"):
        source_ids.update(
            str(item["sourceItemId"])
            for item in payload.get(group_name, [])
            if item.get("sourceItemId")
        )
    case_id = batch.get("caseId")
    question_id = batch.get("questionId")
    return {
        "case": dict(connection.execute("SELECT * FROM research_cases WHERE id=?", (case_id,)).fetchone())
        if case_id and _exists(connection, "research_cases", str(case_id))
        else None,
        "question": dict(connection.execute("SELECT * FROM research_questions WHERE id=?", (question_id,)).fetchone())
        if question_id and _exists(connection, "research_questions", str(question_id))
        else None,
        "sources": {
            source_id: _source_state(connection, source_id)
            for source_id in sorted(source_ids)
        },
    }


def _reassessment_domain_completion(
    connection: Any,
    scope: dict[str, Any],
) -> dict[str, Any] | None:
    declaration = scope.get("domainCompletion")
    if declaration is None:
        return None
    if not isinstance(declaration, dict):
        raise ReassessmentCurationError("batch.scope.domainCompletion musi być obiektem")
    mode = declaration.get("mode")
    if mode == NO_DOMAIN_WRITEBACK_EXPECTED:
        try:
            normalized = normalize_domain_completion(
                declaration, label="batch.scope.domainCompletion"
            )
        except ValueError as error:
            raise ReassessmentCurationError(str(error)) from error
        return {
            "state": NO_DOMAIN_WRITEBACK_EXPECTED,
            "mode": mode,
            "scopeKind": normalized["scopeKind"],
            "sourceItemId": declaration.get("sourceItemId"),
            "writebackLink": None,
            "gaps": [],
        }
    if mode != FULL_ACT:
        raise ReassessmentCurationError(
            "batch.scope.domainCompletion.mode musi być full_act albo "
            "no_domain_writeback_expected"
        )
    source_item_id = _text(
        declaration.get("sourceItemId"), "batch.scope.domainCompletion.sourceItemId"
    )
    rationale = _text(
        declaration.get("rationale"), "batch.scope.domainCompletion.rationale"
    )
    link = declaration.get("writebackLink")
    if not isinstance(link, dict):
        return {
            "state": "incomplete",
            "mode": mode,
            "sourceItemId": source_item_id,
            "rationale": rationale,
            "writebackLink": None,
            "gaps": [{"reason": "missing_writeback_link"}],
        }
    kind = link.get("kind")
    run_id = link.get("runId")
    manifest_fingerprint = link.get("manifestFingerprint")
    domain_fingerprint = link.get("domainCompletionFingerprint")
    missing_link_fields = [
        field
        for field, value in (
            ("kind", kind),
            ("runId", run_id),
            ("manifestFingerprint", manifest_fingerprint),
            ("domainCompletionFingerprint", domain_fingerprint),
        )
        if not isinstance(value, str) or not value.strip()
    ]
    if missing_link_fields:
        return {
            "state": "incomplete",
            "mode": mode,
            "sourceItemId": source_item_id,
            "rationale": rationale,
            "writebackLink": link,
            "gaps": [
                {
                    "reason": "incomplete_writeback_link",
                    "missingFields": missing_link_fields,
                }
            ],
        }
    if kind == "mapping_writeback":
        row = connection.execute(
            """SELECT r.id,r.status,r.diff_json AS gate_json,m.source_item_id,
                      m.manifest_fingerprint,m.manifest_json
                 FROM mapping_writeback_runs r
                 JOIN mapping_writeback_manifests m ON m.id=r.manifest_id
                WHERE r.id=?""",
            (run_id,),
        ).fetchone()
        declaration_path = "extractionState"
    elif kind == "source_structure":
        row = connection.execute(
            """SELECT r.id,r.status,r.summary_json AS gate_json,m.manifest_fingerprint,
                      m.manifest_json,NULL AS source_item_id
                 FROM source_structure_runs r
                 JOIN source_structure_manifests m ON m.id=r.manifest_id
                WHERE r.id=?""",
            (run_id,),
        ).fetchone()
        declaration_path = "extractionStates"
    else:
        return {
            "state": "incomplete",
            "mode": mode,
            "sourceItemId": source_item_id,
            "rationale": rationale,
            "writebackLink": link,
            "gaps": [{"reason": "unsupported_writeback_kind", "kind": kind}],
        }
    if row is None:
        return {
            "state": "incomplete",
            "mode": mode,
            "sourceItemId": source_item_id,
            "rationale": rationale,
            "writebackLink": link,
            "gaps": [{"reason": "missing_writeback_run", "runId": run_id}],
        }
    gaps: list[dict[str, Any]] = []
    if row["status"] != "applied":
        gaps.append(
            {"reason": "writeback_not_applied", "runId": run_id, "status": row["status"]}
        )
    if row["manifest_fingerprint"] != manifest_fingerprint:
        gaps.append(
            {
                "reason": "writeback_manifest_fingerprint_mismatch",
                "declared": manifest_fingerprint,
                "actual": row["manifest_fingerprint"],
            }
        )
    if kind == "mapping_writeback" and row["source_item_id"] != source_item_id:
        gaps.append(
            {
                "reason": "writeback_source_mismatch",
                "declared": source_item_id,
                "actual": row["source_item_id"],
            }
        )
    try:
        manifest = json.loads(row["manifest_json"])
        if declaration_path == "extractionState":
            frozen = manifest.get("extractionState", {}).get("domainCompletion")
        else:
            matches = [
                item.get("domainCompletion")
                for item in manifest.get("extractionStates", [])
                if item.get("sourceItemId") == source_item_id
            ]
            frozen = matches[0] if len(matches) == 1 else None
        normalized = normalize_domain_completion(frozen)
        actual_domain_fingerprint = normalized.get("acceptedNamedPeopleFingerprint")
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        gaps.append(
            {
                "reason": "invalid_linked_domain_completion",
                "detail": str(error),
            }
        )
        actual_domain_fingerprint = None
    try:
        gate_payload = json.loads(row["gate_json"])
        if kind == "mapping_writeback":
            applied_gate = gate_payload.get("domainCompletion")
        else:
            reports = gate_payload.get("domainCompletion", [])
            applied_gate = next(
                (
                    report
                    for report in reports
                    if report.get("sourceItemId") == source_item_id
                ),
                None,
            )
        if (
            not isinstance(applied_gate, dict)
            or applied_gate.get("state") != "complete"
            or applied_gate.get("acceptedNamedPeopleFingerprint")
            != domain_fingerprint
        ):
            gaps.append(
                {
                    "reason": "linked_writeback_gate_not_complete",
                    "gate": applied_gate,
                }
            )
    except (TypeError, json.JSONDecodeError) as error:
        gaps.append(
            {"reason": "invalid_linked_writeback_gate", "detail": str(error)}
        )
    if actual_domain_fingerprint != domain_fingerprint:
        gaps.append(
            {
                "reason": "domain_completion_fingerprint_mismatch",
                "declared": domain_fingerprint,
                "actual": actual_domain_fingerprint,
            }
        )
    return {
        "state": "complete" if not gaps else "incomplete",
        "mode": mode,
        "sourceItemId": source_item_id,
        "rationale": rationale,
        "writebackLink": link,
        "gaps": gaps,
    }
def _validate(connection, payload: dict[str, Any]) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    batch = payload.get("batch")
    state = payload.get("state")
    if not isinstance(batch, dict) or not isinstance(state, dict):
        raise ReassessmentCurationError("Manifest wymaga obiektów batch i state")
    batch_id = _text(batch.get("id"), "batch.id")
    batch_key = _text(batch.get("batchKey"), "batch.batchKey")
    input_fingerprint = _text(batch.get("inputFingerprint"), "batch.inputFingerprint")
    stopping_rule = _text(batch.get("stoppingRule"), "batch.stoppingRule")
    created_by = _text(batch.get("createdBy"), "batch.createdBy")
    status = _text(state.get("status"), "state.status")
    if status not in STATE_STATUSES:
        raise ReassessmentCurationError("Nieprawidłowy status batcha")
    _text(state.get("rationale"), "state.rationale")
    scope = batch.get("scope", {})
    if not isinstance(scope, dict):
        raise ReassessmentCurationError("batch.scope musi być obiektem")

    case_id = batch.get("caseId")
    question_id = batch.get("questionId")
    conflicts: list[dict[str, Any]] = []
    if case_id and not _exists(connection, "research_cases", str(case_id)):
        conflicts.append({"reason": "missing_case", "id": case_id})
    if question_id and not _exists(connection, "research_questions", str(question_id)):
        conflicts.append({"reason": "missing_question", "id": question_id})
    domain_completion = _reassessment_domain_completion(connection, scope)
    declares_full_act = scope.get("fullAct") is True or (
        domain_completion is not None and domain_completion.get("mode") == FULL_ACT
    )
    if scope.get("fullAct") is True and domain_completion is None:
        conflicts.append({"reason": "domain_completion_declaration_required"})
    if (
        scope.get("fullAct") is True
        and domain_completion is not None
        and domain_completion.get("mode") == NO_DOMAIN_WRITEBACK_EXPECTED
    ):
        conflicts.append(
            {"reason": "full_act_cannot_use_no_domain_writeback_expected"}
        )
    if (
        status == "completed"
        and declares_full_act
        and (
            domain_completion is None
            or domain_completion.get("state") != "complete"
        )
    ):
        conflicts.append(
            {
                "reason": "completed_full_act_requires_applied_writeback",
                "domainCompletion": domain_completion,
            }
        )

    dependency_state = _dependency_state(connection, payload)
    current_input_fingerprint = _fingerprint(dependency_state)
    if input_fingerprint != current_input_fingerprint:
        conflicts.append(
            {
                "reason": "batch_input_drift",
                "declared": input_fingerprint,
                "current": current_input_fingerprint,
            }
        )

    existing = connection.execute(
        "SELECT batch_key,input_fingerprint FROM reassessment_batches WHERE id=?",
        (batch_id,),
    ).fetchone()
    if existing:
        if existing["batch_key"] == batch_key and existing["input_fingerprint"] == input_fingerprint:
            return {
                "batchId": batch_id,
                "status": "no_op",
                "conflicts": [],
                "currentInputFingerprint": current_input_fingerprint,
                "currentItemFingerprints": {},
                "dependencyFingerprint": _fingerprint(dependency_state),
                "domainCompletion": domain_completion,
            }
        conflicts.append({"reason": "batch_id_conflict", "id": batch_id})

    for group_name in ("triggers", "items", "impacts"):
        entries = payload.get(group_name, [])
        if not isinstance(entries, list) or not all(isinstance(item, dict) for item in entries):
            raise ReassessmentCurationError(f"{group_name} musi być listą obiektów")
        for entry in entries:
            source_item_id = entry.get("sourceItemId")
            if source_item_id and not _exists(connection, "source_items", str(source_item_id)):
                conflicts.append({"reason": "missing_source_item", "id": source_item_id})
    item_fingerprints: dict[str, str] = {}
    for item in payload.get("items", []):
        if item.get("riskLevel") not in {"R0", "R1", "R2", "R3", "R4"}:
            raise ReassessmentCurationError("Nieprawidłowy riskLevel reassessment item")
        if item.get("costClass") not in {"A", "B", "C", "D"}:
            raise ReassessmentCurationError("Nieprawidłowy costClass reassessment item")
        result = item.get("result")
        if not isinstance(result, dict) or result.get("status") not in RESULT_STATUSES:
            raise ReassessmentCurationError("Każdy item wymaga prawidłowego result")
        if result.get("status") in {"blocked", "deferred"}:
            result_payload = result.get("result")
            if not isinstance(result_payload, dict) or not str(
                result_payload.get("nextAction") or ""
            ).strip():
                raise ReassessmentCurationError(
                    "Wynik blocked/deferred wymaga jawnego result.nextAction"
                )
        item_key = f"{item.get('objectType')}:{item.get('objectId')}"
        source_item_id = item.get("sourceItemId")
        current_item_fingerprint = _fingerprint(
            _source_state(connection, str(source_item_id))
            if source_item_id
            else {
                "objectType": item.get("objectType"),
                "objectId": item.get("objectId"),
            }
        )
        item_fingerprints[item_key] = current_item_fingerprint
        if item.get("inputFingerprint") != current_item_fingerprint:
            conflicts.append(
                {
                    "reason": "item_input_drift",
                    "id": item_key,
                    "declared": item.get("inputFingerprint"),
                    "current": current_item_fingerprint,
                }
            )
    for impact in payload.get("impacts", []):
        if impact.get("writebackStatus") not in WRITEBACK_STATUSES:
            raise ReassessmentCurationError("Nieprawidłowy writebackStatus reassessment impact")
        if (
            domain_completion is not None
            and domain_completion.get("mode") == NO_DOMAIN_WRITEBACK_EXPECTED
            and impact.get("writebackStatus") in {"planned", "ready", "applied"}
        ):
            conflicts.append(
                {
                    "reason": "no_domain_writeback_expected_has_domain_impact",
                    "objectType": impact.get("objectType"),
                    "objectId": impact.get("objectId"),
                    "writebackStatus": impact.get("writebackStatus"),
                }
            )
    for review in payload.get("reviews", []):
        if review.get("reviewerKind") not in {"agent", "human"}:
            raise ReassessmentCurationError("Nieprawidłowy reviewerKind")
        if review.get("decision") not in {"approved", "changes_required", "inconclusive"}:
            raise ReassessmentCurationError("Nieprawidłowa decyzja review")
    return {
        "batchId": batch_id,
        "status": "conflict" if conflicts else "ready",
        "conflicts": conflicts,
        "currentInputFingerprint": current_input_fingerprint,
        "currentItemFingerprints": item_fingerprints,
        "dependencyFingerprint": _fingerprint(dependency_state),
        "domainCompletion": domain_completion,
    }


def reassessment_writeback_preview(root: Path, manifest_path: Path) -> dict[str, Any]:
    payload = _load(manifest_path)
    initialize(root)
    with connect(root) as connection:
        require_tables(connection, REQUIRED_TABLES)
        connection.execute("PRAGMA query_only=ON")
        preview = _validate(connection, payload)
    accepted_fingerprint = _fingerprint(
        {
            "manifest": payload["manifestFingerprint"],
            "dependencies": preview["dependencyFingerprint"],
        }
    )
    return {
        "mode": "preview",
        "manifestFingerprint": payload["manifestFingerprint"],
        "fingerprint": accepted_fingerprint,
        **preview,
    }


def apply_reassessment_writeback(
    root: Path,
    manifest_path: Path,
    *,
    expected_fingerprint: str,
) -> dict[str, Any]:
    payload = _load(manifest_path)
    initialize(root)
    now = utc_now()
    actor = _text(payload.get("actor"), "actor")
    rationale = _text(payload.get("rationale"), "rationale")
    command_id = _text(payload.get("commandId"), "commandId")
    expected_fingerprint = _text(expected_fingerprint, "expectedFingerprint")
    with connect(root) as connection:
        require_tables(connection, REQUIRED_TABLES)
        connection.execute("BEGIN IMMEDIATE")
        request_fingerprint = _fingerprint(
            {
                "manifestFingerprint": payload["manifestFingerprint"],
                "expectedFingerprint": expected_fingerprint,
                "actor": actor,
                "rationale": rationale,
            }
        )
        try:
            replay = replay_command(
                connection,
                command_id=command_id,
                command_type="reassessment_writeback",
                object_type="reassessment_batch",
                object_id=str(payload["batch"]["id"]),
                request_fingerprint=request_fingerprint,
            )
        except CommandEnvelopeConflict as error:
            raise ReassessmentCurationConflict(str(error)) from error
        if replay is not None:
            return {**replay, "mode": "apply", "status": "no_op"}
        preview = _validate(connection, payload)
        current_fingerprint = _fingerprint(
            {
                "manifest": payload["manifestFingerprint"],
                "dependencies": preview["dependencyFingerprint"],
            }
        )
        if current_fingerprint != expected_fingerprint:
            raise ReassessmentCurationConflict(
                "Manifest lub zależne źródła zmieniły się po podglądzie"
            )
        if preview["status"] == "no_op":
            return {"mode": "apply", "status": "no_op", **preview}
        if preview["conflicts"]:
            raise ReassessmentCurationConflict("Manifest reassessmentu ma konflikty")

        batch = payload["batch"]
        state = payload["state"]
        batch_id = batch["id"]
        connection.execute(
            """INSERT INTO reassessment_batches(
                   id,batch_key,case_id,question_id,input_fingerprint,scope_json,
                   stopping_rule,supersedes_batch_id,created_by,created_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                batch_id, batch["batchKey"], batch.get("caseId"), batch.get("questionId"),
                batch["inputFingerprint"], _canonical(batch.get("scope", {})),
                batch["stoppingRule"], batch.get("supersedesBatchId"),
                batch["createdBy"], now,
            ),
        )
        connection.execute(
            """INSERT INTO reassessment_batch_states(
                   id,batch_id,revision,status,rationale,created_by,created_at
               ) VALUES (?,?,1,?,?,?,?)""",
            (
                stable_id("reassessment_state", batch_id, "1"), batch_id,
                state["status"], state["rationale"], actor, now,
            ),
        )
        for entry in payload.get("triggers", []):
            trigger_id = stable_id(
                "reassessment_trigger", batch_id, entry["triggerType"],
                entry["objectType"], entry["objectId"],
            )
            connection.execute(
                """INSERT INTO reassessment_triggers(
                       id,batch_id,trigger_type,object_type,object_id,source_item_id,
                       priority,rationale,metadata_json,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    trigger_id, batch_id, entry["triggerType"], entry["objectType"],
                    entry["objectId"], entry.get("sourceItemId"), int(entry.get("priority", 0)),
                    entry["rationale"], _canonical(entry.get("metadata", {})), now,
                ),
            )
        for entry in payload.get("items", []):
            item_id = stable_id(
                "reassessment_item", batch_id, entry["objectType"],
                entry["objectId"], entry["inputFingerprint"],
            )
            connection.execute(
                """INSERT INTO reassessment_items(
                       id,batch_id,item_type,object_type,object_id,source_item_id,
                       inclusion_reason,risk_level,cost_class,input_fingerprint,
                       metadata_json,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    item_id, batch_id, entry["itemType"], entry["objectType"],
                    entry["objectId"], entry.get("sourceItemId"), entry["inclusionReason"],
                    entry["riskLevel"], entry["costClass"], entry["inputFingerprint"],
                    _canonical(entry.get("metadata", {})), now,
                ),
            )
            result = entry["result"]
            connection.execute(
                """INSERT INTO reassessment_item_results(
                       id,item_id,revision,status,result_json,rationale,created_by,created_at
                   ) VALUES (?,?,1,?,?,?,?,?)""",
                (
                    stable_id("reassessment_result", item_id, "1"), item_id,
                    result["status"], _canonical(result.get("result", {})),
                    result["rationale"], actor, now,
                ),
            )
        for entry in payload.get("impacts", []):
            impact_id = stable_id(
                "reassessment_impact", batch_id, entry["objectType"],
                entry["objectId"], entry["impactType"],
            )
            connection.execute(
                """INSERT INTO reassessment_impacts(
                       id,batch_id,revision,object_type,object_id,impact_type,
                       planned_action,writeback_status,preview_json,rationale,
                       created_by,created_at
                   ) VALUES (?,?,1,?,?,?,?,?,?,?,?,?)""",
                (
                    impact_id, batch_id, entry["objectType"], entry["objectId"],
                    entry["impactType"], entry["plannedAction"], entry["writebackStatus"],
                    _canonical(entry.get("preview", {})), entry["rationale"], actor, now,
                ),
            )
        for entry in payload.get("reviews", []):
            review_id = stable_id(
                "reassessment_review", batch_id, entry["reviewKind"],
                entry["reviewer"], entry["decision"],
            )
            connection.execute(
                """INSERT INTO reassessment_reviews(
                       id,batch_id,review_kind,reviewer,reviewer_kind,decision,
                       notes,metadata_json,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    review_id, batch_id, entry["reviewKind"], entry["reviewer"],
                    entry["reviewerKind"], entry["decision"], entry["notes"],
                    _canonical(entry.get("metadata", {})), now,
                ),
            )
        result = {
            "batchId": batch_id,
            "manifestFingerprint": payload["manifestFingerprint"],
            "commandId": command_id,
            "inputFingerprint": expected_fingerprint,
            "projectionStatus": "pending",
            "state": state["status"],
            "domainCompletion": preview.get("domainCompletion"),
            "counts": {
                "triggers": len(payload.get("triggers", [])),
                "items": len(payload.get("items", [])),
                "impacts": len(payload.get("impacts", [])),
                "reviews": len(payload.get("reviews", [])),
            },
        }
        record_command(
            connection,
            command_id=command_id,
            command_type="reassessment_writeback",
            object_type="reassessment_batch",
            object_id=batch_id,
            input_fingerprint=expected_fingerprint,
            request_fingerprint=request_fingerprint,
            actor=actor,
            rationale=rationale,
            result=result,
        )
        cursor = connection.execute(
            """INSERT INTO audit_log(
                   occurred_at,actor,action,object_type,object_id,before_json,after_json,rationale
               ) VALUES (?,?,'reassessment_writeback_apply','reassessment_batch',?,NULL,?,?)""",
            (now, actor, batch_id, _canonical(result), rationale),
        )
        connection.commit()
        return {"mode": "apply", "status": "applied", "auditId": int(cursor.lastrowid), **result}


__all__ = [
    "ReassessmentCurationConflict",
    "ReassessmentCurationError",
    "apply_reassessment_writeback",
    "reassessment_writeback_preview",
]
