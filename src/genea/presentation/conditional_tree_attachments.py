from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..workspace import stable_id


_ALLOWED_IDENTITY_TIERS = {
    "agent_approved_human_pending",
    "human_confirmed",
}
_HIDDEN_TREE_PRIVACY = {"masked_living", "living", "restricted"}


def project_conditional_tree_attachments(
    *,
    networks: list[dict[str, Any]],
    occurrences: list[dict[str, Any]],
    tree: dict[str, Any] | None,
    contexts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Project direct, privacy-safe identity links to visible tree people.

    The link is presentation-only.  It never promotes an external relationship
    into a canonical family or union, and it is emitted only when every backing
    lookup has one exact result.
    """

    occurrences_by_tree_node: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for occurrence in occurrences:
        if not isinstance(occurrence, dict):
            continue
        occurrence_id = occurrence.get("id")
        if isinstance(occurrence_id, str) and occurrence_id:
            occurrences_by_tree_node[
                stable_id("source_reported_tree_node", occurrence_id)
            ].append(occurrence)

    contexts_by_person: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for context in contexts:
        if not isinstance(context, dict) or context.get("identityScope") != "canonical":
            continue
        canonical_ids = context.get("canonicalPersonIds")
        if (
            isinstance(canonical_ids, list)
            and len(canonical_ids) == 1
            and isinstance(canonical_ids[0], str)
            and canonical_ids[0]
        ):
            contexts_by_person[canonical_ids[0]].append(context)

    tree_people_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    tree_people = (tree or {}).get("people", []) if isinstance(tree, dict) else []
    if isinstance(tree_people, list):
        for person in tree_people:
            if not isinstance(person, dict):
                continue
            person_id = person.get("id")
            if isinstance(person_id, str) and person_id:
                tree_people_by_id[person_id].append(person)

    networks_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for network in networks:
        if not isinstance(network, dict):
            continue
        network_id = network.get("id")
        if isinstance(network_id, str) and network_id:
            networks_by_id[network_id].append(network)

    attachments: list[dict[str, Any]] = []
    for network_id in sorted(networks_by_id):
        matching_networks = networks_by_id[network_id]
        if len(matching_networks) != 1:
            continue
        nodes = matching_networks[0].get("nodes")
        if not isinstance(nodes, list):
            continue

        nodes_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for node in nodes:
            if not isinstance(node, dict):
                continue
            tree_node_id = node.get("treeNodeId")
            if isinstance(tree_node_id, str) and tree_node_id:
                nodes_by_id[tree_node_id].append(node)

        for tree_node_id in sorted(nodes_by_id):
            matching_nodes = nodes_by_id[tree_node_id]
            if len(matching_nodes) != 1:
                continue
            node = matching_nodes[0]
            tier = node.get("identityDecisionTier")
            if (
                node.get("identityStatus") != "resolved_same"
                or node.get("personIdentityResolved") is not True
                or tier not in _ALLOWED_IDENTITY_TIERS
            ):
                continue

            matching_occurrences = occurrences_by_tree_node.get(tree_node_id, [])
            if len(matching_occurrences) != 1:
                continue
            occurrence = matching_occurrences[0]
            person_id = occurrence.get("personId")
            if (
                not isinstance(person_id, str)
                or not person_id
                or occurrence.get("externalTreeIdentityProjection")
                != "resolved_same"
                or occurrence.get("personIdentityResolved") is not True
                or occurrence.get("identityDecisionTier") != tier
            ):
                continue

            matching_contexts = contexts_by_person.get(person_id, [])
            if len(matching_contexts) != 1:
                continue
            tree_person_ids = matching_contexts[0].get("treePersonIds")
            if not isinstance(tree_person_ids, list):
                continue

            visible_targets: list[str] = []
            for target_id in tree_person_ids:
                if not isinstance(target_id, str) or not target_id:
                    continue
                matching_tree_people = tree_people_by_id.get(target_id, [])
                if len(matching_tree_people) != 1:
                    continue
                tree_person = matching_tree_people[0]
                if (
                    target_id == tree_node_id
                    or tree_person.get("sourceReported") is True
                    or tree_person.get("sourceReportedNetworkId") is not None
                    or tree_person.get("privacy") in _HIDDEN_TREE_PRIVACY
                ):
                    continue
                visible_targets.append(target_id)
            if len(visible_targets) != 1:
                continue
            target_id = visible_targets[0]

            attachments.append(
                {
                    "id": stable_id(
                        "conditional_tree_identity_attachment",
                        network_id,
                        tree_node_id,
                        target_id,
                    ),
                    "networkId": network_id,
                    "fromTreeNodeId": tree_node_id,
                    "toTreePersonId": target_id,
                    "identityStatus": "resolved_same",
                    "identityDecisionTier": tier,
                    "kind": "external_profile_identity_attachment",
                    "canonicalRelationship": False,
                    "relationshipIdentityStatus": "unresolved",
                    "automaticMerge": False,
                }
            )

    return sorted(
        attachments,
        key=lambda item: (
            item["networkId"],
            item["fromTreeNodeId"],
            item["toTreePersonId"],
            item["id"],
        ),
    )
