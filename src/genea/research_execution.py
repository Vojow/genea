from __future__ import annotations

import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from .archive_providers import list_archive_providers
from .question_backlog import build_research_backlog
from .workspace import stable_id


EXECUTION_SCHEMA_VERSION = 1
_YEAR = re.compile(r"(?<!\d)(1[5-9]\d{2}|20\d{2})(?!\d)")
_EVENT_TERMS = {
    "birth": ("urod", "chrz", "birth", "bapt"),
    "marriage": ("ślub", "małżeń", "marriage", "wedding"),
    "death": ("zgon", "śmier", "death", "burial", "pogrzeb"),
}
_RESTRICTED_EXTERNAL_PROVIDER_NAMES = {"familysearch", "myheritage"}


def _event_types(item: dict[str, Any]) -> list[str]:
    text = " ".join(
        str(value or "")
        for value in (item.get("questionType"), item.get("question"), item.get("completionCriterion"))
    ).casefold()
    return [event for event, terms in _EVENT_TERMS.items() if any(term in text for term in terms)]


def _person_parts(item: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    targets = [
        target for target in item.get("targets", [])
        if target.get("type") in {"person", "person_occurrence", "named_person"}
        and isinstance(target.get("label"), str)
    ]
    target = next((entry for entry in targets if entry.get("primary")), None) or next(iter(targets), None)
    if not target:
        return None, None, None
    label = " ".join(target["label"].split())
    parts = label.split()
    if len(parts) < 2:
        return label or None, None, "target_label_search_only"
    return " ".join(parts[:-1]), parts[-1], "target_label_search_only"


def build_question_archive_plan(
    item: dict[str, Any],
    provider_capabilities: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build deterministic search proposals; never executes or persists them."""

    year_basis = " ".join([
        str(item.get("question") or ""),
        str(item.get("completionCriterion") or ""),
        *(str(target.get("label") or "") for target in item.get("targets", [])),
    ])
    years = sorted({int(value) for value in _YEAR.findall(year_basis)})
    places = [
        target.get("label") for target in item.get("targets", [])
        if target.get("type") in {"place", "place_study"} and target.get("label")
    ]
    given_name, surname, name_basis = _person_parts(item)
    event_types = _event_types(item)
    base_spec = {
        **({"given_name": given_name} if given_name else {}),
        **({"surname": surname} if surname else {}),
        **({"place": places[0]} if places else {}),
        **({"from_year": min(years)} if years else {}),
        **({"to_year": max(years)} if years else {}),
        **({"event_types": event_types} if event_types else {}),
    }
    if not base_spec:
        base_spec = {"query": item.get("question", "")}

    capabilities = {entry["provider"]: entry for entry in provider_capabilities}
    proposals: list[dict[str, Any]] = []
    genealodzy = capabilities.get("genealodzy")
    if genealodzy and surname:
        proposals.append({
            "id": stable_id("archive_query", item["id"], "genealodzy", str(sorted(base_spec.items()))),
            "provider": "genealodzy",
            "status": "ready",
            "executable": True,
            "specification": base_spec,
            "basis": [value for value in [name_basis, "question_text_years" if years else None, "explicit_place_target" if places else None, "question_event_terms" if event_types else None] if value],
            "warning": "Wyniki są kandydaturami źródeł; brak trafienia nie jest dowodem negatywnym.",
        })

    if capabilities.get("szukajwarchiwach"):
        proposals.append({
            "id": stable_id("archive_query", item["id"], "szukajwarchiwach", str(sorted(base_spec.items()))),
            "provider": "szukajwarchiwach",
            "status": "needs_stable_locator",
            "executable": False,
            "specification": {"query": item.get("question", ""), **({"place": places[0]} if places else {})},
            "basis": ["question_text", *( ["explicit_place_target"] if places else [])],
            "gate": "Dodaj publiczny, stabilny URL zespołu, jednostki lub skanu jako start_urls.",
            "warning": "Globalny formularz nie jest stabilnym API i nie będzie automatycznie crawlowany.",
        })

    familysearch = capabilities.get("familysearch")
    if familysearch:
        proposals.append({
            "id": stable_id("archive_query", item["id"], "familysearch", str(sorted(base_spec.items()))),
            "provider": "familysearch",
            "status": "nice_to_have",
            "executable": False,
            "specification": base_spec,
            "basis": ["optional_provider_profile"],
            "gate": "Wymagana legalna sesja użytkownika albo przyszły oficjalny OAuth/API.",
            "warning": "Ta propozycja nie jest wykonywana przez bieżący adapter.",
        })

    external_network_proposal = (
        str(item.get("ruleId", "")).startswith("external_network.")
        or any(
            target.get("type") == "external_network_node"
            for target in item.get("targets", [])
            if isinstance(target, dict)
        )
    )
    if external_network_proposal:
        # External-tree provenance stays internal to the generator and curator
        # audit.  A public execution plan must not reveal which restricted
        # service supplied the candidate network.
        proposals = [
            proposal
            for proposal in proposals
            if proposal.get("provider") not in _RESTRICTED_EXTERNAL_PROVIDER_NAMES
        ]

    proposals.sort(key=lambda entry: (not entry["executable"], entry["provider"], entry["id"]))
    return {
        "schemaVersion": EXECUTION_SCHEMA_VERSION,
        "questionId": item["id"],
        "inputBasis": {
            "origin": item.get("origin"),
            "caseId": item.get("caseId"),
            "questionType": item.get("questionType"),
            "targetIds": sorted(
                f"{target.get('type')}:{target.get('id')}" for target in item.get("targets", [])
                if target.get("type") and target.get("id")
            ),
        },
        "archiveQueries": proposals,
        "automaticDiscovery": False,
        "automaticAcquisition": False,
        "automaticWriteback": False,
    }


def _batch_summaries(connection: sqlite3.Connection, allowed_question_ids: set[str]) -> list[dict[str, Any]]:
    rows = connection.execute(
        """SELECT b.id,b.batch_key,b.case_id,b.question_id,b.input_fingerprint,b.stopping_rule,b.created_at,
                  (SELECT s.status FROM reassessment_batch_states s WHERE s.batch_id=b.id ORDER BY s.revision DESC LIMIT 1) AS status
           FROM reassessment_batches b ORDER BY b.created_at DESC,b.id"""
    ).fetchall()
    batches: list[dict[str, Any]] = []
    for row in rows:
        # Case-only batches can contain names or stopping rules outside the
        # privacy-filtered question scope. They stay hidden until explicitly
        # linked to an allowed canonical question.
        if not row["question_id"] or row["question_id"] not in allowed_question_ids:
            continue
        item_states = Counter(
            value["status"] for value in connection.execute(
                """SELECT r.status FROM reassessment_items i
                   LEFT JOIN reassessment_item_results r ON r.item_id=i.id AND r.revision=(
                     SELECT MAX(latest.revision) FROM reassessment_item_results latest WHERE latest.item_id=i.id
                   ) WHERE i.batch_id=?""",
                (row["id"],),
            ) if value["status"]
        )
        review_states = Counter(
            value["decision"] for value in connection.execute(
                "SELECT decision FROM reassessment_reviews WHERE batch_id=?", (row["id"],)
            )
        )
        batches.append({
            "id": row["id"],
            "batchKey": row["batch_key"],
            "caseId": row["case_id"],
            "questionId": row["question_id"],
            "status": row["status"] or "planned",
            "inputFingerprint": row["input_fingerprint"],
            "stoppingRule": row["stopping_rule"],
            "createdAt": row["created_at"],
            "itemStates": dict(sorted(item_states.items())),
            "reviewDecisions": dict(sorted(review_states.items())),
        })
    return batches


def _active_gate(item: dict[str, Any], plan: dict[str, Any]) -> str:
    if item.get("origin") == "deterministic":
        return "Najpierw kurator musi wykonać triage kandydatury pytania."
    active_batches = [
        batch for batch in plan["batches"]
        if batch.get("status") not in {"completed", "inconclusive", "superseded"}
    ]
    if active_batches:
        return "Kontynuuj najnowszy batch zgodnie ze stopping rule i kolejką wyjątków."
    if any(query["executable"] for query in plan["archiveQueries"]):
        return "Uruchom zatwierdzoną kwerendę read-only i oceń kandydatury źródeł."
    return "Uzupełnij stabilny lokalizator archiwalny przed kwerendą."


def attach_research_execution(root: Path, connection: sqlite3.Connection, backlog: dict[str, Any]) -> None:
    providers = list_archive_providers(root)
    allowed_question_ids = {item["id"] for item in backlog["items"] if item.get("origin") == "curated"}
    batches = _batch_summaries(connection, allowed_question_ids)
    for item in backlog["items"]:
        plan = build_question_archive_plan(item, providers)
        plan["batches"] = [
            batch for batch in batches
            if batch.get("questionId") == item["id"]
        ]
        plan["activeGate"] = _active_gate(item, plan)
        item["executionPlan"] = plan

    backlog["execution"] = {
        "schemaVersion": EXECUTION_SCHEMA_VERSION,
        "mode": "read-only-derived",
        "providers": [
            {
                "provider": provider["provider"],
                "status": provider["status"],
                "interface": provider["interface"],
                "authentication": provider["authentication"],
                **({"profile": provider["profile"]} if provider.get("profile") else {}),
            }
            for provider in providers
            if provider["provider"] in {"genealodzy", "szukajwarchiwach", "familysearch"}
        ],
        "batches": batches,
        "summary": {
            "questions": len(backlog["items"]),
            "readyQueries": sum(
                query["executable"] for item in backlog["items"]
                for query in item["executionPlan"]["archiveQueries"]
            ),
            "linkedBatches": len(batches),
            "automaticWrites": 0,
        },
        "methodNote": (
            "Pytanie prowadzi do propozycji kwerendy, kandydatur źródeł i batcha review. "
            "Każde pobranie, writeback i decyzja tożsamości zachowują osobne bramki kuratorskie."
        ),
    }


def question_archive_plan(root: Path, connection: sqlite3.Connection, question_id: str) -> dict[str, Any]:
    backlog = build_research_backlog(root, connection=connection)
    item = next((entry for entry in backlog["items"] if entry["id"] == question_id), None)
    if not item:
        raise KeyError(f"Nie znaleziono pytania lub kandydatury: {question_id}")
    plan = build_question_archive_plan(item, list_archive_providers(root))
    plan["batches"] = _batch_summaries(connection, {question_id})
    plan["activeGate"] = _active_gate(item, plan)
    return plan


__all__ = ["attach_research_execution", "build_question_archive_plan", "question_archive_plan"]
