"""Bounded source occurrence / retained imported-profile identity resolution.

All artifacts are version 1 JSON under research/records/reassessments. Exact
schemas (unknown/missing fields fail closed):
* manifest: version, operation, operationId, reviewId, caseId, candidate
  {id, expectedStatus="unresolved", decision="same"}, sourceProfile
  {externalOccurrenceId, sourceOccurrenceId, targetPersonId}, agentReview
  {artifactPath, expectedSha256}, decidedBy, rationale.
* strict review: version, artifactType="strict_source_profile_identity_review",
  reviewId, caseId, status="terminal", operational=false, supersedesReviewId
  (null or prior ID), guardrails {requiredApplyMechanism, directOnly=true,
  transitiveMerge=false}, independentReviewRefs, entries. Each entry has
  candidateId, binding (source_profile_review_binding), recommendation="same",
  humanDecision="pending", rationale, debt (nonempty object).
* independent artifact: version, artifactType (strict_source_profile_source_audit
  or strict_source_profile_evidence_correlation), artifactId, caseId,
  reviewerRole, reviewerRunId, status="terminal", operational=false,
  sourceReviewId, results. Each result has candidateId, binding,
  verdict="approve_agent_source_profile_identity", rationale, debt.
* independent ref: artifactPath, artifactSha256, artifactId, reviewerRole,
  reviewerRunId, reviewedCandidateIds, verdict.
* agent decision: version, artifactType="strict_source_profile_agent_decision",
  reviewId, sourceReviewId, sourceReviewSha256, caseId, candidateId, binding,
  decision="approve_same", decidedByKind="agent", identityDecisionTier,
  decidedBy, decidedAt (timezone ISO), rationale, debt, independentReviewRefs.
* ratification manifest: version, operation, operationId, caseId, candidateId,
  priorCommandId, expectedDecisionId, ratificationReview {artifactPath,
  expectedSha256}, ratifiedBy, rationale.
* ratification artifact: version,
  artifactType="strict_source_profile_agent_ratification", ratificationId,
  caseId, candidateId, priorCommandId, priorCommandResultSha256,
  priorDecisionId, agentReviewSha256, sourceReviewId, sourceReviewSha256,
  independentReviewRefs, binding, decision="ratify_existing_agent_same",
  decidedByKind="agent", identityDecisionTier, decidedBy, decidedAt,
  rationale, debt. Ratification is append-only with no domain writes.

V2 dependency revalidation schema (version 1): manifest fields version,
operation="identity_dependency_revalidation", operationId, reviewId, caseId,
candidate {id, expectedStatus="resolved_same", decision="revalidate"},
priorCommandId, beforeState {artifactPath, expectedSha256}, transitions,
currentStateSha256, agentReview {artifactPath, expectedSha256}, decidedBy,
rationale. Each transition has exactly candidateId, resolutionCommandId,
resolutionResultSha256, ratificationCommandId, ratificationResultSha256,
revalidationCommandId and revalidationResultSha256 (both null when absent).
The readonly capture helper returns the exact strict_identity_dependency_before_state
artifact schema; the binding helper returns the complete hash-bound review binding.
Fresh strict_dependency_revalidation_identity_review / source_audit /
evidence_correlation / agent_decision artifacts reuse the strict schemas above,
with recommendation="revalidate", decision="approve_revalidation" and verdict
"approve_agent_dependency_revalidation_identity". Original debt remains exact.
Later manifests may add supersedesRevalidation {commandId, resultSha256}.
They may also enumerate introducedCandidates {candidateId, candidateBeforeSha256}:
each full unresolved-row hash must match the immutable candidateBefore captured
by its one explicitly listed resolution, followed by exact same-target ratification.
Legacy resolutions without that preimage cannot introduce candidates.
Revisions form one append-only linear chain with one leaf, each leaf proving the
complete cumulative delta against the original frozen before-state. Earlier
revisions remain immutable historical receipts. Forks and cycles fail closed.

Source != proof. These operations retain human pending and never promote names,
facts, kinship, person status, other candidates or question targets.
"""
from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

from .command_envelope import CommandEnvelopeConflict, fingerprint, record_command, replay_command
from .identity_only_curation import (
    IdentityOnlyCurationConflict, IdentityOnlyCurationError, _current_connection,
    _load_json, _row, _rows, _strict_object, _text,
)
from .identity_only_local_curation import _relative_artifact, _iso_timestamp, REQUIRED_TABLES
from .research import require_tables
from .workspace import sha256_file, stable_id, utc_now

OPERATION = "identity_source_profile_resolution"
RATIFICATION_OPERATION = "identity_source_profile_agent_ratification"
PAIR_OPERATION = "identity_source_pair_resolution"
PAIR_RATIFICATION_OPERATION = "identity_source_pair_agent_ratification"
SOURCE_ONLY_OPERATION = "identity_source_only_resolution"
SOURCE_ONLY_RATIFICATION_OPERATION = "identity_source_only_agent_ratification"
REVALIDATION_OPERATION = "identity_dependency_revalidation"
IDENTITY_DECISION_TIER = "agent_approved_human_pending"
VERDICT = "approve_agent_source_profile_identity"
ROLES = {"source_auditor", "evidence_correlator"}
REF_FIELDS = {"artifactPath", "artifactSha256", "artifactId", "reviewerRole", "reviewerRunId", "reviewedCandidateIds", "verdict"}
_CATALOG_READ_WORKERS = 4
_CATALOG_READ_BATCH_SIZE = 8
_CATALOG_PARALLEL_MIN_FILES = 32
MANIFEST_FIELDS = {"version", "operation", "operationId", "reviewId", "caseId", "candidate", "sourceProfile", "agentReview", "decidedBy", "rationale"}
RAT_MANIFEST_FIELDS = {"version", "operation", "operationId", "caseId", "candidateId", "priorCommandId", "expectedDecisionId", "ratificationReview", "ratifiedBy", "rationale"}
DECISION_FIELDS = {"version", "artifactType", "reviewId", "sourceReviewId", "sourceReviewSha256", "caseId", "candidateId", "binding", "decision", "decidedByKind", "identityDecisionTier", "decidedBy", "decidedAt", "rationale", "debt", "independentReviewRefs"}
STRICT_FIELDS = {"version", "artifactType", "reviewId", "caseId", "status", "operational", "supersedesReviewId", "guardrails", "independentReviewRefs", "entries"}
ENTRY_FIELDS = {"candidateId", "binding", "recommendation", "humanDecision", "rationale", "debt"}
INDEPENDENT_FIELDS = {"version", "artifactType", "artifactId", "caseId", "reviewerRole", "reviewerRunId", "status", "operational", "sourceReviewId", "results"}
RESULT_FIELDS = {"candidateId", "binding", "verdict", "rationale", "debt"}
RAT_FIELDS = {"version", "artifactType", "ratificationId", "caseId", "candidateId", "priorCommandId", "priorCommandResultSha256", "priorDecisionId", "agentReviewSha256", "sourceReviewId", "sourceReviewSha256", "independentReviewRefs", "binding", "decision", "decidedByKind", "identityDecisionTier", "decidedBy", "decidedAt", "rationale", "debt"}


def _profile(p: dict[str, Any]) -> dict[str, Any]:
    if "sourceProfile" in p:
        return p["sourceProfile"]
    pair = p["sourceOnly"] if p["operation"] == SOURCE_ONLY_OPERATION else p["sourcePair"]
    return {"externalOccurrenceId": pair["occurrenceAId"], "sourceOccurrenceId": pair["occurrenceBId"], "targetPersonId": pair.get("targetPersonId")}


def _family(p: dict[str, Any]) -> str:
    if p["operation"] in {SOURCE_ONLY_OPERATION, SOURCE_ONLY_RATIFICATION_OPERATION}:
        return "source_only"
    if p["operation"] == REVALIDATION_OPERATION:
        return "dependency_revalidation"
    return "source_pair" if p["operation"] in {PAIR_OPERATION, PAIR_RATIFICATION_OPERATION} else "source_profile"


def _rat_operation(p: dict[str, Any]) -> str:
    if _family(p) == "source_only":
        return SOURCE_ONLY_RATIFICATION_OPERATION
    return PAIR_RATIFICATION_OPERATION if _family(p) == "source_pair" else RATIFICATION_OPERATION



def _check(condition: bool, message: str) -> None:
    if not condition:
        raise IdentityOnlyCurationError(message)


def _object(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    return _strict_object(value, label, fields)


def _debt(value: Any) -> None:
    _check(isinstance(value, dict) and bool(value), "Explicit nonempty debt object required")


def _load_manifest(path: Path, *, ratification: bool = False, operation: str | None = None) -> dict[str, Any]:
    operation = operation or (RATIFICATION_OPERATION if ratification else OPERATION)
    _check(operation in ({RATIFICATION_OPERATION, PAIR_RATIFICATION_OPERATION, SOURCE_ONLY_RATIFICATION_OPERATION} if ratification else {OPERATION, PAIR_OPERATION, SOURCE_ONLY_OPERATION}), "Unsupported typed operation")
    pair = operation in {PAIR_OPERATION, PAIR_RATIFICATION_OPERATION}
    source_only = operation in {SOURCE_ONLY_OPERATION, SOURCE_ONLY_RATIFICATION_OPERATION}
    fields = (MANIFEST_FIELDS - {"sourceProfile"}) | {"sourceOnly"} if source_only else ((MANIFEST_FIELDS - {"sourceProfile"}) | {"sourcePair"} if pair else MANIFEST_FIELDS)
    value = _load_json(path, "manifest")
    if source_only and not ratification and isinstance(value, dict) and "historicalSourceAssessment" in value:
        fields = fields | {"historicalSourceAssessment"}
    p = _object(value, RAT_MANIFEST_FIELDS if ratification else fields, "manifest")
    _check(type(p["version"]) is int and p["version"] == 1, "Manifest version must be 1")
    _check(p["operation"] == operation, "Wrong operation")
    texts = ("operationId", "caseId", "candidateId", "priorCommandId", "expectedDecisionId", "ratifiedBy", "rationale") if ratification else ("operationId", "reviewId", "caseId", "decidedBy", "rationale")
    for field in texts:
        p[field] = _text(p[field], field)
    _check(len(p["rationale"]) >= 20, "Rationale must have at least 20 characters")
    ref = p["ratificationReview" if ratification else "agentReview"]
    _object(ref, {"artifactPath", "expectedSha256"}, "artifact reference")
    if not ratification:
        c = _object(p["candidate"], {"id", "expectedStatus", "decision"}, "candidate")
        _text(c["id"], "candidate.id")
        _check(c["expectedStatus"] == "unresolved" and c["decision"] == "same", "Only unresolved same permitted")
        domain_key = "sourceOnly" if source_only else ("sourcePair" if pair else "sourceProfile")
        domain_fields = {"occurrenceAId", "occurrenceBId"} if source_only else ({"occurrenceAId", "occurrenceBId", "targetPersonId"} if pair else {"externalOccurrenceId", "sourceOccurrenceId", "targetPersonId"})
        _object(p[domain_key], domain_fields, domain_key)
        s = _profile(p)
        for field in s:
            if not (source_only and field == "targetPersonId"):
                _text(s[field], field)
        _check(s["externalOccurrenceId"] != s["sourceOccurrenceId"], "Endpoints must differ")
        if source_only and "historicalSourceAssessment" in p:
            from .identity_source_only_curation import _validate_historical_assessment
            _validate_historical_assessment(p["historicalSourceAssessment"])
    return p


def source_profile_review_binding(connection, *, case_id: str, candidate_id: str,
                                  external_occurrence_id: str, source_occurrence_id: str,
                                  target_person_id: str, normalize_source_person: bool = True) -> dict[str, Any]:
    """Build a read-only exact review binding; source person link normalized NULL.

    Both source-item rows, literal occurrence rows and retained person row are
    hashed. This binding is identical before/after the permitted attachment.
    """
    occurrences = []
    sources = []
    for oid in (external_occurrence_id, source_occurrence_id):
        row = _row(connection, "person_occurrences", oid)
        _check(row is not None, "Missing bound occurrence")
        occurrence = dict(row)
        if oid == source_occurrence_id and normalize_source_person:
            occurrence["person_id"] = None
        occurrences.append(occurrence)
        source = _row(connection, "source_items", row["source_item_id"])
        _check(source is not None, "Missing bound source")
        sources.append(dict(source))
    person = _row(connection, "persons", target_person_id)
    _check(person is not None, "Missing bound target")
    return {"caseId": case_id, "candidateId": candidate_id,
            "externalOccurrenceId": external_occurrence_id, "sourceOccurrenceId": source_occurrence_id,
            "targetPersonId": target_person_id, "sourceItemIds": [r["id"] for r in sources],
            "sourceSha256": [fingerprint(r) for r in sources],
            "occurrenceSha256": [fingerprint(r) for r in occurrences],
            "targetSha256": fingerprint(dict(person))}


def _binding(connection, p: dict[str, Any]) -> dict[str, Any]:
    if _family(p) == "source_only":
        from .identity_source_only_curation import source_only_review_binding
        pair = p["sourceOnly"]
        return source_only_review_binding(connection, case_id=p["caseId"], candidate_id=p["candidate"]["id"], occurrence_a_id=pair["occurrenceAId"], occurrence_b_id=pair["occurrenceBId"], historical_source_assessment=p.get("historicalSourceAssessment"))
    s = _profile(p)
    binding = source_profile_review_binding(connection, case_id=p["caseId"], candidate_id=p["candidate"]["id"],
        external_occurrence_id=s["externalOccurrenceId"], source_occurrence_id=s["sourceOccurrenceId"], target_person_id=s["targetPersonId"], normalize_source_person=_family(p) != "source_pair")
    if _family(p) == "source_pair":
        binding["occurrenceAId"] = binding.pop("externalOccurrenceId")
        binding["occurrenceBId"] = binding.pop("sourceOccurrenceId")
    return binding


def _read_catalog_file(file: Path) -> tuple[Path, str | None, bytes | None]:
    try:
        data = file.read_bytes()
    except OSError:
        return file, None, None
    return file, hashlib.sha256(data).hexdigest(), data


def _catalog_file_bytes(files: list[Path]):
    """Read/hash every path, with bounded in-flight bytes and sorted results.

    Workers only perform file I/O and hashing; parsing and all database access
    stay on the caller thread. Small inventories avoid executor setup overhead.
    """
    if len(files) < _CATALOG_PARALLEL_MIN_FILES:
        yield from map(_read_catalog_file, files)
        return
    with ThreadPoolExecutor(max_workers=_CATALOG_READ_WORKERS) as executor:
        for start in range(0, len(files), _CATALOG_READ_BATCH_SIZE):
            # Executor.map submits its iterable eagerly on supported Python
            # versions, so supply only one small batch, never the full catalog.
            yield from executor.map(_read_catalog_file, files[start:start + _CATALOG_READ_BATCH_SIZE])


class _ValidationContext:
    """Artifact-only memoization owned by one synchronous admission invocation.

    Inventory and actual bytes are read again for every gate lookup. No stat
    shortcut, database state, prior validation or admission result is cached.
    """
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.parsed: dict[tuple[str, str], Any] = {}
        self.gates: dict[str, dict[str, Any]] = {}
        self.catalog_sha: str | None = None
        self.catalog_records: list[tuple[Path, dict[str, Any]]] = []
        self._catalog_entries: dict[Path, tuple[str | None, Any]] = {}

    def _parse_bytes(self, path: Path, data: bytes) -> Any:
        key = (str(path.resolve()), hashlib.sha256(data).hexdigest())
        if key not in self.parsed:
            self.parsed[key] = json.loads(data.decode("utf-8"))
        # No caller can mutate the memoized preimage, gate or review objects.
        return deepcopy(self.parsed[key])

    def artifact(self, path: Path, digest: str, label: str) -> Any:
        data = path.read_bytes()
        _check(hashlib.sha256(data).hexdigest() == digest, f"{label} bytes changed during validation")
        return self._parse_bytes(path, data)

    def catalog(self, root: Path) -> tuple[str, list[tuple[Path, dict[str, Any]]]]:
        _check(root.resolve() == self.root, "Validation context root mismatch")
        inventory = []
        entries = {}
        files = sorted((root / "research/records/reassessments").rglob("*.json"))
        for file, file_digest, data in _catalog_file_bytes(files):
            inventory.append((str(file), file_digest))
            if data is None:
                # Preserve the old scan's unreadable-file handling while making
                # removal/unreadability part of the gate invalidation key.
                entries[file] = (None, None)
                continue
            previous = self._catalog_entries.get(file)
            if previous is not None and previous[0] == file_digest:
                value = previous[1]
            else:
                try:
                    value = self._parse_bytes(file, data)
                except ValueError:
                    value = None
                # Retain the private parse preimage, rather than the mutable
                # catalog value exposed to callers. Patched/nonmemoizing parsers
                # still work through the returned value.
                value = self.parsed.get((str(file.resolve()), file_digest), value)
            entries[file] = (file_digest, value)
        digest = fingerprint(inventory)
        if digest != self.catalog_sha:
            self.catalog_records = [(file, deepcopy(value)) for file, (_, value) in entries.items() if isinstance(value, dict)]
            self.catalog_sha = digest
            self.gates.clear()
        self._catalog_entries = entries
        return digest, self.catalog_records


class _ProjectionSnapshotChanged(IdentityOnlyCurationError):
    pass


class _ProjectionValidationSnapshot:
    """Private, invocation-owned read snapshot; never used by mutable admission.

    SQLite holds the database read view. The complete review inventory and every
    pointed artifact are bound by bytes, then checked again before publication.
    Only successful full validations enter the node memo; failures never do.
    """
    def __init__(self, root: Path, connection):
        self.active = True
        self.artifacts = _ValidationContext(root)
        self.connection = connection
        self.changes = connection.total_changes
        self.schema = connection.execute("PRAGMA schema_version").fetchone()[0]
        self.version = connection.execute("PRAGMA data_version").fetchone()[0]
        self.inventory = self.artifacts.catalog(root)
        self.references: dict[Path, str] = {}
        self.relative_references: dict[str, tuple[Path, str]] = {}
        self.review_bytes_changed = False
        self.priors: dict[str, Any] = {}
        self.ratifications: dict[str, Any] = {}
        self._audit_rows: dict[tuple[str, str], list[dict[str, Any]]] | None = None

    @property
    def gates(self):
        _check(self.active, "Projection validation snapshot expired")
        return self.artifacts.gates

    def catalog(self, root: Path):
        _check(self.active, "Projection validation snapshot expired")
        _check(root.resolve() == self.artifacts.root, "Validation context root mismatch")
        return self.inventory

    def catalog_entries(self, root: Path):
        """Full frozen dictionary catalog with byte digests, without more I/O."""
        _, records = self.catalog(root)
        for path, value in records:
            yield path, value, self.artifacts._catalog_entries[path][0]

    def bind_reference(self, path: Path, digest: str):
        """Remember bytes already successfully checked by a reference reader."""
        _check(self.active, "Projection validation snapshot expired")
        if path in self.references and self.references[path] != digest:
            self._changed_review_bytes()
        self.references[path] = digest

    def _changed_review_bytes(self):
        # A later restoration cannot make an inconsistent export admissible.
        self.review_bytes_changed = True
        raise _ProjectionSnapshotChanged("Review bytes changed during projection validation")

    def artifact(self, path: Path, digest: str, label: str):
        _check(self.active, "Projection validation snapshot expired")
        bound_digest = self.references.get(path)
        try:
            data = path.read_bytes()
        except OSError:
            if bound_digest is not None:
                self._changed_review_bytes()
            raise
        actual_digest = hashlib.sha256(data).hexdigest()
        if bound_digest is not None and actual_digest != bound_digest:
            self._changed_review_bytes()
        _check(actual_digest == digest, f"{label} bytes changed during validation")
        # Direct readers may supply an already-invalid reference. Bind only
        # after its first successful hash, preserving their guarded output.
        self.bind_reference(path, digest)
        return self.artifacts._parse_bytes(path, data)

    def audit_receipts(self, connection, action: str, object_id: str):
        """Index raw audit rows once in this database view, never admissions."""
        self.check_scope(self.artifacts.root, connection)
        if self._audit_rows is None:
            indexed: dict[tuple[str, str], list[dict[str, Any]]] = {}
            for row in connection.execute("SELECT * FROM audit_log WHERE object_type='identity_candidate'"):
                receipt = dict(row)
                indexed.setdefault((receipt["action"], receipt["object_id"]), []).append(receipt)
            self.check_database()
            self._audit_rows = indexed
        # Preserve every duplicate and field, without exposing the cached rows
        # to callers that parse or otherwise inspect the receipt data.
        return deepcopy(self._audit_rows.get((action, object_id), []))

    def check_database(self):
        if self.review_bytes_changed:
            raise _ProjectionSnapshotChanged("Review bytes changed during projection validation")
        if (not self.active or not self.connection.in_transaction or
                self.connection.total_changes != self.changes or
                self.connection.execute("PRAGMA schema_version").fetchone()[0] != self.schema or
                self.connection.execute("PRAGMA data_version").fetchone()[0] != self.version):
            raise _ProjectionSnapshotChanged("Database changed during projection validation")

    def check_scope(self, root: Path, connection):
        _check(self.active, "Projection validation snapshot expired")
        _check(isinstance(root, Path) and root.resolve() == self.artifacts.root, "Validation context root mismatch")
        _check(connection is self.connection, "Validation context connection mismatch")
        self.check_database()

    def verify(self, root: Path):
        self.check_database()
        if self.artifacts.catalog(root)[0] != self.inventory[0]:
            raise _ProjectionSnapshotChanged("Review inventory changed during projection validation")
        for path, digest in self.references.items():
            try:
                unchanged = hashlib.sha256(path.read_bytes()).hexdigest() == digest
            except OSError:
                unchanged = False
            if not unchanged:
                self._changed_review_bytes()


@contextmanager
def _projection_validation_snapshot(root: Path, connection):
    # A uniquely named SAVEPOINT nests without committing or rolling back caller
    # work. Reading sqlite_master establishes the database snapshot immediately.
    name = "genea_projection_" + uuid4().hex
    connection.execute(f'SAVEPOINT "{name}"')
    context = None
    try:
        connection.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
        context = _ProjectionValidationSnapshot(root, connection)
        yield context
        context.verify(root)
    except BaseException:
        # Undo only work introduced after our savepoint. In particular, never
        # let RELEASE commit an unexpected same-connection write on failure.
        connection.execute(f'ROLLBACK TO SAVEPOINT "{name}"')
        raise
    finally:
        if context is not None:
            context.active = False
        connection.execute(f'RELEASE SAVEPOINT "{name}"')
    # Outside our transaction, data_version also detects a concurrent commit
    # previously hidden by SQLite's stable read view. An existing caller read
    # transaction deliberately retains its own consistent SQLite snapshot.
    if connection.execute("PRAGMA data_version").fetchone()[0] != context.version:
        raise _ProjectionSnapshotChanged("Database changed while finishing projection")


def _check_projection_context(root: Path, connection, context) -> None:
    """Reject admission-only, expired or differently scoped export contexts."""
    _check(isinstance(context, _ProjectionValidationSnapshot), "Projection validation snapshot required")
    context.check_scope(root, connection)


def _context_relative_artifact(root: Path, path_text, digest, *, label: str, context=None):
    """Retain observed drift even when prehash failure is guarded per chain."""
    snapshot = context if isinstance(context, _ProjectionValidationSnapshot) else None
    reference_key = path_text.strip() if isinstance(path_text, str) else None
    if snapshot is not None:
        snapshot.catalog(root)  # Validate the active root before any binding.
    previous = snapshot.relative_references.get(reference_key) if snapshot is not None else None
    if snapshot is not None and previous is None and reference_key and not Path(reference_key).is_absolute():
        try:
            path = (root / reference_key).resolve()
        except OSError:
            pass
        else:
            if path in snapshot.references:
                previous = (path, snapshot.references[path])
    try:
        bound = _relative_artifact(root, path_text, digest, label=label)
    except (OSError, ValueError):
        if previous is not None:
            path, bound_digest = previous
            if digest == bound_digest:
                snapshot._changed_review_bytes()
            # An additional statically invalid reference to an unchanged file
            # is still just an invalid chain, even after another valid reader.
            try:
                unchanged = hashlib.sha256(path.read_bytes()).hexdigest() == bound_digest
            except OSError:
                unchanged = False
            if not unchanged:
                snapshot._changed_review_bytes()
        raise
    if snapshot is not None:
        path, _, bound_digest = bound
        if previous is not None and previous[1] != bound_digest:
            snapshot._changed_review_bytes()
        snapshot.bind_reference(path, bound_digest)
        snapshot.relative_references[reference_key] = (path, bound_digest)
    return bound


def _artifact(root: Path, ref: dict[str, Any], label: str, *, independent: bool = False, context: _ValidationContext | None = None):
    path, relative, digest = _context_relative_artifact(root, ref["artifactPath"], ref["artifactSha256" if independent else "expectedSha256"], label=label, context=context)
    if context is not None:
        return context.artifact(path, digest, label), relative, digest
    return _load_json(path, label), relative, digest


def _review_gate(root: Path, p: dict[str, Any], binding: dict[str, Any], *, historical_receipt: bool = False, context: _ValidationContext | None = None) -> dict[str, Any]:
    if context is None:
        return _review_gate_uncached(root, p, binding, historical_receipt=historical_receipt)
    catalog_sha, catalog = context.catalog(root)
    key = fingerprint({"manifest": p, "binding": binding, "historicalReceipt": historical_receipt, "catalogSha256": catalog_sha})
    if key in context.gates:
        gate = context.gates[key]
        # Pointed artifacts can have non-JSON suffixes and are not necessarily
        # in the broad catalog. Rehash each exact reference on every cache hit.
        _context_relative_artifact(root, p["agentReview"]["artifactPath"], p["agentReview"]["expectedSha256"], label="agent decision", context=context)
        _context_relative_artifact(root, gate["sourceReviewPath"], gate["sourceReviewSha256"], label="strict review", context=context)
        for ref in gate["independentReviewRefs"]:
            _context_relative_artifact(root, ref["artifactPath"], ref["artifactSha256"], label=ref["reviewerRole"], context=context)
        return deepcopy(gate)
    gate = _review_gate_uncached(root, p, binding, historical_receipt=historical_receipt, context=context, catalog=catalog)
    context.gates[key] = deepcopy(gate)
    return deepcopy(gate)


def _review_gate_uncached(root: Path, p: dict[str, Any], binding: dict[str, Any], *, historical_receipt: bool = False,
        context: _ValidationContext | None = None, catalog: list[tuple[Path, dict[str, Any]]] | None = None) -> dict[str, Any]:
    decision, path, digest = _artifact(root, p["agentReview"], "agent decision", context=context)
    d = _object(decision, DECISION_FIELDS, "decision")
    expected = {"version": 1, "artifactType": f"strict_{_family(p)}_agent_decision", "reviewId": p["reviewId"], "caseId": p["caseId"], "candidateId": p["candidate"]["id"], "binding": binding, "decision": "approve_revalidation" if p["operation"] == REVALIDATION_OPERATION else "approve_same", "decidedByKind": "agent", "identityDecisionTier": IDENTITY_DECISION_TIER, "decidedBy": p["decidedBy"], "rationale": p["rationale"]}
    _check(type(d["version"]) is int and all(d[k] == v for k, v in expected.items()), "Decision binding mismatch")
    _iso_timestamp(d["decidedAt"], "decidedAt")
    _text(d["sourceReviewId"], "sourceReviewId")
    _debt(d["debt"])
    records = []
    if catalog is None:
        catalog = []
        for file in (root / "research/records/reassessments").rglob("*.json"):
            try:
                catalog.append((file, json.loads(file.read_text(encoding="utf-8"))))
            except (OSError, ValueError):
                continue
    for file, value in catalog:
        if isinstance(value, dict) and value.get("artifactType") == f"strict_{_family(p)}_identity_review" and value.get("caseId") == p["caseId"]:
            records.append((file, value))
    matches = [(f, r) for f, r in records if r.get("reviewId") == d["sourceReviewId"]]
    _check(len(matches) == 1, "Strict review must be unique")
    file, strict = matches[0]
    strict = _object(strict, STRICT_FIELDS, "strict review")
    strict_relative = file.resolve().relative_to(root.resolve()).as_posix()
    _context_relative_artifact(root, strict_relative, d["sourceReviewSha256"], label="strict review", context=context)
    _check(not historical_receipt or p["operation"] == REVALIDATION_OPERATION, "Historical review validation only applies to immutable revalidation receipts")
    if not historical_receipt:
        _check(not any(r.get("supersedesReviewId") == d["sourceReviewId"] for _, r in records), "Strict review is superseded")
    _check(type(strict["version"]) is int and strict["version"] == 1 and strict["status"] == "terminal" and strict["operational"] is False, "Strict review is not terminal nonoperational")
    guardrails = _object(strict["guardrails"], {"requiredApplyMechanism", "directOnly", "transitiveMerge"}, "guardrails")
    _check(guardrails["requiredApplyMechanism"] == p["operation"] and guardrails["directOnly"] is True and guardrails["transitiveMerge"] is False, "Unsafe guardrails")
    _check(isinstance(strict["entries"], list), "Entries must be a list")
    entries = [_object(e, ENTRY_FIELDS, "entry") for e in strict["entries"]]
    if _family(p) == "source_only":
        _check(len(entries) == 1, "Source-only review requires one exact pair")
    matching = [e for e in entries if e["candidateId"] == binding["candidateId"]]
    _check(len(matching) == 1, "Exact entry required")
    entry = matching[0]
    _check(entry["binding"] == binding and entry["recommendation"] == ("revalidate" if p["operation"] == REVALIDATION_OPERATION else "same") and entry["humanDecision"] == "pending" and entry["rationale"] == d["rationale"] and entry["debt"] == d["debt"], "Strict entry binding mismatch")
    refs = d["independentReviewRefs"]
    _check(isinstance(refs, list) and len(refs) == 2 and refs == strict["independentReviewRefs"], "Exact two independent refs required")
    normalized = [_object(r, REF_FIELDS, "independent ref") for r in refs]
    _check({r["reviewerRole"] for r in normalized} == ROLES and len({r["reviewerRunId"] for r in normalized}) == 2, "Independent roles/runs required")
    paths = {path, strict_relative}
    hashes = {digest, d["sourceReviewSha256"]}
    for ref in normalized:
        role = ref["reviewerRole"]
        a, relative, sha = _artifact(root, ref, role, independent=True, context=context)
        a = _object(a, INDEPENDENT_FIELDS, role)
        _check(relative not in paths and sha not in hashes, "Review artifacts must be disjoint")
        paths.add(relative)
        hashes.add(sha)
        _text(ref["artifactId"], "artifactId")
        _text(ref["reviewerRunId"], "reviewerRunId")
        expected_type = f"strict_{_family(p)}_source_audit" if role == "source_auditor" else f"strict_{_family(p)}_evidence_correlation"
        _check(type(a["version"]) is int and a["version"] == 1 and a["artifactType"] == expected_type and a["caseId"] == p["caseId"] and a["artifactId"] == ref["artifactId"] and a["reviewerRole"] == role and a["reviewerRunId"] == ref["reviewerRunId"] and a["sourceReviewId"] == d["sourceReviewId"] and a["status"] == "terminal" and a["operational"] is False and ref["verdict"] == f"approve_agent_{_family(p)}_identity", "Independent artifact binding mismatch")
        _check(isinstance(a["results"], list) and isinstance(ref["reviewedCandidateIds"], list), "Review results must be lists")
        results = [_object(r, RESULT_FIELDS, "review result") for r in a["results"]]
        ids = [r["candidateId"] for r in results]
        if _family(p) == "source_only":
            _check(ids == [binding["candidateId"]], "Source-only independent review requires one exact pair")
        _check(ids == ref["reviewedCandidateIds"] and len(ids) == len(set(ids)) and binding["candidateId"] in ids, "Reviewed IDs mismatch")
        result = next(r for r in results if r["candidateId"] == binding["candidateId"])
        _check(result["binding"] == binding and result["verdict"] == f"approve_agent_{_family(p)}_identity" and result["debt"] == d["debt"], "Independent result binding/debt mismatch")
        _check(len(_text(result["rationale"], "review rationale")) >= 20, "Review rationale too short")
    return {"artifactPath": path, "artifactSha256": digest, "sourceReviewPath": strict_relative,
            "sourceReviewSha256": d["sourceReviewSha256"], "sourceReviewId": d["sourceReviewId"],
            "independentReviewRefs": refs, "debt": d["debt"], "binding": binding}


def _domain_state(connection, p: dict[str, Any]) -> dict[str, Any]:
    """Hash related rows, including private/dependency data without exporting it."""
    s = _profile(p)
    ids = {s["externalOccurrenceId"], s["sourceOccurrenceId"], s["targetPersonId"], p["caseId"], p["candidate"]["id"]}
    ids.discard(None)
    occurrences = [dict(r) if (r := _row(connection, "person_occurrences", oid)) else None for oid in (s["externalOccurrenceId"], s["sourceOccurrenceId"])]
    ids.update(r["source_item_id"] for r in occurrences if r)
    # Source hierarchy and its privacy/provenance rows are protected dependencies.
    pending_sources = [r["source_item_id"] for r in occurrences if r]
    visited_sources = set()
    while pending_sources:
        source_id = pending_sources.pop()
        if source_id in visited_sources:
            continue
        visited_sources.add(source_id)
        source = _row(connection, "source_items", source_id)
        if source and source["parent_id"]:
            ids.add(source["parent_id"])
            pending_sources.append(source["parent_id"])
    for r in occurrences:
        if r:
            ids.update(r[k] for k in ("reading_version_id", "transcript_segment_id") if r.get(k))
    # An explicit evidence/dependency allowlist prevents routine bookkeeping
    # (logs, next actions, search coverage, work items) from revoking identity.
    # Never seed the case ID: sharing a case is not an identity dependency.
    ids.discard(p["caseId"])
    protected_tables = {
        "persons", "person_names", "source_items", "artifacts", "artifact_derivations",
        "citations", "readings", "reading_versions", "transcript_segments",
        "field_readings", "reading_comparisons", "reading_review_decisions",
        "reading_review_inputs", "reading_pipeline_runs", "reading_pipeline_layers",
        "reading_pipeline_fields", "reading_pipeline_escalations", "source_assertions",
        "source_assertion_references", "informants", "events", "event_readings",
        "participations", "household_memberships", "privacy_labels", "private_fields",
        "private_attachments", "private_relationships", "family_notes", "external_sync_items",
    }
    available = {r[0] for r in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    ref_columns = {
        "id", "person_id", "occurrence_id", "source_item_id", "reading_version_id",
        "transcript_segment_id", "subject_person_id", "object_person_id", "subject_id",
        "object_id", "subject_ref", "object_ref", "reading_id", "artifact_id",
        "parent_artifact_id", "child_artifact_id", "assertion_id", "source_assertion_id",
        "decision_id", "run_id", "field_reading_id", "source_segment_id",
        "blind_version_id", "contextual_version_id", "reviewer_version_id", "event_id",
    }
    columns = {table: sorted({r[1] for r in connection.execute(f'PRAGMA table_info("{table}")')} & ref_columns)
               for table in sorted(protected_tables & available)}
    # Grow only through the selected evidence rows' own IDs and upward reading /
    # artifact references. Do not traverse arbitrary person/object references,
    # which would turn a shared household or source into a family-wide merge.
    upward_columns = {"reading_id", "artifact_id", "parent_artifact_id", "child_artifact_id",
                      "reading_version_id", "transcript_segment_id", "blind_version_id",
                      "contextual_version_id", "reviewer_version_id", "source_segment_id"}
    dependencies = {}
    while True:
        before_ids = set(ids)
        for table, relevant in columns.items():
            if not relevant:
                continue
            bound_ids = tuple(sorted(ids))
            marks = ','.join('?' for _ in bound_ids)
            where = ' OR '.join(f'"{column}" IN ({marks})' for column in relevant)
            rows = [dict(r) for r in connection.execute(
                f'SELECT * FROM "{table}" WHERE {where}', bound_ids * len(relevant))]
            dependencies[table] = sorted(rows, key=fingerprint)
            for row in rows:
                if isinstance(row.get("id"), str):
                    ids.add(row["id"])
                ids.update(row[column] for column in upward_columns & row.keys()
                           if isinstance(row[column], str) and row[column])
        if ids == before_ids:
            break
    # Protect only targets for the retained person or the two exact occurrences.
    # Own question criteria/status are evidence dependencies; other persons'
    # questions and ordinary case administration are not.
    targets = _rows(connection, """SELECT * FROM research_question_targets
        WHERE (target_type='person' AND target_id=?)
           OR (target_type='person_occurrence' AND target_id IN (?,?)) ORDER BY id""",
        (s["targetPersonId"], s["externalOccurrenceId"], s["sourceOccurrenceId"]))
    question_ids = tuple(sorted({row["question_id"] for row in targets}))
    dependencies["research_question_targets"] = targets
    dependencies["research_questions"] = _rows(connection,
        f'SELECT * FROM research_questions WHERE id IN ({",".join("?" for _ in question_ids)}) ORDER BY id',
        question_ids) if question_ids else []
    candidate_id = p["candidate"]["id"]
    endpoints = (s["externalOccurrenceId"], s["sourceOccurrenceId"])
    return {"candidate": dict(r) if (r := _row(connection, "identity_candidates", candidate_id)) else None,
            "decisions": _rows(connection, "SELECT * FROM identity_decisions WHERE candidate_id=? ORDER BY id", (candidate_id,)),
            "occurrences": occurrences,
            "dependencies": dependencies,
            "sharedCandidates": _rows(connection, "SELECT * FROM identity_candidates WHERE id<>? AND (occurrence_a_id IN (?,?) OR occurrence_b_id IN (?,?)) ORDER BY id", (candidate_id, *endpoints, *endpoints)),
            "sharedDecisions": _rows(connection, "SELECT d.* FROM identity_decisions d JOIN identity_candidates c ON c.id=d.candidate_id WHERE c.id<>? AND (c.occurrence_a_id IN (?,?) OR c.occurrence_b_id IN (?,?)) ORDER BY d.id", (candidate_id, *endpoints, *endpoints))}


def _guards(connection, p: dict[str, Any], state: dict[str, Any], *, applied: bool = False) -> list[str]:
    if _family(p) == "source_only":
        from .identity_source_only_curation import _source_only_guards
        return _source_only_guards(connection, p, state, applied=applied)
    blockers = []
    s = _profile(p)
    c = state["candidate"]
    expected_status = "resolved_same" if applied else "unresolved"
    if not c or c["status"] != expected_status:
        blockers.append("candidate_status_mismatch")
    if not c or {c["occurrence_a_id"], c["occurrence_b_id"]} != {s["externalOccurrenceId"], s["sourceOccurrenceId"]}:
        blockers.append("candidate_endpoints_mismatch")
    if any({r["occurrence_a_id"], r["occurrence_b_id"]} == {s["externalOccurrenceId"], s["sourceOccurrenceId"]} for r in state["sharedCandidates"]):
        blockers.append("ambiguous_duplicate_pair")
    ext, src = state["occurrences"]
    if not ext or ext["person_id"] != s["targetPersonId"]:
        blockers.append("external_target_mismatch")
    if not src or src["person_id"] != (s["targetPersonId"] if applied or _family(p) == "source_pair" else None):
        blockers.append("source_link_mismatch")
    person = _row(connection, "persons", s["targetPersonId"])
    if not person or person["status"] in {"merged", "living", "restricted"}:
        blockers.append("invalid_target")
    if not _row(connection, "research_cases", p["caseId"]):
        blockers.append("missing_case")
    for index, occurrence in enumerate((ext, src)):
        source = _row(connection, "source_items", occurrence["source_item_id"]) if occurrence else None
        if not source or source["item_type"] != ("external_tree_profile" if index == 0 and _family(p) == "source_profile" else "act"):
            blockers.append("source_type_mismatch")
    protected_ids = {s["targetPersonId"], s["sourceOccurrenceId"], s["externalOccurrenceId"]}
    if src:
        protected_ids.add(src["source_item_id"])
    if ext and _family(p) == "source_pair":
        protected_ids.add(ext["source_item_id"])
    privacy = state["dependencies"].get("privacy_labels", [])
    target_labels = [row for row in privacy if row["object_type"] == "person" and row["object_id"] == s["targetPersonId"]]
    if len(target_labels) != 1 or target_labels[0]["classification"] not in {"deceased", "public_historical"}:
        blockers.append("target_privacy_not_historical")
    external_labels = [row for row in privacy if ext and row["object_type"] == "source_item" and row["object_id"] == ext["source_item_id"]]
    if any(row["classification"] == "restricted" for row in external_labels) and (not target_labels or target_labels[0]["classification"] != "deceased"):
        blockers.append("restricted_wrapper_requires_deceased_target")
    for row in privacy:
        if row["object_id"] in protected_ids and row["classification"] not in {"public_historical", "deceased"}:
            blockers.append("private_or_living_endpoint")
    if not applied and state["decisions"]:
        blockers.append("candidate_already_decided")
    return sorted(set(blockers))


def _inspect(root: Path, connection, p: dict[str, Any]) -> dict[str, Any]:
    state = _domain_state(connection, p)
    blockers = _guards(connection, p, state)
    gate = _review_gate(root, p, _binding(connection, p))
    return {"operation": p["operation"], "operationId": p["operationId"], "caseId": p["caseId"],
            "candidateId": p["candidate"]["id"], "occurrenceIds": [_profile(p)["externalOccurrenceId"], _profile(p)["sourceOccurrenceId"]],
            "personId": _profile(p)["targetPersonId"], "identityDecisionTier": IDENTITY_DECISION_TIER,
            "fingerprint": fingerprint({"manifest": p, "state": state, "reviewGate": gate}),
            "applyAllowed": not blockers, "blockers": blockers, "commandIdRequired": True,
            "writeSet": ([] if _family(p) in {"source_pair", "source_only"} else [{"table": "person_occurrences", "id": _profile(p)["sourceOccurrenceId"], "action": "attach_null_person"}]) + [ {"table": "identity_candidates", "id": p["candidate"]["id"], "action": "update_status"}, {"table": "identity_decisions", "action": "insert_one"}, {"table": "curation_commands", "action": "insert_one"}, {"table": "audit_log", "action": "insert_one"}], "reviewGate": gate}


def identity_source_profile_resolution_preview(root: Path, manifest: Path, *, operation: str = OPERATION) -> dict[str, Any]:
    p = _load_manifest(manifest, operation=operation)
    with _current_connection(root, readonly=True) as connection:
        require_tables(connection, REQUIRED_TABLES)
        preview = _inspect(root, connection, p)
        preview.pop("reviewGate")
        return preview


def _request(p: dict[str, Any], expected: str) -> str:
    return fingerprint({"manifestFingerprint": fingerprint(p), "expectedFingerprint": expected})


def _audit(connection, p: dict[str, Any], command_type: str, result: dict[str, Any], actor: str) -> None:
    connection.execute("INSERT INTO audit_log(occurred_at,actor,action,object_type,object_id,before_json,after_json,rationale) VALUES (?,?,?,?,?,?,?,?)", (utc_now(), actor, command_type, "identity_candidate", result["candidateId"], json.dumps({"inputFingerprint": result["inputFingerprint"]}, sort_keys=True), json.dumps(result, ensure_ascii=False, sort_keys=True), p["rationale"]))


def apply_identity_source_profile_resolution(root: Path, manifest: Path, *, expected_fingerprint: str, command_id: str, operation: str = OPERATION) -> dict[str, Any]:
    p = _load_manifest(manifest, operation=operation)
    expected_fingerprint = _text(expected_fingerprint, "expectedFingerprint")
    command_id = _text(command_id, "commandId")
    with _current_connection(root, readonly=False) as connection:
        require_tables(connection, REQUIRED_TABLES)
        connection.execute("BEGIN IMMEDIATE")
        try:
            replay = replay_command(connection, command_id=command_id, command_type=p["operation"], object_type="identity_candidate", object_id=p["candidate"]["id"], request_fingerprint=_request(p, expected_fingerprint))
            if replay is not None:
                _validate_prior(root, connection, command_id)
                return replay
            preview = _inspect(root, connection, p)
            if preview["fingerprint"] != expected_fingerprint:
                raise IdentityOnlyCurationConflict("State changed after preview")
            _check(preview["applyAllowed"], "Blocked: " + ', '.join(preview["blockers"]))
            candidate_id = p["candidate"]["id"]
            # Capture the actual full unresolved row inside the apply transaction,
            # before any domain update. The result and audit bind this preimage;
            # it is never reconstructed or backfilled for legacy commands.
            candidate_before = dict(_row(connection, "identity_candidates", candidate_id))
            decision_id = stable_id("identity_decision", p["operation"], candidate_id, command_id)
            result = {"operation": p["operation"], "operationId": p["operationId"], "caseId": p["caseId"], "candidateId": candidate_id, "status": "resolved_same", "decision": "same", "decidedByKind": "agent", "identityDecisionTier": IDENTITY_DECISION_TIER, "decisionId": decision_id, "commandId": command_id, "inputFingerprint": expected_fingerprint, "manifestFingerprint": fingerprint(p), "manifest": p, "occurrenceIds": preview["occurrenceIds"], "personId": preview["personId"], "reviewGate": preview["reviewGate"], "writeSet": preview["writeSet"], "projectionStatus": "pending"}
            result["candidateBefore"] = candidate_before
            if _family(p) == "source_only":
                result["sourceIdentityGroupId"] = stable_id("source_identity", candidate_id, *sorted(preview["occurrenceIds"]))
            if _family(p) == "source_profile":
                updated = connection.execute("UPDATE person_occurrences SET person_id=? WHERE id=? AND person_id IS NULL", (preview["personId"], _profile(p)["sourceOccurrenceId"]))
                _check(updated.rowcount == 1, "Source occurrence no longer unlinked")
            updated = connection.execute("UPDATE identity_candidates SET status='resolved_same' WHERE id=? AND status='unresolved'", (candidate_id,))
            _check(updated.rowcount == 1, "Candidate no longer unresolved")
            # Command first: identity_decisions.command_id is an FK.
            result["postStateSha256"] = ""  # populated after decision; command result updated within transaction
            record_command(connection, command_id=command_id, command_type=p["operation"], object_type="identity_candidate", object_id=candidate_id, input_fingerprint=expected_fingerprint, request_fingerprint=_request(p, expected_fingerprint), actor=p["decidedBy"], rationale=p["rationale"], result=result)
            connection.execute("INSERT INTO identity_decisions(id,candidate_id,decision,decided_by,decided_at,rationale,command_id,input_fingerprint) VALUES (?,?,?,?,?,?,?,?)", (decision_id, candidate_id, "same", p["decidedBy"], utc_now(), p["rationale"], command_id, expected_fingerprint))
            result["postStateSha256"] = fingerprint(_domain_state(connection, p))
            connection.execute("UPDATE curation_commands SET result_json=? WHERE command_id=?", (json.dumps(result, ensure_ascii=False, sort_keys=True), command_id))
            _audit(connection, p, p["operation"], result, p["decidedBy"])
            return {**result, "alreadyApplied": False}
        except CommandEnvelopeConflict as error:
            raise IdentityOnlyCurationConflict(str(error)) from error


def _prior_core(root: Path, connection, command_id: str, *, context: _ValidationContext | None = None) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    command = connection.execute("SELECT * FROM curation_commands WHERE command_id=?", (command_id,)).fetchone()
    _check(command is not None and command["command_type"] in {OPERATION, PAIR_OPERATION, SOURCE_ONLY_OPERATION} and command["object_type"] == "identity_candidate", "Invalid prior command")
    result = json.loads(command["result_json"])
    _check(isinstance(result, dict), "Invalid prior result")
    p = result["manifest"]
    # Parse through exactly the same schema without writing to root.
    _validate_manifest_value(p)
    _check(result["operation"] == p["operation"] == command["command_type"] and result["candidateId"] == command["object_id"] == p["candidate"]["id"] and result["commandId"] == command_id and result["manifestFingerprint"] == fingerprint(p) and result["inputFingerprint"] == command["input_fingerprint"] and command["request_fingerprint"] == _request(p, result["inputFingerprint"]) and command["actor"] == p["decidedBy"] and command["rationale"] == p["rationale"], "Prior envelope mismatch")
    _check(result["status"] == "resolved_same" and result["decision"] == "same" and result["decidedByKind"] == "agent" and result["identityDecisionTier"] == IDENTITY_DECISION_TIER and result["personId"] == _profile(p)["targetPersonId"] and result["occurrenceIds"] == [_profile(p)["externalOccurrenceId"], _profile(p)["sourceOccurrenceId"]], "Prior result scope mismatch")
    state = _domain_state(connection, p)
    if "candidateBefore" in result:
        _candidate_preimage(result, state["candidate"])
    _check(not _guards(connection, p, state, applied=True), "Prior domain guards failed")
    gate = _review_gate(root, p, _binding(connection, p), context=context)
    _check(result["reviewGate"] == gate, "Prior review drift")
    if _family(p) == "source_only":
        _check("candidateBefore" in result, "Source-only candidate preimage required")
        _check(result["sourceIdentityGroupId"] == stable_id("source_identity", result["candidateId"], *sorted(result["occurrenceIds"])), "Source-only group backing ID mismatch")
        before_state = {**state, "candidate": result["candidateBefore"], "decisions": []}
        _check(result["inputFingerprint"] == fingerprint({"manifest": p, "state": before_state, "reviewGate": gate}), "Source-only preview preimage drift")
        expected_writes = [{"table": "identity_candidates", "id": p["candidate"]["id"], "action": "update_status"}, {"table": "identity_decisions", "action": "insert_one"}, {"table": "curation_commands", "action": "insert_one"}, {"table": "audit_log", "action": "insert_one"}]
        _check(result["writeSet"] == expected_writes, "Source-only forbidden write set")
    decisions = state["decisions"]
    _check(len(decisions) == 1 and decisions[0]["id"] == result["decisionId"] and decisions[0]["command_id"] == command_id and decisions[0]["decision"] == "same" and decisions[0]["input_fingerprint"] == command["input_fingerprint"] and decisions[0]["decided_by"] == p["decidedBy"] and decisions[0]["rationale"] == p["rationale"], "Decision drift")
    commands = _rows(connection, "SELECT command_id FROM curation_commands WHERE command_type IN (?,?,?) AND object_id=?", (OPERATION, PAIR_OPERATION, SOURCE_ONLY_OPERATION, result["candidateId"]))
    _check(len(commands) == 1, "Forked resolution chain")
    _validate_audit(connection, command, result, context=context)
    return p, result, state


def _validate_prior(root: Path, connection, command_id: str, *, validation_stack: tuple[str, ...] = (), context: _ValidationContext | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    _check(command_id not in validation_stack, "Cyclic dependency chain")
    snapshot = context if isinstance(context, _ProjectionValidationSnapshot) else None
    if snapshot is not None:
        snapshot.check_scope(root, connection)
        if command_id in snapshot.priors:
            return deepcopy(snapshot.priors[command_id])
    p, result, state = _prior_core(root, connection, command_id, context=context)
    revalidation_rows = _rows(connection, "SELECT command_id FROM curation_commands WHERE command_type=? AND object_id=?", (REVALIDATION_OPERATION, result["candidateId"]))
    if _family(p) == "source_only":
        _check(result["postStateSha256"] == fingerprint(state) and not revalidation_rows, "Source-only dependency drift or unsupported revalidation")
        if snapshot is not None:
            snapshot.priors[command_id] = deepcopy((p, result))
        return p, result
    if result["postStateSha256"] != fingerprint(state) or revalidation_rows:
        _validate_dependency_revalidation(root, connection, command_id, result, state, (*validation_stack, command_id), context=context)
    if snapshot is not None:
        snapshot.priors[command_id] = deepcopy((p, result))
    return p, result


def _validate_manifest_value(p: dict[str, Any]) -> None:
    _check(isinstance(p, dict), "Stored manifest must be an object")
    fields = (MANIFEST_FIELDS - {"sourceProfile"}) | {"sourceOnly"} if p.get("operation") == SOURCE_ONLY_OPERATION else ((MANIFEST_FIELDS - {"sourceProfile"}) | {"sourcePair"} if p.get("operation") == PAIR_OPERATION else MANIFEST_FIELDS)
    if p.get("operation") == SOURCE_ONLY_OPERATION and "historicalSourceAssessment" in p:
        fields = fields | {"historicalSourceAssessment"}
    _object(p, fields, "stored manifest")
    _check(type(p["version"]) is int and p["version"] == 1 and p["operation"] in {OPERATION, PAIR_OPERATION, SOURCE_ONLY_OPERATION}, "Invalid stored manifest")
    _object(p["candidate"], {"id", "expectedStatus", "decision"}, "candidate")
    if p["operation"] == SOURCE_ONLY_OPERATION:
        _object(p["sourceOnly"], {"occurrenceAId", "occurrenceBId"}, "sourceOnly")
        for value in p["sourceOnly"].values():
            _text(value, "sourceOnly endpoint")
        _check(p["sourceOnly"]["occurrenceAId"] != p["sourceOnly"]["occurrenceBId"], "Endpoints must differ")
        if "historicalSourceAssessment" in p:
            from .identity_source_only_curation import _validate_historical_assessment
            _validate_historical_assessment(p["historicalSourceAssessment"])
    elif p["operation"] == PAIR_OPERATION:
        _object(p["sourcePair"], {"occurrenceAId", "occurrenceBId", "targetPersonId"}, "sourcePair")
    else:
        _object(p["sourceProfile"], {"externalOccurrenceId", "sourceOccurrenceId", "targetPersonId"}, "sourceProfile")
    _object(p["agentReview"], {"artifactPath", "expectedSha256"}, "agentReview")
    _check(p["candidate"]["expectedStatus"] == "unresolved" and p["candidate"]["decision"] == "same", "Invalid stored candidate")


def _validate_audit(connection, command, result: dict[str, Any], *, context=None) -> None:
    if isinstance(context, _ProjectionValidationSnapshot):
        audits = context.audit_receipts(connection, command["command_type"], command["object_id"])
    else:
        audits = _rows(connection, "SELECT * FROM audit_log WHERE action=? AND object_type='identity_candidate' AND object_id=?", (command["command_type"], command["object_id"]))
    if command["command_type"] == REVALIDATION_OPERATION:
        envelopes = _rows(connection, "SELECT command_id FROM curation_commands WHERE command_type=? AND object_id=?", (REVALIDATION_OPERATION, command["object_id"]))
        _check(len(audits) == len(envelopes), "Revalidation audit receipt set mismatch")
        matching = []
        for row in audits:
            after = json.loads(row["after_json"])
            _check(isinstance(after, dict), "Revalidation audit result must be an object")
            if after.get("commandId") == command["command_id"]:
                matching.append(row)
        audits = matching
    _check(len(audits) == 1 and audits[0]["actor"] == command["actor"] and audits[0]["rationale"] == command["rationale"] and json.loads(audits[0]["after_json"]) == result and json.loads(audits[0]["before_json"]) == {"inputFingerprint": command["input_fingerprint"]}, "Audit chain mismatch")


def _ratification_gate(root: Path, p: dict[str, Any], prior_p: dict[str, Any], prior: dict[str, Any], *, context: _ValidationContext | None = None) -> dict[str, Any]:
    _check(p["operation"] == _rat_operation(prior_p), "Ratification topology mismatch")
    a, path, sha = _artifact(root, p["ratificationReview"], "ratification", context=context)
    a = _object(a, RAT_FIELDS, "ratification artifact")
    gate = prior["reviewGate"]
    expected = {"version": 1, "artifactType": f"strict_{_family(prior_p)}_agent_ratification", "ratificationId": p["operationId"], "caseId": p["caseId"], "candidateId": p["candidateId"], "priorCommandId": p["priorCommandId"], "priorCommandResultSha256": fingerprint(prior), "priorDecisionId": p["expectedDecisionId"], "agentReviewSha256": gate["artifactSha256"], "sourceReviewId": gate["sourceReviewId"], "sourceReviewSha256": gate["sourceReviewSha256"], "independentReviewRefs": gate["independentReviewRefs"], "binding": gate["binding"], "decision": "ratify_existing_agent_same", "decidedByKind": "agent", "identityDecisionTier": IDENTITY_DECISION_TIER, "decidedBy": p["ratifiedBy"], "rationale": p["rationale"], "debt": gate["debt"]}
    _check(type(a["version"]) is int and all(a[k] == v for k, v in expected.items()), "Ratification exact chain mismatch")
    _check(p["caseId"] == prior_p["caseId"] and p["candidateId"] == prior["candidateId"] and p["expectedDecisionId"] == prior["decisionId"], "Ratification scope mismatch")
    _iso_timestamp(a["decidedAt"], "ratification.decidedAt")
    _check(path not in {gate["artifactPath"], gate["sourceReviewPath"], *(r["artifactPath"] for r in gate["independentReviewRefs"])}, "Ratification artifact must be separate")
    return {"artifactPath": path, "artifactSha256": sha}


def _rat_inspect(root: Path, connection, p: dict[str, Any]) -> dict[str, Any]:
    prior_p, prior = _validate_prior(root, connection, p["priorCommandId"])
    gate = _ratification_gate(root, p, prior_p, prior)
    existing = _rows(connection, "SELECT * FROM curation_commands WHERE command_type=? AND object_id=? ORDER BY command_id", (p["operation"], p["candidateId"]))
    return {"operation": p["operation"], "operationId": p["operationId"], "candidateId": p["candidateId"], "priorCommandId": p["priorCommandId"], "priorDecisionId": p["expectedDecisionId"], "identityDecisionTier": IDENTITY_DECISION_TIER, "writeSet": [], "commandIdRequired": True, "applyAllowed": not existing, "blockers": ["candidate_already_ratified"] if existing else [], "fingerprint": fingerprint({"manifest": p, "prior": prior, "gate": gate, "existing": existing}), "gate": gate}


def identity_source_profile_agent_ratification_preview(root: Path, manifest: Path, *, operation: str = RATIFICATION_OPERATION) -> dict[str, Any]:
    p = _load_manifest(manifest, ratification=True, operation=operation)
    with _current_connection(root, readonly=True) as connection:
        require_tables(connection, REQUIRED_TABLES)
        preview = _rat_inspect(root, connection, p)
        preview.pop("gate")
        return preview


def apply_identity_source_profile_agent_ratification(root: Path, manifest: Path, *, expected_fingerprint: str, command_id: str, operation: str = RATIFICATION_OPERATION) -> dict[str, Any]:
    p = _load_manifest(manifest, ratification=True, operation=operation)
    expected_fingerprint = _text(expected_fingerprint, "expectedFingerprint")
    command_id = _text(command_id, "commandId")
    with _current_connection(root, readonly=False) as connection:
        require_tables(connection, REQUIRED_TABLES)
        connection.execute("BEGIN IMMEDIATE")
        try:
            replay = replay_command(connection, command_id=command_id, command_type=p["operation"], object_type="identity_candidate", object_id=p["candidateId"], request_fingerprint=_request(p, expected_fingerprint))
            if replay is not None:
                _validate_ratification(root, connection, command_id)
                return replay
            preview = _rat_inspect(root, connection, p)
            if preview["fingerprint"] != expected_fingerprint:
                raise IdentityOnlyCurationConflict("Ratification state changed after preview")
            _check(preview["applyAllowed"], "Candidate already ratified")
            result = {"operation": p["operation"], "status": "ratified_existing_agent_same", "commandId": command_id, "candidateId": p["candidateId"], "priorCommandId": p["priorCommandId"], "priorDecisionId": p["expectedDecisionId"], "identityDecisionTier": IDENTITY_DECISION_TIER, "writeSet": [], "manifest": p, "manifestFingerprint": fingerprint(p), "inputFingerprint": expected_fingerprint, "ratificationReviewArtifactPath": preview["gate"]["artifactPath"], "ratificationReviewSha256": preview["gate"]["artifactSha256"], "projectionStatus": "not_required"}
            record_command(connection, command_id=command_id, command_type=p["operation"], object_type="identity_candidate", object_id=p["candidateId"], input_fingerprint=expected_fingerprint, request_fingerprint=_request(p, expected_fingerprint), actor=p["ratifiedBy"], rationale=p["rationale"], result=result, projection_status="not_required")
            _audit(connection, p, p["operation"], result, p["ratifiedBy"])
            return {**result, "alreadyApplied": False}
        except CommandEnvelopeConflict as error:
            raise IdentityOnlyCurationConflict(str(error)) from error


def _ratification_core(root: Path, connection, command_id: str, prior_p: dict[str, Any], prior: dict[str, Any], *, context: _ValidationContext | None = None) -> dict[str, Any]:
    command = connection.execute("SELECT * FROM curation_commands WHERE command_id=?", (command_id,)).fetchone()
    _check(command is not None and command["command_type"] in {RATIFICATION_OPERATION, PAIR_RATIFICATION_OPERATION, SOURCE_ONLY_RATIFICATION_OPERATION} and command["object_type"] == "identity_candidate", "Invalid ratification command")
    result = json.loads(command["result_json"])
    _check(isinstance(result, dict), "Invalid ratification result")
    p = _object(result["manifest"], RAT_MANIFEST_FIELDS, "stored ratification manifest")
    _check(p["operation"] in {RATIFICATION_OPERATION, PAIR_RATIFICATION_OPERATION, SOURCE_ONLY_RATIFICATION_OPERATION} and type(p["version"]) is int and p["version"] == 1, "Invalid ratification manifest")
    _check(p["priorCommandId"] == prior["commandId"], "Ratification prior command mismatch")
    gate = _ratification_gate(root, p, prior_p, prior, context=context)
    _check(p["operation"] == _rat_operation(prior_p), "Ratification topology mismatch")
    _check(result["operation"] == p["operation"] == command["command_type"] and result["status"] == "ratified_existing_agent_same" and result["commandId"] == command_id and result["candidateId"] == p["candidateId"] == command["object_id"] and result["priorCommandId"] == p["priorCommandId"] and result["priorDecisionId"] == p["expectedDecisionId"] and result["identityDecisionTier"] == IDENTITY_DECISION_TIER and result["writeSet"] == [] and result["manifestFingerprint"] == fingerprint(p) and result["inputFingerprint"] == command["input_fingerprint"] and command["request_fingerprint"] == _request(p, result["inputFingerprint"]) and command["actor"] == p["ratifiedBy"] and command["rationale"] == p["rationale"] and result["ratificationReviewArtifactPath"] == gate["artifactPath"] and result["ratificationReviewSha256"] == gate["artifactSha256"], "Ratification envelope/result mismatch")
    siblings = _rows(connection, "SELECT command_id FROM curation_commands WHERE command_type IN (?,?,?) AND object_id=?", (RATIFICATION_OPERATION, PAIR_RATIFICATION_OPERATION, SOURCE_ONLY_RATIFICATION_OPERATION, result["candidateId"]))
    _check(len(siblings) == 1, "Duplicate/forked ratification")
    _validate_audit(connection, command, result, context=context)
    return prior



def _validate_ratification(root: Path, connection, command_id: str, *, validation_stack: tuple[str, ...] = (), context: _ValidationContext | None = None) -> dict[str, Any]:
    _check(command_id not in validation_stack, "Cyclic dependency chain")
    snapshot = context if isinstance(context, _ProjectionValidationSnapshot) else None
    if snapshot is not None:
        snapshot.check_scope(root, connection)
        if command_id in snapshot.ratifications:
            prior = snapshot.ratifications[command_id]
            _check(prior["commandId"] not in validation_stack, "Cyclic dependency chain")
            return deepcopy(prior)
    command = connection.execute("SELECT * FROM curation_commands WHERE command_id=?", (command_id,)).fetchone()
    _check(command is not None, "Missing ratification command")
    result = json.loads(command["result_json"])
    _check(isinstance(result, dict), "Invalid ratification result")
    prior_p, prior = _validate_prior(root, connection, result["priorCommandId"], validation_stack=validation_stack, context=context)
    validated = _ratification_core(root, connection, command_id, prior_p, prior, context=context)
    if snapshot is not None:
        snapshot.ratifications[command_id] = deepcopy(validated)
    return validated


# Revalidation is a separately reviewed, append-only delta authorization. It does
# not rewrite or weaken the original resolution/ratification/result fingerprints.
REVALIDATION_FIELDS = {"version", "operation", "operationId", "reviewId", "caseId", "candidate",
    "priorCommandId", "beforeState", "transitions", "currentStateSha256", "agentReview", "decidedBy", "rationale"}
BEFORE_FIELDS = {"version", "artifactType", "artifactId", "caseId", "candidateId", "priorCommandId",
    "priorCommandSha256", "priorResultSha256", "priorDecisionSha256", "priorRatificationCommandId",
    "priorRatificationCommandSha256", "priorRatificationResultSha256", "reviewGateSha256", "stateSha256", "state"}
TRANSITION_FIELDS = {"candidateId", "resolutionCommandId", "resolutionResultSha256",
    "ratificationCommandId", "ratificationResultSha256", "revalidationCommandId", "revalidationResultSha256"}
INTRODUCTION_FIELDS = {"candidateId", "candidateBeforeSha256"}


def _candidate_preimage(result: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    before = result.get("candidateBefore")
    _check(isinstance(before, dict) and isinstance(candidate, dict) and set(before) == set(candidate), "Missing or malformed immutable candidate preimage")
    _check(before["id"] == result["candidateId"] and before["status"] == "unresolved" and
           candidate == {**before, "status": "resolved_same"}, "Candidate preimage/status transformation drift")
    return before


def _introductions(p: dict[str, Any]) -> list[dict[str, Any]]:
    if "introducedCandidates" not in p:
        return []
    introductions = p["introducedCandidates"]
    _check(isinstance(introductions, list) and bool(introductions), "Exact nonempty introducedCandidates required")
    for ref in introductions:
        _object(ref, INTRODUCTION_FIELDS, "introduced candidate")
        _text(ref["candidateId"], "introduced candidateId")
        sha = _text(ref["candidateBeforeSha256"], "candidateBeforeSha256")
        _check(len(sha) == 64 and all(c in "0123456789abcdef" for c in sha), "Invalid candidate preimage hash")
    ids = [ref["candidateId"] for ref in introductions]
    _check(len(ids) == len(set(ids)), "Duplicate introduced candidate")
    transition_ids = [ref["candidateId"] for ref in p["transitions"]]
    _check(all(transition_ids.count(cid) == 1 for cid in ids), "Introduction requires one exact matching transition")
    return introductions


def _command(connection, command_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    row = connection.execute("SELECT * FROM curation_commands WHERE command_id=?", (command_id,)).fetchone()
    _check(row is not None, "Missing referenced command")
    command = dict(row)
    result = json.loads(command["result_json"])
    _check(isinstance(result, dict), "Referenced result must be an object")
    return command, result


def _command_sha(command: dict[str, Any]) -> str:
    """Hash immutable envelope content; projection refresh metadata is operational."""
    return fingerprint({k: v for k, v in command.items()
        if k not in {"projection_status", "projection_updated_at", "projection_error"}})


def _ratification_for(connection, p: dict[str, Any], prior: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = _rows(connection, "SELECT * FROM curation_commands WHERE command_type=? AND object_id=?",
                 (_rat_operation(p), prior["candidateId"]))
    _check(len(rows) == 1, "Exactly one prior ratification required")
    return _command(connection, rows[0]["command_id"])


def capture_identity_dependency_before_state(root: Path, connection, prior_command_id: str) -> dict[str, Any]:
    """Return a full, demonstrably original-valid frozen state without root writes.

    The caller stores this private-bearing artifact under reassessments and binds
    its bytes. It must be captured before transitions: its complete state hash
    must equal the original immutable postStateSha256. An already revalidated
    state cannot masquerade as the original valid-before state.
    """
    p, prior = _validate_prior(root, connection, prior_command_id)
    _check(_family(p) != "source_only", "Source-only dependency revalidation is unsupported")
    state = _domain_state(connection, p)
    _check(fingerprint(state) == prior["postStateSha256"], "Capture requires original exact prior-valid state")
    command, stored_prior = _command(connection, prior_command_id)
    rat_command, rat_result = _ratification_for(connection, p, prior)
    _validate_ratification(root, connection, rat_command["command_id"])
    decision = dict(_row(connection, "identity_decisions", prior["decisionId"]))
    return {"version": 1, "artifactType": "strict_identity_dependency_before_state",
        "artifactId": stable_id("identity_before", prior_command_id, fingerprint(state)),
        "caseId": p["caseId"], "candidateId": prior["candidateId"], "priorCommandId": prior_command_id,
        "priorCommandSha256": _command_sha(command), "priorResultSha256": fingerprint(stored_prior),
        "priorDecisionSha256": fingerprint(decision), "priorRatificationCommandId": rat_command["command_id"],
        "priorRatificationCommandSha256": _command_sha(rat_command), "priorRatificationResultSha256": fingerprint(rat_result),
        "reviewGateSha256": fingerprint(prior["reviewGate"]), "stateSha256": fingerprint(state), "state": state}


def _load_revalidation(manifest: Path) -> dict[str, Any]:
    return _validate_revalidation_value(_load_json(manifest, "dependency revalidation manifest"))


def _validate_revalidation_value(value: Any) -> dict[str, Any]:
    fields = REVALIDATION_FIELDS | ({"supersedesRevalidation", "introducedCandidates"} & value.keys() if isinstance(value, dict) else set())
    p = _object(value, fields, "revalidation manifest")
    _check(type(p["version"]) is int and p["version"] == 1 and p["operation"] == REVALIDATION_OPERATION, "Wrong revalidation version/operation")
    for field in ("operationId", "reviewId", "caseId", "priorCommandId", "currentStateSha256", "decidedBy", "rationale"):
        _text(p[field], field)
    _check(len(p["rationale"]) >= 20, "Revalidation rationale too short")
    candidate = _object(p["candidate"], {"id", "expectedStatus", "decision"}, "candidate")
    _check(candidate["expectedStatus"] == "resolved_same" and candidate["decision"] == "revalidate", "Revalidation only preserves an existing same decision")
    _text(candidate["id"], "candidate.id")
    for field in ("beforeState", "agentReview"):
        _object(p[field], {"artifactPath", "expectedSha256"}, field)
    _check(isinstance(p["transitions"], list) and bool(p["transitions"]), "Exact nonempty transitions required")
    for ref in p["transitions"]:
        _object(ref, TRANSITION_FIELDS, "transition")
        for field in TRANSITION_FIELDS - {"revalidationCommandId", "revalidationResultSha256"}:
            _text(ref[field], field)
        _check((ref["revalidationCommandId"] is None) == (ref["revalidationResultSha256"] is None), "Partial dependency revalidation reference")
        for field in ("revalidationCommandId", "revalidationResultSha256"):
            if ref[field] is not None:
                _text(ref[field], field)
    if "supersedesRevalidation" in p:
        predecessor = _object(p["supersedesRevalidation"], {"commandId", "resultSha256"}, "supersedesRevalidation")
        for field in predecessor:
            _text(predecessor[field], f"supersedesRevalidation.{field}")
    ids = [ref["candidateId"] for ref in p["transitions"]]
    _check(len(ids) == len(set(ids)), "Duplicate transition candidate")
    _introductions(p)
    return p


def identity_transition_reference(connection, *, resolution_command_id: str,
        ratification_command_id: str, revalidation_command_id: str | None = None) -> dict[str, Any]:
    """Construct exact result-hashed refs; admission validates the full chains."""
    _, resolution = _command(connection, resolution_command_id)
    _, ratification = _command(connection, ratification_command_id)
    _check(ratification["priorCommandId"] == resolution_command_id and
           ratification["candidateId"] == resolution["candidateId"], "Transition command scope mismatch")
    revalidation = None
    if revalidation_command_id is not None:
        _, revalidation = _command(connection, revalidation_command_id)
        _check(revalidation["priorCommandId"] == resolution_command_id, "Transition revalidation scope mismatch")
    return {"candidateId": resolution["candidateId"], "resolutionCommandId": resolution_command_id,
        "resolutionResultSha256": fingerprint(resolution), "ratificationCommandId": ratification_command_id,
        "ratificationResultSha256": fingerprint(ratification), "revalidationCommandId": revalidation_command_id,
        "revalidationResultSha256": fingerprint(revalidation) if revalidation is not None else None}


def _transition_chain(root: Path, connection, ref: dict[str, Any], validation_stack: tuple[str, ...], person_id: str, *, context: _ValidationContext | None = None) -> dict[str, Any]:
    _object(ref, TRANSITION_FIELDS, "transition")
    command, result = _command(connection, ref["resolutionCommandId"])
    rat_command, rat_result = _command(connection, ref["ratificationCommandId"])
    _check(fingerprint(result) == ref["resolutionResultSha256"] and fingerprint(rat_result) == ref["ratificationResultSha256"], "Transition result hash drift")
    _check(command["object_id"] == ref["candidateId"] and rat_command["object_id"] == ref["candidateId"] and rat_result["priorCommandId"] == ref["resolutionCommandId"], "Transition exact chain mismatch")
    validated = _validate_ratification(root, connection, ref["ratificationCommandId"], validation_stack=validation_stack, context=context)
    _check(validated["commandId"] == ref["resolutionCommandId"] and validated["candidateId"] == ref["candidateId"] and validated["personId"] == person_id, "Contradictory transition target")
    revalidations = _rows(connection, "SELECT * FROM curation_commands WHERE command_type=? AND object_id=?", (REVALIDATION_OPERATION, ref["candidateId"]))
    if ref["revalidationCommandId"] is None:
        _check(not revalidations and ref["revalidationResultSha256"] is None, "Missing explicit revalidation dependency reference")
    else:
        lineage = _revalidation_lineage(connection, ref["resolutionCommandId"], ref["candidateId"])
        _check(lineage and lineage[-1][0]["command_id"] == ref["revalidationCommandId"], "Stale/forked/missing explicit revalidation dependency")
        _, revalidation = _command(connection, ref["revalidationCommandId"])
        _check(fingerprint(revalidation) == ref["revalidationResultSha256"] and revalidation["priorCommandId"] == ref["resolutionCommandId"], "Revalidation dependency hash/scope drift")
    return validated


def _allowed_dependency_delta(before: dict[str, Any], after: dict[str, Any], transitions: list[dict[str, Any]], validated: list[dict[str, Any]], connection, introductions: list[dict[str, Any]] | None = None) -> None:
    # Every non-enumerated state field, row and column remains byte-semantically
    # identical. No ignore list and no person/source/private transition exist.
    _check(set(before) == set(after), "Dependency state schema drift")
    _check({k: v for k, v in before.items() if k not in {"sharedCandidates", "sharedDecisions"}} ==
           {k: v for k, v in after.items() if k not in {"sharedCandidates", "sharedDecisions"}}, "Non-authorized dependency drift")
    old = {r["id"]: r for r in before["sharedCandidates"]}
    new = {r["id"]: r for r in after["sharedCandidates"]}
    introduced = {ref["candidateId"]: ref for ref in introductions or []}
    _check(len(old) == len(before["sharedCandidates"]) and len(new) == len(after["sharedCandidates"]) and
           not set(old) & set(introduced) and set(new) == set(old) | set(introduced), "Shared candidate set drift")
    transition_ids = {ref["candidateId"] for ref in transitions}
    validated_by_id = {result["candidateId"]: result for result in validated}
    _check(len(validated_by_id) == len(validated) and set(validated_by_id) == transition_ids, "Transition validation set mismatch")
    changed = {cid for cid in old if old[cid] != new[cid]}
    _check(changed | set(introduced) == transition_ids, "Unlisted or irrelevant transition")
    for cid in changed:
        expected = {**old[cid], "status": "resolved_same"}
        _check(old[cid]["status"] == "unresolved" and new[cid] == expected, "Only unresolved-to-resolved_same status transition permitted")
    endpoints = {row["id"] for row in before["occurrences"]}
    for cid, ref in introduced.items():
        preimage = _candidate_preimage(validated_by_id[cid], new[cid])
        _check(fingerprint(preimage) == ref["candidateBeforeSha256"] and
               bool({preimage["occurrence_a_id"], preimage["occurrence_b_id"]} & endpoints), "Introduced candidate preimage hash/shared endpoint mismatch")
    old_decisions = {r["id"]: r for r in before["sharedDecisions"]}
    new_decisions = {r["id"]: r for r in after["sharedDecisions"]}
    _check(len(old_decisions) == len(before["sharedDecisions"]) and len(new_decisions) == len(after["sharedDecisions"]), "Duplicate shared decision")
    _check(all(new_decisions.get(did) == row for did, row in old_decisions.items()), "Old shared decision drift")
    added = {did: row for did, row in new_decisions.items() if did not in old_decisions}
    _check(set(added) == {r["decisionId"] for r in validated}, "Unlisted/missing additive shared decisions")
    for result in validated:
        decision = _row(connection, "identity_decisions", result["decisionId"])
        _check(decision is not None and added[result["decisionId"]] == dict(decision) and decision["command_id"] == result["commandId"] and decision["candidate_id"] in transition_ids, "Shared decision is not exact approved transition")


def _revalidation_lineage(connection, prior_command_id: str, candidate_id: str) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    rows = _rows(connection, "SELECT command_id FROM curation_commands WHERE command_type=? AND object_id=?", (REVALIDATION_OPERATION, candidate_id))
    nodes = {row["command_id"]: _command(connection, row["command_id"]) for row in rows}
    if not nodes:
        return []
    roots = []
    children = {}
    for command_id, (command, result) in nodes.items():
        p = _validate_revalidation_value(result["manifest"])
        _check(command["object_type"] == "identity_candidate" and result["candidateId"] == p["candidate"]["id"] == candidate_id and result["priorCommandId"] == p["priorCommandId"] == prior_command_id, "Revalidation lineage scope mismatch")
        predecessor = p.get("supersedesRevalidation")
        if predecessor is None:
            roots.append(command_id)
            continue
        parent_id = predecessor["commandId"]
        _check(parent_id in nodes and parent_id != command_id, "Missing/self-cyclic revalidation predecessor")
        _check(fingerprint(nodes[parent_id][1]) == predecessor["resultSha256"], "Revalidation predecessor result hash drift")
        _check(parent_id not in children, "Forked revalidation successor")
        children[parent_id] = command_id
    _check(len(roots) == 1, "Revalidation lineage has forked roots or cycle")
    chain = []
    seen = set()
    current = roots[0]
    while current is not None:
        _check(current not in seen, "Cyclic revalidation predecessor chain")
        seen.add(current)
        chain.append(nodes[current])
        current = children.get(current)
    _check(len(seen) == len(nodes), "Disconnected/forked/cyclic revalidation lineage")
    return chain


def _historical_revalidation_receipt(root: Path, connection, command: dict[str, Any], result: dict[str, Any], prior: dict[str, Any], *, context: _ValidationContext | None = None) -> None:
    """Validate immutable prior receipt; it never grants current admission.

    Later revisions replace only current admission, so their old before/after
    review binding and input fingerprint stay historical and byte-bound.
    """
    p = _validate_revalidation_value(result["manifest"])
    introductions = _introductions(p)
    gate = _review_gate(root, p, result["reviewGate"]["binding"], historical_receipt=True, context=context)
    binding = gate["binding"]
    before, _, before_sha = _artifact(root, p["beforeState"], "historical frozen before-state", context=context)
    _object(before, BEFORE_FIELDS, "historical before artifact")
    _check(before["stateSha256"] == prior["postStateSha256"] == fingerprint(before["state"]) and
        before["priorResultSha256"] == fingerprint(prior) and before_sha == binding["beforeArtifactSha256"] and
        binding["priorResultSha256"] == fingerprint(prior) and binding["priorCommandId"] == prior["commandId"] and
        binding["candidateId"] == prior["candidateId"] and binding["transitions"] == p["transitions"] and
        binding["currentStateSha256"] == p["currentStateSha256"] == result["postStateSha256"] and
        ("introducedCandidates" in binding) == ("introducedCandidates" in p) and
        binding.get("introducedCandidates") == p.get("introducedCandidates") and
        binding.get("supersedesRevalidation") == p.get("supersedesRevalidation"), "Historical revalidation binding drift")
    # Reconstruct the historical authorized after-state from the original
    # before-state and immutable transition decisions; no live drift is ignored.
    historical = json.loads(json.dumps(before["state"]))
    validated = []
    for ref in p["transitions"]:
        transition_p, transition, _ = _prior_core(root, connection, ref["resolutionCommandId"], context=context)
        _, ratification = _command(connection, ref["ratificationCommandId"])
        _ratification_core(root, connection, ref["ratificationCommandId"], transition_p, transition, context=context)
        _check(fingerprint(transition) == ref["resolutionResultSha256"] and fingerprint(ratification) == ref["ratificationResultSha256"] and
            transition["candidateId"] == ref["candidateId"] and transition["personId"] == prior["personId"], "Historical transition receipt drift")
        if ref["revalidationCommandId"] is not None:
            _, old_revalidation = _command(connection, ref["revalidationCommandId"])
            _check(fingerprint(old_revalidation) == ref["revalidationResultSha256"] and old_revalidation["priorCommandId"] == ref["resolutionCommandId"], "Historical child revalidation hash drift")
        matches = [r for r in historical["sharedCandidates"] if r["id"] == ref["candidateId"]]
        if ref["candidateId"] in {r["candidateId"] for r in introductions}:
            _check(not matches, "Historical introduction was already in before state")
            preimage = _candidate_preimage(transition, dict(_row(connection, "identity_candidates", ref["candidateId"])))
            historical["sharedCandidates"].append({**preimage, "status": "resolved_same"})
        else:
            _check(len(matches) == 1, "Historical transition is not in before state")
            matches[0]["status"] = "resolved_same"
        historical["sharedDecisions"].append(dict(_row(connection, "identity_decisions", transition["decisionId"])))
        validated.append(transition)
    historical["sharedDecisions"].sort(key=lambda r: r["id"])
    historical["sharedCandidates"].sort(key=lambda r: r["id"])
    _allowed_dependency_delta(before["state"], historical, p["transitions"], validated, connection, introductions)
    _check(fingerprint(historical) == p["currentStateSha256"], "Historical full-state proof drift")
    dependency = {"binding": binding, "prior": prior, "currentStateSha256": p["currentStateSha256"]}
    expected_input = fingerprint({"manifest": p, "context": dependency, "reviewGate": gate})
    _check(result["operation"] == command["command_type"] == REVALIDATION_OPERATION and command["object_type"] == "identity_candidate" and
        result["status"] == "revalidated_existing_agent_same" and result["commandId"] == command["command_id"] and
        result["candidateId"] == command["object_id"] == prior["candidateId"] and result["priorCommandId"] == prior["commandId"] and
        result["writeSet"] == [] and result["identityDecisionTier"] == IDENTITY_DECISION_TIER and result["reviewGate"] == gate and
        result["manifestFingerprint"] == fingerprint(p) and result["inputFingerprint"] == command["input_fingerprint"] == expected_input and
        command["request_fingerprint"] == _request(p, expected_input) and command["actor"] == p["decidedBy"] and command["rationale"] == p["rationale"], "Historical revalidation envelope drift")
    _validate_audit(connection, command, result, context=context)


def _revalidation_predecessor(root: Path, connection, p: dict[str, Any], prior: dict[str, Any], *, context: _ValidationContext | None = None) -> dict[str, Any] | None:
    lineage = _revalidation_lineage(connection, p["priorCommandId"], prior["candidateId"])
    predecessor = p.get("supersedesRevalidation")
    if predecessor is None:
        _check(not lineage or lineage[0][1]["manifest"] == p, "Missing revalidation predecessor")
        return None
    predecessor = _object(predecessor, {"commandId", "resultSha256"}, "supersedesRevalidation")
    _check(bool(lineage), "Missing revalidation predecessor")
    recorded = lineage[-1][1]["manifest"] == p
    parent = lineage[-2] if recorded and len(lineage) > 1 else lineage[-1]
    _check(parent[0]["command_id"] == predecessor["commandId"] and fingerprint(parent[1]) == predecessor["resultSha256"], "Wrong/stale revalidation predecessor")
    for command, result in lineage[:-1] if recorded else lineage:
        _historical_revalidation_receipt(root, connection, command, result, prior, context=context)
    old = {r["candidateId"]: r for r in parent[1]["manifest"]["transitions"]}
    new = {r["candidateId"]: r for r in p["transitions"]}
    _check(set(old) <= set(new), "Successor must preserve cumulative transitions")
    immutable_ref_fields = TRANSITION_FIELDS - {"revalidationCommandId", "revalidationResultSha256"}
    _check(all(all(old[cid][field] == new[cid][field] for field in immutable_ref_fields) for cid in old), "Successor rewrites original transition commands")
    old_introduced = {r["candidateId"]: r for r in _introductions(parent[1]["manifest"])}
    new_introduced = {r["candidateId"]: r for r in _introductions(p)}
    _check(all(new_introduced.get(cid) == ref for cid, ref in old_introduced.items()), "Successor rewrites or omits cumulative introductions")
    if new_introduced:
        _check(p["beforeState"] == parent[1]["manifest"]["beforeState"], "Successor changes original before-state reference")
    _check(p["currentStateSha256"] != parent[1]["postStateSha256"] or p["transitions"] != parent[1]["manifest"]["transitions"] or
           p.get("introducedCandidates") != parent[1]["manifest"].get("introducedCandidates"), "No-op duplicate revalidation successor")
    return predecessor


def _dependency_context(root: Path, connection, p: dict[str, Any], validation_stack: tuple[str, ...], *, context: _ValidationContext | None = None) -> dict[str, Any]:
    prior_p, prior, current = _prior_core(root, connection, p["priorCommandId"], context=context)
    _check(_family(prior_p) != "source_only", "Source-only dependency revalidation is unsupported")
    before, path, sha = _artifact(root, p["beforeState"], "frozen prior-valid state", context=context)
    before = _object(before, BEFORE_FIELDS, "frozen before artifact")
    command, _ = _command(connection, p["priorCommandId"])
    rat_command, rat_result = _ratification_for(connection, prior_p, prior)
    _ratification_core(root, connection, rat_command["command_id"], prior_p, prior, context=context)
    decision = dict(_row(connection, "identity_decisions", prior["decisionId"]))
    expected = {"version": 1, "artifactType": "strict_identity_dependency_before_state", "caseId": prior_p["caseId"],
        "candidateId": prior["candidateId"], "priorCommandId": prior["commandId"], "priorCommandSha256": _command_sha(command),
        "priorResultSha256": fingerprint(prior), "priorDecisionSha256": fingerprint(decision),
        "priorRatificationCommandId": rat_command["command_id"], "priorRatificationCommandSha256": _command_sha(rat_command),
        "priorRatificationResultSha256": fingerprint(rat_result), "reviewGateSha256": fingerprint(prior["reviewGate"]),
        "stateSha256": prior["postStateSha256"]}
    _check(type(before["version"]) is int and all(before[k] == v for k, v in expected.items()) and fingerprint(before["state"]) == before["stateSha256"], "Frozen state does not prove exact immutable prior-valid state")
    _text(before["artifactId"], "before.artifactId")
    _check(p["caseId"] == prior_p["caseId"] and p["candidate"]["id"] == prior["candidateId"] and p["currentStateSha256"] == fingerprint(current), "Stale revalidation scope/current state")
    _check(p["priorCommandId"] in validation_stack, "Revalidation validation stack missing prior")
    introductions = _introductions(p)
    validated = [_transition_chain(root, connection, ref, validation_stack, prior["personId"], context=context) for ref in p["transitions"]]
    _allowed_dependency_delta(before["state"], current, p["transitions"], validated, connection, introductions)
    binding = {"caseId": p["caseId"], "candidateId": prior["candidateId"], "priorCommandId": prior["commandId"],
        "priorCommandSha256": before["priorCommandSha256"], "priorResultSha256": before["priorResultSha256"],
        "priorDecisionSha256": before["priorDecisionSha256"], "priorRatificationCommandId": before["priorRatificationCommandId"],
        "priorRatificationCommandSha256": before["priorRatificationCommandSha256"], "priorRatificationResultSha256": before["priorRatificationResultSha256"],
        "reviewGateSha256": before["reviewGateSha256"], "beforeArtifactPath": path, "beforeArtifactSha256": sha,
        "beforeStateSha256": before["stateSha256"], "currentStateSha256": fingerprint(current), "transitions": p["transitions"]}
    if "introducedCandidates" in p:
        binding["introducedCandidates"] = introductions
    predecessor = _revalidation_predecessor(root, connection, p, prior, context=context)
    if predecessor is not None:
        binding["supersedesRevalidation"] = predecessor
    return {"binding": binding, "prior": prior, "currentStateSha256": fingerprint(current)}


def identity_dependency_revalidation_binding(root: Path, connection, *, prior_command_id: str,
        before_state: dict[str, Any], transitions: list[dict[str, Any]], supersedes_revalidation: dict[str, Any] | None = None,
        introduced_candidates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Read-only exact binding constructor for fresh strict C2/C3 artifacts."""
    prior_p, prior, state = _prior_core(root, connection, prior_command_id)
    p = {"priorCommandId": prior_command_id, "caseId": prior_p["caseId"], "candidate": {"id": prior["candidateId"]},
         "beforeState": before_state, "transitions": transitions, "currentStateSha256": fingerprint(state)}
    if supersedes_revalidation is not None:
        p["supersedesRevalidation"] = supersedes_revalidation
    if introduced_candidates is not None:
        p["introducedCandidates"] = introduced_candidates
    return _dependency_context(root, connection, p, (prior_command_id,))["binding"]


def _revalidation_inspect(root: Path, connection, p: dict[str, Any], *, validation_stack: tuple[str, ...], context: _ValidationContext | None = None) -> dict[str, Any]:
    dependency = _dependency_context(root, connection, p, validation_stack, context=context)
    gate = _review_gate(root, p, dependency["binding"], context=context)
    _check(gate["debt"] == dependency["prior"]["reviewGate"]["debt"], "Revalidation must retain original debt")
    return {"operation": REVALIDATION_OPERATION, "operationId": p["operationId"], "caseId": p["caseId"],
        "candidateId": p["candidate"]["id"], "priorCommandId": p["priorCommandId"], "identityDecisionTier": IDENTITY_DECISION_TIER,
        "writeSet": [], "commandIdRequired": True, "applyAllowed": True, "blockers": [],
        "fingerprint": fingerprint({"manifest": p, "context": dependency, "reviewGate": gate}),
        "reviewGate": gate, "postStateSha256": dependency["currentStateSha256"]}


def identity_dependency_revalidation_preview(root: Path, manifest: Path) -> dict[str, Any]:
    p = _load_revalidation(manifest)
    with _current_connection(root, readonly=True) as connection:
        require_tables(connection, REQUIRED_TABLES)
        preview = _revalidation_inspect(root, connection, p, validation_stack=(p["priorCommandId"],))
        existing = _rows(connection, "SELECT command_id FROM curation_commands WHERE command_type=? AND object_id=?", (REVALIDATION_OPERATION, p["candidate"]["id"]))
        is_recorded = any(_command(connection, row["command_id"])[1].get("manifest") == p for row in existing)
        allowed = not is_recorded and (not existing or "supersedesRevalidation" in p)
        preview.update(applyAllowed=allowed, blockers=["revalidation_already_recorded_or_missing_predecessor"] if not allowed else [])
        preview.pop("reviewGate")
        return preview


def apply_identity_dependency_revalidation(root: Path, manifest: Path, *, expected_fingerprint: str, command_id: str) -> dict[str, Any]:
    p = _load_revalidation(manifest)
    expected_fingerprint = _text(expected_fingerprint, "expectedFingerprint")
    command_id = _text(command_id, "commandId")
    with _current_connection(root, readonly=False) as connection:
        require_tables(connection, REQUIRED_TABLES)
        connection.execute("BEGIN IMMEDIATE")
        try:
            replay = replay_command(connection, command_id=command_id, command_type=REVALIDATION_OPERATION,
                object_type="identity_candidate", object_id=p["candidate"]["id"], request_fingerprint=_request(p, expected_fingerprint))
            if replay is not None:
                _, prior, current = _prior_core(root, connection, p["priorCommandId"])
                lineage = _revalidation_lineage(connection, p["priorCommandId"], p["candidate"]["id"])
                _validate_dependency_revalidation(root, connection, p["priorCommandId"], prior, current, (p["priorCommandId"],))
                if lineage[-1][0]["command_id"] != command_id:
                    matching = [node for node in lineage if node[0]["command_id"] == command_id]
                    _check(len(matching) == 1, "Replay command is not an immutable lineage receipt")
                    _historical_revalidation_receipt(root, connection, matching[0][0], matching[0][1], prior)
                    return {**replay, "historicalReceipt": True, "currentRevalidationCommandId": lineage[-1][0]["command_id"]}
                return replay
            preview = _revalidation_inspect(root, connection, p, validation_stack=(p["priorCommandId"],))
            if preview["fingerprint"] != expected_fingerprint:
                raise IdentityOnlyCurationConflict("Revalidation state changed after preview")
            existing = _rows(connection, "SELECT command_id FROM curation_commands WHERE command_type=? AND object_id=?", (REVALIDATION_OPERATION, p["candidate"]["id"]))
            _check(not any(_command(connection, row["command_id"])[1].get("manifest") == p for row in existing), "Duplicate revalidation")
            _check(not existing or "supersedesRevalidation" in p, "Missing revalidation predecessor")
            result = {"operation": REVALIDATION_OPERATION, "status": "revalidated_existing_agent_same", "commandId": command_id,
                "candidateId": p["candidate"]["id"], "priorCommandId": p["priorCommandId"], "identityDecisionTier": IDENTITY_DECISION_TIER,
                "writeSet": [], "manifest": p, "manifestFingerprint": fingerprint(p), "inputFingerprint": expected_fingerprint,
                "reviewGate": preview["reviewGate"], "postStateSha256": preview["postStateSha256"], "projectionStatus": "not_required"}
            record_command(connection, command_id=command_id, command_type=REVALIDATION_OPERATION, object_type="identity_candidate",
                object_id=p["candidate"]["id"], input_fingerprint=expected_fingerprint, request_fingerprint=_request(p, expected_fingerprint),
                actor=p["decidedBy"], rationale=p["rationale"], result=result, projection_status="not_required")
            _audit(connection, p, REVALIDATION_OPERATION, result, p["decidedBy"])
            return {**result, "alreadyApplied": False}
        except CommandEnvelopeConflict as error:
            raise IdentityOnlyCurationConflict(str(error)) from error


def _validate_dependency_revalidation(root: Path, connection, prior_command_id: str, prior: dict[str, Any], current: dict[str, Any], validation_stack: tuple[str, ...], *, context: _ValidationContext | None = None) -> None:
    lineage = _revalidation_lineage(connection, prior_command_id, prior["candidateId"])
    _check(bool(lineage), "Post-state drift requires explicit revalidation")
    for old_command, old_result in lineage[:-1]:
        _historical_revalidation_receipt(root, connection, old_command, old_result, prior, context=context)
    command, result = lineage[-1]
    p = _validate_revalidation_value(result["manifest"])
    _check(p["priorCommandId"] == prior_command_id and p["operation"] == REVALIDATION_OPERATION, "Revalidation prior mismatch")
    preview = _revalidation_inspect(root, connection, p, validation_stack=validation_stack, context=context)
    _check(result["operation"] == command["command_type"] == REVALIDATION_OPERATION and command["object_type"] == "identity_candidate" and
        result["status"] == "revalidated_existing_agent_same" and result["commandId"] == command["command_id"] and
        result["candidateId"] == command["object_id"] == prior["candidateId"] and result["priorCommandId"] == prior_command_id and
        result["identityDecisionTier"] == IDENTITY_DECISION_TIER and result["writeSet"] == [] and
        result["manifestFingerprint"] == fingerprint(p) and result["inputFingerprint"] == command["input_fingerprint"] == preview["fingerprint"] and
        command["request_fingerprint"] == _request(p, result["inputFingerprint"]) and command["actor"] == p["decidedBy"] and command["rationale"] == p["rationale"] and
        result["reviewGate"] == preview["reviewGate"] and result["postStateSha256"] == fingerprint(current) == preview["postStateSha256"], "Revalidation envelope/result/state drift")
    _validate_audit(connection, command, result, context=context)


def validated_source_profile_identity_chains(root: Path, connection, *, context=None) -> dict[str, Any]:
    """Read-only projection admission. Typed IDs come from envelope/candidate joins.

    Malformed or drifting commands still reserve their typed endpoints so generic
    person/status paths cannot grant approval. Only exactly one fully validated
    resolution plus one append-only ratification yields an admitted chain.
    A supplied projection snapshot is owned and finalized by the enclosing
    export; mutable admission contexts cannot be reused here.
    """
    if context is not None:
        _check_projection_context(root, connection, context)
        return _validated_source_profile_identity_chains(root, connection, context=context)
    output: dict[str, Any] = {"chains": [], "typedOccurrenceIds": [], "typedCandidateIds": []}
    try:
        with _projection_validation_snapshot(root, connection) as context:
            output = _validated_source_profile_identity_chains(root, connection, context=context)
    except _ProjectionSnapshotChanged:
        output["chains"] = []
    return output


def _validated_source_profile_identity_chains(root: Path, connection, *, context) -> dict[str, Any]:
    output: dict[str, Any] = {"chains": [], "typedOccurrenceIds": [], "typedCandidateIds": []}
    commands = _rows(connection, "SELECT * FROM curation_commands WHERE command_type IN (?,?,?,?,?) ORDER BY command_id", (OPERATION, RATIFICATION_OPERATION, PAIR_OPERATION, PAIR_RATIFICATION_OPERATION, REVALIDATION_OPERATION))
    typed_candidates = {c["object_id"] for c in commands}
    # Reserve the decision-backed candidate even if an envelope object ID drifts.
    for command in commands:
        typed_candidates.update(r[0] for r in connection.execute(
            "SELECT candidate_id FROM identity_decisions WHERE command_id=?", (command["command_id"],)))
    typed_occurrences = set()
    for cid in typed_candidates:
        candidate = _row(connection, "identity_candidates", cid)
        if candidate:
            typed_occurrences.update((candidate["occurrence_a_id"], candidate["occurrence_b_id"]))
    output["typedCandidateIds"] = sorted(typed_candidates)
    output["typedOccurrenceIds"] = sorted(typed_occurrences)
    for command in commands:
        if command["command_type"] not in {RATIFICATION_OPERATION, PAIR_RATIFICATION_OPERATION}:
            continue
        try:
            prior = _validate_ratification(root, connection, command["command_id"], context=context)
        except (ValueError, KeyError, TypeError, OSError, StopIteration):
            continue
        chain = {"candidateId": prior["candidateId"], "commandId": prior["commandId"], "decisionId": prior["decisionId"], "occurrenceIds": prior["occurrenceIds"], "personId": prior["personId"], "identityDecisionTier": IDENTITY_DECISION_TIER}
        if prior["operation"] == PAIR_OPERATION:
            chain["topology"] = "source_pair"
        output["chains"].append(chain)
    return output


__all__ = [
    "IdentityOnlyCurationError", "IdentityOnlyCurationConflict", "OPERATION",
    "RATIFICATION_OPERATION", "IDENTITY_DECISION_TIER", "source_profile_review_binding",
    "identity_source_profile_resolution_preview", "apply_identity_source_profile_resolution",
    "identity_source_profile_agent_ratification_preview", "apply_identity_source_profile_agent_ratification",
    "validated_source_profile_identity_chains", "PAIR_OPERATION", "PAIR_RATIFICATION_OPERATION", "REVALIDATION_OPERATION",
    "capture_identity_dependency_before_state", "identity_dependency_revalidation_binding", "identity_transition_reference",
    "identity_dependency_revalidation_preview", "apply_identity_dependency_revalidation",
]
