from __future__ import annotations

import json
import mimetypes
import shutil
import urllib.error
import urllib.parse
import urllib.request
from email.message import Message
from pathlib import Path

from .archive_providers import validate_download_file
from .db import connect, initialize
from .workspace import safe_filename, sha256_file, stable_id, utc_now


SERVICE_DOMAINS = {
    "geneteka": ("geneteka.genealodzy.pl",),
    "metryki": ("metryki.genealodzy.pl",),
    "familysearch": ("familysearch.org",),
    "szukajwarchiwach": ("szukajwarchiwach.gov.pl",),
    "polona": ("polona.pl",),
}


def infer_service(url: str | None, explicit: str | None = None) -> str:
    if explicit:
        return explicit
    host = urllib.parse.urlparse(url or "").hostname or ""
    host = host.lower()
    for service, domains in SERVICE_DOMAINS.items():
        if any(host == domain or host.endswith("." + domain) for domain in domains):
            return service
    return "digital-library" if url else "local"


def validate_source_url(url: str) -> str:
    """Validate a durable public provenance URL without fetching it."""

    if url != url.strip():
        raise ValueError("Adres źródła nie może zawierać początkowych ani końcowych spacji")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Adres źródła musi być poprawnym adresem http:// lub https://")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Adres źródła nie może zawierać danych logowania")
    return url


def _filename_from_headers(url: str, headers: Message) -> str:
    disposition = headers.get("Content-Disposition", "")
    filename = headers.get_filename() if disposition else None
    if not filename:
        filename = Path(urllib.parse.unquote(urllib.parse.urlparse(url).path)).name
    if not filename:
        content_type = headers.get_content_type()
        filename = "download" + (mimetypes.guess_extension(content_type) or ".bin")
    return safe_filename(filename)


def download_url(
    root: Path,
    url: str,
    max_bytes: int = 200 * 1024 * 1024,
    timeout: int = 60,
) -> tuple[Path, str | None, str]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Dozwolone są wyłącznie adresy http:// i https://")
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "genea-local/0.1 (+local archival; contact: local-user)",
            "Accept": "*/*",
        },
    )
    temporary_dir = root / "research" / "tmp"
    temporary_dir.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > max_bytes:
                raise ValueError(f"Plik przekracza limit {max_bytes // (1024 * 1024)} MB")
            filename = _filename_from_headers(response.geturl(), response.headers)
            target = temporary_dir / filename
            received = 0
            with target.open("wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > max_bytes:
                        output.close()
                        target.unlink(missing_ok=True)
                        raise ValueError(
                            f"Pobieranie przerwane po przekroczeniu limitu "
                            f"{max_bytes // (1024 * 1024)} MB"
                        )
                    output.write(chunk)
            media_type = response.headers.get_content_type()
            try:
                detected_type = validate_download_file(target, media_type)
            except Exception:
                target.unlink(missing_ok=True)
                raise
            return target, detected_type or media_type, response.geturl()
    except urllib.error.HTTPError as error:
        if error.code in {401, 403}:
            raise RuntimeError(
                "Serwis wymaga zalogowanej sesji lub blokuje pobieranie bezpośrednie. "
                "Użyj poleceń browser-open i browser-capture."
            ) from error
        raise


def add_source(
    root: Path,
    location: str,
    *,
    title: str | None = None,
    service: str | None = None,
    source_url: str | None = None,
    rights_note: str | None = None,
    kind: str = "document",
    metadata: dict | None = None,
) -> dict:
    initialize(root)
    is_url = urllib.parse.urlparse(location).scheme in {"http", "https"}
    if is_url and source_url is not None:
        source_url = validate_source_url(source_url)
    downloaded = False
    media_type = None
    final_url = None
    if is_url:
        local_path, media_type, final_url = download_url(root, location)
        downloaded = True
    else:
        local_path = Path(location).expanduser().resolve()
        if not local_path.is_file():
            raise FileNotFoundError(f"Nie znaleziono pliku: {local_path}")
        media_type = mimetypes.guess_type(local_path.name)[0]

    try:
        digest = sha256_file(local_path)
        effective_url = source_url or final_url or (location if is_url else None)
        source_id = stable_id("src", digest, effective_url or "")
        archive_dir = root / "research" / "archive" / "originals" / source_id
        archive_dir.mkdir(parents=True, exist_ok=True)
        suffix = "".join(local_path.suffixes[-2:]) if local_path.name.lower().endswith(".tar.gz") else local_path.suffix
        destination = archive_dir / ("original" + (suffix.lower() or ".bin"))
        if not destination.exists():
            shutil.copy2(local_path, destination)
        relative_path = destination.relative_to(root).as_posix()
        now = utc_now()
        metadata_payload = {"original_filename": local_path.name, **(metadata or {})}
        if is_url and source_url is not None:
            metadata_payload["source_url_override_used"] = True
        record = {
            "id": source_id,
            "parent_id": None,
            "kind": kind,
            "service": infer_service(effective_url, service),
            "title": title or local_path.stem,
            "source_url": effective_url,
            "archive_path": relative_path,
            "media_type": media_type or mimetypes.guess_type(destination.name)[0],
            "sha256": digest,
            "captured_at": now,
            "accessed_at": now,
            "rights_note": rights_note,
            "metadata_json": json.dumps(metadata_payload, ensure_ascii=False),
        }
        with connect(root) as connection:
            connection.execute(
                """
                INSERT INTO sources (
                    id, parent_id, kind, service, title, source_url, archive_path,
                    media_type, sha256, captured_at, accessed_at, rights_note,
                    metadata_json
                ) VALUES (
                    :id, :parent_id, :kind, :service, :title, :source_url,
                    :archive_path, :media_type, :sha256, :captured_at,
                    :accessed_at, :rights_note, :metadata_json
                )
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    accessed_at = excluded.accessed_at,
                    rights_note = COALESCE(excluded.rights_note, sources.rights_note),
                    metadata_json = excluded.metadata_json
                """,
                record,
            )
        return record
    finally:
        if downloaded:
            local_path.unlink(missing_ok=True)


def get_source(root: Path, source_id: str) -> dict:
    initialize(root)
    with connect(root) as connection:
        row = connection.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    if row is None:
        raise KeyError(f"Nie znaleziono źródła: {source_id}")
    return dict(row)


def list_sources(root: Path) -> list[dict]:
    initialize(root)
    with connect(root) as connection:
        rows = connection.execute(
            "SELECT id, service, title, media_type, source_url, archive_path, accessed_at "
            "FROM sources ORDER BY accessed_at DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def register_source_item(
    root: Path,
    source_id: str,
    *,
    item_type: str,
    citation_text: str,
    date_text: str | None = None,
    locator: str | None = None,
    source_quality: str = "Indeterminable",
    actor: str = "curator",
) -> dict:
    """Promote one archived legacy source into the canonical evidence model.

    This operation is intentionally separate from ``source-add``: technical
    captures and access-failure artifacts may be archived without becoming
    evidence. Registration is idempotent and never creates a reading.
    """

    initialize(root)
    if not item_type.strip():
        raise ValueError("item_type nie może być pusty")
    if not citation_text.strip():
        raise ValueError("citation_text nie może być pusty")

    with connect(root) as connection:
        source_row = connection.execute(
            "SELECT * FROM sources WHERE id = ?", (source_id,)
        ).fetchone()
        if source_row is None:
            raise KeyError(f"Nie znaleziono źródła: {source_id}")
        source = dict(source_row)

        archive_root = (root / "research" / "archive" / "originals").resolve()
        archive_path = (root / source["archive_path"]).resolve()
        if archive_root not in archive_path.parents:
            raise ValueError("Ścieżka RAW wychodzi poza research/archive/originals")
        if not archive_path.is_file():
            raise FileNotFoundError(f"Brak zarchiwizowanego RAW: {archive_path}")
        actual_sha256 = sha256_file(archive_path)
        if actual_sha256 != source["sha256"]:
            raise ValueError(
                "Suma SHA-256 RAW nie zgadza się z rekordem sources; "
                "rejestracja została przerwana"
            )

        item_id = stable_id("item", "legacy", source_id)
        artifact_id = stable_id("artifact", "legacy", source_id)
        citation_id = stable_id("citation", "legacy", source_id)
        now = utc_now()
        metadata = json.loads(source["metadata_json"] or "{}")
        metadata.update(
            {
                "legacy_source_id": source_id,
                "service": source["service"],
                "rights_note": source["rights_note"],
            }
        )
        metadata_json = json.dumps(metadata, ensure_ascii=False, sort_keys=True)

        existing_item = connection.execute(
            "SELECT * FROM source_items WHERE legacy_source_id = ?", (source_id,)
        ).fetchone()
        if existing_item is not None and existing_item["id"] != item_id:
            raise ValueError(
                f"Źródło {source_id} jest już powiązane z innym source_item: "
                f"{existing_item['id']}"
            )

        connection.execute(
            """
            INSERT INTO source_items(
                id, legacy_source_id, title, item_type, date_text, locator,
                source_url, citation_text, source_quality,
                verification_status, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'imported_unverified', ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (
                item_id,
                source_id,
                source["title"],
                item_type.strip(),
                date_text,
                locator,
                source["source_url"],
                citation_text.strip(),
                source_quality,
                metadata_json,
            ),
        )
        connection.execute(
            """
            INSERT INTO artifacts(
                id, source_item_id, legacy_source_id, artifact_type, path,
                media_type, sha256, immutable, created_at, metadata_json
            ) VALUES (?, ?, ?, 'RAW', ?, ?, ?, 1, ?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (
                artifact_id,
                item_id,
                source_id,
                source["archive_path"],
                source["media_type"],
                source["sha256"],
                source["captured_at"],
                source["metadata_json"],
            ),
        )
        connection.execute(
            """
            INSERT INTO citations(
                id, source_item_id, artifact_id, citation_text, locator,
                accessed_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (
                citation_id,
                item_id,
                artifact_id,
                citation_text.strip(),
                locator,
                source["accessed_at"],
                now,
            ),
        )

        item = dict(connection.execute(
            "SELECT * FROM source_items WHERE id = ?", (item_id,)
        ).fetchone())
        artifact = dict(connection.execute(
            "SELECT * FROM artifacts WHERE id = ?", (artifact_id,)
        ).fetchone())
        citation = dict(connection.execute(
            "SELECT * FROM citations WHERE id = ?", (citation_id,)
        ).fetchone())
        expected_item = {
            "legacy_source_id": source_id,
            "item_type": item_type.strip(),
            "date_text": date_text,
            "locator": locator,
            "source_url": source["source_url"],
            "citation_text": citation_text.strip(),
            "source_quality": source_quality,
            "verification_status": "imported_unverified",
        }
        expected_artifact = {
            "source_item_id": item_id,
            "legacy_source_id": source_id,
            "artifact_type": "RAW",
            "path": source["archive_path"],
            "sha256": source["sha256"],
            "immutable": 1,
        }
        expected_citation = {
            "source_item_id": item_id,
            "artifact_id": artifact_id,
            "citation_text": citation_text.strip(),
            "locator": locator,
        }
        for label, actual, expected in (
            ("source_item", item, expected_item),
            ("artifact", artifact, expected_artifact),
            ("citation", citation, expected_citation),
        ):
            differences = {
                key: {"existing": actual.get(key), "requested": value}
                for key, value in expected.items()
                if actual.get(key) != value
            }
            if differences:
                raise ValueError(
                    f"Istniejąca rejestracja {label} różni się od żądania: "
                    f"{json.dumps(differences, ensure_ascii=False, sort_keys=True)}"
                )

        registered = {
            "source_id": source_id,
            "source_item_id": item_id,
            "artifact_id": artifact_id,
            "citation_id": citation_id,
            "source_quality": source_quality,
            "verification_status": "imported_unverified",
            "sha256": source["sha256"],
        }
        already_audited = connection.execute(
            """
            SELECT 1 FROM audit_log
            WHERE action = 'source_register' AND object_type = 'source_item'
              AND object_id = ?
            """,
            (item_id,),
        ).fetchone()
        if already_audited is None:
            connection.execute(
                """
                INSERT INTO audit_log(
                    occurred_at, actor, action, object_type, object_id,
                    before_json, after_json, rationale
                ) VALUES (?, ?, 'source_register', 'source_item', ?, NULL, ?, ?)
                """,
                (
                    now,
                    actor,
                    item_id,
                    json.dumps(registered, ensure_ascii=False, sort_keys=True),
                    "Jawna promocja zarchiwizowanego RAW do kanonicznego modelu dowodowego; bez odczytu.",
                ),
            )
        return registered


def attach_source_artifact(
    root: Path,
    source_id: str,
    source_item_id: str,
    *,
    citation_text: str,
    locator: str | None = None,
    actor: str = "curator",
    update_citation: bool = False,
    rehome_existing: bool = False,
) -> dict:
    """Attach an archived legacy RAW to an existing canonical source item.

    Unlike :func:`register_source_item`, this operation never creates or
    modifies a ``source_item`` unless ``rehome_existing`` is explicitly
    enabled. One legacy source can back exactly one RAW artifact, so an
    existing attachment may only be replayed with the same target and
    citation. Citation text and locator remain immutable unless
    ``update_citation`` is explicitly enabled.

    Rehome is deliberately narrow: the former item must contain no readings,
    events, occurrences or browse sessions. Its RAW and matching citation are
    moved transactionally, while the old item remains as a parented
    ``source_alias`` with a complete audit trail.
    """

    initialize(root)
    citation_text = citation_text.strip()
    if not citation_text:
        raise ValueError("citation_text nie może być pusty")

    with connect(root) as connection:
        source_row = connection.execute(
            "SELECT * FROM sources WHERE id = ?", (source_id,)
        ).fetchone()
        if source_row is None:
            raise KeyError(f"Nie znaleziono źródła: {source_id}")
        source = dict(source_row)

        source_item = connection.execute(
            "SELECT id FROM source_items WHERE id = ?", (source_item_id,)
        ).fetchone()
        if source_item is None:
            raise KeyError(f"Nie znaleziono source_item: {source_item_id}")

        archive_root = (root / "research" / "archive" / "originals").resolve()
        archive_path = (root / source["archive_path"]).resolve()
        if archive_root not in archive_path.parents:
            raise ValueError("Ścieżka RAW wychodzi poza research/archive/originals")
        if not archive_path.is_file():
            raise FileNotFoundError(f"Brak zarchiwizowanego RAW: {archive_path}")
        actual_sha256 = sha256_file(archive_path)
        if actual_sha256 != source["sha256"]:
            raise ValueError(
                "Suma SHA-256 RAW nie zgadza się z rekordem sources; "
                "dołączenie zostało przerwane"
            )

        artifact_id = stable_id("artifact", "legacy", source_id)
        citation_id = stable_id("citation", "legacy", source_id)
        now = utc_now()

        owning_item = connection.execute(
            "SELECT * FROM source_items WHERE legacy_source_id = ?", (source_id,)
        ).fetchone()
        if owning_item is not None and owning_item["id"] != source_item_id:
            existing = connection.execute(
                "SELECT * FROM artifacts WHERE legacy_source_id = ?", (source_id,)
            ).fetchone()
            already_rehomed = (
                owning_item["parent_id"] == source_item_id
                and owning_item["item_type"] == "source_alias"
                and existing is not None
                and existing["source_item_id"] == source_item_id
            )
            if not already_rehomed and not rehome_existing:
                raise ValueError(
                    f"Źródło {source_id} jest już przypisane do innego source_item: "
                    f"{owning_item['id']}"
                )
            if not already_rehomed:
                old_item_id = owning_item["id"]
                blockers = {
                    "readings": connection.execute(
                        "SELECT COUNT(*) FROM readings WHERE source_item_id=?",
                        (old_item_id,),
                    ).fetchone()[0],
                    "events": connection.execute(
                        "SELECT COUNT(*) FROM events WHERE source_item_id=?",
                        (old_item_id,),
                    ).fetchone()[0],
                    "occurrences": connection.execute(
                        "SELECT COUNT(*) FROM person_occurrences WHERE source_item_id=?",
                        (old_item_id,),
                    ).fetchone()[0],
                    "browse_sessions": connection.execute(
                        "SELECT COUNT(*) FROM browse_sessions WHERE source_item_id=?",
                        (old_item_id,),
                    ).fetchone()[0],
                }
                if any(blockers.values()):
                    raise ValueError(
                        "Rehome wymaga pustego technicznego source_item; blokery: "
                        + json.dumps(blockers, ensure_ascii=False, sort_keys=True)
                    )
                if existing is None or existing["id"] != artifact_id:
                    raise ValueError("Rehome wymaga dokładnie jednego oczekiwanego RAW")
                existing_citation = connection.execute(
                    "SELECT * FROM citations WHERE artifact_id=?", (artifact_id,)
                ).fetchall()
                before = {
                    "source_item": dict(owning_item),
                    "artifact": dict(existing),
                    "citations": [dict(row) for row in existing_citation],
                }
                connection.execute(
                    "UPDATE artifacts SET source_item_id=? WHERE id=?",
                    (source_item_id, artifact_id),
                )
                connection.execute(
                    "UPDATE citations SET source_item_id=? WHERE artifact_id=?",
                    (source_item_id, artifact_id),
                )
                alias_metadata = json.loads(owning_item["metadata_json"] or "{}")
                alias_metadata["superseded_by_source_item_id"] = source_item_id
                alias_metadata["alias_reason"] = "technical_single_scan_item_consolidated_into_register"
                connection.execute(
                    """UPDATE source_items
                       SET parent_id=?, item_type='source_alias',
                           metadata_json=? WHERE id=?""",
                    (
                        source_item_id,
                        json.dumps(alias_metadata, ensure_ascii=False, sort_keys=True),
                        old_item_id,
                    ),
                )
                after = {
                    "source_item": dict(
                        connection.execute(
                            "SELECT * FROM source_items WHERE id=?", (old_item_id,)
                        ).fetchone()
                    ),
                    "artifact": dict(
                        connection.execute(
                            "SELECT * FROM artifacts WHERE id=?", (artifact_id,)
                        ).fetchone()
                    ),
                    "citations": [
                        dict(row)
                        for row in connection.execute(
                            "SELECT * FROM citations WHERE artifact_id=?", (artifact_id,)
                        ).fetchall()
                    ],
                }
                connection.execute(
                    """INSERT INTO audit_log(
                           occurred_at, actor, action, object_type, object_id,
                           before_json, after_json, rationale
                       ) VALUES (?, ?, 'source_artifact_rehome', 'source_item', ?, ?, ?, ?)""",
                    (
                        now,
                        actor,
                        old_item_id,
                        json.dumps(before, ensure_ascii=False, sort_keys=True),
                        json.dumps(after, ensure_ascii=False, sort_keys=True),
                        "Konsolidacja technicznego itemu pojedynczego skanu z naturalną jednostką rejestru; RAW pozostaje niezmieniony.",
                    ),
                )

        existing_artifact = connection.execute(
            "SELECT * FROM artifacts WHERE legacy_source_id = ?", (source_id,)
        ).fetchone()
        if existing_artifact is not None and (
            existing_artifact["id"] != artifact_id
            or existing_artifact["source_item_id"] != source_item_id
        ):
            raise ValueError(
                f"Źródło {source_id} jest już przypisane do innego artifact/source_item: "
                f"{existing_artifact['id']} / {existing_artifact['source_item_id']}"
            )

        existing_artifact_id = connection.execute(
            "SELECT * FROM artifacts WHERE id = ?", (artifact_id,)
        ).fetchone()
        if existing_artifact_id is not None and (
            existing_artifact_id["legacy_source_id"] != source_id
            or existing_artifact_id["source_item_id"] != source_item_id
        ):
            raise ValueError(
                f"Artifact {artifact_id} jest już przypisany do innego źródła/source_item"
            )

        connection.execute(
            """
            INSERT INTO artifacts(
                id, source_item_id, legacy_source_id, artifact_type, path,
                media_type, sha256, immutable, created_at, metadata_json
            ) VALUES (?, ?, ?, 'RAW', ?, ?, ?, 1, ?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (
                artifact_id,
                source_item_id,
                source_id,
                source["archive_path"],
                source["media_type"],
                source["sha256"],
                source["captured_at"],
                source["metadata_json"],
            ),
        )
        artifact = dict(
            connection.execute(
                "SELECT * FROM artifacts WHERE id = ?", (artifact_id,)
            ).fetchone()
        )
        expected_artifact = {
            "source_item_id": source_item_id,
            "legacy_source_id": source_id,
            "artifact_type": "RAW",
            "path": source["archive_path"],
            "media_type": source["media_type"],
            "sha256": source["sha256"],
            "immutable": 1,
        }
        artifact_differences = {
            key: {"existing": artifact.get(key), "requested": value}
            for key, value in expected_artifact.items()
            if artifact.get(key) != value
        }
        if artifact_differences:
            raise ValueError(
                "Istniejące dołączenie artifact różni się od żądania: "
                f"{json.dumps(artifact_differences, ensure_ascii=False, sort_keys=True)}"
            )

        existing_citation_row = connection.execute(
            "SELECT * FROM citations WHERE id = ?", (citation_id,)
        ).fetchone()
        if existing_citation_row is None:
            connection.execute(
                """
                INSERT INTO citations(
                    id, source_item_id, artifact_id, citation_text, locator,
                    accessed_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    citation_id,
                    source_item_id,
                    artifact_id,
                    citation_text,
                    locator,
                    source["accessed_at"],
                    now,
                ),
            )
        else:
            existing_citation = dict(existing_citation_row)
            citation_ownership = {
                "source_item_id": source_item_id,
                "artifact_id": artifact_id,
            }
            ownership_differences = {
                key: {"existing": existing_citation.get(key), "requested": value}
                for key, value in citation_ownership.items()
                if existing_citation.get(key) != value
            }
            if ownership_differences:
                raise ValueError(
                    "Istniejące dołączenie citation należy do innego "
                    "artifact/source_item: "
                    f"{json.dumps(ownership_differences, ensure_ascii=False, sort_keys=True)}"
                )

            requested_citation = {
                "citation_text": citation_text,
                "locator": locator,
            }
            citation_differences = {
                key: {"existing": existing_citation.get(key), "requested": value}
                for key, value in requested_citation.items()
                if existing_citation.get(key) != value
            }
            if citation_differences and not update_citation:
                raise ValueError(
                    "Istniejące dołączenie citation różni się od żądania: "
                    f"{json.dumps(citation_differences, ensure_ascii=False, sort_keys=True)}"
                )
            if citation_differences:
                connection.execute(
                    """
                    UPDATE citations
                    SET citation_text = ?, locator = ?
                    WHERE id = ?
                    """,
                    (citation_text, locator, citation_id),
                )
                updated_citation = dict(
                    connection.execute(
                        "SELECT * FROM citations WHERE id = ?", (citation_id,)
                    ).fetchone()
                )
                connection.execute(
                    """
                    INSERT INTO audit_log(
                        occurred_at, actor, action, object_type, object_id,
                        before_json, after_json, rationale
                    ) VALUES (?, ?, 'source_citation_update', 'citation', ?, ?, ?, ?)
                    """,
                    (
                        now,
                        actor,
                        citation_id,
                        json.dumps(existing_citation, ensure_ascii=False, sort_keys=True),
                        json.dumps(updated_citation, ensure_ascii=False, sort_keys=True),
                        "Jawna korekta wyłącznie citation_text i locator dla "
                        "istniejącego dołączenia RAW.",
                    ),
                )

        citation = dict(
            connection.execute(
                "SELECT * FROM citations WHERE id = ?", (citation_id,)
            ).fetchone()
        )
        expected_citation = {
            "source_item_id": source_item_id,
            "artifact_id": artifact_id,
            "citation_text": citation_text,
            "locator": locator,
        }
        citation_differences = {
            key: {"existing": citation.get(key), "requested": value}
            for key, value in expected_citation.items()
            if citation.get(key) != value
        }
        if citation_differences:
            raise ValueError(
                "Istniejące dołączenie citation różni się od żądania: "
                f"{json.dumps(citation_differences, ensure_ascii=False, sort_keys=True)}"
            )

        attached = {
            "source_id": source_id,
            "source_item_id": source_item_id,
            "artifact_id": artifact_id,
            "citation_id": citation_id,
            "sha256": source["sha256"],
        }
        already_audited = connection.execute(
            """
            SELECT 1 FROM audit_log
            WHERE action = 'source_artifact_attach' AND object_type = 'artifact'
              AND object_id = ?
            """,
            (artifact_id,),
        ).fetchone()
        if already_audited is None:
            connection.execute(
                """
                INSERT INTO audit_log(
                    occurred_at, actor, action, object_type, object_id,
                    before_json, after_json, rationale
                ) VALUES (?, ?, 'source_artifact_attach', 'artifact', ?, NULL, ?, ?)
                """,
                (
                    now,
                    actor,
                    artifact_id,
                    json.dumps(attached, ensure_ascii=False, sort_keys=True),
                    "Dołączenie istniejącego zarchiwizowanego RAW i cytowania "
                    "do istniejącego source_item; bez odczytu.",
                ),
            )
        return attached
