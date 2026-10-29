<script setup lang="ts">
import { computed, watch, watchEffect } from "vue";
import { useRoute, useRouter } from "vue-router";

import { ROUTE_NAMES, type EntityKind } from "../router";
import { EntityIndex, labelForKind, type EntityReference } from "../routing/entities";
import { useAppStore } from "../stores/app";
import EntityDetailView from "./EntityDetailView.vue";
import CatalogView from "./CatalogView.vue";
import ResearchWorkspaceView from "./ResearchWorkspaceView.vue";
import ArchiveRequestsView from "./ArchiveRequestsView.vue";
import RunComparisonView from "./RunComparisonView.vue";
import VolumePageView from "./VolumePageView.vue";
import SourceIdentityDetailView from "./SourceIdentityDetailView.vue";
import SourceOccurrenceDetailView from "./SourceOccurrenceDetailView.vue";
import NotFoundView from "../components/NotFoundView.vue";
import PrivateRouteGate from "../components/PrivateRouteGate.vue";


const route = useRoute();
const router = useRouter();
const store = useAppStore();
const index = computed(() => new EntityIndex(store.payload!));
const entityKind = computed(() => typeof route.meta.entityKind === "string" ? route.meta.entityKind as EntityKind : null);
const entityId = computed(() => {
  const values = Object.values(route.params);
  const value = values.find((item) => typeof item === "string");
  return typeof value === "string" ? value : "";
});
const entity = computed<EntityReference | null>(() => {
  if (!entityKind.value || !entityId.value) return null;
  const publicEntity = entityKind.value === "person"
    ? index.value.resolvePersonRoute(entityId.value)
    : index.value.resolve(entityKind.value, entityId.value);
  if (publicEntity.availability !== "missing") return publicEntity;
  if (entityKind.value === "person" && store.curatorAccess.authenticated) {
    const privatePerson = store.privatePeople.find((item) => item.id === entityId.value);
    if (privatePerson) return {
      kind: "person",
      id: privatePerson.id,
      label: privatePerson.preferredName,
      status: privatePerson.classification,
      availability: "found",
      raw: { id: privatePerson.id, preferredName: privatePerson.preferredName, status: privatePerson.classification, private: true },
    };
  }
  return publicEntity;
});

watch(entity, (next) => {
  if (next?.kind !== "person" || next.availability !== "found" || next.raw?.private) return;
  store.activatePerson(String(next.raw?.personKey || next.id));
  if (entityId.value !== next.id) {
    void router.replace({ name: ROUTE_NAMES.person, params: { personId: next.id } });
  }
}, { immediate: true });
const isResearchList = computed(() => [
  ROUTE_NAMES.researchDashboard,
  ROUTE_NAMES.researchCases,
  ROUTE_NAMES.researchQuestions,
  ROUTE_NAMES.researchVolumes,
  ROUTE_NAMES.researchEvidence,
].includes(route.name as never));
const isCatalog = computed(() => Boolean(route.meta.catalog || route.meta.catalogCollection));
const isLockedCandidate = computed(() => Boolean(
  entity.value?.availability === "missing"
  && store.curatorAccess.available
  && !store.curatorAccess.authenticated,
));
const routedTitle = computed(() => ({
  [ROUTE_NAMES.researchDashboard]: "Pulpit badań",
  [ROUTE_NAMES.researchCases]: "Sprawy badawcze",
  [ROUTE_NAMES.researchQuestions]: "Pytania i backlog",
  [ROUTE_NAMES.researchVolumes]: "Woluminy i przebiegi",
  [ROUTE_NAMES.researchEvidence]: "Twierdzenia i konflikty",
  [ROUTE_NAMES.researchRequests]: "Kwerendy do instytucji",
  [ROUTE_NAMES.researchVolumePage]: "Strona woluminu",
  [ROUTE_NAMES.researchRunCompare]: "Porównanie przebiegów",
  [ROUTE_NAMES.catalog]: "Katalog",
  [ROUTE_NAMES.peopleCatalog]: "Osoby",
  [ROUTE_NAMES.sourceIdentity]: "Powiązane wystąpienia źródłowe",
  [ROUTE_NAMES.sourceOccurrence]: "Wystąpienie źródłowe",
}[String(route.name)] || "lokalne archiwum rodzinne"));

watchEffect(() => {
  if (isLockedCandidate.value) {
    document.title = "Genea — obiekt chroniony";
  } else if (entity.value?.availability === "found") {
    document.title = `Genea — ${entity.value.label}`;
  } else if (isCatalog.value || isResearchList.value || route.name === ROUTE_NAMES.researchRequests || route.name === ROUTE_NAMES.researchVolumePage || route.name === ROUTE_NAMES.researchRunCompare || route.name === ROUTE_NAMES.sourceIdentity || route.name === ROUTE_NAMES.sourceOccurrence) {
    document.title = `Genea — ${routedTitle.value}`;
  } else {
    document.title = "Genea — lokalne archiwum rodzinne";
  }
});
</script>

<template>
  <CatalogView v-if="isCatalog" />
  <ArchiveRequestsView v-else-if="route.name === ROUTE_NAMES.researchRequests" />
  <ResearchWorkspaceView v-else-if="isResearchList" />
  <VolumePageView v-else-if="route.name === ROUTE_NAMES.researchVolumePage" />
  <RunComparisonView v-else-if="route.name === ROUTE_NAMES.researchRunCompare" />
  <SourceIdentityDetailView v-else-if="route.name === ROUTE_NAMES.sourceIdentity" :group-id="entityId" />
  <SourceOccurrenceDetailView v-else-if="route.name === ROUTE_NAMES.sourceOccurrence" :occurrence-id="entityId" />
  <PrivateRouteGate v-else-if="isLockedCandidate" :kind-label="entityKind ? labelForKind(entityKind) : 'Obiekt'" />
  <EntityDetailView v-else-if="entity?.availability === 'found'" :entity="entity" />
  <NotFoundView v-else-if="entity?.availability === 'not_exported'" reason="not_exported" />
  <NotFoundView v-else :reason="route.name === ROUTE_NAMES.notFound ? 'route' : 'missing'" />
</template>
