from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import research_budget_policy, research_session_guard


SCHEMA_VERSION = 1
METRICS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "images",
    "calls",
    "retries",
    "duration_seconds",
    "cost_usd",
    "pages",
    "regions",
    "hits",
    "correct_acts",
    "accepted_fields",
    "resolved_pairs",
    "agent_runs",
    "allowance_points",
)
CANONICAL_UNITS = {
    "input_tokens": "tokens",
    "cached_input_tokens": "tokens",
    "output_tokens": "tokens",
    "reasoning_output_tokens": "tokens",
    "images": "images",
    "calls": "calls",
    "retries": "retries",
    "duration_seconds": "seconds",
    "cost_usd": "usd",
    "pages": "pages",
    "regions": "regions",
    "hits": "hits",
    "correct_acts": "acts",
    "accepted_fields": "fields",
    "resolved_pairs": "pairs",
    "agent_runs": "runs",
    "allowance_points": "points",
}
BUDGET_METRICS = (
    "agent_runs",
    "calls",
    "duration_seconds",
    "cost_usd",
    "allowance_points",
)
SCOPE_KINDS = {"act", "person", "generation", "group", "batch", "volume", "case"}
QA_OUTCOME_METRICS = {
    "retries",
    "pages",
    "regions",
    "hits",
    "correct_acts",
    "accepted_fields",
    "resolved_pairs",
}
COUNT_METRICS = {
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "images",
    "calls",
    "retries",
    "pages",
    "regions",
    "hits",
    "correct_acts",
    "accepted_fields",
    "resolved_pairs",
    "agent_runs",
}
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class TelemetryError(ValueError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise TelemetryError(f"{label} must be a safe identifier")
    return value


def _text(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise TelemetryError(f"{label} must be a non-empty string")
    return value


def _number(value: Any, label: str, *, positive: bool = False) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise TelemetryError(f"{label} must be a finite number")
    if value < 0 or (positive and value <= 0):
        qualifier = "positive" if positive else "non-negative"
        raise TelemetryError(f"{label} must be {qualifier}")
    return value


def _parse_timestamp(value: Any, label: str) -> datetime:
    _text(value, label)
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise TelemetryError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TelemetryError(f"{label} must include a timezone")
    return parsed


def _utc_timestamp(value: str) -> str:
    parsed = _parse_timestamp(value, "timestamp").astimezone(timezone.utc)
    return parsed.isoformat().replace("+00:00", "Z")


def _json_file(path: Path, label: str) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TelemetryError(f"cannot read valid JSON from {label}") from exc


def _write_exclusive(path: Path, value: Any) -> None:
    data = (_canonical(value) + "\n").encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".telemetry-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


@contextlib.contextmanager
def _single_writer(ledger: Path) -> Iterable[None]:
    lock_path = ledger / ".write.lock"
    try:
        descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise TelemetryError(
            "ledger writer lock is active or stale; use a new batch after crash recovery"
        ) from exc
    owned = os.fstat(descriptor)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(b'{"schemaVersion":1,"status":"active"}\n')
            handle.flush()
            os.fsync(handle.fileno())
        yield
    finally:
        try:
            current = lock_path.stat()
        except FileNotFoundError:
            current = None
        if current is not None and (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino):
            lock_path.unlink()


def _is_within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def _validated_ledger_path(root: Path, ledger: str | Path | None, batch_id: str | None) -> Path:
    root = root.resolve()
    if ledger is None:
        if batch_id is None:
            raise TelemetryError("--batch-id is required when --ledger is omitted")
        path = root / "research" / "tmp" / "research-telemetry" / _identifier(batch_id, "batchId")
    else:
        candidate = Path(ledger).expanduser()
        path = candidate if candidate.is_absolute() else root / candidate
    path = path.resolve(strict=False)
    forbidden_directories = (
        root / "sources",
        root / "research" / "archive" / "originals",
        root / "research" / "records",
    )
    if any(_is_within(path, item.resolve(strict=False)) for item in forbidden_directories):
        raise TelemetryError("ledger path is inside an immutable or evidence directory")
    if any(part.casefold() == "raw" for part in path.parts):
        raise TelemetryError("ledger path is inside a RAW directory")
    canonical_databases = (
        root / "research" / "genea.sqlite3",
        root / "research" / "genea.encrypted.sqlite3",
    )
    if any(path == item.resolve(strict=False) or item.resolve(strict=False) in path.parents for item in canonical_databases):
        raise TelemetryError("ledger path conflicts with the canonical database")
    return path


def _validate_budgets(value: Any) -> dict[str, int | float]:
    if (
        not isinstance(value, dict)
        or not value
        or not set(value).issubset(BUDGET_METRICS)
    ):
        raise TelemetryError("budget JSON must contain a non-empty subset of supported budget metrics")
    return {
        name: _number(value[name], f"budget.{name}", positive=True)
        for name in BUDGET_METRICS
        if name in value
    }


def _manifest(ledger: Path) -> dict[str, Any]:
    value = _json_file(ledger / "manifest.json", "ledger manifest")
    if not isinstance(value, dict) or value.get("schemaVersion") != SCHEMA_VERSION:
        raise TelemetryError("unsupported or malformed ledger manifest")
    _identifier(value.get("batchId"), "manifest.batchId")
    _text(value.get("scope"), "manifest.scope")
    _validate_budgets(value.get("budgets"))
    return value


def _ensure_open(ledger: Path) -> None:
    if (ledger / "close.json").exists():
        raise TelemetryError("ledger is closed")


def _validate_metric(name: str, value: Any) -> dict[str, Any]:
    if name not in METRICS:
        raise TelemetryError(f"unsupported metric: {name}")
    if not isinstance(value, dict) or set(value) != {"value", "status", "unit", "note"}:
        raise TelemetryError(f"metric {name} must contain value, status, unit and note")
    status = value["status"]
    if status not in {"actual", "estimated", "unknown"}:
        raise TelemetryError(f"metric {name} has an invalid status")
    unit = _text(value["unit"], f"metric {name}.unit")
    if unit != CANONICAL_UNITS[name]:
        raise TelemetryError(
            f"metric {name}.unit must be {CANONICAL_UNITS[name]}"
        )
    note = _text(value["note"], f"metric {name}.note", allow_empty=True)
    raw = value["value"]
    if status == "unknown":
        if raw is not None:
            raise TelemetryError(f"unknown metric {name} must have a null value")
        number = None
    else:
        number = _number(raw, f"metric {name}.value")
        if name in COUNT_METRICS and not isinstance(number, int):
            raise TelemetryError(f"metric {name} must be an integer")
    return {"value": number, "status": status, "unit": unit, "note": note}


def _validate_scope_ref(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TelemetryError("scopeRef must be an object")
    kind = value.get("kind")
    if kind not in SCOPE_KINDS:
        raise TelemetryError("scopeRef.kind is unsupported")
    scope_id = _text(value.get("id"), "scopeRef.id")
    if kind == "generation":
        if set(value) != {"kind", "id", "anchorId", "depth"}:
            raise TelemetryError("generation scopeRef requires only id, anchorId and depth")
        anchor_id = _text(value.get("anchorId"), "generation.anchorId")
        depth = value.get("depth")
        if isinstance(depth, bool) or not isinstance(depth, int) or depth < 0:
            raise TelemetryError("generation.depth must be a non-negative integer")
        return {"kind": kind, "id": scope_id, "anchorId": anchor_id, "depth": depth}
    if set(value) != {"kind", "id"}:
        raise TelemetryError("non-generation scopeRef accepts only kind and id")
    return {"kind": kind, "id": scope_id}


def _validate_scope_refs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise TelemetryError("scopeRefs must be a non-empty list")
    refs = {_canonical(ref): ref for ref in (_validate_scope_ref(item) for item in value)}
    return [refs[key] for key in sorted(refs)]


def _validate_generation_semantics(events: Iterable[dict[str, Any]]) -> None:
    meanings: dict[str, tuple[str, int]] = {}
    for event in events:
        for ref in event["scopeRefs"]:
            if ref["kind"] != "generation":
                continue
            meaning = (ref["anchorId"], ref["depth"])
            existing = meanings.get(ref["id"])
            if existing is not None and existing != meaning:
                raise TelemetryError(
                    "generation scope id has ambiguous anchorId/depth semantics"
                )
            meanings[ref["id"]] = meaning


def _operation_event_ids(events: Iterable[dict[str, Any]]) -> list[str]:
    return sorted(
        event["eventId"]
        for event in events
        if event.get("measurementKind", "operation") == "operation"
    )


def _operation_digest(event_ids: list[str]) -> str:
    return hashlib.sha256(_canonical(event_ids).encode("utf-8")).hexdigest()


def _coverage_watermark(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    event_ids = _operation_event_ids(events)
    return {
        "operationEventIds": event_ids,
        "digest": _operation_digest(event_ids),
    }


def _validate_coverage_watermark(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"operationEventIds", "digest"}:
        raise TelemetryError("batch_total coverageWatermark is malformed")
    event_ids = value["operationEventIds"]
    if not isinstance(event_ids, list):
        raise TelemetryError("batch_total coverageWatermark event IDs are malformed")
    validated_ids = [_identifier(item, "coverageWatermark.operationEventId") for item in event_ids]
    if validated_ids != sorted(set(validated_ids)):
        raise TelemetryError("batch_total coverageWatermark event IDs must be sorted and unique")
    digest = _text(value["digest"], "coverageWatermark.digest")
    if digest != _operation_digest(validated_ids):
        raise TelemetryError("batch_total coverageWatermark digest does not match event IDs")
    return {"operationEventIds": validated_ids, "digest": digest}


def _attach_batch_scope(event: dict[str, Any], batch_id: str) -> None:
    for ref in event["scopeRefs"]:
        if ref["kind"] == "batch" and ref["id"] != batch_id:
            raise TelemetryError("event batch scopeRef does not match the ledger")
    if not any(ref["kind"] == "batch" for ref in event["scopeRefs"]):
        event["scopeRefs"].append({"kind": "batch", "id": batch_id})
        event["scopeRefs"] = _validate_scope_refs(event["scopeRefs"])


def _validate_record(value: Any) -> dict[str, Any]:
    required = {"eventId", "stageId", "runId", "role", "model", "engine", "version", "reasoning", "scopeRefs", "metrics"}
    allowed = required | {"timestamp", "measurementKind"}
    if not isinstance(value, dict) or not required.issubset(value) or not set(value).issubset(allowed):
        raise TelemetryError("record event has missing or unsupported fields")
    metrics = value["metrics"]
    if not isinstance(metrics, dict) or not metrics:
        raise TelemetryError("record metrics must be a non-empty object")
    event = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "record",
        "eventId": _identifier(value["eventId"], "eventId"),
        "stageId": _identifier(value["stageId"], "stageId"),
        "runId": _identifier(value["runId"], "runId"),
        "role": _text(value["role"], "role"),
        "model": _text(value["model"], "model"),
        "engine": _text(value["engine"], "engine"),
        "version": _text(value["version"], "version"),
        "reasoning": _text(value["reasoning"], "reasoning"),
        "measurementKind": value.get("measurementKind", "operation"),
        "scopeRefs": _validate_scope_refs(value["scopeRefs"]),
        "metrics": {name: _validate_metric(name, metric) for name, metric in sorted(metrics.items())},
    }
    if "timestamp" in value:
        event["timestamp"] = _utc_timestamp(value["timestamp"])
    if event["measurementKind"] not in {"operation", "batch_total"}:
        raise TelemetryError("measurementKind must be operation or batch_total")
    _validate_token_relations(event["metrics"])
    return event


def _known(metric: dict[str, Any] | None) -> bool:
    return bool(metric and metric["status"] != "unknown")


def _validate_token_relations(metrics: dict[str, dict[str, Any]]) -> None:
    input_metric = metrics.get("input_tokens")
    cached = metrics.get("cached_input_tokens")
    output = metrics.get("output_tokens")
    reasoning = metrics.get("reasoning_output_tokens")
    if _known(input_metric) and _known(cached) and cached["value"] > input_metric["value"]:
        raise TelemetryError("cached_input_tokens cannot exceed input_tokens")
    if _known(output) and _known(reasoning) and reasoning["value"] > output["value"]:
        raise TelemetryError("reasoning_output_tokens cannot exceed output_tokens")


def _event_path(ledger: Path, event_id: str) -> Path:
    return ledger / "events" / f"{_identifier(event_id, 'eventId')}.json"


def _store_event(ledger: Path, event: dict[str, Any]) -> bool:
    path = _event_path(ledger, event["eventId"])
    if path.exists():
        existing = _json_file(path, "existing event")
        if _canonical(existing) != _canonical(event):
            raise TelemetryError("eventId was already used for a different payload")
        return False
    _write_exclusive(path, event)
    return True


def _init(args: argparse.Namespace, root: Path) -> tuple[dict[str, Any], int]:
    batch_id = _identifier(args.batch_id, "batchId")
    ledger = _validated_ledger_path(root, args.ledger, batch_id)
    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "batchId": batch_id,
        "scope": _text(args.scope, "scope"),
        "budgets": _validate_budgets(_json_file(Path(args.budget_json), "budget JSON")),
    }
    manifest_path = ledger / "manifest.json"
    if ledger.exists():
        if not manifest_path.exists():
            raise TelemetryError("existing ledger directory has no manifest; refusing to overwrite")
        existing = _json_file(manifest_path, "ledger manifest")
        if _canonical(existing) != _canonical(manifest):
            raise TelemetryError("ledger is already initialized with different settings")
        return {"status": "initialized", "batchId": batch_id, "ledger": str(ledger), "alreadyApplied": True}, 0
    # Hard policy ties new batches to the active session; report-only makes it optional.
    research_session_guard.require_batch_admission(root, batch_id)
    ledger.mkdir(parents=True, mode=0o700)
    (ledger / "events").mkdir(mode=0o700)
    _write_exclusive(manifest_path, manifest)
    return {"status": "initialized", "batchId": batch_id, "ledger": str(ledger), "alreadyApplied": False}, 0


def _load_ledger(args: argparse.Namespace, root: Path) -> tuple[Path, dict[str, Any]]:
    batch_id = getattr(args, "batch_id", None)
    if batch_id is not None:
        _identifier(batch_id, "batchId")
    ledger = _validated_ledger_path(root, getattr(args, "ledger", None), batch_id)
    manifest = _manifest(ledger)
    if batch_id is not None and manifest["batchId"] != batch_id:
        raise TelemetryError("--batch-id does not match the ledger manifest")
    return ledger, manifest


def _record(args: argparse.Namespace, root: Path) -> tuple[dict[str, Any], int]:
    ledger, manifest = _load_ledger(args, root)
    with _single_writer(ledger):
        _ensure_open(ledger)
        event = _validate_record(_json_file(Path(args.event_json), "event JSON"))
        _attach_batch_scope(event, manifest["batchId"])
        existing_events = _read_events(ledger)
        _validate_generation_semantics([*existing_events, event])
        if event["measurementKind"] == "batch_total":
            if event["scopeRefs"] != [{"kind": "batch", "id": manifest["batchId"]}]:
                raise TelemetryError("batch_total must have only the matching batch scopeRef")
            existing_same_id = next(
                (
                    existing
                    for existing in existing_events
                    if existing["eventId"] == event["eventId"]
                ),
                None,
            )
            if existing_same_id is None:
                event["coverageWatermark"] = _coverage_watermark(existing_events)
            elif "coverageWatermark" in existing_same_id:
                event["coverageWatermark"] = existing_same_id["coverageWatermark"]
            for existing in existing_events:
                if existing["eventId"] == event["eventId"]:
                    continue
                if existing.get("measurementKind", "operation") != "batch_total":
                    continue
                overlap = set(existing["metrics"]).intersection(event["metrics"])
                if overlap:
                    raise TelemetryError(
                        "only one batch_total observation is allowed per metric: "
                        + ", ".join(sorted(overlap))
                    )
        written = _store_event(ledger, event)
    return {
        "status": "recorded",
        "batchId": manifest["batchId"],
        "eventId": event["eventId"],
        "alreadyApplied": not written,
    }, 0


def _usage_metric(value: Any, name: str, note: str) -> dict[str, Any]:
    number = _number(value, f"last_token_usage.{name}")
    if not isinstance(number, int):
        raise TelemetryError(f"last_token_usage.{name} must be an integer")
    return {
        "value": number,
        "status": "actual",
        "unit": "tokens",
        "note": note,
    }


def _token_snapshot(value: Any, label: str) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    snapshot: dict[str, int] = {}
    for name in (
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
    ):
        if name not in value:
            continue
        number = _number(value[name], f"{label}.{name}")
        if not isinstance(number, int):
            raise TelemetryError(f"{label}.{name} must be an integer")
        snapshot[name] = number
    return snapshot


def _import_events(
    rollout: Path,
    stage_id: str,
    start: datetime,
    end: datetime,
    scope_refs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    if start > end:
        raise TelemetryError("--start must not be later than --end")
    events: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    session_id: str | None = None
    model = "unknown"
    effort = "unknown"
    previous_total_by_session: dict[str, dict[str, int]] = {}
    seen_source_observations: set[str] = set()
    usage_by_source_observation: dict[str, dict[str, int]] = {}
    try:
        handle = rollout.open("rb")
    except OSError as exc:
        raise TelemetryError("cannot open rollout JSONL") from exc
    with handle:
        line_number = 0
        while True:
            raw = handle.readline()
            if not raw:
                break
            line_number += 1
            complete_line = raw.endswith(b"\n")
            try:
                item = json.loads(raw)
            except (UnicodeError, json.JSONDecodeError) as exc:
                if not complete_line and not handle.peek(1):
                    warnings.append(f"ignored partial JSONL tail at line {line_number}")
                    break
                raise TelemetryError(f"malformed rollout JSONL at line {line_number}") from exc
            if not isinstance(item, dict):
                raise TelemetryError(f"rollout JSONL line {line_number} is not an object")
            kind = item.get("type")
            payload = item.get("payload")
            if kind == "session_meta" and isinstance(payload, dict):
                candidate = payload.get("id")
                if isinstance(candidate, str) and candidate:
                    session_id = _identifier(candidate, "session_meta.id")
            elif kind == "turn_context" and isinstance(payload, dict):
                candidate_model = payload.get("model")
                candidate_effort = payload.get("effort", payload.get("reasoning_effort"))
                model = candidate_model if isinstance(candidate_model, str) and candidate_model else "unknown"
                effort = candidate_effort if isinstance(candidate_effort, str) and candidate_effort else "unknown"
            elif kind == "event_msg" and isinstance(payload, dict) and payload.get("type") == "token_count":
                timestamp = item.get("timestamp")
                observed = _parse_timestamp(timestamp, f"line {line_number}.timestamp")
                if observed < start or observed > end:
                    continue
                info = payload.get("info")
                usage = info.get("last_token_usage") if isinstance(info, dict) else None
                if not isinstance(usage, dict):
                    continue
                if session_id is None:
                    raise TelemetryError("token_count event has no preceding session_meta id")
                total_snapshot = _token_snapshot(info.get("total_token_usage"), "total_token_usage")
                normalized_timestamp = _utc_timestamp(timestamp)
                if total_snapshot:
                    previous = previous_total_by_session.get(session_id)
                    if previous is not None and any(
                        total_snapshot[name] < previous[name]
                        for name in set(previous).intersection(total_snapshot)
                    ):
                        raise TelemetryError(
                            "cumulative token counter reset requires a separate import segment"
                        )
                    previous_total_by_session[session_id] = total_snapshot
                    observation_basis = "total_token_usage_snapshot_not_summed"
                    source_basis = {"sessionId": session_id, "totalTokenUsage": total_snapshot}
                    metric_note = (
                        "Codex last_token_usage; deduplicated by total_token_usage snapshot; "
                        "cumulative total was not summed"
                    )
                else:
                    observation_basis = "timestamp_plus_last_token_usage_limited"
                    source_basis = {
                        "sessionId": session_id,
                        "timestamp": normalized_timestamp,
                        "lastTokenUsage": _token_snapshot(usage, "last_token_usage"),
                    }
                    metric_note = (
                        "Codex last_token_usage; limited deduplication by timestamp and last usage "
                        "because total_token_usage was unavailable"
                    )
                source_observation_id = hashlib.sha256(
                    _canonical(source_basis).encode("utf-8")
                ).hexdigest()
                usage_snapshot = _token_snapshot(usage, "last_token_usage")
                if source_observation_id in seen_source_observations:
                    if usage_by_source_observation[source_observation_id] != usage_snapshot:
                        raise TelemetryError(
                            "repeated cumulative token snapshot has conflicting last_token_usage"
                        )
                    warning = "deduplicated repeated cumulative token snapshot"
                    if warning not in warnings:
                        warnings.append(warning)
                    continue
                seen_source_observations.add(source_observation_id)
                usage_by_source_observation[source_observation_id] = usage_snapshot
                metrics: dict[str, Any] = {}
                for name in (
                    "input_tokens",
                    "cached_input_tokens",
                    "output_tokens",
                    "reasoning_output_tokens",
                ):
                    if name in usage:
                        metrics[name] = _usage_metric(usage[name], name, metric_note)
                if not metrics:
                    continue
                _validate_token_relations(metrics)
                basis = {
                    "sessionId": session_id,
                    "sourceObservationId": source_observation_id,
                }
                event_id = "rollout-" + hashlib.sha256(_canonical(basis).encode("utf-8")).hexdigest()[:32]
                run_id = session_id if IDENTIFIER.fullmatch(session_id) else "session-" + hashlib.sha256(session_id.encode()).hexdigest()[:24]
                event = {
                    "schemaVersion": SCHEMA_VERSION,
                    "kind": "codex_rollout_usage",
                    "eventId": event_id,
                    "stageId": stage_id,
                    "runId": run_id,
                    "role": "unknown",
                    "model": model,
                    "engine": "codex_rollout",
                    "version": "unknown",
                    "reasoning": effort,
                    "measurementKind": "operation",
                    "timestamp": normalized_timestamp,
                    "scopeRefs": scope_refs,
                    "metrics": metrics,
                    "attribution": {
                        "source": "token_count.info.last_token_usage",
                        "sourceObservationId": source_observation_id,
                        "deduplicationBasis": observation_basis,
                        "sessionId": session_id,
                        "sessionCoverage": "single_session_log",
                        "modelSource": "latest_preceding_turn_context",
                    },
                }
                existing = events.get(event_id)
                if existing is not None and _canonical(existing) != _canonical(event):
                    raise TelemetryError("duplicate imported event has conflicting payload")
                events[event_id] = event
    return [events[key] for key in sorted(events)], warnings


def _same_import_observation(existing: Any, candidate: dict[str, Any]) -> bool:
    if not isinstance(existing, dict):
        return False
    if (
        existing.get("kind") != "codex_rollout_usage"
        or candidate.get("kind") != "codex_rollout_usage"
    ):
        return _canonical(existing) == _canonical(candidate)
    left = dict(existing)
    right = dict(candidate)
    left.pop("timestamp", None)
    right.pop("timestamp", None)
    return _canonical(left) == _canonical(right)


def _import(args: argparse.Namespace, root: Path) -> tuple[dict[str, Any], int]:
    ledger, manifest = _load_ledger(args, root)
    with _single_writer(ledger):
        _ensure_open(ledger)
        _read_events(ledger)
        stage_id = _identifier(args.stage_id, "stageId")
        start = _parse_timestamp(args.start, "--start")
        end = _parse_timestamp(args.end, "--end")
        scope_kind = getattr(args, "scope_kind", None)
        scope_id = getattr(args, "scope_id", None)
        if (scope_kind is None) != (scope_id is None):
            raise TelemetryError("--scope-kind and --scope-id must be provided together")
        scope_refs = [{"kind": "batch", "id": manifest["batchId"]}]
        if scope_kind is not None:
            if scope_kind == "generation":
                raise TelemetryError("generation import scope requires manual anchorId and depth")
            scope_refs.append(_validate_scope_ref({"kind": scope_kind, "id": scope_id}))
        scope_refs = _validate_scope_refs(scope_refs)
        events, warnings = _import_events(Path(args.rollout), stage_id, start, end, scope_refs)
        duplicates = 0
        new_events: list[dict[str, Any]] = []
        for event in events:
            path = _event_path(ledger, event["eventId"])
            if path.exists():
                existing = _json_file(path, "existing event")
                if not _same_import_observation(existing, event):
                    raise TelemetryError("eventId was already used for a different payload")
                duplicates += 1
            else:
                new_events.append(event)
        imported = 0
        for event in new_events:
            if _store_event(ledger, event):
                imported += 1
    return {
        "status": "imported",
        "batchId": manifest["batchId"],
        "stageId": stage_id,
        "importedEvents": imported,
        "duplicateEvents": duplicates,
        "warnings": warnings,
        "coverage": "single_session_log",
        "scopeRefs": scope_refs,
    }, 0


def _read_events(ledger: Path) -> list[dict[str, Any]]:
    directory = ledger / "events"
    if not directory.is_dir():
        raise TelemetryError("ledger events directory is missing")
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sorted(directory.glob("*.json"), key=lambda item: item.name):
        value = _json_file(path, "ledger event")
        if not isinstance(value, dict) or value.get("schemaVersion") != SCHEMA_VERSION:
            raise TelemetryError("unsupported or malformed ledger event")
        event_id = _identifier(value.get("eventId"), "eventId")
        if event_id in seen or path.name != f"{event_id}.json":
            raise TelemetryError("ledger contains a duplicate or mismatched event file")
        seen.add(event_id)
        _identifier(value.get("stageId"), "stageId")
        _identifier(value.get("runId"), "runId")
        for field in ("role", "model", "engine", "version", "reasoning"):
            _text(value.get(field), field)
        measurement_kind = value.get("measurementKind", "operation")
        if measurement_kind not in {"operation", "batch_total"}:
            raise TelemetryError("ledger event measurementKind is invalid")
        value["measurementKind"] = measurement_kind
        if measurement_kind == "operation" and "coverageWatermark" in value:
            raise TelemetryError("operation event cannot have a coverageWatermark")
        if measurement_kind == "batch_total" and "coverageWatermark" in value:
            value["coverageWatermark"] = _validate_coverage_watermark(
                value["coverageWatermark"]
            )
        metrics = value.get("metrics")
        if not isinstance(metrics, dict):
            raise TelemetryError("ledger event metrics are malformed")
        value["metrics"] = {name: _validate_metric(name, metric) for name, metric in metrics.items()}
        value["scopeRefs"] = _validate_scope_refs(value.get("scopeRefs"))
        _validate_token_relations(value["metrics"])
        events.append(value)
    _validate_generation_semantics(events)
    return events


def _metric_summary(
    observations: Iterable[dict[str, Any]],
    fallback_unit: str | None = None,
    *,
    missing_observations: int = 0,
    aggregation: str = "operation_sum",
) -> dict[str, Any]:
    values = list(observations)
    units = {item["unit"] for item in values}
    if len(units) > 1:
        raise TelemetryError("metric unit changed within the ledger")
    known = [item for item in values if item["status"] != "unknown"]
    unknown_count = len(values) - len(known) + missing_observations
    if not known:
        status = "unknown"
        total = None
    elif unknown_count:
        status = "partial"
        total = sum(item["value"] for item in known)
    elif any(item["status"] == "estimated" for item in known):
        status = "estimated"
        total = sum(item["value"] for item in known)
    else:
        status = "actual"
        total = sum(item["value"] for item in known)
    return {
        "value": total,
        "status": status,
        "unit": next(iter(units), fallback_unit),
        "observations": len(values) + missing_observations,
        "unknownObservations": unknown_count,
        "aggregation": aggregation,
        "notes": sorted({item["note"] for item in values if item.get("note")}),
    }


def _summaries(
    events: list[dict[str, Any]],
    *,
    coverage_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    operation_events = [event for event in events if event["measurementKind"] == "operation"]
    batch_total_events = [event for event in events if event["measurementKind"] == "batch_total"]
    current_operation_ids = _operation_event_ids(
        events if coverage_events is None else coverage_events
    )
    current_operation_digest = _operation_digest(current_operation_ids)
    summaries: dict[str, Any] = {}
    for name in METRICS:
        total_events = [event for event in batch_total_events if name in event["metrics"]]
        totals = [event["metrics"][name] for event in total_events]
        operations = [event["metrics"][name] for event in operation_events if name in event["metrics"]]
        units = {item["unit"] for item in [*totals, *operations]}
        if len(units) > 1:
            raise TelemetryError("metric unit changed within the ledger")
        if len(totals) > 1:
            raise TelemetryError(f"multiple batch_total observations for metric {name}")
        if totals:
            watermark = total_events[0].get("coverageWatermark")
            if watermark is None:
                coverage_status = "missing_watermark"
                covered_ids: list[str] = []
                covered_digest = None
            else:
                covered_ids = watermark["operationEventIds"]
                covered_digest = watermark["digest"]
                coverage_status = (
                    "complete" if covered_ids == current_operation_ids else "stale"
                )
            if totals[0]["status"] == "unknown":
                summaries[name] = _metric_summary(
                    [*operations, *totals],
                    CANONICAL_UNITS[name],
                    missing_observations=len(operation_events) - len(operations),
                    aggregation="operation_subtotal_batch_total_unknown",
                )
            else:
                summaries[name] = _metric_summary(
                    totals,
                    CANONICAL_UNITS[name],
                    aggregation="batch_total",
                )
            summaries[name]["coverage"] = {
                "status": coverage_status,
                "coveredOperationCount": len(covered_ids),
                "coveredOperationDigest": covered_digest,
                "currentOperationCount": len(current_operation_ids),
                "currentOperationDigest": current_operation_digest,
            }
            if coverage_status != "complete" and summaries[name]["status"] in {
                "actual",
                "estimated",
            }:
                summaries[name]["status"] = "partial"
                summaries[name]["aggregation"] = "batch_total_stale"
                summaries[name]["unknownObservations"] += 1
            continue
        summaries[name] = _metric_summary(
            operations,
            CANONICAL_UNITS[name],
            missing_observations=len(operation_events) - len(operations),
        )
    uncached: list[dict[str, Any]] = []
    uncached_missing = 0
    for event in operation_events:
        metrics = event["metrics"]
        input_metric = metrics.get("input_tokens")
        if input_metric is None:
            uncached_missing += 1
            continue
        cached = metrics.get("cached_input_tokens")
        if not _known(input_metric) or not _known(cached):
            uncached.append({"value": None, "status": "unknown", "unit": "tokens", "note": ""})
        else:
            status = "estimated" if "estimated" in {input_metric["status"], cached["status"]} else "actual"
            uncached.append({
                "value": input_metric["value"] - cached["value"],
                "status": status,
                "unit": "tokens",
                "note": "input_tokens minus cached_input_tokens",
            })
    summaries["uncached_input_tokens"] = _metric_summary(
        uncached,
        "tokens",
        missing_observations=uncached_missing,
    )
    return summaries


def _ratios(metrics: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    cost = metrics["cost_usd"]
    for denominator in ("pages", "hits", "correct_acts", "accepted_fields"):
        item = metrics[denominator]
        name = f"cost_per_{denominator[:-1] if denominator.endswith('s') else denominator}"
        if cost["status"] not in {"actual", "estimated"}:
            result[name] = {"value": None, "status": "unknown", "reason": "cost_not_complete"}
        elif item["status"] not in {"actual", "estimated"}:
            result[name] = {"value": None, "status": "unknown", "reason": "denominator_not_complete"}
        elif item["value"] == 0:
            result[name] = {"value": None, "status": "unknown", "reason": "zero_denominator"}
        else:
            result[name] = {
                "value": cost["value"] / item["value"],
                "status": (
                    "estimated"
                    if "estimated" in {cost["status"], item["status"]}
                    else "actual"
                ),
                "unit": f"usd/{item['unit'] or denominator}",
            }
    return result


def _build_report(
    ledger: Path,
    manifest: dict[str, Any],
    scope_filter: tuple[str, str] | None = None,
) -> dict[str, Any]:
    events = _read_events(ledger)
    closed = (ledger / "close.json").exists()
    closure = None
    if closed:
        closure = _json_file(ledger / "close.json", "closure")
        if not isinstance(closure, dict):
            raise TelemetryError("closure is malformed")
        outcome = closure.get("outcome")
        if outcome is not None:
            validated_outcome = _validate_outcome(outcome)
            _validate_generation_semantics([
                *events,
                *(
                    {"scopeRefs": [entry["scopeRef"]]}
                    for entry in validated_outcome["scopeOutcomes"]
                ),
            ])
    if scope_filter is not None:
        kind, scope_id = scope_filter
        events = [
            event
            for event in events
            if any(ref["kind"] == kind and ref["id"] == scope_id for ref in event["scopeRefs"])
        ]
    metrics = _summaries(events)
    stages = []
    for stage_id in sorted({event["stageId"] for event in events}):
        stage_events = [event for event in events if event["stageId"] == stage_id]
        stages.append({
            "stageId": stage_id,
            "eventCount": len(stage_events),
            "runIds": sorted({event["runId"] for event in stage_events}),
            "models": sorted({event["model"] for event in stage_events}),
            "reasoning": sorted({event["reasoning"] for event in stage_events}),
            "scopeRefs": _validate_scope_refs(
                [ref for event in stage_events for ref in event["scopeRefs"]]
            ),
            "metrics": _summaries(stage_events, coverage_events=events),
        })
    result = {
        "schemaVersion": SCHEMA_VERSION,
        "batchId": manifest["batchId"],
        "scope": manifest["scope"],
        "closed": closed,
        "eventCount": len(events),
        "scopeRefs": (
            _validate_scope_refs([ref for event in events for ref in event["scopeRefs"]])
            if events
            else []
        ),
        "scopeFilter": (
            {"kind": scope_filter[0], "id": scope_filter[1]}
            if scope_filter is not None
            else None
        ),
        "costSemantics": {
            "mode": "associated_shared_full_event_cost",
            "exclusiveAttribution": False,
            "deduplication": "union_by_eventId",
            "weightedAllocation": "not_implemented",
        },
        "metrics": metrics,
        "ratios": _ratios(metrics),
        "stages": stages,
    }
    if closed:
        result["closure"] = closure
    return result


def _report(args: argparse.Namespace, root: Path) -> tuple[dict[str, Any], int]:
    ledger, manifest = _load_ledger(args, root)
    scope_kind = getattr(args, "scope_kind", None)
    scope_id = getattr(args, "scope_id", None)
    if (scope_kind is None) != (scope_id is None):
        raise TelemetryError("--scope-kind and --scope-id must be provided together")
    scope_filter = None
    if scope_kind is not None:
        if scope_kind not in SCOPE_KINDS:
            raise TelemetryError("--scope-kind is unsupported")
        scope_filter = (scope_kind, _text(scope_id, "--scope-id"))
    return _build_report(ledger, manifest, scope_filter), 0


def _gate(args: argparse.Namespace, root: Path) -> tuple[dict[str, Any], int]:
    ledger, manifest = _load_ledger(args, root)
    report = _build_report(ledger, manifest)
    if report["closed"]:
        return {"status": "closed", "allowed": False, "batchId": manifest["batchId"], "checks": []}, 2
    checks = []
    for name in BUDGET_METRICS:
        if name not in manifest["budgets"]:
            continue
        measured = report["metrics"][name]
        limit = manifest["budgets"][name]
        if measured["status"] in {"unknown", "partial"}:
            status = "measurement_required"
        elif measured["status"] == "estimated":
            status = "conditional"
        elif measured["value"] > limit:
            status = "exceeded"
        else:
            status = "adequate"
        checks.append({"metric": name, "limit": limit, "measured": measured, "status": status})
    if any(item["status"] == "exceeded" for item in checks):
        status = "exceeded"
    elif any(item["status"] == "measurement_required" for item in checks):
        status = "measurement_required"
    elif any(item["status"] == "conditional" for item in checks):
        status = "conditional"
    else:
        status = "allowed"
    report_only = research_budget_policy.report_only(root)
    measurement_status = status
    if report_only:
        status = "allowed"
    allowed = status == "allowed"
    session_gate = research_session_guard.check_batch_gate(root, manifest["batchId"])
    if session_gate is not None and session_gate["status"] != "allowed":
        allowed = False
        status = "session_blocked"
    return {
        "status": status,
        "allowed": allowed,
        "batchId": manifest["batchId"],
        "checks": checks,
        **({"sessionGate": session_gate} if session_gate is not None else {}),
        "enforcement": "report_only" if report_only else "preflight_only",
        **({"measurementStatus": measurement_status} if report_only else {}),
    }, 0 if allowed else 2


def _close(args: argparse.Namespace, root: Path) -> tuple[dict[str, Any], int]:
    ledger, manifest = _load_ledger(args, root)
    with _single_writer(ledger):
        existing_events = _read_events(ledger)
        outcome = None
        if args.outcome_json:
            outcome = _json_file(Path(args.outcome_json), "outcome JSON")
            outcome = _validate_outcome(outcome)
            _validate_generation_semantics([
                *existing_events,
                *(
                    {"scopeRefs": [entry["scopeRef"]]}
                    for entry in outcome["scopeOutcomes"]
                ),
            ])
        closure = {
            "schemaVersion": SCHEMA_VERSION,
            "batchId": manifest["batchId"],
            "reason": _text(args.reason, "reason"),
            "outcome": outcome,
        }
        path = ledger / "close.json"
        if path.exists():
            existing = _json_file(path, "closure")
            if _canonical(existing) != _canonical(closure):
                raise TelemetryError("ledger was already closed with a different payload")
            return {
                "status": "closed",
                "batchId": manifest["batchId"],
                "alreadyApplied": True,
            }, 0
        _write_exclusive(path, closure)
        return {
            "status": "closed",
            "batchId": manifest["batchId"],
            "alreadyApplied": False,
        }, 0


def _validate_outcome(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"scopeOutcomes"}:
        raise TelemetryError("outcome JSON must contain only scopeOutcomes")
    entries = value["scopeOutcomes"]
    if not isinstance(entries, list) or not entries:
        raise TelemetryError("scopeOutcomes must be a non-empty list")
    validated = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"scopeRef", "qaMetrics"}:
            raise TelemetryError("each scope outcome requires only scopeRef and qaMetrics")
        metrics = entry["qaMetrics"]
        if not isinstance(metrics, dict) or not metrics:
            raise TelemetryError("scope outcome qaMetrics must be non-empty")
        if not set(metrics).issubset(QA_OUTCOME_METRICS):
            raise TelemetryError("scope outcome contains a non-QA metric")
        validated.append({
            "scopeRef": _validate_scope_ref(entry["scopeRef"]),
            "qaMetrics": {
                name: _validate_metric(name, metric) for name, metric in sorted(metrics.items())
            },
        })
    validated.sort(key=lambda item: _canonical(item["scopeRef"]))
    _validate_generation_semantics(
        {"scopeRefs": [entry["scopeRef"]]} for entry in validated
    )
    return {"scopeOutcomes": validated}


def add_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
    parser = sub.add_parser("research-telemetry", help="Local append-only operational telemetry")
    commands = parser.add_subparsers(dest="research_telemetry_command", required=True)
    research_session_guard.add_parser(commands)

    init = commands.add_parser("init")
    init.add_argument("--batch-id", required=True)
    init.add_argument("--scope", required=True)
    init.add_argument("--budget-json", required=True)
    init.add_argument("--ledger")

    def ledger_args(command: argparse.ArgumentParser) -> None:
        command.add_argument("--batch-id")
        command.add_argument("--ledger")

    record = commands.add_parser("record")
    ledger_args(record)
    record.add_argument("--event-json", required=True)

    import_parser = commands.add_parser("import")
    ledger_args(import_parser)
    import_parser.add_argument("--rollout", required=True)
    import_parser.add_argument("--stage-id", required=True)
    import_parser.add_argument("--start", required=True)
    import_parser.add_argument("--end", required=True)
    import_parser.add_argument("--scope-kind", choices=sorted(SCOPE_KINDS))
    import_parser.add_argument("--scope-id")

    report = commands.add_parser("report")
    ledger_args(report)
    report.add_argument("--scope-kind", choices=sorted(SCOPE_KINDS))
    report.add_argument("--scope-id")
    gate = commands.add_parser("gate")
    ledger_args(gate)

    close = commands.add_parser("close")
    ledger_args(close)
    close.add_argument("--reason", required=True)
    close.add_argument("--outcome-json")
    return parser


def run(args: argparse.Namespace, root: Path) -> tuple[dict[str, Any], int]:
    handlers = {
        "init": _init,
        "record": _record,
        "import": _import,
        "report": _report,
        "gate": _gate,
        "close": _close,
    }
    try:
        research_budget_policy.report_only(root)
        command = getattr(args, "research_telemetry_command", None)
        if command == "session":
            return research_session_guard.run(args, Path(root))
        if command not in handlers:
            raise TelemetryError("missing research telemetry command")
        return handlers[command](args, Path(root))
    except (TelemetryError, research_session_guard.GuardError, research_budget_policy.BudgetPolicyError, OSError) as exc:
        return {"status": "error", "error": str(exc)}, 2


__all__ = ["METRICS", "TelemetryError", "add_parser", "run"]
