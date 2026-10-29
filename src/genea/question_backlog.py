from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import nullcontext
from pathlib import Path
from typing import Any, ContextManager, Iterable

from .question_assessments import assessment_candidate, latest_question_assessment
from .storage import configured_database_backend
from .workspace import search_fold, stable_id


DEFAULT_RULES_PATH = Path("research/rules/questions/rules.v1.json")
DEFAULT_SCOPE_PATH = Path("research/rules/questions/scope-profiles.v1.json")
REPORT_SCHEMA_VERSION = 1
BACKLOG_SCHEMA_VERSION = 2
_PRIVATE_CLASSES = {"living", "restricted", "family_private", "strict_private"}
WORKFLOW_STAGES = (
    "detected",
    "triage",
    "ready",
    "in_progress",
    "blocked",
    "correlation_review",
    "answered",
    "dismissed",
)

_TOKEN_STOPWORDS = {
    "akt",
    "albo",
    "byli",
    "bylo",
    "czy",
    "dla",
    "gdzie",
    "i",
    "jak",
    "jaki",
    "jakie",
    "kiedy",
    "ktore",
    "oraz",
    "osoba",
    "osoby",
    "roku",
    "sie",
    "tego",
    "w",
    "z",
    "zrodlo",
    "zrodla",
}


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise FileNotFoundError(f"Nie znaleziono pliku {label}: {path}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"Plik {label} musi zawierać obiekt JSON")
    return payload


def _resolve_config_path(root: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value).expanduser() if value is not None else default
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def _display_path(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _readonly_connection(root: Path) -> sqlite3.Connection:
    return configured_database_backend(root).connect(readonly=True)


def _connection_scope(
    root: Path,
    connection: sqlite3.Connection | None,
) -> ContextManager[sqlite3.Connection]:
    """Use an injected read-only connection without taking ownership of it."""

    return (
        nullcontext(connection)
        if connection is not None
        else _readonly_connection(root)
    )


def _require_tables(connection: sqlite3.Connection, names: Iterable[str]) -> None:
    existing = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    missing = sorted(set(names) - existing)
    if missing:
        raise RuntimeError("Baza nie zawiera wymaganych tabel: " + ", ".join(missing))


def _validate_rules(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if payload.get("schema_version") != 1:
        raise ValueError("Nieobsługiwana schema_version reguł")
    if not isinstance(payload.get("rule_set_version"), str):
        raise ValueError("Reguły wymagają rule_set_version")
    rules = payload.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ValueError("Reguły wymagają niepustej listy rules")
    supported = {
        "missing_required_event",
        "missing_or_unresolved_parents",
        "open_date_conflict",
        "incomplete_search_coverage",
        "external_network_direct_ancestor_unresolved_identity",
        "external_network_unclassified",
        "external_network_conflict",
    }
    seen: set[tuple[str, int]] = set()
    for rule in rules:
        if not isinstance(rule, dict):
            raise ValueError("Każda reguła musi być obiektem JSON")
        identity = (str(rule.get("id", "")), int(rule.get("version", 0)))
        if not identity[0] or identity[1] < 1:
            raise ValueError("Każda reguła wymaga id i dodatniej version")
        if identity in seen:
            raise ValueError(f"Powtórzona reguła: {identity[0]} v{identity[1]}")
        if rule.get("kind") not in supported:
            raise ValueError(f"Nieobsługiwany rodzaj reguły: {rule.get('kind')}")
        if rule.get("kind") == "incomplete_search_coverage":
            window_years = rule.get("window_years")
            if not isinstance(window_years, int) or isinstance(window_years, bool) or window_years < 1:
                raise ValueError("Reguła coverage wymaga dodatniego window_years")
            priority_step = rule.get("priority_step_per_window", 0)
            if (
                not isinstance(priority_step, int)
                or isinstance(priority_step, bool)
                or priority_step < 0
            ):
                raise ValueError(
                    "priority_step_per_window musi być nieujemną liczbą całkowitą"
                )
            aggregate_record_types = rule.get("aggregate_record_types", [])
            if not isinstance(aggregate_record_types, list) or not all(
                isinstance(value, str) and value.strip()
                for value in aggregate_record_types
            ):
                raise ValueError("aggregate_record_types musi być listą nazw")
        if str(rule.get("kind", "")).startswith("external_network_"):
            for field in ("question_template", "completion_criterion_template"):
                if not isinstance(rule.get(field), str) or not rule[field].strip():
                    raise ValueError(
                        f"Reguła zewnętrznej sieci wymaga pola {field}"
                    )
        seen.add(identity)
    return sorted(rules, key=lambda row: (int(row["priority"]), row["id"], row["version"]))


def _validate_scope(payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != 1:
        raise ValueError("Nieobsługiwana schema_version profili")
    profiles = payload.get("profiles")
    if not isinstance(profiles, dict):
        raise ValueError("Profile wymagają obiektu profiles")
    for required in ("direct_ancestor", "fan_limited"):
        if required not in profiles:
            raise ValueError(f"Brakuje wymaganego profilu: {required}")
    if not isinstance(payload.get("assignments", []), list):
        raise ValueError("assignments musi być listą")
    if not isinstance(payload.get("pilot_documents", []), list):
        raise ValueError("pilot_documents musi być listą")


def _format(template: str, values: dict[str, Any]) -> str:
    try:
        return template.format_map(values)
    except KeyError as error:
        raise ValueError(f"Brak wartości szablonu: {error.args[0]}") from error


def _candidate(
    rule: dict[str, Any],
    *,
    case_id: str,
    subject_type: str,
    subject_id: str,
    subject_label: str,
    gap_key: str,
    question_text: str,
    completion_criterion: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    key_parts = {
        "rule_id": str(rule["id"]),
        "rule_version": int(rule["version"]),
        "subject_type": subject_type,
        "subject_id": subject_id,
        "gap_key": gap_key,
    }
    candidate_key = _fingerprint(key_parts)
    return {
        "id": stable_id("question_candidate", candidate_key),
        "candidate_key": candidate_key,
        "rule_id": rule["id"],
        "rule_version": rule["version"],
        "case_id": case_id,
        "question_type": rule["question_type"],
        "subject_type": subject_type,
        "subject_id": subject_id,
        "subject_label": subject_label,
        "gap_key": gap_key,
        "question": question_text,
        "completion_criterion": completion_criterion,
        "priority": int(rule["priority"]),
        "evidence_fingerprint": _fingerprint(evidence),
        "evidence": evidence,
    }


def _case_ids(connection: sqlite3.Connection, requested: str | None) -> list[str]:
    if requested is not None:
        row = connection.execute(
            "SELECT id FROM research_cases WHERE id = ?", (requested,)
        ).fetchone()
        if row is None:
            raise LookupError(f"Nie znaleziono sprawy: {requested}")
        return [requested]
    return [
        row[0]
        for row in connection.execute("SELECT id FROM research_cases ORDER BY id")
    ]


def _research_scope_projection(
    connection: sqlite3.Connection,
    question_ids: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    scope_rows = connection.execute(
        """SELECT id,scope_type,title,summary,status,legacy_case_id
             FROM research_scopes
            ORDER BY scope_type,title,id"""
    ).fetchall()
    scope_by_id = {row["id"]: dict(row) for row in scope_rows}
    edge_rows = connection.execute(
        """SELECT parent_scope_id,child_scope_id,relation_type,is_primary,
                  sort_order,notes
             FROM research_scope_edges
            ORDER BY relation_type,parent_scope_id,sort_order,child_scope_id"""
    ).fetchall()
    primary_parent = {
        row["child_scope_id"]: row["parent_scope_id"]
        for row in edge_rows
        if row["relation_type"] == "contains" and row["is_primary"]
    }

    def path_for(scope_id: str) -> list[dict[str, str]]:
        path: list[dict[str, str]] = []
        current = scope_id
        seen: set[str] = set()
        while current in scope_by_id and current not in seen:
            seen.add(current)
            scope = scope_by_id[current]
            path.append(
                {
                    "id": current,
                    "title": scope["title"],
                    "type": scope["scope_type"],
                }
            )
            current = primary_parent.get(current, "")
        return list(reversed(path))

    targets_by_scope: dict[str, list[dict[str, Any]]] = {
        scope_id: [] for scope_id in scope_by_id
    }
    for row in connection.execute(
        """SELECT scope_id,target_type,target_id,target_label,association_role,
                  semantics,date_from,date_to,notes
             FROM research_scope_targets
            ORDER BY scope_id,target_type,target_label,target_id"""
    ):
        targets_by_scope.setdefault(row["scope_id"], []).append(
            {
                "type": row["target_type"],
                "id": row["target_id"],
                "label": row["target_label"],
                "role": row["association_role"],
                "semantics": row["semantics"],
                "dateFrom": row["date_from"],
                "dateTo": row["date_to"],
                "notes": row["notes"],
            }
        )

    scopes = [
        {
            "id": row["id"],
            "type": row["scope_type"],
            "title": row["title"],
            "summary": row["summary"],
            "status": row["status"],
            "legacyCaseId": row["legacy_case_id"],
            "primaryPath": path_for(row["id"]),
            "targets": targets_by_scope.get(row["id"], []),
        }
        for row in scope_rows
    ]
    edges = [
        {
            "parentScopeId": row["parent_scope_id"],
            "childScopeId": row["child_scope_id"],
            "relationType": row["relation_type"],
            "primary": bool(row["is_primary"]),
            "sortOrder": row["sort_order"],
            "notes": row["notes"],
        }
        for row in edge_rows
    ]
    memberships: dict[str, list[dict[str, Any]]] = {
        question_id: [] for question_id in question_ids
    }
    if question_ids:
        placeholders = ",".join("?" for _ in question_ids)
        for row in connection.execute(
            f"""SELECT qs.question_id,qs.scope_id,qs.role,qs.is_primary,qs.notes,
                       s.scope_type,s.title,s.status,s.legacy_case_id
                  FROM research_question_scopes qs
                  JOIN research_scopes s ON s.id=qs.scope_id
                 WHERE qs.question_id IN ({placeholders})
                 ORDER BY qs.question_id,qs.is_primary DESC,qs.role,s.title,s.id""",
            tuple(question_ids),
        ):
            memberships[row["question_id"]].append(
                {
                    "id": row["scope_id"],
                    "type": row["scope_type"],
                    "title": row["title"],
                    "status": row["status"],
                    "legacyCaseId": row["legacy_case_id"],
                    "role": row["role"],
                    "primary": bool(row["is_primary"]),
                    "notes": row["notes"],
                    "primaryPath": path_for(row["scope_id"]),
                    "targets": targets_by_scope.get(row["scope_id"], []),
                }
            )
    return scopes, edges, memberships


def _missing_required_event(
    connection: sqlite3.Connection,
    rule: dict[str, Any],
    assignments: list[dict[str, Any]],
    case_ids: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    event_types = tuple(sorted(set(rule["event_types"])))
    subject_roles = {search_fold(value) for value in rule["subject_roles"]}
    applicable_profiles = set(rule["profiles"])

    for assignment in sorted(
        assignments,
        key=lambda row: (
            row.get("case_id", ""),
            row.get("subject_type", ""),
            row.get("subject_id", ""),
        ),
    ):
        if assignment.get("case_id") not in case_ids:
            continue
        if assignment.get("subject_type") != "person":
            continue
        subject_id = str(assignment["subject_id"])
        profile = str(assignment["profile"])
        decision_base = {
            "rule_id": rule["id"],
            "case_id": assignment["case_id"],
            "subject_type": "person",
            "subject_id": subject_id,
            "profile": profile,
        }
        if profile not in applicable_profiles:
            decisions.append(
                {
                    **decision_base,
                    "outcome": "suppressed",
                    "reason": "profile_not_applicable",
                }
            )
            continue
        person = connection.execute(
            "SELECT id, preferred_name, status FROM persons WHERE id = ?", (subject_id,)
        ).fetchone()
        if person is None:
            decisions.append(
                {
                    **decision_base,
                    "outcome": "suppressed",
                    "reason": "subject_not_found",
                }
            )
            continue
        placeholders = ",".join("?" for _ in event_types)
        rows = connection.execute(
            f"""SELECT DISTINCT e.id, e.event_type, e.date_text, pa.role,
                              po.id AS occurrence_id, si.id AS source_item_id
                   FROM person_occurrences po
                   JOIN participations pa ON pa.occurrence_id = po.id
                   JOIN events e ON e.id = pa.event_id
                   LEFT JOIN source_items si ON si.id = e.source_item_id
                  WHERE po.person_id = ?
                    AND e.event_type IN ({placeholders})
                  ORDER BY e.id, pa.role""",
            (subject_id, *event_types),
        ).fetchall()
        matching = [
            dict(row)
            for row in rows
            if any(
                required_role == search_fold(row["role"])
                or required_role in search_fold(row["role"])
                for required_role in subject_roles
            )
        ]
        if matching:
            decisions.append(
                {
                    **decision_base,
                    "outcome": "satisfied",
                    "reason": "required_event_present",
                    "event_ids": sorted({row["id"] for row in matching}),
                }
            )
            continue
        label = assignment.get("label") or person["preferred_name"] or subject_id
        evidence = {
            "profile": profile,
            "person": dict(person),
            "checked_event_types": list(event_types),
            "checked_subject_roles": sorted(subject_roles),
            "matching_events": [],
        }
        values = {"subject_label": label}
        item = _candidate(
            rule,
            case_id=assignment["case_id"],
            subject_type="person",
            subject_id=subject_id,
            subject_label=label,
            gap_key=rule["gap_key"],
            question_text=_format(rule["question_template"], values),
            completion_criterion=_format(rule["completion_criterion_template"], values),
            evidence=evidence,
        )
        candidates.append(item)
        decisions.append(
            {
                **decision_base,
                "outcome": "emitted",
                "reason": "required_event_missing",
                "candidate_id": item["id"],
            }
        )
    return candidates, decisions


def _tree_parent_state(
    tree: dict[str, Any] | None,
    *,
    person_id: str,
    occurrence_ids: set[str],
) -> dict[str, Any]:
    if not tree:
        return {"families": [], "documented": False}
    tree_people = [item for item in tree.get("people", []) if isinstance(item, dict)]
    tree_ids = {
        str(item.get("id"))
        for item in tree_people
        if item.get("canonicalPersonId") == person_id
        or bool(occurrence_ids & set(item.get("occurrenceIds", [])))
    }
    families = [
        item
        for item in tree.get("families", [])
        if isinstance(item, dict)
        and tree_ids.intersection(item.get("childIds", []))
        and item.get("parentIds")
    ]
    documented_statuses = {"confirmed", "documented", "agent_approved"}
    return {
        "families": [
            {
                "id": item.get("id"),
                "parentIds": sorted(item.get("parentIds", [])),
                "status": item.get("status"),
                "sourceIds": sorted(item.get("sourceIds", [])),
            }
            for item in families
        ],
        "documented": any(
            search_fold(str(item.get("status") or "")) in documented_statuses
            and len(item.get("parentIds", [])) >= 1
            for item in families
        ),
    }


def _missing_or_unresolved_parents(
    connection: sqlite3.Connection,
    rule: dict[str, Any],
    assignments: list[dict[str, Any]],
    case_ids: set[str],
    tree: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    applicable_profiles = set(rule["profiles"])
    parent_predicates = {search_fold(value).replace("_", "-") for value in rule["parent_predicates"]}

    for assignment in sorted(
        assignments,
        key=lambda row: (row.get("case_id", ""), row.get("subject_type", ""), row.get("subject_id", "")),
    ):
        if assignment.get("case_id") not in case_ids or assignment.get("subject_type") != "person":
            continue
        person_id = str(assignment["subject_id"])
        profile = str(assignment["profile"])
        decision_base = {
            "rule_id": rule["id"],
            "case_id": assignment["case_id"],
            "subject_type": "person",
            "subject_id": person_id,
            "profile": profile,
        }
        if profile not in applicable_profiles:
            decisions.append({**decision_base, "outcome": "suppressed", "reason": "profile_not_applicable"})
            continue
        person = connection.execute(
            "SELECT id,preferred_name,status FROM persons WHERE id=?", (person_id,)
        ).fetchone()
        if person is None:
            decisions.append({**decision_base, "outcome": "suppressed", "reason": "subject_not_found"})
            continue
        occurrence_rows = connection.execute(
            "SELECT id,raw_name,normalized_name FROM person_occurrences WHERE person_id=? ORDER BY id",
            (person_id,),
        ).fetchall()
        occurrence_ids = {row["id"] for row in occurrence_rows}
        tree_state = _tree_parent_state(tree, person_id=person_id, occurrence_ids=occurrence_ids)
        if tree_state["documented"]:
            decisions.append({**decision_base, "outcome": "satisfied", "reason": "documented_tree_parent_relation"})
            continue

        assertion_rows = connection.execute(
            """SELECT sa.id,sa.source_item_id,sa.subject_ref,sa.predicate,sa.object_text,
                      sa.reading_certainty,sa.verification_status,sa.metadata_json,
                      subject_ref.object_id AS subject_occurrence_id,
                      object_ref.object_id AS object_occurrence_id,
                      subject.person_id AS subject_person_id,
                      subject.raw_name AS subject_name,
                      object.person_id AS object_person_id,
                      object.raw_name AS object_name
                 FROM source_assertions sa
                 LEFT JOIN source_assertion_references subject_ref
                   ON subject_ref.assertion_id=sa.id
                  AND subject_ref.position='subject'
                  AND subject_ref.object_type='person_occurrence'
                 LEFT JOIN source_assertion_references object_ref
                   ON object_ref.assertion_id=sa.id
                  AND object_ref.position='object'
                  AND object_ref.object_type='person_occurrence'
                 LEFT JOIN person_occurrences subject ON subject.id=subject_ref.object_id
                 LEFT JOIN person_occurrences object ON object.id=object_ref.object_id
                WHERE sa.assertion_type='source-stated-relationship'
                  AND sa.verification_status NOT IN ('retracted','superseded')
                  AND (subject_ref.object_id IN ({placeholders}) OR object_ref.object_id IN ({placeholders}))
                ORDER BY sa.source_item_id,sa.id""".format(
                    placeholders=",".join("?" for _ in occurrence_ids) or "NULL"
                ),
            (*sorted(occurrence_ids), *sorted(occurrence_ids)),
        ).fetchall() if occurrence_ids else []
        parent_assertions: list[dict[str, Any]] = []
        for row in assertion_rows:
            predicate = search_fold(str(row["predicate"] or "")).replace("_", "-")
            if predicate not in parent_predicates:
                continue
            current_is_subject = row["subject_occurrence_id"] in occurrence_ids
            current_is_object = row["object_occurrence_id"] in occurrence_ids
            parent_is_other = (
                (predicate in {"child-of", "child-of-father", "child-of-mother"} and current_is_subject)
                or (predicate in {"father-of", "mother-of", "parent-of"} and current_is_object)
            )
            if not parent_is_other:
                continue
            item = dict(row)
            item["related_occurrence_id"] = (
                row["object_occurrence_id"] if current_is_subject else row["subject_occurrence_id"]
            )
            item["related_name"] = row["object_name"] if current_is_subject else row["subject_name"]
            item["related_person_id"] = row["object_person_id"] if current_is_subject else row["subject_person_id"]
            parent_assertions.append(item)

        label = assignment.get("label") or person["preferred_name"] or person_id
        state = "source_relation_unresolved" if parent_assertions else "parent_relation_missing"
        values = {"subject_label": label}
        evidence = {
            "profile": profile,
            "person": dict(person),
            "tree_parent_state": tree_state,
            "source_parent_assertions": parent_assertions,
            "state": state,
        }
        question_template = (
            rule["unresolved_question_template"]
            if parent_assertions
            else rule["missing_question_template"]
        )
        item = _candidate(
            rule,
            case_id=assignment["case_id"],
            subject_type="person",
            subject_id=person_id,
            subject_label=label,
            gap_key=f"parentage:{state}",
            question_text=_format(question_template, values),
            completion_criterion=_format(rule["completion_criterion_template"], values),
            evidence=evidence,
        )
        candidates.append(item)
        decisions.append({
            **decision_base,
            "outcome": "emitted",
            "reason": state,
            "candidate_id": item["id"],
            "source_assertion_ids": [row["id"] for row in parent_assertions],
        })
    return candidates, decisions


def _open_date_conflicts(
    connection: sqlite3.Connection,
    rule: dict[str, Any],
    case_ids: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    keywords = tuple(search_fold(value) for value in rule["keywords"])
    for case_id in sorted(case_ids):
        rows = connection.execute(
            """SELECT c.id, c.title, c.description, c.status, c.claim_id,
                      cl.statement, cl.subject_ref
                 FROM conflicts c
                 JOIN claims cl ON cl.id = c.claim_id
                WHERE cl.case_id = ? AND c.status = 'open'
                ORDER BY c.id""",
            (case_id,),
        ).fetchall()
        for row in rows:
            folded = search_fold(row["title"])
            decision_base = {
                "rule_id": rule["id"],
                "case_id": case_id,
                "subject_type": "conflict",
                "subject_id": row["id"],
            }
            matched_keywords = sorted({keyword for keyword in keywords if keyword in folded})
            if not matched_keywords:
                decisions.append(
                    {
                        **decision_base,
                        "outcome": "suppressed",
                        "reason": "not_a_date_conflict",
                    }
                )
                continue
            evidence = {**dict(row), "matched_keywords": matched_keywords}
            values = {"conflict_title": row["title"]}
            item = _candidate(
                rule,
                case_id=case_id,
                subject_type="conflict",
                subject_id=row["id"],
                subject_label=row["title"],
                gap_key=f"open-date-conflict:{row['id']}",
                question_text=_format(rule["question_template"], values),
                completion_criterion=_format(
                    rule["completion_criterion_template"], values
                ),
                evidence=evidence,
            )
            candidates.append(item)
            decisions.append(
                {
                    **decision_base,
                    "outcome": "emitted",
                    "reason": "open_date_conflict",
                    "candidate_id": item["id"],
                }
            )
    return candidates, decisions


def _five_year_windows(
    year_from: int | None,
    year_to: int | None,
    window_years: int,
) -> list[tuple[int, int]]:
    """Split an inclusive year range into stable consecutive windows."""

    if year_from is None or year_to is None or year_to < year_from:
        return []
    windows: list[tuple[int, int]] = []
    start = year_from
    while start <= year_to:
        end = min(year_to, start + window_years - 1)
        windows.append((start, end))
        start = end + 1
    return windows


def _incomplete_search_coverage(
    connection: sqlite3.Connection,
    rule: dict[str, Any],
    case_ids: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    complete_statuses = set(rule["complete_statuses"])
    window_years = int(rule["window_years"])
    priority_step = int(rule.get("priority_step_per_window", 0))
    aggregate_record_types = {
        search_fold(value) for value in rule.get("aggregate_record_types", [])
    }
    for case_id in sorted(case_ids):
        rows = connection.execute(
            """SELECT psc.id, psc.study_id, ps.title AS study_title,
                      psc.record_type, psc.coverage_status, psc.completeness,
                      psc.year_from, psc.year_to, psc.notes
                 FROM place_study_coverage psc
                 JOIN place_studies ps ON ps.id = psc.study_id
                WHERE ps.case_id = ?
                ORDER BY psc.study_id, psc.year_from, psc.year_to,
                         psc.record_type, psc.id""",
            (case_id,),
        ).fetchall()
        for row in rows:
            decision_base = {
                "rule_id": rule["id"],
                "case_id": case_id,
                "subject_type": "place_study_coverage",
                "subject_id": row["id"],
            }
            complete = row["coverage_status"] in complete_statuses
            if complete:
                decisions.append(
                    {
                        **decision_base,
                        "outcome": "satisfied",
                        "reason": "coverage_complete",
                    }
                )
                continue
            if search_fold(row["record_type"]) in aggregate_record_types:
                decisions.append(
                    {
                        **decision_base,
                        "outcome": "suppressed",
                        "reason": "aggregate_record_type",
                    }
                )
                continue
            windows = _five_year_windows(
                row["year_from"], row["year_to"], window_years
            )
            if not windows:
                decisions.append(
                    {
                        **decision_base,
                        "outcome": "suppressed",
                        "reason": "invalid_or_missing_year_range",
                    }
                )
                continue
            for window_index, (year_from, year_to) in enumerate(windows):
                window_target_id = (
                    f"coverage-window:{row['id']}:{year_from}-{year_to}"
                )
                subject_label = (
                    f"{row['study_title']} — {row['record_type']}, "
                    f"{year_from}–{year_to}"
                )
                values = {
                    "study_title": row["study_title"],
                    "record_type": row["record_type"],
                    "year_from": year_from,
                    "year_to": year_to,
                }
                evidence = {
                    "study_id": row["study_id"],
                    "study_title": row["study_title"],
                    "coverage_record": dict(row),
                    "window": {
                        "year_from": year_from,
                        "year_to": year_to,
                        "window_years": window_years,
                        "index": window_index,
                    },
                }
                item = _candidate(
                    rule,
                    case_id=case_id,
                    subject_type="other",
                    subject_id=window_target_id,
                    subject_label=subject_label,
                    gap_key=window_target_id,
                    question_text=_format(rule["question_template"], values),
                    completion_criterion=_format(
                        rule["completion_criterion_template"], values
                    ),
                    evidence=evidence,
                )
                item["priority"] = int(rule["priority"]) + (
                    window_index * priority_step
                )
                item["window_year_from"] = year_from
                item["window_year_to"] = year_to
                candidates.append(item)
                decisions.append(
                    {
                        **decision_base,
                        "subject_type": "place_study_coverage_window",
                        "subject_id": window_target_id,
                        "source_coverage_id": row["id"],
                        "outcome": "emitted",
                        "reason": "coverage_window_incomplete",
                        "candidate_id": item["id"],
                    }
                )
    return candidates, decisions


def _external_network_case_ids(
    connection: sqlite3.Connection,
    occurrence_ids: list[str],
    selected_cases: set[str],
) -> list[str]:
    """Resolve network scope from canonical provenance, never from UI labels."""

    if not occurrence_ids or not selected_cases:
        return []
    occurrence_marks = ",".join("?" for _ in occurrence_ids)
    case_marks = ",".join("?" for _ in selected_cases)
    parameters = (*sorted(selected_cases), *sorted(occurrence_ids))
    case_ids = {
        str(row[0])
        for row in connection.execute(
            f"""SELECT DISTINCT case_id
                   FROM source_reported_network_anchors
                  WHERE case_id IN ({case_marks})
                    AND anchor_occurrence_id IN ({occurrence_marks})""",
            parameters,
        )
    }
    claim_parameters = (
        *sorted(selected_cases),
        *sorted(occurrence_ids),
        *sorted(occurrence_ids),
    )
    case_ids.update(
        str(row[0])
        for row in connection.execute(
            f"""SELECT DISTINCT case_id
                   FROM claims
                  WHERE case_id IN ({case_marks})
                    AND (subject_ref IN ({occurrence_marks})
                         OR object_ref IN ({occurrence_marks}))""",
            claim_parameters,
        )
    )
    return sorted(case_ids)


def _identity_gap_evidence(
    connection: sqlite3.Connection,
    *,
    person_id: str,
    occurrence_id: str,
) -> tuple[list[dict[str, Any]], set[str]]:
    """Return unresolved identity candidates and all references for privacy QA."""

    person_occurrences = {
        str(row[0])
        for row in connection.execute(
            "SELECT id FROM person_occurrences WHERE person_id=? ORDER BY id",
            (person_id,),
        )
    }
    person_occurrences.add(occurrence_id)
    marks = ",".join("?" for _ in person_occurrences)
    rows = [
        dict(row)
        for row in connection.execute(
            f"""SELECT id,occurrence_a_id,occurrence_b_id,status
                   FROM identity_candidates
                  WHERE status='unresolved'
                    AND (occurrence_a_id IN ({marks})
                         OR occurrence_b_id IN ({marks}))
                  ORDER BY id""",
            (*sorted(person_occurrences), *sorted(person_occurrences)),
        )
    ]
    references = {person_id, occurrence_id}
    for row in rows:
        references.update((str(row["occurrence_a_id"]), str(row["occurrence_b_id"])))
    if references:
        reference_marks = ",".join("?" for _ in references)
        references.update(
            str(row[0])
            for row in connection.execute(
                f"""SELECT DISTINCT person_id
                       FROM person_occurrences
                      WHERE id IN ({reference_marks}) AND person_id IS NOT NULL
                      ORDER BY person_id""",
                tuple(sorted(references)),
            )
        )
    return rows, references


def _private_references(
    connection: sqlite3.Connection, references: set[str]
) -> list[dict[str, str]]:
    if not references:
        return []
    marks = ",".join("?" for _ in references)
    private_marks = ",".join("?" for _ in _PRIVATE_CLASSES)
    return [
        {
            "object_type": str(row["object_type"]),
            "object_id": str(row["object_id"]),
            "classification": str(row["classification"]),
        }
        for row in connection.execute(
            f"""SELECT object_type,object_id,classification
                   FROM privacy_labels
                  WHERE object_id IN ({marks})
                    AND classification IN ({private_marks})
                  ORDER BY object_type,object_id,classification""",
            (*sorted(references), *sorted(_PRIVATE_CLASSES)),
        )
    ]


def _classification_snapshot(classification: dict[str, Any]) -> dict[str, Any]:
    alternatives: list[dict[str, Any]] = []
    for alternative in classification.get("alternatives") or []:
        if not isinstance(alternative, dict):
            continue
        ordered_path = [
            {
                "relationshipId": str(step["relationshipId"]),
                "direction": str(step["direction"]),
            }
            for step in alternative.get("orderedPath") or []
            if isinstance(step, dict)
            and isinstance(step.get("relationshipId"), str)
            and isinstance(step.get("direction"), str)
        ]
        alternatives.append(
            {
                "role": alternative.get("role"),
                "basis": alternative.get("basis"),
                "orderedPath": ordered_path,
                "backingRelationshipIds": sorted(
                    str(value)
                    for value in alternative.get("backingRelationshipIds") or []
                    if isinstance(value, str)
                ),
            }
        )
    return {
        "state": classification.get("state"),
        "primaryRole": classification.get("primaryRole"),
        "alternatives": sorted(
            alternatives,
            key=lambda item: (
                str(item["role"]),
                _canonical_json(item["orderedPath"]),
                _canonical_json(item["backingRelationshipIds"]),
            ),
        ),
        "unclassifiedReason": classification.get("unclassifiedReason"),
    }


def _external_network_backing_by_tree_node(
    connection: sqlite3.Connection,
) -> dict[str, dict[str, str]]:
    """Resolve public node IDs internally without exporting backing IDs."""

    return {
        stable_id("source_reported_tree_node", str(row["occurrence_id"])): {
            "personId": str(row["person_id"]),
            "occurrenceId": str(row["occurrence_id"]),
        }
        for row in connection.execute(
            """SELECT po.id AS occurrence_id,po.person_id
                 FROM person_occurrences po
                 JOIN persons p ON p.id=po.person_id
                 JOIN source_items si ON si.id=po.source_item_id
                WHERE p.status='candidate'
                  AND si.item_type='external_tree_profile'
                  AND po.verification_status NOT IN ('retracted','superseded')
                ORDER BY po.id"""
        )
    }


def _external_network_gaps(
    connection: sqlite3.Connection,
    rule: dict[str, Any],
    selected_cases: set[str],
    tree_payload: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Propose research gaps from the privacy-filtered external-tree projection."""

    candidates: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    networks = (
        tree_payload.get("sourceReportedNetworks", [])
        if isinstance(tree_payload, dict)
        else []
    )
    if not isinstance(networks, list):
        return candidates, decisions
    backing_by_tree_node = _external_network_backing_by_tree_node(connection)

    for network in sorted(
        (item for item in networks if isinstance(item, dict)),
        key=lambda item: str(item.get("id", "")),
    ):
        network_id = network.get("id")
        nodes = network.get("nodes")
        if not isinstance(network_id, str) or not isinstance(nodes, list):
            continue
        occurrence_ids = sorted(
            {
                backing_by_tree_node[str(node["treeNodeId"])]["occurrenceId"]
                for node in nodes
                if isinstance(node, dict)
                and isinstance(node.get("treeNodeId"), str)
                and str(node["treeNodeId"]) in backing_by_tree_node
            }
        )
        case_ids = _external_network_case_ids(
            connection, occurrence_ids, selected_cases
        )
        if not case_ids:
            continue
        for node in sorted(
            (item for item in nodes if isinstance(item, dict)),
            key=lambda item: str(item.get("treeNodeId", "")),
        ):
            tree_node_id = node.get("treeNodeId")
            backing = backing_by_tree_node.get(str(tree_node_id))
            classification = node.get("classification")
            safe_subject_id = (
                str(tree_node_id) if isinstance(tree_node_id, str) else "unknown-node"
            )
            decision_subject = {
                "rule_id": rule["id"],
                "subject_type": "external_network_node",
                "subject_id": safe_subject_id,
                "network_id": network_id,
            }
            for resolved_case_id in case_ids:
                decision_base = {"case_id": resolved_case_id, **decision_subject}
                if network.get("privacyFiltered") is not True:
                    decisions.append(
                        {
                            **decision_base,
                            "outcome": "suppressed",
                            "reason": "network_not_privacy_filtered",
                        }
                    )
                    continue
                if not all(
                    isinstance(value, str) and value
                    for value in (
                        tree_node_id,
                        node.get("publicProfileId"),
                        node.get("personId"),
                        node.get("occurrenceId"),
                    )
                ) or not isinstance(classification, dict) or not (
                    node.get("publicProfileId")
                    == node.get("personId")
                    == node.get("occurrenceId")
                    == tree_node_id
                ) or backing is None:
                    decisions.append(
                        {
                            **decision_base,
                            "outcome": "suppressed",
                            "reason": "incomplete_projection_contract",
                        }
                    )
                    continue

                person_id = backing["personId"]
                occurrence_id = backing["occurrenceId"]

                identity_rows, privacy_references = _identity_gap_evidence(
                    connection,
                    person_id=person_id,
                    occurrence_id=occurrence_id,
                )
                blocked = _private_references(connection, privacy_references)
                if blocked:
                    decisions.append(
                        {
                            **decision_base,
                            "outcome": "suppressed",
                            "reason": "privacy_blocked",
                            "blocked_reference_count": len(blocked),
                        }
                    )
                    continue

                state = classification.get("state")
                role = classification.get("primaryRole")
                identity_status = node.get("identityStatus")
                kind = rule["kind"]
                applicable = False
                rationale = ""
                if kind == "external_network_direct_ancestor_unresolved_identity":
                    applicable = (
                        state == "classified"
                        and role == "direct_ancestor"
                        and identity_status == "unresolved"
                    )
                    reason = (
                        "direct_ancestor_with_unresolved_identity"
                        if applicable
                        else "identity_not_unresolved"
                        if state == "classified" and role == "direct_ancestor"
                        else "classification_not_applicable"
                    )
                    rationale = (
                        "Drzewo zewnętrzne zgłasza ścieżkę przodka bezpośredniego, "
                        "a identity pozostaje nierozstrzygnięte; potrzebny jest "
                        "oryginalny akt, nie automatyczne połączenie."
                    )
                elif kind == "external_network_unclassified":
                    applicable = state == "unclassified"
                    reason = (
                        "external_network_unclassified"
                        if applicable
                        else "classification_not_applicable"
                    )
                    rationale = (
                        "Widoczny fragment sieci nie ma kompletnej, typowanej "
                        "ścieżki względem anchoru."
                    )
                else:
                    applicable = state == "conflict"
                    reason = (
                        "external_network_classification_conflict"
                        if applicable
                        else "classification_not_applicable"
                    )
                    rationale = (
                        "Sieć zewnętrzna daje konkurencyjne klasyfikacje; żadna "
                        "nie może zostać wybrana bez korelacji z oryginałem."
                    )
                if not applicable:
                    decisions.append(
                        {
                            **decision_base,
                            "outcome": "satisfied"
                            if reason == "identity_not_unresolved"
                            else "suppressed",
                            "reason": reason,
                        }
                    )
                    continue

                label = (
                    str(node.get("label"))
                    if isinstance(node.get("label"), str) and node.get("label")
                    else "Kandydat z drzewa zewnętrznego"
                )
                values = {"subject_label": label}
                evidence = {
                    "proposalStatus": "detected",
                    "triageRequired": True,
                    "automaticAction": None,
                    "sourceSemantics": "source_reported_external_tree_not_proof",
                    "rationale": rationale,
                    "privacyCheck": {
                        "status": "passed",
                        "basis": "privacy_filtered_projection_and_database_labels",
                        "checkedReferenceCount": len(privacy_references),
                    },
                    "network": {
                        "networkId": network_id,
                        "treeNodeId": tree_node_id,
                        "publicProfileId": tree_node_id,
                        "anchorState": network.get("anchorState"),
                        "identityStatus": identity_status,
                    },
                    "classification": _classification_snapshot(classification),
                    "unresolvedIdentityCandidateCount": len(identity_rows),
                }
                gap_key = f"{network_id}:{tree_node_id}:{kind}"
                item = _candidate(
                    rule,
                    case_id=resolved_case_id,
                    subject_type="external_network_node",
                    subject_id=tree_node_id,
                    subject_label=label,
                    gap_key=gap_key,
                    question_text=_format(rule["question_template"], values),
                    completion_criterion=_format(
                        rule["completion_criterion_template"], values
                    ),
                    evidence=evidence,
                )
                candidates.append(item)
                decisions.append(
                    {
                        **decision_base,
                        "outcome": "emitted",
                        "reason": reason,
                        "candidate_id": item["id"],
                    }
                )
    return candidates, decisions


def _audit_pilot_document(
    connection: sqlite3.Connection, specification: dict[str, Any]
) -> dict[str, Any]:
    source_item_id = str(specification["source_item_id"])
    row = connection.execute(
        """SELECT si.id, si.title, si.legacy_source_id,
                  COUNT(DISTINCT a.id) AS artifact_count,
                  COUNT(DISTINCT CASE
                      WHEN a.sha256 IS NOT NULL AND a.sha256 <> '' THEN a.id END
                  ) AS hashed_artifact_count,
                  COUNT(DISTINCT e.id) AS event_count,
                  COUNT(DISTINCT po.id) AS occurrence_count,
                  COUNT(DISTINCT pa.id) AS participation_count,
                  COUNT(DISTINCT CASE
                      WHEN po.transcript_segment_id IS NOT NULL THEN po.id END
                  ) AS segmented_occurrence_count
             FROM source_items si
             LEFT JOIN artifacts a ON a.source_item_id = si.id
             LEFT JOIN events e ON e.source_item_id = si.id
             LEFT JOIN person_occurrences po ON po.source_item_id = si.id
             LEFT JOIN participations pa
                    ON pa.occurrence_id = po.id AND pa.event_id = e.id
            WHERE si.id = ?
            GROUP BY si.id""",
        (source_item_id,),
    ).fetchone()
    if row is None:
        return {
            "case_id": specification["case_id"],
            "source_item_id": source_item_id,
            "label": specification.get("label", source_item_id),
            "chain_status": "missing_source_item",
            "missing_stages": ["source_item"],
        }
    result = dict(row)
    missing: list[str] = []
    if result["hashed_artifact_count"] < 1:
        missing.append("raw_sha256")
    if result["event_count"] < 1:
        missing.append("event")
    if result["occurrence_count"] < 1:
        missing.append("person_occurrence")
    if result["participation_count"] < 1:
        missing.append("participation")
    if result["segmented_occurrence_count"] < result["occurrence_count"]:
        missing.append("transcript_segment_links")
    return {
        "case_id": specification["case_id"],
        "source_item_id": source_item_id,
        "label": specification.get("label", result["title"]),
        "chain_status": "complete_linkage" if not missing else "partial_linkage",
        "missing_stages": missing,
        "counts": {
            "artifacts": result["artifact_count"],
            "hashed_artifacts": result["hashed_artifact_count"],
            "events": result["event_count"],
            "occurrences": result["occurrence_count"],
            "participations": result["participation_count"],
            "segmented_occurrences": result["segmented_occurrence_count"],
        },
        "legacy_source_id": result["legacy_source_id"],
        "title": result["title"],
    }


def generate_question_backlog(
    root: Path,
    *,
    case_id: str | None = None,
    rules_path: str | Path | None = None,
    scope_path: str | Path | None = None,
    connection: sqlite3.Connection | None = None,
    source_reported_networks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Generate an explainable backlog report without modifying SQLite."""

    root = root.resolve()
    resolved_rules = _resolve_config_path(root, rules_path, DEFAULT_RULES_PATH)
    resolved_scope = _resolve_config_path(root, scope_path, DEFAULT_SCOPE_PATH)
    rule_payload = _load_json(resolved_rules, "reguł")
    scope_payload = _load_json(resolved_scope, "profili i zakresu")
    rules = _validate_rules(rule_payload)
    _validate_scope(scope_payload)
    tree_path = root / "research" / "presentation" / "family-tree.json"
    tree_payload = _load_json(tree_path, "drzewa prezentacyjnego") if tree_path.is_file() else None
    external_network_payload = (
        {"sourceReportedNetworks": source_reported_networks}
        if source_reported_networks is not None
        else tree_payload
    )

    required_tables = {
        "artifacts",
        "claims",
        "conflicts",
        "events",
        "participations",
        "person_occurrences",
        "persons",
        "place_studies",
        "place_study_coverage",
        "privacy_labels",
        "research_cases",
        "source_reported_network_anchors",
        "source_items",
        "source_assertions",
        "identity_candidates",
    }
    with _connection_scope(root, connection) as connection:
        _require_tables(connection, required_tables)
        selected_cases = _case_ids(connection, case_id)
        case_set = set(selected_cases)
        candidates: list[dict[str, Any]] = []
        decisions: list[dict[str, Any]] = []
        for rule in rules:
            if rule["kind"] == "missing_required_event":
                emitted, evaluated = _missing_required_event(
                    connection,
                    rule,
                    list(scope_payload.get("assignments", [])),
                    case_set,
                )
            elif rule["kind"] == "missing_or_unresolved_parents":
                emitted, evaluated = _missing_or_unresolved_parents(
                    connection,
                    rule,
                    list(scope_payload.get("assignments", [])),
                    case_set,
                    tree_payload,
                )
            elif rule["kind"] == "open_date_conflict":
                emitted, evaluated = _open_date_conflicts(connection, rule, case_set)
            elif str(rule["kind"]).startswith("external_network_"):
                emitted, evaluated = _external_network_gaps(
                    connection, rule, case_set, external_network_payload
                )
            else:
                emitted, evaluated = _incomplete_search_coverage(
                    connection, rule, case_set
                )
            candidates.extend(emitted)
            decisions.extend(evaluated)

        pilot_documents = [
            _audit_pilot_document(connection, item)
            for item in sorted(
                scope_payload.get("pilot_documents", []),
                key=lambda row: (row.get("case_id", ""), row.get("source_item_id", "")),
            )
            if item.get("case_id") in case_set
        ]
        existing_question_counts = {
            row["case_id"]: row["question_count"]
            for row in connection.execute(
                f"""SELECT case_id, COUNT(*) AS question_count
                       FROM research_questions
                      WHERE case_id IN ({','.join('?' for _ in selected_cases)})
                      GROUP BY case_id ORDER BY case_id""",
                tuple(selected_cases),
            )
        }

    candidates.sort(
        key=lambda row: (
            row["priority"],
            row["case_id"],
            row["rule_id"],
            row["subject_type"],
            row.get("window_year_from", -1),
            row.get("window_year_to", -1),
            row["subject_id"],
            row["id"],
        )
    )
    decisions.sort(
        key=lambda row: (
            row["case_id"],
            row["rule_id"],
            row["subject_type"],
            row["subject_id"],
            row["outcome"],
        )
    )
    core = {
        "rule_set_version": rule_payload["rule_set_version"],
        "rule_config_fingerprint": _fingerprint(rule_payload),
        "profile_set_version": scope_payload.get("profile_set_version"),
        "scope_config_fingerprint": _fingerprint(scope_payload),
        "case_ids": selected_cases,
        "candidates": candidates,
        "decisions": decisions,
        "indexing_pilot": pilot_documents,
        "existing_question_counts": existing_question_counts,
    }
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "mode": "dry-run",
        "database_access": "read-only",
        "deterministic": True,
        "input_fingerprint": _fingerprint(core),
        "rules_path": _display_path(root, resolved_rules),
        "scope_path": _display_path(root, resolved_scope),
        "summary": {
            "candidate_count": len(candidates),
            "decision_count": len(decisions),
            "pilot_document_count": len(pilot_documents),
            "pilot_complete_count": sum(
                item["chain_status"] == "complete_linkage" for item in pilot_documents
            ),
        },
        **core,
    }


def _question_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-ząćęłńóśźż0-9]+", search_fold(value))
        if len(token) >= 4 and token not in _TOKEN_STOPWORDS
    }


def _progress(
    *,
    status: str,
    has_target: bool,
    has_plan: bool,
    log_count: int,
    coverage_count: int,
    complete_coverage_count: int,
) -> dict[str, Any]:
    finished = status in {"answered", "closed"}
    dimensions = [
        {"id": "scope", "label": "Pytanie i kryterium", "weight": 15, "done": True},
        {"id": "target", "label": "Wskazany cel", "weight": 15, "done": has_target},
        {"id": "plan", "label": "Plan lub następny krok", "weight": 10, "done": has_plan},
        {"id": "research", "label": "Zapisane wyszukiwanie", "weight": 25, "done": log_count > 0},
        {
            "id": "coverage",
            "label": "Odpowiednie, pełne i systematyczne pokrycie",
            "weight": 20,
            "done": coverage_count > 0
            and complete_coverage_count == coverage_count,
        },
        {"id": "answer", "label": "Zapisana odpowiedź", "weight": 15, "done": finished},
    ]
    score = sum(item["weight"] for item in dimensions if item["done"])
    gates: list[str] = []
    if not has_target:
        gates.append("Brak wskazanego celu pytania.")
    if log_count == 0:
        gates.append("Nie zapisano jeszcze wyszukiwania.")
    elif coverage_count == 0:
        gates.append("Wyszukiwanie nie ma oceny pokrycia.")
    elif complete_coverage_count < coverage_count:
        gates.append("Pokrycie nie jest jeszcze jednocześnie odpowiednie, pełne i systematyczne.")
    if not finished:
        gates.append("Nie zapisano odpowiedzi wraz z ograniczeniami.")
    if status == "blocked":
        gates.insert(0, "Pytanie ma jawny status blokady.")
    return {
        "score": score,
        "meaning": "wykonanie procesu badawczego, nie pewność tezy",
        "dimensions": dimensions,
        "gates": gates,
    }


def _workflow_stage(
    *,
    status: str,
    has_target: bool,
    log_count: int,
    coverage_count: int,
    complete_coverage_count: int,
    open_action_count: int,
) -> str:
    if status == "answered":
        return "answered"
    if status == "closed":
        return "dismissed"
    if status == "blocked":
        return "blocked"
    if log_count:
        if (
            coverage_count > 0
            and complete_coverage_count == coverage_count
            and open_action_count == 0
        ):
            return "correlation_review"
        return "in_progress"
    return "ready" if has_target else "triage"


def _candidate_duplicates(
    candidate: dict[str, Any], canonical: list[dict[str, Any]]
) -> list[str]:
    exact: list[str] = []
    similar: list[str] = []
    candidate_tokens = _question_tokens(candidate["question"])
    for item in canonical:
        if item["caseId"] != candidate["case_id"]:
            continue
        same_target = any(
            target["type"] == candidate["subject_type"]
            and target["id"] == candidate["subject_id"]
            for target in item["targets"]
        )
        item_tokens = _question_tokens(item["question"])
        overlap = candidate_tokens & item_tokens
        if same_target and overlap:
            exact.append(item["id"])
            continue
        if len(overlap) >= 3 and len(overlap) / max(1, min(len(candidate_tokens), len(item_tokens))) >= 0.35:
            similar.append(item["id"])
    return sorted(dict.fromkeys(exact + similar))


def build_research_backlog(
    root: Path,
    *,
    case_id: str | None = None,
    generation_report: dict[str, Any] | None = None,
    rules_path: str | Path | None = None,
    scope_path: str | Path | None = None,
    connection: sqlite3.Connection | None = None,
    source_reported_networks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Combine curated questions and deterministic candidates without writes."""

    root = root.resolve()
    if generation_report is None:
        resolved_rules = _resolve_config_path(root, rules_path, DEFAULT_RULES_PATH)
        resolved_scope = _resolve_config_path(root, scope_path, DEFAULT_SCOPE_PATH)
        if resolved_rules.is_file() and resolved_scope.is_file():
            generation_report = generate_question_backlog(
                root,
                case_id=case_id,
                rules_path=resolved_rules,
                scope_path=resolved_scope,
                connection=connection,
                source_reported_networks=source_reported_networks,
            )
        else:
            generation_report = {
                "rule_set_version": None,
                "profile_set_version": None,
                "input_fingerprint": None,
                "candidates": [],
            }

    required_tables = {
        "next_actions",
        "research_cases",
        "research_log_entries",
        "research_plans",
        "research_question_targets",
        "research_question_scopes",
        "research_questions",
        "research_scope_edges",
        "research_scope_targets",
        "research_scopes",
        "search_coverage",
    }
    with _connection_scope(root, connection) as connection:
        _require_tables(connection, required_tables)
        selected_cases = _case_ids(connection, case_id)
        placeholders = ",".join("?" for _ in selected_cases)
        question_rows = connection.execute(
            f"""SELECT q.id,q.case_id,rc.title AS case_title,q.question,
                       q.completion_criterion,q.status,q.priority,q.updated_at
                  FROM research_questions q
                  JOIN research_cases rc ON rc.id=q.case_id
                 WHERE q.case_id IN ({placeholders})
                 ORDER BY q.priority,q.case_id,q.id""",
            tuple(selected_cases),
        ).fetchall()
        question_ids = [row["id"] for row in question_rows]
        scope_catalog, scope_edges, scopes_by_question = _research_scope_projection(
            connection, question_ids
        )
        targets: dict[str, list[dict[str, Any]]] = {item: [] for item in question_ids}
        metrics: dict[str, dict[str, int]] = {
            item: {
                "log_count": 0,
                "coverage_count": 0,
                "complete_coverage_count": 0,
                "plan_count": 0,
                "open_action_count": 0,
            }
            for item in question_ids
        }
        next_actions: dict[str, str | None] = {item: None for item in question_ids}
        recent_research: dict[str, list[dict[str, Any]]] = {
            item: [] for item in question_ids
        }
        if question_ids:
            question_placeholders = ",".join("?" for _ in question_ids)
            for row in connection.execute(
                f"""SELECT question_id,target_type,target_id,target_label,is_primary,notes
                       FROM research_question_targets
                      WHERE question_id IN ({question_placeholders})
                      ORDER BY question_id,is_primary DESC,target_type,target_label,target_id""",
                tuple(question_ids),
            ):
                targets[row["question_id"]].append(
                    {
                        "type": row["target_type"],
                        "id": row["target_id"],
                        "label": row["target_label"],
                        "primary": bool(row["is_primary"]),
                        "notes": row["notes"],
                    }
                )
            for row in connection.execute(
                f"""SELECT rl.question_id,
                            COUNT(DISTINCT rl.id) AS log_count,
                            COUNT(DISTINCT sc.id) AS coverage_count,
                            COUNT(DISTINCT CASE WHEN sc.appropriate=1 AND sc.complete=1
                                                   AND sc.systematic=1 THEN sc.id END)
                                AS complete_coverage_count
                       FROM research_log_entries rl
                       LEFT JOIN search_coverage sc ON sc.log_entry_id=rl.id
                      WHERE rl.question_id IN ({question_placeholders})
                      GROUP BY rl.question_id""",
                tuple(question_ids),
            ):
                metrics[row["question_id"]].update(dict(row))
            for row in connection.execute(
                f"""SELECT question_id,COUNT(*) AS plan_count
                       FROM research_plans
                      WHERE question_id IN ({question_placeholders})
                      GROUP BY question_id""",
                tuple(question_ids),
            ):
                metrics[row["question_id"]]["plan_count"] = row["plan_count"]
            for row in connection.execute(
                f"""SELECT question_id,action,priority
                       FROM next_actions
                      WHERE question_id IN ({question_placeholders}) AND status='open'
                      ORDER BY question_id,priority DESC,updated_at,id""",
                tuple(question_ids),
            ):
                metrics[row["question_id"]]["open_action_count"] += 1
                if next_actions[row["question_id"]] is None:
                    next_actions[row["question_id"]] = row["action"]
            seen_log_ids: dict[str, set[str]] = {
                item: set() for item in question_ids
            }
            for row in connection.execute(
                f"""SELECT rl.question_id,rl.id,rl.query_text,rl.repository,
                            rl.scope_text,rl.outcome,rl.result_summary,rl.searched_at,
                            sc.id AS coverage_id,sc.appropriate,sc.complete,
                            sc.systematic,sc.assessment_note
                       FROM research_log_entries rl
                       LEFT JOIN search_coverage sc ON sc.log_entry_id=rl.id
                      WHERE rl.question_id IN ({question_placeholders})
                      ORDER BY rl.question_id,rl.searched_at DESC,rl.created_at DESC,
                               rl.id DESC,sc.created_at DESC,sc.id DESC""",
                tuple(question_ids),
            ):
                question_id = row["question_id"]
                # Structured assessment prose and historical input bindings are
                # never ordinary display summaries. Current provenance is in
                # the separately validated assessment.source whitelist.
                if assessment_candidate(row["result_summary"]):
                    continue
                if (
                    row["id"] in seen_log_ids[question_id]
                    or len(recent_research[question_id]) >= 3
                ):
                    continue
                activity = {
                    "id": row["id"],
                    "query": row["query_text"],
                    "repository": row["repository"],
                    "scope": row["scope_text"],
                    "outcome": row["outcome"],
                    "summary": row["result_summary"],
                    "searchedAt": row["searched_at"],
                }
                if row["coverage_id"]:
                    activity["coverage"] = {
                        "id": row["coverage_id"],
                        "appropriate": bool(row["appropriate"]),
                        "complete": bool(row["complete"]),
                        "systematic": bool(row["systematic"]),
                        "assessment": row["assessment_note"],
                    }
                recent_research[question_id].append(activity)
                seen_log_ids[question_id].add(row["id"])

        canonical: list[dict[str, Any]] = []
        for row in question_rows:
            item_metrics = metrics[row["id"]]
            has_target = bool(targets[row["id"]])
            has_plan = bool(
                item_metrics["plan_count"] or item_metrics["open_action_count"]
            )
            stage = _workflow_stage(
                status=row["status"],
                has_target=has_target,
                log_count=item_metrics["log_count"],
                coverage_count=item_metrics["coverage_count"],
                complete_coverage_count=item_metrics["complete_coverage_count"],
                open_action_count=item_metrics["open_action_count"],
            )
            item_scopes = scopes_by_question.get(row["id"], [])
            primary_scope = next(
                (scope for scope in item_scopes if scope["primary"]),
                item_scopes[0] if item_scopes else None,
            )
            backlog_item = {
                "id": row["id"],
                "origin": "curated",
                "caseId": row["case_id"],
                "caseTitle": row["case_title"],
                "scopeIds": [scope["id"] for scope in item_scopes],
                "scopes": item_scopes,
                "primaryScopeId": primary_scope["id"] if primary_scope else None,
                "primaryScopeTitle": primary_scope["title"] if primary_scope else row["case_title"],
                "primaryScopeType": primary_scope["type"] if primary_scope else "legacy_case",
                "primaryScopePath": primary_scope["primaryPath"] if primary_scope else [],
                "question": row["question"],
                "completionCriterion": row["completion_criterion"],
                "answerStatus": row["status"],
                "workflowStage": stage,
                "priority": row["priority"],
                "updatedAt": row["updated_at"],
                "questionType": "curated",
                "targets": targets[row["id"]],
                "nextAction": next_actions[row["id"]],
                "metrics": {
                    "logs": item_metrics["log_count"],
                    "coverageAssessments": item_metrics["coverage_count"],
                    "completeCoverageAssessments": item_metrics["complete_coverage_count"],
                    "openActions": item_metrics["open_action_count"],
                },
                "progress": _progress(
                    status=row["status"],
                    has_target=has_target,
                    has_plan=has_plan,
                    log_count=item_metrics["log_count"],
                    coverage_count=item_metrics["coverage_count"],
                    complete_coverage_count=item_metrics["complete_coverage_count"],
                ),
                "potentialDuplicateQuestionIds": [],
            }
            if recent_research[row["id"]]:
                backlog_item["recentResearch"] = recent_research[row["id"]]
            backlog_item.update(latest_question_assessment(connection, dict(row)))
            canonical.append(backlog_item)

        case_titles = {
            row["id"]: row["title"]
            for row in connection.execute(
                f"SELECT id,title FROM research_cases WHERE id IN ({placeholders})",
                tuple(selected_cases),
            )
        }
        legacy_scope_by_case = {
            scope["legacyCaseId"]: scope
            for scope in scope_catalog
            if scope.get("legacyCaseId")
        }
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        persisted_candidates: dict[str, dict[str, Any]] = {}
        if "research_question_candidates" in table_names:
            case_marks = ",".join("?" for _ in selected_cases)
            persisted_candidates = {
                row["id"]: dict(row)
                for row in connection.execute(
                    f"""SELECT id,status,privacy_status,first_seen_at,last_seen_at,
                                  seen_count,current_revision,promoted_question_id,
                                  merged_into_candidate_id
                           FROM research_question_candidates
                          WHERE case_id IN ({case_marks})""",
                    tuple(selected_cases),
                )
            }

    candidates: list[dict[str, Any]] = []
    for candidate in generation_report.get("candidates", []):
        if candidate["case_id"] not in case_titles:
            continue
        persisted = persisted_candidates.get(candidate["id"])
        persisted_status = persisted["status"] if persisted else "detected"
        if persisted_status in {"promoted", "merged"}:
            continue
        duplicate_ids = _candidate_duplicates(candidate, canonical)
        target_type = candidate["subject_type"]
        legacy_scope = legacy_scope_by_case.get(candidate["case_id"])
        candidate_scopes = []
        if legacy_scope:
            candidate_scopes = [
                {
                    "id": legacy_scope["id"],
                    "type": legacy_scope["type"],
                    "title": legacy_scope["title"],
                    "status": legacy_scope["status"],
                    "legacyCaseId": legacy_scope["legacyCaseId"],
                    "role": "primary",
                    "primary": True,
                    "notes": "Zakres odziedziczony z generatora sprawy legacy.",
                    "primaryPath": legacy_scope["primaryPath"],
                    "targets": legacy_scope["targets"],
                }
            ]
        targets = [
            {
                "type": target_type,
                "id": candidate["subject_id"],
                "label": candidate["subject_label"],
                "primary": True,
                "notes": None,
            }
        ]
        candidates.append(
            {
                "id": candidate["id"],
                "origin": "deterministic",
                "caseId": candidate["case_id"],
                "caseTitle": case_titles[candidate["case_id"]],
                "scopeIds": [scope["id"] for scope in candidate_scopes],
                "scopes": candidate_scopes,
                "primaryScopeId": legacy_scope["id"] if legacy_scope else None,
                "primaryScopeTitle": legacy_scope["title"] if legacy_scope else case_titles[candidate["case_id"]],
                "primaryScopeType": legacy_scope["type"] if legacy_scope else "legacy_case",
                "primaryScopePath": legacy_scope["primaryPath"] if legacy_scope else [],
                "question": candidate["question"],
                "completionCriterion": candidate["completion_criterion"],
                "answerStatus": persisted_status if persisted else "candidate",
                "workflowStage": (
                    "dismissed"
                    if persisted_status in {"dismissed", "superseded", "stale"}
                    else "triage"
                    if persisted_status in {"triage", "deferred"}
                    else "detected"
                ),
                "priority": candidate["priority"],
                "updatedAt": None,
                "questionType": candidate["question_type"],
                "ruleId": candidate["rule_id"],
                "ruleVersion": candidate["rule_version"],
                "evidenceFingerprint": candidate["evidence_fingerprint"],
                "targets": targets,
                "nextAction": "Oceń zakres, możliwy duplikat i wartość pytania.",
                "metrics": {
                    "logs": 0,
                    "coverageAssessments": 0,
                    "completeCoverageAssessments": 0,
                    "openActions": 0,
                },
                "progress": {
                    "score": 10,
                    "meaning": "wykonanie procesu badawczego, nie pewność tezy",
                    "dimensions": [
                        {"id": "detection", "label": "Luka wykryta i wyjaśniona", "weight": 10, "done": True}
                    ],
                    "gates": [
                        "Kandydatura wymaga triage przed utworzeniem pytania kanonicznego."
                    ],
                },
                "potentialDuplicateQuestionIds": duplicate_ids,
                "triage": (
                    {
                        "status": persisted_status,
                        "privacyStatus": persisted["privacy_status"],
                        "firstSeenAt": persisted["first_seen_at"],
                        "lastSeenAt": persisted["last_seen_at"],
                        "seenCount": persisted["seen_count"],
                        "revision": persisted["current_revision"],
                    }
                    if persisted
                    else None
                ),
            }
        )

    items = sorted(
        canonical + candidates,
        key=lambda item: (
            WORKFLOW_STAGES.index(item["workflowStage"]),
            item["priority"],
            item.get("primaryScopeTitle") or "",
            item["caseId"],
            item["id"],
        ),
    )
    by_stage = {
        stage: sum(item["workflowStage"] == stage for item in items)
        for stage in WORKFLOW_STAGES
    }
    core = {
        "ruleSetVersion": generation_report.get("rule_set_version"),
        "profileSetVersion": generation_report.get("profile_set_version"),
        "generatorInputFingerprint": generation_report.get("input_fingerprint"),
        "caseIds": selected_cases,
        "scopeIds": [scope["id"] for scope in scope_catalog],
        "scopes": scope_catalog,
        "scopeEdges": scope_edges,
        "items": items,
        "candidateTriageSummary": {
            "persisted": len(persisted_candidates),
            "byStatus": {
                status: sum(row["status"] == status for row in persisted_candidates.values())
                for status in (
                    "detected", "triage", "deferred", "promoted", "merged",
                    "dismissed", "superseded", "stale",
                )
            },
        },
    }
    return {
        "schemaVersion": BACKLOG_SCHEMA_VERSION,
        "mode": "read-only-derived",
        "deterministic": True,
        "inputFingerprint": _fingerprint(core),
        "workflowStages": list(WORKFLOW_STAGES),
        "summary": {
            "total": len(items),
            "canonical": len(canonical),
            "candidates": len(candidates),
            "possibleDuplicates": sum(
                bool(item["potentialDuplicateQuestionIds"]) for item in candidates
            ),
            "byStage": by_stage,
        },
        **core,
    }


__all__ = ["build_research_backlog", "generate_question_backlog"]
