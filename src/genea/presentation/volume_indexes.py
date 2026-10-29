from __future__ import annotations

import json
import hashlib
import re
import sqlite3
from pathlib import Path
from typing import Any

from ..storage import connect_derivative_cache


REGISTRY_PATH = Path("config/volume-indexes.json")
ALLOWED_CLASSIFICATIONS = {"public", "historical"}
TELEMETRY_PROVENANCE = {"measured", "estimated", "unknown"}


def _telemetry_value(config: dict[str, Any], key: str) -> dict[str, Any]:
    telemetry = config.get("telemetry")
    item = telemetry.get(key) if isinstance(telemetry, dict) else None
    if not isinstance(item, dict):
        return {"value": None, "provenance": "unknown"}
    provenance = str(item.get("provenance") or "unknown")
    if provenance not in TELEMETRY_PROVENANCE:
        provenance = "unknown"
    value = item.get("value") if provenance != "unknown" else None
    return {
        "value": value,
        "provenance": provenance,
        "note": item.get("note"),
    }


def _project_runs(connection: sqlite3.Connection, volume_id: str) -> list[dict[str, Any]]:
    tables = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "recognition_attempts" not in tables:
        return []
    result = []
    for row in connection.execute(
        "SELECT * FROM recognition_attempts ORDER BY created_at DESC, attempt_id"
    ):
        config = json.loads(row["config_json"] or "{}")
        quality = {
            key: _telemetry_value(config, key)
            for key in ("recall", "precision", "cer", "wer", "fieldAccuracy", "unsupportedEvidence")
        }
        result.append(
            {
                "id": row["attempt_id"],
                "volumeId": volume_id,
                "unitId": row["unit_id"],
                "parentRegionId": row["parent_region_id"],
                "backend": row["backend"],
                "backendVersion": row["backend_version"],
                "model": row["model"],
                "configHash": hashlib.sha256(
                    json.dumps(config, ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest(),
                "createdAt": row["created_at"],
                "status": row["status"],
                "durationMs": _telemetry_value(config, "durationMs"),
                "tokens": _telemetry_value(config, "tokens"),
                "images": _telemetry_value(config, "images"),
                "cost": _telemetry_value(config, "cost"),
                "quality": quality,
            }
        )
    return result


def load_volume_index_registry(root: Path) -> list[dict[str, Any]]:
    path = root / REGISTRY_PATH
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != "genea.volume-index-registry/v1":
        raise ValueError("Nieobsługiwany schemat rejestru volume-index")
    entries = []
    seen = set()
    for item in payload.get("indexes", []):
        index_id = str(item.get("id") or "")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,63}", index_id) or index_id in seen:
            raise ValueError("Niepoprawny lub powtórzony identyfikator volume-index")
        classification = str(item.get("classification") or "")
        if classification not in ALLOWED_CLASSIFICATIONS:
            raise ValueError("UI może rejestrować tylko publiczne lub historyczne indeksy")
        relative = Path(str(item.get("path") or ""))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Ścieżka volume-index musi być względna i pozostawać w repo")
        resolved = (root / relative).resolve()
        if not resolved.is_relative_to(root.resolve()):
            raise ValueError("Ścieżka volume-index wychodzi poza repo")
        entries.append(
            {
                "id": index_id,
                "label": str(item.get("label") or index_id),
                "classification": classification,
                "path": resolved,
            }
        )
        seen.add(index_id)
    return entries


def resolve_registered_volume_index(root: Path, index_id: str) -> Path:
    for item in load_volume_index_registry(root):
        if item["id"] == index_id:
            return item["path"]
    raise LookupError("Nie znaleziono zarejestrowanego indeksu")


def project_volume_indexes(root: Path, *, candidate_limit: int = 80) -> list[dict[str, Any]]:
    result = []
    for entry in load_volume_index_registry(root):
        index_root = entry["path"]
        build_path = index_root / "build.json"
        database_path = index_root / "volume-index.sqlite3"
        if not build_path.is_file() or not database_path.is_file():
            result.append({**{key: entry[key] for key in ("id", "label", "classification")}, "status": "missing", "candidates": []})
            continue
        build = json.loads(build_path.read_text(encoding="utf-8"))
        connection = connect_derivative_cache(database_path, readonly=True)
        connection.row_factory = sqlite3.Row
        try:
            table_names = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            review_table = "review_decisions" in table_names
            attempt_count = (
                connection.execute("SELECT COUNT(*) FROM recognition_attempts").fetchone()[0]
                if "recognition_attempts" in table_names
                else 0
            )
            reviewed_count = (
                connection.execute("SELECT COUNT(DISTINCT region_id) FROM review_decisions").fetchone()[0]
                if review_table
                else 0
            )
            total_candidate_count = connection.execute("SELECT COUNT(*) FROM regions").fetchone()[0]
            review_join = """
                LEFT JOIN review_decisions d ON d.rowid=(
                  SELECT d2.rowid FROM review_decisions d2
                  WHERE d2.region_id=r.region_id
                  ORDER BY d2.created_at DESC, d2.rowid DESC LIMIT 1
                )
            """ if review_table else ""
            review_columns = ", d.decision, d.corrected_text, d.note, d.reviewer" if review_table else ", NULL AS decision, NULL AS corrected_text, NULL AS note, NULL AS reviewer"
            rows = connection.execute(
                f"""
                SELECT r.region_id, r.ordinal, r.locator, r.bbox_json, r.raw_text,
                       r.confidence, r.engine, r.model, u.render_path, u.width, u.height
                       {review_columns}
                FROM regions r JOIN units u ON u.unit_id=r.unit_id
                {review_join}
                ORDER BY CASE WHEN decision IS NULL OR decision='defer' THEN 0 ELSE 1 END,
                         r.ordinal, r.region_id
                LIMIT ?
                """,
                (candidate_limit,),
            ).fetchall()
            candidates = [
                {
                    "regionId": row["region_id"],
                    "ordinal": row["ordinal"],
                    "locator": row["locator"],
                    "bbox": json.loads(row["bbox_json"]) if row["bbox_json"] else None,
                    "rawText": row["raw_text"],
                    "confidence": row["confidence"],
                    "engine": row["engine"],
                    "model": row["model"],
                    "reviewDecision": row["decision"],
                    "correctedText": row["corrected_text"],
                    "reviewNote": row["note"],
                    "reviewer": row["reviewer"],
                    "renderUrl": f"/local-volume-index/{entry['id']}/{row['ordinal']}",
                    "width": row["width"],
                    "height": row["height"],
                }
                for row in rows
            ]
            runs = _project_runs(connection, entry["id"])
        finally:
            connection.close()
        summary = build.get("summary") or {}
        result.append(
            {
                "id": entry["id"],
                "label": entry["label"],
                "classification": entry["classification"],
                "status": "ready",
                "unitCount": summary.get("unit_count", 0),
                "regionCount": summary.get("region_count", 0),
                "attemptCount": attempt_count,
                "reviewedCount": reviewed_count,
                "candidateCount": total_candidate_count,
                "scope": build.get("scope"),
                "warning": build.get("warning"),
                "candidates": candidates,
                "runs": runs,
            }
        )
    return result


def project_volume_index_page(
    root: Path,
    index_id: str,
    *,
    cursor: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    """Return one bounded, read-only page from an allowlisted derivative index."""

    if cursor < 0 or not 1 <= limit <= 100:
        raise ValueError("Niepoprawny cursor lub limit")
    index_root = resolve_registered_volume_index(root, index_id)
    database_path = index_root / "volume-index.sqlite3"
    connection = connect_derivative_cache(database_path, readonly=True)
    connection.row_factory = sqlite3.Row
    try:
        table_names = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        review_table = "review_decisions" in table_names
        review_join = """
            LEFT JOIN review_decisions d ON d.rowid=(
              SELECT d2.rowid FROM review_decisions d2
              WHERE d2.region_id=r.region_id
              ORDER BY d2.created_at DESC, d2.rowid DESC LIMIT 1
            )
        """ if review_table else ""
        review_columns = ", d.decision, d.corrected_text, d.note, d.reviewer" if review_table else ", NULL AS decision, NULL AS corrected_text, NULL AS note, NULL AS reviewer"
        total = int(connection.execute("SELECT COUNT(*) FROM regions").fetchone()[0])
        rows = connection.execute(
            f"""
            SELECT r.region_id, r.ordinal, r.locator, r.bbox_json, r.raw_text,
                   r.confidence, r.engine, r.model, u.width, u.height
                   {review_columns}
            FROM regions r JOIN units u ON u.unit_id=r.unit_id
            {review_join}
            ORDER BY CASE WHEN decision IS NULL OR decision='defer' THEN 0 ELSE 1 END,
                     r.ordinal, r.region_id LIMIT ? OFFSET ?
            """,
            (limit, cursor),
        ).fetchall()
        items = [
            {
                "regionId": row["region_id"],
                "ordinal": row["ordinal"],
                "locator": row["locator"],
                "bbox": json.loads(row["bbox_json"]) if row["bbox_json"] else None,
                "rawText": row["raw_text"],
                "confidence": row["confidence"],
                "engine": row["engine"],
                "model": row["model"],
                "reviewDecision": row["decision"],
                "correctedText": row["corrected_text"],
                "reviewNote": row["note"],
                "reviewer": row["reviewer"],
                "renderUrl": f"/local-volume-index/{index_id}/{row['ordinal']}",
                "width": row["width"],
                "height": row["height"],
            }
            for row in rows
        ]
    finally:
        connection.close()
    next_cursor = cursor + len(items)
    return {
        "items": items,
        "cursor": cursor,
        "nextCursor": next_cursor if next_cursor < total else None,
        "total": total,
        "limit": limit,
    }


def served_volume_index_renders(root: Path) -> dict[str, dict[str, Any]]:
    files = {}
    for entry in load_volume_index_registry(root):
        database = entry["path"] / "volume-index.sqlite3"
        if not database.is_file():
            continue
        connection = connect_derivative_cache(database, readonly=True)
        connection.row_factory = sqlite3.Row
        try:
            for row in connection.execute("SELECT ordinal, render_path, render_sha256 FROM units"):
                path = (entry["path"] / row["render_path"]).resolve()
                if path.is_relative_to(entry["path"]) and path.is_file():
                    files[f"{entry['id']}:{row['ordinal']}"] = {
                        "path": path,
                        "mediaType": "image/png",
                    }
        finally:
            connection.close()
    return files
