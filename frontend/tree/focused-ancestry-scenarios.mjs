import { conditionalAncestryRootOptions, projectConditionalAncestry } from "./conditional-ancestry-overlay.mjs";
import { familyRelationshipCanonical } from "./family-tree-model.mjs";

const list = (value) => Array.isArray(value) ? value : [];
const protectedStatuses = new Set(["masked", "masked_living", "private", "restricted"]);
export function focusedPersonProtected(person) {
  const privacy = typeof person?.privacy === "string" ? person.privacy : person?.privacy?.status;
  const normalize = (value) => typeof value === "string" ? value.trim().toLowerCase() : value;
  return person?.sourceRedacted === true || person?.privacy?.redacted === true
    || protectedStatuses.has(normalize(privacy)) || protectedStatuses.has(normalize(person?.evidenceStatus));
}

/** Adapts the existing public family view, never source-network or FAN edges. */
export function canonicalFocusedScenario(tree, rootPersonId) {
  const people = list(tree?.people);
  const root = people.find((person) => person.id === rootPersonId);
  if (!root || focusedPersonProtected(root)) return null;
  const byId = new Map(people.map((person) => [person.id, person]));
  const edges = [];
  const gaps = [];
  for (const family of list(tree?.families)) {
    if (typeof family?.id !== "string") continue;
    for (const childId of list(family.childIds)) {
      if (!byId.has(childId)) continue;
      for (const parentId of list(family.parentIds)) {
        if (!byId.has(parentId) || focusedPersonProtected(byId.get(parentId)) || focusedPersonProtected(byId.get(childId))) {
          gaps.push({ nodeId: childId, kind: "blocked", label: "Dalsza ścieżka nie jest dostępna w tym widoku.", conditionLabel: "Granica opublikowanych danych; nie dowód braku rodziców." });
          continue;
        }
        edges.push({ id: `${family.id}:${childId}:${parentId}`, fromNodeId: childId, toNodeId: parentId,
          status: family.status || "unresolved", canonicalRelationship: familyRelationshipCanonical(family),
          ...(!familyRelationshipCanonical(family) ? { relationshipIdentityStatus: family.relationshipIdentityStatus || "unresolved" } : {}),
          sourceIds: [...list(family.sourceIds)], provenance: { kind: "existing_family", familyId: family.id } });
      }
    }
  }
  const reachable = new Set([rootPersonId]);
  const queue = [rootPersonId];
  while (queue.length) {
    const childId = queue.shift();
    for (const edge of edges.filter((item) => item.fromNodeId === childId)) {
      if (reachable.has(edge.toNodeId)) continue;
      reachable.add(edge.toNodeId);
      queue.push(edge.toNodeId);
    }
  }
  const uncertain = new Set();
  const uncertainQueue = edges.filter((edge) => reachable.has(edge.fromNodeId) && !edge.canonicalRelationship)
    .map((edge) => edge.toNodeId);
  while (uncertainQueue.length) {
    const id = uncertainQueue.shift();
    if (uncertain.has(id)) continue;
    uncertain.add(id);
    uncertainQueue.push(...edges.filter((edge) => edge.fromNodeId === id).map((edge) => edge.toNodeId));
  }
  return {
    key: `canonical:${rootPersonId}`, label: `Drzewo bazowe · ${root.name}`, basis: "canonical", rootNodeId: rootPersonId,
    nodes: people.filter((person) => reachable.has(person.id) && !focusedPersonProtected(person)).map((person) => ({
      id: person.id, label: person.name, person, basisLabel: "Osoba z drzewa bazowego", pathUncertain: uncertain.has(person.id),
    })), edges: edges.filter((edge) => reachable.has(edge.fromNodeId)), gaps: gaps.filter((gap) => reachable.has(gap.nodeId)), truncated: false,
  };
}

function conditionalPerson(node) {
  return { id: node.displayId, name: node.label, lifeSpanLabel: node.lifeSpanLabel || "?–?",
    generation: node.depth, evidenceStatus: "unresolved", conditionalAncestry: true,
    conditionalAncestryNode: node, publicProfileId: node.publicProfileId || null,
    occurrenceId: node.occurrenceId || null, sourceIds: [...list(node.sourceIds)] };
}

/** Exact typed identifiers only; resolved display identity never crosses namespaces. */
function matchesNode(node, person) {
  if (person?.conditionalAncestry) return node.id === person.conditionalAncestryNode?.id;
  if (person?.sourceReported) return node.scope === "source_reported_network"
    && node.networkId === person.sourceReportedNetworkId && typeof person.publicProfileId === "string"
    && node.publicProfileId === person.publicProfileId;
  return node.scope !== "source_reported_network" && node.identityScope === "canonical"
    && node.treePersonId === person?.id;
}

export function focusedScenariosForPerson({ tree, person, conditionalAncestry = tree?.conditionalAncestry } = {}) {
  if (!person || focusedPersonProtected(person)) return [];
  const scenarios = [];
  const canonical = !person.conditionalAncestry && !person.sourceReported ? canonicalFocusedScenario(tree, person.id) : null;
  if (canonical) scenarios.push(canonical);
  for (const root of conditionalAncestryRootOptions(conditionalAncestry)) {
    if (person.sourceReported && (root.scope !== "source_reported_network" || root.networkId !== person.sourceReportedNetworkId)) continue;
    if (!person.sourceReported && !person.conditionalAncestry && root.treePersonId !== person.id) continue;
    if (person.conditionalAncestry && root.scope === "source_reported_network"
      && root.networkId !== person.conditionalAncestryNode?.networkId) continue;
    const projection = projectConditionalAncestry({ conditionalAncestry, rootId: root.id,
      canonicalPeople: list(tree?.people), visibleTreePersonIds: list(tree?.people).map((p) => p.id) });
    const target = projection.nodes.find((item) => matchesNode(item, person));
    if (!target) continue;
    const nodeIds = new Set(projection.nodes.map((item) => item.id));
    scenarios.push({
      key: `conditional:${root.id}`, label: `${root.label} · ${root.scope === "source_reported_network" ? "drzewo zewnętrzne" : "lokalna ścieżka warunkowa"}`,
      basis: root.scope === "source_reported_network" ? "external_tree" : "local_source",
      rootNodeId: root.nodeId, entryNodeId: target.id,
      nodes: projection.nodes.map((item) => ({ ...item, label: item.label, person: conditionalPerson(item) })),
      edges: projection.edges,
      gaps: projection.gaps.filter((gap) => !gap.nodeId || nodeIds.has(gap.nodeId))
        .map((gap) => ({ ...gap, nodeId: gap.nodeId || root.nodeId })),
      truncated: projection.truncated,
      limitLabel: "Granica zapisanej projekcji; brak dalszej ścieżki nie oznacza braku przodków.",
    });
  }
  if (!scenarios.length) scenarios.push({
    key: `unavailable:${person.sourceReportedNetworkId || "local"}:${person.id}`, label: `${person.name} · przegląd ścieżki`,
    basis: person.sourceReported ? "external_tree" : "local_source", rootNodeId: person.id,
    nodes: [{ id: person.id, label: person.name, person, basisLabel: person.sourceReported ? "Profil zewnętrzny" : "Wystąpienie źródłowe" }], edges: [],
    gaps: [{ nodeId: person.id, kind: "blocked", label: "Brak zakwalifikowanej ścieżki w tej projekcji.", conditionLabel: "Uzupełnić kwalifikowaną proweniencję i publiczne końce relacji; nie wyprowadzać rodzicielstwa z FAN lub nazwiska." }], truncated: false,
  });
  return scenarios;
}
