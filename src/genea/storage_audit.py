"""Local storage inventory and explicitly confirmed dependency/cache cleanup.

Research material and LFS are never deleted by this module. Byte counts describe
logical regular-file sizes, not allocated disk blocks; symlinks are not followed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterable


Runner = Callable[..., subprocess.CompletedProcess]
_REBUILDABLE = {".venv", "node_modules", "__pycache__", ".pytest_cache"}
_INVENTORY = (
    (".git/lfs", "conditional"),
    ("research/tmp", "manual_review"),
    ("research/backups", "manual_review"),
    ("research/archive/originals", "protected"),
    ("research/archive/rendered", "manual_review"),
    (".venv", "rebuildable"),
    ("node_modules", "rebuildable"),
    ("frontend/node_modules", "rebuildable"),
    ("sources", "protected"),
)


def _root(root: Path) -> Path:
    root = Path(root).expanduser()
    if root.is_symlink():
        raise ValueError("Workspace root must not be a symlink")
    root = root.resolve(strict=True)
    if not root.is_dir() or root == Path(root.anchor):
        raise ValueError("Expected a specific workspace directory")
    git_marker = root / ".git"
    project_marker = root / "pyproject.toml"
    if (git_marker.is_symlink() or not git_marker.exists()
            or project_marker.is_symlink() or not project_marker.is_file()):
        raise ValueError("Expected the Genea repository root")
    return root


def _target(root: Path, value: str | Path) -> tuple[Path, str]:
    value = Path(value)
    if ".." in value.parts:
        raise ValueError("Parent traversal is forbidden")
    path = value if value.is_absolute() else root / value
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise ValueError("Target is outside workspace") from error
    if not relative.parts:
        raise ValueError("Workspace root cannot be a cleanup target")
    cursor = root
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise ValueError("Symlink targets or ancestors are forbidden")
    if path.resolve(strict=False) != path:
        raise ValueError("Target resolution changed")
    return path, relative.as_posix()


def _category(relative: str) -> tuple[str, str]:
    parts = Path(relative).parts
    if parts[0] == "sources" or parts[:3] == ("research", "archive", "originals"):
        return "protected", "immutable_source"
    if parts[0] == ".git":
        return "conditional", "lfs_and_git_require_manual_review"
    if parts[:2] == ("frontend", "data") or parts[0] == "research":
        if any(".sqlite" in part for part in parts):
            return "protected", "research_database"
        return "manual_review", "research_or_controlled_export"
    if parts[-1] in _REBUILDABLE:
        return "rebuildable", "explicit_dependency_or_cache"
    return "manual_review", "not_allowlisted"


def _measure(path: Path, *, parent_fd: int | None = None) -> dict:
    """Inspect metadata only and fingerprint directory membership and file stats."""
    result = {"bytes": 0, "files": 0, "symlinkCount": 0, "errorCount": 0,
              "exists": False, "type": "missing", "status": "missing"}
    digest = hashlib.sha256()
    def inspect(metadata: os.stat_result, relative: str, parent_fd: int | None, name: str | Path):
        try:
            if relative == ".":
                result["exists"] = True
                result["type"] = ("symlink" if stat.S_ISLNK(metadata.st_mode) else
                                  "directory" if stat.S_ISDIR(metadata.st_mode) else
                                  "file" if stat.S_ISREG(metadata.st_mode) else "special")
                result["identity"] = [metadata.st_dev, metadata.st_ino]
            digest.update(json.dumps([relative, metadata.st_mode, metadata.st_dev,
                                      metadata.st_ino, metadata.st_size, metadata.st_mtime_ns,
                                      metadata.st_ctime_ns],
                                     separators=(",", ":")).encode())
            if stat.S_ISLNK(metadata.st_mode):
                result["symlinkCount"] += 1
            elif stat.S_ISREG(metadata.st_mode):
                result["bytes"] += metadata.st_size
                result["files"] += 1
            elif stat.S_ISDIR(metadata.st_mode):
                descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                     dir_fd=parent_fd)
                try:
                    opened = os.fstat(descriptor)
                    if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                        result["errorCount"] += 1
                        return
                    with os.scandir(descriptor) as entries:
                        children = sorted(item.name for item in entries)
                    for child in children:
                        try:
                            child_stat = os.stat(child, dir_fd=descriptor, follow_symlinks=False)
                            inspect(child_stat, relative + "/" + child, descriptor, child)
                        except OSError:
                            result["errorCount"] += 1
                finally:
                    os.close(descriptor)
            else:
                result["errorCount"] += 1
        except FileNotFoundError:
            if relative != ".":
                result["errorCount"] += 1
        except OSError:
            result["errorCount"] += 1
    try:
        metadata = os.stat(path, dir_fd=parent_fd, follow_symlinks=False)
        inspect(metadata, ".", parent_fd, path)
    except FileNotFoundError:
        pass
    except OSError:
        result["errorCount"] += 1
    if result["exists"]:
        result["status"] = "partial" if result["errorCount"] else "measured"
    result["snapshot"] = digest.hexdigest()
    return result


def _inventory_entry(root: Path, relative: str, category: str) -> dict:
    entry = {"path": relative, "category": category, "safe": False}
    try:
        path, _ = _target(root, relative)
        entry.update(_measure(path))
    except ValueError:
        entry.update(bytes=0, exists=False, type="symlink", status="blocked_symlink")
    return entry


def _active_database(root: Path) -> tuple[str | None, str]:
    try:
        config, _ = _target(root, "research/storage.json")
        if not config.exists():
            return "research/genea.sqlite3", "plaintext_default"
        if not config.is_file() or config.stat().st_size > 16_384:
            return None, "invalid_configuration"
        payload = json.loads(config.read_text(encoding="utf-8"))
        if payload.get("backend") == "plaintext":
            return "research/genea.sqlite3", "configured_plaintext"
        name = payload.get("database")
        if (payload.get("backend") == "sqlcipher" and isinstance(name, str)
                and name not in {"", ".", ".."} and Path(name).name == name):
            return "research/" + name, "configured_sqlcipher"
    except (OSError, ValueError, AttributeError):
        pass
    return None, "invalid_configuration"


def _run_lfs(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess:
    environment = dict(os.environ, LC_ALL="C", LANG="C", GIT_TERMINAL_PROMPT="0",
                       GCM_INTERACTIVE="Never")
    return subprocess.run(command, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=120, check=False)


def _parse_lfs(result: subprocess.CompletedProcess) -> dict:
    output = str(result.stdout or "") + "\n" + str(result.stderr or "")
    output = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", output).replace("\r", "\n")
    summaries = re.findall(r"(\d+) files? would be pruned \(([\d.]+)\s*([KMGT]?i?B)\)", output)
    progress = re.findall(r"(\d+) local objects?, (\d+) retained", output)
    candidate_count = candidate_bytes = None
    ambiguous = False
    if summaries:
        ambiguous = len(set(summaries)) != 1
        count, amount, unit = summaries[-1]
        powers = {"B": 1, "KB": 1000, "MB": 1000**2, "GB": 1000**3,
                  "TB": 1000**4, "KiB": 1024, "MiB": 1024**2,
                  "GiB": 1024**3, "TiB": 1024**4}
        candidate_count, candidate_bytes = int(count), round(float(amount) * powers[unit])
    elif progress and progress[-1][0] == progress[-1][1]:
        candidate_count = candidate_bytes = 0
    missing_counts = [int(item) for item in re.findall(r"(\d+) not on remote", output)]
    missing_ids = sorted(set(re.findall(r"(?m)^\s*\*\s*([0-9a-f]{64})\s*$", output)))
    missing = max(missing_counts + [len(missing_ids)], default=0)
    verified = [int(item) for item in re.findall(r"(\d+) verified with remote", output)]
    error = bool(re.search(r"\b(error|fatal|failed|missing|warning|unable|cannot|unverified)\b",
                          output, re.IGNORECASE))
    understood = candidate_count is not None and not ambiguous
    return {"returnCode": result.returncode, "candidateCount": candidate_count,
            "candidateBytes": candidate_bytes, "sizeIsApproximate": bool(summaries),
            "missingRemoteCount": missing, "missingRemoteIds": missing_ids,
            "verifiedRemoteCount": max(verified, default=0),
            "status": "ok" if result.returncode == 0 and understood and not error else "blocked"}


def audit_lfs(root: Path, *, runner: Runner | None = None) -> dict:
    """Only dry-runs; require positive remote verification for every candidate."""
    root = _root(root)
    runner = runner or _run_lfs
    reports = {}
    try:
        _target(root, ".git/lfs")
    except ValueError:
        return {"safeToPrune": False, "status": "blocked_symlink", "dryRuns": reports}
    for label, suffix in (
        ("local", []),
        ("remote", ["--verify-remote", "--verify-unreachable"]),
    ):
        command = ["git", "lfs", "prune", "--dry-run", *suffix]
        try:
            reports[label] = dict(_parse_lfs(runner(command, cwd=root)), command=command)
        except Exception:
            # Injected runners and unexpected output also fail closed.
            reports[label] = {"status": "blocked", "command": command,
                              "reason": "dry_run_failed", "candidateCount": None,
                              "candidateBytes": None, "missingRemoteCount": None}
    local, remote = reports["local"], reports["remote"]
    safe = (local["status"] == remote["status"] == "ok"
            and local["candidateCount"] == remote["candidateCount"]
            and local["candidateBytes"] == remote["candidateBytes"]
            and remote["missingRemoteCount"] == 0
            and remote.get("verifiedRemoteCount") == remote["candidateCount"])
    return {"safeToPrune": bool(safe), "status": "verified" if safe else "blocked",
            "dryRuns": reports, "automaticPruneAllowed": False}


def audit_workspace(root: Path, *, runner: Runner | None = None) -> dict:
    root = _root(root)
    entries = [_inventory_entry(root, path, category) for path, category in _INVENTORY]
    database, configuration_status = _active_database(root)
    for relative in dict.fromkeys([database, "research/genea.sqlite3",
                                  "research/genea.encrypted.sqlite3"]):
        if relative is not None:
            entries.append(_inventory_entry(root, relative, "protected"))
            for suffix in ("-wal", "-shm", "-journal"):
                sidecar = _inventory_entry(root, relative + suffix, "protected")
                if sidecar.get("exists") or sidecar["status"] == "blocked_symlink":
                    entries.append(sidecar)
    disk = None
    try:
        usage = shutil.disk_usage(root)
        disk = {"totalBytes": usage.total, "usedBytes": usage.used, "freeBytes": usage.free}
    except OSError:
        pass
    return {"schemaVersion": 1, "root": str(root), "entries": entries,
            "activeDatabase": {"path": database, "status": configuration_status},
            "disk": disk, "lfs": audit_lfs(root, runner=runner)}


def _fingerprint(plan: dict) -> str:
    payload = {key: value for key, value in plan.items() if key not in {"fingerprint", "confirmToken"}}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=True).encode()).hexdigest()


def plan_prune(root: Path, targets: Iterable[str | Path]) -> dict:
    """Plan only explicit directories; protected ancestors override cache names."""
    root = _root(root)
    entries = []
    seen = set()
    for value in targets:
        try:
            path, relative = _target(root, value)
        except ValueError as error:
            entries.append({"path": str(value), "category": "protected", "safe": False,
                            "bytes": 0, "status": "blocked", "reason": str(error)})
            continue
        if relative in seen:
            continue
        seen.add(relative)
        category, reason = _category(relative)
        entry = {"path": relative, "category": category, "reason": reason, **_measure(path)}
        entry["safe"] = (category == "rebuildable" and entry["type"] == "directory"
                         and entry["errorCount"] == 0)
        entries.append(entry)
    safe_paths = [Path(entry["path"]) for entry in entries if entry["safe"]]
    for entry in entries:
        if entry["safe"] and any(parent in Path(entry["path"]).parents for parent in safe_paths):
            entry.update(safe=False, reason="covered_by_selected_parent")
    plan = {"schemaVersion": 1, "root": str(root), "entries": entries,
            "totalSafeBytes": sum(entry["bytes"] for entry in entries if entry["safe"])}
    plan["fingerprint"] = plan["confirmToken"] = _fingerprint(plan)
    return plan


@contextmanager
def _pinned_parent(root: Path, relative: str):
    """Hold each ancestor open without following symlinks, including during deletion."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(root, flags)
    try:
        for part in Path(relative).parts[:-1]:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def _remove_tree(parent_fd: int, name: str, identity: list[int]) -> None:
    metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if not stat.S_ISDIR(metadata.st_mode) or [metadata.st_dev, metadata.st_ino] != identity:
        raise ValueError("Cleanup directory was replaced before removal")
    # Public fd-relative APIs keep the opened tree pinned on every supported
    # Python and avoid a name re-open race in shutil.rmtree(dir_fd=...).
    descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                         dir_fd=parent_fd)
    try:
        opened = os.fstat(descriptor)
        if [opened.st_dev, opened.st_ino] != identity:
            raise ValueError("Cleanup directory was replaced while opening")
        with os.scandir(descriptor) as entries:
            children = sorted(item.name for item in entries)
        for child in children:
            metadata = os.stat(child, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISDIR(metadata.st_mode):
                _remove_tree(descriptor, child, [metadata.st_dev, metadata.st_ino])
            else:
                os.unlink(child, dir_fd=descriptor)
    finally:
        os.close(descriptor)
    current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if not stat.S_ISDIR(current.st_mode) or [current.st_dev, current.st_ino] != identity:
        raise ValueError("Cleanup directory was replaced before final removal")
    os.rmdir(name, dir_fd=parent_fd)


def apply_prune(root: Path, plan: dict, confirm_token: str | None) -> dict:
    """Confirm, revalidate every safe entry, then remove only approved directories."""
    root = _root(root)
    if not confirm_token or not isinstance(plan, dict):
        raise ValueError("Explicit plan confirmation is required")
    fingerprint = _fingerprint(plan)
    if (plan.get("schemaVersion") != 1 or plan.get("root") != str(root)
            or confirm_token != fingerprint or plan.get("fingerprint") != fingerprint
            or plan.get("confirmToken") != fingerprint):
        raise ValueError("Plan fingerprint, token or workspace does not match")
    approved = []
    for entry in plan["entries"]:
        if entry.get("safe") is not True:
            continue
        path, relative = _target(root, entry["path"])
        category, _ = _category(relative)
        measured = _measure(path)
        if (category != "rebuildable" or measured["type"] != "directory"
                or measured["errorCount"] or measured["snapshot"] != entry.get("snapshot")
                or measured.get("identity") != entry.get("identity")):
            raise ValueError("Cleanup target changed or is no longer safe: " + relative)
        approved.append((relative, measured))
    result = {"schemaVersion": 1, "deletedBytes": 0, "deletedPaths": [],
              "skippedPaths": [entry["path"] for entry in plan["entries"] if not entry.get("safe")]}
    for relative, measured in approved:
        with _pinned_parent(root, relative) as descriptor:
            name = Path(relative).name
            metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if not stat.S_ISDIR(metadata.st_mode) or [metadata.st_dev, metadata.st_ino] != measured["identity"]:
                raise ValueError("Cleanup target was replaced: " + relative)
            if _measure(Path(name), parent_fd=descriptor)["snapshot"] != measured["snapshot"]:
                raise ValueError("Cleanup target changed before deletion: " + relative)
            _remove_tree(descriptor, name, measured["identity"])
        result["deletedBytes"] += measured["bytes"]
        result["deletedPaths"].append(relative)
    return result
