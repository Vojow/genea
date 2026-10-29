from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from ..workspace import stable_id


_CYRILLIC = re.compile(r"[\u0400-\u052f]")
_LATIN = re.compile(r"[A-Za-zÀ-ž]")
_CYRILLIC_TRANSLITERATION = str.maketrans(
    {
        "А": "A", "а": "a", "Б": "B", "б": "b", "В": "V", "в": "v",
        "Г": "G", "г": "g", "Д": "D", "д": "d", "Е": "E", "е": "e",
        "Ё": "Jo", "ё": "jo", "Ж": "Ż", "ж": "ż", "З": "Z", "з": "z",
        "И": "I", "и": "i", "І": "I", "і": "i", "Й": "J", "й": "j",
        "К": "K", "к": "k", "Л": "L", "л": "l", "М": "M", "м": "m",
        "Н": "N", "н": "n", "О": "O", "о": "o", "П": "P", "п": "p",
        "Р": "R", "р": "r", "С": "S", "с": "s", "Т": "T", "т": "t",
        "У": "U", "у": "u", "Ф": "F", "ф": "f", "Х": "Ch", "х": "ch",
        "Ц": "C", "ц": "c", "Ч": "Cz", "ч": "cz", "Ш": "Sz", "ш": "sz",
        "Щ": "Szcz", "щ": "szcz", "Ы": "Y", "ы": "y", "Э": "E", "э": "e",
        "Ю": "Ju", "ю": "ju", "Я": "Ja", "я": "ja", "Ѣ": "Ie", "ѣ": "ie",
        "Ѳ": "F", "ѳ": "f", "Ѵ": "I", "ѵ": "i", "Ъ": "", "ъ": "",
        "Ь": "", "ь": "",
    }
)


def _script(value: str) -> str:
    has_cyrillic = bool(_CYRILLIC.search(value))
    has_latin = bool(_LATIN.search(value))
    if has_cyrillic and has_latin:
        return "mixed"
    if has_cyrillic:
        return "cyrillic"
    if has_latin:
        return "latin"
    return "unknown"


def transliterate_cyrillic(value: str) -> str | None:
    if not _CYRILLIC.search(value):
        return None
    return re.sub(r"\s+", " ", value.translate(_CYRILLIC_TRANSLITERATION)).strip()


def project_person_profiles(
    *,
    contexts: list[dict[str, Any]],
    people: list[dict[str, Any]],
    occurrences: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Project readable name layers without changing or inferring identity."""

    occurrences_by_id = {item.get("id"): item for item in occurrences}
    people_by_id = {item.get("id"): item for item in people}
    profiles: list[dict[str, Any]] = []

    for context in contexts:
        grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
        for occurrence_id in context.get("occurrenceIds", []):
            occurrence = occurrences_by_id.get(occurrence_id)
            if not occurrence:
                continue
            original = occurrence.get("name")
            if not isinstance(original, str) or not original.strip():
                continue
            original = original.strip()
            transliteration = transliterate_cyrillic(original)
            normalized = occurrence.get("normalizedName")
            polish_form = normalized.strip() if isinstance(normalized, str) and normalized.strip() else None
            if polish_form and polish_form.casefold() == original.casefold():
                polish_form = None
            script = _script(original)
            status = occurrence.get("status") or "unknown"
            key = (original, transliteration, polish_form, script, status)
            reading = grouped.setdefault(
                key,
                {
                    "id": stable_id("presented_name", context["personKey"], *[str(item or "") for item in key]),
                    "original": original,
                    "script": script,
                    "transliteration": transliteration,
                    "transliterationBasis": "deterministic_character_mapping" if transliteration else None,
                    "polishForm": polish_form,
                    "polishFormBasis": "occurrence_normalized_name" if polish_form else None,
                    "reviewStatus": status,
                    "reviewBasis": "occurrence_verification_status",
                    "confidence": occurrence.get("confidence"),
                    "uncertain": "?" in original or "[" in original or "]" in original,
                    "occurrenceIds": [],
                    "sourceIds": [],
                },
            )
            reading["occurrenceIds"].append(occurrence_id)
            source_id = occurrence.get("sourceId")
            if isinstance(source_id, str) and source_id:
                reading["sourceIds"].append(source_id)

        for reading in grouped.values():
            reading["occurrenceIds"] = sorted(set(reading["occurrenceIds"]))
            reading["sourceIds"] = sorted(set(reading["sourceIds"]))

        aliases: list[dict[str, Any]] = []
        for person_id in context.get("canonicalPersonIds", []):
            for name in people_by_id.get(person_id, {}).get("names", []):
                if not isinstance(name, dict) or not isinstance(name.get("text"), str):
                    continue
                aliases.append({
                    "text": name["text"],
                    "type": name.get("type"),
                    "language": name.get("language"),
                    "dateFrom": name.get("dateFrom"),
                    "dateTo": name.get("dateTo"),
                    "personId": person_id,
                })

        profiles.append(
            {
                "personKey": context["personKey"],
                "publicProfileId": context["publicProfileId"],
                "identityScope": context["identityScope"],
                "displayName": context["label"],
                "displayNameBasis": (
                    "canonical_preferred_name"
                    if context["identityScope"] == "canonical"
                    else "presentation_label_not_identity_decision"
                ),
                "nameReadings": sorted(
                    grouped.values(),
                    key=lambda item: (item["original"].casefold(), item["reviewStatus"], item["id"]),
                ),
                "aliases": sorted(
                    aliases,
                    key=lambda item: (item["text"].casefold(), str(item.get("type") or ""), item["personId"]),
                ),
                "methodNote": (
                    "Zapis źródłowy, transliteracja i polska forma są osobnymi warstwami. "
                    "Żadna z nich nie tworzy decyzji tożsamości."
                ),
            }
        )

    return sorted(profiles, key=lambda item: item["personKey"])
