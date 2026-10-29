"""Immutable content addressed derivatives with explicit lineage and retention.

The store is operational, never a canonical evidence database. Collection only
touches store-owned unreferenced rebuildable/temporary objects after confirmation.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path

from .lfs_archive import _atomic_copy, _guard, _hash, _identity, _json, _lock, _safe_path, _token


RETENTION_CLASSES = frozenset({"keep", "rebuildable", "temporary", "archived"})
PRIVACY_CLASSES = frozenset({"public", "family_private", "strict_private"})


class DerivedStore:
    def __init__(self, root: Path, *, workspace_root: Path):
        self.workspace_root = _safe_path(workspace_root)
        self.root = _guard(root, self.workspace_root)

    def _object(self, oid: str) -> Path:
        if not isinstance(oid, str) or not re.fullmatch(r"[0-9a-f]{64}", oid):
            raise ValueError("Invalid artifact OID")
        return _safe_path(self.root / "objects" / oid[:2] / oid)

    def _state(self) -> tuple[dict, str]:
        path = _safe_path(self.root / "audit.jsonl")
        state, previous = {"objects": {}, "refs": {}}, "0" * 64
        if path.exists():
            with path.open("rb") as journal:
                for line in journal:
                    if not line.endswith(b"\n"):
                        raise ValueError("Incomplete derived audit journal record")
                    event = json.loads(line)
                    claimed = event.pop("hash")
                    if event["previousHash"] != previous or _token(event) != claimed:
                        raise ValueError("Derived audit journal integrity failed")
                    state, previous = event["state"], claimed
        return state, previous

    def _record(self, operation: str, state: dict, details: dict) -> None:
        _, previous = self._state()
        event = {"version": 1, "operation": operation, "previousHash": previous,
                 "details": details, "state": state}
        event["hash"] = _token(event)
        descriptor = os.open(_safe_path(self.root / "audit.jsonl"),
                             os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "ab") as output:
            output.write(_json(event) + b"\n")
            output.flush()
            os.fsync(output.fileno())

    def put(self, source: bytes | Path, *, retention: str = "rebuildable",
            lineage: tuple[str, ...] = (), backing_ids: tuple[str, ...] = (),
            source_ids: tuple[str, ...] = (), privacy: str = "family_private") -> dict:
        if retention not in RETENTION_CLASSES:
            raise ValueError("Unknown retention class")
        if privacy not in PRIVACY_CLASSES:
            raise ValueError("Unknown derivative privacy class")
        for identifiers in (lineage, backing_ids, source_ids):
            if any(not isinstance(value, str) or not value or len(value) > 256 for value in identifiers):
                raise ValueError("Provenance identifiers must be explicit nonempty strings")
        with _lock(_guard(self.root, self.workspace_root)):
            state, _ = self._state()
            for parent in lineage:
                if parent not in state["objects"]:
                    raise ValueError("Lineage artifact must exist in this store")
            temporary = None
            if isinstance(source, bytes):
                descriptor, name = tempfile.mkstemp(prefix=".input-", dir=self.root)
                temporary = Path(name)
                with os.fdopen(descriptor, "wb") as output:
                    output.write(source)
                source_path = temporary
                oid, size = hashlib.sha256(source).hexdigest(), len(source)
            else:
                source_path = _safe_path(source)
                oid, size = _hash(source_path)
            try:
                created = _atomic_copy(source_path, self._object(oid), oid, size)
                existing = state["objects"].get(oid)
                entry = {"oid": oid, "sha256": oid, "size": size, "retention": retention,
                         "lineage": sorted(set(lineage)), "backingIds": sorted(set(backing_ids)),
                         "sourceIds": sorted(set(source_ids)), "privacy": privacy,
                         "status": "unreviewed_derivative"}
                if existing:
                    # Deduplication may add provenance but never weakens protection.
                    rank = {"temporary": 0, "rebuildable": 1, "archived": 2, "keep": 3}
                    entry["retention"] = max((retention, existing["retention"]), key=rank.get)
                    privacy_rank = {"public": 0, "family_private": 1, "strict_private": 2}
                    entry["privacy"] = max((privacy, existing["privacy"]), key=privacy_rank.get)
                    for key in ("lineage", "backingIds", "sourceIds"):
                        entry[key] = sorted(set(entry[key]) | set(existing[key]))
                if oid in entry["lineage"]:
                    raise ValueError("Self-referential lineage is forbidden")
                state["objects"][oid] = entry
                self._record("put", state, {"oid": oid, "deduplicated": not created})
                return dict(entry, deduplicated=not created)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

    def add_ref(self, name: str, oid: str) -> dict:
        if not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", name):
            raise ValueError("Invalid explicit reference name")
        self._object(oid)
        with _lock(_guard(self.root, self.workspace_root)):
            state, _ = self._state()
            if oid not in state["objects"]:
                raise ValueError("Reference artifact does not exist")
            if name in state["refs"] and state["refs"][name] != oid:
                raise ValueError("Remove the old reference explicitly before replacing it")
            state["refs"][name] = oid
            self._record("add_ref", state, {"name": name, "oid": oid})
            return {"name": name, "oid": oid}

    def remove_ref(self, name: str) -> dict:
        with _lock(_guard(self.root, self.workspace_root)):
            state, _ = self._state()
            if name not in state["refs"]:
                raise ValueError("Unknown reference")
            oid = state["refs"].pop(name)
            self._record("remove_ref", state, {"name": name, "oid": oid})
            return {"name": name, "oid": oid, "status": "removed"}

    def _report(self) -> dict:
        state, journal_hash = self._state()
        referenced = set(state["refs"].values())
        for entry in state["objects"].values():
            referenced.update(entry["lineage"])
        entries = []
        for oid, entry in sorted(state["objects"].items()):
            path = self._object(oid)
            valid = path.is_file() and _hash(path) == (oid, entry["size"])
            entries.append(dict(entry, referenced=oid in referenced, verified=valid,
                                identity=_identity(path) if path.exists() else None,
                                collectable=valid and oid not in referenced and entry["retention"] in {"rebuildable", "temporary"}))
        return {"version": 1, "root": str(self.root), "rootIdentity": _identity(self.root),
                "journalHash": journal_hash, "entries": entries, "refs": state["refs"],
                "status": "verified" if all(e["verified"] for e in entries) else "corrupt"}

    def report(self) -> dict:
        if not self.root.exists():
            return {"version": 1, "root": str(self.root), "entries": [], "refs": {}, "status": "missing"}
        _guard(self.root, self.workspace_root)
        return self._report()

    def _plan(self) -> dict:
        report = self._report()
        plan = dict(report, operation="derived_gc", candidates=[e["oid"] for e in report["entries"] if e["collectable"]],
                    totalBytes=sum(e["size"] for e in report["entries"] if e["collectable"]))
        plan["confirmToken"] = _token(plan)
        return plan

    def plan_gc(self) -> dict:
        _guard(self.root, self.workspace_root)
        if not self.root.exists():
            raise ValueError("Store does not exist; initialize it with put")
        return self._plan()

    def apply_gc(self, plan: dict, confirm_token: str) -> dict:
        with _lock(_guard(self.root, self.workspace_root)):
            supplied = {k: v for k, v in plan.items() if k != "confirmToken"}
            current = self._plan()
            if (not confirm_token or _token(supplied) != confirm_token
                    or plan.get("confirmToken") != confirm_token or current["confirmToken"] != confirm_token
                    or current["status"] != "verified"):
                raise ValueError("GC confirmation invalid or store changed")
            state, _ = self._state()
            deleted = []
            # An intent record prevents stale plans from being reused after a crash.
            self._record("gc_intent", state, {"oids": current["candidates"], "confirmToken": confirm_token})
            for oid in current["candidates"]:
                entry = next(item for item in current["entries"] if item["oid"] == oid)
                root_descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    root_metadata = os.fstat(root_descriptor)
                    if [root_metadata.st_dev, root_metadata.st_ino] != current["rootIdentity"]:
                        raise ValueError("Derived store changed during GC")
                    objects_descriptor = os.open(
                        "objects", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=root_descriptor,
                    )
                    try:
                        prefix_descriptor = os.open(
                            oid[:2], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=objects_descriptor,
                        )
                        try:
                            metadata = os.stat(oid, dir_fd=prefix_descriptor, follow_symlinks=False)
                            if (not stat.S_ISREG(metadata.st_mode)
                                    or [metadata.st_dev, metadata.st_ino] != entry["identity"]
                                    or metadata.st_size != entry["size"]):
                                raise ValueError("Derived object changed during GC")
                            # The directory descriptor pins the validated CAS directory.
                            # If its pathname is replaced with a symlink concurrently,
                            # unlink still applies only to this store-owned directory.
                            os.unlink(oid, dir_fd=prefix_descriptor)
                        finally:
                            os.close(prefix_descriptor)
                    finally:
                        os.close(objects_descriptor)
                finally:
                    os.close(root_descriptor)
                del state["objects"][oid]
                deleted.append(oid)
                self._record("gc_deleted", state, {"oid": oid})
            return {"status": "completed", "deletedOids": deleted, "deletedBytes": current["totalBytes"]}
