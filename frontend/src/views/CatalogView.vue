<script setup lang="ts">
import { computed, ref, watch, watchEffect } from "vue";
import { useRoute } from "vue-router";

import EntityBreadcrumbs from "../components/EntityBreadcrumbs.vue";
import EntityLink from "../components/EntityLink.vue";
import RecentEntities from "../components/RecentEntities.vue";
import UiIcon from "../components/UiIcon.vue";
import WorkspaceHeader from "../components/WorkspaceHeader.vue";
import { CATALOG_DEFINITIONS, catalogDefinition, catalogLocation, isCatalogKind, type CatalogKind } from "../navigation/catalog";
import { ROUTE_NAMES } from "../router";
import { EntityIndex, type EntityReference } from "../routing/entities";
import { useAppStore } from "../stores/app";


const route = useRoute();
const store = useAppStore();
const query = ref("");
const index = computed(() => new EntityIndex(store.payload!));
const selectedKind = computed<CatalogKind | null>(() => {
  if (isCatalogKind(route.meta.catalogKind)) return route.meta.catalogKind;
  return isCatalogKind(route.query.kind) ? route.query.kind : null;
});
const inGlobalCatalog = computed(() => Boolean(route.meta.catalog));
const selectedDefinition = computed(() => selectedKind.value ? catalogDefinition(selectedKind.value) : null);
const workspaceLabel = computed(() => {
  if (inGlobalCatalog.value) return "Katalog";
  const kind = selectedDefinition.value?.kind;
  if (kind === "person" || kind === "line") return "Rodzina";
  if (kind === "place") return "Mapa";
  if (kind === "event" || kind === "source") return "Źródła";
  return "Sieć FAN";
});
const headerEyebrow = computed(() => inGlobalCatalog.value ? "Katalog rodzinny" : `${workspaceLabel.value} · katalog`);

const privatePeople = computed<EntityReference[]>(() => {
  if (!store.curatorAccess.authenticated) return [];
  const publicIds = new Set(index.value.all("person").map((item) => item.id));
  return store.privatePeople
    .filter((person) => !publicIds.has(person.id))
    .map((person) => ({
      kind: "person",
      id: person.id,
      label: person.preferredName,
      status: "Dane prywatne · dostęp sesyjny",
      availability: "found",
      raw: { private: true },
    }));
});

const items = computed(() => {
  if (!selectedKind.value) return [];
  const normalized = query.value.trim().toLocaleLowerCase("pl-PL");
  const values = [...index.value.all(selectedKind.value), ...privatePeople.value]
    .filter((item) => item.availability === "found")
    .sort((first, second) => first.label.localeCompare(second.label, "pl-PL"));
  if (!normalized) return values;
  return values.filter((item) => `${item.label} ${item.status || ""} ${item.id}`.toLocaleLowerCase("pl-PL").includes(normalized));
});

function countFor(kind: CatalogKind): number {
  const base = index.value.all(kind).filter((item) => item.availability === "found").length;
  return kind === "person" && store.curatorAccess.authenticated ? base + privatePeople.value.length : base;
}

watch(selectedKind, () => {
  query.value = "";
});

watchEffect(() => {
  document.title = selectedDefinition.value
    ? `Genea — ${selectedDefinition.value.label}`
    : "Genea — Katalog";
});
</script>

<template>
  <section class="catalog-view">
    <EntityBreadcrumbs :workspace="workspaceLabel" :label="selectedDefinition?.label" />
    <WorkspaceHeader
      :eyebrow="headerEyebrow"
      :title="selectedDefinition?.label || 'Znajdź obiekt bez szukania go na grafie'"
      :description="selectedDefinition?.description || 'Osoby, miejsca, zdarzenia i materiały mają własne, linkowalne katalogi. Badania pozostają osobnym warsztatem.'"
    />

    <nav v-if="inGlobalCatalog" class="catalog-categories" aria-label="Kategorie katalogu">
      <RouterLink
        v-for="definition in CATALOG_DEFINITIONS"
        :key="definition.kind"
        :to="catalogLocation(definition.kind)"
        :class="{ 'is-active': selectedKind === definition.kind }"
        :aria-current="selectedKind === definition.kind ? 'page' : undefined"
      >
        <span class="catalog-category-icon"><UiIcon :name="definition.icon" /></span>
        <span><strong>{{ definition.label }}</strong><small>{{ definition.description }}</small></span>
        <b>{{ countFor(definition.kind) }}</b>
      </RouterLink>
    </nav>

    <template v-if="selectedDefinition">
      <div class="catalog-list-toolbar">
        <label class="search-field">
          <UiIcon name="search" />
          <span class="sr-only">Szukaj w kategorii {{ selectedDefinition.label }}</span>
          <input v-model="query" type="search" autocomplete="off" :placeholder="`Szukaj: ${selectedDefinition.label.toLocaleLowerCase('pl-PL')}…`" />
        </label>
        <span>{{ items.length }} {{ items.length === 1 ? selectedDefinition.singular.toLocaleLowerCase('pl-PL') : 'obiektów' }}</span>
      </div>
      <div class="catalog-entity-grid">
        <EntityLink v-for="item in items" :key="`${item.kind}:${item.id}`" :kind="item.kind" :id="item.id" :label="item.label" :status="item.status" />
      </div>
      <p v-if="!items.length" class="empty-state-card">Brak obiektów pasujących do filtra w bieżącym, dopuszczonym eksporcie.</p>
    </template>
    <RecentEntities v-else />
  </section>
</template>
