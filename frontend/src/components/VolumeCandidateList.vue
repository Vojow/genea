<script setup lang="ts">
import { computed, ref } from "vue";

import type { VolumeIndexCandidate, VolumeIndexSummary } from "../types/presentation";


const props = defineProps<{ volume: VolumeIndexSummary }>();
const items = ref<VolumeIndexCandidate[]>([...props.volume.candidates]);
const cursor = ref(items.value.length);
const loading = ref(false);
const failure = ref("");
const total = computed(() => props.volume.candidateCount ?? items.value.length);
const hasMore = computed(() => cursor.value < total.value);

async function loadMore(): Promise<void> {
  loading.value = true;
  failure.value = "";
  try {
    const response = await fetch(`/api/presentation/volume-index/${encodeURIComponent(props.volume.id)}/candidates?cursor=${cursor.value}&limit=50`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const page = await response.json() as { items?: VolumeIndexCandidate[]; nextCursor?: number | null; total?: number };
    const additions = Array.isArray(page.items) ? page.items : [];
    const known = new Set(items.value.map((item) => item.regionId));
    items.value.push(...additions.filter((item) => !known.has(item.regionId)));
    cursor.value = typeof page.nextCursor === "number" ? page.nextCursor : (page.total ?? total.value);
  } catch {
    failure.value = "Nie udało się pobrać kolejnej strony lokalnego indeksu.";
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <div class="volume-candidate-list">
    <RouterLink
      v-for="candidate in items"
      :key="candidate.regionId"
      class="entity-link"
      :to="{ name: 'research-volume-page', params: { volumeId: volume.id, ordinal: candidate.ordinal }, query: { region: candidate.regionId } }"
    >
      Strona {{ candidate.ordinal }} · {{ candidate.engine }} · {{ candidate.reviewDecision || "do review" }}
    </RouterLink>
    <p v-if="failure" class="empty-note" role="alert">{{ failure }}</p>
    <button v-if="hasMore" type="button" class="quiet-button" :disabled="loading" @click="loadMore">
      {{ loading ? "Pobieranie…" : `Załaduj więcej (${items.length}/${total})` }}
    </button>
  </div>
</template>
