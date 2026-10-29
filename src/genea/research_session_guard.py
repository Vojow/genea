"""Private operational admission guard; never opens research databases or agents.

Explicit actual usage checkpoints cover each registered run (including main).
Account snapshots are account-global admission bounds, never task attribution.
"""
from __future__ import annotations

import argparse
import copy
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time

from . import research_budget_policy


class GuardError(ValueError):
    """Admission, storage or measurement failed closed."""


BASE = Path("research/tmp/research-session-guard")
RESOURCES = ("total_tokens", "output_tokens", "duration_seconds", "agent_runs")
COUNTERS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")


def _now():
    return time.time()


def _number(value, name, positive=False):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0 or (positive and value == 0):
        raise GuardError(f"Invalid {name}: finite {'positive' if positive else 'nonnegative'} number required")
    return value


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}", value):
        raise GuardError("Invalid identifier")
    return value


def _stamp(value):
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError()
        return stamp.timestamp()
    except (AttributeError, ValueError, OverflowError) as exc:
        raise GuardError("Timestamp with timezone required") from exc


def _text(value, name):
    if not isinstance(value, str) or not value.strip() or value == "unknown":
        raise GuardError(f"Explicit {name} required")
    return value


def _safe(path, directory=False):
    """Reject symlinks (including dangling links) in every existing component."""
    for component in (path, *path.parents):
        if component.is_symlink():
            raise GuardError(f"Symlink rejected: {component.name}")
    if path.exists() and (not path.is_dir() if directory else not path.is_file()):
        raise GuardError("Unexpected filesystem object")


def _private(path):
    if path.stat().st_mode & 0o077:
        raise GuardError("Operational state must have private permissions")


def _load(path):
    _safe(path)
    _private(path)
    try:
        with path.open(encoding="utf-8") as stream:
            return json.load(stream, parse_constant=lambda value: (_ for _ in ()).throw(GuardError("Nonfinite JSON")))
    except (OSError, ValueError) as exc:
        raise GuardError("Invalid or unreadable JSON state") from exc


def _input(path):
    # Inputs may live elsewhere; only operational outputs have a fixed root.
    try:
        with Path(path).open(encoding="utf-8") as stream:
            result = json.load(stream)
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (OSError, ValueError, TypeError) as exc:
        raise GuardError("JSON object input required") from exc


def _write(path, value):
    _safe(path)
    descriptor, temporary = tempfile.mkstemp(prefix=".guard-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _base(root, create=False):
    root = Path(root).resolve()
    base = root / BASE
    _safe(base, True)
    if create:
        for parent in (root / "research", root / "research/tmp", base):
            _safe(parent, True)
            if not parent.exists():
                parent.mkdir(mode=0o700)
        _private(base)
    return base


@contextmanager
def _locked(root, create=False):
    base = _base(root, create)
    if not base.exists():
        yield None
        return
    _private(base)
    lock = base / "write.lock"
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except OSError as exc:
        raise GuardError("Session writer lock exists or is unsafe; no automatic stale-lock recovery") from exc
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield base
    finally:
        lock.unlink()


def _snapshot(value, now, report_only=False):
    if not isinstance(value, dict) or value.get("status") != "actual":
        raise GuardError("Actual account snapshot required")
    for key in ("account_id", "window_id", "provenance"):
        _text(value.get(key), key)
    _stamp(value.get("resets_at"))
    percent = _number(value.get("used_percent"), "used_percent")
    if percent > 100 or (not report_only and _stamp(value.get("resets_at")) <= now) or _stamp(value.get("observed_at")) > now:
        raise GuardError("Invalid account snapshot window or percentage")
    return value


def _config(value, now, report_only=False):
    limits = value.get("limits")
    if not isinstance(limits, dict) or set(limits) != set(RESOURCES):
        raise GuardError("All four explicit resource limits required")
    for key in RESOURCES:
        _number(limits[key], key, True)
    if int(limits["agent_runs"]) != limits["agent_runs"]:
        raise GuardError("agent_runs must be integral")
    result = {"limits": limits}
    token_policy = value.get("token_policy", "hard")
    if token_policy not in ("hard", "report_only"):
        raise GuardError("token_policy must be hard or report_only")
    # Preserve the stored configuration shape of existing default sessions.
    if "token_policy" in value:
        result["token_policy"] = token_policy
    for key, default in (("allowance_points", 5), ("allowance_ceiling", 95), ("max_parallel", 2), ("freshness_seconds", 120), ("closeout_fraction", .2)):
        result[key] = _number(value.get(key, default), key, True)
        if (not report_only and key != "closeout_fraction" and result[key] > default) or (key == "closeout_fraction" and not (0 < result[key] < 1 if report_only else .2 <= result[key] < 1)):
            raise GuardError(f"Unsafe policy value: {key}")
    if int(result["max_parallel"]) != result["max_parallel"]:
        raise GuardError("max_parallel must be integral")
    snapshot = _snapshot(value.get("snapshot"), now, report_only)
    if not report_only and now - _stamp(snapshot["observed_at"]) > result["freshness_seconds"]:
        raise GuardError("Initial account snapshot is stale")
    return result, snapshot


def _usage(value, now):
    if not isinstance(value, dict) or value.get("status") != "actual":
        raise GuardError("Measured actual usage required; unknown is not zero")
    _text(value.get("provenance"), "usage provenance")
    for key in COUNTERS:
        number = _number(value.get(key), key)
        if int(number) != number:
            raise GuardError("Token counters must be integral")
    if value["cached_input_tokens"] > value["input_tokens"] or value["reasoning_output_tokens"] > value["output_tokens"]:
        raise GuardError("Cache/reasoning subset exceeds its total")
    if _stamp(value.get("observed_at")) > now:
        raise GuardError("Future usage checkpoint")
    return value


def _state(base, report_only=False):
    pointer = base / "active.json"
    if not pointer.exists() and not pointer.is_symlink():
        if any(base.glob("session-*.json")):
            raise GuardError("Missing active pointer with existing sessions")
        return None, None
    active = _load(pointer)
    try:
        session_id = _id(active["session_id"])
        path = base / f"session-{session_id}.json"
        state = _load(path)
        if state["version"] != 1 or state["session_id"] != session_id or state["lifecycle"] not in ("active", "closed"):
            raise GuardError("Session identity mismatch")
        config, _ = _config({**state["config"], "snapshot": state["initial_snapshot"]}, state["started_at"], report_only)
        if config != state["config"] or not isinstance(state["stop_reasons"], list) or not isinstance(state["batches"], dict):
            raise GuardError("Corrupt session configuration")
        _number(state["started_at"], "started_at")
        if state["started_at"] > _now() or _stamp(state["snapshot"]["observed_at"]) > _now():
            raise GuardError("Corrupt future session timestamp")
        if state["lifecycle"] == "closed" and not state["started_at"] <= _number(state["closed_at"], "closed_at") <= _now():
            raise GuardError("Corrupt session close timestamp")
        _snapshot(state["snapshot"], _stamp(state["snapshot"]["observed_at"]), report_only)
        if any(state["snapshot"][key] != state["initial_snapshot"][key] for key in ("account_id", "window_id", "resets_at")) or state["snapshot"]["used_percent"] < state["initial_snapshot"]["used_percent"]:
            raise GuardError("Corrupt account snapshot history")
        if not all(isinstance(reason, str) for reason in state["stop_reasons"]):
            raise GuardError("Corrupt stop reasons")
        for batch_id, batch in state["batches"].items():
            _id(batch_id)
            if batch["status"] not in ("open", "closed") or not isinstance(batch["source_ids"], list) or not batch["source_ids"] or (not report_only and len(batch["source_ids"]) > 2) or len(set(batch["source_ids"])) != len(batch["source_ids"]):
                raise GuardError("Corrupt batch state")
            for source in batch["source_ids"]:
                _id(source)
            _text(batch["question"], "batch question")
        if sum(batch["status"] == "open" for batch in state["batches"].values()) != int(state["open_batch"] is not None):
            raise GuardError("Corrupt batch pointer")
        runs = set()
        for stage_id, stage in state["stages"].items():
            _id(stage_id)
            if stage["run_id"] in runs or stage["batch_id"] not in state["batches"] or stage["phase"] not in ("research", "closeout") or stage["status"] not in ("active", "finished"):
                raise GuardError("Corrupt stage identity or status")
            runs.add(_id(stage["run_id"]))
            _reservation(stage["reservation"])
            if stage["status"] == "active" and stage["batch_id"] != state["open_batch"]:
                raise GuardError("Active run outside open batch")
            _number(stage["started_at"], "stage start")
            if not state["started_at"] <= stage["started_at"] <= _now():
                raise GuardError("Corrupt stage timestamp")
            if stage["usage"] is not None:
                _usage(stage["usage"], _now())
                if _stamp(stage["usage"]["observed_at"]) < stage["started_at"]:
                    raise GuardError("Corrupt usage timestamp")
            if stage["status"] == "finished" and stage["usage"] is None:
                raise GuardError("Finished stage has no measurement")
        if state["open_batch"] is not None and state["batches"][state["open_batch"]]["status"] != "open":
            raise GuardError("Corrupt open batch")
        return state, path
    except (KeyError, TypeError, AttributeError) as exc:
        raise GuardError("Corrupt session state") from exc


def _reservation(value):
    if not isinstance(value, dict) or value.get("role") not in ("main", "agent"):
        raise GuardError("Reservation requires explicit role main or agent")
    for key in RESOURCES:
        _number(value.get(key), key, True)
    if value["agent_runs"] != 1 or value["output_tokens"] > value["total_tokens"]:
        raise GuardError("Each unique registered run reserves one run and bounded output")
    return value


def _totals(state, reserved=False):
    totals = {key: 0 for key in RESOURCES}
    totals["duration_seconds"] = max(0, state.get("closed_at", _now()) - state["started_at"])
    for stage in state["stages"].values():
        usage = stage["usage"]
        actual = {"total_tokens": usage["input_tokens"] + usage["output_tokens"] if usage else 0, "output_tokens": usage["output_tokens"] if usage else 0, "agent_runs": 1}
        for key in ("total_tokens", "output_tokens", "agent_runs"):
            totals[key] += max(actual[key], stage["reservation"][key]) if reserved and stage["status"] == "active" else actual[key]
        if reserved and stage["status"] == "active":
            totals["duration_seconds"] = max(totals["duration_seconds"], stage["started_at"] - state["started_at"] + stage["reservation"]["duration_seconds"])
    return totals


def _enforced_resources(config):
    if config.get("token_policy", "hard") == "report_only":
        return ("duration_seconds", "agent_runs")
    return RESOURCES


def _gate(state, watch=False, report_only=False):
    if report_only:
        # Calculate diagnostics on a copy: historical stop reasons remain untouched.
        result = _gate(copy.deepcopy(state), watch)
        result.update(enforcement="report_only", status="allowed", stopRequired=False,
                      warnings=result["reasons"], reasons=[], interruptRunIds=[])
        return result
    now = state.get("closed_at", _now())
    config = state["config"]
    actual = _totals(state)
    pending = _totals(state, True)
    reasons = list(state["stop_reasons"])
    exhaustion = [key + " exhausted" for key in _enforced_resources(config) if (actual[key] > config["limits"][key] if key == "agent_runs" else actual[key] >= config["limits"][key])]
    snapshot = state["snapshot"]
    delta = snapshot["used_percent"] - state["initial_snapshot"]["used_percent"]
    if snapshot["used_percent"] >= config["allowance_ceiling"] or delta >= config["allowance_points"]:
        exhaustion.append("account allowance exhausted")
    for reason in exhaustion:
        if reason not in state["stop_reasons"]:
            state["stop_reasons"].append(reason)
    reasons.extend(exhaustion)
    stale = now - _stamp(snapshot["observed_at"]) > config["freshness_seconds"] or now >= _stamp(snapshot["resets_at"])
    if delta >= config["allowance_points"] * (1 - config["closeout_fraction"]):
        reasons.append("research allocation exhausted: allowance_points")
    headroom = config["allowance_ceiling"] - state["initial_snapshot"]["used_percent"]
    if delta >= headroom * (1 - config["closeout_fraction"]):
        reasons.append("research allocation exhausted: allowance_ceiling")
    if stale:
        reason = "account snapshot stale or window ended"
        reasons.append(reason)
        if reason not in state["stop_reasons"]:
            state["stop_reasons"].append(reason)
    active = [stage for stage in state["stages"].values() if stage["status"] == "active"]
    for stage in active:
        if stage["usage"] is None or now - _stamp(stage["usage"]["observed_at"]) > config["freshness_seconds"]:
            reason = "usage checkpoint required: " + stage["run_id"]
            reasons.append(reason)
            checkpoint_at = stage["started_at"] if stage["usage"] is None else _stamp(stage["usage"]["observed_at"])
            if now - checkpoint_at > config["freshness_seconds"] and reason not in state["stop_reasons"]:
                state["stop_reasons"].append(reason)
        if now - stage["started_at"] >= stage["reservation"]["duration_seconds"]:
            reason = "stage duration exhausted: " + stage["run_id"]
            reasons.append(reason)
            if reason not in state["stop_reasons"]:
                state["stop_reasons"].append(reason)
    for key in _enforced_resources(config):
        if pending[key] >= config["limits"][key] * (1 - config["closeout_fraction"]):
            reasons.append("research allocation exhausted: " + key)
    reasons = sorted(set(reasons + state["stop_reasons"]))
    return {"sessionId": state["session_id"], "tokenPolicy": config.get("token_policy", "hard"), "status": "blocked" if reasons else "allowed", "stopRequired": bool(state["stop_reasons"]), "reasons": reasons, "activeRunIds": [stage["run_id"] for stage in active], "interruptRunIds": [stage["run_id"] for stage in active] if state["stop_reasons"] else [], "actual": actual, "actualMeasurementStatus": "partial" if any(stage["usage"] is None for stage in active) else "actual", "missingUsageRunIds": [stage["run_id"] for stage in active if stage["usage"] is None], "committed": pending, "accountAllowanceDelta": {"value": delta, "attribution": "account_global_upper_bound"}, "openBatch": state["open_batch"]}


def _artifact(root, item):
    relative = Path(item["path"])
    if relative.is_absolute() or ".." in relative.parts or not relative.parts or relative.parts[:2] == ("research", "tmp"):
        raise GuardError("Durable artifact must be repo-relative and outside operational tmp")
    path = Path(root).resolve() / relative
    _safe(path)
    if not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
        raise GuardError("Durable artifact hash mismatch")


def _receipt(root, value):
    try:
        if value["disposition"] not in ("written", "deferred") or not value["artifacts"]:
            raise GuardError("Written/deferred disposition and durable artifacts required")
        for item in value["artifacts"]:
            _artifact(root, item)
    except (KeyError, TypeError, OSError) as exc:
        raise GuardError("Invalid durable closeout receipt") from exc


def _dispatch(args, root, base, state, report_only=False):
    command = args.session_command
    now = _now()
    if command == "init":
        if state is not None and state.get("lifecycle") != "closed":
            raise GuardError("Active session cannot be replaced")
        config, snapshot = _config(_input(args.config_json), now, report_only)
        session_id = _id(args.session_id)
        state = {"lifecycle": "active", "version": 1, "session_id": session_id, "started_at": now, "config": config, "initial_snapshot": snapshot, "snapshot": snapshot, "stages": {}, "batches": {}, "open_batch": None, "stop_reasons": []}
        path = base / f"session-{session_id}.json"
        if path.exists() or path.is_symlink():
            raise GuardError("Session already exists")
        _gate(state, report_only=report_only)
        _write(path, state)
        _write(base / "active.json", {"session_id": session_id})
        return {"status": "initialized", "sessionId": session_id}, 0
    if state is None:
        raise GuardError("No active research session")
    if state.get("lifecycle") == "closed" and command != "report":
        raise GuardError("Session is explicitly closed")
    if command == "close":
        _text(args.reason, "session close reason")
        if state["open_batch"] is not None or any(stage["status"] == "active" for stage in state["stages"].values()):
            raise GuardError("Finish measured stages and close all batches before session close")
        for batch in state["batches"].values():
            _receipt(root, batch["receipt"])
        state["lifecycle"] = "closed"
        state["closed_at"] = now
        state["close_reason"] = args.reason
    elif command == "snapshot":
        snapshot = _snapshot(_input(args.snapshot_json), now, report_only)
        previous = state["snapshot"]
        if any(snapshot[key] != previous[key] for key in ("account_id", "window_id", "resets_at")) or snapshot["used_percent"] < previous["used_percent"] or _stamp(snapshot["observed_at"]) < _stamp(previous["observed_at"]):
            state["stop_reasons"].append("account window changed or percentage/timestamp decreased")
            raise GuardError("Snapshot account/window/reset must match and counters cannot decrease")
        if not report_only and now - _stamp(snapshot["observed_at"]) > state["config"]["freshness_seconds"]:
            raise GuardError("Snapshot stale")
        state["snapshot"] = snapshot
    elif command == "batch-open":
        gate = _gate(state, report_only=report_only)
        if gate["status"] != "allowed" or state["open_batch"] is not None:
            raise GuardError("Session gate or previous closeout blocks new batch")
        batch_id = _id(args.batch_id)
        if batch_id in state["batches"]:
            raise GuardError("Batch identifier already used")
        sources = args.source_ids
        if not sources or (not report_only and len(sources) > 2) or len(set(sources)) != len(sources):
            raise GuardError("Exact nonempty distinct source IDs required (legacy maximum two)")
        for source in sources:
            _id(source)
        _text(args.question, "one research question")
        for batch in state["batches"].values():
            _receipt(root, batch["receipt"])
        state["batches"][batch_id] = {"question": args.question, "source_ids": sources, "status": "open"}
        state["open_batch"] = batch_id
    elif command == "reserve":
        reservation = _reservation(_input(args.reservation_json))
        stage_id, run_id = _id(args.stage_id), _id(args.run_id)
        if stage_id in state["stages"] or any(stage["run_id"] == run_id for stage in state["stages"].values()):
            raise GuardError("Stage/run identifier cannot be reused")
        if args.batch_id != state["open_batch"]:
            raise GuardError("Reservation needs the admitted open batch")
        gate = _gate(state, report_only=report_only)
        active = [stage for stage in state["stages"].values() if stage["status"] == "active"]
        if not report_only and len(active) >= state["config"]["max_parallel"]:
            raise GuardError("Parallel run limit reached")
        if args.phase == "research":
            if gate["status"] != "allowed":
                raise GuardError("Research admission blocked: " + "; ".join(gate["reasons"]))
            if not report_only and reservation["role"] == "agent" and not any(stage["batch_id"] == args.batch_id and stage["reservation"]["role"] == "main" and stage["usage"] is not None and now - _stamp(stage["usage"]["observed_at"]) <= state["config"]["freshness_seconds"] for stage in state["stages"].values()):
                raise GuardError("Main work requires a registered measured run before agent admission")
        elif not report_only and (any(reason.startswith("account ") for reason in gate["reasons"]) or gate["actual"]["duration_seconds"] >= state["config"]["limits"]["duration_seconds"]):
            raise GuardError("Fresh account headroom and remaining wall time required for closeout run")
        projected = dict(gate["committed"])
        for key in (() if report_only else _enforced_resources(state["config"])):
            projected[key] = max(projected[key], now - state["started_at"] + reservation[key]) if key == "duration_seconds" else projected[key] + reservation[key]
            fraction = 1 - state["config"]["closeout_fraction"] if args.phase == "research" else 1
            if projected[key] > state["config"]["limits"][key] * fraction:
                raise GuardError("Reservation exceeds remaining " + key)
        state["stages"][stage_id] = {"run_id": run_id, "batch_id": args.batch_id, "phase": args.phase, "reservation": reservation, "started_at": now, "usage": None, "status": "active"}
    elif command in ("observe", "finish-stage"):
        if args.stage_id not in state["stages"]:
            raise GuardError("Unknown stage")
        stage = state["stages"][args.stage_id]
        usage = _usage(_input(args.usage_json), now)
        if not report_only and now - _stamp(usage["observed_at"]) > state["config"]["freshness_seconds"]:
            raise GuardError("Usage checkpoint stale")
        if _stamp(usage["observed_at"]) < stage["started_at"]:
            raise GuardError("Usage checkpoint predates registered run")
        previous = stage["usage"]
        if stage["status"] == "finished":
            if command == "finish-stage" and previous == usage:
                return _gate(state, report_only=report_only), 0
            raise GuardError("Finished run is immutable")
        if previous and (any(usage[key] < previous[key] for key in COUNTERS) or _stamp(usage["observed_at"]) < _stamp(previous["observed_at"])):
            raise GuardError("Cumulative run counters/timestamp cannot decrease")
        stage["usage"] = usage
        if not report_only and now - stage["started_at"] >= stage["reservation"]["duration_seconds"]:
            state["stop_reasons"].append("stage duration exhausted: " + stage["run_id"])
        if command == "finish-stage":
            stage["status"] = "finished"
        if not report_only and state["config"].get("token_policy", "hard") == "hard" and (usage["input_tokens"] + usage["output_tokens"] > stage["reservation"]["total_tokens"] or usage["output_tokens"] > stage["reservation"]["output_tokens"]):
            state["stop_reasons"].append("measured usage exceeded reservation: " + stage["run_id"])
    elif command == "batch-close":
        if args.batch_id != state["open_batch"]:
            raise GuardError("Batch is not open")
        if any(stage["status"] == "active" for stage in state["stages"].values()):
            raise GuardError("All runs require measured terminal usage before batch close")
        receipt = _input(args.receipt_json)
        _receipt(root, receipt)
        state["batches"][args.batch_id].update(status="closed", receipt=receipt)
        state["open_batch"] = None
    elif command not in ("report", "gate", "watch", "close"):
        raise GuardError("Unknown session command")
    gate = _gate(state, watch=command == "watch", report_only=report_only)
    if command == "reserve":
        gate["gateStatus"] = gate["status"]
        gate["status"] = "reserved"
        gate["accepted"] = True
    return gate, 2 if command in ("gate", "watch") and gate["status"] != "allowed" else 0


def run(args, root):
    """Return JSON-safe output and CLI exit status; never connect to SQLite."""
    try:
        report_only = research_budget_policy.report_only(root)
        if args.session_command == "watch":
            interval = _number(args.interval, "interval", True)
            timeout = _number(args.timeout, "timeout", True)
            if not report_only and (interval > 30 or timeout > 3600):
                raise GuardError("Watch interval <=30 seconds and timeout <=3600 seconds required")
        deadline = _now() + getattr(args, "timeout", 0)
        while True:
            with _locked(root, create=args.session_command == "init") as base:
                state, path = _state(base, report_only) if base is not None else (None, None)
                try:
                    result, code = _dispatch(args, root, base, state, report_only)
                finally:
                    if state is not None:
                        _write(path, state)
            if args.session_command != "watch" or args.once or code or _now() >= deadline:
                return result, code
            time.sleep(min(args.interval, max(0, deadline - _now())))
    except (GuardError, research_budget_policy.BudgetPolicyError, OSError) as exc:
        return {"status": "error", "error": str(exc)}, 2


def check_batch_gate(root, batch_id):
    """Shared admission gate; absence preserves legacy telemetry behavior."""
    report_only = research_budget_policy.report_only(root)
    with _locked(root) as base:
        if base is None:
            return None
        state, path = _state(base, report_only)
        if state is None or state.get("lifecycle") == "closed":
            return None
        result = _gate(state, report_only=report_only)
        if batch_id != state["open_batch"]:
            if report_only:
                result["warnings"].append("batch is not admitted in optional session")
            else:
                result["status"] = "blocked"
                result["reasons"].append("batch is not admitted in active session")
        _write(path, state)
        return result


def require_batch_admission(root, batch_id):
    result = check_batch_gate(root, batch_id)
    if result and result["status"] != "allowed":
        raise GuardError("Session admission blocked: " + "; ".join(result["reasons"]))


def add_parser(commands):
    parser = commands.add_parser("session", help="Shared operational session admission and watch")
    sub = parser.add_subparsers(dest="session_command", required=True)
    for name in ("init", "snapshot", "batch-open", "reserve", "observe", "finish-stage", "batch-close", "report", "gate", "watch", "close"):
        command = sub.add_parser(name)
        if name == "init":
            command.add_argument("--session-id", required=True)
            command.add_argument("--config-json", required=True)
        elif name == "snapshot":
            command.add_argument("--snapshot-json", required=True)
        elif name == "batch-open":
            command.add_argument("--batch-id", required=True)
            command.add_argument("--question", required=True)
            command.add_argument("--source-ids", nargs="+", required=True)
        elif name == "reserve":
            for field in ("stage-id", "batch-id", "run-id", "reservation-json"):
                command.add_argument("--" + field, required=True)
            command.add_argument("--phase", choices=("research", "closeout"), required=True)
        elif name in ("observe", "finish-stage"):
            command.add_argument("--stage-id", required=True)
            command.add_argument("--usage-json", required=True)
        elif name == "batch-close":
            command.add_argument("--batch-id", required=True)
            command.add_argument("--receipt-json", required=True)
        elif name == "close":
            command.add_argument("--reason", required=True)
        elif name == "watch":
            command.add_argument("--once", action="store_true")
            command.add_argument("--interval", type=float, default=10)
            command.add_argument("--timeout", type=float, default=30)
    return parser
