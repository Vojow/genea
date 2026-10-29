"""Private archival inquiry registry; importing records never delivers requests.

Only an explicit curator import writes this table. A recorded dispatch/reply is
an operator assertion with a required response note, not evidence of delivery or
of the genealogical claims in the requested material. No files or URLs are read.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from .db import connect
from .storage import configured_database_backend
from .workspace import utc_now


STATUSES = frozenset({"draft", "ready", "sent", "awaiting_reply", "received", "deferred"})
READINESS = frozenset({"ready_for_inquiry", "needs_details", "needs_eligibility", "online_first"})
PRIVACY = frozenset({"private", "strict_private"})
FIELDS = {
    "id": "id", "caseId": "case_id", "title": "title", "repository": "repository",
    "recipient": "recipient", "status": "status", "readiness": "readiness",
    "priority": "priority", "requestBody": "request_body", "instructions": "instructions",
    "targets": "targets_json", "provenance": "provenance_json",
    "missingFields": "missing_fields_json", "privacy": "privacy",
    "responseNote": "response_note", "createdAt": "created_at", "updatedAt": "updated_at",
}
JSON_FIELDS = {"targets", "provenance", "missingFields"}


class RegistryUnavailableError(LookupError):
    """The archive request migration has not been explicitly applied."""


def _require_registry(connection) -> None:
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='archive_requests'"
    ).fetchone() is None:
        raise RegistryUnavailableError("Archive request registry requires migration 27")


def _text(value: object, field: str, *, required: bool = False) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise ValueError(f"{field} must be text without NUL characters")
    if required and not value.strip():
        raise ValueError(f"{field} must not be empty")
    return value


def _choice(value: object, choices: frozenset[str], field: str) -> str:
    value = _text(value, field, required=True)
    if value not in choices:
        raise ValueError(f"Invalid {field}: {value}")
    return value


def _url(value: object, field: str) -> str:
    value = _text(value, field, required=True)
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme in {"http", "https"} and bool(parsed.hostname)
            and parsed.username is None and parsed.password is None
            and not any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value)
            and "\\" not in value
        )
        parsed.port  # Reject malformed ports as well as malformed IPv6 hosts.
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(f"{field} must be a safe http(s) URL")
    return value


def _provenance(value: object, root: Path) -> str:
    value = _text(value, "provenance", required=True)
    if urlsplit(value).scheme or value.startswith("//"):
        return _url(value, "provenance")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value or path == PurePosixPath("."):
        raise ValueError("provenance must be a project-relative path or http(s) URL")
    if not (root / value).resolve().is_relative_to(root):
        raise ValueError("provenance path escapes the project")
    return value


def _timestamp(value: object, field: str) -> str:
    value = _text(value, field, required=True)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO timestamp with timezone") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must have a timezone")
    return value


def _validate_item(root: Path, item: object) -> dict[str, object]:
    if not isinstance(item, dict) or set(item) - FIELDS.keys():
        raise ValueError("Archive request must be an object with known camelCase fields")
    result: dict[str, object] = {}
    for field in ("id", "title", "repository", "readiness"):
        result[field] = _text(item.get(field), field, required=True)
    for field in ("recipient", "requestBody", "instructions", "responseNote"):
        result[field] = _text(item.get(field, ""), field)
    result["caseId"] = None if item.get("caseId") is None else _text(item["caseId"], "caseId", required=True)
    result["status"] = _choice(item.get("status", "draft"), STATUSES, "status")
    result["readiness"] = _choice(result["readiness"], READINESS, "readiness")
    result["privacy"] = _choice(item.get("privacy", "private"), PRIVACY, "privacy")
    priority = item.get("priority", 100)
    if type(priority) is not int or not 0 <= priority <= 2**53 - 1:
        raise ValueError("priority must be a nonnegative JavaScript-safe integer")
    result["priority"] = priority
    if result["status"] in {"sent", "awaiting_reply", "received"} and not str(result["responseNote"]).strip():
        raise ValueError("Recorded dispatch/reply status requires responseNote; import does not send requests")
    for field in JSON_FIELDS:
        if not isinstance(item.get(field, []), list):
            raise ValueError(f"{field} must be a list")
    targets = []
    for target in item.get("targets", []):
        if not isinstance(target, dict) or set(target) - {"signature", "title", "dates", "url", "sourceId"}:
            raise ValueError("target must be an object with signature/title/dates/url and optional sourceId")
        normalized = {key: _text(target.get(key, ""), f"target.{key}") for key in ("signature", "title", "dates", "url")}
        if not any(normalized[key].strip() for key in ("signature", "title", "url")):
            raise ValueError("target requires a signature, title or URL")
        if normalized["url"]:
            normalized["url"] = _url(normalized["url"], "target.url")
        if target.get("sourceId") is not None:
            normalized["sourceId"] = _text(target["sourceId"], "target.sourceId", required=True)
        targets.append(normalized)
    result["targets"] = targets
    result["provenance"] = [_provenance(value, root) for value in item.get("provenance", [])]
    result["missingFields"] = [_text(value, "missingFields", required=True) for value in item.get("missingFields", [])]
    for field in ("createdAt", "updatedAt"):
        if field in item:
            result[field] = _timestamp(item[field], field)
    return result


def _decode(row) -> dict[str, object]:
    return {field: json.loads(row[column]) if field in JSON_FIELDS else row[column] for field, column in FIELDS.items()}


def import_archive_requests(root: Path, items: list[dict[str, object]]) -> dict[str, object]:
    """Atomically upsert curator records by stable supplied ID, without delivery.

    No initialization, migration, seed, attachment read or network request occurs.
    Existing createdAt is retained; an identical import retains both timestamps.
    Optional target sourceId refers to source_items.id, never an inferred match.
    """
    if not isinstance(items, list):
        raise ValueError("items must be a list")
    root = Path(root).resolve()
    prepared = [_validate_item(root, item) for item in items]
    ids = [item["id"] for item in prepared]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate archive request IDs in import")
    with connect(root) as connection:
        _require_registry(connection)
        connection.execute("BEGIN IMMEDIATE")
        for item in prepared:
            if item["caseId"] is not None and connection.execute(
                "SELECT 1 FROM research_cases WHERE id=?", (item["caseId"],)
            ).fetchone() is None:
                raise ValueError(f"Unknown caseId: {item['caseId']}")
            for target in item["targets"]:
                if target.get("sourceId") and connection.execute(
                    "SELECT 1 FROM source_items WHERE id=?", (target["sourceId"],)
                ).fetchone() is None:
                    raise ValueError(f"Unknown target.sourceId: {target['sourceId']}")
        now = utc_now()
        for item in prepared:
            previous = connection.execute("SELECT * FROM archive_requests WHERE id=?", (item["id"],)).fetchone()
            old = _decode(previous) if previous is not None else None
            if old is not None and all(item[field] == old[field] for field in FIELDS if field not in {"createdAt", "updatedAt"}):
                continue
            item["createdAt"] = old["createdAt"] if old is not None else item.get("createdAt", now)
            item["updatedAt"] = item.get("updatedAt", now)
            if datetime.fromisoformat(str(item["updatedAt"]).replace("Z", "+00:00")) < datetime.fromisoformat(str(item["createdAt"]).replace("Z", "+00:00")):
                raise ValueError("updatedAt must not precede createdAt")
            values = [json.dumps(item[field], ensure_ascii=False, sort_keys=True) if field in JSON_FIELDS else item[field] for field in FIELDS]
            columns = list(FIELDS.values())
            assignments = ",".join(f"{column}=excluded.{column}" for column in columns if column not in {"id", "created_at"})
            connection.execute(
                f"INSERT INTO archive_requests({','.join(columns)}) VALUES({','.join('?' for _ in columns)}) ON CONFLICT(id) DO UPDATE SET {assignments}",
                values,
            )
    return {"imported": len(prepared), "ids": ids}


def list_archive_requests(root: Path, include_strict_private: bool = False) -> dict[str, object]:
    """Read private registry records in ascending priority/ID order; never seed."""
    if type(include_strict_private) is not bool:
        raise ValueError("include_strict_private must be a boolean")
    with configured_database_backend(Path(root)).connect(readonly=True) as connection:
        _require_registry(connection)
        rows = connection.execute(
            "SELECT * FROM archive_requests WHERE (? OR privacy != 'strict_private') ORDER BY priority,id",
            (include_strict_private,),
        ).fetchall()
    return {"requests": [_decode(row) for row in rows]}


__all__ = ["RegistryUnavailableError", "import_archive_requests", "list_archive_requests"]
