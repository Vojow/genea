#!/usr/bin/env python3
"""Check only Git index blobs before publishing a source tree, entirely offline."""
from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Iterable, TypedDict


class Entry(TypedDict):
    path: str
    mode: str
    bytes: bytes


BLOCKED_ROOTS = {
    "research", "sources", ".genea", ".codex", ".local-workspace",
    "output", "work", "node_modules", ".venv",
}
DATABASE = re.compile(r"\.(?:sqlite\d*|db\d*|sqlcipher)(?:$|[.\-])", re.I)
PRIVATE_FILE = re.compile(
    r"(?:\.(?:pem|key|p12|pfx|keystore|kdbx|agekey)$|"
    r"(?:^|[._-])(?:recovery[._-]?(?:key|codes?|phrase)|seed[._-]?phrase)(?:$|[._-])|"
    r"(?:^|[._-])recovery(?:[._-][^.]+)*\.(?:json|txt|yaml|yml|bin)(?:$|[.\-])|"
    r"^recovery$|^id_(?:rsa|dsa|ecdsa|ed25519)(?:$|\.))",
    re.I,
)
BROWSER_FILE = re.compile(
    r"^(?:cookies|history|login data|web data|local state|preferences|secure preferences)"
    r"(?:$|[.\-])|(?:storage[._-]?state|browser[._-]?state|auth[._-]?state).*\.json$",
    re.I,
)
CONTENT_RULES = (
    ("external-user-path", re.compile(rb"/(?:Users|home)/[A-Za-z0-9_.-]+(?:/|\b)")),
    ("private-key-material", re.compile(rb"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----")),
    ("cloud-access-key", re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("service-token", re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-(?:proj-)?[A-Za-z0-9_-]{24,})\b")),
    ("credential-url", re.compile(rb"https?://([^\s/:@]+:[^\s/@]+)@([A-Za-z0-9.-]+)", re.I)),
    ("bearer-token", re.compile(rb"\bBearer[ \t]+[A-Za-z0-9._~+/=-]{24,}", re.I)),
    ("credential-assignment", re.compile(
        rb"\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password|passwd|secret[_-]?key)"
        rb"[\"']?\s*[:=]\s*[\"'][A-Za-z0-9+/_.=-]{24,}[\"']", re.I)),
)
# Only these short, explicit dummy credentials on reserved example hosts are
# harmless fixtures. Other credentials, including on example hosts, still fail.
DUMMY_USERINFO = re.compile(rb"user:(?:pass|secret)", re.I)
DUMMY_HOST = re.compile(rb"example\.(?:invalid|org)", re.I)


def path_rules(path: str, mode: str) -> list[str]:
    """Inspect metadata first so forbidden private blobs need never be opened."""
    rules: list[str] = []
    parts = PurePosixPath(path).parts
    if mode not in {"100644", "100755"}:
        rules.append("non-regular-index-entry")
    if not parts or path.startswith("/") or ".." in parts:
        rules.append("invalid-index-path")
    if (parts and parts[0] in BLOCKED_ROOTS) or parts[:2] == ("frontend", "data"):
        rules.append("private-or-runtime-root")
    if any(part in {"node_modules", ".venv", ".genea", ".codex", ".local-workspace"} for part in parts[1:]):
        rules.append("nested-runtime-root")
    name = parts[-1] if parts else ""
    if DATABASE.search(name):
        rules.append("database-file")
    if PRIVATE_FILE.search(name):
        rules.append("key-or-recovery-file")
    if re.search(r"\.gpa(?:$|[.\-])", name, re.I):
        rules.append("encrypted-attachment-file")
    if name.lower() == ".env" or name.lower().startswith(".env.") or name.lower() in {".netrc", ".npmrc", ".pypirc"}:
        rules.append("environment-or-credential-file")
    if BROWSER_FILE.search(name):
        rules.append("browser-state-file")
    return rules


def scan_entries(entries: Iterable[Entry]) -> list[tuple[str, str]]:
    violations: list[tuple[str, str]] = []
    count = 0
    for entry in entries:
        count += 1
        path, mode = entry["path"], entry["mode"]
        metadata = path_rules(path, mode)
        violations.extend((path, rule) for rule in metadata)
        if not metadata:
            for rule, pattern in CONTENT_RULES:
                matches = pattern.finditer(entry["bytes"])
                if rule == "credential-url":
                    matches = (
                        match for match in matches
                        if not (DUMMY_USERINFO.fullmatch(match[1]) and DUMMY_HOST.fullmatch(match[2]))
                    )
                if next(matches, None) is not None:
                    violations.append((path, rule))
    if not count:
        violations.append((".", "empty-index"))
    return violations


def _git(root: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "--no-pager", "-C", str(root), *arguments],
        capture_output=True, check=False,
    )


def check_index(root: Path) -> list[tuple[str, str]]:
    root = root.resolve()
    try:
        top = _git(root, "rev-parse", "--show-toplevel")
        if top.returncode or Path(top.stdout.decode("utf-8").strip()).resolve() != root:
            return [(".", "missing-local-git-index")]
        index = _git(root, "ls-files", "--stage", "-z")
        if index.returncode:
            return [(".", "unreadable-index")]
        if not index.stdout:
            return [(".", "empty-index")]
        violations: list[tuple[str, str]] = []
        for record in index.stdout.rstrip(b"\0").split(b"\0"):
            metadata, raw_path = record.split(b"\t", 1)
            mode, _object_id, stage = metadata.decode("ascii").split()
            path = raw_path.decode("utf-8", errors="surrogateescape")
            rules = path_rules(path, mode)
            violations.extend((path, rule) for rule in rules)
            if stage != "0":
                violations.append((path, "unmerged-index-entry"))
            if rules or stage != "0":
                continue
            blob = _git(root, "show", ":" + path)
            if blob.returncode:
                violations.append((path, "unreadable-index-blob"))
                continue
            violations.extend(scan_entries([{"path": path, "mode": mode, "bytes": blob.stdout}]))
        return violations
    except (OSError, ValueError, UnicodeError):
        return [(".", "unreadable-index")]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    violations = check_index(args.root)
    for path, rule in sorted(set(violations)):
        # repr escapes newline/control characters; never print a matched value.
        print(f"{path!r}: {rule}")
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
