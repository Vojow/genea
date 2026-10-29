from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from genea.archive import add_source, attach_source_artifact, register_source_item
from genea.cli import _parser, main
from genea.db import connect, initialize
from genea.workspace import sha256_file


class SourceArtifactAttachTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        initialize(self.root)
        self.primary = self._add_source("primary.jpg", b"primary", "primary")
        registration = register_source_item(
            self.root,
            self.primary["id"],
            item_type="register",
            citation_text="Księga testowa.",
            locator="jednostka testowa",
        )
        self.source_item_id = registration["source_item_id"]

    def tearDown(self):
        self.temporary.cleanup()

    def _add_source(self, name: str, content: bytes, slug: str) -> dict:
        path = self.root / name
        path.write_bytes(content)
        return add_source(
            self.root,
            str(path),
            title=name,
            source_url=f"https://example.test/{slug}",
        )

    def test_attach_is_idempotent_and_does_not_create_source_item_or_reading(self):
        auxiliary = self._add_source("scan-2.jpg", b"second scan", "scan-2")

        first = attach_source_artifact(
            self.root,
            auxiliary["id"],
            self.source_item_id,
            citation_text="Księga testowa, skan 2.",
            locator="skan 2",
            actor="test-curator",
        )
        second = attach_source_artifact(
            self.root,
            auxiliary["id"],
            self.source_item_id,
            citation_text="Księga testowa, skan 2.",
            locator="skan 2",
            actor="test-curator",
        )

        self.assertEqual(first, second)
        with connect(self.root) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM source_items").fetchone()[0],
                1,
            )
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM citations").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM readings").fetchone()[0], 0)
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM audit_log WHERE action='source_artifact_attach'"
                ).fetchone()[0],
                1,
            )
            item = connection.execute(
                "SELECT legacy_source_id FROM source_items WHERE id = ?",
                (self.source_item_id,),
            ).fetchone()
            self.assertEqual(item["legacy_source_id"], self.primary["id"])
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_attach_rejects_missing_source_item(self):
        auxiliary = self._add_source("scan-2.jpg", b"second scan", "scan-2")
        with self.assertRaisesRegex(KeyError, "source_item"):
            attach_source_artifact(
                self.root,
                auxiliary["id"],
                "item-missing",
                citation_text="Księga testowa, skan 2.",
            )

    def test_attach_requires_nonempty_citation(self):
        auxiliary = self._add_source("scan-2.jpg", b"second scan", "scan-2")
        with self.assertRaisesRegex(ValueError, "citation_text"):
            attach_source_artifact(
                self.root,
                auxiliary["id"],
                self.source_item_id,
                citation_text="   ",
            )

    def test_attach_rejects_tampered_raw(self):
        auxiliary = self._add_source("scan-2.jpg", b"second scan", "scan-2")
        (self.root / auxiliary["archive_path"]).write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            attach_source_artifact(
                self.root,
                auxiliary["id"],
                self.source_item_id,
                citation_text="Księga testowa, skan 2.",
            )

    def test_attach_rejects_raw_path_outside_originals(self):
        auxiliary = self._add_source("scan-2.jpg", b"second scan", "scan-2")
        outside = self.root / "outside.jpg"
        outside.write_bytes(b"outside")
        with connect(self.root) as connection:
            connection.execute(
                "UPDATE sources SET archive_path = ?, sha256 = ? WHERE id = ?",
                ("outside.jpg", sha256_file(outside), auxiliary["id"]),
            )
        with self.assertRaisesRegex(ValueError, "wychodzi poza"):
            attach_source_artifact(
                self.root,
                auxiliary["id"],
                self.source_item_id,
                citation_text="Księga testowa, skan 2.",
            )

    def test_attach_rejects_source_registered_to_another_source_item(self):
        auxiliary = self._add_source("scan-2.jpg", b"second scan", "scan-2")
        other = register_source_item(
            self.root,
            auxiliary["id"],
            item_type="register",
            citation_text="Druga księga.",
        )
        with self.assertRaisesRegex(ValueError, "innego source_item"):
            attach_source_artifact(
                self.root,
                auxiliary["id"],
                self.source_item_id,
                citation_text="Księga testowa, skan 2.",
            )
        self.assertNotEqual(other["source_item_id"], self.source_item_id)

    def test_attach_rejects_source_assigned_to_another_artifact(self):
        auxiliary = self._add_source("scan-2.jpg", b"second scan", "scan-2")
        with connect(self.root) as connection:
            connection.execute(
                """
                INSERT INTO artifacts(
                    id,source_item_id,legacy_source_id,artifact_type,path,
                    media_type,sha256,immutable,created_at,metadata_json
                ) VALUES (?,?,?,'RAW',?,?,?,1,?,'{}')
                """,
                (
                    "artifact-custom",
                    self.source_item_id,
                    auxiliary["id"],
                    auxiliary["archive_path"],
                    auxiliary["media_type"],
                    auxiliary["sha256"],
                    auxiliary["captured_at"],
                ),
            )
        with self.assertRaisesRegex(ValueError, "innego artifact/source_item"):
            attach_source_artifact(
                self.root,
                auxiliary["id"],
                self.source_item_id,
                citation_text="Księga testowa, skan 2.",
            )

    def test_attach_rejects_conflicting_replay(self):
        auxiliary = self._add_source("scan-2.jpg", b"second scan", "scan-2")
        attach_source_artifact(
            self.root,
            auxiliary["id"],
            self.source_item_id,
            citation_text="Księga testowa, skan 2.",
            locator="skan 2",
        )
        with self.assertRaisesRegex(ValueError, "różni się od żądania"):
            attach_source_artifact(
                self.root,
                auxiliary["id"],
                self.source_item_id,
                citation_text="Inne cytowanie.",
                locator="skan 2",
            )
        with connect(self.root) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM citations").fetchone()[0], 2)

    def test_explicit_citation_update_is_audited_and_idempotent(self):
        auxiliary = self._add_source("scan-2.jpg", b"second scan", "scan-2")
        initial = attach_source_artifact(
            self.root,
            auxiliary["id"],
            self.source_item_id,
            citation_text="Księga testowa, skan 2.",
            locator="skan 2",
        )
        with connect(self.root) as connection:
            artifact_before = dict(
                connection.execute(
                    "SELECT * FROM artifacts WHERE id = ?", (initial["artifact_id"],)
                ).fetchone()
            )
            source_item_before = dict(
                connection.execute(
                    "SELECT * FROM source_items WHERE id = ?", (self.source_item_id,)
                ).fetchone()
            )

        updated = attach_source_artifact(
            self.root,
            auxiliary["id"],
            self.source_item_id,
            citation_text="Księga testowa, karta 2.",
            locator="karta 2 recto",
            actor="test-curator",
            update_citation=True,
        )
        replay = attach_source_artifact(
            self.root,
            auxiliary["id"],
            self.source_item_id,
            citation_text="Księga testowa, karta 2.",
            locator="karta 2 recto",
            actor="test-curator",
            update_citation=True,
        )
        default_replay = attach_source_artifact(
            self.root,
            auxiliary["id"],
            self.source_item_id,
            citation_text="Księga testowa, karta 2.",
            locator="karta 2 recto",
        )

        self.assertEqual(initial, updated)
        self.assertEqual(updated, replay)
        self.assertEqual(replay, default_replay)
        with connect(self.root) as connection:
            artifact_after = dict(
                connection.execute(
                    "SELECT * FROM artifacts WHERE id = ?", (updated["artifact_id"],)
                ).fetchone()
            )
            source_item_after = dict(
                connection.execute(
                    "SELECT * FROM source_items WHERE id = ?", (self.source_item_id,)
                ).fetchone()
            )
            citation = dict(
                connection.execute(
                    "SELECT * FROM citations WHERE id = ?", (updated["citation_id"],)
                ).fetchone()
            )
            audit_rows = connection.execute(
                """
                SELECT * FROM audit_log
                WHERE action='source_citation_update' AND object_id = ?
                """,
                (updated["citation_id"],),
            ).fetchall()

        self.assertEqual(artifact_after, artifact_before)
        self.assertEqual(source_item_after, source_item_before)
        self.assertEqual(citation["id"], initial["citation_id"])
        self.assertEqual(citation["citation_text"], "Księga testowa, karta 2.")
        self.assertEqual(citation["locator"], "karta 2 recto")
        self.assertEqual(len(audit_rows), 1)
        before = json.loads(audit_rows[0]["before_json"])
        after = json.loads(audit_rows[0]["after_json"])
        self.assertEqual(before["id"], after["id"])
        self.assertEqual(before["citation_text"], "Księga testowa, skan 2.")
        self.assertEqual(before["locator"], "skan 2")
        self.assertEqual(after["citation_text"], "Księga testowa, karta 2.")
        self.assertEqual(after["locator"], "karta 2 recto")
        self.assertEqual(audit_rows[0]["actor"], "test-curator")

    def test_cli_command_is_exposed_and_wired(self):
        auxiliary = self._add_source("scan-2.jpg", b"second scan", "scan-2")
        parsed = _parser().parse_args(
            [
                "source-artifact-attach",
                auxiliary["id"],
                self.source_item_id,
                "--citation-text",
                "Księga testowa, skan 2.",
                "--locator",
                "skan 2",
            ]
        )
        self.assertEqual(parsed.command, "source-artifact-attach")
        self.assertFalse(parsed.update_citation)
        self.assertFalse(parsed.rehome_existing)

        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(
                [
                    "--root",
                    str(self.root),
                    "source-artifact-attach",
                    auxiliary["id"],
                    self.source_item_id,
                    "--citation-text",
                    "Księga testowa, skan 2.",
                    "--locator",
                    "skan 2",
                    "--actor",
                    "cli-test",
                ]
            )
        self.assertEqual(exit_code, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["source_id"], auxiliary["id"])
        self.assertEqual(result["source_item_id"], self.source_item_id)

        update_output = io.StringIO()
        with redirect_stdout(update_output):
            update_exit_code = main(
                [
                    "--root",
                    str(self.root),
                    "source-artifact-attach",
                    auxiliary["id"],
                    self.source_item_id,
                    "--citation-text",
                    "Księga testowa, karta 2.",
                    "--locator",
                    "karta 2 recto",
                    "--actor",
                    "cli-update-test",
                    "--update-citation",
                ]
            )
        self.assertEqual(update_exit_code, 0)
        self.assertEqual(json.loads(update_output.getvalue())["citation_id"], result["citation_id"])
        with connect(self.root) as connection:
            citation = connection.execute(
                "SELECT citation_text,locator FROM citations WHERE id = ?",
                (result["citation_id"],),
            ).fetchone()
        self.assertEqual(citation["citation_text"], "Księga testowa, karta 2.")
        self.assertEqual(citation["locator"], "karta 2 recto")

    def test_explicit_rehome_moves_raw_and_citation_and_preserves_alias(self):
        auxiliary = self._add_source("scan-2.jpg", b"second scan", "scan-2")
        old = register_source_item(
            self.root,
            auxiliary["id"],
            item_type="register",
            citation_text="Techniczny item skanu 2.",
            locator="skan 2",
        )

        moved = attach_source_artifact(
            self.root,
            auxiliary["id"],
            self.source_item_id,
            citation_text="Techniczny item skanu 2.",
            locator="skan 2",
            actor="test-curator",
            rehome_existing=True,
        )
        replay = attach_source_artifact(
            self.root,
            auxiliary["id"],
            self.source_item_id,
            citation_text="Techniczny item skanu 2.",
            locator="skan 2",
        )

        self.assertEqual(moved, replay)
        with connect(self.root) as connection:
            artifact = connection.execute(
                "SELECT source_item_id FROM artifacts WHERE id=?",
                (moved["artifact_id"],),
            ).fetchone()
            citation = connection.execute(
                "SELECT source_item_id FROM citations WHERE id=?",
                (moved["citation_id"],),
            ).fetchone()
            alias = connection.execute(
                "SELECT parent_id,item_type,metadata_json FROM source_items WHERE id=?",
                (old["source_item_id"],),
            ).fetchone()
            audit_count = connection.execute(
                "SELECT COUNT(*) FROM audit_log WHERE action='source_artifact_rehome'"
            ).fetchone()[0]
        self.assertEqual(artifact["source_item_id"], self.source_item_id)
        self.assertEqual(citation["source_item_id"], self.source_item_id)
        self.assertEqual(alias["parent_id"], self.source_item_id)
        self.assertEqual(alias["item_type"], "source_alias")
        self.assertEqual(
            json.loads(alias["metadata_json"])["superseded_by_source_item_id"],
            self.source_item_id,
        )
        self.assertEqual(audit_count, 1)

    def test_rehome_rejects_source_item_with_readings(self):
        auxiliary = self._add_source("scan-2.jpg", b"second scan", "scan-2")
        old = register_source_item(
            self.root,
            auxiliary["id"],
            item_type="register",
            citation_text="Techniczny item skanu 2.",
        )
        with connect(self.root) as connection:
            connection.execute(
                """INSERT INTO readings(
                       id,source_item_id,reading_type,status,created_at
                   ) VALUES ('reading-blocker',?,'contextual','draft','2026-08-31')""",
                (old["source_item_id"],),
            )
        with self.assertRaisesRegex(ValueError, "blokery"):
            attach_source_artifact(
                self.root,
                auxiliary["id"],
                self.source_item_id,
                citation_text="Techniczny item skanu 2.",
                rehome_existing=True,
            )


if __name__ == "__main__":
    unittest.main()
