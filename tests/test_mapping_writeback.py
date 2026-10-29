from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from genea.db import connect, initialize
from genea.mapping_writeback import (
    apply_mapping_writeback,
    mapping_writeback_preview,
    source_input_fingerprint,
)


class MappingWritebackTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        initialize(self.root)
        with connect(self.root) as connection:
            connection.execute(
                "INSERT INTO source_items(id,title,item_type) VALUES ('item-1','Akt testowy','record')"
            )
            connection.execute(
                "INSERT INTO places(id,preferred_name,place_type) VALUES ('place-1','Nery','village')"
            )
            connection.execute(
                """INSERT INTO place_studies(
                       id,place_id,title,scope,status,created_at,updated_at
                   ) VALUES ('study-1','place-1','Nery – karty gospodarstw',
                             'household_register','active','2026-08-31','2026-08-31')"""
            )
            connection.execute(
                """INSERT INTO citations(
                       id,source_item_id,citation_text,locator,created_at
                   ) VALUES ('citation-1','item-1','Karta gospodarstwa Nery','scan 1','2026-08-31')"""
            )
            connection.execute(
                """INSERT INTO artifacts(
                       id,source_item_id,artifact_type,path,sha256,immutable,created_at
                   ) VALUES ('raw-1','item-1','RAW','raw.jpg',?,1,'2026-08-31')""",
                ("a" * 64,),
            )
            connection.execute(
                """INSERT INTO readings(
                       id,source_item_id,artifact_id,reading_type,status,
                       model_or_reviewer,created_at,metadata_json
                   ) VALUES ('reading-1','item-1','raw-1','agent_review','needs-review',
                             'agent','2026-08-31','{}')"""
            )
            connection.execute(
                """INSERT INTO reading_versions(
                       id,reading_id,version_number,status,payload_json,created_at
                   ) VALUES ('version-1','reading-1',1,'needs-review','{}','2026-08-31')"""
            )
            connection.execute(
                """INSERT INTO person_occurrences(
                       id,source_item_id,reading_version_id,raw_name,role_text,
                       verification_status,evidence_json
                   ) VALUES ('occ-existing','item-1','version-1','Jan','ojciec',
                             'imported_unverified','{}')"""
            )
            connection.execute(
                """INSERT INTO field_readings(
                       id,reading_version_id,field_name,value_text,reading_certainty
                   ) VALUES ('field-1','version-1','father','Jan Kowalski','Certain')"""
            )
            connection.execute(
                """INSERT INTO field_readings(
                       id,reading_version_id,field_name,value_text,reading_certainty
                   ) VALUES ('field-2','version-1','witness','Piotr','Certain')"""
            )

    def tearDown(self):
        self.temporary.cleanup()

    def test_child_act_can_use_reading_and_artifact_from_parent_source(self):
        with connect(self.root) as connection:
            connection.execute(
                """INSERT INTO source_items(id,parent_id,title,item_type)
                   VALUES ('item-child','item-1','Akt 1','act')"""
            )
        fingerprint = source_input_fingerprint(self.root, "item-child")
        with connect(self.root) as connection:
            schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        payload = {
            "schemaVersion": 1,
            "sourceItemId": "item-child",
            "expectedDatabaseSchema": schema_version,
            "expectedReviewRevision": None,
            "inputFingerprint": fingerprint,
            "privacyReviewed": True,
            "actor": "curator-test",
            "rationale": "Akt dziedziczy reprodukcję i odczyt ze strony nadrzędnej.",
            "batch": {
                "id": "batch-child",
                "batchKey": "child-source-evidence",
                "inputFingerprint": fingerprint,
                "stoppingRule": "Jeden akt.",
                "createdBy": "curator-test",
            },
            "extractionState": {
                "status": "partial",
                "readingReviewDecisionId": None,
                "rationale": "Test zgodności źródła dowodu z potomnym aktem.",
            },
            "operations": {
                "transcriptSegments": [
                    {
                        "id": "segment-child",
                        "reading_version_id": "version-1",
                        "artifact_id": "raw-1",
                        "sequence_number": 1,
                        "language": "polski",
                        "page_label": "akt 1",
                        "region_json": "{}",
                        "text": "Jan.",
                    }
                ],
                "events": [
                    {
                        "id": "event-child",
                        "source_item_id": "item-child",
                        "event_type": "birth",
                        "date_text": "1837",
                        "date_from": None,
                        "date_to": None,
                        "place_id": None,
                        "place_text": None,
                        "verification_status": "imported_unverified",
                        "notes": None,
                    }
                ],
                "sourceAssertions": [
                    {
                        "id": "assertion-child",
                        "source_item_id": "item-child",
                        "reading_version_id": "version-1",
                        "transcript_segment_id": "segment-child",
                        "assertion_type": "participant",
                        "subject_ref": "occ-child",
                        "predicate": "appears",
                        "object_text": "Jan",
                        "information_quality": "Indeterminable",
                        "reading_certainty": "Probable",
                        "verification_status": "imported_unverified",
                        "metadata_json": "{}",
                    }
                ],
                "personOccurrences": [
                    {
                        "id": "occ-child",
                        "source_item_id": "item-child",
                        "reading_version_id": "version-1",
                        "transcript_segment_id": "segment-child",
                        "person_id": None,
                        "raw_name": "Jan",
                        "normalized_name": "Jan",
                        "search_name": "jan",
                        "role_text": "uczestnik",
                        "reading_confidence": None,
                        "verification_status": "imported_unverified",
                        "evidence_json": "{}",
                    }
                ],
            },
        }
        preview = mapping_writeback_preview(self.root, self._write(payload))
        self.assertEqual(preview["status"], "ready")
        self.assertEqual(preview["diff"]["summary"]["conflict"], 0)

    def _manifest(self) -> dict:
        fingerprint = source_input_fingerprint(self.root, "item-1")
        with connect(self.root) as connection:
            schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        return {
            "schemaVersion": 1,
            "sourceItemId": "item-1",
            "expectedDatabaseSchema": schema_version,
            "expectedReviewRevision": None,
            "inputFingerprint": fingerprint,
            "privacyReviewed": True,
            "actor": "curator-test",
            "rationale": "Pełny test transakcyjnego mapowania bez identity.",
            "riskLevel": "R2",
            "costClass": "B",
            "batch": {
                "id": "batch-1",
                "batchKey": "mapping-pilot",
                "inputFingerprint": fingerprint,
                "scope": {"sourceItemId": "item-1"},
                "stoppingRule": "Jeden dokument i drugi apply jako no-op.",
                "createdBy": "curator-test",
            },
            "extractionState": {
                "status": "all_named_people_extracted",
                "readingReviewDecisionId": None,
                "rationale": "Wszystkie nazwane osoby objęte manifestem.",
                "domainCompletion": {
                    "mode": "full_act",
                    "rationale": "Frozen lista nazwanych ról po review.",
                    "acceptedNamedPeople": [
                        {
                            "personKey": "father-jan",
                            "fieldPath": "father",
                            "fieldReadingId": "field-1",
                            "name": "Jan Kowalski",
                            "role": "ojciec",
                            "transcriptSegmentId": "segment-1",
                            "eventId": "event-1",
                            "occurrenceId": "occ-existing",
                            "participationId": "participation-2",
                            "sourceAssertionId": "assertion-2",
                        },
                        {
                            "personKey": "witness-piotr",
                            "fieldPath": "witness",
                            "fieldReadingId": "field-2",
                            "name": "Piotr",
                            "role": "świadek",
                            "transcriptSegmentId": "segment-1",
                            "eventId": "event-1",
                            "occurrenceId": "occ-new",
                            "participationId": "participation-1",
                            "sourceAssertionId": "assertion-1",
                        },
                    ],
                },
            },
            "operations": {
                "transcriptSegments": [
                    {
                        "id": "segment-1",
                        "reading_version_id": "version-1",
                        "artifact_id": "raw-1",
                        "sequence_number": 1,
                        "language": "polski",
                        "page_label": "akt 1",
                        "region_json": "{\"region\":\"act\"}",
                        "text": "Jan i Piotr.",
                    }
                ],
                "events": [
                    {
                        "id": "event-1",
                        "source_item_id": "item-1",
                        "event_type": "birth",
                        "date_text": "1837",
                        "date_from": "1837-01-01",
                        "date_to": None,
                        "place_id": None,
                        "place_text": "Testowo",
                        "verification_status": "imported_unverified",
                        "notes": None,
                    }
                ],
                "sourceAssertions": [
                    {
                        "id": "assertion-1",
                        "source_item_id": "item-1",
                        "reading_version_id": "version-1",
                        "transcript_segment_id": "segment-1",
                        "assertion_type": "participant",
                        "subject_ref": "occ-new",
                        "predicate": "appears-as-witness",
                        "object_text": "Piotr",
                        "information_quality": "Indeterminable",
                        "reading_certainty": "Probable",
                        "verification_status": "imported_unverified",
                        "metadata_json": "{}",
                    },
                    {
                        "id": "assertion-2",
                        "source_item_id": "item-1",
                        "reading_version_id": "version-1",
                        "transcript_segment_id": "segment-1",
                        "assertion_type": "participant",
                        "subject_ref": "occ-existing",
                        "predicate": "appears-as-father",
                        "object_text": "Jan Kowalski",
                        "information_quality": "Indeterminable",
                        "reading_certainty": "Probable",
                        "verification_status": "imported_unverified",
                        "metadata_json": "{}",
                    },
                ],
                "personOccurrences": [
                    {
                        "id": "occ-new",
                        "source_item_id": "item-1",
                        "reading_version_id": "version-1",
                        "transcript_segment_id": "segment-1",
                        "person_id": None,
                        "raw_name": "Piotr",
                        "normalized_name": "Piotr",
                        "search_name": "piotr",
                        "role_text": "świadek",
                        "reading_confidence": None,
                        "verification_status": "imported_unverified",
                        "evidence_json": "{\"segment\":\"segment-1\"}",
                    }
                ],
                "personOccurrencePatches": [
                    {
                        "id": "occ-existing",
                        "expected": {
                            "transcript_segment_id": None,
                            "raw_name": "Jan",
                            "normalized_name": None,
                        },
                        "set": {
                            "transcript_segment_id": "segment-1",
                            "raw_name": "Jan Kowalski",
                            "normalized_name": "Jan Kowalski",
                            "search_name": "jan kowalski",
                            "reading_confidence": 0.95,
                        },
                    }
                ],
                "fieldReadingPatches": [
                    {
                        "id": "field-1",
                        "expected": {"transcript_segment_id": None},
                        "set": {"transcript_segment_id": "segment-1"},
                    },
                    {
                        "id": "field-2",
                        "expected": {"transcript_segment_id": None},
                        "set": {"transcript_segment_id": "segment-1"},
                    },
                ],
                "occurrenceReadings": [
                    {
                        "id": "occ-reading-new",
                        "occurrence_id": "occ-new",
                        "reading_version_id": "version-1",
                        "role_text": "świadek",
                        "evidence_json": "{\"segment\":\"segment-1\"}",
                    },
                    {
                        "id": "occ-reading-existing",
                        "occurrence_id": "occ-existing",
                        "reading_version_id": "version-1",
                        "role_text": "ojciec",
                        "evidence_json": "{\"segment\":\"segment-1\"}",
                    },
                ],
                "participations": [
                    {
                        "id": "participation-1",
                        "event_id": "event-1",
                        "occurrence_id": "occ-new",
                        "role": "świadek",
                        "age_text": None,
                        "occupation_text": None,
                        "residence_text": "Testowo",
                        "status_text": "Probable",
                        "is_informant": 0,
                        "source_assertion_id": "assertion-1",
                    },
                    {
                        "id": "participation-2",
                        "event_id": "event-1",
                        "occurrence_id": "occ-existing",
                        "role": "ojciec",
                        "age_text": None,
                        "occupation_text": None,
                        "residence_text": "Testowo",
                        "status_text": "Probable",
                        "is_informant": 0,
                        "source_assertion_id": "assertion-2",
                    },
                ],
                "households": [
                    {
                        "id": "household-1",
                        "place_id": "place-1",
                        "label": "Nery, dom nr 3",
                        "date_from": "1854",
                        "date_to": "1854",
                        "verification_status": "imported_unverified",
                        "notes": "Gospodarstwo z jednej karty źródłowej.",
                    }
                ],
                "addresses": [
                    {
                        "id": "address-1",
                        "household_id": "household-1",
                        "place_id": "place-1",
                        "address_text": "Nery, dom nr 3",
                        "house_number": "3",
                        "date_from": "1854",
                        "date_to": "1854",
                        "source_item_id": "item-1",
                    }
                ],
                "householdMemberships": [
                    {
                        "id": "membership-1",
                        "household_id": "household-1",
                        "person_id": None,
                        "occurrence_id": "occ-new",
                        "role": "domownik",
                        "date_from": "1854",
                        "date_to": "1854",
                        "source_assertion_id": "assertion-1",
                    }
                ],
                "localOccurrences": [
                    {
                        "id": "local-1",
                        "study_id": "study-1",
                        "occurrence_id": "occ-new",
                        "household_id": "household-1",
                        "source_item_id": "item-1",
                        "event_date_text": "1854",
                        "event_type": "household_register",
                        "role": "domownik",
                        "surname_variant": None,
                        "given_names": "Piotr",
                        "search_name": "piotr",
                        "house_number": "3",
                        "citation_id": "citation-1",
                        "verification_status": "imported_unverified",
                        "metadata_json": "{\"segment\":\"segment-1\"}",
                    }
                ],
            },
        }

    def _prepare_quarantine_occurrence(self) -> None:
        with connect(self.root) as connection:
            connection.execute(
                """INSERT INTO persons(id,preferred_name,status,created_at,notes)
                   VALUES ('person-wrong','Błędna osoba','candidate','2026-08-31',NULL)"""
            )
            connection.execute(
                """INSERT INTO transcript_segments(
                       id,reading_version_id,artifact_id,sequence_number,language,
                       page_label,region_json,text
                   ) VALUES ('segment-existing','version-1','raw-1',1,'polski',
                             'akt testowy','{}','Jan.')"""
            )
            connection.execute(
                """UPDATE person_occurrences
                      SET reading_version_id='version-1',
                          transcript_segment_id='segment-existing',
                          person_id='person-wrong',
                          normalized_name='Jan',
                          search_name='jan',
                          reading_confidence=0.8,
                          evidence_json='{"segment":"segment-existing"}'
                    WHERE id='occ-existing'"""
            )
            connection.execute(
                """INSERT INTO person_occurrences(
                       id,source_item_id,reading_version_id,transcript_segment_id,
                       raw_name,normalized_name,search_name,role_text,
                       verification_status,evidence_json
                   ) VALUES ('occ-peer','item-1','version-1','segment-existing',
                             'Piotr','Piotr','piotr','świadek',
                             'imported_unverified','{}')"""
            )
            connection.execute(
                """INSERT INTO identity_candidates(
                       id,occurrence_a_id,occurrence_b_id,score,basis_json,status,created_at
                   ) VALUES ('candidate-1','occ-existing','occ-peer',0.2,'{}',
                             'unresolved','2026-08-31')"""
            )
            connection.execute(
                """INSERT INTO identity_decisions(
                       id,candidate_id,decision,decided_by,decided_at,rationale
                   ) VALUES ('decision-1','candidate-1','different','curator-test',
                             '2026-08-31','Niezależna decyzja testowa.')"""
            )

    def _quarantine_manifest(self) -> dict:
        payload = self._manifest()
        with connect(self.root) as connection:
            expected = dict(
                connection.execute(
                    "SELECT * FROM person_occurrences WHERE id='occ-existing'"
                ).fetchone()
            )
        payload["rationale"] = "Kwarantanna błędnego przypisania bez decyzji identity."
        payload["extractionState"] = {
            "status": "target_only",
            "readingReviewDecisionId": None,
            "rationale": "Kwarantanna jednego occurrence nie jest pełnym mapowaniem aktu.",
        }
        payload["operations"] = {
            "personOccurrenceQuarantines": [
                {
                    "id": "occ-existing",
                    "expected": expected,
                    "set": {"person_id": None},
                }
            ]
        }
        payload["impactAssessment"] = [
            {
                "category": "source_item",
                "objectType": "source_items",
                "objectId": "item-1",
                "impactType": "quarantine_scope",
                "plannedAction": "retain_source_record",
                "writebackStatus": "not_affected",
                "rationale": "Kwarantanna nie zmienia rekordu źródła.",
            },
            {
                "category": "occurrence_projection",
                "objectType": "person_occurrences",
                "objectId": "occ-existing",
                "impactType": "identity_assignment_quarantine",
                "plannedAction": "clear_person_and_correct_source_projection",
                "writebackStatus": "applied",
                "rationale": "Ta operacja atomowo koryguje occurrence.",
            },
            {
                "category": "prior_person",
                "objectType": "persons",
                "objectId": "person-wrong",
                "impactType": "dependency_review",
                "plannedAction": "review_person_projection",
                "writebackStatus": "deferred",
                "rationale": "Osoba pozostaje bez automatycznej decyzji identity.",
            },
            {
                "category": "identity_candidate",
                "objectType": "identity_candidates",
                "objectId": "candidate-1",
                "impactType": "dependency_review",
                "plannedAction": "review_candidate",
                "writebackStatus": "deferred",
                "rationale": "Kandydatura wymaga osobnego review.",
            },
            {
                "category": "identity_decision",
                "objectType": "identity_decisions",
                "objectId": "decision-1",
                "impactType": "dependency_review",
                "plannedAction": "review_decision",
                "writebackStatus": "deferred",
                "rationale": "Decyzja identity nie jest zmieniana przez kwarantannę.",
            },
            {
                "category": "question_next_action",
                "objectType": "question_next_action_scope",
                "objectId": "item-1",
                "impactType": "dependency_review",
                "plannedAction": "none_found",
                "writebackStatus": "not_affected",
                "rationale": "Brak powiązanego pytania lub Next Action w fixture.",
            },
            {
                "category": "ui",
                "objectType": "ui_projection_scope",
                "objectId": "item-1",
                "impactType": "projection_refresh",
                "plannedAction": "refresh_ui_projection",
                "writebackStatus": "deferred",
                "rationale": "UI wymaga odświeżenia po kontrolowanym writebacku.",
            },
            {
                "category": "controlled_export",
                "objectType": "controlled_export_scope",
                "objectId": "item-1",
                "impactType": "projection_refresh",
                "plannedAction": "refresh_controlled_exports",
                "writebackStatus": "deferred",
                "rationale": "Eksporty wymagają osobnego odświeżenia.",
            },
            {
                "category": "external_sync",
                "objectType": "external_sync_scope",
                "objectId": "item-1",
                "impactType": "dependency_review",
                "plannedAction": "none_found",
                "writebackStatus": "not_affected",
                "rationale": "Brak wpisu external_sync w fixture.",
            },
        ]
        return payload

    def _make_occurrence_provenance_stale(self) -> None:
        with connect(self.root) as connection:
            connection.execute(
                "INSERT INTO source_items(id,title,item_type) VALUES ('item-stale','Obce źródło','record')"
            )
            connection.execute(
                """INSERT INTO readings(
                       id,source_item_id,reading_type,status,model_or_reviewer,
                       created_at,metadata_json
                   ) VALUES ('reading-stale','item-stale','agent_review','needs-review',
                             'agent','2026-08-31','{}')"""
            )
            connection.execute(
                """INSERT INTO reading_versions(
                       id,reading_id,version_number,status,payload_json,created_at
                   ) VALUES ('version-stale','reading-stale',1,'needs-review','{}',
                             '2026-08-31')"""
            )
            connection.execute(
                """UPDATE person_occurrences
                      SET reading_version_id='version-stale',
                          transcript_segment_id=NULL,
                          raw_name='Jan błędnie',
                          normalized_name='Jan błędnie',
                          search_name='jan blednie',
                          role_text='rola błędna',
                          reading_confidence=0.1,
                          evidence_json='{"stale":true}'
                    WHERE id='occ-existing'"""
            )

    def _write(self, payload: dict, name: str = "manifest.json") -> Path:
        path = self.root / name
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def test_preview_apply_and_second_apply_are_safe_and_idempotent(self):
        manifest = self._write(self._manifest())
        with connect(self.root) as connection:
            audit_before = connection.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        preview = mapping_writeback_preview(self.root, manifest)
        self.assertEqual(preview["status"], "ready")
        self.assertEqual(preview["diff"]["summary"], {"create": 13, "update": 3, "no_op": 0, "conflict": 0})
        self.assertEqual(preview["diff"]["domainCompletion"]["state"], "complete")
        with connect(self.root) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0], audit_before)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM reassessment_batches").fetchone()[0], 0)

        applied = apply_mapping_writeback(self.root, manifest)
        self.assertEqual(applied["status"], "applied")
        with connect(self.root) as connection:
            occurrence = connection.execute(
                "SELECT transcript_segment_id,raw_name,normalized_name,search_name,reading_confidence "
                "FROM person_occurrences WHERE id='occ-existing'"
            ).fetchone()
            self.assertEqual(tuple(occurrence), ("segment-1", "Jan Kowalski", "Jan Kowalski", "jan kowalski", 0.95))
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM person_occurrences WHERE id='occ-new'").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM households WHERE id='household-1'").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM addresses WHERE id='address-1'").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM household_memberships WHERE id='membership-1'").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM local_occurrences WHERE id='local-1'").fetchone()[0], 1)
            household_audits = connection.execute(
                """SELECT object_type,action FROM audit_log
                   WHERE object_id IN ('household-1','address-1','membership-1','local-1')
                   ORDER BY object_type"""
            ).fetchall()
            self.assertEqual(
                [(row["object_type"], row["action"]) for row in household_audits],
                [
                    ("addresses", "mapping_writeback_create"),
                    ("household_memberships", "mapping_writeback_create"),
                    ("households", "mapping_writeback_create"),
                    ("local_occurrences", "mapping_writeback_create"),
                ],
            )
            self.assertEqual(
                connection.execute(
                    """SELECT COUNT(*) FROM reassessment_impacts
                       WHERE object_type IN (
                           'households','addresses','household_memberships','local_occurrences'
                       ) AND writeback_status='applied'"""
                ).fetchone()[0],
                4,
            )
            self.assertEqual(connection.execute("SELECT status FROM document_extraction_states").fetchone()[0], "all_named_people_extracted")
            self.assertEqual(connection.execute("SELECT status FROM reassessment_batch_states ORDER BY revision DESC LIMIT 1").fetchone()[0], "completed")
            counts = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("mapping_writeback_runs", "person_occurrences", "audit_log")
            }
        repeated = apply_mapping_writeback(self.root, manifest)
        self.assertEqual(repeated["status"], "no_op")
        with connect(self.root) as connection:
            self.assertEqual(
                counts,
                {
                    table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in counts
                },
            )

    def test_full_act_reports_one_missing_named_role_with_layers(self):
        payload = self._manifest()
        payload["operations"]["participations"] = [
            item
            for item in payload["operations"]["participations"]
            if item["id"] != "participation-1"
        ]

        preview = mapping_writeback_preview(self.root, self._write(payload))

        self.assertEqual(preview["status"], "conflict")
        report = preview["diff"]["domainCompletion"]
        self.assertEqual(report["state"], "incomplete")
        self.assertEqual(report["completeCount"], 1)
        self.assertEqual(report["gaps"][0]["personKey"], "witness-piotr")
        self.assertEqual(report["gaps"][0]["role"], "świadek")
        self.assertEqual(report["gaps"][0]["missingLayers"], ["participation"])
        self.assertIn(
            "missing_participation",
            {issue["code"] for issue in report["gaps"][0]["issues"]},
        )

    def test_geometry_only_batch_has_explicit_no_domain_writeback_state(self):
        payload = self._manifest()
        payload["batch"]["id"] = "batch-geometry"
        payload["batch"]["batchKey"] = "geometry-only"
        payload["operations"] = {}
        payload["extractionState"] = {
            "status": "target_only",
            "readingReviewDecisionId": None,
            "rationale": "Wyznaczono wyłącznie geometrię pól.",
            "domainCompletion": {
                "mode": "no_domain_writeback_expected",
                "scopeKind": "geometry_only",
                "rationale": "Ten batch nie tworzy obiektów domenowych.",
            },
        }

        preview = mapping_writeback_preview(self.root, self._write(payload))

        self.assertEqual(preview["status"], "ready")
        self.assertEqual(
            preview["diff"]["domainCompletion"]["state"],
            "no_domain_writeback_expected",
        )
        applied = apply_mapping_writeback(self.root, self._write(payload))
        self.assertEqual(applied["status"], "applied")
        self.assertEqual(
            applied["diff"]["domainCompletion"]["state"],
            "no_domain_writeback_expected",
        )

    def test_no_domain_writeback_exemption_rejects_domain_operations(self):
        payload = self._manifest()
        payload["batch"]["id"] = "batch-invalid-exemption"
        payload["batch"]["batchKey"] = "invalid-geometry-exemption"
        payload["operations"] = {
            "events": [payload["operations"]["events"][0]],
        }
        payload["extractionState"] = {
            "status": "target_only",
            "readingReviewDecisionId": None,
            "rationale": "Niepoprawna próba zwolnienia zapisu zdarzenia.",
            "domainCompletion": {
                "mode": "no_domain_writeback_expected",
                "scopeKind": "geometry_only",
                "rationale": "Deklarowany zakres nie odpowiada operacjom.",
            },
        }

        preview = mapping_writeback_preview(self.root, self._write(payload))

        self.assertEqual(preview["status"], "conflict")
        self.assertIn(
            "no_domain_writeback_expected_has_domain_operations",
            {item["reason"] for item in preview["diff"]["conflicts"]},
        )
        self.assertEqual(preview["diff"]["domainCompletion"]["state"], "incomplete")
        self.assertEqual(
            preview["diff"]["domainCompletion"]["gaps"][0]["operationGroups"],
            ["events"],
        )

    def test_person_occurrence_quarantine_is_audited_identity_safe_and_idempotent(self):
        self._prepare_quarantine_occurrence()
        manifest = self._write(self._quarantine_manifest())
        with connect(self.root) as connection:
            identity_before = {
                table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")]
                for table in ("identity_candidates", "identity_decisions")
            }

        preview = mapping_writeback_preview(self.root, manifest)
        self.assertEqual(preview["status"], "ready")
        self.assertEqual(
            preview["diff"]["summary"],
            {"create": 0, "update": 1, "no_op": 0, "conflict": 0},
        )

        applied = apply_mapping_writeback(self.root, manifest)
        self.assertEqual(applied["status"], "applied")
        with connect(self.root) as connection:
            self.assertIsNone(
                connection.execute(
                    "SELECT person_id FROM person_occurrences WHERE id='occ-existing'"
                ).fetchone()[0]
            )
            audit = connection.execute(
                """SELECT action,before_json,after_json FROM audit_log
                   WHERE object_type='person_occurrences' AND object_id='occ-existing'"""
            ).fetchone()
            self.assertEqual(
                audit["action"], "mapping_writeback_person_occurrence_quarantine"
            )
            self.assertEqual(json.loads(audit["before_json"])["person_id"], "person-wrong")
            self.assertIsNone(json.loads(audit["after_json"])["person_id"])
            impact = connection.execute(
                """SELECT writeback_status,preview_json FROM reassessment_impacts
                   WHERE object_type='person_occurrences' AND object_id='occ-existing'
                     AND impact_type='mapping'"""
            ).fetchone()
            self.assertEqual(impact["writeback_status"], "applied")
            self.assertEqual(
                json.loads(impact["preview_json"])["group"],
                "personOccurrenceQuarantines",
            )
            declared_impacts = connection.execute(
                """SELECT writeback_status,preview_json FROM reassessment_impacts
                   WHERE impact_type<>'mapping' ORDER BY id"""
            ).fetchall()
            self.assertEqual(len(declared_impacts), 9)
            declared_categories = {
                json.loads(row["preview_json"])["category"]
                for row in declared_impacts
            }
            self.assertEqual(
                declared_categories,
                {
                    "source_item",
                    "occurrence_projection",
                    "prior_person",
                    "identity_candidate",
                    "identity_decision",
                    "question_next_action",
                    "ui",
                    "controlled_export",
                    "external_sync",
                },
            )
            self.assertEqual(
                {
                    json.loads(row["preview_json"])["category"]
                    for row in declared_impacts
                    if row["writeback_status"] == "applied"
                },
                {"occurrence_projection"},
            )
            self.assertEqual(
                identity_before,
                {
                    table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")]
                    for table in identity_before
                },
            )
            counts = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "audit_log",
                    "mapping_writeback_manifests",
                    "mapping_writeback_runs",
                    "reassessment_impacts",
                )
            }

        repeated = apply_mapping_writeback(self.root, manifest)
        self.assertEqual(repeated["status"], "no_op")
        repeated_preview = mapping_writeback_preview(self.root, manifest)
        self.assertEqual(repeated_preview["status"], "ready")
        self.assertEqual(repeated_preview["diff"]["summary"]["no_op"], 1)
        with connect(self.root) as connection:
            self.assertEqual(
                counts,
                {
                    table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in counts
                },
            )

        stale_replay = self._quarantine_manifest()
        stale_replay["batch"]["id"] = "batch-stale-replay"
        stale_replay["batch"]["batchKey"] = "mapping-stale-replay"
        stale_replay["rationale"] = "Inny manifest nie jest idempotentnym replayem."
        stale_replay["operations"]["personOccurrenceQuarantines"][0]["expected"][
            "person_id"
        ] = "person-other"
        stale_replay["impactAssessment"][2]["objectId"] = "person-other"
        preview = mapping_writeback_preview(self.root, self._write(stale_replay))
        self.assertEqual(preview["status"], "conflict")
        self.assertIn(
            "optimistic_concurrency",
            {item["reason"] for item in preview["diff"]["conflicts"]},
        )

    def test_person_occurrence_quarantine_atomically_corrects_stale_provenance(self):
        self._prepare_quarantine_occurrence()
        self._make_occurrence_provenance_stale()
        payload = self._quarantine_manifest()
        target = {
            "person_id": None,
            "reading_version_id": "version-1",
            "transcript_segment_id": "segment-existing",
            "raw_name": "Jan",
            "normalized_name": "Jan",
            "search_name": "jan",
            "role_text": "ojciec",
            "reading_confidence": 0.8,
            "verification_status": "imported_unverified",
            "evidence_json": '{"segment":"segment-existing"}',
        }
        payload["operations"]["personOccurrenceQuarantines"][0]["set"] = target
        with connect(self.root) as connection:
            identity_before = {
                table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")]
                for table in ("identity_candidates", "identity_decisions")
            }

        preview = mapping_writeback_preview(self.root, self._write(payload))
        self.assertEqual(preview["status"], "ready")
        applied = apply_mapping_writeback(self.root, self._write(payload))
        self.assertEqual(applied["status"], "applied")
        with connect(self.root) as connection:
            current = dict(
                connection.execute(
                    "SELECT * FROM person_occurrences WHERE id='occ-existing'"
                ).fetchone()
            )
            self.assertEqual({field: current[field] for field in target}, target)
            self.assertEqual(
                identity_before,
                {
                    table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")]
                    for table in identity_before
                },
            )

    def test_person_occurrence_quarantine_exact_replay_rejects_reattachment(self):
        self._prepare_quarantine_occurrence()
        manifest = self._write(self._quarantine_manifest())
        self.assertEqual(apply_mapping_writeback(self.root, manifest)["status"], "applied")
        with connect(self.root) as connection:
            connection.execute(
                "UPDATE person_occurrences SET person_id='person-wrong' WHERE id='occ-existing'"
            )
        preview = mapping_writeback_preview(self.root, manifest)
        self.assertEqual(preview["status"], "conflict")
        self.assertIn(
            "stale_applied_quarantine_state",
            {item["reason"] for item in preview["diff"]["conflicts"]},
        )
        with self.assertRaisesRegex(RuntimeError, "drift stanu"):
            apply_mapping_writeback(self.root, manifest)

    def test_person_occurrence_quarantine_exact_replay_rejects_source_field_drift(self):
        self._prepare_quarantine_occurrence()
        self._make_occurrence_provenance_stale()
        payload = self._quarantine_manifest()
        payload["operations"]["personOccurrenceQuarantines"][0]["set"] = {
            "person_id": None,
            "reading_version_id": "version-1",
            "transcript_segment_id": "segment-existing",
            "raw_name": "Jan",
            "normalized_name": "Jan",
            "search_name": "jan",
            "role_text": "ojciec",
            "reading_confidence": 0.8,
            "verification_status": "imported_unverified",
            "evidence_json": '{"segment":"segment-existing"}',
        }
        manifest = self._write(payload)
        self.assertEqual(apply_mapping_writeback(self.root, manifest)["status"], "applied")
        with connect(self.root) as connection:
            connection.execute(
                "UPDATE person_occurrences SET raw_name='Późniejszy drift' WHERE id='occ-existing'"
            )
        preview = mapping_writeback_preview(self.root, manifest)
        self.assertEqual(preview["status"], "conflict")
        self.assertIn(
            "stale_applied_quarantine_state",
            {item["reason"] for item in preview["diff"]["conflicts"]},
        )
        with self.assertRaisesRegex(RuntimeError, "drift stanu"):
            apply_mapping_writeback(self.root, manifest)

    def test_person_occurrence_quarantine_invalid_corrected_provenance_blocks(self):
        self._prepare_quarantine_occurrence()
        self._make_occurrence_provenance_stale()
        payload = self._quarantine_manifest()
        payload["operations"]["personOccurrenceQuarantines"][0]["set"] = {
            "person_id": None,
            "reading_version_id": "version-stale",
            "transcript_segment_id": "segment-existing",
        }
        preview = mapping_writeback_preview(self.root, self._write(payload))
        self.assertEqual(preview["status"], "conflict")
        self.assertIn(
            "occurrence_provenance_mismatch",
            {item["reason"] for item in preview["diff"]["conflicts"]},
        )

    def test_person_occurrence_quarantine_requires_complete_terminal_impacts(self):
        self._prepare_quarantine_occurrence()
        payload = self._quarantine_manifest()
        payload["impactAssessment"] = [
            impact for impact in payload["impactAssessment"] if impact["category"] != "ui"
        ]
        with self.assertRaisesRegex(ValueError, "impactAssessment.*ui"):
            mapping_writeback_preview(self.root, self._write(payload))

        payload = self._quarantine_manifest()
        payload["impactAssessment"][0]["writebackStatus"] = "planned"
        with self.assertRaisesRegex(ValueError, "terminalny"):
            mapping_writeback_preview(self.root, self._write(payload))

        payload = self._quarantine_manifest()
        payload["impactAssessment"][2]["writebackStatus"] = "applied"
        with self.assertRaisesRegex(ValueError, "wyłącznie.*occurrence_projection"):
            mapping_writeback_preview(self.root, self._write(payload))

        payload = self._quarantine_manifest()
        payload["impactAssessment"][3]["objectId"] = "candidate-other"
        preview = mapping_writeback_preview(self.root, self._write(payload))
        self.assertEqual(preview["status"], "conflict")
        self.assertIn(
            "missing_declared_impact",
            {item["reason"] for item in preview["diff"]["conflicts"]},
        )

    def test_person_occurrence_quarantine_stale_expected_blocks_preview(self):
        self._prepare_quarantine_occurrence()
        payload = self._quarantine_manifest()
        payload["operations"]["personOccurrenceQuarantines"][0]["expected"][
            "raw_name"
        ] = "Nieaktualny odczyt"
        preview = mapping_writeback_preview(self.root, self._write(payload))
        self.assertEqual(preview["status"], "conflict")
        self.assertIn(
            "optimistic_concurrency",
            {item["reason"] for item in preview["diff"]["conflicts"]},
        )

    def test_person_occurrence_quarantine_source_scope_blocks_preview(self):
        self._prepare_quarantine_occurrence()
        with connect(self.root) as connection:
            connection.execute(
                "INSERT INTO source_items(id,title,item_type) VALUES ('item-2','Obcy akt','record')"
            )
            connection.execute(
                "UPDATE person_occurrences SET source_item_id='item-2' WHERE id='occ-existing'"
            )
        preview = mapping_writeback_preview(
            self.root, self._write(self._quarantine_manifest())
        )
        self.assertEqual(preview["status"], "conflict")
        self.assertIn(
            "source_scope_mismatch",
            {item["reason"] for item in preview["diff"]["conflicts"]},
        )

    def test_person_occurrence_quarantine_requires_valid_target_provenance_and_null_target(self):
        self._prepare_quarantine_occurrence()
        payload = self._quarantine_manifest()
        payload["operations"]["personOccurrenceQuarantines"][0]["set"] = {
            "person_id": "person-wrong"
        }
        with self.assertRaisesRegex(ValueError, "person_id.*null"):
            mapping_writeback_preview(self.root, self._write(payload))

        payload = self._quarantine_manifest()
        payload["operations"]["personOccurrenceQuarantines"][0]["set"] = {
            "person_id": None,
            "source_item_id": "item-1",
        }
        with self.assertRaisesRegex(ValueError, "source_item_id"):
            mapping_writeback_preview(self.root, self._write(payload))

        with connect(self.root) as connection:
            connection.execute(
                "UPDATE person_occurrences SET transcript_segment_id=NULL WHERE id='occ-existing'"
            )
        payload = self._quarantine_manifest()
        preview = mapping_writeback_preview(self.root, self._write(payload))
        self.assertEqual(preview["status"], "conflict")
        self.assertIn(
            "occurrence_provenance_required",
            {item["reason"] for item in preview["diff"]["conflicts"]},
        )

    def test_person_occurrence_quarantine_protected_person_requires_permission(self):
        self._prepare_quarantine_occurrence()
        with connect(self.root) as connection:
            connection.execute(
                """INSERT INTO privacy_labels(
                       id,object_type,object_id,classification,rationale,created_at,updated_at
                   ) VALUES ('privacy-person','person','person-wrong','living',
                             'test living person','now','now')"""
            )
        payload = self._quarantine_manifest()
        manifest = self._write(payload)
        with self.assertRaisesRegex(RuntimeError, "allowRestricted"):
            mapping_writeback_preview(self.root, manifest)
        payload["allowRestricted"] = True
        preview = mapping_writeback_preview(self.root, self._write(payload))
        self.assertEqual(preview["status"], "ready")

    def test_person_occurrence_quarantine_protected_occurrence_requires_permission(self):
        self._prepare_quarantine_occurrence()
        with connect(self.root) as connection:
            connection.execute(
                """INSERT INTO privacy_labels(
                       id,object_type,object_id,classification,rationale,created_at,updated_at
                   ) VALUES ('privacy-occurrence','person_occurrence','occ-existing',
                             'restricted','test restricted occurrence','now','now')"""
            )
        payload = self._quarantine_manifest()
        with self.assertRaisesRegex(RuntimeError, "allowRestricted"):
            mapping_writeback_preview(self.root, self._write(payload))
        payload["allowRestricted"] = True
        preview = mapping_writeback_preview(self.root, self._write(payload))
        self.assertEqual(preview["status"], "ready")

    def test_person_occurrence_quarantine_failure_rolls_back_everything(self):
        self._prepare_quarantine_occurrence()
        self._make_occurrence_provenance_stale()
        payload = self._quarantine_manifest()
        payload["operations"]["personOccurrenceQuarantines"][0]["set"] = {
            "person_id": None,
            "reading_version_id": "version-1",
            "transcript_segment_id": "segment-existing",
            "raw_name": "Jan",
            "normalized_name": "Jan",
            "search_name": "jan",
            "role_text": "ojciec",
            "reading_confidence": 0.8,
            "verification_status": "imported_unverified",
            "evidence_json": '{"segment":"segment-existing"}',
        }
        manifest = self._write(payload)
        from genea import mapping_writeback as module

        original = module._audit

        def fail_on_quarantine(connection, actor, action, *args):
            if action == "mapping_writeback_person_occurrence_quarantine":
                raise RuntimeError("synthetic quarantine failure")
            return original(connection, actor, action, *args)

        with patch("genea.mapping_writeback._audit", side_effect=fail_on_quarantine):
            with self.assertRaisesRegex(RuntimeError, "synthetic quarantine failure"):
                apply_mapping_writeback(self.root, manifest)
        with connect(self.root) as connection:
            occurrence = connection.execute(
                """SELECT person_id,reading_version_id,transcript_segment_id,raw_name,
                          normalized_name,search_name,role_text,reading_confidence,evidence_json
                     FROM person_occurrences WHERE id='occ-existing'"""
            ).fetchone()
            self.assertEqual(
                tuple(occurrence),
                (
                    "person-wrong",
                    "version-stale",
                    None,
                    "Jan błędnie",
                    "Jan błędnie",
                    "jan blednie",
                    "rola błędna",
                    0.1,
                    '{"stale":true}',
                ),
            )
            for table in (
                "reassessment_batches",
                "mapping_writeback_manifests",
                "mapping_writeback_runs",
                "reassessment_impacts",
                "audit_log",
            ):
                self.assertEqual(
                    connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0
                )

    def test_optimistic_conflict_and_missing_provenance_block_preview(self):
        payload = self._manifest()
        payload["operations"]["personOccurrencePatches"][0]["expected"] = {
            "transcript_segment_id": "unexpected"
        }
        payload["operations"]["personOccurrences"][0]["transcript_segment_id"] = None
        preview = mapping_writeback_preview(self.root, self._write(payload))
        self.assertEqual(preview["status"], "conflict")
        reasons = {item["reason"] for item in preview["diff"]["conflicts"]}
        self.assertIn("optimistic_concurrency", reasons)
        self.assertIn("occurrence_provenance_required", reasons)

    def test_restricted_source_requires_explicit_permission(self):
        with connect(self.root) as connection:
            connection.execute(
                """INSERT INTO privacy_labels(
                       id,object_type,object_id,classification,rationale,created_at,updated_at
                   ) VALUES ('privacy-1','source_item','item-1','restricted','test','now','now')"""
            )
        manifest = self._write(self._manifest())
        with self.assertRaisesRegex(RuntimeError, "allowRestricted"):
            mapping_writeback_preview(self.root, manifest)

    def test_household_provenance_conflicts_block_preview(self):
        with connect(self.root) as connection:
            connection.execute(
                "INSERT INTO source_items(id,title,item_type) VALUES ('item-2','Inna karta','record')"
            )
            connection.execute(
                """INSERT INTO person_occurrences(
                       id,source_item_id,raw_name,verification_status,evidence_json
                   ) VALUES ('occ-other','item-2','Obca osoba','imported_unverified','{}')"""
            )
            connection.execute(
                """INSERT INTO citations(id,source_item_id,citation_text,created_at)
                   VALUES ('citation-other','item-2','Inna karta','2026-08-31')"""
            )
        payload = self._manifest()
        payload["operations"]["addresses"][0]["source_item_id"] = "item-2"
        payload["operations"]["householdMemberships"][0]["occurrence_id"] = "occ-other"
        payload["operations"]["householdMemberships"][0]["source_assertion_id"] = None
        payload["operations"]["localOccurrences"][0]["citation_id"] = "citation-other"
        preview = mapping_writeback_preview(self.root, self._write(payload))
        self.assertEqual(preview["status"], "conflict")
        reasons = {item["reason"] for item in preview["diff"]["conflicts"]}
        self.assertIn("address_provenance_mismatch", reasons)
        self.assertIn("membership_occurrence_source_mismatch", reasons)
        self.assertIn("citation_source_mismatch", reasons)

    def test_review_revision_drift_blocks_preview(self):
        payload = self._manifest()
        manifest = self._write(payload)
        with connect(self.root) as connection:
            connection.execute(
                """INSERT INTO reading_review_decisions(
                       id,source_item_id,revision,status,decided_by,decided_by_kind,
                       rationale,created_at
                   ) VALUES ('review-1','item-1',1,'agent_single_pass','agent','agent',
                             'new review','2026-08-31')"""
            )
        with self.assertRaisesRegex(RuntimeError, "rewizji review"):
            mapping_writeback_preview(self.root, manifest)

    def test_existing_event_can_be_curated_without_creating_a_duplicate(self):
        with connect(self.root) as connection:
            connection.execute(
                """INSERT INTO events(
                       id,source_item_id,event_type,date_text,date_from,place_text,
                       verification_status,notes
                   ) VALUES ('event-existing','item-1','birth','rok niepewny',NULL,
                             'Testowo','imported_unverified','{}')"""
            )
        payload = self._manifest()
        payload["operations"]["eventPatches"] = [
            {
                "id": "event-existing",
                "expected": {"date_text": "rok niepewny", "date_from": None},
                "set": {
                    "date_text": "1/13 stycznia 1889",
                    "date_from": "1889-01-13",
                    "notes": '{"review":"agent_adjudicated"}',
                },
            }
        ]
        manifest = self._write(payload)
        preview = mapping_writeback_preview(self.root, manifest)
        self.assertEqual(preview["status"], "ready")
        self.assertEqual(preview["diff"]["summary"]["update"], 4)
        apply_mapping_writeback(self.root, manifest)
        with connect(self.root) as connection:
            event = connection.execute(
                "SELECT date_text,date_from,notes FROM events WHERE id='event-existing'"
            ).fetchone()
        self.assertEqual(
            tuple(event),
            ("1/13 stycznia 1889", "1889-01-13", '{"review":"agent_adjudicated"}'),
        )

    def test_mid_transaction_failure_rolls_back_everything(self):
        manifest = self._write(self._manifest())
        from genea import mapping_writeback as module

        original = module._insert

        def fail_on_occurrence(connection, table, target):
            if table == "person_occurrences":
                raise RuntimeError("synthetic failure")
            return original(connection, table, target)

        with patch("genea.mapping_writeback._insert", side_effect=fail_on_occurrence):
            with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
                apply_mapping_writeback(self.root, manifest)
        with connect(self.root) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM reassessment_batches").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM transcript_segments").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM mapping_writeback_manifests").fetchone()[0], 0)

    def test_household_failure_rolls_back_all_household_objects(self):
        manifest = self._write(self._manifest())
        from genea import mapping_writeback as module

        original = module._insert

        def fail_on_local_occurrence(connection, table, target):
            if table == "local_occurrences":
                raise RuntimeError("synthetic household failure")
            return original(connection, table, target)

        with patch("genea.mapping_writeback._insert", side_effect=fail_on_local_occurrence):
            with self.assertRaisesRegex(RuntimeError, "synthetic household failure"):
                apply_mapping_writeback(self.root, manifest)
        with connect(self.root) as connection:
            for table in (
                "households",
                "addresses",
                "household_memberships",
                "local_occurrences",
            ):
                self.assertEqual(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM mapping_writeback_manifests").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
