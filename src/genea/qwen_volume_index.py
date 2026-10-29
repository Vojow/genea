"""Local-only Qwen page recognition; all writes belong to a derivative index.

The model's uncertainty flag is not a confidence score or human review. PAGE
XML is only an interchange format for the existing auxiliary region importer.
"""
from __future__ import annotations

import base64
import hashlib
import http.client
import ipaddress
import json
import math
import os
import shutil
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlsplit

from .volume_index import (
    VOLUME_INDEX_SCHEMA, _build_sqlite, _dimensions, _open_index,
    _output_lock, _validate_output,
)
from .volume_index_workflow import (
    _local_tool, _mutation_lock, _refresh_checksums, _unit,
    import_recognition_attempt,
)
from .workspace import sha256_file, utc_now


PROMPT = (
    "Wykonaj wierną, dosłowną transkrypcję wszystkich widocznych wierszy rękopisu. "
    "Zachowaj język, kolejność, oryginalną pisownię, diakrytykę i skróty. "
    "Nie tłumacz, nie normalizuj nazwisk i nie uzupełniaj treści z kontekstu. "
    "Gdy fragmentu nie można odczytać, wpisz [nieczytelne]. "
    "Dla każdego fizycznego wiersza zwróć osobny obiekt. "
    "bbox_2d podaj jako [x1,y1,x2,y2] w skali 0-1000 względem całego obrazu. "
    "uncertain ma być true, jeśli choć jeden znak w wierszu jest niepewny."
)
DEFAULT_OPTIONS = {"temperature": 0, "seed": 42, "num_predict": 4096}
OUTPUT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["lines"],
    "properties": {"lines": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["text_raw", "bbox_2d", "uncertain"],
        "properties": {
            "text_raw": {"type": "string", "minLength": 1},
            "bbox_2d": {"type": "array", "minItems": 4, "maxItems": 4,
                        "items": {"type": "number", "minimum": 0, "maximum": 1000}},
            "uncertain": {"type": "boolean"},
        },
    }}},
}
SCOPE = "ocr_auxiliary_collect_only"


def prepare_qwen_index(source_index: Path, output: Path, ordinals: list[int], *, project_root: Path) -> dict:
    """Create an isolated empty index over verified copies of existing renders."""
    source, connection = _open_index(source_index)
    output = output.expanduser().resolve()
    if not ordinals or len(set(ordinals)) != len(ordinals):
        connection.close()
        raise ValueError("Wymagana niepusta lista unikalnych ordinali")
    try:
        units = {ordinal: dict(_unit(connection, ordinal)) for ordinal in ordinals}
    finally:
        connection.close()
    manifest_path = source / "manifest.json"
    manifest = _strict_json(manifest_path.read_text(encoding="utf-8"))
    selected = [row for row in manifest if row["ordinal"] in units]
    if len(selected) != len(units) or {row["ordinal"] for row in selected} != set(units):
        raise ValueError("Manifest nie zawiera jednoznacznie wszystkich wybranych stron")
    for row in selected:
        unit = units[row["ordinal"]]
        if any(row[key] != unit[key] for key in (
                "unit_id", "render_path", "render_sha256", "width", "height", "input_sha256")):
            raise ValueError("Manifest i indeks źródłowy są niezgodne")
        image = (source / row["render_path"]).resolve()
        if (Path(row["render_path"]).is_absolute() or ".." in Path(row["render_path"]).parts
                or not image.is_relative_to(source) or not image.is_file()
                or sha256_file(image) != row["render_sha256"]):
            raise ValueError("Niepoprawny render bazowego indeksu")
    identity = {"source_index": str(source), "source_manifest_sha256": sha256_file(manifest_path),
                "source_build_sha256": sha256_file(source / "build.json"), "ordinals": sorted(ordinals),
                "manifest_subset_sha256": _sha(_canonical(selected).encode()),
                "render_reuse": "verified_copy", "scope": SCOPE}
    if output.exists():
        root, existing = _open_qwen_index(output)
        try:
            build = _strict_json((root / "build.json").read_text(encoding="utf-8"))
            if (build.get("qwen_source") != identity
                    or _strict_json((root / "manifest.json").read_text(encoding="utf-8")) != selected):
                raise ValueError("Istniejący OUTPUT nie odpowiada temu indeksowi i zakresowi")
            if existing.execute("SELECT COUNT(*) FROM units").fetchone()[0] != len(selected):
                raise ValueError("Zakres istniejącego OUTPUT jest niezgodny")
            for row in selected:
                cached = dict(_unit(existing, row["ordinal"]))
                if cached != units[row["ordinal"]] or sha256_file(root / row["render_path"]) != row["render_sha256"]:
                    raise ValueError("Istniejący OUTPUT nie przeszedł kontroli jednostek/renderów")
        finally:
            existing.close()
        return {"status": "reused_qwen_index", "output": str(output), "page_count": len(selected)}
    _validate_output(source, output, project_root.expanduser().resolve())
    output.parent.mkdir(parents=True, exist_ok=True)
    with _output_lock(output):
        working = output.parent / f".{output.name}.qwen-init-{uuid.uuid4().hex}"
        working.mkdir()
        for row in selected:
            destination = working / row["render_path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / row["render_path"], destination)
            if sha256_file(destination) != row["render_sha256"]:
                raise ValueError("Render zmienił się podczas inicjalizacji")
        _new_json(working / "manifest.json", selected)
        _new_json(working / "build.json", {
            "schema": VOLUME_INDEX_SCHEMA, "scope": SCOPE, "qwen_source": identity,
            "config_fingerprint": _sha(_canonical(identity).encode()), "created_at": utc_now(),
            "summary": {"unit_count": len(selected), "region_count": 0},
            "warning": "Pomocniczy indeks Qwen nie jest transkrypcją źródłową ani pokryciem badań.",
        })
        _build_sqlite(working / "volume-index.sqlite3", selected, [])
        with (working / "regions.jsonl").open("x", encoding="utf-8"):
            pass
        with (working / "report.md").open("x", encoding="utf-8") as stream:
            stream.write("# Pomocniczy indeks Qwen\n\nStan regionów i prób: volume-index-report / przeszukiwanie.\n"
                         "Source != proof. Odczyty wymagają kontroli obrazu; brak trafień nie jest negative evidence.\n")
        _refresh_checksums(working)
        if output.exists():
            raise ValueError("OUTPUT pojawił się podczas inicjalizacji")
        working.rename(output)
    return {"status": "qwen_index_prepared", "output": str(output), "page_count": len(selected)}


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _open_qwen_index(index_dir: Path):
    """Reject a base/general index before any mutation or inference request."""
    root, connection = _open_index(index_dir)
    try:
        build = _strict_json((root / "build.json").read_text(encoding="utf-8"))
        identity = build.get("qwen_source") if isinstance(build, dict) else None
        fields = {"source_index", "source_manifest_sha256", "source_build_sha256", "ordinals",
                  "manifest_subset_sha256", "render_reuse", "scope"}
        if (not isinstance(identity, dict) or set(identity) != fields
                or build.get("schema") != VOLUME_INDEX_SCHEMA or build.get("scope") != SCOPE
                or identity.get("scope") != SCOPE or identity.get("render_reuse") != "verified_copy"
                or build.get("config_fingerprint") != _sha(_canonical(identity).encode())):
            raise ValueError("Wymagany izolowany Qwen OUTPUT; przygotuj go przez --source-index")
        source = Path(identity["source_index"]).expanduser().resolve()
        if root == source or root.is_relative_to(source):
            raise ValueError("Qwen OUTPUT musi być odseparowany od BASE")
        source_database = source / "volume-index.sqlite3"
        if source_database.is_file() and (root / "volume-index.sqlite3").samefile(source_database):
            raise ValueError("SQLite Qwen OUTPUT musi być fizycznie odseparowany od BASE")
        manifest = _strict_json((root / "manifest.json").read_text(encoding="utf-8"))
        if (not isinstance(manifest, list)
                or _sha(_canonical(manifest).encode()) != identity["manifest_subset_sha256"]
                or sorted(row["ordinal"] for row in manifest) != identity["ordinals"]
                or len(set(identity["ordinals"])) != len(manifest)):
            raise ValueError("Manifest Qwen OUTPUT nie odpowiada zapisanej tożsamości")
        if connection.execute("SELECT COUNT(*) FROM units").fetchone()[0] != len(manifest):
            raise ValueError("Zakres SQLite Qwen OUTPUT nie odpowiada manifestowi")
        for row in manifest:
            unit = _unit(connection, row["ordinal"])
            if (any(row[field] != unit[field] for field in (
                    "unit_id", "render_path", "render_sha256", "width", "height", "input_sha256",
                    "source_relative_path", "source_frame", "pdf_page", "source_fingerprint", "classification"))
                    or row["stable_locator"] != unit["locator"]):
                raise ValueError("Jednostka SQLite Qwen OUTPUT nie odpowiada manifestowi")
        return root, connection
    except Exception:
        connection.close()
        raise


def _strict_json(payload: str) -> object:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Powtórzony klucz JSON: {key}")
            result[key] = value
        return result

    def invalid(value):
        raise ValueError(f"Niedozwolona wartość JSON: {value}")

    return json.loads(payload, object_pairs_hook=pairs, parse_constant=invalid)


def parse_qwen_lines(payload: str, width: int, height: int) -> list[dict]:
    """Validate the exact model contract, without repair, clipping or guessing."""
    if width <= 0 or height <= 0:
        raise ValueError("Wymiary obrazu muszą być dodatnie")
    value = _strict_json(payload)
    if not isinstance(value, dict) or set(value) != {"lines"} or not isinstance(value["lines"], list):
        raise ValueError("Oczekiwano obiektu JSON zawierającego wyłącznie lines")
    lines = []
    for line in value["lines"]:
        if not isinstance(line, dict) or set(line) != {"text_raw", "bbox_2d", "uncertain"}:
            raise ValueError("Wiersz wymaga text_raw, bbox_2d i uncertain")
        if not isinstance(line["text_raw"], str) or not line["text_raw"].strip():
            raise ValueError("text_raw musi być niepustym tekstem")
        if type(line["uncertain"]) is not bool:
            raise ValueError("uncertain musi być boolean")
        bbox = line["bbox_2d"]
        if (not isinstance(bbox, list) or len(bbox) != 4
                or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1000 for v in bbox)
                or bbox[0] >= bbox[2] or bbox[1] >= bbox[3]):
            raise ValueError("Niepoprawny bbox_2d: wymagany dodatni prostokąt w skali 0–1000")
        lines.append({**line, "bbox": [bbox[0] * width / 1000, bbox[1] * height / 1000,
                                     bbox[2] * width / 1000, bbox[3] * height / 1000]})
    return lines


class LocalOllama:
    """Literal loopback HTTP only: no proxies, redirects, downloads or fallback."""

    def __init__(self, endpoint: str = "http://127.0.0.1:11434", timeout: float = 600):
        parsed = urlsplit(endpoint)
        host = parsed.hostname
        if host == "localhost":
            host = "127.0.0.1"
        try:
            local = ipaddress.ip_address(host or "").is_loopback
        except ValueError:
            local = False
        if (parsed.scheme != "http" or not local or parsed.username or parsed.password
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise ValueError("Ollama wymaga lokalnego adresu loopback HTTP")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout musi być dodatni")
        self.host, self.port, self.timeout = host, parsed.port or 11434, timeout

    def request(self, route: str, payload: dict | None = None) -> bytes:
        connection = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        try:
            connection.request("GET" if payload is None else "POST", route,
                               body=None if payload is None else _canonical(payload).encode("utf-8"),
                               headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            body = response.read()
            if response.status != 200:
                error = RuntimeError(f"Lokalny Ollama {route}: HTTP {response.status}")
                error.response_body = body
                raise error
            return body
        except (OSError, http.client.HTTPException) as error:
            raise RuntimeError(f"Lokalny Ollama {route}: {error}") from error
        finally:
            connection.close()

    def info(self, model: str) -> dict:
        if not model or "cloud" in model.lower():
            raise ValueError("Model cloud jest zabroniony")
        version = _strict_json(self.request("/api/version").decode())
        tags = _strict_json(self.request("/api/tags").decode())
        if not isinstance(version, dict) or not isinstance(tags, dict) or not isinstance(tags.get("models"), list):
            raise ValueError("Niepoprawne metadane lokalnego Ollama")
        tag = next((row for row in tags.get("models", []) if row.get("name") == model), None)
        if not tag or not tag.get("digest"):
            raise ValueError("Wymagany jawny tag modelu z digestem zainstalowany lokalnie")
        show = _strict_json(self.request("/api/show", {"model": model}).decode())
        if not isinstance(show, dict):
            raise ValueError("Niepoprawne metadane modelu Ollama")
        if any(record.get(field) for record in (tag, show) for field in ("remote_host", "remote_model")):
            raise ValueError("Model delegowany do cloud/hosted jest zabroniony")
        architecture = show.get("model_info", {}).get("general.architecture", "")
        if architecture not in {"qwen3vl", "qwen3vlmoe"} or "vision" not in show.get("capabilities", []):
            raise ValueError("Wymagany lokalny model Qwen3-VL z vision")
        if not version.get("version"):
            raise ValueError("Brak wersji lokalnego runtime")
        return {"model_tag": model, "model_digest": tag["digest"],
                "runtime_version": version["version"], "model_details": tag.get("details"),
                "model_show": show}


def _new_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        stream.write(_canonical(value) + "\n")


def _publish_completion(path: Path, value: object) -> None:
    """Publish a fully flushed marker atomically; interrupted temps remain auditable."""
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        stream.write(_canonical(value) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    if path.exists():
        raise FileExistsError(f"Nie nadpisuję checkpointu: {path}")
    os.replace(temporary, path)


def _page_xml(lines: list[dict], image: str, width: int, height: int, fingerprint: str) -> bytes:
    namespace = "http://schema.primaresearch.org/PAGE/gts/pagecontent/2019-07-15"
    ET.register_namespace("", namespace)
    def child(parent, name, attrs=None):
        return ET.SubElement(parent, f"{{{namespace}}}{name}", attrs or {})
    root = ET.Element(f"{{{namespace}}}PcGts")
    metadata = child(root, "Metadata")
    child(metadata, "Creator").text = "Genea local Qwen3-VL auxiliary adapter"
    child(metadata, "Comments").text = f"fingerprint={fingerprint}; {SCOPE}; auxiliary_unreviewed"
    page = child(root, "Page", {"imageFilename": image, "imageWidth": str(width), "imageHeight": str(height)})
    region = child(page, "TextRegion", {"id": "region_1"})
    child(region, "Coords", {"points": f"0,0 {width},0 {width},{height} 0,{height}"})
    for index, line in enumerate(lines, 1):
        node = child(region, "TextLine", {"id": f"line_{index}",
                                          "custom": f"qwen {{uncertain:{str(line['uncertain']).lower()};}}"})
        x1, y1, x2, y2 = line["bbox"]
        # PAGE Coords are integer pixels; round outward to preserve the extent.
        x1, y1, x2, y2 = math.floor(x1), math.floor(y1), math.ceil(x2), math.ceil(y2)
        child(node, "Coords", {"points": f"{x1},{y1} {x2},{y1} {x2},{y2} {x1},{y2}"})
        child(child(node, "TextEquiv"), "Unicode").text = line["text_raw"]
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _verified_completed(directory: Path) -> Path | None:
    completed = sorted(directory.glob("*/complete.json"))
    for marker in completed:
        expected = {"raw-response.json", "provenance.json", "lines.json", "page.xml"}
        try:
            record = _strict_json(marker.read_text(encoding="utf-8"))
            if (not isinstance(record, dict) or not isinstance(record.get("checksums"), dict)
                    or set(record["checksums"]) != expected
                    or any(not isinstance(value, str) or len(value) != 64
                           or any(char not in "0123456789abcdef" for char in value)
                           for value in record["checksums"].values())):
                raise ValueError("Niepełny checkpoint")
        except (ValueError, UnicodeError) as error:
            rejected = marker.parent / "checkpoint-rejected.json"
            if not rejected.exists():
                _new_json(rejected, {"status": "invalid_checkpoint_not_imported",
                                     "marker_sha256": sha256_file(marker), "reason": str(error),
                                     "created_at": utc_now()})
            continue
        for name, digest in record["checksums"].items():
            artifact = marker.parent / name
            if not artifact.is_file() or sha256_file(artifact) != digest:
                raise RuntimeError(f"Artefakt nie przeszedł kontroli integralności: {artifact}")
        provenance = _strict_json((marker.parent / "provenance.json").read_text(encoding="utf-8"))
        if provenance.get("fingerprint") != directory.name:
            raise RuntimeError(f"Niezgodny fingerprint checkpointu: {marker}")
        return marker.parent
    return None


def recognize_qwen_volume(
    index_dir: Path, ordinals: list[int], *, model: str,
    endpoint: str = "http://127.0.0.1:11434", options: dict | None = None,
    prompt: str = PROMPT, timeout: float = 600,
) -> dict:
    """Resume a bounded batch, checkpointing each validated page before import.

    Raw failures are retained in separate immutable runs. An interrupted import
    reuses the validated PAGE artifact without invoking the model again.
    """
    if not ordinals or len(set(ordinals)) != len(ordinals):
        raise ValueError("Wymagana niepusta lista unikalnych ordinali")
    if not prompt.strip():
        raise ValueError("Prompt nie może być pusty")
    if options is not None and not isinstance(options, dict):
        raise ValueError("Opcje generacji muszą być obiektem JSON")
    selected_options = dict(DEFAULT_OPTIONS if options is None else options)
    _canonical(selected_options)
    client = LocalOllama(endpoint, timeout)
    root, connection = _open_qwen_index(index_dir)
    try:
        units = [dict(_unit(connection, ordinal)) for ordinal in ordinals]
    finally:
        connection.close()
    # Do not create a separate output outside an existing derivative index.
    batch_root = root / "attempts" / "qwen"
    batch_root.mkdir(parents=True, exist_ok=True)
    results = []
    with _mutation_lock(batch_root):
        info = client.info(model)
        for unit in units:
            image = (root / unit["render_path"]).resolve()
            if not image.is_relative_to(root) or not image.is_file() or sha256_file(image) != unit["render_sha256"]:
                raise RuntimeError("Render nie przeszedł kontroli ścieżki/SHA256")
            width, height = _dimensions(_local_tool("magick"), image)
            if (width, height) != (unit["width"], unit["height"]):
                raise RuntimeError("Wymiary renderu nie zgadzają się z manifestem")
            identity = {
                "adapter_schema": "genea.qwen-volume/v1", "unit_id": unit["unit_id"],
                "ordinal": unit["ordinal"], "locator": unit["locator"],
                "source_sha256": unit["input_sha256"], "image_sha256": unit["render_sha256"],
                "width": width, "height": height, "prompt": prompt,
                "prompt_sha256": _sha(prompt.encode()), "generation_config": selected_options,
                "output_schema": OUTPUT_SCHEMA, "stream": False, "think": False,
                "model_tag": info["model_tag"], "model_digest": info["model_digest"],
                "runtime_version": info["runtime_version"],
                "model_show_sha256": _sha(_canonical(info["model_show"]).encode()),
            }
            fingerprint = _sha(_canonical(identity).encode())
            directory = batch_root / fingerprint
            artifact_dir = _verified_completed(directory)
            reused = artifact_dir is not None
            if artifact_dir is None:
                artifact_dir = directory / uuid.uuid4().hex
                artifact_dir.mkdir(parents=True)
                start = time.monotonic()
                metadata = {**identity, "fingerprint": fingerprint, "created_at": utc_now(),
                            "status": "auxiliary_unreviewed", "scope": SCOPE,
                            "image_path": unit["render_path"], "model_info": info,
                            "endpoint": endpoint}
                _new_json(artifact_dir / "request.json", metadata)
                try:
                    image_bytes = image.read_bytes()
                    if _sha(image_bytes) != unit["render_sha256"]:
                        raise RuntimeError("Render zmienił się przed żądaniem")
                    response = client.request("/api/chat", {
                        "model": model, "messages": [{"role": "user", "content": prompt,
                            "images": [base64.b64encode(image_bytes).decode("ascii")]}],
                        "options": selected_options, "format": OUTPUT_SCHEMA,
                        "stream": False, "think": False,
                    })
                    with (artifact_dir / "raw-response.json").open("xb") as stream:
                        stream.write(response)
                    envelope = _strict_json(response.decode("utf-8"))
                    if (not isinstance(envelope, dict)
                            or envelope.get("done") is not True or envelope.get("done_reason") != "stop"
                            or envelope.get("model") != model):
                        raise ValueError("Odpowiedź jest ucięta, nieukończona lub pochodzi z innego modelu")
                    message = envelope.get("message")
                    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
                        raise ValueError("Brak tekstowej treści odpowiedzi modelu")
                    lines = parse_qwen_lines(message["content"], width, height)
                    # A mutable local tag must still denote the same model at completion.
                    if client.info(model) != info:
                        raise RuntimeError("Model/runtime zmienił się w trakcie rozpoznawania")
                    telemetry = {key: value for key, value in envelope.items()
                                 if key.endswith("_duration") or key.endswith("_count") or key in {"done", "done_reason", "created_at"}}
                    metadata.update(elapsed_seconds=time.monotonic() - start, telemetry=telemetry)
                    _new_json(artifact_dir / "provenance.json", metadata)
                    _new_json(artifact_dir / "lines.json", {"lines": lines})
                    with (artifact_dir / "page.xml").open("xb") as stream:
                        stream.write(_page_xml(lines, unit["render_path"], width, height, fingerprint))
                    _publish_completion(artifact_dir / "complete.json", {"checksums": {
                        name: sha256_file(artifact_dir / name)
                        for name in ("raw-response.json", "provenance.json", "lines.json", "page.xml")}})
                except Exception as error:
                    if hasattr(error, "response_body") and not (artifact_dir / "raw-response.json").exists():
                        with (artifact_dir / "raw-response.json").open("xb") as stream:
                            stream.write(error.response_body)
                    _new_json(artifact_dir / "failure.json", {
                        "status": "failed_auxiliary_attempt", "error": str(error),
                        "elapsed_seconds": time.monotonic() - start, "created_at": utc_now()})
                    raise
            provenance = {"fingerprint": fingerprint, "scope": SCOPE,
                          "path": str((artifact_dir / "provenance.json").relative_to(root)),
                          "sha256": sha256_file(artifact_dir / "provenance.json")}
            result = import_recognition_attempt(
                root, unit["ordinal"], artifact_dir / "page.xml", backend="qwen3-vl-ollama",
                model=model, format_type="page", provenance=provenance,
                backend_version=info["runtime_version"],
            )
            # Import is idempotent too, including crash recovery after SQLite commit.
            results.append({**result, "ordinal": unit["ordinal"], "fingerprint": fingerprint,
                            "reused_generation": reused, "provenance": provenance})
    with _mutation_lock(root):
        _refresh_checksums(root)
    return {"status": "auxiliary_unreviewed", "scope": SCOPE, "pages": results,
            "page_count": len(results), "reused_pages": sum(row["reused_generation"] for row in results)}


def import_qwen_diagnostic(
    index_dir: Path, ordinal: int, result_file: Path, *,
    expected_response_sha256: str, expected_image_sha256: str,
    recovery_metadata: dict,
) -> dict:
    """Import an existing diagnostic without rerunning inference or inventing history.

    The caller supplies independently retained hashes and describes how runtime
    and model metadata were recovered. This is deliberately a distinct attempt
    fingerprint, not a retroactively frozen native adapter run.
    """
    for field in ("runtime_version", "model_tag", "model_blob_sha256", "recovery_note"):
        if not isinstance(recovery_metadata.get(field), str) or not recovery_metadata[field].strip():
            raise ValueError(f"Brak jawnej metadanej odzyskania: {field}")
    payload = result_file.read_bytes()
    if _sha(payload) != expected_response_sha256:
        raise ValueError("SHA256 diagnostycznej odpowiedzi jest niezgodny")
    diagnostic = _strict_json(payload.decode("utf-8"))
    if (diagnostic.get("page") != ordinal or diagnostic.get("model") != recovery_metadata["model_tag"]
            or diagnostic.get("ollama", {}).get("done_reason") != "stop"):
        raise ValueError("Niezgodny ordinal/model lub ucięta odpowiedź diagnostyczna")
    if not isinstance(diagnostic.get("prompt"), str) or not diagnostic["prompt"].strip():
        raise ValueError("Brak oryginalnego promptu")
    if not isinstance(diagnostic.get("options"), dict):
        raise ValueError("Brak oryginalnych opcji generacji")
    root, connection = _open_qwen_index(index_dir)
    try:
        unit = dict(_unit(connection, ordinal))
    finally:
        connection.close()
    image = (root / unit["render_path"]).resolve()
    if (not image.is_relative_to(root) or unit["render_sha256"] != expected_image_sha256
            or sha256_file(image) != expected_image_sha256):
        raise ValueError("SHA256 renderu nie zgadza się z zachowaną proweniencją")
    original_image = Path(diagnostic["image_path"])
    if not original_image.is_file() or sha256_file(original_image) != expected_image_sha256:
        raise ValueError("Pierwotny render diagnostyczny nie przeszedł kontroli SHA256")
    width, height = _dimensions(_local_tool("magick"), image)
    if (width, height) != (unit["width"], unit["height"]):
        raise ValueError("Wymiary renderu nie zgadzają się z manifestem")
    lines = parse_qwen_lines(_canonical(diagnostic["output"]), width, height)
    identity = {
        "adapter_schema": "genea.qwen-diagnostic-import/v1",
        "unit_id": unit["unit_id"], "ordinal": ordinal, "locator": unit["locator"],
        "source_sha256": unit["input_sha256"], "image_sha256": expected_image_sha256,
        "width": width, "height": height, "diagnostic_sha256": expected_response_sha256,
        "prompt": diagnostic["prompt"], "prompt_sha256": _sha(diagnostic["prompt"].encode()),
        "generation_config": diagnostic["options"], "recovery_metadata": recovery_metadata,
    }
    fingerprint = _sha(_canonical(identity).encode())
    batch_root = root / "attempts" / "qwen"
    batch_root.mkdir(parents=True, exist_ok=True)
    with _mutation_lock(batch_root):
        directory = batch_root / fingerprint
        artifact_dir = _verified_completed(directory)
        reused = artifact_dir is not None
        if artifact_dir is None:
            artifact_dir = directory / uuid.uuid4().hex
            artifact_dir.mkdir(parents=True)
            with (artifact_dir / "raw-response.json").open("xb") as stream:
                stream.write(payload)
            _new_json(artifact_dir / "provenance.json", {
                **identity, "fingerprint": fingerprint, "created_at": utc_now(),
                "status": "auxiliary_unreviewed", "scope": SCOPE,
                "provenance_status": "recovered_validated_not_originally_frozen",
                "raw_response_kind": "diagnostic_wrapper_not_original_http_envelope",
                "historical_limitations": ["original_schema_not_versioned", "image_hash_validated_at_import",
                                           "runtime_and_model_metadata_recovered"],
                "telemetry": diagnostic["ollama"], "elapsed_seconds": diagnostic.get("elapsed_seconds"),
                "original_image_path": str(original_image),
            })
            _new_json(artifact_dir / "lines.json", {"lines": lines})
            with (artifact_dir / "page.xml").open("xb") as stream:
                stream.write(_page_xml(lines, unit["render_path"], width, height, fingerprint))
            _publish_completion(artifact_dir / "complete.json", {"checksums": {
                name: sha256_file(artifact_dir / name)
                for name in ("raw-response.json", "provenance.json", "lines.json", "page.xml")}})
        provenance = {"fingerprint": fingerprint, "scope": SCOPE,
                      "path": str((artifact_dir / "provenance.json").relative_to(root)),
                      "sha256": sha256_file(artifact_dir / "provenance.json")}
        result = import_recognition_attempt(
            root, ordinal, artifact_dir / "page.xml", backend="qwen3-vl-ollama-recovered",
            model=diagnostic["model"], format_type="page", provenance=provenance,
            backend_version=recovery_metadata["runtime_version"],
        )
    with _mutation_lock(root):
        _refresh_checksums(root)
    return {**result, "ordinal": ordinal, "fingerprint": fingerprint,
            "reused_generation": reused, "provenance": provenance, "scope": SCOPE}
