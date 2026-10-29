import type { RouteLocationRaw } from "vue-router";

import { ROUTE_NAMES, type EntityKind } from "../router";


export type CatalogKind = Exclude<EntityKind, "case" | "question" | "volume" | "run">;

export interface CatalogDefinition {
  kind: CatalogKind;
  label: string;
  singular: string;
  description: string;
  icon: string;
}

export const CATALOG_DEFINITIONS: readonly CatalogDefinition[] = [
  { kind: "person", label: "Osoby", singular: "Osoba", description: "Profile, warianty nazw i status tożsamości", icon: "person" },
  { kind: "line", label: "Linie rodzinne", singular: "Linia rodzinna", description: "Rdzenie gałęzi i ich zakres badawczy", icon: "line" },
  { kind: "place", label: "Miejsca", singular: "Miejsce", description: "Miejscowości, parafie i obserwacje w czasie", icon: "place" },
  { kind: "event", label: "Zdarzenia", singular: "Zdarzenie", description: "Urodzenia, małżeństwa, zgony i inne zdarzenia", icon: "event" },
  { kind: "source", label: "Akta i materiały", singular: "Akt lub materiał", description: "Źródła, skany, odczyty i transkrypcje", icon: "source" },
  { kind: "household", label: "Gospodarstwa", singular: "Gospodarstwo", description: "Domy źródłowe i rodziny oczekujące na potwierdzenie adresu", icon: "household" },
  { kind: "cluster", label: "Klastry", singular: "Klaster", description: "Agregaty prezentacyjne sieci badawczej", icon: "cluster" },
] as const;

const CATALOG_KINDS = new Set(CATALOG_DEFINITIONS.map((item) => item.kind));

export function isCatalogKind(value: unknown): value is CatalogKind {
  return typeof value === "string" && CATALOG_KINDS.has(value as CatalogKind);
}

export function catalogDefinition(kind: CatalogKind): CatalogDefinition {
  return CATALOG_DEFINITIONS.find((item) => item.kind === kind)!;
}

export function catalogLocation(kind?: CatalogKind | null): RouteLocationRaw {
  return kind
    ? { name: ROUTE_NAMES.catalog, query: { kind } }
    : { name: ROUTE_NAMES.catalog };
}

export function collectionLocation(kind: EntityKind): RouteLocationRaw {
  const workspaceCollections: Partial<Record<CatalogKind, RouteLocationRaw>> = {
    person: { name: ROUTE_NAMES.peopleCatalog },
    line: { name: ROUTE_NAMES.linesCatalog },
    place: { name: ROUTE_NAMES.placesCatalog },
    event: { name: ROUTE_NAMES.eventsCatalog },
    source: { name: ROUTE_NAMES.sources },
    household: { name: ROUTE_NAMES.householdsCatalog },
    cluster: { name: ROUTE_NAMES.clustersCatalog },
  };
  if (isCatalogKind(kind)) return workspaceCollections[kind]!;
  if (kind === "case") return { name: ROUTE_NAMES.researchCases };
  if (kind === "question") return { name: ROUTE_NAMES.researchQuestions };
  return { name: ROUTE_NAMES.researchVolumes };
}

export function collectionLabel(kind: EntityKind): string {
  if (isCatalogKind(kind)) return catalogDefinition(kind).label;
  return ({ case: "Sprawy badawcze", question: "Pytania", volume: "Woluminy", run: "Przebiegi" } as const)[kind];
}
