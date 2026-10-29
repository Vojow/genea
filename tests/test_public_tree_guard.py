from __future__ import annotations

import importlib.util
import contextlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_public_tree.py"
SPEC = importlib.util.spec_from_file_location("public_tree_guard", SCRIPT)
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


class PublicTreeGuardTests(unittest.TestCase):
    def test_safe_source_and_empty_input(self):
        self.assertEqual(guard.scan_entries([{"path": "src/example.py", "mode": "100644", "bytes": b"print('hello')\n"}]), [])
        self.assertEqual(guard.scan_entries([]), [(".", "empty-index")])

    def test_symlink_submodule_and_private_roots(self):
        for mode in ("120000", "160000"):
            with self.subTest(mode=mode):
                self.assertIn(("src/link", "non-regular-index-entry"), guard.scan_entries([{"path": "src/link", "mode": mode, "bytes": b"target"}]))
        for root in guard.BLOCKED_ROOTS | {"frontend/data"}:
            with self.subTest(root=root):
                path = root + "/fixture.txt"
                self.assertIn((path, "private-or-runtime-root"), guard.scan_entries([{"path": path, "mode": "100644", "bytes": b"safe"}]))

    def test_database_keys_environment_and_browser_state(self):
        for name, rule in (
            ("copy.sqlite3-wal", "database-file"), ("copy.db.enc", "database-file"),
            ("private.pem", "key-or-recovery-file"), ("recovery-codes.txt", "key-or-recovery-file"),
            ("project-recovery.json", "key-or-recovery-file"),
            ("project_recovery.txt", "key-or-recovery-file"),
            ("project-recovery.yaml", "key-or-recovery-file"),
            ("project-recovery.yml", "key-or-recovery-file"),
            ("project-recovery.bin", "key-or-recovery-file"),
            ("project-recovery.json.enc", "key-or-recovery-file"),
            ("attachment.gpa", "encrypted-attachment-file"),
            (".env.production", "environment-or-credential-file"),
            ("storage-state.json", "browser-state-file"), ("Cookies", "browser-state-file"),
        ):
            with self.subTest(name=name):
                self.assertIn((name, rule), guard.scan_entries([{"path": name, "mode": "100644", "bytes": b"safe"}]))

    def test_generic_recovery_python_modules_are_publishable(self):
        for name in ("src/recovery.py", "tests/test_recovery.py", "src/project_recovery.py"):
            with self.subTest(name=name):
                self.assertEqual(guard.scan_entries([{"path": name, "mode": "100644", "bytes": b"# generic recovery logic"}]), [])

    def test_credential_url_exception_is_limited_to_explicit_dummies(self):
        def scan(userinfo, host):
            value = b"https://" + userinfo + b"@" + host + b"/"
            return guard.scan_entries([{"path": "fixture.txt", "mode": "100644", "bytes": value}])

        for password, host in ((b"pass", b"example.invalid"), (b"secret", b"example.org")):
            self.assertEqual(scan(b"user:" + password, host), [])
        for userinfo, host in (
            (b"user:" + b"x" * 24, b"example.invalid"),
            (b"user:" + b"pass", b"live.invalid"),
            (b"user:" + b"pass", b"example.org.live.invalid"),
            (b"account:" + b"pass", b"example.org"),
        ):
            self.assertIn(("fixture.txt", "credential-url"), scan(userinfo, host))

    def test_content_rules_use_neutral_generated_values(self):
        for content, rule in (
            (b"/" + b"Users" + b"/sample/project", "external-user-path"),
            (b"-----BEGIN " + b"PRIVATE KEY-----", "private-key-material"),
            (b"AK" + b"IA" + b"Z" * 16, "cloud-access-key"),
            (b"gh" + b"p_" + b"x" * 36, "service-token"),
            (b"https://" + b"example:synthetic@example.invalid/", "credential-url"),
            (b"Bearer " + b"x" * 30, "bearer-token"),
            (b"api_key=" + b'"' + b"x" * 30 + b'"', "credential-assignment"),
        ):
            with self.subTest(rule=rule):
                self.assertIn(("fixture.txt", rule), guard.scan_entries([{"path": "fixture.txt", "mode": "100644", "bytes": content}]))

    def _git(self, root, *arguments):
        return subprocess.run(["git", "-C", str(root), *arguments], check=True, capture_output=True)

    def test_git_index_overrides_worktree_and_ignored_paths_are_checked(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._git(root, "init", "--quiet")
            self.assertEqual(guard.check_index(root), [(".", "empty-index")])
            source = root / "example.py"
            source.write_bytes(b"print('hello')\n")
            self._git(root, "add", "example.py")
            self.assertEqual(guard.check_index(root), [])
            source.write_bytes(b"/" + b"Users" + b"/sample/project")
            self._git(root, "add", "example.py")
            source.write_bytes(b"print('safe worktree')\n")
            self.assertIn(("example.py", "external-user-path"), guard.check_index(root))
            (root / ".gitignore").write_text("research/\n", encoding="utf-8")
            (root / "research").mkdir()
            (root / "research/fixture.txt").write_text("synthetic", encoding="utf-8")
            self._git(root, "add", "-f", "research/fixture.txt")
            with patch.object(guard, "_git", wraps=guard._git) as commands:
                self.assertIn(("research/fixture.txt", "private-or-runtime-root"), guard.check_index(root))
            self.assertFalse(any("show" in call.args and ":research/fixture.txt" in call.args for call in commands.call_args_list))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(guard.main(["--root", str(root)]), 1)
            self.assertIn("example.py", output.getvalue())
            self.assertIn("external-user-path", output.getvalue())
            self.assertNotIn("sample/project", output.getvalue())

    def test_missing_repository_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual(guard.check_index(Path(temporary)), [(".", "missing-local-git-index")])


if __name__ == "__main__":
    unittest.main()
