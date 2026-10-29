import { $, $$, node } from "../core/dom.mjs";
import { polishCount } from "../core/formatting.mjs";
import { EXTERNAL_NETWORK_FILTERS, FamilyTreeModel } from "./family-tree-model.mjs";
import { FamilyTreeLayout } from "./family-tree-layout.mjs";
import { FamilyTreeRenderer } from "./family-tree-renderer.mjs";
import { familyConnectorGeometry } from "./family-tree-geometry.mjs";
import { buildFamilyTreeInspectorModel, buildSourceReportedTreeInspectorModel } from "./family-tree-inspector.mjs";
import { TREE_CARD_GEOMETRY, presentTreePersonCard, semanticTreeCardVariant } from "./person-card-presenter.mjs";
import { projectAncestorScope } from "./ancestor-scope-projector.mjs";
import { AncestorVerticalLayout } from "./ancestor-vertical-layout.mjs";
import { ancestorConnectorsGeometry } from "./ancestor-connector-geometry.mjs";
import { layoutAncestorFan } from "./ancestor-fan-layout.mjs";
import { AncestorFanRenderer } from "./ancestor-fan-renderer.mjs";
import { sourceReportedNetworkPresentation } from "../graph/graph-scope.mjs";
import { FocusedAncestryView } from "./focused-ancestry-view.mjs";
import { focusedPersonProtected, focusedScenariosForPerson } from "./focused-ancestry-scenarios.mjs";
import { projectUnifiedFamilyTree } from "./unified-family-tree.mjs";
import { appendUnifiedAncestryEdges } from "./unified-family-tree-renderer.mjs";
import {
  layoutConditionalExternalOverlay,
  projectConditionalExternalOverlay,
} from "./conditional-external-overlay.mjs";
import {
  buildConditionalAncestryInspectorModel,
  conditionalAncestryRootOptions,
  conditionalAncestrySummary,
  defaultConditionalAncestryRootId,
  layoutConditionalAncestryOverlay,
  projectConditionalAncestry,
} from "./conditional-ancestry-overlay.mjs";

const MIN_OVERVIEW_SCALE = .2;
const MIN_FOCUS_SCALE = .68;
const DEFAULT_HOME_SCALE = .68;
const DEFAULT_ANCESTOR_VISIBLE_DEPTH = 4;
const MIN_ANCESTOR_VERTICAL_SCALE = .78;
const MIN_ANCESTOR_FAN_SCALE = .9;
const MAX_SCALE = 1.35;
const CAMERA_PADDING = 36;
const PARENT_CAMERA_PADDING = 24;
const UNIFIED_CARD_HEIGHT = 134;
const UNIFIED_ROW_GAP = 198;
const EXTERNAL_CAMERA_STORAGE_KEY = "genea:tree-external-camera:v1";
const EXTERNAL_CAMERA_HISTORY_KEY = "geneaExternalTreeCamera";
const FOCUSED_ANCESTRY_STORAGE_KEY = "genea:tree-focused-ancestry:v1";

function finiteCameraNumber(value, minimum, maximum) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.max(minimum, Math.min(maximum, parsed)) : null;
}

export class FamilyTree {
  constructor(tree, { dataIndex, onInspect }) {
    this.dataIndex = dataIndex;
    this.onInspect = onInspect;
    this.publicTree = tree;
    this.tree = tree;
    this.model = new FamilyTreeModel(tree);
    this.mode = "all";
    this.unifiedExpandedIds = new Set();
    this.unifiedCollapsedIds = new Set();
    this.unifiedProjection = null;
    this.unifiedCandidatesEnabled = true;
    this.scale = 1;
    this.selectedId = tree?.rootPersonId || null;
    this.query = "";
    this.cardWidth = TREE_CARD_GEOMETRY.width;
    this.cardHeight = TREE_CARD_GEOMETRY.height;
    this.cardVariant = "standard";
    this.rowGap = 150;
    this.top = 54;
    this.layout = new FamilyTreeLayout({ cardWidth: this.cardWidth, cardHeight: this.cardHeight, rowGap: this.rowGap, top: this.top });
    this.ancestorVerticalLayout = new AncestorVerticalLayout({ cardWidth: this.cardWidth, cardHeight: this.cardHeight, rowGap: this.rowGap, top: this.top });
    this.scroll = $("#tree-scroll");
    this.inner = $("#tree-inner");
    this.surface = $("#tree-surface");
    this.layer = $("#tree-node-layer");
    this.svg = $("#tree-connectors");
    this.renderer = new FamilyTreeRenderer(this.svg);
    this.ancestorFanRenderer = new AncestorFanRenderer(this.svg, {
      onPersonClick: (personId, sector) => this.select(personId, { ancestorSlotId: sector.slotId }),
    });
    this.personById = this.model.personById;
    this.branchById = this.model.branchById;
    this.networkById = this.model.networkById;
    this.branchId = tree?.branches?.[0]?.id || null;
    this.networkId = this.model.defaultNetworkId();
    this.candidateRoleFilter = "all";
    this.conditionalOverlayEnabled = false;
    this.conditionalOverlayNetworkId = (tree?.conditionalTreeAttachments || [])
      .map((attachment) => attachment?.networkId)
      .find((networkId) => this.model.network(networkId))
      || this.model.defaultNetworkId();
    this.conditionalOverlayRoleFilter = "all";
    this.conditionalOverlayProjection = null;
    this.conditionalOverlayLayout = null;
    this.conditionalOverlayPersonIds = new Set();
    this.conditionalOverlayCanonicalCount = 0;
    this.conditionalAncestry = tree?.conditionalAncestry || null;
    this.conditionalAncestryRoots = conditionalAncestryRootOptions(this.conditionalAncestry);
    this.conditionalAncestryRootId = defaultConditionalAncestryRootId(
      this.conditionalAncestryRoots,
      tree?.rootPersonId || null,
    );
    this.conditionalAncestryEnabled = false;
    this.conditionalAncestryProjection = null;
    this.conditionalAncestryLayout = null;
    this.conditionalAncestryPersonIds = new Set();
    this.focusedAncestryEnabled = false;
    this.focusedAncestryScenario = null;
    this.focusedAncestrySeed = null;
    this.focusedAncestryReturnSelection = null;
    this.ancestorLayoutMode = "vertical";
    this.ancestorFocusId = tree?.rootPersonId || null;
    this.ancestorProjection = null;
    this.ancestorMaxDepth = DEFAULT_ANCESTOR_VISIBLE_DEPTH;
    this.selectedAncestorSlotId = null;
    this.collapsedAncestors = new Set();
    this.collapsedUnions = new Set(this.model.defaultCollapsedUnionOwners());
    this.expandedFamilies = new Set();
    this.activeUnionByOwner = new Map();
    this.branchPicker = $("#tree-branch-picker");
    this.branchSelect = $("#tree-branch-select");
    this.networkFilterFieldset = null;
    this.networkFilterSelect = null;
    this.networkFilterSummary = null;
    this.conditionalOverlayFieldset = null;
    this.conditionalOverlayToggle = null;
    this.conditionalOverlayNetworkSelect = null;
    this.conditionalOverlayRoleSelect = null;
    this.conditionalOverlayStatus = null;
    this.conditionalAncestryFieldset = null;
    this.conditionalAncestryToggle = null;
    this.conditionalAncestryRootSelect = null;
    this.conditionalAncestryStatus = null;
    this.ancestorLayoutControls = $("#tree-ancestor-layout-controls");
    this.ancestorDepthControls = $("#tree-ancestor-depth-controls");
    this.ancestorDepthStatus = $("#tree-ancestor-depth-status");
    this.ancestorDepthMore = $("#tree-ancestor-depth-more");
    this.ancestorDepthAll = $("#tree-ancestor-depth-all");
    this.layoutGenerationSpan = 0;
    this.cameraMode = "overview";
    this.presentationMode = "canvas";
    this.panFrame = null;
    this.pendingPan = null;
    this.wheelZoomFrame = null;
    this.pendingWheelZoom = null;
    this.externalCameraFrame = null;
    this.suppressExternalCameraPersistence = false;
    this.externalCameraWasRestored = false;
    this.allTreeWidth = Math.max(1440, this.layout.requiredWidth(tree?.people || [], 180, tree?.families || []));
    this.baseWidth = this.allTreeWidth;
    if (tree?.subtitle) $("#tree-description").textContent = tree.subtitle;
    this.ensureNetworkModeButton();
    this.ensureNetworkFilterControl();
    this.ensureConditionalOverlayControls();
    this.ensureConditionalAncestryControls();
    this.ensureFocusedAncestryControls();
    this.ensureUnifiedTreeControls();
    this.bindAncestorDepthControls();
    this.renderBranchOptions();
    this.bindPan();
    this.render();
    this.resizeObserver = new ResizeObserver(() => this.handleResize());
    this.resizeObserver.observe(this.scroll);
    requestAnimationFrame(() => {
      if (!this.restoreFocusedAncestry()) this.home({ behavior: "auto" });
    });
  }

  readFocusedAncestryState() {
    try {
      const stored = JSON.parse(sessionStorage.getItem(FOCUSED_ANCESTRY_STORAGE_KEY) || "null");
      return stored && typeof stored === "object" && !Array.isArray(stored) ? stored : null;
    } catch { return null; }
  }

  unifiedTreeActive() {
    return ["all", "branch"].includes(this.mode) && !this.focusedAncestryEnabled
      && !this.conditionalOverlayEnabled && !this.conditionalAncestryEnabled;
  }

  ensureUnifiedTreeControls() {
    const toolbar = this.scroll.closest(".tree-stage").querySelector(".tree-toolbar");
    const advancedBody = node("div", { class: "tree-advanced-body" });
    this.unifiedAdvanced = node("details", { class: "tree-advanced-controls" },
      node("summary", {}, "Widoki i narzędzia"), advancedBody);
    this.unifiedCandidatesToggle = node("button", { type: "button", class: "quiet-button", "aria-pressed": "true",
      onclick: () => {
        this.unifiedCandidatesEnabled = !this.unifiedCandidatesEnabled;
        this.unifiedCandidatesToggle.setAttribute("aria-pressed", String(this.unifiedCandidatesEnabled));
        this.unifiedCandidatesToggle.textContent = this.unifiedCandidatesEnabled ? "Ukryj tropy rodzinne" : "Pokaż tropy rodzinne";
        this.renderKeepingPerson(this.selectedId);
      } }, "Ukryj tropy rodzinne");
    advancedBody.append(this.unifiedCandidatesToggle);
    for (const element of [...toolbar.querySelectorAll(".view-toggle, .segmented-control, .branch-picker, .tree-ancestor-layout-controls, .tree-ancestor-depth-controls, [data-tree-focused-open]")]) {
      advancedBody.append(element);
    }
    // The mode switch group is part of the advanced catalog, not the main tree.
    const modeGroup = toolbar.querySelector("[data-tree-mode]")?.parentElement;
    if (modeGroup) advancedBody.prepend(modeGroup);
    this.unifiedStatus = node("p", { class: "tree-unified-status", role: "status" });
    const home = node("button", { type: "button", class: "quiet-button", "aria-label": "Wróć do osoby głównej",
      onclick: () => this.home({ behavior: "auto" }) }, "Osoba główna");
    toolbar.append(home, this.unifiedAdvanced);
    toolbar.insertAdjacentElement("afterend", this.unifiedStatus);
  }

  unifiedParentControlState(personId, { base = false, conditional = false } = {}) {
    const baseExpanded = base && !this.collapsedAncestors.has(personId);
    const conditionalCollapsed = conditional && (
      this.unifiedCollapsedIds.has(personId)
      || this.unifiedProjection?.collapsedIds?.has(personId)
    );
    const conditionalExpanded = conditional && !conditionalCollapsed && (
      this.unifiedExpandedIds.has(personId)
      || (this.unifiedProjection?.edges || []).some((edge) => edge.fromDisplayId === personId)
    );
    return {
      expanded: baseExpanded || conditionalExpanded,
      base,
      conditional,
    };
  }

  hasUnifiedConditionalParents(personId) {
    return Boolean(this.unifiedProjection && this.unifiedCandidatesEnabled) && (
      (this.unifiedProjection.edges || []).some((edge) => edge.fromDisplayId === personId)
      || this.unifiedProjection.expandableIds?.has(personId)
      || this.unifiedProjection.collapsedIds?.has(personId)
      || this.unifiedExpandedIds.has(personId)
      || this.unifiedCollapsedIds.has(personId)
    );
  }

  toggleUnifiedParents(personId, capabilities = {}) {
    const state = this.unifiedParentControlState(personId, capabilities);
    if (state.expanded) {
      if (state.base) this.collapsedAncestors.add(personId);
      if (state.conditional) {
        this.unifiedExpandedIds.delete(personId);
        this.unifiedCollapsedIds.add(personId);
      }
    } else {
      if (state.base) this.collapsedAncestors.delete(personId);
      if (state.conditional) {
        this.unifiedCollapsedIds.delete(personId);
        this.unifiedExpandedIds.add(personId);
      }
    }
    this.selectedId = personId;
    // A structure toggle keeps the anchored camera even if status wrapping
    // changes the canvas dimensions; the next explicit focus can frame again.
    this.cameraMode = "manual";
    this.renderKeepingPerson(personId);
  }

  syncCanvasGeometry() {
    const unified = this.unifiedTreeActive();
    const cardHeight = unified ? UNIFIED_CARD_HEIGHT : TREE_CARD_GEOMETRY.height;
    const rowGap = unified ? UNIFIED_ROW_GAP : 150;
    if (this.cardHeight === cardHeight && this.rowGap === rowGap) return;
    this.cardHeight = cardHeight;
    this.rowGap = rowGap;
    this.layout = new FamilyTreeLayout({
      cardWidth: this.cardWidth,
      cardHeight: this.cardHeight,
      rowGap: this.rowGap,
      top: this.top,
    });
  }

  ensureFocusedAncestryControls() {
    this.focusedAncestryButton = node("button", {
      type: "button", class: "quiet-button", "aria-pressed": "false",
      "data-tree-focused-open": "true",
      onclick: () => this.focusedAncestryEnabled ? this.closeFocusedAncestry() : this.openFocusedAncestry(),
    }, "Drzewo skupione");
    $("#tree-fit").insertAdjacentElement("beforebegin", this.focusedAncestryButton);
    this.focusedAncestryContainer = node("section", { class: "tree-focused-ancestry", "aria-label": "Drzewo skupione", hidden: "" });
    this.scroll.insertAdjacentElement("beforebegin", this.focusedAncestryContainer);
    this.focusedAncestryView = new FocusedAncestryView({
      container: this.focusedAncestryContainer,
      initialState: this.readFocusedAncestryState()?.view || null,
      onExit: () => this.closeFocusedAncestry(),
      onSelect: (selected) => {
        this.selectedId = selected.person.id;
        this.showInspector(selected.person);
        document.dispatchEvent(new CustomEvent("genea:tree-person-selected", { detail: { personId: selected.person.id, ancestorSlotId: null } }));
      },
      onStateChange: () => this.persistFocusedAncestry(),
    });
    this.focusedAncestryScenarioPicker = node("label", { class: "tree-focused-scenario-picker", hidden: "" },
      node("span", {}, "Podstawa ścieżki"),
      node("select", { "aria-label": "Podstawa ścieżki skupionej", onchange: (event) => this.setFocusedAncestryScenario(event.currentTarget.value) }));
    this.focusedAncestryContainer.insertAdjacentElement("beforebegin", this.focusedAncestryScenarioPicker);
  }

  focusedSeedPerson(seed) {
    if (seed?.treePersonId) return this.personById.get(seed.treePersonId) || null;
    if (seed?.networkId && seed?.publicProfileId) return [...this.personById.values()].find((person) => (
      person.sourceReported && person.sourceReportedNetworkId === seed.networkId && person.publicProfileId === seed.publicProfileId
    )) || null;
    if (seed?.conditionalNodeId) {
      for (const root of this.conditionalAncestryRoots) {
        const projection = projectConditionalAncestry({ conditionalAncestry: this.conditionalAncestry, rootId: root.id,
          canonicalPeople: this.tree?.people || [], visibleTreePersonIds: (this.tree?.people || []).map((p) => p.id) });
        const ancestryNode = projection.nodes.find((item) => item.id === seed.conditionalNodeId);
        if (ancestryNode) return { id: ancestryNode.displayId, name: ancestryNode.label, conditionalAncestry: true, conditionalAncestryNode: ancestryNode };
      }
    }
    return null;
  }

  openFocusedAncestry(person = null, { restore = false, scenarioKey = null } = {}) {
    const stored = this.readFocusedAncestryState();
    const seedPerson = person || (restore ? this.focusedSeedPerson(stored?.seed) : null)
      || this.renderedPersonById?.get(this.selectedId) || this.personById.get(this.selectedId)
      || this.personById.get(this.tree?.rootPersonId);
    if (!seedPerson) return false;
    const scenarios = focusedScenariosForPerson({ tree: this.tree, person: seedPerson, conditionalAncestry: this.conditionalAncestry });
    if (!scenarios.length) return false;
    if (!this.focusedAncestryEnabled) this.focusedAncestryReturnSelection = this.selectedId;
    this.focusedAncestrySeed = seedPerson.conditionalAncestry
      ? { conditionalNodeId: seedPerson.conditionalAncestryNode.id }
      : seedPerson.sourceReported ? { networkId: seedPerson.sourceReportedNetworkId, publicProfileId: seedPerson.publicProfileId }
        : { treePersonId: seedPerson.id };
    this.focusedAncestryScenarios = scenarios;
    const scenario = scenarios.find((item) => item.key === (scenarioKey || (restore ? stored?.scenarioKey : null))) || scenarios[0];
    const select = $("select", this.focusedAncestryScenarioPicker);
    select.replaceChildren(...scenarios.map((item) => node("option", { value: item.key }, item.label)));
    select.value = scenario.key;
    this.focusedAncestryEnabled = true;
    this.focusedAncestryScenario = scenario;
    const sameSeed = JSON.stringify(stored?.seed) === JSON.stringify(this.focusedAncestrySeed);
    this.focusedAncestryView.setScenario(scenario, restore || (!person && sameSeed) ? {} : { focusNodeId: scenario.entryNodeId || null });
    this.updateFocusedAncestryVisibility();
    this.updateHeading();
    this.persistFocusedAncestry();
    return true;
  }

  setFocusedAncestryScenario(key) {
    const scenario = this.focusedAncestryScenarios?.find((item) => item.key === key);
    if (!scenario) return false;
    this.focusedAncestryScenario = scenario;
    this.showEmptyInspector();
    this.focusedAncestryView.setScenario(scenario);
    this.updateHeading();
    this.persistFocusedAncestry();
    return true;
  }

  persistFocusedAncestry() {
    if (!this.focusedAncestryView) return;
    try {
      sessionStorage.setItem(FOCUSED_ANCESTRY_STORAGE_KEY, JSON.stringify({
        active: this.focusedAncestryEnabled, seed: this.focusedAncestrySeed,
        scenarioKey: this.focusedAncestryScenario?.key || null, view: this.focusedAncestryView.serializedState(),
      }));
    } catch { /* Browsing still works when session storage is unavailable. */ }
  }

  restoreFocusedAncestry() {
    const stored = this.readFocusedAncestryState();
    if (!stored?.active || !this.focusedSeedPerson(stored.seed)) return false;
    const params = new URLSearchParams(location.search);
    if (params.has("network") || params.has("person")) return false;
    return this.openFocusedAncestry(null, { restore: true, scenarioKey: stored.scenarioKey });
  }

  closeFocusedAncestry({ render = true } = {}) {
    if (!this.focusedAncestryEnabled) return;
    this.focusedAncestryEnabled = false;
    this.selectedId = this.focusedAncestryReturnSelection;
    this.updateFocusedAncestryVisibility();
    this.persistFocusedAncestry();
    if (render) { this.updateHeading(); this.render(); }
  }

  updateFocusedAncestryVisibility() {
    const active = this.focusedAncestryEnabled;
    this.focusedAncestryContainer.hidden = !active;
    this.focusedAncestryScenarioPicker.hidden = !active || (this.focusedAncestryScenarios?.length || 0) < 2;
    this.scroll.hidden = active;
    this.scroll.closest(".tree-stage")?.classList.toggle("is-focused-ancestry", active);
    $("#view-tree")?.classList.toggle("is-focused-ancestry", active);
    this.focusedAncestryButton.setAttribute("aria-pressed", String(active));
    $("#tree-search").placeholder = active ? "Szukaj w wybranej ścieżce…" : this.mode === "ancestors" ? "Znajdź osobę fokusową…" : "Szukaj osoby…";
  }

  inspectTreePerson(person, inspection) {
    if (this.focusedAncestryButton) {
      this.focusedAncestryButton.disabled = !this.focusedAncestryEnabled && focusedPersonProtected(person);
      this.focusedAncestryButton.title = this.focusedAncestryButton.disabled ? "Wybierz osobę dostępną w publicznym zakresie drzewa" : "Przeglądaj dalsze linie wybranej osoby";
    }
    const identitySources = this.unifiedProjection?.identitySourcesByPerson?.get(person?.id) || [];
    const sharedCardId = this.unifiedProjection?.aliases?.get(person?.id);
    if (inspection && sharedCardId && sharedCardId !== person.id) {
      inspection.structureActions = [
        { label: "Wróć do wspólnej karty w drzewie", run: () => this.select(sharedCardId) },
        ...(inspection.structureActions || []),
      ];
    }
    if (inspection && identitySources.length) {
      inspection.summary = [inspection.summary, "Jedna karta korzysta z zatwierdzonych powiązań profili. Ich relacje rodzinne pozostają oceniane osobno."].filter(Boolean).join(" ");
      inspection.structureActions = [
        ...(inspection.structureActions || []),
        ...identitySources.map((item) => ({ label: `Profil źródłowy: ${item.label || person.name}`,
          run: () => {
            const external = this.model.networkPersonByPublicProfileId(item.networkId, item.publicProfileId);
            if (external) this.showInspector(external);
          } })),
      ];
    }
    if (inspection && person && !focusedPersonProtected(person) && !this.focusedAncestryEnabled && !this.unifiedTreeActive()) inspection.structureActions = [
      { label: "Odkryj przodków w drzewie skupionym", run: () => this.openFocusedAncestry(person) },
      ...(inspection.structureActions || []),
    ];
    this.onInspect(inspection);
  }

  basePeople() {
    if (this.mode === "ancestors") {
      const projection = this.ancestorProjection || this.projectAncestors();
      const unique = new Map(projection.slots.map((slot) => [slot.personId, slot.person]));
      return [...unique.values()];
    }
    return this.model.basePeople(
      this.mode,
      this.mode === "network" ? this.networkId : this.branchId,
      this.candidateRoleFilter,
    );
  }

  projectAncestors() {
    this.ancestorProjection = projectAncestorScope(this.tree, {
      focusPersonId: this.ancestorFocusId || this.tree?.rootPersonId,
      maxDepth: this.ancestorMaxDepth,
    });
    return this.ancestorProjection;
  }

  bindAncestorDepthControls() {
    this.ancestorDepthMore?.addEventListener("click", () => this.showOneMoreAncestorGeneration());
    this.ancestorDepthAll?.addEventListener("click", () => this.showAllAncestorGenerations());
  }

  ancestorAllDepthLimit() {
    return Math.max(DEFAULT_ANCESTOR_VISIBLE_DEPTH, this.tree?.people?.length || 0);
  }

  updateAncestorDepthControls(projection, visibleDepth) {
    if (!this.ancestorDepthControls) return;
    this.ancestorDepthControls.hidden = this.mode !== "ancestors";
    const hasHiddenDepth = Boolean(projection?.truncated);
    if (this.ancestorDepthStatus) {
      this.ancestorDepthStatus.textContent = hasHiddenDepth
        ? `Pokazano ${visibleDepth} ${visibleDepth === 1 ? "pokolenie" : visibleDepth >= 2 && visibleDepth <= 4 ? "pokolenia" : "pokoleń"} wstecz. Dalsze zapisane relacje są ukryte przez limit widoku; nie oznacza to braku danych.`
        : "Brak pokoleń ukrytych przez limit widoku.";
    }
    if (this.ancestorDepthMore) this.ancestorDepthMore.disabled = !hasHiddenDepth;
    if (this.ancestorDepthAll) this.ancestorDepthAll.disabled = !hasHiddenDepth;
  }

  setAncestorMaxDepth(maxDepth) {
    const nextDepth = Math.max(0, Math.min(this.ancestorAllDepthLimit(), Math.floor(Number(maxDepth))));
    if (!Number.isFinite(nextDepth) || nextDepth === this.ancestorMaxDepth) return false;
    const selectedSlotId = this.selectedAncestorSlotId;
    this.ancestorMaxDepth = nextDepth;
    this.ancestorProjection = null;
    this.render();
    this.cameraMode = "focus";
    requestAnimationFrame(() => this.revealAncestorSlot(selectedSlotId, { behavior: "auto" }));
    return true;
  }

  showOneMoreAncestorGeneration() {
    return this.setAncestorMaxDepth(this.ancestorMaxDepth + 1);
  }

  showAllAncestorGenerations() {
    return this.setAncestorMaxDepth(this.ancestorAllDepthLimit());
  }

  activeNetwork() {
    return this.mode === "network" ? this.model.network(this.networkId) : null;
  }

  activeNetworkProjection() {
    return this.mode === "network"
      ? this.model.networkProjection(this.networkId, this.candidateRoleFilter)
      : null;
  }

  visiblePeople() {
    const base = this.basePeople();
    return this.model.visiblePeople({
      basePeople: base,
      collapsedAncestors: this.collapsedAncestors,
      collapsedUnions: this.collapsedUnions,
      expandedFamilies: this.expandedFamilies,
      activeUnion: (personId) => this.activeUnion(personId),
    });
  }

  additionalUnions(personId) {
    return this.model.additionalUnions(personId);
  }

  primaryChildId(family, children = family?.childIds || []) {
    return this.model.primaryChildId(family, children);
  }

  expandableFamilies(personId = null) {
    return this.model.expandableFamilies(personId, this.basePeople());
  }

  ownedUnions(personId) {
    return this.model.ownedUnions(personId);
  }

  activeUnion(personId) {
    return this.model.activeUnion(personId, this.activeUnionByOwner);
  }

  hasAncestorBranch(personId) {
    return this.model.hasAncestorBranch(personId, this.basePeople());
  }

  ensureNetworkModeButton() {
    if (!this.networkById.size || document.querySelector('[data-tree-mode="network"]')) return;
    const controls = document.querySelector('[aria-label="Zakres drzewa"]');
    controls?.append(node("button", { type: "button", "data-tree-mode": "network" }, "Sieci kandydackie"));
  }

  ensureNetworkFilterControl() {
    if (this.networkFilterFieldset || !this.networkById.size) return;
    this.networkFilterSelect = node("select", {
      "aria-label": "Klasyfikacja kandydatów w zewnętrznym drzewie",
      onchange: (event) => this.setCandidateRoleFilter(event.currentTarget.value),
    });
    this.networkFilterSummary = node("span", {
      class: "sr-only",
      role: "status",
      "aria-live": "polite",
    });
    this.networkFilterFieldset = node("fieldset", {
      class: "branch-picker tree-network-filter",
      hidden: true,
      style: "border:0;padding:0;margin:0",
    },
    node("legend", { class: "sr-only" }, "Filtr klasyfikacji kandydatów"),
    this.networkFilterSelect,
    this.networkFilterSummary);
    this.branchPicker.insertAdjacentElement("afterend", this.networkFilterFieldset);
    this.updateNetworkFilterControl();
  }

  updateNetworkFilterControl() {
    if (!this.networkFilterFieldset || !this.networkFilterSelect) return;
    this.networkFilterFieldset.hidden = this.mode !== "network";
    const counts = this.model.externalNetworkFilterCounts(this.networkId);
    const options = EXTERNAL_NETWORK_FILTERS.map((filter) => node("option", {
      value: filter.id,
    }, `${filter.label} (${counts[filter.id] || 0})`));
    this.networkFilterSelect.replaceChildren(...options);
    this.networkFilterSelect.value = this.candidateRoleFilter;
    const visible = this.model.networkProjection(this.networkId, this.candidateRoleFilter).people.length;
    this.networkFilterSummary.textContent = this.candidateRoleFilter === "all"
      ? `Pełny komponent: ${visible} osób.`
      : `Po filtrze pozostaje ${visible} osób wraz z wymaganymi łącznikami.`;
  }

  setCandidateRoleFilter(candidateRole) {
    if (!this.model.isExternalNetworkFilter(candidateRole)) return false;
    this.candidateRoleFilter = candidateRole;
    const visibleIds = new Set(this.model.networkProjection(this.networkId, candidateRole).people.map((person) => person.id));
    if (this.selectedId && !visibleIds.has(this.selectedId)) this.selectedId = null;
    this.updateNetworkFilterControl();
    this.updateHeading();
    this.render();
    return true;
  }

  conditionalOverlaySupported() {
    return ["all", "branch"].includes(this.mode) && Boolean(this.conditionalOverlayNetworkId);
  }

  ensureConditionalOverlayControls() {
    if (this.conditionalOverlayFieldset || !this.networkById.size) return;
    this.conditionalOverlayToggle = node("button", {
      type: "button",
      class: "quiet-button",
      "aria-pressed": "false",
      style: "min-height:44px;margin-left:0",
      onclick: () => this.setConditionalOverlayEnabled(!this.conditionalOverlayEnabled),
    }, "Pokaż gałęzie kandydackie");
    this.conditionalOverlayNetworkSelect = node("select", {
      "aria-label": "Wybierz gałąź kandydacką do nałożenia",
      style: "min-height:44px",
      onchange: (event) => this.setConditionalOverlayNetwork(event.currentTarget.value),
    });
    this.conditionalOverlayRoleSelect = node("select", {
      "aria-label": "Filtr roli w nałożonej gałęzi kandydackiej",
      style: "min-height:44px",
      onchange: (event) => this.setConditionalOverlayRoleFilter(event.currentTarget.value),
    });
    this.conditionalOverlayStatus = node("span", {
      role: "status",
      "aria-live": "polite",
      style: "display:block;max-width:420px;color:#735f3e;font-size:10px;line-height:1.35",
    });
    this.conditionalOverlayFieldset = node("fieldset", {
      class: "branch-picker tree-conditional-overlay-controls",
      style: "display:flex;flex:1 1 100%;align-items:center;flex-wrap:wrap;gap:8px;border:0;padding:0;margin:0;min-width:0",
    },
    node("legend", { class: "sr-only" }, "Warstwa gałęzi kandydackiej"),
    this.conditionalOverlayToggle,
    node("label", { style: "min-width:210px;flex:1 1 210px" },
      node("span", { class: "sr-only" }, "Gałąź kandydacka"),
      this.conditionalOverlayNetworkSelect,
    ),
    node("label", { style: "min-width:190px;flex:1 1 190px" },
      node("span", { class: "sr-only" }, "Rola kandydatów"),
      this.conditionalOverlayRoleSelect,
    ),
    this.conditionalOverlayStatus);
    (this.networkFilterFieldset || this.branchPicker)
      .insertAdjacentElement("afterend", this.conditionalOverlayFieldset);
    this.updateConditionalOverlayControls();
  }

  updateConditionalOverlayControls() {
    if (!this.conditionalOverlayFieldset) return;
    const supported = this.conditionalOverlaySupported();
    this.conditionalOverlayFieldset.hidden = !supported;
    this.conditionalOverlayFieldset.style.display = supported ? "flex" : "none";
    this.conditionalOverlayToggle.disabled = !supported;
    this.conditionalOverlayToggle.setAttribute("aria-pressed", String(this.conditionalOverlayEnabled && supported));
    this.conditionalOverlayToggle.textContent = this.conditionalOverlayEnabled
      ? "Ukryj gałęzie kandydackie"
      : "Pokaż gałęzie kandydackie";
    const networkOptions = [...this.networkById.values()].map((network) => node(
      "option",
      { value: network.id },
      sourceReportedNetworkPresentation(network).optionLabel,
    ));
    this.conditionalOverlayNetworkSelect.replaceChildren(...networkOptions);
    this.conditionalOverlayNetworkSelect.value = this.conditionalOverlayNetworkId || "";
    const counts = this.model.externalNetworkFilterCounts(this.conditionalOverlayNetworkId);
    this.conditionalOverlayRoleSelect.replaceChildren(...EXTERNAL_NETWORK_FILTERS.map((filter) => node(
      "option",
      { value: filter.id },
      `${filter.label} (${counts[filter.id] || 0})`,
    )));
    this.conditionalOverlayRoleSelect.value = this.conditionalOverlayRoleFilter;
    this.conditionalOverlayNetworkSelect.disabled = !supported || !this.conditionalOverlayEnabled;
    this.conditionalOverlayRoleSelect.disabled = !supported || !this.conditionalOverlayEnabled;
    if (!this.conditionalOverlayEnabled) {
      this.conditionalOverlayStatus.textContent = "Opcjonalna wyspa nie zmienia relacji ani układu drzewa kanonicznego.";
    } else if (this.conditionalOverlayProjection) {
      this.conditionalOverlayStatus.textContent = `${this.conditionalOverlayCountSummary()}. ${this.conditionalOverlayProjection.attachmentNotice}`;
    }
  }

  conditionalOverlayCountSummary() {
    const candidateCount = this.conditionalOverlayProjection?.people?.length || 0;
    const attachmentCount = this.conditionalOverlayProjection?.attachments?.length || 0;
    return `${this.conditionalOverlayCanonicalCount} w drzewie · ${candidateCount} profili kandydackich · ${attachmentCount} powiązań profili`;
  }

  setConditionalOverlayEnabled(enabled) {
    if (enabled && !this.conditionalOverlaySupported()) return false;
    this.conditionalOverlayEnabled = Boolean(enabled);
    if (!this.conditionalOverlayEnabled && this.conditionalOverlayPersonIds.has(this.selectedId)) {
      this.selectedId = null;
    }
    this.updateConditionalOverlayControls();
    this.render();
    this.fit({ behavior: "auto" });
    return true;
  }

  setConditionalOverlayNetwork(networkId) {
    if (!this.model.network(networkId)) return false;
    this.conditionalOverlayNetworkId = networkId;
    this.conditionalOverlayRoleFilter = "all";
    if (this.conditionalOverlayPersonIds.has(this.selectedId)) this.selectedId = null;
    this.conditionalOverlayProjection = null;
    this.updateConditionalOverlayControls();
    this.render();
    if (this.conditionalOverlayEnabled) this.fit({ behavior: "auto" });
    return true;
  }

  setConditionalOverlayRoleFilter(candidateRole) {
    if (!this.model.isExternalNetworkFilter(candidateRole)) return false;
    this.conditionalOverlayRoleFilter = candidateRole;
    const visibleIds = new Set(this.model.networkProjection(
      this.conditionalOverlayNetworkId,
      candidateRole,
    ).people.map((person) => person.id));
    if (this.conditionalOverlayPersonIds.has(this.selectedId) && !visibleIds.has(this.selectedId)) {
      this.selectedId = null;
    }
    this.conditionalOverlayProjection = null;
    this.updateConditionalOverlayControls();
    this.render();
    if (this.conditionalOverlayEnabled) this.fit({ behavior: "auto" });
    return true;
  }

  conditionalAncestrySupported() {
    return ["all", "branch"].includes(this.mode) && this.conditionalAncestryRoots.length > 0;
  }

  ensureConditionalAncestryControls() {
    if (this.conditionalAncestryFieldset || !this.conditionalAncestryRoots.length) return;
    this.conditionalAncestryToggle = node("button", {
      type: "button",
      class: "quiet-button",
      "aria-pressed": "false",
      style: "min-height:44px;margin-left:0",
      onclick: () => this.setConditionalAncestryEnabled(!this.conditionalAncestryEnabled),
    }, "Pokaż warunkowych przodków");
    this.conditionalAncestryRootSelect = node("select", {
      "aria-label": "Wybierz korzeń warunkowych przodków",
      style: "min-height:44px",
      onchange: (event) => this.setConditionalAncestryRoot(event.currentTarget.value),
    });
    this.conditionalAncestryStatus = node("span", {
      role: "status",
      "aria-live": "polite",
      style: "display:block;max-width:560px;color:#735f3e;font-size:10px;line-height:1.35",
    });
    this.conditionalAncestryFieldset = node("fieldset", {
      class: "branch-picker tree-conditional-ancestry-controls",
      style: "display:flex;flex:1 1 100%;align-items:center;flex-wrap:wrap;gap:8px;border:0;padding:0;margin:0;min-width:0",
    },
    node("legend", { class: "sr-only" }, "Warunkowi przodkowie"),
    this.conditionalAncestryToggle,
    node("label", { style: "min-width:230px;flex:1 1 230px" },
      node("span", { class: "sr-only" }, "Korzeń warunkowych przodków"),
      this.conditionalAncestryRootSelect,
    ),
    this.conditionalAncestryStatus);
    (this.conditionalOverlayFieldset || this.networkFilterFieldset || this.branchPicker)
      .insertAdjacentElement("afterend", this.conditionalAncestryFieldset);
    this.updateConditionalAncestryControls();
  }

  updateConditionalAncestryControls() {
    if (!this.conditionalAncestryFieldset) return;
    const supported = this.conditionalAncestrySupported();
    this.conditionalAncestryFieldset.hidden = !supported;
    this.conditionalAncestryFieldset.style.display = supported ? "flex" : "none";
    this.conditionalAncestryToggle.disabled = !supported;
    this.conditionalAncestryToggle.setAttribute("aria-pressed", String(this.conditionalAncestryEnabled && supported));
    this.conditionalAncestryToggle.textContent = this.conditionalAncestryEnabled
      ? "Ukryj warunkowych przodków"
      : "Pokaż warunkowych przodków";
    const rootLabels = this.conditionalAncestryRootLabels();
    this.conditionalAncestryRootSelect.replaceChildren(...this.conditionalAncestryRoots.map((root) => node(
      "option",
      { value: root.id },
      `${rootLabels.get(root.id)} (${root.visibleNodeCount} węzłów · ${root.visibleEdgeCount} relacji)`,
    )));
    this.conditionalAncestryRootSelect.value = this.conditionalAncestryRootId || "";
    this.conditionalAncestryRootSelect.disabled = !supported || !this.conditionalAncestryEnabled;
    this.conditionalAncestryStatus.textContent = this.conditionalAncestryProjection
      ? conditionalAncestrySummary(this.conditionalAncestryProjection)
      : `Warstwa opcjonalna. Audyt obejmuje ${this.conditionalAncestry?.audit?.rootCount || this.conditionalAncestryRoots.length} korzeni i nie zmienia potwierdzonych przodków.`;
  }

  conditionalAncestryRootLabels() {
    const nodeById = new Map((this.conditionalAncestry?.nodes || []).map((item) => [item.id, item]));
    const baseLabels = this.conditionalAncestryRoots.map((root) => {
      const ancestryNode = nodeById.get(root.nodeId);
      const source = (ancestryNode?.sourceIds || []).map((sourceId) => this.dataIndex.sources.get(sourceId)).find(Boolean);
      const scopeLabel = root.scope === "source_reported_network"
        ? "drzewo zewnętrzne"
        : root.scope === "source_occurrence"
          ? ["wystąpienie w akcie", source?.title, source?.date].filter(Boolean).join(" · ")
          : "drzewo bazowe";
      return { id: root.id, label: `${root.label} · ${scopeLabel}` };
    });
    const totals = new Map();
    baseLabels.forEach((item) => totals.set(item.label, (totals.get(item.label) || 0) + 1));
    const positions = new Map();
    return new Map(baseLabels.map((item) => {
      if (totals.get(item.label) === 1) return [item.id, item.label];
      const position = (positions.get(item.label) || 0) + 1;
      positions.set(item.label, position);
      return [item.id, `${item.label} · pozycja ${position}`];
    }));
  }

  setConditionalAncestryEnabled(enabled) {
    if (enabled && !this.conditionalAncestrySupported()) return false;
    this.conditionalAncestryEnabled = Boolean(enabled);
    if (!this.conditionalAncestryEnabled && this.conditionalAncestryPersonIds.has(this.selectedId)) this.selectedId = null;
    this.render();
    this.fit({ behavior: "auto" });
    return true;
  }

  setConditionalAncestryRoot(rootId) {
    if (!this.conditionalAncestryRoots.some((root) => root.id === rootId)) return false;
    this.conditionalAncestryRootId = rootId;
    if (this.conditionalAncestryPersonIds.has(this.selectedId)) this.selectedId = null;
    this.conditionalAncestryProjection = null;
    this.render();
    if (this.conditionalAncestryEnabled) this.fit({ behavior: "auto" });
    return true;
  }

  applyBasePersonDeepLink(personId) {
    const person = (this.publicTree?.people || []).find((item) => item.id === personId);
    if (!person || focusedPersonProtected(person)) return false;
    const publicPeople = new Map((this.publicTree?.people || [])
      .filter((item) => !focusedPersonProtected(item)).map((item) => [item.id, item]));
    const pathIds = new Set([personId]);
    const queue = [personId];
    while (queue.length) {
      const parentId = queue.shift();
      for (const family of this.publicTree?.families || []) {
        if (!(family.parentIds || []).includes(parentId)) continue;
        for (const childId of family.childIds || []) {
          if (!publicPeople.has(childId) || pathIds.has(childId)) continue;
          pathIds.add(childId);
          queue.push(childId);
        }
      }
    }
    // Only descendant controls on an explicit public family path can hide this target.
    for (const pathId of pathIds) this.collapsedAncestors.delete(pathId);
    const ancestorIds = new Set([personId]);
    const ancestorQueue = [personId];
    while (ancestorQueue.length) {
      const childId = ancestorQueue.shift();
      for (const family of this.publicTree?.families || []) {
        if (!(family.childIds || []).includes(childId)) continue;
        if (family.showAllChildren !== true && this.primaryChildId(family) !== childId) {
          this.expandedFamilies.add(family.id);
        }
        for (const parentId of family.parentIds || []) {
          if (!publicPeople.has(parentId) || ancestorIds.has(parentId)) continue;
          ancestorIds.add(parentId);
          ancestorQueue.push(parentId);
        }
      }
    }
    this.unifiedCollapsedIds.delete(personId);
    this.unifiedExpandedIds.add(personId);
    if (this.mode !== "all") {
      this.setMode("all");
      $$("[data-tree-mode]").forEach((button) => button.classList.toggle("is-active", button.dataset.treeMode === "all"));
    } else {
      this.closeFocusedAncestry({ render: false });
      this.renderKeepingPerson(personId);
    }
    this.focusPerson(personId);
    return true;
  }

  applyExternalNetworkDeepLink({ networkId, publicProfileId = null, candidateRole = "all" } = {}) {
    const focusedState = this.readFocusedAncestryState();
    const network = typeof networkId === "string" ? this.model.network(networkId) : null;
    const filter = typeof candidateRole === "string" ? candidateRole : "all";
    const person = publicProfileId == null
      ? null
      : typeof publicProfileId === "string"
        ? this.model.networkPersonByPublicProfileId(networkId, publicProfileId)
        : null;
    const invalid = !network
      || !this.model.isExternalNetworkFilter(filter)
      || (publicProfileId != null && !person)
      || (person && !this.model.networkProjection(networkId, filter).people.some((item) => item.id === person.id));
    if (invalid) {
      this.candidateRoleFilter = "all";
      this.setMode("all");
      $$("[data-tree-mode]").forEach((button) => button.classList.toggle("is-active", button.dataset.treeMode === "all"));
      return false;
    }

    const unifiedTarget = person ? this.unifiedProjection?.aliases?.get(person.id) : null;
    if (unifiedTarget && this.personById.has(unifiedTarget)) {
      this.setMode("all");
      requestAnimationFrame(() => this.select(unifiedTarget, { includeParents: true }));
      return true;
    }
    if (this.unifiedAdvanced) this.unifiedAdvanced.open = true;
    this.networkId = networkId;
    this.candidateRoleFilter = filter;
    this.setMode("network");
    $$("[data-tree-mode]").forEach((button) => button.classList.toggle("is-active", button.dataset.treeMode === "network"));
    if (person) requestAnimationFrame(() => {
      this.select(person.id, { reveal: !this.externalCameraWasRestored });
      if (focusedState?.active && focusedState.seed?.networkId === networkId && focusedState.seed?.publicProfileId === publicProfileId) {
        this.openFocusedAncestry(person, { restore: true, scenarioKey: focusedState.scenarioKey });
      }
    });
    return true;
  }

  clearExternalNetworkDeepLink() {
    this.candidateRoleFilter = "all";
    if (this.mode !== "network") {
      this.updateNetworkFilterControl();
      return false;
    }
    this.setMode("all");
    $$("[data-tree-mode]").forEach((button) => button.classList.toggle("is-active", button.dataset.treeMode === "all"));
    return true;
  }

  candidateProfileHref(publicProfileId, networkId, candidateRole) {
    const url = new URL(`people/${encodeURIComponent(publicProfileId)}`, document.baseURI);
    url.searchParams.set("returnView", "tree");
    url.searchParams.set("network", networkId);
    url.searchParams.set("person", publicProfileId);
    if (candidateRole !== "all") url.searchParams.set("candidateRole", candidateRole);
    return url.toString();
  }

  externalSelectionHref(publicProfileId, networkId, candidateRole) {
    const url = new URL("tree", document.baseURI);
    url.searchParams.set("network", networkId);
    url.searchParams.set("person", publicProfileId);
    url.searchParams.set("candidateRole", candidateRole);
    return `${url.pathname}${url.search}`;
  }

  persistExternalSelection(person) {
    if (!person?.sourceReported || !person.publicProfileId || !person.sourceReportedNetworkId) return false;
    if (this.conditionalOverlayPersonIds?.has(person.id)) return false;
    const treePath = new URL("tree", document.baseURI).pathname;
    if (typeof window !== "undefined" && window.location.pathname !== treePath) return false;
    const href = this.externalSelectionHref(
      person.publicProfileId,
      person.sourceReportedNetworkId,
      this.candidateRoleFilter,
    );
    try {
      history.replaceState(history.state || {}, "", href);
      return true;
    } catch {
      return false;
    }
  }

  externalCameraState() {
    if (this.mode !== "network") return null;
    return {
      scale: Number(this.scale.toFixed(4)),
      scrollLeft: Math.max(0, Math.round(this.scroll.scrollLeft)),
      scrollTop: Math.max(0, Math.round(this.scroll.scrollTop)),
    };
  }

  persistExternalCamera() {
    if (this.suppressExternalCameraPersistence) return false;
    const camera = this.externalCameraState();
    if (!camera) return false;
    try {
      sessionStorage.setItem(EXTERNAL_CAMERA_STORAGE_KEY, JSON.stringify(camera));
      history.replaceState({
        ...(history.state || {}),
        [EXTERNAL_CAMERA_HISTORY_KEY]: camera,
      }, "", window.location.href);
      return true;
    } catch {
      return false;
    }
  }

  scheduleExternalCameraPersistence() {
    if (this.mode !== "network" || this.suppressExternalCameraPersistence || this.externalCameraFrame) return;
    this.externalCameraFrame = requestAnimationFrame(() => {
      this.externalCameraFrame = null;
      this.persistExternalCamera();
    });
  }

  restoreExternalCamera() {
    let stored = null;
    try {
      stored = history.state?.[EXTERNAL_CAMERA_HISTORY_KEY]
        || JSON.parse(sessionStorage.getItem(EXTERNAL_CAMERA_STORAGE_KEY) || "null");
    } catch {
      stored = null;
    }
    if (!stored || typeof stored !== "object" || Array.isArray(stored)) return false;
    const scale = finiteCameraNumber(stored.scale, MIN_OVERVIEW_SCALE, MAX_SCALE);
    const scrollLeft = finiteCameraNumber(stored.scrollLeft, 0, 10_000_000);
    const scrollTop = finiteCameraNumber(stored.scrollTop, 0, 10_000_000);
    if (scale == null || scrollLeft == null || scrollTop == null) return false;
    this.scale = scale;
    this.cardVariant = semanticTreeCardVariant(this.scale, this.cardVariant);
    this.render();
    this.scroll.scrollTo({ left: scrollLeft, top: scrollTop, behavior: "auto" });
    this.cameraMode = "manual";
    return true;
  }

  renderBranchOptions() {
    const options = this.mode === "network"
      ? [...this.networkById.values()].map((network) => node(
          "option",
          { value: network.id },
          sourceReportedNetworkPresentation(network).optionLabel,
        ))
      : [...this.branchById.values()].map((branch) => node("option", { value: branch.id }, branch.label));
    this.branchSelect.replaceChildren(...options);
    this.branchSelect.setAttribute("aria-label", this.mode === "network" ? "Wybierz zewnętrzną sieć kandydacką" : "Wybierz gałąź rodziny");
    this.branchSelect.value = (this.mode === "network" ? this.networkId : this.branchId) || "";
    if (!this.branchSelect.dataset.geneaBound) {
      this.branchSelect.dataset.geneaBound = "true";
      this.branchSelect.addEventListener("change", () => {
        if (this.mode === "network") {
          this.networkId = this.branchSelect.value;
          this.candidateRoleFilter = "all";
        }
        else this.branchId = this.branchSelect.value;
        this.selectedId = null;
        this.updateNetworkFilterControl();
        this.updateHeading();
        this.render();
        this.fit();
      });
    }
  }

  peoplePerGeneration(people = this.tree?.people || []) {
    return this.model.peoplePerGeneration(people);
  }

  setMode(mode) {
    this.closeFocusedAncestry({ render: false });
    if (mode === "network" && !this.networkId) return;
    this.suppressExternalCameraPersistence = mode === "network";
    this.externalCameraWasRestored = false;
    const previousMode = this.mode;
    const previousSelection = this.selectedId;
    this.mode = mode;
    this.baseWidth = mode === "all" ? this.allTreeWidth : 1100;
    if (mode === "ancestors") {
      if (previousMode !== "ancestors") this.ancestorMaxDepth = DEFAULT_ANCESTOR_VISIBLE_DEPTH;
      const previousCanonicalSelection = (this.tree?.people || []).some((person) => person.id === previousSelection);
      this.ancestorFocusId = previousCanonicalSelection
        ? previousSelection
        : this.ancestorFocusId || this.tree?.rootPersonId;
      this.selectedId = this.ancestorFocusId;
      this.selectedAncestorSlotId = null;
      this.ancestorProjection = null;
    } else {
      this.selectedId = null;
      this.selectedAncestorSlotId = null;
    }
    this.branchPicker.hidden = !["branch", "network"].includes(mode);
    this.ancestorLayoutControls.hidden = mode !== "ancestors";
    if (this.ancestorDepthControls) this.ancestorDepthControls.hidden = mode !== "ancestors";
    if (mode !== "network") this.candidateRoleFilter = "all";
    this.updateNetworkFilterControl();
    this.updateConditionalOverlayControls();
    this.updateConditionalAncestryControls();
    $("#tree-search").placeholder = mode === "ancestors"
      ? "Znajdź osobę fokusową…"
      : "Szukaj osoby…";
    this.renderBranchOptions();
    this.updateHeading();
    this.render();
    if (mode === "ancestors") this.home({ behavior: "auto" });
    else if (mode === "network") {
      this.externalCameraWasRestored = this.restoreExternalCamera();
      if (!this.externalCameraWasRestored) this.fit({ behavior: "auto" });
      this.suppressExternalCameraPersistence = false;
    } else this.fit();
  }

  setAncestorLayout(mode) {
    if (!new Set(["vertical", "fan"]).has(mode) || this.ancestorLayoutMode === mode) return;
    const selectedSlotId = this.selectedAncestorSlotId;
    this.ancestorLayoutMode = mode;
    this.updateHeading();
    this.render();
    this.cameraMode = "focus";
    requestAnimationFrame(() => this.revealAncestorSlot(selectedSlotId, { behavior: "auto" }));
  }

  setAncestorFocus(personId) {
    if (!this.personById.has(personId)) return false;
    if (personId !== this.ancestorFocusId) this.ancestorMaxDepth = DEFAULT_ANCESTOR_VISIBLE_DEPTH;
    this.ancestorFocusId = personId;
    this.selectedId = personId;
    this.selectedAncestorSlotId = null;
    this.ancestorProjection = null;
    if (this.mode !== "ancestors") this.mode = "ancestors";
    this.branchPicker.hidden = true;
    this.ancestorLayoutControls.hidden = false;
    if (this.ancestorDepthControls) this.ancestorDepthControls.hidden = false;
    this.updateConditionalOverlayControls();
    this.updateConditionalAncestryControls();
    this.updateHeading();
    this.render();
    this.cameraMode = "focus";
    requestAnimationFrame(() => this.revealPerson(personId, { behavior: "auto" }));
    return true;
  }

  togglePresentation() {
    this.presentationMode = this.presentationMode === "list" ? "canvas" : "list";
    this.scroll.closest(".tree-stage")?.classList.toggle("is-list-presentation", this.presentationMode === "list");
    this.render();
    if (this.presentationMode === "canvas") requestAnimationFrame(() => this.home({ behavior: "auto" }));
    return this.presentationMode === "list";
  }

  updateHeading() {
    if (this.focusedAncestryEnabled && this.focusedAncestryScenario) {
      $("#tree-title").textContent = `Drzewo skupione: ${this.focusedAncestryScenario.label}`;
      $("#tree-description").textContent = "Czytelny fragment przodków. Rozwijaj dalsze linie przy osobie; ścieżka u góry zachowuje możliwość powrotu.";
      return;
    }
    const branch = this.branchById.get(this.branchId);
    const network = this.activeNetwork();
    const networkPresentation = network ? sourceReportedNetworkPresentation(network) : null;
    const identityCounts = network ? this.model.externalNetworkIdentityCounts(network.id) : null;
    const selectedFilter = EXTERNAL_NETWORK_FILTERS.find((item) => item.id === this.candidateRoleFilter);
    const anchorSummary = network?.anchorState === "visible" && network?.anchor?.label
      ? `Punkt odniesienia: ${network.anchor.label}.`
      : network?.anchorState === "masked"
        ? "Punkt odniesienia jest ukryty przez reguły prywatności."
        : "Brak widocznego punktu odniesienia.";
    const ancestorFocus = this.personById.get(this.ancestorFocusId);
    $("#tree-title").textContent = this.mode === "ancestors"
      ? `Przodkowie: ${ancestorFocus?.name || "wybrana osoba"}`
      : network
        ? `${networkPresentation.label}${this.candidateRoleFilter === "all" ? "" : ` · ${selectedFilter?.label || "filtr"}`}`
      : this.mode === "branch" ? (branch?.label || "Gałąź rodziny") : (this.tree.title || "Całe drzewo rodzinne");
    $("#tree-description").textContent = this.mode === "ancestors"
      ? this.ancestorLayoutMode === "fan"
        ? "Ta sama linia przodków w półkolistym wachlarzu. Powtórzona osoba pozostaje osobnym miejscem w pedigree."
        : "Osoba fokusowa znajduje się na dole, a kolejne pokolenia przodków rosną ku górze."
      : network
        ? `Zewnętrzna struktura do weryfikacji. ${anchorSummary} Profile widoczne po filtrze prywatności: ${polishCount(identityCounts.agentApprovedHumanPending, "zatwierdzony agentowo i oczekujący potwierdzenia człowieka", "zatwierdzone agentowo i oczekujące potwierdzenia człowieka", "zatwierdzonych agentowo i oczekujących potwierdzenia człowieka")}; ${polishCount(identityCounts.humanConfirmed, "potwierdzony przez człowieka", "potwierdzone przez człowieka", "potwierdzonych przez człowieka")}; ${polishCount(identityCounts.resolvedWithoutDecisionTier, "z tożsamością rozstrzygniętą — poziom decyzji nieokreślony", "z tożsamością rozstrzygniętą — poziom decyzji nieokreślony", "z tożsamością rozstrzygniętą — poziom decyzji nieokreślony")}; ${polishCount(identityCounts.unresolved, "wymagający rozstrzygnięcia", "wymagające rozstrzygnięcia", "wymagających rozstrzygnięcia")}. Relacje pozostają source-reported, nierozstrzygnięte, niekanoniczne i imported_unverified.`
      : this.mode === "branch"
        ? (branch?.description || "Wybrana gałąź rodziny")
        : (this.tree.subtitle || "Wszystkie zapisane gałęzie");
    const evidenceNote = document.querySelector(".tree-evidence-note");
    if (evidenceNote) evidenceNote.textContent = this.mode === "ancestors"
      ? "Widok przechodzi wyłącznie po kontrolowanych relacjach rodzic–dziecko. Brak dalszej osoby oznacza brak danych w drzewie, a nie dowód jej nieistnienia."
      : network
        ? `Warstwa zewnętrzna · imported_unverified. ${this.candidateRoleFilter === "all" ? "Widoczny jest pełny komponent." : "Przygaszone elementy zachowują jedynie ścieżkę poza filtrem."} Przerywane łączniki nie są relacjami kanonicznymi i nie uruchamiają automatycznego scalania.`
      : "Przeciągnij puste tło, aby przesuwać drzewo. Kolor i opis karty mówią, czy łącznik wynika z aktu, materiału rodzinnego czy pozostaje hipotezą.";
  }

  updateGenerationDepth(people) {
    const depth = Math.max(0, ...people.map((person) => person.generation));
    $("#tree-generation-count").textContent = depth;
    $("#tree-generation-label").textContent = depth === 1
      ? "pokolenie wstecz"
      : depth >= 2 && depth <= 4
        ? "pokolenia wstecz"
        : "pokoleń wstecz";
  }

  updatePersonCount(people) {
    const count = people.length;
    $("#tree-person-count").textContent = count;
    $("#tree-person-count-label").textContent = count === 1
      ? "osoba w widoku"
      : count >= 2 && count <= 4
        ? "osoby w widoku"
        : "osób w widoku";
  }

  generationLabel(person) {
    if (person.sourceReported) return `Warstwa zewnętrzna · poziom ${person.generation}`;
    if (person.generation < 0) return `Potomek · pokolenie ${Math.abs(person.generation)}`;
    if (person.id === this.tree.rootPersonId) return "Punkt odniesienia";
    if (person.generation === 0) return "To samo pokolenie";
    return `Pokolenie ${person.generation}`;
  }

  positions(people) {
    const result = this.layout.positions(people, this.baseWidth, this.scale, this.activeNetwork() ? [] : this.tree?.families || []);
    this.layoutGenerationSpan = result.generationSpan;
    this.baseWidth = Math.max(this.baseWidth, result.width || 0);
    return result.positions;
  }

  generationStep() {
    return this.layout.generationStep(this.scale);
  }

  render() {
    this.syncCanvasGeometry();
    if (!this.unifiedTreeActive()) {
      this.unifiedProjection = null;
      this.scroll.closest(".tree-stage")?.classList.remove("is-unified-tree");
      if (this.unifiedStatus) this.unifiedStatus.hidden = true;
    }
    if (this.focusedAncestryEnabled) {
      const seed = this.focusedSeedPerson(this.focusedAncestrySeed);
      const refreshed = seed ? focusedScenariosForPerson({ tree: this.tree, person: seed, conditionalAncestry: this.conditionalAncestry }) : [];
      const scenario = refreshed.find((item) => item.key === this.focusedAncestryScenario?.key);
      if (scenario) {
        this.focusedAncestryScenario = scenario;
        this.focusedAncestryView.setScenario(scenario);
        this.updateFocusedAncestryVisibility();
        return;
      }
      this.closeFocusedAncestry({ render: false });
    }
    if (!this.tree) {
      this.layer.replaceChildren(node("div", { class: "tree-unavailable" }, "Brak kuratorowanego drzewa w tym eksporcie."));
      return;
    }
    if (this.mode === "ancestors") {
      this.renderAncestors();
      return;
    }
    this.scroll.closest(".tree-stage")?.classList.remove("is-ancestor-fan");
    this.svg.classList.remove("ancestor-fan-svg");
    this.svg.setAttribute("aria-hidden", "true");
    this.svg.removeAttribute("role");
    this.svg.removeAttribute("aria-label");
    const basePeople = this.basePeople();
    const baseVisiblePeople = this.visiblePeople();
    this.unifiedProjection = this.unifiedTreeActive() ? projectUnifiedFamilyTree({
      tree: this.tree, model: this.model, visiblePeople: baseVisiblePeople,
      maxDepth: this.unifiedCandidatesEnabled ? 2 : 0,
      expandedNodeIds: this.unifiedExpandedIds, collapsedNodeIds: this.unifiedCollapsedIds,
    }) : null;
    const people = this.unifiedProjection?.people || baseVisiblePeople;
    const layoutFamilies = this.unifiedProjection?.layoutFamilies || this.tree?.families || [];
    this.scroll.closest(".tree-stage")?.classList.toggle("is-unified-tree", Boolean(this.unifiedProjection));
    if (this.unifiedStatus) {
      this.unifiedStatus.hidden = !this.unifiedProjection;
      const extraCount = people.filter((person) => person.conditionalAncestry).length;
      const linkedCount = people.reduce((total, person) => total + (this.unifiedProjection?.identitySourcesByPerson?.get(person.id)?.length || 0), 0);
      this.unifiedStatus.textContent = this.unifiedProjection
        ? `${people.length - extraCount} w drzewie bazowym · ${extraCount} kart warunkowych · ${linkedCount} powiązanych profili. Przerywane relacje: niepotwierdzone.${this.unifiedProjection.truncated ? " Dalszy zapis poza widokiem: + rodzice." : ""}` : "";
    }
    if (this.mode === "network") this.updateNetworkFilterControl();
    this.updateGenerationDepth(basePeople);
    const canonicalWidth = Math.max(
      this.mode === "all" ? 1440 : 1100,
      this.layout.requiredWidth(people, 180, this.activeNetwork() ? [] : layoutFamilies),
    );
    const canonicalLayout = this.layout.positions(
      people,
      canonicalWidth,
      this.scale,
      this.activeNetwork() ? [] : layoutFamilies,
    );
    this.layoutGenerationSpan = canonicalLayout.generationSpan;
    this.baseWidth = Math.max(canonicalWidth, canonicalLayout.width || 0);
    this.positionById = new Map(canonicalLayout.positions);
    this.conditionalOverlayProjection = null;
    this.conditionalOverlayLayout = null;
    this.conditionalOverlayPersonIds = new Set();
    this.conditionalOverlayCanonicalCount = people.length;
    if (this.conditionalOverlayEnabled && this.conditionalOverlaySupported()) {
      this.conditionalOverlayProjection = projectConditionalExternalOverlay({
        model: this.model,
        attachments: this.tree?.conditionalTreeAttachments || [],
        networkId: this.conditionalOverlayNetworkId,
        candidateRole: this.conditionalOverlayRoleFilter,
        visibleTreePersonIds: people.map((person) => person.id),
      });
      this.conditionalOverlayLayout = layoutConditionalExternalOverlay({
        projection: this.conditionalOverlayProjection,
        layout: this.layout,
        canonicalWidth: this.baseWidth,
      });
      this.conditionalOverlayPersonIds = new Set(
        this.conditionalOverlayProjection.people.map((person) => person.id),
      );
      this.conditionalOverlayLayout.positions.forEach((position, personId) => {
        this.positionById.set(personId, position);
      });
      this.baseWidth = Math.max(this.baseWidth, this.conditionalOverlayLayout.width);
      this.layoutGenerationSpan = Math.max(
        this.layoutGenerationSpan,
        this.conditionalOverlayLayout.generationSpan,
      );
    }
    this.conditionalAncestryProjection = null;
    this.conditionalAncestryLayout = null;
    this.conditionalAncestryPersonIds = new Set();
    if (this.conditionalAncestryEnabled && this.conditionalAncestrySupported()) {
      this.conditionalAncestryProjection = projectConditionalAncestry({
        conditionalAncestry: this.conditionalAncestry,
        rootId: this.conditionalAncestryRootId,
        canonicalPeople: this.tree?.people || [],
        visibleTreePersonIds: people.map((person) => person.id),
      });
      this.conditionalAncestryLayout = layoutConditionalAncestryOverlay({
        projection: this.conditionalAncestryProjection,
        layout: this.layout,
        canonicalWidth: this.baseWidth,
      });
      this.conditionalAncestryLayout.positions.forEach((position, personId) => {
        this.positionById.set(personId, position);
      });
      this.baseWidth = Math.max(this.baseWidth, this.conditionalAncestryLayout.width);
      this.layoutGenerationSpan = Math.max(
        this.layoutGenerationSpan,
        this.conditionalAncestryLayout.generationSpan,
      );
    }
    const conditionalAncestryPeople = (this.conditionalAncestryProjection?.cards || []).map((item) => ({
      id: item.displayId,
      name: item.label,
      lifeSpanLabel: item.lifeSpanLabel || "?–?",
      generation: item.depth,
      order: item.order,
      evidenceStatus: "unresolved",
      occurrenceId: item.occurrenceId || null,
      publicProfileId: item.publicProfileId || null,
      sourceIds: item.sourceIds || [],
      conditionalAncestry: true,
      conditionalAncestryNode: item,
      pathUncertain: item.pathUncertain,
    }));
    this.conditionalAncestryPersonIds = new Set(conditionalAncestryPeople.map((person) => person.id));
    const renderedPeople = [
      ...people,
      ...(this.conditionalOverlayProjection?.people || []),
      ...conditionalAncestryPeople,
    ];
    this.renderedPersonById = new Map(renderedPeople.map((person) => [person.id, person]));
    this.updatePersonCount(baseVisiblePeople);
    this.updateConditionalOverlayControls();
    this.updateConditionalAncestryControls();
    const height = this.estimatedSurfaceHeight(this.scale);
    this.surfaceHeight = height;
    this.surface.style.width = `${this.baseWidth}px`;
    this.surface.style.height = `${height}px`;
    this.svg.setAttribute("viewBox", `0 0 ${this.baseWidth} ${height}`);
    this.svg.setAttribute("width", this.baseWidth);
    this.svg.setAttribute("height", height);
    const activeBranch = this.branchById.get(this.branchId);
    const focus = new Set(this.mode === "network" ? [] : this.mode === "branch" ? activeBranch?.personIds || [] : this.tree.focusPath || []);
    const cards = renderedPeople.map((person) => {
      const position = this.positionById.get(person.id);
      const unionCount = (this.tree.unions || []).filter((union) => union.partnerIds.includes(person.id)).length;
      const ownedUnions = this.ownedUnions(person.id);
      const activeUnion = this.activeUnion(person.id);
      const activeUnionIndex = Math.max(0, ownedUnions.findIndex((union) => union.id === activeUnion?.id));
      const singleUnionMode = this.collapsedUnions.has(person.id);
      const canCollapseAncestors = this.hasAncestorBranch(person.id);
      const hasConditionalParents = this.hasUnifiedConditionalParents(person.id);
      const unifiedParentControl = this.unifiedProjection && (canCollapseAncestors || hasConditionalParents)
        ? this.unifiedParentControlState(person.id, {
            base: canCollapseAncestors,
            conditional: hasConditionalParents,
          })
        : null;
      const review = person.conditionalAncestry ? null : this.dataIndex.personReview(person);
      const candidateCount = (person.candidateLinks || []).length;
      const personContext = person.conditionalAncestry
        ? null
        : this.dataIndex.personContext(person) || (person.occurrenceId ? this.dataIndex.occurrenceContext(person.occurrenceId) : null);
      const presentation = presentTreePersonCard(person, {
        variant: this.cardVariant,
        generationLabel: person.conditionalAncestry
          ? `Warunkowy przodek · poziom ${person.generation}`
          : person.sourceReported ? person.candidateRoleLabel : this.generationLabel(person),
        evidenceLabel: person.conditionalAncestry
          ? "nierozstrzygnięte · niekanoniczne"
          : person.sourceReported ? "imported_unverified" : this.evidenceLabel(person.evidenceStatus),
        review,
        candidateCount,
        identityScope: person.sourceReported ? "presentation_only" : personContext?.identityScope || "presentation_only",
        identityComponentCount: personContext?.canonicalPersonIds?.length || 0,
      });
      const profileId = person.publicProfileId || personContext?.publicProfileId || null;
      const resolvedExternalProfile = person.sourceReported && person.personIdentityResolved === true;
      const familyKnown = person.familyKnown;
      const externalProfileStatus = person.summary || "Zewnętrzny profil kandydacki · tożsamość nierozstrzygnięta";
      const externalRelationshipStatus = "relacja wskazana w źródle · niekanoniczna · imported_unverified";
      const mainAction = person.conditionalAncestry
        ? node("button", {
            type: "button",
            class: "tree-card-main tree-conditional-ancestry-select",
            "aria-label": `Wybierz warunkowego przodka: ${presentation.fullName}. Poziom ${person.generation}. Relacja nierozstrzygnięta i niekanoniczna`,
            title: presentation.fullName,
            onclick: () => this.select(person.id, { reveal: false }),
          },
          node("span", { class: `tree-avatar ${person.sex || "unknown"}` }, presentation.initials),
          node("span", { class: "tree-card-copy" },
            node("strong", {}, presentation.displayName),
            node("small", {}, "Rodzice wskazani w źródle"),
            node("span", { class: "tree-evidence unresolved" }, "relacja warunkowa"),
          ))
        : person.sourceReported && profileId
        ? node("button", {
            type: "button",
            class: "tree-card-main tree-candidate-profile-link",
            "aria-label": familyKnown
              ? `Wybierz profil: ${presentation.fullName}. ${familyKnown.label}. ${familyKnown.confidenceLabel}. ${familyKnown.basisLabel}. ${externalProfileStatus}. ${person.candidateRoleLabel}. ${externalRelationshipStatus}`
              : resolvedExternalProfile
              ? `Wybierz profil: ${presentation.fullName}. ${externalProfileStatus}. ${person.candidateRoleLabel}. ${externalRelationshipStatus}`
              : `Wybierz profil: ${presentation.fullName}. ${person.candidateRoleLabel}. imported_unverified`,
            title: presentation.fullName,
            onclick: () => this.select(person.id, { reveal: false }),
          },
          node("span", { class: `tree-avatar ${person.sex || "unknown"}` }, presentation.initials),
          node("span", { class: "tree-card-copy" },
            node("strong", {}, presentation.displayName),
            familyKnown ? node("span", {
              class: "tree-family-known-badge",
              title: familyKnown.notice,
              style: "display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:9px;line-height:1.3",
            }, familyKnown.badgeLabel) : null,
            node("small", {}, person.outsideFilterConnector
              ? "łącznik poza filtrem"
              : familyKnown ? familyKnown.basisLabel : resolvedExternalProfile ? externalProfileStatus : person.candidateRoleLabel),
            node("span", {
              class: "tree-evidence unresolved",
              title: resolvedExternalProfile ? externalRelationshipStatus : "imported_unverified",
            }, resolvedExternalProfile ? externalRelationshipStatus : "imported_unverified"),
          ),
        )
        : node("button", {
            class: "tree-card-main",
            "aria-label": presentation.accessibleName,
            title: presentation.fullName,
            onclick: () => this.select(person.id),
          },
          node("span", { class: `tree-avatar ${person.sex || "unknown"}` }, presentation.initials),
          node("span", { class: "tree-card-copy" },
            node("strong", {}, presentation.displayName),
            node("small", {}, presentation.subtitle),
            node("span", { class: `tree-evidence ${person.evidenceStatus || ""}` }, presentation.evidence),
          ),
        );
      const card = node("article", {
        class: `tree-person tree-card-${presentation.variant} ${person.evidenceStatus || ""} identity-${presentation.identityScope} ${person.sourceReported ? `is-source-reported classification-${person.classificationState} role-${person.candidateRole} ${person.isNetworkAnchor ? "is-network-anchor" : ""} ${person.outsideFilterConnector ? "is-dimmed is-filter-connector" : ""}` : ""} ${person.conditionalAncestry ? "is-conditional-ancestry" : ""} ${unifiedParentControl ? "has-unified-parent-control" : ""} ${focus.has(person.id) ? "is-focus" : ""} ${this.selectedId === person.id ? "is-selected" : ""}`,
        "data-person-id": person.id,
        "data-identity-scope": presentation.identityScope,
        "data-network-id": person.sourceReportedNetworkId || null,
        "data-candidate-role": person.sourceReported ? person.candidateRole : null,
        "data-classification-state": person.sourceReported ? person.classificationState : null,
        "data-network-anchor": person.sourceReported ? String(person.isNetworkAnchor) : null,
        "data-filter-connector": person.sourceReported ? String(person.outsideFilterConnector) : null,
        "data-conditional-ancestry-node-id": person.conditionalAncestry ? person.conditionalAncestryNode.id : null,
        "data-conditional-ancestry-depth": person.conditionalAncestry ? person.generation : null,
        "data-path-uncertain": person.conditionalAncestry ? String(person.pathUncertain === true) : null,
        "data-person-backing-id": person.sourceReported ? null : person.personId || null,
        "data-occurrence-backing-id": person.sourceReported ? null : person.occurrenceId || null,
        "data-source-ids": person.conditionalAncestry ? (person.sourceIds || []).join(" ") : null,
        "data-review-state": review?.state || "none",
        style: `left:${position.x}px;top:${position.y}px${person.conditionalAncestry ? ";border:2px dashed #8d6b2f;background:linear-gradient(135deg,#fffdf8,#f7f0df)" : ""}`,
      },
      mainAction,
      unifiedParentControl ? node("button", {
        type: "button",
        class: `tree-unified-parent-toggle ${unifiedParentControl.expanded ? "is-expanded" : "is-collapsed"}`,
        title: `${unifiedParentControl.expanded ? "Zwiń" : "Rozwiń"} rodziców osoby ${person.name}`,
        "aria-label": `${unifiedParentControl.expanded ? "Zwiń" : "Rozwiń"} rodziców osoby ${person.name}`,
        "aria-expanded": String(unifiedParentControl.expanded),
        "data-controls-base-parents": String(unifiedParentControl.base),
        "data-controls-conditional-parents": String(unifiedParentControl.conditional),
        onclick: (event) => {
          event.stopPropagation();
          this.toggleUnifiedParents(person.id, unifiedParentControl);
        },
      }, unifiedParentControl.expanded ? "Zwiń rodziców" : "Rozwiń rodziców") : null,
      presentation.attention ? node("span", {
        class: `tree-attention-marker is-${presentation.attention.kind}`,
        title: presentation.attention.label,
        "aria-label": presentation.attention.label,
      }, presentation.attention.symbol) : null,
      ownedUnions.length > 1 ? node("span", { class: `tree-union-switcher ${singleUnionMode ? "is-single" : ""}` },
        node("button", {
          title: "Poprzednie małżeństwo",
          "aria-label": `Poprzednie małżeństwo osoby ${person.name}`,
          onclick: (event) => { event.stopPropagation(); this.cycleUnion(person.id, -1); },
        }, "‹"),
        node("button", {
          class: "tree-union-position",
          title: singleUnionMode ? "Pokaż wszystkie małżeństwa" : "Pokaż jedno małżeństwo",
          "aria-label": singleUnionMode ? "Pokaż wszystkie małżeństwa" : "Pokaż jedno małżeństwo",
          onclick: (event) => { event.stopPropagation(); this.toggleUnions(person.id); },
        }, singleUnionMode ? `♡ ${activeUnionIndex + 1}/${ownedUnions.length}` : `♡ ${ownedUnions.length}`),
        node("button", {
          title: "Następne małżeństwo",
          "aria-label": `Następne małżeństwo osoby ${person.name}`,
          onclick: (event) => { event.stopPropagation(); this.cycleUnion(person.id, 1); },
        }, "›"),
      )
        : unionCount ? node("span", { class: "tree-union-count", title: polishCount(unionCount, "związek", "związki", "związków") }, `♡ ${unionCount}`) : null,
      canCollapseAncestors && !this.unifiedProjection ? node("button", {
        class: `tree-branch-toggle ${this.collapsedAncestors.has(person.id) ? "is-collapsed" : ""}`,
        title: this.collapsedAncestors.has(person.id) ? "Rozwiń dalszą gałąź" : "Zwiń dalszą gałąź",
        "aria-label": this.collapsedAncestors.has(person.id) ? `Rozwiń gałąź osoby ${person.name}` : `Zwiń gałąź osoby ${person.name}`,
        onclick: (event) => { event.stopPropagation(); this.toggleAncestors(person.id); },
      }, this.collapsedAncestors.has(person.id) ? "+" : "−") : null);
      if (this.query && person.name.toLocaleLowerCase("pl").includes(this.query)) card.classList.add("is-match");
      return card;
    });
    const familyControls = this.renderFamilyControls(baseVisiblePeople);
    const overlayLabel = this.renderConditionalOverlayLabel();
    const ancestryLabel = this.renderConditionalAncestryLabel();
    const canonicalCards = cards.slice(0, people.length);
    const candidateCards = cards.slice(people.length);
    this.layer.replaceChildren(
      ...canonicalCards,
      ...(overlayLabel ? [overlayLabel] : []),
      ...(ancestryLabel ? [ancestryLabel] : []),
      ...candidateCards,
      ...familyControls,
    );
    this.syncSearchCards();
    this.drawConnectors(people);
    this.applyScale();
    if (this.selectedId) this.showInspector(this.renderedPersonById.get(this.selectedId) || this.personById.get(this.selectedId));
    else this.showEmptyInspector();
  }

  renderAncestorCard(slot, position) {
    if (!position) return null;
    const person = slot.person;
    const review = this.dataIndex.personReview(person);
    const candidateCount = (person.candidateLinks || []).length;
    const personContext = this.dataIndex.personContext(person)
      || (person.occurrenceId ? this.dataIndex.occurrenceContext(person.occurrenceId) : null);
    const presentation = presentTreePersonCard(person, {
      variant: this.cardVariant,
      generationLabel: slot.depth === 0 ? "Punkt odniesienia" : `Pokolenie ${slot.depth}`,
      evidenceLabel: this.evidenceLabel(person.evidenceStatus),
      review,
      candidateCount,
      identityScope: personContext?.identityScope || "presentation_only",
      identityComponentCount: personContext?.canonicalPersonIds?.length || 0,
    });
    const card = node("article", {
      class: `tree-person tree-card-${presentation.variant} ${person.evidenceStatus || ""} identity-${presentation.identityScope} ${slot.depth === 0 ? "is-focus" : ""} ${this.selectedAncestorSlotId === slot.slotId ? "is-selected" : ""}`,
      "data-person-id": person.id,
      "data-person-backing-id": person.personId || null,
      "data-occurrence-backing-id": person.occurrenceId || null,
      "data-source-ids": (person.sourceIds || []).join(" "),
      "data-ancestor-slot-id": slot.slotId,
      "data-duplicate-of-slot-id": slot.duplicateOfSlotId || null,
      "data-review-state": review?.state || "none",
      style: `left:${position.x}px;top:${position.y}px`,
    },
    node("button", {
      class: "tree-card-main",
      "aria-label": slot.duplicateOfSlotId
        ? `${presentation.accessibleName}. Ta sama osoba występuje w innej ścieżce przodków`
        : presentation.accessibleName,
      title: presentation.fullName,
      onclick: () => this.select(person.id, { ancestorSlotId: slot.slotId }),
    },
    node("span", { class: `tree-avatar ${person.sex || "unknown"}` }, presentation.initials),
    node("span", { class: "tree-card-copy" },
      node("strong", {}, presentation.displayName),
      node("small", {}, presentation.subtitle),
      node("span", { class: `tree-evidence ${person.evidenceStatus || ""}` }, presentation.evidence),
    )),
    slot.duplicateOfSlotId ? node("span", {
      class: "tree-ancestor-duplicate",
      title: "Ta sama osoba występuje w więcej niż jednej ścieżce przodków",
      "aria-label": "Powtórzona pozycja w pedigree",
    }, "↺ inna ścieżka") : null);
    if (this.query && person.name.toLocaleLowerCase("pl").includes(this.query)) card.classList.add("is-match");
    return card;
  }

  renderAncestors() {
    const projection = this.projectAncestors();
    const stage = this.scroll.closest(".tree-stage");
    const fanMode = this.ancestorLayoutMode === "fan";
    stage?.classList.toggle("is-ancestor-fan", fanMode);
    this.svg.classList.toggle("ancestor-fan-svg", fanMode);

    const generationDepth = Math.max(0, ...projection.slots.map((slot) => slot.depth));
    const selectedSlot = projection.slots.find((slot) => slot.slotId === this.selectedAncestorSlotId);
    if (!selectedSlot || selectedSlot.personId !== this.selectedId) {
      this.selectedAncestorSlotId = projection.slots.find((slot) => (
        slot.personId === this.selectedId && (slot.depth === 0 || this.selectedId !== this.ancestorFocusId)
      ))?.slotId || projection.slots[0]?.slotId || null;
    }
    $("#tree-generation-count").textContent = generationDepth;
    $("#tree-generation-label").textContent = generationDepth === 1
      ? "pokolenie wstecz"
      : generationDepth >= 2 && generationDepth <= 4
        ? "pokolenia wstecz"
        : "pokoleń wstecz";
    $("#tree-person-count").textContent = projection.uniquePersonCount;
    $("#tree-person-count-label").textContent = projection.uniquePersonCount === 1
      ? "unikalna osoba"
      : projection.uniquePersonCount >= 2 && projection.uniquePersonCount <= 4
        ? "unikalne osoby"
        : "unikalnych osób";
    this.updateAncestorDepthControls(projection, generationDepth);

    const minimumWidth = Math.max(760, this.scroll.clientWidth || 0);
    const vertical = this.ancestorVerticalLayout.positions(projection, minimumWidth);
    const layout = fanMode
      ? layoutAncestorFan(projection, { baseWidth: minimumWidth })
      : vertical;
    this.baseWidth = layout.width;
    this.surfaceHeight = layout.height;
    this.layoutGenerationSpan = Math.max(0, ...projection.slots.map((slot) => slot.depth));
    this.surface.style.width = `${layout.width}px`;
    this.surface.style.height = `${layout.height}px`;
    this.svg.setAttribute("viewBox", fanMode ? layout.viewBox : `0 0 ${layout.width} ${layout.height}`);
    this.svg.setAttribute("width", layout.width);
    this.svg.setAttribute("height", layout.height);

    this.positionById = new Map();
    this.positionBySlotId = fanMode
      ? new Map(layout.sectors.map((sector) => [sector.slotId, {
          x: sector.center.x - this.cardWidth / 2,
          y: sector.center.y - this.cardHeight / 2,
          depth: sector.depth,
        }]))
      : vertical.positions;
    projection.slots.forEach((slot) => {
      if (!this.positionById.has(slot.personId)) this.positionById.set(slot.personId, this.positionBySlotId.get(slot.slotId));
    });
    const cards = projection.slots
      .map((slot) => this.renderAncestorCard(slot, vertical.positions.get(slot.slotId)))
      .filter(Boolean);
    this.layer.replaceChildren(...cards);

    if (fanMode) {
      this.svg.removeAttribute("aria-hidden");
      this.ancestorFanRenderer.render({
        layout,
        selectedSlotId: this.selectedAncestorSlotId,
        selectedPersonId: this.selectedId,
      });
    } else {
      this.svg.setAttribute("aria-hidden", "true");
      const namespace = "http://www.w3.org/2000/svg";
      const edgesById = new Map(projection.edges.map((edge) => [edge.edgeId, edge]));
      const paths = ancestorConnectorsGeometry({
        edges: projection.edges,
        positions: vertical.positions,
        cardWidth: this.cardWidth,
        cardHeight: this.cardHeight,
      }).map((geometry) => {
        const edge = edgesById.get(geometry.edgeId);
        const path = document.createElementNS(namespace, "path");
        path.setAttribute("d", geometry.path);
        path.setAttribute("class", `tree-link ${edge?.status || ""}`);
        path.setAttribute("data-ancestor-edge-id", geometry.edgeId || "");
        path.setAttribute("data-family-id", edge?.familyId || "");
        path.setAttribute("data-source-ids", (edge?.sourceIds || []).join(" "));
        return path;
      });
      this.svg.replaceChildren(...paths);
    }
    this.applyScale();
    if (this.selectedId) this.showInspector(this.personById.get(this.selectedId));
    else this.showEmptyInspector();
  }

  drawConnectors(people) {
    const network = this.activeNetwork();
    if (network) {
      const projection = this.activeNetworkProjection();
      this.renderer.drawSourceReportedNetwork({
        network: { ...network, edges: projection?.edges || [] },
        people,
        positions: this.positionById,
        cardWidth: this.cardWidth,
        cardHeight: this.cardHeight,
      });
      return;
    }
    const activeBranch = this.branchById.get(this.branchId);
    this.renderer.drawConnectors({
      tree: this.tree,
      people,
      positions: this.positionById,
      cardWidth: this.cardWidth,
      cardHeight: this.cardHeight,
      focusIds: this.mode === "branch" ? activeBranch?.personIds || [] : this.tree.focusPath || [],
    });
    if (this.unifiedProjection) appendUnifiedAncestryEdges({
      svg: this.svg, edges: this.unifiedProjection.edges, positions: this.positionById,
      cardWidth: this.cardWidth, cardHeight: this.cardHeight,
    });
    if (this.conditionalOverlayProjection?.network) {
      this.renderer.drawConditionalExternalOverlay({
        projection: this.conditionalOverlayProjection,
        canonicalPeople: people,
        candidatePeople: this.conditionalOverlayProjection.people,
        positions: this.positionById,
        cardWidth: this.cardWidth,
        cardHeight: this.cardHeight,
      });
    }
    if (this.conditionalAncestryProjection?.root) {
      this.renderer.drawConditionalAncestryOverlay({
        projection: this.conditionalAncestryProjection,
        positions: this.positionById,
        cardWidth: this.cardWidth,
        cardHeight: this.cardHeight,
      });
    }
  }

  renderConditionalOverlayLabel() {
    const projection = this.conditionalOverlayProjection;
    const overlayLayout = this.conditionalOverlayLayout;
    if (!projection?.network || !overlayLayout?.islandWidth) return null;
    const presentation = sourceReportedNetworkPresentation(projection.network);
    const listStyle = this.presentationMode === "list"
      ? "position:relative;left:auto;top:auto;width:auto;min-height:44px;grid-column:1/-1"
      : `position:absolute;left:${overlayLayout.islandX + 12}px;top:5px;width:${Math.max(240, overlayLayout.islandWidth - 24)}px;min-height:40px`;
    return node("aside", {
      class: "tree-conditional-overlay-label",
      "data-conditional-network-id": projection.network.id,
      style: `${listStyle};z-index:7;box-sizing:border-box;padding:6px 10px;border:1px dashed #b56b48;border-radius:9px;background:#fff4e9;color:#735f3e;font-size:10px;line-height:1.25`,
    },
    node("strong", { style: "display:block;color:#8d472b" }, `Gałąź kandydacka: ${presentation.label}`),
    node("span", {}, `${this.conditionalOverlayCountSummary()}. ${projection.attachmentNotice} Relacje pozostają source-reported, nierozstrzygnięte i niekanoniczne.`));
  }

  renderConditionalAncestryLabel() {
    const projection = this.conditionalAncestryProjection;
    const overlayLayout = this.conditionalAncestryLayout;
    if (!projection?.root || !overlayLayout?.islandWidth) return null;
    const listStyle = this.presentationMode === "list"
      ? "position:relative;left:auto;top:auto;width:auto;min-height:44px;grid-column:1/-1"
      : `position:absolute;left:${overlayLayout.islandX + 12}px;top:5px;width:${Math.max(240, overlayLayout.islandWidth - 24)}px;min-height:40px`;
    const gapConditions = [...new Set((projection.gaps || []).map((gap) => gap.conditionLabel || gap.label).filter(Boolean))];
    return node("aside", {
      class: "tree-conditional-ancestry-label",
      "data-conditional-ancestry-root-id": projection.root.id,
      style: `${listStyle};z-index:7;pointer-events:none;box-sizing:border-box;padding:6px 10px;border:1px dashed #8d6b2f;border-radius:9px;background:#f7f0df;color:#66542f;font-size:10px;line-height:1.3`,
    },
    node("strong", { style: "display:block;color:#6f511f" }, `Warunkowi przodkowie: ${projection.root.label}`),
    node("span", {}, conditionalAncestrySummary(projection)),
    gapConditions.length
      ? node("span", { style: "display:block;margin-top:3px" }, `Warunek dalszej pracy: ${gapConditions.join("; ")}`)
      : null);
  }

  renderFamilyControls(people) {
    if (this.activeNetwork()) return [];
    const visible = new Set(people.map((person) => person.id));
    const baseIds = new Set(this.basePeople().map((person) => person.id));
    return (this.tree?.families || []).flatMap((family) => {
      const children = (family.childIds || []).filter((id) => baseIds.has(id));
      if (family.showAllChildren === true || children.length < 2) return [];
      const primaryChildId = this.primaryChildId(family, children);
      const geometry = familyConnectorGeometry({
        family,
        visibleIds: visible,
        positions: this.positionById,
        cardWidth: this.cardWidth,
        cardHeight: this.cardHeight,
      });
      if (!geometry) return [];
      const expanded = this.expandedFamilies.has(family.id);
      const hiddenCount = expanded ? 0 : children.length - 1;
      const parentNames = (family.parentIds || []).map((id) => this.personById.get(id)?.name).filter(Boolean).join(" i ");
      return [node("button", {
        class: `tree-family-toggle ${expanded ? "is-expanded" : ""}`,
        "data-family-id": family.id,
        style: `left:${geometry.controlX}px;top:${geometry.controlY}px`,
        title: expanded ? "Zwiń pozostałe dzieci i ich linie" : "Pokaż pozostałe dzieci",
        "aria-label": expanded
          ? `Zwiń pozostałe dzieci: ${parentNames}`
          : `Pokaż wszystkie dzieci: ${parentNames}`,
        onclick: (event) => { event.stopPropagation(); this.toggleFamilyChildren(family.id, primaryChildId); },
      }, expanded ? `Dzieci ${children.length}/${children.length} −` : `Dzieci 1/${children.length} +${hiddenCount}`)];
    });
  }

  evidenceLabel(status) {
    return ({ documented: "akt", agent_approved: "sprawdzone przez agentów", family_known: "materiał rodzinny", private_unverified: "prywatne · niezweryfikowane", masked_living: "dane chronione", unchecked: "niesprawdzone", unresolved: "nierozstrzygnięte" })[status] || "status nieustalony";
  }

  setPrivateOverlay(overlay) {
    const replacements = new Map((overlay?.people || []).map((person) => [person.treePersonId, person]));
    this.tree = replacements.size && this.publicTree
      ? {
          ...this.publicTree,
          people: (this.publicTree.people || []).map((person) => {
            const replacement = replacements.get(person.id);
            if (!replacement) return person;
            return {
              ...person,
              name: replacement.name,
              summary: replacement.summary,
              sex: replacement.sex || person.sex,
              privacy: replacement.privacy,
              evidenceStatus: replacement.evidenceStatus,
              facts: [],
              occurrenceIds: [],
              sourceIds: [],
            };
          }),
        }
      : this.publicTree;
    this.model = new FamilyTreeModel(this.tree);
    this.personById = this.model.personById;
    this.branchById = this.model.branchById;
    this.networkById = this.model.networkById;
    this.networkId = this.networkById.has(this.networkId) ? this.networkId : this.model.defaultNetworkId();
    this.conditionalOverlayNetworkId = this.networkById.has(this.conditionalOverlayNetworkId)
      ? this.conditionalOverlayNetworkId
      : this.model.defaultNetworkId();
    if (!this.model.isExternalNetworkFilter(this.candidateRoleFilter)) this.candidateRoleFilter = "all";
    if (!this.model.isExternalNetworkFilter(this.conditionalOverlayRoleFilter)) this.conditionalOverlayRoleFilter = "all";
    this.ensureNetworkFilterControl();
    this.ensureConditionalOverlayControls();
    this.ensureConditionalAncestryControls();
    this.updateNetworkFilterControl();
    this.updateConditionalOverlayControls();
    this.updateConditionalAncestryControls();
    this.ancestorProjection = null;
    if (!this.personById.has(this.ancestorFocusId)) this.ancestorFocusId = this.tree?.rootPersonId || null;
    this.render();
  }

  select(personId, { reveal = true, ancestorSlotId = null, includeParents = true } = {}) {
    const person = this.renderedPersonById?.get(personId) || this.personById.get(personId);
    this.selectedId = personId;
    if (this.mode === "ancestors") {
      const projection = this.ancestorProjection || this.projectAncestors();
      const requestedSlot = projection.slots.find((slot) => slot.slotId === ancestorSlotId && slot.personId === personId);
      const retainedSlot = projection.slots.find((slot) => slot.slotId === this.selectedAncestorSlotId && slot.personId === personId);
      this.selectedAncestorSlotId = (requestedSlot || retainedSlot || projection.slots.find((slot) => slot.personId === personId))?.slotId || null;
    } else {
      this.selectedAncestorSlotId = null;
    }
    $$(".tree-person", this.layer).forEach((card) => card.classList.toggle(
      "is-selected",
      this.mode === "ancestors"
        ? card.dataset.ancestorSlotId === this.selectedAncestorSlotId
        : card.dataset.personId === personId,
    ));
    if (this.mode !== "ancestors") this.syncSearchCards();
    this.persistExternalSelection(person);
    this.showInspector(person);
    document.dispatchEvent(new CustomEvent("genea:tree-person-selected", {
      detail: { personId, ancestorSlotId: this.selectedAncestorSlotId },
    }));
    if (!reveal) return;
    this.cameraMode = "focus";
    this.cameraIncludeParents = includeParents && this.unifiedTreeActive();
    if (this.mode === "ancestors" && this.ancestorLayoutMode === "fan") {
      $$(".ancestor-fan-sector", this.svg).forEach((sector) => {
        const selected = sector.dataset.slotId === this.selectedAncestorSlotId;
        sector.classList.toggle("is-selected", selected);
        if (selected) sector.setAttribute("aria-current", "true");
        else sector.removeAttribute("aria-current");
      });
      requestAnimationFrame(() => this.revealAncestorSlot(this.selectedAncestorSlotId, {
        minimumScale: MIN_ANCESTOR_FAN_SCALE,
      }));
      return;
    }
    if (this.mode === "ancestors") {
      requestAnimationFrame(() => this.revealAncestorSlot(this.selectedAncestorSlotId, {
        minimumScale: MIN_ANCESTOR_VERTICAL_SCALE,
      }));
      return;
    }
    requestAnimationFrame(() => this.revealPerson(personId, {
      minimumScale: MIN_FOCUS_SCALE, includeParents: this.cameraIncludeParents,
    }));
  }

  toggleAncestors(personId) {
    if (this.collapsedAncestors.has(personId)) this.collapsedAncestors.delete(personId);
    else this.collapsedAncestors.add(personId);
    this.selectedId = personId;
    this.renderKeepingPerson(personId);
  }

  toggleFamilyChildren(familyId, anchorId = null) {
    if (this.expandedFamilies.has(familyId)) this.expandedFamilies.delete(familyId);
    else this.expandedFamilies.add(familyId);
    const family = (this.tree?.families || []).find((item) => item.id === familyId);
    const anchor = anchorId || this.primaryChildId(family);
    if (anchor) this.selectedId = anchor;
    this.renderKeepingPerson(anchor);
  }

  toggleUnions(personId) {
    if (this.collapsedUnions.has(personId)) this.collapsedUnions.delete(personId);
    else {
      const activeUnion = this.activeUnion(personId);
      if (activeUnion) this.activeUnionByOwner.set(personId, activeUnion.id);
      this.collapsedUnions.add(personId);
    }
    this.selectedId = personId;
    this.renderKeepingPerson(personId);
  }

  cycleUnion(personId, direction) {
    const unions = this.ownedUnions(personId);
    if (unions.length < 2) return;
    const current = this.activeUnion(personId);
    const currentIndex = Math.max(0, unions.findIndex((union) => union.id === current?.id));
    const nextIndex = (currentIndex + direction + unions.length) % unions.length;
    this.activeUnionByOwner.set(personId, unions[nextIndex].id);
    this.collapsedUnions.add(personId);
    this.selectedId = personId;
    this.renderKeepingPerson(personId);
  }

  showUnion(personId, unionId) {
    if (!this.ownedUnions(personId).some((union) => union.id === unionId)) return;
    this.activeUnionByOwner.set(personId, unionId);
    this.collapsedUnions.add(personId);
    this.selectedId = personId;
    this.renderKeepingPerson(personId);
  }

  renderKeepingPerson(personId) {
    const before = $(`[data-person-id="${personId}"]`, this.layer)?.getBoundingClientRect();
    this.render();
    const after = $(`[data-person-id="${personId}"]`, this.layer)?.getBoundingClientRect();
    if (!before || !after) return;
    this.scroll.scrollBy({
      left: after.left - before.left,
      top: after.top - before.top,
      behavior: "auto",
    });
  }

  showEmptyInspector() {
    this.onInspect(null);
  }

  showInspector(person) {
    if (person?.conditionalAncestry) {
      const conditionalNode = person.conditionalAncestryNode;
      const fullRecordHref = conditionalNode.publicProfileId
        ? new URL(`people/${encodeURIComponent(conditionalNode.publicProfileId)}`, document.baseURI).toString()
        : null;
      this.inspectTreePerson(person, buildConditionalAncestryInspectorModel(conditionalNode, { fullRecordHref }));
      return;
    }
    if (person?.sourceReported) {
      const conditionalOverlayPerson = this.conditionalOverlayPersonIds.has(person.id);
      const network = conditionalOverlayPerson
        ? this.model.network(person.sourceReportedNetworkId)
        : this.activeNetwork();
      const activeFilter = conditionalOverlayPerson
        ? this.conditionalOverlayRoleFilter
        : this.candidateRoleFilter;
      this.inspectTreePerson(person, buildSourceReportedTreeInspectorModel(person, {
        network,
        activeFilter,
        fullRecordHref: this.candidateProfileHref(
          person.publicProfileId || person.id,
          person.sourceReportedNetworkId,
          activeFilter,
        ),
        canFilter: (candidateRole) => this.model.isExternalNetworkFilter(candidateRole),
        setFilter: (candidateRole) => conditionalOverlayPerson
          ? this.setConditionalOverlayRoleFilter(candidateRole)
          : this.setCandidateRoleFilter(candidateRole),
      }, this.dataIndex));
      return;
    }
    this.inspectTreePerson(person, buildFamilyTreeInspectorModel(person, {
      tree: this.tree,
      personById: this.personById,
      collapsedUnions: this.collapsedUnions,
      collapsedAncestors: this.collapsedAncestors,
      expandedFamilies: this.expandedFamilies,
      ownedUnions: (personId) => this.ownedUnions(personId),
      activeUnion: (personId) => this.activeUnion(personId),
      unionStatusLabel: (status) => this.unionStatusLabel(status),
      showUnion: (personId, unionId) => this.showUnion(personId, unionId),
      focusPerson: (personId) => this.focusPerson(personId),
      additionalUnions: (personId) => this.additionalUnions(personId),
      toggleUnions: (personId) => this.toggleUnions(personId),
      hasAncestorBranch: (personId) => this.hasAncestorBranch(personId),
      toggleAncestors: (personId) => this.unifiedProjection
        ? this.toggleUnifiedParents(personId, {
            base: this.hasAncestorBranch(personId),
            conditional: this.hasUnifiedConditionalParents(personId),
          })
        : this.toggleAncestors(personId),
      expandableFamilies: (personId) => this.expandableFamilies(personId),
      toggleFamilyChildren: (familyId, personId) => this.toggleFamilyChildren(familyId, personId),
      generationLabel: (item) => this.generationLabel(item),
      evidenceLabel: (status) => this.evidenceLabel(status),
      ancestorScope: this.mode === "ancestors" ? {
        focusPersonId: this.ancestorFocusId,
        setFocus: (personId) => this.setAncestorFocus(personId),
        showInFullTree: (personId) => {
          this.setMode("all");
          $$("[data-tree-mode]").forEach((button) => button.classList.toggle("is-active", button.dataset.treeMode === "all"));
          this.focusPerson(personId);
        },
      } : null,
    }, this.dataIndex));
  }

  unionStatusLabel(status) {
    return ({ documented: "udokumentowane", agent_approved: "sprawdzone przez agentów", unresolved: "nierozstrzygnięte", unchecked: "niezaimportowane", family_recorded: "zapisani jako rodzice", masked_living: "dane chronione" })[status] || status || "brak statusu";
  }

  focusPerson(personId) {
    this.closeFocusedAncestry({ render: false });
    const sourceReported = this.personById.get(personId);
    if (sourceReported?.sourceReported) {
      this.networkId = sourceReported.sourceReportedNetworkId;
      if (this.mode !== "network") {
        this.setMode("network");
        $$('[data-tree-mode]').forEach((button) => button.classList.toggle("is-active", button.dataset.treeMode === "network"));
      }
      this.select(personId);
      return;
    }
    if (this.mode === "ancestors") {
      this.setAncestorFocus(personId);
      return;
    }
    let visibilityChanged = false;
    let anchorOwnerId = null;
    for (const family of this.tree.families || []) {
      if (family.showAllChildren !== true && (family.childIds || []).includes(personId) && !this.expandedFamilies.has(family.id)) {
        this.expandedFamilies.add(family.id);
        visibilityChanged = true;
        anchorOwnerId = this.primaryChildId(family);
      }
    }
    for (const union of this.tree.unions || []) {
      if (union.partnerIds.includes(personId) && union.ownerId && this.collapsedUnions.has(union.ownerId)) {
        if (this.activeUnion(union.ownerId)?.id !== union.id) {
          this.activeUnionByOwner.set(union.ownerId, union.id);
          visibilityChanged = true;
          anchorOwnerId = union.ownerId;
        }
      }
    }
    if (visibilityChanged) this.renderKeepingPerson(anchorOwnerId);
    if (!this.positionById.has(personId)) {
      this.setMode("all");
      $$("[data-tree-mode]").forEach((button) => button.classList.toggle("is-active", button.dataset.treeMode === "all"));
    }
    this.select(personId, { includeParents: true });
  }

  bindPan() {
    const flushPan = () => {
      this.panFrame = null;
      const pending = this.pendingPan;
      this.pendingPan = null;
      if (!pending || !this.pan) return;
      this.scroll.scrollLeft = this.pan.left - (pending.x - this.pan.x);
      this.scroll.scrollTop = this.pan.top - (pending.y - this.pan.y);
    };
    const finish = () => {
      if (this.pendingPan) flushPan();
      this.pan = null;
      this.pendingPan = null;
      if (this.panFrame) cancelAnimationFrame(this.panFrame);
      this.panFrame = null;
      this.scroll.classList.remove("is-panning");
    };
    this.scroll.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || event.target.closest("button, input, select, a, .tree-person")) return;
      this.pan = { x: event.clientX, y: event.clientY, left: this.scroll.scrollLeft, top: this.scroll.scrollTop };
      this.scroll.setPointerCapture(event.pointerId);
      this.scroll.classList.add("is-panning");
    });
    this.scroll.addEventListener("pointermove", (event) => {
      if (!this.pan) return;
      this.pendingPan = { x: event.clientX, y: event.clientY };
      if (!this.panFrame) this.panFrame = requestAnimationFrame(flushPan);
    });
    this.scroll.addEventListener("pointerup", finish);
    this.scroll.addEventListener("pointercancel", finish);
    this.scroll.addEventListener("lostpointercapture", finish);
    this.scroll.addEventListener("wheel", (event) => {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      const bounds = this.scroll.getBoundingClientRect();
      const previous = this.pendingWheelZoom;
      this.pendingWheelZoom = {
        delta: Math.max(-.18, Math.min(.18, (previous?.delta || 0) - event.deltaY * .002)),
        anchorX: event.clientX - bounds.left,
        anchorY: event.clientY - bounds.top,
      };
      if (this.wheelZoomFrame) return;
      this.wheelZoomFrame = requestAnimationFrame(() => {
        this.wheelZoomFrame = null;
        const pending = this.pendingWheelZoom;
        this.pendingWheelZoom = null;
        if (pending && Math.abs(pending.delta) > .001) this.zoomBy(pending.delta, pending);
      });
    }, { passive: false });
    this.scroll.addEventListener("scroll", () => this.scheduleExternalCameraPersistence(), { passive: true });
  }

  search(value) {
    this.query = value.trim().toLocaleLowerCase("pl");
    if (this.focusedAncestryEnabled) {
      if (!this.query) return;
      const match = this.focusedAncestryScenario?.nodes.find((item) => item.label.toLocaleLowerCase("pl").includes(this.query));
      if (match) this.focusedAncestryView.setScenario(this.focusedAncestryScenario, { focusNodeId: match.id });
      return;
    }
    if (this.mode === "ancestors" && this.query) {
      const match = (this.tree?.people || []).find((person) =>
        person.name.toLocaleLowerCase("pl").includes(this.query)
      );
      if (match) this.setAncestorFocus(match.id);
      return;
    }
    this.syncSearchCards();
    if (!this.query) return;
    const match = [...(this.renderedPersonById?.values() || this.basePeople())].find((person) => person.name.toLocaleLowerCase("pl").includes(this.query));
    if (match) {
      if (match.conditionalAncestry && this.unifiedTreeActive()) this.select(match.id, { includeParents: true });
      else this.focusPerson(match.id);
    }
  }

  syncSearchCards() {
    const readableIds = this.unifiedTreeActive()
      ? this.visibleParentNeighborhoodIds(this.selectedId)
      : new Set();
    $$(".tree-person", this.layer).forEach((card) => {
      const person = this.renderedPersonById?.get(card.dataset.personId) || this.personById.get(card.dataset.personId);
      if (!person) return;
      const matched = Boolean(this.query) && person.name.toLocaleLowerCase("pl").includes(this.query);
      card.classList.toggle("is-match", matched);
      card.classList.toggle("is-dimmed", Boolean(person.outsideFilterConnector)
        || (Boolean(this.query) && !matched && !readableIds.has(person.id)));
    });
  }

  zoomBy(delta, { anchorX = this.scroll.clientWidth / 2, anchorY = this.scroll.clientHeight / 2 } = {}) {
    if (this.focusedAncestryEnabled) return;
    const oldWidth = Math.max(1, this.baseWidth * this.scale);
    const oldHeight = Math.max(1, (this.surfaceHeight || 1) * this.scale);
    const anchorXRatio = (this.scroll.scrollLeft + anchorX) / oldWidth;
    const anchorYRatio = (this.scroll.scrollTop + anchorY) / oldHeight;
    const nextScale = Math.max(MIN_OVERVIEW_SCALE, Math.min(MAX_SCALE, this.scale + delta));
    if (nextScale === this.scale) return;
    this.cameraMode = "manual";
    this.scale = nextScale;
    this.cardVariant = semanticTreeCardVariant(this.scale, this.cardVariant);
    this.render();
    const newWidth = this.baseWidth * this.scale;
    const newHeight = this.surfaceHeight * this.scale;
    this.scroll.scrollTo({
      left: Math.max(0, anchorXRatio * newWidth - anchorX),
      top: Math.max(0, anchorYRatio * newHeight - anchorY),
      behavior: "auto",
    });
    this.scheduleExternalCameraPersistence();
  }

  applyScale() {
    const height = this.surfaceHeight || this.top * 2 + this.layoutGenerationSpan * this.generationStep() + this.cardHeight;
    this.surface.style.transform = `scale(${this.scale})`;
    this.inner.style.width = `${this.baseWidth * this.scale}px`;
    this.inner.style.height = `${height * this.scale}px`;
    $("#tree-zoom-value").textContent = `${Math.round(this.scale * 100)}%`;
  }

  estimatedSurfaceHeight(scale) {
    if (this.mode === "ancestors") return Math.max(1, this.surfaceHeight || 1);
    const bottomCameraSpace = Math.max(
      this.top,
      this.scroll.clientHeight / (2 * Math.max(scale, MIN_OVERVIEW_SCALE)) - this.cardHeight / 2,
    );
    return this.top + this.layoutGenerationSpan * this.layout.generationStep() + this.cardHeight + bottomCameraSpace;
  }

  home({ behavior = "smooth" } = {}) {
    if (this.focusedAncestryEnabled) return;
    const rootId = this.mode === "ancestors" ? this.ancestorFocusId : this.tree?.rootPersonId;
    if (!rootId || !this.positionById?.has(rootId)) {
      this.fit({ behavior });
      return;
    }
    this.cameraMode = "focus";
    let homeScale = this.mode === "ancestors" && this.ancestorLayoutMode === "fan"
      ? 1
      : this.mode === "ancestors" ? MIN_ANCESTOR_VERTICAL_SCALE : DEFAULT_HOME_SCALE;
    if (this.unifiedTreeActive()) homeScale = Math.max(.85, homeScale);
    if (Math.abs(this.scale - homeScale) > .001) {
      this.scale = homeScale;
      this.cardVariant = semanticTreeCardVariant(this.scale, this.cardVariant);
      this.render();
    }
    this.revealPerson(rootId, { minimumScale: homeScale, behavior });
  }

  fit({ behavior = "smooth" } = {}) {
    if (this.focusedAncestryEnabled) return;
    if (!this.scroll.clientWidth || !this.scroll.clientHeight) return;
    this.cameraMode = this.mode === "ancestors" ? "focus" : "overview";
    const width = Math.max(240, this.scroll.clientWidth - CAMERA_PADDING);
    const height = Math.max(240, this.scroll.clientHeight - CAMERA_PADDING);
    const widthScale = width / Math.max(1, this.baseWidth);
    let nextScale = Math.max(MIN_OVERVIEW_SCALE, Math.min(1, widthScale));
    const heightScale = height / Math.max(1, this.estimatedSurfaceHeight(nextScale));
    nextScale = Math.max(MIN_OVERVIEW_SCALE, Math.min(1, widthScale, heightScale));
    if (this.unifiedTreeActive()) nextScale = Math.max(.85, nextScale);
    if (this.mode === "ancestors") {
      const readableScale = this.ancestorLayoutMode === "fan"
        ? MIN_ANCESTOR_FAN_SCALE
        : MIN_ANCESTOR_VERTICAL_SCALE;
      nextScale = Math.max(readableScale, nextScale);
    }
    const scaleChanged = Math.abs(nextScale - this.scale) > .001;
    this.scale = nextScale;
    this.cardVariant = semanticTreeCardVariant(this.scale, this.cardVariant);
    if (scaleChanged) this.render();
    else this.applyScale();
    if (this.mode === "ancestors") {
      this.revealAncestorSlot(this.selectedAncestorSlotId, { minimumScale: nextScale, behavior });
      return;
    }
    if (this.unifiedTreeActive()) {
      this.revealPerson(this.selectedId || this.tree.rootPersonId, { minimumScale: .85, behavior });
      return;
    }
    this.scroll.scrollTo({
      left: Math.max(0, (this.baseWidth * this.scale - this.scroll.clientWidth) / 2),
      top: Math.max(0, (this.surfaceHeight * this.scale - this.scroll.clientHeight) / 2),
      behavior,
    });
    this.scheduleExternalCameraPersistence();
  }

  visibleCameraViewport() {
    const rect = this.scroll.getBoundingClientRect();
    const left = Math.max(0, rect.left);
    const top = Math.max(0, rect.top);
    return {
      width: Math.max(0, Math.min(rect.left + this.scroll.clientWidth, window.innerWidth) - left),
      height: Math.max(0, Math.min(rect.top + this.scroll.clientHeight, window.innerHeight) - top),
      offsetX: left - rect.left,
      offsetY: top - rect.top,
    };
  }

  visibleParentNeighborhoodIds(personId) {
    const ids = new Set([personId]);
    // The rendered projection is already privacy filtered; only structured edges
    // and visible positions can contribute to this camera neighborhood.
    for (const family of this.unifiedProjection?.layoutFamilies || []) {
      if ((family.childIds || []).includes(personId)) {
        for (const parentId of family.parentIds || []) ids.add(parentId);
      }
    }
    for (const edge of this.unifiedProjection?.edges || []) {
      if (edge.fromDisplayId === personId) ids.add(edge.toDisplayId);
    }
    return new Set([...ids].filter((id) => this.positionById?.has(id)));
  }

  visibleParentNeighborhood(personId) {
    const positions = [...this.visibleParentNeighborhoodIds(personId)].map((id) => this.positionById.get(id));
    return {
      left: Math.min(...positions.map((position) => position.x)),
      right: Math.max(...positions.map((position) => position.x + this.cardWidth)),
      top: Math.min(...positions.map((position) => position.y)),
      bottom: Math.max(...positions.map((position) => position.y + this.cardHeight)),
    };
  }

  revealPerson(personId, { minimumScale = 0, behavior = "smooth", includeParents = false } = {}) {
    if (!this.positionById?.has(personId) || !this.scroll.clientWidth) return;
    let viewport = includeParents && this.unifiedTreeActive() ? this.visibleCameraViewport() : null;
    if (viewport && viewport.height < this.cardHeight * .85 + PARENT_CAMERA_PADDING) {
      // The phone toolbar can place the entire canvas below the browser viewport.
      this.scroll.scrollIntoView({ block: "start", behavior: "auto" });
      viewport = this.visibleCameraViewport();
    }
    if (viewport && (!viewport.width || !viewport.height)) return;
    let neighborhood = null;
    if (viewport) {
      minimumScale = Math.max(.85, minimumScale);
      const bounds = this.visibleParentNeighborhood(personId);
      const fitScale = Math.min(
        (viewport.width - PARENT_CAMERA_PADDING) / (bounds.right - bounds.left),
        (viewport.height - PARENT_CAMERA_PADDING) / (bounds.bottom - bounds.top),
      );
      if (fitScale >= minimumScale) {
        neighborhood = bounds;
        const nextScale = Math.min(Math.max(this.scale, minimumScale), fitScale, MAX_SCALE);
        if (Math.abs(nextScale - this.scale) > .001) {
          this.scale = nextScale;
          this.cardVariant = semanticTreeCardVariant(this.scale, this.cardVariant);
          this.render();
          neighborhood = this.visibleParentNeighborhood(personId);
        }
      } else {
        const selectedFitScale = Math.min(
          (viewport.width - PARENT_CAMERA_PADDING) / this.cardWidth,
          (viewport.height - PARENT_CAMERA_PADDING) / this.cardHeight,
        );
        const nextScale = Math.max(minimumScale, Math.min(this.scale, selectedFitScale));
        if (Math.abs(nextScale - this.scale) > .001) {
          this.scale = nextScale;
          this.cardVariant = semanticTreeCardVariant(this.scale, this.cardVariant);
          this.render();
        }
      }
    }
    if (this.scale < minimumScale) {
      this.scale = Math.min(MAX_SCALE, minimumScale);
      this.cardVariant = semanticTreeCardVariant(this.scale, this.cardVariant);
      this.render();
    }
    const position = this.positionById.get(personId);
    const centerX = (neighborhood ? (neighborhood.left + neighborhood.right) / 2 : position.x + this.cardWidth / 2) * this.scale;
    const centerY = (neighborhood ? (neighborhood.top + neighborhood.bottom) / 2 : position.y + this.cardHeight / 2) * this.scale;
    this.scroll.scrollTo({
      left: Math.max(0, centerX - (viewport ? viewport.offsetX + viewport.width / 2 : this.scroll.clientWidth / 2)),
      top: Math.max(0, centerY - (viewport ? viewport.offsetY + viewport.height / 2 : this.scroll.clientHeight / 2)),
      behavior,
    });
    this.scheduleExternalCameraPersistence();
  }

  revealAncestorSlot(slotId, { minimumScale = null, behavior = "smooth" } = {}) {
    const fallbackSlotId = this.ancestorProjection?.slots.find((slot) => slot.personId === this.selectedId)?.slotId;
    const resolvedSlotId = this.positionBySlotId?.has(slotId) ? slotId : fallbackSlotId;
    if (!resolvedSlotId || !this.positionBySlotId?.has(resolvedSlotId) || !this.scroll.clientWidth) return;
    const readableScale = minimumScale ?? (this.ancestorLayoutMode === "fan"
      ? MIN_ANCESTOR_FAN_SCALE
      : MIN_ANCESTOR_VERTICAL_SCALE);
    if (this.scale < readableScale) {
      this.scale = Math.min(MAX_SCALE, readableScale);
      this.cardVariant = semanticTreeCardVariant(this.scale, this.cardVariant);
      this.render();
    }
    const position = this.positionBySlotId.get(resolvedSlotId);
    const centerX = (position.x + this.cardWidth / 2) * this.scale;
    const centerY = (position.y + this.cardHeight / 2) * this.scale;
    this.scroll.scrollTo({
      left: Math.max(0, centerX - this.scroll.clientWidth / 2),
      top: Math.max(0, centerY - this.scroll.clientHeight / 2),
      behavior,
    });
  }

  handleResize() {
    if (this.focusedAncestryEnabled) return;
    if (!this.surface.style.transform) this.applyScale();
    if (!this.scroll.clientWidth) return;
    if (this.cameraMode === "overview") {
      this.fit({ behavior: "auto" });
      return;
    }
    if (this.cameraMode === "focus" && this.selectedId) {
      requestAnimationFrame(() => {
        if (this.mode === "ancestors") this.revealAncestorSlot(this.selectedAncestorSlotId, { behavior: "auto" });
        else this.revealPerson(this.selectedId, { behavior: "auto", includeParents: this.cameraIncludeParents });
      });
    }
  }

  ensureVisible() {
    this.handleResize();
  }
}
