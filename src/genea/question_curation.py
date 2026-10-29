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
from .question_backlog import generate_question_backlog
from .research import require_tables
from .workspace import stable_id, utc_now


ACTIVE_STATUSES = {"detected", "triage", "deferred"}
DECISIONS = {"promote", "merge", "dismiss", "defer", "supersede"}
REQUIRED_TABLES = {
    "audit_log",
    "curation_commands",
    "privacy_labels",
    "research_cases",
    "research_questions",
    "research_question_candidates",
    "research_question_candidate_revisions",
    "research_question_candidate_decisions",
}
PRIVATE_CLASSES = {"living", "restricted", "family_private", "strict_private"}
QUESTION_TARGET_TYPES = {
    "person", "person_occurrence", "named_person", "household", "place",
    "place_study", "family_cluster", "event", "source_item", "other",
}


class QuestionCurationError(ValueError):
    """A safe validation error for deterministic question triage."""


class QuestionCurationConflict(QuestionCurationError):
    """A candidate or one of its dependencies changed after preview."""


def _required(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise QuestionCurationError(f"Pole {label} musi być niepustym tekstem")
    return value.strip()


def _collect_strings(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, str) and value:
        result.add(value)
    elif isinstance(value, dict):
        for item in value.values():
            result.update(_collect_strings(item))
    elif isinstance(value, list):
        for item in value:
            result.update(_collect_strings(item))
    return result


def _privacy_check(connection, candidate: dict[str, Any]) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    evidence = json.loads(candidate.get("evidence_json") or "{}")
    references = _collect_strings(evidence) | {str(candidate["subject_id"])}
    labels = []
    if references:
        placeholders = ",".join("?" for _ in references)
        labels = [
            dict(row)
            for row in connection.execute(
                f"""SELECT object_type,object_id,classification
                       FROM privacy_labels
                      WHERE object_id IN ({placeholders})
                        AND classification IN ('living','restricted','family_private','strict_private')
                      ORDER BY object_type,object_id""",
                tuple(sorted(references)),
            )
        ]
    return {
        "status": "blocked" if labels else "passed",
        "checkedReferenceCount": len(references),
        "blockedReferences": labels,
    }


def _snapshot(candidate: dict[str, Any], generator_input_fingerprint: str) -> dict[str, Any]:
    return {
        "id": candidate["id"],
        "candidateKey": candidate["candidate_key"],
        "ruleId": candidate["rule_id"],
        "ruleVersion": str(candidate["rule_version"]),
        "caseId": candidate["case_id"],
        "questionType": candidate["question_type"],
        "subjectType": candidate["subject_type"],
        "subjectId": candidate["subject_id"],
        "subjectLabel": candidate["subject_label"],
        "gapKey": candidate["gap_key"],
        "question": candidate["question"],
        "completionCriterion": candidate["completion_criterion"],
        "priority": int(candidate["priority"]),
        "evidenceFingerprint": candidate["evidence_fingerprint"],
        "evidence": candidate.get("evidence") or {},
        "generatorInputFingerprint": generator_input_fingerprint,
    }


def refresh_question_candidates(
    root: Path,
    *,
    generation_report: dict[str, Any] | None = None,
    case_id: str | None = None,
    actor: str,
) -> dict[str, Any]:
    """Persist a generator observation without promoting any research question."""

    actor = _required(actor, "actor")
    report = generation_report or generate_question_backlog(root, case_id=case_id)
    if report.get("mode") != "dry-run":
        raise QuestionCurationError("Kandydatury muszą pochodzić z raportu dry-run")
    generated_at = utc_now()
    generator_fingerprint = _required(report.get("input_fingerprint"), "inputFingerprint")
    incoming = list(report.get("candidates") or [])
    observed_ids = {str(item["id"]) for item in incoming}
    selected_cases = {str(item["case_id"]) for item in incoming}
    if case_id:
        selected_cases.add(case_id)

    initialize(root)
    created = changed = seen = stale = 0
    with connect(root) as connection:
        require_tables(connection, REQUIRED_TABLES)
        connection.execute("BEGIN IMMEDIATE")
        for item in incoming:
            candidate_id = str(item["id"])
            snapshot = _snapshot(item, generator_fingerprint)
            existing = connection.execute(
                "SELECT * FROM research_question_candidates WHERE id=?", (candidate_id,)
            ).fetchone()
            privacy = _privacy_check(
                connection,
                {
                    "subject_id": item["subject_id"],
                    "evidence_json": canonical_json(item.get("evidence") or {}),
                },
            )
            if existing is None:
                revision = 1
                status = "detected"
                connection.execute(
                    """INSERT INTO research_question_candidates(
                           id,candidate_key,rule_id,rule_version,case_id,question_type,
                           subject_type,subject_id,subject_label,gap_key,question_text,
                           completion_criterion,priority,evidence_fingerprint,evidence_json,
                           generator_input_fingerprint,status,privacy_status,privacy_json,
                           first_seen_at,last_seen_at,seen_count,current_revision,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        candidate_id, item["candidate_key"], item["rule_id"],
                        str(item["rule_version"]), item["case_id"], item["question_type"],
                        item["subject_type"], item["subject_id"], item["subject_label"],
                        item["gap_key"], item["question"], item["completion_criterion"],
                        int(item["priority"]), item["evidence_fingerprint"],
                        canonical_json(item.get("evidence") or {}), generator_fingerprint,
                        status, privacy["status"], canonical_json(privacy), generated_at,
                        generated_at, 1, revision, generated_at,
                    ),
                )
                created += 1
                append_revision = True
            else:
                existing_dict = dict(existing)
                revision = int(existing["current_revision"])
                append_revision = (
                    existing["evidence_fingerprint"] != item["evidence_fingerprint"]
                    or existing["generator_input_fingerprint"] != generator_fingerprint
                    or existing["question_text"] != item["question"]
                    or existing["completion_criterion"] != item["completion_criterion"]
                )
                if append_revision:
                    revision += 1
                    changed += 1
                else:
                    seen += 1
                status = existing["status"]
                if status == "stale":
                    status = "detected"
                connection.execute(
                    """UPDATE research_question_candidates SET
                           rule_version=?,question_type=?,subject_label=?,question_text=?,
                           completion_criterion=?,priority=?,evidence_fingerprint=?,
                           evidence_json=?,generator_input_fingerprint=?,status=?,
                           privacy_status=?,privacy_json=?,last_seen_at=?,seen_count=seen_count+1,
                           current_revision=?,updated_at=? WHERE id=?""",
                    (
                        str(item["rule_version"]), item["question_type"], item["subject_label"],
                        item["question"], item["completion_criterion"], int(item["priority"]),
                        item["evidence_fingerprint"], canonical_json(item.get("evidence") or {}),
                        generator_fingerprint, status, privacy["status"], canonical_json(privacy),
                        generated_at, revision, generated_at, candidate_id,
                    ),
                )
            if append_revision:
                connection.execute(
                    """INSERT INTO research_question_candidate_revisions(
                           id,candidate_id,revision,evidence_fingerprint,
                           generator_input_fingerprint,snapshot_json,seen_at
                       ) VALUES(?,?,?,?,?,?,?)""",
                    (
                        stable_id("question_candidate_revision", candidate_id, str(revision)),
                        candidate_id, revision, item["evidence_fingerprint"],
                        generator_fingerprint, canonical_json(snapshot), generated_at,
                    ),
                )

        if selected_cases:
            placeholders = ",".join("?" for _ in selected_cases)
            rows = connection.execute(
                f"""SELECT id FROM research_question_candidates
                      WHERE case_id IN ({placeholders})
                        AND status IN ('detected','triage','deferred')""",
                tuple(sorted(selected_cases)),
            ).fetchall()
            missing = [row["id"] for row in rows if row["id"] not in observed_ids]
            if missing:
                marks = ",".join("?" for _ in missing)
                connection.execute(
                    f"""UPDATE research_question_candidates
                           SET status='stale',updated_at=? WHERE id IN ({marks})""",
                    (generated_at, *missing),
                )
                stale = len(missing)
        connection.execute(
            """INSERT INTO audit_log(
                   occurred_at,actor,action,object_type,object_id,after_json,rationale
               ) VALUES(?,?,'question_candidates_refreshed','research_question_candidates',?,?,?)""",
            (
                generated_at, actor, case_id or "all",
                canonical_json({"created": created, "changed": changed, "seen": seen, "stale": stale}),
                "Jawne utrwalenie obserwacji deterministycznego generatora; bez promocji pytań.",
            ),
        )
    return {
        "status": "refreshed",
        "created": created,
        "changed": changed,
        "seen": seen,
        "stale": stale,
        "observed": len(incoming),
        "generatorInputFingerprint": generator_fingerprint,
    }


def _candidate_row(connection, candidate_id: str) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    row = connection.execute(
        "SELECT * FROM research_question_candidates WHERE id=?", (candidate_id,)
    ).fetchone()
    if row is None:
        raise LookupError(f"Nie znaleziono kandydatury pytania {candidate_id}")
    return dict(row)


def _preview(connection, candidate_id: str) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    candidate = _candidate_row(connection, candidate_id)
    revision = connection.execute(
        """SELECT * FROM research_question_candidate_revisions
             WHERE candidate_id=? AND revision=?""",
        (candidate_id, candidate["current_revision"]),
    ).fetchone()
    privacy = _privacy_check(connection, candidate)
    duplicates = [
        dict(row)
        for row in connection.execute(
            """SELECT q.id,q.question,q.status
                 FROM research_questions q
                 JOIN research_question_targets t ON t.question_id=q.id
                WHERE q.case_id=? AND t.target_type=? AND t.target_id=?
                ORDER BY q.priority,q.id""",
            (candidate["case_id"], candidate["subject_type"], candidate["subject_id"]),
        )
    ]
    state = {
        "candidate": candidate,
        "revision": dict(revision) if revision else None,
        "privacy": privacy,
        "potentialDuplicateQuestions": duplicates,
    }
    return {
        "candidateId": candidate_id,
        "status": candidate["status"],
        "revision": candidate["current_revision"],
        "privacyCheck": privacy,
        "potentialDuplicateQuestions": duplicates,
        "allowedDecisions": sorted(DECISIONS),
        "canPromote": candidate["status"] in ACTIVE_STATUSES and privacy["status"] == "passed",
        "fingerprint": fingerprint(state),
        "candidate": {
            "question": candidate["question_text"],
            "completionCriterion": candidate["completion_criterion"],
            "priority": candidate["priority"],
            "subjectType": candidate["subject_type"],
            "subjectId": candidate["subject_id"],
            "subjectLabel": candidate["subject_label"],
            "evidence": json.loads(candidate["evidence_json"]),
        },
    }


def question_candidate_preview(root: Path, candidate_id: str) -> dict[str, Any]:
    initialize(root)
    with connect(root) as connection:
        require_tables(connection, REQUIRED_TABLES)
        connection.execute("PRAGMA query_only = ON")
        return _preview(connection, _required(candidate_id, "candidateId"))


def apply_question_candidate_decision(
    root: Path,
    candidate_id: str,
    *,
    decision: str,
    expected_fingerprint: str,
    actor: str,
    rationale: str,
    command_id: str,
    target_question_id: str | None = None,
    target_candidate_id: str | None = None,
) -> dict[str, Any]:
    candidate_id = _required(candidate_id, "candidateId")
    expected_fingerprint = _required(expected_fingerprint, "expectedFingerprint")
    actor = _required(actor, "actor")
    rationale = _required(rationale, "rationale")
    command_id = _required(command_id, "commandId")
    if len(rationale) < 20:
        raise QuestionCurationError("Uzasadnienie musi mieć co najmniej 20 znaków")
    if decision not in DECISIONS:
        raise QuestionCurationError("Nieobsługiwana decyzja triage")
    if decision == "merge" and not target_question_id:
        raise QuestionCurationError("Decyzja merge wymaga targetQuestionId")
    if decision == "supersede" and not target_candidate_id:
        raise QuestionCurationError("Decyzja supersede wymaga targetCandidateId")

    request = {
        "candidateId": candidate_id,
        "decision": decision,
        "expectedFingerprint": expected_fingerprint,
        "targetQuestionId": target_question_id,
        "targetCandidateId": target_candidate_id,
        "actor": actor,
        "rationale": rationale,
    }
    request_fingerprint = fingerprint(request)
    initialize(root)
    with connect(root) as connection:
        require_tables(connection, REQUIRED_TABLES)
        connection.execute("BEGIN IMMEDIATE")
        try:
            replay = replay_command(
                connection,
                command_id=command_id,
                command_type="question_candidate_decision",
                object_type="research_question_candidate",
                object_id=candidate_id,
                request_fingerprint=request_fingerprint,
            )
        except CommandEnvelopeConflict as error:
            raise QuestionCurationConflict(str(error)) from error
        if replay is not None:
            return replay

        preview = _preview(connection, candidate_id)
        if preview["fingerprint"] != expected_fingerprint:
            raise QuestionCurationConflict(
                "Kandydatura lub jej zależności zmieniły się po podglądzie"
            )
        candidate = _candidate_row(connection, candidate_id)
        if candidate["status"] not in ACTIVE_STATUSES:
            raise QuestionCurationConflict(
                f"Kandydatura ma status {candidate['status']} i nie przyjmuje tej decyzji"
            )
        if decision == "promote" and preview["privacyCheck"]["status"] != "passed":
            raise QuestionCurationConflict(
                "Kandydatury z zablokowaną kontrolą prywatności nie można promować"
            )

        now = utc_now()
        promoted_question_id: str | None = None
        if decision == "promote":
            promoted_question_id = stable_id("question", candidate_id)
            target_type = (
                candidate["subject_type"]
                if candidate["subject_type"] in QUESTION_TARGET_TYPES
                else "other"
            )
            connection.execute(
                """INSERT INTO research_questions(
                       id,case_id,question,completion_criterion,status,created_at,updated_at,priority
                   ) VALUES(?,?,?,?, 'open',?,?,?)""",
                (
                    promoted_question_id, candidate["case_id"], candidate["question_text"],
                    candidate["completion_criterion"], now, now, candidate["priority"],
                ),
            )
            connection.execute(
                """INSERT INTO research_question_targets(
                       id,question_id,target_type,target_id,target_label,is_primary,notes,created_at
                   ) VALUES(?,?,?,?,?,1,?,?)""",
                (
                    stable_id("question_target", promoted_question_id, candidate["subject_type"], candidate["subject_id"]),
                    promoted_question_id, target_type, candidate["subject_id"],
                    candidate["subject_label"], "Promocja deterministycznej kandydatury po triage.", now,
                ),
            )
            scope = connection.execute(
                "SELECT id FROM research_scopes WHERE legacy_case_id=?", (candidate["case_id"],)
            ).fetchone()
            if scope is not None:
                connection.execute(
                    """INSERT INTO research_question_scopes(
                           id,question_id,scope_id,role,is_primary,notes,created_at
                       ) VALUES(?,?,?,'primary',1,?,?)""",
                    (
                        stable_id("research_question_scope", promoted_question_id, scope["id"]),
                        promoted_question_id, scope["id"],
                        "Zakres odziedziczony z kandydatury deterministycznej.", now,
                    ),
                )
            target_status = "promoted"
        elif decision == "merge":
            if connection.execute(
                "SELECT 1 FROM research_questions WHERE id=?", (target_question_id,)
            ).fetchone() is None:
                raise LookupError(f"Nie znaleziono pytania {target_question_id}")
            promoted_question_id = target_question_id
            target_status = "merged"
        elif decision == "supersede":
            if target_candidate_id == candidate_id or connection.execute(
                "SELECT 1 FROM research_question_candidates WHERE id=?", (target_candidate_id,)
            ).fetchone() is None:
                raise QuestionCurationError("Nieprawidłowa kandydatura zastępująca")
            target_status = "superseded"
        else:
            target_status = "dismissed" if decision == "dismiss" else "deferred"

        connection.execute(
            """UPDATE research_question_candidates
                  SET status=?,promoted_question_id=?,merged_into_candidate_id=?,
                      privacy_status=?,privacy_json=?,updated_at=?
                WHERE id=?""",
            (
                target_status, promoted_question_id,
                target_candidate_id if decision == "supersede" else None,
                preview["privacyCheck"]["status"], canonical_json(preview["privacyCheck"]),
                now, candidate_id,
            ),
        )
        result = {
            "candidateId": candidate_id,
            "decision": decision,
            "status": target_status,
            "questionId": promoted_question_id,
            "targetCandidateId": target_candidate_id,
            "commandId": command_id,
            "projectionStatus": "pending",
            "alreadyApplied": False,
        }
        record_command(
            connection,
            command_id=command_id,
            command_type="question_candidate_decision",
            object_type="research_question_candidate",
            object_id=candidate_id,
            input_fingerprint=expected_fingerprint,
            request_fingerprint=request_fingerprint,
            actor=actor,
            rationale=rationale,
            result=result,
        )
        connection.execute(
            """INSERT INTO research_question_candidate_decisions(
                   id,candidate_id,revision,decision,target_question_id,target_candidate_id,
                   expected_fingerprint,privacy_check_json,actor,rationale,command_id,decided_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                stable_id("question_candidate_decision", command_id), candidate_id,
                candidate["current_revision"], decision, promoted_question_id,
                target_candidate_id, expected_fingerprint,
                canonical_json(preview["privacyCheck"]), actor, rationale, command_id, now,
            ),
        )
        connection.execute(
            """INSERT INTO audit_log(
                   occurred_at,actor,action,object_type,object_id,before_json,after_json,rationale
               ) VALUES(?,?,'question_candidate_decision','research_question_candidate',?,?,?,?)""",
            (
                now, actor, candidate_id, canonical_json(candidate), canonical_json(result), rationale,
            ),
        )
        return result


__all__ = [
    "QuestionCurationConflict",
    "QuestionCurationError",
    "apply_question_candidate_decision",
    "question_candidate_preview",
    "refresh_question_candidates",
]
