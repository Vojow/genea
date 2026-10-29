<script setup lang="ts">
import { computed } from "vue";
import { useRoute } from "vue-router";

import { EntityIndex } from "../routing/entities";
import { useAppStore } from "../stores/app";
import type { TelemetryValue, VolumeRunSummary } from "../types/presentation";
import EntityBreadcrumbs from "../components/EntityBreadcrumbs.vue";
import NotFoundView from "../components/NotFoundView.vue";


const route = useRoute();
const store = useAppStore();
const index = computed(() => new EntityIndex(store.payload!));
const left = computed(() => index.value.findRun(String(route.params.runId || "")));
const right = computed(() => index.value.findRun(String(route.params.otherRunId || "")));
const metricRows = ["durationMs", "tokens", "images", "cost"] as const;

function value(metric: TelemetryValue | undefined): string {
  return `${metric?.value ?? "unknown"} · ${metric?.provenance || "unknown"}`;
}

function quality(run: VolumeRunSummary | null, key: string): string {
  return value(run?.quality?.[key]);
}
</script>

<template>
  <article v-if="left && right" class="run-comparison-view">
    <EntityBreadcrumbs workspace="Badania / Przebiegi" />
    <header class="entity-page-header"><div><p class="eyebrow">PORÓWNANIE A/B</p><h1>Dokładność, błędy dowodowe i koszt</h1><p>Najpierw bramki jakości; koszt dopiero dla konfiguracji, które je spełniają.</p></div></header>
    <table>
      <thead><tr><th>Metryka</th><th>{{ left.backend }} {{ left.model }}</th><th>{{ right.backend }} {{ right.model }}</th></tr></thead>
      <tbody>
        <tr v-for="metric in ['recall', 'precision', 'cer', 'wer', 'fieldAccuracy']" :key="metric"><th>{{ metric }}</th><td>{{ quality(left, metric) }}</td><td>{{ quality(right, metric) }}</td></tr>
        <tr><th>Błędy dowodowe</th><td>{{ quality(left, 'unsupportedEvidence') }}</td><td>{{ quality(right, 'unsupportedEvidence') }}</td></tr>
        <tr v-for="metric in metricRows" :key="metric"><th>{{ metric }}</th><td>{{ value(left[metric]) }}</td><td>{{ value(right[metric]) }}</td></tr>
      </tbody>
    </table>
    <p class="method-note">`unknown` oznacza brak zachowanej telemetrii. UI nie wylicza pozornej dokładności ani kosztu z liczby regionów.</p>
  </article>
  <NotFoundView v-else reason="missing" />
</template>
