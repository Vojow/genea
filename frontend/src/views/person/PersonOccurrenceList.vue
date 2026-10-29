<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";

import type { PersonOccurrenceEntry, PersonOccurrenceFilters, PersonOccurrenceGrouping } from "../../models/person-profile-projector";
import { filterPersonOccurrences, groupPersonOccurrences } from "../../models/person-profile-projector";
import { eventTypeLabel, roleLabel, statusLabel } from "../../utils/formatting";
import PersonOccurrenceCard from "./PersonOccurrenceCard.vue";


const props = defineProps<{ entries: PersonOccurrenceEntry[]; personId: string; showComponents?: boolean }>();
const PAGE_SIZE = 20;
const filters = reactive<PersonOccurrenceFilters>({ eventType: "", role: "", status: "", fromYear: null, toYear: null, conflictsOnly: false, attentionOnly: false });
const grouping = ref<PersonOccurrenceGrouping>("year");
const renderLimit = ref(PAGE_SIZE);
const filtered = computed(() => filterPersonOccurrences(props.entries, filters));
const visible = computed(() => filtered.value.slice(0, renderLimit.value));
const groups = computed(() => groupPersonOccurrences(visible.value, grouping.value));
const eventTypes = computed(() => [...new Set(props.entries.map((entry) => entry.eventType).filter(Boolean))].sort());
const roles = computed(() => [...new Set(props.entries.map((entry) => entry.role).filter(Boolean))].sort());
const statuses = computed(() => [...new Set(props.entries.map((entry) => entry.status).filter(Boolean))].sort());

function resetFilters(): void {
  Object.assign(filters, { eventType: "", role: "", status: "", fromYear: null, toYear: null, conflictsOnly: false, attentionOnly: false });
  renderLimit.value = PAGE_SIZE;
}

onMounted(() => {
  const state = history.state?.geneaPersonOccurrenceList;
  if (state?.personId === props.personId && state.filters) Object.assign(filters, state.filters);
  if (state?.personId === props.personId && ["year", "eventType"].includes(state.grouping)) grouping.value = state.grouping;
  if (state?.personId === props.personId && typeof state.renderLimit === "number") renderLimit.value = state.renderLimit;
});
watch([filters, renderLimit, grouping], () => {
  history.replaceState({ ...(history.state || {}), geneaPersonOccurrenceList: { personId: props.personId, filters: { ...filters }, grouping: grouping.value, renderLimit: renderLimit.value } }, "", window.location.href);
}, { deep: true });
watch(() => props.personId, () => { resetFilters(); grouping.value = "year"; });
watch(filters, () => { renderLimit.value = PAGE_SIZE; }, { deep: true });
</script>

<template>
  <section class="person-profile-section person-occurrence-list" aria-labelledby="person-occurrences-title">
    <header class="detail-section-heading"><p class="eyebrow">DOWODY</p><h2 id="person-occurrences-title">Akty i wystąpienia</h2><p>{{ filtered.length }} z {{ entries.length }} wpisów</p></header>
    <details class="person-occurrence-filters">
      <summary>Filtry wystąpień <small>{{ [filters.eventType, filters.role, filters.status, filters.conflictsOnly ? 'konflikt' : '', filters.attentionOnly ? 'uwaga' : ''].filter(Boolean).length }}</small></summary>
      <div class="person-filter-grid">
        <label><span>Grupowanie</span><select v-model="grouping"><option value="year">Chronologicznie</option><option value="eventType">Według typu aktu</option></select></label>
        <label><span>Typ aktu</span><select v-model="filters.eventType"><option value="">Wszystkie</option><option v-for="value in eventTypes" :key="value" :value="value">{{ eventTypeLabel(value) }}</option></select></label>
        <label><span>Rola</span><select v-model="filters.role"><option value="">Wszystkie</option><option v-for="value in roles" :key="value" :value="value">{{ roleLabel(value) }}</option></select></label>
        <label><span>Status</span><select v-model="filters.status"><option value="">Wszystkie</option><option v-for="value in statuses" :key="value" :value="value">{{ statusLabel(value) }}</option></select></label>
        <label><span>Od roku</span><input v-model.number="filters.fromYear" type="number" inputmode="numeric" placeholder="np. 1850" /></label>
        <label><span>Do roku</span><input v-model.number="filters.toYear" type="number" inputmode="numeric" placeholder="np. 1900" /></label>
        <label class="person-attention-filter"><input v-model="filters.conflictsOnly" type="checkbox" /><span>Tylko wpisy z jawnie powiązanym konfliktem</span></label>
        <label class="person-attention-filter"><input v-model="filters.attentionOnly" type="checkbox" /><span>Tylko wpisy wymagające uwagi</span></label>
      </div>
      <button type="button" class="quiet-button" @click="resetFilters">Wyczyść filtry</button>
    </details>
    <div v-if="groups.length" class="person-occurrence-groups">
      <details v-for="(group, index) in groups" :key="group.id" :open="index === 0">
        <summary><strong>{{ grouping === 'eventType' ? eventTypeLabel(group.label) : group.label }}</strong><span>{{ group.entries.length }} {{ group.entries.length === 1 ? "wpis" : "wpisy" }}</span><small v-if="group.commonStatus">{{ statusLabel(group.commonStatus) }}</small></summary>
        <PersonOccurrenceCard v-for="entry in group.entries" :key="entry.id" :entry="entry" :hide-group-status="group.commonStatus === entry.status" :show-component="showComponents" />
      </details>
    </div>
    <p v-else class="empty-note">Żaden wpis nie spełnia aktualnych filtrów. Brak wyniku filtra nie jest dowodem braku zdarzenia.</p>
    <button v-if="visible.length < filtered.length" type="button" class="person-load-more" @click="renderLimit += PAGE_SIZE">Pokaż kolejne {{ Math.min(PAGE_SIZE, filtered.length - visible.length) }} · pozostało {{ filtered.length - visible.length }}</button>
  </section>
</template>
