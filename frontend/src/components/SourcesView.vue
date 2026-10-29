<script setup lang="ts">
import { computed, ref } from "vue";
import { RouterLink } from "vue-router";

import type { PresentationSource } from "../types/presentation";
import { polishCount } from "../utils/formatting";
import { sourceReviewSummary } from "../../core/review-status.mjs";

const props = defineProps<{ sources: PresentationSource[] }>();

const query = ref("");
const reviewFilter = ref<"all" | "agent" | "human">("all");

const filteredSources = computed(() => {
  const normalized = query.value.trim().toLocaleLowerCase("pl-PL");
  return props.sources.filter((source) => {
    const review = sourceReviewSummary(source);
    if (reviewFilter.value === "agent" && !review?.hasAgentApproval) return false;
    if (reviewFilter.value === "human" && !review?.needsHumanReview) return false;
    const polishSegments = source.readings
      .flatMap((reading) => reading.segments)
      .filter((segment) => (segment.language || "").toLocaleLowerCase("pl-PL").includes("polski"))
      .map((segment) => segment.text);
    const reviewText = [review?.label, ...(source.reviewWorkflow?.fields || []).flatMap((field) => [field.field, field.value, field.alternative])];
    return [source.title, source.date, source.locator, ...polishSegments, ...reviewText]
      .filter(Boolean)
      .join(" ")
      .toLocaleLowerCase("pl-PL")
      .includes(normalized);
  });
});

const humanReviewCount = computed(() => props.sources.filter((source) => sourceReviewSummary(source)?.needsHumanReview).length);
const agentReviewCount = computed(() => props.sources.filter((source) => sourceReviewSummary(source)?.hasAgentApproval).length);
const transcribedSourceCount = computed(() => props.sources.filter((source) => transcriptCount(source) > 0).length);

function transcriptCount(source: PresentationSource): number {
  return source.readings.reduce((sum, reading) => sum + reading.segments.length, 0);
}
</script>

<template>
  <Teleport to="#sources-view-host">
    <div class="section-heading compact">
      <div>
        <p class="eyebrow">Katalog źródeł</p>
        <h1>Akta, materiały i transkrypcje</h1>
        <p class="lead">Wybierz materiał, aby otworzyć jego jedną, kompletną kartę: oryginał, główny odczyt po polsku, warianty, uczestników i stan kontroli.</p>
      </div>
      <label class="search-field wide">
        <span aria-hidden="true">⌕</span>
        <input id="source-search" v-model="query" type="search" placeholder="Szukaj aktu, miejsca lub roku…" />
      </label>
      <div class="source-review-filters" role="group" aria-label="Filtr kontroli odczytów">
        <button :class="{ 'is-active': reviewFilter === 'all' }" @click="reviewFilter = 'all'">Wszystkie</button>
        <button :class="{ 'is-active': reviewFilter === 'agent' }" @click="reviewFilter = 'agent'">Agentowo sprawdzone · {{ agentReviewCount }}</button>
        <button :class="{ 'is-active': reviewFilter === 'human' }" @click="reviewFilter = 'human'">Wymaga mojej kontroli · {{ humanReviewCount }}</button>
      </div>
    </div>
    <div class="view-metrics" aria-label="Podsumowanie aktualnego widoku źródeł" aria-live="polite">
      <div><strong id="source-visible-count">{{ filteredSources.length }}</strong><span>materiałów w widoku</span></div>
      <div><strong>{{ transcribedSourceCount }}</strong><span>z odczytem</span></div>
      <div><strong>{{ humanReviewCount }}</strong><span>do Twojej kontroli</span></div>
    </div>
    <div class="source-catalog-layout">
      <aside id="source-list" class="source-list source-catalog-list" aria-label="Lista źródeł">
        <RouterLink
          v-for="source in filteredSources"
          :key="source.id"
          class="source-card"
          :data-source-id="source.id"
          :to="{ name: 'source-detail', params: { sourceId: source.id } }"
        >
          <span class="document-icon">{{ source.availableLocally ? "A" : "↗" }}</span>
          <span>
            <strong>{{ source.title }}</strong>
            <small>
              {{ transcriptCount(source) ? `${polishCount(transcriptCount(source), "segment", "segmenty", "segmentów")} odczytu` : "bez transkrypcji" }}{{ source.availableLocally ? " · plik lokalny" : "" }}
            </small>
            <span v-if="sourceReviewSummary(source)" class="source-review-label" :data-review-state="sourceReviewSummary(source)?.state">{{ sourceReviewSummary(source)?.label }}</span>
          </span>
          <span class="source-card-action" aria-hidden="true">→</span>
        </RouterLink>
        <p v-if="!filteredSources.length" class="no-content">Brak źródeł spełniających filtr.</p>
      </aside>
    </div>
  </Teleport>
</template>
