from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .db import connect, initialize
from .domain_completion import (
    FULL_ACT,
    assess_domain_completion,
    gap_conflicts,
)
from .research import require_tables
from .workspace import stable_id, utc_now


REQUIRED_TABLES = {
    "audit_log",
    "document_extraction_states",
    "events",
    "field_readings",
    "participations",
    "person_occurrences",
    "readings",
    "reading_versions",
    "reading_review_decisions",
    "source_assertions",
    "source_coverage_assessments",
    "source_items",
    "source_structure_manifests",
    "source_structure_runs",
    "transcript_segments",
}

ITEM_FIELDS = {
    "collection_id": "collectionId",
    "parent_id": "parentId",
    "title": "title",
    "item_type": "itemType",
    "date_text": "dateText",
    "locator": "locator",
    "source_url": "sourceUrl",
    "citation_text": "citationText",
    "source_quality": "sourceQuality",
    "verification_status": "verificationStatus",
}

MOVE_TABLES = {
    "event": "events",
    "occurrence": "person_occurrences",
    "assertion": "source_assertions",
}

COVERAGE_DIMENSIONS = {
    "inventory",
    "index",
    "content_review",
    "extraction",
    "transcription",
    "review",
}
COVERAGE_STATUSES = {
    "unknown",
    "not_started",
    "partial",
    "complete",
    "blocked",
    "not_applicable",
}
UNIT_KINDS = {"scan", "page", "folio", "act", "entry", "field", "household", "custom"}
QA_STATUSES = {"unreviewed", "agent_reviewed", "human_reviewed", "not_applicable"}
BASIS_TYPES = {
    "manifest",
    "index",
    "browse_session",
    "source_children",
    "curator_assessment",
    "derived",
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Manifest hierarchii źródeł musi być obiektem JSON")
    if payload.get("schemaVersion") != 1:
        raise ValueError("Obsługiwana jest tylko wersja 1 manifestu hierarchii źródeł")
    actor = str(payload.get("actor") or "").strip()
    rationale = str(payload.get("rationale") or "").strip()
    if not actor or not rationale:
        raise ValueError("Manifest wymaga actor i rationale")
    declared = payload.get("manifestFingerprint")
    fingerprint_payload = dict(payload)
    fingerprint_payload.pop("manifestFingerprint", None)
    calculated = _fingerprint(fingerprint_payload)
    if declared is not None and declared != calculated:
        raise ValueError("manifestFingerprint nie odpowiada treści manifestu")
    payload["manifestFingerprint"] = calculated
    return payload


def _row(connection, table: str, object_id: str) -> dict[str, Any] | None:
    result = connection.execute(f"SELECT * FROM {table} WHERE id=?", (object_id,)).fetchone()
    return dict(result) if result else None


def _json_object(value: str | None) -> dict[str, Any]:
    try:
        decoded = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _item_target(entry: dict[str, Any], current: dict[str, Any] | None) -> dict[str, Any]:
    target: dict[str, Any] = {}
    for column, manifest_key in ITEM_FIELDS.items():
        if manifest_key in entry:
            target[column] = entry[manifest_key]
    if "metadataPatch" in entry:
        patch = entry["metadataPatch"]
        if not isinstance(patch, dict):
            raise ValueError("metadataPatch musi być obiektem")
        metadata = _json_object(current.get("metadata_json") if current else None)
        metadata.update(patch)
        target["metadata_json"] = _canonical(metadata)
    return target


def _validate_coverage(entry: dict[str, Any]) -> None:
    for key in ("sourceItemId", "coverageKey", "dimension", "coverageLevel", "scopeText", "unitKind", "status", "basisType", "rationale"):
        if not str(entry.get(key) or "").strip():
            raise ValueError(f"Coverage wymaga pola {key}")
    if entry["dimension"] not in COVERAGE_DIMENSIONS:
        raise ValueError(f"Nieznany wymiar coverage: {entry['dimension']}")
    if entry["status"] not in COVERAGE_STATUSES:
        raise ValueError(f"Nieznany status coverage: {entry['status']}")
    if entry["unitKind"] not in UNIT_KINDS:
        raise ValueError(f"Nieznany unitKind coverage: {entry['unitKind']}")
    if entry.get("qaStatus", "unreviewed") not in QA_STATUSES:
        raise ValueError(f"Nieznany qaStatus coverage: {entry.get('qaStatus')}")
    if entry["basisType"] not in BASIS_TYPES:
        raise ValueError(f"Nieznany basisType coverage: {entry['basisType']}")
    completed = entry.get("completedUnits")
    total = entry.get("totalUnits")
    if completed is not None and (not isinstance(completed, int) or completed < 0):
        raise ValueError("completedUnits musi być nieujemną liczbą całkowitą albo null")
    if total is not None and (not isinstance(total, int) or total < 0):
        raise ValueError("totalUnits musi być nieujemną liczbą całkowitą albo null")
    if completed is not None and total is not None and completed > total:
        raise ValueError("completedUnits nie może przekraczać totalUnits")


def _coverage_target(entry: dict[str, Any]) -> dict[str, Any]:
    _validate_coverage(entry)
    return {
        "source_item_id": entry["sourceItemId"],
        "coverage_key": entry["coverageKey"],
        "dimension": entry["dimension"],
        "coverage_level": entry["coverageLevel"],
        "record_type": entry.get("recordType"),
        "scope_text": entry["scopeText"],
        "unit_kind": entry["unitKind"],
        "completed_units": entry.get("completedUnits"),
        "total_units": entry.get("totalUnits"),
        "status": entry["status"],
        "systematic": int(bool(entry.get("systematic", False))),
        "qa_status": entry.get("qaStatus", "unreviewed"),
        "basis_type": entry["basisType"],
        "basis_ref": entry.get("basisRef"),
        "metadata_json": _canonical(entry.get("metadata", {})),
        "rationale": entry["rationale"],
        "created_by": entry.get("createdBy"),
    }


def _extraction_target(entry: dict[str, Any]) -> dict[str, Any]:
    required = ("sourceItemId", "status", "rationale")
    if any(not str(entry.get(key) or "").strip() for key in required):
        raise ValueError("Stan ekstrakcji wymaga sourceItemId, status i rationale")
    if entry["status"] not in {
        "not_started", "target_only", "partial", "all_named_people_extracted", "reviewed", "blocked"
    }:
        raise ValueError(f"Nieznany status ekstrakcji: {entry['status']}")
    return {
        "source_item_id": entry["sourceItemId"],
        "status": entry["status"],
        "source_fingerprint": entry.get("sourceFingerprint") or _fingerprint({"sourceItemId": entry["sourceItemId"]}),
        "extraction_fingerprint": entry.get("extractionFingerprint") or _fingerprint(entry),
        "rationale": entry["rationale"],
        "created_by": entry.get("createdBy"),
        "batch_id": entry.get("batchId"),
        "reading_review_decision_id": entry.get("readingReviewDecisionId"),
    }


def _same_subset(current: dict[str, Any], target: dict[str, Any]) -> bool:
    return all(current.get(key) == value for key, value in target.items())


def _build_preview(connection, payload: dict[str, Any]) -> dict[str, Any]:
    changes: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    domain_completion_reports: list[dict[str, Any]] = []
    planned_item_ids = {str(entry.get("id")) for entry in payload.get("items", [])}
    if len(planned_item_ids) != len(payload.get("items", [])):
        raise ValueError("Manifest zawiera powtórzony source item")
    seen_moves: set[tuple[str, str]] = set()
    seen_extractions: set[str] = set()
    seen_coverage: set[tuple[str, str]] = set()

    for entry in payload.get("items", []):
        if not isinstance(entry, dict) or not str(entry.get("id") or "").strip():
            raise ValueError("Każdy wpis items wymaga id")
        object_id = entry["id"]
        current = _row(connection, "source_items", object_id)
        mode = entry.get("mode", "update")
        if mode not in {"create", "update"}:
            raise ValueError("items.mode musi być create albo update")
        target = _item_target(entry, current)
        parent_id = target.get("parent_id")
        if parent_id and parent_id != object_id and not _row(connection, "source_items", parent_id) and parent_id not in planned_item_ids:
            conflicts.append({"kind": "source_item", "id": object_id, "reason": "missing_parent", "parentId": parent_id})
        expected = entry.get("expected", {})
        if not isinstance(expected, dict):
            raise ValueError("items.expected musi być obiektem")
        if current:
            expected_target = {
                column: expected[manifest_key]
                for column, manifest_key in ITEM_FIELDS.items()
                if manifest_key in expected
            }
            if _same_subset(current, target):
                action = "no_op"
            elif expected_target and not _same_subset(current, expected_target):
                conflicts.append({"kind": "source_item", "id": object_id, "reason": "expected_state_mismatch"})
                continue
            elif mode == "create":
                conflicts.append({"kind": "source_item", "id": object_id, "reason": "create_target_exists_differently"})
                continue
            else:
                action = "update"
        else:
            if mode == "update":
                conflicts.append({"kind": "source_item", "id": object_id, "reason": "missing_update_target"})
                continue
            if not str(entry.get("title") or "").strip() or not str(entry.get("itemType") or "").strip():
                raise ValueError("Nowy source item wymaga title i itemType")
            action = "create"
        changes.append({"kind": "source_item", "id": object_id, "action": action, "target": target})

    for entry in payload.get("moves", []):
        if not isinstance(entry, dict):
            raise ValueError("moves musi zawierać obiekty")
        object_type = entry.get("objectType")
        table = MOVE_TABLES.get(object_type)
        if not table:
            raise ValueError(f"Nieznany objectType w moves: {object_type}")
        object_id = str(entry.get("id") or "")
        move_key = (str(object_type), object_id)
        if move_key in seen_moves:
            raise ValueError(
                f"Manifest zawiera powtórzone przeniesienie: {object_type}/{object_id}"
            )
        seen_moves.add(move_key)
        target_source = str(entry.get("targetSourceItemId") or "")
        expected_source = str(entry.get("expectedSourceItemId") or "")
        current = _row(connection, table, object_id)
        if not current:
            conflicts.append({"kind": object_type, "id": object_id, "reason": "missing_move_target"})
            continue
        if not _row(connection, "source_items", target_source) and target_source not in planned_item_ids:
            conflicts.append({"kind": object_type, "id": object_id, "reason": "missing_target_source"})
            continue
        if current["source_item_id"] == target_source:
            action = "no_op"
        elif current["source_item_id"] != expected_source:
            conflicts.append({"kind": object_type, "id": object_id, "reason": "move_source_mismatch", "actualSourceItemId": current["source_item_id"]})
            continue
        else:
            action = "update"
        changes.append({"kind": object_type, "table": table, "id": object_id, "action": action, "targetSourceItemId": target_source})

    for entry in payload.get("extractionStates", []):
        target = _extraction_target(entry)
        if target["source_item_id"] in seen_extractions:
            raise ValueError(
                "Manifest zawiera powtórzony stan ekstrakcji: "
                + target["source_item_id"]
            )
        seen_extractions.add(target["source_item_id"])
        if not _row(connection, "source_items", target["source_item_id"]) and target["source_item_id"] not in planned_item_ids:
            conflicts.append({"kind": "extraction", "id": target["source_item_id"], "reason": "missing_source_item"})
            continue
        declaration = entry.get("domainCompletion")
        if target["status"] == "all_named_people_extracted" and declaration is None:
            report = {
                "state": "incomplete",
                "mode": FULL_ACT,
                "sourceItemId": target["source_item_id"],
                "expectedCount": None,
                "completeCount": 0,
                "acceptedNamedPeopleFingerprint": None,
                "gaps": [],
            }
            domain_completion_reports.append(report)
            conflicts.append(
                {
                    "kind": "domain_completion",
                    "id": target["source_item_id"],
                    "reason": "domain_completion_declaration_required",
                    "status": target["status"],
                }
            )
        elif declaration is not None:
            report = assess_domain_completion(
                connection,
                source_item_id=target["source_item_id"],
                declaration=declaration,
            )
            domain_completion_reports.append(report)
            if (
                target["status"] == "all_named_people_extracted"
                and report["mode"] != FULL_ACT
            ):
                conflicts.append(
                    {
                        "kind": "domain_completion",
                        "id": target["source_item_id"],
                        "reason": "full_extraction_requires_domain_writeback",
                        "state": report["state"],
                    }
                )
            elif target["status"] == "all_named_people_extracted":
                conflicts.extend(gap_conflicts(report))
        review_decision_id = target["reading_review_decision_id"]
        if review_decision_id:
            review = _row(connection, "reading_review_decisions", review_decision_id)
            if not review:
                conflicts.append({
                    "kind": "extraction",
                    "id": target["source_item_id"],
                    "reason": "missing_reading_review_decision",
                    "readingReviewDecisionId": review_decision_id,
                })
                continue
            if review["source_item_id"] != target["source_item_id"]:
                conflicts.append({
                    "kind": "extraction",
                    "id": target["source_item_id"],
                    "reason": "reading_review_source_mismatch",
                    "readingReviewDecisionId": review_decision_id,
                    "reviewSourceItemId": review["source_item_id"],
                })
                continue
        latest_row = connection.execute(
            "SELECT * FROM document_extraction_states WHERE source_item_id=? ORDER BY revision DESC LIMIT 1",
            (target["source_item_id"],),
        ).fetchone()
        latest = dict(latest_row) if latest_row else None
        comparison = {key: value for key, value in target.items() if key not in {"created_by"}}
        action = "no_op" if latest and _same_subset(latest, comparison) else "create"
        changes.append(
            {
                "kind": "extraction",
                "id": target["source_item_id"],
                "action": action,
                "target": target,
                "domainCompletion": domain_completion_reports[-1]
                if declaration is not None
                or target["status"] == "all_named_people_extracted"
                else None,
            }
        )

    for entry in payload.get("coverage", []):
        target = _coverage_target(entry)
        coverage_identity = (target["source_item_id"], target["coverage_key"])
        if coverage_identity in seen_coverage:
            raise ValueError(
                "Manifest zawiera powtórzony coverage: "
                + "/".join(coverage_identity)
            )
        seen_coverage.add(coverage_identity)
        if not _row(connection, "source_items", target["source_item_id"]) and target["source_item_id"] not in planned_item_ids:
            conflicts.append({"kind": "coverage", "id": target["source_item_id"], "reason": "missing_source_item"})
            continue
        latest_row = connection.execute(
            """SELECT * FROM source_coverage_assessments
                 WHERE source_item_id=? AND coverage_key=?
                 ORDER BY revision DESC LIMIT 1""",
            (target["source_item_id"], target["coverage_key"]),
        ).fetchone()
        latest = dict(latest_row) if latest_row else None
        comparison = {key: value for key, value in target.items() if key not in {"created_by"}}
        action = "no_op" if latest and _same_subset(latest, comparison) else "create"
        changes.append({"kind": "coverage", "id": f"{target['source_item_id']}:{target['coverage_key']}", "action": action, "target": target})

    summary = {action: sum(change["action"] == action for change in changes) for action in ("create", "update", "no_op")}
    summary["conflict"] = len(conflicts)
    return {
        "summary": summary,
        "changes": changes,
        "conflicts": conflicts,
        "domainCompletion": domain_completion_reports,
    }


def source_structure_preview(root: Path, manifest_path: Path) -> dict[str, Any]:
    payload = _load_manifest(manifest_path)
    initialize(root)
    with connect(root) as connection:
        require_tables(connection, REQUIRED_TABLES)
        connection.execute("PRAGMA query_only=ON")
        preview = _build_preview(connection, payload)
    return {
        "mode": "preview",
        "status": "conflict" if preview["conflicts"] else "ready",
        "manifestFingerprint": payload["manifestFingerprint"],
        **preview,
    }


def _audit(connection, actor: str, action: str, object_type: str, object_id: str, before: Any, after: Any, rationale: str) -> None:
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


def _insert_source_item(connection, object_id: str, target: dict[str, Any]) -> None:
    defaults = {
        "collection_id": None,
        "parent_id": None,
        "legacy_source_id": None,
        "title": None,
        "item_type": None,
        "date_text": None,
        "locator": None,
        "source_url": None,
        "citation_text": None,
        "source_quality": "Indeterminable",
        "verification_status": "imported_unverified",
        "metadata_json": "{}",
    }
    defaults.update(target)
    connection.execute(
        """INSERT INTO source_items(
               id,collection_id,parent_id,legacy_source_id,title,item_type,date_text,
               locator,source_url,citation_text,source_quality,verification_status,metadata_json
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (object_id,) + tuple(defaults[key] for key in defaults),
    )


def apply_source_structure(root: Path, manifest_path: Path) -> dict[str, Any]:
    payload = _load_manifest(manifest_path)
    initialize(root)
    actor = payload["actor"]
    rationale = payload["rationale"]
    now = utc_now()
    fingerprint = payload["manifestFingerprint"]
    manifest_id = stable_id("source_structure_manifest", fingerprint)
    with connect(root) as connection:
        require_tables(connection, REQUIRED_TABLES)
        prior = connection.execute(
            """SELECT r.id,r.summary_json FROM source_structure_runs r
                 JOIN source_structure_manifests m ON m.id=r.manifest_id
                WHERE m.manifest_fingerprint=? AND r.status='applied'""",
            (fingerprint,),
        ).fetchone()
        if prior:
            prior_payload = json.loads(prior["summary_json"])
            if "summary" in prior_payload:
                prior_summary = prior_payload["summary"]
                prior_domain_completion = prior_payload.get("domainCompletion", [])
            else:
                prior_summary = prior_payload
                prior_domain_completion = []
            return {
                "mode": "apply",
                "status": "no_op",
                "manifestFingerprint": fingerprint,
                "priorRunId": prior["id"],
                "summary": prior_summary,
                "domainCompletion": prior_domain_completion,
            }
        connection.execute("BEGIN IMMEDIATE")
        try:
            preview = _build_preview(connection, payload)
            if preview["conflicts"]:
                raise RuntimeError("Manifest hierarchii źródeł ma konflikty; apply przerwany")
            for change in preview["changes"]:
                if change["action"] == "no_op":
                    continue
                kind = change["kind"]
                if kind == "source_item":
                    before = _row(connection, "source_items", change["id"])
                    if change["action"] == "create":
                        _insert_source_item(connection, change["id"], change["target"])
                    else:
                        assignments = ",".join(f"{key}=?" for key in change["target"])
                        connection.execute(
                            f"UPDATE source_items SET {assignments} WHERE id=?",
                            tuple(change["target"].values()) + (change["id"],),
                        )
                    after = _row(connection, "source_items", change["id"])
                    _audit(connection, actor, f"source_structure_{change['action']}", "source_item", change["id"], before, after, rationale)
                elif kind in MOVE_TABLES:
                    before = _row(connection, change["table"], change["id"])
                    connection.execute(
                        f"UPDATE {change['table']} SET source_item_id=? WHERE id=?",
                        (change["targetSourceItemId"], change["id"]),
                    )
                    after = _row(connection, change["table"], change["id"])
                    _audit(connection, actor, "source_structure_move", change["table"], change["id"], before, after, rationale)
                elif kind == "extraction":
                    target = change["target"]
                    prior_state = connection.execute(
                        "SELECT id,revision FROM document_extraction_states WHERE source_item_id=? ORDER BY revision DESC LIMIT 1",
                        (target["source_item_id"],),
                    ).fetchone()
                    revision = int(prior_state["revision"]) + 1 if prior_state else 1
                    state_id = stable_id("extraction_state", target["source_item_id"], str(revision), target["extraction_fingerprint"])
                    connection.execute(
                        """INSERT INTO document_extraction_states(
                               id,source_item_id,revision,supersedes_state_id,batch_id,
                               reading_review_decision_id,status,source_fingerprint,
                               extraction_fingerprint,rationale,created_by,created_at
                           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            state_id, target["source_item_id"], revision,
                            prior_state["id"] if prior_state else None,
                            target["batch_id"], target["reading_review_decision_id"],
                            target["status"], target["source_fingerprint"],
                            target["extraction_fingerprint"], target["rationale"],
                            target["created_by"] or actor, now,
                        ),
                    )
                    _audit(connection, actor, "source_extraction_assess", "document_extraction_state", state_id, None, target, rationale)
                elif kind == "coverage":
                    target = change["target"]
                    prior_assessment = connection.execute(
                        """SELECT id,revision FROM source_coverage_assessments
                             WHERE source_item_id=? AND coverage_key=?
                             ORDER BY revision DESC LIMIT 1""",
                        (target["source_item_id"], target["coverage_key"]),
                    ).fetchone()
                    revision = int(prior_assessment["revision"]) + 1 if prior_assessment else 1
                    assessment_id = stable_id(
                        "source_coverage", target["source_item_id"], target["coverage_key"],
                        str(revision), target["extraction_fingerprint"] if "extraction_fingerprint" in target else _fingerprint(target),
                    )
                    columns = (
                        "id", "source_item_id", "coverage_key", "revision",
                        "supersedes_assessment_id", "dimension", "coverage_level",
                        "record_type", "scope_text", "unit_kind", "completed_units",
                        "total_units", "status", "systematic", "qa_status", "basis_type",
                        "basis_ref", "metadata_json", "rationale", "created_by", "created_at",
                    )
                    values = (
                        assessment_id, target["source_item_id"], target["coverage_key"], revision,
                        prior_assessment["id"] if prior_assessment else None,
                        target["dimension"], target["coverage_level"], target["record_type"],
                        target["scope_text"], target["unit_kind"], target["completed_units"],
                        target["total_units"], target["status"], target["systematic"],
                        target["qa_status"], target["basis_type"], target["basis_ref"],
                        target["metadata_json"], target["rationale"],
                        target["created_by"] or actor, now,
                    )
                    connection.execute(
                        f"INSERT INTO source_coverage_assessments({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                        values,
                    )
                    _audit(connection, actor, "source_coverage_assess", "source_coverage_assessment", assessment_id, None, target, rationale)

            connection.execute(
                """INSERT INTO source_structure_manifests(
                       id,manifest_fingerprint,manifest_json,actor,rationale,created_at
                   ) VALUES (?,?,?,?,?,?)""",
                (manifest_id, fingerprint, _canonical(payload), actor, rationale, now),
            )
            run_id = stable_id("source_structure_run", manifest_id, "apply")
            connection.execute(
                """INSERT INTO source_structure_runs(
                       id,manifest_id,mode,status,summary_json,actor,created_at
                   ) VALUES (?,?,'apply','applied',?,?,?)""",
                (
                    run_id,
                    manifest_id,
                    _canonical(
                        {
                            "summary": preview["summary"],
                            "domainCompletion": preview["domainCompletion"],
                        }
                    ),
                    actor,
                    now,
                ),
            )
            _audit(connection, actor, "source_structure_apply", "source_structure_manifest", manifest_id, None, preview["summary"], rationale)
            connection.commit()
            return {
                "mode": "apply",
                "status": "applied",
                "manifestFingerprint": fingerprint,
                "runId": run_id,
                **preview,
            }
        except Exception:
            connection.rollback()
            raise


def source_coverage_report(root: Path, source_item_id: str | None = None) -> dict[str, Any]:
    initialize(root)
    with connect(root) as connection:
        require_tables(connection, REQUIRED_TABLES)
        connection.execute("PRAGMA query_only=ON")
        parameters: tuple[Any, ...] = ()
        scope_cte = ""
        where = ""
        if source_item_id:
            scope_cte = """WITH RECURSIVE scoped_sources(id) AS (
                                SELECT ?
                                UNION ALL
                                SELECT child.id
                                  FROM source_items child
                                  JOIN scoped_sources parent ON child.parent_id=parent.id
                            )"""
            where = "WHERE s.id IN (SELECT id FROM scoped_sources)"
            parameters = (source_item_id,)
        items = [
            dict(row)
            for row in connection.execute(
                f"""{scope_cte}
                       SELECT s.id,s.parent_id,s.title,s.item_type,s.date_text,s.locator,
                            COUNT(DISTINCT child.id) AS child_count,
                            COUNT(DISTINCT CASE WHEN child.item_type='act' THEN child.id END) AS act_count,
                            COUNT(DISTINCT e.id) AS event_count,
                            COUNT(DISTINCT o.id) AS occurrence_count
                       FROM source_items s
                       LEFT JOIN source_items child ON child.parent_id=s.id
                       LEFT JOIN events e ON e.source_item_id=s.id
                       LEFT JOIN person_occurrences o ON o.source_item_id=s.id
                       {where}
                      GROUP BY s.id
                      ORDER BY CASE s.item_type WHEN 'collection' THEN 0 WHEN 'register' THEN 1 WHEN 'act' THEN 3 ELSE 2 END,
                               s.title,s.id""",
                parameters,
            )
        ]
        item_ids = [item["id"] for item in items]
        coverage_by_source: dict[str, list[dict[str, Any]]] = {item_id: [] for item_id in item_ids}
        if item_ids:
            placeholders = ",".join("?" for _ in item_ids)
            rows = connection.execute(
                f"""SELECT assessment.*
                       FROM source_coverage_assessments assessment
                      WHERE assessment.source_item_id IN ({placeholders})
                        AND NOT EXISTS (
                            SELECT 1 FROM source_coverage_assessments newer
                             WHERE newer.source_item_id=assessment.source_item_id
                               AND newer.coverage_key=assessment.coverage_key
                               AND newer.revision>assessment.revision
                        )
                      ORDER BY assessment.source_item_id,assessment.dimension,
                               assessment.coverage_level,assessment.coverage_key""",
                tuple(item_ids),
            )
            for row in rows:
                item = dict(row)
                item["systematic"] = bool(item["systematic"])
                item["metadata"] = _json_object(item.pop("metadata_json"))
                coverage_by_source[item["source_item_id"]].append(item)
        for item in items:
            item["coverage"] = coverage_by_source[item["id"]]
    return {
        "schemaVersion": 1,
        "scope": {"sourceItemId": source_item_id},
        "summary": {
            "items": len(items),
            "registers": sum(item["item_type"] == "register" for item in items),
            "acts": sum(item["item_type"] == "act" for item in items),
            "coverageAssessments": sum(len(item["coverage"]) for item in items),
        },
        "items": items,
    }


__all__ = [
    "apply_source_structure",
    "source_coverage_report",
    "source_structure_preview",
]
