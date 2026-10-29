from __future__ import annotations

from collections import defaultdict
from heapq import heappop, heappush
from typing import Any

from ..command_envelope import fingerprint
from ..workspace import stable_id
from .family_knowledge import sanitize_family_knowledge


_NETWORK_KIND = "external_tree_candidate"
_PARENT_RELATIONS = {"parent_of", "father_of", "mother_of"}
_FAN_RELATIONS = {
    "associated_with",
    "godparent_of",
    "informant_of",
    "neighbor_of",
    "witness_of",
}


class _DisjointSet:
    def __init__(self, values: set[str]) -> None:
        self._parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self._parent[value]
        if parent != value:
            self._parent[value] = self.find(parent)
        return self._parent[value]

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        lower, higher = sorted((left_root, right_root))
        self._parent[higher] = lower


def _strongly_connected_components(
    vertices: set[str], edges: set[tuple[str, str]]
) -> list[list[str]]:
    """Return deterministic SCCs for inconsistent imported ancestry loops."""

    adjacency: dict[str, list[str]] = defaultdict(list)
    for source, target in sorted(edges):
        adjacency[source].append(target)

    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[list[str]] = []

    def visit(vertex: str) -> None:
        nonlocal index
        indices[vertex] = index
        lowlinks[vertex] = index
        index += 1
        stack.append(vertex)
        on_stack.add(vertex)

        for target in adjacency.get(vertex, []):
            if target not in indices:
                visit(target)
                lowlinks[vertex] = min(lowlinks[vertex], lowlinks[target])
            elif target in on_stack:
                lowlinks[vertex] = min(lowlinks[vertex], indices[target])

        if lowlinks[vertex] != indices[vertex]:
            return
        component: list[str] = []
        while stack:
            member = stack.pop()
            on_stack.remove(member)
            component.append(member)
            if member == vertex:
                break
        components.append(sorted(component))

    for vertex in sorted(vertices):
        if vertex not in indices:
            visit(vertex)
    return sorted(components, key=lambda component: component[0])


def _layout(
    occurrence_ids: set[str], relationships: list[dict[str, Any]]
) -> tuple[dict[str, int], dict[str, int]]:
    """Lay out child-to-parent edges while keeping spouses at one level."""

    spouses = _DisjointSet(occurrence_ids)
    for relationship in relationships:
        if relationship["type"] == "spouse_of":
            spouses.union(
                relationship["fromOccurrenceId"], relationship["toOccurrenceId"]
            )

    spouse_groups: dict[str, set[str]] = defaultdict(set)
    for occurrence_id in sorted(occurrence_ids):
        spouse_groups[spouses.find(occurrence_id)].add(occurrence_id)
    spouse_group_by_occurrence = {
        occurrence_id: group_id
        for group_id, members in spouse_groups.items()
        for occurrence_id in members
    }

    ancestry_edges = {
        (
            spouse_group_by_occurrence[relationship["fromOccurrenceId"]],
            spouse_group_by_occurrence[relationship["toOccurrenceId"]],
        )
        for relationship in relationships
        if relationship["type"] == "child_of"
        and spouse_group_by_occurrence[relationship["fromOccurrenceId"]]
        != spouse_group_by_occurrence[relationship["toOccurrenceId"]]
    }
    group_ids = set(spouse_groups)
    components = _strongly_connected_components(group_ids, ancestry_edges)
    component_by_group = {
        group_id: component[0]
        for component in components
        for group_id in component
    }
    condensed_edges = {
        (component_by_group[source], component_by_group[target])
        for source, target in ancestry_edges
        if component_by_group[source] != component_by_group[target]
    }
    component_ids = {component[0] for component in components}
    outgoing: dict[str, set[str]] = defaultdict(set)
    indegree = {component_id: 0 for component_id in component_ids}
    for source, target in sorted(condensed_edges):
        outgoing[source].add(target)
        indegree[target] += 1

    queue: list[str] = []
    for component_id, degree in sorted(indegree.items()):
        if degree == 0:
            heappush(queue, component_id)
    generation_by_component = {component_id: 0 for component_id in component_ids}
    while queue:
        source = heappop(queue)
        for target in sorted(outgoing[source]):
            generation_by_component[target] = max(
                generation_by_component[target], generation_by_component[source] + 1
            )
            indegree[target] -= 1
            if indegree[target] == 0:
                heappush(queue, target)

    generation = {
        occurrence_id: generation_by_component[
            component_by_group[spouse_group_by_occurrence[occurrence_id]]
        ]
        for occurrence_id in occurrence_ids
    }
    order: dict[str, int] = {}
    by_generation: dict[int, list[str]] = defaultdict(list)
    for occurrence_id, value in generation.items():
        by_generation[value].append(occurrence_id)
    for generation_value in sorted(by_generation):
        for index, occurrence_id in enumerate(sorted(by_generation[generation_value])):
            order[occurrence_id] = index
    return generation, order


def _source_components(
    relationships: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build case-scoped private components used by persisted anchors."""

    valid = [
        relationship
        for relationship in relationships
        if isinstance(relationship.get("assertionId"), str)
        and isinstance(relationship.get("subjectOccurrenceId"), str)
        and isinstance(relationship.get("objectOccurrenceId"), str)
        and isinstance(relationship.get("predicate"), str)
        and relationship["subjectOccurrenceId"]
        != relationship["objectOccurrenceId"]
    ]
    if not valid:
        return []
    result: list[dict[str, Any]] = []
    by_case: dict[str | None, list[dict[str, Any]]] = defaultdict(list)
    for relationship in valid:
        case_id = relationship.get("caseId")
        by_case[case_id if isinstance(case_id, str) and case_id else None].append(
            relationship
        )
    for case_id, scoped in sorted(
        by_case.items(), key=lambda item: item[0] or ""
    ):
        occurrence_ids = {
            occurrence_id
            for relationship in scoped
            for occurrence_id in (
                relationship["subjectOccurrenceId"],
                relationship["objectOccurrenceId"],
            )
        }
        components = _DisjointSet(occurrence_ids)
        for relationship in scoped:
            components.union(
                relationship["subjectOccurrenceId"],
                relationship["objectOccurrenceId"],
            )
        members_by_component: dict[str, set[str]] = defaultdict(set)
        for occurrence_id in sorted(occurrence_ids):
            members_by_component[components.find(occurrence_id)].add(occurrence_id)

        for members in sorted(
            members_by_component.values(), key=lambda values: sorted(values)
        ):
            basis = sorted(
                (
                    {
                        "assertionId": relationship["assertionId"],
                        "subjectOccurrenceId": relationship["subjectOccurrenceId"],
                        "predicate": relationship["predicate"],
                        "objectOccurrenceId": relationship["objectOccurrenceId"],
                    }
                    for relationship in scoped
                    if relationship["subjectOccurrenceId"] in members
                    and relationship["objectOccurrenceId"] in members
                ),
                key=lambda value: (
                    value["assertionId"],
                    value["subjectOccurrenceId"],
                    value["predicate"],
                    value["objectOccurrenceId"],
                ),
            )
            result.append(
                {
                    "caseId": case_id,
                    "componentFingerprint": fingerprint(
                        {"sourceRelationships": basis}
                    ),
                    "occurrenceIds": sorted(members),
                    "relationships": basis,
                }
            )
    return sorted(
        result,
        key=lambda value: (
            value["caseId"] or "",
            value["componentFingerprint"],
        ),
    )


def _path_step(
    relationship_id: str, direction: str, source: str, target: str
) -> dict[str, str]:
    return {
        "relationshipId": relationship_id,
        "direction": direction,
        "source": source,
        "target": target,
    }


def _all_simple_paths(
    start: str,
    adjacency: dict[str, list[dict[str, str]]],
) -> dict[str, list[list[dict[str, str]]]]:
    paths: dict[str, list[list[dict[str, str]]]] = defaultdict(list)
    stack: list[tuple[str, list[dict[str, str]], frozenset[str]]] = [
        (start, [], frozenset({start}))
    ]
    while stack:
        vertex, path, visited = stack.pop()
        for step in reversed(adjacency.get(vertex, [])):
            target = step["target"]
            if target in visited:
                continue
            next_path = [*path, step]
            paths[target].append(next_path)
            stack.append((target, next_path, visited | {target}))
    for target in paths:
        paths[target].sort(
            key=lambda path: tuple(
                (step["relationshipId"], step["direction"]) for step in path
            )
        )
    return paths


def _alternative(role: str, path: list[dict[str, str]]) -> dict[str, Any]:
    ordered_path = [
        {
            "relationshipId": step["relationshipId"],
            "direction": step["direction"],
        }
        for step in path
    ]
    return {
        "role": role,
        "basis": "derived_from_explicit_source_path",
        "orderedPath": ordered_path,
        "backingRelationshipIds": [
            step["relationshipId"] for step in ordered_path
        ],
    }


def _network_label(network: dict[str, Any], ordinal: int) -> str:
    prefix = f"Sieć kandydacka {ordinal}"
    anchor = network["anchor"]
    if network["anchorState"] == "visible" and anchor is not None:
        return f"{prefix} · {anchor['label']}"
    if network["anchorState"] == "masked":
        return f"{prefix} · punkt odniesienia ukryty"
    return f"{prefix} · punkt odniesienia nieustalony"


def _classifications(
    *,
    occurrence_ids: set[str],
    relationships: list[dict[str, Any]],
    anchor_occurrence_id: str,
) -> dict[str, dict[str, Any]]:
    """Classify explicit typed paths without names or layout generations."""

    upward: dict[str, list[dict[str, str]]] = defaultdict(list)
    downward: dict[str, list[dict[str, str]]] = defaultdict(list)
    spouses: dict[str, list[dict[str, str]]] = defaultdict(list)
    fan: dict[str, list[dict[str, str]]] = defaultdict(list)
    for relationship in sorted(relationships, key=lambda item: item["id"]):
        source = relationship["fromOccurrenceId"]
        target = relationship["toOccurrenceId"]
        if source not in occurrence_ids or target not in occurrence_ids:
            continue
        relationship_id = relationship["id"]
        predicate = relationship["type"]
        if predicate == "child_of":
            upward[source].append(
                _path_step(relationship_id, "child_to_parent", source, target)
            )
            downward[target].append(
                _path_step(relationship_id, "parent_to_child", target, source)
            )
        elif predicate in _PARENT_RELATIONS:
            upward[target].append(
                _path_step(relationship_id, "child_to_parent", target, source)
            )
            downward[source].append(
                _path_step(relationship_id, "parent_to_child", source, target)
            )
        elif predicate == "spouse_of":
            spouses[source].append(
                _path_step(relationship_id, "spouse", source, target)
            )
            spouses[target].append(
                _path_step(relationship_id, "spouse", target, source)
            )
        elif predicate in _FAN_RELATIONS:
            fan[source].append(
                _path_step(relationship_id, "forward", source, target)
            )
            fan[target].append(
                _path_step(relationship_id, "reverse", target, source)
            )
    for adjacency in (upward, downward, spouses, fan):
        for source in adjacency:
            adjacency[source].sort(
                key=lambda step: (
                    step["relationshipId"], step["direction"], step["target"]
                )
            )

    upward_paths = _all_simple_paths(anchor_occurrence_id, upward)
    downward_paths = _all_simple_paths(anchor_occurrence_id, downward)
    fan_paths = _all_simple_paths(anchor_occurrence_id, fan)
    alternatives: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for target, paths in upward_paths.items():
        alternatives[target].extend(
            _alternative("direct_ancestor", path) for path in paths
        )
    for target, paths in downward_paths.items():
        alternatives[target].extend(
            _alternative("descendant", path) for path in paths
        )
    for target, paths in fan_paths.items():
        alternatives[target].extend(_alternative("fan", path) for path in paths)

    for ancestor, paths in upward_paths.items():
        for spouse_step in spouses.get(ancestor, []):
            target = spouse_step["target"]
            alternatives[target].extend(
                _alternative("ancestor_spouse", [*path, spouse_step])
                for path in paths
            )

    for ancestor, paths_to_ancestor in upward_paths.items():
        descendant_paths = _all_simple_paths(ancestor, downward)
        for target, paths_from_ancestor in descendant_paths.items():
            if target == anchor_occurrence_id:
                continue
            for upward_path in paths_to_ancestor:
                upward_nodes = {
                    anchor_occurrence_id,
                    *(step["target"] for step in upward_path),
                }
                for downward_path in paths_from_ancestor:
                    downward_nodes = [step["target"] for step in downward_path]
                    if upward_nodes.intersection(downward_nodes):
                        continue
                    alternatives[target].append(
                        _alternative(
                            "collateral", [*upward_path, *downward_path]
                        )
                    )

    result: dict[str, dict[str, Any]] = {}
    for occurrence_id in sorted(occurrence_ids):
        if occurrence_id == anchor_occurrence_id:
            result[occurrence_id] = {
                "state": "classified",
                "primaryRole": "anchor",
                "alternatives": [],
                "unclassifiedReason": None,
            }
            continue
        candidates = alternatives.get(occurrence_id, [])
        unique: dict[tuple[Any, ...], dict[str, Any]] = {}
        for item in candidates:
            key = (
                item["role"],
                tuple(
                    (step["relationshipId"], step["direction"])
                    for step in item["orderedPath"]
                ),
            )
            unique[key] = item
        candidates = [unique[key] for key in sorted(unique)]
        roles = {item["role"] for item in candidates}
        if "direct_ancestor" in roles and "ancestor_spouse" in roles:
            candidates = [
                item for item in candidates if item["role"] != "ancestor_spouse"
            ]
            roles.discard("ancestor_spouse")
        if not candidates:
            result[occurrence_id] = {
                "state": "unclassified",
                "primaryRole": None,
                "alternatives": [],
                "unclassifiedReason": "unsupported_relation",
            }
        elif len(roles) == 1:
            result[occurrence_id] = {
                "state": "classified",
                "primaryRole": next(iter(roles)),
                "alternatives": candidates,
                "unclassifiedReason": None,
            }
        else:
            result[occurrence_id] = {
                "state": "conflict",
                "primaryRole": None,
                "alternatives": candidates,
                "unclassifiedReason": None,
            }
    return result


def project_source_reported_networks(
    *,
    people: list[dict[str, Any]],
    occurrences: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    source_relationships: list[dict[str, Any]] | None = None,
    network_anchors: list[dict[str, Any]] | None = None,
    materialized_occurrence_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Project privacy-safe external-tree components for layout only.

    The inputs are already privacy-filtered.  This function narrows them again
    to explicit sanitized candidate shells or explicitly identity-projected
    external profiles, plus redacted relationships.  It never promotes the
    imported network to a canonical family or identity graph.
    """

    external_people = {
        person["id"]: person
        for person in people
        if isinstance(person.get("id"), str)
    }
    external_occurrences = {
        occurrence["id"]: occurrence
        for occurrence in occurrences
        if isinstance(occurrence.get("id"), str)
        and occurrence.get("personId") in external_people
        and occurrence.get("externalTreeCandidate") is True
        and occurrence.get("sourceRedacted") is True
        and occurrence.get("status") == "imported_unverified"
        and (
            (
                (
                    (
                        external_people[occurrence["personId"]].get("status")
                        == "candidate"
                        and occurrence.get("personStatus") == "candidate"
                    )
                    or occurrence.get("sourceProfileIdentityGuarded") is True
                )
                and occurrence.get("identityStatus") == "unresolved"
                and occurrence.get("personIdentityResolved") is not True
            )
            or (
                occurrence.get("externalTreeIdentityProjection")
                == "resolved_same"
                and occurrence.get("identityStatus") == "resolved_same"
                and occurrence.get("personIdentityResolved") is True
            )
        )
    }
    filtered_relationships = [
        relationship
        for relationship in relationships
        if isinstance(relationship.get("id"), str)
        and relationship.get("restrictedBacking") is True
        and relationship.get("relationshipScope")
        == "source_reported_external_tree"
        and relationship.get("fromOccurrenceId") in external_occurrences
        and relationship.get("toOccurrenceId") in external_occurrences
        and relationship.get("fromOccurrenceId")
        != relationship.get("toOccurrenceId")
        and isinstance(relationship.get("type"), str)
        and relationship.get("type")
    ]
    if source_relationships is None:
        source_relationships = [
            {
                "assertionId": relationship["id"],
                "subjectOccurrenceId": relationship["fromOccurrenceId"],
                "predicate": relationship["type"],
                "objectOccurrenceId": relationship["toOccurrenceId"],
            }
            for relationship in filtered_relationships
        ]
    source_components = _source_components(source_relationships)
    anchors_by_scope: dict[tuple[str | None, str], list[dict[str, Any]]] = (
        defaultdict(list)
    )
    for anchor in network_anchors or []:
        if (
            anchor.get("status") == "active"
            and isinstance(anchor.get("componentFingerprint"), str)
            and isinstance(anchor.get("anchorOccurrenceId"), str)
        ):
            case_id = anchor.get("caseId")
            anchors_by_scope[
                (
                    case_id if isinstance(case_id, str) and case_id else None,
                    anchor["componentFingerprint"],
                )
            ].append(anchor)

    visible_relationship_by_key = {
        (
            relationship["fromOccurrenceId"],
            relationship["type"],
            relationship["toOccurrenceId"],
        ): relationship
        for relationship in filtered_relationships
    }

    networks: list[dict[str, Any]] = []
    for source_component in source_components:
        full_occurrence_ids = set(source_component["occurrenceIds"])
        known_materialized_ids = (
            full_occurrence_ids
            if materialized_occurrence_ids is None
            else full_occurrence_ids.intersection(materialized_occurrence_ids)
        )
        missing_materialization_count = len(
            full_occurrence_ids - known_materialized_ids
        )
        materialization_state = (
            "incomplete" if missing_materialization_count else "complete"
        )
        visible_occurrence_ids = full_occurrence_ids.intersection(
            external_occurrences
        )
        if not visible_occurrence_ids:
            continue
        full_relationships = [
            {
                "id": stable_id(
                    "redacted_external_tree_relation",
                    relationship["subjectOccurrenceId"],
                    relationship["predicate"],
                    relationship["objectOccurrenceId"],
                ),
                "fromOccurrenceId": relationship["subjectOccurrenceId"],
                "toOccurrenceId": relationship["objectOccurrenceId"],
                "type": relationship["predicate"],
            }
            for relationship in source_component["relationships"]
        ]
        visible_component_relationships = [
            visible_relationship_by_key[key]
            for key in sorted(visible_relationship_by_key)
            if key[0] in visible_occurrence_ids and key[2] in visible_occurrence_ids
        ]
        fragments = _DisjointSet(visible_occurrence_ids)
        for relationship in visible_component_relationships:
            fragments.union(
                relationship["fromOccurrenceId"],
                relationship["toOccurrenceId"],
            )
        members_by_fragment: dict[str, set[str]] = defaultdict(set)
        for occurrence_id in sorted(visible_occurrence_ids):
            members_by_fragment[fragments.find(occurrence_id)].add(occurrence_id)

        anchor_scope = (
            source_component["caseId"],
            source_component["componentFingerprint"],
        )
        active_anchors = anchors_by_scope.get(
            anchor_scope, []
        )
        anchor_occurrence_id = (
            active_anchors[0]["anchorOccurrenceId"]
            if len(active_anchors) == 1
            else None
        )
        full_classifications = (
            _classifications(
                occurrence_ids=full_occurrence_ids,
                relationships=full_relationships,
                anchor_occurrence_id=anchor_occurrence_id,
            )
            if anchor_occurrence_id in full_occurrence_ids
            else {}
        )

        for occurrence_ids in sorted(
            members_by_fragment.values(), key=lambda members: sorted(members)
        ):
            component_relationships = sorted(
                (
                    relationship
                    for relationship in visible_component_relationships
                    if relationship["fromOccurrenceId"] in occurrence_ids
                    and relationship["toOccurrenceId"] in occurrence_ids
                ),
                key=lambda relationship: relationship["id"],
            )
            relationship_ids = sorted(
                {relationship["id"] for relationship in component_relationships}
            )
            sorted_occurrence_ids = sorted(occurrence_ids)
            network_id = (
                stable_id(
                    "source_reported_network",
                    "occurrences",
                    *sorted_occurrence_ids,
                    "relationships",
                    *relationship_ids,
                )
                if source_component["caseId"] is None
                else stable_id(
                    "source_reported_network",
                    source_component["caseId"],
                    source_component["componentFingerprint"],
                    "fragment",
                    *sorted_occurrence_ids,
                )
            )
            generation, order = _layout(occurrence_ids, component_relationships)
            tree_node_by_occurrence = {
                occurrence_id: stable_id(
                    "source_reported_tree_node", occurrence_id
                )
                for occurrence_id in sorted_occurrence_ids
            }
            public_profile_ids = sorted(tree_node_by_occurrence.values())

            if anchor_occurrence_id is None:
                anchor_state = "missing"
                anchor = None
                classifications = {
                    occurrence_id: {
                        "state": "unclassified",
                        "primaryRole": None,
                        "alternatives": [],
                        "unclassifiedReason": "missing_anchor",
                    }
                    for occurrence_id in occurrence_ids
                }
            elif anchor_occurrence_id not in known_materialized_ids:
                anchor_state = "missing"
                anchor = None
                classifications = {
                    occurrence_id: {
                        "state": "unclassified",
                        "primaryRole": None,
                        "alternatives": [],
                        "unclassifiedReason": "incomplete_materialization",
                    }
                    for occurrence_id in occurrence_ids
                }
            elif anchor_occurrence_id not in external_occurrences:
                anchor_state = "masked"
                anchor = None
                classifications = {
                    occurrence_id: {
                        "state": "unclassified",
                        "primaryRole": None,
                        "alternatives": [],
                        "unclassifiedReason": "masked_anchor",
                    }
                    for occurrence_id in occurrence_ids
                }
            elif anchor_occurrence_id not in occurrence_ids:
                # The anchor itself is publishable, but this fragment reaches it
                # only through a privacy-filtered path.  Do not leak an anchor
                # label or route that is absent from this fragment.
                anchor_state = "masked"
                anchor = None
                classifications = {
                    occurrence_id: {
                        "state": "unclassified",
                        "primaryRole": None,
                        "alternatives": [],
                        "unclassifiedReason": "masked_path",
                    }
                    for occurrence_id in occurrence_ids
                }
            else:
                anchor_state = "visible"
                anchor_tree_node_id = stable_id(
                    "source_reported_tree_node", anchor_occurrence_id
                )
                anchor_person_id = external_occurrences[anchor_occurrence_id][
                    "personId"
                ]
                anchor = {
                    "treeNodeId": anchor_tree_node_id,
                    "publicProfileId": anchor_tree_node_id,
                    "label": external_people[anchor_person_id].get("name")
                    or external_occurrences[anchor_occurrence_id].get("name")
                    or "Kandydat bez nazwy",
                }
                classifications = _classifications(
                    occurrence_ids=occurrence_ids,
                    relationships=component_relationships,
                    anchor_occurrence_id=anchor_occurrence_id,
                )
                for occurrence_id in occurrence_ids:
                    if (
                        classifications[occurrence_id]["state"]
                        == "unclassified"
                        and full_classifications.get(occurrence_id, {}).get(
                            "state"
                        )
                        in {"classified", "conflict"}
                    ):
                        classifications[occurrence_id] = {
                            "state": "unclassified",
                            "primaryRole": None,
                            "alternatives": [],
                            "unclassifiedReason": (
                                "incomplete_materialization"
                                if missing_materialization_count
                                else "masked_path"
                            ),
                        }

            nodes: list[dict[str, Any]] = []
            for occurrence_id in sorted_occurrence_ids:
                occurrence = external_occurrences[occurrence_id]
                profile_identity_status = occurrence.get("identityStatus")
                if profile_identity_status not in {"unresolved", "resolved_same"}:
                    profile_identity_status = "unresolved"
                node = {
                    "treeNodeId": tree_node_by_occurrence[occurrence_id],
                    "publicProfileId": tree_node_by_occurrence[occurrence_id],
                    "personId": tree_node_by_occurrence[occurrence_id],
                    "occurrenceId": tree_node_by_occurrence[occurrence_id],
                    "label": external_people[occurrence["personId"]].get("name")
                    or occurrence.get("name")
                    or "Kandydat bez nazwy",
                    "status": "candidate",
                    "identityStatus": profile_identity_status,
                    "profileIdentityStatus": profile_identity_status,
                    "relationshipIdentityStatus": "unresolved",
                    "personIdentityResolved": (
                        occurrence.get("personIdentityResolved") is True
                        and profile_identity_status == "resolved_same"
                    ),
                    "generation": generation[occurrence_id],
                    "order": order[occurrence_id],
                    "classification": classifications[occurrence_id],
                }
                identity_decision_tier = occurrence.get("identityDecisionTier")
                family_knowledge = sanitize_family_knowledge(occurrence.get("familyKnowledge"))
                if family_knowledge is not None:
                    node["familyKnowledge"] = family_knowledge
                if (
                    profile_identity_status == "resolved_same"
                    and identity_decision_tier
                    in {"agent_approved_human_pending", "human_confirmed"}
                ):
                    node["identityDecisionTier"] = identity_decision_tier
                nodes.append(node)
            edges = [
                {
                    "treeEdgeId": stable_id(
                        "source_reported_tree_edge", relationship["id"]
                    ),
                    "relationshipId": relationship["id"],
                    "fromTreeNodeId": tree_node_by_occurrence[
                        relationship["fromOccurrenceId"]
                    ],
                    "toTreeNodeId": tree_node_by_occurrence[
                        relationship["toOccurrenceId"]
                    ],
                    "type": relationship["type"],
                    "identityStatus": "unresolved",
                    "relationshipIdentityStatus": "unresolved",
                    "canonicalRelationship": False,
                }
                for relationship in component_relationships
            ]
            networks.append(
                {
                    "id": network_id,
                    "kind": _NETWORK_KIND,
                    "status": "imported_unverified",
                    "identityStatus": "unresolved",
                    "relationshipIdentityStatus": "unresolved",
                    "canonicalRelationship": False,
                    "automaticMerge": False,
                    "privacyFiltered": True,
                    "materializationState": materialization_state,
                    "materializationGap": (
                        {
                            "id": stable_id(
                                "source_reported_network_materialization_gap",
                                source_component["caseId"] or "legacy_unscoped",
                                source_component["componentFingerprint"],
                            ),
                            "kind": "incomplete_materialization",
                            "status": "open",
                            "automaticPersonCreation": False,
                        }
                        if missing_materialization_count
                        else None
                    ),
                    "anchorState": anchor_state,
                    "anchor": anchor,
                    "counts": {
                        # Compatibility aliases are deliberately redacted to
                        # the visible counts.  Pre-privacy component size must
                        # never enter the controlled payload.
                        "sourceProfiles": len(occurrence_ids),
                        "sourceRelationships": len(component_relationships),
                        "visibleProfiles": len(occurrence_ids),
                        "visibleRelationships": len(component_relationships),
                    },
                    "personIds": public_profile_ids,
                    "occurrenceIds": public_profile_ids,
                    "relationshipIds": relationship_ids,
                    "nodes": nodes,
                    "edges": edges,
                }
            )
    networks.sort(key=lambda network: network["id"])
    for ordinal, network in enumerate(networks, start=1):
        network["label"] = _network_label(network, ordinal)
    return networks
