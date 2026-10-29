import { $, node } from "../core/dom.mjs";
import { DataIndex } from "../core/data-index.mjs";
import { bindPrimaryControls } from "../core/navigation.mjs";
import { FamilyTree } from "../tree/family-tree-controller.mjs";
import { ResearchGraphController } from "../graph/research-graph-controller.mjs";
import { buildGraphInspectorModel } from "../graph/graph-inspector.mjs";
import { LineageMapController } from "../map/lineage-map-controller.mjs";

import type {
  GraphInspectorModel,
  MapInspection,
  PresentationPayload,
  TreeInspectorModel,
  ViewName,
  PresentedPersonContext,
} from "./types/presentation";
import { externalNetworkDeepLinkFromSearch, treePersonDeepLinkFromSearch } from "./router";


interface RuntimeController {
  ensureVisible?: () => void;
  render?: () => void;
  resize?: () => void;
}

interface ExternalNetworkRuntimeController {
  applyExternalNetworkDeepLink?: (link: unknown) => boolean;
  clearExternalNetworkDeepLink?: () => boolean;
}

interface LegacyRuntime {
  activate: (view: ViewName) => void;
  focusPerson: (view: ViewName, context: PresentedPersonContext) => boolean;
}

function renderLegacyCounters(payload: PresentationPayload): void {
  const counts = payload.meta.counts;
  $("#people-count").textContent = counts.people;
  $("#event-count").textContent = counts.events;
  $("#candidate-count").textContent = counts.identityCandidates;
  $("#map-place-count").textContent = counts.mapPlaces || payload.map?.places?.length || 0;
  $("#map-observation-count").textContent =
    counts.mapObservations || payload.map?.observations?.length || 0;
  $("#method-note").textContent = payload.meta.methodNote;
}

export function showLoadFailure(error: unknown): void {
  const loading = $("#loading-view");
  loading.replaceChildren(
    node("h1", {}, "Nie udało się wczytać eksportu"),
    node(
      "p",
      {},
      "Uruchom: ./genea frontend-export, a następnie ./genea frontend-serve",
    ),
    node("code", {}, String(error)),
  );
}

export function bootstrapLegacyViews(
  payload: PresentationPayload,
  inspectMap: (inspection: MapInspection | null) => void,
  inspectGraph: (inspection: GraphInspectorModel | null) => void,
  inspectTree: (inspection: TreeInspectorModel | null) => void,
): LegacyRuntime {
  const legacyPayload = payload as never;
  const dataIndex = new DataIndex(legacyPayload);
  renderLegacyCounters(payload);
  const mapFilterPanel = document.querySelector<HTMLDetailsElement>(".map-filter-panel");
  if (mapFilterPanel) mapFilterPanel.open = !window.matchMedia("(max-width: 760px)").matches;

  const controllers: Record<string, RuntimeController> = {};
  const topLevelNetworks = payload.sourceReportedNetworks;
  const nestedNetworks = payload.familyTree?.sourceReportedNetworks;
  const sourceReportedNetworks = topLevelNetworks?.length
    ? topLevelNetworks
    : nestedNetworks ?? [];
  const familyTree = payload.familyTree
    ? {
        ...payload.familyTree,
        sourceReportedNetworks,
      }
    : sourceReportedNetworks.length
      ? {
          people: [], branches: [], families: [], unions: [], focusPath: [],
          title: "Zewnętrzne sieci kandydackie",
          subtitle: "Warstwy do weryfikacji, poza drzewem kanonicznym",
          sourceReportedNetworks,
        }
      : null;
  const tree = new FamilyTree(familyTree as never, {
    dataIndex,
    onInspect: (inspection: TreeInspectorModel | null) => inspectTree(inspection),
  });
  document.addEventListener("genea:private-tree-overlay", (event) => {
    if (event instanceof CustomEvent) tree.setPrivateOverlay(event.detail);
  });
  const map = new LineageMapController((payload.map || null) as never, {
    onInspect: (inspection: MapInspection | null) => inspectMap(inspection),
  });
  const graph = new ResearchGraphController($("#graph-canvas"), $("#graph-wrap"), {
    payload: legacyPayload,
    dataIndex,
    onInspect: (graphNode: unknown) =>
      inspectGraph(buildGraphInspectorModel(graphNode, {
        payload: legacyPayload,
        dataIndex,
      }) as GraphInspectorModel),
  });
  document.addEventListener("genea:graph-toggle-identity", (event) => {
    if (event instanceof CustomEvent && typeof event.detail?.personId === "string") {
      graph.toggleIdentity(event.detail.personId);
    }
  });
  document.addEventListener("genea:graph-toggle-evidence", (event) => {
    if (event instanceof CustomEvent && typeof event.detail?.personId === "string") {
      graph.toggleEvidence(event.detail.personId);
    }
  });
  document.addEventListener("genea:graph-toggle-evidence-event", (event) => {
    if (event instanceof CustomEvent && typeof event.detail?.eventId === "string") {
      graph.toggleEvidenceEvent(event.detail.eventId);
    }
  });
  document.addEventListener("genea:graph-toggle-cluster", (event) => {
    if (event instanceof CustomEvent && typeof event.detail?.clusterId === "string") {
      graph.toggleCluster(event.detail.clusterId);
    }
  });
  controllers.tree = tree;
  controllers.map = map;
  controllers.graph = graph;
  graph.setMode(graph.initialMode());
  bindPrimaryControls({ tree, graph });
  $("#loading-view").classList.add("is-hidden");

  function activate(view: ViewName): void {
    document.querySelectorAll<HTMLElement>(".view").forEach((panel) => {
      panel.classList.toggle("is-active", panel.dataset.viewPanel === view);
    });
    if (view === "tree") requestAnimationFrame(() => tree.ensureVisible());
    if (view === "map") requestAnimationFrame(() => { map.resize(); map.render(); });
    if (view === "graph") requestAnimationFrame(() => graph.resize());
    if (view === "tree" || view === "graph") {
      const query = new URLSearchParams(window.location.search);
      const target = (view === "tree" ? tree : graph) as unknown as ExternalNetworkRuntimeController;
      if (query.has("network")) {
        const link = externalNetworkDeepLinkFromSearch(window.location.search)
          || { networkId: "", candidateRole: "all" };
        requestAnimationFrame(() => target.applyExternalNetworkDeepLink?.(link));
      } else {
        const personId = view === "tree" ? treePersonDeepLinkFromSearch(window.location.search) : null;
        requestAnimationFrame(() => {
          target.clearExternalNetworkDeepLink?.();
          if (personId) tree.applyBasePersonDeepLink(personId);
        });
      }
    }
  }

  function focusPerson(view: ViewName, context: PresentedPersonContext): boolean {
    const query = new URLSearchParams(window.location.search);
    if (((view === "tree" || view === "graph") && query.has("network"))
      || (view === "tree" && query.has("person"))) {
      return false;
    }
    if (view === "tree") {
      const personId = context.treePersonIds[0];
      if (!personId) return false;
      requestAnimationFrame(() => tree.focusPerson(personId));
      return true;
    }
    if (view === "graph") {
      requestAnimationFrame(() => graph.focusPersonContext(context));
      return Boolean(context.graphIdentityIds.length || context.occurrenceIds.length);
    }
    if (view === "map") {
      requestAnimationFrame(() => map.focusPersonContext(context));
      return Boolean(context.mapPersonIds.length);
    }
    return false;
  }

  return { activate, focusPerson };
}
