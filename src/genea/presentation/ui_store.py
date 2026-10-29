"""Bounded, private JSON derivatives for freshly rebuilt local UI payloads.

Only current.json, previous.json and build.json are payload slots. This store
never opens a database, selects a stale payload, or removes unrelated files.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any

DEFAULT_MAX_PAYLOAD_BYTES = 128 * 1024 * 1024
DEFAULT_MIN_FREE_BYTES = 512 * 1024 * 1024
_SLOTS = ("current.json", "previous.json", "build.json")
_BUFFER_BYTES = 64 * 1024
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


class UIStoreError(RuntimeError):
    """The private derivative cannot be safely published."""


class UIStoreLimitError(UIStoreError):
    """The payload size or available-space budget was exceeded."""


def current_path(root: Path) -> Path:
    return Path(root) / ".genea" / "ui-store" / "current.json"


def _available_bytes(directory_fd: int) -> int:
    usage = os.fstatvfs(directory_fd)
    return usage.f_bavail * usage.f_frsize


@contextmanager
def _store_directory(root: Path):
    # Open each component without following symlinks, including the workspace.
    # All later mutation is relative to this pinned directory descriptor.
    absolute = Path(root).absolute()
    descriptor = os.open(absolute.anchor, _DIRECTORY_FLAGS)
    try:
        for component in absolute.parts[1:]:
            next_descriptor = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        for component in (".genea", "ui-store"):
            try:
                os.mkdir(component, 0o700, dir_fd=descriptor)
            except FileExistsError:
                pass
            next_descriptor = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        if os.fstat(descriptor).st_uid != os.getuid():
            raise UIStoreError("UI store must belong to the current user")
        os.fchmod(descriptor, 0o700)
        yield absolute / ".genea" / "ui-store", descriptor
    finally:
        os.close(descriptor)


def _slot_info(directory_fd: int, name: str):
    try:
        info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise UIStoreError(f"UI store slot must be a user-owned regular file: {name}")
    return info


def _validate_slots(directory_fd: int):
    slots = {name: _slot_info(directory_fd, name) for name in _SLOTS}
    for name, info in slots.items():
        if info is None:
            continue
        known_links = sum(other is not None and (other.st_dev, other.st_ino) == (info.st_dev, info.st_ino)
                          for other in slots.values())
        if info.st_nlink != known_links or (name == "build.json" and info.st_nlink != 1):
            raise UIStoreError(f"UI store slot has an unexpected hardlink: {name}")
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                raise UIStoreError(f"UI store slot changed while opening: {name}")
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
    return slots


@contextmanager
def _publication_lock(directory_fd: int):
    # Open existing locks without O_CREAT. Exclusive creation handles the race
    # between first publishers without replacing another process's lock inode.
    for _ in range(4):
        try:
            descriptor = os.open(".publish.lock", os.O_RDWR | os.O_NOFOLLOW, dir_fd=directory_fd)
        except FileNotFoundError:
            try:
                descriptor = os.open(".publish.lock", os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW,
                                     0o600, dir_fd=directory_fd)
            except FileExistsError:
                continue
        break
    else:
        raise UIStoreError("UI store publication lock changed repeatedly")
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise UIStoreError("UI store publication lock must be a private regular file")
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        locked = os.stat(".publish.lock", dir_fd=directory_fd, follow_symlinks=False)
        if (locked.st_dev, locked.st_ino) != (info.st_dev, info.st_ino) or locked.st_nlink != 1:
            raise UIStoreError("UI store publication lock changed while acquiring")
        yield
    finally:
        os.close(descriptor)  # Closing releases flock, including on exceptions.


def _stream_payload(stream, payload, directory_fd: int, max_bytes: int, reserve_bytes: int):
    encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    digest = hashlib.sha256()
    written = 0
    buffer = bytearray()

    def flush():
        nonlocal written
        if not buffer:
            return
        if _available_bytes(directory_fd) < reserve_bytes + len(buffer):
            raise UIStoreLimitError("Insufficient free space for UI payload reserve")
        stream.write(buffer)
        digest.update(buffer)
        written += len(buffer)
        buffer.clear()

    for token in encoder.iterencode(payload):
        # A JSON string token can itself exceed the quota; encode it in bounded
        # pieces instead of allocating a second payload-sized UTF-8 buffer.
        for start in range(0, len(token), _BUFFER_BYTES // 4):
            piece = token[start:start + _BUFFER_BYTES // 4].encode("utf-8")
            if written + len(buffer) + len(piece) > max_bytes:
                raise UIStoreLimitError("UI payload exceeds max_payload_bytes")
            buffer.extend(piece)
            if len(buffer) >= _BUFFER_BYTES:
                flush()
    flush()
    stream.flush()
    os.fsync(stream.fileno())
    return written, digest.hexdigest()


def publish(root: Path, payload: dict[str, Any], *,
            max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
            min_free_bytes: int = DEFAULT_MIN_FREE_BYTES) -> dict[str, Any]:
    """Publish a newly built payload atomically, retaining one rollback version.

    Space preflight reserves room for a maximum-sized build in addition to the
    configured free-space reserve. Cooperating processes serialize via flock.
    Failures before the atomic replacement leave current.json in place; stale
    build.json is reclaimed only while holding that same lock.
    """
    if not isinstance(payload, dict):
        raise TypeError("UI payload must be an object")
    if type(max_payload_bytes) is not int or max_payload_bytes <= 0:
        raise ValueError("max_payload_bytes must be a positive integer")
    if type(min_free_bytes) is not int or min_free_bytes < 0:
        raise ValueError("min_free_bytes must be a nonnegative integer")
    with _store_directory(Path(root)) as (directory, directory_fd), _publication_lock(directory_fd):
        slots = _validate_slots(directory_fd)
        if slots["build.json"] is not None:
            os.unlink("build.json", dir_fd=directory_fd)
        if _available_bytes(directory_fd) < min_free_bytes + max_payload_bytes:
            raise UIStoreLimitError("Insufficient free space for maximum UI payload and reserve")
        descriptor = os.open("build.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd)
        staging = os.fstat(descriptor)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                size, digest = _stream_payload(stream, payload, directory_fd, max_payload_bytes, min_free_bytes)
            slots = _validate_slots(directory_fd)
            if slots["current.json"] is not None:
                if slots["previous.json"] is not None:
                    os.unlink("previous.json", dir_fd=directory_fd)
                # A hardlink pins the old bytes without a fourth payload slot
                # or an extra full copy. current.json is never moved away.
                os.link("current.json", "previous.json", src_dir_fd=directory_fd,
                        dst_dir_fd=directory_fd, follow_symlinks=False)
                os.fsync(directory_fd)
            os.replace("build.json", "current.json", src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
            os.fsync(directory_fd)
        finally:
            # Do not remove a replacement staging path introduced by someone
            # else. Only this invocation's exact regular-file inode is owned.
            try:
                remaining = os.stat("build.json", dir_fd=directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                remaining = None
            if remaining is not None and (remaining.st_dev, remaining.st_ino) == (staging.st_dev, staging.st_ino):
                os.unlink("build.json", dir_fd=directory_fd)
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        counts = meta.get("counts", {})
        previous = _slot_info(directory_fd, "previous.json")
        return {"path": str(directory / "current.json"), "currentPath": str(directory / "current.json"),
                "previousPath": str(directory / "previous.json") if previous is not None else None,
                "storePath": str(directory), "bytes": size, "sha256": digest,
                "generatedAt": meta.get("generatedAt"), "counts": counts}


publish_ui_payload = publish
