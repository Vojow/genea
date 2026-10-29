const RING_STEP = 270;
const CORE_RADIUS = 92;

function hashOf(value) {
  return [...String(value)].reduce((sum, character) => (sum * 33 + character.charCodeAt(0)) >>> 0, 5381);
}

function angleOf(value) {
  return (hashOf(value) % 360) * Math.PI / 180;
}

function average(points) {
  if (!points.length) return null;
  return {
    x: points.reduce((sum, item) => sum + item.x, 0) / points.length,
    y: points.reduce((sum, item) => sum + item.y, 0) / points.length,
  };
}

function gridPosition(index, count, spacing = 250) {
  const columns = Math.max(1, Math.ceil(Math.sqrt(count)));
  const row = Math.floor(index / columns);
  const column = index % columns;
  return {
    x: (column - (columns - 1) / 2) * spacing,
    y: (row - (Math.ceil(count / columns) - 1) / 2) * spacing,
  };
}

function polar(radius, angle) {
  return { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius };
}

function orbit(anchor, id, index, count, radius) {
  const base = angleOf(id);
  const angle = base + (Math.PI * 2 * index) / Math.max(1, count);
  return { x: anchor.x + Math.cos(angle) * radius, y: anchor.y + Math.sin(angle) * radius };
}

function connect(adjacency, left, right) {
  if (!left || !right || left === right) return;
  const leftSet = adjacency.get(left) || new Set();
  leftSet.add(right);
  adjacency.set(left, leftSet);
  const rightSet = adjacency.get(right) || new Set();
  rightSet.add(left);
  adjacency.set(right, rightSet);
}

function sorted(values) {
  return [...values].sort((left, right) => String(left).localeCompare(String(right)));
}

function placeBand(targetById, items, center, baseRadius, clusterId, step = 68) {
  const ordered = [...items].sort((left, right) => left.id.localeCompare(right.id));
  let offset = 0;
  let ring = 0;
  while (offset < ordered.length) {
    const capacity = Math.max(6, Math.floor((Math.PI * 2 * Math.max(baseRadius + ring * step, 60)) / 72));
    const band = ordered.slice(offset, offset + capacity);
    const radius = baseRadius + ring * step;
    const start = angleOf(`${clusterId}:${ring}`);
    band.forEach((item, index) => {
      const angle = start + (Math.PI * 2 * index) / Math.max(1, band.length);
      targetById.set(item.id, {
        x: center.x + Math.cos(angle) * radius,
        y: center.y + Math.sin(angle) * radius,
      });
    });
    offset += band.length;
    ring += 1;
  }
}

function nodeOccurrenceIds(node) {
  if (node.kind === "occurrence") return [node.item?.id || node.ref].filter(Boolean);
  if (node.kind === "identity") return [...(node.item?.occurrenceIds || [])];
  if (node.kind === "event") return (node.item?.participants || []).map((item) => item.occurrenceId).filter(Boolean);
  if (node.kind === "household") return (node.item?.members || []).map((item) => item.occurrenceId).filter(Boolean);
  return [];
}

export class SemanticGraphLayout {
  constructor({ payload = null } = {}) {
    this.payload = payload;
    this.guides = [];
    this.topology = this.#buildFamilyTopology(payload?.familyTree);
    this.occurrenceToTreePerson = new Map();
    for (const person of payload?.familyTree?.people || []) {
      for (const occurrenceId of person.occurrenceIds || []) this.occurrenceToTreePerson.set(occurrenceId, person.id);
    }
    this.canonicalToTreePerson = new Map();
    for (const identity of payload?.identityGroups || []) {
      const treePersonId = (identity.occurrenceIds || [])
        .map((occurrenceId) => this.occurrenceToTreePerson.get(occurrenceId))
        .find(Boolean);
      if (treePersonId) this.canonicalToTreePerson.set(identity.personId, treePersonId);
    }
    this.scopes = (payload?.graphScopes?.scopes || [])
      .filter((scope) => (scope.seedOccurrenceIds || []).length || (scope.seedHouseholdIds || []).length);
    this.scopeOrder = new Map(this.scopes.map((scope, index) => [scope.id, index]));
  }

  apply({ nodes, edges, layoutEdges = [], previousPositions = new Map(), mode = "people" }) {
    if (nodes.some((item) => item.kind === "scope")) return this.#orbital({ nodes, edges, previousPositions, mode });
    if (mode === "people" || mode === "fan" || mode === "clusters") return this.#researchIslands({ nodes, edges, layoutEdges, previousPositions, mode });
    if (!this.topology) return this.#layered({ nodes, edges, previousPositions, mode });
    return this.#orbital({ nodes, edges, previousPositions, mode });
  }

  #buildFamilyTopology(tree) {
    if (!tree?.rootPersonId || !tree?.people?.length) return null;
    const people = new Map(tree.people.map((item) => [item.id, item]));
    const adjacency = new Map(tree.people.map((item) => [item.id, new Set()]));
    const parents = new Set();
    const partners = new Set();
    const children = new Set();
    for (const family of tree.families || []) {
      const parentIds = (family.parentIds || []).filter((id) => people.has(id));
      const childIds = (family.childIds || []).filter((id) => people.has(id));
      for (let first = 0; first < parentIds.length; first += 1) {
        for (let second = first + 1; second < parentIds.length; second += 1) connect(adjacency, parentIds[first], parentIds[second]);
      }
      for (const parentId of parentIds) for (const childId of childIds) connect(adjacency, parentId, childId);
      if (childIds.includes(tree.rootPersonId)) parentIds.forEach((id) => parents.add(id));
      if (parentIds.includes(tree.rootPersonId)) {
        parentIds.filter((id) => id !== tree.rootPersonId).forEach((id) => partners.add(id));
        childIds.forEach((id) => children.add(id));
      }
    }
    for (const union of tree.unions || []) {
      const partnerIds = (union.partnerIds || []).filter((id) => people.has(id));
      for (let first = 0; first < partnerIds.length; first += 1) {
        for (let second = first + 1; second < partnerIds.length; second += 1) connect(adjacency, partnerIds[first], partnerIds[second]);
      }
    }

    const root = tree.rootPersonId;
    const distance = new Map([[root, 0]]);
    const firstHop = new Map([[root, root]]);
    const queue = [root];
    while (queue.length) {
      const current = queue.shift();
      for (const neighbor of sorted(adjacency.get(current) || [])) {
        if (distance.has(neighbor)) continue;
        distance.set(neighbor, distance.get(current) + 1);
        firstHop.set(neighbor, current === root ? neighbor : firstHop.get(current));
        queue.push(neighbor);
      }
    }

    const generation = new Map(
      [...people].filter(([, item]) => Number.isInteger(item.generation)).map(([id, item]) => [id, item.generation]),
    );
    generation.set(root, 0);
    let changed = true;
    while (changed) {
      changed = false;
      const assign = (id, value) => {
        if (!people.has(id) || generation.has(id) || !Number.isInteger(value)) return;
        generation.set(id, value);
        changed = true;
      };
      for (const family of tree.families || []) {
        const parentIds = (family.parentIds || []).filter((id) => people.has(id));
        const childIds = (family.childIds || []).filter((id) => people.has(id));
        const knownChild = childIds.find((id) => generation.has(id));
        const knownParent = parentIds.find((id) => generation.has(id));
        if (knownChild) parentIds.forEach((id) => assign(id, generation.get(knownChild) + 1));
        if (knownParent) childIds.forEach((id) => assign(id, generation.get(knownParent) - 1));
        if (knownParent) parentIds.forEach((id) => assign(id, generation.get(knownParent)));
      }
      for (const union of tree.unions || []) {
        const partnerIds = (union.partnerIds || []).filter((id) => people.has(id));
        const knownPartner = partnerIds.find((id) => generation.has(id));
        if (knownPartner) partnerIds.forEach((id) => assign(id, generation.get(knownPartner)));
      }
    }

    const branchAngle = new Map();
    sorted(parents).forEach((id, index, list) => {
      branchAngle.set(id, list.length === 1 ? -Math.PI / 2 : -Math.PI * .75 + index * Math.PI / 2);
    });
    sorted(partners).forEach((id, index) => branchAngle.set(id, Math.PI * .25 + index * .42));
    sorted(children).forEach((id, index, list) => {
      branchAngle.set(id, Math.PI / 2 + (index - (list.length - 1) / 2) * .34);
    });
    const remaining = sorted(adjacency.get(root) || []).filter((id) => !branchAngle.has(id));
    remaining.forEach((id, index) => branchAngle.set(id, -Math.PI + (Math.PI * 2 * index) / Math.max(1, remaining.length)));

    const core = new Set([...people.keys()].filter((id) => generation.get(id) === 0));
    core.add(root);
    const positions = new Map([[root, { x: 0, y: 0, ring: 0, generation: 0, sector: "root" }]]);
    const coreOthers = sorted(core).filter((id) => id !== root);
    coreOthers.forEach((id, index) => {
      const angle = branchAngle.get(id) ?? -Math.PI + (Math.PI * 2 * index) / Math.max(1, coreOthers.length);
      positions.set(id, { ...polar(CORE_RADIUS, angle), ring: 0, generation: 0, sector: firstHop.get(id) || id });
    });

    const groups = new Map();
    for (const personId of people.keys()) {
      if (core.has(personId) || !generation.has(personId)) continue;
      const key = `${generation.get(personId)}|${firstHop.get(personId)}`;
      const group = groups.get(key) || [];
      group.push(personId);
      groups.set(key, group);
    }
    for (const [key, personIds] of groups) {
      const [generationValue, sector] = key.split("|");
      const personGeneration = Number(generationValue);
      const ring = Math.abs(personGeneration);
      const ordered = sorted(personIds);
      const branchBase = branchAngle.get(sector) ?? angleOf(sector);
      const baseAngle = personGeneration > 0
        ? -Math.PI / 2 + Math.cos(branchBase) * .92
        : Math.PI / 2 + Math.cos(branchBase) * .92;
      const spacing = Math.min(.22, 1.18 / Math.max(1, ordered.length));
      ordered.forEach((personId, index) => {
        const angle = baseAngle + (index - (ordered.length - 1) / 2) * spacing;
        positions.set(personId, { ...polar(ring * RING_STEP, angle), ring, generation: personGeneration, sector });
      });
    }
    return { tree, people, adjacency, root, distance, generation, firstHop, branchAngle, core, positions };
  }

  #treePersonForNode(node) {
    if (node.kind === "bridge") return node.item?.treePersonId || node.ref || null;
    if (node.kind === "identity") return this.canonicalToTreePerson.get(node.item?.personId) || null;
    if (node.contextualEvidenceFor) return this.canonicalToTreePerson.get(node.contextualEvidenceFor) || null;
    const candidates = nodeOccurrenceIds(node)
      .map((occurrenceId) => this.occurrenceToTreePerson.get(occurrenceId))
      .filter(Boolean)
      .sort((left, right) => (this.topology.distance.get(left) ?? Infinity) - (this.topology.distance.get(right) ?? Infinity)
        || left.localeCompare(right));
    return candidates[0] || null;
  }

  #scopeLabels(nodes, edges) {
    const nodeById = new Map(nodes.map((item) => [item.id, item]));
    const adjacency = new Map(nodes.map((item) => [item.id, new Set()]));
    for (const edge of edges) {
      if (edge.kind === "candidate" || edge.kind === "bridge") continue;
      if (!nodeById.has(edge.from) || !nodeById.has(edge.to)) continue;
      connect(adjacency, edge.from, edge.to);
    }
    const best = new Map();
    const queue = [];
    for (const node of nodes) {
      const declaredScopeIds = (node.scopeIds || []).filter((id) => this.scopeOrder.has(id));
      const occurrenceIds = new Set(nodeOccurrenceIds(node));
      const matching = this.scopes.filter((scope) => declaredScopeIds.includes(scope.id) ||
        (scope.seedOccurrenceIds || []).some((id) => occurrenceIds.has(id))
        || (node.kind === "household" && (scope.seedHouseholdIds || []).includes(node.ref)));
      if (!matching.length) continue;
      matching.sort((left, right) => this.scopeOrder.get(left.id) - this.scopeOrder.get(right.id));
      const scopeId = matching[0].id;
      best.set(node.id, { scopeId, distance: 0 });
      queue.push(node.id);
    }
    queue.sort();
    while (queue.length) {
      const currentId = queue.shift();
      const current = best.get(currentId);
      for (const neighborId of sorted(adjacency.get(currentId) || [])) {
        const candidate = { scopeId: current.scopeId, distance: current.distance + 1 };
        const existing = best.get(neighborId);
        const better = !existing
          || candidate.distance < existing.distance
          || (candidate.distance === existing.distance
            && this.scopeOrder.get(candidate.scopeId) < this.scopeOrder.get(existing.scopeId));
        if (!better) continue;
        best.set(neighborId, candidate);
        queue.push(neighborId);
      }
    }
    return { labels: new Map([...best].map(([id, value]) => [id, value.scopeId])), adjacency };
  }

  #scopeAngle(scopeId) {
    const scope = this.scopes.find((item) => item.id === scopeId);
    const treePerson = [...(scope?.bridgeNodes || [])]
      .map((item) => item.treePersonId)
      .filter((id) => this.topology.distance.has(id))
      .sort((left, right) => this.topology.distance.get(right) - this.topology.distance.get(left))[0];
    if (treePerson) {
      const sector = this.topology.firstHop.get(treePerson);
      return this.topology.branchAngle.get(sector) ?? angleOf(scopeId);
    }
    const index = this.scopeOrder.get(scopeId) ?? this.scopes.length;
    return -Math.PI + (Math.PI * 2 * index) / Math.max(1, this.scopes.length);
  }

  #orbital({ nodes, edges, previousPositions }) {
    const targetById = new Map();
    const metadata = new Map();
    const nodeById = new Map(nodes.map((item) => [item.id, item]));
    const treeGroups = new Map();
    const scopeNodes = [];
    const satelliteNodes = [];
    for (const node of nodes) {
      if (node.kind === "scope") {
        scopeNodes.push(node);
        continue;
      }
      const treePersonId = this.#treePersonForNode(node);
      if (treePersonId && this.topology.positions.has(treePersonId)) {
        const group = treeGroups.get(treePersonId) || [];
        group.push(node);
        treeGroups.set(treePersonId, group);
      } else satelliteNodes.push(node);
    }

    for (const [treePersonId, items] of treeGroups) {
      const personAnchor = this.topology.positions.get(treePersonId);
      const center = { x: personAnchor.x, y: personAnchor.y };
      const clusterId = `family:${personAnchor.sector}`;
      const primary = [...items].sort((left, right) => {
        const priority = { identity: 0, bridge: 1, occurrence: 2, household: 3, event: 4, source: 5 };
        return (priority[left.kind] ?? 9) - (priority[right.kind] ?? 9) || left.id.localeCompare(right.id);
      })[0];
      targetById.set(primary.id, center);
      metadata.set(primary.id, {
        ring: personAnchor.ring,
        generation: personAnchor.generation,
        sector: personAnchor.sector,
        clusterId,
        band: personAnchor.ring ? "generation" : "center",
      });
      const remaining = items.filter((item) => item !== primary);
      const occurrences = remaining.filter((item) => item.kind === "occurrence" && !item.contextualEvidenceDetail);
      const events = remaining.filter((item) => item.kind === "event");
      const evidenceDetails = remaining.filter((item) => item.contextualEvidenceDetail || item.kind === "source");
      const other = remaining.filter((item) => !occurrences.includes(item) && !events.includes(item) && !evidenceDetails.includes(item));
      placeBand(targetById, occurrences, center, 62, `${clusterId}:${treePersonId}:occ`, 54);
      placeBand(targetById, other, center, 88, `${clusterId}:${treePersonId}:other`, 58);
      placeBand(targetById, events, center, 145, `${clusterId}:${treePersonId}:events`, 66);
      placeBand(targetById, evidenceDetails, center, 220, `${clusterId}:${treePersonId}:evidence`, 72);
      for (const item of remaining) {
        const band = item.kind === "event" ? "event" : item.kind === "source" ? "source" : "occurrence";
        metadata.set(item.id, { ring: personAnchor.ring, generation: personAnchor.generation, sector: personAnchor.sector, clusterId, band });
      }
    }

    const overviewMode = scopeNodes.length > 0;
    const visibleGenerations = [...treeGroups.keys()]
      .map((id) => this.topology.positions.get(id)?.generation)
      .filter(Number.isInteger);
    const maxAncestorGeneration = Math.max(0, ...visibleGenerations.filter((value) => value > 0));
    const maxDescendantGeneration = Math.max(0, ...visibleGenerations.filter((value) => value < 0).map(Math.abs));
    const maxVisibleGeneration = Math.max(maxAncestorGeneration, maxDescendantGeneration);
    const overviewRadius = Math.max(680, (maxVisibleGeneration + 1) * RING_STEP + 180);
    const scopeNodesBySector = new Map();
    for (const item of scopeNodes) {
      const baseAngle = this.#scopeAngle(item.ref);
      const key = String(Math.round(baseAngle / .18));
      const group = scopeNodesBySector.get(key) || [];
      group.push({ item, baseAngle });
      scopeNodesBySector.set(key, group);
    }
    for (const group of scopeNodesBySector.values()) {
      group.sort((left, right) => (this.scopeOrder.get(left.item.ref) ?? Infinity) - (this.scopeOrder.get(right.item.ref) ?? Infinity));
      group.forEach(({ item, baseAngle }, index) => {
        const slot = index % 4;
        const slotCount = Math.min(4, group.length - Math.floor(index / 4) * 4);
        const angle = baseAngle + (slot - (slotCount - 1) / 2) * .32;
        const lane = Math.floor(index / 4);
        const target = polar(overviewRadius + lane * 190, angle);
        targetById.set(item.id, target);
        metadata.set(item.id, {
          ring: 1 + lane,
          sector: item.ref,
          clusterId: `scope:${item.ref}`,
          clusterLabel: item.label,
          band: "scope",
        });
      });
    }

    const { labels, adjacency } = this.#scopeLabels(nodes, edges);
    const satelliteIds = new Set(satelliteNodes.map((item) => item.id));
    const components = [];
    const visited = new Set();
    for (const start of [...satelliteNodes].sort((left, right) => left.id.localeCompare(right.id))) {
      if (visited.has(start.id)) continue;
      const scopeId = labels.get(start.id) || "unassigned";
      const queue = [start.id];
      const componentIds = [];
      visited.add(start.id);
      while (queue.length) {
        const current = queue.shift();
        componentIds.push(current);
        for (const neighbor of sorted(adjacency.get(current) || [])) {
          if (!satelliteIds.has(neighbor) || visited.has(neighbor)) continue;
          if ((labels.get(neighbor) || "unassigned") !== scopeId) continue;
          visited.add(neighbor);
          queue.push(neighbor);
        }
      }
      components.push({ id: componentIds.sort()[0], scopeId, nodes: componentIds.map((id) => nodeById.get(id)) });
    }

    const maxKinshipRing = overviewMode
      ? 0
      : Math.max(2, ...[...treeGroups.keys()].map((id) => this.topology.positions.get(id)?.ring || 0));
    const fanBaseRadius = (maxKinshipRing + 1) * RING_STEP + 180;
    const componentsByScope = new Map();
    for (const component of components) {
      const list = componentsByScope.get(component.scopeId) || [];
      list.push(component);
      componentsByScope.set(component.scopeId, list);
    }
    const scopeEntries = [...componentsByScope]
      .map(([scopeId, scopeComponents]) => ({
        scopeId,
        nodes: [...new Map(
          scopeComponents.flatMap((component) => component.nodes).map((item) => [item.id, item]),
        ).values()].sort((left, right) => left.id.localeCompare(right.id)),
        baseAngle: scopeId === "unassigned" ? Math.PI : this.#scopeAngle(scopeId),
      }))
      .sort((left, right) => {
        if (left.scopeId === "unassigned") return 1;
        if (right.scopeId === "unassigned") return -1;
        return (this.scopeOrder.get(left.scopeId) ?? Infinity) - (this.scopeOrder.get(right.scopeId) ?? Infinity)
          || left.scopeId.localeCompare(right.scopeId);
      });
    const scopesBySector = new Map();
    for (const entry of scopeEntries) {
      const sectorKey = String(Math.round(entry.baseAngle / .18));
      const group = scopesBySector.get(sectorKey) || [];
      group.push(entry);
      scopesBySector.set(sectorKey, group);
    }
    for (const scopeGroup of scopesBySector.values()) {
      scopeGroup.forEach((entry, index) => {
        const slot = index % 5;
        const slotCount = Math.min(5, scopeGroup.length - Math.floor(index / 5) * 5);
        const angle = entry.baseAngle + (slot - (slotCount - 1) / 2) * .24;
        const lane = Math.floor(index / 5);
        const radius = fanBaseRadius + lane * 560;
        const center = polar(radius, angle);
        const clusterId = `satellite:${entry.scopeId}`;
        const anchors = entry.nodes.filter((item) => item.kind === "identity" || item.kind === "household");
        const occurrences = entry.nodes.filter((item) => item.kind === "occurrence");
        const events = entry.nodes.filter((item) => item.kind === "event");
        const sources = entry.nodes.filter((item) => item.kind === "source");
        const others = entry.nodes.filter((item) => !anchors.includes(item) && !occurrences.includes(item) && !events.includes(item) && !sources.includes(item));
        placeBand(targetById, anchors, center, anchors.length > 1 ? 42 : 0, `${clusterId}:anchors`, 54);
        placeBand(targetById, occurrences, center, 105, `${clusterId}:occurrences`, 72);
        placeBand(targetById, events, center, 195, `${clusterId}:events`, 78);
        placeBand(targetById, sources, center, 280, `${clusterId}:sources`, 84);
        placeBand(targetById, others, center, 350, `${clusterId}:others`, 88);
        for (const item of entry.nodes) {
          const band = item.kind === "event" ? "event" : item.kind === "source" ? "source" : "cluster";
        const clusterLabel = this.scopes.find((scope) => scope.id === entry.scopeId)?.label
          || (entry.scopeId === "unassigned" ? "Pozostałe elementy" : entry.scopeId);
        metadata.set(item.id, { ring: maxKinshipRing + 1 + lane, sector: entry.scopeId, clusterId, clusterLabel, band });
        }
      });
    }

    this.guides = overviewMode
      ? [
        { radius: CORE_RADIUS + 34, label: "centrum rodziny", kind: "core" },
        ...Array.from({ length: maxAncestorGeneration }, (_, index) => ({
          radius: (index + 1) * RING_STEP,
          label: `pokolenie +${index + 1}`,
          kind: "ancestor",
          startAngle: Math.PI,
          endAngle: Math.PI * 2,
        })),
        ...Array.from({ length: maxDescendantGeneration }, (_, index) => ({
          radius: (index + 1) * RING_STEP,
          label: `pokolenie −${index + 1}`,
          kind: "descendant",
          startAngle: 0,
          endAngle: Math.PI,
        })),
        { radius: overviewRadius, label: "linie rodzinne", kind: "scope" },
      ]
      : [
        { radius: CORE_RADIUS + 34, label: "centrum rodziny", kind: "core" },
        ...Array.from({ length: maxAncestorGeneration }, (_, index) => ({
          radius: (index + 1) * RING_STEP,
          label: `pokolenie +${index + 1}`,
          kind: "ancestor",
          startAngle: Math.PI,
          endAngle: Math.PI * 2,
        })),
        ...Array.from({ length: maxDescendantGeneration }, (_, index) => ({
          radius: (index + 1) * RING_STEP,
          label: `pokolenie −${index + 1}`,
          kind: "descendant",
          startAngle: 0,
          endAngle: Math.PI,
        })),
        { radius: fanBaseRadius, label: "klastry FAN", kind: "fan" },
      ];

    return nodes.map((item, index) => {
      const target = targetById.get(item.id) || gridPosition(index, nodes.length, 180);
      const previous = previousPositions.get(item.id);
      const fixed = previous?.fixed === true;
      const meta = metadata.get(item.id) || { ring: maxKinshipRing + 1, sector: "unassigned", clusterId: `loose:${item.id}`, band: "cluster" };
      return {
        ...item,
        x: fixed ? previous.x : target.x,
        y: fixed ? previous.y : target.y,
        vx: 0,
        vy: 0,
        fixed,
        semanticAnchorX: target.x,
        semanticAnchorY: target.y,
        semanticBand: meta.band,
        semanticRing: meta.ring,
        semanticGeneration: Number.isInteger(meta.generation) ? meta.generation : null,
        semanticSector: meta.sector,
        semanticClusterId: meta.clusterId,
        semanticClusterLabel: meta.clusterLabel || null,
        semanticAnchorStrength: meta.band === "scope" ? .095 : meta.band === "center" ? .065 : meta.band === "generation" ? .052 : .038,
        layoutMode: "orbital",
      };
    });
  }

  #researchIslands({ nodes, edges, layoutEdges, previousPositions, mode }) {
    const nodeById = new Map(nodes.map((item) => [item.id, item]));
    const adjacency = new Map(nodes.map((item) => [item.id, new Set()]));
    const structuralKinds = new Set(["relation", "same-person", "household", "scope-link", "cluster-link", "cluster-member"]);
    for (const edge of edges) {
      if (!structuralKinds.has(edge.kind)) continue;
      if (!nodeById.has(edge.from) || !nodeById.has(edge.to)) continue;
      connect(adjacency, edge.from, edge.to);
    }
    // Uczestnicy jednego aktu tworzą wspólną wyspę tylko w obrębie tej samej
    // warstwy badawczej. Akt nie wciąga świadków i sąsiadów do rdzenia rodziny,
    // lecz świadkowie tego samego aktu nadal pozostają czytelnym klastrem FAN.
    const eventParticipants = new Map();
    for (const edge of edges.filter((item) => item.kind === "event-link" || item.kind === "participation")) {
      const eventNodeId = edge.kind === "participation"
        ? [edge.from, edge.to].find((id) => nodeById.get(id)?.kind === "event")
        : null;
      const occurrenceIds = edge.kind === "participation"
        ? [edge.from, edge.to].filter((id) => nodeById.get(id)?.kind === "occurrence" || nodeById.get(id)?.kind === "identity")
        : [edge.from, edge.to];
      const eventId = edge.item?.id || eventNodeId || edge.id;
      const participants = eventParticipants.get(eventId) || new Set();
      occurrenceIds.forEach((id) => participants.add(id));
      eventParticipants.set(eventId, participants);
    }
    const isCoreLayer = (node) => node?.kind === "identity"
      || node?.kind === "bridge"
      || node?.visibilityRole === "core"
      || node?.visibilityRole === "bridge";
    for (const participantIds of eventParticipants.values()) {
      const layers = new Map([["core", []], ["fan", []]]);
      for (const id of sorted(participantIds)) {
        const node = nodeById.get(id);
        if (!node) continue;
        layers.get(isCoreLayer(node) ? "core" : "fan").push(id);
      }
      for (const ids of layers.values()) {
        for (let index = 1; index < ids.length; index += 1) connect(adjacency, ids[0], ids[index]);
      }
    }
    const visited = new Set();
    const components = [];
    const structuralNodes = nodes.filter((item) => item.kind !== "event" && item.kind !== "source");
    for (const start of [...structuralNodes].sort((left, right) => left.id.localeCompare(right.id))) {
      if (visited.has(start.id)) continue;
      const queue = [start.id];
      const componentIds = [];
      visited.add(start.id);
      while (queue.length) {
        const current = queue.shift();
        componentIds.push(current);
        for (const neighbor of sorted(adjacency.get(current) || [])) {
          if (visited.has(neighbor)) continue;
          visited.add(neighbor);
          queue.push(neighbor);
        }
      }
      const componentNodes = componentIds.map((id) => nodeById.get(id));
      const priority = componentNodes.some((item) => item.visibilityRole === "core" || item.kind === "bridge") ? 0
        : componentNodes.some((item) => item.visibilityRole === "fan-direct") ? 1
        : componentNodes.some((item) => item.visibilityRole === "candidate") ? 3 : 2;
      components.push({ id: componentIds.sort()[0], nodes: componentNodes, priority });
    }

    // Dokument dołącza do najbardziej istotnej wyspy jednego z uczestników.
    // Nie scala przez to wszystkich osób wymienionych w akcie w jeden potworny
    // komponent, ale wszystkie krawędzie dowodowe nadal pozostają widoczne.
    const componentByNodeId = new Map();
    components.forEach((component) => component.nodes.forEach((item) => componentByNodeId.set(item.id, component)));
    const evidenceNodes = nodes
      .filter((item) => item.kind === "event" || item.kind === "source")
      .sort((left, right) => left.id.localeCompare(right.id));
    for (const evidenceNode of evidenceNodes) {
      const candidates = edges
        .filter((edge) => edge.from === evidenceNode.id || edge.to === evidenceNode.id)
        .map((edge) => componentByNodeId.get(edge.from === evidenceNode.id ? edge.to : edge.from))
        .filter(Boolean)
        .sort((left, right) => left.priority - right.priority || right.nodes.length - left.nodes.length || left.id.localeCompare(right.id));
      const component = candidates[0] || {
        id: evidenceNode.id,
        nodes: [],
        priority: evidenceNode.kind === "event" ? 1 : 2,
      };
      if (!candidates.length) components.push(component);
      component.nodes.push(evidenceNode);
      componentByNodeId.set(evidenceNode.id, component);
    }
    components.sort((left, right) => left.priority - right.priority || right.nodes.length - left.nodes.length || left.id.localeCompare(right.id));

    const targetById = new Map();
    const metadata = new Map();
    const spacing = mode === "clusters" ? 300 : mode === "people" ? 300 : 680;
    components.forEach((component, index) => {
      const center = gridPosition(index, components.length, spacing);
      const clusterId = `island:${component.id}`;
      const anchors = component.nodes.filter((item) => item.kind === "identity" || item.kind === "household" || item.kind === "bridge" || item.kind === "cluster");
      const occurrences = component.nodes.filter((item) => item.kind === "occurrence");
      const events = component.nodes.filter((item) => item.kind === "event");
      const sources = component.nodes.filter((item) => item.kind === "source");
      const others = component.nodes.filter((item) => !anchors.includes(item) && !occurrences.includes(item) && !events.includes(item) && !sources.includes(item));
      const labelNode = anchors.find((item) => item.kind === "household")
        || anchors.find((item) => item.kind === "identity")
        || occurrences[0]
        || component.nodes[0];
      const clusterLabel = component.nodes.length === 1 && component.nodes[0].visibilityRole === "candidate"
        ? "Nierozstrzygnięta kandydatura"
        : labelNode?.label || `Klaster ${index + 1}`;
      placeBand(targetById, anchors, center, anchors.length > 1 ? 54 : 0, `${clusterId}:anchors`, 62);
      placeBand(targetById, occurrences, center, 125, `${clusterId}:occurrences`, 78);
      placeBand(targetById, events, center, 225, `${clusterId}:events`, 84);
      placeBand(targetById, sources, center, 325, `${clusterId}:sources`, 90);
      placeBand(targetById, others, center, 405, `${clusterId}:others`, 96);
      for (const item of component.nodes) {
        metadata.set(item.id, {
          clusterId,
          clusterLabel,
          band: item.kind === "source" ? "source" : item.kind === "event" ? "event" : item.kind === "occurrence" ? "occurrence" : "island",
        });
      }
    });
    this.guides = [];
    return nodes.map((item, index) => {
      const target = targetById.get(item.id) || gridPosition(index, nodes.length, 180);
      const previous = previousPositions.get(item.id);
      const fixed = previous?.fixed === true;
      const meta = metadata.get(item.id) || { clusterId: `island:${item.id}`, clusterLabel: item.label, band: "island" };
      return {
        ...item,
        x: fixed ? previous.x : target.x,
        y: fixed ? previous.y : target.y,
        vx: 0,
        vy: 0,
        fixed,
        semanticAnchorX: target.x,
        semanticAnchorY: target.y,
        semanticBand: meta.band,
        semanticRing: null,
        semanticGeneration: null,
        semanticSector: null,
        semanticClusterId: meta.clusterId,
        semanticClusterLabel: meta.clusterLabel,
        semanticAnchorStrength: mode === "people" ? .008 : .055,
        layoutMode: "islands",
      };
    });
  }

  #layered({ nodes, edges, previousPositions, mode }) {
    const nodeById = new Map(nodes.map((item) => [item.id, item]));
    const adjacency = new Map();
    for (const edge of edges) connect(adjacency, edge.from, edge.to);
    const anchors = nodes.filter((item) => item.kind === "identity" || item.kind === "household" || item.kind === "bridge").sort((left, right) => left.id.localeCompare(right.id));
    const targetById = new Map();
    anchors.forEach((item, index) => targetById.set(item.id, gridPosition(index, anchors.length, mode === "evidence" ? 330 : 250)));
    const occurrences = nodes.filter((item) => item.kind === "occurrence" && !item.contextualEvidenceDetail).sort((a, b) => a.id.localeCompare(b.id));
    const occurrencesByAnchor = new Map();
    for (const item of occurrences) {
      const anchorId = sorted(adjacency.get(item.id) || []).map((id) => nodeById.get(id)).find((neighbor) => neighbor?.kind === "identity" || neighbor?.kind === "household")?.id;
      if (!anchorId) continue;
      const group = occurrencesByAnchor.get(anchorId) || [];
      group.push(item);
      occurrencesByAnchor.set(anchorId, group);
    }
    for (const [anchorId, items] of occurrencesByAnchor) {
      const anchor = targetById.get(anchorId) || { x: 0, y: 0 };
      items.forEach((item, index) => targetById.set(item.id, orbit(anchor, item.id, index, items.length, mode === "evidence" ? 92 : 78)));
    }
    let looseIndex = 0;
    for (const item of occurrences) {
      if (targetById.has(item.id)) continue;
      const angle = angleOf(item.id) + looseIndex * 2.399963229728653;
      const radius = 175 + Math.sqrt(looseIndex + 1) * 62;
      targetById.set(item.id, polar(radius, angle));
      looseIndex += 1;
    }
    const events = nodes.filter((item) => item.kind === "event").sort((a, b) => a.id.localeCompare(b.id));
    for (const item of events) {
      const neighbors = sorted(adjacency.get(item.id) || []).map((id) => targetById.get(id)).filter(Boolean);
      const center = average(neighbors) || { x: 0, y: 0 };
      const angle = Math.hypot(center.x, center.y) > 10 ? Math.atan2(center.y, center.x) : angleOf(item.id);
      targetById.set(item.id, { x: center.x + Math.cos(angle) * 135, y: center.y + Math.sin(angle) * 135 });
    }
    const detailOccurrences = nodes.filter((item) => item.kind === "occurrence" && item.contextualEvidenceDetail).sort((a, b) => a.id.localeCompare(b.id));
    detailOccurrences.forEach((item, index) => {
      const eventId = sorted(adjacency.get(item.id) || []).map((id) => nodeById.get(id)).find((neighbor) => neighbor?.kind === "event")?.id;
      targetById.set(item.id, orbit(targetById.get(eventId) || { x: 0, y: 0 }, item.id, index, detailOccurrences.length, 68));
    });
    const sources = nodes.filter((item) => item.kind === "source").sort((a, b) => a.id.localeCompare(b.id));
    sources.forEach((item, index) => {
      const center = average(sorted(adjacency.get(item.id) || []).map((id) => targetById.get(id)).filter(Boolean)) || { x: 0, y: 0 };
      const angle = Math.hypot(center.x, center.y) > 10 ? Math.atan2(center.y, center.x) : angleOf(item.id);
      targetById.set(item.id, { x: center.x + Math.cos(angle + (index % 3 - 1) * .18) * 115, y: center.y + Math.sin(angle + (index % 3 - 1) * .18) * 115 });
    });
    this.guides = [];
    return nodes.map((item, index) => {
      const target = targetById.get(item.id) || gridPosition(index, nodes.length, 180);
      const previous = previousPositions.get(item.id);
      const fixed = previous?.fixed === true;
      return {
        ...item,
        x: fixed ? previous.x : (previous?.x ?? target.x),
        y: fixed ? previous.y : (previous?.y ?? target.y),
        vx: 0,
        vy: 0,
        fixed,
        semanticAnchorX: target.x,
        semanticAnchorY: target.y,
        semanticBand: item.kind === "identity" || item.kind === "household" ? "center" : item.kind === "occurrence" ? "occurrence" : item.kind === "event" ? "event" : item.kind === "source" ? "source" : "context",
        semanticClusterId: "legacy",
        semanticAnchorStrength: .012,
        layoutMode: "layered",
      };
    });
  }
}
