"""Read-only validation of local LFS objects needed by unpushed Git history."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess


_POINTER = re.compile(
    rb"version https://git-lfs.github.com/spec/v1\r?\n"
    rb"oid sha256:([0-9a-f]{64})\r?\nsize (0|[1-9][0-9]*)\r?\n?\Z"
)


def _git(root: Path, *args: str, input: bytes | None = None) -> bytes:
    # A partial clone must not lazily fetch objects during this local diagnostic.
    env = {**os.environ, "GIT_NO_LAZY_FETCH": "1", "GIT_OPTIONAL_LOCKS": "0"}
    result = subprocess.run(
        ["git", "-C", str(root), *args], input=input,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, check=False,
    )
    if result.returncode:
        raise ValueError(result.stderr.decode("utf-8", errors="replace").strip())
    return result.stdout


def _pointer_blobs(root: Path, head: str, upstream: str):
    paths = {}
    for line in _git(root, "rev-list", "--objects", head, f"^{upstream}").splitlines():
        oid, _, path = line.partition(b" ")
        paths[oid] = path.decode("utf-8", errors="replace")
    if not paths:
        return
    request = b"".join(oid + b"\n" for oid in paths)
    small = []
    for line in _git(root, "cat-file", "--batch-check", input=request).splitlines():
        parts = line.split()
        if len(parts) != 3:
            raise ValueError("Git object unavailable during history inspection")
        oid, kind, size = parts
        if kind == b"blob" and int(size) <= 1024:
            small.append(oid)
    if not small:
        return
    contents = _git(root, "cat-file", "--batch", input=b"".join(oid + b"\n" for oid in small))
    offset = 0
    for expected in small:
        end = contents.index(b"\n", offset)
        oid, kind, size = contents[offset:end].split()
        if oid != expected or kind != b"blob":
            raise ValueError("Unexpected Git batch response")
        start = end + 1
        offset = start + int(size) + 1
        yield paths[oid], contents[start:offset - 1]
    if offset != len(contents):
        raise ValueError("Unexpected trailing Git batch data")


def inspect_lfs_upload(root: Path, upstream: str = "origin/main") -> dict:
    """Report readiness relative to a local upstream ref; never fetch or upload.

    Covers all newly reachable historical pointer blobs, including pointers
    replaced before HEAD. Unsupported/malformed small LFS pointers fail closed.
    Resolves the shared Git directory and lfs.storage without invoking Git
    LFS (even its env command creates cache directories). Readiness is a
    snapshot, not a guarantee about the remote or a later push.
    """
    root = Path(root).resolve()
    report = {
        "status": "blocked", "requiredObjectCount": 0,
        "missingObjects": [], "corruptObjects": [],
        "head": None, "upstream": None, "upstreamRef": upstream, "errors": [],
    }
    try:
        report["head"] = _git(root, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
        report["upstream"] = _git(
            root, "rev-parse", "--verify", "--end-of-options", f"{upstream}^{{commit}}",
        ).decode().strip()
        object_dir = Path(os.fsdecode(_git(root, "rev-parse", "--git-path", "lfs/objects").strip()))
        if not object_dir.is_absolute():
            object_dir = root / object_dir
        common_dir = Path(os.fsdecode(_git(root, "rev-parse", "--git-common-dir").strip()))
        if not common_dir.is_absolute():
            common_dir = root / common_dir
        storage = os.fsdecode(_git(root, "config", "--get", "--default", "", "lfs.storage").strip())
        if storage:
            storage_dir = Path(storage)
            if not storage_dir.is_absolute():
                storage_dir = common_dir / storage_dir
            object_dir = storage_dir / "objects"
        else:
            git_dir = Path(os.fsdecode(_git(root, "rev-parse", "--absolute-git-dir").strip()))
            if git_dir.resolve() != common_dir.resolve():
                object_dir = common_dir / "lfs/objects"
        required = {}
        for path, content in _pointer_blobs(root, report["head"], report["upstream"]):
            match = _POINTER.fullmatch(content)
            if not match:
                if content.startswith(b"version https://git-lfs.github.com/spec/"):
                    report["errors"].append(f"Malformed or unsupported LFS pointer: {path}")
                continue
            oid, size = match[1].decode(), int(match[2])
            if oid in required and required[oid]["size"] != size:
                report["errors"].append(f"Conflicting pointer sizes for {oid}")
            else:
                required[oid] = {"oid": oid, "size": size, "path": path}
        report["requiredObjectCount"] = len(required)
        for oid, entry in sorted(required.items()):
            object_path = object_dir / oid[:2] / oid[2:4] / oid
            try:
                if any(parent.is_symlink() for parent in (object_path, *object_path.parents)):
                    raise OSError("Symlink in LFS object path")
                digest, size = hashlib.sha256(), 0
                descriptor = os.open(object_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(descriptor, "rb") as source:
                    if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                        raise OSError("LFS object is not a regular file")
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(block)
                        size += len(block)
                if size != entry["size"] or digest.hexdigest() != oid:
                    report["corruptObjects"].append({
                        **entry, "actualSize": size, "actualOid": digest.hexdigest(),
                    })
            except FileNotFoundError:
                report["missingObjects"].append(entry)
            except OSError as exc:
                report["corruptObjects"].append({**entry, "error": str(exc)})
        if not (report["errors"] or report["missingObjects"] or report["corruptObjects"]):
            report["status"] = "ready"
    except (OSError, ValueError) as exc:
        report["errors"].append(str(exc))
    return report
