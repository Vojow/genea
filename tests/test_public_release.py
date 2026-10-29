from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from genea.private_drive_import import _source_provenance


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PublicReleaseTests(unittest.TestCase):
    def _cli(self, root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(PROJECT_ROOT / "src")
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            [sys.executable, "-m", "genea.cli", "--root", str(root), *arguments],
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def test_help_loads_without_private_pilot_module(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = self._cli(Path(temporary), "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("migration-status", result.stdout)
        self.assertIn("reading-compare", result.stdout)
        self.assertNotIn("pilot-", result.stdout)

    def test_init_and_migration_status_use_temporary_workspace(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            initialized = self._cli(root, "init")
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            initialization = json.loads(initialized.stdout)
            self.assertEqual(initialization["status"], "ready")
            database = Path(initialization["database"])
            self.assertTrue(database.is_relative_to(root))
            self.assertTrue(database.is_file())
            before = database.read_bytes()

            status = self._cli(root, "migration-status")
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertEqual(json.loads(status.stdout)["pending"], 0)
            self.assertEqual(database.read_bytes(), before)

    def test_source_identifiers_do_not_imply_family_testimony(self):
        identifiers = ["", "synthetic-source", "synthetic-family-account"]
        identifiers.extend(f"S{index:04d}" for index in range(1, 65))
        for source_id in identifiers:
            with self.subTest(source_id=source_id):
                self.assertEqual(
                    _source_provenance(source_id), ("imported_unverified", None)
                )


if __name__ == "__main__":
    unittest.main()
