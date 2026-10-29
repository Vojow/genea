from __future__ import annotations

import hashlib
import inspect
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .workspace import (
    dump_json,
    ensure_workspace,
    iter_files,
    research_root,
    search_fold,
    sha256_file,
    stable_id,
)
from .storage import configured_database_backend


LEGACY_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, parent_id TEXT REFERENCES sources(id), kind TEXT NOT NULL, service TEXT NOT NULL DEFAULT 'other', title TEXT NOT NULL, source_url TEXT, archive_path TEXT NOT NULL, media_type TEXT, sha256 TEXT NOT NULL, captured_at TEXT NOT NULL, accessed_at TEXT NOT NULL, rights_note TEXT, metadata_json TEXT NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS idx_sources_sha256 ON sources(sha256);
CREATE INDEX IF NOT EXISTS idx_sources_service ON sources(service);
CREATE TABLE IF NOT EXISTS analyses (id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id), result_path TEXT NOT NULL, status TEXT NOT NULL, model TEXT, created_at TEXT NOT NULL, payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS entities (id TEXT PRIMARY KEY, entity_type TEXT NOT NULL, display_name TEXT NOT NULL, normalized_name TEXT NOT NULL, notes TEXT);
CREATE INDEX IF NOT EXISTS idx_entities_normalized ON entities(normalized_name);
CREATE TABLE IF NOT EXISTS mentions (id INTEGER PRIMARY KEY AUTOINCREMENT, analysis_id TEXT NOT NULL REFERENCES analyses(id), entity_id TEXT NOT NULL REFERENCES entities(id), role TEXT, raw_text TEXT, confidence REAL, evidence_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS relations (id INTEGER PRIMARY KEY AUTOINCREMENT, analysis_id TEXT NOT NULL REFERENCES analyses(id), subject_entity_id TEXT NOT NULL REFERENCES entities(id), predicate TEXT NOT NULL, object_entity_id TEXT NOT NULL REFERENCES entities(id), date_text TEXT, place_text TEXT, confidence REAL, evidence_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS hypotheses (id TEXT PRIMARY KEY, statement TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', confidence REAL, rationale TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS hypothesis_evidence (hypothesis_id TEXT NOT NULL REFERENCES hypotheses(id), source_id TEXT NOT NULL REFERENCES sources(id), analysis_id TEXT REFERENCES analyses(id), stance TEXT NOT NULL, note TEXT, PRIMARY KEY (hypothesis_id, source_id, analysis_id));
CREATE TABLE IF NOT EXISTS project_files (path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, size INTEGER NOT NULL, modified_at REAL NOT NULL, indexed_at TEXT NOT NULL, extracted_text TEXT NOT NULL DEFAULT '');
"""


EVIDENCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS data_migration_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, migration_version INTEGER NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL, records_json TEXT NOT NULL DEFAULT '{}', error_text TEXT);
CREATE TABLE IF NOT EXISTS audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, object_type TEXT NOT NULL, object_id TEXT NOT NULL, before_json TEXT, after_json TEXT, rationale TEXT);
CREATE TABLE IF NOT EXISTS curation_queue (id TEXT PRIMARY KEY, object_type TEXT NOT NULL, object_id TEXT NOT NULL, reason_code TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL, resolved_at TEXT, resolution_note TEXT, UNIQUE(object_type, object_id, reason_code));

CREATE TABLE IF NOT EXISTS research_cases (id TEXT PRIMARY KEY, title TEXT NOT NULL, summary TEXT, status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS research_questions (id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES research_cases(id), question TEXT NOT NULL, completion_criterion TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS research_plans (id TEXT PRIMARY KEY, question_id TEXT NOT NULL REFERENCES research_questions(id), title TEXT NOT NULL, strategy TEXT, status TEXT NOT NULL DEFAULT 'draft', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS research_plan_sources (id INTEGER PRIMARY KEY AUTOINCREMENT, plan_id TEXT NOT NULL REFERENCES research_plans(id), repository_id TEXT REFERENCES repositories(id), collection_id TEXT REFERENCES collections(id), source_description TEXT NOT NULL, date_from TEXT, date_to TEXT, jurisdiction TEXT, priority INTEGER NOT NULL DEFAULT 0, UNIQUE(plan_id, source_description, date_from, date_to));
CREATE TABLE IF NOT EXISTS research_log_entries (id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES research_cases(id), question_id TEXT REFERENCES research_questions(id), query_text TEXT NOT NULL, repository TEXT, scope_text TEXT NOT NULL, outcome TEXT NOT NULL, result_summary TEXT, searched_at TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS search_coverage (id TEXT PRIMARY KEY, log_entry_id TEXT NOT NULL REFERENCES research_log_entries(id), appropriate INTEGER NOT NULL DEFAULT 0, complete INTEGER NOT NULL DEFAULT 0, systematic INTEGER NOT NULL DEFAULT 0, assessment_note TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS next_actions (id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES research_cases(id), question_id TEXT REFERENCES research_questions(id), action TEXT NOT NULL, priority INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS repositories (id TEXT PRIMARY KEY, name TEXT NOT NULL, service TEXT, url TEXT, notes TEXT);
CREATE TABLE IF NOT EXISTS collections (id TEXT PRIMARY KEY, repository_id TEXT REFERENCES repositories(id), parent_id TEXT REFERENCES collections(id), title TEXT NOT NULL, reference_code TEXT, date_from TEXT, date_to TEXT, notes TEXT);
CREATE TABLE IF NOT EXISTS source_items (id TEXT PRIMARY KEY, collection_id TEXT REFERENCES collections(id), parent_id TEXT REFERENCES source_items(id), legacy_source_id TEXT UNIQUE REFERENCES sources(id), title TEXT NOT NULL, item_type TEXT NOT NULL, date_text TEXT, locator TEXT, source_url TEXT, citation_text TEXT, source_quality TEXT NOT NULL DEFAULT 'Indeterminable', verification_status TEXT NOT NULL DEFAULT 'imported_unverified', metadata_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS artifacts (id TEXT PRIMARY KEY, source_item_id TEXT NOT NULL REFERENCES source_items(id), parent_artifact_id TEXT REFERENCES artifacts(id), legacy_source_id TEXT UNIQUE REFERENCES sources(id), artifact_type TEXT NOT NULL, path TEXT NOT NULL, media_type TEXT, sha256 TEXT, immutable INTEGER NOT NULL DEFAULT 0, created_at TEXT, metadata_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS artifact_derivations (id INTEGER PRIMARY KEY AUTOINCREMENT, parent_artifact_id TEXT NOT NULL REFERENCES artifacts(id), child_artifact_id TEXT NOT NULL REFERENCES artifacts(id), operation TEXT NOT NULL, tool TEXT, parameters_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, UNIQUE(parent_artifact_id, child_artifact_id));
CREATE TABLE IF NOT EXISTS citations (id TEXT PRIMARY KEY, source_item_id TEXT NOT NULL REFERENCES source_items(id), artifact_id TEXT REFERENCES artifacts(id), citation_text TEXT NOT NULL, locator TEXT, accessed_at TEXT, created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS readings (id TEXT PRIMARY KEY, source_item_id TEXT NOT NULL REFERENCES source_items(id), artifact_id TEXT REFERENCES artifacts(id), legacy_analysis_id TEXT UNIQUE REFERENCES analyses(id), reading_type TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft', model_or_reviewer TEXT, created_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS reading_versions (id TEXT PRIMARY KEY, reading_id TEXT NOT NULL REFERENCES readings(id), supersedes_id TEXT REFERENCES reading_versions(id), version_number INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'draft', result_path TEXT, payload_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, UNIQUE(reading_id, version_number));
CREATE TABLE IF NOT EXISTS transcript_segments (id TEXT PRIMARY KEY, reading_version_id TEXT NOT NULL REFERENCES reading_versions(id), artifact_id TEXT REFERENCES artifacts(id), sequence_number INTEGER NOT NULL, language TEXT, page_label TEXT, region_json TEXT, text TEXT NOT NULL, UNIQUE(reading_version_id, sequence_number));
CREATE TABLE IF NOT EXISTS field_readings (id TEXT PRIMARY KEY, reading_version_id TEXT NOT NULL REFERENCES reading_versions(id), transcript_segment_id TEXT REFERENCES transcript_segments(id), field_name TEXT NOT NULL, value_text TEXT, alternatives_json TEXT NOT NULL DEFAULT '[]', reading_certainty TEXT NOT NULL DEFAULT 'Ambiguous', notes TEXT);

CREATE TABLE IF NOT EXISTS persons (id TEXT PRIMARY KEY, preferred_name TEXT, status TEXT NOT NULL DEFAULT 'candidate', created_at TEXT NOT NULL, notes TEXT);
CREATE TABLE IF NOT EXISTS person_names (id TEXT PRIMARY KEY, person_id TEXT NOT NULL REFERENCES persons(id), name_text TEXT NOT NULL, normalized_name TEXT, name_type TEXT NOT NULL DEFAULT 'source', language TEXT, date_from TEXT, date_to TEXT, source_assertion_id TEXT REFERENCES source_assertions(id));
CREATE INDEX IF NOT EXISTS idx_person_names_normalized ON person_names(normalized_name);
CREATE TABLE IF NOT EXISTS person_occurrences (id TEXT PRIMARY KEY, source_item_id TEXT NOT NULL REFERENCES source_items(id), reading_version_id TEXT REFERENCES reading_versions(id), transcript_segment_id TEXT REFERENCES transcript_segments(id), person_id TEXT REFERENCES persons(id), legacy_mention_id INTEGER UNIQUE REFERENCES mentions(id), raw_name TEXT NOT NULL, normalized_name TEXT, search_name TEXT, role_text TEXT, reading_confidence REAL, verification_status TEXT NOT NULL DEFAULT 'imported_unverified', evidence_json TEXT NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS idx_occurrences_normalized ON person_occurrences(normalized_name);
CREATE INDEX IF NOT EXISTS idx_occurrences_search_name ON person_occurrences(search_name);
CREATE TABLE IF NOT EXISTS occurrence_readings (id TEXT PRIMARY KEY, occurrence_id TEXT NOT NULL REFERENCES person_occurrences(id), reading_version_id TEXT NOT NULL REFERENCES reading_versions(id), role_text TEXT, evidence_json TEXT NOT NULL DEFAULT '{}', UNIQUE(occurrence_id, reading_version_id));
CREATE TABLE IF NOT EXISTS occurrence_legacy_mentions (id TEXT PRIMARY KEY, occurrence_id TEXT NOT NULL REFERENCES person_occurrences(id), legacy_mention_id INTEGER NOT NULL UNIQUE REFERENCES mentions(id), reading_version_id TEXT NOT NULL REFERENCES reading_versions(id), role_text TEXT, evidence_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS identity_candidates (id TEXT PRIMARY KEY, occurrence_a_id TEXT NOT NULL REFERENCES person_occurrences(id), occurrence_b_id TEXT NOT NULL REFERENCES person_occurrences(id), score REAL, basis_json TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'unresolved', created_at TEXT NOT NULL, UNIQUE(occurrence_a_id, occurrence_b_id));
CREATE TABLE IF NOT EXISTS identity_decisions (id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL REFERENCES identity_candidates(id), decision TEXT NOT NULL, decided_by TEXT NOT NULL, decided_at TEXT NOT NULL, rationale TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS places (id TEXT PRIMARY KEY, preferred_name TEXT NOT NULL, place_type TEXT, latitude REAL, longitude REAL, notes TEXT);
CREATE TABLE IF NOT EXISTS place_names (id TEXT PRIMARY KEY, place_id TEXT NOT NULL REFERENCES places(id), name_text TEXT NOT NULL, normalized_name TEXT, language TEXT, date_from TEXT, date_to TEXT, source_item_id TEXT REFERENCES source_items(id));
CREATE INDEX IF NOT EXISTS idx_place_names_normalized ON place_names(normalized_name);
CREATE TABLE IF NOT EXISTS place_jurisdictions (id TEXT PRIMARY KEY, place_id TEXT NOT NULL REFERENCES places(id), jurisdiction_place_id TEXT REFERENCES places(id), jurisdiction_name TEXT NOT NULL, jurisdiction_type TEXT, date_from TEXT, date_to TEXT, source_item_id TEXT REFERENCES source_items(id));
CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, source_item_id TEXT REFERENCES source_items(id), event_type TEXT NOT NULL, date_text TEXT, date_from TEXT, date_to TEXT, place_id TEXT REFERENCES places(id), place_text TEXT, verification_status TEXT NOT NULL DEFAULT 'imported_unverified', notes TEXT);
CREATE TABLE IF NOT EXISTS event_readings (id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(id), reading_version_id TEXT NOT NULL REFERENCES reading_versions(id), evidence_json TEXT NOT NULL DEFAULT '{}', UNIQUE(event_id, reading_version_id));
CREATE TABLE IF NOT EXISTS participations (id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(id), occurrence_id TEXT NOT NULL REFERENCES person_occurrences(id), role TEXT NOT NULL, age_text TEXT, occupation_text TEXT, residence_text TEXT, status_text TEXT, is_informant INTEGER NOT NULL DEFAULT 0, source_assertion_id TEXT REFERENCES source_assertions(id), UNIQUE(event_id, occurrence_id, role));
CREATE TABLE IF NOT EXISTS households (id TEXT PRIMARY KEY, place_id TEXT REFERENCES places(id), label TEXT NOT NULL, date_from TEXT, date_to TEXT, verification_status TEXT NOT NULL DEFAULT 'imported_unverified', notes TEXT);
CREATE TABLE IF NOT EXISTS addresses (id TEXT PRIMARY KEY, household_id TEXT REFERENCES households(id), place_id TEXT NOT NULL REFERENCES places(id), address_text TEXT NOT NULL, house_number TEXT, date_from TEXT, date_to TEXT, source_item_id TEXT REFERENCES source_items(id));
CREATE TABLE IF NOT EXISTS household_memberships (id TEXT PRIMARY KEY, household_id TEXT NOT NULL REFERENCES households(id), person_id TEXT REFERENCES persons(id), occurrence_id TEXT REFERENCES person_occurrences(id), role TEXT, date_from TEXT, date_to TEXT, source_assertion_id TEXT REFERENCES source_assertions(id));

CREATE TABLE IF NOT EXISTS source_assertions (id TEXT PRIMARY KEY, source_item_id TEXT NOT NULL REFERENCES source_items(id), reading_version_id TEXT REFERENCES reading_versions(id), transcript_segment_id TEXT REFERENCES transcript_segments(id), legacy_relation_id INTEGER UNIQUE REFERENCES relations(id), legacy_evidence_key TEXT UNIQUE, assertion_type TEXT NOT NULL, subject_ref TEXT, predicate TEXT NOT NULL, object_text TEXT, information_quality TEXT NOT NULL DEFAULT 'Indeterminable', reading_certainty TEXT NOT NULL DEFAULT 'Ambiguous', verification_status TEXT NOT NULL DEFAULT 'imported_unverified', metadata_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS informants (id TEXT PRIMARY KEY, source_assertion_id TEXT NOT NULL REFERENCES source_assertions(id), occurrence_id TEXT REFERENCES person_occurrences(id), role TEXT, knowledge_assessment TEXT, notes TEXT);
CREATE TABLE IF NOT EXISTS claims (id TEXT PRIMARY KEY, case_id TEXT REFERENCES research_cases(id), legacy_hypothesis_id TEXT UNIQUE REFERENCES hypotheses(id), statement TEXT NOT NULL, subject_ref TEXT, predicate TEXT, object_ref TEXT, status TEXT NOT NULL DEFAULT 'imported_unverified', rationale TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS claim_evidence (id TEXT PRIMARY KEY, claim_id TEXT NOT NULL REFERENCES claims(id), source_assertion_id TEXT NOT NULL REFERENCES source_assertions(id), stance TEXT NOT NULL, evidence_type TEXT NOT NULL DEFAULT 'Indeterminable', dependency_group TEXT, note TEXT, UNIQUE(claim_id, source_assertion_id, stance));
CREATE TABLE IF NOT EXISTS conflicts (id TEXT PRIMARY KEY, claim_id TEXT REFERENCES claims(id), title TEXT NOT NULL, description TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS conflict_resolutions (id TEXT PRIMARY KEY, conflict_id TEXT NOT NULL REFERENCES conflicts(id), resolution TEXT NOT NULL, rationale TEXT NOT NULL, resolved_by TEXT NOT NULL, resolved_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS conclusions (id TEXT PRIMARY KEY, case_id TEXT REFERENCES research_cases(id), claim_id TEXT REFERENCES claims(id), status TEXT NOT NULL, summary TEXT NOT NULL, reasoning TEXT NOT NULL, author TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS place_studies (id TEXT PRIMARY KEY, case_id TEXT REFERENCES research_cases(id), place_id TEXT NOT NULL REFERENCES places(id), title TEXT NOT NULL, date_from TEXT, date_to TEXT, scope TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'planned', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS place_study_coverage (id TEXT PRIMARY KEY, study_id TEXT NOT NULL REFERENCES place_studies(id), collection_id TEXT REFERENCES collections(id), year_from INTEGER, year_to INTEGER, record_type TEXT NOT NULL, coverage_status TEXT NOT NULL, completeness REAL, notes TEXT);
CREATE TABLE IF NOT EXISTS local_occurrences (id TEXT PRIMARY KEY, study_id TEXT NOT NULL REFERENCES place_studies(id), occurrence_id TEXT REFERENCES person_occurrences(id), household_id TEXT REFERENCES households(id), source_item_id TEXT NOT NULL REFERENCES source_items(id), event_date_text TEXT, event_type TEXT, role TEXT, surname_variant TEXT, given_names TEXT, search_name TEXT, house_number TEXT, citation_id TEXT REFERENCES citations(id), verification_status TEXT NOT NULL DEFAULT 'imported_unverified', metadata_json TEXT NOT NULL DEFAULT '{}');

CREATE INDEX IF NOT EXISTS idx_source_items_legacy ON source_items(legacy_source_id);
CREATE INDEX IF NOT EXISTS idx_readings_source ON readings(source_item_id);
CREATE INDEX IF NOT EXISTS idx_events_date ON events(date_from, date_to);
CREATE INDEX IF NOT EXISTS idx_participations_role ON participations(role);
CREATE INDEX IF NOT EXISTS idx_assertions_source ON source_assertions(source_item_id);
CREATE INDEX IF NOT EXISTS idx_claim_evidence_claim ON claim_evidence(claim_id);
CREATE INDEX IF NOT EXISTS idx_local_occurrences_name ON local_occurrences(surname_variant, given_names);
CREATE INDEX IF NOT EXISTS idx_local_occurrences_search_name ON local_occurrences(search_name);
"""


PILOT_SCHEMA = """
CREATE TABLE IF NOT EXISTS reading_comparisons (
    id TEXT PRIMARY KEY,
    source_item_id TEXT NOT NULL REFERENCES source_items(id),
    blind_version_id TEXT NOT NULL REFERENCES reading_versions(id),
    contextual_version_id TEXT NOT NULL REFERENCES reading_versions(id),
    reviewer_version_id TEXT REFERENCES reading_versions(id),
    differences_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','reviewed')),
    created_at TEXT NOT NULL,
    reviewed_at TEXT,
    UNIQUE(blind_version_id, contextual_version_id)
);
CREATE TABLE IF NOT EXISTS privacy_labels (
    id TEXT PRIMARY KEY,
    object_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    classification TEXT NOT NULL CHECK(classification IN ('public_historical','deceased','living','restricted')),
    rationale TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(object_type, object_id)
);
CREATE INDEX IF NOT EXISTS idx_privacy_labels_object
    ON privacy_labels(object_type, object_id, classification);
"""


QUESTION_TARGET_SCHEMA = """
ALTER TABLE research_questions
    ADD COLUMN priority INTEGER NOT NULL DEFAULT 100 CHECK(priority >= 0);

CREATE TABLE IF NOT EXISTS research_question_targets (
    id TEXT PRIMARY KEY,
    question_id TEXT NOT NULL REFERENCES research_questions(id) ON DELETE CASCADE,
    target_type TEXT NOT NULL CHECK(target_type IN (
        'person', 'person_occurrence', 'named_person', 'household', 'place',
        'place_study', 'family_cluster', 'event', 'source_item', 'other'
    )),
    target_id TEXT NOT NULL,
    target_label TEXT NOT NULL,
    is_primary INTEGER NOT NULL DEFAULT 0 CHECK(is_primary IN (0, 1)),
    notes TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(question_id, target_type, target_id)
);
CREATE INDEX IF NOT EXISTS idx_research_questions_priority
    ON research_questions(status, priority, case_id);
CREATE INDEX IF NOT EXISTS idx_research_question_targets_object
    ON research_question_targets(target_type, target_id, question_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_research_question_one_primary_target
    ON research_question_targets(question_id) WHERE is_primary = 1;
"""


BROWSE_RESEARCH_SCHEMA = """
CREATE TABLE IF NOT EXISTS handwriting_profiles (
    id TEXT PRIMARY KEY,
    source_item_id TEXT NOT NULL REFERENCES source_items(id),
    label TEXT NOT NULL,
    scope_text TEXT NOT NULL,
    script TEXT,
    languages_json TEXT NOT NULL DEFAULT '[]',
    probable_scribe TEXT,
    attribution_confidence REAL CHECK(attribution_confidence IS NULL OR (attribution_confidence >= 0 AND attribution_confidence <= 1)),
    date_from TEXT,
    date_to TEXT,
    place_or_office TEXT,
    status TEXT NOT NULL DEFAULT 'collecting' CHECK(status IN ('collecting','usable','reviewed','invalidated')),
    features_json TEXT NOT NULL DEFAULT '{}',
    known_ambiguities_json TEXT NOT NULL DEFAULT '[]',
    analysis_types_json TEXT NOT NULL DEFAULT '[]',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS handwriting_samples (
    id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL REFERENCES handwriting_profiles(id),
    source_item_id TEXT NOT NULL REFERENCES source_items(id),
    artifact_id TEXT REFERENCES artifacts(id),
    citation_id TEXT REFERENCES citations(id),
    ordinal INTEGER CHECK(ordinal IS NULL OR ordinal >= 1),
    locator TEXT NOT NULL,
    page_label TEXT,
    act_label TEXT,
    region_json TEXT NOT NULL DEFAULT '{}',
    region_hash TEXT,
    sample_type TEXT NOT NULL CHECK(sample_type IN ('grapheme','word','name','surname','toponym','number','formula','abbreviation','signature','other')),
    diplomatic_text TEXT NOT NULL,
    normalized_text TEXT,
    represented_graphemes_json TEXT NOT NULL DEFAULT '[]',
    confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
    independent_context INTEGER NOT NULL DEFAULT 1 CHECK(independent_context IN (0,1)),
    notes TEXT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(profile_id, artifact_id, region_hash, diplomatic_text)
);

CREATE TABLE IF NOT EXISTS browse_sessions (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES research_cases(id),
    question_id TEXT REFERENCES research_questions(id),
    source_item_id TEXT NOT NULL REFERENCES source_items(id),
    artifact_id TEXT REFERENCES artifacts(id),
    place_study_id TEXT REFERENCES place_studies(id),
    title TEXT NOT NULL,
    unit_type TEXT NOT NULL CHECK(unit_type IN ('index','register','volume','viewer','collection','other')),
    ordinal_kind TEXT NOT NULL CHECK(ordinal_kind IN ('scan','page','folio','act','entry','row','household','custom')),
    arrangement TEXT NOT NULL CHECK(arrangement IN ('chronological','alphabetical','act_number','household','geographical','mixed','unknown')),
    method TEXT NOT NULL CHECK(method IN ('manual','ocr','llm','hybrid','index_lookup')),
    scope_text TEXT NOT NULL,
    search_terms_json TEXT NOT NULL DEFAULT '[]',
    query_fingerprint TEXT NOT NULL,
    canonical_url TEXT,
    source_version TEXT,
    manifest_sha256 TEXT,
    expected_start_ordinal INTEGER CHECK(expected_start_ordinal IS NULL OR expected_start_ordinal >= 1),
    expected_end_ordinal INTEGER CHECK(expected_end_ordinal IS NULL OR expected_end_ordinal >= 1),
    expected_unit_count INTEGER CHECK(expected_unit_count IS NULL OR expected_unit_count >= 0),
    status TEXT NOT NULL DEFAULT 'planned' CHECK(status IN ('planned','in_progress','paused','submitted','completed','closed_partial','review_required','invalidated')),
    privacy_classification TEXT NOT NULL DEFAULT 'public_historical' CHECK(privacy_classification IN ('public_historical','deceased','living','restricted')),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    CHECK(expected_start_ordinal IS NULL OR expected_end_ordinal IS NULL OR expected_end_ordinal >= expected_start_ordinal)
);

CREATE TABLE IF NOT EXISTS browse_ranges (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES browse_sessions(id),
    start_ordinal INTEGER NOT NULL CHECK(start_ordinal >= 1),
    end_ordinal INTEGER NOT NULL CHECK(end_ordinal >= start_ordinal),
    start_locator TEXT,
    end_locator TEXT,
    start_page_label TEXT,
    end_page_label TEXT,
    start_act_label TEXT,
    end_act_label TEXT,
    range_kind TEXT NOT NULL DEFAULT 'primary' CHECK(range_kind IN ('primary','qa_overlap')),
    status TEXT NOT NULL DEFAULT 'assigned' CHECK(status IN ('planned','assigned','in_progress','paused','submitted','accepted','accepted_partial','rejected','blocked','invalidated')),
    assigned_to TEXT,
    agent_run_id TEXT,
    lease_token TEXT,
    lease_expires_at TEXT,
    revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS browse_inventory_units (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES browse_sessions(id),
    ordinal INTEGER NOT NULL CHECK(ordinal >= 1),
    stable_locator TEXT NOT NULL,
    page_label TEXT,
    act_label TEXT,
    content_sha256 TEXT,
    availability TEXT NOT NULL CHECK(availability IN ('available','missing','duplicate','restricted')),
    duplicate_of_ordinal INTEGER CHECK(duplicate_of_ordinal IS NULL OR duplicate_of_ordinal >= 1),
    notes TEXT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(session_id, ordinal),
    CHECK(availability = 'duplicate' OR duplicate_of_ordinal IS NULL)
);

CREATE TABLE IF NOT EXISTS browse_checkpoints (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES browse_sessions(id),
    range_id TEXT NOT NULL REFERENCES browse_ranges(id),
    supersedes_id TEXT REFERENCES browse_checkpoints(id),
    sequence_number INTEGER NOT NULL CHECK(sequence_number >= 1),
    last_completed_ordinal INTEGER CHECK(last_completed_ordinal IS NULL OR last_completed_ordinal >= 1),
    last_completed_locator TEXT,
    last_page_label TEXT,
    last_act_label TEXT,
    next_ordinal INTEGER CHECK(next_ordinal IS NULL OR next_ordinal >= 1),
    next_locator TEXT,
    position_fingerprint_json TEXT NOT NULL DEFAULT '{}',
    search_terms_snapshot_json TEXT NOT NULL DEFAULT '[]',
    method_snapshot TEXT NOT NULL,
    source_version TEXT,
    manifest_sha256 TEXT,
    checkpoint_status TEXT NOT NULL CHECK(checkpoint_status IN ('progress','paused','submitted','accepted','blocked','handoff','invalidated')),
    note TEXT,
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(range_id, sequence_number)
);

CREATE TABLE IF NOT EXISTS browse_findings (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES browse_sessions(id),
    range_id TEXT REFERENCES browse_ranges(id),
    checkpoint_id TEXT REFERENCES browse_checkpoints(id),
    ordinal INTEGER CHECK(ordinal IS NULL OR ordinal >= 1),
    locator TEXT NOT NULL,
    page_label TEXT,
    act_label TEXT,
    finding_type TEXT NOT NULL CHECK(finding_type IN ('exact_hit','candidate','near_miss','context','negative_control','anomaly')),
    raw_text TEXT,
    normalized_text TEXT,
    confidence REAL CHECK(confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    citation_id TEXT REFERENCES citations(id),
    occurrence_id TEXT REFERENCES person_occurrences(id),
    assertion_id TEXT REFERENCES source_assertions(id),
    verification_status TEXT NOT NULL DEFAULT 'imported_unverified' CHECK(verification_status = 'imported_unverified'),
    status TEXT NOT NULL DEFAULT 'lead' CHECK(status IN ('lead','recorded','review_required','dismissed')),
    note TEXT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS browse_issues (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES browse_sessions(id),
    range_id TEXT REFERENCES browse_ranges(id),
    checkpoint_id TEXT REFERENCES browse_checkpoints(id),
    finding_id TEXT REFERENCES browse_findings(id),
    handwriting_profile_id TEXT REFERENCES handwriting_profiles(id),
    ordinal INTEGER CHECK(ordinal IS NULL OR ordinal >= 1),
    locator TEXT NOT NULL,
    page_label TEXT,
    act_label TEXT,
    issue_field TEXT NOT NULL CHECK(issue_field IN ('whole_entry','given_name','surname','alias','patronymic','toponym','jurisdiction','date','age','sex','occupation','social_status','relationship','event_role','religion','act_number','house_number','other_number','marginal_note','abbreviation','signature','language','hand_attribution','other')),
    issue_kind TEXT NOT NULL CHECK(issue_kind IN ('unreadable','partially_readable','ambiguous_grapheme','ambiguous_word_boundary','ambiguous_abbreviation','ambiguous_expansion','ambiguous_numeral','ambiguous_language','ambiguous_hand','overwritten','crossed_out','corrected_in_source','missing_text','conflicting_readings','context_mismatch','unexpected_formula','uncertain_locator','ocr_disagreement','index_source_disagreement')),
    primary_cause TEXT,
    additional_causes_json TEXT NOT NULL DEFAULT '[]',
    image_quality TEXT NOT NULL CHECK(image_quality IN ('q0_unusable','q1_poor','q2_usable','q3_good','q4_excellent')),
    current_reading TEXT,
    alternatives_json TEXT NOT NULL DEFAULT '[]',
    disputed_graphemes_json TEXT NOT NULL DEFAULT '[]',
    reading_confidence TEXT NOT NULL CHECK(reading_confidence IN ('c0_none','c1_guess','c2_plausible','c3_high','c4_secure')),
    review_satisfaction TEXT NOT NULL CHECK(review_satisfaction IN ('s0_blocked','s1_provisional','s2_sufficient','s3_verified')),
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','in_review','waiting_for_trigger','resolved','waived','reopened')),
    severity TEXT NOT NULL DEFAULT 'warning' CHECK(severity IN ('info','warning','blocker')),
    claim_impact TEXT NOT NULL DEFAULT 'none' CHECK(claim_impact IN ('none','low','medium','high','critical')),
    recommended_action TEXT,
    resolution_note TEXT,
    created_by TEXT NOT NULL,
    last_reviewed_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS browse_revisit_triggers (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES browse_sessions(id),
    issue_id TEXT REFERENCES browse_issues(id),
    trigger_type TEXT NOT NULL CHECK(trigger_type IN ('better_scan_available','alternate_scan_available','color_scan_available','missing_area_recovered','new_ocr_engine','new_ocr_version','new_llm_model','new_transcription_method','hand_profile_ready','hand_profile_expanded','disputed_grapheme_profiled','parallel_word_found','parallel_entry_found','adjacent_page_available','duplicate_record_found','external_index_found','new_place_dictionary_entry','new_occupation_dictionary_entry','new_family_context','new_conflict','expert_review_available','peer_review_requested','user_request','quality_audit')),
    condition_text TEXT,
    priority TEXT NOT NULL DEFAULT 'r2_medium' CHECK(priority IN ('r0_none','r1_low','r2_medium','r3_high','r4_critical')),
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','satisfied','dismissed')),
    evidence_json TEXT NOT NULL DEFAULT '{}',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    satisfied_at TEXT
);

CREATE TABLE IF NOT EXISTS browse_issue_assessments (
    id TEXT PRIMARY KEY,
    issue_id TEXT NOT NULL REFERENCES browse_issues(id),
    artifact_id TEXT REFERENCES artifacts(id),
    handwriting_profile_id TEXT REFERENCES handwriting_profiles(id),
    method TEXT NOT NULL CHECK(method IN ('manual','ocr','llm','hybrid','paleographic_profile','expert_review')),
    tool_name TEXT,
    tool_version TEXT,
    model_name TEXT,
    model_version TEXT,
    proposed_reading TEXT,
    alternatives_json TEXT NOT NULL DEFAULT '[]',
    confidence REAL CHECK(confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    outcome TEXT NOT NULL CHECK(outcome IN ('improved','unchanged','worse','resolved','conflicting')),
    rationale TEXT,
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS browse_quality_reviews (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES browse_sessions(id),
    completeness_score REAL NOT NULL CHECK(completeness_score >= 0 AND completeness_score <= 1),
    systematicity_score REAL NOT NULL CHECK(systematicity_score >= 0 AND systematicity_score <= 1),
    locator_quality_score REAL NOT NULL CHECK(locator_quality_score >= 0 AND locator_quality_score <= 1),
    writeback_quality_score REAL NOT NULL CHECK(writeback_quality_score >= 0 AND writeback_quality_score <= 1),
    legibility_score REAL NOT NULL CHECK(legibility_score >= 0 AND legibility_score <= 1),
    qa_score REAL NOT NULL CHECK(qa_score >= 0 AND qa_score <= 1),
    overall_score REAL NOT NULL CHECK(overall_score >= 0 AND overall_score <= 1),
    satisfaction TEXT NOT NULL CHECK(satisfaction IN ('s0_blocked','s1_provisional','s2_sufficient','s3_verified')),
    reviewed_by TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_browse_sessions_case ON browse_sessions(case_id, status, updated_at);
CREATE INDEX IF NOT EXISTS idx_browse_sessions_source ON browse_sessions(source_item_id, query_fingerprint);
CREATE INDEX IF NOT EXISTS idx_browse_ranges_session ON browse_ranges(session_id, range_kind, start_ordinal, end_ordinal);
CREATE INDEX IF NOT EXISTS idx_browse_inventory_session ON browse_inventory_units(session_id, ordinal, availability);
CREATE INDEX IF NOT EXISTS idx_browse_checkpoints_session ON browse_checkpoints(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_browse_findings_session ON browse_findings(session_id, ordinal, finding_type);
CREATE INDEX IF NOT EXISTS idx_browse_issues_open ON browse_issues(session_id, status, claim_impact, issue_field);
CREATE INDEX IF NOT EXISTS idx_browse_revisit_pending ON browse_revisit_triggers(status, priority, session_id);
CREATE INDEX IF NOT EXISTS idx_handwriting_samples_profile ON handwriting_samples(profile_id, sample_type, ordinal);
CREATE INDEX IF NOT EXISTS idx_browse_quality_session ON browse_quality_reviews(session_id, created_at);

CREATE TRIGGER IF NOT EXISTS trg_browse_session_question_case_insert
BEFORE INSERT ON browse_sessions
WHEN NEW.question_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM research_questions q
    WHERE q.id = NEW.question_id AND q.case_id = NEW.case_id
)
BEGIN
    SELECT RAISE(ABORT, 'browse session question must belong to case');
END;

CREATE TRIGGER IF NOT EXISTS trg_browse_session_question_case_update
BEFORE UPDATE OF case_id, question_id ON browse_sessions
WHEN NEW.question_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM research_questions q
    WHERE q.id = NEW.question_id AND q.case_id = NEW.case_id
)
BEGIN
    SELECT RAISE(ABORT, 'browse session question must belong to case');
END;

CREATE TRIGGER IF NOT EXISTS trg_browse_range_bounds_insert
BEFORE INSERT ON browse_ranges
WHEN EXISTS (
    SELECT 1 FROM browse_sessions s WHERE s.id = NEW.session_id AND (
        (s.expected_start_ordinal IS NOT NULL AND NEW.start_ordinal < s.expected_start_ordinal) OR
        (s.expected_end_ordinal IS NOT NULL AND NEW.end_ordinal > s.expected_end_ordinal)
    )
)
BEGIN
    SELECT RAISE(ABORT, 'browse range outside expected session bounds');
END;

CREATE TRIGGER IF NOT EXISTS trg_browse_range_bounds_update
BEFORE UPDATE OF session_id, start_ordinal, end_ordinal ON browse_ranges
WHEN EXISTS (
    SELECT 1 FROM browse_sessions s WHERE s.id = NEW.session_id AND (
        (s.expected_start_ordinal IS NOT NULL AND NEW.start_ordinal < s.expected_start_ordinal) OR
        (s.expected_end_ordinal IS NOT NULL AND NEW.end_ordinal > s.expected_end_ordinal)
    )
)
BEGIN
    SELECT RAISE(ABORT, 'browse range outside expected session bounds');
END;

CREATE TRIGGER IF NOT EXISTS trg_browse_range_primary_overlap_insert
BEFORE INSERT ON browse_ranges
WHEN NEW.range_kind = 'primary' AND NEW.status NOT IN ('rejected','invalidated') AND EXISTS (
    SELECT 1 FROM browse_ranges r
    WHERE r.session_id = NEW.session_id
      AND r.range_kind = 'primary'
      AND r.status NOT IN ('rejected','invalidated')
      AND NEW.start_ordinal <= r.end_ordinal
      AND NEW.end_ordinal >= r.start_ordinal
)
BEGIN
    SELECT RAISE(ABORT, 'overlapping primary browse ranges are not allowed');
END;

CREATE TRIGGER IF NOT EXISTS trg_browse_range_primary_overlap_update
BEFORE UPDATE OF session_id, start_ordinal, end_ordinal, range_kind, status ON browse_ranges
WHEN NEW.range_kind = 'primary' AND NEW.status NOT IN ('rejected','invalidated') AND EXISTS (
    SELECT 1 FROM browse_ranges r
    WHERE r.id <> NEW.id
      AND r.session_id = NEW.session_id
      AND r.range_kind = 'primary'
      AND r.status NOT IN ('rejected','invalidated')
      AND NEW.start_ordinal <= r.end_ordinal
      AND NEW.end_ordinal >= r.start_ordinal
)
BEGIN
    SELECT RAISE(ABORT, 'overlapping primary browse ranges are not allowed');
END;

CREATE TRIGGER IF NOT EXISTS trg_browse_checkpoint_append_only_update
BEFORE UPDATE ON browse_checkpoints
BEGIN
    SELECT RAISE(ABORT, 'browse checkpoints are append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_browse_checkpoint_append_only_delete
BEFORE DELETE ON browse_checkpoints
BEGIN
    SELECT RAISE(ABORT, 'browse checkpoints are append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_browse_checkpoint_consistency_insert
BEFORE INSERT ON browse_checkpoints
WHEN NOT EXISTS (
    SELECT 1 FROM browse_ranges r
    WHERE r.id = NEW.range_id
      AND r.session_id = NEW.session_id
      AND (NEW.last_completed_ordinal IS NULL OR
           NEW.last_completed_ordinal BETWEEN r.start_ordinal AND r.end_ordinal)
      AND (NEW.next_ordinal IS NULL OR
           NEW.next_ordinal BETWEEN r.start_ordinal AND r.end_ordinal + 1)
)
BEGIN
    SELECT RAISE(ABORT, 'browse checkpoint does not match range');
END;

CREATE TRIGGER IF NOT EXISTS trg_browse_checkpoint_chain_insert
BEFORE INSERT ON browse_checkpoints
WHEN NEW.sequence_number <> COALESCE((
         SELECT MAX(c.sequence_number) + 1 FROM browse_checkpoints c
         WHERE c.range_id = NEW.range_id
     ), 1)
  OR (NEW.sequence_number = 1 AND NEW.supersedes_id IS NOT NULL)
  OR (NEW.sequence_number > 1 AND NEW.supersedes_id IS NOT (
         SELECT c.id FROM browse_checkpoints c
         WHERE c.range_id = NEW.range_id
         ORDER BY c.sequence_number DESC LIMIT 1
     ))
BEGIN
    SELECT RAISE(ABORT, 'invalid browse checkpoint chain');
END;

CREATE TRIGGER IF NOT EXISTS trg_browse_finding_range_consistency_insert
BEFORE INSERT ON browse_findings
WHEN NEW.range_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM browse_ranges r
    WHERE r.id = NEW.range_id AND r.session_id = NEW.session_id
)
BEGIN
    SELECT RAISE(ABORT, 'browse finding range does not match session');
END;

CREATE TRIGGER IF NOT EXISTS trg_browse_issue_range_consistency_insert
BEFORE INSERT ON browse_issues
WHEN NEW.range_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM browse_ranges r
    WHERE r.id = NEW.range_id AND r.session_id = NEW.session_id
)
BEGIN
    SELECT RAISE(ABORT, 'browse issue range does not match session');
END;

CREATE TRIGGER IF NOT EXISTS trg_browse_assessment_append_only_update
BEFORE UPDATE ON browse_issue_assessments
BEGIN
    SELECT RAISE(ABORT, 'browse issue assessments are append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_browse_assessment_append_only_delete
BEFORE DELETE ON browse_issue_assessments
BEGIN
    SELECT RAISE(ABORT, 'browse issue assessments are append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_browse_quality_append_only_update
BEFORE UPDATE ON browse_quality_reviews
BEGIN
    SELECT RAISE(ABORT, 'browse quality reviews are append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_browse_quality_append_only_delete
BEFORE DELETE ON browse_quality_reviews
BEGIN
    SELECT RAISE(ABORT, 'browse quality reviews are append-only');
END;

CREATE TRIGGER IF NOT EXISTS trg_browse_closed_range_insert
BEFORE INSERT ON browse_ranges
WHEN EXISTS (
    SELECT 1 FROM browse_sessions s WHERE s.id=NEW.session_id
    AND s.status IN ('completed','closed_partial','invalidated')
)
BEGIN
    SELECT RAISE(ABORT, 'closed browse session is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_browse_closed_checkpoint_insert
BEFORE INSERT ON browse_checkpoints
WHEN EXISTS (
    SELECT 1 FROM browse_sessions s WHERE s.id=NEW.session_id
    AND s.status IN ('completed','closed_partial','invalidated')
)
BEGIN
    SELECT RAISE(ABORT, 'closed browse session is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_browse_closed_finding_insert
BEFORE INSERT ON browse_findings
WHEN EXISTS (
    SELECT 1 FROM browse_sessions s WHERE s.id=NEW.session_id
    AND s.status IN ('completed','closed_partial','invalidated')
)
BEGIN
    SELECT RAISE(ABORT, 'closed browse session is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_browse_closed_issue_insert
BEFORE INSERT ON browse_issues
WHEN EXISTS (
    SELECT 1 FROM browse_sessions s WHERE s.id=NEW.session_id
    AND s.status IN ('completed','closed_partial','invalidated')
)
BEGIN
    SELECT RAISE(ABORT, 'closed browse session is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_browse_closed_quality_insert
BEFORE INSERT ON browse_quality_reviews
WHEN EXISTS (
    SELECT 1 FROM browse_sessions s WHERE s.id=NEW.session_id
    AND s.status IN ('completed','closed_partial','invalidated')
)
BEGIN
    SELECT RAISE(ABORT, 'closed browse session is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_browse_closed_inventory_insert
BEFORE INSERT ON browse_inventory_units
WHEN EXISTS (
    SELECT 1 FROM browse_sessions s WHERE s.id=NEW.session_id
    AND s.status IN ('completed','closed_partial','invalidated')
)
BEGIN
    SELECT RAISE(ABORT, 'closed browse session is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_browse_closed_range_update
BEFORE UPDATE ON browse_ranges
WHEN EXISTS (
    SELECT 1 FROM browse_sessions s WHERE s.id=OLD.session_id
    AND s.status IN ('completed','closed_partial','invalidated')
)
BEGIN SELECT RAISE(ABORT, 'closed browse session is immutable'); END;

CREATE TRIGGER IF NOT EXISTS trg_browse_closed_finding_update
BEFORE UPDATE ON browse_findings
WHEN EXISTS (
    SELECT 1 FROM browse_sessions s WHERE s.id=OLD.session_id
    AND s.status IN ('completed','closed_partial','invalidated')
)
BEGIN SELECT RAISE(ABORT, 'closed browse session is immutable'); END;

CREATE TRIGGER IF NOT EXISTS trg_browse_closed_issue_update
BEFORE UPDATE ON browse_issues
WHEN EXISTS (
    SELECT 1 FROM browse_sessions s WHERE s.id=OLD.session_id
    AND s.status IN ('completed','closed_partial','invalidated')
)
BEGIN SELECT RAISE(ABORT, 'closed browse session is immutable'); END;

CREATE TRIGGER IF NOT EXISTS trg_browse_closed_trigger_update
BEFORE UPDATE ON browse_revisit_triggers
WHEN EXISTS (
    SELECT 1 FROM browse_sessions s WHERE s.id=OLD.session_id
    AND s.status IN ('completed','closed_partial','invalidated')
)
BEGIN SELECT RAISE(ABORT, 'closed browse session is immutable'); END;

CREATE TRIGGER IF NOT EXISTS trg_browse_closed_assessment_insert
BEFORE INSERT ON browse_issue_assessments
WHEN EXISTS (
    SELECT 1 FROM browse_issues i JOIN browse_sessions s ON s.id=i.session_id
    WHERE i.id=NEW.issue_id
      AND s.status IN ('completed','closed_partial','invalidated')
)
BEGIN SELECT RAISE(ABORT, 'closed browse session is immutable'); END;
"""


HANDWRITING_PROFILE_SOURCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS handwriting_profile_sources (
    profile_id TEXT NOT NULL REFERENCES handwriting_profiles(id) ON DELETE CASCADE,
    source_item_id TEXT NOT NULL REFERENCES source_items(id),
    scope_role TEXT NOT NULL CHECK(scope_role IN ('anchor','sample','context')),
    status TEXT NOT NULL CHECK(status IN ('candidate','accepted','excluded')),
    rationale TEXT NOT NULL,
    linked_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(profile_id, source_item_id)
);
CREATE INDEX IF NOT EXISTS idx_handwriting_profile_sources_source
    ON handwriting_profile_sources(source_item_id, status, scope_role);

INSERT INTO handwriting_profile_sources(
    profile_id, source_item_id, scope_role, status, rationale, linked_by,
    created_at, updated_at
)
SELECT id, source_item_id, 'anchor', 'accepted',
       'Automatycznie zachowany akt kotwiczący profilu podczas migracji v12.',
       'migration-v12', created_at, updated_at
FROM handwriting_profiles
WHERE 1
ON CONFLICT(profile_id, source_item_id) DO NOTHING;
"""


EXTERNAL_SYNC_SCHEMA = """
CREATE TABLE IF NOT EXISTS external_sync_items (
    id TEXT PRIMARY KEY,
    service TEXT NOT NULL,
    object_type TEXT NOT NULL CHECK(object_type IN (
        'event', 'claim', 'person', 'source_item'
    )),
    object_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN (
        'pending', 'staged', 'published', 'needs_review', 'failed', 'skipped'
    )),
    local_fingerprint TEXT NOT NULL,
    published_fingerprint TEXT,
    remote_object_id TEXT,
    remote_url TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_checked_at TEXT,
    last_published_at TEXT,
    UNIQUE(service, object_type, object_id)
);
CREATE INDEX IF NOT EXISTS idx_external_sync_service_status
    ON external_sync_items(service, status);
CREATE INDEX IF NOT EXISTS idx_external_sync_object
    ON external_sync_items(object_type, object_id);
"""


BEST_AVAILABLE_RENDITION_SCHEMA = """
ALTER TABLE browse_sessions
ADD COLUMN rendition_gate_required INTEGER NOT NULL DEFAULT 0
CHECK(rendition_gate_required IN (0,1));

CREATE TABLE IF NOT EXISTS browse_renditions (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES browse_sessions(id),
    ordinal INTEGER CHECK(ordinal IS NULL OR ordinal >= 1),
    locator TEXT NOT NULL,
    rendition_class TEXT NOT NULL CHECK(rendition_class IN (
        'thumbnail','viewer','medium','max','original','download','iiif','pdf','other'
    )),
    natural_width_px INTEGER CHECK(natural_width_px IS NULL OR natural_width_px > 0),
    natural_height_px INTEGER CHECK(natural_height_px IS NULL OR natural_height_px > 0),
    byte_size INTEGER CHECK(byte_size IS NULL OR byte_size >= 0),
    media_type TEXT,
    content_sha256 TEXT,
    acquisition_method TEXT NOT NULL CHECK(acquisition_method IN (
        'browser_view','repository_download','iiif','api','pdf','local_file','other'
    )),
    access_status TEXT NOT NULL CHECK(access_status IN (
        'available','restricted','blocked','missing'
    )),
    information_quality TEXT NOT NULL CHECK(information_quality IN (
        'q0_unusable','q1_poor','q2_usable','q3_good','q4_excellent'
    )),
    is_upscaled INTEGER CHECK(is_upscaled IS NULL OR is_upscaled IN (0,1)),
    legal_access_checked INTEGER NOT NULL DEFAULT 0 CHECK(legal_access_checked IN (0,1)),
    notes TEXT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(session_id, ordinal, locator)
);

CREATE TABLE IF NOT EXISTS browse_rendition_gate_reviews (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES browse_sessions(id),
    selected_rendition_id TEXT REFERENCES browse_renditions(id),
    status TEXT NOT NULL CHECK(status IN ('passed','provisional','blocked')),
    representative_ordinal INTEGER CHECK(representative_ordinal IS NULL OR representative_ordinal >= 1),
    comparison_json TEXT NOT NULL DEFAULT '{}',
    rationale TEXT NOT NULL,
    reviewed_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK(status = 'blocked' OR selected_rendition_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_browse_renditions_session
ON browse_renditions(session_id, ordinal, information_quality, created_at);

CREATE INDEX IF NOT EXISTS idx_browse_rendition_reviews_session
ON browse_rendition_gate_reviews(session_id, created_at, id);

CREATE TRIGGER IF NOT EXISTS browse_renditions_no_update
BEFORE UPDATE ON browse_renditions
BEGIN
    SELECT RAISE(ABORT, 'browse renditions are append-only');
END;

CREATE TRIGGER IF NOT EXISTS browse_renditions_no_delete
BEFORE DELETE ON browse_renditions
BEGIN
    SELECT RAISE(ABORT, 'browse renditions are append-only');
END;

CREATE TRIGGER IF NOT EXISTS browse_rendition_reviews_no_update
BEFORE UPDATE ON browse_rendition_gate_reviews
BEGIN
    SELECT RAISE(ABORT, 'browse rendition gate reviews are append-only');
END;

CREATE TRIGGER IF NOT EXISTS browse_rendition_reviews_no_delete
BEFORE DELETE ON browse_rendition_gate_reviews
BEGIN
    SELECT RAISE(ABORT, 'browse rendition gate reviews are append-only');
END;

CREATE TRIGGER IF NOT EXISTS browse_rendition_review_selected_session
BEFORE INSERT ON browse_rendition_gate_reviews
WHEN NEW.selected_rendition_id IS NOT NULL
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM browse_renditions r
        WHERE r.id = NEW.selected_rendition_id AND r.session_id = NEW.session_id
    ) THEN RAISE(ABORT, 'selected rendition must belong to browse session') END;
END;
"""


AGENT_FIRST_READING_REVIEW_SCHEMA = """
CREATE TABLE IF NOT EXISTS reading_review_decisions (
    id TEXT PRIMARY KEY,
    source_item_id TEXT NOT NULL REFERENCES source_items(id),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    supersedes_decision_id TEXT REFERENCES reading_review_decisions(id),
    status TEXT NOT NULL CHECK(status IN (
        'agent_single_pass','agent_consensus','agent_adjudicated',
        'human_review_required','human_reviewed'
    )),
    decided_by TEXT NOT NULL CHECK(trim(decided_by) <> ''),
    decided_by_kind TEXT NOT NULL CHECK(decided_by_kind IN ('agent','human')),
    decided_by_run_id TEXT,
    rationale TEXT NOT NULL CHECK(trim(rationale) <> ''),
    decision_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(source_item_id, revision),
    CHECK(status <> 'human_reviewed' OR decided_by_kind = 'human'),
    CHECK(
        (revision = 1 AND supersedes_decision_id IS NULL) OR
        (revision > 1 AND supersedes_decision_id IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS reading_review_inputs (
    id TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL REFERENCES reading_review_decisions(id),
    reading_version_id TEXT NOT NULL REFERENCES reading_versions(id),
    input_role TEXT NOT NULL CHECK(input_role IN (
        'blind_primary','blind_secondary','contextual',
        'agent_adjudicator','human_review'
    )),
    reader_id TEXT,
    run_id TEXT,
    context_disclosed INTEGER CHECK(context_disclosed IS NULL OR context_disclosed IN (0,1)),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(decision_id, input_role),
    UNIQUE(decision_id, reading_version_id)
);

CREATE INDEX IF NOT EXISTS idx_reading_review_decisions_source
ON reading_review_decisions(source_item_id, revision DESC);

CREATE INDEX IF NOT EXISTS idx_reading_review_decisions_status
ON reading_review_decisions(status, created_at);

CREATE INDEX IF NOT EXISTS idx_reading_review_inputs_version
ON reading_review_inputs(reading_version_id, decision_id);

CREATE TRIGGER IF NOT EXISTS reading_review_decisions_no_update
BEFORE UPDATE ON reading_review_decisions
BEGIN
    SELECT RAISE(ABORT, 'reading review decisions are append-only');
END;

CREATE TRIGGER IF NOT EXISTS reading_review_decisions_no_delete
BEFORE DELETE ON reading_review_decisions
BEGIN
    SELECT RAISE(ABORT, 'reading review decisions are append-only');
END;

CREATE TRIGGER IF NOT EXISTS reading_review_decisions_revision_chain
BEFORE INSERT ON reading_review_decisions
BEGIN
    SELECT CASE WHEN NEW.revision = 1 AND EXISTS (
        SELECT 1 FROM reading_review_decisions prior
        WHERE prior.source_item_id = NEW.source_item_id
    ) THEN RAISE(ABORT, 'reading review revision chain mismatch') END;
    SELECT CASE WHEN NEW.revision > 1 AND NOT EXISTS (
        SELECT 1 FROM reading_review_decisions prior
        WHERE prior.id = NEW.supersedes_decision_id
          AND prior.source_item_id = NEW.source_item_id
          AND prior.revision = NEW.revision - 1
    ) THEN RAISE(ABORT, 'reading review revision chain mismatch') END;
END;

CREATE TRIGGER IF NOT EXISTS reading_review_inputs_no_update
BEFORE UPDATE ON reading_review_inputs
BEGIN
    SELECT RAISE(ABORT, 'reading review inputs are append-only');
END;

CREATE TRIGGER IF NOT EXISTS reading_review_inputs_no_delete
BEFORE DELETE ON reading_review_inputs
BEGIN
    SELECT RAISE(ABORT, 'reading review inputs are append-only');
END;

CREATE TRIGGER IF NOT EXISTS reading_review_inputs_same_source
BEFORE INSERT ON reading_review_inputs
WHEN (
    SELECT r.source_item_id
    FROM reading_versions rv
    JOIN readings r ON r.id = rv.reading_id
    WHERE rv.id = NEW.reading_version_id
) <> (
    SELECT d.source_item_id
    FROM reading_review_decisions d
    WHERE d.id = NEW.decision_id
)
BEGIN
    SELECT RAISE(ABORT, 'reading review input source mismatch');
END;
"""


REASSESSMENT_BATCH_SCHEMA = """
CREATE TABLE IF NOT EXISTS reassessment_batches (
    id TEXT PRIMARY KEY,
    batch_key TEXT NOT NULL CHECK(trim(batch_key) <> ''),
    case_id TEXT REFERENCES research_cases(id),
    question_id TEXT REFERENCES research_questions(id),
    input_fingerprint TEXT NOT NULL CHECK(trim(input_fingerprint) <> ''),
    scope_json TEXT NOT NULL DEFAULT '{}',
    stopping_rule TEXT NOT NULL CHECK(trim(stopping_rule) <> ''),
    supersedes_batch_id TEXT REFERENCES reassessment_batches(id),
    created_by TEXT NOT NULL CHECK(trim(created_by) <> ''),
    created_at TEXT NOT NULL,
    UNIQUE(batch_key, input_fingerprint)
);

CREATE TABLE IF NOT EXISTS reassessment_batch_states (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES reassessment_batches(id),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    supersedes_state_id TEXT REFERENCES reassessment_batch_states(id),
    status TEXT NOT NULL CHECK(status IN (
        'planned','in_review','waiting','completed','inconclusive','superseded'
    )),
    rationale TEXT NOT NULL CHECK(trim(rationale) <> ''),
    created_by TEXT NOT NULL CHECK(trim(created_by) <> ''),
    created_at TEXT NOT NULL,
    UNIQUE(batch_id, revision),
    CHECK(
        (revision = 1 AND supersedes_state_id IS NULL) OR
        (revision > 1 AND supersedes_state_id IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS reassessment_triggers (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES reassessment_batches(id),
    trigger_type TEXT NOT NULL CHECK(trim(trigger_type) <> ''),
    object_type TEXT NOT NULL CHECK(trim(object_type) <> ''),
    object_id TEXT NOT NULL CHECK(trim(object_id) <> ''),
    source_item_id TEXT REFERENCES source_items(id),
    priority INTEGER NOT NULL DEFAULT 0 CHECK(priority >= 0),
    rationale TEXT NOT NULL CHECK(trim(rationale) <> ''),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(batch_id, trigger_type, object_type, object_id)
);

CREATE TABLE IF NOT EXISTS reassessment_items (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES reassessment_batches(id),
    item_type TEXT NOT NULL CHECK(trim(item_type) <> ''),
    object_type TEXT NOT NULL CHECK(trim(object_type) <> ''),
    object_id TEXT NOT NULL CHECK(trim(object_id) <> ''),
    source_item_id TEXT REFERENCES source_items(id),
    inclusion_reason TEXT NOT NULL CHECK(trim(inclusion_reason) <> ''),
    risk_level TEXT NOT NULL CHECK(risk_level IN ('R0','R1','R2','R3','R4')),
    cost_class TEXT NOT NULL CHECK(cost_class IN ('A','B','C','D')),
    input_fingerprint TEXT NOT NULL CHECK(trim(input_fingerprint) <> ''),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(batch_id, object_type, object_id, input_fingerprint)
);

CREATE TABLE IF NOT EXISTS reassessment_item_results (
    id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL REFERENCES reassessment_items(id),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    supersedes_result_id TEXT REFERENCES reassessment_item_results(id),
    status TEXT NOT NULL CHECK(status IN (
        'pending','in_review','completed','inconclusive','blocked','deferred','not_affected'
    )),
    result_json TEXT NOT NULL DEFAULT '{}',
    rationale TEXT NOT NULL CHECK(trim(rationale) <> ''),
    created_by TEXT NOT NULL CHECK(trim(created_by) <> ''),
    created_at TEXT NOT NULL,
    UNIQUE(item_id, revision),
    CHECK(
        (revision = 1 AND supersedes_result_id IS NULL) OR
        (revision > 1 AND supersedes_result_id IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS reassessment_impacts (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES reassessment_batches(id),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    supersedes_impact_id TEXT REFERENCES reassessment_impacts(id),
    object_type TEXT NOT NULL CHECK(trim(object_type) <> ''),
    object_id TEXT NOT NULL CHECK(trim(object_id) <> ''),
    impact_type TEXT NOT NULL CHECK(trim(impact_type) <> ''),
    planned_action TEXT NOT NULL CHECK(trim(planned_action) <> ''),
    writeback_status TEXT NOT NULL CHECK(writeback_status IN (
        'planned','ready','applied','deferred','not_affected','blocked','conflict'
    )),
    preview_json TEXT NOT NULL DEFAULT '{}',
    rationale TEXT NOT NULL CHECK(trim(rationale) <> ''),
    created_by TEXT NOT NULL CHECK(trim(created_by) <> ''),
    created_at TEXT NOT NULL,
    UNIQUE(batch_id, object_type, object_id, impact_type, revision),
    CHECK(
        (revision = 1 AND supersedes_impact_id IS NULL) OR
        (revision > 1 AND supersedes_impact_id IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS reassessment_reviews (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES reassessment_batches(id),
    review_kind TEXT NOT NULL CHECK(trim(review_kind) <> ''),
    reviewer TEXT NOT NULL CHECK(trim(reviewer) <> ''),
    reviewer_kind TEXT NOT NULL CHECK(reviewer_kind IN ('agent','human')),
    decision TEXT NOT NULL CHECK(decision IN ('approved','changes_required','inconclusive')),
    notes TEXT NOT NULL CHECK(trim(notes) <> ''),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS document_extraction_states (
    id TEXT PRIMARY KEY,
    source_item_id TEXT NOT NULL REFERENCES source_items(id),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    supersedes_state_id TEXT REFERENCES document_extraction_states(id),
    batch_id TEXT REFERENCES reassessment_batches(id),
    reading_review_decision_id TEXT REFERENCES reading_review_decisions(id),
    status TEXT NOT NULL CHECK(status IN (
        'not_started','target_only','partial','all_named_people_extracted','reviewed','blocked'
    )),
    source_fingerprint TEXT NOT NULL CHECK(trim(source_fingerprint) <> ''),
    extraction_fingerprint TEXT NOT NULL CHECK(trim(extraction_fingerprint) <> ''),
    rationale TEXT NOT NULL CHECK(trim(rationale) <> ''),
    created_by TEXT NOT NULL CHECK(trim(created_by) <> ''),
    created_at TEXT NOT NULL,
    UNIQUE(source_item_id, revision),
    CHECK(
        (revision = 1 AND supersedes_state_id IS NULL) OR
        (revision > 1 AND supersedes_state_id IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS mapping_writeback_manifests (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES reassessment_batches(id),
    source_item_id TEXT NOT NULL REFERENCES source_items(id),
    schema_version INTEGER NOT NULL CHECK(schema_version >= 1),
    manifest_fingerprint TEXT NOT NULL UNIQUE CHECK(trim(manifest_fingerprint) <> ''),
    input_fingerprint TEXT NOT NULL CHECK(trim(input_fingerprint) <> ''),
    expected_database_schema INTEGER NOT NULL CHECK(expected_database_schema >= 1),
    expected_review_revision INTEGER CHECK(expected_review_revision IS NULL OR expected_review_revision >= 1),
    manifest_json TEXT NOT NULL,
    created_by TEXT NOT NULL CHECK(trim(created_by) <> ''),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mapping_writeback_runs (
    id TEXT PRIMARY KEY,
    manifest_id TEXT NOT NULL REFERENCES mapping_writeback_manifests(id),
    mode TEXT NOT NULL CHECK(mode IN ('preview','apply')),
    status TEXT NOT NULL CHECK(status IN (
        'ready','no_op','applied','conflict','failed'
    )),
    database_fingerprint TEXT NOT NULL CHECK(trim(database_fingerprint) <> ''),
    diff_json TEXT NOT NULL DEFAULT '{}',
    error_text TEXT,
    actor TEXT NOT NULL CHECK(trim(actor) <> ''),
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reassessment_batches_key
ON reassessment_batches(batch_key, created_at);
CREATE INDEX IF NOT EXISTS idx_reassessment_batch_states_latest
ON reassessment_batch_states(batch_id, revision DESC);
CREATE INDEX IF NOT EXISTS idx_reassessment_items_batch
ON reassessment_items(batch_id, risk_level, cost_class);
CREATE INDEX IF NOT EXISTS idx_reassessment_impacts_batch
ON reassessment_impacts(batch_id, writeback_status, revision DESC);
CREATE INDEX IF NOT EXISTS idx_document_extraction_source
ON document_extraction_states(source_item_id, revision DESC);
CREATE INDEX IF NOT EXISTS idx_mapping_manifests_batch
ON mapping_writeback_manifests(batch_id, source_item_id);

CREATE TRIGGER IF NOT EXISTS reassessment_batch_states_revision_chain
BEFORE INSERT ON reassessment_batch_states
BEGIN
    SELECT CASE WHEN NEW.revision = 1 AND EXISTS (
        SELECT 1 FROM reassessment_batch_states prior WHERE prior.batch_id = NEW.batch_id
    ) THEN RAISE(ABORT, 'reassessment batch state revision chain mismatch') END;
    SELECT CASE WHEN NEW.revision > 1 AND NOT EXISTS (
        SELECT 1 FROM reassessment_batch_states prior
        WHERE prior.id = NEW.supersedes_state_id
          AND prior.batch_id = NEW.batch_id
          AND prior.revision = NEW.revision - 1
    ) THEN RAISE(ABORT, 'reassessment batch state revision chain mismatch') END;
END;

CREATE TRIGGER IF NOT EXISTS reassessment_item_results_revision_chain
BEFORE INSERT ON reassessment_item_results
BEGIN
    SELECT CASE WHEN NEW.revision = 1 AND EXISTS (
        SELECT 1 FROM reassessment_item_results prior WHERE prior.item_id = NEW.item_id
    ) THEN RAISE(ABORT, 'reassessment item result revision chain mismatch') END;
    SELECT CASE WHEN NEW.revision > 1 AND NOT EXISTS (
        SELECT 1 FROM reassessment_item_results prior
        WHERE prior.id = NEW.supersedes_result_id
          AND prior.item_id = NEW.item_id
          AND prior.revision = NEW.revision - 1
    ) THEN RAISE(ABORT, 'reassessment item result revision chain mismatch') END;
END;

CREATE TRIGGER IF NOT EXISTS reassessment_impacts_revision_chain
BEFORE INSERT ON reassessment_impacts
BEGIN
    SELECT CASE WHEN NEW.revision = 1 AND EXISTS (
        SELECT 1 FROM reassessment_impacts prior
        WHERE prior.batch_id = NEW.batch_id
          AND prior.object_type = NEW.object_type
          AND prior.object_id = NEW.object_id
          AND prior.impact_type = NEW.impact_type
    ) THEN RAISE(ABORT, 'reassessment impact revision chain mismatch') END;
    SELECT CASE WHEN NEW.revision > 1 AND NOT EXISTS (
        SELECT 1 FROM reassessment_impacts prior
        WHERE prior.id = NEW.supersedes_impact_id
          AND prior.batch_id = NEW.batch_id
          AND prior.object_type = NEW.object_type
          AND prior.object_id = NEW.object_id
          AND prior.impact_type = NEW.impact_type
          AND prior.revision = NEW.revision - 1
    ) THEN RAISE(ABORT, 'reassessment impact revision chain mismatch') END;
END;

CREATE TRIGGER IF NOT EXISTS document_extraction_states_revision_chain
BEFORE INSERT ON document_extraction_states
BEGIN
    SELECT CASE WHEN NEW.revision = 1 AND EXISTS (
        SELECT 1 FROM document_extraction_states prior
        WHERE prior.source_item_id = NEW.source_item_id
    ) THEN RAISE(ABORT, 'document extraction state revision chain mismatch') END;
    SELECT CASE WHEN NEW.revision > 1 AND NOT EXISTS (
        SELECT 1 FROM document_extraction_states prior
        WHERE prior.id = NEW.supersedes_state_id
          AND prior.source_item_id = NEW.source_item_id
          AND prior.revision = NEW.revision - 1
    ) THEN RAISE(ABORT, 'document extraction state revision chain mismatch') END;
END;

CREATE TRIGGER IF NOT EXISTS document_extraction_review_same_source
BEFORE INSERT ON document_extraction_states
WHEN NEW.reading_review_decision_id IS NOT NULL
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM reading_review_decisions d
        WHERE d.id = NEW.reading_review_decision_id
          AND d.source_item_id = NEW.source_item_id
    ) THEN RAISE(ABORT, 'document extraction review source mismatch') END;
END;

CREATE TRIGGER IF NOT EXISTS reassessment_batches_no_update
BEFORE UPDATE ON reassessment_batches BEGIN
    SELECT RAISE(ABORT, 'reassessment batches are append-only');
END;
CREATE TRIGGER IF NOT EXISTS reassessment_batches_no_delete
BEFORE DELETE ON reassessment_batches BEGIN
    SELECT RAISE(ABORT, 'reassessment batches are append-only');
END;
CREATE TRIGGER IF NOT EXISTS reassessment_batch_states_no_update
BEFORE UPDATE ON reassessment_batch_states BEGIN
    SELECT RAISE(ABORT, 'reassessment batch states are append-only');
END;
CREATE TRIGGER IF NOT EXISTS reassessment_batch_states_no_delete
BEFORE DELETE ON reassessment_batch_states BEGIN
    SELECT RAISE(ABORT, 'reassessment batch states are append-only');
END;
CREATE TRIGGER IF NOT EXISTS reassessment_triggers_no_update
BEFORE UPDATE ON reassessment_triggers BEGIN
    SELECT RAISE(ABORT, 'reassessment triggers are append-only');
END;
CREATE TRIGGER IF NOT EXISTS reassessment_triggers_no_delete
BEFORE DELETE ON reassessment_triggers BEGIN
    SELECT RAISE(ABORT, 'reassessment triggers are append-only');
END;
CREATE TRIGGER IF NOT EXISTS reassessment_items_no_update
BEFORE UPDATE ON reassessment_items BEGIN
    SELECT RAISE(ABORT, 'reassessment items are append-only');
END;
CREATE TRIGGER IF NOT EXISTS reassessment_items_no_delete
BEFORE DELETE ON reassessment_items BEGIN
    SELECT RAISE(ABORT, 'reassessment items are append-only');
END;
CREATE TRIGGER IF NOT EXISTS reassessment_item_results_no_update
BEFORE UPDATE ON reassessment_item_results BEGIN
    SELECT RAISE(ABORT, 'reassessment item results are append-only');
END;
CREATE TRIGGER IF NOT EXISTS reassessment_item_results_no_delete
BEFORE DELETE ON reassessment_item_results BEGIN
    SELECT RAISE(ABORT, 'reassessment item results are append-only');
END;
CREATE TRIGGER IF NOT EXISTS reassessment_impacts_no_update
BEFORE UPDATE ON reassessment_impacts BEGIN
    SELECT RAISE(ABORT, 'reassessment impacts are append-only');
END;
CREATE TRIGGER IF NOT EXISTS reassessment_impacts_no_delete
BEFORE DELETE ON reassessment_impacts BEGIN
    SELECT RAISE(ABORT, 'reassessment impacts are append-only');
END;
CREATE TRIGGER IF NOT EXISTS reassessment_reviews_no_update
BEFORE UPDATE ON reassessment_reviews BEGIN
    SELECT RAISE(ABORT, 'reassessment reviews are append-only');
END;
CREATE TRIGGER IF NOT EXISTS reassessment_reviews_no_delete
BEFORE DELETE ON reassessment_reviews BEGIN
    SELECT RAISE(ABORT, 'reassessment reviews are append-only');
END;
CREATE TRIGGER IF NOT EXISTS document_extraction_states_no_update
BEFORE UPDATE ON document_extraction_states BEGIN
    SELECT RAISE(ABORT, 'document extraction states are append-only');
END;
CREATE TRIGGER IF NOT EXISTS document_extraction_states_no_delete
BEFORE DELETE ON document_extraction_states BEGIN
    SELECT RAISE(ABORT, 'document extraction states are append-only');
END;
CREATE TRIGGER IF NOT EXISTS mapping_writeback_manifests_no_update
BEFORE UPDATE ON mapping_writeback_manifests BEGIN
    SELECT RAISE(ABORT, 'mapping writeback manifests are append-only');
END;
CREATE TRIGGER IF NOT EXISTS mapping_writeback_manifests_no_delete
BEFORE DELETE ON mapping_writeback_manifests BEGIN
    SELECT RAISE(ABORT, 'mapping writeback manifests are append-only');
END;
CREATE TRIGGER IF NOT EXISTS mapping_writeback_runs_no_update
BEFORE UPDATE ON mapping_writeback_runs BEGIN
    SELECT RAISE(ABORT, 'mapping writeback runs are append-only');
END;
CREATE TRIGGER IF NOT EXISTS mapping_writeback_runs_no_delete
BEFORE DELETE ON mapping_writeback_runs BEGIN
    SELECT RAISE(ABORT, 'mapping writeback runs are append-only');
END;
"""


PRESENTATION_EXTERNAL_SYNC_SCHEMA = """
ALTER TABLE external_sync_items RENAME TO external_sync_items_v9;
DROP INDEX idx_external_sync_service_status;
DROP INDEX idx_external_sync_object;

CREATE TABLE external_sync_items (
    id TEXT PRIMARY KEY,
    service TEXT NOT NULL,
    object_type TEXT NOT NULL CHECK(object_type IN (
        'event', 'claim', 'person', 'source_item',
        'presentation_person', 'presentation_family', 'presentation_union'
    )),
    object_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN (
        'pending', 'staged', 'published', 'needs_review', 'failed', 'skipped'
    )),
    local_fingerprint TEXT NOT NULL,
    published_fingerprint TEXT,
    remote_object_id TEXT,
    remote_url TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_checked_at TEXT,
    last_published_at TEXT,
    UNIQUE(service, object_type, object_id)
);
INSERT INTO external_sync_items (
    id, service, object_type, object_id, status, local_fingerprint,
    published_fingerprint, remote_object_id, remote_url, notes,
    created_at, updated_at, last_checked_at, last_published_at
)
SELECT
    id, service, object_type, object_id, status, local_fingerprint,
    published_fingerprint, remote_object_id, remote_url, notes,
    created_at, updated_at, last_checked_at, last_published_at
FROM external_sync_items_v9;
DROP TABLE external_sync_items_v9;

CREATE INDEX idx_external_sync_service_status
    ON external_sync_items(service, status);
CREATE INDEX idx_external_sync_object
    ON external_sync_items(object_type, object_id);
"""


PRIVATE_DATA_SCHEMA = """
ALTER TABLE privacy_labels RENAME TO privacy_labels_v10;
DROP INDEX IF EXISTS idx_privacy_labels_object;
CREATE TABLE privacy_labels (
    id TEXT PRIMARY KEY,
    object_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    classification TEXT NOT NULL CHECK(classification IN (
        'public_historical','deceased','living','restricted',
        'family_private','strict_private'
    )),
    rationale TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(object_type, object_id)
);
INSERT INTO privacy_labels(
    id,object_type,object_id,classification,rationale,created_at,updated_at
) SELECT
    id,object_type,object_id,classification,rationale,created_at,updated_at
  FROM privacy_labels_v10;
DROP TABLE privacy_labels_v10;
CREATE INDEX idx_privacy_labels_object
    ON privacy_labels(object_type, object_id, classification);

CREATE TABLE private_fields (
    id TEXT PRIMARY KEY,
    object_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    value_json TEXT NOT NULL,
    classification TEXT NOT NULL CHECK(classification IN (
        'family_private','strict_private'
    )),
    provenance_kind TEXT NOT NULL CHECK(provenance_kind IN (
        'family_testimony','derived_record','primary_document','imported_unverified'
    )),
    informant TEXT,
    consent_status TEXT NOT NULL DEFAULT 'not_asked' CHECK(consent_status IN (
        'not_asked','granted','denied','withdrawn','not_applicable'
    )),
    retention_until TEXT,
    review_after TEXT,
    source_assertion_id TEXT REFERENCES source_assertions(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(object_type, object_id, field_name)
);
CREATE INDEX idx_private_fields_object
    ON private_fields(object_type, object_id, classification);

CREATE TABLE family_notes (
    id TEXT PRIMARY KEY,
    subject_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    note_text TEXT NOT NULL,
    classification TEXT NOT NULL CHECK(classification IN (
        'family_private','strict_private'
    )),
    informant TEXT NOT NULL,
    consent_status TEXT NOT NULL DEFAULT 'not_asked' CHECK(consent_status IN (
        'not_asked','granted','denied','withdrawn','not_applicable'
    )),
    recorded_at TEXT NOT NULL,
    review_after TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_family_notes_subject
    ON family_notes(subject_type, subject_id, classification);

CREATE TABLE private_relationships (
    id TEXT PRIMARY KEY,
    subject_person_id TEXT NOT NULL REFERENCES persons(id),
    object_person_id TEXT NOT NULL REFERENCES persons(id),
    relationship_type TEXT NOT NULL CHECK(relationship_type IN (
        'parent','child','spouse','partner','sibling','guardian','other'
    )),
    date_from TEXT,
    date_to TEXT,
    classification TEXT NOT NULL CHECK(classification IN (
        'family_private','strict_private'
    )),
    provenance_kind TEXT NOT NULL CHECK(provenance_kind IN (
        'family_testimony','derived_record','primary_document','imported_unverified'
    )),
    verification_status TEXT NOT NULL DEFAULT 'family_testimony' CHECK(
        verification_status IN (
            'family_testimony','imported_unverified','Possible','Probable','Confirmed','Rejected'
        )
    ),
    rationale TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(subject_person_id, object_person_id, relationship_type)
);
CREATE INDEX idx_private_relationships_subject
    ON private_relationships(subject_person_id, relationship_type);
CREATE INDEX idx_private_relationships_object
    ON private_relationships(object_person_id, relationship_type);

CREATE TABLE private_attachments (
    id TEXT PRIMARY KEY,
    object_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    encrypted_path TEXT NOT NULL UNIQUE,
    media_type TEXT NOT NULL,
    original_size INTEGER NOT NULL CHECK(original_size >= 0),
    plaintext_sha256 TEXT NOT NULL,
    ciphertext_sha256 TEXT NOT NULL,
    classification TEXT NOT NULL CHECK(classification IN (
        'family_private','strict_private'
    )),
    source_name_hint TEXT,
    created_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_private_attachments_object
    ON private_attachments(object_type, object_id, classification);

CREATE TABLE private_access_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    action TEXT NOT NULL,
    object_type TEXT,
    object_id TEXT,
    session_fingerprint TEXT,
    result TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_private_access_audit_time
    ON private_access_audit(occurred_at, action, result);
"""


HIERARCHICAL_RESEARCH_SCOPE_SCHEMA = """
CREATE TABLE research_scopes (
    id TEXT PRIMARY KEY,
    scope_type TEXT NOT NULL CHECK(scope_type IN (
        'program','lineage','branch','family_unit','place_study',
        'theme','legacy_case','other'
    )),
    title TEXT NOT NULL CHECK(trim(title) <> ''),
    summary TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN (
        'planned','active','on_hold','completed','closed'
    )),
    legacy_case_id TEXT UNIQUE REFERENCES research_cases(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX idx_research_scopes_type_status
    ON research_scopes(scope_type, status, title, id);

CREATE TABLE research_scope_edges (
    id TEXT PRIMARY KEY,
    parent_scope_id TEXT NOT NULL REFERENCES research_scopes(id) ON DELETE CASCADE,
    child_scope_id TEXT NOT NULL REFERENCES research_scopes(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL CHECK(relation_type IN (
        'contains','related','continues','overlaps'
    )),
    is_primary INTEGER NOT NULL DEFAULT 0 CHECK(is_primary IN (0,1)),
    sort_order INTEGER NOT NULL DEFAULT 100 CHECK(sort_order >= 0),
    notes TEXT,
    created_at TEXT NOT NULL,
    CHECK(parent_scope_id <> child_scope_id),
    UNIQUE(parent_scope_id, child_scope_id, relation_type)
);
CREATE INDEX idx_research_scope_edges_parent
    ON research_scope_edges(parent_scope_id, relation_type, sort_order, child_scope_id);
CREATE INDEX idx_research_scope_edges_child
    ON research_scope_edges(child_scope_id, relation_type, is_primary, parent_scope_id);
CREATE UNIQUE INDEX idx_research_scope_one_primary_parent
    ON research_scope_edges(child_scope_id)
    WHERE relation_type='contains' AND is_primary=1;

CREATE TRIGGER research_scope_contains_cycle_guard
BEFORE INSERT ON research_scope_edges
WHEN NEW.relation_type='contains'
BEGIN
    SELECT CASE WHEN EXISTS (
        WITH RECURSIVE descendants(id) AS (
            SELECT child_scope_id
              FROM research_scope_edges
             WHERE parent_scope_id=NEW.child_scope_id
               AND relation_type='contains'
            UNION
            SELECT edge.child_scope_id
              FROM research_scope_edges edge
              JOIN descendants current ON edge.parent_scope_id=current.id
             WHERE edge.relation_type='contains'
        )
        SELECT 1 FROM descendants WHERE id=NEW.parent_scope_id
    ) THEN RAISE(ABORT, 'research scope contains cycle') END;
END;

CREATE TRIGGER research_scope_contains_cycle_guard_update
BEFORE UPDATE OF parent_scope_id,child_scope_id,relation_type ON research_scope_edges
WHEN NEW.relation_type='contains'
BEGIN
    SELECT CASE WHEN NEW.parent_scope_id=NEW.child_scope_id OR EXISTS (
        WITH RECURSIVE descendants(id) AS (
            SELECT child_scope_id
              FROM research_scope_edges
             WHERE parent_scope_id=NEW.child_scope_id
               AND relation_type='contains'
               AND id<>OLD.id
            UNION
            SELECT edge.child_scope_id
              FROM research_scope_edges edge
              JOIN descendants current ON edge.parent_scope_id=current.id
             WHERE edge.relation_type='contains'
               AND edge.id<>OLD.id
        )
        SELECT 1 FROM descendants WHERE id=NEW.parent_scope_id
    ) THEN RAISE(ABORT, 'research scope contains cycle') END;
END;

CREATE TABLE research_scope_targets (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL REFERENCES research_scopes(id) ON DELETE CASCADE,
    target_type TEXT NOT NULL CHECK(target_type IN (
        'person','person_occurrence','named_person','household','place',
        'place_study','family_cluster','event','source_item','other'
    )),
    target_id TEXT NOT NULL CHECK(trim(target_id) <> ''),
    target_label TEXT NOT NULL CHECK(trim(target_label) <> ''),
    association_role TEXT NOT NULL CHECK(trim(association_role) <> ''),
    semantics TEXT NOT NULL DEFAULT 'research_context' CHECK(semantics IN (
        'research_context','source_recorded','hypothesis'
    )),
    date_from TEXT,
    date_to TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(scope_id, target_type, target_id, association_role)
);
CREATE INDEX idx_research_scope_targets_object
    ON research_scope_targets(target_type, target_id, scope_id);
CREATE INDEX idx_research_scope_targets_scope
    ON research_scope_targets(scope_id, target_type, target_label, target_id);

CREATE TABLE research_question_scopes (
    id TEXT PRIMARY KEY,
    question_id TEXT NOT NULL REFERENCES research_questions(id) ON DELETE CASCADE,
    scope_id TEXT NOT NULL REFERENCES research_scopes(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'supporting' CHECK(role IN (
        'primary','supporting','context'
    )),
    is_primary INTEGER NOT NULL DEFAULT 0 CHECK(is_primary IN (0,1)),
    notes TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(question_id, scope_id)
);
CREATE INDEX idx_research_question_scopes_scope
    ON research_question_scopes(scope_id, question_id);
CREATE UNIQUE INDEX idx_research_question_one_primary_scope
    ON research_question_scopes(question_id) WHERE is_primary=1;

CREATE TABLE research_work_items (
    id TEXT PRIMARY KEY,
    scope_id TEXT REFERENCES research_scopes(id),
    question_id TEXT REFERENCES research_questions(id),
    parent_work_item_id TEXT REFERENCES research_work_items(id),
    item_type TEXT NOT NULL CHECK(item_type IN (
        'problem','work_package','task','review','waiting'
    )),
    title TEXT NOT NULL CHECK(trim(title) <> ''),
    completion_criterion TEXT,
    status TEXT NOT NULL DEFAULT 'planned' CHECK(status IN (
        'planned','ready','in_progress','blocked','completed',
        'cancelled','superseded'
    )),
    priority INTEGER NOT NULL DEFAULT 100 CHECK(priority >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK(
        scope_id IS NOT NULL OR question_id IS NOT NULL
        OR parent_work_item_id IS NOT NULL
    )
);
CREATE INDEX idx_research_work_items_parent
    ON research_work_items(parent_work_item_id, priority, id);
CREATE INDEX idx_research_work_items_scope
    ON research_work_items(scope_id, status, priority, id);
CREATE INDEX idx_research_work_items_question
    ON research_work_items(question_id, status, priority, id);

CREATE TRIGGER research_work_item_parent_cycle_guard
BEFORE INSERT ON research_work_items
WHEN NEW.parent_work_item_id IS NOT NULL
BEGIN
    SELECT CASE WHEN NEW.parent_work_item_id=NEW.id THEN
        RAISE(ABORT, 'research work item parent cycle') END;
END;

CREATE TRIGGER research_work_item_parent_cycle_guard_update
BEFORE UPDATE OF parent_work_item_id ON research_work_items
WHEN NEW.parent_work_item_id IS NOT NULL
BEGIN
    SELECT CASE WHEN NEW.parent_work_item_id=NEW.id OR EXISTS (
        WITH RECURSIVE ancestors(id) AS (
            SELECT parent_work_item_id
              FROM research_work_items
             WHERE id=NEW.parent_work_item_id
               AND parent_work_item_id IS NOT NULL
            UNION
            SELECT item.parent_work_item_id
              FROM research_work_items item
              JOIN ancestors current ON item.id=current.id
             WHERE item.parent_work_item_id IS NOT NULL
        )
        SELECT 1 FROM ancestors WHERE id=NEW.id
    ) THEN RAISE(ABORT, 'research work item parent cycle') END;
END;

ALTER TABLE next_actions
    ADD COLUMN work_item_id TEXT REFERENCES research_work_items(id);
CREATE INDEX idx_next_actions_work_item
    ON next_actions(work_item_id, status, priority, updated_at, id);
"""


SOURCE_DOCUMENT_COVERAGE_SCHEMA = """
CREATE TABLE source_coverage_assessments (
    id TEXT PRIMARY KEY,
    source_item_id TEXT NOT NULL REFERENCES source_items(id),
    coverage_key TEXT NOT NULL CHECK(trim(coverage_key) <> ''),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    supersedes_assessment_id TEXT REFERENCES source_coverage_assessments(id),
    dimension TEXT NOT NULL CHECK(dimension IN (
        'inventory','index','content_review','extraction','transcription','review'
    )),
    coverage_level TEXT NOT NULL CHECK(trim(coverage_level) <> ''),
    record_type TEXT,
    scope_text TEXT NOT NULL CHECK(trim(scope_text) <> ''),
    unit_kind TEXT NOT NULL CHECK(unit_kind IN (
        'scan','page','folio','act','entry','field','household','custom'
    )),
    completed_units INTEGER CHECK(completed_units IS NULL OR completed_units >= 0),
    total_units INTEGER CHECK(total_units IS NULL OR total_units >= 0),
    status TEXT NOT NULL CHECK(status IN (
        'unknown','not_started','partial','complete','blocked','not_applicable'
    )),
    systematic INTEGER NOT NULL DEFAULT 0 CHECK(systematic IN (0,1)),
    qa_status TEXT NOT NULL DEFAULT 'unreviewed' CHECK(qa_status IN (
        'unreviewed','agent_reviewed','human_reviewed','not_applicable'
    )),
    basis_type TEXT NOT NULL CHECK(basis_type IN (
        'manifest','index','browse_session','source_children',
        'curator_assessment','derived'
    )),
    basis_ref TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    rationale TEXT NOT NULL CHECK(trim(rationale) <> ''),
    created_by TEXT NOT NULL CHECK(trim(created_by) <> ''),
    created_at TEXT NOT NULL,
    UNIQUE(source_item_id, coverage_key, revision),
    CHECK(
        completed_units IS NULL OR total_units IS NULL
        OR completed_units <= total_units
    ),
    CHECK(
        (revision = 1 AND supersedes_assessment_id IS NULL)
        OR (revision > 1 AND supersedes_assessment_id IS NOT NULL)
    )
);
CREATE INDEX idx_source_coverage_latest
    ON source_coverage_assessments(
        source_item_id, coverage_key, revision DESC
    );
CREATE INDEX idx_source_coverage_dimension
    ON source_coverage_assessments(
        dimension, coverage_level, record_type, status
    );

CREATE TRIGGER source_coverage_revision_guard
BEFORE INSERT ON source_coverage_assessments
WHEN NEW.revision > 1
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
          FROM source_coverage_assessments previous
         WHERE previous.id=NEW.supersedes_assessment_id
           AND previous.source_item_id=NEW.source_item_id
           AND previous.coverage_key=NEW.coverage_key
           AND previous.revision=NEW.revision-1
    ) THEN RAISE(ABORT, 'source coverage revision chain mismatch') END;
END;

CREATE TRIGGER source_item_parent_cycle_guard
BEFORE UPDATE OF parent_id ON source_items
WHEN NEW.parent_id IS NOT NULL
BEGIN
    SELECT CASE WHEN NEW.parent_id=NEW.id OR EXISTS (
        WITH RECURSIVE ancestors(id) AS (
            SELECT parent_id
              FROM source_items
             WHERE id=NEW.parent_id
               AND parent_id IS NOT NULL
            UNION
            SELECT item.parent_id
              FROM source_items item
              JOIN ancestors current ON item.id=current.id
             WHERE item.parent_id IS NOT NULL
        )
        SELECT 1 FROM ancestors WHERE id=NEW.id
    ) THEN RAISE(ABORT, 'source item parent cycle') END;
END;

CREATE TRIGGER source_item_parent_self_insert_guard
BEFORE INSERT ON source_items
WHEN NEW.parent_id IS NOT NULL AND NEW.parent_id=NEW.id
BEGIN
    SELECT RAISE(ABORT, 'source item parent cycle');
END;

CREATE TABLE source_structure_manifests (
    id TEXT PRIMARY KEY,
    manifest_fingerprint TEXT NOT NULL UNIQUE CHECK(trim(manifest_fingerprint) <> ''),
    manifest_json TEXT NOT NULL,
    actor TEXT NOT NULL CHECK(trim(actor) <> ''),
    rationale TEXT NOT NULL CHECK(trim(rationale) <> ''),
    created_at TEXT NOT NULL
);

CREATE TABLE source_structure_runs (
    id TEXT PRIMARY KEY,
    manifest_id TEXT NOT NULL REFERENCES source_structure_manifests(id),
    mode TEXT NOT NULL CHECK(mode IN ('apply')),
    status TEXT NOT NULL CHECK(status IN ('applied','failed')),
    summary_json TEXT NOT NULL DEFAULT '{}',
    error_text TEXT,
    actor TEXT NOT NULL CHECK(trim(actor) <> ''),
    created_at TEXT NOT NULL,
    UNIQUE(manifest_id, mode, status)
);
"""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str = ""
    callback: Callable[[sqlite3.Connection], dict[str, object]] | None = None
    callback_revision: str = ""

    @property
    def checksum(self) -> str:
        callback_source = inspect.getsource(self.callback) if self.callback else ""
        payload = (
            f"{self.version}\0{self.name}\0{self.sql}\0{callback_source}\0{self.callback_revision}"
        ).encode()
        return hashlib.sha256(payload).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _exists(connection: sqlite3.Connection, table: str, column: str, value: object) -> bool:
    return connection.execute(f"SELECT 1 FROM {table} WHERE {column} = ? LIMIT 1", (value,)).fetchone() is not None


def _legacy_event_proposal(payload_json: str) -> dict[str, object]:
    try:
        payload = json.loads(payload_json)
    except (TypeError, json.JSONDecodeError):
        payload = {}
    dates = payload.get("dates") if isinstance(payload.get("dates"), list) else []
    places = payload.get("places") if isinstance(payload.get("places"), list) else []
    first_date = dates[0] if dates and isinstance(dates[0], dict) else {}
    first_place = places[0] if places and isinstance(places[0], dict) else {}
    return {
        "event_type": str(payload.get("document_type") or "legacy_document_observation"),
        "date_text": first_date.get("as_written"),
        "date_from": first_date.get("normalized"),
        "place_text": first_place.get("normalized") or first_place.get("as_written"),
        "source_payload": {
            "date": first_date,
            "place": first_place,
            "document_type": payload.get("document_type"),
        },
    }


def _looks_compound_name(value: str) -> bool:
    folded = " ".join(value.casefold().split())
    return bool(re.search(r"\s+i\s+|[,;/]", folded) or folded.startswith("dom "))


def _legacy_import(connection: sqlite3.Connection) -> dict[str, int]:
    """Copy legacy rows without promoting evidence or resolving identities."""

    counts = {key: 0 for key in (
        "source_items", "artifacts", "readings", "persons", "occurrences",
        "events", "assertions", "claims", "claim_evidence", "curation_queue",
    )}
    source_items: dict[str, str] = {}
    for row in connection.execute("SELECT * FROM sources ORDER BY id"):
        item_id = stable_id("item", "legacy", row["id"])
        source_items[row["id"]] = item_id
        if not _exists(connection, "source_items", "legacy_source_id", row["id"]):
            connection.execute(
                """INSERT INTO source_items(
                    id, legacy_source_id, title, item_type, source_url,
                    source_quality, verification_status, metadata_json
                ) VALUES (?, ?, ?, ?, ?, 'Indeterminable', 'imported_unverified', ?)""",
                (item_id, row["id"], row["title"], row["kind"], row["source_url"],
                 json.dumps({"legacy_service": row["service"]}, ensure_ascii=False)),
            )
            counts["source_items"] += 1
        if not _exists(connection, "artifacts", "legacy_source_id", row["id"]):
            connection.execute(
                """INSERT INTO artifacts(
                    id, source_item_id, legacy_source_id, artifact_type, path,
                    media_type, sha256, immutable, created_at, metadata_json
                ) VALUES (?, ?, ?, 'RAW', ?, ?, ?, 1, ?, ?)""",
                (stable_id("artifact", "legacy", row["id"]), item_id, row["id"],
                 row["archive_path"], row["media_type"], row["sha256"],
                 row["captured_at"], row["metadata_json"]),
            )
            counts["artifacts"] += 1

    reading_versions: dict[str, str] = {}
    analysis_sources: dict[str, str] = {}
    analysis_payloads: dict[str, str] = {}
    for row in connection.execute("SELECT * FROM analyses ORDER BY id"):
        analysis_sources[row["id"]] = row["source_id"]
        analysis_payloads[row["id"]] = row["payload_json"]
        reading_id = stable_id("reading", "legacy", row["id"])
        version_id = stable_id("reading_version", "legacy", row["id"])
        reading_versions[row["id"]] = version_id
        if not _exists(connection, "readings", "legacy_analysis_id", row["id"]):
            connection.execute(
                """INSERT INTO readings(
                    id, source_item_id, artifact_id, legacy_analysis_id, reading_type,
                    status, model_or_reviewer, created_at, metadata_json
                ) VALUES (?, ?, ?, ?, 'legacy_ai_import', 'imported_unverified', ?, ?, ?)""",
                (reading_id, source_items[row["source_id"]],
                 stable_id("artifact", "legacy", row["source_id"]), row["id"],
                 row["model"], row["created_at"],
                 json.dumps({"legacy_status": row["status"]}, ensure_ascii=False)),
            )
            connection.execute(
                """INSERT INTO reading_versions(
                    id, reading_id, version_number, status, result_path, payload_json, created_at
                ) VALUES (?, ?, 1, 'imported_unverified', ?, ?, ?)""",
                (version_id, reading_id, row["result_path"], row["payload_json"], row["created_at"]),
            )
            counts["readings"] += 1

    events: dict[str, str] = {}
    analyses_by_source: dict[str, list[str]] = {}
    for analysis_id, source_id in analysis_sources.items():
        analyses_by_source.setdefault(source_id, []).append(analysis_id)
    for source_id, analysis_ids in analyses_by_source.items():
        event_id = stable_id("event", "legacy-source", source_id)
        events[source_id] = event_id
        proposals = [_legacy_event_proposal(analysis_payloads[item]) for item in analysis_ids]
        proposal = proposals[0]
        if not _exists(connection, "events", "id", event_id):
            connection.execute(
                """INSERT INTO events(
                       id, source_item_id, event_type, date_text, date_from, place_text,
                       verification_status, notes
                   ) VALUES (?, ?, ?, ?, ?, ?, 'imported_unverified', ?)""",
                (
                    event_id,
                    source_items[source_id],
                    proposal["event_type"],
                    proposal["date_text"],
                    proposal["date_from"],
                    proposal["place_text"],
                    json.dumps(
                        {"legacy_analyses": analysis_ids, "selected_proposal": proposal},
                        ensure_ascii=False,
                    ),
                ),
            )
            counts["events"] += 1
        for analysis_id, analysis_proposal in zip(analysis_ids, proposals):
            connection.execute(
                """INSERT OR IGNORE INTO event_readings(
                       id, event_id, reading_version_id, evidence_json
                   ) VALUES (?, ?, ?, ?)""",
                (
                    stable_id("event_reading", event_id, reading_versions[analysis_id]),
                    event_id,
                    reading_versions[analysis_id],
                    json.dumps(analysis_proposal, ensure_ascii=False),
                ),
            )
        proposal_keys = {
            (item["event_type"], item["date_from"], item["place_text"]) for item in proposals
        }
        if len(proposal_keys) > 1:
            queue_id = stable_id("curation", "event-alternatives", event_id)
            connection.execute(
                """INSERT OR IGNORE INTO curation_queue(
                       id, object_type, object_id, reason_code, payload_json, created_at
                   ) VALUES (?, 'event', ?, 'alternative_event_readings', ?, ?)""",
                (queue_id, event_id, json.dumps(proposals, ensure_ascii=False), _utc_now()),
            )
            counts["curation_queue"] += 1

    mention_rows = connection.execute(
        """SELECT m.*, e.display_name, e.normalized_name
           FROM mentions m JOIN entities e ON e.id = m.entity_id ORDER BY m.id"""
    ).fetchall()
    mention_groups: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for row in mention_rows:
        source_id = analysis_sources[row["analysis_id"]]
        mention_groups.setdefault((source_id, row["entity_id"]), []).append(row)
    for (source_id, entity_id), rows in mention_groups.items():
        row = rows[0]
        suffix = f"{source_id}|{entity_id}"
        person_id = stable_id("person", "legacy-occurrence", suffix)
        occurrence_id = stable_id("occ", "legacy-occurrence", suffix)
        if _exists(connection, "person_occurrences", "id", occurrence_id):
            continue
        raw_names = list(dict.fromkeys((item["raw_text"] or item["display_name"]) for item in rows))
        raw_name = raw_names[0]
        roles = list(dict.fromkeys((item["role"] or "unspecified") for item in rows))
        confidences = [item["confidence"] for item in rows if item["confidence"] is not None]
        evidence = [
            {
                "legacy_mention_id": item["id"],
                "role": item["role"],
                "evidence": json.loads(item["evidence_json"]),
            }
            for item in rows
        ]
        connection.execute(
            "INSERT INTO persons(id, preferred_name, status, created_at, notes) VALUES (?, ?, 'candidate', ?, ?)",
            (person_id, row["display_name"], _utc_now(),
             "One candidate per person named in one source document; identity across sources intentionally unresolved."),
        )
        for name_text in raw_names:
            connection.execute(
                """INSERT INTO person_names(id, person_id, name_text, normalized_name, name_type)
                   VALUES (?, ?, ?, ?, 'legacy_import')""",
                (
                    stable_id("name", "legacy-occurrence", suffix, name_text),
                    person_id,
                    name_text,
                    row["normalized_name"],
                ),
            )
        first_analysis_id = row["analysis_id"]
        connection.execute(
            """INSERT INTO person_occurrences(
                id, source_item_id, reading_version_id, person_id, legacy_mention_id,
                raw_name, normalized_name, search_name, role_text, reading_confidence,
                verification_status, evidence_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'imported_unverified', ?)""",
            (
                occurrence_id,
                source_items[source_id],
                reading_versions[first_analysis_id],
                person_id,
                row["id"],
                raw_name,
                row["normalized_name"],
                search_fold(" ".join(raw_names)),
                "; ".join(roles),
                min(confidences) if confidences else None,
                json.dumps(
                    {"legacy_mention_ids": [item["id"] for item in rows], "mentions": evidence},
                    ensure_ascii=False,
                ),
            ),
        )
        rows_by_analysis: dict[str, list[sqlite3.Row]] = {}
        for item in rows:
            rows_by_analysis.setdefault(item["analysis_id"], []).append(item)
        for analysis_id, analysis_rows in rows_by_analysis.items():
            analysis_roles = list(
                dict.fromkeys((item["role"] or "unspecified") for item in analysis_rows)
            )
            analysis_evidence = [
                {"legacy_mention_id": item["id"], "evidence": json.loads(item["evidence_json"])}
                for item in analysis_rows
            ]
            connection.execute(
                """INSERT OR IGNORE INTO occurrence_readings(
                       id, occurrence_id, reading_version_id, role_text, evidence_json
                   ) VALUES (?, ?, ?, ?, ?)""",
                (
                    stable_id("occurrence_reading", occurrence_id, reading_versions[analysis_id]),
                    occurrence_id,
                    reading_versions[analysis_id],
                    "; ".join(analysis_roles),
                    json.dumps(analysis_evidence, ensure_ascii=False),
                ),
            )
            for item in analysis_rows:
                connection.execute(
                    """INSERT OR IGNORE INTO occurrence_legacy_mentions(
                           id, occurrence_id, legacy_mention_id, reading_version_id,
                           role_text, evidence_json
                       ) VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        stable_id("occ_mention", str(item["id"])),
                        occurrence_id,
                        item["id"],
                        reading_versions[analysis_id],
                        item["role"],
                        item["evidence_json"],
                    ),
                )
        for role in roles:
            connection.execute(
                "INSERT INTO participations(id, event_id, occurrence_id, role) VALUES (?, ?, ?, ?)",
                (
                    stable_id("participation", "legacy-occurrence", suffix, role),
                    events[source_id],
                    occurrence_id,
                    role,
                ),
            )
        counts["persons"] += 1
        counts["occurrences"] += 1

    referenced_entities = {
        row[0]
        for row in connection.execute(
            """SELECT entity_id FROM mentions
               UNION SELECT subject_entity_id FROM relations
               UNION SELECT object_entity_id FROM relations"""
        )
    }
    for row in connection.execute("SELECT id, display_name FROM entities ORDER BY id"):
        if row["id"] not in referenced_entities or not _looks_compound_name(row["display_name"]):
            continue
        queue_id = stable_id("curation", "compound-legacy-entity", row["id"])
        connection.execute(
            """INSERT OR IGNORE INTO curation_queue(
                   id, object_type, object_id, reason_code, payload_json, created_at
               ) VALUES (?, 'legacy_entity', ?, 'compound_person_requires_split', ?, ?)""",
            (
                queue_id,
                row["id"],
                json.dumps({"display_name": row["display_name"]}, ensure_ascii=False),
                _utc_now(),
            ),
        )
        counts["curation_queue"] += 1

    for row in connection.execute("SELECT * FROM relations ORDER BY id").fetchall():
        if _exists(connection, "source_assertions", "legacy_relation_id", row["id"]):
            continue
        source_id = analysis_sources[row["analysis_id"]]
        evidence = json.loads(row["evidence_json"])
        metadata = {"date_text": row["date_text"], "place_text": row["place_text"],
                    "legacy_confidence": row["confidence"], "evidence": evidence}
        connection.execute(
            """INSERT INTO source_assertions(
                id, source_item_id, reading_version_id, legacy_relation_id,
                assertion_type, subject_ref, predicate, object_text,
                information_quality, reading_certainty, verification_status, metadata_json
            ) VALUES (?, ?, ?, ?, 'legacy_relation', ?, ?, ?,
                      'Indeterminable', 'Ambiguous', 'imported_unverified', ?)""",
            (stable_id("assertion", "legacy-relation", str(row["id"])),
             source_items[source_id], reading_versions[row["analysis_id"]], row["id"],
             row["subject_entity_id"], row["predicate"], row["object_entity_id"],
             json.dumps(metadata, ensure_ascii=False)),
        )
        counts["assertions"] += 1

    for row in connection.execute("SELECT * FROM hypotheses ORDER BY id").fetchall():
        if _exists(connection, "claims", "legacy_hypothesis_id", row["id"]):
            continue
        rationale = {"legacy_status": row["status"], "legacy_confidence": row["confidence"],
                     "rationale": row["rationale"]}
        connection.execute(
            """INSERT INTO claims(
                id, legacy_hypothesis_id, statement, status, rationale, created_at, updated_at
            ) VALUES (?, ?, ?, 'imported_unverified', ?, ?, ?)""",
            (stable_id("claim", "legacy", row["id"]), row["id"], row["statement"],
             json.dumps(rationale, ensure_ascii=False), row["created_at"], row["updated_at"]),
        )
        counts["claims"] += 1

    evidence_rows = connection.execute(
        "SELECT rowid AS legacy_rowid, * FROM hypothesis_evidence ORDER BY rowid"
    ).fetchall()
    for row in evidence_rows:
        key = f"{row['hypothesis_id']}|{row['source_id']}|{row['analysis_id'] or ''}"
        assertion_id = stable_id("assertion", "legacy-hypothesis-evidence", key)
        if not _exists(connection, "source_assertions", "legacy_evidence_key", key):
            connection.execute(
                """INSERT INTO source_assertions(
                    id, source_item_id, reading_version_id, legacy_evidence_key,
                    assertion_type, predicate, object_text, information_quality,
                    reading_certainty, verification_status, metadata_json
                ) VALUES (?, ?, ?, ?, 'legacy_evidence_link', 'reported_as_evidence_for', ?,
                          'Indeterminable', 'Ambiguous', 'imported_unverified', ?)""",
                (assertion_id, source_items[row["source_id"]],
                 reading_versions.get(row["analysis_id"]), key, row["hypothesis_id"],
                 json.dumps({"note": row["note"]}, ensure_ascii=False)),
            )
            counts["assertions"] += 1
        evidence_id = stable_id("claim_evidence", "legacy", key)
        if not _exists(connection, "claim_evidence", "id", evidence_id):
            stance = {
                "supports": "supports",
                "contradicts": "conflicts",
                "conflicts": "conflicts",
                "context": "context",
            }.get(row["stance"], "context")
            connection.execute(
                """INSERT INTO claim_evidence(
                    id, claim_id, source_assertion_id, stance, evidence_type, dependency_group, note
                ) VALUES (?, ?, ?, ?, 'Indeterminable', ?, ?)""",
                (evidence_id, stable_id("claim", "legacy", row["hypothesis_id"]),
                 assertion_id, stance, row["source_id"], row["note"]),
            )
            counts["claim_evidence"] += 1
    return counts


def _optional_fts(connection: sqlite3.Connection) -> dict[str, int]:
    try:
        connection.execute(
            """CREATE VIRTUAL TABLE IF NOT EXISTS local_name_search USING fts5(
                occurrence_id UNINDEXED, name_text, normalized_name, search_name, role_text
            )"""
        )
        connection.execute(
            """CREATE TRIGGER IF NOT EXISTS local_name_search_ai
               AFTER INSERT ON person_occurrences BEGIN
                 INSERT INTO local_name_search(
                   occurrence_id, name_text, normalized_name, search_name, role_text
                 ) VALUES (new.id, new.raw_name, new.normalized_name, new.search_name, new.role_text);
               END"""
        )
        connection.execute(
            """CREATE TRIGGER IF NOT EXISTS local_name_search_ad
               AFTER DELETE ON person_occurrences BEGIN
                 DELETE FROM local_name_search WHERE occurrence_id = old.id;
               END"""
        )
        connection.execute(
            """CREATE TRIGGER IF NOT EXISTS local_name_search_au
               AFTER UPDATE ON person_occurrences BEGIN
                 DELETE FROM local_name_search WHERE occurrence_id = old.id;
                 INSERT INTO local_name_search(
                   occurrence_id, name_text, normalized_name, search_name, role_text
                 ) VALUES (new.id, new.raw_name, new.normalized_name, new.search_name, new.role_text);
               END"""
        )
        connection.execute("DELETE FROM local_name_search")
        connection.execute(
            """INSERT INTO local_name_search(
                   occurrence_id, name_text, normalized_name, search_name, role_text
               ) SELECT id, raw_name, normalized_name, search_name, role_text
                 FROM person_occurrences"""
        )
        return {"fts5_enabled": 1}
    except sqlite3.OperationalError as error:
        if "no such module: fts5" not in str(error).casefold():
            raise
        connection.execute("DROP TRIGGER IF EXISTS local_name_search_ai")
        connection.execute("DROP TRIGGER IF EXISTS local_name_search_ad")
        connection.execute("DROP TRIGGER IF EXISTS local_name_search_au")
        connection.execute("DROP TABLE IF EXISTS local_name_search")
        return {"fts5_enabled": 0}


def _dismiss_variant_only_curation(connection: sqlite3.Connection) -> dict[str, object]:
    """Close slash-only name variants that migration 3 conservatively queued."""

    rows = connection.execute(
        """SELECT id, payload_json FROM curation_queue
           WHERE reason_code = 'compound_person_requires_split' AND status = 'open'"""
    ).fetchall()
    dismissed = 0
    for row in rows:
        payload = json.loads(row["payload_json"])
        name = str(payload.get("display_name") or "")
        if "/" not in name or re.search(r"\s+i\s+|[,;]", name.casefold()):
            continue
        connection.execute(
            """UPDATE curation_queue
               SET status = 'dismissed', resolved_at = ?, resolution_note = ?
               WHERE id = ?""",
            (
                _utc_now(),
                "Wariant pisowni rozdzielony ukośnikiem, nie złożona osoba; bez zmiany danych źródłowych.",
                row["id"],
            ),
        )
        dismissed += 1
    return {"dismissed_variant_only_items": dismissed}


def _backfill_hierarchical_research_scopes(
    connection: sqlite3.Connection,
) -> dict[str, object]:
    """Create a lossless legacy hierarchy without inferring family facts."""

    now = _utc_now()
    root_scope_id = "scope_genea_program"
    connection.execute(
        """INSERT INTO research_scopes(
               id,scope_type,title,summary,status,created_at,updated_at
           ) VALUES(?,?,?,?,?,?,?)""",
        (
            root_scope_id,
            "program",
            "Program badań genealogicznych",
            "Główny kontener organizacyjny; nie jest twierdzeniem genealogicznym.",
            "active",
            now,
            now,
        ),
    )
    case_rows = connection.execute(
        "SELECT id,title,summary,status FROM research_cases ORDER BY id"
    ).fetchall()
    case_scope_ids: dict[str, str] = {}
    for order, row in enumerate(case_rows, start=1):
        case_id = str(row["id"])
        scope_id = stable_id("research_scope", "legacy_case", case_id)
        case_scope_ids[case_id] = scope_id
        scope_type = "place_study" if case_id.startswith("LOC-") else "legacy_case"
        status = {
            "open": "active",
            "on-hold": "on_hold",
            "closed": "closed",
        }.get(str(row["status"]), "active")
        connection.execute(
            """INSERT INTO research_scopes(
                   id,scope_type,title,summary,status,legacy_case_id,
                   created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                scope_id,
                scope_type,
                row["title"],
                row["summary"],
                status,
                case_id,
                now,
                now,
            ),
        )
        connection.execute(
            """INSERT INTO research_scope_edges(
                   id,parent_scope_id,child_scope_id,relation_type,is_primary,
                   sort_order,notes,created_at
               ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                stable_id("research_scope_edge", root_scope_id, scope_id, "contains"),
                root_scope_id,
                scope_id,
                "contains",
                1,
                order * 10,
                "Automatyczny, bezstratny backfill sprawy legacy.",
                now,
            ),
        )

    question_count = 0
    for row in connection.execute(
        "SELECT id,case_id FROM research_questions ORDER BY case_id,priority,id"
    ):
        scope_id = case_scope_ids.get(str(row["case_id"]))
        if scope_id is None:
            continue
        connection.execute(
            """INSERT INTO research_question_scopes(
                   id,question_id,scope_id,role,is_primary,notes,created_at
               ) VALUES(?,?,?,?,?,?,?)""",
            (
                stable_id("research_question_scope", row["id"], scope_id),
                row["id"],
                scope_id,
                "primary",
                1,
                "Główne członkostwo zgodne z wcześniejszym case_id.",
                now,
            ),
        )
        question_count += 1

    return {
        "root_scope_id": root_scope_id,
        "legacy_scope_count": len(case_scope_ids),
        "question_membership_count": question_count,
        "semantic_inference_count": 0,
    }


EVENT_RECORD_CURATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS event_record_candidates (
    id TEXT PRIMARY KEY,
    source_item_id TEXT NOT NULL REFERENCES source_items(id),
    event_group TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'proposed'
        CHECK(status IN ('proposed','resolved_same_record','resolved_distinct','deferred','superseded')),
    basis_json TEXT NOT NULL DEFAULT '{}',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source_item_id,event_group,fingerprint)
);
CREATE TABLE IF NOT EXISTS event_record_candidate_members (
    candidate_id TEXT NOT NULL REFERENCES event_record_candidates(id),
    event_id TEXT NOT NULL REFERENCES events(id),
    ordinal INTEGER NOT NULL,
    PRIMARY KEY(candidate_id,event_id),
    UNIQUE(candidate_id,ordinal)
);
CREATE TABLE IF NOT EXISTS event_record_decisions (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES event_record_candidates(id),
    revision INTEGER NOT NULL CHECK(revision > 0),
    supersedes_decision_id TEXT REFERENCES event_record_decisions(id),
    decision TEXT NOT NULL CHECK(decision IN ('same_record','distinct_records','defer')),
    decided_by TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    rationale TEXT NOT NULL,
    impact_json TEXT NOT NULL DEFAULT '{}',
    command_id TEXT NOT NULL UNIQUE,
    UNIQUE(candidate_id,revision)
);
CREATE INDEX IF NOT EXISTS idx_event_record_candidates_status
    ON event_record_candidates(status,source_item_id,event_group);
CREATE INDEX IF NOT EXISTS idx_event_record_members_event
    ON event_record_candidate_members(event_id,candidate_id);
CREATE INDEX IF NOT EXISTS idx_event_record_decisions_candidate
    ON event_record_decisions(candidate_id,revision DESC);
"""


CURATION_COMMAND_ENVELOPE_SCHEMA = """
CREATE TABLE IF NOT EXISTS curation_commands (
    command_id TEXT PRIMARY KEY,
    command_type TEXT NOT NULL,
    object_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    result_json TEXT NOT NULL,
    actor TEXT NOT NULL,
    rationale TEXT NOT NULL,
    committed_at TEXT NOT NULL,
    projection_status TEXT NOT NULL DEFAULT 'pending'
        CHECK(projection_status IN ('pending','refreshed','failed','not_required')),
    projection_updated_at TEXT,
    projection_error TEXT
);
CREATE INDEX IF NOT EXISTS idx_curation_commands_object
    ON curation_commands(object_type,object_id,committed_at);
CREATE INDEX IF NOT EXISTS idx_curation_commands_projection
    ON curation_commands(projection_status,committed_at);

ALTER TABLE identity_decisions
    ADD COLUMN command_id TEXT REFERENCES curation_commands(command_id);
ALTER TABLE identity_decisions
    ADD COLUMN input_fingerprint TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_identity_decisions_command
    ON identity_decisions(command_id) WHERE command_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS household_membership_revisions (
    id TEXT PRIMARY KEY,
    membership_id TEXT NOT NULL REFERENCES household_memberships(id),
    household_id TEXT NOT NULL REFERENCES households(id),
    person_id TEXT REFERENCES persons(id),
    occurrence_id TEXT REFERENCES person_occurrences(id),
    role TEXT,
    date_from TEXT,
    date_to TEXT,
    source_assertion_id TEXT REFERENCES source_assertions(id),
    revision INTEGER NOT NULL CHECK(revision > 0),
    supersedes_revision_id TEXT REFERENCES household_membership_revisions(id),
    state TEXT NOT NULL CHECK(state IN ('active','withdrawn')),
    actor TEXT NOT NULL,
    rationale TEXT NOT NULL,
    command_id TEXT NOT NULL REFERENCES curation_commands(command_id),
    created_at TEXT NOT NULL,
    UNIQUE(membership_id,revision),
    UNIQUE(command_id,membership_id)
);
CREATE INDEX IF NOT EXISTS idx_household_membership_revisions_current
    ON household_membership_revisions(membership_id,revision DESC);

CREATE TABLE IF NOT EXISTS private_relationship_revisions (
    id TEXT PRIMARY KEY,
    relationship_id TEXT NOT NULL REFERENCES private_relationships(id),
    revision INTEGER NOT NULL CHECK(revision > 0),
    supersedes_revision_id TEXT REFERENCES private_relationship_revisions(id),
    subject_person_id TEXT NOT NULL REFERENCES persons(id),
    object_person_id TEXT NOT NULL REFERENCES persons(id),
    relationship_type TEXT NOT NULL,
    date_from TEXT,
    date_to TEXT,
    classification TEXT NOT NULL,
    provenance_kind TEXT NOT NULL,
    verification_status TEXT NOT NULL,
    rationale TEXT NOT NULL,
    actor TEXT NOT NULL,
    command_id TEXT NOT NULL REFERENCES curation_commands(command_id),
    created_at TEXT NOT NULL,
    UNIQUE(relationship_id,revision),
    UNIQUE(command_id,relationship_id)
);
CREATE INDEX IF NOT EXISTS idx_private_relationship_revisions_current
    ON private_relationship_revisions(relationship_id,revision DESC);
"""


QUESTION_CANDIDATE_AND_INTEGRITY_SCHEMA = """
CREATE TABLE IF NOT EXISTS research_question_candidates (
    id TEXT PRIMARY KEY,
    candidate_key TEXT NOT NULL UNIQUE,
    rule_id TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    case_id TEXT NOT NULL REFERENCES research_cases(id),
    question_type TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    subject_label TEXT NOT NULL,
    gap_key TEXT NOT NULL,
    question_text TEXT NOT NULL,
    completion_criterion TEXT NOT NULL,
    priority INTEGER NOT NULL CHECK(priority >= 0),
    evidence_fingerprint TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    generator_input_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
        'detected','triage','deferred','promoted','merged','dismissed',
        'superseded','stale'
    )),
    privacy_status TEXT NOT NULL CHECK(privacy_status IN ('pending','passed','blocked')),
    privacy_json TEXT NOT NULL DEFAULT '{}',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    seen_count INTEGER NOT NULL DEFAULT 1 CHECK(seen_count > 0),
    current_revision INTEGER NOT NULL DEFAULT 1 CHECK(current_revision > 0),
    promoted_question_id TEXT REFERENCES research_questions(id),
    merged_into_candidate_id TEXT REFERENCES research_question_candidates(id),
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_question_candidates_state
    ON research_question_candidates(status,priority,case_id,last_seen_at);
CREATE INDEX IF NOT EXISTS idx_question_candidates_subject
    ON research_question_candidates(subject_type,subject_id,status);

CREATE TABLE IF NOT EXISTS research_question_candidate_revisions (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES research_question_candidates(id),
    revision INTEGER NOT NULL CHECK(revision > 0),
    evidence_fingerprint TEXT NOT NULL,
    generator_input_fingerprint TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    seen_at TEXT NOT NULL,
    UNIQUE(candidate_id,revision)
);

CREATE TABLE IF NOT EXISTS research_question_candidate_decisions (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES research_question_candidates(id),
    revision INTEGER NOT NULL CHECK(revision > 0),
    decision TEXT NOT NULL CHECK(decision IN (
        'promote','merge','dismiss','defer','supersede'
    )),
    target_question_id TEXT REFERENCES research_questions(id),
    target_candidate_id TEXT REFERENCES research_question_candidates(id),
    expected_fingerprint TEXT NOT NULL,
    privacy_check_json TEXT NOT NULL,
    actor TEXT NOT NULL,
    rationale TEXT NOT NULL,
    command_id TEXT NOT NULL UNIQUE REFERENCES curation_commands(command_id),
    decided_at TEXT NOT NULL,
    CHECK(decision <> 'merge' OR target_question_id IS NOT NULL),
    CHECK(decision <> 'supersede' OR target_candidate_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_question_candidate_decisions_candidate
    ON research_question_candidate_decisions(candidate_id,decided_at);

CREATE TABLE IF NOT EXISTS source_assertion_references (
    id TEXT PRIMARY KEY,
    assertion_id TEXT NOT NULL REFERENCES source_assertions(id) ON DELETE CASCADE,
    position TEXT NOT NULL CHECK(position IN ('subject','object')),
    object_type TEXT NOT NULL CHECK(object_type IN (
        'entity','person','person_occurrence','event','household','place','source_item'
    )),
    object_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(assertion_id,position)
);
CREATE INDEX IF NOT EXISTS idx_source_assertion_references_object
    ON source_assertion_references(object_type,object_id,assertion_id);

CREATE TRIGGER IF NOT EXISTS validate_source_assertion_reference_insert
BEFORE INSERT ON source_assertion_references
WHEN (NEW.object_type='person' AND NOT EXISTS(SELECT 1 FROM persons WHERE id=NEW.object_id))
  OR (NEW.object_type='entity' AND NOT EXISTS(SELECT 1 FROM entities WHERE id=NEW.object_id))
  OR (NEW.object_type='person_occurrence' AND NOT EXISTS(SELECT 1 FROM person_occurrences WHERE id=NEW.object_id))
  OR (NEW.object_type='event' AND NOT EXISTS(SELECT 1 FROM events WHERE id=NEW.object_id))
  OR (NEW.object_type='household' AND NOT EXISTS(SELECT 1 FROM households WHERE id=NEW.object_id))
  OR (NEW.object_type='place' AND NOT EXISTS(SELECT 1 FROM places WHERE id=NEW.object_id))
  OR (NEW.object_type='source_item' AND NOT EXISTS(SELECT 1 FROM source_items WHERE id=NEW.object_id))
BEGIN
    SELECT RAISE(ABORT, 'source assertion reference does not resolve');
END;

CREATE TRIGGER IF NOT EXISTS type_occurrence_refs_after_assertion_insert
AFTER INSERT ON source_assertions
BEGIN
    INSERT OR IGNORE INTO source_assertion_references(
        id,assertion_id,position,object_type,object_id,created_at
    )
    SELECT 'assertion_ref_' || NEW.id || '_subject',NEW.id,'subject',
           'person_occurrence',NEW.subject_ref,CURRENT_TIMESTAMP
      WHERE NEW.subject_ref IS NOT NULL
        AND EXISTS(SELECT 1 FROM person_occurrences WHERE id=NEW.subject_ref);
    INSERT OR IGNORE INTO source_assertion_references(
        id,assertion_id,position,object_type,object_id,created_at
    )
    SELECT 'assertion_ref_' || NEW.id || '_object',NEW.id,'object',
           'person_occurrence',NEW.object_text,CURRENT_TIMESTAMP
      WHERE NEW.object_text IS NOT NULL
        AND EXISTS(SELECT 1 FROM person_occurrences WHERE id=NEW.object_text);
END;

CREATE TRIGGER IF NOT EXISTS type_occurrence_refs_after_assertion_update
AFTER UPDATE OF subject_ref,object_text ON source_assertions
BEGIN
    DELETE FROM source_assertion_references
     WHERE assertion_id=NEW.id AND object_type='person_occurrence';
    INSERT OR IGNORE INTO source_assertion_references(
        id,assertion_id,position,object_type,object_id,created_at
    )
    SELECT 'assertion_ref_' || NEW.id || '_subject',NEW.id,'subject',
           'person_occurrence',NEW.subject_ref,CURRENT_TIMESTAMP
      WHERE NEW.subject_ref IS NOT NULL
        AND EXISTS(SELECT 1 FROM person_occurrences WHERE id=NEW.subject_ref);
    INSERT OR IGNORE INTO source_assertion_references(
        id,assertion_id,position,object_type,object_id,created_at
    )
    SELECT 'assertion_ref_' || NEW.id || '_object',NEW.id,'object',
           'person_occurrence',NEW.object_text,CURRENT_TIMESTAMP
      WHERE NEW.object_text IS NOT NULL
        AND EXISTS(SELECT 1 FROM person_occurrences WHERE id=NEW.object_text);
END;

CREATE TRIGGER IF NOT EXISTS validate_household_membership_insert
BEFORE INSERT ON household_memberships
WHEN ((NEW.person_id IS NULL) = (NEW.occurrence_id IS NULL))
BEGIN
    SELECT RAISE(ABORT, 'household membership requires exactly one person or occurrence');
END;
CREATE TRIGGER IF NOT EXISTS validate_household_membership_update
BEFORE UPDATE OF person_id,occurrence_id ON household_memberships
WHEN ((NEW.person_id IS NULL) = (NEW.occurrence_id IS NULL))
BEGIN
    SELECT RAISE(ABORT, 'household membership requires exactly one person or occurrence');
END;

CREATE TRIGGER IF NOT EXISTS validate_identity_candidate_status_insert
BEFORE INSERT ON identity_candidates
WHEN NEW.status NOT IN ('unresolved','resolved_same','different','resolved_different','dismissed')
BEGIN
    SELECT RAISE(ABORT, 'invalid identity candidate status');
END;
CREATE TRIGGER IF NOT EXISTS validate_identity_candidate_status_update
BEFORE UPDATE OF status ON identity_candidates
WHEN NEW.status NOT IN ('unresolved','resolved_same','different','resolved_different','dismissed')
BEGIN
    SELECT RAISE(ABORT, 'invalid identity candidate status');
END;

CREATE TRIGGER IF NOT EXISTS validate_identity_decision_insert
BEFORE INSERT ON identity_decisions
WHEN NEW.decision NOT IN ('same','different','dismissed')
BEGIN
    SELECT RAISE(ABORT, 'invalid identity decision');
END;

CREATE TRIGGER IF NOT EXISTS validate_claim_subject_reference_insert
BEFORE INSERT ON claims
WHEN NEW.subject_ref IS NOT NULL
 AND NOT EXISTS(SELECT 1 FROM entities WHERE id=NEW.subject_ref)
 AND NOT EXISTS(SELECT 1 FROM persons WHERE id=NEW.subject_ref)
 AND NOT EXISTS(SELECT 1 FROM person_occurrences WHERE id=NEW.subject_ref)
 AND NOT EXISTS(SELECT 1 FROM events WHERE id=NEW.subject_ref)
 AND NOT EXISTS(SELECT 1 FROM households WHERE id=NEW.subject_ref)
 AND NOT EXISTS(SELECT 1 FROM places WHERE id=NEW.subject_ref)
BEGIN
    SELECT RAISE(ABORT, 'claim subject_ref does not resolve to a supported object');
END;
CREATE TRIGGER IF NOT EXISTS validate_claim_object_reference_insert
BEFORE INSERT ON claims
WHEN NEW.object_ref IS NOT NULL
 AND NOT EXISTS(SELECT 1 FROM entities WHERE id=NEW.object_ref)
 AND NOT EXISTS(SELECT 1 FROM persons WHERE id=NEW.object_ref)
 AND NOT EXISTS(SELECT 1 FROM person_occurrences WHERE id=NEW.object_ref)
 AND NOT EXISTS(SELECT 1 FROM events WHERE id=NEW.object_ref)
 AND NOT EXISTS(SELECT 1 FROM households WHERE id=NEW.object_ref)
 AND NOT EXISTS(SELECT 1 FROM places WHERE id=NEW.object_ref)
BEGIN
    SELECT RAISE(ABORT, 'claim object_ref does not resolve to a supported object');
END;
CREATE TRIGGER IF NOT EXISTS validate_claim_subject_reference_update
BEFORE UPDATE OF subject_ref ON claims
WHEN NEW.subject_ref IS NOT NULL
 AND NOT EXISTS(SELECT 1 FROM entities WHERE id=NEW.subject_ref)
 AND NOT EXISTS(SELECT 1 FROM persons WHERE id=NEW.subject_ref)
 AND NOT EXISTS(SELECT 1 FROM person_occurrences WHERE id=NEW.subject_ref)
 AND NOT EXISTS(SELECT 1 FROM events WHERE id=NEW.subject_ref)
 AND NOT EXISTS(SELECT 1 FROM households WHERE id=NEW.subject_ref)
 AND NOT EXISTS(SELECT 1 FROM places WHERE id=NEW.subject_ref)
BEGIN
    SELECT RAISE(ABORT, 'claim subject_ref does not resolve to a supported object');
END;
CREATE TRIGGER IF NOT EXISTS validate_claim_object_reference_update
BEFORE UPDATE OF object_ref ON claims
WHEN NEW.object_ref IS NOT NULL
 AND NOT EXISTS(SELECT 1 FROM entities WHERE id=NEW.object_ref)
 AND NOT EXISTS(SELECT 1 FROM persons WHERE id=NEW.object_ref)
 AND NOT EXISTS(SELECT 1 FROM person_occurrences WHERE id=NEW.object_ref)
 AND NOT EXISTS(SELECT 1 FROM events WHERE id=NEW.object_ref)
 AND NOT EXISTS(SELECT 1 FROM households WHERE id=NEW.object_ref)
 AND NOT EXISTS(SELECT 1 FROM places WHERE id=NEW.object_ref)
BEGIN
    SELECT RAISE(ABORT, 'claim object_ref does not resolve to a supported object');
END;
"""


PLACE_RESIDENCE_SCHEMA = """
ALTER TABLE participations
    ADD COLUMN residence_place_id TEXT REFERENCES places(id);
CREATE INDEX IF NOT EXISTS idx_participations_residence_place
    ON participations(residence_place_id);
"""


READING_PIPELINE_SCHEMA = """
CREATE TABLE IF NOT EXISTS reading_pipeline_runs (
    id TEXT PRIMARY KEY,
    source_item_id TEXT NOT NULL REFERENCES source_items(id),
    artifact_id TEXT NOT NULL REFERENCES artifacts(id),
    provider TEXT NOT NULL CHECK(trim(provider) <> ''),
    model TEXT NOT NULL CHECK(trim(model) <> ''),
    pipeline_version TEXT NOT NULL CHECK(trim(pipeline_version) <> ''),
    document_type TEXT NOT NULL CHECK(document_type IN (
        'birth-baptism','marriage','death-burial','census','index','other','unknown'
    )),
    source_language TEXT NOT NULL CHECK(trim(source_language) <> ''),
    input_sha256 TEXT NOT NULL CHECK(length(input_sha256) = 64),
    manifest_fingerprint TEXT NOT NULL,
    dependency_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('review_required','ready_for_curator')),
    completeness REAL NOT NULL CHECK(completeness >= 0 AND completeness <= 1),
    issues_json TEXT NOT NULL DEFAULT '[]',
    metrics_json TEXT NOT NULL DEFAULT '{}',
    provenance_json TEXT NOT NULL DEFAULT '{}',
    command_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    UNIQUE(source_item_id,artifact_id,manifest_fingerprint)
);

CREATE TABLE IF NOT EXISTS reading_pipeline_layers (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES reading_pipeline_runs(id),
    layer_type TEXT NOT NULL CHECK(layer_type IN (
        'transcription','transliteration','translation','extract'
    )),
    reading_id TEXT NOT NULL REFERENCES readings(id),
    reading_version_id TEXT NOT NULL REFERENCES reading_versions(id),
    language TEXT,
    status TEXT NOT NULL CHECK(status IN ('machine_proposal','needs_review')),
    created_at TEXT NOT NULL,
    UNIQUE(run_id,layer_type),
    UNIQUE(run_id,reading_version_id)
);

CREATE TABLE IF NOT EXISTS reading_pipeline_fields (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES reading_pipeline_runs(id),
    field_reading_id TEXT NOT NULL REFERENCES field_readings(id),
    field_path TEXT NOT NULL CHECK(trim(field_path) <> ''),
    role TEXT,
    normalized_value TEXT,
    confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
    source_quote TEXT,
    source_segment_id TEXT REFERENCES transcript_segments(id),
    region_json TEXT NOT NULL DEFAULT '{}',
    review_status TEXT NOT NULL CHECK(review_status IN (
        'machine_proposal','human_review_required','missing'
    )),
    created_at TEXT NOT NULL,
    UNIQUE(run_id,field_path)
);

CREATE TABLE IF NOT EXISTS reading_pipeline_escalations (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES reading_pipeline_runs(id),
    field_path TEXT,
    reason_code TEXT NOT NULL CHECK(reason_code IN (
        'missing_required_field','low_confidence','ambiguous_reading',
        'illegible_reading','missing_source_quote','missing_layer',
        'missing_locator','artifact_hash_mismatch'
    )),
    priority INTEGER NOT NULL CHECK(priority BETWEEN 0 AND 4),
    recommended_role TEXT NOT NULL CHECK(recommended_role IN (
        'blind_paleographer','contextual_reader','agent_adjudicator',
        'source_auditor','human_curator'
    )),
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(run_id,field_path,reason_code)
);

CREATE INDEX IF NOT EXISTS idx_reading_pipeline_runs_source
    ON reading_pipeline_runs(source_item_id,status,created_at);
CREATE INDEX IF NOT EXISTS idx_reading_pipeline_fields_review
    ON reading_pipeline_fields(review_status,run_id,field_path);
CREATE INDEX IF NOT EXISTS idx_reading_pipeline_escalations_run
    ON reading_pipeline_escalations(run_id,priority,reason_code);

CREATE TRIGGER IF NOT EXISTS reading_pipeline_runs_no_update
BEFORE UPDATE ON reading_pipeline_runs
BEGIN
    SELECT RAISE(ABORT, 'reading pipeline runs are append-only');
END;
CREATE TRIGGER IF NOT EXISTS reading_pipeline_runs_no_delete
BEFORE DELETE ON reading_pipeline_runs
BEGIN
    SELECT RAISE(ABORT, 'reading pipeline runs are append-only');
END;
CREATE TRIGGER IF NOT EXISTS reading_pipeline_layers_no_update
BEFORE UPDATE ON reading_pipeline_layers
BEGIN
    SELECT RAISE(ABORT, 'reading pipeline layers are append-only');
END;
CREATE TRIGGER IF NOT EXISTS reading_pipeline_layers_no_delete
BEFORE DELETE ON reading_pipeline_layers
BEGIN
    SELECT RAISE(ABORT, 'reading pipeline layers are append-only');
END;
CREATE TRIGGER IF NOT EXISTS reading_pipeline_fields_no_update
BEFORE UPDATE ON reading_pipeline_fields
BEGIN
    SELECT RAISE(ABORT, 'reading pipeline fields are append-only');
END;
CREATE TRIGGER IF NOT EXISTS reading_pipeline_fields_no_delete
BEFORE DELETE ON reading_pipeline_fields
BEGIN
    SELECT RAISE(ABORT, 'reading pipeline fields are append-only');
END;
CREATE TRIGGER IF NOT EXISTS reading_pipeline_escalations_no_update
BEFORE UPDATE ON reading_pipeline_escalations
BEGIN
    SELECT RAISE(ABORT, 'reading pipeline escalations are append-only');
END;
CREATE TRIGGER IF NOT EXISTS reading_pipeline_escalations_no_delete
BEFORE DELETE ON reading_pipeline_escalations
BEGIN
    SELECT RAISE(ABORT, 'reading pipeline escalations are append-only');
END;
"""


READING_PIPELINE_COMPARISON_SCHEMA = """
ALTER TABLE reading_pipeline_runs
    ADD COLUMN comparison_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE reading_pipeline_runs
    ADD COLUMN image_quality_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE reading_pipeline_runs
    ADD COLUMN role_expectations_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE reading_pipeline_runs
    ADD COLUMN independent_readings_json TEXT NOT NULL DEFAULT '[]';

DROP INDEX IF EXISTS idx_reading_pipeline_escalations_run;
DROP TRIGGER IF EXISTS reading_pipeline_escalations_no_update;
DROP TRIGGER IF EXISTS reading_pipeline_escalations_no_delete;
ALTER TABLE reading_pipeline_escalations
    RENAME TO reading_pipeline_escalations_v22;

CREATE TABLE reading_pipeline_escalations (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES reading_pipeline_runs(id),
    field_path TEXT,
    reason_code TEXT NOT NULL CHECK(reason_code IN (
        'missing_required_field','low_confidence','ambiguous_reading',
        'illegible_reading','missing_source_quote','missing_layer',
        'missing_locator','artifact_hash_mismatch',
        'independent_reading_disagreement','additional_name_disagreement',
        'expected_role_missing','image_region_obstructed',
        'literal_normalization_divergence','material_alternative_present'
    )),
    priority INTEGER NOT NULL CHECK(priority BETWEEN 0 AND 4),
    recommended_role TEXT NOT NULL CHECK(recommended_role IN (
        'blind_paleographer','contextual_reader','agent_adjudicator',
        'source_auditor','human_curator'
    )),
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(run_id,field_path,reason_code)
);

INSERT INTO reading_pipeline_escalations(
    id,run_id,field_path,reason_code,priority,recommended_role,details_json,created_at
)
SELECT id,run_id,field_path,reason_code,priority,recommended_role,details_json,created_at
  FROM reading_pipeline_escalations_v22;

DROP TABLE reading_pipeline_escalations_v22;

CREATE INDEX idx_reading_pipeline_escalations_run
    ON reading_pipeline_escalations(run_id,priority,reason_code);

CREATE TRIGGER reading_pipeline_escalations_no_update
BEFORE UPDATE ON reading_pipeline_escalations
BEGIN
    SELECT RAISE(ABORT, 'reading pipeline escalations are append-only');
END;
CREATE TRIGGER reading_pipeline_escalations_no_delete
BEFORE DELETE ON reading_pipeline_escalations
BEGIN
    SELECT RAISE(ABORT, 'reading pipeline escalations are append-only');
END;
"""


READING_PIPELINE_GROUNDING_ESCALATION_SCHEMA = """
DROP INDEX IF EXISTS idx_reading_pipeline_escalations_run;
DROP TRIGGER IF EXISTS reading_pipeline_escalations_no_update;
DROP TRIGGER IF EXISTS reading_pipeline_escalations_no_delete;
ALTER TABLE reading_pipeline_escalations
    RENAME TO reading_pipeline_escalations_v23;

CREATE TABLE reading_pipeline_escalations (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES reading_pipeline_runs(id),
    field_path TEXT,
    reason_code TEXT NOT NULL CHECK(reason_code IN (
        'missing_required_field','low_confidence','ambiguous_reading',
        'illegible_reading','missing_source_quote','missing_layer',
        'missing_locator','artifact_hash_mismatch',
        'independent_reading_disagreement','additional_name_disagreement',
        'expected_role_missing','image_region_obstructed',
        'literal_normalization_divergence','material_alternative_present',
        'weakly_grounded_agreement','language_formula_mismatch'
    )),
    priority INTEGER NOT NULL CHECK(priority BETWEEN 0 AND 4),
    recommended_role TEXT NOT NULL CHECK(recommended_role IN (
        'blind_paleographer','contextual_reader','agent_adjudicator',
        'source_auditor','human_curator'
    )),
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(run_id,field_path,reason_code)
);

INSERT INTO reading_pipeline_escalations(
    id,run_id,field_path,reason_code,priority,recommended_role,details_json,created_at
)
SELECT id,run_id,field_path,reason_code,priority,recommended_role,details_json,created_at
  FROM reading_pipeline_escalations_v23;

DROP TABLE reading_pipeline_escalations_v23;

CREATE INDEX idx_reading_pipeline_escalations_run
    ON reading_pipeline_escalations(run_id,priority,reason_code);

CREATE TRIGGER reading_pipeline_escalations_no_update
BEFORE UPDATE ON reading_pipeline_escalations
BEGIN
    SELECT RAISE(ABORT, 'reading pipeline escalations are append-only');
END;
CREATE TRIGGER reading_pipeline_escalations_no_delete
BEFORE DELETE ON reading_pipeline_escalations
BEGIN
    SELECT RAISE(ABORT, 'reading pipeline escalations are append-only');
END;
"""


SOURCE_REPORTED_NETWORK_ANCHOR_SCHEMA = """
CREATE TABLE IF NOT EXISTS source_reported_network_anchors (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES research_cases(id),
    component_fingerprint TEXT NOT NULL CHECK(trim(component_fingerprint) <> ''),
    anchor_occurrence_id TEXT NOT NULL REFERENCES person_occurrences(id),
    status TEXT NOT NULL CHECK(status IN ('active','superseded')),
    actor TEXT NOT NULL CHECK(trim(actor) <> ''),
    rationale TEXT NOT NULL CHECK(trim(rationale) <> ''),
    created_at TEXT NOT NULL CHECK(trim(created_at) <> ''),
    supersedes_anchor_id TEXT REFERENCES source_reported_network_anchors(id),
    CHECK(supersedes_anchor_id IS NULL OR supersedes_anchor_id <> id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_source_reported_network_anchor_active
    ON source_reported_network_anchors(case_id,component_fingerprint)
    WHERE status='active';
CREATE INDEX IF NOT EXISTS idx_source_reported_network_anchor_occurrence
    ON source_reported_network_anchors(anchor_occurrence_id,status);

CREATE TRIGGER IF NOT EXISTS source_reported_network_anchors_supersedes_scope
BEFORE INSERT ON source_reported_network_anchors
WHEN NEW.supersedes_anchor_id IS NOT NULL
 AND NOT EXISTS (
     SELECT 1
       FROM source_reported_network_anchors previous
      WHERE previous.id=NEW.supersedes_anchor_id
        AND previous.case_id=NEW.case_id
        AND previous.component_fingerprint=NEW.component_fingerprint
 )
BEGIN
    SELECT RAISE(ABORT, 'network anchor supersession scope mismatch');
END;

CREATE TRIGGER IF NOT EXISTS source_reported_network_anchors_restrict_update
BEFORE UPDATE ON source_reported_network_anchors
WHEN NOT (
    OLD.status='active' AND NEW.status='superseded'
    AND NEW.id IS OLD.id
    AND NEW.case_id IS OLD.case_id
    AND NEW.component_fingerprint IS OLD.component_fingerprint
    AND NEW.anchor_occurrence_id IS OLD.anchor_occurrence_id
    AND NEW.actor IS OLD.actor
    AND NEW.rationale IS OLD.rationale
    AND NEW.created_at IS OLD.created_at
    AND NEW.supersedes_anchor_id IS OLD.supersedes_anchor_id
)
BEGIN
    SELECT RAISE(ABORT, 'network anchor history is append-only');
END;

CREATE TRIGGER IF NOT EXISTS source_reported_network_anchors_no_delete
BEFORE DELETE ON source_reported_network_anchors
BEGIN
    SELECT RAISE(ABORT, 'network anchor history is append-only');
END;
"""


def _backfill_source_assertion_references(
    connection: sqlite3.Connection,
) -> dict[str, object]:
    created = 0
    now = _utc_now()
    object_tables = (
        ("entity", "entities"),
        ("person", "persons"),
        ("person_occurrence", "person_occurrences"),
        ("event", "events"),
        ("household", "households"),
        ("place", "places"),
        ("source_item", "source_items"),
    )
    for row in connection.execute(
        "SELECT id,subject_ref,object_text FROM source_assertions ORDER BY id"
    ).fetchall():
        for position, value in (("subject", row["subject_ref"]), ("object", row["object_text"])):
            if not value:
                continue
            matches = [
                object_type
                for object_type, table in object_tables
                if connection.execute(
                    f'SELECT 1 FROM "{table}" WHERE id=?', (value,)
                ).fetchone()
            ]
            if len(matches) != 1:
                continue
            connection.execute(
                """INSERT OR IGNORE INTO source_assertion_references(
                       id,assertion_id,position,object_type,object_id,created_at
                   ) VALUES(?,?,?,?,?,?)""",
                (
                    stable_id("assertion_ref", row["id"], position),
                    row["id"], position, matches[0], value, now,
                ),
            )
            created += 1
    return {"typed_source_assertion_references": created}


def _backfill_event_record_candidates(
    connection: sqlite3.Connection,
) -> dict[str, object]:
    from .event_records import detect_event_record_candidates

    return detect_event_record_candidates(
        connection,
        actor="migration:18:event-record-curation",
        detected_at=_utc_now(),
    )


def _optional_project_file_fts(connection: sqlite3.Connection) -> dict[str, int]:
    """Install a rebuildable text index without requiring SQLite's FTS5 module."""

    connection.execute("SAVEPOINT project_file_fts")
    try:
        connection.execute(
            """CREATE VIRTUAL TABLE project_files_fts USING fts5(
                path, extracted_text,
                content='project_files', content_rowid='rowid',
                tokenize='unicode61 remove_diacritics 2'
            )"""
        )
        connection.execute(
            """CREATE TRIGGER project_files_fts_ai
               AFTER INSERT ON project_files BEGIN
                 INSERT INTO project_files_fts(rowid,path,extracted_text)
                 VALUES(new.rowid,new.path,new.extracted_text);
               END"""
        )
        connection.execute(
            """CREATE TRIGGER project_files_fts_ad
               AFTER DELETE ON project_files BEGIN
                 INSERT INTO project_files_fts(project_files_fts,rowid,path,extracted_text)
                 VALUES('delete',old.rowid,old.path,old.extracted_text);
               END"""
        )
        connection.execute(
            """CREATE TRIGGER project_files_fts_au
               AFTER UPDATE ON project_files BEGIN
                 INSERT INTO project_files_fts(project_files_fts,rowid,path,extracted_text)
                 VALUES('delete',old.rowid,old.path,old.extracted_text);
                 INSERT INTO project_files_fts(rowid,path,extracted_text)
                 VALUES(new.rowid,new.path,new.extracted_text);
               END"""
        )
        connection.execute("INSERT INTO project_files_fts(project_files_fts) VALUES('rebuild')")
    except Exception as error:
        connection.execute("ROLLBACK TO project_file_fts")
        connection.execute("RELEASE project_file_fts")
        # SQLCipher uses a separate DB-API OperationalError class.
        if type(error).__name__ != "OperationalError" or "no such module: fts5" not in str(error).casefold():
            raise
        return {"project_files_fts5_enabled": 0}
    connection.execute("RELEASE project_file_fts")
    return {"project_files_fts5_enabled": 1}


ARCHIVE_REQUEST_SCHEMA = """
CREATE TABLE archive_requests (
    id TEXT PRIMARY KEY NOT NULL,
    case_id TEXT REFERENCES research_cases(id),
    title TEXT NOT NULL CHECK(length(trim(title)) > 0),
    repository TEXT NOT NULL CHECK(length(trim(repository)) > 0),
    recipient TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK(status IN ('draft','ready','sent','awaiting_reply','received','deferred')),
    readiness TEXT NOT NULL CHECK(readiness IN ('ready_for_inquiry','needs_details','needs_eligibility','online_first')),
    priority INTEGER NOT NULL CHECK(typeof(priority) = 'integer' AND priority >= 0),
    request_body TEXT NOT NULL DEFAULT '',
    instructions TEXT NOT NULL DEFAULT '',
    targets_json TEXT NOT NULL CHECK(json_valid(targets_json) AND json_type(targets_json) = 'array'),
    provenance_json TEXT NOT NULL CHECK(json_valid(provenance_json) AND json_type(provenance_json) = 'array'),
    missing_fields_json TEXT NOT NULL CHECK(json_valid(missing_fields_json) AND json_type(missing_fields_json) = 'array'),
    privacy TEXT NOT NULL CHECK(privacy IN ('private','strict_private')),
    response_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK(status NOT IN ('sent','awaiting_reply','received') OR length(trim(response_note)) > 0)
);
CREATE INDEX idx_archive_requests_priority ON archive_requests(priority, id);
CREATE INDEX idx_archive_requests_case ON archive_requests(case_id);
"""


MIGRATIONS = (
    Migration(1, "legacy_mvp_schema", LEGACY_SCHEMA),
    Migration(2, "evidence_and_research_schema", EVIDENCE_SCHEMA),
    Migration(
        3,
        "import_legacy_as_unverified",
        callback=_legacy_import,
        callback_revision="legacy-import-v3-source-occurrence-bridges-curation-2026-08-24",
    ),
    Migration(
        4,
        "optional_local_name_fts",
        callback=_optional_fts,
        callback_revision="fts-v2-contentless-folded-name-2026-08-24",
    ),
    Migration(
        5,
        "dismiss_variant_only_curation_items",
        callback=_dismiss_variant_only_curation,
        callback_revision="variant-only-curation-v1-2026-08-24",
    ),
    Migration(6, "pilot_review_and_privacy_controls", PILOT_SCHEMA),
    Migration(7, "research_question_priorities_and_targets", QUESTION_TARGET_SCHEMA),
    Migration(8, "source_browsing_checkpoints_and_paleography", BROWSE_RESEARCH_SCHEMA),
    Migration(9, "external_service_publication_tracking", EXTERNAL_SYNC_SCHEMA),
    Migration(10, "presentation_profile_and_relationship_publication_tracking", PRESENTATION_EXTERNAL_SYNC_SCHEMA),
    Migration(11, "private_data_fields_notes_attachments_and_audit", PRIVATE_DATA_SCHEMA),
    Migration(12, "multi_source_handwriting_profile_scope", HANDWRITING_PROFILE_SOURCE_SCHEMA),
    Migration(13, "best_available_rendition_gate", BEST_AVAILABLE_RENDITION_SCHEMA),
    Migration(14, "agent_first_reading_review", AGENT_FIRST_READING_REVIEW_SCHEMA),
    Migration(15, "reassessment_batches_and_mapping_foundation", REASSESSMENT_BATCH_SCHEMA),
    Migration(
        16,
        "hierarchical_research_scopes",
        HIERARCHICAL_RESEARCH_SCOPE_SCHEMA,
        callback=_backfill_hierarchical_research_scopes,
        callback_revision="hierarchical-research-scopes-v1-lossless-backfill-2026-08-31",
    ),
    Migration(
        17,
        "source_document_hierarchy_and_coverage",
        SOURCE_DOCUMENT_COVERAGE_SCHEMA,
    ),
    Migration(
        18,
        "event_record_curation",
        EVENT_RECORD_CURATION_SCHEMA,
        callback=_backfill_event_record_candidates,
        callback_revision="event-record-curation-v1-2026-09-16",
    ),
    Migration(
        19,
        "shared_curation_command_envelope",
        CURATION_COMMAND_ENVELOPE_SCHEMA,
    ),
    Migration(
        20,
        "research_question_candidates_and_integrity_guards",
        QUESTION_CANDIDATE_AND_INTEGRITY_SCHEMA,
        callback=_backfill_source_assertion_references,
        callback_revision="typed-source-assertion-refs-v1-2026-09-17",
    ),
    Migration(
        21,
        "participant_residence_place_reference",
        PLACE_RESIDENCE_SCHEMA,
    ),
    Migration(
        22,
        "scribe_inspired_reading_pipeline",
        READING_PIPELINE_SCHEMA,
    ),
    Migration(
        23,
        "independent_reading_comparison_contract",
        READING_PIPELINE_COMPARISON_SCHEMA,
    ),
    Migration(
        24,
        "reading_pipeline_grounding_escalations",
        READING_PIPELINE_GROUNDING_ESCALATION_SCHEMA,
    ),
    Migration(
        25,
        "source_reported_network_anchors",
        SOURCE_REPORTED_NETWORK_ANCHOR_SCHEMA,
    ),
    Migration(
        26,
        "optional_project_file_fts",
        callback=_optional_project_file_fts,
        callback_revision="project-file-fts-v1-external-content-triggers-2026-10-10",
    ),
    Migration(27, "private_archive_request_registry", ARCHIVE_REQUEST_SCHEMA),
)
LATEST_SCHEMA_VERSION = MIGRATIONS[-1].version


def _execute_script_transactional(connection: sqlite3.Connection, script: str) -> None:
    """Execute a SQL script statement-by-statement inside the caller's transaction."""

    statement = ""
    for character in script:
        statement += character
        if character == ";" and sqlite3.complete_statement(statement):
            sql = statement.strip()
            statement = ""
            if sql:
                connection.execute(sql)
    if statement.strip():
        raise sqlite3.OperationalError("incomplete SQL statement in migration")


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone() is not None


def apply_migrations(connection: sqlite3.Connection) -> list[int]:
    """Apply pending, checksum-verified migrations and return their versions."""

    if connection.row_factory is None:
        connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY, name TEXT NOT NULL,
            checksum TEXT NOT NULL, applied_at TEXT NOT NULL
        )"""
    )
    connection.commit()
    previous = {row["version"]: row for row in connection.execute("SELECT * FROM schema_migrations")}
    applied: list[int] = []
    for migration in MIGRATIONS:
        recorded = previous.get(migration.version)
        if recorded:
            if recorded["checksum"] != migration.checksum:
                raise RuntimeError(f"Migration {migration.version} checksum mismatch; refusing to continue")
            continue
        started = _utc_now()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if migration.sql:
                _execute_script_transactional(connection, migration.sql)
            records: dict[str, object] = {}
            if migration.callback:
                run_id = connection.execute(
                    "INSERT INTO data_migration_runs(migration_version, started_at, status) VALUES (?, ?, 'running')",
                    (migration.version, started),
                ).lastrowid
                records = migration.callback(connection)
                connection.execute(
                    """UPDATE data_migration_runs SET finished_at = ?, status = 'completed',
                       records_json = ? WHERE id = ?""",
                    (_utc_now(), json.dumps(records, sort_keys=True), run_id),
                )
            connection.execute(
                "INSERT INTO schema_migrations(version, name, checksum, applied_at) VALUES (?, ?, ?, ?)",
                (migration.version, migration.name, migration.checksum, _utc_now()),
            )
            connection.execute(f"PRAGMA user_version = {migration.version}")
            connection.commit()
            applied.append(migration.version)
        except Exception:
            connection.rollback()
            raise
    return applied


def _record_counts(connection: sqlite3.Connection) -> dict[str, int]:
    names = [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ) if not row[0].startswith("local_name_search_")]
    result: dict[str, int] = {}
    for name in names:
        try:
            result[name] = connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        except sqlite3.OperationalError:
            pass
    return result


def create_migration_snapshot(root: Path, destination: Path | None = None) -> dict[str, object]:
    """Explicitly create a database backup, record counts and SHA-256 manifest.

    No migration is run here, allowing a true pre-upgrade snapshot.
    """

    root = Path(root).resolve()
    base = ensure_workspace(root)
    backend = configured_database_backend(root)
    database = backend.path
    if not database.is_file():
        raise FileNotFoundError(f"Database does not exist: {database}")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot_dir = Path(destination).resolve() if destination else base / "backups" / timestamp
    snapshot_dir.mkdir(parents=True, exist_ok=False)
    backup_path = snapshot_dir / "genea.sqlite3"
    backend.backup(backup_path)
    with backend.connect(readonly=True) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = [list(row) for row in connection.execute("PRAGMA foreign_key_check")]
        counts = _record_counts(connection)
    files: list[dict[str, object]] = []
    backup_root = base / "backups"
    for path in iter_files(root):
        if path == database or backup_root in path.parents or ".git" in path.parts:
            continue
        files.append({"path": path.relative_to(root).as_posix(), "size": path.stat().st_size,
                      "sha256": sha256_file(path)})
    manifest = {
        "created_at": _utc_now(),
        "database": {"source_path": database.relative_to(root).as_posix(),
                     "backup_path": backup_path.name, "sha256": sha256_file(backup_path),
                     "integrity_check": integrity, "foreign_key_issues": foreign_keys,
                     "record_counts": counts},
        "files": files,
    }
    manifest_path = snapshot_dir / "manifest.json"
    dump_json(manifest_path, manifest)
    return {"snapshot_dir": str(snapshot_dir), "database_backup": str(backup_path),
            "manifest": str(manifest_path), "integrity_check": integrity,
            "foreign_key_issues": len(foreign_keys), "record_counts": counts,
            "file_count": len(files)}


__all__ = ["LATEST_SCHEMA_VERSION", "MIGRATIONS", "apply_migrations", "create_migration_snapshot"]
