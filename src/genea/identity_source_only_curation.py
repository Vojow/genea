"""Strict direct identity for two historical, unlinked act occurrences.

Manifest v1 mirrors the source-pair schema, replacing sourcePair with sourceOnly
{occurrenceAId, occurrenceBId} and omitting targetPersonId. Review artifacts use
strict_source_only_* and verdict approve_agent_source_only_identity. Binding
includes the immutable unresolved candidate preimage and protected dependency
hash. Only identity candidate/decision/command/audit rows may change. Source !=
proof: an admitted group is agent approved with explicit pending human debt.
No person-based dependency revalidation or transitive groups are supported.

An optional historicalSourceAssessment {version:1, entries:[two exact entries]}
can explicitly attest unlabeled sources. Entry fields: sourceItemId, recordYear,
readingVersionId, locatorSourceItemId, locator. Years must be strictly older than
100 UTC calendar years; readings and exact locators belong to each act's upward
source lineage. The full assessment and provenance hashes enter the own review
binding and immutable command manifest. Existing nonhistorical labels still win.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import identity_source_profile_curation as base
from .command_envelope import fingerprint
from .workspace import stable_id

OPERATION = base.SOURCE_ONLY_OPERATION
RATIFICATION_OPERATION = base.SOURCE_ONLY_RATIFICATION_OPERATION
IDENTITY_DECISION_TIER = base.IDENTITY_DECISION_TIER
VERDICT = "approve_agent_source_only_identity"
IdentityOnlyCurationError = base.IdentityOnlyCurationError
IdentityOnlyCurationConflict = base.IdentityOnlyCurationConflict


def _payload(*, case_id: str, candidate_id: str, occurrence_a_id: str,
             occurrence_b_id: str, historical_source_assessment: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {"operation": OPERATION, "caseId": case_id,
            "candidate": {"id": candidate_id},
            "sourceOnly": {"occurrenceAId": occurrence_a_id, "occurrenceBId": occurrence_b_id}}
    if historical_source_assessment is not None:
        payload["historicalSourceAssessment"] = historical_source_assessment
    return payload


def _validate_historical_assessment(value: Any) -> dict[str, Any]:
    assessment = base._object(value, {"version", "entries"}, "historical source assessment")
    base._check(type(assessment["version"]) is int and assessment["version"] == 1,
                "Historical assessment version must be 1")
    entries = assessment["entries"]
    base._check(isinstance(entries, list) and len(entries) == 2,
                "Historical assessment requires two exact source entries")
    for entry in entries:
        base._object(entry, {"sourceItemId", "recordYear", "readingVersionId", "locatorSourceItemId", "locator"}, "historical assessment entry")
        for field in ("sourceItemId", "readingVersionId", "locatorSourceItemId", "locator"):
            base._text(entry[field], f"historical assessment {field}")
        base._check(type(entry["recordYear"]) is int and
                    1 <= entry["recordYear"] < datetime.now(timezone.utc).year - 100,
                    "Historical record year must be strictly older than 100 UTC years")
    base._check(len({entry["sourceItemId"] for entry in entries}) == 2,
                "Duplicate historical assessment source")
    return assessment


def _source_lineage(connection, source_id: str) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    current = source_id
    while current:
        base._check(current not in rows, "Cyclic historical source lineage")
        row = base._row(connection, "source_items", current)
        base._check(row is not None, "Missing historical source lineage")
        rows[current] = dict(row)
        current = row["parent_id"]
    return rows


def _historical_assessment_provenance(connection, assessment: Any,
                                    source_ids: list[str]) -> list[dict[str, str]]:
    assessment = _validate_historical_assessment(assessment)
    base._check({entry["sourceItemId"] for entry in assessment["entries"]} == set(source_ids)
                and len(set(source_ids)) == 2, "Historical assessment source IDs mismatch")
    provenance = []
    for entry in assessment["entries"]:
        lineage = _source_lineage(connection, entry["sourceItemId"])
        base._check(lineage[entry["sourceItemId"]]["item_type"] == "act", "Historical assessment requires act sources")
        version = base._row(connection, "reading_versions", entry["readingVersionId"])
        base._check(version is not None and version["status"] not in {"retracted", "superseded"},
                    "Missing or inactive historical reading version")
        reading = base._row(connection, "readings", version["reading_id"])
        base._check(reading is not None and reading["source_item_id"] in lineage and
                    reading["status"] not in {"retracted", "superseded"},
                    "Historical reading does not belong to source lineage")
        locator_source = lineage.get(entry["locatorSourceItemId"])
        base._check(locator_source is not None and isinstance(locator_source["locator"], str)
                    and bool(locator_source["locator"].strip()) and entry["locator"] == locator_source["locator"],
                    "Historical locator does not match exact source lineage")
        provenance.append({"sourceItemId": entry["sourceItemId"],
            "readingVersionSha256": fingerprint(dict(version)), "readingSha256": fingerprint(dict(reading)),
            "locatorSourceSha256": fingerprint(locator_source), "sourceLineageSha256": fingerprint(lineage)})
    return provenance


def source_only_review_binding(connection, *, case_id: str, candidate_id: str,
                               occurrence_a_id: str, occurrence_b_id: str,
                               historical_source_assessment: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read-only exact unordered pair binding, stable across permitted writes."""
    for value in (case_id, candidate_id, occurrence_a_id, occurrence_b_id):
        base._text(value, "binding ID")
    base._check(occurrence_a_id != occurrence_b_id, "Endpoints must differ")
    ids = sorted((occurrence_a_id, occurrence_b_id))
    p = _payload(case_id=case_id, candidate_id=candidate_id,
                 occurrence_a_id=ids[0], occurrence_b_id=ids[1],
                 historical_source_assessment=historical_source_assessment)
    state = base._domain_state(connection, p)
    candidate = state["candidate"]
    base._check(candidate is not None, "Missing bound candidate")
    candidate = {**candidate, "status": "unresolved"}
    sources = []
    for occurrence in state["occurrences"]:
        base._check(occurrence is not None, "Missing bound occurrence")
        source = base._row(connection, "source_items", occurrence["source_item_id"])
        base._check(source is not None, "Missing bound source")
        sources.append(dict(source))
    frozen = {**state, "candidate": candidate, "decisions": []}
    binding = {"caseId": case_id, "candidateId": candidate_id,
            "occurrenceIds": ids, "sourceItemIds": [row["id"] for row in sources],
            "sourceSha256": [fingerprint(row) for row in sources],
            "occurrenceSha256": [fingerprint(row) for row in state["occurrences"]],
            "candidateBeforeSha256": fingerprint(candidate),
            "dependencyStateSha256": fingerprint(frozen)}
    if historical_source_assessment is not None:
        binding["historicalSourceAssessment"] = deepcopy(historical_source_assessment)
        binding["historicalSourceAssessmentSha256"] = fingerprint(historical_source_assessment)
        binding["historicalSourceProvenance"] = _historical_assessment_provenance(connection,
            historical_source_assessment, [source["id"] for source in sources])
    return binding


def _source_only_guards(connection, p: dict[str, Any], state: dict[str, Any], *,
                        applied: bool = False) -> list[str]:
    pair = p["sourceOnly"]
    ids = {pair["occurrenceAId"], pair["occurrenceBId"]}
    blockers = []
    candidate = state["candidate"]
    if not candidate or candidate["status"] != ("resolved_same" if applied else "unresolved"):
        blockers.append("candidate_status_mismatch")
    if not candidate or {candidate["occurrence_a_id"], candidate["occurrence_b_id"]} != ids:
        blockers.append("candidate_endpoints_mismatch")
    if any({row["occurrence_a_id"], row["occurrence_b_id"]} == ids for row in state["sharedCandidates"]):
        blockers.append("ambiguous_duplicate_pair")
    if any(row["decision"] == "same" for row in state["sharedDecisions"]) or any(
        row["status"] == "resolved_same" for row in state["sharedCandidates"]
    ):
        blockers.append("overlapping_identity_group")
    if not base._row(connection, "research_cases", p["caseId"]):
        blockers.append("missing_case")
    sources = []
    for occurrence in state["occurrences"]:
        if not occurrence or occurrence["person_id"] is not None:
            blockers.append("source_only_requires_unlinked_endpoints")
        if occurrence and occurrence["verification_status"] in {"retracted", "superseded"}:
            blockers.append("inactive_occurrence")
        source = base._row(connection, "source_items", occurrence["source_item_id"]) if occurrence else None
        if not source or source["item_type"] != "act":
            blockers.append("source_type_mismatch")
        if source:
            sources.append(dict(source))
            if source["verification_status"] in {"retracted", "superseded"}:
                blockers.append("inactive_source")
    if len(sources) == 2 and sources[0]["id"] == sources[1]["id"]:
        blockers.append("distinct_act_sources_required")
    privacy = state["dependencies"].get("privacy_labels", [])
    assessed_sources: set[str] = set()
    if "historicalSourceAssessment" in p:
        try:
            _historical_assessment_provenance(connection, p["historicalSourceAssessment"],
                                            [source["id"] for source in sources])
            assessed_sources = {entry["sourceItemId"] for entry in p["historicalSourceAssessment"]["entries"]}
        except (ValueError, TypeError, KeyError):
            blockers.append("historical_source_assessment_invalid")
    for source in sources:
        labels = [row for row in privacy if row["object_type"] == "source_item" and row["object_id"] == source["id"]]
        if ((not labels and source["id"] not in assessed_sources) or len(labels) > 1
                or (labels and labels[0]["classification"] not in {"public_historical", "deceased"})):
            blockers.append("source_privacy_not_historical")
    if any(row["classification"] not in {"public_historical", "deceased"} for row in privacy):
        blockers.append("private_or_living_dependency")
    for table in ("private_fields", "private_attachments", "private_relationships", "family_notes"):
        if state["dependencies"].get(table):
            blockers.append("private_evidence_dependency")
    # Command membership comes from backing IDs, never mutable result JSON.
    endpoint_tuple = tuple(sorted(ids))
    commands = base._rows(connection, """SELECT DISTINCT cc.command_id,cc.object_id
        FROM curation_commands cc JOIN identity_candidates ic
          ON ic.id=cc.object_id OR ic.id IN
             (SELECT candidate_id FROM identity_decisions WHERE command_id=cc.command_id)
        WHERE cc.command_type IN (?,?,?,?,?,?) AND ic.id<>?
          AND (ic.occurrence_a_id IN (?,?) OR ic.occurrence_b_id IN (?,?))""",
        (OPERATION, RATIFICATION_OPERATION, base.OPERATION, base.RATIFICATION_OPERATION,
         base.PAIR_OPERATION, base.PAIR_RATIFICATION_OPERATION, p["candidate"]["id"],
         *endpoint_tuple, *endpoint_tuple))
    if commands:
        blockers.append("overlapping_typed_identity_chain")
    if not applied and state["decisions"]:
        blockers.append("candidate_already_decided")
    return sorted(set(blockers))


def identity_source_only_resolution_preview(root: Path, manifest: Path) -> dict[str, Any]:
    preview = base.identity_source_profile_resolution_preview(root, manifest, operation=OPERATION)
    preview["sourceIdentityGroupId"] = stable_id("source_identity", preview["candidateId"], *sorted(preview["occurrenceIds"]))
    return preview


def apply_identity_source_only_resolution(root: Path, manifest: Path, *,
                                          expected_fingerprint: str, command_id: str) -> dict[str, Any]:
    return base.apply_identity_source_profile_resolution(root, manifest,
        expected_fingerprint=expected_fingerprint, command_id=command_id, operation=OPERATION)


def identity_source_only_agent_ratification_preview(root: Path, manifest: Path) -> dict[str, Any]:
    return base.identity_source_profile_agent_ratification_preview(root, manifest, operation=RATIFICATION_OPERATION)


def apply_identity_source_only_agent_ratification(root: Path, manifest: Path, *,
                                                  expected_fingerprint: str, command_id: str) -> dict[str, Any]:
    return base.apply_identity_source_profile_agent_ratification(root, manifest,
        expected_fingerprint=expected_fingerprint, command_id=command_id, operation=RATIFICATION_OPERATION)


def source_only_typed_ids(connection) -> tuple[set[str], set[str]]:
    """Reserve exact backing IDs even for corrupt or unratified commands."""
    rows = base._rows(connection, """SELECT DISTINCT ic.id,ic.occurrence_a_id,ic.occurrence_b_id
        FROM curation_commands cc JOIN identity_candidates ic
          ON ic.id=cc.object_id OR ic.id IN
             (SELECT candidate_id FROM identity_decisions WHERE command_id=cc.command_id)
        WHERE cc.command_type IN (?,?)""", (OPERATION, RATIFICATION_OPERATION))
    # New direct decisions retain their own backing candidate IDs. Reserving
    # these IDs is independent of (and weaker than) lineage admission.
    from .identity_source_group_curation import GROWTH_OPERATION
    rows += base._rows(connection, """SELECT DISTINCT c.id,c.occurrence_a_id,c.occurrence_b_id
        FROM identity_decisions d JOIN identity_candidates c ON c.id=d.candidate_id
        JOIN curation_commands cc ON cc.command_id=d.command_id WHERE cc.command_type=?""", (GROWTH_OPERATION,))
    return ({row["id"] for row in rows},
            {row[key] for row in rows for key in ("occurrence_a_id", "occurrence_b_id")})


def validated_source_only_identity_chains(root: Path, connection, *, context=None) -> dict[str, Any]:
    """Return direct groups only after the complete own chain is admitted."""
    if context is not None:
        base._check_projection_context(root, connection, context)
    candidates, occurrences = source_only_typed_ids(connection)
    output = {"groups": [], "typedCandidateIds": sorted(candidates),
              "typedOccurrenceIds": sorted(occurrences)}
    validation_context = context if context is not None else base._ValidationContext(root)
    for command in base._rows(connection,
            "SELECT * FROM curation_commands WHERE command_type=? ORDER BY command_id",
            (RATIFICATION_OPERATION,)):
        try:
            prior = base._validate_ratification(root, connection, command["command_id"], context=validation_context)
            base._check(prior["operation"] == OPERATION and prior["personId"] is None,
                        "Wrong source-only chain topology")
            base._check("candidateBefore" in prior, "Source-only candidate preimage required")
            ratification = base._command(connection, command["command_id"])[1]
            ids = sorted(prior["occurrenceIds"])
            gate = prior["reviewGate"]
            output["groups"].append({
                "id": prior["sourceIdentityGroupId"],
                "candidateId": prior["candidateId"], "occurrenceIds": ids,
                "sourceItemIds": gate["binding"]["sourceItemIds"], "personId": None,
                "identityDecisionTier": IDENTITY_DECISION_TIER,
                "resolutionCommandId": prior["commandId"],
                "ratificationCommandId": command["command_id"],
                "reviewRefs": {
                    "strict": {"artifactPath": gate["sourceReviewPath"], "artifactSha256": gate["sourceReviewSha256"], "reviewId": gate["sourceReviewId"]},
                    "agent": {"artifactPath": gate["artifactPath"], "artifactSha256": gate["artifactSha256"]},
                    "independent": deepcopy(gate["independentReviewRefs"]),
                    "ratification": {"artifactPath": ratification["ratificationReviewArtifactPath"], "artifactSha256": ratification["ratificationReviewSha256"]}},
                "humanReviewDebt": deepcopy(gate["debt"]),
            })
        except (ValueError, KeyError, TypeError, OSError, StopIteration):
            continue
    from .identity_source_group_curation import validated_source_identity_groups
    successors = validated_source_identity_groups(root, connection, context=context)
    claimed = set(successors["claimedGroupIds"])
    output["groups"] = [group for group in output["groups"] if group["id"] not in claimed] + successors["groups"]
    # Defensive exact disjointness, without transitive graph closure.
    counts = Counter(oid for group in output["groups"] for oid in group["occurrenceIds"])
    output["groups"] = sorted((group for group in output["groups"]
        if all(counts[oid] == 1 for oid in group["occurrenceIds"])), key=lambda group: group["id"])
    return output


__all__ = ["OPERATION", "RATIFICATION_OPERATION", "IDENTITY_DECISION_TIER", "VERDICT",
    "IdentityOnlyCurationError", "IdentityOnlyCurationConflict", "source_only_review_binding",
    "identity_source_only_resolution_preview", "apply_identity_source_only_resolution",
    "identity_source_only_agent_ratification_preview", "apply_identity_source_only_agent_ratification",
    "validated_source_only_identity_chains", "source_only_typed_ids"]
