"""Direct act/act identity decisions for two already linked occurrences.

Schemas mirror identity_source_profile_curation version 1 with sourcePair
{occurrenceAId, occurrenceBId, targetPersonId}; artifact family strict_source_pair_*,
verdict approve_agent_source_pair_identity. Binding keys occurrenceAId and
occurrenceBId replace externalOccurrenceId/sourceOccurrenceId; both occurrence
hashes retain their existing person link. Own independent pair review is required.
"""
from pathlib import Path
from typing import Any
from . import identity_source_profile_curation as base

OPERATION = base.PAIR_OPERATION
RATIFICATION_OPERATION = base.PAIR_RATIFICATION_OPERATION
IDENTITY_DECISION_TIER = base.IDENTITY_DECISION_TIER
IdentityOnlyCurationError = base.IdentityOnlyCurationError
IdentityOnlyCurationConflict = base.IdentityOnlyCurationConflict


def source_pair_review_binding(connection, *, case_id: str, candidate_id: str,
                               occurrence_a_id: str, occurrence_b_id: str,
                               target_person_id: str) -> dict[str, Any]:
    return base._binding(connection, {"operation": OPERATION, "caseId": case_id,
        "candidate": {"id": candidate_id}, "sourcePair": {"occurrenceAId": occurrence_a_id,
        "occurrenceBId": occurrence_b_id, "targetPersonId": target_person_id}})


def identity_source_pair_resolution_preview(root: Path, manifest: Path) -> dict[str, Any]:
    return base.identity_source_profile_resolution_preview(root, manifest, operation=OPERATION)


def apply_identity_source_pair_resolution(root: Path, manifest: Path, *, expected_fingerprint: str, command_id: str) -> dict[str, Any]:
    return base.apply_identity_source_profile_resolution(root, manifest,
        expected_fingerprint=expected_fingerprint, command_id=command_id, operation=OPERATION)


def identity_source_pair_agent_ratification_preview(root: Path, manifest: Path) -> dict[str, Any]:
    return base.identity_source_profile_agent_ratification_preview(root, manifest, operation=RATIFICATION_OPERATION)


def apply_identity_source_pair_agent_ratification(root: Path, manifest: Path, *, expected_fingerprint: str, command_id: str) -> dict[str, Any]:
    return base.apply_identity_source_profile_agent_ratification(root, manifest,
        expected_fingerprint=expected_fingerprint, command_id=command_id, operation=RATIFICATION_OPERATION)
