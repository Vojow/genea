"""Small offline selection check. A catalogue is a lead, never identity proof."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name}: expected nonempty text")
    return value


def _references(value: object, name: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{name}: expected reference array")
    for reference in value:
        _text(reference, name)
    return value


def validate_discovery_plan(payload: object, *, source_id: str | None = None,
                            lead_id: str | None = None) -> dict:
    """Check selection and reuse, not claims about search coverage or RAW."""
    if not isinstance(payload, dict) or type(payload.get("schemaVersion")) is not int or payload["schemaVersion"] != 1:
        raise ValueError("plan: expected object with schemaVersion 1")
    _text(payload.get("question"), "question")
    scope = payload.get("scope")
    _text(scope.get("description") if isinstance(scope, dict) else scope, "scope")
    _text(payload.get("searchSummary", payload.get("routeReason")), "searchSummary")
    rows = payload.get("leads")
    if not isinstance(rows, list):
        raise ValueError("leads: expected array")
    leads, documents = {}, {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("leads: expected objects")
        identifier = _text(row.get("id"), "lead.id")
        if identifier != identifier.strip() or identifier in leads:
            raise ValueError("lead.id: duplicate or whitespace-padded ID")
        leads[identifier] = row
        locator = _text(row.get("locator"), f"{identifier}.locator")
        provenance = row.get("provenance")
        if not isinstance(provenance, dict):
            raise ValueError(f"{identifier}.provenance: expected URL or backingIds")
        backing = _references(provenance.get("backingIds", []), "provenance.backingIds")
        url = provenance.get("url")
        if url is not None:
            try:
                parsed = urlsplit(_text(url, "provenance.url"))
                valid = parsed.scheme in {"http", "https"} and bool(parsed.hostname) and not parsed.username and not parsed.password
                parsed.port
            except (ValueError, TypeError):
                valid = False
            if not valid or any(character.isspace() for character in url):
                raise ValueError("provenance.url: expected HTTP(S) URL without credentials")
        elif not backing:
            raise ValueError("provenance: URL or backing ID required")
        if "sourceId" in row:
            _text(row["sourceId"], f"{identifier}.sourceId")
        disposition = row.get("disposition", "deferred")
        if disposition not in ("selected", "deferred", "reuse"):
            raise ValueError(f"{identifier}.disposition: expected selected, deferred or reuse")
        status = row.get("readingStatus", "not_read")
        if status not in ("not_read", "needs_reread", "reusable"):
            raise ValueError(f"{identifier}.readingStatus: invalid status")
        references = _references(row.get("existingWorkRefs", []), "existingWorkRefs")
        if status in ("needs_reread", "reusable") and not references:
            raise ValueError(f"{identifier}: existing reading requires existingWorkRefs")
        if status == "needs_reread":
            _text(row.get("rereadReason"), f"{identifier}.rereadReason")
        if disposition == "selected":
            _text(row.get("reason"), f"{identifier}.reason")
            if "readingStatus" not in row or status == "reusable":
                raise ValueError(f"{identifier}: selected lead needs a new reading or justified reread; reusable cannot launch")
        if disposition == "reuse" and status != "reusable":
            raise ValueError(f"{identifier}: reuse requires reusable readingStatus")
        # A page can contain several acts. Compare exact locators, never names.
        repository = provenance.get("repository")
        key = (("source", row["sourceId"], locator) if "sourceId" in row
               else ("repository", repository, locator) if isinstance(repository, str) and repository else None)
        if key is not None:
            documents.setdefault(key, []).append(row)
    for occurrences in documents.values():
        statuses = {row.get("readingStatus", "not_read") for row in occurrences}
        if len(statuses) > 1:
            selected = [row for row in occurrences if row.get("disposition") == "selected"]
            if selected and any(row.get("readingStatus") != "needs_reread" for row in selected):
                raise ValueError("contradictory readingStatus for exact document; reuse or justify reread")
    bound = None
    if source_id is not None or lead_id is not None:
        _text(source_id, "source_id binding")
        _text(lead_id, "lead_id binding")
        if lead_id not in leads:
            raise ValueError("lead_id: unknown lead")
        bound = leads[lead_id]
        if bound.get("disposition") != "selected":
            raise ValueError("lead_id: reading requires selected lead; reuse/deferred cannot launch")
        if bound.get("sourceId") != source_id:
            raise ValueError("selected sourceId does not match source_id")
    return {
        "schemaVersion": 1, "status": "validated", "leadCount": len(leads),
        "selectedLeadIds": [key for key, row in leads.items() if row.get("disposition") == "selected"],
        "deferredLeadIds": [key for key, row in leads.items() if row.get("disposition", "deferred") == "deferred"],
        "reuseLeadIds": [key for key, row in leads.items() if row.get("disposition") == "reuse"],
        "boundLeadId": lead_id if bound is not None else None, "boundSourceId": source_id,
        "identityDecision": None, "negativeEvidenceEstablished": False,
    }


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"Duplicate JSON key: {key}")
        value[key] = item
    return value


def _invalid_constant(value: str):
    raise ValueError(f"Nonfinite JSON value: {value}")


def load_discovery_plan(path: Path, *, source_id: str | None = None,
                        lead_id: str | None = None) -> dict:
    """Read once, validate without mutation, preserve the exact input hash."""
    try:
        raw = Path(path).read_bytes()
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot load discovery plan {path}: {error}") from error
    result = validate_discovery_plan(payload, source_id=source_id, lead_id=lead_id)
    result["inputSha256"] = hashlib.sha256(raw).hexdigest()
    return result
