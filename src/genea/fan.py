from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

from .db import connect, initialize
from .research import require_tables


INCIDENTAL_ROLES = {
    "clergy",
    "duchowny",
    "ksiądz",
    "official",
    "urzędnik",
    "registrar",
}


def _target_occurrences(
    connection: sqlite3.Connection,
    *,
    person_id: str | None,
    occurrence_id: str | None,
) -> list[str]:
    if bool(person_id) == bool(occurrence_id):
        raise ValueError("Podaj dokładnie jedno z: person_id albo occurrence_id")
    if occurrence_id:
        row = connection.execute(
            "SELECT id FROM person_occurrences WHERE id = ?", (occurrence_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"Nie znaleziono wystąpienia: {occurrence_id}")
        return [row[0]]
    rows = connection.execute(
        "SELECT id FROM person_occurrences WHERE person_id = ? ORDER BY id",
        (person_id,),
    ).fetchall()
    if not rows:
        raise LookupError(f"Osoba nie ma wystąpień źródłowych: {person_id}")
    return [row[0] for row in rows]


def _identity_candidates(
    connection: sqlite3.Connection, occurrence_ids: list[str]
) -> list[dict]:
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(identity_candidates)").fetchall()
    }
    pair = next(
        (
            names
            for names in (
                ("occurrence_a_id", "occurrence_b_id"),
                ("left_occurrence_id", "right_occurrence_id"),
                ("first_occurrence_id", "second_occurrence_id"),
            )
            if set(names) <= columns
        ),
        None,
    )
    if pair is None or not occurrence_ids:
        return []
    placeholders = ",".join("?" for _ in occurrence_ids)
    rows = connection.execute(
        f"""
        SELECT * FROM identity_candidates
        WHERE {pair[0]} IN ({placeholders}) OR {pair[1]} IN ({placeholders})
        ORDER BY id
        """,
        (*occurrence_ids, *occurrence_ids),
    ).fetchall()
    return [dict(row) for row in rows]


def fan_report(
    root: Path,
    *,
    person_id: str | None = None,
    occurrence_id: str | None = None,
    include_incidental: bool = True,
) -> dict:
    """Build source-backed FAN edges without silently resolving identities."""

    initialize(root)
    with connect(root) as connection:
        require_tables(
            connection,
            {
                "persons",
                "person_occurrences",
                "identity_candidates",
                "events",
                "participations",
                "places",
            },
        )
        targets = _target_occurrences(
            connection, person_id=person_id, occurrence_id=occurrence_id
        )
        placeholders = ",".join("?" for _ in targets)
        event_rows = connection.execute(
            f"""
            SELECT DISTINCT event_id FROM participations
            WHERE occurrence_id IN ({placeholders})
            """,
            targets,
        ).fetchall()
        event_ids = [row[0] for row in event_rows]
        if not event_ids:
            return {
                "target": {"person_id": person_id, "occurrence_ids": targets},
                "edges": [],
                "repeated_associates": [],
                "identity_candidates": _identity_candidates(connection, targets),
            }
        event_placeholders = ",".join("?" for _ in event_ids)
        rows = connection.execute(
            f"""
            SELECT
                p.event_id,
                p.occurrence_id,
                p.role,
                p.age_text,
                p.occupation_text,
                p.residence_text,
                p.status_text,
                p.is_informant,
                e.event_type,
                e.date_text,
                e.date_from,
                e.date_to,
                e.source_item_id,
                e.place_id,
                pl.preferred_name AS place_name,
                o.raw_name,
                o.normalized_name,
                o.person_id,
                per.status AS person_status,
                o.verification_status AS occurrence_status,
                o.reading_confidence,
                o.source_item_id AS occurrence_source_item_id,
                o.reading_version_id,
                o.transcript_segment_id
            FROM participations p
            JOIN events e ON e.id = p.event_id
            JOIN person_occurrences o ON o.id = p.occurrence_id
            LEFT JOIN persons per ON per.id = o.person_id
            LEFT JOIN places pl ON pl.id = e.place_id
            WHERE p.event_id IN ({event_placeholders})
            ORDER BY COALESCE(e.date_from, e.date_text), p.event_id, p.id
            """,
            event_ids,
        ).fetchall()
        candidates = _identity_candidates(connection, targets)

    edges: list[dict] = []
    target_set = set(targets)
    for row in rows:
        item = dict(row)
        if item["occurrence_id"] in target_set:
            continue
        item["source_item_id"] = (
            item["source_item_id"] or item.pop("occurrence_source_item_id")
        )
        item["identity_level"] = (
            "canonical-person"
            if item["person_id"] and item["person_status"] in {"confirmed", "canonical", "resolved"}
            else "source-occurrence"
        )
        item["incidental"] = (item["role"] or "").casefold() in INCIDENTAL_ROLES
        if include_incidental or not item["incidental"]:
            edges.append(item)

    def identity_key(edge: dict) -> str:
        if edge["identity_level"] == "canonical-person":
            return f"person:{edge['person_id']}"
        return f"occurrence:{edge['occurrence_id']}"

    grouped: dict[str, list[dict]] = {}
    for edge in edges:
        if not edge["incidental"]:
            grouped.setdefault(identity_key(edge), []).append(edge)
    repeated: list[dict] = []
    for key, matching in grouped.items():
        event_count = len({edge["event_id"] for edge in matching})
        if event_count < 2:
            continue
        repeated.append(
            {
                "identity_key": key,
                "identity_level": matching[0]["identity_level"],
                "display_name": matching[0]["normalized_name"]
                or matching[0]["raw_name"],
                "event_count": event_count,
                "roles": sorted({edge["role"] for edge in matching if edge["role"]}),
                "source_item_ids": sorted(
                    {
                        edge["source_item_id"]
                        for edge in matching
                        if edge["source_item_id"]
                    }
                ),
            }
        )
    repeated.sort(key=lambda item: (-item["event_count"], item["display_name"], item["identity_key"]))
    return {
        "target": {"person_id": person_id, "occurrence_ids": targets},
        "edges": edges,
        "repeated_associates": repeated,
        "identity_candidates": candidates,
    }


def life_profile(
    root: Path,
    person_id: str,
    *,
    expected_event_types: Iterable[str] | None = None,
) -> dict:
    """Return a source-backed life timeline and question-specific completeness."""

    initialize(root)
    with connect(root) as connection:
        require_tables(
            connection,
            {"persons", "person_occurrences", "events", "participations", "places"},
        )
        person = connection.execute(
            "SELECT * FROM persons WHERE id = ?", (person_id,)
        ).fetchone()
        if person is None:
            raise LookupError(f"Nie znaleziono osoby: {person_id}")
        rows = connection.execute(
            f"""
            SELECT
                e.id AS event_id,
                e.event_type,
                e.date_text,
                e.date_from,
                e.date_to,
                e.source_item_id,
                e.place_id,
                pl.preferred_name AS place_name,
                p.role,
                p.age_text,
                p.occupation_text,
                p.residence_text,
                p.status_text,
                p.is_informant,
                o.id AS occurrence_id,
                o.raw_name,
                o.normalized_name,
                o.verification_status AS occurrence_status,
                o.reading_confidence,
                o.reading_version_id,
                o.transcript_segment_id
            FROM person_occurrences o
            JOIN participations p ON p.occurrence_id = o.id
            JOIN events e ON e.id = p.event_id
            LEFT JOIN places pl ON pl.id = e.place_id
            WHERE o.person_id = ?
            ORDER BY COALESCE(e.date_from, e.date_text), e.id, p.id
            """,
            (person_id,),
        ).fetchall()
    timeline = [dict(row) for row in rows]
    observed = sorted({row["event_type"] for row in timeline})
    expected = sorted(set(expected_event_types or []))
    missing = sorted(set(expected) - set(observed))
    return {
        "person": dict(person),
        "timeline": timeline,
        "observed_event_types": observed,
        "completeness": {
            "assessed": expected_event_types is not None,
            "expected_event_types": expected,
            "missing_event_types": missing,
            "complete": not missing if expected_event_types is not None else None,
            "note": (
                "Kompletność oceniono wobec przekazanego pytania badawczego."
                if expected_event_types is not None
                else "Brak sztywnego minimum aktów; podaj typy zdarzeń wymagane przez pytanie."
            ),
        },
    }
