from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..workspace import research_root, stable_id


def load_graph_scope_config(root: Path) -> dict[str, Any] | None:
    path = research_root(root) / "presentation" / "fan-scopes.json"
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
        raise ValueError("research/presentation/fan-scopes.json ma nieobsługiwany schemat")
    scopes = payload.get("scopes")
    if not isinstance(scopes, list):
        raise ValueError("fan-scopes.json wymaga tablicy scopes")
    scope_ids = [item.get("id") for item in scopes if isinstance(item, dict)]
    if len(scope_ids) != len(scopes) or len(scope_ids) != len(set(scope_ids)):
        raise ValueError("Zakresy FAN wymagają unikalnych identyfikatorów")
    default_scope_id = payload.get("defaultScopeId", "all")
    if default_scope_id != "all" and default_scope_id not in scope_ids:
        raise ValueError("defaultScopeId nie wskazuje istniejącego zakresu FAN")
    default_depth = payload.get("defaultDepth", 1)
    if default_depth not in {0, 1, 2}:
        raise ValueError("defaultDepth zakresów FAN musi mieć wartość 0, 1 albo 2")
    return payload


def _family_household_candidates(
    tree: dict[str, Any],
    scope_person_ids: list[str],
) -> list[dict[str, Any]]:
    """Project family units as residence hypotheses, never as address facts.

    A line contains not only nuclear families touching its direct path. Once a
    sibling or another child of that path is known, their descendant families
    remain part of the collateral branch. Expansion only follows children into
    their own families; it never walks through a spouse into the spouse's
    ancestors or unrelated line.
    """

    people = {person["id"]: person for person in tree.get("people", [])}
    scope_people = set(scope_person_ids)
    families = sorted(tree.get("families", []), key=lambda item: item["id"])
    unions = tree.get("unions", [])
    relevant_families: dict[str, dict[str, Any]] = {}
    descendant_people: set[str] = set()

    for family in families:
        member_ids = set(family.get("parentIds", [])) | set(family.get("childIds", []))
        anchors = scope_people.intersection(member_ids)
        if not anchors:
            continue
        relevant_families[family["id"]] = {
            "scopeRelation": "direct",
            "anchorPersonIds": sorted(anchors),
        }
        descendant_people.update(family.get("childIds", []))

    changed = True
    while changed:
        changed = False
        for family in families:
            if family["id"] in relevant_families:
                continue
            anchors = descendant_people.intersection(family.get("parentIds", []))
            if not anchors:
                continue
            relevant_families[family["id"]] = {
                "scopeRelation": "collateral_descendant",
                "anchorPersonIds": sorted(anchors),
            }
            before = len(descendant_people)
            descendant_people.update(family.get("childIds", []))
            changed = changed or len(descendant_people) != before

    candidates: list[dict[str, Any]] = []
    for family in families:
        scope_context = relevant_families.get(family["id"])
        if scope_context is None:
            continue
        parent_ids = list(dict.fromkeys(family.get("parentIds", [])))
        child_ids = list(dict.fromkeys(family.get("childIds", [])))
        member_ids = parent_ids + child_ids
        # A lone parent-child edge is a relationship, not enough even for a
        # provisional household reconstructed from a nuclear family.
        if len(parent_ids) < 2 or not child_ids:
            continue
        parent_set = set(parent_ids)
        matching_unions = [
            union for union in unions
            if set(union.get("partnerIds", [])) == parent_set
        ]
        parent_labels = [people[item].get("name", "Osoba chroniona") for item in parent_ids]
        source_ids = list(dict.fromkeys(
            list(family.get("sourceIds", []))
            + [source_id for union in matching_unions for source_id in union.get("sourceIds", [])]
        ))
        members = []
        for person_id in member_ids:
            person = people.get(person_id, {})
            role = "rodzic" if person_id in parent_set else "dziecko"
            occurrence_ids = list(dict.fromkeys(person.get("occurrenceIds", [])))
            members.append(
                {
                    "treePersonId": person_id,
                    "label": person.get("name") or "Osoba chroniona",
                    "role": role,
                    "occurrenceIds": occurrence_ids,
                    "privacy": person.get("privacy", "historical"),
                }
            )
        candidates.append(
            {
                "id": stable_id("family_household_candidate", family["id"]),
                "label": f"Rodzina: {' + '.join(parent_labels)}",
                "kind": "family_reconstruction",
                "status": "needs_residence_confirmation",
                "residenceEvidenceStatus": "unconfirmed",
                "basis": "curated_family_structure",
                "familyId": family["id"],
                "unionIds": [union["id"] for union in matching_unions],
                "familyStatus": family.get("status") or "unknown",
                "scopeRelation": scope_context["scopeRelation"],
                "anchorPersonIds": scope_context["anchorPersonIds"],
                "anchorLabels": [
                    people.get(person_id, {}).get("name") or "Osoba chroniona"
                    for person_id in scope_context["anchorPersonIds"]
                ],
                "parentIds": parent_ids,
                "childIds": child_ids,
                "personIds": member_ids,
                "members": members,
                "memberCount": len(member_ids),
                "sourceIds": source_ids,
                "placeId": None,
                "dateFrom": None,
                "dateTo": None,
                "evidenceNeeded": (
                    "Spis ludności, księga meldunkowa, adres w akcie albo inne "
                    "źródło potwierdzające wspólne zamieszkanie."
                ),
            }
        )
    return sorted(
        candidates,
        key=lambda item: (
            item["scopeRelation"] != "direct",
            item["label"],
            item["id"],
        ),
    )


def build_graph_scope_facade(
    config: dict[str, Any] | None,
    tree: dict[str, Any] | None,
    occurrences: list[dict[str, Any]],
    households: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if config is None:
        return None
    if tree is None:
        raise ValueError("Zakresy FAN wymagają family-tree.json")
    people = {person["id"]: person for person in tree.get("people", [])}
    branches = {branch["id"]: branch for branch in tree.get("branches", [])}
    occurrence_ids = {item["id"] for item in occurrences}
    household_ids = {item["id"] for item in households}
    output_scopes: list[dict[str, Any]] = []
    list_fields = (
        "extraOccurrenceIds",
        "householdIds",
        "connectorPersonIds",
        "excludedOccurrenceIds",
    )
    for scope in config["scopes"]:
        scope_id = scope["id"]
        branch_id = scope.get("branchId")
        branch = branches.get(branch_id)
        if branch is None:
            raise ValueError(f"Zakres FAN {scope_id} wskazuje nieznaną gałąź {branch_id}")
        for field in list_fields:
            if not isinstance(scope.get(field, []), list):
                raise ValueError(f"Zakres FAN {scope_id} wymaga tablicy {field}")
        default_depth = scope.get("defaultDepth", config.get("defaultDepth", 1))
        if default_depth not in {0, 1, 2}:
            raise ValueError(f"Zakres FAN {scope_id} ma nieprawidłowy defaultDepth")
        if not isinstance(scope.get("emptyScopeAllowed", False), bool):
            raise ValueError(f"Zakres FAN {scope_id} ma nieprawidłowe emptyScopeAllowed")
        tree_person_ids = list(dict.fromkeys(branch.get("personIds", [])))
        connectors = list(dict.fromkeys(scope.get("connectorPersonIds", [])))
        root_person_id = scope.get("rootPersonId")
        referenced_people = set(tree_person_ids) | set(connectors)
        if root_person_id:
            referenced_people.add(root_person_id)
        missing_people = sorted(referenced_people - people.keys())
        if missing_people:
            raise ValueError(
                f"Zakres FAN {scope_id} odwołuje się do nieznanych osób: "
                + ", ".join(missing_people)
            )
        extra_occurrences = list(dict.fromkeys(scope.get("extraOccurrenceIds", [])))
        excluded_occurrences = list(dict.fromkeys(scope.get("excludedOccurrenceIds", [])))
        missing_occurrences = sorted(
            (set(extra_occurrences) | set(excluded_occurrences)) - occurrence_ids
        )
        if missing_occurrences:
            raise ValueError(
                f"Zakres FAN {scope_id} wskazuje niedostępne wystąpienia: "
                + ", ".join(missing_occurrences)
            )
        configured_households = list(dict.fromkeys(scope.get("householdIds", [])))
        missing_households = sorted(set(configured_households) - household_ids)
        if missing_households:
            raise ValueError(
                f"Zakres FAN {scope_id} wskazuje niedostępne gospodarstwa: "
                + ", ".join(missing_households)
            )
        seed_occurrences: list[str] = []
        for person_id in tree_person_ids:
            seed_occurrences.extend(people[person_id].get("occurrenceIds", []))
        seed_occurrences.extend(extra_occurrences)
        excluded_set = set(excluded_occurrences)
        seed_occurrences = [
            occurrence_id
            for occurrence_id in dict.fromkeys(seed_occurrences)
            if occurrence_id in occurrence_ids and occurrence_id not in excluded_set
        ]
        seed_occurrence_set = set(seed_occurrences)
        seed_person_ids = {
            occurrence.get("personId")
            for occurrence in occurrences
            if occurrence.get("id") in seed_occurrence_set and occurrence.get("personId")
        }
        associated_households = [
            household["id"]
            for household in households
            if any(
                member.get("occurrenceId") in seed_occurrence_set
                or member.get("personId") in seed_person_ids
                for member in household.get("members", [])
            )
        ]
        source_households = list(dict.fromkeys(configured_households + associated_households))
        family_households = _family_household_candidates(tree, tree_person_ids)
        family_household_ids = [item["id"] for item in family_households]
        seed_households = list(dict.fromkeys(source_households + family_household_ids))
        if (
            not seed_occurrences
            and not seed_households
            and not scope.get("emptyScopeAllowed", False)
        ):
            raise ValueError(
                f"Zakres FAN {scope_id} nie ma rdzenia; ustaw emptyScopeAllowed jawnie, "
                "jeżeli ma pokazywać wyłącznie łączniki"
            )
        bridge_nodes = [
            {
                "id": f"bridge:{person_id}",
                "treePersonId": person_id,
                "label": people[person_id].get("name") or "Osoba chroniona",
                "privacy": people[person_id].get("privacy", "historical"),
                "summary": (
                    "Łącznik rodzinny — dane chronione"
                    if people[person_id].get("privacy") == "masked_living"
                    else "Łącznik warstwy prezentacyjnej"
                ),
            }
            for person_id in connectors
        ]
        connector_set = set(connectors)
        bridge_edges: list[dict[str, Any]] = []
        for family in tree.get("families", []):
            for parent_id in family.get("parentIds", []):
                for child_id in family.get("childIds", []):
                    if parent_id not in connector_set or child_id not in connector_set:
                        continue
                    bridge_edges.append(
                        {
                            "id": stable_id(
                                "graph_bridge_family", scope_id, family["id"], parent_id, child_id
                            ),
                            "from": f"bridge:{parent_id}",
                            "to": f"bridge:{child_id}",
                            "type": "tree-family",
                            "label": "struktura drzewa rodzinnego",
                            "basis": "kuratorowana rodzina prezentacyjna",
                        }
                    )
        for union in tree.get("unions", []):
            first_id, second_id = union.get("partnerIds", [None, None])
            if first_id not in connector_set or second_id not in connector_set:
                continue
            bridge_edges.append(
                {
                    "id": stable_id(
                        "graph_bridge_union", scope_id, union["id"], first_id, second_id
                    ),
                    "from": f"bridge:{first_id}",
                    "to": f"bridge:{second_id}",
                    "type": "tree-union",
                    "label": "związek w drzewie prezentacyjnym",
                    "basis": "kuratorowany związek prezentacyjny",
                }
            )
        for person_id in connectors:
            for occurrence_id in people[person_id].get("occurrenceIds", []):
                if occurrence_id not in seed_occurrences:
                    continue
                bridge_edges.append(
                    {
                        "id": stable_id(
                            "graph_bridge_crosswalk", scope_id, person_id, occurrence_id
                        ),
                        "from": f"bridge:{person_id}",
                        "to": f"occ:{occurrence_id}",
                        "type": "explicit-crosswalk",
                        "label": "jawne wystąpienie w akcie",
                        "basis": "occurrenceIds osoby prezentacyjnej",
                    }
                )
        warnings: list[str] = []
        if not seed_occurrences and not source_households:
            warnings.append(
                "Zakres nie ma jeszcze wystąpień ani gospodarstw źródłowych; "
                "gospodarstwa rekonstruowane pozostają kandydaturami do potwierdzenia."
            )
        if connectors and not any(edge["type"] == "explicit-crosswalk" for edge in bridge_edges):
            warnings.append(
                "Łączniki nie mają jeszcze jawnego crosswalku do wystąpień źródłowych."
            )
        output_scopes.append(
            {
                "id": scope_id,
                "branchId": branch_id,
                "label": scope.get("label") or branch.get("label") or scope_id,
                "description": scope.get("description") or branch.get("description"),
                "rootPersonId": root_person_id or tree.get("rootPersonId"),
                "seedOccurrenceIds": seed_occurrences,
                "seedHouseholdIds": seed_households,
                "configuredHouseholdIds": configured_households,
                "associatedHouseholdIds": associated_households,
                "sourceHouseholdIds": source_households,
                "familyHouseholdIds": family_household_ids,
                "familyHouseholds": family_households,
                "excludedOccurrenceIds": excluded_occurrences,
                "treePersonIds": tree_person_ids,
                "bridgeNodes": bridge_nodes,
                "bridgeEdges": bridge_edges,
                "defaultDepth": default_depth,
                "warnings": warnings,
            }
        )
    return {
        "schemaVersion": config["schemaVersion"],
        "defaultScopeId": config.get("defaultScopeId", "all"),
        "defaultDepth": config.get("defaultDepth", 1),
        "scopes": output_scopes,
    }
