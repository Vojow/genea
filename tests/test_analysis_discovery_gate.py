from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from genea.analysis import run_analysis


class AnalysisDiscoveryGateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "discovery.json"
        example = Path(__file__).resolve().parent / "fixtures/synthetic-discovery.json"
        self.plan = json.loads(example.read_text(encoding="utf-8"))
        self.write_plan()

    def write_plan(self):
        self.path.write_text(json.dumps(self.plan), encoding="utf-8")

    def test_absent_plan_or_binding_blocks_every_side_effect_even_dry_run(self):
        for dry_run in (False, True):
            for kwargs in ({}, {"discovery_plan": self.path}, {"lead_id": "EXAMPLE-LEAD-1"}):
                with self.subTest(dry_run=dry_run, kwargs=kwargs):
                    with patch("genea.analysis.initialize") as initialize, patch("genea.analysis.get_source") as get_source, patch("genea.analysis.codex_command") as command, patch("genea.analysis.subprocess.run") as subprocess:
                        with self.assertRaisesRegex(ValueError, "discovery.plan"):
                            run_analysis(self.root, "EXAMPLE-SOURCE-1", dry_run=dry_run, **kwargs)
                        for mock in (initialize, get_source, command, subprocess):
                            mock.assert_not_called()

    def test_invalid_plan_lead_and_source_block_before_initialize_or_subprocess(self):
        for case in ("malformed", "missing", "unselected", "reuse", "source_mismatch", "unknown_lead", "missing_reason"):
            for dry_run in (False, True):
                with self.subTest(case=case, dry_run=dry_run):
                    self.write_plan()
                    source, lead = "EXAMPLE-SOURCE-1", "EXAMPLE-LEAD-1"
                    if case == "malformed":
                        self.path.write_text("{", encoding="utf-8")
                    elif case == "missing":
                        self.path.unlink()
                    elif case == "unselected":
                        lead = "EXAMPLE-LEAD-2"
                    elif case == "reuse":
                        source, lead = "EXAMPLE-SOURCE-OLD", "EXAMPLE-LEAD-3"
                    elif case == "source_mismatch":
                        source = "WRONG"
                    elif case == "unknown_lead":
                        lead = "UNKNOWN"
                    elif case == "missing_reason":
                        payload = json.loads(self.path.read_text())
                        payload["leads"][0]["reason"] = ""
                        self.path.write_text(json.dumps(payload))
                    with patch("genea.analysis.initialize") as initialize, patch("genea.analysis.get_source") as get_source, patch("genea.analysis.codex_command") as command, patch("genea.analysis.subprocess.run") as subprocess:
                        with self.assertRaises(ValueError):
                            run_analysis(self.root, source, discovery_plan=self.path, lead_id=lead, dry_run=dry_run)
                        for mock in (initialize, get_source, command, subprocess):
                            mock.assert_not_called()

    def test_valid_plan_launches_existing_flow_and_preserves_import_arguments(self):
        output = self.root / "response.json"
        output.write_text('{"document_type":"fictional","research_discovery":{"inputSha256":"MODEL-FABRICATION"}}', encoding="utf-8")
        with patch("genea.analysis.initialize") as initialize, patch("genea.analysis.codex_command", return_value=(["codex", "NEUTRAL PROMPT"], output)) as command, patch("genea.analysis.subprocess.run") as subprocess, patch("genea.analysis.import_analysis", return_value={"analysis_id": "test-reading"}) as importer:
            result = run_analysis(self.root, "EXAMPLE-SOURCE-1", discovery_plan=self.path, lead_id="EXAMPLE-LEAD-1", model="test-model", researcher_context="Explicit existing context")
        initialize.assert_called_once_with(self.root)
        command.assert_called_once_with(self.root, "EXAMPLE-SOURCE-1", model="test-model", researcher_context="Explicit existing context")
        subprocess.assert_called_once_with(["codex", "NEUTRAL PROMPT"], check=True)
        receipt = {field: result["discovery"][field] for field in ("schemaVersion", "inputSha256", "boundLeadId", "boundSourceId")}
        importer.assert_called_once_with(self.root, {"document_type": "fictional", "source_id": "EXAMPLE-SOURCE-1", "research_discovery": receipt}, model="test-model")
        self.assertEqual(result["analysis_id"], "test-reading")
        self.assertEqual(len(result["discovery"]["inputSha256"]), 64)

    def test_plan_context_never_enters_blind_prompt_or_command(self):
        secret = "SECRET EXPECTED FAMILY AND IDENTITY HYPOTHESIS"
        self.plan["question"] = secret
        self.plan["leads"][0]["expectedInformation"] = secret
        self.plan["leads"][0]["reportedData"] = {"literal": secret}
        self.write_plan()
        (self.root / "schemas").mkdir()
        (self.root / "schemas/act-analysis.schema.json").write_text("{}")
        with patch("genea.analysis.initialize"), patch("genea.analysis.shutil.which", return_value="/bin/codex"), patch("genea.analysis.visual_files", return_value=[self.root / "image.png"]), patch("genea.analysis.build_prompt", return_value="NEUTRAL PROMPT") as prompt, patch("genea.analysis.subprocess.run") as subprocess:
            result = run_analysis(self.root, "EXAMPLE-SOURCE-1", discovery_plan=self.path, lead_id="EXAMPLE-LEAD-1", dry_run=True)
        prompt.assert_called_once_with(self.root, "EXAMPLE-SOURCE-1", None)
        subprocess.assert_not_called()
        self.assertIn("NEUTRAL PROMPT", result["command"])
        self.assertNotIn(secret, json.dumps(result))
        self.assertNotIn(str(self.path), result["command"])


if __name__ == "__main__":
    unittest.main()
