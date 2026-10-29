export const VIEW_NAMES = ["tree", "map", "graph", "sources", "research"] as const;

export type ViewName = (typeof VIEW_NAMES)[number];

export type PersonIdentityScope = "canonical" | "hypothesis_group" | "presentation_only";

export const SOURCE_REPORTED_CANDIDATE_ROLES = [
  "all",
  "anchor",
  "direct_ancestor",
  "ancestor_spouse",
  "collateral",
  "descendant",
  "fan",
  "unclassified",
  "conflict",
] as const;

export type SourceReportedCandidateRole = (typeof SOURCE_REPORTED_CANDIDATE_ROLES)[number];

export type ExternalTreeProfileIdentityStatus = "unresolved" | "resolved_same";

export type IdentityDecisionTier = "agent_approved_human_pending" | "human_confirmed";

export interface SourceReportedMembership {
  networkId: string;
  treeNodeId: string;
  occurrenceId: string;
  classificationState: "classified" | "unclassified" | "conflict";
  role: Exclude<SourceReportedCandidateRole, "all" | "unclassified" | "conflict"> | null;
  unclassifiedReason?: "missing_anchor" | "masked_anchor" | "masked_path" | "disconnected" | "unsupported_relation" | "incomplete_materialization" | null;
}

export interface PresentedPersonContext {
  personKey: string;
  identityScope: PersonIdentityScope;
  canonicalPersonIds: string[];
  treePersonIds: string[];
  mapPersonIds: string[];
  graphIdentityIds: string[];
  occurrenceIds: string[];
  sourceIds: string[];
  publicProfileId: string;
  aliases: string[];
  label: string;
  status?: string | null;
  warnings?: string[];
  sourceReportedMemberships?: SourceReportedMembership[];
}

export interface PresentedNameReading {
  id: string;
  original: string;
  script: "cyrillic" | "latin" | "mixed" | "unknown" | string;
  transliteration?: string | null;
  transliterationBasis?: string | null;
  polishForm?: string | null;
  polishFormBasis?: string | null;
  reviewStatus: string;
  reviewBasis: "occurrence_verification_status" | string;
  confidence?: number | null;
  uncertain: boolean;
  occurrenceIds: string[];
  sourceIds: string[];
}

export interface PresentedPersonProfile {
  personKey: string;
  publicProfileId: string;
  identityScope: PersonIdentityScope;
  displayName: string;
  displayNameBasis: "canonical_preferred_name" | "presentation_label_not_identity_decision" | string;
  nameReadings: PresentedNameReading[];
  aliases: Array<{
    text: string;
    type?: string | null;
    language?: string | null;
    dateFrom?: string | null;
    dateTo?: string | null;
    personId: string;
  }>;
  methodNote: string;
}

export interface SourceArtifact {
  id: string;
  aliasIds?: string[];
  type?: string;
  parentArtifactId?: string | null;
  derived?: boolean;
  mediaType?: string;
  viewer: "image" | "pdf" | "text" | string;
  label: string;
  url: string;
}

export interface SourceReadingDifference {
  field?: string;
  blind?: string;
  contextual?: string;
  importance?: string;
  status?: string;
  note?: string;
  literalReading?: string;
  comparisonEvidence?: string;
  hypothesis?: string;
  identityDecision?: string;
}

export interface SourceReadingComparison {
  id: string;
  blindVersionId: string;
  contextualVersionId: string;
  reviewerVersionId?: string | null;
  differences: SourceReadingDifference[];
  status: string;
  createdAt?: string | null;
  reviewedAt?: string | null;
}

export interface SourceReviewField {
  field?: string;
  fieldPath?: string;
  status: string;
  value?: string | null;
  alternative?: string | null;
  context_only?: string | null;
  risk?: string | null;
  certainty?: string | null;
  [key: string]: unknown;
}

export interface SourceReviewWorkflow {
  id: string;
  revision: number;
  status: string;
  decidedByKind: "agent" | "human" | string;
  rationale: string;
  createdAt?: string | null;
  fields: SourceReviewField[];
  summary: {
    inputCount: number;
    blindInputCount: number;
    agentInputCount: number;
    fieldCount: number | null;
    agentApprovedFieldCount: number | null;
    humanReviewRequiredFieldCount: number | null;
    humanReviewedFieldCount: number | null;
    fieldReviewState?: "known" | "partial" | "unknown";
    singlePassFieldCount?: number | null;
    unmappedFieldCount?: number | null;
    hasAgentApproval: boolean;
    needsHumanReview: boolean;
  };
}

export interface SourceReadingPipelineEscalation {
  id?: string | null;
  fieldPath?: string | null;
  reasonCode: string;
  priority?: number | null;
  recommendedRole?: string | null;
  details?: Record<string, unknown>;
}

export type ReadingFieldComparisonStatus = "agreed" | "disputed" | "incomplete" | "not_compared";

export interface SourceReadingVerificationBlindReading {
  readingId: string | null;
  readerId: string | null;
  value: string | null;
  normalizedValue?: string | null;
  certainty?: string | null;
  contextDisclosed?: boolean;
}

export interface SourceReadingVerificationField {
  path: string;
  role: string | null;
  criticality: "standard" | "critical" | string | null;
  proposal: {
    value: string | null;
    normalizedValue: string | null;
    confidence: number | null;
    certainty: string | null;
    alternatives: string[];
    materialAlternatives: string[];
    sourceQuote: string | null;
  };
  blindReadings: SourceReadingVerificationBlindReading[];
  comparison: {
    status: ReadingFieldComparisonStatus;
    differenceKind?: string;
    values: Array<{ readingId: string | null; readerId: string | null; value: string | null }>;
  };
  workflow: {
    status: "machine_proposal" | "missing" | "agent_consensus" | "agent_adjudicated" | "human_review_required" | "human_reviewed";
    basis: "pipeline_assessment" | "review_decision";
    decisionId?: string;
    revision?: number;
  };
  issues: SourceReadingPipelineEscalation[];
  backing: {
    sourceItemId: string;
    artifactId: string;
    sourceSegmentId?: string;
    bbox?: { x: number; y: number; width: number; height: number } | null;
  };
}

export interface SourceReadingVerificationCard {
  schema: "genea.reading-verification-card/v1";
  summary: {
    fieldCount: number;
    agreedCount: number;
    disputedCount: number;
    incompleteCount: number;
    notComparedCount: number;
    humanReviewRequiredCount: number;
  };
  fields: SourceReadingVerificationField[];
  issues: SourceReadingPipelineEscalation[];
}

export interface SourceReadingPipelineRun {
  id: string;
  artifactId: string;
  provider: string;
  model: string;
  pipelineVersion: string;
  documentType: string;
  sourceLanguage: string;
  status: "review_required" | "ready_for_curator" | string;
  completeness: number;
  metrics?: Record<string, unknown>;
  provenance?: Record<string, unknown>;
  comparison?: {
    readingIds?: string[];
    fieldCount?: number;
    agreementCount?: number;
    disagreementCount?: number;
    missingInSomeCount?: number;
    fields?: Array<Record<string, unknown>>;
  };
  imageQuality?: Record<string, unknown>;
  roleExpectations?: Array<Record<string, unknown>>;
  independentReadings?: Array<Record<string, unknown>>;
  commandId: string;
  createdAt?: string | null;
  summary: {
    fieldCount: number;
    humanReviewRequiredFieldCount: number;
    missingFieldCount: number;
    escalationCount: number;
    independentReadingCount?: number;
    disagreementCount?: number;
  };
  escalations: SourceReadingPipelineEscalation[];
  verificationCard?: SourceReadingVerificationCard;
}

export interface SourceSegment {
  sequence: number;
  page?: string | null;
  language?: string | null;
  text: string;
}

export interface SourceReading {
  id: string;
  readingId?: string;
  type: string;
  version: number | string;
  status?: string | null;
  readingStatus?: string | null;
  author?: string | null;
  createdAt?: string | null;
  segments: SourceSegment[];
  inheritedFromSourceId?: string;
}

export interface SourceCoverageAssessment {
  id: string;
  key: string;
  revision: number;
  dimension: "inventory" | "index" | "content_review" | "extraction" | "transcription" | "review" | string;
  level: string;
  recordType?: string | null;
  scope: string;
  unitKind: string;
  completed?: number | null;
  total?: number | null;
  status: "unknown" | "not_started" | "partial" | "complete" | "blocked" | "not_applicable" | string;
  systematic: boolean;
  qaStatus: string;
  basisType: string;
  basisRef?: string | null;
  rationale: string;
}

export interface SourceRecordSummary {
  sourceType?: string | null;
  typeLabel?: string;
  eventGroups: string[];
  eventTypes: string[];
  date?: string | null;
  dateBasis?: "source_item.date_text" | "linked_event.date_from" | string | null;
  recordNumber?: string | null;
  recordNumberBasis?: "source_item.metadata" | "source_item.title" | string | null;
  places: string[];
  repository?: string | null;
  canonicalLocator?: string | null;
  citationLocators: string[];
  eventCount: number;
  participantCount: number;
  missingCanonicalFields: string[];
}

export interface PresentationSource {
  id: string;
  parentId?: string | null;
  childIds?: string[];
  title: string;
  type?: string | null;
  date?: string | null;
  locator?: string | null;
  url?: string | null;
  citation?: string | null;
  quality?: string | null;
  status?: string | null;
  metadata?: Record<string, unknown>;
  recordSummary?: SourceRecordSummary;
  availableLocally?: boolean;
  artifactCount?: number;
  artifacts?: SourceArtifact[];
  citations?: Array<{ id?: string; text?: string | null; locator?: string | null; accessedAt?: string | null }>;
  readings: SourceReading[];
  coverage?: SourceCoverageAssessment[];
  comparisons?: SourceReadingComparison[];
  reviewWorkflow?: SourceReviewWorkflow;
  readingPipelineRuns?: SourceReadingPipelineRun[];
}

export type PresentationVitalStatusCode = "living" | "deceased" | "unknown" | string;

export interface PresentationVitalStatus {
  code: PresentationVitalStatusCode;
  label?: string | null;
  status?: string | null;
  sourceAssertionId?: string | null;
}

export interface PresentationEventParticipant {
  occurrenceId?: string | null;
  personId?: string | null;
  role?: string | null;
  age?: string | null;
  occupation?: string | null;
  residence?: string | null;
  statusText?: string | null;
  vitalStatus?: PresentationVitalStatus | PresentationVitalStatusCode | null;
  vitalStatusSourceAssertionId?: string | null;
  sourceAssertionId?: string | null;
  provenance?: "source_assertion" | string | null;
  personStatus?: string | null;
  identityStatus?: string | null;
  candidatePersonStatus?: string | null;
  personIdentityResolved?: boolean | null;
  [key: string]: unknown;
}

export type PresentationTemporalRelation = "before" | "on_or_before" | "after" | "on_or_after" | string;

export interface PresentationTemporalConstraint {
  id: string;
  occurrenceId: string;
  personId?: string | null;
  sourceId: string;
  eventId: string;
  sourceAssertionId?: string | null;
  vitalEventType: "death" | string;
  relation: PresentationTemporalRelation;
  boundaryDate: string;
  label?: string | null;
  status?: string | null;
}

export interface ResearchTarget {
  type?: string;
  id?: string;
  label: string;
  primary?: boolean;
  role?: string;
  semantics?: "research_context" | "source_recorded" | "hypothesis" | string;
  dateFrom?: string | null;
  dateTo?: string | null;
}

export interface ResearchScopePathPart {
  id: string;
  title: string;
  type: string;
}

export interface ResearchScopeMembership {
  id: string;
  type: string;
  title: string;
  status?: string;
  legacyCaseId?: string | null;
  role?: "primary" | "supporting" | "context" | string;
  primary?: boolean;
  primaryPath?: ResearchScopePathPart[];
  targets?: ResearchTarget[];
}

export interface ResearchScope extends ResearchScopeMembership {
  summary?: string | null;
}

export interface ResearchScopeEdge {
  parentScopeId: string;
  childScopeId: string;
  relationType: "contains" | "related" | "continues" | "overlaps" | string;
  primary?: boolean;
  sortOrder?: number;
  notes?: string | null;
}

export interface ResearchProgress {
  score?: number;
  meaning?: string;
  gates?: string[];
  dimensions?: Array<{ label: string; done?: boolean }>;
}

export interface ResearchArchiveQuery {
  id: string;
  provider: string;
  status: "ready" | "needs_stable_locator" | "nice_to_have" | string;
  executable: boolean;
  specification: Record<string, unknown>;
  basis: string[];
  gate?: string;
  warning: string;
}

export interface ResearchExecutionBatch {
  id: string;
  batchKey: string;
  caseId?: string | null;
  questionId: string;
  status: string;
  inputFingerprint: string;
  stoppingRule: string;
  createdAt: string;
  itemStates: Record<string, number>;
  reviewDecisions: Record<string, number>;
}

export interface ResearchExecutionPlan {
  schemaVersion: number;
  questionId: string;
  inputBasis: Record<string, unknown>;
  archiveQueries: ResearchArchiveQuery[];
  batches: ResearchExecutionBatch[];
  activeGate: string;
  automaticDiscovery: false;
  automaticAcquisition: false;
  automaticWriteback: false;
}

export type ResearchAssessmentDisposition =
  | "defer_specific_material"
  | "identity_linked_biography_partial"
  | "ready_known_source_followup"
  | "source_supported_probable";

export const researchAssessmentDispositionLabels: Record<ResearchAssessmentDisposition, string> = {
  defer_specific_material: "Odroczone do uzyskania wskazanego materiału",
  identity_linked_biography_partial: "Tożsamość powiązana · biografia częściowa",
  ready_known_source_followup: "Gotowe do dalszej pracy ze znanym źródłem",
  source_supported_probable: "Prawdopodobne na podstawie źródła",
};

export interface ResearchQuestionAssessment {
  schemaVersion: 1;
  disposition: ResearchAssessmentDisposition;
  missingEvidence: string;
  nextAction: string;
  returnCondition: string;
  source: {
    logEntryId: string;
    assessmentVersion: 60 | 69;
    assessedAt: string;
    summarySha256: string;
  };
  scopedWork: {
    id: string;
    itemType: string;
    status: string;
    scopeId: string;
    parentWorkItemId: string | null;
  };
}

// Validate the optional projection before either view presents curator semantics.
// Canonical freshness, binding and privacy remain the projection's responsibility.
export function parseResearchQuestionAssessment(value: unknown): ResearchQuestionAssessment | null {
  const object = (input: unknown): Record<string, unknown> | null =>
    input !== null && typeof input === "object" && !Array.isArray(input) ? input as Record<string, unknown> : null;
  const nonempty = (input: unknown): input is string => typeof input === "string" && input.trim().length > 0;
  const id = (input: unknown): input is string => typeof input === "string" && /^[A-Za-z0-9][A-Za-z0-9_.:-]*$/.test(input);
  const assessment = object(value);
  if (!assessment || assessment.schemaVersion !== 1 || typeof assessment.disposition !== "string"
    || !Object.hasOwn(researchAssessmentDispositionLabels, assessment.disposition)
    || ![assessment.missingEvidence, assessment.nextAction, assessment.returnCondition].every(nonempty)) return null;
  const source = object(assessment.source);
  const work = object(assessment.scopedWork);
  if (!source || !work || !id(source.logEntryId) || ![60, 69].includes(source.assessmentVersion as number)
    || typeof source.assessedAt !== "string" || !Number.isFinite(Date.parse(source.assessedAt))
    || typeof source.summarySha256 !== "string" || !/^[a-fA-F0-9]{64}$/.test(source.summarySha256)
    || !id(work.id) || !id(work.scopeId) || !nonempty(work.itemType) || !nonempty(work.status)
    || (work.parentWorkItemId !== null && !id(work.parentWorkItemId))) return null;
  return assessment as unknown as ResearchQuestionAssessment;
}

export interface ResearchBacklogItem {
  id: string;
  origin: "curated" | "deterministic" | string;
  caseId: string;
  caseTitle: string;
  scopeIds?: string[];
  scopes?: ResearchScopeMembership[];
  primaryScopeId?: string | null;
  primaryScopeTitle?: string | null;
  primaryScopeType?: string | null;
  primaryScopePath?: ResearchScopePathPart[];
  question: string;
  completionCriterion?: string;
  answerStatus?: string;
  assessment?: ResearchQuestionAssessment | null;
  workflowStage: string;
  priority: number;
  targets?: ResearchTarget[];
  progress?: ResearchProgress;
  nextAction?: string | null;
  potentialDuplicateQuestionIds?: string[];
  metrics?: {
    logs?: number;
    coverageAssessments?: number;
    completeCoverageAssessments?: number;
    openActions?: number;
  };
  recentResearch?: Array<{
    id: string;
    query: string;
    repository?: string | null;
    scope: string;
    outcome: string;
    summary?: string | null;
    searchedAt: string;
    coverage?: {
      id: string;
      appropriate: boolean;
      complete: boolean;
      systematic: boolean;
      assessment?: string | null;
    };
  }>;
  executionPlan?: ResearchExecutionPlan;
}

export interface ResearchCase {
  id: string;
  title: string;
  status: string;
  summary: string | null;
  nextActions: Array<{ id: string; action: string; priority: number; status: "open" }>;
}

export interface ResearchBacklog {
  items: ResearchBacklogItem[];
  cases?: ResearchCase[];
  workflowStages: string[];
  scopeIds?: string[];
  scopes?: ResearchScope[];
  scopeEdges?: ResearchScopeEdge[];
  summary?: { total?: number; candidates?: number };
  execution?: {
    schemaVersion: number;
    mode: string;
    providers: Array<Record<string, unknown>>;
    batches: ResearchExecutionBatch[];
    summary: { questions: number; readyQueries: number; linkedBatches: number; automaticWrites: number };
    methodNote: string;
  };
}

export interface VolumeIndexCandidate {
  regionId: string;
  ordinal: number;
  locator: string;
  bbox?: number[] | null;
  rawText: string;
  confidence?: number | null;
  engine: string;
  model?: string | null;
  reviewDecision?: string | null;
  correctedText?: string | null;
  reviewNote?: string | null;
  reviewer?: string | null;
  renderUrl: string;
  width: number;
  height: number;
}

export interface VolumeIndexSummary {
  id: string;
  label: string;
  classification: "public" | "historical";
  status: string;
  unitCount?: number;
  regionCount?: number;
  attemptCount?: number;
  reviewedCount?: number;
  candidateCount?: number;
  scope?: string;
  warning?: string;
  candidates: VolumeIndexCandidate[];
  runs?: VolumeRunSummary[];
}

export type TelemetryProvenance = "measured" | "estimated" | "unknown";

export interface TelemetryValue<T = number> {
  value?: T | null;
  provenance: TelemetryProvenance;
  note?: string | null;
}

export interface VolumeRunSummary {
  id: string;
  volumeId?: string;
  volumeLabel?: string;
  unitId?: string | null;
  parentRegionId?: string | null;
  backend: string;
  backendVersion?: string | null;
  model?: string | null;
  configHash?: string | null;
  createdAt?: string | null;
  status: string;
  durationMs?: TelemetryValue;
  tokens?: TelemetryValue;
  images?: TelemetryValue;
  cost?: TelemetryValue;
  quality?: Record<string, TelemetryValue>;
}

export interface PresentationClaim {
  id?: string;
  status?: string;
  statement: string;
  rationale?: string | null;
  caseId?: string | null;
  predicate?: string | null;
}

export interface PresentationConflict {
  id?: string;
  status?: string;
  title: string;
  description: string;
  claimId?: string | null;
  [key: string]: unknown;
}

export interface MapObservation {
  id?: string;
  personId?: string;
  personLabel: string;
  personLifeSpanLabel?: string;
  occurrenceId?: string | null;
  occurrenceLabel?: string | null;
  rawName?: string | null;
  eventId?: string | null;
  placeId?: string | null;
  reason?: "missing_place_reference" | "place_not_exported" | "place_missing_coordinates" | string;
  eventType?: string;
  dateFrom?: string | null;
  yearFrom?: number | null;
  kind?: string;
  role?: string | null;
  placeText?: string | null;
  sourceId?: string | null;
  [key: string]: unknown;
}

export interface MapSourceObservation {
  id?: string;
  placeId: string;
  kind?: string;
  dateFrom?: string | null;
  dateTo?: string | null;
  yearFrom?: number | null;
  yearTo?: number | null;
  eventId?: string | null;
  eventType?: string | null;
  eventStatus?: string | null;
  sourceId?: string | null;
  occurrenceId?: string | null;
  occurrenceLabel?: string | null;
  rawName?: string | null;
  role?: string | null;
  readingCertainty?: string | null;
  personId?: never;
  personLabel?: never;
  personLifeSpanLabel?: never;
  lineIds?: never;
  [key: string]: unknown;
}

export interface MapInspection {
  place?: { id?: string; label?: string; precision?: string | boolean; isRegionCluster?: boolean; localityCount?: number; [key: string]: unknown } | null;
  items: Array<MapObservation | MapSourceObservation>;
  lines?: Array<{ id?: string; label: string; color?: string }>;
  route?: {
    personLabel: string;
    from: { yearFrom?: number | null };
    to: { yearFrom?: number | null };
  } | null;
}

export interface GraphInspectorModel {
  empty?: boolean;
  glyph?: string;
  kindLabel?: string;
  status?: string;
  statusKind?: string;
  title: string;
  subtitle: string;
  sections?: Array<{ title: string; rows: Array<[string, string | number | null | undefined]> }>;
  candidates?: IdentityReviewCandidate[];
  review?: { state: string; label: string } | null;
  appearances?: Array<{ id: string; label: string; literalName?: string; source: string; sourceId?: string | null; sortKey?: string; review?: { state: string; label: string } | null }>;
  participants?: Array<{ role: string; name: string; details?: string; original?: string | null }>;
  sourceId?: string | null;
  actionLabel?: string | null;
  externalProfileHref?: string | null;
  approvedActMaterials?: Array<{ sourceId: string; title: string; occurrenceIds: string[] }>;
  approvedActMaterialSummary?: string;
  identityPersonId?: string | null;
  identityExpanded?: boolean;
  identityActionLabel?: string | null;
  evidencePersonId?: string | null;
  evidenceActionLabel?: string | null;
  evidenceGraphVisible?: boolean;
  evidenceEventId?: string | null;
  evidenceEventExpanded?: boolean;
  evidenceEventActionLabel?: string | null;
  clusterId?: string | null;
  clusterExpanded?: boolean;
  clusterActionLabel?: string | null;
  searchMatch?: {
    title: string;
    label: string;
    detail?: string | null;
    occurrenceId?: string | null;
  } | null;
}

export interface IdentityReviewCandidate {
  id: string;
  status: string;
  fromOccurrenceId: string;
  toOccurrenceId: string;
  label: string;
  fromLabel?: string;
  toLabel?: string;
  score: string;
  scoreValue?: number | null;
  highPriority: boolean;
  basis: string;
}

export interface PresentationIdentityCandidate {
  id: string;
  fromOccurrenceId: string;
  toOccurrenceId: string;
  fromPersonId?: string | null;
  toPersonId?: string | null;
  fromLabel: string;
  toLabel: string;
  score?: number | null;
  status: string;
  basis?: string | null;
  scoreSemantics?: string | null;
  candidateBundleId?: string | null;
  support?: string[];
  against?: string[];
  humanReviewDebt?: string[];
  highPriority?: boolean;
}

export interface PresentationIdentityGroup {
  id: string;
  personId: string;
  label: string;
  status: string;
  occurrenceIds: string[];
  occurrenceCount: number;
  lifeSpanLabel?: string;
  lifeSpan?: Record<string, unknown> | null;
}

export interface SourceIdentityArtifactRef {
  artifactPath: string;
  artifactSha256: string;
}

export interface SourceIdentityDirectEdge {
  candidateId: string;
  occurrenceIds: [string, string];
  resolutionCommandId: string;
  ratificationCommandId: string;
  reviewRefs: {
    strict: SourceIdentityArtifactRef & { reviewId: string };
    agent: SourceIdentityArtifactRef;
    independent: Array<SourceIdentityArtifactRef & {
      artifactId: string;
      reviewerRole: "source_auditor" | "evidence_correlator";
      reviewerRunId: string;
      reviewedCandidateIds: string[];
      verdict: string;
    }>;
    ratification: SourceIdentityArtifactRef;
  };
  humanReviewDebt: Record<string, unknown>;
}

export interface PresentationSourceIdentityGroup extends Omit<SourceIdentityDirectEdge, "occurrenceIds"> {
  id: string;
  occurrenceIds: string[];
  sourceItemIds: string[];
  personId: null;
  identityDecisionTier: "agent_approved_human_pending";
  directEdges?: SourceIdentityDirectEdge[];
  introducedCandidateIds?: string[];
  lineage?: {
    version: 1;
    seedResolutionCommandId: string;
    headCommandId: string;
    headResultSha256: string;
    predecessorCommandId: string;
    predecessorResultSha256: string;
  };
}

export interface CuratorAccess {
  available: boolean;
  authenticated: boolean;
  strict: boolean;
  csrf: string;
}

export interface FamilyKnowledge {
  status: "family_known";
  provenanceKind: "family_testimony";
  basis: "user_reported_family_conversations";
  confidenceDeclared: "100%";
  confidenceSemantics: "user_declared";
  recognitionScope: "family_person";
  provenanceRef: string;
}

export interface FamilyKnowledgePresentation {
  label: string;
  badgeLabel: string;
  confidenceLabel: string;
  basisLabel: string;
  notice: string;
}

export interface TreeInspectorModel {
  personId: string;
  identityScope: PersonIdentityScope;
  identityLabel: string;
  identityNotice?: string | null;
  familyKnown?: FamilyKnowledgePresentation | null;
  localIdentityReviews?: Array<{
    decisionTier: IdentityDecisionTier;
    label: string;
    humanReviewLabel: string;
    humanReviewOpen: boolean;
    occurrenceIds: string[];
  }>;
  identityComponentCount: number;
  generation: string;
  evidence: string;
  evidenceKind: string;
  name: string;
  fullRecordHref?: string | null;
  suppressFullRecordLink?: boolean;
  summary: string;
  lifeSpanLabel: string;
  review?: { state: string; label: string } | null;
  structureActions: Array<{ label: string; run: () => void }>;
  family: Record<"parents" | "coParents" | "children" | "siblings", Array<{
    id: string;
    familyId: string;
    treePersonId: string;
    personId: string;
    personName: string;
    lifeSpanLabel: string;
    relationLabel: string;
    status: string;
    statusLabel: string;
    identityScope: PersonIdentityScope;
    identityNotice?: string | null;
    sources: Array<{ id: string; title: string }>;
    focus: () => void;
  }>>;
  unions: Array<{
    id: string;
    partnerName: string;
    status: string;
    statusClass: string;
    statusKind: string;
    details: string;
    canChoose: boolean;
    isActive: boolean;
    focus: () => void;
    choose: () => void;
    sources: Array<{ id: string; title: string }>;
  }>;
  sourceRelations: Array<{
    id: string;
    relationLabel: string;
    personName: string;
    personId?: string | null;
    lifeSpanLabel: string;
    personIdentityResolved: boolean;
    relationshipCanonical: boolean;
    resolutionLabel: string;
    sources: Array<{
      id: string;
      sourceId?: string | null;
      sourceTitle: string;
      relationshipIds: string[];
      occurrenceIds: string[];
      eventIds: string[];
      statuses: string[];
      readingCertainties: string[];
      literals: string[];
      provenance: string[];
      derivationStatuses: string[];
    }>;
  }>;
  candidates: Array<{
    id: string;
    sourceId: string;
    sourceTitle: string;
    occurrenceLabel: string;
    rolesLabel: string;
    basis: string;
    support: string[];
    against: string[];
    humanReviewDebt: string[];
    status: string;
    priorityLabel: string;
    scoreSemantics: string;
    review?: { state: string; label: string } | null;
  }>;
  appearances: Array<{
    sourceId: string;
    sourceTitle: string;
    type: string;
    occurrenceLabel: string;
    context: string;
    rolesLabel: string;
    roles: string[];
    occurrenceIds: string[];
    eventIds: string[];
    review?: { state: string; label: string } | null;
  }>;
  appearanceSummary: string;
  facts: Array<{
    label: string;
    value: string;
    place?: string | null;
    unresolved: boolean;
    identityAmbiguous?: boolean;
    sourceId?: string | null;
  }>;
  materials: Array<{ id: string; title: string }>;
  unavailableCount: number;
  materialTitle: string;
  materialNote?: string | null;
  importantFacts: Array<{
    label: string;
    value: string;
    place?: string | null;
    sourceId?: string | null;
  }>;
  alerts: Array<{
    id: string;
    kind: string;
    label: string;
    detail: string;
    sourceId?: string | null;
  }>;
  uniqueSources: Array<{
    sourceId: string;
    title: string;
    shortLabel: string;
    occurrenceIds: string[];
    eventIds: string[];
    roles: string[];
    origins: string[];
    review?: { state: string; label: string } | null;
  }>;
  tabs: Array<{ id: "summary" | "family" | "acts" | "research"; label: string; count: number }>;
}

export interface PresentationCounts {
  people: number;
  personContexts?: number;
  personProfiles?: number;
  occurrences: number;
  events: number;
  places?: number;
  relationships: number;
  sourceReportedNetworks?: number;
  identityCandidates: number;
  households: number;
  sources: number;
  claims: number;
  conflicts: number;
  researchBacklog: number;
  mapPlaces: number;
  mapObservations: number;
  volumeIndexes?: number;
}

export interface SourceReportedNetworkNode {
  treeNodeId: string;
  publicProfileId?: string;
  personId: string;
  occurrenceId: string;
  label: string;
  status: "candidate";
  identityStatus: ExternalTreeProfileIdentityStatus;
  profileIdentityStatus?: ExternalTreeProfileIdentityStatus;
  identityDecisionTier?: IdentityDecisionTier;
  relationshipIdentityStatus?: "unresolved";
  personIdentityResolved?: boolean;
  familyKnowledge?: FamilyKnowledge | null;
  generation: number;
  order: number;
  classification?: {
    state: "classified" | "unclassified" | "conflict";
    primaryRole: Exclude<SourceReportedCandidateRole, "all" | "unclassified" | "conflict"> | null;
    alternatives: Array<{
      role: Exclude<SourceReportedCandidateRole, "all" | "unclassified" | "conflict">;
      basis: "derived_from_explicit_source_path";
      orderedPath: Array<{
        relationshipId: string;
        direction: string;
      }>;
      backingRelationshipIds: string[];
    }>;
    unclassifiedReason: "missing_anchor" | "masked_anchor" | "masked_path" | "disconnected" | "unsupported_relation" | "incomplete_materialization" | null;
  };
}

export interface SourceReportedNetworkEdge {
  treeEdgeId: string;
  relationshipId: string;
  fromTreeNodeId: string;
  toTreeNodeId: string;
  type: string;
  identityStatus?: "unresolved";
  relationshipIdentityStatus?: "unresolved";
  canonicalRelationship: false;
}

export interface SourceReportedNetwork {
  id: string;
  kind: "external_tree_candidate";
  label: string;
  status: "imported_unverified";
  identityStatus: "unresolved";
  relationshipIdentityStatus?: "unresolved";
  canonicalRelationship: false;
  automaticMerge: false;
  privacyFiltered?: true;
  materializationState?: "complete" | "incomplete";
  materializationGap?: null | {
    id?: string;
    kind: "incomplete_materialization";
    status: "open" | string;
    automaticPersonCreation: false;
  };
  anchorState?: "visible" | "missing" | "masked";
  anchor?: null | {
    treeNodeId: string;
    publicProfileId: string;
    label: string;
  };
  counts?: {
    visibleProfiles: number;
    visibleRelationships: number;
  };
  personIds: string[];
  occurrenceIds: string[];
  relationshipIds: string[];
  nodes: SourceReportedNetworkNode[];
  edges: SourceReportedNetworkEdge[];
}

export interface ConditionalTreeAttachment {
  id: string;
  networkId: string;
  fromTreeNodeId: string;
  toTreePersonId: string;
  kind: "external_profile_identity_attachment";
  identityStatus: "resolved_same";
  identityDecisionTier: "agent_approved_human_pending" | "human_confirmed";
  canonicalRelationship: false;
  relationshipIdentityStatus: "unresolved";
  automaticMerge: false;
}

export interface ConditionalAncestryProvenance {
  kind: string;
  sourceIds: string[];
  sourceNetworkId?: string;
  backingRelationshipIds?: string[];
  backingAssertionIds?: string[];
  backingEventIds?: string[];
  backingOccurrenceIds?: string[];
  backingSegmentIds?: string[];
  backingHypothesisIds?: string[];
}

export interface ConditionalAncestryNode {
  id: string;
  label: string;
  treePersonId?: string | null;
  publicProfileId?: string | null;
  occurrenceId?: string | null;
  identityScope: string;
  scope: string;
  networkId?: string;
  sourceIds?: string[];
}

export interface ConditionalAncestryEdge {
  id: string;
  fromNodeId: string;
  toNodeId: string;
  direction: "child_to_parent";
  relationKind: "source_stated" | "parentage_hypothesis";
  status: "unresolved";
  canonicalRelationship: false;
  sourceStatus?: string;
  verificationStatus?: string;
  identityStatus?: string;
  provenance: ConditionalAncestryProvenance;
  sourceIds: string[];
  backingRelationshipIds?: string[];
  backingEventIds?: string[];
  backingOccurrenceIds?: string[];
}

export interface ConditionalAncestryRootAudit {
  rootId: string;
  visitedNodeCount: number;
  visitedEdgeCount: number;
  maxDepthReached: number;
  complete: boolean;
  stopReasons: string[];
  frontierCount: number;
}

export interface ConditionalAncestry {
  version: 1;
  roots: Array<{ id: string; nodeId: string; treePersonId?: string | null; label?: string; scope: string; networkId?: string }>;
  nodes: ConditionalAncestryNode[];
  edges: ConditionalAncestryEdge[];
  gaps: Array<{ id: string; rootId?: string; nodeId?: string; kind: string; label?: string; condition?: Record<string, unknown> }>;
  audit: {
    inputFingerprint: string;
    rootCount: number;
    nodeCount: number;
    edgeCount: number;
    gapCount: number;
    allRootsComplete: boolean;
    snapshotTraversalComplete: boolean;
    contentCoverage: "not_assessed";
    negativeEvidence: "not_established";
    truncated: boolean;
    limits: Record<string, number>;
    excludedByReason: Record<string, number>;
    rootAudits: ConditionalAncestryRootAudit[];
  };
}

export interface PresentationFamilyEvidence {
  basis: "source_assertion" | "claim" | "mixed";
  status: "source_reported";
  tier: string;
  claimStatuses: string[];
  verificationStatuses: string[];
  reviewStatuses: string[];
  evidenceTypes?: string[];
  backingRefs: Array<{ entityType: string; entityId: string }>;
  backingReferenceCounts: Record<string, number>;
  evidenceRedacted: boolean;
  auditFingerprint: string;
}

export interface PresentationFamily {
  id: string;
  parentIds: string[];
  childIds: string[];
  status?: string;
  sourceIds?: string[];
  canonicalRelationship?: boolean;
  relationshipIdentityStatus?: string;
  evidence?: PresentationFamilyEvidence;
  familyKnowledgeStatus?: "family_known";
  familyKnowledgeEdges?: Array<{
    parentId: string;
    childId: string;
    familyKnowledgeStatus: "family_known";
    basis: "family_testimony";
    summary: string;
  }>;
  relationshipEvidence?: Array<{
    parentId: string;
    childId: string;
    canonicalRelationship: false;
    relationshipIdentityStatus: "unresolved";
    sourceIds: string[];
    sourceAssertionIds: string[];
    claimIds: string[];
    parentOccurrenceId: string | null;
    childOccurrenceId: string | null;
    evidence: PresentationFamilyEvidence;
  }>;
  [key: string]: unknown;
}

export interface PresentationFamilyTree {
  people?: unknown[];
  branches?: unknown[];
  families?: PresentationFamily[];
  unions?: unknown[];
  focusPath?: string[];
  rootPersonId?: string;
  title?: string;
  subtitle?: string;
  sourceReportedNetworks?: SourceReportedNetwork[];
  conditionalTreeAttachments?: ConditionalTreeAttachment[];
  conditionalAncestry?: ConditionalAncestry;
  [key: string]: unknown;
}

export interface PresentationPayload {
  meta: {
    schemaVersion: number;
    generatedAt: string;
    mode: string;
    methodNote: string;
    counts: PresentationCounts;
  };
  people: unknown[];
  personContexts?: PresentedPersonContext[];
  personProfiles?: PresentedPersonProfile[];
  occurrences: unknown[];
  events: unknown[];
  temporalConstraints?: PresentationTemporalConstraint[];
  places?: unknown[];
  relationships: unknown[];
  sourceReportedNetworks?: SourceReportedNetwork[];
  identityCandidates: PresentationIdentityCandidate[];
  identityGroups?: PresentationIdentityGroup[];
  sourceIdentityGroups?: PresentationSourceIdentityGroup[];
  identityCuration?: {
    reviewThreshold: number;
    scoreSemantics: string;
    automaticMerge: boolean;
  };
  households: unknown[];
  sources: PresentationSource[];
  claims: PresentationClaim[];
  conflicts: PresentationConflict[];
  researchBacklog?: ResearchBacklog;
  volumeIndexes?: VolumeIndexSummary[];
  clusters?: unknown[];
  familyTree?: PresentationFamilyTree | null;
  graphScopes?: Record<string, unknown> | null;
  map?: {
    places?: unknown[];
    observations?: unknown[];
    sourceObservations?: MapSourceObservation[];
    [key: string]: unknown;
  } | null;
}

export function isViewName(value: string): value is ViewName {
  return (VIEW_NAMES as readonly string[]).includes(value);
}
