"""Confirmed, resumable physical LFS archive. Never prunes the source cache.

Manifests contain operational identifiers, never remote credentials. Hash
verification proves byte preservation, not genealogical evidence.
"""
from __future__ import annotations

import ctypes
import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path


def _json(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _token(value) -> str:
    return hashlib.sha256(_json(value)).hexdigest()


def _safe_path(value: Path) -> Path:
    path = Path(os.path.abspath(Path(value).expanduser()))
    if ".." in Path(value).parts:
        raise ValueError("Parent traversal is forbidden")
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink():
            raise ValueError("Symlink paths are forbidden")
    return path


def _guard(value: Path, workspace_root: Path | None = None) -> Path:
    path = _safe_path(value)
    if path == Path(path.anchor):
        raise ValueError("A specific destination is required")
    if workspace_root is not None:
        workspace = _safe_path(workspace_root)
        protected = [workspace / p for p in ("sources", "research", ".git", "frontend/data")]
        if path == workspace or any(path == p or p in path.parents or path in p.parents for p in protected):
            raise ValueError("Protected workspace path")
    return path


def _hash(path: Path) -> tuple[str, int]:
    path = _safe_path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("Expected a regular file")
        digest, size = hashlib.sha256(), 0
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
                size += len(block)
        after = os.fstat(descriptor)
        if (metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError("File changed during verification")
        return digest.hexdigest(), size
    finally:
        os.close(descriptor)


def _identity(path: Path) -> list[int]:
    metadata = _safe_path(path).stat()
    return [metadata.st_dev, metadata.st_ino]


def _mount(path: Path) -> dict:
    cursor = _safe_path(path)
    while not cursor.exists():
        cursor = cursor.parent
    device = cursor.stat().st_dev
    while cursor.parent != cursor and cursor.parent.stat().st_dev == device:
        cursor = cursor.parent
    return {"path": str(cursor), "identity": _identity(cursor)}


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def _lock(root: Path):
    _safe_path(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(root / ".storage.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def _atomic_json(path: Path, payload) -> None:
    path = _safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, name = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(_json(payload) + b"\n")
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _rename_no_replace(source: Path, target: Path) -> None:
    """Atomically publish on filesystems without hardlinks, preserving races.

    A precheck followed by os.rename/os.replace could overwrite another writer.
    Use the native exclusive rename instead, or fail without publishing.
    """
    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        operation = getattr(library, "renamex_np", None)
        arguments = (os.fsencode(source), os.fsencode(target), 0x00000004)  # RENAME_EXCL
        argument_types = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
    elif sys.platform.startswith("linux"):
        operation = getattr(library, "renameat2", None)
        arguments = (-100, os.fsencode(source), -100, os.fsencode(target), 1)  # AT_FDCWD, RENAME_NOREPLACE
        argument_types = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    else:
        operation = None
    if operation is None:
        raise OSError(errno.ENOTSUP, "Atomic exclusive rename is unavailable", str(target))
    operation.argtypes = argument_types
    operation.restype = ctypes.c_int
    if operation(*arguments) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(target))


def _atomic_copy(source: Path, target: Path, oid: str, size: int, *, allow_existing: bool = True) -> bool:
    """Publish verified bytes without overwriting an existing object."""
    source, target = _safe_path(source), _safe_path(target)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if target.exists():
        if not allow_existing:
            raise FileExistsError("Restore destination already exists")
        if _hash(target) != (oid, size):
            raise ValueError("Existing destination object is corrupt")
        return False
    descriptor, name = tempfile.mkstemp(prefix=".pending-", dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(source_fd, "rb") as input_file:
                shutil.copyfileobj(input_file, output, 1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        if _hash(temporary) != (oid, size):
            raise ValueError("Copied bytes do not match planned hash")
        temporary.chmod(0o400)
        try:
            try:
                os.link(temporary, target)
            except OSError as error:
                if error.errno not in {errno.ENOTSUP, errno.EOPNOTSUPP, errno.EPERM}:
                    raise
                _rename_no_replace(temporary, target)
        except FileExistsError:
            if not allow_existing:
                raise FileExistsError("Restore destination appeared during copy") from None
            if _hash(target) != (oid, size):
                raise ValueError("Concurrent destination object is corrupt")
        _sync_directory(target.parent)
        return True
    finally:
        temporary.unlink(missing_ok=True)


def _git_directory(root: Path) -> Path:
    marker = _safe_path(root / ".git")
    if marker.is_dir():
        return marker
    result = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                            cwd=root, capture_output=True, text=True, check=True)
    return _safe_path(Path(result.stdout.strip()))


def _inventory(root: Path) -> tuple[dict, list[dict], list[str]]:
    git = _git_directory(root)
    objects = _safe_path(git / "lfs/objects")
    entries, invalid = [], []
    if objects.exists():
        for current, directories, files in os.walk(objects, followlinks=False):
            for name in directories[:]:
                if (Path(current) / name).is_symlink():
                    invalid.append(str((Path(current) / name).relative_to(objects)))
                    directories.remove(name)
            for name in files:
                path = Path(current) / name
                relative = path.relative_to(objects).as_posix()
                if (not re.fullmatch(r"[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]{64}", relative)
                        or path.is_symlink() or relative != f"{name[:2]}/{name[2:4]}/{name}"):
                    invalid.append(relative)
                    continue
                oid, size = _hash(path)
                if oid != name:
                    invalid.append(relative)
                    continue
                metadata = path.stat()
                entries.append({"oid": oid, "sha256": oid, "size": size, "path": relative,
                                "sourceIdentity": [metadata.st_dev, metadata.st_ino, metadata.st_mtime_ns]})
    identity = {"root": str(root), "gitDirectory": str(git), "gitIdentity": _identity(git)}
    identity["repositoryId"] = _token(identity)
    return identity, sorted(entries, key=lambda e: e["oid"]), sorted(invalid)


def plan_archive(root: Path, destination: Path) -> dict:
    root, destination = _safe_path(root), _guard(destination, root)
    git = _git_directory(root)
    if git == destination or git in destination.parents or destination in git.parents:
        raise ValueError("Archive must be outside Git storage")
    if root in destination.parents or destination in root.parents:
        raise ValueError("LFS archive must be external to repository")
    if not destination.is_dir():
        raise ValueError("Explicit existing destination directory is required")
    repository, entries, invalid = _inventory(root)
    plan = {"version": 1, "operation": "lfs_archive", "repository": repository,
            "destination": str(destination), "destinationIdentity": _identity(destination),
            "mount": _mount(destination), "objects": entries, "invalidObjects": invalid,
            "totalBytes": sum(e["size"] for e in entries)}
    plan["confirmToken"] = _token(plan)
    plan["freeBytes"] = shutil.disk_usage(destination).free
    plan["status"] = "ready" if not invalid else "blocked_invalid_objects"
    return plan


def apply_archive(root: Path, plan: dict, confirm_token: str) -> dict:
    supplied = {k: v for k, v in plan.items() if k not in {"confirmToken", "freeBytes", "status"}}
    if not confirm_token or confirm_token != plan.get("confirmToken") or _token(supplied) != confirm_token:
        raise ValueError("Invalid archive confirmation")
    destination = _guard(Path(plan["destination"]), _safe_path(root))
    # Validate before even creating the operational lock at the destination.
    if plan_archive(root, destination)["confirmToken"] != confirm_token:
        raise ValueError("Archive plan or destination changed; confirmation invalid")
    with _lock(destination):
        current = plan_archive(root, destination)
        if not confirm_token or confirm_token != plan.get("confirmToken") or current["confirmToken"] != confirm_token:
            raise ValueError("Archive plan or destination changed; confirmation invalid")
        # Also reject edits to the caller's plan; volatile free space is informational.
        if _token(supplied) != confirm_token or current["invalidObjects"]:
            raise ValueError("Invalid archive plan or physical LFS objects")
        previous = _manifest(destination) if (destination / "manifest.json").exists() else None
        if previous and previous["repository"]["repositoryId"] != current["repository"]["repositoryId"]:
            raise ValueError("Archive belongs to another repository")
        if previous and verify_archive(destination)["status"] != "verified":
            raise ValueError("Previous archive manifest failed verification")
        required = 0
        for entry in current["objects"]:
            target = _safe_path(destination / "objects" / entry["path"])
            if target.exists():
                if _hash(target) != (entry["oid"], entry["size"]):
                    raise ValueError("Existing archive object is corrupt")
            else:
                required += entry["size"]
        if shutil.disk_usage(destination).free < required + 65536:
            raise ValueError("Insufficient destination free space")
        source = Path(current["repository"]["gitDirectory"]) / "lfs/objects"
        for entry in current["objects"]:
            if _mount(destination) != current["mount"] or _identity(destination) != current["destinationIdentity"]:
                raise ValueError("Destination mount identity changed")
            _atomic_copy(source / entry["path"], destination / "objects" / entry["path"], entry["oid"], entry["size"])
        entries = {e["oid"]: e for e in (previous["objects"] if previous else [])}
        entries.update({e["oid"]: e for e in current["objects"]})
        manifest = {"version": 1, "kind": "physical_lfs_archive", "repository": current["repository"],
                    "objects": sorted(entries.values(), key=lambda e: e["oid"]), "confirmToken": confirm_token, "status": "verified"}
        _atomic_json(destination / "manifest.json", manifest)
        return verify_archive(destination)


def _manifest(destination: Path) -> dict:
    payload = json.loads(_safe_path(destination / "manifest.json").read_text())
    if payload.get("kind") != "physical_lfs_archive" or payload.get("version") != 1:
        raise ValueError("Unsupported LFS manifest")
    seen = set()
    for entry in payload["objects"]:
        oid = entry["oid"]
        if (not re.fullmatch(r"[0-9a-f]{64}", oid) or oid in seen
                or entry["path"] != f"{oid[:2]}/{oid[2:4]}/{oid}" or entry["sha256"] != oid
                or not isinstance(entry["size"], int) or entry["size"] < 0):
            raise ValueError("Invalid LFS manifest object")
        seen.add(oid)
    return payload


def verify_archive(destination: Path) -> dict:
    destination = _safe_path(destination)
    manifest = _manifest(destination)
    errors = []
    for entry in manifest["objects"]:
        try:
            valid = _hash(destination / "objects" / entry["path"]) == (entry["oid"], entry["size"])
        except (OSError, ValueError):
            valid = False
        if not valid:
            errors.append(entry["oid"])
    return {"status": "verified" if not errors else "corrupt", "objectCount": len(manifest["objects"]),
            "totalBytes": sum(e["size"] for e in manifest["objects"]), "errors": errors,
            "repositoryId": manifest["repository"]["repositoryId"], "safeToPrune": False}


def restore_drill(destination: Path, new_destination: Path) -> dict:
    destination = _safe_path(destination)
    manifest = _manifest(destination)
    target = _guard(new_destination, Path(manifest["repository"]["root"]))
    if target.exists() or target == destination or destination in target.parents or target in destination.parents:
        raise ValueError("Restore drill requires a new isolated destination")
    if verify_archive(destination)["status"] != "verified":
        raise ValueError("Archive verification failed")
    target.mkdir(parents=True, mode=0o700)
    for entry in manifest["objects"]:
        _atomic_copy(destination / "objects" / entry["path"], target / "objects" / entry["path"], entry["oid"], entry["size"], allow_existing=False)
    _atomic_json(target / "manifest.json", manifest)
    report = verify_archive(target)
    report["destination"] = str(target)
    return report
