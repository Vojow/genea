from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .db import connect, initialize
from .research import require_tables
from .storage import SQLCipherBackend, configured_database_backend
from .workspace import stable_id, utc_now


REQUIRED_TABS = {
    "People": {"Person ID", "Display name", "Death date", "Generation from root"},
    "Relationships": {
        "Relationship ID",
        "Type",
        "From Person ID",
        "To Person ID",
        "Family ID",
        "Confidence",
        "Source ID",
    },
    "Facts": {"Fact ID", "Subject ID", "Fact type", "Source ID"},
    "Sources": {"Source ID", "Source type", "Title"},
    "Residences": {"Residence ID", "Person / household", "Source ID"},
    "Ancestor Audit": {"Person ID", "Life status"},
}

PERSON_FIELD_MAP = {
    "Given names": "givenNames",
    "Surname": "surname",
    "Sex": "sex",
    "Birth date": "birthDate",
    "Birth place": "birthPlace",
    "Death date": "deathDate",
    "Death place": "deathPlace",
    "Direct ancestor?": "directAncestor",
    "Generation from root": "generationFromRoot",
    "Confidence": "sourceConfidence",
    "MyHeritage RIN": "source.myHeritageRin",
    "GEDCOM xref": "source.gedcomXref",
    "UID": "source.gedcomUid",
    "Last updated in source": "source.lastUpdatedAt",
    "Imported from": "source.importedFrom",
}


def _canonical_payload(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _rows(payload: dict[str, Any], tab: str) -> list[dict[str, Any]]:
    tabs = payload.get("tabs")
    if not isinstance(tabs, dict) or not isinstance(tabs.get(tab), list):
        raise ValueError(f"Brak wymaganej zakładki Drive: {tab}")
    values = tabs[tab]
    if not values or not isinstance(values[0], list):
        raise ValueError(f"Zakładka {tab} nie ma nagłówka")
    headers = [str(value).strip() for value in values[0]]
    missing = REQUIRED_TABS[tab] - set(headers)
    if missing:
        raise ValueError(f"Zakładka {tab} nie ma kolumn: {', '.join(sorted(missing))}")
    result: list[dict[str, Any]] = []
    for raw in values[1:]:
        if not isinstance(raw, list) or not any(value not in {None, ""} for value in raw):
            continue
        result.append(
            {header: raw[index] if index < len(raw) else "" for index, header in enumerate(headers)}
        )
    return result


def _nonempty(value: Any) -> str:
    return str(value or "").strip()


def _private_scope(
    people: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    ancestor_audit: list[dict[str, Any]],
) -> tuple[set[str], Counter[str]]:
    people_by_id = {_nonempty(item["Person ID"]): item for item in people}
    parents_to_children: dict[str, set[str]] = defaultdict(set)
    children_to_parents: dict[str, set[str]] = defaultdict(set)
    partners: dict[str, set[str]] = defaultdict(set)
    for relation in relationships:
        source = _nonempty(relation["From Person ID"])
        target = _nonempty(relation["To Person ID"])
        kind = _nonempty(relation["Type"])
        if source not in people_by_id or target not in people_by_id:
            continue
        if kind in {"Father-child", "Mother-child"}:
            parents_to_children[source].add(target)
            children_to_parents[target].add(source)
        elif kind == "Spouse/partner":
            partners[source].add(target)
            partners[target].add(source)

    def eligible(person_id: str) -> bool:
        person = people_by_id.get(person_id)
        return bool(
            person
            and _nonempty(person.get("Display name"))
            and not _nonempty(person.get("Death date"))
        )

    explicit_living = {
        _nonempty(item["Person ID"])
        for item in ancestor_audit
        if _nonempty(item.get("Life status")).casefold() == "living"
        and eligible(_nonempty(item["Person ID"]))
    }
    # Pokolenia 0-2 są prywatne zgodnie z decyzją użytkownika, nawet jeśli
    # arkusz audytu nie zawiera dla nich osobnego wiersza.
    for person in people:
        generation = _nonempty(person.get("Generation from root"))
        if generation.isdigit() and int(generation) <= 2 and eligible(_nonempty(person["Person ID"])):
            explicit_living.add(_nonempty(person["Person ID"]))

    scope: set[str] = set()
    reasons: dict[str, set[str]] = defaultdict(set)

    def include(person_id: str, reason: str) -> None:
        if eligible(person_id):
            scope.add(person_id)
            reasons[person_id].add(reason)

    for person_id in explicit_living:
        include(person_id, "living-core")
        for related in partners[person_id]:
            include(related, "partner-of-living-core")
        for related in parents_to_children[person_id]:
            include(related, "child-of-living-core")

    protected_siblings: set[str] = set()
    for person_id in explicit_living:
        for parent_id in children_to_parents[person_id]:
            for sibling_id in parents_to_children[parent_id]:
                if sibling_id != person_id and eligible(sibling_id):
                    include(sibling_id, "sibling-of-living-core")
                    protected_siblings.add(sibling_id)
    for person_id in protected_siblings:
        for related in partners[person_id]:
            include(related, "partner-of-protected-sibling")
        for related in parents_to_children[person_id]:
            include(related, "child-of-protected-sibling")

    roots = {
        _nonempty(person["Person ID"])
        for person in people
        if _nonempty(person.get("Direct ancestor?")).casefold() == "root"
    }
    root_partners = {
        partner
        for root_id in roots
        for partner in partners[root_id]
        if eligible(partner)
    }
    spouse_family: set[str] = set(root_partners)
    for partner_id in root_partners:
        include(partner_id, "root-partner")
        for parent_id in children_to_parents[partner_id]:
            include(parent_id, "parent-of-root-partner")
            spouse_family.add(parent_id)
            for sibling_id in parents_to_children[parent_id]:
                if sibling_id != partner_id:
                    include(sibling_id, "sibling-of-root-partner")
                    spouse_family.add(sibling_id)
    for person_id in spouse_family:
        for related in partners[person_id]:
            include(related, "partner-in-spouse-family")
        for related in parents_to_children[person_id]:
            include(related, "child-in-spouse-family")

    reason_counts = Counter(reason for values in reasons.values() for reason in values)
    return scope, reason_counts


def _source_provenance(source_id: str) -> tuple[str, str | None]:
    return "imported_unverified", None


def _upsert_private_field(
    connection,
    *,
    person_id: str,
    field_name: str,
    value: Any,
    provenance_kind: str,
    informant: str | None,
    now: str,
) -> None:  # type: ignore[no-untyped-def]
    value_json = json.dumps(value, ensure_ascii=False, sort_keys=True)
    connection.execute(
        """INSERT INTO private_fields(
               id,object_type,object_id,field_name,value_json,classification,
               provenance_kind,informant,consent_status,created_at,updated_at
           ) VALUES (?, 'person', ?, ?, ?, 'family_private', ?, ?, 'not_asked', ?, ?)
           ON CONFLICT(object_type,object_id,field_name) DO UPDATE SET
               value_json=excluded.value_json,
               classification=excluded.classification,
               provenance_kind=excluded.provenance_kind,
               informant=excluded.informant,
               updated_at=excluded.updated_at""",
        (
            stable_id("private_field", "person", person_id, field_name),
            person_id,
            field_name,
            value_json,
            provenance_kind,
            informant,
            now,
            now,
        ),
    )


def _plan(payload: dict[str, Any]) -> dict[str, Any]:
    people = _rows(payload, "People")
    relationships = _rows(payload, "Relationships")
    facts = _rows(payload, "Facts")
    sources = _rows(payload, "Sources")
    residences = _rows(payload, "Residences")
    ancestor_audit = _rows(payload, "Ancestor Audit")
    scope, reason_counts = _private_scope(people, relationships, ancestor_audit)
    people_by_id = {_nonempty(item["Person ID"]): item for item in people}

    private_relationships = [
        item
        for item in relationships
        if _nonempty(item["From Person ID"]) in scope
        and _nonempty(item["To Person ID"]) in scope
    ]
    private_family_ids = {
        _nonempty(item.get("Family ID")) for item in private_relationships if _nonempty(item.get("Family ID"))
    }
    private_facts = [
        item
        for item in facts
        if _nonempty(item["Subject ID"]) in scope
        or _nonempty(item["Subject ID"]) in private_family_ids
    ]
    private_residences = [
        item
        for item in residences
        if any(
            person_id in _nonempty(item["Person / household"])
            or _nonempty(people_by_id[person_id]["Display name"])
            in _nonempty(item["Person / household"])
            for person_id in scope
        )
    ]
    source_ids = {
        _nonempty(item.get("Source ID"))
        for item in [*private_relationships, *private_facts, *private_residences]
        if _nonempty(item.get("Source ID"))
    }
    source_by_id = {_nonempty(item["Source ID"]): item for item in sources}
    source_metadata = {
        source_id: source_by_id[source_id]
        for source_id in sorted(source_ids)
        if source_id in source_by_id
    }
    return {
        "people": people,
        "peopleById": people_by_id,
        "relationships": relationships,
        "facts": facts,
        "residences": residences,
        "scope": scope,
        "reasonCounts": dict(reason_counts),
        "privateRelationships": private_relationships,
        "privateFamilyIds": private_family_ids,
        "privateFacts": private_facts,
        "privateResidences": private_residences,
        "sourceMetadata": source_metadata,
    }


def import_private_drive_workbook(
    root: Path,
    payload: dict[str, Any],
    *,
    apply: bool = False,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Import Drive wymaga obiektu JSON")
    source = payload.get("source")
    if not isinstance(source, dict) or not _nonempty(source.get("driveFileId")):
        raise ValueError("Import Drive wymaga source.driveFileId")
    plan = _plan(payload)
    fingerprint = hashlib.sha256(_canonical_payload(payload)).hexdigest()
    summary: dict[str, Any] = {
        "status": "dry-run" if not apply else "imported",
        "fingerprint": fingerprint,
        "source": {
            "driveFileId": _nonempty(source.get("driveFileId")),
            "title": _nonempty(source.get("title")),
            "modifiedTime": _nonempty(source.get("modifiedTime")),
        },
        "private": {
            "people": len(plan["scope"]),
            "relationships": len(plan["privateRelationships"]),
            "facts": len(plan["privateFacts"]),
            "residences": len(plan["privateResidences"]),
            "scopeReasons": plan["reasonCounts"],
        },
        "localFirstSuppressed": {
            "people": len(plan["people"]) - len(plan["scope"]),
            "relationships": len(plan["relationships"]) - len(plan["privateRelationships"]),
            "facts": len(plan["facts"]) - len(plan["privateFacts"]),
        },
        "policy": {
            "private": "Drive values imported with explicit provenance into SQLCipher",
            "nonPrivate": "local canonical data wins; Drive rows are not written",
            "identity": "no name-only merge with existing local persons",
        },
    }
    if not apply:
        return summary

    backend = configured_database_backend(root)
    if not isinstance(backend, SQLCipherBackend):
        raise RuntimeError("Import prywatny wymaga aktywnej bazy SQLCipher")
    initialize(root)
    now = utc_now()
    with connect(root) as connection:
        require_tables(
            connection,
            {"persons", "privacy_labels", "private_fields", "private_relationships", "audit_log"},
        )
        previous = connection.execute(
            "SELECT 1 FROM audit_log WHERE action='private_drive_import' AND object_id=?",
            (fingerprint,),
        ).fetchone()
        if previous:
            return {**summary, "status": "already-imported"}

        person_mapping: dict[str, str] = {}
        created = 0
        for drive_person_id in sorted(plan["scope"]):
            person = plan["peopleById"][drive_person_id]
            person_id = stable_id(
                "person_private_drive",
                _nonempty(source["driveFileId"]),
                drive_person_id,
                length=16,
            )
            person_mapping[drive_person_id] = person_id
            exists = connection.execute(
                "SELECT 1 FROM persons WHERE id=?", (person_id,)
            ).fetchone()
            if not exists:
                created += 1
            connection.execute(
                """INSERT INTO persons(id,preferred_name,status,created_at,notes)
                   VALUES (?,NULL,'private',?,NULL)
                   ON CONFLICT(id) DO UPDATE SET status='private'""",
                (person_id, now),
            )
            connection.execute(
                """INSERT INTO privacy_labels(
                       id,object_type,object_id,classification,rationale,created_at,updated_at
                   ) VALUES (?, 'person', ?, 'family_private', ?, ?, ?)
                   ON CONFLICT(object_type,object_id) DO UPDATE SET
                       classification='family_private',rationale=excluded.rationale,
                       updated_at=excluded.updated_at""",
                (
                    stable_id("privacy", "person", person_id),
                    person_id,
                    "Kontrolowany import prywatnych danych z Google Drive; bez scalenia po nazwie.",
                    now,
                    now,
                ),
            )
            _upsert_private_field(
                connection,
                person_id=person_id,
                field_name="preferredName",
                value=_nonempty(person["Display name"]),
                provenance_kind="imported_unverified",
                informant=None,
                now=now,
            )
            _upsert_private_field(
                connection,
                person_id=person_id,
                field_name="source.googleDrive.personId",
                value=drive_person_id,
                provenance_kind="imported_unverified",
                informant=None,
                now=now,
            )
            for source_column, field_name in PERSON_FIELD_MAP.items():
                value = person.get(source_column)
                if _nonempty(value):
                    _upsert_private_field(
                        connection,
                        person_id=person_id,
                        field_name=field_name,
                        value=value,
                        provenance_kind="imported_unverified",
                        informant=None,
                        now=now,
                    )

        relationship_count = 0
        for relation in plan["privateRelationships"]:
            source_person = person_mapping[_nonempty(relation["From Person ID"])]
            target_person = person_mapping[_nonempty(relation["To Person ID"])]
            source_id = _nonempty(relation.get("Source ID"))
            provenance, _informant = _source_provenance(source_id)
            source_kind = _nonempty(relation["Type"])
            if source_kind in {"Father-child", "Mother-child"}:
                relationship_type = "parent"
            elif source_kind == "Spouse/partner" and provenance == "family_testimony":
                relationship_type = "spouse"
            elif source_kind == "Spouse/partner":
                relationship_type = "partner"
            else:
                relationship_type = "other"
            verification = "family_testimony" if provenance == "family_testimony" else "imported_unverified"
            rationale = {
                "driveRelationshipId": _nonempty(relation["Relationship ID"]),
                "driveSourceId": source_id,
                "sourceType": source_kind,
                "sourceConfidence": _nonempty(relation.get("Confidence")),
                "notes": _nonempty(relation.get("Notes")),
            }
            connection.execute(
                """INSERT INTO private_relationships(
                       id,subject_person_id,object_person_id,relationship_type,
                       classification,provenance_kind,verification_status,rationale,
                       created_at,updated_at
                   ) VALUES (?, ?, ?, ?, 'family_private', ?, ?, ?, ?, ?)
                   ON CONFLICT(subject_person_id,object_person_id,relationship_type) DO UPDATE SET
                       classification=excluded.classification,
                       provenance_kind=excluded.provenance_kind,
                       verification_status=excluded.verification_status,
                       rationale=excluded.rationale,updated_at=excluded.updated_at""",
                (
                    stable_id(
                        "private_relationship",
                        source_person,
                        target_person,
                        relationship_type,
                    ),
                    source_person,
                    target_person,
                    relationship_type,
                    provenance,
                    verification,
                    json.dumps(rationale, ensure_ascii=False, sort_keys=True),
                    now,
                    now,
                ),
            )
            relationship_count += 1

        family_members: dict[str, set[str]] = defaultdict(set)
        for relation in plan["privateRelationships"]:
            family_id = _nonempty(relation.get("Family ID"))
            if family_id:
                family_members[family_id].add(_nonempty(relation["From Person ID"]))
                family_members[family_id].add(_nonempty(relation["To Person ID"]))
        fact_field_count = 0
        for fact in plan["privateFacts"]:
            subject_id = _nonempty(fact["Subject ID"])
            targets = {subject_id} if subject_id in person_mapping else family_members.get(subject_id, set())
            provenance, informant = _source_provenance(_nonempty(fact.get("Source ID")))
            value = {
                key: fact.get(key, "")
                for key in (
                    "Fact ID",
                    "Fact type",
                    "Value",
                    "Date",
                    "Place",
                    "Source ID",
                    "Confidence",
                    "Conflict?",
                    "Notes",
                )
            }
            prefix = "fact" if subject_id in person_mapping else "familyFact"
            for target in sorted(targets):
                if target not in person_mapping:
                    continue
                _upsert_private_field(
                    connection,
                    person_id=person_mapping[target],
                    field_name=f"{prefix}.{_nonempty(fact['Fact ID'])}",
                    value=value,
                    provenance_kind=provenance,
                    informant=informant,
                    now=now,
                )
                fact_field_count += 1

        residence_field_count = 0
        for residence in plan["privateResidences"]:
            label = _nonempty(residence["Person / household"])
            matched = {
                drive_id
                for drive_id in plan["scope"]
                if drive_id in label
                or _nonempty(plan["peopleById"][drive_id]["Display name"]) in label
            }
            provenance, informant = _source_provenance(_nonempty(residence.get("Source ID")))
            value = {
                key: residence.get(key, "")
                for key in ("Residence ID", "Place", "Start", "End", "Source ID", "Confidence", "Notes")
            }
            for drive_id in sorted(matched):
                _upsert_private_field(
                    connection,
                    person_id=person_mapping[drive_id],
                    field_name=f"residence.{_nonempty(residence['Residence ID'])}",
                    value=value,
                    provenance_kind=provenance,
                    informant=informant,
                    now=now,
                )
                residence_field_count += 1

        audit_summary = {
            "fingerprint": fingerprint,
            "driveFileId": _nonempty(source["driveFileId"]),
            "privatePeople": len(plan["scope"]),
            "createdPeople": created,
            "relationships": relationship_count,
            "factFields": fact_field_count,
            "residenceFields": residence_field_count,
            "localFirstSuppressed": summary["localFirstSuppressed"],
        }
        connection.execute(
            """INSERT INTO audit_log(
                   occurred_at,actor,action,object_type,object_id,before_json,after_json,rationale
               ) VALUES (?, 'private-curator', 'private_drive_import', 'drive_import', ?, NULL, ?, ?)""",
            (
                now,
                fingerprint,
                json.dumps(audit_summary, ensure_ascii=False, sort_keys=True),
                "Kontrolowany import prywatnego podzbioru; lokalne dane nieprywatne zachowały pierwszeństwo.",
            ),
        )
    summary["applied"] = {
        "createdPeople": created,
        "relationships": relationship_count,
        "factFields": fact_field_count,
        "residenceFields": residence_field_count,
    }
    return summary


__all__ = ["import_private_drive_workbook"]
