from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from genea.workspace import dump_json


class WorkspaceWriteTests(unittest.TestCase):
    def test_concurrent_json_writers_leave_one_complete_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "payload.json"
            payloads = [
                {"writer": index, "values": list(range(index, index + 250))}
                for index in range(12)
            ]
            with ThreadPoolExecutor(max_workers=6) as pool:
                list(pool.map(lambda value: dump_json(target, value), payloads))
            stored = json.loads(target.read_text(encoding="utf-8"))
            self.assertIn(stored, payloads)
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
