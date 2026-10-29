from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from .db import connect, initialize
from .research import require_tables
from .workspace import search_fold, stable_id, utc_now


VERIFICATION_IMPORTED = "imported_unverified"
VALID_COVERAGE_STATUSES = {"complete", "partial", "missing", "unsearched"}


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def _named_associates(value: str | None, base_role: str) -> list[tuple[str, str]]:
    """Parse curated semicolon-separated names without resolving identity."""

    result: list[tuple[str, str]] = []
    for raw_part in (value or "").split(";"):
        part = raw_part.strip()
        if not part or part.casefold().startswith("z domu "):
            continue
        match = re.fullmatch(r"(.+?)\s*\(([^()]+)\)", part)
        if match:
            name = match.group(1).strip()
            role = f"{base_role}: {match.group(2).strip()}"
        else:
            name = part
            role = base_role
        if name:
            result.append((name, role))
    return result


def ensure_place(
    root: Path,
    preferred_name: str,
    *,
    place_type: str | None = None,
    notes: str | None = None,
) -> dict:
    """Return an idempotent canonical place container, not an identity claim."""

    preferred_name = preferred_name.strip()
    if not preferred_name:
        raise ValueError("preferred_name nie może być puste")
    place_id = stable_id("place", _normalize(preferred_name))
    initialize(root)
    with connect(root) as connection:
        require_tables(connection, {"places", "place_names"})
        connection.execute(
            """
            INSERT INTO places(id, preferred_name, place_type, notes)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                place_type = COALESCE(excluded.place_type, places.place_type),
                notes = COALESCE(excluded.notes, places.notes)
            """,
            (place_id, preferred_name, place_type, notes),
        )
        connection.execute(
            """
            INSERT INTO place_names(id, place_id, name_text, normalized_name)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (
                stable_id("place_name", place_id, _normalize(preferred_name)),
                place_id,
                preferred_name,
                _normalize(preferred_name),
            ),
        )
        row = connection.execute("SELECT * FROM places WHERE id = ?", (place_id,)).fetchone()
    return dict(row)


def add_place_name(
    root: Path,
    place_id: str,
    name_text: str,
    *,
    language: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    source_item_id: str | None = None,
) -> dict:
    name_text = name_text.strip()
    if not name_text:
        raise ValueError("name_text nie może być puste")
    item = {
        "id": stable_id(
            "place_name", place_id, _normalize(name_text), language or "", date_from or "", date_to or ""
        ),
        "place_id": place_id,
        "name_text": name_text,
        "normalized_name": _normalize(name_text),
        "language": language,
        "date_from": date_from,
        "date_to": date_to,
        "source_item_id": source_item_id,
    }
    initialize(root)
    with connect(root) as connection:
        require_tables(connection, {"places", "place_names"})
        connection.execute(
            """
            INSERT INTO place_names(
                id, place_id, name_text, normalized_name, language,
                date_from, date_to, source_item_id
            ) VALUES (
                :id, :place_id, :name_text, :normalized_name, :language,
                :date_from, :date_to, :source_item_id
            )
            ON CONFLICT(id) DO NOTHING
            """,
            item,
        )
    return item


def add_place_jurisdiction(
    root: Path,
    place_id: str,
    jurisdiction_name: str,
    *,
    jurisdiction_type: str | None = None,
    jurisdiction_place_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    source_item_id: str | None = None,
) -> dict:
    jurisdiction_name = jurisdiction_name.strip()
    if not jurisdiction_name:
        raise ValueError("jurisdiction_name nie może być puste")
    item = {
        "id": stable_id(
            "jurisdiction",
            place_id,
            jurisdiction_name.casefold(),
            jurisdiction_type or "",
            date_from or "",
            date_to or "",
        ),
        "place_id": place_id,
        "jurisdiction_place_id": jurisdiction_place_id,
        "jurisdiction_name": jurisdiction_name,
        "jurisdiction_type": jurisdiction_type,
        "date_from": date_from,
        "date_to": date_to,
        "source_item_id": source_item_id,
    }
    initialize(root)
    with connect(root) as connection:
        require_tables(connection, {"places", "place_jurisdictions"})
        connection.execute(
            """
            INSERT INTO place_jurisdictions(
                id, place_id, jurisdiction_place_id, jurisdiction_name,
                jurisdiction_type, date_from, date_to, source_item_id
            ) VALUES (
                :id, :place_id, :jurisdiction_place_id, :jurisdiction_name,
                :jurisdiction_type, :date_from, :date_to, :source_item_id
            ) ON CONFLICT(id) DO NOTHING
            """,
            item,
        )
    return item


def create_place_study(
    root: Path,
    place_id: str,
    title: str,
    *,
    case_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    scope: str,
    status: str = "planned",
    study_id: str | None = None,
) -> dict:
    title = title.strip()
    scope = scope.strip()
    if not title or not scope:
        raise ValueError("title i scope nie mogą być puste")
    now = utc_now()
    item = {
        "id": study_id or stable_id("study", place_id, date_from or "", date_to or "", scope.casefold()),
        "case_id": case_id,
        "place_id": place_id,
        "title": title,
        "date_from": date_from,
        "date_to": date_to,
        "scope": scope,
        "status": status,
        "created_at": now,
        "updated_at": now,
    }
    initialize(root)
    with connect(root) as connection:
        require_tables(connection, {"places", "place_studies"})
        connection.execute(
            """
            INSERT INTO place_studies(
                id, case_id, place_id, title, date_from, date_to,
                scope, status, created_at, updated_at
            ) VALUES (
                :id, :case_id, :place_id, :title, :date_from, :date_to,
                :scope, :status, :created_at, :updated_at
            ) ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                scope = excluded.scope,
                status = excluded.status,
                updated_at = excluded.updated_at
            """,
            item,
        )
    return item


def record_place_study_coverage(
    root: Path,
    study_id: str,
    record_type: str,
    coverage_status: str,
    *,
    collection_id: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    completeness: float | None = None,
    notes: str | None = None,
) -> dict:
    if coverage_status not in VALID_COVERAGE_STATUSES:
        raise ValueError(
            "coverage_status musi być jednym z: "
            + ", ".join(sorted(VALID_COVERAGE_STATUSES))
        )
    if completeness is not None and not 0 <= completeness <= 1:
        raise ValueError("completeness musi mieścić się w zakresie 0–1")
    item = {
        "id": stable_id(
            "study_coverage",
            study_id,
            collection_id or "",
            record_type.casefold(),
            str(year_from or ""),
            str(year_to or ""),
        ),
        "study_id": study_id,
        "collection_id": collection_id,
        "year_from": year_from,
        "year_to": year_to,
        "record_type": record_type.strip(),
        "coverage_status": coverage_status,
        "completeness": completeness,
        "notes": notes,
    }
    if not item["record_type"]:
        raise ValueError("record_type nie może być puste")
    initialize(root)
    with connect(root) as connection:
        require_tables(connection, {"place_studies", "place_study_coverage"})
        connection.execute(
            """
            INSERT INTO place_study_coverage(
                id, study_id, collection_id, year_from, year_to, record_type,
                coverage_status, completeness, notes
            ) VALUES (
                :id, :study_id, :collection_id, :year_from, :year_to, :record_type,
                :coverage_status, :completeness, :notes
            ) ON CONFLICT(id) DO UPDATE SET
                coverage_status = excluded.coverage_status,
                completeness = excluded.completeness,
                notes = excluded.notes
            """,
            item,
        )
    return item


def place_study_coverage_matrix(root: Path, study_id: str) -> dict:
    """Expose missing/unsearched years instead of interpreting every absence as evidence."""

    initialize(root)
    with connect(root) as connection:
        require_tables(connection, {"place_studies", "place_study_coverage"})
        study = connection.execute(
            "SELECT * FROM place_studies WHERE id = ?", (study_id,)
        ).fetchone()
        if study is None:
            raise LookupError(f"Nie znaleziono studium: {study_id}")
        rows = connection.execute(
            """
            SELECT * FROM place_study_coverage
            WHERE study_id = ? ORDER BY record_type, year_from, year_to
            """,
            (study_id,),
        ).fetchall()
    entries = [dict(row) for row in rows]
    return {
        "study": dict(study),
        "entries": entries,
        "has_unsearched_ranges": any(
            row["coverage_status"] == "unsearched" for row in entries
        ),
        "has_missing_material": any(
            row["coverage_status"] == "missing" for row in entries
        ),
        "negative_result_warning": (
            "Brak wystąpienia nie jest dowodem negatywnym, jeśli zakres jest niepełny, "
            "brakujący albo niesprawdzony."
        ),
    }


def _source_item_id(connection, legacy_source_id: str) -> str:
    row = connection.execute(
        "SELECT id FROM source_items WHERE legacy_source_id = ? OR id = ?",
        (legacy_source_id, legacy_source_id),
    ).fetchone()
    if row is None:
        raise LookupError(
            f"Brak source_item dla legacy source {legacy_source_id}; uruchom migrację v2"
        )
    return row[0]


def import_household_occurrences(
    root: Path,
    csv_path: Path | None = None,
    *,
    case_id: str | None = None,
) -> dict:
    """Idempotently import the curated CSV without promoting or merging identities."""

    path = csv_path or root / "research" / "records" / "household-occurrences.csv"
    if not path.is_file():
        raise FileNotFoundError(path)
    initialize(root)
    required = {
        "source_items",
        "places",
        "place_names",
        "place_studies",
        "person_occurrences",
        "events",
        "participations",
        "households",
        "addresses",
        "household_memberships",
        "citations",
        "local_occurrences",
        "curation_queue",
    }
    counts = {"rows": 0, "inserted": 0, "updated": 0, "studies": set()}
    with path.open(newline="", encoding="utf-8") as handle:
        records = list(csv.DictReader(handle))
    link_path = root / "research" / "records" / "local-occurrence-links.json"
    curated_links: dict[str, dict] = {}
    if link_path.is_file():
        link_payload = json.loads(link_path.read_text(encoding="utf-8"))
        for link in link_payload.get("links", []):
            curated_links[link["csv_occurrence_id"]] = link
    with connect(root) as connection:
        require_tables(connection, required)
        for row in records:
            counts["rows"] += 1
            occurrence_id = row["occurrence_id"].strip()
            place_name = (row.get("place") or "Nieustalone").strip()
            place_id = stable_id("place", _normalize(place_name))
            connection.execute(
                """
                INSERT INTO places(id, preferred_name, place_type)
                VALUES (?, ?, 'locality') ON CONFLICT(id) DO NOTHING
                """,
                (place_id, place_name),
            )
            connection.execute(
                """
                INSERT INTO place_names(id, place_id, name_text, normalized_name)
                VALUES (?, ?, ?, ?) ON CONFLICT(id) DO NOTHING
                """,
                (
                    stable_id("place_name", place_id, _normalize(place_name)),
                    place_id,
                    place_name,
                    _normalize(place_name),
                ),
            )
            study_id = stable_id("study", "csv-import", place_id)
            counts["studies"].add(study_id)
            now = utc_now()
            connection.execute(
                """
                INSERT INTO place_studies(
                    id, case_id, place_id, title, scope, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
                ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at
                """,
                (
                    study_id,
                    case_id,
                    place_id,
                    f"Indeks lokalny: {place_name}",
                    "Import kontrolowanego rejestru household-occurrences.csv",
                    now,
                    now,
                ),
            )
            source_item_id = _source_item_id(connection, row["source_id"].strip())
            household_id = _clean(row.get("household_id"))
            if household_id:
                connection.execute(
                    """
                    INSERT INTO households(
                        id, place_id, label, verification_status, notes
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        notes = COALESCE(excluded.notes, households.notes)
                    """,
                    (
                        household_id,
                        place_id,
                        household_id,
                        VERIFICATION_IMPORTED,
                        _clean(row.get("notes")),
                    ),
                )
            house_number = _clean(row.get("house_number"))
            if house_number:
                connection.execute(
                    """
                    INSERT INTO addresses(
                        id, household_id, place_id, address_text, house_number,
                        date_from, date_to, source_item_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO NOTHING
                    """,
                    (
                        stable_id("address", household_id or "", place_id, house_number, row.get("event_date") or ""),
                        household_id,
                        place_id,
                        f"{place_name}, dom {house_number}",
                        house_number,
                        _clean(row.get("event_date")),
                        _clean(row.get("event_date")),
                        source_item_id,
                    ),
                )
            evidence = dict(row)
            raw_name = " ".join(
                part for part in (_clean(row.get("given_names")), _clean(row.get("surname_variant"))) if part
            )
            folded_name = search_fold(raw_name)
            curated_link = curated_links.get(occurrence_id)
            source_occurrence = None
            if curated_link:
                source_occurrence = connection.execute(
                    """SELECT id FROM person_occurrences
                       WHERE id=? AND source_item_id=?""",
                    (curated_link["source_occurrence_id"], source_item_id),
                ).fetchone()
                if source_occurrence is None:
                    raise ValueError(
                        f"Nieprawidłowy curated link dla {occurrence_id}: "
                        "wystąpienie nie należy do tego samego źródła"
                    )
            resolved_occurrence_id = source_occurrence[0] if source_occurrence else occurrence_id
            if source_occurrence:
                connection.execute(
                    """UPDATE person_occurrences
                       SET verification_status='superseded_projection'
                       WHERE id=? AND person_id IS NULL""",
                    (occurrence_id,),
                )
                connection.execute(
                    """INSERT INTO curation_queue(
                           id,object_type,object_id,reason_code,payload_json,
                           status,created_at
                       ) VALUES (?, 'local_occurrence', ?,
                                 'curated_source_occurrence_link', ?, 'open', ?)
                       ON CONFLICT(object_type,object_id,reason_code) DO UPDATE SET
                         payload_json=excluded.payload_json""",
                    (
                        stable_id("curation", "local-occurrence-link", occurrence_id),
                        occurrence_id,
                        json.dumps(curated_link, ensure_ascii=False),
                        now,
                    ),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO person_occurrences(
                        id, source_item_id, raw_name, normalized_name, search_name, role_text,
                        reading_confidence, verification_status, evidence_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        search_name = excluded.search_name,
                        evidence_json = excluded.evidence_json
                    """,
                    (
                        occurrence_id,
                        source_item_id,
                        raw_name,
                        _normalize(raw_name),
                        folded_name,
                        _clean(row.get("role_or_status")),
                        None,
                        VERIFICATION_IMPORTED,
                        json.dumps(evidence, ensure_ascii=False),
                    ),
                )
            event_type = _clean(row.get("event_type")) or "unspecified"
            event_date = _clean(row.get("event_date"))
            # A CSV row describes a participant. The event is stable at the
            # source/date/type level so spouses or household members from the
            # same document do not create duplicate events.
            source_event = connection.execute(
                """SELECT DISTINCT e.id FROM events e
                   JOIN event_readings er ON er.event_id=e.id
                   WHERE e.source_item_id=? AND e.date_from=?
                   ORDER BY e.id LIMIT 2""",
                (source_item_id, event_date),
            ).fetchall()
            uses_source_event = len(source_event) == 1
            event_id = (
                source_event[0][0]
                if uses_source_event
                else stable_id(
                    "event", "local-csv", source_item_id, event_date or "", event_type
                )
            )
            if not uses_source_event:
                connection.execute(
                    """
                    INSERT INTO events(
                        id, source_item_id, event_type, date_text, date_from, date_to,
                        place_id, place_text, verification_status, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET notes = excluded.notes
                    """,
                    (
                        event_id,
                        source_item_id,
                        event_type,
                        event_date,
                        event_date,
                        event_date,
                        place_id,
                        place_name,
                        VERIFICATION_IMPORTED,
                        _clean(row.get("notes")),
                    ),
                )
            role = _clean(row.get("role_or_status")) or "unspecified"
            local_participation_id = stable_id("participation", "local-csv", occurrence_id)
            existing_participation = connection.execute(
                """SELECT id FROM participations
                   WHERE event_id=? AND occurrence_id=? AND id<>? LIMIT 1""",
                (event_id, resolved_occurrence_id, local_participation_id),
            ).fetchone()
            connection.execute(
                "DELETE FROM participations WHERE id=?", (local_participation_id,)
            )
            if existing_participation is None:
                connection.execute(
                    """
                    INSERT INTO participations(id, event_id, occurrence_id, role, occupation_text, status_text)
                    VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(event_id, occurrence_id, role)
                    DO UPDATE SET occupation_text = COALESCE(excluded.occupation_text, participations.occupation_text),
                                  status_text = excluded.status_text
                    """,
                    (
                        local_participation_id,
                        event_id,
                        resolved_occurrence_id,
                        role,
                        _clean(row.get("occupation")),
                        role,
                    ),
                )
            old_event_id = stable_id("event", "local-csv", occurrence_id)
            obsolete_event_ids = {old_event_id}
            if uses_source_event:
                obsolete_event_ids.add(
                    stable_id(
                        "event", "local-csv", source_item_id, event_date or "", event_type
                    )
                )
            for obsolete_event_id in obsolete_event_ids - {event_id}:
                connection.execute(
                    "DELETE FROM events WHERE id = ? AND NOT EXISTS "
                    "(SELECT 1 FROM participations WHERE event_id = ?)",
                    (obsolete_event_id, obsolete_event_id),
                )
            associates = [] if uses_source_event else [
                *_named_associates(row.get("related_persons"), "related-person"),
                *_named_associates(row.get("witnesses_or_neighbors"), "witness-or-neighbor"),
            ]
            for associate_name, associate_role in associates:
                associate_id = stable_id(
                    "occ", "local-associate", source_item_id, search_fold(associate_name)
                )
                connection.execute(
                    """INSERT INTO person_occurrences(
                           id, source_item_id, raw_name, normalized_name, search_name,
                           role_text, verification_status, evidence_json
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET
                           role_text = excluded.role_text,
                           evidence_json = excluded.evidence_json""",
                    (
                        associate_id,
                        source_item_id,
                        associate_name,
                        _normalize(associate_name),
                        search_fold(associate_name),
                        associate_role,
                        VERIFICATION_IMPORTED,
                        json.dumps(
                            {
                                "import": "household-occurrences.csv",
                                "primary_occurrence_id": occurrence_id,
                                "field_role": associate_role,
                                "raw_row": row,
                            },
                            ensure_ascii=False,
                        ),
                    ),
                )
                connection.execute(
                    """INSERT INTO participations(
                           id, event_id, occurrence_id, role, status_text
                       ) VALUES (?, ?, ?, ?, 'imported_unverified')
                       ON CONFLICT(id) DO UPDATE SET
                           event_id = excluded.event_id,
                           role = excluded.role,
                           status_text = excluded.status_text""",
                    (
                        stable_id(
                            "participation",
                            "local-associate",
                            event_id,
                            associate_id,
                            associate_role,
                        ),
                        event_id,
                        associate_id,
                        associate_role,
                    ),
                )
            if household_id:
                connection.execute(
                    """
                    INSERT INTO household_memberships(
                        id, household_id, occurrence_id, role, date_from, date_to
                    ) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET
                        occurrence_id = excluded.occurrence_id,
                        role = excluded.role
                    """,
                    (
                        stable_id("membership", household_id, occurrence_id),
                        household_id,
                        resolved_occurrence_id,
                        role,
                        _clean(row.get("event_date")),
                        _clean(row.get("event_date")),
                    ),
                )
            citation_text = _clean(row.get("source_url")) or f"Legacy source {row['source_id']}"
            citation_id = stable_id("citation", "local-csv", occurrence_id)
            connection.execute(
                """
                INSERT INTO citations(id, source_item_id, citation_text, locator, created_at)
                VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET citation_text = excluded.citation_text
                """,
                (citation_id, source_item_id, citation_text, occurrence_id, now),
            )
            local_id = stable_id("local_occurrence", occurrence_id)
            existed = connection.execute(
                "SELECT 1 FROM local_occurrences WHERE id = ?", (local_id,)
            ).fetchone()
            connection.execute(
                """
                INSERT INTO local_occurrences(
                    id, study_id, occurrence_id, household_id, source_item_id,
                    event_date_text, event_type, role, surname_variant, given_names,
                    search_name, house_number, citation_id, verification_status, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    study_id = excluded.study_id,
                    occurrence_id = excluded.occurrence_id,
                    household_id = excluded.household_id,
                    source_item_id = excluded.source_item_id,
                    event_date_text = excluded.event_date_text,
                    event_type = excluded.event_type,
                    role = excluded.role,
                    surname_variant = excluded.surname_variant,
                    given_names = excluded.given_names,
                    house_number = excluded.house_number,
                    citation_id = excluded.citation_id,
                    search_name = excluded.search_name,
                    metadata_json = excluded.metadata_json
                """,
                (
                    local_id,
                    study_id,
                    resolved_occurrence_id,
                    household_id,
                    source_item_id,
                    _clean(row.get("event_date")),
                    _clean(row.get("event_type")),
                    role,
                    _clean(row.get("surname_variant")),
                    _clean(row.get("given_names")),
                    search_fold(raw_name),
                    house_number,
                    citation_id,
                    VERIFICATION_IMPORTED,
                    json.dumps(
                        {
                            **evidence,
                            "csv_occurrence_id": occurrence_id,
                            "resolved_occurrence_id": resolved_occurrence_id,
                            "resolved_event_id": event_id,
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            counts["updated" if existed else "inserted"] += 1
    return {
        "path": str(path),
        "rows": counts["rows"],
        "inserted": counts["inserted"],
        "updated": counts["updated"],
        "study_ids": sorted(counts["studies"]),
        "verification_status": VERIFICATION_IMPORTED,
    }


def search_local_occurrences(
    root: Path,
    query: str,
    *,
    study_id: str | None = None,
    household_id: str | None = None,
) -> list[dict]:
    """Find local hits while retaining citation, source, and occurrence identity."""

    initialize(root)
    like = f"%{search_fold(query)}%"
    clauses = ["(lo.search_name LIKE ? OR po.search_name LIKE ?)"]
    parameters: list[object] = [like, like]
    if study_id:
        clauses.append("lo.study_id = ?")
        parameters.append(study_id)
    if household_id:
        clauses.append("lo.household_id = ?")
        parameters.append(household_id)
    with connect(root) as connection:
        require_tables(
            connection,
            {"local_occurrences", "person_occurrences", "source_items", "citations"},
        )
        rows = connection.execute(
            f"""
            SELECT lo.*, po.person_id, po.raw_name, po.normalized_name,
                   si.legacy_source_id, si.title AS source_title, si.source_url,
                   c.citation_text, c.locator
            FROM local_occurrences lo
            JOIN person_occurrences po ON po.id = lo.occurrence_id
            JOIN source_items si ON si.id = lo.source_item_id
            LEFT JOIN citations c ON c.id = lo.citation_id
            WHERE {' AND '.join(clauses)}
            ORDER BY lo.event_date_text, lo.surname_variant, lo.given_names, lo.id
            """,
            parameters,
        ).fetchall()
    return [dict(row) for row in rows]


def household_timeline(root: Path, household_id: str) -> dict:
    initialize(root)
    with connect(root) as connection:
        require_tables(
            connection,
            {
                "households",
                "household_memberships",
                "local_occurrences",
                "addresses",
                "persons",
                "person_occurrences",
                "participations",
                "events",
            },
        )
        household = connection.execute(
            "SELECT * FROM households WHERE id = ?", (household_id,)
        ).fetchone()
        if household is None:
            raise LookupError(f"Nie znaleziono gospodarstwa: {household_id}")
        addresses = connection.execute(
            "SELECT * FROM addresses WHERE household_id = ? ORDER BY date_from, id",
            (household_id,),
        ).fetchall()
        occurrences = connection.execute(
            """
            SELECT * FROM local_occurrences WHERE household_id = ?
            ORDER BY event_date_text, id
            """,
            (household_id,),
        ).fetchall()
        members = connection.execute(
            """
            SELECT hm.id,hm.person_id,hm.occurrence_id,hm.role,hm.date_from,hm.date_to,
                   p.preferred_name,p.status AS person_status
            FROM household_memberships hm
            LEFT JOIN persons p ON p.id=hm.person_id
            WHERE hm.household_id=?
            ORDER BY hm.role,COALESCE(p.preferred_name,hm.occurrence_id),hm.id
            """,
            (household_id,),
        ).fetchall()
        source_occurrences = connection.execute(
            """
            SELECT DISTINCT po.id,po.person_id,po.raw_name,po.normalized_name,
                   po.search_name,po.role_text,po.verification_status,
                   e.id AS event_id,e.event_type,e.date_text,e.date_from,e.date_to,
                   p.role AS participation_role
            FROM household_memberships hm
            JOIN person_occurrences po
              ON po.id=hm.occurrence_id OR (hm.person_id IS NOT NULL AND po.person_id=hm.person_id)
            LEFT JOIN participations p ON p.occurrence_id=po.id
            LEFT JOIN events e ON e.id=p.event_id
            WHERE hm.household_id=?
            ORDER BY COALESCE(e.date_from,e.date_text),po.id,p.role
            """,
            (household_id,),
        ).fetchall()
    return {
        "household": dict(household),
        "addresses": [dict(row) for row in addresses],
        "occurrences": [dict(row) for row in occurrences],
        "members": [dict(row) for row in members],
        "sourceOccurrences": [dict(row) for row in source_occurrences],
    }
