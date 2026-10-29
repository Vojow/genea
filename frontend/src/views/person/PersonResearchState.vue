<script setup lang="ts">
import { RouterLink } from "vue-router";

import type { PersonProfileViewModel } from "../../models/person-profile-projector";
import { statusLabel } from "../../utils/formatting";
import EntityLink from "../../components/EntityLink.vue";


defineProps<{
  questions: PersonProfileViewModel["questions"];
  conflicts: PersonProfileViewModel["conflicts"];
  candidates: PersonProfileViewModel["identityCandidates"];
  unresolvedFacts: PersonProfileViewModel["unresolvedFacts"];
}>();
</script>

<template>
  <section class="person-profile-section person-research-state" aria-labelledby="person-research-title">
    <header class="detail-section-heading"><p class="eyebrow">STAN BADAŃ</p><h2 id="person-research-title">Co wymaga uwagi</h2></header>
    <div v-if="unresolvedFacts.length" class="person-alert-group"><h3>Ustalenia do sprawdzenia · {{ unresolvedFacts.length }}</h3><article v-for="fact in unresolvedFacts" :key="fact.id" class="person-research-alert is-unresolved"><div><strong>{{ fact.label || "Nierozstrzygnięte ustalenie" }}</strong><span>{{ statusLabel(fact.status) }}</span></div><p>{{ fact.value || "Brak bezpiecznej wartości w eksporcie." }}<template v-if="fact.place"> · {{ fact.place }}</template></p><small v-if="fact.identityAmbiguous">Podstawa może być udokumentowana, ale przypisanie do jednej osoby pozostaje nierozstrzygnięte.</small><EntityLink v-if="fact.sourceId" kind="source" :id="fact.sourceId" label="Otwórz podstawę źródłową" :source-id="fact.sourceId" /></article></div>
    <div v-if="conflicts.length" class="person-alert-group"><h3>Konflikty · {{ conflicts.length }}</h3><article v-for="conflict in conflicts" :key="conflict.id" class="person-research-alert is-conflict"><div><strong>{{ conflict.title }}</strong><span>{{ statusLabel(conflict.status || 'open') }}</span></div><p>{{ conflict.description }}</p><RouterLink to="/research/evidence">Otwórz w widoku dowodów</RouterLink></article></div>
    <div v-if="questions.length" class="person-alert-group"><h3>Pytania · {{ questions.length }}</h3><EntityLink v-for="question in questions" :key="question.id" kind="question" :id="question.id" :label="question.question" :status="question.workflowStage" /></div>
    <details v-if="candidates.length" class="person-identity-candidates">
      <summary>Kandydatury tożsamości <small>{{ candidates.length }}</small></summary>
      <article v-for="candidate in candidates" :key="candidate.id" class="person-research-alert is-candidate">
        <div><strong>{{ candidate.fromLabel }} ⇄ {{ candidate.toLabel }}</strong><span>{{ statusLabel(candidate.status) }}</span></div>
        <template v-if="candidate.hasStructuredBasis">
          <section v-if="candidate.support?.length"><h4>Przesłanki wspierające</h4><ul><li v-for="(item, index) in candidate.support" :key="index">{{ item }}</li></ul></section>
          <section v-if="candidate.against?.length"><h4>Ograniczenia i przesłanki przeciw</h4><ul><li v-for="(item, index) in candidate.against" :key="index">{{ item }}</li></ul></section>
          <section v-if="candidate.humanReviewDebt?.length"><h4>Do kontroli człowieka</h4><ul><li v-for="(item, index) in candidate.humanReviewDebt" :key="index">{{ item }}</li></ul></section>
        </template>
        <p v-else-if="candidate.basis">{{ candidate.basis }}</p>
        <EntityLink v-for="source in candidate.sourceLinks" :key="source.sourceId" kind="source" :id="source.sourceId" :label="source.sourceLabel" :source-id="source.sourceId" :occurrence-id="source.occurrenceId" />
        <small>Priorytet badawczy, nie prawdopodobieństwo i nie decyzja identity.</small>
      </article>
    </details>
    <p v-if="!conflicts.length && !questions.length && !candidates.length && !unresolvedFacts.length" class="empty-note">Brak otwartych elementów dotyczących tej osoby w kontrolowanym eksporcie. Nie oznacza to braku problemów badawczych.</p>
  </section>
</template>
