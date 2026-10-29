import { svgNode } from "../core/dom.mjs";
import { polishCount } from "../core/formatting.mjs";
import { linesForItems, NEUTRAL_PLACE_COLOR } from "./maplibre-model.mjs";

export class SchematicSvgMapRenderer {
  constructor(svg, facade, onSelect) {
    this.svg = svg;
    this.facade = facade;
    this.onSelect = onSelect;
    this.width = 960;
    this.height = 560;
    this.margin = { left: 78, right: 68, top: 52, bottom: 58 };
  }

  point(place) {
    const bounds = this.bounds();
    const width = this.width - this.margin.left - this.margin.right;
    const height = this.height - this.margin.top - this.margin.bottom;
    return {
      x: this.margin.left + (place.longitude - bounds.west) / (bounds.east - bounds.west) * width,
      y: this.margin.top + (bounds.north - place.latitude) / (bounds.north - bounds.south) * height,
    };
  }

  bounds() {
    return this.facade.payload?.bounds || { west: 14.07, south: 49, east: 24.15, north: 54.84 };
  }

  routePath(from, to, index) {
    const a = this.point(this.facade.places.get(from.placeId));
    const b = this.point(this.facade.places.get(to.placeId));
    const bend = 18 + (index % 4) * 8;
    const middleX = (a.x + b.x) / 2;
    const middleY = (a.y + b.y) / 2 - bend;
    return `M ${a.x} ${a.y} Q ${middleX} ${middleY} ${b.x} ${b.y}`;
  }

  render(model) {
    if (!this.facade.payload) return;
    this.svg.setAttribute("viewBox", `0 0 ${this.width} ${this.height}`);
    this.svg.replaceChildren();
    const color = model.line?.color || "#285c53";
    const arrowLines = model.lines?.length ? model.lines : [{ id: "default", color }];
    const defs = svgNode("defs", {}, ...arrowLines.map((line, index) =>
      svgNode("marker", { id: `map-arrow-${index}`, markerWidth: 8, markerHeight: 8, refX: 7, refY: 4, orient: "auto", markerUnits: "strokeWidth" },
        svgNode("path", { d: "M 0 0 L 8 4 L 0 8 z", fill: line.color || color }),
      )
    ));
    this.svg.append(defs);
    this.svg.append(svgNode("rect", { class: "map-land", x: 34, y: 24, width: this.width - 68, height: this.height - 48, rx: 28 }));

    const bounds = this.bounds();
    for (let index = 0; index <= 5; index += 1) {
      const x = this.margin.left + index / 5 * (this.width - this.margin.left - this.margin.right);
      const longitude = bounds.west + index / 5 * (bounds.east - bounds.west);
      this.svg.append(svgNode("line", { class: "map-grid-line", x1: x, y1: this.margin.top, x2: x, y2: this.height - this.margin.bottom }));
      this.svg.append(svgNode("text", { class: "map-grid-label", x, y: this.height - 34, "text-anchor": "middle" }, `${longitude.toFixed(1)}°E`));
      const y = this.margin.top + index / 5 * (this.height - this.margin.top - this.margin.bottom);
      const latitude = bounds.north - index / 5 * (bounds.north - bounds.south);
      this.svg.append(svgNode("line", { class: "map-grid-line", x1: this.margin.left, y1: y, x2: this.width - this.margin.right, y2: y }));
      this.svg.append(svgNode("text", { class: "map-grid-label", x: 42, y: y + 3 }, `${latitude.toFixed(1)}°N`));
    }

    for (const place of this.facade.places.values()) {
      const point = this.point(place);
      this.svg.append(
        svgNode("circle", { class: "map-place-base", cx: point.x, cy: point.y, r: 4 }),
        svgNode("text", { class: "map-place-label", x: point.x + 9, y: point.y - 9 }, place.shortLabel || place.label),
      );
    }

    model.routes.forEach((route, index) => {
      const path = this.routePath(route.from, route.to, index);
      const routeColor = route.color || color;
      const arrowIndex = Math.max(0, arrowLines.findIndex((line) => line.id === route.lineId));
      this.svg.append(
        svgNode("path", { class: `map-route ${route.verified ? "" : "is-unverified"}`, d: path, stroke: routeColor, "marker-end": `url(#map-arrow-${arrowIndex})` }),
        svgNode("path", { class: "map-route-hit", d: path, onclick: () => this.onSelect(route.to.placeId, [route.from, route.to], route) }, svgNode("title", {}, `${route.personLabel}: ${route.from.yearFrom}–${route.to.yearFrom}`)),
      );
    });

    const pointPlaceIds = new Set([
      ...model.groups.keys(),
      ...(model.sourceGroups?.keys() || []),
    ]);
    for (const placeId of pointPlaceIds) {
      const place = this.facade.places.get(placeId);
      if (!place) continue;
      const personItems = [...(model.groups.get(placeId) || [])];
      const sourceItems = [...(model.sourceGroups?.get(placeId) || [])];
      const items = [...personItems, ...sourceItems];
      const isSourceOnly = personItems.length === 0 && sourceItems.length > 0;
      const point = this.point(place);
      const radius = 9 + Math.min(12, Math.sqrt(items.length) * 3);
      const lines = linesForItems(model, items);
      const pointColor = isSourceOnly ? NEUTRAL_PLACE_COLOR : lines[0]?.color || color;
      const countLabel = sourceItems.length
        ? `${polishCount(personItems.length, "obserwacja osoby", "obserwacje osób", "obserwacji osób")} · ${polishCount(sourceItems.length, "wystąpienie źródłowe", "wystąpienia źródłowe", "wystąpień źródłowych")}`
        : polishCount(personItems.length, "obserwacja", "obserwacje", "obserwacji");
      this.svg.append(svgNode("g", { class: `map-point${isSourceOnly ? " is-source-only" : ""}`, onclick: () => this.onSelect(placeId, items) },
        svgNode("title", {}, `${place.label}: ${countLabel}`),
        svgNode("circle", { cx: point.x, cy: point.y, r: radius + 4 + Math.max(0, lines.length - 1) * 3, fill: "rgba(255,253,248,.88)" }),
        ...lines.map((line, index) => svgNode("circle", { class: "map-point-ring", cx: point.x, cy: point.y, r: radius + 3 + index * 3, stroke: line.color, fill: "none" })),
        svgNode("circle", { class: "map-point-core", cx: point.x, cy: point.y, r: radius, fill: pointColor }),
        svgNode("text", { class: "map-point-count", x: point.x, y: point.y + .5 }, personItems.length || sourceItems.length),
      ));
    }
  }
}
