from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from ..command_envelope import fingerprint
from ..workspace import stable_id


_CHILD_TO_PARENT_TYPES = {
    "child_of",
    "child-of",
    "child-of-father",
    "child-of-mother",
}
_PARENT_TO_CHILD_TYPES = {"father_of", "mother_of", "parent_of"}
_SUPPORTED_PROVENANCE = {
    "legacy_relation",
    "source_assertion",
    "event_participation_role",
    "external_tree_assertion",
}
_QUALIFIED_CONTEXT_SCOPES = {"canonical"}
_HIDDEN_STATUSES = {"masked", "masked_living", "private", "restricted"}


def _strings(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return sorted({value for value in values if isinstance(value, str) and value})


def _one_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _is_hidden(value: dict[str, Any]) -> bool:
    def hidden_status(raw: Any) -> bool:
        if isinstance(raw, dict):
            if raw.get("redacted") is True:
                return True
            raw = raw.get("status")
        return (
            isinstance(raw, str)
            and raw.strip().casefold() in _HIDDEN_STATUSES
        )

    return (
        value.get("sourceRedacted") is True
        or hidden_status(value.get("privacy"))
        or hidden_status(value.get("evidenceStatus"))
    )


def _visible_tree_people(
    family_tree: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for person in (family_tree or {}).get("people", []):
        if not isinstance(person, dict):
            continue
        person_id = _one_string(person.get("id"))
        if person_id is None:
            continue
        if _is_hidden(person):
            continue
        result[person_id] = person
    return result


def _context_crosswalk(
    person_contexts: list[dict[str, Any]], visible_tree_ids: set[str]
) -> tuple[dict[str, str], dict[str, str]]:
    """Return occurrence -> tree card only for an exact canonical crosswalk.

    A context with zero or multiple visible tree cards is deliberately ignored.
    An occurrence claimed by two different qualified contexts is also ignored.
    This keeps unresolved identity and presentation groupings from becoming an
    implicit bridge between people.
    """

    candidates: dict[str, set[str]] = defaultdict(set)
    tree_scopes: dict[str, set[str]] = defaultdict(set)
    for context in person_contexts:
        if not isinstance(context, dict):
            continue
        identity_scope = _one_string(context.get("identityScope"))
        tree_ids = sorted(
            set(_strings(context.get("treePersonIds"))) & visible_tree_ids
        )
        for tree_id in tree_ids:
            if identity_scope:
                tree_scopes[tree_id].add(identity_scope)
        if identity_scope not in _QUALIFIED_CONTEXT_SCOPES or len(tree_ids) != 1:
            continue
        tree_id = tree_ids[0]
        for occurrence_id in _strings(context.get("occurrenceIds")):
            candidates[occurrence_id].add(tree_id)
    crosswalk = {
        occurrence_id: next(iter(tree_ids))
        for occurrence_id, tree_ids in candidates.items()
        if len(tree_ids) == 1
    }
    scope_by_tree = {
        tree_id: (
            "canonical"
            if scopes == {"canonical"}
            else "presentation_only"
        )
        for tree_id, scopes in tree_scopes.items()
    }
    return crosswalk, scope_by_tree


def _orientation(
    relation_type: Any, from_id: Any, to_id: Any
) -> tuple[str, str] | None:
    if not isinstance(from_id, str) or not isinstance(to_id, str):
        return None
    if from_id == to_id:
        return None
    if relation_type in _CHILD_TO_PARENT_TYPES:
        return from_id, to_id
    if relation_type in _PARENT_TO_CHILD_TYPES:
        return to_id, from_id
    return None


def _provenance_kind(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, dict):
        return _one_string(value.get("kind"))
    return None


def _relationship_provenance(
    relationship: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    kind = _provenance_kind(relationship.get("provenance"))
    if kind not in _SUPPORTED_PROVENANCE:
        return None, "unsupported_or_missing_provenance"
    relationship_id = _one_string(relationship.get("id"))
    if relationship_id is None:
        return None, "missing_relationship_id"

    event_ids = _strings(relationship.get("backingEventIds"))
    if kind == "event_participation_role" and not event_ids:
        return None, "missing_event_backing"

    assertion_ids = _strings(relationship.get("backingAssertionIds"))
    source_assertion_id = _one_string(relationship.get("sourceAssertionId"))
    if source_assertion_id:
        assertion_ids.append(source_assertion_id)
    if kind == "source_assertion":
        assertion_ids.append(relationship_id)
    assertion_ids = sorted(set(assertion_ids))

    source_ids = _strings(relationship.get("backingSourceIds"))
    source_id = _one_string(relationship.get("sourceId"))
    if source_id:
        source_ids.append(source_id)
    source_ids = sorted(set(source_ids))

    if kind == "event_participation_role" and not source_ids:
        return None, "missing_source_backing"
    if (
        kind in {"legacy_relation", "source_assertion", "external_tree_assertion"}
        and not source_ids
    ):
        return None, "missing_source_backing"

    backing_occurrence_ids = _strings(relationship.get("backingOccurrenceIds"))
    for key in ("fromOccurrenceId", "toOccurrenceId"):
        occurrence_id = _one_string(relationship.get(key))
        if occurrence_id:
            backing_occurrence_ids.append(occurrence_id)
    backing_occurrence_ids = sorted(set(backing_occurrence_ids))

    provenance = {
        "kind": kind,
        "sourceIds": source_ids,
        "backingRelationshipIds": [relationship_id],
        "backingEventIds": event_ids,
        "backingAssertionIds": assertion_ids,
        "backingSegmentIds": _strings(
            [relationship.get("segmentId")]
            if _one_string(relationship.get("segmentId"))
            else []
        ),
        "backingOccurrenceIds": backing_occurrence_ids,
    }
    return provenance, None


def _edge_from_relationship(
    *,
    relationship: dict[str, Any],
    child_node_id: str,
    parent_node_id: str,
    scope: str,
    network_id: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    provenance, reason = _relationship_provenance(relationship)
    if provenance is None:
        return None, reason
    relationship_id = provenance["backingRelationshipIds"][0]
    edge_id = stable_id(
        "conditional_ancestry_edge",
        scope,
        network_id or "family-tree",
        relationship_id,
        child_node_id,
        parent_node_id,
    )
    return (
        {
            "id": edge_id,
            "fromNodeId": child_node_id,
            "toNodeId": parent_node_id,
            "direction": "child_to_parent",
            "relationKind": "source_stated",
            "status": "unresolved",
            "sourceStatus": relationship.get("status") or "source-reported",
            "verificationStatus": relationship.get("verificationStatus"),
            "identityStatus": relationship.get("identityStatus") or "unresolved",
            "canonicalRelationship": False,
            "provenance": provenance,
            "sourceIds": provenance["sourceIds"],
            "backingRelationshipIds": provenance["backingRelationshipIds"],
            "backingEventIds": provenance["backingEventIds"],
            "backingAssertionIds": provenance["backingAssertionIds"],
            "backingSegmentIds": provenance["backingSegmentIds"],
            "backingOccurrenceIds": provenance["backingOccurrenceIds"],
        },
        None,
    )


def _hypothesis_edge(
    *,
    hypothesis: dict[str, Any],
    child_node_id: str,
    parent_node_id: str,
) -> tuple[dict[str, Any] | None, str | None]:
    hypothesis_id = _one_string(hypothesis.get("id"))
    kind = _provenance_kind(hypothesis.get("provenance"))
    if hypothesis_id is None or kind != "structured_hypothesis":
        return None, "invalid_structured_hypothesis"
    source_ids = _strings(hypothesis.get("sourceIds"))
    event_ids = _strings(hypothesis.get("backingEventIds"))
    basis = hypothesis.get("basis") or hypothesis.get("rationale")
    if isinstance(basis, str):
        basis = basis.strip()
    if not source_ids or not event_ids or not basis:
        return None, "missing_hypothesis_evidence"
    backing_ids = _strings(hypothesis.get("backingHypothesisIds"))
    backing_ids.append(hypothesis_id)
    backing_ids = sorted(set(backing_ids))
    provenance = {
        "kind": "structured_hypothesis",
        "sourceIds": source_ids,
        "backingHypothesisIds": backing_ids,
        "backingRelationshipIds": [],
        "backingEventIds": event_ids,
        "backingAssertionIds": [],
        "backingSegmentIds": [],
        "backingOccurrenceIds": _strings([hypothesis.get("parentOccurrenceId")]),
    }
    return (
        {
            "id": stable_id(
                "conditional_ancestry_edge",
                "structured-hypothesis",
                hypothesis_id,
                child_node_id,
                parent_node_id,
            ),
            "fromNodeId": child_node_id,
            "toNodeId": parent_node_id,
            "direction": "child_to_parent",
            "relationKind": "parentage_hypothesis",
            "status": "unresolved",
            "sourceStatus": hypothesis.get("status") or "unresolved",
            "verificationStatus": hypothesis.get("verificationStatus"),
            "identityStatus": hypothesis.get("identityStatus") or "unresolved",
            "childEndpointScope": hypothesis.get("childEndpointScope"),
            "basis": basis,
            **(
                {"rationale": hypothesis["rationale"].strip()}
                if isinstance(hypothesis.get("rationale"), str)
                and hypothesis["rationale"].strip()
                else {}
            ),
            "canonicalRelationship": False,
            "provenance": provenance,
            "sourceIds": source_ids,
            "backingRelationshipIds": [],
            "backingEventIds": event_ids,
            "backingAssertionIds": [],
            "backingSegmentIds": [],
            "backingOccurrenceIds": provenance["backingOccurrenceIds"],
            "backingHypothesisIds": backing_ids,
        },
        None,
    )


def _gap(
    *,
    root_id: str,
    kind: str,
    node_id: str | None = None,
    relationship_id: str | None = None,
    condition: dict[str, Any] | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    parts = [root_id, kind, node_id or "no-node", relationship_id or "no-relation"]
    result: dict[str, Any] = {
        "id": stable_id("conditional_ancestry_gap", *parts),
        "rootId": root_id,
        "kind": kind,
        "condition": condition or {"kind": kind},
    }
    if node_id:
        result["nodeId"] = node_id
    if relationship_id:
        result["relationshipId"] = relationship_id
    if label:
        result["label"] = label
    return result


def _endpoint_node(
    value: Any,
    *,
    family_node_by_tree: dict[str, str],
    occurrence_to_tree: dict[str, str],
) -> str | None:
    endpoint = _one_string(value)
    if endpoint is None:
        return None
    if endpoint in family_node_by_tree:
        return family_node_by_tree[endpoint]
    tree_id = occurrence_to_tree.get(endpoint)
    return family_node_by_tree.get(tree_id) if tree_id else None


def _structured_hypothesis_endpoints(
    hypothesis: dict[str, Any],
    *,
    family_node_by_tree: dict[str, str],
    occurrence_node_by_id: dict[str, str],
    occurrence_to_tree: dict[str, str],
) -> tuple[str | None, str | None]:
    """Resolve explicit manifest endpoints without creating an identity bridge."""

    child_tree_id = _one_string(hypothesis.get("childTreePersonId"))
    parent_occurrence_id = _one_string(hypothesis.get("parentOccurrenceId"))
    endpoint_scope = _one_string(hypothesis.get("childEndpointScope"))
    if parent_occurrence_id is not None or endpoint_scope is not None:
        if endpoint_scope not in {"canonical", "presentation_hypothesis_group"}:
            return None, None
        return (
            family_node_by_tree.get(child_tree_id or ""),
            occurrence_node_by_id.get(parent_occurrence_id or ""),
        )

    # Compatibility for callers still supplying the older all-crosswalk form.
    return (
        _endpoint_node(
            hypothesis.get("childTreePersonId", hypothesis.get("childOccurrenceId")),
            family_node_by_tree=family_node_by_tree,
            occurrence_to_tree=occurrence_to_tree,
        ),
        _endpoint_node(
            hypothesis.get("parentTreePersonId"),
            family_node_by_tree=family_node_by_tree,
            occurrence_to_tree=occurrence_to_tree,
        ),
    )


def project_conditional_ancestry(
    *,
    family_tree: dict[str, Any] | None,
    relationships: list[dict[str, Any]],
    person_contexts: list[dict[str, Any]],
    occurrences: list[dict[str, Any]] | None = None,
    source_reported_networks: list[dict[str, Any]] | None = None,
    structured_hypotheses: list[dict[str, Any]] | None = None,
    max_depth: int = 8,
    max_nodes: int = 256,
    max_edges: int = 512,
) -> dict[str, Any]:
    """Project a bounded, privacy-safe conditional ancestry overlay.

    Inputs must already be privacy-filtered public projections.  This function
    never parses labels or prose and never mutates its inputs.  Every source
    network is traversed in its own identity scope.
    """

    if max_depth < 0 or max_nodes < 1 or max_edges < 1:
        raise ValueError("Limity ancestry wymagają depth >= 0 oraz nodes/edges >= 1")

    visible_people = _visible_tree_people(family_tree)
    visible_tree_ids = set(visible_people)
    occurrence_to_tree, scope_by_tree = _context_crosswalk(
        person_contexts, visible_tree_ids
    )

    nodes_by_id: dict[str, dict[str, Any]] = {}
    family_node_by_tree: dict[str, str] = {}
    roots: list[dict[str, Any]] = []
    for tree_id, person in sorted(visible_people.items()):
        node_id = stable_id("conditional_ancestry_node", "family-tree", tree_id)
        family_node_by_tree[tree_id] = node_id
        label = person.get("name") or tree_id
        nodes_by_id[node_id] = {
            "id": node_id,
            "label": label,
            "treePersonId": tree_id,
            "publicProfileId": tree_id,
            "identityScope": scope_by_tree.get(tree_id, "presentation_only"),
            "scope": "family_tree",
        }
        roots.append(
            {
                "id": stable_id("conditional_ancestry_root", "family-tree", tree_id),
                "nodeId": node_id,
                "treePersonId": tree_id,
                "label": label,
                "scope": "family_tree",
            }
        )

    occurrence_by_id = {
        item["id"]: item
        for item in occurrences or []
        if isinstance(item, dict)
        and _one_string(item.get("id"))
        and not _is_hidden(item)
    }
    if occurrences is not None:
        occurrence_to_tree = {
            occurrence_id: tree_id
            for occurrence_id, tree_id in occurrence_to_tree.items()
            if occurrence_id in occurrence_by_id
        }
    occurrence_node_by_id: dict[str, str] = {}
    # An occurrence backing ID is not automatically a routable person profile.
    # Only exact exported profile IDs/aliases may become a full-card action.
    route_contexts: dict[str, set[str]] = defaultdict(set)
    for context in person_contexts:
        public_profile_id = _one_string(context.get("publicProfileId"))
        if public_profile_id is None:
            continue
        for route_id in [public_profile_id, *_strings(context.get("aliases"))]:
            route_contexts[route_id].add(public_profile_id)
    for occurrence_id, occurrence in sorted(occurrence_by_id.items()):
        node_id = stable_id(
            "conditional_ancestry_node", "source-occurrence", occurrence_id
        )
        occurrence_node_by_id[occurrence_id] = node_id
        source_id = _one_string(occurrence.get("sourceId"))
        nodes_by_id[node_id] = {
            "id": node_id,
            "label": occurrence.get("normalizedName")
            or occurrence.get("name")
            or "Wystąpienie źródłowe",
            **(
                {"publicProfileId": next(iter(route_contexts[occurrence_id]))}
                if len(route_contexts.get(occurrence_id, set())) == 1
                else {}
            ),
            "occurrenceId": occurrence_id,
            "identityScope": "occurrence",
            "scope": "source_occurrence",
            "sourceIds": [source_id] if source_id else [],
        }

    candidate_edges: dict[str, dict[str, Any]] = {}
    blocked_by_child: dict[str, list[dict[str, Any]]] = defaultdict(list)
    excluded_by_reason: dict[str, int] = defaultdict(int)

    # Explicit existing-family bindings have already been checked against SQL
    # by tree_family_evidence.  Keep their own card endpoints and uncertainty;
    # an exact presentation binding is not a canonical identity bridge.
    for family in (family_tree or {}).get("families", []):
        if family.get("canonicalRelationship") is not False:
            continue
        for binding in family.get("relationshipEvidence", []):
            child_node_id = family_node_by_tree.get(binding.get("childId"))
            parent_node_id = family_node_by_tree.get(binding.get("parentId"))
            if child_node_id is None or parent_node_id is None:
                # Hidden endpoints stop traversal before public counts and gaps.
                continue
            evidence = binding.get("evidence") or {}
            if evidence.get("evidenceRedacted") is True:
                blocked_by_child[child_node_id].append({
                    "kind": "family_backing_not_public",
                    "condition": {"kind": "public_source_backing_required"},
                })
                continue
            source_ids = _strings(binding.get("sourceIds"))
            assertion_ids = _strings(binding.get("sourceAssertionIds"))
            if (not source_ids or not assertion_ids or not evidence.get("auditFingerprint")
                    or binding.get("relationshipIdentityStatus") != "unresolved"
                    or binding.get("canonicalRelationship") is not False):
                blocked_by_child[child_node_id].append({
                    "kind": "family_backing_not_validated",
                    "condition": {"kind": "validated_family_backing_required"},
                })
                continue
            edge_id = stable_id("conditional_ancestry_edge", "existing-family",
                                family["id"], binding["childId"], binding["parentId"])
            provenance = {
                "kind": "existing_tree_family", "sourceIds": source_ids,
                "backingRelationshipIds": [family["id"]],
                "backingEventIds": [], "backingAssertionIds": assertion_ids,
                "backingClaimIds": _strings(binding.get("claimIds")),
                "backingSegmentIds": [],
                "backingOccurrenceIds": _strings([
                    binding.get("childOccurrenceId"), binding.get("parentOccurrenceId")
                ]),
            }
            candidate_edges[edge_id] = {
                "id": edge_id, "fromNodeId": child_node_id, "toNodeId": parent_node_id,
                "direction": "child_to_parent", "relationKind": "source_stated",
                "status": "unresolved", "sourceStatus": "source_reported",
                "verificationStatus": evidence.get("tier", "imported_unverified"),
                "identityStatus": "unresolved", "canonicalRelationship": False,
                "evidence": evidence, "provenance": provenance,
                **{key: value for key, value in provenance.items() if key != "kind"},
            }

    relationship_by_id = {
        item["id"]: item
        for item in relationships
        if isinstance(item, dict) and _one_string(item.get("id"))
    }
    source_occurrence_root_nodes: set[str] = set()
    for relationship_id, relationship in sorted(relationship_by_id.items()):
        oriented = _orientation(
            relationship.get("type"),
            relationship.get("fromOccurrenceId"),
            relationship.get("toOccurrenceId"),
        )
        if oriented is None:
            continue
        child_occurrence_id, parent_occurrence_id = oriented
        child_tree_id = occurrence_to_tree.get(child_occurrence_id)
        parent_tree_id = occurrence_to_tree.get(parent_occurrence_id)
        child_node_id = (
            family_node_by_tree.get(child_tree_id)
            if child_tree_id
            else occurrence_node_by_id.get(child_occurrence_id)
        )
        parent_node_id = (
            family_node_by_tree.get(parent_tree_id)
            if parent_tree_id
            else occurrence_node_by_id.get(parent_occurrence_id)
        )
        if child_node_id is None:
            # No public node can safely own this gap.
            continue
        if parent_node_id is None:
            blocked_by_child[child_node_id].append(
                {
                    "kind": "endpoint_not_publicly_crosswalked",
                    "condition": {"kind": "explicit_public_endpoint_required"},
                }
            )
            continue
        if (
            _provenance_kind(relationship.get("provenance"))
            == "external_tree_assertion"
            or relationship.get("relationshipScope")
            == "source_reported_external_tree"
        ):
            excluded_by_reason["external_tree_requires_isolated_network"] += 1
            continue
        provenance, provenance_reason = _relationship_provenance(relationship)
        if provenance is None:
            assert provenance_reason is not None
            excluded_by_reason[provenance_reason] += 1
            blocked_by_child[child_node_id].append(
                {
                    "kind": provenance_reason,
                    "relationshipId": relationship_id,
                    "condition": {"kind": "supported_provenance_required"},
                }
            )
            continue
        if child_node_id == parent_node_id:
            excluded_by_reason["same_visible_node"] += 1
            blocked_by_child[child_node_id].append(
                {
                    "kind": "same_visible_node",
                    "relationshipId": relationship_id,
                    "condition": {"kind": "distinct_endpoints_required"},
                }
            )
            continue
        edge, reason = _edge_from_relationship(
            relationship=relationship,
            child_node_id=child_node_id,
            parent_node_id=parent_node_id,
            scope="family_tree",
        )
        if edge is None:
            assert reason is not None
            blocked_by_child[child_node_id].append(
                {
                    "kind": reason,
                    "relationshipId": relationship_id,
                    "condition": {"kind": "supported_provenance_required"},
                }
            )
            continue
        candidate_edges[edge["id"]] = edge
        if child_node_id in occurrence_node_by_id.values():
            source_occurrence_root_nodes.add(child_node_id)

    for node_id in sorted(source_occurrence_root_nodes):
        node = nodes_by_id[node_id]
        roots.append(
            {
                "id": stable_id(
                    "conditional_ancestry_root", "source-occurrence", node["occurrenceId"]
                ),
                "nodeId": node_id,
                "label": node["label"],
                "scope": "source_occurrence",
            }
        )

    for hypothesis in structured_hypotheses or []:
        if not isinstance(hypothesis, dict):
            continue
        child_node_id, parent_node_id = _structured_hypothesis_endpoints(
            hypothesis,
            family_node_by_tree=family_node_by_tree,
            occurrence_node_by_id=occurrence_node_by_id,
            occurrence_to_tree=occurrence_to_tree,
        )
        if child_node_id is None:
            continue
        if parent_node_id is None:
            blocked_by_child[child_node_id].append(
                {
                    "kind": "hypothesis_endpoint_not_publicly_crosswalked",
                    "condition": {"kind": "explicit_public_endpoint_required"},
                }
            )
            continue
        if child_node_id == parent_node_id:
            excluded_by_reason["same_visible_node"] += 1
            continue
        edge, reason = _hypothesis_edge(
            hypothesis=hypothesis,
            child_node_id=child_node_id,
            parent_node_id=parent_node_id,
        )
        if edge is None:
            assert reason is not None
            excluded_by_reason[reason] += 1
            blocked_by_child[child_node_id].append(
                {
                    "kind": reason,
                    "condition": {"kind": "explicit_structured_hypothesis_required"},
                }
            )
            continue
        candidate_edges[edge["id"]] = edge
        endpoint_scope = _one_string(hypothesis.get("childEndpointScope"))
        if endpoint_scope is not None:
            nodes_by_id[child_node_id]["parentageHypothesisAnchor"] = True
            previous_scope = nodes_by_id[child_node_id].get("hypothesisEndpointScope")
            nodes_by_id[child_node_id]["hypothesisEndpointScope"] = (
                endpoint_scope
                if previous_scope in {None, endpoint_scope}
                else "canonical"
            )

    # External source-reported networks remain distinct scopes even when their
    # labels or public occurrence shells happen to match.
    for network in sorted(
        source_reported_networks or [], key=lambda item: str(item.get("id", ""))
    ):
        if not isinstance(network, dict):
            continue
        network_id = _one_string(network.get("id"))
        if network_id is None or network.get("privacyFiltered") is not True:
            continue
        network_node_by_tree: dict[str, str] = {}
        for network_node in network.get("nodes", []):
            if not isinstance(network_node, dict):
                continue
            tree_node_id = _one_string(network_node.get("treeNodeId"))
            public_profile_id = _one_string(network_node.get("publicProfileId"))
            input_occurrence_id = _one_string(network_node.get("occurrenceId"))
            if (
                _is_hidden(network_node)
                or
                tree_node_id is None
                or public_profile_id != tree_node_id
                or input_occurrence_id != tree_node_id
            ):
                continue
            node_id = stable_id(
                "conditional_ancestry_node", "source-network", network_id, tree_node_id
            )
            network_node_by_tree[tree_node_id] = node_id
            nodes_by_id[node_id] = {
                "id": node_id,
                "label": network_node.get("label") or public_profile_id,
                "publicProfileId": public_profile_id,
                "identityScope": "occurrence",
                "scope": "source_reported_network",
                "networkId": network_id,
            }
        anchor = network.get("anchor") if isinstance(network.get("anchor"), dict) else {}
        anchor_tree_id = _one_string(anchor.get("treeNodeId"))
        anchor_node_id = network_node_by_tree.get(anchor_tree_id) if anchor_tree_id else None
        if anchor_node_id is not None and not _is_hidden(anchor):
            roots.append(
                {
                    "id": stable_id(
                        "conditional_ancestry_root", "source-network", network_id
                    ),
                    "nodeId": anchor_node_id,
                    "label": anchor.get("label")
                    or nodes_by_id[anchor_node_id]["label"],
                    "scope": "source_reported_network",
                    "networkId": network_id,
                }
            )
        for network_edge in sorted(
            network.get("edges", []), key=lambda item: str(item.get("treeEdgeId", ""))
        ):
            if not isinstance(network_edge, dict):
                continue
            relationship_id = _one_string(network_edge.get("relationshipId"))
            relationship = relationship_by_id.get(relationship_id or "")
            oriented = _orientation(
                network_edge.get("type"),
                network_edge.get("fromTreeNodeId"),
                network_edge.get("toTreeNodeId"),
            )
            if oriented is None:
                continue
            child_tree_id, parent_tree_id = oriented
            child_node_id = network_node_by_tree.get(child_tree_id)
            parent_node_id = network_node_by_tree.get(parent_tree_id)
            if child_node_id is None:
                continue
            if parent_node_id is None:
                blocked_by_child[child_node_id].append(
                    {
                        "kind": "endpoint_not_publicly_crosswalked",
                        "condition": {"kind": "explicit_public_endpoint_required"},
                    }
                )
                continue
            if relationship is None:
                reason = "missing_relationship_backing"
                excluded_by_reason[reason] += 1
                blocked_by_child[child_node_id].append(
                    {
                        "kind": reason,
                        "relationshipId": relationship_id,
                        "condition": {"kind": "public_relationship_backing_required"},
                    }
                )
                continue
            provenance_kind = _provenance_kind(relationship.get("provenance"))

            def matches_endpoint(raw_id: Any, public_id: Any) -> bool:
                return (
                    isinstance(raw_id, str)
                    and isinstance(public_id, str)
                    and (
                        raw_id == public_id
                        or stable_id("source_reported_tree_node", raw_id)
                        == public_id
                    )
                )

            exact_external_backing = (
                relationship_id in _strings(network.get("relationshipIds"))
                and relationship.get("relationshipScope")
                == "source_reported_external_tree"
                and relationship.get("canonicalRelationship") is False
                and relationship.get("identityStatus") == "unresolved"
                and provenance_kind in {None, "external_tree_assertion"}
                and relationship.get("type") == network_edge.get("type")
                and matches_endpoint(
                    relationship.get("fromOccurrenceId"),
                    network_edge.get("fromTreeNodeId"),
                )
                and matches_endpoint(
                    relationship.get("toOccurrenceId"),
                    network_edge.get("toTreeNodeId"),
                )
            )
            if not exact_external_backing:
                reason = "invalid_external_network_backing"
                excluded_by_reason[reason] += 1
                blocked_by_child[child_node_id].append(
                    {
                        "kind": reason,
                        "condition": {
                            "kind": "exact_public_external_backing_required"
                        },
                    }
                )
                continue

            # The restricted public export intentionally strips canonical
            # source-item provenance from external-tree relationships.  Inside
            # one privacy-filtered network, the network itself is the source
            # carrier.  This does not authorize a family-tree bridge or claim
            # an independent/primary source.
            provenance = {
                "kind": "external_tree",
                "sourceNetworkId": network_id,
                "sourceIds": [],
                "backingRelationshipIds": [relationship_id],
                "backingEventIds": [],
                "backingAssertionIds": [],
                "backingSegmentIds": [],
                "backingOccurrenceIds": [],
            }
            edge = {
                "id": stable_id(
                    "conditional_ancestry_edge",
                    "source_reported_network",
                    network_id,
                    relationship_id,
                    child_node_id,
                    parent_node_id,
                ),
                "fromNodeId": child_node_id,
                "toNodeId": parent_node_id,
                "direction": "child_to_parent",
                "relationKind": "source_stated",
                "status": "unresolved",
                "sourceStatus": "imported_unverified",
                "verificationStatus": "imported_unverified",
                "identityStatus": "unresolved",
                "canonicalRelationship": False,
                "provenance": provenance,
                "sourceIds": [],
                "backingRelationshipIds": [relationship_id],
                "backingEventIds": [],
                "backingAssertionIds": [],
                "backingSegmentIds": [],
                "backingOccurrenceIds": [],
            }
            candidate_edges[edge["id"]] = edge

    adjacency: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in candidate_edges.values():
        adjacency[edge["fromNodeId"]].append(edge)
    for values in adjacency.values():
        values.sort(key=lambda item: (item["toNodeId"], item["id"]))
    for values in blocked_by_child.values():
        values.sort(
            key=lambda item: (
                item["kind"],
                item.get("relationshipId") or "",
            )
        )

    projected_node_ids: set[str] = set()
    projected_edge_ids: set[str] = set()
    gaps_by_id: dict[str, dict[str, Any]] = {}
    root_audits: list[dict[str, Any]] = []
    for root in sorted(roots, key=lambda item: item["id"]):
        root_id = root["id"]
        root_node_id = root["nodeId"]
        queue: deque[tuple[str, int, tuple[str, ...]]] = deque(
            [(root_node_id, 0, (root_node_id,))]
        )
        visited_nodes = {root_node_id}
        expanded_nodes: set[str] = set()
        visited_edges: set[str] = set()
        max_depth_reached = 0
        stop_reasons: set[str] = set()
        frontier_count = 0
        alternative_path_count = 0
        cycle_edge_ids: set[str] = set()
        truncated = False
        projected_node_ids.add(root_node_id)

        def add_gap(item: dict[str, Any]) -> None:
            gaps_by_id.setdefault(item["id"], item)

        while queue:
            node_id, depth, path = queue.popleft()
            max_depth_reached = max(max_depth_reached, depth)
            if node_id in expanded_nodes:
                continue
            expanded_nodes.add(node_id)

            for blocked in blocked_by_child.get(node_id, []):
                stop_reasons.add(blocked["kind"])
                add_gap(
                    _gap(
                        root_id=root_id,
                        node_id=node_id,
                        kind=blocked["kind"],
                        relationship_id=blocked.get("relationshipId"),
                        condition=blocked["condition"],
                    )
                )

            outgoing = adjacency.get(node_id, [])
            if not outgoing and not blocked_by_child.get(node_id):
                stop_reasons.add("no_recorded_parentage")
                add_gap(
                    _gap(
                        root_id=root_id,
                        node_id=node_id,
                        kind="no_recorded_parentage",
                        condition={"kind": "explicit_parentage_evidence_required"},
                        label=(
                            "Brak jawnej kwalifikowanej relacji rodzicielskiej; "
                            "to granica danych, nie dowód braku rodziców."
                        ),
                    )
                )
            if depth >= max_depth and outgoing:
                truncated = True
                frontier_count += 1
                stop_reasons.add("max_depth")
                add_gap(
                    _gap(
                        root_id=root_id,
                        node_id=node_id,
                        kind="max_depth",
                        condition={"kind": "increase_max_depth", "limit": max_depth},
                        label="Dalsza ścieżka istnieje poza limitem głębokości.",
                    )
                )
                continue

            for edge in outgoing:
                parent_node_id = edge["toNodeId"]
                if edge["id"] not in visited_edges and len(visited_edges) >= max_edges:
                    truncated = True
                    frontier_count += 1
                    stop_reasons.add("max_edges")
                    add_gap(
                        _gap(
                            root_id=root_id,
                            node_id=node_id,
                            kind="max_edges",
                            condition={"kind": "increase_max_edges", "limit": max_edges},
                            label="Dalsza krawędź istnieje poza limitem audytu.",
                        )
                    )
                    continue
                if parent_node_id not in visited_nodes and len(visited_nodes) >= max_nodes:
                    truncated = True
                    frontier_count += 1
                    stop_reasons.add("max_nodes")
                    add_gap(
                        _gap(
                            root_id=root_id,
                            node_id=node_id,
                            kind="max_nodes",
                            condition={"kind": "increase_max_nodes", "limit": max_nodes},
                            label="Dalszy węzeł istnieje poza limitem audytu.",
                        )
                    )
                    continue
                visited_edges.add(edge["id"])
                projected_edge_ids.add(edge["id"])
                projected_node_ids.add(parent_node_id)
                if parent_node_id in path:
                    cycle_edge_ids.add(edge["id"])
                    continue
                if parent_node_id in visited_nodes:
                    alternative_path_count += 1
                    continue
                visited_nodes.add(parent_node_id)
                queue.append((parent_node_id, depth + 1, (*path, parent_node_id)))

        # A first-expanded-node BFS preserves alternative edges but may reach a
        # repeated node through a path that hides a later back-edge.  Detect
        # cycles on the bounded, actually visited subgraph without enumerating
        # every path.
        colors: dict[str, int] = {}
        for start_node_id in sorted(visited_nodes):
            if colors.get(start_node_id, 0) != 0:
                continue
            colors[start_node_id] = 1
            stack: list[tuple[str, int]] = [(start_node_id, 0)]
            while stack:
                current_node_id, edge_index = stack[-1]
                current_edges = [
                    edge
                    for edge in adjacency.get(current_node_id, [])
                    if edge["id"] in visited_edges
                    and edge["toNodeId"] in visited_nodes
                ]
                if edge_index >= len(current_edges):
                    colors[current_node_id] = 2
                    stack.pop()
                    continue
                edge = current_edges[edge_index]
                stack[-1] = (current_node_id, edge_index + 1)
                target_node_id = edge["toNodeId"]
                target_color = colors.get(target_node_id, 0)
                if target_color == 0:
                    colors[target_node_id] = 1
                    stack.append((target_node_id, 0))
                elif target_color == 1:
                    cycle_edge_ids.add(edge["id"])

        if cycle_edge_ids:
            stop_reasons.add("cycle")
        for cycle_edge_id in sorted(cycle_edge_ids):
            edge = candidate_edges[cycle_edge_id]
            add_gap(
                _gap(
                    root_id=root_id,
                    node_id=edge["fromNodeId"],
                    kind="cycle",
                    relationship_id=edge["backingRelationshipIds"][0]
                    if edge["backingRelationshipIds"]
                    else None,
                    condition={"kind": "path_local_cycle_review_required"},
                    label="Jawna relacja zamyka cykl w odwiedzonym podgrafie.",
                )
            )

        root_audits.append(
            {
                "rootId": root_id,
                "visitedNodeCount": len(visited_nodes),
                "visitedEdgeCount": len(visited_edges),
                "maxDepthReached": max_depth_reached,
                "complete": not truncated,
                "truncated": truncated,
                "stopReasons": sorted(stop_reasons),
                "frontierCount": frontier_count,
                "alternativePathCount": alternative_path_count,
                "cycleCount": len(cycle_edge_ids),
            }
        )

    projected_nodes = [
        nodes_by_id[node_id]
        for node_id in sorted(projected_node_ids)
        if node_id in nodes_by_id
    ]
    projected_edges = [
        candidate_edges[edge_id] for edge_id in sorted(projected_edge_ids)
    ]
    projected_gaps = [gaps_by_id[gap_id] for gap_id in sorted(gaps_by_id)]
    sorted_roots = sorted(roots, key=lambda item: item["id"])
    limits = {
        "maxDepth": max_depth,
        "maxNodes": max_nodes,
        "maxEdges": max_edges,
    }
    input_fingerprint = fingerprint(
        {
            "roots": sorted_roots,
            "nodes": projected_nodes,
            "edges": projected_edges,
            "gaps": projected_gaps,
            "rootAudits": root_audits,
            "limits": limits,
        }
    )
    truncated = any(item["truncated"] for item in root_audits)
    return {
        "version": 1,
        "roots": sorted_roots,
        "nodes": projected_nodes,
        "edges": projected_edges,
        "gaps": projected_gaps,
        "audit": {
            "inputFingerprint": input_fingerprint,
            "rootCount": len(sorted_roots),
            "nodeCount": len(projected_nodes),
            "edgeCount": len(projected_edges),
            "gapCount": len(projected_gaps),
            "allRootsComplete": not truncated,
            "snapshotTraversalComplete": not truncated,
            "contentCoverage": "not_assessed",
            "negativeEvidence": "not_established",
            "truncated": truncated,
            "limits": limits,
            "supportedRelationTypes": sorted(
                _CHILD_TO_PARENT_TYPES | _PARENT_TO_CHILD_TYPES
            ),
            "excludedByReason": dict(sorted(excluded_by_reason.items())),
            "rootAudits": root_audits,
        },
    }
