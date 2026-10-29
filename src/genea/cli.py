from __future__ import annotations

import argparse
import getpass
import json
import shutil
import sys
from pathlib import Path

from . import research_telemetry
from .community_research import (
    plan_community_research,
    prepare_community_research_log,
    validate_search_timestamp,
)

from .analysis import import_analysis, run_analysis
from .research_discovery import load_discovery_plan
from .archive import (
    add_source,
    attach_source_artifact,
    get_source,
    list_sources,
    register_source_item,
    validate_source_url,
)
from .archive_providers import (
    archive_manifest,
    archive_provider,
    discover_archive,
    list_archive_providers,
)
from .browser import capture_browser, open_browser, playwright_cli
from .command_envelope import set_projection_status
from .curation import apply_family_writeback, family_writeback_preview
from .curation_queue import (
    apply_curation_queue_decision,
    curation_queue_preview,
)
from .event_curation import (
    apply_event_record_decision,
    event_record_candidate_preview,
    list_event_record_candidates,
    refresh_event_record_candidates,
)
from .identity_curation import add_identity_candidate
from .identity_only_curation import (
    apply_identity_only_agent_ratification,
    apply_identity_only_external_candidate_disposition,
    apply_identity_only_external_shell_merge,
    apply_identity_only_human_confirmation,
    identity_only_agent_ratification_preview,
    identity_only_external_candidate_disposition_preview,
    identity_only_external_shell_merge_preview,
    identity_only_human_confirmation_preview,
)
from .identity_only_local_curation import (
    apply_identity_only_local_agent_ratification,
    apply_identity_only_local_candidate_resolution,
    identity_only_local_agent_ratification_preview,
    identity_only_local_candidate_resolution_preview,
)
from .identity_only_local_human_curation import (
    apply_identity_only_local_human_candidate_resolution,
    identity_only_local_human_candidate_resolution_preview,
)
from .identity_source_profile_curation import (
    apply_identity_dependency_revalidation,
    capture_identity_dependency_before_state,
    apply_identity_source_profile_agent_ratification,
    apply_identity_source_profile_resolution,
    identity_source_profile_agent_ratification_preview,
    identity_source_profile_resolution_preview,
    identity_dependency_revalidation_preview,
)
from .identity_source_pair_curation import (
    apply_identity_source_pair_agent_ratification,
    apply_identity_source_pair_resolution,
    identity_source_pair_agent_ratification_preview,
    identity_source_pair_resolution_preview,
)
from .identity_source_only_curation import (
    apply_identity_source_only_agent_ratification,
    apply_identity_source_only_resolution,
    identity_source_only_agent_ratification_preview,
    identity_source_only_resolution_preview,
)
from .browse import (
    CAUSE_CODES,
    ISSUE_FIELDS,
    ISSUE_KINDS,
    TRIGGER_TYPES,
    add_browse_finding,
    add_browse_issue,
    add_browse_quality_review,
    add_browse_rendition,
    add_handwriting_sample,
    append_browse_checkpoint,
    assess_browse_issue,
    browse_session_report,
    claim_browse_range,
    close_browse_session,
    create_browse_session,
    create_handwriting_profile,
    export_browse_session_report,
    handwriting_profile_report,
    link_handwriting_profile_source,
    list_browse_sessions,
    plan_browse_range,
    record_browse_inventory_units,
    reopen_browse_session,
    resolve_browse_issue,
    review_browse_range,
    review_browse_rendition_gate,
    set_revisit_trigger_status,
)
from .db import connect, create_migration_snapshot, initialize
from .archive_requests import import_archive_requests, list_archive_requests
from .external_sync import mark_external_object, sync_status, track_external_object
from .external_tree_leads import apply_external_tree_leads, external_tree_leads_preview
from .fan import fan_report, life_profile
from .frontend import export_frontend, serve_frontend, serve_reading_verification
from .migrations import LATEST_SCHEMA_VERSION, apply_migrations
from .mapping_writeback import (
    apply_mapping_writeback,
    mapping_writeback_preview,
    source_input_fingerprint,
)
from .reading_pipeline import (
    apply_reading_pipeline,
    benchmark_reading_pipeline,
    reading_pipeline_preview,
)
from .reading_verification_adapter import (
    compose_reading_verification_proposal,
    export_reading_verification_run,
)
from .reading_snippets import (
    build_reading_snippet_package,
    build_reading_snippet_package_from_pipeline,
    verify_reading_snippet_package,
)
from .source_coverage import (
    apply_source_structure,
    source_coverage_report,
    source_structure_preview,
)
from .source_relative_materialization import (
    apply_source_relative_materialization,
    source_relative_materialization_follow_up,
    source_relative_materialization_preview,
)
from .storage_audit import apply_prune, audit_workspace, plan_prune
from .tmp_lifecycle import apply_tmp_cleanup, plan_tmp_cleanup
from .research_closeout import apply_closeout, prepare_closeout
from .lfs_preflight import inspect_lfs_upload
from .derived_store import DerivedStore
from .lfs_archive import (
    apply_archive as apply_lfs_archive,
    plan_archive as plan_lfs_archive,
    restore_drill as restore_lfs_archive,
    verify_archive as verify_lfs_archive,
)
from .storage_snapshots import (
    apply_snapshot_retention,
    plan_snapshot_retention,
    create_snapshot as create_storage_snapshot,
    restore_drill as restore_storage_snapshot,
    snapshot_if_due,
    verify_checkpoint_journal,
)
from .microfilm_preflight import (
    DEFAULT_ORDINAL_REGEX,
    preflight_microfilm,
    validate_microfilm_inventory,
)
from .volume_index import (
    build_volume_index,
    create_volume_region_snippet,
    qa_volume_index,
    report_volume_index,
    resume_volume_index,
    search_volume_index,
)
from .volume_index_perf import evaluate_performance_gate
from .ocrmypdf_adapter import derive_pdf_with_ocrmypdf
from .volume_index_workflow import (
    derive_volume_image,
    evaluate_volume_index,
    export_external_htr_bundle,
    export_ground_truth,
    export_writeback_candidate,
    import_recognition_attempt,
    plan_kraken_training,
    recognize_volume_index,
    review_volume_region,
    split_ground_truth,
    volume_index_capabilities,
)
from .qwen_volume_index import (
    PROMPT as QWEN_VOLUME_PROMPT, import_qwen_diagnostic,
    prepare_qwen_index, recognize_qwen_volume,
)
from .qwen_overnight import run_qwen_overnight
from .place_studies import (
    create_place_study,
    ensure_place,
    household_timeline,
    import_household_occurrences,
    place_study_coverage_matrix,
    record_place_study_coverage,
    search_local_occurrences,
)
from .place_writeback import apply_place_writeback, place_writeback_preview
from .private_lifecycle import (
    activate_encrypted_database,
    change_private_ui_password,
    initialize_private_storage,
    lock_private_ui,
    migrate_plaintext_copy,
    private_status,
    recover_private_storage_key,
    rollback_encrypted_database,
    rotate_private_data_key,
    unlock_private_ui,
)
from .private_audit import audit_private_storage
from .private_drive_import import import_private_drive_workbook
from .project_index import cross_check, index_project
from .question_backlog import generate_question_backlog
from .question_curation import (
    apply_question_candidate_decision,
    question_candidate_preview,
    refresh_question_candidates,
)
from .research_execution import question_archive_plan
from .render import visual_files
from .research import (
    VALID_QUESTION_TARGET_TYPES,
    add_hypothesis,
    add_research_log_entry,
    add_research_question,
    create_research_case,
    get_research_case,
    link_evidence,
    list_hypotheses,
    link_research_question_target,
    list_research_questions,
    negative_evidence_assessment,
    record_search_coverage,
    set_research_question_priority,
    set_next_action,
)
from .research_hierarchy import (
    VALID_QUESTION_SCOPE_ROLES,
    VALID_SCOPE_RELATIONS,
    VALID_SCOPE_SEMANTICS,
    VALID_SCOPE_STATUSES,
    VALID_SCOPE_TYPES,
    VALID_WORK_ITEM_STATUSES,
    VALID_WORK_ITEM_TYPES,
    apply_research_scope_manifest,
    create_research_scope,
    create_research_work_item,
    link_question_scope,
    link_research_scope_target,
    link_research_scopes,
    preview_research_scope_manifest,
    research_scope_tree,
)
from .review import (
    get_reading_review_decision,
    label_privacy,
    list_reading_review_decisions,
    record_reading_comparison,
    record_reading_review_decision,
    review_claim_status,
)
from .reassessment import (
    agent_review_manifest,
    index_debt_report,
    reassessment_impact_report,
    reassessment_show,
)
from .reassessment_curation import (
    apply_reassessment_writeback,
    reassessment_writeback_preview,
)
from .storage import configured_database_backend
from .workspace import dump_json, project_root, research_root


def _json(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _qwen_night_progress(event: dict) -> None:
    kind = event.get("event")
    ordinal = event.get("ordinal")
    if kind == "preflight_completed":
        info = event.get("model_info", {})
        message = f"preflight OK: {info.get('model_tag', 'model')}"
    elif kind in {"page_started", "rescue_started"}:
        message = f"strona {ordinal}: {'rescue' if kind == 'rescue_started' else 'start'}"
    elif kind == "attempt_failed":
        message = f"strona {ordinal}: błąd {event.get('configuration')}: {event.get('error')}"
    elif kind == "page_finished":
        page = event.get("page", {})
        message = f"strona {ordinal}: {page.get('status', 'zakończona')} ({page.get('elapsed_seconds', 0):.1f} s)"
    elif kind == "run_stopped":
        message = f"koniec: {event.get('status')}"
    else:
        return
    print(f"[qwen-night] {message}", file=sys.stderr, flush=True)


def _json_file(path: str | None, expected_type: type, label: str):
    if path is None:
        return expected_type()
    value = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    if not isinstance(value, expected_type):
        raise ValueError(f"Plik {label} musi zawierać {expected_type.__name__} JSON")
    return value


def _private_json_stdin() -> dict:
    """Read private JSON without echoing it or requiring a plaintext file.

    A framed, chunked form avoids the small canonical-line buffer of a PTY.
    The legacy single-line form remains accepted for scripts and tests.
    """

    restore = None
    if sys.stdin.isatty():
        import termios

        descriptor = sys.stdin.fileno()
        restore = termios.tcgetattr(descriptor)
        hidden = termios.tcgetattr(descriptor)
        hidden[3] &= ~(termios.ECHO | termios.ICANON)
        hidden[6][termios.VMIN] = 1
        hidden[6][termios.VTIME] = 0
        termios.tcsetattr(descriptor, termios.TCSADRAIN, hidden)
    try:
        line = sys.stdin.readline()
        header = line.rstrip("\r\n")
        if header.startswith("GENEA_PRIVATE_JSON_LENGTH:"):
            try:
                length = int(header.partition(":")[2])
            except ValueError as error:
                raise ValueError("Nieprawidłowa długość strumienia importu Drive") from error
            if length <= 0 or length > 10_000_000:
                raise ValueError("Nieprawidłowa długość strumienia importu Drive")
            line = sys.stdin.read(length)
            if len(line) != length:
                raise ValueError("Niekompletny strumień importu Drive")
        elif header == "GENEA_PRIVATE_JSON_V1":
            chunks: list[str] = []
            while True:
                chunk = sys.stdin.readline()
                if not chunk:
                    raise ValueError("Niekompletny strumień importu Drive")
                chunk = chunk.rstrip("\r\n")
                if chunk == "GENEA_PRIVATE_JSON_END":
                    break
                chunks.append(chunk)
            line = "".join(chunks)
    finally:
        if restore is not None:
            import termios

            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, restore)
    if not line:
        raise ValueError("Import Drive wymaga obiektu JSON na stdin")
    payload = json.loads(line)
    if not isinstance(payload, dict):
        raise ValueError("Import Drive wymaga obiektu JSON na stdin")
    return payload


def _prompt_secret(label: str, *, confirm: bool = False) -> str:
    value = getpass.getpass(f"{label}: ")
    if confirm:
        repeated = getpass.getpass(f"Powtórz {label.lower()}: ")
        if value != repeated:
            raise ValueError("Podane wartości nie są identyczne")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="genea",
        description="Lokalny warsztat badań genealogicznych oparty na źródłach.",
    )
    parser.add_argument("--root", default=".", help="Katalog projektu (domyślnie bieżący)")
    sub = parser.add_subparsers(dest="command", required=True)
    research_telemetry.add_parser(sub)

    discovery = sub.add_parser(
        "research-discovery-check",
        help="Sprawdź katalog poszlak i wybór źródła bez bazy, sieci i readerów",
    )
    discovery.add_argument("plan")
    discovery.add_argument("--source-id")
    discovery.add_argument("--lead-id")

    sub.add_parser("init", help="Utwórz lokalny magazyn i bazę SQLite")
    sub.add_parser("doctor", help="Sprawdź wymagane i opcjonalne narzędzia")
    sub.add_parser("migration-status", help="Pokaż wersję schematu bez zmiany bazy")
    migrate = sub.add_parser(
        "migration-run", help="Utwórz snapshot istniejącej bazy i zastosuj migracje"
    )
    migrate.add_argument("--snapshot-dir")
    sub.add_parser(
        "storage-status",
        help="Pokaż backend bazy, szyfrowanie i kontrolę integralności",
    )
    sub.add_parser(
        "storage-audit",
        help="Pokaż zajętość dysku i bezpieczne granice lokalnego sprzątania",
    )
    storage_prune = sub.add_parser(
        "storage-prune",
        help="Zaplanuj lub wykonaj jawnie potwierdzone usunięcie odtwarzalnych zależności/cache",
    )
    storage_prune.add_argument(
        "--target",
        action="append",
        required=True,
        help="Ścieżka względem projektu; można powtórzyć",
    )
    storage_prune.add_argument(
        "--apply",
        action="store_true",
        help="Wykonaj plan tylko po podaniu jego niezmienionego tokenu",
    )
    storage_prune.add_argument(
        "--confirm",
        help="Token confirmToken zwrócony przez wcześniejszy dry-run tego samego planu",
    )
    tmp_finalize = sub.add_parser(
        "research-tmp-finalize",
        help="Zaplanuj lub usuń jeden ukończony run tymczasowy po weryfikacji trwałych wyników",
    )
    tmp_finalize.add_argument(
        "run_dir",
        help="Katalog pojedynczego runu pod research/tmp z manifestem .genea-run.json",
    )
    tmp_finalize.add_argument(
        "--apply",
        action="store_true",
        help="Usuń run wyłącznie po podaniu tokenu z aktualnego planu",
    )
    tmp_finalize.add_argument(
        "--confirm",
        help="Token confirmToken zwrócony przez wcześniejszy plan tego samego runu",
    )
    closeout = sub.add_parser("research-closeout", help="Zachowaj wyniki i zaplanuj GC ukończonych prób")
    closeout.add_argument("--snapshot-store", type=Path)
    closeout.add_argument("--snapshot-id", help="Użyj istniejącego, zweryfikowanego snapshotu")
    closeout.add_argument("--output", type=Path, help="Nowy plan w research/records")
    closeout.add_argument("--publish", action="store_true", help="Push HEAD po preflight LFS")
    closeout.add_argument("--apply", type=Path, metavar="PLAN", help="Wykonaj zapisany plan GC")
    closeout.add_argument("--confirm", help="Token zapisanego planu")
    lfs_preflight = sub.add_parser(
        "storage-lfs-preflight",
        help="Sprawdź lokalne obiekty LFS całej niepushowanej historii przed uploadem",
    )
    lfs_preflight.add_argument("--upstream", default="origin/main")
    lfs_archive = sub.add_parser(
        "storage-lfs-archive",
        help="Zaplanuj lub wykonaj zweryfikowaną kopię wszystkich lokalnych obiektów LFS",
    )
    lfs_archive.add_argument("destination", help="Istniejący katalog na zewnętrznym nośniku")
    lfs_archive.add_argument("--apply", action="store_true", help="Wykonaj kopię po potwierdzeniu planu")
    lfs_archive.add_argument("--confirm", help="Token confirmToken z niezmienionego planu")
    lfs_verify = sub.add_parser(
        "storage-lfs-verify",
        help="Zweryfikuj wszystkie obiekty i manifest zewnętrznego archiwum LFS",
    )
    lfs_verify.add_argument("destination")
    lfs_restore = sub.add_parser(
        "storage-lfs-restore-drill",
        help="Odtwórz archiwum LFS do nowego izolowanego katalogu i zweryfikuj kopię",
    )
    lfs_restore.add_argument("archive")
    lfs_restore.add_argument("destination")

    derived_put = sub.add_parser(
        "derived-store-put",
        help="Umieść pochodny artefakt w deduplikowanym magazynie operacyjnym",
    )
    derived_put.add_argument("store")
    derived_put.add_argument("source")
    derived_put.add_argument("--retention", choices=["keep", "rebuildable", "temporary", "archived"], default="rebuildable")
    derived_put.add_argument("--privacy", choices=["public", "family_private", "strict_private"], default="family_private")
    derived_put.add_argument("--lineage", action="append")
    derived_put.add_argument("--backing-id", action="append")
    derived_put.add_argument("--source-id", action="append")
    derived_report = sub.add_parser("derived-store-report", help="Zweryfikuj magazyn pochodnych i jego referencje")
    derived_report.add_argument("store")
    derived_ref_add = sub.add_parser("derived-store-ref-add", help="Przypnij jawną referencję do pochodnego artefaktu")
    derived_ref_add.add_argument("store")
    derived_ref_add.add_argument("name")
    derived_ref_add.add_argument("oid")
    derived_ref_remove = sub.add_parser("derived-store-ref-remove", help="Usuń jawną referencję bez usuwania artefaktu")
    derived_ref_remove.add_argument("store")
    derived_ref_remove.add_argument("name")
    derived_gc = sub.add_parser(
        "derived-store-gc",
        help="Zaplanuj lub wykonaj usunięcie wyłącznie nieużywanych pochodnych temporary/rebuildable",
    )
    derived_gc.add_argument("store")
    derived_gc.add_argument("--apply", action="store_true")
    derived_gc.add_argument("--confirm", help="Token confirmToken z niezmienionego planu")

    storage_snapshot = sub.add_parser(
        "storage-snapshot",
        help="Utwórz zaszyfrowany snapshot bazy w zewnętrznym magazynie checkpointów",
    )
    storage_snapshot.add_argument("destination")
    storage_snapshot.add_argument("--if-due-hours", type=float, help="Pomiń snapshot, jeśli ostatni jest młodszy")
    storage_snapshot.add_argument(
        "--keep-last", type=int,
        help="Retencja cyklicznych snapshotów z --if-due-hours; domyślnie 7",
    )
    snapshot_retention = sub.add_parser(
        "storage-snapshot-retention",
        help="Zaplanuj retencję checkpointów SQLCipher, zachowując manifesty i audyt",
    )
    snapshot_retention.add_argument("destination")
    snapshot_retention.add_argument("--keep-last", type=int, default=7)
    snapshot_retention.add_argument("--apply", action="store_true")
    snapshot_retention.add_argument("--confirm")
    snapshot_verify = sub.add_parser("storage-snapshot-verify", help="Zweryfikuj łańcuch checkpointów i ciphertext")
    snapshot_verify.add_argument("destination")
    snapshot_restore = sub.add_parser(
        "storage-snapshot-restore-drill",
        help="Odtwórz wskazany snapshot do nowej testowej ścieżki",
    )
    snapshot_restore.add_argument("archive")
    snapshot_restore.add_argument("snapshot_id")
    snapshot_restore.add_argument("destination")
    sub.add_parser(
        "nas-preflight",
        help="Sprawdź zaszyfrowany magazyn przed uruchomieniem na NAS",
    )
    private_init = sub.add_parser(
        "private-init",
        help="Utwórz klucz, verifier UI i zewnętrzny plik recovery bez migracji bazy",
    )
    private_init.add_argument("--recovery-output", required=True)
    sub.add_parser("private-status", help="Pokaż bezpieczny stan prywatnego magazynu")
    sub.add_parser("private-audit", help="Sprawdź magazyn, eksporty, cache i wycieki sekretów")
    sub.add_parser("private-lock", help="Unieważnij prywatne sesje UI")
    sub.add_parser("private-unlock", help="Sprawdź hasło i zezwól na nowe sesje UI")
    private_recover = sub.add_parser(
        "private-recover", help="Odtwórz klucz Keychain z zewnętrznego recovery"
    )
    private_recover.add_argument("--recovery-file", required=True)
    private_migrate = sub.add_parser(
        "private-migrate-copy",
        help="Utwórz i porównaj nowy ciphertext bez zmiany bazy kanonicznej",
    )
    private_migrate.add_argument("--output")
    private_activate = sub.add_parser(
        "private-activate",
        help="Przełącz zweryfikowany ciphertext i przenieś plaintext poza workspace",
    )
    private_activate.add_argument("--recovery-file", required=True)
    private_activate.add_argument("--plaintext-rollback", required=True)
    private_rollback = sub.add_parser(
        "private-rollback", help="Przywróć zweryfikowaną plaintextową kopię rollback"
    )
    private_rollback.add_argument("--plaintext-rollback", required=True)
    sub.add_parser(
        "private-password-change",
        help="Zmień hasło prywatnego UI i unieważnij sesje",
    )
    private_rotate = sub.add_parser(
        "private-key-rotate",
        help="Wykonaj szyfrowany backup, rekey i utwórz nowy recovery envelope",
    )
    private_rotate.add_argument("--current-recovery-file", required=True)
    private_rotate.add_argument("--new-recovery-output", required=True)
    private_rotate.add_argument("--encrypted-backup", required=True)
    private_drive_import = sub.add_parser(
        "private-drive-import",
        help="Dry-run lub kontrolowany import prywatnego arkusza Drive przez stdin",
    )
    private_drive_import.add_argument(
        "--apply",
        action="store_true",
        help="Zapisz zweryfikowany prywatny podzbiór do aktywnej bazy SQLCipher",
    )

    external_track = sub.add_parser(
        "external-sync-track", help="Dodaj lokalny obiekt do rejestru publikacji zewnętrznych"
    )
    external_track.add_argument("service")
    external_track.add_argument(
        "object_type",
        choices=(
            "event", "claim", "person", "source_item",
            "presentation_person", "presentation_family", "presentation_union",
        ),
    )
    external_track.add_argument("object_id")
    external_track.add_argument(
        "--status",
        choices=("pending", "staged", "published", "needs_review", "failed", "skipped"),
        default="pending",
    )
    external_track.add_argument("--remote-object-id")
    external_track.add_argument("--remote-url")
    external_track.add_argument("--notes")
    external_status = sub.add_parser(
        "external-sync-status", help="Pokaż publikacje i lokalne zmiany od ostatniego wysłania"
    )
    external_status.add_argument("--service")
    external_status.add_argument("--pending-only", action="store_true")
    external_mark = sub.add_parser(
        "external-sync-mark", help="Zmień stan publikacji obiektu w serwisie zewnętrznym"
    )
    external_mark.add_argument("service")
    external_mark.add_argument(
        "object_type",
        choices=(
            "event", "claim", "person", "source_item",
            "presentation_person", "presentation_family", "presentation_union",
        ),
    )
    external_mark.add_argument("object_id")
    external_mark.add_argument(
        "status",
        choices=("pending", "staged", "published", "needs_review", "failed", "skipped"),
    )
    external_mark.add_argument("--remote-object-id")
    external_mark.add_argument("--remote-url")
    external_mark.add_argument("--notes")

    source_add = sub.add_parser("source-add", help="Zarchiwizuj plik lub bezpośredni URL")
    source_add.add_argument("location")
    source_add.add_argument("--title")
    source_add.add_argument("--service")
    source_add.add_argument("--source-url")
    source_add.add_argument("--rights-note")
    sub.add_parser("source-list", help="Pokaż zarchiwizowane źródła")
    source_show = sub.add_parser("source-show", help="Pokaż metrykę źródła")
    source_show.add_argument("source_id")
    source_register = sub.add_parser(
        "source-register",
        help="Zarejestruj zarchiwizowany RAW jako source_item + artifact + citation",
    )
    source_register.add_argument("source_id")
    source_register.add_argument("--item-type", required=True)
    source_register.add_argument("--citation-text", required=True)
    source_register.add_argument("--date-text")
    source_register.add_argument("--locator")
    source_register.add_argument(
        "--source-quality",
        choices=("Original", "Derivative", "Authored", "Indeterminable"),
        default="Indeterminable",
    )
    source_register.add_argument("--actor", default="curator")
    source_artifact_attach = sub.add_parser(
        "source-artifact-attach",
        help="Dołącz zarchiwizowany legacy RAW do istniejącego source_item",
    )
    source_artifact_attach.add_argument("source_id")
    source_artifact_attach.add_argument("source_item_id")
    source_artifact_attach.add_argument("--citation-text", required=True)
    source_artifact_attach.add_argument("--locator")
    source_artifact_attach.add_argument("--actor", default="curator")
    source_artifact_attach.add_argument(
        "--update-citation",
        action="store_true",
        help="Jawnie zaktualizuj wyłącznie citation_text i locator istniejącego cytowania",
    )
    source_artifact_attach.add_argument(
        "--rehome-existing",
        action="store_true",
        help=(
            "Jawnie przenieś RAW i jego cytowanie z pustego technicznego "
            "source_item do właściwej jednostki; stary item pozostaje audytowanym aliasem"
        ),
    )

    sub.add_parser(
        "archive-provider-list",
        help="Pokaż deterministyczne adaptery archiwów i ich możliwości",
    )
    archive_discover = sub.add_parser(
        "archive-discover",
        help="Wyszukaj kandydatów przez adapter bez zapisu do kanonicznej bazy",
    )
    archive_discover.add_argument("provider")
    archive_discover.add_argument("--spec", required=True, help="Plik JSON ze specyfikacją kwerendy")
    archive_discover.add_argument("--output", help="Opcjonalny plik JSON na wynik kwerendy")
    question_archive = sub.add_parser(
        "question-archive-plan",
        help="Zbuduj read-only plan kwerend archiwalnych dla pytania lub kandydatury",
    )
    question_archive.add_argument("question_id")
    question_archive.add_argument("--provider", help="Wybierz gotową specyfikację jednego adaptera")
    question_archive.add_argument("--output", help="Zapisz wybraną specyfikację dla archive-discover")
    archive_manifest_cmd = sub.add_parser(
        "archive-manifest",
        help="Pobierz i znormalizuj manifest zasobu (obecnie IIIF)",
    )
    archive_manifest_cmd.add_argument("provider")
    archive_manifest_cmd.add_argument("resource_url")
    archive_manifest_cmd.add_argument("--output", help="Opcjonalny plik JSON na manifest")
    archive_acquire = sub.add_parser(
        "archive-acquire",
        help="Pobierz zweryfikowany publiczny plik przez wybrany adapter i zarchiwizuj go",
    )
    archive_acquire.add_argument("provider")
    archive_acquire.add_argument("url")
    archive_acquire.add_argument("--title")
    archive_acquire.add_argument(
        "--source-url",
        help="Trwały publiczny URL źródła, gdy URL pobrania jest efemeryczny",
    )
    archive_acquire.add_argument("--rights-note")

    render = sub.add_parser("render", help="Zamień PDF na obrazy dostępne agentowi")
    render.add_argument("source_id")

    browser_open = sub.add_parser("browser-open", help="Otwórz trwałą sesję przeglądarki")
    browser_open.add_argument("url")
    browser_open.add_argument("--session", default="genea")
    browser_capture = sub.add_parser("browser-capture", help="Zarchiwizuj widok zalogowanej strony")
    browser_capture.add_argument("--source-url", required=True)
    browser_capture.add_argument("--title")
    browser_capture.add_argument("--service")
    browser_capture.add_argument("--session", default="genea")
    browser_capture.add_argument("--full-page", action="store_true")
    browser_capture.add_argument("--rights-note")

    analyze = sub.add_parser("analyze", help="Przeanalizuj obraz/PDF wizualnie przez Codex CLI")
    analyze.add_argument("source_id")
    analyze.add_argument("--model")
    analyze.add_argument("--context")
    analyze.add_argument("--dry-run", action="store_true")
    analyze.add_argument("--discovery-plan", required=True, help="Zwalidowany katalog poszlak i selekcja aktu")
    analyze.add_argument("--lead-id", required=True, help="ID wybranej poszlaki w katalogu")
    analysis_import = sub.add_parser("analysis-import", help="Zaimportuj ręcznie poprawiony JSON")
    analysis_import.add_argument("path")
    analysis_import.add_argument("--model")

    project_index_cmd = sub.add_parser(
        "project-index", help="Indeksuj istniejący projekt bez modyfikowania jego plików"
    )
    project_index_cmd.add_argument("directory", nargs="?", default="sources")
    cross = sub.add_parser("cross-check", help="Szukaj zgodności w projekcie i nowych analizach")
    cross.add_argument("query")
    cross.add_argument("--limit", type=int, default=20)
    cross.add_argument(
        "--fuzzy",
        action="store_true",
        help="Rozszerz wyniki o tekstowe kandydatury RapidFuzz; score nie jest probability identity",
    )

    hyp_add = sub.add_parser("hypothesis-add", help="Zapisz hipotezę badawczą")
    hyp_add.add_argument("statement")
    hyp_add.add_argument("--confidence", type=float)
    hyp_add.add_argument("--rationale")
    hyp_link = sub.add_parser("hypothesis-link", help="Połącz hipotezę ze źródłem")
    hyp_link.add_argument("hypothesis_id")
    hyp_link.add_argument("source_id")
    hyp_link.add_argument("--stance", required=True, choices=["supports", "contradicts", "context"])
    hyp_link.add_argument("--analysis-id")
    hyp_link.add_argument("--note")
    sub.add_parser("hypothesis-list", help="Pokaż hipotezy")

    frontend_export = sub.add_parser(
        "frontend-export",
        help="Zbuduj bezpieczny eksport JSON dla lokalnego interfejsu",
    )
    frontend_export.add_argument("--output")
    frontend_serve = sub.add_parser(
        "frontend-serve",
        help="Uruchom interfejs; domyślnie tylko na 127.0.0.1",
    )
    frontend_serve.add_argument("--port", type=int, default=8765)
    frontend_serve.add_argument("--no-open", action="store_true")
    frontend_serve.add_argument(
        "--host",
        choices=("127.0.0.1", "0.0.0.0"),
        default="127.0.0.1",
        help="Adres nasłuchu; 0.0.0.0 tylko wewnątrz kontrolowanego kontenera",
    )
    frontend_serve.add_argument(
        "--prebuilt-assets",
        help="Gotowy katalog dist; pomija npm i nie osadza eksportu w obrazie",
    )
    frontend_serve.add_argument(
        "--private-api",
        action="store_true",
        help="Włącz prywatne endpointy sesyjne dla aktywnej bazy SQLCipher",
    )
    frontend_serve.add_argument(
        "--trusted-private-proxy",
        action="store_true",
        help="Przestarzałe potwierdzenie; użyj także jawnego --trusted-proxy-cidr",
    )
    frontend_serve.add_argument(
        "--trusted-proxy-cidr",
        action="append",
        default=[],
        help="Dozwolony adres lub sieć reverse proxy, np. 172.18.0.0/16; można powtórzyć",
    )

    case_add = sub.add_parser("case-add", help="Utwórz lub uzupełnij sprawę badawczą")
    case_add.add_argument("code")
    case_add.add_argument("title")
    case_add.add_argument("--summary")
    case_show = sub.add_parser("case-show", help="Pokaż sprawę, log i Next Action")
    case_show.add_argument("case_id")
    archive_requests_import = sub.add_parser(
        "archive-requests-import", help="Zapisz projekty kwerend z pliku JSON; bez wysyłania"
    )
    archive_requests_import.add_argument("path")
    sub.add_parser("archive-requests-list", help="Pokaż lokalny rejestr kwerend (dane prywatne)")
    scope_add = sub.add_parser(
        "research-scope-add", help="Dodaj hierarchiczny zakres organizacji badań"
    )
    scope_add.add_argument("scope_id")
    scope_add.add_argument("scope_type", choices=sorted(VALID_SCOPE_TYPES))
    scope_add.add_argument("title")
    scope_add.add_argument("--summary")
    scope_add.add_argument("--status", choices=sorted(VALID_SCOPE_STATUSES), default="active")
    scope_add.add_argument("--legacy-case-id")
    scope_link = sub.add_parser(
        "research-scope-link", help="Połącz dwa zakresy relacją hierarchiczną lub przekrojową"
    )
    scope_link.add_argument("parent_scope_id")
    scope_link.add_argument("child_scope_id")
    scope_link.add_argument("--relation", choices=sorted(VALID_SCOPE_RELATIONS), default="contains")
    scope_link.add_argument("--primary", action="store_true")
    scope_link.add_argument("--sort-order", type=int, default=100)
    scope_link.add_argument("--notes")
    scope_target = sub.add_parser(
        "research-scope-target-add",
        help="Powiąż zakres z osobą, rodziną, gospodarstwem lub miejscem",
    )
    scope_target.add_argument("scope_id")
    scope_target.add_argument("target_type", choices=sorted(VALID_QUESTION_TARGET_TYPES))
    scope_target.add_argument("target_id")
    scope_target.add_argument("target_label")
    scope_target.add_argument("--role", default="research_context")
    scope_target.add_argument("--semantics", choices=sorted(VALID_SCOPE_SEMANTICS), default="research_context")
    scope_target.add_argument("--date-from")
    scope_target.add_argument("--date-to")
    scope_target.add_argument("--notes")
    question_scope = sub.add_parser(
        "question-scope-add", help="Przypnij jedno pytanie do dodatkowego zakresu"
    )
    question_scope.add_argument("question_id")
    question_scope.add_argument("scope_id")
    question_scope.add_argument("--role", choices=sorted(VALID_QUESTION_SCOPE_ROLES), default="supporting")
    question_scope.add_argument("--primary", action="store_true")
    question_scope.add_argument("--notes")
    sub.add_parser("research-scope-tree", help="Pokaż pełną hierarchię i członkostwa pytań")
    scope_import = sub.add_parser(
        "research-scope-import", help="Sprawdź lub zastosuj jawny manifest hierarchii"
    )
    scope_import.add_argument("manifest")
    scope_import.add_argument("--apply", action="store_true")
    work_item = sub.add_parser(
        "research-work-item-add", help="Dodaj zagnieżdżony pakiet pracy lub zadanie"
    )
    work_item.add_argument("work_item_id")
    work_item.add_argument("item_type", choices=sorted(VALID_WORK_ITEM_TYPES))
    work_item.add_argument("title")
    work_item.add_argument("--scope-id")
    work_item.add_argument("--question-id")
    work_item.add_argument("--parent-work-item-id")
    work_item.add_argument("--completion-criterion")
    work_item.add_argument("--status", choices=sorted(VALID_WORK_ITEM_STATUSES), default="planned")
    work_item.add_argument("--priority", type=int, default=100)
    question_add = sub.add_parser("question-add", help="Dodaj atomowe pytanie badawcze")
    question_add.add_argument("case_id")
    question_add.add_argument("question")
    question_add.add_argument("completion_criterion")
    question_add.add_argument("--priority", type=int, default=100)
    question_priority = sub.add_parser(
        "question-priority-set", help="Ustaw kolejność pytania; niższy numer ma pierwszeństwo"
    )
    question_priority.add_argument("question_id")
    question_priority.add_argument("priority", type=int)
    question_target = sub.add_parser(
        "question-target-add", help="Powiąż pytanie z osobą, gospodarstwem lub miejscem"
    )
    question_target.add_argument("question_id")
    question_target.add_argument(
        "target_type",
        choices=[
            "person", "person_occurrence", "named_person", "household", "place",
            "place_study", "family_cluster", "event", "source_item", "other",
        ],
    )
    question_target.add_argument("target_id")
    question_target.add_argument("target_label")
    question_target.add_argument("--primary", action="store_true")
    question_target.add_argument("--notes")
    question_list = sub.add_parser(
        "question-list", help="Pokaż pytania w trwałej kolejności priorytetów"
    )
    question_list.add_argument("--case-id")
    question_list.add_argument("--status", choices=["open", "answered", "blocked", "closed"])
    question_list.add_argument("--target-type")
    question_list.add_argument("--target-id")
    question_list.add_argument("--scope-id")
    question_generate = sub.add_parser(
        "question-generate",
        help="Wykryj deterministyczne kandydatury pytań bez zapisu do bazy",
    )
    question_generate.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Jawne potwierdzenie trybu read-only; jest to jedyny dostępny tryb",
    )
    question_generate.add_argument("--case-id")
    question_generate.add_argument("--rules-path")
    question_generate.add_argument("--scope-path")
    question_generate.add_argument("--format", choices=("json",), default="json")
    question_refresh = sub.add_parser(
        "question-candidate-refresh",
        help="Utrwal obserwacje generatora jako kandydatury wymagające triage",
    )
    question_refresh.add_argument("--case-id")
    question_refresh.add_argument("--actor", required=True)
    question_preview = sub.add_parser(
        "question-candidate-preview",
        help="Pokaż stan, prywatność i fingerprint kandydatury pytania",
    )
    question_preview.add_argument("candidate_id")
    question_apply = sub.add_parser(
        "question-candidate-apply",
        help="Zastosuj audytowalną decyzję triage kandydatury pytania",
    )
    question_apply.add_argument("candidate_id")
    question_apply.add_argument(
        "decision", choices=("promote", "merge", "dismiss", "defer", "supersede")
    )
    question_apply.add_argument("--expected-fingerprint", required=True)
    question_apply.add_argument("--command-id", required=True)
    question_apply.add_argument("--actor", required=True)
    question_apply.add_argument("--rationale", required=True)
    question_apply.add_argument("--target-question-id")
    question_apply.add_argument("--target-candidate-id")
    community_plan = sub.add_parser(
        "community-research-plan", help="Przygotuj ograniczony plan kwerend społecznościowych bez internetu i SQLite"
    )
    community_plan.add_argument("input_json")
    community_log = sub.add_parser(
        "community-research-prepare-log", help="Zwaliduj wyniki i przygotuj istniejący Research Log bez zapisu"
    )
    community_log.add_argument("input_json")
    log_add = sub.add_parser("research-log-add", help="Zapisz wykonaną kwerendę")
    log_add.add_argument("case_id")
    log_add.add_argument("--repository", required=True)
    log_add.add_argument("--query", required=True)
    log_add.add_argument(
        "--result-type",
        required=True,
        choices=["positive", "negative", "inconclusive", "not-searched"],
    )
    log_summary = log_add.add_mutually_exclusive_group(required=True)
    log_summary.add_argument("--result-summary")
    log_summary.add_argument("--result-summary-file", help="Pełny opis lub koperta JSON UTF-8")
    log_add.add_argument("--searched-at", help="Jawna data ISO z timezone; zachowaj ją przy replay")
    log_add.add_argument("--question-id")
    log_add.add_argument("--collection")
    log_add.add_argument("--date-from")
    log_add.add_argument("--date-to")
    log_add.add_argument("--scope")
    coverage_add = sub.add_parser(
        "search-coverage-add", help="Oceń zakres kwerendy dla dowodu negatywnego"
    )
    coverage_add.add_argument("log_entry_id")
    coverage_add.add_argument("--appropriate", action="store_true")
    coverage_add.add_argument("--complete", action="store_true")
    coverage_add.add_argument("--systematic", action="store_true")
    coverage_add.add_argument("--note")
    negative = sub.add_parser("negative-evidence", help="Sprawdź warunki dowodu negatywnego")
    negative.add_argument("case_id")
    next_action = sub.add_parser("next-action-set", help="Ustaw jedyny bieżący następny krok")
    next_action.add_argument("case_id")
    next_action.add_argument("action")
    next_action.add_argument("--question-id")
    next_action.add_argument("--work-item-id")
    next_action.add_argument("--priority", type=int, default=3)

    fan = sub.add_parser("fan-report", help="Zbuduj źródłowy raport FAN")
    target = fan.add_mutually_exclusive_group(required=True)
    target.add_argument("--person-id")
    target.add_argument("--occurrence-id")
    fan.add_argument("--exclude-incidental", action="store_true")
    profile = sub.add_parser("life-profile", help="Pokaż profil życia wobec pytania")
    profile.add_argument("person_id")
    profile.add_argument("--expected-event", action="append")

    place_add = sub.add_parser("place-add", help="Dodaj kontener miejscowości")
    place_add.add_argument("name")
    place_add.add_argument("--type")
    place_add.add_argument("--notes")
    study_add = sub.add_parser("place-study-add", help="Utwórz ograniczone studium miejscowości")
    study_add.add_argument("place_name")
    study_add.add_argument("title")
    study_add.add_argument("--scope", required=True)
    study_add.add_argument("--case-id")
    study_add.add_argument("--date-from")
    study_add.add_argument("--date-to")
    study_coverage = sub.add_parser("place-coverage-add", help="Zapisz pokrycie studium")
    study_coverage.add_argument("study_id")
    study_coverage.add_argument("record_type")
    study_coverage.add_argument(
        "coverage_status", choices=["complete", "partial", "missing", "unsearched"]
    )
    study_coverage.add_argument("--year-from", type=int)
    study_coverage.add_argument("--year-to", type=int)
    study_coverage.add_argument("--completeness", type=float)
    study_coverage.add_argument("--notes")
    coverage_show = sub.add_parser("place-coverage-show", help="Pokaż macierz pokrycia")
    coverage_show.add_argument("study_id")
    occurrence_import = sub.add_parser(
        "place-occurrences-import", help="Zaimportuj lokalny CSV jako imported_unverified"
    )
    occurrence_import.add_argument("path", nargs="?")
    occurrence_import.add_argument("--case-id")
    place_search = sub.add_parser("place-search", help="Szukaj w lokalnym indeksie")
    place_search.add_argument("query")
    place_search.add_argument("--study-id")
    place_search.add_argument("--household-id")
    household = sub.add_parser("household-show", help="Pokaż oś gospodarstwa")
    household.add_argument("household_id")
    comparison = sub.add_parser(
        "reading-compare", help="Zapisz porównanie odczytu ślepego i kontekstowego"
    )
    comparison.add_argument("source_item_id")
    comparison.add_argument("blind_version_id")
    comparison.add_argument("contextual_version_id")
    comparison.add_argument("--differences-json", required=True)
    comparison.add_argument("--reviewer-version-id")
    comparison.add_argument("--status", choices=["draft", "reviewed"], default="draft")
    review_record = sub.add_parser(
        "reading-review-record",
        help="Dopisz agentową lub ludzką decyzję recenzji odczytu",
    )
    review_record.add_argument("source_item_id")
    review_record.add_argument(
        "status",
        choices=[
            "agent_single_pass",
            "agent_consensus",
            "agent_adjudicated",
            "human_review_required",
            "human_reviewed",
        ],
    )
    review_record.add_argument("--inputs-json", required=True)
    review_record.add_argument("--decided-by", required=True)
    review_record.add_argument("--decided-by-kind", required=True, choices=["agent", "human"])
    review_record.add_argument("--decided-by-run-id")
    review_record.add_argument("--rationale", required=True)
    review_record.add_argument("--decision-json")
    review_record.add_argument("--expected-revision", type=int)
    review_show = sub.add_parser(
        "reading-review-show", help="Pokaż decyzję recenzji wraz z wejściami"
    )
    review_show.add_argument("decision_id")
    review_list = sub.add_parser(
        "reading-review-list", help="Pokaż historię decyzji recenzji"
    )
    review_list.add_argument("--source-item-id")
    review_list.add_argument(
        "--status",
        choices=[
            "agent_single_pass",
            "agent_consensus",
            "agent_adjudicated",
            "human_review_required",
            "human_reviewed",
        ],
    )
    review_list.add_argument("--latest-only", action="store_true")
    review_list.add_argument("--limit", type=int, default=100)
    debt_report = sub.add_parser(
        "index-debt-report",
        help="Pokaż read-only dług mapowania source -> segment -> occurrence -> event",
    )
    debt_report.add_argument("--source-item-id")
    debt_report.add_argument("--include-items", action="store_true")
    debt_report.add_argument("--include-restricted", action="store_true")
    debt_report.add_argument("--limit", type=int, default=1000)
    review_manifest = sub.add_parser(
        "agent-review-manifest",
        help="Zbuduj deterministyczny read-only manifest gotowości agent-first",
    )
    review_manifest.add_argument("--source-item-id", action="append", default=[])
    review_manifest.add_argument("--include-reviewed", action="store_true")
    review_manifest.add_argument("--include-restricted", action="store_true")
    review_manifest.add_argument("--limit", type=int, default=1000)
    review_manifest.add_argument(
        "--dry-run",
        action="store_true",
        help="Jawna etykieta zgodności; komenda jest zawsze read-only",
    )
    reassessment_show_parser = sub.add_parser(
        "reassessment-show", help="Pokaż trwały batch reassessmentu i jego stan"
    )
    reassessment_show_parser.add_argument("batch_id")
    reassessment_impact_parser = sub.add_parser(
        "reassessment-impact", help="Pokaż najnowszą listę wpływu batcha"
    )
    reassessment_impact_parser.add_argument("batch_id")
    reassessment_writeback = sub.add_parser(
        "reassessment-writeback",
        help="Zweryfikuj albo zapisz append-only manifest reassessmentu",
    )
    reassessment_writeback.add_argument(
        "--expected-fingerprint",
        help="Fingerprint zaakceptowanego preview; wymagany razem z --apply",
    )
    reassessment_writeback.add_argument("manifest")
    reassessment_writeback.add_argument("--apply", action="store_true")
    privacy = sub.add_parser(
        "privacy-label", help="Oznacz prywatność obiektu przed eksportem"
    )
    privacy.add_argument("object_type")
    privacy.add_argument("object_id")
    privacy.add_argument(
        "classification",
        choices=[
            "public_historical",
            "deceased",
            "living",
            "restricted",
            "family_private",
            "strict_private",
        ],
    )
    privacy.add_argument("--rationale", required=True)
    claim_review = sub.add_parser(
        "claim-review", help="Zmień status twierdzenia przez kontrolowaną recenzję"
    )
    claim_review.add_argument("claim_id")
    claim_review.add_argument(
        "target_status",
        choices=[
            "imported_unverified",
            "Speculation",
            "Possible",
            "Probable",
            "Confirmed",
            "Rejected",
        ],
    )
    claim_review.add_argument("--reviewer", required=True)
    claim_review.add_argument("--rationale", required=True)
    identity_candidate_add = sub.add_parser(
        "identity-candidate-add",
        help="Dodaj audytowaną nierozstrzygniętą kandydaturę identity bez scalania",
    )
    identity_candidate_add.add_argument("occurrence_a_id")
    identity_candidate_add.add_argument("occurrence_b_id")
    identity_candidate_add.add_argument("--score", required=True, type=float)
    identity_candidate_add.add_argument("--basis", required=True)
    identity_candidate_add.add_argument("--actor", required=True)
    identity_candidate_add.add_argument("--support", action="append", default=[])
    identity_candidate_add.add_argument("--against", action="append", default=[])
    identity_candidate_add.add_argument("--human-review-debt", action="append", default=[])
    identity_candidate_add.add_argument("--bundle-id")
    identity_only_merge = sub.add_parser(
        "identity-only-external-shell-merge",
        help="Zweryfikuj albo zastosuj pojedynczą decyzję identity-only dla profilu zewnętrznego",
    )
    identity_only_merge.add_argument("manifest")
    identity_only_merge.add_argument(
        "--apply",
        action="store_true",
        help="Zastosuj zaakceptowany preview; bez tej flagi komenda jest read-only",
    )
    identity_only_merge.add_argument("--expected-fingerprint")
    identity_only_merge.add_argument("--command-id")
    identity_only_merge.add_argument(
        "--refresh-presentation",
        action="store_true",
        help=(
            "Po commicie domenowym odśwież kontrolowaną projekcję; awaria eksportu "
            "zostawia decyzję i oznacza projekcję jako failed"
        ),
    )
    identity_only_disposition = sub.add_parser(
        "identity-only-external-candidate-disposition",
        help=(
            "Zweryfikuj albo zapisz decyzję człowieka dismissing redundantną "
            "kandydaturę external-local bez wniosku same/different"
        ),
    )
    identity_only_disposition.add_argument("manifest")
    identity_only_disposition.add_argument("--apply", action="store_true")
    identity_only_disposition.add_argument("--expected-fingerprint")
    identity_only_disposition.add_argument("--command-id")
    identity_only_ratification = sub.add_parser(
        "identity-only-agent-ratification",
        help="Zweryfikuj albo zapisz append-only ratyfikację agentowej decyzji same",
    )
    identity_only_ratification.add_argument("manifest")
    identity_only_ratification.add_argument("--apply", action="store_true")
    identity_only_ratification.add_argument("--expected-fingerprint")
    identity_only_ratification.add_argument("--command-id")
    identity_only_human_confirmation = sub.add_parser(
        "identity-only-human-confirmation",
        help=(
            "Zweryfikuj albo zapisz append-only potwierdzenie człowieka "
            "dla ratyfikowanej decyzji agentowej"
        ),
    )
    identity_only_human_confirmation.add_argument("manifest")
    identity_only_human_confirmation.add_argument("--apply", action="store_true")
    identity_only_human_confirmation.add_argument("--expected-fingerprint")
    identity_only_human_confirmation.add_argument("--command-id")
    for name, help_text in (
        ("identity-source-profile-resolution",
         "Powiąż wystąpienie aktu z profilem importu jako decyzję agentową oczekującą kontroli człowieka"),
        ("identity-source-profile-agent-ratification",
         "Zapisz append-only ratyfikację dokładnego powiązania aktu z profilem importu"),
        ("identity-source-pair-resolution",
         "Rozstrzygnij osobno tożsamość dwóch wystąpień z aktów przypisanych do jednej osoby"),
        ("identity-source-pair-agent-ratification",
         "Ratyfikuj dokładną bezpośrednią decyzję dla dwóch wystąpień z aktów"),
        ("identity-source-only-resolution",
         "Powiąż dwa nieprzypisane wystąpienia źródłowe bez tworzenia osoby kanonicznej"),
        ("identity-source-only-agent-ratification",
         "Ratyfikuj dokładną źródłową grupę tożsamości oczekującą kontroli człowieka"),
        ("identity-dependency-revalidation",
         "Zapisz append-only rewalidację dokładnych zatwierdzonych przejść zależności"),
    ):
        source_profile_command = sub.add_parser(name, help=help_text)
        source_profile_command.add_argument("manifest")
        source_profile_command.add_argument("--apply", action="store_true")
        source_profile_command.add_argument("--expected-fingerprint")
        source_profile_command.add_argument("--command-id")
    dependency_capture = sub.add_parser(
        "identity-dependency-capture", help="Odczytaj pełny wcześniejszy poprawny stan łańcucha"
    )
    dependency_capture.add_argument("prior_command_id")
    source_group = sub.add_parser(
        "identity-source-group",
        help="Sprawdź albo zapisz jawną introdukcję lub następcę źródłowej grupy tożsamości",
    )
    source_group.add_argument("manifest")
    source_group.add_argument("--apply", action="store_true")
    source_group.add_argument("--expected-fingerprint")
    source_group.add_argument("--command-id")
    source_group_capture = sub.add_parser(
        "identity-source-group-capture",
        help="Odczytaj aktualny zatwierdzony stan źródłowej grupy do nowej jawnej operacji",
    )
    source_group_capture.add_argument("group_id")
    identity_only_local_resolution = sub.add_parser(
        "identity-only-local-candidate-resolution",
        help=(
            "Zweryfikuj albo zastosuj bezpośrednią lokalną decyzję identity-only "
            "bez scalania osób i przepisywania occurrences"
        ),
    )
    identity_only_local_resolution.add_argument("manifest")
    identity_only_local_resolution.add_argument("--apply", action="store_true")
    identity_only_local_resolution.add_argument("--expected-fingerprint")
    identity_only_local_resolution.add_argument("--command-id")
    identity_only_local_human_resolution = sub.add_parser(
        "identity-only-local-human-candidate-resolution",
        help=(
            "Zweryfikuj albo zastosuj bezpośrednią decyzję człowieka dla "
            "lokalnej pary occurrences bez scalania osób"
        ),
    )
    identity_only_local_human_resolution.add_argument("manifest")
    identity_only_local_human_resolution.add_argument("--apply", action="store_true")
    identity_only_local_human_resolution.add_argument("--expected-fingerprint")
    identity_only_local_human_resolution.add_argument("--command-id")
    identity_only_local_ratification = sub.add_parser(
        "identity-only-local-agent-ratification",
        help=(
            "Zweryfikuj albo zapisz append-only ratyfikację bezpośredniej "
            "lokalnej decyzji identity-only"
        ),
    )
    identity_only_local_ratification.add_argument("manifest")
    identity_only_local_ratification.add_argument("--apply", action="store_true")
    identity_only_local_ratification.add_argument("--expected-fingerprint")
    identity_only_local_ratification.add_argument("--command-id")
    family_writeback = sub.add_parser(
        "family-writeback",
        help="Zweryfikuj albo zastosuj audytowany manifest scalania rodziny",
    )
    family_writeback.add_argument("manifest")
    family_writeback.add_argument(
        "--apply",
        action="store_true",
        help="Zastosuj manifest; bez tej flagi wykonywany jest tylko dry-run",
    )
    family_writeback.add_argument(
        "--expected-fingerprint",
        help="Fingerprint zaakceptowanego preview; wymagany razem z --apply",
    )
    family_writeback.add_argument(
        "--command-id",
        help="Unikalny identyfikator idempotentnej komendy; wymagany razem z --apply",
    )
    event_record_detect = sub.add_parser(
        "event-record-candidate-detect",
        help="Dodaj kandydatury równoległych rekordów zdarzeń bez ich rozstrzygania",
    )
    event_record_detect.add_argument("--actor", required=True)
    event_record_list = sub.add_parser(
        "event-record-candidate-list",
        help="Pokaż kolejkę kandydatur: to samo zdarzenie czy różne zdarzenia",
    )
    event_record_list.add_argument(
        "--status",
        choices=[
            "proposed",
            "resolved_same_record",
            "resolved_distinct",
            "deferred",
            "superseded",
        ],
    )
    event_record_preview = sub.add_parser(
        "event-record-candidate-preview",
        help="Pokaż świeży zakres i wpływ decyzji o rekordach zdarzenia",
    )
    event_record_preview.add_argument("candidate_id")
    event_record_apply = sub.add_parser(
        "event-record-candidate-apply",
        help="Zapisz audytowaną decyzję bez usuwania źródłowych zdarzeń",
    )
    event_record_apply.add_argument("candidate_id")
    event_record_apply.add_argument(
        "--decision",
        required=True,
        choices=["same_record", "distinct_records", "defer"],
    )
    event_record_apply.add_argument("--expected-fingerprint", required=True)
    event_record_apply.add_argument("--actor", required=True)
    event_record_apply.add_argument("--rationale", required=True)
    event_record_apply.add_argument("--command-id", required=True)
    queue_preview = sub.add_parser(
        "curation-queue-preview",
        help="Pokaż świeży stan jednej administracyjnej pozycji kolejki",
    )
    queue_preview.add_argument("queue_id")
    queue_apply = sub.add_parser(
        "curation-queue-apply",
        help="Audytowanie rozstrzygnij, odrocz albo odrzuć pozycję kolejki",
    )
    queue_apply.add_argument("queue_id")
    queue_apply.add_argument(
        "--decision", required=True, choices=["resolve", "defer", "dismiss"]
    )
    queue_apply.add_argument("--expected-fingerprint", required=True)
    queue_apply.add_argument("--actor", required=True)
    queue_apply.add_argument("--rationale", required=True)
    queue_apply.add_argument("--command-id", required=True)
    mapping_fingerprint = sub.add_parser(
        "mapping-input-fingerprint",
        help="Oblicz read-only fingerprint wejść źródła do manifestu mapowania",
    )
    mapping_fingerprint.add_argument("source_item_id")
    mapping_writeback = sub.add_parser(
        "mapping-writeback",
        help="Zweryfikuj albo transakcyjnie zastosuj manifest mapowania dokumentu",
    )
    mapping_writeback.add_argument("manifest")
    mapping_writeback.add_argument(
        "--apply",
        action="store_true",
        help="Zastosuj manifest; bez tej flagi wykonywany jest wyłącznie preview",
    )
    source_relative = sub.add_parser(
        "source-relative-materialize",
        help=(
            "Pokaż lub zastosuj bezpieczną materializację nazwanych krewnych "
            "źródłowych bez decyzji identity"
        ),
    )
    source_relative.add_argument("source_item_id")
    source_relative.add_argument(
        "--apply",
        action="store_true",
        help="Zastosuj zaakceptowany preview; bez tej flagi komenda jest read-only",
    )
    source_relative.add_argument("--expected-fingerprint")
    source_relative.add_argument("--command-id")
    source_relative.add_argument("--actor")
    source_relative.add_argument("--rationale")
    place_writeback = sub.add_parser(
        "place-writeback",
        help="Zweryfikuj albo zastosuj audytowany manifest mapowania miejsc",
    )
    place_writeback.add_argument("manifest")
    place_writeback.add_argument(
        "--apply",
        action="store_true",
        help="Zastosuj manifest; bez tej flagi wykonywany jest wyłącznie preview",
    )
    place_writeback.add_argument(
        "--expected-fingerprint",
        help="Fingerprint zaakceptowanego preview; wymagany razem z --apply",
    )
    place_writeback.add_argument(
        "--command-id",
        help="Unikalny identyfikator idempotentnej komendy; wymagany razem z --apply",
    )
    external_tree_leads = sub.add_parser(
        "external-tree-leads-import",
        help="Zweryfikuj lub zapisz nierozstrzygnięte profile i relacje z zewnętrznego drzewa",
    )
    external_tree_leads.add_argument("manifest")
    external_tree_leads.add_argument("--apply", action="store_true")
    external_tree_leads.add_argument("--expected-fingerprint")
    external_tree_leads.add_argument("--refresh-presentation", action="store_true")
    reading_pipeline = sub.add_parser(
        "reading-pipeline",
        help="Zweryfikuj lub zapisz wielowarstwową propozycję odczytu AI bez tworzenia faktów",
    )
    reading_pipeline.add_argument("manifest")
    reading_pipeline.add_argument("--apply", action="store_true")
    reading_pipeline.add_argument("--expected-fingerprint")
    reading_pipeline.add_argument("--actor")
    reading_pipeline.add_argument("--rationale")
    reading_benchmark = sub.add_parser(
        "reading-pipeline-benchmark",
        help="Porównaj propozycję pipeline z ręcznie zatwierdzonym ground truth",
    )
    reading_benchmark.add_argument("candidate_manifest")
    reading_benchmark.add_argument("gold_manifest")
    reading_verification_export = sub.add_parser(
        "reading-verification-export",
        help="Wyeksportuj istniejący blind run do bezbazowej propozycji pipeline",
    )
    reading_verification_export.add_argument("package")
    reading_verification_export.add_argument("audit")
    reading_verification_export.add_argument("--reader-id", required=True)
    reading_verification_compose = sub.add_parser(
        "reading-verification-compose",
        help="Dołącz co najmniej dwa zgodne blind runy do manifestu pipeline",
    )
    reading_verification_compose.add_argument("manifest")
    reading_verification_compose.add_argument("proposals", nargs="+")
    reading_snippet_build = sub.add_parser(
        "reading-snippet-build",
        help="Zbuduj bezbazowy pakiet wycinków do weryfikacji odczytu",
    )
    reading_snippet_build.add_argument("request")
    reading_snippet_build.add_argument("output")
    reading_snippet_build_pipeline = sub.add_parser(
        "reading-snippet-build-from-pipeline",
        help="Zbuduj wycinki wszystkich pól bezpośrednio z manifestu pipeline",
    )
    reading_snippet_build_pipeline.add_argument("manifest")
    reading_snippet_build_pipeline.add_argument("source")
    reading_snippet_build_pipeline.add_argument("output")
    reading_snippet_build_pipeline.add_argument("--locator", required=True)
    reading_snippet_build_pipeline.add_argument(
        "--privacy-classification",
        required=True,
        choices=("public_historical",),
        help="Jawne potwierdzenie klasyfikacji; dane living/restricted są odrzucane",
    )
    reading_snippet_verify = sub.add_parser(
        "reading-snippet-verify",
        help="Zweryfikuj integralność bezbazowego pakietu wycinków",
    )
    reading_snippet_verify.add_argument("output")
    reading_snippet_serve = sub.add_parser(
        "reading-snippet-serve",
        help="Uruchom izolowaną weryfikację blind jednego pakietu snippetów",
    )
    reading_snippet_serve.add_argument("output")
    reading_snippet_serve.add_argument("--port", type=int, default=8766)
    reading_snippet_serve.add_argument("--no-open", action="store_true")
    reading_snippet_serve.add_argument(
        "--prebuilt-assets",
        help="Gotowy katalog dist; pomija npm i nie tworzy eksportu frontendowego",
    )
    reading_snippet_serve.add_argument("--run-id")
    reading_snippet_serve.add_argument("--audit-output")
    source_structure = sub.add_parser(
        "source-structure-writeback",
        help="Zweryfikuj albo zastosuj hierarchię księga–akt i wielowymiarowe coverage",
    )
    source_structure.add_argument("manifest")
    source_structure.add_argument(
        "--apply",
        action="store_true",
        help="Zastosuj manifest; bez tej flagi wykonywany jest wyłącznie preview",
    )
    source_coverage = sub.add_parser(
        "source-coverage-report",
        help="Pokaż najnowsze oceny coverage ksiąg i aktów",
    )
    source_coverage.add_argument("--source-item-id")

    browse_add = sub.add_parser(
        "browse-session-add", help="Załóż kartę systematycznego przeglądania tomu lub indeksu"
    )
    browse_add.add_argument("case_id")
    browse_add.add_argument("source_item_id")
    browse_add.add_argument("title")
    browse_add.add_argument("--created-by", required=True)
    browse_add.add_argument(
        "--unit-type", required=True,
        choices=["index", "register", "volume", "viewer", "collection", "other"],
    )
    browse_add.add_argument(
        "--ordinal-kind", required=True,
        choices=["scan", "page", "folio", "act", "entry", "row", "household", "custom"],
        help="Znaczenie kolejnego numeru używanego do mierzenia postępu",
    )
    browse_add.add_argument(
        "--arrangement", required=True,
        choices=["chronological", "alphabetical", "act_number", "household", "geographical", "mixed", "unknown"],
    )
    browse_add.add_argument(
        "--method", required=True, choices=["manual", "ocr", "llm", "hybrid", "index_lookup"]
    )
    browse_add.add_argument("--scope", required=True)
    browse_add.add_argument("--term", action="append")
    browse_add.add_argument("--question-id")
    browse_add.add_argument("--place-study-id")
    browse_add.add_argument("--artifact-id")
    browse_add.add_argument("--canonical-url")
    browse_add.add_argument("--source-version")
    browse_add.add_argument("--manifest-sha256")
    browse_add.add_argument("--expected-start", type=int)
    browse_add.add_argument("--expected-end", type=int)
    browse_add.add_argument(
        "--privacy", default="public_historical",
        choices=["public_historical", "deceased", "living", "restricted"],
    )
    browse_list = sub.add_parser("browse-session-list", help="Pokaż karty przeglądania i liczbę otwartych problemów")
    browse_list.add_argument("--case-id")
    browse_list.add_argument("--source-item-id")
    browse_list.add_argument(
        "--status", choices=["planned", "in_progress", "paused", "submitted", "completed", "closed_partial", "review_required", "invalidated"]
    )
    browse_show = sub.add_parser("browse-session-show", help="Pokaż pełny raport postępu, jakości i powrotów")
    browse_show.add_argument("session_id")
    browse_export = sub.add_parser("browse-session-export", help="Zapisz raport JSON i czytelną kartę Markdown")
    browse_export.add_argument("session_id")
    browse_close = sub.add_parser("browse-session-close", help="Zamknij kartę po sprawdzeniu bramek jakości")
    browse_close.add_argument("session_id")
    browse_close.add_argument("status", choices=["completed", "closed_partial"])
    browse_close.add_argument("--actor", required=True)
    browse_close.add_argument("--rationale", required=True)
    browse_reopen = sub.add_parser("browse-session-reopen", help="Jawnie otwórz zamkniętą kartę do ponownej analizy")
    browse_reopen.add_argument("session_id")
    browse_reopen.add_argument("--actor", required=True)
    browse_reopen.add_argument("--rationale", required=True)
    inventory_import = sub.add_parser(
        "browse-inventory-import",
        help="Zaimportuj rzeczywisty manifest jednostek (ordinal, lokalizator, hash, dostępność)",
    )
    inventory_import.add_argument("session_id")
    inventory_import.add_argument("path")
    inventory_import.add_argument("--actor", required=True)

    rendition_add = sub.add_parser(
        "browse-rendition-add",
        help="Zarejestruj porównany wariant reprodukcji obrazu lub dokumentu",
    )
    rendition_add.add_argument("session_id")
    rendition_add.add_argument("locator")
    rendition_add.add_argument(
        "--class", dest="rendition_class", required=True,
        choices=["thumbnail", "viewer", "medium", "max", "original", "download", "iiif", "pdf", "other"],
    )
    rendition_add.add_argument(
        "--acquisition-method", required=True,
        choices=["browser_view", "repository_download", "iiif", "api", "pdf", "local_file", "other"],
    )
    rendition_add.add_argument(
        "--access-status", required=True,
        choices=["available", "restricted", "blocked", "missing"],
    )
    rendition_add.add_argument(
        "--quality", required=True,
        choices=["q0_unusable", "q1_poor", "q2_usable", "q3_good", "q4_excellent"],
    )
    rendition_add.add_argument("--created-by", required=True)
    rendition_add.add_argument("--ordinal", type=int)
    rendition_add.add_argument("--width", type=int)
    rendition_add.add_argument("--height", type=int)
    rendition_add.add_argument("--bytes", type=int)
    rendition_add.add_argument("--media-type")
    rendition_add.add_argument("--sha256")
    rendition_add.add_argument("--upscaled", choices=["yes", "no", "unknown"], default="unknown")
    rendition_add.add_argument("--legal-access-checked", action="store_true")
    rendition_add.add_argument("--notes")

    rendition_review = sub.add_parser(
        "browse-rendition-review",
        help="Zapisz decyzję bramki najlepszej legalnie dostępnej reprodukcji",
    )
    rendition_review.add_argument("session_id")
    rendition_review.add_argument("status", choices=["passed", "provisional", "blocked"])
    rendition_review.add_argument("--reviewed-by", required=True)
    rendition_review.add_argument("--rationale", required=True)
    rendition_review.add_argument("--selected-rendition-id")
    rendition_review.add_argument("--representative-ordinal", type=int)
    rendition_review.add_argument("--comparison-json")

    preflight = sub.add_parser(
        "microfilm-preflight",
        help="Przygotuj lokalny manifest, metryki i contact sheets bez zapisu do bazy",
    )
    preflight.add_argument("input", help="Katalog legalnie pozyskanych lokalnych obrazów")
    preflight.add_argument("output", help="Nowy katalog pochodnych, poza INPUT")
    preflight.add_argument(
        "--locator-prefix",
        required=True,
        help="Stabilny prefiks; można użyć {ordinal}, inaczej ordinal zostanie dopisany po dwukropku",
    )
    ordinal_mode = preflight.add_mutually_exclusive_group()
    ordinal_mode.add_argument(
        "--ordinal-start",
        type=int,
        help="Przypisz kolejne ordinale od tej wartości według naturalnej kolejności plików",
    )
    ordinal_mode.add_argument(
        "--ordinal-regex",
        default=DEFAULT_ORDINAL_REGEX,
        help="Regex odczytujący ordinal z nazwy; pierwsza grupa musi być liczbą",
    )
    preflight.add_argument("--recursive", action="store_true", help="Uwzględnij podkatalogi")
    preflight.add_argument("--contact-sheet-size", type=int, default=30)
    preflight.add_argument("--contact-sheet-columns", type=int, default=5)
    preflight.add_argument("--duplicate-window", type=int, default=40)
    preflight.add_argument(
        "--ocr-ordinal",
        action="append",
        type=int,
        help="Uruchom pomocniczy Tesseract tylko dla wskazanego ordinalu; można powtórzyć",
    )
    preflight.add_argument("--ocr-language", help="Kod języka Tesseract, np. pol lub rus")

    inventory_validate = sub.add_parser(
        "microfilm-inventory-validate",
        help="Sprawdź ręcznie poprawiony inventory i przelicz kanoniczny hash bez zapisu do bazy",
    )
    inventory_validate.add_argument("path")
    inventory_validate.add_argument("--expected-start", type=int)
    inventory_validate.add_argument("--expected-end", type=int)

    volume_ocrmypdf = sub.add_parser(
        "volume-index-ocrmypdf",
        help="Utwórz prywatny, hashowany PDF pochodny przez opcjonalny OCRmyPDF",
    )
    volume_ocrmypdf.add_argument("input", help="Lokalny źródłowy PDF; nie zostanie zmieniony")
    volume_ocrmypdf.add_argument("output", help="Nowy katalog pochodny poza RAW i sources")
    volume_ocrmypdf.add_argument(
        "--language", action="append", help="Kod modelu Tesseract; można powtórzyć (domyślnie pol)"
    )
    volume_ocrmypdf.add_argument("--rotate-pages", action="store_true")
    volume_ocrmypdf.add_argument("--deskew", action="store_true")
    volume_ocrmypdf.add_argument("--sidecar", action="store_true")
    volume_ocrmypdf.add_argument("--locator-prefix")
    volume_ocrmypdf.add_argument("--source-id")
    volume_ocrmypdf.add_argument("--source-url")

    volume_build = sub.add_parser(
        "volume-index-build",
        help="Zbuduj lokalny indeks kandydatur dla PDF albo katalogu obrazów",
    )
    volume_build.add_argument("input", help="Lokalny PDF albo katalog legalnie pozyskanych obrazów")
    volume_build.add_argument("output", help="Nowy katalog pochodnego indeksu, poza INPUT")
    volume_build.add_argument(
        "--locator-prefix",
        required=True,
        help="Stabilny prefiks; można użyć {ordinal}, inaczej ordinal zostanie dopisany",
    )
    volume_ordinal = volume_build.add_mutually_exclusive_group()
    volume_ordinal.add_argument(
        "--ordinal-start",
        type=int,
        help="Przypisz kolejne ordinale od tej wartości; domyślnie od 1",
    )
    volume_ordinal.add_argument(
        "--ordinal-regex",
        help="Regex odczytujący ordinal z nazwy obrazu; wieloklatkowe TIFF-y wymagają trybu sekwencyjnego",
    )
    volume_build.add_argument("--recursive", action="store_true", help="Uwzględnij podkatalogi obrazów")
    volume_build.add_argument(
        "--engine",
        choices=["auto", "native", "tesseract", "none"],
        default="auto",
        help="Auto używa najpierw tekstu PDF, a potem lokalnego Tesseract",
    )
    volume_build.add_argument(
        "--language",
        action="append",
        help="Kod lokalnego modelu Tesseract; można powtórzyć (domyślnie eng)",
    )
    volume_build.add_argument("--render-dpi", type=int, default=220)
    volume_build.add_argument("--tesseract-psm", type=int, default=6)
    volume_build.add_argument("--native-minimum-characters", type=int, default=24)
    volume_build.add_argument("--contact-sheet-size", type=int, default=30)
    volume_build.add_argument("--contact-sheet-columns", type=int, default=5)
    volume_build.add_argument("--duplicate-window", type=int, default=40)
    volume_build.add_argument(
        "--incremental-from",
        help="Poprzedni kompletny indeks; zgodne jednostki zostaną użyte ponownie w nowym OUTPUT",
    )
    volume_build.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Maksymalna liczba równoległych jednostek; domyślnie 1",
    )

    volume_resume = sub.add_parser(
        "volume-index-resume",
        help="Wznów przerwany OUTPUT z zachowanego checkpointu jednostek",
    )
    volume_resume.add_argument("output")
    volume_resume.add_argument(
        "--workers",
        type=int,
        help="Nowy limit workerów; bez parametru zachowuje limit zapisany w checkpointcie",
    )

    volume_search = sub.add_parser(
        "volume-index-search",
        help="Wyszukaj techniczne kandydatury z jawną podstawą dopasowania",
    )
    volume_search.add_argument("output", help="Katalog utworzony przez volume-index-build")
    volume_search.add_argument("query")
    volume_search.add_argument("--variant", action="append", help="Jawny wariant nazwy; można powtórzyć")
    volume_search.add_argument("--limit", type=int, default=50)
    volume_search.add_argument("--no-fuzzy", action="store_true")

    volume_report = sub.add_parser(
        "volume-index-report",
        help="Pokaż stan, metryki i pochodzenie lokalnego indeksu",
    )
    volume_report.add_argument("output")

    volume_snippet = sub.add_parser(
        "volume-index-snippet",
        help="Utwórz lokalny wycinek regionu z locatorami i hashami bez zmiany indeksu",
    )
    volume_snippet.add_argument("output", help="Katalog indeksu")
    volume_snippet.add_argument("region_id")
    volume_snippet.add_argument("destination", help="Nowy katalog wynikowy")
    volume_snippet.add_argument("--padding", type=int, default=0, help="Margines w pikselach renderu")

    performance_gate = sub.add_parser(
        "volume-index-performance-gate",
        help="Oceń pomiary kandydata na akcelerator bez deklarowania niezmierzonego przyspieszenia",
    )
    performance_gate.add_argument("--baseline-seconds", type=float)
    performance_gate.add_argument("--candidate-seconds", type=float)
    performance_gate.add_argument("--equivalent-outputs", action="store_true")
    performance_gate.add_argument("--minimum-speedup", type=float, default=1.2)

    volume_qa = sub.add_parser(
        "volume-index-qa",
        help="Wylosuj deterministyczną próbkę stron bez trafień do kontroli false negatives",
    )
    volume_qa.add_argument("output")
    volume_qa.add_argument("query")
    volume_qa.add_argument("--variant", action="append")
    volume_qa.add_argument("--sample-no-hit", type=int, default=30)
    volume_qa.add_argument("--seed", default="genea-volume-index-no-hit-v1")

    sub.add_parser(
        "volume-index-capabilities",
        help="Sprawdź lokalne modele pol/rus/lat i backend Kraken",
    )

    volume_recognize = sub.add_parser(
        "volume-index-recognize",
        help="Dopisz lokalną próbę Tesseract albo Kraken dla wybranej strony",
    )
    volume_recognize.add_argument("output")
    volume_recognize.add_argument("ordinal", type=int)
    volume_recognize.add_argument("--backend", choices=["tesseract", "kraken"], required=True)
    volume_recognize.add_argument("--model", help="Jawna ścieżka modelu Kraken")
    volume_recognize.add_argument("--language", action="append")
    volume_recognize.add_argument("--tesseract-psm", type=int, default=6)
    volume_recognize.add_argument("--derivative-id")

    volume_qwen = sub.add_parser(
        "volume-index-qwen",
        help="Dopisz/wznów pomocnicze odczyty stron przez lokalny Qwen3-VL/Ollama",
    )
    volume_qwen.add_argument("output")
    volume_qwen.add_argument("--ordinal", type=int, action="append")
    volume_qwen.add_argument("--start-ordinal", type=int)
    volume_qwen.add_argument("--end-ordinal", type=int)
    volume_qwen.add_argument("--source-index", help="Bazowy indeks: zweryfikowane kopie renderów do nowego OUTPUT")
    volume_qwen.add_argument("--initialize-only", action="store_true")
    volume_qwen.add_argument("--model", help="Jawny lokalny tag Qwen3-VL (wymagany do generacji)")
    volume_qwen.add_argument("--endpoint", default="http://127.0.0.1:11434")
    volume_qwen.add_argument("--options-json", help="Obiekt JSON opcji generacji Ollama")
    volume_qwen.add_argument("--prompt-file", help="Lokalny UTF-8; pełny prompt trafia do fingerprintu")
    volume_qwen.add_argument("--timeout", type=float, default=600)

    volume_qwen_night = sub.add_parser(
        "volume-index-qwen-night",
        help="Uruchom ręcznie wznawialny nocny przebieg lokalnego Qwen3-VL",
    )
    volume_qwen_night.add_argument("output")
    volume_qwen_night.add_argument("--ordinal", type=int, action="append")
    volume_qwen_night.add_argument("--start-ordinal", type=int)
    volume_qwen_night.add_argument("--end-ordinal", type=int)
    volume_qwen_night.add_argument("--source-index", help="Bazowy indeks renderów; wymagany tylko przy pierwszym uruchomieniu")
    volume_qwen_night.add_argument("--model", required=True, help="Jawny lokalny tag Qwen3-VL")
    volume_qwen_night.add_argument("--endpoint", default="http://127.0.0.1:11434")
    volume_qwen_night.add_argument("--options-json", help="Obiekt JSON bazowych opcji generacji Ollama")
    volume_qwen_night.add_argument("--rescue-options-json", help="Obiekt JSON nadpisujący opcje jednej próby rescue")
    volume_qwen_night.add_argument("--no-rescue", action="store_true")
    volume_qwen_night.add_argument("--prompt-file", help="Lokalny UTF-8; pełny prompt trafia do fingerprintu")
    volume_qwen_night.add_argument("--timeout", type=float, default=600, help="Limit sekund pojedynczego żądania")
    volume_qwen_night.add_argument("--max-hours", type=float, default=10, help="Maksymalny czas przebiegu; domyślnie 10 h")
    volume_qwen_night.add_argument("--max-consecutive-failures", type=int, default=3)
    volume_qwen_night.add_argument("--quiet", action="store_true", help="Nie pokazuj postępu stron na stderr")

    volume_qwen_import = sub.add_parser("volume-index-qwen-import", help="Importuj zachowaną diagnostykę Qwen z kontrolą hashów")
    volume_qwen_import.add_argument("output")
    volume_qwen_import.add_argument("ordinal", type=int)
    volume_qwen_import.add_argument("result_file")
    volume_qwen_import.add_argument("--expected-response-sha256", required=True)
    volume_qwen_import.add_argument("--expected-image-sha256", required=True)
    volume_qwen_import.add_argument("--recovery-metadata-file", required=True)

    volume_import = sub.add_parser(
        "volume-index-attempt-import",
        help="Dopisz zwrócony ALTO/PageXML jako nieprzejrzaną próbę",
    )
    volume_import.add_argument("output")
    volume_import.add_argument("ordinal", type=int)
    volume_import.add_argument("result_file")
    volume_import.add_argument("--backend", required=True)
    volume_import.add_argument("--model")
    volume_import.add_argument("--format", dest="format_type", choices=["alto", "page"], required=True)

    volume_derive = sub.add_parser(
        "volume-index-derive",
        help="Utwórz wersjonowaną pochodną obrazu bez zmiany RAW ani renderu referencyjnego",
    )
    volume_derive.add_argument("output")
    volume_derive.add_argument("ordinal", type=int)
    volume_derive.add_argument(
        "operation",
        choices=["auto-orient", "grayscale", "deskew", "rotate-90", "rotate-180", "split-left", "split-right"],
    )
    volume_derive.add_argument("--deskew-threshold", type=float, default=40.0)

    volume_review = sub.add_parser(
        "volume-index-review",
        help="Dopisz ludzką decyzję review dla regionu",
    )
    volume_review.add_argument("output")
    volume_review.add_argument("region_id")
    volume_review.add_argument("decision", choices=["hit", "false_positive", "illegible", "defer"])
    volume_review.add_argument("--reviewer", required=True)
    volume_review.add_argument("--corrected-text")
    volume_review.add_argument("--note")

    volume_candidate = sub.add_parser(
        "volume-index-writeback-candidate",
        help="Wyeksportuj przejrzany manifest do istniejącej bramki aktu bez zapisu SQLite",
    )
    volume_candidate.add_argument("output")
    volume_candidate.add_argument("region_id")
    volume_candidate.add_argument("destination")

    volume_gt = sub.add_parser(
        "volume-index-ground-truth-export",
        help="Wyeksportuj skorygowane linie human review jako Ground Truth",
    )
    volume_gt.add_argument("output")
    volume_gt.add_argument("destination")

    volume_gt_split = sub.add_parser(
        "volume-index-ground-truth-split",
        help="Podziel Ground Truth deterministycznie bez przecieku stron między zbiorami",
    )
    volume_gt_split.add_argument("ground_truth")
    volume_gt_split.add_argument("--seed", default="genea-ground-truth-v1")
    volume_gt_split.add_argument("--train-ratio", type=float, default=0.8)
    volume_gt_split.add_argument("--validation-ratio", type=float, default=0.1)

    volume_train = sub.add_parser(
        "volume-index-kraken-train",
        help="Sprawdź gotowość korpusu i opcjonalnie uruchom deterministyczny trening Kraken",
    )
    volume_train.add_argument("ground_truth")
    volume_train.add_argument("model_output")
    volume_train.add_argument("--execute", action="store_true")
    volume_train.add_argument("--min-train-lines", type=int, default=100)
    volume_train.add_argument("--epochs", type=int, default=50)

    volume_evaluate = sub.add_parser(
        "volume-index-evaluate",
        help="Zmierz recall kotwic i precision@k na zatwierdzonym złotym korpusie",
    )
    volume_evaluate.add_argument("output")
    volume_evaluate.add_argument("gold_file")
    volume_evaluate.add_argument("--top-k", type=int, default=20)

    volume_external = sub.add_parser(
        "volume-index-external-export",
        help="Utwórz ograniczony pakiet do jawnego uploadu; komenda sama niczego nie wysyła",
    )
    volume_external.add_argument("output")
    volume_external.add_argument("destination")
    volume_external.add_argument("--ordinal", action="append", type=int, required=True)
    volume_external.add_argument("--service", required=True)
    volume_external.add_argument(
        "--classification",
        choices=["public", "historical", "living", "restricted"],
        required=True,
    )
    volume_external.add_argument("--allow-external", action="store_true")
    volume_external.add_argument("--consent-reference", required=True)
    volume_external.add_argument("--max-pages", type=int, default=25)

    range_add = sub.add_parser("browse-range-add", help="Przydziel rozłączny zakres tomu lub zakres kontroli QA")
    range_add.add_argument("session_id")
    range_add.add_argument("start", type=int)
    range_add.add_argument("end", type=int)
    range_add.add_argument("--actor", required=True)
    range_add.add_argument("--assigned-to")
    range_add.add_argument("--kind", default="primary", choices=["primary", "qa_overlap"])
    range_add.add_argument("--start-locator")
    range_add.add_argument("--end-locator")
    range_add.add_argument("--start-page")
    range_add.add_argument("--end-page")
    range_add.add_argument("--start-act")
    range_add.add_argument("--end-act")
    range_add.add_argument("--notes")
    range_claim = sub.add_parser("browse-range-claim", help="Przejmij zakres z kontrolą rewizji i lease tokenem")
    range_claim.add_argument("range_id")
    range_claim.add_argument("--assigned-to", required=True)
    range_claim.add_argument("--actor", required=True)
    range_claim.add_argument("--agent-run-id")
    range_claim.add_argument("--lease-expires-at")
    range_claim.add_argument("--expected-revision", type=int)
    checkpoint = sub.add_parser("browse-checkpoint-add", help="Zapisz niezmienny punkt wznowienia pracy")
    checkpoint.add_argument("range_id")
    checkpoint.add_argument("--actor", required=True)
    checkpoint.add_argument(
        "--status", required=True,
        choices=["progress", "paused", "submitted", "blocked", "handoff", "invalidated"],
    )
    checkpoint.add_argument("--last-ordinal", type=int)
    checkpoint.add_argument("--next-ordinal", type=int)
    checkpoint.add_argument("--last-locator")
    checkpoint.add_argument("--next-locator")
    checkpoint.add_argument("--last-page")
    checkpoint.add_argument("--last-act")
    checkpoint.add_argument("--fingerprint-json")
    checkpoint.add_argument("--term", action="append")
    checkpoint.add_argument("--method")
    checkpoint.add_argument("--source-version")
    checkpoint.add_argument("--manifest-sha256")
    checkpoint.add_argument("--note")
    checkpoint.add_argument("--lease-token")
    range_review = sub.add_parser("browse-range-review", help="Zaakceptuj, częściowo zaakceptuj albo odrzuć zakres")
    range_review.add_argument("range_id")
    range_review.add_argument("decision", choices=["accepted", "accepted_partial", "rejected"])
    range_review.add_argument("--actor", required=True)
    range_review.add_argument("--rationale", required=True)
    range_review.add_argument("--expected-revision", type=int)

    finding_add = sub.add_parser("browse-finding-add", help="Zapisz trafienie lub kontrolę bez tworzenia dowodu")
    finding_add.add_argument("session_id")
    finding_add.add_argument("locator")
    finding_add.add_argument("finding_type", choices=["exact_hit", "candidate", "near_miss", "context", "negative_control", "anomaly"])
    finding_add.add_argument("--created-by", required=True)
    finding_add.add_argument("--range-id")
    finding_add.add_argument("--checkpoint-id")
    finding_add.add_argument("--ordinal", type=int)
    finding_add.add_argument("--page")
    finding_add.add_argument("--act")
    finding_add.add_argument("--raw-text")
    finding_add.add_argument("--normalized-text")
    finding_add.add_argument("--confidence", type=float)
    finding_add.add_argument("--citation-id")
    finding_add.add_argument("--occurrence-id")
    finding_add.add_argument("--assertion-id")
    finding_add.add_argument("--status", default="lead", choices=["lead", "recorded", "review_required", "dismissed"])
    finding_add.add_argument("--note")

    issue_add = sub.add_parser("browse-issue-add", help="Zapisz trudność odczytu i warunki przyszłego powrotu")
    issue_add.add_argument("session_id")
    issue_add.add_argument("locator")
    issue_add.add_argument("issue_field", choices=sorted(ISSUE_FIELDS))
    issue_add.add_argument("issue_kind", choices=sorted(ISSUE_KINDS))
    issue_add.add_argument("--created-by", required=True)
    issue_add.add_argument("--image-quality", required=True, choices=["q0_unusable", "q1_poor", "q2_usable", "q3_good", "q4_excellent"])
    issue_add.add_argument("--reading-confidence", required=True, choices=["c0_none", "c1_guess", "c2_plausible", "c3_high", "c4_secure"])
    issue_add.add_argument("--satisfaction", required=True, choices=["s0_blocked", "s1_provisional", "s2_sufficient", "s3_verified"])
    issue_add.add_argument("--range-id")
    issue_add.add_argument("--checkpoint-id")
    issue_add.add_argument("--finding-id")
    issue_add.add_argument("--handwriting-profile-id")
    issue_add.add_argument("--ordinal", type=int)
    issue_add.add_argument("--page")
    issue_add.add_argument("--act")
    issue_add.add_argument("--primary-cause", choices=sorted(CAUSE_CODES))
    issue_add.add_argument("--additional-cause", action="append", choices=sorted(CAUSE_CODES))
    issue_add.add_argument("--current-reading")
    issue_add.add_argument("--alternative", action="append")
    issue_add.add_argument("--disputed-grapheme", action="append")
    issue_add.add_argument("--severity", default="warning", choices=["info", "warning", "blocker"])
    issue_add.add_argument("--claim-impact", default="none", choices=["none", "low", "medium", "high", "critical"])
    issue_add.add_argument("--recommended-action")
    issue_add.add_argument("--revisit-trigger", action="append", choices=sorted(TRIGGER_TYPES))
    issue_add.add_argument("--revisit-priority", default="r2_medium", choices=["r0_none", "r1_low", "r2_medium", "r3_high", "r4_critical"])
    issue_assess = sub.add_parser("browse-issue-assess", help="Dopisz kolejną próbę odczytu wraz z wersją narzędzia lub modelu")
    issue_assess.add_argument("issue_id")
    issue_assess.add_argument("--actor", required=True)
    issue_assess.add_argument("--method", required=True, choices=["manual", "ocr", "llm", "hybrid", "paleographic_profile", "expert_review"])
    issue_assess.add_argument("--outcome", required=True, choices=["improved", "unchanged", "worse", "resolved", "conflicting"])
    issue_assess.add_argument("--artifact-id")
    issue_assess.add_argument("--handwriting-profile-id")
    issue_assess.add_argument("--tool-name")
    issue_assess.add_argument("--tool-version")
    issue_assess.add_argument("--model-name")
    issue_assess.add_argument("--model-version")
    issue_assess.add_argument("--proposed-reading")
    issue_assess.add_argument("--alternative", action="append")
    issue_assess.add_argument("--confidence", type=float)
    issue_assess.add_argument("--rationale")
    issue_resolve = sub.add_parser("browse-issue-resolve", help="Zapisz oddzielną ludzką decyzję o rozwiązaniu problemu")
    issue_resolve.add_argument("issue_id")
    issue_resolve.add_argument("decision", choices=["resolved", "waived", "reopened"])
    issue_resolve.add_argument("--actor", required=True)
    issue_resolve.add_argument("--rationale", required=True)
    issue_resolve.add_argument("--final-reading")
    issue_resolve.add_argument("--reading-confidence", choices=["c2_plausible", "c3_high", "c4_secure"])
    issue_resolve.add_argument("--satisfaction", choices=["s2_sufficient", "s3_verified"])
    trigger_set = sub.add_parser("browse-trigger-set", help="Oznacz warunek powrotu jako spełniony lub zbędny")
    trigger_set.add_argument("trigger_id")
    trigger_set.add_argument("status", choices=["satisfied", "dismissed"])
    trigger_set.add_argument("--actor", required=True)
    trigger_set.add_argument("--evidence-json")
    trigger_set.add_argument("--rationale")

    quality = sub.add_parser("browse-quality-review", help="Oceń kompletność, systematyczność i jakość bieżącej pracy")
    quality.add_argument("session_id")
    quality.add_argument("--reviewed-by", required=True)
    quality.add_argument("--satisfaction", required=True, choices=["s0_blocked", "s1_provisional", "s2_sufficient", "s3_verified"])
    quality.add_argument("--completeness", required=True, type=float)
    quality.add_argument("--systematicity", required=True, type=float)
    quality.add_argument("--locator-quality", required=True, type=float)
    quality.add_argument("--writeback-quality", required=True, type=float)
    quality.add_argument("--legibility", required=True, type=float)
    quality.add_argument("--qa", required=True, type=float)
    quality.add_argument("--note", required=True)

    hand_add = sub.add_parser("hand-profile-add", help="Załóż profil ręki pisarskiej lub duktu autora akt")
    hand_add.add_argument("source_item_id")
    hand_add.add_argument("label")
    hand_add.add_argument("scope")
    hand_add.add_argument("--created-by", required=True)
    hand_add.add_argument("--script")
    hand_add.add_argument("--language", action="append")
    hand_add.add_argument("--probable-scribe")
    hand_add.add_argument("--attribution-confidence", type=float)
    hand_add.add_argument("--date-from")
    hand_add.add_argument("--date-to")
    hand_add.add_argument("--place-or-office")
    hand_add.add_argument("--features-json")
    hand_add.add_argument("--known-ambiguity", action="append")
    hand_add.add_argument("--analysis-type", action="append")
    hand_source = sub.add_parser(
        "hand-profile-source-set",
        help="Dodaj lub oceń źródło w kontrolowanym zakresie profilu ręki",
    )
    hand_source.add_argument("profile_id")
    hand_source.add_argument("source_item_id")
    hand_source.add_argument("--role", required=True, choices=["anchor", "sample", "context"])
    hand_source.add_argument("--status", required=True, choices=["candidate", "accepted", "excluded"])
    hand_source.add_argument("--linked-by", required=True)
    hand_source.add_argument("--rationale", required=True)
    hand_sample = sub.add_parser("hand-sample-add", help="Dodaj porównawczą próbkę pisma z dokładnym lokalizatorem")
    hand_sample.add_argument("profile_id")
    hand_sample.add_argument("source_item_id")
    hand_sample.add_argument("locator")
    hand_sample.add_argument("sample_type", choices=["grapheme", "word", "name", "surname", "toponym", "number", "formula", "abbreviation", "signature", "other"])
    hand_sample.add_argument("diplomatic_text")
    hand_sample.add_argument("--confidence", required=True, type=float)
    hand_sample.add_argument("--created-by", required=True)
    hand_sample.add_argument("--artifact-id")
    hand_sample.add_argument("--citation-id")
    hand_sample.add_argument("--ordinal", type=int)
    hand_sample.add_argument("--page")
    hand_sample.add_argument("--act")
    hand_sample.add_argument("--region-json")
    hand_sample.add_argument("--normalized-text")
    hand_sample.add_argument("--grapheme", action="append")
    hand_sample.add_argument("--dependent-context", action="store_true")
    hand_sample.add_argument("--notes")
    hand_show = sub.add_parser("hand-profile-show", help="Pokaż gotowość profilu pisma i pokrycie grafemów")
    hand_show.add_argument("profile_id")
    return parser


def _migration_status(root: Path) -> dict:
    database = configured_database_backend(root).path
    if not database.is_file():
        return {"database": str(database), "exists": False, "current": 0, "latest": LATEST_SCHEMA_VERSION}
    with connect(root) as connection:
        current = connection.execute("PRAGMA user_version").fetchone()[0]
        has_history = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone()[0]
        applied = (
            [dict(row) for row in connection.execute("SELECT * FROM schema_migrations ORDER BY version")]
            if has_history
            else []
        )
    return {
        "database": str(database),
        "exists": True,
        "current": current,
        "latest": LATEST_SCHEMA_VERSION,
        "pending": max(0, LATEST_SCHEMA_VERSION - current),
        "applied": applied,
    }


def _run_migration(root: Path, snapshot_dir: str | None) -> dict:
    database = configured_database_backend(root).path
    snapshot = None
    if database.is_file():
        snapshot = create_migration_snapshot(
            root, Path(snapshot_dir).expanduser() if snapshot_dir else None
        )
        if snapshot["integrity_check"] != "ok" or snapshot["foreign_key_issues"]:
            raise RuntimeError(
                "Snapshot został zapisany, ale kontrola bazy nie przeszła; "
                "migracja została przerwana przed zmianą danych."
            )
    with connect(root) as connection:
        applied = apply_migrations(connection)
    return {"snapshot": snapshot, "applied": applied, "status": _migration_status(root)}


def _doctor(root: Path) -> dict:
    checks = {
        "python": sys.version.split()[0],
        "codex": shutil.which("codex"),
        "npx": shutil.which("npx"),
        "pdftoppm": shutil.which("pdftoppm"),
        "pdftotext": shutil.which("pdftotext"),
        "imagemagick": shutil.which("magick"),
        "tesseract": shutil.which("tesseract"),
        "project_sources": str(root / "sources") if (root / "sources").exists() else None,
    }
    try:
        checks["playwright_cli"] = str(playwright_cli())
    except RuntimeError:
        checks["playwright_cli"] = None
    checks["ready_for_visual_analysis"] = bool(checks["codex"])
    checks["ready_for_browser_capture"] = bool(checks["npx"] and checks["playwright_cli"])
    return checks


def _nas_preflight(root: Path) -> dict[str, object]:
    backend = configured_database_backend(root)
    if not backend.encrypted:
        raise RuntimeError("NAS wymaga skonfigurowanego backendu SQLCipher")
    plaintext = research_root(root) / "genea.sqlite3"
    if plaintext.is_file():
        raise RuntimeError("Preflight NAS odrzucony: obok ciphertextu istnieje baza plaintext")
    if not backend.path.is_file():
        raise FileNotFoundError("Nie znaleziono zaszyfrowanej bazy dla wdrożenia NAS")
    integrity = backend.integrity_report()
    if integrity.get("cipher_integrity_check"):
        raise RuntimeError("Preflight NAS odrzucony: cipher_integrity_check zgłosił błędy")
    if integrity.get("integrity_check") != "ok":
        raise RuntimeError("Preflight NAS odrzucony: integrity_check nie jest poprawny")
    if integrity.get("foreign_key_issues"):
        raise RuntimeError("Preflight NAS odrzucony: foreign_key_check zgłosił błędy")
    return {
        "status": "ready-for-nas-readonly",
        "backend": backend.describe()["backend"],
        "driver": backend.driver,
        "encrypted": True,
        "database": str(backend.path),
        "user_version": integrity.get("user_version"),
        "cipher_version": integrity.get("cipher_version"),
        "integrity_check": "ok",
        "foreign_key_issues": [],
        "plaintext_database_present": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    root = project_root(args.root)
    try:
        if args.command == "research-telemetry":
            result, exit_code = research_telemetry.run(args, root)
            _json(result)
            return exit_code
        elif args.command == "research-discovery-check":
            plan = Path(args.plan).expanduser()
            if not plan.is_absolute():
                plan = root / plan
            _json(load_discovery_plan(plan, source_id=args.source_id, lead_id=args.lead_id))
        elif args.command == "init":
            _json({"database": str(initialize(root)), "status": "ready"})
        elif args.command == "doctor":
            _json(_doctor(root))
        elif args.command == "migration-status":
            _json(_migration_status(root))
        elif args.command == "migration-run":
            _json(_run_migration(root, args.snapshot_dir))
        elif args.command == "storage-status":
            backend = configured_database_backend(root)
            result = backend.describe()
            if backend.path.is_file():
                result.update(backend.integrity_report())
                result["status"] = "ready"
            else:
                result["status"] = "missing"
            _json(result)
        elif args.command == "storage-audit":
            _json(audit_workspace(root))
        elif args.command == "storage-prune":
            plan = plan_prune(root, args.target)
            if args.apply:
                _json({
                    "plan": plan,
                    "result": apply_prune(root, plan, args.confirm),
                })
            else:
                _json(plan)
        elif args.command == "research-tmp-finalize":
            plan = plan_tmp_cleanup(root, Path(args.run_dir))
            if args.apply:
                _json({
                    "plan": plan,
                    "result": apply_tmp_cleanup(root, Path(args.run_dir), args.confirm),
                })
            else:
                _json(plan)
        elif args.command == "research-closeout":
            if args.apply:
                if args.output or args.snapshot_store or args.snapshot_id or args.publish:
                    raise ValueError("--apply nie łączy się z opcjami przygotowania")
                _json(apply_closeout(root, args.apply, args.confirm))
            else:
                if not args.snapshot_store or not args.output or args.confirm:
                    raise ValueError("Plan wymaga --snapshot-store i --output; --confirm służy do --apply")
                _json(prepare_closeout(root, args.snapshot_store, args.output,
                                      publish=args.publish, snapshot_id=args.snapshot_id))
        elif args.command == "storage-lfs-preflight":
            result = inspect_lfs_upload(root, upstream=args.upstream)
            _json(result)
            if result["status"] != "ready":
                return 2
        elif args.command == "storage-lfs-archive":
            plan = plan_lfs_archive(root, Path(args.destination))
            if args.apply:
                _json({"plan": plan, "result": apply_lfs_archive(root, plan, args.confirm)})
            else:
                _json(plan)
        elif args.command == "storage-lfs-verify":
            _json(verify_lfs_archive(Path(args.destination)))
        elif args.command == "storage-lfs-restore-drill":
            _json(restore_lfs_archive(Path(args.archive), Path(args.destination)))
        elif args.command.startswith("derived-store-"):
            store = DerivedStore(Path(args.store), workspace_root=root)
            if args.command == "derived-store-put":
                _json(store.put(
                    Path(args.source), retention=args.retention, privacy=args.privacy,
                    lineage=tuple(args.lineage or ()), backing_ids=tuple(args.backing_id or ()),
                    source_ids=tuple(args.source_id or ()),
                ))
            elif args.command == "derived-store-report":
                _json(store.report())
            elif args.command == "derived-store-ref-add":
                _json(store.add_ref(args.name, args.oid))
            elif args.command == "derived-store-ref-remove":
                _json(store.remove_ref(args.name))
            else:
                plan = store.plan_gc()
                _json({"plan": plan, "result": store.apply_gc(plan, args.confirm)}) if args.apply else _json(plan)
        elif args.command == "storage-snapshot":
            if args.keep_last is not None and (args.if_due_hours is None or args.keep_last < 1):
                raise ValueError("--keep-last wymaga --if-due-hours oraz wartości co najmniej 1")
            backend = configured_database_backend(root)
            if args.if_due_hours is None:
                _json(create_storage_snapshot(backend, Path(args.destination), workspace_root=root))
            else:
                _json(snapshot_if_due(
                    backend, Path(args.destination), interval_seconds=args.if_due_hours * 3600,
                    workspace_root=root, keep_last=args.keep_last if args.keep_last is not None else 7,
                ))
        elif args.command == "storage-snapshot-retention":
            plan = plan_snapshot_retention(
                Path(args.destination), keep_last=args.keep_last, workspace_root=root,
            )
            if args.apply:
                result = apply_snapshot_retention(
                    Path(args.destination), args.confirm,
                    keep_last=args.keep_last, workspace_root=root,
                )
                _json({"plan": plan, "result": result})
            else:
                _json(plan)
        elif args.command == "storage-snapshot-verify":
            _json(verify_checkpoint_journal(Path(args.destination)))
        elif args.command == "storage-snapshot-restore-drill":
            _json(restore_storage_snapshot(
                configured_database_backend(root), Path(args.archive), args.snapshot_id,
                Path(args.destination), workspace_root=root,
            ))
        elif args.command == "nas-preflight":
            _json(_nas_preflight(root))
        elif args.command == "private-init":
            ui_password = _prompt_secret("Hasło prywatnego UI", confirm=True)
            recovery_passphrase = _prompt_secret("Hasło recovery", confirm=True)
            _json(
                initialize_private_storage(
                    root,
                    ui_password=ui_password,
                    recovery_passphrase=recovery_passphrase,
                    recovery_output=Path(args.recovery_output),
                )
            )
        elif args.command == "private-status":
            _json(private_status(root))
        elif args.command == "private-audit":
            _json(audit_private_storage(root))
        elif args.command == "private-lock":
            _json(lock_private_ui(root))
        elif args.command == "private-unlock":
            _json(unlock_private_ui(root, _prompt_secret("Hasło prywatnego UI")))
        elif args.command == "private-recover":
            _json(
                recover_private_storage_key(
                    root,
                    recovery_file=Path(args.recovery_file),
                    recovery_passphrase=_prompt_secret("Hasło recovery"),
                )
            )
        elif args.command == "private-migrate-copy":
            output = Path(args.output).expanduser() if args.output else None
            if output is not None and not output.is_absolute():
                output = root / output
            _json(migrate_plaintext_copy(root, destination=output))
        elif args.command == "private-activate":
            _json(
                activate_encrypted_database(
                    root,
                    recovery_file=Path(args.recovery_file),
                    recovery_passphrase=_prompt_secret("Hasło recovery"),
                    plaintext_rollback_destination=Path(args.plaintext_rollback),
                )
            )
        elif args.command == "private-rollback":
            _json(
                rollback_encrypted_database(
                    root,
                    plaintext_rollback_source=Path(args.plaintext_rollback),
                )
            )
        elif args.command == "private-password-change":
            current = _prompt_secret("Bieżące hasło prywatnego UI")
            new_password = _prompt_secret("Nowe hasło prywatnego UI", confirm=True)
            _json(
                change_private_ui_password(
                    root,
                    current_password=current,
                    new_password=new_password,
                )
            )
        elif args.command == "private-key-rotate":
            current_recovery = _prompt_secret("Bieżące hasło recovery")
            new_recovery = _prompt_secret("Nowe hasło recovery", confirm=True)
            _json(
                rotate_private_data_key(
                    root,
                    current_recovery_file=Path(args.current_recovery_file),
                    current_recovery_passphrase=current_recovery,
                    new_recovery_output=Path(args.new_recovery_output),
                    new_recovery_passphrase=new_recovery,
                    encrypted_backup=Path(args.encrypted_backup),
                )
            )
        elif args.command == "private-drive-import":
            payload = _private_json_stdin()
            _json(import_private_drive_workbook(root, payload, apply=args.apply))
        elif args.command == "external-sync-track":
            _json(track_external_object(
                root, args.service, args.object_type, args.object_id,
                status=args.status, remote_object_id=args.remote_object_id,
                remote_url=args.remote_url, notes=args.notes,
            ))
        elif args.command == "external-sync-status":
            _json(sync_status(root, service=args.service, pending_only=args.pending_only))
        elif args.command == "external-sync-mark":
            _json(mark_external_object(
                root, args.service, args.object_type, args.object_id, args.status,
                remote_object_id=args.remote_object_id,
                remote_url=args.remote_url, notes=args.notes,
            ))
        elif args.command == "source-add":
            _json(
                add_source(
                    root,
                    args.location,
                    title=args.title,
                    service=args.service,
                    source_url=args.source_url,
                    rights_note=args.rights_note,
                )
            )
        elif args.command == "source-list":
            _json(list_sources(root))
        elif args.command == "source-show":
            _json(get_source(root, args.source_id))
        elif args.command == "source-register":
            _json(
                register_source_item(
                    root,
                    args.source_id,
                    item_type=args.item_type,
                    citation_text=args.citation_text,
                    date_text=args.date_text,
                    locator=args.locator,
                    source_quality=args.source_quality,
                    actor=args.actor,
                )
            )
        elif args.command == "source-artifact-attach":
            _json(
                attach_source_artifact(
                    root,
                    args.source_id,
                    args.source_item_id,
                    citation_text=args.citation_text,
                    locator=args.locator,
                    actor=args.actor,
                    update_citation=args.update_citation,
                    rehome_existing=args.rehome_existing,
                )
            )
        elif args.command == "archive-provider-list":
            _json(list_archive_providers(root))
        elif args.command == "archive-discover":
            specification = _json_file(args.spec, dict, "specyfikacji kwerendy")
            result = discover_archive(args.provider, specification, root=root)
            if args.output:
                output = Path(args.output).expanduser().resolve()
                dump_json(output, result)
                result = {**result, "output": str(output)}
            _json(result)
        elif args.command == "question-archive-plan":
            with connect(root) as connection:
                result = question_archive_plan(root, connection, args.question_id)
            if args.provider:
                selected = [item for item in result["archiveQueries"] if item["provider"] == args.provider]
                if not selected:
                    raise KeyError(f"Brak propozycji adaptera: {args.provider}")
                result = {**result, "archiveQueries": selected, "selectedProvider": args.provider}
            if args.output:
                if not args.provider:
                    raise ValueError("--output wymaga wskazania --provider")
                proposal = result["archiveQueries"][0]
                if not proposal:
                    raise KeyError(f"Brak propozycji adaptera: {args.provider}")
                if not proposal["executable"]:
                    raise ValueError(f"Kwerenda {args.provider} nie jest jeszcze wykonywalna: {proposal.get('gate')}")
                output = Path(args.output).expanduser().resolve()
                dump_json(output, proposal["specification"])
                result = {**result, "output": str(output)}
            _json(result)
        elif args.command == "archive-manifest":
            result = archive_manifest(args.provider, args.resource_url, root=root)
            if args.output:
                output = Path(args.output).expanduser().resolve()
                dump_json(output, result)
                result = {**result, "output": str(output)}
            _json(result)
        elif args.command == "archive-acquire":
            provider = archive_provider(args.provider, root=root)
            provider.validate_resource_url(args.url)
            if args.source_url is not None:
                validate_source_url(args.source_url)
            _json(add_source(
                root,
                args.url,
                title=args.title,
                service=args.provider,
                source_url=args.source_url,
                rights_note=args.rights_note,
                metadata={"archive_provider": args.provider},
            ))
        elif args.command == "render":
            _json({"source_id": args.source_id, "images": [str(p) for p in visual_files(root, args.source_id)]})
        elif args.command == "browser-open":
            initialize(root)
            open_browser(root, args.url, session=args.session)
            _json({"status": "browser-open", "session": args.session, "url": args.url})
        elif args.command == "browser-capture":
            _json(
                capture_browser(
                    root,
                    source_url=args.source_url,
                    title=args.title,
                    service=args.service,
                    session=args.session,
                    full_page=args.full_page,
                    rights_note=args.rights_note,
                )
            )
        elif args.command == "analyze":
            discovery_plan = Path(args.discovery_plan).expanduser()
            if not discovery_plan.is_absolute():
                discovery_plan = root / discovery_plan
            _json(
                run_analysis(
                    root,
                    args.source_id,
                    model=args.model,
                    researcher_context=args.context,
                    dry_run=args.dry_run,
                    discovery_plan=discovery_plan,
                    lead_id=args.lead_id,
                )
            )
        elif args.command == "analysis-import":
            payload = json.loads(Path(args.path).expanduser().read_text(encoding="utf-8"))
            _json(import_analysis(root, payload, model=args.model))
        elif args.command == "project-index":
            directory = Path(args.directory)
            if not directory.is_absolute():
                directory = root / directory
            _json(index_project(root, directory))
        elif args.command == "cross-check":
            _json(cross_check(root, args.query, limit=args.limit, fuzzy=args.fuzzy))
        elif args.command == "hypothesis-add":
            if args.confidence is not None and not 0 <= args.confidence <= 1:
                raise ValueError("confidence musi mieścić się w zakresie 0–1")
            _json(
                add_hypothesis(
                    root,
                    args.statement,
                    confidence=args.confidence,
                    rationale=args.rationale,
                )
            )
        elif args.command == "hypothesis-link":
            _json(
                link_evidence(
                    root,
                    args.hypothesis_id,
                    args.source_id,
                    stance=args.stance,
                    analysis_id=args.analysis_id,
                    note=args.note,
                )
            )
        elif args.command == "hypothesis-list":
            _json(list_hypotheses(root))
        elif args.command == "frontend-export":
            destination = Path(args.output).expanduser() if args.output else None
            if destination is not None and not destination.is_absolute():
                destination = root / destination
            _json(export_frontend(root, destination))
        elif args.command == "frontend-serve":
            prebuilt_assets = (
                Path(args.prebuilt_assets).expanduser().resolve()
                if args.prebuilt_assets
                else None
            )
            serve_frontend(
                root,
                port=args.port,
                open_browser=not args.no_open,
                host=args.host,
                prebuilt_assets=prebuilt_assets,
                private_api=args.private_api,
                trusted_private_proxy=args.trusted_private_proxy,
                trusted_proxy_cidrs=tuple(args.trusted_proxy_cidr),
            )
        elif args.command == "case-add":
            _json(create_research_case(root, args.code, args.title, summary=args.summary))
        elif args.command == "case-show":
            _json(get_research_case(root, args.case_id))
        elif args.command == "archive-requests-import":
            packet = json.loads(Path(args.path).expanduser().read_text(encoding="utf-8"))
            if not isinstance(packet, dict) or set(packet) != {"requests"}:
                raise ValueError("Plik musi zawierać obiekt requests z listą projektów")
            _json(import_archive_requests(root, packet["requests"]))
        elif args.command == "archive-requests-list":
            _json(list_archive_requests(root))
        elif args.command == "research-scope-add":
            _json(
                create_research_scope(
                    root,
                    args.scope_id,
                    args.scope_type,
                    args.title,
                    summary=args.summary,
                    status=args.status,
                    legacy_case_id=args.legacy_case_id,
                )
            )
        elif args.command == "research-scope-link":
            _json(
                link_research_scopes(
                    root,
                    args.parent_scope_id,
                    args.child_scope_id,
                    relation_type=args.relation,
                    is_primary=args.primary,
                    sort_order=args.sort_order,
                    notes=args.notes,
                )
            )
        elif args.command == "research-scope-target-add":
            _json(
                link_research_scope_target(
                    root,
                    args.scope_id,
                    args.target_type,
                    args.target_id,
                    args.target_label,
                    association_role=args.role,
                    semantics=args.semantics,
                    date_from=args.date_from,
                    date_to=args.date_to,
                    notes=args.notes,
                )
            )
        elif args.command == "question-scope-add":
            _json(
                link_question_scope(
                    root,
                    args.question_id,
                    args.scope_id,
                    role=args.role,
                    is_primary=args.primary,
                    notes=args.notes,
                )
            )
        elif args.command == "research-scope-tree":
            _json(research_scope_tree(root))
        elif args.command == "research-scope-import":
            manifest_path = Path(args.manifest).expanduser()
            if not manifest_path.is_absolute():
                manifest_path = root / manifest_path
            helper = (
                apply_research_scope_manifest
                if args.apply
                else preview_research_scope_manifest
            )
            _json(helper(root, manifest_path.resolve()))
        elif args.command == "research-work-item-add":
            _json(
                create_research_work_item(
                    root,
                    args.work_item_id,
                    args.item_type,
                    args.title,
                    scope_id=args.scope_id,
                    question_id=args.question_id,
                    parent_work_item_id=args.parent_work_item_id,
                    completion_criterion=args.completion_criterion,
                    status=args.status,
                    priority=args.priority,
                )
            )
        elif args.command == "question-add":
            _json(
                add_research_question(
                    root,
                    args.case_id,
                    args.question,
                    args.completion_criterion,
                    priority=args.priority,
                )
            )
        elif args.command == "question-priority-set":
            _json(set_research_question_priority(root, args.question_id, args.priority))
        elif args.command == "question-target-add":
            _json(
                link_research_question_target(
                    root,
                    args.question_id,
                    args.target_type,
                    args.target_id,
                    args.target_label,
                    is_primary=args.primary,
                    notes=args.notes,
                )
            )
        elif args.command == "question-list":
            _json(
                list_research_questions(
                    root,
                    case_id=args.case_id,
                    status=args.status,
                    target_type=args.target_type,
                    target_id=args.target_id,
                    scope_id=args.scope_id,
                )
            )
        elif args.command == "question-generate":
            _json(
                generate_question_backlog(
                    root,
                    case_id=args.case_id,
                    rules_path=args.rules_path,
                    scope_path=args.scope_path,
                )
            )
        elif args.command == "question-candidate-refresh":
            _json(
                refresh_question_candidates(
                    root,
                    case_id=args.case_id,
                    actor=args.actor,
                )
            )
        elif args.command == "question-candidate-preview":
            _json(question_candidate_preview(root, args.candidate_id))
        elif args.command == "question-candidate-apply":
            _json(
                apply_question_candidate_decision(
                    root,
                    args.candidate_id,
                    decision=args.decision,
                    expected_fingerprint=args.expected_fingerprint,
                    command_id=args.command_id,
                    actor=args.actor,
                    rationale=args.rationale,
                    target_question_id=args.target_question_id,
                    target_candidate_id=args.target_candidate_id,
                )
            )
        elif args.command == "community-research-plan":
            _json(plan_community_research(json.loads(Path(args.input_json).read_text(encoding="utf-8"))))
        elif args.command == "community-research-prepare-log":
            _json(prepare_community_research_log(json.loads(Path(args.input_json).read_text(encoding="utf-8"))))
        elif args.command == "research-log-add":
            _json(
                add_research_log_entry(
                    root,
                    args.case_id,
                    repository=args.repository,
                    query_text=args.query,
                    result_type=args.result_type,
                    result_summary=(
                        Path(args.result_summary_file).read_text(encoding="utf-8")
                        if args.result_summary_file else args.result_summary
                    ),
                    question_id=args.question_id,
                    collection_name=args.collection,
                    date_from=args.date_from,
                    date_to=args.date_to,
                    scope_text=args.scope,
                    searched_at=(
                        validate_search_timestamp(args.searched_at)
                        if args.searched_at is not None else None
                    ),
                )
            )
        elif args.command == "search-coverage-add":
            _json(
                record_search_coverage(
                    root,
                    args.log_entry_id,
                    appropriate=args.appropriate,
                    complete=args.complete,
                    systematic=args.systematic,
                    assessment_note=args.note,
                )
            )
        elif args.command == "negative-evidence":
            _json(negative_evidence_assessment(root, args.case_id))
        elif args.command == "next-action-set":
            _json(
                set_next_action(
                    root,
                    args.case_id,
                    args.action,
                    question_id=args.question_id,
                    work_item_id=args.work_item_id,
                    priority=args.priority,
                )
            )
        elif args.command == "fan-report":
            _json(
                fan_report(
                    root,
                    person_id=args.person_id,
                    occurrence_id=args.occurrence_id,
                    include_incidental=not args.exclude_incidental,
                )
            )
        elif args.command == "life-profile":
            _json(life_profile(root, args.person_id, expected_event_types=args.expected_event))
        elif args.command == "place-add":
            _json(ensure_place(root, args.name, place_type=args.type, notes=args.notes))
        elif args.command == "place-study-add":
            place = ensure_place(root, args.place_name, place_type="locality")
            _json(
                create_place_study(
                    root,
                    place["id"],
                    args.title,
                    case_id=args.case_id,
                    date_from=args.date_from,
                    date_to=args.date_to,
                    scope=args.scope,
                )
            )
        elif args.command == "place-coverage-add":
            _json(
                record_place_study_coverage(
                    root,
                    args.study_id,
                    args.record_type,
                    args.coverage_status,
                    year_from=args.year_from,
                    year_to=args.year_to,
                    completeness=args.completeness,
                    notes=args.notes,
                )
            )
        elif args.command == "place-coverage-show":
            _json(place_study_coverage_matrix(root, args.study_id))
        elif args.command == "place-occurrences-import":
            _json(
                import_household_occurrences(
                    root, Path(args.path).expanduser() if args.path else None, case_id=args.case_id
                )
            )
        elif args.command == "place-search":
            _json(
                search_local_occurrences(
                    root,
                    args.query,
                    study_id=args.study_id,
                    household_id=args.household_id,
                )
            )
        elif args.command == "household-show":
            _json(household_timeline(root, args.household_id))
        elif args.command == "reading-compare":
            differences_path = Path(args.differences_json).expanduser()
            differences = json.loads(differences_path.read_text(encoding="utf-8"))
            if not isinstance(differences, list):
                raise ValueError("Plik differences-json musi zawierać tablicę JSON")
            _json(
                record_reading_comparison(
                    root,
                    args.source_item_id,
                    args.blind_version_id,
                    args.contextual_version_id,
                    differences,
                    reviewer_version_id=args.reviewer_version_id,
                    status=args.status,
                )
            )
        elif args.command == "reading-review-record":
            _json(
                record_reading_review_decision(
                    root,
                    args.source_item_id,
                    args.status,
                    _json_file(args.inputs_json, list, "inputs-json"),
                    decided_by=args.decided_by,
                    decided_by_kind=args.decided_by_kind,
                    decided_by_run_id=args.decided_by_run_id,
                    rationale=args.rationale,
                    decision=_json_file(args.decision_json, dict, "decision-json")
                    if args.decision_json
                    else None,
                    expected_revision=args.expected_revision,
                )
            )
        elif args.command == "reading-review-show":
            _json(get_reading_review_decision(root, args.decision_id))
        elif args.command == "reading-review-list":
            _json(
                list_reading_review_decisions(
                    root,
                    source_item_id=args.source_item_id,
                    status=args.status,
                    latest_only=args.latest_only,
                    limit=args.limit,
                )
            )
        elif args.command == "index-debt-report":
            _json(
                index_debt_report(
                    root,
                    source_item_id=args.source_item_id,
                    include_items=args.include_items,
                    include_restricted=args.include_restricted,
                    limit=args.limit,
                )
            )
        elif args.command == "agent-review-manifest":
            _json(
                agent_review_manifest(
                    root,
                    source_item_ids=args.source_item_id,
                    include_reviewed=args.include_reviewed,
                    include_restricted=args.include_restricted,
                    limit=args.limit,
                )
            )
        elif args.command == "reassessment-show":
            _json(reassessment_show(root, args.batch_id))
        elif args.command == "reassessment-impact":
            _json(reassessment_impact_report(root, args.batch_id))
        elif args.command == "reassessment-writeback":
            manifest = Path(args.manifest).expanduser()
            _json(
                apply_reassessment_writeback(
                    root,
                    manifest,
                    expected_fingerprint=args.expected_fingerprint,
                )
                if args.apply
                else reassessment_writeback_preview(root, manifest)
            )
        elif args.command == "privacy-label":
            _json(
                label_privacy(
                    root,
                    args.object_type,
                    args.object_id,
                    args.classification,
                    args.rationale,
                )
            )
        elif args.command == "claim-review":
            _json(
                review_claim_status(
                    root,
                    args.claim_id,
                    args.target_status,
                    reviewer=args.reviewer,
                    rationale=args.rationale,
                )
            )
        elif args.command == "identity-candidate-add":
            _json(
                add_identity_candidate(
                    root,
                    args.occurrence_a_id,
                    args.occurrence_b_id,
                    score=args.score,
                    basis=args.basis,
                    actor=args.actor,
                    support=args.support,
                    against=args.against,
                    human_review_debt=args.human_review_debt,
                    bundle_id=args.bundle_id,
                )
            )
        elif args.command == "identity-only-external-shell-merge":
            manifest = Path(args.manifest).expanduser()
            if not manifest.is_absolute():
                manifest = root / manifest
            if args.apply and not all((args.expected_fingerprint, args.command_id)):
                parser.error("--apply wymaga --expected-fingerprint i --command-id")
            if args.refresh_presentation and not args.apply:
                parser.error("--refresh-presentation wymaga --apply")
            if args.apply:
                result = apply_identity_only_external_shell_merge(
                    root,
                    manifest.resolve(),
                    expected_fingerprint=args.expected_fingerprint,
                    command_id=args.command_id,
                )
                if args.refresh_presentation:
                    try:
                        projection = export_frontend(root)
                    except Exception as error:
                        set_projection_status(
                            root,
                            args.command_id,
                            status="failed",
                            error=str(error),
                        )
                        result = {
                            **result,
                            "projectionStatus": "failed",
                            "projectionError": str(error),
                        }
                    else:
                        set_projection_status(
                            root,
                            args.command_id,
                            status="refreshed",
                        )
                        result = {
                            **result,
                            "projectionStatus": "refreshed",
                            "presentation": projection,
                        }
            else:
                result = identity_only_external_shell_merge_preview(
                    root, manifest.resolve()
                )
            _json(result)
        elif args.command == "identity-only-external-candidate-disposition":
            manifest = Path(args.manifest).expanduser()
            if not manifest.is_absolute():
                manifest = root / manifest
            if args.apply and not all((args.expected_fingerprint, args.command_id)):
                parser.error("--apply wymaga --expected-fingerprint i --command-id")
            _json(
                apply_identity_only_external_candidate_disposition(
                    root,
                    manifest.resolve(),
                    expected_fingerprint=args.expected_fingerprint,
                    command_id=args.command_id,
                )
                if args.apply
                else identity_only_external_candidate_disposition_preview(
                    root, manifest.resolve()
                )
            )
        elif args.command == "identity-only-agent-ratification":
            manifest = Path(args.manifest).expanduser()
            if not manifest.is_absolute():
                manifest = root / manifest
            if args.apply and not all((args.expected_fingerprint, args.command_id)):
                parser.error("--apply wymaga --expected-fingerprint i --command-id")
            _json(
                apply_identity_only_agent_ratification(
                    root,
                    manifest.resolve(),
                    expected_fingerprint=args.expected_fingerprint,
                    command_id=args.command_id,
                )
                if args.apply
                else identity_only_agent_ratification_preview(
                    root, manifest.resolve()
                )
            )
        elif args.command == "identity-only-human-confirmation":
            manifest = Path(args.manifest).expanduser()
            if not manifest.is_absolute():
                manifest = root / manifest
            if args.apply and not all((args.expected_fingerprint, args.command_id)):
                parser.error("--apply wymaga --expected-fingerprint i --command-id")
            _json(
                apply_identity_only_human_confirmation(
                    root,
                    manifest.resolve(),
                    expected_fingerprint=args.expected_fingerprint,
                    command_id=args.command_id,
                )
                if args.apply
                else identity_only_human_confirmation_preview(
                    root, manifest.resolve()
                )
            )
        elif args.command == "identity-source-group-capture":
            from .identity_source_group_curation import capture_source_identity_group_before_state
            with configured_database_backend(root).connect(readonly=True) as connection:
                _json(capture_source_identity_group_before_state(root, connection, args.group_id))
        elif args.command == "identity-source-group":
            from .identity_source_group_curation import (
                apply_identity_source_group,
                identity_source_group_preview,
            )
            manifest = Path(args.manifest).expanduser()
            if not manifest.is_absolute():
                manifest = root / manifest
            if args.apply and not all((args.expected_fingerprint, args.command_id)):
                parser.error("--apply wymaga --expected-fingerprint i --command-id")
            _json(
                apply_identity_source_group(
                    root, manifest.resolve(),
                    expected_fingerprint=args.expected_fingerprint,
                    command_id=args.command_id,
                )
                if args.apply else identity_source_group_preview(root, manifest.resolve())
            )
        elif args.command == "identity-dependency-capture":
            with configured_database_backend(root).connect(readonly=True) as connection:
                _json(capture_identity_dependency_before_state(root, connection, args.prior_command_id))
        elif args.command in {
            "identity-source-profile-resolution",
            "identity-source-profile-agent-ratification",
            "identity-source-pair-resolution",
            "identity-source-pair-agent-ratification",
            "identity-source-only-resolution",
            "identity-source-only-agent-ratification",
            "identity-dependency-revalidation",
        }:
            manifest = Path(args.manifest).expanduser()
            if not manifest.is_absolute():
                manifest = root / manifest
            if args.apply and not all((args.expected_fingerprint, args.command_id)):
                parser.error("--apply wymaga --expected-fingerprint i --command-id")
            preview, apply = {
                "identity-source-profile-resolution": (
                    identity_source_profile_resolution_preview, apply_identity_source_profile_resolution),
                "identity-source-profile-agent-ratification": (
                    identity_source_profile_agent_ratification_preview, apply_identity_source_profile_agent_ratification),
                "identity-source-pair-resolution": (
                    identity_source_pair_resolution_preview, apply_identity_source_pair_resolution),
                "identity-source-pair-agent-ratification": (
                    identity_source_pair_agent_ratification_preview, apply_identity_source_pair_agent_ratification),
                "identity-source-only-resolution": (
                    identity_source_only_resolution_preview, apply_identity_source_only_resolution),
                "identity-source-only-agent-ratification": (
                    identity_source_only_agent_ratification_preview, apply_identity_source_only_agent_ratification),
                "identity-dependency-revalidation": (
                    identity_dependency_revalidation_preview, apply_identity_dependency_revalidation),
            }[args.command]
            _json(
                apply(root, manifest.resolve(),
                      expected_fingerprint=args.expected_fingerprint,
                      command_id=args.command_id)
                if args.apply else preview(root, manifest.resolve())
            )
        elif args.command == "identity-only-local-candidate-resolution":
            manifest = Path(args.manifest).expanduser()
            if not manifest.is_absolute():
                manifest = root / manifest
            if args.apply and not all((args.expected_fingerprint, args.command_id)):
                parser.error("--apply wymaga --expected-fingerprint i --command-id")
            _json(
                apply_identity_only_local_candidate_resolution(
                    root,
                    manifest.resolve(),
                    expected_fingerprint=args.expected_fingerprint,
                    command_id=args.command_id,
                )
                if args.apply
                else identity_only_local_candidate_resolution_preview(
                    root, manifest.resolve()
                )
            )
        elif args.command == "identity-only-local-human-candidate-resolution":
            manifest = Path(args.manifest).expanduser()
            if not manifest.is_absolute():
                manifest = root / manifest
            if args.apply and not all((args.expected_fingerprint, args.command_id)):
                parser.error("--apply wymaga --expected-fingerprint i --command-id")
            _json(
                apply_identity_only_local_human_candidate_resolution(
                    root,
                    manifest.resolve(),
                    expected_fingerprint=args.expected_fingerprint,
                    command_id=args.command_id,
                )
                if args.apply
                else identity_only_local_human_candidate_resolution_preview(
                    root, manifest.resolve()
                )
            )
        elif args.command == "identity-only-local-agent-ratification":
            manifest = Path(args.manifest).expanduser()
            if not manifest.is_absolute():
                manifest = root / manifest
            if args.apply and not all((args.expected_fingerprint, args.command_id)):
                parser.error("--apply wymaga --expected-fingerprint i --command-id")
            _json(
                apply_identity_only_local_agent_ratification(
                    root,
                    manifest.resolve(),
                    expected_fingerprint=args.expected_fingerprint,
                    command_id=args.command_id,
                )
                if args.apply
                else identity_only_local_agent_ratification_preview(
                    root, manifest.resolve()
                )
            )
        elif args.command == "family-writeback":
            manifest = Path(args.manifest).expanduser()
            _json(
                apply_family_writeback(
                    root,
                    manifest,
                    expected_fingerprint=args.expected_fingerprint,
                    command_id=args.command_id,
                )
                if args.apply
                else family_writeback_preview(root, manifest)
            )
        elif args.command == "event-record-candidate-detect":
            _json(refresh_event_record_candidates(root, actor=args.actor))
        elif args.command == "event-record-candidate-list":
            _json(list_event_record_candidates(root, status=args.status))
        elif args.command == "event-record-candidate-preview":
            _json(event_record_candidate_preview(root, args.candidate_id))
        elif args.command == "event-record-candidate-apply":
            _json(
                apply_event_record_decision(
                    root,
                    args.candidate_id,
                    decision=args.decision,
                    expected_fingerprint=args.expected_fingerprint,
                    actor=args.actor,
                    rationale=args.rationale,
                    command_id=args.command_id,
                )
            )
        elif args.command == "curation-queue-preview":
            _json(curation_queue_preview(root, args.queue_id))
        elif args.command == "curation-queue-apply":
            _json(
                apply_curation_queue_decision(
                    root,
                    args.queue_id,
                    decision=args.decision,
                    expected_fingerprint=args.expected_fingerprint,
                    actor=args.actor,
                    rationale=args.rationale,
                    command_id=args.command_id,
                )
            )
        elif args.command == "mapping-input-fingerprint":
            _json(
                {
                    "sourceItemId": args.source_item_id,
                    "inputFingerprint": source_input_fingerprint(root, args.source_item_id),
                }
            )
        elif args.command == "mapping-writeback":
            manifest = Path(args.manifest).expanduser()
            mapping_result = (
                apply_mapping_writeback(root, manifest)
                if args.apply
                else mapping_writeback_preview(root, manifest)
            )
            if args.apply and mapping_result.get("sourceItemId"):
                mapping_result = {
                    **mapping_result,
                    "requiredFollowUp": source_relative_materialization_follow_up(
                        root, mapping_result["sourceItemId"]
                    ),
                }
            _json(mapping_result)
        elif args.command == "source-relative-materialize":
            if args.apply and not all(
                (args.expected_fingerprint, args.command_id, args.actor, args.rationale)
            ):
                parser.error(
                    "--apply wymaga --expected-fingerprint, --command-id, --actor i --rationale"
                )
            _json(
                apply_source_relative_materialization(
                    root,
                    args.source_item_id,
                    expected_fingerprint=args.expected_fingerprint,
                    command_id=args.command_id,
                    actor=args.actor,
                    rationale=args.rationale,
                )
                if args.apply
                else source_relative_materialization_preview(root, args.source_item_id)
            )
        elif args.command == "place-writeback":
            manifest = Path(args.manifest).expanduser()
            if not manifest.is_absolute():
                manifest = root / manifest
            _json(
                apply_place_writeback(
                    root,
                    manifest.resolve(),
                    expected_fingerprint=args.expected_fingerprint,
                    command_id=args.command_id,
                )
                if args.apply
                else place_writeback_preview(root, manifest.resolve())
            )
        elif args.command == "external-tree-leads-import":
            manifest = Path(args.manifest).expanduser()
            if not manifest.is_absolute():
                manifest = root / manifest
            if args.apply and not args.expected_fingerprint:
                parser.error("--apply wymaga --expected-fingerprint")
            if args.refresh_presentation and not args.apply:
                parser.error("--refresh-presentation wymaga --apply")
            if args.apply:
                result = apply_external_tree_leads(
                    root,
                    manifest.resolve(),
                    expected_fingerprint=args.expected_fingerprint,
                )
                if args.refresh_presentation:
                    try:
                        projection = export_frontend(root)
                    except Exception as error:
                        set_projection_status(
                            root,
                            result["commandId"],
                            status="failed",
                            error=str(error),
                        )
                        result = {
                            **result,
                            "projectionStatus": "failed",
                            "projectionRefreshState": "failed",
                            "projectionError": str(error),
                        }
                    else:
                        set_projection_status(
                            root,
                            result["commandId"],
                            status="refreshed",
                        )
                        result = {
                            **result,
                            "projectionStatus": "refreshed",
                            "projectionRefreshState": "refreshed",
                            "presentation": projection,
                        }
            else:
                result = external_tree_leads_preview(root, manifest.resolve())
            _json(result)
        elif args.command == "reading-pipeline":
            manifest = Path(args.manifest).expanduser()
            if not manifest.is_absolute():
                manifest = root / manifest
            if args.apply and not all(
                (args.expected_fingerprint, args.actor, args.rationale)
            ):
                parser.error(
                    "--apply wymaga --expected-fingerprint, --actor i --rationale"
                )
            _json(
                apply_reading_pipeline(
                    root,
                    manifest.resolve(),
                    expected_fingerprint=args.expected_fingerprint,
                    actor=args.actor,
                    rationale=args.rationale,
                )
                if args.apply
                else reading_pipeline_preview(root, manifest.resolve())
            )
        elif args.command == "reading-pipeline-benchmark":
            candidate = Path(args.candidate_manifest).expanduser()
            gold = Path(args.gold_manifest).expanduser()
            if not candidate.is_absolute():
                candidate = root / candidate
            if not gold.is_absolute():
                gold = root / gold
            _json(benchmark_reading_pipeline(candidate.resolve(), gold.resolve()))
        elif args.command == "reading-verification-export":
            package = Path(args.package).expanduser()
            audit = Path(args.audit).expanduser()
            if not package.is_absolute():
                package = root / package
            if not audit.is_absolute():
                audit = root / audit
            _json(
                export_reading_verification_run(
                    root,
                    package.absolute(),
                    audit.absolute(),
                    reader_id=args.reader_id,
                )
            )
        elif args.command == "reading-verification-compose":
            manifest = Path(args.manifest).expanduser()
            if not manifest.is_absolute():
                manifest = root / manifest
            proposals = []
            for raw_path in args.proposals:
                proposal = Path(raw_path).expanduser()
                if not proposal.is_absolute():
                    proposal = root / proposal
                proposals.append(proposal.resolve())
            _json(
                compose_reading_verification_proposal(
                    manifest.resolve(), proposals
                )
            )
        elif args.command == "reading-snippet-build":
            request = Path(args.request).expanduser()
            output = Path(args.output).expanduser()
            if not request.is_absolute():
                request = root / request
            if not output.is_absolute():
                output = root / output
            _json(
                build_reading_snippet_package(
                    root,
                    request.resolve(),
                    output.absolute(),
                )
            )
        elif args.command == "reading-snippet-build-from-pipeline":
            manifest = Path(args.manifest).expanduser()
            source = Path(args.source).expanduser()
            output = Path(args.output).expanduser()
            if not manifest.is_absolute():
                manifest = root / manifest
            if not source.is_absolute():
                source = root / source
            if not output.is_absolute():
                output = root / output
            _json(
                build_reading_snippet_package_from_pipeline(
                    root,
                    manifest.resolve(),
                    source.resolve(),
                    output.absolute(),
                    source_locator=args.locator,
                    privacy_classification=args.privacy_classification,
                )
            )
        elif args.command == "reading-snippet-verify":
            output = Path(args.output).expanduser()
            if not output.is_absolute():
                output = root / output
            _json(verify_reading_snippet_package(root, output.absolute()))
        elif args.command == "reading-snippet-serve":
            output = Path(args.output).expanduser()
            if not output.is_absolute():
                output = root / output
            prebuilt_assets = (
                Path(args.prebuilt_assets).expanduser().absolute()
                if args.prebuilt_assets
                else None
            )
            audit_output = (
                Path(args.audit_output).expanduser()
                if args.audit_output
                else None
            )
            if audit_output is not None and not audit_output.is_absolute():
                audit_output = root / audit_output
            serve_reading_verification(
                root,
                output.absolute(),
                port=args.port,
                open_browser=not args.no_open,
                prebuilt_assets=prebuilt_assets,
                run_id=args.run_id,
                audit_output=audit_output,
            )
        elif args.command == "source-structure-writeback":
            manifest = Path(args.manifest).expanduser()
            _json(
                apply_source_structure(root, manifest)
                if args.apply
                else source_structure_preview(root, manifest)
            )
        elif args.command == "source-coverage-report":
            _json(source_coverage_report(root, args.source_item_id))
        elif args.command == "browse-session-add":
            _json(create_browse_session(
                root, args.case_id, args.source_item_id, args.title,
                created_by=args.created_by, unit_type=args.unit_type,
                ordinal_kind=args.ordinal_kind, arrangement=args.arrangement,
                method=args.method, scope_text=args.scope, search_terms=args.term,
                question_id=args.question_id, place_study_id=args.place_study_id,
                artifact_id=args.artifact_id, canonical_url=args.canonical_url,
                source_version=args.source_version, manifest_sha256=args.manifest_sha256,
                expected_start_ordinal=args.expected_start,
                expected_end_ordinal=args.expected_end,
                privacy_classification=args.privacy,
            ))
        elif args.command == "browse-session-list":
            _json(list_browse_sessions(
                root, case_id=args.case_id, source_item_id=args.source_item_id,
                status=args.status,
            ))
        elif args.command == "browse-session-show":
            _json(browse_session_report(root, args.session_id))
        elif args.command == "browse-session-export":
            _json(export_browse_session_report(root, args.session_id))
        elif args.command == "browse-session-close":
            _json(close_browse_session(
                root, args.session_id, args.status,
                actor=args.actor, rationale=args.rationale,
            ))
        elif args.command == "browse-session-reopen":
            _json(reopen_browse_session(
                root, args.session_id, actor=args.actor, rationale=args.rationale,
            ))
        elif args.command == "browse-inventory-import":
            _json(record_browse_inventory_units(
                root, args.session_id,
                _json_file(args.path, list, "manifest jednostek"),
                actor=args.actor,
            ))
        elif args.command == "browse-rendition-add":
            _json(add_browse_rendition(
                root, args.session_id, args.locator,
                rendition_class=args.rendition_class,
                acquisition_method=args.acquisition_method,
                access_status=args.access_status,
                information_quality=args.quality,
                created_by=args.created_by,
                ordinal=args.ordinal,
                natural_width_px=args.width,
                natural_height_px=args.height,
                byte_size=args.bytes,
                media_type=args.media_type,
                content_sha256=args.sha256,
                is_upscaled={"yes": True, "no": False, "unknown": None}[args.upscaled],
                legal_access_checked=args.legal_access_checked,
                notes=args.notes,
            ))
        elif args.command == "browse-rendition-review":
            _json(review_browse_rendition_gate(
                root, args.session_id, args.status,
                reviewed_by=args.reviewed_by,
                rationale=args.rationale,
                selected_rendition_id=args.selected_rendition_id,
                representative_ordinal=args.representative_ordinal,
                comparison=_json_file(args.comparison_json, dict, "comparison-json"),
            ))
        elif args.command == "microfilm-preflight":
            _json(preflight_microfilm(
                Path(args.input), Path(args.output),
                project_root=root,
                locator_prefix=args.locator_prefix,
                ordinal_start=args.ordinal_start,
                ordinal_regex=args.ordinal_regex,
                recursive=args.recursive,
                contact_sheet_size=args.contact_sheet_size,
                contact_sheet_columns=args.contact_sheet_columns,
                duplicate_window=args.duplicate_window,
                ocr_ordinals=args.ocr_ordinal,
                ocr_language=args.ocr_language,
            ))
        elif args.command == "microfilm-inventory-validate":
            _json(validate_microfilm_inventory(
                Path(args.path),
                expected_start=args.expected_start,
                expected_end=args.expected_end,
            ))
        elif args.command == "volume-index-ocrmypdf":
            _json(derive_pdf_with_ocrmypdf(
                Path(args.input),
                Path(args.output),
                project_root=root,
                languages=args.language or ("pol",),
                rotate_pages=args.rotate_pages,
                deskew=args.deskew,
                sidecar=args.sidecar,
                locator_prefix=args.locator_prefix,
                source_id=args.source_id,
                source_url=args.source_url,
            ))
        elif args.command == "volume-index-build":
            _json(build_volume_index(
                Path(args.input),
                Path(args.output),
                project_root=root,
                locator_prefix=args.locator_prefix,
                ordinal_start=args.ordinal_start,
                ordinal_regex=args.ordinal_regex,
                recursive=args.recursive,
                engine=args.engine,
                languages=args.language,
                render_dpi=args.render_dpi,
                tesseract_psm=args.tesseract_psm,
                native_minimum_characters=args.native_minimum_characters,
                contact_sheet_size=args.contact_sheet_size,
                contact_sheet_columns=args.contact_sheet_columns,
                duplicate_window=args.duplicate_window,
                incremental_from=Path(args.incremental_from) if args.incremental_from else None,
                worker_limit=args.workers,
            ))
        elif args.command == "volume-index-search":
            _json(search_volume_index(
                Path(args.output),
                args.query,
                variants=args.variant,
                limit=args.limit,
                fuzzy=not args.no_fuzzy,
            ))
        elif args.command == "volume-index-resume":
            _json(resume_volume_index(
                Path(args.output), project_root=root, worker_limit=args.workers,
            ))
        elif args.command == "volume-index-report":
            _json(report_volume_index(Path(args.output)))
        elif args.command == "volume-index-snippet":
            _json(create_volume_region_snippet(
                Path(args.output), args.region_id, Path(args.destination),
                project_root=root, padding=args.padding,
            ))
        elif args.command == "volume-index-performance-gate":
            result = evaluate_performance_gate(
                args.baseline_seconds, args.candidate_seconds,
                equivalent_outputs=args.equivalent_outputs,
                minimum_speedup=args.minimum_speedup,
            )
            _json(result)
            if result["status"] != "passed":
                return 2
        elif args.command == "volume-index-qa":
            _json(qa_volume_index(
                Path(args.output),
                args.query,
                variants=args.variant,
                sample_no_hit=args.sample_no_hit,
                seed=args.seed,
            ))
        elif args.command == "volume-index-capabilities":
            _json(volume_index_capabilities())
        elif args.command == "volume-index-recognize":
            _json(recognize_volume_index(
                Path(args.output),
                args.ordinal,
                backend=args.backend,
                model=Path(args.model) if args.model else None,
                languages=args.language,
                psm=args.tesseract_psm,
                derivative_id=args.derivative_id,
            ))
        elif args.command == "volume-index-qwen":
            ordinals = args.ordinal
            if args.start_ordinal is not None or args.end_ordinal is not None:
                if (ordinals or args.start_ordinal is None or args.end_ordinal is None
                        or args.start_ordinal > args.end_ordinal):
                    raise ValueError("Podaj --ordinal albo pełny, rosnący zakres --start-ordinal/--end-ordinal")
                ordinals = list(range(args.start_ordinal, args.end_ordinal + 1))
            if not ordinals:
                raise ValueError("Wymagany ordinal albo zakres stron")
            if not args.initialize_only and not args.model:
                raise ValueError("Generacja wymaga --model")
            if args.initialize_only and not args.source_index:
                raise ValueError("--initialize-only wymaga --source-index")
            if args.source_index:
                prepared = prepare_qwen_index(Path(args.source_index), Path(args.output), ordinals, project_root=root)
            if args.initialize_only:
                _json(prepared)
            else:
                _json(recognize_qwen_volume(
                    Path(args.output), ordinals, model=args.model,
                    endpoint=args.endpoint,
                    options=json.loads(args.options_json) if args.options_json else None,
                    prompt=Path(args.prompt_file).read_text(encoding="utf-8") if args.prompt_file else QWEN_VOLUME_PROMPT,
                    timeout=args.timeout,
                ))
        elif args.command == "volume-index-qwen-night":
            ordinals = args.ordinal
            if args.start_ordinal is not None or args.end_ordinal is not None:
                if (ordinals or args.start_ordinal is None or args.end_ordinal is None
                        or args.start_ordinal > args.end_ordinal):
                    raise ValueError("Podaj --ordinal albo pełny, rosnący zakres --start-ordinal/--end-ordinal")
                ordinals = list(range(args.start_ordinal, args.end_ordinal + 1))
            if not ordinals:
                raise ValueError("Wymagany ordinal albo zakres stron")
            report = run_qwen_overnight(
                Path(args.output), ordinals, project_root=root,
                source_index=Path(args.source_index) if args.source_index else None,
                model=args.model, endpoint=args.endpoint,
                options=json.loads(args.options_json) if args.options_json else None,
                rescue=not args.no_rescue,
                rescue_options=json.loads(args.rescue_options_json) if args.rescue_options_json else None,
                prompt=Path(args.prompt_file).read_text(encoding="utf-8") if args.prompt_file else QWEN_VOLUME_PROMPT,
                timeout=args.timeout, max_seconds=args.max_hours * 60 * 60,
                max_consecutive_failures=args.max_consecutive_failures,
                on_event=None if args.quiet else _qwen_night_progress,
            )
            _json(report)
            if report["status"] != "completed" or report["failed"]:
                return 2
        elif args.command == "volume-index-qwen-import":
            _json(import_qwen_diagnostic(
                Path(args.output), args.ordinal, Path(args.result_file),
                expected_response_sha256=args.expected_response_sha256,
                expected_image_sha256=args.expected_image_sha256,
                recovery_metadata=json.loads(Path(args.recovery_metadata_file).read_text(encoding="utf-8")),
            ))
        elif args.command == "volume-index-attempt-import":
            _json(import_recognition_attempt(
                Path(args.output),
                args.ordinal,
                Path(args.result_file),
                backend=args.backend,
                model=args.model,
                format_type=args.format_type,
            ))
        elif args.command == "volume-index-derive":
            _json(derive_volume_image(
                Path(args.output),
                args.ordinal,
                args.operation,
                deskew_threshold=args.deskew_threshold,
            ))
        elif args.command == "volume-index-review":
            _json(review_volume_region(
                Path(args.output),
                args.region_id,
                args.decision,
                reviewer=args.reviewer,
                corrected_text=args.corrected_text,
                note=args.note,
            ))
        elif args.command == "volume-index-writeback-candidate":
            _json(export_writeback_candidate(
                Path(args.output), args.region_id, Path(args.destination)
            ))
        elif args.command == "volume-index-ground-truth-export":
            _json(export_ground_truth(Path(args.output), Path(args.destination)))
        elif args.command == "volume-index-ground-truth-split":
            _json(split_ground_truth(
                Path(args.ground_truth),
                seed=args.seed,
                train_ratio=args.train_ratio,
                validation_ratio=args.validation_ratio,
            ))
        elif args.command == "volume-index-kraken-train":
            _json(plan_kraken_training(
                Path(args.ground_truth),
                Path(args.model_output),
                execute=args.execute,
                min_train_lines=args.min_train_lines,
                epochs=args.epochs,
            ))
        elif args.command == "volume-index-evaluate":
            _json(evaluate_volume_index(
                Path(args.output), Path(args.gold_file), top_k=args.top_k
            ))
        elif args.command == "volume-index-external-export":
            _json(export_external_htr_bundle(
                Path(args.output),
                Path(args.destination),
                args.ordinal,
                service=args.service,
                classification=args.classification,
                allow_external=args.allow_external,
                consent_reference=args.consent_reference,
                max_pages=args.max_pages,
            ))
        elif args.command == "browse-range-add":
            _json(plan_browse_range(
                root, args.session_id, args.start, args.end, actor=args.actor,
                assigned_to=args.assigned_to, range_kind=args.kind,
                start_locator=args.start_locator, end_locator=args.end_locator,
                start_page_label=args.start_page, end_page_label=args.end_page,
                start_act_label=args.start_act, end_act_label=args.end_act,
                notes=args.notes,
            ))
        elif args.command == "browse-range-claim":
            _json(claim_browse_range(
                root, args.range_id, assigned_to=args.assigned_to, actor=args.actor,
                agent_run_id=args.agent_run_id, lease_expires_at=args.lease_expires_at,
                expected_revision=args.expected_revision,
            ))
        elif args.command == "browse-checkpoint-add":
            _json(append_browse_checkpoint(
                root, args.range_id, actor=args.actor,
                checkpoint_status=args.status,
                last_completed_ordinal=args.last_ordinal,
                next_ordinal=args.next_ordinal,
                last_completed_locator=args.last_locator,
                next_locator=args.next_locator,
                last_page_label=args.last_page, last_act_label=args.last_act,
                position_fingerprint=_json_file(args.fingerprint_json, dict, "fingerprint-json"),
                search_terms_snapshot=args.term, method_snapshot=args.method,
                source_version=args.source_version,
                manifest_sha256=args.manifest_sha256,
                note=args.note, lease_token=args.lease_token,
            ))
        elif args.command == "browse-range-review":
            _json(review_browse_range(
                root, args.range_id, args.decision, actor=args.actor,
                rationale=args.rationale, expected_revision=args.expected_revision,
            ))
        elif args.command == "browse-finding-add":
            _json(add_browse_finding(
                root, args.session_id, args.locator, args.finding_type,
                created_by=args.created_by, range_id=args.range_id,
                checkpoint_id=args.checkpoint_id, ordinal=args.ordinal,
                page_label=args.page, act_label=args.act, raw_text=args.raw_text,
                normalized_text=args.normalized_text, confidence=args.confidence,
                citation_id=args.citation_id, occurrence_id=args.occurrence_id,
                assertion_id=args.assertion_id, status=args.status, note=args.note,
            ))
        elif args.command == "browse-issue-add":
            _json(add_browse_issue(
                root, args.session_id, args.locator, args.issue_field,
                args.issue_kind, created_by=args.created_by,
                image_quality=args.image_quality,
                reading_confidence=args.reading_confidence,
                review_satisfaction=args.satisfaction,
                range_id=args.range_id, checkpoint_id=args.checkpoint_id,
                finding_id=args.finding_id,
                handwriting_profile_id=args.handwriting_profile_id,
                ordinal=args.ordinal, page_label=args.page, act_label=args.act,
                primary_cause=args.primary_cause,
                additional_causes=args.additional_cause,
                current_reading=args.current_reading,
                alternatives=args.alternative,
                disputed_graphemes=args.disputed_grapheme,
                severity=args.severity,
                claim_impact=args.claim_impact,
                recommended_action=args.recommended_action,
                revisit_triggers=args.revisit_trigger,
                revisit_priority=args.revisit_priority,
            ))
        elif args.command == "browse-issue-assess":
            _json(assess_browse_issue(
                root, args.issue_id, actor=args.actor, method=args.method,
                outcome=args.outcome, artifact_id=args.artifact_id,
                handwriting_profile_id=args.handwriting_profile_id,
                tool_name=args.tool_name, tool_version=args.tool_version,
                model_name=args.model_name, model_version=args.model_version,
                proposed_reading=args.proposed_reading,
                alternatives=args.alternative, confidence=args.confidence,
                rationale=args.rationale,
            ))
        elif args.command == "browse-issue-resolve":
            _json(resolve_browse_issue(
                root, args.issue_id, actor=args.actor, decision=args.decision,
                rationale=args.rationale, final_reading=args.final_reading,
                reading_confidence=args.reading_confidence,
                review_satisfaction=args.satisfaction,
            ))
        elif args.command == "browse-trigger-set":
            _json(set_revisit_trigger_status(
                root, args.trigger_id, args.status, actor=args.actor,
                evidence=_json_file(args.evidence_json, dict, "evidence-json")
                if args.evidence_json else None,
                rationale=args.rationale,
            ))
        elif args.command == "browse-quality-review":
            _json(add_browse_quality_review(
                root, args.session_id, reviewed_by=args.reviewed_by,
                satisfaction=args.satisfaction,
                completeness_score=args.completeness,
                systematicity_score=args.systematicity,
                locator_quality_score=args.locator_quality,
                writeback_quality_score=args.writeback_quality,
                legibility_score=args.legibility, qa_score=args.qa,
                note=args.note,
            ))
        elif args.command == "hand-profile-add":
            _json(create_handwriting_profile(
                root, args.source_item_id, args.label, args.scope,
                created_by=args.created_by, script=args.script,
                languages=args.language, probable_scribe=args.probable_scribe,
                attribution_confidence=args.attribution_confidence,
                date_from=args.date_from, date_to=args.date_to,
                place_or_office=args.place_or_office,
                features=_json_file(args.features_json, dict, "features-json"),
                known_ambiguities=args.known_ambiguity,
                analysis_types=args.analysis_type,
            ))
        elif args.command == "hand-sample-add":
            _json(add_handwriting_sample(
                root, args.profile_id, args.source_item_id, args.locator,
                args.sample_type, args.diplomatic_text,
                confidence=args.confidence, created_by=args.created_by,
                artifact_id=args.artifact_id, citation_id=args.citation_id,
                ordinal=args.ordinal, page_label=args.page, act_label=args.act,
                region=_json_file(args.region_json, dict, "region-json"),
                normalized_text=args.normalized_text,
                represented_graphemes=args.grapheme,
                independent_context=not args.dependent_context,
                notes=args.notes,
            ))
        elif args.command == "hand-profile-source-set":
            _json(link_handwriting_profile_source(
                root, args.profile_id, args.source_item_id,
                scope_role=args.role, status=args.status,
                rationale=args.rationale, linked_by=args.linked_by,
            ))
        elif args.command == "hand-profile-show":
            _json(handwriting_profile_report(root, args.profile_id))
        else:
            raise AssertionError(f"Nieobsługiwane polecenie: {args.command}")
        return 0
    except (FileNotFoundError, LookupError, PermissionError, RuntimeError, ValueError) as error:
        print(f"Błąd: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
