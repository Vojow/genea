from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from ..workspace import stable_id
from ..identity_curation import IDENTITY_REVIEW_THRESHOLD
from .common import event_group, json_object
from .privacy import PrivacyContext


@dataclass(frozen=True)
class RelationshipsProjection:
    relationships: list[dict[str, Any]]
    identity_candidates: list[dict[str, Any]]
    identity_groups: list[dict[str, Any]]
    households: list[dict[str, Any]]


def _role_tokens(value: Any) -> set[str]:
    if not isinstance(value, str):
        return set()
    return {
        " ".join(token.casefold().split())
        for token in value.split(";")
        if token.strip()
    }


def _event_role_relationships(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Project only relations encoded by the event-role contract.

    This does not parse names or narrative prose.  It recognizes exact,
    controlled participant-role tokens within a source event and retains all
    backing IDs.  The result remains source-reported presentation evidence;
    it is never a canonical family relationship or an identity decision.
    """

    relationships: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()

    def participants_with_token(
        participants: list[dict[str, Any]], tokens: set[str]
    ) -> list[dict[str, Any]]:
        return [
            participant
            for participant in participants
            if _role_tokens(participant.get("role")) & tokens
        ]

    def append(
        event: dict[str, Any],
        subject: dict[str, Any],
        predicate: str,
        object_: dict[str, Any],
    ) -> None:
        subject_id = subject.get("occurrenceId")
        object_id = object_.get("occurrenceId")
        source_id = event.get("sourceId")
        event_key = event.get("recordKey") or event.get("id") or "no-event"
        if not subject_id or not object_id or subject_id == object_id:
            return
        key = (source_id or "", event_key, subject_id, predicate, object_id)
        if key in seen:
            return
        seen.add(key)
        backing_event_ids = list(
            dict.fromkeys(event.get("mergedEventIds") or [event.get("id")])
        )
        relationships.append(
            {
                "id": stable_id(
                    "event_participant_relation",
                    source_id or "no-source",
                    event_key,
                    subject_id,
                    predicate,
                    object_id,
                ),
                "fromOccurrenceId": subject_id,
                "toOccurrenceId": object_id,
                "type": predicate,
                "date": event.get("dateFrom") or event.get("date"),
                "place": event.get("place"),
                "confidence": None,
                "status": "source-reported",
                "verificationStatus": event.get("status") or "imported_unverified",
                "readingCertainty": None,
                "identityStatus": "none",
                "sourceId": source_id,
                "backingEventIds": [item for item in backing_event_ids if item],
                "backingOccurrenceIds": [subject_id, object_id],
                "roleEvidence": [
                    {"occurrenceId": subject_id, "role": subject.get("role")},
                    {"occurrenceId": object_id, "role": object_.get("role")},
                ],
                "provenance": "event_participation_role",
                "derivationStatus": "deterministic_role_contract",
            }
        )

    for event in events:
        participants = event.get("participants") or []
        group = event_group(event.get("type"))
        if group == "birth-baptism":
            children = participants_with_token(
                participants,
                {
                    "dziecko",
                    "child",
                    "baptized_child",
                    "ochrzczony",
                    "ochrzczona",
                    "ochrzczona dziewczynka",
                    "ochrzczony chłopiec",
                },
            )
            fathers = participants_with_token(
                participants,
                {
                    "ojciec",
                    "father",
                    "child_father_named",
                    "ojciec dziecka",
                    "ojciec i zgłaszający",
                },
            )
            mothers = participants_with_token(
                participants, {"matka", "mother", "child_mother_named", "matka dziecka"}
            )
            for child in children:
                for father in fathers:
                    append(event, father, "father_of", child)
                for mother in mothers:
                    append(event, mother, "mother_of", child)
        elif group == "marriage":
            grooms = participants_with_token(participants, {"pan młody", "groom"})
            brides = participants_with_token(participants, {"panna młoda", "bride"})
            fathers_of_groom = participants_with_token(
                participants,
                {"ojciec pana młodego", "father of groom", "groom_father_named"},
            )
            mothers_of_groom = participants_with_token(
                participants,
                {"matka pana młodego", "mother of groom", "groom_mother_named"},
            )
            fathers_of_bride = participants_with_token(
                participants,
                {"ojciec panny młodej", "father of bride", "bride_father_named"},
            )
            mothers_of_bride = participants_with_token(
                participants,
                {"matka panny młodej", "mother of bride", "bride_mother_named"},
            )
            for groom in grooms:
                for father in fathers_of_groom:
                    append(event, father, "father_of", groom)
                for mother in mothers_of_groom:
                    append(event, mother, "mother_of", groom)
                for bride in brides:
                    append(event, groom, "spouse_of", bride)
            for bride in brides:
                for father in fathers_of_bride:
                    append(event, father, "father_of", bride)
                for mother in mothers_of_bride:
                    append(event, mother, "mother_of", bride)
    return relationships


def project_relationships(
    connection: sqlite3.Connection,
    privacy: PrivacyContext,
    *,
    allowed_occurrence_ids: set[str],
    allowed_person_ids: set[str],
    events: list[dict[str, Any]] | None = None,
) -> RelationshipsProjection:
    sanitized_external_occurrence_ids = {
        row["id"]
        for row in connection.execute(
            """SELECT po.id,po.person_id,po.source_item_id,po.verification_status,
                      p.status AS person_status,p.preferred_name,
                      si.item_type AS source_item_type,
                      EXISTS(
                          SELECT 1
                            FROM identity_candidates ic
                            JOIN identity_decisions idc ON idc.candidate_id=ic.id
                           WHERE ic.status='resolved_same'
                             AND idc.decision='same'
                             AND (ic.occurrence_a_id=po.id OR ic.occurrence_b_id=po.id)
                      ) AS explicit_same_identity
                 FROM person_occurrences po
                 JOIN persons p ON p.id=po.person_id
                 JOIN source_items si ON si.id=po.source_item_id
                ORDER BY po.id"""
        )
        if (
            row["id"] in allowed_occurrence_ids
            and row["verification_status"] not in {"retracted", "superseded"}
            and (
                row["person_status"] == "candidate"
                or row["explicit_same_identity"]
            )
            and row["preferred_name"]
            and row["source_item_type"] == "external_tree_profile"
            and privacy.labels.get(("person", row["person_id"]))
            in {"public_historical", "deceased"}
            and privacy.labels.get(("source_item", row["source_item_id"]))
            == "restricted"
            and not privacy.blocked("person_occurrence", row["id"])
        )
    }
    relation_occurrences: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in connection.execute(
        """SELECT m.analysis_id,m.entity_id,olm.occurrence_id
           FROM mentions m
           JOIN occurrence_legacy_mentions olm ON olm.legacy_mention_id=m.id"""
    ):
        if row["occurrence_id"] in allowed_occurrence_ids:
            relation_occurrences[(row["analysis_id"], row["entity_id"])].add(
                row["occurrence_id"]
            )
    relationships: list[dict[str, Any]] = []
    seen_relationships: set[tuple[str, str, str, str]] = set()
    for row in connection.execute(
        """SELECT id,analysis_id,subject_entity_id,predicate,object_entity_id,
                  date_text,place_text,confidence
           FROM relations ORDER BY id"""
    ):
        subjects = relation_occurrences.get(
            (row["analysis_id"], row["subject_entity_id"]), set()
        )
        objects = relation_occurrences.get(
            (row["analysis_id"], row["object_entity_id"]), set()
        )
        for subject_id in subjects:
            for object_id in objects:
                key = (row["analysis_id"], subject_id, row["predicate"], object_id)
                if subject_id == object_id or key in seen_relationships:
                    continue
                seen_relationships.add(key)
                relationships.append(
                    {
                        "id": stable_id("frontend_relation", *key),
                        "fromOccurrenceId": subject_id,
                        "toOccurrenceId": object_id,
                        "type": row["predicate"],
                        "date": row["date_text"],
                        "place": row["place_text"],
                        "confidence": row["confidence"],
                        "status": "source-reported",
                        "provenance": "legacy_relation",
                    }
                )

    # New agent-first mappings preserve literal family statements as atomic
    # source assertions.  They are presentation evidence, not canonical family
    # links, but omitting them makes an act look as if it contained no family.
    for row in connection.execute(
        """SELECT id,source_item_id,transcript_segment_id,subject_ref,predicate,
                  object_text,information_quality,reading_certainty,
                  verification_status,metadata_json
             FROM source_assertions
            WHERE assertion_type='source-stated-relationship'
              AND legacy_relation_id IS NULL
            ORDER BY source_item_id,id"""
    ):
        subject_id = row["subject_ref"]
        object_id = row["object_text"]
        if (
            subject_id not in allowed_occurrence_ids
            or object_id not in allowed_occurrence_ids
            or subject_id == object_id
            or row["verification_status"] in {"retracted", "superseded"}
        ):
            continue
        key = ("source_assertion", subject_id, row["predicate"], object_id)
        if key in seen_relationships:
            continue
        seen_relationships.add(key)
        metadata = json_object(row["metadata_json"])
        relationships.append(
            {
                "id": row["id"],
                "fromOccurrenceId": subject_id,
                "toOccurrenceId": object_id,
                "type": row["predicate"],
                "date": metadata.get("date") or metadata.get("dateText"),
                "place": metadata.get("place") or metadata.get("placeText"),
                "confidence": None,
                "status": "source-reported",
                "verificationStatus": row["verification_status"],
                "readingCertainty": row["reading_certainty"],
                "informationQuality": row["information_quality"],
                "sourceId": row["source_item_id"],
                "segmentId": row["transcript_segment_id"],
                "literal": metadata.get("literal"),
                "identityStatus": metadata.get("identityDecision")
                or metadata.get("identityStatus")
                or "none",
                "provenance": "source_assertion",
            }
        )

    # External collaborative trees are useful source-reported leads, but they
    # are neither canonical family links nor identity decisions.  Public
    # backing stays explicit; when the source is restricted, only a minimal
    # edge between independently allowed sanitized candidate shells survives.
    for row in connection.execute(
        """SELECT id,source_item_id,transcript_segment_id,subject_ref,predicate,
                  object_text,metadata_json
             FROM source_assertions
            WHERE assertion_type='external_tree_relationship'
              AND legacy_relation_id IS NULL
              AND verification_status NOT IN ('retracted','superseded')
            ORDER BY source_item_id,id"""
    ):
        subject_id = row["subject_ref"]
        object_id = row["object_text"]
        restricted_source = privacy.blocked("source_item", row["source_item_id"])
        sanitized_restricted_edge = bool(
            restricted_source
            and subject_id in sanitized_external_occurrence_ids
            and object_id in sanitized_external_occurrence_ids
        )
        if (
            subject_id not in allowed_occurrence_ids
            or object_id not in allowed_occurrence_ids
            or subject_id == object_id
            or privacy.blocked("source_assertion", row["id"])
            or (restricted_source and not sanitized_restricted_edge)
            or (
                not sanitized_restricted_edge
                and (
                    privacy.blocked_reference(subject_id)
                    or privacy.blocked_reference(object_id)
                )
            )
        ):
            continue
        if sanitized_restricted_edge:
            relationships.append(
                {
                    "id": stable_id(
                        "redacted_external_tree_relation",
                        subject_id,
                        row["predicate"],
                        object_id,
                    ),
                    "fromOccurrenceId": subject_id,
                    "toOccurrenceId": object_id,
                    "type": row["predicate"],
                    "status": "source-reported",
                    "verificationStatus": "imported_unverified",
                    "identityStatus": "unresolved",
                    "resolutionStatus": "unresolved",
                    "relationshipScope": "source_reported_external_tree",
                    "canonicalRelationship": False,
                    "noAutoMerge": True,
                    "noCanonicalRelationship": True,
                    "restrictedBacking": True,
                    "backingOccurrenceIds": [subject_id, object_id],
                }
            )
            continue
        key = (row["id"], subject_id, row["predicate"], object_id)
        if key in seen_relationships:
            continue
        seen_relationships.add(key)
        metadata = json_object(row["metadata_json"])
        relationships.append(
            {
                "id": row["id"],
                "fromOccurrenceId": subject_id,
                "toOccurrenceId": object_id,
                "type": row["predicate"],
                "date": metadata.get("date") or metadata.get("dateText"),
                "place": metadata.get("place") or metadata.get("placeText"),
                "confidence": None,
                "status": "source-reported",
                "verificationStatus": "imported_unverified",
                "readingCertainty": "Ambiguous",
                "informationQuality": "Indeterminable",
                "sourceId": row["source_item_id"],
                "segmentId": row["transcript_segment_id"],
                "sourceAssertionId": row["id"],
                "backingAssertionIds": [row["id"]],
                "backingSourceIds": [row["source_item_id"]],
                "backingOccurrenceIds": [subject_id, object_id],
                "literal": metadata.get("literal") or metadata.get("statement"),
                "identityStatus": "unresolved",
                "resolutionStatus": "unresolved",
                "relationshipScope": "source_reported_external_tree",
                "canonicalRelationship": False,
                "provenance": "external_tree_assertion",
                "derivationStatus": "imported_external_tree",
                "externalTreeService": metadata.get("service"),
                "noAutoMerge": True,
                "noCanonicalRelationship": True,
            }
        )

    for relationship in _event_role_relationships(events or []):
        if (
            relationship["fromOccurrenceId"] not in allowed_occurrence_ids
            or relationship["toOccurrenceId"] not in allowed_occurrence_ids
        ):
            continue
        relationships.append(relationship)

    identity_candidates = []
    for row in connection.execute(
        """SELECT ic.id,ic.occurrence_a_id,ic.occurrence_b_id,ic.score,
                  ic.basis_json,ic.status,
                  a.person_id AS person_a_id,a.raw_name AS raw_a,
                  a.normalized_name AS normalized_a,
                  b.person_id AS person_b_id,b.raw_name AS raw_b,
                  b.normalized_name AS normalized_b,
                  pa.preferred_name AS person_a_name,pb.preferred_name AS person_b_name
           FROM identity_candidates ic
           JOIN person_occurrences a ON a.id=ic.occurrence_a_id
           JOIN person_occurrences b ON b.id=ic.occurrence_b_id
           LEFT JOIN persons pa ON pa.id=a.person_id
           LEFT JOIN persons pb ON pb.id=b.person_id
           ORDER BY ic.score DESC,ic.id"""
    ):
        if (
            row["occurrence_a_id"] not in allowed_occurrence_ids
            or row["occurrence_b_id"] not in allowed_occurrence_ids
            or privacy.blocked("identity_candidate", row["id"])
            or row["status"] != "unresolved"
        ):
            continue
        basis = json_object(row["basis_json"])
        identity_candidates.append(
            {
                "id": row["id"],
                "fromOccurrenceId": row["occurrence_a_id"],
                "toOccurrenceId": row["occurrence_b_id"],
                "score": row["score"],
                "status": row["status"],
                "basis": basis.get("basis"),
                "scoreSemantics": basis.get("score_semantics"),
                "candidateBundleId": basis.get("candidate_bundle_id"),
                "support": basis.get("support") or [],
                "against": basis.get("against") or [],
                "humanReviewDebt": basis.get("human_review_debt") or [],
                "fromPersonId": row["person_a_id"],
                "toPersonId": row["person_b_id"],
                "fromLabel": row["person_a_name"] or row["normalized_a"] or row["raw_a"],
                "toLabel": row["person_b_name"] or row["normalized_b"] or row["raw_b"],
                "highPriority": (
                    row["score"] is not None
                    and float(row["score"]) >= IDENTITY_REVIEW_THRESHOLD
                ),
            }
        )

    grouped_occurrences: dict[str, list[str]] = defaultdict(list)
    person_rows: dict[str, dict[str, Any]] = {}
    for row in connection.execute(
        """SELECT po.id,po.person_id,p.preferred_name,p.status
           FROM person_occurrences po
           JOIN persons p ON p.id=po.person_id
           WHERE po.person_id IS NOT NULL
           ORDER BY po.person_id,po.id"""
    ):
        if (
            row["id"] not in allowed_occurrence_ids
            or row["person_id"] not in allowed_person_ids
            or row["id"] in sanitized_external_occurrence_ids
        ):
            continue
        grouped_occurrences[row["person_id"]].append(row["id"])
        person_rows[row["person_id"]] = {
            "name": row["preferred_name"] or "[bez nazwy]",
            "status": row["status"],
        }
    identity_groups = [
        {
            "id": f"identity:{person_id}",
            "personId": person_id,
            "label": person_rows[person_id]["name"],
            "status": person_rows[person_id]["status"],
            "occurrenceIds": occurrence_ids,
            "occurrenceCount": len(occurrence_ids),
        }
        for person_id, occurrence_ids in sorted(grouped_occurrences.items())
        if (
            len(occurrence_ids) > 1
            and not privacy.blocked("person", person_id)
        )
    ]

    household_members: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in connection.execute(
        """SELECT household_id,person_id,occurrence_id,role,date_from,date_to
           FROM household_memberships ORDER BY household_id,role,id"""
    ):
        if row["occurrence_id"] and row["occurrence_id"] not in allowed_occurrence_ids:
            continue
        if row["person_id"] and row["person_id"] not in allowed_person_ids:
            continue
        household_members[row["household_id"]].append(
            {
                "personId": row["person_id"],
                "occurrenceId": row["occurrence_id"],
                "role": row["role"],
                "dateFrom": row["date_from"],
                "dateTo": row["date_to"],
            }
        )
    households = [
        {
            "id": row["id"],
            "label": row["label"],
            "placeId": row["place_id"],
            "dateFrom": row["date_from"],
            "dateTo": row["date_to"],
            "status": row["verification_status"],
            "notes": row["notes"],
            "members": household_members.get(row["id"], []),
        }
        for row in connection.execute(
            """SELECT id,place_id,label,date_from,date_to,verification_status,notes
               FROM households ORDER BY label,id"""
        )
        if not privacy.blocked("household", row["id"])
    ]
    return RelationshipsProjection(
        relationships, identity_candidates, identity_groups, households
    )
