<script setup lang="ts">
import type { PersonOccurrenceEntry } from "../../models/person-profile-projector";
import { eventTypeLabel, polishDate, roleLabel, statusLabel } from "../../utils/formatting";
import EntityLink from "../../components/EntityLink.vue";
import PresentedNameReading from "../../components/PresentedNameReading.vue";


defineProps<{ entry: PersonOccurrenceEntry; hideGroupStatus?: boolean; showComponent?: boolean }>();
</script>

<template>
  <article class="person-occurrence-card" :class="{ 'needs-attention': entry.uncertain || entry.conflictIds.length, 'has-open-identity-review': entry.humanIdentityReviewOpen }">
    <header><div><time>{{ polishDate(entry.eventDate) || "Bez daty" }}</time><h3>{{ eventTypeLabel(entry.eventType) }}</h3></div><span v-if="entry.uncertain || entry.conflictIds.length" class="person-attention-label">Wymaga uwagi</span></header>
    <p class="person-occurrence-role">{{ roleLabel(entry.role) || "Rola niewskazana" }}</p>
    <p v-if="showComponent" class="person-component-basis">Składnik tożsamości: {{ entry.componentLabel || "wystąpienie nieprzypisane" }}</p>
    <div v-if="entry.identityDecisionTier" class="person-occurrence-identity-review" role="status">
      <strong>{{ entry.identityDecisionLabel }}</strong>
      <span>{{ entry.humanIdentityReviewOpen ? "Kontrola człowieka: otwarta" : "Kontrola człowieka: zamknięta" }}</span>
      <small>Backing occurrence: {{ entry.id }}</small>
    </div>
    <p v-if="entry.placeLabel" class="person-occurrence-place">{{ entry.placeLabel }}</p>
    <PresentedNameReading v-if="entry.reading" :reading="entry.reading" compact />
    <div v-else class="person-occurrence-raw"><span>W akcie zapisany jako</span><strong>{{ entry.rawName || "Brak literalnego zapisu" }}</strong><small v-if="entry.normalizedName && entry.normalizedName !== entry.rawName">Forma indeksowa: {{ entry.normalizedName }}</small></div>
    <p v-if="!hideGroupStatus" class="person-occurrence-status">Status wystąpienia: {{ statusLabel(entry.status) }}</p>
    <div class="person-occurrence-links"><EntityLink v-if="entry.eventId" kind="event" :id="entry.eventId" :label="eventTypeLabel(entry.eventType)" :occurrence-id="entry.id" :source-id="entry.sourceId" /><EntityLink v-if="entry.placeId" kind="place" :id="entry.placeId" :label="entry.placeLabel" /><EntityLink v-if="entry.sourceId" kind="source" :id="entry.sourceId" :label="entry.sourceLabel || 'Otwórz akt, skan i transkrypcję'" :occurrence-id="entry.id" :source-id="entry.sourceId" /></div>
  </article>
</template>
