import { describe, expect, it, vi } from "vitest";

import { SchematicSvgMapRenderer } from "../../map/schematic-svg-renderer.mjs";


describe("SchematicSvgMapRenderer source observations", () => {
  it("opens a neutral source-only point without creating a route or line assignment", () => {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    const sourceItem = {
      id: "source-observation",
      placeId: "place-source",
      occurrenceId: "occ-source",
      eventId: "event-source",
      sourceId: "source-act",
      yearFrom: 1835,
    };
    const facade = {
      payload: { bounds: { west: 19, south: 51, east: 20, north: 52 } },
      places: new Map([
        ["place-source", { id: "place-source", label: "Dąbrowa", longitude: 19.5, latitude: 51.5 }],
      ]),
    };
    const onSelect = vi.fn();
    const renderer = new SchematicSvgMapRenderer(svg, facade, onSelect);

    renderer.render({
      line: { id: "line", color: "#a95637" },
      lines: [{ id: "line", color: "#a95637" }],
      groups: new Map(),
      sourceGroups: new Map([["place-source", [sourceItem]]]),
      routes: [],
    });

    const point = svg.querySelector<SVGGElement>(".map-point.is-source-only");
    expect(point).not.toBeNull();
    expect(point?.querySelector("title")?.textContent).toContain("0 obserwacji osób · 1 wystąpienie źródłowe");
    expect(point?.querySelector(".map-point-core")?.getAttribute("fill")).toBe("#66756e");
    expect(point?.querySelector(".map-point-count")?.textContent).toBe("1");
    expect(svg.querySelector(".map-route")).toBeNull();
    expect(svg.querySelector(".map-point-ring")).toBeNull();

    point?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    expect(onSelect).toHaveBeenCalledWith("place-source", [sourceItem]);
  });
});
