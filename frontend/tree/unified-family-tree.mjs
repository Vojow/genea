import { projectConditionalAncestry } from "./conditional-ancestry-overlay.mjs";
import { projectConditionalExternalOverlay } from "./conditional-external-overlay.mjs";

const DEFAULT_MAX_DEPTH = 2;
const MAX_NODES_PER_START = 256;
const MAX_EDGES_PER_START = 512;
const PRIVATE_STATUSES = new Set(["masked", "masked_living", "private", "restricted"]);

function list(value) {
  return Array.isArray(value) ? value : [];
}

function stringSet(value) {
  return value instanceof Set
    ? new Set([...value].filter((item) => typeof item === "string"))
    : new Set(list(value).filter((item) => typeof item === "string"));
}

function depthLimit(value) {
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? Math.floor(number) : DEFAULT_MAX_DEPTH;
}

function namespaceFor(node, root) {
  if (node?.scope === "source_reported_network") {
    return `source_reported_network:${node.networkId || root?.networkId || "unknown"}`;
  }
  return "family_tree";
}

function nodeKey(node, root) {
  return `${namespaceFor(node, root)}\u0000${node.id}`;
}

function candidateDisplayId(node, root) {
  return `conditional-ancestor:${encodeURIComponent(namespaceFor(node, root))}:${encodeURIComponent(node.id)}`;
}

function pairKey(childId, parentId) {
  return `${childId}\u0000${parentId}`;
}

function externalProfileKey(networkId, publicProfileId) {
  return `${networkId}\u0000${publicProfileId}`;
}

function compareText(first, second) {
  return String(first).localeCompare(String(second), "pl");
}

function isPrivate(item) {
  const privacy = typeof item?.privacy === "string" ? item.privacy : item?.privacy?.status;
  return item?.sourceRedacted === true
    || item?.privacy?.redacted === true
    || PRIVATE_STATUSES.has(String(privacy || "").toLocaleLowerCase("pl"))
    || PRIVATE_STATUSES.has(String(item?.evidenceStatus || "").toLocaleLowerCase("pl"));
}

function gapSignalsTruncation(gap) {
  const reason = gap?.condition?.kind || gap?.kind;
  return new Set([
    "increase_max_depth",
    "increase_max_nodes",
    "increase_max_edges",
    "max_depth",
    "max_nodes",
    "max_edges",
    "presentation_depth_limit",
  ]).has(reason);
}

function boundedLimit(value, fallback) {
  return Number.isFinite(value) && value >= 0 ? Math.min(value, fallback) : fallback;
}

function generationOf(person, fallback = 0) {
  const generation = Number(person?.generation);
  return Number.isFinite(generation) ? generation : fallback;
}

function provenanceGroup(edge, node) {
  const networkId = edge?.provenance?.sourceNetworkId || node?.networkId || null;
  if (networkId) return { key: `external:${networkId}`, sourceNetworkId: networkId };
  const provenance = edge?.provenance || {};
  const sourceIds = [...new Set(list(edge?.sourceIds).filter((id) => typeof id === "string"))].sort(compareText);
  const backingIds = [...new Set([
    ...list(edge?.backingIds),
    ...list(edge?.backingRelationshipIds),
    ...list(edge?.backingEventIds),
  ].filter((id) => typeof id === "string"))].sort(compareText);
  return {
    key: `provenance:${provenance.kind || "unspecified"}:${sourceIds.join(",")}:${backingIds.join(",")}`,
    sourceNetworkId: null,
  };
}

function publicIdentityDescriptor(network, rawNode, attachment) {
  return {
    kind: attachment.kind,
    networkId: network.id,
    treeNodeId: rawNode.treeNodeId,
    publicProfileId: rawNode.publicProfileId,
    label: rawNode.label || "Profil zewnętrzny",
    identityStatus: attachment.identityStatus,
    identityDecisionTier: attachment.identityDecisionTier,
  };
}

function addMapList(index, key, value) {
  const values = index.get(key) || [];
  values.push(value);
  index.set(key, values);
}

function buildExternalIdentityIndex(tree, model, allTreePersonIds) {
  const attachments = list(tree?.conditionalTreeAttachments);
  const rawProfilesByPair = new Map();
  const safeTargetsByPair = new Map();
  const aliasCandidates = new Map();
  const descriptorsByPerson = new Map();

  for (const network of list(tree?.sourceReportedNetworks)) {
    if (typeof network?.id !== "string") continue;
    for (const rawNode of list(network.nodes)) {
      if (typeof rawNode?.treeNodeId !== "string"
        || typeof rawNode?.publicProfileId !== "string"
        || isPrivate(rawNode)) continue;
      addMapList(rawProfilesByPair, externalProfileKey(network.id, rawNode.publicProfileId), rawNode);
    }

    const overlay = projectConditionalExternalOverlay({
      model,
      attachments,
      networkId: network.id,
      candidateRole: "all",
      visibleTreePersonIds: allTreePersonIds,
    });
    for (const attachment of overlay.attachments) {
      const matches = list(network.nodes).filter((node) => (
        node?.treeNodeId === attachment.fromTreeNodeId
          && typeof node?.publicProfileId === "string"
          && !isPrivate(node)
      ));
      if (matches.length !== 1) continue;
      const rawNode = matches[0];
      const profileKey = externalProfileKey(network.id, rawNode.publicProfileId);
      addMapList(safeTargetsByPair, profileKey, attachment.toTreePersonId);
      addMapList(aliasCandidates, rawNode.treeNodeId, {
        networkId: network.id,
        targetId: attachment.toTreePersonId,
        profileKey,
      });
      addMapList(descriptorsByPerson, attachment.toTreePersonId, {
        profileKey,
        descriptor: publicIdentityDescriptor(network, rawNode, attachment),
      });
    }
  }

  const exactRawProfileByPair = new Map([...rawProfilesByPair]
    .filter(([, profiles]) => profiles.length === 1)
    .map(([key, profiles]) => [key, profiles[0]]));
  const exactTargetByPair = new Map([...safeTargetsByPair].flatMap(([key, targets]) => {
    const uniqueTargets = [...new Set(targets)];
    return exactRawProfileByPair.has(key) && uniqueTargets.length === 1 ? [[key, uniqueTargets[0]]] : [];
  }));
  const aliases = new Map([...aliasCandidates].flatMap(([treeNodeId, values]) => {
    const namespaces = new Set(values.map((item) => item.networkId));
    const targets = new Set(values.map((item) => item.targetId));
    const exactProfiles = values.every((item) => exactRawProfileByPair.has(item.profileKey));
    return namespaces.size === 1 && targets.size === 1 && exactProfiles ? [[treeNodeId, values[0].targetId]] : [];
  }));
  const identitySourcesByPerson = new Map([...descriptorsByPerson].flatMap(([personId, records]) => {
    const descriptors = records
      .filter((record) => exactRawProfileByPair.has(record.profileKey))
      .map((record) => record.descriptor);
    const exactDescriptors = descriptors
      .filter((descriptor, index) => descriptors.findIndex((item) => (
        item.networkId === descriptor.networkId && item.treeNodeId === descriptor.treeNodeId
      )) === index)
      .sort((first, second) => compareText(first.networkId, second.networkId)
        || compareText(first.treeNodeId, second.treeNodeId));
    return exactDescriptors.length ? [[personId, exactDescriptors]] : [];
  }));

  return { exactRawProfileByPair, exactTargetByPair, aliases, identitySourcesByPerson };
}

/**
 * Projects accepted base cards and qualified conditional parentage into one
 * presentation-only tree. It does not create people, unions, relationships,
 * or identity decisions and never reads source-network edges directly.
 */
export function projectUnifiedFamilyTree({
  tree,
  model,
  visiblePeople,
  maxDepth = DEFAULT_MAX_DEPTH,
  expandedNodeIds = new Set(),
  collapsedNodeIds = new Set(),
} = {}) {
  const basePeople = list(tree?.people).filter((person) => typeof person?.id === "string");
  const baseById = new Map(basePeople.map((person) => [person.id, person]));
  const baseCounts = new Map();
  for (const person of basePeople) baseCounts.set(person.id, (baseCounts.get(person.id) || 0) + 1);
  const visibleBaseIds = new Set(list(visiblePeople)
    .map((person) => person?.id)
    .filter((id) => typeof id === "string" && baseCounts.get(id) === 1));
  const expanded = stringSet(expandedNodeIds);
  const collapsed = stringSet(collapsedNodeIds);
  const requestedDepth = depthLimit(maxDepth);
  const allTreePersonIds = [...baseCounts].filter(([, count]) => count === 1).map(([id]) => id);
  const external = buildExternalIdentityIndex(tree, model, allTreePersonIds);

  const outputBaseIds = new Set(visibleBaseIds);
  const candidateRecords = new Map();
  const selectedEdges = new Map();
  const selectedGaps = new Map();
  const expandableIds = new Set();
  const collapsedIds = new Set();
  let truncated = false;
  let cycleGapAdded = false;
  let collapsedGapAdded = false;
  let limitGapAdded = false;
  let hiddenEndpointGapAdded = false;

  const conditional = tree?.conditionalAncestry;
  const mappingForNode = (node, root) => {
    const explicitGroupHypothesis = node?.scope === "family_tree"
      && node?.parentageHypothesisAnchor === true
      && node?.hypothesisEndpointScope === "presentation_hypothesis_group"
      && list(conditional?.edges).some((edge) => edge.fromNodeId === node.id
        && edge.relationKind === "parentage_hypothesis"
        && edge.childEndpointScope === "presentation_hypothesis_group"
        && edge.status === "unresolved"
        && edge.canonicalRelationship === false
        && edge.provenance?.kind === "structured_hypothesis"
        && list(edge.backingHypothesisIds).length > 0
        && list(edge.backingEventIds).length > 0
        && list(edge.sourceIds).length > 0);
    // An explicit hypothesis is about this exact displayed group, not an
    // identity decision joining its constituent source occurrences.
    if ((node?.identityScope === "canonical" || explicitGroupHypothesis)
      && typeof node?.treePersonId === "string"
      && baseCounts.get(node.treePersonId) === 1) {
      return { displayId: node.treePersonId, baseId: node.treePersonId, exactRawProfile: null };
    }
    if (node?.scope !== "source_reported_network"
    ) {
      return { displayId: candidateDisplayId(node, root), baseId: null, exactRawProfile: null };
    }
    if (typeof node?.networkId !== "string" || typeof node?.publicProfileId !== "string") {
      return { displayId: null, baseId: null, exactRawProfile: null, rejected: true };
    }
    const profileKey = externalProfileKey(node.networkId, node.publicProfileId);
    const exactRawProfile = external.exactRawProfileByPair.get(profileKey) || null;
    if (!exactRawProfile) {
      return { displayId: null, baseId: null, exactRawProfile: null, rejected: true };
    }
    const baseId = exactRawProfile ? external.exactTargetByPair.get(profileKey) || null : null;
    return {
      displayId: baseId || candidateDisplayId(node, root),
      baseId,
      exactRawProfile,
    };
  };
  const rawConditionalNodes = new Map(list(conditional?.nodes)
    .filter((node) => typeof node?.id === "string")
    .map((node) => [node.id, node]));
  const roots = list(conditional?.roots).filter((root) => {
    if (requestedDepth === 0
      || typeof root?.id !== "string"
      || typeof root?.nodeId !== "string"
      || typeof root?.label !== "string") return false;
    const rootNode = rawConditionalNodes.get(root.nodeId);
    if (!rootNode || isPrivate(rootNode)) return false;
    if (root.scope === "source_reported_network") {
      if (rootNode.scope !== "source_reported_network"
        || typeof root.networkId !== "string"
        || rootNode.networkId !== root.networkId) return false;
    } else if (rootNode.scope === "source_reported_network") return false;
    const mapping = mappingForNode(rootNode, root);
    return mapping.baseId && visibleBaseIds.has(mapping.baseId);
  });
  const projections = roots.map((root) => ({
    root,
    projection: projectConditionalAncestry({
      conditionalAncestry: conditional,
      rootId: root.id,
      canonicalPeople: basePeople,
      visibleTreePersonIds: allTreePersonIds,
    }),
  }));

  for (const { root, projection } of projections) {
    const projectedById = new Map(projection.nodes.map((node) => [node.id, node]));
    const projectionReachableNodeIds = new Set();
    const outgoing = new Map();
    for (const edge of projection.edges) addMapList(outgoing, edge.fromNodeId, edge);
    for (const values of outgoing.values()) values.sort((first, second) => compareText(first.toNodeId, second.toNodeId) || compareText(first.id, second.id));

    const starts = projection.nodes.filter((node) => {
      const mapping = mappingForNode(node, root);
      return mapping.baseId && visibleBaseIds.has(mapping.baseId);
    });
    if (!starts.length) continue;
    starts.forEach((node) => projectionReachableNodeIds.add(node.id));

    for (const start of starts) {
      const startMapping = mappingForNode(start, root);
      const startGeneration = generationOf(baseById.get(startMapping.baseId));
      const queue = [{
        node: start,
        depth: 0,
        limit: requestedDepth,
        generation: startGeneration,
        pathKeys: new Set([nodeKey(start, root)]),
      }];
      const startNodeKeys = new Set([nodeKey(start, root)]);
      const startEdgeKeys = new Set();
      const bestRemainingByNodeKey = new Map([[nodeKey(start, root), requestedDepth]]);

      while (queue.length) {
        const current = queue.shift();
        const currentMapping = mappingForNode(current.node, root);
        const displayId = currentMapping.displayId;
        const parentEdges = outgoing.get(current.node.id) || [];
        const isCollapsed = collapsed.has(displayId);
        if (isCollapsed) {
          if (parentEdges.length) {
            collapsedIds.add(displayId);
            if (!collapsedGapAdded) {
              selectedGaps.set("unified-family-tree:collapsed", {
                id: "unified-family-tree:collapsed",
                kind: "collapsed_branch",
                label: "Dalsza warunkowa gałąź jest zwinięta w tym widoku.",
              });
              collapsedGapAdded = true;
            }
          }
          continue;
        }
        const activeLimit = expanded.has(displayId)
          ? Math.max(current.limit, current.depth + requestedDepth)
          : current.limit;
        if (current.depth >= activeLimit) {
          if (parentEdges.length) {
            expandableIds.add(displayId);
            truncated = true;
            if (!limitGapAdded) {
              selectedGaps.set("unified-family-tree:depth-limit", {
                id: "unified-family-tree:depth-limit",
                kind: "presentation_depth_limit",
                label: "Dalsza warunkowa gałąź pozostaje poza bieżącym limitem widoku.",
              });
              limitGapAdded = true;
            }
          }
          continue;
        }

        for (const edge of parentEdges) {
          const parent = projectedById.get(edge.toNodeId);
          if (!parent) continue;
          const parentMapping = mappingForNode(parent, root);
          if (parentMapping.rejected) continue;
          if (parentMapping.baseId && !visibleBaseIds.has(parentMapping.baseId)) {
            if (!hiddenEndpointGapAdded) {
              selectedGaps.set("unified-family-tree:hidden-endpoint", {
                id: "unified-family-tree:hidden-endpoint",
                kind: "hidden_endpoint",
                label: "Dalsza warunkowa ścieżka została zatrzymana przez bieżący zakres widoku.",
              });
              hiddenEndpointGapAdded = true;
            }
            continue;
          }
          const parentKey = nodeKey(parent, root);
          projectionReachableNodeIds.add(parent.id);
          const edgeKey = `${namespaceFor(current.node, root)}\u0000${edge.id}`;
          if (!startEdgeKeys.has(edgeKey)
            && startEdgeKeys.size >= boundedLimit(projection.limits?.maxEdges, MAX_EDGES_PER_START)) {
            truncated = true;
            continue;
          }
          const isCycle = current.pathKeys.has(parentKey);
          const parentGeneration = parentMapping.baseId
            ? generationOf(baseById.get(parentMapping.baseId), current.generation + 1)
            : current.generation + 1;
          if (!parentMapping.baseId && !candidateRecords.has(parentKey)) candidateRecords.set(parentKey, {
            node: parent,
            root,
            mapping: parentMapping,
            depth: current.depth + 1,
            generation: parentGeneration,
            firstEdge: edge,
          });

          startEdgeKeys.add(edgeKey);
          if (!selectedEdges.has(edgeKey)) selectedEdges.set(edgeKey, {
            ...edge,
            fromDisplayId: currentMapping.displayId,
            toDisplayId: parentMapping.displayId,
            conditionalAncestry: true,
            presentationOnly: true,
            cyclic: isCycle,
          });
          if (isCycle) {
            if (!cycleGapAdded) {
              selectedGaps.set("unified-family-tree:cycle", {
                id: "unified-family-tree:cycle",
                kind: "cycle_boundary",
                label: "Dalsza warunkowa ścieżka została zatrzymana z powodu cyklu.",
              });
              cycleGapAdded = true;
            }
            continue;
          }
          if (!startNodeKeys.has(parentKey)
            && startNodeKeys.size >= boundedLimit(projection.limits?.maxNodes, MAX_NODES_PER_START)) {
            truncated = true;
            continue;
          }
          startNodeKeys.add(parentKey);
          const nextDepth = current.depth + 1;
          const nextRemaining = expanded.has(parentMapping.displayId)
            ? Math.max(activeLimit - nextDepth, requestedDepth)
            : activeLimit - nextDepth;
          if ((bestRemainingByNodeKey.get(parentKey) ?? -1) >= nextRemaining) continue;
          bestRemainingByNodeKey.set(parentKey, nextRemaining);
          queue.push({
            node: parent,
            depth: nextDepth,
            limit: activeLimit,
            generation: parentGeneration,
            pathKeys: new Set([...current.pathKeys, parentKey]),
          });
        }
      }
    }

    for (const gap of projection.gaps) {
      if (gap?.nodeId && !projectionReachableNodeIds.has(gap.nodeId)) continue;
      const key = `${namespaceFor(projectedById.get(gap.nodeId) || projectedById.get(root.nodeId), root)}\u0000${gap.id}`;
      if (!selectedGaps.has(key)) selectedGaps.set(key, { ...gap });
      if (gapSignalsTruncation(gap)) truncated = true;
    }
  }

  const clonedBasePeople = [...outputBaseIds].flatMap((id) => {
    const person = baseById.get(id);
    if (!person) return [];
    const identitySources = external.identitySourcesByPerson.get(id) || [];
    return [{ ...person, identitySources: identitySources.map((item) => ({ ...item })) }];
  });
  const candidatePeople = [...candidateRecords.values()]
    .sort((first, second) => first.generation - second.generation
      || compareText(first.mapping.displayId, second.mapping.displayId))
    .map((record, order) => {
      const { node, root, mapping, firstEdge } = record;
      const exactExternal = node.scope === "source_reported_network" ? mapping.exactRawProfile : null;
      const publicProfileId = node.scope === "source_reported_network"
        ? exactExternal?.publicProfileId || null
        : typeof node.publicProfileId === "string" ? node.publicProfileId : null;
      const projectedNode = {
        ...node,
        id: node.id,
        displayId: mapping.displayId,
        label: node.label || "Warunkowy przodek",
        scope: node.scope || "presentation_only",
        publicProfileId,
        provenance: node.provenance || firstEdge?.provenance || null,
        depth: record.depth,
        generation: record.generation,
      };
      return {
        id: mapping.displayId,
        name: projectedNode.label,
        lifeSpanLabel: node.lifeSpanLabel || "?–?",
        generation: record.generation,
        order,
        evidenceStatus: "unresolved",
        sourceIds: [...new Set(list(node.sourceIds).filter((id) => typeof id === "string"))],
        conditionalAncestry: true,
        conditionalAncestryNode: projectedNode,
        pathUncertain: true,
        conditionalAncestryCollapsed: collapsedIds.has(mapping.displayId),
        ...(exactExternal ? {
          sourceReportedNetworkId: node.networkId,
          publicProfileId: exactExternal.publicProfileId,
        } : publicProfileId ? { publicProfileId } : {}),
      };
    });

  const people = [...clonedBasePeople, ...candidatePeople].sort((first, second) => (
    (Number(first.generation) || 0) - (Number(second.generation) || 0)
      || (Number(first.order) || 0) - (Number(second.order) || 0)
      || compareText(first.id, second.id)
  ));
  const peopleIds = new Set(people.map((person) => person.id));
  const baseLayoutFamilies = list(tree?.families).flatMap((family) => {
    const parentIds = [...new Set(list(family?.parentIds).filter((id) => peopleIds.has(id)))];
    const childIds = [...new Set(list(family?.childIds).filter((id) => peopleIds.has(id)))];
    return parentIds.length && childIds.length ? [{ ...family, parentIds, childIds }] : [];
  });
  const basePairs = new Set(baseLayoutFamilies.flatMap((family) => (
    family.childIds.flatMap((childId) => family.parentIds.map((parentId) => pairKey(childId, parentId)))
  )));
  const supportByPair = new Map();
  const drawableGroups = new Map();
  const edges = [...selectedEdges.values()].map((edge) => {
    const key = pairKey(edge.fromDisplayId, edge.toDisplayId);
    const suppressedByBaseFamily = basePairs.has(key);
    const suppressedByCycle = edge.cyclic === true;
    const projected = {
      ...edge,
      suppressedByBaseFamily,
      suppressedByCycle,
      draw: !suppressedByBaseFamily && !suppressedByCycle,
    };
    addMapList(supportByPair, key, projected);
    if (!suppressedByBaseFamily && !suppressedByCycle) {
      const parentNode = [...candidateRecords.values()].find((record) => record.mapping.displayId === edge.toDisplayId)?.node;
      const group = provenanceGroup(edge, parentNode);
      const groupKey = `${edge.fromDisplayId}\u0000${group.key}`;
      const current = drawableGroups.get(groupKey) || {
        childId: edge.fromDisplayId,
        parentIds: new Set(),
        edgeIds: [],
        sourceNetworkId: group.sourceNetworkId,
        provenance: edge.provenance || null,
      };
      current.parentIds.add(edge.toDisplayId);
      current.edgeIds.push(edge.id);
      drawableGroups.set(groupKey, current);
    }
    return projected;
  });
  const syntheticFamilies = [...drawableGroups.entries()].map(([key, group], index) => ({
    id: `conditional-family:${encodeURIComponent(key)}:${index}`,
    parentIds: [...group.parentIds],
    childIds: [group.childId],
    status: "unresolved",
    conditionalAncestry: true,
    presentationOnly: true,
    canonicalRelationship: false,
    spouseAssumption: false,
    unionId: null,
    sourceNetworkId: group.sourceNetworkId,
    provenance: group.provenance,
    backingEdgeIds: [...new Set(group.edgeIds)],
  }));

  return {
    people,
    layoutFamilies: [...baseLayoutFamilies, ...syntheticFamilies],
    edges,
    aliases: external.aliases,
    identitySourcesByPerson: external.identitySourcesByPerson,
    supportByPair,
    gaps: [...selectedGaps.values()],
    expandableIds,
    collapsedIds,
    truncated,
  };
}
