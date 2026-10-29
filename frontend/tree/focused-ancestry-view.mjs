import { node, svgNode } from "../core/dom.mjs";
import { statusLabel } from "../core/formatting.mjs";
import { FocusedAncestryModel } from "./focused-ancestry-model.mjs";

const BASIS_LABELS = {
  canonical: "Drzewo bazowe",
  external_tree: "Dosłowna struktura drzewa zewnętrznego",
  local_source: "Relacje wskazane przez źródło lokalne",
};

const RELATION_LABELS = {
  source_stated: "rodzicielstwo wskazane przez źródło",
  parentage_hypothesis: "ustrukturyzowana hipoteza rodzicielstwa",
};

function identityBasis(scenario, item) {
  if (item.id === scenario.rootNodeId) return "Osoba bazowa";
  if (scenario.basis === "external_tree") return "Profil zewnętrzny";
  if (scenario.basis === "local_source") return "Wystąpienie źródłowe";
  return "Osoba drzewa bazowego";
}

function relationStatusLabel(status) {
  return ({
    family_known: "znane rodzinnie",
    family_recorded: "zapisani jako rodzice",
    private_unverified: "niezweryfikowane",
  })[status] || statusLabel(status);
}

function gapClass(gap) {
  const value = `${gap?.kind || ""}`.toLocaleLowerCase("pl");
  if (value.includes("cycle")) return "cycle";
  if (value.includes("limit") || value.includes("max_") || value.includes("frontier")) return "limit";
  return "gap";
}

function gapCopy(gap) {
  const prefix = gapClass(gap) === "cycle" ? "Cykl ścieżki" : gapClass(gap) === "limit" ? "Granica snapshotu" : "Luka danych";
  return `${prefix}: ${gap.label}${gap.conditionLabel ? ` · Warunek: ${gap.conditionLabel}` : ""}`;
}

export class FocusedAncestryView {
  constructor({ container, onSelect = () => {}, onExit = () => {}, onStateChange = () => {}, initialState = null } = {}) {
    if (!container || typeof container.replaceChildren !== "function") throw new TypeError("FocusedAncestryView wymaga kontenera DOM.");
    this.container = container;
    this.onSelect = onSelect;
    this.onExit = onExit;
    this.onStateChange = onStateChange;
    this.model = new FocusedAncestryModel({ initialState });
    this.cardElements = new Map();
    this.connectorEntries = [];
    this.destroyed = false;
    this.onResize = () => this.scheduleConnectors();
    globalThis.window?.addEventListener?.("resize", this.onResize);
    const ResizeObserverClass = globalThis.ResizeObserver || globalThis.window?.ResizeObserver;
    this.resizeObserver = ResizeObserverClass ? new ResizeObserverClass(this.onResize) : null;
  }

  setScenario(scenario, { focusNodeId = null } = {}) {
    this.model.setScenario(scenario, { focusNodeId });
    this.render();
    const selectedNodeId = this.model.state.selectedNodeId;
    if (selectedNodeId) this.onSelect(this.model.model.nodeById.get(selectedNodeId));
  }

  serializedState() {
    return this.model.serializedState();
  }

  emitStateChange() {
    this.onStateChange(this.serializedState());
  }

  render() {
    if (this.destroyed || !this.model.scenario) return;
    this.cardElements.clear();
    this.connectorEntries = [];
    const scenario = this.model.scenario;
    const frame = this.model.frame();
    const headingId = `focused-ancestry-heading-${String(scenario.key).replace(/[^a-zA-Z0-9_-]/g, "-")}`;
    this.svg = svgNode("svg", { class: "focused-ancestry-connectors", "aria-hidden": "true" });

    const breadcrumbs = node("nav", { class: "focused-ancestry-breadcrumbs", "aria-label": "Historia przeglądanej linii" },
      ...frame.breadcrumbs.map((item, index) => (
        index === frame.breadcrumbs.length - 1
          ? node("span", { class: "is-current", "aria-current": "page" }, item.node.label)
          : node("button", { type: "button", "aria-label": item.node.label, onclick: () => this.backTo(item.index) }, item.node.label)
      )),
    );
    const visitedWindows = this.model.visitedWindows().filter((item) => !item.current);
    const visitedLines = visitedWindows.length ? node("details", { class: "focused-ancestry-visited" },
      node("summary", {}, `Odwiedzone linie (${visitedWindows.length})`),
      node("ul", {}, ...visitedWindows.map((item) => {
        const label = item.nodes.map((person) => person.label).join(" → ");
        return node("li", {}, node("button", {
          type: "button",
          "aria-label": `Wróć do odwiedzonej linii: ${label}`,
          onclick: () => this.revisitVisitedPath(item.index),
        }, label));
      })),
    ) : null;

    const rootKey = `root:${frame.breadcrumbs.map((item) => item.viaEdgeId || item.nodeId).join(":")}`;
    const rootColumn = node("section", { class: "focused-ancestry-root", "aria-label": "Osoba początkowa bieżącego okna" },
      node("h3", {}, "Punkt bieżący"),
      this.renderCard(frame.root, { occurrenceKey: rootKey, generation: 0 }),
    );

    const branches = node("div", { class: "focused-ancestry-branches" });
    if (!frame.parents.length) {
      branches.append(node("section", { class: "focused-ancestry-empty-branch" },
        node("h3", {}, "Rodzice"),
        this.renderBoundary(frame.root),
      ));
    }
    frame.parents.forEach((parent, parentIndex) => {
      const parentKey = `parent:${parent.edge.id}`;
      this.connectorEntries.push({ fromKey: rootKey, toKey: parentKey, edge: parent.edge });
      const grandparents = node("div", { class: "focused-ancestry-grandparents", "aria-label": `Rodzice: ${parent.node.label}` },
        node("h4", {}, `Rodzice: ${parent.node.label}`),
      );
      if (!parent.grandparents.length) grandparents.append(this.renderBoundary(parent.node, { cyclic: parent.cyclic }));
      parent.grandparents.forEach((grandparent, grandparentIndex) => {
        const grandparentKey = `grandparent:${parent.edge.id}:${grandparent.edge.id}:${grandparentIndex}`;
        this.connectorEntries.push({ fromKey: parentKey, toKey: grandparentKey, edge: grandparent.edge });
        grandparents.append(this.renderCard(grandparent.node, {
          occurrenceKey: grandparentKey,
          generation: 2,
          viaEdge: grandparent.edge,
          bridgeEdge: parent.edge,
          cyclic: grandparent.cyclic,
        }));
      });
      branches.append(node("section", { class: "focused-ancestry-branch", "data-branch-index": parentIndex },
        node("div", { class: "focused-ancestry-parent" },
          node("h3", {}, parentIndex < 2 ? "Rodzic" : "Alternatywna relacja rodzicielska"),
          this.renderCard(parent.node, {
            occurrenceKey: parentKey,
            generation: 1,
            viaEdge: parent.edge,
            cyclic: parent.cyclic,
          }),
        ),
        grandparents,
      ));
    });

    const conditional = scenario.basis !== "canonical"
      || scenario.edges.some((edge) => edge.canonicalRelationship === false);
    const warning = conditional ? node("p", { class: "focused-ancestry-warning", role: "note" },
      "Warstwa warunkowa: te połączenia nie zmieniają tożsamości osób ani relacji drzewa bazowego. Niepewność wcześniejszego kroku pozostaje w dalszej ścieżce.",
    ) : null;
    const limit = scenario.truncated ? node("p", { class: "focused-ancestry-limit", role: "note" },
      scenario.limitLabel || "Snapshot został ograniczony. Ukryty dalszy zakres nie oznacza braku przodków ani pełnego przeglądu materiału.",
    ) : null;

    this.canvas = node("div", { class: "focused-ancestry-canvas" },
      this.svg,
      node("div", { class: "focused-ancestry-columns" }, rootColumn, branches),
    );
    const shell = node("section", { class: "focused-ancestry-shell", "aria-labelledby": headingId },
      node("header", { class: "focused-ancestry-header" },
        node("div", {},
          node("p", { class: "focused-ancestry-eyebrow" }, "Skupione drzewo przodków"),
          node("h2", { id: headingId }, scenario.label),
          node("p", { class: "focused-ancestry-basis" }, `Podstawa scenariusza: ${BASIS_LABELS[scenario.basis]}`),
        ),
        node("button", { type: "button", class: "focused-ancestry-exit", onclick: () => this.onExit() }, "Wróć do drzewa"),
      ),
      warning,
      limit,
      breadcrumbs,
      visitedLines,
      this.canvas,
      node("footer", { class: "focused-ancestry-legend", "aria-label": "Legenda" },
        node("span", {}, "Linia ciągła — relacja drzewa bazowego"),
        node("span", {}, "Linia przerywana — relacja warunkowa"),
        node("span", {}, "Luka lub limit nie stanowią dowodu negatywnego"),
      ),
    );
    this.container.replaceChildren(shell);
    this.resizeObserver?.disconnect();
    this.resizeObserver?.observe(this.canvas);
    this.scheduleConnectors();
  }

  renderCard(item, { occurrenceKey, generation, viaEdge = null, bridgeEdge = null, cyclic = false }) {
    const state = this.model.state;
    const selected = state.selectedNodeId === item.id;
    const parentEdges = this.model.parentsOf(item.id);
    const gaps = this.model.gapsFor(item.id);
    const expandAction = parentEdges.length && generation > 0 && !cyclic ? node("button", {
      type: "button",
      class: "focused-ancestry-expand",
      "aria-label": `Rozwiń rodziców: ${item.label}`,
      onclick: () => this.expand(item.id, viaEdge, bridgeEdge),
    }, "Rozwiń rodziców →") : null;
    const relationBasis = viaEdge ? node("p", {
      class: `focused-ancestry-relation-warning${viaEdge.canonicalRelationship ? " is-base" : ""}`,
      "data-relation-status": viaEdge.status,
    },
    viaEdge.canonicalRelationship
      ? "Relacja drzewa bazowego"
      : (RELATION_LABELS[viaEdge.relationKind] || "relacja warunkowa"),
    node("span", {}, `Status: ${relationStatusLabel(viaEdge.status)}`),
    ) : null;
    const card = node("article", {
      class: `focused-ancestry-card${selected ? " is-selected" : ""}`,
      "data-node-id": item.id,
      "data-generation": generation,
    },
    node("button", {
      type: "button",
      class: "focused-ancestry-select",
      "aria-pressed": String(selected),
      onclick: () => this.select(item),
    },
    node("strong", {}, item.label),
    node("span", { class: "focused-ancestry-node-basis" }, identityBasis(this.model.scenario, item)),
    item.pathUncertain ? node("span", { class: "focused-ancestry-path-warning" }, "Niepewność ścieżki zachowana") : null,
    ),
    relationBasis,
    expandAction ? node("div", { class: "focused-ancestry-card-actions" }, expandAction) : null,
    cyclic ? node("p", { class: "focused-ancestry-boundary is-cycle" }, "Cykl ścieżki — dalsze rozwijanie zatrzymane.") : null,
    generation === 2 && !cyclic && (!parentEdges.length || gaps.length) ? this.renderBoundary(item) : null,
    parentEdges.length && generation === 2 && !cyclic
      ? node("p", { class: "focused-ancestry-boundary is-collapsed" }, "Dalsza linia jest zwinięta w tym czytelnym oknie dwóch poziomów.")
      : null,
    );
    this.cardElements.set(occurrenceKey, card);
    return card;
  }

  renderBoundary(item, { cyclic = false } = {}) {
    if (cyclic) return node("p", { class: "focused-ancestry-boundary is-cycle" }, "Cykl ścieżki — dalsze rozwijanie zatrzymane.");
    const gaps = this.model.gapsFor(item.id);
    if (gaps.length) return node("div", { class: "focused-ancestry-boundaries" }, ...gaps.map((gap) => (
      node("p", { class: `focused-ancestry-boundary is-${gapClass(gap)}` }, gapCopy(gap))
    )));
    return node("p", { class: "focused-ancestry-boundary is-unrecorded" },
      "Brak zapisanej jawnej relacji rodzicielstwa w tym snapshotcie — nie jest to dowód braku rodziców.",
    );
  }

  select(item) {
    if (!this.model.select(item.id)) return;
    this.onSelect(item);
    this.emitStateChange();
    this.render();
  }

  expand(nodeId, viaEdge, bridgeEdge = null) {
    if (!viaEdge || !this.model.expand(nodeId, viaEdge.id, bridgeEdge?.id || null)) return;
    this.emitStateChange();
    this.render();
  }

  backTo(index) {
    if (!this.model.backTo(index)) return;
    this.emitStateChange();
    this.render();
  }

  revisitVisitedPath(index) {
    if (!this.model.revisitVisitedPath(index)) return;
    const selectedNodeId = this.model.state.selectedNodeId;
    if (selectedNodeId) this.onSelect(this.model.model.nodeById.get(selectedNodeId));
    this.emitStateChange();
    this.render();
  }

  scheduleConnectors() {
    if (this.destroyed) return;
    if (this.connectorFrame != null) globalThis.cancelAnimationFrame?.(this.connectorFrame);
    const schedule = globalThis.requestAnimationFrame || ((callback) => setTimeout(callback, 0));
    this.connectorFrame = schedule(() => {
      this.connectorFrame = null;
      this.drawConnectors();
    });
  }

  drawConnectors() {
    if (!this.canvas || !this.svg) return;
    const canvasRect = this.canvas.getBoundingClientRect();
    // The previous absolute SVG must not determine the next canvas size.
    // Otherwise a desktop-sized SVG prevents a resized panel from shrinking.
    const width = Math.max(1, canvasRect.width);
    const height = Math.max(1, canvasRect.height);
    const paths = [];
    for (const entry of this.connectorEntries) {
      const from = this.cardElements.get(entry.fromKey)?.getBoundingClientRect();
      const to = this.cardElements.get(entry.toKey)?.getBoundingClientRect();
      if (!from || !to) continue;
      const fromX = from.right - canvasRect.left;
      const fromY = from.top + from.height / 2 - canvasRect.top;
      const toX = to.left - canvasRect.left;
      const toY = to.top + to.height / 2 - canvasRect.top;
      const stacked = toX <= fromX + 12;
      const startX = stacked ? from.left + from.width / 2 - canvasRect.left : fromX;
      const startY = stacked ? from.bottom - canvasRect.top : fromY;
      const endX = stacked ? to.left + to.width / 2 - canvasRect.left : toX;
      const endY = stacked ? to.top - canvasRect.top : toY;
      const middle = stacked ? startY + (endY - startY) / 2 : startX + (endX - startX) / 2;
      const d = stacked
        ? `M ${startX} ${startY} V ${middle} H ${endX} V ${endY}`
        : `M ${startX} ${startY} H ${middle} V ${endY} H ${endX}`;
      paths.push(svgNode("path", {
        d,
        class: entry.edge.canonicalRelationship ? "is-canonical" : "is-conditional",
        "data-edge-id": entry.edge.id,
        "data-canonical-relationship": String(entry.edge.canonicalRelationship),
      }, svgNode("title", {}, entry.edge.canonicalRelationship ? "Relacja drzewa bazowego" : "Relacja warunkowa")));
    }
    this.svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    this.svg.setAttribute("width", width);
    this.svg.setAttribute("height", height);
    this.svg.replaceChildren(...paths);
  }

  destroy() {
    this.destroyed = true;
    globalThis.window?.removeEventListener?.("resize", this.onResize);
    this.resizeObserver?.disconnect();
    if (this.connectorFrame != null) {
      const cancel = globalThis.cancelAnimationFrame || clearTimeout;
      cancel(this.connectorFrame);
    }
    this.container.replaceChildren();
    this.cardElements.clear();
    this.connectorEntries = [];
  }
}
