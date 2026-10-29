from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


REQUEST_SCHEMA = "genea.reading-snippet-request/v1"
PACKAGE_SCHEMA = "genea.reading-snippet-package/v1"
PIPELINE_VERSION = "genea-reading-snippets-v1"
REGION_NAMES = ("field", "context", "fullAct")
FIELD_KINDS = {
    "person-name",
    "place",
    "date",
    "age",
    "occupation",
    "relationship",
    "number",
    "other",
}
_PACKAGE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_BACKING_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class ReadingSnippetError(ValueError):
    """A safe validation or integrity error for a derivative snippet package."""


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReadingSnippetError(f"Pole {label} musi być niepustym tekstem")
    return value.strip()


def _strict_keys(value: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ReadingSnippetError(
            f"{label} zawiera nieobsługiwane pola: {', '.join(unknown)}"
        )


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReadingSnippetError(f"Nie można odczytać {label}: {error}") from error
    if not isinstance(payload, dict):
        raise ReadingSnippetError(f"{label} musi być obiektem JSON")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise ReadingSnippetError(f"Nie można obliczyć SHA-256 pliku {path}: {error}") from error
    return digest.hexdigest()


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _run(command: list[str]) -> str:
    try:
        return subprocess.run(
            command,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as error:
        output = getattr(error, "stdout", None)
        detail = str(output).strip() if output else str(error)
        raise ReadingSnippetError(f"ImageMagick nie wykonał operacji: {detail}") from error


def _magick() -> tuple[str, str]:
    executable = shutil.which("magick")
    if not executable:
        raise ReadingSnippetError("Brak programu ImageMagick 'magick'")
    version_output = _run([executable, "-version"])
    version = version_output.splitlines()[0] if version_output else "unknown"
    return executable, version


def _dimensions(magick: str, path: Path) -> dict[str, int]:
    output = _run([magick, "identify", "-format", "%w %h", str(path)])
    try:
        width, height = (int(part) for part in output.split())
    except (TypeError, ValueError) as error:
        raise ReadingSnippetError(f"Nie można ustalić wymiarów obrazu {path}") from error
    if width <= 0 or height <= 0:
        raise ReadingSnippetError(f"Obraz {path} ma niepoprawne wymiary")
    return {"width": width, "height": height}


def _resolve_source(root: Path, source_path: str) -> Path:
    relative = Path(source_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ReadingSnippetError("source.path musi być bezpieczną ścieżką względem workspace")
    resolved_root = root.resolve()
    resolved = (resolved_root / relative).resolve()
    if resolved_root != resolved and resolved_root not in resolved.parents:
        raise ReadingSnippetError("source.path wskazuje poza workspace")
    if not resolved.is_file():
        raise ReadingSnippetError(f"Nie znaleziono obrazu źródłowego: {source_path}")
    return resolved


def _resolve_output(root: Path, output_dir: Path) -> Path:
    root_input = root.absolute()
    resolved_root = root_input.resolve()
    output = output_dir if output_dir.is_absolute() else root_input / output_dir
    resolved = output.resolve()
    try:
        relative_parts = output.relative_to(root_input).parts
        symlink_candidate = root_input
    except ValueError:
        if resolved == resolved_root or resolved_root in resolved.parents:
            relative_parts = resolved.relative_to(resolved_root).parts
            symlink_candidate = resolved_root
        else:
            relative_parts = output.parts[1:]
            symlink_candidate = Path(output.anchor)
    for part in relative_parts:
        symlink_candidate /= part
        if symlink_candidate.is_symlink():
            raise ReadingSnippetError("OUTPUT nie może prowadzić przez dowiązanie symboliczne")
    protected_roots = {
        "RAW": (resolved_root / "research/archive/originals").resolve(),
        "sources": (resolved_root / "sources").resolve(),
    }
    for label, protected in protected_roots.items():
        if resolved == protected or protected in resolved.parents:
            raise ReadingSnippetError(
                f"OUTPUT nie może znajdować się w chronionym katalogu {label}"
            )
    return resolved


def _bbox(value: Any, label: str) -> dict[str, int]:
    if not isinstance(value, dict):
        raise ReadingSnippetError(f"{label} musi być obiektem x/y/width/height")
    _strict_keys(value, {"x", "y", "width", "height"}, label)
    result: dict[str, int] = {}
    for key in ("x", "y", "width", "height"):
        number = value.get(key)
        if not isinstance(number, int) or isinstance(number, bool):
            raise ReadingSnippetError(f"{label}.{key} musi być liczbą całkowitą")
        if key in {"width", "height"} and number <= 0:
            raise ReadingSnippetError(f"{label}.{key} musi być dodatnie")
        if key in {"x", "y"} and number < 0:
            raise ReadingSnippetError(f"{label}.{key} nie może być ujemne")
        result[key] = number
    return result


def _contains(outer: dict[str, int], inner: dict[str, int]) -> bool:
    return (
        inner["x"] >= outer["x"]
        and inner["y"] >= outer["y"]
        and inner["x"] + inner["width"] <= outer["x"] + outer["width"]
        and inner["y"] + inner["height"] <= outer["y"] + outer["height"]
    )


def _pipeline_field_kind(field_path: str) -> str:
    normalized = field_path.lower()
    if normalized.endswith(".date") or ".date." in normalized:
        return "date"
    if normalized.endswith(".age") or ".age." in normalized:
        return "age"
    if any(token in normalized for token in ("place", "residence", "birthplace")):
        return "place"
    if "occupation" in normalized or "profession" in normalized:
        return "occupation"
    if any(token in normalized for token in ("parent", "relationship", "spouse")):
        return "relationship"
    if normalized.endswith(".name") or "witness" in normalized:
        return "person-name"
    if any(token in normalized for token in ("number", "ordinal")):
        return "number"
    return "other"


def _pipeline_bbox(value: Any, field_path: str) -> dict[str, int]:
    if not isinstance(value, dict):
        raise ReadingSnippetError(
            f"Pole {field_path} nie ma bbox; pełna karta wymaga wycinka każdego pola"
        )
    result: dict[str, int] = {}
    for key in ("x", "y", "width", "height"):
        number = value.get(key)
        if (
            not isinstance(number, (int, float))
            or isinstance(number, bool)
            or int(number) != number
        ):
            raise ReadingSnippetError(
                f"bbox pola {field_path}.{key} musi mieć całkowitą współrzędną pikselową"
            )
        result[key] = int(number)
    return _bbox(result, f"bbox pola {field_path}")


def _expanded_context(
    field: dict[str, int], *, source_width: int, source_height: int
) -> dict[str, int]:
    horizontal = max(48, field["width"] // 2)
    vertical = max(32, field["height"])
    x = max(0, field["x"] - horizontal)
    y = max(0, field["y"] - vertical)
    right = min(source_width, field["x"] + field["width"] + horizontal)
    bottom = min(source_height, field["y"] + field["height"] + vertical)
    return {"x": x, "y": y, "width": right - x, "height": bottom - y}


def reading_snippet_request_from_pipeline(
    root: Path,
    manifest_path: Path,
    source_path: Path,
    *,
    source_locator: str,
    privacy_classification: str,
) -> dict[str, Any]:
    """Create a value-free crop request for every main pipeline field.

    AI supplies the semantic field inventory and exact field bboxes.  This
    deterministic step validates that no proposed field is silently omitted
    and derives only the surrounding context/full-act regions.
    """

    resolved_root = root.resolve()
    payload = _load_json(manifest_path, "manifestu reading pipeline")
    manifest_privacy = payload.get("privacyClassification")
    if manifest_privacy is not None and manifest_privacy != privacy_classification:
        raise ReadingSnippetError(
            "Klasyfikacja prywatności manifestu nie zgadza się z jawnym potwierdzeniem"
        )
    if privacy_classification != "public_historical":
        raise ReadingSnippetError(
            "Builder from-pipeline obsługuje wyłącznie jawnie potwierdzone public_historical"
        )
    source_item_id = _required_text(payload.get("sourceItemId"), "sourceItemId")
    input_sha256 = _required_text(payload.get("inputSha256"), "inputSha256").lower()
    if not _SHA256.fullmatch(input_sha256):
        raise ReadingSnippetError("inputSha256 manifestu musi być SHA-256")
    source = source_path if source_path.is_absolute() else resolved_root / source_path
    try:
        relative_source = source.resolve().relative_to(resolved_root)
    except ValueError as error:
        raise ReadingSnippetError("Obraz pipeline musi znajdować się w workspace") from error
    source = _resolve_source(resolved_root, str(relative_source))
    if _sha256(source) != input_sha256:
        raise ReadingSnippetError("Obraz pipeline nie zgadza się z inputSha256 manifestu")
    magick, _ = _magick()
    dimensions = _dimensions(magick, source)
    raw_fields = payload.get("fields")
    if not isinstance(raw_fields, list) or not raw_fields:
        raise ReadingSnippetError("Manifest pipeline nie zawiera pól do wycięcia")

    fields: list[dict[str, Any]] = []
    for index, raw_field in enumerate(raw_fields):
        if not isinstance(raw_field, dict):
            raise ReadingSnippetError(f"fields[{index}] manifestu musi być obiektem")
        field_path = _required_text(raw_field.get("path"), f"fields[{index}].path")
        source_segment_id = _required_text(
            raw_field.get("sourceSegment"), f"pole {field_path}.sourceSegment"
        )
        field_region = _pipeline_bbox(raw_field.get("bbox"), field_path)
        image_bounds = {
            "x": 0,
            "y": 0,
            "width": dimensions["width"],
            "height": dimensions["height"],
        }
        if not _contains(image_bounds, field_region):
            raise ReadingSnippetError(f"bbox pola {field_path} wychodzi poza obraz")
        fields.append(
            {
                "path": field_path,
                "kind": _pipeline_field_kind(field_path),
                "locator": source_locator,
                "sourceSegmentId": source_segment_id,
                "regions": {
                    "field": field_region,
                    "context": _expanded_context(
                        field_region,
                        source_width=dimensions["width"],
                        source_height=dimensions["height"],
                    ),
                    "fullAct": image_bounds,
                },
            }
        )

    manifest_fingerprint = _canonical_hash(payload)[:16]
    request = {
        "schema": REQUEST_SCHEMA,
        "packageId": f"{source_item_id}-{manifest_fingerprint}"[:128],
        "privacyClassification": privacy_classification,
        "source": {
            "id": source_item_id,
            "path": str(relative_source),
            "sha256": input_sha256,
            "locator": _required_text(source_locator, "sourceLocator"),
        },
        "fields": fields,
    }
    return _normalize_request(
        request,
        source_width=dimensions["width"],
        source_height=dimensions["height"],
    )


def build_reading_snippet_package_from_pipeline(
    root: Path,
    manifest_path: Path,
    source_path: Path,
    output_dir: Path,
    *,
    source_locator: str,
    privacy_classification: str,
) -> dict[str, Any]:
    """Build all field crops from a pipeline manifest without storing values in the package."""

    request = reading_snippet_request_from_pipeline(
        root,
        manifest_path,
        source_path,
        source_locator=source_locator,
        privacy_classification=privacy_classification,
    )
    temporary_dir = Path(tempfile.mkdtemp(prefix="genea-reading-snippet-request-"))
    try:
        request_path = temporary_dir / "request.json"
        request_path.write_text(
            json.dumps(request, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return build_reading_snippet_package(root, request_path, output_dir)
    finally:
        shutil.rmtree(temporary_dir, ignore_errors=True)


def _normalize_request(
    payload: dict[str, Any], *, source_width: int, source_height: int
) -> dict[str, Any]:
    _strict_keys(
        payload,
        {"schema", "packageId", "privacyClassification", "source", "fields"},
        "request",
    )
    if payload.get("schema") != REQUEST_SCHEMA:
        raise ReadingSnippetError(f"Obsługiwany schema requestu to {REQUEST_SCHEMA}")
    package_id = _required_text(payload.get("packageId"), "packageId")
    if not _PACKAGE_ID.fullmatch(package_id):
        raise ReadingSnippetError("packageId ma niepoprawny format")
    privacy_classification = _required_text(
        payload.get("privacyClassification"), "privacyClassification"
    )
    if privacy_classification != "public_historical":
        raise ReadingSnippetError(
            "Pakiety snippetów obsługują obecnie wyłącznie public_historical; "
            "materiał prywatny wymaga osobnego szyfrowanego workflow"
        )

    raw_source = payload.get("source")
    if not isinstance(raw_source, dict):
        raise ReadingSnippetError("source musi być obiektem")
    _strict_keys(raw_source, {"id", "path", "sha256", "locator"}, "source")
    source_sha256 = _required_text(raw_source.get("sha256"), "source.sha256").lower()
    if not _SHA256.fullmatch(source_sha256):
        raise ReadingSnippetError("source.sha256 musi być 64-znakowym SHA-256")
    source = {
        "id": _required_text(raw_source.get("id"), "source.id"),
        "path": _required_text(raw_source.get("path"), "source.path"),
        "sha256": source_sha256,
        "locator": _required_text(raw_source.get("locator"), "source.locator"),
    }

    raw_fields = payload.get("fields")
    if not isinstance(raw_fields, list) or not raw_fields:
        raise ReadingSnippetError("fields musi być niepustą tablicą")
    fields: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    image_bounds = {"x": 0, "y": 0, "width": source_width, "height": source_height}
    for index, raw_field in enumerate(raw_fields):
        label = f"fields[{index}]"
        if not isinstance(raw_field, dict):
            raise ReadingSnippetError(f"{label} musi być obiektem")
        _strict_keys(
            raw_field,
            {"path", "kind", "locator", "sourceSegmentId", "regions"},
            label,
        )
        field_path = _required_text(raw_field.get("path"), f"{label}.path")
        if field_path in seen_paths:
            raise ReadingSnippetError(f"Powtórzona ścieżka pola: {field_path}")
        seen_paths.add(field_path)
        kind = _required_text(raw_field.get("kind"), f"{label}.kind")
        if kind not in FIELD_KINDS:
            raise ReadingSnippetError(f"Nieobsługiwany rodzaj pola {field_path}: {kind}")
        raw_regions = raw_field.get("regions")
        if not isinstance(raw_regions, dict):
            raise ReadingSnippetError(f"{label}.regions musi być obiektem")
        _strict_keys(raw_regions, set(REGION_NAMES), f"{label}.regions")
        if set(raw_regions) != set(REGION_NAMES):
            missing = sorted(set(REGION_NAMES) - set(raw_regions))
            raise ReadingSnippetError(
                f"Pole {field_path} nie ma wszystkich regionów: {', '.join(missing)}"
            )
        regions = {
            name: _bbox(raw_regions[name], f"{label}.regions.{name}")
            for name in REGION_NAMES
        }
        for name, region in regions.items():
            if not _contains(image_bounds, region):
                raise ReadingSnippetError(
                    f"Region {name} pola {field_path} wychodzi poza obraz źródłowy"
                )
        if not _contains(regions["context"], regions["field"]):
            raise ReadingSnippetError(f"Region field pola {field_path} nie mieści się w context")
        if not _contains(regions["fullAct"], regions["context"]):
            raise ReadingSnippetError(
                f"Region context pola {field_path} nie mieści się w fullAct"
            )
        field = {
            "path": field_path,
            "kind": kind,
            "locator": _required_text(raw_field.get("locator"), f"{label}.locator"),
            "regions": regions,
        }
        source_segment_id = _required_text(
            raw_field.get("sourceSegmentId"), f"{label}.sourceSegmentId"
        )
        if not _BACKING_ID.fullmatch(source_segment_id):
            raise ReadingSnippetError(
                f"{label}.sourceSegmentId musi być stabilnym nieprzezroczystym ID"
            )
        field["sourceSegmentId"] = source_segment_id
        fields.append(field)
    return {
        "schema": REQUEST_SCHEMA,
        "packageId": package_id,
        "privacyClassification": privacy_classification,
        "source": source,
        "fields": sorted(fields, key=lambda item: item["path"]),
    }


def _slug(field_path: str) -> str:
    readable = re.sub(r"[^a-z0-9]+", "-", field_path.lower()).strip("-")[:48] or "field"
    return f"{readable}-{hashlib.sha256(field_path.encode('utf-8')).hexdigest()[:10]}"


def _geometry(region: dict[str, int]) -> str:
    return f"{region['width']}x{region['height']}+{region['x']}+{region['y']}"


def _display_path(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())


def _expected_asset_path(field_path: str, region_name: str) -> str:
    return f"fields/{_slug(field_path)}/{region_name}.png"


def _configuration(
    request: dict[str, Any], magick_version: str
) -> dict[str, Any]:
    return {
        "pipelineVersion": PIPELINE_VERSION,
        "imageMagickVersion": magick_version,
        "request": request,
        "operations": ["crop", "+repage"],
        "outputFormat": "PNG",
        "imageAdjustments": [],
    }


def _provenance_boundary() -> dict[str, Any]:
    return {
        "purpose": "targeted_field_verification",
        "structuredExpectedValuesExcluded": True,
        "blindIsolationProvided": False,
        "eachCropParent": "source",
        "rawWrites": False,
        "canonicalDatabaseWrites": False,
        "excludedOperations": [
            "OCR",
            "HTR",
            "contrast",
            "sharpening",
            "retouching",
            "generative_fill",
            "identity_decision",
            "relationship_decision",
        ],
    }


def _verify_package(
    root: Path,
    output: Path,
    *,
    expected_fingerprint: str | None = None,
    magick: str | None = None,
) -> dict[str, Any]:
    if not output.is_dir():
        raise ReadingSnippetError(f"Nie znaleziono katalogu pakietu: {output}")
    manifest_path = output / "manifest.json"
    if not manifest_path.is_file():
        raise ReadingSnippetError(f"OUTPUT istnieje bez manifest.json: {output}")
    symlinks = [path for path in output.rglob("*") if path.is_symlink()]
    if symlinks:
        raise ReadingSnippetError("Pakiet nie może zawierać dowiązań symbolicznych")
    manifest = _load_json(manifest_path, "manifestu pakietu snippetów")
    if manifest.get("schema") != PACKAGE_SCHEMA:
        raise ReadingSnippetError("OUTPUT nie jest pakietem reading-snippet-package/v1")
    configuration = manifest.get("configuration")
    if not isinstance(configuration, dict):
        raise ReadingSnippetError("Manifest nie zawiera konfiguracji pakietu")
    _strict_keys(
        configuration,
        {
            "pipelineVersion",
            "imageMagickVersion",
            "request",
            "operations",
            "outputFormat",
            "imageAdjustments",
        },
        "configuration",
    )
    if (
        configuration.get("pipelineVersion") != PIPELINE_VERSION
        or not isinstance(configuration.get("imageMagickVersion"), str)
        or not configuration["imageMagickVersion"].strip()
        or configuration.get("operations") != ["crop", "+repage"]
        or configuration.get("outputFormat") != "PNG"
        or configuration.get("imageAdjustments") != []
    ):
        raise ReadingSnippetError("Konfiguracja pakietu ma nieobsługiwane operacje")
    fingerprint = _canonical_hash(configuration)
    if manifest.get("configFingerprint") != fingerprint:
        raise ReadingSnippetError("Fingerprint konfiguracji pakietu jest niepoprawny")
    if expected_fingerprint is not None and fingerprint != expected_fingerprint:
        raise ReadingSnippetError("OUTPUT należy do innej konfiguracji snippetów")

    request = configuration.get("request")
    if not isinstance(request, dict):
        raise ReadingSnippetError("Konfiguracja pakietu nie zawiera requestu")
    source_data = request.get("source")
    if not isinstance(source_data, dict):
        raise ReadingSnippetError("Konfiguracja pakietu nie zawiera źródła")
    source = _resolve_source(root, _required_text(source_data.get("path"), "source.path"))
    actual_source_sha = _sha256(source)
    if actual_source_sha != source_data.get("sha256"):
        raise ReadingSnippetError("Obraz źródłowy zmienił się od utworzenia pakietu")

    magick_path = magick or _magick()[0]
    source_dimensions = _dimensions(magick_path, source)
    normalized_request = _normalize_request(
        request,
        source_width=source_dimensions["width"],
        source_height=source_dimensions["height"],
    )
    if normalized_request != request:
        raise ReadingSnippetError("Request zapisany w pakiecie nie jest kanoniczny")

    expected_source_record = {
        **normalized_request["source"],
        **source_dimensions,
        "hashVerifiedBeforeBuild": True,
        "hashVerifiedAfterBuild": True,
    }
    if manifest.get("packageId") != normalized_request["packageId"]:
        raise ReadingSnippetError("Manifest ma niezgodny packageId")
    if manifest.get("privacyClassification") != normalized_request["privacyClassification"]:
        raise ReadingSnippetError("Manifest ma niezgodną klasyfikację prywatności")
    if manifest.get("pipelineVersion") != PIPELINE_VERSION:
        raise ReadingSnippetError("Manifest ma nieobsługiwaną wersję pipeline")
    if manifest.get("source") != expected_source_record:
        raise ReadingSnippetError("Manifest nie zachował pełnego rekordu źródła")
    if manifest.get("coordinateSpace") != {
        "unit": "pixel",
        "origin": "top-left",
        "rectangle": "xywh",
        "sourceWidth": source_dimensions["width"],
        "sourceHeight": source_dimensions["height"],
    }:
        raise ReadingSnippetError("Manifest ma niepoprawną przestrzeń współrzędnych")
    if manifest.get("provenanceBoundary") != _provenance_boundary():
        raise ReadingSnippetError("Manifest ma niepoprawną granicę provenance")
    if manifest.get("workspaceRelativeOutput") != _display_path(root, output):
        raise ReadingSnippetError("Manifest nie odpowiada bieżącej lokalizacji OUTPUT")
    if manifest.get("counts") != {
        "fields": len(normalized_request["fields"]),
        "derivedImages": len(normalized_request["fields"]) * len(REGION_NAMES),
    }:
        raise ReadingSnippetError("Manifest ma niepoprawne liczniki")
    if manifest.get("runStatus") != "built":
        raise ReadingSnippetError("Manifest ma niepoprawny zapisany status budowy")

    fields = manifest.get("fields")
    if not isinstance(fields, list) or len(fields) != len(normalized_request["fields"]):
        raise ReadingSnippetError("Manifest ma niezgodną listę pól")
    manifest_by_path: dict[str, dict[str, Any]] = {}
    for field in fields:
        if not isinstance(field, dict) or not isinstance(field.get("path"), str):
            raise ReadingSnippetError("Manifest zawiera niepoprawny rekord pola")
        if field["path"] in manifest_by_path:
            raise ReadingSnippetError("Manifest zawiera powtórzony rekord pola")
        manifest_by_path[field["path"]] = field

    expected_files = {"manifest.json"}
    for configured_field in normalized_request["fields"]:
        field_path = configured_field["path"]
        field = manifest_by_path.get(field_path)
        if field is None or field.get("kind") != configured_field["kind"]:
            raise ReadingSnippetError(f"Manifest nie zachował pola {field_path}")
        if field.get("locator") != configured_field["locator"]:
            raise ReadingSnippetError(f"Manifest nie zachował lokatora pola {field_path}")
        if field.get("sourceSegmentId") != configured_field.get("sourceSegmentId"):
            raise ReadingSnippetError(f"Manifest nie zachował segmentu pola {field_path}")
        assets = field.get("regions")
        if not isinstance(assets, dict) or set(assets) != set(REGION_NAMES):
            raise ReadingSnippetError(f"Manifest ma niepoprawne regiony pola {field_path}")
        for region_name in REGION_NAMES:
            asset = assets[region_name]
            if not isinstance(asset, dict):
                raise ReadingSnippetError(f"Manifest ma niepoprawny asset {field_path}/{region_name}")
            expected_bbox = configured_field["regions"][region_name]
            expected_path = _expected_asset_path(field_path, region_name)
            if asset.get("bbox") != expected_bbox or asset.get("path") != expected_path:
                raise ReadingSnippetError(f"Manifest zmienił region {field_path}/{region_name}")
            expected_geometry = _geometry(expected_bbox)
            if asset.get("operation") != (
                f"magick SOURCE -crop {expected_geometry} +repage OUTPUT"
            ) or asset.get("parentSha256") != actual_source_sha:
                raise ReadingSnippetError(
                    f"Manifest ma niepoprawne provenance {field_path}/{region_name}"
                )
            relative = Path(expected_path)
            if relative.is_absolute() or ".." in relative.parts:
                raise ReadingSnippetError("Manifest zawiera niebezpieczną ścieżkę assetu")
            image_path = output / relative
            if not image_path.is_file():
                raise ReadingSnippetError(f"Brakuje assetu: {expected_path}")
            if _sha256(image_path) != asset.get("sha256"):
                raise ReadingSnippetError(f"Checksum assetu jest niepoprawny: {expected_path}")
            image_dimensions = _dimensions(magick_path, image_path)
            expected_dimensions = {
                "width": expected_bbox["width"],
                "height": expected_bbox["height"],
            }
            if image_dimensions != expected_dimensions or {
                "width": asset.get("width"),
                "height": asset.get("height"),
            } != expected_dimensions:
                raise ReadingSnippetError(f"Wymiary assetu są niepoprawne: {expected_path}")
            expected_files.add(expected_path)

    actual_files = {
        str(path.relative_to(output))
        for path in output.rglob("*")
        if path.is_file()
    }
    if actual_files != expected_files:
        unexpected = sorted(actual_files - expected_files)
        missing = sorted(expected_files - actual_files)
        raise ReadingSnippetError(
            "Zawartość pakietu nie zgadza się z manifestem"
            + (f"; nadmiarowe: {', '.join(unexpected)}" if unexpected else "")
            + (f"; brakujące: {', '.join(missing)}" if missing else "")
        )
    return {**manifest, "runStatus": "verified_existing"}


def verify_reading_snippet_package(root: Path, output_dir: Path) -> dict[str, Any]:
    """Fully verify an existing derivative package without touching SQLite or RAW."""

    resolved_root = root.resolve()
    output = _resolve_output(root, output_dir)
    return _verify_package(resolved_root, output)


def build_reading_snippet_package(
    root: Path, request_path: Path, output_dir: Path
) -> dict[str, Any]:
    """Build three-scale, source-backed crops for blind field verification."""

    resolved_root = root.resolve()
    output = _resolve_output(root, output_dir)
    magick, magick_version = _magick()
    payload = _load_json(request_path, "requestu snippetów")
    raw_source = payload.get("source")
    if not isinstance(raw_source, dict):
        raise ReadingSnippetError("source musi być obiektem")
    source_path = _required_text(raw_source.get("path"), "source.path")
    source = _resolve_source(resolved_root, source_path)
    source_sha_before = _sha256(source)
    source_dimensions = _dimensions(magick, source)
    request = _normalize_request(
        payload,
        source_width=source_dimensions["width"],
        source_height=source_dimensions["height"],
    )
    if source_sha_before != request["source"]["sha256"]:
        raise ReadingSnippetError(
            "Checksum obrazu źródłowego nie zgadza się z source.sha256 requestu"
        )
    configuration = _configuration(request, magick_version)
    config_fingerprint = _canonical_hash(configuration)
    if output.exists():
        return _verify_package(
            resolved_root,
            output,
            expected_fingerprint=config_fingerprint,
            magick=magick,
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        manifest_fields: list[dict[str, Any]] = []
        for field in request["fields"]:
            region_assets: dict[str, dict[str, Any]] = {}
            for region_name in REGION_NAMES:
                bbox = field["regions"][region_name]
                relative_path = _expected_asset_path(field["path"], region_name)
                destination = temporary / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                geometry = _geometry(bbox)
                _run(
                    [
                        magick,
                        str(source),
                        "-crop",
                        geometry,
                        "+repage",
                        str(destination),
                    ]
                )
                image_dimensions = _dimensions(magick, destination)
                expected_dimensions = {"width": bbox["width"], "height": bbox["height"]}
                if image_dimensions != expected_dimensions:
                    raise ReadingSnippetError(
                        f"Crop {field['path']}/{region_name} ma nieoczekiwane wymiary"
                    )
                region_assets[region_name] = {
                    "bbox": bbox,
                    "path": relative_path,
                    "sha256": _sha256(destination),
                    **image_dimensions,
                    "operation": f"magick SOURCE -crop {geometry} +repage OUTPUT",
                    "parentSha256": source_sha_before,
                }
            manifest_fields.append(
                {
                    "path": field["path"],
                    "kind": field["kind"],
                    "locator": field["locator"],
                    **(
                        {"sourceSegmentId": field["sourceSegmentId"]}
                        if field.get("sourceSegmentId")
                        else {}
                    ),
                    "regions": region_assets,
                }
            )

        source_sha_after = _sha256(source)
        if source_sha_after != source_sha_before:
            raise ReadingSnippetError("Obraz źródłowy zmienił się podczas budowania pakietu")
        manifest: dict[str, Any] = {
            "schema": PACKAGE_SCHEMA,
            "packageId": request["packageId"],
            "privacyClassification": request["privacyClassification"],
            "pipelineVersion": PIPELINE_VERSION,
            "configFingerprint": config_fingerprint,
            "configuration": configuration,
            "workspaceRelativeOutput": _display_path(resolved_root, output),
            "source": {
                **request["source"],
                **source_dimensions,
                "hashVerifiedBeforeBuild": True,
                "hashVerifiedAfterBuild": True,
            },
            "coordinateSpace": {
                "unit": "pixel",
                "origin": "top-left",
                "rectangle": "xywh",
                "sourceWidth": source_dimensions["width"],
                "sourceHeight": source_dimensions["height"],
            },
            "fields": manifest_fields,
            "counts": {
                "fields": len(manifest_fields),
                "derivedImages": len(manifest_fields) * len(REGION_NAMES),
            },
            "provenanceBoundary": _provenance_boundary(),
            "runStatus": "built",
        }
        (temporary / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if output.exists():
            raise ReadingSnippetError(f"OUTPUT pojawił się podczas budowania: {output}")
        os.rename(temporary, output)
        return manifest
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
