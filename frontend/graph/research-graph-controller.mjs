import {
  GraphScopeFacade,
  SOURCE_REPORTED_CANDIDATE_ROLES,
  normalizedCandidateRole,
} from "./graph-scope.mjs";
import { $, $$, node } from "../core/dom.mjs";
import { eventTypeLabel, polishCount, roleLabel } from "../core/formatting.mjs";
import { ResearchGraphBuilder } from "./research-graph-builder.mjs";
import { runForceLayout } from "./force-layout.mjs";
import { CanvasGraphRenderer } from "./canvas-renderer.mjs";
import { GraphViewReducer } from "./graph-view-reducer.mjs";
import { SemanticGraphLayout } from "./semantic-graph-layout.mjs";
import { GraphViewStateStore } from "./graph-view-state-store.mjs";
import { GraphModeProjector } from "./graph-mode-projector.mjs";

const DEFAULT_INCLUDE_CANDIDATES = true;

const CANDIDATE_ROLE_LABELS = {
  all: "Wszystkie klasyfikacje",
  anchor: "Punkt odniesienia",
  direct_ancestor: "Bezpośredni przodkowie",
  ancestor_spouse: "Małżonkowie przodków",
  collateral: "Linie poboczne",
  descendant: "Potomkowie",
  fan: "FAN",
  unclassified: "Nieklasyfikowane",
  conflict: "Konflikty",
};

const FAN_ROLE_OPTIONS = [
  ["all", "Wszystkie role"],
  ["witness", "Świadkowie"],
  ["godparent", "Chrzestni"],
  ["informant", "Zgłaszający"],
  ["family", "Rodzina w akcie"],
  ["other", "Pozostałe"],
];

export function candidateRoleLabel(role) {
  return CANDIDATE_ROLE_LABELS[normalizedCandidateRole(role)] || CANDIDATE_ROLE_LABELS.all;
}

export function graphViewStateForPersistence(state, { sourceReportedScope = false } = {}) {
  if (!sourceReportedScope) return state;
  return {
    ...state,
    focusedNodeId: null,
    expandedIdentityIds: [],
    pinnedPositions: [],
  };
}

export function normalizeGraphModeForScope(mode, scope) {
  if (scope?.scopeKind === "source_reported_network") return "fan";
  return mode === "family" ? "people" : mode;
}

export class ResearchGraphController {
  constructor(canvas, wrap, { payload, dataIndex, onInspect }) {
    this.payload = payload;
    this.dataIndex = dataIndex;
    this.onInspect = onInspect;
    this.builder = new ResearchGraphBuilder({ payload, dataIndex });
    this.canvas = canvas;
    this.wrap = wrap;
    this.stage = wrap.closest(".graph-stage");
    this.list = $("#graph-mobile-list");
    this.presentationMode = "canvas";
    this.ctx = canvas.getContext("2d");
    this.renderer = new CanvasGraphRenderer(canvas, this.ctx);
    this.stateStore = new GraphViewStateStore();
    this.restoredState = this.stateStore.load();
    this.mode = this.restoredState?.mode || "people";
    this.nodes = [];
    this.edges = [];
    this.layoutEdges = [];
    this.nodeById = new Map();
    this.scale = this.restoredState?.camera?.scale || 1;
    this.offsetX = this.restoredState?.camera?.offsetX || 0;
    this.offsetY = this.restoredState?.camera?.offsetY || 0;
    this.drag = null;
    this.pan = null;
    this.selected = null;
    this.selectedEdge = null;
    this.evidencePersonId = null;
    this.expandedEvidenceEventIds = new Set();
    this.expandedClusterIds = new Set();
    this.query = "";
    this.frame = null;
    this.fullGraph = { nodes: [], edges: [] };
    this.positionCache = new Map();
    for (const item of this.restoredState?.pinnedPositions || []) {
      this.positionCache.set(item.id, { x: item.x, y: item.y, fixed: true });
    }
    this.scopeFacade = new GraphScopeFacade(this.payload.graphScopes, this.payload.sourceReportedNetworks);
    this.viewReducer = new GraphViewReducer({ payload, dataIndex });
    this.modeProjector = new GraphModeProjector();
    this.viewReducer.focus(this.restoredState?.focusedNodeId || null);
    this.viewReducer.expandedIdentityIds = new Set(this.restoredState?.expandedIdentityIds || []);
    this.semanticLayout = new SemanticGraphLayout({ payload });
    this.layoutGuides = [];
    this.scopeResult = { nodes: [], edges: [], counts: {}, warnings: [] };
    this.scopeId = this.scopeFacade.scopeById.has(this.restoredState?.scopeId)
      ? this.restoredState.scopeId
      : this.payload.graphScopes?.defaultScopeId || "all";
    const restoredScope = this.scopeFacade.scopeById.get(this.scopeId);
    this.mode = normalizeGraphModeForScope(this.mode, restoredScope);
    this.viewReducer.setEvidenceMode(this.mode);
    const sourceReportedScope = restoredScope?.scopeKind === "source_reported_network";
    this.depth = sourceReportedScope ? 0 : this.restoredState?.depth ?? this.payload.graphScopes?.defaultDepth ?? 1;
    this.includeBridges = !sourceReportedScope;
    this.includeCandidates = DEFAULT_INCLUDE_CANDIDATES;
    this.showDocuments = false;
    this.showHouseholds = false;
    this.identityStatus = "all";
    this.minimumOccurrences = 1;
    this.fanRole = "all";
    this.candidateRole = "all";
    this.clusterType = "all";
    this.minimumClusterMembers = 1;
    this.hasBuilt = false;
    this.needsInitialFit = !this.restoredState?.camera;
    this.bind();
    this.bindControls();
    this.resize();
  }

  bindControls() {
    const scopeSelect = $("#graph-scope");
    scopeSelect.replaceChildren(
      node("option", { value: "all" }, "Wszystkie linie"),
      ...this.scopeFacade.scopes().map((scope) => node("option", { value: scope.id }, scope.optionLabel || scope.label)),
    );
    scopeSelect.value = this.scopeId;
    $("#graph-depth").value = String(this.depth);
    $("#graph-bridges").checked = this.includeBridges;
    $("#graph-candidates").checked = this.includeCandidates;
    $("#graph-documents").checked = this.showDocuments;
    $("#graph-households").checked = this.showHouseholds;
    scopeSelect.addEventListener("change", (event) => {
      const previousScopeId = this.scopeId;
      this.scopeId = event.target.value;
      const scope = this.scopeFacade.scopeById.get(this.scopeId);
      if (scope) this.depth = 0;
      this.mode = normalizeGraphModeForScope(this.mode, scope);
      this.viewReducer.setEvidenceMode(this.mode);
      this.includeBridges = this.scopeId !== "all" && scope?.scopeKind !== "source_reported_network";
      this.showDocuments = false;
      this.showHouseholds = false;
      this.candidateRole = "all";
      this.viewReducer.focus(null);
      this.selected = null;
      this.selectedEdge = null;
      if (previousScopeId !== this.scopeId) this.needsInitialFit = this.scopeId !== "all";
      this.updateControlValues();
      this.build();
      if (this.scopeId !== "all") {
        this.fit();
        this.simulate();
        this.inspectCurrentContext();
      } else {
        this.cancelSimulation?.();
        this.onInspect(null);
      }
      this.persistState();
    });
    $("#graph-depth").addEventListener("change", (event) => {
      this.depth = event.target.value === "all" ? "all" : Number(event.target.value);
      this.applyScope();
      this.persistState();
    });
    $("#graph-bridges").addEventListener("change", (event) => {
      this.includeBridges = event.target.checked;
      this.applyScope();
    });
    $("#graph-candidates").addEventListener("change", (event) => {
      this.includeCandidates = event.target.checked;
      this.applyScope();
    });
    $("#graph-documents").addEventListener("change", (event) => {
      this.showDocuments = event.target.checked;
      this.build();
      this.fit();
      this.simulate();
    });
    $("#graph-households").addEventListener("change", (event) => {
      this.showHouseholds = event.target.checked;
      this.applyViewReduction();
    });
    $("#graph-identity-status").addEventListener("change", (event) => {
      this.identityStatus = event.target.value;
      this.applyViewReduction();
    });
    $("#graph-min-occurrences").addEventListener("change", (event) => {
      this.minimumOccurrences = Number(event.target.value);
      this.applyViewReduction();
    });
    $("#graph-fan-role").addEventListener("change", (event) => {
      const sourceReportedScope = this.scopeFacade.scopeById.get(this.scopeId)?.scopeKind === "source_reported_network";
      if (sourceReportedScope) this.candidateRole = normalizedCandidateRole(event.target.value);
      else this.fanRole = event.target.value;
      this.applyViewReduction();
    });
    $("#graph-cluster-type").addEventListener("change", (event) => {
      this.clusterType = event.target.value;
      this.applyViewReduction();
    });
    $("#graph-min-cluster-members").addEventListener("change", (event) => {
      this.minimumClusterMembers = Number(event.target.value);
      this.applyViewReduction();
    });
    $("#graph-clear-filters").addEventListener("click", () => {
      this.scopeId = "all";
      this.depth = this.payload.graphScopes?.defaultDepth ?? 1;
      this.includeBridges = false;
      this.includeCandidates = DEFAULT_INCLUDE_CANDIDATES;
      this.showDocuments = false;
      this.showHouseholds = false;
      this.identityStatus = "all";
      this.minimumOccurrences = 1;
      this.fanRole = "all";
      this.candidateRole = "all";
      this.clusterType = "all";
      this.minimumClusterMembers = 1;
      this.expandedClusterIds.clear();
      this.viewReducer.focus(null);
      this.selected = null;
      this.selectedEdge = null;
      this.updateControlValues();
      this.build();
      this.cancelSimulation?.();
      this.onInspect(null);
      this.persistState();
    });
    $("#graph-line-navigator")?.addEventListener("click", (event) => {
      const target = event.target.closest("[data-line-scope]");
      if (!target) return;
      this.activateScope(target.dataset.lineScope, { preferredMode: "fan" });
    });
    $$(".graph-overlay-panel").forEach((panel) => {
      panel.addEventListener("toggle", () => this.draw());
    });
    $("#graph-evidence-close")?.addEventListener("click", () => {
      if (this.evidencePersonId) this.toggleEvidence(this.evidencePersonId);
    });
    this.updateControlValues();
  }

  updateControlValues() {
    const sourceReportedScope = this.scopeFacade.scopeById.get(this.scopeId)?.scopeKind === "source_reported_network";
    $("#graph-scope").value = this.scopeId;
    $("#graph-depth").value = String(this.depth);
    $("#graph-depth").disabled = this.scopeId === "all" || sourceReportedScope;
    $("#graph-bridges").checked = this.includeBridges;
    $("#graph-bridges").disabled = this.scopeId === "all" || this.mode === "clusters" || sourceReportedScope;
    $("#graph-bridges").closest("label").hidden = this.mode === "clusters";
    $("#graph-candidates").checked = sourceReportedScope ? false : this.includeCandidates;
    $("#graph-candidates").disabled = sourceReportedScope;
    $("#graph-identity-status").value = this.identityStatus;
    $("#graph-min-occurrences").value = String(this.minimumOccurrences);
    const roleSelect = $("#graph-fan-role");
    const expectedRoleOptions = sourceReportedScope
      ? SOURCE_REPORTED_CANDIDATE_ROLES.map((role) => [role, CANDIDATE_ROLE_LABELS[role]])
      : FAN_ROLE_OPTIONS;
    const optionSignature = expectedRoleOptions.map(([value]) => value).join("|");
    if (roleSelect.dataset.optionSignature !== optionSignature) {
      roleSelect.replaceChildren(...expectedRoleOptions.map(([value, label]) => node("option", { value }, label)));
      roleSelect.dataset.optionSignature = optionSignature;
    }
    roleSelect.value = sourceReportedScope ? this.candidateRole : this.fanRole;
    roleSelect.setAttribute("aria-label", sourceReportedScope ? "Klasyfikacja w drzewie zewnętrznym" : "Rola w sieci FAN");
    const roleFieldLabel = roleSelect.closest("label")?.querySelector("span");
    if (roleFieldLabel) roleFieldLabel.textContent = sourceReportedScope ? "Klasyfikacja" : "Rola";
    $("#graph-cluster-type").value = this.clusterType;
    $("#graph-min-cluster-members").value = String(this.minimumClusterMembers);
    $("#graph-households").checked = this.showHouseholds;
    $$('[data-graph-mode]').forEach((item) => {
      const unavailable = sourceReportedScope && item.dataset.graphMode !== "fan";
      item.disabled = unavailable;
      item.setAttribute("aria-disabled", String(unavailable));
      item.classList.toggle("is-active", item.dataset.graphMode === this.mode);
    });
    $$('[data-mode-controls]').forEach((item) => { item.hidden = item.dataset.modeControls !== this.mode; });
    $$('[data-legend-modes]').forEach((item) => {
      item.hidden = !item.dataset.legendModes.split(" ").includes(this.mode);
    });
    const documents = $("#graph-documents");
    const documentsAvailable = this.scopeId !== "all" && this.mode === "fan" && !sourceReportedScope;
    documents.disabled = !documentsAvailable;
    documents.checked = documentsAvailable && this.showDocuments;
    documents.closest("label").hidden = sourceReportedScope;
    const households = $("#graph-households");
    households.disabled = sourceReportedScope;
    households.checked = !sourceReportedScope && this.showHouseholds;
    households.closest("label").hidden = sourceReportedScope;
  }

  bind() {
    const pointer = (event) => {
      const rect = this.canvas.getBoundingClientRect();
      return { x: event.clientX - rect.left, y: event.clientY - rect.top };
    };
    this.canvas.addEventListener("pointerdown", (event) => {
      const p = pointer(event);
      const hit = this.hit(p.x, p.y);
      const edgeHit = hit ? null : this.renderer.hitEdge(this, p.x, p.y);
      this.canvas.setPointerCapture(event.pointerId);
      if (hit) {
        if (hit.kind === "scope") {
          this.activateScope(hit.ref);
          return;
        }
        this.drag = {
          node: hit,
          dx: p.x - this.screenX(hit.x),
          dy: p.y - this.screenY(hit.y),
          startX: p.x,
          startY: p.y,
          moved: false,
        };
        this.select(hit);
        this.drag.node = this.selected || hit;
      } else if (edgeHit) {
        this.selectEdge(edgeHit);
      } else {
        this.pan = { x: p.x, y: p.y, ox: this.offsetX, oy: this.offsetY };
      }
    });
    this.canvas.addEventListener("pointermove", (event) => {
      const p = pointer(event);
      if (this.drag) {
        if (Math.hypot(p.x - this.drag.startX, p.y - this.drag.startY) <= 4) return;
        this.drag.moved = true;
        this.drag.node.fixed = true;
        this.drag.node.x = (p.x - this.drag.dx - this.offsetX) / this.scale;
        this.drag.node.y = (p.y - this.drag.dy - this.offsetY) / this.scale;
        this.draw();
      } else if (this.pan) {
        this.offsetX = this.pan.ox + p.x - this.pan.x;
        this.offsetY = this.pan.oy + p.y - this.pan.y;
        this.draw();
      } else {
        this.canvas.style.cursor = this.hit(p.x, p.y) || this.renderer.hitEdge(this, p.x, p.y) ? "pointer" : "grab";
      }
    });
    const release = (event) => {
      const pan = this.pan;
      const drag = this.drag;
      this.drag = null;
      this.pan = null;
      if (drag) {
        if (drag.moved) {
          this.positionCache.set(drag.node.id, { x: drag.node.x, y: drag.node.y, fixed: true });
          this.persistState();
        }
        return;
      }
      if (event.type !== "pointerup" || !pan) return;
      const p = pointer(event);
      if (Math.hypot(p.x - pan.x, p.y - pan.y) > 4) {
        this.persistState();
        return;
      }
      this.offsetX = pan.ox;
      this.offsetY = pan.oy;
      this.clearFocus();
    };
    this.canvas.addEventListener("pointerup", release);
    this.canvas.addEventListener("pointercancel", release);
    this.canvas.addEventListener("wheel", (event) => {
      event.preventDefault();
      const p = pointer(event);
      const beforeX = (p.x - this.offsetX) / this.scale;
      const beforeY = (p.y - this.offsetY) / this.scale;
      const minimumScale = this.nodes[0]?.layoutMode === "orbital" ? .1 : .35;
      this.scale = Math.max(minimumScale, Math.min(2.8, this.scale * (event.deltaY > 0 ? .9 : 1.1)));
      this.offsetX = p.x - beforeX * this.scale;
      this.offsetY = p.y - beforeY * this.scale;
      this.draw();
      this.persistState();
    }, { passive: false });
    new ResizeObserver(() => this.resize()).observe(this.wrap);
  }

  resize() {
    const rect = this.wrap.getBoundingClientRect();
    const ratio = window.devicePixelRatio || 1;
    this.canvas.width = Math.max(1, Math.floor(rect.width * ratio));
    this.canvas.height = Math.max(1, Math.floor(rect.height * ratio));
    this.canvas.style.width = `${rect.width}px`;
    this.canvas.style.height = `${rect.height}px`;
    this.ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    this.width = rect.width;
    this.height = rect.height;
    if (this.needsInitialFit && this.nodes.length && this.width && this.height) this.fit();
    else this.draw();
  }

  setMode(mode) {
    const scope = this.scopeFacade.scopeById.get(this.scopeId);
    this.mode = normalizeGraphModeForScope(mode, scope);
    if (this.mode !== "fan") this.showDocuments = false;
    this.expandedClusterIds.clear();
    this.viewReducer.setEvidenceMode(this.mode);
    $$('[data-graph-mode]').forEach((item) => item.classList.toggle("is-active", item.dataset.graphMode === this.mode));
    this.updateMethodNote();
    this.updateEvidenceContext();
    this.updateControlValues();
    this.build();
    if (!this.hasBuilt) {
      if (this.restoredState?.camera) {
        this.needsInitialFit = false;
        this.draw();
      } else {
        this.needsInitialFit = true;
        this.fit();
      }
    }
    this.hasBuilt = true;
    this.simulate();
    this.selected = this.nodeById.get(this.viewReducer.focusedNodeId) || null;
    this.selectedEdge = null;
    if (this.selected) this.onInspect(this.selected);
    else this.inspectCurrentContext();
    this.persistState();
  }

  togglePresentation() {
    this.presentationMode = this.presentationMode === "list" ? "canvas" : "list";
    this.stage?.classList.toggle("is-list-presentation", this.presentationMode === "list");
    if (this.list) this.list.hidden = this.presentationMode !== "list";
    this.renderMobileList();
    if (this.presentationMode === "canvas") requestAnimationFrame(() => this.resize());
    return this.presentationMode === "list";
  }

  mobileListLabel(item) {
    if (item.kind === "occurrence") return this.dataIndex.occurrenceLabel(item.item);
    if (item.kind === "event") return `${eventTypeLabel(item.item?.type)}${item.item?.dateFrom || item.item?.date ? ` · ${item.item.dateFrom || item.item.date}` : ""}`;
    return item.item?.label || item.label || item.item?.name || item.ref || item.id;
  }

  renderMobileList() {
    if (!this.list || this.presentationMode !== "list") return;
    const sourceReportedScope = this.scopeFacade.scopeById.get(this.scopeId)?.scopeKind === "source_reported_network";
    const ordered = [...this.nodes].sort((left, right) => {
      if (sourceReportedScope) {
        const leftRole = left.sourceReportedCandidateRole || "unclassified";
        const rightRole = right.sourceReportedCandidateRole || "unclassified";
        const roleOrder = SOURCE_REPORTED_CANDIDATE_ROLES;
        const roleDifference = roleOrder.indexOf(leftRole) - roleOrder.indexOf(rightRole);
        if (roleDifference) return roleDifference;
      }
      const first = [left.semanticRing ?? 99, left.kind, this.mobileListLabel(left)];
      const second = [right.semanticRing ?? 99, right.kind, this.mobileListLabel(right)];
      return first[0] - second[0] || String(first[1]).localeCompare(String(second[1]), "pl-PL") || String(first[2]).localeCompare(String(second[2]), "pl-PL");
    });
    const buttonFor = (item) => node("button", {
      type: "button",
      class: `graph-list-item is-${item.kind}${this.selected?.id === item.id ? " is-selected" : ""}`,
      onclick: () => this.select(item),
    },
    node("span", {}, sourceReportedScope
      ? candidateRoleLabel(item.sourceReportedCandidateRole)
      : ({ identity: "Osoba", occurrence: "Wystąpienie", event: "Zdarzenie", source: "Źródło", household: "Gospodarstwo", cluster: "Klaster", scope: "Linia" })[item.kind] || item.kind),
    node("strong", {}, this.mobileListLabel(item)),
    item.searchMatch?.detail ? node("small", {}, item.searchMatch.detail) : null);
    if (!sourceReportedScope) {
      this.list.replaceChildren(...ordered.map(buttonFor));
      return;
    }
    const grouped = SOURCE_REPORTED_CANDIDATE_ROLES
      .filter((role) => role !== "all")
      .map((role) => {
        const members = ordered.filter((item) => (item.sourceReportedCandidateRole || "unclassified") === role);
        if (!members.length) return null;
        return node("section", { class: "graph-list-group", "aria-label": candidateRoleLabel(role) },
          node("h3", {}, `${candidateRoleLabel(role)} · ${members.length}`),
          ...members.map(buttonFor));
      })
      .filter(Boolean);
    this.list.replaceChildren(...grouped);
  }

  lineGroupForScope(scope) {
    if (scope.scopeKind === "source_reported_network") return "external";
    const bridgePeople = new Set((scope.bridgeNodes || []).map((item) => item.treePersonId));
    if (bridgePeople.has("wife") || bridgePeople.has("father-in-law-muzyka") || bridgePeople.has("mother-in-law-wroniak")) return "spouse";
    if (bridgePeople.has("mother")) return "maternal";
    if (bridgePeople.has("father")) return "paternal";
    return "other";
  }

  renderLineNavigator(result) {
    const navigator = $("#graph-line-navigator");
    const groupsRoot = $("#graph-line-groups");
    if (!navigator || !groupsRoot) return;
    const active = this.scopeId === "all";
    navigator.hidden = !active;
    this.wrap.classList.toggle("is-line-navigator", active);
    this.stage?.classList.toggle("is-line-navigator", active);
    if (!active) return;

    const summaryByScopeId = new Map(
      (result.nodes || []).filter((item) => item.kind === "scope").map((item) => [item.ref, item]),
    );
    const definitions = [
      ["maternal", "Strona mamy", "Linie przodków mamy oraz związane z nimi gospodarstwa i wystąpienia."],
      ["paternal", "Strona taty", "Linie przodków taty oraz powiązane gałęzie badawcze."],
      ["spouse", "Rodzina żony", "Linie żony, teściów i jej najbliższej rodziny."],
      ["other", "Pozostałe zakresy", "Zakresy przekrojowe, które nie należą wyłącznie do jednej strony rodziny."],
      ["external", "Źródła zewnętrzne", "Jawnie odrębne, nierozstrzygnięte sieci z ograniczoną granicą i bez automatycznego scalania."],
    ];
    const scopes = this.scopeFacade.scopes().filter((scope) => scope.id !== "immediate-family");
    const groupNodes = definitions.map(([groupId, label, description]) => {
      const cards = scopes
        .filter((scope) => this.lineGroupForScope(scope) === groupId)
        .map((scope) => {
          const summary = summaryByScopeId.get(scope.id);
          const item = summary?.item || {};
          const metrics = scope.scopeKind === "source_reported_network"
            ? [scope.countsLabel]
            : [
                item.occurrenceCount != null ? polishCount(item.occurrenceCount, "wystąpienie", "wystąpienia", "wystąpień") : null,
                item.actCount ? polishCount(item.actCount, "akt", "akty", "aktów") : null,
                item.householdCount ? polishCount(item.householdCount, "gospodarstwo", "gospodarstwa", "gospodarstw") : null,
                item.candidateCount ? polishCount(item.candidateCount, "kandydatura", "kandydatury", "kandydatur") : null,
              ].filter(Boolean);
          if (!metrics.length) metrics.push("brak aktów w zakresie");
          return node("button", {
            type: "button",
            class: "line-navigator-card",
            "data-line-scope": scope.id,
            "aria-label": `Otwórz FAN: ${scope.label}${scope.countsLabel ? `. ${scope.countsLabel}` : ""}`,
          },
          node("strong", {}, scope.label),
          node("span", {}, scope.description || scope.notes || "Kontrolowany zakres badań rodzinnych."),
          node("span", { class: "line-navigator-metrics" }, metrics.map((metric) => node("i", {}, metric))),
          node("span", { class: "line-navigator-anchor" }, scope.scopeKind === "source_reported_network"
            ? scope.anchorState === "visible"
              ? "Punkt odniesienia dostępny · stała granica"
              : scope.anchorState === "masked"
                ? "Punkt odniesienia ukryty · bez węzła zastępczego"
                : "Punkt odniesienia nieustalony · stała granica"
            : `Punkt wejścia: ${item.scopeAnchorLabel || "osoba centralna"}`));
        });
      if (!cards.length) return null;
      return node("section", { class: "line-navigator-group", "data-line-group": groupId },
        node("h3", {}, label),
        node("p", {}, description),
        node("div", { class: "line-navigator-list" }, cards));
    }).filter(Boolean);
    groupsRoot.replaceChildren(...groupNodes);
  }

  updateMethodNote() {
    if (this.scopeId === "all" && !this.evidencePersonId) {
      $("#method-note").textContent = "Wybierz linię rodzinną jako punkt startowy. Pokrewieństwo i pokolenia pozostają w Drzewie rodzinnym; tutaj otwierasz lokalny zakres FAN i dowodów.";
      return;
    }
    const externalScope = this.scopeFacade.scopeById.get(this.scopeId);
    if (externalScope?.scopeKind === "source_reported_network") {
      const anchorNote = externalScope.anchorState === "masked"
        ? " Punkt odniesienia jest ukryty; UI nie tworzy węzła, etykiety ani łącznika zastępczego."
        : externalScope.anchorState === "missing"
          ? " Punkt odniesienia nie został ustalony, dlatego role pozostają nieklasyfikowane."
          : " Klasyfikacja jest liczona względem jawnego punktu odniesienia.";
      $("#method-note").textContent = `Źródło zewnętrzne · relacje nierozstrzygnięte. Widok pokazuje wyłącznie elementy jawnie wymienione w eksporcie; nie rozszerza FAN i nie tworzy relacji kanonicznych.${anchorNote}`;
      return;
    }
    const modeNote = {
      people: "Osoby i tożsamości odpowiadają na pytanie „kto jest kim?”. Pokazują rozstrzygnięte grupy wystąpień oraz nierozstrzygnięte kandydatury, ale nie udają drzewa rodzinnego.",
      fan: "FAN odpowiada na pytanie „kto występuje z kim?”. Filtruj role, dołącz gospodarstwa albo włącz akty i źródła bez zmiany zakresu badania.",
      evidence: "Łańcuch dowodowy: osoba kanoniczna → wystąpienie → zdarzenie → źródło → skan i transkrypcja w widoku aktu.",
      clusters: "Klastry odpowiadają na pytanie „jakie grupy i konteksty się wyłaniają?”. Każdy kafel agreguje osoby i akty; można go jawnie rozwinąć z inspektora.",
    }[this.mode];
    $("#method-note").textContent = this.evidencePersonId
      ? "Na graf nałożono akty wybranej osoby. Wybierz akt, aby jawnie rozwinąć jego wystąpienie i źródło; zakres rodziny/FAN nie został rozszerzony."
      : modeNote || this.payload.meta?.methodNote || "";
  }

  updateEvidenceContext() {
    const context = $("#graph-evidence-context");
    if (!context) return;
    context.hidden = !this.evidencePersonId;
    const label = (this.payload.identityGroups || []).find((item) => item.personId === this.evidencePersonId)?.label;
    const output = $("#graph-evidence-person");
    if (output) output.textContent = label || "wybrana osoba";
  }

  initialMode() { return this.mode; }

  sourceReportedContextNode() {
    const scope = this.scopeFacade.scopeById.get(this.scopeId);
    if (scope?.scopeKind !== "source_reported_network") return null;
    return {
      id: `scope-context:${scope.id}`,
      kind: "source-reported-network-context",
      ref: scope.sourceReportedNetworkId,
      label: scope.label,
      item: scope,
    };
  }

  inspectCurrentContext() {
    this.onInspect(this.sourceReportedContextNode());
  }

  searchText(graphNode) {
    if (graphNode.searchEntries?.length) {
      return graphNode.searchEntries.map((entry) => entry.text).filter(Boolean).join(" ");
    }
    const item = graphNode.item;
    if (graphNode.kind === "occurrence") {
      return [this.dataIndex.occurrenceLabel(item), item.name, item.searchName, roleLabel(item.role), item.birth, item.notes].filter(Boolean).join(" ");
    }
    if (graphNode.kind === "event") {
      return [eventTypeLabel(item.type), item.type, item.date, item.dateFrom, item.place, ...(item.originalDates || [])].filter(Boolean).join(" ");
    }
    return [item.label, item.summary, item.place, item.notes].filter(Boolean).join(" ");
  }

  buildFullGraph() {
    // Nawigator linii ma stałe, niezależne od aktualnego trybu podsumowania.
    // Pełny graf FAN zapewnia wystąpienia, gospodarstwa i akty potrzebne do
    // liczników kart, ale nie jest renderowany przed wyborem linii.
    if (this.scopeId === "all") return this.builder.build("fan");
    return this.builder.buildResearchGraph(this.evidencePersonId, this.expandedEvidenceEventIds);
  }

  build() {
    this.fullGraph = this.buildFullGraph();
    this.applyScope(false);
  }

  applyScope(runSimulation = true) {
    for (const item of this.nodes) {
      this.positionCache.set(item.id, { x: item.x, y: item.y, fixed: item.fixed });
    }
    const result = this.scopeFacade.apply(this.fullGraph, {
      scopeIds: [this.scopeId],
      depth: this.depth,
      includeBridges: this.includeBridges,
      includeCandidates: this.includeCandidates,
      candidateRole: this.candidateRole,
    });
    this.scopeResult = result;
    this.applyViewReduction(runSimulation);
  }

  applyViewReduction(runSimulation = true) {
    for (const item of this.nodes) {
      this.positionCache.set(item.id, { x: item.x, y: item.y, fixed: item.fixed });
    }
    const sourceReportedScope = this.scopeFacade.scopeById.get(this.scopeId)?.scopeKind === "source_reported_network";
    const projected = this.scopeId === "all" ? this.scopeResult : this.modeProjector.project(this.scopeResult, this.mode, {
      showDocuments: this.showDocuments,
      showHouseholds: this.showHouseholds,
      identityStatus: this.identityStatus,
      minimumOccurrences: this.minimumOccurrences,
      fanRole: sourceReportedScope ? "all" : this.fanRole,
      clusterType: this.clusterType,
      minimumClusterMembers: this.minimumClusterMembers,
      expandedClusterIds: this.expandedClusterIds,
    });
    const result = this.viewReducer.reduce(projected);
    this.renderLineNavigator(result);
    const presentationNodes = result.nodes.map((item) => ({
      ...item,
      evidenceGraphVisible: item.kind === "identity" && item.item?.personId === this.evidencePersonId,
      ...(item.contextualEvidenceFor === this.evidencePersonId
        ? { focusRole: "neighbor", labelVisible: true }
        : {}),
    }));
    this.nodes = this.semanticLayout.apply({
      nodes: presentationNodes,
      edges: result.edges,
      layoutEdges: result.layoutEdges,
      previousPositions: this.positionCache,
      mode: this.mode,
    });
    this.layoutGuides = this.semanticLayout.guides;
    this.nodeById = new Map(this.nodes.map((item) => [item.id, item]));
    this.edges = result.edges.filter((edge) => this.nodeById.has(edge.from) && this.nodeById.has(edge.to));
    this.layoutEdges = (result.layoutEdges || []).filter((edge) => this.nodeById.has(edge.from) && this.nodeById.has(edge.to));
    if (this.selected && !this.nodeById.has(this.selected.id)) {
      this.selected = null;
      if (sourceReportedScope) this.inspectCurrentContext();
      else this.onInspect({ kind: "hidden-selection" });
    } else if (this.selected) {
      this.selected = this.nodeById.get(this.selected.id);
    }
    if (this.selectedEdge) {
      const selectedEdge = this.edges.find((edge) => edge.id === this.selectedEdge.id) || null;
      this.selectedEdge = selectedEdge;
      if (!selectedEdge) this.inspectCurrentContext();
    }
    const counts = result.counts;
    const clusterNodes = result.nodes.filter((item) => item.kind === "cluster");
    const summary = result.overview
      ? `${result.overviewScopeCount} linii · ${result.overviewBackingNodeCount} elementów dostępnych po rozwinięciu`
      : sourceReportedScope
        ? `${polishCount(result.sourceReportedOccurrenceCount, "kandydat", "kandydatów", "kandydatów")} z drzewa zewnętrznego · ${polishCount(result.sourceReportedRelationshipCount, "relacja nierozstrzygnięta", "relacje nierozstrzygnięte", "relacji nierozstrzygniętych")} · ${candidateRoleLabel(result.sourceReportedCandidateRole)} · stały zakres`
      : this.mode === "clusters"
        ? `${polishCount(clusterNodes.length, "klaster", "klastry", "klastrów")} · ${polishCount(clusterNodes.reduce((sum, item) => sum + Number(item.item?.memberCount || 0), 0), "osoba", "osoby", "osób")} · ${polishCount(clusterNodes.reduce((sum, item) => sum + Number(item.item?.actCount || 0), 0), "akt", "akty", "aktów")} · ${polishCount(result.edges.filter((item) => item.kind === "candidate").length, "trop między klastrami", "tropy między klastrami", "tropów między klastrami")}`
        : this.mode === "people"
          ? `${polishCount(result.nodes.filter((item) => item.kind === "identity").length, "rozstrzygnięta osoba", "rozstrzygnięte osoby", "rozstrzygniętych osób")} · ${polishCount(result.nodes.filter((item) => item.kind === "occurrence").length, "osobne wystąpienie", "osobne wystąpienia", "osobnych wystąpień")} · ${polishCount(result.edges.filter((item) => item.kind === "candidate").length, "kandydatura", "kandydatury", "kandydatur")}`
          : `${counts.core} rdzeń · ${counts.fanDirect + counts.fanExtended} FAN · ${counts.bridge} łączniki · ${counts.candidate} kandydatury · ${counts.hidden} ukrytych`;
    $("#graph-filter-summary").textContent = summary;
    $("#graph-scope-warning").textContent = result.warnings.join(" ");
    $("#graph-scope-warning").hidden = !result.warnings.length;
    this.canvas.dataset.nodeCount = String(this.nodes.length);
    this.canvas.dataset.edgeCount = String(this.edges.length);
    this.canvas.dataset.scopeNodeCount = String(result.sourceNodeCount);
    this.canvas.dataset.scopeEdgeCount = String(result.sourceEdgeCount);
    const focusedPublicProfileIds = sourceReportedScope
      ? (result.focusedNodeIds || [])
          .map((id) => this.nodeById.get(id)?.publicProfileId)
          .filter(Boolean)
      : [];
    this.canvas.dataset.focusedNodeId = sourceReportedScope
      ? focusedPublicProfileIds[0] || ""
      : result.focusedNodeId || "";
    this.canvas.dataset.focusedNodeIds = sourceReportedScope
      ? focusedPublicProfileIds.join(",")
      : (result.focusedNodeIds || []).join(",");
    this.canvas.dataset.firstCircleCount = String(result.firstCircleNodeIds.length);
    this.canvas.dataset.collapsedIdentityCount = String(result.collapsedIdentityCount);
    this.canvas.dataset.expandedIdentityIds = sourceReportedScope ? "" : result.expandedIdentityIds.join(",");
    this.canvas.dataset.dimmedNodeCount = String(this.nodes.filter((item) => item.focusRole === "dimmed").length);
    this.canvas.dataset.visibleLabelCount = String(this.nodes.filter((item) => item.labelVisible).length);
    this.canvas.dataset.searchMatchCount = String(this.nodes.filter((item) => item.searchMatch).length);
    this.canvas.dataset.identityNodeCount = String(this.nodes.filter((item) => item.kind === "identity").length);
    this.canvas.dataset.occurrenceNodeCount = String(this.nodes.filter((item) => item.kind === "occurrence").length);
    this.canvas.dataset.eventNodeCount = String(this.nodes.filter((item) => item.kind === "event").length);
    this.canvas.dataset.sourceNodeCount = String(this.nodes.filter((item) => item.kind === "source").length);
    this.canvas.dataset.clusterNodeCount = String(this.nodes.filter((item) => item.kind === "cluster").length);
    this.canvas.dataset.externalRelationCount = String(result.sourceReportedRelationshipCount
      ?? this.edges.filter((item) => item.kind === "external-relation").length);
    this.canvas.dataset.expandedClusterIds = sourceReportedScope ? "" : [...this.expandedClusterIds].sort().join(",");
    this.canvas.dataset.documentsVisible = String(this.showDocuments && this.mode === "fan");
    this.canvas.dataset.evidencePersonId = sourceReportedScope ? "" : this.evidencePersonId || "";
    this.canvas.dataset.expandedEvidenceEventIds = sourceReportedScope ? "" : [...this.expandedEvidenceEventIds].sort().join(",");
    this.canvas.dataset.contextualEvidenceNodeCount = sourceReportedScope
      ? "0"
      : String(this.nodes.filter((item) => item.contextualEvidenceFor === this.evidencePersonId).length);
    this.canvas.dataset.scopeId = this.scopeId;
    this.canvas.dataset.depth = String(this.depth);
    this.canvas.dataset.mode = this.mode;
    this.canvas.dataset.candidateRole = sourceReportedScope ? this.candidateRole : "";
    this.canvas.dataset.anchorState = sourceReportedScope ? result.sourceReportedAnchorState || "missing" : "";
    this.canvas.dataset.semanticLayout = "true";
    this.canvas.dataset.layoutMode = this.nodes[0]?.layoutMode || "layered";
    this.canvas.dataset.semanticRingCount = String(this.layoutGuides.length);
    this.canvas.dataset.semanticClusterCount = String(new Set(this.nodes.map((item) => item.semanticClusterId)).size);
    this.canvas.dataset.scopeSummaryCount = String(this.nodes.filter((item) => item.kind === "scope").length);
    this.canvas.setAttribute("aria-label", `Interaktywna sieć rodzinna. ${summary}`);
    this.updateMethodNote();
    this.updateEvidenceContext();
    this.updateControlValues();
    this.renderMobileList();
    this.draw();
    if (runSimulation) this.simulate();
  }

  simulate() {
    this.cancelSimulation?.();
    if (this.scopeId === "all") {
      this.cancelSimulation = null;
      return;
    }
    this.cancelSimulation = runForceLayout({
      nodes: this.nodes,
      edges: [...this.edges, ...this.layoutEdges],
      nodeById: this.nodeById,
      onTick: () => this.draw(),
    });
  }

  fit() {
    if (this.scopeId === "all") return;
    if (!this.nodes.length || !this.width || !this.height) return;
    this.needsInitialFit = false;
    const legendOpen = document.querySelector(".graph-legend-panel")?.open === true;
    const topInset = legendOpen ? (this.width <= 760 ? 126 : 112) : 28;
    const bottomInset = 24;
    const sideInset = 24;
    const xs = this.nodes.map((item) => item.x);
    const ys = this.nodes.map((item) => item.y);
    const nodeMargin = this.nodes.some((item) => item.kind === "scope") ? 170 : 60;
    const minX = Math.min(...xs) - nodeMargin;
    const maxX = Math.max(...xs) + nodeMargin;
    const minY = Math.min(...ys) - nodeMargin;
    const maxY = Math.max(...ys) + nodeMargin;
    const usableWidth = Math.max(1, this.width - sideInset * 2);
    const usableHeight = Math.max(1, this.height - topInset - bottomInset);
    const orbital = this.nodes[0]?.layoutMode === "orbital";
    const minimumScale = orbital ? .1 : ["people", "clusters"].includes(this.mode) ? .28 : .38;
    if (orbital && this.nodes.some((item) => item.semanticBand === "center")) {
      const centerX = sideInset + usableWidth / 2;
      const centerRatio = this.scopeId === "all" ? .5 : .66;
      const centerY = topInset + usableHeight * centerRatio;
      const leftExtent = Math.max(60, -minX);
      const rightExtent = Math.max(60, maxX);
      const topExtent = Math.max(60, -minY);
      const bottomExtent = Math.max(60, maxY);
      const fitted = Math.min(
        (usableWidth / 2) / leftExtent,
        (usableWidth / 2) / rightExtent,
        (usableHeight * centerRatio) / topExtent,
        (usableHeight * (1 - centerRatio)) / bottomExtent,
      );
      this.scale = Math.min(1.35, Math.max(minimumScale, fitted));
      this.offsetX = centerX;
      this.offsetY = centerY;
    } else {
      this.scale = Math.min(1.35, Math.max(minimumScale, Math.min(usableWidth / (maxX - minX), usableHeight / (maxY - minY))));
      this.offsetX = sideInset + usableWidth / 2 - ((minX + maxX) / 2) * this.scale;
      this.offsetY = topInset + usableHeight / 2 - ((minY + maxY) / 2) * this.scale;
    }
    this.draw();
  }

  screenX(value) { return value * this.scale + this.offsetX; }
  screenY(value) { return value * this.scale + this.offsetY; }

  hit(x, y) {
    return this.renderer.hit(this, x, y);
  }

  search(value) {
    this.query = value.trim().toLocaleLowerCase("pl-PL");
    this.viewReducer.setQuery(value);
    this.applyViewReduction(false);
    const match = this.query && this.nodes
      .filter((item) => item.searchMatch)
      .sort((left, right) => (left.searchRank ?? 99) - (right.searchRank ?? 99) || left.id.localeCompare(right.id))[0];
    if (match?.kind === "scope" && this.scopeId === "all") {
      this.activateScope(match.ref, { preserveQuery: true });
      this.search(value);
      return;
    }
    if (match) {
      this.offsetX = this.width / 2 - match.x * this.scale;
      this.offsetY = this.height / 2 - match.y * this.scale;
      this.select(match);
    }
    this.draw();
  }

  select(item) {
    this.selectedEdge = null;
    this.viewReducer.focus(item.id);
    this.applyViewReduction(false);
    this.selected = this.nodeById.get(this.viewReducer.focusedNodeId) || null;
    this.renderMobileList();
    this.draw();
    this.onInspect(this.selected);
    this.persistState();
  }

  selectEdge(edge) {
    if (edge?.kind !== "external-relation") return;
    this.viewReducer.focus(null);
    this.selected = null;
    this.selectedEdge = null;
    this.applyViewReduction(false);
    this.selectedEdge = this.edges.find((item) => item.id === edge.id) || null;
    if (!this.selectedEdge) {
      this.inspectCurrentContext();
      return;
    }
    this.draw();
    this.onInspect({
      ...this.selectedEdge,
      fromNode: this.nodeById.get(this.selectedEdge.from) || null,
      toNode: this.nodeById.get(this.selectedEdge.to) || null,
    });
    this.persistState();
  }

  activateScope(scopeId, { preserveQuery = false, preferredMode = null, candidateRole = null } = {}) {
    const scope = this.scopeFacade.scopeById.get(scopeId);
    if (!scope) return;
    this.scopeId = scopeId;
    const sourceReportedScope = scope.scopeKind === "source_reported_network";
    if (preferredMode || sourceReportedScope) {
      this.mode = normalizeGraphModeForScope(preferredMode || this.mode, scope);
      this.viewReducer.setEvidenceMode(this.mode);
      $$('[data-graph-mode]').forEach((item) => item.classList.toggle("is-active", item.dataset.graphMode === this.mode));
    }
    this.depth = sourceReportedScope ? 0 : preserveQuery ? "all" : 0;
    this.includeBridges = !sourceReportedScope;
    this.showDocuments = false;
    this.showHouseholds = false;
    this.candidateRole = sourceReportedScope ? normalizedCandidateRole(candidateRole) : "all";
    if (sourceReportedScope) {
      this.evidencePersonId = null;
      this.expandedEvidenceEventIds.clear();
      this.expandedClusterIds.clear();
      this.viewReducer.expandedIdentityIds.clear();
    }
    this.viewReducer.focus(null);
    this.selected = null;
    this.selectedEdge = null;
    if (!preserveQuery) {
      this.query = "";
      this.viewReducer.setQuery("");
      const search = $("#graph-search");
      if (search) search.value = "";
    }
    this.updateControlValues();
    this.build();
    this.fit();
    this.simulate();
    this.inspectCurrentContext();
    this.persistState();
  }

  resetExternalNetworkDeepLink(message = "Nie znaleziono bezpiecznego zakresu wskazanego w adresie. Przywrócono widok wszystkich linii.") {
    this.scopeId = "all";
    this.depth = this.payload.graphScopes?.defaultDepth ?? 1;
    this.includeBridges = false;
    this.includeCandidates = DEFAULT_INCLUDE_CANDIDATES;
    this.showDocuments = false;
    this.showHouseholds = false;
    this.candidateRole = "all";
    this.viewReducer.focus(null);
    this.selected = null;
    this.selectedEdge = null;
    this.updateControlValues();
    this.build();
    this.cancelSimulation?.();
    this.onInspect(null);
    const warning = $("#graph-scope-warning");
    if (warning) {
      warning.textContent = message;
      warning.hidden = !message;
    }
    this.persistState();
    return false;
  }

  clearExternalNetworkDeepLink() {
    if (this.scopeFacade.scopeById.get(this.scopeId)?.scopeKind !== "source_reported_network") return false;
    return this.resetExternalNetworkDeepLink("");
  }

  applyExternalNetworkDeepLink({ networkId, publicProfileId = null, candidateRole = "all" } = {}) {
    const resolved = this.scopeFacade.resolveSourceReportedDeepLink({ networkId, publicProfileId, candidateRole });
    if (!resolved) return this.resetExternalNetworkDeepLink();
    const { scopeId, occurrenceId } = resolved;
    this.activateScope(scopeId, { preferredMode: "fan", candidateRole: resolved.candidateRole });
    if (!occurrenceId) return true;
    const target = this.nodeById.get(`occ:${occurrenceId}`);
    if (!target || target.publicProfileId !== publicProfileId) return this.resetExternalNetworkDeepLink();
    this.select(target);
    return true;
  }

  clearFocus() {
    this.viewReducer.focus(null);
    this.selected = null;
    this.selectedEdge = null;
    this.applyViewReduction(false);
    this.draw();
    this.inspectCurrentContext();
    this.persistState();
  }

  focusPersonContext(context) {
    const occurrenceIds = new Set(context?.occurrenceIds || []);
    const matchingScopes = this.scopeFacade.scopes()
      .map((scope) => ({
        scope,
        matches: (scope.scopeKind === "source_reported_network" ? scope.exactOccurrenceIds : scope.seedOccurrenceIds || [])
          .filter((id) => occurrenceIds.has(id)).length,
      }))
      .filter((item) => item.matches > 0)
      .sort((left, right) => right.matches - left.matches || left.scope.id.localeCompare(right.scope.id));
    const currentScope = this.scopeFacade.scopeById.get(this.scopeId);
    const currentMatches = (currentScope?.scopeKind === "source_reported_network"
      ? currentScope.exactOccurrenceIds
      : currentScope?.seedOccurrenceIds || []).filter((id) => occurrenceIds.has(id)).length;
    if (this.scopeId === "all" || !currentScope || (matchingScopes.length && currentMatches === 0)) {
      this.scopeId = matchingScopes[0]?.scope.id || this.scopeFacade.scopes()[0]?.id || "all";
    }
    if (this.scopeId === "all") return false;
    const sourceReportedScope = this.scopeFacade.scopeById.get(this.scopeId)?.scopeKind === "source_reported_network";
    this.mode = normalizeGraphModeForScope("people", this.scopeFacade.scopeById.get(this.scopeId));
    this.depth = sourceReportedScope ? 0 : "all";
    this.includeBridges = !sourceReportedScope;
    this.viewReducer.setEvidenceMode(this.mode);
    this.updateControlValues();
    this.build();
    const requested = [
      ...(context.graphIdentityIds || []),
      ...(context.occurrenceIds || []).map((id) => `occ:${id}`),
    ];
    this.viewReducer.focusMany(requested);
    this.applyViewReduction(false);
    const focused = [...this.viewReducer.focusedNodeIds]
      .map((id) => this.nodeById.get(id))
      .filter(Boolean);
    if (focused.length) {
      const x = focused.reduce((sum, item) => sum + item.x, 0) / focused.length;
      const y = focused.reduce((sum, item) => sum + item.y, 0) / focused.length;
      this.offsetX = this.width / 2 - x * this.scale;
      this.offsetY = this.height / 2 - y * this.scale;
    }
    this.selected = focused[0] || null;
    this.selectedEdge = null;
    this.canvas.dataset.focusedPersonKey = context.personKey || "";
    this.draw();
    this.onInspect(this.selected);
    this.persistState();
    return focused.length > 0;
  }

  toggleIdentity(personId) {
    if (this.mode === "evidence") return;
    const identityNodeId = `identity:${personId}`;
    this.viewReducer.toggleIdentity(personId);
    this.viewReducer.focus(identityNodeId);
    this.applyViewReduction(true);
    this.selected = this.nodeById.get(identityNodeId) || null;
    this.selectedEdge = null;
    this.draw();
    this.onInspect(this.selected);
    this.persistState();
  }

  toggleEvidence(personId) {
    const identityNodeId = `identity:${personId}`;
    this.evidencePersonId = this.evidencePersonId === personId ? null : personId;
    this.expandedEvidenceEventIds.clear();
    this.viewReducer.focus(identityNodeId);
    this.build();
    this.selected = this.nodeById.get(identityNodeId) || null;
    this.selectedEdge = null;
    this.simulate();
    this.draw();
    this.onInspect(this.selected);
    this.persistState();
  }

  toggleEvidenceEvent(eventId) {
    if (!this.evidencePersonId || typeof eventId !== "string") return;
    if (this.expandedEvidenceEventIds.has(eventId)) this.expandedEvidenceEventIds.delete(eventId);
    else this.expandedEvidenceEventIds.add(eventId);
    const eventNodeId = `event:${eventId}`;
    this.viewReducer.focus(eventNodeId);
    this.build();
    this.selected = this.nodeById.get(eventNodeId) || null;
    this.selectedEdge = null;
    this.simulate();
    this.draw();
    this.onInspect(this.selected);
    this.persistState();
  }

  toggleCluster(clusterId) {
    if (this.mode !== "clusters" || typeof clusterId !== "string") return;
    if (this.expandedClusterIds.has(clusterId)) this.expandedClusterIds.delete(clusterId);
    else this.expandedClusterIds.add(clusterId);
    this.viewReducer.focus(null);
    this.selected = null;
    this.selectedEdge = null;
    this.applyViewReduction(true);
    this.draw();
    this.onInspect(null);
    this.persistState();
  }

  persistState() {
    const sourceReportedScope = this.scopeFacade.scopeById.get(this.scopeId)?.scopeKind === "source_reported_network";
    const pinned = new Map(
      [...this.positionCache.entries()]
        .filter(([, value]) => value?.fixed)
        .map(([id, value]) => [id, { id, x: value.x, y: value.y }]),
    );
    for (const item of this.nodes) {
      if (item.fixed) pinned.set(item.id, { id: item.id, x: item.x, y: item.y });
    }
    this.stateStore.save(graphViewStateForPersistence({
      mode: this.mode,
      scopeId: this.scopeId,
      depth: this.depth,
      focusedNodeId: this.viewReducer.focusedNodeId,
      expandedIdentityIds: [...this.viewReducer.expandedIdentityIds].sort(),
      pinnedPositions: [...pinned.values()].sort((left, right) => left.id.localeCompare(right.id)),
      camera: { scale: this.scale, offsetX: this.offsetX, offsetY: this.offsetY },
    }, { sourceReportedScope }));
  }

  draw() {
    if (this.scopeId === "all") return;
    this.renderer.draw(this, (item) => this.searchText(item));
  }
}
