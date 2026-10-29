from __future__ import annotations

import json
import math
import os
import re
import shutil
import statistics
import subprocess
import tempfile
from collections import Counter
from contextlib import ExitStack, contextmanager
from pathlib import Path

from .browse import browse_inventory_hash
from .workspace import dump_json, sha256_file


IMAGE_EXTENSIONS = {
    ".bmp",
    ".jpeg",
    ".jpg",
    ".pgm",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}
DEFAULT_ORDINAL_REGEX = r"(\d+)(?!.*\d)"
SAMPLE_SIDE = 64
INVENTORY_FIELDS = {
    "ordinal",
    "stable_locator",
    "page_label",
    "act_label",
    "content_sha256",
    "availability",
    "duplicate_of_ordinal",
    "notes",
}


def _natural_key(value: str) -> list[object]:
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", value)]


def _command_version(executable: str, *arguments: str) -> str:
    completed = subprocess.run(
        [executable, *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return completed.stdout.splitlines()[0].strip()


@contextmanager
def _exclusive_output_lock(output_dir: Path):
    lock = output_dir.parent / f".{output_dir.name}.microfilm-preflight.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise RuntimeError(f"Inny preflight rezerwuje ten OUTPUT: {lock}") from error
    try:
        os.write(descriptor, f"pid={os.getpid()}\n".encode("ascii"))
        yield
    finally:
        os.close(descriptor)
        lock.unlink(missing_ok=True)


def _image_files(input_dir: Path, *, recursive: bool) -> list[Path]:
    candidates = input_dir.rglob("*") if recursive else input_dir.iterdir()
    files = [
        path
        for path in candidates
        if path.is_file() and not path.is_symlink() and path.suffix.casefold() in IMAGE_EXTENSIONS
    ]
    return sorted(files, key=lambda path: _natural_key(path.relative_to(input_dir).as_posix()))


def _assign_ordinals(
    files: list[Path],
    *,
    ordinal_start: int | None,
    ordinal_regex: str,
) -> list[tuple[int, Path]]:
    if ordinal_start is not None:
        if ordinal_start < 1:
            raise ValueError("--ordinal-start musi być liczbą >= 1")
        return [(ordinal_start + offset, path) for offset, path in enumerate(files)]

    try:
        pattern = re.compile(ordinal_regex)
    except re.error as error:
        raise ValueError(f"Niepoprawne --ordinal-regex: {error}") from error

    assigned: list[tuple[int, Path]] = []
    seen: dict[int, Path] = {}
    for path in files:
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
            raise ValueError(
                f"Powtórzony ordinal {ordinal}: {seen[ordinal].name} oraz {path.name}"
            )
        seen[ordinal] = path
        assigned.append((ordinal, path))
    return sorted(assigned)


def _stable_locator(locator_prefix: str, ordinal: int) -> str:
    prefix = locator_prefix.strip()
    if not prefix:
        raise ValueError("--locator-prefix nie może być pusty")
    if "{ordinal}" in prefix:
        return prefix.replace("{ordinal}", str(ordinal))
    return f"{prefix.rstrip(':')}:{ordinal}"


def validate_microfilm_inventory(
    path: Path,
    *,
    expected_start: int | None = None,
    expected_end: int | None = None,
) -> dict:
    """Validate a human-reviewed manifest and recompute its canonical hash."""

    if (expected_start is None) != (expected_end is None):
        raise ValueError("--expected-start i --expected-end muszą wystąpić razem")
    if expected_start is not None and (expected_start < 1 or expected_end < expected_start):
        raise ValueError("Oczekiwany zakres ordinali jest niepoprawny")
    path = path.expanduser().resolve()
    try:
        units = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"Manifest nie jest poprawnym JSON: {error}") from error
    if not isinstance(units, list) or not units:
        raise ValueError("Manifest musi być niepustą listą JSON")

    ordinals = set()
    locators = set()
    for index, unit in enumerate(units, start=1):
        if not isinstance(unit, dict) or set(unit) != INVENTORY_FIELDS:
            raise ValueError(
                f"Jednostka {index} musi zawierać dokładnie pola: {sorted(INVENTORY_FIELDS)}"
            )
        ordinal = unit["ordinal"]
        if not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 1:
            raise ValueError(f"Jednostka {index}: ordinal musi być liczbą całkowitą >= 1")
        if ordinal in ordinals:
            raise ValueError(f"Powtórzony ordinal: {ordinal}")
        ordinals.add(ordinal)
        locator = unit["stable_locator"]
        if not isinstance(locator, str) or not locator.strip():
            raise ValueError(f"Jednostka {ordinal}: stable_locator jest wymagany")
        if locator in locators:
            raise ValueError(f"Powtórzony stable_locator: {locator}")
        locators.add(locator)
        availability = unit["availability"]
        if not isinstance(availability, str) or availability not in {
            "available",
            "missing",
            "duplicate",
            "restricted",
        }:
            raise ValueError(f"Jednostka {ordinal}: niepoprawna availability {availability!r}")
        duplicate_of = unit["duplicate_of_ordinal"]
        if availability == "duplicate":
            if not isinstance(duplicate_of, int) or isinstance(duplicate_of, bool):
                raise ValueError(f"Jednostka {ordinal}: duplicate wymaga duplicate_of_ordinal")
        elif duplicate_of is not None:
            raise ValueError(
                f"Jednostka {ordinal}: duplicate_of_ordinal jest dozwolony tylko dla duplicate"
            )
        content_hash = unit["content_sha256"]
        if content_hash is not None and (
            not isinstance(content_hash, str)
            or re.fullmatch(r"[0-9a-fA-F]{64}", content_hash) is None
        ):
            raise ValueError(f"Jednostka {ordinal}: content_sha256 nie jest SHA-256")
        for label in ("page_label", "act_label", "notes"):
            if unit[label] is not None and not isinstance(unit[label], str):
                raise ValueError(f"Jednostka {ordinal}: {label} musi być tekstem albo null")
        if availability != "available" and (
            not isinstance(unit["notes"], str) or not unit["notes"].strip()
        ):
            raise ValueError(
                f"Jednostka {ordinal}: {availability} wymaga niepustego notes z uzasadnieniem"
            )

    by_ordinal = {unit["ordinal"]: unit for unit in units}
    for unit in units:
        duplicate_of = unit["duplicate_of_ordinal"]
        if duplicate_of is not None and (
            duplicate_of == unit["ordinal"] or duplicate_of not in ordinals
        ):
            raise ValueError(
                f"Jednostka {unit['ordinal']}: duplicate_of_ordinal nie wskazuje innej jednostki manifestu"
            )
        if duplicate_of is not None and by_ordinal[duplicate_of]["availability"] != "available":
            raise ValueError(
                f"Jednostka {unit['ordinal']}: duplicate_of_ordinal musi wskazywać dostępny oryginał"
            )

    ordered = sorted(units, key=lambda item: item["ordinal"])
    gaps = _ordinal_gap_candidates(ordered)
    if expected_start is not None:
        expected_count = expected_end - expected_start + 1
        if (
            ordered[0]["ordinal"] != expected_start
            or ordered[-1]["ordinal"] != expected_end
            or len(ordered) != expected_count
            or gaps
        ):
            raise ValueError(
                f"Manifest nie pokrywa pełnego zakresu {expected_start}–{expected_end}; luki: {gaps}"
            )
    return {
        "status": "valid_for_import_review",
        "path": str(path),
        "unit_count": len(ordered),
        "first_ordinal": ordered[0]["ordinal"],
        "last_ordinal": ordered[-1]["ordinal"],
        "ordinal_gap_candidates": gaps,
        "canonical_sha256": browse_inventory_hash(ordered),
        "warning": "Walidacja kontraktu nie zastępuje kontroli pochodzenia, mapy strukturalnej ani treści.",
    }


def _identify_image(magick: str, path: Path) -> tuple[int, int, str]:
    completed = subprocess.run(
        [magick, "identify", "-format", "%n\t%w\t%h\t%m\n", str(path)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    frames = [line for line in completed.stdout.splitlines() if line.strip()]
    if len(frames) != 1:
        raise ValueError(
            f"Plik {path.name} zawiera {len(frames)} klatek/stron. "
            "Rozdziel kontener na osobne, jawnie ponumerowane obrazy przed preflightem."
        )
    frame_count, width, height, image_format = frames[0].split("\t", 3)
    if int(frame_count) != 1:
        raise ValueError(
            f"Plik {path.name} deklaruje {frame_count} klatek/stron; wymagany jest pojedynczy obraz"
        )
    return int(width), int(height), image_format.strip()


def _sample_grayscale(magick: str, path: Path) -> bytes:
    completed = subprocess.run(
        [
            magick,
            f"{path}[0]",
            "-auto-orient",
            "-colorspace",
            "Gray",
            "-resize",
            f"{SAMPLE_SIDE}x{SAMPLE_SIDE}!",
            "-depth",
            "8",
            "gray:-",
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    expected = SAMPLE_SIDE * SAMPLE_SIDE
    if len(completed.stdout) != expected:
        raise RuntimeError(
            f"ImageMagick zwrócił {len(completed.stdout)} bajtów próbki zamiast {expected} dla {path.name}"
        )
    return completed.stdout


def _sample_metrics(sample: bytes) -> dict:
    values = list(sample)
    mean = statistics.fmean(values)
    standard_deviation = statistics.pstdev(values)
    counts = Counter(values)
    entropy = -sum(
        (count / len(values)) * math.log2(count / len(values)) for count in counts.values()
    )

    edge_total = 0
    edge_count = 0
    for y in range(SAMPLE_SIDE):
        row = y * SAMPLE_SIDE
        for x in range(SAMPLE_SIDE):
            value = values[row + x]
            if x + 1 < SAMPLE_SIDE:
                edge_total += abs(value - values[row + x + 1])
                edge_count += 1
            if y + 1 < SAMPLE_SIDE:
                edge_total += abs(value - values[row + SAMPLE_SIDE + x])
                edge_count += 1

    bits = 0
    bit_count = 0
    for y_index in range(8):
        y = round(y_index * (SAMPLE_SIDE - 1) / 7)
        samples = [
            values[y * SAMPLE_SIDE + round(x_index * (SAMPLE_SIDE - 1) / 8)]
            for x_index in range(9)
        ]
        for left, right in zip(samples, samples[1:]):
            bits = (bits << 1) | int(left > right)
            bit_count += 1

    flags = []
    if entropy < 1.25 or standard_deviation < 7.0:
        flags.append("blank_like_candidate")
    if standard_deviation < 18.0:
        flags.append("low_contrast_candidate")
    if mean < 35.0:
        flags.append("dark_candidate")
    if mean > 235.0:
        flags.append("light_candidate")

    return {
        "mean_gray": round(mean, 3),
        "standard_deviation": round(standard_deviation, 3),
        "entropy": round(entropy, 4),
        "edge_score": round(edge_total / edge_count, 4),
        "dhash64": f"{bits:0{bit_count // 4}x}",
        "flags": flags,
    }


def _hamming_distance(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def _ordinal_gap_candidates(records: list[dict]) -> list[dict]:
    ordinals = [item["ordinal"] for item in records]
    gaps = []
    for left, right in zip(ordinals, ordinals[1:]):
        if right > left + 1:
            gaps.append(
                {
                    "start": left + 1,
                    "end": right - 1,
                    "count": right - left - 1,
                }
            )
    return gaps


def _verify_input_snapshot(records: list[dict], files_by_ordinal: dict[int, Path]) -> None:
    changed = []
    for record in records:
        path = files_by_ordinal[record["ordinal"]]
        if path.is_symlink() or not path.is_file() or sha256_file(path) != record["content_sha256"]:
            changed.append(record["source_relative_path"])
    if changed:
        raise RuntimeError(
            "Pliki INPUT zmieniły się podczas preflightu; wynik został porzucony: "
            + ", ".join(changed[:10])
        )


def _duplicate_candidates(records: list[dict], *, duplicate_window: int) -> None:
    exact_hashes: dict[str, int] = {}
    for index, record in enumerate(records):
        candidates = []
        content_hash = record["content_sha256"]
        if content_hash in exact_hashes:
            candidates.append(
                {
                    "kind": "exact_content_candidate",
                    "other_ordinal": exact_hashes[content_hash],
                    "distance": 0,
                }
            )
        else:
            exact_hashes[content_hash] = record["ordinal"]

        start = max(0, index - duplicate_window)
        visual = []
        for other in records[start:index]:
            if other["content_sha256"] == content_hash:
                continue
            distance = _hamming_distance(record["metrics"]["dhash64"], other["metrics"]["dhash64"])
            comparable_tone = (
                abs(record["metrics"]["mean_gray"] - other["metrics"]["mean_gray"]) <= 20.0
                and abs(
                    record["metrics"]["standard_deviation"]
                    - other["metrics"]["standard_deviation"]
                ) <= 20.0
                and abs(record["metrics"]["entropy"] - other["metrics"]["entropy"]) <= 1.5
            )
            if distance <= 4 and comparable_tone:
                visual.append(
                    {
                        "kind": "visual_near_duplicate_candidate",
                        "other_ordinal": other["ordinal"],
                        "distance": distance,
                    }
                )
        candidates.extend(sorted(visual, key=lambda item: (item["distance"], item["other_ordinal"]))[:3])
        record["duplicate_candidates"] = candidates
        if candidates:
            record["metrics"]["flags"].append("duplicate_candidate")


def _create_contact_sheets(
    magick: str,
    records: list[dict],
    files_by_ordinal: dict[int, Path],
    output_dir: Path,
    *,
    sheet_size: int,
    columns: int,
) -> list[dict]:
    if sheet_size < 1:
        raise ValueError("--contact-sheet-size musi być >= 1")
    if columns < 1:
        raise ValueError("--contact-sheet-columns musi być >= 1")
    contact_dir = output_dir / "contact-sheets"
    contact_dir.mkdir(parents=True, exist_ok=True)
    sheets = []
    for sheet_number, offset in enumerate(range(0, len(records), sheet_size), start=1):
        group = records[offset : offset + sheet_size]
        output = contact_dir / f"contact-sheet-{sheet_number:04d}.jpg"
        command = [
            magick,
            "montage",
            *[f"{files_by_ordinal[item['ordinal']]}[0]" for item in group],
            "-auto-orient",
            "-thumbnail",
            "260x200",
            "-tile",
            f"{columns}x",
            "-geometry",
            "+8+24",
            "-background",
            "#f4f0e6",
            "-fill",
            "#222222",
            "-pointsize",
            "14",
            "-set",
            "label",
            "%t",
            str(output),
        ]
        subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        sheet = {
            "path": output.relative_to(output_dir).as_posix(),
            "first_ordinal": group[0]["ordinal"],
            "last_ordinal": group[-1]["ordinal"],
            "count": len(group),
        }
        sheets.append(sheet)
        for item in group:
            item["contact_sheet"] = sheet["path"]
    return sheets


def _run_selected_ocr(
    tesseract: str,
    records: list[dict],
    files_by_ordinal: dict[int, Path],
    output_dir: Path,
    *,
    ordinals: set[int],
    language: str | None,
) -> None:
    known = {item["ordinal"] for item in records}
    unknown = sorted(ordinals - known)
    if unknown:
        raise ValueError(f"--ocr-ordinal wskazuje ordinal spoza wejścia: {unknown}")
    if not ordinals:
        return
    ocr_dir = output_dir / "ocr"
    ocr_dir.mkdir(parents=True, exist_ok=True)
    by_ordinal = {item["ordinal"]: item for item in records}
    for ordinal in sorted(ordinals):
        command = [tesseract, str(files_by_ordinal[ordinal]), "stdout"]
        if language:
            command.extend(["-l", language])
        command.extend(["--psm", "6"])
        completed = subprocess.run(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        output = ocr_dir / f"{ordinal:06d}.txt"
        output.write_text(completed.stdout, encoding="utf-8")
        by_ordinal[ordinal]["ocr"] = {
            "path": output.relative_to(output_dir).as_posix(),
            "language": language,
            "psm": 6,
            "status": "auxiliary_unreviewed",
        }


def _report(structural_map: dict, inventory_hash: str, structural_hash: str) -> str:
    summary = structural_map["summary"]
    return "\n".join(
        [
            "# Raport microfilm-preflight",
            "",
            "> To jest analiza techniczna lokalnych kopii. Nie stanowi przeglądu treści,",
            "> nie zwiększa pokrycia badawczego i nie tworzy dowodu negatywnego.",
            "",
            f"- Obrazy: {summary['image_count']}.",
            f"- Zakres ordinali: {summary['first_ordinal']}–{summary['last_ordinal']}.",
            f"- Luki ordinali wymagające wyjaśnienia: {summary['ordinal_gap_candidates'] or 'brak'}.",
            f"- Kandydaci pustych klatek: {summary['blank_like_candidates']}.",
            f"- Kandydaci duplikatów/retakes: {summary['duplicate_candidates']}.",
            f"- OCR pomocniczy, nieprzejrzany — liczba klatek: {summary['ocr_units']}.",
            f"- Contact sheets: {summary['contact_sheet_count']}.",
            f"- Selektywna kolejka kontroli technicznej — liczba klatek: {summary['technical_review_queue_units']}.",
            f"- Kanoniczny hash inventory: `{inventory_hash}`.",
            f"- SHA-256 mapy strukturalnej: `{structural_hash}`.",
            "",
            "## Następna kontrola człowieka",
            "",
            "1. Potwierdź mapowanie filename → ordinal → stable_locator.",
            "2. Oceń kandydatów pustych, duplikatów, retakes i niskiej jakości.",
            "3. Po korelacji zapisz nowy `inventory-reviewed-vN.json` i uruchom `microfilm-inventory-validate`.",
            "4. Importuj wyłącznie zatwierdzoną wersję reviewed; preflight nie jest content coverage.",
            "",
        ]
    )


def preflight_microfilm(
    input_dir: Path,
    output_dir: Path,
    *,
    project_root: Path,
    locator_prefix: str,
    ordinal_start: int | None = None,
    ordinal_regex: str = DEFAULT_ORDINAL_REGEX,
    recursive: bool = False,
    contact_sheet_size: int = 30,
    contact_sheet_columns: int = 5,
    duplicate_window: int = 40,
    ocr_ordinals: list[int] | None = None,
    ocr_language: str | None = None,
) -> dict:
    """Inspect legal local image copies without touching RAW or the database."""

    input_dir = input_dir.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    project_root = project_root.expanduser().resolve()
    if not input_dir.is_dir():
        raise FileNotFoundError(f"Katalog wejściowy nie istnieje: {input_dir}")
    if output_dir.exists():
        raise ValueError("Katalog OUTPUT musi jeszcze nie istnieć, aby niczego nie nadpisać")
    if output_dir == input_dir or output_dir.is_relative_to(input_dir):
        raise ValueError("Katalog OUTPUT musi znajdować się poza katalogiem INPUT")
    protected_output_roots = [
        project_root / "sources",
        project_root / "research" / "archive" / "originals",
        project_root / "research" / "browser-profile",
        project_root / ".git",
        project_root / ".codex",
        project_root / ".agents",
    ]
    for protected in protected_output_roots:
        protected = protected.expanduser().resolve()
        if output_dir == protected or output_dir.is_relative_to(protected):
            raise ValueError(
                f"Katalog OUTPUT nie może znajdować się w chronionej strefie: {protected}"
            )
    if duplicate_window < 0:
        raise ValueError("--duplicate-window musi być >= 0")

    magick = shutil.which("magick")
    if not magick:
        raise RuntimeError(
            "Brak ImageMagick (`magick`). Zainstaluj go przed preflightem "
            "(macOS: brew install imagemagick)."
        )
    requested_ocr = set(ocr_ordinals or [])
    tesseract = shutil.which("tesseract") if requested_ocr else None
    if requested_ocr and not tesseract:
        raise RuntimeError("Wybrano OCR, ale nie znaleziono programu tesseract")

    files = _image_files(input_dir, recursive=recursive)
    if not files:
        raise ValueError("Katalog INPUT nie zawiera obsługiwanych obrazów")
    assigned = _assign_ordinals(
        files,
        ordinal_start=ordinal_start,
        ordinal_regex=ordinal_regex,
    )

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        stack.enter_context(_exclusive_output_lock(output_dir))
        temporary = stack.enter_context(tempfile.TemporaryDirectory(
            prefix=f".{output_dir.name}-building-", dir=output_dir.parent
        ))
        working = Path(temporary)
        records = []
        files_by_ordinal = dict(assigned)
        for ordinal, path in assigned:
            content_hash = sha256_file(path)
            width, height, image_format = _identify_image(magick, path)
            metrics = _sample_metrics(_sample_grayscale(magick, path))
            records.append(
                {
                    "ordinal": ordinal,
                    "stable_locator": _stable_locator(locator_prefix, ordinal),
                    "source_relative_path": path.relative_to(input_dir).as_posix(),
                    "content_sha256": content_hash,
                    "byte_size": path.stat().st_size,
                    "width": width,
                    "height": height,
                    "format": image_format,
                    "metrics": metrics,
                }
            )

        _duplicate_candidates(records, duplicate_window=duplicate_window)
        sheets = _create_contact_sheets(
            magick,
            records,
            files_by_ordinal,
            working,
            sheet_size=contact_sheet_size,
            columns=contact_sheet_columns,
        )
        if tesseract:
            _run_selected_ocr(
                tesseract,
                records,
                files_by_ordinal,
                working,
                ordinals=requested_ocr,
                language=ocr_language,
            )
        _verify_input_snapshot(records, files_by_ordinal)

        inventory = [
            {
                "ordinal": item["ordinal"],
                "stable_locator": item["stable_locator"],
                "page_label": None,
                "act_label": None,
                "content_sha256": item["content_sha256"],
                "availability": "available",
                "duplicate_of_ordinal": None,
                "notes": None,
            }
            for item in records
        ]
        inventory_hash = browse_inventory_hash(inventory)
        dump_json(working / "inventory.json", inventory)
        (working / "inventory.sha256").write_text(f"{inventory_hash}\n", encoding="ascii")

        review_units = []
        for item in records:
            reasons = list(item["metrics"]["flags"])
            reasons.extend(candidate["kind"] for candidate in item["duplicate_candidates"])
            if "ocr" in item:
                reasons.append("selected_auxiliary_ocr")
            if reasons:
                review_units.append(
                    {
                        "ordinal": item["ordinal"],
                        "stable_locator": item["stable_locator"],
                        "source_relative_path": item["source_relative_path"],
                        "contact_sheet": item.get("contact_sheet"),
                        "reasons": sorted(set(reasons)),
                    }
                )
        ordinal_gaps = _ordinal_gap_candidates(records)
        review_queue = {
            "schema": "genea.microfilm-preflight-review-queue/v1",
            "scope": "technical_candidate_review_only_not_content_coverage",
            "candidate_count": len(review_units),
            "image_count": len(records),
            "ordinal_gap_candidates": ordinal_gaps,
            "units": review_units,
            "warning": "Kolejka optymalizuje kontrolę techniczną; nie zastępuje pełnego przeglądu treści.",
        }
        dump_json(working / "review-queue.json", review_queue)

        structural_map = {
            "schema": "genea.microfilm-preflight/v1",
            "scope": "technical_preflight_only_not_content_coverage",
            "input_policy": "legal_local_images_read_only",
            "ordinal_assignment": {
                "mode": "sequential" if ordinal_start is not None else "filename_regex",
                "start": ordinal_start,
                "regex": None if ordinal_start is not None else ordinal_regex,
            },
            "locator_prefix": locator_prefix,
            "thresholds": {
                "blank_entropy_below": 1.25,
                "blank_standard_deviation_below": 7.0,
                "low_contrast_standard_deviation_below": 18.0,
                "visual_duplicate_dhash_distance_at_most": 4,
                "visual_duplicate_mean_difference_at_most": 20.0,
                "visual_duplicate_standard_deviation_difference_at_most": 20.0,
                "visual_duplicate_entropy_difference_at_most": 1.5,
                "duplicate_window": duplicate_window,
            },
            "tool_versions": {
                "imagemagick": _command_version(magick, "-version"),
                "tesseract": _command_version(tesseract, "--version") if tesseract else None,
            },
            "operations": [
                "sha256_original_file",
                "grayscale_64x64_metrics",
                "dhash64_candidate_detection",
                "contact_sheet_derivative",
            ] + (["tesseract_selected_ordinals_auxiliary"] if tesseract else []),
            "summary": {
                "image_count": len(records),
                "first_ordinal": records[0]["ordinal"],
                "last_ordinal": records[-1]["ordinal"],
                "ordinal_gap_candidates": ordinal_gaps,
                "blank_like_candidates": sum(
                    "blank_like_candidate" in item["metrics"]["flags"] for item in records
                ),
                "duplicate_candidates": sum(bool(item["duplicate_candidates"]) for item in records),
                "ocr_units": sum("ocr" in item for item in records),
                "contact_sheet_count": len(sheets),
                "technical_review_queue_units": len(review_units),
            },
            "contact_sheets": sheets,
            "units": records,
        }
        dump_json(working / "structural-map.json", structural_map)
        structural_hash = sha256_file(working / "structural-map.json")
        (working / "structural-map.sha256").write_text(f"{structural_hash}\n", encoding="ascii")
        (working / "report.md").write_text(
            _report(structural_map, inventory_hash, structural_hash), encoding="utf-8"
        )

        artifact_hashes = {
            path.relative_to(working).as_posix(): sha256_file(path)
            for path in sorted(working.rglob("*"))
            if path.is_file()
        }
        dump_json(working / "artifact-checksums.json", artifact_hashes)
        if output_dir.exists():
            raise ValueError(
                "Katalog OUTPUT pojawił się podczas preflightu; wynik nie zostanie opublikowany ani nadpisany"
            )
        working.rename(output_dir)

    return {
        "status": "ready_for_human_review",
        "scope": "technical_preflight_only_not_content_coverage",
        "output_dir": str(output_dir),
        "inventory": str(output_dir / "inventory.json"),
        "inventory_sha256": inventory_hash,
        "structural_map": str(output_dir / "structural-map.json"),
        "structural_map_sha256": structural_hash,
        "image_count": len(records),
        "contact_sheet_count": len(sheets),
        "warning": "Nie importuj manifestu przed ludzką kontrolą mapowania i kandydatów.",
    }


__all__ = [
    "DEFAULT_ORDINAL_REGEX",
    "IMAGE_EXTENSIONS",
    "preflight_microfilm",
    "validate_microfilm_inventory",
]
