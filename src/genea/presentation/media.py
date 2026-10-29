from __future__ import annotations

import mimetypes
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .common import json_object


LOCAL_ARTIFACT_PREFIX = "/local-artifacts/"
PREVIEWABLE_MEDIA_TYPES = {
    "application/json",
    "application/pdf",
    "image/bmp",
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/tiff",
    "image/webp",
    "text/markdown",
    "text/plain",
}


def artifact_media_type(path: Path, declared: str | None) -> str | None:
    normalized = (declared or "").split(";", 1)[0].strip().lower()
    if normalized in PREVIEWABLE_MEDIA_TYPES:
        return normalized
    guessed = (mimetypes.guess_type(path.name)[0] or "").lower()
    if guessed in PREVIEWABLE_MEDIA_TYPES:
        return guessed
    try:
        with path.open("rb") as artifact_file:
            signature = artifact_file.read(16)
    except OSError:
        return None
    if signature.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if signature.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if signature.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if signature.startswith(b"%PDF-"):
        return "application/pdf"
    if signature.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff"
    if signature.startswith(b"RIFF") and signature[8:12] == b"WEBP":
        return "image/webp"
    return None


def local_originals(
    root: Path,
    connection: sqlite3.Connection,
    allowed_source_ids: set[str],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    """Return safe presentation metadata and the localhost serving index."""

    root_resolved = root.resolve()
    originals: dict[str, list[dict[str, Any]]] = defaultdict(list)
    served: dict[str, dict[str, Any]] = {}
    seen_paths: dict[str, set[Path]] = defaultdict(set)
    presented_by_path: dict[str, dict[Path, dict[str, Any]]] = defaultdict(dict)
    rows = connection.execute(
        """SELECT id,source_item_id,parent_artifact_id,artifact_type,path,
                  media_type,metadata_json
           FROM artifacts
           ORDER BY source_item_id,
                    CASE WHEN artifact_type='RAW' THEN 0 ELSE 1 END,id"""
    )
    for row in rows:
        source_id = row["source_item_id"]
        if source_id not in allowed_source_ids:
            continue
        path = Path(row["path"]).expanduser()
        if not path.is_absolute():
            path = root / path
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if not resolved.is_relative_to(root_resolved) or not resolved.is_file():
            continue
        if resolved in seen_paths[source_id]:
            presented = presented_by_path[source_id][resolved]
            presented.setdefault("aliasIds", []).append(row["id"])
            served[row["id"]] = {
                "path": resolved,
                "mediaType": presented["mediaType"],
            }
            continue
        media_type = artifact_media_type(resolved, row["media_type"])
        if media_type not in PREVIEWABLE_MEDIA_TYPES:
            continue
        metadata = json_object(row["metadata_json"])
        display_label = metadata.get("display_label")
        original_filename = metadata.get("original_filename")
        format_label = media_type.split("/", 1)[1].upper().replace("JPEG", "JPG")
        label = (
            display_label
            if isinstance(display_label, str) and display_label.strip()
            else original_filename
            if isinstance(original_filename, str) and original_filename.strip()
            else (
                f"Oryginał · {format_label}"
                if row["artifact_type"] == "RAW"
                else f"Materiał pochodny · {format_label}"
            )
        )
        viewer = (
            "image"
            if media_type.startswith("image/")
            else "pdf"
            if media_type == "application/pdf"
            else "text"
        )
        artifact_id = row["id"]
        presentation_artifact = {
            "id": artifact_id,
            "type": row["artifact_type"],
            "mediaType": media_type,
            "viewer": viewer,
            "label": label,
            "url": f"{LOCAL_ARTIFACT_PREFIX}{quote(artifact_id, safe='')}",
        }
        if row["artifact_type"] != "RAW":
            presentation_artifact["derived"] = True
        if row["parent_artifact_id"]:
            presentation_artifact["parentArtifactId"] = row["parent_artifact_id"]
        originals[source_id].append(presentation_artifact)
        served[artifact_id] = {"path": resolved, "mediaType": media_type}
        seen_paths[source_id].add(resolved)
        presented_by_path[source_id][resolved] = presentation_artifact
    return dict(originals), served
