from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Iterable

from .db import connect
from .research import require_tables
from .review import _reading_provenance


PRIVATE_CLASSIFICATIONS = {"living", "restricted", "family_private", "strict_private"}
BASE_DEBT_TABLES = {
    "source_items",
    "artifacts",
    "readings",
    "reading_versions",
    "transcript_segments",
    "events",
    "person_occurrences",
    "participations",
}
REASSESSMENT_TABLES = {
    "reassessment_batches",
    "reassessment_batch_states",
    "reassessment_triggers",
    "reassessment_items",
    "reassessment_item_results",
    "reassessment_impacts",
    "reassessment_reviews",
    "document_extraction_states",
    "mapping_writeback_manifests",
    "mapping_writeback_runs",
}


def _json_object(value: str | None) -> dict:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _fingerprint(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _table_exists(connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _privacy_classification(connection, source_item_id: str) -> str | None:
    if not _table_exists(connection, "privacy_labels"):
        return None
    row = connection.execute(
        """SELECT classification FROM privacy_labels
           WHERE object_type='source_item' AND object_id=?""",
        (source_item_id,),
    ).fetchone()
    return row[0] if row else None


def _source_item_rows(connection, source_item_id: str | None = None) -> list[dict]:
    extraction_select = (
        """(SELECT status FROM document_extraction_states des
              WHERE des.source_item_id=si.id ORDER BY revision DESC LIMIT 1)"""
        if _table_exists(connection, "document_extraction_states")
        else "NULL"
    )
    review_select = (
        """(SELECT status FROM reading_review_decisions rrd
              WHERE rrd.source_item_id=si.id ORDER BY revision DESC LIMIT 1)"""
        if _table_exists(connection, "reading_review_decisions")
        else "NULL"
    )
    review_revision_select = (
        """(SELECT revision FROM reading_review_decisions rrd
              WHERE rrd.source_item_id=si.id ORDER BY revision DESC LIMIT 1)"""
        if _table_exists(connection, "reading_review_decisions")
        else "NULL"
    )
    clauses = "WHERE si.id=?" if source_item_id else ""
    values: tuple[object, ...] = (source_item_id,) if source_item_id else ()
    rows = connection.execute(
        f"""SELECT si.id,si.title,si.item_type,si.legacy_source_id,
                   (SELECT COUNT(*) FROM artifacts a WHERE a.source_item_id=si.id) artifact_count,
                   (SELECT COUNT(*) FROM readings r WHERE r.source_item_id=si.id) reading_count,
                   (SELECT COUNT(*) FROM transcript_segments ts
                      JOIN reading_versions rv ON rv.id=ts.reading_version_id
                      JOIN readings r ON r.id=rv.reading_id
                     WHERE r.source_item_id=si.id) segment_count,
                   (SELECT COUNT(*) FROM events e WHERE e.source_item_id=si.id) event_count,
                   (SELECT COUNT(*) FROM person_occurrences po WHERE po.source_item_id=si.id) occurrence_count,
                   (SELECT COUNT(*) FROM person_occurrences po
                     WHERE po.source_item_id=si.id AND po.reading_version_id IS NOT NULL)
                       occurrence_with_reading_count,
                   (SELECT COUNT(*) FROM person_occurrences po
                     WHERE po.source_item_id=si.id AND po.transcript_segment_id IS NOT NULL)
                       occurrence_with_segment_count,
                   (SELECT COUNT(*) FROM participations p
                      JOIN person_occurrences po ON po.id=p.occurrence_id
                     WHERE po.source_item_id=si.id) participation_count,
                   {review_select} review_status,
                   {review_revision_select} review_revision,
                   {extraction_select} extraction_status
              FROM source_items si {clauses}
             ORDER BY si.id""",
        values,
    ).fetchall()
    return [dict(row) for row in rows]


def _debt_codes(row: dict) -> list[str]:
    if row.get("item_type") == "source_alias":
        return []
    result: list[str] = []
    if not row["artifact_count"]:
        result.append("missing_artifact")
    if not row["reading_count"]:
        result.append("missing_reading")
    if row["reading_count"] and not row["segment_count"]:
        result.append("missing_transcript_segment")
    if not row["event_count"]:
        result.append("missing_event")
    if row["occurrence_count"] and (
        row["occurrence_with_reading_count"] < row["occurrence_count"]
    ):
        result.append("occurrence_missing_reading_version")
    if row["occurrence_count"] and (
        row["occurrence_with_segment_count"] < row["occurrence_count"]
    ):
        result.append("occurrence_missing_segment")
    if row["event_count"] and not row["occurrence_count"]:
        result.append("event_missing_occurrences")
    if not row["extraction_status"]:
        result.append("missing_extraction_status")
    return result


def index_debt_report(
    root: Path,
    *,
    source_item_id: str | None = None,
    include_items: bool = False,
    include_restricted: bool = False,
    limit: int = 1000,
) -> dict:
    """Return a deterministic, read-only report of mapping and extraction debt."""

    if limit < 1 or limit > 10000:
        raise ValueError("limit musi mieścić się w zakresie 1..10000")
    with connect(root) as connection:
        require_tables(connection, BASE_DEBT_TABLES)
        rows = _source_item_rows(connection, source_item_id)
        if source_item_id and not rows:
            raise KeyError(f"Nie znaleziono source_item: {source_item_id}")
        debt_counter: Counter[str] = Counter()
        restricted_count = 0
        visible_items: list[dict] = []
        for row in rows:
            classification = _privacy_classification(connection, row["id"])
            row["privacy_classification"] = classification
            row["debt_codes"] = _debt_codes(row)
            row["debt_count"] = len(row["debt_codes"])
            debt_counter.update(row["debt_codes"])
            if classification in PRIVATE_CLASSIFICATIONS and not include_restricted:
                restricted_count += 1
                continue
            visible_items.append(row)
        visible_items.sort(key=lambda item: (-item["debt_count"], item["id"]))
        summary = {
            "sourceItems": len(rows),
            "artifacts": sum(item["artifact_count"] for item in rows),
            "readings": sum(item["reading_count"] for item in rows),
            "transcriptSegments": sum(item["segment_count"] for item in rows),
            "events": sum(item["event_count"] for item in rows),
            "personOccurrences": sum(item["occurrence_count"] for item in rows),
            "occurrencesWithReadingVersion": sum(
                item["occurrence_with_reading_count"] for item in rows
            ),
            "occurrencesWithSegment": sum(
                item["occurrence_with_segment_count"] for item in rows
            ),
            "participations": sum(item["participation_count"] for item in rows),
            "reviewedSourceItems": sum(bool(item["review_status"]) for item in rows),
            "extractionStateSourceItems": sum(
                bool(item["extraction_status"]) for item in rows
            ),
            "restrictedItemsOmitted": restricted_count,
            "debtByCode": dict(sorted(debt_counter.items())),
        }
        payload: dict[str, object] = {
            "schemaVersion": 1,
            "databaseSchema": int(connection.execute("PRAGMA user_version").fetchone()[0]),
            "scope": {"sourceItemId": source_item_id},
            "summary": summary,
        }
        payload["reportFingerprint"] = _fingerprint(payload)
        if include_items:
            payload["items"] = visible_items[:limit]
            payload["itemsTruncated"] = len(visible_items) > limit
        return payload


def _reading_rows(connection, source_item_ids: Iterable[str] | None = None) -> list[dict]:
    identifiers = sorted(set(source_item_ids or []))
    where = ""
    values: tuple[object, ...] = ()
    if identifiers:
        placeholders = ",".join("?" for _ in identifiers)
        where = f"WHERE si.id IN ({placeholders})"
        values = tuple(identifiers)
    rows = connection.execute(
        f"""SELECT si.id source_item_id,si.title,si.item_type,
                   r.id reading_id,r.reading_type,r.status reading_status,
                   r.model_or_reviewer,r.metadata_json,
                   rv.id id,rv.id reading_version_id,rv.version_number,
                   rv.status version_status,
                   rv.result_path,rv.payload_json,rv.created_at
              FROM source_items si
              JOIN readings r ON r.source_item_id=si.id
              JOIN reading_versions rv ON rv.reading_id=r.id
              {where}
             ORDER BY si.id,r.reading_type,rv.version_number,rv.id""",
        values,
    ).fetchall()
    return [dict(row) for row in rows]


def _artifact_fingerprints(connection, source_item_id: str) -> list[dict]:
    return [
        {"id": row[0], "sha256": row[1], "artifactType": row[2]}
        for row in connection.execute(
            """SELECT id,sha256,artifact_type FROM artifacts
               WHERE source_item_id=? ORDER BY id""",
            (source_item_id,),
        ).fetchall()
    ]


def _latest_review(connection, source_item_id: str) -> dict | None:
    if not _table_exists(connection, "reading_review_decisions"):
        return None
    row = connection.execute(
        """SELECT id,revision,status,decided_by_kind,created_at
           FROM reading_review_decisions WHERE source_item_id=?
           ORDER BY revision DESC LIMIT 1""",
        (source_item_id,),
    ).fetchone()
    return dict(row) if row else None


def _candidate_state(readings: list[dict], review: dict | None) -> tuple[str, list[str]]:
    if review:
        return "reviewed", []
    blinds = [item for item in readings if item["readingType"] == "blind"]
    contextual = [item for item in readings if item["readingType"] == "contextual"]
    adjudicators = [
        item
        for item in readings
        if item["readingType"] in {"agent_review", "adjudication"}
    ]
    valid_blinds = [
        item
        for item in blinds
        if item["provenance"].get("context_disclosed") is False
        and item["provenance"].get("reader_id")
        and item["provenance"].get("run_id")
    ]
    valid_contextual = [
        item
        for item in contextual
        if item["provenance"].get("context_disclosed") is True
    ]
    blind_runs = {item["provenance"].get("run_id") for item in valid_blinds}
    blind_readers = {item["provenance"].get("reader_id") for item in valid_blinds}
    problems: list[str] = []
    if len(valid_blinds) != len(blinds):
        problems.append("blind_provenance_incomplete")
    if len(valid_contextual) != len(contextual):
        problems.append("contextual_provenance_incomplete")
    if len(valid_blinds) >= 2 and (len(blind_runs) < 2 or len(blind_readers) < 2):
        problems.append("blind_independence_not_demonstrated")
    if problems:
        return "needs_provenance_audit", sorted(set(problems))
    if len(valid_blinds) < 1:
        return "needs_blind_primary", []
    if len(valid_blinds) < 2:
        return "needs_blind_secondary", []
    if not valid_contextual:
        return "needs_contextual", []
    if not adjudicators:
        return "ready_for_agent_adjudication", []
    return "ready_to_record_decision", []


def agent_review_manifest(
    root: Path,
    *,
    source_item_ids: Iterable[str] | None = None,
    include_reviewed: bool = False,
    include_restricted: bool = False,
    limit: int = 1000,
) -> dict:
    """Build a deterministic read-only manifest of agent-first review readiness."""

    if limit < 1 or limit > 10000:
        raise ValueError("limit musi mieścić się w zakresie 1..10000")
    requested_ids = sorted(set(source_item_ids or []))
    with connect(root) as connection:
        require_tables(
            connection,
            {
                "source_items",
                "artifacts",
                "readings",
                "reading_versions",
            },
        )
        if requested_ids:
            placeholders = ",".join("?" for _ in requested_ids)
            found = {
                row[0]
                for row in connection.execute(
                    f"SELECT id FROM source_items WHERE id IN ({placeholders})",
                    tuple(requested_ids),
                ).fetchall()
            }
            missing = sorted(set(requested_ids) - found)
            if missing:
                raise KeyError("Nie znaleziono source_item: " + ", ".join(missing))
        grouped: dict[str, list[dict]] = {}
        titles: dict[str, tuple[str, str]] = {}
        for row in _reading_rows(connection, requested_ids or None):
            provenance = _reading_provenance(row)
            reading = {
                "readingId": row["reading_id"],
                "readingVersionId": row["reading_version_id"],
                "readingType": row["reading_type"],
                "versionNumber": row["version_number"],
                "readingStatus": row["reading_status"],
                "versionStatus": row["version_status"],
                "resultPath": row["result_path"],
                "provenance": provenance,
                "payloadFingerprint": _fingerprint(_json_object(row["payload_json"])),
            }
            grouped.setdefault(row["source_item_id"], []).append(reading)
            titles[row["source_item_id"]] = (row["title"], row["item_type"])
        if requested_ids:
            for source_item_id in requested_ids:
                if source_item_id not in titles:
                    row = connection.execute(
                        "SELECT title,item_type FROM source_items WHERE id=?",
                        (source_item_id,),
                    ).fetchone()
                    titles[source_item_id] = (row[0], row[1])
                    grouped[source_item_id] = []
        items: list[dict] = []
        omitted_restricted = 0
        states: Counter[str] = Counter()
        for source_item_id in sorted(grouped):
            classification = _privacy_classification(connection, source_item_id)
            if classification in PRIVATE_CLASSIFICATIONS and not include_restricted:
                omitted_restricted += 1
                continue
            readings = grouped[source_item_id]
            review = _latest_review(connection, source_item_id)
            state, problems = _candidate_state(readings, review)
            if state == "reviewed" and not include_reviewed:
                continue
            title, item_type = titles[source_item_id]
            input_payload = {
                "sourceItemId": source_item_id,
                "artifacts": _artifact_fingerprints(connection, source_item_id),
                "readings": readings,
                "latestReview": review,
            }
            item = {
                "sourceItemId": source_item_id,
                "title": title,
                "itemType": item_type,
                "privacyClassification": classification,
                "state": state,
                "problems": problems,
                "inputFingerprint": _fingerprint(input_payload),
                "latestReview": review,
                "readings": readings,
            }
            states[state] += 1
            items.append(item)
        items.sort(key=lambda item: (item["state"], item["sourceItemId"]))
        result: dict[str, object] = {
            "schemaVersion": 1,
            "databaseSchema": int(connection.execute("PRAGMA user_version").fetchone()[0]),
            "scope": {"sourceItemIds": requested_ids},
            "summary": {
                "items": len(items),
                "byState": dict(sorted(states.items())),
                "restrictedItemsOmitted": omitted_restricted,
            },
            "items": items[:limit],
            "itemsTruncated": len(items) > limit,
        }
        result["manifestFingerprint"] = _fingerprint(result)
        return result


def reassessment_show(root: Path, batch_id: str) -> dict:
    """Return the immutable batch record and the latest state of its parts."""

    with connect(root) as connection:
        require_tables(connection, REASSESSMENT_TABLES)
        batch = connection.execute(
            "SELECT * FROM reassessment_batches WHERE id=?", (batch_id,)
        ).fetchone()
        if not batch:
            raise KeyError(f"Nie znaleziono batcha reassessmentu: {batch_id}")
        state = connection.execute(
            """SELECT * FROM reassessment_batch_states WHERE batch_id=?
               ORDER BY revision DESC LIMIT 1""",
            (batch_id,),
        ).fetchone()
        triggers = connection.execute(
            "SELECT * FROM reassessment_triggers WHERE batch_id=? ORDER BY priority DESC,id",
            (batch_id,),
        ).fetchall()
        items = connection.execute(
            "SELECT * FROM reassessment_items WHERE batch_id=? ORDER BY risk_level DESC,cost_class,id",
            (batch_id,),
        ).fetchall()
        impacts = connection.execute(
            """SELECT i.* FROM reassessment_impacts i
               WHERE i.batch_id=? AND i.revision=(
                   SELECT MAX(latest.revision) FROM reassessment_impacts latest
                   WHERE latest.batch_id=i.batch_id
                     AND latest.object_type=i.object_type
                     AND latest.object_id=i.object_id
                     AND latest.impact_type=i.impact_type
               ) ORDER BY i.object_type,i.object_id,i.impact_type""",
            (batch_id,),
        ).fetchall()
        reviews = connection.execute(
            "SELECT * FROM reassessment_reviews WHERE batch_id=? ORDER BY created_at,id",
            (batch_id,),
        ).fetchall()
        manifests = connection.execute(
            "SELECT * FROM mapping_writeback_manifests WHERE batch_id=? ORDER BY created_at,id",
            (batch_id,),
        ).fetchall()
        return {
            "batch": dict(batch),
            "latestState": dict(state) if state else None,
            "triggers": [dict(row) for row in triggers],
            "items": [dict(row) for row in items],
            "impacts": [dict(row) for row in impacts],
            "reviews": [dict(row) for row in reviews],
            "manifests": [dict(row) for row in manifests],
        }


def reassessment_impact_report(root: Path, batch_id: str) -> dict:
    details = reassessment_show(root, batch_id)
    statuses = Counter(item["writeback_status"] for item in details["impacts"])
    return {
        "batchId": batch_id,
        "batchState": (details["latestState"] or {}).get("status"),
        "impactCount": len(details["impacts"]),
        "byWritebackStatus": dict(sorted(statuses.items())),
        "impacts": details["impacts"],
    }


__all__ = [
    "agent_review_manifest",
    "index_debt_report",
    "reassessment_impact_report",
    "reassessment_show",
]
