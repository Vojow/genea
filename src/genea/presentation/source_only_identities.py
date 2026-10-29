"""Additive, fail-closed presentation of direct source-only identity groups."""
from __future__ import annotations

import re
import sqlite3
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any

from ..identity_source_only_curation import (
    IDENTITY_DECISION_TIER, VERDICT, source_only_typed_ids,
    validated_source_only_identity_chains,
)


def project_source_only_identities(connection: sqlite3.Connection,
        workspace_root: Path | None, *,
        visible_occurrence_ids: set[str] | frozenset[str] | None = None,
        context=None) -> list[dict[str, Any]]:
    """Project admitted groups only when every literal member is visible.

No root means artifact validation is unavailable, so no groups are emitted.
Callers building a controlled payload must supply their privacy-filtered visible
occurrence IDs. Raw names, private evidence and candidate preimages are omitted.
"""
    if context is not None:
        from ..identity_source_profile_curation import _check_projection_context

        _check_projection_context(workspace_root, connection, context)
    if workspace_root is None:
        return []
    try:
        candidate_ids, _ = source_only_typed_ids(connection)
        result = validated_source_only_identity_chains(workspace_root, connection, context=context)
        groups = result.get("groups") if isinstance(result, dict) else None
        if not isinstance(groups, list):
            return []
        counts = Counter(oid for group in groups if isinstance(group, dict)
                         and isinstance(group.get("occurrenceIds"), list)
                         for oid in group["occurrenceIds"] if isinstance(oid, str))
        id_counts = Counter(group.get("id") for group in groups if isinstance(group, dict)
                            and isinstance(group.get("id"), str))
        output = []
        for group in groups:
            required = {
                "id", "candidateId", "occurrenceIds", "sourceItemIds", "personId",
                "identityDecisionTier", "resolutionCommandId", "ratificationCommandId",
                "reviewRefs", "humanReviewDebt"}
            if not isinstance(group, dict) or not required <= set(group) or set(group) - required - {"directEdges", "lineage", "introducedCandidateIds"}:
                continue
            ids, sources = group["occurrenceIds"], group["sourceItemIds"]
            if not (isinstance(ids, list) and len(ids) >= 2 and
                    all(isinstance(oid, str) and oid for oid in ids) and len(set(ids)) == len(ids)
                    and isinstance(sources, list) and len(sources) == len(ids) and
                    all(isinstance(sid, str) and sid for sid in sources) and len(set(sources)) == len(ids)):
                continue
            if any(not isinstance(group[key], str) or not group[key] for key in
                   ("id", "candidateId", "resolutionCommandId", "ratificationCommandId")):
                continue
            if (group["candidateId"] not in candidate_ids or group["personId"] is not None
                or group["identityDecisionTier"] != IDENTITY_DECISION_TIER
                or id_counts[group["id"]] != 1 or any(counts[oid] != 1 for oid in ids)
                or (visible_occurrence_ids is not None and not set(ids) <= visible_occurrence_ids)):
                continue
            candidate = connection.execute("SELECT * FROM identity_candidates WHERE id=?",
                                            (group["candidateId"],)).fetchone()
            if not candidate or not {candidate["occurrence_a_id"], candidate["occurrence_b_id"]} <= set(ids):
                continue
            if not _lineage(group, candidate):
                continue
            marks = ','.join('?' for _ in ids)
            rows = {row["id"]: row for row in connection.execute("""SELECT po.id,po.person_id,
                po.source_item_id,si.item_type FROM person_occurrences po
                JOIN source_items si ON si.id=po.source_item_id WHERE po.id IN (""" + marks + ")", ids)}
            if any(oid not in rows or rows[oid]["person_id"] is not None or
                   rows[oid]["source_item_id"] != sid or rows[oid]["item_type"] != "act"
                   for oid, sid in zip(ids, sources)):
                continue
            if not _references(group["reviewRefs"], group["candidateId"]):
                continue
            if not isinstance(group["humanReviewDebt"], dict) or not group["humanReviewDebt"]:
                continue
            safe_group = deepcopy(group)
            if "introducedCandidateIds" in safe_group:
                # A pending proposal is not a member. A scoped export can keep
                # the admitted subset while withholding a proposal whose other
                # endpoint is absent from that export's privacy-filtered view.
                safe_ids = []
                for cid in safe_group["introducedCandidateIds"]:
                    proposed = connection.execute("SELECT occurrence_a_id,occurrence_b_id,status FROM identity_candidates WHERE id=?", (cid,)).fetchone()
                    if (proposed and proposed["status"] == "unresolved" and
                        (visible_occurrence_ids is None or {proposed["occurrence_a_id"], proposed["occurrence_b_id"]} <= visible_occurrence_ids)):
                        safe_ids.append(cid)
                safe_group["introducedCandidateIds"] = safe_ids
            output.append(safe_group)
        return sorted(output, key=lambda group: group["id"])
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        return []


def _references(refs: Any, candidate_id: str, verdict: str = VERDICT) -> bool:
    if not isinstance(refs, dict) or set(refs) != {"strict", "agent", "independent", "ratification"}:
        return False
    if not isinstance(refs["independent"], list) or len(refs["independent"]) != 2:
        return False
    artifacts = [refs["strict"], refs["agent"], *refs["independent"], refs["ratification"]]
    if any(not isinstance(ref, dict) or not isinstance(ref.get("artifactPath"), str)
           or not ref["artifactPath"] or not isinstance(ref.get("artifactSha256"), str)
           or re.fullmatch(r"[0-9a-f]{64}", ref["artifactSha256"]) is None for ref in artifacts):
        return False
    if len({ref["artifactPath"] for ref in artifacts}) != 5 or len({ref["artifactSha256"] for ref in artifacts}) != 5:
        return False
    if not isinstance(refs["strict"].get("reviewId"), str) or not refs["strict"]["reviewId"]:
        return False
    independent = refs["independent"]
    if any(not isinstance(ref.get("reviewerRole"), str) or
           not isinstance(ref.get("reviewerRunId"), str) or not ref["reviewerRunId"] or
           not isinstance(ref.get("artifactId"), str) or not ref["artifactId"] or
           ref.get("verdict") != verdict or ref.get("reviewedCandidateIds") != [candidate_id]
           for ref in independent):
        return False
    return ({ref["reviewerRole"] for ref in independent} == {"source_auditor", "evidence_correlator"}
            and len({ref["reviewerRunId"] for ref in independent}) == 2)


def _lineage(group: dict[str, Any], candidate) -> bool:
    """Defend the public DTO independently of the curation validator."""
    ids = group["occurrenceIds"]
    if "lineage" not in group and "directEdges" not in group:
        return len(ids) == 2 and "introducedCandidateIds" not in group
    lineage, edges = group.get("lineage"), group.get("directEdges")
    if not isinstance(lineage, dict) or set(lineage) != {"version", "seedResolutionCommandId", "headCommandId", "headResultSha256", "predecessorCommandId", "predecessorResultSha256"}:
        return False
    if (type(lineage["version"]) is not int or lineage["version"] != 1
        or lineage["seedResolutionCommandId"] != group["resolutionCommandId"]
        or any(not isinstance(lineage[key], str) or not lineage[key] for key in lineage if key != "version")
        or any(re.fullmatch(r"[0-9a-f]{64}", lineage[key]) is None for key in ("headResultSha256", "predecessorResultSha256"))):
        return False
    if not isinstance(edges, list) or len(edges) != len(ids) - 1:
        return False
    seen_candidates, seen_pairs, seen_commands, reached = set(), set(), set(), set()
    seed = None
    for edge in edges:
        if not isinstance(edge, dict) or set(edge) != {"candidateId", "occurrenceIds", "resolutionCommandId", "ratificationCommandId", "reviewRefs", "humanReviewDebt"}:
            return False
        pair = edge["occurrenceIds"]
        if not isinstance(pair, list) or len(pair) != 2 or len(set(pair)) != 2 or not set(pair) <= set(ids):
            return False
        if any(not isinstance(edge[key], str) or not edge[key] for key in ("candidateId", "resolutionCommandId", "ratificationCommandId")):
            return False
        commands = {edge["resolutionCommandId"], edge["ratificationCommandId"]}
        if edge["candidateId"] in seen_candidates or tuple(sorted(pair)) in seen_pairs or len(commands) != 2 or seen_commands & commands:
            return False
        seen_candidates.add(edge["candidateId"])
        seen_pairs.add(tuple(sorted(pair)))
        seen_commands.update(commands)
        verdict = VERDICT if edge["candidateId"] == group["candidateId"] else "approve_agent_identity_source_group_growth"
        if not _references(edge["reviewRefs"], edge["candidateId"], verdict) or not isinstance(edge["humanReviewDebt"], dict) or not edge["humanReviewDebt"]:
            return False
        if edge["candidateId"] == group["candidateId"]:
            seed = edge
        if not reached:
            reached.update(pair)
    if seed is None or set(seed["occurrenceIds"]) != {candidate["occurrence_a_id"], candidate["occurrence_b_id"]}:
        return False
    if any(seed[key] != group[key] for key in ("resolutionCommandId", "ratificationCommandId", "reviewRefs", "humanReviewDebt")):
        return False
    for _ in edges:
        for edge in edges:
            if reached & set(edge["occurrenceIds"]):
                reached.update(edge["occurrenceIds"])
    introduced = group.get("introducedCandidateIds", [])
    return reached == set(ids) and isinstance(introduced, list) and all(isinstance(cid, str) and cid and cid not in seen_candidates for cid in introduced) and len(set(introduced)) == len(introduced)
