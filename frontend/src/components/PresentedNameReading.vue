<script setup lang="ts">
import type { PresentedNameReading as Reading } from "../types/presentation";
import { statusLabel } from "../utils/formatting";
import EntityLink from "./EntityLink.vue";


defineProps<{ reading: Reading; compact?: boolean }>();
</script>

<template>
  <article class="presented-name-reading" :class="{ 'is-compact': compact }">
    <div class="presented-name-layer">
      <span>W akcie zapisany jako</span>
      <strong :lang="reading.script === 'cyrillic' || reading.script === 'mixed' ? 'ru' : undefined">{{ reading.original }}</strong>
    </div>
    <div v-if="reading.transliteration" class="presented-name-layer">
      <span>Transliteracja</span>
      <strong>{{ reading.transliteration }}<template v-if="reading.uncertain"> ?</template></strong>
      <small>automatyczne przełożenie znak po znaku</small>
    </div>
    <div v-if="reading.polishForm" class="presented-name-layer">
      <span>Polska forma prezentacyjna</span>
      <strong>{{ reading.polishForm }}<template v-if="reading.uncertain"> (?)</template></strong>
      <small>nie jest decyzją tożsamości ani tłumaczeniem nazwiska</small>
    </div>
    <div class="presented-name-meta">
      <span>{{ statusLabel(reading.reviewStatus) }}</span>
      <span v-if="reading.uncertain">niepewny zapis</span>
    </div>
    <div v-if="!compact && reading.sourceIds.length" class="presented-name-sources">
      <EntityLink v-for="sourceId in reading.sourceIds" :key="sourceId" kind="source" :id="sourceId" label="Źródło zapisu" :source-id="sourceId" />
    </div>
  </article>
</template>
