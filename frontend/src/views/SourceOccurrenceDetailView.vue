<script setup lang="ts">
import { computed } from "vue";
import { RouterLink } from "vue-router";
import { useAppStore } from "../stores/app";
import { projectSourceIdentityGroups, sourceIdentityPath } from "../models/source-identity-groups";
import EntityLink from "../components/EntityLink.vue";
import NotFoundView from "../components/NotFoundView.vue";
import { roleLabel, statusLabel } from "../utils/formatting";

const props = defineProps<{ occurrenceId: string }>();
const store = useAppStore();
const group = computed(() => store.payload ? projectSourceIdentityGroups(store.payload).find((item) => item.occurrenceIds.includes(props.occurrenceId)) : null);
const member = computed(() => group.value?.members.find((item) => item.occurrenceId === props.occurrenceId));
</script>

<template>
  <article v-if="group && member" class="entity-detail-view source-occurrence-detail" :data-occurrence-id="member.occurrenceId">
    <header><p class="eyebrow">WYSTĄPIENIE ŹRÓDŁOWE</p><h1>{{ member.literalName }}</h1></header>
    <p>{{ roleLabel(member.role) }} · odczyt: {{ statusLabel(member.readingStatus) }}</p>
    <EntityLink kind="source" :id="member.sourceId" :label="member.sourceTitle" :source-id="member.sourceId" :occurrence-id="member.occurrenceId" />
    <p>Tożsamość zatwierdzona przez agenta · oczekuje na potwierdzenie człowieka.</p>
    <RouterLink :to="sourceIdentityPath(group.id)" @click="store.captureNavigationContext()">Otwórz grupę wystąpień źródłowych ({{ group.members.length }})</RouterLink>
    <p>Wystąpienie zachowuje własny zapis nazwy i identyfikator: {{ member.occurrenceId }}.</p>
  </article>
  <NotFoundView v-else reason="missing" />
</template>

<style scoped>
.source-occurrence-detail { overflow-wrap: anywhere; }
</style>
