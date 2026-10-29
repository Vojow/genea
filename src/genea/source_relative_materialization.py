from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .command_envelope import (
    CommandEnvelopeConflict,
    fingerprint,
    record_command,
    replay_command,
)
from .db import connect, initialize
from .research import require_tables
from .source_relative_name_review import (
    NameBindingError,
    normalize_name_bindings,
    resolve_name_bindings,
)
from .workspace import search_fold, stable_id, utc_now


REQUIRED_TABLES = {
    "audit_log",
    "curation_commands",
    "events",
    "participations",
    "person_names",
    "person_occurrences",
    "persons",
    "privacy_labels",
    "source_assertions",
    "source_items",
}

_RESTRICTED = {"living", "restricted", "family_private", "strict_private"}
_HISTORICAL_AGE_YEARS = 120
_VITAL_REVIEW_GATE = {
    "agent_adjudicated",
    "agent_approved",
    "human_reviewed",
    "human_verified",
    "confirmed",
    "documented",
}


class SourceRelativeMaterializationError(ValueError):
    """The source-relative candidate cannot be created safely."""


class SourceRelativeMaterializationConflict(SourceRelativeMaterializationError):
    """The accepted preview no longer matches the database state."""


def _required(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceRelativeMaterializationError(f"Pole {label} musi być niepustym tekstem")
    return value.strip()


def _fold(value: Any) -> str:
    return " ".join(str(value or "").casefold().replace("-", "_").split())


def family_role(role: Any) -> str | None:
    """Return a conservative source-relative role category or ``None``.

    Machine-readable mapping roles are preferred.  The small Polish/English
    fallback exists for older records, but godparents and witnesses are
    explicitly excluded.  This function never infers a relationship from a
    name or from co-occurrence.
    """

    value = _fold(role)
    if not value:
        return None
    machine = value.replace(" ", "_")
    if machine in {"father_and_informant", "mother_and_informant"}:
        return "parent"
    literal_parent_informant = bool(
        re.search(
            r"\b(?:ojciec|matka)\b\s*(?:[,;:]\s*)?\bi\s+"
            r"(?:zgłaszając[ya]|zglaszajac[ya])\b",
            value,
        )
    )
    if any(
        token in value
        for token in (
            "witness",
            "świadek",
            "swiadek",
            "godfather",
            "godmother",
            "chrzestn",
            "urzędnik",
            "urzednik",
            "priest",
            "duchown",
            "informant",
        )
    ):
        return None
    if ("zgłasz" in value or "zglasz" in value) and not literal_parent_informant:
        return None

    if machine in {"deceased", "zmarły", "zmarła"}:
        return "principal"
    if machine in {"groom", "bride", "spouse", "husband", "wife"}:
        return "spouse"
    if machine in {
        "father_stated",
        "father_stated_in_source",
        "mother_mentioned",
        "mother_stated",
        "mother_stated_in_source",
        "source_stated_father",
        "source_stated_mother",
    }:
        return "parent"
    if re.fullmatch(
        r"(?:(?:groom|bride|child|deceased)_)?(?:father|mother|parent)(?:_named)?",
        machine,
    ):
        return "parent"
    if machine in {
        "child", "son", "daughter", "newborn", "baptized_child",
        "child_named_in_source", "born_child",
    }:
        return "child"

    if re.search(r"\b(ojciec|matka|rodzic|father|mother|parent)\b", value):
        return "parent"
    if re.search(
        r"\b(?:pan młody|pan mlody|panna młoda|panna mloda|małżonek|małżonka|mąż|żona|zona|wdowa|bride|groom|spouse)\b",
        value,
    ):
        return "spouse"
    if re.search(r"\b(dziecko|syn|córka|corka|child|son|daughter)\b", value):
        return "child"
    return None


def _json_object(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        result = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        return {}
    return result if isinstance(result, dict) else {}


def explicit_vital_status(evidence: Any, status_texts: list[Any]) -> str | None:
    """Read a subject's explicit life state, rejecting qualified or conflicting text."""

    values: set[str] = set()
    structured = _fold(_json_object(evidence).get("status"))
    if structured in {"deceased", "dead", "zmarły", "zmarla", "zmarła"}:
        values.add("deceased")
    elif structured in {"living", "alive", "żyjący", "żyjąca", "zyjacy", "zyjaca"}:
        values.add("living")

    for raw in status_texts:
        text = _fold(raw)
        if not text or any(
            marker in text
            for marker in ("prawdopodob", "możliw", "mozliw", "probably", "possibly", "maybe", "?")
        ):
            continue
        if re.search(r"\b(zmarły|zmarla|zmarła|nieżyjący|niezyjacy|deceased|dead)\b", text):
            values.add("deceased")
        if re.search(r"\b(żyjący|żyjąca|zyjacy|zyjaca|living|alive)\b", text):
            values.add("living")
    return next(iter(values)) if len(values) == 1 else None


def _reviewed_vital_status(
    occurrence: dict[str, Any],
    part: dict[str, Any],
    occurrence_event_ids: set[str],
) -> tuple[str | None, str | None, str | None]:
    """Return a reviewed life state and its structured basis.

    ``status_text`` is a structured participation field, but imported text is
    not sufficient by itself.  It enters the typed layer only after an
    explicit field/assertion review gate.
    """

    values: set[tuple[str, str]] = set()
    evidence = _json_object(occurrence.get("evidence_json"))
    evidence_review = _fold(
        evidence.get("vitalStatusFieldStatus")
        or evidence.get("fieldStatus")
        or occurrence.get("verification_status")
    )
    structured = explicit_vital_status(evidence, [])
    if structured and evidence_review in _VITAL_REVIEW_GATE:
        evidence_event_id = evidence.get("vitalStatusEventId") or evidence.get("eventId")
        if evidence_event_id:
            if str(evidence_event_id) == str(part["event_id"]):
                values.add((structured, "structured_occurrence_evidence"))
            elif str(evidence_event_id) not in occurrence_event_ids:
                return None, None, "occurrence_vital_status_event_mismatch"
        elif len(occurrence_event_ids) == 1:
            values.add((structured, "structured_occurrence_evidence"))
        else:
            return None, None, "unscoped_occurrence_vital_status_multiple_events"

    assertion_metadata = _json_object(part.get("assertion_metadata_json"))
    assertion_review = _fold(
        assertion_metadata.get("vitalStatusFieldStatus")
        or assertion_metadata.get("fieldStatus")
        or part.get("assertion_verification_status")
    )
    if assertion_review in _VITAL_REVIEW_GATE:
        state = explicit_vital_status({}, [part.get("status_text")])
        if state:
            values.add((state, "reviewed_participation_status"))

    states = {item[0] for item in values}
    if len(states) != 1:
        return None, None, "conflicting_event_vital_status" if len(states) > 1 else None
    state = next(iter(states))
    basis = (
        "structured_occurrence_evidence"
        if (state, "structured_occurrence_evidence") in values
        else "reviewed_participation_status"
    )
    return state, basis, None


def _assertion_revision(row: dict[str, Any]) -> int:
    metadata = _json_object(row.get("metadata_json"))
    try:
        return max(0, int(metadata.get("revision", 0)))
    except (TypeError, ValueError):
        return 0


def _question_gap_proposals(
    source_item_id: str,
    vital_assertions: list[dict[str, Any]],
    participations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    parts = {
        (str(item["occurrence_id"]), str(item["event_id"])): item
        for item in participations
    }
    result: list[dict[str, Any]] = []
    for item in vital_assertions:
        target = item.get("target") or {}
        if target.get("assertion_type") != "source-stated-vital-status":
            continue
        occurrence_id = item["occurrenceId"]
        event_id = item["eventId"]
        part = parts.get((occurrence_id, event_id), {})
        state = target.get("object_text")
        if state == "deceased":
            boundary = part.get("date_to") or part.get("date_from")
            relation = "on_or_before"
            rule = "locate-death-on-or-before-source-boundary"
        elif state == "living":
            boundary = part.get("date_from") or part.get("date_to")
            relation = "on_or_after"
            rule = "locate-death-on-or-after-source-boundary"
        else:
            continue
        if not boundary or not re.match(r"^\d{4}(?:-\d{2}(?:-\d{2})?)?$", str(boundary)):
            continue
        temporal_id = stable_id(
            "temporal_constraint", event_id, occurrence_id, state, relation, str(boundary)
        )
        result.append(
            {
                "candidateId": stable_id("question_gap", rule, temporal_id),
                "rule": rule,
                "status": "proposal_only",
                "writebackAllowed": False,
                "sourceItemId": source_item_id,
                "eventId": event_id,
                "occurrenceId": occurrence_id,
                "sourceAssertionId": item["id"],
                "temporalConstraintId": temporal_id,
                "vitalEventType": "death",
                "relation": relation,
                "boundaryDate": boundary,
                "backingIds": {
                    "sourceItemIds": [source_item_id],
                    "eventIds": [event_id],
                    "occurrenceIds": [occurrence_id],
                    "sourceAssertionIds": [item["id"]],
                    "temporalConstraintIds": [temporal_id],
                },
            }
        )
    return result


def _source_privacy(connection, source_item_id: str, event_dates: list[str], vital_status: str | None) -> tuple[str | None, str | None]:
    label = connection.execute(
        """SELECT classification FROM privacy_labels
           WHERE object_type='source_item' AND object_id=?""",
        (source_item_id,),
    ).fetchone()
    classification = str(label[0]) if label else None
    if classification in _RESTRICTED:
        return None, "restricted_source"
    if classification in {"public_historical", "deceased"}:
        return classification, None

    years = [
        int(match.group(1))
        for value in event_dates
        if (match := re.match(r"^((?:1[5-9]|20)\d{2})", str(value or "")))
    ]
    current_year = datetime.now(timezone.utc).year
    if years and max(years) <= current_year - _HISTORICAL_AGE_YEARS:
        return "public_historical", None
    if vital_status == "deceased":
        return "deceased", None
    return None, "privacy_review_required"


def _source_state(connection, source_item_id: str) -> dict[str, Any]:
    source = connection.execute(
        "SELECT id,title,item_type,date_text,verification_status FROM source_items WHERE id=?",
        (source_item_id,),
    ).fetchone()
    if source is None:
        raise LookupError(f"Nie znaleziono source_item: {source_item_id}")

    occurrences = [
        dict(row)
        for row in connection.execute(
            """SELECT id,source_item_id,person_id,raw_name,normalized_name,search_name,
                      role_text,verification_status,evidence_json
                 FROM person_occurrences WHERE source_item_id=? ORDER BY id""",
            (source_item_id,),
        )
    ]
    occurrence_ids = {row["id"] for row in occurrences}
    participations: list[dict[str, Any]] = []
    events: dict[str, dict[str, Any]] = {}
    if occurrence_ids:
        placeholders = ",".join("?" for _ in occurrence_ids)
        for row in connection.execute(
            f"""SELECT p.id,p.event_id,p.occurrence_id,p.role,p.status_text,
                       p.source_assertion_id,e.source_item_id,e.date_text,e.date_from,e.date_to,
                       sa.reading_version_id AS assertion_reading_version_id,
                       sa.transcript_segment_id AS assertion_segment_id,
                       sa.information_quality AS assertion_information_quality,
                       sa.reading_certainty AS assertion_reading_certainty,
                       sa.verification_status AS assertion_verification_status,
                       sa.metadata_json AS assertion_metadata_json
                  FROM participations p JOIN events e ON e.id=p.event_id
                  LEFT JOIN source_assertions sa ON sa.id=p.source_assertion_id
                 WHERE p.occurrence_id IN ({placeholders})
                 ORDER BY p.occurrence_id,p.event_id,p.id""",
            tuple(sorted(occurrence_ids)),
        ):
            item = dict(row)
            participations.append(item)
            events.setdefault(
                item["event_id"],
                {
                    "id": item["event_id"],
                    "source_item_id": item["source_item_id"],
                    "date_text": item["date_text"],
                    "date_from": item["date_from"],
                    "date_to": item["date_to"],
                },
            )

    person_ids = {
        stable_id("person", "source-relative", source_item_id, row["id"])
        for row in occurrences
    }
    existing_people: list[dict[str, Any]] = []
    existing_names: list[dict[str, Any]] = []
    existing_privacy: list[dict[str, Any]] = []
    if person_ids:
        placeholders = ",".join("?" for _ in person_ids)
        parameters = tuple(sorted(person_ids))
        existing_people = [
            dict(row)
            for row in connection.execute(
                f"SELECT id,preferred_name,status,notes FROM persons WHERE id IN ({placeholders}) ORDER BY id",
                parameters,
            )
        ]
        existing_names = [
            dict(row)
            for row in connection.execute(
                f"""SELECT id,person_id,name_text,normalized_name,name_type,source_assertion_id
                       FROM person_names WHERE person_id IN ({placeholders}) ORDER BY id""",
                parameters,
            )
        ]
        existing_privacy = [
            dict(row)
            for row in connection.execute(
                f"""SELECT id,object_id,classification,rationale
                       FROM privacy_labels
                      WHERE object_type='person' AND object_id IN ({placeholders}) ORDER BY id""",
                parameters,
            )
        ]
    source_privacy = connection.execute(
        """SELECT id,classification,rationale FROM privacy_labels
           WHERE object_type='source_item' AND object_id=? ORDER BY id""",
        (source_item_id,),
    ).fetchall()
    vital_assertions = connection.execute(
        """SELECT id,source_item_id,reading_version_id,transcript_segment_id,
                  assertion_type,subject_ref,predicate,object_text,
                  information_quality,reading_certainty,verification_status,metadata_json
             FROM source_assertions
            WHERE source_item_id=?
              AND assertion_type IN (
                    'source-stated-vital-status',
                    'source-stated-vital-status-retraction'
              )
            ORDER BY id""",
        (source_item_id,),
    ).fetchall()
    # Recognize the original migration's explicit bindings, not an identity
    # guess from names or ID similarity. Include backing rows in the fingerprint.
    legacy_bindings = [dict(row) for row in connection.execute(
        """SELECT o.id AS occurrence_id,o.person_id,o.legacy_mention_id,
                  a.source_id,m.entity_id
             FROM person_occurrences o JOIN mentions m ON m.id=o.legacy_mention_id
             JOIN analyses a ON a.id=m.analysis_id
             JOIN source_items s ON s.id=o.source_item_id AND s.legacy_source_id=a.source_id
             JOIN persons p ON p.id=o.person_id
            WHERE o.source_item_id=? ORDER BY o.id""", (source_item_id,),
    )]
    return {
        "source": dict(source),
        "sourcePrivacy": [dict(row) for row in source_privacy],
        "occurrences": occurrences,
        "participations": participations,
        "events": sorted(events.values(), key=lambda item: item["id"]),
        "existingPeople": existing_people,
        "existingNames": existing_names,
        "existingPrivacy": existing_privacy,
        "existingVitalAssertions": [dict(row) for row in vital_assertions],
        "legacyBindings": legacy_bindings,
    }


def _bindings(value: Any) -> dict:
    try:
        return normalize_name_bindings(value)
    except NameBindingError as error:
        raise SourceRelativeMaterializationError(str(error)) from error


def _plan(connection, source_item_id: str, name_bindings: dict | None = None) -> dict[str, Any]:
    state = _source_state(connection, source_item_id)
    persisted_name_views = [dict(row) for row in connection.execute(
        """SELECT n.person_id,sa.* FROM person_names n
             JOIN source_assertions sa ON sa.id=n.source_assertion_id
            WHERE sa.source_item_id=? AND sa.assertion_type='source-profile-name-view'
            ORDER BY n.person_id,sa.id""", (source_item_id,)
    )]
    if persisted_name_views:
        state["existingNameViews"] = persisted_name_views
    try:
        bound_names = resolve_name_bindings(
            connection, source_item_id, name_bindings or {}, state["occurrences"]
        )
    except NameBindingError as error:
        raise SourceRelativeMaterializationConflict(str(error)) from error
    if name_bindings:
        state["nameBindings"] = name_bindings
        state["nameBindingInputs"] = {key: value["snapshot"] for key, value in bound_names.items()}
    by_occurrence: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in state["participations"]:
        by_occurrence[item["occurrence_id"]].append(item)
    people = {row["id"]: row for row in state["existingPeople"]}
    names = {row["id"]: row for row in state["existingNames"]}
    names_by_person: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in state["existingNames"]:
        names_by_person[str(row["person_id"])].append(row)
    privacy = {row["object_id"]: row for row in state["existingPrivacy"]}
    existing_vital_by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in state["existingVitalAssertions"]:
        metadata = _json_object(row.get("metadata_json"))
        event_id = metadata.get("eventId")
        if event_id and row.get("subject_ref"):
            existing_vital_by_key[(str(row["subject_ref"]), str(event_id))].append(row)

    candidates: list[dict[str, Any]] = []
    vital_assertions: list[dict[str, Any]] = []
    privacy_quarantines: list[dict[str, Any]] = []
    blocked_candidates: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    legacy_link_ids = {
        row["occurrence_id"] for row in state["legacyBindings"]
        if row["occurrence_id"] == stable_id("occ", "legacy-occurrence", f'{row["source_id"]}|{row["entity_id"]}')
        and row["person_id"] == stable_id("person", "legacy-occurrence", f'{row["source_id"]}|{row["entity_id"]}')
    }
    for occurrence in state["occurrences"]:
        occurrence_id = occurrence["id"]
        if occurrence["verification_status"] == "retracted":
            excluded.append({"occurrenceId": occurrence_id, "reason": "retracted_occurrence"})
            continue
        person_id = stable_id("person", "source-relative", source_item_id, occurrence_id)
        parts = by_occurrence.get(occurrence_id, [])
        family_parts = [item for item in parts if family_role(item["role"])]
        categories = {family_role(item["role"]) for item in family_parts}
        if not categories:
            excluded.append({"occurrenceId": occurrence_id, "reason": "ineligible_role"})
            continue
        linked_child_spouse = (
            categories == {"child", "spouse"}
            and (occurrence["person_id"] == person_id or occurrence_id in legacy_link_ids)
        )
        if len(categories) != 1 and not linked_child_spouse:
            conflicts.append({"occurrenceId": occurrence_id, "reason": "ambiguous_family_role", "roles": sorted(categories)})
            continue
        # An exact existing source-relative or migration-backed link can carry
        # both roles. Legacy links still reach the unchanged already_assigned
        # exclusion below; never create or rebind their profiles here.
        projected_family_role = "spouse" if linked_child_spouse else next(iter(categories))
        foreign_events = sorted({item["event_id"] for item in parts if item["source_item_id"] != source_item_id})
        if foreign_events:
            conflicts.append({"occurrenceId": occurrence_id, "reason": "event_source_mismatch", "eventIds": foreign_events})
            continue
        if occurrence_id not in bound_names and any(
            row["subject_ref"] == occurrence_id
            and row["person_id"] == occurrence.get("person_id")
            for row in persisted_name_views
        ):
            # SN-PROV-006: never let the old unbound facade dequalify a name
            # whose exact curator binding has already been persisted.
            conflicts.append({"occurrenceId": occurrence_id, "reason": "explicit_name_binding_required"})
            continue
        raw_name = str(occurrence["raw_name"] or "").strip()
        if not raw_name:
            conflicts.append({"occurrenceId": occurrence_id, "reason": "missing_name"})
            continue

        occurrence_evidence = _json_object(occurrence["evidence_json"])
        occurrence_event_ids = {str(item["event_id"]) for item in parts}
        vital_statuses: set[str] = set()
        planned_vital_keys: set[tuple[str, str]] = set()
        for part in family_parts:
            vital_key = (occurrence_id, str(part["event_id"]))
            if vital_key in planned_vital_keys:
                continue
            planned_vital_keys.add(vital_key)
            vital_status, vital_basis, vital_scope_error = _reviewed_vital_status(
                occurrence, part, occurrence_event_ids
            )
            if vital_scope_error:
                conflicts.append(
                    {
                        "occurrenceId": occurrence_id,
                        "eventId": part["event_id"],
                        "reason": vital_scope_error,
                    }
                )
                continue
            if vital_status:
                vital_statuses.add(vital_status)
            history = existing_vital_by_key.get(vital_key, [])
            active = [
                row
                for row in history
                if row["assertion_type"] == "source-stated-vital-status"
                and row["verification_status"] not in {"retracted", "superseded"}
            ]
            next_revision = max((_assertion_revision(row) for row in history), default=0) + 1
            exact_backing = {
                "sourceItemId": source_item_id,
                "eventId": part["event_id"],
                "occurrenceId": occurrence_id,
                "originalParticipationId": part["id"],
                "originalParticipationAssertionId": part["source_assertion_id"],
                "basis": vital_basis,
            }

            same_active = [row for row in active if row["object_text"] == vital_status]
            backing_matches = False
            if len(same_active) == 1:
                current_metadata = _json_object(same_active[0]["metadata_json"])
                backing_matches = all(
                    current_metadata.get(key) == value
                    for key, value in exact_backing.items()
                )
            if vital_status and len(active) == 1 and len(same_active) == 1 and backing_matches:
                vital_assertions.append(
                    {
                        "id": same_active[0]["id"],
                        "occurrenceId": occurrence_id,
                        "eventId": part["event_id"],
                        "action": "no_op",
                        "target": same_active[0],
                        "retire": [],
                    }
                )
                continue

            retire_status = "superseded" if vital_status else "retracted"
            retire = [
                {"id": row["id"], "fromStatus": row["verification_status"], "toStatus": retire_status}
                for row in active
            ]
            if vital_status:
                review_status = _fold(
                    occurrence_evidence.get("vitalStatusFieldStatus")
                    or occurrence_evidence.get("fieldStatus")
                )
                if review_status not in _VITAL_REVIEW_GATE:
                    assertion_metadata = _json_object(part.get("assertion_metadata_json"))
                    review_status = _fold(
                        assertion_metadata.get("vitalStatusFieldStatus")
                        or assertion_metadata.get("fieldStatus")
                        or part.get("assertion_verification_status")
                    )
                assertion_id = stable_id(
                    "assertion", "source-vital-status", source_item_id,
                    part["event_id"], occurrence_id, vital_status, str(next_revision),
                )
                metadata = {
                    **exact_backing,
                    "revision": next_revision,
                    "supersedesAssertionIds": [item["id"] for item in retire],
                    "fieldStatus": review_status,
                    "literalStatusText": part["status_text"],
                }
                target_assertion = {
                    "id": assertion_id,
                    "source_item_id": source_item_id,
                    "reading_version_id": part["assertion_reading_version_id"],
                    "transcript_segment_id": part["assertion_segment_id"],
                    "assertion_type": "source-stated-vital-status",
                    "subject_ref": occurrence_id,
                    "predicate": "vital-status-at-event",
                    "object_text": vital_status,
                    "information_quality": part["assertion_information_quality"] or "Indeterminable",
                    "reading_certainty": part["assertion_reading_certainty"] or "Ambiguous",
                    "verification_status": review_status,
                    "metadata_json": json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                }
                action = "create" if not retire else "supersede"
            elif active:
                retracted_id = active[0]["id"]
                assertion_id = stable_id(
                    "assertion", "source-vital-status-retraction", source_item_id,
                    part["event_id"], occurrence_id, retracted_id, str(next_revision),
                )
                metadata = {
                    **exact_backing,
                    "basis": "reviewed_status_removed",
                    "revision": next_revision,
                    "retractsAssertionIds": [item["id"] for item in retire],
                }
                target_assertion = {
                    "id": assertion_id,
                    "source_item_id": source_item_id,
                    "reading_version_id": part["assertion_reading_version_id"],
                    "transcript_segment_id": part["assertion_segment_id"],
                    "assertion_type": "source-stated-vital-status-retraction",
                    "subject_ref": occurrence_id,
                    "predicate": "retracts-vital-status-at-event",
                    "object_text": retracted_id,
                    "information_quality": part["assertion_information_quality"] or "Indeterminable",
                    "reading_certainty": part["assertion_reading_certainty"] or "Ambiguous",
                    "verification_status": "active",
                    "metadata_json": json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                }
                action = "retract"
            else:
                continue
            vital_assertions.append(
                {
                    "id": assertion_id,
                    "occurrenceId": occurrence_id,
                    "eventId": part["event_id"],
                    "action": action,
                    "target": target_assertion,
                    "retire": retire,
                }
            )

        if occurrence["person_id"] and occurrence["person_id"] != person_id:
            excluded.append({"occurrenceId": occurrence_id, "reason": "already_assigned", "personId": occurrence["person_id"]})
            continue
        current_person = people.get(person_id)
        event_dates = [
            item["date_to"] or item["date_from"] or item["date_text"]
            for item in family_parts
        ]
        privacy_vital_status = (
            "deceased" if vital_statuses == {"deceased"} else None
        )
        classification, privacy_error = _source_privacy(
            connection, source_item_id, event_dates, privacy_vital_status
        )
        if privacy_error:
            occurrence_vital_writes = [
                item
                for item in vital_assertions
                if item["occurrenceId"] == occurrence_id
                and item["action"] in {"retract", "supersede"}
            ]
            blocked_candidates.append(
                {
                    "occurrenceId": occurrence_id,
                    "personId": occurrence.get("person_id"),
                    "reason": privacy_error,
                    "vitalMaintenanceAllowed": bool(occurrence_vital_writes),
                }
            )
            if not occurrence_vital_writes:
                conflicts.append(
                    {
                        "occurrenceId": occurrence_id,
                        "personId": occurrence.get("person_id"),
                        "reason": privacy_error,
                    }
                )
            if occurrence_vital_writes and current_person:
                current_privacy = privacy.get(person_id)
                if not current_privacy or current_privacy["classification"] not in _RESTRICTED:
                    privacy_quarantines.append(
                        {
                            "personId": person_id,
                            "occurrenceId": occurrence_id,
                            "privacyId": (
                                current_privacy["id"]
                                if current_privacy
                                else stable_id("privacy", "source-relative", person_id)
                            ),
                            "action": "update" if current_privacy else "create",
                            "expectedClassification": (
                                current_privacy["classification"] if current_privacy else None
                            ),
                            "targetClassification": "restricted",
                            "reason": "vital_status_removed_or_corrected_privacy_fail_closed",
                        }
                    )
            continue

        assertion_ids = sorted(
            {
                str(item["source_assertion_id"])
                for item in family_parts
                if item["source_assertion_id"]
            }
        )
        source_assertion_id = assertion_ids[0] if len(assertion_ids) == 1 else None
        bound_name = bound_names.get(occurrence_id)
        if bound_name:
            raw_name = bound_name["evidence"]["literal"]
            source_assertion_id = bound_name["assertion"]["id"]
        privacy_id = stable_id("privacy", "source-relative", person_id)
        expected_notes = (
            f"Kandydat źródłowy z wystąpienia {occurrence_id}; "
            "tożsamość między źródłami pozostaje nierozstrzygnięta."
        )
        target_person = {
            "id": person_id,
            "preferred_name": raw_name,
            "status": "candidate",
            "notes": expected_notes,
        }
        target_normalized_name = (
            search_fold(raw_name) if bound_name
            else occurrence["normalized_name"] or search_fold(raw_name)
        )
        matching_names = [
            row
            for row in names_by_person.get(person_id, [])
            if row["name_text"] == raw_name
            and row["normalized_name"] == target_normalized_name
            and row["name_type"] == "source"
            and row["source_assertion_id"] == source_assertion_id
        ]
        if len(matching_names) > 1:
            conflicts.append(
                {
                    "occurrenceId": occurrence_id,
                    "personId": person_id,
                    "reason": "ambiguous_matching_person_names",
                    "nameIds": sorted(row["id"] for row in matching_names),
                }
            )
            continue
        if matching_names:
            name_id = matching_names[0]["id"]
        elif current_person:
            name_id = stable_id(
                "person_name",
                "source-relative-revision",
                source_item_id,
                occurrence_id,
                raw_name,
                str(target_normalized_name),
                str(source_assertion_id or ""),
            )
        else:
            name_id = stable_id("person_name", "source-relative", source_item_id, occurrence_id)
        target_name = {
            "id": name_id,
            "person_id": person_id,
            "name_text": raw_name,
            "normalized_name": target_normalized_name,
            "name_type": "source",
            "source_assertion_id": source_assertion_id,
        }
        target_privacy = {
            "id": privacy_id,
            "object_id": person_id,
            "classification": classification,
        }
        action = "create"
        if current_person:
            if (
                current_person["status"] != "candidate"
                or current_person["notes"] != expected_notes
            ):
                conflicts.append({"occurrenceId": occurrence_id, "personId": person_id, "reason": "person_id_collision"})
                continue
            current_name = names.get(name_id)
            if current_name:
                comparable_name = {key: current_name[key] for key in target_name}
                if comparable_name != target_name:
                    conflicts.append({"occurrenceId": occurrence_id, "personId": person_id, "reason": "person_name_collision"})
                    continue
            current_privacy = privacy.get(person_id)
            if current_privacy and current_privacy["classification"] != classification:
                conflicts.append({"occurrenceId": occurrence_id, "personId": person_id, "reason": "privacy_label_collision"})
                continue
            if current_person["preferred_name"] != raw_name:
                if occurrence["person_id"] != person_id:
                    conflicts.append(
                        {
                            "occurrenceId": occurrence_id,
                            "personId": person_id,
                            "reason": "unsafe_person_name_revision_without_exact_occurrence_link",
                        }
                    )
                    continue
                action = "revise_name"
            else:
                action = (
                    "no_op"
                    if occurrence["person_id"] == person_id and current_name and current_privacy
                    and (not bound_name or bound_name["assertionExists"])
                    else "complete"
                )

        candidates.append(
            {
                "occurrenceId": occurrence_id,
                "personId": person_id,
                "nameId": name_id,
                "privacyId": privacy_id,
                "familyRole": projected_family_role,
                "eventIds": sorted({item["event_id"] for item in family_parts}),
                "sourceAssertionIds": assertion_ids,
                "vitalStatusesByEvent": {
                    item["eventId"]: item["target"].get("object_text")
                    for item in vital_assertions
                    if item["occurrenceId"] == occurrence_id
                    and item["target"].get("assertion_type")
                    == "source-stated-vital-status"
                },
                "privacyClassification": classification,
                "person": target_person,
                "name": target_name,
                "action": action,
                **({"nameEvidence": bound_name["evidence"],
                    "nameAssertion": bound_name["assertion"],
                    "nameAssertionExists": bound_name["assertionExists"]} if bound_name else {}),
            }
        )

    return {
        "sourceItemId": source_item_id,
        "inputFingerprint": fingerprint(state),
        "status": "conflict"
        if conflicts or any(
            not item["vitalMaintenanceAllowed"] for item in blocked_candidates
        )
        else (
            "ready"
            if any(item["action"] != "no_op" for item in candidates)
            or any(item["action"] != "no_op" for item in vital_assertions)
            or bool(privacy_quarantines)
            else "conflict"
            if blocked_candidates
            else "no_op"
        ),
        "candidates": candidates,
        "vitalAssertions": vital_assertions,
        "privacyQuarantines": privacy_quarantines,
        "blockedCandidates": blocked_candidates,
        "questionGapProposals": _question_gap_proposals(
            source_item_id, vital_assertions, state["participations"]
        ),
        "excluded": excluded,
        "conflicts": conflicts,
        "summary": {
            "create": sum(item["action"] == "create" for item in candidates),
            "complete": sum(item["action"] == "complete" for item in candidates),
            "reviseName": sum(item["action"] == "revise_name" for item in candidates),
            "noOp": sum(item["action"] == "no_op" for item in candidates),
            "excluded": len(excluded),
            "conflict": len(conflicts),
            "blockedCandidate": len(blocked_candidates),
            "privacyQuarantine": len(privacy_quarantines),
            "vitalAssertionCreate": sum(
                item["action"] == "create" for item in vital_assertions
            ),
            "vitalAssertionNoOp": sum(
                item["action"] == "no_op" for item in vital_assertions
            ),
            "vitalAssertionSupersede": sum(
                item["action"] == "supersede" for item in vital_assertions
            ),
            "vitalAssertionRetract": sum(
                item["action"] == "retract" for item in vital_assertions
            ),
        },
    }


def source_relative_materialization_preview(
    root: Path, source_item_id: str, *, name_bindings: dict | None = None
) -> dict[str, Any]:
    """Preview source candidates with optional explicit curator name bindings.

    A bound reading review qualifies the literal name, never actor identity.
    Without bindings the legacy occurrence-name contract remains unchanged.
    """
    source_item_id = _required(source_item_id, "sourceItemId")
    name_bindings = _bindings(name_bindings)
    initialize(root)
    with connect(root) as connection:
        require_tables(connection, REQUIRED_TABLES)
        connection.execute("PRAGMA query_only = ON")
        return _plan(connection, source_item_id, name_bindings)


def source_relative_materialization_follow_up(
    root: Path, source_item_id: str, *, name_bindings: dict | None = None
) -> dict[str, Any]:
    """Return the mandatory gated follow-up after a document mapping writeback.

    This helper is intentionally read-only.  It makes the next operation
    deterministic, but never converts preview into implicit authorization.
    """

    preview = source_relative_materialization_preview(root, source_item_id, name_bindings=name_bindings)
    return {
        "kind": "source-relative-materialization",
        "required": preview["status"] in {"ready", "conflict"},
        "status": preview["status"],
        "sourceItemId": source_item_id,
        "inputFingerprint": preview["inputFingerprint"],
        "preview": preview,
        "applyRequires": [
            "accepted inputFingerprint",
            "unique commandId",
            "actor",
            "rationale",
        ],
        "automaticApply": False,
    }


def _audit(connection, *, actor: str, action: str, object_type: str, object_id: str, before: Any, after: Any, rationale: str) -> None:
    connection.execute(
        """INSERT INTO audit_log(
               occurred_at,actor,action,object_type,object_id,before_json,after_json,rationale
           ) VALUES (?,?,?,?,?,?,?,?)""",
        (
            utc_now(), actor, action, object_type, object_id,
            json.dumps(before, ensure_ascii=False, sort_keys=True) if before is not None else None,
            json.dumps(after, ensure_ascii=False, sort_keys=True) if after is not None else None,
            rationale,
        ),
    )


def apply_source_relative_materialization(
    root: Path,
    source_item_id: str,
    *,
    expected_fingerprint: str,
    actor: str,
    rationale: str,
    command_id: str,
    name_bindings: dict | None = None,
) -> dict[str, Any]:
    source_item_id = _required(source_item_id, "sourceItemId")
    expected_fingerprint = _required(expected_fingerprint, "expectedFingerprint")
    actor = _required(actor, "actor")
    rationale = _required(rationale, "rationale")
    command_id = _required(command_id, "commandId")
    name_bindings = _bindings(name_bindings)
    if len(rationale) < 20:
        raise SourceRelativeMaterializationError("Uzasadnienie musi mieć co najmniej 20 znaków")
    request = {
        "sourceItemId": source_item_id,
        "expectedFingerprint": expected_fingerprint,
        "actor": actor,
        "rationale": rationale,
    }
    if name_bindings:
        request["nameBindings"] = name_bindings
    request_fingerprint = fingerprint(request)

    initialize(root)
    with connect(root) as connection:
        require_tables(connection, REQUIRED_TABLES)
        connection.execute("BEGIN IMMEDIATE")
        try:
            replay = replay_command(
                connection,
                command_id=command_id,
                command_type="source_relative_materialization",
                object_type="source_item",
                object_id=source_item_id,
                request_fingerprint=request_fingerprint,
            )
        except CommandEnvelopeConflict as error:
            raise SourceRelativeMaterializationConflict(str(error)) from error
        if replay is not None:
            return replay

        preview = _plan(connection, source_item_id, name_bindings)
        if preview["inputFingerprint"] != expected_fingerprint:
            raise SourceRelativeMaterializationConflict(
                "Źródło lub zależne rekordy zmieniły się po podglądzie"
            )
        if preview["status"] == "conflict":
            raise SourceRelativeMaterializationConflict(
                "Materializacja zatrzymana przez konflikt lub brak bezpiecznej klasyfikacji prywatności"
            )

        now = utc_now()
        created = completed = revised_names = no_op = 0
        vital_assertions_created = 0
        vital_assertions_retired = 0
        for assertion in preview["vitalAssertions"]:
            if assertion["action"] == "no_op":
                continue
            for retired in assertion.get("retire", []):
                current = connection.execute(
                    "SELECT verification_status FROM source_assertions WHERE id=?",
                    (retired["id"],),
                ).fetchone()
                if current is None or current["verification_status"] != retired["fromStatus"]:
                    raise SourceRelativeMaterializationConflict(
                        f"Typed vital assertion {retired['id']} zmieniło stan podczas operacji"
                    )
                connection.execute(
                    "UPDATE source_assertions SET verification_status=? WHERE id=?",
                    (retired["toStatus"], retired["id"]),
                )
                vital_assertions_retired += 1
                _audit(
                    connection,
                    actor=actor,
                    action="source_vital_status_assertion_retire",
                    object_type="source_assertion",
                    object_id=retired["id"],
                    before={"verificationStatus": retired["fromStatus"]},
                    after={
                        "verificationStatus": retired["toStatus"],
                        "successorAssertionId": assertion["id"],
                    },
                    rationale=rationale,
                )
            target = assertion["target"]
            connection.execute(
                """INSERT INTO source_assertions(
                       id,source_item_id,reading_version_id,transcript_segment_id,
                       assertion_type,subject_ref,predicate,object_text,
                       information_quality,reading_certainty,verification_status,metadata_json
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                tuple(
                    target[key]
                    for key in (
                        "id", "source_item_id", "reading_version_id",
                        "transcript_segment_id", "assertion_type", "subject_ref",
                        "predicate", "object_text", "information_quality",
                        "reading_certainty", "verification_status", "metadata_json",
                    )
                ),
            )
            vital_assertions_created += 1
            _audit(
                connection,
                actor=actor,
                action="source_vital_status_assertion_create",
                object_type="source_assertion",
                object_id=assertion["id"],
                before=None,
                after=target,
                rationale=rationale,
            )
        privacy_quarantines_applied = 0
        for quarantine in preview["privacyQuarantines"]:
            current = connection.execute(
                """SELECT id,classification FROM privacy_labels
                   WHERE object_type='person' AND object_id=?""",
                (quarantine["personId"],),
            ).fetchone()
            current_classification = current["classification"] if current else None
            if current_classification != quarantine["expectedClassification"]:
                raise SourceRelativeMaterializationConflict(
                    f"Klasyfikacja prywatności {quarantine['personId']} zmieniła się podczas operacji"
                )
            if current:
                connection.execute(
                    """UPDATE privacy_labels
                          SET classification='restricted',rationale=?,updated_at=?
                        WHERE id=?""",
                    (
                        "Status życia usunięto lub skorygowano; profil kandydata ukryty do ponownego privacy review.",
                        now,
                        current["id"],
                    ),
                )
            else:
                connection.execute(
                    """INSERT INTO privacy_labels(
                           id,object_type,object_id,classification,rationale,created_at,updated_at
                       ) VALUES (?, 'person', ?, 'restricted', ?, ?, ?)""",
                    (
                        quarantine["privacyId"],
                        quarantine["personId"],
                        "Status życia usunięto lub skorygowano; profil kandydata ukryty do ponownego privacy review.",
                        now,
                        now,
                    ),
                )
            privacy_quarantines_applied += 1
            _audit(
                connection,
                actor=actor,
                action="source_relative_person_privacy_quarantine",
                object_type="person",
                object_id=quarantine["personId"],
                before={"classification": current_classification},
                after={"classification": "restricted", "occurrenceId": quarantine["occurrenceId"]},
                rationale=rationale,
            )
        for item in preview["candidates"]:
            if item["action"] == "no_op":
                no_op += 1
                continue
            name_evidence = ({"nameEvidence": item["nameEvidence"]} if "nameEvidence" in item else {})
            if item.get("nameAssertion") and not item["nameAssertionExists"]:
                target = item["nameAssertion"]
                keys = tuple(target)
                connection.execute(
                    f"INSERT INTO source_assertions({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",
                    tuple(target[key] for key in keys),
                )
                _audit(connection, actor=actor, action="source_profile_name_view_create",
                       object_type="source_assertion", object_id=target["id"],
                       before=None, after={**target, **name_evidence}, rationale=rationale)
            person = item["person"]
            if item["action"] == "create":
                connection.execute(
                    """INSERT INTO persons(id,preferred_name,status,created_at,notes)
                       VALUES (?,?,?,?,?)""",
                    (person["id"], person["preferred_name"], "candidate", now, person["notes"]),
                )
                created += 1
                _audit(
                    connection, actor=actor, action="source_relative_person_create",
                    object_type="person", object_id=person["id"], before=None,
                    after={**person, "sourceItemId": source_item_id, "occurrenceId": item["occurrenceId"], **name_evidence},
                    rationale=rationale,
                )
            elif item["action"] == "revise_name":
                current = connection.execute(
                    "SELECT preferred_name,status,notes FROM persons WHERE id=?",
                    (person["id"],),
                ).fetchone()
                if (
                    current is None
                    or current["status"] != "candidate"
                    or current["notes"] != person["notes"]
                    or current["preferred_name"] == person["preferred_name"]
                ):
                    raise SourceRelativeMaterializationConflict(
                        f"Kandydat {person['id']} zmienił się podczas korekty nazwy"
                    )
                before_name = current["preferred_name"]
                connection.execute(
                    "UPDATE persons SET preferred_name=? WHERE id=?",
                    (person["preferred_name"], person["id"]),
                )
                revised_names += 1
                _audit(
                    connection,
                    actor=actor,
                    action="source_relative_person_name_revise",
                    object_type="person",
                    object_id=person["id"],
                    before={
                        "preferredName": before_name,
                        "status": "candidate",
                        "occurrenceId": item["occurrenceId"],
                    },
                    after={
                        "preferredName": person["preferred_name"],
                        "status": "candidate",
                        "occurrenceId": item["occurrenceId"],
                        "sourceItemId": source_item_id,
                        "identityStatus": "unresolved",
                        **name_evidence,
                    },
                    rationale=rationale,
                )
            else:
                completed += 1

            name = item["name"]
            name_cursor = connection.execute(
                """INSERT INTO person_names(
                       id,person_id,name_text,normalized_name,name_type,source_assertion_id
                   ) VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING""",
                (
                    name["id"], name["person_id"], name["name_text"],
                    name["normalized_name"], name["name_type"], name["source_assertion_id"],
                ),
            )
            if name_cursor.rowcount:
                _audit(
                    connection,
                    actor=actor,
                    action="source_relative_person_name_create",
                    object_type="person_name",
                    object_id=name["id"],
                    before=None,
                    after={
                        **name,
                        "sourceItemId": source_item_id,
                        "occurrenceId": item["occurrenceId"],
                        "identityStatus": "unresolved",
                        **name_evidence,
                    },
                    rationale=rationale,
                )
            connection.execute(
                """INSERT INTO privacy_labels(
                       id,object_type,object_id,classification,rationale,created_at,updated_at
                   ) VALUES (?, 'person', ?, ?, ?, ?, ?) ON CONFLICT(object_type,object_id) DO NOTHING""",
                (
                    item["privacyId"], item["personId"], item["privacyClassification"],
                    "Klasyfikacja kandydata źródłowego wyprowadzona z jawnego statusu lub wieku źródła.",
                    now, now,
                ),
            )
            before = connection.execute(
                "SELECT person_id FROM person_occurrences WHERE id=?",
                (item["occurrenceId"],),
            ).fetchone()
            if before is None or before["person_id"] not in {None, item["personId"]}:
                raise SourceRelativeMaterializationConflict(
                    f"Wystąpienie {item['occurrenceId']} zmieniło przypisanie podczas operacji"
                )
            connection.execute(
                "UPDATE person_occurrences SET person_id=? WHERE id=?",
                (item["personId"], item["occurrenceId"]),
            )
            _audit(
                connection, actor=actor, action="source_relative_occurrence_link",
                object_type="person_occurrence", object_id=item["occurrenceId"],
                before={"personId": before["person_id"]},
                after={
                    "personId": item["personId"], "personStatus": "candidate",
                    "identityStatus": "unresolved", "sourceItemId": source_item_id,
                    "eventIds": item["eventIds"], "sourceAssertionIds": item["sourceAssertionIds"],
                    **name_evidence,
                },
                rationale=rationale,
            )

        result = {
            "sourceItemId": source_item_id,
            "commandId": command_id,
            "inputFingerprint": expected_fingerprint,
            "created": created,
            "completed": completed,
            "revisedNames": revised_names,
            "noOp": no_op,
            "candidatePersonIds": [item["personId"] for item in preview["candidates"]],
            "identityDecisionsCreated": 0,
            "vitalAssertionsCreated": vital_assertions_created,
            "vitalAssertionsRetired": vital_assertions_retired,
            "privacyQuarantinesApplied": privacy_quarantines_applied,
            "projectionStatus": "pending",
        }
        if name_bindings:
            result["nameBindings"] = name_bindings
            result["nameEvidence"] = [item["nameEvidence"] for item in preview["candidates"] if "nameEvidence" in item]
        record_command(
            connection,
            command_id=command_id,
            command_type="source_relative_materialization",
            object_type="source_item",
            object_id=source_item_id,
            input_fingerprint=expected_fingerprint,
            request_fingerprint=request_fingerprint,
            actor=actor,
            rationale=rationale,
            result=result,
        )
        return {**result, "alreadyApplied": False}


__all__ = [
    "SourceRelativeMaterializationConflict",
    "SourceRelativeMaterializationError",
    "apply_source_relative_materialization",
    "explicit_vital_status",
    "family_role",
    "source_relative_materialization_preview",
    "source_relative_materialization_follow_up",
]
