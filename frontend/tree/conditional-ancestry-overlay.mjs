const SUPPORTED_RELATION_KINDS = new Set(["source_stated", "parentage_hypothesis"]);
const MASKED_PRIVACY = new Set(["masked", "masked_living", "private", "restricted"]);

function array(value) {
  return Array.isArray(value) ? value : [];
}

function polishCount(value, one, few, many) {
  const number = Number(value) || 0;
  const lastTwo = number % 100;
  const label = number === 1 ? one : number % 10 >= 2 && number % 10 <= 4 && !(lastTwo >= 12 && lastTwo <= 14) ? few : many;
  return `${number} ${label}`;
}

function isMasked(node) {
  const privacy = typeof node?.privacy === "string" ? node.privacy : node?.privacy?.status;
  return node?.sourceRedacted === true
    || node?.privacy?.redacted === true
    || MASKED_PRIVACY.has(String(privacy || "").toLocaleLowerCase("pl"))
    || MASKED_PRIVACY.has(String(node?.evidenceStatus || "").toLocaleLowerCase("pl"));
}

function nodeInRootNamespace(node, root) {
  if (!node || !root) return false;
  if (root.scope === "source_reported_network") {
    return node.scope === "source_reported_network"
      && typeof root.networkId === "string"
      && node.networkId === root.networkId;
  }
  return node.scope !== "source_reported_network";
}

function rootAudit(audit, rootId) {
  return array(audit?.rootAudits).find((item) => item?.rootId === rootId) || null;
}

function safeLimits(audit) {
  const limits = audit?.limits || {};
  return {
    maxDepth: Number.isFinite(limits.maxDepth) ? limits.maxDepth : null,
    maxNodes: Number.isFinite(limits.maxNodes) ? limits.maxNodes : null,
    maxEdges: Number.isFinite(limits.maxEdges) ? limits.maxEdges : null,
  };
}

function displayId(node, canonicalTreePersonId) {
  return canonicalTreePersonId || `conditional-ancestor:${node.id}`;
}

function privacySafeRootCounts(conditionalAncestry, root) {
  const nodes = new Map(array(conditionalAncestry?.nodes)
    .filter((item) => typeof item?.id === "string" && !isMasked(item) && nodeInRootNamespace(item, root))
    .map((item) => [item.id, item]));
  const rootNodeId = root?.nodeId;
  if (!nodes.has(rootNodeId)) return { nodes: 0, edges: 0 };
  const outgoing = new Map();
  array(conditionalAncestry?.edges).forEach((edge) => {
    if (typeof edge?.id !== "string"
      || edge?.direction !== "child_to_parent"
      || edge?.canonicalRelationship !== false
      || !SUPPORTED_RELATION_KINDS.has(edge?.relationKind)
      || !nodes.has(edge.fromNodeId)
      || !nodes.has(edge.toNodeId)) return;
    const values = outgoing.get(edge.fromNodeId) || [];
    values.push(edge);
    outgoing.set(edge.fromNodeId, values);
  });
  outgoing.forEach((values) => values.sort((first, second) => (
    first.toNodeId.localeCompare(second.toNodeId) || first.id.localeCompare(second.id)
  )));
  const limits = safeLimits(conditionalAncestry?.audit);
  const maxDepth = limits.maxDepth ?? Number.POSITIVE_INFINITY;
  const maxNodes = limits.maxNodes ?? Number.POSITIVE_INFINITY;
  const maxEdges = limits.maxEdges ?? Number.POSITIVE_INFINITY;
  const seenNodes = new Set([rootNodeId]);
  const seenEdges = new Set();
  const queue = [{ nodeId: rootNodeId, depth: 0 }];
  while (queue.length) {
    const { nodeId: current, depth } = queue.shift();
    if (depth >= maxDepth) continue;
    for (const edge of outgoing.get(current) || []) {
      if (!seenEdges.has(edge.id) && seenEdges.size >= maxEdges) continue;
      if (!seenNodes.has(edge.toNodeId) && seenNodes.size >= maxNodes) continue;
      seenEdges.add(edge.id);
      if (seenNodes.has(edge.toNodeId)) continue;
      seenNodes.add(edge.toNodeId);
      queue.push({ nodeId: edge.toNodeId, depth: depth + 1 });
    }
  }
  return { nodes: seenNodes.size, edges: seenEdges.size };
}

function gapConditionLabel(gap) {
  if (typeof gap?.condition === "string") return gap.condition;
  const kind = gap?.condition?.kind || gap?.kind;
  const labels = {
    explicit_public_endpoint_required: "wskazać jawny, publiczny i jednoznaczny endpoint relacji",
    distinct_endpoints_required: "rozdzielić dwa różne endpointy relacji",
    supported_provenance_required: "uzupełnić kwalifikowaną proweniencję relacji",
    public_relationship_backing_required: "uzupełnić publiczne backing ID relacji",
    explicit_structured_hypothesis_required: "zapisać hipotezę z jawnymi końcami i proweniencją",
    explicit_parentage_evidence_required: "odnaleźć i zapisać jawną podstawę rodzicielstwa",
    path_local_cycle_review_required: "sprawdzić cykl w tej konkretnej ścieżce",
    increase_max_depth: `zwiększyć limit głębokości${Number.isFinite(gap?.condition?.limit) ? ` ponad ${gap.condition.limit}` : ""}`,
    increase_max_nodes: `zwiększyć limit węzłów${Number.isFinite(gap?.condition?.limit) ? ` ponad ${gap.condition.limit}` : ""}`,
    increase_max_edges: `zwiększyć limit krawędzi${Number.isFinite(gap?.condition?.limit) ? ` ponad ${gap.condition.limit}` : ""}`,
  };
  return labels[kind] || gap?.label || "uzupełnić wskazaną lukę danych";
}

export function conditionalAncestryRootOptions(conditionalAncestry) {
  if (!conditionalAncestry || ![conditionalAncestry.schemaVersion, conditionalAncestry.version].includes(1)) return [];
  const nodeById = new Map(array(conditionalAncestry.nodes)
    .filter((item) => typeof item?.id === "string")
    .map((item) => [item.id, item]));
  return array(conditionalAncestry.roots).filter((root) => (
    typeof root?.id === "string"
      && typeof root?.nodeId === "string"
      && typeof root?.label === "string"
      && nodeById.has(root.nodeId)
      && !isMasked(nodeById.get(root.nodeId))
      && nodeInRootNamespace(nodeById.get(root.nodeId), root)
  )).map((root) => {
    const audit = rootAudit(conditionalAncestry.audit, root.id);
    const visibleCounts = privacySafeRootCounts(conditionalAncestry, root);
    return {
      id: root.id,
      nodeId: root.nodeId,
      treePersonId: root.treePersonId || null,
      label: root.label,
      scope: root.scope || "family_tree",
      networkId: root.networkId || null,
      visibleNodeCount: visibleCounts.nodes,
      visibleEdgeCount: visibleCounts.edges,
      complete: audit?.complete === true,
      truncated: audit?.truncated === true,
    };
  });
}

export function defaultConditionalAncestryRootId(roots, preferredTreePersonId = null) {
  const preferred = array(roots).find((root) => root.treePersonId === preferredTreePersonId);
  if (preferred) return preferred.id;
  return [...array(roots)].sort((first, second) => (
    second.visibleEdgeCount - first.visibleEdgeCount
      || second.visibleNodeCount - first.visibleNodeCount
      || first.label.localeCompare(second.label, "pl")
      || first.id.localeCompare(second.id)
  ))[0]?.id || null;
}

export function projectConditionalAncestry({
  conditionalAncestry,
  rootId,
  canonicalPeople = [],
  visibleTreePersonIds = [],
} = {}) {
  const roots = conditionalAncestryRootOptions(conditionalAncestry);
  const root = roots.find((item) => item.id === rootId) || roots[0] || null;
  const audit = conditionalAncestry?.audit || {};
  const limits = safeLimits(audit);
  const empty = {
    root,
    nodes: [],
    cards: [],
    edges: [],
    gaps: [],
    depth: 0,
    cycleCount: 0,
    missingEndpointCount: 0,
    audit,
    rootAudit: root ? rootAudit(audit, root.id) : null,
    limits,
    truncated: Boolean(audit.truncated),
  };
  if (!root) return empty;

  const canonicalCounts = new Map();
  for (const person of canonicalPeople) {
    if (typeof person?.id !== "string") continue;
    canonicalCounts.set(person.id, (canonicalCounts.get(person.id) || 0) + 1);
  }
  const visibleCanonical = new Set(visibleTreePersonIds);
  const rawNodes = new Map(array(conditionalAncestry.nodes)
    .filter((item) => typeof item?.id === "string")
    .map((item) => [item.id, item]));
  const outgoing = new Map();
  const validEdges = array(conditionalAncestry.edges).filter((edge) => (
    typeof edge?.id === "string"
      && edge.direction === "child_to_parent"
      && SUPPORTED_RELATION_KINDS.has(edge.relationKind)
      && edge.canonicalRelationship === false
  ));
  validEdges.forEach((edge) => {
    const values = outgoing.get(edge.fromNodeId) || [];
    values.push(edge);
    outgoing.set(edge.fromNodeId, values);
  });
  outgoing.forEach((values) => values.sort((first, second) => (
    first.toNodeId.localeCompare(second.toNodeId) || first.id.localeCompare(second.id)
  )));

  const depthByNodeId = new Map([[root.nodeId, 0]]);
  const uncertainByNodeId = new Map([[root.nodeId, false]]);
  const queue = [root.nodeId];
  const reachableEdges = [];
  const reachableEdgeIds = new Set();
  const missingEndpoints = [];
  const localStopReasons = new Set();
  let localFrontierCount = 0;
  let locallyTruncated = false;
  const maxDepth = limits.maxDepth ?? Number.POSITIVE_INFINITY;
  const maxNodes = limits.maxNodes ?? Number.POSITIVE_INFINITY;
  const maxEdges = limits.maxEdges ?? Number.POSITIVE_INFINITY;
  while (queue.length) {
    const currentId = queue.shift();
    const currentDepth = depthByNodeId.get(currentId) || 0;
    const currentUncertain = uncertainByNodeId.get(currentId) === true;
    const visibleOutgoing = (outgoing.get(currentId) || []).filter((edge) => {
      const source = rawNodes.get(edge.fromNodeId);
      const target = rawNodes.get(edge.toNodeId);
      return source && target
        && !isMasked(source) && !isMasked(target)
        && nodeInRootNamespace(source, root) && nodeInRootNamespace(target, root);
    });
    if (currentDepth >= maxDepth && visibleOutgoing.length) {
      locallyTruncated = true;
      localFrontierCount += 1;
      localStopReasons.add("max_depth");
      continue;
    }
    for (const edge of outgoing.get(currentId) || []) {
      const target = rawNodes.get(edge.toNodeId);
      const source = rawNodes.get(edge.fromNodeId);
      if (!source || !target) {
        missingEndpoints.push(edge.id);
        continue;
      }
      if (isMasked(source) || isMasked(target)
        || !nodeInRootNamespace(source, root) || !nodeInRootNamespace(target, root)) continue;
      if (!reachableEdgeIds.has(edge.id) && reachableEdgeIds.size >= maxEdges) {
        locallyTruncated = true;
        localFrontierCount += 1;
        localStopReasons.add("max_edges");
        continue;
      }
      if (!depthByNodeId.has(edge.toNodeId) && depthByNodeId.size >= maxNodes) {
        locallyTruncated = true;
        localFrontierCount += 1;
        localStopReasons.add("max_nodes");
        continue;
      }
      if (!reachableEdgeIds.has(edge.id)) {
        reachableEdgeIds.add(edge.id);
        reachableEdges.push(edge);
      }
      const nextDepth = currentDepth + 1;
      const nextUncertain = currentUncertain
        || edge.status === "unresolved"
        || edge.relationKind === "parentage_hypothesis";
      if (depthByNodeId.has(edge.toNodeId)) {
        uncertainByNodeId.set(edge.toNodeId, uncertainByNodeId.get(edge.toNodeId) || nextUncertain);
        continue;
      }
      depthByNodeId.set(edge.toNodeId, nextDepth);
      uncertainByNodeId.set(edge.toNodeId, nextUncertain);
      queue.push(edge.toNodeId);
    }
  }
  const selectedAdjacency = new Map();
  reachableEdges.forEach((edge) => {
    const values = selectedAdjacency.get(edge.fromNodeId) || [];
    values.push(edge.toNodeId);
    selectedAdjacency.set(edge.fromNodeId, values);
  });
  const visitedForCycles = new Set();
  const activePath = new Set();
  let cycleCount = 0;
  const visitForCycles = (nodeId) => {
    if (visitedForCycles.has(nodeId)) return;
    visitedForCycles.add(nodeId);
    activePath.add(nodeId);
    for (const targetId of selectedAdjacency.get(nodeId) || []) {
      if (activePath.has(targetId)) {
        cycleCount += 1;
        continue;
      }
      visitForCycles(targetId);
    }
    activePath.delete(nodeId);
  };
  visitForCycles(root.nodeId);
  if (cycleCount) localStopReasons.add("cycle");

  const nodes = [...depthByNodeId.entries()].flatMap(([nodeId, depth], stableIndex) => {
    const item = rawNodes.get(nodeId);
    if (!item || isMasked(item)) return [];
    const canonicalTreePersonId = item.identityScope === "canonical"
      && typeof item.treePersonId === "string"
      && canonicalCounts.get(item.treePersonId) === 1
      && visibleCanonical.has(item.treePersonId)
      ? item.treePersonId
      : null;
    return [{
      ...item,
      occurrenceId: item.scope === "source_reported_network" ? null : item.occurrenceId || null,
      displayId: displayId(item, canonicalTreePersonId),
      canonicalTreePersonId,
      usesCanonicalCard: Boolean(canonicalTreePersonId),
      depth,
      generation: depth,
      order: stableIndex,
      pathUncertain: uncertainByNodeId.get(nodeId) === true,
    }];
  }).sort((first, second) => first.depth - second.depth || first.order - second.order || first.id.localeCompare(second.id));
  const projectedById = new Map(nodes.map((item) => [item.id, item]));
  const edges = reachableEdges.flatMap((edge) => {
    const from = projectedById.get(edge.fromNodeId);
    const to = projectedById.get(edge.toNodeId);
    if (!from || !to) return [];
    return [{
      ...edge,
      fromDisplayId: from.displayId,
      toDisplayId: to.displayId,
      pathUncertain: from.pathUncertain
        || to.pathUncertain
        || edge.status === "unresolved"
        || edge.relationKind === "parentage_hypothesis",
    }];
  });
  nodes.forEach((item) => {
    const incoming = edges.filter((edge) => edge.toNodeId === item.id);
    item.sourceIds = [...new Set([
      ...array(item.sourceIds),
      ...incoming.flatMap((edge) => array(edge.sourceIds)),
    ])].sort();
    const external = incoming.some((edge) => edge.provenance?.kind === "external_tree");
    const hypothesis = incoming.some((edge) => edge.relationKind === "parentage_hypothesis");
    item.hypothesisBasis = [...new Set(incoming
      .filter((edge) => edge.relationKind === "parentage_hypothesis")
      .map((edge) => edge.basis)
      .filter((basis) => typeof basis === "string" && basis.trim()))];
    item.basisLabel = external
      ? "Rodzicielstwo wskazane w drzewie zewnętrznym (niezweryfikowane)."
      : hypothesis
        ? "Ustrukturyzowana hipoteza rodzicielstwa (nierozstrzygnięta)."
        : incoming.length
          ? "Rodzicielstwo jawnie wskazane w lokalnym źródle lub roli (nierozstrzygnięte)."
          : "Korzeń warunkowego przeglądu.";
  });
  const reachableIds = new Set(nodes.map((item) => item.id));
  const gaps = array(conditionalAncestry.gaps).filter((gap) => (
    gap?.rootId === root.id
      || (!gap?.rootId && (!gap?.nodeId || reachableIds.has(gap.nodeId)))
  )).map((gap) => ({ ...gap, conditionLabel: gapConditionLabel(gap) }));
  const selectedAudit = rootAudit(audit, root.id);
  return {
    root,
    nodes,
    cards: nodes.filter((item) => !item.usesCanonicalCard),
    edges,
    gaps,
    depth: Math.max(0, ...nodes.map((item) => item.depth)),
    cycleCount,
    missingEndpointCount: missingEndpoints.length,
    audit,
    rootAudit: selectedAudit,
    limits,
    traversalComplete: selectedAudit?.complete === true && !locallyTruncated,
    localStopReasons: [...localStopReasons].sort(),
    localFrontierCount,
    truncated: Boolean(locallyTruncated || selectedAudit?.truncated),
  };
}

export function layoutConditionalAncestryOverlay({
  projection,
  layout,
  canonicalWidth = 0,
  gap = 96,
  minimumIslandWidth = 520,
  labelOffset = 104,
} = {}) {
  const cards = projection?.cards || [];
  if (!layout || !projection?.root) return {
    positions: new Map(), width: canonicalWidth, islandX: canonicalWidth, islandWidth: 0,
    generationSpan: projection?.depth || 0,
  };
  if (!cards.length) {
    const islandX = Math.max(0, canonicalWidth) + gap;
    return {
      positions: new Map(),
      width: islandX + minimumIslandWidth,
      islandX,
      islandWidth: minimumIslandWidth,
      generationSpan: projection.depth || 0,
    };
  }
  const layoutCards = cards.map((item) => ({ ...item, id: item.displayId }));
  const islandWidth = Math.max(minimumIslandWidth, layout.requiredWidth(layoutCards, 140, []));
  const local = layout.positions(layoutCards, islandWidth, 1, []);
  const islandX = Math.max(0, canonicalWidth) + gap;
  const positions = new Map([...local.positions].map(([id, position]) => [id, {
    ...position,
    x: position.x + islandX,
    y: position.y + labelOffset,
  }]));
  return {
    positions,
    width: islandX + Math.max(islandWidth, local.width || 0),
    islandX,
    islandWidth: Math.max(islandWidth, local.width || 0),
    generationSpan: Math.max(projection?.depth || 0, (local.generationSpan || 0) + labelOffset / layout.generationStep()),
  };
}

export function conditionalAncestrySummary(projection) {
  const audit = projection?.audit || {};
  const selected = projection?.rootAudit;
  const limitParts = Object.entries(projection?.limits || {})
    .filter(([, value]) => Number.isFinite(value))
    .map(([key, value]) => `${({ maxDepth: "głębokość", maxNodes: "węzły", maxEdges: "krawędzie" })[key]} ${value}`);
  const allRoots = audit.allRootsComplete === true
    ? `Snapshot wszystkich ${audit.rootCount || 0} korzeni przejrzany w zapisanym zakresie.`
    : `Snapshot wszystkich ${audit.rootCount || 0} korzeni jest częściowy.`;
  const selectedRoot = projection?.traversalComplete === true
    ? "Wybrany korzeń przejrzany do granicy zapisanych krawędzi."
    : "Wybrany korzeń ma frontier lub limit widoku; nie oznacza to braku dalszych przodków.";
  return `${polishCount(projection?.nodes?.length, "węzeł warunkowy", "węzły warunkowe", "węzłów warunkowych")} projekcji (nie liczba potwierdzonych osób) · ${polishCount(projection?.edges?.length, "relacja", "relacje", "relacji")} · ${polishCount(projection?.gaps?.length, "luka", "luki", "luk")}. ${allRoots} ${selectedRoot} Kompletność treści źródeł i genealogii nie była oceniana.${limitParts.length ? ` Limity: ${limitParts.join(", ")}.` : ""}`;
}

export function buildConditionalAncestryInspectorModel(node, { fullRecordHref = null } = {}) {
  if (!node) return null;
  const relationBasis = `${node.basisLabel || "Węzeł osiągnięty po jawnej ścieżce warunkowej."} ${node.pathUncertain
    ? "Cała ścieżka pozostaje warunkowa; późniejszy profil nie rozstrzyga wcześniejszego kroku."
    : ""}`.trim();
  const sourceIds = [...new Set(array(node.sourceIds).filter((id) => typeof id === "string"))];
  return {
    personId: node.publicProfileId || node.occurrenceId || node.id,
    identityScope: node.identityScope || "presentation_only",
    identityLabel: node.scope === "source_occurrence"
      ? "Warunkowy przodek · wystąpienie źródłowe"
      : node.publicProfileId ? "Warunkowy przodek · profil publiczny" : "Warunkowy przodek · wystąpienie źródłowe",
    identityNotice: "Ta karta nie potwierdza tożsamości ani rodzicielstwa i nie zmienia drzewa kanonicznego.",
    localIdentityReviews: [],
    identityComponentCount: 0,
    generation: `Warunkowi przodkowie · poziom ${node.depth}`,
    evidence: "nierozstrzygnięte",
    evidenceKind: "is-warning",
    name: node.label || "Wystąpienie bez publicznej etykiety",
    summary: [relationBasis, ...array(node.hypothesisBasis)].join(" "),
    lifeSpanLabel: node.lifeSpanLabel || "?–?",
    fullRecordHref,
    suppressFullRecordLink: !node.publicProfileId,
    review: null,
    structureActions: [],
    family: { parents: [], coParents: [], children: [], siblings: [] },
    unions: [],
    sourceRelations: [],
    candidates: [],
    appearances: [],
    appearanceSummary: `${sourceIds.length} materiałów źródłowych`,
    facts: [],
    materials: sourceIds.map((sourceId) => ({ id: sourceId, title: "Materiał stanowiący podstawę warunkowej ścieżki" })),
    unavailableCount: 0,
    materialTitle: "Podstawa warunkowej ścieżki",
    materialNote: node.publicProfileId
      ? "Pełny profil jest osobną akcją; relacje na tej warstwie pozostają nierozstrzygnięte."
      : "Brak jednoznacznego publicznego profilu. Pokazano wyłącznie kontekst wystąpienia źródłowego.",
    importantFacts: [],
    alerts: [],
    uniqueSources: sourceIds.map((sourceId) => ({
      sourceId,
      title: "Materiał stanowiący podstawę warunkowej ścieżki",
      shortLabel: "Podstawa relacji warunkowej",
      occurrenceIds: node.occurrenceId ? [node.occurrenceId] : [],
      eventIds: [],
      roles: [],
      origins: ["conditional-ancestry"],
      review: null,
    })),
    tabs: [
      { id: "summary", label: "Podsumowanie", count: 0 },
      { id: "family", label: "Rodzina", count: 0 },
      { id: "acts", label: "Akty", count: sourceIds.length },
    ],
  };
}
