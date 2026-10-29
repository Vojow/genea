import { roleLabel } from "../core/formatting.mjs";

function unique(values) {
  return [...new Set(values.filter(Boolean))].sort();
}

function endpointMap(edges) {
  const result = new Map();
  for (const edge of edges) {
    const from = result.get(edge.from) || [];
    from.push(edge);
    result.set(edge.from, from);
    const to = result.get(edge.to) || [];
    to.push(edge);
    result.set(edge.to, to);
  }
  return result;
}

function otherEnd(edge, nodeId) {
  return edge.from === nodeId ? edge.to : edge.from;
}

function withExistingEdges(graph, nodes) {
  const visible = new Set(nodes.map((item) => item.id));
  return {
    ...graph,
    nodes,
    edges: (graph.edges || []).filter((edge) => visible.has(edge.from) && visible.has(edge.to)),
  };
}

function roleGroupValue(role) {
  const value = roleLabel(role).toLocaleLowerCase("pl-PL");
  if (/świad|witness/.test(value)) return "witness";
  if (/chrzest|godparent/.test(value)) return "godparent";
  if (/zgłasz|informant/.test(value)) return "informant";
  if (/ojciec|matka|syn|cór|dziec|małż|panna młoda|pan młody|wdow|żona|mąż|ochrzcz/.test(value)) return "family";
  return "other";
}

function roleGroup(node) {
  return roleGroupValue(node.item?.role);
}

function peopleProjection(graph, options) {
  const occurrenceToIdentity = new Map();
  for (const node of graph.nodes || []) {
    if (node.kind !== "identity") continue;
    for (const occurrenceId of node.item?.occurrenceIds || []) {
      occurrenceToIdentity.set(`occ:${occurrenceId}`, node.id);
    }
  }
  const unresolvedOccurrenceIds = new Set(
    (graph.edges || [])
      .filter((edge) => edge.kind === "candidate")
      .flatMap((edge) => [edge.from, edge.to]),
  );
  const minimum = Math.max(1, Number(options.minimumOccurrences) || 1);
  const identityAllowed = new Set(
    (graph.nodes || [])
      .filter((node) => node.kind === "identity" && Number(node.item?.occurrenceCount || node.item?.occurrenceIds?.length || 0) >= minimum)
      .map((node) => node.id),
  );
  const status = options.identityStatus || "all";
  const nodes = (graph.nodes || []).filter((node) => {
    if (node.contextualEvidenceFor) return true;
    if (node.kind === "bridge") return false;
    if (node.kind === "identity") {
      if (!identityAllowed.has(node.id)) return false;
      if (status === "unresolved") {
        return (node.item?.occurrenceIds || []).some((id) => unresolvedOccurrenceIds.has(`occ:${id}`));
      }
      if (status === "unlinked") return false;
      return true;
    }
    if (node.kind !== "occurrence") return false;
    const identityId = occurrenceToIdentity.get(node.id);
    if (status === "resolved") return Boolean(identityId && identityAllowed.has(identityId));
    if (status === "unresolved") return unresolvedOccurrenceIds.has(node.id);
    if (status === "unlinked") return !identityId;
    return !identityId || identityAllowed.has(identityId);
  });
  const projected = withExistingEdges(graph, nodes);
  projected.edges = projected.edges.filter((edge) => ["same-person", "candidate"].includes(edge.kind) || edge.contextualEvidenceFor);
  const visibleNodeIds = new Set(projected.nodes.map((item) => item.id));
  projected.layoutEdges = (graph.edges || [])
    .filter((edge) => edge.kind === "event-link"
      && visibleNodeIds.has(edge.from)
      && visibleNodeIds.has(edge.to))
    .map((edge) => ({
      ...edge,
      id: `layout:${edge.id}`,
      kind: "co-occurrence-proximity",
      sourceKind: edge.kind,
      layoutOnly: true,
      backingNodeIds: [...new Set(edge.backingNodeIds || [edge.from, edge.to])].sort(),
      backingEdgeIds: [...new Set(edge.backingEdgeIds || [edge.id])].sort(),
    }));
  return projected;
}

function fanProjection(graph, options) {
  const documents = options.showDocuments === true;
  const households = options.showHouseholds === true;
  const selectedRole = options.fanRole || "all";
  const participationRoles = new Map();
  for (const edge of graph.edges || []) {
    if (edge.kind !== "participation") continue;
    const occurrenceId = edge.from.startsWith("occ:") ? edge.from : edge.to.startsWith("occ:") ? edge.to : null;
    if (!occurrenceId) continue;
    const groups = participationRoles.get(occurrenceId) || new Set();
    groups.add(roleGroupValue(edge.label));
    participationRoles.set(occurrenceId, groups);
  }
  let nodes = (graph.nodes || []).filter((node) => {
    if (node.kind === "event" || node.kind === "source") return documents;
    if (node.kind === "household") return households;
    if (node.kind !== "occurrence" || selectedRole === "all") return true;
    return ["core", "bridge"].includes(node.visibilityRole)
      || roleGroup(node) === selectedRole
      || participationRoles.get(node.id)?.has(selectedRole);
  });
  let visible = new Set(nodes.map((item) => item.id));
  let edges = (graph.edges || []).filter((edge) => {
    if (!visible.has(edge.from) || !visible.has(edge.to)) return false;
    if (edge.kind === "household") return households;
    if (documents) return edge.kind !== "event-link";
    return !["participation", "source"].includes(edge.kind);
  });
  // Usuń konteksty dokumentowe bez widocznego uczestnika i osierocone
  // tożsamości po zastosowaniu filtra roli.
  const connected = new Set(edges.flatMap((edge) => [edge.from, edge.to]));
  nodes = nodes.filter((node) => !["event", "source", "identity", "household"].includes(node.kind) || connected.has(node.id));
  visible = new Set(nodes.map((item) => item.id));
  edges = edges.filter((edge) => visible.has(edge.from) && visible.has(edge.to));
  return { ...graph, nodes, edges };
}

function clusterBackings(graph) {
  const nodes = new Map((graph.nodes || []).map((item) => [item.id, item]));
  const edges = graph.edges || [];
  const adjacency = endpointMap(edges.filter((edge) => edge.kind !== "candidate" && edge.kind !== "bridge"));
  const owner = new Map();
  const definitions = [];

  for (const household of [...nodes.values()].filter((item) => item.kind === "household").sort((a, b) => a.id.localeCompare(b.id))) {
    const backing = new Set([household.id]);
    for (const edge of adjacency.get(household.id) || []) {
      if (edge.kind === "household") backing.add(otherEnd(edge, household.id));
    }
    let identityClosureChanged = true;
    while (identityClosureChanged) {
      identityClosureChanged = false;
      for (const nodeId of [...backing]) {
        for (const edge of adjacency.get(nodeId) || []) {
          if (edge.kind !== "same-person") continue;
          const neighbor = otherEnd(edge, nodeId);
          if (backing.has(neighbor)) continue;
          backing.add(neighbor);
          identityClosureChanged = true;
        }
      }
    }
    const memberLabels = unique([...backing]
      .filter((nodeId) => nodes.get(nodeId)?.kind === "occurrence")
      .map((nodeId) => nodes.get(nodeId)?.label));
    const technicalLabel = !household.label
      || household.label === household.ref
      || /^[A-ZĄĆĘŁŃÓŚŹŻ0-9-]+$/.test(household.label);
    const label = technicalLabel && memberLabels.length
      ? `Gospodarstwo: ${memberLabels.slice(0, 2).join(" + ")}`
      : household.label || "Gospodarstwo";
    const id = `cluster:household:${household.ref || household.id}`;
    for (const nodeId of backing) if (!owner.has(nodeId)) owner.set(nodeId, id);
    definitions.push({ id, type: "household", label, sourceLabel: household.label || household.ref, backing });
  }

  const eligible = [...nodes.values()]
    .filter((node) => !["bridge", "source"].includes(node.kind) && !owner.has(node.id))
    .map((node) => node.id)
    .sort();
  const eligibleSet = new Set(eligible);
  const visited = new Set();
  for (const seed of eligible) {
    if (visited.has(seed)) continue;
    const component = new Set();
    const queue = [seed];
    visited.add(seed);
    while (queue.length) {
      const current = queue.shift();
      component.add(current);
      for (const edge of adjacency.get(current) || []) {
        const neighbor = otherEnd(edge, current);
        if (!eligibleSet.has(neighbor) || visited.has(neighbor)) continue;
        visited.add(neighbor);
        queue.push(neighbor);
      }
    }
    const id = `cluster:network:${[...component].sort()[0].replaceAll(":", "-")}`;
    for (const nodeId of component) owner.set(nodeId, id);
    definitions.push({ id, type: "network", label: "Sieć badawcza", backing: component });
  }
  return { definitions, owner, nodes };
}

function clusterProjection(graph, options) {
  const { definitions: rawDefinitions, owner, nodes: sourceNodes } = clusterBackings(graph);
  const personCountOf = (definition) => new Set([...definition.backing]
    .flatMap((id) => {
      const node = sourceNodes.get(id);
      if (node?.kind === "identity") return [node.item?.personId];
      if (node?.kind === "occurrence") return [node.item?.personId || id];
      return [];
    }).filter(Boolean)).size;
  const singletonNetworks = rawDefinitions.filter((definition) => definition.type === "network" && personCountOf(definition) <= 1);
  const definitions = rawDefinitions.filter((definition) => !singletonNetworks.includes(definition));
  if (singletonNetworks.length) {
    const backing = new Set(singletonNetworks.flatMap((definition) => [...definition.backing]));
    const unassigned = {
      id: "cluster:unassigned",
      type: "unassigned",
      label: "Poza klastrami",
      sourceLabel: null,
      backing,
    };
    for (const nodeId of backing) owner.set(nodeId, unassigned.id);
    definitions.push(unassigned);
  }
  const expandedIds = options.expandedClusterIds || new Set();
  const minimum = Math.max(1, Number(options.minimumClusterMembers) || 1);
  const type = options.clusterType || "all";
  const sourceEdges = graph.edges || [];
  const candidateEdges = sourceEdges.filter((edge) => edge.kind === "candidate");
  const clusterNodes = [];
  const visibleBacking = new Set();
  const allowedClusters = new Set();

  for (const definition of definitions) {
    const occurrenceIds = [...definition.backing].filter((id) => sourceNodes.get(id)?.kind === "occurrence");
    const identityIds = [...definition.backing].filter((id) => sourceNodes.get(id)?.kind === "identity");
    const eventIds = new Set([...definition.backing].filter((id) => sourceNodes.get(id)?.kind === "event"));
    for (const edge of sourceEdges) {
      if (edge.item?.id && edge.kind === "event-link" && (definition.backing.has(edge.from) || definition.backing.has(edge.to))) eventIds.add(`event:${edge.item.id}`);
    }
    const personCount = personCountOf(definition);
    if (type !== "all" && definition.type !== type) continue;
    if (personCount < minimum) continue;
    allowedClusters.add(definition.id);
    const backingEdgeIds = sourceEdges
      .filter((edge) => definition.backing.has(edge.from) && definition.backing.has(edge.to))
      .flatMap((edge) => edge.backingEdgeIds || [edge.id]);
    const candidateCount = candidateEdges.filter((edge) => definition.backing.has(edge.from) || definition.backing.has(edge.to)).length;
    const labels = occurrenceIds.map((id) => sourceNodes.get(id)?.label).filter(Boolean);
    const householdNode = [...definition.backing]
      .map((id) => sourceNodes.get(id))
      .find((node) => node?.kind === "household");
    const item = {
      id: definition.id,
      type: definition.type,
      label: definition.label,
      householdKind: householdNode?.item?.kind || null,
      residenceEvidenceStatus: householdNode?.item?.residenceEvidenceStatus || null,
      evidenceNeeded: householdNode?.item?.evidenceNeeded || null,
      memberCount: personCount,
      occurrenceCount: occurrenceIds.length,
      actCount: eventIds.size,
      candidateCount,
      memberLabels: unique(labels),
      eventIds: unique([...eventIds].map((id) => id.replace(/^event:/, ""))),
      sourceLabel: definition.sourceLabel || null,
    };
    const expanded = expandedIds.has(definition.id);
    if (expanded) {
      for (const nodeId of definition.backing) {
        if (!["event", "source"].includes(sourceNodes.get(nodeId)?.kind)) visibleBacking.add(nodeId);
      }
    }
    clusterNodes.push({
      id: definition.id,
      ref: definition.id,
      kind: "cluster",
      label: `${definition.label} · ${personCount}`,
      labelDetail: `${eventIds.size} aktów · ${candidateCount} tropów`,
      item,
      visibilityRole: [...definition.backing].map((id) => sourceNodes.get(id)?.visibilityRole).find((value) => value === "core") || "fan-direct",
      scopeIds: unique([...definition.backing].flatMap((id) => sourceNodes.get(id)?.scopeIds || [])),
      backingNodeIds: [...definition.backing].sort(),
      backingEdgeIds: unique(backingEdgeIds),
      clusterExpanded: expanded,
      searchEntries: [
        {
          kind: "cluster",
          label: definition.label,
          text: [definition.label, item.eventIds.join(" ")].join(" "),
        },
        ...occurrenceIds.map((id) => {
          const occurrence = sourceNodes.get(id);
          return {
            kind: "occurrence",
            occurrenceId: occurrence?.item?.id || id.replace(/^occ:/, ""),
            label: occurrence?.item?.name || occurrence?.label || id,
            detail: `ukryte wystąpienie w klastrze ${definition.label}`,
            text: [occurrence?.label, occurrence?.item?.name, occurrence?.item?.normalizedName, occurrence?.item?.searchName].filter(Boolean).join(" "),
          };
        }),
      ],
    });
  }

  // Łączniki zostały już użyte przez GraphScopeFacade do wyznaczenia zakresu.
  // W projekcji klastrowej nie rysujemy ponownie prywatnego mini-drzewa,
  // ponieważ zasłaniałoby ono agregaty i sugerowało, że klaster opisuje
  // pokrewieństwo. Pełny szkielet pozostaje w Drzewie rodzinnym.
  const bridgeNodes = [];
  const expandedNodes = [...visibleBacking].map((id) => ({
    ...sourceNodes.get(id),
    expandedClusterId: owner.get(id),
    clusterExpanded: true,
  }));
  const outputNodes = [...clusterNodes, ...expandedNodes, ...bridgeNodes];
  const visibleIds = new Set(outputNodes.map((item) => item.id));
  const remap = (nodeId) => {
    if (visibleIds.has(nodeId)) return nodeId;
    const clusterId = owner.get(nodeId);
    return allowedClusters.has(clusterId) ? clusterId : null;
  };
  const groups = new Map();
  for (const edge of sourceEdges) {
    if (["participation", "source"].includes(edge.kind)) continue;
    const from = remap(edge.from);
    const to = remap(edge.to);
    if (!from || !to || from === to || !visibleIds.has(from) || !visibleIds.has(to)) continue;
    const kind = edge.kind === "candidate" ? "candidate" : edge.kind === "bridge" ? "bridge" : "cluster-link";
    const [a, b] = [from, to].sort();
    const key = `${kind}:${a}:${b}`;
    const current = groups.get(key) || {
      id: `aggregate:${key}`, from: a, to: b, kind,
      label: kind === "candidate" ? "kandydatura pomiędzy klastrami" : kind === "bridge" ? edge.label : "wspólny kontekst",
      backingNodeIds: [], backingEdgeIds: [], aggregateCount: 0,
    };
    current.backingNodeIds.push(...(edge.backingNodeIds || [edge.from, edge.to]));
    current.backingEdgeIds.push(...(edge.backingEdgeIds || [edge.id]));
    current.aggregateCount += 1;
    groups.set(key, current);
  }
  const outputEdges = [...groups.values()].map((edge) => ({
    ...edge,
    backingNodeIds: unique(edge.backingNodeIds),
    backingEdgeIds: unique(edge.backingEdgeIds),
  }));
  for (const nodeId of visibleBacking) {
    const clusterId = owner.get(nodeId);
    if (!allowedClusters.has(clusterId) || !visibleIds.has(nodeId)) continue;
    outputEdges.push({
      id: `cluster-member:${clusterId}:${nodeId}`,
      from: clusterId,
      to: nodeId,
      kind: "cluster-member",
      label: "element klastra",
      backingNodeIds: [nodeId],
      backingEdgeIds: [],
    });
  }
  return { ...graph, nodes: outputNodes, edges: outputEdges };
}

export class GraphModeProjector {
  project(scopedGraph, mode, options = {}) {
    if (mode === "clusters") return clusterProjection(scopedGraph, options);
    if (mode === "fan") return fanProjection(scopedGraph, options);
    return peopleProjection(scopedGraph, options);
  }
}
