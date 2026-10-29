from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows is not a supported private host.
    fcntl = None  # type: ignore[assignment]


RESEARCH_DIRS = (
    "archive/originals",
    "archive/rendered",
    "archive/browser",
    "records/analyses",
    "exports",
    "tmp",
)
_JSON_LOCKS: dict[str, threading.Lock] = {}
_JSON_LOCKS_GUARD = threading.Lock()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def project_root(value: str | Path | None = None) -> Path:
    return Path(value or ".").expanduser().resolve()


def research_root(root: Path) -> Path:
    return root / "research"


def ensure_workspace(root: Path) -> Path:
    base = research_root(root)
    for relative in RESEARCH_DIRS:
        (base / relative).mkdir(parents=True, exist_ok=True)
    return base


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_id(prefix: str, *parts: str, length: int = 12) -> str:
    payload = "\0".join(parts).encode("utf-8", errors="replace")
    return f"{prefix}_{hashlib.sha256(payload).hexdigest()[:length]}"


def safe_filename(value: str, fallback: str = "document") -> str:
    value = value.strip().replace("\\", "-").replace("/", "-")
    value = re.sub(r"[^0-9A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż._-]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-.")
    return value[:160] or fallback


def search_fold(value: str | None) -> str:
    """Return a stable, accent-insensitive key for human name search."""

    if not value:
        return ""
    translated = value.casefold().translate(str.maketrans({"ł": "l", "đ": "d", "ð": "d"}))
    decomposed = unicodedata.normalize("NFKD", translated)
    plain = "".join(character for character in decomposed if not unicodedata.combining(character))
    return " ".join(plain.split())


@contextmanager
def _serialized_target(path: Path):
    resolved = str(path.resolve())
    with _JSON_LOCKS_GUARD:
        thread_lock = _JSON_LOCKS.setdefault(resolved, threading.Lock())
    digest = hashlib.sha256(resolved.encode("utf-8")).hexdigest()
    lock_path = Path(tempfile.gettempdir()) / f"genea-json-{digest}.lock"
    with thread_lock, lock_path.open("a+b") as lock_handle:
        if fcntl is not None:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def dump_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _serialized_target(path):
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            payload = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode(
                "utf-8"
            )
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(path)
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            temporary.unlink(missing_ok=True)
            raise


def iter_files(base: Path, extensions: Iterable[str] | None = None):
    allowed = {item.lower() for item in extensions} if extensions else None
    for path in sorted(base.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        if allowed is None or path.suffix.lower() in allowed:
            yield path
