from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SAFE_RESPONSE_HEADERS = {"cache-control", "content-type", "date", "etag", "last-modified"}
SECRET_QUERY_MARKERS = {"api_key", "apikey", "key", "password", "secret", "token", "wskey"}


def is_cacheable_public_url(url: str) -> bool:
    parsed = urllib.parse.urlsplit(url)
    return not any(
        any(marker in key.casefold() for marker in SECRET_QUERY_MARKERS)
        for key, _ in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    )


def _redact_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = []
    for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
        if any(marker in key.casefold() for marker in SECRET_QUERY_MARKERS):
            value = "REDACTED"
        query.append((key, value))
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query), ""))


def _cache_key(url: str, accept: str) -> str:
    material = json.dumps(
        {"url": _redact_url(url), "accept": accept},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


@dataclass(frozen=True)
class CachedHttpResponse:
    key: str
    accept: str
    request_url: str
    final_url: str
    status: int
    content_type: str | None
    headers: dict[str, str]
    body: bytes
    stored_at: float

    def is_fresh(self, ttl_seconds: int, now: float | None = None) -> bool:
        return (time.time() if now is None else now) - self.stored_at <= ttl_seconds


class ArchiveHttpCache:
    """Local operational response cache. It is never evidence or a RAW archive."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _paths(self, key: str) -> tuple[Path, Path]:
        directory = self.root / key[:2]
        return directory / f"{key}.json", directory / f"{key}.body"

    def lookup(self, url: str, accept: str) -> CachedHttpResponse | None:
        key = _cache_key(url, accept)
        metadata_path, body_path = self._paths(key)
        if not metadata_path.is_file() or not body_path.is_file():
            return None
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            body = body_path.read_bytes()
            if metadata.get("schemaVersion") != 1:
                return None
            if hashlib.sha256(body).hexdigest() != metadata.get("bodySha256"):
                return None
            return CachedHttpResponse(
                key=key,
                accept=str(metadata.get("accept") or accept),
                request_url=metadata["requestUrl"],
                final_url=metadata["finalUrl"],
                status=int(metadata["status"]),
                content_type=metadata.get("contentType"),
                headers=dict(metadata.get("headers") or {}),
                body=body,
                stored_at=float(metadata["storedAt"]),
            )
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return None

    def store(
        self,
        *,
        request_url: str,
        accept: str,
        final_url: str,
        status: int,
        content_type: str | None,
        headers: dict[str, str],
        body: bytes,
        stored_at: float | None = None,
    ) -> CachedHttpResponse:
        key = _cache_key(request_url, accept)
        metadata_path, body_path = self._paths(key)
        metadata_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        safe_headers = {
            name.casefold(): value
            for name, value in headers.items()
            if name.casefold() in SAFE_RESPONSE_HEADERS
        }
        timestamp = time.time() if stored_at is None else stored_at
        metadata: dict[str, Any] = {
            "schemaVersion": 1,
            "accept": accept,
            "requestUrl": _redact_url(request_url),
            "finalUrl": _redact_url(final_url),
            "status": status,
            "contentType": content_type,
            "headers": safe_headers,
            "storedAt": timestamp,
            "bodySha256": hashlib.sha256(body).hexdigest(),
            "size": len(body),
        }
        temporary_body = body_path.with_suffix(".body.tmp")
        temporary_metadata = metadata_path.with_suffix(".json.tmp")
        temporary_body.write_bytes(body)
        temporary_metadata.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.chmod(temporary_body, 0o600)
        os.chmod(temporary_metadata, 0o600)
        temporary_body.replace(body_path)
        temporary_metadata.replace(metadata_path)
        return CachedHttpResponse(
            key=key,
            accept=accept,
            request_url=metadata["requestUrl"],
            final_url=metadata["finalUrl"],
            status=status,
            content_type=content_type,
            headers=safe_headers,
            body=body,
            stored_at=timestamp,
        )

    def touch(self, cached: CachedHttpResponse, *, headers: dict[str, str] | None = None) -> CachedHttpResponse:
        return self.store(
            request_url=cached.request_url,
            accept=cached.accept,
            final_url=cached.final_url,
            status=cached.status,
            content_type=cached.content_type,
            headers={**cached.headers, **(headers or {})},
            body=cached.body,
        )

    def stats(self) -> dict[str, int]:
        metadata_files = list(self.root.glob("*/*.json"))
        bodies = list(self.root.glob("*/*.body"))
        return {
            "entries": min(len(metadata_files), len(bodies)),
            "bytes": sum(path.stat().st_size for path in bodies if path.is_file()),
        }


__all__ = ["ArchiveHttpCache", "CachedHttpResponse", "is_cacheable_public_url"]
