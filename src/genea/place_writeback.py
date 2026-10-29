from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .command_envelope import (
    CommandEnvelopeConflict,
    canonical_json,
    fingerprint,
    record_command,
    replay_command,
)
from .db import connect, initialize
from .research import require_tables
from .workspace import search_fold, utc_now


MINIMUM_SCHEMA_VERSION = 21
REQUIRED_TABLES = {
    "audit_log",
    "citations",
    "curation_commands",
    "events",
    "households",
    "household_memberships",
    "participations",
    "place_jurisdictions",
    "place_names",
    "places",
    "privacy_labels",
    "source_assertions",
    "source_items",
}
WRITEBACK_STATUSES = {"draft", "reviewed", "approved"}

UPSERT_SPECS: dict[str, tuple[str, dict[str, str], set[str]]] = {
    "places": (
        "places",
        {
            "preferredName": "preferred_name",
            "placeType": "place_type",
            "latitude": "latitude",
            "longitude": "longitude",
            "notes": "notes",
        },
        {"preferredName"},
    ),
    "placeNames": (
        "place_names",
        {
            "placeId": "place_id",
            "nameText": "name_text",
            "normalizedName": "normalized_name",
            "language": "language",
            "dateFrom": "date_from",
            "dateTo": "date_to",
            "sourceItemId": "source_item_id",
        },
        {"placeId", "nameText", "sourceItemId"},
    ),
    "placeJurisdictions": (
        "place_jurisdictions",
        {
            "placeId": "place_id",
            "jurisdictionPlaceId": "jurisdiction_place_id",
            "jurisdictionName": "jurisdiction_name",
            "jurisdictionType": "jurisdiction_type",
            "dateFrom": "date_from",
            "dateTo": "date_to",
            "sourceItemId": "source_item_id",
        },
        {"placeId", "jurisdictionName", "sourceItemId"},
    ),
}

PATCH_SPECS: dict[str, tuple[str, dict[str, str], set[str], str]] = {
    "eventPlacePatches": (
        "events",
        {"placeId": "place_id", "placeText": "place_text"},
        {"placeId", "placeText"},
        "place_id",
    ),
    "participationResidencePatches": (
        "participations",
        {
            "residencePlaceId": "residence_place_id",
            "residenceText": "residence_text",
        },
        {"residencePlaceId", "residenceText"},
        "residence_place_id",
    ),
    "householdPlacePatches": (
        "households",
        {"placeId": "place_id"},
        {"placeId"},
        "place_id",
    ),
}


class PlaceWritebackConflict(ValueError):
    """The manifest, accepted preview, or command envelope no longer matches."""


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Pole {label} musi być niepustym tekstem")
    return value.strip()


def _optional_text(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Pole {label} musi być tekstem albo null")
    return value.strip() or None


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"Pole {label} musi być obiektem")
    return value


def _entries(value: Any, label: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"Pole {label} musi być listą obiektów")
    return value


def _load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Manifest place-writeback musi być obiektem JSON")
    allowed = {
        "version",
        "writebackId",
        "expectedDatabaseSchema",
        "status",
        "actor",
        "rationale",
        "privacyReviewed",
        "allowRestricted",
        "provenance",
        "operations",
        "manifestFingerprint",
    }
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError("Manifest zawiera niedozwolone pola: " + ", ".join(sorted(unknown)))
    if payload.get("version") != 1:
        raise ValueError("Nieobsługiwana wersja manifestu place-writeback")
    return payload


def _manifest_fingerprint(payload: dict[str, Any]) -> str:
    clean = dict(payload)
    clean.pop("manifestFingerprint", None)
    return fingerprint(clean)


def _row(connection, table: str, object_id: str) -> dict[str, Any] | None:  # type: ignore[no-untyped-def]
    row = connection.execute(f"SELECT * FROM {table} WHERE id=?", (object_id,)).fetchone()
    return dict(row) if row else None


def _rows(connection, table: str, ids: Iterable[str]) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
    values = sorted(set(ids))
    if not values:
        return []
    placeholders = ",".join("?" for _ in values)
    return [
        dict(row)
        for row in connection.execute(
            f"SELECT * FROM {table} WHERE id IN ({placeholders}) ORDER BY id",
            tuple(values),
        )
    ]


def _provenance(connection, payload: dict[str, Any]) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    raw = _object(payload.get("provenance"), "provenance")
    unknown = set(raw) - {"sourceItemIds", "citationIds", "basis"}
    if unknown:
        raise ValueError("provenance zawiera niedozwolone pola: " + ", ".join(sorted(unknown)))
    source_values = raw.get("sourceItemIds")
    if not isinstance(source_values, list) or not source_values:
        raise ValueError("provenance.sourceItemIds musi być niepustą listą")
    source_ids = [_required_text(value, "provenance.sourceItemIds[]") for value in source_values]
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("provenance.sourceItemIds zawiera duplikaty")
    citation_values = raw.get("citationIds", [])
    if not isinstance(citation_values, list):
        raise ValueError("provenance.citationIds musi być listą")
    citation_ids = [_required_text(value, "provenance.citationIds[]") for value in citation_values]
    if len(citation_ids) != len(set(citation_ids)):
        raise ValueError("provenance.citationIds zawiera duplikaty")
    basis = _required_text(raw.get("basis"), "provenance.basis")

    found_sources = {row["id"] for row in _rows(connection, "source_items", source_ids)}
    missing_sources = sorted(set(source_ids) - found_sources)
    if missing_sources:
        raise LookupError("Brak source_items z provenance: " + ", ".join(missing_sources))
    citations = _rows(connection, "citations", citation_ids)
    found_citations = {row["id"] for row in citations}
    missing_citations = sorted(set(citation_ids) - found_citations)
    if missing_citations:
        raise LookupError("Brak citations z provenance: " + ", ".join(missing_citations))
    invalid_citations = sorted(
        row["id"] for row in citations if row["source_item_id"] not in set(source_ids)
    )
    if invalid_citations:
        raise ValueError(
            "Cytowania nie należą do zadeklarowanych sourceItemIds: "
            + ", ".join(invalid_citations)
        )
    return {"sourceItemIds": source_ids, "citationIds": citation_ids, "basis": basis}


def _validate_header(connection, payload: dict[str, Any]) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    writeback_id = _required_text(payload.get("writebackId"), "writebackId")
    actor = _required_text(payload.get("actor"), "actor")
    rationale = _required_text(payload.get("rationale"), "rationale")
    status = _required_text(payload.get("status"), "status")
    if status not in WRITEBACK_STATUSES:
        raise ValueError("status musi być jednym z: " + ", ".join(sorted(WRITEBACK_STATUSES)))
    if payload.get("privacyReviewed") is not True:
        raise ValueError("Manifest wymaga jawnego privacyReviewed=true")
    current_schema = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if current_schema < MINIMUM_SCHEMA_VERSION:
        raise PlaceWritebackConflict(
            f"Niekompatybilna schema: place-writeback wymaga co najmniej {MINIMUM_SCHEMA_VERSION}, "
            f"baza ma {current_schema}"
        )
    if payload.get("expectedDatabaseSchema") != current_schema:
        raise PlaceWritebackConflict(
            "Manifest expectedDatabaseSchema nie odpowiada bieżącej wersji bazy"
        )
    provenance = _provenance(connection, payload)
    source_ids = provenance["sourceItemIds"]
    placeholders = ",".join("?" for _ in source_ids)
    restricted_sources = [
        row["object_id"]
        for row in connection.execute(
            f"""SELECT object_id FROM privacy_labels
                WHERE object_type='source_item'
                  AND object_id IN ({placeholders})
                  AND classification IN (
                      'living','restricted','family_private','strict_private'
                  )
                ORDER BY object_id""",
            tuple(source_ids),
        )
    ]
    if restricted_sources and payload.get("allowRestricted") is not True:
        raise PlaceWritebackConflict(
            "Chronione źródła wymagają jawnego allowRestricted=true: "
            + ", ".join(restricted_sources)
        )
    manifest_fingerprint = _manifest_fingerprint(payload)
    declared = payload.get("manifestFingerprint")
    if declared is not None and declared != manifest_fingerprint:
        raise PlaceWritebackConflict("Deklarowany manifestFingerprint jest niezgodny z treścią")
    return {
        "writebackId": writeback_id,
        "actor": actor,
        "rationale": rationale,
        "writebackStatus": status,
        "provenance": provenance,
        "databaseSchema": current_schema,
        "manifestFingerprint": manifest_fingerprint,
    }


def _normalize_values(
    value: Any,
    mapping: dict[str, str],
    label: str,
    *,
    nullable_ids: bool = False,
) -> dict[str, Any]:
    raw = _object(value, label)
    unknown = set(raw) - set(mapping)
    if unknown:
        raise ValueError(f"{label} zawiera niedozwolone pola: {', '.join(sorted(unknown))}")
    normalized: dict[str, Any] = {}
    for external, item in raw.items():
        column = mapping[external]
        if external in {
            "preferredName",
            "placeId",
            "nameText",
            "sourceItemId",
            "jurisdictionName",
            "residencePlaceId",
        }:
            normalized[column] = (
                None
                if nullable_ids and item is None
                else _required_text(item, f"{label}.{external}")
            )
        elif external in {"latitude", "longitude"}:
            if item is not None and (not isinstance(item, (int, float)) or isinstance(item, bool)):
                raise ValueError(f"{label}.{external} musi być liczbą albo null")
            normalized[column] = item
        else:
            normalized[column] = _optional_text(item, f"{label}.{external}")
    return normalized


def _normalize_upsert(
    entry: dict[str, Any],
    mapping: dict[str, str],
    required_for_create: set[str],
    label: str,
) -> tuple[str, dict[str, Any] | None, dict[str, Any]]:
    unknown = set(entry) - {"id", "expected", "set"}
    if unknown:
        raise ValueError(f"{label} zawiera niedozwolone pola: {', '.join(sorted(unknown))}")
    if "expected" not in entry:
        raise ValueError(f"{label}.expected musi być jawnie podane jako null albo obiekt")
    object_id = _required_text(entry.get("id"), f"{label}.id")
    expected = (
        None
        if entry["expected"] is None
        else _normalize_values(entry["expected"], mapping, f"{label}.expected")
    )
    target = _normalize_values(entry.get("set"), mapping, f"{label}.set")
    if not target:
        raise ValueError(f"{label}.set nie może być pusty")
    if expected is None:
        missing = required_for_create - set(entry["set"])
        if missing:
            raise ValueError(
                f"{label}.set dla tworzenia wymaga pól: {', '.join(sorted(missing))}"
            )
    if "nameText" in entry["set"] and "normalizedName" not in entry["set"]:
        target["normalized_name"] = search_fold(target["name_text"])
    return object_id, expected, target


def _validate_place_coordinates(values: dict[str, Any], label: str) -> None:
    latitude = values.get("latitude")
    longitude = values.get("longitude")
    if (latitude is None) != (longitude is None):
        raise ValueError(f"{label} wymaga pary latitude i longitude albo obu null")
    if latitude is not None and not -90 <= latitude <= 90:
        raise ValueError(f"{label}.latitude musi mieścić się w zakresie -90..90")
    if longitude is not None and not -180 <= longitude <= 180:
        raise ValueError(f"{label}.longitude musi mieścić się w zakresie -180..180")


def _normalize_patch(
    entry: dict[str, Any],
    mapping: dict[str, str],
    required_expected: set[str],
    target_column: str,
    label: str,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    unknown = set(entry) - {"id", "expected", "set"}
    if unknown:
        raise ValueError(f"{label} zawiera niedozwolone pola: {', '.join(sorted(unknown))}")
    object_id = _required_text(entry.get("id"), f"{label}.id")
    expected_raw = _object(entry.get("expected"), f"{label}.expected")
    if set(expected_raw) != required_expected:
        raise ValueError(
            f"{label}.expected musi zawierać dokładnie: {', '.join(sorted(required_expected))}"
        )
    target_raw = _object(entry.get("set"), f"{label}.set")
    target_external = next(key for key, value in mapping.items() if value == target_column)
    if set(target_raw) != {target_external}:
        raise ValueError(f"{label}.set może zawierać wyłącznie {target_external}")
    expected = _normalize_values(
        expected_raw, mapping, f"{label}.expected", nullable_ids=True
    )
    target = _normalize_values(target_raw, mapping, f"{label}.set")
    return object_id, expected, target


def _add_conflict(
    conflicts: list[dict[str, Any]],
    *,
    group: str,
    table: str,
    object_id: str,
    reason: str,
    **details: Any,
) -> None:
    conflict = {"group": group, "table": table, "id": object_id, "reason": reason, **details}
    if conflict not in conflicts:
        conflicts.append(conflict)


def _build_diff(connection, payload: dict[str, Any], header: dict[str, Any]) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    operations = _object(payload.get("operations"), "operations")
    unknown_groups = set(operations) - set(UPSERT_SPECS) - set(PATCH_SPECS)
    if unknown_groups:
        raise ValueError("Nieznane grupy operacji: " + ", ".join(sorted(unknown_groups)))
    changes: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    alias_effective: list[dict[str, Any]] = []

    for group, (table, mapping, required) in UPSERT_SPECS.items():
        for index, entry in enumerate(_entries(operations.get(group), f"operations.{group}")):
            label = f"operations.{group}[{index}]"
            object_id, expected, target = _normalize_upsert(entry, mapping, required, label)
            key = (table, object_id)
            if key in seen:
                raise ValueError(f"Powtórzony obiekt w manifeście: {table}/{object_id}")
            seen.add(key)
            current = _row(connection, table, object_id)
            if current is None:
                if expected is not None:
                    _add_conflict(
                        conflicts,
                        group=group,
                        table=table,
                        object_id=object_id,
                        reason="missing_expected_row",
                    )
                    effective = target
                else:
                    changes.append(
                        {"group": group, "table": table, "id": object_id, "action": "create", "target": target}
                    )
                    effective = {"id": object_id, **target}
            elif all(current.get(key) == value for key, value in target.items()):
                changes.append(
                    {"group": group, "table": table, "id": object_id, "action": "no_op", "target": target}
                )
                effective = dict(current)
            elif expected is None:
                _add_conflict(
                    conflicts,
                    group=group,
                    table=table,
                    object_id=object_id,
                    reason="existing_row_requires_expected",
                    current={key: current.get(key) for key in target},
                )
                effective = {**current, **target}
            elif not all(current.get(key) == value for key, value in expected.items()):
                _add_conflict(
                    conflicts,
                    group=group,
                    table=table,
                    object_id=object_id,
                    reason="optimistic_concurrency",
                    current={key: current.get(key) for key in set(expected) | set(target)},
                    expected=expected,
                    target=target,
                )
                effective = {**current, **target}
            else:
                changes.append(
                    {"group": group, "table": table, "id": object_id, "action": "update", "target": target}
                )
                effective = {**current, **target}
            if table == "places":
                _validate_place_coordinates(effective, label)
            if table == "place_names" and effective.get("normalized_name"):
                alias_effective.append(
                    {
                        "id": object_id,
                        "place_id": effective.get("place_id"),
                        "normalized_name": effective["normalized_name"],
                    }
                )

    for group, (table, mapping, required_expected, target_column) in PATCH_SPECS.items():
        for index, entry in enumerate(_entries(operations.get(group), f"operations.{group}")):
            label = f"operations.{group}[{index}]"
            object_id, expected, target = _normalize_patch(
                entry, mapping, required_expected, target_column, label
            )
            key = (table, object_id)
            if key in seen:
                raise ValueError(f"Powtórzony obiekt w manifeście: {table}/{object_id}")
            seen.add(key)
            current = _row(connection, table, object_id)
            if current is None:
                _add_conflict(
                    conflicts,
                    group=group,
                    table=table,
                    object_id=object_id,
                    reason="missing_row",
                )
            elif all(current.get(key) == value for key, value in target.items()):
                changes.append(
                    {"group": group, "table": table, "id": object_id, "action": "no_op", "target": target}
                )
            elif not all(current.get(key) == value for key, value in expected.items()):
                _add_conflict(
                    conflicts,
                    group=group,
                    table=table,
                    object_id=object_id,
                    reason="optimistic_concurrency",
                    current={key: current.get(key) for key in set(expected) | set(target)},
                    expected=expected,
                    target=target,
                )
            else:
                changes.append(
                    {"group": group, "table": table, "id": object_id, "action": "update", "target": target}
                )

    created_places = {
        change["id"]
        for change in changes
        if change["table"] == "places" and change["action"] == "create"
    }

    def place_exists(place_id: str | None) -> bool:
        return bool(
            place_id
            and (place_id in created_places or _row(connection, "places", place_id) is not None)
        )

    source_ids = set(header["provenance"]["sourceItemIds"])
    for change in changes:
        if change["action"] == "no_op":
            continue
        target = change["target"]
        table = change["table"]
        effective = {**(_row(connection, table, change["id"]) or {}), **target}
        if table in {"place_names", "place_jurisdictions"}:
            if not place_exists(effective.get("place_id")):
                _add_conflict(
                    conflicts,
                    group=change["group"],
                    table=table,
                    object_id=change["id"],
                    reason="missing_place_id",
                )
            if effective.get("source_item_id") not in source_ids:
                _add_conflict(
                    conflicts,
                    group=change["group"],
                    table=table,
                    object_id=change["id"],
                    reason="source_item_outside_provenance",
                )
        if table == "place_jurisdictions":
            jurisdiction_place_id = effective.get("jurisdiction_place_id")
            if jurisdiction_place_id and not place_exists(jurisdiction_place_id):
                _add_conflict(
                    conflicts,
                    group=change["group"],
                    table=table,
                    object_id=change["id"],
                    reason="missing_jurisdiction_place_id",
                )
        if table in {"events", "participations", "households"}:
            place_id = target.get("place_id") or target.get("residence_place_id")
            if not place_exists(place_id):
                _add_conflict(
                    conflicts,
                    group=change["group"],
                    table=table,
                    object_id=change["id"],
                    reason="missing_place_id",
                )
        if table == "events":
            current = _row(connection, table, change["id"]) or {}
            if current.get("source_item_id") not in source_ids:
                _add_conflict(
                    conflicts,
                    group=change["group"],
                    table=table,
                    object_id=change["id"],
                    reason="event_source_outside_provenance",
                )
        if table == "participations":
            current = _row(connection, table, change["id"]) or {}
            event = _row(connection, "events", current.get("event_id")) if current else None
            if not event or event.get("source_item_id") not in source_ids:
                _add_conflict(
                    conflicts,
                    group=change["group"],
                    table=table,
                    object_id=change["id"],
                    reason="participation_source_outside_provenance",
                )
        if table == "households":
            household_source_ids = {
                row["source_item_id"]
                for row in connection.execute(
                    """SELECT DISTINCT COALESCE(sa.source_item_id,po.source_item_id)
                                  AS source_item_id
                           FROM household_memberships hm
                           LEFT JOIN source_assertions sa
                             ON sa.id=hm.source_assertion_id
                           LEFT JOIN person_occurrences po
                             ON po.id=hm.occurrence_id
                          WHERE hm.household_id=?""",
                    (change["id"],),
                )
                if row["source_item_id"]
            }
            if not household_source_ids.intersection(source_ids):
                _add_conflict(
                    conflicts,
                    group=change["group"],
                    table=table,
                    object_id=change["id"],
                    reason="household_source_outside_provenance",
                    householdSourceIds=sorted(household_source_ids),
                )

    planned_alias_ids = {item["id"] for item in alias_effective}
    normalized_values = sorted({item["normalized_name"] for item in alias_effective})
    database_aliases: list[dict[str, Any]] = []
    if normalized_values:
        placeholders = ",".join("?" for _ in normalized_values)
        database_aliases = [
            dict(row)
            for row in connection.execute(
                f"""SELECT id,place_id,normalized_name FROM place_names
                    WHERE normalized_name IN ({placeholders}) ORDER BY id""",
                tuple(normalized_values),
            )
            if row["id"] not in planned_alias_ids
        ]
    by_alias: dict[str, list[dict[str, Any]]] = {}
    for alias in [*database_aliases, *alias_effective]:
        by_alias.setdefault(alias["normalized_name"], []).append(alias)
    for normalized_name, aliases in sorted(by_alias.items()):
        if len(aliases) < 2:
            continue
        place_ids = {alias["place_id"] for alias in aliases}
        reason = "duplicate_alias" if len(place_ids) == 1 else "alias_target_conflict"
        for alias in aliases:
            _add_conflict(
                conflicts,
                group="placeNames",
                table="place_names",
                object_id=alias["id"],
                reason=reason,
                normalizedName=normalized_name,
                placeIds=sorted(str(value) for value in place_ids),
            )

    summary = {
        action: sum(change["action"] == action for change in changes)
        for action in ("create", "update", "no_op")
    }
    summary["conflict"] = len(conflicts)
    return {"summary": summary, "changes": changes, "conflicts": conflicts}


def _state(connection, payload: dict[str, Any], header: dict[str, Any]) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    operations = payload.get("operations", {})
    ids_by_table: dict[str, list[str]] = {table: [] for table in (*[value[0] for value in UPSERT_SPECS.values()], *[value[0] for value in PATCH_SPECS.values()])}
    for group, (table, _, _) in UPSERT_SPECS.items():
        ids_by_table[table].extend(
            _required_text(entry.get("id"), f"operations.{group}.id")
            for entry in _entries(operations.get(group), f"operations.{group}")
        )
    for group, (table, _, _, _) in PATCH_SPECS.items():
        ids_by_table[table].extend(
            _required_text(entry.get("id"), f"operations.{group}.id")
            for entry in _entries(operations.get(group), f"operations.{group}")
        )
    return {
        "schema": int(connection.execute("PRAGMA user_version").fetchone()[0]),
        "targets": {
            table: _rows(connection, table, ids)
            for table, ids in sorted(ids_by_table.items())
        },
        "places": [dict(row) for row in connection.execute("SELECT * FROM places ORDER BY id")],
        "placeNames": [
            dict(row) for row in connection.execute("SELECT * FROM place_names ORDER BY id")
        ],
        "placeJurisdictions": [
            dict(row)
            for row in connection.execute("SELECT * FROM place_jurisdictions ORDER BY id")
        ],
        "sources": _rows(connection, "source_items", header["provenance"]["sourceItemIds"]),
        "citations": _rows(connection, "citations", header["provenance"]["citationIds"]),
    }


def _preview(connection, payload: dict[str, Any]) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    header = _validate_header(connection, payload)
    state_fingerprint = fingerprint(_state(connection, payload, header))
    accepted_fingerprint = fingerprint(
        {"manifest": header["manifestFingerprint"], "state": state_fingerprint}
    )
    diff = _build_diff(connection, payload, header)
    return {
        "mode": "preview",
        "status": "conflict" if diff["conflicts"] else "ready",
        **header,
        "inputFingerprint": state_fingerprint,
        "fingerprint": accepted_fingerprint,
        "commandIdRequired": True,
        "identityPolicy": "no_identity_decisions",
        "literalTextPolicy": "preserve_place_text_and_residence_text",
        "diff": diff,
    }


def place_writeback_preview(root: Path, manifest_path: Path) -> dict[str, Any]:
    payload = _load_manifest(manifest_path)
    initialize(root)
    with connect(root) as connection:
        require_tables(connection, REQUIRED_TABLES)
        connection.execute("PRAGMA query_only = ON")
        return _preview(connection, payload)


def _audit(
    connection,  # type: ignore[no-untyped-def]
    *,
    actor: str,
    action: str,
    object_type: str,
    object_id: str,
    before: object,
    after: object,
    rationale: str,
) -> None:
    connection.execute(
        """INSERT INTO audit_log(
               occurred_at,actor,action,object_type,object_id,
               before_json,after_json,rationale
           ) VALUES (?,?,?,?,?,?,?,?)""",
        (
            utc_now(),
            actor,
            action,
            object_type,
            object_id,
            canonical_json(before) if before is not None else None,
            canonical_json(after) if after is not None else None,
            rationale,
        ),
    )


def _apply_change(connection, change: dict[str, Any]) -> None:  # type: ignore[no-untyped-def]
    target = change["target"]
    if change["action"] == "create":
        columns = ("id", *target.keys())
        connection.execute(
            f"INSERT INTO {change['table']}({','.join(columns)}) "
            f"VALUES ({','.join('?' for _ in columns)})",
            (change["id"], *(target[column] for column in target)),
        )
    elif change["action"] == "update":
        columns = tuple(target)
        connection.execute(
            f"UPDATE {change['table']} SET "
            + ",".join(f"{column}=?" for column in columns)
            + " WHERE id=?",
            (*(target[column] for column in columns), change["id"]),
        )


def apply_place_writeback(
    root: Path,
    manifest_path: Path,
    *,
    expected_fingerprint: str,
    command_id: str,
) -> dict[str, Any]:
    payload = _load_manifest(manifest_path)
    expected_fingerprint = _required_text(expected_fingerprint, "expectedFingerprint")
    command_id = _required_text(command_id, "commandId")
    initialize(root)
    with connect(root) as connection:
        require_tables(connection, REQUIRED_TABLES)
        connection.execute("BEGIN IMMEDIATE")
        try:
            current = _preview(connection, payload)
            header = {key: current[key] for key in (
                "writebackId",
                "actor",
                "rationale",
                "writebackStatus",
                "provenance",
                "databaseSchema",
                "manifestFingerprint",
            )}
            request_fingerprint = fingerprint(
                {
                    "writebackId": header["writebackId"],
                    "manifestFingerprint": header["manifestFingerprint"],
                    "expectedFingerprint": expected_fingerprint,
                    "actor": header["actor"],
                    "rationale": header["rationale"],
                }
            )
            try:
                replay = replay_command(
                    connection,
                    command_id=command_id,
                    command_type="place_writeback",
                    object_type="place_writeback",
                    object_id=header["writebackId"],
                    request_fingerprint=request_fingerprint,
                )
            except CommandEnvelopeConflict as error:
                raise PlaceWritebackConflict(str(error)) from error
            if replay is not None:
                return replay
            prior = connection.execute(
                """SELECT command_id FROM curation_commands
                   WHERE command_type='place_writeback'
                     AND object_type='place_writeback' AND object_id=?
                   ORDER BY committed_at LIMIT 1""",
                (header["writebackId"],),
            ).fetchone()
            if prior:
                raise PlaceWritebackConflict(
                    "Ten writebackId został już zastosowany przez inną komendę"
                )
            if current["fingerprint"] != expected_fingerprint:
                raise PlaceWritebackConflict(
                    "Manifest lub zależne dane zmieniły się po preview; wygeneruj nowy preview"
                )
            if header["writebackStatus"] != "approved":
                raise PlaceWritebackConflict("Apply wymaga status='approved' w manifeście")
            if current["diff"]["conflicts"]:
                raise PlaceWritebackConflict("Place-writeback ma konflikty; apply został przerwany")

            applied = 0
            for change in current["diff"]["changes"]:
                if change["action"] == "no_op":
                    continue
                before = _row(connection, change["table"], change["id"])
                _apply_change(connection, change)
                after = _row(connection, change["table"], change["id"])
                _audit(
                    connection,
                    actor=header["actor"],
                    action=f"place_writeback_{change['action']}",
                    object_type=change["table"],
                    object_id=change["id"],
                    before=before,
                    after=after,
                    rationale=header["rationale"],
                )
                applied += 1

            result = {
                "mode": "apply",
                "status": "applied" if applied else "no_op",
                "writebackId": header["writebackId"],
                "manifestFingerprint": header["manifestFingerprint"],
                "inputFingerprint": current["inputFingerprint"],
                "fingerprint": current["fingerprint"],
                "alreadyApplied": False,
                "changesApplied": applied,
                "diff": current["diff"],
                "identityPolicy": "no_identity_decisions",
                "literalTextPolicy": "preserve_place_text_and_residence_text",
            }
            record_command(
                connection,
                command_id=command_id,
                command_type="place_writeback",
                object_type="place_writeback",
                object_id=header["writebackId"],
                input_fingerprint=current["inputFingerprint"],
                request_fingerprint=request_fingerprint,
                actor=header["actor"],
                rationale=header["rationale"],
                result=result,
                projection_status="pending",
            )
            _audit(
                connection,
                actor=header["actor"],
                action="place_writeback_apply",
                object_type="place_writeback",
                object_id=header["writebackId"],
                before=None,
                after={
                    "status": header["writebackStatus"],
                    "provenance": header["provenance"],
                    "summary": current["diff"]["summary"],
                    "commandId": command_id,
                },
                rationale=header["rationale"],
            )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise


__all__ = [
    "PlaceWritebackConflict",
    "apply_place_writeback",
    "place_writeback_preview",
]
