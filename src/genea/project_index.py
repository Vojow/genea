from __future__ import annotations

import re
import shutil
import sqlite3
import subprocess
import unicodedata
import zipfile
from difflib import SequenceMatcher
from pathlib import Path
from xml.etree import ElementTree

from .db import connect, initialize
from .workspace import iter_files, sha256_file, utc_now

try:
    from rapidfuzz.fuzz import ratio as _rapidfuzz_ratio
except ImportError:
    _rapidfuzz_ratio = None


TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".json",
    ".jsonl",
    ".csv",
    ".tsv",
    ".yaml",
    ".yml",
    ".html",
    ".htm",
    ".xml",
}
INDEX_EXTENSIONS = TEXT_EXTENSIONS | {".pdf", ".docx", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}
MAX_TEXT_BYTES = 4 * 1024 * 1024


def _extract_docx(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        payload = archive.read("word/document.xml")
    root = ElementTree.fromstring(payload)
    return " ".join(node.text or "" for node in root.iter() if node.tag.endswith("}t"))


def _extract_pdf(path: Path) -> str:
    executable = shutil.which("pdftotext")
    if not executable:
        return ""
    result = subprocess.run(
        [executable, str(path), "-"],
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout


def extract_text(path: Path) -> str:
    try:
        if path.suffix.lower() in TEXT_EXTENSIONS:
            return path.read_bytes()[:MAX_TEXT_BYTES].decode("utf-8", errors="replace")
        if path.suffix.lower() == ".docx":
            return _extract_docx(path)[:MAX_TEXT_BYTES]
        if path.suffix.lower() == ".pdf":
            return _extract_pdf(path)[:MAX_TEXT_BYTES]
    except (OSError, ValueError, zipfile.BadZipFile, subprocess.SubprocessError):
        return ""
    return ""


def index_project(root: Path, source_dir: Path) -> dict:
    initialize(root)
    root = root.expanduser().resolve()
    source_dir = source_dir.expanduser().resolve()
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Nie znaleziono katalogu projektu: {source_dir}")
    count = 0
    text_count = 0
    seen_paths: set[str] = set()
    now = utc_now()
    with connect(root) as connection:
        for path in iter_files(source_dir, INDEX_EXTENSIONS):
            stat = path.stat()
            text = extract_text(path)
            display_path = path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)
            seen_paths.add(display_path)
            connection.execute(
                """
                INSERT INTO project_files(path, sha256, size, modified_at, indexed_at, extracted_text)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    sha256 = excluded.sha256,
                    size = excluded.size,
                    modified_at = excluded.modified_at,
                    indexed_at = excluded.indexed_at,
                    extracted_text = excluded.extracted_text
                """,
                (display_path, sha256_file(path), stat.st_size, stat.st_mtime, now, text),
            )
            count += 1
            text_count += bool(text.strip())
        # Synchronize only the requested directory. Other indexed directories,
        # including siblings with a common name prefix, retain their entries.
        for row in connection.execute("SELECT path FROM project_files").fetchall():
            indexed_path = Path(row["path"])
            if not indexed_path.is_absolute():
                indexed_path = root / indexed_path
            indexed_path = indexed_path.resolve()
            if indexed_path.is_relative_to(source_dir) and row["path"] not in seen_paths:
                connection.execute("DELETE FROM project_files WHERE path=?", (row["path"],))
    return {"indexed": count, "with_text": text_count, "directory": str(source_dir)}


def _snippet(text: str, terms: list[str], width: int = 240) -> str:
    normalized = text.lower()
    positions = [normalized.find(term) for term in terms if normalized.find(term) >= 0]
    start = max(0, (min(positions) if positions else 0) - width // 3)
    value = re.sub(r"\s+", " ", text[start : start + width]).strip()
    return ("…" if start else "") + value + ("…" if start + width < len(text) else "")


def _fold_search(value: str) -> str:
    """Unicode case/diacritic folding for local lexical and fuzzy search only."""

    return "".join(
        character for character in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(character)
    )


def _fts_query(terms: list[str]) -> str:
    # Neither boolean operators nor punctuation supplied by the user are FTS
    # syntax. Each literal token is a prefix, combined with an explicit AND.
    return " AND ".join('"' + term.replace('"', '""') + '"*' for term in terms)


def _like_query(columns: tuple[str, ...], terms: list[str]) -> tuple[str, list[str]]:
    clause = "(" + " OR ".join(f"genea_search_fold({column}) LIKE ? ESCAPE '\\'" for column in columns) + ")"
    parameters = []
    for term in terms:
        literal = _fold_search(term).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        parameters.extend([f"%{literal}%"] * len(columns))
    return " AND ".join([clause] * len(terms)), parameters


def _search_files(connection: sqlite3.Connection, terms: list[str], limit: int):
    rows = []
    has_fts = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='project_files_fts'"
    ).fetchone()
    if has_fts and not any("_" in term for term in terms):
        try:
            rows = connection.execute(
                """SELECT pf.path,pf.extracted_text FROM project_files_fts
                   JOIN project_files pf ON pf.rowid=project_files_fts.rowid
                   WHERE project_files_fts MATCH ? ORDER BY pf.path LIMIT ?""",
                (_fts_query(terms), limit),
            ).fetchall()
            if len(rows) == limit:
                return rows
        except Exception as error:
            # SQLCipher exposes its own DB-API OperationalError class.
            if type(error).__name__ != "OperationalError" or "no such module: fts5" not in str(error).casefold():
                raise
    # A nonempty prefix result can still omit interior-substring matches.
    # Complete a partial result with legacy LIKE hits, and use the same query
    # when SQLite has no FTS5. Deduplicate by backing path and retain the
    # deterministic lexical ordering before applying the requested limit.
    clauses, parameters = _like_query(("path", "extracted_text"), terms)
    substring_rows = connection.execute(
        "SELECT path,extracted_text FROM project_files WHERE " + clauses + " ORDER BY path LIMIT ?",
        (*parameters, limit),
    ).fetchall()
    by_path = {row["path"]: row for row in (*rows, *substring_rows)}
    return [by_path[path] for path in sorted(by_path)[:limit]]


def _fuzzy_score(terms: list[str], text: str) -> float | None:
    tokens = sorted(set(re.findall(r"\w+", _fold_search(text))))
    if not tokens:
        return None
    scores = []
    for term in terms:
        target = _fold_search(term)
        if target in tokens:
            scores.append(100.0)
            continue
        # Short names and numerical dates must match literally: a one-letter
        # change in these tokens is too broad even for candidate retrieval.
        if len(target) < 4 or any(character.isdigit() for character in target):
            return None
        score = max(
            float(_rapidfuzz_ratio(target, token)) if _rapidfuzz_ratio is not None
            else 100.0 * SequenceMatcher(None, target, token, autojunk=False).ratio()
            for token in tokens
        )
        if score < 80.0:
            return None
        scores.append(score)
    return round(sum(scores) / len(scores), 6)


def cross_check(root: Path, query: str, limit: int = 20, *, fuzzy: bool = False) -> list[dict]:
    """Return local search leads; similarity never decides genealogy identity.

    Fuzzy candidate retrieval/ranking is opt-in. Scores are text similarity on
    a 0..100 scale, not probability, verification or evidence quality. No
    entity, occurrence, relationship or research decision is written here.
    """

    initialize(root)
    terms = list(dict.fromkeys(term.casefold() for term in re.findall(r"\w+", query)))
    if not terms:
        raise ValueError("Podaj nazwisko, miejscowość, rok lub inną szukaną frazę")
    if limit < 0:
        raise ValueError("Limit nie może być ujemny")
    if limit == 0:
        return []
    candidates: dict[tuple[str, str], dict] = {}

    def collect(files, entities, sources, *, exact: bool):
        for kind, rows in (("project-file", files), ("entity", entities), ("source", sources)):
            for row in rows:
                if kind == "project-file":
                    backing_id = row["path"]
                    text = row["path"] + " " + row["extracted_text"]
                    result = {"kind": kind, "path": backing_id,
                              "snippet": _snippet(row["extracted_text"], terms) if row["extracted_text"] else ""}
                elif kind == "entity":
                    backing_id = row["id"]
                    text = row["display_name"]
                    result = {"kind": kind, "entity_id": backing_id, "entity_type": row["entity_type"],
                              "name": text, "notes": row["notes"]}
                else:
                    backing_id = row["id"]
                    text = row["title"] + " " + row["metadata_json"]
                    result = {"kind": kind, "source_id": backing_id, "title": row["title"],
                              "service": row["service"], "url": row["source_url"]}
                key = (kind, backing_id)
                if key in candidates:
                    continue
                score = 100.0 if exact else _fuzzy_score(terms, text)
                if score is None:
                    continue
                if fuzzy:
                    lexical = exact or score == 100.0
                    result.update({
                        "match_basis": "lexical" if lexical else "fuzzy_text",
                        "ranking_score": score,
                        "ranking_engine": "lexical" if lexical else (
                            "rapidfuzz" if _rapidfuzz_ratio is not None else "difflib"
                        ),
                        "ranking_semantics": "text_similarity_0_100_not_probability",
                        "search_status": "candidate",
                    })
                candidates[key] = result

    with connect(root) as connection:
        connection.create_function("genea_search_fold", 1, lambda value: _fold_search(value or ""))
        entity_clauses, entity_parameters = _like_query(("display_name",), terms)
        source_clauses, source_parameters = _like_query(("title", "metadata_json"), terms)
        collect(
            _search_files(connection, terms, limit),
            connection.execute(
                "SELECT id,entity_type,display_name,notes FROM entities WHERE " + entity_clauses
                + " ORDER BY display_name,id LIMIT ?", (*entity_parameters, limit),
            ).fetchall(),
            connection.execute(
                "SELECT id,title,service,source_url,metadata_json FROM sources WHERE " + source_clauses
                + " ORDER BY id LIMIT ?", (*source_parameters, limit),
            ).fetchall(),
            exact=True,
        )
        if fuzzy:
            collect(
                connection.execute("SELECT path,extracted_text FROM project_files ORDER BY path").fetchall(),
                connection.execute("SELECT id,entity_type,display_name,notes FROM entities ORDER BY display_name,id").fetchall(),
                connection.execute("SELECT id,title,service,source_url,metadata_json FROM sources ORDER BY id").fetchall(),
                exact=False,
            )
    results = list(candidates.values())
    if fuzzy:
        kind_order = {"project-file": 0, "entity": 1, "source": 2}
        results.sort(key=lambda result: (
            -result["ranking_score"], kind_order[result["kind"]],
            result.get("path") or result.get("entity_id") or result["source_id"],
        ))
    return results[:limit]

