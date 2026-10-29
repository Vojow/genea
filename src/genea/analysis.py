from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path

from .archive import get_source
from .db import connect, initialize
from .render import visual_files
from .research_discovery import load_discovery_plan
from .workspace import dump_json, safe_filename, search_fold, stable_id, utc_now


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def build_prompt(root: Path, source_id: str, researcher_context: str | None = None) -> str:
    source = get_source(root, source_id)
    template = (root / "prompts" / "act-analysis.md").read_text(encoding="utf-8")
    context = researcher_context.strip() if researcher_context else "Brak dodatkowego kontekstu."
    return template.format(
        source_id=source_id,
        source_title=source["title"],
        source_service=source["service"],
        source_url=source["source_url"] or "brak",
        researcher_context=context,
    )


def codex_command(
    root: Path,
    source_id: str,
    *,
    model: str | None = None,
    researcher_context: str | None = None,
) -> tuple[list[str], Path]:
    executable = shutil.which("codex")
    if not executable:
        raise RuntimeError("Nie znaleziono Codex CLI w PATH")
    visuals = visual_files(root, source_id)
    if len(visuals) > 20:
        raise ValueError(
            f"Dokument ma {len(visuals)} stron. MVP analizuje maksymalnie 20 naraz; "
            "podziel PDF lub wybierz mniejszy dokument."
        )
    schema = root / "schemas" / "act-analysis.schema.json"
    if not schema.is_file():
        raise FileNotFoundError(f"Brak schematu odpowiedzi: {schema}")
    output = root / "research" / "tmp" / f"analysis-{safe_filename(source_id)}.json"
    command = [
        executable,
        "exec",
        "--sandbox",
        "read-only",
        "--ephemeral",
        "--skip-git-repo-check",
        "--output-schema",
        str(schema),
        "--output-last-message",
        str(output),
    ]
    if model:
        command.extend(["--model", model])
    # `--image` accepts one or more values. With current Codex CLI versions a
    # prompt appended after the final image can be consumed as another image
    # argument, leaving exec with no prompt. Put the positional prompt first;
    # clap still accepts the following options and keeps the boundary clear.
    command.append(build_prompt(root, source_id, researcher_context))
    for image in visuals:
        command.extend(["--image", str(image)])
    return command, output


def run_analysis(
    root: Path,
    source_id: str,
    *,
    model: str | None = None,
    researcher_context: str | None = None,
    dry_run: bool = False,
    discovery_plan: Path | None = None,
    lead_id: str | None = None,
) -> dict:
    if discovery_plan is None or not lead_id:
        raise ValueError(
            "Przed odczytem wymagany jest katalog poszlak: podaj discovery_plan "
            "(--discovery-plan) oraz lead_id (--lead-id) wybranej poszlaki ze zgodnym sourceId. "
            "Zobacz docs/RESEARCH_DISCOVERY_PROTOCOL.md i "
            "research/templates/RESEARCH_DISCOVERY.example.json."
        )
    discovery = load_discovery_plan(discovery_plan, source_id=source_id, lead_id=lead_id)
    initialize(root)
    command, output = codex_command(
        root,
        source_id,
        model=model,
        researcher_context=researcher_context,
    )
    if dry_run:
        redacted = ["<PROMPT>" if item.startswith("Jesteś paleografem") else item for item in command]
        return {"command": redacted, "output": str(output), "discovery": discovery}
    subprocess.run(command, check=True)
    try:
        payload = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Codex nie zapisał poprawnej odpowiedzi JSON w {output}") from error
    payload["source_id"] = source_id
    payload["research_discovery"] = {
        field: discovery[field]
        for field in ("schemaVersion", "inputSha256", "boundLeadId", "boundSourceId")
    }
    result = import_analysis(root, payload, model=model)
    result["discovery"] = discovery
    return result


def _entity_id(entity_type: str, name: str) -> str:
    return stable_id("ent", entity_type, _normalize(name))


def _table_info(connection, table: str) -> dict[str, dict]:
    """Return a table contract without assuming that a migration is installed."""

    if table not in {
        "artifacts",
        "artifact_derivations",
        "source_items",
        "readings",
        "reading_versions",
        "transcript_segments",
        "field_readings",
        "persons",
        "person_occurrences",
        "occurrence_readings",
        "places",
        "events",
        "event_readings",
        "participations",
        "source_assertions",
        "informants",
    }:
        return {}
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row["name"]): dict(row) for row in rows}


def _insert_if_supported(connection, table: str, values: Mapping[str, object]) -> bool:
    """Insert only when the discovered table can accept the supplied row.

    This intentionally uses INSERT OR IGNORE. Re-importing an immutable reading
    must not delete a parent row (and cascade to its versions), and an older
    database must keep working without any v2 tables.
    """

    info = _table_info(connection, table)
    if not info:
        return False
    row = {key: value for key, value in values.items() if key in info}
    missing_required = {
        name
        for name, column in info.items()
        if column["notnull"] and column["dflt_value"] is None and not column["pk"] and name not in row
    }
    if not row or missing_required:
        return False
    columns = ", ".join(row)
    placeholders = ", ".join("?" for _ in row)
    connection.execute(
        f"INSERT OR IGNORE INTO {table} ({columns}) VALUES ({placeholders})",
        tuple(row.values()),
    )
    return True


def _existing_id(connection, table: str, candidate: object) -> str | None:
    if not candidate or "id" not in _table_info(connection, table):
        return None
    row = connection.execute(f"SELECT id FROM {table} WHERE id = ?", (str(candidate),)).fetchone()
    return str(row[0]) if row else None


def _source_item_id(
    connection,
    source_id: str,
    requested_source_item_id: object = None,
) -> str | None:
    if requested_source_item_id is not None:
        requested = str(requested_source_item_id).strip()
        if not requested:
            raise ValueError("source_item_id nie może być pusty")
        row = connection.execute(
            "SELECT id,parent_id,legacy_source_id FROM source_items WHERE id=?",
            (requested,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Nie znaleziono source_item_id: {requested}")
        current = row
        visited: set[str] = set()
        while current is not None:
            current_id = str(current["id"])
            if current_id in visited:
                raise ValueError("Wykryto cykl w hierarchii source_items")
            visited.add(current_id)
            if current["legacy_source_id"] == source_id:
                return requested
            artifact = connection.execute(
                """SELECT id FROM artifacts
                   WHERE legacy_source_id=? AND source_item_id=?
                   ORDER BY id LIMIT 1""",
                (source_id, current_id),
            ).fetchone()
            if artifact is not None:
                return requested
            parent_id = current["parent_id"]
            if parent_id is None:
                break
            current = connection.execute(
                "SELECT id,parent_id,legacy_source_id FROM source_items WHERE id=?",
                (parent_id,),
            ).fetchone()
        raise ValueError(
            f"source_item_id {requested} nie należy do hierarchii źródła {source_id}"
        )
    columns = _table_info(connection, "source_items")
    for legacy_column in ("legacy_source_id", "source_id", "legacy_id"):
        if legacy_column in columns:
            row = connection.execute(
                f"SELECT id FROM source_items WHERE {legacy_column} = ? ORDER BY id LIMIT 1",
                (source_id,),
            ).fetchone()
            if row:
                return str(row[0])
    found = _existing_id(connection, "source_items", source_id)
    if found:
        return found
    if "legacy_source_id" not in columns:
        return None
    source = connection.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    if source is None:
        return None
    item_id = stable_id("item", "legacy", source_id)
    inserted = _insert_if_supported(
        connection,
        "source_items",
        {
            "id": item_id,
            "legacy_source_id": source_id,
            "title": source["title"],
            "item_type": source["kind"],
            "source_url": source["source_url"],
            "source_quality": "Indeterminable",
            "verification_status": "imported_unverified",
            "metadata_json": source["metadata_json"],
        },
    )
    if not inserted or not _existing_id(connection, "source_items", item_id):
        return None
    _insert_if_supported(
        connection,
        "artifacts",
        {
            "id": stable_id("artifact", "legacy", source_id),
            "source_item_id": item_id,
            "legacy_source_id": source_id,
            "artifact_type": "RAW",
            "path": source["archive_path"],
            "media_type": source["media_type"],
            "sha256": source["sha256"],
            "immutable": 1,
            "created_at": source["captured_at"],
            "metadata_json": source["metadata_json"],
        },
    )
    return item_id


def _artifact_id(connection, source_item_id: str | None, candidate: object = None) -> str | None:
    found = _existing_id(connection, "artifacts", candidate)
    if found and source_item_id:
        columns = _table_info(connection, "artifacts")
        if "source_item_id" not in columns:
            return found
        owner = connection.execute(
            "SELECT source_item_id FROM artifacts WHERE id=?",
            (found,),
        ).fetchone()
        current_id: str | None = source_item_id
        visited: set[str] = set()
        while current_id is not None:
            if current_id in visited:
                raise ValueError("Wykryto cykl w hierarchii source_items")
            visited.add(current_id)
            if owner is not None and owner["source_item_id"] == current_id:
                return found
            current = connection.execute(
                "SELECT parent_id FROM source_items WHERE id=?",
                (current_id,),
            ).fetchone()
            current_id = str(current["parent_id"]) if current and current["parent_id"] else None
        actual_owner = owner["source_item_id"] if owner is not None else None
        raise ValueError(
            f"artifact_id {found} należy do niezgodnego source_item {actual_owner}; "
            f"oczekiwano {source_item_id} lub jego przodka"
        )
    if found or not source_item_id:
        return found
    columns = _table_info(connection, "artifacts")
    if "source_item_id" not in columns:
        return None
    order = "CASE WHEN artifact_type = 'RAW' THEN 0 ELSE 1 END, id" if "artifact_type" in columns else "id"
    row = connection.execute(
        f"SELECT id FROM artifacts WHERE source_item_id = ? ORDER BY {order} LIMIT 1",
        (source_item_id,),
    ).fetchone()
    return str(row[0]) if row else None


def _import_v2(
    connection,
    *,
    payload: dict,
    analysis_id: str,
    source_id: str,
    result_path: Path,
    root: Path,
    model: str | None,
    now: str,
) -> dict[str, int | bool]:
    """Project a v2 payload into whichever v2 tables are installed.

    The complete JSON remains authoritative for the imported reading. Missing
    optional tables cause a documented no-op, never a partial legacy rewrite.
    Claims are deliberately not created or confirmed here.
    """

    reading_data = payload.get("reading") if isinstance(payload.get("reading"), dict) else {}
    is_v2 = str(payload.get("schema_version", "")) == "2.0" or bool(
        reading_data or payload.get("layers") or payload.get("events") or payload.get("field_readings")
    )
    counts: dict[str, int | bool] = {
        "v2_detected": is_v2,
        "reading_versions": 0,
        "transcript_segments": 0,
        "events": 0,
        "participations": 0,
        "source_assertions": 0,
    }
    if not is_v2:
        return counts

    source_item_id = _source_item_id(
        connection,
        source_id,
        payload.get("source_item_id"),
    )
    if not source_item_id or not _table_info(connection, "readings") or not _table_info(connection, "reading_versions"):
        return counts

    artifact_payload = payload.get("artifact_provenance") or []
    if isinstance(artifact_payload, list) and _table_info(connection, "artifacts"):
        for item in sorted(
            (item for item in artifact_payload if isinstance(item, dict)),
            key=lambda item: item.get("derived_from") is not None,
        ):
            artifact_metadata = {
                "operation": item.get("operation"),
                "parameters": item.get("parameters", []),
                "tool": item.get("tool"),
                "citeable_as_source": item.get("citeable_as_source"),
            }
            _insert_if_supported(
                connection,
                "artifacts",
                {
                    "id": item.get("artifact_id"),
                    "source_item_id": source_item_id,
                    "parent_artifact_id": _existing_id(connection, "artifacts", item.get("derived_from")),
                    "artifact_type": item.get("kind"),
                    "path": item.get("path"),
                    "immutable": 1 if item.get("kind") == "RAW" else 0,
                    "created_at": now,
                    "metadata_json": json.dumps(artifact_metadata, ensure_ascii=False),
                },
            )
        for item in (item for item in artifact_payload if isinstance(item, dict) and item.get("derived_from")):
            parent_id = _existing_id(connection, "artifacts", item.get("derived_from"))
            child_id = _existing_id(connection, "artifacts", item.get("artifact_id"))
            if parent_id and child_id:
                _insert_if_supported(
                    connection,
                    "artifact_derivations",
                    {
                        "parent_artifact_id": parent_id,
                        "child_artifact_id": child_id,
                        "operation": item.get("operation") or "unspecified",
                        "tool": item.get("tool"),
                        "parameters_json": json.dumps(item.get("parameters", []), ensure_ascii=False),
                        "created_at": now,
                    },
                )

    reading_type = str(reading_data.get("reading_type") or "contextual")
    reader = reading_data.get("reader") or model
    supersedes_analysis_id = reading_data.get("supersedes_analysis_id")
    supersedes_id = stable_id("rdv", str(supersedes_analysis_id)) if supersedes_analysis_id else None
    if supersedes_id and not _existing_id(connection, "reading_versions", supersedes_id):
        supersedes_id = None
    explicit_reading_id = reading_data.get("reading_id")
    reading_scope = str(payload.get("source_item_id") or source_id)
    reading_id = (
        stable_id("rdg", reading_scope, str(explicit_reading_id), reading_type)
        if explicit_reading_id
        else stable_id("rdg", analysis_id)
    )
    raw_candidate = next(
        (item.get("artifact_id") for item in artifact_payload if isinstance(item, dict) and item.get("kind") == "RAW"),
        None,
    )
    raw_artifact_id = _artifact_id(connection, source_item_id, raw_candidate)
    metadata = {
        "reading": reading_data,
        "classifications": payload.get("classifications"),
        "informant": payload.get("informant"),
        "artifact_provenance": artifact_payload,
        "independent_checks": payload.get("independent_checks", []),
        "uncertainties": payload.get("uncertainties", []),
        "field_readings": payload.get("field_readings", []),
    }
    _insert_if_supported(
        connection,
        "readings",
        {
            "id": reading_id,
            "source_item_id": source_item_id,
            "artifact_id": raw_artifact_id,
            "legacy_analysis_id": analysis_id,
            "reading_type": reading_type,
            "status": "unreviewed",
            "model_or_reviewer": reader,
            "model": model,
            "created_at": now,
            "metadata_json": json.dumps(metadata, ensure_ascii=False),
            "payload_json": json.dumps(payload, ensure_ascii=False, sort_keys=True),
        },
    )

    reading_version_id = stable_id("rdv", analysis_id)
    inserted_version = _insert_if_supported(
        connection,
        "reading_versions",
        {
            "id": reading_version_id,
            "reading_id": reading_id,
            "supersedes_id": supersedes_id,
            "version_number": int(reading_data.get("version") or 1),
            "status": str(payload.get("review_status") or "needs-review"),
            "result_path": result_path.relative_to(root).as_posix(),
            "payload_json": json.dumps(payload, ensure_ascii=False, sort_keys=True),
            "created_at": now,
        },
    )
    if not inserted_version or not _existing_id(connection, "reading_versions", reading_version_id):
        return counts
    counts["reading_versions"] = 1

    segment_ids: dict[str, str] = {}
    database_sequence = 0
    layers = payload.get("layers") if isinstance(payload.get("layers"), dict) else {}
    for layer_type in ("transcription", "transliteration", "translation", "extract"):
        segments = layers.get(layer_type, [])
        if not isinstance(segments, list):
            continue
        for sequence, segment in enumerate(segments):
            if not isinstance(segment, dict):
                continue
            local_segment_id = str(segment.get("segment_id") or f"{layer_type}-{sequence}")
            segment_id = stable_id("seg", reading_version_id, layer_type, local_segment_id)
            region_payload = {
                "layer_type": layer_type,
                "image": segment.get("image"),
                "region": segment.get("region"),
                "read_status": segment.get("read_status"),
            }
            artifact_id = _artifact_id(connection, source_item_id, segment.get("artifact_id"))
            if _insert_if_supported(
                connection,
                "transcript_segments",
                {
                    "id": segment_id,
                    "reading_version_id": reading_version_id,
                    "reading_id": reading_id,
                    "artifact_id": artifact_id,
                    "sequence_number": database_sequence,
                    "sequence_no": int(segment.get("sequence", sequence)),
                    "layer_type": layer_type,
                    "language": segment.get("language"),
                    "page_label": segment.get("image"),
                    "image_path": segment.get("image"),
                    "region_json": json.dumps(region_payload, ensure_ascii=False),
                    "region": segment.get("region"),
                    "text": str(segment.get("text") or ""),
                    "read_status": segment.get("read_status"),
                },
            ):
                segment_ids[local_segment_id] = segment_id
                counts["transcript_segments"] = int(counts["transcript_segments"]) + 1
                database_sequence += 1

    classifications = payload.get("classifications") if isinstance(payload.get("classifications"), dict) else {}
    source_class = classifications.get("source")
    information_class = classifications.get("information")

    def add_assertion(
        key: str,
        *,
        assertion_type: str,
        subject_ref: str | None,
        predicate: str,
        object_text: object,
        certainty: object,
        evidence: object,
    ) -> str | None:
        assertion_id = stable_id("ast", reading_version_id, key)
        evidence_list = evidence if isinstance(evidence, list) else []
        segment_ref = evidence_list[0].get("segment_ref") if evidence_list and isinstance(evidence_list[0], dict) else None
        segment_id = segment_ids.get(str(segment_ref)) if segment_ref else None
        inserted = _insert_if_supported(
            connection,
            "source_assertions",
            {
                "id": assertion_id,
                "source_item_id": source_item_id,
                "reading_version_id": reading_version_id,
                "reading_id": reading_id,
                "transcript_segment_id": segment_id,
                "segment_id": segment_id,
                "assertion_type": assertion_type,
                "subject_ref": subject_ref,
                "subject_text": subject_ref,
                "predicate": predicate,
                "object_text": None if object_text is None else str(object_text),
                "value_text": None if object_text is None else str(object_text),
                "information_quality": information_class,
                "information_class": information_class,
                "reading_certainty": certainty,
                "read_status": certainty,
                "source_class": source_class,
                "verification_status": "imported_unverified",
                "created_at": now,
                "legacy_evidence_key": f"v2:{reading_version_id}:{key}",
                "metadata_json": json.dumps(
                    {"evidence": evidence_list, "classifications": classifications},
                    ensure_ascii=False,
                ),
            },
        )
        if inserted and _existing_id(connection, "source_assertions", assertion_id):
            counts["source_assertions"] = int(counts["source_assertions"]) + 1
            return assertion_id
        return None

    for index, field in enumerate(payload.get("field_readings") or []):
        if not isinstance(field, dict):
            continue
        field_path = str(field.get("field_path") or f"field-{index}")
        evidence = field.get("evidence") if isinstance(field.get("evidence"), list) else []
        segment_ref = evidence[0].get("segment_ref") if evidence and isinstance(evidence[0], dict) else None
        segment_id = segment_ids.get(str(segment_ref)) if segment_ref else None
        _insert_if_supported(
            connection,
            "field_readings",
            {
                "id": stable_id("fld", reading_version_id, field_path),
                "reading_version_id": reading_version_id,
                "transcript_segment_id": segment_id,
                "field_name": field_path,
                "value_text": field.get("proposed_reading"),
                "alternatives_json": json.dumps(field.get("alternatives", []), ensure_ascii=False),
                "reading_certainty": field.get("read_status") or "Ambiguous",
                "notes": field.get("reason"),
            },
        )
        add_assertion(
            f"field:{field_path}",
            assertion_type="field-reading",
            subject_ref=field_path,
            predicate="proposed-reading",
            object_text=field.get("proposed_reading"),
            certainty=field.get("read_status"),
            evidence=field.get("evidence"),
        )

    for event_index, event in enumerate(payload.get("events") or []):
        if not isinstance(event, dict):
            continue
        local_event_id = str(event.get("event_id") or f"event-{event_index}")
        # An event belongs to the source document. Alternative readings are
        # attached through event_readings instead of duplicating the event.
        event_id = stable_id("evt", source_item_id, local_event_id)
        date = event.get("date") if isinstance(event.get("date"), dict) else {}
        place = event.get("place") if isinstance(event.get("place"), dict) else {}
        place_id = _existing_id(connection, "places", place.get("place_ref"))
        inserted_event = _insert_if_supported(
            connection,
            "events",
            {
                "id": event_id,
                "source_item_id": source_item_id,
                "reading_id": reading_id,
                "event_type": str(event.get("event_type") or "unknown"),
                "date_text": date.get("as_written"),
                "date_as_written": date.get("as_written"),
                "date_from": date.get("normalized"),
                "date_normalized": date.get("normalized"),
                "date_to": None,
                "calendar": date.get("calendar"),
                "place_id": place_id,
                "place_text": place.get("normalized") or place.get("as_written"),
                "verification_status": "imported_unverified",
                "status": "imported_unverified",
                "notes": json.dumps(
                    {"read_status": event.get("read_status"), "evidence": event.get("evidence", [])},
                    ensure_ascii=False,
                ),
            },
        )
        if not inserted_event or not _existing_id(connection, "events", event_id):
            continue
        _insert_if_supported(
            connection,
            "event_readings",
            {
                "id": stable_id("event_reading", event_id, reading_version_id),
                "event_id": event_id,
                "reading_version_id": reading_version_id,
                "evidence_json": json.dumps(
                    {"read_status": event.get("read_status"), "evidence": event.get("evidence", [])},
                    ensure_ascii=False,
                ),
            },
        )
        counts["events"] = int(counts["events"]) + 1
        participant_links: dict[str, tuple[str, str | None]] = {}
        for participant_index, participant in enumerate(event.get("participants") or []):
            if not isinstance(participant, dict):
                continue
            local_participant_id = str(participant.get("participant_id") or f"participant-{participant_index}")
            # Stable within a source event. A second reading with the same
            # participant key supplies another observation of one occurrence.
            occurrence_id = stable_id(
                "occ", source_item_id, local_event_id, local_participant_id
            )
            occurrence_details = {
                "name_original": participant.get("name_original"),
                "name_normalized": participant.get("name_normalized"),
                "requested_person_ref": participant.get("person_ref"),
                "sex": participant.get("sex"),
                "evidence": participant.get("evidence", []),
                "read_status": participant.get("read_status"),
            }
            occurrence_inserted = _insert_if_supported(
                connection,
                "person_occurrences",
                {
                    "id": occurrence_id,
                    "source_item_id": source_item_id,
                    "reading_version_id": reading_version_id,
                    # A model-supplied reference is only a candidate. Identity is
                    # linked later through an explicit identity decision.
                    "person_id": None,
                    "raw_name": participant.get("name_original"),
                    "name_as_written": participant.get("name_original"),
                    "normalized_name": participant.get("name_normalized"),
                    "search_name": search_fold(
                        str(participant.get("name_normalized") or participant.get("name_original") or "")
                    ),
                    "role_text": participant.get("role"),
                    "sex": participant.get("sex"),
                    "verification_status": "imported_unverified",
                    "status": "imported_unverified",
                    "evidence_json": json.dumps(participant.get("evidence", []), ensure_ascii=False),
                    "metadata_json": json.dumps(occurrence_details, ensure_ascii=False),
                },
            )
            if not occurrence_inserted or not _existing_id(connection, "person_occurrences", occurrence_id):
                continue
            _insert_if_supported(
                connection,
                "occurrence_readings",
                {
                    "id": stable_id("occurrence_reading", occurrence_id, reading_version_id),
                    "occurrence_id": occurrence_id,
                    "reading_version_id": reading_version_id,
                    "role_text": participant.get("role"),
                    "evidence_json": json.dumps(occurrence_details, ensure_ascii=False),
                },
            )
            assertion_id = add_assertion(
                f"participant:{local_event_id}:{local_participant_id}",
                assertion_type="participant-role",
                subject_ref=occurrence_id,
                predicate="participated-as",
                object_text=participant.get("role"),
                certainty=participant.get("read_status"),
                evidence=participant.get("evidence"),
            )
            participant_links[local_participant_id] = (occurrence_id, assertion_id)
            if _insert_if_supported(
                connection,
                "participations",
                {
                    "id": stable_id("par", event_id, occurrence_id, str(participant.get("role") or "")),
                    "event_id": event_id,
                    "occurrence_id": occurrence_id,
                    "person_occurrence_id": occurrence_id,
                    "role": str(participant.get("role") or "participant"),
                    "age_text": participant.get("age"),
                    "occupation_text": participant.get("occupation"),
                    "residence_text": participant.get("residence"),
                    "status_text": participant.get("read_status"),
                    "is_informant": 1 if participant.get("is_informant") else 0,
                    "source_assertion_id": assertion_id,
                    "details_json": json.dumps(occurrence_details, ensure_ascii=False),
                },
            ):
                counts["participations"] = int(counts["participations"]) + 1
            if participant.get("is_informant") and assertion_id:
                _insert_if_supported(
                    connection,
                    "informants",
                    {
                        "id": stable_id("inf", assertion_id, occurrence_id),
                        "source_assertion_id": assertion_id,
                        "occurrence_id": occurrence_id,
                        "role": participant.get("role"),
                        "knowledge_assessment": information_class or "Indeterminable",
                        "notes": "Informant wskazany w odczycie v2; ocena wymaga weryfikacji badacza.",
                    },
                )
        informant = payload.get("informant") if isinstance(payload.get("informant"), dict) else None
        if informant and informant.get("participant_ref") in participant_links:
            occurrence_id, assertion_id = participant_links[str(informant["participant_ref"])]
            if assertion_id:
                _insert_if_supported(
                    connection,
                    "informants",
                    {
                        "id": stable_id("inf", assertion_id, occurrence_id),
                        "source_assertion_id": assertion_id,
                        "occurrence_id": occurrence_id,
                        "role": informant.get("role"),
                        "knowledge_assessment": informant.get("information_class") or "Indeterminable",
                        "notes": informant.get("basis"),
                    },
                )
    return counts


def import_analysis(root: Path, payload: dict, model: str | None = None) -> dict:
    initialize(root)
    source_id = str(payload.get("source_id", "")).strip()
    get_source(root, source_id)
    if not isinstance(payload.get("people", []), list):
        raise ValueError("Pole people musi być listą")
    result_text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    analysis_id = stable_id("ana", source_id, result_text)
    result_path = root / "research" / "records" / "analyses" / f"{analysis_id}.json"
    dump_json(result_path, payload)
    status = str(payload.get("review_status", "needs-review"))
    now = utc_now()
    people_by_name: dict[str, str] = {}
    has_v2_shape = str(payload.get("schema_version", "")) == "2.0" or any(
        key in payload for key in ("reading", "layers", "events", "field_readings")
    )
    legacy_people = [] if has_v2_shape else payload.get("people", [])
    legacy_relationships = [] if has_v2_shape else payload.get("relationships", [])
    v2_counts: dict[str, int | bool] = {"v2_detected": has_v2_shape}
    with connect(root) as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO analyses(
                id, source_id, result_path, status, model, created_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                analysis_id,
                source_id,
                result_path.relative_to(root).as_posix(),
                status,
                model,
                now,
                result_text,
            ),
        )
        connection.execute("DELETE FROM mentions WHERE analysis_id = ?", (analysis_id,))
        connection.execute("DELETE FROM relations WHERE analysis_id = ?", (analysis_id,))
        for person in legacy_people:
            display_name = str(person.get("name_normalized") or person.get("name_original") or "").strip()
            if not display_name:
                continue
            entity_id = _entity_id("person", display_name)
            people_by_name[_normalize(display_name)] = entity_id
            people_by_name[_normalize(str(person.get("name_original", "")))] = entity_id
            connection.execute(
                """
                INSERT INTO entities(id, entity_type, display_name, normalized_name, notes)
                VALUES (?, 'person', ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    display_name = excluded.display_name,
                    notes = COALESCE(excluded.notes, entities.notes)
                """,
                (entity_id, display_name, _normalize(display_name), person.get("notes")),
            )
            roles = person.get("roles") or [None]
            for role in roles:
                connection.execute(
                    """
                    INSERT INTO mentions(
                        analysis_id, entity_id, role, raw_text, confidence, evidence_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        analysis_id,
                        entity_id,
                        role,
                        person.get("name_original"),
                        person.get("confidence"),
                        json.dumps(person.get("evidence", []), ensure_ascii=False),
                    ),
                )
        for relation in legacy_relationships:
            subject_name = str(relation.get("subject", "")).strip()
            object_name = str(relation.get("object", "")).strip()
            if not subject_name or not object_name:
                continue
            subject_id = people_by_name.get(_normalize(subject_name)) or _entity_id("person", subject_name)
            object_id = people_by_name.get(_normalize(object_name)) or _entity_id("person", object_name)
            for entity_id, display_name in ((subject_id, subject_name), (object_id, object_name)):
                connection.execute(
                    """
                    INSERT OR IGNORE INTO entities(
                        id, entity_type, display_name, normalized_name, notes
                    ) VALUES (?, 'person', ?, ?, NULL)
                    """,
                    (entity_id, display_name, _normalize(display_name)),
                )
            connection.execute(
                """
                INSERT INTO relations(
                    analysis_id, subject_entity_id, predicate, object_entity_id,
                    date_text, place_text, confidence, evidence_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    analysis_id,
                    subject_id,
                    relation.get("type", "related-to"),
                    object_id,
                    relation.get("date"),
                    relation.get("place"),
                    relation.get("confidence"),
                    json.dumps(relation.get("evidence", []), ensure_ascii=False),
                ),
            )
        v2_counts = _import_v2(
            connection,
            payload=payload,
            analysis_id=analysis_id,
            source_id=source_id,
            result_path=result_path,
            root=root,
            model=model,
            now=now,
        )
    temporary_output = root / "research" / "tmp" / f"analysis-{safe_filename(source_id)}.json"
    temporary_output.unlink(missing_ok=True)
    return {
        "analysis_id": analysis_id,
        "source_id": source_id,
        "status": status,
        "result_path": result_path.relative_to(root).as_posix(),
        "people": len(payload.get("people", [])),
        "relationships": len(payload.get("relationships", [])),
        "v2": v2_counts,
    }
