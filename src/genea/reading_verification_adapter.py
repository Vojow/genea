from __future__ import annotations

import hashlib
import hmac
import json
import re
from pathlib import Path
from typing import Any, Iterable

from .presentation.reading_verification import (
    PACKAGE_BINDING_SCHEMA,
    ReadingVerificationAudit,
    ReadingVerificationBinding,
    ReadingVerificationError,
    ReadingVerificationStoreError,
    ReadingVerificationSubmissions,
    resolve_audit_output,
)
from .reading_snippets import verify_reading_snippet_package


PROPOSAL_SCHEMA = "genea.reading-verification-pipeline-proposal/v1"
ADAPTER_VERSION = "genea-reading-verification-adapter-v1"


class ReadingVerificationAdapterError(ValueError):
    """A fail-closed validation error for the database-free adapter."""


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReadingVerificationAdapterError(f"Pole {label} musi być niepustym tekstem")
    return value.strip()


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise ReadingVerificationAdapterError(f"Nie można obliczyć SHA-256 {path}") from error
    return digest.hexdigest()


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReadingVerificationAdapterError(f"Nie można odczytać {label}: {error}") from error
    if not isinstance(payload, dict):
        raise ReadingVerificationAdapterError(f"{label} musi być obiektem JSON")
    return payload


def _exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ReadingVerificationAdapterError(
            f"{label} ma niepoprawny kształt; missing={missing}, extra={extra}"
        )


def _sha256_text(value: Any, label: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ReadingVerificationAdapterError(f"{label} musi być SHA-256")
    return value


def _proposal_bbox(value: Any, label: str) -> None:
    if not isinstance(value, dict):
        raise ReadingVerificationAdapterError(f"{label} musi być bbox")
    _exact_keys(value, {"x", "y", "width", "height"}, label)
    for key in ("x", "y", "width", "height"):
        item = value[key]
        if not isinstance(item, (int, float)) or isinstance(item, bool):
            raise ReadingVerificationAdapterError(f"{label}.{key} musi być liczbą")
        if item < 0 or (key in {"width", "height"} and item == 0):
            raise ReadingVerificationAdapterError(f"{label}.{key} ma niepoprawną wartość")


def _validate_proposal_shape(proposal: dict[str, Any]) -> None:
    _exact_keys(
        proposal,
        {
            "schema",
            "adapterVersion",
            "package",
            "run",
            "independentReading",
            "exportFingerprint",
        },
        "proposal",
    )
    if proposal.get("schema") != PROPOSAL_SCHEMA:
        raise ReadingVerificationAdapterError(
            f"Obsługiwana schema proposal to {PROPOSAL_SCHEMA}"
        )
    if proposal.get("adapterVersion") != ADAPTER_VERSION:
        raise ReadingVerificationAdapterError(
            f"Obsługiwana adapterVersion to {ADAPTER_VERSION}"
        )
    _sha256_text(proposal.get("exportFingerprint"), "exportFingerprint")

    package = proposal.get("package")
    run = proposal.get("run")
    reading = proposal.get("independentReading")
    if not all(isinstance(item, dict) for item in (package, run, reading)):
        raise ReadingVerificationAdapterError("Proposal nie zawiera package/run/independentReading")
    _exact_keys(
        package,
        {
            "packageId",
            "configFingerprint",
            "manifestSha256",
            "bindingHash",
            "neutralFieldMapSha256",
            "source",
            "fieldMap",
        },
        "proposal.package",
    )
    _required_text(package.get("packageId"), "package.packageId")
    for key in (
        "configFingerprint",
        "manifestSha256",
        "bindingHash",
        "neutralFieldMapSha256",
    ):
        _sha256_text(package.get(key), f"package.{key}")
    source = package.get("source")
    if not isinstance(source, dict):
        raise ReadingVerificationAdapterError("package.source musi być obiektem")
    _exact_keys(source, {"id", "sha256", "locator"}, "package.source")
    _required_text(source.get("id"), "package.source.id")
    _sha256_text(source.get("sha256"), "package.source.sha256")
    _required_text(source.get("locator"), "package.source.locator")
    field_map = package.get("fieldMap")
    if not isinstance(field_map, list) or not field_map:
        raise ReadingVerificationAdapterError("package.fieldMap musi być niepustą tablicą")
    for index, item in enumerate(field_map):
        if not isinstance(item, dict):
            raise ReadingVerificationAdapterError(f"package.fieldMap[{index}] musi być obiektem")
        _exact_keys(
            item,
            {
                "fieldId",
                "path",
                "sourceSegmentId",
                "locator",
                "bbox",
                "regionSha256",
            },
            f"package.fieldMap[{index}]",
        )
        field_id = _required_text(item.get("fieldId"), f"package.fieldMap[{index}].fieldId")
        if re.fullmatch(r"f[0-9]{3}", field_id) is None:
            raise ReadingVerificationAdapterError("fieldId musi mieć format fNNN")
        _required_text(item.get("path"), f"package.fieldMap[{index}].path")
        _required_text(item.get("sourceSegmentId"), f"package.fieldMap[{index}].sourceSegmentId")
        _required_text(item.get("locator"), f"package.fieldMap[{index}].locator")
        _proposal_bbox(item.get("bbox"), f"package.fieldMap[{index}].bbox")
        _sha256_text(item.get("regionSha256"), f"package.fieldMap[{index}].regionSha256")
    backing_by_path = {item["path"]: item for item in field_map}
    if len(backing_by_path) != len(field_map):
        raise ReadingVerificationAdapterError("package.fieldMap zawiera powtórzoną path")

    _exact_keys(
        run,
        {"runId", "readerId", "auditTailHash", "submissionTailHash"},
        "proposal.run",
    )
    _required_text(run.get("runId"), "run.runId")
    _required_text(run.get("readerId"), "run.readerId")
    _sha256_text(run.get("auditTailHash"), "run.auditTailHash", nullable=True)
    _sha256_text(run.get("submissionTailHash"), "run.submissionTailHash")

    _exact_keys(
        reading,
        {"id", "readerId", "runId", "contextDisclosed", "fields"},
        "proposal.independentReading",
    )
    _required_text(reading.get("id"), "independentReading.id")
    fields = reading.get("fields")
    if not isinstance(fields, list) or not fields:
        raise ReadingVerificationAdapterError("independentReading.fields musi być niepustą tablicą")
    expected_field_keys = {
        "path",
        "value",
        "normalizedValue",
        "confidence",
        "certainty",
        "alternatives",
        "sourceQuote",
        "sourceSegment",
        "bbox",
        "groundingBasis",
        "characterAnalysis",
        "graphemeEvidenceRefs",
        "languageFormulaAssessment",
    }
    for index, field in enumerate(fields):
        if not isinstance(field, dict):
            raise ReadingVerificationAdapterError(f"independentReading.fields[{index}] musi być obiektem")
        label = f"independentReading.fields[{index}]"
        _exact_keys(field, expected_field_keys, label)
        _required_text(field.get("path"), f"{label}.path")
        _required_text(field.get("value"), f"{label}.value")
        if any(field.get(key) is not None for key in ("normalizedValue", "confidence", "certainty", "sourceQuote")):
            raise ReadingVerificationAdapterError(f"{label} zawiera niedozwoloną interpretację blind")
        if field.get("alternatives") != [] or field.get("characterAnalysis") != []:
            raise ReadingVerificationAdapterError(f"{label} zawiera kontekstową analizę blind")
        _required_text(field.get("sourceSegment"), f"{label}.sourceSegment")
        _proposal_bbox(field.get("bbox"), f"{label}.bbox")
        if field.get("groundingBasis") != "source_segment_region":
            raise ReadingVerificationAdapterError(f"{label}.groundingBasis jest niepoprawne")
        evidence = field.get("graphemeEvidenceRefs")
        if not isinstance(evidence, list) or len(evidence) != 1 or not isinstance(evidence[0], dict):
            raise ReadingVerificationAdapterError(f"{label}.graphemeEvidenceRefs musi mieć jeden wpis")
        ref = evidence[0]
        _exact_keys(ref, {"sourceSegmentId", "locator", "sha256", "bbox", "scale"}, f"{label}.graphemeEvidenceRefs[0]")
        _required_text(ref.get("sourceSegmentId"), f"{label}.graphemeEvidenceRefs[0].sourceSegmentId")
        _required_text(ref.get("locator"), f"{label}.graphemeEvidenceRefs[0].locator")
        _sha256_text(ref.get("sha256"), f"{label}.graphemeEvidenceRefs[0].sha256")
        _proposal_bbox(ref.get("bbox"), f"{label}.graphemeEvidenceRefs[0].bbox")
        if ref.get("scale") != "field":
            raise ReadingVerificationAdapterError(f"{label}.graphemeEvidenceRefs[0].scale jest niepoprawne")
        if field.get("languageFormulaAssessment") != {"status": "not_assessed"}:
            raise ReadingVerificationAdapterError(f"{label}.languageFormulaAssessment jest niepoprawne")
        backing = backing_by_path.get(field["path"])
        if backing is None:
            raise ReadingVerificationAdapterError(f"{label}.path nie występuje w package.fieldMap")
        if (
            field["sourceSegment"] != backing["sourceSegmentId"]
            or field["bbox"] != backing["bbox"]
            or ref["sourceSegmentId"] != backing["sourceSegmentId"]
            or ref["locator"] != backing["locator"]
            or ref["sha256"] != backing["regionSha256"]
            or ref["bbox"] != backing["bbox"]
        ):
            raise ReadingVerificationAdapterError(
                f"{label} nie odpowiada backing fieldMap zweryfikowanego pakietu"
            )


def _verified_store_paths(
    root: Path, package_dir: Path, audit_path: Path
) -> tuple[Path, Path, Path]:
    audit = resolve_audit_output(root, package_dir, audit_path)
    submissions = resolve_audit_output(root, package_dir, audit.with_name("submissions.jsonl"))
    binding = resolve_audit_output(root, package_dir, audit.with_name("package-binding.json"))
    for label, path in (
        ("audit", audit),
        ("submissions", submissions),
        ("package binding", binding),
    ):
        if not path.is_file():
            raise ReadingVerificationAdapterError(
                f"Brak istniejącego store {label}; eksport nigdy go nie tworzy: {path}"
            )
    return audit, submissions, binding


def _field_map(manifest: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    raw_fields = manifest.get("fields")
    if not isinstance(raw_fields, list) or not raw_fields:
        raise ReadingVerificationAdapterError("Zweryfikowany pakiet nie zawiera pól")
    fields: list[dict[str, Any]] = []
    mapping: dict[str, str] = {}
    for index, raw_field in enumerate(raw_fields, start=1):
        if not isinstance(raw_field, dict):
            raise ReadingVerificationAdapterError("Manifest pakietu zawiera niepoprawne pole")
        field_id = f"f{index:03d}"
        path = _required_text(raw_field.get("path"), f"fields[{index - 1}].path")
        segment_id = _required_text(
            raw_field.get("sourceSegmentId"), f"fields[{index - 1}].sourceSegmentId"
        )
        region = raw_field.get("regions", {}).get("field")
        if not isinstance(region, dict):
            raise ReadingVerificationAdapterError(f"Pole {path} nie ma regionu field")
        bbox = region.get("bbox")
        sha256 = region.get("sha256")
        if not isinstance(bbox, dict) or not isinstance(sha256, str):
            raise ReadingVerificationAdapterError(
                f"Pole {path} nie ma backing bbox/SHA-256 regionu field"
            )
        fields.append(
            {
                "id": field_id,
                "path": path,
                "sourceSegmentId": segment_id,
                "locator": _required_text(raw_field.get("locator"), f"fields[{index - 1}].locator"),
                "bbox": bbox,
                "regionSha256": sha256,
            }
        )
        mapping[field_id] = segment_id
    return fields, mapping


def export_reading_verification_run(
    root: Path,
    package_dir: Path,
    audit_path: Path,
    *,
    reader_id: str,
) -> dict[str, Any]:
    """Export one verified blind run without creating stores or opening SQLite."""

    reader_id = _required_text(reader_id, "readerId")
    resolved_root = root.resolve()
    raw_package = package_dir if package_dir.is_absolute() else root.absolute() / package_dir
    try:
        manifest = verify_reading_snippet_package(resolved_root, raw_package)
        package = raw_package.resolve()
        audit, submissions, binding_path = _verified_store_paths(
            resolved_root, package, audit_path
        )
        fields, field_binding = _field_map(manifest)
        raw_binding = _load_json(binding_path, "package binding")
        run_id = _required_text(raw_binding.get("runId"), "binding.runId")
        expected_binding = {
            "schema": PACKAGE_BINDING_SCHEMA,
            "runId": run_id,
            "configFingerprint": _required_text(
                manifest.get("configFingerprint"), "configFingerprint"
            ),
            "manifestSha256": _sha256_file(package / "manifest.json"),
            "neutralFieldMapSha256": _canonical_hash(field_binding),
        }
        expected_binding["bindingHash"] = _canonical_hash(expected_binding)
        binding = ReadingVerificationBinding(
            binding_path, expected_binding, allow_create=False
        )
        field_ids = set(field_binding)
        audit_store = ReadingVerificationAudit(
            audit, run_id=run_id, field_ids=field_ids, binding=binding
        )
        submission_store = ReadingVerificationSubmissions(
            submissions, run_id=run_id, field_ids=field_ids, binding=binding
        )
        events, _ = audit_store.details()
        records = submission_store.records()
    except (ReadingVerificationError, ReadingVerificationStoreError) as error:
        raise ReadingVerificationAdapterError(str(error)) from error

    frozen = {
        (event["fieldId"], event["scale"]): event["submissionSha256"]
        for event in events
        if event["action"] == "freeze"
    }
    field_records = {
        record["fieldId"]: record for record in records if record["scale"] == "field"
    }
    if not field_records:
        raise ReadingVerificationAdapterError(
            "Run nie zawiera żadnego submission skali field"
        )
    export_fields: list[dict[str, Any]] = []
    for field in fields:
        record = field_records.get(field["id"])
        if record is None:
            continue
        expected_sha = frozen.get((field["id"], "field"))
        if expected_sha is None or not hmac.compare_digest(
            expected_sha, record["submissionSha256"]
        ):
            raise ReadingVerificationAdapterError(
                f"Submission {field['id']} nie odpowiada freeze skali field"
            )
        export_fields.append(
            {
                "path": field["path"],
                "value": record["literalReading"],
                "normalizedValue": None,
                "confidence": None,
                "certainty": None,
                "alternatives": [],
                "sourceQuote": None,
                "sourceSegment": field["sourceSegmentId"],
                "bbox": field["bbox"],
                "groundingBasis": "source_segment_region",
                "characterAnalysis": [],
                "graphemeEvidenceRefs": [
                    {
                        "sourceSegmentId": field["sourceSegmentId"],
                        "locator": field["locator"],
                        "sha256": field["regionSha256"],
                        "bbox": field["bbox"],
                        "scale": "field",
                    }
                ],
                "languageFormulaAssessment": {"status": "not_assessed"},
            }
        )

    source = manifest.get("source")
    if not isinstance(source, dict):
        raise ReadingVerificationAdapterError("Manifest pakietu nie zawiera source")
    proposal: dict[str, Any] = {
        "schema": PROPOSAL_SCHEMA,
        "adapterVersion": ADAPTER_VERSION,
        "package": {
            "packageId": _required_text(manifest.get("packageId"), "packageId"),
            "configFingerprint": expected_binding["configFingerprint"],
            "manifestSha256": expected_binding["manifestSha256"],
            "bindingHash": expected_binding["bindingHash"],
            "neutralFieldMapSha256": expected_binding["neutralFieldMapSha256"],
            "source": {
                "id": _required_text(source.get("id"), "source.id"),
                "sha256": _required_text(source.get("sha256"), "source.sha256").lower(),
                "locator": _required_text(source.get("locator"), "source.locator"),
            },
            "fieldMap": [
                {
                    "fieldId": field["id"],
                    "path": field["path"],
                    "sourceSegmentId": field["sourceSegmentId"],
                    "locator": field["locator"],
                    "bbox": field["bbox"],
                    "regionSha256": field["regionSha256"],
                }
                for field in fields
            ],
        },
        "run": {
            "runId": run_id,
            "readerId": reader_id,
            "auditTailHash": events[-1]["eventHash"] if events else None,
            "submissionTailHash": records[-1]["submissionRecordHash"] if records else None,
        },
        "independentReading": {
            "id": f"verification-{run_id}",
            "readerId": reader_id,
            "runId": run_id,
            "contextDisclosed": False,
            "fields": export_fields,
        },
    }
    proposal["exportFingerprint"] = _canonical_hash(proposal)
    return proposal


def _validated_proposal(path: Path) -> dict[str, Any]:
    proposal = _load_json(path, "propozycji adaptera")
    _validate_proposal_shape(proposal)
    supplied = proposal.get("exportFingerprint")
    fingerprint_input = dict(proposal)
    fingerprint_input.pop("exportFingerprint", None)
    if not isinstance(supplied, str) or not hmac.compare_digest(
        supplied, _canonical_hash(fingerprint_input)
    ):
        raise ReadingVerificationAdapterError("exportFingerprint propozycji jest niepoprawny")
    package = proposal.get("package")
    run = proposal.get("run")
    reading = proposal.get("independentReading")
    if not all(isinstance(item, dict) for item in (package, run, reading)):
        raise ReadingVerificationAdapterError("Proposal nie zawiera package/run/independentReading")
    if reading.get("readerId") != run.get("readerId") or reading.get("runId") != run.get("runId"):
        raise ReadingVerificationAdapterError("Proposal ma niespójne readerId/runId")
    if reading.get("contextDisclosed") is not False:
        raise ReadingVerificationAdapterError(
            "Proposal adaptera musi pochodzić z blind runu bez ujawnionego kontekstu"
        )
    return proposal


def compose_reading_verification_proposal(
    manifest_path: Path, proposal_paths: Iterable[Path]
) -> dict[str, Any]:
    """Append at least two compatible blind exports to a pipeline manifest."""

    manifest = _load_json(manifest_path, "manifestu reading pipeline")
    if manifest.get("schemaVersion") != 2:
        raise ReadingVerificationAdapterError(
            "Compose wymaga manifestu reading pipeline schemaVersion 2"
        )
    proposals = [_validated_proposal(path) for path in proposal_paths]
    if len(proposals) < 2:
        raise ReadingVerificationAdapterError(
            "Compose wymaga co najmniej dwóch proposal z niezależnych runów"
        )
    run_ids = [_required_text(item["run"].get("runId"), "run.runId") for item in proposals]
    reader_ids = [
        _required_text(item["run"].get("readerId"), "run.readerId") for item in proposals
    ]
    if len(set(run_ids)) != len(run_ids) or len(set(reader_ids)) != len(reader_ids):
        raise ReadingVerificationAdapterError(
            "Compose wymaga różnych runId i readerId dla każdego blindu"
        )

    first_package = proposals[0]["package"]
    package_identity_keys = (
        "packageId",
        "configFingerprint",
        "manifestSha256",
        "neutralFieldMapSha256",
        "source",
        "fieldMap",
    )
    expected_identity = {key: first_package.get(key) for key in package_identity_keys}
    for proposal in proposals[1:]:
        actual = {key: proposal["package"].get(key) for key in package_identity_keys}
        if actual != expected_identity:
            raise ReadingVerificationAdapterError(
                "Nie można komponować runów z różnych package/config/binding/source/map"
            )

    source = first_package.get("source")
    if not isinstance(source, dict) or manifest.get("inputSha256") != source.get("sha256"):
        raise ReadingVerificationAdapterError(
            "inputSha256 manifestu nie odpowiada źródłu zweryfikowanego package"
        )
    manifest_fields = manifest.get("fields")
    if not isinstance(manifest_fields, list):
        raise ReadingVerificationAdapterError("Manifest reading pipeline nie ma fields")
    backing = {
        item.get("path"): item.get("sourceSegment")
        for item in manifest_fields
        if isinstance(item, dict)
    }
    package_map = first_package.get("fieldMap")
    if not isinstance(package_map, list) or any(
        not isinstance(item, dict)
        or backing.get(item.get("path")) != item.get("sourceSegmentId")
        for item in package_map
    ):
        raise ReadingVerificationAdapterError(
            "Mapowanie path/sourceSegmentId package nie odpowiada manifestowi pipeline"
        )

    existing = manifest.get("independentReadings", [])
    if not isinstance(existing, list):
        raise ReadingVerificationAdapterError("independentReadings manifestu musi być tablicą")
    existing_ids = {
        item.get("id") for item in existing if isinstance(item, dict)
    }
    existing_readers = {
        item.get("readerId") for item in existing if isinstance(item, dict)
    }
    existing_runs = {
        item.get("runId")
        for item in existing
        if isinstance(item, dict) and item.get("runId") is not None
    }
    additions = [item["independentReading"] for item in proposals]
    if any(
        reading.get("id") in existing_ids
        or reading.get("readerId") in existing_readers
        or reading.get("runId") in existing_runs
        for reading in additions
    ):
        raise ReadingVerificationAdapterError(
            "Compose nie może nadpisać istniejącego independentReading"
        )

    composed = dict(manifest)
    composed["independentReadings"] = [*existing, *sorted(additions, key=lambda item: item["id"])]
    provenance = manifest.get("provenance", {})
    if not isinstance(provenance, dict):
        raise ReadingVerificationAdapterError("provenance manifestu musi być obiektem")
    provenance = dict(provenance)
    provenance["readingVerificationAdapter"] = {
        "adapterVersion": ADAPTER_VERSION,
        "packageId": first_package["packageId"],
        "configFingerprint": first_package["configFingerprint"],
        "manifestSha256": first_package["manifestSha256"],
        "bindingHashes": sorted(item["package"]["bindingHash"] for item in proposals),
        "neutralFieldMapSha256": first_package["neutralFieldMapSha256"],
        "exportFingerprints": sorted(item["exportFingerprint"] for item in proposals),
        "runIds": sorted(run_ids),
        "readerIds": sorted(reader_ids),
    }
    composed["provenance"] = provenance
    provenance["readingVerificationAdapter"]["compositionFingerprint"] = _canonical_hash(
        composed
    )
    return composed


__all__ = [
    "ADAPTER_VERSION",
    "PROPOSAL_SCHEMA",
    "ReadingVerificationAdapterError",
    "compose_reading_verification_proposal",
    "export_reading_verification_run",
]
