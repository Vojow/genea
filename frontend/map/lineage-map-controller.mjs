import { $, $$, node } from "../core/dom.mjs";
import { MapDataFacade } from "./map-data-facade.mjs";
import { MapLibreMapRenderer } from "./maplibre-renderer.mjs";
import { SchematicSvgMapRenderer } from "./schematic-svg-renderer.mjs";

export class LineageMapController {
  constructor(payload, { onInspect }) {
    this.payload = payload;
    this.onInspect = onInspect;
    this.facade = new MapDataFacade(payload);
    this.svg = $("#family-map-svg");
    this.canvas = $("#family-map-canvas");
    this.mode = "locations";
    this.focusedPersonIds = null;
    this.focusedPersonKey = null;
    this.lineIds = new Set((payload?.lines || []).map((line) => line.id).filter(Boolean));
    this.range = {
      from: payload?.yearRange?.from ?? 1800,
      to: payload?.yearRange?.to ?? new Date().getFullYear(),
    };
    this.yearFrom = this.range.from;
    this.yearTo = this.range.to;
    this.usesMapLibre = payload?.renderer === "maplibre" && MapLibreMapRenderer.isSupported();
    this.svg.hidden = this.usesMapLibre;
    this.canvas.hidden = !this.usesMapLibre;
    this.renderer = this.usesMapLibre
      ? new MapLibreMapRenderer(
        this.canvas,
        this.facade,
        (place, items, route) => this.inspect(place, items, route),
        (status, message) => this.updateBasemapStatus(status, message),
      )
      : new SchematicSvgMapRenderer(this.svg, this.facade, (placeId, items, route) => this.inspect(placeId, items, route));
    this.updateBasemapStatus(this.usesMapLibre ? "loading" : "fallback");
    this.bind();
    this.render();
  }

  bind() {
    const options = $("#map-line-options");
    const renderLineOptions = () => {
      options.replaceChildren(...(this.payload?.lines || []).map((line) => {
        const input = node("input", { type: "checkbox", value: line.id, "aria-label": line.label });
        input.checked = this.lineIds.has(line.id);
        input.addEventListener("change", () => {
          if (input.checked) this.lineIds.add(line.id);
          else this.lineIds.delete(line.id);
          this.clearInspector();
          this.updateLineSummary();
          this.render();
        });
        return node("label", { class: "map-line-option" },
          input,
          node("i", { style: `--line-color:${line.color}` }),
          node("span", {}, line.label),
          node("em", {}, line.observationCount),
        );
      }));
    };
    const setAllLines = (selected) => {
      this.lineIds = new Set(selected ? (this.payload?.lines || []).map((line) => line.id) : []);
      this.clearInspector();
      renderLineOptions();
      this.updateLineSummary();
      this.render();
    };
    renderLineOptions();
    this.updateLineSummary();
    $("#map-lines-all").onclick = () => setAllLines(true);
    $("#map-lines-none").onclick = () => setAllLines(false);
    $$('[data-map-mode]').forEach((button) => button.addEventListener("click", () => {
      this.mode = button.dataset.mapMode;
      $$('[data-map-mode]').forEach((item) => item.classList.toggle("is-active", item === button));
      this.clearInspector();
      this.render();
    }));
    const from = $("#map-year-from");
    const to = $("#map-year-to");
    for (const input of [from, to]) {
      input.min = this.range.from ?? this.yearFrom;
      input.max = this.range.to ?? this.yearTo;
      input.step = 1;
    }
    from.value = this.yearFrom;
    to.value = this.yearTo;
    const updateRange = (changed) => {
      this.yearFrom = Number(from.value);
      this.yearTo = Number(to.value);
      if (this.yearFrom > this.yearTo) {
        if (changed === from) this.yearTo = this.yearFrom;
        else this.yearFrom = this.yearTo;
        from.value = this.yearFrom;
        to.value = this.yearTo;
      }
      this.render();
    };
    from.oninput = () => updateRange(from);
    to.oninput = () => updateRange(to);
    $("#map-reset").onclick = () => {
      this.focusedPersonIds = null;
      this.focusedPersonKey = null;
      this.yearFrom = this.range.from;
      this.yearTo = this.range.to;
      from.value = this.yearFrom;
      to.value = this.yearTo;
      this.render();
    };
    $$('[data-map-camera]').forEach((button) => button.addEventListener("click", () => {
      this.renderer.fit?.(button.dataset.mapCamera);
    }));
  }

  render() {
    if (!this.payload || this.lineIds.size === 0) {
      $("#map-empty").hidden = false;
      $("#map-observation-count").textContent = 0;
      $("#map-route-count").textContent = 0;
      $("#map-description").textContent = "Wybierz co najmniej jedną linię, aby pokazać jej miejsca i przemieszczanie.";
      $("#map-legend").replaceChildren();
      this.renderer.render(this.facade.viewModel([], this.yearFrom, this.yearTo, this.mode));
      return;
    }
    const model = this.facade.viewModel(this.lineIds, this.yearFrom, this.yearTo, this.mode, this.focusedPersonIds);
    this.renderer.render(model);
    $("#map-empty").hidden = model.groups.size > 0 || model.sourceGroups.size > 0;
    $("#map-year-from-output").textContent = this.yearFrom;
    $("#map-year-to-output").textContent = this.yearTo;
    $("#map-observation-count").textContent = model.observations.length;
    $("#map-route-count").textContent = model.routes.length;
    $("#map-title").textContent = this.payload.title || "Mapa rodzin i przemieszczania";
    $("#map-description").textContent = this.focusedPersonIds
      ? "Bezpośrednie obserwacje wybranej osoby. Linie rodzinne i kamera pozostają niezależnym kontekstem mapy."
      : model.lines.length === 1
        ? model.line?.description || this.payload.subtitle || ""
        : `${model.lines.length} wybrane linie na wspólnej mapie. Obserwacja należąca do kilku linii jest liczona tylko raz.`;
    const stage = $("#view-map");
    if (stage) {
      stage.dataset.focusedPersonKey = this.focusedPersonKey || "";
      stage.dataset.focusedPersonIds = [...(this.focusedPersonIds || [])].sort().join(",");
    }
    const unmapped = this.facade.observations(this.lineIds, this.yearFrom, this.yearTo, true).filter((item) => item.reason);
    const legendItems = [
      ...model.lines.map((line) => node("span", {}, node("i", { class: "point", style: `background:${line.color}` }), line.label)),
      node("span", {}, node("i", { class: "route", style: "color:#66756e" }), "kolejne miejsca tej samej osoby"),
      node("span", {}, node("i", { class: "route uncertain" }), "odcinek z materiału do weryfikacji"),
      unmapped.length ? node("span", {}, `${unmapped.length} obserwacji bez pewnej geolokalizacji`) : null,
    ].filter(Boolean);
    $("#map-legend").replaceChildren(...legendItems);
  }

  updateLineSummary() {
    const selected = (this.payload?.lines || []).filter((line) => this.lineIds.has(line.id));
    const summary = $("#map-line-summary");
    if (!selected.length) summary.textContent = "Nie wybrano";
    else if (selected.length === 1) summary.textContent = selected[0].label;
    else if (selected.length === (this.payload?.lines || []).length) summary.textContent = `Wszystkie · ${selected.length}`;
    else summary.textContent = `${selected.length} wybrane`;
  }

  clearInspector() {
    this.onInspect(null);
  }

  focusPersonContext(context) {
    const personIds = new Set(context?.mapPersonIds || []);
    if (!personIds.size) return false;
    const observations = [
      ...(this.payload?.observations || []),
      ...(this.payload?.unmappedObservations || []),
    ].filter((item) => personIds.has(item.personId));
    if (!observations.length) return false;
    this.focusedPersonIds = personIds;
    this.focusedPersonKey = context.personKey || null;
    const lineIds = observations.flatMap((item) => item.lineIds || []);
    if (lineIds.length) this.lineIds = new Set(lineIds);
    document.querySelectorAll("#map-line-options input").forEach((input) => {
      input.checked = this.lineIds.has(input.value);
    });
    const years = observations.flatMap((item) => [item.yearFrom, item.yearTo]).filter(Number.isFinite);
    if (years.length) {
      this.yearFrom = Math.min(...years);
      this.yearTo = Math.max(...years);
      $("#map-year-from").value = this.yearFrom;
      $("#map-year-to").value = this.yearTo;
    }
    this.updateLineSummary();
    this.render();
    const first = observations.find((item) => this.facade.places.has(item.placeId));
    if (first) this.inspect(first.placeId, observations.filter((item) => item.placeId === first.placeId && !item.reason));
    return true;
  }

  resize() {
    this.renderer.resize?.();
  }

  updateBasemapStatus(status, message = "") {
    const badge = $("#map-local-badge");
    badge.dataset.status = status;
    badge.textContent = status === "ready"
      ? "lokalnie · PMTiles"
      : status === "loading"
        ? "ładowanie lokalnych kafli…"
        : status === "error"
          ? `kafle niedostępne${message ? " · mapa danych działa" : ""}`
          : "lokalnie · widok schematyczny";
  }

  inspect(placeReference, items, route = null) {
    const place = typeof placeReference === "string" ? this.facade.places.get(placeReference) : placeReference;
    const lines = (this.payload?.lines || []).filter((line) =>
      this.lineIds.has(line.id) && items.some((item) => item.lineIds?.includes(line.id))
    );
    this.onInspect({ place, items: [...items], route, lines });
  }
}
