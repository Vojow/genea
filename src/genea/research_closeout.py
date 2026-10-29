"""Preserve a research checkpoint, then plan and apply exact temporary-run GC.

Reuses the encrypted snapshot store and the existing two-phase run finalizer.
Never commits files, rewrites history, prunes LFS, or deletes operational backups.
"""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path

from .lfs_archive import _atomic_json, _lock
from .lfs_preflight import inspect_lfs_upload
from .private_audit import audit_private_storage
from .storage import configured_database_backend
from .storage_audit import _measure
from .storage_snapshots import create_snapshot
from .tmp_lifecycle import (
    _digest, _hash_file, _inventory, _path, apply_tmp_cleanup, plan_tmp_cleanup,
    preservation_verification_scope, validate_preservation,
)


def _git(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=False)
    if result.returncode:
        raise ValueError(result.stderr.decode(errors="replace").strip() or "Git operation failed")
    return result.stdout


def _published_artifacts(root: Path, manifest: dict, commit: str) -> None:
    artifacts = manifest.get("durableArtifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError("No durable artifacts")
    for artifact in artifacts:
        relative = Path(artifact["path"])
        if relative.is_absolute() or ".." in relative.parts or relative.parts[:2] not in (
            ("research", "records"), ("research", "exports")
        ):
            raise ValueError("Invalid durable artifact path")
        _published_file(root, relative, artifact["sha256"], commit)


def _published_file(root: Path, relative: Path, expected: str, commit: str) -> None:
    actual, metadata = _hash_file(_path(root / relative))
    if actual != expected:
        raise ValueError("Durable artifact changed")
    committed = _git(root, "show", f"{commit}:{relative.as_posix()}")
    pointer = (b"version https://git-lfs.github.com/spec/v1\n"
               + b"oid sha256:" + actual.encode() + b"\n"
               + f"size {metadata.st_size}\n".encode())
    if hashlib.sha256(committed).hexdigest() != actual and committed != pointer:
        raise ValueError("Durable artifact is not preserved in the published commit")


def _trial_manifest(root: Path, run: Path, manifest: dict, preservation: dict) -> dict:
    proposed = copy.deepcopy(manifest)
    trials, raw_copies = [], []
    for path in sorted(run.rglob("*")):
        if path.is_symlink():
            raise ValueError("Symlink inside run")
        if path.is_file() and path.name == "genea.encrypted.sqlite3":
            relative = path.relative_to(run)
            if relative.parent.name != "research":
                raise ValueError("Unknown database location; requires explicit classification")
            digest, metadata = _hash_file(path)
            if metadata.st_nlink != 1:
                raise ValueError("Hard-linked trial database")
            trials.append({"path": relative.as_posix(), "sha256": digest})
        elif path.is_file():
            parts = path.relative_to(run).parts
            for index in range(len(parts) - 3):
                if parts[index:index + 3] != ("research", "archive", "originals"):
                    continue
                canonical = Path(*parts[index:])
                digest, metadata = _hash_file(path)
                if metadata.st_nlink != 1:
                    raise ValueError("Hard-linked trial RAW copy")
                _published_file(root, canonical, digest, preservation["publishedCommit"])
                raw_copies.append({"path": path.relative_to(run).as_posix(),
                                   "canonicalPath": canonical.as_posix(), "sha256": digest})
                break
    if trials or raw_copies:
        proposed.update(version=2, disposableTrialDatabases=trials,
                        disposableTrialRawCopies=raw_copies, preservation=preservation)
    return proposed


def _plan_token(plan: dict) -> str:
    return _digest({key: value for key, value in plan.items() if key != "confirmToken"})


def _output_path(root: Path, value: Path) -> Path:
    path = _path(value if value.is_absolute() else root / value)
    if root / "research/records" not in path.parents:
        raise ValueError("Closeout plans must be preserved under research/records")
    return path


def prepare_closeout(root: Path, snapshot_store: Path, output: Path, *,
                     publish: bool = False, snapshot_id: str | None = None) -> dict:
    """Backup and verify published outcomes; never delete a run during planning."""
    root = _path(root)
    output = _output_path(root, output)
    if output.exists():
        raise ValueError("Plan already exists; apply it or use a new explicit checkpoint")
    backend = configured_database_backend(root)
    if not backend.encrypted:
        raise ValueError("Closeout requires encrypted canonical storage")
    canonical = _path(backend.path)
    canonical_hash, _ = _hash_file(canonical)
    head = _git(root, "rev-parse", "HEAD").decode().strip()
    relative = canonical.relative_to(root).as_posix()
    expected_pointer = (b"version https://git-lfs.github.com/spec/v1\n"
                        + b"oid sha256:" + canonical_hash.encode() + b"\n")
    if not _git(root, "show", f"{head}:{relative}").startswith(expected_pointer):
        raise ValueError("Canonical database must be committed before preservation")
    audit = audit_private_storage(root)
    if audit.get("status") != "ready" or audit.get("criticalIssues"):
        raise ValueError("Privacy audit is not ready")
    store = _path(snapshot_store)
    if store == root or root in store.parents or store in root.parents:
        raise ValueError("Recovery store must be outside the workspace")
    store.mkdir(parents=True, exist_ok=True, mode=0o700)
    if snapshot_id is None:
        snapshot_id = create_snapshot(backend, store, workspace_root=root)["snapshotId"]
    if publish:
        preflight = inspect_lfs_upload(root, "origin/main")
        if preflight["status"] != "ready" or preflight["head"] != head:
            raise ValueError("LFS preflight blocked publication")
        _git(root, "push", "origin", f"{head}:refs/heads/main")
    preservation = {
        "canonicalPath": relative, "canonicalSha256": canonical_hash,
        "snapshotStore": str(store), "snapshotId": snapshot_id,
        "publishedCommit": head, "remote": "origin", "branch": "main",
    }
    proof = validate_preservation(root, preservation)
    candidates, blocked = [], []
    for receipt in sorted((root / "research/tmp").rglob(".genea-run.json")):
        run = receipt.parent
        try:
            original_hash, _ = _hash_file(_path(receipt))
            original = json.loads(receipt.read_text(encoding="utf-8"))
            if (original.get("state") != "completed"
                    or original.get("retention") not in ("temporary", "rebuildable")
                    or original.get("protectedTrialDatabaseRetain")):
                raise ValueError("Active, interrupted or protected run")
            _published_artifacts(root, original, head)
            proposed = _trial_manifest(root, run, original, preservation)
            _inventory(run, {row['path']: row['sha256']
                             for row in proposed.get('disposableTrialDatabases', [])},
                       {row['path']: row['sha256']
                        for row in proposed.get('disposableTrialRawCopies', [])})
            measured = _measure(run)
            if measured["errorCount"] or measured["symlinkCount"]:
                raise ValueError("Run inventory incomplete or contains symlinks")
            candidates.append({
                "run": run.relative_to(root).as_posix(), "originalManifestSha256": original_hash,
                "originalManifest": original, "manifest": proposed,
                "inventory": measured["snapshot"], "bytes": measured["bytes"],
            })
        except (ValueError, OSError, KeyError, TypeError) as error:
            blocked.append({"run": run.relative_to(root).as_posix(), "reason": str(error)})
    # Nested candidate directories must not be deleted twice.
    paths = [Path(item["run"]) for item in candidates]
    if any(a in b.parents for a in paths for b in paths if a != b):
        raise ValueError("Nested cleanup candidates require separate reviewed plans")
    plan = {
        "version": 1, "kind": "research_closeout", "root": str(root),
        "preservation": preservation, "preservationFingerprint": proof["fingerprint"],
        "candidates": candidates, "blocked": blocked,
        "totalBytes": sum(row["bytes"] for row in candidates),
    }
    plan["confirmToken"] = _plan_token(plan)
    _atomic_json(output, plan)
    return {"status": "planned", "planPath": str(output), "confirmToken": plan["confirmToken"],
            "candidateCount": len(candidates), "blockedCount": len(blocked),
            "candidateBytes": plan["totalBytes"], "snapshotId": snapshot_id,
            "publishedCommit": head}


def apply_closeout(root: Path, plan_path: Path, confirm_token: str) -> dict:
    """Revalidate the exact plan and journal each result; delegate actual deletion."""
    root = _path(root)
    path = _output_path(root, plan_path)
    plan = json.loads(path.read_text(encoding="utf-8"))
    if (plan.get("kind") != "research_closeout" or plan.get("version") != 1
            or plan.get("root") != str(root) or not confirm_token
            or plan.get("confirmToken") != confirm_token or _plan_token(plan) != confirm_token):
        raise ValueError("Closeout plan confirmation invalid")
    report_path = path.with_suffix(".receipt.json")
    with _lock(root / "research/tmp/.closeout-operation"), preservation_verification_scope():
        proof = validate_preservation(root, plan["preservation"])
        if proof["fingerprint"] != plan["preservationFingerprint"]:
            raise ValueError("Preservation changed since planning")
        report = (json.loads(report_path.read_text()) if report_path.exists() else
                  {"planToken": confirm_token, "status": "running", "results": []})
        if report.get("planToken") != confirm_token:
            raise ValueError("Existing closeout receipt belongs to another plan")
        if report.get("pendingRun"):
            raise ValueError("Interrupted run requires reconciliation and a fresh plan: " + report["pendingRun"])
        completed = {row["run"] for row in report["results"] if row.get("status") == "removed"}
        _atomic_json(report_path, report)
        for entry in plan["candidates"]:
            if entry["run"] in completed:
                continue
            run = _path(root / entry["run"])
            if root / "research/tmp" not in run.parents:
                raise ValueError("Cleanup target must be a run below research/tmp")
            if _measure(run)["snapshot"] != entry["inventory"]:
                raise ValueError("Run changed after planning: " + entry["run"])
            receipt = run / ".genea-run.json"
            if _hash_file(receipt)[0] != entry["originalManifestSha256"]:
                raise ValueError("Run receipt changed after planning")
            _published_artifacts(root, entry["originalManifest"], plan["preservation"]["publishedCommit"])
            report.update(status="running", pendingRun=entry["run"])
            _atomic_json(report_path, report)
            _atomic_json(receipt, entry["manifest"])
            try:
                cleanup = plan_tmp_cleanup(root, run)
                result = apply_tmp_cleanup(root, run, cleanup["confirmToken"])
            except BaseException:
                # Restore the receipt if untouched; a failed run needs a fresh plan.
                if receipt.exists() and json.loads(receipt.read_text()) == entry["manifest"]:
                    _atomic_json(receipt, entry["originalManifest"])
                report["status"] = "interrupted"
                _atomic_json(report_path, report)
                raise
            report.pop("pendingRun", None)
            report["results"].append({"run": entry["run"], "status": result["status"],
                                      "bytes": result["bytes"], "fingerprint": result["fingerprint"]})
            _atomic_json(report_path, report)
        report.update(status="completed", removedBytes=sum(row["bytes"] for row in report["results"]),
                      removedRuns=len(report["results"]), blocked=plan["blocked"])
        _atomic_json(report_path, report)
    return {"status": report["status"], "receiptPath": str(report_path),
            "removedRuns": report["removedRuns"], "removedBytes": report["removedBytes"],
            "blockedCount": len(plan["blocked"])}
