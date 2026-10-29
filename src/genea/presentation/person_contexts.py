from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..workspace import stable_id


def _ids(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return sorted({value for value in values if isinstance(value, str) and value})


def project_person_contexts(
    *,
    people: list[dict[str, Any]],
    occurrences: list[dict[str, Any]],
    tree: dict[str, Any] | None,
    identity_groups: list[dict[str, Any]],
    presentation_map: dict[str, Any] | None,
    source_reported_networks: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build a privacy-filtered navigation crosswalk without inferring identity.

    Every input is already part of the final presentation projection.  The
    projector deliberately uses only explicit ``canonicalPersonId`` and
    ``occurrence.personId`` links; names and unresolved candidates never take
    part in classification.
    """

    people_by_id = {item["id"]: item for item in people}
    occurrence_by_id = {item["id"]: item for item in occurrences}
    identity_by_person = {
        item["personId"]: item["id"]
        for item in identity_groups
        if item.get("personId") in people_by_id and isinstance(item.get("id"), str)
    }
    tree_people = list((tree or {}).get("people", []))
    map_observations = list((presentation_map or {}).get("observations", []))
    map_ids_by_tree: dict[str, set[str]] = defaultdict(set)
    for observation in map_observations:
        tree_id = observation.get("personId")
        if isinstance(tree_id, str):
            map_ids_by_tree[tree_id].add(tree_id)

    tree_rows: list[dict[str, Any]] = []
    canonical_tree_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    source_reported_person_ids: set[str] = set()
    source_reported_memberships: dict[str, list[dict[str, Any]]] = defaultdict(list)
    source_backing_by_tree_node = {
        stable_id("source_reported_tree_node", occurrence["id"]): {
            "personId": occurrence["personId"],
            "occurrenceId": occurrence["id"],
        }
        for occurrence in occurrences
        if isinstance(occurrence.get("id"), str)
        and occurrence.get("personId") in people_by_id
        and occurrence.get("externalTreeCandidate") is True
    }
    for tree_person in tree_people:
        tree_id = tree_person.get("id")
        if not isinstance(tree_id, str) or not tree_id:
            continue
        occurrence_ids = [
            item_id
            for item_id in _ids(tree_person.get("occurrenceIds"))
            if item_id in occurrence_by_id
        ]
        canonical_ids = {
            occurrence_by_id[item_id].get("personId")
            for item_id in occurrence_ids
            if occurrence_by_id[item_id].get("personId") in people_by_id
        }
        explicit = tree_person.get("canonicalPersonId")
        if explicit in people_by_id:
            canonical_ids.add(explicit)
        row = {
            "tree": tree_person,
            "treeId": tree_id,
            "occurrenceIds": occurrence_ids,
            "canonicalPersonIds": sorted(canonical_ids),
        }
        tree_rows.append(row)
        if len(canonical_ids) == 1:
            canonical_tree_rows[next(iter(canonical_ids))].append(row)

    # Redacted external-tree candidates are separate layout networks rather
    # than canonical family-tree people.  Their explicit person/occurrence
    # links may still join the same navigation context without creating an
    # identity group or inferring a match from the label.
    for network in source_reported_networks or []:
        network_id = network.get("id")
        if not isinstance(network_id, str) or not network_id:
            continue
        for node in network.get("nodes", []):
            tree_id = node.get("treeNodeId")
            backing = source_backing_by_tree_node.get(tree_id)
            if (
                not isinstance(tree_id, str)
                or not tree_id
                or backing is None
                or node.get("publicProfileId") != tree_id
                or node.get("personId") != tree_id
                or node.get("occurrenceId") != tree_id
            ):
                continue
            person_id = backing["personId"]
            occurrence_id = backing["occurrenceId"]
            source_reported_person_ids.add(person_id)
            classification = node.get("classification") or {}
            source_reported_memberships[person_id].append(
                {
                    "networkId": network_id,
                    "treeNodeId": tree_id,
                    "occurrenceId": tree_id,
                    "classificationState": classification.get("state")
                    or "unclassified",
                    "role": classification.get("primaryRole"),
                    "unclassifiedReason": classification.get(
                        "unclassifiedReason"
                    ),
                }
            )
            canonical_tree_rows[person_id].append(
                {
                    "tree": {"sourceIds": []},
                    "treeId": tree_id,
                    "occurrenceIds": [occurrence_id],
                    "canonicalPersonIds": [person_id],
                }
            )

    contexts: list[dict[str, Any]] = []

    def backing(
        canonical_ids: list[str], rows: list[dict[str, Any]]
    ) -> dict[str, list[str]]:
        tree_ids = sorted({row["treeId"] for row in rows})
        occurrence_ids = {
            item["id"]
            for item in occurrences
            if item.get("personId") in canonical_ids
        }
        source_ids: set[str] = set()
        for row in rows:
            occurrence_ids.update(row["occurrenceIds"])
            source_ids.update(_ids(row["tree"].get("sourceIds")))
        for occurrence_id in occurrence_ids:
            source_id = occurrence_by_id.get(occurrence_id, {}).get("sourceId")
            if isinstance(source_id, str) and source_id:
                source_ids.add(source_id)
        return {
            "canonicalPersonIds": sorted(canonical_ids),
            "treePersonIds": tree_ids,
            "mapPersonIds": sorted(
                {item for tree_id in tree_ids for item in map_ids_by_tree[tree_id]}
            ),
            "graphIdentityIds": sorted(
                identity_by_person[person_id]
                for person_id in canonical_ids
                if person_id in identity_by_person
                and person_id not in source_reported_person_ids
            ),
            "occurrenceIds": sorted(occurrence_ids),
            "sourceIds": sorted(source_ids),
        }

    # A canonical person always owns exactly one context, including people that
    # are not represented in the family-tree presentation.
    for person_id, person in sorted(people_by_id.items()):
        rows = canonical_tree_rows.get(person_id, [])
        tree_ids = sorted(row["treeId"] for row in rows)
        public_profile_id = tree_ids[0] if tree_ids else person_id
        contexts.append(
            {
                "personKey": person_id,
                "identityScope": "canonical",
                "publicProfileId": public_profile_id,
                "aliases": sorted(({person_id, *tree_ids}) - {public_profile_id}),
                "label": person.get("preferredName") or person.get("name") or public_profile_id,
                "status": person.get("status"),
                "warnings": [],
                **(
                    {
                        "sourceReportedMemberships": sorted(
                            source_reported_memberships[person_id],
                            key=lambda item: (
                                item["networkId"],
                                item["treeNodeId"],
                                item["occurrenceId"],
                            ),
                        )
                    }
                    if source_reported_memberships.get(person_id)
                    else {}
                ),
                **backing([person_id], rows),
            }
        )

    for row in tree_rows:
        canonical_ids = row["canonicalPersonIds"]
        if len(canonical_ids) == 1:
            continue
        tree_person = row["tree"]
        tree_id = row["treeId"]
        protected = tree_person.get("privacy") == "masked_living"
        public_profile_id = (
            stable_id("profile", "protected", tree_id) if protected else tree_id
        )
        identity_scope = "hypothesis_group" if len(canonical_ids) > 1 else "presentation_only"
        contexts.append(
            {
                "personKey": public_profile_id,
                "identityScope": identity_scope,
                "publicProfileId": public_profile_id,
                "aliases": [] if protected else sorted({tree_id} - {public_profile_id}),
                "label": tree_person.get("name") or "Osoba chroniona",
                "status": tree_person.get("status") or tree_person.get("evidence"),
                "warnings": (
                    ["Węzeł prezentacyjny obejmuje kilka osób kanonicznych i pozostaje nierozstrzygnięty."]
                    if identity_scope == "hypothesis_group"
                    else []
                ),
                **backing(canonical_ids, [row]),
            }
        )

    contexts.sort(key=lambda item: (item["identityScope"], item["publicProfileId"]))

    claimed_routes: dict[str, str] = {}
    person_keys: set[str] = set()
    for context in contexts:
        if context["personKey"] in person_keys:
            raise ValueError(f"Powielony personKey: {context['personKey']}")
        person_keys.add(context["personKey"])
        for route_id in [context["publicProfileId"], *context["aliases"]]:
            previous = claimed_routes.setdefault(route_id, context["personKey"])
            if previous != context["personKey"]:
                raise ValueError(
                    f"Alias profilu {route_id!r} wskazuje więcej niż jeden kontekst osoby"
                )
    return contexts
