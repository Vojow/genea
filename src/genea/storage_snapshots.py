"""Encrypted database checkpoints and an append-only hash-chained journal.

This is a checkpoint journal, not semantic change replay. Restore always copies
to a new isolated file and validates it through the existing storage facade.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
import uuid
from pathlib import Path
from typing import Callable

from .lfs_archive import (_atomic_copy, _atomic_json, _guard, _identity, _json,
                          _lock, _mount, _safe_path, _sync_directory, _token)
from .lfs_archive import _rename_no_replace
from .storage import DatabaseBackend, SQLCipherBackend


class _SnapshotBackend(SQLCipherBackend):
    @property
    def path(self) -> Path:
        return self.root / self.filename


def _regular_open(path: Path) -> int:
    descriptor = os.open(_safe_path(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ValueError("Snapshot store entries must be regular files")
    return descriptor


def _file_signature(metadata) -> tuple:
    return (metadata.st_dev, metadata.st_ino, metadata.st_size,
            metadata.st_mtime_ns, metadata.st_ctime_ns, metadata.st_nlink)


def _read_regular(path: Path) -> bytes:
    with os.fdopen(_regular_open(path), "rb") as source:
        before = _file_signature(os.fstat(source.fileno()))
        content = source.read()
        if before != _file_signature(os.fstat(source.fileno())):
            raise ValueError("Snapshot store file changed during read")
    return content


def _hash(path: Path) -> tuple[str, int]:
    """Unlike a blocking open, reject special-file replacement races safely."""
    with os.fdopen(_regular_open(path), "rb") as source:
        before = _file_signature(os.fstat(source.fileno()))
        digest, size = hashlib.sha256(), 0
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
        if before != _file_signature(os.fstat(source.fileno())):
            raise ValueError("Snapshot store file changed during verification")
    return digest.hexdigest(), size


def _opener(backend: DatabaseBackend, open_snapshot: Callable | None) -> Callable:
    if open_snapshot is not None:
        return open_snapshot
    if isinstance(backend, SQLCipherBackend):
        return lambda path: _SnapshotBackend(path.parent, backend.key_provider, path.name)
    raise ValueError("Encrypted backend requires an explicit snapshot backend factory")


def _integrity(backend: DatabaseBackend) -> dict:
    if not backend.encrypted:
        raise ValueError("Snapshots require an encrypted database backend")
    report = backend.integrity_report()
    if (report.get("integrity_check") != "ok" or report.get("foreign_key_issues")
            or report.get("cipher_integrity_check") not in ([], ())
            or not report.get("cipher_version")):
        raise ValueError("Encrypted database integrity verification failed")
    return report


def _read_journal(destination: Path) -> list[dict]:
    path = _safe_path(destination / "checkpoint-journal.jsonl")
    previous, records = "0" * 64, []
    if path.exists():
        for line in _read_regular(path).splitlines(keepends=True):
            if not line.endswith(b"\n"):
                raise ValueError("Incomplete checkpoint journal record")
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError("Invalid checkpoint journal record")
            claimed = record.pop("hash", None)
            if (record.get("version") != 1 or record.get("kind") != "snapshot_checkpoint"
                    or type(record.get("sequence")) is not int
                    or record.get("sequence") != len(records) + 1
                    or record.get("previousHash") != previous or _token(record) != claimed):
                raise ValueError("Checkpoint journal hash chain failed")
            oid = record.get("snapshotId")
            base = record.get("baseSnapshotId")
            if (not isinstance(oid, str) or not re.fullmatch(r"[0-9a-f]{64}", oid)
                    or record.get("manifest") != oid + ".manifest.json"
                    or not isinstance(record.get("manifestSha256"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", record["manifestSha256"])
                    or (base is not None and (not isinstance(base, str) or not re.fullmatch(r"[0-9a-f]{64}", base)))):
                raise ValueError("Invalid snapshot checkpoint record")
            record["hash"] = claimed
            records.append(record)
            previous = claimed
    return records


def _load_manifest(destination: Path, name: str) -> dict:
    if not isinstance(name, str) or Path(name).name != name or not name.endswith(".manifest.json"):
        raise ValueError("Invalid snapshot manifest name")
    manifest = json.loads(_read_regular(destination / name))
    if not isinstance(manifest, dict):
        raise ValueError("Invalid encrypted snapshot manifest")
    snapshot = manifest.get("snapshotId")
    if (not isinstance(snapshot, str) or len(snapshot) != 64
            or any(c not in "0123456789abcdef" for c in snapshot)
            or manifest.get("filename") != snapshot + ".sqlite3"
            or manifest.get("sha256") != snapshot or manifest.get("encrypted") is not True
            or manifest.get("version") != 1 or manifest.get("kind") != "encrypted_database_snapshot"
            or type(manifest.get("size")) is not int or manifest["size"] < 1
            or name != snapshot + ".manifest.json"):
        raise ValueError("Invalid encrypted snapshot manifest")
    return manifest


def _latest_ids(records: list[dict], retired: set[str], keep_last: int) -> list[str]:
    result = []
    for record in reversed(records):
        oid = record["snapshotId"]
        if oid not in retired and oid not in result:
            result.append(oid)
        if len(result) == keep_last:
            break
    return result


def _keep_count(keep_last: int) -> None:
    if type(keep_last) is not int or keep_last < 1:
        raise ValueError("keep_last must be a positive integer")


def _retiring_name(oid: str) -> str:
    return ".retiring-" + oid + ".sqlite3"


def _read_retirements(destination: Path, records: list[dict]) -> list[dict]:
    path = _safe_path(destination / "retention-journal.jsonl")
    previous, retired, events = "0" * 64, set(), []
    if not path.exists():
        return events
    for line in _read_regular(path).splitlines(keepends=True):
        if not line.endswith(b"\n"):
            raise ValueError("Incomplete retention journal record")
        event = json.loads(line)
        if not isinstance(event, dict):
            raise ValueError("Invalid retention journal record")
        claimed = event.pop("hash", None)
        if (event.get("version") != 1 or event.get("kind") != "snapshot_retirement"
                or type(event.get("sequence")) is not int
                or event.get("sequence") != len(events) + 1
                or event.get("previousHash") != previous or _token(event) != claimed):
            raise ValueError("Retention journal hash chain failed")
        sequence, head = event.get("checkpointSequence"), event.get("checkpointHeadSequence")
        _keep_count(event.get("keepLast"))
        if (type(sequence) is not int or type(head) is not int
                or not 1 <= sequence <= head <= len(records)):
            raise ValueError("Invalid retirement checkpoint anchor")
        checkpoint = records[sequence - 1]
        oid = checkpoint["snapshotId"]
        retained = _latest_ids(records[:head], retired, event["keepLast"])
        if (event.get("snapshotId") != oid or oid in retired or oid in retained
                or event.get("checkpointHash") != checkpoint["hash"]
                or event.get("manifestSha256") != checkpoint["manifestSha256"]
                or event.get("checkpointHeadHash") != records[head - 1]["hash"]
                or event.get("retainedSnapshotIds") != retained
                or event.get("stagedFilename") != _retiring_name(oid)
                or not isinstance(event.get("quarantineName"), str)
                or not re.fullmatch(r"\.retention-[0-9a-f]{32}", event["quarantineName"])):
            raise ValueError("Retirement is not anchored to a verified checkpoint and policy")
        event["hash"] = claimed
        events.append(event)
        retired.add(oid)
        previous = claimed
    return events


def _quarantine_payload(destination: Path, event: dict) -> Path:
    directory = _safe_path(destination / event["quarantineName"])
    if directory.exists():
        metadata = directory.stat()
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_mode & 0o077:
            raise ValueError("Snapshot retirement quarantine must be a private directory")
        for child in directory.iterdir():
            if child.name != event["snapshotId"] + ".sqlite3" or child.is_symlink() or not child.is_file():
                raise ValueError("Unknown or unsafe snapshot retirement quarantine entry")
    return _safe_path(directory / (event["snapshotId"] + ".sqlite3"))


def _verified_state(destination: Path) -> tuple[list[dict], list[dict], dict[str, dict]]:
    records = _read_journal(destination)
    retirements = _read_retirements(destination, records)
    retired = {r["snapshotId"] for r in retirements}
    retirement_by_id = {r["snapshotId"]: r for r in retirements}
    for event in retirements:
        if any(record["snapshotId"] == event["snapshotId"]
               for record in records[event["checkpointHeadSequence"]:]):
            raise ValueError("Retired snapshot cannot be reintroduced by a later checkpoint")
    known, manifests = set(), {}
    for record in records:
        manifest_path = destination / record["manifest"]
        manifest = _load_manifest(destination, record["manifest"])
        if (_hash(manifest_path)[0] != record["manifestSha256"]
                or record["snapshotId"] != manifest["snapshotId"]
                or record.get("baseSnapshotId") != manifest.get("baseSnapshotId")
                or (manifest.get("baseSnapshotId") is not None and manifest["baseSnapshotId"] not in known)):
            raise ValueError("Snapshot checkpoint verification failed")
        known.add(manifest["snapshotId"])
        manifests[manifest["snapshotId"]] = manifest
    for oid, manifest in manifests.items():
        payload = _safe_path(destination / manifest["filename"])
        staged = _safe_path(destination / _retiring_name(oid))
        if staged.exists() and oid not in retired:
            raise ValueError("Unrecorded staged snapshot retirement")
        paths = [payload, staged]
        if oid in retired:
            paths.append(_quarantine_payload(destination, retirement_by_id[oid]))
        for path in paths:
            if path.exists() and _hash(path) != (oid, manifest["size"]):
                raise ValueError("Snapshot checkpoint verification failed")
        if oid not in retired and not payload.exists():
            raise ValueError("Missing snapshot payload without validated retirement")
        if sum(path.exists() for path in paths) > 1:
            raise ValueError("Conflicting snapshot retirement payloads; operator recovery required")
    return records, retirements, manifests


def verify_checkpoint_journal(destination: Path) -> dict:
    destination = _safe_path(destination)
    records, retirements, manifests = _verified_state(destination)
    retired = {r["snapshotId"] for r in retirements}
    retirement_by_id = {r["snapshotId"]: r for r in retirements}
    return {"status": "verified", "kind": "checkpoint_journal", "checkpointCount": len(records),
            "lastHash": records[-1]["hash"] if records else "0" * 64,
            "lastSnapshotId": records[-1]["snapshotId"] if records else None,
            "availableSnapshotCount": len(set(manifests) - retired),
            "retiredSnapshotCount": len(retired),
            "pendingRetirementCount": sum(
                (destination / manifests[oid]["filename"]).exists()
                or (destination / _retiring_name(oid)).exists()
                or _quarantine_payload(destination, retirement_by_id[oid]).exists() for oid in retired),
            "lastRetentionHash": retirements[-1]["hash"] if retirements else "0" * 64}


def _retention_destination(destination: Path, workspace_root: Path) -> Path:
    workspace = _safe_path(workspace_root)
    destination = _guard(destination, workspace)
    if workspace in destination.parents or destination in workspace.parents:
        raise ValueError("Private snapshots must be outside workspace")
    if not destination.is_dir():
        raise ValueError("Existing snapshot store directory is required")
    return destination


def plan_snapshot_retention(destination: Path, *, keep_last: int = 7, workspace_root: Path) -> dict:
    """Inspect a recognized checkpoint store without changing any files."""
    _keep_count(keep_last)
    destination = _retention_destination(destination, workspace_root)
    records, retirements, manifests = _verified_state(destination)
    retired = {r["snapshotId"] for r in retirements}
    retirement_by_id = {r["snapshotId"]: r for r in retirements}
    quarantine_by_name = {r["quarantineName"]: r for r in retirements}
    retained = _latest_ids(records, retired, keep_last)
    allowed = {".storage.lock", "checkpoint-journal.jsonl", "retention-journal.jsonl"}
    for oid, manifest in manifests.items():
        allowed.update((oid + ".manifest.json", manifest["filename"]))
        if oid in retired:
            allowed.add(_retiring_name(oid))
            allowed.add(retirement_by_id[oid]["quarantineName"])
    inventory, empty_quarantines = [], []
    for path in sorted(destination.iterdir()):
        if path.name in quarantine_by_name:
            payload = _quarantine_payload(destination, quarantine_by_name[path.name])
            inventory.append({"filename": path.name, "kind": "quarantine", "identity": _identity(path)})
            if payload.exists():
                digest, size = _hash(payload)
                inventory.append({"filename": payload.relative_to(destination).as_posix(),
                                  "sha256": digest, "size": size, "identity": _identity(payload)})
            else:
                empty_quarantines.append({"filename": path.name, "identity": _identity(path)})
            continue
        if path.name not in allowed or path.is_symlink() or not path.is_file():
            raise ValueError("Unknown or unsafe snapshot store entry: " + path.name)
        if path.name == ".storage.lock":
            continue  # Operational locking is not part of checkpoint content.
        digest, size = _hash(path)
        inventory.append({"filename": path.name, "sha256": digest, "size": size,
                          "identity": _identity(path)})
    candidates = []
    for oid, manifest in manifests.items():
        if oid in retained:
            continue
        payload = destination / manifest["filename"]
        staged = destination / _retiring_name(oid)
        path = payload if payload.exists() else staged
        if not path.exists() and oid in retired:
            path = _quarantine_payload(destination, retirement_by_id[oid])
        if not path.exists():
            continue
        metadata = path.stat()
        if metadata.st_nlink != 1:
            raise ValueError("Hardlinked snapshot payload cannot be retired")
        candidates.append({"snapshotId": oid, "filename": path.relative_to(destination).as_posix(), "size": manifest["size"],
                           "sha256": oid, "identity": _identity(path), "alreadyRetired": oid in retired})
    plan = {"version": 1, "operation": "snapshot_retention", "status": "ready",
            "destination": str(destination), "destinationIdentity": _identity(destination),
            "mount": _mount(destination), "keepLast": keep_last,
            "checkpointHash": records[-1]["hash"] if records else "0" * 64,
            "retentionHash": retirements[-1]["hash"] if retirements else "0" * 64,
            "retainedSnapshotIds": retained, "candidates": candidates,
            "candidateCount": len(candidates), "totalBytes": sum(c["size"] for c in candidates),
            "inventory": inventory, "emptyQuarantines": empty_quarantines}
    plan["confirmToken"] = _token(plan)
    return plan


def _append_retirement(destination: Path, event: dict) -> None:
    event["hash"] = _token(event)
    descriptor = os.open(_safe_path(destination / "retention-journal.jsonl"),
                         os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "ab") as journal:
        journal.write(_json(event) + b"\n")
        journal.flush()
        os.fsync(journal.fileno())
    _sync_directory(destination)


def _validate_retirement_payload(path: Path, candidate: dict) -> None:
    if (_identity(path) != candidate["identity"] or path.stat().st_nlink != 1
            or _hash(path) != (candidate["sha256"], candidate["size"])):
        raise ValueError("Snapshot retirement payload changed; preserving files")


def _unlink_quarantined(directory: Path, candidate: dict, directory_identity: list[int]) -> None:
    """Delete through a pinned private directory, never a mutable store filename."""
    descriptor = os.open(_safe_path(directory), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if [metadata.st_dev, metadata.st_ino] != directory_identity or metadata.st_mode & 0o077:
            raise ValueError("Snapshot quarantine identity changed; preserving files")
        name = candidate["snapshotId"] + ".sqlite3"
        file_descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        with os.fdopen(file_descriptor, "rb") as source:
            metadata = os.fstat(source.fileno())
            before = _file_signature(metadata)
            digest, size = hashlib.sha256(), 0
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                    or [metadata.st_dev, metadata.st_ino] != candidate["identity"]):
                raise ValueError("Quarantined snapshot identity changed; preserving files")
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
                size += len(block)
            current = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if (before != _file_signature(os.fstat(source.fileno())) or before != _file_signature(current)
                    or (digest.hexdigest(), size) != (candidate["sha256"], candidate["size"])):
                raise ValueError("Quarantined snapshot changed; preserving files")
            os.unlink(name, dir_fd=descriptor)
            os.fsync(descriptor)
    finally:
        os.close(descriptor)


def apply_snapshot_retention(destination: Path, confirm_token: str, *, keep_last: int = 7,
                             workspace_root: Path) -> dict:
    """Retire exact payloads, preserving both append-only provenance journals."""
    destination = _retention_destination(destination, workspace_root)
    # Refuse stale/invalid plans before creating or modifying even the lock file.
    if not confirm_token or plan_snapshot_retention(
            destination, keep_last=keep_last, workspace_root=workspace_root)["confirmToken"] != confirm_token:
        raise ValueError("Snapshot retention confirmation invalid or store changed")
    with _lock(destination):
        plan = plan_snapshot_retention(destination, keep_last=keep_last, workspace_root=workspace_root)
        if plan["confirmToken"] != confirm_token:
            raise ValueError("Snapshot retention confirmation invalid or store changed")
        records, retirements, _ = _verified_state(destination)
        retired = {r["snapshotId"] for r in retirements}
        retirement_by_id = {r["snapshotId"]: r for r in retirements}
        removed, cleaned_quarantines = [], set()
        for candidate in plan["candidates"]:
            if (_identity(destination) != plan["destinationIdentity"] or _mount(destination) != plan["mount"]):
                raise ValueError("Snapshot store identity or mount changed")
            oid = candidate["snapshotId"]
            source = _safe_path(destination / candidate["filename"])
            staged = _safe_path(destination / _retiring_name(oid))
            original = _safe_path(destination / (oid + ".sqlite3"))
            _validate_retirement_payload(source, candidate)
            if oid not in retired:
                checkpoint = next(r for r in reversed(records) if r["snapshotId"] == oid)
                event = {"version": 1, "kind": "snapshot_retirement", "sequence": len(retirements) + 1,
                         "snapshotId": oid, "checkpointSequence": checkpoint["sequence"],
                         "checkpointHash": checkpoint["hash"], "manifestSha256": checkpoint["manifestSha256"],
                         "checkpointHeadSequence": records[-1]["sequence"], "checkpointHeadHash": records[-1]["hash"],
                         "keepLast": keep_last, "retainedSnapshotIds": _latest_ids(records, retired, keep_last),
                         "stagedFilename": staged.name, "quarantineName": ".retention-" + uuid.uuid4().hex,
                         "createdAt": time.time(),
                         "previousHash": retirements[-1]["hash"] if retirements else "0" * 64}
                _append_retirement(destination, event)
                retirements.append(event)
                retired.add(oid)
                retirement_by_id[oid] = event
            quarantined = _quarantine_payload(destination, retirement_by_id[oid])
            if source == original:
                _validate_retirement_payload(source, candidate)
                _rename_no_replace(source, staged)
                _sync_directory(destination)
            if original.exists() or original.is_symlink():
                raise ValueError("Conflicting new snapshot payload; preserving original and staged files")
            if source != quarantined:
                _validate_retirement_payload(staged, candidate)
                if not quarantined.parent.exists():
                    quarantined.parent.mkdir(mode=0o700)
                    _sync_directory(destination)
                directory_identity = _identity(quarantined.parent)
                _rename_no_replace(staged, quarantined)
                _sync_directory(quarantined.parent)
                _sync_directory(destination)
            else:
                directory_identity = _identity(quarantined.parent)
            _validate_retirement_payload(quarantined, candidate)
            if original.exists() or original.is_symlink() or staged.exists() or staged.is_symlink():
                raise ValueError("Conflicting new snapshot payload; preserving all files")
            _unlink_quarantined(quarantined.parent, candidate, directory_identity)
            if _identity(quarantined.parent) != directory_identity:
                raise ValueError("Snapshot quarantine directory changed; preserving directory")
            quarantined.parent.rmdir()
            cleaned_quarantines.add(quarantined.parent.name)
            _sync_directory(destination)
            removed.append(oid)
        for entry in plan["emptyQuarantines"]:
            if entry["filename"] in cleaned_quarantines:
                continue
            directory = _safe_path(destination / entry["filename"])
            if _identity(directory) != entry["identity"] or any(directory.iterdir()):
                raise ValueError("Empty snapshot quarantine changed; preserving files")
            directory.rmdir()
            _sync_directory(destination)
        return dict(verify_checkpoint_journal(destination), operation="snapshot_retention",
                    removedSnapshotIds=removed, removedBytes=plan["totalBytes"],
                    retainedSnapshotIds=plan["retainedSnapshotIds"])


def create_snapshot(backend: DatabaseBackend, destination: Path, *, workspace_root: Path,
                    base_snapshot: str | None = None, open_snapshot: Callable | None = None) -> dict:
    if not backend.encrypted:
        raise ValueError("Snapshots require an encrypted database backend")
    workspace = _safe_path(workspace_root)
    destination = _guard(destination, workspace)
    if workspace in destination.parents or destination in workspace.parents:
        raise ValueError("Private snapshots must be outside workspace")
    open_copy = _opener(backend, open_snapshot)
    with _lock(destination):
        verify_checkpoint_journal(destination)
        records, retirements, _ = _verified_state(destination)
        retired = {r["snapshotId"] for r in retirements}
        base = base_snapshot if base_snapshot is not None else (records[-1]["snapshotId"] if records else None)
        if base is not None and (base not in {r["snapshotId"] for r in records} or base in retired):
            raise ValueError("Base snapshot must be an available verified checkpoint in this store")
        source_integrity = _integrity(backend)
        identity, mount = _identity(destination), _mount(destination)
        temporary = destination / (".pending-" + uuid.uuid4().hex + ".sqlite3")
        try:
            backend.backup(temporary)
            copy_backend = open_copy(temporary)
            if _safe_path(copy_backend.path) != temporary:
                raise ValueError("Snapshot factory opened a different database")
            integrity = _integrity(copy_backend)
            # A broken adapter claiming encryption must not publish a plaintext DB.
            with temporary.open("rb") as source:
                if source.read(16) == b"SQLite format 3\x00":
                    raise ValueError("Backup produced a plaintext database")
            oid, size = _hash(temporary)
            if oid in retired:
                raise ValueError("Snapshot payload was retired; create a new checkpoint instead")
            if identity != _identity(destination) or mount != _mount(destination):
                raise ValueError("Snapshot destination mount changed")
            filename = oid + ".sqlite3"
            _atomic_copy(temporary, destination / filename, oid, size)
            manifest_name = oid + ".manifest.json"
            manifest_path = destination / manifest_name
            if manifest_path.exists():
                existing = _load_manifest(destination, manifest_name)
                if oid not in {r["snapshotId"] for r in records}:
                    raise ValueError("Unjournaled snapshot requires operator recovery")
                return dict(existing, manifestPath=str(manifest_path), status="deduplicated")
            manifest = {"version": 1, "kind": "encrypted_database_snapshot", "snapshotId": oid,
                        "sha256": oid, "size": size, "filename": filename, "encrypted": True,
                        "backendDriver": backend.driver, "sourceId": _token({"path": str(backend.path)}),
                        "createdAt": time.time(), "baseSnapshotId": base,
                        "sourceIntegrity": source_integrity, "integrity": integrity}
            _atomic_json(manifest_path, manifest)
            record = {"version": 1, "kind": "snapshot_checkpoint", "sequence": len(records) + 1,
                      "snapshotId": oid, "baseSnapshotId": base, "manifest": manifest_name,
                      "manifestSha256": _hash(manifest_path)[0], "createdAt": manifest["createdAt"],
                      "previousHash": records[-1]["hash"] if records else "0" * 64}
            record["hash"] = _token(record)
            descriptor = os.open(_safe_path(destination / "checkpoint-journal.jsonl"),
                                 os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "ab") as journal:
                journal.write(_json(record) + b"\n")
                journal.flush()
                os.fsync(journal.fileno())
            _sync_directory(destination)
            verify_checkpoint_journal(destination)
            return dict(manifest, manifestPath=str(manifest_path), status="verified")
        finally:
            temporary.unlink(missing_ok=True)


def snapshot_if_due(backend: DatabaseBackend, destination: Path, *, interval_seconds: float,
                    workspace_root: Path, open_snapshot: Callable | None = None,
                    now: float | None = None, keep_last: int = 7) -> dict:
    """A scheduler may invoke this periodically; it never installs a scheduler."""
    if interval_seconds <= 0:
        raise ValueError("Snapshot interval must be positive")
    _keep_count(keep_last)
    destination = _guard(destination, workspace_root)
    verify_checkpoint_journal(destination)
    records = _read_journal(destination)
    clock = time.time() if now is None else now
    if records and clock - records[-1]["createdAt"] < interval_seconds:
        result = {"status": "not_due", "nextDueAt": records[-1]["createdAt"] + interval_seconds}
    else:
        result = create_snapshot(backend, destination, workspace_root=workspace_root, open_snapshot=open_snapshot)
    plan = plan_snapshot_retention(destination, keep_last=keep_last, workspace_root=workspace_root)
    retention = apply_snapshot_retention(destination, plan["confirmToken"], keep_last=keep_last,
                                         workspace_root=workspace_root)
    return dict(result, retention=retention)


def restore_drill(backend: DatabaseBackend, destination: Path, snapshot_id: str, new_path: Path,
                  *, workspace_root: Path, open_snapshot: Callable | None = None) -> dict:
    destination = _safe_path(destination)
    target = _guard(new_path, workspace_root)
    if (target.exists() or destination == target or destination in target.parents
            or target in destination.parents or any(Path(str(target) + suffix).exists() for suffix in ("-wal", "-shm", "-journal"))):
        raise ValueError("Restore drill requires a new isolated test database path")
    verify_checkpoint_journal(destination)
    if snapshot_id in {r["snapshotId"] for r in _read_retirements(destination, _read_journal(destination))}:
        raise ValueError("Snapshot was retired by retention and is unavailable for restore")
    if snapshot_id not in {r["snapshotId"] for r in _read_journal(destination)}:
        raise ValueError("Snapshot is not a verified checkpoint")
    manifest = _load_manifest(destination, snapshot_id + ".manifest.json")
    opener = _opener(backend, open_snapshot)
    _atomic_copy(destination / manifest["filename"], target, snapshot_id, manifest["size"], allow_existing=False)
    copy_backend = opener(target)
    if _safe_path(copy_backend.path) != target:
        raise ValueError("Restore factory opened a different database")
    integrity = _integrity(copy_backend)
    return {"status": "verified", "snapshotId": snapshot_id, "destination": str(target), "integrity": integrity}
