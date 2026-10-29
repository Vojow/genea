import { familyKnowledgePresentation, sanitizeFamilyKnowledge } from "../core/family-knowledge.mjs";

export const EXTERNAL_NETWORK_FILTERS = Object.freeze([
  { id: "all", label: "Wszystkie" },
  { id: "direct_ancestor", label: "Przodkowie" },
  { id: "ancestor_spouse", label: "Małżonkowie przodków" },
  { id: "collateral", label: "Linie poboczne" },
  { id: "descendant", label: "Potomkowie" },
  { id: "fan", label: "FAN" },
  { id: "unclassified", label: "Nieklasyfikowane" },
  { id: "conflict", label: "Konflikty" },
]);

export const EXTERNAL_NETWORK_ROLE_LABELS = Object.freeze({
  anchor: "Punkt odniesienia",
  direct_ancestor: "Kandydat na bezpośredniego przodka",
  ancestor_spouse: "Małżonek / małżonka przodka",
  collateral: "Linia poboczna",
  descendant: "Potomek wskazany w drzewie zewnętrznym",
  fan: "Rola FAN wskazana w drzewie zewnętrznym",
  unclassified: "Rola nieklasyfikowana",
  conflict: "Sprzeczne klasyfikacje",
});

const FILTER_IDS = new Set(EXTERNAL_NETWORK_FILTERS.map((item) => item.id));
const IDENTITY_DECISION_TIERS = new Set(["agent_approved_human_pending", "human_confirmed"]);

// Legacy families retain their existing meaning; only an explicit false opts out.
export function familyRelationshipCanonical(family) {
  return family?.canonicalRelationship !== false;
}

export function familyRelationshipQualifier(family) {
  return familyRelationshipCanonical(family) ? null : "Relacja źródłowa · do weryfikacji";
}

function identityDecisionTier(item) {
  return IDENTITY_DECISION_TIERS.has(item?.identityDecisionTier) ? item.identityDecisionTier : null;
}

function sourceReportedProfileIdentityResolved(item) {
  return item?.personIdentityResolved === true
    || item?.profileIdentityStatus === "resolved_same"
    || item?.identityStatus === "resolved_same";
}

function classificationFilter(node) {
  const classification = node?.classification || {};
  if (classification.state === "conflict") return "conflict";
  if (classification.state !== "classified" || !classification.primaryRole) return "unclassified";
  return classification.primaryRole;
}

export class FamilyTreeModel {
  constructor(tree) {
    this.tree = tree;
    this.sourceReportedNetworks = (tree?.sourceReportedNetworks || []).filter((network) => network?.id);
    this.networkById = new Map(this.sourceReportedNetworks.map((network) => [network.id, network]));
    this.networkPeopleById = new Map(this.sourceReportedNetworks.map((network) => [
      network.id,
      (network.nodes || []).map((item, index) => this.#sourceReportedPerson(network, item, index)),
    ]));
    this.personById = new Map([
      ...(tree?.people || []).map((person) => [person.id, person]),
      ...[...this.networkPeopleById.values()].flat().map((person) => [person.id, person]),
    ]);
    this.branchById = new Map((tree?.branches || []).map((branch) => [branch.id, branch]));
  }

  #sourceReportedPerson(network, item, index) {
    const treeNodeId = item?.treeNodeId;
    const classification = item?.classification || {};
    const candidateRole = classificationFilter(item);
    const isAnchor = classification.primaryRole === "anchor";
    const identityStatus = item?.profileIdentityStatus || item?.identityStatus || "unresolved";
    const personIdentityResolved = sourceReportedProfileIdentityResolved(item);
    const decisionTier = personIdentityResolved ? identityDecisionTier(item) : null;
    const familyKnowledge = sanitizeFamilyKnowledge(item?.familyKnowledge);
    const familyKnown = familyKnowledgePresentation(familyKnowledge);
    return {
      id: treeNodeId,
      publicProfileId: item?.publicProfileId || treeNodeId,
      personId: item?.personId || null,
      occurrenceId: item?.occurrenceId || null,
      name: item?.label || "Osoba bez etykiety",
      lifeSpanLabel: "?–?",
      generation: Number.isFinite(Number(item?.generation)) ? Number(item.generation) : 0,
      order: Number.isFinite(Number(item?.order)) ? Number(item.order) : index,
      sex: "unknown",
      summary: personIdentityResolved
        ? decisionTier === "agent_approved_human_pending"
          ? "Profil zewnętrzny · tożsamość zatwierdzona agentowo — oczekuje potwierdzenia człowieka"
          : decisionTier === "human_confirmed"
            ? "Profil zewnętrzny · tożsamość potwierdzona przez człowieka"
            : "Profil zewnętrzny · tożsamość rozstrzygnięta"
        : "Zewnętrzny profil kandydacki · tożsamość nierozstrzygnięta",
      evidenceStatus: item?.status || network?.status || "unresolved",
      identityStatus,
      profileIdentityStatus: identityStatus,
      identityDecisionTier: decisionTier,
      relationshipIdentityStatus: "unresolved",
      personIdentityResolved,
      familyKnowledge,
      familyKnown,
      occurrenceIds: item?.occurrenceId ? [item.occurrenceId] : [],
      sourceIds: [],
      facts: [],
      sourceReported: true,
      sourceReportedNetworkId: network.id,
      sourceReportedStatus: "imported_unverified",
      classification,
      classificationState: classification.state || "unclassified",
      candidateRole,
      candidateRoleLabel: isAnchor
        ? EXTERNAL_NETWORK_ROLE_LABELS.anchor
        : EXTERNAL_NETWORK_ROLE_LABELS[candidateRole] || EXTERNAL_NETWORK_ROLE_LABELS.unclassified,
      isNetworkAnchor: isAnchor,
      outsideFilterConnector: false,
      canonicalRelationship: false,
      automaticMerge: false,
    };
  }

  basePeople(mode, selectionId, candidateRole = "all") {
    if (!this.tree) return [];
    if (mode === "network") {
      return this.networkProjection(selectionId, candidateRole).people
        .sort((a, b) => a.generation - b.generation || a.order - b.order || a.id.localeCompare(b.id, "pl"));
    }
    const branch = this.branchById.get(selectionId);
    const allowed = mode === "branch" ? new Set(branch?.personIds || this.tree.focusPath || []) : null;
    return (this.tree.people || [])
      .filter((person) => !allowed || allowed.has(person.id))
      .sort((a, b) => a.generation - b.generation || a.order - b.order);
  }

  network(networkId) { return this.networkById.get(networkId) || null; }
  defaultNetworkId() { return this.sourceReportedNetworks[0]?.id || null; }

  isExternalNetworkFilter(value) { return FILTER_IDS.has(value); }

  externalNetworkFilterCounts(networkId) {
    const people = this.networkPeopleById.get(networkId) || [];
    const counts = Object.fromEntries(EXTERNAL_NETWORK_FILTERS.map((item) => [item.id, 0]));
    counts.all = people.length;
    for (const person of people) {
      if (person.isNetworkAnchor) continue;
      if (FILTER_IDS.has(person.candidateRole)) counts[person.candidateRole] += 1;
    }
    return counts;
  }

  externalNetworkIdentityCounts(networkId) {
    const people = this.networkPeopleById.get(networkId) || [];
    return people.reduce((counts, person) => {
      if (person.profileIdentityStatus === "unresolved") counts.unresolved += 1;
      if (person.profileIdentityStatus !== "resolved_same") return counts;
      counts.resolvedSame += 1;
      if (person.identityDecisionTier === "agent_approved_human_pending") counts.agentApprovedHumanPending += 1;
      else if (person.identityDecisionTier === "human_confirmed") counts.humanConfirmed += 1;
      else counts.resolvedWithoutDecisionTier += 1;
      return counts;
    }, {
      unresolved: 0,
      resolvedSame: 0,
      agentApprovedHumanPending: 0,
      humanConfirmed: 0,
      resolvedWithoutDecisionTier: 0,
    });
  }

  networkPersonByPublicProfileId(networkId, publicProfileId) {
    return (this.networkPeopleById.get(networkId) || [])
      .find((person) => person.publicProfileId === publicProfileId) || null;
  }

  networkProjection(networkId, candidateRole = "all") {
    const network = this.network(networkId);
    const people = this.networkPeopleById.get(networkId) || [];
    if (!network || candidateRole === "all" || !FILTER_IDS.has(candidateRole)) {
      return {
        network,
        people: people.map((person) => ({ ...person, outsideFilterConnector: false })),
        edges: (network?.edges || []).map((edge) => ({ ...edge, outsideFilterConnector: false })),
      };
    }

    const directIds = new Set(people
      .filter((person) => !person.isNetworkAnchor && person.candidateRole === candidateRole)
      .map((person) => person.id));
    const anchorIds = new Set(people.filter((person) => person.isNetworkAnchor).map((person) => person.id));
    const requiredRelationshipIds = new Set();
    for (const person of people) {
      if (!directIds.has(person.id)) continue;
      for (const alternative of person.classification?.alternatives || []) {
        for (const relationshipId of alternative.backingRelationshipIds || []) requiredRelationshipIds.add(relationshipId);
      }
    }

    const edges = (network.edges || []).filter((edge) => requiredRelationshipIds.has(edge.relationshipId));
    const visibleIds = new Set([...directIds, ...anchorIds]);
    for (const edge of edges) {
      visibleIds.add(edge.fromTreeNodeId);
      visibleIds.add(edge.toTreeNodeId);
    }
    const connectorIds = new Set([...visibleIds].filter((id) => !directIds.has(id) && !anchorIds.has(id)));
    return {
      network,
      people: people
        .filter((person) => visibleIds.has(person.id))
        .map((person) => ({
          ...person,
          outsideFilterConnector: !directIds.has(person.id) && !anchorIds.has(person.id),
        })),
      edges: edges.map((edge) => ({
        ...edge,
        outsideFilterConnector: connectorIds.has(edge.fromTreeNodeId) || connectorIds.has(edge.toTreeNodeId),
      })),
    };
  }

  visiblePeople({ basePeople, collapsedAncestors, collapsedUnions, expandedFamilies, activeUnion }) {
    const baseIds = new Set(basePeople.map((person) => person.id));
    const hidden = new Set();
    const familiesByChild = new Map();
    const familiesByParent = new Map();
    for (const family of this.tree?.families || []) {
      for (const childId of family.childIds || []) this.#append(familiesByChild, childId, family);
      for (const parentId of family.parentIds || []) this.#append(familiesByParent, parentId, family);
    }
    const hideDescendants = (personId) => {
      const queue = [personId];
      while (queue.length) {
        const descendantId = queue.shift();
        if (hidden.has(descendantId)) continue;
        hidden.add(descendantId);
        for (const family of familiesByParent.get(descendantId) || []) {
          queue.push(...(family.childIds || []).filter((id) => baseIds.has(id)));
        }
      }
    };
    for (const family of this.tree?.families || []) {
      const children = (family.childIds || []).filter((id) => baseIds.has(id));
      if (!familyRelationshipCanonical(family) || family.showAllChildren === true || children.length < 2 || expandedFamilies.has(family.id)) continue;
      const primaryChildId = this.primaryChildId(family, children);
      children.filter((id) => id !== primaryChildId).forEach(hideDescendants);
    }
    for (const personId of collapsedAncestors) {
      const queue = [...(familiesByChild.get(personId) || []).flatMap((family) => family.parentIds || [])];
      while (queue.length) {
        const ancestorId = queue.shift();
        if (hidden.has(ancestorId)) continue;
        hidden.add(ancestorId);
        queue.push(...(familiesByChild.get(ancestorId) || []).flatMap((family) => family.parentIds || []));
      }
    }
    for (const union of this.tree?.unions || []) {
      if (union.partnerIds.some((id) => hidden.has(id))) union.partnerIds.forEach((id) => hidden.add(id));
    }
    for (const ownerId of collapsedUnions) {
      const selectedUnion = activeUnion(ownerId);
      for (const union of this.ownedUnions(ownerId)) {
        if (union.id === selectedUnion?.id) continue;
        const partnerId = union.partnerIds.find((id) => id !== ownerId);
        if (!partnerId) continue;
        hidden.add(partnerId);
        const queue = [...(familiesByChild.get(partnerId) || []).flatMap((family) => family.parentIds || [])];
        while (queue.length) {
          const ancestorId = queue.shift();
          if (hidden.has(ancestorId)) continue;
          hidden.add(ancestorId);
          queue.push(...(familiesByChild.get(ancestorId) || []).flatMap((family) => family.parentIds || []));
        }
      }
    }
    return basePeople.filter((person) => !hidden.has(person.id));
  }

  #append(index, key, value) {
    const items = index.get(key) || [];
    items.push(value);
    index.set(key, items);
  }

  additionalUnions(personId) { return (this.tree?.unions || []).filter((union) => union.ownerId === personId && union.primary === false); }
  primaryChildId(family, children = family?.childIds || []) { return children.includes(family?.primaryChildId) ? family.primaryChildId : children[0]; }
  ownedUnions(personId) { return (this.tree?.unions || []).filter((union) => union.ownerId === personId); }

  defaultCollapsedUnionOwners() {
    const counts = new Map();
    for (const union of this.tree?.unions || []) {
      if (!union.ownerId) continue;
      counts.set(union.ownerId, (counts.get(union.ownerId) || 0) + 1);
    }
    return [...counts].filter(([, count]) => count > 1).map(([ownerId]) => ownerId);
  }

  activeUnion(personId, activeUnionByOwner) {
    const unions = this.ownedUnions(personId);
    const activeId = activeUnionByOwner.get(personId);
    return unions.find((union) => union.id === activeId) || unions.find((union) => union.primary) || unions[0] || null;
  }

  expandableFamilies(personId, basePeople) {
    const baseIds = new Set(basePeople.map((person) => person.id));
    return (this.tree?.families || []).filter((family) => {
      const belongs = personId == null || (family.parentIds || []).includes(personId);
      const children = (family.childIds || []).filter((id) => baseIds.has(id));
      return belongs && familyRelationshipCanonical(family) && family.showAllChildren !== true && children.length > 1;
    });
  }

  hasAncestorBranch(personId, basePeople) {
    const baseIds = new Set(basePeople.map((person) => person.id));
    return (this.tree?.families || []).some((family) =>
      family.childIds.includes(personId) && family.parentIds.some((id) => baseIds.has(id))
    );
  }

  peoplePerGeneration(people = this.tree?.people || []) {
    const counts = new Map();
    for (const person of people) counts.set(person.generation, (counts.get(person.generation) || 0) + 1);
    return [...counts.values()];
  }
}
