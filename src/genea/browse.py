from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from .db import connect, initialize
from .research import require_tables
from .workspace import research_root, stable_id, utc_now


SESSION_STATUSES = {
    "planned", "in_progress", "paused", "submitted", "completed",
    "closed_partial", "review_required", "invalidated",
}
RANGE_STATUSES = {
    "planned", "assigned", "in_progress", "paused", "submitted",
    "accepted", "accepted_partial", "rejected", "blocked", "invalidated",
}
ISSUE_FIELDS = {
    "whole_entry", "given_name", "surname", "alias", "patronymic",
    "toponym", "jurisdiction", "date", "age", "sex", "occupation",
    "social_status", "relationship", "event_role", "religion",
    "act_number", "house_number", "other_number", "marginal_note",
    "abbreviation", "signature", "language", "hand_attribution", "other",
}
ISSUE_KINDS = {
    "unreadable", "partially_readable", "ambiguous_grapheme",
    "ambiguous_word_boundary", "ambiguous_abbreviation",
    "ambiguous_expansion", "ambiguous_numeral", "ambiguous_language",
    "ambiguous_hand", "overwritten", "crossed_out", "corrected_in_source",
    "missing_text", "conflicting_readings", "context_mismatch",
    "unexpected_formula", "uncertain_locator", "ocr_disagreement",
    "index_source_disagreement",
}
CAUSE_CODES = {
    "low_resolution", "out_of_focus", "motion_blur", "compression_artifacts",
    "low_contrast", "overexposed", "underexposed", "color_loss", "cropped",
    "gutter_occlusion", "page_curvature", "show_through", "bleed_through",
    "stain", "fold", "tear", "missing_fragment", "ink_faded", "ink_spread",
    "ambiguous_allograph", "ligature", "nonstandard_abbreviation",
    "archaic_orthography", "phonetic_spelling", "latinization",
    "cyrillic_transcription", "language_switch", "similar_letter_forms",
    "similar_numeral_forms", "word_joining", "word_splitting", "overwriting",
    "correction_by_scribe", "multiple_hands", "hand_change", "writer_fatigue",
    "unstable_ductus", "unfamiliar_formula", "insufficient_context",
    "missing_adjacent_page", "unknown_place", "unknown_person",
    "unknown_occupation", "conflicting_index", "conflicting_duplicate",
    "circular_inference_risk",
}
TRIGGER_TYPES = {
    "better_scan_available", "alternate_scan_available", "color_scan_available",
    "missing_area_recovered", "new_ocr_engine", "new_ocr_version",
    "new_llm_model", "new_transcription_method", "hand_profile_ready",
    "hand_profile_expanded", "disputed_grapheme_profiled", "parallel_word_found",
    "parallel_entry_found", "adjacent_page_available", "duplicate_record_found",
    "external_index_found", "new_place_dictionary_entry",
    "new_occupation_dictionary_entry", "new_family_context", "new_conflict",
    "expert_review_available", "peer_review_requested", "user_request",
    "quality_audit",
}
HANDWRITING_SCOPE_ROLES = {"anchor", "sample", "context"}
HANDWRITING_SCOPE_STATUSES = {"candidate", "accepted", "excluded"}
REVISIT_PRIORITY_ORDER = {
    "r0_none": 0, "r1_low": 1, "r2_medium": 2, "r3_high": 3, "r4_critical": 4,
}
RENDITION_CLASSES = {
    "thumbnail", "viewer", "medium", "max", "original", "download", "iiif", "pdf", "other",
}
RENDITION_ACQUISITION_METHODS = {
    "browser_view", "repository_download", "iiif", "api", "pdf", "local_file", "other",
}
RENDITION_ACCESS_STATUSES = {"available", "restricted", "blocked", "missing"}
RENDITION_QUALITIES = {
    "q0_unusable", "q1_poor", "q2_usable", "q3_good", "q4_excellent",
}
RENDITION_QUALITY_ORDER = {
    "q0_unusable": 0,
    "q1_poor": 1,
    "q2_usable": 2,
    "q3_good": 3,
    "q4_excellent": 4,
}
RENDITION_GATE_STATUSES = {"passed", "provisional", "blocked"}
REQUIRED_TABLES = {
    "browse_sessions", "browse_ranges", "browse_checkpoints", "browse_issues",
    "browse_findings", "browse_revisit_triggers", "browse_issue_assessments",
    "browse_quality_reviews", "browse_inventory_units",
    "browse_renditions", "browse_rendition_gate_reviews",
    "handwriting_profiles", "handwriting_profile_sources", "handwriting_samples",
}


def _required(value: str, label: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{label} nie może być puste")
    return cleaned


def _validate(value: str, allowed: set[str], label: str) -> str:
    if value not in allowed:
        raise ValueError(f"{label} musi być jednym z: {', '.join(sorted(allowed))}")
    return value


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _loads(value: str | None, fallback):
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _fingerprint(parts: Iterable[object]) -> str:
    payload = _json(list(parts)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def browse_inventory_hash(units: list[dict]) -> str:
    """Return the canonical hash used by ``browse-inventory-import``.

    Keep this public so local, read-only preparation tools can seal a manifest
    before it is imported without writing to the research database.
    """

    canonical = [
        {
            "ordinal": row["ordinal"],
            "stable_locator": row["stable_locator"],
            "page_label": row.get("page_label"),
            "act_label": row.get("act_label"),
            "content_sha256": row.get("content_sha256"),
            "availability": row["availability"],
            "duplicate_of_ordinal": row.get("duplicate_of_ordinal"),
            "notes": row.get("notes"),
        }
        for row in sorted(units, key=lambda item: item["ordinal"])
    ]
    return hashlib.sha256(_json(canonical).encode("utf-8")).hexdigest()


# Internal compatibility alias for the existing browse-session implementation.
_inventory_hash = browse_inventory_hash


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Znacznik czasu lease musi zawierać strefę czasową")
    return parsed.astimezone(timezone.utc)


def _audit(
    connection: sqlite3.Connection,
    *,
    actor: str,
    action: str,
    object_type: str,
    object_id: str,
    before: object | None,
    after: object | None,
    rationale: str | None = None,
) -> None:
    connection.execute(
        """INSERT INTO audit_log(
               occurred_at, actor, action, object_type, object_id,
               before_json, after_json, rationale
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            utc_now(), actor, action, object_type, object_id,
            _json(before) if before is not None else None,
            _json(after) if after is not None else None,
            rationale,
        ),
    )


def _ready(root: Path) -> None:
    initialize(root)
    with connect(root) as connection:
        require_tables(connection, REQUIRED_TABLES)


def _require_session_mutable(connection: sqlite3.Connection, session_id: str) -> dict:
    row = connection.execute("SELECT * FROM browse_sessions WHERE id=?", (session_id,)).fetchone()
    if row is None:
        raise LookupError(f"Nie znaleziono sesji: {session_id}")
    item = dict(row)
    if item["status"] in {"completed", "closed_partial", "invalidated"}:
        raise RuntimeError("Sesja jest zamknięta; wymaga jawnego ponownego otwarcia")
    return item


def _validate_session_links(
    connection: sqlite3.Connection,
    session_id: str,
    *,
    range_id: str | None = None,
    checkpoint_id: str | None = None,
    finding_id: str | None = None,
    handwriting_profile_id: str | None = None,
) -> None:
    session = connection.execute(
        "SELECT source_item_id FROM browse_sessions WHERE id=?", (session_id,)
    ).fetchone()
    if session is None:
        raise LookupError(f"Nie znaleziono sesji: {session_id}")
    if range_id:
        row = connection.execute(
            "SELECT session_id FROM browse_ranges WHERE id=?", (range_id,)
        ).fetchone()
        if row is None or row["session_id"] != session_id:
            raise ValueError("range_id nie należy do tej sesji")
    if checkpoint_id:
        row = connection.execute(
            "SELECT session_id,range_id FROM browse_checkpoints WHERE id=?", (checkpoint_id,)
        ).fetchone()
        if row is None or row["session_id"] != session_id or (
            range_id and row["range_id"] != range_id
        ):
            raise ValueError("checkpoint_id nie należy do tej sesji lub zakresu")
    if finding_id:
        row = connection.execute(
            "SELECT session_id FROM browse_findings WHERE id=?", (finding_id,)
        ).fetchone()
        if row is None or row["session_id"] != session_id:
            raise ValueError("finding_id nie należy do tej sesji")
    if handwriting_profile_id:
        row = connection.execute(
            "SELECT source_item_id FROM handwriting_profiles WHERE id=?",
            (handwriting_profile_id,),
        ).fetchone()
        if row is None or row["source_item_id"] != session["source_item_id"]:
            raise ValueError("Profil ręki nie dotyczy źródła tej sesji")


def create_browse_session(
    root: Path,
    case_id: str,
    source_item_id: str,
    title: str,
    *,
    created_by: str,
    unit_type: str,
    ordinal_kind: str,
    arrangement: str,
    method: str,
    scope_text: str,
    search_terms: list[str] | None = None,
    question_id: str | None = None,
    place_study_id: str | None = None,
    artifact_id: str | None = None,
    canonical_url: str | None = None,
    source_version: str | None = None,
    manifest_sha256: str | None = None,
    expected_start_ordinal: int | None = None,
    expected_end_ordinal: int | None = None,
    privacy_classification: str = "public_historical",
) -> dict:
    _ready(root)
    title = _required(title, "title")
    actor = _required(created_by, "created_by")
    scope_text = _required(scope_text, "scope_text")
    _validate(unit_type, {"index", "register", "volume", "viewer", "collection", "other"}, "unit_type")
    _validate(ordinal_kind, {"scan", "page", "folio", "act", "entry", "row", "household", "custom"}, "ordinal_kind")
    _validate(arrangement, {"chronological", "alphabetical", "act_number", "household", "geographical", "mixed", "unknown"}, "arrangement")
    _validate(method, {"manual", "ocr", "llm", "hybrid", "index_lookup"}, "method")
    _validate(privacy_classification, {"public_historical", "deceased", "living", "restricted"}, "privacy_classification")
    if expected_start_ordinal is not None and expected_start_ordinal < 1:
        raise ValueError("expected_start_ordinal musi być >= 1")
    if expected_end_ordinal is not None and expected_end_ordinal < 1:
        raise ValueError("expected_end_ordinal musi być >= 1")
    if expected_start_ordinal is not None and expected_end_ordinal is not None and expected_end_ordinal < expected_start_ordinal:
        raise ValueError("expected_end_ordinal nie może poprzedzać początku")
    terms = sorted({item.strip() for item in (search_terms or []) if item.strip()}, key=str.casefold)
    query_fingerprint = _fingerprint(
        (case_id, question_id, source_item_id, scope_text, terms, method, ordinal_kind)
    )
    now = utc_now()
    item = {
        "id": stable_id("browse_session", case_id, source_item_id, query_fingerprint),
        "case_id": case_id,
        "question_id": question_id,
        "source_item_id": source_item_id,
        "artifact_id": artifact_id,
        "place_study_id": place_study_id,
        "title": title,
        "unit_type": unit_type,
        "ordinal_kind": ordinal_kind,
        "arrangement": arrangement,
        "method": method,
        "scope_text": scope_text,
        "search_terms_json": _json(terms),
        "query_fingerprint": query_fingerprint,
        "canonical_url": canonical_url,
        "source_version": source_version,
        "manifest_sha256": manifest_sha256,
        "expected_start_ordinal": expected_start_ordinal,
        "expected_end_ordinal": expected_end_ordinal,
        "expected_unit_count": (
            expected_end_ordinal - expected_start_ordinal + 1
            if expected_start_ordinal is not None and expected_end_ordinal is not None
            else None
        ),
        "rendition_gate_required": 1,
        "status": "planned",
        "privacy_classification": privacy_classification,
        "created_by": actor,
        "created_at": now,
        "updated_at": now,
        "completed_at": None,
    }
    with connect(root) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """INSERT INTO browse_sessions(
                   id, case_id, question_id, source_item_id, artifact_id, place_study_id,
                   title, unit_type, ordinal_kind, arrangement, method, scope_text, search_terms_json,
                   query_fingerprint, canonical_url, source_version, manifest_sha256,
                   expected_start_ordinal, expected_end_ordinal, expected_unit_count,
                   rendition_gate_required, status, privacy_classification, created_by, created_at, updated_at,
                   completed_at
               ) VALUES (
                   :id, :case_id, :question_id, :source_item_id, :artifact_id, :place_study_id,
                   :title, :unit_type, :ordinal_kind, :arrangement, :method, :scope_text,
                   :search_terms_json, :query_fingerprint, :canonical_url,
                   :source_version, :manifest_sha256, :expected_start_ordinal,
                   :expected_end_ordinal, :expected_unit_count,
                   :rendition_gate_required, :status,
                   :privacy_classification, :created_by, :created_at, :updated_at,
                   :completed_at
               )
               ON CONFLICT(id) DO NOTHING""",
            item,
        )
        stored = dict(connection.execute("SELECT * FROM browse_sessions WHERE id=?", (item["id"],)).fetchone())
        if stored["query_fingerprint"] != query_fingerprint:
            raise RuntimeError("Konflikt identyfikatora sesji przeglądania")
        _audit(connection, actor=actor, action="browse_session_create", object_type="browse_session", object_id=item["id"], before=None, after=stored, rationale=scope_text)
    stored["search_terms"] = _loads(stored.pop("search_terms_json"), [])
    return stored


def add_browse_rendition(
    root: Path,
    session_id: str,
    locator: str,
    *,
    rendition_class: str,
    acquisition_method: str,
    access_status: str,
    information_quality: str,
    created_by: str,
    ordinal: int | None = None,
    natural_width_px: int | None = None,
    natural_height_px: int | None = None,
    byte_size: int | None = None,
    media_type: str | None = None,
    content_sha256: str | None = None,
    is_upscaled: bool | None = None,
    legal_access_checked: bool = False,
    notes: str | None = None,
) -> dict:
    """Append one observed reproduction variant without treating it as evidence."""

    _ready(root)
    locator = _required(locator, "locator")
    actor = _required(created_by, "created_by")
    _validate(rendition_class, RENDITION_CLASSES, "rendition_class")
    _validate(acquisition_method, RENDITION_ACQUISITION_METHODS, "acquisition_method")
    _validate(access_status, RENDITION_ACCESS_STATUSES, "access_status")
    _validate(information_quality, RENDITION_QUALITIES, "information_quality")
    for value, label in (
        (ordinal, "ordinal"),
        (natural_width_px, "natural_width_px"),
        (natural_height_px, "natural_height_px"),
    ):
        if value is not None and value < 1:
            raise ValueError(f"{label} musi być >= 1")
    if byte_size is not None and byte_size < 0:
        raise ValueError("byte_size musi być >= 0")
    if content_sha256 is not None:
        content_sha256 = content_sha256.casefold()
        if len(content_sha256) != 64 or any(character not in "0123456789abcdef" for character in content_sha256):
            raise ValueError("content_sha256 musi być 64-znakowym SHA-256")
    now = utc_now()
    item = {
        "id": stable_id(
            "browse_rendition", session_id, str(ordinal or ""), locator,
            rendition_class, content_sha256 or "", now,
        ),
        "session_id": session_id,
        "ordinal": ordinal,
        "locator": locator,
        "rendition_class": rendition_class,
        "natural_width_px": natural_width_px,
        "natural_height_px": natural_height_px,
        "byte_size": byte_size,
        "media_type": media_type,
        "content_sha256": content_sha256,
        "acquisition_method": acquisition_method,
        "access_status": access_status,
        "information_quality": information_quality,
        "is_upscaled": None if is_upscaled is None else int(is_upscaled),
        "legal_access_checked": int(legal_access_checked),
        "notes": notes,
        "created_by": actor,
        "created_at": now,
    }
    with connect(root) as connection:
        connection.execute("BEGIN IMMEDIATE")
        _require_session_mutable(connection, session_id)
        connection.execute(
            """INSERT INTO browse_renditions(
                   id,session_id,ordinal,locator,rendition_class,natural_width_px,
                   natural_height_px,byte_size,media_type,content_sha256,
                   acquisition_method,access_status,information_quality,is_upscaled,
                   legal_access_checked,notes,created_by,created_at
               ) VALUES (
                   :id,:session_id,:ordinal,:locator,:rendition_class,:natural_width_px,
                   :natural_height_px,:byte_size,:media_type,:content_sha256,
                   :acquisition_method,:access_status,:information_quality,:is_upscaled,
                   :legal_access_checked,:notes,:created_by,:created_at
               )""",
            item,
        )
        stored = dict(connection.execute(
            "SELECT * FROM browse_renditions WHERE id=?", (item["id"],)
        ).fetchone())
        _audit(
            connection, actor=actor, action="browse_rendition_add",
            object_type="browse_rendition", object_id=item["id"],
            before=None, after=stored, rationale=notes,
        )
    return stored


def _latest_rendition_gate(connection: sqlite3.Connection, session_id: str) -> dict | None:
    row = connection.execute(
        """SELECT * FROM browse_rendition_gate_reviews
           WHERE session_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1""",
        (session_id,),
    ).fetchone()
    return dict(row) if row else None


def review_browse_rendition_gate(
    root: Path,
    session_id: str,
    status: str,
    *,
    reviewed_by: str,
    rationale: str,
    selected_rendition_id: str | None = None,
    representative_ordinal: int | None = None,
    comparison: dict | None = None,
) -> dict:
    """Record a curator decision on the best legally available reproduction."""

    _ready(root)
    _validate(status, RENDITION_GATE_STATUSES, "status")
    actor = _required(reviewed_by, "reviewed_by")
    rationale = _required(rationale, "rationale")
    if representative_ordinal is not None and representative_ordinal < 1:
        raise ValueError("representative_ordinal musi być >= 1")
    if status != "blocked" and not selected_rendition_id:
        raise ValueError("passed/provisional wymaga selected_rendition_id")
    now = utc_now()
    with connect(root) as connection:
        connection.execute("BEGIN IMMEDIATE")
        session = _require_session_mutable(connection, session_id)
        selected = None
        if selected_rendition_id:
            selected_row = connection.execute(
                "SELECT * FROM browse_renditions WHERE id=? AND session_id=?",
                (selected_rendition_id, session_id),
            ).fetchone()
            if selected_row is None:
                raise ValueError("Wybrany wariant nie należy do tej sesji")
            selected = dict(selected_row)
        if status == "passed":
            if selected["access_status"] != "available":
                raise ValueError("Bramka passed wymaga dostępnego wariantu")
            if not selected["legal_access_checked"]:
                raise ValueError("Bramka passed wymaga potwierdzenia legalnego dostępu")
            if selected["is_upscaled"] == 1:
                raise ValueError("Wariant oznaczony jako sztucznie powiększony nie może przejść bramki")
            eligible = [dict(row) for row in connection.execute(
                """SELECT * FROM browse_renditions
                   WHERE session_id=? AND access_status='available'
                     AND legal_access_checked=1 AND COALESCE(is_upscaled,0)=0""",
                (session_id,),
            )]
            best_quality = max(
                (RENDITION_QUALITY_ORDER[row["information_quality"]] for row in eligible),
                default=-1,
            )
            if RENDITION_QUALITY_ORDER[selected["information_quality"]] < best_quality:
                raise ValueError("Wybrany wariant nie ma najwyższej zarejestrowanej jakości informacyjnej")
        item = {
            "id": stable_id(
                "browse_rendition_gate", session_id, status,
                selected_rendition_id or "", now,
            ),
            "session_id": session_id,
            "selected_rendition_id": selected_rendition_id,
            "status": status,
            "representative_ordinal": representative_ordinal,
            "comparison_json": _json(comparison or {}),
            "rationale": rationale,
            "reviewed_by": actor,
            "created_at": now,
        }
        connection.execute(
            """INSERT INTO browse_rendition_gate_reviews(
                   id,session_id,selected_rendition_id,status,representative_ordinal,
                   comparison_json,rationale,reviewed_by,created_at
               ) VALUES (
                   :id,:session_id,:selected_rendition_id,:status,:representative_ordinal,
                   :comparison_json,:rationale,:reviewed_by,:created_at
               )""",
            item,
        )
        connection.execute(
            "UPDATE browse_sessions SET rendition_gate_required=1,updated_at=? WHERE id=?",
            (now, session_id),
        )
        stored = dict(connection.execute(
            "SELECT * FROM browse_rendition_gate_reviews WHERE id=?", (item["id"],)
        ).fetchone())
        _audit(
            connection, actor=actor, action="browse_rendition_gate_review",
            object_type="browse_session", object_id=session_id,
            before={"rendition_gate_required": session["rendition_gate_required"]},
            after=stored, rationale=rationale,
        )
    stored["comparison"] = _loads(stored.pop("comparison_json"), {})
    return stored


def link_handwriting_profile_source(
    root: Path,
    profile_id: str,
    source_item_id: str,
    *,
    scope_role: str,
    status: str,
    rationale: str,
    linked_by: str,
) -> dict:
    """Record a reviewed source-scope decision for a handwriting profile."""

    _ready(root)
    _validate(scope_role, HANDWRITING_SCOPE_ROLES, "scope_role")
    _validate(status, HANDWRITING_SCOPE_STATUSES, "status")
    reason = _required(rationale, "rationale")
    actor = _required(linked_by, "linked_by")
    now = utc_now()
    with connect(root) as connection:
        connection.execute("BEGIN IMMEDIATE")
        profile = connection.execute(
            "SELECT source_item_id FROM handwriting_profiles WHERE id=?", (profile_id,)
        ).fetchone()
        if profile is None:
            raise LookupError(f"Nie znaleziono profilu ręki: {profile_id}")
        if connection.execute(
            "SELECT 1 FROM source_items WHERE id=?", (source_item_id,)
        ).fetchone() is None:
            raise LookupError(f"Nie znaleziono źródła: {source_item_id}")
        is_anchor = profile["source_item_id"] == source_item_id
        if is_anchor and (scope_role != "anchor" or status != "accepted"):
            raise ValueError("Źródło kotwiczące musi zachować rolę anchor i status accepted")
        if not is_anchor and scope_role == "anchor":
            raise ValueError("Profil może mieć tylko jedno źródło kotwiczące")
        previous = connection.execute(
            """SELECT * FROM handwriting_profile_sources
               WHERE profile_id=? AND source_item_id=?""",
            (profile_id, source_item_id),
        ).fetchone()
        before = dict(previous) if previous else None
        connection.execute(
            """INSERT INTO handwriting_profile_sources(
                   profile_id, source_item_id, scope_role, status, rationale,
                   linked_by, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(profile_id, source_item_id) DO UPDATE SET
                   scope_role=excluded.scope_role, status=excluded.status,
                   rationale=excluded.rationale, linked_by=excluded.linked_by,
                   updated_at=excluded.updated_at""",
            (profile_id, source_item_id, scope_role, status, reason, actor, now, now),
        )
        stored = dict(connection.execute(
            """SELECT * FROM handwriting_profile_sources
               WHERE profile_id=? AND source_item_id=?""",
            (profile_id, source_item_id),
        ).fetchone())
        _audit(
            connection, actor=actor,
            action="handwriting_profile_source_update" if before else "handwriting_profile_source_add",
            object_type="handwriting_profile_source",
            object_id=f"{profile_id}:{source_item_id}",
            before=before, after=stored, rationale=reason,
        )
    return stored


def plan_browse_range(
    root: Path,
    session_id: str,
    start_ordinal: int,
    end_ordinal: int,
    *,
    actor: str,
    assigned_to: str | None = None,
    range_kind: str = "primary",
    start_locator: str | None = None,
    end_locator: str | None = None,
    start_page_label: str | None = None,
    end_page_label: str | None = None,
    start_act_label: str | None = None,
    end_act_label: str | None = None,
    notes: str | None = None,
) -> dict:
    _ready(root)
    actor = _required(actor, "actor")
    _validate(range_kind, {"primary", "qa_overlap"}, "range_kind")
    if start_ordinal < 1 or end_ordinal < start_ordinal:
        raise ValueError("Zakres musi mieć 1 <= start_ordinal <= end_ordinal")
    now = utc_now()
    item = {
        "id": stable_id("browse_range", session_id, range_kind, str(start_ordinal), str(end_ordinal)),
        "session_id": session_id,
        "start_ordinal": start_ordinal,
        "end_ordinal": end_ordinal,
        "start_locator": start_locator,
        "end_locator": end_locator,
        "start_page_label": start_page_label,
        "end_page_label": end_page_label,
        "start_act_label": start_act_label,
        "end_act_label": end_act_label,
        "range_kind": range_kind,
        "status": "assigned" if assigned_to else "planned",
        "assigned_to": assigned_to,
        "agent_run_id": None,
        "lease_token": None,
        "lease_expires_at": None,
        "revision": 0,
        "notes": notes,
        "created_at": now,
        "updated_at": now,
    }
    with connect(root) as connection:
        connection.execute("BEGIN IMMEDIATE")
        _require_session_mutable(connection, session_id)
        connection.execute(
            """INSERT INTO browse_ranges(
                   id, session_id, start_ordinal, end_ordinal, start_locator, end_locator,
                   start_page_label, end_page_label, start_act_label, end_act_label,
                   range_kind, status, assigned_to, agent_run_id, lease_token,
                   lease_expires_at, revision, notes, created_at, updated_at
               ) VALUES (
                   :id, :session_id, :start_ordinal, :end_ordinal, :start_locator,
                   :end_locator, :start_page_label, :end_page_label, :start_act_label,
                   :end_act_label, :range_kind, :status, :assigned_to, :agent_run_id,
                   :lease_token, :lease_expires_at, :revision, :notes, :created_at,
                   :updated_at
               )""",
            item,
        )
        connection.execute("UPDATE browse_sessions SET status='in_progress', updated_at=? WHERE id=? AND status='planned'", (now, session_id))
        _audit(connection, actor=actor, action="browse_range_plan", object_type="browse_range", object_id=item["id"], before=None, after=item, rationale=notes)
    return item


def record_browse_inventory_units(
    root: Path,
    session_id: str,
    units: list[dict],
    *,
    actor: str,
) -> dict:
    """Record the manifest behind the session hash, one stable unit at a time."""

    _ready(root)
    actor = _required(actor, "actor")
    if not units:
        raise ValueError("Manifest jednostek nie może być pusty")
    now = utc_now()
    stored_units: list[dict] = []
    canonical_hash = None
    with connect(root) as connection:
        connection.execute("BEGIN IMMEDIATE")
        session = _require_session_mutable(connection, session_id)
        for raw in units:
            if not isinstance(raw, dict):
                raise ValueError("Każda jednostka manifestu musi być obiektem JSON")
            ordinal = raw.get("ordinal")
            if not isinstance(ordinal, int) or ordinal < 1:
                raise ValueError("ordinal jednostki musi być liczbą całkowitą >= 1")
            if (
                session["expected_start_ordinal"] is not None
                and ordinal < session["expected_start_ordinal"]
            ) or (
                session["expected_end_ordinal"] is not None
                and ordinal > session["expected_end_ordinal"]
            ):
                raise ValueError(f"Ordinal {ordinal} wypada poza zakresem sesji")
            locator = _required(str(raw.get("stable_locator") or ""), "stable_locator")
            availability = str(raw.get("availability") or "available")
            _validate(
                availability,
                {"available", "missing", "duplicate", "restricted"},
                "availability",
            )
            duplicate_of = raw.get("duplicate_of_ordinal")
            if availability == "duplicate" and not isinstance(duplicate_of, int):
                raise ValueError("Jednostka duplicate wymaga duplicate_of_ordinal")
            item = {
                "id": stable_id("browse_inventory", session_id, str(ordinal)),
                "session_id": session_id,
                "ordinal": ordinal,
                "stable_locator": locator,
                "page_label": raw.get("page_label"),
                "act_label": raw.get("act_label"),
                "content_sha256": raw.get("content_sha256"),
                "availability": availability,
                "duplicate_of_ordinal": duplicate_of,
                "notes": raw.get("notes"),
                "created_by": actor,
                "created_at": now,
            }
            previous = connection.execute(
                "SELECT * FROM browse_inventory_units WHERE session_id=? AND ordinal=?",
                (session_id, ordinal),
            ).fetchone()
            before = dict(previous) if previous else None
            connection.execute(
                """INSERT INTO browse_inventory_units(
                       id,session_id,ordinal,stable_locator,page_label,act_label,
                       content_sha256,availability,duplicate_of_ordinal,notes,
                       created_by,created_at
                   ) VALUES (
                       :id,:session_id,:ordinal,:stable_locator,:page_label,:act_label,
                       :content_sha256,:availability,:duplicate_of_ordinal,:notes,
                       :created_by,:created_at
                   ) ON CONFLICT(session_id,ordinal) DO UPDATE SET
                       stable_locator=excluded.stable_locator,
                       page_label=excluded.page_label,
                       act_label=excluded.act_label,
                       content_sha256=excluded.content_sha256,
                       availability=excluded.availability,
                       duplicate_of_ordinal=excluded.duplicate_of_ordinal,
                       notes=excluded.notes""",
                item,
            )
            stored = dict(connection.execute(
                "SELECT * FROM browse_inventory_units WHERE session_id=? AND ordinal=?",
                (session_id, ordinal),
            ).fetchone())
            _audit(
                connection, actor=actor,
                action="browse_inventory_update" if before else "browse_inventory_add",
                object_type="browse_inventory_unit", object_id=stored["id"],
                before=before, after=stored, rationale=stored.get("notes"),
            )
            stored_units.append(stored)
        all_units = [dict(row) for row in connection.execute(
            "SELECT * FROM browse_inventory_units WHERE session_id=? ORDER BY ordinal",
            (session_id,),
        )]
        expected = (
            set(range(session["expected_start_ordinal"], session["expected_end_ordinal"] + 1))
            if session["expected_start_ordinal"] is not None
            and session["expected_end_ordinal"] is not None
            else set()
        )
        if expected and {row["ordinal"] for row in all_units} == expected:
            canonical_hash = _inventory_hash(all_units)
            declared = session["manifest_sha256"]
            if declared and declared != canonical_hash:
                raise RuntimeError(
                    "manifest_sha256 sesji nie odpowiada kanonicznemu manifestowi jednostek"
                )
            if not declared:
                before_session = dict(session)
                connection.execute(
                    "UPDATE browse_sessions SET manifest_sha256=?,updated_at=? WHERE id=?",
                    (canonical_hash, now, session_id),
                )
                after_session = dict(connection.execute(
                    "SELECT * FROM browse_sessions WHERE id=?", (session_id,)
                ).fetchone())
                _audit(
                    connection, actor=actor, action="browse_manifest_seal",
                    object_type="browse_session", object_id=session_id,
                    before=before_session, after=after_session,
                    rationale="Kanoniczny SHA-256 pełnego manifestu jednostek",
                )
    return {
        "session_id": session_id,
        "recorded": len(stored_units),
        "canonical_manifest_sha256": canonical_hash,
        "units": stored_units,
    }


def claim_browse_range(
    root: Path,
    range_id: str,
    *,
    assigned_to: str,
    actor: str,
    agent_run_id: str | None = None,
    lease_expires_at: str | None = None,
    expected_revision: int | None = None,
) -> dict:
    _ready(root)
    assigned_to = _required(assigned_to, "assigned_to")
    actor = _required(actor, "actor")
    now = utc_now()
    lease_expires_at = lease_expires_at or (
        datetime.now(timezone.utc) + timedelta(hours=4)
    ).replace(microsecond=0).isoformat()
    if _parse_time(lease_expires_at) <= datetime.now(timezone.utc):
        raise ValueError("lease_expires_at musi wskazywać przyszłość")
    with connect(root) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM browse_ranges WHERE id=?", (range_id,)).fetchone()
        if row is None:
            raise LookupError(f"Nie znaleziono zakresu: {range_id}")
        before = dict(row)
        if expected_revision is not None and before["revision"] != expected_revision:
            raise RuntimeError(f"Zakres ma rewizję {before['revision']}, oczekiwano {expected_revision}")
        if before["status"] not in {"planned", "assigned", "paused"}:
            raise RuntimeError(f"Nie można przejąć zakresu w stanie {before['status']}")
        if before["range_kind"] == "primary":
            session = connection.execute(
                "SELECT rendition_gate_required FROM browse_sessions WHERE id=?",
                (before["session_id"],),
            ).fetchone()
            latest_gate = _latest_rendition_gate(connection, before["session_id"])
            if session["rendition_gate_required"] and (
                latest_gate is None or latest_gate["status"] != "passed"
            ):
                raise RuntimeError(
                    "Nie można rozpocząć głównego zakresu przed przejściem "
                    "bramki najlepszej dostępnej reprodukcji"
                )
        lease_token = uuid.uuid4().hex
        connection.execute(
            """UPDATE browse_ranges SET assigned_to=?, agent_run_id=?, lease_token=?,
                   lease_expires_at=?, status='in_progress', revision=revision+1,
                   updated_at=? WHERE id=?""",
            (assigned_to, agent_run_id, lease_token, lease_expires_at, now, range_id),
        )
        after = dict(connection.execute("SELECT * FROM browse_ranges WHERE id=?", (range_id,)).fetchone())
        _audit(connection, actor=actor, action="browse_range_claim", object_type="browse_range", object_id=range_id, before=before, after=after, rationale=f"assigned_to={assigned_to}")
    return after


def append_browse_checkpoint(
    root: Path,
    range_id: str,
    *,
    actor: str,
    checkpoint_status: str,
    last_completed_ordinal: int | None,
    next_ordinal: int | None,
    last_completed_locator: str | None = None,
    next_locator: str | None = None,
    last_page_label: str | None = None,
    last_act_label: str | None = None,
    position_fingerprint: dict | None = None,
    search_terms_snapshot: list[str] | None = None,
    method_snapshot: str | None = None,
    source_version: str | None = None,
    manifest_sha256: str | None = None,
    note: str | None = None,
    lease_token: str | None = None,
) -> dict:
    _ready(root)
    actor = _required(actor, "actor")
    _validate(checkpoint_status, {"progress", "paused", "submitted", "blocked", "handoff", "invalidated"}, "checkpoint_status")
    if last_completed_ordinal is not None and last_completed_ordinal < 1:
        raise ValueError("last_completed_ordinal musi być >= 1")
    if next_ordinal is not None and next_ordinal < 1:
        raise ValueError("next_ordinal musi być >= 1")
    now = utc_now()
    with connect(root) as connection:
        connection.execute("BEGIN IMMEDIATE")
        range_row = connection.execute(
            """SELECT r.*, s.source_version AS session_source_version,
                      s.manifest_sha256 AS session_manifest_sha256,
                      s.search_terms_json AS session_search_terms,
                      s.method AS session_method, s.status AS session_status
               FROM browse_ranges r JOIN browse_sessions s ON s.id=r.session_id
               WHERE r.id=?""",
            (range_id,),
        ).fetchone()
        if range_row is None:
            raise LookupError(f"Nie znaleziono zakresu: {range_id}")
        data = dict(range_row)
        if data["session_status"] in {"completed", "closed_partial", "invalidated"}:
            raise RuntimeError("Nie można dopisywać checkpointów do zamkniętej sesji")
        if data["status"] not in {"in_progress", "paused"}:
            raise RuntimeError(f"Nie można dopisać checkpointu w stanie zakresu {data['status']}")
        if not data["lease_token"] or lease_token != data["lease_token"]:
            raise RuntimeError("Nieprawidłowy lub nieaktualny lease_token")
        if actor != data["assigned_to"]:
            raise RuntimeError("Checkpoint może dopisać tylko aktualnie przypisany wykonawca")
        if data["lease_expires_at"] and _parse_time(data["lease_expires_at"]) <= datetime.now(timezone.utc):
            raise RuntimeError("Lease zakresu wygasł; zakres trzeba przejąć ponownie")
        for value, label in ((last_completed_ordinal, "last_completed_ordinal"), (next_ordinal, "next_ordinal")):
            if value is not None and not data["start_ordinal"] <= value <= data["end_ordinal"] + (1 if label == "next_ordinal" else 0):
                raise ValueError(f"{label} wypada poza przydzielonym zakresem")
        sequence = connection.execute("SELECT COALESCE(MAX(sequence_number),0)+1 FROM browse_checkpoints WHERE range_id=?", (range_id,)).fetchone()[0]
        previous = connection.execute("SELECT * FROM browse_checkpoints WHERE range_id=? ORDER BY sequence_number DESC LIMIT 1", (range_id,)).fetchone()
        if previous and last_completed_ordinal is not None:
            previous_last = previous["last_completed_ordinal"]
            if previous_last is not None and last_completed_ordinal < previous_last:
                raise RuntimeError("Checkpoint nie może cofać ostatniej ukończonej jednostki")
        if checkpoint_status in {"submitted", "handoff"}:
            if last_completed_ordinal is None or not last_completed_locator:
                raise ValueError("Checkpoint przekazania wymaga ukończonego ordinalu i lokalizatora")
            if not position_fingerprint:
                raise ValueError("Checkpoint przekazania wymaga position_fingerprint")
        item = {
            "id": stable_id("browse_checkpoint", range_id, str(sequence), now),
            "session_id": data["session_id"],
            "range_id": range_id,
            "supersedes_id": previous["id"] if previous else None,
            "sequence_number": sequence,
            "last_completed_ordinal": last_completed_ordinal,
            "last_completed_locator": last_completed_locator,
            "last_page_label": last_page_label,
            "last_act_label": last_act_label,
            "next_ordinal": next_ordinal,
            "next_locator": next_locator,
            "position_fingerprint_json": _json(position_fingerprint or {}),
            "search_terms_snapshot_json": _json(search_terms_snapshot if search_terms_snapshot is not None else _loads(data["session_search_terms"], [])),
            "method_snapshot": method_snapshot or data["session_method"],
            "source_version": source_version if source_version is not None else data["session_source_version"],
            "manifest_sha256": manifest_sha256 if manifest_sha256 is not None else data["session_manifest_sha256"],
            "checkpoint_status": checkpoint_status,
            "note": note,
            "actor": actor,
            "created_at": now,
        }
        if data["session_source_version"] and item["source_version"] != data["session_source_version"]:
            raise RuntimeError("Checkpoint dotyczy innej wersji źródła niż sesja")
        if data["session_manifest_sha256"] and item["manifest_sha256"] != data["session_manifest_sha256"]:
            raise RuntimeError("Checkpoint dotyczy innego manifestu niż sesja")
        connection.execute(
            """INSERT INTO browse_checkpoints(
                   id, session_id, range_id, supersedes_id, sequence_number,
                   last_completed_ordinal, last_completed_locator, last_page_label,
                   last_act_label, next_ordinal, next_locator,
                   position_fingerprint_json, search_terms_snapshot_json,
                   method_snapshot, source_version, manifest_sha256,
                   checkpoint_status, note, actor, created_at
               ) VALUES (
                   :id, :session_id, :range_id, :supersedes_id, :sequence_number,
                   :last_completed_ordinal, :last_completed_locator, :last_page_label,
                   :last_act_label, :next_ordinal, :next_locator,
                   :position_fingerprint_json, :search_terms_snapshot_json,
                   :method_snapshot, :source_version, :manifest_sha256,
                   :checkpoint_status, :note, :actor, :created_at
               )""",
            item,
        )
        next_status = {
            "progress": "in_progress", "paused": "paused", "handoff": "paused",
            "submitted": "submitted", "blocked": "blocked", "invalidated": "invalidated",
        }.get(checkpoint_status)
        if next_status:
            connection.execute("UPDATE browse_ranges SET status=?, revision=revision+1, updated_at=? WHERE id=?", (next_status, now, range_id))
        _audit(connection, actor=actor, action="browse_checkpoint_append", object_type="browse_checkpoint", object_id=item["id"], before=None, after=item, rationale=note)
    result = dict(item)
    result["position_fingerprint"] = _loads(result.pop("position_fingerprint_json"), {})
    result["search_terms_snapshot"] = _loads(result.pop("search_terms_snapshot_json"), [])
    return result


def review_browse_range(
    root: Path,
    range_id: str,
    decision: str,
    *,
    actor: str,
    rationale: str,
    expected_revision: int | None = None,
) -> dict:
    _ready(root)
    _validate(decision, {"accepted", "accepted_partial", "rejected"}, "decision")
    actor = _required(actor, "actor")
    rationale = _required(rationale, "rationale")
    now = utc_now()
    with connect(root) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM browse_ranges WHERE id=?", (range_id,)).fetchone()
        if row is None:
            raise LookupError(f"Nie znaleziono zakresu: {range_id}")
        before = dict(row)
        if expected_revision is not None and before["revision"] != expected_revision:
            raise RuntimeError(f"Zakres ma rewizję {before['revision']}, oczekiwano {expected_revision}")
        if before["status"] not in {"submitted", "blocked", "paused"}:
            raise RuntimeError("Zakres musi być przekazany lub jawnie częściowy przed recenzją")
        last_cp = connection.execute("SELECT * FROM browse_checkpoints WHERE range_id=? ORDER BY sequence_number DESC LIMIT 1", (range_id,)).fetchone()
        if last_cp is None:
            raise RuntimeError("Nie można zrecenzować zakresu bez checkpointu")
        session = connection.execute(
            "SELECT * FROM browse_sessions WHERE id=?", (before["session_id"],)
        ).fetchone()
        if session["status"] in {"completed", "closed_partial", "invalidated"}:
            raise RuntimeError("Nie można recenzować zakresu zamkniętej sesji")
        if before["assigned_to"] and actor == before["assigned_to"]:
            raise RuntimeError("Zakres musi zaakceptować inny aktor niż wykonawca")
        if decision == "accepted":
            full_checkpoint = (
                last_cp["checkpoint_status"] == "submitted"
                and last_cp["last_completed_ordinal"] == before["end_ordinal"]
                and last_cp["next_ordinal"] in {None, before["end_ordinal"] + 1}
                and bool(last_cp["last_completed_locator"])
                and bool(_loads(last_cp["position_fingerprint_json"], {}))
                and last_cp["source_version"] == session["source_version"]
                and last_cp["manifest_sha256"] == session["manifest_sha256"]
            )
            if not full_checkpoint:
                raise RuntimeError(
                    "accepted wymaga pełnego checkpointu submitted na końcu zakresu "
                    "i zgodnej wersji źródła; użyj accepted_partial"
                )
        connection.execute("UPDATE browse_ranges SET status=?, revision=revision+1, updated_at=? WHERE id=?", (decision, now, range_id))
        after = dict(connection.execute("SELECT * FROM browse_ranges WHERE id=?", (range_id,)).fetchone())
        if decision in {"accepted", "accepted_partial"}:
            cp = dict(last_cp)
            sequence = cp["sequence_number"] + 1
            accepted_cp = {
                **{key: cp[key] for key in (
                    "session_id", "range_id", "last_completed_ordinal",
                    "last_completed_locator", "last_page_label", "last_act_label",
                    "next_ordinal", "next_locator", "position_fingerprint_json",
                    "search_terms_snapshot_json", "method_snapshot", "source_version",
                    "manifest_sha256",
                )},
                "id": stable_id("browse_checkpoint", range_id, str(sequence), now),
                "supersedes_id": cp["id"],
                "sequence_number": sequence,
                "checkpoint_status": "accepted",
                "note": rationale,
                "actor": actor,
                "created_at": now,
            }
            connection.execute(
                """INSERT INTO browse_checkpoints(
                       id, session_id, range_id, supersedes_id, sequence_number,
                       last_completed_ordinal, last_completed_locator,
                       last_page_label, last_act_label, next_ordinal, next_locator,
                       position_fingerprint_json, search_terms_snapshot_json,
                       method_snapshot, source_version, manifest_sha256,
                       checkpoint_status, note, actor, created_at
                   ) VALUES (
                       :id, :session_id, :range_id, :supersedes_id, :sequence_number,
                       :last_completed_ordinal, :last_completed_locator, :last_page_label,
                       :last_act_label, :next_ordinal, :next_locator,
                       :position_fingerprint_json, :search_terms_snapshot_json,
                       :method_snapshot, :source_version, :manifest_sha256,
                       :checkpoint_status, :note, :actor, :created_at
                   )""",
                accepted_cp,
            )
        _audit(connection, actor=actor, action=f"browse_range_{decision}", object_type="browse_range", object_id=range_id, before=before, after=after, rationale=rationale)
    return after


def add_browse_finding(
    root: Path,
    session_id: str,
    locator: str,
    finding_type: str,
    *,
    created_by: str,
    range_id: str | None = None,
    checkpoint_id: str | None = None,
    ordinal: int | None = None,
    page_label: str | None = None,
    act_label: str | None = None,
    raw_text: str | None = None,
    normalized_text: str | None = None,
    confidence: float | None = None,
    citation_id: str | None = None,
    occurrence_id: str | None = None,
    assertion_id: str | None = None,
    status: str = "lead",
    note: str | None = None,
) -> dict:
    _ready(root)
    locator = _required(locator, "locator")
    actor = _required(created_by, "created_by")
    _validate(finding_type, {"exact_hit", "candidate", "near_miss", "context", "negative_control", "anomaly"}, "finding_type")
    _validate(status, {"lead", "recorded", "review_required", "dismissed"}, "status")
    if confidence is not None and not 0 <= confidence <= 1:
        raise ValueError("confidence musi mieścić się w zakresie 0–1")
    now = utc_now()
    item = {
        "id": stable_id("browse_finding", session_id, locator, finding_type, raw_text or "", normalized_text or ""),
        "session_id": session_id, "range_id": range_id, "checkpoint_id": checkpoint_id,
        "ordinal": ordinal, "locator": locator, "page_label": page_label,
        "act_label": act_label, "finding_type": finding_type, "raw_text": raw_text,
        "normalized_text": normalized_text, "confidence": confidence,
        "citation_id": citation_id, "occurrence_id": occurrence_id,
        "assertion_id": assertion_id, "verification_status": "imported_unverified",
        "status": status, "note": note, "created_by": actor, "created_at": now,
    }
    with connect(root) as connection:
        connection.execute("BEGIN IMMEDIATE")
        _require_session_mutable(connection, session_id)
        _validate_session_links(
            connection, session_id, range_id=range_id,
            checkpoint_id=checkpoint_id,
        )
        connection.execute(
            """INSERT INTO browse_findings(
                   id, session_id, range_id, checkpoint_id, ordinal, locator,
                   page_label, act_label, finding_type, raw_text, normalized_text,
                   confidence, citation_id, occurrence_id, assertion_id,
                   verification_status, status, note, created_by, created_at
               ) VALUES (
                   :id, :session_id, :range_id, :checkpoint_id, :ordinal, :locator,
                   :page_label, :act_label, :finding_type, :raw_text,
                   :normalized_text, :confidence, :citation_id, :occurrence_id,
                   :assertion_id, :verification_status, :status, :note,
                   :created_by, :created_at
               )""",
            item,
        )
        _audit(connection, actor=actor, action="browse_finding_add", object_type="browse_finding", object_id=item["id"], before=None, after=item, rationale=note)
    return item


def add_revisit_trigger(
    root: Path,
    session_id: str,
    trigger_type: str,
    *,
    created_by: str,
    issue_id: str | None = None,
    condition_text: str | None = None,
    priority: str = "r2_medium",
    evidence: dict | None = None,
) -> dict:
    _ready(root)
    _validate(trigger_type, TRIGGER_TYPES, "trigger_type")
    _validate(priority, set(REVISIT_PRIORITY_ORDER), "priority")
    actor = _required(created_by, "created_by")
    now = utc_now()
    item = {
        "id": stable_id("browse_trigger", session_id, issue_id or "", trigger_type, condition_text or ""),
        "session_id": session_id,
        "issue_id": issue_id,
        "trigger_type": trigger_type,
        "condition_text": condition_text,
        "priority": priority,
        "status": "pending",
        "evidence_json": _json(evidence or {}),
        "created_by": actor,
        "created_at": now,
        "satisfied_at": None,
    }
    with connect(root) as connection:
        connection.execute("BEGIN IMMEDIATE")
        _require_session_mutable(connection, session_id)
        if issue_id:
            issue = connection.execute(
                "SELECT session_id FROM browse_issues WHERE id=?", (issue_id,)
            ).fetchone()
            if issue is None or issue["session_id"] != session_id:
                raise ValueError("issue_id nie należy do tej sesji")
        cursor = connection.execute(
            """INSERT INTO browse_revisit_triggers(
                   id, session_id, issue_id, trigger_type, condition_text, priority,
                   status, evidence_json, created_by, created_at, satisfied_at
               ) VALUES (
                   :id, :session_id, :issue_id, :trigger_type, :condition_text,
                   :priority, :status, :evidence_json, :created_by, :created_at,
                   :satisfied_at
               ) ON CONFLICT(id) DO NOTHING""",
            item,
        )
        stored = dict(connection.execute(
            "SELECT * FROM browse_revisit_triggers WHERE id=?", (item["id"],)
        ).fetchone())
        if cursor.rowcount:
            _audit(
                connection, actor=actor, action="browse_trigger_add",
                object_type="browse_revisit_trigger", object_id=item["id"],
                before=None, after=stored, rationale=condition_text,
            )
    result = stored
    result["evidence"] = _loads(result.pop("evidence_json"), {})
    return result


def set_revisit_trigger_status(
    root: Path,
    trigger_id: str,
    status: str,
    *,
    actor: str,
    evidence: dict | None = None,
    rationale: str | None = None,
) -> dict:
    _ready(root)
    _validate(status, {"satisfied", "dismissed"}, "status")
    actor = _required(actor, "actor")
    now = utc_now()
    with connect(root) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM browse_revisit_triggers WHERE id=?", (trigger_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"Nie znaleziono wyzwalacza powrotu: {trigger_id}")
        before = dict(row)
        _require_session_mutable(connection, before["session_id"])
        payload = evidence if evidence is not None else _loads(before["evidence_json"], {})
        connection.execute(
            """UPDATE browse_revisit_triggers
               SET status=?, evidence_json=?, satisfied_at=? WHERE id=?""",
            (status, _json(payload), now if status == "satisfied" else None, trigger_id),
        )
        after = dict(connection.execute(
            "SELECT * FROM browse_revisit_triggers WHERE id=?", (trigger_id,)
        ).fetchone())
        _audit(
            connection, actor=actor, action=f"browse_trigger_{status}",
            object_type="browse_revisit_trigger", object_id=trigger_id,
            before=before, after=after, rationale=rationale,
        )
    after["evidence"] = _loads(after.pop("evidence_json"), {})
    return after


def add_browse_issue(
    root: Path,
    session_id: str,
    locator: str,
    issue_field: str,
    issue_kind: str,
    *,
    created_by: str,
    image_quality: str,
    reading_confidence: str,
    review_satisfaction: str,
    range_id: str | None = None,
    checkpoint_id: str | None = None,
    finding_id: str | None = None,
    handwriting_profile_id: str | None = None,
    ordinal: int | None = None,
    page_label: str | None = None,
    act_label: str | None = None,
    primary_cause: str | None = None,
    additional_causes: list[str] | None = None,
    current_reading: str | None = None,
    alternatives: list[str] | None = None,
    disputed_graphemes: list[str] | None = None,
    status: str = "open",
    severity: str = "warning",
    claim_impact: str = "none",
    recommended_action: str | None = None,
    revisit_triggers: list[str] | None = None,
    revisit_priority: str = "r2_medium",
) -> dict:
    _ready(root)
    locator = _required(locator, "locator")
    actor = _required(created_by, "created_by")
    _validate(issue_field, ISSUE_FIELDS, "issue_field")
    _validate(issue_kind, ISSUE_KINDS, "issue_kind")
    _validate(image_quality, {"q0_unusable", "q1_poor", "q2_usable", "q3_good", "q4_excellent"}, "image_quality")
    _validate(reading_confidence, {"c0_none", "c1_guess", "c2_plausible", "c3_high", "c4_secure"}, "reading_confidence")
    _validate(review_satisfaction, {"s0_blocked", "s1_provisional", "s2_sufficient", "s3_verified"}, "review_satisfaction")
    _validate(status, {"open", "in_review", "waiting_for_trigger", "reopened"}, "status")
    _validate(severity, {"info", "warning", "blocker"}, "severity")
    _validate(claim_impact, {"none", "low", "medium", "high", "critical"}, "claim_impact")
    _validate(revisit_priority, set(REVISIT_PRIORITY_ORDER), "revisit_priority")
    causes = [cause for cause in (additional_causes or []) if cause]
    for cause in ([primary_cause] if primary_cause else []) + causes:
        _validate(cause, CAUSE_CODES, "cause_code")
    trigger_types = revisit_triggers or []
    for trigger in trigger_types:
        _validate(trigger, TRIGGER_TYPES, "trigger_type")
    if trigger_types and status == "open" and review_satisfaction in {"s0_blocked", "s1_provisional"}:
        status = "waiting_for_trigger"
    now = utc_now()
    item = {
        "id": stable_id("browse_issue", session_id, locator, issue_field, issue_kind),
        "session_id": session_id, "range_id": range_id, "checkpoint_id": checkpoint_id,
        "finding_id": finding_id, "handwriting_profile_id": handwriting_profile_id,
        "ordinal": ordinal, "locator": locator, "page_label": page_label,
        "act_label": act_label, "issue_field": issue_field, "issue_kind": issue_kind,
        "primary_cause": primary_cause, "additional_causes_json": _json(causes),
        "image_quality": image_quality, "current_reading": current_reading,
        "alternatives_json": _json(alternatives or []),
        "disputed_graphemes_json": _json(disputed_graphemes or []),
        "reading_confidence": reading_confidence,
        "review_satisfaction": review_satisfaction, "status": status,
        "severity": severity, "claim_impact": claim_impact,
        "recommended_action": recommended_action, "resolution_note": None,
        "created_by": actor, "last_reviewed_by": None,
        "created_at": now, "updated_at": now, "resolved_at": None,
    }
    with connect(root) as connection:
        connection.execute("BEGIN IMMEDIATE")
        _require_session_mutable(connection, session_id)
        _validate_session_links(
            connection, session_id, range_id=range_id,
            checkpoint_id=checkpoint_id, finding_id=finding_id,
            handwriting_profile_id=handwriting_profile_id,
        )
        connection.execute(
            """INSERT INTO browse_issues(
                   id, session_id, range_id, checkpoint_id, finding_id,
                   handwriting_profile_id, ordinal, locator, page_label, act_label,
                   issue_field, issue_kind, primary_cause, additional_causes_json,
                   image_quality, current_reading, alternatives_json,
                   disputed_graphemes_json,
                   reading_confidence, review_satisfaction, status, severity,
                   claim_impact, recommended_action, resolution_note, created_by,
                   last_reviewed_by, created_at, updated_at, resolved_at
               ) VALUES (
                   :id, :session_id, :range_id, :checkpoint_id, :finding_id,
                   :handwriting_profile_id, :ordinal, :locator, :page_label,
                   :act_label, :issue_field, :issue_kind, :primary_cause,
                   :additional_causes_json, :image_quality, :current_reading,
                   :alternatives_json, :disputed_graphemes_json,
                   :reading_confidence, :review_satisfaction,
                   :status, :severity, :claim_impact, :recommended_action,
                   :resolution_note, :created_by, :last_reviewed_by, :created_at,
                   :updated_at, :resolved_at
               )""",
            item,
        )
        for trigger_type in trigger_types:
            trigger_status = "pending"
            satisfied_at = None
            if handwriting_profile_id and trigger_type in {
                "hand_profile_ready", "disputed_grapheme_profiled"
            }:
                profile_status = connection.execute(
                    "SELECT status FROM handwriting_profiles WHERE id=?",
                    (handwriting_profile_id,),
                ).fetchone()[0]
                if trigger_type == "hand_profile_ready" and profile_status in {"usable", "reviewed"}:
                    trigger_status, satisfied_at = "satisfied", now
                elif trigger_type == "disputed_grapheme_profiled" and disputed_graphemes:
                    counts: dict[str, int] = {}
                    for sample in connection.execute(
                        """SELECT represented_graphemes_json FROM handwriting_samples
                           WHERE profile_id=? AND independent_context=1 AND confidence>=0.8""",
                        (handwriting_profile_id,),
                    ):
                        for grapheme in _loads(sample[0], []):
                            counts[grapheme] = counts.get(grapheme, 0) + 1
                    if all(counts.get(grapheme, 0) >= 3 for grapheme in disputed_graphemes):
                        trigger_status, satisfied_at = "satisfied", now
            trigger = {
                "id": stable_id("browse_trigger", session_id, item["id"], trigger_type, recommended_action or ""),
                "session_id": session_id, "issue_id": item["id"],
                "trigger_type": trigger_type, "condition_text": recommended_action,
                "priority": revisit_priority, "status": trigger_status,
                "evidence_json": "{}", "created_by": actor,
                "created_at": now, "satisfied_at": satisfied_at,
            }
            connection.execute(
                """INSERT OR IGNORE INTO browse_revisit_triggers(
                       id, session_id, issue_id, trigger_type, condition_text,
                       priority, status, evidence_json, created_by, created_at,
                       satisfied_at
                   ) VALUES (
                       :id, :session_id, :issue_id, :trigger_type, :condition_text,
                       :priority, :status, :evidence_json, :created_by, :created_at,
                       :satisfied_at
                   )""",
                trigger,
            )
        _audit(connection, actor=actor, action="browse_issue_add", object_type="browse_issue", object_id=item["id"], before=None, after=item, rationale=recommended_action)
    result = dict(item)
    result["additional_causes"] = _loads(result.pop("additional_causes_json"), [])
    result["alternatives"] = _loads(result.pop("alternatives_json"), [])
    result["disputed_graphemes"] = _loads(result.pop("disputed_graphemes_json"), [])
    result["revisit_triggers"] = trigger_types
    return result


def assess_browse_issue(
    root: Path,
    issue_id: str,
    *,
    actor: str,
    method: str,
    outcome: str,
    artifact_id: str | None = None,
    handwriting_profile_id: str | None = None,
    tool_name: str | None = None,
    tool_version: str | None = None,
    model_name: str | None = None,
    model_version: str | None = None,
    proposed_reading: str | None = None,
    alternatives: list[str] | None = None,
    confidence: float | None = None,
    rationale: str | None = None,
) -> dict:
    _ready(root)
    actor = _required(actor, "actor")
    _validate(method, {"manual", "ocr", "llm", "hybrid", "paleographic_profile", "expert_review"}, "method")
    _validate(outcome, {"improved", "unchanged", "worse", "resolved", "conflicting"}, "outcome")
    if confidence is not None and not 0 <= confidence <= 1:
        raise ValueError("confidence musi mieścić się w zakresie 0–1")
    now = utc_now()
    item = {
        "id": stable_id("browse_issue_assessment", issue_id, actor, method, tool_name or "", tool_version or "", model_name or "", model_version or "", now, uuid.uuid4().hex),
        "issue_id": issue_id, "artifact_id": artifact_id,
        "handwriting_profile_id": handwriting_profile_id, "method": method,
        "tool_name": tool_name, "tool_version": tool_version,
        "model_name": model_name, "model_version": model_version,
        "proposed_reading": proposed_reading,
        "alternatives_json": _json(alternatives or []),
        "confidence": confidence, "outcome": outcome, "rationale": rationale,
        "actor": actor, "created_at": now,
    }
    with connect(root) as connection:
        connection.execute("BEGIN IMMEDIATE")
        issue = connection.execute("SELECT * FROM browse_issues WHERE id=?", (issue_id,)).fetchone()
        if issue is None:
            raise LookupError(f"Nie znaleziono problemu: {issue_id}")
        before = dict(issue)
        _require_session_mutable(connection, before["session_id"])
        connection.execute(
            """INSERT INTO browse_issue_assessments(
                   id, issue_id, artifact_id, handwriting_profile_id, method,
                   tool_name, tool_version, model_name, model_version,
                   proposed_reading, alternatives_json, confidence, outcome,
                   rationale, actor, created_at
               ) VALUES (
                   :id, :issue_id, :artifact_id, :handwriting_profile_id, :method,
                   :tool_name, :tool_version, :model_name, :model_version,
                   :proposed_reading, :alternatives_json, :confidence, :outcome,
                   :rationale, :actor, :created_at
               )""",
            item,
        )
        new_status = "in_review" if outcome in {"improved", "resolved", "conflicting"} else before["status"]
        connection.execute(
            """UPDATE browse_issues SET status=?,
                   alternatives_json=COALESCE(?,alternatives_json),
                   last_reviewed_by=?, updated_at=?
               WHERE id=?""",
            (
                new_status,
                _json(alternatives) if alternatives is not None else None,
                actor,
                now,
                issue_id,
            ),
        )
        after = dict(connection.execute("SELECT * FROM browse_issues WHERE id=?", (issue_id,)).fetchone())
        _audit(connection, actor=actor, action="browse_issue_assess", object_type="browse_issue", object_id=issue_id, before=before, after=after, rationale=rationale)
    result = dict(item)
    result["alternatives"] = _loads(result.pop("alternatives_json"), [])
    return result


def resolve_browse_issue(
    root: Path,
    issue_id: str,
    *,
    actor: str,
    decision: str,
    rationale: str,
    final_reading: str | None = None,
    reading_confidence: str | None = None,
    review_satisfaction: str | None = None,
) -> dict:
    """Human review decision; automated assessments never close an issue."""

    _ready(root)
    actor = _required(actor, "actor")
    rationale = _required(rationale, "rationale")
    _validate(decision, {"resolved", "waived", "reopened"}, "decision")
    if decision == "resolved":
        final_reading = _required(final_reading or "", "final_reading")
        _validate(reading_confidence or "", {"c2_plausible", "c3_high", "c4_secure"}, "reading_confidence")
        _validate(review_satisfaction or "", {"s2_sufficient", "s3_verified"}, "review_satisfaction")
    now = utc_now()
    with connect(root) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM browse_issues WHERE id=?", (issue_id,)).fetchone()
        if row is None:
            raise LookupError(f"Nie znaleziono problemu: {issue_id}")
        before = dict(row)
        session_status = connection.execute(
            "SELECT status FROM browse_sessions WHERE id=?", (before["session_id"],)
        ).fetchone()[0]
        if session_status in {"completed", "closed_partial", "invalidated"}:
            raise RuntimeError("Najpierw trzeba ponownie otworzyć zakończoną sesję")
        connection.execute(
            """UPDATE browse_issues SET status=?,
                   current_reading=COALESCE(?,current_reading),
                   reading_confidence=COALESCE(?,reading_confidence),
                   review_satisfaction=COALESCE(?,review_satisfaction),
                   resolution_note=?, last_reviewed_by=?, updated_at=?,
                   resolved_at=CASE WHEN ? IN ('resolved','waived') THEN ? ELSE NULL END
               WHERE id=?""",
            (
                decision, final_reading, reading_confidence, review_satisfaction,
                rationale, actor, now, decision, now, issue_id,
            ),
        )
        if decision in {"resolved", "waived"}:
            connection.execute(
                """UPDATE browse_revisit_triggers SET status='dismissed'
                   WHERE issue_id=? AND status='pending'""",
                (issue_id,),
            )
        after = dict(connection.execute("SELECT * FROM browse_issues WHERE id=?", (issue_id,)).fetchone())
        _audit(
            connection, actor=actor, action=f"browse_issue_{decision}",
            object_type="browse_issue", object_id=issue_id,
            before=before, after=after, rationale=rationale,
        )
    after["additional_causes"] = _loads(after.pop("additional_causes_json"), [])
    after["alternatives"] = _loads(after.pop("alternatives_json"), [])
    after["disputed_graphemes"] = _loads(after.pop("disputed_graphemes_json"), [])
    return after


def create_handwriting_profile(
    root: Path,
    source_item_id: str,
    label: str,
    scope_text: str,
    *,
    created_by: str,
    script: str | None = None,
    languages: list[str] | None = None,
    probable_scribe: str | None = None,
    attribution_confidence: float | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    place_or_office: str | None = None,
    features: dict | None = None,
    known_ambiguities: list[str] | None = None,
    analysis_types: list[str] | None = None,
) -> dict:
    _ready(root)
    label = _required(label, "label")
    scope_text = _required(scope_text, "scope_text")
    actor = _required(created_by, "created_by")
    if attribution_confidence is not None and not 0 <= attribution_confidence <= 1:
        raise ValueError("attribution_confidence musi mieścić się w zakresie 0–1")
    now = utc_now()
    item = {
        "id": stable_id("handwriting_profile", source_item_id, label, scope_text),
        "source_item_id": source_item_id, "label": label, "scope_text": scope_text,
        "script": script, "languages_json": _json(languages or []),
        "probable_scribe": probable_scribe,
        "attribution_confidence": attribution_confidence,
        "date_from": date_from, "date_to": date_to,
        "place_or_office": place_or_office, "status": "collecting",
        "features_json": _json(features or {}),
        "known_ambiguities_json": _json(known_ambiguities or []),
        "analysis_types_json": _json(analysis_types or []),
        "created_by": actor, "created_at": now, "updated_at": now,
    }
    with connect(root) as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT * FROM handwriting_profiles WHERE id=?", (item["id"],)
        ).fetchone()
        before = dict(existing) if existing else None
        connection.execute(
            """INSERT INTO handwriting_profiles(
                   id, source_item_id, label, scope_text, script, languages_json,
                   probable_scribe, attribution_confidence, date_from, date_to,
                   place_or_office, status, features_json, known_ambiguities_json,
                   analysis_types_json, created_by, created_at, updated_at
               ) VALUES (
                   :id, :source_item_id, :label, :scope_text, :script,
                   :languages_json, :probable_scribe, :attribution_confidence,
                   :date_from, :date_to, :place_or_office, :status, :features_json,
                   :known_ambiguities_json, :analysis_types_json, :created_by,
                   :created_at, :updated_at
               ) ON CONFLICT(id) DO UPDATE SET
                   features_json=excluded.features_json,
                   known_ambiguities_json=excluded.known_ambiguities_json,
                   analysis_types_json=excluded.analysis_types_json,
                   updated_at=excluded.updated_at""",
            item,
        )
        connection.execute(
            """INSERT INTO handwriting_profile_sources(
                   profile_id, source_item_id, scope_role, status, rationale,
                   linked_by, created_at, updated_at
               ) VALUES (?, ?, 'anchor', 'accepted', ?, ?, ?, ?)
               ON CONFLICT(profile_id, source_item_id) DO UPDATE SET
                   scope_role='anchor', status='accepted',
                   rationale=excluded.rationale, linked_by=excluded.linked_by,
                   updated_at=excluded.updated_at""",
            (
                item["id"], source_item_id,
                "Akt kotwiczący wskazany przy utworzeniu profilu.",
                actor, now, now,
            ),
        )
        stored = dict(connection.execute("SELECT * FROM handwriting_profiles WHERE id=?", (item["id"],)).fetchone())
        _audit(
            connection, actor=actor,
            action="handwriting_profile_update" if before else "handwriting_profile_create",
            object_type="handwriting_profile", object_id=item["id"],
            before=before, after=stored, rationale=scope_text,
        )
    for key in ("languages_json", "features_json", "known_ambiguities_json", "analysis_types_json"):
        stored[key.removesuffix("_json")] = _loads(stored.pop(key), [] if key != "features_json" else {})
    return stored


def add_handwriting_sample(
    root: Path,
    profile_id: str,
    source_item_id: str,
    locator: str,
    sample_type: str,
    diplomatic_text: str,
    *,
    confidence: float,
    created_by: str,
    artifact_id: str | None = None,
    citation_id: str | None = None,
    ordinal: int | None = None,
    page_label: str | None = None,
    act_label: str | None = None,
    region: dict | None = None,
    normalized_text: str | None = None,
    represented_graphemes: list[str] | None = None,
    independent_context: bool = True,
    notes: str | None = None,
) -> dict:
    _ready(root)
    locator = _required(locator, "locator")
    diplomatic_text = _required(diplomatic_text, "diplomatic_text")
    actor = _required(created_by, "created_by")
    _validate(sample_type, {"grapheme", "word", "name", "surname", "toponym", "number", "formula", "abbreviation", "signature", "other"}, "sample_type")
    if not 0 <= confidence <= 1:
        raise ValueError("confidence musi mieścić się w zakresie 0–1")
    region_payload = region or {}
    region_hash = _fingerprint((source_item_id, artifact_id, locator, region_payload))
    now = utc_now()
    item = {
        "id": stable_id("handwriting_sample", profile_id, region_hash, diplomatic_text),
        "profile_id": profile_id, "source_item_id": source_item_id,
        "artifact_id": artifact_id, "citation_id": citation_id, "ordinal": ordinal,
        "locator": locator, "page_label": page_label, "act_label": act_label,
        "region_json": _json(region_payload), "region_hash": region_hash,
        "sample_type": sample_type, "diplomatic_text": diplomatic_text,
        "normalized_text": normalized_text,
        "represented_graphemes_json": _json(represented_graphemes or []),
        "confidence": confidence, "independent_context": int(bool(independent_context)),
        "notes": notes, "created_by": actor, "created_at": now,
    }
    with connect(root) as connection:
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute(
            "SELECT 1 FROM handwriting_profiles WHERE id=?", (profile_id,)
        ).fetchone() is None:
            raise LookupError(f"Nie znaleziono profilu ręki: {profile_id}")
        scope = connection.execute(
            """SELECT scope_role, status FROM handwriting_profile_sources
               WHERE profile_id=? AND source_item_id=?""",
            (profile_id, source_item_id),
        ).fetchone()
        if scope is None:
            raise ValueError("Źródło próbki nie należy do kontrolowanego zakresu profilu ręki")
        if scope["status"] != "accepted":
            raise ValueError("Źródło próbki musi mieć w profilu status accepted")
        if scope["scope_role"] == "context" and independent_context:
            raise ValueError("Źródło kontekstowe nie może dostarczać próbki niezależnej")
        if artifact_id is not None:
            artifact = connection.execute(
                "SELECT source_item_id FROM artifacts WHERE id=?", (artifact_id,)
            ).fetchone()
            if artifact is None:
                raise LookupError(f"Nie znaleziono artefaktu: {artifact_id}")
            if artifact["source_item_id"] != source_item_id:
                raise ValueError("Artefakt próbki musi należeć do wskazanego źródła")
        if citation_id is not None:
            citation = connection.execute(
                "SELECT source_item_id FROM citations WHERE id=?", (citation_id,)
            ).fetchone()
            if citation is None:
                raise LookupError(f"Nie znaleziono cytowania: {citation_id}")
            if citation["source_item_id"] != source_item_id:
                raise ValueError("Cytowanie próbki musi należeć do wskazanego źródła")
        connection.execute(
            """INSERT INTO handwriting_samples(
                   id, profile_id, source_item_id, artifact_id, citation_id,
                   ordinal, locator, page_label, act_label, region_json, region_hash,
                   sample_type, diplomatic_text, normalized_text,
                   represented_graphemes_json, confidence, independent_context,
                   notes, created_by, created_at
               ) VALUES (
                   :id, :profile_id, :source_item_id, :artifact_id, :citation_id,
                   :ordinal, :locator, :page_label, :act_label, :region_json,
                   :region_hash, :sample_type, :diplomatic_text, :normalized_text,
                   :represented_graphemes_json, :confidence, :independent_context,
                   :notes, :created_by, :created_at
               )""",
            item,
        )
        readiness = connection.execute(
            """SELECT COUNT(*) AS sample_count,
                      COUNT(DISTINCT hs.source_item_id || ':' ||
                          COALESCE(CAST(hs.ordinal AS TEXT), hs.locator)) AS location_count,
                      SUM(CASE WHEN hs.sample_type IN ('formula','abbreviation')
                               THEN 1 ELSE 0 END) AS formula_count
               FROM handwriting_samples hs
               JOIN handwriting_profile_sources hps
                 ON hps.profile_id=hs.profile_id
                AND hps.source_item_id=hs.source_item_id
               WHERE hs.profile_id=? AND hs.independent_context=1
                 AND hs.confidence>=0.8 AND hps.status='accepted'
                 AND hps.scope_role IN ('anchor','sample')""",
            (profile_id,),
        ).fetchone()
        operational = bool(
            readiness["sample_count"] >= 12
            and readiness["location_count"] >= 3
            and readiness["formula_count"] >= 3
        )
        connection.execute(
            """UPDATE handwriting_profiles
               SET updated_at=?, status=CASE
                   WHEN ? AND status='collecting' THEN 'usable' ELSE status END
               WHERE id=?""",
            (now, int(operational), profile_id),
        )
        if operational:
            connection.execute(
                """UPDATE browse_revisit_triggers SET status='satisfied', satisfied_at=?
                   WHERE status='pending' AND trigger_type='hand_profile_ready'
                     AND issue_id IN (
                         SELECT i.id FROM browse_issues i
                         JOIN browse_sessions s ON s.id=i.session_id
                         WHERE i.handwriting_profile_id=?
                           AND s.status NOT IN ('completed','closed_partial','invalidated')
                     )""",
                (now, profile_id),
            )
        grapheme_counts: dict[str, int] = {}
        for sample in connection.execute(
            """SELECT hs.represented_graphemes_json
               FROM handwriting_samples hs
               JOIN handwriting_profile_sources hps
                 ON hps.profile_id=hs.profile_id
                AND hps.source_item_id=hs.source_item_id
               WHERE hs.profile_id=? AND hs.independent_context=1
                 AND hs.confidence>=0.8 AND hps.status='accepted'
                 AND hps.scope_role IN ('anchor','sample')""",
            (profile_id,),
        ):
            for grapheme in _loads(sample[0], []):
                grapheme_counts[grapheme] = grapheme_counts.get(grapheme, 0) + 1
        disputed_triggers = connection.execute(
            """SELECT t.id,i.disputed_graphemes_json
               FROM browse_revisit_triggers t
               JOIN browse_issues i ON i.id=t.issue_id
               JOIN browse_sessions s ON s.id=i.session_id
               WHERE t.status='pending'
                 AND t.trigger_type='disputed_grapheme_profiled'
                 AND i.handwriting_profile_id=?
                 AND s.status NOT IN ('completed','closed_partial','invalidated')""",
            (profile_id,),
        ).fetchall()
        for trigger in disputed_triggers:
            disputed = _loads(trigger["disputed_graphemes_json"], [])
            if disputed and all(grapheme_counts.get(value, 0) >= 3 for value in disputed):
                connection.execute(
                    """UPDATE browse_revisit_triggers
                       SET status='satisfied',satisfied_at=?,evidence_json=?
                       WHERE id=?""",
                    (
                        now,
                        _json({"profile_id": profile_id, "grapheme_counts": grapheme_counts}),
                        trigger["id"],
                    ),
                )
        _audit(connection, actor=actor, action="handwriting_sample_add", object_type="handwriting_sample", object_id=item["id"], before=None, after=item, rationale=notes)
    result = dict(item)
    result["region"] = _loads(result.pop("region_json"), {})
    result["represented_graphemes"] = _loads(result.pop("represented_graphemes_json"), [])
    result["independent_context"] = bool(result["independent_context"])
    return result


def add_browse_quality_review(
    root: Path,
    session_id: str,
    *,
    reviewed_by: str,
    satisfaction: str,
    completeness_score: float,
    systematicity_score: float,
    locator_quality_score: float,
    writeback_quality_score: float,
    legibility_score: float,
    qa_score: float,
    note: str | None = None,
) -> dict:
    _ready(root)
    actor = _required(reviewed_by, "reviewed_by")
    note = _required(note or "", "note")
    _validate(satisfaction, {"s0_blocked", "s1_provisional", "s2_sufficient", "s3_verified"}, "satisfaction")
    scores = [completeness_score, systematicity_score, locator_quality_score, writeback_quality_score, legibility_score, qa_score]
    if any(not 0 <= score <= 1 for score in scores):
        raise ValueError("Wszystkie oceny muszą mieścić się w zakresie 0–1")
    overall = round(sum(scores) / len(scores), 4)
    minimum = min(scores)
    derived_satisfaction = (
        "s3_verified" if minimum >= 0.9
        else "s2_sufficient" if minimum >= 0.75
        else "s1_provisional" if overall >= 0.4
        else "s0_blocked"
    )
    ranking = {"s0_blocked": 0, "s1_provisional": 1, "s2_sufficient": 2, "s3_verified": 3}
    if ranking[satisfaction] > ranking[derived_satisfaction]:
        raise ValueError(
            f"satisfaction={satisfaction} jest wyższa niż wynikająca z ocen {derived_satisfaction}"
        )
    now = utc_now()
    item = {
        "id": stable_id("browse_quality", session_id, actor, now, uuid.uuid4().hex),
        "session_id": session_id, "completeness_score": completeness_score,
        "systematicity_score": systematicity_score,
        "locator_quality_score": locator_quality_score,
        "writeback_quality_score": writeback_quality_score,
        "legibility_score": legibility_score, "qa_score": qa_score,
        "overall_score": overall, "satisfaction": satisfaction,
        "reviewed_by": actor, "note": note, "created_at": now,
    }
    with connect(root) as connection:
        connection.execute("BEGIN IMMEDIATE")
        _require_session_mutable(connection, session_id)
        assignees = {
            row[0] for row in connection.execute(
                """SELECT DISTINCT assigned_to FROM browse_ranges
                   WHERE session_id=? AND range_kind='primary' AND assigned_to IS NOT NULL""",
                (session_id,),
            )
        }
        if actor in assignees:
            raise RuntimeError("Oceny jakości musi dokonać aktor niezależny od wykonawców zakresów")
        connection.execute(
            """INSERT INTO browse_quality_reviews(
                   id, session_id, completeness_score, systematicity_score,
                   locator_quality_score, writeback_quality_score,
                   legibility_score, qa_score, overall_score, satisfaction,
                   reviewed_by, note, created_at
               ) VALUES (
                   :id, :session_id, :completeness_score, :systematicity_score,
                   :locator_quality_score, :writeback_quality_score,
                   :legibility_score, :qa_score, :overall_score, :satisfaction,
                   :reviewed_by, :note, :created_at
               )""",
            item,
        )
        _audit(connection, actor=actor, action="browse_quality_review", object_type="browse_session", object_id=session_id, before=None, after=item, rationale=note)
    return item


def _merged_intervals(rows: list[dict], statuses: set[str]) -> list[tuple[int, int]]:
    intervals = sorted(
        (row["start_ordinal"], row["end_ordinal"])
        for row in rows
        if row["range_kind"] == "primary" and row["status"] in statuses
    )
    merged: list[tuple[int, int]] = []
    for start, end in intervals:
        if not merged or start > merged[-1][1] + 1:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _completed_intervals(rows: list[dict], statuses: set[str]) -> list[tuple[int, int]]:
    intervals: list[tuple[int, int]] = []
    for row in rows:
        checkpoint = row.get("latest_checkpoint")
        if (
            row["range_kind"] != "primary"
            or row["status"] not in statuses
            or not checkpoint
            or checkpoint["last_completed_ordinal"] is None
        ):
            continue
        end = min(row["end_ordinal"], checkpoint["last_completed_ordinal"])
        if end >= row["start_ordinal"]:
            intervals.append((row["start_ordinal"], end))
    merged: list[tuple[int, int]] = []
    for start, end in sorted(intervals):
        if not merged or start > merged[-1][1] + 1:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _interval_units(intervals: list[tuple[int, int]]) -> int:
    return sum(end - start + 1 for start, end in intervals)


def _gaps(start: int, end: int, intervals: list[tuple[int, int]]) -> list[dict]:
    result: list[dict] = []
    cursor = start
    for left, right in intervals:
        if right < start or left > end:
            continue
        left, right = max(left, start), min(right, end)
        if left > cursor:
            result.append({"start_ordinal": cursor, "end_ordinal": left - 1})
        cursor = max(cursor, right + 1)
    if cursor <= end:
        result.append({"start_ordinal": cursor, "end_ordinal": end})
    return result


def handwriting_profile_report(root: Path, profile_id: str) -> dict:
    _ready(root)
    with connect(root) as connection:
        profile = connection.execute("SELECT * FROM handwriting_profiles WHERE id=?", (profile_id,)).fetchone()
        if profile is None:
            raise LookupError(f"Nie znaleziono profilu ręki: {profile_id}")
        scope_sources = [dict(row) for row in connection.execute(
            """SELECT hps.*, si.title AS source_title, si.date_text AS source_date,
                      si.locator AS source_locator
               FROM handwriting_profile_sources hps
               JOIN source_items si ON si.id=hps.source_item_id
               WHERE hps.profile_id=?
               ORDER BY CASE hps.scope_role WHEN 'anchor' THEN 0 WHEN 'sample' THEN 1 ELSE 2 END,
                        si.date_text, hps.source_item_id""",
            (profile_id,),
        )]
        samples = [dict(row) for row in connection.execute("SELECT * FROM handwriting_samples WHERE profile_id=? ORDER BY ordinal, created_at, id", (profile_id,))]
    independent_source_ids = {
        row["source_item_id"] for row in scope_sources
        if row["status"] == "accepted" and row["scope_role"] in {"anchor", "sample"}
    }
    independent = [
        row for row in samples
        if row["independent_context"] and row["confidence"] >= 0.8
        and row["source_item_id"] in independent_source_ids
    ]
    locations = {
        (row["source_item_id"], row["ordinal"] if row["ordinal"] is not None else row["locator"])
        for row in independent
    }
    formula_count = sum(row["sample_type"] in {"formula", "abbreviation"} for row in independent)
    grapheme_counts: dict[str, int] = {}
    for row in independent:
        for grapheme in _loads(row["represented_graphemes_json"], []):
            grapheme_counts[grapheme] = grapheme_counts.get(grapheme, 0) + 1
    operational = len(independent) >= 12 and len(locations) >= 3 and formula_count >= 3
    item = dict(profile)
    for key in ("languages_json", "features_json", "known_ambiguities_json", "analysis_types_json"):
        item[key.removesuffix("_json")] = _loads(item.pop(key), [] if key != "features_json" else {})
    return {
        "profile": item,
        "sample_count": len(samples),
        "scope_sources": scope_sources,
        "independent_high_confidence_samples": len(independent),
        "independent_locations": len(locations),
        "independent_ordinals": len(locations),
        "formula_or_abbreviation_samples": formula_count,
        "grapheme_counts": grapheme_counts,
        "graphemes_ready_for_disputes": sorted(
            grapheme for grapheme, count in grapheme_counts.items() if count >= 3
        ),
        "operational_profile_ready": operational,
        "readiness_note": (
            "Profil spełnia minimalne progi ogólne; gotowość dla spornego grafemu nadal wymaga co najmniej trzech niezależnych próbek."
            if operational else
            "Profil wymaga co najmniej 12 niezależnych pewnych próbek z 3 lokalizacji i 3 próbek formuł lub skrótów."
        ),
        "samples": samples,
    }


def list_browse_sessions(
    root: Path,
    *,
    case_id: str | None = None,
    source_item_id: str | None = None,
    status: str | None = None,
) -> list[dict]:
    _ready(root)
    if status is not None:
        _validate(status, SESSION_STATUSES, "status")
    clauses: list[str] = []
    values: list[str] = []
    if case_id:
        clauses.append("s.case_id=?")
        values.append(case_id)
    if source_item_id:
        clauses.append("s.source_item_id=?")
        values.append(source_item_id)
    if status:
        clauses.append("s.status=?")
        values.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with connect(root) as connection:
        rows = [dict(row) for row in connection.execute(
            f"""SELECT s.*,
                       COUNT(DISTINCT r.id) AS range_count,
                       COUNT(DISTINCT i.id) AS issue_count,
                       COUNT(DISTINCT CASE WHEN i.status NOT IN ('resolved','waived')
                                          THEN i.id END) AS open_issue_count,
                       MAX(q.created_at) AS last_quality_review_at
                FROM browse_sessions s
                LEFT JOIN browse_ranges r ON r.session_id=s.id
                LEFT JOIN browse_issues i ON i.session_id=s.id
                LEFT JOIN browse_quality_reviews q ON q.session_id=s.id
                {where}
                GROUP BY s.id
                ORDER BY s.updated_at DESC, s.id""",
            values,
        )]
    for row in rows:
        row["search_terms"] = _loads(row.pop("search_terms_json"), [])
    return rows


def browse_session_report(root: Path, session_id: str) -> dict:
    _ready(root)
    with connect(root) as connection:
        session_row = connection.execute("SELECT * FROM browse_sessions WHERE id=?", (session_id,)).fetchone()
        if session_row is None:
            raise LookupError(f"Nie znaleziono sesji: {session_id}")
        session = dict(session_row)
        inventory_units = [dict(row) for row in connection.execute(
            "SELECT * FROM browse_inventory_units WHERE session_id=? ORDER BY ordinal",
            (session_id,),
        )]
        ranges = [dict(row) for row in connection.execute("SELECT * FROM browse_ranges WHERE session_id=? ORDER BY range_kind,start_ordinal,end_ordinal,id", (session_id,))]
        checkpoints = [dict(row) for row in connection.execute("SELECT * FROM browse_checkpoints WHERE session_id=? ORDER BY range_id,sequence_number", (session_id,))]
        findings = [dict(row) for row in connection.execute("SELECT * FROM browse_findings WHERE session_id=? ORDER BY ordinal,created_at,id", (session_id,))]
        issues = [dict(row) for row in connection.execute("SELECT * FROM browse_issues WHERE session_id=? ORDER BY status,claim_impact DESC,ordinal,created_at,id", (session_id,))]
        assessments = [dict(row) for row in connection.execute(
            """SELECT a.* FROM browse_issue_assessments a
               JOIN browse_issues i ON i.id=a.issue_id
               WHERE i.session_id=? ORDER BY a.rowid""",
            (session_id,),
        )]
        triggers = [dict(row) for row in connection.execute("SELECT * FROM browse_revisit_triggers WHERE session_id=? ORDER BY status,priority DESC,created_at,id", (session_id,))]
        quality_rows = [dict(row) for row in connection.execute(
            "SELECT * FROM browse_quality_reviews WHERE session_id=? ORDER BY rowid",
            (session_id,),
        )]
        renditions = [dict(row) for row in connection.execute(
            """SELECT * FROM browse_renditions
               WHERE session_id=? ORDER BY ordinal,rendition_class,created_at,id""",
            (session_id,),
        )]
        rendition_gate_history = [dict(row) for row in connection.execute(
            """SELECT * FROM browse_rendition_gate_reviews
               WHERE session_id=? ORDER BY created_at,rowid""",
            (session_id,),
        )]
    latest_by_range: dict[str, dict] = {}
    for checkpoint in checkpoints:
        checkpoint["position_fingerprint"] = _loads(
            checkpoint.pop("position_fingerprint_json"), {}
        )
        checkpoint["search_terms_snapshot"] = _loads(
            checkpoint.pop("search_terms_snapshot_json"), []
        )
        latest_by_range[checkpoint["range_id"]] = checkpoint
    for row in ranges:
        row["latest_checkpoint"] = latest_by_range.get(row["id"])
    accepted_intervals = _merged_intervals(ranges, {"accepted"})
    worked_intervals = _completed_intervals(
        ranges,
        {"in_progress", "paused", "submitted", "accepted", "accepted_partial", "blocked"},
    )
    expected_start = session["expected_start_ordinal"]
    expected_end = session["expected_end_ordinal"]
    expected_count = session["expected_unit_count"]
    known_inventory = expected_start is not None and expected_end is not None and expected_count is not None
    accepted_units = _interval_units(accepted_intervals)
    worked_units = _interval_units(worked_intervals)
    gaps = _gaps(expected_start, expected_end, accepted_intervals) if known_inventory else []
    open_blockers = [
        row for row in issues
        if row["status"] not in {"resolved", "waived"}
        and (
            row["severity"] == "blocker"
            or row["claim_impact"] in {"high", "critical"}
            or row["review_satisfaction"] in {"s0_blocked", "s1_provisional"}
        )
    ]
    accepted_ranges = [row for row in ranges if row["range_kind"] == "primary" and row["status"] == "accepted"]
    terminal_checkpoints_ok = all(
        row.get("latest_checkpoint") and row["latest_checkpoint"]["checkpoint_status"] == "accepted"
        for row in accepted_ranges
    )
    manifest_declared = bool(session["source_version"] and session["manifest_sha256"])
    inventory_ordinals = {row["ordinal"] for row in inventory_units}
    expected_ordinals = (
        set(range(expected_start, expected_end + 1)) if known_inventory else set()
    )
    inventory_gaps = sorted(expected_ordinals - inventory_ordinals)
    unavailable_units = [
        row for row in inventory_units
        if row["availability"] in {"missing", "restricted"}
    ]
    inventory_complete = bool(
        known_inventory
        and inventory_ordinals == expected_ordinals
        and not unavailable_units
    )
    canonical_inventory_hash = _inventory_hash(inventory_units) if inventory_units else None
    manifest_matches_inventory = bool(
        inventory_complete
        and canonical_inventory_hash
        and session["manifest_sha256"] == canonical_inventory_hash
    )
    stable_inventory = bool(manifest_declared and manifest_matches_inventory)
    coverage_complete = bool(known_inventory and accepted_units == expected_count and not gaps)
    for gate_review in rendition_gate_history:
        gate_review["comparison"] = _loads(gate_review.pop("comparison_json"), {})
    latest_rendition_gate = rendition_gate_history[-1] if rendition_gate_history else None
    rendition_gate_required = bool(session["rendition_gate_required"])
    rendition_gate_met = bool(
        not rendition_gate_required
        or (latest_rendition_gate and latest_rendition_gate["status"] == "passed")
    )
    ready_for_complete = bool(
        coverage_complete and terminal_checkpoints_ok and stable_inventory
        and not open_blockers and rendition_gate_met
    )
    pending_triggers = [row for row in triggers if row["status"] == "pending"]
    satisfied_triggers = [row for row in triggers if row["status"] == "satisfied"]
    unresolved_reading_issues = [
        row for row in issues
        if row["status"] not in {"resolved", "waived"}
        and row["review_satisfaction"] in {"s0_blocked", "s1_provisional"}
    ]
    important_open_issues = [
        row for row in issues
        if row["status"] not in {"resolved", "waived"}
        and row["claim_impact"] in {"high", "critical"}
    ]
    revisit_priority = max(
        (row["priority"] for row in pending_triggers + satisfied_triggers),
        key=lambda item: REVISIT_PRIORITY_ORDER[item],
        default="r0_none",
    )
    issue_counts: dict[str, int] = {}
    field_counts: dict[str, int] = {}
    assessments_by_issue: dict[str, list[dict]] = {}
    for assessment in assessments:
        assessment["alternatives"] = _loads(assessment.pop("alternatives_json"), [])
        assessments_by_issue.setdefault(assessment["issue_id"], []).append(assessment)
    for issue in issues:
        issue["additional_causes"] = _loads(issue.pop("additional_causes_json"), [])
        issue["alternatives"] = _loads(issue.pop("alternatives_json"), [])
        issue["disputed_graphemes"] = _loads(
            issue.pop("disputed_graphemes_json"), []
        )
        issue["assessment_history"] = assessments_by_issue.get(issue["id"], [])
        if issue["status"] in {"resolved", "waived"}:
            continue
        issue_counts[issue["issue_kind"]] = issue_counts.get(issue["issue_kind"], 0) + 1
        field_counts[issue["issue_field"]] = field_counts.get(issue["issue_field"], 0) + 1
    for trigger in triggers:
        trigger["evidence"] = _loads(trigger.pop("evidence_json"), {})
    session["search_terms"] = _loads(session.pop("search_terms_json"), [])
    latest_quality = quality_rows[-1] if quality_rows else None
    profile_ids = sorted({
        issue["handwriting_profile_id"] for issue in issues
        if issue["handwriting_profile_id"]
    })
    handwriting_reports = [
        handwriting_profile_report(root, profile_id) for profile_id in profile_ids
    ]
    handwriting_by_id = {
        row["profile"]["id"]: row for row in handwriting_reports
    }
    for issue in issues:
        profile_report = handwriting_by_id.get(issue["handwriting_profile_id"])
        if not profile_report:
            issue["handwriting_readiness"] = None
            continue
        disputed = issue["disputed_graphemes"]
        ready_graphemes = set(profile_report["graphemes_ready_for_disputes"])
        issue["handwriting_readiness"] = {
            "operational_profile_ready": profile_report["operational_profile_ready"],
            "disputed_graphemes": disputed,
            "ready_for_all_disputed_graphemes": bool(
                disputed and set(disputed).issubset(ready_graphemes)
            ),
            "missing_graphemes": sorted(set(disputed) - ready_graphemes),
        }
    quality_gate_met = bool(
        latest_quality and latest_quality["satisfaction"] in {"s2_sufficient", "s3_verified"}
    )
    ready_for_complete = bool(ready_for_complete and quality_gate_met)
    if session["status"] == "completed" and not ready_for_complete:
        integrity_warning = "Sesja ma status completed, ale bieżące bramki kompletności nie są spełnione."
    else:
        integrity_warning = None
    return {
        "session": session,
        "progress": {
            "known_inventory": known_inventory,
            "expected_units": expected_count,
            "worked_units": worked_units,
            "accepted_units": accepted_units,
            "worked_fraction": round(worked_units / expected_count, 4) if expected_count else None,
            "accepted_fraction": round(accepted_units / expected_count, 4) if expected_count else None,
            "accepted_intervals": accepted_intervals,
            "gaps": gaps,
            "stable_inventory": stable_inventory,
            "manifest_declared": manifest_declared,
            "inventory_complete": inventory_complete,
            "canonical_inventory_sha256": canonical_inventory_hash,
            "manifest_matches_inventory": manifest_matches_inventory,
            "inventory_recorded_units": len(inventory_units),
            "inventory_gaps": inventory_gaps,
            "unavailable_units": unavailable_units,
            "terminal_checkpoints_ok": terminal_checkpoints_ok,
            "open_blockers": len(open_blockers),
            "coverage_complete": coverage_complete,
            "ready_for_complete": ready_for_complete,
            "quality_gate_met": quality_gate_met,
            "rendition_gate_required": rendition_gate_required,
            "rendition_gate_met": rendition_gate_met,
            "integrity_warning": integrity_warning,
        },
        "satisfaction": {
            "latest_review": latest_quality,
            "worth_revisit": bool(
                satisfied_triggers
                or (
                    unresolved_reading_issues
                    and (
                        pending_triggers
                        or important_open_issues
                    )
                    or important_open_issues
                )
            ),
            "revisit_now": bool(satisfied_triggers),
            "waiting_for_condition": bool(pending_triggers),
            "needs_current_review": bool(
                unresolved_reading_issues and not pending_triggers
            ),
            "revisit_priority": revisit_priority,
            "unresolved_provisional_or_blocked": len(unresolved_reading_issues),
            "pending_trigger_count": len(pending_triggers),
            "open_issue_kinds": issue_counts,
            "open_issue_fields": field_counts,
        },
        "ranges": ranges,
        "checkpoints": checkpoints,
        "inventory_units": inventory_units,
        "issues": issues,
        "revisit_triggers": triggers,
        "findings": findings,
        "quality_history": quality_rows,
        "renditions": renditions,
        "rendition_gate": {
            "required": rendition_gate_required,
            "met": rendition_gate_met,
            "latest_review": latest_rendition_gate,
            "history": rendition_gate_history,
        },
        "handwriting_profiles": handwriting_reports,
        "safety_note": "Checkpointy, problemy, profile pisma i findings opisują proces. Nie są źródłami, twierdzeniami ani dowodem negatywnym.",
    }


def browse_session_markdown(root: Path, session_id: str) -> str:
    report = browse_session_report(root, session_id)
    session = report["session"]
    progress = report["progress"]
    satisfaction = report["satisfaction"]
    lines = [
        f"# Karta przeglądania: {session['title']}",
        "",
        f"- Identyfikator: `{session['id']}`",
        f"- Status: **{session['status']}**",
        f"- Zakres badania: {session['scope_text']}",
        f"- Metoda: {session['method']}",
        f"- Jednostka postępu: **{session['ordinal_kind']}**",
        f"- Źródło: `{session['source_item_id']}`",
        f"- Wersja źródła: `{session['source_version'] or 'brak'}`",
        f"- Manifest SHA-256: `{session['manifest_sha256'] or 'brak'}`",
        "",
        "## Najlepsza dostępna reprodukcja",
        "",
        f"- Bramka wymagana: {'tak' if report['rendition_gate']['required'] else 'nie (sesja historyczna)' }.",
        f"- Bramka spełniona: {'tak' if report['rendition_gate']['met'] else 'nie'}.",
        f"- Ostatnia decyzja: {report['rendition_gate']['latest_review'] or 'brak'}.",
        f"- Zarejestrowane warianty: {len(report['renditions'])}.",
        "",
        "## Postęp i możliwość wznowienia",
        "",
        f"- Zaakceptowano: {progress['accepted_units']} z {progress['expected_units'] if progress['expected_units'] is not None else 'nieznanej liczby'} jednostek.",
        f"- Przejrzano roboczo: {progress['worked_units']} jednostek.",
        f"- Braki w zaakceptowanym pokryciu: {progress['gaps'] or 'brak'}.",
        f"- Stabilna wersja źródła i manifest: {'tak' if progress['stable_inventory'] else 'nie'}.",
        f"- Hash manifestu zgodny z inwentarzem: {'tak' if progress['manifest_matches_inventory'] else 'nie'} (`{progress['canonical_inventory_sha256'] or 'brak'}`).",
        f"- Jednostki manifestu: {progress['inventory_recorded_units']}; braki: {progress['inventory_gaps'] or 'brak'}.",
        f"- Jednostki niedostępne/ograniczone: {progress['unavailable_units'] or 'brak'}.",
        f"- Bramka najlepszej reprodukcji: {'spełniona' if progress['rendition_gate_met'] else 'niespełniona'}.",
        f"- Gotowe do pełnego zamknięcia: {'tak' if progress['ready_for_complete'] else 'nie'}.",
        "",
        "## Jakość i sens powrotu",
        "",
        f"- Ostatnia ocena jakości: {satisfaction['latest_review'] or 'brak'}.",
        f"- Nierozwiązane odczyty prowizoryczne/zablokowane: {satisfaction['unresolved_provisional_or_blocked']}.",
        f"- Oczekujące warunki powrotu: {satisfaction['pending_trigger_count']}.",
        f"- Priorytet powrotu: **{satisfaction['revisit_priority']}**.",
        f"- Czekamy na warunek: {'tak' if satisfaction['waiting_for_condition'] else 'nie'}.",
        f"- Warunek spełniony — wróć teraz: {'tak' if satisfaction['revisit_now'] else 'nie'}.",
        f"- Warto wrócić: {'tak' if satisfaction['worth_revisit'] else 'nie'}.",
        "",
        "## Zakresy i checkpointy",
        "",
    ]
    for row in report["ranges"]:
        checkpoint = row.get("latest_checkpoint") or {}
        lines.append(
            f"- {row['start_ordinal']}–{row['end_ordinal']} ({row['range_kind']}): "
            f"**{row['status']}**; ostatnio {checkpoint.get('last_completed_locator') or 'brak lokalizatora'}; "
            f"następnie {checkpoint.get('next_locator') or checkpoint.get('next_ordinal') or 'nie wskazano'}."
        )
    lines.extend(["", "### Pełna historia checkpointów", ""])
    if not report["checkpoints"]:
        lines.append("Brak checkpointów.")
    for checkpoint in report["checkpoints"]:
        lines.append(
            f"- Zakres `{checkpoint['range_id']}`, sekwencja {checkpoint['sequence_number']}: "
            f"**{checkpoint['checkpoint_status']}**; wykonano do "
            f"{checkpoint['last_completed_locator'] or checkpoint['last_completed_ordinal'] or 'nie wskazano'}; "
            f"następnie {checkpoint['next_locator'] or checkpoint['next_ordinal'] or 'nie wskazano'}; "
            f"metoda {checkpoint['method_snapshot']}; aktor {checkpoint['actor']}; "
            f"wersja {checkpoint['source_version'] or 'brak'}; notatka: {checkpoint['note'] or 'brak'}."
        )
    lines.extend(["", "## Problemy odczytu", ""])
    if not report["issues"]:
        lines.append("Brak zapisanych problemów.")
    for issue in report["issues"]:
        lines.append(
            f"- **{issue['issue_field']} / {issue['issue_kind']}** — {issue['locator']}; "
            f"odczyt: {issue['current_reading'] or 'brak'}; pewność {issue['reading_confidence']}; "
            f"alternatywy: {issue['alternatives'] or 'brak'}; grafemy: {issue['disputed_graphemes'] or 'brak'}; "
            f"przyczyny: {[issue['primary_cause']] + issue['additional_causes'] if issue['primary_cause'] else issue['additional_causes'] or 'brak'}; "
            f"satysfakcja {issue['review_satisfaction']}; status {issue['status']}; "
            f"severity {issue['severity']}; wpływ {issue['claim_impact']}; "
            f"zalecenie: {issue['recommended_action'] or 'brak'}; "
            f"gotowość pisma: {issue['handwriting_readiness'] or 'nie dotyczy'}."
        )
        for assessment in issue["assessment_history"]:
            tool = "/".join(filter(None, [
                assessment["tool_name"], assessment["tool_version"],
                assessment["model_name"], assessment["model_version"],
            ])) or "bez wersji narzędzia"
            lines.append(
                f"  - Próba {assessment['created_at']}: {assessment['method']} ({tool}); "
                f"propozycja `{assessment['proposed_reading'] or 'brak'}`; "
                f"alternatywy {assessment['alternatives'] or 'brak'}; "
                f"confidence {assessment['confidence']}; wynik {assessment['outcome']}; "
                f"aktor {assessment['actor']}; uzasadnienie: {assessment['rationale'] or 'brak'}."
            )
    lines.extend(["", "## Warunki ponownej analizy", ""])
    active_triggers = [
        row for row in report["revisit_triggers"]
        if row["status"] in {"pending", "satisfied"}
    ]
    if not active_triggers:
        lines.append("Brak aktywnych warunków powrotu.")
    for trigger in active_triggers:
        lines.append(
            f"- **{trigger['status']} / {trigger['priority']} / {trigger['trigger_type']}** — "
            f"{trigger['condition_text'] or 'bez dodatkowego opisu'}; "
            f"dowody spełnienia: {trigger['evidence'] or 'brak'}"
        )
    lines.extend(["", "## Profile ręki pisarskiej", ""])
    if not report["handwriting_profiles"]:
        lines.append("Brak profilu powiązanego z problemami tej sesji.")
    for profile in report["handwriting_profiles"]:
        lines.append(
            f"- **{profile['profile']['label']}**: status {profile['profile']['status']}; "
            f"próbki pewne/niezależne {profile['independent_high_confidence_samples']}; "
            f"lokalizacje {profile['independent_ordinals']}; formuły/skróty "
            f"{profile['formula_or_abbreviation_samples']}; gotowy ogólnie: "
            f"{'tak' if profile['operational_profile_ready'] else 'nie'}; "
            f"grafemy gotowe do sporu: {profile['graphemes_ready_for_disputes'] or 'brak'}."
        )
    lines.extend([
        "",
        "> Uwaga: karta opisuje proces przeglądania. Sama nie jest dowodem źródłowym ani dowodem negatywnym.",
        "",
    ])
    return "\n".join(lines)


def export_browse_session_report(root: Path, session_id: str) -> dict:
    report = browse_session_report(root, session_id)
    destination = research_root(root) / "exports"
    destination.mkdir(parents=True, exist_ok=True)
    json_path = destination / f"browse-{session_id}.json"
    markdown_path = destination / f"browse-{session_id}.md"
    json_path.write_text(_json(report) + "\n", encoding="utf-8")
    markdown_path.write_text(browse_session_markdown(root, session_id), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(markdown_path)}


def close_browse_session(
    root: Path,
    session_id: str,
    status: str,
    *,
    actor: str,
    rationale: str,
) -> dict:
    _ready(root)
    _validate(status, {"completed", "closed_partial"}, "status")
    actor = _required(actor, "actor")
    rationale = _required(rationale, "rationale")
    now = utc_now()
    with connect(root) as connection:
        connection.execute("BEGIN IMMEDIATE")
        report = browse_session_report(root, session_id)
        if status == "completed" and not report["progress"]["ready_for_complete"]:
            raise RuntimeError("Nie można zamknąć jako completed: bramki kompletności nie są spełnione")
        row = connection.execute("SELECT * FROM browse_sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            raise LookupError(f"Nie znaleziono sesji: {session_id}")
        before = dict(row)
        connection.execute("UPDATE browse_sessions SET status=?, completed_at=?, updated_at=? WHERE id=?", (status, now, now, session_id))
        after = dict(connection.execute("SELECT * FROM browse_sessions WHERE id=?", (session_id,)).fetchone())
        _audit(connection, actor=actor, action=f"browse_session_close_{status}", object_type="browse_session", object_id=session_id, before=before, after=after, rationale=rationale)
    return browse_session_report(root, session_id)


def reopen_browse_session(
    root: Path,
    session_id: str,
    *,
    actor: str,
    rationale: str,
) -> dict:
    _ready(root)
    actor = _required(actor, "actor")
    rationale = _required(rationale, "rationale")
    now = utc_now()
    with connect(root) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM browse_sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            raise LookupError(f"Nie znaleziono sesji: {session_id}")
        before = dict(row)
        if before["status"] not in {"completed", "closed_partial"}:
            raise RuntimeError("Ponownie otworzyć można tylko sesję zamkniętą")
        connection.execute(
            """UPDATE browse_sessions SET status='review_required',
                   completed_at=NULL, updated_at=? WHERE id=?""",
            (now, session_id),
        )
        after = dict(connection.execute("SELECT * FROM browse_sessions WHERE id=?", (session_id,)).fetchone())
        _audit(
            connection, actor=actor, action="browse_session_reopen",
            object_type="browse_session", object_id=session_id,
            before=before, after=after, rationale=rationale,
        )
    return browse_session_report(root, session_id)


__all__ = [
    "CAUSE_CODES", "ISSUE_FIELDS", "ISSUE_KINDS", "TRIGGER_TYPES",
    "RENDITION_CLASSES", "RENDITION_ACQUISITION_METHODS",
    "RENDITION_ACCESS_STATUSES", "RENDITION_QUALITIES",
    "add_browse_finding", "add_browse_issue", "add_browse_quality_review",
    "add_browse_rendition",
    "add_handwriting_sample", "add_revisit_trigger", "append_browse_checkpoint",
    "assess_browse_issue", "browse_session_report", "claim_browse_range",
    "browse_inventory_hash",
    "close_browse_session", "create_browse_session", "create_handwriting_profile",
    "export_browse_session_report", "handwriting_profile_report",
    "link_handwriting_profile_source",
    "list_browse_sessions", "plan_browse_range",
    "record_browse_inventory_units", "review_browse_range",
    "review_browse_rendition_gate",
    "reopen_browse_session", "resolve_browse_issue",
    "set_revisit_trigger_status",
]
