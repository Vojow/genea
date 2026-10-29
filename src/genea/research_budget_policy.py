"""Workspace operational budget policy, independent of account/provider limits."""
from __future__ import annotations

import json
from pathlib import Path


class BudgetPolicyError(ValueError):
    """An explicit workspace policy must be valid before any telemetry action."""


POLICY_PATH = Path("research/cost-policy.json")


def report_only(root):
    path = Path(root).resolve() / POLICY_PATH
    if any(component.is_symlink() for component in (path, *path.parents)):
        raise BudgetPolicyError("Workspace cost policy must not use symlinks")
    if not path.exists():
        return False
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BudgetPolicyError("Invalid or unreadable workspace cost policy") from exc
    if (not isinstance(value, dict)
            or set(value) != {"schemaVersion", "enforcement", "authorization"}
            or type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1
            or value["enforcement"] != "report_only"
            or not isinstance(value["authorization"], str)
            or not value["authorization"].strip()):
        raise BudgetPolicyError("Workspace cost policy requires schemaVersion 1, enforcement report_only and explicit authorization")
    return True
