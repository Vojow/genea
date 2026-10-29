from __future__ import annotations

import json
from pathlib import Path

from .db import connect, initialize
from .research import require_tables
from .workspace import stable_id, utc_now


CLAIM_STATUSES = {
    "imported_unverified",
    "Speculation",
    "Possible",
    "Probable",
    "Confirmed",
    "Rejected",
}
PRIVACY_CLASSES = {
    "public_historical",
    "deceased",
    "living",
    "restricted",
    "family_private",
    "strict_private",
}
READING_REVIEW_STATUSES = {
    "agent_single_pass",
    "agent_consensus",
    "agent_adjudicated",
    "human_review_required",
    "human_reviewed",
}
READING_REVIEW_INPUT_ROLES = {
    "blind_primary",
    "blind_secondary",
    "contextual",
    "agent_adjudicator",
    "human_review",
}
_READING_REVIEW_TRANSITIONS = {
    "agent_single_pass": READING_REVIEW_STATUSES,
    "agent_consensus": {
        "agent_consensus",
        "agent_adjudicated",
        "human_review_required",
        "human_reviewed",
    },
    "agent_adjudicated": {
        "agent_adjudicated",
        "human_review_required",
        "human_reviewed",
    },
    "human_review_required": {"human_review_required", "human_reviewed"},
    "human_reviewed": {"human_reviewed"},
}


def _json_object(value: str | None) -> dict:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _first_text(*values: object) -> str | None:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _reading_provenance(row) -> dict:
    payload = _json_object(row["payload_json"])
    metadata = _json_object(row["metadata_json"])
    payload_reading = payload.get("reading") if isinstance(payload.get("reading"), dict) else {}
    metadata_reading = (
        metadata.get("reading") if isinstance(metadata.get("reading"), dict) else {}
    )
    context_values = [
        container.get("context_disclosed")
        for container in (payload, payload_reading, metadata, metadata_reading)
        if "context_disclosed" in container
    ]
    context_disclosed = None
    if context_values:
        if not all(isinstance(value, bool) for value in context_values):
            raise ValueError("context_disclosed musi być wartością boolean")
        if len(set(context_values)) > 1:
            raise ValueError("Sprzeczne wartości context_disclosed w payload/metadata")
        context_disclosed = bool(context_values[0])
    reader_id = _first_text(
        payload_reading.get("reader_id"),
        payload_reading.get("reader"),
        metadata_reading.get("reader_id"),
        metadata_reading.get("reader"),
        row["model_or_reviewer"],
    )
    run_id = _first_text(
        payload_reading.get("reader_run_id"),
        payload_reading.get("run_id"),
        payload_reading.get("agent_run_id"),
        payload.get("run_id"),
        payload.get("agent_run_id"),
        metadata_reading.get("reader_run_id"),
        metadata_reading.get("run_id"),
        metadata_reading.get("agent_run_id"),
        metadata.get("run_id"),
        metadata.get("agent_run_id"),
        # Legacy frozen analyses did not record a tool run id. Their immutable
        # reading-version id is a safe surrogate; explicit telemetry wins.
        row["id"],
    )
    return {
        "reader_id": reader_id,
        "run_id": run_id,
        "context_disclosed": context_disclosed,
        "reading_type": row["reading_type"],
    }


def _validate_review_inputs(status: str, inputs: list[dict]) -> list[dict]:
    if not inputs:
        raise ValueError("Decyzja recenzji wymaga co najmniej jednego wejścia")
    roles = [item["input_role"] for item in inputs]
    if len(roles) != len(set(roles)):
        raise ValueError("Każda rola wejściowa może wystąpić tylko raz")
    versions = [item["reading_version_id"] for item in inputs]
    if len(versions) != len(set(versions)):
        raise ValueError("Jedna wersja odczytu nie może pełnić dwóch ról")
    unknown_roles = set(roles) - READING_REVIEW_INPUT_ROLES
    if unknown_roles:
        raise ValueError("Nieprawidłowa rola wejścia recenzji: " + ", ".join(sorted(unknown_roles)))
    by_role = {item["input_role"]: item for item in inputs}
    for role in ("blind_primary", "blind_secondary"):
        item = by_role.get(role)
        if item and (item["reading_type"] != "blind" or item["context_disclosed"] is not False):
            raise ValueError(
                "Rola blind wymaga odczytu typu blind z jawnym context_disclosed=false"
            )
    if status == "agent_single_pass" and set(roles) != {"blind_primary"}:
        raise ValueError("agent_single_pass wymaga dokładnie jednego blind_primary")
    if status in {"agent_consensus", "agent_adjudicated"}:
        required = {"blind_primary", "blind_secondary"}
        if not required.issubset(by_role):
            raise ValueError(f"{status} wymaga blind_primary i blind_secondary")
        blind = [by_role["blind_primary"], by_role["blind_secondary"]]
        for item in blind:
            if not item["reader_id"] or not item["run_id"]:
                raise ValueError("Konsensus wymaga jawnych reader_id i run_id")
        if len({item["reader_id"] for item in blind}) != 2:
            raise ValueError("Odczyty blind muszą pochodzić od różnych czytelników")
        if len({item["run_id"] for item in blind}) != 2:
            raise ValueError("Odczyty blind muszą pochodzić z różnych uruchomień")
    if status == "agent_consensus" and set(roles) != {"blind_primary", "blind_secondary"}:
        raise ValueError("agent_consensus przyjmuje wyłącznie dwa niezależne odczyty blind")
    if status == "agent_adjudicated":
        adjudicator = by_role.get("agent_adjudicator")
        if adjudicator is None:
            raise ValueError("agent_adjudicated wymaga wejścia agent_adjudicator")
        if adjudicator["reading_type"] not in {"contextual", "agent_review", "adjudication"}:
            raise ValueError("agent_adjudicator wymaga odczytu contextual/agent_review/adjudication")
        if not adjudicator["reader_id"] or not adjudicator["run_id"]:
            raise ValueError("agent_adjudicator wymaga jawnych reader_id i run_id")
        if adjudicator["reader_id"] in {
            by_role["blind_primary"]["reader_id"],
            by_role["blind_secondary"]["reader_id"],
        }:
            raise ValueError("Agent rozstrzygający musi być niezależny od czytelników blind")
        if adjudicator["run_id"] in {
            by_role["blind_primary"]["run_id"],
            by_role["blind_secondary"]["run_id"],
        }:
            raise ValueError("Rozstrzygnięcie musi pochodzić z niezależnego uruchomienia")
    if "contextual" in by_role and by_role["contextual"]["reading_type"] != "contextual":
        raise ValueError("Rola contextual wymaga odczytu typu contextual")
    if "human_review" in by_role and by_role["human_review"]["reading_type"] != "human_review":
        raise ValueError("Rola human_review wymaga odczytu typu human_review")
    if status == "human_reviewed" and "human_review" not in by_role:
        raise ValueError("human_reviewed wymaga rzeczywistego wejścia human_review")
    return inputs


def _review_decision_row(connection, decision_id: str) -> dict:
    row = connection.execute(
        "SELECT * FROM reading_review_decisions WHERE id=?", (decision_id,)
    ).fetchone()
    if row is None:
        raise LookupError(f"Nie znaleziono decyzji recenzji: {decision_id}")
    decision = dict(row)
    decision["decision"] = _json_object(decision.pop("decision_json"))
    decision["inputs"] = [
        {
            **dict(input_row),
            "metadata": _json_object(input_row["metadata_json"]),
        }
        for input_row in connection.execute(
            """SELECT * FROM reading_review_inputs
               WHERE decision_id=? ORDER BY id""",
            (decision_id,),
        )
    ]
    for item in decision["inputs"]:
        item.pop("metadata_json", None)
        if item["context_disclosed"] is not None:
            item["context_disclosed"] = bool(item["context_disclosed"])
    return decision


def record_reading_review_decision(
    root: Path,
    source_item_id: str,
    status: str,
    inputs: list[dict],
    *,
    decided_by: str,
    decided_by_kind: str,
    decided_by_run_id: str | None = None,
    rationale: str,
    decision: dict | None = None,
    expected_revision: int | None = None,
) -> dict:
    if status not in READING_REVIEW_STATUSES:
        raise ValueError("Nieprawidłowy status decyzji recenzji")
    if not source_item_id.strip() or not decided_by.strip() or not rationale.strip():
        raise ValueError("Decyzja wymaga źródła, autora i uzasadnienia")
    if decided_by_kind not in {"agent", "human"}:
        raise ValueError("decided_by_kind musi mieć wartość agent albo human")
    if status == "human_reviewed" and decided_by_kind != "human":
        raise ValueError("human_reviewed wymaga decided_by_kind=human")
    if status.startswith("agent_") and decided_by_kind != "agent":
        raise ValueError(f"{status} wymaga decided_by_kind=agent")
    if decision is not None and not isinstance(decision, dict):
        raise ValueError("decision musi być obiektem JSON")
    normalized: list[dict] = []
    for item in inputs:
        if not isinstance(item, dict):
            raise ValueError("Każde wejście recenzji musi być obiektem JSON")
        version_id = str(item.get("reading_version_id") or "").strip()
        role = str(item.get("input_role") or "").strip()
        if not version_id or not role:
            raise ValueError("Wejście wymaga reading_version_id i input_role")
        normalized.append({"reading_version_id": version_id, "input_role": role})
    initialize(root)
    now = utc_now()
    with connect(root) as connection:
        require_tables(
            connection,
            {
                "readings",
                "reading_versions",
                "reading_review_decisions",
                "reading_review_inputs",
                "audit_log",
            },
        )
        source = connection.execute("SELECT id FROM source_items WHERE id=?", (source_item_id,)).fetchone()
        if source is None:
            raise LookupError(f"Nie znaleziono dokumentu: {source_item_id}")
        version_ids = [item["reading_version_id"] for item in normalized]
        placeholders = ",".join("?" for _ in version_ids)
        rows = connection.execute(
            f"""SELECT rv.id,rv.payload_json,r.source_item_id,r.reading_type,
                       r.model_or_reviewer,r.metadata_json
                FROM reading_versions rv JOIN readings r ON r.id=rv.reading_id
                WHERE rv.id IN ({placeholders})""",
            version_ids,
        ).fetchall()
        if len(rows) != len(version_ids):
            raise ValueError("Nie znaleziono wszystkich wersji wejściowych")
        rows_by_id = {row["id"]: row for row in rows}
        if {row["source_item_id"] for row in rows} != {source_item_id}:
            raise ValueError("Wszystkie wejścia muszą należeć do wskazanego dokumentu")
        enriched = []
        for item in normalized:
            provenance = _reading_provenance(rows_by_id[item["reading_version_id"]])
            enriched.append({**item, **provenance})
        _validate_review_inputs(status, enriched)
        inputs_by_role = {item["input_role"]: item for item in enriched}
        if status == "agent_adjudicated":
            adjudicator = inputs_by_role["agent_adjudicator"]
            if adjudicator["reading_type"] not in {"agent_review", "adjudication"}:
                raise ValueError(
                    "agent_adjudicated wymaga osobnego odczytu agent_review/adjudication, "
                    "nie istniejącego contextual"
                )
            if adjudicator["context_disclosed"] is not True:
                raise ValueError(
                    "Odczyt adjudicatora wymaga jawnego context_disclosed=true"
                )
            if decided_by.strip() != adjudicator["reader_id"]:
                raise ValueError("decided_by musi wskazywać czytelnika adjudicatora")
            if not decided_by_run_id or decided_by_run_id.strip() != adjudicator["run_id"]:
                raise ValueError("decided_by_run_id musi wskazywać przebieg adjudicatora")
        if status == "human_reviewed":
            human_input = inputs_by_role["human_review"]
            if decided_by.strip() != human_input["reader_id"]:
                raise ValueError("decided_by musi wskazywać autora human_review")
            if not decided_by_run_id or decided_by_run_id.strip() != human_input["run_id"]:
                raise ValueError("decided_by_run_id musi wskazywać przebieg human_review")
        previous_row = connection.execute(
            """SELECT * FROM reading_review_decisions
               WHERE source_item_id=? ORDER BY revision DESC LIMIT 1""",
            (source_item_id,),
        ).fetchone()
        revision = int(previous_row["revision"]) + 1 if previous_row else 1
        if expected_revision is not None and expected_revision != revision:
            raise ValueError(
                f"Konflikt rewizji: oczekiwano {expected_revision}, bieżąca następna rewizja to {revision}"
            )
        if previous_row and status not in _READING_REVIEW_TRANSITIONS[previous_row["status"]]:
            raise ValueError(
                f"Niedozwolone przejście {previous_row['status']} -> {status}"
            )
        decision_id = stable_id("reading_review", source_item_id, str(revision), status)
        connection.execute(
            """INSERT INTO reading_review_decisions(
                   id,source_item_id,revision,supersedes_decision_id,status,
                   decided_by,decided_by_kind,decided_by_run_id,rationale,
                   decision_json,created_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                decision_id,
                source_item_id,
                revision,
                previous_row["id"] if previous_row else None,
                status,
                decided_by.strip(),
                decided_by_kind,
                decided_by_run_id.strip() if decided_by_run_id else None,
                rationale.strip(),
                json.dumps(decision or {}, ensure_ascii=False, sort_keys=True),
                now,
            ),
        )
        for item in enriched:
            input_id = stable_id("reading_review_input", decision_id, item["input_role"])
            connection.execute(
                """INSERT INTO reading_review_inputs(
                       id,decision_id,reading_version_id,input_role,reader_id,run_id,
                       context_disclosed,metadata_json,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    input_id,
                    decision_id,
                    item["reading_version_id"],
                    item["input_role"],
                    item["reader_id"],
                    item["run_id"],
                    None if item["context_disclosed"] is None else int(item["context_disclosed"]),
                    json.dumps({"reading_type": item["reading_type"]}, ensure_ascii=False),
                    now,
                ),
            )
        after = _review_decision_row(connection, decision_id)
        connection.execute(
            """INSERT INTO audit_log(
                   occurred_at,actor,action,object_type,object_id,
                   before_json,after_json,rationale
               ) VALUES (?,?,'record_reading_review_decision','reading_review_decision',?,?,?,?)""",
            (
                now,
                decided_by.strip(),
                decision_id,
                json.dumps(dict(previous_row), ensure_ascii=False) if previous_row else None,
                json.dumps(after, ensure_ascii=False),
                rationale.strip(),
            ),
        )
    return after


def get_reading_review_decision(root: Path, decision_id: str) -> dict:
    initialize(root)
    with connect(root) as connection:
        require_tables(connection, {"reading_review_decisions", "reading_review_inputs"})
        return _review_decision_row(connection, decision_id)


def list_reading_review_decisions(
    root: Path,
    *,
    source_item_id: str | None = None,
    status: str | None = None,
    latest_only: bool = False,
    limit: int = 100,
) -> list[dict]:
    if status is not None and status not in READING_REVIEW_STATUSES:
        raise ValueError("Nieprawidłowy status decyzji recenzji")
    if limit < 1 or limit > 1000:
        raise ValueError("limit musi mieścić się w zakresie 1..1000")
    initialize(root)
    with connect(root) as connection:
        require_tables(connection, {"reading_review_decisions", "reading_review_inputs"})
        clauses: list[str] = []
        values: list[object] = []
        if source_item_id:
            clauses.append("d.source_item_id=?")
            values.append(source_item_id)
        if status:
            clauses.append("d.status=?")
            values.append(status)
        if latest_only:
            clauses.append(
                "d.revision=(SELECT MAX(current.revision) FROM reading_review_decisions current "
                "WHERE current.source_item_id=d.source_item_id)"
            )
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = connection.execute(
            f"""SELECT d.id FROM reading_review_decisions d{where}
                ORDER BY d.created_at DESC,d.id DESC LIMIT ?""",
            (*values, limit),
        ).fetchall()
        return [_review_decision_row(connection, row["id"]) for row in rows]


def record_reading_comparison(
    root: Path,
    source_item_id: str,
    blind_version_id: str,
    contextual_version_id: str,
    differences: list[dict],
    *,
    reviewer_version_id: str | None = None,
    status: str = "draft",
) -> dict:
    if blind_version_id == contextual_version_id:
        raise ValueError("Odczyt ślepy i kontekstowy muszą być różnymi wersjami")
    if status not in {"draft", "reviewed"}:
        raise ValueError("status porównania musi być draft albo reviewed")
    if status == "reviewed" and not reviewer_version_id:
        raise ValueError("Zatwierdzone porównanie wymaga osobnej wersji human_review")
    initialize(root)
    item = {
        "id": stable_id("reading_comparison", blind_version_id, contextual_version_id),
        "source_item_id": source_item_id,
        "blind_version_id": blind_version_id,
        "contextual_version_id": contextual_version_id,
        "reviewer_version_id": reviewer_version_id,
        "differences_json": json.dumps(differences, ensure_ascii=False),
        "status": status,
        "created_at": utc_now(),
        "reviewed_at": utc_now() if status == "reviewed" else None,
    }
    with connect(root) as connection:
        require_tables(
            connection,
            {"readings", "reading_versions", "reading_comparisons", "audit_log"},
        )
        requested_version_ids = [blind_version_id, contextual_version_id]
        if reviewer_version_id:
            requested_version_ids.append(reviewer_version_id)
        placeholders = ",".join("?" for _ in requested_version_ids)
        versions = connection.execute(
            f"""SELECT rv.id, r.source_item_id, r.reading_type
                FROM reading_versions rv JOIN readings r ON r.id=rv.reading_id
                WHERE rv.id IN ({placeholders})""",
            requested_version_ids,
        ).fetchall()
        if len(versions) != len(requested_version_ids) or {
            row["source_item_id"] for row in versions
        } != {source_item_id}:
            raise ValueError("Wszystkie wersje muszą należeć do wskazanego dokumentu")
        types = {row["id"]: row["reading_type"] for row in versions}
        if types.get(blind_version_id) != "blind" or types.get(contextual_version_id) != "contextual":
            raise ValueError("Porównanie wymaga odpowiednio odczytu blind i contextual")
        if reviewer_version_id and types.get(reviewer_version_id) != "human_review":
            raise ValueError("Wersja zatwierdzająca musi mieć typ human_review")
        existing = connection.execute(
            "SELECT * FROM reading_comparisons WHERE id=?", (item["id"],)
        ).fetchone()
        if existing is not None and existing["status"] == "reviewed" and status == "draft":
            raise ValueError("Nie można zdegradować zatwierdzonego porównania do draft")
        connection.execute(
            """INSERT INTO reading_comparisons(
                   id, source_item_id, blind_version_id, contextual_version_id,
                   reviewer_version_id, differences_json, status, created_at, reviewed_at
               ) VALUES (
                   :id, :source_item_id, :blind_version_id, :contextual_version_id,
                   :reviewer_version_id, :differences_json, :status, :created_at, :reviewed_at
               ) ON CONFLICT(id) DO UPDATE SET
                   reviewer_version_id=excluded.reviewer_version_id,
                   differences_json=excluded.differences_json,
                   status=excluded.status,
                   reviewed_at=excluded.reviewed_at""",
            item,
        )
        after = dict(
            connection.execute(
                "SELECT * FROM reading_comparisons WHERE id=?", (item["id"],)
            ).fetchone()
        )
        connection.execute(
            """INSERT INTO audit_log(
                   occurred_at,actor,action,object_type,object_id,
                   before_json,after_json,rationale
               ) VALUES (?,?,'record_reading_comparison','reading_comparison',?,?,?,?)""",
            (
                utc_now(),
                "review-workflow",
                item["id"],
                json.dumps(dict(existing), ensure_ascii=False) if existing else None,
                json.dumps(after, ensure_ascii=False),
                "Zapis porównania odczytu ślepego i kontekstowego.",
            ),
        )
    return item


def label_privacy(
    root: Path,
    object_type: str,
    object_id: str,
    classification: str,
    rationale: str,
) -> dict:
    if not object_type.strip() or not object_id.strip():
        raise ValueError("Etykieta prywatności wymaga typu i identyfikatora obiektu")
    if classification not in PRIVACY_CLASSES:
        raise ValueError("Nieprawidłowa klasyfikacja prywatności")
    if not rationale.strip():
        raise ValueError("Klasyfikacja prywatności wymaga uzasadnienia")
    now = utc_now()
    item = {
        "id": stable_id("privacy", object_type, object_id),
        "object_type": object_type.strip(),
        "object_id": object_id.strip(),
        "classification": classification,
        "rationale": rationale.strip(),
        "created_at": now,
        "updated_at": now,
    }
    initialize(root)
    with connect(root) as connection:
        require_tables(connection, {"privacy_labels"})
        connection.execute(
            """INSERT INTO privacy_labels(
                   id, object_type, object_id, classification, rationale,
                   created_at, updated_at
               ) VALUES (
                   :id, :object_type, :object_id, :classification, :rationale,
                   :created_at, :updated_at
               ) ON CONFLICT(object_type, object_id) DO UPDATE SET
                   classification=excluded.classification,
                   rationale=excluded.rationale,
                   updated_at=excluded.updated_at""",
            item,
        )
    return item


def _confirmed_gate(connection, claim_id: str) -> dict:
    claim = connection.execute(
        "SELECT case_id,predicate FROM claims WHERE id=?", (claim_id,)
    ).fetchone()
    case_id = claim[0] if claim else None
    evidence = connection.execute(
        """SELECT ce.*, sa.source_item_id, sa.transcript_segment_id
           FROM claim_evidence ce
           JOIN source_assertions sa ON sa.id=ce.source_assertion_id
           WHERE ce.claim_id=? AND ce.stance='supports'""",
        (claim_id,),
    ).fetchall()
    source_items = {row["source_item_id"] for row in evidence}
    missing_segments = [row["id"] for row in evidence if not row["transcript_segment_id"]]
    unresolved_conflicts = connection.execute(
        """SELECT COUNT(*) FROM conflicts c
           WHERE c.claim_id=? AND (
             c.status<>'resolved' OR NOT EXISTS (
               SELECT 1 FROM conflict_resolutions cr WHERE cr.conflict_id=c.id
             )
           )""",
        (claim_id,),
    ).fetchone()[0]
    final_conclusion = connection.execute(
        """SELECT id FROM conclusions
           WHERE claim_id=? AND status IN ('reviewed','final')
           ORDER BY updated_at DESC LIMIT 1""",
        (claim_id,),
    ).fetchone()
    reviewed_assertions = {
        row[0]
        for row in connection.execute(
            """SELECT DISTINCT sa.id
               FROM claim_evidence ce
               JOIN source_assertions sa ON sa.id=ce.source_assertion_id
               JOIN reading_comparisons rc ON rc.source_item_id=sa.source_item_id
                 AND sa.reading_version_id IN (
                   rc.contextual_version_id, rc.reviewer_version_id
                 )
               WHERE ce.claim_id=? AND ce.stance='supports'
                 AND rc.status='reviewed'""",
            (claim_id,),
        )
    }
    human_reviewed_assertions = {
        row[0]
        for row in connection.execute(
            """SELECT DISTINCT sa.id
               FROM claim_evidence ce
               JOIN source_assertions sa ON sa.id=ce.source_assertion_id
               JOIN reading_review_decisions d ON d.source_item_id=sa.source_item_id
               JOIN reading_review_inputs i ON i.decision_id=d.id
                 AND i.reading_version_id=sa.reading_version_id
               WHERE ce.claim_id=? AND ce.stance='supports'
                 AND d.status='human_reviewed'
                 AND d.revision=(
                   SELECT MAX(latest.revision) FROM reading_review_decisions latest
                   WHERE latest.source_item_id=d.source_item_id
                 )
                 AND EXISTS (
                   SELECT 1 FROM reading_review_inputs human_input
                   JOIN reading_versions human_version
                     ON human_version.id=human_input.reading_version_id
                   JOIN readings human_reading ON human_reading.id=human_version.reading_id
                   WHERE human_input.decision_id=d.id
                     AND human_input.input_role='human_review'
                     AND human_reading.reading_type='human_review'
                 )""",
            (claim_id,),
        )
    }
    reviewed_assertions |= human_reviewed_assertions
    raw_sources = {
        row[0]
        for row in connection.execute(
            """SELECT DISTINCT source_item_id FROM artifacts
               WHERE artifact_type='RAW' AND source_item_id IN (
                 SELECT DISTINCT sa.source_item_id FROM claim_evidence ce
                 JOIN source_assertions sa ON sa.id=ce.source_assertion_id
                 WHERE ce.claim_id=?
               )""",
            (claim_id,),
        )
    }
    question_count = (
        connection.execute(
            "SELECT COUNT(*) FROM research_questions WHERE case_id=?", (case_id,)
        ).fetchone()[0]
        if case_id
        else 0
    )
    covered_question_count = (
        connection.execute(
            """SELECT COUNT(DISTINCT rl.question_id)
               FROM search_coverage sc
               JOIN research_log_entries rl ON rl.id=sc.log_entry_id
               WHERE rl.case_id=? AND sc.appropriate=1
                 AND sc.complete=1 AND sc.systematic=1
                 AND rl.question_id IS NOT NULL""",
            (case_id,),
        ).fetchone()[0]
        if case_id
        else 0
    )
    sufficient_coverage = question_count > 0 and covered_question_count == question_count
    cited_sources = {
        row[0]
        for row in connection.execute(
            """SELECT DISTINCT c.source_item_id FROM citations c
               WHERE c.locator IS NOT NULL AND trim(c.locator)<>''
                 AND c.source_item_id IN (
                   SELECT DISTINCT sa.source_item_id FROM claim_evidence ce
                   JOIN source_assertions sa ON sa.id=ce.source_assertion_id
                   WHERE ce.claim_id=? AND ce.stance='supports'
                 )""",
            (claim_id,),
        )
    }
    reasons: list[str] = []
    if claim and (claim["predicate"] or "").endswith("-summary"):
        reasons.append("twierdzenie zbiorcze musi być rozbite na atomowe claims")
    if not evidence:
        reasons.append("brak powiązań dowodowych")
    if missing_segments:
        reasons.append("nie wszystkie informacje prowadzą do segmentu transkrypcji")
    if source_items - raw_sources:
        reasons.append("nie wszystkie źródła mają RAW")
    if {row["source_assertion_id"] for row in evidence} - reviewed_assertions:
        reasons.append("nie wszystkie informacje wspierające pochodzą z zatwierdzonego porównania")
    if source_items - cited_sources:
        reasons.append("nie wszystkie źródła wspierające mają cytowanie z lokalizatorem")
    if unresolved_conflicts:
        reasons.append("pozostają nierozwiązane konflikty")
    if not final_conclusion:
        reasons.append("brak przejrzanego lub finalnego wniosku")
    if not sufficient_coverage:
        reasons.append("brak kompletnej, właściwej i systematycznej oceny pokrycia kwerendy")
    return {
        "eligible": not reasons,
        "reasons": reasons,
        "evidence_count": len(evidence),
        "dependency_groups": sorted({row["dependency_group"] for row in evidence if row["dependency_group"]}),
        "unresolved_conflicts": unresolved_conflicts,
        "sufficient_search_coverage": bool(sufficient_coverage),
        "covered_questions": covered_question_count,
        "question_count": question_count,
    }


def review_claim_status(
    root: Path,
    claim_id: str,
    target_status: str,
    *,
    reviewer: str,
    rationale: str,
) -> dict:
    if target_status not in CLAIM_STATUSES:
        raise ValueError("Nieprawidłowy status twierdzenia")
    if not reviewer.strip() or not rationale.strip():
        raise ValueError("Zmiana statusu wymaga recenzenta i uzasadnienia")
    initialize(root)
    with connect(root) as connection:
        require_tables(
            connection,
            {
                "claims",
                "claim_evidence",
                "source_assertions",
                "artifacts",
                "reading_comparisons",
                "reading_review_decisions",
                "reading_review_inputs",
                "conflicts",
                "conclusions",
                "audit_log",
                "research_log_entries",
                "search_coverage",
                "research_questions",
                "citations",
                "conflict_resolutions",
            },
        )
        claim = connection.execute("SELECT * FROM claims WHERE id=?", (claim_id,)).fetchone()
        if claim is None:
            raise LookupError(f"Nie znaleziono twierdzenia: {claim_id}")
        gate = _confirmed_gate(connection, claim_id)
        if target_status == "Confirmed" and not gate["eligible"]:
            raise ValueError("Nie można nadać Confirmed: " + "; ".join(gate["reasons"]))
        before = dict(claim)
        now = utc_now()
        connection.execute(
            "UPDATE claims SET status=?, rationale=?, updated_at=? WHERE id=?",
            (target_status, rationale.strip(), now, claim_id),
        )
        after = dict(connection.execute("SELECT * FROM claims WHERE id=?", (claim_id,)).fetchone())
        connection.execute(
            """INSERT INTO audit_log(
                   occurred_at, actor, action, object_type, object_id,
                   before_json, after_json, rationale
               ) VALUES (?, ?, 'review_claim_status', 'claim', ?, ?, ?, ?)""",
            (
                now,
                reviewer.strip(),
                claim_id,
                json.dumps(before, ensure_ascii=False),
                json.dumps(after, ensure_ascii=False),
                rationale.strip(),
            ),
        )
    return {"claim": after, "confirmed_gate": gate}


__all__ = [
    "get_reading_review_decision",
    "label_privacy",
    "list_reading_review_decisions",
    "record_reading_comparison",
    "record_reading_review_decision",
    "review_claim_status",
]
