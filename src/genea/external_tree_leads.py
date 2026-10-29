from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .command_envelope import (
    CommandEnvelopeConflict,
    canonical_json,
    fingerprint,
    record_command,
    replay_command,
)
from .db import connect, initialize
from .research import require_tables
from .workspace import search_fold, stable_id, utc_now


REQUIRED_TABLES = {
    "audit_log",
    "claims",
    "claim_evidence",
    "conflicts",
    "curation_commands",
    "identity_candidates",
    "person_occurrences",
    "person_names",
    "persons",
    "privacy_labels",
    "research_cases",
    "source_assertions",
    "source_items",
    "source_reported_network_anchors",
}
RELATION_STATUSES = {"unresolved", "unresolved_high_conflict", "profile_conflation"}
PROFILE_STATES = {"imported_unverified", "unresolved_high_conflict", "profile_conflation"}
PRIVACY_CLASSIFICATIONS = {"public_historical", "deceased", "living", "restricted"}
SUPPORTED_SERVICES = {"familysearch", "myheritage"}
IDENTITY_SCORE_SEMANTICS = "research_priority_not_probability"
MYHERITAGE_HOSTS = {
    "myheritage.com",
    "www.myheritage.com",
    "myheritage.pl",
    "www.myheritage.pl",
}


class ExternalTreeLeadError(ValueError):
    pass


class ExternalTreeLeadConflict(ExternalTreeLeadError):
    pass


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExternalTreeLeadError(f"Pole {label} musi być niepustym tekstem")
    return value.strip()


def _text_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise ExternalTreeLeadError(f"Pole {label} musi być listą tekstów")
    result: list[str] = []
    for index, item in enumerate(value, start=1):
        result.append(_text(item, f"{label}[{index}]"))
    return result


def _identity_basis_envelope(
    payload: dict[str, Any], item: dict[str, Any]
) -> dict[str, Any]:
    """Build the common auditable identity-candidate basis envelope.

    External-tree candidates are new research proposals, so they fail closed
    when the manifest omits semantics required by the ordinary identity
    candidate flow.  Provider-specific provenance remains internal metadata.
    """

    semantics = item.get("scoreSemantics", item.get("score_semantics"))
    if semantics != IDENTITY_SCORE_SEMANTICS:
        raise ExternalTreeLeadError(
            "Kandydatura identity wymaga "
            "scoreSemantics=research_priority_not_probability"
        )
    for field in ("support", "against", "humanReviewDebt"):
        if field not in item:
            raise ExternalTreeLeadError(
                f"Kandydatura identity wymaga jawnego pola {field}"
            )
    score = item.get("score")
    if score is not None and (
        isinstance(score, bool)
        or not isinstance(score, (int, float))
        or not 0 <= float(score) <= 1
    ):
        raise ExternalTreeLeadError(
            "Score kandydatury identity musi mieścić się w zakresie 0–1"
        )
    return {
        "basis": _text(item.get("basis"), "identityCandidate.basis"),
        "score_semantics": IDENTITY_SCORE_SEMANTICS,
        "created_by": payload["actor"],
        "support": _text_list(item["support"], "identityCandidate.support"),
        "against": _text_list(item["against"], "identityCandidate.against"),
        "human_review_debt": _text_list(
            item["humanReviewDebt"], "identityCandidate.humanReviewDebt"
        ),
    }


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
        raise ExternalTreeLeadError("Manifest external-tree musi mieć schemaVersion=1")
    if payload.get("service") not in SUPPORTED_SERVICES:
        raise ExternalTreeLeadError(
            "Nieobsługiwany serwis external-tree; dozwolone: familysearch, myheritage"
        )
    for field in ("commandId", "caseId", "actor", "rationale", "observedAt"):
        _text(payload.get(field), field)
    declared = payload.get("manifestFingerprint")
    clean = dict(payload)
    clean.pop("manifestFingerprint", None)
    calculated = fingerprint(clean)
    if declared is not None and declared != calculated:
        raise ExternalTreeLeadError("manifestFingerprint nie odpowiada treści manifestu")
    payload["manifestFingerprint"] = calculated
    return payload


def _profile_ids(
    service: str,
    *,
    person_id: str,
    tree_id: str | None = None,
    materialize_candidate: bool = False,
) -> dict[str, str]:
    if service == "familysearch":
        result = {
            "sourceItemId": stable_id("item_fs_profile", person_id),
            "occurrenceId": stable_id("occ_fs_profile", person_id),
            "sourcePrivacyId": stable_id("privacy_fs_source", person_id),
        }
        if materialize_candidate:
            result.update(
                {
                    "personId": stable_id("person_fs_candidate", person_id),
                    "personNameId": stable_id("person_name_fs_candidate", person_id),
                    "privacyId": stable_id("privacy_fs_candidate", person_id),
                }
            )
        return result
    if tree_id is None:
        raise ExternalTreeLeadError("Profil MyHeritage wymaga treeId")
    result = {
        "sourceItemId": stable_id("item_mh_profile", tree_id, person_id),
        "occurrenceId": stable_id("occ_mh_profile", tree_id, person_id),
        "sourcePrivacyId": stable_id("privacy_mh_source", tree_id, person_id),
    }
    if materialize_candidate:
        result.update(
            {
                "personId": stable_id("person_mh_candidate", tree_id, person_id),
                "personNameId": stable_id(
                    "person_name_mh_candidate", tree_id, person_id
                ),
                "privacyId": stable_id("privacy_mh_candidate", tree_id, person_id),
            }
        )
    return result


def _relationship_ids(
    service: str,
    item: dict[str, Any],
    *,
    subject_key: str | None = None,
    object_key: str | None = None,
    case_id: str | None = None,
) -> dict[str, str]:
    key = _text(item.get("id"), "relationship.id")
    if service == "myheritage":
        if subject_key is None or object_key is None:
            raise ExternalTreeLeadError(f"Relacja {key} nie ma kluczy profili")
        result = {
            "assertionId": stable_id(
                "assertion_mh_relation", subject_key, object_key, key
            ),
            "claimId": stable_id("claim_mh_relation", subject_key, object_key, key),
            "claimEvidenceId": stable_id(
                "claim_evidence_mh", subject_key, object_key, key
            ),
        }
    else:
        result = {
            "assertionId": stable_id("assertion_fs_relation", key),
            "claimId": stable_id("claim_fs_relation", key),
            "claimEvidenceId": stable_id("claim_evidence_fs", key),
        }
    if case_id is not None:
        result["claimId"] = stable_id(
            "external_tree_relation_claim", case_id, result["assertionId"]
        )
        result["claimEvidenceId"] = stable_id(
            "external_tree_relation_evidence", case_id, result["assertionId"]
        )
    return result


def _assessment_ids(
    service: str, item: dict[str, Any], *, profile_key: str | None = None
) -> dict[str, str]:
    key = _text(item.get("id"), "assessment.id")
    if service == "myheritage":
        if profile_key is None:
            raise ExternalTreeLeadError(f"Ocena {key} nie ma klucza profilu")
        return {"claimId": stable_id("claim_mh_profile", profile_key, key)}
    return {"claimId": stable_id("claim_fs_profile", key)}


def _familysearch_url(pid: str, value: Any) -> str:
    url = _text(value, f"profiles[{pid}].url")
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc not in {
        "familysearch.org",
        "www.familysearch.org",
    }:
        raise ExternalTreeLeadError(f"Profil {pid} nie ma bezpośredniego URL FamilySearch")
    if pid not in parsed.path:
        raise ExternalTreeLeadError(f"URL profilu nie zawiera PID {pid}")
    return url


def _myheritage_url(tree_id: str, person_id: str, value: Any) -> str:
    url = _text(value, f"profiles[{tree_id}:{person_id}].url")
    parsed = urlparse(url)
    try:
        port = parsed.port
    except ValueError as error:
        raise ExternalTreeLeadError(
            f"Profil {tree_id}:{person_id} ma nieprawidłowy port URL"
        ) from error
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() not in MYHERITAGE_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        raise ExternalTreeLeadError(
            f"Profil {tree_id}:{person_id} nie ma bezpośredniego URL MyHeritage"
        )
    expected = f"pedigree-tree-{tree_id}-{person_id}"
    if expected not in parsed.path:
        raise ExternalTreeLeadError(
            f"URL profilu nie odpowiada treeId/personId {tree_id}:{person_id}"
        )
    return url


def _profile_ids_for(item: dict[str, Any]) -> dict[str, str]:
    return _profile_ids(
        item["_service"],
        person_id=item["_personId"],
        tree_id=item.get("_treeId"),
        materialize_candidate=bool(item.get("materializeCandidate", False)),
    )


def _profile_key(service: str, *, person_id: str, tree_id: str | None) -> str:
    return person_id if service == "familysearch" else f"{tree_id}:{person_id}"


def _source_privacy_rationale(service: str, profile_key: str) -> str:
    return (
        f"Restricted {service} external-tree profile {profile_key}; source and its "
        "occurrence graph remain restricted independently of person privacy."
    )


def _relationship_component_data(
    service: str,
    relationships: list[dict[str, Any]],
    by_key: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    parent: dict[str, str] = {}

    def find(value: str) -> str:
        parent.setdefault(value, value)
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    for item in relationships:
        union(item["_subjectKey"], item["_objectKey"])

    grouped_profiles: dict[str, set[str]] = {}
    grouped_relationships: dict[str, list[dict[str, str]]] = {}
    for item in relationships:
        root = find(item["_subjectKey"])
        grouped_profiles.setdefault(root, set()).update(
            (item["_subjectKey"], item["_objectKey"])
        )
        ids = _relationship_ids(
            service,
            item,
            subject_key=item["_subjectKey"],
            object_key=item["_objectKey"],
        )
        grouped_relationships.setdefault(root, []).append(
            {
                "assertionId": ids["assertionId"],
                "subjectOccurrenceId": _profile_ids_for(
                    by_key[item["_subjectKey"]]
                )["occurrenceId"],
                "predicate": item["predicate"],
                "objectOccurrenceId": _profile_ids_for(
                    by_key[item["_objectKey"]]
                )["occurrenceId"],
            }
        )

    result: list[dict[str, Any]] = []
    for root in sorted(grouped_profiles):
        relation_basis = sorted(
            grouped_relationships[root],
            key=lambda value: (
                value["assertionId"],
                value["subjectOccurrenceId"],
                value["predicate"],
                value["objectOccurrenceId"],
            ),
        )
        profile_keys = sorted(grouped_profiles[root])
        result.append(
            {
                "componentFingerprint": fingerprint(
                    {"sourceRelationships": relation_basis}
                ),
                "profileKeys": profile_keys,
                "occurrenceIds": [
                    _profile_ids_for(by_key[key])["occurrenceId"]
                    for key in profile_keys
                ],
                "relationshipAssertionIds": [
                    value["assertionId"] for value in relation_basis
                ],
                "relationshipBasis": relation_basis,
            }
        )
    return sorted(result, key=lambda value: value["componentFingerprint"])


def _anchor_profile_key(
    payload: dict[str, Any],
    anchor: dict[str, Any],
    by_key: dict[str, dict[str, Any]],
    by_person_id: dict[str, list[str]],
) -> str:
    occurrence_id = anchor.get("anchorOccurrenceId")
    if occurrence_id is not None:
        occurrence_id = _text(occurrence_id, "networkAnchor.anchorOccurrenceId")
        matches = [
            key
            for key, profile in by_key.items()
            if _profile_ids_for(profile)["occurrenceId"] == occurrence_id
        ]
        if len(matches) != 1:
            raise ExternalTreeLeadError(
                "Anchor wskazuje occurrence spoza profili manifestu"
            )
        return matches[0]
    if payload["service"] == "familysearch":
        return _text(
            anchor.get("anchorPid", anchor.get("pid")),
            "networkAnchor.anchorPid",
        )
    profile_key = anchor.get("anchorProfileKey")
    if profile_key is not None:
        return _text(profile_key, "networkAnchor.anchorProfileKey")
    return _resolve_profile_reference(
        payload,
        {
            "subjectPersonId": anchor.get(
                "anchorPersonId", anchor.get("personId")
            ),
            "subjectTreeId": anchor.get(
                "anchorTreeId", anchor.get("treeId", payload.get("treeId"))
            ),
        },
        "subject",
        by_key,
        by_person_id,
    )


def _profile_metadata(payload: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    metadata = {
        "service": payload["service"],
        "observedAt": payload["observedAt"],
        "profileState": item.get("profileState", "imported_unverified"),
        "facts": item.get("facts", {}),
        "sourceCount": item.get("sourceCount"),
        "sourceRefs": item.get("sourceRefs", []),
        "warnings": item.get("warnings", []),
        "familyPids": item.get("familyPids", {}),
        "provenance": item.get("provenance", {}),
        "externalTreeLead": True,
        "noAutoMerge": True,
        "noConfirmedPromotion": True,
    }
    if payload["service"] == "familysearch":
        metadata["pid"] = item["_personId"]
    else:
        metadata["treeId"] = item["_treeId"]
        metadata["personId"] = item["_personId"]
    return metadata


def _profile_source_values(
    payload: dict[str, Any], item: dict[str, Any]
) -> dict[str, Any]:
    if payload["service"] == "familysearch":
        external_id = item["_personId"]
        title = f"FamilySearch: {item['name']} ({external_id})"
        locator = f"familysearch:person:{external_id}"
        citation = (
            f"FamilySearch collaborative tree, person {external_id}; "
            f"accessed {payload['observedAt']}"
        )
    else:
        tree_id = item["_treeId"]
        person_id = item["_personId"]
        title = f"MyHeritage: {item['name']} (tree {tree_id}; person {person_id})"
        locator = f"myheritage:tree:{tree_id}:person:{person_id}"
        citation = (
            f"MyHeritage member tree {tree_id}, person {person_id}; "
            f"accessed {payload['observedAt']}"
        )
    return {
        "title": title,
        "item_type": "external_tree_profile",
        "date_text": item.get("dateText"),
        "locator": locator,
        "source_url": item["url"],
        "citation_text": citation,
        "source_quality": "Authored",
        "verification_status": "imported_unverified",
        "metadata_json": canonical_json(_profile_metadata(payload, item)),
    }


def _resolve_profile_reference(
    payload: dict[str, Any],
    item: dict[str, Any],
    prefix: str,
    by_key: dict[str, dict[str, Any]],
    by_person_id: dict[str, list[str]],
) -> str:
    if payload["service"] == "familysearch":
        pid = _text(item.get(f"{prefix}Pid"), f"relationship.{prefix}Pid")
        if pid not in by_key:
            raise ExternalTreeLeadError(
                f"Relacja wskazuje profil spoza manifestu: {pid}"
            )
        return pid
    person_id = _text(
        item.get(f"{prefix}PersonId", item.get(f"{prefix}Pid")),
        f"relationship.{prefix}PersonId",
    )
    tree_value = item.get(f"{prefix}TreeId", payload.get("treeId"))
    if tree_value is not None:
        tree_id = _text(tree_value, f"relationship.{prefix}TreeId")
        key = _profile_key("myheritage", person_id=person_id, tree_id=tree_id)
        if key not in by_key:
            raise ExternalTreeLeadError(
                f"Relacja wskazuje profil spoza manifestu: {key}"
            )
        return key
    matches = by_person_id.get(person_id, [])
    if len(matches) != 1:
        raise ExternalTreeLeadError(
            f"Relacja wymaga jawnego {prefix}TreeId dla personId {person_id}"
        )
    return matches[0]


def _identity_candidate_id(
    service: str, profile_key: str, local_occurrence_id: str
) -> str:
    if service == "familysearch":
        return stable_id("identity_candidate_fs", profile_key, local_occurrence_id)
    return stable_id("identity_candidate_mh", profile_key, local_occurrence_id)


def _external_identity_basis(
    payload: dict[str, Any], item: dict[str, Any], profile_key: str
) -> dict[str, Any]:
    basis = {
        **item["_basisEnvelope"],
        "service": payload["service"],
        "presentationStatus": item.get("presentationStatus", "unresolved"),
        "localSourceIds": item.get("localSourceIds", []),
        "observedAt": payload["observedAt"],
        "noAutoMerge": True,
    }
    if payload["service"] == "familysearch":
        basis["externalPid"] = profile_key
    else:
        basis["externalProfileKey"] = profile_key
    return basis


def _conflict_id(
    service: str,
    kind: str,
    key: str,
    index: int,
    *,
    profile_key: str | None = None,
    subject_key: str | None = None,
    object_key: str | None = None,
    case_id: str | None = None,
) -> str:
    if case_id is not None:
        return stable_id(
            "external_tree_relation_conflict", case_id, key, str(index)
        )
    if service == "familysearch":
        return stable_id(f"conflict_fs_{kind}", key, str(index))
    if kind == "relation":
        return stable_id(
            "conflict_mh_relation",
            subject_key or "",
            object_key or "",
            key,
            str(index),
        )
    return stable_id("conflict_mh_profile", profile_key or "", key, str(index))


def _existing_rows(connection, table: str, ids: list[str]) -> dict[str, dict[str, Any]]:  # type: ignore[no-untyped-def]
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    return {
        str(row["id"]): dict(row)
        for row in connection.execute(
            f"SELECT * FROM {table} WHERE id IN ({marks}) ORDER BY id", tuple(ids)
        )
    }


def _payload_differs(
    row: dict[str, Any] | None, expected: dict[str, Any]
) -> bool:
    return bool(row and any(row[field] != value for field, value in expected.items()))


def _validate(connection, payload: dict[str, Any]) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    service = payload["service"]
    case_id = payload["caseId"]
    case = connection.execute("SELECT * FROM research_cases WHERE id=?", (case_id,)).fetchone()
    if case is None:
        raise ExternalTreeLeadError(f"Nie znaleziono sprawy {case_id}")

    profiles = payload.get("profiles")
    relationships = payload.get("relationships", [])
    assessments = payload.get("profileAssessments", [])
    identities = payload.get("identityCandidates", [])
    network_anchors = payload.get("networkAnchors")
    if not isinstance(profiles, list) or not profiles:
        raise ExternalTreeLeadError("Manifest wymaga niepustej listy profiles")
    if not all(isinstance(value, list) for value in (relationships, assessments, identities)):
        raise ExternalTreeLeadError("Relacje, oceny i kandydatury muszą być listami")
    if network_anchors is not None and not isinstance(network_anchors, list):
        raise ExternalTreeLeadError("networkAnchors musi być listą")

    by_key: dict[str, dict[str, Any]] = {}
    by_person_id: dict[str, list[str]] = {}
    planned: dict[str, list[str]] = {
        "source_items": [],
        "person_occurrences": [],
        "persons": [],
        "person_names": [],
        "privacy_labels": [],
        "source_assertions": [],
        "claims": [],
        "claim_evidence": [],
        "conflicts": [],
        "identity_candidates": [],
        "source_reported_network_anchors": [],
    }
    for profile in profiles:
        if not isinstance(profile, dict):
            raise ExternalTreeLeadError("Każdy profil musi być obiektem")
        if not isinstance(profile.get("materializeCandidate", False), bool):
            raise ExternalTreeLeadError("materializeCandidate musi być wartością boolean")
        if service == "familysearch":
            person_id = _text(profile.get("pid"), "profile.pid")
            tree_id = None
            profile["url"] = _familysearch_url(person_id, profile.get("url"))
        else:
            person_id = _text(profile.get("personId"), "profile.personId")
            tree_id = _text(
                profile.get("treeId", payload.get("treeId")),
                f"profiles[{person_id}].treeId",
            )
            profile["url"] = _myheritage_url(tree_id, person_id, profile.get("url"))
            source_privacy = profile.get(
                "sourcePrivacyClassification",
                payload.get("sourcePrivacyClassification", "restricted"),
            )
            if source_privacy != "restricted":
                raise ExternalTreeLeadError(
                    f"Profil MyHeritage {tree_id}:{person_id} wymaga "
                    "sourcePrivacyClassification=restricted"
                )
            profile["sourcePrivacyClassification"] = source_privacy
        key = _profile_key(service, person_id=person_id, tree_id=tree_id)
        if key in by_key:
            raise ExternalTreeLeadError(f"Powtórzony profil {key}")
        profile["name"] = _text(profile.get("name"), f"profiles[{key}].name")
        state = str(profile.get("profileState", "imported_unverified"))
        if state not in PROFILE_STATES:
            raise ExternalTreeLeadError(f"Nieobsługiwany stan profilu {key}: {state}")
        profile["_service"] = service
        profile["_personId"] = person_id
        profile["_treeId"] = tree_id
        profile["_profileKey"] = key
        by_key[key] = profile
        by_person_id.setdefault(person_id, []).append(key)
        ids = _profile_ids_for(profile)
        planned["source_items"].append(ids["sourceItemId"])
        planned["person_occurrences"].append(ids["occurrenceId"])
        if (
            service == "myheritage"
            or profile.get("sourcePrivacyClassification") == "restricted"
        ):
            planned["privacy_labels"].append(ids["sourcePrivacyId"])
        if profile.get("materializeCandidate", False):
            privacy_classification = profile.get("privacyClassification")
            if privacy_classification not in PRIVACY_CLASSIFICATIONS:
                raise ExternalTreeLeadError(
                    f"Materializowany profil {key} wymaga jawnego privacyClassification"
                )
            explicit_source_privacy = profile.get("sourcePrivacyClassification")
            if service == "familysearch" or network_anchors is not None:
                if explicit_source_privacy != "restricted":
                    raise ExternalTreeLeadError(
                        f"Materializowany profil {key} wymaga jawnego "
                        "sourcePrivacyClassification=restricted"
                    )
            else:
                explicit_source_privacy = "restricted"
            profile["sourcePrivacyClassification"] = explicit_source_privacy
            planned["persons"].append(ids["personId"])
            planned["person_names"].append(ids["personNameId"])
            if ids["sourcePrivacyId"] not in planned["privacy_labels"]:
                planned["privacy_labels"].append(ids["sourcePrivacyId"])
            planned["privacy_labels"].append(ids["privacyId"])

    seen_relationships: set[tuple[str, str, str]] = set()
    for item in relationships:
        if not isinstance(item, dict):
            raise ExternalTreeLeadError("Każda relacja musi być obiektem")
        key = _text(item.get("id"), "relationship.id")
        subject_key = _resolve_profile_reference(
            payload, item, "subject", by_key, by_person_id
        )
        object_key = _resolve_profile_reference(
            payload, item, "object", by_key, by_person_id
        )
        relation_key = (subject_key, object_key, key)
        if relation_key in seen_relationships:
            raise ExternalTreeLeadError(f"Powtórzona relacja {key}")
        seen_relationships.add(relation_key)
        item["_subjectKey"] = subject_key
        item["_objectKey"] = object_key
        _text(item.get("predicate"), f"relationships[{key}].predicate")
        if item.get("status") not in RELATION_STATUSES:
            raise ExternalTreeLeadError(f"Nieprawidłowy status relacji {key}")
        base_ids = _relationship_ids(
            service, item, subject_key=subject_key, object_key=object_key
        )
        existing_base_claim = connection.execute(
            "SELECT case_id FROM claims WHERE id=?",
            (base_ids["claimId"],),
        ).fetchone()
        item["_claimCaseScope"] = (
            case_id
            if existing_base_claim is not None
            and existing_base_claim["case_id"] != case_id
            else None
        )
        ids = _relationship_ids(
            service,
            item,
            subject_key=subject_key,
            object_key=object_key,
            case_id=item["_claimCaseScope"],
        )
        if ids["assertionId"] in planned["source_assertions"]:
            raise ExternalTreeLeadError(
                f"Powtórzony trwały identyfikator relacji {key}"
            )
        planned["source_assertions"].append(ids["assertionId"])
        planned["claims"].append(ids["claimId"])
        planned["claim_evidence"].append(ids["claimEvidenceId"])
        conflicts = item.get("conflicts", [])
        if not isinstance(conflicts, list):
            raise ExternalTreeLeadError(f"Konflikty relacji {key} muszą być listą")
        for index, conflict in enumerate(conflicts, start=1):
            if not isinstance(conflict, dict):
                raise ExternalTreeLeadError(f"Konflikt relacji {key} musi być obiektem")
            _text(conflict.get("title"), f"relationships[{key}].conflicts[{index}].title")
            _text(conflict.get("description"), f"relationships[{key}].conflicts[{index}].description")
            planned["conflicts"].append(
                _conflict_id(
                    service,
                    "relation",
                    key,
                    index,
                    subject_key=subject_key,
                    object_key=object_key,
                    case_id=item["_claimCaseScope"],
                )
            )

    relationship_components = _relationship_component_data(
        service, relationships, by_key
    )
    component_by_fingerprint = {
        item["componentFingerprint"]: item for item in relationship_components
    }
    anchor_decisions: list[dict[str, Any]] = []
    if network_anchors is not None:
        if len(network_anchors) != len(relationship_components):
            raise ExternalTreeLeadError(
                "networkAnchors musi wskazywać dokładnie jeden anchor każdego "
                "jawnego komponentu relacji"
            )
        seen_component_fingerprints: set[str] = set()
        for index, anchor in enumerate(network_anchors, start=1):
            if not isinstance(anchor, dict):
                raise ExternalTreeLeadError("Każdy networkAnchor musi być obiektem")
            declared_fingerprint = _text(
                anchor.get("componentFingerprint"),
                f"networkAnchors[{index}].componentFingerprint",
            )
            if declared_fingerprint in seen_component_fingerprints:
                raise ExternalTreeLeadError(
                    "Wielokrotny anchor dla tego samego komponentu"
                )
            seen_component_fingerprints.add(declared_fingerprint)
            component = component_by_fingerprint.get(declared_fingerprint)
            if component is None:
                raise ExternalTreeLeadConflict(
                    f"Stale componentFingerprint anchoru: {declared_fingerprint}"
                )
            profile_key = _anchor_profile_key(
                payload, anchor, by_key, by_person_id
            )
            if profile_key not in component["profileKeys"]:
                raise ExternalTreeLeadError(
                    f"Anchor {profile_key} nie należy do wskazanego komponentu relacji"
                )
            rationale_value = _text(
                anchor.get("rationale"), f"networkAnchors[{index}].rationale"
            )
            expected_field = (
                "expectedPreviousAnchorId"
                if "expectedPreviousAnchorId" in anchor
                else "expectedActiveAnchorId"
                if "expectedActiveAnchorId" in anchor
                else None
            )
            if expected_field is None:
                raise ExternalTreeLeadError(
                    "networkAnchor wymaga jawnego expectedPreviousAnchorId "
                    "(null dla pierwszej decyzji)"
                )
            expected_previous = anchor.get(expected_field)
            if expected_previous is not None:
                expected_previous = _text(
                    expected_previous,
                    f"networkAnchors[{index}].{expected_field}",
                )
            supersedes_anchor_id = anchor.get("supersedesAnchorId")
            if supersedes_anchor_id is not None:
                supersedes_anchor_id = _text(
                    supersedes_anchor_id,
                    f"networkAnchors[{index}].supersedesAnchorId",
                )
            anchor_occurrence_id = _profile_ids_for(by_key[profile_key])[
                "occurrenceId"
            ]
            active_rows = connection.execute(
                """SELECT * FROM source_reported_network_anchors
                   WHERE case_id=? AND component_fingerprint=? AND status='active'
                   ORDER BY id""",
                (case_id, declared_fingerprint),
            ).fetchall()
            if len(active_rows) > 1:
                raise ExternalTreeLeadConflict(
                    "Baza zawiera wielokrotne aktywne anchory komponentu"
                )
            active = dict(active_rows[0]) if active_rows else None
            if active is None:
                if expected_previous is not None or supersedes_anchor_id is not None:
                    raise ExternalTreeLeadConflict(
                        "Stale oczekiwany poprzedni anchor: komponent nie ma aktywnej decyzji"
                    )
                action = "create"
            elif expected_previous != active["id"]:
                raise ExternalTreeLeadConflict(
                    "Stale oczekiwany poprzedni anchor komponentu"
                )
            elif active["anchor_occurrence_id"] == anchor_occurrence_id:
                if supersedes_anchor_id is not None:
                    raise ExternalTreeLeadConflict(
                        "Nie można supersede niezmienionej aktywnej decyzji anchoru"
                    )
                action = "existing"
            else:
                if supersedes_anchor_id != active["id"]:
                    raise ExternalTreeLeadConflict(
                        "Korekta anchoru wymaga jawnego supersedesAnchorId aktywnej decyzji"
                    )
                action = "supersede"
            anchor_id = (
                active["id"]
                if action == "existing"
                else stable_id(
                    "source_reported_network_anchor",
                    case_id,
                    declared_fingerprint,
                    anchor_occurrence_id,
                    supersedes_anchor_id or "initial",
                    payload["manifestFingerprint"],
                )
            )
            anchor["_anchorId"] = anchor_id
            anchor["_anchorOccurrenceId"] = anchor_occurrence_id
            anchor["_anchorProfileKey"] = profile_key
            anchor["_action"] = action
            anchor["_activeAnchorId"] = active["id"] if active else None
            anchor["_supersedesAnchorId"] = supersedes_anchor_id
            anchor["_rationale"] = rationale_value
            planned["source_reported_network_anchors"].append(anchor_id)
            anchor_decisions.append(
                {
                    "anchorId": anchor_id,
                    "componentFingerprint": declared_fingerprint,
                    "anchorOccurrenceId": anchor_occurrence_id,
                    "profileKey": profile_key,
                    "action": action,
                    "supersedesAnchorId": supersedes_anchor_id,
                    "expectedPreviousAnchorId": expected_previous,
                    "rationale": rationale_value,
                }
            )

    for item in assessments:
        if not isinstance(item, dict):
            raise ExternalTreeLeadError("Każda ocena profilu musi być obiektem")
        key = _text(item.get("id"), "assessment.id")
        if service == "familysearch":
            profile_key = _text(item.get("pid"), f"profileAssessments[{key}].pid")
        else:
            profile_key = _resolve_profile_reference(
                payload,
                {
                    "subjectPersonId": item.get("personId", item.get("pid")),
                    "subjectTreeId": item.get("treeId", payload.get("treeId")),
                },
                "subject",
                by_key,
                by_person_id,
            )
        if profile_key not in by_key:
            raise ExternalTreeLeadError(f"Ocena {key} wskazuje profil spoza manifestu")
        item["_profileKey"] = profile_key
        if item.get("status") not in {"unresolved_high_conflict", "profile_conflation"}:
            raise ExternalTreeLeadError(f"Nieprawidłowy status oceny {key}")
        item["statement"] = _text(item.get("statement"), f"profileAssessments[{key}].statement")
        item["rationale"] = _text(item.get("rationale"), f"profileAssessments[{key}].rationale")
        planned["claims"].append(
            _assessment_ids(service, item, profile_key=profile_key)["claimId"]
        )
        conflicts = item.get("conflicts", [])
        if not isinstance(conflicts, list):
            raise ExternalTreeLeadError(f"Konflikty oceny {key} muszą być listą")
        for index, conflict in enumerate(conflicts, start=1):
            if not isinstance(conflict, dict):
                raise ExternalTreeLeadError(f"Konflikt oceny {key} musi być obiektem")
            _text(conflict.get("title"), f"profileAssessments[{key}].conflicts[{index}].title")
            _text(conflict.get("description"), f"profileAssessments[{key}].conflicts[{index}].description")
            planned["conflicts"].append(
                _conflict_id(
                    service, "profile", key, index, profile_key=profile_key
                )
            )

    local_occurrence_ids: set[str] = set()
    local_source_ids: set[str] = set()
    for item in identities:
        if not isinstance(item, dict):
            raise ExternalTreeLeadError("Każda kandydatura identity musi być obiektem")
        if service == "familysearch":
            profile_key = _text(item.get("externalPid"), "identityCandidate.externalPid")
        else:
            profile_key = _resolve_profile_reference(
                payload,
                {
                    "subjectPersonId": item.get(
                        "externalPersonId", item.get("externalPid")
                    ),
                    "subjectTreeId": item.get(
                        "externalTreeId", payload.get("treeId")
                    ),
                },
                "subject",
                by_key,
                by_person_id,
            )
        local_id = _text(item.get("localOccurrenceId"), "identityCandidate.localOccurrenceId")
        if profile_key not in by_key:
            raise ExternalTreeLeadError(
                f"Kandydatura identity wskazuje profil spoza manifestu: {profile_key}"
            )
        if item.get("status", "unresolved") != "unresolved":
            raise ExternalTreeLeadError("Import zewnętrznego drzewa nie może rozstrzygać identity")
        item["_profileKey"] = profile_key
        item["_basisEnvelope"] = _identity_basis_envelope(payload, item)
        local_occurrence_ids.add(local_id)
        local_source_ids.update(str(value) for value in item.get("localSourceIds", []))
        planned["identity_candidates"].append(
            _identity_candidate_id(service, profile_key, local_id)
        )
    for item in relationships:
        local_source_ids.update(str(value) for value in item.get("localSupportSourceIds", []))
        local_occurrence_ids.update(str(value) for value in item.get("localSupportOccurrenceIds", []))

    missing_occurrences = [
        value for value in sorted(local_occurrence_ids)
        if connection.execute("SELECT 1 FROM person_occurrences WHERE id=?", (value,)).fetchone() is None
    ]
    missing_sources = [
        value for value in sorted(local_source_ids)
        if connection.execute("SELECT 1 FROM source_items WHERE id=?", (value,)).fetchone() is None
    ]
    if missing_occurrences or missing_sources:
        raise ExternalTreeLeadError(
            "Brak lokalnych podstaw manifestu: "
            + canonical_json({"occurrences": missing_occurrences, "sources": missing_sources})
        )

    existing = {table: _existing_rows(connection, table, ids) for table, ids in planned.items()}
    collisions: list[dict[str, str]] = []
    for profile_key, profile in by_key.items():
        ids = _profile_ids_for(profile)
        source = existing["source_items"].get(ids["sourceItemId"])
        expected_source = _profile_source_values(payload, profile)
        if source:
            if service == "familysearch" and not profile.get("materializeCandidate", False):
                mismatch = (
                    source["source_url"] != profile["url"]
                    or source["item_type"] != "external_tree_profile"
                )
            else:
                mismatch = any(source[field] != value for field, value in expected_source.items())
            if mismatch:
                collisions.append({"type": "source_item_payload", "id": ids["sourceItemId"]})
        if ids["sourcePrivacyId"] in planned["privacy_labels"]:
            source_privacy = existing["privacy_labels"].get(ids["sourcePrivacyId"])
            if source_privacy and (
                source_privacy["object_type"] != "source_item"
                or source_privacy["object_id"] != ids["sourceItemId"]
                or source_privacy["classification"] != "restricted"
            ):
                collisions.append(
                    {"type": "source_privacy_payload", "id": ids["sourcePrivacyId"]}
                )
        occurrence = existing["person_occurrences"].get(ids["occurrenceId"])
        expected_person_id = ids.get("personId")
        if occurrence:
            allowed_person_ids = (
                {None, expected_person_id} if expected_person_id is not None else {None}
            )
            if occurrence["person_id"] not in allowed_person_ids:
                collisions.append({"type": "canonical_identity", "id": ids["occurrenceId"]})
            if service == "myheritage" or profile.get("materializeCandidate", False):
                expected_occurrence = {
                    "source_item_id": ids["sourceItemId"],
                    "raw_name": profile["name"],
                    "normalized_name": profile["name"],
                    "search_name": search_fold(profile["name"]),
                    "role_text": "subject of external tree profile",
                    "verification_status": "imported_unverified",
                    "evidence_json": canonical_json(_profile_metadata(payload, profile)),
                }
                if any(
                    occurrence[field] != value
                    for field, value in expected_occurrence.items()
                ):
                    collisions.append(
                        {"type": "person_occurrence_payload", "id": ids["occurrenceId"]}
                    )
        if profile.get("materializeCandidate", False):
            person = existing["persons"].get(ids["personId"])
            if person and (
                person["preferred_name"] != profile["name"]
                or person["status"] != "candidate"
            ):
                collisions.append({"type": "candidate_person_payload", "id": ids["personId"]})
            person_name = existing["person_names"].get(ids["personNameId"])
            if person_name and (
                person_name["person_id"] != ids["personId"]
                or person_name["name_text"] != profile["name"]
                or person_name["normalized_name"] != search_fold(profile["name"])
                or person_name["name_type"] != "external_tree"
            ):
                collisions.append({"type": "candidate_name_payload", "id": ids["personNameId"]})
            privacy = existing["privacy_labels"].get(ids["privacyId"])
            if privacy and (
                privacy["object_type"] != "person"
                or privacy["object_id"] != ids["personId"]
                or privacy["classification"] != profile["privacyClassification"]
            ):
                collisions.append({"type": "candidate_privacy_payload", "id": ids["privacyId"]})

    for anchor in network_anchors or []:
        if anchor["_action"] == "existing":
            continue
        existing_anchor = existing["source_reported_network_anchors"].get(
            anchor["_anchorId"]
        )
        if existing_anchor and (
            existing_anchor["case_id"] != case_id
            or existing_anchor["component_fingerprint"]
            != anchor["componentFingerprint"]
            or existing_anchor["anchor_occurrence_id"]
            != anchor["_anchorOccurrenceId"]
            or existing_anchor["status"] != "active"
            or existing_anchor["actor"] != payload["actor"]
            or existing_anchor["rationale"] != anchor["_rationale"]
            or existing_anchor["supersedes_anchor_id"]
            != anchor["_supersedesAnchorId"]
        ):
            collisions.append(
                {"type": "network_anchor_payload", "id": anchor["_anchorId"]}
            )

    for item in relationships:
        subject_key = item["_subjectKey"]
        object_key = item["_objectKey"]
        ids = _relationship_ids(
            service,
            item,
            subject_key=subject_key,
            object_key=object_key,
            case_id=item["_claimCaseScope"],
        )
        subject_ids = _profile_ids_for(by_key[subject_key])
        object_ids = _profile_ids_for(by_key[object_key])
        provenance = {
            "service": service,
            "observedAt": payload["observedAt"],
            "status": item["status"],
            "cluster": item.get("cluster"),
            "provenance": item.get("provenance", {}),
            "localSupportSourceIds": item.get("localSupportSourceIds", []),
            "localSupportOccurrenceIds": item.get("localSupportOccurrenceIds", []),
            "noAutoMerge": True,
            "noCanonicalRelationship": True,
        }
        assertion_expected = {
            "source_item_id": subject_ids["sourceItemId"],
            "assertion_type": "external_tree_relationship",
            "subject_ref": subject_ids["occurrenceId"],
            "predicate": item["predicate"],
            "object_text": object_ids["occurrenceId"],
            "information_quality": "Indeterminable",
            "reading_certainty": "Ambiguous",
            "verification_status": "imported_unverified",
            "metadata_json": canonical_json(provenance),
        }
        if _payload_differs(
            existing["source_assertions"].get(ids["assertionId"]),
            assertion_expected,
        ):
            collisions.append(
                {"type": "source_assertion_payload", "id": ids["assertionId"]}
            )
        service_label = "FamilySearch" if service == "familysearch" else "MyHeritage"
        statement = item.get("statement") or (
            f"{service_label} wskazuje {by_key[subject_key]['name']} "
            f"{item['predicate']} {by_key[object_key]['name']}"
        )
        claim_expected = {
            "case_id": case_id,
            "statement": statement,
            "subject_ref": subject_ids["occurrenceId"],
            "predicate": item["predicate"],
            "object_ref": object_ids["occurrenceId"],
            "status": item["status"],
            "rationale": item.get("rationale") or payload["rationale"],
        }
        if _payload_differs(existing["claims"].get(ids["claimId"]), claim_expected):
            collisions.append({"type": "claim_payload", "id": ids["claimId"]})
        dependency_group = item.get(
            "dependencyGroup",
            "familysearch-collaborative-tree"
            if service == "familysearch"
            else "myheritage-member-tree",
        )
        evidence_note = (
            "Zależny zapis z drzewa współtworzonego; nie jest niezależnym dowodem."
            if service == "familysearch"
            else "Zależny zapis z drzewa zewnętrznego; nie jest niezależnym dowodem."
        )
        evidence_expected = {
            "claim_id": ids["claimId"],
            "source_assertion_id": ids["assertionId"],
            "stance": "supports",
            "evidence_type": "Authored",
            "dependency_group": dependency_group,
            "note": evidence_note,
        }
        if _payload_differs(
            existing["claim_evidence"].get(ids["claimEvidenceId"]),
            evidence_expected,
        ):
            collisions.append(
                {"type": "claim_evidence_payload", "id": ids["claimEvidenceId"]}
            )
        for index, conflict in enumerate(item.get("conflicts", []), start=1):
            conflict_id = _conflict_id(
                service,
                "relation",
                item["id"],
                index,
                subject_key=subject_key,
                object_key=object_key,
                case_id=item["_claimCaseScope"],
            )
            conflict_expected = {
                "claim_id": ids["claimId"],
                "title": conflict["title"],
                "description": conflict["description"],
                "status": "open",
            }
            if _payload_differs(
                existing["conflicts"].get(conflict_id), conflict_expected
            ):
                collisions.append({"type": "conflict_payload", "id": conflict_id})

    for item in assessments:
        profile_key = item["_profileKey"]
        claim_id = _assessment_ids(
            service, item, profile_key=profile_key
        )["claimId"]
        profile_ids = _profile_ids_for(by_key[profile_key])
        assessment_expected = {
            "case_id": case_id,
            "statement": item["statement"],
            "subject_ref": profile_ids["occurrenceId"],
            "predicate": "profile_assessment",
            "object_ref": None,
            "status": item["status"],
            "rationale": item["rationale"],
        }
        if _payload_differs(
            existing["claims"].get(claim_id), assessment_expected
        ):
            collisions.append({"type": "claim_payload", "id": claim_id})
        for index, conflict in enumerate(item.get("conflicts", []), start=1):
            conflict_id = _conflict_id(
                service,
                "profile",
                item["id"],
                index,
                profile_key=profile_key,
            )
            conflict_expected = {
                "claim_id": claim_id,
                "title": conflict["title"],
                "description": conflict["description"],
                "status": "open",
            }
            if _payload_differs(
                existing["conflicts"].get(conflict_id), conflict_expected
            ):
                collisions.append({"type": "conflict_payload", "id": conflict_id})

    for item in identities:
        profile_key = item["_profileKey"]
        candidate_id = _identity_candidate_id(
            service, profile_key, item["localOccurrenceId"]
        )
        basis = _external_identity_basis(payload, item, profile_key)
        identity_expected = {
            "occurrence_a_id": _profile_ids_for(by_key[profile_key])["occurrenceId"],
            "occurrence_b_id": item["localOccurrenceId"],
            "score": item.get("score"),
            "basis_json": canonical_json(basis),
            "status": "unresolved",
        }
        if _payload_differs(
            existing["identity_candidates"].get(candidate_id), identity_expected
        ):
            collisions.append(
                {"type": "identity_candidate_payload", "id": candidate_id}
            )
    if collisions:
        raise ExternalTreeLeadConflict("Kolizja z istniejącym stanem: " + canonical_json(collisions))

    anchor_component_fingerprints = [
        item["componentFingerprint"] for item in relationship_components
    ]
    if anchor_component_fingerprints:
        marks = ",".join("?" for _ in anchor_component_fingerprints)
        anchor_history = [
            dict(row)
            for row in connection.execute(
                f"""SELECT * FROM source_reported_network_anchors
                    WHERE case_id=? AND component_fingerprint IN ({marks})
                    ORDER BY component_fingerprint,created_at,id""",
                (case_id, *anchor_component_fingerprints),
            )
        ]
    else:
        anchor_history = []
    dependencies = {
        "case": dict(case),
        "localSources": _existing_rows(connection, "source_items", sorted(local_source_ids)),
        "localOccurrences": _existing_rows(connection, "person_occurrences", sorted(local_occurrence_ids)),
        "existingDesiredRows": existing,
        "relationshipComponents": relationship_components,
        "networkAnchorHistory": anchor_history,
    }
    counts = {
        "profiles": len(profiles),
        "relationships": len(relationships),
        "profileAssessments": len(assessments),
        "identityCandidates": len(identities),
        "conflicts": len(planned["conflicts"]),
        "candidatePersons": len(planned["persons"]),
        "networkAnchors": len(anchor_decisions),
    }
    materialized_persons = []
    linked_occurrences = []
    privacy_labels = []
    for profile_key, profile in sorted(by_key.items()):
        ids = _profile_ids_for(profile)
        if ids["sourcePrivacyId"] in planned["privacy_labels"]:
            privacy_labels.append(
                {
                    "id": ids["sourcePrivacyId"],
                    "objectType": "source_item",
                    "objectId": ids["sourceItemId"],
                    "classification": "restricted",
                    "action": (
                        "existing"
                        if ids["sourcePrivacyId"] in existing["privacy_labels"]
                        else "create"
                    ),
                }
            )
        if not profile.get("materializeCandidate", False):
            continue
        materialized_persons.append(
            {
                "profileKey": profile_key,
                "personId": ids["personId"],
                "action": (
                    "existing"
                    if ids["personId"] in existing["persons"]
                    else "create"
                ),
            }
        )
        occurrence = existing["person_occurrences"].get(ids["occurrenceId"])
        linked_occurrences.append(
            {
                "profileKey": profile_key,
                "occurrenceId": ids["occurrenceId"],
                "personId": ids["personId"],
                "action": (
                    "existing"
                    if occurrence and occurrence["person_id"] == ids["personId"]
                    else "link"
                ),
            }
        )
        privacy_labels.append(
            {
                "id": ids["privacyId"],
                "objectType": "person",
                "objectId": ids["personId"],
                "classification": profile["privacyClassification"],
                "action": (
                    "existing"
                    if ids["privacyId"] in existing["privacy_labels"]
                    else "create"
                ),
            }
        )
    tracked_tables = (
        "persons",
        "person_occurrences",
        "privacy_labels",
        "source_reported_network_anchors",
        "identity_decisions",
    )
    before_counts = {
        table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        for table in tracked_tables
    }
    predicted_inserts = {
        "persons": sum(item["action"] == "create" for item in materialized_persons),
        "person_occurrences": sum(
            ids not in existing["person_occurrences"]
            for ids in planned["person_occurrences"]
        ),
        "privacy_labels": sum(
            item["action"] == "create" for item in privacy_labels
        ),
        "source_reported_network_anchors": sum(
            item["action"] in {"create", "supersede"}
            for item in anchor_decisions
        ),
        "identity_decisions": 0,
    }
    before_after_counts = {
        table: {
            "before": before_counts[table],
            "after": before_counts[table] + predicted_inserts[table],
        }
        for table in tracked_tables
    }
    projection_refresh_state = (
        "pending"
        if any(
            len(existing[table]) < len(set(ids))
            for table, ids in planned.items()
        )
        or any(item["action"] == "supersede" for item in anchor_decisions)
        else "not_required"
    )
    return {
        "caseId": case_id,
        "service": service,
        "counts": counts,
        "plannedIds": planned,
        "profileIds": {
            key: _profile_ids_for(by_key[key]) for key in sorted(by_key)
        },
        "relationshipComponents": relationship_components,
        "materializedPersons": materialized_persons,
        "linkedOccurrences": linked_occurrences,
        "privacyLabels": privacy_labels,
        "anchorDecisions": anchor_decisions,
        "beforeAfterCounts": before_after_counts,
        "identityDecisionsCreated": 0,
        "projectionRefreshState": projection_refresh_state,
        "dependencyFingerprint": fingerprint(dependencies),
        "existingCounts": {table: len(rows) for table, rows in existing.items()},
    }


def external_tree_leads_preview(root: Path, manifest_path: Path) -> dict[str, Any]:
    payload = _load(manifest_path)
    initialize(root)
    with connect(root) as connection:
        require_tables(connection, REQUIRED_TABLES)
        connection.execute("PRAGMA query_only=ON")
        preview = _validate(connection, payload)
    accepted = fingerprint(
        {"manifest": payload["manifestFingerprint"], "dependencies": preview["dependencyFingerprint"]}
    )
    return {
        "mode": "preview",
        "status": "ready",
        "manifestFingerprint": payload["manifestFingerprint"],
        "fingerprint": accepted,
        **preview,
    }


def apply_external_tree_leads(
    root: Path,
    manifest_path: Path,
    *,
    expected_fingerprint: str,
) -> dict[str, Any]:
    payload = _load(manifest_path)
    initialize(root)
    actor = payload["actor"]
    rationale = payload["rationale"]
    command_id = payload["commandId"]
    now = utc_now()
    with connect(root) as connection:
        require_tables(connection, REQUIRED_TABLES)
        connection.execute("BEGIN IMMEDIATE")
        request_fingerprint = fingerprint(
            {"manifest": payload["manifestFingerprint"], "actor": actor, "rationale": rationale}
        )
        try:
            replay = replay_command(
                connection,
                command_id=command_id,
                command_type="external_tree_leads_import",
                object_type="research_case",
                object_id=payload["caseId"],
                request_fingerprint=request_fingerprint,
            )
        except CommandEnvelopeConflict as error:
            raise ExternalTreeLeadConflict(str(error)) from error
        if replay is not None:
            return {
                **replay,
                "mode": "apply",
                "status": "no_op",
                "projectionRefreshState": replay["projectionStatus"],
            }

        preview = _validate(connection, payload)
        current = fingerprint(
            {"manifest": payload["manifestFingerprint"], "dependencies": preview["dependencyFingerprint"]}
        )
        if current != expected_fingerprint:
            raise ExternalTreeLeadConflict("Manifest lub lokalne podstawy zmieniły się po preview")

        service = payload["service"]
        profile_map = {item["_profileKey"]: item for item in payload["profiles"]}
        for profile_key, item in profile_map.items():
            ids = _profile_ids_for(item)
            metadata = _profile_metadata(payload, item)
            source = _profile_source_values(payload, item)
            connection.execute(
                """INSERT OR IGNORE INTO source_items(
                       id,title,item_type,date_text,locator,source_url,citation_text,
                       source_quality,verification_status,metadata_json
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    ids["sourceItemId"],
                    source["title"],
                    source["item_type"],
                    source["date_text"],
                    source["locator"],
                    source["source_url"],
                    source["citation_text"],
                    source["source_quality"],
                    source["verification_status"],
                    source["metadata_json"],
                ),
            )
            if item.get("sourcePrivacyClassification") == "restricted":
                connection.execute(
                    """INSERT OR IGNORE INTO privacy_labels(
                           id,object_type,object_id,classification,rationale,
                           created_at,updated_at
                       ) VALUES(?,'source_item',?,'restricted',?,?,?)""",
                    (
                        ids["sourcePrivacyId"],
                        ids["sourceItemId"],
                        _source_privacy_rationale(service, profile_key),
                        now,
                        now,
                    ),
                )
            if item.get("materializeCandidate", False):
                connection.execute(
                    """INSERT OR IGNORE INTO persons(
                           id,preferred_name,status,created_at,notes
                       ) VALUES(?,?,'candidate',?,?)""",
                    (
                        ids["personId"],
                        item["name"],
                        now,
                        (
                            f"Standalone candidate materialized from {service} profile "
                            f"{profile_key}; imported_unverified; no automatic merge."
                        ),
                    ),
                )
                connection.execute(
                    """INSERT OR IGNORE INTO person_names(
                           id,person_id,name_text,normalized_name,name_type
                       ) VALUES(?,?,?,?,'external_tree')""",
                    (
                        ids["personNameId"],
                        ids["personId"],
                        item["name"],
                        search_fold(item["name"]),
                    ),
                )
                connection.execute(
                    """INSERT OR IGNORE INTO privacy_labels(
                           id,object_type,object_id,classification,rationale,
                           created_at,updated_at
                       ) VALUES(?,'person',?,?,?, ?, ?)""",
                    (
                        ids["privacyId"],
                        ids["personId"],
                        item["privacyClassification"],
                        (
                            f"Explicit privacy classification for {service} candidate "
                            f"{profile_key}; external-tree import does not infer living status."
                        ),
                        now,
                        now,
                    ),
                )
            connection.execute(
                """INSERT OR IGNORE INTO person_occurrences(
                       id,source_item_id,person_id,raw_name,normalized_name,search_name,
                       role_text,verification_status,evidence_json
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    ids["occurrenceId"], ids["sourceItemId"], ids.get("personId"),
                    item["name"], item["name"],
                    search_fold(item["name"]), "subject of external tree profile",
                    "imported_unverified", canonical_json(metadata),
                ),
            )
            if ids.get("personId") is not None:
                connection.execute(
                    """UPDATE person_occurrences
                       SET person_id=?
                       WHERE id=? AND person_id IS NULL""",
                    (ids["personId"], ids["occurrenceId"]),
                )

        relationship_rows: list[dict[str, Any]] = []
        for item in payload.get("relationships", []):
            subject_key = item["_subjectKey"]
            object_key = item["_objectKey"]
            ids = _relationship_ids(
                service,
                item,
                subject_key=subject_key,
                object_key=object_key,
                case_id=item["_claimCaseScope"],
            )
            subject_ids = _profile_ids_for(profile_map[subject_key])
            object_ids = _profile_ids_for(profile_map[object_key])
            provenance = {
                "service": service,
                "observedAt": payload["observedAt"],
                "status": item["status"],
                "cluster": item.get("cluster"),
                "provenance": item.get("provenance", {}),
                "localSupportSourceIds": item.get("localSupportSourceIds", []),
                "localSupportOccurrenceIds": item.get("localSupportOccurrenceIds", []),
                "noAutoMerge": True,
                "noCanonicalRelationship": True,
            }
            connection.execute(
                """INSERT OR IGNORE INTO source_assertions(
                       id,source_item_id,assertion_type,subject_ref,predicate,object_text,
                       information_quality,reading_certainty,verification_status,metadata_json
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    ids["assertionId"], subject_ids["sourceItemId"], "external_tree_relationship",
                    subject_ids["occurrenceId"], item["predicate"], object_ids["occurrenceId"],
                    "Indeterminable", "Ambiguous", "imported_unverified",
                    canonical_json(provenance),
                ),
            )
            statement = item.get("statement") or (
                f"{'FamilySearch' if service == 'familysearch' else 'MyHeritage'} wskazuje "
                f"{profile_map[subject_key]['name']} {item['predicate']} "
                f"{profile_map[object_key]['name']}"
            )
            connection.execute(
                """INSERT OR IGNORE INTO claims(
                       id,case_id,statement,subject_ref,predicate,object_ref,status,
                       rationale,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    ids["claimId"], payload["caseId"], statement,
                    subject_ids["occurrenceId"], item["predicate"], object_ids["occurrenceId"],
                    item["status"], item.get("rationale") or rationale, now, now,
                ),
            )
            connection.execute(
                """INSERT OR IGNORE INTO claim_evidence(
                       id,claim_id,source_assertion_id,stance,evidence_type,dependency_group,note
                   ) VALUES(?,?,?,?,?,?,?)""",
                (
                    ids["claimEvidenceId"], ids["claimId"], ids["assertionId"], "supports",
                    "Authored",
                    item.get(
                        "dependencyGroup",
                        "familysearch-collaborative-tree"
                        if service == "familysearch"
                        else "myheritage-member-tree",
                    ),
                    (
                        "Zależny zapis z drzewa współtworzonego; nie jest niezależnym dowodem."
                        if service == "familysearch"
                        else "Zależny zapis z drzewa zewnętrznego; nie jest niezależnym dowodem."
                    ),
                ),
            )
            conflict_ids = []
            for index, conflict in enumerate(item.get("conflicts", []), start=1):
                conflict_id = _conflict_id(
                    service,
                    "relation",
                    item["id"],
                    index,
                    subject_key=subject_key,
                    object_key=object_key,
                    case_id=item["_claimCaseScope"],
                )
                conflict_ids.append(conflict_id)
                connection.execute(
                    """INSERT OR IGNORE INTO conflicts(
                           id,claim_id,title,description,status,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?)""",
                    (
                        conflict_id, ids["claimId"], conflict["title"], conflict["description"],
                        "open", now, now,
                    ),
                )
            relationship_rows.append({"id": item["id"], **ids, "status": item["status"], "conflictIds": conflict_ids})

        anchor_rows: list[dict[str, Any]] = []
        for item in payload.get("networkAnchors", []):
            action = item["_action"]
            if action == "supersede":
                updated = connection.execute(
                    """UPDATE source_reported_network_anchors
                       SET status='superseded'
                       WHERE id=? AND case_id=? AND component_fingerprint=?
                         AND status='active'""",
                    (
                        item["_supersedesAnchorId"],
                        payload["caseId"],
                        item["componentFingerprint"],
                    ),
                )
                if updated.rowcount != 1:
                    raise ExternalTreeLeadConflict(
                        "Aktywny anchor zmienił się podczas korekty"
                    )
            if action in {"create", "supersede"}:
                connection.execute(
                    """INSERT INTO source_reported_network_anchors(
                           id,case_id,component_fingerprint,anchor_occurrence_id,
                           status,actor,rationale,created_at,supersedes_anchor_id
                       ) VALUES(?,?,?,?, 'active',?,?,?,?)""",
                    (
                        item["_anchorId"],
                        payload["caseId"],
                        item["componentFingerprint"],
                        item["_anchorOccurrenceId"],
                        actor,
                        item["_rationale"],
                        now,
                        item["_supersedesAnchorId"],
                    ),
                )
            anchor_rows.append(
                {
                    "anchorId": item["_anchorId"],
                    "componentFingerprint": item["componentFingerprint"],
                    "anchorOccurrenceId": item["_anchorOccurrenceId"],
                    "profileKey": item["_anchorProfileKey"],
                    "action": action,
                    "supersedesAnchorId": item["_supersedesAnchorId"],
                    "actor": actor,
                    "rationale": item["_rationale"],
                }
            )

        assessment_rows: list[dict[str, Any]] = []
        for item in payload.get("profileAssessments", []):
            profile_key = item["_profileKey"]
            claim_id = _assessment_ids(
                service, item, profile_key=profile_key
            )["claimId"]
            profile_ids = _profile_ids_for(profile_map[profile_key])
            connection.execute(
                """INSERT OR IGNORE INTO claims(
                       id,case_id,statement,subject_ref,predicate,object_ref,status,
                       rationale,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    claim_id, payload["caseId"], item["statement"], profile_ids["occurrenceId"],
                    "profile_assessment", None, item["status"], item["rationale"], now, now,
                ),
            )
            conflict_ids = []
            for index, conflict in enumerate(item.get("conflicts", []), start=1):
                conflict_id = _conflict_id(
                    service,
                    "profile",
                    item["id"],
                    index,
                    profile_key=profile_key,
                )
                conflict_ids.append(conflict_id)
                connection.execute(
                    """INSERT OR IGNORE INTO conflicts(
                           id,claim_id,title,description,status,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?)""",
                    (conflict_id, claim_id, conflict["title"], conflict["description"], "open", now, now),
                )
            assessment_rows.append({"id": item["id"], "claimId": claim_id, "status": item["status"], "conflictIds": conflict_ids})

        identity_rows: list[dict[str, Any]] = []
        for item in payload.get("identityCandidates", []):
            profile_key = item["_profileKey"]
            candidate_id = _identity_candidate_id(
                service, profile_key, item["localOccurrenceId"]
            )
            basis = _external_identity_basis(payload, item, profile_key)
            connection.execute(
                """INSERT OR IGNORE INTO identity_candidates(
                       id,occurrence_a_id,occurrence_b_id,score,basis_json,status,created_at
                   ) VALUES(?,?,?,?,?,'unresolved',?)""",
                (
                    candidate_id,
                    _profile_ids_for(profile_map[profile_key])["occurrenceId"],
                    item["localOccurrenceId"], item.get("score"), canonical_json(basis), now,
                ),
            )
            identity_row = {
                "id": candidate_id,
                "localOccurrenceId": item["localOccurrenceId"],
                "status": "unresolved",
                "presentationStatus": basis["presentationStatus"],
                "scoreSemantics": basis["score_semantics"],
                "basis": basis["basis"],
                "support": basis["support"],
                "against": basis["against"],
                "humanReviewDebt": basis["human_review_debt"],
                "createdBy": basis["created_by"],
            }
            if service == "familysearch":
                identity_row["externalPid"] = profile_key
            else:
                identity_row["externalProfileKey"] = profile_key
            identity_rows.append(identity_row)

        after_counts = {
            table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in preview["beforeAfterCounts"]
        }
        before_after_counts = {
            table: {
                "before": values["before"],
                "after": after_counts[table],
            }
            for table, values in preview["beforeAfterCounts"].items()
        }
        result = {
            "commandId": command_id,
            "caseId": payload["caseId"],
            "manifestFingerprint": payload["manifestFingerprint"],
            "inputFingerprint": expected_fingerprint,
            "profiles": preview["profileIds"],
            "relationships": relationship_rows,
            "profileAssessments": assessment_rows,
            "identityCandidates": identity_rows,
            "materializedPersons": preview["materializedPersons"],
            "linkedOccurrences": preview["linkedOccurrences"],
            "privacyLabels": preview["privacyLabels"],
            "anchorDecisions": anchor_rows,
            "beforeAfterCounts": before_after_counts,
            "counts": preview["counts"],
            "externalWrites": 0,
            "canonicalPersonsCreated": 0,
            "candidatePersonsCreated": (
                preview["counts"]["candidatePersons"]
                - preview["existingCounts"]["persons"]
            ),
            "identityDecisionsCreated": 0,
            "projectionStatus": preview["projectionRefreshState"],
            "projectionRefreshState": preview["projectionRefreshState"],
        }
        record_command(
            connection,
            command_id=command_id,
            command_type="external_tree_leads_import",
            object_type="research_case",
            object_id=payload["caseId"],
            input_fingerprint=expected_fingerprint,
            request_fingerprint=request_fingerprint,
            actor=actor,
            rationale=rationale,
            result=result,
            projection_status=preview["projectionRefreshState"],
        )
        cursor = connection.execute(
            """INSERT INTO audit_log(
                   occurred_at,actor,action,object_type,object_id,before_json,after_json,rationale
               ) VALUES (?,?,'external_tree_leads_import','research_case',?,NULL,?,?)""",
            (now, actor, payload["caseId"], canonical_json(result), rationale),
        )
        connection.commit()
        return {"mode": "apply", "status": "applied", "auditId": int(cursor.lastrowid), **result}


__all__ = [
    "ExternalTreeLeadConflict",
    "ExternalTreeLeadError",
    "apply_external_tree_leads",
    "external_tree_leads_preview",
]
