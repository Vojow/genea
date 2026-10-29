const EDGE_FORCE_PROFILES = Object.freeze({
  "same-person": Object.freeze({ target: 42, strength: .006, crossCluster: false }),
  candidate: Object.freeze({ target: 92, strength: .012, crossCluster: true }),
  "co-occurrence-proximity": Object.freeze({ target: 176, strength: .0022, crossCluster: true }),
  participation: Object.freeze({ target: 118, strength: .0025, crossCluster: false }),
  source: Object.freeze({ target: 126, strength: .0025, crossCluster: false }),
  "event-link": Object.freeze({ target: 104, strength: .0025, crossCluster: false }),
  household: Object.freeze({ target: 92, strength: .0025, crossCluster: false }),
});

const DEFAULT_EDGE_FORCE_PROFILE = Object.freeze({ target: 78, strength: .0025, crossCluster: false });

export function forceProfileForEdge(edge) {
  return EDGE_FORCE_PROFILES[edge?.kind] || DEFAULT_EDGE_FORCE_PROFILE;
}

export function runForceLayout({
  nodes,
  edges,
  nodeById,
  onTick,
  maxTicks = 260,
  schedule = requestAnimationFrame,
  cancel = cancelAnimationFrame,
}) {
  let frame = null;
  let tick = 0;
  const step = () => {
    const count = nodes.length;
    for (let first = 0; first < count; first += 1) {
      const a = nodes[first];
      for (let second = first + 1; second < count; second += 1) {
        const b = nodes[second];
        if (a.semanticClusterId && b.semanticClusterId && a.semanticClusterId !== b.semanticClusterId) continue;
        let dx = a.x - b.x;
        let dy = a.y - b.y;
        const distanceSquared = dx * dx + dy * dy + 40;
        const force = Math.min(2.2, 1100 / distanceSquared);
        const distance = Math.sqrt(distanceSquared);
        dx /= distance;
        dy /= distance;
        a.vx += dx * force;
        a.vy += dy * force;
        b.vx -= dx * force;
        b.vy -= dy * force;
      }
    }
    for (const edge of edges) {
      const a = nodeById.get(edge.from);
      const b = nodeById.get(edge.to);
      if (!a || !b) continue;
      const profile = forceProfileForEdge(edge);
      if (!profile.crossCluster && a.semanticClusterId && b.semanticClusterId && a.semanticClusterId !== b.semanticClusterId) continue;
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const distance = Math.max(1, Math.hypot(dx, dy));
      const force = (distance - profile.target) * profile.strength;
      a.vx += dx / distance * force;
      a.vy += dy / distance * force;
      b.vx -= dx / distance * force;
      b.vy -= dy / distance * force;
    }
    for (const item of nodes) {
      if (item.fixed) continue;
      const anchorStrength = item.semanticAnchorStrength
        ?? (item.semanticBand === "center" ? .012 : .008);
      item.vx += ((item.semanticAnchorX ?? 0) - item.x) * anchorStrength;
      item.vy += ((item.semanticAnchorY ?? 0) - item.y) * anchorStrength;
      item.vx *= item.layoutMode === "orbital" ? .76 : item.layoutMode === "islands" ? .78 : .84;
      item.vy *= item.layoutMode === "orbital" ? .76 : item.layoutMode === "islands" ? .78 : .84;
      item.x += item.vx;
      item.y += item.vy;
    }
    onTick();
    tick += 1;
    if (tick < maxTicks) frame = schedule(step);
  };
  frame = schedule(step);
  return () => { if (frame != null) cancel(frame); };
}
