const DEFAULT_CARD_WIDTH = 224;
const DEFAULT_CARD_HEIGHT = 86;
const DEFAULT_COLUMN_GAP = 56;
const DEFAULT_ROW_STEP = 150;
const DEFAULT_SIDE_PADDING = 90;
const DEFAULT_TOP = 54;

function finitePositive(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : fallback;
}

function compareSlotIds(first, second) {
  if (first.slotId < second.slotId) return -1;
  if (first.slotId > second.slotId) return 1;
  return 0;
}

export class AncestorVerticalLayout {
  constructor({
    cardWidth = DEFAULT_CARD_WIDTH,
    cardHeight = DEFAULT_CARD_HEIGHT,
    columnGap = DEFAULT_COLUMN_GAP,
    rowGap = DEFAULT_ROW_STEP,
    sidePadding = DEFAULT_SIDE_PADDING,
    top = DEFAULT_TOP,
  } = {}) {
    this.cardWidth = finitePositive(cardWidth, DEFAULT_CARD_WIDTH);
    this.cardHeight = finitePositive(cardHeight, DEFAULT_CARD_HEIGHT);
    this.columnGap = finitePositive(columnGap, DEFAULT_COLUMN_GAP);
    this.rowGap = finitePositive(rowGap, DEFAULT_ROW_STEP);
    this.sidePadding = finitePositive(sidePadding, DEFAULT_SIDE_PADDING);
    this.top = Number.isFinite(Number(top)) ? Number(top) : DEFAULT_TOP;
  }

  generationStep() {
    return Math.max(this.rowGap, this.cardHeight + 1);
  }

  positions(projectionOrSlots, baseWidth = 0) {
    const slots = (Array.isArray(projectionOrSlots) ? projectionOrSlots : projectionOrSlots?.slots || [])
      .filter((slot) => typeof slot?.slotId === "string" && Number.isFinite(Number(slot?.depth)));
    const grouped = new Map();
    for (const slot of slots) {
      const depth = Math.max(0, Math.floor(Number(slot.depth)));
      const row = grouped.get(depth) || [];
      row.push(slot);
      grouped.set(depth, row);
    }
    for (const row of grouped.values()) row.sort(compareSlotIds);

    const maximumDepth = grouped.size ? Math.max(...grouped.keys()) : 0;
    const widestRowCount = grouped.size ? Math.max(...[...grouped.values()].map((row) => row.length)) : 0;
    const widestRowWidth = widestRowCount
      ? widestRowCount * this.cardWidth + (widestRowCount - 1) * this.columnGap
      : 0;
    const requestedWidth = Math.max(0, Number(baseWidth) || 0);
    const width = Math.max(requestedWidth, widestRowWidth + this.sidePadding * 2);
    const step = this.generationStep();
    const positions = new Map();

    for (const [depth, row] of [...grouped.entries()].sort(([first], [second]) => first - second)) {
      const rowWidth = row.length * this.cardWidth + Math.max(0, row.length - 1) * this.columnGap;
      const startX = (width - rowWidth) / 2;
      const y = this.top + (maximumDepth - depth) * step;
      row.forEach((slot, index) => {
        positions.set(slot.slotId, {
          x: startX + index * (this.cardWidth + this.columnGap),
          y,
          depth,
        });
      });
    }

    const height = slots.length
      ? this.top + maximumDepth * step + this.cardHeight + this.sidePadding
      : 0;
    return {
      positions,
      width,
      height,
      generationSpan: maximumDepth,
    };
  }
}

export function layoutAncestorSlots(projectionOrSlots, options = {}) {
  const { baseWidth = 0, ...layoutOptions } = options;
  return new AncestorVerticalLayout(layoutOptions).positions(projectionOrSlots, baseWidth);
}
