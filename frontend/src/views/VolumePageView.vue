<script setup lang="ts">
import { computed } from "vue";
import { useRoute } from "vue-router";

import { EntityIndex } from "../routing/entities";
import { useAppStore } from "../stores/app";
import EntityBreadcrumbs from "../components/EntityBreadcrumbs.vue";
import EntityLink from "../components/EntityLink.vue";
import NotFoundView from "../components/NotFoundView.vue";


const route = useRoute();
const store = useAppStore();
const index = computed(() => new EntityIndex(store.payload!));
const volumeId = computed(() => String(route.params.volumeId || ""));
const ordinal = computed(() => Number(route.params.ordinal));
const volume = computed(() => store.payload?.volumeIndexes?.find((item) => item.id === volumeId.value) || null);
const candidates = computed(() => volume.value?.candidates.filter((item) => item.ordinal === ordinal.value) || []);
const selected = computed(() => candidates.value.find((item) => item.regionId === route.query.region) || candidates.value[0] || null);
</script>

<template>
  <article v-if="volume && selected" class="volume-page-view">
    <EntityBreadcrumbs workspace="Badania / Woluminy" kind="volume" :label="volume.label" />
    <header class="entity-page-header"><div><p class="eyebrow">STRONA I REGION</p><h1>Strona {{ ordinal }}</h1><p>{{ selected.locator }}</p></div><EntityLink kind="volume" :id="volume.id" :label="volume.label" /></header>
    <div class="volume-page-layout">
      <figure><img :src="selected.renderUrl" :alt="`Render strony ${ordinal}`" /><figcaption>Render pochodny · locator {{ selected.locator }}</figcaption></figure>
      <section><h2>Dokładny region</h2><dl><dt>Region ID</dt><dd>{{ selected.regionId }}</dd><dt>bbox</dt><dd>{{ selected.bbox?.join(", ") || "cała strona / unknown" }}</dd><dt>Silnik</dt><dd>{{ selected.engine }} {{ selected.model }}</dd><dt>Review</dt><dd>{{ selected.reviewDecision || "do przeglądu" }}</dd></dl><blockquote>{{ selected.correctedText || selected.rawText }}</blockquote><p>Automatyczny odczyt jest pomocą lokalizacyjną, nie źródłem ani decyzją identity.</p><EntityLink v-for="run in volume.runs || []" :key="run.id" kind="run" :id="run.id" :label="`${run.backend} ${run.model || ''}`" :status="run.status" /></section>
    </div>
  </article>
  <NotFoundView v-else :reason="index.resolve('volume', volumeId).availability === 'not_exported' ? 'not_exported' : 'missing'" />
</template>
