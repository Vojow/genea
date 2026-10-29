from __future__ import annotations

import json
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
from .workspace import search_fold, stable_id, utc_now


REQUIRED_TABLES = {
    "audit_log",
    "curation_commands",
    "households",
    "household_memberships",
    "identity_candidates",
    "identity_decisions",
    "person_names",
    "person_occurrences",
    "persons",
    "privacy_labels",
    "household_membership_revisions",
}


class FamilyWritebackConflict(ValueError):
    """The accepted preview or command envelope no longer matches."""


_BLOCKED_APPLY_POLICY_MARKERS = ("preview", "pending", "forbidden")


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Pole {label} musi być niepustym tekstem")
    return value.strip()


def _optional_text(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Pole {label} musi być tekstem albo null")
    return value.strip() or None


def _load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Manifest writebacku musi być obiektem JSON")
    if payload.get("version") != 1:
        raise ValueError("Nieobsługiwana wersja manifestu writebacku")
    if not isinstance(payload.get("persons"), list) or not payload["persons"]:
        raise ValueError("Manifest musi zawierać niepustą listę persons")
    if not isinstance(payload.get("household"), dict):
        raise ValueError("Manifest musi zawierać obiekt household")
    return payload


def _apply_policy(payload: dict[str, Any]) -> tuple[bool, str | None]:
    """Return the manifest-level apply decision used by preview and apply."""

    for field in ("reviewStatus", "applyPolicy"):
        value = payload.get(field)
        if value is None:
            continue
        if not isinstance(value, str):
            raise ValueError(f"Pole {field} musi być tekstem albo null")
        normalized = value.casefold()
        if any(marker in normalized for marker in _BLOCKED_APPLY_POLICY_MARKERS):
            return False, f"{field}={value!r} blokuje apply"
    return True, None


def _require_apply_allowed(payload: dict[str, Any]) -> None:
    """Reject manifests that still describe a non-applicable review state."""

    apply_allowed, block_reason = _apply_policy(payload)
    if not apply_allowed:
        raise FamilyWritebackConflict(
            f"Manifest nie może zostać zastosowany: {block_reason}"
        )


def _validate_manifest_name_policy(payload: dict[str, Any]) -> None:
    """Reject unreviewed source variants before any writeback side effects."""

    for person_index, person in enumerate(payload["persons"]):
        if not isinstance(person, dict):
            continue
        names = person.get("names", [])
        if not isinstance(names, list):
            continue
        for name_index, name in enumerate(names):
            if not isinstance(name, dict):
                continue
            name_type = name.get("nameType")
            if (
                isinstance(name_type, str)
                and name_type.strip().casefold() == "source_variant_unreviewed"
            ):
                raise ValueError(
                    "Nie można zastosować niezweryfikowanego wariantu źródłowego "
                    f"persons[{person_index}].names[{name_index}]"
                )


def _audit(
    connection,
    *,
    actor: str,
    action: str,
    object_type: str,
    object_id: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    rationale: str,
) -> None:
    connection.execute(
        """INSERT INTO audit_log(
               occurred_at,actor,action,object_type,object_id,
               before_json,after_json,rationale
           ) VALUES (?,?,?,?,?,?,?,?)""",
        (
            utc_now(),
            actor,
            action,
            object_type,
            object_id,
            json.dumps(before, ensure_ascii=False, sort_keys=True) if before is not None else None,
            json.dumps(after, ensure_ascii=False, sort_keys=True) if after is not None else None,
            rationale,
        ),
    )


def _manifest_summary(payload: dict[str, Any]) -> dict[str, Any]:
    occurrence_count = sum(len(person.get("occurrences", [])) for person in payload["persons"])
    alias_count = sum(len(person.get("names", [])) for person in payload["persons"])
    decision_count = sum(
        len(person.get("identityCandidateIds", [])) for person in payload["persons"]
    )
    return {
        "writebackId": payload.get("writebackId"),
        "householdId": payload.get("household", {}).get("id"),
        "personCount": len(payload["persons"]),
        "occurrenceCount": occurrence_count,
        "aliasCount": alias_count,
        "identityDecisionCount": decision_count,
    }


def _validate_manifest(connection, payload: dict[str, Any]) -> dict[str, Any]:
    _validate_manifest_name_policy(payload)
    writeback_id = _required_text(payload.get("writebackId"), "writebackId")
    _required_text(payload.get("decidedBy"), "decidedBy")
    _required_text(payload.get("rationale"), "rationale")
    household = payload["household"]
    _required_text(household.get("id"), "household.id")
    _required_text(household.get("label"), "household.label")
    _required_text(household.get("verificationStatus"), "household.verificationStatus")

    seen_occurrences: set[str] = set()
    person_occurrences: dict[str, set[str]] = {}
    for index, person in enumerate(payload["persons"]):
        if not isinstance(person, dict):
            raise ValueError(f"persons[{index}] musi być obiektem")
        person_id = _required_text(person.get("id"), f"persons[{index}].id")
        _required_text(person.get("preferredName"), f"persons[{index}].preferredName")
        _required_text(person.get("status"), f"persons[{index}].status")
        occurrences = person.get("occurrences")
        if not isinstance(occurrences, list) or not occurrences:
            raise ValueError(f"persons[{index}].occurrences musi być niepustą listą")
        current_ids: set[str] = set()
        for occurrence_index, occurrence in enumerate(occurrences):
            if not isinstance(occurrence, dict):
                raise ValueError(
                    f"persons[{index}].occurrences[{occurrence_index}] musi być obiektem"
                )
            occurrence_id = _required_text(
                occurrence.get("id"),
                f"persons[{index}].occurrences[{occurrence_index}].id",
            )
            if occurrence_id in seen_occurrences:
                raise ValueError(f"Wystąpienie {occurrence_id} przypisano więcej niż raz")
            seen_occurrences.add(occurrence_id)
            current_ids.add(occurrence_id)
            row = connection.execute(
                "SELECT person_id FROM person_occurrences WHERE id=?", (occurrence_id,)
            ).fetchone()
            if row is None:
                raise LookupError(f"Nie znaleziono wystąpienia {occurrence_id}")
            expected = occurrence.get("expectedPersonId")
            if expected is not None and not isinstance(expected, str):
                raise ValueError(f"expectedPersonId dla {occurrence_id} musi być tekstem albo null")
            if row["person_id"] != expected:
                raise ValueError(
                    f"Stan {occurrence_id} zmienił się: oczekiwano person_id={expected!r}, "
                    f"jest {row['person_id']!r}"
                )
        person_occurrences[person_id] = current_ids

        names = person.get("names", [])
        if not isinstance(names, list):
            raise ValueError(f"persons[{index}].names musi być listą")
        for name_index, name in enumerate(names):
            if not isinstance(name, dict):
                raise ValueError(f"persons[{index}].names[{name_index}] musi być obiektem")
            _required_text(name.get("nameText"), "nameText")
            _required_text(name.get("nameType"), "nameType")

    for person in payload["persons"]:
        occurrence_ids = person_occurrences[person["id"]]
        candidate_ids = person.get("identityCandidateIds", [])
        if not isinstance(candidate_ids, list):
            raise ValueError("identityCandidateIds musi być listą")
        for candidate_id in candidate_ids:
            candidate_id = _required_text(candidate_id, "identityCandidateId")
            row = connection.execute(
                """SELECT occurrence_a_id,occurrence_b_id,status
                   FROM identity_candidates WHERE id=?""",
                (candidate_id,),
            ).fetchone()
            if row is None:
                raise LookupError(f"Nie znaleziono kandydatury {candidate_id}")
            if {row["occurrence_a_id"], row["occurrence_b_id"]} - occurrence_ids:
                raise ValueError(
                    f"Kandydatura {candidate_id} nie łączy dwóch wystąpień osoby {person['id']}"
                )
            if row["status"] not in {"unresolved", "resolved_same"}:
                raise ValueError(
                    f"Kandydatura {candidate_id} ma nieobsługiwany status {row['status']}"
                )
    return _manifest_summary(payload)


def _family_state(connection, payload: dict[str, Any]) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    person_ids = sorted({_required_text(item.get("id"), "person.id") for item in payload["persons"]})
    source_person_ids = sorted(
        {
            expected_person_id
            for person in payload["persons"]
            for item in person.get("occurrences", [])
            if (expected_person_id := item.get("expectedPersonId")) is not None
            and expected_person_id != person.get("id")
        }
    )
    occurrence_ids = sorted(
        {
            _required_text(item.get("id"), "occurrence.id")
            for person in payload["persons"]
            for item in person.get("occurrences", [])
        }
    )
    candidate_ids = sorted(
        {
            _required_text(item, "identityCandidateId")
            for person in payload["persons"]
            for item in person.get("identityCandidateIds", [])
        }
    )
    household_id = _required_text(payload["household"].get("id"), "household.id")

    def rows(table: str, column: str, values: list[str]) -> list[dict[str, Any]]:
        if not values:
            return []
        placeholders = ",".join("?" for _ in values)
        return [
            dict(row)
            for row in connection.execute(
                f"SELECT * FROM {table} WHERE {column} IN ({placeholders}) ORDER BY id",
                tuple(values),
            )
        ]

    source_person_occurrences = rows(
        "person_occurrences", "person_id", source_person_ids
    )
    source_person_occurrence_counts = {
        person_id: sum(
            occurrence["person_id"] == person_id
            for occurrence in source_person_occurrences
        )
        for person_id in source_person_ids
    }

    def privacy_rows(person_values: list[str]) -> list[dict[str, Any]]:
        if not person_values:
            return []
        return [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM privacy_labels "
                "WHERE object_type='person' AND object_id IN ({}) ORDER BY id".format(
                    ",".join("?" for _ in person_values)
                ),
                tuple(person_values),
            )
        ]

    return {
        "persons": rows("persons", "id", person_ids),
        "occurrences": rows("person_occurrences", "id", occurrence_ids),
        "sourcePersons": rows("persons", "id", source_person_ids),
        "sourcePersonOccurrences": source_person_occurrences,
        "sourcePersonOccurrenceCounts": source_person_occurrence_counts,
        "personNames": rows("person_names", "person_id", person_ids),
        "sourcePersonNames": rows("person_names", "person_id", source_person_ids),
        "identityCandidates": rows("identity_candidates", "id", candidate_ids),
        "identityDecisions": rows("identity_decisions", "candidate_id", candidate_ids),
        "privacyLabels": privacy_rows(person_ids),
        "sourcePrivacyLabels": privacy_rows(source_person_ids),
        "household": rows("households", "id", [household_id]),
        "memberships": [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM household_memberships WHERE household_id=? ORDER BY id",
                (household_id,),
            )
        ],
    }


def _preview(connection, payload: dict[str, Any]) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    summary = _validate_manifest(connection, payload)
    apply_allowed, apply_block_reason = _apply_policy(payload)
    manifest_fingerprint = fingerprint(payload)
    state_fingerprint = fingerprint(_family_state(connection, payload))
    return {
        **summary,
        "alreadyApplied": False,
        "manifestFingerprint": manifest_fingerprint,
        "stateFingerprint": state_fingerprint,
        "fingerprint": fingerprint(
            {"manifest": manifest_fingerprint, "state": state_fingerprint}
        ),
        "commandIdRequired": True,
        "membershipPolicy": "additive_revisioned",
        "reviewStatus": payload.get("reviewStatus"),
        "applyPolicy": payload.get("applyPolicy"),
        "applyAllowed": apply_allowed,
        "applyBlockReason": apply_block_reason,
    }


def family_writeback_preview(root: Path, manifest_path: Path) -> dict[str, Any]:
    """Validate a family writeback manifest without changing canonical state."""

    payload = _load_manifest(manifest_path)
    initialize(root)
    with connect(root) as connection:
        require_tables(connection, REQUIRED_TABLES)
        connection.execute("PRAGMA query_only = ON")
        return _preview(connection, payload)


def apply_family_writeback(
    root: Path,
    manifest_path: Path,
    *,
    expected_fingerprint: str,
    command_id: str,
) -> dict[str, Any]:
    """Apply one explicit, auditable family/identity decision manifest.

    Literal ``raw_name`` and ``normalized_name`` values are never changed. Family
    variants are added as typed person-name aliases and to ``search_name`` only.
    """

    payload = _load_manifest(manifest_path)
    _validate_manifest_name_policy(payload)
    _require_apply_allowed(payload)
    initialize(root)
    actor = _required_text(payload.get("decidedBy"), "decidedBy")
    rationale = _required_text(payload.get("rationale"), "rationale")
    writeback_id = _required_text(payload.get("writebackId"), "writebackId")
    expected_fingerprint = _required_text(expected_fingerprint, "expectedFingerprint")
    command_id = _required_text(command_id, "commandId")
    now = utc_now()

    with connect(root) as connection:
        require_tables(connection, REQUIRED_TABLES)
        connection.execute("BEGIN IMMEDIATE")
        request_fingerprint = fingerprint(
            {
                "writebackId": writeback_id,
                "manifestFingerprint": fingerprint(payload),
                "expectedFingerprint": expected_fingerprint,
                "actor": actor,
                "rationale": rationale,
            }
        )
        try:
            replay = replay_command(
                connection,
                command_id=command_id,
                command_type="family_writeback",
                object_type="family_writeback",
                object_id=writeback_id,
                request_fingerprint=request_fingerprint,
            )
        except CommandEnvelopeConflict as error:
            raise FamilyWritebackConflict(str(error)) from error
        if replay is not None:
            return replay
        previous = connection.execute(
            """SELECT command_id FROM curation_commands
               WHERE command_type='family_writeback'
                 AND object_type='family_writeback' AND object_id=?
               ORDER BY committed_at LIMIT 1""",
            (writeback_id,),
        ).fetchone()
        if previous:
            raise FamilyWritebackConflict(
                "Ten writebackId został już zastosowany przez inną komendę"
            )
        manifest_fingerprint = fingerprint(payload)
        state_fingerprint = fingerprint(_family_state(connection, payload))
        current_fingerprint = fingerprint(
            {"manifest": manifest_fingerprint, "state": state_fingerprint}
        )
        if current_fingerprint != expected_fingerprint:
            raise FamilyWritebackConflict(
                "Manifest lub zależne dane zmieniły się po podglądzie; wygeneruj nowy preview"
            )
        validation = _validate_manifest(connection, payload)

        merged_person_ids: set[str] = set()
        linked_occurrences = 0
        aliases_added = 0
        decisions_recorded = 0

        for person in payload["persons"]:
            person_id = person["id"]
            preferred_name = person["preferredName"]
            status = person["status"]
            existing_person = connection.execute(
                "SELECT * FROM persons WHERE id=?", (person_id,)
            ).fetchone()
            if existing_person is None:
                connection.execute(
                    """INSERT INTO persons(id,preferred_name,status,created_at,notes)
                       VALUES (?,?,?,?,?)""",
                    (
                        person_id,
                        preferred_name,
                        status,
                        now,
                        _optional_text(person.get("notes"), "person.notes"),
                    ),
                )
                _audit(
                    connection,
                    actor=actor,
                    action="family_writeback_person_create",
                    object_type="person",
                    object_id=person_id,
                    before=None,
                    after={"preferred_name": preferred_name, "status": status},
                    rationale=rationale,
                )
            else:
                expected_preferred = person.get("expectedPreferredName")
                if expected_preferred is not None and existing_person["preferred_name"] != expected_preferred:
                    raise ValueError(
                        f"Stan osoby {person_id} zmienił się: oczekiwano preferred_name="
                        f"{expected_preferred!r}, jest {existing_person['preferred_name']!r}"
                    )
                updated_notes = (
                    _optional_text(person.get("notes"), "person.notes")
                    if "notes" in person
                    else existing_person["notes"]
                )
                connection.execute(
                    "UPDATE persons SET preferred_name=?,status=?,notes=? WHERE id=?",
                    (preferred_name, status, updated_notes, person_id),
                )
                _audit(
                    connection,
                    actor=actor,
                    action="family_writeback_person_update",
                    object_type="person",
                    object_id=person_id,
                    before={
                        "preferred_name": existing_person["preferred_name"],
                        "status": existing_person["status"],
                        "notes": existing_person["notes"],
                    },
                    after={
                        "preferred_name": preferred_name,
                        "status": status,
                        "notes": updated_notes,
                    },
                    rationale=rationale,
                )

            connection.execute(
                """INSERT INTO privacy_labels(
                       id,object_type,object_id,classification,rationale,created_at,updated_at
                   ) VALUES (?, 'person', ?, 'deceased', ?, ?, ?)
                   ON CONFLICT(object_type,object_id) DO NOTHING""",
                (
                    stable_id("privacy", "person", person_id),
                    person_id,
                    "Osoba historyczna objęta kontrolowanym writebackiem rodzinnym.",
                    now,
                    now,
                ),
            )

            search_values = [preferred_name]
            for name in person.get("names", []):
                name_text = name["nameText"].strip()
                name_type = name["nameType"].strip()
                language = _optional_text(name.get("language"), "name.language")
                name_id = stable_id("person_name", person_id, name_type, name_text)
                cursor = connection.execute(
                    """INSERT INTO person_names(
                           id,person_id,name_text,normalized_name,name_type,language
                       ) VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING""",
                    (name_id, person_id, name_text, search_fold(name_text), name_type, language),
                )
                aliases_added += max(cursor.rowcount, 0)
                search_values.append(name_text)

            canonical_search_terms = list(
                dict.fromkeys(
                    value for value in (search_fold(item) for item in search_values) if value
                )
            )
            for occurrence in person["occurrences"]:
                occurrence_id = occurrence["id"]
                before = connection.execute(
                    "SELECT person_id,search_name,raw_name,normalized_name FROM person_occurrences WHERE id=?",
                    (occurrence_id,),
                ).fetchone()
                old_person_id = before["person_id"]
                source_search_terms = (
                    [part.strip() for part in before["search_name"].split("|")]
                    if before["search_name"]
                    else []
                )
                search_blob = " | ".join(
                    dict.fromkeys(
                        value
                        for value in (
                            search_fold(item)
                            for item in [*source_search_terms, *canonical_search_terms]
                        )
                        if value
                    )
                )
                connection.execute(
                    "UPDATE person_occurrences SET person_id=?,search_name=? WHERE id=?",
                    (person_id, search_blob, occurrence_id),
                )
                linked_occurrences += 1
                if old_person_id and old_person_id != person_id:
                    merged_person_ids.add(old_person_id)
                _audit(
                    connection,
                    actor=actor,
                    action="family_writeback_occurrence_link",
                    object_type="person_occurrence",
                    object_id=occurrence_id,
                    before={"person_id": old_person_id, "search_name": before["search_name"]},
                    after={"person_id": person_id, "search_name": search_blob},
                    rationale=rationale,
                )

            for candidate_id in person.get("identityCandidateIds", []):
                candidate = connection.execute(
                    "SELECT status FROM identity_candidates WHERE id=?", (candidate_id,)
                ).fetchone()
                decision_id = stable_id("identity_decision", writeback_id, candidate_id)
                connection.execute(
                    """INSERT INTO identity_decisions(
                           id,candidate_id,decision,decided_by,decided_at,rationale
                       ) VALUES (?,?, 'same', ?, ?, ?) ON CONFLICT(id) DO NOTHING""",
                    (decision_id, candidate_id, actor, now, rationale),
                )
                connection.execute(
                    "UPDATE identity_candidates SET status='resolved_same' WHERE id=?",
                    (candidate_id,),
                )
                decisions_recorded += 1
                _audit(
                    connection,
                    actor=actor,
                    action="family_writeback_identity_same",
                    object_type="identity_candidate",
                    object_id=candidate_id,
                    before={"status": candidate["status"]},
                    after={"status": "resolved_same", "decision_id": decision_id},
                    rationale=rationale,
                )

        for old_person_id in merged_person_ids:
            remaining = connection.execute(
                "SELECT COUNT(*) FROM person_occurrences WHERE person_id=?", (old_person_id,)
            ).fetchone()[0]
            if remaining == 0:
                old = connection.execute(
                    "SELECT status,notes FROM persons WHERE id=?", (old_person_id,)
                ).fetchone()
                if old:
                    merge_note = f"Scalono przez {writeback_id}; profil bez wystąpień źródłowych."
                    notes = "\n".join(filter(None, [old["notes"], merge_note]))
                    connection.execute(
                        "UPDATE persons SET status='merged',notes=? WHERE id=?",
                        (notes, old_person_id),
                    )
                    _audit(
                        connection,
                        actor=actor,
                        action="family_writeback_person_merged",
                        object_type="person",
                        object_id=old_person_id,
                        before={"status": old["status"]},
                        after={"status": "merged", "remaining_occurrences": 0},
                        rationale=rationale,
                    )

        household = payload["household"]
        household_id = household["id"]
        before_household = connection.execute(
            "SELECT * FROM households WHERE id=?", (household_id,)
        ).fetchone()
        connection.execute(
            """INSERT INTO households(
                   id,place_id,label,date_from,date_to,verification_status,notes
               ) VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                   place_id=excluded.place_id,label=excluded.label,date_from=excluded.date_from,
                   date_to=excluded.date_to,verification_status=excluded.verification_status,
                   notes=excluded.notes""",
            (
                household_id,
                _optional_text(household.get("placeId"), "household.placeId"),
                household["label"],
                _optional_text(household.get("dateFrom"), "household.dateFrom"),
                _optional_text(household.get("dateTo"), "household.dateTo"),
                household["verificationStatus"],
                _optional_text(household.get("notes"), "household.notes"),
            ),
        )
        membership_changes: list[dict[str, Any]] = []
        for person in payload["persons"]:
            membership_id = stable_id("household_membership", household_id, person["id"])
            membership_values = {
                "household_id": household_id,
                "person_id": person["id"],
                "occurrence_id": None,
                "role": _optional_text(person.get("membershipRole"), "person.membershipRole"),
                "date_from": _optional_text(person.get("dateFrom"), "person.dateFrom"),
                "date_to": _optional_text(person.get("dateTo"), "person.dateTo"),
                "source_assertion_id": None,
            }
            current = connection.execute(
                "SELECT * FROM household_memberships WHERE id=?", (membership_id,)
            ).fetchone()
            if current is None:
                connection.execute(
                    """INSERT INTO household_memberships(
                           id,household_id,person_id,occurrence_id,role,date_from,date_to,
                           source_assertion_id
                       ) VALUES (?,?,?,?,?,?,?,?)""",
                    (membership_id, *membership_values.values()),
                )
                change = "created"
            else:
                current_values = {key: current[key] for key in membership_values}
                if current_values == membership_values:
                    continue
                connection.execute(
                    """UPDATE household_memberships
                       SET household_id=?,person_id=?,occurrence_id=?,role=?,date_from=?,
                           date_to=?,source_assertion_id=? WHERE id=?""",
                    (*membership_values.values(), membership_id),
                )
                change = "updated"
            latest_revision = connection.execute(
                """SELECT id,revision FROM household_membership_revisions
                   WHERE membership_id=? ORDER BY revision DESC LIMIT 1""",
                (membership_id,),
            ).fetchone()
            revision = int(latest_revision["revision"]) + 1 if latest_revision else 1
            revision_id = stable_id(
                "household_membership_revision", membership_id, str(revision), command_id
            )
            membership_changes.append(
                {
                    "id": revision_id,
                    "membershipId": membership_id,
                    "revision": revision,
                    "supersedes": latest_revision["id"] if latest_revision else None,
                    "values": membership_values,
                    "change": change,
                }
            )
        _audit(
            connection,
            actor=actor,
            action="family_writeback_household_set",
            object_type="household",
            object_id=household_id,
            before=dict(before_household) if before_household else None,
            after={
                "label": household["label"],
                "verification_status": household["verificationStatus"],
                "member_count": len(payload["persons"]),
            },
            rationale=rationale,
        )

        summary = {
            **_manifest_summary(payload),
            "alreadyApplied": False,
            "linkedOccurrences": linked_occurrences,
            "aliasesAdded": aliases_added,
            "identityDecisionsRecorded": decisions_recorded,
            "mergedProfiles": sorted(merged_person_ids),
            "commandId": command_id,
            "inputFingerprint": expected_fingerprint,
            "membershipChanges": [
                {
                    "membershipId": item["membershipId"],
                    "revision": item["revision"],
                    "change": item["change"],
                }
                for item in membership_changes
            ],
            "preservedUnlistedMemberships": True,
            "projectionStatus": "pending",
        }
        record_command(
            connection,
            command_id=command_id,
            command_type="family_writeback",
            object_type="family_writeback",
            object_id=writeback_id,
            input_fingerprint=expected_fingerprint,
            request_fingerprint=request_fingerprint,
            actor=actor,
            rationale=rationale,
            result=summary,
        )
        for item in membership_changes:
            values = item["values"]
            connection.execute(
                """INSERT INTO household_membership_revisions(
                       id,membership_id,household_id,person_id,occurrence_id,role,
                       date_from,date_to,source_assertion_id,revision,
                       supersedes_revision_id,state,actor,rationale,command_id,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,'active',?,?,?,?)""",
                (
                    item["id"],
                    item["membershipId"],
                    values["household_id"],
                    values["person_id"],
                    values["occurrence_id"],
                    values["role"],
                    values["date_from"],
                    values["date_to"],
                    values["source_assertion_id"],
                    item["revision"],
                    item["supersedes"],
                    actor,
                    rationale,
                    command_id,
                    now,
                ),
            )
        _audit(
            connection,
            actor=actor,
            action="family_writeback_apply",
            object_type="family_writeback",
            object_id=writeback_id,
            before=None,
            after=summary,
            rationale=rationale,
        )
        connection.commit()
        return {**summary, "alreadyApplied": False}


__all__ = [
    "FamilyWritebackConflict",
    "apply_family_writeback",
    "family_writeback_preview",
]
