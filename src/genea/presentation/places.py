from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Any

from .privacy import PrivacyContext


_MAP_OVERLAY_FIELDS = (
    "latitude",
    "longitude",
    "precision",
    "regionId",
    "regionLabel",
    "coordinateSourceUrl",
    "researchPriority",
    "researchCaseId",
)


def enrich_places_with_map_overlay(
    places: list[dict[str, Any]],
    overlays: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach geo metadata without creating a second place identity registry."""

    overlays_by_place_id = {item["placeId"]: item for item in overlays}
    result = []
    for place in places:
        item = dict(place)
        overlay = overlays_by_place_id.get(place["id"])
        if overlay:
            item.update(
                {
                    field: overlay[field]
                    for field in _MAP_OVERLAY_FIELDS
                    if field in overlay
                }
            )
        result.append(item)
    return result


def project_places(
    connection: sqlite3.Connection,
    privacy: PrivacyContext,
    *,
    allowed_source_ids: set[str],
) -> list[dict[str, Any]]:
    """Export every admitted place, not only places configured for the map.

    The map remains a curated geospatial facade.  Routed catalogue references
    need the wider place registry so a valid historical place does not become
    a misleading "missing object" merely because it has no map coordinates.
    """

    aliases: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in connection.execute(
        """SELECT id,place_id,name_text,normalized_name,language,date_from,date_to,
                  source_item_id
             FROM place_names
            ORDER BY place_id,date_from,name_text,id"""
    ):
        if row["source_item_id"] and row["source_item_id"] not in allowed_source_ids:
            continue
        aliases[row["place_id"]].append(
            {
                "id": row["id"],
                "text": row["name_text"],
                "normalized": row["normalized_name"],
                "language": row["language"],
                "dateFrom": row["date_from"],
                "dateTo": row["date_to"],
                "sourceId": row["source_item_id"],
            }
        )

    places: list[dict[str, Any]] = []
    for row in connection.execute(
        """SELECT id,preferred_name,place_type,latitude,longitude
             FROM places ORDER BY preferred_name,id"""
    ):
        if privacy.blocked("place", row["id"]):
            continue
        names = aliases.get(row["id"], [])
        places.append(
            {
                "id": row["id"],
                "label": row["preferred_name"],
                "name": row["preferred_name"],
                "type": row["place_type"],
                "latitude": row["latitude"],
                "longitude": row["longitude"],
                "aliases": [item["text"] for item in names],
                "names": names,
            }
        )
    return places
