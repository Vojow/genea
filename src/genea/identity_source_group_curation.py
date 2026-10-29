"""Explicit append-only successors of an admitted source-only identity pair.

The seed is never repaired from an after-state. Its original post-state hash
proves the frozen foundation; each successor proves an exact domain delta and
one predecessor. Introduction creates one fully captured unresolved row. Growth
decides one directly reviewed pair; a separate ratification admits its new
member. No operation assigns a canonical person or promotes facts/kinship.

Manifests have common fields version=1, operation, operationId, caseId,
groupId, predecessor {commandId,commandSha256,resultSha256}, beforeState (the
capture helper result), actor, rationale, privacyAcknowledged=true, agentReview
{artifactPath,expectedSha256}. Introduction adds candidate (the complete seven
column unresolved row); growth and ratification add candidateId. Review schemas
mirror the existing strict source-only schemas, with source_group artifact
families, operation-specific verdict approve_agent_<operation>, and a full
group binding. Ratification uses the same fresh four-artifact review gate.
All artifacts live beneath research/records/reassessments, as in legacy gates.
Candidate basis_json must explicitly retain basis, created_by, source_item_ids,
support, against, human_review_debt, and score_semantics set to
research_priority_not_probability. Optional historicalSourceAssessment on an
introduction preserves the legacy exact reading/locator provenance mechanism.
"""
from __future__ import annotations

import json
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any

from . import identity_source_profile_curation as base
from . import identity_source_only_curation as legacy
from .command_envelope import CommandEnvelopeConflict, fingerprint, record_command, replay_command
from .workspace import stable_id, utc_now

INTRODUCTION_OPERATION = "identity_source_group_candidate_introduction"
GROWTH_OPERATION = "identity_source_group_growth"
RATIFICATION_OPERATION = "identity_source_group_agent_ratification"
OPERATIONS = {INTRODUCTION_OPERATION, GROWTH_OPERATION, RATIFICATION_OPERATION}
COMMON = {"version", "operation", "operationId", "caseId", "groupId", "predecessor",
          "beforeState", "actor", "rationale", "privacyAcknowledged", "agentReview"}
CANDIDATE_FIELDS = {"id", "occurrence_a_id", "occurrence_b_id", "score", "basis_json", "status", "created_at"}
SNAPSHOT_FIELDS = {"version", "groupId", "caseId", "seedResolutionCommandId", "seedRatificationCommandId",
    "seedState", "group", "candidates", "scopeStates", "introductions", "historicalAssessments",
    "directEdges", "pendingGrowth", "head"}
IdentitySourceGroupError = base.IdentityOnlyCurationError
IdentitySourceGroupConflict = base.IdentityOnlyCurationConflict


def _check(ok: bool, message: str) -> None:
    base._check(ok, message)


def _manifest(value: Any) -> dict[str, Any]:
    _check(isinstance(value, dict) and value.get("operation") in OPERATIONS, "unsupported_group_operation")
    intro = value["operation"] == INTRODUCTION_OPERATION
    fields = COMMON | ({"candidate"} if intro else {"candidateId"})
    if "historicalSourceAssessment" in value:
        _check(intro, "historical_assessment_requires_introduction")
        fields = fields | {"historicalSourceAssessment"}
        legacy._validate_historical_assessment(value["historicalSourceAssessment"])
    base._object(value, fields, "group manifest")
    _check(type(value["version"]) is int and value["version"] == 1, "invalid_group_version")
    for key in ("operationId", "caseId", "groupId", "actor", "rationale"):
        base._text(value[key], key)
    _check(len(value["rationale"]) >= 20, "group_rationale_too_short")
    _check(value["privacyAcknowledged"] is True, "fresh_privacy_acknowledgement_required")
    base._object(value["predecessor"], {"commandId", "commandSha256", "resultSha256"}, "predecessor")
    base._object(value["agentReview"], {"artifactPath", "expectedSha256"}, "agentReview")
    if intro:
        row = base._object(value["candidate"], CANDIDATE_FIELDS, "introduced candidate")
        for key in ("id", "occurrence_a_id", "occurrence_b_id", "basis_json", "created_at"):
            base._text(row[key], key)
        _check(row["status"] == "unresolved", "introduction_requires_unresolved")
        _check(row["score"] is None or type(row["score"]) in {int, float}, "invalid_candidate_score")
        basis = json.loads(row["basis_json"])
        _check(isinstance(basis, dict), "candidate_provenance_object_required")
        _check(basis.get("score_semantics") == "research_priority_not_probability" and "scoreSemantics" not in basis,
               "candidate_score_semantics_required")
        for key in ("basis", "created_by"):
            base._text(basis.get(key), "candidate provenance " + key)
        for key in ("support", "against", "human_review_debt", "source_item_ids"):
            values = basis.get(key)
            _check(isinstance(values, list) and all(isinstance(item, str) and bool(item.strip()) for item in values), "candidate_provenance_" + key + "_required")
        _check(bool(basis["human_review_debt"]) and len(basis["source_item_ids"]) == 2 and len(set(basis["source_item_ids"])) == 2,
               "candidate_debt_and_exact_sources_required")
    else:
        base._text(value["candidateId"], "candidateId")
    return value


def _pair(case_id: str, candidate: dict[str, Any], assessment: dict[str, Any] | None = None) -> dict[str, Any]:
    return legacy._payload(case_id=case_id, candidate_id=candidate["id"],
        occurrence_a_id=candidate["occurrence_a_id"], occurrence_b_id=candidate["occurrence_b_id"],
        historical_source_assessment=assessment)


def _candidate_id(p: dict[str, Any]) -> str:
    return p["candidate"]["id"] if p["operation"] == INTRODUCTION_OPERATION else p["candidateId"]


def _states(connection, case_id: str, candidates: dict[str, Any]) -> dict[str, Any]:
    return {cid: base._domain_state(connection, _pair(case_id, candidate))
            for cid, candidate in sorted(candidates.items())}


def _command_reference(connection, command_id: str) -> dict[str, str]:
    command, result = base._command(connection, command_id)
    return {"commandId": command_id, "commandSha256": base._command_sha(command),
            "resultSha256": fingerprint(result)}


def _edge(prior: dict[str, Any], ratification_id: str, refs: dict[str, Any]) -> dict[str, Any]:
    return {"candidateId": prior["candidateId"], "occurrenceIds": sorted(prior["occurrenceIds"]),
            "resolutionCommandId": prior["commandId"], "ratificationCommandId": ratification_id,
            "reviewRefs": deepcopy(refs), "humanReviewDebt": deepcopy(prior["reviewGate"]["debt"])}


def _legacy_group(prior: dict[str, Any], ratification_id: str, ratification: dict[str, Any]) -> dict[str, Any]:
    gate = prior["reviewGate"]
    return {"id": prior["sourceIdentityGroupId"], "candidateId": prior["candidateId"],
        "occurrenceIds": sorted(prior["occurrenceIds"]),
        "sourceItemIds": gate["binding"]["sourceItemIds"], "personId": None,
        "identityDecisionTier": legacy.IDENTITY_DECISION_TIER,
        "resolutionCommandId": prior["commandId"], "ratificationCommandId": ratification_id,
        "reviewRefs": {"strict": {"artifactPath": gate["sourceReviewPath"], "artifactSha256": gate["sourceReviewSha256"], "reviewId": gate["sourceReviewId"]},
            "agent": {"artifactPath": gate["artifactPath"], "artifactSha256": gate["artifactSha256"]},
            "independent": deepcopy(gate["independentReviewRefs"]),
            "ratification": {"artifactPath": ratification["ratificationReviewArtifactPath"], "artifactSha256": ratification["ratificationReviewSha256"]}},
        "humanReviewDebt": deepcopy(gate["debt"])}


def _seed_capture(root: Path, connection, group_id: str, *, context=None) -> dict[str, Any]:
    matches = []
    for row in base._rows(connection, "SELECT command_id FROM curation_commands WHERE command_type=?", (legacy.RATIFICATION_OPERATION,)):
        try:
            prior = base._validate_ratification(root, connection, row["command_id"], context=context)
            if prior["sourceIdentityGroupId"] == group_id:
                matches.append((prior, row["command_id"]))
        except (ValueError, KeyError, TypeError, OSError):
            continue
    _check(len(matches) == 1, "admitted_seed_required")
    prior, rat_id = matches[0]
    p = prior["manifest"]
    state = base._domain_state(connection, p)
    _check(fingerprint(state) == prior["postStateSha256"], "seed_state_drift")
    rat = base._command(connection, rat_id)[1]
    group = _legacy_group(prior, rat_id, rat)
    candidate = state["candidate"]
    return {"version": 1, "groupId": group_id, "caseId": p["caseId"],
        "seedResolutionCommandId": prior["commandId"], "seedRatificationCommandId": rat_id,
        "seedState": state, "group": group, "candidates": {candidate["id"]: candidate},
        "scopeStates": {candidate["id"]: state}, "introductions": [],
        "historicalAssessments": {candidate["id"]: deepcopy(p["historicalSourceAssessment"])} if "historicalSourceAssessment" in p else {},
        "directEdges": [_edge(prior, rat_id, group["reviewRefs"])], "pendingGrowth": None,
        "head": _command_reference(connection, rat_id)}


def _seed_validate(root: Path, connection, snapshot: dict[str, Any], *, context=None) -> None:
    """Validate the immutable legacy basis without blessing a current after-state."""
    base._object(snapshot, SNAPSHOT_FIELDS, "source group before state")
    _check(type(snapshot["version"]) is int and snapshot["version"] == 1, "invalid_group_snapshot_version")
    command, prior = base._command(connection, snapshot["seedResolutionCommandId"])
    p = prior["manifest"]
    base._validate_manifest_value(p)
    _check(p["operation"] == legacy.OPERATION and command["command_type"] == legacy.OPERATION,
           "seed_operation_mismatch")
    _check(snapshot["caseId"] == p["caseId"], "seed_case_mismatch")
    state = snapshot["seedState"]
    _check(fingerprint(state) == prior["postStateSha256"], "unproven_seed_preimage")
    before = base._candidate_preimage(prior, state["candidate"])
    frozen = {**state, "candidate": before, "decisions": []}
    gate = base._review_gate(root, p, prior["reviewGate"]["binding"], context=context)
    _check(gate == prior["reviewGate"] and gate["binding"]["dependencyStateSha256"] == fingerprint(frozen),
           "seed_review_preimage_mismatch")
    _check(prior["inputFingerprint"] == fingerprint({"manifest": p, "state": frozen, "reviewGate": gate}), "seed_input_mismatch")
    _check(command["input_fingerprint"] == prior["inputFingerprint"] and command["request_fingerprint"] == base._request(p, prior["inputFingerprint"])
           and command["object_id"] == prior["candidateId"] == p["candidate"]["id"]
           and command["actor"] == p["decidedBy"] and command["rationale"] == p["rationale"]
           and prior["manifestFingerprint"] == fingerprint(p), "seed_envelope_mismatch")
    _check(prior["sourceIdentityGroupId"] == snapshot["groupId"] == stable_id("source_identity", prior["candidateId"], *sorted(prior["occurrenceIds"])), "seed_group_mismatch")
    base._validate_audit(connection, command, prior, context=context)
    base._ratification_core(root, connection, snapshot["seedRatificationCommandId"], p, prior, context=context)
    _check(len(base._rows(connection, "SELECT command_id FROM curation_commands WHERE command_type=? AND object_id=?", (legacy.OPERATION, prior["candidateId"]))) == 1, "seed_fork")


def _lineage_rows(connection, group_id: str) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    rows = base._rows(connection, "SELECT * FROM curation_commands WHERE command_type IN (?,?,?) AND object_id=? ORDER BY committed_at,command_id", (*sorted(OPERATIONS), group_id))
    return [(row, json.loads(row["result_json"])) for row in rows]


def _audit_validate(connection, command: dict[str, Any], result: dict[str, Any]) -> None:
    rows = base._rows(connection, "SELECT * FROM audit_log WHERE action=? AND object_type='source_identity_group' AND object_id=?", (command["command_type"], command["object_id"]))
    rows = [row for row in rows if json.loads(row["after_json"]).get("commandId") == command["command_id"]]
    _check(len(rows) == 1 and json.loads(rows[0]["after_json"]) == result
           and json.loads(rows[0]["before_json"]) == {"inputFingerprint": command["input_fingerprint"]}
           and rows[0]["actor"] == command["actor"] and rows[0]["rationale"] == command["rationale"], "group_audit_mismatch")


def _review_gate(root: Path, p: dict[str, Any], binding: dict[str, Any], *, context=None) -> dict[str, Any]:
    """Fresh strict and independent exact-pair plus whole-group authorization."""
    verdict = "approve_agent_" + p["operation"]
    agent, agent_path, agent_sha = base._artifact(root, p["agentReview"], "group agent decision", context=context)
    fields = {"version", "artifactType", "reviewId", "sourceReviewId", "sourceReviewSha256", "caseId", "candidateId", "binding", "decision", "decidedByKind", "identityDecisionTier", "decidedBy", "decidedAt", "rationale", "debt", "independentReviewRefs"}
    base._object(agent, fields, "group agent decision")
    expected = {"version": 1, "artifactType": "strict_source_group_agent_decision", "caseId": p["caseId"],
        "candidateId": _candidate_id(p), "binding": binding, "decision": verdict,
        "decidedByKind": "agent", "identityDecisionTier": legacy.IDENTITY_DECISION_TIER,
        "decidedBy": p["actor"], "rationale": p["rationale"]}
    _check(type(agent["version"]) is int and all(agent[key] == val for key, val in expected.items()), "group_agent_binding_mismatch")
    base._iso_timestamp(agent["decidedAt"], "decidedAt")
    base._text(agent["reviewId"], "reviewId")
    base._text(agent["sourceReviewId"], "sourceReviewId")
    base._debt(agent["debt"])
    if context is None:
        records = []
        for path in (root / "research/records/reassessments").rglob("*.json"):
            try:
                records.append((path, json.loads(path.read_text(encoding="utf-8"))))
            except (ValueError, OSError):
                continue
    else:
        _, records = context.catalog(root)
    catalog = [(path, artifact) for path, artifact in records
               if isinstance(artifact, dict)
               and artifact.get("artifactType") == "strict_source_group_identity_review"
               and artifact.get("caseId") == p["caseId"]]
    matches = [(path, artifact) for path, artifact in catalog if artifact.get("reviewId") == agent["sourceReviewId"]]
    _check(len(matches) == 1 and not any(a.get("supersedesReviewId") == agent["sourceReviewId"] for _, a in catalog), "group_strict_missing_duplicate_or_superseded")
    path, strict = matches[0]
    strict_path = path.resolve().relative_to(root.resolve()).as_posix()
    strict, _, _ = base._artifact(root, {"artifactPath": strict_path,
        "expectedSha256": agent["sourceReviewSha256"]}, "group strict", context=context)
    base._object(strict, base.STRICT_FIELDS, "group strict")
    _check(type(strict["version"]) is int and strict["version"] == 1 and strict["status"] == "terminal" and strict["operational"] is False, "group_strict_not_terminal")
    guardrails = base._object(strict["guardrails"], {"requiredApplyMechanism", "directOnly", "transitiveMerge"}, "group guardrails")
    _check(guardrails["requiredApplyMechanism"] == p["operation"] and guardrails["directOnly"] is True
           and guardrails["transitiveMerge"] is False, "group_guardrails_mismatch")
    entry = {"candidateId": _candidate_id(p), "binding": binding, "recommendation": verdict,
             "humanDecision": "pending", "rationale": p["rationale"], "debt": agent["debt"]}
    _check(strict["entries"] == [entry], "group_strict_exact_pair_required")
    refs = agent["independentReviewRefs"]
    _check(isinstance(refs, list) and len(refs) == 2 and refs == strict["independentReviewRefs"], "group_independent_reviews_required")
    for ref in refs:
        base._object(ref, base.REF_FIELDS, "group independent ref")
    _check({r["reviewerRole"] for r in refs} == {"source_auditor", "evidence_correlator"}
           and len({r["reviewerRunId"] for r in refs}) == 2, "group_independent_roles_runs_required")
    paths, hashes = {agent_path, strict_path}, {agent_sha, agent["sourceReviewSha256"]}
    _check(len(paths) == len(hashes) == 2, "group_artifacts_must_differ")
    for ref in refs:
        artifact, relative, sha = base._artifact(root, ref, "group independent", independent=True, context=context)
        base._object(artifact, base.INDEPENDENT_FIELDS, "group independent")
        for key in ("artifactId", "reviewerRunId"):
            base._text(ref[key], key)
        role_type = "source_audit" if ref["reviewerRole"] == "source_auditor" else "evidence_correlation"
        _check(type(artifact["version"]) is int and artifact["version"] == 1 and artifact["artifactType"] == "strict_source_group_" + role_type
               and artifact["caseId"] == p["caseId"] and artifact["artifactId"] == ref["artifactId"]
               and artifact["reviewerRole"] == ref["reviewerRole"] and artifact["reviewerRunId"] == ref["reviewerRunId"]
               and artifact["sourceReviewId"] == agent["sourceReviewId"] and artifact["status"] == "terminal" and artifact["operational"] is False
               and ref["verdict"] == verdict and ref["reviewedCandidateIds"] == [_candidate_id(p)], "group_independent_binding_mismatch")
        _check(isinstance(artifact["results"], list) and len(artifact["results"]) == 1, "group_independent_exact_pair_required")
        result = base._object(artifact["results"][0], base.RESULT_FIELDS, "group independent result")
        _check(result["candidateId"] == _candidate_id(p) and result["binding"] == binding and result["verdict"] == verdict
               and result["debt"] == agent["debt"] and len(base._text(result["rationale"], "rationale")) >= 20, "group_independent_result_mismatch")
        _check(relative not in paths and sha not in hashes, "group_artifacts_must_differ")
        paths.add(relative)
        hashes.add(sha)
    return {"artifactPath": agent_path, "artifactSha256": agent_sha, "sourceReviewPath": strict_path,
        "sourceReviewSha256": agent["sourceReviewSha256"], "sourceReviewId": agent["sourceReviewId"],
        "independentReviewRefs": deepcopy(refs), "debt": deepcopy(agent["debt"]), "binding": binding}


def _current_matches(connection, snapshot: dict[str, Any]) -> None:
    _check(_states(connection, snapshot["caseId"], snapshot["candidates"]) == snapshot["scopeStates"], "group_dependency_drift")
    _snapshot_guards(connection, snapshot)


def _snapshot_guards(connection, snapshot: dict[str, Any]) -> None:
    own = set(snapshot["candidates"])
    for cid, candidate in snapshot["candidates"].items():
        state = deepcopy(snapshot["scopeStates"][cid])
        state["sharedCandidates"] = [row for row in state["sharedCandidates"] if row["id"] not in own]
        state["sharedDecisions"] = [row for row in state["sharedDecisions"] if row["candidate_id"] not in own]
        blockers = legacy._source_only_guards(connection, _pair(snapshot["caseId"], candidate, snapshot["historicalAssessments"].get(cid)), state,
            applied=candidate["status"] == "resolved_same")
        _check(not [b for b in blockers if b != "overlapping_typed_identity_chain"], "group_domain_guards_failed:" + ";".join(blockers))
    # Only the immutable original seed may have a legacy typed command touching
    # these endpoints. A later cross-topology command is evidence of conflict.
    ids = sorted({oid for state in snapshot["scopeStates"].values() for row in state["occurrences"] if row for oid in [row["id"]]})
    marks = ",".join("?" for _ in ids)
    typed = base._rows(connection, f"""SELECT DISTINCT cc.command_id FROM curation_commands cc
        JOIN identity_candidates c ON c.id=cc.object_id OR c.id IN (SELECT candidate_id FROM identity_decisions WHERE command_id=cc.command_id)
        WHERE cc.command_type IN (?,?,?,?,?,?) AND (c.occurrence_a_id IN ({marks}) OR c.occurrence_b_id IN ({marks}))""",
        (legacy.OPERATION, legacy.RATIFICATION_OPERATION, base.OPERATION, base.RATIFICATION_OPERATION,
         base.PAIR_OPERATION, base.PAIR_RATIFICATION_OPERATION, *ids, *ids))
    _check({row["command_id"] for row in typed} <= {snapshot["seedResolutionCommandId"], snapshot["seedRatificationCommandId"]}, "cross_group_or_mixed_tier_chain")


def _capture(root: Path, connection, group_id: str, *, context=None) -> dict[str, Any]:
    rows = _lineage_rows(connection, group_id)
    if not rows:
        return _seed_capture(root, connection, group_id, context=context)
    children: dict[str, list] = {}
    for command, result in rows:
        _check(isinstance(result, dict) and isinstance(result.get("manifest"), dict), "invalid_group_receipt")
        parent_id = result["manifest"]["predecessor"]["commandId"]
        children.setdefault(parent_id, []).append((command, result))
    roots = [(c, r) for c, r in rows if r["manifest"]["predecessor"]["commandId"] not in {c["command_id"] for c, _ in rows}]
    _check(len(roots) == 1 and all(len(items) == 1 for items in children.values()), "forked_or_cyclic_group_lineage")
    snapshot = deepcopy(roots[0][1]["manifest"]["beforeState"])
    _seed_validate(root, connection, snapshot, context=context)
    seed_prior = base._command(connection, snapshot["seedResolutionCommandId"])[1]
    _check(snapshot["scopeStates"] == {seed_prior["candidateId"]: snapshot["seedState"]}
           and snapshot["candidates"] == {seed_prior["candidateId"]: snapshot["seedState"]["candidate"]}
           and snapshot["introductions"] == [] and snapshot["pendingGrowth"] is None,
           "invalid_initial_group_snapshot")
    seed_assessment = {seed_prior["candidateId"]: seed_prior["manifest"]["historicalSourceAssessment"]} if "historicalSourceAssessment" in seed_prior["manifest"] else {}
    _check(snapshot["historicalAssessments"] == seed_assessment, "seed_historical_assessment_mismatch")
    rat = base._command(connection, snapshot["seedRatificationCommandId"])[1]
    seed_group = _legacy_group(seed_prior, snapshot["seedRatificationCommandId"], rat)
    _check(snapshot["group"] == seed_group and snapshot["directEdges"] == [_edge(seed_prior, snapshot["seedRatificationCommandId"], seed_group["reviewRefs"])], "invalid_seed_group_snapshot")
    _check(snapshot["head"] == _command_reference(connection, snapshot["seedRatificationCommandId"]), "seed_head_mismatch")
    visited = set()
    while snapshot["head"]["commandId"] in children:
        command, result = children[snapshot["head"]["commandId"]][0]
        _check(command["command_id"] not in visited, "cyclic_group_lineage")
        visited.add(command["command_id"])
        p = _manifest(result["manifest"])
        _check(p["beforeState"] == snapshot and p["predecessor"] == snapshot["head"], "group_predecessor_snapshot_mismatch")
        _check(p["groupId"] == group_id == command["object_id"] and p["caseId"] == snapshot["caseId"], "group_scope_mismatch")
        binding = _binding(p, result["inputState"])
        gate = _review_gate(root, p, binding, context=context)
        _check(gate == result["reviewGate"], "group_review_drift")
        expected_fp = fingerprint({"manifest": p, "inputState": result["inputState"], "reviewGate": gate})
        _check(command["command_type"] == p["operation"] == result["operation"] and command["object_type"] == "source_identity_group"
               and command["actor"] == p["actor"] and command["rationale"] == p["rationale"]
               and result["manifestFingerprint"] == fingerprint(p) and result["commandId"] == command["command_id"]
               and result["inputFingerprint"] == command["input_fingerprint"] == expected_fp
               and command["request_fingerprint"] == base._request(p, expected_fp), "group_envelope_mismatch")
        _validate_delta(p, result)
        _audit_validate(connection, command, result)
        snapshot = deepcopy(result["afterState"])
        # The receipt cannot include its own hash: its successor carries it.
        snapshot["head"] = _command_reference(connection, command["command_id"])
    _check(len(visited) == len(rows), "disconnected_group_lineage")
    _current_matches(connection, snapshot)
    return snapshot


def capture_source_identity_group_before_state(root: Path, connection, group_id: str) -> dict[str, Any]:
    """Read-only full admitted state. Capture before adding any candidate row."""
    return deepcopy(_capture(root, connection, group_id))


def _eligible(connection, p: dict[str, Any], candidate: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    ids = {candidate["occurrence_a_id"], candidate["occurrence_b_id"]}
    members = set(snapshot["group"]["occurrenceIds"])
    _check(len(ids) == 2 and len(ids & members) == 1, "growth_requires_one_member_one_new_occurrence")
    _check(not any(ids == {row["occurrence_a_id"], row["occurrence_b_id"]} for cid, row in snapshot["candidates"].items() if cid != candidate["id"]), "duplicate_or_reversed_direct_pair")
    assessment = p.get("historicalSourceAssessment", snapshot["historicalAssessments"].get(candidate["id"]))
    pair_manifest = _pair(p["caseId"], candidate, assessment)
    state = base._domain_state(connection, pair_manifest)
    basis = json.loads(candidate["basis_json"])
    _check(set(basis["source_item_ids"]) == {row["source_item_id"] for row in state["occurrences"] if row}, "candidate_source_provenance_mismatch")
    # Reuse all evidence/privacy/topology guards; only explicitly known own
    # direct edges may overlap. No generic filtering of arbitrary added rows.
    filtered = deepcopy(state)
    own = set(snapshot["candidates"])
    filtered["sharedCandidates"] = [r for r in state["sharedCandidates"] if r["id"] not in own]
    filtered["sharedDecisions"] = [r for r in state["sharedDecisions"] if r["candidate_id"] not in own]
    filtered["candidate"] = candidate
    blockers = legacy._source_only_guards(connection, pair_manifest, filtered)
    # Existing seed's typed chain is authorized by the exact predecessor.
    blockers = [b for b in blockers if b != "overlapping_typed_identity_chain"]
    _check(not blockers, ";".join(blockers))
    new_id = next(iter(ids - members))
    new_occurrence = next(row for row in state["occurrences"] if row and row["id"] == new_id)
    _check(new_occurrence["source_item_id"] not in snapshot["group"]["sourceItemIds"], "distinct_group_act_sources_required")
    existing = base._rows(connection, """SELECT DISTINCT cc.command_type FROM curation_commands cc
        JOIN identity_candidates c ON c.id=cc.object_id OR c.id IN (SELECT candidate_id FROM identity_decisions WHERE command_id=cc.command_id)
        WHERE cc.command_type IN (?,?,?,?,?,?) AND (c.occurrence_a_id=? OR c.occurrence_b_id=?)""",
        (legacy.OPERATION, legacy.RATIFICATION_OPERATION, base.OPERATION, base.RATIFICATION_OPERATION,
         base.PAIR_OPERATION, base.PAIR_RATIFICATION_OPERATION, new_id, new_id))
    _check(not existing, "cross_group_or_mixed_tier_occurrence")
    # Group successors use group object IDs; reserve their named candidates too.
    for command in base._rows(connection, "SELECT object_id,result_json FROM curation_commands WHERE command_type IN (?,?,?)", (*sorted(OPERATIONS),)):
        if command["object_id"] != p["groupId"]:
            other = json.loads(command["result_json"])
            _check(new_id not in {oid for state in other["afterState"]["scopeStates"].values() for row in state["occurrences"] if row for oid in [row["id"]]}, "cross_group_occurrence")
    return state


def _input(root: Path, connection, p: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    _check(p["beforeState"] == snapshot and p["predecessor"] == snapshot["head"], "stale_or_wrong_group_predecessor")
    _check(p["caseId"] == snapshot["caseId"] and p["groupId"] == snapshot["groupId"], "wrong_group_or_case")
    cid = _candidate_id(p)
    if p["operation"] == INTRODUCTION_OPERATION:
        _check(snapshot["pendingGrowth"] is None, "unratified_growth_pending")
        _check(base._row(connection, "identity_candidates", cid) is None and cid not in snapshot["candidates"], "candidate_already_exists")
        state = _eligible(connection, p, p["candidate"], snapshot)
        _check(state["candidate"] is None and not state["decisions"], "candidate_already_exists")
        return {"scopeStates": snapshot["scopeStates"], "proposedState": state}
    _check(cid in snapshot["candidates"], "explicit_candidate_introduction_required")
    if p["operation"] == GROWTH_OPERATION:
        _check(snapshot["pendingGrowth"] is None, "unratified_growth_pending")
        _check(cid in {row["candidateBefore"]["id"] for row in snapshot["introductions"]}, "introduced_preimage_required")
        candidate = snapshot["candidates"][cid]
        _check(candidate["status"] == "unresolved", "candidate_not_unresolved")
        _eligible(connection, p, candidate, snapshot)
    else:
        _check(snapshot["pendingGrowth"] is not None and snapshot["pendingGrowth"]["candidateId"] == cid, "exact_pending_growth_required")
    return {"scopeStates": snapshot["scopeStates"], "proposedState": None}


def _binding(p: dict[str, Any], input_state: dict[str, Any]) -> dict[str, Any]:
    before = p["beforeState"]
    cid = _candidate_id(p)
    candidate = p["candidate"] if p["operation"] == INTRODUCTION_OPERATION else before["candidates"][cid]
    return {"version": 1, "operation": p["operation"], "operationId": p["operationId"],
        "caseId": p["caseId"], "groupId": p["groupId"], "candidateId": cid,
        "occurrenceIds": sorted((candidate["occurrence_a_id"], candidate["occurrence_b_id"])),
        "candidateBefore": deepcopy(candidate), "candidateBeforeSha256": fingerprint(candidate),
        "predecessor": p["predecessor"], "beforeStateSha256": fingerprint(before),
        "inputStateSha256": fingerprint(input_state), "directEdges": deepcopy(before["directEdges"]),
        "introductions": deepcopy(before["introductions"]), "pendingGrowth": deepcopy(before["pendingGrowth"]),
        "actor": p["actor"], "rationale": p["rationale"], "privacyAcknowledged": True,
        "identityDecisionTier": legacy.IDENTITY_DECISION_TIER,
        "historicalSourceAssessment": deepcopy(p.get("historicalSourceAssessment", before["historicalAssessments"].get(cid))),
        "humanReviewDebt": deepcopy(before["group"]["humanReviewDebt"])}


def source_identity_group_review_binding(root: Path, connection, manifest: dict[str, Any]) -> dict[str, Any]:
    p = _manifest(deepcopy(manifest))
    snapshot = _capture(root, connection, p["groupId"])
    return _binding(p, _input(root, connection, p, snapshot))


def _inspect(root: Path, connection, p: dict[str, Any]) -> dict[str, Any]:
    snapshot = _capture(root, connection, p["groupId"])
    input_state = _input(root, connection, p, snapshot)
    gate = _review_gate(root, p, _binding(p, input_state))
    return {"operation": p["operation"], "groupId": p["groupId"], "candidateId": _candidate_id(p),
        "applyAllowed": True, "blockers": [], "commandIdRequired": True,
        "fingerprint": fingerprint({"manifest": p, "inputState": input_state, "reviewGate": gate}),
        "inputState": input_state, "reviewGate": gate}


def identity_source_group_preview(root: Path, manifest: Path) -> dict[str, Any]:
    p = _manifest(base._load_json(manifest, "group manifest"))
    with base._current_connection(root, readonly=True) as connection:
        try:
            result = _inspect(root, connection, p)
            return {key: val for key, val in result.items() if key not in {"inputState", "reviewGate"}}
        except (ValueError, KeyError, TypeError, OSError) as error:
            return {"operation": p["operation"], "groupId": p["groupId"], "candidateId": _candidate_id(p),
                    "applyAllowed": False, "blockers": [str(error)], "fingerprint": None, "commandIdRequired": True}


def _replace_states(states: dict[str, Any], candidate: dict[str, Any], decision: dict[str, Any] | None = None) -> dict[str, Any]:
    output = deepcopy(states)
    cid = candidate["id"]
    for own_id, state in output.items():
        if own_id == cid:
            state["candidate"] = deepcopy(candidate)
            if decision is not None:
                state["decisions"] = [deepcopy(decision)]
        elif any(row and row["id"] in {candidate["occurrence_a_id"], candidate["occurrence_b_id"]} for row in state["occurrences"]):
            state["sharedCandidates"] = sorted([r for r in state["sharedCandidates"] if r["id"] != cid] + [deepcopy(candidate)], key=lambda r: r["id"])
            if decision is not None:
                state["sharedDecisions"] = sorted([*state["sharedDecisions"], deepcopy(decision)], key=lambda r: r["id"])
    return output


def _after(p: dict[str, Any], input_state: dict[str, Any], gate: dict[str, Any], command_id: str,
           decision: dict[str, Any] | None) -> dict[str, Any]:
    after = deepcopy(p["beforeState"])
    cid = _candidate_id(p)
    if p["operation"] == INTRODUCTION_OPERATION:
        candidate = deepcopy(p["candidate"])
        after["candidates"][cid] = candidate
        states = {**input_state["scopeStates"], cid: input_state["proposedState"]}
        after["scopeStates"] = _replace_states(states, candidate)
        after["introductions"].append({"commandId": command_id, "candidateBefore": candidate,
                                       "candidateBeforeSha256": fingerprint(candidate)})
        if "historicalSourceAssessment" in p:
            after["historicalAssessments"][cid] = deepcopy(p["historicalSourceAssessment"])
    elif p["operation"] == GROWTH_OPERATION:
        _check(decision is not None, "growth_decision_required")
        candidate = {**after["candidates"][cid], "status": "resolved_same"}
        after["candidates"][cid] = candidate
        after["scopeStates"] = _replace_states(after["scopeStates"], candidate, decision)
        after["pendingGrowth"] = {"candidateId": cid, "commandId": command_id,
            "occurrenceIds": gate["binding"]["occurrenceIds"], "reviewGate": gate}
    else:
        pending = after["pendingGrowth"]
        growth_gate = pending["reviewGate"]
        refs = {"strict": {"artifactPath": growth_gate["sourceReviewPath"], "artifactSha256": growth_gate["sourceReviewSha256"], "reviewId": growth_gate["sourceReviewId"]},
            "agent": {"artifactPath": growth_gate["artifactPath"], "artifactSha256": growth_gate["artifactSha256"]},
            "independent": deepcopy(growth_gate["independentReviewRefs"]),
            "ratification": {"artifactPath": gate["artifactPath"], "artifactSha256": gate["artifactSha256"]}}
        after["directEdges"].append({"candidateId": cid, "occurrenceIds": pending["occurrenceIds"],
            "resolutionCommandId": pending["commandId"], "ratificationCommandId": command_id,
            "reviewRefs": refs, "humanReviewDebt": deepcopy(growth_gate["debt"])})
        after["group"]["occurrenceIds"] = sorted(set(after["group"]["occurrenceIds"]) | set(pending["occurrenceIds"]))
        occurrences = {row["id"]: row for state in after["scopeStates"].values() for row in state["occurrences"]}
        after["group"]["sourceItemIds"] = [occurrences[oid]["source_item_id"] for oid in after["group"]["occurrenceIds"]]
        after["pendingGrowth"] = None
    return after


def _validate_delta(p: dict[str, Any], result: dict[str, Any]) -> None:
    before, inputs = p["beforeState"], result["inputState"]
    base._object(before, SNAPSHOT_FIELDS, "source group before state")
    base._object(inputs, {"scopeStates", "proposedState"}, "source group input state")
    _check(inputs["scopeStates"] == before["scopeStates"], "group_before_state_drift")
    cid = _candidate_id(p)
    if p["operation"] == INTRODUCTION_OPERATION:
        candidate = p["candidate"]
        _check(cid not in before["candidates"] and inputs["proposedState"]["candidate"] is None
               and inputs["proposedState"]["decisions"] == [], "invalid_introduction_preimage")
        _check(before["pendingGrowth"] is None, "unratified_growth_pending")
        _check(len({candidate["occurrence_a_id"], candidate["occurrence_b_id"]} & set(before["group"]["occurrenceIds"])) == 1, "invalid_introduction_topology")
        _check(not any({candidate["occurrence_a_id"], candidate["occurrence_b_id"]} == {row["occurrence_a_id"], row["occurrence_b_id"]} for row in before["candidates"].values()), "duplicate_or_reversed_direct_pair")
    elif p["operation"] == GROWTH_OPERATION:
        _check(before["pendingGrowth"] is None and before["candidates"][cid]["status"] == "unresolved", "invalid_growth_preimage")
        _check(any(row["candidateBefore"] == before["candidates"][cid] and row["candidateBeforeSha256"] == fingerprint(before["candidates"][cid]) for row in before["introductions"]), "missing_introduction_preimage")
        candidate = before["candidates"][cid]
        _check(len({candidate["occurrence_a_id"], candidate["occurrence_b_id"]} & set(before["group"]["occurrenceIds"])) == 1, "invalid_growth_topology")
        decision = result["decision"]
        _check(isinstance(decision, dict) and decision["candidate_id"] == cid and decision["decision"] == "same"
               and decision["command_id"] == result["commandId"] and decision["input_fingerprint"] == result["inputFingerprint"]
               and decision["decided_by"] == p["actor"] and decision["rationale"] == p["rationale"], "growth_decision_mismatch")
    else:
        _check(before["pendingGrowth"] is not None and before["pendingGrowth"]["candidateId"] == cid, "ratification_growth_mismatch")
    expected = _after(p, inputs, result["reviewGate"], result["commandId"], result["decision"])
    _check(result["afterState"] == expected and result["postStateSha256"] == fingerprint(expected["scopeStates"]), "unexpected_group_delta")


def apply_identity_source_group(root: Path, manifest: Path, *, expected_fingerprint: str, command_id: str) -> dict[str, Any]:
    p = _manifest(base._load_json(manifest, "group manifest"))
    base._text(expected_fingerprint, "expectedFingerprint")
    base._text(command_id, "commandId")
    with base._current_connection(root, readonly=False) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            replay = replay_command(connection, command_id=command_id, command_type=p["operation"],
                object_type="source_identity_group", object_id=p["groupId"], request_fingerprint=base._request(p, expected_fingerprint))
            if replay is not None:
                _capture(root, connection, p["groupId"])
                return replay
            preview = _inspect(root, connection, p)
            if preview["fingerprint"] != expected_fingerprint:
                raise IdentitySourceGroupConflict("group_state_changed_after_preview")
            # The decision references its envelope through an immediate FK.
            # Final immutable result replaces this placeholder in this same
            # transaction; no incomplete envelope can be committed or observed.
            record_command(connection, command_id=command_id, command_type=p["operation"], object_type="source_identity_group",
                object_id=p["groupId"], input_fingerprint=expected_fingerprint, request_fingerprint=base._request(p, expected_fingerprint),
                actor=p["actor"], rationale=p["rationale"], result={}, projection_status="not_required")
            cid, decision = _candidate_id(p), None
            if p["operation"] == INTRODUCTION_OPERATION:
                row = p["candidate"]
                columns = sorted(CANDIDATE_FIELDS)
                connection.execute(f'INSERT INTO identity_candidates({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})', tuple(row[key] for key in columns))
            elif p["operation"] == GROWTH_OPERATION:
                decision_id = stable_id("identity_decision", command_id, cid)
                connection.execute("UPDATE identity_candidates SET status='resolved_same' WHERE id=? AND status='unresolved'", (cid,))
                connection.execute("""INSERT INTO identity_decisions(id,candidate_id,decision,decided_by,decided_at,rationale,command_id,input_fingerprint)
                    VALUES (?,?,'same',?,?,?,?,?)""", (decision_id, cid, p["actor"], utc_now(), p["rationale"], command_id, expected_fingerprint))
                decision = dict(base._row(connection, "identity_decisions", decision_id))
            after = _after(p, preview["inputState"], preview["reviewGate"], command_id, decision)
            _current_matches(connection, after)
            result = {"operation": p["operation"], "commandId": command_id, "groupId": p["groupId"],
                "candidateId": cid, "manifest": p, "manifestFingerprint": fingerprint(p),
                "inputFingerprint": expected_fingerprint, "inputState": preview["inputState"],
                "reviewGate": preview["reviewGate"], "decision": decision, "afterState": after,
                "postStateSha256": fingerprint(after["scopeStates"]), "projectionStatus": "not_required"}
            _validate_delta(p, result)
            connection.execute("UPDATE curation_commands SET result_json=? WHERE command_id=?",
                (json.dumps(result, ensure_ascii=False, sort_keys=True), command_id))
            connection.execute("INSERT INTO audit_log(occurred_at,actor,action,object_type,object_id,before_json,after_json,rationale) VALUES (?,?,?,'source_identity_group',?,?,?,?)",
                (utc_now(), p["actor"], p["operation"], p["groupId"], json.dumps({"inputFingerprint": expected_fingerprint}), json.dumps(result, ensure_ascii=False, sort_keys=True), p["rationale"]))
            return {**result, "alreadyApplied": False}
        except CommandEnvelopeConflict as error:
            raise IdentitySourceGroupConflict(str(error)) from error


def validated_source_identity_groups(root: Path, connection, *, context=None) -> dict[str, Any]:
    """Fresh fail-closed lineage admission, no persistent cache or domain writes."""
    if context is not None:
        base._check_projection_context(root, connection, context)
    groups, blocked = [], []
    ids = [row[0] for row in connection.execute("SELECT DISTINCT object_id FROM curation_commands WHERE command_type IN (?,?,?)", tuple(sorted(OPERATIONS)))]
    for group_id in ids:
        try:
            snapshot = _capture(root, connection, group_id, context=context)
            group = deepcopy(snapshot["group"])
            group["directEdges"] = deepcopy(snapshot["directEdges"])
            group["introducedCandidateIds"] = sorted(cid for cid, row in snapshot["candidates"].items() if row["status"] == "unresolved")
            head_command, head_result = base._command(connection, snapshot["head"]["commandId"])
            parent = head_result["manifest"]["predecessor"]
            group["lineage"] = {"version": 1, "seedResolutionCommandId": snapshot["seedResolutionCommandId"],
                "headCommandId": head_command["command_id"], "headResultSha256": fingerprint(head_result),
                "predecessorCommandId": parent["commandId"], "predecessorResultSha256": parent["resultSha256"]}
            groups.append(group)
        except (ValueError, TypeError, KeyError, OSError) as error:
            blocked.append({"groupId": group_id, "blockers": [str(error)]})
    counts = Counter(oid for group in groups for oid in group["occurrenceIds"])
    groups = [group for group in groups if all(counts[oid] == 1 for oid in group["occurrenceIds"])]
    return {"groups": sorted(groups, key=lambda g: g["id"]), "claimedGroupIds": sorted(ids), "blocked": blocked}


__all__ = ["INTRODUCTION_OPERATION", "GROWTH_OPERATION", "RATIFICATION_OPERATION", "IdentitySourceGroupError",
    "IdentitySourceGroupConflict", "capture_source_identity_group_before_state", "source_identity_group_review_binding",
    "identity_source_group_preview", "apply_identity_source_group", "validated_source_identity_groups"]
