from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from genea.research_discovery import load_discovery_plan, validate_discovery_plan

EXAMPLE = Path(__file__).resolve().parent / "fixtures/synthetic-discovery.json"


class ResearchDiscoveryTests(unittest.TestCase):
    """RES-DISC: guard selection and reuse without imposing a research form."""

    def setUp(self):
        self.plan = json.loads(EXAMPLE.read_text(encoding="utf-8"))

    def bound(self):
        return validate_discovery_plan(self.plan, source_id="EXAMPLE-SOURCE-1", lead_id="EXAMPLE-LEAD-1")

    def test_compact_catalogue_preserves_candidates_without_identity_decision(self):
        before = copy.deepcopy(self.plan)
        result = self.bound()
        self.assertEqual(result["selectedLeadIds"], ["EXAMPLE-LEAD-1"])
        self.assertEqual(result["deferredLeadIds"], ["EXAMPLE-LEAD-2"])
        self.assertEqual(result["reuseLeadIds"], ["EXAMPLE-LEAD-3"])
        self.assertIsNone(result["identityDecision"])
        self.assertFalse(result["negativeEvidenceEstablished"])
        self.assertEqual(self.plan, before)

    def test_no_hits_or_unacquired_candidates_do_not_need_fictitious_data(self):
        self.plan["leads"] = []
        self.assertEqual(validate_discovery_plan(self.plan)["leadCount"], 0)
        with self.assertRaisesRegex(ValueError, "unknown lead"):
            self.bound()
        self.setUp()
        del self.plan["leads"][0]["sourceId"]
        validate_discovery_plan(self.plan)
        with self.assertRaisesRegex(ValueError, "sourceId"):
            self.bound()

    def test_direct_document_needs_only_scope_origin_and_selection(self):
        self.plan["searchSummary"] = "Document supplied directly; index unavailable; one act only."
        self.plan["leads"] = [self.plan["leads"][0]]
        self.plan["leads"][0]["provenance"] = {"backingIds": ["USER-DOCUMENT"]}
        self.bound()

    def test_optional_search_and_hypothesis_notes_are_neither_mandatory_nor_proof(self):
        self.plan.update(coverage={"M": "partial"}, identityDecision="same")
        self.plan["leads"][1]["hypothesis"] = "Possible competing household"
        result = self.bound()
        self.assertIsNone(result["identityDecision"])
        self.assertFalse(result["negativeEvidenceEstablished"])

    def test_binding_rejects_wrong_unselected_reused_and_missing_ids(self):
        for source, lead in (
            ("WRONG", "EXAMPLE-LEAD-1"), ("EXAMPLE-SOURCE-1", "UNKNOWN"),
            ("EXAMPLE-SOURCE-1", "EXAMPLE-LEAD-2"), ("EXAMPLE-SOURCE-OLD", "EXAMPLE-LEAD-3"),
            ("EXAMPLE-SOURCE-1", None), (None, "EXAMPLE-LEAD-1"),
        ):
            with self.subTest(source=source, lead=lead), self.assertRaises(ValueError):
                validate_discovery_plan(self.plan, source_id=source, lead_id=lead)

    def test_core_scope_provenance_and_selection_reason_remain_required(self):
        mutations = [
            lambda p: p.update(question=""), lambda p: p.update(scope=""),
            lambda p: p.update(searchSummary=""), lambda p: p["leads"][0].update(locator=""),
            lambda p: p["leads"][0].update(reason=""), lambda p: p["leads"][0].pop("readingStatus"),
            lambda p: p["leads"][0].update(provenance={}),
            lambda p: p["leads"].append(copy.deepcopy(p["leads"][0])),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.setUp()
                mutation(self.plan)
                with self.assertRaises(ValueError):
                    self.bound()

    def test_urls_reject_malformed_and_credentialed_inputs(self):
        for url in ("not-a-url", "https://user:pass@example.invalid/", "https://example.invalid:bad/", 42):
            with self.subTest(url=url):
                self.plan["leads"][0]["provenance"]["url"] = url
                with self.assertRaises(ValueError):
                    self.bound()

    def test_reusable_document_requires_explicit_reread_reason_and_reference(self):
        lead = self.plan["leads"][0]
        lead.update(readingStatus="reusable", existingWorkRefs=["OLD-READING"])
        with self.assertRaisesRegex(ValueError, "reusable"):
            self.bound()
        lead["readingStatus"] = "needs_reread"
        with self.assertRaisesRegex(ValueError, "rereadReason"):
            self.bound()
        lead["rereadReason"] = "New scan reveals the formerly clipped parent field."
        self.bound()
        lead["existingWorkRefs"] = []
        with self.assertRaisesRegex(ValueError, "existingWorkRefs"):
            self.bound()

    def test_duplicate_document_cannot_hide_reuse_but_distinct_acts_remain_separate(self):
        selected, reused = self.plan["leads"][0], self.plan["leads"][2]
        reused["sourceId"] = selected["sourceId"]
        self.bound()  # Same scan, different acts.
        reused["locator"] = selected["locator"]
        with self.assertRaisesRegex(ValueError, "contradictory"):
            self.bound()
        selected.update(readingStatus="needs_reread", existingWorkRefs=["OLD"], rereadReason="Changed input.")
        self.bound()

    def test_source_less_exact_repository_locator_also_preserves_reuse(self):
        selected, reused = self.plan["leads"][0], self.plan["leads"][2]
        for row in (selected, reused):
            row.pop("sourceId")
            row["provenance"]["repository"] = "Same repository"
        reused["locator"] = selected["locator"]
        with self.assertRaisesRegex(ValueError, "contradictory"):
            validate_discovery_plan(self.plan)

    def test_loader_preserves_exact_hash_and_does_not_rewrite_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            raw = json.dumps(self.plan, indent=3).encode() + b"\n"
            path.write_bytes(raw)
            self.assertEqual(load_discovery_plan(path)["inputSha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(path.read_bytes(), raw)

    def test_loader_rejects_missing_malformed_duplicate_nonfinite_and_invalid_encoding(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            with self.assertRaises(ValueError):
                load_discovery_plan(path)
            for raw in (b"{", b'{"schemaVersion":1,"schemaVersion":1}', b'{"x":NaN}', b"\xff"):
                with self.subTest(raw=raw):
                    path.write_bytes(raw)
                    with self.assertRaises(ValueError):
                        load_discovery_plan(path)

    def test_malformed_structure_fails_with_value_error(self):
        for value in (None, [], {}, {"schemaVersion": True}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_discovery_plan(value)


if __name__ == "__main__":
    unittest.main()
