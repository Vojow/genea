"""Manually invoked local Qwen batch runner with derivative-only run journals.

The time budget is cooperative: it is checked between preflight/page/rescue
calls. Each call gets at most the remaining budget as its HTTP timeout; a
response or the adapter's final integrity/import work can finish after the
deadline. No signal interrupts artifact or SQLite writes.
"""
from __future__ import annotations

import json
import math
import os
import time
import uuid
from pathlib import Path
from typing import Callable

from .qwen_volume_index import (
    DEFAULT_OPTIONS, PROMPT, SCOPE, LocalOllama, _canonical, _open_qwen_index,
    _sha, _strict_json, prepare_qwen_index, recognize_qwen_volume,
)
from .volume_index_workflow import _mutation_lock, _refresh_checksums, _unit
from .workspace import sha256_file, utc_now


DEFAULT_RESCUE_OPTIONS = {"repeat_penalty": 1.2, "repeat_last_n": 512}
WARNING = (
    "Source != proof. Wyniki są pomocnicze i nieprzejrzane: nie stanowią dowodu, "
    "transkrypcji źródłowej ani pokrycia badań. No-hit nie jest negative evidence."
)


def _atomic_text(path: Path, payload: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _append_event(events_dir: Path, journal: Path, sequence: int, payload: str) -> None:
    """Publish one immutable event, then atomically refresh its JSONL projection."""
    events_dir.mkdir(exist_ok=True)
    _atomic_text(events_dir / f"{sequence:08d}.json", payload + "\n")
    projected = "".join(
        path.read_text(encoding="utf-8")
        for path in sorted(events_dir.glob("[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].json"))
    )
    _atomic_text(journal, projected)


def _prepare_output(
    output: Path, ordinals: list[int], source_index: Path | None, project_root: Path,
) -> Path:
    if not output.expanduser().resolve().exists():
        if source_index is None:
            raise ValueError("Pierwsze uruchomienie wymaga source_index")
        prepare_qwen_index(source_index, output, ordinals, project_root=project_root)
    root, connection = _open_qwen_index(output)
    try:
        available = [row[0] for row in connection.execute("SELECT ordinal FROM units ORDER BY ordinal")]
        if not set(ordinals).issubset(available):
            raise ValueError("Żądane ordinale nie należą do przygotowanego Qwen OUTPUT")
    finally:
        connection.close()
    if source_index is not None:
        # An existing output may contain a wider range than this particular run.
        prepare_qwen_index(source_index, root, available, project_root=project_root)
    return root


def _preferred_configuration(
    root: Path, ordinal: int, variants: list[tuple[str, dict]], model_info: dict, prompt: str,
) -> list[tuple[str, dict]]:
    """Prefer a previously successful rescue without retrying the failed base.

    This only selects the configuration; the adapter still verifies its native
    checkpoint and input hashes before reusing any inference or import.
    """
    _, connection = _open_qwen_index(root)
    try:
        unit = _unit(connection, ordinal)
        attempts = connection.execute(
            "SELECT config_json FROM recognition_attempts WHERE unit_id=? AND backend=?",
            (unit["unit_id"], "qwen3-vl-ollama"),
        ).fetchall()
        for name, options in variants:
            for attempt in attempts:
                try:
                    pointer = _strict_json(attempt["config_json"])["provenance"]
                    path = (root / pointer["path"]).resolve()
                    if not path.is_relative_to(root) or sha256_file(path) != pointer["sha256"]:
                        continue
                    metadata = _strict_json(path.read_text(encoding="utf-8"))
                    expected = {
                        "adapter_schema": "genea.qwen-volume/v1", "unit_id": unit["unit_id"],
                        "ordinal": ordinal, "image_sha256": unit["render_sha256"],
                        "model_tag": model_info["model_tag"], "model_digest": model_info["model_digest"],
                        "runtime_version": model_info["runtime_version"], "prompt": prompt,
                        "generation_config": options,
                        "model_show_sha256": _sha(_canonical(model_info["model_show"]).encode()),
                    }
                    if all(metadata.get(key) == value for key, value in expected.items()):
                        if name == "rescue":
                            return [(name, options)]
                        return variants
                except (OSError, ValueError, KeyError, TypeError):
                    continue
        return variants
    finally:
        connection.close()


def _markdown(report: dict) -> str:
    counts = report["counts"]
    lines = ["# Raport nocnego przebiegu Qwen", "", f"Status: {report['status']}", "",
             f"Run: `{report['run_id']}`", f"Początek: {report['started_at']}",
             f"Aktualizacja: {report['updated_at']}", f"Czas: {report['elapsed_seconds']:.3f} s", "",
             "| Kategoria | Liczba | Ordinale |", "|---|---:|---|"]
    for key in ("completed", "failed", "pending", "skipped", "reused"):
        lines.append(f"| {key} | {counts[key]} | {', '.join(map(str, report[key])) or '—'} |")
    lines.extend(["", "Reused oraz skipped są podzbiorami completed; skipped oznacza pominiętą inferencję.",
                  "", f"Journal: `{report['journal_path']}`", f"JSON: `{report['report_json_path']}`",
                  "", WARNING, ""])
    if report.get("error"):
        lines.extend(["Błąd przebiegu:", "", report["error"], ""])
    return "\n".join(lines)


def run_qwen_overnight(
    output: Path, ordinals: list[int], *, project_root: Path,
    source_index: Path | None = None, model: str,
    endpoint: str = "http://127.0.0.1:11434", prompt: str = PROMPT,
    options: dict | None = None, timeout: float = 600,
    max_seconds: float = 8 * 60 * 60, max_consecutive_failures: int = 3,
    rescue: bool = True, rescue_options: dict | None = None,
    on_event: Callable[[dict], None] | None = None,
) -> dict:
    """Run pages serially and return a JSON-serializable morning report.

    ``rescue_options`` overrides base generation options once per failed page.
    Existing output can run without the original base directory. Invalid input
    or an unsafe output raises before writing; once a valid run directory exists,
    preflight errors, page errors and KeyboardInterrupt produce a final report.
    The deadline covers preflight and page processing, after safe preparation.
    """
    if (not ordinals or any(type(value) is not int or value < 1 for value in ordinals)
            or len(set(ordinals)) != len(ordinals)):
        raise ValueError("ordinals muszą być niepustą listą unikalnych dodatnich liczb")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("Prompt nie może być pusty")
    for name, value in (("timeout", timeout), ("max_seconds", max_seconds)):
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} musi być dodatnią skończoną liczbą")
    if type(max_consecutive_failures) is not int or max_consecutive_failures < 1:
        raise ValueError("max_consecutive_failures musi być dodatnią liczbą całkowitą")
    if on_event is not None and not callable(on_event):
        raise ValueError("on_event musi być callable albo None")
    if options is not None and not isinstance(options, dict):
        raise ValueError("options musi być obiektem JSON")
    if rescue_options is not None and not isinstance(rescue_options, dict):
        raise ValueError("rescue_options musi być obiektem JSON")
    base_options = dict(DEFAULT_OPTIONS if options is None else options)
    variants = [("base", base_options)]
    if rescue:
        recovery_options = {**base_options, **(DEFAULT_RESCUE_OPTIONS if rescue_options is None else rescue_options)}
        if recovery_options == base_options:
            raise ValueError("Konfiguracja rescue musi różnić się od bazowej")
        variants.append(("rescue", recovery_options))
    _canonical(variants)
    # Endpoint validation does not contact or start Ollama.
    client = LocalOllama(endpoint, min(timeout, max_seconds))
    root = _prepare_output(Path(output), ordinals, source_index, Path(project_root))
    run_id = f"run-{uuid.uuid4().hex}"
    run_dir = root / "overnight" / run_id
    run_dir.mkdir(parents=True)
    events_dir = run_dir / "events"
    journal = run_dir / "journal.jsonl"
    json_report, md_report = run_dir / "morning-report.json", run_dir / "morning-report.md"
    started = time.monotonic()
    deadline = started + max_seconds
    pages = [{"ordinal": ordinal, "status": "pending", "attempts": []} for ordinal in ordinals]
    report = {
        "schema": "genea.qwen-overnight/v1", "run_id": run_id, "status": "running",
        "scope": SCOPE, "evidence_status": "auxiliary_unreviewed", "warning": WARNING,
        "output": str(root), "started_at": utc_now(), "pages": pages,
        "artifacts_directory": str(root / "attempts" / "qwen"),
        "events_directory": str(events_dir),
        "journal_path": str(journal), "report_json_path": str(json_report), "report_md_path": str(md_report),
        "configuration": {"model": model, "endpoint": endpoint, "prompt": prompt,
                          "options": base_options, "rescue_options": variants[1][1] if rescue else None,
                          "timeout": timeout, "max_seconds": max_seconds,
                          "max_consecutive_failures": max_consecutive_failures},
        "callback_errors": [], "stop_reason": None,
    }
    sequence = 0

    def emit(event: str, *, notify: bool = True, **details) -> None:
        nonlocal sequence
        sequence += 1
        record = {"event": event, "run_id": run_id, "sequence": sequence,
                  "created_at": utc_now(), "elapsed_seconds": max(0, time.monotonic() - started), **details}
        payload = _canonical(record)
        _append_event(events_dir, journal, sequence, payload)
        if notify and on_event is not None:
            try:
                on_event(json.loads(payload))
            except Exception as error:
                report["callback_errors"].append(str(error))
                emit("callback_failed", notify=False, error=str(error), source_sequence=sequence)

    def snapshot() -> None:
        report["updated_at"] = utc_now()
        report["elapsed_seconds"] = max(0, time.monotonic() - started)
        report["last_event_sequence"] = sequence
        for status in ("completed", "failed", "pending"):
            report[status] = [page["ordinal"] for page in pages if page["status"] == status]
        report["reused"] = [page["ordinal"] for page in pages if page.get("reused_generation")]
        report["skipped"] = [page["ordinal"] for page in pages if page.get("skipped_inference")]
        report["counts"] = {key: len(report[key]) for key in ("completed", "failed", "pending", "skipped", "reused")}
        _atomic_text(json_report, _canonical(report) + "\n")
        _atomic_text(md_report, _markdown(report))

    def remaining() -> float:
        return deadline - time.monotonic()

    consecutive_failures = 0
    current_attempt = None
    try:
        emit("run_started", configuration=report["configuration"], ordinals=ordinals)
        snapshot()
        if remaining() <= 0:
            report["status"] = "time_limit"
        else:
            try:
                info = client.info(model)
            except Exception as error:
                report.update(status="preflight_failed", error=str(error))
                emit("preflight_failed", error=str(error))
            else:
                report["model_info"] = info
                emit("preflight_completed", model_info=info)
                for page in pages:
                    if remaining() <= 0:
                        report["status"] = "time_limit"
                        break
                    page_start = time.monotonic()
                    page["started_at"] = utc_now()
                    chosen = _preferred_configuration(root, page["ordinal"], variants, info, prompt)
                    for config_name, generation in chosen:
                        budget = remaining()
                        if budget <= 0:
                            report["status"] = "time_limit"
                            break
                        call_timeout = min(timeout, budget)
                        current_attempt = {"configuration": config_name, "options": generation,
                                           "started_at": utc_now(), "status": "running", "timeout": call_timeout}
                        page["attempts"].append(current_attempt)
                        attempt_start = time.monotonic()
                        emit("rescue_started" if config_name == "rescue" else "page_started",
                             ordinal=page["ordinal"], configuration=config_name, options=generation, timeout=call_timeout)
                        try:
                            outcome = recognize_qwen_volume(
                                root, [page["ordinal"]], model=model, endpoint=endpoint,
                                prompt=prompt, options=generation, timeout=call_timeout,
                            )
                            result = outcome["pages"][0]
                            if result["ordinal"] != page["ordinal"]:
                                raise ValueError("Adapter zwrócił inną stronę niż zlecona")
                        except Exception as error:
                            current_attempt.update(status="failed", error=str(error),
                                                   elapsed_seconds=max(0, time.monotonic() - attempt_start))
                            emit("attempt_failed", ordinal=page["ordinal"], configuration=config_name, error=str(error))
                            current_attempt = None
                            continue
                        current_attempt.update(status="completed", result=result,
                                               elapsed_seconds=max(0, time.monotonic() - attempt_start))
                        page.update(status="completed", result=result, configuration=config_name,
                                    reused_generation=bool(result.get("reused_generation")),
                                    skipped_inference=bool(result.get("reused_generation")) and len(page["attempts"]) == 1)
                        current_attempt = None
                        break
                    page["elapsed_seconds"] = max(0, time.monotonic() - page_start)
                    if report["status"] == "time_limit":
                        emit("page_pending", ordinal=page["ordinal"], reason="time_limit")
                        snapshot()
                        break
                    if page["status"] != "completed":
                        page["status"] = "failed"
                        consecutive_failures += 1
                    else:
                        consecutive_failures = 0
                    emit("page_finished", ordinal=page["ordinal"], page=page,
                         consecutive_failures=consecutive_failures)
                    snapshot()
                    if consecutive_failures >= max_consecutive_failures:
                        report["status"] = "failure_limit"
                        break
                if report["status"] == "running":
                    report["status"] = "completed_with_failures" if any(page["status"] == "failed" for page in pages) else "completed"
    except KeyboardInterrupt:
        report["status"] = "interrupted"
        if current_attempt is not None:
            current_attempt.update(status="interrupted", elapsed_seconds=max(0, time.monotonic() - attempt_start))
            page["elapsed_seconds"] = max(0, time.monotonic() - page_start)
    except Exception as error:
        report.update(status="run_failed", error=str(error))
        if current_attempt is not None:
            current_attempt.update(status="failed", error=str(error), elapsed_seconds=max(0, time.monotonic() - attempt_start))
            page["elapsed_seconds"] = max(0, time.monotonic() - page_start)
    finally:
        report["stop_reason"] = report["status"]
        report["finished_at"] = utc_now()
        # A callback is advisory; a second Ctrl-C from the callback must not
        # prevent publication of the already collected final report.
        try:
            emit("run_stopped", status=report["status"])
        except KeyboardInterrupt:
            report["status"] = report["stop_reason"] = "interrupted"
        snapshot()
        with _mutation_lock(root):
            _refresh_checksums(root)
    return report
