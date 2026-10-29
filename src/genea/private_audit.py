from __future__ import annotations

import base64
import hashlib
import json
import subprocess
from pathlib import Path

from .db import connect
from .storage import SQLCipherBackend, configured_database_backend
from .workspace import research_root, sha256_file, utc_now


def _service_worker_excludes_private_api(worker: str) -> bool:
    """Recognize both legacy root paths and scope-aware private API guards."""

    legacy_root_guard = '"/api/"' in worker
    scoped_guard = (
        '"api/"' in worker
        and "isPrivateRequest(url)" in worker
        and "self.registration.scope" in worker
    )
    return legacy_root_guard or scoped_guard


def _tracked_files(root: Path) -> list[Path]:
    if not (root / ".git").exists():
        return []
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return [root / value.decode("utf-8") for value in result.stdout.split(b"\0") if value]


def audit_private_storage(root: Path) -> dict[str, object]:
    root = Path(root).resolve()
    issues: list[dict[str, str]] = []
    checks: dict[str, object] = {}
    try:
        backend = configured_database_backend(root)
        checks["backend"] = type(backend).__name__
        checks["encrypted"] = backend.encrypted
        checks["encryptionState"] = "encrypted" if backend.encrypted else "plaintext"
        checks["databaseExists"] = backend.path.is_file()
    except Exception as error:
        backend = None
        checks["backend"] = "unavailable"
        checks["encrypted"] = None
        checks["encryptionState"] = "unknown"
        checks["storageErrorType"] = type(error).__name__
        issues.append({"severity": "critical", "code": "storage-configuration-unavailable"})
    plaintext = research_root(root) / "genea.sqlite3"
    checks["plaintextDatabasePresent"] = plaintext.is_file()
    if backend is not None and backend.encrypted and plaintext.is_file():
        issues.append({"severity": "critical", "code": "plaintext-database-alongside-ciphertext"})
    if backend is not None and not backend.encrypted:
        issues.append({"severity": "critical", "code": "canonical-database-not-encrypted"})
    elif backend is not None and backend.path.is_file():
        try:
            report = backend.integrity_report()
            checks["integrity"] = report
            checks["databaseVerificationState"] = "verified"
            if report.get("integrity_check") != "ok" or report.get("foreign_key_issues"):
                issues.append({"severity": "critical", "code": "encrypted-database-integrity-failed"})
        except Exception as error:
            checks["databaseVerificationState"] = "unknown"
            checks["databaseVerificationErrorType"] = type(error).__name__
            issues.append(
                {"severity": "critical", "code": "encrypted-database-verification-unavailable"}
            )
    storage_config = research_root(root) / "storage.json"
    if storage_config.is_file():
        raw = storage_config.read_text(encoding="utf-8")
        try:
            config = json.loads(raw)
        except json.JSONDecodeError:
            config = {}
        forbidden = {"key", "password", "passphrase", "secretValue", "recoveryKey"}
        if forbidden.intersection(config):
            issues.append({"severity": "critical", "code": "inline-secret-in-storage-config"})
    auth_file = research_root(root) / "private-auth.json"
    checks["authVerifierPresent"] = auth_file.is_file()
    if auth_file.is_file():
        try:
            auth = json.loads(auth_file.read_text(encoding="utf-8"))
            if auth.get("algorithm") != "argon2id" or not str(auth.get("verifier", "")).startswith("$argon2id$"):
                issues.append({"severity": "critical", "code": "private-auth-verifier-invalid"})
        except Exception:
            issues.append({"severity": "critical", "code": "private-auth-config-invalid"})
    else:
        issues.append({"severity": "warning", "code": "private-auth-not-configured"})
    sw = root / "frontend" / "public" / "sw.js"
    if not sw.is_file() or not _service_worker_excludes_private_api(sw.read_text(encoding="utf-8")):
        issues.append({"severity": "critical", "code": "service-worker-private-api-not-excluded"})
    embedded = root / "frontend" / "dist" / "data" / "research.json"
    checks["builtPayloadPresent"] = embedded.is_file()
    if embedded.is_file():
        issues.append({"severity": "warning", "code": "built-presentation-payload-present"})
    tracked = _tracked_files(root)
    tracked_private_plain = [
        path.relative_to(root).as_posix()
        for path in tracked
        if "private-archive" in path.parts and path.suffix != ".gpa"
    ]
    checks["trackedPrivatePlaintext"] = tracked_private_plain
    if tracked_private_plain:
        issues.append({"severity": "critical", "code": "tracked-private-archive-plaintext"})
    key_forms: tuple[bytes, ...] = ()
    if isinstance(backend, SQLCipherBackend):
        try:
            key = backend.key_provider.load_key()
            checks["keyAvailability"] = "available" if key else "missing"
            if key:
                key_forms = (key.hex().encode("ascii"), base64.b64encode(key))
        except Exception as error:
            checks["keyAvailability"] = "unknown"
            checks["keyErrorType"] = type(error).__name__
            issues.append({"severity": "critical", "code": "storage-key-verification-unavailable"})
    key_leak_paths: list[str] = []
    if key_forms:
        for path in tracked:
            try:
                if not path.is_file() or path.stat().st_size > 5 * 1024 * 1024:
                    continue
                if path.suffix.lower() in {".sqlite3", ".gpa", ".png", ".jpg", ".jpeg", ".pdf", ".pmtiles"}:
                    continue
                payload = path.read_bytes()
            except OSError:
                continue
            if any(secret in payload for secret in key_forms):
                key_leak_paths.append(path.relative_to(root).as_posix())
    checks["trackedKeyLeaks"] = key_leak_paths
    if key_leak_paths:
        issues.append({"severity": "critical", "code": "data-key-found-in-tracked-file"})
    attachment_report: dict[str, object] = {"count": 0, "issues": 0}
    if backend is not None and backend.encrypted and backend.path.is_file():
        try:
            with connect(root) as connection:
                has_table = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='private_attachments'"
                ).fetchone()
                rows = connection.execute("SELECT * FROM private_attachments").fetchall() if has_table else []
            attachment_issues = 0
            for row in rows:
                path = root / row["encrypted_path"]
                if (
                    not path.is_file()
                    or path.suffix != ".gpa"
                    or sha256_file(path) != row["ciphertext_sha256"]
                ):
                    attachment_issues += 1
            attachment_report = {"count": len(rows), "issues": attachment_issues}
            if attachment_issues:
                issues.append({"severity": "critical", "code": "private-attachment-integrity-failed"})
        except Exception:
            issues.append({"severity": "critical", "code": "private-attachment-audit-failed"})
    checks["attachments"] = attachment_report
    critical = sum(item["severity"] == "critical" for item in issues)
    return {
        "generatedAt": utc_now(),
        "status": "ready" if critical == 0 else "blocked",
        "criticalIssues": critical,
        "issues": issues,
        "checks": checks,
        "fingerprint": hashlib.sha256(
            json.dumps({"issues": issues, "checks": checks}, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest(),
    }


__all__ = ["audit_private_storage"]
