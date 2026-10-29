function centerX(position, cardWidth) {
  return position.x + cardWidth / 2;
}

/**
 * Builds a downward parent -> child connector for the vertical ancestor view.
 */
export function ancestorConnectorGeometry({
  edge,
  positions,
  cardWidth,
  cardHeight,
}) {
  const parentSlotId = edge?.parentSlotId ?? edge?.fromSlotId;
  const childSlotId = edge?.childSlotId ?? edge?.toSlotId;
  const parentPosition = positions?.get(parentSlotId);
  const childPosition = positions?.get(childSlotId);
  const width = Number(cardWidth);
  const height = Number(cardHeight);
  if (!parentPosition || !childPosition || !Number.isFinite(width) || !Number.isFinite(height)) return null;

  const parent = {
    slotId: parentSlotId,
    x: centerX(parentPosition, width),
    y: parentPosition.y + height,
  };
  const child = {
    slotId: childSlotId,
    x: centerX(childPosition, width),
    y: childPosition.y,
  };
  const controlY = parent.y + (child.y - parent.y) / 2;
  return {
    edgeId: edge.edgeId ?? null,
    parent,
    child,
    controlY,
    path: `M ${parent.x} ${parent.y} V ${controlY} H ${child.x} V ${child.y}`,
  };
}

export function ancestorConnectorsGeometry({ edges, positions, cardWidth, cardHeight }) {
  return (Array.isArray(edges) ? edges : [])
    .map((edge) => ancestorConnectorGeometry({ edge, positions, cardWidth, cardHeight }))
    .filter(Boolean);
}
