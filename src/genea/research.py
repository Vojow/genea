from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

from .db import connect, initialize
from .workspace import stable_id, utc_now


VALID_STATUSES = {"open", "supported", "contradicted", "closed"}
VALID_STANCES = {"supports", "contradicts", "context"}
VALID_CASE_STATUSES = {"open", "on-hold", "closed"}
VALID_QUESTION_STATUSES = {"open", "answered", "blocked", "closed"}
VALID_RESULT_TYPES = {"positive", "negative", "inconclusive", "not-searched"}
VALID_ACTION_STATUSES = {"open", "completed", "cancelled", "superseded"}
VALID_QUESTION_TARGET_TYPES = {
    "person",
    "person_occurrence",
    "named_person",
    "household",
    "place",
    "place_study",
    "family_cluster",
    "event",
    "source_item",
    "other",
}
QUESTION_TARGET_TABLES = {
    "person": "persons",
    "person_occurrence": "person_occurrences",
    "household": "households",
    "place": "places",
    "place_study": "place_studies",
    "event": "events",
    "source_item": "source_items",
}


class MigrationRequiredError(RuntimeError):
    """Raised when a v2 API is used before the corresponding migration."""


def require_tables(connection: sqlite3.Connection, tables: Iterable[str]) -> None:
    """Fail with an actionable error instead of leaking SQLite's table error."""

    required = set(tables)
    if not required:
        return
    placeholders = ",".join("?" for _ in required)
    rows = connection.execute(
        f"SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ({placeholders})",
        tuple(sorted(required)),
    ).fetchall()
    missing = sorted(required - {row[0] for row in rows})
    if missing:
        raise MigrationRequiredError(
            "Baza wymaga migracji metodologicznej v2; brakuje tabel: "
            + ", ".join(missing)
        )


def _required(value: str, label: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{label} nie może być puste")
    return cleaned


def _validate(value: str, allowed: set[str], label: str) -> str:
    if value not in allowed:
        raise ValueError(f"{label} musi być jednym z: {', '.join(sorted(allowed))}")
    return value


def _table_available(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _ensure_legacy_case_scope(
    connection: sqlite3.Connection,
    *,
    case_id: str,
    title: str,
    summary: str | None,
    status: str,
    now: str,
) -> str | None:
    """Keep the v15 case facade losslessly represented in the v16 hierarchy."""

    if not _table_available(connection, "research_scopes"):
        return None
    root_scope_id = "scope_genea_program"
    scope_id = stable_id("research_scope", "legacy_case", case_id)
    scope_type = "place_study" if case_id.startswith("LOC-") else "legacy_case"
    scope_status = {
        "open": "active",
        "on-hold": "on_hold",
        "closed": "closed",
    }.get(status, "active")
    connection.execute(
        """INSERT INTO research_scopes(
               id,scope_type,title,summary,status,legacy_case_id,created_at,updated_at
           ) VALUES(?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
               title=excluded.title,
               summary=COALESCE(excluded.summary,research_scopes.summary),
               status=excluded.status,
               updated_at=excluded.updated_at""",
        (
            scope_id,
            scope_type,
            title,
            summary,
            scope_status,
            case_id,
            now,
            now,
        ),
    )
    if connection.execute(
        "SELECT 1 FROM research_scopes WHERE id=?", (root_scope_id,)
    ).fetchone():
        connection.execute(
            """INSERT OR IGNORE INTO research_scope_edges(
                   id,parent_scope_id,child_scope_id,relation_type,is_primary,
                   sort_order,notes,created_at
               ) VALUES(?,?,?,'contains',1,1000,?,?)""",
            (
                stable_id(
                    "research_scope_edge", root_scope_id, scope_id, "contains"
                ),
                root_scope_id,
                scope_id,
                "Automatyczna reprezentacja zgodności ze sprawą legacy.",
                now,
            ),
        )
    return scope_id


def _ensure_legacy_question_scope(
    connection: sqlite3.Connection,
    *,
    question_id: str,
    case_id: str,
    now: str,
) -> None:
    """Give a new question a safe default scope without overriding curation."""

    if not _table_available(connection, "research_question_scopes"):
        return
    scope = connection.execute(
        "SELECT id FROM research_scopes WHERE legacy_case_id=?", (case_id,)
    ).fetchone()
    if scope is None:
        return
    scope_id = str(scope["id"])
    if connection.execute(
        "SELECT 1 FROM research_question_scopes WHERE question_id=? AND scope_id=?",
        (question_id, scope_id),
    ).fetchone():
        return
    has_primary = connection.execute(
        "SELECT 1 FROM research_question_scopes WHERE question_id=? AND is_primary=1",
        (question_id,),
    ).fetchone() is not None
    connection.execute(
        """INSERT INTO research_question_scopes(
               id,question_id,scope_id,role,is_primary,notes,created_at
           ) VALUES(?,?,?,?,?,?,?)""",
        (
            stable_id("research_question_scope", question_id, scope_id),
            question_id,
            scope_id,
            "supporting" if has_primary else "primary",
            0 if has_primary else 1,
            "Automatyczne członkostwo zgodne z case_id.",
            now,
        ),
    )


def add_hypothesis(
    root: Path,
    statement: str,
    *,
    confidence: float | None = None,
    rationale: str | None = None,
) -> dict:
    initialize(root)
    now = utc_now()
    hypothesis = {
        "id": stable_id("hyp", statement),
        "statement": statement.strip(),
        "status": "open",
        "confidence": confidence,
        "rationale": rationale,
        "created_at": now,
        "updated_at": now,
    }
    with connect(root) as connection:
        connection.execute(
            """
            INSERT INTO hypotheses(id, statement, status, confidence, rationale, created_at, updated_at)
            VALUES (:id, :statement, :status, :confidence, :rationale, :created_at, :updated_at)
            ON CONFLICT(id) DO UPDATE SET
                confidence = excluded.confidence,
                rationale = COALESCE(excluded.rationale, hypotheses.rationale),
                updated_at = excluded.updated_at
            """,
            hypothesis,
        )
    return hypothesis


def link_evidence(
    root: Path,
    hypothesis_id: str,
    source_id: str,
    *,
    stance: str,
    analysis_id: str | None = None,
    note: str | None = None,
) -> dict:
    if stance not in VALID_STANCES:
        raise ValueError(f"stance musi być jednym z: {', '.join(sorted(VALID_STANCES))}")
    initialize(root)
    with connect(root) as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO hypothesis_evidence(
                hypothesis_id, source_id, analysis_id, stance, note
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (hypothesis_id, source_id, analysis_id, stance, note),
        )
    return {
        "hypothesis_id": hypothesis_id,
        "source_id": source_id,
        "analysis_id": analysis_id,
        "stance": stance,
        "note": note,
    }


def list_hypotheses(root: Path) -> list[dict]:
    initialize(root)
    with connect(root) as connection:
        rows = connection.execute(
            """
            SELECT h.*, COUNT(e.source_id) AS evidence_count
            FROM hypotheses h
            LEFT JOIN hypothesis_evidence e ON e.hypothesis_id = h.id
            GROUP BY h.id
            ORDER BY h.updated_at DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def create_research_case(
    root: Path,
    code: str,
    title: str,
    *,
    summary: str | None = None,
    status: str = "open",
) -> dict:
    """Create or update a research case without changing its existing status."""

    code = _required(code, "code")
    title = _required(title, "title")
    _validate(status, VALID_CASE_STATUSES, "status")
    initialize(root)
    now = utc_now()
    item = {
        "id": code,
        "title": title,
        "status": status,
        "summary": summary,
        "created_at": now,
        "updated_at": now,
    }
    with connect(root) as connection:
        require_tables(connection, {"research_cases"})
        connection.execute(
            """
            INSERT INTO research_cases(id, title, status, summary, created_at, updated_at)
            VALUES (:id, :title, :status, :summary, :created_at, :updated_at)
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                summary = COALESCE(excluded.summary, research_cases.summary),
                updated_at = excluded.updated_at
            """,
            item,
        )
        _ensure_legacy_case_scope(
            connection,
            case_id=code,
            title=title,
            summary=summary,
            status=status,
            now=now,
        )
        row = connection.execute(
            "SELECT * FROM research_cases WHERE id = ?", (code,)
        ).fetchone()
    result = dict(row)
    result["code"] = result["id"]
    return result


def add_research_question(
    root: Path,
    case_id: str,
    question: str,
    completion_criterion: str,
    *,
    status: str = "open",
    priority: int = 100,
) -> dict:
    """Add an atomic question with an explicit stopping criterion."""

    question = _required(question, "question")
    completion_criterion = _required(completion_criterion, "completion_criterion")
    _validate(status, VALID_QUESTION_STATUSES, "status")
    if priority < 0:
        raise ValueError("priority nie może być ujemny")
    initialize(root)
    now = utc_now()
    item = {
        "id": stable_id("question", case_id, question.casefold()),
        "case_id": case_id,
        "question": question,
        "status": status,
        "priority": priority,
        "completion_criterion": completion_criterion,
        "created_at": now,
        "updated_at": now,
    }
    with connect(root) as connection:
        require_tables(connection, {"research_cases", "research_questions"})
        connection.execute(
            """
            INSERT INTO research_questions(
                id, case_id, question, status, priority, completion_criterion,
                created_at, updated_at
            ) VALUES (
                :id, :case_id, :question, :status, :priority, :completion_criterion,
                :created_at, :updated_at
            )
            ON CONFLICT(id) DO UPDATE SET
                completion_criterion = excluded.completion_criterion,
                priority = excluded.priority,
                updated_at = excluded.updated_at
            """,
            item,
        )
        _ensure_legacy_question_scope(
            connection,
            question_id=item["id"],
            case_id=case_id,
            now=now,
        )
        row = connection.execute(
            "SELECT * FROM research_questions WHERE id = ?", (item["id"],)
        ).fetchone()
    return dict(row)


def set_research_question_priority(root: Path, question_id: str, priority: int) -> dict:
    """Set a stable research-question rank; lower numbers are handled first."""

    if priority < 0:
        raise ValueError("priority nie może być ujemny")
    initialize(root)
    with connect(root) as connection:
        require_tables(connection, {"research_questions"})
        cursor = connection.execute(
            "UPDATE research_questions SET priority = ?, updated_at = ? WHERE id = ?",
            (priority, utc_now(), question_id),
        )
        if not cursor.rowcount:
            raise LookupError(f"Nie znaleziono pytania: {question_id}")
        row = connection.execute(
            "SELECT * FROM research_questions WHERE id = ?", (question_id,)
        ).fetchone()
    return dict(row)


def link_research_question_target(
    root: Path,
    question_id: str,
    target_type: str,
    target_id: str,
    target_label: str,
    *,
    is_primary: bool = False,
    notes: str | None = None,
) -> dict:
    """Link one question to a person, household, place, study, or named cluster."""

    _validate(target_type, VALID_QUESTION_TARGET_TYPES, "target_type")
    target_id = _required(target_id, "target_id")
    target_label = _required(target_label, "target_label")
    initialize(root)
    now = utc_now()
    item = {
        "id": stable_id("question_target", question_id, target_type, target_id),
        "question_id": question_id,
        "target_type": target_type,
        "target_id": target_id,
        "target_label": target_label,
        "is_primary": int(bool(is_primary)),
        "notes": notes,
        "created_at": now,
    }
    with connect(root) as connection:
        require_tables(
            connection, {"research_questions", "research_question_targets"}
        )
        if connection.execute(
            "SELECT 1 FROM research_questions WHERE id = ?", (question_id,)
        ).fetchone() is None:
            raise LookupError(f"Nie znaleziono pytania: {question_id}")
        physical_table = QUESTION_TARGET_TABLES.get(target_type)
        if physical_table and connection.execute(
            f"SELECT 1 FROM {physical_table} WHERE id = ?", (target_id,)
        ).fetchone() is None:
            raise LookupError(
                f"Nie znaleziono celu {target_type}: {target_id}"
            )
        if is_primary:
            connection.execute(
                "UPDATE research_question_targets SET is_primary = 0 WHERE question_id = ?",
                (question_id,),
            )
        connection.execute(
            """
            INSERT INTO research_question_targets(
                id, question_id, target_type, target_id, target_label,
                is_primary, notes, created_at
            ) VALUES (
                :id, :question_id, :target_type, :target_id, :target_label,
                :is_primary, :notes, :created_at
            )
            ON CONFLICT(question_id, target_type, target_id) DO UPDATE SET
                target_label = excluded.target_label,
                is_primary = excluded.is_primary,
                notes = COALESCE(excluded.notes, research_question_targets.notes)
            """,
            item,
        )
        row = connection.execute(
            "SELECT * FROM research_question_targets WHERE id = ?", (item["id"],)
        ).fetchone()
    result = dict(row)
    result["is_primary"] = bool(result["is_primary"])
    return result


def list_research_questions(
    root: Path,
    *,
    case_id: str | None = None,
    status: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    scope_id: str | None = None,
) -> list[dict]:
    """List questions in stable priority order, optionally scoped to an object."""

    if status is not None:
        _validate(status, VALID_QUESTION_STATUSES, "status")
    if target_type is not None:
        _validate(target_type, VALID_QUESTION_TARGET_TYPES, "target_type")
    if target_id is not None and target_type is None:
        raise ValueError("target_id wymaga target_type")
    initialize(root)
    clauses: list[str] = []
    parameters: list[object] = []
    if case_id is not None:
        clauses.append("q.case_id = ?")
        parameters.append(case_id)
    if status is not None:
        clauses.append("q.status = ?")
        parameters.append(status)
    if target_type is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM research_question_targets f "
            "WHERE f.question_id = q.id AND f.target_type = ?"
            + (" AND f.target_id = ?" if target_id is not None else "")
            + ")"
        )
        parameters.append(target_type)
        if target_id is not None:
            parameters.append(target_id)
    if scope_id is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM research_question_scopes qs "
            "WHERE qs.question_id=q.id AND qs.scope_id=?)"
        )
        parameters.append(scope_id)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with connect(root) as connection:
        require_tables(
            connection,
            {
                "research_questions",
                "research_question_targets",
                "research_question_scopes",
                "research_scopes",
            },
        )
        questions = connection.execute(
            "SELECT q.* FROM research_questions q"
            + where
            + " ORDER BY CASE q.status WHEN 'open' THEN 0 WHEN 'blocked' THEN 1 ELSE 2 END, "
              "q.priority, q.case_id, q.created_at, q.id",
            parameters,
        ).fetchall()
        ids = [row["id"] for row in questions]
        targets_by_question: dict[str, list[dict]] = {item: [] for item in ids}
        scopes_by_question: dict[str, list[dict]] = {item: [] for item in ids}
        if ids:
            placeholders = ",".join("?" for _ in ids)
            targets = connection.execute(
                f"SELECT * FROM research_question_targets WHERE question_id IN ({placeholders}) "
                "ORDER BY is_primary DESC, target_type, target_label, id",
                ids,
            ).fetchall()
            for row in targets:
                target = dict(row)
                target["is_primary"] = bool(target["is_primary"])
                targets_by_question[target["question_id"]].append(target)
            memberships = connection.execute(
                f"""SELECT qs.*,s.scope_type,s.title,s.status AS scope_status,
                           s.legacy_case_id
                      FROM research_question_scopes qs
                      JOIN research_scopes s ON s.id=qs.scope_id
                     WHERE qs.question_id IN ({placeholders})
                     ORDER BY qs.question_id,qs.is_primary DESC,qs.role,s.title,s.id""",
                ids,
            ).fetchall()
            for row in memberships:
                membership = dict(row)
                membership["is_primary"] = bool(membership["is_primary"])
                scopes_by_question[membership["question_id"]].append(membership)
    result: list[dict] = []
    for row in questions:
        item = dict(row)
        item["targets"] = targets_by_question[item["id"]]
        item["scopes"] = scopes_by_question[item["id"]]
        result.append(item)
    return result


def add_research_log_entry(
    root: Path,
    case_id: str,
    *,
    repository: str,
    query_text: str,
    result_type: str,
    result_summary: str,
    question_id: str | None = None,
    collection_name: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    scope_text: str | None = None,
    searched_at: str | None = None,
) -> dict:
    """Record a positive, negative, or inconclusive search as performed."""

    repository = _required(repository, "repository")
    query_text = _required(query_text, "query_text")
    result_summary = _required(result_summary, "result_summary")
    _validate(result_type, VALID_RESULT_TYPES, "result_type")
    searched_at = searched_at or utc_now()
    if scope_text is None:
        scope_text = "; ".join(
            part for part in (collection_name, date_from, date_to) if part
        )
    scope_text = _required(scope_text, "scope_text")
    item = {
        "id": stable_id(
            "search",
            case_id,
            question_id or "",
            repository,
            collection_name or "",
            query_text,
            date_from or "",
            date_to or "",
            searched_at,
        ),
        "case_id": case_id,
        "question_id": question_id,
        "repository": repository,
        "query_text": query_text,
        "scope_text": scope_text,
        "outcome": result_type,
        "result_summary": result_summary,
        "searched_at": searched_at,
        "created_at": utc_now(),
    }
    initialize(root)
    with connect(root) as connection:
        require_tables(connection, {"research_cases", "research_log_entries"})
        connection.execute(
            """
            INSERT INTO research_log_entries(
                id, case_id, question_id, query_text, repository, scope_text,
                outcome, result_summary, searched_at, created_at
            ) VALUES (
                :id, :case_id, :question_id, :query_text, :repository, :scope_text,
                :outcome, :result_summary, :searched_at, :created_at
            )
            ON CONFLICT(id) DO UPDATE SET
                outcome = excluded.outcome,
                result_summary = excluded.result_summary
            """,
            item,
        )
    return item


def record_search_coverage(
    root: Path,
    log_entry_id: str,
    *,
    appropriate: bool,
    complete: bool,
    systematic: bool,
    assessment_note: str | None = None,
) -> dict:
    """Describe coverage; all three tests are required for negative evidence."""

    item = {
        "id": stable_id("coverage", log_entry_id),
        "log_entry_id": log_entry_id,
        "appropriate": int(bool(appropriate)),
        "complete": int(bool(complete)),
        "systematic": int(bool(systematic)),
        "assessment_note": assessment_note,
        "created_at": utc_now(),
    }
    initialize(root)
    with connect(root) as connection:
        require_tables(connection, {"research_log_entries", "search_coverage"})
        log_row = connection.execute(
            "SELECT outcome FROM research_log_entries WHERE id=?",
            (log_entry_id,),
        ).fetchone()
        if log_row is None:
            raise LookupError(f"Nie znaleziono wpisu Research Log: {log_entry_id}")
        log_outcome = str(log_row["outcome"])
        connection.execute(
            """
            INSERT INTO search_coverage(
                id, log_entry_id, appropriate, complete, systematic,
                assessment_note, created_at
            ) VALUES (
                :id, :log_entry_id, :appropriate, :complete, :systematic,
                :assessment_note, :created_at
            )
            ON CONFLICT(id) DO UPDATE SET
                appropriate = excluded.appropriate,
                complete = excluded.complete,
                systematic = excluded.systematic,
                assessment_note = excluded.assessment_note
            """,
            item,
        )
    item["appropriate"] = bool(item["appropriate"])
    item["complete"] = bool(item["complete"])
    item["systematic"] = bool(item["systematic"])
    item["qualifies_as_negative_evidence"] = log_outcome == "negative" and all(
        (item["appropriate"], item["complete"], item["systematic"])
    )
    return item


def negative_evidence_assessment(root: Path, case_id: str) -> dict:
    """Assess whether this case has a defensible, explicitly negative search."""

    initialize(root)
    with connect(root) as connection:
        require_tables(
            connection,
            {"research_log_entries", "search_coverage"},
        )
        negative_count = connection.execute(
            """
            SELECT COUNT(*) FROM research_log_entries
            WHERE case_id = ? AND outcome = 'negative'
            """,
            (case_id,),
        ).fetchone()[0]
        rows = connection.execute(
            """
            SELECT c.*, l.scope_text, l.outcome
            FROM search_coverage c
            JOIN research_log_entries l ON l.id = c.log_entry_id
            WHERE l.case_id = ? AND l.outcome = 'negative'
            ORDER BY l.searched_at, c.id
            """,
            (case_id,),
        ).fetchall()
    coverage = [dict(row) for row in rows]
    complete_rows = [
        row
        for row in coverage
        if row["appropriate"] and row["complete"] and row["systematic"]
    ]
    reasons: list[str] = []
    if not negative_count:
        reasons.append("brak zapisanej kwerendy z wynikiem negatywnym")
    if not complete_rows:
        reasons.append(
            "brak zakresu jednocześnie właściwego, kompletnego i sprawdzonego systematycznie"
        )
    return {
        "case_id": case_id,
        "eligible": bool(negative_count and complete_rows),
        "negative_search_count": negative_count,
        "qualifying_coverage": complete_rows,
        "reasons": reasons,
    }


def set_next_action(
    root: Path,
    case_id: str,
    action: str,
    *,
    question_id: str | None = None,
    work_item_id: str | None = None,
    priority: int = 3,
) -> dict:
    """Set the current step for a work item, question, or legacy case.

    The narrowest supplied scope wins. This keeps compatibility with the old
    one-action-per-case contract without collapsing parallel questions.
    """

    action = _required(action, "action")
    if not 1 <= priority <= 5:
        raise ValueError("priority musi mieścić się w zakresie 1–5")
    initialize(root)
    now = utc_now()
    item = {
        "id": stable_id(
            "action", case_id, question_id or "", work_item_id or "", action.casefold()
        ),
        "case_id": case_id,
        "question_id": question_id,
        "work_item_id": work_item_id,
        "action": action,
        "status": "open",
        "priority": priority,
        "created_at": now,
        "updated_at": now,
    }
    with connect(root) as connection:
        require_tables(connection, {"research_cases", "next_actions"})
        if question_id and connection.execute(
            "SELECT 1 FROM research_questions WHERE id=? AND case_id=?",
            (question_id, case_id),
        ).fetchone() is None:
            raise LookupError(
                f"Nie znaleziono pytania {question_id} w sprawie {case_id}"
            )
        if work_item_id:
            require_tables(connection, {"research_work_items"})
            if connection.execute(
                "SELECT 1 FROM research_work_items WHERE id=?", (work_item_id,)
            ).fetchone() is None:
                raise LookupError(f"Nie znaleziono pakietu pracy: {work_item_id}")
        if work_item_id:
            supersede_where = "work_item_id = ?"
            supersede_parameters = (work_item_id,)
        elif question_id:
            supersede_where = "work_item_id IS NULL AND question_id = ?"
            supersede_parameters = (question_id,)
        else:
            supersede_where = (
                "work_item_id IS NULL AND question_id IS NULL AND case_id = ?"
            )
            supersede_parameters = (case_id,)
        connection.execute(
            f"""
            UPDATE next_actions
            SET status = 'superseded', updated_at = ?
            WHERE {supersede_where} AND status = 'open' AND id <> ?
            """,
            (now, *supersede_parameters, item["id"]),
        )
        connection.execute(
            """
            INSERT INTO next_actions(
                id, case_id, question_id, work_item_id, action, priority, status,
                created_at, updated_at
            ) VALUES (
                :id, :case_id, :question_id, :work_item_id, :action, :priority,
                :status, :created_at, :updated_at
            )
            ON CONFLICT(id) DO UPDATE SET
                question_id = excluded.question_id,
                work_item_id = excluded.work_item_id,
                status = 'open',
                priority = excluded.priority,
                updated_at = excluded.updated_at
            """,
            item,
        )
    return item


def complete_next_action(root: Path, action_id: str) -> dict:
    initialize(root)
    completed_at = utc_now()
    with connect(root) as connection:
        require_tables(connection, {"next_actions"})
        cursor = connection.execute(
            """
            UPDATE next_actions SET status = 'completed', updated_at = ?
            WHERE id = ? AND status = 'open'
            """,
            (completed_at, action_id),
        )
        if not cursor.rowcount:
            raise LookupError(f"Brak oczekującego Next Action: {action_id}")
        row = connection.execute(
            "SELECT * FROM next_actions WHERE id = ?", (action_id,)
        ).fetchone()
    return dict(row)


def get_research_case(root: Path, case_id: str) -> dict:
    """Return the case and its questions, searches, coverage, and current action."""

    initialize(root)
    with connect(root) as connection:
        require_tables(
            connection,
            {
                "research_cases",
                "research_questions",
                "research_question_targets",
                "research_log_entries",
                "search_coverage",
                "next_actions",
            },
        )
        case = connection.execute(
            "SELECT * FROM research_cases WHERE id = ?", (case_id,)
        ).fetchone()
        if case is None:
            raise LookupError(f"Nie znaleziono sprawy: {case_id}")
        resolved_id = case["id"]
        searches = connection.execute(
            "SELECT * FROM research_log_entries WHERE case_id = ? ORDER BY searched_at, id",
            (resolved_id,),
        ).fetchall()
        coverage = connection.execute(
            """
            SELECT c.* FROM search_coverage c
            JOIN research_log_entries l ON l.id = c.log_entry_id
            WHERE l.case_id = ? ORDER BY l.searched_at, c.id
            """,
            (resolved_id,),
        ).fetchall()
        actions = connection.execute(
            """
            SELECT * FROM next_actions WHERE case_id = ?
            ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END, priority, created_at DESC
            """,
            (resolved_id,),
        ).fetchall()
    result = dict(case)
    result.update(
        {
            "questions": list_research_questions(root, case_id=resolved_id),
            "research_log": [dict(row) for row in searches],
            "search_coverage": [dict(row) for row in coverage],
            "next_actions": [dict(row) for row in actions],
        }
    )
    return result
