function center(position, cardWidth) {
  return position.x + cardWidth / 2;
}

export function familyConnectorGeometry({
  family,
  visibleIds,
  positions,
  cardWidth,
  cardHeight,
}) {
  const parents = (family?.parentIds || [])
    .filter((id) => visibleIds.has(id))
    .map((id) => ({ id, position: positions.get(id) }))
    .filter((item) => item.position)
    .map((item) => ({ id: item.id, x: center(item.position, cardWidth), y: item.position.y }));
  const children = (family?.childIds || [])
    .filter((id) => visibleIds.has(id))
    .map((id) => ({ id, position: positions.get(id) }))
    .filter((item) => item.position)
    .map((item) => ({ id: item.id, x: center(item.position, cardWidth), y: item.position.y + cardHeight }));
  if (!parents.length || !children.length) return null;

  const childEdgeY = Math.max(...children.map((item) => item.y));
  const parentEdgeY = Math.min(...parents.map((item) => item.y));
  const available = Math.max(36, parentEdgeY - childEdgeY);
  const childBusY = childEdgeY + Math.max(14, available * .26);
  const parentBusY = parentEdgeY - Math.max(14, available * .26);
  const middleY = (childBusY + parentBusY) / 2;
  const childCenterX = children.reduce((sum, item) => sum + item.x, 0) / children.length;
  const parentCenterX = parents.reduce((sum, item) => sum + item.x, 0) / parents.length;
  const commands = [];

  for (const child of children) commands.push(`M ${child.x} ${child.y} V ${childBusY}`);
  if (children.length > 1) {
    commands.push(`M ${Math.min(...children.map((item) => item.x))} ${childBusY} H ${Math.max(...children.map((item) => item.x))}`);
  }
  commands.push(`M ${childCenterX} ${childBusY} V ${middleY} H ${parentCenterX} V ${parentBusY}`);
  if (parents.length > 1) {
    commands.push(`M ${Math.min(...parents.map((item) => item.x))} ${parentBusY} H ${Math.max(...parents.map((item) => item.x))}`);
  }
  for (const parent of parents) commands.push(`M ${parent.x} ${parentBusY} V ${parent.y}`);

  return {
    parents,
    children,
    childBusY,
    parentBusY,
    controlX: (childCenterX + parentCenterX) / 2,
    controlY: middleY,
    path: commands.join(" "),
  };
}
