import { polishCount, roleLabel } from "../core/formatting.mjs";

function folded(value) {
  return String(value || "")
    .toLocaleLowerCase("pl-PL")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replaceAll("ł", "l");
}

function yearOf(event) {
  const value = event?.dateFrom || event?.date || event?.originalDates?.[0] || "";
  return String(value).match(/\b(1[5-9]\d{2}|20\d{2})\b/)?.[1] || "rok nieustalony";
}

function actOf(source) {
  const value = [source?.locator, source?.title].filter(Boolean).join(" · ");
  const match = value.match(/\bakt(?:u|em|owi)?\s*(?:nr\.?\s*)?(\d+)/i);
  return match ? `akt ${match[1]}` : "akt bez numeru";
}

function aliasValues(person) {
  return (person?.names || []).flatMap((item) => {
    if (typeof item === "string") return [item];
    return [item?.name, item?.value, item?.rawName, item?.normalizedName].filter(Boolean);
  });
}

function searchRank(node, entry, query) {
  const label = folded(entry?.label);
  const exact = label === query || label.split(/[^\p{L}\p{N}]+/u).includes(query);
  if (node.kind === "event" && node.contextualEvidenceFor) return exact ? 0 : 1;
  if (node.kind === "source" && exact) return 0;
  if (node.kind === "identity" && entry?.kind === "occurrence") return exact ? 1 : 2;
  if (node.kind === "occurrence") return exact ? 3 : 4;
  if (node.kind === "identity") return exact ? 5 : 6;
  return exact ? 7 : 8;
}

export function occurrenceViewMetadata(occurrence, dataIndex) {
  const events = [...(dataIndex.eventsByOccurrence.get(occurrence?.id) || [])]
    .sort((left, right) => String(left.dateFrom || left.date || left.id).localeCompare(String(right.dateFrom || right.date || right.id)));
  const event = events[0] || null;
  const source = dataIndex.sources.get(occurrence?.sourceId || event?.sourceId);
  const role = roleLabel(occurrence?.role) || "rola nieustalona";
  const year = yearOf(event);
  const act = actOf(source);
  return {
    event,
    source,
    role,
    year,
    act,
    label: `${role} · ${year} · ${act}`,
  };
}

function edgeBackings(edge, nodeById) {
  return {
    edges: [...new Set(edge.backingEdgeIds || [edge.id])],
    nodes: [...new Set(edge.backingNodeIds?.length ? edge.backingNodeIds : [
      ...(nodeById.get(edge.from)?.backingNodeIds || [edge.from]),
      ...(nodeById.get(edge.to)?.backingNodeIds || [edge.to]),
    ])],
  };
}

function aggregateEdges(edges, nodeById) {
  const groups = new Map();
  for (const edge of edges) {
    if (edge.from === edge.to) continue;
    const [from, to] = [edge.from, edge.to].sort();
    // Każda relacja zewnętrzna jest osobnym literalnym assertion. Nie wolno
    // redukować kilku takich relacji do jednej kreski i jednego backing ID.
    const key = edge.kind === "external-relation"
      ? `${edge.kind}|${edge.id}`
      : `${edge.kind}|${from}|${to}`;
    const backings = edgeBackings(edge, nodeById);
    const current = groups.get(key) || {
      ...edge,
      id: edge.kind === "external-relation" ? edge.id : `aggregate:${edge.kind}:${from}:${to}`,
      from,
      to,
      labels: [],
      backingEdgeIds: [],
      backingNodeIds: [],
      aggregateCount: 0,
    };
    current.labels.push(edge.label);
    current.backingEdgeIds.push(...backings.edges);
    current.backingNodeIds.push(...backings.nodes);
    current.aggregateCount += backings.edges.length;
    groups.set(key, current);
  }
  const aggregated = [...groups.values()]
    .map((edge) => ({
      ...edge,
      labels: [...new Set(edge.labels.filter(Boolean))].sort(),
      label: [...new Set(edge.labels.filter(Boolean))].sort().join(" · ") || edge.label,
      backingEdgeIds: [...new Set(edge.backingEdgeIds)].sort(),
      backingNodeIds: [...new Set(edge.backingNodeIds)].sort(),
    }))
    .sort((left, right) => left.id.localeCompare(right.id));
  const routeGroups = new Map();
  for (const edge of aggregated) {
    const key = [edge.from, edge.to].sort().join("|");
    const group = routeGroups.get(key) || [];
    group.push(edge);
    routeGroups.set(key, group);
  }
  for (const group of routeGroups.values()) {
    group.sort((left, right) => left.kind.localeCompare(right.kind) || left.id.localeCompare(right.id));
    group.forEach((edge, index) => {
      edge.parallelCount = group.length;
      edge.parallelIndex = index - (group.length - 1) / 2;
    });
  }
  return aggregated;
}

export class GraphViewReducer {
  constructor({ payload, dataIndex }) {
    this.payload = payload;
    this.dataIndex = dataIndex;
    this.focusedNodeId = null;
    this.focusedNodeIds = new Set();
    this.expandedIdentityIds = new Set();
    this.evidenceMode = "people";
    this.query = "";
  }

  focus(nodeId) {
    this.focusedNodeId = nodeId || null;
    this.focusedNodeIds = new Set(nodeId ? [nodeId] : []);
  }

  focusMany(nodeIds) {
    const ordered = [...new Set((nodeIds || []).filter(Boolean))].sort();
    this.focusedNodeId = ordered[0] || null;
    this.focusedNodeIds = new Set(ordered);
  }

  setQuery(value) {
    this.query = String(value || "").trim();
  }

  setEvidenceMode(mode) {
    this.evidenceMode = mode || "people";
  }

  toggleIdentity(personId) {
    if (this.expandedIdentityIds.has(personId)) this.expandedIdentityIds.delete(personId);
    else this.expandedIdentityIds.add(personId);
  }

  isExpanded(personId) {
    return this.expandedIdentityIds.has(personId);
  }

  reduce(scopedGraph) {
    const sourceNodes = new Map((scopedGraph.nodes || []).map((item) => [item.id, item]));
    const occurrenceToIdentity = new Map();
    const hiddenOccurrences = new Set();
    const reducedNodes = [];

    for (const node of sourceNodes.values()) {
      if (node.kind !== "identity") continue;
      const inScopeOccurrences = (node.item?.occurrenceIds || [])
        .map((id) => `occ:${id}`)
        .filter((id) => sourceNodes.has(id))
        .sort();
      const expanded = this.evidenceMode === "evidence" || this.isExpanded(node.item.personId);
      for (const occurrenceId of inScopeOccurrences) {
        occurrenceToIdentity.set(occurrenceId, node.id);
        if (!expanded) hiddenOccurrences.add(occurrenceId);
      }
    }

    for (const node of sourceNodes.values()) {
      if (hiddenOccurrences.has(node.id)) continue;
      if (node.kind === "identity") {
        const inScopeOccurrences = (node.item?.occurrenceIds || [])
          .map((id) => `occ:${id}`)
          .filter((id) => sourceNodes.has(id))
          .sort();
        const person = this.dataIndex.people.get(node.item.personId);
        const searchEntries = [
          {
            kind: "preferred",
            label: node.item.label,
            text: [node.item.label, ...aliasValues(person)].filter(Boolean).join(" "),
          },
          ...inScopeOccurrences.map((nodeId) => {
            const occurrence = sourceNodes.get(nodeId)?.item;
            const meta = occurrenceViewMetadata(occurrence, this.dataIndex);
            return {
              kind: "occurrence",
              occurrenceId: occurrence.id,
              label: occurrence.name || occurrence.normalizedName || occurrence.id,
              detail: meta.label,
              text: [occurrence.name, occurrence.normalizedName, occurrence.searchName, meta.label, meta.source?.locator, meta.source?.title, meta.event?.place].filter(Boolean).join(" "),
            };
          }),
        ];
        reducedNodes.push({
          ...node,
          label: node.item.label,
          labelDetail: polishCount(node.item.occurrenceCount, "akt", "akty", "aktów"),
          backingNodeIds: [node.id, ...inScopeOccurrences],
          expandedIdentity: this.evidenceMode === "evidence" || this.isExpanded(node.item.personId),
          identityExpansionLocked: this.evidenceMode === "evidence",
          viewMode: this.evidenceMode,
          searchEntries,
        });
        continue;
      }
      if (node.kind === "occurrence") {
        const meta = occurrenceViewMetadata(node.item, this.dataIndex);
        reducedNodes.push({
          ...node,
          label: this.dataIndex.occurrenceLabel(node.item),
          labelDetail: meta.label,
          backingNodeIds: [node.id],
          occurrenceMeta: meta,
          viewMode: this.evidenceMode,
          searchEntries: [{
            kind: "occurrence",
            occurrenceId: node.item.id,
            label: node.item.name || node.item.normalizedName || node.item.id,
            detail: meta.label,
            text: [node.item.name, node.item.normalizedName, node.item.searchName, meta.label, meta.source?.locator, meta.source?.title, meta.event?.place].filter(Boolean).join(" "),
          }],
        });
        continue;
      }
      reducedNodes.push({
        ...node,
        backingNodeIds: node.backingNodeIds || [node.id],
        viewMode: this.evidenceMode,
        searchEntries: node.searchEntries || [{
          kind: node.kind,
          label: node.label,
          text: [
            node.label,
            node.labelDetail,
            node.item?.date,
            node.item?.dateFrom,
            ...(node.item?.originalDates || []),
            node.item?.summary,
            node.item?.searchText,
            node.item?.place,
            node.item?.notes,
          ].filter(Boolean).join(" "),
        }],
      });
    }

    const reducedNodeById = new Map(reducedNodes.map((item) => [item.id, item]));
    const redirectedEdges = [];
    for (const edge of scopedGraph.edges || []) {
      if (edge.kind === "same-person") {
        const identityNode = sourceNodes.get(edge.from)?.kind === "identity" ? sourceNodes.get(edge.from) : sourceNodes.get(edge.to);
        if (identityNode && this.evidenceMode !== "evidence" && !this.isExpanded(identityNode.item.personId)) continue;
      }
      const from = hiddenOccurrences.has(edge.from) ? occurrenceToIdentity.get(edge.from) : edge.from;
      const to = hiddenOccurrences.has(edge.to) ? occurrenceToIdentity.get(edge.to) : edge.to;
      if (!from || !to || !reducedNodeById.has(from) || !reducedNodeById.has(to)) continue;
      redirectedEdges.push({
        ...edge,
        from,
        to,
        backingNodeIds: [...new Set([...(edge.backingNodeIds || []), edge.from, edge.to])],
      });
    }
    const reducedEdges = aggregateEdges(redirectedEdges, reducedNodeById);
    const redirectedLayoutEdges = [];
    for (const edge of scopedGraph.layoutEdges || []) {
      const from = hiddenOccurrences.has(edge.from) ? occurrenceToIdentity.get(edge.from) : edge.from;
      const to = hiddenOccurrences.has(edge.to) ? occurrenceToIdentity.get(edge.to) : edge.to;
      if (!from || !to || !reducedNodeById.has(from) || !reducedNodeById.has(to)) continue;
      redirectedLayoutEdges.push({
        ...edge,
        from,
        to,
        layoutOnly: true,
        backingNodeIds: [...new Set([...(edge.backingNodeIds || []), edge.from, edge.to])],
      });
    }
    const reducedLayoutEdges = aggregateEdges(redirectedLayoutEdges, reducedNodeById)
      .map((edge) => ({ ...edge, layoutOnly: true }));
    const adjacency = new Map();
    for (const edge of reducedEdges) {
      const from = adjacency.get(edge.from) || new Set();
      from.add(edge.to);
      adjacency.set(edge.from, from);
      const to = adjacency.get(edge.to) || new Set();
      to.add(edge.from);
      adjacency.set(edge.to, to);
    }

    const redirectedFocus = [...this.focusedNodeIds]
      .map((nodeId) => reducedNodeById.has(nodeId) ? nodeId : occurrenceToIdentity.get(nodeId))
      .filter((nodeId) => nodeId && reducedNodeById.has(nodeId));
    this.focusedNodeIds = new Set(redirectedFocus);
    this.focusedNodeId = redirectedFocus[0] || null;
    const firstCircle = new Set();
    for (const nodeId of this.focusedNodeIds) {
      for (const neighborId of adjacency.get(nodeId) || []) {
        if (!this.focusedNodeIds.has(neighborId)) firstCircle.add(neighborId);
      }
    }
    const focusSet = new Set([...this.focusedNodeIds, ...firstCircle]);
    const query = folded(this.query);
    const nodes = reducedNodes.map((node) => {
      const searchMatches = query
        ? node.searchEntries
          .filter((entry) => folded(entry.text).includes(query))
          .map((entry) => ({ entry, rank: searchRank(node, entry, query) }))
          .sort((left, right) => left.rank - right.rank || String(left.entry.label).localeCompare(String(right.entry.label), "pl"))
        : [];
      const searchMatch = searchMatches[0]?.entry || null;
      const focused = this.focusedNodeIds.has(node.id);
      const neighbor = firstCircle.has(node.id);
      const importantCandidate = node.visibilityRole === "candidate";
      return {
        ...node,
        focusRole: !this.focusedNodeId ? "overview" : focused ? "focused" : neighbor ? "first-circle" : "dimmed",
        labelVisible: Boolean(!this.focusedNodeId || focused || neighbor || searchMatch || importantCandidate),
        searchMatch,
        searchRank: searchMatches[0]?.rank ?? null,
      };
    });
    const edges = reducedEdges.map((edge) => ({
      ...edge,
      focusRole: !this.focusedNodeId || (focusSet.has(edge.from) && focusSet.has(edge.to)) ? "active" : "dimmed",
    }));

    return {
      ...scopedGraph,
      nodes,
      edges,
      layoutEdges: reducedLayoutEdges,
      focusedNodeId: this.focusedNodeId,
      focusedNodeIds: [...this.focusedNodeIds].sort(),
      firstCircleNodeIds: [...firstCircle].sort(),
      collapsedIdentityCount: nodes.filter((item) => item.kind === "identity" && !item.expandedIdentity).length,
      expandedIdentityIds: [...this.expandedIdentityIds].sort(),
      sourceNodeCount: scopedGraph.nodes?.length || 0,
      sourceEdgeCount: scopedGraph.edges?.length || 0,
    };
  }
}
