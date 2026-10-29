import {
  Map as MapLibreMap,
  Marker,
  NavigationControl,
  addProtocol,
} from "maplibre-gl";
import { Protocol } from "pmtiles";

import { boundsForModel, linesForItems, markerBackground, markerGroupsForZoom, NEUTRAL_PLACE_COLOR, POLAND_BOUNDS, routeFeatureCollection, WORLD_BOUNDS } from "./maplibre-model.mjs";


let pmtilesProtocol = null;
export const DEFAULT_LINE_MAX_ZOOM = 5.6;

function ensurePmtilesProtocol() {
  if (pmtilesProtocol) return pmtilesProtocol;
  pmtilesProtocol = new Protocol({ metadata: true });
  addProtocol("pmtiles", pmtilesProtocol.tile);
  return pmtilesProtocol;
}

function emptyFeatureCollection() {
  return { type: "FeatureCollection", features: [] };
}

export function localStyle(archiveUrl) {
  return {
    version: 8,
    sources: {
      "local-basemap": {
        type: "vector",
        url: `pmtiles://${archiveUrl}`,
        attribution: "Natural Earth · lokalne PMTiles",
      },
      "family-routes": {
        type: "geojson",
        data: emptyFeatureCollection(),
      },
    },
    layers: [
      { id: "background", type: "background", paint: { "background-color": "#d7e5e2" } },
      {
        id: "world-land",
        type: "fill",
        source: "local-basemap",
        "source-layer": "countries",
        maxzoom: 5.8,
        paint: { "fill-color": "#f6f0df", "fill-opacity": 0.96 },
      },
      {
        id: "world-borders",
        type: "line",
        source: "local-basemap",
        "source-layer": "countries",
        maxzoom: 5.8,
        paint: { "line-color": "#72847c", "line-width": ["interpolate", ["linear"], ["zoom"], 0, 0.55, 5, 1.35] },
      },
      {
        id: "poland-land",
        type: "fill",
        source: "local-basemap",
        "source-layer": "poland",
        minzoom: 4.5,
        paint: { "fill-color": "#f0ddb2", "fill-opacity": 0.98 },
      },
      {
        id: "poland-border",
        type: "line",
        source: "local-basemap",
        "source-layer": "poland",
        minzoom: 4.5,
        paint: { "line-color": "#465f55", "line-width": ["interpolate", ["linear"], ["zoom"], 4.5, 1.5, 8, 2.5] },
      },
      {
        id: "family-routes-unverified",
        type: "line",
        source: "family-routes",
        filter: ["==", ["get", "verified"], false],
        paint: {
          "line-color": ["get", "color"],
          "line-width": 3,
          "line-opacity": 0.78,
          "line-dasharray": [2, 2],
        },
      },
      {
        id: "family-routes-verified",
        type: "line",
        source: "family-routes",
        filter: ["==", ["get", "verified"], true],
        paint: {
          "line-color": ["get", "color"],
          "line-width": 3.4,
          "line-opacity": 0.88,
        },
      },
    ],
  };
}

export class MapLibreMapRenderer {
  static isSupported() {
    if (typeof document === "undefined") return false;
    try {
      return Boolean(document.createElement("canvas").getContext("webgl2"));
    } catch {
      return false;
    }
  }

  constructor(container, facade, onSelect, onStatus) {
    this.container = container;
    this.facade = facade;
    this.onSelect = onSelect;
    this.onStatus = onStatus;
    this.map = null;
    this.model = null;
    this.markers = [];
    this.loaded = false;
    this.fitOnLoad = true;
    this.pendingFit = null;
    this.initialize();
  }

  initialize() {
    ensurePmtilesProtocol();
    const configured = this.facade.payload?.baseMap?.archiveUrl || "map/genea-basemap.pmtiles";
    const archive = new URL(configured, document.baseURI);
    const buildToken = new URL(document.location.href).searchParams.get("build");
    if (buildToken) archive.searchParams.set("build", buildToken);
    const archiveUrl = archive.href;
    this.map = new MapLibreMap({
      container: this.container,
      style: localStyle(archiveUrl),
      center: [19.145, 52.0],
      zoom: 5,
      minZoom: 0.6,
      maxZoom: 9,
      attributionControl: true,
      dragRotate: false,
      pitchWithRotate: false,
      renderWorldCopies: false,
    });
    this.map.addControl(new NavigationControl({ showCompass: false }), "bottom-right");
    this.map.on("load", () => {
      this.loaded = true;
      this.updateSources();
      this.renderMarkers();
      if (this.pendingFit) this.fit(this.pendingFit);
      else if (this.fitOnLoad) this.fit("line");
    });
    this.map.on("sourcedata", (event) => {
      if (event.sourceId === "local-basemap" && event.isSourceLoaded) this.onStatus?.("ready");
    });
    this.map.on("idle", () => {
      if (this.map.isSourceLoaded("local-basemap")) this.onStatus?.("ready");
    });
    this.map.on("zoomend", () => this.renderMarkers());
    this.map.on("error", (event) => {
      const message = String(event?.error?.message || event?.error || "");
      if (message.includes("Tile not found") || /abort|cancel/i.test(message)) return;
      this.onStatus?.("error", message);
    });
    for (const layerId of ["family-routes-unverified", "family-routes-verified"]) {
      this.map.on("click", layerId, (event) => {
        const routeIndex = Number(event.features?.[0]?.properties?.routeIndex);
        const route = this.model?.routes?.[routeIndex];
        if (route) this.onSelect(route.to.placeId, [route.from, route.to], route);
      });
      this.map.on("mouseenter", layerId, () => { this.map.getCanvas().style.cursor = "pointer"; });
      this.map.on("mouseleave", layerId, () => { this.map.getCanvas().style.cursor = ""; });
    }
  }

  render(model) {
    this.model = model;
    if (!this.loaded) return;
    this.updateSources();
    this.renderMarkers();
  }

  updateSources() {
    if (!this.loaded || !this.model) return;
    this.map.getSource("family-routes")?.setData(routeFeatureCollection(this.model, this.facade.places));
  }

  renderMarkers() {
    for (const marker of this.markers) marker.remove();
    this.markers = [];
    if (!this.loaded || !this.model) return;
    for (const group of markerGroupsForZoom(this.model, this.facade.places, this.map.getZoom())) {
      const { place, items } = group;
      const lines = linesForItems(this.model, items);
      const personCount = group.personItems.length;
      const sourceCount = group.sourceItems.length;
      const isNeutral = personCount === 0;
      const element = document.createElement("button");
      element.type = "button";
      element.className = `maplibre-family-marker ${group.isRegionCluster ? "is-region" : ""} ${isNeutral ? "is-neutral" : ""} ${group.isSourceOnly ? "is-source-only" : ""}`;
      element.style.setProperty("--line-fill", markerBackground(lines, isNeutral ? NEUTRAL_PLACE_COLOR : this.model.line?.color || NEUTRAL_PLACE_COLOR));
      const lineLabels = lines.map((line) => line.label).join(", ");
      const localityLabel = group.isRegionCluster ? ` w ${group.placeIds.length} miejscowościach` : "";
      const countLabel = sourceCount
        ? `${personCount} obserwacji osób; ${sourceCount} wystąpień źródłowych`
        : `${personCount} obserwacji`;
      element.setAttribute("aria-label", `${place.label}: ${countLabel}${localityLabel}${lineLabels ? `; ${lineLabels}` : ""}`);
      element.innerHTML = `<span>${personCount || sourceCount}</span><small>${place.shortLabel || place.label}</small>`;
      element.addEventListener("click", (event) => {
        event.stopPropagation();
        this.onSelect(place, items);
      });
      this.markers.push(new Marker({ element, anchor: "center" })
        .setLngLat([place.longitude, place.latitude])
        .addTo(this.map));
    }
  }

  fit(scope) {
    if (!this.loaded || !this.model) {
      this.pendingFit = scope;
      return;
    }
    this.pendingFit = null;
    this.container.dataset.cameraScope = scope;
    const bounds = scope === "world"
      ? WORLD_BOUNDS
      : scope === "poland"
        ? POLAND_BOUNDS
        : boundsForModel(this.model, this.facade.places, this.facade.payload?.bounds);
    const options = { padding: 48, duration: 500 };
    // The local basemap deliberately contains administrative context, not
    // street-level detail. Keep the initial fit below the country-layer cutoff
    // so the map shows Poland and neighbouring borders instead of an anonymous
    // uniformly coloured surface. Exact markers remain available by zooming.
    if (scope === "line") options.maxZoom = DEFAULT_LINE_MAX_ZOOM;
    this.map.fitBounds(bounds, options);
  }

  resize() {
    this.map?.resize();
  }
}
