<script setup lang="ts">
import { computed } from "vue";
import { RouterLink } from "vue-router";
import { useAppStore } from "../stores/app";
import { projectSourceIdentityGroups, projectSourceIdentityProposals, sourceOccurrencePath } from "../models/source-identity-groups";
import EntityLink from "../components/EntityLink.vue";
import NotFoundView from "../components/NotFoundView.vue";
import { roleLabel, statusLabel } from "../utils/formatting";

const props = defineProps<{ groupId: string }>();
const store = useAppStore();
const group = computed(() => store.payload ? projectSourceIdentityGroups(store.payload).find((item) => item.id === props.groupId) : null);
const proposals = computed(() => store.payload && group.value ? projectSourceIdentityProposals(store.payload, group.value) : []);
const debtValue = (value: unknown): string => typeof value === "string" ? value : JSON.stringify(value);
</script>

<template>
  <article v-if="group" class="entity-detail-view source-identity-detail" :data-source-identity-id="group.id">
    <RouterLink to="/sources" @click="store.captureNavigationContext()">← Akta i materiały</RouterLink>
    <header><p class="eyebrow">TOŻSAMOŚĆ ŹRÓDŁOWA</p><h1>Powiązane wystąpienia źródłowe</h1></header>
    <p role="status">Tożsamość zatwierdzona przez agenta · oczekuje na potwierdzenie człowieka.</p>
    <p>Liczba wystąpień zapisanych w aktach w tej grupie: {{ group.members.length }}. Grupa zachowuje odrębne zapisy nazw i źródłowe relacje. Osoba kanoniczna nie została utworzona.</p>
    <p>Brak osoby kanonicznej i profilu biograficznego pozostaje otwartą luką. Grupa nie zamyka pytania badawczego.</p>
    <section aria-labelledby="source-identity-members-title"><h2 id="source-identity-members-title">Zapisy w źródłach</h2>
      <article v-for="member in group.members" :key="member.occurrenceId" class="source-identity-member" :data-occurrence-id="member.occurrenceId">
        <h3>{{ member.literalName }}</h3><p>{{ roleLabel(member.role) }} · odczyt: {{ statusLabel(member.readingStatus) }}</p>
        <EntityLink kind="source" :id="member.sourceId" :label="member.sourceTitle" :source-id="member.sourceId" :occurrence-id="member.occurrenceId" />
        <RouterLink :to="sourceOccurrencePath(member.occurrenceId)" @click="store.captureNavigationContext()">Otwórz wystąpienie źródłowe</RouterLink>
      </article>
    </section>
    <section aria-labelledby="source-identity-debt-title"><h2 id="source-identity-debt-title">Niepewność i otwarty review człowieka</h2>
      <dl><template v-for="(value, key) in group.humanReviewDebt" :key="key"><dt>{{ key }}</dt><dd>{{ debtValue(value) }}</dd></template></dl>
    </section>
    <section v-if="proposals.length" aria-labelledby="source-identity-proposals-title">
      <h2 id="source-identity-proposals-title">Nierozstrzygnięte propozycje</h2>
      <p>Propozycje wymagają osobnego rozstrzygnięcia. Nie dodają wystąpień do tej grupy.</p>
      <article v-for="proposal in proposals" :key="proposal.id" class="source-identity-proposal" :data-identity-candidate-id="proposal.id">
        <h3>{{ proposal.members.map((member) => member.literalName).join(' ↔ ') }}</h3>
        <p>Tożsamość nierozstrzygnięta · priorytet badawczy, nie prawdopodobieństwo<span v-if="proposal.researchPriority !== null">: {{ proposal.researchPriority }}</span>.</p>
        <ul><li v-for="member in proposal.members" :key="member.occurrenceId">
          {{ member.literalName }} · {{ member.occurrenceId }}
          <EntityLink kind="source" :id="member.sourceId" :label="member.sourceTitle" :source-id="member.sourceId" :occurrence-id="member.occurrenceId" />
        </li></ul>
        <small>Kandydatura: {{ proposal.id }}</small>
      </article>
    </section>
    <details><summary>Podstawa decyzji i identyfikatory</summary>
      <dl><dt>Grupa</dt><dd>{{ group.id }}</dd><dt>Kandydatura</dt><dd>{{ group.candidateId }}</dd><dt>Rozstrzygnięcie</dt><dd>{{ group.resolutionCommandId }}</dd><dt>Ratyfikacja</dt><dd>{{ group.ratificationCommandId }}</dd><dt>Review</dt><dd>{{ group.reviewRefs.strict.reviewId }}</dd></dl>
      <pre>{{ JSON.stringify(group.reviewRefs, null, 2) }}</pre>
      <section v-if="group.directEdges" aria-labelledby="source-identity-edges-title">
        <h3 id="source-identity-edges-title">Bezpośrednie decyzje o tożsamości</h3>
        <p>Każda decyzja dotyczy wskazanej pary wystąpień. Członkostwo w grupie nie dopisuje decyzji o pozostałych parach ani pokrewieństwie.</p>
        <article v-for="edge in group.directEdges" :key="edge.candidateId" :data-identity-candidate-id="edge.candidateId">
          <h4>{{ edge.candidateId }}</h4>
          <ul><li v-for="id in edge.occurrenceIds" :key="id"><RouterLink :to="sourceOccurrencePath(id)" @click="store.captureNavigationContext()">{{ id }}</RouterLink></li></ul>
          <dl><dt>Rozstrzygnięcie</dt><dd>{{ edge.resolutionCommandId }}</dd><dt>Ratyfikacja</dt><dd>{{ edge.ratificationCommandId }}</dd></dl>
          <pre>{{ JSON.stringify(edge.reviewRefs, null, 2) }}</pre>
          <dl><template v-for="(value, key) in edge.humanReviewDebt" :key="key"><dt>{{ key }}</dt><dd>{{ debtValue(value) }}</dd></template></dl>
        </article>
      </section>
      <pre v-if="group.lineage">{{ JSON.stringify(group.lineage, null, 2) }}</pre>
    </details>
  </article>
  <NotFoundView v-else reason="missing" />
</template>

<style scoped>
.source-identity-detail { overflow-wrap: anywhere; }
.source-identity-member { margin-block: 1rem; }
.source-identity-member > a { margin-inline-end: 1rem; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; }
dl { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 2fr); gap: .5rem; }
dd { margin: 0; }
@media (max-width: 480px) { dl { grid-template-columns: minmax(0, 1fr); } }
</style>
