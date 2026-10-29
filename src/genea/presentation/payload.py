from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from ..migrations import LATEST_SCHEMA_VERSION
from ..identity_source_profile_curation import _projection_validation_snapshot
from ..storage import configured_database_backend
from ..workspace import research_root, utc_now
from .events import project_events
from .graph_scopes import build_graph_scope_facade, load_graph_scope_config
from .life_spans import enrich_life_spans
from .map import build_map_facade, load_map_config
from .occurrences import project_occurrences
from .person_contexts import project_person_contexts
from .person_profiles import project_person_profiles
from .places import enrich_places_with_map_overlay, project_places
from .privacy import PrivacyContext
from .relationships import project_relationships
from .research import project_research
from .sources import enrich_source_record_summaries, project_sources
from .source_reported_networks import project_source_reported_networks
from .source_only_identities import project_source_only_identities
from .conditional_tree_attachments import project_conditional_tree_attachments
from .conditional_ancestry import project_conditional_ancestry
from .structured_parentage import load_structured_parentage
from .temporal import project_temporal_constraints
from .tree import enrich_tree_occurrences, load_tree
from .tree_family_evidence import project_tree_family_evidence
from .tree_family_knowledge import project_tree_family_knowledge
from .volume_indexes import project_volume_indexes
from ..identity_curation import IDENTITY_REVIEW_THRESHOLD


FRONTEND_SCHEMA_VERSION = 1


def _source_reported_network_inputs(
    connection: sqlite3.Connection,
) -> tuple[list[dict[str, str]], list[dict[str, str]], set[str]]:
    """Load private topology dependencies before presentation privacy.

    Canonical assertion IDs are used only to reproduce the component
    fingerprint protected by the persisted anchor decision.  The projector
    emits exclusively sanitized relationship IDs.
    """

    source_relationships = [
        {
            "assertionId": row["id"],
            "caseId": row["case_id"],
            "subjectOccurrenceId": row["subject_ref"],
            "predicate": row["predicate"],
            "objectOccurrenceId": row["object_text"],
        }
        for row in connection.execute(
            """SELECT DISTINCT sa.id,c.case_id,sa.subject_ref,sa.predicate,
                               sa.object_text
                 FROM source_assertions sa
                 LEFT JOIN claim_evidence ce ON ce.source_assertion_id=sa.id
                 LEFT JOIN claims c ON c.id=ce.claim_id
                WHERE sa.assertion_type='external_tree_relationship'
                  AND sa.legacy_relation_id IS NULL
                  AND sa.verification_status NOT IN ('retracted','superseded')
                ORDER BY c.case_id,sa.id"""
        )
        if row["subject_ref"] and row["object_text"]
    ]
    anchors = [
        {
            "caseId": row["case_id"],
            "componentFingerprint": row["component_fingerprint"],
            "anchorOccurrenceId": row["anchor_occurrence_id"],
            "status": row["status"],
        }
        for row in connection.execute(
            """SELECT case_id,component_fingerprint,anchor_occurrence_id,status
                 FROM source_reported_network_anchors
                WHERE status='active'
                ORDER BY case_id,component_fingerprint,id"""
        )
    ]
    materialized_occurrence_ids = {
        row["id"]
        for row in connection.execute(
            """SELECT po.id
                 FROM person_occurrences po
                 JOIN source_items si ON si.id=po.source_item_id
                WHERE si.item_type='external_tree_profile'
                  AND po.person_id IS NOT NULL
                ORDER BY po.id"""
        )
    }
    return source_relationships, anchors, materialized_occurrence_ids


def _fail_safe_external_identity_candidates(
    connection: sqlite3.Connection, candidates: list[dict[str, Any]]
) -> None:
    """Normalize legacy external candidates without upgrading their evidence."""

    by_id = {candidate["id"]: candidate for candidate in candidates}
    if not by_id:
        return
    marks = ",".join("?" for _ in by_id)
    for row in connection.execute(
        f"SELECT id,basis_json FROM identity_candidates WHERE id IN ({marks})",
        tuple(sorted(by_id)),
    ):
        try:
            basis = json.loads(row["basis_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            basis = {}
        if basis.get("service") not in {"familysearch", "myheritage"}:
            continue
        candidate = by_id[row["id"]]
        valid = (
            basis.get("score_semantics")
            == "research_priority_not_probability"
            and isinstance(basis.get("basis"), str)
            and bool(basis["basis"].strip())
            and isinstance(basis.get("created_by"), str)
            and bool(basis["created_by"].strip())
            and all(
                isinstance(basis.get(field), list)
                and all(
                    isinstance(value, str) and bool(value.strip())
                    for value in basis[field]
                )
                for field in ("support", "against", "human_review_debt")
            )
        )
        candidate["scoreSemantics"] = "research_priority_not_probability"
        if valid:
            candidate["basisState"] = "complete"
            candidate["createdBy"] = basis["created_by"]
            continue
        candidate.update(
            {
                "score": None,
                "highPriority": False,
                "basis": "Legacy external-tree candidate requires a new human review.",
                "basisState": "legacy_incomplete",
                "humanReviewRequired": True,
            }
        )


def _map_privacy_excluded_occurrence_ids(
    root: Path,
    tree: dict[str, Any] | None,
    occurrences: list[dict[str, Any]],
) -> set[str]:
    """Recover backing IDs hidden by the existing family-tree privacy policy."""

    if tree is None:
        return set()
    protected_tree_ids = {
        person["id"]
        for person in tree.get("people", [])
        if person.get("privacy") == "masked_living"
    }
    if not protected_tree_ids:
        return set()

    raw_tree = json.loads(
        (research_root(root) / "presentation" / "family-tree.json").read_text(
            encoding="utf-8"
        )
    )
    raw_people = {
        person["id"]: person
        for person in raw_tree.get("people", [])
        if isinstance(person, dict) and isinstance(person.get("id"), str)
    }
    excluded_occurrence_ids = {
        occurrence_id
        for tree_id in protected_tree_ids
        for occurrence_id in raw_people.get(tree_id, {}).get("occurrenceIds", [])
        if isinstance(occurrence_id, str)
    }
    occurrence_by_id = {item["id"]: item for item in occurrences}
    protected_canonical_ids = {
        raw_people[tree_id]["canonicalPersonId"]
        for tree_id in protected_tree_ids
        if isinstance(raw_people.get(tree_id, {}).get("canonicalPersonId"), str)
    }
    protected_canonical_ids.update(
        occurrence_by_id[occurrence_id]["personId"]
        for occurrence_id in excluded_occurrence_ids
        if isinstance(occurrence_by_id.get(occurrence_id, {}).get("personId"), str)
    )
    excluded_occurrence_ids.update(
        occurrence["id"]
        for occurrence in occurrences
        if occurrence.get("personId") in protected_canonical_ids
    )
    return excluded_occurrence_ids


def open_readonly_connection(root: Path) -> sqlite3.Connection:
    backend = configured_database_backend(root)
    if not backend.path.is_file():
        raise FileNotFoundError(
            "Brak research/genea.sqlite3. Najpierw uruchom `./genea init`."
        )
    connection = backend.connect(readonly=True)
    current = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if current < LATEST_SCHEMA_VERSION:
        connection.close()
        raise RuntimeError(
            "Baza wymaga kontrolowanej migracji przed eksportem frontendu; "
            "uruchom `./genea migration-run`."
        )
    return connection


def _align_guarded_identity(
    item: dict[str, Any], occurrence: dict[str, Any]
) -> None:
    """Copy identity membership from the validated occurrence, retaining source data."""
    for field in (
        "personId", "personStatus", "identityStatus", "candidatePersonStatus",
        "personIdentityResolved", "identityDecisionTier",
    ):
        item.pop(field, None)
        if field in occurrence:
            item[field] = occurrence[field]


def _align_guarded_temporal_identity(occurrences, temporal) -> None:
    guarded = {item["id"]: item for item in occurrences
               if item.get("sourceProfileIdentityGuarded") is True}
    for (_, occurrence_id), detail in temporal.participant_details.items():
        if occurrence_id in guarded:
            _align_guarded_identity(detail, guarded[occurrence_id])
    for constraint in temporal.constraints:
        if constraint["occurrenceId"] in guarded:
            _align_guarded_identity(constraint, guarded[constraint["occurrenceId"]])


def _align_guarded_event_identity(occurrences, events) -> None:
    guarded = {item["id"]: item for item in occurrences
               if item.get("sourceProfileIdentityGuarded") is True}
    for event in events:
        for participant in event.get("participants", []):
            occurrence_id = participant.get("occurrenceId")
            if occurrence_id in guarded:
                _align_guarded_identity(participant, guarded[occurrence_id])


def _align_guarded_candidate_identity(occurrences, candidates) -> None:
    guarded = {item["id"]: item for item in occurrences
               if item.get("sourceProfileIdentityGuarded") is True}
    for candidate in candidates:
        for side in ("from", "to"):
            occurrence = guarded.get(candidate.get(side + "OccurrenceId"))
            if occurrence is not None:
                candidate[side + "PersonId"] = occurrence.get("personId")
                candidate[side + "Label"] = occurrence.get("name")


def _identity_groups_for_projected_occurrences(
    groups: list[dict[str, Any]], occurrences: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Respect projection guards when SQL backing links are not yet publishable."""
    projected_people = {item["id"]: item.get("personId") for item in occurrences}
    result = []
    for group in groups:
        members = [
            occurrence_id for occurrence_id in group["occurrenceIds"]
            if projected_people.get(occurrence_id) == group["personId"]
        ]
        if len(members) > 1:
            result.append({**group, "occurrenceIds": members, "occurrenceCount": len(members)})
    return result


def build_frontend_payload(root: Path) -> dict[str, Any]:
    """Compose a source-preserving, privacy-filtered presentation payload."""

    with (
        closing(open_readonly_connection(root)) as connection,
        _projection_validation_snapshot(root, connection) as validation_context,
    ):
        privacy = PrivacyContext.load(connection)
        sources = project_sources(root, connection, privacy)
        occurrence_data = project_occurrences(
            connection, privacy, sources.allowed_ids, validation_context=validation_context
        )
        temporal = project_temporal_constraints(
            connection,
            privacy,
            allowed_source_ids=sources.allowed_ids,
            allowed_occurrence_ids=occurrence_data.allowed_occurrence_ids,
        )
        _align_guarded_temporal_identity(occurrence_data.occurrences, temporal)
        events = project_events(
            connection,
            privacy,
            allowed_source_ids=sources.allowed_ids,
            allowed_occurrence_ids=occurrence_data.allowed_occurrence_ids,
            occurrences=occurrence_data.occurrences,
            participant_details=temporal.participant_details,
        )
        _align_guarded_event_identity(occurrence_data.occurrences, events)
        enrich_source_record_summaries(sources.items, events)
        places = project_places(
            connection,
            privacy,
            allowed_source_ids=sources.allowed_ids,
        )
        relations = project_relationships(
            connection,
            privacy,
            allowed_occurrence_ids=occurrence_data.allowed_occurrence_ids,
            allowed_person_ids=occurrence_data.allowed_person_ids,
            events=events,
        )
        relations.identity_groups[:] = _identity_groups_for_projected_occurrences(
            relations.identity_groups, occurrence_data.occurrences
        )
        _fail_safe_external_identity_candidates(
            connection, relations.identity_candidates
        )
        _align_guarded_candidate_identity(
            occurrence_data.occurrences, relations.identity_candidates
        )
        tree = enrich_tree_occurrences(
            load_tree(root),
            occurrence_data.occurrences,
            sources.allowed_ids,
            relations.identity_candidates,
        )
        tree = project_tree_family_evidence(
            root, connection, privacy, tree,
            allowed_source_ids=sources.allowed_ids,
        )
        tree = project_tree_family_knowledge(root, connection, tree)
        (
            source_relationships,
            network_anchors,
            materialized_external_occurrence_ids,
        ) = _source_reported_network_inputs(connection)
        source_reported_networks = project_source_reported_networks(
            people=occurrence_data.people,
            occurrences=occurrence_data.occurrences,
            relationships=relations.relationships,
            source_relationships=source_relationships,
            network_anchors=network_anchors,
            materialized_occurrence_ids=materialized_external_occurrence_ids,
        )
        if tree is not None:
            tree["sourceReportedNetworks"] = source_reported_networks
        research = project_research(
            root,
            connection,
            privacy,
            allowed_source_ids=sources.allowed_ids,
            source_reported_networks=source_reported_networks,
        )
        enrich_life_spans(
            occurrence_data.people,
            tree,
            occurrence_data.occurrences,
            events,
        )
        people_by_id = {item["id"]: item for item in occurrence_data.people}
        for identity_group in relations.identity_groups:
            person = people_by_id.get(identity_group["personId"], {})
            identity_group["lifeSpan"] = person.get("lifeSpan")
            identity_group["lifeSpanLabel"] = person.get("lifeSpanLabel", "?–?")
        graph_scopes = build_graph_scope_facade(
            load_graph_scope_config(root),
            tree,
            occurrence_data.occurrences,
            relations.households,
        )
        map_config = load_map_config(root, tree)
        places = enrich_places_with_map_overlay(
            places,
            map_config["places"] if map_config else [],
        )
        map_excluded_occurrence_ids = _map_privacy_excluded_occurrence_ids(
            root,
            tree,
            occurrence_data.occurrences,
        )
        presentation_map = build_map_facade(
            map_config,
            tree,
            places,
            events,
            occurrence_data.occurrences,
            excluded_occurrence_ids=map_excluded_occurrence_ids,
        )
        person_contexts = project_person_contexts(
            people=occurrence_data.people,
            occurrences=occurrence_data.occurrences,
            tree=tree,
            identity_groups=relations.identity_groups,
            presentation_map=presentation_map,
            source_reported_networks=source_reported_networks,
        )
        if tree is not None:
            attachments = project_conditional_tree_attachments(
                networks=source_reported_networks,
                occurrences=occurrence_data.occurrences,
                tree=tree,
                contexts=person_contexts,
            )
            if attachments:
                tree["conditionalTreeAttachments"] = attachments
            tree["conditionalAncestry"] = project_conditional_ancestry(
                family_tree=tree,
                relationships=relations.relationships,
                person_contexts=person_contexts,
                source_reported_networks=source_reported_networks,
                occurrences=occurrence_data.occurrences,
                structured_hypotheses=load_structured_parentage(
                    root, connection, privacy, tree,
                    occurrence_data.occurrences, sources.allowed_ids,
                ),
            )
        person_profiles = project_person_profiles(
            contexts=person_contexts,
            people=occurrence_data.people,
            occurrences=occurrence_data.occurrences,
        )
        volume_indexes = project_volume_indexes(root)
        source_identity_groups = project_source_only_identities(
            connection, root,
            visible_occurrence_ids={item["id"] for item in occurrence_data.occurrences},
            context=validation_context,
        )
        payload = {
            "meta": {
                "schemaVersion": FRONTEND_SCHEMA_VERSION,
                "generatedAt": utc_now(),
                "databaseSchemaVersion": LATEST_SCHEMA_VERSION,
                "mode": "local-research-view-with-curator-writeback",
                "privacy": {
                    "default": "exclude-living-and-restricted",
                    "excludedObjects": privacy.excluded_count,
                },
                "methodNote": (
                    "Wystąpienia osób pozostają oddzielne do czasu jawnej decyzji "
                    "tożsamości; próg kolejki porządkuje pracę i nie jest "
                    "prawdopodobieństwem ani automatycznym scaleniem."
                ),
            },
            "people": occurrence_data.people,
            "occurrences": occurrence_data.occurrences,
            "events": events,
            "temporalConstraints": temporal.constraints,
            "places": places,
            "relationships": relations.relationships,
            "sourceReportedNetworks": source_reported_networks,
            "identityCandidates": relations.identity_candidates,
            "identityGroups": relations.identity_groups,
            "sourceIdentityGroups": source_identity_groups,
            "personContexts": person_contexts,
            "personProfiles": person_profiles,
            "identityCuration": {
                "reviewThreshold": IDENTITY_REVIEW_THRESHOLD,
                "scoreSemantics": "research_priority_not_probability",
                "automaticMerge": False,
            },
            "households": relations.households,
            "sources": sources.items,
            "claims": research.claims,
            "conflicts": research.conflicts,
            "researchBacklog": research.backlog,
            "volumeIndexes": volume_indexes,
            "familyTree": tree,
            "graphScopes": graph_scopes,
            "map": presentation_map,
        }
        payload["meta"]["counts"] = {
            key: len(payload[key])
            for key in (
                "people",
                "occurrences",
                "events",
                "temporalConstraints",
                "places",
                "relationships",
                "sourceReportedNetworks",
                "identityCandidates",
                "identityGroups",
                "sourceIdentityGroups",
                "personContexts",
                "personProfiles",
                "households",
                "sources",
                "claims",
                "conflicts",
            )
        }
        payload["meta"]["counts"]["researchBacklog"] = research.backlog["summary"]["total"]
        payload["meta"]["counts"]["volumeIndexes"] = len(volume_indexes)
        payload["meta"]["counts"]["mapPlaces"] = len(
            presentation_map["places"] if presentation_map else []
        )
        payload["meta"]["counts"]["mapObservations"] = len(
            presentation_map["observations"] if presentation_map else []
        )
        return payload
