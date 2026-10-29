<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { RouterLink } from "vue-router";

import Badge from "./Badge.vue";
import OpenFullRecordLink from "./OpenFullRecordLink.vue";
import EntityLink from "./EntityLink.vue";
import type { GraphInspectorModel, IdentityReviewCandidate } from "../types/presentation";
import { reviewBadgeKind } from "../../core/review-status.mjs";
import { useWorkspaceInspectorState } from "../composables/useWorkspaceInspectorState";

const props = defineProps<{
  inspection: GraphInspectorModel | null;
  priorityCandidates: IdentityReviewCandidate[];
}>();
const emit = defineEmits<{
  reviewIdentity: [candidate: IdentityReviewCandidate];
  toggleIdentity: [personId: string];
  toggleEvidence: [personId: string];
  toggleEvidenceEvent: [eventId: string];
  toggleCluster: [clusterId: string];
}>();
const researchCardOpen = ref(false);
const selectionKey = computed(() => props.inspection?.identityPersonId
  || props.inspection?.clusterId
  || props.inspection?.evidenceEventId
  || props.inspection?.sourceId
  || "");
const inspectorTitle = computed(() => props.inspection?.title || (props.priorityCandidates.length ? "Kolejka kuratorska" : "Inspektor sieci"));
const { state: sheetState } = useWorkspaceInspectorState({ id: "graph", hostId: "graph-inspector", selectionKey });

watch(() => props.inspection?.identityPersonId, () => {
  researchCardOpen.value = false;
});
</script>

<template>
  <Teleport to="#graph-inspector">
    <div class="workspace-inspector-shell graph-inspector-shell" :class="`is-${sheetState}`">
      <header class="workspace-inspector-header">
        <div><span>Sieć</span><strong>{{ inspectorTitle }}</strong></div>
        <div class="workspace-inspector-size-controls" aria-label="Rozmiar panelu sieci">
          <button :aria-pressed="sheetState === 'collapsed'" aria-label="Zwiń inspektor sieci" @click="sheetState = 'collapsed'">−</button>
          <button :aria-pressed="sheetState === 'peek'" aria-label="Pokaż skrót inspektora sieci" @click="sheetState = 'peek'">▱</button>
          <button :aria-pressed="sheetState === 'expanded'" aria-label="Rozwiń inspektor sieci" @click="sheetState = 'expanded'">□</button>
        </div>
      </header>
      <div v-if="sheetState !== 'collapsed' && !inspection && !priorityCandidates.length" class="empty-state"><span class="empty-glyph">↗</span><h2>Wybierz element sieci</h2><p>Zobacz rolę w akcie, źródło, poziom pewności i nierozstrzygnięte połączenia.</p></div>
      <div v-else-if="sheetState !== 'collapsed' && !inspection" class="inspector-content">
      <div class="inspector-kind"><span>Kolejka kuratorska</span><Badge kind="is-warning">≥ próg</Badge></div>
      <h2>Tożsamości do oceny</h2>
      <p class="inspector-subtitle">Wysoki wynik ustala kolejność przeglądu. Nie jest prawdopodobieństwem i nie uruchamia automatycznego scalania.</p>
      <div class="detail-block">
        <div v-for="candidate in priorityCandidates" :key="candidate.id" class="candidate-link">
          <strong>{{ candidate.fromLabel }} ⇄ {{ candidate.toLabel }}</strong>
          <span class="candidate-score">{{ candidate.score }}</span>
          <p>{{ candidate.basis }}</p>
          <button type="button" class="candidate-action" @click="emit('reviewIdentity', candidate)">Porównaj i rozstrzygnij</button>
        </div>
      </div>
    </div>
      <div v-else-if="inspection && sheetState !== 'collapsed' && inspection.empty" class="empty-state"><span class="empty-glyph">{{ inspection.glyph || "↗" }}</span><h2>{{ inspection.title }}</h2><p>{{ inspection.subtitle }}</p></div>
      <div v-else-if="inspection && sheetState !== 'collapsed'" class="inspector-content">
      <div class="inspector-kind"><span>{{ inspection.kindLabel }}</span><Badge :kind="inspection.statusKind">{{ inspection.status }}</Badge></div>
      <Badge v-if="inspection.review" class="inspector-review-badge" :kind="reviewBadgeKind(inspection.review.state)">{{ inspection.review.label }}</Badge>
      <h2>{{ inspection.title }}</h2>
      <p class="inspector-subtitle">{{ inspection.subtitle }}</p>
      <OpenFullRecordLink v-if="inspection.identityPersonId" kind="person" :id="inspection.identityPersonId" :label="inspection.title" />
      <OpenFullRecordLink v-else-if="inspection.clusterId" kind="cluster" :id="inspection.clusterId" :label="inspection.title" />
      <OpenFullRecordLink v-else-if="inspection.evidenceEventId" kind="event" :id="inspection.evidenceEventId" :label="inspection.title" />
      <OpenFullRecordLink v-else-if="inspection.sourceId" kind="source" :id="inspection.sourceId" :label="inspection.title" />
      <RouterLink v-if="inspection.externalProfileHref" class="open-full-record-link" :to="inspection.externalProfileHref">Otwórz literalny profil źródła zewnętrznego</RouterLink>
      <section v-if="inspection.approvedActMaterials?.length" class="detail-block approved-act-materials" aria-label="Powiązane materiały aktowe">
        <h3>Powiązane akty</h3>
        <p>{{ inspection.approvedActMaterialSummary }}</p>
        <article v-for="material in inspection.approvedActMaterials" :key="material.sourceId" class="identity-appearance">
          <strong>{{ material.title }}</strong>
          <EntityLink kind="source" :id="material.sourceId" :source-id="material.sourceId" :occurrence-id="material.occurrenceIds[0]" label="Otwórz akt i transkrypcję" />
        </article>
      </section>
      <div v-if="inspection.searchMatch" class="graph-search-basis" role="status">
        <strong>{{ inspection.searchMatch.title }}: {{ inspection.searchMatch.label }}</strong>
        <span v-if="inspection.searchMatch.detail">{{ inspection.searchMatch.detail }}</span>
        <small v-if="inspection.searchMatch.occurrenceId">{{ inspection.searchMatch.occurrenceId }}</small>
      </div>
      <button
        v-if="inspection.clusterId && inspection.clusterActionLabel"
        type="button"
        class="identity-toggle-action cluster-toggle-action"
        :aria-expanded="inspection.clusterExpanded === true"
        @click="emit('toggleCluster', inspection.clusterId)"
      >{{ inspection.clusterActionLabel }}</button>
      <button
        v-if="inspection.identityPersonId && inspection.identityActionLabel"
        type="button"
        class="identity-toggle-action"
        :aria-expanded="inspection.identityExpanded"
        @click="emit('toggleIdentity', inspection.identityPersonId)"
      >{{ inspection.identityActionLabel }}</button>
      <button
        v-if="inspection.evidencePersonId && inspection.evidenceActionLabel"
        type="button"
        class="evidence-mode-action"
        :aria-expanded="researchCardOpen"
        @click="researchCardOpen = !researchCardOpen"
      >{{ researchCardOpen ? "Zamknij kartę badawczą" : inspection.evidenceActionLabel }}</button>
      <button
        v-if="inspection.evidenceEventId && inspection.evidenceEventActionLabel"
        type="button"
        class="evidence-graph-action"
        :aria-expanded="inspection.evidenceEventExpanded === true"
        @click="emit('toggleEvidenceEvent', inspection.evidenceEventId)"
      >{{ inspection.evidenceEventActionLabel }}</button>
      <div v-if="inspection.appearances?.length && researchCardOpen" class="detail-block research-card" data-testid="person-research-card">
        <div class="research-card-heading">
          <div><span>Karta badawcza osoby</span><h3>Chronologia wystąpień i aktów</h3></div>
          <strong>{{ inspection.appearances.length }}</strong>
        </div>
        <button
          v-if="inspection.evidencePersonId"
          type="button"
          class="evidence-graph-action"
          :aria-pressed="inspection.evidenceGraphVisible === true"
          @click="emit('toggleEvidence', inspection.evidencePersonId)"
        >{{ inspection.evidenceGraphVisible ? "Ukryj akty na grafie" : "Pokaż akty na grafie" }}</button>
        <div v-for="appearance in inspection.appearances" :key="appearance.id" class="identity-appearance">
          <strong>{{ appearance.label }}</strong>
          <span>{{ appearance.literalName }}</span>
          <small>{{ appearance.source }}</small>
          <Badge v-if="appearance.review" :kind="reviewBadgeKind(appearance.review.state)">{{ appearance.review.label }}</Badge>
          <EntityLink v-if="appearance.sourceId" kind="source" :id="appearance.sourceId" label="Otwórz akt" :occurrence-id="appearance.id" :source-id="appearance.sourceId" />
        </div>
      </div>
      <div v-for="section in inspection.sections || []" :key="section.title" class="detail-block">
        <h3>{{ section.title }}</h3>
        <div class="fact-list"><div v-for="row in section.rows" :key="row[0]" class="fact-row"><span>{{ row[0] }}</span><span>{{ row[1] ?? "—" }}</span></div></div>
      </div>
      <div v-if="inspection.candidates?.length" class="detail-block">
        <h3>Kandydaci tej samej tożsamości</h3>
        <div v-for="candidate in inspection.candidates" :key="candidate.id" class="candidate-link">
          <strong>{{ candidate.label }}</strong><span class="candidate-score">{{ candidate.score }}</span>{{ candidate.basis }}
          <button v-if="candidate.highPriority" type="button" class="candidate-action" @click="emit('reviewIdentity', candidate)">Porównaj i rozstrzygnij</button>
        </div>
      </div>
      <div v-if="inspection.participants?.length" class="detail-block">
        <h3>Uczestnicy i role</h3>
        <div v-for="participant in inspection.participants" :key="`${participant.role}-${participant.name}`" class="fact-row participant-row">
          <span>{{ participant.role }}</span><span class="participant-name"><strong>{{ participant.name }}</strong><small v-if="participant.details">{{ participant.details }}</small><small v-if="participant.original" lang="ru">oryg.: {{ participant.original }}</small></span>
        </div>
      </div>
      <EntityLink v-if="inspection.sourceId && inspection.actionLabel" class="primary-action" kind="source" :id="inspection.sourceId" :label="inspection.actionLabel" />
      </div>
    </div>
  </Teleport>
</template>
