from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from ..workspace import research_root, stable_id
from .events import participant_presence


_PLACE_OVERLAY_FIELDS = {
    "placeId",
    "latitude",
    "longitude",
    "precision",
    "regionId",
    "regionLabel",
    "coordinateSourceUrl",
    "researchPriority",
    "researchCaseId",
}
_PLACE_IDENTITY_FIELDS = {"id", "label", "name", "shortLabel", "aliases", "names"}


def _place_key(value: str | None) -> str:
    """Legacy text normalizer retained for compatibility, not map resolution."""

    return re.sub(r"[^\wąćęłńóśźż]+", " ", (value or "").casefold()).strip()


def load_map_config(root: Path, tree: dict[str, Any] | None) -> dict[str, Any] | None:
    path = research_root(root) / "presentation" / "family-map.json"
    if not path.is_file():
        return None
    if tree is None:
        raise ValueError("Mapa prezentacyjna wymaga family-tree.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
        raise ValueError("research/presentation/family-map.json ma nieobsługiwany schemat")
    if payload.get("renderer") not in {"schematic-svg", "maplibre"}:
        raise ValueError("Mapa prezentacyjna wskazuje nieobsługiwany renderer")
    base_map = payload.get("baseMap")
    if payload.get("renderer") == "maplibre":
        if not isinstance(base_map, dict):
            raise ValueError("Renderer MapLibre wymaga konfiguracji baseMap")
        archive_url = base_map.get("archiveUrl")
        if (
            not isinstance(archive_url, str)
            or not archive_url.endswith(".pmtiles")
            or archive_url.startswith(("/", "http://", "https://"))
            or ".." in archive_url.split("/")
        ):
            raise ValueError("Mapa MapLibre wymaga lokalnej względnej ścieżki PMTiles")
    overlays = payload.get("places")
    lines = payload.get("lines")
    if not isinstance(overlays, list) or not isinstance(lines, list):
        raise ValueError("Mapa prezentacyjna wymaga tablic places i lines")
    place_ids = [item.get("placeId") for item in overlays if isinstance(item, dict)]
    line_ids = [item.get("id") for item in lines if isinstance(item, dict)]
    if (
        len(place_ids) != len(overlays)
        or any(not isinstance(place_id, str) or not place_id for place_id in place_ids)
        or len(place_ids) != len(set(place_ids))
    ):
        raise ValueError("Nakładki mapy wymagają unikalnych kanonicznych placeId")
    if len(line_ids) != len(lines) or len(line_ids) != len(set(line_ids)):
        raise ValueError("Linie mapy wymagają unikalnych identyfikatorów")
    for overlay in overlays:
        forbidden = sorted(set(overlay) & _PLACE_IDENTITY_FIELDS)
        unsupported = sorted(set(overlay) - _PLACE_OVERLAY_FIELDS)
        if forbidden:
            raise ValueError(
                f"Nakładka miejsca {overlay.get('placeId')} nie może definiować: "
                + ", ".join(forbidden)
            )
        if unsupported:
            raise ValueError(
                f"Nakładka miejsca {overlay.get('placeId')} ma nieobsługiwane pola: "
                + ", ".join(unsupported)
            )
        latitude = overlay.get("latitude")
        longitude = overlay.get("longitude")
        if (
            not isinstance(latitude, (int, float))
            or isinstance(latitude, bool)
            or not -90 <= latitude <= 90
        ):
            raise ValueError(
                f"Miejsce {overlay.get('placeId')} ma nieprawidłową szerokość"
            )
        if (
            not isinstance(longitude, (int, float))
            or isinstance(longitude, bool)
            or not -180 <= longitude <= 180
        ):
            raise ValueError(
                f"Miejsce {overlay.get('placeId')} ma nieprawidłową długość"
            )
    known_people = {person["id"] for person in tree.get("people", [])}
    for line in lines:
        if not isinstance(line.get("personIds"), list):
            raise ValueError(f"Linia {line.get('id')} wymaga tablicy personIds")
        missing = sorted(set(line["personIds"]) - known_people)
        if missing:
            raise ValueError(
                f"Linia mapy {line.get('id')} odwołuje się do nieznanych osób: "
                + ", ".join(missing)
            )
    return payload


def _event_year(value: str | None) -> int | None:
    match = re.search(r"\b(1[5-9]\d{2}|20\d{2})\b", value or "")
    return int(match.group(1)) if match else None


def _has_coordinates(place: dict[str, Any]) -> bool:
    latitude = place.get("latitude")
    longitude = place.get("longitude")
    return (
        isinstance(latitude, (int, float))
        and not isinstance(latitude, bool)
        and -90 <= latitude <= 90
        and isinstance(longitude, (int, float))
        and not isinstance(longitude, bool)
        and -180 <= longitude <= 180
    )


def build_map_facade(
    config: dict[str, Any] | None,
    tree: dict[str, Any] | None,
    places: list[dict[str, Any]],
    events: list[dict[str, Any]],
    occurrences: list[dict[str, Any]],
    *,
    excluded_occurrence_ids: set[str] | None = None,
) -> dict[str, Any] | None:
    if config is None or tree is None:
        return None

    canonical_places = {place["id"]: place for place in places}
    configured_place_ids = {overlay["placeId"] for overlay in config["places"]}
    mapped_places = {
        place_id: place
        for place_id, place in canonical_places.items()
        if place_id in configured_place_ids and _has_coordinates(place)
    }
    people = {person["id"]: person for person in tree.get("people", [])}
    occurrence_to_person: dict[str, str] = {}
    for person in people.values():
        for occurrence_id in person.get("occurrenceIds", []):
            previous = occurrence_to_person.setdefault(occurrence_id, person["id"])
            if previous != person["id"]:
                raise ValueError(
                    f"Wystąpienie {occurrence_id} przypisano do dwóch osób prezentacyjnych"
                )
    line_ids_by_person: dict[str, list[str]] = defaultdict(list)
    for line in config["lines"]:
        for person_id in line["personIds"]:
            line_ids_by_person[person_id].append(line["id"])
    occurrence_by_id = {occurrence["id"]: occurrence for occurrence in occurrences}
    excluded_occurrence_ids = excluded_occurrence_ids or set()
    observations: list[dict[str, Any]] = []
    source_observations: list[dict[str, Any]] = []
    unmapped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    seen_source: set[tuple[str, str, str]] = set()

    def place_admission(place_id: str | None) -> tuple[str | None, str | None]:
        admitted_place_id = place_id if place_id in canonical_places else None
        if place_id is None:
            return admitted_place_id, "missing_place_reference"
        if admitted_place_id is None:
            return admitted_place_id, "place_not_exported"
        if place_id not in mapped_places:
            return admitted_place_id, "place_missing_coordinates"
        return admitted_place_id, None

    def add_observation(
        *,
        person_id: str,
        event: dict[str, Any],
        participant: dict[str, Any],
        kind: str,
        place_id: str | None,
        place_text: str | None,
    ) -> None:
        admitted_place_id, reason = place_admission(place_id)
        identity = (
            person_id,
            event["id"],
            kind,
            str(participant.get("occurrenceId") or ""),
        )
        if identity in seen:
            return
        seen.add(identity)
        occurrence = occurrence_by_id.get(participant["occurrenceId"], {})
        date_from = event.get("dateFrom") or event.get("date")
        date_to = event.get("dateTo") or date_from
        item = {
            "id": stable_id("map_observation", *identity),
            "lineIds": line_ids_by_person.get(person_id, []),
            "personId": person_id,
            "personLabel": people[person_id].get("name", person_id),
            "personLifeSpanLabel": people[person_id].get("lifeSpanLabel", "?–?"),
            "placeId": admitted_place_id,
            "placeText": place_text if place_id is None or admitted_place_id else None,
            "kind": kind,
            "dateFrom": date_from,
            "dateTo": date_to,
            "yearFrom": _event_year(date_from),
            "yearTo": _event_year(date_to),
            "eventId": event["id"],
            "eventType": event.get("type"),
            "sourceId": event.get("sourceId") or occurrence.get("sourceId"),
            "occurrenceId": participant["occurrenceId"],
            "role": participant.get("role") or occurrence.get("role"),
            "presence": participant_presence(event.get("type"), participant),
            "eventStatus": event.get("status"),
            "readingCertainty": participant.get("statusText"),
        }
        if reason:
            item["reason"] = reason
            unmapped.append(item)
        else:
            observations.append(item)

    def add_source_observation(
        *,
        event: dict[str, Any],
        participant: dict[str, Any],
        kind: str,
        place_id: str | None,
        place_text: str | None,
    ) -> None:
        """Keep a source occurrence visible without asserting a person identity."""

        occurrence_id = participant["occurrenceId"]
        identity = (event["id"], kind, occurrence_id)
        if identity in seen_source:
            return
        seen_source.add(identity)
        occurrence = occurrence_by_id.get(occurrence_id, {})
        admitted_place_id, reason = place_admission(place_id)
        date_from = event.get("dateFrom") or event.get("date")
        date_to = event.get("dateTo") or date_from
        item = {
            "id": stable_id("map_source_observation", *identity),
            "placeId": admitted_place_id,
            "placeText": place_text if place_id is None or admitted_place_id else None,
            "kind": kind,
            "dateFrom": date_from,
            "dateTo": date_to,
            "yearFrom": _event_year(date_from),
            "yearTo": _event_year(date_to),
            "eventId": event["id"],
            "eventType": event.get("type"),
            "sourceId": event.get("sourceId") or occurrence.get("sourceId"),
            "occurrenceId": occurrence_id,
            "occurrenceLabel": occurrence.get("name")
            or occurrence.get("normalizedName"),
            "role": participant.get("role") or occurrence.get("role"),
            "presence": participant_presence(event.get("type"), participant),
            "eventStatus": event.get("status"),
            "readingCertainty": participant.get("statusText"),
        }
        if reason:
            item["reason"] = reason
            unmapped.append(item)
        else:
            source_observations.append(item)

    for event in events:
        for participant in event.get("participants", []):
            if participant.get("occurrenceId") in excluded_occurrence_ids:
                continue
            person_id = occurrence_to_person.get(participant.get("occurrenceId"))
            if not person_id:
                # Explicit source foreign keys may be projected as points, but
                # never acquire a family-tree identity or movement route here.
                if event.get("placeId") or event.get("place"):
                    add_source_observation(
                        event=event,
                        participant=participant,
                        kind="event",
                        place_id=event.get("placeId"),
                        place_text=event.get("place"),
                    )
                if participant.get("residencePlaceId") or participant.get(
                    "residence"
                ):
                    add_source_observation(
                        event=event,
                        participant=participant,
                        kind="residence",
                        place_id=participant.get("residencePlaceId"),
                        place_text=participant.get("residence"),
                    )
                continue
            # A person may have a valid, privacy-filtered map observation even
            # when no family line currently includes them. Such observations
            # stay hidden in line mode (empty lineIds) but remain available to
            # the explicit person-focus adapter.
            if event.get("placeId") or event.get("place"):
                add_observation(
                    person_id=person_id,
                    event=event,
                    participant=participant,
                    kind="event",
                    place_id=event.get("placeId"),
                    place_text=event.get("place"),
                )
            if participant.get("residencePlaceId") or participant.get("residence"):
                add_observation(
                    person_id=person_id,
                    event=event,
                    participant=participant,
                    kind="residence",
                    place_id=participant.get("residencePlaceId"),
                    place_text=participant.get("residence"),
                )
    years = [
        year
        for item in observations + source_observations + unmapped
        for year in (item.get("yearFrom"), item.get("yearTo"))
        if year is not None
    ]
    longitudes = [place["longitude"] for place in mapped_places.values()]
    latitudes = [place["latitude"] for place in mapped_places.values()]
    bounds = None
    if longitudes and latitudes:
        padding_x = max((max(longitudes) - min(longitudes)) * 0.12, 0.12)
        padding_y = max((max(latitudes) - min(latitudes)) * 0.12, 0.08)
        bounds = {
            "west": min(longitudes) - padding_x,
            "south": min(latitudes) - padding_y,
            "east": max(longitudes) + padding_x,
            "north": max(latitudes) + padding_y,
        }
    lines = []
    for line in config["lines"]:
        item = dict(line)
        item["observationCount"] = sum(line["id"] in obs["lineIds"] for obs in observations)
        lines.append(item)
    result = {
        "schemaVersion": config["schemaVersion"],
        "renderer": config["renderer"],
        "title": config.get("title"),
        "subtitle": config.get("subtitle"),
        "privacy": config.get("privacyPolicy", {}),
        "bounds": bounds,
        "yearRange": {"from": min(years) if years else None, "to": max(years) if years else None},
        "places": [dict(place) for place in mapped_places.values()],
        "lines": lines,
        "observations": sorted(
            observations,
            key=lambda item: (item.get("yearFrom") or 9999, item["personLabel"], item["id"]),
        ),
        "sourceObservations": sorted(
            source_observations,
            key=lambda item: (
                item.get("yearFrom") or 9999,
                item.get("sourceId") or "",
                item["eventId"],
                item["occurrenceId"],
                item["kind"],
                item["id"],
            ),
        ),
        "unmappedObservations": sorted(
            unmapped,
            key=lambda item: (
                item.get("yearFrom") or 9999,
                item.get("personLabel") or "",
                item["id"],
            ),
        ),
    }
    if config.get("baseMap"):
        result["baseMap"] = dict(config["baseMap"])
    return result
