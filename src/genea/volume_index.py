from __future__ import annotations

import csv
import difflib
import hashlib
import io
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .microfilm_preflight import (
    DEFAULT_ORDINAL_REGEX,
    IMAGE_EXTENSIONS,
    _stable_locator,
    preflight_microfilm,
)
from .storage import connect_derivative_cache
from . import volume_index_perf as perf
from .workspace import dump_json, search_fold, sha256_file, stable_id, utc_now


VOLUME_INDEX_SCHEMA = "genea.volume-index/v1"
REGION_SCHEMA = "genea.volume-index-region/v1"
NORMALIZER_VERSION = "genea-search-fold-cyrillic-v1"
SUPPORTED_IMAGE_EXTENSIONS = IMAGE_EXTENSIONS | {".heic", ".heif"}
PROTECTED_OUTPUT_PARTS = (
    ("sources",),
    ("research", "archive", "originals"),
    ("research", "browser-profile"),
    (".git",),
    (".codex",),
    (".agents",),
)


_CYRILLIC_SEARCH_MAP = str.maketrans(
    {
        "а": "a",
        "б": "b",
        "в": "v",
        "г": "g",
        "д": "d",
        "е": "e",
        "ё": "e",
        "ж": "zh",
        "з": "z",
        "и": "i",
        "й": "j",
        "к": "k",
        "л": "l",
        "м": "m",
        "н": "n",
        "о": "o",
        "п": "p",
        "р": "r",
        "с": "s",
        "т": "t",
        "у": "u",
        "ф": "f",
        "х": "h",
        "ц": "c",
        "ч": "ch",
        "ш": "sh",
        "щ": "shch",
        "ъ": "",
        "ы": "y",
        "ь": "",
        "э": "e",
        "ю": "ju",
        "я": "ja",
        "і": "i",
        "ѣ": "e",
        "ѳ": "f",
        "ѵ": "i",
    }
)


@dataclass(frozen=True)
class SourceUnit:
    ordinal: int
    source_path: Path
    source_relative_path: str
    source_frame: int | None
    pdf_page: int | None
    input_sha256: str

    @property
    def source_fingerprint(self) -> str:
        return hashlib.sha256(
            "\0".join(
                [
                    self.source_relative_path,
                    str(self.source_frame if self.source_frame is not None else ""),
                    str(self.pdf_page if self.pdf_page is not None else ""),
                    self.input_sha256,
                ]
            ).encode("utf-8")
        ).hexdigest()

    @property
    def unit_id(self) -> str:
        return stable_id("unit", self.source_fingerprint)


def normalize_search_text(value: str | None) -> str:
    """Create a deterministic technical search form without changing raw text."""

    folded = search_fold(value)
    transliterated = folded.translate(_CYRILLIC_SEARCH_MAP)
    transliterated = unicodedata.normalize("NFKC", transliterated)
    transliterated = re.sub(r"[^0-9a-z]+", " ", transliterated)
    return " ".join(transliterated.split())


def _run(command: list[str], *, text: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
    )


def _version(executable: str, *arguments: str) -> str:
    completed = _run([executable, *arguments], text=True)
    output = completed.stdout or completed.stderr
    return output.splitlines()[0].strip() if output.splitlines() else "unknown"


def _require_tool(name: str, hint: str) -> str:
    executable = shutil.which(name)
    if not executable:
        raise RuntimeError(f"Brak narzędzia {name}. {hint}")
    return executable


def _validate_output(input_path: Path, output_dir: Path, project_root: Path) -> None:
    if output_dir.exists():
        raise ValueError("Katalog OUTPUT musi jeszcze nie istnieć, aby niczego nie nadpisać")
    if input_path.is_dir() and (output_dir == input_path or output_dir.is_relative_to(input_path)):
        raise ValueError("Katalog OUTPUT musi znajdować się poza katalogiem INPUT")
    for parts in PROTECTED_OUTPUT_PARTS:
        protected = project_root.joinpath(*parts).resolve()
        if output_dir == protected or output_dir.is_relative_to(protected):
            raise ValueError(f"Katalog OUTPUT nie może znajdować się w chronionej strefie: {protected}")


@contextmanager
def _output_lock(output_dir: Path):
    lock = output_dir.parent / f".{output_dir.name}.volume-index.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise RuntimeError(f"Inny proces rezerwuje ten OUTPUT: {lock}") from error
    try:
        os.write(descriptor, f"pid={os.getpid()}\n".encode("ascii"))
        yield
    finally:
        os.close(descriptor)
        lock.unlink(missing_ok=True)


def _pdf_page_count(pdfinfo: str, path: Path) -> int:
    completed = _run([pdfinfo, str(path)], text=True)
    match = re.search(r"^Pages:\s+(\d+)\s*$", completed.stdout, re.MULTILINE)
    if not match:
        raise RuntimeError(f"Nie udało się ustalić liczby stron PDF: {path}")
    count = int(match.group(1))
    if count < 1:
        raise ValueError("PDF nie zawiera stron")
    return count


def _image_frame_count(magick: str, path: Path) -> int:
    completed = _run([magick, "identify", "-format", "%p\n", str(path)], text=True)
    frames = [line for line in completed.stdout.splitlines() if line.strip()]
    if not frames:
        raise ValueError(f"Nie udało się odczytać obrazu: {path}")
    return len(frames)


def _natural_key(value: str) -> list[object]:
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", value)]


def _discover_image_files(input_dir: Path, recursive: bool) -> list[Path]:
    candidates = input_dir.rglob("*") if recursive else input_dir.iterdir()
    files = [
        path
        for path in candidates
        if path.is_file()
        and not path.is_symlink()
        and path.suffix.casefold() in SUPPORTED_IMAGE_EXTENSIONS
    ]
    return sorted(files, key=lambda path: _natural_key(path.relative_to(input_dir).as_posix()))


def _compile_ordinal_regex(value: str | None) -> re.Pattern[str] | None:
    if value is None:
        return None
    try:
        return re.compile(value)
    except re.error as error:
        raise ValueError(f"Niepoprawne --ordinal-regex: {error}") from error


def _source_units(
    input_path: Path,
    *,
    magick: str,
    pdfinfo: str,
    ordinal_start: int | None,
    ordinal_regex: str | None,
    recursive: bool,
) -> tuple[str, list[SourceUnit], dict[str, str]]:
    start = 1 if ordinal_start is None and ordinal_regex is None else ordinal_start
    if start is not None and start < 1:
        raise ValueError("--ordinal-start musi być liczbą >= 1")
    if input_path.is_file():
        if input_path.suffix.casefold() != ".pdf":
            raise ValueError("Plik INPUT musi być dokumentem PDF; obrazy podaj jako katalog")
        if ordinal_regex is not None:
            raise ValueError("--ordinal-regex dotyczy katalogów obrazów, nie stron PDF")
        count = _pdf_page_count(pdfinfo, input_path)
        input_hash = sha256_file(input_path)
        first = start or 1
        units = [
            SourceUnit(
                ordinal=first + page - 1,
                source_path=input_path,
                source_relative_path=input_path.name,
                source_frame=None,
                pdf_page=page,
                input_sha256=input_hash,
            )
            for page in range(1, count + 1)
        ]
        return "pdf", units, {str(input_path): input_hash}

    if not input_path.is_dir():
        raise FileNotFoundError(f"INPUT nie istnieje: {input_path}")
    files = _discover_image_files(input_path, recursive)
    if not files:
        raise ValueError("Katalog INPUT nie zawiera obsługiwanych obrazów")
    pattern = _compile_ordinal_regex(ordinal_regex)
    snapshots: dict[str, str] = {}
    file_frames: list[tuple[Path, int, str]] = []
    for path in files:
        content_hash = sha256_file(path)
        snapshots[str(path)] = content_hash
        frame_count = _image_frame_count(magick, path)
        if pattern is not None and frame_count != 1:
            raise ValueError(
                f"Plik {path.name} ma {frame_count} klatek; dla wielostronicowego TIFF/HEIC "
                "użyj kolejności sekwencyjnej zamiast --ordinal-regex"
            )
        for frame in range(frame_count):
            file_frames.append((path, frame, content_hash))

    units: list[SourceUnit] = []
    seen: dict[int, Path] = {}
    next_ordinal = start or 1
    for path, frame, content_hash in file_frames:
        if pattern is None:
            ordinal = next_ordinal
            next_ordinal += 1
        else:
            match = pattern.search(path.stem)
            if not match:
                raise ValueError(
                    f"Nie można odczytać ordinalu z nazwy {path.name}; "
                    "podaj --ordinal-start albo właściwy --ordinal-regex"
                )
            raw = match.group(1) if match.groups() else match.group(0)
            try:
                ordinal = int(raw)
            except ValueError as error:
                raise ValueError(f"Ordinal w nazwie {path.name} nie jest liczbą: {raw}") from error
            if ordinal < 1:
                raise ValueError(f"Ordinal w nazwie {path.name} musi być >= 1")
        if ordinal in seen:
            raise ValueError(f"Powtórzony ordinal {ordinal}: {seen[ordinal].name} oraz {path.name}")
        seen[ordinal] = path
        units.append(
            SourceUnit(
                ordinal=ordinal,
                source_path=path,
                source_relative_path=path.relative_to(input_path).as_posix(),
                source_frame=frame,
                pdf_page=None,
                input_sha256=content_hash,
            )
        )
    return "image_directory", sorted(units, key=lambda unit: unit.ordinal), snapshots


def _render_unit(
    unit: SourceUnit,
    destination: Path,
    *,
    input_kind: str,
    magick: str,
    pdftoppm: str,
    render_dpi: int,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if input_kind == "pdf":
        prefix = destination.with_suffix("")
        _run(
            [
                pdftoppm,
                "-f",
                str(unit.pdf_page),
                "-l",
                str(unit.pdf_page),
                "-singlefile",
                "-r",
                str(render_dpi),
                "-png",
                str(unit.source_path),
                str(prefix),
            ]
        )
        return
    frame = unit.source_frame or 0
    _run(
        [
            magick,
            f"{unit.source_path}[{frame}]",
            "-auto-orient",
            "-strip",
            str(destination),
        ]
    )


def _dimensions(magick: str, path: Path) -> tuple[int, int]:
    completed = _run([magick, "identify", "-format", "%w\t%h", str(path)], text=True)
    width, height = completed.stdout.strip().split("\t", 1)
    return int(width), int(height)


def _parse_bbox_xml(payload: str) -> list[dict]:
    if not payload.strip():
        return []
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return []
    regions = []
    for line in root.iter():
        if line.tag.rsplit("}", 1)[-1] != "line":
            continue
        words = [node for node in line.iter() if node.tag.rsplit("}", 1)[-1] == "word"]
        text = " ".join("".join(word.itertext()).strip() for word in words).strip()
        if not text:
            continue
        boxes = []
        for word in words:
            try:
                boxes.append(
                    (
                        float(word.attrib["xMin"]),
                        float(word.attrib["yMin"]),
                        float(word.attrib["xMax"]),
                        float(word.attrib["yMax"]),
                    )
                )
            except (KeyError, ValueError):
                pass
        bbox = None
        if boxes:
            bbox = [
                min(box[0] for box in boxes),
                min(box[1] for box in boxes),
                max(box[2] for box in boxes),
                max(box[3] for box in boxes),
            ]
        regions.append({"raw_text": text, "bbox": bbox, "confidence": None})
    return regions


def _native_pdf_regions(
    unit: SourceUnit,
    *,
    pdftotext: str,
    attempt_path: Path,
    render_dpi: int,
) -> list[dict]:
    completed = _run(
        [
            pdftotext,
            "-f",
            str(unit.pdf_page),
            "-l",
            str(unit.pdf_page),
            "-bbox-layout",
            str(unit.source_path),
            "-",
        ],
        text=True,
    )
    attempt_path.parent.mkdir(parents=True, exist_ok=True)
    attempt_path.write_text(completed.stdout, encoding="utf-8")
    regions = _parse_bbox_xml(completed.stdout)
    scale = render_dpi / 72.0
    for region in regions:
        if region["bbox"] is not None:
            region["bbox"] = [round(value * scale, 3) for value in region["bbox"]]
    return regions


def _native_text_is_useful(regions: list[dict], minimum_characters: int) -> bool:
    text = " ".join(region["raw_text"] for region in regions)
    visible = "".join(character for character in text if not character.isspace())
    alphabetic = sum(character.isalpha() for character in visible)
    return len(visible) >= minimum_characters and alphabetic >= max(4, minimum_characters // 4)


def _parse_tesseract_tsv(payload: str) -> list[dict]:
    groups: dict[tuple[str, str, str, str], list[dict]] = defaultdict(list)
    reader = csv.DictReader(io.StringIO(payload), delimiter="\t")
    for row in reader:
        text = (row.get("text") or "").strip()
        if not text or row.get("level") != "5":
            continue
        try:
            confidence = float(row.get("conf", "-1"))
            left = int(row.get("left", "0"))
            top = int(row.get("top", "0"))
            width = int(row.get("width", "0"))
            height = int(row.get("height", "0"))
        except ValueError:
            continue
        key = (
            row.get("page_num", ""),
            row.get("block_num", ""),
            row.get("par_num", ""),
            row.get("line_num", ""),
        )
        groups[key].append(
            {
                "text": text,
                "confidence": confidence,
                "bbox": [left, top, left + width, top + height],
            }
        )
    regions = []
    for key in sorted(groups, key=lambda value: tuple(int(part or 0) for part in value)):
        words = groups[key]
        boxes = [word["bbox"] for word in words]
        confidences = [word["confidence"] for word in words if word["confidence"] >= 0]
        regions.append(
            {
                "raw_text": " ".join(word["text"] for word in words),
                "bbox": [
                    min(box[0] for box in boxes),
                    min(box[1] for box in boxes),
                    max(box[2] for box in boxes),
                    max(box[3] for box in boxes),
                ],
                "confidence": round(sum(confidences) / len(confidences), 3)
                if confidences
                else None,
            }
        )
    return regions


def _tesseract_regions(
    render_path: Path,
    *,
    tesseract: str,
    languages: list[str],
    psm: int,
    attempt_path: Path,
) -> list[dict]:
    language = "+".join(languages)
    completed = _run(
        [
            tesseract,
            str(render_path),
            "stdout",
            "-l",
            language,
            "--psm",
            str(psm),
            "tsv",
        ],
        text=True,
    )
    attempt_path.parent.mkdir(parents=True, exist_ok=True)
    attempt_path.write_text(completed.stdout, encoding="utf-8")
    return _parse_tesseract_tsv(completed.stdout)


def _tesseract_languages(tesseract: str) -> set[str]:
    completed = _run([tesseract, "--list-langs"], text=True)
    return {
        line.strip()
        for line in completed.stdout.splitlines()
        if line.strip() and not line.startswith("List of available")
    }


def _load_incremental(index_dir: Path | None, config_fingerprint: str) -> tuple[dict, dict]:
    if index_dir is None:
        return {}, {}
    index_dir = index_dir.expanduser().resolve()
    build_path = index_dir / "build.json"
    manifest_path = index_dir / "manifest.json"
    regions_path = index_dir / "regions.jsonl"
    checksums_path = index_dir / "artifact-checksums.json"
    if not build_path.is_file() or not manifest_path.is_file() or not regions_path.is_file():
        raise ValueError("--incremental-from nie wskazuje kompletnego indeksu volume-index")
    if not checksums_path.is_file():
        raise ValueError("--incremental-from nie ma manifestu integralności artefaktów")
    checksums = json.loads(checksums_path.read_text(encoding="utf-8"))
    for relative, path in (
        ("build.json", build_path),
        ("manifest.json", manifest_path),
        ("regions.jsonl", regions_path),
        ("volume-index.sqlite3", index_dir / "volume-index.sqlite3"),
    ):
        expected = checksums.get(relative)
        if not path.is_file() or expected is None or sha256_file(path) != expected:
            raise ValueError(f"--incremental-from ma naruszony artefakt kontrolny: {relative}")
    build = json.loads(build_path.read_text(encoding="utf-8"))
    if build.get("schema") != VOLUME_INDEX_SCHEMA:
        raise ValueError("--incremental-from ma nieobsługiwany schemat")
    if build.get("config_fingerprint") != config_fingerprint:
        return {}, {}
    manifests = json.loads(manifest_path.read_text(encoding="utf-8"))
    by_fingerprint = {item["source_fingerprint"]: item for item in manifests}
    regions: dict[str, list[dict]] = defaultdict(list)
    with regions_path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                record = json.loads(line)
                regions[record["unit_id"]].append(record)
    attempt_paths = {item.get("attempt_path") for item in manifests}
    attempt_paths.update(region.get("attempt_path") for records in regions.values() for region in records)
    for relative in sorted(path for path in attempt_paths if path):
        path = (index_dir / relative).resolve()
        if (not path.is_relative_to(index_dir) or not path.is_file()
                or sha256_file(path) != checksums.get(relative)):
            raise ValueError(f"--incremental-from ma naruszony artefakt próby: {relative}")
    return by_fingerprint, dict(regions)


def _copy_attempts(old_root: Path, new_root: Path, regions: list[dict]) -> None:
    copied = set()
    for region in regions:
        relative = region.get("attempt_path")
        if not relative or relative in copied:
            continue
        source = old_root / relative
        destination = new_root / relative
        if source.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copied.add(relative)


def _copy_relative_file(old_root: Path, new_root: Path, relative: str | None) -> bool:
    if not relative:
        return False
    source = (old_root / relative).resolve()
    if not source.is_relative_to(old_root) or not source.is_file():
        return False
    destination = new_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return True


def _region_records(
    unit: SourceUnit,
    raw_regions: list[dict],
    *,
    locator: str,
    engine: str,
    engine_version: str,
    model: str | None,
    attempt_path: str,
) -> list[dict]:
    records = []
    for index, raw in enumerate(raw_regions, start=1):
        raw_text = raw["raw_text"]
        records.append(
            {
                "schema": REGION_SCHEMA,
                "unit_id": unit.unit_id,
                "ordinal": unit.ordinal,
                "locator": locator,
                "region_id": f"{unit.unit_id}:region:{index:05d}",
                "bbox": raw.get("bbox"),
                "raw_text": raw_text,
                "search_text": normalize_search_text(raw_text),
                "confidence": raw.get("confidence"),
                "engine": engine,
                "engine_version": engine_version,
                "model": model,
                "normalizer_version": NORMALIZER_VERSION,
                "input_sha256": unit.input_sha256,
                "source_fingerprint": unit.source_fingerprint,
                "attempt_id": stable_id(
                    "attempt",
                    unit.source_fingerprint,
                    engine,
                    engine_version,
                    model or "",
                ),
                "attempt_path": attempt_path,
                "status": "auxiliary_unreviewed_not_source_transcription",
            }
        )
    return records


def _write_regions(path: Path, regions: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for region in regions:
            handle.write(json.dumps(region, ensure_ascii=False, sort_keys=True) + "\n")


def _build_sqlite(path: Path, manifest: list[dict], regions: list[dict]) -> None:
    with connect_derivative_cache(path) as connection:
        connection.executescript(
            """
            PRAGMA journal_mode=DELETE;
            CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE units(
                unit_id TEXT PRIMARY KEY,
                ordinal INTEGER NOT NULL UNIQUE,
                locator TEXT NOT NULL UNIQUE,
                source_relative_path TEXT NOT NULL,
                source_frame INTEGER,
                pdf_page INTEGER,
                input_sha256 TEXT NOT NULL,
                source_fingerprint TEXT NOT NULL UNIQUE,
                render_path TEXT NOT NULL,
                render_sha256 TEXT NOT NULL,
                width INTEGER NOT NULL,
                height INTEGER NOT NULL,
                classification TEXT NOT NULL,
                extraction_status TEXT NOT NULL
            );
            CREATE TABLE regions(
                region_id TEXT PRIMARY KEY,
                unit_id TEXT NOT NULL REFERENCES units(unit_id),
                ordinal INTEGER NOT NULL,
                locator TEXT NOT NULL,
                bbox_json TEXT,
                raw_text TEXT NOT NULL,
                search_text TEXT NOT NULL,
                confidence REAL,
                engine TEXT NOT NULL,
                engine_version TEXT NOT NULL,
                model TEXT,
                attempt_id TEXT NOT NULL,
                attempt_path TEXT NOT NULL,
                status TEXT NOT NULL
            );
            CREATE INDEX regions_ordinal_idx ON regions(ordinal);
            CREATE TABLE tokens(token TEXT NOT NULL, region_id TEXT NOT NULL REFERENCES regions(region_id));
            CREATE INDEX tokens_token_idx ON tokens(token);
            CREATE INDEX tokens_region_idx ON tokens(region_id);
            CREATE VIRTUAL TABLE regions_fts USING fts5(
                region_id UNINDEXED,
                raw_text,
                search_text,
                tokenize='unicode61 remove_diacritics 2'
            );
            CREATE TABLE recognition_attempts(
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
            CREATE TABLE review_decisions(
                review_id TEXT PRIMARY KEY,
                region_id TEXT NOT NULL REFERENCES regions(region_id),
                decision TEXT NOT NULL,
                reviewer TEXT NOT NULL,
                corrected_text TEXT,
                note TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX review_decisions_region_idx
                ON review_decisions(region_id, created_at);
            CREATE TABLE image_derivatives(
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
        connection.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)",
            [
                ("schema", VOLUME_INDEX_SCHEMA),
                ("scope", "technical_candidate_index_not_content_coverage"),
                ("normalizer_version", NORMALIZER_VERSION),
            ],
        )
        connection.executemany(
            """
            INSERT INTO units(
                unit_id, ordinal, locator, source_relative_path, source_frame,
                pdf_page, input_sha256, source_fingerprint, render_path,
                render_sha256, width, height, classification, extraction_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    item["unit_id"],
                    item["ordinal"],
                    item["stable_locator"],
                    item["source_relative_path"],
                    item["source_frame"],
                    item["pdf_page"],
                    item["input_sha256"],
                    item["source_fingerprint"],
                    item["render_path"],
                    item["render_sha256"],
                    item["width"],
                    item["height"],
                    item["classification"],
                    item["extraction_status"],
                )
                for item in manifest
            ],
        )
        region_rows = [
            (
                region["region_id"],
                region["unit_id"],
                region["ordinal"],
                region["locator"],
                json.dumps(region["bbox"]) if region["bbox"] is not None else None,
                region["raw_text"],
                region["search_text"],
                region["confidence"],
                region["engine"],
                region["engine_version"],
                region["model"],
                region["attempt_id"],
                region["attempt_path"],
                region["status"],
            )
            for region in regions
        ]
        connection.executemany(
            """
            INSERT INTO regions(
                region_id, unit_id, ordinal, locator, bbox_json, raw_text,
                search_text, confidence, engine, engine_version, model,
                attempt_id, attempt_path, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            region_rows,
        )
        connection.executemany(
            "INSERT INTO regions_fts(region_id, raw_text, search_text) VALUES (?, ?, ?)",
            [(region["region_id"], region["raw_text"], region["search_text"]) for region in regions],
        )
        token_rows = []
        for region in regions:
            token_rows.extend(
                (token, region["region_id"])
                for token in sorted(set(region["search_text"].split()))
                if token
            )
        connection.executemany("INSERT INTO tokens(token, region_id) VALUES (?, ?)", token_rows)
        connection.commit()


def _artifact_checksums(root: Path) -> dict[str, str]:
    checksums = {}
    for path in sorted(root.rglob("*")):
        if (
            path.is_file()
            and path.name != "artifact-checksums.json"
            and not path.name.endswith(".lock")
        ):
            checksums[path.relative_to(root).as_posix()] = sha256_file(path)
    return checksums


def _verify_snapshots(snapshots: dict[str, str]) -> None:
    changed = [path for path, digest in snapshots.items() if not Path(path).is_file() or sha256_file(Path(path)) != digest]
    if changed:
        raise RuntimeError(
            "INPUT zmienił się podczas indeksowania; wynik został porzucony: "
            + ", ".join(changed[:10])
        )


def _checkpoint_artifacts(working: Path, item: dict, regions: list[dict]) -> dict:
    paths = {item["render_path"]}
    paths.update(region["attempt_path"] for region in regions if region.get("attempt_path"))
    if item.get("attempt_path"):
        paths.add(item["attempt_path"])
    artifacts = {}
    for relative in sorted(paths):
        path = (working / relative).resolve()
        if not path.is_relative_to(working) or not path.is_file():
            raise ValueError(f"Brak poprawnego artefaktu checkpointu: {relative}")
        artifacts[relative] = sha256_file(path)
    return artifacts


def _completed_checkpoint(working: Path, unit: SourceUnit, locator: str,
                          config_fingerprint: str) -> tuple[dict, list[dict]] | None:
    path = working / "checkpoints" / "units" / f"{unit.unit_id}.json"
    if not path.is_file():
        return None
    try:
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
        item = checkpoint["manifest"]
        regions = checkpoint["regions"]
        payload = {"manifest": item, "regions": regions}
        if checkpoint.get("schema") == "genea.volume-index-unit-checkpoint/v2":
            if checkpoint.get("payload_sha256") != hashlib.sha256(
                json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest():
                return None
        if (item["source_fingerprint"] != unit.source_fingerprint
                or item["unit_id"] != unit.unit_id or item["ordinal"] != unit.ordinal
                or item["stable_locator"] != locator
                or item["render_path"] != f"renders/recognition/{unit.ordinal:06d}.png"):
            return None
        if any(region.get("unit_id") != unit.unit_id or region.get("ordinal") != unit.ordinal
               or region.get("locator") != locator for region in regions):
            return None
        actual = _checkpoint_artifacts(working, item, regions)
        if actual[item["render_path"]] != item["render_sha256"]:
            return None
        # Legacy checkpoints without attempt hashes are recomputed when they
        # contain text; structural-only legacy checkpoints remain resumable.
        if checkpoint.get("artifact_sha256") is None:
            if regions or item.get("attempt_path"):
                return None
        elif (actual != checkpoint["artifact_sha256"]
              or checkpoint.get("config_fingerprint") != config_fingerprint):
            return None
        return item, regions
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _operational_telemetry(working: Path, *, worker_limit: int, counts: dict,
                           stages: dict, status: str) -> dict:
    """Instrumentation must never decide whether an index can be published."""
    try:
        return {"schema": "genea.volume-index-telemetry/v1",
                "scope": "operational_not_research_evidence", "status": status,
                "worker_limit": worker_limit, "counts": dict(counts),
                "stage_seconds": dict(stages), "output_bytes": perf.output_bytes(working),
                "output_bytes_basis": "directory_snapshot_before_final_reports",
                "peak_ram_bytes": perf.UNKNOWN}
    except Exception:
        return {"status": "unknown", "output_bytes": "unknown",
                "stage_seconds": "unknown", "counts": "unknown"}


def _persist_telemetry(working: Path, telemetry: dict) -> None:
    try:
        dump_json(working / "telemetry.json", telemetry)
    except Exception:
        pass


def build_volume_index(
    input_path: Path,
    output_dir: Path,
    *,
    project_root: Path,
    locator_prefix: str,
    ordinal_start: int | None = None,
    ordinal_regex: str | None = None,
    recursive: bool = False,
    engine: str = "auto",
    languages: list[str] | None = None,
    render_dpi: int = 220,
    tesseract_psm: int = 6,
    native_minimum_characters: int = 24,
    contact_sheet_size: int = 30,
    contact_sheet_columns: int = 5,
    duplicate_window: int = 40,
    incremental_from: Path | None = None,
    resume: bool = False,
    worker_limit: int = 1,
) -> dict:
    """Build a local derivative search index for a PDF or image directory."""

    build_started = perf.clock()
    if isinstance(worker_limit, bool) or not isinstance(worker_limit, int) or worker_limit < 1:
        raise ValueError("--workers musi być liczbą całkowitą >= 1")
    input_path = input_path.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    project_root = project_root.expanduser().resolve()
    _validate_output(input_path, output_dir, project_root)
    if engine not in {"auto", "native", "tesseract", "none"}:
        raise ValueError("--engine musi mieć wartość auto, native, tesseract albo none")
    if render_dpi < 72 or render_dpi > 600:
        raise ValueError("--render-dpi musi mieścić się w zakresie 72–600")
    if tesseract_psm < 0 or tesseract_psm > 13:
        raise ValueError("--tesseract-psm musi mieścić się w zakresie 0–13")
    if native_minimum_characters < 1:
        raise ValueError("--native-minimum-characters musi być >= 1")

    magick = _require_tool("magick", "Zainstaluj ImageMagick.")
    pdftoppm = _require_tool("pdftoppm", "Zainstaluj Poppler.")
    pdftotext = _require_tool("pdftotext", "Zainstaluj Poppler.")
    pdfinfo = _require_tool("pdfinfo", "Zainstaluj Poppler.")
    tesseract = shutil.which("tesseract")
    requested_languages = list(dict.fromkeys(languages or ["eng"]))
    if engine in {"auto", "tesseract"}:
        if not tesseract:
            raise RuntimeError("Wybrany silnik może wymagać OCR, ale nie znaleziono Tesseract")
        available = _tesseract_languages(tesseract)
        missing = [language for language in requested_languages if language not in available]
        if missing:
            raise RuntimeError(
                "Brak modeli Tesseract: "
                + ", ".join(missing)
                + f". Dostępne: {', '.join(sorted(available)) or 'brak'}."
            )

    tools = {
        "imagemagick": _version(magick, "--version"),
        "pdftoppm": _version(pdftoppm, "-v"),
        "pdftotext": _version(pdftotext, "-v"),
        "tesseract": _version(tesseract, "--version") if tesseract else None,
    }
    config = {
        "engine": engine,
        "languages": requested_languages,
        "render_dpi": render_dpi,
        "tesseract_psm": tesseract_psm,
        "native_minimum_characters": native_minimum_characters,
        "normalizer_version": NORMALIZER_VERSION,
        "tools": tools,
    }
    config_fingerprint = hashlib.sha256(
        json.dumps(config, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    previous_manifest, previous_regions = _load_incremental(incremental_from, config_fingerprint)
    previous_root = incremental_from.expanduser().resolve() if incremental_from else None

    input_kind, units, snapshots = _source_units(
        input_path,
        magick=magick,
        pdfinfo=pdfinfo,
        ordinal_start=ordinal_start,
        ordinal_regex=ordinal_regex,
        recursive=recursive,
    )
    units.sort(key=lambda unit: unit.ordinal)
    stages = {"discovery": perf.elapsed(build_started)}
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    working = output_dir.parent / f".{output_dir.name}.volume-index-work"
    if working.is_symlink():
        raise ValueError("Katalog roboczy nie może być symlinkiem")
    if resume:
        if not (working / "work-state.json").is_file():
            raise ValueError(f"Brak zachowanego checkpointu dla OUTPUT: {output_dir}")
    elif working.exists():
        raise ValueError(
            f"Istnieje przerwany katalog roboczy {working}; użyj volume-index-resume albo wybierz nowy OUTPUT"
        )
    with ExitStack() as stack:
        stack.enter_context(_output_lock(output_dir))
        working.mkdir(parents=True, exist_ok=True)
        state = {
            "schema": "genea.volume-index-work/v1",
            "input": str(input_path),
            "output": str(output_dir),
            "project_root": str(project_root),
            "locator_prefix": locator_prefix,
            "options": {
                "ordinal_start": ordinal_start,
                "ordinal_regex": ordinal_regex,
                "recursive": recursive,
                "engine": engine,
                "languages": languages,
                "render_dpi": render_dpi,
                "tesseract_psm": tesseract_psm,
                "native_minimum_characters": native_minimum_characters,
                "contact_sheet_size": contact_sheet_size,
                "contact_sheet_columns": contact_sheet_columns,
                "duplicate_window": duplicate_window,
                "incremental_from": str(incremental_from.expanduser().resolve()) if incremental_from else None,
            },
            "input_kind": input_kind,
            "config_fingerprint": config_fingerprint,
            "unit_count": len(units),
            "source_snapshots": snapshots,
            "updated_at": utc_now(),
        }
        existing_state_path = working / "work-state.json"
        if resume:
            existing_state = json.loads(existing_state_path.read_text(encoding="utf-8"))
            # Older work states did not freeze source hashes; their units still
            # pass the per-unit fingerprint checks or are rebuilt.
            existing_state.setdefault("source_snapshots", snapshots)
            comparable = {key: value for key, value in state.items() if key not in {"updated_at", "worker_limit"}}
            previous = {key: value for key, value in existing_state.items() if key not in {"updated_at", "worker_limit"}}
            if comparable != previous:
                raise RuntimeError("Checkpoint nie odpowiada bieżącemu wejściu lub konfiguracji")
        state["worker_limit"] = worker_limit
        dump_json(existing_state_path, state)
        renders = working / "renders" / "recognition"
        manifest: list[dict] = []
        all_regions: list[dict] = []
        reused_units = 0
        resumed_units = 0
        extracted_units = 0
        counts = {"processed": 0, "reused": 0, "resumed": 0, "failed": 0, "cache_hits": 0}

        def process_unit(unit: SourceUnit) -> tuple[dict, list[dict], bool]:
            unit_started = perf.clock()
            unit_stages = {}
            locator = _stable_locator(locator_prefix, unit.ordinal)
            render_relative = f"renders/recognition/{unit.ordinal:06d}.png"
            render_path = working / render_relative
            previous = previous_manifest.get(unit.source_fingerprint)
            render_started = perf.clock()
            if previous_root and previous:
                previous_render = (previous_root / previous["render_path"]).resolve()
                valid_cached_render = (
                    previous_render.is_relative_to(previous_root)
                    and previous_render.is_file()
                    and sha256_file(previous_render) == previous.get("render_sha256")
                )
                if valid_cached_render:
                    render_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(previous_render, render_path)
                else:
                    _render_unit(
                        unit,
                        render_path,
                        input_kind=input_kind,
                        magick=magick,
                        pdftoppm=pdftoppm,
                        render_dpi=render_dpi,
                    )
            else:
                _render_unit(
                    unit,
                    render_path,
                    input_kind=input_kind,
                    magick=magick,
                    pdftoppm=pdftoppm,
                    render_dpi=render_dpi,
                )
            width, height = _dimensions(magick, render_path)
            unit_stages["render"] = perf.elapsed(render_started)

            reuse = previous is not None
            classification = "unknown"
            extraction_status = "not_run"
            attempt_relative = None
            selected_engine = "none"
            extraction_started = perf.clock()
            if reuse:
                reused = []
                for region in previous_regions.get(unit.unit_id, []):
                    updated = dict(region)
                    updated["ordinal"] = unit.ordinal
                    updated["locator"] = locator
                    reused.append(updated)
                _copy_attempts(previous_root, working, reused)
                attempt_relative = previous.get("attempt_path")
                _copy_relative_file(previous_root, working, attempt_relative)
                records = reused
                classification = previous.get("classification", "unknown")
                selected_engine = previous.get("extraction_engine", "unknown")
                extraction_status = "reused_from_incremental_index"
            else:
                raw_regions: list[dict] = []
                engine_version = "none"
                model = None
                attempt_relative = f"attempts/{unit.unit_id}.none.txt"
                if input_kind == "pdf" and engine in {"auto", "native"}:
                    attempt_relative = f"attempts/{unit.unit_id}.native.xhtml"
                    native = _native_pdf_regions(
                        unit,
                        pdftotext=pdftotext,
                        attempt_path=working / attempt_relative,
                        render_dpi=render_dpi,
                    )
                    if _native_text_is_useful(native, native_minimum_characters):
                        raw_regions = native
                        selected_engine = "pdftotext_bbox"
                        engine_version = tools["pdftotext"]
                        classification = "native_text"
                should_ocr = engine == "tesseract" or (
                    engine == "auto" and not raw_regions
                )
                if should_ocr:
                    attempt_relative = f"attempts/{unit.unit_id}.tesseract.tsv"
                    raw_regions = _tesseract_regions(
                        render_path,
                        tesseract=tesseract,
                        languages=requested_languages,
                        psm=tesseract_psm,
                        attempt_path=working / attempt_relative,
                    )
                    selected_engine = "tesseract_tsv"
                    engine_version = tools["tesseract"]
                    model = "+".join(requested_languages)
                    classification = "printed_text" if raw_regions else "unknown"
                if engine == "none" or (engine == "native" and input_kind != "pdf"):
                    raw_regions = []
                    classification = "unknown"
                records = _region_records(
                    unit,
                    raw_regions,
                    locator=locator,
                    engine=selected_engine,
                    engine_version=engine_version,
                    model=model,
                    attempt_path=attempt_relative,
                )
                extraction_status = "auxiliary_unreviewed" if records else "no_searchable_text"
            unit_stages["extract"] = perf.elapsed(extraction_started)

            manifest_item = {
                    "unit_id": unit.unit_id,
                    "ordinal": unit.ordinal,
                    "stable_locator": locator,
                    "source_relative_path": unit.source_relative_path,
                    "source_frame": unit.source_frame,
                    "pdf_page": unit.pdf_page,
                    "input_sha256": unit.input_sha256,
                    "source_fingerprint": unit.source_fingerprint,
                    "render_path": render_relative,
                    "render_sha256": sha256_file(render_path),
                    "width": width,
                    "height": height,
                    "classification": classification,
                    "extraction_status": extraction_status,
                    "extraction_engine": selected_engine,
                    "attempt_path": attempt_relative
                    if attempt_relative and (working / attempt_relative).is_file()
                    else None,
                    "status": "technical_unit_not_content_coverage",
                    "telemetry": {"stage_seconds": unit_stages,
                                  "total_seconds": perf.elapsed(unit_started),
                                  "outcome": "reused" if reuse else "processed",
                                  "cache_hit_count": int(reuse),
                                  "output_bytes": perf.artifact_bytes(
                                      [render_path] + ([working / attempt_relative]
                                       if attempt_relative and (working / attempt_relative).is_file() else [])),
                                  "output_bytes_basis": "render_and_selected_attempt"},
                }
            return manifest_item, records, reuse

        # Only the coordinator publishes checkpoints and shared projections.
        # At most worker_limit tasks are submitted, and results are consumed in
        # ordinal order even when later renders finish first.
        extraction_started = perf.clock()
        executor = ThreadPoolExecutor(max_workers=worker_limit) if worker_limit > 1 else None
        pending = deque()
        cursor = iter(units)

        def enqueue(unit: SourceUnit) -> None:
            completed = _completed_checkpoint(
                working, unit, _stable_locator(locator_prefix, unit.ordinal), config_fingerprint
            ) if resume else None
            pending.append((unit, completed, executor.submit(process_unit, unit)
                            if executor and completed is None else None))

        try:
            for _ in range(worker_limit):
                unit = next(cursor, None)
                if unit is not None:
                    enqueue(unit)
            while pending:
                unit, completed, future = pending.popleft()
                if completed:
                    item, records = completed
                    item = dict(item)
                    item["telemetry"] = {"stage_seconds": perf.UNKNOWN, "outcome": "resumed",
                                         "cache_hit_count": 1, "output_bytes": perf.UNKNOWN}
                    resumed_units += 1
                    counts["resumed"] += 1
                    counts["cache_hits"] += 1
                else:
                    item, records, reuse = future.result() if future else process_unit(unit)
                    checkpoint_path = working / "checkpoints" / "units" / f"{unit.unit_id}.json"
                    dump_json(checkpoint_path, {
                        "schema": "genea.volume-index-unit-checkpoint/v2",
                        "completed_at": utc_now(), "next_ordinal": unit.ordinal + 1,
                        "config_fingerprint": config_fingerprint,
                        "artifact_sha256": _checkpoint_artifacts(working, item, records),
                        "payload_sha256": hashlib.sha256(json.dumps(
                            {"manifest": item, "regions": records},
                            ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
                        "manifest": item, "regions": records,
                    })
                    if reuse:
                        reused_units += 1
                        counts["reused"] += 1
                        counts["cache_hits"] += 1
                    else:
                        extracted_units += 1
                        counts["processed"] += 1
                manifest.append(item)
                all_regions.extend(records)
                following = next(cursor, None)
                if following is not None:
                    enqueue(following)
        except BaseException as error:
            if not isinstance(error, KeyboardInterrupt):
                counts["failed"] += 1
            stages["unit_pipeline"] = perf.elapsed(extraction_started)
            _persist_telemetry(working, _operational_telemetry(
                working, worker_limit=worker_limit, counts=counts, stages=stages,
                status="interrupted" if isinstance(error, KeyboardInterrupt) else "failed"))
            raise
        finally:
            if executor:
                # Do not release the output lock while running tasks can write.
                executor.shutdown(wait=True, cancel_futures=True)
        stages["unit_pipeline"] = perf.elapsed(extraction_started)
        _persist_telemetry(working, _operational_telemetry(
            working, worker_limit=worker_limit, counts=counts, stages=stages,
            status="units_completed"))

        @contextmanager
        def measured_stage(name: str):
            started = perf.clock()
            status = "units_completed"
            try:
                yield
            except BaseException as error:
                status = "interrupted" if isinstance(error, KeyboardInterrupt) else "failed"
                if status == "failed":
                    counts["failed"] += 1
                raise
            finally:
                stages[name] = perf.elapsed(started)
                _persist_telemetry(working, _operational_telemetry(
                    working, worker_limit=worker_limit, counts=counts, stages=stages,
                    status=status))

        with measured_stage("preflight"):
            # This is a rebuildable projection in the reserved work directory;
            # checkpoints/renders/attempts survive a failed finalization.
            preflight_dir = working / "preflight"
            if preflight_dir.is_symlink():
                raise ValueError("Preflight w katalogu roboczym nie może być symlinkiem")
            if preflight_dir.exists():
                shutil.rmtree(preflight_dir)
            preflight_result = preflight_microfilm(
                renders,
                preflight_dir,
                project_root=project_root,
                locator_prefix=locator_prefix,
                ordinal_regex=DEFAULT_ORDINAL_REGEX,
                contact_sheet_size=contact_sheet_size,
                contact_sheet_columns=contact_sheet_columns,
                duplicate_window=duplicate_window,
            )
        preflight_map = json.loads(
            (working / "preflight" / "structural-map.json").read_text(encoding="utf-8")
        )
        preflight_by_ordinal = {item["ordinal"]: item for item in preflight_map["units"]}
        for item in manifest:
            technical = preflight_by_ordinal[item["ordinal"]]
            item["metrics"] = technical["metrics"]
            item["duplicate_candidates"] = technical["duplicate_candidates"]
            item["contact_sheet"] = f"preflight/{technical['contact_sheet']}"

        all_regions.sort(key=lambda region: (region["ordinal"], region["region_id"]))
        _verify_snapshots(snapshots)
        dump_json(working / "manifest.json", manifest)
        _write_regions(working / "regions.jsonl", all_regions)
        with measured_stage("fts"):
            temporary_database = working / ".volume-index.sqlite3.tmp"
            temporary_database.unlink(missing_ok=True)
            _build_sqlite(temporary_database, manifest, all_regions)
            temporary_database.replace(working / "volume-index.sqlite3")
        stages["total"] = perf.elapsed(build_started)
        telemetry = _operational_telemetry(working, worker_limit=worker_limit,
                                         counts=counts, stages=stages, status="completed")
        _persist_telemetry(working, telemetry)

        units_with_regions = {region["unit_id"] for region in all_regions}
        summary = {
            "unit_count": len(manifest),
            "region_count": len(all_regions),
            "reused_units": reused_units,
            "processed_units": extracted_units,
            "resumed_units": resumed_units,
            "failed_units": counts["failed"],
            "cache_hit_count": counts["cache_hits"],
            "units_with_searchable_text": sum(
                item["unit_id"] in units_with_regions for item in manifest
            ),
            "input_kind": input_kind,
            "first_ordinal": manifest[0]["ordinal"],
            "last_ordinal": manifest[-1]["ordinal"],
        }
        build = {
            "schema": VOLUME_INDEX_SCHEMA,
            "created_at": utc_now(),
            "scope": "technical_candidate_index_not_content_coverage",
            "warning": "OCR, tekst PDF i brak trafienia są pomocą lokalizacyjną, nie dowodem ani transkrypcją źródłową.",
            "input": str(input_path),
            "input_kind": input_kind,
            "locator_prefix": locator_prefix,
            "config": config,
            "config_fingerprint": config_fingerprint,
            "incremental_from": str(previous_root) if previous_root else None,
            "summary": summary,
            "preflight": preflight_result,
            "telemetry": telemetry,
        }
        dump_json(working / "build.json", build)
        report = "\n".join(
            [
                "# Raport volume-index",
                "",
                "> Indeks jest lokalną pochodną nawigacyjną. Nie stanowi przeglądu treści,",
                "> transkrypcji źródłowej, decyzji identity ani dowodu negatywnego.",
                "",
                f"- Typ wejścia: `{input_kind}`.",
                f"- Jednostki: {summary['unit_count']}.",
                f"- Regiony tekstowe: {summary['region_count']}.",
                f"- Jednostki użyte ponownie: {summary['reused_units']}.",
                f"- Jednostki przetworzone: {summary['processed_units']}.",
                f"- Jednostki wznowione: {summary['resumed_units']}.",
                f"- Błędy / cache hits: {counts['failed']} / {counts['cache_hits']}.",
                f"- Limit workerów: {worker_limit}.",
                f"- Telemetryka operacyjna: `{json.dumps(telemetry, ensure_ascii=False, sort_keys=True)}`.",
                f"- Fingerprint konfiguracji: `{config_fingerprint}`.",
                "",
                "## Następny krok",
                "",
                "1. Sprawdź `preflight/report.md` i mapowanie ordinali.",
                "2. Wyszukaj warianty przez `volume-index-search`.",
                "3. Otwórz wskazany render i potwierdź kandydaturę na obrazie.",
                "4. Uruchom `volume-index-qa` dla stron bez trafień.",
                "5. Dopiero osobny workflow aktu może utworzyć zatwierdzony odczyt lub writeback.",
                "",
            ]
        )
        (working / "report.md").write_text(report, encoding="utf-8")
        dump_json(working / "artifact-checksums.json", _artifact_checksums(working))
        if output_dir.exists():
            raise ValueError("Katalog OUTPUT pojawił się podczas budowy; nie zostanie nadpisany")
        working.replace(output_dir)

    return {
        "status": "ready_for_candidate_search",
        "output": str(output_dir),
        "scope": "technical_candidate_index_not_content_coverage",
        "summary": summary,
        "warning": build["warning"],
        "telemetry": telemetry,
    }


def resume_volume_index(output_dir: Path, *, project_root: Path,
                        worker_limit: int | None = None) -> dict:
    """Resume a preserved work directory without asking for the original options again."""

    output_dir = output_dir.expanduser().resolve()
    working = output_dir.parent / f".{output_dir.name}.volume-index-work"
    state_path = working / "work-state.json"
    if not state_path.is_file():
        raise ValueError(f"Brak zachowanego checkpointu: {state_path}")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    options = state["options"]
    return build_volume_index(
        Path(state["input"]),
        output_dir,
        project_root=project_root,
        locator_prefix=state["locator_prefix"],
        ordinal_start=options.get("ordinal_start"),
        ordinal_regex=options.get("ordinal_regex"),
        recursive=bool(options.get("recursive")),
        engine=options.get("engine", "auto"),
        languages=options.get("languages"),
        render_dpi=int(options.get("render_dpi", 220)),
        tesseract_psm=int(options.get("tesseract_psm", 6)),
        native_minimum_characters=int(options.get("native_minimum_characters", 24)),
        contact_sheet_size=int(options.get("contact_sheet_size", 30)),
        contact_sheet_columns=int(options.get("contact_sheet_columns", 5)),
        duplicate_window=int(options.get("duplicate_window", 40)),
        incremental_from=Path(options["incremental_from"]) if options.get("incremental_from") else None,
        resume=True,
        worker_limit=worker_limit if worker_limit is not None else state.get("worker_limit", 1),
    )


def create_volume_region_snippet(
    index_dir: Path,
    region_id: str,
    output_dir: Path,
    *,
    project_root: Path,
    padding: int = 0,
) -> dict:
    """Opt-in local image derivative, never a reviewed reading or independent source.

    The referenced region supplies bbox in recognition-render pixel coordinates.
    Output is a new, atomically published directory with PNG and provenance JSON;
    the source index, render and its review state are opened read-only.
    """
    root, connection = _open_index(index_dir)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            """SELECT r.region_id, r.bbox_json, r.attempt_id, r.status AS region_status,
                      u.* FROM regions r JOIN units u USING(unit_id) WHERE r.region_id=?""",
            (region_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Brak regionu w indeksie")
        item = dict(row)
    finally:
        connection.close()
    output_dir = output_dir.expanduser().resolve()
    project_root = project_root.expanduser().resolve()
    _validate_output(root, output_dir, project_root)
    if isinstance(padding, bool) or not isinstance(padding, int) or padding < 0:
        raise ValueError("padding musi być liczbą całkowitą >= 0")
    bbox = json.loads(item["bbox_json"]) if item["bbox_json"] else None
    if (not isinstance(bbox, list) or len(bbox) != 4
            or any(isinstance(value, bool) or not isinstance(value, (int, float))
                   or not math.isfinite(value) for value in bbox)):
        raise ValueError("Region nie ma poprawnego bbox w pikselach renderu")
    width, height = item["width"], item["height"]
    left, top, right, bottom = bbox
    if not (0 <= left < right <= width and 0 <= top < bottom <= height):
        raise ValueError("bbox wykracza poza render albo ma pusty obszar")
    crop_bbox = [max(0, math.floor(left) - padding), max(0, math.floor(top) - padding),
                 min(width, math.ceil(right) + padding), min(height, math.ceil(bottom) + padding)]
    render = (root / item["render_path"]).resolve()
    if not render.is_relative_to(root) or not render.is_file():
        raise ValueError("Render regionu musi być lokalnym artefaktem indeksu")
    if sha256_file(render) != item["render_sha256"]:
        raise ValueError("Hash renderu regionu jest niezgodny")
    magick = _require_tool("magick", "Zainstaluj ImageMagick.")
    if _dimensions(magick, render) != (width, height):
        raise ValueError("Wymiary renderu są niezgodne z indeksem")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with _output_lock(output_dir), tempfile.TemporaryDirectory(
        prefix=f".{output_dir.name}.snippet-", dir=output_dir.parent
    ) as temporary:
        staged = Path(temporary) / "result"
        staged.mkdir()
        x0, y0, x1, y1 = crop_bbox
        snippet = staged / "snippet.png"
        _run([magick, str(render), "-crop", f"{x1-x0}x{y1-y0}+{x0}+{y0}",
              "+repage", "-strip", str(snippet)])
        if sha256_file(render) != item["render_sha256"]:
            raise ValueError("Render zmienił się podczas kadrowania")
        metadata = {
            "schema": "genea.volume-index-region-snippet/v1",
            "status": "auxiliary_image_derivative_not_reviewed_evidence",
            "scope": "local_navigation_not_source_transcription_or_content_coverage",
            "privacy": "local_only_no_external_transfer",
            "index": str(root), "unit_id": item["unit_id"], "region_id": region_id,
            "ordinal": item["ordinal"], "locator": item["locator"],
            "source_relative_path": item["source_relative_path"],
            "source_frame": item["source_frame"], "pdf_page": item["pdf_page"],
            "source_sha256": item["input_sha256"],
            "source_fingerprint": item["source_fingerprint"],
            "render_path": item["render_path"], "render_sha256": item["render_sha256"],
            "attempt_id": item["attempt_id"], "region_status": item["region_status"],
            "region_bbox": bbox, "crop_bbox": crop_bbox, "padding_pixels": padding,
            "coordinate_contract": {
                "space": "recognition_render_pixels", "origin": "top_left",
                "axes": "x_right_y_down", "bbox": "xyxy_half_open",
                "render_width": width, "render_height": height,
                "rounding": "floor_left_top_ceil_right_bottom_then_padding_clipped",
                "crop_to_render_translation": [x0, y0],
            },
            "snippet_path": "snippet.png", "snippet_sha256": sha256_file(snippet),
            "created_at": utc_now(),
        }
        dump_json(staged / "snippet.json", metadata)
        if output_dir.exists():
            raise ValueError("OUTPUT pojawił się podczas kadrowania")
        staged.replace(output_dir)
    return {"status": "snippet_created", "output": str(output_dir),
            "snippet": str(output_dir / "snippet.png"), "metadata": metadata}


def _open_index(index_dir: Path) -> tuple[Path, sqlite3.Connection]:
    root = index_dir.expanduser().resolve()
    database = root / "volume-index.sqlite3"
    build = root / "build.json"
    if not database.is_file() or not build.is_file():
        raise ValueError("Katalog nie zawiera kompletnego indeksu volume-index")
    connection = connect_derivative_cache(database, readonly=True)
    connection.row_factory = sqlite3.Row
    return root, connection


def _levenshtein(left: str, right: str) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for index, left_char in enumerate(left, start=1):
        current = [index]
        for other_index, right_char in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[other_index] + 1,
                    previous[other_index - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def _fuzzy_threshold(value: str) -> int:
    if len(value) < 5:
        return 0
    if len(value) < 8:
        return 1
    return 2


def search_volume_index(
    index_dir: Path,
    query: str,
    *,
    variants: list[str] | None = None,
    limit: int = 50,
    fuzzy: bool = True,
) -> dict:
    if limit < 1 or limit > 10000:
        raise ValueError("--limit musi mieścić się w zakresie 1–10000")
    literal_queries = [query] + list(variants or [])
    expansions = []
    for index, value in enumerate(literal_queries):
        normalized = normalize_search_text(value)
        if normalized and normalized not in {item["normalized"] for item in expansions}:
            expansions.append(
                {
                    "literal": value,
                    "normalized": normalized,
                    "kind": "query" if index == 0 else "explicit_variant",
                }
            )
    if not expansions:
        raise ValueError("Zapytanie po normalizacji jest puste")

    root, connection = _open_index(index_dir)
    try:
        candidate_ids: set[str] = set()
        for expansion in expansions:
            terms = expansion["normalized"].split()
            if terms:
                expression = " AND ".join(f'"{term}"' for term in terms)
                candidate_ids.update(
                    row[0]
                    for row in connection.execute(
                        "SELECT region_id FROM regions_fts WHERE regions_fts MATCH ?",
                        (expression,),
                    )
                )
        fuzzy_tokens: dict[str, tuple[str, int]] = {}
        if fuzzy:
            for expansion in expansions:
                for target in expansion["normalized"].split():
                    threshold = _fuzzy_threshold(target)
                    if threshold == 0:
                        continue
                    rows = connection.execute(
                        "SELECT DISTINCT token FROM tokens WHERE length(token) BETWEEN ? AND ?",
                        (max(1, len(target) - threshold), len(target) + threshold),
                    )
                    for row in rows:
                        token = row[0]
                        distance = _levenshtein(target, token)
                        if distance <= threshold:
                            current = fuzzy_tokens.get(token)
                            if current is None or distance < current[1]:
                                fuzzy_tokens[token] = (target, distance)
            if fuzzy_tokens:
                placeholders = ",".join("?" for _ in fuzzy_tokens)
                candidate_ids.update(
                    row[0]
                    for row in connection.execute(
                        f"SELECT DISTINCT region_id FROM tokens WHERE token IN ({placeholders})",
                        tuple(sorted(fuzzy_tokens)),
                    )
                )

        if not candidate_ids:
            return {
                "query": query,
                "variants": variants or [],
                "candidate_count": 0,
                "results": [],
                "scope": "technical_candidates_not_source_findings",
                "warning": "Brak trafienia nie jest dowodem negatywnym; wykonaj QA no-hit i kontrolę obrazu.",
            }
        placeholders = ",".join("?" for _ in candidate_ids)
        rows = connection.execute(
            f"""
            SELECT r.*, u.render_path
            FROM regions r JOIN units u ON u.unit_id = r.unit_id
            WHERE r.region_id IN ({placeholders})
            ORDER BY r.ordinal, r.region_id
            """,
            tuple(sorted(candidate_ids)),
        ).fetchall()
        ranked = []
        for row in rows:
            raw_casefold = row["raw_text"].casefold()
            search_text = row["search_text"]
            basis = None
            matched = None
            distance = None
            rank = 99
            for expansion in expansions:
                if expansion["literal"].casefold() in raw_casefold:
                    basis = "exact_raw" if expansion["kind"] == "query" else "explicit_variant_raw"
                    matched = expansion["literal"]
                    rank = 0 if expansion["kind"] == "query" else 2
                    break
                if expansion["normalized"] in search_text:
                    basis = (
                        "exact_normalized"
                        if expansion["kind"] == "query"
                        else "explicit_variant_normalized"
                    )
                    matched = expansion["literal"]
                    rank = 1 if expansion["kind"] == "query" else 2
                    break
            if basis is None and fuzzy_tokens:
                for token in search_text.split():
                    if token in fuzzy_tokens:
                        matched, distance = fuzzy_tokens[token]
                        basis = "fuzzy_token"
                        matched_token = token
                        rank = 3
                        break
            if basis is None:
                continue
            ranked.append(
                {
                    "ordinal": row["ordinal"],
                    "locator": row["locator"],
                    "region_id": row["region_id"],
                    "bbox": json.loads(row["bbox_json"]) if row["bbox_json"] else None,
                    "raw_text": row["raw_text"],
                    "match_basis": basis,
                    "matched_variant": matched,
                    "matched_token": matched_token if basis == "fuzzy_token" else None,
                    "edit_distance": distance,
                    "confidence": row["confidence"],
                    "engine": row["engine"],
                    "model": row["model"],
                    "render_path": str(root / row["render_path"]),
                    "status": "candidate_requires_image_review",
                    "_rank": rank,
                }
            )
        ranked.sort(key=lambda item: (item["_rank"], item["ordinal"], item["region_id"]))
        for item in ranked:
            item.pop("_rank", None)
        return {
            "query": query,
            "variants": variants or [],
            "candidate_count": len(ranked),
            "returned_count": min(len(ranked), limit),
            "results": ranked[:limit],
            "scope": "technical_candidates_not_source_findings",
            "warning": "Każdy wynik wymaga kontroli na obrazie; ranking nie jest oceną identity ani dowodu.",
        }
    finally:
        connection.close()


def report_volume_index(index_dir: Path) -> dict:
    root = index_dir.expanduser().resolve()
    build_path = root / "build.json"
    if not build_path.is_file():
        raise ValueError("Katalog nie zawiera build.json indeksu volume-index")
    build = json.loads(build_path.read_text(encoding="utf-8"))
    return {
        "status": "ready_for_candidate_search",
        "output": str(root),
        "schema": build.get("schema"),
        "scope": build.get("scope"),
        "summary": build.get("summary"),
        "config_fingerprint": build.get("config_fingerprint"),
        "incremental_from": build.get("incremental_from"),
        "report": str(root / "report.md"),
        "warning": build.get("warning"),
        "telemetry": build.get("telemetry", {"status": "unknown", "output_bytes": "unknown"}),
    }


def qa_volume_index(
    index_dir: Path,
    query: str,
    *,
    variants: list[str] | None = None,
    sample_no_hit: int = 30,
    seed: str = "genea-volume-index-no-hit-v1",
) -> dict:
    if sample_no_hit < 1 or sample_no_hit > 1000:
        raise ValueError("--sample-no-hit musi mieścić się w zakresie 1–1000")
    search = search_volume_index(
        index_dir,
        query,
        variants=variants,
        limit=10000,
        fuzzy=True,
    )
    hit_ordinals = {item["ordinal"] for item in search["results"]}
    root, connection = _open_index(index_dir)
    try:
        rows = connection.execute(
            "SELECT ordinal, locator, render_path FROM units ORDER BY ordinal"
        ).fetchall()
    finally:
        connection.close()
    no_hits = [row for row in rows if row["ordinal"] not in hit_ordinals]
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    stratum_by_ordinal = {
        item["ordinal"]: item.get("contact_sheet") or "unassigned"
        for item in manifest
    }
    strata: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in no_hits:
        strata[stratum_by_ordinal.get(row["ordinal"], "unassigned")].append(row)
    for name in strata:
        strata[name].sort(
            key=lambda row: hashlib.sha256(
                f"{seed}:{name}:{row['ordinal']}".encode("utf-8")
            ).hexdigest()
        )
    ordered = []
    while len(ordered) < min(sample_no_hit, len(no_hits)):
        added = False
        for name in sorted(strata):
            if strata[name]:
                ordered.append(strata[name].pop(0))
                added = True
                if len(ordered) >= sample_no_hit:
                    break
        if not added:
            break
    sample = [
        {
            "ordinal": row["ordinal"],
            "locator": row["locator"],
            "render_path": str(root / row["render_path"]),
            "stratum": stratum_by_ordinal.get(row["ordinal"], "unassigned"),
            "review_status": "not_reviewed",
        }
        for row in ordered[:sample_no_hit]
    ]
    return {
        "query": query,
        "variants": variants or [],
        "hit_unit_count": len(hit_ordinals),
        "no_hit_unit_count": len(no_hits),
        "sample_size": len(sample),
        "seed": seed,
        "stratification": "preflight_contact_sheet_natural_group_round_robin",
        "sample": sample,
        "scope": "deterministic_technical_no_hit_sample_not_negative_evidence",
        "warning": "Próbka służy wykrywaniu false negatives; nawet pełny OCR nie zastępuje zaakceptowanego przeglądu treści.",
    }
