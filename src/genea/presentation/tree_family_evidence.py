"""Read-only backing validation for explicitly noncanonical existing families."""
from __future__ import annotations

import json
import hashlib
import sqlite3
from pathlib import Path
from typing import Any

from ..command_envelope import fingerprint
from ..workspace import research_root
from .privacy import PrivacyContext


_PARENT_TYPES = {"parent_of", "father_of", "mother_of"}
_ASSERTION_TYPES = {"source-stated-relationship", "external_tree_relationship"}
_CLAIM_CHILD_GRAMMAR = {
    "source_grammar_born_of", "source_grammar_paternal_reference_via_his_wife",
}
_INACTIVE = {"retracted", "superseded", "rejected", "Disproven"}


def _require(condition: bool) -> None:
    # Validation errors must not themselves disclose private backing IDs.
    if not condition:
        raise ValueError("Nieprawidłowe lub niepełne backing rodziny źródłowej")


def _ids(value: Any) -> list[str]:
    _require(isinstance(value, list))
    _require(all(isinstance(item, str) and bool(item.strip()) for item in value))
    _require(len(value) == len(set(value)))
    return sorted(value)


def _json(value: Any) -> dict[str, Any]:
    try:
        result = json.loads(value or "{}")
    except (ValueError, TypeError):
        return {}
    return result if isinstance(result, dict) else {}


def _reference_matches(value: Any, occurrence: sqlite3.Row) -> bool:
    return value in {
        occurrence["id"], f"occurrence:{occurrence['id']}",
        f"person_occurrence:{occurrence['id']}",
        *([occurrence["person_id"], f"person:{occurrence['person_id']}"]
          if occurrence["person_id"] else []),
    }


def _endpoint(
    root: Path,
    connection: sqlite3.Connection,
    card: dict[str, Any],
    occurrence_id: str,
    endpoint_evidence: Any,
) -> sqlite3.Row:
    occurrence = connection.execute(
        """SELECT po.id,po.person_id,po.source_item_id,po.verification_status,
                  si.metadata_json,si.item_type
             FROM person_occurrences po JOIN source_items si ON si.id=po.source_item_id
            WHERE po.id=?""", (occurrence_id,),
    ).fetchone()
    _require(occurrence is not None and occurrence["verification_status"] not in _INACTIVE)
    _require(occurrence_id in _ids(card.get("occurrenceIds", [])))
    canonical_id = card.get("canonicalPersonId")
    if canonical_id and canonical_id == occurrence["person_id"]:
        return occurrence
    # An occurrence-only card refers to that occurrence in its own identity scope.
    if not canonical_id:
        return occurrence
    # A protected private card can have a technically reviewed source crosswalk.
    # This validates exact persisted UID/xref, never names or a new identity merge.
    _require(isinstance(endpoint_evidence, dict))
    _require(endpoint_evidence.get("kind") == "private_source_crosswalk")
    review_refs = _ids(endpoint_evidence.get("reviewRefs", []))
    _require(bool(review_refs))
    for reference in review_refs:
        review_path = (root / reference).resolve()
        _require(review_path.is_relative_to(root.resolve()) and review_path.is_file())
    fields = {
        row["field_name"]: json.loads(row["value_json"])
        for row in connection.execute(
            """SELECT field_name,value_json FROM private_fields
                WHERE object_type='person' AND object_id=?
                  AND field_name IN ('presentation.treePersonId','source.gedcomUid','source.gedcomXref')""",
            (canonical_id,),
        )
    }
    _require(fields.get("presentation.treePersonId") == card["id"])
    metadata = _json(occurrence["metadata_json"])
    provenance = metadata.get("provenance", {})
    _require(isinstance(provenance, dict))
    _require(bool(fields.get("source.gedcomUid")) and bool(fields.get("source.gedcomXref")))
    _require(occurrence["item_type"] == "external_tree_profile")
    raw_path_value = provenance.get("rawPath")
    _require(isinstance(raw_path_value, str) and bool(raw_path_value))
    raw_path = (root / raw_path_value).resolve()
    _require(raw_path.is_relative_to(root.resolve()) and raw_path.is_file())
    start, end = provenance.get("lineFrom"), provenance.get("lineTo")
    _require(type(start) is int and type(end) is int and 1 <= start <= end and end - start <= 2000)
    digest = hashlib.sha256()
    with raw_path.open("rb") as raw_file:
        for block in iter(lambda: raw_file.read(1024 * 1024), b""):
            digest.update(block)
    _require(digest.hexdigest() == provenance.get("rawSha256"))
    lines = []
    with raw_path.open(encoding="utf-8-sig") as raw_file:
        for line_number, line in enumerate(raw_file, 1):
            if line_number > end:
                break
            if line_number >= start:
                lines.append(line.strip())
    _require(len(lines) == end - start + 1 and bool(lines))
    record = lines[0].split()
    _require(len(record) == 3 and record[0] == "0" and record[2] == "INDI")
    _require(record[1] == fields["source.gedcomXref"] == provenance.get("gedcomRecordId"))
    _require(not any(line.startswith("0 ") for line in lines[1:]))
    uids = [parts[2] for line in lines[1:] if len(parts := line.split(maxsplit=2)) == 3
            and parts[0] == "1" and parts[1] in {"_UID", "UID"}]
    _require(uids == [fields["source.gedcomUid"]])
    return occurrence


def _evidence_summary(edges: list[dict[str, Any]]) -> dict[str, Any]:
    kinds = {edge["evidence"]["basis"] for edge in edges}
    claims = sorted({value for edge in edges for value in edge["evidence"]["claimStatuses"]})
    assertions = sorted({value for edge in edges for value in edge["evidence"]["verificationStatuses"]})
    reviews = sorted({value for edge in edges for value in edge["evidence"]["reviewStatuses"]})
    evidence_types = sorted({value for edge in edges for value in edge["evidence"]["evidenceTypes"]})
    refs = {(ref["entityType"], ref["entityId"])
            for edge in edges for ref in edge["evidence"]["backingRefs"]}
    return {
        "basis": next(iter(kinds)) if len(kinds) == 1 else "mixed",
        "status": "source_reported",
        "tier": "Probable" if claims and set(claims) == {"Probable"} else "imported_unverified",
        "claimStatuses": claims,
        "verificationStatuses": assertions,
        "reviewStatuses": reviews,
        "evidenceTypes": evidence_types,
        "backingRefs": [{"entityType": kind, "entityId": oid} for kind, oid in sorted(refs)],
        "backingReferenceCounts": {
            kind: sum(edge["evidence"]["backingReferenceCounts"][kind] for edge in edges)
            for kind in ("source_assertion", "claim", "source_item")
        },
        "evidenceRedacted": any(edge["evidence"]["evidenceRedacted"] for edge in edges),
        "auditFingerprint": fingerprint([edge["evidence"]["auditFingerprint"] for edge in edges]),
    }


def project_tree_family_evidence(
    root: Path,
    connection: sqlite3.Connection,
    privacy: PrivacyContext,
    tree: dict[str, Any] | None,
    *,
    allowed_source_ids: set[str],
) -> dict[str, Any] | None:
    """Validate private exact endpoints, then replace evidence with a safe projection.

    Families lacking the optional canonicalRelationship field retain legacy behavior.
    Every parent/child pair of an explicit false family must have one binding.
    The connection is used exclusively for SELECTs; no research state is written.
    """
    if tree is None or not any(
        family.get("canonicalRelationship") is False for family in tree.get("families", [])
    ):
        return tree
    raw = json.loads((research_root(root) / "presentation" / "family-tree.json").read_text(encoding="utf-8"))
    cards = {card["id"]: card for card in raw["people"]}
    public_cards = {card["id"]: card for card in tree["people"]}
    raw_families = {family["id"]: family for family in raw["families"]}
    projected = []
    for family in tree["families"]:
        if family.get("canonicalRelationship") is not False:
            continue
        durable = raw_families[family["id"]]
        _require(durable.get("canonicalRelationship") is False)
        _require(durable.get("relationshipIdentityStatus") == "unresolved")
        expected = {(parent, child) for parent in _ids(durable["parentIds"])
                    for child in _ids(durable["childIds"])}
        _require(bool(expected) and all(parent != child for parent, child in expected))
        bindings = durable.get("relationshipEvidence")
        _require(isinstance(bindings, list) and len(bindings) == len(expected))
        seen = set()
        edges = []
        family_refs = set()
        for binding in bindings:
            _require(isinstance(binding, dict))
            pair = (binding.get("parentId"), binding.get("childId"))
            _require(pair in expected and pair not in seen)
            seen.add(pair)
            parent_id, child_id = pair
            endpoint_evidence = binding.get("endpointEvidence") or {}
            _require(isinstance(endpoint_evidence, dict))
            parent = _endpoint(root, connection, cards[parent_id], binding.get("parentOccurrenceId"), endpoint_evidence.get("parent"))
            child = _endpoint(root, connection, cards[child_id], binding.get("childOccurrenceId"), endpoint_evidence.get("child"))
            _require(parent["id"] != child["id"])
            assertion_ids = _ids(binding.get("sourceAssertionIds", []))
            direct_assertion_ids = set(assertion_ids)
            claim_ids = _ids(binding.get("claimIds", []))
            _require(bool(assertion_ids or claim_ids))
            claim_statuses = set()
            evidence_types = set()
            claim_assertion_ids = set()
            for claim_id in claim_ids:
                claim = connection.execute("SELECT * FROM claims WHERE id=?", (claim_id,)).fetchone()
                _require(claim is not None and claim["status"] not in _INACTIVE)
                _require(claim["predicate"] in _PARENT_TYPES)
                _require(_reference_matches(claim["subject_ref"], parent) and _reference_matches(claim["object_ref"], child))
                claim_statuses.add(claim["status"])
                support_rows = list(connection.execute(
                    "SELECT source_assertion_id,evidence_type FROM claim_evidence WHERE claim_id=? AND stance='supports' ORDER BY source_assertion_id", (claim_id,),
                ))
                support = [row["source_assertion_id"] for row in support_rows]
                evidence_types.update(row["evidence_type"] for row in support_rows)
                claim_assertion_ids.update(support)
                _require(bool(support))
                assertion_ids = sorted(set(assertion_ids) | set(support))
            assertions = []
            for assertion_id in assertion_ids:
                assertion = connection.execute("SELECT * FROM source_assertions WHERE id=?", (assertion_id,)).fetchone()
                _require(assertion is not None and assertion["verification_status"] not in _INACTIVE)
                literal_parent = (assertion["assertion_type"] in _ASSERTION_TYPES
                                  and assertion["predicate"] in _PARENT_TYPES
                                  and assertion["subject_ref"] == parent["id"]
                                  and assertion["object_text"] == child["id"])
                claimed_grammar = (assertion_id in claim_assertion_ids
                                   and assertion_id not in direct_assertion_ids
                                   and assertion["assertion_type"] == "source-stated-relationship"
                                   and assertion["predicate"] in _CLAIM_CHILD_GRAMMAR
                                   and assertion["subject_ref"] == child["id"]
                                   and assertion["object_text"] == parent["id"])
                _require(literal_parent or claimed_grammar)
                _require(connection.execute("SELECT 1 FROM source_items WHERE id=?", (assertion["source_item_id"],)).fetchone() is not None)
                assertions.append(assertion)
            sources = sorted({assertion["source_item_id"] for assertion in assertions})
            verification = sorted({assertion["verification_status"] for assertion in assertions})
            reviews = sorted({value for assertion in assertions
                              for value in [_json(assertion["metadata_json"]).get("reviewStatus")]
                              if value in {"agent_approved_human_pending", "human_confirmed", "agent_consensus"}})
            refs = [("source_assertion", oid) for oid in assertion_ids] + [("claim", oid) for oid in claim_ids] + [("source_item", oid) for oid in sources]
            family_refs.update(refs)
            protected = (
                any(public_cards[oid].get("privacy") == "masked_living" for oid in pair)
                or any(privacy.blocked_reference(row["id"]) or privacy.blocked("person_occurrence", row["id"]) or privacy.blocked("person", row["person_id"]) for row in (parent, child))
                or any(privacy.blocked("source_item", row["source_item_id"])
                       or row["source_item_id"] not in allowed_source_ids for row in (parent, child))
                or any(privacy.blocked(kind, oid) or privacy.blocked_reference(oid) for kind, oid in refs)
                or not set(sources).issubset(allowed_source_ids)
            )
            evidence = {
                "basis": "claim" if claim_ids else "source_assertion",
                "status": "source_reported",
                "tier": "Probable" if claim_statuses == {"Probable"} else "imported_unverified",
                "claimStatuses": sorted(claim_statuses), "verificationStatuses": verification,
                "reviewStatuses": reviews,
                "evidenceTypes": sorted(evidence_types),
                "backingRefs": [] if protected else [{"entityType": kind, "entityId": oid} for kind, oid in refs],
                "backingReferenceCounts": {kind: sum(ref_kind == kind for ref_kind, _ in refs) for kind in ("source_assertion", "claim", "source_item")},
                "evidenceRedacted": protected,
                "auditFingerprint": fingerprint({
                    "binding": binding,
                    "assertions": [dict(row) for row in assertions],
                    "claimStatuses": sorted(claim_statuses),
                }),
            }
            edges.append({
                "parentId": parent_id, "childId": child_id,
                "canonicalRelationship": False, "relationshipIdentityStatus": "unresolved",
                "sourceIds": [] if protected else sources,
                "sourceAssertionIds": [] if protected else assertion_ids,
                "claimIds": [] if protected else claim_ids,
                "parentOccurrenceId": None if protected else parent["id"],
                "childOccurrenceId": None if protected else child["id"],
                "evidence": evidence,
            })
        summary = _evidence_summary(edges)
        summary["backingReferenceCounts"] = {
            kind: sum(ref_kind == kind for ref_kind, _ in family_refs)
            for kind in ("source_assertion", "claim", "source_item")
        }
        projected.append((family, edges, summary))
    # Fail the whole export before introducing any partially validated family.
    for family, edges, summary in projected:
        family["relationshipEvidence"] = edges
        family["evidence"] = summary
        family["status"] = "Probable" if summary["tier"] == "Probable" else "source-reported"
        family["sourceIds"] = sorted({source for edge in edges for source in edge["sourceIds"]})
    return tree
