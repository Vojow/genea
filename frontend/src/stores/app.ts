import { defineStore } from "pinia";
import { ref, shallowRef } from "vue";

import type {
  GraphInspectorModel,
  MapInspection,
  PresentationPayload,
  TreeInspectorModel,
  ViewName,
  CuratorAccess,
  PresentedPersonContext,
  PersonIdentityScope,
} from "../types/presentation";
import type { EntityKind } from "../router";
import { PersonContextIndex } from "../routing/person-context";


export interface RecentEntity {
  kind: EntityKind;
  id: string;
  label: string;
  viewedAt: number;
  personKey?: string;
}

export interface PersonNavigationOrigin {
  view: ViewName;
  occurrenceId?: string;
  sourceId?: string;
}

const RECENT_KEY = "genea:recent-entities:v2";
const LEGACY_RECENT_KEY = "genea:recent-entities:v1";


export const useAppStore = defineStore("app", () => {
  const payload = shallowRef<PresentationPayload | null>(null);
  const personContextIndex = shallowRef<PersonContextIndex | null>(null);
  const activeView = ref<ViewName>("tree");
  const activePersonKey = ref<string | null>(null);
  const activePersonIdentityScope = ref<PersonIdentityScope | null>(null);
  const activePersonOrigin = ref<PersonNavigationOrigin | null>(null);
  const mapInspection = shallowRef<MapInspection | null>(null);
  const graphInspection = shallowRef<GraphInspectorModel | null>(null);
  const treeInspection = shallowRef<TreeInspectorModel | null>(null);
  const privatePeople = ref<Array<{ id: string; preferredName: string; classification: string }>>([]);
  const recentEntities = ref<RecentEntity[]>([]);
  const contextNotice = ref("");
  const curatorAccess = ref<CuratorAccess>({
    available: false,
    authenticated: false,
    strict: false,
    csrf: "",
  });

  function initialize(nextPayload: PresentationPayload, initialView: ViewName): void {
    payload.value = nextPayload;
    personContextIndex.value = new PersonContextIndex(nextPayload);
    activeView.value = initialView;
    try {
      sessionStorage.removeItem(LEGACY_RECENT_KEY);
      const stored = JSON.parse(sessionStorage.getItem(RECENT_KEY) || "[]");
      const contextIndex = personContextIndex.value;
      recentEntities.value = Array.isArray(stored) ? stored.flatMap((item): RecentEntity[] => {
        if (!item || typeof item !== "object") return [];
        const candidate = item as RecentEntity;
        if (candidate.kind !== "person") return [candidate];
        const context = contextIndex?.resolveAny(candidate.personKey || candidate.id);
        return context ? [{ ...candidate, id: context.publicProfileId, label: context.label, personKey: context.personKey }] : [];
      }).filter((item, index, values) => values.findIndex((candidate) => candidate.kind === item.kind && (candidate.personKey || candidate.id) === (item.personKey || item.id)) === index).slice(0, 12) : [];
    } catch {
      recentEntities.value = [];
    }
  }

  function personContext(id: string): PresentedPersonContext | null {
    return personContextIndex.value?.resolveAny(id) || null;
  }

  function activatePerson(
    idOrContext: string | PresentedPersonContext,
    origin: Partial<PersonNavigationOrigin> = {},
  ): PresentedPersonContext | null {
    const context = typeof idOrContext === "string" ? personContext(idOrContext) : idOrContext;
    if (!context) return null;
    activePersonKey.value = context.personKey;
    activePersonIdentityScope.value = context.identityScope;
    activePersonOrigin.value = {
      view: origin.view || activeView.value,
      ...(origin.occurrenceId ? { occurrenceId: origin.occurrenceId } : {}),
      ...(origin.sourceId ? { sourceId: origin.sourceId } : {}),
    };
    return context;
  }

  function clearActivePerson(): void {
    activePersonKey.value = null;
    activePersonIdentityScope.value = null;
    activePersonOrigin.value = null;
  }

  function activate(view: ViewName): void {
    activeView.value = view;
  }

  function inspectMap(inspection: MapInspection | null): void {
    mapInspection.value = inspection;
  }

  function inspectGraph(inspection: GraphInspectorModel | null): void {
    graphInspection.value = inspection;
  }

  function inspectTree(inspection: TreeInspectorModel | null): void {
    treeInspection.value = inspection;
  }

  function setCuratorAccess(access: Partial<CuratorAccess>): void {
    curatorAccess.value = { ...curatorAccess.value, ...access };
  }

  function setPrivatePeople(people: Array<{ id: string; preferredName: string; classification: string }>): void {
    privatePeople.value = people.map((person) => ({ ...person }));
  }

  function payloadVersion(): string {
    return `${payload.value?.meta.schemaVersion || 0}:${payload.value?.meta.generatedAt || "unknown"}`;
  }

  function captureNavigationContext(): void {
    const snapshot = {
      version: payloadVersion(),
      view: activeView.value,
      scrollX: window.scrollX,
      scrollY: window.scrollY,
      capturedAt: Date.now(),
      personKey: activePersonKey.value,
      identityScope: activePersonIdentityScope.value,
      origin: activePersonOrigin.value ? { ...activePersonOrigin.value } : null,
    };
    const state = { ...(history.state || {}), geneaContext: snapshot };
    history.replaceState(state, "", window.location.href);
  }

  function restoreNavigationContext(state: unknown): void {
    const context = state && typeof state === "object"
      ? (state as { geneaContext?: Record<string, unknown> }).geneaContext
      : null;
    if (!context) return;
    if (context.version !== payloadVersion()) {
      clearActivePerson();
      contextNotice.value = "Widok wrócił do bezpiecznego ustawienia domyślnego, ponieważ zmieniła się wersja danych.";
      return;
    }
    if (typeof context.personKey === "string") {
      const restored = personContext(context.personKey);
      if (restored) {
        activePersonKey.value = restored.personKey;
        activePersonIdentityScope.value = restored.identityScope;
        const origin = context.origin && typeof context.origin === "object" ? context.origin as Record<string, unknown> : null;
        activePersonOrigin.value = origin && typeof origin.view === "string"
          ? {
              view: origin.view as ViewName,
              ...(typeof origin.occurrenceId === "string" ? { occurrenceId: origin.occurrenceId } : {}),
              ...(typeof origin.sourceId === "string" ? { sourceId: origin.sourceId } : {}),
            }
          : null;
      }
    }
    const x = typeof context.scrollX === "number" ? context.scrollX : 0;
    const y = typeof context.scrollY === "number" ? context.scrollY : 0;
    requestAnimationFrame(() => window.scrollTo(x, y));
  }

  function recordRecent(entity: Omit<RecentEntity, "viewedAt">): void {
    let normalized = entity;
    if (entity.kind === "person") {
      const context = personContext(entity.personKey || entity.id);
      // Private overlay objects intentionally have no public person context and
      // must not persist names or identifiers in sessionStorage.
      if (!context) return;
      normalized = {
        ...entity,
        id: context.publicProfileId,
        label: context.label,
        personKey: context.personKey,
      };
    }
    const next = [
      { ...normalized, viewedAt: Date.now() },
      ...recentEntities.value.filter((item) => item.kind !== normalized.kind || (item.personKey || item.id) !== (normalized.personKey || normalized.id)),
    ].slice(0, 12);
    recentEntities.value = next;
    sessionStorage.setItem(RECENT_KEY, JSON.stringify(next));
  }

  return {
    payload,
    activeView,
    activePersonKey,
    activePersonIdentityScope,
    activePersonOrigin,
    mapInspection,
    graphInspection,
    treeInspection,
    privatePeople,
    recentEntities,
    contextNotice,
    curatorAccess,
    initialize,
    activate,
    personContext,
    activatePerson,
    clearActivePerson,
    inspectMap,
    inspectGraph,
    inspectTree,
    setCuratorAccess,
    setPrivatePeople,
    captureNavigationContext,
    restoreNavigationContext,
    recordRecent,
  };
});
