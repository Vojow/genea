"""Bounded recovery checkpoints on UI launch; no scheduler or canonical writes."""
import os
import shutil
from pathlib import Path

from ..storage import configured_database_backend
from ..storage_snapshots import snapshot_if_due


def maintain_backups(root: Path):
    if not (root / "research" / "storage.json").is_file():
        return None
    backend = configured_database_backend(root)
    if not backend.encrypted:
        return None
    store = Path(os.environ.get("GENEA_SNAPSHOT_STORE", str(Path.home() / ".local/share/genea/snapshots"))).expanduser()
    existing = store
    while not existing.exists():
        existing = existing.parent
    if shutil.disk_usage(existing).free < 2 * backend.path.stat().st_size + 512 * 1024**2:
        raise RuntimeError("Insufficient space for two snapshot staging copies and 512 MiB reserve")
    return snapshot_if_due(backend, store, workspace_root=root,
                           interval_seconds=86400, keep_last=7)
