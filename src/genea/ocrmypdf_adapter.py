"""Optional, local OCRmyPDF derivatives; never canonical research evidence.

Only this function discovers executables. Importing Genea does not require
OCRmyPDF, Poppler or an OCRmyPDF Python package to be installed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Sequence

from .workspace import sha256_file, utc_now


MANIFEST_SCHEMA = "genea.ocrmypdf-derivative/v1"
_PROTECTED_PARTS = (
    ("sources",),
    ("research", "archive", "originals"),
    ("research", "browser-profile"),
    (".git",),
    (".codex",),
    (".agents",),
)


def _run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=False,
        **kwargs,
    )


def _page_count(executable: str | None, path: Path) -> int | None:
    if executable is None:
        return None
    try:
        result = _run([executable, str(path)])
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"^Pages:\s+(\d+)\s*$", result.stdout or "", re.MULTILINE)
    return int(match.group(1)) if match and int(match.group(1)) > 0 else None


def _validate_output(input_path: Path, requested: Path, project_root: Path) -> Path:
    # Check the unresolved path too: a dangling symlink must never be replaced.
    if requested.exists() or requested.is_symlink():
        raise ValueError("Katalog OUTPUT musi być nowy; istniejący wynik nie zostanie nadpisany")
    output = requested.resolve()
    if output == input_path or output.is_relative_to(input_path):
        raise ValueError("OUTPUT musi znajdować się poza INPUT")
    for parts in _PROTECTED_PARTS:
        protected = project_root.joinpath(*parts).resolve()
        if output == protected or output.is_relative_to(protected):
            raise ValueError(f"OUTPUT nie może znajdować się w chronionej strefie: {protected}")
    return output


@contextmanager
def _output_parent(output: Path, expected: os.stat_result):
    """Pin the validated existing directory without following any symlink."""
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise RuntimeError("Platforma nie obsługuje bezpiecznej publikacji przez deskryptor katalogu")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(output.anchor, flags)
    try:
        for component in output.parent.parts[1:]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        pinned = os.fstat(descriptor)
        if (pinned.st_dev, pinned.st_ino) != (expected.st_dev, expected.st_ino):
            raise ValueError("Katalog rodzica OUTPUT zmienił tożsamość; przerwano zapis")
        _assert_parent_identity(output, descriptor)
        yield descriptor
    finally:
        os.close(descriptor)


def _assert_parent_identity(output: Path, descriptor: int) -> None:
    try:
        current = output.parent.stat(follow_symlinks=False)
        pinned = os.fstat(descriptor)
        unchanged = (stat.S_ISDIR(current.st_mode)
                     and (current.st_dev, current.st_ino) == (pinned.st_dev, pinned.st_ino)
                     and output.parent.resolve() == output.parent)
    except OSError:
        unchanged = False
    if not unchanged:
        raise ValueError("Katalog rodzica OUTPUT zmienił tożsamość; przerwano zapis")


@contextmanager
def _output_lock(output: Path, parent_descriptor: int):
    lock = f".{output.name}.ocrmypdf.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                             0o600, dir_fd=parent_descriptor)
    except FileExistsError as error:
        raise RuntimeError("Inny proces rezerwuje ten OUTPUT") from error
    try:
        yield
    finally:
        os.close(descriptor)
        os.unlink(lock, dir_fd=parent_descriptor)


def _publish(staged: Path, output: Path, parent_descriptor: int) -> None:
    """Copy into a private fd-relative directory and rename in that same parent."""
    _assert_parent_identity(output, parent_descriptor)
    hidden = f".{output.name}.ocrmypdf-{uuid.uuid4().hex}"
    os.mkdir(hidden, mode=0o700, dir_fd=parent_descriptor)
    bundle_descriptor = None
    copied = []
    published = False
    try:
        bundle_descriptor = os.open(hidden, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                    dir_fd=parent_descriptor)
        for source in staged.iterdir():
            artifact_descriptor = os.open(source.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                                         | os.O_NOFOLLOW, 0o600, dir_fd=bundle_descriptor)
            copied.append(source.name)
            with os.fdopen(artifact_descriptor, "wb") as destination, source.open("rb") as original:
                shutil.copyfileobj(original, destination)
        _assert_parent_identity(output, parent_descriptor)
        try:
            os.stat(output.name, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ValueError("OUTPUT został utworzony podczas przetwarzania; niczego nie nadpisano")
        os.rename(hidden, output.name, src_dir_fd=parent_descriptor, dst_dir_fd=parent_descriptor)
        published = True
        _assert_parent_identity(output, parent_descriptor)
    except BaseException:
        # Every removal is anchored to our opened directory, even after a parent
        # path is replaced with a symlink. Never traverse that substituted path.
        for name in copied:
            os.unlink(name, dir_fd=bundle_descriptor)
        os.rmdir(output.name if published else hidden, dir_fd=parent_descriptor)
        raise
    finally:
        if bundle_descriptor is not None:
            os.close(bundle_descriptor)


def derive_pdf_with_ocrmypdf(
    input_path: Path,
    output_dir: Path,
    *,
    project_root: Path,
    languages: Sequence[str] = ("pol",),
    rotate_pages: bool = False,
    deskew: bool = False,
    sidecar: bool = False,
    locator_prefix: str | None = None,
    source_id: str | None = None,
    source_url: str | None = None,
) -> dict:
    """Create a new private derivative directory, then publish it atomically.

    INPUT is a PDF, OUTPUT is a nonexistent directory outside protected RAW,
    whose parent must already exist. Publication is anchored to that directory's
    validated identity; substituted symlinks cannot redirect output writes.
    OCRmyPDF sees a verified temporary copy rather than the reference original.
    Sidecar text has no bbox and is not used to establish page locators. Page
    count equality verifies numbering only; it does not prove visual fidelity.
    Existing text or unsupported transformations fail according to OCRmyPDF;
    there is no implicit force-OCR, skip-text or language fallback.
    """
    input_path = Path(input_path).expanduser().resolve()
    project_root = Path(project_root).expanduser().resolve()
    requested = Path(output_dir).expanduser()
    output = _validate_output(input_path, requested, project_root)
    if not input_path.is_file() or input_path.suffix.lower() != ".pdf":
        raise ValueError("INPUT musi być istniejącym plikiem PDF")
    with input_path.open("rb") as source:
        if source.read(5) != b"%PDF-":
            raise ValueError("INPUT nie ma nagłówka PDF")
    if isinstance(languages, str):
        raise ValueError("Podaj co najmniej jeden jawny język OCR")
    languages = tuple(languages)
    if not languages:
        raise ValueError("Podaj co najmniej jeden jawny język OCR")
    if any(not isinstance(language, str) or not re.fullmatch(r"[A-Za-z0-9_]+", language)
           for language in languages):
        raise ValueError("Języki OCR muszą być kodami języka, np. pol, rus, lat")
    if any(type(flag) is not bool for flag in (rotate_pages, deskew, sidecar)):
        raise ValueError("Opcje rotate_pages, deskew i sidecar muszą być boolean")
    if locator_prefix is not None and not locator_prefix.strip():
        raise ValueError("locator_prefix nie może być pusty")
    try:
        expected_parent = output.parent.stat(follow_symlinks=False)
    except FileNotFoundError as error:
        raise ValueError("Katalog rodzica OUTPUT musi już istnieć") from error
    if not stat.S_ISDIR(expected_parent.st_mode):
        raise ValueError("Katalog rodzica OUTPUT musi już istnieć i być katalogiem")

    # OCR execution and its TMPDIR are independent of the mutable OUTPUT path.
    temporary_parent = Path(tempfile.gettempdir()).resolve()
    _validate_output(input_path, temporary_parent / f".genea-ocrmypdf-{uuid.uuid4().hex}", project_root)

    executable = shutil.which("ocrmypdf")
    if executable is None:
        raise RuntimeError(
            "Brak opcjonalnego narzędzia ocrmypdf. Zainstaluj OCRmyPDF "
            "(np. brew install ocrmypdf) oraz modele Tesseract dla wybranych języków."
        )
    try:
        version_result = _run([executable, "--version"])
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("Nie udało się uruchomić ocrmypdf --version; sprawdź instalację") from error
    version_lines = (version_result.stdout or version_result.stderr or "").splitlines()
    version = version_lines[0].strip() if version_lines else "unknown"
    pdfinfo = shutil.which("pdfinfo")
    input_hash = sha256_file(input_path)
    options = {
        "languages": list(languages),
        "rotate_pages": rotate_pages,
        "deskew": deskew,
        "sidecar": sidecar,
        "output_type": "pdf",
        "optimize": 0,
        "existing_text": "abort",
    }
    with _output_parent(output, expected_parent) as parent_descriptor, \
         _output_lock(output, parent_descriptor), tempfile.TemporaryDirectory(
        prefix="genea-ocrmypdf-", dir=temporary_parent
    ) as temporary:
        temporary_root = Path(temporary)
        snapshot = temporary_root / "input.pdf"
        shutil.copyfile(input_path, snapshot)
        snapshot.chmod(0o600)
        if sha256_file(snapshot) != input_hash:
            raise RuntimeError("INPUT zmienił się podczas tworzenia kopii; przerwano pochodną")
        input_pages = _page_count(pdfinfo, snapshot)
        staged = temporary_root / "bundle"
        staged.mkdir(mode=0o700)
        pdf = staged / "document.pdf"
        sidecar_path = staged / "sidecar.txt"
        command = [executable, "--output-type", "pdf", "--optimize", "0",
                   "--language", "+".join(languages)]
        if rotate_pages:
            command.append("--rotate-pages")
        if deskew:
            command.append("--deskew")
        if sidecar:
            command.extend(["--sidecar", str(sidecar_path)])
        command.extend([str(snapshot), str(pdf)])
        environment = os.environ.copy()
        environment["TMPDIR"] = str(temporary_root)
        try:
            _run(command, cwd=temporary_root, env=environment)
        except (OSError, subprocess.SubprocessError) as error:
            # stderr may contain OCR text or private metadata; do not echo it.
            code = getattr(error, "returncode", "unknown")
            raise RuntimeError(f"OCRmyPDF nie utworzył pochodnej (exit status: {code})") from error
        if sha256_file(input_path) != input_hash or sha256_file(snapshot) != input_hash:
            raise RuntimeError("INPUT lub jego kopia zmieniły się; wynik nie zostanie opublikowany")
        if not pdf.is_file() or pdf.is_symlink():
            raise RuntimeError("OCRmyPDF nie utworzył pliku PDF")
        with pdf.open("rb") as derived:
            if derived.read(5) != b"%PDF-":
                raise RuntimeError("OCRmyPDF zwrócił wynik bez nagłówka PDF")
        if sidecar and (not sidecar_path.is_file() or sidecar_path.is_symlink()):
            raise RuntimeError("OCRmyPDF nie utworzył żądanego sidecar")
        output_pages = _page_count(pdfinfo, pdf)
        if input_pages is not None and output_pages is not None and input_pages != output_pages:
            raise RuntimeError("Liczba stron pochodnej różni się od INPUT; wynik odrzucony")
        continuity = "verified_page_count" if input_pages is not None and output_pages is not None else "unknown"
        page_map = []
        if continuity == "verified_page_count" and locator_prefix is not None:
            page_map = [{"input_pdf_page": page, "output_pdf_page": page,
                         "locator": f"{locator_prefix}:{page}"}
                        for page in range(1, input_pages + 1)]
        manifest = {
            "schema": MANIFEST_SCHEMA,
            "created_at": utc_now(),
            "status": "ocr_auxiliary_collect_only",
            "classification": "private",
            "source": {"path": str(input_path), "sha256": input_hash,
                       "source_id": source_id, "source_url": source_url,
                       "locator_prefix": locator_prefix, "page_count": input_pages},
            "derivative": {"path": "document.pdf", "sha256": sha256_file(pdf),
                           "page_count": output_pages},
            "tool": {"name": "ocrmypdf", "executable": executable, "version": version},
            "options": options,
            "provenance": {"command": command, "shell": False,
                           "input_snapshot_sha256": input_hash,
                           "execution_paths": "temporary_paths_removed_after_publish",
                           "local_only": True},
            "locator_continuity": {"status": continuity,
                                   "basis": "pdfinfo_page_count" if continuity != "unknown" else "unavailable_page_count",
                                   "page_map": page_map,
                                   "visual_fidelity": "requires_image_review"},
            "sidecar": {"path": "sidecar.txt" if sidecar else None,
                        "sha256": sha256_file(sidecar_path) if sidecar else None,
                        "status": "ocr_text_only" if sidecar else "not_requested",
                        "bbox_available": False,
                        "page_locator_basis": False,
                        "complete_pdf_text": False},
            "evidence_boundary": {"source_is_proof": False, "transcription": False,
                                  "identity_decision": False, "content_coverage": False,
                                  "negative_evidence": False, "canonical_writeback": False},
        }
        (staged / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        for artifact in staged.iterdir():
            artifact.chmod(0o600)
        _publish(staged, output, parent_descriptor)
    return {"status": "derived_pdf_created", "output": str(output),
            "pdf": str(output / "document.pdf"),
            "manifest": str(output / "manifest.json"), "metadata": manifest}
