"""Read-only projection of explicitly bound family testimony onto tree cards."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ..workspace import research_root
from .privacy import RESTRICTED_CLASSES


def _require(condition: bool) -> None:
    if not condition:
        raise ValueError("Nieprawidłowe backing wiedzy rodzinnej drzewa")


def _endpoint(connection: sqlite3.Connection, card: dict[str, Any]) -> str:
    canonical_id = card.get("canonicalPersonId")
    if "canonicalPersonId" in card:
        _require(isinstance(canonical_id, str) and bool(canonical_id.strip()))
        return canonical_id
    matches = set()
    for row in connection.execute(
        """SELECT object_id,value_json FROM private_fields
            WHERE object_type='person' AND field_name='presentation.treePersonId'"""
    ):
        try:
            value = json.loads(row["value_json"])
        except (TypeError, ValueError):
            continue
        if value == card["id"]:
            matches.add(row["object_id"])
    _require(len(matches) == 1)
    return next(iter(matches))


def project_tree_family_knowledge(
    root: Path,
    connection: sqlite3.Connection,
    tree: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Expose generic testimony status without publishing private backing IDs.

    This does not establish a source identity, canonical relationship or marriage.
    Masked cards retain their role IDs; only public endpoints require public SQL
    privacy labels. Optional bindings are validated completely before projection.
    """
    if tree is None:
        return None
    for family in tree.get("families", []):
        family.pop("familyKnowledgeEvidence", None)
        family.pop("familyKnowledgeEdges", None)
        family.pop("familyKnowledgeStatus", None)
    raw = json.loads(
        (research_root(root) / "presentation/family-tree.json").read_text(encoding="utf-8")
    )
    cards = {card["id"]: card for card in raw["people"]}
    public_cards = {card["id"]: card for card in tree["people"]}
    raw_families = {family["id"]: family for family in raw["families"]}
    projected = []
    for family in tree.get("families", []):
        durable = raw_families[family["id"]]
        if "familyKnowledgeEvidence" not in durable:
            continue
        bindings = durable["familyKnowledgeEvidence"]
        _require(isinstance(bindings, list))
        expected = {(parent, child) for parent in durable["parentIds"]
                    for child in durable["childIds"]}
        _require(bool(expected) and all(parent != child for parent, child in expected))
        _require(family.get("parentIds") == durable["parentIds"]
                 and family.get("childIds") == durable["childIds"])
        seen = set()
        edges = []
        for binding in bindings:
            _require(isinstance(binding, dict))
            parent_id, child_id = binding.get("parentId"), binding.get("childId")
            _require(isinstance(parent_id, str) and isinstance(child_id, str))
            pair = parent_id, child_id
            _require(pair in expected and pair not in seen)
            seen.add(pair)
            relationship_id = binding.get("privateRelationshipId")
            _require(isinstance(relationship_id, str) and bool(relationship_id.strip()))
            parent = _endpoint(connection, cards[parent_id])
            child = _endpoint(connection, cards[child_id])
            _require(parent != child)
            row = connection.execute(
                """SELECT subject_person_id,object_person_id,relationship_type,
                          provenance_kind,verification_status
                     FROM private_relationships WHERE id=?""", (relationship_id,),
            ).fetchone()
            _require(row is not None and row["subject_person_id"] == parent
                     and row["object_person_id"] == child
                     and row["relationship_type"] == "parent"
                     and row["provenance_kind"] == "family_testimony"
                     and row["verification_status"] == "family_testimony")
            for tree_id, canonical_id in ((parent_id, parent), (child_id, child)):
                _require(tree_id in public_cards)
                if public_cards[tree_id].get("privacy") != "masked_living":
                    labels = connection.execute(
                        """SELECT classification FROM privacy_labels
                            WHERE object_type='person' AND object_id=?""", (canonical_id,),
                    )
                    _require(not any(label["classification"] in RESTRICTED_CLASSES for label in labels))
            edges.append({
                "parentId": parent_id,
                "childId": child_id,
                "familyKnowledgeStatus": "family_known",
                "basis": "family_testimony",
                "summary": "Relacja rodzic–dziecko potwierdzona wiedzą rodzinną.",
            })
        projected.append((family, edges, seen == expected))
    for family, edges, complete in projected:
        family["familyKnowledgeEdges"] = edges
        if complete:
            family["familyKnowledgeStatus"] = "family_known"
            family["status"] = "family_known"
    return tree
