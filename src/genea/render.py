from __future__ import annotations

import mimetypes
import shutil
import subprocess
from pathlib import Path

from .archive import get_source
from .workspace import iter_files


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}


def visual_files(root: Path, source_id: str) -> list[Path]:
    source = get_source(root, source_id)
    original = root / source["archive_path"]
    rendered_dir = root / "research" / "archive" / "rendered" / source_id
    if original.suffix.lower() in IMAGE_EXTENSIONS:
        return [original]
    if original.suffix.lower() == ".pdf":
        existing = list(iter_files(rendered_dir, {".png"})) if rendered_dir.exists() else []
        if existing:
            return existing
        executable = shutil.which("pdftoppm")
        if not executable:
            raise RuntimeError(
                "Brak programu pdftoppm. Zainstaluj Poppler "
                "(macOS: brew install poppler; Ubuntu: sudo apt install poppler-utils)."
            )
        rendered_dir.mkdir(parents=True, exist_ok=True)
        prefix = rendered_dir / "page"
        subprocess.run(
            [executable, "-png", "-r", "200", str(original), str(prefix)],
            check=True,
        )
        return list(iter_files(rendered_dir, {".png"}))
    raise ValueError(
        f"Format {original.suffix or '(bez rozszerzenia)'} nie jest obrazem ani PDF-em. "
        "Zarchiwizuj skan lub wykonaj browser-capture."
    )

