const ROLE_PRIORITY = {
  unassigned: 0,
  candidate: 1,
  "fan-extended": 2,
  "fan-direct": 3,
  bridge: 4,
  core: 5,
};

function preferredRole(current, next) {
  return (ROLE_PRIORITY[next] || 0) > (ROLE_PRIORITY[current] || 0) ? next : current;
}

function depthLimit(value) {
  if (value === "all") return Number.POSITIVE_INFINITY;
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed >= 0 ? Math.min(parsed, 2) : 1;
}

function otherEnd(edge, nodeId) {
  return edge.from === nodeId ? edge.to : edge.from;
}

function networkList(value) {
  if (Array.isArray(value)) return value;
  return Array.isArray(value?.networks) ? value.networks : [];
}

const SOURCE_REPORTED_NETWORK_FALLBACK_LABELS = {
  visible: "Sieć kandydacka: widoczny punkt odniesienia",
  masked: "Sieć kandydacka: punkt odniesienia ukryty",
  missing: "Sieć kandydacka: punkt odniesienia nieustalony",
};

function visibleCount(value, fallbackItems = []) {
  const parsed = Number(value);
  if (Number.isInteger(parsed) && parsed >= 0) return parsed;
  return Array.isArray(fallbackItems) ? fallbackItems.length : 0;
}

export function sourceReportedNetworkPresentation(network = {}) {
  const anchorState = ["visible", "missing", "masked"].includes(network?.anchorState)
    ? network.anchorState
    : "missing";
  const suppliedLabel = typeof network?.label === "string" ? network.label.trim() : "";
  const label = anchorState === "visible" && suppliedLabel
    ? suppliedLabel
    : SOURCE_REPORTED_NETWORK_FALLBACK_LABELS[anchorState];
  const visibleProfiles = visibleCount(network?.counts?.visibleProfiles, network?.nodes);
  const visibleRelationships = visibleCount(network?.counts?.visibleRelationships, network?.edges);
  const countsLabel = `Widoczne profile: ${visibleProfiles} · widoczne relacje: ${visibleRelationships}`;
  return {
    label,
    optionLabel: `${label} · ${countsLabel}`,
    countsLabel,
    visibleProfiles,
    visibleRelationships,
  };
}

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
];

export function normalizedCandidateRole(value) {
  return SOURCE_REPORTED_CANDIDATE_ROLES.includes(value) ? value : "all";
}

function classificationRole(classification) {
  if (classification?.state === "conflict") return "conflict";
  if (classification?.state === "unclassified") return "unclassified";
  return classification?.primaryRole || "unclassified";
}

function materializationProjection(network) {
  const state = ["complete", "incomplete"].includes(network?.materializationState)
    ? network.materializationState
    : null;
  const gap = network?.materializationGap && typeof network.materializationGap === "object"
    ? {
        kind: network.materializationGap.kind || null,
        status: network.materializationGap.status || null,
        automaticPersonCreation: typeof network.materializationGap.automaticPersonCreation === "boolean"
          ? network.materializationGap.automaticPersonCreation
          : null,
      }
    : null;
  return { state, gap };
}

function sourceReportedScope(network, index) {
  const networkId = network?.id || `network-${index + 1}`;
  const nodes = Array.isArray(network?.nodes) ? network.nodes : [];
  const presentation = sourceReportedNetworkPresentation(network);
  const classificationsByOccurrenceId = new Map(nodes
    .filter((item) => item?.occurrenceId)
    .map((item) => [item.occurrenceId, item.classification || {
      state: "unclassified",
      primaryRole: null,
      alternatives: [],
      unclassifiedReason: "missing_anchor",
    }]));
  const publicProfileByOccurrenceId = new Map(nodes
    .filter((item) => item?.occurrenceId && item?.treeNodeId)
    .map((item) => [item.occurrenceId, item.treeNodeId]));
  const candidateRoleCounts = {};
  for (const classification of classificationsByOccurrenceId.values()) {
    const role = classificationRole(classification);
    candidateRoleCounts[role] = (candidateRoleCounts[role] || 0) + 1;
  }
  const anchorState = ["visible", "missing", "masked"].includes(network?.anchorState)
    ? network.anchorState
    : "missing";
  const anchor = anchorState === "visible" && network?.anchor
    ? {
        treeNodeId: network.anchor.treeNodeId,
        publicProfileId: network.anchor.publicProfileId,
        label: network.anchor.label,
      }
    : null;
  const anchorWarning = anchorState === "masked"
    ? "Punkt odniesienia został ukryty przez reguły prywatności. Widoczny fragment nie zawiera zastępczego węzła ani łącznika."
    : anchorState === "missing"
      ? "Sieć nie ma ustalonego punktu odniesienia; role pozostają nieklasyfikowane."
      : null;
  const materialization = materializationProjection(network);
  const materializationWarning = materialization.state === "incomplete"
    ? "Kontrolowany fragment ma niepełną materializację. Brakujące profile wymagają kuracji; UI nie tworzy osób automatycznie."
    : null;
  return {
    id: `source-reported:${networkId}`,
    sourceReportedNetworkId: networkId,
    scopeKind: "source_reported_network",
    label: presentation.label,
    optionLabel: presentation.optionLabel,
    countsLabel: presentation.countsLabel,
    visibleProfileCount: presentation.visibleProfiles,
    visibleRelationshipCount: presentation.visibleRelationships,
    description: "Relacje zgłoszone przez zewnętrzne drzewo. Zakres jest stały i nie rozszerza FAN.",
    status: network?.status || null,
    identityStatus: network?.relationshipIdentityStatus || network?.identityStatus || null,
    canonicalRelationship: typeof network?.canonicalRelationship === "boolean" ? network.canonicalRelationship : null,
    automaticMerge: typeof network?.automaticMerge === "boolean" ? network.automaticMerge : null,
    materializationState: materialization.state,
    materializationGap: materialization.gap,
    exactPersonIds: [...new Set(network?.personIds || [])],
    exactOccurrenceIds: [...new Set(network?.occurrenceIds || [])],
    exactRelationshipIds: [...new Set(network?.relationshipIds || [])],
    classificationsByOccurrenceId,
    publicProfileByOccurrenceId,
    candidateRoleCounts,
    anchorState,
    anchor,
    seedOccurrenceIds: [],
    seedHouseholdIds: [],
    excludedOccurrenceIds: [],
    bridgeNodes: [],
    bridgeEdges: [],
    defaultDepth: 0,
    warnings: [
      "Źródło zewnętrzne · relacje nierozstrzygnięte · bez automatycznego scalania.",
      materializationWarning,
      anchorWarning,
    ].filter(Boolean),
  };
}

export class GraphScopeFacade {
  constructor(payload = null, sourceReportedNetworks = []) {
    this.payload = payload || { schemaVersion: 1, defaultScopeId: "all", defaultDepth: 1, scopes: [] };
    const configured = this.payload.scopes || [];
    const external = networkList(sourceReportedNetworks).map(sourceReportedScope);
    this.scopeById = new Map([...configured, ...external].map((scope) => [scope.id, scope]));
  }

  scopes() {
    return [...this.scopeById.values()];
  }

  resolveSourceReportedDeepLink({ networkId, publicProfileId = null, candidateRole = "all" } = {}) {
    if (typeof networkId !== "string" || !networkId) return null;
    if (candidateRole !== normalizedCandidateRole(candidateRole)) return null;
    const scopeId = `source-reported:${networkId}`;
    const scope = this.scopeById.get(scopeId);
    if (!scope || scope.scopeKind !== "source_reported_network") return null;
    const occurrenceId = publicProfileId == null
      ? null
      : [...(scope.publicProfileByOccurrenceId || [])]
          .find(([, publicId]) => publicId === publicProfileId)?.[0] || null;
    if (publicProfileId != null && !occurrenceId) return null;
    if (occurrenceId && candidateRole !== "all") {
      const actualRole = classificationRole(scope.classificationsByOccurrenceId?.get(occurrenceId));
      if (actualRole !== candidateRole) return null;
    }
    return { scopeId, scope, occurrenceId, candidateRole };
  }

  apply(fullGraph, options = {}) {
    const includeCandidates = options.includeCandidates !== false;
    const includeBridges = options.includeBridges !== false;
    const requestedScopeIds = [...new Set(options.scopeIds || [this.payload.defaultScopeId || "all"])];
    const activeScopeIds = requestedScopeIds.filter((id) => id !== "all" && this.scopeById.has(id));
    const nodesById = new Map((fullGraph.nodes || []).map((item) => [item.id, item]));
    const edges = fullGraph.edges || [];
    const documentEdges = edges.filter((edge) => !["candidate", "bridge", "external-relation"].includes(edge.kind));
    const candidateEdges = edges.filter((edge) => edge.kind === "candidate");
    const externalEdges = edges.filter((edge) => edge.kind === "external-relation");
    const adjacency = new Map();
    const addAdjacent = (nodeId, edge) => {
      const list = adjacency.get(nodeId) || [];
      list.push(edge);
      adjacency.set(nodeId, list);
    };
    for (const edge of documentEdges) {
      addAdjacent(edge.from, edge);
      addAdjacent(edge.to, edge);
    }

    const personOccurrences = new Map();
    for (const item of nodesById.values()) {
      if (item.kind !== "occurrence" || !item.item?.personId) continue;
      const list = personOccurrences.get(item.item.personId) || [];
      list.push(item.id);
      personOccurrences.set(item.item.personId, list);
    }

    if (!activeScopeIds.length) {
      return this.#allView(nodesById, edges, includeCandidates);
    }

    const visible = new Set();
    const roleByNodeId = new Map();
    const scopeIdsByNodeId = new Map();
    const warnings = [];
    const selectedScopes = activeScopeIds.map((id) => this.scopeById.get(id));
    const fixedExternalScope = selectedScopes.some((scope) => scope.scopeKind === "source_reported_network");
    const selectedCandidateRole = normalizedCandidateRole(options.candidateRole);
    const exactExternalEdges = [];
    const exactExternalOccurrenceIds = new Set();

    const mark = (nodeId, role, scopeId) => {
      if (!nodesById.has(nodeId) && !nodeId.startsWith("bridge:")) return;
      visible.add(nodeId);
      roleByNodeId.set(nodeId, preferredRole(roleByNodeId.get(nodeId) || "unassigned", role));
      const memberships = scopeIdsByNodeId.get(nodeId) || new Set();
      memberships.add(scopeId);
      scopeIdsByNodeId.set(nodeId, memberships);
    };

    const exactPeers = (nodeIds, excluded) => {
      const result = new Set(nodeIds);
      for (const nodeId of nodeIds) {
        const personId = nodesById.get(nodeId)?.item?.personId;
        if (!personId) continue;
        for (const peerId of personOccurrences.get(personId) || []) {
          if (!excluded.has(peerId)) result.add(peerId);
        }
      }
      return result;
    };

    for (const scope of selectedScopes) {
      warnings.push(...(scope.warnings || []));
      const localVisible = new Set();
      const markScope = (nodeId, role) => {
        mark(nodeId, role, scope.id);
        localVisible.add(nodeId);
      };
      if (scope.scopeKind === "source_reported_network") {
        const relationshipIds = new Set(scope.exactRelationshipIds || []);
        for (const occurrenceId of scope.exactOccurrenceIds || []) {
          const candidateRole = classificationRole(scope.classificationsByOccurrenceId?.get(occurrenceId));
          if (selectedCandidateRole !== "all" && candidateRole !== selectedCandidateRole) continue;
          const nodeId = `occ:${occurrenceId}`;
          if (!nodesById.has(nodeId)) continue;
          markScope(nodeId, "candidate");
          const graphNode = nodesById.get(nodeId);
          graphNode.sourceReportedClassification = scope.classificationsByOccurrenceId?.get(occurrenceId) || null;
          graphNode.sourceReportedCandidateRole = candidateRole;
          graphNode.publicProfileId = scope.publicProfileByOccurrenceId?.get(occurrenceId) || null;
          graphNode.sourceReportedAnchorState = scope.anchorState;
          graphNode.sourceReportedAnchorLabel = scope.anchorState === "visible" ? scope.anchor?.label || null : null;
          graphNode.sourceReportedActiveNetworkId = scope.sourceReportedNetworkId;
          exactExternalOccurrenceIds.add(nodeId);
        }
        for (const edge of externalEdges) {
          const relationshipId = edge.externalRelationshipId || edge.item?.id;
          if (!relationshipIds.has(relationshipId)) continue;
          if (!(edge.sourceReportedNetworkIds || []).includes(scope.sourceReportedNetworkId)) continue;
          if (!localVisible.has(edge.from) || !localVisible.has(edge.to)) continue;
          exactExternalEdges.push({
            ...edge,
            sourceReportedActiveNetworkId: scope.sourceReportedNetworkId,
          });
        }
        continue;
      }
      const excluded = new Set((scope.excludedOccurrenceIds || []).map((id) => `occ:${id}`));
      let core = new Set(
        (scope.seedOccurrenceIds || [])
          .map((id) => `occ:${id}`)
          .filter((id) => nodesById.has(id) && !excluded.has(id)),
      );
      for (const householdId of scope.seedHouseholdIds || []) {
        const houseNodeId = `house:${householdId}`;
        if (!nodesById.has(houseNodeId)) continue;
        markScope(houseNodeId, "core");
        for (const edge of adjacency.get(houseNodeId) || []) {
          const memberId = otherEnd(edge, houseNodeId);
          if (nodesById.get(memberId)?.kind === "occurrence" && !excluded.has(memberId)) core.add(memberId);
        }
      }
      core = exactPeers(core, excluded);
      for (const nodeId of core) markScope(nodeId, "core");

      // Głębokość 0 zachowuje kontekst aktu/domostwa rdzenia, ale nie odsłania
      // pozostałych uczestników.
      for (const nodeId of core) {
        for (const edge of adjacency.get(nodeId) || []) {
          const neighborId = otherEnd(edge, nodeId);
          const neighbor = nodesById.get(neighborId);
          if (neighbor && (neighbor.kind === "event" || neighbor.kind === "household" || neighbor.kind === "identity")) {
            markScope(neighborId, "core");
          }
        }
      }

      let frontier = new Set(core);
      const limit = depthLimit(options.depth ?? scope.defaultDepth ?? this.payload.defaultDepth);
      for (let round = 1; frontier.size && round <= limit; round += 1) {
        let next = new Set();
        const contexts = new Set();
        for (const nodeId of frontier) {
          for (const edge of adjacency.get(nodeId) || []) {
            const neighborId = otherEnd(edge, nodeId);
            const neighbor = nodesById.get(neighborId);
            if (!neighbor) continue;
            if (neighbor.kind === "event" || neighbor.kind === "household" || neighbor.kind === "identity") {
              contexts.add(neighborId);
            } else if (neighbor.kind === "occurrence" && !localVisible.has(neighborId) && !excluded.has(neighborId)) {
              next.add(neighborId);
            }
          }
        }
        for (const contextId of contexts) {
          markScope(contextId, round === 1 ? "fan-direct" : "fan-extended");
          for (const edge of adjacency.get(contextId) || []) {
            const neighborId = otherEnd(edge, contextId);
            if (nodesById.get(neighborId)?.kind === "occurrence" && !localVisible.has(neighborId) && !excluded.has(neighborId)) {
              next.add(neighborId);
            }
          }
        }
        next = exactPeers(next, excluded);
        for (const nodeId of next) markScope(nodeId, round === 1 ? "fan-direct" : "fan-extended");
        frontier = next;
      }
    }

    // W trybie dowodowym źródło jest liściem widocznego zdarzenia. Nie
    // uczestniczy w liczeniu kręgów FAN, dlatego dołączamy je dopiero po BFS.
    for (const edge of documentEdges.filter((item) => item.kind === "source")) {
      const eventId = nodesById.get(edge.from)?.kind === "event" ? edge.from : edge.to;
      const sourceId = eventId === edge.from ? edge.to : edge.from;
      if (!visible.has(eventId) || nodesById.get(sourceId)?.kind !== "source") continue;
      const role = roleByNodeId.get(eventId) || "fan-direct";
      const memberships = scopeIdsByNodeId.get(eventId) || new Set(activeScopeIds);
      for (const scopeId of memberships) mark(sourceId, role, scopeId);
    }

    // Jawnie rozwinięte wystąpienie jest szczegółem widocznego aktu, a nie
    // kolejnym kręgiem FAN. Dołączamy je jak liść, również przy głębokości 0.
    for (const edge of documentEdges.filter((item) => item.contextualEvidenceDetail)) {
      const eventId = nodesById.get(edge.from)?.kind === "event" ? edge.from
        : nodesById.get(edge.to)?.kind === "event" ? edge.to : null;
      if (!eventId || !visible.has(eventId)) continue;
      const detailId = eventId === edge.from ? edge.to : edge.from;
      if (nodesById.get(detailId)?.kind !== "occurrence") continue;
      const role = roleByNodeId.get(eventId) || "fan-direct";
      const memberships = scopeIdsByNodeId.get(eventId) || new Set(activeScopeIds);
      for (const scopeId of memberships) mark(detailId, role, scopeId);
    }

    const hasExpandableScope = selectedScopes.some((scope) => scope.scopeKind !== "source_reported_network");
    const outputEdges = [
      ...(hasExpandableScope ? documentEdges.filter((edge) => visible.has(edge.from) && visible.has(edge.to)) : []),
      ...(hasExpandableScope ? externalEdges.filter((edge) => visible.has(edge.from) && visible.has(edge.to)) : []),
      ...exactExternalEdges,
    ];

    if (includeCandidates && !fixedExternalScope) {
      for (const edge of candidateEdges) {
        const touchesVisible = visible.has(edge.from) || visible.has(edge.to);
        if (!touchesVisible) continue;
        const memberships = new Set([
          ...(scopeIdsByNodeId.get(edge.from) || []),
          ...(scopeIdsByNodeId.get(edge.to) || []),
        ]);
        for (const nodeId of [edge.from, edge.to]) {
          if (!nodesById.has(nodeId)) continue;
          visible.add(nodeId);
          roleByNodeId.set(nodeId, preferredRole(roleByNodeId.get(nodeId) || "unassigned", "candidate"));
          const nodeMemberships = scopeIdsByNodeId.get(nodeId) || new Set();
          for (const scopeId of memberships) nodeMemberships.add(scopeId);
          scopeIdsByNodeId.set(nodeId, nodeMemberships);
        }
        if (visible.has(edge.from) && visible.has(edge.to)) outputEdges.push(edge);
      }
    }

    const bridgeNodes = new Map();
    if (includeBridges && !fixedExternalScope) {
      for (const scope of selectedScopes) {
        for (const item of scope.bridgeNodes || []) {
          bridgeNodes.set(item.id, {
            id: item.id,
            ref: item.treePersonId,
            kind: "bridge",
            label: item.label,
            item,
          });
          mark(item.id, "bridge", scope.id);
        }
        for (const edge of scope.bridgeEdges || []) {
          if ((visible.has(edge.from) || bridgeNodes.has(edge.from)) && (visible.has(edge.to) || bridgeNodes.has(edge.to))) {
            outputEdges.push({ ...edge, kind: "bridge" });
          }
        }
      }
    }

    const outputNodes = [...nodesById.values(), ...bridgeNodes.values()]
      .filter((item) => visible.has(item.id))
      .map((item) => ({
        ...item,
        visibilityRole: roleByNodeId.get(item.id) || "unassigned",
        scopeIds: [...(scopeIdsByNodeId.get(item.id) || [])].sort(),
      }));

    return {
      nodes: outputNodes,
      edges: outputEdges.filter((edge, index, list) =>
        visible.has(edge.from)
        && visible.has(edge.to)
        && list.findIndex((candidate) => candidate.id === edge.id) === index),
      roleByNodeId,
      scopeIdsByNodeId,
      activeScopeIds,
      depth: fixedExternalScope ? 0 : options.depth ?? this.payload.defaultDepth ?? 1,
      counts: this.#counts(outputNodes, nodesById.size - [...visible].filter((id) => nodesById.has(id)).length),
      sourceReportedOccurrenceCount: exactExternalOccurrenceIds.size,
      sourceReportedRelationshipCount: new Set(exactExternalEdges.map((edge) => edge.externalRelationshipId || edge.item?.id)).size,
      sourceReportedCandidateRole: fixedExternalScope ? selectedCandidateRole : null,
      sourceReportedCandidateRoleCounts: fixedExternalScope
        ? selectedScopes.reduce((counts, scope) => {
            for (const [role, count] of Object.entries(scope.candidateRoleCounts || {})) {
              counts[role] = (counts[role] || 0) + count;
            }
            return counts;
          }, {})
        : {},
      sourceReportedAnchorState: fixedExternalScope ? selectedScopes[0]?.anchorState || "missing" : null,
      sourceReportedAnchor: fixedExternalScope ? selectedScopes[0]?.anchor || null : null,
      warnings: [...new Set(warnings)],
    };
  }

  #allView(nodesById, edges, includeCandidates) {
    const familyCore = this.scopeById.get("immediate-family");
    // Widok ogólny pokazuje wspólny szkielet wszystkich opisanych ścieżek
    // rodzinnych. Ograniczenie go do immediate-family urywało obraz na
    // rodzicach i rysowało każdą linię bezpośrednio od bridge:self.
    const familyNodeById = new Map();
    const familyScopeIds = new Map();
    for (const scope of this.scopes()) {
      for (const item of scope.bridgeNodes || []) {
        const existing = familyNodeById.get(item.id);
        familyNodeById.set(item.id, existing ? { ...existing, ...item } : item);
        const memberships = familyScopeIds.get(item.id) || new Set();
        memberships.add(scope.id);
        familyScopeIds.set(item.id, memberships);
      }
    }
    const familyNodes = [...familyNodeById.values()].map((item) => ({
        id: item.id,
        ref: item.treePersonId,
        kind: "bridge",
        label: item.label,
        item,
        visibilityRole: "bridge",
        scopeIds: [...(familyScopeIds.get(item.id) || [])].sort(),
      }));
    const familyNodeIds = new Set(familyNodes.map((item) => item.id));
    const familyEdgeById = new Map();
    for (const scope of this.scopes()) {
      for (const edge of scope.bridgeEdges || []) {
        if (!familyNodeIds.has(edge.from) || !familyNodeIds.has(edge.to)) continue;
        familyEdgeById.set(edge.id, { ...edge, kind: "bridge" });
      }
    }
    const familyEdges = [...familyEdgeById.values()];
    const rawGraph = { nodes: [...nodesById.values()], edges };
    const scopeNodes = [];
    const scopeEdges = [];
    const rootBridgeId = familyNodeIds.has("bridge:self") ? "bridge:self" : familyNodes[0]?.id;
    const bridgeAdjacency = new Map(familyNodes.map((item) => [item.id, new Set()]));
    for (const edge of familyEdges) {
      bridgeAdjacency.get(edge.from)?.add(edge.to);
      bridgeAdjacency.get(edge.to)?.add(edge.from);
    }
    const bridgeDistance = new Map(rootBridgeId ? [[rootBridgeId, 0]] : []);
    const queue = rootBridgeId ? [rootBridgeId] : [];
    while (queue.length) {
      const current = queue.shift();
      for (const neighbor of [...(bridgeAdjacency.get(current) || [])].sort()) {
        if (bridgeDistance.has(neighbor)) continue;
        bridgeDistance.set(neighbor, bridgeDistance.get(current) + 1);
        queue.push(neighbor);
      }
    }
    const overviewScopes = this.scopes().filter((scope) => scope.id !== familyCore?.id);
    for (const scope of overviewScopes) {
      const scoped = this.apply(rawGraph, {
        scopeIds: [scope.id],
        depth: "all",
        includeBridges: false,
        includeCandidates,
      });
      const backingNodes = scoped.nodes.filter((item) => item.kind !== "bridge");
      const occurrenceCount = scope.scopeKind === "source_reported_network"
        ? scope.visibleProfileCount
        : backingNodes.filter((item) => item.kind === "occurrence").length;
      const eventCount = backingNodes.filter((item) => item.kind === "event").length;
      const householdCount = backingNodes.filter((item) => item.kind === "household").length;
      const candidateCount = scope.scopeKind === "source_reported_network"
        ? scope.visibleProfileCount
        : backingNodes.filter((item) => item.visibilityRole === "candidate").length;
      const externalRelationCount = scope.scopeKind === "source_reported_network"
        ? scope.visibleRelationshipCount
        : scoped.edges.filter((item) => item.kind === "external-relation").length;
      const actIds = new Set(backingNodes.filter((item) => item.kind === "event").map((item) => item.ref || item.item?.id));
      for (const edge of scoped.edges.filter((item) => item.kind === "event-link" || item.kind === "participation")) {
        if (edge.item?.id) actIds.add(edge.item.id);
      }
      const actCount = actIds.size;
      const contextCount = actCount + householdCount;
      const scopeAnchorId = scope.scopeKind === "source_reported_network" ? null : (scope.bridgeNodes || [])
        .map((item, index) => ({ id: item.id, index }))
        .filter((item) => familyNodeIds.has(item.id))
        .sort((left, right) => (bridgeDistance.get(right.id) ?? -1) - (bridgeDistance.get(left.id) ?? -1)
          || right.index - left.index
          || left.id.localeCompare(right.id))[0]?.id
        || rootBridgeId;
      const scopeAnchorLabel = scope.scopeKind === "source_reported_network"
        ? "odrębny zakres — bez połączenia z osobą centralną"
        : familyNodeById.get(scopeAnchorId)?.label || "osoba centralna";
      const detailParts = [
        `${occurrenceCount} wystąpień`,
        actCount ? `${actCount} aktów` : null,
        householdCount ? `${householdCount} gospodarstw` : null,
        candidateCount ? `${candidateCount} kandydatur` : null,
        externalRelationCount ? `${externalRelationCount} relacji zewnętrznych` : null,
      ].filter(Boolean);
      const summaryId = `scope:${scope.id}`;
      scopeNodes.push({
        id: summaryId,
        ref: scope.id,
        kind: "scope",
        label: scope.label,
        labelDetail: detailParts.join(" · ") || "linia bez aktów",
        visibilityRole: "scope",
        scopeIds: [scope.id],
        backingNodeIds: backingNodes.map((item) => item.id).sort(),
        backingEdgeIds: scoped.edges.map((item) => item.id).sort(),
        item: {
          id: scope.id,
          label: scope.label,
          summary: scope.description || scope.notes || "Zakres rodzinny — kliknij, aby rozwinąć.",
          occurrenceCount,
          eventCount,
          actCount,
          householdCount,
          contextCount,
          candidateCount,
          externalRelationCount,
          scopeKind: scope.scopeKind || "family_line",
          scopeAnchorId,
          scopeAnchorLabel,
          searchText: backingNodes.flatMap((item) => [
            item.label,
            item.item?.name,
            item.item?.normalizedName,
            item.item?.searchName,
            item.item?.place,
          ]).filter(Boolean).join(" "),
        },
      });
      if (scopeAnchorId && scope.scopeKind !== "source_reported_network") scopeEdges.push({
        id: `scope-link:${scope.id}`,
        from: scopeAnchorId,
        to: summaryId,
        kind: "scope-link",
        label: "kontynuacja linii rodzinnej",
        backingEdgeIds: scoped.edges.map((item) => item.id).sort(),
        backingNodeIds: backingNodes.map((item) => item.id).sort(),
      });
    }
    const nodes = [...familyNodes, ...scopeNodes];
    const selectedEdges = [...familyEdges, ...scopeEdges];
    return {
      nodes,
      edges: selectedEdges,
      roleByNodeId: new Map(nodes.map((item) => [item.id, item.visibilityRole])),
      scopeIdsByNodeId: new Map(nodes.map((item) => [item.id, new Set(item.scopeIds || [])])),
      activeScopeIds: [],
      depth: "all",
      counts: this.#counts(nodes, nodesById.size),
      overview: true,
      overviewScopeCount: scopeNodes.length,
      overviewBackingNodeCount: new Set(scopeNodes.flatMap((item) => item.backingNodeIds)).size,
      warnings: [],
    };
  }

  #counts(nodes, hidden) {
    const counts = {
      core: 0,
      fanDirect: 0,
      fanExtended: 0,
      bridge: 0,
      candidate: 0,
      visible: nodes.length,
      hidden: Math.max(0, hidden),
    };
    for (const item of nodes) {
      if (item.kind !== "occurrence" && item.kind !== "bridge") continue;
      if (item.visibilityRole === "core") counts.core += 1;
      else if (item.visibilityRole === "fan-direct") counts.fanDirect += 1;
      else if (item.visibilityRole === "fan-extended") counts.fanExtended += 1;
      else if (item.visibilityRole === "bridge") counts.bridge += 1;
      else if (item.visibilityRole === "candidate") counts.candidate += 1;
    }
    return counts;
  }
}
