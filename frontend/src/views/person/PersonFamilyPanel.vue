<script setup lang="ts">
import { computed } from "vue";
import { getActivePinia } from "pinia";
import { RouterLink } from "vue-router";
import type { PersonProfileLink, PersonProfileViewModel } from "../../models/person-profile-projector";
import { groupSourceIdentityFamilyRows, projectSourceIdentityGroups, sourceIdentityPath } from "../../models/source-identity-groups";
import { useAppStore } from "../../stores/app";
import { statusLabel } from "../../utils/formatting";
import EntityLink from "../../components/EntityLink.vue";


const props = defineProps<{ family: PersonProfileViewModel["family"]; identityAmbiguous?: boolean }>();
const pinia = getActivePinia();
const store = pinia ? useAppStore(pinia) : null;
const sourceRows = computed(() => groupSourceIdentityFamilyRows(props.family.sourceReported, store?.payload ? projectSourceIdentityGroups(store.payload) : []));

function keyFor(person: PersonProfileLink): string {
  return `${person.id || "unlinked"}:${person.label}`;
}
</script>

<template>
  <section class="person-profile-section person-family-panel" aria-labelledby="person-family-title">
    <header class="detail-section-heading"><p class="eyebrow">RELACJE</p><h2 id="person-family-title">Rodzina</h2></header>
    <p v-if="identityAmbiguous" class="person-family-identity-note">Relacje zachowują strukturę drzewa, ale ich przypisanie do jednej osoby wymaga korelacji.</p>
    <details v-if="family.parents.length || family.siblings.length" open>
      <summary>{{ identityAmbiguous ? "Rodzice i rodzeństwo — do korelacji" : "Rodzice i rodzeństwo" }} <small>{{ family.parents.length + family.siblings.length }}</small></summary>
      <div v-if="family.parents.length" class="person-relation-list"><h3>Rodzice</h3><template v-for="person in family.parents" :key="keyFor(person)"><p class="person-relation-person"><EntityLink v-if="person.id" kind="person" :id="person.id" :label="person.label" :status="person.status" /><span v-else>{{ person.label }} · {{ statusLabel(person.status) }}</span><small>{{ person.lifeSpanLabel }}</small></p></template></div>
      <div v-if="family.siblings.length" class="person-relation-list"><h3>Rodzeństwo</h3><template v-for="person in family.siblings" :key="keyFor(person)"><p class="person-relation-person"><EntityLink v-if="person.id" kind="person" :id="person.id" :label="person.label" :status="person.status" /><span v-else>{{ person.label }} · {{ statusLabel(person.status) }}</span><small>{{ person.lifeSpanLabel }}</small></p></template></div>
    </details>
    <details v-if="family.unions.length" open>
      <summary>Udokumentowane związki <small>{{ family.unions.length }}</small></summary>
      <article v-for="union in family.unions" :key="union.id" class="person-union-card">
        <header><strong>{{ union.label }}</strong><span>{{ statusLabel(union.status) }}</span></header>
        <p>{{ [union.date, union.place].filter(Boolean).join(" · ") || "Brak daty i miejsca w eksporcie" }}</p>
        <div class="person-relation-list"><template v-for="person in union.partners" :key="keyFor(person)"><p class="person-relation-person"><EntityLink v-if="person.id" kind="person" :id="person.id" :label="person.label" :status="person.status" /><span v-else>{{ person.label }}</span><small>{{ person.lifeSpanLabel }}</small></p></template></div>
        <div v-if="union.children.length" class="person-union-children"><h3>Dzieci · {{ union.children.length }}</h3><template v-for="person in union.children" :key="keyFor(person)"><p class="person-relation-person"><EntityLink v-if="person.id" kind="person" :id="person.id" :label="person.label" :status="person.status" /><span v-else>{{ person.label }} · {{ statusLabel(person.status) }}</span><small>{{ person.lifeSpanLabel }}</small></p></template></div>
        <div class="person-union-sources"><EntityLink v-for="sourceId in union.sourceIds" :key="sourceId" kind="source" :id="sourceId" label="Źródło relacji" :source-id="sourceId" /></div>
      </article>
    </details>
    <details v-if="family.unconfirmed.length" class="person-family-unconfirmed">
      <summary>Do sprawdzenia <small>{{ family.unconfirmed.length }}</small></summary>
      <article v-for="union in family.unconfirmed" :key="union.id" class="person-union-card is-unconfirmed">
        <header><strong>{{ union.label }}</strong><span>{{ statusLabel(union.status) }}</span></header>
        <p>{{ [union.date, union.place].filter(Boolean).join(" · ") || "Relacja bez ustalonej daty" }}</p>
        <template v-for="person in union.partners" :key="keyFor(person)"><EntityLink v-if="person.id" kind="person" :id="person.id" :label="person.label" :status="person.status" /><p v-else>{{ person.label }}</p></template>
      </article>
    </details>
    <details v-if="family.externalTreeRelations.length" class="person-family-source-reported" open>
      <summary>Relacje wskazane w drzewie zewnętrznym <small>{{ family.externalTreeRelations.length }}</small></summary>
      <p class="person-family-identity-note">Każda relacja pozostaje przypisana do konkretnej sieci i ma status imported_unverified. Powtórzenie w innej sieci nie jest dodatkowym potwierdzeniem.</p>
      <article v-for="relation in family.externalTreeRelations" :key="relation.id" class="person-source-relation-card is-unresolved">
        <header><span>{{ relation.relationLabel }}</span><strong>{{ relation.person.label }}</strong><small>{{ relation.person.lifeSpanLabel }}</small></header>
        <p class="person-source-relation-status"><strong>{{ relation.networkLabel }} · imported_unverified</strong></p>
        <EntityLink v-if="relation.person.id" kind="person" :id="relation.person.id" :label="`Otwórz profil kandydacki: ${relation.person.label}`" />
      </article>
    </details>
    <details v-if="family.sourceReported.length" class="person-family-source-reported" open>
      <summary>Powiązania wskazane w aktach <small>{{ sourceRows.length }}</small></summary>
      <p class="person-family-identity-note">To powiązania rodzinne i FAN jawnie wskazane w źródłach lub rolach uczestników aktu. Rozstrzygnięta tożsamość osoby nie awansuje jeszcze relacji do drzewa kanonicznego.</p>
      <template v-for="row in sourceRows" :key="row.id">
      <article v-if="row.group" class="person-source-relation-card is-unresolved source-identity-family-row" :data-source-identity-id="row.group.id">
        <header><span>{{ row.relations[0]!.relationLabel }}</span><strong>Powiązane wystąpienia źródłowe</strong></header>
        <p>Tożsamość zatwierdzona przez agenta · oczekuje na potwierdzenie człowieka.</p>
        <p>Relacja pozostaje wskazana w źródłach i wymaga osobnego potwierdzenia.</p>
        <RouterLink :to="sourceIdentityPath(row.group.id)" @click="store?.captureNavigationContext()">Otwórz grupę wystąpień źródłowych ({{ row.group.members.length }})</RouterLink>
        <p v-for="member in row.group.members" :key="member.occurrenceId">Zapis źródłowy: „{{ member.literalName }}”</p>
        <ul class="person-source-evidence-list" aria-label="Dowody relacji powiązanych wystąpień">
          <li v-for="source in row.relations.flatMap((relation) => relation.sources)" :key="source.id + source.relationshipIds.join(':')">
            <div><strong>{{ source.sourceLabel || 'Akt / materiał źródłowy' }}</strong>
              <small>Relacja: {{ source.statuses.map(statusLabel).join(' · ') }}<template v-if="source.readingCertainties.length"> · odczyt {{ source.readingCertainties.map(statusLabel).join(' · ') }}</template></small>
              <small v-if="source.provenance.includes('event_participation_role')">Podstawa: jawne role uczestników tego aktu</small>
              <p v-for="literal in source.literals" :key="literal">„{{ literal }}”</p>
            </div>
            <EntityLink v-if="source.sourceId" kind="source" :id="source.sourceId" :label="source.sourceLabel || 'Otwórz akt i transkrypcję'" :source-id="source.sourceId" />
          </li>
        </ul>
      </article>
      <article v-else v-for="relation in row.relations" :key="relation.id" class="person-source-relation-card" :class="{ 'is-unresolved': !relation.personIdentityResolved }">
        <header><span>{{ relation.relationLabel }}</span><strong>{{ relation.person.label }}</strong><small>{{ relation.person.lifeSpanLabel }}</small></header>
        <p class="person-source-relation-status"><strong>{{ relation.resolutionLabel }}</strong></p>
        <EntityLink v-if="relation.person.id" kind="person" :id="relation.person.id" :label="`Otwórz profil: ${relation.person.label}`" />
        <ul class="person-source-evidence-list" :aria-label="`Dowody relacji: ${relation.person.label}`">
          <li v-for="source in relation.sources" :key="source.id">
            <div>
              <strong>{{ source.sourceLabel || "Akt / materiał źródłowy" }}</strong>
              <small>Relacja: {{ source.statuses.map(statusLabel).join(" · ") }}<template v-if="source.readingCertainties.length"> · odczyt {{ source.readingCertainties.map(statusLabel).join(" · ") }}</template></small>
              <small v-if="source.provenance.includes('event_participation_role')">Podstawa: jawne role uczestników tego aktu</small>
              <p v-for="literal in source.literals" :key="literal">„{{ literal }}”</p>
            </div>
            <EntityLink v-if="source.sourceId" kind="source" :id="source.sourceId" :label="source.sourceLabel || 'Otwórz akt i transkrypcję'" :source-id="source.sourceId" />
          </li>
        </ul>
      </article>
      </template>
    </details>
    <p v-if="!family.parents.length && !family.siblings.length && !family.unions.length && !family.unconfirmed.length && !family.sourceReported.length && !family.externalTreeRelations.length" class="empty-note is-important">Nie znamy jeszcze relacji rodzinnych tej osoby w kontrolowanym eksporcie. To luka badawcza do triage kuratora.</p>
  </section>
</template>
