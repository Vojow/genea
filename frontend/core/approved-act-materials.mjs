import { eventTypeLabel, polishCount, roleLabel } from "./formatting.mjs";

const TIERS = new Set(["agent_approved_human_pending", "human_confirmed"]);
const PRIVATE = new Set(["restricted", "living", "private", "family_private", "masked_living"]);
const identifier = (value) => typeof value === "string" && Boolean(value) && !/[\s/\\]/.test(value);

function hidden(item) {
  return item?.restrictedBacking === true || item?.sourceRedacted === true
    || PRIVATE.has(typeof item?.privacy === "string" ? item.privacy : item?.privacy?.status);
}

function directChains(occurrence) {
  const chains = occurrence?.sourceProfileIdentityDirectChains;
  if (occurrence?.sourceProfileIdentityGuarded !== true || occurrence.personIdentityResolved !== true
    || occurrence.identityStatus !== "resolved_same" || !TIERS.has(occurrence.identityDecisionTier)
    || !Array.isArray(chains) || !chains.length
    || !chains.every((chain) => chain && typeof chain === "object" && !Array.isArray(chain)
      && [chain.candidateId, chain.commandId, chain.decisionId].every(identifier))) return null;
  return new Set(chains.map((chain) => JSON.stringify([chain.candidateId, chain.commandId, chain.decisionId])));
}

export function projectApprovedActMaterials({ publicProfileId, dataIndex }) {
  const empty = { materials: [], appearances: [], sourceCount: 0, occurrenceCount: 0,
    appearanceSummary: "0 aktów · 0 wystąpień" };
  if (!identifier(publicProfileId) || !dataIndex?.personContext || !dataIndex.occurrences || !dataIndex.sources) return empty;
  const context = dataIndex.personContext({ id: publicProfileId });
  if (!context || hidden(context) || !Array.isArray(context.occurrenceIds)
    || !Array.isArray(context.canonicalPersonIds) || !context.canonicalPersonIds.length
    || !Array.isArray(context.sourceIds)
    || ![context.occurrenceIds, context.canonicalPersonIds, context.sourceIds].every((items) => items.every(identifier))
    || (context.treePersonIds != null && !Array.isArray(context.treePersonIds))
    || (context.aliases != null && !Array.isArray(context.aliases))
    || ![context.publicProfileId, ...(context.treePersonIds || []), ...(context.aliases || [])].includes(publicProfileId)) return empty;
  const occurrences = [...new Set(context.occurrenceIds)].map((id) => dataIndex.occurrences.get(id))
    .filter((item) => item && context.occurrenceIds.includes(item.id) && context.canonicalPersonIds.includes(item.personId));
  const anchors = occurrences.filter((item) => item.externalTreeCandidate === true && !item.restrictedBacking
    && !PRIVATE.has(typeof item.privacy === "string" ? item.privacy : item.privacy?.status))
    .map((item) => ({ item, chains: directChains(item) })).filter((anchor) => anchor.chains);
  if (!anchors.length) return empty;
  const admitted = occurrences.filter((item) => {
    if (!identifier(item.id) || !identifier(item.sourceId) || !context.sourceIds.includes(item.sourceId)
      || item.externalTreeCandidate === true || hidden(item)) return false;
    const source = dataIndex.sources.get(item.sourceId);
    const chains = directChains(item);
    return source?.id === item.sourceId && source.type === "act" && !hidden(source) && chains
      && anchors.some((anchor) => anchor.item.personId === item.personId
        && anchor.item.identityDecisionTier === item.identityDecisionTier
        && [...chains].some((chain) => anchor.chains.has(chain)));
  });
  const materialsById = new Map();
  const appearances = admitted.map((item) => {
    const source = dataIndex.sources.get(item.sourceId);
    const events = (dataIndex.eventsByOccurrence?.get(item.id) || []).filter((event) => event.sourceId === source.id);
    const event = events[0];
    const review = dataIndex.sourceReview?.(source.id) || null;
    const material = materialsById.get(source.id) || { sourceId: source.id, title: source.title || "Akt źródłowy", sourceTitle: source.title || "Akt źródłowy", occurrenceIds: [], eventIds: [], review };
    material.occurrenceIds.push(item.id);
    material.eventIds.push(...events.map((candidate) => candidate.id).filter(identifier));
    materialsById.set(source.id, material);
    return { id: item.id, sourceId: source.id, source: source.title || "Akt źródłowy", sourceTitle: source.title || "Akt źródłowy",
      label: [eventTypeLabel(event?.type || "unknown"), roleLabel(item.role)].filter(Boolean).join(" · "),
      type: eventTypeLabel(event?.type || "unknown"), occurrenceIds: [item.id], eventIds: events.map((candidate) => candidate.id).filter(identifier),
      roles: item.role ? [roleLabel(item.role)] : [], review };
  }).sort((first, second) => first.sourceId.localeCompare(second.sourceId) || first.id.localeCompare(second.id));
  const materials = [...materialsById.values()].map((item) => ({ ...item,
    occurrenceIds: [...new Set(item.occurrenceIds)].sort(), eventIds: [...new Set(item.eventIds)].sort(),
  })).sort((first, second) => first.sourceId.localeCompare(second.sourceId));
  return { materials, appearances, sourceCount: materials.length, occurrenceCount: appearances.length,
    appearanceSummary: `${polishCount(materials.length, "akt", "akty", "aktów")} · ${polishCount(appearances.length, "wystąpienie", "wystąpienia", "wystąpień")}` };
}
