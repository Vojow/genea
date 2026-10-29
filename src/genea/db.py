from __future__ import annotations

import sqlite3
from pathlib import Path

from .migrations import LATEST_SCHEMA_VERSION, apply_migrations, create_migration_snapshot
from .storage import configured_database_backend


def connect(root: Path) -> sqlite3.Connection:
    """Open the database without implicitly changing its schema."""

    return configured_database_backend(root).connect()


def _requires_controlled_upgrade(connection: sqlite3.Connection) -> bool:
    user_tables = connection.execute(
        """SELECT COUNT(*) FROM sqlite_master
           WHERE type='table' AND name NOT LIKE 'sqlite_%'"""
    ).fetchone()[0]
    if not user_tables:
        return False
    has_history = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    if not has_history:
        return True
    current = connection.execute("PRAGMA user_version").fetchone()[0]
    return int(current) < LATEST_SCHEMA_VERSION


def initialize(root: Path, *, allow_legacy_upgrade: bool = False) -> Path:
    with connect(root) as connection:
        if _requires_controlled_upgrade(connection) and not allow_legacy_upgrade:
            raise RuntimeError(
                "Istniejąca baza wymaga kontrolowanej migracji ze snapshotem; "
                "uruchom polecenie `genea migration-run`."
            )
        apply_migrations(connection)
    return configured_database_backend(root).path


__all__ = ["connect", "initialize", "create_migration_snapshot"]
