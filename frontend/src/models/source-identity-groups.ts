import type { PresentationPayload, PresentationSourceIdentityGroup } from "../types/presentation";
import type { PersonSourceRelationship } from "./person-profile-projector";

const object = (value: unknown): Record<string, unknown> | null => value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
const identifier = (value: unknown): value is string => typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9_.:-]*$/.test(value);
const nonempty = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0;
const pair = (value: unknown): value is [string, string] => Array.isArray(value) && value.length === 2 && value.every(identifier) && value[0] !== value[1];
const identifiers = (value: unknown): value is string[] => Array.isArray(value) && value.length >= 2 && value.every(identifier) && new Set(value).size === value.length;
const sha256 = (value: unknown): value is string => typeof value === "string" && /^[a-fA-F0-9]{64}$/.test(value);
const hidden = (value: Record<string, unknown>): boolean => value.private === true || value.restrictedBacking === true || value.sourceRedacted === true
  || ["restricted", "living", "private", "family_private", "masked_living"].includes(String(typeof value.privacy === "string" ? value.privacy : object(value.privacy)?.status || ""))
  || (value.availability != null && value.availability !== "found");
const artifact = (value: unknown): boolean => {
  const ref = object(value);
  return Boolean(ref && nonempty(ref.artifactPath) && sha256(ref.artifactSha256));
};
const sameValue = (left: unknown, right: unknown): boolean => {
  if (left === right) return true;
  if (Array.isArray(left) && Array.isArray(right)) return left.length === right.length && left.every((value, index) => sameValue(value, right[index]));
  const a = object(left); const b = object(right);
  return Boolean(a && b && Object.keys(a).length === Object.keys(b).length && Object.keys(a).every((key) => Object.hasOwn(b, key) && sameValue(a[key], b[key])));
};

function reviewedDecision(value: Record<string, unknown>, verdict = "approve_agent_source_only_identity"): boolean {
  if (hidden(value) || ![value.candidateId, value.resolutionCommandId, value.ratificationCommandId].every(identifier)
    || value.resolutionCommandId === value.ratificationCommandId) return false;
  const refs = object(value.reviewRefs);
  const debt = object(value.humanReviewDebt);
  const strict = object(refs?.strict);
  if (!refs || !debt || !Object.keys(debt).length || !artifact(strict) || !nonempty(strict?.reviewId)
    || !artifact(refs.agent) || !artifact(refs.ratification) || !Array.isArray(refs.independent) || refs.independent.length !== 2) return false;
  const independent = refs.independent.map(object);
  if (!independent.every((ref) => ref && artifact(ref) && nonempty(ref.artifactId) && nonempty(ref.reviewerRunId)
    && ["source_auditor", "evidence_correlator"].includes(String(ref.reviewerRole)) && ref.verdict === verdict
    && Array.isArray(ref.reviewedCandidateIds) && ref.reviewedCandidateIds.length === 1 && ref.reviewedCandidateIds[0] === value.candidateId)
    || ["reviewerRole", "reviewerRunId", "artifactPath", "artifactSha256"].some((key) => independent[0]?.[key] === independent[1]?.[key])) return false;
  const artifacts = [strict, object(refs.agent), ...independent, object(refs.ratification)];
  return !["artifactPath", "artifactSha256"].some((key) => new Set(artifacts.map((ref) => String(ref?.[key]).toLowerCase())).size !== 5);
}

/** Check the explicitly declared membership, without adding any transitive identity decision. */
function directMembership(group: Record<string, unknown>, occurrenceIds: string[]): boolean {
  if (group.directEdges !== undefined && group.lineage === undefined) return false;
  if (group.lineage !== undefined) {
    const lineage = object(group.lineage);
    if (!lineage || lineage.version !== 1 || lineage.seedResolutionCommandId !== group.resolutionCommandId
      || ![lineage.headCommandId, lineage.predecessorCommandId].every(identifier)
      || !sha256(lineage.headResultSha256) || !sha256(lineage.predecessorResultSha256)) return false;
  }
  if (group.directEdges === undefined) return occurrenceIds.length === 2 && group.lineage === undefined;
  if (!Array.isArray(group.directEdges) || group.directEdges.length !== occurrenceIds.length - 1) return false;
  const pairs = new Set<string>();
  const candidates = new Set<string>();
  const commands = new Set<string>();
  const neighbors = new Map(occurrenceIds.map((id) => [id, new Set<string>()]));
  let seedFound = false;
  for (const value of group.directEdges) {
    const edge = object(value);
    if (!edge || !pair(edge.occurrenceIds) || !reviewedDecision(edge, edge.candidateId === group.candidateId
      ? "approve_agent_source_only_identity" : "approve_agent_identity_source_group_growth")) return false;
    // An edge is source-only; conflicting explicit semantics cannot be admitted.
    if ((edge.personId !== undefined && edge.personId !== null)
      || (edge.identityDecisionTier !== undefined && edge.identityDecisionTier !== "agent_approved_human_pending")) return false;
    const [from, to] = edge.occurrenceIds;
    const key = JSON.stringify([from, to].sort());
    if (!neighbors.has(from) || !neighbors.has(to) || pairs.has(key) || candidates.has(String(edge.candidateId))
      || commands.has(String(edge.resolutionCommandId)) || commands.has(String(edge.ratificationCommandId))) return false;
    pairs.add(key); candidates.add(String(edge.candidateId));
    commands.add(String(edge.resolutionCommandId)); commands.add(String(edge.ratificationCommandId));
    neighbors.get(from)!.add(to); neighbors.get(to)!.add(from);
    if (edge.candidateId === group.candidateId) {
      if (edge.resolutionCommandId !== group.resolutionCommandId || edge.ratificationCommandId !== group.ratificationCommandId
        || !sameValue(edge.reviewRefs, group.reviewRefs) || !sameValue(edge.humanReviewDebt, group.humanReviewDebt)) return false;
      seedFound = true;
    }
  }
  if (!seedFound) return false;
  const seen = new Set<string>();
  const pending = [occurrenceIds[0]!];
  while (pending.length) {
    const id = pending.pop()!;
    if (seen.has(id)) continue;
    seen.add(id);
    pending.push(...neighbors.get(id)!);
  }
  return seen.size === occurrenceIds.length;
}

export interface SourceIdentityMember {
  occurrenceId: string;
  sourceId: string;
  literalName: string;
  sourceTitle: string;
  role: string;
  readingStatus: string;
}

export interface SourceIdentityGroupModel extends PresentationSourceIdentityGroup {
  members: SourceIdentityMember[];
}

export interface SourceIdentityProposal {
  id: string;
  members: SourceIdentityMember[];
  researchPriority: number | null;
}

/** Unresolved proposals remain separate from the admitted membership and direct decisions. */
export function projectSourceIdentityProposals(payload: PresentationPayload, group: SourceIdentityGroupModel): SourceIdentityProposal[] {
  return payload.identityCandidates.flatMap((candidate) => {
    if (hidden(candidate as unknown as Record<string, unknown>) || !identifier(candidate.id) || candidate.status !== "unresolved"
      || !pair([candidate.fromOccurrenceId, candidate.toOccurrenceId])
      || ![candidate.fromOccurrenceId, candidate.toOccurrenceId].some((id) => group.occurrenceIds.includes(id))
      || payload.identityCandidates.filter((item) => item.id === candidate.id).length !== 1) return [];
    const members: SourceIdentityMember[] = [];
    for (const occurrenceId of [candidate.fromOccurrenceId, candidate.toOccurrenceId]) {
      const matches = payload.occurrences.map(object).filter((value) => value?.id === occurrenceId);
      const occurrence = matches[0];
      const sources = payload.sources.filter((source) => source.id === occurrence?.sourceId);
      const source = sources[0];
      if (matches.length !== 1 || !occurrence || hidden(occurrence) || sources.length !== 1 || !source || !identifier(source.id)
        || hidden(source as unknown as Record<string, unknown>)) return [];
      members.push({ occurrenceId, sourceId: source.id, literalName: nonempty(occurrence.name) ? occurrence.name : "Zapis nazwy niedostępny",
        sourceTitle: source.title, role: typeof occurrence.role === "string" ? occurrence.role : "", readingStatus: typeof occurrence.status === "string" ? occurrence.status : "unknown" });
    }
    return [{ id: candidate.id, members, researchPriority: candidate.scoreSemantics === "research_priority_not_probability"
      && typeof candidate.score === "number" && Number.isFinite(candidate.score) ? candidate.score : null }];
  });
}

/** Presents only backend-admitted direct groups; names and family topology never admit identity. */
export function projectSourceIdentityGroups(payload: PresentationPayload): SourceIdentityGroupModel[] {
  if (!Array.isArray(payload.sourceIdentityGroups)) return [];
  const occurrences = new Map<string, Record<string, unknown>>();
  const duplicateOccurrences = new Set<string>();
  for (const value of payload.occurrences) {
    const occurrence = object(value);
    if (!occurrence || !identifier(occurrence.id)) continue;
    if (occurrences.has(occurrence.id)) duplicateOccurrences.add(occurrence.id);
    occurrences.set(occurrence.id, occurrence);
  }
  const sources = new Map(payload.sources.map((source) => [source.id, source]));
  const duplicateSources = new Set(payload.sources.filter((source, index) => payload.sources.findIndex((item) => item.id === source.id) !== index).map((source) => source.id));
  // Count even malformed entries so an overlapping claim cannot leave one admitted side visible.
  const counts = new Map<string, number>();
  const idCounts = new Map<string, number>();
  const candidateCounts = new Map<string, number>();
  for (const value of payload.sourceIdentityGroups) {
    const group = object(value);
    if (!group) continue;
    for (const id of ["id", "candidateId"] as const) {
      const map = id === "id" ? idCounts : candidateCounts;
      if (identifier(group[id])) map.set(group[id], (map.get(group[id]) || 0) + 1);
    }
    if (Array.isArray(group.occurrenceIds)) for (const id of group.occurrenceIds) {
      if (identifier(id)) counts.set(id, (counts.get(id) || 0) + 1);
    }
  }
  return payload.sourceIdentityGroups.flatMap((value) => {
    const group = object(value);
    if (!group || hidden(group) || ![group.id, group.candidateId, group.resolutionCommandId, group.ratificationCommandId].every(identifier)
      || group.resolutionCommandId === group.ratificationCommandId || group.personId !== null
      || group.identityDecisionTier !== "agent_approved_human_pending" || !identifiers(group.occurrenceIds)
      || !Array.isArray(group.sourceItemIds) || group.sourceItemIds.length !== group.occurrenceIds.length || !group.sourceItemIds.every(identifier)
      || new Set(group.sourceItemIds).size !== group.occurrenceIds.length
      || idCounts.get(String(group.id)) !== 1 || candidateCounts.get(String(group.candidateId)) !== 1
      || group.occurrenceIds.some((id) => counts.get(id) !== 1 || duplicateOccurrences.has(id))) return [];
    if (!reviewedDecision(group) || !directMembership(group, group.occurrenceIds)) return [];
    const members: SourceIdentityMember[] = [];
    for (let index = 0; index < group.occurrenceIds.length; index += 1) {
      const occurrenceId = group.occurrenceIds[index]!;
      const sourceId = group.sourceItemIds[index]!;
      const occurrence = occurrences.get(occurrenceId);
      const source = sources.get(sourceId);
      if (!occurrence || !source || duplicateSources.has(sourceId) || hidden(occurrence) || hidden(source as unknown as Record<string, unknown>)
        || occurrence.personId !== null || occurrence.sourceId !== sourceId || occurrence.externalTreeCandidate === true || source.type !== "act") return [];
      members.push({ occurrenceId, sourceId, literalName: nonempty(occurrence.name) ? occurrence.name : "Zapis nazwy niedostępny", sourceTitle: source.title,
        role: typeof occurrence.role === "string" ? occurrence.role : "", readingStatus: typeof occurrence.status === "string" ? occurrence.status : "unknown" });
    }
    return [{ ...value, members }];
  });
}

export const sourceIdentityPath = (id: string): string => `/source-identities/${encodeURIComponent(id)}`;
export const sourceOccurrencePath = (id: string): string => `/source-occurrences/${encodeURIComponent(id)}`;

export interface SourceIdentityFamilyRow {
  id: string;
  relations: PersonSourceRelationship[];
  group: SourceIdentityGroupModel | null;
}

/** Collapse exact admitted endpoints under the same source-reported role; preserve every evidence row. */
export function groupSourceIdentityFamilyRows(relations: PersonSourceRelationship[], groups: SourceIdentityGroupModel[]): SourceIdentityFamilyRow[] {
  const rows: SourceIdentityFamilyRow[] = [];
  const childPredicates = new Set(["father-of", "ojciec-of", "mother-of", "matka-of", "parent-of", "rodzic-of", "child-of-father", "child-of-mother", "child-of", "dziecko-of", "source-grammar-child-of", "source-grammar-paternal-reference-via-his-wife"]);
  for (const relation of relations) {
    const group = relation.relationLabel === "Dziecko" && relation.predicates.length > 0
      && relation.predicates.every((predicate) => childPredicates.has(predicate.replaceAll("_", "-")))
      && !relation.personIdentityResolved && !relation.relationshipCanonical
      ? groups.find((item) => {
        const members = relation.sources.flatMap((source) => item.members.filter((member) => member.sourceId === source.sourceId && source.occurrenceIds.includes(member.occurrenceId)));
        return new Set(members.map((member) => member.occurrenceId)).size === 1;
      }) || null : null;
    const key = group ? JSON.stringify([group.id, relation.relationLabel]) : relation.id;
    const existing = group ? rows.find((row) => row.id === key) : null;
    if (existing) existing.relations.push(relation);
    else rows.push({ id: key, group, relations: [relation] });
  }
  return rows.flatMap((row) => {
    if (!row.group) return [row];
    const linkedEndpoints = new Set(row.relations.flatMap((relation) => relation.sources.flatMap((source) => row.group!.members
      .filter((member) => member.sourceId === source.sourceId && source.occurrenceIds.includes(member.occurrenceId)).map((member) => member.occurrenceId))));
    return linkedEndpoints.size === row.group.members.length ? [row] : row.relations.map((relation) => ({ id: relation.id, group: null, relations: [relation] }));
  });
}
