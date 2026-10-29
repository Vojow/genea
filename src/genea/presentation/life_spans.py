from __future__ import annotations

import re
import unicodedata
from statistics import median
from typing import Any

from .common import event_group


_YEAR = re.compile(r"(?<!\d)(1[5-9]\d{2}|20\d{2})(?!\d)")
_AGE = re.compile(r"(?<!\d)(\d{1,3})(?!\d)")
_CERTAIN_STATUSES = {
    "confirmed",
    "documented",
    "human_reviewed",
    "resolved_same",
}

_POLISH_AGE_NUMBERS = {
    "zero": 0,
    "jeden": 1, "jedna": 1, "pierwszy": 1, "pierwsza": 1,
    "dwa": 2, "dwie": 2, "drugi": 2, "druga": 2,
    "trzy": 3, "trzeci": 3, "trzecia": 3,
    "cztery": 4, "czwarty": 4, "czwarta": 4,
    "piec": 5, "piaty": 5, "piata": 5,
    "szesc": 6, "szosty": 6, "szosta": 6,
    "siedem": 7, "siedm": 7, "siodmy": 7, "siodma": 7,
    "osiem": 8, "osm": 8, "osmy": 8, "osma": 8,
    "dziewiec": 9, "dziewiaty": 9, "dziewiata": 9,
    "dziesiec": 10, "dziesiaty": 10, "dziesiata": 10,
    "jedenascie": 11, "jedenasty": 11, "jedenasta": 11,
    "dwanascie": 12, "dwunasty": 12, "dwunasta": 12,
    "trzynascie": 13, "trzynasty": 13, "trzynasta": 13,
    "czternascie": 14, "czternasty": 14, "czternasta": 14,
    "pietnascie": 15, "pietnasty": 15, "pietnasta": 15,
    "szesnascie": 16, "szesnasty": 16, "szesnasta": 16,
    "siedemnascie": 17, "siedmnascie": 17, "siedemnasty": 17, "siedemnasta": 17,
    "osiemnascie": 18, "osiemnasty": 18, "osiemnasta": 18,
    "dziewietnascie": 19, "dziewietnasty": 19, "dziewietnasta": 19,
    "dwadziescia": 20, "dwudziesty": 20, "dwudziestu": 20,
    "trzydziesci": 30, "trzydziesty": 30, "trzydziestu": 30,
    "czterdziesci": 40, "czterdziesty": 40, "czterdziestu": 40,
    "piecdziesiat": 50, "piecdziesiaty": 50, "piecdziesieciu": 50,
    "szescdziesiat": 60, "szescdziesiaty": 60, "szescdziesieciu": 60,
    "siedemdziesiat": 70, "siedmdziesiat": 70, "siedemdziesiaty": 70, "siedemdziesieciu": 70,
    "osiemdziesiat": 80, "osiemdziesiaty": 80, "osiemdziesieciu": 80,
    "dziewiecdziesiat": 90, "dziewiecdziesiaty": 90, "dziewiecdziesieciu": 90,
    "sto": 100, "setny": 100,
}


def _year(value: Any) -> int | None:
    match = _YEAR.search(str(value or ""))
    return int(match.group(1)) if match else None


def _years(value: Any) -> list[int]:
    return [int(item) for item in _YEAR.findall(str(value or ""))]


def _ascii_words(value: Any) -> list[str]:
    raw = str(value or "").casefold().translate(str.maketrans({"ł": "l"}))
    normalized = unicodedata.normalize("NFKD", raw)
    plain = "".join(character for character in normalized if not unicodedata.combining(character))
    return re.findall(r"[a-z]+", plain)


def _age(value: Any) -> int | None:
    raw = str(value or "").casefold()
    if not raw:
        return None
    # This field occasionally contains a literal birth date instead of an age.
    # Never turn the day or month number from such a date into an age estimate.
    if any(token in raw for token in ("urodz", "ur.")) and _YEAR.search(raw):
        return None
    has_year_unit = any(token in raw for token in (" lat", "lata", "rok", "years", "year"))
    if not has_year_unit and any(token in raw for token in ("miesią", "tygod", "dzień", "dni")):
        return None
    if any(token in raw for token in ("noworod", "niemowl")):
        return 0
    range_match = re.fullmatch(r"\s*\[\s*(\d{1,3})\s*/\s*(\d{1,3})\s*\]\s*", raw)
    if range_match:
        boundaries = [int(range_match.group(1)), int(range_match.group(2))]
        if all(0 <= item <= 110 for item in boundaries):
            return round(median(boundaries))
    match = _AGE.search(raw)
    if match:
        parsed = int(match.group(1))
        return parsed if 0 <= parsed <= 110 else None

    numbers = [_POLISH_AGE_NUMBERS[word] for word in _ascii_words(raw) if word in _POLISH_AGE_NUMBERS]
    if not numbers:
        return None
    if len(numbers) == 1:
        parsed = numbers[0]
    elif numbers[0] >= 20 and all(item < 10 for item in numbers[1:]):
        parsed = sum(numbers)
    else:
        # A teen or another complete number word is already atomic. Refuse a
        # more complicated phrase instead of silently manufacturing an age.
        return None
    return parsed if 0 <= parsed <= 110 else None


def _fold(value: Any) -> str:
    return " ".join(str(value or "").casefold().replace("-", " ").split())


def _subject_role(event_type: Any, role: Any, *, boundary: str) -> bool:
    group = event_group(str(event_type or ""))
    folded = _fold(role)
    if boundary == "birth":
        return group == "birth-baptism" and any(
            token in folded
            for token in ("dziecko", "podmiot", "ochrzcz", "urodzon", "baptized", "newborn")
        )
    if group != "death-burial":
        return False
    # "ojciec zmarłego" names a relative of the deceased; matching the
    # substring "zmarł" would incorrectly turn the child's act into the
    # father's own death event.
    if re.search(
        r"\b(ojciec|matka|rodzic|żona|zona|mąż|maz|brat|siostra|father|mother|wife|husband|witness|świadek|swiadek|zgłasz|zglasz)\b",
        folded,
    ):
        return False
    return bool(
        re.search(
            r"\b(zmarły|zmarla|zmarła|zmarłe|deceased|pochowany|pochowana|pochowane|podmiot)\b",
            folded,
        )
    )


def _reported_life_state(participant: dict[str, Any]) -> str | None:
    """Return only a life state stated explicitly in the participant record.

    Merely being mentioned in a later act is not evidence that the person was
    alive then. In particular, a parent named in a child's death act must not
    turn the child's death year into the parent's life-span boundary.
    """

    typed = participant.get("vitalStatus")
    if isinstance(typed, dict):
        typed = typed.get("code")
    typed_state = _fold(typed)
    if typed_state in {"living", "deceased"}:
        return typed_state

    # Legacy fallback for records not yet backfilled with a typed assertion.
    # A role such as "ojciec zmarłego"
    # describes the child and must not mark the father as deceased. Some
    # imported acts append the participant's own state after a comma, e.g.
    # "ojciec zmarłego, zmarły"; only that explicit suffix is admissible.
    folded = _fold(participant.get("statusText"))
    role = _fold(participant.get("role"))
    explicit_role_state = re.search(
        r"(?:^|[,;(])\s*(zmarły|zmarla|zmarła|nieżyjący|niezyjacy|deceased|living|żyjący|żyjąca|zyjacy|zyjaca)\s*(?:$|[);])",
        role,
    )
    if explicit_role_state:
        folded = f"{folded} {explicit_role_state.group(1)}".strip()
    if any(
        token in folded
        for token in (
            "zmarł", "zmarla", "zmarła", "zmarły", "nieżyjąc", "nie zyjac",
            "deceased", "defunct", "mortu", "умерш", "покойн",
        )
    ):
        return "deceased"
    if any(
        token in folded
        for token in (
            "żyjący", "żyjąca", "zyjacy", "zyjaca", "żyje", "zyje",
            "living", "vivent", "vivus", "viva", "жив",
        )
    ):
        return "living"
    return None


def _point(year: int, qualifier: str, basis: str, event_ids: list[str], source_ids: list[str]) -> dict[str, Any]:
    return {
        "year": year,
        "qualifier": qualifier,
        "basis": basis,
        "eventIds": sorted(set(filter(None, event_ids))),
        "sourceIds": sorted(set(filter(None, source_ids))),
    }


def _point_label(point: dict[str, Any] | None) -> str:
    if not point:
        return "?"
    year = point["year"]
    qualifier = point["qualifier"]
    if qualifier == "exact":
        return str(year)
    if qualifier == "about":
        return f"ok. {year}?"
    if qualifier == "before":
        return f"przed {year}?"
    if qualifier == "after":
        return f"po {year}?"
    return f"{year}?"


def life_span_for_occurrences(
    occurrence_ids: set[str],
    events: list[dict[str, Any]],
    *,
    facts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    exact_birth: list[tuple[int, bool, str, str, str]] = []
    exact_death: list[tuple[int, bool, str, str, str]] = []
    age_births: list[tuple[int, str, str]] = []
    reported_alive: list[tuple[int, str, str]] = []
    reported_deceased: list[tuple[int, str, str]] = []

    for event in events:
        event_year = _year(event.get("dateFrom") or event.get("date") or event.get("dateTo"))
        if event_year is None:
            continue
        event_id = str(event.get("id") or "")
        source_id = str(event.get("sourceId") or "")
        certain = _fold(event.get("status")) in _CERTAIN_STATUSES
        for participant in event.get("participants", []):
            if participant.get("occurrenceId") not in occurrence_ids:
                continue
            if _subject_role(event.get("type"), participant.get("role"), boundary="birth"):
                exact_birth.append((event_year, certain, event_id, source_id, "birth_event"))
            if _subject_role(event.get("type"), participant.get("role"), boundary="death"):
                exact_death.append((event_year, certain, event_id, source_id, "death_event"))
            life_state = _reported_life_state(participant)
            if life_state == "living":
                reported_alive.append((event_year, event_id, source_id))
            elif life_state == "deceased":
                reported_deceased.append((event_year, event_id, source_id))
            participant_age = _age(participant.get("age"))
            if participant_age is not None:
                age_births.append((event_year - participant_age, event_id, source_id))

    for fact in facts or []:
        fact_years = _years(fact.get("value"))
        if not fact_years:
            continue
        label = _fold(fact.get("label"))
        value = _fold(fact.get("value"))
        explicitly_approximate = any(token in value for token in ("?", "około", "ok.", "circa", "przed", "po "))
        exact_as_written = not explicitly_approximate
        source_id = str(fact.get("sourceId") or "")
        if any(token in label for token in ("życie", "zycie", "lata życia", "lata zycia", "life span")) and len(fact_years) >= 2:
            exact_birth.append((fact_years[0], exact_as_written, "", source_id, "life_range_fact"))
            exact_death.append((fact_years[-1], exact_as_written, "", source_id, "life_range_fact"))
            continue
        fact_year = fact_years[0]
        if any(token in label for token in ("urod", "chrzest")):
            exact_birth.append((fact_year, exact_as_written, "", source_id, "birth_fact"))
        if any(token in label for token in ("zgon", "śmier", "zmarl", "zmarł")):
            exact_death.append((fact_year, exact_as_written, "", source_id, "death_fact"))

    def event_boundary(values: list[tuple[int, bool, str, str, str]]) -> dict[str, Any] | None:
        if not values:
            return None
        preferred = sorted(values, key=lambda item: (not item[1], item[0]))[0]
        same_year = [item for item in values if item[0] == preferred[0]]
        return _point(
            preferred[0],
            "exact" if preferred[1] else "about",
            preferred[4],
            [item[2] for item in same_year],
            [item[3] for item in same_year],
        )

    birth = event_boundary(exact_birth)
    death = event_boundary(exact_death)
    if birth is None and age_births:
        estimated_year = round(median(item[0] for item in age_births))
        birth = _point(
            estimated_year,
            "about",
            "age_in_source",
            [item[1] for item in age_births],
            [item[2] for item in age_births],
        )
    if death is None and reported_deceased:
        first_deceased = min(reported_deceased, key=lambda item: item[0])
        death = _point(
            first_deceased[0], "before", "explicit_deceased_in_source",
            [first_deceased[1]], [first_deceased[2]],
        )
    elif death is None and reported_alive:
        last_alive = max(reported_alive, key=lambda item: item[0])
        death = _point(
            last_alive[0], "after", "explicit_living_in_source",
            [last_alive[1]], [last_alive[2]],
        )

    missing = []
    if not exact_birth:
        missing.append("birth")
    if not exact_death:
        missing.append("death")
    return {
        "birth": birth,
        "death": death,
        "label": f"{_point_label(birth)}–{_point_label(death)}",
        "status": "complete" if not missing else "partial" if birth or death else "missing",
        "missing": missing,
        "estimated": any(
            point and point.get("qualifier") != "exact" for point in (birth, death)
        ),
        "methodNote": (
            "Znak ? oznacza granicę wyliczoną z wieku albo z jawnej informacji "
            "o życiu lub zgonie w akcie, a nie potwierdzoną datę urodzenia lub zgonu."
        ),
    }


def enrich_life_spans(
    people: list[dict[str, Any]],
    tree: dict[str, Any] | None,
    occurrences: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> None:
    occurrence_ids_by_person: dict[str, set[str]] = {}
    occurrence_person: dict[str, str] = {}
    for occurrence in occurrences:
        person_id = occurrence.get("personId")
        occurrence_id = occurrence.get("id")
        if not person_id or not occurrence_id:
            continue
        occurrence_ids_by_person.setdefault(person_id, set()).add(occurrence_id)
        occurrence_person[occurrence_id] = person_id

    for person in people:
        ids = occurrence_ids_by_person.get(str(person.get("id")), set())
        person["lifeSpan"] = life_span_for_occurrences(ids, events)
        person["lifeSpanLabel"] = person["lifeSpan"]["label"]

    if not tree:
        return
    for person in tree.get("people", []):
        if person.get("privacy") == "masked_living":
            person["lifeSpan"] = None
            person["lifeSpanLabel"] = "Dane chronione"
            continue
        occurrence_ids = set(person.get("occurrenceIds", []))
        canonical_ids = {
            occurrence_person[item]
            for item in occurrence_ids
            if item in occurrence_person
        }
        explicit = person.get("canonicalPersonId")
        if explicit:
            canonical_ids.add(explicit)
            occurrence_ids.update(occurrence_ids_by_person.get(explicit, set()))
        if len(canonical_ids) > 1:
            years = sorted(
                year
                for event in events
                if any(
                    participant.get("occurrenceId") in occurrence_ids
                    for participant in event.get("participants", [])
                )
                if (year := _year(event.get("dateFrom") or event.get("date") or event.get("dateTo"))) is not None
            )
            person["lifeSpan"] = None
            person["lifeSpanLabel"] = (
                f"materiał {years[0]}?–{years[-1]}?" if years else "brak wspólnych lat życia"
            )
            continue
        span = life_span_for_occurrences(
            occurrence_ids,
            events,
            facts=person.get("facts", []),
        )
        person["lifeSpan"] = span
        person["lifeSpanLabel"] = span["label"]


__all__ = ["enrich_life_spans", "life_span_for_occurrences"]
