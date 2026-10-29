import type {
  IdentityDecisionTier,
  FamilyKnowledgePresentation,
  PresentedNameReading,
  PresentedPersonContext,
  PresentedPersonProfile,
  PresentationConflict,
  PresentationIdentityCandidate,
  PresentationPayload,
  ResearchBacklogItem,
  SourceReportedNetwork,
  SourceReportedNetworkNode,
} from "../types/presentation";
import { PersonContextIndex } from "../routing/person-context";
import { statusLabel } from "../utils/formatting";
import { familyKnowledgePresentation } from "../../core/family-knowledge.mjs";
import { DataIndex } from "../../core/data-index.mjs";
import { projectApprovedActMaterials } from "../../core/approved-act-materials.mjs";


export interface PersonProfileLink {
  id: string | null;
  label: string;
  status: string;
  sourceIds: string[];
  lifeSpanLabel: string;
}

export interface PersonSourceRelationship {
  id: string;
  relationLabel: string;
  predicates: string[];
  person: PersonProfileLink;
  personIdentityResolved: boolean;
  relationshipCanonical: boolean;
  resolutionLabel: string;
  sources: PersonSourceRelationshipEvidence[];
}

export interface PersonSourceRelationshipEvidence {
  id: string;
  sourceId: string | null;
  sourceLabel: string;
  relationshipIds: string[];
  occurrenceIds: string[];
  eventIds: string[];
  statuses: string[];
  readingCertainties: string[];
  literals: string[];
  provenance: string[];
  derivationStatuses: string[];
}

export interface PersonExternalTreeClassificationAlternative {
  role: string;
  roleLabel: string;
  pathLength: number;
}

export interface PersonExternalTreeMembership {
  id: string;
  networkId: string;
  networkLabel: string;
  status: "imported_unverified";
  anchorState: string;
  anchorLabel: string;
  classificationState: string;
  role: string | null;
  roleLabel: string;
  alternatives: PersonExternalTreeClassificationAlternative[];
  unclassifiedReason: string;
}

export interface PersonExternalTreeRelationship {
  id: string;
  networkId: string;
  networkLabel: string;
  relationLabel: string;
  person: PersonProfileLink;
  status: "imported_unverified";
  relationshipCanonical: false;
}

export interface PersonLifePoint {
  year: number;
  qualifier: "exact" | "about" | "before" | "after" | string;
  basis: string;
  eventIds: string[];
  sourceIds: string[];
}

export interface PersonLifeSpan {
  birth: PersonLifePoint | null;
  death: PersonLifePoint | null;
  label: string;
  status: "complete" | "partial" | "missing" | string;
  missing: string[];
  estimated: boolean;
  methodNote: string;
}

export interface PersonProfileFact {
  id: string;
  label: string;
  value: string;
  place: string;
  status: string;
  sourceId: string | null;
  identityAmbiguous: boolean;
}

export interface PersonIdentityComponent {
  id: string;
  label: string;
  occurrenceIds: string[];
}

export interface PersonProfileUnion {
  id: string;
  label: string;
  type: string;
  status: string;
  date: string;
  place: string;
  partners: PersonProfileLink[];
  children: PersonProfileLink[];
  sourceIds: string[];
  confirmed: boolean;
}

export interface PersonTimelineEntry {
  id: string;
  eventId: string;
  eventType: string;
  date: string;
  year: number | null;
  placeId: string | null;
  placeLabel: string;
  roles: string[];
  sourceId: string | null;
  sourceLabel: string;
  status: string;
  occurrenceIds: string[];
  componentLabels: string[];
  uncertain: boolean;
}

export interface PersonOccurrenceEntry {
  entryKind?: "source_reported_lead";
  id: string;
  rawName: string;
  normalizedName: string;
  role: string;
  status: string;
  confidence: number | null;
  eventId: string | null;
  eventType: string;
  eventDate: string;
  year: number | null;
  placeId: string | null;
  placeLabel: string;
  sourceId: string | null;
  sourceLabel: string;
  reading: PresentedNameReading | null;
  conflictIds: string[];
  componentPersonId: string | null;
  componentLabel: string;
  identityDecisionTier: IdentityDecisionTier | null;
  identityDecisionLabel: string | null;
  humanIdentityReviewOpen: boolean;
  uncertain: boolean;
}

export interface PersonLocalIdentityReview {
  decisionTier: IdentityDecisionTier;
  label: string;
  humanReviewLabel: string;
  humanReviewOpen: boolean;
  occurrenceIds: string[];
}

export interface PersonOccurrenceGroup {
  id: string;
  label: string;
  year: number | null;
  entries: PersonOccurrenceEntry[];
  commonStatus: string | null;
}

export interface PersonIdentityCandidate extends PresentationIdentityCandidate {
  hasStructuredBasis: boolean;
  sourceLinks: Array<{ sourceId: string; sourceLabel: string; occurrenceId: string }>;
}

export interface PersonProfileViewModel {
  personKey: string;
  publicProfileId: string;
  displayName: string;
  identityScope: PresentedPersonContext["identityScope"];
  identityLabel: string;
  identityNotice: string | null;
  familyKnown: FamilyKnowledgePresentation | null;
  localIdentityReviews: PersonLocalIdentityReview[];
  identityComponents: PersonIdentityComponent[];
  sourceReportedMemberships: PersonExternalTreeMembership[];
  approvedActMaterials: Array<{ sourceId: string; title: string; occurrenceIds: string[] }>;
  approvedActMaterialSummary: string | null;
  sourceReportedLeadEntries: PersonOccurrenceEntry[];
  unassignedOccurrenceCount: number;
  treeEvidenceStatus: string;
  treeEvidenceLabel: string;
  lifeSpan: PersonLifeSpan;
  lifeSpanLabel: string;
  lifeSpanNotice: string;
  sourceBoundaryLabel: string;
  placeSequence: string[];
  methodNote: string;
  metrics: {
    facts: number;
    occurrences: number;
    conflicts: number;
    questions: number;
    reviewItems: number;
  };
  facts: PersonProfileFact[];
  unresolvedFacts: PersonProfileFact[];
  family: {
    parents: PersonProfileLink[];
    siblings: PersonProfileLink[];
    unions: PersonProfileUnion[];
    unconfirmed: PersonProfileUnion[];
    sourceReported: PersonSourceRelationship[];
    externalTreeRelations: PersonExternalTreeRelationship[];
  };
  timeline: PersonTimelineEntry[];
  undatedTimeline: PersonTimelineEntry[];
  occurrenceGroups: PersonOccurrenceGroup[];
  occurrenceEntries: PersonOccurrenceEntry[];
  nameReadings: PresentedNameReading[];
  questions: ResearchBacklogItem[];
  conflicts: PresentationConflict[];
  identityCandidates: PersonIdentityCandidate[];
}

export interface PersonOccurrenceFilters {
  eventType: string;
  role: string;
  status: string;
  fromYear: number | null;
  toYear: number | null;
  conflictsOnly: boolean;
  attentionOnly: boolean;
}

export type PersonOccurrenceGrouping = "year" | "eventType";

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function records(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.map(record).filter((item) => Object.keys(item).length > 0) : [];
}

function text(value: unknown): string {
  return typeof value === "string" || typeof value === "number" ? String(value) : "";
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.map(text).filter(Boolean) : [];
}

function unique(values: string[]): string[] {
  return [...new Set(values.filter(Boolean))].sort((first, second) => first.localeCompare(second, "pl-PL"));
}

function uniqueInOrder(values: string[]): string[] {
  return [...new Set(values.filter(Boolean))];
}

function sourceRecordCountLabel(count: number): string {
  if (count === 1) return "1 zapis źródłowy";
  if (count % 10 >= 2 && count % 10 <= 4 && (count % 100 < 12 || count % 100 > 14)) return `${count} zapisy źródłowe`;
  return `${count} zapisów źródłowych`;
}

function yearFrom(value: unknown): number | null {
  const match = text(value).match(/(?:^|\D)(1[5-9]\d{2}|20\d{2})(?:\D|$)/);
  return match ? Number(match[1]) : null;
}

function unresolvedStatus(value: unknown): boolean {
  const status = text(value).toLocaleLowerCase("pl-PL");
  return ["unresolved", "candidate", "imported_unverified", "hypothesis", "ambiguous", "human_review_required", "rejected"].some((token) => status.includes(token));
}

function lifeSpanFrom(value: unknown, fallbackLabel: string): PersonLifeSpan {
  const item = record(value);
  const point = (raw: unknown): PersonLifePoint | null => {
    const candidate = record(raw);
    const year = Number(candidate.year);
    if (!Number.isFinite(year)) return null;
    return {
      year,
      qualifier: text(candidate.qualifier) || "about",
      basis: text(candidate.basis),
      eventIds: strings(candidate.eventIds),
      sourceIds: strings(candidate.sourceIds),
    };
  };
  const birth = point(item.birth);
  const death = point(item.death);
  const missing = strings(item.missing);
  return {
    birth,
    death,
    label: text(item.label) || fallbackLabel || "?–?",
    status: text(item.status) || (birth || death ? "partial" : "missing"),
    missing: missing.length ? missing : [!birth ? "birth" : "", !death ? "death" : ""].filter(Boolean),
    estimated: Boolean(item.estimated),
    methodNote: text(item.methodNote) || "Znak ? oznacza granicę przybliżoną, a nie potwierdzoną datę.",
  };
}

function sourceRelationLabel(predicate: string, currentIsFrom: boolean): string {
  const value = normalized(predicate).replaceAll("_", "-");
  if (value === "source-grammar-paternal-reference-via-his-wife") return currentIsFrom ? "Ojciec" : "Dziecko";
  if (value === "source-grammar-child-of") return currentIsFrom ? "Rodzic" : "Dziecko";
  if (value === "source-grammar-wife-of") return "Małżonek / małżonka";
  if (value === "godparent-of") return currentIsFrom ? "Chrześniak / chrześniaczka (FAN)" : "Rodzic chrzestny (FAN)";
  if (["father-of", "ojciec-of"].includes(value)) return currentIsFrom ? "Dziecko" : "Ojciec";
  if (["mother-of", "matka-of"].includes(value)) return currentIsFrom ? "Dziecko" : "Matka";
  if (["parent-of", "rodzic-of"].includes(value)) return currentIsFrom ? "Dziecko" : "Rodzic";
  if (["child-of-father", "child-of-mother", "child-of", "dziecko-of"].includes(value)) return currentIsFrom ? "Rodzic" : "Dziecko";
  if (/(marriage|spouse|husband|wife|malzen|malzon|małzon|maz|zona)/.test(value)) return "Małżonek / małżonka";
  if (/(sibling|brother|sister|brat|siostr)/.test(value)) return "Rodzeństwo";
  return predicate || "Relacja rodzinna";
}

function identityStatusResolved(value: unknown): boolean {
  return ["confirmed", "resolved", "resolved_same", "accepted", "agent_approved"]
    .includes(text(value).toLocaleLowerCase("pl-PL"));
}

function sourceRelationshipResolutionLabel(
  personIdentityResolved: boolean,
  relationshipCanonical: boolean,
  decisionTier: string | null = null,
): string {
  if (personIdentityResolved && decisionTier === "agent_approved_human_pending") {
    return `${AGENT_APPROVED_HUMAN_PENDING_LABEL} · relacja wskazana w źródłach`;
  }
  if (personIdentityResolved && decisionTier === "human_confirmed") {
    return `${HUMAN_CONFIRMED_LABEL} · relacja wskazana w źródłach`;
  }
  if (!personIdentityResolved) return "Tożsamość nierozstrzygnięta · relacja wskazana w źródłach";
  if (relationshipCanonical) return "Tożsamość rozstrzygnięta · relacja kanoniczna";
  return "Tożsamość rozstrzygnięta · relacja wskazana w źródłach";
}

function identityLabel(identityScope: PresentedPersonContext["identityScope"], evidenceStatus: string): string {
  if (identityScope === "canonical") return "Osoba kanoniczna";
  if (identityScope === "hypothesis_group") return "Tożsamość do rozstrzygnięcia";
  if (evidenceStatus === "unresolved") return "Osoba w drzewie — dane do rozstrzygnięcia";
  if (evidenceStatus === "unchecked") return "Osoba w drzewie — niesprawdzona";
  if (evidenceStatus === "masked_living") return "Profil chroniony";
  if (evidenceStatus === "family_known") return "Osoba znana z materiału rodzinnego";
  return "Profil prezentacyjny";
}

const AGENT_APPROVED_HUMAN_PENDING_LABEL = "tożsamość zatwierdzona agentowo — oczekuje potwierdzenia człowieka";
const HUMAN_CONFIRMED_LABEL = "Tożsamość potwierdzona przez człowieka";

function sourceReportedCandidate(
  payload: PresentationPayload,
  context: PresentedPersonContext,
): { network: SourceReportedNetwork; node: SourceReportedNetworkNode } | null {
  const networks = [
    ...(payload.sourceReportedNetworks ?? []),
    ...(payload.familyTree?.sourceReportedNetworks ?? []),
  ];
  const treeIds = new Set(context.treePersonIds);
  const occurrenceIds = new Set(context.occurrenceIds);
  for (const network of networks) {
    const candidate = network.nodes.find((item) => (
      treeIds.has(item.treeNodeId)
      || occurrenceIds.has(item.occurrenceId)
    ));
    if (candidate) return { network, node: candidate };
  }
  return null;
}

function externalProfileIdentity(candidate: ReturnType<typeof sourceReportedCandidate>): {
  status: "unresolved" | "resolved_same";
  decisionTier: "agent_approved_human_pending" | "human_confirmed" | null;
  resolved: boolean;
} {
  const status = candidate?.node.profileIdentityStatus ?? candidate?.node.identityStatus ?? "unresolved";
  const resolved = status === "resolved_same" && candidate?.node.personIdentityResolved === true;
  const decisionTier = resolved && ["agent_approved_human_pending", "human_confirmed"].includes(candidate?.node.identityDecisionTier || "")
    ? candidate?.node.identityDecisionTier ?? null
    : null;
  return { status: resolved ? "resolved_same" : "unresolved", decisionTier, resolved };
}

function externalProfileIdentityLabel(
  resolved: boolean,
  decisionTier: "agent_approved_human_pending" | "human_confirmed" | null,
): string {
  if (!resolved) return "Zewnętrzny profil kandydacki · tożsamość nierozstrzygnięta";
  if (decisionTier === "agent_approved_human_pending") {
    return "Profil zewnętrzny · tożsamość zatwierdzona agentowo — oczekuje potwierdzenia człowieka";
  }
  if (decisionTier === "human_confirmed") return "Profil zewnętrzny · tożsamość potwierdzona przez człowieka";
  return "Profil zewnętrzny · tożsamość rozstrzygnięta";
}

function externalProfileEvidenceLabel(
  resolved: boolean,
  decisionTier: "agent_approved_human_pending" | "human_confirmed" | null,
): string {
  if (!resolved) return statusLabel("unresolved");
  if (decisionTier === "agent_approved_human_pending") {
    return "zatwierdzona agentowo — oczekuje potwierdzenia człowieka";
  }
  if (decisionTier === "human_confirmed") return "potwierdzona przez człowieka";
  return "tożsamość rozstrzygnięta";
}

const externalRoleLabels: Record<string, string> = {
  anchor: "Punkt odniesienia",
  direct_ancestor: "Kandydat na bezpośredniego przodka",
  ancestor_spouse: "Małżonek / małżonka przodka",
  collateral: "Linia poboczna",
  descendant: "Potomek wskazany w drzewie zewnętrznym",
  fan: "Rola FAN wskazana w drzewie zewnętrznym",
};

const externalUnclassifiedLabels: Record<string, string> = {
  missing_anchor: "brak jawnego punktu odniesienia",
  masked_anchor: "punkt odniesienia ukryty przez reguły prywatności",
  masked_path: "część ścieżki ukryta przez reguły prywatności",
  disconnected: "brak połączenia z punktem odniesienia",
  unsupported_relation: "nieobsługiwany typ relacji",
};

function sourceReportedNetworkList(payload: PresentationPayload): SourceReportedNetwork[] {
  const byId = new Map<string, SourceReportedNetwork>();
  for (const network of [
    ...(payload.sourceReportedNetworks ?? []),
    ...(payload.familyTree?.sourceReportedNetworks ?? []),
  ]) {
    if (network?.id && !byId.has(network.id)) byId.set(network.id, network);
  }
  return [...byId.values()];
}

function externalTreeProfileData(
  payload: PresentationPayload,
  context: PresentedPersonContext,
): { memberships: PersonExternalTreeMembership[]; relations: PersonExternalTreeRelationship[] } {
  const networks = new Map(sourceReportedNetworkList(payload).map((network) => [network.id, network]));
  const rawMemberships = records((context as PresentedPersonContext & { sourceReportedMemberships?: unknown }).sourceReportedMemberships);
  const fallbackMemberships = rawMemberships.length ? rawMemberships : [...networks.values()].flatMap((network) => (
    network.nodes
      .filter((node) => context.treePersonIds.includes(node.treeNodeId) || context.occurrenceIds.includes(node.occurrenceId))
      .map((node) => ({
        networkId: network.id,
        treeNodeId: node.treeNodeId,
        occurrenceId: node.occurrenceId,
        classificationState: text(record(node).classification && record(record(node).classification).state) || "unclassified",
        role: text(record(record(node).classification).primaryRole),
        unclassifiedReason: text(record(record(node).classification).unclassifiedReason),
      }))
  ));
  const memberships: PersonExternalTreeMembership[] = [];
  const relations: PersonExternalTreeRelationship[] = [];
  const seenMemberships = new Set<string>();
  const seenRelations = new Set<string>();

  for (const rawMembership of fallbackMemberships) {
    const networkId = text(rawMembership.networkId);
    const treeNodeId = text(rawMembership.treeNodeId);
    const network = networks.get(networkId);
    if (!network || !treeNodeId) continue;
    const node = network.nodes.find((item) => item.treeNodeId === treeNodeId);
    if (!node) continue;
    const nodeRecord = record(node);
    const classification = record(nodeRecord.classification);
    const classificationState = text(rawMembership.classificationState || classification.state) || "unclassified";
    const role = text(rawMembership.role || classification.primaryRole) || null;
    const unclassifiedReason = text(rawMembership.unclassifiedReason || classification.unclassifiedReason);
    const alternatives = records(classification.alternatives).map((alternative) => ({
      role: text(alternative.role),
      roleLabel: externalRoleLabels[text(alternative.role)] || text(alternative.role) || "Rola nieokreślona",
      pathLength: records(alternative.orderedPath).length,
    }));
    const membershipId = `${networkId}:${treeNodeId}`;
    if (!seenMemberships.has(membershipId)) {
      seenMemberships.add(membershipId);
      const anchor = record(record(network).anchor);
      memberships.push({
        id: membershipId,
        networkId,
        networkLabel: network.label || "Zewnętrzna sieć kandydatów",
        status: "imported_unverified",
        anchorState: text(record(network).anchorState) || "missing",
        anchorLabel: text(anchor.label),
        classificationState,
        role,
        roleLabel: classificationState === "conflict"
          ? "Sprzeczne klasyfikacje — bez wyboru zwycięzcy"
          : role
            ? externalRoleLabels[role] || role
            : `Nieklasyfikowane: ${externalUnclassifiedLabels[unclassifiedReason] || unclassifiedReason || "brak pełnej jawnej ścieżki"}`,
        alternatives,
        unclassifiedReason,
      });
    }

    for (const edge of network.edges) {
      const currentIsFrom = edge.fromTreeNodeId === treeNodeId;
      const currentIsTo = edge.toTreeNodeId === treeNodeId;
      if (currentIsFrom === currentIsTo) continue;
      const otherTreeNodeId = currentIsFrom ? edge.toTreeNodeId : edge.fromTreeNodeId;
      const other = network.nodes.find((item) => item.treeNodeId === otherTreeNodeId);
      if (!other) continue;
      const relationId = `${networkId}:${edge.treeEdgeId}`;
      if (seenRelations.has(relationId)) continue;
      seenRelations.add(relationId);
      relations.push({
        id: relationId,
        networkId,
        networkLabel: network.label || "Zewnętrzna sieć kandydatów",
        relationLabel: sourceRelationLabel(edge.type, currentIsFrom),
        person: {
          id: other.treeNodeId,
          label: other.label || "Kandydat bez nazwy",
          status: "candidate",
          sourceIds: [],
          lifeSpanLabel: "?–?",
        },
        status: "imported_unverified",
        relationshipCanonical: false,
      });
    }
  }
  return {
    memberships: memberships.sort((first, second) => first.networkLabel.localeCompare(second.networkLabel, "pl-PL") || first.id.localeCompare(second.id)),
    relations: relations.sort((first, second) => first.networkLabel.localeCompare(second.networkLabel, "pl-PL") || first.relationLabel.localeCompare(second.relationLabel, "pl-PL") || first.person.label.localeCompare(second.person.label, "pl-PL")),
  };
}

function normalized(value: unknown): string {
  return text(value).normalize("NFKD").replace(/\p{Diacritic}/gu, "").toLocaleLowerCase("pl-PL").replace(/\s+/g, " ").trim();
}

function normalizedPersonTarget(value: unknown): string {
  return normalized(value)
    .replace(/\s+(?:ur\.?|urodzon[ay]|zm\.?|zmar[łl][ay])\s+(?:ok\.?\s*)?(?:1[5-9]\d{2}|20\d{2})(?:\s*r\.?)?$/, "")
    .trim();
}

function relatedQuestion(
  item: ResearchBacklogItem,
  context: PresentedPersonContext,
  aliases: Set<string>,
): boolean {
  const backingIds = new Set([
    context.personKey,
    context.publicProfileId,
    ...context.canonicalPersonIds,
    ...context.treePersonIds,
    ...context.occurrenceIds,
  ]);
  return (item.targets || []).some((target) => {
    if (target.id && backingIds.has(target.id)) return true;
    if (!["named_person", "person"].includes(target.type || "")) return false;
    return aliases.has(normalizedPersonTarget(target.label));
  });
}

function commonStatus(entries: PersonOccurrenceEntry[]): string | null {
  const statuses = unique(entries.map((entry) => entry.status || "unknown"));
  return statuses.length === 1 ? statuses[0] : null;
}

export function groupPersonOccurrences(entries: PersonOccurrenceEntry[], grouping: PersonOccurrenceGrouping = "year"): PersonOccurrenceGroup[] {
  const grouped = new Map<string, PersonOccurrenceEntry[]>();
  for (const entry of entries) {
    const key = grouping === "eventType" ? `type:${entry.eventType || "unknown"}` : entry.year == null ? "undated" : String(entry.year);
    grouped.set(key, [...(grouped.get(key) || []), entry]);
  }
  return [...grouped.entries()]
    .map(([id, values]) => ({
      id,
      label: id === "undated" ? "Bez ustalonej daty" : id.startsWith("type:") ? id.slice(5) : id,
      year: id === "undated" || id.startsWith("type:") ? null : Number(id),
      entries: values.sort((first, second) => (first.year ?? Number.MAX_SAFE_INTEGER) - (second.year ?? Number.MAX_SAFE_INTEGER) || first.id.localeCompare(second.id)),
      commonStatus: commonStatus(values),
    }))
    .sort((first, second) => grouping === "eventType"
      ? first.label.localeCompare(second.label, "pl-PL")
      : first.year == null ? 1 : second.year == null ? -1 : first.year - second.year);
}

export function filterPersonOccurrences(
  entries: PersonOccurrenceEntry[],
  filters: PersonOccurrenceFilters,
): PersonOccurrenceEntry[] {
  return entries.filter((entry) => {
    if (filters.eventType && entry.eventType !== filters.eventType) return false;
    if (filters.role && entry.role !== filters.role) return false;
    if (filters.status && entry.status !== filters.status) return false;
    if (filters.fromYear != null && (entry.year == null || entry.year < filters.fromYear)) return false;
    if (filters.toYear != null && (entry.year == null || entry.year > filters.toYear)) return false;
    if (filters.conflictsOnly && !entry.conflictIds.length) return false;
    if (filters.attentionOnly && !entry.uncertain && !entry.conflictIds.length) return false;
    return true;
  });
}

export function projectPersonProfile(
  payload: PresentationPayload,
  context: PresentedPersonContext,
  profile: PresentedPersonProfile | null,
  rawPerson: Record<string, unknown>,
): PersonProfileViewModel {
  const contextIndex = new PersonContextIndex(payload);
  const externalCandidate = sourceReportedCandidate(payload, context);
  const sourceReportedProfile = Boolean(externalCandidate || context.sourceReportedMemberships?.length);
  const externalIdentity = externalProfileIdentity(externalCandidate);
  const externalTree = externalTreeProfileData(payload, context);
  const treeEvidenceStatus = externalCandidate ? externalIdentity.status : text(rawPerson.evidenceStatus) || text(context.status) || "unknown";
  const treeEvidenceLabel = externalCandidate
    ? externalProfileEvidenceLabel(externalIdentity.resolved, externalIdentity.decisionTier)
    : statusLabel(treeEvidenceStatus);
  const identityUnresolved = externalCandidate ? !externalIdentity.resolved : context.identityScope === "hypothesis_group" || treeEvidenceStatus === "unresolved";
  const occurrenceIds = new Set(context.occurrenceIds);
  const canonicalIds = new Set(context.canonicalPersonIds);
  const treeIds = new Set(context.treePersonIds);
  const allOccurrences = records(payload.occurrences);
  const contextOccurrences = allOccurrences.filter((item) => (
    occurrenceIds.has(text(item.id)) || canonicalIds.has(text(item.personId))
  ));
  const approvedActs = sourceReportedProfile ? projectApprovedActMaterials({
    publicProfileId: externalCandidate?.node.publicProfileId || externalCandidate?.node.treeNodeId
      || context.sourceReportedMemberships?.find((item) => context.treePersonIds.includes(item.treeNodeId))?.treeNodeId
      || context.publicProfileId,
    dataIndex: new DataIndex(payload),
  }) : null;
  const primaryOccurrenceIds = approvedActs
    ? new Set<string>(approvedActs.appearances.map((item) => item.id)) : occurrenceIds;
  const occurrences = approvedActs
    ? allOccurrences.filter((item) => primaryOccurrenceIds.has(text(item.id))) : contextOccurrences;
  const leadOccurrences = sourceReportedProfile ? contextOccurrences.filter((item) => (
    occurrenceIds.has(text(item.id)) && canonicalIds.has(text(item.personId)) && item.externalTreeCandidate === true
  )) : [];
  const displayedOccurrences = [...occurrences, ...leadOccurrences];
  const displayedOccurrenceIds = new Set(displayedOccurrences.map((item) => text(item.id)));
  const familyKnown = externalCandidate
    ? familyKnowledgePresentation(externalCandidate.node.familyKnowledge
      ?? contextOccurrences.find((item) => text(item.id) === externalCandidate.node.occurrenceId)?.familyKnowledge)
    : null;
  const occurrenceById = new Map(occurrences.map((item) => [text(item.id), item]));
  const allOccurrenceById = new Map(allOccurrences.map((item) => [text(item.id), item]));
  const canonicalPeople = new Map(records(payload.people).map((item) => [text(item.id), item]));
  const identityComponents: PersonIdentityComponent[] = context.canonicalPersonIds.map((id) => ({
    id,
    label: text(canonicalPeople.get(id)?.name) || id,
    occurrenceIds: occurrences.filter((item) => text(item.personId) === id).map((item) => text(item.id)).sort(),
  }));
  const sources = new Map(records(payload.sources).map((item) => [text(item.id), item]));
  const places = new Map(records(payload.map?.places).map((item) => [text(item.id), item]));
  const readingsByOccurrence = new Map<string, PresentedNameReading>();
  for (const reading of profile?.nameReadings || []) {
    for (const id of reading.occurrenceIds) readingsByOccurrence.set(id, reading);
  }

  const eventScopeIds = sourceReportedProfile ? displayedOccurrenceIds : occurrenceIds;
  const entryEvents = records(payload.events).filter((event) => records(event.participants).some((participant) => eventScopeIds.has(text(participant.occurrenceId))));
  const events = sourceReportedProfile
    ? entryEvents.filter((event) => records(event.participants).some((participant) => {
      const id = text(participant.occurrenceId);
      return primaryOccurrenceIds.has(id) && text(occurrenceById.get(id)?.sourceId) === text(event.sourceId);
    })) : entryEvents;
  const eventByOccurrence = new Map<string, Record<string, unknown>>();
  for (const event of entryEvents) {
    for (const participant of records(event.participants)) {
      const id = text(participant.occurrenceId);
      if (sourceReportedProfile && primaryOccurrenceIds.has(id)
        && text(event.sourceId) !== text(occurrenceById.get(id)?.sourceId)) continue;
      if (eventScopeIds.has(id) && !eventByOccurrence.has(id)) eventByOccurrence.set(id, event);
    }
  }

  const rawAliases = [context.label, ...(profile?.aliases || []).map((item) => item.text), ...(profile?.nameReadings || []).flatMap((item) => [item.original, item.polishForm || ""]), ...occurrences.flatMap((item) => [text(item.name), text(item.normalizedName)])];
  const aliases = new Set(rawAliases.map(normalized).filter(Boolean));
  const questions = (payload.researchBacklog?.items || []).filter((item) => relatedQuestion(item, context, aliases));
  const questionConflictIds = new Set(questions.flatMap((item) => (item.targets || []).filter((target) => target.type === "conflict").map((target) => target.id || "")).filter(Boolean));
  const claims = new Map((payload.claims || []).map((item) => [item.id || "", item]));
  const backingIds = new Set([context.personKey, context.publicProfileId, ...context.canonicalPersonIds, ...context.treePersonIds, ...context.occurrenceIds]);
  const conflicts = (payload.conflicts || []).filter((conflict) => {
    if (conflict.id && questionConflictIds.has(conflict.id)) return true;
    const claim = conflict.claimId ? claims.get(conflict.claimId) as Record<string, unknown> | undefined : undefined;
    return Boolean(claim && [text(claim.subjectRef), text(claim.objectRef)].some((id) => backingIds.has(id)));
  });
  const conflictsByBackingId = new Map<string, string[]>();
  for (const conflict of conflicts) {
    const claim = conflict.claimId ? claims.get(conflict.claimId) as Record<string, unknown> | undefined : undefined;
    if (!claim || !conflict.id) continue;
    for (const backingId of [text(claim.subjectRef), text(claim.objectRef)].filter(Boolean)) {
      conflictsByBackingId.set(backingId, unique([...(conflictsByBackingId.get(backingId) || []), conflict.id]));
    }
  }

  const identityCandidates: PersonIdentityCandidate[] = (payload.identityCandidates || []).filter((candidate) => [
    candidate.fromOccurrenceId,
    candidate.toOccurrenceId,
    candidate.fromPersonId,
    candidate.toPersonId,
  ].some((id) => backingIds.has(text(id)))).map((candidate) => {
    const linkedSourceIds = new Set<string>();
    const sourceLinks: PersonIdentityCandidate["sourceLinks"] = [];
    for (const occurrenceId of [candidate.fromOccurrenceId, candidate.toOccurrenceId]) {
      const sourceId = text(allOccurrenceById.get(occurrenceId)?.sourceId);
      const source = sources.get(sourceId);
      if (!source || linkedSourceIds.has(sourceId)) continue;
      linkedSourceIds.add(sourceId);
      sourceLinks.push({ sourceId, sourceLabel: text(source.title) || "Otwórz podstawę źródłową", occurrenceId });
    }
    return {
      ...candidate,
      hasStructuredBasis: [candidate.support, candidate.against, candidate.humanReviewDebt].some((items) => Array.isArray(items) && items.length > 0),
      sourceLinks,
    };
  });

  const projectedOccurrenceEntries: PersonOccurrenceEntry[] = displayedOccurrences.map((occurrence) => {
    const id = text(occurrence.id);
    const event = eventByOccurrence.get(id) || {};
    const eventId = text(event.id) || null;
    const sourceId = text(occurrence.sourceId || event.sourceId) || null;
    const placeId = text(event.placeId) || null;
    const reading = readingsByOccurrence.get(id) || null;
    const componentPersonId = text(occurrence.personId) || null;
    const componentLabel = componentPersonId ? text(canonicalPeople.get(componentPersonId)?.name) : "";
    const status = text(occurrence.status) || "unknown";
    const rawIdentityDecisionTier = text(occurrence.identityDecisionTier);
    const identityDecisionTier: IdentityDecisionTier | null = rawIdentityDecisionTier === "agent_approved_human_pending"
      || rawIdentityDecisionTier === "human_confirmed"
      ? rawIdentityDecisionTier
      : null;
    const date = text(event.date || event.dateFrom || event.dateTo);
    return {
      id,
      rawName: text(occurrence.name),
      normalizedName: text(occurrence.normalizedName),
      role: text(occurrence.role) || text(records(event.participants).find((item) => text(item.occurrenceId) === id)?.role),
      status,
      confidence: typeof occurrence.confidence === "number" ? occurrence.confidence : null,
      eventId,
      eventType: text(event.type) || "unknown",
      eventDate: date,
      year: yearFrom(date),
      placeId,
      placeLabel: text(event.place) || text(places.get(placeId || "")?.label || places.get(placeId || "")?.name),
      sourceId,
      sourceLabel: text(sources.get(sourceId || "")?.title || sources.get(sourceId || "")?.label),
      reading,
      conflictIds: conflictsByBackingId.get(id) || [],
      componentPersonId,
      componentLabel,
      identityDecisionTier,
      identityDecisionLabel: identityDecisionTier === "agent_approved_human_pending"
        ? AGENT_APPROVED_HUMAN_PENDING_LABEL
        : identityDecisionTier === "human_confirmed"
          ? HUMAN_CONFIRMED_LABEL
          : null,
      humanIdentityReviewOpen: identityDecisionTier === "agent_approved_human_pending",
      uncertain: unresolvedStatus(status) || Boolean(reading?.uncertain) || unresolvedStatus(reading?.reviewStatus),
    };
  }).sort((first, second) => (first.year ?? Number.MAX_SAFE_INTEGER) - (second.year ?? Number.MAX_SAFE_INTEGER) || first.id.localeCompare(second.id));
  const occurrenceEntries = sourceReportedProfile
    ? projectedOccurrenceEntries.filter((entry) => primaryOccurrenceIds.has(entry.id)) : projectedOccurrenceEntries;
  const leadIds = new Set(leadOccurrences.map((item) => text(item.id)));
  const sourceReportedLeadEntries: PersonOccurrenceEntry[] = projectedOccurrenceEntries.filter((entry) => leadIds.has(entry.id))
    .map((entry) => ({ ...entry, entryKind: "source_reported_lead", sourceId: null,
      sourceLabel: "Profil zgłoszony w drzewie", eventId: null, eventType: "external_tree_profile",
      eventDate: "", year: null, placeId: null, placeLabel: "", reading: null }));

  const localIdentityReviews: PersonLocalIdentityReview[] = ([
    {
      decisionTier: "agent_approved_human_pending",
      label: AGENT_APPROVED_HUMAN_PENDING_LABEL,
      humanReviewLabel: "Kontrola człowieka: otwarta",
      humanReviewOpen: true,
    },
    {
      decisionTier: "human_confirmed",
      label: HUMAN_CONFIRMED_LABEL,
      humanReviewLabel: "Kontrola człowieka: zamknięta",
      humanReviewOpen: false,
    },
  ] as const).map((review) => ({
    ...review,
    occurrenceIds: occurrenceEntries
      .filter((entry) => entry.identityDecisionTier === review.decisionTier)
      .map((entry) => entry.id),
  })).filter((review) => review.occurrenceIds.length > 0);

  const timelineByKey = new Map<string, PersonTimelineEntry>();
  for (const event of events) {
    const participants = records(event.participants).filter((item) => primaryOccurrenceIds.has(text(item.occurrenceId)));
    const matchingOccurrenceIds = unique(participants.map((item) => text(item.occurrenceId)));
    const date = text(event.date || event.dateFrom || event.dateTo);
    const eventId = text(event.id);
    const key = text(event.recordKey) || eventId;
    const sourceId = text(event.sourceId) || null;
    const placeId = text(event.placeId) || null;
    const statuses = matchingOccurrenceIds.map((id) => text(occurrenceById.get(id)?.status));
    const item: PersonTimelineEntry = {
      id: key,
      eventId,
      eventType: text(event.type) || "unknown",
      date,
      year: yearFrom(date),
      placeId,
      placeLabel: text(event.place) || text(places.get(placeId || "")?.label || places.get(placeId || "")?.name),
      roles: unique(participants.map((entry) => text(entry.role))),
      sourceId,
      sourceLabel: text(sources.get(sourceId || "")?.title || sources.get(sourceId || "")?.label),
      status: text(event.status) || commonStatus(matchingOccurrenceIds.map((id) => occurrenceEntries.find((entry) => entry.id === id)!).filter(Boolean)) || "unknown",
      occurrenceIds: matchingOccurrenceIds,
      componentLabels: unique(matchingOccurrenceIds.map((id) => occurrenceEntries.find((entry) => entry.id === id)?.componentLabel || "")),
      uncertain: identityUnresolved || unresolvedStatus(event.status) || statuses.some(unresolvedStatus),
    };
    const previous = timelineByKey.get(key);
    if (!previous || item.occurrenceIds.length > previous.occurrenceIds.length) timelineByKey.set(key, item);
  }
  const timeline = [...timelineByKey.values()].sort((first, second) => (first.year ?? Number.MAX_SAFE_INTEGER) - (second.year ?? Number.MAX_SAFE_INTEGER) || first.id.localeCompare(second.id));

  const tree = record(payload.familyTree);
  const treePeople = new Map(records(tree.people).map((item) => [text(item.id), item]));
  const families = records(tree.families);
  const unions = records(tree.unions);
  const linkForTreeId = (id: string, status = "unknown", sourceIds: string[] = []): PersonProfileLink => {
    const person = treePeople.get(id) || {};
    const personContext = contextIndex.resolveAny(id);
    return {
      id: personContext?.publicProfileId || (treePeople.has(id) && !contextIndex.contexts.length ? id : null),
      label: text(person.name) || "Osoba bez etykiety",
      status: text(person.evidenceStatus) || status,
      sourceIds: unique([...sourceIds, ...strings(person.sourceIds)]),
      lifeSpanLabel: text(person.lifeSpanLabel) || "?–?",
    };
  };
  const linkForOccurrence = (occurrenceId: string, status = "source-reported", sourceIds: string[] = []): PersonProfileLink => {
    const occurrence = allOccurrenceById.get(occurrenceId) || {};
    const personContext = contextIndex.resolveAny(occurrenceId);
    const canonicalPerson = text(occurrence.personId) ? canonicalPeople.get(text(occurrence.personId)) || {} : {};
    const contextTreePerson = personContext?.treePersonIds
      .map((id) => treePeople.get(id))
      .find(Boolean) || {};
    return {
      id: personContext?.publicProfileId || null,
      label: text(canonicalPerson.name) || text(occurrence.normalizedName) || text(occurrence.name) || "Osoba bez etykiety",
      status: text(occurrence.status) || status,
      sourceIds: unique([...sourceIds, text(occurrence.sourceId)]),
      lifeSpanLabel: text(contextTreePerson.lifeSpanLabel || canonicalPerson.lifeSpanLabel) || "?–?",
    };
  };
  const parentFamilies = families.filter((family) => strings(family.childIds).some((id) => treeIds.has(id)));
  const parents = unique(parentFamilies.flatMap((family) => strings(family.parentIds))).map((id) => linkForTreeId(id));
  const siblings = unique(parentFamilies.flatMap((family) => strings(family.childIds)).filter((id) => !treeIds.has(id))).map((id) => linkForTreeId(id));
  const projectedUnions: PersonProfileUnion[] = unions
    .filter((union) => strings(union.partnerIds).some((id) => treeIds.has(id)))
    .map((union) => {
      const partnerIds = strings(union.partnerIds).filter((id) => !treeIds.has(id));
      const family = families.find((item) => {
        const familyParents = strings(item.parentIds);
        return [...treeIds].some((id) => familyParents.includes(id)) && partnerIds.every((id) => familyParents.includes(id));
      });
      const status = text(union.status) || "unknown";
      const sourceIds = unique([...strings(union.sourceIds), ...strings(family?.sourceIds)]);
      return {
        id: text(union.id),
        label: text(union.label) || text(union.type) || "Związek",
        type: text(union.type),
        status,
        date: text(union.date),
        place: text(union.place),
        partners: partnerIds.map((id) => linkForTreeId(id, status, sourceIds)),
        children: strings(family?.childIds).map((id) => linkForTreeId(id, text(family?.status), sourceIds)),
        sourceIds,
        confirmed: !identityUnresolved && !unresolvedStatus(status) && status !== "family_known" && status !== "masked_living",
      };
    })
    .sort((first, second) => first.date.localeCompare(second.date, "pl-PL") || first.id.localeCompare(second.id));

  const relationshipIsCanonical = (
    relationLabel: string,
    relatedContext: PresentedPersonContext | null,
  ): boolean => {
    const currentTreeIds = context.treePersonIds;
    const relatedTreeIds = relatedContext?.treePersonIds || [];
    if (!currentTreeIds.length || !relatedTreeIds.length) return false;
    const includesBoth = (ids: string[]) => (
      currentTreeIds.some((id) => ids.includes(id)) && relatedTreeIds.some((id) => ids.includes(id))
    );
    if (relationLabel === "Małżonek / małżonka") {
      return unions.some((union) => includesBoth(strings(union.partnerIds)));
    }
    if (["Ojciec", "Matka", "Rodzic"].includes(relationLabel)) {
      return families.some((family) => (
        relatedTreeIds.some((id) => strings(family.parentIds).includes(id))
          && currentTreeIds.some((id) => strings(family.childIds).includes(id))
      ));
    }
    if (relationLabel === "Dziecko") {
      return families.some((family) => (
        currentTreeIds.some((id) => strings(family.parentIds).includes(id))
          && relatedTreeIds.some((id) => strings(family.childIds).includes(id))
      ));
    }
    if (relationLabel === "Rodzeństwo") {
      return families.some((family) => {
        const children = strings(family.childIds);
        return currentTreeIds.some((id) => children.includes(id)) && relatedTreeIds.some((id) => children.includes(id));
      });
    }
    return false;
  };

  const sourceRelationshipGroups = new Map<string, PersonSourceRelationship>();
  const sourceRelationshipTiers = new Map<string, string | null>();
  const relatedActIndex = new DataIndex(payload);
  const admittedRelatedActs = new Map<string, ReturnType<typeof projectApprovedActMaterials>>();
  for (const relationship of records(payload.relationships)) {
    // External-tree topology has its own network-scoped projection below.  It
    // must never be presented again as a relationship stated in an archival
    // act, even when the candidate occurrence belongs to this profile.
    if (text(relationship.relationshipScope) === "source_reported_external_tree") continue;
    const fromId = text(relationship.fromOccurrenceId);
    const toId = text(relationship.toOccurrenceId);
    const currentIsFrom = occurrenceIds.has(fromId);
    const currentIsTo = occurrenceIds.has(toId);
    if (currentIsFrom === currentIsTo) continue;
    const otherId = currentIsFrom ? toId : fromId;
    const otherOccurrence = allOccurrenceById.get(otherId) || {};
    const relatedCanonicalId = text(otherOccurrence.personId);
    const relatedCanonicalPerson = relatedCanonicalId ? canonicalPeople.get(relatedCanonicalId) || {} : {};
    const relatedContext = contextIndex.resolveAny(otherId) || (relatedCanonicalId ? contextIndex.resolveAny(relatedCanonicalId) : null);
    // Reuse the protected act admission policy; candidate person status alone
    // does not express a reviewed match to this exact source occurrence.
    let relatedDecisionTier: string | null = null;
    if (relatedCanonicalPerson.status === "candidate" && relatedContext?.identityScope === "canonical"
      && relatedContext.canonicalPersonIds.length === 1
      && relatedContext.canonicalPersonIds.includes(relatedCanonicalId)
      && relatedContext.occurrenceIds.includes(otherId)
      && (!relationship.sourceId || relationship.sourceId === otherOccurrence.sourceId)) {
      const key = relatedContext.treePersonIds.find((id) => id === relatedContext.publicProfileId)
        || relatedContext.treePersonIds[0] || relatedContext.publicProfileId;
      if (!admittedRelatedActs.has(key)) {
        admittedRelatedActs.set(key, projectApprovedActMaterials({ publicProfileId: key, dataIndex: relatedActIndex }));
      }
      if (admittedRelatedActs.get(key)?.appearances.some((item) => (
        item.id === otherId && item.sourceId === otherOccurrence.sourceId
      ))) relatedDecisionTier = text(otherOccurrence.identityDecisionTier) || null;
    }
    const personIdentityResolved = Boolean(relatedDecisionTier) || Boolean(
      relatedCanonicalId
        && relatedContext?.identityScope === "canonical"
        && identityStatusResolved(relatedCanonicalPerson.status),
    );
    const sourceId = text(relationship.sourceId || allOccurrenceById.get(otherId)?.sourceId) || null;
    const relatedPerson = linkForOccurrence(otherId, text(relationship.status), sourceId ? [sourceId] : []);
    const predicate = text(relationship.type);
    const relationLabel = sourceRelationLabel(predicate, currentIsFrom);
    const relationshipCanonical = relationshipIsCanonical(relationLabel, relatedContext);
    const relationshipId = text(relationship.id) || `${fromId}:${predicate}:${toId}`;
    const groupPersonKey = personIdentityResolved ? `person:${relatedCanonicalId}` : `occurrence:${otherId}`;
    const groupKey = `${relationLabel}:${groupPersonKey}`;
    const evidenceKey = sourceId || relationshipId;
    const evidence: PersonSourceRelationshipEvidence = {
      id: evidenceKey,
      sourceId,
      sourceLabel: text(sources.get(sourceId || "")?.title || sources.get(sourceId || "")?.label),
      relationshipIds: [relationshipId],
      occurrenceIds: unique([fromId, toId]),
      eventIds: unique(strings(relationship.backingEventIds)),
      statuses: unique([text(relationship.verificationStatus || relationship.status) || "source-reported"]),
      readingCertainties: unique([text(relationship.readingCertainty)]),
      literals: unique([text(relationship.literal)]),
      provenance: unique([text(relationship.provenance)]),
      derivationStatuses: unique([text(relationship.derivationStatus)]),
    };
    const existing = sourceRelationshipGroups.get(groupKey);
    if (!existing) {
      sourceRelationshipTiers.set(groupKey, relatedDecisionTier);
      sourceRelationshipGroups.set(groupKey, {
        id: groupKey,
        relationLabel,
        predicates: unique([predicate]),
        person: relatedPerson,
        personIdentityResolved,
        relationshipCanonical,
        resolutionLabel: sourceRelationshipResolutionLabel(personIdentityResolved, relationshipCanonical, relatedDecisionTier),
        sources: [evidence],
      });
      continue;
    }
    existing.predicates = unique([...existing.predicates, predicate]);
    existing.person.sourceIds = unique([...existing.person.sourceIds, ...relatedPerson.sourceIds]);
    existing.relationshipCanonical = existing.relationshipCanonical || relationshipCanonical;
    const previousTier = sourceRelationshipTiers.get(groupKey);
    const groupTier = previousTier === "agent_approved_human_pending" || relatedDecisionTier === "agent_approved_human_pending"
      ? "agent_approved_human_pending" : previousTier || relatedDecisionTier;
    sourceRelationshipTiers.set(groupKey, groupTier);
    existing.resolutionLabel = sourceRelationshipResolutionLabel(existing.personIdentityResolved, existing.relationshipCanonical, groupTier);
    const existingEvidence = existing.sources.find((item) => item.id === evidenceKey);
    if (!existingEvidence) {
      existing.sources.push(evidence);
      continue;
    }
    existingEvidence.relationshipIds = unique([...existingEvidence.relationshipIds, ...evidence.relationshipIds]);
    existingEvidence.occurrenceIds = unique([...existingEvidence.occurrenceIds, ...evidence.occurrenceIds]);
    existingEvidence.eventIds = unique([...existingEvidence.eventIds, ...evidence.eventIds]);
    existingEvidence.statuses = unique([...existingEvidence.statuses, ...evidence.statuses]);
    existingEvidence.readingCertainties = unique([...existingEvidence.readingCertainties, ...evidence.readingCertainties]);
    existingEvidence.literals = unique([...existingEvidence.literals, ...evidence.literals]);
    existingEvidence.provenance = unique([...existingEvidence.provenance, ...evidence.provenance]);
    existingEvidence.derivationStatuses = unique([...existingEvidence.derivationStatuses, ...evidence.derivationStatuses]);
  }
  const sourceReported = [...sourceRelationshipGroups.values()]
    .map((item) => ({
      ...item,
      sources: item.sources.sort((first, second) => (
        first.sourceLabel.localeCompare(second.sourceLabel, "pl-PL") || first.id.localeCompare(second.id, "pl-PL")
      )),
    }))
    .sort((first, second) => first.relationLabel.localeCompare(second.relationLabel, "pl-PL") || first.person.label.localeCompare(second.person.label, "pl-PL"));

  const projectedFacts: PersonProfileFact[] = records(rawPerson.facts)
    .map((fact, index) => ({
      id: `${text(fact.label)}:${text(fact.value)}:${index}`,
      label: text(fact.label),
      value: text(fact.value),
      place: text(fact.place),
      status: text(fact.status) || "unknown",
      sourceId: text(fact.sourceId) || null,
      identityAmbiguous: identityUnresolved,
    }));
  const facts = projectedFacts.filter((fact) => !fact.identityAmbiguous && !unresolvedStatus(fact.status));
  const unresolvedFacts = projectedFacts.filter((fact) => fact.identityAmbiguous || unresolvedStatus(fact.status));

  const dated = timeline.filter((item) => item.year != null);
  const datedFactPlaces = projectedFacts
    .filter((fact) => fact.sourceId && yearFrom(fact.value) != null)
    .map((fact) => ({ year: yearFrom(fact.value)!, place: fact.place }));
  const boundaryYears = [...dated.map((item) => item.year!), ...datedFactPlaces.map((item) => item.year)].sort((first, second) => first - second);
  const firstYear = boundaryYears[0];
  const lastYear = boundaryYears[boundaryYears.length - 1];
  const boundaryUncertain = identityUnresolved || dated.some((item) => item.uncertain);
  const sourceBoundaryLabel = firstYear == null
    ? "Brak dat wystąpień w kontrolowanym eksporcie"
    : `Wystąpienia w aktach: ${firstYear}${boundaryUncertain ? "?" : ""}${lastYear !== firstYear ? `–${lastYear}${boundaryUncertain ? "?" : ""}` : ""}`;
  const placeSequence = uniqueInOrder([
    ...datedFactPlaces,
    ...dated.map((item) => ({ year: item.year!, place: item.placeLabel })),
  ].sort((first, second) => first.year - second.year).map((item) => item.place)).slice(0, 4);
  const externalClassificationConflicts = externalTree.memberships.filter((item) => item.classificationState === "conflict").length;
  const reviewItems = occurrenceEntries.filter((item) => item.uncertain).length + identityCandidates.length + unresolvedFacts.length + externalClassificationConflicts;
  const profileIdentityLabel = externalCandidate
    ? familyKnown && !externalIdentity.resolved
      ? "Profil zewnętrzny · dopasowanie do aktów nierozstrzygnięte"
      : externalProfileIdentityLabel(externalIdentity.resolved, externalIdentity.decisionTier)
    : identityLabel(context.identityScope, treeEvidenceStatus);
  const lifeSpan = lifeSpanFrom(rawPerson.lifeSpan, text(rawPerson.lifeSpanLabel));
  const birthDerivedFromAge = lifeSpan.birth?.basis === "age_in_source";
  const deathDerivedFromLifeState = ["explicit_living_in_source", "explicit_deceased_in_source"].includes(lifeSpan.death?.basis || "");
  const missingBirth = lifeSpan.missing.includes("birth");
  const missingDeath = lifeSpan.missing.includes("death");
  const lifeSpanNotice = externalCandidate
    ? "Daty lub szacunki widoczne na tej karcie nie są potwierdzonymi latami życia jednej osoby."
    : context.identityScope === "hypothesis_group"
    ? "To zakres materiału kilku kandydatów, nie lata życia jednej osoby."
    : lifeSpan.status === "missing"
      ? "Brakuje roku urodzenia i roku zgonu; ta luka powinna trafić do triage kuratora."
      : missingBirth && missingDeath && birthDerivedFromAge && deathDerivedFromLifeState
        ? "Brakuje własnych aktów urodzenia i zgonu; granice wynikają z wieku oraz jawnej informacji o stanie osoby w innych aktach."
        : missingBirth && missingDeath && birthDerivedFromAge
          ? "Brakuje własnych aktów urodzenia i zgonu; początek wynika z wieku podanego przy tej osobie w akcie."
          : missingBirth
        ? birthDerivedFromAge
          ? "Brakuje własnego aktu urodzenia; przybliżenie wynika z wieku podanego przy tej osobie w akcie."
          : "Brakuje roku urodzenia; daty innych aktów nie zastępują tej granicy."
        : missingDeath
          ? deathDerivedFromLifeState
            ? "Brakuje aktu zgonu; granica wynika tylko z jawnej informacji, że osoba żyła lub już nie żyła."
            : "Brakuje roku zgonu; późniejsze akty krewnych nie są używane jako granica życia."
          : lifeSpan.estimated
            ? lifeSpan.methodNote
            : "Daty graniczne wynikają z powiązanych zdarzeń źródłowych.";
  const identityNotice = externalCandidate
    ? externalIdentity.decisionTier === "agent_approved_human_pending"
      ? "Tożsamość zatwierdzona agentowo — oczekuje potwierdzenia człowieka. Relacje nadal są wskazane przez źródło zewnętrzne, niekanoniczne i niepotwierdzone."
      : externalIdentity.decisionTier === "human_confirmed"
        ? "Tożsamość została potwierdzona przez człowieka. Relacje nadal są wskazane przez źródło zewnętrzne, niekanoniczne i niepotwierdzone."
        : externalIdentity.resolved
          ? "Tożsamość profilu została rozstrzygnięta. Relacje nadal są wskazane przez źródło zewnętrzne, niekanoniczne i niepotwierdzone."
          : familyKnown
            ? "Ten profil pochodzi z zewnętrznego drzewa. Dopasowanie do konkretnych aktów i relacje pozostają nierozstrzygnięte; profil nie został automatycznie scalony z osobą kanoniczną."
            : "Ten profil pochodzi z zewnętrznego drzewa. Tożsamość i relacje pozostają nierozstrzygnięte; profil nie został automatycznie scalony z osobą kanoniczną."
    : context.identityScope === "hypothesis_group"
    ? `Ten profil obejmuje ${identityComponents.length} odrębne osoby kanoniczne. Wydarzenia, relacje i fakty są pokazane jako materiał do korelacji, bez scalania ich w jedną osobę.`
    : treeEvidenceStatus === "unresolved"
      ? "Ta osoba pozostaje widoczna w strukturze rodziny, ale jej dane lub przypisanie nie zostały rozstrzygnięte."
      : null;
  const assignedComponentOccurrenceIds = new Set(identityComponents.flatMap((item) => item.occurrenceIds));
  const unassignedOccurrenceCount = occurrenceEntries.filter((item) => !assignedComponentOccurrenceIds.has(item.id)).length;

  return {
    personKey: context.personKey,
    publicProfileId: context.publicProfileId,
    displayName: profile?.displayName || context.label,
    identityScope: context.identityScope,
    identityLabel: profileIdentityLabel,
    identityNotice,
    familyKnown,
    localIdentityReviews,
    identityComponents,
    sourceReportedMemberships: externalTree.memberships,
    approvedActMaterials: approvedActs?.materials || [],
    approvedActMaterialSummary: approvedActs?.appearanceSummary || null,
    sourceReportedLeadEntries,
    unassignedOccurrenceCount,
    treeEvidenceStatus,
    treeEvidenceLabel,
    lifeSpan,
    lifeSpanLabel: text(rawPerson.lifeSpanLabel) || lifeSpan.label,
    lifeSpanNotice,
    sourceBoundaryLabel,
    placeSequence,
    methodNote: externalCandidate
      ? externalIdentity.resolved
        ? `Ta karta pokazuje ${approvedActs?.appearanceSummary} powiązanych przez zatwierdzone łańcuchy tożsamości. Profil zgłoszony w drzewie pozostaje osobnym tropem. Tożsamość profilu została rozstrzygnięta, ale relacje zewnętrzne pozostają niekanoniczne i nie są awansowane do modelu kanonicznego.`
        : `Ta karta pokazuje ${approvedActs?.appearanceSummary} powiązanych przez zatwierdzone łańcuchy tożsamości oraz osobny profil zgłoszony w drzewie. Nie awansuje osoby ani relacji do modelu kanonicznego.`
      : context.identityScope === "hypothesis_group"
      ? `Ta karta zestawia ${sourceRecordCountLabel(occurrenceEntries.length)} przypisane do ${identityComponents.length} odrębnych osób kanonicznych. To przestrzeń korelacji, nie scalona biografia.`
      : treeEvidenceStatus === "unresolved"
        ? `Ta karta zestawia ${sourceRecordCountLabel(occurrenceEntries.length)} powiązane z nierozstrzygniętą pozycją w drzewie. Nie tworzy z nich potwierdzonej biografii.`
      : `Ta karta łączy ${sourceRecordCountLabel(occurrenceEntries.length)}. Warianty nazw pozostają odrębnymi odczytami i nie są automatycznie uznawane za tę samą osobę.`,
    metrics: {
      facts: facts.length,
      occurrences: occurrenceEntries.length,
      conflicts: conflicts.length + externalClassificationConflicts,
      questions: questions.length,
      reviewItems,
    },
    facts,
    unresolvedFacts,
    family: {
      parents,
      siblings,
      unions: projectedUnions.filter((item) => item.confirmed),
      unconfirmed: projectedUnions.filter((item) => !item.confirmed),
      sourceReported,
      externalTreeRelations: externalTree.relations,
    },
    timeline: timeline.filter((item) => item.year != null),
    undatedTimeline: timeline.filter((item) => item.year == null),
    occurrenceGroups: groupPersonOccurrences(occurrenceEntries),
    occurrenceEntries,
    nameReadings: profile?.nameReadings || [],
    questions,
    conflicts,
    identityCandidates,
  };
}
