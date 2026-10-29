from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..question_backlog import build_research_backlog
from ..research_execution import attach_research_execution
from .common import display_rationale
from .privacy import PrivacyContext


@dataclass(frozen=True)
class ResearchProjection:
    claims: list[dict[str, Any]]
    conflicts: list[dict[str, Any]]
    backlog: dict[str, Any]


def project_research(
    root: Path,
    connection: sqlite3.Connection,
    privacy: PrivacyContext,
    *,
    allowed_source_ids: set[str],
    allowed_conflict_target_ids: set[str] | None = None,
    source_reported_networks: list[dict[str, Any]] | None = None,
) -> ResearchProjection:
    restricted_people = privacy.restricted_ids("person")
    restricted_occurrences = privacy.restricted_ids("person_occurrence")
    restricted_claim_ids = {
        row["claim_id"]
        for row in connection.execute(
            """SELECT DISTINCT ce.claim_id,sa.source_item_id
               FROM claim_evidence ce
               JOIN source_assertions sa ON sa.id=ce.source_assertion_id"""
        )
        if row["source_item_id"] not in allowed_source_ids
    }
    claims: list[dict[str, Any]] = []
    allowed_claim_ids: set[str] = set()
    for row in connection.execute(
        """SELECT id,case_id,statement,subject_ref,predicate,object_ref,
                  status,rationale,updated_at
           FROM claims ORDER BY case_id,updated_at,id"""
    ):
        if (
            row["id"] in restricted_claim_ids
            or privacy.blocked("claim", row["id"])
            or privacy.blocked("research_case", row["case_id"])
            or privacy.blocked_reference(row["subject_ref"])
            or privacy.blocked_reference(row["object_ref"])
        ):
            continue
        allowed_claim_ids.add(row["id"])
        claims.append(
            {
                "id": row["id"],
                "caseId": row["case_id"],
                "statement": row["statement"],
                "predicate": row["predicate"],
                "status": row["status"],
                "rationale": display_rationale(row["rationale"]),
                "updatedAt": row["updated_at"],
            }
        )
    conflicts = [
        {
            "id": row["id"],
            "claimId": row["claim_id"],
            "title": row["title"],
            "description": row["description"],
            "status": row["status"],
        }
        for row in connection.execute(
            "SELECT id,claim_id,title,description,status FROM conflicts ORDER BY status,title,id"
        )
        if (not row["claim_id"] or row["claim_id"] in allowed_claim_ids)
        and not privacy.blocked("conflict", row["id"])
    ]
    allowed_conflict_ids = {item["id"] for item in conflicts}
    if allowed_conflict_target_ids is not None:
        allowed_conflict_ids &= allowed_conflict_target_ids

    backlog = build_research_backlog(
        root,
        connection=connection,
        source_reported_networks=source_reported_networks,
    )

    def target_blocked(target: dict[str, Any]) -> bool:
        target_type = target.get("type")
        target_id = target.get("id")
        if privacy.blocked_reference(target_id):
            return True
        if privacy.blocked(str(target_type), target_id):
            return True
        if target_type == "person" and target_id in restricted_people:
            return True
        if target_type == "person_occurrence" and target_id in restricted_occurrences:
            return True
        if target_type == "source_item" and target_id not in allowed_source_ids:
            return True
        if target_type == "conflict" and target_id not in allowed_conflict_ids:
            return True
        return False

    def reference_blocked(object_id: str | None, *kinds: str) -> bool:
        return privacy.blocked_reference(object_id) or any(
            privacy.blocked(kind, object_id) for kind in kinds
        )

    def scope_backing_blocked(scope_id: str) -> bool:
        # Include the containment path: a public child may have private backing
        # in its parent. These IDs are used only for gating, never serialized.
        scopes = connection.execute(
            """WITH RECURSIVE ancestors(id) AS (
                   SELECT id FROM research_scopes WHERE id=?
                   UNION
                   SELECT edge.parent_scope_id FROM research_scope_edges edge
                   JOIN ancestors ON edge.child_scope_id=ancestors.id
                   WHERE edge.relation_type='contains'
               ) SELECT scope.id,scope.legacy_case_id FROM research_scopes scope
                 JOIN ancestors ON ancestors.id=scope.id""",
            (scope_id,),
        ).fetchall()
        if not scopes:
            return True
        for scope in scopes:
            if reference_blocked(scope["id"], "research_scope") or reference_blocked(
                scope["legacy_case_id"], "research_case"
            ):
                return True
            for target in connection.execute(
                """SELECT id,target_type,target_id FROM research_scope_targets
                   WHERE scope_id=?""", (scope["id"],)
            ):
                if reference_blocked(target["id"], "research_scope_target") or target_blocked(
                    {"type": target["target_type"], "id": target["target_id"]}
                ):
                    return True
        return False

    def work_backing_blocked(work_id: str) -> bool:
        seen: set[str] = set()
        while work_id:
            if work_id in seen or reference_blocked(
                work_id, "research_work_item", "work_item"
            ):
                return True
            seen.add(work_id)
            work = connection.execute(
                """SELECT scope_id,question_id,parent_work_item_id,status
                   FROM research_work_items WHERE id=?""", (work_id,)
            ).fetchone()
            if work is None or work["status"] in {"completed", "cancelled", "superseded"}:
                return True
            if work["scope_id"] and scope_backing_blocked(work["scope_id"]):
                return True
            if work["question_id"]:
                question = connection.execute(
                    "SELECT case_id FROM research_questions WHERE id=?",
                    (work["question_id"],),
                ).fetchone()
                if question is None or reference_blocked(
                    work["question_id"], "research_question", "question"
                ) or reference_blocked(question["case_id"], "research_case"):
                    return True
                for target in connection.execute(
                    """SELECT id,target_type,target_id FROM research_question_targets
                       WHERE question_id=?""", (work["question_id"],)
                ):
                    if reference_blocked(target["id"], "research_question_target") or target_blocked(
                        {"type": target["target_type"], "id": target["target_id"]}
                    ):
                        return True
                for scope in connection.execute(
                    "SELECT id,scope_id FROM research_question_scopes WHERE question_id=?",
                    (work["question_id"],),
                ):
                    if reference_blocked(
                        scope["id"], "research_question_scope"
                    ) or scope_backing_blocked(scope["scope_id"]):
                        return True
            work_id = work["parent_work_item_id"]
        return False

    cases: dict[str, dict[str, Any]] = {
        row["id"]: {
            "id": row["id"],
            "title": row["title"],
            "status": row["status"],
            "summary": row["summary"],
            "nextActions": [],
        }
        for row in connection.execute(
            "SELECT id,title,status,summary FROM research_cases ORDER BY id"
        )
        if not reference_blocked(row["id"], "research_case")
    }
    for row in connection.execute(
        """SELECT id,case_id,work_item_id,action,priority,status FROM next_actions
           WHERE question_id IS NULL AND status='open'
           ORDER BY case_id,priority,id"""
    ):
        if row["case_id"] not in cases or reference_blocked(
            row["id"], "next_action", "research_next_action"
        ) or (row["work_item_id"] and work_backing_blocked(row["work_item_id"])):
            continue
        cases[row["case_id"]]["nextActions"].append({
            "id": row["id"], "action": row["action"],
            "priority": row["priority"], "status": row["status"],
        })
    backlog["cases"] = list(cases.values())

    def visible_scope_path(scope: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            part
            for part in scope.get("primaryPath", [])
            if not privacy.blocked("research_scope", part.get("id"))
        ]

    def backlog_item_blocked(item: dict[str, Any]) -> bool:
        if privacy.blocked("research_case", item.get("caseId")):
            return True
        if item["origin"] == "curated" and (
            privacy.blocked("research_question", item["id"])
            or privacy.blocked("question", item["id"])
        ):
            return True
        for target in item.get("targets", []):
            if target_blocked(target):
                return True
        return False

    backlog["items"] = [item for item in backlog["items"] if not backlog_item_blocked(item)]
    for item in backlog["items"]:
        assessment = item.get("assessment")
        if assessment is not None:
            source = assessment["source"]
            work = assessment["scopedWork"]
            scoped = next((scope for scope in item.get("scopes", []) if scope["id"] == work["scopeId"]), None)
            blocked_refs = (
                ("research_log_entry", source["logEntryId"]),
                ("research_log", source["logEntryId"]),
                ("research_work_item", work["id"]),
                ("work_item", work["id"]),
                ("research_work_item", work["parentWorkItemId"]),
                ("research_scope", work["scopeId"]),
            )
            scope_refs = [("research_scope", scope["id"]) for scope in item.get("scopes", [])]
            scope_refs.extend(("research_scope", part.get("id")) for scope in item.get("scopes", []) for part in scope.get("primaryPath", []))
            backing_blocked = any(
                privacy.blocked(kind, oid) or privacy.blocked_reference(oid)
                for kind, oid in (*blocked_refs, *scope_refs)
            )
            scope_target_blocked = any(
                target_blocked(target)
                for scope in item.get("scopes", [])
                for target in scope.get("targets", [])
            )
            if scoped is None or backing_blocked or scope_target_blocked:
                # Preserve explicit invalid/unpublishable presence; never show an
                # older ready assessment or reveal the reason/private backing.
                item["assessment"] = None
        recent_research = [
            activity for activity in item.get("recentResearch", [])
            if not privacy.blocked("research_log_entry", activity["id"])
            and not privacy.blocked("research_log", activity["id"])
            and not privacy.blocked_reference(activity["id"])
        ]
        if recent_research:
            item["recentResearch"] = recent_research
        else:
            item.pop("recentResearch", None)
        item["scopes"] = [
            {
                **scope,
                "primaryPath": visible_scope_path(scope),
                "targets": [
                    target
                    for target in scope.get("targets", [])
                    if not target_blocked(target)
                ],
            }
            for scope in item.get("scopes", [])
            if not privacy.blocked("research_scope", scope.get("id"))
        ]
        item["scopeIds"] = [scope["id"] for scope in item["scopes"]]
        primary_scope = next(
            (scope for scope in item["scopes"] if scope.get("primary")),
            item["scopes"][0] if item["scopes"] else None,
        )
        if primary_scope:
            item["primaryScopeId"] = primary_scope["id"]
            item["primaryScopeTitle"] = primary_scope["title"]
            item["primaryScopeType"] = primary_scope["type"]
            item["primaryScopePath"] = primary_scope.get("primaryPath", [])
        else:
            item["primaryScopeId"] = None
            item["primaryScopeTitle"] = None
            item["primaryScopeType"] = None
            item["primaryScopePath"] = []

    referenced_scope_ids = {
        scope_id
        for item in backlog["items"]
        for scope_id in item.get("scopeIds", [])
    }
    visible_edges = [
        edge
        for edge in backlog.get("scopeEdges", [])
        if not privacy.blocked("research_scope", edge.get("parentScopeId"))
        and not privacy.blocked("research_scope", edge.get("childScopeId"))
    ]
    changed = True
    while changed:
        changed = False
        for edge in visible_edges:
            if (
                edge.get("relationType") == "contains"
                and edge.get("childScopeId") in referenced_scope_ids
                and edge.get("parentScopeId") not in referenced_scope_ids
            ):
                referenced_scope_ids.add(edge["parentScopeId"])
                changed = True
    backlog["scopes"] = [
        {
            **scope,
            "primaryPath": visible_scope_path(scope),
            "targets": [
                target
                for target in scope.get("targets", [])
                if not target_blocked(target)
            ],
        }
        for scope in backlog.get("scopes", [])
        if scope.get("id") in referenced_scope_ids
        and not privacy.blocked("research_scope", scope.get("id"))
    ]
    visible_scope_ids = {scope["id"] for scope in backlog["scopes"]}
    backlog["scopeIds"] = sorted(visible_scope_ids)
    backlog["scopeEdges"] = [
        edge
        for edge in visible_edges
        if edge.get("parentScopeId") in visible_scope_ids
        and edge.get("childScopeId") in visible_scope_ids
    ]
    backlog["summary"]["canonical"] = sum(
        item["origin"] == "curated" for item in backlog["items"]
    )
    backlog["summary"]["candidates"] = sum(
        item["origin"] == "deterministic" for item in backlog["items"]
    )
    backlog["summary"]["possibleDuplicates"] = sum(
        bool(item["potentialDuplicateQuestionIds"])
        for item in backlog["items"]
        if item["origin"] == "deterministic"
    )
    backlog["summary"]["total"] = len(backlog["items"])
    backlog["summary"]["byStage"] = {
        stage: sum(item["workflowStage"] == stage for item in backlog["items"])
        for stage in backlog["workflowStages"]
    }
    attach_research_execution(root, connection, backlog)
    return ResearchProjection(claims, conflicts, backlog)
