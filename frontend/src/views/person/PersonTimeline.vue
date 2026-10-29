<script setup lang="ts">
import { computed, ref } from "vue";

import type { PersonTimelineEntry } from "../../models/person-profile-projector";
import { eventTypeLabel, polishDate, roleLabel, statusLabel } from "../../utils/formatting";
import EntityLink from "../../components/EntityLink.vue";


const props = defineProps<{ entries: PersonTimelineEntry[]; undated: PersonTimelineEntry[]; identityAmbiguous?: boolean; identityGroup?: boolean; lifeSpanLabel: string; lifeSpanNotice: string }>();
const visibleLimit = ref(12);
const visibleEntries = computed(() => props.entries.slice(0, visibleLimit.value));
const uncertainCount = computed(() => [...props.entries, ...props.undated].filter((entry) => entry.uncertain).length);

function compactDate(entry: PersonTimelineEntry): string {
  const value = entry.date.trim();
  if (!value) return entry.year == null ? "Bez daty" : String(entry.year);
  if (/^\d{4}(?:-\d{2}(?:-\d{2})?)?$/.test(value) || value.length <= 12) return polishDate(value);
  return entry.year == null ? "Bez daty" : `${entry.year}${entry.uncertain ? "?" : ""}`;
}

function showsSourceDate(entry: PersonTimelineEntry): boolean {
  return Boolean(entry.date.trim()) && compactDate(entry) !== polishDate(entry.date);
}
</script>

<template>
  <section class="person-profile-section person-timeline" aria-labelledby="person-timeline-title">
    <header class="detail-section-heading"><p class="eyebrow">CHRONOLOGIA</p><h2 id="person-timeline-title">{{ identityGroup ? "Oś materiałów kandydatów" : identityAmbiguous ? "Oś materiałów do korelacji" : "Oś życia i miejsc" }}</h2><div class="person-timeline-life-span"><strong>{{ lifeSpanLabel }}</strong><span>{{ lifeSpanNotice }}</span></div><p v-if="identityGroup">Zestawienie porządkuje źródła kilku osób; nie przedstawia ich jako jednej biografii.</p><p v-else-if="identityAmbiguous">Zestawienie porządkuje materiał nierozstrzygniętej pozycji w drzewie; nie tworzy potwierdzonej biografii.</p><p v-else-if="uncertainCount">{{ uncertainCount }} {{ uncertainCount === 1 ? "wpis wymaga" : "wpisów wymaga" }} korelacji; oś nie podnosi ich statusu.</p></header>
    <ol v-if="entries.length" class="person-timeline-list">
      <li v-for="entry in visibleEntries" :key="entry.id" :class="{ 'is-uncertain': entry.uncertain }">
        <time>{{ compactDate(entry) }}</time>
        <div><h3>{{ eventTypeLabel(entry.eventType) }}</h3><p v-if="identityAmbiguous" class="person-component-basis">Składnik tożsamości: {{ entry.componentLabels.join(" · ") || "wystąpienie nieprzypisane" }}</p><p>{{ entry.roles.map(roleLabel).join(" · ") || "rola niewskazana" }}</p><p v-if="entry.placeLabel">{{ entry.placeLabel }}</p><p v-if="showsSourceDate(entry)" class="person-timeline-source-date">Zapis daty w źródle: <span>{{ entry.date }}</span></p><small>{{ statusLabel(entry.status) }} · {{ entry.occurrenceIds.length }} {{ entry.occurrenceIds.length === 1 ? "wystąpienie" : "wystąpienia" }}</small><div class="person-timeline-links"><EntityLink v-if="entry.eventId" kind="event" :id="entry.eventId" :label="eventTypeLabel(entry.eventType)" :source-id="entry.sourceId" /><EntityLink v-if="entry.placeId" kind="place" :id="entry.placeId" :label="entry.placeLabel" /><EntityLink v-if="entry.sourceId" kind="source" :id="entry.sourceId" :label="entry.sourceLabel || 'Akt i transkrypcja'" :source-id="entry.sourceId" /></div></div>
      </li>
    </ol>
    <button v-if="visibleEntries.length < entries.length" type="button" class="person-load-more" @click="visibleLimit += 20">Pokaż kolejne zdarzenia · pozostało {{ entries.length - visibleEntries.length }}</button>
    <details v-if="undated.length" class="person-undated-events"><summary>Bez ustalonej daty <small>{{ undated.length }}</small></summary><article v-for="entry in undated" :key="entry.id"><strong>{{ eventTypeLabel(entry.eventType) }}</strong><span>{{ entry.roles.map(roleLabel).join(" · ") }}</span><EntityLink v-if="entry.sourceId" kind="source" :id="entry.sourceId" :label="entry.sourceLabel || 'Akt i transkrypcja'" :source-id="entry.sourceId" /></article></details>
    <p v-if="!entries.length && !undated.length" class="empty-note">Brak datowanych zdarzeń powiązanych jawnie z tą osobą.</p>
  </section>
</template>
