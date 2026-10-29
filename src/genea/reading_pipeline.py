from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .command_envelope import (
    CommandEnvelopeConflict,
    canonical_json,
    fingerprint,
    record_command,
    replay_command,
)
from .db import connect, initialize
from .research import require_tables
from .workspace import search_fold, stable_id, utc_now


REQUIRED_TABLES = {
    "artifacts",
    "audit_log",
    "curation_commands",
    "curation_queue",
    "field_readings",
    "reading_pipeline_escalations",
    "reading_pipeline_fields",
    "reading_pipeline_layers",
    "reading_pipeline_runs",
    "reading_versions",
    "readings",
    "source_items",
    "transcript_segments",
}
DOCUMENT_TYPES = {
    "birth-baptism",
    "marriage",
    "death-burial",
    "census",
    "index",
    "other",
    "unknown",
}
LAYER_TYPES = ("transcription", "transliteration", "translation", "extract")
CERTAINTIES = {"Certain", "Probable", "Ambiguous", "Illegible", "Damaged"}
CYRILLIC_LANGUAGES = {"rus", "ukr", "bel", "chu", "church-slavonic"}
NORMALIZATION_BASES = {
    "orthographic",
    "morphology",
    "transliteration",
    "translation",
    "external_context",
    "unknown",
}
SAFE_NORMALIZATION_BASES = {
    "orthographic",
    "morphology",
    "transliteration",
    "translation",
}
IMAGE_QUALITY_STATUSES = {"clear", "obstructed", "damaged", "unknown"}
OBSTRUCTION_SEVERITIES = {"minor", "material", "blocking"}
CRITICALITIES = {"standard", "critical"}
GROUNDING_BASES = {
    "source_quote",
    "source_segment_region",
    "grapheme_evidence",
    "unknown",
}
FORMULA_ASSESSMENT_STATUSES = {"match", "mismatch", "ambiguous", "not_assessed"}
REQUIRED_FIELDS = {
    "birth-baptism": (
        "event.date",
        "event.place",
        "child.name",
        "father.name",
        "mother.name",
    ),
    "marriage": (
        "event.date",
        "event.place",
        "groom.name",
        "bride.name",
        "groom.father.name",
        "groom.mother.name",
        "bride.father.name",
        "bride.mother.name",
    ),
    "death-burial": (
        "event.date",
        "event.place",
        "deceased.name",
    ),
    "census": ("event.date", "event.place", "household.head.name"),
    "index": ("entry.name", "entry.locator"),
    "other": (),
    "unknown": (),
}


class ReadingPipelineError(ValueError):
    """A safe validation error for an AI-assisted reading proposal."""


class ReadingPipelineConflict(ReadingPipelineError):
    """The source, artifact or proposal changed after preview."""


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReadingPipelineError(f"Pole {label} musi być niepustym tekstem")
    return value.strip()


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReadingPipelineError(f"Nie można odczytać manifestu: {error}") from error
    if not isinstance(payload, dict):
        raise ReadingPipelineError("Manifest pipeline musi być obiektem JSON")
    return payload


def _validate_bbox(value: Any, label: str) -> dict[str, float] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ReadingPipelineError(f"{label} musi być obiektem x/y/width/height")
    result: dict[str, float] = {}
    for key in ("x", "y", "width", "height"):
        number = value.get(key)
        if not isinstance(number, (int, float)) or number < 0:
            raise ReadingPipelineError(f"{label}.{key} musi być liczbą nieujemną")
        result[key] = float(number)
    return result


def _validate_character_analysis(value: Any, label: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ReadingPipelineError(f"{label} musi być tablicą")
    result: list[dict[str, Any]] = []
    for index, raw_item in enumerate(value):
        if not isinstance(raw_item, dict):
            raise ReadingPipelineError(f"{label}[{index}] musi być obiektem")
        unit = _required_text(raw_item.get("unit"), f"{label}[{index}].unit")
        observation = _required_text(
            raw_item.get("observation"), f"{label}[{index}].observation"
        )
        position = raw_item.get("position")
        if position is not None and (
            not isinstance(position, int) or isinstance(position, bool) or position < 0
        ):
            raise ReadingPipelineError(
                f"{label}[{index}].position musi być nieujemną liczbą całkowitą"
            )
        result.append(
            {"unit": unit, "observation": observation, "position": position}
        )
    return result


def _validate_grapheme_evidence(value: Any, label: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ReadingPipelineError(f"{label} musi być tablicą")
    result: list[dict[str, Any]] = []
    for index, raw_item in enumerate(value):
        if not isinstance(raw_item, dict):
            raise ReadingPipelineError(f"{label}[{index}] musi być obiektem")
        sha256 = _required_text(raw_item.get("sha256"), f"{label}[{index}].sha256").lower()
        if len(sha256) != 64 or any(character not in "0123456789abcdef" for character in sha256):
            raise ReadingPipelineError(f"{label}[{index}].sha256 musi być SHA-256")
        scale = str(raw_item.get("scale") or "field")
        if scale not in {"field", "context", "fullAct"}:
            raise ReadingPipelineError(f"{label}[{index}].scale jest nieobsługiwane")
        result.append(
            {
                "sourceSegmentId": _required_text(
                    raw_item.get("sourceSegmentId"),
                    f"{label}[{index}].sourceSegmentId",
                ),
                "locator": _required_text(
                    raw_item.get("locator"), f"{label}[{index}].locator"
                ),
                "sha256": sha256,
                "bbox": _validate_bbox(raw_item.get("bbox"), f"{label}[{index}].bbox"),
                "scale": scale,
            }
        )
        if result[-1]["bbox"] is None:
            raise ReadingPipelineError(f"{label}[{index}].bbox jest wymagane")
    return result


def _validate_formula_assessment(value: Any, label: str) -> dict[str, Any]:
    if value is None:
        return {"status": "not_assessed", "formula": None, "notes": None}
    if not isinstance(value, dict):
        raise ReadingPipelineError(f"{label} musi być obiektem")
    status = str(value.get("status") or "not_assessed")
    if status not in FORMULA_ASSESSMENT_STATUSES:
        raise ReadingPipelineError(f"{label}.status jest nieobsługiwany: {status}")
    return {
        "status": status,
        "formula": str(value.get("formula") or "").strip() or None,
        "notes": str(value.get("notes") or "").strip() or None,
    }


def _criticality(raw_field: dict[str, Any], label: str) -> tuple[bool, str]:
    raw_critical = raw_field.get("critical")
    if raw_critical is not None and not isinstance(raw_critical, bool):
        raise ReadingPipelineError(f"{label}.critical musi być boolean")
    raw_criticality = raw_field.get("criticality")
    if raw_criticality is not None and raw_criticality not in CRITICALITIES:
        raise ReadingPipelineError(f"{label}.criticality jest nieobsługiwane")
    critical = bool(raw_critical) or raw_criticality == "critical"
    if raw_critical is False and raw_criticality == "critical":
        raise ReadingPipelineError(f"{label}.critical i criticality są sprzeczne")
    return critical, "critical" if critical else "standard"


def _validate_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    schema_version = payload.get("schemaVersion")
    if schema_version not in {1, 2}:
        raise ReadingPipelineError("Obsługiwane schemaVersion pipeline to 1 i 2")
    source_item_id = _required_text(payload.get("sourceItemId"), "sourceItemId")
    artifact_id = _required_text(payload.get("artifactId"), "artifactId")
    command_id = _required_text(payload.get("commandId"), "commandId")
    provider = _required_text(payload.get("provider"), "provider")
    model = _required_text(payload.get("model"), "model")
    pipeline_version = _required_text(payload.get("pipelineVersion"), "pipelineVersion")
    document_type = _required_text(payload.get("documentType"), "documentType")
    if document_type not in DOCUMENT_TYPES:
        raise ReadingPipelineError(f"Nieobsługiwany documentType: {document_type}")
    source_language = _required_text(payload.get("sourceLanguage"), "sourceLanguage").lower()
    source_script = str(payload.get("sourceScript") or "").strip() or None
    input_sha256 = _required_text(payload.get("inputSha256"), "inputSha256").lower()
    if len(input_sha256) != 64 or any(character not in "0123456789abcdef" for character in input_sha256):
        raise ReadingPipelineError("inputSha256 musi być 64-znakowym SHA-256")

    raw_layers = payload.get("layers")
    if not isinstance(raw_layers, list) or not raw_layers:
        raise ReadingPipelineError("layers musi być niepustą tablicą")
    layers: list[dict[str, Any]] = []
    seen_layer_types: set[str] = set()
    segment_keys: set[tuple[str, str]] = set()
    for layer_index, raw_layer in enumerate(raw_layers):
        if not isinstance(raw_layer, dict):
            raise ReadingPipelineError(f"layers[{layer_index}] musi być obiektem")
        layer_type = _required_text(raw_layer.get("type"), f"layers[{layer_index}].type")
        if layer_type not in LAYER_TYPES:
            raise ReadingPipelineError(f"Nieobsługiwana warstwa: {layer_type}")
        if layer_type in seen_layer_types:
            raise ReadingPipelineError(f"Powtórzona warstwa: {layer_type}")
        seen_layer_types.add(layer_type)
        language = str(raw_layer.get("language") or "").strip() or None
        raw_segments = raw_layer.get("segments", [])
        if not isinstance(raw_segments, list):
            raise ReadingPipelineError(f"segments warstwy {layer_type} musi być tablicą")
        segments: list[dict[str, Any]] = []
        seen_sequences: set[int] = set()
        for segment_index, raw_segment in enumerate(raw_segments):
            if not isinstance(raw_segment, dict):
                raise ReadingPipelineError(
                    f"Segment {layer_type}[{segment_index}] musi być obiektem"
                )
            segment_id = _required_text(
                raw_segment.get("id"), f"segments {layer_type}[{segment_index}].id"
            )
            sequence = raw_segment.get("sequence", segment_index)
            if not isinstance(sequence, int) or sequence < 0 or sequence in seen_sequences:
                raise ReadingPipelineError(
                    f"Sekwencja segmentu {layer_type}/{segment_id} jest niepoprawna"
                )
            seen_sequences.add(sequence)
            text = str(raw_segment.get("text") or "")
            certainty = str(raw_segment.get("certainty") or "Ambiguous")
            if certainty not in CERTAINTIES:
                raise ReadingPipelineError(
                    f"Nieobsługiwana certainty segmentu {layer_type}/{segment_id}"
                )
            segment_keys.add((layer_type, segment_id))
            segments.append(
                {
                    "id": segment_id,
                    "sequence": sequence,
                    "text": text,
                    "certainty": certainty,
                    "pageLabel": str(raw_segment.get("pageLabel") or "").strip() or None,
                    "locator": str(raw_segment.get("locator") or "").strip() or None,
                    "bbox": _validate_bbox(
                        raw_segment.get("bbox"), f"bbox {layer_type}/{segment_id}"
                    ),
                }
            )
        layers.append(
            {
                "type": layer_type,
                "language": language,
                "segments": sorted(segments, key=lambda item: (item["sequence"], item["id"])),
            }
        )
    missing_layer_objects = set(LAYER_TYPES) - seen_layer_types
    if missing_layer_objects:
        raise ReadingPipelineError(
            "Manifest musi jawnie zawierać cztery warstwy; brakuje: "
            + ", ".join(sorted(missing_layer_objects))
        )

    raw_fields = payload.get("fields")
    if not isinstance(raw_fields, list):
        raise ReadingPipelineError("fields musi być tablicą")
    fields: list[dict[str, Any]] = []
    seen_fields: set[str] = set()
    for index, raw_field in enumerate(raw_fields):
        if not isinstance(raw_field, dict):
            raise ReadingPipelineError(f"fields[{index}] musi być obiektem")
        field_path = _required_text(raw_field.get("path"), f"fields[{index}].path")
        if field_path in seen_fields:
            raise ReadingPipelineError(f"Powtórzone pole strukturalne: {field_path}")
        seen_fields.add(field_path)
        confidence = raw_field.get("confidence")
        if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise ReadingPipelineError(f"confidence pola {field_path} musi mieścić się w 0..1")
        certainty = str(raw_field.get("certainty") or "Ambiguous")
        if certainty not in CERTAINTIES:
            raise ReadingPipelineError(f"Nieobsługiwana certainty pola {field_path}")
        source_segment = str(raw_field.get("sourceSegment") or "").strip() or None
        if source_segment and ("transcription", source_segment) not in segment_keys:
            raise ReadingPipelineError(
                f"Pole {field_path} odwołuje się do nieznanego segmentu transkrypcji"
            )
        alternatives = raw_field.get("alternatives", [])
        if not isinstance(alternatives, list) or not all(
            isinstance(value, str) for value in alternatives
        ):
            raise ReadingPipelineError(f"alternatives pola {field_path} musi być tablicą tekstów")
        material_alternatives = raw_field.get("materialAlternatives", [])
        if not isinstance(material_alternatives, list) or not all(
            isinstance(value, str) for value in material_alternatives
        ):
            raise ReadingPipelineError(
                f"materialAlternatives pola {field_path} musi być tablicą tekstów"
            )
        normalization_basis = str(raw_field.get("normalizationBasis") or "").strip() or None
        if normalization_basis and normalization_basis not in NORMALIZATION_BASES:
            raise ReadingPipelineError(
                f"Nieobsługiwany normalizationBasis pola {field_path}: {normalization_basis}"
            )
        critical, criticality = _criticality(raw_field, f"fields[{index}]")
        grounding_basis = str(raw_field.get("groundingBasis") or "unknown")
        if grounding_basis not in GROUNDING_BASES:
            raise ReadingPipelineError(
                f"Nieobsługiwany groundingBasis pola {field_path}: {grounding_basis}"
            )
        fields.append(
            {
                "path": field_path,
                "value": None if raw_field.get("value") is None else str(raw_field.get("value")),
                "normalizedValue": None
                if raw_field.get("normalizedValue") is None
                else str(raw_field.get("normalizedValue")),
                "role": str(raw_field.get("role") or "").strip() or None,
                "confidence": float(confidence),
                "certainty": certainty,
                "sourceQuote": str(raw_field.get("sourceQuote") or "").strip() or None,
                "sourceSegment": source_segment,
                "bbox": _validate_bbox(raw_field.get("bbox"), f"bbox pola {field_path}"),
                "alternatives": alternatives,
                "materialAlternatives": material_alternatives,
                "normalizationBasis": normalization_basis,
                "notes": str(raw_field.get("notes") or "").strip() or None,
                "critical": critical,
                "criticality": criticality,
                "groundingBasis": grounding_basis,
                "characterAnalysis": _validate_character_analysis(
                    raw_field.get("characterAnalysis"),
                    f"fields[{index}].characterAnalysis",
                ),
                "graphemeEvidenceRefs": _validate_grapheme_evidence(
                    raw_field.get("graphemeEvidenceRefs"),
                    f"fields[{index}].graphemeEvidenceRefs",
                ),
                "languageFormulaAssessment": _validate_formula_assessment(
                    raw_field.get("languageFormulaAssessment"),
                    f"fields[{index}].languageFormulaAssessment",
                ),
            }
        )

    raw_independent = payload.get("independentReadings", [])
    if not isinstance(raw_independent, list):
        raise ReadingPipelineError("independentReadings musi być tablicą")
    if schema_version == 2 and len(raw_independent) < 2:
        raise ReadingPipelineError(
            "Manifest schemaVersion=2 wymaga co najmniej dwóch niezależnych odczytów"
        )
    independent_readings: list[dict[str, Any]] = []
    seen_reading_ids: set[str] = set()
    seen_reader_ids: set[str] = set()
    seen_run_ids: set[str] = set()
    for reading_index, raw_reading in enumerate(raw_independent):
        if not isinstance(raw_reading, dict):
            raise ReadingPipelineError(
                f"independentReadings[{reading_index}] musi być obiektem"
            )
        reading_id = _required_text(
            raw_reading.get("id"), f"independentReadings[{reading_index}].id"
        )
        reader_id = _required_text(
            raw_reading.get("readerId"),
            f"independentReadings[{reading_index}].readerId",
        )
        if reading_id in seen_reading_ids or reader_id in seen_reader_ids:
            raise ReadingPipelineError(
                "Niezależne odczyty muszą mieć różne id i readerId"
            )
        seen_reading_ids.add(reading_id)
        seen_reader_ids.add(reader_id)
        run_id = str(raw_reading.get("runId") or "").strip() or None
        if run_id and run_id in seen_run_ids:
            raise ReadingPipelineError("Niezależne odczyty muszą mieć różne runId")
        if run_id:
            seen_run_ids.add(run_id)
        context_disclosed = raw_reading.get("contextDisclosed")
        if not isinstance(context_disclosed, bool):
            raise ReadingPipelineError(
                f"independentReadings[{reading_index}].contextDisclosed musi być boolean"
            )
        if schema_version == 2 and context_disclosed:
            raise ReadingPipelineError(
                "Odczyt z ujawnionym kontekstem nie może wypełnić bramki dwóch blindów"
            )
        raw_reading_fields = raw_reading.get("fields")
        if not isinstance(raw_reading_fields, list):
            raise ReadingPipelineError(
                f"independentReadings[{reading_index}].fields musi być tablicą"
            )
        reading_fields: list[dict[str, Any]] = []
        reading_field_paths: set[str] = set()
        for field_index, raw_reading_field in enumerate(raw_reading_fields):
            if not isinstance(raw_reading_field, dict):
                raise ReadingPipelineError(
                    f"Pole odczytu {reading_id}[{field_index}] musi być obiektem"
                )
            field_path = _required_text(
                raw_reading_field.get("path"),
                f"independentReadings[{reading_index}].fields[{field_index}].path",
            )
            if field_path in reading_field_paths:
                raise ReadingPipelineError(
                    f"Powtórzone pole {field_path} w odczycie {reading_id}"
                )
            reading_field_paths.add(field_path)
            confidence = raw_reading_field.get("confidence")
            if confidence is not None and (
                not isinstance(confidence, (int, float))
                or isinstance(confidence, bool)
                or not 0 <= confidence <= 1
            ):
                raise ReadingPipelineError(
                    f"confidence pola {field_path} w odczycie {reading_id} musi być null lub mieścić się w 0..1"
                )
            raw_certainty = raw_reading_field.get("certainty")
            certainty = None if raw_certainty is None else str(raw_certainty)
            if certainty is not None and certainty not in CERTAINTIES:
                raise ReadingPipelineError(
                    f"Nieobsługiwana certainty pola {field_path} w odczycie {reading_id}"
                )
            alternatives = raw_reading_field.get("alternatives", [])
            if not isinstance(alternatives, list) or not all(
                isinstance(value, str) for value in alternatives
            ):
                raise ReadingPipelineError(
                    f"alternatives pola {field_path} w odczycie {reading_id} musi być tablicą tekstów"
                )
            critical, criticality = _criticality(
                raw_reading_field,
                f"independentReadings[{reading_index}].fields[{field_index}]",
            )
            grounding_basis = str(raw_reading_field.get("groundingBasis") or "unknown")
            if grounding_basis not in GROUNDING_BASES:
                raise ReadingPipelineError(
                    f"Nieobsługiwany groundingBasis pola {field_path} w odczycie {reading_id}"
                )
            reading_fields.append(
                {
                    "path": field_path,
                    "value": None
                    if raw_reading_field.get("value") is None
                    else str(raw_reading_field.get("value")),
                    "normalizedValue": None
                    if raw_reading_field.get("normalizedValue") is None
                    else str(raw_reading_field.get("normalizedValue")),
                    "confidence": None if confidence is None else float(confidence),
                    "certainty": certainty,
                    "alternatives": alternatives,
                    "sourceQuote": str(raw_reading_field.get("sourceQuote") or "").strip()
                    or None,
                    "sourceSegment": str(
                        raw_reading_field.get("sourceSegment") or ""
                    ).strip()
                    or None,
                    "bbox": _validate_bbox(
                        raw_reading_field.get("bbox"),
                        f"bbox pola {field_path} w odczycie {reading_id}",
                    ),
                    "critical": critical,
                    "criticality": criticality,
                    "groundingBasis": grounding_basis,
                    "characterAnalysis": _validate_character_analysis(
                        raw_reading_field.get("characterAnalysis"),
                        f"independentReadings[{reading_index}].fields[{field_index}].characterAnalysis",
                    ),
                    "graphemeEvidenceRefs": _validate_grapheme_evidence(
                        raw_reading_field.get("graphemeEvidenceRefs"),
                        f"independentReadings[{reading_index}].fields[{field_index}].graphemeEvidenceRefs",
                    ),
                    "languageFormulaAssessment": _validate_formula_assessment(
                        raw_reading_field.get("languageFormulaAssessment"),
                        f"independentReadings[{reading_index}].fields[{field_index}].languageFormulaAssessment",
                    ),
                }
            )
        independent_readings.append(
            {
                "id": reading_id,
                "readerId": reader_id,
                "runId": run_id,
                "contextDisclosed": context_disclosed,
                "fields": sorted(reading_fields, key=lambda item: item["path"]),
            }
        )

    raw_role_expectations = payload.get("roleExpectations", [])
    if not isinstance(raw_role_expectations, list):
        raise ReadingPipelineError("roleExpectations musi być tablicą")
    if schema_version == 2 and "roleExpectations" not in payload:
        raise ReadingPipelineError("Manifest schemaVersion=2 wymaga roleExpectations")
    role_expectations: list[dict[str, Any]] = []
    seen_roles: set[str] = set()
    for expectation_index, raw_expectation in enumerate(raw_role_expectations):
        if not isinstance(raw_expectation, dict):
            raise ReadingPipelineError(
                f"roleExpectations[{expectation_index}] musi być obiektem"
            )
        role = _required_text(
            raw_expectation.get("role"), f"roleExpectations[{expectation_index}].role"
        )
        minimum = raw_expectation.get("minimum")
        if not isinstance(minimum, int) or minimum < 1:
            raise ReadingPipelineError(
                f"roleExpectations[{expectation_index}].minimum musi być dodatnią liczbą całkowitą"
            )
        if role in seen_roles:
            raise ReadingPipelineError(f"Powtórzone oczekiwanie roli: {role}")
        seen_roles.add(role)
        role_expectations.append(
            {
                "role": role,
                "minimum": minimum,
                "basis": str(raw_expectation.get("basis") or "").strip() or None,
            }
        )

    raw_image_quality = payload.get("imageQuality", {})
    if not isinstance(raw_image_quality, dict):
        raise ReadingPipelineError("imageQuality musi być obiektem")
    if schema_version == 2 and "imageQuality" not in payload:
        raise ReadingPipelineError("Manifest schemaVersion=2 wymaga imageQuality")
    image_status = str(raw_image_quality.get("status") or "unknown")
    if image_status not in IMAGE_QUALITY_STATUSES:
        raise ReadingPipelineError(f"Nieobsługiwany imageQuality.status: {image_status}")
    raw_obstructions = raw_image_quality.get("obstructions", [])
    if not isinstance(raw_obstructions, list):
        raise ReadingPipelineError("imageQuality.obstructions musi być tablicą")
    obstructions: list[dict[str, Any]] = []
    seen_obstructions: set[str] = set()
    for obstruction_index, raw_obstruction in enumerate(raw_obstructions):
        if not isinstance(raw_obstruction, dict):
            raise ReadingPipelineError(
                f"imageQuality.obstructions[{obstruction_index}] musi być obiektem"
            )
        obstruction_id = _required_text(
            raw_obstruction.get("id"),
            f"imageQuality.obstructions[{obstruction_index}].id",
        )
        if obstruction_id in seen_obstructions:
            raise ReadingPipelineError(f"Powtórzona przeszkoda obrazu: {obstruction_id}")
        seen_obstructions.add(obstruction_id)
        severity = str(raw_obstruction.get("severity") or "material")
        if severity not in OBSTRUCTION_SEVERITIES:
            raise ReadingPipelineError(
                f"Nieobsługiwana severity przeszkody {obstruction_id}: {severity}"
            )
        affects_fields = raw_obstruction.get("affectsFields", [])
        if not isinstance(affects_fields, list) or not all(
            isinstance(value, str) and value.strip() for value in affects_fields
        ):
            raise ReadingPipelineError(
                f"affectsFields przeszkody {obstruction_id} musi być tablicą ścieżek"
            )
        obstructions.append(
            {
                "id": obstruction_id,
                "severity": severity,
                "description": _required_text(
                    raw_obstruction.get("description"),
                    f"imageQuality.obstructions[{obstruction_index}].description",
                ),
                "affectsFields": sorted(set(value.strip() for value in affects_fields)),
            }
        )

    metrics = payload.get("metrics", {})
    provenance = payload.get("provenance", {})
    if not isinstance(metrics, dict) or not isinstance(provenance, dict):
        raise ReadingPipelineError("metrics i provenance muszą być obiektami")
    return {
        "schemaVersion": schema_version,
        "commandId": command_id,
        "sourceItemId": source_item_id,
        "artifactId": artifact_id,
        "provider": provider,
        "model": model,
        "pipelineVersion": pipeline_version,
        "documentType": document_type,
        "sourceLanguage": source_language,
        "sourceScript": source_script,
        "inputSha256": input_sha256,
        "layers": sorted(layers, key=lambda item: LAYER_TYPES.index(item["type"])),
        "fields": sorted(fields, key=lambda item: item["path"]),
        "independentReadings": sorted(independent_readings, key=lambda item: item["id"]),
        "roleExpectations": sorted(role_expectations, key=lambda item: item["role"]),
        "imageQuality": {
            "status": image_status,
            "notes": str(raw_image_quality.get("notes") or "").strip() or None,
            "obstructions": sorted(obstructions, key=lambda item: item["id"]),
        },
        "metrics": metrics,
        "provenance": provenance,
    }


def _comparison_value(field: dict[str, Any] | None) -> tuple[str, str | None]:
    if not field:
        return "", None
    raw_value = field.get("normalizedValue")
    if raw_value is None or not str(raw_value).strip():
        raw_value = field.get("value")
    if raw_value is None or not str(raw_value).strip():
        return "", None
    text = str(raw_value).strip()
    return search_fold(text), text


def _is_additional_name_disagreement(values: list[str]) -> bool:
    token_sets = [set(value.split()) for value in values if value]
    if len(token_sets) < 2:
        return False
    return any(
        left < right or right < left
        for index, left in enumerate(token_sets)
        for right in token_sets[index + 1 :]
    )


def _compare_independent_readings(payload: dict[str, Any]) -> dict[str, Any]:
    readings = payload["independentReadings"]
    if len(readings) < 2:
        return {
            "readingIds": [reading["id"] for reading in readings],
            "fieldCount": 0,
            "agreementCount": 0,
            "disagreementCount": 0,
            "missingInSomeCount": 0,
            "fields": [],
        }
    field_maps = {
        reading["id"]: {field["path"]: field for field in reading["fields"]}
        for reading in readings
    }
    field_paths = sorted(
        {path for mapping in field_maps.values() for path in mapping}
    )
    compared_fields: list[dict[str, Any]] = []
    agreement_count = 0
    disagreement_count = 0
    missing_count = 0
    for field_path in field_paths:
        values: list[dict[str, Any]] = []
        folded_nonempty: list[str] = []
        missing = False
        for reading in readings:
            folded, display = _comparison_value(field_maps[reading["id"]].get(field_path))
            missing = missing or not folded
            if folded:
                folded_nonempty.append(folded)
            values.append(
                {
                    "readingId": reading["id"],
                    "readerId": reading["readerId"],
                    "value": display,
                }
            )
        distinct = sorted(set(folded_nonempty))
        disagreement = len(distinct) > 1 or (missing and bool(distinct))
        difference_kind = "agreement"
        if disagreement:
            disagreement_count += 1
            if missing:
                missing_count += 1
                difference_kind = "missing_in_some"
            elif field_path.endswith(".name") and _is_additional_name_disagreement(
                distinct
            ):
                difference_kind = "additional_name"
            else:
                difference_kind = "different_value"
        else:
            agreement_count += 1
        compared_fields.append(
            {
                "path": field_path,
                "status": "disagreement" if disagreement else "agreement",
                "differenceKind": difference_kind,
                "values": values,
            }
        )
    return {
        "readingIds": [reading["id"] for reading in readings],
        "fieldCount": len(field_paths),
        "agreementCount": agreement_count,
        "disagreementCount": disagreement_count,
        "missingInSomeCount": missing_count,
        "fields": compared_fields,
    }


def _dependency_snapshot(connection, payload: dict[str, Any]) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    source = connection.execute(
        """SELECT id,title,item_type,date_text,locator,citation_text,
                  verification_status,metadata_json
           FROM source_items WHERE id=?""",
        (payload["sourceItemId"],),
    ).fetchone()
    if source is None:
        raise ReadingPipelineError(f"Nie istnieje source item {payload['sourceItemId']}")
    artifact = connection.execute(
        """SELECT id,source_item_id,artifact_type,path,media_type,sha256,immutable,
                  metadata_json
           FROM artifacts WHERE id=?""",
        (payload["artifactId"],),
    ).fetchone()
    if artifact is None:
        raise ReadingPipelineError(f"Nie istnieje artifact {payload['artifactId']}")
    if artifact["source_item_id"] != payload["sourceItemId"]:
        raise ReadingPipelineError("Artifact nie należy do wskazanego source item")
    if not artifact["sha256"]:
        raise ReadingPipelineError("Artifact nie ma zapisanego SHA-256")
    source_payload = dict(source)
    artifact_payload = dict(artifact)
    for item in (source_payload, artifact_payload):
        if "metadata_json" in item:
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
    return {"source": source_payload, "artifact": artifact_payload}


def _assess(payload: dict[str, Any], dependency: dict[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    comparison = _compare_independent_readings(payload)
    layer_by_type = {layer["type"]: layer for layer in payload["layers"]}
    required_layers = {"transcription", "translation", "extract"}
    if payload["sourceLanguage"] in CYRILLIC_LANGUAGES or payload.get("sourceScript") == "Cyrl":
        required_layers.add("transliteration")
    for layer_type in sorted(required_layers):
        if layer_type not in layer_by_type or not layer_by_type[layer_type]["segments"]:
            issues.append(
                {
                    "fieldPath": None,
                    "reasonCode": "missing_layer",
                    "priority": 1,
                    "recommendedRole": "human_curator",
                    "details": {"layerType": layer_type},
                }
            )
    if not dependency["source"].get("locator"):
        issues.append(
            {
                "fieldPath": None,
                "reasonCode": "missing_locator",
                "priority": 1,
                "recommendedRole": "source_auditor",
                "details": {"sourceItemId": payload["sourceItemId"]},
            }
        )
    if dependency["artifact"]["sha256"].lower() != payload["inputSha256"]:
        issues.append(
            {
                "fieldPath": None,
                "reasonCode": "artifact_hash_mismatch",
                "priority": 0,
                "recommendedRole": "source_auditor",
                "details": {
                    "manifest": payload["inputSha256"],
                    "database": dependency["artifact"]["sha256"].lower(),
                },
            }
        )
    fields = {field["path"]: field for field in payload["fields"]}
    required = REQUIRED_FIELDS[payload["documentType"]]
    present_required = 0
    for field_path in required:
        field = fields.get(field_path)
        if field is None or not str(field.get("value") or "").strip():
            issues.append(
                {
                    "fieldPath": field_path,
                    "reasonCode": "missing_required_field",
                    "priority": 1,
                    "recommendedRole": "contextual_reader",
                    "details": {"documentType": payload["documentType"]},
                }
            )
        else:
            present_required += 1
    for field in payload["fields"]:
        value_present = bool(str(field.get("value") or "").strip())
        if value_present and not field.get("sourceQuote"):
            issues.append(
                {
                    "fieldPath": field["path"],
                    "reasonCode": "missing_source_quote",
                    "priority": 1,
                    "recommendedRole": "source_auditor",
                    "details": {},
                }
            )
        if value_present and field["confidence"] < 0.85:
            issues.append(
                {
                    "fieldPath": field["path"],
                    "reasonCode": "low_confidence",
                    "priority": 2,
                    "recommendedRole": "blind_paleographer",
                    "details": {"confidence": field["confidence"]},
                }
            )
        if field["certainty"] == "Ambiguous":
            issues.append(
                {
                    "fieldPath": field["path"],
                    "reasonCode": "ambiguous_reading",
                    "priority": 2,
                    "recommendedRole": "agent_adjudicator",
                    "details": {"alternatives": field["alternatives"]},
                }
            )
        elif field["certainty"] in {"Illegible", "Damaged"}:
            issues.append(
                {
                    "fieldPath": field["path"],
                    "reasonCode": "illegible_reading",
                    "priority": 1,
                    "recommendedRole": "human_curator",
                    "details": {"certainty": field["certainty"]},
                }
            )
        if field["materialAlternatives"]:
            issues.append(
                {
                    "fieldPath": field["path"],
                    "reasonCode": "material_alternative_present",
                    "priority": 2,
                    "recommendedRole": "agent_adjudicator",
                    "details": {"alternatives": field["materialAlternatives"]},
                }
            )
        normalized = str(field.get("normalizedValue") or "").strip()
        literal = str(field.get("value") or "").strip()
        normalization_differs = (
            bool(normalized)
            and bool(literal)
            and search_fold(normalized) != search_fold(literal)
        )
        if normalization_differs and field.get("normalizationBasis") not in SAFE_NORMALIZATION_BASES:
            issues.append(
                {
                    "fieldPath": field["path"],
                    "reasonCode": "literal_normalization_divergence",
                    "priority": 2,
                    "recommendedRole": "contextual_reader",
                    "details": {
                        "literal": literal,
                        "normalized": normalized,
                        "basis": field.get("normalizationBasis"),
                    },
                }
            )
        formula_status = field["languageFormulaAssessment"]["status"]
        if formula_status in {"mismatch", "ambiguous"}:
            issues.append(
                {
                    "fieldPath": field["path"],
                    "reasonCode": "language_formula_mismatch",
                    "priority": 2,
                    "recommendedRole": "contextual_reader",
                    "details": {
                        "status": formula_status,
                        "formula": field["languageFormulaAssessment"]["formula"],
                        "notes": field["languageFormulaAssessment"]["notes"],
                        "scope": "pipeline_field",
                    },
                }
            )

    role_counts: dict[str, int] = {}
    for field in payload["fields"]:
        if field.get("role") and str(field.get("value") or "").strip():
            role_counts[field["role"]] = role_counts.get(field["role"], 0) + 1
    for expectation in payload["roleExpectations"]:
        actual = role_counts.get(expectation["role"], 0)
        if actual < expectation["minimum"]:
            issues.append(
                {
                    "fieldPath": f"role:{expectation['role']}",
                    "reasonCode": "expected_role_missing",
                    "priority": 1,
                    "recommendedRole": "contextual_reader",
                    "details": {
                        "role": expectation["role"],
                        "expectedMinimum": expectation["minimum"],
                        "actual": actual,
                        "basis": expectation["basis"],
                    },
                }
            )

    for obstruction in payload["imageQuality"]["obstructions"]:
        affected = obstruction["affectsFields"] or ["document:image"]
        for field_path in affected:
            issues.append(
                {
                    "fieldPath": field_path,
                    "reasonCode": "image_region_obstructed",
                    "priority": 0 if obstruction["severity"] == "blocking" else 1,
                    "recommendedRole": "source_auditor",
                    "details": {
                        "obstructionId": obstruction["id"],
                        "severity": obstruction["severity"],
                        "description": obstruction["description"],
                    },
                }
            )

    independent_maps = {
        reading["id"]: {field["path"]: field for field in reading["fields"]}
        for reading in payload["independentReadings"]
    }
    for reading in payload["independentReadings"]:
        for field in reading["fields"]:
            formula_status = field["languageFormulaAssessment"]["status"]
            if formula_status in {"mismatch", "ambiguous"}:
                issues.append(
                    {
                        "fieldPath": field["path"],
                        "reasonCode": "language_formula_mismatch",
                        "priority": 2,
                        "recommendedRole": "contextual_reader",
                        "details": {
                            "status": formula_status,
                            "formula": field["languageFormulaAssessment"]["formula"],
                            "notes": field["languageFormulaAssessment"]["notes"],
                            "scope": "independent_reading",
                            "readingId": reading["id"],
                            "readerId": reading["readerId"],
                        },
                    }
                )

    for compared_field in comparison["fields"]:
        if compared_field["status"] == "disagreement":
            reason_code = (
                "additional_name_disagreement"
                if compared_field["differenceKind"] == "additional_name"
                else "independent_reading_disagreement"
            )
            issues.append(
                {
                    "fieldPath": compared_field["path"],
                    "reasonCode": reason_code,
                    "priority": 1,
                    "recommendedRole": "agent_adjudicator",
                    "details": {
                        "differenceKind": compared_field["differenceKind"],
                        "values": compared_field["values"],
                    },
                }
            )
            continue
        main_field = fields.get(compared_field["path"])
        if not main_field or not main_field["critical"]:
            continue
        weak_readings: list[dict[str, Any]] = []
        for reading in payload["independentReadings"]:
            independent_field = independent_maps[reading["id"]].get(compared_field["path"])
            source_grounded = bool(
                independent_field
                and (
                    independent_field.get("sourceQuote")
                    or (
                        independent_field.get("sourceSegment")
                        and independent_field.get("bbox")
                    )
                )
            )
            # A crop reference proves where the reading came from, but it is not
            # itself a character-level analysis.  Critical agreement is grounded
            # only after the reader records both the disputed units and their
            # image-backed grapheme evidence.
            character_grounded = bool(
                independent_field
                and independent_field.get("characterAnalysis")
                and independent_field.get("graphemeEvidenceRefs")
            )
            if not source_grounded or not character_grounded:
                weak_readings.append(
                    {
                        "readingId": reading["id"],
                        "readerId": reading["readerId"],
                        "sourceGrounded": source_grounded,
                        "characterGrounded": character_grounded,
                    }
                )
        if weak_readings:
            issues.append(
                {
                    "fieldPath": compared_field["path"],
                    "reasonCode": "weakly_grounded_agreement",
                    "priority": 1,
                    "recommendedRole": "blind_paleographer",
                    "details": {
                        "criticality": main_field["criticality"],
                        "weakReadings": weak_readings,
                    },
                }
            )
    deduplicated = {
        (item["fieldPath"], item["reasonCode"]): item for item in issues
    }
    issues = sorted(
        deduplicated.values(),
        key=lambda item: (item["priority"], item["reasonCode"], item["fieldPath"] or ""),
    )
    completeness = present_required / len(required) if required else 1.0
    blocking = any(item["reasonCode"] == "artifact_hash_mismatch" for item in issues)
    status = "review_required" if issues else "ready_for_curator"
    return {
        "status": status,
        "completeness": completeness,
        "requiredFields": list(required),
        "presentRequiredFields": present_required,
        "comparison": comparison,
        "issues": issues,
        "blocking": blocking,
    }


def _preview(connection, payload: dict[str, Any]) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    dependency = _dependency_snapshot(connection, payload)
    manifest_fingerprint = fingerprint(payload)
    dependency_fingerprint = fingerprint(dependency)
    input_fingerprint = fingerprint(
        {
            "manifestFingerprint": manifest_fingerprint,
            "dependencyFingerprint": dependency_fingerprint,
        }
    )
    assessment = _assess(payload, dependency)
    return {
        "mode": "preview",
        "sourceItemId": payload["sourceItemId"],
        "artifactId": payload["artifactId"],
        "documentType": payload["documentType"],
        "provider": payload["provider"],
        "model": payload["model"],
        "pipelineVersion": payload["pipelineVersion"],
        "manifestFingerprint": manifest_fingerprint,
        "dependencyFingerprint": dependency_fingerprint,
        "inputFingerprint": input_fingerprint,
        "assessment": assessment,
        "counts": {
            "layers": len(payload["layers"]),
            "segments": sum(len(layer["segments"]) for layer in payload["layers"]),
            "fields": len(payload["fields"]),
            "independentReadings": len(payload["independentReadings"]),
            "disagreements": assessment["comparison"]["disagreementCount"],
            "escalations": len(assessment["issues"]),
        },
        "comparison": assessment["comparison"],
        "impact": {
            "canonicalPersonsCreated": 0,
            "canonicalEventsCreated": 0,
            "canonicalRelationshipsCreated": 0,
            "identityDecisionsCreated": 0,
            "sourceRowsDeleted": 0,
            "machineReadingProposalsCreated": len(payload["layers"]),
        },
        "canApply": not assessment["blocking"],
    }


def reading_pipeline_preview(root: Path, manifest: Path) -> dict[str, Any]:
    payload = _validate_manifest(_load_manifest(manifest))
    initialize(root)
    with connect(root) as connection:
        require_tables(connection, REQUIRED_TABLES)
        connection.execute("PRAGMA query_only = ON")
        return _preview(connection, payload)


def apply_reading_pipeline(
    root: Path,
    manifest: Path,
    *,
    expected_fingerprint: str,
    actor: str,
    rationale: str,
) -> dict[str, Any]:
    payload = _validate_manifest(_load_manifest(manifest))
    expected_fingerprint = _required_text(expected_fingerprint, "expectedFingerprint")
    actor = _required_text(actor, "actor")
    rationale = _required_text(rationale, "rationale")
    if len(rationale) < 20:
        raise ReadingPipelineError("Uzasadnienie musi mieć co najmniej 20 znaków")
    initialize(root)
    now = utc_now()
    with connect(root) as connection:
        require_tables(connection, REQUIRED_TABLES)
        connection.execute("BEGIN IMMEDIATE")
        preview = _preview(connection, payload)
        request_fingerprint = fingerprint(
            {
                "commandId": payload["commandId"],
                "inputFingerprint": expected_fingerprint,
                "actor": actor,
                "rationale": rationale,
            }
        )
        try:
            replay = replay_command(
                connection,
                command_id=payload["commandId"],
                command_type="reading_pipeline_ingest",
                object_type="source_item",
                object_id=payload["sourceItemId"],
                request_fingerprint=request_fingerprint,
            )
        except CommandEnvelopeConflict as error:
            raise ReadingPipelineConflict(str(error)) from error
        if replay is not None:
            return {"mode": "apply", **replay}
        if preview["inputFingerprint"] != expected_fingerprint:
            raise ReadingPipelineConflict(
                "Źródło, artifact albo manifest zmieniły się po preview"
            )
        if not preview["canApply"]:
            raise ReadingPipelineConflict(
                "Manifest ma blokujący konflikt SHA-256 i nie może zostać zapisany"
            )
        run_id = stable_id(
            "reading_pipeline_run",
            payload["sourceItemId"],
            payload["artifactId"],
            preview["manifestFingerprint"],
        )
        existing = connection.execute(
            "SELECT command_id FROM reading_pipeline_runs WHERE id=?", (run_id,)
        ).fetchone()
        if existing:
            raise ReadingPipelineConflict(
                f"Ten manifest zapisano wcześniej przez commandId {existing['command_id']}"
            )
        assessment = preview["assessment"]
        connection.execute(
            """INSERT INTO reading_pipeline_runs(
                   id,source_item_id,artifact_id,provider,model,pipeline_version,
                   document_type,source_language,input_sha256,manifest_fingerprint,
                   dependency_fingerprint,status,completeness,issues_json,
                   metrics_json,provenance_json,comparison_json,image_quality_json,
                   role_expectations_json,independent_readings_json,command_id,created_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run_id,
                payload["sourceItemId"],
                payload["artifactId"],
                payload["provider"],
                payload["model"],
                payload["pipelineVersion"],
                payload["documentType"],
                payload["sourceLanguage"],
                payload["inputSha256"],
                preview["manifestFingerprint"],
                preview["dependencyFingerprint"],
                assessment["status"],
                assessment["completeness"],
                canonical_json(assessment["issues"]),
                canonical_json(payload["metrics"]),
                canonical_json(payload["provenance"]),
                canonical_json(assessment["comparison"]),
                canonical_json(payload["imageQuality"]),
                canonical_json(payload["roleExpectations"]),
                canonical_json(payload["independentReadings"]),
                payload["commandId"],
                now,
            ),
        )

        segment_ids: dict[tuple[str, str], str] = {}
        layer_rows: list[dict[str, Any]] = []
        for layer in payload["layers"]:
            layer_type = layer["type"]
            reading_id = stable_id("reading_pipeline_reading", run_id, layer_type)
            version_id = stable_id("reading_pipeline_version", run_id, layer_type, "1")
            layer_status = (
                "needs_review" if assessment["status"] == "review_required" else "machine_proposal"
            )
            metadata = {
                "pipelineRunId": run_id,
                "provider": payload["provider"],
                "model": payload["model"],
                "pipelineVersion": payload["pipelineVersion"],
                "documentType": payload["documentType"],
                "sourceLanguage": payload["sourceLanguage"],
                "sourceScript": payload.get("sourceScript"),
                "contextDisclosed": payload["provenance"].get("contextDisclosed"),
                "machineProposalOnly": True,
            }
            connection.execute(
                """INSERT INTO readings(
                       id,source_item_id,artifact_id,reading_type,status,
                       model_or_reviewer,created_at,metadata_json
                   ) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    reading_id,
                    payload["sourceItemId"],
                    payload["artifactId"],
                    f"pipeline_{layer_type}",
                    "draft",
                    f"{payload['provider']}:{payload['model']}",
                    now,
                    canonical_json(metadata),
                ),
            )
            connection.execute(
                """INSERT INTO reading_versions(
                       id,reading_id,version_number,status,payload_json,created_at
                   ) VALUES (?,?,1,?,?,?)""",
                (
                    version_id,
                    reading_id,
                    layer_status,
                    canonical_json({"segments": layer["segments"], "language": layer["language"]}),
                    now,
                ),
            )
            for segment in layer["segments"]:
                segment_id = stable_id(
                    "reading_pipeline_segment", run_id, layer_type, segment["id"]
                )
                region = {
                    "pipelineRunId": run_id,
                    "layerType": layer_type,
                    "locator": segment["locator"],
                    "bbox": segment["bbox"],
                    "certainty": segment["certainty"],
                }
                connection.execute(
                    """INSERT INTO transcript_segments(
                           id,reading_version_id,artifact_id,sequence_number,
                           language,page_label,region_json,text
                       ) VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        segment_id,
                        version_id,
                        payload["artifactId"],
                        segment["sequence"],
                        layer["language"],
                        segment["pageLabel"],
                        canonical_json(region),
                        segment["text"],
                    ),
                )
                segment_ids[(layer_type, segment["id"])] = segment_id
            layer_id = stable_id("reading_pipeline_layer", run_id, layer_type)
            connection.execute(
                """INSERT INTO reading_pipeline_layers(
                       id,run_id,layer_type,reading_id,reading_version_id,
                       language,status,created_at
                   ) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    layer_id,
                    run_id,
                    layer_type,
                    reading_id,
                    version_id,
                    layer["language"],
                    layer_status,
                    now,
                ),
            )
            layer_rows.append(
                {
                    "type": layer_type,
                    "readingId": reading_id,
                    "readingVersionId": version_id,
                    "segments": len(layer["segments"]),
                }
            )

        extract_version = next(
            row["readingVersionId"] for row in layer_rows if row["type"] == "extract"
        )
        issue_keys = {
            (issue["fieldPath"], issue["reasonCode"]) for issue in assessment["issues"]
        }
        field_rows: list[dict[str, Any]] = []
        for field in payload["fields"]:
            source_segment_id = (
                segment_ids.get(("transcription", field["sourceSegment"]))
                if field["sourceSegment"]
                else None
            )
            missing = not str(field.get("value") or "").strip()
            needs_review = any(key[0] == field["path"] for key in issue_keys)
            review_status = (
                "missing" if missing else "human_review_required" if needs_review else "machine_proposal"
            )
            field_reading_id = stable_id("reading_pipeline_field_reading", run_id, field["path"])
            notes = {
                "pipelineRunId": run_id,
                "normalizedValue": field["normalizedValue"],
                "role": field["role"],
                "confidence": field["confidence"],
                "sourceQuote": field["sourceQuote"],
                "bbox": field["bbox"],
                "reviewStatus": review_status,
                "materialAlternatives": field["materialAlternatives"],
                "normalizationBasis": field["normalizationBasis"],
                "notes": field["notes"],
                "critical": field["critical"],
                "criticality": field["criticality"],
                "groundingBasis": field["groundingBasis"],
                "characterAnalysis": field["characterAnalysis"],
                "graphemeEvidenceRefs": field["graphemeEvidenceRefs"],
                "languageFormulaAssessment": field["languageFormulaAssessment"],
            }
            connection.execute(
                """INSERT INTO field_readings(
                       id,reading_version_id,transcript_segment_id,field_name,
                       value_text,alternatives_json,reading_certainty,notes
                   ) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    field_reading_id,
                    extract_version,
                    source_segment_id,
                    field["path"],
                    field["value"],
                    canonical_json(field["alternatives"]),
                    field["certainty"],
                    canonical_json(notes),
                ),
            )
            pipeline_field_id = stable_id("reading_pipeline_field", run_id, field["path"])
            connection.execute(
                """INSERT INTO reading_pipeline_fields(
                       id,run_id,field_reading_id,field_path,role,normalized_value,
                       confidence,source_quote,source_segment_id,region_json,
                       review_status,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    pipeline_field_id,
                    run_id,
                    field_reading_id,
                    field["path"],
                    field["role"],
                    field["normalizedValue"],
                    field["confidence"],
                    field["sourceQuote"],
                    source_segment_id,
                    canonical_json(field["bbox"] or {}),
                    review_status,
                    now,
                ),
            )
            field_rows.append(
                {
                    "path": field["path"],
                    "fieldReadingId": field_reading_id,
                    "reviewStatus": review_status,
                }
            )

        escalation_rows: list[dict[str, Any]] = []
        for issue in assessment["issues"]:
            escalation_id = stable_id(
                "reading_pipeline_escalation",
                run_id,
                issue["fieldPath"] or "document",
                issue["reasonCode"],
            )
            connection.execute(
                """INSERT INTO reading_pipeline_escalations(
                       id,run_id,field_path,reason_code,priority,recommended_role,
                       details_json,created_at
                   ) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    escalation_id,
                    run_id,
                    issue["fieldPath"],
                    issue["reasonCode"],
                    issue["priority"],
                    issue["recommendedRole"],
                    canonical_json(issue["details"]),
                    now,
                ),
            )
            escalation_rows.append({"id": escalation_id, **issue})

        connection.execute(
            """INSERT INTO curation_queue(
                   id,object_type,object_id,reason_code,payload_json,status,created_at
               ) VALUES (?,?,?,?,?,'open',?)""",
            (
                stable_id("curation", "reading_pipeline_run", run_id, "reading_pipeline_review"),
                "reading_pipeline_run",
                run_id,
                "reading_pipeline_review",
                canonical_json(
                    {
                        "sourceItemId": payload["sourceItemId"],
                        "documentType": payload["documentType"],
                        "status": assessment["status"],
                        "completeness": assessment["completeness"],
                        "issueCount": len(escalation_rows),
                        "independentReadingCount": len(payload["independentReadings"]),
                        "disagreementCount": assessment["comparison"]["disagreementCount"],
                    }
                ),
                now,
            ),
        )

        result = {
            "runId": run_id,
            "sourceItemId": payload["sourceItemId"],
            "artifactId": payload["artifactId"],
            "commandId": payload["commandId"],
            "status": assessment["status"],
            "completeness": assessment["completeness"],
            "comparison": assessment["comparison"],
            "inputFingerprint": expected_fingerprint,
            "layers": layer_rows,
            "fields": field_rows,
            "escalations": escalation_rows,
            "canonicalPersonsCreated": 0,
            "canonicalEventsCreated": 0,
            "canonicalRelationshipsCreated": 0,
            "identityDecisionsCreated": 0,
            "sourceRowsDeleted": 0,
            "projectionStatus": "not_required",
        }
        record_command(
            connection,
            command_id=payload["commandId"],
            command_type="reading_pipeline_ingest",
            object_type="source_item",
            object_id=payload["sourceItemId"],
            input_fingerprint=expected_fingerprint,
            request_fingerprint=request_fingerprint,
            actor=actor,
            rationale=rationale,
            result=result,
            projection_status="not_required",
        )
        cursor = connection.execute(
            """INSERT INTO audit_log(
                   occurred_at,actor,action,object_type,object_id,
                   before_json,after_json,rationale
               ) VALUES (?,?,'reading_pipeline_ingest','reading_pipeline_run',?,NULL,?,?)""",
            (now, actor, run_id, canonical_json(result), rationale),
        )
        connection.commit()
        return {"mode": "apply", "auditId": int(cursor.lastrowid), **result}


def _field_map(payload: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for field in payload.get("fields", []):
        if not isinstance(field, dict) or not field.get("path"):
            continue
        value = field.get("normalizedValue")
        if value is None:
            value = field.get("value")
        if value is not None and str(value).strip():
            result[str(field["path"])] = search_fold(str(value))
    return result


def _layer_text(payload: dict[str, Any], layer_type: str) -> str:
    layers = payload.get("layers", [])
    if not isinstance(layers, list):
        return ""
    for layer in layers:
        if isinstance(layer, dict) and layer.get("type") == layer_type:
            segments = layer.get("segments", [])
            if isinstance(segments, list):
                return "\n".join(
                    str(segment.get("text") or "")
                    for segment in sorted(
                        (item for item in segments if isinstance(item, dict)),
                        key=lambda item: (item.get("sequence", 0), str(item.get("id") or "")),
                    )
                )
    return ""


def _edit_distance(left: str, right: str) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for left_index, left_character in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_character in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_character != right_character),
                )
            )
        previous = current
    return previous[-1]


def benchmark_reading_pipeline(candidate_manifest: Path, gold_manifest: Path) -> dict[str, Any]:
    candidate_raw = _load_manifest(candidate_manifest)
    gold_raw = _load_manifest(gold_manifest)
    candidate = _validate_manifest(candidate_raw)
    gold = _validate_manifest(gold_raw)
    candidate_fields = _field_map(candidate)
    gold_fields = _field_map(gold)
    correct = sorted(
        key for key, value in candidate_fields.items() if gold_fields.get(key) == value
    )
    incorrect = sorted(
        key for key, value in candidate_fields.items() if key in gold_fields and gold_fields[key] != value
    )
    missed = sorted(set(gold_fields) - set(candidate_fields))
    extra = sorted(set(candidate_fields) - set(gold_fields))
    precision = len(correct) / len(candidate_fields) if candidate_fields else 0.0
    recall = len(correct) / len(gold_fields) if gold_fields else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    candidate_text = _layer_text(candidate, "transcription")
    gold_text = _layer_text(gold, "transcription")
    character_error_rate = (
        _edit_distance(candidate_text, gold_text) / len(gold_text) if gold_text else 0.0
    )
    return {
        "candidate": str(candidate_manifest),
        "gold": str(gold_manifest),
        "documentType": candidate["documentType"],
        "fieldMetrics": {
            "correct": len(correct),
            "incorrect": len(incorrect),
            "missed": len(missed),
            "extra": len(extra),
            "precision": precision,
            "recall": recall,
            "f1": f1,
        },
        "fieldDetails": {
            "correct": correct,
            "incorrect": incorrect,
            "missed": missed,
            "extra": extra,
        },
        "transcription": {
            "candidateCharacters": len(candidate_text),
            "goldCharacters": len(gold_text),
            "characterErrorRate": character_error_rate,
        },
        "cost": candidate.get("metrics", {}),
        "sourceFirstWarning": (
            "Benchmark mierzy jakość propozycji. Nie promuje odczytu, osoby, "
            "zdarzenia ani relacji do stanu potwierdzonego."
        ),
    }


__all__ = [
    "ReadingPipelineConflict",
    "ReadingPipelineError",
    "apply_reading_pipeline",
    "benchmark_reading_pipeline",
    "reading_pipeline_preview",
]
