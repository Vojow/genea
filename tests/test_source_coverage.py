from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from genea.db import connect, initialize
from genea.source_coverage import (
    apply_source_structure,
    source_coverage_report,
    source_structure_preview,
)


class SourceCoverageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        initialize(self.root)
        with connect(self.root) as connection:
            connection.execute(
                """INSERT INTO source_items(id,title,item_type,metadata_json)
                   VALUES ('register-1','Księga 1819','document','{}')"""
            )
            connection.execute(
                """INSERT INTO events(id,source_item_id,event_type,date_text)
                   VALUES ('event-1','register-1','marriage','1819-01-16')"""
            )
            connection.execute(
                """INSERT INTO person_occurrences(
                       id,source_item_id,raw_name,verification_status,evidence_json
                   ) VALUES ('occurrence-1','register-1','Jan Testowy',
                             'imported_unverified','{}')"""
            )

    def tearDown(self):
        self.temporary.cleanup()

    def _manifest(self) -> Path:
        payload = {
            "schemaVersion": 1,
            "actor": "test-curator",
            "rationale": "Rozdzielenie księgi od aktu bez decyzji identity.",
            "items": [
                {
                    "id": "register-1",
                    "mode": "update",
                    "expected": {"itemType": "document", "parentId": None},
                    "itemType": "register",
                    "metadataPatch": {"displayKind": "Księga"},
                },
                {
                    "id": "act-1",
                    "mode": "create",
                    "parentId": "register-1",
                    "title": "Akt małżeństwa M1/1819",
                    "itemType": "act",
                    "dateText": "1819",
                    "metadataPatch": {"actNumber": 1, "recordType": "marriage"},
                },
            ],
            "moves": [
                {
                    "objectType": "event",
                    "id": "event-1",
                    "expectedSourceItemId": "register-1",
                    "targetSourceItemId": "act-1",
                },
                {
                    "objectType": "occurrence",
                    "id": "occurrence-1",
                    "expectedSourceItemId": "register-1",
                    "targetSourceItemId": "act-1",
                },
            ],
            "extractionStates": [
                {
                    "sourceItemId": "register-1",
                    "status": "partial",
                    "rationale": "Opracowano jeden z osiemnastu aktów.",
                },
                {
                    "sourceItemId": "act-1",
                    "status": "partial",
                    "rationale": "Struktura aktu nie jest jeszcze pełnym writebackiem domenowym.",
                },
            ],
            "coverage": [
                {
                    "sourceItemId": "register-1",
                    "coverageKey": "marriages:extraction:fan",
                    "dimension": "extraction",
                    "coverageLevel": "fan",
                    "recordType": "marriage",
                    "scopeText": "Akty małżeństw M1-M18",
                    "unitKind": "act",
                    "completedUnits": 1,
                    "totalUnits": 18,
                    "status": "partial",
                    "systematic": True,
                    "qaStatus": "agent_reviewed",
                    "basisType": "source_children",
                    "basisRef": "act-1",
                    "rationale": "Jeden akt ma pełną ekstrakcję FAN.",
                },
                {
                    "sourceItemId": "register-1",
                    "coverageKey": "marriages:transcription:full_literal",
                    "dimension": "transcription",
                    "coverageLevel": "full_literal",
                    "recordType": "marriage",
                    "scopeText": "Akty małżeństw M1-M18",
                    "unitKind": "act",
                    "completedUnits": 0,
                    "totalUnits": 18,
                    "status": "not_started",
                    "systematic": False,
                    "qaStatus": "unreviewed",
                    "basisType": "curator_assessment",
                    "rationale": "Ekstrakt nie jest pełną transkrypcją literalną.",
                },
            ],
        }
        path = self.root / "manifest.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_preview_apply_and_report_keep_book_and_act_coverage_separate(self):
        manifest = self._manifest()
        preview = source_structure_preview(self.root, manifest)
        self.assertEqual(preview["status"], "ready")
        self.assertEqual(preview["summary"]["conflict"], 0)

        applied = apply_source_structure(self.root, manifest)
        self.assertEqual(applied["status"], "applied")
        replay = apply_source_structure(self.root, manifest)
        self.assertEqual(replay["status"], "no_op")

        with connect(self.root) as connection:
            register = connection.execute(
                "SELECT item_type,metadata_json FROM source_items WHERE id='register-1'"
            ).fetchone()
            self.assertEqual(register["item_type"], "register")
            self.assertEqual(json.loads(register["metadata_json"])["displayKind"], "Księga")
            self.assertEqual(
                connection.execute("SELECT parent_id FROM source_items WHERE id='act-1'").fetchone()[0],
                "register-1",
            )
            self.assertEqual(
                connection.execute("SELECT source_item_id FROM events WHERE id='event-1'").fetchone()[0],
                "act-1",
            )
            self.assertEqual(
                connection.execute("SELECT source_item_id FROM person_occurrences WHERE id='occurrence-1'").fetchone()[0],
                "act-1",
            )
            self.assertEqual(
                connection.execute(
                    "SELECT status FROM document_extraction_states WHERE source_item_id='register-1' ORDER BY revision DESC LIMIT 1"
                ).fetchone()[0],
                "partial",
            )

        report = source_coverage_report(self.root, "register-1")
        self.assertEqual(report["summary"]["registers"], 1)
        self.assertEqual(report["summary"]["acts"], 1)
        register = next(item for item in report["items"] if item["id"] == "register-1")
        self.assertEqual(register["act_count"], 1)
        self.assertEqual(len(register["coverage"]), 2)
        full = next(row for row in register["coverage"] if row["coverage_level"] == "full_literal")
        self.assertEqual(full["completed_units"], 0)
        self.assertEqual(full["total_units"], 18)

    def test_report_includes_acts_nested_below_register_pages(self):
        with connect(self.root) as connection:
            connection.execute(
                """INSERT INTO source_items(id,parent_id,title,item_type)
                   VALUES ('page-1','register-1','Strona 1','register_page')"""
            )
            connection.execute(
                """INSERT INTO source_items(id,parent_id,title,item_type)
                   VALUES ('nested-act','page-1','Akt 1','act')"""
            )

        report = source_coverage_report(self.root, "register-1")
        self.assertEqual(
            {item["id"] for item in report["items"]},
            {"register-1", "page-1", "nested-act"},
        )
        self.assertEqual(report["summary"]["acts"], 1)

    def test_preview_reports_reading_review_source_mismatch_before_apply(self):
        with connect(self.root) as connection:
            connection.execute(
                """INSERT INTO reading_review_decisions(
                       id,source_item_id,revision,status,decided_by,decided_by_kind,
                       rationale,created_at
                   ) VALUES ('review-register','register-1',1,'agent_adjudicated',
                             'agent-a','agent','Batch review','2026-09-11')"""
            )
        payload = json.loads(self._manifest().read_text(encoding="utf-8"))
        payload["extractionStates"][1]["readingReviewDecisionId"] = "review-register"
        manifest = self.root / "mismatch.json"
        manifest.write_text(json.dumps(payload), encoding="utf-8")

        preview = source_structure_preview(self.root, manifest)

        self.assertEqual(preview["status"], "conflict")
        self.assertEqual(preview["summary"]["conflict"], 1)
        conflict = preview["conflicts"][0]
        self.assertEqual(conflict["reason"], "reading_review_source_mismatch")
        self.assertEqual(conflict["reviewSourceItemId"], "register-1")
        self.assertEqual(conflict["id"], "act-1")

    def test_preview_reports_missing_reading_review_decision(self):
        payload = json.loads(self._manifest().read_text(encoding="utf-8"))
        payload["extractionStates"][0]["readingReviewDecisionId"] = "missing-review"
        manifest = self.root / "missing-review.json"
        manifest.write_text(json.dumps(payload), encoding="utf-8")

        preview = source_structure_preview(self.root, manifest)

        self.assertEqual(preview["status"], "conflict")
        self.assertEqual(preview["conflicts"][0]["reason"], "missing_reading_review_decision")

    def test_matching_reading_review_decision_is_accepted(self):
        with connect(self.root) as connection:
            connection.execute(
                """INSERT INTO reading_review_decisions(
                       id,source_item_id,revision,status,decided_by,decided_by_kind,
                       rationale,created_at
                   ) VALUES ('review-register','register-1',1,'agent_adjudicated',
                             'agent-a','agent','Batch review','2026-09-11')"""
            )
        payload = json.loads(self._manifest().read_text(encoding="utf-8"))
        payload["extractionStates"][0]["readingReviewDecisionId"] = "review-register"
        manifest = self.root / "matching-review.json"
        manifest.write_text(json.dumps(payload), encoding="utf-8")

        preview = source_structure_preview(self.root, manifest)

        self.assertEqual(preview["status"], "ready")
        self.assertEqual(preview["summary"]["conflict"], 0)

    def test_all_named_people_status_without_domain_gate_is_rejected(self):
        payload = json.loads(self._manifest().read_text(encoding="utf-8"))
        payload["extractionStates"][1]["status"] = "all_named_people_extracted"
        manifest = self.root / "text-only-completion.json"
        manifest.write_text(json.dumps(payload), encoding="utf-8")

        preview = source_structure_preview(self.root, manifest)

        self.assertEqual(preview["status"], "conflict")
        self.assertIn(
            "domain_completion_declaration_required",
            {item["reason"] for item in preview["conflicts"]},
        )

    def test_geometry_only_exemption_has_explicit_domain_state(self):
        payload = json.loads(self._manifest().read_text(encoding="utf-8"))
        payload["extractionStates"][1].update(
            {
                "status": "target_only",
                "domainCompletion": {
                    "mode": "no_domain_writeback_expected",
                    "scopeKind": "geometry_only",
                    "rationale": "Wyznaczono tylko region aktu.",
                },
            }
        )
        manifest = self.root / "geometry-only.json"
        manifest.write_text(json.dumps(payload), encoding="utf-8")

        preview = source_structure_preview(self.root, manifest)

        self.assertEqual(preview["status"], "ready")
        act_report = next(
            item
            for item in preview["domainCompletion"]
            if item["sourceItemId"] == "act-1"
        )
        self.assertEqual(act_report["state"], "no_domain_writeback_expected")


if __name__ == "__main__":
    unittest.main()
