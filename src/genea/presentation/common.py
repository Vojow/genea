from __future__ import annotations

import json
import re
from typing import Any

from ..event_records import EVENT_TYPE_GROUPS, event_group


def birth_detail(notes: str | None) -> str | None:
    if not notes:
        return None
    match = re.search(r"\burodzon[ay]\s+([^;,]+)", notes, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip().rstrip(".")
    match = re.search(
        r"\bdata\s+(prawdopodobnie\s+[^;,]+)", notes, flags=re.IGNORECASE
    )
    return match.group(1).strip() if match else None


def mergeable_event_dates(group: str, first: str | None, second: str | None) -> bool:
    if group == "census":
        return True
    if not first or not second:
        return True
    if first == second:
        return True
    return (len(first) == 4 and second.startswith(first)) or (
        len(second) == 4 and first.startswith(second)
    )


def unique_display_values(values: list[str | None]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value:
            continue
        key = " ".join(value.casefold().split())
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def json_object(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def json_array(value: str | None) -> list[Any]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def display_rationale(value: str | None) -> str | None:
    parsed = json_object(value)
    if parsed and isinstance(parsed.get("rationale"), str):
        return parsed["rationale"]
    return value
