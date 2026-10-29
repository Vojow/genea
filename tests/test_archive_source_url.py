from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from genea.archive import add_source, get_source
from genea.cli import main
from genea.db import connect


JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"stable archival image"


class FakeDownloadResponse:
    def __init__(self, url: str):
        self._url = url
        self._offset = 0
        self.headers = Message()
        self.headers["Content-Type"] = "image/jpeg"
        self.headers["Content-Length"] = str(len(JPEG_BYTES))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def geturl(self):
        return self._url

    def read(self, size=-1):
        if size < 0:
            size = len(JPEG_BYTES) - self._offset
        chunk = JPEG_BYTES[self._offset:self._offset + size]
        self._offset += len(chunk)
        return chunk


class ArchiveSourceUrlTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def _response_for_request(request, timeout=60):
        return FakeDownloadResponse(request.full_url)

    def test_archive_acquire_uses_stable_source_url_without_persisting_tokens(self):
        stable_url = "https://archive.example/public/item/123"
        token_urls = (
            "https://download.example/scan.jpg?token=secret-one&expires=1",
            "https://download.example/scan.jpg?token=secret-two&expires=2",
        )

        emitted = []
        with patch(
            "genea.archive.urllib.request.urlopen",
            side_effect=self._response_for_request,
        ) as urlopen, patch("genea.cli._json", side_effect=emitted.append):
            for token_url in token_urls:
                exit_code = main([
                    "--root",
                    str(self.root),
                    "archive-acquire",
                    "direct-http",
                    token_url,
                    "--source-url",
                    stable_url,
                ])
                self.assertEqual(exit_code, 0)

        self.assertEqual(
            [call.args[0].full_url for call in urlopen.call_args_list],
            list(token_urls),
        )
        self.assertEqual(emitted[0]["id"], emitted[1]["id"])
        self.assertEqual(emitted[0]["source_url"], stable_url)
        self.assertEqual(emitted[1]["source_url"], stable_url)

        stored = get_source(self.root, emitted[0]["id"])
        metadata = json.loads(stored["metadata_json"])
        self.assertEqual(stored["source_url"], stable_url)
        self.assertTrue(metadata["source_url_override_used"])
        self.assertEqual(metadata["archive_provider"], "direct-http")
        with connect(self.root) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0], 1)

        persisted = json.dumps({"record": stored, "metadata": metadata}, ensure_ascii=False)
        self.assertNotIn("download.example", persisted)
        self.assertNotIn("token=", persisted)
        self.assertNotIn("secret-one", persisted)
        self.assertNotIn("secret-two", persisted)

    def test_remote_add_source_without_override_keeps_final_url_behavior(self):
        request_url = "https://download.example/scan.jpg?request=temporary"
        final_url = "https://cdn.example/scan.jpg?token=legacy-token"
        with patch(
            "genea.archive.urllib.request.urlopen",
            return_value=FakeDownloadResponse(final_url),
        ):
            result = add_source(self.root, request_url)

        self.assertEqual(result["source_url"], final_url)
        self.assertNotIn(
            "source_url_override_used",
            json.loads(result["metadata_json"]),
        )

    def test_source_register_propagates_only_the_stable_viewer_locator(self):
        token_url = "https://download.example/scan.jpg?token=registration-secret"
        stable_url = "https://archive.example/viewer/item/123"
        citation_text = f"Archiwum testowe, trwały widok: {stable_url}"
        emitted = []

        with patch(
            "genea.archive.urllib.request.urlopen",
            side_effect=self._response_for_request,
        ), patch("genea.cli._json", side_effect=emitted.append):
            acquire_exit_code = main([
                "--root",
                str(self.root),
                "archive-acquire",
                "direct-http",
                token_url,
                "--source-url",
                stable_url,
            ])
            source_id = emitted[0]["id"]
            register_exit_code = main([
                "--root",
                str(self.root),
                "source-register",
                source_id,
                "--item-type",
                "civil-registration-image",
                "--citation-text",
                citation_text,
                "--locator",
                stable_url,
            ])

        self.assertEqual(acquire_exit_code, 0)
        self.assertEqual(register_exit_code, 0)
        registration = emitted[1]
        self.assertEqual(registration["source_id"], source_id)

        with connect(self.root) as connection:
            source_item = dict(connection.execute(
                """SELECT id,source_url,locator,citation_text
                   FROM source_items WHERE id=?""",
                (registration["source_item_id"],),
            ).fetchone())
            citation = dict(connection.execute(
                "SELECT id,locator,citation_text FROM citations WHERE id=?",
                (registration["citation_id"],),
            ).fetchone())

        self.assertEqual(source_item["source_url"], stable_url)
        self.assertEqual(source_item["locator"], stable_url)
        self.assertEqual(source_item["citation_text"], citation_text)
        self.assertEqual(citation["locator"], stable_url)
        self.assertEqual(citation["citation_text"], citation_text)
        registered_payload = json.dumps({
            "result": registration,
            "source_item": source_item,
            "citation": citation,
        }, ensure_ascii=False)
        self.assertIn(stable_url, registered_payload)
        self.assertNotIn("download.example", registered_payload)
        self.assertNotIn("registration-secret", registered_payload)

    def test_archive_acquire_rejects_non_http_source_url_before_download(self):
        with patch("genea.archive.urllib.request.urlopen") as urlopen, patch(
            "sys.stderr", new=io.StringIO()
        ):
            exit_code = main([
                "--root",
                str(self.root),
                "archive-acquire",
                "direct-http",
                "https://download.example/scan.jpg?token=secret",
                "--source-url",
                "file:///private/source",
            ])

        self.assertEqual(exit_code, 2)
        urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
