<script setup lang="ts">
import { computed } from "vue";

import OpenFullRecordLink from "./OpenFullRecordLink.vue";
import EntityLink from "./EntityLink.vue";
import type { MapInspection, MapObservation, MapSourceObservation } from "../types/presentation";
import { eventTypeLabel, polishCount, polishDate, roleLabel } from "../utils/formatting";
import { useWorkspaceInspectorState } from "../composables/useWorkspaceInspectorState";

const props = defineProps<{ inspection: MapInspection | null; sourceIds: Set<string> }>();
const sortedItems = computed(() => [...(props.inspection?.items || [])].sort((first, second) => (first.yearFrom ?? 9999) - (second.yearFrom ?? 9999)));
const personObservationCount = computed(() => (props.inspection?.items || []).filter((item) => Boolean(item.personId)).length);
const sourceObservationCount = computed(() => (props.inspection?.items || []).filter((item) => !item.personId).length);
const selectionKey = computed(() => props.inspection?.route
  ? `${props.inspection.route.personLabel}:${props.inspection.route.from.yearFrom}:${props.inspection.route.to.yearFrom}`
  : props.inspection?.place?.id || props.inspection?.place?.label || "");
const inspectorTitle = computed(() => props.inspection?.place?.label || props.inspection?.items[0]?.placeText || "Inspektor mapy");
const { state: sheetState } = useWorkspaceInspectorState({ id: "map", hostId: "map-inspector", selectionKey });

function itemLabel(item: MapObservation | MapSourceObservation): string {
  return item.personLabel
    || item.occurrenceLabel
    || item.rawName
    || item.occurrenceId
    || "Wystąpienie źródłowe";
}
</script>

<template>
  <Teleport to="#map-inspector">
    <div class="workspace-inspector-shell map-inspector-shell" :class="`is-${sheetState}`">
      <header class="workspace-inspector-header">
        <div><span>Mapa</span><strong>{{ inspectorTitle }}</strong></div>
        <div class="workspace-inspector-size-controls" aria-label="Rozmiar panelu mapy">
          <button :aria-pressed="sheetState === 'collapsed'" aria-label="Zwiń inspektor mapy" @click="sheetState = 'collapsed'">−</button>
          <button :aria-pressed="sheetState === 'peek'" aria-label="Pokaż skrót inspektora mapy" @click="sheetState = 'peek'">▱</button>
          <button :aria-pressed="sheetState === 'expanded'" aria-label="Rozwiń inspektor mapy" @click="sheetState = 'expanded'">□</button>
        </div>
      </header>
      <div v-if="sheetState !== 'collapsed' && !inspection" class="empty-state"><span class="empty-glyph">⌖</span><h2>Wybierz punkt</h2><p>Zobacz osoby, daty, role i akta przypisane do tego miejsca.</p></div>
      <div v-else-if="inspection && sheetState !== 'collapsed'" class="inspector-content">
      <div class="inspector-kind"><span>{{ inspection.route ? "Odcinek osoby" : inspection.place?.isRegionCluster ? "Rejon" : "Miejsce" }}</span><span v-if="inspection.lines?.length" class="badge">{{ inspection.lines.map((line) => line.label).join(" · ") }}</span></div>
      <h2>{{ inspection.place?.label || inspection.items[0]?.placeText || "Miejsce nieustalone" }}</h2>
      <p class="inspector-subtitle">{{ inspection.route ? `${inspection.route.personLabel}: ${inspection.route.from.yearFrom} → ${inspection.route.to.yearFrom}` : `${polishCount(personObservationCount, "obserwacja osoby", "obserwacje osób", "obserwacji osób")}${sourceObservationCount ? ` · ${polishCount(sourceObservationCount, "wystąpienie źródłowe", "wystąpienia źródłowe", "wystąpień źródłowych")}` : ""}${inspection.place?.isRegionCluster ? ` · ${polishCount(inspection.place.localityCount || 0, "miejscowość", "miejscowości", "miejscowości")}` : ""}` }}</p>
      <OpenFullRecordLink v-if="inspection.place?.id" kind="place" :id="inspection.place.id" :label="inspection.place.label || 'Miejsce'" />
      <section class="detail-block">
        <h3>Osoby i wystąpienia źródłowe</h3>
        <div class="map-observation-list">
          <article v-for="(item, index) in sortedItems" :key="item.id || `${item.sourceId || item.occurrenceId}-${index}`" class="map-observation-card" :class="{ 'is-source-only': !item.personId }">
            <strong>{{ itemLabel(item) }}</strong>
            <small v-if="item.personId" class="map-person-life-span">{{ item.personLifeSpanLabel || "?–?" }}</small>
            <span>{{ eventTypeLabel(item.eventType || "") }} · {{ polishDate(item.dateFrom) }} · {{ item.kind === "residence" ? "miejsce pobytu" : "miejsce zdarzenia / aktu" }}</span>
            <small v-if="inspection.place?.isRegionCluster">{{ item.placeText || "Miejscowość nieustalona" }}</small>
            <small>{{ roleLabel(item.role || "") || "rola nieokreślona" }}</small>
            <small v-if="item.occurrenceId">Wystąpienie: {{ item.occurrenceId }}</small>
            <small v-if="item.eventId">Zdarzenie: {{ item.eventId }}</small>
            <small v-if="item.sourceId">Źródło: {{ item.sourceId }}</small>
            <EntityLink v-if="item.personId" kind="person" :id="item.personId" :label="itemLabel(item)" :occurrence-id="item.occurrenceId" :source-id="item.sourceId" />
            <EntityLink v-if="item.sourceId && sourceIds.has(item.sourceId)" kind="source" :id="item.sourceId" label="Otwórz akt i transkrypcję" />
          </article>
        </div>
      </section>
      <div v-if="inspection.place?.precision" class="map-unmapped">Punkt oznacza środek miejscowości. Nie wskazuje konkretnego domu, działki ani przebiegu historycznej drogi.</div>
      </div>
    </div>
  </Teleport>
</template>
