from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


PENDING_TIER = "agent_approved_human_pending"
_OPERATIONS = (
    "identity_source_profile_resolution",
    "identity_source_profile_agent_ratification",
    "identity_source_pair_resolution",
    "identity_source_pair_agent_ratification",
)
_DEPENDENCY_OPERATION = "identity_dependency_revalidation"


@dataclass(frozen=True)
class SourceProfileIdentitiesProjection:
    typed_candidate_ids: frozenset[str]
    typed_occurrence_ids: frozenset[str]
    external_occurrence_ids: frozenset[str]
    chains_by_occurrence: dict[str, dict[str, Any]]
    chains_by_candidate: dict[str, dict[str, Any]] = field(default_factory=dict)
    chain_ids_by_occurrence: dict[str, tuple[tuple[str, str, str], ...]] = field(
        default_factory=dict
    )


def project_source_profile_identities(
    connection: sqlite3.Connection, workspace_root: Path | None, *, context=None
) -> SourceProfileIdentitiesProjection:
    """Keep direct typed endpoints guarded even when their result is invalid.

    Guard membership comes from command/candidate backing IDs, never result
    JSON, names, person status, or transitive identity candidates. The backend
    owns artifact and current-state validation; this adapter also rejects
    ambiguous endpoints before allowing its pending tier into presentation.
    """
    if context is not None:
        from ..identity_source_profile_curation import _check_projection_context

        _check_projection_context(workspace_root, connection, context)
    candidate_rows = connection.execute(
        """SELECT DISTINCT ic.id,ic.occurrence_a_id,ic.occurrence_b_id
             FROM curation_commands cc
             JOIN identity_candidates ic ON ic.id=cc.object_id
                OR ic.id IN (
                    SELECT candidate_id FROM identity_decisions
                     WHERE command_id=cc.command_id
                )
            WHERE cc.command_type IN (?,?,?,?)""",
        _OPERATIONS,
    ).fetchall()
    candidates = {row["id"]: row for row in candidate_rows}
    dependency_candidate_ids = {
        row["id"]
        for row in connection.execute(
            """SELECT DISTINCT ic.id FROM curation_commands cc
                 JOIN identity_candidates ic ON ic.id=cc.object_id
                WHERE cc.command_type=?""",
            (_DEPENDENCY_OPERATION,),
        )
    }
    typed_candidate_ids = frozenset(candidates.keys() | dependency_candidate_ids)
    if not candidates:
        return SourceProfileIdentitiesProjection(
            typed_candidate_ids, frozenset(), frozenset(), {}
        )
    typed_occurrences = {
        row[key]
        for row in candidate_rows
        for key in ("occurrence_a_id", "occurrence_b_id")
    }
    endpoint_rows = {
        row["id"]: row
        for row in connection.execute(
            f"""SELECT po.id,po.person_id,si.item_type
                 FROM person_occurrences po
                 JOIN source_items si ON si.id=po.source_item_id
                WHERE po.id IN ({','.join('?' for _ in typed_occurrences)})""",
            tuple(sorted(typed_occurrences)),
        )
    }
    external_ids = frozenset(
        occurrence_id
        for occurrence_id, row in endpoint_rows.items()
        if row["item_type"] == "external_tree_profile"
    )
    chains: list[dict[str, Any]] = []
    if workspace_root is not None:
        from ..identity_source_profile_curation import (
            validated_source_profile_identity_chains,
        )

        try:
            result = validated_source_profile_identity_chains(
                workspace_root, connection, context=context
            )
            if isinstance(result, dict) and isinstance(result.get("chains"), list):
                chains = result["chains"]
        except (OSError, ValueError, TypeError, sqlite3.Error):
            # No badge or canonical membership survives failed validation.
            chains = []

    def usable(chain: Any) -> bool:
        if not isinstance(chain, dict):
            return False
        ids = chain.get("occurrenceIds")
        candidate_id = chain.get("candidateId")
        candidate = (
            candidates.get(candidate_id) if isinstance(candidate_id, str) else None
        )
        if (
            candidate is None
            or not isinstance(ids, list)
            or len(ids) != 2
            or any(not isinstance(value, str) or not value for value in ids)
            or len(set(ids)) != 2
            or set(ids) != {
                candidate["occurrence_a_id"], candidate["occurrence_b_id"]
            }
            or chain.get("identityDecisionTier") != PENDING_TIER
            or any(
                not isinstance(chain.get(key), str) or not chain[key]
                for key in ("candidateId", "commandId", "decisionId", "personId")
            )
        ):
            return False
        topology = chain.get("topology", "source_profile")
        if topology == "source_profile":
            topology_matches = ids[0] in external_ids and ids[1] not in external_ids
        elif topology == "source_pair":
            topology_matches = all(
                item in endpoint_rows and endpoint_rows[item]["item_type"] == "act"
                for item in ids
            )
        else:
            return False
        return bool(
            topology_matches
            and all(item in endpoint_rows for item in ids)
            and all(
                endpoint_rows[occurrence_id]["person_id"] == chain["personId"]
                for occurrence_id in ids
            )
        )

    # Distinct direct approvals may share an endpoint, but copies or forks of
    # one candidate/command/decision never establish another approval.
    candidate_counts = Counter(
        chain.get("candidateId")
        for chain in chains
        if isinstance(chain, dict) and isinstance(chain.get("candidateId"), str)
    )
    command_counts = Counter(
        chain["commandId"] for chain in chains
        if isinstance(chain, dict) and isinstance(chain.get("commandId"), str)
    )
    decision_counts = Counter(
        chain["decisionId"] for chain in chains
        if isinstance(chain, dict) and isinstance(chain.get("decisionId"), str)
    )
    signatures_by_occurrence: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for chain in chains:
        if not isinstance(chain, dict) or not isinstance(chain.get("candidateId"), str):
            continue
        candidate = candidates.get(chain["candidateId"])
        ids = chain.get("occurrenceIds")
        if (
            candidate is not None and isinstance(ids, list)
            and all(isinstance(item, str) for item in ids)
            and set(ids) == {candidate["occurrence_a_id"], candidate["occurrence_b_id"]}
            and isinstance(chain.get("personId"), str) and chain["personId"]
            and isinstance(chain.get("identityDecisionTier"), str)
        ):
            for occurrence_id in ids:
                signatures_by_occurrence[occurrence_id].add(
                    (chain["personId"], chain["identityDecisionTier"])
                )
    contradictory_endpoints = {
        occurrence_id for occurrence_id, signatures in signatures_by_occurrence.items()
        if len(signatures) > 1
    }
    by_candidate: dict[str, dict[str, Any]] = {}
    for chain in chains:
        if (
            usable(chain)
            and candidate_counts[chain["candidateId"]] == 1
            and command_counts[chain["commandId"]] == 1
            and decision_counts[chain["decisionId"]] == 1
            and not contradictory_endpoints.intersection(chain["occurrenceIds"])
        ):
            by_candidate[chain["candidateId"]] = chain
    # A deterministic representative preserves the existing occurrence adapter;
    # the complete direct chain IDs retain provenance without graph closure.
    by_candidate = dict(sorted(by_candidate.items()))
    by_occurrence: dict[str, dict[str, Any]] = {}
    ids_by_occurrence: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for chain in by_candidate.values():
        for occurrence_id in chain["occurrenceIds"]:
            by_occurrence.setdefault(occurrence_id, chain)
            ids_by_occurrence[occurrence_id].append(
                (chain["candidateId"], chain["commandId"], chain["decisionId"])
            )
    return SourceProfileIdentitiesProjection(
        typed_candidate_ids, frozenset(typed_occurrences), external_ids,
        by_occurrence, by_candidate,
        {item: tuple(sorted(values)) for item, values in sorted(ids_by_occurrence.items())},
    )
