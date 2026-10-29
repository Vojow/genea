<script setup lang="ts">
import type { PersonProfileFact } from "../../models/person-profile-projector";
import { statusLabel } from "../../utils/formatting";
import EntityLink from "../../components/EntityLink.vue";


defineProps<{ facts: PersonProfileFact[] }>();
</script>

<template>
  <section class="person-profile-section person-fact-summary" aria-labelledby="person-facts-title">
    <header class="detail-section-heading"><p class="eyebrow">PODSUMOWANIE</p><h2 id="person-facts-title">Najważniejsze ustalenia</h2></header>
    <div v-if="facts.length" class="person-fact-list">
      <article v-for="fact in facts" :key="fact.id" class="person-fact-row">
        <div><span>{{ fact.label }}</span><strong>{{ fact.value }}</strong><small>{{ [fact.place, statusLabel(fact.status)].filter(Boolean).join(" · ") }}</small></div>
        <EntityLink v-if="fact.sourceId" kind="source" :id="fact.sourceId" label="Podstawa źródłowa" :source-id="fact.sourceId" />
      </article>
    </div>
    <p v-else class="empty-note">Brak jawnie ustalonych faktów w kontrolowanym eksporcie. Nie uzupełniamy tej sekcji wnioskami z samych wystąpień.</p>
  </section>
</template>
