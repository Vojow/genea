const SVG_NAMESPACE = "http://www.w3.org/2000/svg";

function compareText(first, second) {
  if (first < second) return -1;
  if (first > second) return 1;
  return 0;
}

function qualifiedConditionalEdge(edge) {
  return Boolean(
    edge
    && typeof edge.id === "string"
    && edge.id.length
    && typeof edge.fromDisplayId === "string"
    && edge.fromDisplayId.length
    && typeof edge.toDisplayId === "string"
    && edge.toDisplayId.length
    && typeof edge.status === "string"
    && edge.status.length
    && typeof edge.relationKind === "string"
    && edge.relationKind.length
    && Array.isArray(edge.sourceIds)
    && edge.sourceIds.every((sourceId) => typeof sourceId === "string" && sourceId.length)
    && edge.canonicalRelationship === false
    && edge.draw !== false
  );
}

function edgeOrder(first, second) {
  return compareText(first.id, second.id)
    || compareText(first.fromDisplayId, second.fromDisplayId)
    || compareText(first.toDisplayId, second.toDisplayId);
}

function normalizedNumber(value) {
  const rounded = Math.round(value * 1000) / 1000;
  return Object.is(rounded, -0) ? 0 : rounded;
}

function verticalOverlap(start, end, top, bottom) {
  return Math.max(Math.min(start, end), top) <= Math.min(Math.max(start, end), bottom);
}

function horizontalOverlap(start, end, left, right) {
  return Math.max(Math.min(start, end), left) <= Math.min(Math.max(start, end), right);
}

function railPenalty({ railY, fromX, fromY, toX, toY, edge, obstacles, usedRails }) {
  let penalty = usedRails.reduce((total, usedRail) => (
    total + (Math.abs(usedRail - railY) < 6 ? 30 : 0)
  ), 0);
  for (const { displayId, left, right, top, bottom } of obstacles) {
    if (displayId === edge.fromDisplayId || displayId === edge.toDisplayId) continue;
    if (railY >= top && railY <= bottom && horizontalOverlap(fromX, toX, left, right)) penalty += 100;
    if (fromX >= left && fromX <= right && verticalOverlap(fromY, railY, top, bottom)) penalty += 10;
    if (toX >= left && toX <= right && verticalOverlap(railY, toY, top, bottom)) penalty += 10;
  }
  return penalty;
}

function railCandidates(fromY, toY, fromX, toX, preferredRail, obstacles) {
  const minimum = Math.min(fromY, toY);
  const maximum = Math.max(fromY, toY);
  const gap = maximum - minimum;
  if (!gap) return [preferredRail];
  const inset = Math.min(4, gap / 4);
  const candidates = new Set([
    preferredRail,
    minimum + gap * .25,
    minimum + gap * .5,
    minimum + gap * .75,
  ]);
  for (const { left, right, top, bottom } of obstacles) {
    const crossesHorizontalSpan = horizontalOverlap(fromX, toX, left, right);
    const touchesVerticalLeg = (fromX >= left && fromX <= right) || (toX >= left && toX <= right);
    if (!crossesHorizontalSpan && !touchesVerticalLeg) continue;
    const before = top - inset;
    const after = bottom + inset;
    if (before > minimum && before < maximum) candidates.add(before);
    if (after > minimum && after < maximum) candidates.add(after);
  }
  return [...candidates]
    .map((candidate) => Math.max(minimum + inset, Math.min(maximum - inset, candidate)))
    .sort((first, second) => (
      Math.abs(first - preferredRail) - Math.abs(second - preferredRail)
      || first - second
    ))
    .slice(0, 32);
}

function edgePorts(edge, positions, cardWidth, cardHeight) {
  const from = positions.get(edge.fromDisplayId);
  const to = positions.get(edge.toDisplayId);
  if (!from || !to) return null;
  const fromX = from.x + cardWidth / 2;
  const toX = to.x + cardWidth / 2;
  if (to.y < from.y) {
    return { fromX, fromY: from.y, toX, toY: to.y + cardHeight };
  }
  if (to.y > from.y) {
    return { fromX, fromY: from.y + cardHeight, toX, toY: to.y };
  }
  return {
    fromX,
    fromY: from.y + cardHeight / 2,
    toX,
    toY: to.y + cardHeight / 2,
  };
}

function titleFor(edge) {
  return edge.relationKind === "source_stated"
    ? "Wskazanie rodzica ze źródła · relacja nierozstrzygnięta"
    : "Hipoteza rodzicielstwa ze źródeł · relacja nierozstrzygnięta";
}

export function appendUnifiedAncestryEdges({
  svg,
  edges = [],
  positions,
  cardWidth,
  cardHeight,
} = {}) {
  if (!svg?.append || !positions?.get || !positions?.entries || !positions?.values) return [];
  if (!Number.isFinite(cardWidth) || cardWidth <= 0 || !Number.isFinite(cardHeight) || cardHeight <= 0) return [];

  const prepared = edges
    .filter(qualifiedConditionalEdge)
    .map((edge) => ({ edge, ports: edgePorts(edge, positions, cardWidth, cardHeight) }))
    .filter(({ ports }) => ports)
    .sort((first, second) => edgeOrder(first.edge, second.edge));
  const railGroups = new Map();
  for (const item of prepared) {
    const { fromY, toY } = item.ports;
    const key = `${normalizedNumber(Math.min(fromY, toY))}:${normalizedNumber(Math.max(fromY, toY))}`;
    const group = railGroups.get(key) || [];
    group.push(item);
    railGroups.set(key, group);
  }

  const documentRef = svg.ownerDocument || globalThis.document;
  if (!documentRef?.createElementNS) return [];
  const obstacles = [...positions.entries()]
    .sort(([firstId], [secondId]) => compareText(firstId, secondId))
    .map(([displayId, position]) => ({
      displayId,
      left: position.x,
      right: position.x + cardWidth,
      top: position.y,
      bottom: position.y + cardHeight,
    }));
  const paths = [];
  for (const group of railGroups.values()) {
    const usedRails = [];
    group.forEach(({ edge, ports }, index) => {
      const { fromX, fromY, toX, toY } = ports;
      const gap = Math.abs(toY - fromY);
      const spacing = Math.min(12, gap / (group.length + 1));
      const preferredRail = (fromY + toY) / 2 + (index - (group.length - 1) / 2) * spacing;
      const railY = railCandidates(fromY, toY, fromX, toX, preferredRail, obstacles)
        .map((candidate) => ({
          candidate,
          penalty: railPenalty({
            railY: candidate,
            fromX,
            fromY,
            toX,
            toY,
            edge,
            obstacles,
            usedRails,
          }),
        }))
        .sort((first, second) => (
          first.penalty - second.penalty
          || Math.abs(first.candidate - preferredRail) - Math.abs(second.candidate - preferredRail)
          || first.candidate - second.candidate
        ))[0].candidate;
      usedRails.push(railY);

      const path = documentRef.createElementNS(SVG_NAMESPACE, "path");
      const geometry = fromY === toY
        ? `M ${normalizedNumber(fromX)} ${normalizedNumber(fromY)} H ${normalizedNumber(toX)}`
        : `M ${normalizedNumber(fromX)} ${normalizedNumber(fromY)} V ${normalizedNumber(railY)} H ${normalizedNumber(toX)} V ${normalizedNumber(toY)}`;
      path.setAttribute("d", geometry);
      path.setAttribute("class", "tree-link tree-unified-ancestry-link is-conditional is-unresolved");
      path.setAttribute("data-unified-edge-id", edge.id);
      path.setAttribute("data-from-display-id", edge.fromDisplayId);
      path.setAttribute("data-to-display-id", edge.toDisplayId);
      path.setAttribute("data-relationship-status", edge.status);
      path.setAttribute("data-relation-kind", edge.relationKind);
      path.setAttribute("data-source-ids", edge.sourceIds.join(" "));
      path.setAttribute("data-canonical-relationship", "false");
      path.setAttribute("data-direction", "child_to_parent");
      const title = documentRef.createElementNS(SVG_NAMESPACE, "title");
      title.textContent = titleFor(edge);
      path.append(title);
      paths.push(path);
    });
  }
  svg.append(...paths);
  return paths;
}
