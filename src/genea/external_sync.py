from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .db import connect, initialize
from .research import require_tables
from .workspace import stable_id, utc_now


SYNC_STATUSES = {
    "pending",
    "staged",
    "published",
    "needs_review",
    "failed",
    "skipped",
}

OBJECT_COLUMNS = {
    "event": (
        "events",
        "id,event_type,date_text,date_from,date_to,place_id,place_text,"
        "verification_status,notes,source_item_id",
    ),
    "claim": (
        "claims",
        "id,case_id,statement,subject_ref,predicate,object_ref,status,rationale,updated_at",
    ),
    "person": ("persons", "id,preferred_name,status,notes"),
    "source_item": (
        "source_items",
        "id,title,item_type,date_text,locator,source_url,citation_text,source_quality,"
        "verification_status,metadata_json",
    ),
}

PRESENTATION_OBJECT_KEYS = {
    "presentation_person": "people",
    "presentation_family": "families",
    "presentation_union": "unions",
}


def _presentation_object(root: Path, object_type: str, object_id: str) -> dict:
    collection_key = PRESENTATION_OBJECT_KEYS[object_type]
    path = root / "research" / "presentation" / "family-tree.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise LookupError(f"Nie znaleziono widoku prezentacyjnego: {path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"Nieprawidłowy JSON widoku prezentacyjnego: {path}") from error
    for item in payload.get(collection_key, []):
        if item.get("id") == object_id:
            return item
    raise LookupError(
        f"Nie znaleziono {object_type} o identyfikatorze {object_id} w {path}"
    )


def _canonical_object(root: Path, connection, object_type: str, object_id: str) -> dict:
    if object_type in PRESENTATION_OBJECT_KEYS:
        return _presentation_object(root, object_type, object_id)
    try:
        table, columns = OBJECT_COLUMNS[object_type]
    except KeyError as error:
        raise ValueError(
            "object_type musi być jednym z: " + ", ".join(sorted(OBJECT_COLUMNS))
        ) from error
    row = connection.execute(
        f"SELECT {columns} FROM {table} WHERE id=?", (object_id,)
    ).fetchone()
    if row is None:
        raise LookupError(f"Nie znaleziono {object_type} o identyfikatorze {object_id}")
    return dict(row)


def _fingerprint(value: dict) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def track_external_object(
    root: Path,
    service: str,
    object_type: str,
    object_id: str,
    *,
    status: str = "pending",
    remote_object_id: str | None = None,
    remote_url: str | None = None,
    notes: str | None = None,
) -> dict:
    service = service.strip().casefold()
    if not service:
        raise ValueError("service nie może być puste")
    if status not in SYNC_STATUSES:
        raise ValueError("Nieprawidłowy status synchronizacji")
    initialize(root)
    now = utc_now()
    with connect(root) as connection:
        require_tables(connection, {"external_sync_items", "audit_log"})
        canonical = _canonical_object(root, connection, object_type, object_id)
        fingerprint = _fingerprint(canonical)
        item_id = stable_id("external_sync", service, object_type, object_id)
        existing = connection.execute(
            "SELECT * FROM external_sync_items WHERE id=?", (item_id,)
        ).fetchone()
        created_at = existing["created_at"] if existing else now
        published_fingerprint = (
            fingerprint
            if status == "published"
            else (existing["published_fingerprint"] if existing else None)
        )
        last_published_at = (
            now
            if status == "published"
            else (existing["last_published_at"] if existing else None)
        )
        item = {
            "id": item_id,
            "service": service,
            "object_type": object_type,
            "object_id": object_id,
            "status": status,
            "local_fingerprint": fingerprint,
            "published_fingerprint": published_fingerprint,
            "remote_object_id": remote_object_id
            if remote_object_id is not None
            else (existing["remote_object_id"] if existing else None),
            "remote_url": remote_url
            if remote_url is not None
            else (existing["remote_url"] if existing else None),
            "notes": notes if notes is not None else (existing["notes"] if existing else None),
            "created_at": created_at,
            "updated_at": now,
            "last_checked_at": now,
            "last_published_at": last_published_at,
        }
        connection.execute(
            """INSERT INTO external_sync_items(
                   id,service,object_type,object_id,status,local_fingerprint,
                   published_fingerprint,remote_object_id,remote_url,notes,
                   created_at,updated_at,last_checked_at,last_published_at
               ) VALUES (
                   :id,:service,:object_type,:object_id,:status,:local_fingerprint,
                   :published_fingerprint,:remote_object_id,:remote_url,:notes,
                   :created_at,:updated_at,:last_checked_at,:last_published_at
               ) ON CONFLICT(service,object_type,object_id) DO UPDATE SET
                   status=excluded.status,
                   local_fingerprint=excluded.local_fingerprint,
                   published_fingerprint=excluded.published_fingerprint,
                   remote_object_id=excluded.remote_object_id,
                   remote_url=excluded.remote_url,
                   notes=excluded.notes,
                   updated_at=excluded.updated_at,
                   last_checked_at=excluded.last_checked_at,
                   last_published_at=excluded.last_published_at""",
            item,
        )
        after = dict(
            connection.execute(
                "SELECT * FROM external_sync_items WHERE id=?", (item_id,)
            ).fetchone()
        )
        connection.execute(
            """INSERT INTO audit_log(
                   occurred_at,actor,action,object_type,object_id,
                   before_json,after_json,rationale
               ) VALUES (?,?,'track_external_sync','external_sync_item',?,?,?,?)""",
            (
                now,
                "external-sync",
                item_id,
                json.dumps(dict(existing), ensure_ascii=False) if existing else None,
                json.dumps(after, ensure_ascii=False),
                "Rejestracja lub aktualizacja stanu publikacji w serwisie zewnętrznym.",
            ),
        )
    return sync_status(root, service=service, object_type=object_type, object_id=object_id)[0]


def mark_external_object(
    root: Path,
    service: str,
    object_type: str,
    object_id: str,
    status: str,
    *,
    remote_object_id: str | None = None,
    remote_url: str | None = None,
    notes: str | None = None,
) -> dict:
    return track_external_object(
        root,
        service,
        object_type,
        object_id,
        status=status,
        remote_object_id=remote_object_id,
        remote_url=remote_url,
        notes=notes,
    )


def sync_status(
    root: Path,
    *,
    service: str | None = None,
    object_type: str | None = None,
    object_id: str | None = None,
    pending_only: bool = False,
) -> list[dict]:
    initialize(root)
    with connect(root) as connection:
        require_tables(connection, {"external_sync_items"})
        clauses: list[str] = []
        values: list[str] = []
        if service:
            clauses.append("service=?")
            values.append(service.strip().casefold())
        if object_type:
            clauses.append("object_type=?")
            values.append(object_type)
        if object_id:
            clauses.append("object_id=?")
            values.append(object_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = connection.execute(
            "SELECT * FROM external_sync_items" + where + " ORDER BY service,object_type,object_id",
            values,
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                current = _fingerprint(
                    _canonical_object(root, connection, item["object_type"], item["object_id"])
                )
                missing_local_object = False
            except LookupError:
                current = None
                missing_local_object = True
            changed = current != item["published_fingerprint"]
            effective = item["status"]
            if missing_local_object:
                effective = "needs_review"
            elif changed and effective in {"published", "skipped"}:
                effective = "pending"
            item.update(
                {
                    "current_fingerprint": current,
                    "changed_since_publish": changed,
                    "missing_local_object": missing_local_object,
                    "effective_status": effective,
                }
            )
            if pending_only and effective not in {"pending", "staged", "needs_review", "failed"}:
                continue
            result.append(item)
    return result


__all__ = [
    "SYNC_STATUSES",
    "mark_external_object",
    "sync_status",
    "track_external_object",
]
