"""Explicit, verified cleanup of a completed research temporary run.

The caller persists durable evidence first, then requests a plan and submits
its confirmation token. This module never creates or completes run manifests.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from contextlib import closing, contextmanager
from contextvars import ContextVar
from pathlib import Path


class TmpLifecycleError(ValueError):
    """The run cannot safely be removed under the temporary-run contract."""


_preservation_verifications: ContextVar[dict | None] = ContextVar("preservation_verifications", default=None)


@contextmanager
def preservation_verification_scope():
    """Reuse expensive validity checks only within one closeout operation."""
    token = _preservation_verifications.set({})
    try:
        yield
    finally:
        _preservation_verifications.reset(token)


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def _path(value: str | Path) -> Path:
    path = Path(os.path.abspath(value))
    for part in (path, *path.parents):
        if part.is_symlink():
            raise TmpLifecycleError("Symlinks are not allowed in cleanup paths")
    return path


def _hash_file(path: Path) -> tuple[str, os.stat_result]:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise TmpLifecycleError("Cleanup requires regular files")
            digest = hashlib.sha256()
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
            after = os.fstat(source.fileno())
        if _identity(before) != _identity(after) or _identity(path.lstat()) != _identity(after):
            raise TmpLifecycleError("File changed during cleanup verification")
        return digest.hexdigest(), after
    except OSError as error:
        raise TmpLifecycleError(f"Cannot verify cleanup file: {path}") from error


def _identity(info: os.stat_result) -> list[int]:
    return [info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_nlink]


def _hex(value: object, length: int) -> bool:
    return isinstance(value, str) and re.fullmatch(f"[0-9a-f]{{{length}}}", value) is not None


def _git(root: Path, *arguments: str, binary: bool = False) -> str | bytes:
    try:
        result = subprocess.run(["git", "-C", str(root), *arguments], check=True,
                                capture_output=True, text=not binary, timeout=30,
                                env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
        return result.stdout
    except (OSError, subprocess.SubprocessError) as error:
        raise TmpLifecycleError("Cannot verify published preservation commit") from error


def _has_sidecars(path: Path) -> bool:
    return any(os.path.lexists(str(path) + suffix) for suffix in ("-wal", "-shm", "-journal"))


def validate_preservation(repo_root: str | Path, preservation: object) -> dict:
    """Verify published encrypted bytes and an available, equivalent snapshot.

    Only read-only storage connections and Git queries are used. A JSON claim
    that a backup was verified is never accepted in place of these checks.
    """
    from .lfs_archive import _token
    from .private_lifecycle import semantic_database_manifest
    from .storage import configured_database_backend
    from . import storage_snapshots as snapshots

    root = _path(repo_root)
    if (not isinstance(preservation, dict)
            or preservation.get("canonicalPath") != "research/genea.encrypted.sqlite3"
            or preservation.get("remote") != "origin" or preservation.get("branch") != "main"
            or not _hex(preservation.get("canonicalSha256"), 64)
            or not _hex(preservation.get("snapshotId"), 64)
            or not _hex(preservation.get("publishedCommit"), 40)
            or not isinstance(preservation.get("snapshotStore"), str)
            or not Path(preservation["snapshotStore"]).is_absolute()):
        raise TmpLifecycleError("Trial database cleanup requires a complete preservation proof")
    canonical = _path(root / preservation["canonicalPath"])
    destination = _path(preservation["snapshotStore"])
    if destination == root or root in destination.parents or destination in root.parents:
        raise TmpLifecycleError("Snapshot store must be outside the workspace")
    snapshot_id, commit = preservation["snapshotId"], preservation["publishedCommit"]
    snapshot = _path(destination / (snapshot_id + ".sqlite3"))
    for path in (canonical, snapshot):
        if _has_sidecars(path):
            raise TmpLifecycleError("Preserved databases must have no WAL/SHM/journal sidecars")
    canonical_hash, canonical_info = _hash_file(canonical)
    snapshot_hash, snapshot_info = _hash_file(snapshot)
    if canonical_hash != preservation["canonicalSha256"] or snapshot_hash != snapshot_id:
        raise TmpLifecycleError("Preserved database SHA256 does not match")
    with canonical.open("rb") as source, snapshot.open("rb") as copy:
        if b"SQLite format 3\x00" in (source.read(16), copy.read(16)):
            raise TmpLifecycleError("Preservation requires encrypted database files")
    pointer = ("version https://git-lfs.github.com/spec/v1\n"
               f"oid sha256:{canonical_hash}\nsize {canonical_info.st_size}\n")
    if _git(root, "show", f"{commit}:{preservation['canonicalPath']}") != pointer:
        raise TmpLifecycleError("Published commit does not contain the exact canonical LFS pointer")
    if _git(root, "ls-remote", "--heads", "origin", "main").strip() != f"{commit}\trefs/heads/main":
        raise TmpLifecycleError("Published commit is not the current origin/main tip")
    try:
        backend = configured_database_backend(root)
        if _path(backend.path) != canonical or not backend.encrypted:
            raise TmpLifecycleError("Preservation path must be the configured encrypted database")
        journal = snapshots.verify_checkpoint_journal(destination)
        records = snapshots._read_journal(destination)
        retirements = snapshots._read_retirements(destination, records)
        if (not any(row["snapshotId"] == snapshot_id for row in records)
                or any(row["snapshotId"] == snapshot_id for row in retirements)):
            raise TmpLifecycleError("Preservation snapshot is not an available checkpoint")
        manifest = snapshots._load_manifest(destination, snapshot_id + ".manifest.json")
        if manifest.get("sourceId") != _token({"path": str(backend.path)}):
            raise TmpLifecycleError("Snapshot was not created from the configured canonical path")
        copy_backend = snapshots._opener(backend, None)(snapshot)
        if _path(copy_backend.path) != snapshot:
            raise TmpLifecycleError("Snapshot opener returned a different database")
        memo = _preservation_verifications.get()
        key = (str(root), str(canonical), str(snapshot), canonical_hash, snapshot_hash)
        logical = memo.get(key) if memo is not None else None
        if logical is not None:
            # Identical verified bytes still require authentication with the
            # currently configured keys; an old successful unlock is no proof.
            with closing(backend.connect(readonly=True)) as source, closing(copy_backend.connect(readonly=True)) as copy:
                source.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
                copy.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
        else:
            snapshots._integrity(backend)
            snapshots._integrity(copy_backend)
            if canonical_hash == snapshot_hash:
                # Exact ciphertext equality already proves equal contents.
                logical = {"identicalCiphertextSha256": canonical_hash}
            else:
                with closing(backend.connect(readonly=True)) as source, closing(copy_backend.connect(readonly=True)) as copy:
                    source.execute("BEGIN")
                    copy.execute("BEGIN")
                    logical = semantic_database_manifest(source)
                    if logical != semantic_database_manifest(copy):
                        raise TmpLifecycleError("Snapshot logical contents differ from the canonical database")
    except TmpLifecycleError:
        raise
    except Exception as error:
        raise TmpLifecycleError("Cannot verify encrypted preservation and snapshot integrity") from error
    # Bind both observations to the exact immutable bytes, not only path names.
    for path, digest, info in ((canonical, canonical_hash, canonical_info), (snapshot, snapshot_hash, snapshot_info)):
        current_hash, current_info = _hash_file(path)
        if current_hash != digest or _identity(current_info) != _identity(info) or _has_sidecars(path):
            raise TmpLifecycleError("Preserved database changed during verification")
    if memo is not None:
        memo[key] = logical
    return {"canonicalSha256": canonical_hash, "snapshotId": snapshot_id, "publishedCommit": commit,
            "logicalSha256": _digest(logical),
            "fingerprint": _digest({"proof": preservation, "canonical": _identity(canonical_info),
                                    "snapshot": _identity(snapshot_info), "journal": journal["lastHash"],
                                    "logical": logical})}


def _trial_allowlist(root: Path, run: Path, manifest: dict) -> tuple[dict[str, str], dict | None]:
    rows = manifest.get("disposableTrialDatabases", [])
    if not isinstance(rows, list) or "disposableTrialDatabases" in manifest and manifest["version"] != 2:
        raise TmpLifecycleError("Disposable trial databases require a version 2 run manifest")
    raw_rows = manifest.get("disposableTrialRawCopies", [])
    if (not isinstance(raw_rows, list)
            or "disposableTrialRawCopies" in manifest and manifest["version"] != 2):
        raise TmpLifecycleError("Disposable RAW copies require a version 2 run manifest")
    trials = {}
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get("path"), str)
                or not _hex(row.get("sha256"), 64)):
            raise TmpLifecycleError("Invalid disposable trial database declaration")
        relative = Path(row["path"])
        if (relative.is_absolute() or ".." in relative.parts or relative.as_posix() != row["path"]
                or relative.suffix.lower() not in {".sqlite", ".sqlite3", ".db"} or row["path"] in trials):
            raise TmpLifecycleError("Trial database must be one exact run-relative database file")
        path = _path(run / relative)
        digest, info = _hash_file(path)
        if info.st_nlink != 1:
            raise TmpLifecycleError("Hardlinked trial databases cannot be removed")
        if digest != row["sha256"]:
            raise TmpLifecycleError("Disposable trial database SHA256 does not match")
        trials[row["path"]] = row["sha256"]
    preservation = validate_preservation(root, manifest.get("preservation")) if trials or raw_rows else None
    return trials, preservation


def _raw_allowlist(root: Path, run: Path, manifest: dict) -> tuple[dict[str, str], list[dict]]:
    copies, canonical_files = {}, []
    for row in manifest.get("disposableTrialRawCopies", []):
        if (not isinstance(row, dict) or not isinstance(row.get("path"), str)
                or not isinstance(row.get("canonicalPath"), str) or not _hex(row.get("sha256"), 64)):
            raise TmpLifecycleError("Invalid disposable RAW copy declaration")
        relative, original = Path(row["path"]), Path(row["canonicalPath"])
        if (any(path.is_absolute() or ".." in path.parts or path.as_posix() != value
                for path, value in ((relative, row["path"]), (original, row["canonicalPath"])))
                or original.parts[:3] != ("research", "archive", "originals")
                or len(original.parts) < 4 or len(relative.parts) < len(original.parts)
                or relative.parts[-len(original.parts):] != original.parts or row["path"] in copies):
            raise TmpLifecycleError("RAW copy must use the exact trial and canonical RAW locator")
        copy_path, canonical = _path(run / relative), _path(root / original)
        if copy_path == canonical:
            raise TmpLifecycleError("A disposable RAW copy must be separate from canonical RAW")
        digest, metadata = _hash_file(canonical)
        copy_digest, copy_metadata = _hash_file(copy_path)
        if digest != row["sha256"] or copy_digest != digest or copy_metadata.st_nlink != 1:
            raise TmpLifecycleError("RAW copy must be an exact, non-hardlinked canonical duplicate")
        committed = _git(root, "show", f"{manifest['preservation']['publishedCommit']}:{original.as_posix()}", binary=True)
        pointer = (f"version https://git-lfs.github.com/spec/v1\noid sha256:{digest}\n"
                   f"size {metadata.st_size}\n").encode()
        if hashlib.sha256(committed).hexdigest() != digest and committed != pointer:
            raise TmpLifecycleError("Canonical RAW is not preserved in the published commit")
        after_digest, after_metadata = _hash_file(canonical)
        if after_digest != digest or _identity(after_metadata) != _identity(metadata):
            raise TmpLifecycleError("Canonical RAW changed during verification")
        copies[row["path"]] = digest
        canonical_files.append({"path": row["canonicalPath"], "sha256": digest, "identity": _identity(metadata)})
    return copies, canonical_files


def _inventory(run: Path, trials: dict[str, str] | None = None,
               raw_copies: dict[str, str] | None = None) -> tuple[list[dict], int, int]:
    trials = trials or {}
    raw_copies = raw_copies or {}
    mirrors = {parent for name in raw_copies for parent in Path(name).parents
               if len(parent.parts) >= 3 and parent.parts[-3:] == ("research", "archive", "originals")}
    mirrors.update(Path(name).parent / "archive/originals" for name in trials
                   if Path(name).name == "genea.encrypted.sqlite3"
                   and Path(name).parent.name == "research" and len(Path(name).parts) > 1)
    entries, files, size = [], 0, 0
    for directory, directories, filenames in os.walk(run, followlinks=False):
        for name in sorted(directories + filenames):
            path = Path(directory) / name
            relative = path.relative_to(run)
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise TmpLifecycleError("Symlinks inside temporary runs are not allowed")
            lowered = name.lower()
            if lowered == ".genea-run.json" and len(relative.parts) > 1:
                raise TmpLifecycleError("Nested run requires separate cleanup and retention review")
            declared = relative.as_posix() in trials
            raw_copy = relative.as_posix() in raw_copies
            if (lowered in {"raw", "sources", ".git"}
                    or lowered == "originals" and (relative not in mirrors or not stat.S_ISDIR(info.st_mode))
                    or lowered.endswith(("-wal", "-shm", "-journal"))
                    or lowered.endswith((".sqlite", ".sqlite3", ".db")) and not declared):
                raise TmpLifecycleError("RAW or database material cannot be removed by run cleanup")
            entry = {"path": relative.as_posix(), "identity": _identity(info)}
            if stat.S_ISREG(info.st_mode):
                digest, info = _hash_file(path)
                if any(mirror in relative.parents for mirror in mirrors) and not raw_copy:
                    raise TmpLifecycleError("Every file in a trial RAW mirror must be an explicit duplicate")
                if raw_copy and (digest != raw_copies[relative.as_posix()] or info.st_nlink != 1):
                    raise TmpLifecycleError("Disposable RAW copy SHA256 or link count does not match")
                if declared and (digest != trials[relative.as_posix()] or info.st_nlink != 1):
                    raise TmpLifecycleError("Disposable trial database SHA256 or link count does not match")
                with path.open("rb") as source:
                    if source.read(16) == b"SQLite format 3\x00" and not declared:
                        raise TmpLifecycleError("SQLite files cannot be removed by run cleanup")
                entry.update(sha256=digest, identity=_identity(info))
                files += 1
                size += info.st_size
            elif declared or raw_copy or not stat.S_ISDIR(info.st_mode):
                raise TmpLifecycleError("Special files cannot be removed by run cleanup")
            entries.append(entry)
    return sorted(entries, key=lambda entry: entry["path"]), files, size


def _build_plan(root: Path, run: Path, *, display_run: Path | None = None,
                include_entries: bool = False) -> dict:
    _path(root)
    _path(run)
    if not run.is_dir():
        raise TmpLifecycleError("Temporary run disappeared during verification")
    manifest_path = run / ".genea-run.json"
    _hash_file(manifest_path)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise TmpLifecycleError("Missing or invalid .genea-run.json") from error
    if (not isinstance(manifest, dict) or type(manifest.get("version")) is not int
            or manifest["version"] not in (1, 2) or manifest.get("state") != "completed"
            or manifest.get("retention") not in ("temporary", "rebuildable")):
        raise TmpLifecycleError("Only completed temporary or rebuildable version 1/2 runs can be removed")
    if manifest.get("protectedTrialDatabaseRetain"):
        raise TmpLifecycleError("Run has explicit protectedTrialDatabaseRetain protection")
    artifacts = manifest.get("durableArtifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise TmpLifecycleError("A completed run requires verified durableArtifacts")
    durable = []
    for artifact in artifacts:
        if not isinstance(artifact, dict) or not isinstance(artifact.get("path"), str):
            raise TmpLifecycleError("Invalid durable artifact")
        relative = Path(artifact["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise TmpLifecycleError("Durable artifact must use a repository-relative path")
        path = _path(root / relative)
        if not any(parent in path.parents for parent in (root / "research/records", root / "research/exports")):
            raise TmpLifecycleError("Durable artifact must be inside research/records or research/exports")
        digest, info = _hash_file(path)
        if digest != artifact.get("sha256"):
            raise TmpLifecycleError("Durable artifact SHA256 does not match")
        durable.append({"path": relative.as_posix(), "sha256": digest, "identity": _identity(info)})
    trials, preservation = _trial_allowlist(root, run, manifest)
    raw_copies, canonical_raw = _raw_allowlist(root, run, manifest)
    entries, files, size = _inventory(run, trials, raw_copies)
    fingerprint = _digest({"entries": entries, "durableArtifacts": durable,
                           "runIdentity": [run.stat().st_dev, run.stat().st_ino],
                           "disposableTrialDatabases": trials, "preservation": preservation,
                           "disposableTrialRawCopies": raw_copies, "canonicalRaw": canonical_raw})
    plan = {"version": manifest["version"], "status": "planned", "runDir": str(display_run or run),
            "fileCount": files, "bytes": size, "fingerprint": fingerprint,
            "durableArtifacts": durable}
    if trials:
        plan["disposableTrialDatabases"] = [{"path": path, "sha256": digest} for path, digest in sorted(trials.items())]
    if raw_copies:
        plan["disposableTrialRawCopies"] = manifest["disposableTrialRawCopies"]
    if trials or raw_copies:
        plan["preservation"] = preservation
    plan["confirmToken"] = _digest({"repository": str(root), "run": plan["runDir"],
                                    "fingerprint": fingerprint})
    if include_entries:
        plan["_entries"] = entries
    return plan


def _remove_verified_snapshot(run: Path, purge: Path, entries: list[dict]) -> None:
    """Move only inventoried files into an isolated tree before deleting them.

    Direct ``rmtree(run)`` would also remove a file created after the final
    inventory.  Moving every verified file separately makes an unexpected
    entry keep the source directory non-empty; the operation then rolls the
    planned files back and preserves the late entry.
    """
    if purge.exists() or purge.is_symlink():
        raise TmpLifecycleError("Cleanup purge directory already exists")

    expected = {entry["path"]: entry for entry in entries}
    directories = sorted(
        (path for path, entry in expected.items()
         if stat.S_ISDIR(entry["identity"][2])),
        key=lambda path: (len(Path(path).parts), path),
    )
    files = sorted(
        path for path, entry in expected.items()
        if stat.S_ISREG(entry["identity"][2])
    )
    moved: list[str] = []
    source_removed = False
    purge.mkdir(mode=0o700)
    try:
        for relative in directories:
            (purge / relative).mkdir()
        for relative in files:
            source = run / relative
            target = purge / relative
            if _identity(source.lstat()) != expected[relative]["identity"]:
                raise TmpLifecycleError("Run changed while staging cleanup")
            source.rename(target)
            moved.append(relative)
            if _identity(target.lstat()) != expected[relative]["identity"]:
                raise TmpLifecycleError("Run changed while staging cleanup")

        for relative in reversed(directories):
            (run / relative).rmdir()
        run.rmdir()
        source_removed = True
        shutil.rmtree(purge)
    except BaseException as error:
        if source_removed:
            if purge.exists() and not run.exists():
                purge.rename(run)
        elif purge.exists():
            run.mkdir(exist_ok=True)
            for relative in directories:
                (run / relative).mkdir(exist_ok=True)
            conflicts = []
            for relative in moved:
                source = purge / relative
                target = run / relative
                if source.exists():
                    if target.exists():
                        conflicts.append(relative)
                    else:
                        source.rename(target)
            if conflicts:
                names = ", ".join(conflicts)
                raise TmpLifecycleError(
                    f"Cleanup rollback preserved conflicting originals in {purge}: {names}"
                ) from error
            shutil.rmtree(purge)
        raise


def _locations(repo_root: str | Path, run_dir: str | Path) -> tuple[Path, Path]:
    root = _path(repo_root)
    candidate = Path(run_dir)
    run = _path(candidate if candidate.is_absolute() else root / candidate)
    temporary = root / "research/tmp"
    if temporary not in run.parents or not root.is_dir() or not run.is_dir():
        raise TmpLifecycleError("Cleanup requires one existing run below research/tmp")
    if any(part.startswith(".cleanup-") for part in run.relative_to(temporary).parts):
        raise TmpLifecycleError("Quarantined cleanup directories require operator recovery")
    return root, run


def plan_tmp_cleanup(repo_root: str | Path, run_dir: str | Path) -> dict:
    """Read-only plan; a completed manifest alone never authorizes deletion."""
    root, run = _locations(repo_root, run_dir)
    return _build_plan(root, run)


def apply_tmp_cleanup(repo_root: str | Path, run_dir: str | Path, confirm_token: str) -> dict:
    """Reverify, quarantine and remove exactly one run selected by its token."""
    root, run = _locations(repo_root, run_dir)
    plan = _build_plan(root, run)
    if not isinstance(confirm_token, str) or confirm_token != plan["confirmToken"]:
        raise TmpLifecycleError("Cleanup confirmation token does not match current run contents")
    quarantine = run.parent / (".cleanup-" + confirm_token)
    lock = run.parent / (".cleanup-lock-" + confirm_token)
    try:
        lock.mkdir()
    except FileExistsError as error:
        raise TmpLifecycleError("Cleanup is already in progress") from error
    moved = False
    try:
        if quarantine.exists() or quarantine.is_symlink():
            raise TmpLifecycleError("Cleanup quarantine already exists")
        # Repeat under the operation lock before renaming the exact run.
        if _build_plan(root, run)["confirmToken"] != confirm_token:
            raise TmpLifecycleError("Run changed before cleanup")
        run.rename(quarantine)
        moved = True
        verified = _build_plan(root, quarantine, display_run=run, include_entries=True)
        if verified["confirmToken"] != confirm_token:
            raise TmpLifecycleError("Run changed during quarantine")
        entries = verified.pop("_entries")
        purge = run.parent / (".purge-" + confirm_token)
        _remove_verified_snapshot(quarantine, purge, entries)
        moved = False
        return dict(plan, status="removed")
    finally:
        try:
            if moved and quarantine.exists() and not run.exists() and not run.is_symlink():
                quarantine.rename(run)
        finally:
            lock.rmdir()
