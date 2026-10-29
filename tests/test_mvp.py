from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from genea.analysis import codex_command, import_analysis
from genea.archive import add_source, get_source, list_sources, register_source_item
from genea.db import connect, initialize
from genea.project_index import cross_check, index_project
from genea.render import visual_files
from genea.research import add_hypothesis, link_evidence, list_hypotheses


class GeneaMvpTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        initialize(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def _add_image(self):
        image = self.root / "act-1889.jpg"
        image.write_bytes(b"not-a-real-image-but-stable-test-bytes")
        return add_source(
            self.root,
            str(image),
            title="Akt urodzenia 1889",
            service="metryki",
            source_url="https://metryki.genealodzy.pl/example",
        )

    def test_source_is_archived_with_checksum_and_deduplicated(self):
        first = self._add_image()
        second = add_source(
            self.root,
            str(self.root / "act-1889.jpg"),
            title="Ten sam akt, poprawiony tytuł",
            service="metryki",
            source_url="https://metryki.genealodzy.pl/example",
        )
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(list_sources(self.root)), 1)
        stored = get_source(self.root, first["id"])
        self.assertEqual(stored["title"], "Ten sam akt, poprawiony tytuł")
        self.assertTrue((self.root / stored["archive_path"]).is_file())
        self.assertEqual(len(stored["sha256"]), 64)

    def test_image_is_direct_visual_input(self):
        source = self._add_image()
        images = visual_files(self.root, source["id"])
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0].suffix, ".jpg")

    def test_source_registration_is_idempotent_and_creates_no_reading(self):
        source = self._add_image()
        first = register_source_item(
            self.root,
            source["id"],
            item_type="civil-registration-image",
            date_text="1889",
            locator="akt 12, prawa karta",
            citation_text="Testowe cytowanie, akt 12.",
            source_quality="Derivative",
        )
        second = register_source_item(
            self.root,
            source["id"],
            item_type="civil-registration-image",
            date_text="1889",
            locator="akt 12, prawa karta",
            citation_text="Testowe cytowanie, akt 12.",
            source_quality="Derivative",
        )
        self.assertEqual(first, second)
        with connect(self.root) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM source_items").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM citations").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM readings").fetchone()[0], 0)
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM audit_log WHERE action='source_register'"
                ).fetchone()[0],
                1,
            )

    def test_source_registration_rejects_tampered_raw(self):
        source = self._add_image()
        (self.root / source["archive_path"]).write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            register_source_item(
                self.root,
                source["id"],
                item_type="civil-registration-image",
                citation_text="Testowe cytowanie.",
            )

    def test_source_registration_rejects_conflicting_reregistration(self):
        source = self._add_image()
        register_source_item(
            self.root,
            source["id"],
            item_type="civil-registration-image",
            citation_text="Testowe cytowanie.",
            source_quality="Derivative",
        )
        with self.assertRaisesRegex(ValueError, "różni się od żądania"):
            register_source_item(
                self.root,
                source["id"],
                item_type="civil-registration-image",
                citation_text="Inne cytowanie.",
                source_quality="Original",
            )

    def test_project_index_is_searchable_and_read_only(self):
        sources = self.root / "sources"
        sources.mkdir()
        note = sources / "rodzina.md"
        original = "Jan Kowalski, Łomża, rok 1889. Hipoteza robocza.\n"
        note.write_text(original, encoding="utf-8")
        result = index_project(self.root, sources)
        matches = cross_check(self.root, "Kowalski 1889")
        self.assertEqual(result["indexed"], 1)
        self.assertEqual(matches[0]["kind"], "project-file")
        self.assertIn("Kowalski", matches[0]["snippet"])
        self.assertEqual(note.read_text(encoding="utf-8"), original)

    def test_analysis_populates_people_and_relations(self):
        source = self._add_image()
        payload = {
            "source_id": source["id"],
            "review_status": "needs-review",
            "people": [
                {
                    "name_original": "Иван Ковальский",
                    "name_normalized": "Jan Kowalski",
                    "roles": ["dziecko"],
                    "confidence": 0.8,
                    "notes": None,
                    "evidence": [{"image": "act.jpg", "region": "środek", "quote": "Иван"}],
                },
                {
                    "name_original": "Петр Ковальский",
                    "name_normalized": "Piotr Kowalski",
                    "roles": ["ojciec"],
                    "confidence": 0.7,
                    "notes": None,
                    "evidence": [],
                },
            ],
            "relationships": [
                {
                    "subject": "Jan Kowalski",
                    "type": "child-of",
                    "object": "Piotr Kowalski",
                    "date": "1889",
                    "place": "Łomża",
                    "confidence": 0.75,
                    "evidence": [],
                }
            ],
        }
        result = import_analysis(self.root, payload, model="test-model")
        self.assertEqual(result["people"], 2)
        self.assertEqual(result["relationships"], 1)
        with connect(self.root) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM entities").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM relations").fetchone()[0], 1)
        saved = json.loads((self.root / result["result_path"]).read_text(encoding="utf-8"))
        self.assertEqual(saved["source_id"], source["id"])

    def test_codex_plan_attaches_the_original_image_read_only(self):
        source = self._add_image()
        (self.root / "schemas").mkdir()
        (self.root / "prompts").mkdir()
        (self.root / "schemas" / "act-analysis.schema.json").write_text("{}", encoding="utf-8")
        (self.root / "prompts" / "act-analysis.md").write_text(
            "Źródło {source_id}; {source_title}; {source_service}; "
            "{source_url}; kontekst: {researcher_context}",
            encoding="utf-8",
        )
        with mock.patch("genea.analysis.shutil.which", return_value="/usr/local/bin/codex"):
            command, output = codex_command(
                self.root,
                source["id"],
                researcher_context="wariant nazwiska Kowalewski",
            )
        self.assertIn("--image", command)
        self.assertIn("--output-schema", command)
        self.assertIn("read-only", command)
        prompt_index = next(
            index for index, item in enumerate(command)
            if "wariant nazwiska Kowalewski" in item
        )
        self.assertLess(prompt_index, command.index("--image"))
        self.assertTrue(command[-1].endswith(".jpg"))
        self.assertEqual(output.parent.name, "tmp")

    def test_hypothesis_can_link_to_source(self):
        source = self._add_image()
        hypothesis = add_hypothesis(
            self.root,
            "Jan z aktu jest synem Piotra",
            confidence=0.4,
        )
        evidence = link_evidence(
            self.root,
            hypothesis["id"],
            source["id"],
            stance="supports",
            note="Imię ojca jest zgodne",
        )
        self.assertEqual(evidence["stance"], "supports")
        self.assertEqual(list_hypotheses(self.root)[0]["evidence_count"], 1)


if __name__ == "__main__":
    unittest.main()
