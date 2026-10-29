const HALF_TURN = 180;
const MIN_TARGET_SIZE = 44;
const DEFAULT_INNER_RADIUS = 58;
const DEFAULT_RING_WIDTH = 76;
const DEFAULT_PADDING = 48;
const DEFAULT_GAP_ANGLE = 1.2;

function finiteNumber(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function pointOnArc(center, radius, angle) {
  const radians = angle * Math.PI / 180;
  return {
    x: center.x + radius * Math.cos(radians),
    y: center.y + radius * Math.sin(radians),
  };
}

function rounded(value) {
  return Math.round(value * 1000) / 1000;
}

function pointText(point) {
  return `${rounded(point.x)} ${rounded(point.y)}`;
}

function sectorPath(center, innerRadius, outerRadius, startAngle, endAngle) {
  const innerStart = pointOnArc(center, innerRadius, startAngle);
  const outerStart = pointOnArc(center, outerRadius, startAngle);
  const outerEnd = pointOnArc(center, outerRadius, endAngle);
  const innerEnd = pointOnArc(center, innerRadius, endAngle);
  const largeArc = endAngle - startAngle > 180 ? 1 : 0;
  return [
    `M ${pointText(innerStart)}`,
    `L ${pointText(outerStart)}`,
    `A ${rounded(outerRadius)} ${rounded(outerRadius)} 0 ${largeArc} 1 ${pointText(outerEnd)}`,
    `L ${pointText(innerEnd)}`,
    `A ${rounded(innerRadius)} ${rounded(innerRadius)} 0 ${largeArc} 0 ${pointText(innerStart)}`,
    "Z",
  ].join(" ");
}

function circlePath(center, radius) {
  const left = { x: center.x - radius, y: center.y };
  const right = { x: center.x + radius, y: center.y };
  return [
    `M ${pointText(left)}`,
    `A ${radius} ${radius} 0 1 1 ${pointText(right)}`,
    `A ${radius} ${radius} 0 1 1 ${pointText(left)}`,
    "Z",
  ].join(" ");
}

function slotName(slot) {
  return String(slot?.person?.name || slot?.label || "Nieustalony przodek").trim();
}

function readableRadialRotation(angle) {
  return angle < -90 ? angle + 180 : angle;
}

export function ancestorFanLabelPolicy({ radialWidth, tangentialWidth, focus = false } = {}) {
  const safeRadialWidth = Math.max(0, finiteNumber(radialWidth, 0));
  const safeTangentialWidth = Math.max(0, finiteNumber(tangentialWidth, 0));
  const fontSize = focus ? 11 : safeTangentialWidth < 54 ? 9 : safeTangentialWidth < 72 ? 10 : 11;
  const horizontalPadding = focus ? 22 : 16;
  const estimatedCharacterWidth = fontSize * .58;
  const maximumLength = Math.max(7, Math.min(30, Math.floor(
    Math.max(0, safeRadialWidth - horizontalPadding) / estimatedCharacterWidth,
  )));
  return {
    fontSize,
    maximumLength,
    showLifeSpan: focus && safeTangentialWidth >= 64 && safeRadialWidth >= 92,
  };
}

export function shortenAncestorLabel(value, maximumLength = 24) {
  const label = String(value || "Nieustalony przodek").trim();
  const limit = Math.max(7, Math.floor(finiteNumber(maximumLength, 24)));
  if (label.length <= limit) return label;
  const available = limit - 1;
  const beginning = Math.ceil(available * .56);
  return `${label.slice(0, beginning).trimEnd()}…${label.slice(-(available - beginning)).trimStart()}`;
}

function normalizedSlots(slots) {
  return (slots || []).map((slot, sourceIndex) => ({
    ...slot,
    slotId: String(slot?.slotId || `ancestor-slot-${sourceIndex}`),
    personId: slot?.personId || slot?.person?.id || null,
    depth: Math.max(0, Math.floor(finiteNumber(slot?.depth, 0))),
    sourceIndex,
  }));
}

function groupByDepth(slots) {
  const grouped = new Map();
  slots.forEach((slot) => {
    const group = grouped.get(slot.depth) || [];
    group.push(slot);
    grouped.set(slot.depth, group);
  });
  grouped.forEach((group) => group.sort((first, second) => (
    first.sourceIndex - second.sourceIndex || first.slotId.localeCompare(second.slotId, "pl")
  )));
  return grouped;
}

function requiredRingWidth(grouped, maxDepth, innerRadius, requestedRingWidth, targetSize, gapAngle) {
  let ringWidth = Math.max(targetSize, requestedRingWidth);
  if (maxDepth < 2) return ringWidth;
  for (let depth = 2; depth <= maxDepth; depth += 1) {
    const sectorCount = Math.max(1, (grouped.get(depth) || []).length);
    const angleStep = HALF_TURN / sectorCount;
    const appliedGap = Math.min(Math.max(0, gapAngle), angleStep * .18);
    const availableAngle = (angleStep - appliedGap) * Math.PI / 180;
    const requiredInnerRadius = targetSize / availableAngle;
    ringWidth = Math.max(ringWidth, (requiredInnerRadius - innerRadius) / (depth - 1));
  }
  return ringWidth;
}

function normalizeEdge(edge, sourceIndex) {
  return {
    ...edge,
    edgeId: String(edge?.edgeId || edge?.id || `ancestor-edge-${sourceIndex}`),
    parentSlotId: edge?.parentSlotId || edge?.toSlotId || edge?.targetSlotId || null,
    childSlotId: edge?.childSlotId || edge?.fromSlotId || edge?.sourceSlotId || null,
    sourceIds: [...(edge?.sourceIds || [])],
  };
}

export function computeAncestorFanLayout({
  slots = [],
  edges = [],
  width = 0,
  height = 0,
  innerRadius = DEFAULT_INNER_RADIUS,
  ringWidth = DEFAULT_RING_WIDTH,
  padding = DEFAULT_PADDING,
  gapAngle = DEFAULT_GAP_ANGLE,
  minimumTargetSize = MIN_TARGET_SIZE,
} = {}) {
  const safeSlots = normalizedSlots(slots);
  const grouped = groupByDepth(safeSlots);
  const maxDepth = safeSlots.length ? Math.max(...safeSlots.map((slot) => slot.depth)) : 0;
  const safeTargetSize = Math.max(MIN_TARGET_SIZE, finiteNumber(minimumTargetSize, MIN_TARGET_SIZE));
  const safeInnerRadius = Math.max(safeTargetSize / 2, finiteNumber(innerRadius, DEFAULT_INNER_RADIUS));
  const safeGapAngle = Math.max(0, finiteNumber(gapAngle, DEFAULT_GAP_ANGLE));
  const safeRingWidth = requiredRingWidth(
    grouped,
    maxDepth,
    safeInnerRadius,
    finiteNumber(ringWidth, DEFAULT_RING_WIDTH),
    safeTargetSize,
    safeGapAngle,
  );
  const outerRadius = maxDepth
    ? safeInnerRadius + maxDepth * safeRingWidth
    : safeInnerRadius;
  const safePadding = Math.max(0, finiteNumber(padding, DEFAULT_PADDING));
  const naturalWidth = outerRadius * 2 + safePadding * 2;
  const naturalHeight = outerRadius + safeInnerRadius + safePadding * 2;
  const layoutWidth = Math.max(naturalWidth, finiteNumber(width, 0));
  const layoutHeight = Math.max(naturalHeight, finiteNumber(height, 0));
  const center = {
    x: layoutWidth / 2,
    y: safePadding + outerRadius,
  };
  const sectors = [];
  const rings = [];

  const focusSlots = grouped.get(0) || [];
  focusSlots.forEach((slot, index) => {
    const offset = (index - (focusSlots.length - 1) / 2) * safeTargetSize;
    const slotCenter = { x: center.x + offset, y: center.y };
    const labelPolicy = ancestorFanLabelPolicy({
      radialWidth: safeInnerRadius * 2,
      tangentialWidth: safeInnerRadius * 2,
      focus: true,
    });
    sectors.push({
      ...slot,
      kind: "focus",
      sectorIndex: index,
      sectorCount: focusSlots.length,
      innerRadius: 0,
      outerRadius: safeInnerRadius,
      startAngle: -180,
      endAngle: 180,
      center: slotCenter,
      path: circlePath(slotCenter, safeInnerRadius),
      fullLabel: slotName(slot),
      label: shortenAncestorLabel(slotName(slot), labelPolicy.maximumLength),
      labelFontSize: labelPolicy.fontSize,
      labelMaximumLength: labelPolicy.maximumLength,
      showLifeSpan: labelPolicy.showLifeSpan,
      minimumTargetWidth: safeInnerRadius * 2,
      minimumTargetHeight: safeInnerRadius * 2,
    });
  });

  for (let depth = 1; depth <= maxDepth; depth += 1) {
    const depthSlots = grouped.get(depth) || [];
    const sectorCount = Math.max(1, depthSlots.length);
    const angleStep = HALF_TURN / sectorCount;
    const appliedGap = Math.min(safeGapAngle, angleStep * .18);
    const ringInnerRadius = safeInnerRadius + (depth - 1) * safeRingWidth;
    const ringOuterRadius = ringInnerRadius + safeRingWidth;
    rings.push({
      depth,
      innerRadius: ringInnerRadius,
      outerRadius: ringOuterRadius,
      sectorCount,
    });
    depthSlots.forEach((slot, sectorIndex) => {
      const startAngle = -180 + sectorIndex * angleStep + appliedGap / 2;
      const endAngle = -180 + (sectorIndex + 1) * angleStep - appliedGap / 2;
      const labelAngle = (startAngle + endAngle) / 2;
      const labelRadius = (ringInnerRadius + ringOuterRadius) / 2;
      const slotCenter = pointOnArc(center, labelRadius, labelAngle);
      const tangentialWidth = ringInnerRadius * (endAngle - startAngle) * Math.PI / 180;
      const labelPolicy = ancestorFanLabelPolicy({
        radialWidth: safeRingWidth,
        tangentialWidth,
      });
      sectors.push({
        ...slot,
        kind: "ancestor",
        sectorIndex,
        sectorCount,
        innerRadius: ringInnerRadius,
        outerRadius: ringOuterRadius,
        startAngle,
        endAngle,
        center: slotCenter,
        path: sectorPath(center, ringInnerRadius, ringOuterRadius, startAngle, endAngle),
        fullLabel: slotName(slot),
        label: shortenAncestorLabel(slotName(slot), labelPolicy.maximumLength),
        labelFontSize: labelPolicy.fontSize,
        labelMaximumLength: labelPolicy.maximumLength,
        showLifeSpan: labelPolicy.showLifeSpan,
        labelRotation: readableRadialRotation(labelAngle),
        minimumTargetWidth: tangentialWidth,
        minimumTargetHeight: safeRingWidth,
      });
    });
  }

  const sectorById = new Map(sectors.map((sector) => [sector.slotId, sector]));
  const laidOutEdges = (edges || []).map(normalizeEdge).map((edge) => {
    const parent = sectorById.get(String(edge.parentSlotId));
    const child = sectorById.get(String(edge.childSlotId));
    if (!parent || !child) return { ...edge, path: null };
    const control = {
      x: (parent.center.x + child.center.x) / 2,
      y: Math.min(parent.center.y, child.center.y) + Math.abs(parent.center.y - child.center.y) * .22,
    };
    return {
      ...edge,
      path: `M ${pointText(child.center)} Q ${pointText(control)} ${pointText(parent.center)}`,
    };
  });

  return {
    width: rounded(layoutWidth),
    height: rounded(layoutHeight),
    naturalWidth: rounded(naturalWidth),
    naturalHeight: rounded(naturalHeight),
    viewBox: `0 0 ${rounded(layoutWidth)} ${rounded(layoutHeight)}`,
    center: { x: rounded(center.x), y: rounded(center.y) },
    innerRadius: rounded(safeInnerRadius),
    ringWidth: rounded(safeRingWidth),
    outerRadius: rounded(outerRadius),
    minimumTargetSize: safeTargetSize,
    maxDepth,
    rings,
    sectors,
    edges: laidOutEdges,
  };
}

export class AncestorFanLayout {
  constructor(options = {}) {
    this.options = { ...options };
  }

  compute({ slots = [], edges = [], ...options } = {}) {
    return computeAncestorFanLayout({ ...this.options, ...options, slots, edges });
  }

  positions(projectionOrSlots, baseWidth = 0) {
    const projection = Array.isArray(projectionOrSlots)
      ? { slots: projectionOrSlots, edges: [] }
      : (projectionOrSlots || {});
    return this.compute({ ...projection, width: baseWidth || projection.width || 0 });
  }
}

export function layoutAncestorFan(projectionOrSlots, options = {}) {
  const { baseWidth = 0, ...layoutOptions } = options;
  return new AncestorFanLayout(layoutOptions).positions(projectionOrSlots, baseWidth);
}
