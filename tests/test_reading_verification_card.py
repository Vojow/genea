from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from genea.reading_verification_card import (  # noqa: E402
    CARD_SCHEMA,
    ReadingVerificationCardError,
    project_reading_verification_card,
)


class ReadingVerificationCardTests(unittest.TestCase):
    def _field(
        self, path: str, *, review_status: str = "machine_proposal"
    ) -> dict[str, object]:
        return {
            "path": path,
            "role": path.split(".")[0],
            "value": f"literal {path}",
            "normalizedValue": f"normalized {path}",
            "confidence": 0.91,
            "certainty": "Probable",
            "alternatives": ["wariant"],
            "materialAlternatives": ["wariant istotny"],
            "sourceQuote": "fragment aktu",
            "sourceSegmentId": f"segment-{path}",
            "bbox": {"x": 1, "y": 2, "width": 30, "height": 12},
            "criticality": "critical" if path == "agreed.name" else "supporting",
            "reviewStatus": review_status,
        }

    def _blind(
        self, reading_id: str, values: dict[str, str]
    ) -> dict[str, object]:
        return {
            "id": reading_id,
            "readerId": f"reader-{reading_id}",
            "runId": f"run-{reading_id}",
            "contextDisclosed": False,
            "fields": [
                {
                    "path": path,
                    "value": value,
                    "normalizedValue": value,
                    "certainty": "Certain",
                    "sourceSegment": f"raw-{path}",
                    "bbox": {"x": 1, "y": 2, "width": 30, "height": 12},
                }
                for path, value in values.items()
            ],
        }

    def test_projects_every_main_field_and_keeps_axes_separate(self) -> None:
        fields = [
            self._field("agreed.name"),
            self._field("disputed.name", review_status="human_review_required"),
            self._field("incomplete.name"),
            self._field("unread.name"),
        ]
        blinds = [
            self._blind(
                "a",
                {
                    "agreed.name": "Jan",
                    "disputed.name": "Anna",
                    "incomplete.name": "Piotr",
                },
            ),
            self._blind(
                "b",
                {"agreed.name": "Jan", "disputed.name": "Hanna"},
            ),
        ]
        comparison = {
            "fields": [
                {
                    "path": "agreed.name",
                    "status": "agreement",
                    "differenceKind": "agreement",
                    "values": [
                        {"readingId": "a", "readerId": "reader-a", "value": "Jan"},
                        {"readingId": "b", "readerId": "reader-b", "value": "Jan"},
                    ],
                },
                {
                    "path": "disputed.name",
                    "status": "disagreement",
                    "differenceKind": "different_value",
                    "values": [
                        {"readingId": "a", "readerId": "reader-a", "value": "Anna"},
                        {"readingId": "b", "readerId": "reader-b", "value": "Hanna"},
                    ],
                },
                {
                    "path": "incomplete.name",
                    "status": "disagreement",
                    "differenceKind": "missing_in_some",
                    "values": [
                        {"readingId": "a", "readerId": "reader-a", "value": "Piotr"},
                        {"readingId": "b", "readerId": "reader-b", "value": None},
                    ],
                },
            ]
        }
        issues = [
            {
                "id": "issue-field",
                "fieldPath": "disputed.name",
                "reasonCode": "independent_reading_disagreement",
                "priority": 1,
                "recommendedRole": "agent_adjudicator",
                "details": {},
            },
            {
                "id": "issue-layer",
                "fieldPath": "layer:translation",
                "reasonCode": "missing_layer",
                "priority": 2,
                "recommendedRole": "contextual_reader",
                "details": {},
            },
        ]

        card = project_reading_verification_card(
            source_item_id="source-1",
            artifact_id="artifact-1",
            fields=fields,
            independent_readings=blinds,
            comparison=comparison,
            issues=issues,
        )

        self.assertEqual(card["schema"], CARD_SCHEMA)
        self.assertEqual(
            card["summary"],
            {
                "fieldCount": 4,
                "agreedCount": 1,
                "disputedCount": 1,
                "incompleteCount": 1,
                "notComparedCount": 1,
                "humanReviewRequiredCount": 1,
            },
        )
        by_path = {field["path"]: field for field in card["fields"]}
        self.assertEqual(set(by_path), {field["path"] for field in fields})
        self.assertEqual(by_path["agreed.name"]["comparison"]["status"], "agreed")
        self.assertEqual(
            by_path["agreed.name"]["workflow"],
            {"status": "machine_proposal", "basis": "pipeline_assessment"},
        )
        self.assertEqual(
            by_path["disputed.name"]["comparison"]["status"], "disputed"
        )
        self.assertEqual(
            by_path["disputed.name"]["workflow"]["status"],
            "human_review_required",
        )
        self.assertEqual(
            by_path["incomplete.name"]["comparison"]["status"], "incomplete"
        )
        self.assertEqual(
            by_path["unread.name"]["comparison"]["status"], "not_compared"
        )
        self.assertEqual(len(by_path["disputed.name"]["blindReadings"]), 2)
        self.assertEqual(by_path["disputed.name"]["proposal"]["value"], "literal disputed.name")
        self.assertEqual(
            by_path["disputed.name"]["backing"],
            {
                "sourceItemId": "source-1",
                "artifactId": "artifact-1",
                "sourceSegmentId": "segment-disputed.name",
                "bbox": {"x": 1, "y": 2, "width": 30, "height": 12},
            },
        )
        self.assertEqual([issue["id"] for issue in card["issues"]], ["issue-layer"])
        self.assertEqual(
            [issue["id"] for issue in by_path["disputed.name"]["issues"]],
            ["issue-field"],
        )

    def test_human_reviewed_requires_explicit_valid_human_overlay(self) -> None:
        field = self._field("person.name")
        invalid = project_reading_verification_card(
            source_item_id="source-1",
            artifact_id="artifact-1",
            fields=[field],
            independent_readings=[
                self._blind("a", {"person.name": "Jan"}),
                self._blind("b", {"person.name": "Jan"}),
            ],
            review_overlay={
                "id": "review-1",
                "revision": 1,
                "status": "human_reviewed",
                "decidedByKind": "agent",
                "fields": [{"field": "person.name", "status": "human_reviewed"}],
            },
        )
        projected = invalid["fields"][0]
        self.assertEqual(projected["comparison"]["status"], "agreed")
        self.assertEqual(projected["workflow"]["status"], "machine_proposal")

        valid = project_reading_verification_card(
            source_item_id="source-1",
            artifact_id="artifact-1",
            fields=[field],
            independent_readings=[],
            review_overlay={
                "id": "review-2",
                "revision": 2,
                "status": "human_reviewed",
                "decidedByKind": "human",
                "fields": [{"field": "person.name", "status": "human_reviewed"}],
            },
        )
        self.assertEqual(
            valid["fields"][0]["workflow"],
            {
                "status": "human_reviewed",
                "basis": "review_decision",
                "decisionId": "review-2",
                "revision": 2,
            },
        )

    def test_output_validates_and_never_exposes_filesystem_paths(self) -> None:
        card = project_reading_verification_card(
            source_item_id="source-1",
            artifact_id="artifact-1",
            fields=[self._field("person.name")],
        )
        schema = json.loads(
            (ROOT / "schemas" / "reading-verification-card.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(schema["$id"], CARD_SCHEMA)
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(card)
        serialized = json.dumps(card, ensure_ascii=False)
        self.assertNotIn("/research/", serialized)
        self.assertNotIn("original.jpg", serialized)
        self.assertNotIn('"path": "/', serialized)

    def test_stored_agreement_without_two_values_is_incomplete(self) -> None:
        card = project_reading_verification_card(
            source_item_id="source-1",
            artifact_id="artifact-1",
            fields=[self._field("person.name")],
            comparison={
                "fields": [
                    {
                        "path": "person.name",
                        "status": "agreement",
                        "differenceKind": "agreement",
                        "values": [{"readingId": "blind-a", "value": "Jan"}],
                    }
                ]
            },
        )
        self.assertEqual(card["fields"][0]["comparison"]["status"], "incomplete")
        self.assertEqual(
            card["fields"][0]["comparison"]["differenceKind"], "missing_in_some"
        )

    def test_stored_agreement_with_conflicting_values_is_disputed(self) -> None:
        card = project_reading_verification_card(
            source_item_id="source-1",
            artifact_id="artifact-1",
            fields=[self._field("person.name")],
            independent_readings=[
                self._blind("a", {"person.name": "Jan"}),
                self._blind("b", {"person.name": "Hanna"}),
            ],
            comparison={
                "fields": [
                    {
                        "path": "person.name",
                        "status": "agreement",
                        "differenceKind": "agreement",
                        "values": [
                            {"readingId": "a", "readerId": "reader-a", "value": "Jan"},
                            {"readingId": "b", "readerId": "reader-b", "value": "Hanna"},
                        ],
                    }
                ]
            },
        )
        self.assertEqual(card["fields"][0]["comparison"]["status"], "disputed")
        self.assertEqual(
            card["fields"][0]["comparison"]["differenceKind"], "different_value"
        )

    def test_stored_agreement_cannot_override_conflicting_blinds(self) -> None:
        card = project_reading_verification_card(
            source_item_id="source-1",
            artifact_id="artifact-1",
            fields=[self._field("person.name")],
            independent_readings=[
                self._blind("a", {"person.name": "Jan"}),
                self._blind("b", {"person.name": "Hanna"}),
            ],
            comparison={
                "fields": [
                    {
                        "path": "person.name",
                        "status": "agreement",
                        "differenceKind": "agreement",
                        "values": [
                            {"readingId": "a", "readerId": "reader-a", "value": "Jan"},
                            {"readingId": "b", "readerId": "reader-b", "value": "Jan"},
                        ],
                    }
                ]
            },
        )
        self.assertEqual(card["fields"][0]["comparison"]["status"], "disputed")
        self.assertEqual(
            card["fields"][0]["comparison"]["differenceKind"], "different_value"
        )

    def test_rejects_duplicate_or_pathless_main_fields(self) -> None:
        field = self._field("person.name")
        with self.assertRaisesRegex(ReadingVerificationCardError, "Powtórzona"):
            project_reading_verification_card(
                source_item_id="source-1",
                artifact_id="artifact-1",
                fields=[field, field],
            )
        with self.assertRaisesRegex(ReadingVerificationCardError, "path"):
            project_reading_verification_card(
                source_item_id="source-1",
                artifact_id="artifact-1",
                fields=[{"value": "Jan"}],
            )


if __name__ == "__main__":
    unittest.main()
