from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from genea.analysis import import_analysis
from genea.archive import add_source, attach_source_artifact
from genea.db import connect, initialize


class AnalysisV2Tests(unittest.TestCase):
    def test_schema_accepts_separate_agent_review_artifact(self):
        schema = json.loads(
            (Path(__file__).resolve().parents[1] / "schemas" / "act-analysis.schema.json").read_text()
        )
        reading_types = schema["$defs"]["reading"]["properties"]["reading_type"]["enum"]
        self.assertIn("agent_review", reading_types)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        initialize(self.root)
        image = self.root / "act.jpg"
        image.write_bytes(b"stable-source-image")
        self.source = add_source(self.root, str(image), title="Akt testowy")

    def tearDown(self):
        self.temporary.cleanup()

    def _payload(self) -> dict:
        return {
            "schema_version": "2.0",
            "source_id": self.source["id"],
            "document_type": "birth-baptism",
            "languages": ["rosyjski"],
            "review_status": "needs-review",
            "summary_pl": "Akt urodzenia dziecka; dane wymagają niezależnej kontroli.",
            "transcription_original": "Иван Ковальский",
            "translation_pl": "Jan Kowalski",
            "reading": {
                "reading_id": "test-reading",
                "reading_type": "blind",
                "version": 1,
                "supersedes_analysis_id": None,
                "context_disclosed": False,
                "reader": "model-testowy",
                "method_notes": "Odczyt bez podania oczekiwanego nazwiska.",
            },
            "classifications": {
                "source": "Original",
                "information": "Primary",
                "evidence": "Direct",
                "rationale": "Obraz pierwotnego aktu; klasyfikacja nie potwierdza tożsamości.",
            },
            "layers": {
                layer: [
                    {
                        "segment_id": f"{layer}-1",
                        "text": text,
                        "language": language,
                        "image": "act.jpg",
                        "region": "środek strony",
                        "sequence": 0,
                        "read_status": "Probable",
                    }
                ]
                for layer, text, language in (
                    ("transcription", "Иван Ковальский", "rosyjski"),
                    ("transliteration", "Ivan Kovalski", "polski"),
                    ("translation", "Jan Kowalski", "polski"),
                    ("extract", "Dziecko: Jan Kowalski", "polski"),
                )
            },
            "informant": {
                "participant_ref": "father",
                "name_as_written": "Петр Ковальский",
                "role": "zgłaszający-ojciec",
                "information_class": "Primary",
                "basis": "Osobiste zgłoszenie urodzenia.",
            },
            "events": [
                {
                    "event_id": "birth",
                    "event_type": "birth",
                    "date": {
                        "as_written": "1/13 stycznia 1889",
                        "normalized": "1889-01-13",
                        "calendar": "both",
                        "read_status": "Certain",
                    },
                    "place": {
                        "as_written": "Łomża",
                        "normalized": "Łomża",
                        "place_ref": None,
                        "read_status": "Certain",
                    },
                    "participants": [
                        {
                            "participant_id": "child",
                            "person_ref": None,
                            "name_original": "Иван Ковальский",
                            "name_normalized": "Jan Kowalski",
                            "role": "child",
                            "sex": "M",
                            "age": None,
                            "occupation": None,
                            "residence": "Łomża",
                            "is_informant": False,
                            "read_status": "Probable",
                            "evidence": [
                                {
                                    "image": "act.jpg",
                                    "region": "środek strony",
                                    "quote": "Иван",
                                    "segment_ref": "transcription-1",
                                }
                            ],
                        },
                        {
                            "participant_id": "father",
                            "person_ref": None,
                            "name_original": "Петр Ковальский",
                            "name_normalized": "Piotr Kowalski",
                            "role": "father",
                            "sex": "M",
                            "age": "32",
                            "occupation": "rolnik",
                            "residence": "Łomża",
                            "is_informant": True,
                            "read_status": "Certain",
                            "evidence": [
                                {
                                    "image": "act.jpg",
                                    "region": "środek strony",
                                    "quote": "Петр",
                                    "segment_ref": "transcription-1",
                                }
                            ],
                        },
                    ],
                    "read_status": "Probable",
                    "evidence": [],
                }
            ],
            "field_readings": [
                {
                    "field_path": "events.birth.participants.child.surname",
                    "proposed_reading": "Kowalski",
                    "alternatives": ["Kowalewski"],
                    "read_status": "Ambiguous",
                    "critical": True,
                    "reason": "Zatarte połączenie liter.",
                    "evidence": [
                        {
                            "image": "act.jpg",
                            "region": "środek strony",
                            "quote": "Ковал...",
                            "segment_ref": "transcription-1",
                        }
                    ],
                }
            ],
            "artifact_provenance": [],
            "independent_checks": [],
            "uncertainties": [
                {
                    "field_path": "events.birth.participants.child.surname",
                    "fragment": "Ковал...",
                    "proposed_reading": "Kowalski",
                    "alternatives": ["Kowalewski"],
                    "read_status": "Ambiguous",
                    "reason": "Zatarte litery.",
                    "next_action": "Drugi odczyt kontekstowy.",
                    "evidence": [],
                }
            ],
            "people": [],
            "dates": [],
            "places": [],
            "relationships": [],
            "uncertain_fragments": [],
            "research_notes": ["Wykonać drugi odczyt."],
        }

    def test_v2_import_populates_evidence_tables_without_canonical_people_or_claims(self):
        result = import_analysis(self.root, self._payload(), model="test-model")
        self.assertTrue(result["v2"]["v2_detected"])
        with connect(self.root) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM readings").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM reading_versions").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM transcript_segments").fetchone()[0], 4)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM field_readings").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM person_occurrences").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM occurrence_readings").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM event_readings").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM participations").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM source_assertions").fetchone()[0], 3)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM informants").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM persons").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM entities").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM claims").fetchone()[0], 0)
            statuses = {
                row[0] for row in connection.execute("SELECT DISTINCT verification_status FROM source_assertions")
            }
            self.assertEqual(statuses, {"imported_unverified"})

    def test_second_reading_is_versioned_and_supersedes_without_resolving(self):
        first = import_analysis(self.root, self._payload(), model="test-model")
        second_payload = copy.deepcopy(self._payload())
        second_payload["reading"].update(
            {
                "reading_id": "test-contextual-reading",
                "reading_type": "contextual",
                "version": 1,
                "supersedes_analysis_id": first["analysis_id"],
                "context_disclosed": True,
            }
        )
        second_payload["summary_pl"] += " Odczyt kontekstowy."
        second = import_analysis(self.root, second_payload, model="test-model")
        self.assertNotEqual(first["analysis_id"], second["analysis_id"])
        with connect(self.root) as connection:
            versions = connection.execute(
                "SELECT id, supersedes_id, status FROM reading_versions ORDER BY created_at, id"
            ).fetchall()
            self.assertEqual(len(versions), 2)
            second_row = next(row for row in versions if row["supersedes_id"] is not None)
            self.assertIn(second_row["supersedes_id"], {row["id"] for row in versions})
            self.assertNotEqual(second_row["status"], "Confirmed")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM claims").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM person_occurrences").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM event_readings").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM occurrence_readings").fetchone()[0], 4)

    def test_v2_import_can_target_a_child_source_item_in_the_same_source_hierarchy(self):
        import_analysis(self.root, self._payload(), model="test-model")
        with connect(self.root) as connection:
            parent_id = connection.execute(
                "SELECT id FROM source_items WHERE legacy_source_id=?",
                (self.source["id"],),
            ).fetchone()[0]
            connection.execute(
                """INSERT INTO source_items(
                       id,parent_id,title,item_type,source_quality,verification_status
                   ) VALUES ('item-child',?,'Akt 6','act','Indeterminable','imported_unverified')""",
                (parent_id,),
            )
        payload = self._payload()
        payload["source_item_id"] = "item-child"
        import_analysis(self.root, payload, model="test-model")
        with connect(self.root) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM readings WHERE source_item_id='item-child'"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM events WHERE source_item_id='item-child'"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM person_occurrences WHERE source_item_id='item-child'"
                ).fetchone()[0],
                2,
            )

    def test_v2_import_rejects_an_unrelated_explicit_source_item(self):
        other_image = self.root / "other.jpg"
        other_image.write_bytes(b"other-source-image")
        other = add_source(self.root, str(other_image), title="Inne źródło")
        other_payload = self._payload()
        other_payload["source_id"] = other["id"]
        other_payload["reading"]["reading_id"] = "other-reading"
        import_analysis(self.root, other_payload, model="test-model")
        with connect(self.root) as connection:
            other_item_id = connection.execute(
                "SELECT id FROM source_items WHERE legacy_source_id=?",
                (other["id"],),
            ).fetchone()[0]
        payload = self._payload()
        payload["source_item_id"] = other_item_id
        with self.assertRaisesRegex(ValueError, "nie należy do hierarchii"):
            import_analysis(self.root, payload, model="test-model")

    def test_v2_import_accepts_explicit_item_owning_rehomed_legacy_artifact(self):
        with connect(self.root) as connection:
            connection.execute(
                """INSERT INTO source_items(
                       id,title,item_type,source_quality,verification_status
                   ) VALUES ('item-register','Księga testowa','register',
                             'Derivative','source_verified')"""
            )
        attached = attach_source_artifact(
            self.root,
            self.source["id"],
            "item-register",
            citation_text="Księga testowa, skan 1",
            actor="test-curator",
            rehome_existing=True,
        )
        payload = self._payload()
        payload["source_item_id"] = "item-register"
        for layer in payload["layers"].values():
            layer[0]["artifact_id"] = attached["artifact_id"]

        result = import_analysis(self.root, payload, model="test-model")

        with connect(self.root) as connection:
            reading = connection.execute(
                """SELECT r.source_item_id,r.artifact_id
                   FROM readings r JOIN reading_versions rv ON rv.reading_id=r.id
                   WHERE r.legacy_analysis_id=?""",
                (result["analysis_id"],),
            ).fetchone()
            self.assertEqual(reading["source_item_id"], "item-register")
            self.assertEqual(reading["artifact_id"], attached["artifact_id"])
            segment_owners = connection.execute(
                """SELECT DISTINCT a.source_item_id
                   FROM transcript_segments ts
                   JOIN reading_versions rv ON rv.id=ts.reading_version_id
                   JOIN readings r ON r.id=rv.reading_id
                   JOIN artifacts a ON a.id=ts.artifact_id
                   WHERE r.legacy_analysis_id=?""",
                (result["analysis_id"],),
            ).fetchall()
            self.assertEqual(
                [row["source_item_id"] for row in segment_owners],
                ["item-register"],
            )

    def test_v2_import_accepts_explicit_artifact_from_source_item_ancestor(self):
        import_analysis(self.root, self._payload(), model="test-model")
        with connect(self.root) as connection:
            parent_id = connection.execute(
                "SELECT id FROM source_items WHERE legacy_source_id=?",
                (self.source["id"],),
            ).fetchone()[0]
            artifact_id = connection.execute(
                "SELECT id FROM artifacts WHERE legacy_source_id=?",
                (self.source["id"],),
            ).fetchone()[0]
            connection.execute(
                """INSERT INTO source_items(
                       id,parent_id,title,item_type,source_quality,verification_status
                   ) VALUES ('item-child-artifact',?,'Akt 7','act','Indeterminable','imported_unverified')""",
                (parent_id,),
            )
        payload = self._payload()
        payload["source_item_id"] = "item-child-artifact"
        for layer in payload["layers"].values():
            layer[0]["artifact_id"] = artifact_id

        schema = json.loads(
            (Path(__file__).resolve().parents[1] / "schemas" / "act-analysis.schema.json").read_text(
                encoding="utf-8"
            )
        )
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        validator.validate(payload)
        invalid_payload = copy.deepcopy(payload)
        invalid_payload["layers"]["extract"][0]["artifact_id"] = ""
        with self.assertRaises(ValidationError):
            validator.validate(invalid_payload)

        result = import_analysis(self.root, payload, model="test-model")

        with connect(self.root) as connection:
            owners = connection.execute(
                """SELECT DISTINCT a.source_item_id
                   FROM transcript_segments ts
                   JOIN reading_versions rv ON rv.id=ts.reading_version_id
                   JOIN readings r ON r.id=rv.reading_id
                   JOIN artifacts a ON a.id=ts.artifact_id
                   WHERE r.legacy_analysis_id=?""",
                (result["analysis_id"],),
            ).fetchall()
            self.assertEqual([row["source_item_id"] for row in owners], [parent_id])

    def test_v2_import_rejects_raw_artifact_owned_by_unrelated_source_item(self):
        other_image = self.root / "other-raw.jpg"
        other_image.write_bytes(b"other-raw-source-image")
        other = add_source(self.root, str(other_image), title="Obcy RAW")
        other_result = import_analysis(
            self.root,
            {**self._payload(), "source_id": other["id"]},
            model="test-model",
        )
        with connect(self.root) as connection:
            other_artifact_id = connection.execute(
                "SELECT id FROM artifacts WHERE legacy_source_id=?",
                (other["id"],),
            ).fetchone()[0]
            before = connection.execute("SELECT COUNT(*) FROM reading_versions").fetchone()[0]
        payload = self._payload()
        payload["artifact_provenance"] = [
            {"artifact_id": other_artifact_id, "kind": "RAW", "path": "other-raw.jpg"}
        ]

        with self.assertRaisesRegex(ValueError, "należy do niezgodnego source_item"):
            import_analysis(self.root, payload, model="test-model")

        with connect(self.root) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM reading_versions").fetchone()[0], before)
        self.assertTrue(other_result["analysis_id"])

    def test_v2_import_rejects_segment_artifact_owned_by_unrelated_source_item(self):
        other_image = self.root / "other-segment.jpg"
        other_image.write_bytes(b"other-segment-source-image")
        other = add_source(self.root, str(other_image), title="Obcy segment")
        import_analysis(
            self.root,
            {**self._payload(), "source_id": other["id"]},
            model="test-model",
        )
        with connect(self.root) as connection:
            other_artifact_id = connection.execute(
                "SELECT id FROM artifacts WHERE legacy_source_id=?",
                (other["id"],),
            ).fetchone()[0]
            before = connection.execute("SELECT COUNT(*) FROM reading_versions").fetchone()[0]
        payload = self._payload()
        payload["layers"]["extract"][0]["artifact_id"] = other_artifact_id

        with self.assertRaisesRegex(ValueError, "należy do niezgodnego source_item"):
            import_analysis(self.root, payload, model="test-model")

        with connect(self.root) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM reading_versions").fetchone()[0], before)

    def test_schema_exposes_v2_layers_and_legacy_fields(self):
        schema_path = Path(__file__).resolve().parents[1] / "schemas" / "act-analysis.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        properties = schema["properties"]
        for name in (
            "layers",
            "source_item_id",
            "reading",
            "classifications",
            "informant",
            "events",
            "field_readings",
            "artifact_provenance",
            "independent_checks",
            "uncertainties",
            "transcription_original",
            "people",
        ):
            self.assertIn(name, properties)
        self.assertIn("reader_run_id", schema["$defs"]["reading"]["properties"])
        self.assertEqual(
            schema["$defs"]["readStatus"]["enum"],
            ["Certain", "Probable", "Ambiguous", "Illegible", "Damaged"],
        )


if __name__ == "__main__":
    unittest.main()
