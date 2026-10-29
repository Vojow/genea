from __future__ import annotations

import tempfile
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from genea.archive_cache import ArchiveHttpCache
from genea.archive_providers import HttpClient


class FakeHttpResponse:
    def __init__(self, url: str, body: bytes, *, etag: str | None = None) -> None:
        self._url = url
        self._body = body
        self.status = 200
        self.headers = Message()
        self.headers["Content-Type"] = "application/json"
        self.headers["Content-Length"] = str(len(body))
        if etag:
            self.headers["ETag"] = etag

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def geturl(self) -> str:
        return self._url

    def read(self, size: int = -1) -> bytes:
        return self._body if size < 0 else self._body[:size]


class ArchiveCacheTests(unittest.TestCase):
    def test_http_client_reuses_fresh_public_response(self):
        url = "https://example.org/public.json"
        response = FakeHttpResponse(url, b'{"ok": true}', etag='"v1"')
        with tempfile.TemporaryDirectory() as temporary:
            client = HttpClient(
                cache=ArchiveHttpCache(Path(temporary)),
                cache_ttl_seconds=3600,
                retries=0,
            )
            with patch("genea.archive_providers.urllib.request.urlopen", return_value=response) as urlopen:
                first = client.get(url, allowed_hosts=("example.org",))
                second = client.get(url, allowed_hosts=("example.org",))
        self.assertEqual(first.cache_status, "miss")
        self.assertEqual(second.cache_status, "hit")
        self.assertEqual(second.body, b'{"ok": true}')
        self.assertEqual(urlopen.call_count, 1)

    def test_stale_response_is_conditionally_revalidated(self):
        url = "https://example.org/public.json"
        accept = "application/json"
        headers = Message()
        headers["ETag"] = '"v1"'
        not_modified = urllib.error.HTTPError(url, 304, "Not Modified", headers, None)
        with tempfile.TemporaryDirectory() as temporary:
            cache = ArchiveHttpCache(Path(temporary))
            cache.store(
                request_url=url,
                accept=accept,
                final_url=url,
                status=200,
                content_type="application/json",
                headers={"etag": '"v1"'},
                body=b'{"cached": true}',
                stored_at=1,
            )
            client = HttpClient(cache=cache, cache_ttl_seconds=1, retries=0)
            with patch("genea.archive_providers.urllib.request.urlopen", side_effect=not_modified) as urlopen:
                result = client.get(url, accept=accept, allowed_hosts=("example.org",))
                request = urlopen.call_args.args[0]
            self.assertIsNotNone(cache.lookup(url, accept))
        self.assertEqual(request.get_header("If-none-match"), '"v1"')
        self.assertEqual(result.cache_status, "revalidated")
        self.assertEqual(result.body, b'{"cached": true}')

    def test_cache_never_persists_secret_query_values(self):
        url = "https://example.org/data?token=private-value&q=public"
        with tempfile.TemporaryDirectory() as temporary:
            cache = ArchiveHttpCache(Path(temporary))
            cache.store(
                request_url=url,
                accept="application/json",
                final_url=url,
                status=200,
                content_type="application/json",
                headers={"set-cookie": "session=private", "etag": '"v1"'},
                body=b"{}",
            )
            metadata = next(Path(temporary).glob("*/*.json")).read_text(encoding="utf-8")
        self.assertNotIn("private-value", metadata)
        self.assertNotIn("session=private", metadata)
        self.assertIn("REDACTED", metadata)

    def test_http_client_bypasses_cache_for_secret_query(self):
        url = "https://example.org/data?token=private-value"
        response = FakeHttpResponse(url, b"{}")
        with tempfile.TemporaryDirectory() as temporary:
            client = HttpClient(
                cache=ArchiveHttpCache(Path(temporary)),
                cache_ttl_seconds=3600,
                retries=0,
            )
            with patch("genea.archive_providers.urllib.request.urlopen", return_value=response) as urlopen:
                client.get(url, allowed_hosts=("example.org",))
                client.get(url, allowed_hosts=("example.org",))
            self.assertEqual(list(Path(temporary).glob("*/*")), [])
        self.assertEqual(urlopen.call_count, 2)

    def test_corrupt_body_is_a_cache_miss(self):
        url = "https://example.org/data"
        with tempfile.TemporaryDirectory() as temporary:
            cache = ArchiveHttpCache(Path(temporary))
            stored = cache.store(
                request_url=url,
                accept="application/json",
                final_url=url,
                status=200,
                content_type="application/json",
                headers={},
                body=b"good",
            )
            _, body_path = cache._paths(stored.key)
            body_path.write_bytes(b"changed")
            self.assertIsNone(cache.lookup(url, "application/json"))


if __name__ == "__main__":
    unittest.main()
