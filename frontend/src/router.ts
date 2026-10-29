import {
  createMemoryHistory,
  createRouter,
  createWebHistory,
  type RouteLocationNormalizedLoaded,
  type RouteLocationRaw,
  type RouteRecordRaw,
  type Router,
  type RouterHistory,
} from "vue-router";

import {
  SOURCE_REPORTED_CANDIDATE_ROLES,
  isViewName,
  type SourceReportedCandidateRole,
  type ViewName,
} from "./types/presentation";


export const ROUTE_NAMES = {
  catalog: "catalog",
  peopleCatalog: "people-catalog",
  linesCatalog: "lines-catalog",
  placesCatalog: "places-catalog",
  eventsCatalog: "events-catalog",
  householdsCatalog: "households-catalog",
  clustersCatalog: "clusters-catalog",
  tree: "tree",
  map: "map",
  graph: "graph",
  sources: "sources",
  researchDashboard: "research-dashboard",
  person: "person-detail",
  line: "line-detail",
  place: "place-detail",
  event: "event-detail",
  source: "source-detail",
  sourceIdentity: "source-identity-detail",
  sourceOccurrence: "source-occurrence-detail",
  household: "household-detail",
  cluster: "cluster-detail",
  researchCases: "research-cases",
  researchCase: "research-case-detail",
  researchQuestions: "research-questions",
  researchQuestion: "research-question-detail",
  researchVolumes: "research-volumes",
  researchVolume: "research-volume-detail",
  researchVolumePage: "research-volume-page",
  researchRun: "research-run-detail",
  researchRunCompare: "research-run-compare",
  researchEvidence: "research-evidence",
  researchRequests: "research-requests",
  notFound: "not-found",
} as const;

export type RouteName = (typeof ROUTE_NAMES)[keyof typeof ROUTE_NAMES];
export interface ExternalNetworkDeepLink {
  networkId: string;
  publicProfileId?: string;
  candidateRole: SourceReportedCandidateRole;
}

export interface ExternalCandidateProfileOrigin extends ExternalNetworkDeepLink {
  view: "tree" | "graph";
  publicProfileId: string;
}
export type EntityKind =
  | "person"
  | "line"
  | "place"
  | "event"
  | "source"
  | "household"
  | "cluster"
  | "case"
  | "question"
  | "volume"
  | "run";

const ENTITY_ROUTE: Record<EntityKind, { name: RouteName; param: string }> = {
  person: { name: ROUTE_NAMES.person, param: "personId" },
  line: { name: ROUTE_NAMES.line, param: "lineId" },
  place: { name: ROUTE_NAMES.place, param: "placeId" },
  event: { name: ROUTE_NAMES.event, param: "eventId" },
  source: { name: ROUTE_NAMES.source, param: "sourceId" },
  household: { name: ROUTE_NAMES.household, param: "householdId" },
  cluster: { name: ROUTE_NAMES.cluster, param: "clusterId" },
  case: { name: ROUTE_NAMES.researchCase, param: "caseId" },
  question: { name: ROUTE_NAMES.researchQuestion, param: "questionId" },
  volume: { name: ROUTE_NAMES.researchVolume, param: "volumeId" },
  run: { name: ROUTE_NAMES.researchRun, param: "runId" },
};

const EMPTY_COMPONENT = { render: () => null };

function record(
  path: string,
  name: RouteName,
  primaryView: ViewName,
  extraMeta: Record<string, unknown> = {},
): RouteRecordRaw {
  return {
    path,
    name,
    component: EMPTY_COMPONENT,
    meta: { primaryView, ...extraMeta },
  };
}

export const routes: RouteRecordRaw[] = [
  record("/catalog", ROUTE_NAMES.catalog, "tree", { workspace: true, catalog: true }),
  record("/people", ROUTE_NAMES.peopleCatalog, "tree", { workspace: true, catalogCollection: true, catalogKind: "person" }),
  record("/lines", ROUTE_NAMES.linesCatalog, "tree", { workspace: true, catalogCollection: true, catalogKind: "line" }),
  record("/places", ROUTE_NAMES.placesCatalog, "map", { workspace: true, catalogCollection: true, catalogKind: "place" }),
  record("/events", ROUTE_NAMES.eventsCatalog, "sources", { workspace: true, catalogCollection: true, catalogKind: "event" }),
  record("/households", ROUTE_NAMES.householdsCatalog, "graph", { workspace: true, catalogCollection: true, catalogKind: "household" }),
  record("/clusters", ROUTE_NAMES.clustersCatalog, "graph", { workspace: true, catalogCollection: true, catalogKind: "cluster" }),
  record("/tree", ROUTE_NAMES.tree, "tree"),
  record("/map", ROUTE_NAMES.map, "map"),
  record("/graph", ROUTE_NAMES.graph, "graph"),
  record("/sources", ROUTE_NAMES.sources, "sources"),
  { path: "/research", redirect: { name: ROUTE_NAMES.researchDashboard } },
  record("/research/dashboard", ROUTE_NAMES.researchDashboard, "research", { workspace: true }),
  record("/people/:personId", ROUTE_NAMES.person, "tree", { entityKind: "person", catalogContext: true }),
  record("/lines/:lineId", ROUTE_NAMES.line, "tree", { entityKind: "line", catalogContext: true }),
  record("/places/:placeId", ROUTE_NAMES.place, "map", { entityKind: "place", catalogContext: true }),
  record("/events/:eventId", ROUTE_NAMES.event, "sources", { entityKind: "event", catalogContext: true }),
  record("/sources/:sourceId", ROUTE_NAMES.source, "sources", { entityKind: "source", catalogContext: true }),
  record("/source-identities/:sourceIdentityId", ROUTE_NAMES.sourceIdentity, "sources", { workspace: true }),
  record("/source-occurrences/:occurrenceId", ROUTE_NAMES.sourceOccurrence, "sources", { workspace: true }),
  record("/households/:householdId", ROUTE_NAMES.household, "graph", { entityKind: "household", catalogContext: true }),
  record("/clusters/:clusterId", ROUTE_NAMES.cluster, "graph", { entityKind: "cluster", catalogContext: true }),
  record("/research/cases", ROUTE_NAMES.researchCases, "research", { workspace: true }),
  record("/research/cases/:caseId", ROUTE_NAMES.researchCase, "research", { entityKind: "case", workspace: true }),
  record("/research/questions", ROUTE_NAMES.researchQuestions, "research", { workspace: true }),
  record("/research/questions/:questionId", ROUTE_NAMES.researchQuestion, "research", { entityKind: "question", workspace: true }),
  record("/research/volumes", ROUTE_NAMES.researchVolumes, "research", { workspace: true }),
  record("/research/volumes/:volumeId", ROUTE_NAMES.researchVolume, "research", { entityKind: "volume", workspace: true }),
  record("/research/volumes/:volumeId/pages/:ordinal", ROUTE_NAMES.researchVolumePage, "research", { workspace: true }),
  record("/research/runs/:runId", ROUTE_NAMES.researchRun, "research", { entityKind: "run", workspace: true }),
  record("/research/runs/:runId/compare/:otherRunId", ROUTE_NAMES.researchRunCompare, "research", { workspace: true }),
  record("/research/evidence", ROUTE_NAMES.researchEvidence, "research", { workspace: true }),
  record("/research/requests", ROUTE_NAMES.researchRequests, "research", { workspace: true }),
  { path: "/", redirect: { name: ROUTE_NAMES.tree } },
  record("/:pathMatch(.*)*", ROUTE_NAMES.notFound, "tree", { notFound: true }),
];

export function viewFromHash(hash = window.location.hash): ViewName {
  const value = hash.replace(/^#\/?/, "");
  return isViewName(value) ? value : "tree";
}

export function legacyPathFromHash(hash: string): string | null {
  const value = hash.replace(/^#\/?/, "");
  if (!isViewName(value)) return null;
  return pathForView(value);
}

export function migrateLegacyHash(
  locationLike: Pick<Location, "hash" | "pathname" | "search"> = window.location,
  historyLike: Pick<History, "replaceState" | "state"> = window.history,
): string | null {
  const path = legacyPathFromHash(locationLike.hash);
  if (!path) return null;
  historyLike.replaceState(historyLike.state, "", `${path}${locationLike.search}`);
  return path;
}

export function viewFromRoute(route: Pick<RouteLocationNormalizedLoaded, "meta">): ViewName {
  const value = route.meta.primaryView;
  return typeof value === "string" && isViewName(value) ? value : "tree";
}

export function viewFromRouteName(name: unknown): ViewName {
  if (typeof name !== "string") return "tree";
  if (isViewName(name)) return name;
  const route = routes.find((item) => item.name === name);
  const value = route && "meta" in route ? route.meta?.primaryView : null;
  return typeof value === "string" && isViewName(value) ? value : "tree";
}

export function pathForView(view: ViewName): string {
  return view === "research" ? "/research/dashboard" : `/${view}`;
}

function scalarQueryValue(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 && value.length <= 200 ? value : null;
}

export function treePersonDeepLinkFromSearch(search: string): string | null {
  const params = new URLSearchParams(search);
  if (params.has("network") || params.getAll("person").length !== 1) return null;
  return scalarQueryValue(params.get("person"));
}

export function externalNetworkDeepLinkFromQuery(
  query: Record<string, unknown>,
): ExternalNetworkDeepLink | null {
  const networkId = scalarQueryValue(query.network);
  if (!networkId) return null;
  const publicProfileId = scalarQueryValue(query.person);
  const requestedRole = scalarQueryValue(query.candidateRole) || "all";
  if (!(SOURCE_REPORTED_CANDIDATE_ROLES as readonly string[]).includes(requestedRole)) return null;
  return {
    networkId,
    ...(publicProfileId ? { publicProfileId } : {}),
    candidateRole: requestedRole as SourceReportedCandidateRole,
  };
}

export function externalNetworkDeepLinkFromSearch(search: string): ExternalNetworkDeepLink | null {
  const params = new URLSearchParams(search);
  return externalNetworkDeepLinkFromQuery({
    network: params.get("network"),
    person: params.get("person"),
    candidateRole: params.get("candidateRole"),
  });
}

export function externalNetworkLocation(
  view: "tree" | "graph",
  link: ExternalNetworkDeepLink,
): RouteLocationRaw {
  return {
    name: view === "tree" ? ROUTE_NAMES.tree : ROUTE_NAMES.graph,
    query: {
      network: link.networkId,
      ...(link.publicProfileId ? { person: link.publicProfileId } : {}),
      ...(link.candidateRole !== "all" ? { candidateRole: link.candidateRole } : {}),
    },
  };
}

export function externalCandidateProfileLocation(
  publicProfileId: string,
  origin: Omit<ExternalCandidateProfileOrigin, "publicProfileId">,
): RouteLocationRaw {
  return {
    name: ROUTE_NAMES.person,
    params: { personId: publicProfileId },
    query: {
      returnView: origin.view,
      network: origin.networkId,
      person: publicProfileId,
      ...(origin.candidateRole !== "all" ? { candidateRole: origin.candidateRole } : {}),
    },
  };
}

export function externalNetworkReturnLocation(
  query: Record<string, unknown>,
): RouteLocationRaw | null {
  const link = externalNetworkDeepLinkFromQuery(query);
  const returnView = scalarQueryValue(query.returnView);
  if (!link || !link.publicProfileId || (returnView !== "tree" && returnView !== "graph")) return null;
  return externalNetworkLocation(returnView, link);
}

export function entityLocation(kind: EntityKind, id: string): RouteLocationRaw {
  const definition = ENTITY_ROUTE[kind];
  return { name: definition.name, params: { [definition.param]: id } };
}

export function entityPath(kind: EntityKind, id: string): string {
  const encoded = encodeURIComponent(id);
  const paths: Record<EntityKind, string> = {
    person: `/people/${encoded}`,
    line: `/lines/${encoded}`,
    place: `/places/${encoded}`,
    event: `/events/${encoded}`,
    source: `/sources/${encoded}`,
    household: `/households/${encoded}`,
    cluster: `/clusters/${encoded}`,
    case: `/research/cases/${encoded}`,
    question: `/research/questions/${encoded}`,
    volume: `/research/volumes/${encoded}`,
    run: `/research/runs/${encoded}`,
  };
  return paths[kind];
}

export function createGeneaRouter(history?: RouterHistory): Router {
  return createRouter({
    history: history || createWebHistory(import.meta.env.BASE_URL),
    routes,
    scrollBehavior(_to, _from, savedPosition) {
      return savedPosition || { left: 0, top: 0 };
    },
  });
}

export function createTestRouter(): Router {
  return createGeneaRouter(createMemoryHistory());
}
