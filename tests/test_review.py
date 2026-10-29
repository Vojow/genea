from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from genea.db import connect, initialize
from genea.review import (
    get_reading_review_decision,
    label_privacy,
    list_reading_review_decisions,
    record_reading_comparison,
    record_reading_review_decision,
    review_claim_status,
)
from genea.workspace import utc_now


class ReviewWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        initialize(self.root)
        now = utc_now()
        with connect(self.root) as connection:
            connection.execute(
                "INSERT INTO research_cases(id,title,status,created_at,updated_at) "
                "VALUES ('CASE-1','Test','open',?,?)",
                (now, now),
            )
            connection.execute(
                """INSERT INTO source_items(
                       id,title,item_type,source_quality,verification_status
                   ) VALUES ('item-1','Akt','record','Original','imported_unverified')"""
            )
            for reading_id, reading_type, version_id in (
                ("reading-blind", "blind", "version-blind"),
                ("reading-context", "contextual", "version-context"),
                ("reading-review", "human_review", "version-review"),
            ):
                connection.execute(
                    """INSERT INTO readings(
                           id,source_item_id,reading_type,status,created_at
                       ) VALUES (?, 'item-1', ?, 'needs-review', ?)""",
                    (reading_id, reading_type, now),
                )
                connection.execute(
                    """INSERT INTO reading_versions(
                           id,reading_id,version_number,status,created_at
                       ) VALUES (?, ?, 1, 'needs-review', ?)""",
                    (version_id, reading_id, now),
                )
            connection.execute(
                """INSERT INTO claims(
                       id,case_id,statement,predicate,status,created_at,updated_at
                   ) VALUES ('claim-1','CASE-1','Twierdzenie','identity',
                             'imported_unverified',?,?)""",
                (now, now),
            )

    def tearDown(self):
        self.temporary.cleanup()

    def _add_reading(
        self,
        suffix: str,
        reading_type: str,
        *,
        source_item_id: str = "item-1",
        reader: str,
        run_id: str,
        context_disclosed: bool,
    ) -> str:
        now = utc_now()
        reading_id = f"reading-{suffix}"
        version_id = f"version-{suffix}"
        reading_payload = {
            "reading": {
                "reader": reader,
                "reader_run_id": run_id,
                "context_disclosed": context_disclosed,
            }
        }
        with connect(self.root) as connection:
            connection.execute(
                """INSERT INTO readings(
                       id,source_item_id,reading_type,status,model_or_reviewer,
                       created_at,metadata_json
                   ) VALUES (?,?,?,'needs-review',?,?,?)""",
                (
                    reading_id,
                    source_item_id,
                    reading_type,
                    reader,
                    now,
                    json.dumps(reading_payload),
                ),
            )
            connection.execute(
                """INSERT INTO reading_versions(
                       id,reading_id,version_number,status,payload_json,created_at
                   ) VALUES (?,?,1,'needs-review',?,?)""",
                (version_id, reading_id, json.dumps(reading_payload), now),
            )
        return version_id

    def _seed_complete_claim_path(self, assertion_version: str = "version-context") -> None:
        now = utc_now()
        with connect(self.root) as connection:
            connection.execute(
                """INSERT INTO research_questions(
                       id,case_id,question,completion_criterion,status,created_at,updated_at
                   ) VALUES ('question-1','CASE-1','Czy twierdzenie jest prawdziwe?',
                             'Pełna ścieżka dowodowa','open',?,?)""",
                (now, now),
            )
            connection.execute(
                """INSERT INTO research_log_entries(
                       id,case_id,question_id,query_text,repository,scope_text,
                       outcome,result_summary,searched_at,created_at
                   ) VALUES ('log-1','CASE-1','question-1','pełna kwerenda',
                             'archiwum','pełny zakres','positive','wynik',?,?)""",
                (now, now),
            )
            connection.execute(
                """INSERT INTO search_coverage(
                       id,log_entry_id,appropriate,complete,systematic,created_at
                   ) VALUES ('coverage-1','log-1',1,1,1,?)""",
                (now,),
            )
            connection.execute(
                """INSERT INTO artifacts(
                       id,source_item_id,artifact_type,path,immutable,created_at
                   ) VALUES ('raw-1','item-1','RAW','raw.jpg',1,?)""",
                (now,),
            )
            connection.execute(
                """INSERT INTO transcript_segments(
                       id,reading_version_id,artifact_id,sequence_number,text
                   ) VALUES ('segment-1',?,'raw-1',0,'fragment')""",
                (assertion_version,),
            )
            connection.execute(
                """INSERT INTO source_assertions(
                       id,source_item_id,reading_version_id,transcript_segment_id,
                       assertion_type,predicate,verification_status
                   ) VALUES ('assertion-1','item-1',?,'segment-1',
                             'field','supports-test','imported_unverified')""",
                (assertion_version,),
            )
            connection.execute(
                """INSERT INTO claim_evidence(
                       id,claim_id,source_assertion_id,stance,evidence_type,dependency_group
                   ) VALUES ('evidence-1','claim-1','assertion-1','supports','Direct','item-1')"""
            )
            connection.execute(
                """INSERT INTO citations(
                       id,source_item_id,artifact_id,citation_text,locator,created_at
                   ) VALUES ('citation-1','item-1','raw-1','Akt testowy','akt 1',?)""",
                (now,),
            )
            connection.execute(
                """INSERT INTO conclusions(
                       id,case_id,claim_id,status,summary,reasoning,author,created_at,updated_at
                   ) VALUES ('conclusion-1','CASE-1','claim-1','final','wniosek',
                             'rozumowanie','badacz',?,?)""",
                (now, now),
            )

    def test_comparison_requires_independent_human_review_to_be_reviewed(self):
        draft = record_reading_comparison(
            self.root,
            "item-1",
            "version-blind",
            "version-context",
            [{"field": "surname", "blind": "A", "contextual": "B"}],
        )
        self.assertEqual(draft["status"], "draft")
        with self.assertRaisesRegex(ValueError, "human_review"):
            record_reading_comparison(
                self.root,
                "item-1",
                "version-blind",
                "version-context",
                [],
                status="reviewed",
            )
        reviewed = record_reading_comparison(
            self.root,
            "item-1",
            "version-blind",
            "version-context",
            [],
            reviewer_version_id="version-review",
            status="reviewed",
        )
        self.assertEqual(reviewed["status"], "reviewed")

    def test_claim_review_audits_lower_status_and_blocks_unsubstantiated_confirmed(self):
        result = review_claim_status(
            self.root,
            "claim-1",
            "Possible",
            reviewer="badacz-testowy",
            rationale="Robocza ocena na podstawie niepełnego materiału.",
        )
        self.assertEqual(result["claim"]["status"], "Possible")
        with connect(self.root) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0], 1)
        with self.assertRaisesRegex(ValueError, "Nie można nadać Confirmed"):
            review_claim_status(
                self.root,
                "claim-1",
                "Confirmed",
                reviewer="badacz-testowy",
                rationale="Za wcześnie.",
            )

    def test_privacy_label_is_idempotent(self):
        label_privacy(self.root, "source_item", "item-1", "restricted", "Materiał rodzinny.")
        label_privacy(self.root, "source_item", "item-1", "deceased", "Osoba zmarła.")
        with connect(self.root) as connection:
            row = connection.execute("SELECT * FROM privacy_labels").fetchone()
        self.assertEqual(row["classification"], "deceased")

    def test_confirmed_requires_and_accepts_complete_audited_path(self):
        self._seed_complete_claim_path()
        record_reading_comparison(
            self.root,
            "item-1",
            "version-blind",
            "version-context",
            [],
            reviewer_version_id="version-review",
            status="reviewed",
        )
        result = review_claim_status(
            self.root,
            "claim-1",
            "Confirmed",
            reviewer="badacz-testowy",
            rationale="Pełna kontrola ścieżki dowodowej.",
        )
        self.assertEqual(result["claim"]["status"], "Confirmed")
        self.assertTrue(result["confirmed_gate"]["eligible"])

    def test_agent_consensus_requires_two_truly_independent_blind_runs(self):
        blind_a = self._add_reading(
            "blind-a", "blind", reader="agent-a", run_id="run-a", context_disclosed=False
        )
        blind_b = self._add_reading(
            "blind-b", "blind", reader="agent-b", run_id="run-b", context_disclosed=False
        )
        decision = record_reading_review_decision(
            self.root,
            "item-1",
            "agent_consensus",
            [
                {"reading_version_id": blind_a, "input_role": "blind_primary"},
                {"reading_version_id": blind_b, "input_role": "blind_secondary"},
            ],
            decided_by="curator-agent",
            decided_by_kind="agent",
            rationale="Dwa zgodne, niezależne odczyty.",
            decision={"agreement": "all_fields"},
            expected_revision=1,
        )
        self.assertEqual(decision["revision"], 1)
        self.assertEqual(decision["status"], "agent_consensus")
        self.assertEqual(len(decision["inputs"]), 2)
        self.assertEqual(get_reading_review_decision(self.root, decision["id"])["decision"], {
            "agreement": "all_fields"
        })
        self.assertEqual(
            list_reading_review_decisions(self.root, source_item_id="item-1", latest_only=True)[0]["id"],
            decision["id"],
        )

        disclosed = self._add_reading(
            "blind-disclosed",
            "blind",
            reader="agent-c",
            run_id="run-c",
            context_disclosed=True,
        )
        with self.assertRaisesRegex(ValueError, "context_disclosed=false"):
            record_reading_review_decision(
                self.root,
                "item-1",
                "agent_consensus",
                [
                    {"reading_version_id": blind_a, "input_role": "blind_primary"},
                    {"reading_version_id": disclosed, "input_role": "blind_secondary"},
                ],
                decided_by="curator-agent",
                decided_by_kind="agent",
                rationale="Niepoprawna próba.",
            )

        same_reader = self._add_reading(
            "blind-same-reader",
            "blind",
            reader="agent-a",
            run_id="run-d",
            context_disclosed=False,
        )
        with self.assertRaisesRegex(ValueError, "różnych czytelników"):
            record_reading_review_decision(
                self.root,
                "item-1",
                "agent_consensus",
                [
                    {"reading_version_id": blind_a, "input_role": "blind_primary"},
                    {"reading_version_id": same_reader, "input_role": "blind_secondary"},
                ],
                decided_by="curator-agent",
                decided_by_kind="agent",
                rationale="Niepoprawna próba.",
            )
        same_run = self._add_reading(
            "blind-same-run",
            "blind",
            reader="agent-d",
            run_id="run-a",
            context_disclosed=False,
        )
        with self.assertRaisesRegex(ValueError, "różnych uruchomień"):
            record_reading_review_decision(
                self.root,
                "item-1",
                "agent_consensus",
                [
                    {"reading_version_id": blind_a, "input_role": "blind_primary"},
                    {"reading_version_id": same_run, "input_role": "blind_secondary"},
                ],
                decided_by="curator-agent",
                decided_by_kind="agent",
                rationale="Niepoprawna próba.",
            )

    def test_agent_adjudication_is_append_only_and_requires_independent_adjudicator(self):
        blind_a = self._add_reading(
            "blind-a", "blind", reader="agent-a", run_id="run-a", context_disclosed=False
        )
        blind_b = self._add_reading(
            "blind-b", "blind", reader="agent-b", run_id="run-b", context_disclosed=False
        )
        record_reading_review_decision(
            self.root,
            "item-1",
            "agent_consensus",
            [
                {"reading_version_id": blind_a, "input_role": "blind_primary"},
                {"reading_version_id": blind_b, "input_role": "blind_secondary"},
            ],
            decided_by="curator-agent",
            decided_by_kind="agent",
            rationale="Pierwsza rewizja.",
        )
        contextual_only = self._add_reading(
            "contextual-only",
            "contextual",
            reader="agent-c",
            run_id="run-c-contextual",
            context_disclosed=True,
        )
        with self.assertRaisesRegex(ValueError, "osobnego odczytu"):
            record_reading_review_decision(
                self.root,
                "item-1",
                "agent_adjudicated",
                [
                    {"reading_version_id": blind_a, "input_role": "blind_primary"},
                    {"reading_version_id": blind_b, "input_role": "blind_secondary"},
                    {
                        "reading_version_id": contextual_only,
                        "input_role": "agent_adjudicator",
                    },
                ],
                decided_by="agent-c",
                decided_by_kind="agent",
                decided_by_run_id="run-c-contextual",
                rationale="Contextual nie jest osobnym przebiegiem adjudykacji.",
            )
        adjudicator = self._add_reading(
            "adjudicator",
            "adjudication",
            reader="agent-c",
            run_id="run-c",
            context_disclosed=True,
        )
        with self.assertRaisesRegex(ValueError, "decided_by_run_id"):
            record_reading_review_decision(
                self.root,
                "item-1",
                "agent_adjudicated",
                [
                    {"reading_version_id": blind_a, "input_role": "blind_primary"},
                    {"reading_version_id": blind_b, "input_role": "blind_secondary"},
                    {"reading_version_id": adjudicator, "input_role": "agent_adjudicator"},
                ],
                decided_by="agent-c",
                decided_by_kind="agent",
                decided_by_run_id="wrong-run",
                rationale="Nie można podmienić przebiegu adjudicatora.",
            )
        decision = record_reading_review_decision(
            self.root,
            "item-1",
            "agent_adjudicated",
            [
                {"reading_version_id": blind_a, "input_role": "blind_primary"},
                {"reading_version_id": blind_b, "input_role": "blind_secondary"},
                {"reading_version_id": adjudicator, "input_role": "agent_adjudicator"},
            ],
            decided_by="agent-c",
            decided_by_kind="agent",
            decided_by_run_id="run-c",
            rationale="Trzeci agent rozstrzygnął różnicę.",
            expected_revision=2,
        )
        self.assertEqual(decision["revision"], 2)
        self.assertIsNotNone(decision["supersedes_decision_id"])
        with connect(self.root) as connection:
            with self.assertRaisesRegex(Exception, "append-only"):
                connection.execute(
                    "UPDATE reading_review_decisions SET rationale='zmiana' WHERE id=?",
                    (decision["id"],),
                )
        with self.assertRaisesRegex(ValueError, "Konflikt rewizji"):
            record_reading_review_decision(
                self.root,
                "item-1",
                "human_review_required",
                [{"reading_version_id": adjudicator, "input_role": "agent_adjudicator"}],
                decided_by="curator-agent",
                decided_by_kind="agent",
                rationale="Konflikt wymaga człowieka.",
                expected_revision=2,
            )

    def test_review_inputs_must_share_source_and_human_status_needs_human_reading(self):
        with connect(self.root) as connection:
            connection.execute(
                """INSERT INTO source_items(
                       id,title,item_type,source_quality,verification_status
                   ) VALUES ('item-2','Inny akt','record','Original','imported_unverified')"""
            )
        other = self._add_reading(
            "other", "blind", source_item_id="item-2", reader="agent-z",
            run_id="run-z", context_disclosed=False,
        )
        with self.assertRaisesRegex(ValueError, "wskazanego dokumentu"):
            record_reading_review_decision(
                self.root,
                "item-1",
                "agent_single_pass",
                [{"reading_version_id": other, "input_role": "blind_primary"}],
                decided_by="curator-agent",
                decided_by_kind="agent",
                rationale="Niepoprawne źródło.",
            )
        with self.assertRaisesRegex(ValueError, "human_review"):
            record_reading_review_decision(
                self.root,
                "item-1",
                "human_reviewed",
                [{"reading_version_id": "version-context", "input_role": "contextual"}],
                decided_by="human-curator",
                decided_by_kind="human",
                rationale="Brak rzeczywistego human review.",
            )
        human = self._add_reading(
            "human-kind-check",
            "human_review",
            reader="human-a",
            run_id="human-run-a",
            context_disclosed=True,
        )
        with self.assertRaisesRegex(ValueError, "decided_by_kind=human"):
            record_reading_review_decision(
                self.root,
                "item-1",
                "human_reviewed",
                [{"reading_version_id": human, "input_role": "human_review"}],
                decided_by="human-a",
                decided_by_kind="agent",
                decided_by_run_id="human-run-a",
                rationale="Niepoprawny typ decydenta.",
            )

    def test_agent_status_does_not_open_confirmed_but_human_reviewed_does(self):
        blind_a = self._add_reading(
            "blind-a", "blind", reader="agent-a", run_id="run-a", context_disclosed=False
        )
        blind_b = self._add_reading(
            "blind-b", "blind", reader="agent-b", run_id="run-b", context_disclosed=False
        )
        contextual = self._add_reading(
            "contextual-new", "adjudication", reader="agent-c", run_id="run-c",
            context_disclosed=True,
        )
        self._seed_complete_claim_path(contextual)
        record_reading_review_decision(
            self.root,
            "item-1",
            "agent_adjudicated",
            [
                {"reading_version_id": blind_a, "input_role": "blind_primary"},
                {"reading_version_id": blind_b, "input_role": "blind_secondary"},
                {"reading_version_id": contextual, "input_role": "agent_adjudicator"},
            ],
            decided_by="agent-c",
            decided_by_kind="agent",
            decided_by_run_id="run-c",
            rationale="Agentowe rozstrzygnięcie.",
        )
        with self.assertRaisesRegex(ValueError, "zatwierdzonego porównania"):
            review_claim_status(
                self.root,
                "claim-1",
                "Confirmed",
                reviewer="badacz",
                rationale="Status agentowy nie wystarcza.",
            )
        human = self._add_reading(
            "human-new", "human_review", reader="human-a", run_id="human-run-a",
            context_disclosed=True,
        )
        record_reading_review_decision(
            self.root,
            "item-1",
            "human_reviewed",
            [
                {"reading_version_id": contextual, "input_role": "agent_adjudicator"},
                {"reading_version_id": human, "input_role": "human_review"},
            ],
            decided_by="human-a",
            decided_by_kind="human",
            decided_by_run_id="human-run-a",
            rationale="Człowiek sprawdził obraz i odczyt.",
        )
        result = review_claim_status(
            self.root,
            "claim-1",
            "Confirmed",
            reviewer="badacz",
            rationale="Pełna ścieżka wraz z human review.",
        )
        self.assertTrue(result["confirmed_gate"]["eligible"])

    def test_coverage_of_one_question_does_not_unlock_another_question(self):
        now = utc_now()
        with connect(self.root) as connection:
            for question_id in ("question-1", "question-2"):
                connection.execute(
                    """INSERT INTO research_questions(
                           id,case_id,question,completion_criterion,status,created_at,updated_at
                       ) VALUES (?, 'CASE-1', ?, 'kryterium','open',?,?)""",
                    (question_id, question_id, now, now),
                )
            connection.execute(
                """INSERT INTO research_log_entries(
                       id,case_id,question_id,query_text,scope_text,outcome,searched_at,created_at
                   ) VALUES ('log-1','CASE-1','question-1','q1','pełny','positive',?,?)""",
                (now, now),
            )
            connection.execute(
                """INSERT INTO search_coverage(
                       id,log_entry_id,appropriate,complete,systematic,created_at
                   ) VALUES ('coverage-1','log-1',1,1,1,?)""",
                (now,),
            )
        with self.assertRaisesRegex(ValueError, "pokrycia kwerendy"):
            review_claim_status(
                self.root,
                "claim-1",
                "Confirmed",
                reviewer="badacz",
                rationale="Brak pokrycia drugiego pytania.",
            )


if __name__ == "__main__":
    unittest.main()
