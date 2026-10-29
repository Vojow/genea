"""Read-only, privacy-filtered operational telemetry; never a research export."""
from __future__ import annotations

import math
import os
import stat
from contextlib import ExitStack
from io import StringIO
from pathlib import Path
from typing import Any

from .. import research_telemetry as ledger_api


TelemetryError = ledger_api.TelemetryError
_COST_SEMANTICS = {
    "mode": "associated_shared_full_event_cost",
    "exclusiveAttribution": False,
    "deduplication": "union_by_eventId_with_batch_scoped_conflicts",
    "weightedAllocation": "not_implemented",
}
_UNSUPPORTED = {"value": None, "status": "unknown", "reason": "unsupported"}


class _SnapshotPath:
    """Minimal read-only Path facade consumed by the existing ledger validators."""

    def __init__(self, files: dict[str, str], path: str = ""):
        self.files = files
        self.path = path

    def __truediv__(self, child: str) -> "_SnapshotPath":
        return _SnapshotPath(self.files, f"{self.path}/{child}".lstrip("/"))

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]

    def exists(self) -> bool:
        return self.path in self.files or self.is_dir()

    def is_dir(self) -> bool:
        return self.path in {"", "events"}

    def glob(self, pattern: str) -> list["_SnapshotPath"]:
        assert pattern == "*.json"
        prefix = self.path + "/"
        return [_SnapshotPath(self.files, name) for name in self.files
                if name.startswith(prefix) and "/" not in name[len(prefix):] and name.endswith(".json")]

    def open(self, mode: str, *, encoding: str) -> StringIO:
        assert mode == "r" and encoding == "utf-8"
        if self.path not in self.files:
            raise FileNotFoundError(self.path)
        return StringIO(self.files[self.path])


def _snapshot(path: Path, root: Path) -> _SnapshotPath:
    """Open every component relative to held directory FDs, without following links."""
    files: dict[str, str] = {}
    try:
        with ExitStack() as stack:
            directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            descriptor = os.open(root, directory_flags)
            stack.callback(os.close, descriptor)
            for component in path.relative_to(root).parts:
                descriptor = os.open(component, directory_flags, dir_fd=descriptor)
                stack.callback(os.close, descriptor)

            def entries(directory: int) -> list[str]:
                names = os.listdir(directory)
                for name in names:
                    if stat.S_ISLNK(os.stat(name, dir_fd=directory, follow_symlinks=False).st_mode):
                        raise TelemetryError("telemetry symlinks are unsupported")
                return names

            def read(directory: int, name: str, key: str) -> None:
                file_descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
                with os.fdopen(file_descriptor, "r", encoding="utf-8") as handle:
                    if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                        raise TelemetryError("ledger file is not regular")
                    files[key] = handle.read()

            names = entries(descriptor)
            if ".write.lock" in names:
                raise TelemetryError("ledger writer lock is active or stale")
            read(descriptor, "manifest.json", "manifest.json")
            events_descriptor = os.open("events", directory_flags, dir_fd=descriptor)
            stack.callback(os.close, events_descriptor)
            for name in sorted(entries(events_descriptor)):
                if name.endswith(".json"):
                    read(events_descriptor, name, "events/" + name)
            if "close.json" in names:
                read(descriptor, "close.json", "close.json")
            if ".write.lock" in entries(descriptor):
                raise TelemetryError("ledger changed while reading its snapshot")
    except (OSError, UnicodeError) as exc:
        raise TelemetryError("cannot safely read ledger snapshot") from exc
    return _SnapshotPath(files)


def _safe_path(path: Path, root: Path) -> None:
    """Reject links in every component before the collector opens any file."""
    if not path.is_relative_to(root):
        raise TelemetryError("telemetry path is outside the allowed root")
    current = root
    for component in path.relative_to(root).parts:
        current = current / component
        if current.is_symlink():
            raise TelemetryError("telemetry symlinks are unsupported")
    if not path.resolve().is_relative_to(root.resolve()):
        raise TelemetryError("telemetry path is outside the allowed root")


def _labels(presentation: dict[str, Any]) -> dict[tuple[str, str], str]:
    """Resolve only references already present in the privacy-filtered model."""
    labels: dict[tuple[str, str], str] = {}

    def add(kind: str, items: Any) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            if item.get("privacy") in {"living", "masked_living", "restricted", "family_private", "strict_private"}:
                continue
            label = item.get("label") or item.get("name") or item.get("title") or item["id"]
            if isinstance(label, str):
                labels[(kind, item["id"])] = label

    add("person", presentation.get("people"))
    add("person", presentation.get("occurrences"))
    add("act", presentation.get("sources"))
    add("volume", presentation.get("volumeIndexes"))
    add("group", presentation.get("sourceReportedNetworks"))
    graph = presentation.get("graphScopes", {})
    if isinstance(graph, dict):
        add("group", graph.get("scopes"))
    add("case", presentation.get("cases"))
    backlog = presentation.get("researchBacklog")
    if isinstance(backlog, dict):
        for case_id in backlog.get("caseIds", []):
            if isinstance(case_id, str):
                labels.setdefault(("case", case_id), "Sprawa " + case_id)
    for items in (presentation.get("claims"), backlog.get("items") if isinstance(backlog, dict) else backlog):
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("caseId"), str):
                labels.setdefault(("case", item["caseId"]), "Sprawa " + item["caseId"])
    return labels


def _scope(ref: dict[str, Any], labels: dict[tuple[str, str], str]) -> dict[str, Any] | None:
    kind, scope_id = ref["kind"], ref["id"]
    if kind == "batch":
        label = "Partia " + scope_id
    elif kind == "generation":
        label = labels.get(("person", ref["anchorId"]))
        if label is None:
            return None
        label = f"{label} — pokolenie {ref['depth']}"
    else:
        label = labels.get((kind, scope_id))
        if label is None:
            return None
    return {**ref, "label": label, "availability": "available"}


def _clean_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    # Notes are arbitrary collector text and can mention protected people/logs.
    if any(isinstance(metric.get("value"), float) and not math.isfinite(metric["value"])
           for metric in metrics.values()):
        raise TelemetryError("aggregated telemetry metric is not finite")
    return {name: {key: value for key, value in metric.items() if key != "notes"}
            for name, metric in metrics.items()}


def _identity(event: dict[str, Any]) -> str:
    # The enclosing ledger's batch tag is attribution, not another operation.
    value = dict(event)
    value.pop("_projectionKey", None)
    value["scopeRefs"] = [ref for ref in event["scopeRefs"] if ref["kind"] != "batch"]
    return ledger_api._canonical(value)


def _union(batches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, list[tuple[str, dict[str, Any]]]]] = {}
    for batch in batches:
        for event in batch["events"]:
            identities = groups.setdefault(event["eventId"], {})
            identities.setdefault(_identity(event), []).append((batch["id"], event))
    result: list[dict[str, Any]] = []
    for event_id in sorted(groups):
        identities = groups[event_id]
        conflict = len(identities) > 1
        for observations in identities.values():
            batch_id, event = min(observations, key=lambda item: item[0])
            projected = dict(event)
            projected["_projectionKey"] = f"{batch_id}:{event_id}" if conflict else event_id
            result.append(projected)
    result.sort(key=lambda event: event["_projectionKey"])
    ledger_api._validate_generation_semantics(result)
    return result


def _projection_keys(events: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    """Map a local eventId and payload identity to its stable projected key."""
    return {(event["eventId"], _identity(event)): event["_projectionKey"] for event in events}


def _aggregate(batches: list[dict[str, Any]]) -> dict[str, Any]:
    """Union operations, retaining batch totals without summing their operations."""
    events = _union(batches)
    projection_keys = _projection_keys(events)
    operations = [event for event in events if event["measurementKind"] == "operation"]
    metrics = ledger_api._summaries(operations)
    for name in ledger_api.METRICS:
        totals: dict[str, tuple[dict[str, Any], dict[str, Any], set[str]]] = {}
        for batch in batches:
            for event in batch["events"]:
                if event["measurementKind"] == "batch_total" and name in event["metrics"]:
                    event_key = projection_keys[(event["eventId"], _identity(event))]
                    operation_keys = {
                        projection_keys[(operation["eventId"], _identity(operation))]
                        for operation in batch["events"]
                        if operation["measurementKind"] == "operation"
                    }
                    totals.setdefault(event_key, (
                        event, batch["metrics"][name],
                        operation_keys,
                    ))
        if not totals:
            continue
        covered: set[str] = set()
        observations = []
        partial = False
        extra_unknown = 0
        for event, measured, operation_ids in totals.values():
            if event["metrics"][name]["status"] == "unknown":
                extra_unknown += 1
                continue
            if covered.intersection(operation_ids):
                raise TelemetryError("overlapping batch totals cannot be aggregated")
            covered.update(operation_ids)
            partial = partial or measured["status"] == "partial"
            extra_unknown += measured["unknownObservations"]
            observations.append({
                "value": measured["value"],
                "status": event["metrics"][name]["status"],
                "unit": measured["unit"], "note": "",
            })
        remaining = [event for event in operations if event["_projectionKey"] not in covered]
        observations.extend(event["metrics"][name] for event in remaining if name in event["metrics"])
        measured = ledger_api._metric_summary(
            observations, ledger_api.CANONICAL_UNITS[name],
            missing_observations=extra_unknown + sum(name not in event["metrics"] for event in remaining),
            aggregation="union_by_eventId_with_batch_totals",
        )
        if partial and measured["status"] in {"actual", "estimated"}:
            measured["status"] = "partial"
        metrics[name] = measured
    return {
        "eventCount": len(events), "eventIds": [event["_projectionKey"] for event in events],
        "metrics": _clean_metrics(metrics), "ratios": ledger_api._ratios(metrics),
        "costSemantics": dict(_COST_SEMANTICS),
    }


def project_research_telemetry(
    root: Path, presentation: dict[str, Any], *, batch_id: str | None = None,
    scope_kind: str | None = None, scope_id: str | None = None,
) -> dict[str, Any]:
    """Read only direct ledger children of the fixed local operational root."""
    if batch_id is not None:
        ledger_api._identifier(batch_id, "batchId")
    if (scope_kind is None) != (scope_id is None):
        raise TelemetryError("scopeKind and scopeId must be supplied together")
    if scope_kind is not None:
        if scope_kind not in ledger_api.SCOPE_KINDS:
            raise TelemetryError("unsupported scopeKind")
        ledger_api._identifier(scope_id, "scopeId")
    root = root.resolve()
    allowed = root / "research" / "tmp" / "research-telemetry"
    _safe_path(allowed, root)
    labels = _labels(presentation)
    batches: list[dict[str, Any]] = []
    if allowed.exists():
        if not allowed.is_dir():
            raise TelemetryError("telemetry root is not a directory")
        paths = [allowed / batch_id] if batch_id is not None else sorted(allowed.iterdir())
        for path in paths:
            _safe_path(path, root)
            if not path.exists():
                continue
            if not path.is_dir():
                # Budget/launch sidecars belong to the operational telemetry root,
                # but are not batch ledgers and must not break the read-only view.
                if batch_id is None and path.is_file() and path.suffix == ".json":
                    continue
                raise TelemetryError("telemetry root contains an unsupported entry")
            ledger_api._identifier(path.name, "batchId")
            for item in path.iterdir():
                _safe_path(item, root)
            events_directory = path / "events"
            if events_directory.is_dir():
                for item in events_directory.iterdir():
                    _safe_path(item, root)
            snapshot = _snapshot(path, root)
            manifest = ledger_api._manifest(snapshot)
            if manifest["batchId"] != path.name:
                raise TelemetryError("ledger directory does not match batchId")
            # Reuse all collector checks, including closure and stale totals.
            report = ledger_api._build_report(snapshot, manifest)
            events = ledger_api._read_events(snapshot)
            for event in events:
                if not any(ref["kind"] == "batch" and ref["id"] == path.name for ref in event["scopeRefs"]):
                    raise TelemetryError("ledger event is missing its batch attribution")
                if any(ref["kind"] == "batch" and ref["id"] != path.name for ref in event["scopeRefs"]):
                    raise TelemetryError("ledger event has conflicting batch attribution")
                if event["measurementKind"] == "batch_total" and event["scopeRefs"] != [{"kind": "batch", "id": path.name}]:
                    raise TelemetryError("batch_total must have only its batch scope")
            refs = [ref for event in events for ref in event["scopeRefs"]]
            closure = report.get("closure", {}).get("outcome")
            if closure is not None:
                refs.extend(entry["scopeRef"] for entry in closure["scopeOutcomes"])
            if any(ref["kind"] == "batch" and ref["id"] != path.name for ref in refs):
                raise TelemetryError("closure has conflicting batch attribution")
            # Withhold the entire batch: totals and counts could disclose hidden work.
            if any(_scope(ref, labels) is None for ref in refs):
                continue
            if scope_kind is not None:
                events = [event for event in events if any(
                    ref["kind"] == scope_kind and ref["id"] == scope_id for ref in event["scopeRefs"]
                )]
                if not events:
                    continue
            measured = ledger_api._summaries(events)
            batches.append({
                "id": path.name, "batchId": path.name, "label": "Partia " + path.name,
                "closed": report["closed"], "events": events, "metrics": measured,
            })
    _union(batches)  # Validate conflicts even when no metric uses the event.
    scopes: dict[str, dict[str, Any]] = {}
    projected_batches = []
    for batch in batches:
        events = batch["events"]
        refs = ledger_api._validate_scope_refs([ref for event in events for ref in event["scopeRefs"]]) if events else []
        projected_batches.append({
            "id": batch["id"], "batchId": batch["id"], "label": batch["label"],
            "closed": batch["closed"], "eventCount": len(events),
            "eventIds": sorted(event["eventId"] for event in events),
            "scopeRefs": [_scope(ref, labels) for ref in refs],
            "metrics": _clean_metrics(batch["metrics"]),
            "ratios": ledger_api._ratios(batch["metrics"]),
            "stages": [{
                "stageId": stage, "eventCount": sum(event["stageId"] == stage for event in events),
                "metrics": _clean_metrics(ledger_api._summaries(
                    [event for event in events if event["stageId"] == stage], coverage_events=events,
                )),
            } for stage in sorted({event["stageId"] for event in events})],
            "workItems": dict(_UNSUPPORTED),
        })
        for ref in refs:
            key = f"{ref['kind']}:{ref['id']}"
            scoped = scopes.setdefault(key, {"id": key, "scope": _scope(ref, labels), "batches": []})
            selected = [event for event in events if ref in event["scopeRefs"]]
            scoped["batches"].append({**batch, "events": selected, "metrics": ledger_api._summaries(selected)})
    projected_scopes = []
    for item in sorted(scopes.values(), key=lambda value: value["id"]):
        projected_scopes.append({
            "id": item["id"], "scope": item["scope"],
            "batchIds": sorted(batch["id"] for batch in item["batches"]),
            **_aggregate(item["batches"]), "workItems": dict(_UNSUPPORTED),
        })
    return {
        "schemaVersion": 1, "generatedAt": None, "status": "ready" if batches else "empty",
        "filters": {"batchId": batch_id, "scopeKind": scope_kind, "scopeId": scope_id},
        "summary": _aggregate(batches), "batches": projected_batches, "scopes": projected_scopes,
        "unsupported": {name: dict(_UNSUPPORTED) for name in ("diskUsage", "archiveCoverage", "workItems", "wallTime")},
    }
