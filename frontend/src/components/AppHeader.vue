<script setup lang="ts">
import { computed } from "vue";
import { RouterLink, useRoute } from "vue-router";

import {
  contextNavigationFor,
  contextNavigationItemIsActive,
  PRIMARY_WORKSPACES,
  type ContextNavigationItem,
} from "../navigation/workspaces";
import { pathForView } from "../router";
import { useAppStore } from "../stores/app";
import GlobalSearch from "./GlobalSearch.vue";
import UiIcon from "./UiIcon.vue";


const store = useAppStore();
const route = useRoute();
const isCatalogRoot = computed(() => Boolean(route.meta.catalog));

const navigation = PRIMARY_WORKSPACES;
const contextNavigation = computed(() => contextNavigationFor(route, store.activeView));
const activeContextItem = computed(() => contextNavigation.value.items.find((item) => contextNavIsActive(item)) || contextNavigation.value.items[0]);

const generatedAt = computed(() => {
  const value = store.payload?.meta.generatedAt;
  if (!value) return "ładowanie…";
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? "eksport gotowy"
    : `eksport ${date.toLocaleString("pl-PL", { dateStyle: "short", timeStyle: "short" })}`;
});

function primaryNavIsActive(view: string): boolean {
  return !isCatalogRoot.value && store.activeView === view;
}

function contextNavIsActive(item: ContextNavigationItem): boolean {
  return contextNavigationItemIsActive(item, route);
}

function closeContextMenu(): void {
  const menu = document.querySelector<HTMLDetailsElement>(".workspace-context-menu");
  if (menu) menu.open = false;
}
</script>

<template>
  <header class="topbar" data-app-header>
    <RouterLink class="brand" :to="pathForView('tree')" aria-label="Genea — strona główna" @click="store.captureNavigationContext()">
      <span class="brand-mark" aria-hidden="true">G</span>
      <span class="brand-copy">
        <strong>Genea</strong>
        <small>lokalne archiwum rodzinne</small>
      </span>
    </RouterLink>

    <nav id="main-nav-host" class="main-nav" aria-label="Główna nawigacja">
      <RouterLink
        v-for="item in navigation"
        :key="item.view"
        class="nav-button"
        :class="{ 'is-active': primaryNavIsActive(item.view) }"
        :data-view="item.view"
        :aria-label="item.label"
        :title="item.description"
        :aria-current="primaryNavIsActive(item.view) ? 'page' : undefined"
        :to="pathForView(item.view)"
        @click="store.captureNavigationContext()"
      >
        <i class="nav-icon" aria-hidden="true"><UiIcon :name="item.icon" /></i>
        <span class="nav-label"><span class="nav-label-long">{{ item.label }}</span><span class="nav-label-short">{{ item.shortLabel }}</span></span>
      </RouterLink>
    </nav>

    <div class="topbar-actions">
      <GlobalSearch />
      <div class="sync-state" title="UI działa tylko na lokalnym eksporcie danych">
        <span class="sync-dot" aria-hidden="true"></span>
        <span>
          <strong>Lokalnie</strong>
          <small id="generated-at">{{ generatedAt }}</small>
        </span>
      </div>
    </div>
  </header>

  <div class="workspace-context-bar" :data-context="contextNavigation.id">
    <strong class="workspace-context-title sr-only">Sekcja {{ contextNavigation.label }}</strong>
    <nav class="workspace-context-nav workspace-context-nav-desktop" :aria-label="`Nawigacja kontekstowa: ${contextNavigation.label}`">
      <RouterLink
        v-for="item in contextNavigation.items"
        :key="item.id"
        class="context-nav-link"
        :class="{ 'is-active': contextNavIsActive(item) }"
        :to="item.to"
        :title="item.description"
        :aria-current="contextNavIsActive(item) ? 'page' : undefined"
        @click="store.captureNavigationContext()"
      >
        <UiIcon :name="item.icon" />
        <span>{{ item.label }}</span>
      </RouterLink>
    </nav>
    <details class="workspace-context-menu">
      <summary><UiIcon :name="activeContextItem.icon" /><span>{{ activeContextItem.label }}</span><span aria-hidden="true">⌄</span></summary>
      <nav class="workspace-context-nav workspace-context-nav-mobile" :aria-label="`Nawigacja kontekstowa: ${contextNavigation.label}`">
        <RouterLink
          v-for="item in contextNavigation.items"
          :key="item.id"
          class="context-nav-link"
          :class="{ 'is-active': contextNavIsActive(item) }"
          :to="item.to"
          :title="item.description"
          :aria-current="contextNavIsActive(item) ? 'page' : undefined"
          @click="store.captureNavigationContext(); closeContextMenu()"
        >
          <UiIcon :name="item.icon" />
          <span>{{ item.label }}</span>
        </RouterLink>
      </nav>
    </details>
  </div>
</template>
