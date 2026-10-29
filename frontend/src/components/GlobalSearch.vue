<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute } from "vue-router";

import EntityLink from "./EntityLink.vue";
import { EntityIndex } from "../routing/entities";
import { useAppStore } from "../stores/app";
import UiIcon from "./UiIcon.vue";
import { ROUTE_NAMES } from "../router";


const store = useAppStore();
const route = useRoute();
const query = ref("");
const open = ref(false);
const trigger = ref<HTMLButtonElement | null>(null);
const searchInput = ref<HTMLInputElement | null>(null);
const index = computed(() => store.payload ? new EntityIndex(store.payload) : null);
const results = computed(() => {
  const publicResults = index.value?.search(query.value, 12) || [];
  if (!store.curatorAccess.authenticated || !query.value.trim()) return publicResults;
  const normalized = query.value.trim().toLocaleLowerCase("pl-PL");
  const privateResults = store.privatePeople
    .filter((person) => `${person.preferredName} ${person.id}`.toLocaleLowerCase("pl-PL").includes(normalized))
    .map((person) => ({
      kind: "person" as const,
      id: person.id,
      label: person.preferredName,
      status: person.classification,
      availability: "found" as const,
      detail: "Dane prywatne · dostęp sesyjny",
      searchText: "",
    }));
  return [...privateResults, ...publicResults].slice(0, 12);
});

function close(restoreFocus = false): void {
  query.value = "";
  open.value = false;
  if (restoreFocus) void nextTick(() => trigger.value?.focus());
}

function toggle(): void {
  open.value = !open.value;
}

function handleDocumentKeydown(event: KeyboardEvent): void {
  if (event.key !== "Escape" || !open.value) return;
  event.preventDefault();
  close(true);
}

watch(open, (isOpen) => {
  if (isOpen) void nextTick(() => searchInput.value?.focus());
});

onMounted(() => document.addEventListener("keydown", handleDocumentKeydown));
onUnmounted(() => document.removeEventListener("keydown", handleDocumentKeydown));
</script>

<template>
  <div class="global-search" :class="{ 'is-open': open }">
    <RouterLink
      :to="{ name: ROUTE_NAMES.catalog }"
      class="catalog-trigger"
      :class="{ 'is-active': route.meta.catalog }"
      :aria-current="route.meta.catalog ? 'page' : undefined"
      aria-label="Otwórz katalog"
      @click="close"
    ><UiIcon name="person" /><span>Katalog</span></RouterLink>
    <button ref="trigger" type="button" class="global-search-trigger" aria-label="Otwórz globalne wyszukiwanie" :aria-expanded="open" @click="toggle"><UiIcon name="search" /><span>Szukaj</span></button>
    <div v-if="open" class="global-search-panel" role="search">
      <label><span class="sr-only">Szukaj we wszystkich dopuszczonych obiektach</span><input ref="searchInput" v-model="query" type="search" autocomplete="off" placeholder="Osoba, miejsce, akt, sprawa…" /></label>
      <p v-if="query && !results.length" class="empty-note">Brak wyniku w dopuszczonym eksporcie.</p>
      <EntityLink v-for="result in results" :key="`${result.kind}:${result.id}`" :kind="result.kind" :id="result.id" :label="result.label" :status="result.detail" @click="close()" />
      <small>Wyszukiwanie działa w pamięci i nie zapisuje tekstu w adresie.</small>
    </div>
  </div>
</template>
