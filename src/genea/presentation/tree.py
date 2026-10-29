from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from ..workspace import research_root


def _authorized_deceased_disclosure(root: Path, person: dict[str, Any]) -> bool:
    """Honor only a hash-bound, exact-card local disclosure declaration."""
    exception = person.get("privacyException")
    if person.get("privacy") != "deceased" or not isinstance(exception, dict):
        return False
    if exception.get("kind") != "deceased_public":
        return False
    reference = exception.get("declarationRef")
    digest = exception.get("declarationSHA256")
    if not isinstance(reference, str) or not isinstance(digest, str):
        return False
    relative = Path(reference)
    records = Path("research/records/reassessments")
    if (relative.is_absolute() or ".." in relative.parts
            or not relative.is_relative_to(records) or relative.suffix != ".json"):
        return False
    try:
        workspace = root.resolve()
        records_directory = (root / "research/records").resolve()
        directory = (root / records).resolve()
        path = (root / relative).resolve()
        if (not records_directory.is_relative_to(workspace)
                or not directory.is_relative_to(records_directory)
                or not path.is_relative_to(directory)):
            return False
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != digest:
            return False
        declaration = json.loads(content)
    except (OSError, ValueError, RuntimeError):
        return False
    if (not isinstance(declaration, dict)
            or type(declaration.get("schemaVersion")) is not int
            or declaration["schemaVersion"] != 1
            or declaration.get("type") != "user_family_testimony_and_local_disclosure_authorization"):
        return False
    cards = declaration.get("publicGrandparentCards")
    canonical_id = person.get("canonicalPersonId")
    return bool(
        isinstance(cards, list) and isinstance(canonical_id, str) and canonical_id
        and any(isinstance(card, dict) and card.get("treePersonId") == person.get("id")
                and card.get("personId") == canonical_id for card in cards)
    )


def load_tree(root: Path) -> dict[str, Any] | None:
    path = research_root(root) / "presentation" / "family-tree.json"
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
        raise ValueError("research/presentation/family-tree.json ma nieobsługiwany schemat")
    people = payload.get("people")
    families = payload.get("families")
    if not isinstance(people, list) or not isinstance(families, list):
        raise ValueError("Drzewo prezentacyjne wymaga tablic people i families")
    person_ids = [item.get("id") for item in people if isinstance(item, dict)]
    if len(person_ids) != len(people) or len(person_ids) != len(set(person_ids)):
        raise ValueError("Osoby w drzewie prezentacyjnym wymagają unikalnych identyfikatorów")
    known = set(person_ids)
    for family in families:
        if not isinstance(family, dict):
            raise ValueError("Każda rodzina w drzewie prezentacyjnym musi być obiektem")
        references = list(family.get("parentIds", [])) + list(family.get("childIds", []))
        missing = sorted(set(references) - known)
        if missing:
            raise ValueError(
                f"Rodzina {family.get('id', '[bez id]')} odwołuje się do nieznanych osób: "
                + ", ".join(missing)
            )
        primary_child_id = family.get("primaryChildId")
        if primary_child_id is not None and primary_child_id not in family.get("childIds", []):
            raise ValueError(
                f"Rodzina {family.get('id', '[bez id]')} wskazuje primaryChildId spoza childIds"
            )
        if "showAllChildren" in family and not isinstance(family["showAllChildren"], bool):
            raise ValueError(
                f"Rodzina {family.get('id', '[bez id]')} ma nieprawidłowe showAllChildren"
            )
    branches = payload.get("branches", [])
    if not isinstance(branches, list):
        raise ValueError("branches drzewa prezentacyjnego musi być tablicą")
    branch_ids = [item.get("id") for item in branches if isinstance(item, dict)]
    if len(branch_ids) != len(branches) or len(branch_ids) != len(set(branch_ids)):
        raise ValueError("Gałęzie drzewa wymagają unikalnych identyfikatorów")
    for branch in branches:
        missing = sorted(set(branch.get("personIds", [])) - known)
        if missing:
            raise ValueError(
                f"Gałąź {branch.get('id', '[bez id]')} odwołuje się do nieznanych osób: "
                + ", ".join(missing)
            )
    unions = payload.get("unions", [])
    if not isinstance(unions, list):
        raise ValueError("unions drzewa prezentacyjnego musi być tablicą")
    union_ids = [item.get("id") for item in unions if isinstance(item, dict)]
    if len(union_ids) != len(unions) or len(union_ids) != len(set(union_ids)):
        raise ValueError("Związki drzewa wymagają unikalnych identyfikatorów")
    for union in unions:
        partners = union.get("partnerIds", [])
        if not isinstance(partners, list) or len(partners) != 2:
            raise ValueError(f"Związek {union.get('id')} musi wskazywać dokładnie dwie osoby")
        missing = sorted(set(partners) - known)
        owner_id = union.get("ownerId")
        if owner_id is not None and owner_id not in known:
            missing.append(owner_id)
        if missing:
            raise ValueError(
                f"Związek {union.get('id', '[bez id]')} odwołuje się do nieznanych osób: "
                + ", ".join(missing)
            )
    if payload.get("rootPersonId") not in known:
        raise ValueError("rootPersonId drzewa prezentacyjnego nie wskazuje istniejącej osoby")

    privacy_policy = payload.get("privacyPolicy", {})
    protected_through = privacy_policy.get("protectThroughGeneration", -1)
    if not isinstance(protected_through, int) or protected_through < -1:
        raise ValueError(
            "protectThroughGeneration musi być liczbą całkowitą nie mniejszą niż -1"
        )
    protected_ids: set[str] = set()
    for person in people:
        generation = person.get("generation", 0)
        if not isinstance(generation, int):
            raise ValueError(f"Osoba {person.get('id')} ma nieprawidłowe pokolenie")
        disclosed = generation >= 2 and _authorized_deceased_disclosure(root, person)
        person.pop("privacyException", None)
        person.pop("disclosureBasis", None)
        if (generation <= protected_through and not disclosed) or person.get("privacy") == "masked_living":
            protected_ids.add(person["id"])
            person["name"] = person.pop("privateLabel", None) or "Osoba chroniona"
            person["summary"] = "dane chronione"
            person["privacy"] = "masked_living"
            person["evidenceStatus"] = "masked_living"
            person["facts"] = []
            person["occurrenceIds"] = []
            person["sourceIds"] = []
            person.pop("canonicalPersonId", None)
        else:
            person.pop("privateLabel", None)
            if disclosed:
                person["disclosureBasis"] = "user_authorized_deceased"
    for family in families:
        family.pop("familyKnowledgeEvidence", None)
        family.pop("familyKnowledgeEdges", None)
        family.pop("familyKnowledgeStatus", None)
        if "canonicalRelationship" in family and not isinstance(
            family["canonicalRelationship"], bool
        ):
            raise ValueError("canonicalRelationship rodziny musi być wartością logiczną")
        if family.get("canonicalRelationship") is False:
            if family.get("relationshipIdentityStatus") != "unresolved":
                raise ValueError("Rodzina źródłowa wymaga nierozstrzygniętej relacji")
            # Raw endpoint bindings and curator annotations are private input.
            # Only the SQL-backed projector may reintroduce public evidence.
            family.pop("relationshipEvidence", None)
            family.pop("evidence", None)
            family["sourceIds"] = []
        references = set(family.get("parentIds", [])) | set(family.get("childIds", []))
        if references & protected_ids:
            family["sourceIds"] = []
    for union in unions:
        if set(union.get("partnerIds", [])) & protected_ids:
            union["sourceIds"] = []
    return payload


def enrich_tree_occurrences(
    tree: dict[str, Any] | None,
    occurrences: list[dict[str, Any]],
    allowed_source_ids: set[str],
    identity_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    if tree is None:
        return None
    occurrence_by_id = {item["id"]: item for item in occurrences}
    occurrence_ids_by_person: dict[str, list[str]] = defaultdict(list)
    for occurrence in occurrences:
        if occurrence.get("personId"):
            occurrence_ids_by_person[occurrence["personId"]].append(occurrence["id"])
    for person in tree.get("people", []):
        curated_ids = [
            occurrence_id
            for occurrence_id in person.get("occurrenceIds", [])
            if occurrence_id in occurrence_by_id
        ]
        canonical_ids = {
            occurrence_by_id[occurrence_id].get("personId")
            for occurrence_id in curated_ids
            if occurrence_by_id[occurrence_id].get("personId")
        }
        expanded_ids = list(curated_ids)
        explicit_canonical_id = person.get("canonicalPersonId")
        if explicit_canonical_id:
            expanded_ids.extend(occurrence_ids_by_person.get(explicit_canonical_id, []))
        for canonical_id in canonical_ids:
            expanded_ids.extend(occurrence_ids_by_person[canonical_id])
        person["occurrenceIds"] = list(dict.fromkeys(expanded_ids))
        occurrence_source_ids = [
            occurrence_by_id[occurrence_id]["sourceId"]
            for occurrence_id in person["occurrenceIds"]
            if occurrence_by_id[occurrence_id].get("sourceId") in allowed_source_ids
        ]
        curated_source_ids = [
            source_id
            for source_id in person.get("sourceIds", [])
            if source_id in allowed_source_ids
        ]
        person["sourceIds"] = list(dict.fromkeys(curated_source_ids + occurrence_source_ids))
        member_occurrence_ids = set(person["occurrenceIds"])
        member_person_ids = set(canonical_ids)
        if explicit_canonical_id:
            member_person_ids.add(explicit_canonical_id)
        candidate_links: list[dict[str, Any]] = []
        for candidate in identity_candidates or []:
            from_member = (
                candidate.get("fromOccurrenceId") in member_occurrence_ids
                or candidate.get("fromPersonId") in member_person_ids
            )
            to_member = (
                candidate.get("toOccurrenceId") in member_occurrence_ids
                or candidate.get("toPersonId") in member_person_ids
            )
            if from_member == to_member:
                continue
            candidate_occurrence_id = (
                candidate.get("toOccurrenceId") if from_member else candidate.get("fromOccurrenceId")
            )
            candidate_occurrence = occurrence_by_id.get(candidate_occurrence_id)
            if not candidate_occurrence:
                continue
            source_id = candidate_occurrence.get("sourceId")
            if source_id not in allowed_source_ids:
                continue
            candidate_links.append(
                {
                    "id": candidate.get("id"),
                    "occurrenceId": candidate_occurrence_id,
                    "sourceId": source_id,
                    "label": candidate.get("toLabel") if from_member else candidate.get("fromLabel"),
                    "role": candidate_occurrence.get("role"),
                    "score": candidate.get("score"),
                    "status": candidate.get("status"),
                    "basis": candidate.get("basis"),
                    "scoreSemantics": candidate.get("scoreSemantics"),
                    "candidateBundleId": candidate.get("candidateBundleId"),
                    "support": candidate.get("support") or [],
                    "against": candidate.get("against") or [],
                    "humanReviewDebt": candidate.get("humanReviewDebt") or [],
                    "highPriority": candidate.get("highPriority") is True,
                }
            )
        if candidate_links:
            person["candidateLinks"] = candidate_links
        else:
            person.pop("candidateLinks", None)
    return tree
