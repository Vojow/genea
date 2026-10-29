<script setup lang="ts">
import { computed, ref, watchEffect } from "vue";
import { useRoute } from "vue-router";

import AppHeader from "./components/AppHeader.vue";
import GraphInspector from "./components/GraphInspector.vue";
import IdentityCurationDialog from "./components/IdentityCurationDialog.vue";
import MapInspector from "./components/MapInspector.vue";
import PrivateAccessPanel from "./components/PrivateAccessPanel.vue";
import SourcesView from "./components/SourcesView.vue";
import TreeInspector from "./components/TreeInspector.vue";
import RoutedWorkspace from "./views/RoutedWorkspace.vue";
import { useAppStore } from "./stores/app";
import type { IdentityReviewCandidate } from "./types/presentation";


const store = useAppStore();
const route = useRoute();
const isRoutedScreen = computed(() => Boolean(route.meta.entityKind || route.meta.workspace || route.meta.notFound));

watchEffect(() => {
  document.body.classList.toggle("has-routed-screen", isRoutedScreen.value);
});

const sourceIds = computed(() => new Set(store.payload?.sources.map((source) => source.id) || []));
const identityCandidate = ref<IdentityReviewCandidate | null>(null);
const priorityIdentityCandidates = computed<IdentityReviewCandidate[]>(() =>
  (store.payload?.identityCandidates || [])
    .filter((candidate) => candidate.status === "unresolved" && candidate.highPriority)
    .map((candidate) => ({
      id: candidate.id,
      status: candidate.status,
      fromOccurrenceId: candidate.fromOccurrenceId,
      toOccurrenceId: candidate.toOccurrenceId,
      label: `${candidate.fromLabel} ⇄ ${candidate.toLabel}`,
      fromLabel: candidate.fromLabel,
      toLabel: candidate.toLabel,
      scoreValue: candidate.score,
      highPriority: true,
      score: candidate.score == null ? "bez oceny" : `${Math.round(candidate.score * 100)}/100 siły tropu — to nie jest prawdopodobieństwo`,
      basis: candidate.basis || "Brak zapisanego uzasadnienia.",
    })),
);

function reloadAfterIdentityCuration(): void {
  window.setTimeout(() => window.location.reload(), 500);
}

function toggleGraphIdentity(personId: string): void {
  document.dispatchEvent(new CustomEvent("genea:graph-toggle-identity", { detail: { personId } }));
}

function toggleGraphEvidence(personId: string): void {
  document.dispatchEvent(new CustomEvent("genea:graph-toggle-evidence", { detail: { personId } }));
}

function toggleGraphEvidenceEvent(eventId: string): void {
  document.dispatchEvent(new CustomEvent("genea:graph-toggle-evidence-event", { detail: { eventId } }));
}

function toggleGraphCluster(clusterId: string): void {
  document.dispatchEvent(new CustomEvent("genea:graph-toggle-cluster", { detail: { clusterId } }));
}

</script>

<template>
  <span hidden data-vue-runtime="ready"></span>
  <Teleport to="#app-header-host"><AppHeader /></Teleport>
  <Teleport v-if="isRoutedScreen" to="#routed-view-host"><RoutedWorkspace /></Teleport>
  <Teleport v-if="store.contextNotice" to="#routed-view-host"><p class="context-notice" role="status">{{ store.contextNotice }}</p></Teleport>
  <SourcesView
    v-if="store.payload"
    :sources="store.payload.sources"
  />
  <MapInspector :inspection="store.mapInspection" :source-ids="sourceIds" />
  <GraphInspector
    :inspection="store.graphInspection"
    :priority-candidates="priorityIdentityCandidates"
    @review-identity="identityCandidate = $event"
    @toggle-identity="toggleGraphIdentity"
    @toggle-evidence="toggleGraphEvidence"
    @toggle-evidence-event="toggleGraphEvidenceEvent"
    @toggle-cluster="toggleGraphCluster"
  />
  <TreeInspector :inspection="store.treeInspection" />
  <PrivateAccessPanel />
  <IdentityCurationDialog
    :candidate="identityCandidate"
    :access="store.curatorAccess"
    @close="identityCandidate = null"
    @resolved="reloadAfterIdentityCuration"
  />
</template>
