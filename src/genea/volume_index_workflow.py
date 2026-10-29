from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

from .storage import connect_derivative_cache
from .volume_index import (
    REGION_SCHEMA,
    _artifact_checksums,
    _dimensions,
    _open_index,
    _parse_tesseract_tsv,
    _tesseract_languages,
    normalize_search_text,
    search_volume_index,
)
from .workspace import dump_json, sha256_file, stable_id, utc_now


REVIEW_DECISIONS = {"hit", "false_positive", "illegible", "defer"}
PREPROCESS_OPERATIONS = {
    "auto-orient",
    "grayscale",
    "deskew",
    "rotate-90",
    "rotate-180",
    "split-left",
    "split-right",
}


def _local_tool(name: str) -> str:
    executable = shutil.which(name)
    if executable:
        return executable
    project_candidate = Path(__file__).resolve().parents[2] / ".venv" / "bin" / name
    if project_candidate.is_file():
        return str(project_candidate)
    raise RuntimeError(f"Brak lokalnego narzędzia {name}")


def _tool_version(executable: str) -> str:
    completed = subprocess.run(
        [executable, "--version"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    output = completed.stdout or completed.stderr
    return (output.splitlines() or ["unknown"])[0].strip()


@contextmanager
def _mutation_lock(root: Path):
    lock = root / ".workflow.lock"
    try:
        descriptor = lock.open("x", encoding="ascii")
    except FileExistsError as error:
        raise RuntimeError(f"Indeks jest aktualizowany przez inny proces: {lock}") from error
    try:
        descriptor.write(f"workflow-lock\n")
        descriptor.flush()
        yield
    finally:
        descriptor.close()
        lock.unlink(missing_ok=True)


def _ensure_workflow_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS recognition_attempts(
            attempt_id TEXT PRIMARY KEY,
            unit_id TEXT NOT NULL REFERENCES units(unit_id),
            parent_region_id TEXT,
            backend TEXT NOT NULL,
            backend_version TEXT NOT NULL,
            model TEXT,
            config_json TEXT NOT NULL,
            artifact_path TEXT NOT NULL,
            artifact_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            status TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS review_decisions(
            review_id TEXT PRIMARY KEY,
            region_id TEXT NOT NULL REFERENCES regions(region_id),
            decision TEXT NOT NULL,
            reviewer TEXT NOT NULL,
            corrected_text TEXT,
            note TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS review_decisions_region_idx
            ON review_decisions(region_id, created_at);
        CREATE TABLE IF NOT EXISTS image_derivatives(
            derivative_id TEXT PRIMARY KEY,
            unit_id TEXT NOT NULL REFERENCES units(unit_id),
            operation TEXT NOT NULL,
            config_json TEXT NOT NULL,
            path TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            width INTEGER NOT NULL,
            height INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _refresh_checksums(root: Path) -> None:
    dump_json(root / "artifact-checksums.json", _artifact_checksums(root))


def _bbox_from_points(value: str | None) -> list[float] | None:
    points = []
    for pair in (value or "").split():
        try:
            x, y = pair.split(",", 1)
            points.append((float(x), float(y)))
        except ValueError:
            continue
    if not points:
        return None
    return [
        min(point[0] for point in points),
        min(point[1] for point in points),
        max(point[0] for point in points),
        max(point[1] for point in points),
    ]


def parse_pagexml(payload: str) -> list[dict]:
    """Parse PageXML lines into the common region contract."""

    root = ET.fromstring(payload)
    regions = []
    for line in root.iter():
        if line.tag.rsplit("}", 1)[-1] != "TextLine":
            continue
        text = ""
        bbox = None
        confidence = None
        for node in line:
            name = node.tag.rsplit("}", 1)[-1]
            if name == "Coords" and bbox is None:
                bbox = _bbox_from_points(node.attrib.get("points"))
            elif name == "TextEquiv" and not text:
                for descendant in node:
                    if descendant.tag.rsplit("}", 1)[-1] == "Unicode" and descendant.text:
                        text = descendant.text.strip()
                        break
            if name == "TextEquiv" and node.attrib.get("conf") is not None:
                try:
                    confidence = float(node.attrib["conf"])
                except ValueError:
                    pass
        if text:
            regions.append({"raw_text": text, "bbox": bbox, "confidence": confidence})
    return regions


def parse_alto(payload: str) -> list[dict]:
    """Parse ALTO TextLine/String nodes into the common region contract."""

    root = ET.fromstring(payload)
    regions = []
    for line in root.iter():
        if line.tag.rsplit("}", 1)[-1] != "TextLine":
            continue
        words = []
        confidences = []
        for node in line.iter():
            if node.tag.rsplit("}", 1)[-1] != "String":
                continue
            if node.attrib.get("CONTENT"):
                words.append(node.attrib["CONTENT"])
            if node.attrib.get("WC") is not None:
                try:
                    confidences.append(float(node.attrib["WC"]))
                except ValueError:
                    pass
        text = " ".join(words).strip()
        if not text:
            continue
        try:
            left = float(line.attrib["HPOS"])
            top = float(line.attrib["VPOS"])
            bbox = [
                left,
                top,
                left + float(line.attrib["WIDTH"]),
                top + float(line.attrib["HEIGHT"]),
            ]
        except (KeyError, ValueError):
            bbox = None
        regions.append(
            {
                "raw_text": text,
                "bbox": bbox,
                "confidence": sum(confidences) / len(confidences) if confidences else None,
            }
        )
    return regions


def _unit(connection: sqlite3.Connection, ordinal: int) -> sqlite3.Row:
    connection.row_factory = sqlite3.Row
    row = connection.execute("SELECT * FROM units WHERE ordinal=?", (ordinal,)).fetchone()
    if row is None:
        raise LookupError(f"Brak ordinalu {ordinal} w indeksie")
    return row


def _source_image(
    root: Path,
    connection: sqlite3.Connection,
    unit: sqlite3.Row,
    derivative_id: str | None,
) -> tuple[Path, str, str]:
    if derivative_id:
        _ensure_workflow_schema(connection)
        row = connection.execute(
            "SELECT * FROM image_derivatives WHERE derivative_id=? AND unit_id=?",
            (derivative_id, unit["unit_id"]),
        ).fetchone()
        if row is None:
            raise LookupError("Pochodna obrazu nie należy do wybranej jednostki")
        path = root / row["path"]
        return path, row["sha256"], derivative_id
    path = root / unit["render_path"]
    return path, unit["render_sha256"], "reference"


def _insert_attempt_regions(
    connection: sqlite3.Connection,
    *,
    root: Path,
    unit: sqlite3.Row,
    raw_regions: list[dict],
    attempt_id: str,
    backend: str,
    backend_version: str,
    model: str | None,
    artifact_relative: str,
    parent_region_id: str | None,
) -> list[dict]:
    records = []
    for sequence, region in enumerate(raw_regions, start=1):
        region_id = f"{attempt_id}:region:{sequence:05d}"
        record = {
            "schema": REGION_SCHEMA,
            "unit_id": unit["unit_id"],
            "ordinal": unit["ordinal"],
            "locator": unit["locator"],
            "region_id": region_id,
            "bbox": region.get("bbox"),
            "raw_text": region["raw_text"],
            "search_text": normalize_search_text(region["raw_text"]),
            "confidence": region.get("confidence"),
            "engine": backend,
            "engine_version": backend_version,
            "model": model,
            "input_sha256": unit["input_sha256"],
            "attempt_id": attempt_id,
            "attempt_path": artifact_relative,
            "parent_region_id": parent_region_id,
            "status": "auxiliary_unreviewed_not_source_transcription",
        }
        records.append(record)
        connection.execute(
            """
            INSERT INTO regions(
              region_id, unit_id, ordinal, locator, bbox_json, raw_text,
              search_text, confidence, engine, engine_version, model,
              attempt_id, attempt_path, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                region_id,
                unit["unit_id"],
                unit["ordinal"],
                unit["locator"],
                json.dumps(record["bbox"]) if record["bbox"] is not None else None,
                record["raw_text"],
                record["search_text"],
                record["confidence"],
                backend,
                backend_version,
                model,
                attempt_id,
                artifact_relative,
                record["status"],
            ),
        )
        connection.execute(
            "INSERT INTO regions_fts(region_id, raw_text, search_text) VALUES (?, ?, ?)",
            (region_id, record["raw_text"], record["search_text"]),
        )
        connection.executemany(
            "INSERT INTO tokens(token, region_id) VALUES (?, ?)",
            [(token, region_id) for token in sorted(set(record["search_text"].split()))],
        )
    return records


def recognize_volume_index(
    index_dir: Path,
    ordinal: int,
    *,
    backend: str,
    model: Path | None = None,
    languages: list[str] | None = None,
    psm: int = 6,
    derivative_id: str | None = None,
) -> dict:
    """Append an immutable local Tesseract or Kraken recognition attempt."""

    if backend not in {"tesseract", "kraken"}:
        raise ValueError("backend musi mieć wartość tesseract albo kraken")
    root, connection = _open_index(index_dir)
    connection.close()
    with _mutation_lock(root), connect_derivative_cache(root / "volume-index.sqlite3") as connection:
        connection.row_factory = sqlite3.Row
        _ensure_workflow_schema(connection)
        unit = _unit(connection, ordinal)
        image, image_hash, image_variant = _source_image(
            root, connection, unit, derivative_id
        )
        if not image.is_file() or sha256_file(image) != image_hash:
            raise RuntimeError("Render wejściowy próby nie przeszedł kontroli integralności")
        if backend == "tesseract":
            executable = _local_tool("tesseract")
            selected_languages = list(dict.fromkeys(languages or ["pol", "rus", "lat"]))
            missing = sorted(set(selected_languages) - _tesseract_languages(executable))
            if missing:
                raise RuntimeError("Brak modeli Tesseract: " + ", ".join(missing))
            backend_version = _tool_version(executable)
            model_name = "+".join(selected_languages)
            config = {"languages": selected_languages, "psm": psm, "image_variant": image_variant}
            suffix = "tsv"
        else:
            executable = _local_tool("kraken")
            if model is None:
                raise ValueError("Kraken wymaga jawnej ścieżki --model")
            model = model.expanduser().resolve()
            if not model.is_file():
                raise FileNotFoundError(f"Nie znaleziono modelu Kraken: {model}")
            backend_version = _tool_version(executable)
            model_name = str(model)
            config = {
                "model_sha256": sha256_file(model),
                "model": str(model),
                "image_variant": image_variant,
            }
            suffix = "page.xml"
        attempt_id = stable_id(
            "attempt",
            unit["unit_id"],
            backend,
            backend_version,
            model_name,
            image_hash,
            json.dumps(config, sort_keys=True),
        )
        existing = connection.execute(
            "SELECT artifact_path FROM recognition_attempts WHERE attempt_id=?",
            (attempt_id,),
        ).fetchone()
        if existing:
            return {
                "status": "reused_immutable_attempt",
                "attempt_id": attempt_id,
                "artifact": str(root / existing["artifact_path"]),
            }
        relative = f"attempts/workflow/{attempt_id}.{suffix}"
        artifact = root / relative
        artifact.parent.mkdir(parents=True, exist_ok=True)
        if backend == "tesseract":
            completed = subprocess.run(
                [
                    executable,
                    str(image),
                    "stdout",
                    "-l",
                    model_name,
                    "--psm",
                    str(psm),
                    "tsv",
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            artifact.write_text(completed.stdout, encoding="utf-8")
            raw_regions = _parse_tesseract_tsv(completed.stdout)
        else:
            subprocess.run(
                [
                    executable,
                    "-i",
                    str(image),
                    str(artifact),
                    "-x",
                    "--threads",
                    "1",
                    "segment",
                    "ocr",
                    "-m",
                    str(model),
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            raw_regions = parse_pagexml(artifact.read_text(encoding="utf-8"))
        created_at = utc_now()
        records = _insert_attempt_regions(
            connection,
            root=root,
            unit=unit,
            raw_regions=raw_regions,
            attempt_id=attempt_id,
            backend=backend,
            backend_version=backend_version,
            model=model_name,
            artifact_relative=relative,
            parent_region_id=None,
        )
        connection.execute(
            """
            INSERT INTO recognition_attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                attempt_id,
                unit["unit_id"],
                None,
                backend,
                backend_version,
                model_name,
                json.dumps(config, ensure_ascii=False, sort_keys=True),
                relative,
                sha256_file(artifact),
                created_at,
                "auxiliary_unreviewed",
            ),
        )
        for record in records:
            _append_jsonl(root / "regions.jsonl", record)
        _append_jsonl(
            root / "attempts.jsonl",
            {
                "attempt_id": attempt_id,
                "unit_id": unit["unit_id"],
                "ordinal": ordinal,
                "backend": backend,
                "backend_version": backend_version,
                "model": model_name,
                "config": config,
                "artifact_path": relative,
                "created_at": created_at,
                "status": "auxiliary_unreviewed_not_source_transcription",
            },
        )
        connection.commit()
        _refresh_checksums(root)
        return {
            "status": "attempt_appended",
            "attempt_id": attempt_id,
            "region_count": len(records),
            "artifact": str(artifact),
            "warning": "Wynik OCR/HTR jest kandydaturą pomocniczą i wymaga kontroli obrazu.",
        }


def import_recognition_attempt(
    index_dir: Path,
    ordinal: int,
    result_file: Path,
    *,
    backend: str,
    model: str | None = None,
    format_type: str,
    provenance: dict | None = None,
    backend_version: str = "external-result-import",
) -> dict:
    """Import an ALTO or PageXML result as another immutable attempt."""

    if format_type not in {"alto", "page"}:
        raise ValueError("format_type musi mieć wartość alto albo page")
    result_file = result_file.expanduser().resolve()
    payload = result_file.read_text(encoding="utf-8")
    raw_regions = parse_alto(payload) if format_type == "alto" else parse_pagexml(payload)
    root, readonly = _open_index(index_dir)
    readonly.close()
    digest = sha256_file(result_file)
    attempt_id = stable_id("attempt", backend, model or "", format_type, digest, str(ordinal))
    with _mutation_lock(root), connect_derivative_cache(root / "volume-index.sqlite3") as connection:
        connection.row_factory = sqlite3.Row
        _ensure_workflow_schema(connection)
        unit = _unit(connection, ordinal)
        existing = connection.execute(
            "SELECT artifact_path FROM recognition_attempts WHERE attempt_id=?", (attempt_id,)
        ).fetchone()
        if existing:
            imported = root / existing["artifact_path"]
            if not imported.is_file() or sha256_file(imported) != digest:
                raise RuntimeError("Importowana próba nie przeszła kontroli integralności")
            _refresh_checksums(root)
            return {"status": "reused_immutable_attempt", "attempt_id": attempt_id}
        relative = f"attempts/imported/{attempt_id}.{format_type}.xml"
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(result_file, destination)
        created_at = utc_now()
        records = _insert_attempt_regions(
            connection,
            root=root,
            unit=unit,
            raw_regions=raw_regions,
            attempt_id=attempt_id,
            backend=backend,
            backend_version=backend_version,
            model=model,
            artifact_relative=relative,
            parent_region_id=None,
        )
        connection.execute(
            "INSERT INTO recognition_attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                attempt_id,
                unit["unit_id"],
                None,
                backend,
                backend_version,
                model,
                json.dumps({"format": format_type, **({"provenance": provenance} if provenance else {})}, sort_keys=True),
                relative,
                digest,
                created_at,
                "auxiliary_unreviewed",
            ),
        )
        for record in records:
            _append_jsonl(root / "regions.jsonl", record)
        _append_jsonl(root / "attempts.jsonl", {"attempt_id": attempt_id, "ordinal": ordinal, "backend": backend, "artifact_path": relative, "created_at": created_at})
        connection.commit()
        _refresh_checksums(root)
    return {"status": "attempt_imported", "attempt_id": attempt_id, "region_count": len(records)}


def review_volume_region(
    index_dir: Path,
    region_id: str,
    decision: str,
    *,
    reviewer: str,
    corrected_text: str | None = None,
    note: str | None = None,
) -> dict:
    if decision not in REVIEW_DECISIONS:
        raise ValueError("Nieobsługiwana decyzja review")
    if not reviewer.strip():
        raise ValueError("reviewer jest wymagany")
    if decision == "hit" and corrected_text is not None and not corrected_text.strip():
        raise ValueError("corrected_text nie może być pusty")
    root, readonly = _open_index(index_dir)
    readonly.close()
    created_at = utc_now()
    review_id = stable_id(
        "volume-review",
        region_id,
        decision,
        reviewer,
        corrected_text or "",
        note or "",
        created_at,
    )
    with _mutation_lock(root), connect_derivative_cache(root / "volume-index.sqlite3") as connection:
        connection.row_factory = sqlite3.Row
        _ensure_workflow_schema(connection)
        if connection.execute("SELECT 1 FROM regions WHERE region_id=?", (region_id,)).fetchone() is None:
            raise LookupError("Nie znaleziono regionu")
        connection.execute(
            "INSERT INTO review_decisions VALUES (?, ?, ?, ?, ?, ?, ?)",
            (review_id, region_id, decision, reviewer.strip(), corrected_text, note, created_at),
        )
        record = {
            "review_id": review_id,
            "region_id": region_id,
            "decision": decision,
            "reviewer": reviewer.strip(),
            "corrected_text": corrected_text,
            "note": note,
            "created_at": created_at,
            "scope": "derivative_review_not_canonical_writeback",
        }
        _append_jsonl(root / "reviews.jsonl", record)
        connection.commit()
        _refresh_checksums(root)
    return {"status": "review_appended", **record}


def _latest_review(connection: sqlite3.Connection, region_id: str) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM review_decisions WHERE region_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (region_id,),
    ).fetchone()


def export_writeback_candidate(index_dir: Path, region_id: str, destination: Path) -> dict:
    root, connection = _open_index(index_dir)
    connection.row_factory = sqlite3.Row
    try:
        _ensure_workflow_schema(connection)
        row = connection.execute(
            """SELECT r.*, u.render_path, u.render_sha256, u.input_sha256,
                      u.source_relative_path, u.pdf_page, u.source_frame
               FROM regions r JOIN units u ON u.unit_id=r.unit_id
               WHERE r.region_id=?""",
            (region_id,),
        ).fetchone()
        if row is None:
            raise LookupError("Nie znaleziono regionu")
        review = _latest_review(connection, region_id)
        if review is None or review["decision"] != "hit":
            raise ValueError("Eksport wymaga najnowszej decyzji human review = hit")
        payload = {
            "schema": "genea.volume-index-writeback-candidate/v1",
            "created_at": utc_now(),
            "scope": "candidate_for_existing_curatorial_act_pipeline_not_database_write",
            "locator": row["locator"],
            "ordinal": row["ordinal"],
            "region_id": row["region_id"],
            "bbox": json.loads(row["bbox_json"]) if row["bbox_json"] else None,
            "raw_text": row["raw_text"],
            "human_text": review["corrected_text"] or row["raw_text"],
            "review": dict(review),
            "attempt": {
                "attempt_id": row["attempt_id"],
                "engine": row["engine"],
                "engine_version": row["engine_version"],
                "model": row["model"],
                "artifact_path": row["attempt_path"],
            },
            "source": {
                "relative_path": row["source_relative_path"],
                "pdf_page": row["pdf_page"],
                "source_frame": row["source_frame"],
                "input_sha256": row["input_sha256"],
                "review_render": row["render_path"],
                "review_render_sha256": row["render_sha256"],
            },
            "warning": "Ten manifest nie tworzy aktu, osoby, occurrence, identity ani coverage.",
        }
    finally:
        connection.close()
    destination = destination.expanduser().resolve()
    if destination.exists():
        raise FileExistsError("Plik docelowy już istnieje")
    destination.parent.mkdir(parents=True, exist_ok=True)
    dump_json(destination, payload)
    return {"status": "writeback_candidate_ready", "path": str(destination)}


def derive_volume_image(
    index_dir: Path,
    ordinal: int,
    operation: str,
    *,
    deskew_threshold: float = 40.0,
) -> dict:
    if operation not in PREPROCESS_OPERATIONS:
        raise ValueError("Nieobsługiwana operacja preprocessingu")
    root, readonly = _open_index(index_dir)
    readonly.close()
    magick = _local_tool("magick")
    with _mutation_lock(root), connect_derivative_cache(root / "volume-index.sqlite3") as connection:
        connection.row_factory = sqlite3.Row
        _ensure_workflow_schema(connection)
        unit = _unit(connection, ordinal)
        source = root / unit["render_path"]
        config = {"operation": operation, "deskew_threshold": deskew_threshold}
        derivative_id = stable_id(
            "volume-derivative",
            unit["unit_id"],
            unit["render_sha256"],
            json.dumps(config, sort_keys=True),
            _tool_version(magick),
        )
        existing = connection.execute(
            "SELECT * FROM image_derivatives WHERE derivative_id=?", (derivative_id,)
        ).fetchone()
        if existing:
            return {"status": "reused_derivative", "derivative_id": derivative_id, "path": str(root / existing["path"])}
        relative = f"derivatives/{derivative_id}.png"
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        command = [magick, str(source)]
        if operation == "auto-orient":
            command += ["-auto-orient"]
        elif operation == "grayscale":
            command += ["-colorspace", "Gray"]
        elif operation == "deskew":
            command += ["-deskew", f"{deskew_threshold}%"]
        elif operation == "rotate-90":
            command += ["-rotate", "90"]
        elif operation == "rotate-180":
            command += ["-rotate", "180"]
        elif operation in {"split-left", "split-right"}:
            gravity = "West" if operation == "split-left" else "East"
            command += ["-gravity", gravity, "-crop", "50%x100%+0+0", "+repage"]
        command.append(str(destination))
        subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        width, height = _dimensions(magick, destination)
        created_at = utc_now()
        connection.execute(
            "INSERT INTO image_derivatives VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                derivative_id,
                unit["unit_id"],
                operation,
                json.dumps(config, sort_keys=True),
                relative,
                sha256_file(destination),
                width,
                height,
                created_at,
            ),
        )
        _append_jsonl(root / "derivatives.jsonl", {"derivative_id": derivative_id, "ordinal": ordinal, "operation": operation, "path": relative, "created_at": created_at})
        connection.commit()
        _refresh_checksums(root)
    return {"status": "derivative_created", "derivative_id": derivative_id, "path": str(destination)}


def export_ground_truth(index_dir: Path, destination: Path) -> dict:
    root, connection = _open_index(index_dir)
    connection.row_factory = sqlite3.Row
    _ensure_workflow_schema(connection)
    rows = connection.execute(
        """
        SELECT r.*, u.render_path, u.render_sha256, d.corrected_text, d.reviewer,
               d.created_at AS reviewed_at
        FROM regions r
        JOIN units u ON u.unit_id=r.unit_id
        JOIN review_decisions d ON d.rowid=(
          SELECT d2.rowid FROM review_decisions d2
          WHERE d2.region_id=r.region_id
          ORDER BY d2.created_at DESC, d2.rowid DESC LIMIT 1
        )
        WHERE d.decision='hit' AND d.corrected_text IS NOT NULL AND trim(d.corrected_text)<>''
        ORDER BY r.ordinal, r.region_id
        """
    ).fetchall()
    connection.close()
    if not rows:
        raise ValueError("Brak zatwierdzonych, skorygowanych linii do Ground Truth")
    destination = destination.expanduser().resolve()
    if destination.exists():
        raise FileExistsError("Katalog Ground Truth musi jeszcze nie istnieć")
    destination.mkdir(parents=True)
    magick = _local_tool("magick")
    manifest = []
    for number, row in enumerate(rows, start=1):
        bbox = json.loads(row["bbox_json"]) if row["bbox_json"] else None
        if not bbox:
            continue
        left, top, right, bottom = [int(round(value)) for value in bbox]
        width, height = max(1, right - left), max(1, bottom - top)
        basename = f"line-{number:06d}"
        image = destination / f"{basename}.png"
        subprocess.run(
            [magick, str(root / row["render_path"]), "-crop", f"{width}x{height}+{left}+{top}", "+repage", str(image)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        text_path = destination / f"{basename}.gt.txt"
        text_path.write_text(row["corrected_text"], encoding="utf-8")
        manifest.append(
            {
                "image": image.name,
                "text": text_path.name,
                "region_id": row["region_id"],
                "ordinal": row["ordinal"],
                "locator": row["locator"],
                "reviewer": row["reviewer"],
                "reviewed_at": row["reviewed_at"],
                "render_sha256": row["render_sha256"],
                "image_sha256": sha256_file(image),
            }
        )
    if not manifest:
        shutil.rmtree(destination)
        raise ValueError("Zatwierdzone linie nie mają bbox potrzebnego do Ground Truth")
    dump_json(destination / "manifest.json", manifest)
    dump_json(destination / "artifact-checksums.json", _artifact_checksums(destination))
    return {"status": "ground_truth_ready", "path": str(destination), "line_count": len(manifest)}


def split_ground_truth(
    ground_truth_dir: Path,
    *,
    seed: str = "genea-ground-truth-v1",
    train_ratio: float = 0.8,
    validation_ratio: float = 0.1,
) -> dict:
    root = ground_truth_dir.expanduser().resolve()
    manifest_path = root / "manifest.json"
    records = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not 0.5 <= train_ratio < 1 or not 0 < validation_ratio < 0.5:
        raise ValueError("Niepoprawne proporcje podziału")
    if train_ratio + validation_ratio >= 1:
        raise ValueError("Musi pozostać osobny zbiór testowy")
    groups: dict[int, list[dict]] = {}
    for record in records:
        groups.setdefault(int(record["ordinal"]), []).append(record)
    buckets = {"train": [], "validation": [], "test": []}
    for ordinal, group in sorted(groups.items()):
        fraction = int(hashlib.sha256(f"{seed}:{ordinal}".encode()).hexdigest()[:12], 16) / float(16**12)
        bucket = "train" if fraction < train_ratio else "validation" if fraction < train_ratio + validation_ratio else "test"
        buckets[bucket].extend(group)
    for name, items in buckets.items():
        (root / f"{name}.lst").write_text(
            "\n".join(str((root / item["image"]).resolve()) for item in items) + ("\n" if items else ""),
            encoding="utf-8",
        )
    split = {"seed": seed, "grouping": "ordinal_prevents_same_page_leakage", "counts": {name: len(items) for name, items in buckets.items()}}
    dump_json(root / "split.json", split)
    dump_json(root / "artifact-checksums.json", _artifact_checksums(root))
    return {"status": "ground_truth_split", **split}


def plan_kraken_training(
    ground_truth_dir: Path,
    output_dir: Path,
    *,
    execute: bool = False,
    min_train_lines: int = 100,
    epochs: int = 50,
) -> dict:
    root = ground_truth_dir.expanduser().resolve()
    split = json.loads((root / "split.json").read_text(encoding="utf-8"))
    counts = split["counts"]
    if counts.get("train", 0) < min_train_lines or counts.get("validation", 0) < 1 or counts.get("test", 0) < 1:
        raise ValueError(
            f"Korpus jest za mały lub niereprezentatywny: {counts}; wymagane co najmniej {min_train_lines} train oraz osobne validation/test"
        )
    ketos = _local_tool("ketos")
    output_dir = output_dir.expanduser().resolve()
    command = [
        ketos,
        "--seed",
        "42",
        "--deterministic",
        "train",
        "--format-type",
        "path",
        "--training-data",
        str(root / "train.lst"),
        "--evaluation-data",
        str(root / "validation.lst"),
        "--output",
        str(output_dir),
        "--quit",
        "fixed",
        "--epochs",
        str(epochs),
    ]
    plan = {
        "status": "training_plan_ready" if not execute else "training_started",
        "tool": _tool_version(ketos),
        "ground_truth": str(root),
        "output": str(output_dir),
        "counts": counts,
        "command": command,
        "warning": "Model pozostaje eksperymentalny do pomiaru na odseparowanym teście.",
    }
    if execute:
        if output_dir.exists():
            raise FileExistsError("Katalog modelu musi jeszcze nie istnieć")
        subprocess.run(command, check=True)
        plan["status"] = "training_complete"
    return plan


def evaluate_volume_index(index_dir: Path, gold_file: Path, *, top_k: int = 20) -> dict:
    gold = json.loads(gold_file.expanduser().resolve().read_text(encoding="utf-8"))
    anchors = gold.get("anchors", gold if isinstance(gold, list) else [])
    if not anchors:
        raise ValueError("Złoty korpus nie zawiera anchors")
    found = 0
    relevant_returned = 0
    returned = 0
    details = []
    for anchor in anchors:
        result = search_volume_index(
            index_dir,
            anchor["query"],
            variants=anchor.get("variants"),
            limit=top_k,
            fuzzy=True,
        )
        expected = set(anchor["expected_ordinals"])
        actual = [item["ordinal"] for item in result["results"]]
        matched = expected.intersection(actual)
        found += len(matched)
        relevant_returned += len(matched)
        returned += len(actual)
        details.append({"query": anchor["query"], "expected": sorted(expected), "returned": actual, "matched": sorted(matched)})
    expected_total = sum(len(set(anchor["expected_ordinals"])) for anchor in anchors)
    return {
        "status": "gold_corpus_evaluated",
        "anchor_recall": found / expected_total if expected_total else 0.0,
        "precision_at_k": relevant_returned / returned if returned else 0.0,
        "top_k": top_k,
        "expected_anchor_count": expected_total,
        "found_anchor_count": found,
        "details": details,
        "warning": "Metryki oceniają lokalizowanie kandydatur, nie wiarygodność genealogiczną.",
    }


def export_external_htr_bundle(
    index_dir: Path,
    destination: Path,
    ordinals: Iterable[int],
    *,
    service: str,
    classification: str,
    allow_external: bool,
    consent_reference: str | None,
    max_pages: int = 25,
) -> dict:
    selected = sorted(set(ordinals))
    if not allow_external:
        raise PermissionError("Eksport zewnętrzny wymaga jawnego --allow-external")
    if classification in {"living", "restricted"}:
        raise PermissionError("Materiał living/restricted jest zablokowany dla adaptera zewnętrznego")
    if classification not in {"public", "historical"}:
        raise ValueError("classification musi być public, historical, living albo restricted")
    if not consent_reference:
        raise ValueError("Wymagane jest jawne odniesienie do zgody/zakresu")
    if not selected or len(selected) > max_pages:
        raise ValueError(f"Pakiet musi zawierać od 1 do {max_pages} stron")
    root, connection = _open_index(index_dir)
    connection.row_factory = sqlite3.Row
    placeholders = ",".join("?" for _ in selected)
    rows = connection.execute(
        f"SELECT * FROM units WHERE ordinal IN ({placeholders}) ORDER BY ordinal",
        tuple(selected),
    ).fetchall()
    connection.close()
    if [row["ordinal"] for row in rows] != selected:
        raise LookupError("Nie wszystkie ordinale istnieją")
    destination = destination.expanduser().resolve()
    if destination.exists():
        raise FileExistsError("Katalog pakietu musi jeszcze nie istnieć")
    destination.mkdir(parents=True)
    items = []
    for row in rows:
        source = root / row["render_path"]
        target = destination / f"{row['ordinal']:06d}.png"
        shutil.copy2(source, target)
        items.append(
            {
                "ordinal": row["ordinal"],
                "locator": row["locator"],
                "file": target.name,
                "sha256": sha256_file(target),
                "idempotency_key": stable_id("external-htr", service, row["locator"], sha256_file(target)),
            }
        )
    manifest = {
        "schema": "genea.external-htr-bundle/v1",
        "created_at": utc_now(),
        "service": service,
        "classification": classification,
        "consent_reference": consent_reference,
        "automatic_upload": False,
        "items": items,
        "result_import": "Użyj volume-index-attempt-import dla zwróconego ALTO/PageXML.",
    }
    dump_json(destination / "manifest.json", manifest)
    dump_json(destination / "artifact-checksums.json", _artifact_checksums(destination))
    return {
        "status": "external_bundle_ready_not_uploaded",
        "path": str(destination),
        "page_count": len(items),
        "warning": "Narzędzie nie wykonało transmisji. Upload wymaga osobnego działania użytkownika.",
    }


def volume_index_capabilities() -> dict:
    tesseract = _local_tool("tesseract")
    languages = _tesseract_languages(tesseract)
    required = ["pol", "rus", "lat"]
    try:
        kraken = _local_tool("kraken")
        kraken_version = _tool_version(kraken)
    except RuntimeError:
        kraken_version = None
    try:
        ocrmypdf = _local_tool("ocrmypdf")
        ocrmypdf_version = _tool_version(ocrmypdf)
    except RuntimeError:
        ocrmypdf_version = None
    return {
        "status": "ready" if all(language in languages for language in required) and kraken_version else "incomplete",
        "tesseract": _tool_version(tesseract),
        "required_languages": {language: language in languages for language in required},
        "kraken": kraken_version,
        "ocrmypdf": ocrmypdf_version,
        "ocrmypdf_status": "available_optional" if ocrmypdf_version else "not_installed_optional",
        "external_htr": "bundle_export_import_only_no_automatic_upload",
    }
