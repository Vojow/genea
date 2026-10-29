import type { RouteLocationNormalizedLoaded, RouteLocationRaw } from "vue-router";

import { ROUTE_NAMES, type EntityKind } from "../router";
import type { ViewName } from "../types/presentation";
import { CATALOG_DEFINITIONS, catalogLocation, collectionLocation, type CatalogKind } from "./catalog";


export interface PrimaryWorkspaceItem {
  view: ViewName;
  label: string;
  shortLabel: string;
  description: string;
  icon: string;
}

export interface ContextNavigationItem {
  id: string;
  label: string;
  description: string;
  icon: string;
  to: RouteLocationRaw;
  routeNames?: readonly string[];
  entityKinds?: readonly EntityKind[];
  catalogKind?: CatalogKind | null;
}

export interface ContextNavigationGroup {
  id: string;
  label: string;
  items: readonly ContextNavigationItem[];
}

export const PRIMARY_WORKSPACES: readonly PrimaryWorkspaceItem[] = [
  { view: "tree", label: "Rodzina", shortLabel: "Rodzina", description: "Drzewo, osoby i linie rodzinne", icon: "tree" },
  { view: "map", label: "Mapa", shortLabel: "Mapa", description: "Miejsca, gospodarstwa i przemieszczanie", icon: "map" },
  { view: "graph", label: "Sieć FAN", shortLabel: "FAN", description: "Osoby FAN, łączniki i klastry", icon: "network" },
  { view: "sources", label: "Źródła", shortLabel: "Źródła", description: "Akta, materiały i zdarzenia", icon: "document" },
  { view: "research", label: "Badania", shortLabel: "Badania", description: "Sprawy, pytania, woluminy i dowody", icon: "research" },
] as const;

const WORKSPACE_CONTEXTS: Record<ViewName, ContextNavigationGroup> = {
  tree: {
    id: "family",
    label: "Rodzina",
    items: [
      { id: "tree", label: "Drzewo", description: "Interaktywne drzewo rodzinne", icon: "tree", to: { name: ROUTE_NAMES.tree }, routeNames: [ROUTE_NAMES.tree] },
      { id: "people", label: "Osoby", description: "Katalog wszystkich osób", icon: "person", to: { name: ROUTE_NAMES.peopleCatalog }, routeNames: [ROUTE_NAMES.peopleCatalog], entityKinds: ["person"] },
      { id: "lines", label: "Linie", description: "Linie i gałęzie rodzinne", icon: "line", to: collectionLocation("line"), routeNames: [ROUTE_NAMES.linesCatalog], entityKinds: ["line"] },
    ],
  },
  map: {
    id: "map",
    label: "Mapa",
    items: [
      { id: "map", label: "Mapa rodzin", description: "Miejsca i przemieszczanie w czasie", icon: "map", to: { name: ROUTE_NAMES.map }, routeNames: [ROUTE_NAMES.map] },
      { id: "places", label: "Miejsca", description: "Katalog miejscowości i parafii", icon: "place", to: collectionLocation("place"), routeNames: [ROUTE_NAMES.placesCatalog], entityKinds: ["place"] },
      { id: "households", label: "Gospodarstwa", description: "Domy źródłowe i rekonstrukcje rodzin", icon: "household", to: collectionLocation("household"), routeNames: [ROUTE_NAMES.householdsCatalog], entityKinds: ["household"] },
    ],
  },
  graph: {
    id: "graph",
    label: "Sieć FAN",
    items: [
      { id: "graph", label: "Graf FAN", description: "Relacje osób, świadków i gospodarstw", icon: "network", to: { name: ROUTE_NAMES.graph }, routeNames: [ROUTE_NAMES.graph] },
      { id: "people", label: "Osoby", description: "Katalog osób kanonicznych i kandydatur", icon: "person", to: { name: ROUTE_NAMES.peopleCatalog }, routeNames: [ROUTE_NAMES.peopleCatalog], entityKinds: ["person"] },
      { id: "households", label: "Gospodarstwa", description: "Domy źródłowe i rekonstrukcje rodzin", icon: "household", to: collectionLocation("household"), routeNames: [ROUTE_NAMES.householdsCatalog], entityKinds: ["household"] },
      { id: "clusters", label: "Klastry", description: "Klastry i niepowiązane skupiska", icon: "cluster", to: collectionLocation("cluster"), routeNames: [ROUTE_NAMES.clustersCatalog], entityKinds: ["cluster"] },
    ],
  },
  sources: {
    id: "sources",
    label: "Źródła",
    items: [
      { id: "sources", label: "Akta i materiały", description: "Przegląd źródeł, skanów i transkrypcji", icon: "source", to: { name: ROUTE_NAMES.sources }, routeNames: [ROUTE_NAMES.sources], entityKinds: ["source"] },
      { id: "events", label: "Zdarzenia", description: "Urodzenia, małżeństwa, zgony i inne zdarzenia", icon: "event", to: collectionLocation("event"), routeNames: [ROUTE_NAMES.eventsCatalog], entityKinds: ["event"] },
    ],
  },
  research: {
    id: "research",
    label: "Badania",
    items: [
      { id: "dashboard", label: "Pulpit", description: "Priorytety i stan warsztatu", icon: "research", to: { name: ROUTE_NAMES.researchDashboard }, routeNames: [ROUTE_NAMES.researchDashboard] },
      { id: "cases", label: "Sprawy", description: "Zakresy badawcze i następne kroki", icon: "case", to: { name: ROUTE_NAMES.researchCases }, routeNames: [ROUTE_NAMES.researchCases, ROUTE_NAMES.researchCase], entityKinds: ["case"] },
      { id: "questions", label: "Pytania", description: "Kuratorowany backlog pytań", icon: "question", to: { name: ROUTE_NAMES.researchQuestions }, routeNames: [ROUTE_NAMES.researchQuestions, ROUTE_NAMES.researchQuestion], entityKinds: ["question"] },
      { id: "requests", label: "Kwerendy do instytucji", description: "Projekty pism, zamówienia kopii i odpowiedzi", icon: "document", to: { name: ROUTE_NAMES.researchRequests }, routeNames: [ROUTE_NAMES.researchRequests] },
      { id: "volumes", label: "Woluminy i AI", description: "Indeksy, przebiegi i porównania", icon: "volume", to: { name: ROUTE_NAMES.researchVolumes }, routeNames: [ROUTE_NAMES.researchVolumes, ROUTE_NAMES.researchVolume, ROUTE_NAMES.researchVolumePage, ROUTE_NAMES.researchRun, ROUTE_NAMES.researchRunCompare], entityKinds: ["volume", "run"] },
      { id: "evidence", label: "Dowody", description: "Twierdzenia, źródła i konflikty", icon: "document", to: { name: ROUTE_NAMES.researchEvidence }, routeNames: [ROUTE_NAMES.researchEvidence] },
    ],
  },
};

const CATALOG_CONTEXT: ContextNavigationGroup = {
  id: "catalog",
  label: "Katalog",
  items: [
    {
      id: "all",
      label: "Wszystko",
      description: "Wszystkie typy obiektów",
      icon: "search",
      to: catalogLocation(),
      routeNames: [ROUTE_NAMES.catalog],
      catalogKind: null,
    },
    ...CATALOG_DEFINITIONS.map((definition) => ({
      id: definition.kind,
      label: definition.kind === "line" ? "Linie" : definition.kind === "source" ? "Akta" : definition.label,
      description: definition.description,
      icon: definition.icon,
      to: catalogLocation(definition.kind),
      routeNames: definition.kind === "person" ? [ROUTE_NAMES.peopleCatalog] : undefined,
      entityKinds: [definition.kind],
      catalogKind: definition.kind,
    })),
  ],
};

export function contextNavigationFor(route: RouteLocationNormalizedLoaded, activeView: ViewName): ContextNavigationGroup {
  return route.meta.catalog ? CATALOG_CONTEXT : WORKSPACE_CONTEXTS[activeView];
}

export function workspaceLabelForEntity(kind: EntityKind): string {
  if (kind === "person" || kind === "line") return "Rodzina";
  if (kind === "place") return "Mapa";
  if (kind === "household" || kind === "cluster") return "Sieć FAN";
  if (kind === "event" || kind === "source") return "Źródła";
  return "Badania";
}

export function contextNavigationItemIsActive(item: ContextNavigationItem, route: RouteLocationNormalizedLoaded): boolean {
  const routeName = typeof route.name === "string" ? route.name : "";
  const entityKind = typeof route.meta.entityKind === "string" ? route.meta.entityKind as EntityKind : null;
  if (entityKind && item.entityKinds?.includes(entityKind)) return true;
  if (routeName === ROUTE_NAMES.catalog && Object.prototype.hasOwnProperty.call(item, "catalogKind")) {
    const selectedKind = typeof route.query.kind === "string" ? route.query.kind : null;
    return selectedKind === item.catalogKind;
  }
  return Boolean(item.routeNames?.includes(routeName));
}
