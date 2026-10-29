from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from .db import connect, initialize
from .research import QUESTION_TARGET_TABLES, VALID_QUESTION_TARGET_TYPES, require_tables
from .workspace import stable_id, utc_now


VALID_SCOPE_TYPES = {
    "program",
    "lineage",
    "branch",
    "family_unit",
    "place_study",
    "theme",
    "legacy_case",
    "other",
}
VALID_SCOPE_STATUSES = {"planned", "active", "on_hold", "completed", "closed"}
VALID_SCOPE_RELATIONS = {"contains", "related", "continues", "overlaps"}
VALID_SCOPE_SEMANTICS = {"research_context", "source_recorded", "hypothesis"}
VALID_QUESTION_SCOPE_ROLES = {"primary", "supporting", "context"}
VALID_WORK_ITEM_TYPES = {"problem", "work_package", "task", "review", "waiting"}
VALID_WORK_ITEM_STATUSES = {
    "planned",
    "ready",
    "in_progress",
    "blocked",
    "completed",
    "cancelled",
    "superseded",
}

HIERARCHY_TABLES = {
    "research_scopes",
    "research_scope_edges",
    "research_scope_targets",
    "research_question_scopes",
    "research_work_items",
}


def _required(value: str, label: str) -> str:
    cleaned = str(value).strip()
    if not cleaned:
        raise ValueError(f"{label} nie może być puste")
    return cleaned


def _choice(value: str, allowed: set[str], label: str) -> str:
    if value not in allowed:
        raise ValueError(f"{label} musi być jednym z: {', '.join(sorted(allowed))}")
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _manifest_fingerprint(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(manifest).encode("utf-8")).hexdigest()


def _physical_target_exists(
    connection: sqlite3.Connection,
    target_type: str,
    target_id: str,
) -> bool:
    table = QUESTION_TARGET_TABLES.get(target_type)
    if table is None:
        return True
    return connection.execute(
        f"SELECT 1 FROM {table} WHERE id=?", (target_id,)
    ).fetchone() is not None


def create_research_scope(
    root: Path,
    scope_id: str,
    scope_type: str,
    title: str,
    *,
    summary: str | None = None,
    status: str = "active",
    legacy_case_id: str | None = None,
) -> dict[str, Any]:
    scope_id = _required(scope_id, "scope_id")
    scope_type = _choice(scope_type, VALID_SCOPE_TYPES, "scope_type")
    title = _required(title, "title")
    status = _choice(status, VALID_SCOPE_STATUSES, "status")
    initialize(root)
    now = utc_now()
    with connect(root) as connection:
        require_tables(connection, HIERARCHY_TABLES)
        if legacy_case_id and connection.execute(
            "SELECT 1 FROM research_cases WHERE id=?", (legacy_case_id,)
        ).fetchone() is None:
            raise LookupError(f"Nie znaleziono sprawy legacy: {legacy_case_id}")
        connection.execute(
            """INSERT INTO research_scopes(
                   id,scope_type,title,summary,status,legacy_case_id,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                   scope_type=excluded.scope_type,
                   title=excluded.title,
                   summary=COALESCE(excluded.summary,research_scopes.summary),
                   status=excluded.status,
                   legacy_case_id=COALESCE(excluded.legacy_case_id,research_scopes.legacy_case_id),
                   updated_at=excluded.updated_at""",
            (
                scope_id,
                scope_type,
                title,
                summary,
                status,
                legacy_case_id,
                now,
                now,
            ),
        )
        row = connection.execute(
            "SELECT * FROM research_scopes WHERE id=?", (scope_id,)
        ).fetchone()
    return dict(row)


def link_research_scopes(
    root: Path,
    parent_scope_id: str,
    child_scope_id: str,
    *,
    relation_type: str = "contains",
    is_primary: bool = False,
    sort_order: int = 100,
    notes: str | None = None,
) -> dict[str, Any]:
    relation_type = _choice(relation_type, VALID_SCOPE_RELATIONS, "relation_type")
    if sort_order < 0:
        raise ValueError("sort_order nie może być ujemny")
    initialize(root)
    now = utc_now()
    edge_id = stable_id(
        "research_scope_edge", parent_scope_id, child_scope_id, relation_type
    )
    with connect(root) as connection:
        require_tables(connection, HIERARCHY_TABLES)
        existing = {
            row[0]
            for row in connection.execute(
                "SELECT id FROM research_scopes WHERE id IN (?,?)",
                (parent_scope_id, child_scope_id),
            )
        }
        missing = sorted({parent_scope_id, child_scope_id} - existing)
        if missing:
            raise LookupError("Nie znaleziono zakresów: " + ", ".join(missing))
        if relation_type == "contains" and is_primary:
            connection.execute(
                """UPDATE research_scope_edges
                      SET is_primary=0
                    WHERE child_scope_id=? AND relation_type='contains' AND id<>?""",
                (child_scope_id, edge_id),
            )
        connection.execute(
            """INSERT INTO research_scope_edges(
                   id,parent_scope_id,child_scope_id,relation_type,is_primary,
                   sort_order,notes,created_at
               ) VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(parent_scope_id,child_scope_id,relation_type) DO UPDATE SET
                   is_primary=excluded.is_primary,
                   sort_order=excluded.sort_order,
                   notes=COALESCE(excluded.notes,research_scope_edges.notes)""",
            (
                edge_id,
                parent_scope_id,
                child_scope_id,
                relation_type,
                int(bool(is_primary)),
                sort_order,
                notes,
                now,
            ),
        )
        row = connection.execute(
            "SELECT * FROM research_scope_edges WHERE id=?", (edge_id,)
        ).fetchone()
    result = dict(row)
    result["is_primary"] = bool(result["is_primary"])
    return result


def link_research_scope_target(
    root: Path,
    scope_id: str,
    target_type: str,
    target_id: str,
    target_label: str,
    *,
    association_role: str = "research_context",
    semantics: str = "research_context",
    date_from: str | None = None,
    date_to: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    target_type = _choice(target_type, VALID_QUESTION_TARGET_TYPES, "target_type")
    target_id = _required(target_id, "target_id")
    target_label = _required(target_label, "target_label")
    association_role = _required(association_role, "association_role")
    semantics = _choice(semantics, VALID_SCOPE_SEMANTICS, "semantics")
    initialize(root)
    now = utc_now()
    target_key = stable_id(
        "research_scope_target", scope_id, target_type, target_id, association_role
    )
    with connect(root) as connection:
        require_tables(connection, HIERARCHY_TABLES)
        if connection.execute(
            "SELECT 1 FROM research_scopes WHERE id=?", (scope_id,)
        ).fetchone() is None:
            raise LookupError(f"Nie znaleziono zakresu: {scope_id}")
        if not _physical_target_exists(connection, target_type, target_id):
            raise LookupError(f"Nie znaleziono celu {target_type}: {target_id}")
        connection.execute(
            """INSERT INTO research_scope_targets(
                   id,scope_id,target_type,target_id,target_label,association_role,
                   semantics,date_from,date_to,notes,created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(scope_id,target_type,target_id,association_role) DO UPDATE SET
                   target_label=excluded.target_label,
                   semantics=excluded.semantics,
                   date_from=COALESCE(excluded.date_from,research_scope_targets.date_from),
                   date_to=COALESCE(excluded.date_to,research_scope_targets.date_to),
                   notes=COALESCE(excluded.notes,research_scope_targets.notes)""",
            (
                target_key,
                scope_id,
                target_type,
                target_id,
                target_label,
                association_role,
                semantics,
                date_from,
                date_to,
                notes,
                now,
            ),
        )
        row = connection.execute(
            "SELECT * FROM research_scope_targets WHERE id=?", (target_key,)
        ).fetchone()
    return dict(row)


def link_question_scope(
    root: Path,
    question_id: str,
    scope_id: str,
    *,
    role: str = "supporting",
    is_primary: bool = False,
    notes: str | None = None,
) -> dict[str, Any]:
    role = _choice(role, VALID_QUESTION_SCOPE_ROLES, "role")
    if is_primary:
        role = "primary"
    initialize(root)
    now = utc_now()
    membership_id = stable_id("research_question_scope", question_id, scope_id)
    with connect(root) as connection:
        require_tables(connection, HIERARCHY_TABLES | {"research_questions"})
        if connection.execute(
            "SELECT 1 FROM research_questions WHERE id=?", (question_id,)
        ).fetchone() is None:
            raise LookupError(f"Nie znaleziono pytania: {question_id}")
        if connection.execute(
            "SELECT 1 FROM research_scopes WHERE id=?", (scope_id,)
        ).fetchone() is None:
            raise LookupError(f"Nie znaleziono zakresu: {scope_id}")
        if is_primary:
            connection.execute(
                """UPDATE research_question_scopes
                      SET is_primary=0,
                          role=CASE WHEN role='primary' THEN 'supporting' ELSE role END
                    WHERE question_id=? AND id<>?""",
                (question_id, membership_id),
            )
        connection.execute(
            """INSERT INTO research_question_scopes(
                   id,question_id,scope_id,role,is_primary,notes,created_at
               ) VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(question_id,scope_id) DO UPDATE SET
                   role=excluded.role,
                   is_primary=excluded.is_primary,
                   notes=COALESCE(excluded.notes,research_question_scopes.notes)""",
            (
                membership_id,
                question_id,
                scope_id,
                role,
                int(bool(is_primary)),
                notes,
                now,
            ),
        )
        row = connection.execute(
            "SELECT * FROM research_question_scopes WHERE id=?", (membership_id,)
        ).fetchone()
    result = dict(row)
    result["is_primary"] = bool(result["is_primary"])
    return result


def create_research_work_item(
    root: Path,
    work_item_id: str,
    item_type: str,
    title: str,
    *,
    scope_id: str | None = None,
    question_id: str | None = None,
    parent_work_item_id: str | None = None,
    completion_criterion: str | None = None,
    status: str = "planned",
    priority: int = 100,
) -> dict[str, Any]:
    work_item_id = _required(work_item_id, "work_item_id")
    item_type = _choice(item_type, VALID_WORK_ITEM_TYPES, "item_type")
    title = _required(title, "title")
    status = _choice(status, VALID_WORK_ITEM_STATUSES, "status")
    if priority < 0:
        raise ValueError("priority nie może być ujemny")
    if not (scope_id or question_id or parent_work_item_id):
        raise ValueError("Pakiet pracy wymaga zakresu, pytania albo rodzica")
    initialize(root)
    now = utc_now()
    with connect(root) as connection:
        require_tables(connection, HIERARCHY_TABLES)
        references = (
            ("research_scopes", scope_id, "zakresu"),
            ("research_questions", question_id, "pytania"),
            ("research_work_items", parent_work_item_id, "pakietu nadrzędnego"),
        )
        for table, object_id, label in references:
            if object_id and connection.execute(
                f"SELECT 1 FROM {table} WHERE id=?", (object_id,)
            ).fetchone() is None:
                raise LookupError(f"Nie znaleziono {label}: {object_id}")
        connection.execute(
            """INSERT INTO research_work_items(
                   id,scope_id,question_id,parent_work_item_id,item_type,title,
                   completion_criterion,status,priority,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                   scope_id=COALESCE(excluded.scope_id,research_work_items.scope_id),
                   question_id=COALESCE(excluded.question_id,research_work_items.question_id),
                   parent_work_item_id=COALESCE(
                       excluded.parent_work_item_id,research_work_items.parent_work_item_id
                   ),
                   item_type=excluded.item_type,
                   title=excluded.title,
                   completion_criterion=COALESCE(
                       excluded.completion_criterion,research_work_items.completion_criterion
                   ),
                   status=excluded.status,
                   priority=excluded.priority,
                   updated_at=excluded.updated_at""",
            (
                work_item_id,
                scope_id,
                question_id,
                parent_work_item_id,
                item_type,
                title,
                completion_criterion,
                status,
                priority,
                now,
                now,
            ),
        )
        row = connection.execute(
            "SELECT * FROM research_work_items WHERE id=?", (work_item_id,)
        ).fetchone()
    return dict(row)


def research_scope_tree(root: Path) -> dict[str, Any]:
    initialize(root)
    with connect(root) as connection:
        require_tables(connection, HIERARCHY_TABLES)
        scopes = [
            dict(row)
            for row in connection.execute(
                """SELECT s.*,
                          (SELECT COUNT(*) FROM research_question_scopes qs
                            WHERE qs.scope_id=s.id) AS question_count,
                          (SELECT COUNT(*) FROM research_scope_targets st
                            WHERE st.scope_id=s.id) AS target_count
                     FROM research_scopes s
                    ORDER BY s.scope_type,s.title,s.id"""
            )
        ]
        edges = [
            {
                **dict(row),
                "is_primary": bool(row["is_primary"]),
            }
            for row in connection.execute(
                """SELECT * FROM research_scope_edges
                    ORDER BY relation_type,parent_scope_id,sort_order,child_scope_id"""
            )
        ]
        targets = [
            dict(row)
            for row in connection.execute(
                """SELECT * FROM research_scope_targets
                    ORDER BY scope_id,target_type,target_label,target_id"""
            )
        ]
        memberships = [
            {
                **dict(row),
                "is_primary": bool(row["is_primary"]),
            }
            for row in connection.execute(
                """SELECT * FROM research_question_scopes
                    ORDER BY question_id,is_primary DESC,role,scope_id"""
            )
        ]
        roots = [
            scope["id"]
            for scope in scopes
            if not any(
                edge["child_scope_id"] == scope["id"]
                and edge["relation_type"] == "contains"
                for edge in edges
            )
        ]
    return {
        "schemaVersion": 1,
        "roots": roots,
        "scopes": scopes,
        "edges": edges,
        "targets": targets,
        "questionMemberships": memberships,
    }


def _load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("Manifest zakresów musi być obiektem schema_version=1")
    for key in ("scopes", "edges", "targets", "question_memberships", "work_items"):
        value = payload.get(key, [])
        if not isinstance(value, list):
            raise ValueError(f"Pole manifestu {key} musi być listą")
    return payload


def preview_research_scope_manifest(root: Path, manifest_path: Path) -> dict[str, Any]:
    manifest = _load_manifest(manifest_path)
    initialize(root)
    with connect(root) as connection:
        require_tables(connection, HIERARCHY_TABLES)
        known_scopes = {
            row[0] for row in connection.execute("SELECT id FROM research_scopes")
        }
        known_questions = {
            row[0] for row in connection.execute("SELECT id FROM research_questions")
        }
        known_work_items = {
            row[0] for row in connection.execute("SELECT id FROM research_work_items")
        }
        manifest_scopes = {str(item.get("id", "")) for item in manifest.get("scopes", [])}
        manifest_work_items = {
            str(item.get("id", "")) for item in manifest.get("work_items", [])
        }
        errors: list[str] = []
        if "" in manifest_scopes:
            errors.append("Każdy zakres wymaga id")
        available_scopes = known_scopes | manifest_scopes
        for edge in manifest.get("edges", []):
            for field in ("parent_scope_id", "child_scope_id"):
                if edge.get(field) not in available_scopes:
                    errors.append(f"Nieznany zakres {edge.get(field)} w edges.{field}")
        for target in manifest.get("targets", []):
            if target.get("scope_id") not in available_scopes:
                errors.append(f"Nieznany zakres {target.get('scope_id')} w targets")
            target_type = str(target.get("target_type", ""))
            target_id = str(target.get("target_id", ""))
            if target_type not in VALID_QUESTION_TARGET_TYPES:
                errors.append(f"Nieprawidłowy target_type: {target_type}")
            elif not _physical_target_exists(connection, target_type, target_id):
                errors.append(f"Nieznany cel {target_type}: {target_id}")
        for membership in manifest.get("question_memberships", []):
            if membership.get("question_id") not in known_questions:
                errors.append(f"Nieznane pytanie: {membership.get('question_id')}")
            if membership.get("scope_id") not in available_scopes:
                errors.append(f"Nieznany zakres: {membership.get('scope_id')}")
        available_work_items = known_work_items | manifest_work_items
        for item in manifest.get("work_items", []):
            if item.get("scope_id") and item.get("scope_id") not in available_scopes:
                errors.append(f"Nieznany zakres pakietu: {item.get('scope_id')}")
            if item.get("question_id") and item.get("question_id") not in known_questions:
                errors.append(f"Nieznane pytanie pakietu: {item.get('question_id')}")
            if item.get("parent_work_item_id") and item.get("parent_work_item_id") not in available_work_items:
                errors.append(
                    f"Nieznany pakiet nadrzędny: {item.get('parent_work_item_id')}"
                )
        counts = {
            "scopes": len(manifest.get("scopes", [])),
            "edges": len(manifest.get("edges", [])),
            "targets": len(manifest.get("targets", [])),
            "questionMemberships": len(manifest.get("question_memberships", [])),
            "workItems": len(manifest.get("work_items", [])),
        }
        existing = {
            "scopes": sum(item in known_scopes for item in manifest_scopes),
            "workItems": sum(item in known_work_items for item in manifest_work_items),
        }
    return {
        "mode": "preview",
        "manifest": str(manifest_path),
        "fingerprint": _manifest_fingerprint(manifest),
        "valid": not errors,
        "errors": sorted(set(errors)),
        "counts": counts,
        "existing": existing,
    }


def apply_research_scope_manifest(root: Path, manifest_path: Path) -> dict[str, Any]:
    preview = preview_research_scope_manifest(root, manifest_path)
    if not preview["valid"]:
        raise ValueError("Manifest zakresów jest nieprawidłowy: " + "; ".join(preview["errors"]))
    manifest = _load_manifest(manifest_path)
    now = utc_now()
    with connect(root) as connection:
        for item in manifest.get("scopes", []):
            scope_type = _choice(str(item.get("scope_type")), VALID_SCOPE_TYPES, "scope_type")
            status = _choice(str(item.get("status", "active")), VALID_SCOPE_STATUSES, "status")
            connection.execute(
                """INSERT INTO research_scopes(
                       id,scope_type,title,summary,status,legacy_case_id,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                       scope_type=excluded.scope_type,title=excluded.title,
                       summary=COALESCE(excluded.summary,research_scopes.summary),
                       status=excluded.status,updated_at=excluded.updated_at""",
                (
                    _required(item.get("id", ""), "scope.id"),
                    scope_type,
                    _required(item.get("title", ""), "scope.title"),
                    item.get("summary"),
                    status,
                    item.get("legacy_case_id"),
                    now,
                    now,
                ),
            )
        for item in manifest.get("edges", []):
            relation = _choice(
                str(item.get("relation_type", "contains")),
                VALID_SCOPE_RELATIONS,
                "relation_type",
            )
            edge_id = stable_id(
                "research_scope_edge",
                item["parent_scope_id"],
                item["child_scope_id"],
                relation,
            )
            primary = int(bool(item.get("is_primary", False)))
            if relation == "contains" and primary:
                connection.execute(
                    """UPDATE research_scope_edges SET is_primary=0
                        WHERE child_scope_id=? AND relation_type='contains' AND id<>?""",
                    (item["child_scope_id"], edge_id),
                )
            connection.execute(
                """INSERT INTO research_scope_edges(
                       id,parent_scope_id,child_scope_id,relation_type,is_primary,
                       sort_order,notes,created_at
                   ) VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(parent_scope_id,child_scope_id,relation_type) DO UPDATE SET
                       is_primary=excluded.is_primary,sort_order=excluded.sort_order,
                       notes=COALESCE(excluded.notes,research_scope_edges.notes)""",
                (
                    edge_id,
                    item["parent_scope_id"],
                    item["child_scope_id"],
                    relation,
                    primary,
                    int(item.get("sort_order", 100)),
                    item.get("notes"),
                    now,
                ),
            )
        for item in manifest.get("targets", []):
            target_type = _choice(
                str(item.get("target_type")), VALID_QUESTION_TARGET_TYPES, "target_type"
            )
            role = _required(item.get("association_role", "research_context"), "association_role")
            target_id = _required(item.get("target_id", ""), "target_id")
            target_key = stable_id(
                "research_scope_target", item["scope_id"], target_type, target_id, role
            )
            connection.execute(
                """INSERT INTO research_scope_targets(
                       id,scope_id,target_type,target_id,target_label,association_role,
                       semantics,date_from,date_to,notes,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(scope_id,target_type,target_id,association_role) DO UPDATE SET
                       target_label=excluded.target_label,semantics=excluded.semantics,
                       date_from=COALESCE(excluded.date_from,research_scope_targets.date_from),
                       date_to=COALESCE(excluded.date_to,research_scope_targets.date_to),
                       notes=COALESCE(excluded.notes,research_scope_targets.notes)""",
                (
                    target_key,
                    item["scope_id"],
                    target_type,
                    target_id,
                    _required(item.get("target_label", ""), "target_label"),
                    role,
                    _choice(
                        str(item.get("semantics", "research_context")),
                        VALID_SCOPE_SEMANTICS,
                        "semantics",
                    ),
                    item.get("date_from"),
                    item.get("date_to"),
                    item.get("notes"),
                    now,
                ),
            )
        for item in manifest.get("question_memberships", []):
            membership_id = stable_id(
                "research_question_scope", item["question_id"], item["scope_id"]
            )
            primary = int(bool(item.get("is_primary", False)))
            role = "primary" if primary else _choice(
                str(item.get("role", "supporting")),
                VALID_QUESTION_SCOPE_ROLES,
                "role",
            )
            if primary:
                connection.execute(
                    """UPDATE research_question_scopes
                          SET is_primary=0,
                              role=CASE WHEN role='primary' THEN 'supporting' ELSE role END
                        WHERE question_id=? AND id<>?""",
                    (item["question_id"], membership_id),
                )
            connection.execute(
                """INSERT INTO research_question_scopes(
                       id,question_id,scope_id,role,is_primary,notes,created_at
                   ) VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(question_id,scope_id) DO UPDATE SET
                       role=excluded.role,is_primary=excluded.is_primary,
                       notes=COALESCE(excluded.notes,research_question_scopes.notes)""",
                (
                    membership_id,
                    item["question_id"],
                    item["scope_id"],
                    role,
                    primary,
                    item.get("notes"),
                    now,
                ),
            )
        for item in manifest.get("work_items", []):
            connection.execute(
                """INSERT INTO research_work_items(
                       id,scope_id,question_id,parent_work_item_id,item_type,title,
                       completion_criterion,status,priority,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                       scope_id=COALESCE(excluded.scope_id,research_work_items.scope_id),
                       question_id=COALESCE(excluded.question_id,research_work_items.question_id),
                       parent_work_item_id=COALESCE(
                           excluded.parent_work_item_id,research_work_items.parent_work_item_id
                       ),item_type=excluded.item_type,title=excluded.title,
                       completion_criterion=COALESCE(
                           excluded.completion_criterion,research_work_items.completion_criterion
                       ),status=excluded.status,priority=excluded.priority,
                       updated_at=excluded.updated_at""",
                (
                    _required(item.get("id", ""), "work_item.id"),
                    item.get("scope_id"),
                    item.get("question_id"),
                    item.get("parent_work_item_id"),
                    _choice(str(item.get("item_type")), VALID_WORK_ITEM_TYPES, "item_type"),
                    _required(item.get("title", ""), "work_item.title"),
                    item.get("completion_criterion"),
                    _choice(
                        str(item.get("status", "planned")),
                        VALID_WORK_ITEM_STATUSES,
                        "status",
                    ),
                    int(item.get("priority", 100)),
                    now,
                    now,
                ),
            )
        fingerprint = preview["fingerprint"]
        already_audited = connection.execute(
            """SELECT 1 FROM audit_log
                WHERE action='research_scope_manifest_apply'
                  AND object_type='research_scope_manifest' AND object_id=?""",
            (fingerprint,),
        ).fetchone()
        if already_audited is None:
            connection.execute(
                """INSERT INTO audit_log(
                       occurred_at,actor,action,object_type,object_id,after_json,rationale
                   ) VALUES(?,?,?,?,?,?,?)""",
                (
                    now,
                    "main-curator",
                    "research_scope_manifest_apply",
                    "research_scope_manifest",
                    fingerprint,
                    _canonical_json(preview["counts"]),
                    "Jawny, addytywny import organizacji badań; bez zmiany statusów dowodowych.",
                ),
            )
    return {**preview, "mode": "applied", "applied": True}


__all__ = [
    "apply_research_scope_manifest",
    "create_research_scope",
    "create_research_work_item",
    "link_question_scope",
    "link_research_scope_target",
    "link_research_scopes",
    "preview_research_scope_manifest",
    "research_scope_tree",
]
