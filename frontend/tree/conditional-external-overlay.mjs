const ATTACHMENT_KIND = "external_profile_identity_attachment";
const SAFE_IDENTITY_TIERS = new Set([
  "agent_approved_human_pending",
  "human_confirmed",
]);

function idSet(values) {
  return values instanceof Set ? values : new Set(values || []);
}

function safeSourceEdge(edge, visibleCandidateIds) {
  return Boolean(
    edge?.fromTreeNodeId
    && edge?.toTreeNodeId
    && visibleCandidateIds.has(edge.fromTreeNodeId)
    && visibleCandidateIds.has(edge.toTreeNodeId)
    && edge.canonicalRelationship === false
    && edge.relationshipIdentityStatus === "unresolved",
  );
}

function safeAttachmentShape(attachment, networkId) {
  return Boolean(
    attachment?.id
    && attachment.networkId === networkId
    && attachment.fromTreeNodeId
    && attachment.toTreePersonId
    && attachment.kind === ATTACHMENT_KIND
    && attachment.identityStatus === "resolved_same"
    && SAFE_IDENTITY_TIERS.has(attachment.identityDecisionTier)
    && attachment.canonicalRelationship === false
    && attachment.relationshipIdentityStatus === "unresolved"
    && attachment.automaticMerge === false,
  );
}

export function projectConditionalExternalOverlay({
  model,
  attachments = [],
  networkId,
  candidateRole = "all",
  visibleTreePersonIds = [],
} = {}) {
  const network = model?.network?.(networkId) || null;
  if (!network) {
    return {
      network: null,
      people: [],
      edges: [],
      attachments: [],
      ambiguousCandidateIds: [],
      attachmentNotice: "Brak wybranej publicznej sieci kandydackiej.",
    };
  }

  const canonicalIds = idSet(visibleTreePersonIds);
  const sourceProjection = model.networkProjection(networkId, candidateRole);
  const people = (sourceProjection.people || []).filter((person) => (
    person?.id && !canonicalIds.has(person.id)
  ));
  const candidateById = new Map(people.map((person) => [person.id, person]));
  const candidateIds = new Set(people.map((person) => person.id));
  const edges = (sourceProjection.edges || []).filter((edge) => safeSourceEdge(edge, candidateIds));

  const shaped = (Array.isArray(attachments) ? attachments : [])
    .filter((attachment) => safeAttachmentShape(attachment, networkId));
  const targetsByCandidate = new Map();
  for (const attachment of shaped) {
    const targets = targetsByCandidate.get(attachment.fromTreeNodeId) || new Set();
    targets.add(attachment.toTreePersonId);
    targetsByCandidate.set(attachment.fromTreeNodeId, targets);
  }
  const ambiguousCandidateIds = new Set(
    [...targetsByCandidate]
      .filter(([, targets]) => targets.size !== 1)
      .map(([candidateId]) => candidateId),
  );
  const uniquePairs = new Set();
  const visibleAttachments = shaped.filter((attachment) => {
    if (ambiguousCandidateIds.has(attachment.fromTreeNodeId)) return false;
    if (!candidateIds.has(attachment.fromTreeNodeId) || !canonicalIds.has(attachment.toTreePersonId)) return false;
    const sourceNodes = (network.nodes || []).filter((node) => node.treeNodeId === attachment.fromTreeNodeId);
    if (sourceNodes.length !== 1 || sourceNodes[0].personIdentityResolved !== true) return false;
    const candidate = candidateById.get(attachment.fromTreeNodeId);
    if (candidate?.personIdentityResolved !== true
      || candidate?.profileIdentityStatus !== attachment.identityStatus
      || candidate?.identityDecisionTier !== attachment.identityDecisionTier) return false;
    const pair = `${attachment.fromTreeNodeId}\u0000${attachment.toTreePersonId}`;
    if (uniquePairs.has(pair)) return false;
    uniquePairs.add(pair);
    return true;
  });

  return {
    network,
    people,
    edges,
    attachments: visibleAttachments,
    ambiguousCandidateIds: [...ambiguousCandidateIds].sort((first, second) => first.localeCompare(second, "pl")),
    attachmentNotice: visibleAttachments.length
      ? "Powiązanie profilu; relacje zewnętrzne niezweryfikowane."
      : "Gałąź kandydacka bez jawnego powiązania profilu z widoczną osobą drzewa.",
  };
}

export function layoutConditionalExternalOverlay({
  projection,
  layout,
  canonicalWidth,
  gap = 150,
  minimumIslandWidth = 620,
} = {}) {
  const people = projection?.people || [];
  if (!people.length || !layout) {
    return {
      positions: new Map(),
      width: canonicalWidth || 0,
      islandX: canonicalWidth || 0,
      islandWidth: 0,
      generationSpan: 0,
    };
  }
  const islandWidth = Math.max(minimumIslandWidth, layout.requiredWidth(people, 140, []));
  const local = layout.positions(people, islandWidth, 1, []);
  const islandX = Math.max(0, canonicalWidth || 0) + gap;
  const positions = new Map([...local.positions].map(([personId, position]) => [personId, {
    ...position,
    x: position.x + islandX,
  }]));
  return {
    positions,
    width: islandX + Math.max(islandWidth, local.width || 0),
    islandX,
    islandWidth: Math.max(islandWidth, local.width || 0),
    generationSpan: local.generationSpan || 0,
  };
}

export const CONDITIONAL_EXTERNAL_ATTACHMENT_KIND = ATTACHMENT_KIND;
